"""Argument validation for tools whose input schema pipy did not write.

A `ToolDefinition` with `schema_mode="foreign"` carries an arbitrary JSON
Schema, such as the `inputSchema` of an MCP server tool. Pi validates such
arguments with a full JSON Schema implementation; pipy keeps its runtime free
of a schema dependency and enforces this named keyword set with the standard
library:

- `type` (one name or a list of names; `object`, `array`, `string`, `number`,
  `integer`, `boolean`, `null`)
- `properties`, `required`, `additionalProperties` (a boolean or a schema)
- `items` (a schema, or a list of schemas checked by position), `prefixItems`
- `enum`, `const`
- `anyOf`, `allOf`, and `oneOf` as "at least one branch matches": a branch
  may hold keywords pipy does not enforce, so a second apparent match is no
  proof that the arguments are wrong
- `$ref` to a location in the same schema (`#` or `#/json/pointer`); in a
  schema whose `$schema` names draft 7 or older, the keywords next to a `$ref`
  are ignored, as those drafts define
- `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`
- `minLength`, `maxLength`, `minItems`, `maxItems`
- `pattern`

Every other keyword is ignored, and so is a keyword of the set whose value
does not have the shape the keyword needs (a `required` that is not a list of
strings, a `pattern` Python's `re` cannot compile, a `$ref` that leaves the
schema or does not resolve). Ignoring a keyword must never reject arguments a
full validator accepts, so property names that match a `patternProperties`
key do not count as additional properties (their values are not checked). Patterns are matched
with Python's `re`, not an ECMAScript engine. The tool's owner remains the
authority on what it accepts; this check exists to give the model a precise
error before the call.

Before validation the arguments are prepared as Pi's `validateToolArguments`
prepares them for a schema it did not build: an optional property sent as
`null` that its schema refuses is dropped (`normalizeOptionalNulls`), and
primitive values are converted towards the types the schema names
(`coerceWithJsonSchema`: `"5"` to `5`, `"true"` to `true`, a number to its
JavaScript string, `null` to `0`, `false` or `""`; through `allOf`, `anyOf`,
`oneOf`, properties, `additionalProperties` and items, not `$ref`). Numbers
are read and written as JavaScript's `Number()` and `String()` do. Pi's
TypeBox `Value.Convert` pass, which runs before that and converts some more
spellings (`"TRUE"` or `"1"` to `true`, for one), is not ported. Properties
the schema does not declare are kept unless `additionalProperties` forbids
them, which is JSON Schema's default.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Any
from urllib.parse import unquote

from pipy_harness.native.tools.base import ToolArgumentError

FOREIGN_SCHEMA_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "prefixItems",
        "enum",
        "const",
        "anyOf",
        "oneOf",
        "allOf",
        "$ref",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "pattern",
    }
)

_MISSING = object()


def validate_foreign_arguments(
    *,
    tool_name: str,
    schema: Mapping[str, Any],
    arguments: Any,
) -> dict[str, Any]:
    """Validate `arguments` against a foreign JSON Schema, as Pi's
    `validateToolArguments` does for a plain JSON Schema.

    Returns a detached copy of the arguments with optional `null`s dropped
    and primitives coerced. Raises `ToolArgumentError` naming the first
    argument that does not fit.
    """

    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("validate_foreign_arguments requires a non-empty tool_name")
    if not isinstance(schema, Mapping):
        raise ValueError("validate_foreign_arguments schema must be a mapping")
    if not isinstance(arguments, dict):
        raise ToolArgumentError(tool_name, "expected object")
    try:
        if not _is_json_data(arguments):
            raise ToolArgumentError(tool_name, "arguments must be JSON values")
        validator = _Validator(tool_name, schema)
        prepared = copy.deepcopy(arguments)
        _normalize_optional_nulls(prepared, schema, validator)
        coerced = _Coercer(validator).coerce(prepared, schema)
        # Pi keeps the arguments an object; a schema that would turn them
        # into something else leaves them as they were.
        result = coerced if isinstance(coerced, dict) else prepared
        validator.check(schema, result, ())
        return result
    except RecursionError:
        raise ToolArgumentError(tool_name, "arguments are nested too deeply") from None


def _is_json_data(value: object) -> bool:
    """Whether `value` holds only what a JSON parser produces.

    The checks below rely on it: object keys are strings, and containers are
    `dict` and `list`.
    """

    if isinstance(value, dict):
        return all(
            type(key) is str and _is_json_data(child) for key, child in value.items()
        )
    if isinstance(value, list):
        return all(_is_json_data(child) for child in value)
    return value is None or type(value) in {bool, int, float, str}


class _Validator:
    """One validation run: the root schema for `$ref` and the cycle guard."""

    __slots__ = ("_active", "_ref_only", "_root", "_tool_name")

    def __init__(self, tool_name: str, root: Mapping[str, Any]) -> None:
        self._tool_name = tool_name
        self._root = root
        dialect = root.get("$schema")
        # Draft 7 and older: a `$ref` replaces the schema it appears in.
        self._ref_only = isinstance(dialect, str) and "/draft-0" in dialect
        # (schema, value) pairs under evaluation. A pair that comes up again
        # before the value was descended into is a `$ref` cycle.
        self._active: set[tuple[int, int]] = set()

    def _fail(self, message: str, path: tuple[str, ...]) -> ToolArgumentError:
        return ToolArgumentError(self._tool_name, message, field_path=path)

    def accepts(self, schema: object, value: object) -> bool:
        """Whether ``value`` fits ``schema``, a part of the root schema."""
        return self._matches(schema, value, ())

    def _matches(self, schema: object, value: object, path: tuple[str, ...]) -> bool:
        try:
            self.check(schema, value, path)
        except ToolArgumentError:
            return False
        return True

    def check(self, schema: object, value: object, path: tuple[str, ...]) -> None:
        if schema is False:
            raise self._fail("no value is allowed here", path)
        if not isinstance(schema, Mapping):
            return
        key = (id(schema), id(value))
        if key in self._active:
            return
        self._active.add(key)
        try:
            self._check_keywords(schema, value, path)
        finally:
            self._active.discard(key)

    def _check_keywords(
        self, schema: Mapping[str, Any], value: object, path: tuple[str, ...]
    ) -> None:
        target = self._resolve_ref(schema.get("$ref"))
        if target is not _MISSING:
            self.check(target, value, path)
            if self._ref_only:
                return
        self._check_type(schema.get("type"), value, path)
        self._check_enum(schema, value, path)
        self._check_combinators(schema, value, path)
        number = _number(value)
        if number is not None:
            self._check_number(schema, number, path)
        elif isinstance(value, str):
            self._check_string(schema, value, path)
        elif isinstance(value, list):
            self._check_array(schema, value, path)
        elif isinstance(value, dict):
            self._check_object(schema, value, path)

    def _resolve_ref(self, ref: object) -> object:
        if not isinstance(ref, str) or not ref.startswith("#"):
            return _MISSING
        pointer = unquote(ref[1:])
        if pointer and not pointer.startswith("/"):
            return _MISSING
        node: object = self._root
        for raw in pointer.split("/")[1:]:
            token = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(node, Mapping) and token in node:
                node = node[token]
            elif isinstance(node, (list, tuple)) and token in map(
                str, range(len(node))
            ):
                node = node[int(token)]
            else:
                return _MISSING
        return node

    def _check_type(
        self, declared: object, value: object, path: tuple[str, ...]
    ) -> None:
        if isinstance(declared, str):
            names: tuple[object, ...] = (declared,)
        elif isinstance(declared, (list, tuple)):
            names = tuple(declared)
        else:
            return
        known = tuple(
            name for name in names if isinstance(name, str) and name in _TYPE_CHECKS
        )
        if len(known) != len(names) or not known:
            return
        if not any(_TYPE_CHECKS[name](value) for name in known):
            raise self._fail(f"expected {' or '.join(known)}", path)

    def _check_enum(
        self, schema: Mapping[str, Any], value: object, path: tuple[str, ...]
    ) -> None:
        allowed = schema.get("enum")
        if isinstance(allowed, (list, tuple)) and not any(
            _json_equal(value, item) for item in allowed
        ):
            raise self._fail("value not in allowed set", path)
        if "const" in schema and not _json_equal(value, schema["const"]):
            raise self._fail("value does not equal the required constant", path)

    def _check_combinators(
        self, schema: Mapping[str, Any], value: object, path: tuple[str, ...]
    ) -> None:
        all_of = schema.get("allOf")
        if isinstance(all_of, (list, tuple)):
            for branch in all_of:
                self.check(branch, value, path)
        # More than one apparent `oneOf` match is not rejected: a branch may be
        # narrowed by a keyword this validator ignores.
        for key in ("anyOf", "oneOf"):
            branches = schema.get(key)
            if isinstance(branches, (list, tuple)) and branches:
                if not any(self._matches(branch, value, path) for branch in branches):
                    raise self._fail("does not match any allowed schema", path)

    def _check_number(
        self, schema: Mapping[str, Any], value: float, path: tuple[str, ...]
    ) -> None:
        minimum = _number(schema.get("minimum"))
        if minimum is not None and value < minimum:
            raise self._fail(f"number below minimum {minimum}", path)
        maximum = _number(schema.get("maximum"))
        if maximum is not None and value > maximum:
            raise self._fail(f"number above maximum {maximum}", path)
        lower = _number(schema.get("exclusiveMinimum"))
        if lower is not None and value <= lower:
            raise self._fail(f"number not above exclusiveMinimum {lower}", path)
        upper = _number(schema.get("exclusiveMaximum"))
        if upper is not None and value >= upper:
            raise self._fail(f"number not below exclusiveMaximum {upper}", path)

    def _check_string(
        self, schema: Mapping[str, Any], value: str, path: tuple[str, ...]
    ) -> None:
        min_length = _count(schema.get("minLength"))
        if min_length is not None and len(value) < min_length:
            raise self._fail(f"string shorter than minLength {min_length}", path)
        max_length = _count(schema.get("maxLength"))
        if max_length is not None and len(value) > max_length:
            raise self._fail(f"string longer than maxLength {max_length}", path)
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                found = re.search(pattern, value)
            except (re.error, RecursionError, OverflowError):
                return
            if found is None:
                raise self._fail(f"string does not match pattern {pattern!r}", path)

    def _check_array(
        self, schema: Mapping[str, Any], value: list[Any], path: tuple[str, ...]
    ) -> None:
        min_items = _count(schema.get("minItems"))
        if min_items is not None and len(value) < min_items:
            raise self._fail(f"array shorter than minItems {min_items}", path)
        max_items = _count(schema.get("maxItems"))
        if max_items is not None and len(value) > max_items:
            raise self._fail(f"array longer than maxItems {max_items}", path)
        prefix = schema.get("prefixItems")
        items = schema.get("items", _MISSING)
        if not isinstance(prefix, (list, tuple)):
            # A list-valued `items` is the positional form older drafts use.
            prefix = items if isinstance(items, (list, tuple)) else ()
        if isinstance(items, (list, tuple)):
            items = _MISSING
        for index, item in enumerate(value):
            item_path = path + (f"[{index}]",)
            if index < len(prefix):
                self.check(prefix[index], item, item_path)
            elif items is not _MISSING:
                self.check(items, item, item_path)

    def _check_object(
        self, schema: Mapping[str, Any], value: dict[str, Any], path: tuple[str, ...]
    ) -> None:
        required = schema.get("required")
        if isinstance(required, (list, tuple)) and all(
            isinstance(name, str) for name in required
        ):
            missing = [name for name in required if name not in value]
            if missing:
                raise self._fail(
                    f"missing required argument(s): {', '.join(missing)}", path
                )
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            properties = {}
        for name, sub_schema in properties.items():
            if name in value:
                self.check(sub_schema, value[name], path + (str(name),))
        additional = schema.get("additionalProperties", _MISSING)
        if additional is _MISSING or additional is True:
            return
        covered = _pattern_property_names(schema.get("patternProperties"), value)
        extra = [
            name for name in value if name not in properties and name not in covered
        ]
        if additional is False:
            if extra:
                raise self._fail(
                    f"unsupported argument(s): {', '.join(sorted(extra))}", path
                )
            return
        for name in extra:
            self.check(additional, value[name], path + (str(name),))


def _pattern_property_names(patterns: object, value: dict[str, Any]) -> set[str]:
    """The names in `value` that some `patternProperties` key matches."""

    if not isinstance(patterns, Mapping):
        return set()
    covered: set[str] = set()
    for pattern in patterns:
        try:
            covered.update(name for name in value if re.search(str(pattern), name))
        except (re.error, RecursionError, OverflowError):
            # A pattern pipy cannot evaluate may cover any name.
            return set(value)
    return covered


def _number(value: object) -> float | None:
    """`value` when it is a JSON number (an int or a finite float), else None."""

    if type(value) is int:
        return value
    return value if type(value) is float and math.isfinite(value) else None


def _count(value: object) -> int | None:
    """`value` as a non-negative whole number, else None."""

    number = _number(value)
    if number is None or number < 0 or number != int(number):
        return None
    return int(number)


def _is_number(value: object) -> bool:
    return _number(value) is not None


def _is_integer(value: object) -> bool:
    number = _number(value)
    return number is not None and number == int(number)


_TYPE_CHECKS: dict[str, Callable[[object], bool]] = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "number": _is_number,
    "integer": _is_integer,
    "boolean": lambda value: type(value) is bool,
    "null": lambda value: value is None,
}


def _json_equal(left: object, right: object) -> bool:
    """JSON equality: `true` is not `1`, and `1` equals `1.0`."""

    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    if _is_number(left) and _is_number(right):
        return left == right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return type(left) is type(right) and left == right


# -- Pi ``validateToolArguments``: optional nulls and coercion ---------------


def _normalize_optional_nulls(
    value: object, schema: object, validator: _Validator
) -> None:
    """Pi ``normalizeOptionalNulls``: drop a ``null`` an optional property's
    schema refuses, so a model's habit of sending ``null`` for "not given"
    passes. In place, through arrays and nested objects."""

    if not isinstance(schema, Mapping):
        return
    if isinstance(value, list):
        items = schema.get("items")
        item_schemas = (
            items if isinstance(items, (list, tuple)) else [items] * len(value)
        )
        for item, item_schema in zip(value, item_schemas, strict=False):
            _normalize_optional_nulls(item, item_schema, validator)
        return
    properties = schema.get("properties")
    if not isinstance(value, dict) or not isinstance(properties, Mapping):
        return
    required = _required_names(schema)
    for key, property_schema in properties.items():
        if key not in value:
            continue
        if (
            value[key] is None
            and key not in required
            and not _has_ref(property_schema)
            and not validator.accepts(property_schema, None)
        ):
            del value[key]
        else:
            _normalize_optional_nulls(value[key], property_schema, validator)


def _required_names(schema: Mapping[str, Any]) -> set[str]:
    required = schema.get("required")
    if not isinstance(required, (list, tuple)):
        return set()
    return {name for name in required if isinstance(name, str)}


def _has_ref(schema: object) -> bool:
    return isinstance(schema, Mapping) and isinstance(schema.get("$ref"), str)


class _Coercer:
    """Pi ``coerceWithJsonSchema``: convert primitive values towards the
    types a schema names, through ``allOf``, ``anyOf``, ``oneOf``,
    properties, ``additionalProperties`` and items. Objects and arrays are
    changed in place; ``$ref`` is not followed (Pi does not either)."""

    __slots__ = ("_validator",)

    def __init__(self, validator: _Validator) -> None:
        self._validator = validator

    def coerce(self, value: object, schema: object) -> object:
        if not isinstance(schema, Mapping):
            return value
        result = self._coerce_combinators(value, schema)
        types = _schema_types(schema)
        matches_member = len(types) > 1 and any(
            _matches_json_type(result, name) for name in types
        )
        if types and not matches_member:
            for name in types:
                candidate = _coerce_primitive(result, name)
                if candidate is not result:
                    result = candidate
                    break
        if "object" in types and isinstance(result, dict):
            self._coerce_object(result, schema)
        if "array" in types and isinstance(result, list):
            self._coerce_array(result, schema)
        return result

    def _coerce_combinators(self, value: object, schema: Mapping[str, Any]) -> object:
        result = value
        all_of = schema.get("allOf")
        if isinstance(all_of, (list, tuple)):
            for branch in all_of:
                result = self.coerce(result, branch)
        for key in ("anyOf", "oneOf"):
            branches = schema.get(key)
            if isinstance(branches, (list, tuple)):
                result = self._coerce_union(result, branches)
        return result

    def _coerce_union(self, value: object, branches: Sequence[object]) -> object:
        for branch in branches:
            if self._validator.accepts(branch, value):
                return value
        for branch in branches:
            coerced = self.coerce(copy.deepcopy(value), branch)
            if self._validator.accepts(branch, coerced):
                return coerced
        return value

    def _coerce_object(self, value: dict[str, Any], schema: Mapping[str, Any]) -> None:
        properties = schema.get("properties")
        defined: set[str] = set()
        if isinstance(properties, Mapping):
            defined = set(properties)
            for key, property_schema in properties.items():
                if key in value:
                    value[key] = self.coerce(value[key], property_schema)
        additional = schema.get("additionalProperties")
        if isinstance(additional, Mapping):
            for key in list(value):
                if key not in defined:
                    value[key] = self.coerce(value[key], additional)

    def _coerce_array(self, value: list[Any], schema: Mapping[str, Any]) -> None:
        items = schema.get("items")
        if isinstance(items, (list, tuple)):
            for index in range(min(len(value), len(items))):
                value[index] = self.coerce(value[index], items[index])
        elif isinstance(items, Mapping):
            for index, item in enumerate(value):
                value[index] = self.coerce(item, items)


def _schema_types(schema: Mapping[str, Any]) -> list[str]:
    declared = schema.get("type")
    if isinstance(declared, str):
        return [declared]
    if isinstance(declared, (list, tuple)):
        return [name for name in declared if isinstance(name, str)]
    return []


def _matches_json_type(value: object, name: str) -> bool:
    """Pi ``matchesJsonType``; an unknown name matches nothing."""

    check = _TYPE_CHECKS.get(name)
    return check is not None and check(value)


def _coerce_primitive(value: object, name: str) -> object:
    """Pi ``coercePrimitiveByType``: ``value`` itself when nothing applies."""

    convert = _PRIMITIVE_COERCIONS.get(name)
    return value if convert is None else convert(value)


def _to_number(value: object, *, integer: bool) -> object:
    if value is None:
        return 0
    if type(value) is bool:
        return 1 if value else 0
    if isinstance(value, str) and _js_trim(value):
        parsed = _js_number(value)
        if parsed is not None and (not integer or _is_integer(parsed)):
            return parsed
    return value


def _to_boolean(value: object) -> object:
    if value is None:
        return False
    if isinstance(value, str):
        return {"true": True, "false": False}.get(value, value)
    number = _number(value) if type(value) is not bool else None
    if number == 1:
        return True
    if number == 0:
        return False
    return value


def _to_string(value: object) -> object:
    if value is None:
        return ""
    if type(value) is bool:
        return "true" if value else "false"
    number = _number(value)
    return value if number is None else _js_number_string(number)


def _to_null(value: object) -> object:
    if value == "" and isinstance(value, str):
        return None
    if value is False or (type(value) in (int, float) and value == 0):
        return None
    return value


_PRIMITIVE_COERCIONS: dict[str, Callable[[object], object]] = {
    "number": lambda value: _to_number(value, integer=False),
    "integer": lambda value: _to_number(value, integer=True),
    "boolean": _to_boolean,
    "string": _to_string,
    "null": _to_null,
}


# JavaScript's whitespace for ``String.prototype.trim`` and ``Number()``.
_JS_SPACE = "\t\n\v\f\r                  　﻿"
_JS_DECIMAL = re.compile(
    r"[+-]?(?:Infinity|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)"
)
_JS_RADIX = re.compile(r"0(?:([xX])[0-9a-fA-F]+|([oO])[0-7]+|([bB])[01]+)")
# Integers up to 2^53 are exact in JavaScript; a float beyond stays a float.
_SAFE_INTEGER = 2**53


def _js_trim(text: str) -> str:
    return text.strip(_JS_SPACE)


def _js_number(text: str) -> int | float | None:
    """JavaScript's ``Number(text)`` when it is finite, as a JSON number (an
    ``int`` for a whole value within 2^53); ``None`` for ``NaN`` and the
    infinities."""

    literal = _js_trim(text)
    radix = _JS_RADIX.fullmatch(literal)
    if radix is not None:
        base = 16 if radix.group(1) else 8 if radix.group(2) else 2
        whole = int(literal[2:], base)
        if whole <= _SAFE_INTEGER:
            return whole
        try:
            return float(whole)
        except OverflowError:
            return None  # JavaScript reads it as Infinity
    if _JS_DECIMAL.fullmatch(literal) is None:
        return None
    number = float(literal.replace("Infinity", "inf"))
    if not math.isfinite(number):
        return None
    if number.is_integer() and abs(number) <= _SAFE_INTEGER:
        return int(number)
    return number


def _js_number_string(value: int | float) -> str:
    """JavaScript's ``String(value)`` of the number JavaScript reads for
    ``value``: a double, so a large integer is rounded first and one beyond
    the double range is ``Infinity``."""

    try:
        number = float(value)
    except OverflowError:
        return "Infinity" if value > 0 else "-Infinity"
    if number == 0:
        return "0"
    _sign, digit_tuple, exponent = Decimal(repr(abs(number))).as_tuple()
    digits = "".join(map(str, digit_tuple)).rstrip("0") or "0"
    assert isinstance(exponent, int)
    # ``point``: where the decimal point falls after the digits' first one.
    point = exponent + len("".join(map(str, digit_tuple)))
    count = len(digits)
    prefix = "-" if number < 0 else ""
    if count <= point <= 21:
        return prefix + digits + "0" * (point - count)
    if 0 < point <= 21:
        return prefix + digits[:point] + "." + digits[point:]
    if -6 < point <= 0:
        return prefix + "0." + "0" * -point + digits
    power = point - 1
    mantissa = digits[0] + ("." + digits[1:] if count > 1 else "")
    return f"{prefix}{mantissa}e{'+' if power >= 0 else '-'}{abs(power)}"

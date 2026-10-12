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

Unlike the strict validator, arguments are not rewritten: no coercion, and
properties the schema does not declare are kept unless `additionalProperties`
forbids them, which is JSON Schema's default.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable, Mapping
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
    """Validate `arguments` against a foreign JSON Schema.

    Returns a detached copy of the arguments, unchanged in content. Raises
    `ToolArgumentError` naming the first argument that does not fit.
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
        _Validator(tool_name, schema).check(schema, arguments, ())
        return copy.deepcopy(arguments)
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

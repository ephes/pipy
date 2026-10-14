"""Argument validation for tools whose input schema pipy did not write.

A `ToolDefinition` with `schema_mode="foreign"` carries an arbitrary JSON
Schema, such as the `inputSchema` of an MCP server tool. Pi validates such
arguments with a full JSON Schema implementation; pipy keeps its runtime free
of a schema dependency and enforces this named keyword set with the standard
library:

- `type` (one name or a list of names; `object`, `array`, `string`, `number`,
  `integer`, `boolean`, `null`)
- `properties`, `required`, `additionalProperties` (a boolean or a schema),
  `patternProperties`, `propertyNames`, `minProperties`, `maxProperties`,
  `dependentRequired`, `dependentSchemas` and the older `dependencies`
- `items` (a schema, or a list of schemas checked by position, then
  `additionalItems`), `prefixItems`, `minItems`, `maxItems`, `uniqueItems`,
  `contains` with `minContains` and `maxContains`
- `enum`, `const`
- `allOf`, `anyOf`, `oneOf`, `not`, and `if` with `then` and `else`
- `$ref` to a location in the same schema (`#` or `#/json/pointer`); in a
  schema whose `$schema` names draft 7 or older, the keywords next to a `$ref`
  are ignored, as those drafts define
- `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`
  (TypeBox's rule: exact for integers, within 1e-10 otherwise)
- `minLength`, `maxLength`, `pattern`, and `format` with TypeBox's checks
  (`tools/json_formats.py`; a format TypeBox does not know passes)

Not enforced: `unevaluatedProperties`, `unevaluatedItems`, `$dynamicRef`,
`$recursiveRef`, a `$ref` that leaves the schema, does not resolve or lies
under a nested `$id`, a `pattern` Python's `re` cannot compile, and the
formats `hostname`,
`idn-hostname`, `idn-email`, `iri`, `iri-reference`, `url` and `regex`. A
keyword of the set whose value does not have the shape the keyword needs (a
`required` that is not a list of strings) is ignored, as is every keyword
outside the set. Ignoring a keyword must never reject arguments a full
validator accepts: a schema with a keyword pipy does not enforce may seem
to match where it does not, so a second matching `oneOf` branch, a
matching `not`, too many `contains` matches and an `if` decide only for a
schema pipy enforces in full (`_Validator.enforced`); otherwise `oneOf`
needs one match, `not` and `maxContains` refuse nothing, and an `if` refuses
only a value both `then` and `else` refuse. Patterns are matched with
Python's `re`, not an ECMAScript engine, and string lengths count code
points. The tool's owner remains the authority on what it accepts; this
check exists to give the model a precise error before the call.

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
from pipy_harness.native.tools.json_formats import UNCHECKED_FORMATS, format_matches

FOREIGN_SCHEMA_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "patternProperties",
        "propertyNames",
        "minProperties",
        "maxProperties",
        "dependentRequired",
        "dependentSchemas",
        "dependencies",
        "items",
        "prefixItems",
        "additionalItems",
        "uniqueItems",
        "contains",
        "minContains",
        "maxContains",
        "enum",
        "const",
        "anyOf",
        "oneOf",
        "allOf",
        "not",
        "if",
        "then",
        "else",
        "$ref",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "pattern",
        "format",
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

    __slots__ = (
        "_active",
        "_enforced_cache",
        "_ref_only",
        "_root",
        "_scoped",
        "_tool_name",
    )

    def __init__(self, tool_name: str, root: Mapping[str, Any]) -> None:
        self._tool_name = tool_name
        self._root = root
        dialect = root.get("$schema")
        # Draft 7 and older: a `$ref` replaces the schema it appears in.
        self._ref_only = isinstance(dialect, str) and "/draft-0" in dialect
        # (schema, value) pairs under evaluation. A pair that comes up again
        # before the value was descended into is a `$ref` cycle.
        self._active: set[tuple[int, int]] = set()
        # id(schema) -> whether pipy enforces every keyword under it.
        self._enforced_cache: dict[int, bool] = {}
        # Schemas under a nested `$id`, where a `$ref` means something other
        # than a pointer into the root; pipy does not follow those.
        self._scoped = _under_nested_ids(root)

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
        target = self._ref_target(schema)
        if target is not _MISSING:
            self.check(target, value, path)
        if self._ref_only and isinstance(schema.get("$ref"), str):
            # Draft 7 and older ignore a `$ref`'s siblings, also where pipy
            # does not follow the `$ref` itself.
            return
        self._check_type(schema.get("type"), value, path)
        self._check_enum(schema, value, path)
        self._check_combinators(schema, value, path)
        self._check_not(schema, value, path)
        self._check_conditional(schema, value, path)
        number = _number(value)
        if number is not None:
            self._check_number(schema, number, path)
        elif isinstance(value, str):
            self._check_string(schema, value, path)
        elif isinstance(value, list):
            self._check_array(schema, value, path)
        elif isinstance(value, dict):
            self._check_object(schema, value, path)

    def _ref_target(self, schema: Mapping[str, Any]) -> object:
        """What the `$ref` of ``schema`` points at, or `_MISSING` when it has
        none, pipy cannot resolve it, or it lies under a nested `$id`."""

        if "$ref" not in schema or id(schema) in self._scoped:
            return _MISSING
        return self._resolve_ref(schema["$ref"])

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
        branches = schema.get("anyOf")
        if isinstance(branches, (list, tuple)) and branches:
            if not any(self._matches(branch, value, path) for branch in branches):
                raise self._fail("does not match any allowed schema", path)
        branches = schema.get("oneOf")
        if isinstance(branches, (list, tuple)) and branches:
            matched = [b for b in branches if self._matches(b, value, path)]
            if not matched:
                raise self._fail("does not match any allowed schema", path)
            # An apparent match of a branch with a keyword pipy does not
            # enforce may be none; two branches pipy enforces in full both
            # match for certain.
            if sum(1 for branch in matched if self.enforced(branch)) > 1:
                raise self._fail("matches more than one schema of oneOf", path)

    def _check_not(
        self, schema: Mapping[str, Any], value: object, path: tuple[str, ...]
    ) -> None:
        forbidden = schema.get("not", _MISSING)
        if forbidden is _MISSING or not self.enforced(forbidden):
            return
        if self._matches(forbidden, value, path):
            raise self._fail("matches a schema it must not match", path)

    def _check_conditional(
        self, schema: Mapping[str, Any], value: object, path: tuple[str, ...]
    ) -> None:
        condition = schema.get("if", _MISSING)
        if condition is _MISSING:
            return
        then = schema.get("then", _MISSING)
        otherwise = schema.get("else", _MISSING)
        if self.enforced(condition):
            branch = then if self._matches(condition, value, path) else otherwise
            if branch is not _MISSING:
                self.check(branch, value, path)
            return
        # Which branch applies is not certain: refuse only what both refuse.
        if (
            then is not _MISSING
            and otherwise is not _MISSING
            and not self._matches(then, value, path)
            and not self._matches(otherwise, value, path)
        ):
            raise self._fail("matches neither the then nor the else schema", path)

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
        divisor = _number(schema.get("multipleOf"))
        if divisor is not None and divisor > 0 and not _is_multiple(value, divisor):
            raise self._fail(f"number not a multiple of {divisor}", path)

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
                found: object = re.search(pattern, value)
            except (re.error, RecursionError, OverflowError):
                found = True  # a pattern `re` cannot read is not enforced
            if found is None:
                raise self._fail(f"string does not match pattern {pattern!r}", path)
        name = schema.get("format")
        if isinstance(name, str) and not format_matches(name, value):
            raise self._fail(f"string is not a valid {name}", path)

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
        positional = isinstance(items, (list, tuple))
        if not isinstance(prefix, (list, tuple)):
            # A list-valued `items` is the positional form older drafts use.
            prefix = items if positional else ()
        if positional:
            # Drafts before 2020-12: `additionalItems` follows a list `items`.
            items = (
                _MISSING
                if "prefixItems" in schema
                else schema.get("additionalItems", _MISSING)
            )
        for index, item in enumerate(value):
            item_path = path + (f"[{index}]",)
            if index < len(prefix):
                self.check(prefix[index], item, item_path)
            elif items is not _MISSING:
                self.check(items, item, item_path)
        if schema.get("uniqueItems") is True and not _all_unique(value):
            raise self._fail("array items are not unique", path)
        self._check_contains(schema, value, path)

    def _check_contains(
        self, schema: Mapping[str, Any], value: list[Any], path: tuple[str, ...]
    ) -> None:
        wanted = schema.get("contains", _MISSING)
        if wanted is _MISSING:
            return
        least = _count(schema.get("minContains"))
        least = 1 if least is None else least
        most = _count(schema.get("maxContains"))
        found = sum(1 for item in value if self._matches(wanted, item, path))
        # An apparent match may be none, so only a short count is certain;
        # a long one only for a schema pipy enforces in full.
        if found < least:
            raise self._fail(
                f"array contains fewer than {least} matching item(s)", path
            )
        if most is not None and found > most and self.enforced(wanted):
            raise self._fail(f"array contains more than {most} matching item(s)", path)

    def _check_property_count(
        self, schema: Mapping[str, Any], value: dict[str, Any], path: tuple[str, ...]
    ) -> None:
        least = _count(schema.get("minProperties"))
        if least is not None and len(value) < least:
            raise self._fail(f"object has fewer than {least} properties", path)
        most = _count(schema.get("maxProperties"))
        if most is not None and len(value) > most:
            raise self._fail(f"object has more than {most} properties", path)

    def _check_dependencies(
        self, schema: Mapping[str, Any], value: dict[str, Any], path: tuple[str, ...]
    ) -> None:
        """`dependentRequired`, `dependentSchemas` and the older
        `dependencies`, which holds either form."""

        for key in ("dependentRequired", "dependentSchemas", "dependencies"):
            declared = schema.get(key)
            if not isinstance(declared, Mapping):
                continue
            for name, rule in declared.items():
                if name not in value or not _dependency_shape_ok(key, rule):
                    continue
                if isinstance(rule, (list, tuple)):
                    missing = [item for item in rule if item not in value]
                    if missing:
                        raise self._fail(
                            f"missing required argument(s): {', '.join(missing)}",
                            path,
                        )
                else:
                    self.check(rule, value, path)

    def enforced(self, schema: object) -> bool:
        """Whether pipy enforces every keyword that applies under ``schema``
        as a full validator would, so that its answer for a value is
        certain: no schema reachable from it, through its subschemas and
        `$ref`s, has a keyword pipy skips (`_skips_a_keyword`)."""

        if not isinstance(schema, Mapping):
            return True
        key = id(schema)
        known = self._enforced_cache.get(key)
        if known is not None:
            return known
        seen: set[int] = set()
        pending: list[object] = [schema]
        result = True
        while pending and result:
            node = pending.pop()
            if not isinstance(node, Mapping) or id(node) in seen:
                continue
            seen.add(id(node))
            if self._skips_a_keyword(node):
                result = False
                break
            target = self._ref_target(node)
            if target is not _MISSING:
                pending.append(target)
            pending.extend(_subschemas(node))
        self._enforced_cache[key] = result
        return result

    def _skips_a_keyword(self, schema: Mapping[str, Any]) -> bool:
        """Whether pipy skips a keyword of ``schema`` itself that a full
        validator applies: one it does not implement, a format it does not
        check, a `$ref` it cannot resolve, a nested `$id` (which changes what
        a `$ref` under it means), or a keyword of the set whose value has a
        shape the checks ignore."""

        if any(keyword in schema for keyword in _UNENFORCED_KEYWORDS):
            return True
        if schema is not self._root and isinstance(schema.get("$id"), str):
            return True
        if "$ref" in schema and self._ref_target(schema) is _MISSING:
            return True
        name = schema.get("format")
        if isinstance(name, str) and name in UNCHECKED_FORMATS:
            return True
        return not all(
            _shape_ok(keyword, schema[keyword])
            for keyword in FOREIGN_SCHEMA_KEYWORDS
            if keyword in schema
        )

    def _check_object(
        self, schema: Mapping[str, Any], value: dict[str, Any], path: tuple[str, ...]
    ) -> None:
        self._check_required(schema, value, path)
        self._check_property_count(schema, value, path)
        self._check_dependencies(schema, value, path)
        properties = self._check_property_values(schema, value, path)
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

    def _check_required(
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

    def _check_property_values(
        self, schema: Mapping[str, Any], value: dict[str, Any], path: tuple[str, ...]
    ) -> Mapping[str, Any]:
        """`propertyNames`, `properties` and `patternProperties`; returns the
        declared properties."""

        names = schema.get("propertyNames", _MISSING)
        if names is not _MISSING:
            for name in value:
                self.check(names, name, path + (name,))
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            properties = {}
        for name, sub_schema in properties.items():
            if name in value:
                self.check(sub_schema, value[name], path + (str(name),))
        for name, sub_schema in _pattern_property_schemas(
            schema.get("patternProperties"), value
        ):
            self.check(sub_schema, value[name], path + (name,))
        return properties


# Keywords a full validator applies that pipy does not.
_UNENFORCED_KEYWORDS = frozenset(
    {"unevaluatedProperties", "unevaluatedItems", "$dynamicRef", "$recursiveRef"}
)
_SCHEMA_KEYWORDS = (
    "additionalProperties",
    "additionalItems",
    "items",
    "contains",
    "propertyNames",
    "not",
    "if",
    "then",
    "else",
)
_SCHEMA_MAP_KEYWORDS = ("properties", "patternProperties", "dependentSchemas")
_SCHEMA_LIST_KEYWORDS = ("allOf", "anyOf", "oneOf", "prefixItems", "items")


def _subschemas(schema: Mapping[str, Any]) -> list[object]:
    """The schemas directly under ``schema`` that apply to a value or a part
    of it (not `$defs`, which apply only through a `$ref`)."""

    found: list[object] = [
        schema[key]
        for key in _SCHEMA_KEYWORDS
        if key in schema and not isinstance(schema[key], (list, tuple))
    ]
    for key in _SCHEMA_MAP_KEYWORDS:
        if isinstance(schema.get(key), Mapping):
            found.extend(schema[key].values())
    for key in _SCHEMA_LIST_KEYWORDS:
        if isinstance(schema.get(key), (list, tuple)):
            found.extend(schema[key])
    dependencies = schema.get("dependencies")
    if isinstance(dependencies, Mapping):
        found.extend(
            rule
            for rule in dependencies.values()
            if not isinstance(rule, (list, tuple))
        )
    return found


def _under_nested_ids(root: Mapping[str, Any]) -> set[int]:
    """The ids of the objects that lie under a nested `$id` (the object
    with the `$id` included), wherever else they are also reached."""

    scoped: set[int] = set()
    # An object may be shared: one reached inside a scope anywhere counts as
    # scoped, so it is visited again when reached inside one.
    seen: set[tuple[int, bool]] = set()
    pending: list[tuple[object, bool]] = [(root, False)]
    while pending:
        node, inside = pending.pop()
        if (id(node), inside) in seen:
            continue
        seen.add((id(node), inside))
        if isinstance(node, Mapping):
            inside = inside or (node is not root and isinstance(node.get("$id"), str))
            if inside:
                scoped.add(id(node))
            pending.extend((child, inside) for child in node.values())
        elif isinstance(node, (list, tuple)):
            pending.extend((child, inside) for child in node)
    return scoped


def _is_schema(value: object) -> bool:
    return isinstance(value, Mapping) or type(value) is bool


def _is_schema_list(value: object) -> bool:
    return isinstance(value, (list, tuple)) and all(_is_schema(v) for v in value)


def _is_name_list(value: object) -> bool:
    return isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value)


def _is_schema_map(value: object) -> bool:
    return isinstance(value, Mapping) and all(_is_schema(v) for v in value.values())


def _compiles(pattern: object) -> bool:
    if not isinstance(pattern, str):
        return False
    try:
        re.compile(pattern)
    except (re.error, RecursionError, OverflowError):
        return False
    return True


def _is_type(value: object) -> bool:
    names = value if isinstance(value, (list, tuple)) else [value]
    return bool(names) and all(
        isinstance(name, str) and name in _TYPE_CHECKS for name in names
    )


def _is_pattern_map(value: object) -> bool:
    if not isinstance(value, Mapping) or not _is_schema_map(value):
        return False
    return all(_compiles(key) for key in value)


def _dependency_shape_ok(keyword: str, rule: object) -> bool:
    """`dependentRequired` takes names, `dependentSchemas` a schema and the
    older `dependencies` either."""

    if keyword == "dependentRequired":
        return _is_name_list(rule)
    if keyword == "dependentSchemas":
        return _is_schema(rule)
    return _is_name_list(rule) or _is_schema(rule)


# The shape each keyword of the set needs; a value of another shape is
# skipped by the checks, so a schema holding one is not enforced in full.
_SHAPES: dict[str, Callable[[object], bool]] = {
    "type": _is_type,
    "properties": _is_schema_map,
    "patternProperties": _is_pattern_map,
    "dependentSchemas": _is_schema_map,
    "required": _is_name_list,
    "dependentRequired": lambda value: (
        isinstance(value, Mapping)
        and all(_is_name_list(rule) for rule in value.values())
    ),
    "dependencies": lambda value: (
        isinstance(value, Mapping)
        and all(_dependency_shape_ok("dependencies", rule) for rule in value.values())
    ),
    "items": lambda value: _is_schema(value) or _is_schema_list(value),
    "prefixItems": _is_schema_list,
    "allOf": _is_schema_list,
    "anyOf": lambda value: _is_schema_list(value) and bool(value),
    "oneOf": lambda value: _is_schema_list(value) and bool(value),
    "enum": lambda value: isinstance(value, (list, tuple)),
    "pattern": _compiles,
    "format": lambda value: isinstance(value, str),
    "uniqueItems": lambda value: type(value) is bool,
    "multipleOf": lambda value: (_number(value) or 0) > 0,
    "minimum": lambda value: _number(value) is not None,
    "maximum": lambda value: _number(value) is not None,
    "exclusiveMinimum": lambda value: _number(value) is not None,
    "exclusiveMaximum": lambda value: _number(value) is not None,
    "$ref": lambda value: isinstance(value, str),
    **{
        keyword: (lambda value: _count(value) is not None)
        for keyword in (
            "minLength",
            "maxLength",
            "minItems",
            "maxItems",
            "minProperties",
            "maxProperties",
            "minContains",
            "maxContains",
        )
    },
    **{
        keyword: _is_schema
        for keyword in (
            "additionalProperties",
            "additionalItems",
            "contains",
            "propertyNames",
            "not",
            "if",
            "then",
            "else",
        )
    },
}


def _shape_ok(keyword: str, value: object) -> bool:
    check = _SHAPES.get(keyword)
    return True if check is None else check(value)


def _is_multiple(value: float, divisor: float) -> bool:
    """TypeBox ``IsMultipleOf``: exact for integers, within 1e-10 otherwise."""

    if type(value) is int and type(divisor) is int:
        return value % divisor == 0
    try:
        if float(value).is_integer() and (1 / divisor) % 1 == 0:
            return True
        remainder = math.fmod(value, divisor)
    except OverflowError:
        return True  # beyond JavaScript's numbers; not decided here
    return (
        min(abs(remainder), abs(remainder - divisor), abs(remainder + divisor))
        < _MULTIPLE_TOLERANCE
    )


_MULTIPLE_TOLERANCE = 1e-10


def _all_unique(items: list[Any]) -> bool:
    return not any(
        _json_equal(items[i], items[j])
        for i in range(len(items))
        for j in range(i + 1, len(items))
    )


def _pattern_property_schemas(
    patterns: object, value: dict[str, Any]
) -> list[tuple[str, object]]:
    """Each name of ``value`` with the schema of each ``patternProperties``
    key that matches it; a pattern pipy cannot evaluate checks nothing."""

    if not isinstance(patterns, Mapping):
        return []
    pairs: list[tuple[str, object]] = []
    for pattern, sub_schema in patterns.items():
        try:
            pairs.extend(
                (name, sub_schema) for name in value if re.search(str(pattern), name)
            )
        except (re.error, RecursionError, OverflowError):
            continue
    return pairs


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

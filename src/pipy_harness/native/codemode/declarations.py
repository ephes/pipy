"""Tool declarations for codemode scripts: JSON Schema as Python type text.

The port of Pi's ``packages/codemode/src/declarations.ts`` with Python
spelling. A declaration tells the model how a script calls one tool and what
the call returns::

    search(args: {
      # Largest number of hits.
      # @integer @minimum 1 @default 20
      "limit"?: float,
      "query": str,
    }) -> list[str]

The text is notation for the model, not code that is evaluated:

- ``str``, ``float`` (number and integer; an integer carries ``@integer``),
  ``bool``, ``None``, ``Any`` for an unknown type and ``Never`` for one that
  has no value.
- ``list[T]`` for arrays and ``[A, B]`` for positional arrays (tuples).
- An object is ``{"a": str, "b"?: float}``: property names are quoted and
  sorted, ``?`` marks an optional one, and a last member ``str: T`` gives the
  type of every other property. An object without declared properties is
  ``dict[str, T]``.
- ``A | B`` for unions and ``A & B`` for ``allOf``; ``const`` and ``enum``
  values are Python literals.
- An object is written one property per line when a property has a
  description or constraints the type cannot express. Both go on ``#`` lines
  above the property; the constraint tags keep their JSON Schema names and
  JSON values (``@default true``).

Local references (``#``, ``#/$defs/...``, ``#/definitions/...``) are resolved
against the rendered schema; recursive and remote ones are ``Any``.

Lengths are counted in Python characters where Pi counts UTF-16 units.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from keyword import iskeyword
from typing import TypeGuard
from urllib.parse import unquote

__all__ = [
    "DEFAULT_INPUT_SCHEMA_MAX_CHARS",
    "render_tool_output_type",
    "render_tool_sample",
    "render_tool_signature",
    "schema_to_type",
    "to_codemode_identifier",
]

# Largest rendered input type, in characters, before it becomes ``Any``.
DEFAULT_INPUT_SCHEMA_MAX_CHARS = 16_000

_INDENT = "  "
_UNKNOWN = "Any"
_NEVER = "Never"
# Local ``$ref`` expansions per rendered schema, so shared definitions cannot
# blow up the output.
_MAX_REF_EXPANSIONS = 32
# Keywords with a number or string value shown as ``@keyword value`` tags.
_TAG_KEYWORDS = (
    "minimum",
    "exclusiveMinimum",
    "maximum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minItems",
    "maxItems",
)
# Bounds all hold at once, so only the largest lower and the smallest upper
# one matters.
_LOWER_BOUNDS = frozenset({"minimum", "exclusiveMinimum", "minLength", "minItems"})
_UPPER_BOUNDS = frozenset({"maximum", "exclusiveMaximum", "maxLength", "maxItems"})
# Integers up to here are exact in a JavaScript number.
_MAX_SAFE_INTEGER = 2**53 - 1
# Longest property tag; longer ones (large defaults, long patterns) are left out.
_MAX_TAG_CHARS = 80
_PRIMITIVES = {
    "string": "str",
    "number": "float",
    "integer": "float",
    "boolean": "bool",
    "null": "None",
}
# Keywords that make a schema without ``type`` an object.
_OBJECT_KEYWORDS = ("properties", "additionalProperties", "required")
# Characters that would end a comment line.
_LINE_TERMINATORS = re.compile("[\r\n  ]")
_LONE_SURROGATES = re.compile("[\ud800-\udfff]")
_IDENTIFIER_START = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_")
_IDENTIFIER_PART = _IDENTIFIER_START | frozenset("0123456789")


def to_codemode_identifier(name: str) -> str:
    """The identifier a script uses for a tool (Pi ``toCodemodeIdentifier``).

    Characters that are not valid in an ASCII Python identifier become ``_``:
    ``mcp__docs__search`` stays as it is, ``my-tool`` becomes ``my_tool``. A
    Python keyword gets a trailing ``_`` (``class`` becomes ``class_``), so
    that ``tools.<identifier>`` can always be written.
    """

    identifier = ""
    for char in name:
        valid = _IDENTIFIER_PART if identifier else _IDENTIFIER_START
        identifier += char if char in valid else "_"
    if iskeyword(identifier):
        identifier += "_"
    return identifier or "_"


def schema_to_type(schema: object, *, max_chars: int | None = None) -> str:
    """Render a JSON Schema as Python type text (Pi ``schemaToType``).

    A result longer than ``max_chars`` is rendered again without the
    constraint tags, and is ``Any`` when it is still too long.
    """

    tagged = _render(schema, tags=True)
    if max_chars is None or len(tagged) <= max_chars:
        return tagged
    untagged = _render(schema, tags=False)
    return untagged if len(untagged) <= max_chars else _UNKNOWN


def render_tool_output_type(schema: object | None) -> str:
    """The type a tool call returns; ``Any`` for a tool without output schema."""

    return _UNKNOWN if schema is None else schema_to_type(schema)


def render_tool_signature(
    name: str,
    input_schema: object | None,
    output_schema: object | None = None,
    *,
    input_max_chars: int = DEFAULT_INPUT_SCHEMA_MAX_CHARS,
) -> str:
    """One tool as a member of ``tools``: ``name(args: T) -> R``.

    The name is the identifier scripts use. ``None`` stands for a schema the
    tool does not have. An input type longer than ``input_max_chars`` is
    ``Any`` (Pi ``renderToolSignature``).
    """

    arguments = (
        _UNKNOWN
        if input_schema is None
        else schema_to_type(input_schema, max_chars=input_max_chars)
    )
    result = render_tool_output_type(output_schema)
    return f"{to_codemode_identifier(name)}(args: {arguments}) -> {result}"


def render_tool_sample(
    name: str,
    description: str | None,
    input_schema: object | None,
    output_schema: object | None = None,
    *,
    input_max_chars: int = DEFAULT_INPUT_SCHEMA_MAX_CHARS,
) -> str:
    """A tool's description followed by its declaration (Pi ``renderToolSample``).

    Used for tool listings and ``ALL_TOOLS`` entries.
    """

    signature = render_tool_signature(
        name, input_schema, output_schema, input_max_chars=input_max_chars
    )
    return (
        f"{(description or '').strip()}\n\n"
        f"codemode tool declaration:\n```python\ntools.{signature}\n```"
    )


@dataclass(slots=True)
class _Context:
    root: object
    # Whether property comments include constraint tags.
    tags: bool
    # References being expanded on the current path, to stop at recursive types.
    resolving: set[str] = field(default_factory=set)
    expansions: int = 0


def _render(schema: object, *, tags: bool) -> str:
    try:
        return _to_type(schema, _Context(root=schema, tags=tags))
    except RecursionError:
        # A schema nested deeper than the interpreter can follow.
        return _UNKNOWN


def _is_number(value: object) -> TypeGuard[int | float]:
    """Whether a value is a number JavaScript can hold (a finite double)."""

    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        try:
            float(value)
        except OverflowError:
            return False
        return True
    return isinstance(value, float) and math.isfinite(value)


def _number_text(value: int | float) -> str:
    """A number as JavaScript prints it: ``1.0`` is ``1``, ``1e-07`` is ``1e-7``.

    An integer a double cannot hold exactly is printed as the double is.
    """

    if isinstance(value, int):
        if abs(value) <= _MAX_SAFE_INTEGER:
            return str(value)
        value = float(value)
    if value == 0:
        return "0"
    text = repr(value)
    mantissa, marker, exponent = text.partition("e")
    if marker and not 1e-6 <= abs(value) < 1e21:
        return f"{mantissa}e{exponent[0]}{exponent[1:].lstrip('0')}"
    # The shortest digits that give the number back, without an exponent.
    plain = format(Decimal(text), "f")
    return plain.rstrip("0").rstrip(".") if "." in plain else plain


def _escape_code_unit(match: re.Match[str]) -> str:
    return f"\\u{ord(match.group()):04x}"


def _json_text(value: object, *, python: bool = False) -> str:
    """Compact JSON as ``JSON.stringify`` writes it, or the same as a Python literal."""

    if value is None:
        return "None" if python else "null"
    if isinstance(value, bool):
        if python:
            return "True" if value else "False"
        return "true" if value else "false"
    if isinstance(value, str):
        return _LONE_SURROGATES.sub(
            _escape_code_unit, json.dumps(value, ensure_ascii=False)
        )
    if isinstance(value, int | float):
        if not _is_number(value):
            return "None" if python else "null"
        return _number_text(value)
    if isinstance(value, Mapping):
        members = (
            f"{_json_text(str(key), python=python)}:{_json_text(item, python=python)}"
            for key, item in value.items()
        )
        return "{" + ",".join(members) + "}"
    if isinstance(value, Sequence):
        return "[" + ",".join(_json_text(item, python=python) for item in value) + "]"
    return "None" if python else "null"


def _literal(value: object) -> str:
    return _LINE_TERMINATORS.sub(_escape_code_unit, _json_text(value, python=True))


def _union(types: Sequence[str]) -> str:
    unique = list(dict.fromkeys(types))
    if _UNKNOWN in unique:
        return _UNKNOWN
    return " | ".join(unique) if unique else _NEVER


def _resolve_ref(ref: str, root: object) -> object | None:
    """The schema a local reference names, or ``None``."""

    if ref != "#" and not ref.startswith("#/"):
        return None
    current = root
    for segment in ref[2:].split("/"):
        if not segment:
            continue
        key = unquote(segment).replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    if isinstance(current, bool | Mapping):
        return current
    return None


def _to_type(schema: object, context: _Context) -> str:
    if schema is True:
        return _UNKNOWN
    if schema is False:
        return _NEVER
    if not isinstance(schema, Mapping):
        return _UNKNOWN
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return _referenced_type(ref, context)
    combined = _combined_type(schema, context)
    if combined is not None:
        return combined
    return _named_type(schema, context)


def _referenced_type(ref: str, context: _Context) -> str:
    if ref in context.resolving or context.expansions >= _MAX_REF_EXPANSIONS:
        return _UNKNOWN
    target = _resolve_ref(ref, context.root)
    if target is None:
        return _UNKNOWN
    context.expansions += 1
    context.resolving.add(ref)
    try:
        return _to_type(target, context)
    finally:
        context.resolving.discard(ref)


def _combined_type(schema: Mapping[str, object], context: _Context) -> str | None:
    """The type of a ``const``, ``enum``, ``anyOf``, ``oneOf`` or ``allOf`` schema."""

    if "const" in schema:
        return _literal(schema["const"])
    enum = schema.get("enum")
    if _is_array(enum):
        return _union([_literal(value) for value in enum])
    variants = _variants(schema)
    if variants is not None:
        return _union([_to_type(variant, context) for variant in variants])
    all_of = schema.get("allOf")
    if not _is_array(all_of):
        return None
    parts = [_to_type(part, context) for part in all_of]
    parts = [part for part in parts if part != _UNKNOWN]
    if not parts:
        return _UNKNOWN
    return " & ".join(f"({part})" if " | " in part else part for part in parts)


def _variants(schema: Mapping[str, object]) -> Sequence[object] | None:
    """The branches of ``anyOf``, or of ``oneOf`` when there is no ``anyOf`` list."""

    for keyword in ("anyOf", "oneOf"):
        variants = schema.get(keyword)
        if _is_array(variants):
            return variants
    return None


def _named_type(schema: Mapping[str, object], context: _Context) -> str:
    """The type of a schema by its ``type``, or by its keywords without one."""

    kind = schema.get("type")
    if _is_array(kind):
        return _union([_to_type({**schema, "type": entry}, context) for entry in kind])
    if isinstance(kind, str) and kind in _PRIMITIVES:
        return _PRIMITIVES[kind]
    if kind == "array":
        return _array_type(schema, context)
    if kind == "object":
        return _object_type(schema, context)
    if "type" in schema:
        return _UNKNOWN
    if any(key in schema for key in _OBJECT_KEYWORDS):
        return _object_type(schema, context)
    if "items" in schema or "prefixItems" in schema:
        return _array_type(schema, context)
    return _UNKNOWN


def _is_array(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes)


def _array_type(schema: Mapping[str, object], context: _Context) -> str:
    items = schema.get("items")
    if "items" in schema and not _is_array(items):
        return f"list[{_to_type(items, context)}]"
    positional = schema.get("prefixItems")
    if not _is_array(positional):
        positional = items
    if _is_array(positional) and positional:
        return "[" + ", ".join(_to_type(item, context) for item in positional) + "]"
    return f"list[{_UNKNOWN}]"


def _description_of(schema: object) -> str:
    description = schema.get("description") if isinstance(schema, Mapping) else None
    return description.strip() if isinstance(description, str) else ""


def _constraint_tags(schema: object) -> str:
    """Constraints of a property that its type cannot express, as tags.

    ``@integer @minimum 1 @maximum 100 @default 20``; empty when there are none.
    """

    if not isinstance(schema, Mapping):
        return ""
    schemas = _constraint_sources(schema)
    tags: list[str] = []
    if any(_names_integer(entry.get("type")) for entry in schemas):
        tags.append("@integer")
    for keyword in _TAG_KEYWORDS:
        tags.extend(_keyword_tags(keyword, [entry.get(keyword) for entry in schemas]))
    if any(entry.get("uniqueItems") is True for entry in schemas):
        tags.append("@uniqueItems")
    # Annotations, unlike constraints, come from the property itself.
    if schema.get("default") is not None:
        tags.append(f"@default {_json_text(schema['default'])}")
    if schema.get("deprecated") is True:
        tags.append("@deprecated")
    return " ".join(
        _LINE_TERMINATORS.sub(_escape_code_unit, tag)
        for tag in tags
        if len(tag) <= _MAX_TAG_CHARS
    )


def _constraint_sources(schema: Mapping[str, object]) -> list[dict[str, object]]:
    """The schemas whose constraints hold for a property.

    An optional field such as Pydantic's ``int | None`` keeps its constraints
    in the non-null branch. They apply together with the property's own, so
    both are read.
    """

    branches: list[Mapping[str, object]] = [schema]
    # Pi reads ``oneOf`` only when ``anyOf`` is absent or not a list.
    variants = _variants(schema) or ()
    non_null = [
        variant
        for variant in variants
        if not (isinstance(variant, Mapping) and variant.get("type") == "null")
    ]
    if len(non_null) == 1 and isinstance(non_null[0], Mapping):
        branches.append(non_null[0])
    # Draft-04 and OpenAPI 3.0 make ``minimum``/``maximum`` exclusive with a
    # boolean ``exclusiveMinimum``/``exclusiveMaximum``.
    sources: list[dict[str, object]] = []
    for branch in branches:
        source = dict(branch)
        if branch.get("exclusiveMinimum") is True:
            source["exclusiveMinimum"] = source.pop("minimum", None)
        if branch.get("exclusiveMaximum") is True:
            source["exclusiveMaximum"] = source.pop("maximum", None)
        sources.append(source)
    return sources


def _keyword_tags(keyword: str, values: Sequence[object]) -> list[str]:
    """The tags of one keyword for the values its sources give it."""

    if keyword in _LOWER_BOUNDS or keyword in _UPPER_BOUNDS:
        numbers = [value for value in values if _is_number(value)]
        if not numbers:
            return []
        bound = max(numbers) if keyword in _LOWER_BOUNDS else min(numbers)
        return [f"@{keyword} {_number_text(bound)}"]
    # A number and a string that print alike are two values, as in Pi.
    texts: dict[tuple[bool, object], str] = {}
    for value in values:
        if isinstance(value, str):
            texts.setdefault((True, value), value)
        elif _is_number(value):
            texts.setdefault((False, value), _number_text(value))
    return [f"@{keyword} {text}" for text in texts.values()]


def _names_integer(kind: object) -> bool:
    return kind == "integer" or (_is_array(kind) and "integer" in kind)


def _utf16_order(name: str) -> bytes:
    # JavaScript sorts strings by UTF-16 code unit.
    return name.encode("utf-16-be", "surrogatepass")


def _object_type(schema: Mapping[str, object], context: _Context) -> str:
    declared = schema.get("properties")
    properties: Mapping[str, object] = declared if isinstance(declared, Mapping) else {}
    listed = schema.get("required")
    required = (
        {name for name in listed if isinstance(name, str)}
        if _is_array(listed)
        else set()
    )
    names = sorted(
        (name for name in properties if isinstance(name, str)), key=_utf16_order
    )
    members = [
        f"{_literal(name)}{'' if name in required else '?'}: "
        f"{_to_type(properties[name], context)}"
        for name in names
    ]
    # The type of the properties the schema does not name, if it allows any.
    others: str | None = None
    if "additionalProperties" in schema:
        additional = schema["additionalProperties"]
        if additional is not False:
            others = _UNKNOWN if additional is True else _to_type(additional, context)
    elif not names:
        others = _UNKNOWN
    if not names:
        return "{}" if others is None else f"dict[str, {others}]"
    if others is not None:
        members.append(f"str: {others}")

    comments: list[list[str]] = []
    for name in names:
        lines = [
            line.strip()
            for line in _LINE_TERMINATORS.split(_description_of(properties[name]))
        ]
        lines = [line for line in lines if line]
        tags = _constraint_tags(properties[name]) if context.tags else ""
        comments.append([*lines, tags] if tags else lines)
    if not any(comments):
        return "{" + ", ".join(members) + "}"

    rendered = ["{"]
    for index, member in enumerate(members):
        if index < len(comments):
            rendered.extend(f"{_INDENT}# {line}" for line in comments[index])
        indented = member.replace("\n", f"\n{_INDENT}")
        rendered.append(f"{_INDENT}{indented},")
    rendered.append("}")
    return "\n".join(rendered)

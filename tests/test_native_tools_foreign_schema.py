"""The standard-library argument validator for foreign-schema tools (MCP1 S01).

One accepting and one rejecting case for each keyword of the named set in
`tools/foreign_schema.py`, the rule that everything else is ignored, and the
edges that keep a partial validator from rejecting what a full one accepts.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from pipy_harness.native.tools import ToolArgumentError, validate_foreign_arguments
from pipy_harness.native.tools.foreign_schema import FOREIGN_SCHEMA_KEYWORDS


def _object(value_schema: Any, **extra: Any) -> dict[str, Any]:
    return {"type": "object", "properties": {"v": value_schema}, **extra}


def _check(schema: dict[str, Any], arguments: Any) -> dict[str, Any]:
    return validate_foreign_arguments(tool_name="t", schema=schema, arguments=arguments)


def _error(schema: dict[str, Any], arguments: Any) -> str:
    with pytest.raises(ToolArgumentError) as caught:
        _check(schema, arguments)
    return str(caught.value)


# keyword, schema of property `v`, accepted value, rejected value, error text
_VALUE_CASES: list[tuple[str, Any, Any, Any, str]] = [
    # Rejected values are ones no coercion turns into the declared type.
    ("type", {"type": "string"}, "a", [1], "t.v: expected string"),
    ("type", {"type": "number"}, 1.5, "1.5x", "t.v: expected number"),
    ("type", {"type": "number"}, 2, [2], "t.v: expected number"),
    ("type", {"type": "integer"}, 2.0, 2.5, "t.v: expected integer"),
    ("type", {"type": "boolean"}, False, "yes", "t.v: expected boolean"),
    ("type", {"type": "null"}, None, "x", "t.v: expected null"),
    ("type", {"type": "array"}, [], {}, "t.v: expected array"),
    ("type", {"type": "object"}, {}, [], "t.v: expected object"),
    ("type", {"type": ["string", "null"]}, None, [1], "t.v: expected string or null"),
    (
        "properties",
        {"type": "object", "properties": {"n": {"type": "integer"}}},
        {"n": 1, "other": "kept"},
        {"n": "one"},
        "t.v.n: expected integer",
    ),
    (
        "required",
        {"required": ["a", "b"]},
        {"a": 1, "b": 2},
        {"b": 2},
        "t.v: missing required argument(s): a",
    ),
    (
        "additionalProperties",
        {"properties": {"a": {}}, "additionalProperties": False},
        {"a": 1},
        {"a": 1, "z": 2, "y": 3},
        "t.v: unsupported argument(s): y, z",
    ),
    (
        "additionalProperties",
        {"additionalProperties": {"type": "string"}},
        {"a": "x"},
        {"a": 1},
        "t.v.a: expected string",
    ),
    (
        "items",
        {"type": "array", "items": {"type": "string"}},
        ["a", "b"],
        ["a", [2]],
        "t.v.[1]: expected string",
    ),
    (
        "items",
        {"items": [{"type": "string"}, {"type": "integer"}]},
        ["a", 1, "anything"],
        ["a", "b"],
        "t.v.[1]: expected integer",
    ),
    (
        "prefixItems",
        {"prefixItems": [{"type": "string"}], "items": {"type": "integer"}},
        ["a", 1, 2],
        ["a", 1, "b"],
        "t.v.[2]: expected integer",
    ),
    (
        "prefixItems",
        {"prefixItems": [{"type": "string"}], "items": False},
        ["a"],
        ["a", 1],
        "t.v.[1]: no value is allowed here",
    ),
    ("enum", {"enum": ["a", 2, None]}, None, "b", "t.v: value not in allowed set"),
    ("enum", {"enum": [1, 0]}, 1.0, True, "t.v: value not in allowed set"),
    (
        "const",
        {"const": {"k": [1, 2]}},
        {"k": [1, 2]},
        {"k": [1, 3]},
        "t.v: value does not equal the required constant",
    ),
    ("const", {"const": None}, None, 0, "t.v: value does not equal"),
    (
        "anyOf",
        {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        3,
        [3.5],
        "t.v: does not match any allowed schema",
    ),
    (
        "oneOf",
        {"oneOf": [{"type": "integer"}, {"type": "string"}]},
        "a",
        [],
        "t.v: does not match any allowed schema",
    ),
    (
        "allOf",
        {"allOf": [{"type": "string"}, {"minLength": 2}]},
        "ab",
        "a",
        "t.v: string shorter than minLength 2",
    ),
    ("minimum", {"minimum": 0.5}, 0.5, 0.25, "t.v: number below minimum 0.5"),
    ("maximum", {"maximum": 10}, 10, 10.5, "t.v: number above maximum 10"),
    (
        "exclusiveMinimum",
        {"exclusiveMinimum": 0},
        0.1,
        0,
        "t.v: number not above exclusiveMinimum 0",
    ),
    (
        "exclusiveMaximum",
        {"exclusiveMaximum": 1},
        0.9,
        1,
        "t.v: number not below exclusiveMaximum 1",
    ),
    ("minLength", {"minLength": 2}, "ab", "a", "t.v: string shorter than minLength 2"),
    ("maxLength", {"maxLength": 2}, "ab", "abc", "t.v: string longer than maxLength 2"),
    ("minItems", {"minItems": 1}, [0], [], "t.v: array shorter than minItems 1"),
    ("maxItems", {"maxItems": 1}, [0], [0, 1], "t.v: array longer than maxItems 1"),
    (
        "pattern",
        {"pattern": "^[a-z]+-\\d+$"},
        "abc-12",
        "abc",
        "t.v: string does not match pattern",
    ),
    # Since G6 (goal-check finding M11): the rest of what Pi's validator
    # checks, short of the keywords listed in the module docstring.
    ("multipleOf", {"multipleOf": 3}, 9, 10, "t.v: number not a multiple of 3"),
    ("multipleOf", {"multipleOf": 0.1}, 0.3, 0.35, "t.v: number not a multiple"),
    ("uniqueItems", {"uniqueItems": True}, [1, "1"], [1, 1.0], "t.v: array items"),
    (
        "contains",
        {"contains": {"type": "string"}},
        [1, "a"],
        [1, 2],
        "t.v: array contains fewer than 1 matching item(s)",
    ),
    (
        "minContains",
        {"contains": {"const": 1}, "minContains": 2},
        [1, 1],
        [1, 2],
        "t.v: array contains fewer than 2 matching item(s)",
    ),
    (
        "maxContains",
        {"contains": {"const": 1}, "maxContains": 1},
        [1, 2],
        [1, 1],
        "t.v: array contains more than 1 matching item(s)",
    ),
    (
        "additionalItems",
        {"items": [{"type": "string"}], "additionalItems": {"type": "integer"}},
        ["a", 1],
        ["a", [1]],
        "t.v.[1]: expected integer",
    ),
    ("minProperties", {"minProperties": 1}, {"a": 1}, {}, "t.v: object has fewer"),
    ("maxProperties", {"maxProperties": 1}, {"a": 1}, {"a": 1, "b": 2}, "has more"),
    (
        "propertyNames",
        {"propertyNames": {"pattern": "^[a-z]+$"}},
        {"ab": 1},
        {"Ab": 1},
        "t.v.Ab: string does not match pattern",
    ),
    (
        "patternProperties",
        {"patternProperties": {"^x-": {"type": "integer"}}},
        {"x-a": 1, "y": "s"},
        {"x-a": [1]},
        "t.v.x-a: expected integer",
    ),
    (
        "dependentRequired",
        {"dependentRequired": {"a": ["b"]}},
        {"a": 1, "b": 2},
        {"a": 1},
        "t.v: missing required argument(s): b",
    ),
    (
        "dependentSchemas",
        {"dependentSchemas": {"a": {"required": ["c"]}}},
        {"b": 1},
        {"a": 1},
        "t.v: missing required argument(s): c",
    ),
    (
        "dependencies",
        {"dependencies": {"a": ["b"], "c": {"maxProperties": 1}}},
        {"c": 1},
        {"c": 1, "d": 2},
        "t.v: object has more than 1 properties",
    ),
    ("not", {"not": {"type": "string"}}, [1], "a", "t.v: matches a schema it must"),
    (
        "if",
        {"if": {"type": "string"}, "then": {"minLength": 2}, "else": {"type": "array"}},
        "ab",
        "a",
        "t.v: string shorter than minLength 2",
    ),
    (
        "then",
        {"if": {"const": "a"}, "then": {"const": "a"}, "else": {"maxLength": 1}},
        "b",
        "bc",
        "t.v: string longer than maxLength 1",
    ),
    (
        "else",
        {"if": {"type": "array"}, "else": {"type": "object"}},
        {},
        "s",
        "t.v: expected object",
    ),
    ("format", {"format": "date"}, "2024-02-29", "2023-02-29", "not a valid date"),
    ("format", {"format": "email"}, "a@b.de", "a@", "t.v: string is not a valid email"),
    (
        "format",
        {"format": "uuid"},
        "0" * 8 + "-0000-0000-0000-" + "0" * 12,
        "x",
        "uuid",
    ),
    (
        "format",
        {"format": "date-time"},
        "2020-12-12T20:20:40Z",
        "2020-12-12",
        "date-time",
    ),
    ("format", {"format": "ipv4"}, "10.0.0.1", "10.0.0.256", "not a valid ipv4"),
]


@pytest.mark.parametrize(
    ("keyword", "value_schema", "accepted", "rejected", "message"), _VALUE_CASES
)
def test_each_keyword_accepts_and_rejects(
    keyword: str, value_schema: Any, accepted: Any, rejected: Any, message: str
) -> None:
    schema = _object(value_schema)

    assert _check(schema, {"v": accepted}) == {"v": accepted}
    assert message in _error(schema, {"v": rejected})


def test_every_keyword_of_the_named_set_has_a_case() -> None:
    covered = {keyword for keyword, *_rest in _VALUE_CASES} | {"$ref"}

    assert covered == FOREIGN_SCHEMA_KEYWORDS


_REF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "point": {"$ref": "#/$defs/point"},
        "tree": {"$ref": "#/$defs/node"},
        "odd": {"$ref": "#/$defs/a~1b~0c"},
        "first": {"$ref": "#/$defs/list/0"},
    },
    "$defs": {
        "point": {"type": "object", "required": ["x"]},
        "node": {
            "type": "object",
            "properties": {
                "value": {"type": "integer"},
                "children": {"type": "array", "items": {"$ref": "#/$defs/node"}},
            },
            "required": ["value"],
        },
        "a/b~c": {"type": "boolean"},
        "list": [{"type": "string"}],
    },
}


def test_a_local_ref_is_followed() -> None:
    tree = {"value": 1, "children": [{"value": 2, "children": [{"value": 3}]}]}
    arguments = {"point": {"x": 1}, "tree": tree, "odd": True, "first": "a"}

    assert _check(_REF_SCHEMA, arguments) == arguments
    assert _error(_REF_SCHEMA, {"point": {}}) == (
        "t.point: missing required argument(s): x"
    )
    assert _error(_REF_SCHEMA, {"tree": {"value": 1, "children": [{}]}}) == (
        "t.tree.children.[0]: missing required argument(s): value"
    )
    assert _error(_REF_SCHEMA, {"odd": 1}) == "t.odd: expected boolean"
    assert _error(_REF_SCHEMA, {"first": 1}) == "t.first: expected string"


def test_a_ref_to_the_root_and_a_ref_cycle_terminate() -> None:
    recursive = {
        "type": "object",
        "properties": {"child": {"$ref": "#"}, "n": {"type": "integer"}},
    }
    cycle = {
        "type": "object",
        "properties": {"v": {"$ref": "#/$defs/a"}},
        "$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}},
    }

    assert _check(recursive, {"child": {"child": {"n": 1}}}) == {
        "child": {"child": {"n": 1}}
    }
    assert _error(recursive, {"child": {"child": {"n": "1"}}}) == (
        "t.child.child.n: expected integer"
    )
    assert _check(cycle, {"v": 1}) == {"v": 1}


@pytest.mark.parametrize(
    "ref",
    [
        "https://example.com/s.json",
        "#/$defs/missing",
        "#anchor",
        "#/list/9",
        "#/list/" + "9" * 5000,
        "#/list/-1",
        "#/list/01",
        3,
    ],
)
def test_a_ref_that_cannot_be_resolved_is_ignored(ref: Any) -> None:
    schema = {
        "type": "object",
        "properties": {"v": {"$ref": ref}},
        "list": [False, False],
    }

    assert _check(schema, {"v": 1}) == {"v": 1}


def test_keywords_next_to_a_ref_apply_except_in_draft_7_and_older() -> None:
    properties = {"v": {"$ref": "#/$defs/s", "maxLength": 1}}
    current = {"type": "object", "properties": properties, "$defs": {"s": {}}}
    draft7 = {**current, "$schema": "http://json-schema.org/draft-07/schema#"}

    assert _error(current, {"v": "ab"}) == "t.v: string longer than maxLength 1"
    assert _check(draft7, {"v": "ab"}) == {"v": "ab"}


def test_unknown_keywords_are_ignored() -> None:
    schema = _object(
        {
            "type": "string",
            "format": "hostname",
            "contentEncoding": "base64",
            "x-vendor": {"anything": [1, 2]},
        },
        title="T",
        unevaluatedProperties=False,
    )

    assert _check(schema, {"v": "not a host!"}) == {"v": "not a host!"}
    # A format TypeBox does not know passes, as in Pi.
    assert _check(_object({"format": "x-mine"}), {"v": "s"}) == {"v": "s"}


@pytest.mark.parametrize(
    "value_schema",
    [
        {"type": "tuple"},
        {"type": ["date"]},
        {"type": [["string"]]},
        {"type": 3},
        {"required": "v"},
        {"required": ["missing", 42]},
        {"properties": ["v"]},
        {"enum": "abc"},
        {"anyOf": {"type": "string"}},
        {"oneOf": []},
        {"minimum": "1"},
        {"minimum": True},
        {"minLength": -1},
        {"maxLength": 1.5},
        {"maxItems": "2"},
        {"pattern": "(unclosed"},
        {"pattern": 3},
        {"prefixItems": {"type": "string"}},
        {"additionalProperties": "no"},
        True,
        None,
        "string",
    ],
)
def test_a_keyword_with_a_value_of_the_wrong_shape_is_ignored(
    value_schema: Any,
) -> None:
    schema = _object(value_schema)

    for value in ("text", 7, 2.5, [1, 2, 3], {"a": 1}, None, True):
        assert _check(schema, {"v": value}) == {"v": value}


def test_one_of_needs_exactly_one_match_where_pipy_can_tell() -> None:
    # Goal-check finding M11: two branches pipy enforces in full that both
    # match are one too many, as for a full validator.
    plain = _object({"oneOf": [{"type": "integer"}, {"type": "number"}]})
    assert _error(plain, {"v": 3}) == "t.v: matches more than one schema of oneOf"
    assert _check(plain, {"v": 3.5}) == {"v": 3.5}
    assert _error(plain, {"v": [3]}) == "t.v: does not match any allowed schema"
    # A branch with a keyword pipy does not enforce may only seem to match,
    # so a second apparent match is no proof; a full validator may accept.
    unsure = _object(
        {"oneOf": [{"const": "a.b"}, {"type": "string", "format": "hostname"}]}
    )
    assert _check(unsure, {"v": "a.b"}) == {"v": "a.b"}


def test_not_and_if_decide_only_where_pipy_can_tell() -> None:
    unsure_not = _object({"not": {"format": "hostname"}})
    assert _check(unsure_not, {"v": "x"}) == {"v": "x"}
    unsure_if = _object(
        {"if": {"format": "url"}, "then": {"maxLength": 1}, "else": {"minLength": 3}}
    )
    # Either branch might apply: only what both refuse is refused.
    assert _check(unsure_if, {"v": "a"}) == {"v": "a"}
    assert _check(unsure_if, {"v": "abc"}) == {"v": "abc"}
    assert "neither the then nor the else" in _error(unsure_if, {"v": "ab"})
    unsure_contains = _object({"contains": {"format": "iri"}, "maxContains": 1})
    assert _check(unsure_contains, {"v": ["a", "b"]}) == {"v": ["a", "b"]}


def test_a_ref_cycle_counts_as_enforced_and_terminates() -> None:
    schema = {
        "type": "object",
        "properties": {"v": {"oneOf": [{"$ref": "#/$defs/node"}, {"type": "object"}]}},
        "$defs": {
            "node": {"type": "object", "properties": {"next": {"$ref": "#/$defs/node"}}}
        },
    }
    assert "more than one" in _error(schema, {"v": {"next": {}}})


@pytest.mark.parametrize(
    "arguments",
    [{1: "x"}, {"v": {2: "x"}}, {"v": [{None: 1}]}, {"v": (1, 2)}, {"v": object()}],
)
def test_arguments_that_are_not_json_data_are_an_argument_error(
    arguments: Any,
) -> None:
    closed = {"type": "object", "additionalProperties": False}

    assert _error({"type": "object"}, arguments) == "t: arguments must be JSON values"
    assert _error(closed, arguments) == "t: arguments must be JSON values"


def test_a_false_schema_rejects_and_a_true_schema_accepts() -> None:
    assert _error(_object(False), {"v": 1}) == "t.v: no value is allowed here"
    assert _check(_object(True), {"v": 1}) == {"v": 1}


def test_pattern_properties_are_not_additional_properties() -> None:
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "patternProperties": {"^x-": {"type": "integer"}},
        "additionalProperties": False,
    }
    unreadable = {**schema, "patternProperties": {"(unclosed": {}}}

    # Names a pattern covers are allowed, and the pattern's schema checks
    # their values (since G6).
    assert _check(schema, {"name": "n", "x-a": 2}) == {"name": "n", "x-a": 2}
    assert _error(schema, {"x-a": [2]}) == "t.x-a: expected integer"
    assert _error(schema, {"y": 1}) == "t: unsupported argument(s): y"
    assert _check(unreadable, {"y": 1}) == {"y": 1}


def test_additional_properties_default_to_allowed() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}

    assert _check(schema, {"a": "x", "b": [1]}) == {"a": "x", "b": [1]}


def test_numbers_compare_as_json_numbers() -> None:
    huge = 10**400

    assert _check(_object({"type": "integer", "minimum": 1}), {"v": huge}) == {
        "v": huge
    }
    assert "expected number" in _error(_object({"type": "number"}), {"v": float("nan")})
    assert "expected integer" in _error(_object({"type": "integer"}), {"v": [1]})
    assert _check(_object({"enum": [[1, {"a": 1.0}]]}), {"v": [1.0, {"a": 1}]}) == {
        "v": [1.0, {"a": 1}]
    }
    assert "allowed set" in _error(_object({"enum": [[1]]}), {"v": [True]})


def test_arguments_are_returned_as_a_detached_unchanged_copy() -> None:
    arguments: dict[str, Any] = {
        "v": {"list": [1, {"deep": None}]},
        "undeclared": "kept",
    }

    validated = _check(_object({"type": "object"}), arguments)

    assert validated == arguments
    assert validated is not arguments
    assert validated["v"]["list"][1] is not arguments["v"]["list"][1]


@pytest.mark.parametrize("arguments", [[], "text", None, 3])
def test_arguments_that_are_not_an_object_are_rejected(arguments: Any) -> None:
    assert _error({"type": "object"}, arguments) == "t: expected object"


def test_arguments_nested_too_deeply_are_an_argument_error() -> None:
    schema = {"type": "object", "properties": {"child": {"$ref": "#"}}}
    arguments: dict[str, Any] = {}
    node = arguments
    for _ in range(100_000):
        node["child"] = {}
        node = node["child"]

    assert _error(schema, arguments) == "t: arguments are nested too deeply"


def test_the_caller_must_name_the_tool_and_pass_a_schema() -> None:
    with pytest.raises(ValueError, match="tool_name"):
        validate_foreign_arguments(tool_name="", schema={}, arguments={})
    with pytest.raises(ValueError, match="schema"):
        validate_foreign_arguments(
            tool_name="t", schema=cast(Any, "object"), arguments={}
        )


# -- Pi ``validateToolArguments``: optional nulls and coercion (goal check M11,
# T8; mirrors ``packages/ai/test/validation.test.ts``) ----------------------


def _value(value_schema: Any) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"value": value_schema},
        "required": ["value"],
    }


@pytest.mark.parametrize(
    ("value_schema", "given", "expected"),
    [
        # Pi: "coerces serialized plain JSON schemas with AJV-compatible
        # primitive rules"
        ({"type": "number"}, "42", 42),
        ({"type": "number"}, True, 1),
        ({"type": "number"}, None, 0),
        ({"type": "integer"}, "42", 42),
        ({"type": "boolean"}, "true", True),
        ({"type": "boolean"}, "false", False),
        ({"type": "boolean"}, 1, True),
        ({"type": "boolean"}, 0, False),
        ({"type": "string"}, None, ""),
        ({"type": "string"}, True, "true"),
        ({"type": "null"}, "", None),
        ({"type": "null"}, 0, None),
        ({"type": "null"}, False, None),
        ({"type": ["number", "string"]}, "1", "1"),
        ({"type": ["boolean", "number"]}, "1", 1),
        # Pi: "preserves a value that already matches a oneOf nullable union
        # arm" and "still coerces nullable unions when the original value does
        # not match any arm"
        ({"oneOf": [{"type": "number"}, {"type": "null"}]}, None, None),
        ({"anyOf": [{"type": "number"}, {"type": "null"}]}, "42", 42),
        # Pi: "accepts null for nullable array schemas with items"
        ({"type": ["array", "null"], "items": {"type": "string"}}, None, None),
        # JavaScript's Number() and String().
        ({"type": "number"}, " 1.5e3 ", 1500),
        ({"type": "number"}, "0x1f", 31),
        ({"type": "number"}, ".5", 0.5),
        ({"type": "integer"}, "5.0", 5),
        ({"type": "string"}, 1.5, "1.5"),
        ({"type": "string"}, 5.0, "5"),
        ({"type": "string"}, 1e21, "1e+21"),
        ({"type": "string"}, 1e-7, "1e-7"),
        ({"type": "string"}, 0.000001, "0.000001"),
        ({"type": "string"}, -2.5e-10, "-2.5e-10"),
        # JavaScript reads a JSON number as a double: rounded, or Infinity.
        ({"type": "string"}, 1000000000000000128, "1000000000000000100"),
        ({"type": "string"}, 9007199254740993, "9007199254740992"),
        ({"type": "string"}, 10**400, "Infinity"),
        ({"type": "string"}, -(10**400), "-Infinity"),
        # Through properties, additionalProperties and items.
        (
            {
                "type": "object",
                "properties": {"n": {"type": "integer"}},
                "additionalProperties": {"type": "boolean"},
            },
            {"n": "3", "flag": "true"},
            {"n": 3, "flag": True},
        ),
        ({"type": "array", "items": {"type": "number"}}, ["1", "2"], [1, 2]),
    ],
)
def test_primitives_are_coerced_as_pi_coerces_them(
    value_schema: Any, given: Any, expected: Any
) -> None:
    result = _check(_value(value_schema), {"value": given})

    assert result == {"value": expected}
    assert type(result["value"]) is type(expected)


@pytest.mark.parametrize(
    ("value_schema", "given"),
    [
        # Pi: "rejects invalid coercions for serialized plain JSON schemas"
        ({"type": "boolean"}, "1"),
        ({"type": "boolean"}, "0"),
        ({"type": "null"}, "null"),
        ({"type": "integer"}, "42.1"),
        ({"type": "number"}, "Infinity"),
        ({"type": "number"}, "1_000"),
        ({"type": "number"}, "  "),
        # Beyond the double range JavaScript reads Infinity, which is not finite.
        ({"type": "number"}, "0x" + "f" * 300),
        ({"type": "number"}, "1e400"),
    ],
)
def test_values_no_coercion_fits_are_still_refused(
    value_schema: Any, given: Any
) -> None:
    _error(_value(value_schema), {"value": given})


def test_an_optional_null_the_schema_refuses_is_dropped() -> None:
    # Pi: "treats null as omission for optional non-nullable properties"
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "number"},
            "nullable": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "metadata": {
                "type": "object",
                "properties": {"enabled": {"type": "boolean"}},
            },
        },
        "required": ["path", "metadata"],
    }

    assert _check(
        schema,
        {
            "path": "file.txt",
            "offset": None,
            "nullable": None,
            "metadata": {"enabled": None},
        },
    ) == {"path": "file.txt", "nullable": None, "metadata": {}}
    # A required property's null is coerced instead.
    assert _check(_value({"type": "number"}), {"value": None}) == {"value": 0}


def test_an_optional_null_whose_referenced_schema_is_nullable_is_kept() -> None:
    # Pi: "preserves optional nulls whose referenced schema is nullable"
    schema = {
        "type": "object",
        "properties": {"value": {"$ref": "#/$defs/value"}},
        "$defs": {"value": {"anyOf": [{"type": "number"}, {"type": "null"}]}},
    }

    assert _check(schema, {"value": None}) == {"value": None}


def test_coercion_leaves_the_callers_arguments_unchanged() -> None:
    arguments: dict[str, Any] = {"value": ["1"], "offset": None}
    schema = {
        "type": "object",
        "properties": {
            "value": {"type": "array", "items": {"type": "number"}},
            "offset": {"type": "number"},
        },
    }

    assert _check(schema, arguments) == {"value": [1]}
    assert arguments == {"value": ["1"], "offset": None}


def test_a_ref_cycle_with_a_skipped_keyword_is_uncertain_on_every_path() -> None:
    # A references B before a format pipy does not check, and B references
    # A: neither is enforced in full, whichever is asked about first.
    defs = {
        "a": {"allOf": [{"$ref": "#/$defs/b"}, {"format": "hostname"}]},
        "b": {"anyOf": [{"$ref": "#/$defs/a"}, {"type": "string"}]},
    }
    schema = {
        "type": "object",
        "properties": {
            "probe": {"if": {"$ref": "#/$defs/a"}, "then": True},
            "v": {"not": {"$ref": "#/$defs/b"}},
        },
        "$defs": defs,
    }

    assert _check(schema, {"probe": 1, "v": "not a host!"}) == {
        "probe": 1,
        "v": "not a host!",
    }


def test_a_nested_id_makes_its_refs_uncertain() -> None:
    schema = {
        "type": "object",
        "properties": {
            "v": {
                "$id": "https://example.com/inner",
                "$defs": {"target": False},
                "not": {"$ref": "#/$defs/target"},
            }
        },
        "$defs": {"target": {}},
    }

    assert _check(schema, {"v": 1}) == {"v": 1}


@pytest.mark.parametrize(
    "forbidden",
    [
        {"oneOf": []},
        {"maxLength": 1.5},
        {"type": "date"},
        {"required": [1]},
        {"pattern": "(?<word>.*)"},
        {"dependentRequired": {"a": False}},
    ],
)
def test_a_keyword_the_checks_skip_makes_not_refuse_nothing(forbidden: Any) -> None:
    assert _check(_object({"not": forbidden}), {"v": {"a": 1}}) == {"v": {"a": 1}}


def test_additional_items_follow_an_empty_list_of_items() -> None:
    schema = _object({"items": [], "additionalItems": False})

    assert _check(schema, {"v": []}) == {"v": []}
    assert _error(schema, {"v": [1]}) == "t.v.[0]: no value is allowed here"
    assert _check(_object({"not": {"items": [], "additionalItems": False}}), {"v": [1]})


def test_dependency_keywords_take_only_their_own_shape() -> None:
    malformed = _object(
        {"dependentRequired": {"a": False}, "dependentSchemas": {"a": ["b"]}}
    )

    assert _check(malformed, {"v": {"a": 1}}) == {"v": {"a": 1}}


def test_a_pattern_re_cannot_read_still_leaves_the_format_checked() -> None:
    schema = _object({"pattern": "(?<word>.*)", "format": "date"})

    assert "not a valid date" in _error(schema, {"v": "not a date"})


def test_draft_7_ignores_the_siblings_of_a_ref_pipy_does_not_follow() -> None:
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            "v": {
                "$id": "https://example.com/inner",
                "properties": {
                    "x": {"$ref": "#/definitions/target", "maxProperties": 0}
                },
            }
        },
        "definitions": {"target": {}},
    }

    assert _check(schema, {"v": {"x": {"a": 1}}}) == {"v": {"x": {"a": 1}}}


@pytest.mark.parametrize("scoped_first", [False, True])
def test_a_shared_schema_reached_under_a_nested_id_is_scoped(
    scoped_first: bool,
) -> None:
    shared = {"not": {"$ref": "#/$defs/target"}}
    plain = ("plain", shared)
    scoped = (
        "scoped",
        {
            "$id": "https://example.com/inner",
            "$defs": {"target": False},
            "properties": {"w": shared},
        },
    )
    properties = dict([scoped, plain] if scoped_first else [plain, scoped])
    schema = {"type": "object", "properties": properties, "$defs": {"target": {}}}

    assert _check(schema, {"scoped": {"w": 1}}) == {"scoped": {"w": 1}}

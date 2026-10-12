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
    ("type", {"type": "string"}, "a", 1, "t.v: expected string"),
    ("type", {"type": "number"}, 1.5, "1.5", "t.v: expected number"),
    ("type", {"type": "number"}, 2, True, "t.v: expected number"),
    ("type", {"type": "integer"}, 2.0, 2.5, "t.v: expected integer"),
    ("type", {"type": "boolean"}, False, 0, "t.v: expected boolean"),
    ("type", {"type": "null"}, None, "", "t.v: expected null"),
    ("type", {"type": "array"}, [], {}, "t.v: expected array"),
    ("type", {"type": "object"}, {}, [], "t.v: expected object"),
    ("type", {"type": ["string", "null"]}, None, 1, "t.v: expected string or null"),
    (
        "properties",
        {"type": "object", "properties": {"n": {"type": "integer"}}},
        {"n": 1, "other": "kept"},
        {"n": "1"},
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
        ["a", 2],
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
        3.5,
        "t.v: does not match any allowed schema",
    ),
    (
        "oneOf",
        {"oneOf": [{"type": "integer"}, {"type": "string"}]},
        "a",
        None,
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
            "format": "email",
            "not": {"type": "string"},
            "if": {"const": "a"},
            "then": False,
            "contentEncoding": "base64",
            "x-vendor": {"anything": [1, 2]},
            "multipleOf": 3,
        },
        title="T",
        unevaluatedProperties=False,
        dependentRequired={"v": ["w"]},
    )

    assert _check(schema, {"v": "not an email"}) == {"v": "not an email"}


@pytest.mark.parametrize(
    "value_schema",
    [
        {"type": "tuple"},
        {"type": ["string", "date"]},
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


def test_one_of_does_not_reject_a_second_apparent_match() -> None:
    # `multipleOf` is not enforced, so the second branch only seems to match 1.
    # A full validator accepts 1; rejecting it here would be wrong.
    narrowed = _object({"oneOf": [{"const": 1}, {"type": "number", "multipleOf": 2}]})
    plain = _object({"oneOf": [{"type": "integer"}, {"type": "number"}]})

    assert _check(narrowed, {"v": 1}) == {"v": 1}
    assert _check(plain, {"v": 3}) == {"v": 3}
    assert _error(plain, {"v": "3"}) == "t.v: does not match any allowed schema"


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

    # Names a pattern covers are allowed; their values are not checked.
    assert _check(schema, {"name": "n", "x-a": "kept"}) == {"name": "n", "x-a": "kept"}
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
    assert "expected integer" in _error(_object({"type": "integer"}), {"v": True})
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

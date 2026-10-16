"""Tool declarations for codemode scripts (MCP parity S05, CMD-11 to CMD-15).

The first two groups mirror the ``schemaToType`` and "tool declarations"
groups of Pi's ``packages/codemode/test/declarations.test.ts`` with the same
schemas and the expected text in Python syntax; each test names its Pi case.
"""

from __future__ import annotations

import json
import sys

import pytest

from pipy_harness.native.codemode.declarations import (
    DEFAULT_INPUT_SCHEMA_MAX_CHARS,
    render_tool_output_type,
    render_tool_sample,
    render_tool_signature,
    schema_to_type,
    to_codemode_identifier,
)

# --- Pi: describe("schemaToType") ------------------------------------------


def test_renders_primitives_literals_and_unions() -> None:
    # Pi: "renders primitives, literals, and unions"
    assert schema_to_type({"type": "string"}) == "str"
    assert schema_to_type({"type": "integer"}) == "float"
    assert schema_to_type({"type": ["string", "null"]}) == "str | None"
    assert schema_to_type({"const": "a"}) == '"a"'
    assert schema_to_type({"enum": ["a", 1, None]}) == '"a" | 1 | None'
    assert (
        schema_to_type({"anyOf": [{"type": "string"}, {"type": "number"}]})
        == "str | float"
    )
    assert schema_to_type({"anyOf": [{"type": "string"}, {}]}) == "Any"
    assert (
        schema_to_type(
            {
                "allOf": [
                    {"anyOf": [{"type": "string"}, {"type": "number"}]},
                    {"const": 1},
                ]
            }
        )
        == "(str | float) & 1"
    )
    assert schema_to_type({"$ref": "#/defs/x"}) == "Any"
    assert schema_to_type(True) == "Any"
    assert schema_to_type(False) == "Never"


def test_renders_objects_on_one_line_with_sorted_properties() -> None:
    # Pi: "renders objects on one line with sorted properties"
    assert (
        schema_to_type(
            {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "max-lines": {"type": "number"},
                },
                "required": ["city"],
                "additionalProperties": False,
            }
        )
        == '{"city": str, "max-lines"?: float}'
    )
    assert (
        schema_to_type({"type": "object", "additionalProperties": {"type": "number"}})
        == "dict[str, float]"
    )
    assert schema_to_type({"type": "object"}) == "dict[str, Any]"
    assert (
        schema_to_type(
            {"type": "object", "properties": {}, "additionalProperties": False}
        )
        == "{}"
    )


def test_puts_property_descriptions_on_comment_lines() -> None:
    # Pi: "puts property descriptions on comment lines"
    assert schema_to_type(
        {
            "type": "object",
            "properties": {
                "weather": {
                    "type": "array",
                    "description": "look up weather for a given list of locations",
                    "items": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                        "required": ["location"],
                    },
                },
            },
            "required": ["weather"],
        }
    ) == (
        "{\n"
        "  # look up weather for a given list of locations\n"
        '  "weather": list[{"location": str}],\n'
        "}"
    )
    assert schema_to_type(
        {
            "type": "object",
            "properties": {
                "outer": {
                    "type": "object",
                    "description": "Outer",
                    "properties": {"inner": {"type": "string", "description": "Inner"}},
                },
            },
        }
    ) == ('{\n  # Outer\n  "outer"?: {\n    # Inner\n    "inner"?: str,\n  },\n}')


def test_puts_constraints_the_type_cannot_express_on_property_comment_lines() -> None:
    # Pi: "puts constraints the type cannot express on property comment lines"
    assert schema_to_type(
        {
            "type": "object",
            "properties": {
                "timeoutMs": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 30000,
                    "default": 0,
                    "description": "Wait in milliseconds.",
                },
                "id": {"type": "string", "pattern": "^[a-z]+$", "maxLength": 8},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 5,
                    "uniqueItems": True,
                    "deprecated": True,
                },
                "name": {"type": "string"},
            },
            "required": ["id"],
        }
    ) == "\n".join(
        [
            "{",
            "  # @maxLength 8 @pattern ^[a-z]+$",
            '  "id": str,',
            '  "name"?: str,',
            "  # @maxItems 5 @uniqueItems @deprecated",
            '  "tags"?: list[str],',
            "  # Wait in milliseconds.",
            "  # @integer @minimum 0 @maximum 30000 @default 0",
            '  "timeoutMs"?: float,',
            "}",
        ]
    )


def test_reads_constraints_of_optional_fields_from_the_non_null_branch() -> None:
    # Pi: "reads constraints of optional fields from the non-null branch"
    # Pydantic's `limit: int | None = Field(None, ge=1, le=100)`.
    assert (
        schema_to_type(
            {
                "type": "object",
                "properties": {
                    "limit": {
                        "anyOf": [
                            {"type": "integer", "minimum": 1, "maximum": 100},
                            {"type": "null"},
                        ],
                        "default": None,
                        "title": "Limit",
                    },
                },
            }
        )
        == '{\n  # @integer @minimum 1 @maximum 100\n  "limit"?: float | None,\n}'
    )
    # Constraints of the property and the branch both apply.
    assert (
        schema_to_type(
            {
                "type": "object",
                "properties": {
                    "n": {
                        "anyOf": [
                            {
                                "type": "integer",
                                "minimum": 10,
                                "maximum": 50,
                                "format": "int32",
                            },
                            {"type": "null"},
                        ],
                        "minimum": 0,
                        "maximum": 20,
                        "format": "port",
                    },
                },
            }
        )
        == "{\n"
        "  # @integer @minimum 10 @maximum 20 @format port @format int32\n"
        '  "n"?: float | None,\n'
        "}"
    )


def test_reads_draft_04_boolean_exclusive_bounds() -> None:
    # Pi: "reads draft-04 boolean exclusive bounds"
    assert schema_to_type(
        {
            "type": "object",
            "properties": {
                "n": {
                    "type": "integer",
                    "minimum": 0,
                    "exclusiveMinimum": True,
                    "maximum": 10,
                    "exclusiveMaximum": False,
                },
                "m": {
                    "anyOf": [
                        {"type": "number", "maximum": 5, "exclusiveMaximum": True},
                        {"type": "null"},
                    ],
                    "maximum": 8,
                },
            },
        }
    ) == "\n".join(
        [
            "{",
            "  # @maximum 8 @exclusiveMaximum 5",
            '  "m"?: float | None,',
            "  # @integer @exclusiveMinimum 0 @maximum 10",
            '  "n"?: float,',
            "}",
        ]
    )


def test_leaves_out_long_tags_and_escapes_line_terminators() -> None:
    # Pi: "leaves out long tags and escapes line terminators"
    assert (
        schema_to_type(
            {
                "type": "object",
                "properties": {
                    "config": {"type": "object", "default": {"text": "x" * 100}}
                },
            }
        )
        == '{"config"?: dict[str, Any]}'
    )
    assert schema_to_type(
        {
            "type": "object",
            "properties": {
                "sep": {
                    "type": "string",
                    "pattern": "^[\r\n  ]$",
                    "default": " ",
                    "description": "a b",
                },
            },
        }
    ) == "\n".join(
        [
            "{",
            "  # a",
            "  # b",
            '  # @pattern ^[\\u000d\\u000a\\u2028\\u2029]$ @default "\\u2028"',
            '  "sep"?: str,',
            "}",
        ]
    )


def test_drops_constraint_tags_before_rendering_a_type_over_the_budget_as_unknown() -> (
    None
):
    # Pi: "drops constraint tags before rendering a type over the budget as unknown"
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer", "minimum": 1}},
    }
    assert schema_to_type(schema) == '{\n  # @integer @minimum 1\n  "n"?: float,\n}'
    assert schema_to_type(schema, max_chars=20) == '{"n"?: float}'
    assert schema_to_type(schema, max_chars=10) == "Any"


def test_resolves_local_references_and_stops_at_recursive_ones() -> None:
    # Pi: "resolves local references and stops at recursive ones"
    schema = {
        "type": "object",
        "properties": {
            "item": {"$ref": "#/$defs/Item"},
            "legacy": {"$ref": "#/definitions/Legacy"},
            "remote": {"$ref": "https://example.com/schema.json"},
        },
        "required": ["item"],
        "$defs": {
            "Item": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "parent": {"$ref": "#/$defs/Item"},
                },
                "required": ["id"],
            },
        },
        "definitions": {"Legacy": {"enum": ["a", "b"]}},
    }
    assert schema_to_type(schema) == (
        '{"item": {"id": str, "parent"?: Any}, "legacy"?: "a" | "b", "remote"?: Any}'
    )


def test_renders_arrays_and_tuples() -> None:
    # Pi: "renders arrays and tuples"
    assert schema_to_type({"type": "array", "items": {"type": "string"}}) == "list[str]"
    assert (
        schema_to_type(
            {"type": "array", "prefixItems": [{"type": "string"}, {"type": "number"}]}
        )
        == "[str, float]"
    )
    assert schema_to_type({"type": "array"}) == "list[Any]"


def test_renders_types_over_the_budget_as_unknown() -> None:
    # Pi: "renders types over the budget as unknown"
    schema = {
        "type": "object",
        "properties": {f"field{i}": {"type": "string"} for i in range(50)},
    }
    assert schema_to_type(schema, max_chars=100) == "Any"
    assert '"field49"?: str' in schema_to_type(schema)


# --- Pi: describe("tool declarations") -------------------------------------


def test_renders_signatures_with_normalized_identifiers() -> None:
    # Pi: "renders signatures with normalized identifiers"
    assert (
        render_tool_signature(
            "hidden-dynamic-tool",
            {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
        )
        == 'hidden_dynamic_tool(args: {"city": str}) -> {"ok": bool}'
    )
    assert render_tool_signature("free", None) == "free(args: Any) -> Any"


def test_renders_output_schemas_shaped_like_an_mcp_call_tool_result_as_plain_types() -> (
    None
):
    # Pi: "renders output schemas shaped like an MCP CallToolResult as plain types"
    output_schema = {
        "type": "object",
        "properties": {
            "content": {"type": "array", "items": {"type": "object"}},
            "isError": {"type": "boolean"},
            "_meta": {"type": "object"},
        },
        "required": ["content"],
    }
    assert render_tool_signature(
        "plain", {"type": "object", "properties": {}}, output_schema
    ) == (
        "plain(args: dict[str, Any]) -> "
        '{"_meta"?: dict[str, Any], "content": list[dict[str, Any]], "isError"?: bool}'
    )


def test_renders_the_per_tool_sample() -> None:
    # Pi: "renders the per-tool sample"
    assert render_tool_sample("foo", "bar", {"type": "string"}) == (
        "bar\n\ncodemode tool declaration:\n```python\ntools.foo(args: str) -> Any\n```"
    )


# --- pipy: Python spelling and the edges of the rules above ----------------


def test_identifiers_replace_what_python_cannot_spell() -> None:
    assert to_codemode_identifier("mcp__docs__search") == "mcp__docs__search"
    assert (
        to_codemode_identifier("mcp__dev-radius__search") == "mcp__dev_radius__search"
    )
    assert to_codemode_identifier("9lives") == "_lives"
    # `$` is an identifier character in JavaScript, not in Python.
    assert to_codemode_identifier("a$b.c") == "a_b_c"
    assert to_codemode_identifier("größe") == "gr__e"
    assert to_codemode_identifier("") == "_"


def test_literals_are_python_literals() -> None:
    assert schema_to_type({"enum": [True, False, None, 1.0, 2.5, "x"]}) == (
        'True | False | None | 1 | 2.5 | "x"'
    )
    assert schema_to_type({"const": {"a": [1, None, True], "b": "q"}}) == (
        '{"a":[1,None,True],"b":"q"}'
    )
    # A literal cannot end the line it is written on.
    assert schema_to_type({"const": "a\nb c"}) == '"a\\nb\\u2028c"'
    assert schema_to_type({"const": "\ud800"}) == '"\\ud800"'
    assert schema_to_type({"enum": ["a", "a", 1]}) == '"a" | 1'
    assert schema_to_type({"enum": []}) == "Never"


def test_an_object_with_declared_and_other_properties_lists_both() -> None:
    assert (
        schema_to_type(
            {
                "properties": {"b": {"type": "boolean"}, "a": {"type": "string"}},
                "additionalProperties": {"type": "integer"},
            }
        )
        == '{"a"?: str, "b"?: bool, str: float}'
    )
    assert (
        schema_to_type(
            {
                "type": "object",
                "properties": {"a": {"type": "string", "description": "First."}},
                "additionalProperties": True,
            }
        )
        == '{\n  # First.\n  "a"?: str,\n  str: Any,\n}'
    )
    assert schema_to_type({"required": ["a"]}) == "dict[str, Any]"
    assert schema_to_type({"additionalProperties": False}) == "{}"
    # Property names that are not identifiers, and ones that need escaping.
    assert schema_to_type(
        {
            "properties": {'q"x': {"type": "null"}, "a\nb": {}},
            "additionalProperties": False,
        }
    ) == ('{"a\\nb"?: Any, "q\\"x"?: None}')


def test_arrays_without_a_type_and_positional_items() -> None:
    assert schema_to_type({"items": {"type": "boolean"}}) == "list[bool]"
    assert schema_to_type({"items": [{"type": "string"}, {"type": "null"}]}) == (
        "[str, None]"
    )
    assert schema_to_type({"prefixItems": []}) == "list[Any]"
    assert schema_to_type({"type": "array", "items": True}) == "list[Any]"


def test_unknown_shapes_are_any() -> None:
    assert schema_to_type({}) == "Any"
    assert schema_to_type({"type": "date"}) == "Any"
    assert schema_to_type({"type": None, "properties": {}}) == "Any"
    assert schema_to_type("string") == "Any"
    assert schema_to_type(None) == "Any"
    assert schema_to_type({"allOf": [{}, True]}) == "Any"
    assert schema_to_type({"allOf": [{}, {"type": "string"}]}) == "str"
    assert schema_to_type({"type": ["string", "date"]}) == "Any"
    # `oneOf` is read only when `anyOf` is not a list.
    assert schema_to_type({"anyOf": 1, "oneOf": [{"type": "string"}]}) == "str"
    assert schema_to_type({"anyOf": [{"type": "null"}], "oneOf": [{}]}) == "None"


def test_references_follow_json_pointers() -> None:
    schema = {
        "properties": {
            "escaped": {"$ref": "#/$defs/a~1b~0c"},
            "encoded": {"$ref": "#/$defs/with%20space"},
            "flag": {"$ref": "#/$defs/yes"},
            "missing": {"$ref": "#/$defs/none"},
            "index": {"$ref": "#/list/0"},
            "scalar": {"$ref": "#/$defs/number"},
        },
        "additionalProperties": False,
        "$defs": {
            "a/b~c": {"type": "string"},
            "with space": {"type": "boolean"},
            "yes": False,
            "number": 3,
        },
        "list": [{"type": "string"}],
    }
    # `#` is the whole schema, expanded once; a pointer into a list names nothing.
    assert schema_to_type({"$ref": "#"}) == "Any"
    assert schema_to_type(
        {"properties": {"self": {"$ref": "#"}}, "additionalProperties": False}
    ) == ('{"self"?: {"self"?: Any}}')
    assert schema_to_type(schema) == (
        '{"encoded"?: bool, "escaped"?: str, "flag"?: Never, "index"?: Any, '
        '"missing"?: Any, "scalar"?: Any}'
    )
    # Keywords next to a reference are not read.
    assert schema_to_type({"$ref": "#/$defs/x", "type": "string"}) == "Any"


def test_more_than_32_references_render_as_any() -> None:
    schema = {
        "type": "array",
        "prefixItems": [{"$ref": "#/$defs/Item"}] * 34,
        "$defs": {"Item": {"type": "string"}},
    }
    assert schema_to_type(schema) == "[" + ", ".join(["str"] * 32 + ["Any"] * 2) + "]"


def test_a_shared_definition_may_be_used_more_than_once() -> None:
    schema = {
        "properties": {"a": {"$ref": "#/$defs/P"}, "b": {"$ref": "#/$defs/P"}},
        "additionalProperties": False,
        "$defs": {"P": {"type": "null"}},
    }
    assert schema_to_type(schema) == '{"a"?: None, "b"?: None}'


def test_tags_cover_every_keyword_in_pi_order() -> None:
    schema = {
        "properties": {
            "p": {
                "type": ["integer", "null"],
                "minimum": 1,
                "exclusiveMinimum": 0,
                "maximum": 9,
                "exclusiveMaximum": 10,
                "multipleOf": 0.5,
                "minLength": 2,
                "maxLength": 3,
                "pattern": "x",
                "format": "date",
                "minItems": 4,
                "maxItems": 5,
                "uniqueItems": True,
                "default": {"a": [1.0, True, None]},
                "deprecated": True,
            }
        }
    }
    assert schema_to_type(schema) == (
        "{\n"
        "  # @integer @minimum 1 @exclusiveMinimum 0 @maximum 9 @exclusiveMaximum 10"
        " @multipleOf 0.5 @minLength 2 @maxLength 3 @pattern x @format date"
        " @minItems 4 @maxItems 5 @uniqueItems"
        ' @default {"a":[1,true,null]} @deprecated\n'
        '  "p"?: float | None,\n'
        "}"
    )


def test_tags_ignore_values_of_the_wrong_kind() -> None:
    schema = {
        "properties": {
            "p": {
                "type": "string",
                "minimum": "1",
                "maximum": True,
                "minLength": None,
                "pattern": ["x"],
                "format": False,
                "multipleOf": float("nan"),
                "uniqueItems": 1,
                "deprecated": "yes",
                "default": None,
            },
            "q": True,
        },
        "additionalProperties": False,
    }
    assert schema_to_type(schema) == '{"p"?: str, "q"?: Any}'


def test_the_non_null_branch_is_read_only_when_it_is_the_only_one() -> None:
    def tags(schema: object) -> str:
        return schema_to_type({"properties": {"p": schema}}).split("\n")[1].strip()

    two = [{"type": "integer", "minimum": 1}, {"type": "string"}, {"type": "null"}]
    assert schema_to_type({"properties": {"p": {"anyOf": two}}}) == (
        '{"p"?: float | str | None}'
    )
    assert tags({"oneOf": [{"type": "integer", "minLength": 2}], "minLength": 5}) == (
        "# @integer @minLength 5"
    )
    # The same value from both sources is one tag; different ones are both shown.
    assert tags(
        {
            "anyOf": [{"type": "string", "format": "uri", "multipleOf": 2}],
            "format": "uri",
        }
    ) == ("# @multipleOf 2 @format uri")
    # A draft-04 flag without its bound says nothing.
    assert tags({"type": "number", "exclusiveMinimum": True, "maxLength": 1}) == (
        "# @maxLength 1"
    )


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (3, "3"),
        (3.0, "3"),
        (-0.0, "0"),
        (0.25, "0.25"),
        (1e21, "1e+21"),
        (1e-7, "1e-7"),
        (0.00001, "0.00001"),
        (1.5e-10, "1.5e-10"),
        (1.23456789e20, "123456789000000000000"),
        (1e16, "10000000000000000"),
        (-2.5e-7, "-2.5e-7"),
        (123456.789, "123456.789"),
        (2**53 - 1, "9007199254740991"),
        (10**20 + 1, "100000000000000000000"),
        (10**25, "1e+25"),
    ],
)
def test_numbers_are_written_as_javascript_writes_them(value: float, text: str) -> None:
    assert schema_to_type({"properties": {"n": {"multipleOf": value}}}) == (
        f'{{\n  # @multipleOf {text}\n  "n"?: Any,\n}}'
    )


def test_a_tag_of_80_characters_is_kept_and_one_of_81_is_not() -> None:
    def render(pattern: str) -> str:
        return schema_to_type(
            {
                "properties": {"p": {"type": "string", "pattern": pattern}},
                "additionalProperties": False,
            }
        )

    assert render("x" * 71) == f'{{\n  # @pattern {"x" * 71}\n  "p"?: str,\n}}'
    assert render("x" * 72) == '{"p"?: str}'


def test_descriptions_are_trimmed_and_blank_lines_dropped() -> None:
    schema = {
        "properties": {
            "p": {"type": "string", "description": "  one \r\n\n  two  \n"},
            "q": {"type": "string", "description": 3},
        },
        "additionalProperties": False,
    }
    assert (
        schema_to_type(schema) == '{\n  # one\n  # two\n  "p"?: str,\n  "q"?: str,\n}'
    )


def test_properties_sort_by_utf16_code_unit_like_pi() -> None:
    # U+FF5E sorts after U+1F600 in JavaScript (0xFF5E > 0xD83D).
    schema = {
        "properties": {"～": {"type": "null"}, "\U0001f600": {"type": "null"}},
        "additionalProperties": False,
    }
    assert schema_to_type(schema) == '{"\U0001f600"?: None, "～"?: None}'


def test_the_default_input_budget_drops_tags_and_then_the_type() -> None:
    assert DEFAULT_INPUT_SCHEMA_MAX_CHARS == 16_000

    def schema(count: int) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                f"field{i:04}": {"type": "integer", "minimum": 1} for i in range(count)
            },
            "additionalProperties": False,
        }

    tagged = render_tool_signature("t", schema(100))
    assert "# @integer @minimum 1" in tagged
    # 600 properties: about 27,000 characters with tags, about 11,000 without.
    assert len(schema_to_type(schema(600))) > DEFAULT_INPUT_SCHEMA_MAX_CHARS
    untagged = render_tool_signature("t", schema(600))
    assert "@" not in untagged and '"field0599"?: float' in untagged
    assert render_tool_signature("t", schema(2000)) == "t(args: Any) -> Any"
    # The budget is the input's; an output type is never cut.
    assert len(render_tool_signature("t", None, schema(2000))) > 30_000
    assert render_tool_signature("t", schema(100), input_max_chars=10) == (
        "t(args: Any) -> Any"
    )


def test_signature_and_sample_of_a_documented_tool() -> None:
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to look for."},
            "limit": {"type": "integer", "minimum": 1, "default": 20},
        },
        "required": ["query"],
    }
    assert render_tool_output_type(None) == "Any"
    assert render_tool_output_type({"type": "array", "items": {"type": "string"}}) == (
        "list[str]"
    )
    assert render_tool_sample(
        "mcp__docs-site__search",
        "  Search the docs.\n",
        schema,
        {"type": "array", "items": {"type": "string"}},
    ) == "\n".join(
        [
            "Search the docs.",
            "",
            "codemode tool declaration:",
            "```python",
            "tools.mcp__docs_site__search(args: {",
            "  # @integer @minimum 1 @default 20",
            '  "limit"?: float,',
            "  # What to look for.",
            '  "query": str,',
            "}) -> list[str]",
            "```",
        ]
    )
    assert render_tool_sample("x", None, None, input_max_chars=5) == (
        "\n\ncodemode tool declaration:\n```python\ntools.x(args: Any) -> Any\n```"
    )


def test_a_schema_too_deep_to_follow_is_any() -> None:
    schema: dict[str, object] = {"type": "string"}
    for _ in range(sys.getrecursionlimit()):
        schema = {"type": "array", "items": schema}
    assert schema_to_type(schema) == "Any"
    assert render_tool_signature("deep", schema, schema) == "deep(args: Any) -> Any"


def test_schemas_parsed_from_json_render_like_python_values() -> None:
    text = '{"type":"object","properties":{"n":{"type":"number","maximum":1e2}}}'
    assert schema_to_type(json.loads(text)) == '{\n  # @maximum 100\n  "n"?: float,\n}'


def test_an_integer_no_javascript_number_can_hold_is_not_a_number() -> None:
    huge = 10**4400  # More digits than Python converts to text.
    assert schema_to_type({"const": huge}) == "None"
    assert schema_to_type({"enum": [huge, 10**400, 1]}) == "None | 1"
    schema = {
        "properties": {
            "p": {"type": "integer", "minimum": huge, "maximum": 5, "default": huge}
        },
        "additionalProperties": False,
    }
    assert schema_to_type(schema) == (
        '{\n  # @integer @maximum 5 @default null\n  "p"?: float,\n}'
    )

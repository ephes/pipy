"""Contract tests for `pipy_harness.native.tools.base`.

These tests pin the slice 2 surface of the Tool-Loop Parity Track: the
contracts in `tools/base.py` exist with the documented shape, JSON-schema
validation accepts the supported subset and rejects everything else, the
pipy-owned `tool_request_id` is namespaced away from provider ids, and the
provider-visible `ToolExecutionResult` shape stays separate from the
archive-safe `NativeToolResult` metadata.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

from pipy_harness.native import models as native_models
from pipy_harness.native.tools import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
    validate_arguments,
)
from pipy_harness.native.tools.base import (
    SUPPORTED_TOOL_REQUEST_ID_PREFIX,
)


SIMPLE_OBJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "minLength": 1, "maxLength": 1024},
        "limit": {"type": "integer", "minimum": 1, "maximum": 4096},
        "case_sensitive": {"type": "boolean"},
        "patterns": {"type": "array", "items": {"type": "string"}},
        "mode": {"type": "string", "enum": ["text", "binary"]},
    },
    "required": ["path"],
    "additionalProperties": False,
}


# ----------------------------- ToolDefinition -----------------------------


def test_tool_definition_accepts_minimal_object_schema():
    definition = ToolDefinition(
        name="read",
        description="Read a workspace file.",
        input_schema=SIMPLE_OBJECT_SCHEMA,
    )

    assert definition.name == "read"
    assert definition.description == "Read a workspace file."
    assert definition.input_schema["type"] == "object"


def test_tool_definition_rejects_empty_or_oversized_name():
    with pytest.raises(ValueError, match="non-empty name"):
        ToolDefinition(name="", description="x", input_schema=SIMPLE_OBJECT_SCHEMA)

    with pytest.raises(ValueError, match="exceeds"):
        ToolDefinition(
            name="x" * (ToolDefinition.NAME_MAX_LENGTH + 1),
            description="x",
            input_schema=SIMPLE_OBJECT_SCHEMA,
        )


def test_tool_definition_rejects_non_alphanumeric_name():
    with pytest.raises(ValueError, match="alphanumeric"):
        ToolDefinition(
            name="read-file",
            description="x",
            input_schema=SIMPLE_OBJECT_SCHEMA,
        )


def test_tool_definition_rejects_empty_description():
    with pytest.raises(ValueError, match="non-empty description"):
        ToolDefinition(
            name="read",
            description="",
            input_schema=SIMPLE_OBJECT_SCHEMA,
        )


def test_tool_definition_rejects_non_object_top_level_schema():
    with pytest.raises(ValueError, match="top-level schema"):
        ToolDefinition(
            name="read",
            description="x",
            input_schema={"type": "string"},
        )


def test_tool_definition_rejects_unknown_object_schema_keys():
    with pytest.raises(ValueError, match="Unsupported object schema key"):
        ToolDefinition(
            name="read",
            description="x",
            input_schema={"type": "object", "patternProperties": {}},
        )


def test_tool_definition_rejects_required_key_not_in_properties():
    with pytest.raises(ValueError, match="not declared in properties"):
        ToolDefinition(
            name="read",
            description="x",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["missing"],
            },
        )


def test_tool_definition_rejects_array_schema_without_items():
    with pytest.raises(ValueError, match="must declare 'items'"):
        ToolDefinition(
            name="read",
            description="x",
            input_schema={
                "type": "object",
                "properties": {"patterns": {"type": "array"}},
            },
        )


def test_tool_definition_rejects_unsupported_type():
    with pytest.raises(ValueError, match="Unsupported schema type"):
        ToolDefinition(
            name="read",
            description="x",
            input_schema={
                "type": "object",
                "properties": {"size": {"type": "number"}},
            },
        )


# ------------------------------- ToolRequest -------------------------------


def test_tool_request_round_trip_with_pipy_owned_id():
    request_id = make_tool_request_id()
    request = ToolRequest(
        tool_request_id=request_id,
        tool_name="read",
        arguments={"path": "README.md"},
        provider_correlation_id="call_abc",
    )

    assert request.tool_request_id == request_id
    assert request.tool_request_id.startswith(SUPPORTED_TOOL_REQUEST_ID_PREFIX)
    assert request.tool_name == "read"
    assert request.arguments == {"path": "README.md"}
    assert request.provider_correlation_id == "call_abc"


def test_tool_request_defaults_provider_correlation_id_to_none():
    request = ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="read",
        arguments={},
    )

    assert request.provider_correlation_id is None


def test_tool_request_rejects_non_pipy_owned_id():
    with pytest.raises(ValueError, match="pipy-owned"):
        ToolRequest(
            tool_request_id="call_provider_xyz",
            tool_name="read",
            arguments={},
        )


def test_tool_request_rejects_empty_or_non_string_fields():
    with pytest.raises(ValueError, match="non-empty tool_request_id"):
        ToolRequest(tool_request_id="", tool_name="read", arguments={})
    with pytest.raises(ValueError, match="non-empty tool_name"):
        ToolRequest(
            tool_request_id=make_tool_request_id(), tool_name="", arguments={}
        )
    with pytest.raises(ValueError, match="must be a mapping"):
        ToolRequest(
            tool_request_id=make_tool_request_id(),
            tool_name="read",
            arguments=[],  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="provider_correlation_id"):
        ToolRequest(
            tool_request_id=make_tool_request_id(),
            tool_name="read",
            arguments={},
            provider_correlation_id="",
        )


def test_make_tool_request_id_returns_unique_pipy_owned_ids():
    one = make_tool_request_id()
    two = make_tool_request_id()

    assert one != two
    assert one.startswith(SUPPORTED_TOOL_REQUEST_ID_PREFIX)
    assert two.startswith(SUPPORTED_TOOL_REQUEST_ID_PREFIX)


# -------------------------- ToolExecutionResult ---------------------------


def test_tool_execution_result_defaults_and_carries_provider_id():
    request_id = make_tool_request_id()
    result = ToolExecutionResult(
        tool_request_id=request_id,
        output_text="hello",
        provider_correlation_id="call_abc",
    )

    assert result.tool_request_id == request_id
    assert result.output_text == "hello"
    assert result.is_error is False
    assert result.provider_correlation_id == "call_abc"


def test_tool_execution_result_rejects_non_pipy_owned_id():
    with pytest.raises(ValueError, match="pipy-owned"):
        ToolExecutionResult(tool_request_id="call_xyz", output_text="")


def test_tool_execution_result_rejects_non_string_output():
    with pytest.raises(ValueError, match="output_text must be a string"):
        ToolExecutionResult(
            tool_request_id=make_tool_request_id(),
            output_text=42,  # type: ignore[arg-type]
        )


def test_tool_execution_result_rejects_oversized_output():
    too_long = "x" * (ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH + 1)
    with pytest.raises(ValueError, match="exceeds"):
        ToolExecutionResult(
            tool_request_id=make_tool_request_id(),
            output_text=too_long,
        )


def test_tool_execution_result_rejects_non_bool_is_error():
    with pytest.raises(ValueError, match="is_error"):
        ToolExecutionResult(
            tool_request_id=make_tool_request_id(),
            output_text="oops",
            is_error="yes",  # type: ignore[arg-type]
        )


def test_tool_execution_result_is_not_native_tool_result():
    """`ToolExecutionResult` must stay strictly distinct from
    `NativeToolResult`. Conflating the two would let provider-visible payload
    text leak into archive-safe metadata.
    """

    execution_field_names = {field.name for field in fields(ToolExecutionResult)}
    native_field_names = {
        field.name for field in fields(native_models.NativeToolResult)
    }

    assert ToolExecutionResult is not native_models.NativeToolResult
    assert "output_text" in execution_field_names
    assert "output_text" not in native_field_names
    assert "started_at" not in execution_field_names
    assert "metadata" not in execution_field_names


# ------------------------------ ToolContext -------------------------------


def test_tool_context_requires_absolute_workspace_root(tmp_path: Path):
    context = ToolContext(workspace_root=tmp_path)

    assert context.workspace_root == tmp_path
    assert is_dataclass(context)

    with pytest.raises(ValueError, match="must be a Path"):
        ToolContext(workspace_root="/tmp")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must be absolute"):
        ToolContext(workspace_root=Path("relative/path"))


# --------------------------- ToolArgumentError ----------------------------


def test_tool_argument_error_is_value_error_subclass_with_field_path():
    error = ToolArgumentError(
        "write",
        "expected string",
        field_path=("arguments", "path"),
    )

    assert isinstance(error, ValueError)
    assert error.tool_name == "write"
    assert error.field_path == ("arguments", "path")
    assert "write.arguments.path: expected string" == str(error)


def test_tool_argument_error_rejects_empty_inputs():
    with pytest.raises(ValueError):
        ToolArgumentError("", "message")
    with pytest.raises(ValueError):
        ToolArgumentError("write", "")


# --------------------------- validate_arguments ---------------------------


def test_validate_arguments_accepts_valid_payload():
    result = validate_arguments(
        tool_name="read",
        schema=SIMPLE_OBJECT_SCHEMA,
        arguments={
            "path": "src/pipy_harness/native/tools/base.py",
            "limit": 100,
            "case_sensitive": True,
            "patterns": ["abc", "def"],
            "mode": "text",
        },
    )

    assert result == {
        "path": "src/pipy_harness/native/tools/base.py",
        "limit": 100,
        "case_sensitive": True,
        "patterns": ["abc", "def"],
        "mode": "text",
    }


def test_validate_arguments_returns_defensive_copy():
    payload = {"path": "x.py"}
    result = validate_arguments(
        tool_name="read",
        schema=SIMPLE_OBJECT_SCHEMA,
        arguments=payload,
    )

    assert result == payload
    assert result is not payload
    result["path"] = "mutated"
    assert payload["path"] == "x.py"


def test_validate_arguments_rejects_missing_required_field():
    with pytest.raises(ToolArgumentError) as info:
        validate_arguments(
            tool_name="read",
            schema=SIMPLE_OBJECT_SCHEMA,
            arguments={"limit": 1},
        )

    assert "missing required argument(s): path" in str(info.value)
    assert info.value.tool_name == "read"


def test_validate_arguments_rejects_unsupported_extra_key():
    with pytest.raises(ToolArgumentError, match="unsupported argument"):
        validate_arguments(
            tool_name="read",
            schema=SIMPLE_OBJECT_SCHEMA,
            arguments={"path": "x", "depth": 2},
        )


def test_validate_arguments_rejects_wrong_scalar_types():
    with pytest.raises(ToolArgumentError, match="expected string"):
        validate_arguments(
            tool_name="read",
            schema=SIMPLE_OBJECT_SCHEMA,
            arguments={"path": 123},
        )
    with pytest.raises(ToolArgumentError, match="expected integer"):
        validate_arguments(
            tool_name="read",
            schema=SIMPLE_OBJECT_SCHEMA,
            arguments={"path": "x", "limit": "100"},
        )
    with pytest.raises(ToolArgumentError, match="expected boolean"):
        validate_arguments(
            tool_name="read",
            schema=SIMPLE_OBJECT_SCHEMA,
            arguments={"path": "x", "case_sensitive": "yes"},
        )


def test_validate_arguments_treats_bool_as_non_integer():
    with pytest.raises(ToolArgumentError, match="expected integer"):
        validate_arguments(
            tool_name="read",
            schema=SIMPLE_OBJECT_SCHEMA,
            arguments={"path": "x", "limit": True},
        )


def test_validate_arguments_treats_bool_as_non_string():
    with pytest.raises(ToolArgumentError, match="expected string"):
        validate_arguments(
            tool_name="read",
            schema=SIMPLE_OBJECT_SCHEMA,
            arguments={"path": True},
        )


def test_validate_arguments_enforces_integer_bounds():
    with pytest.raises(ToolArgumentError, match="below minimum"):
        validate_arguments(
            tool_name="read",
            schema=SIMPLE_OBJECT_SCHEMA,
            arguments={"path": "x", "limit": 0},
        )
    with pytest.raises(ToolArgumentError, match="above maximum"):
        validate_arguments(
            tool_name="read",
            schema=SIMPLE_OBJECT_SCHEMA,
            arguments={"path": "x", "limit": 9999},
        )


def test_validate_arguments_enforces_string_length_bounds():
    with pytest.raises(ToolArgumentError, match="shorter than minLength"):
        validate_arguments(
            tool_name="read",
            schema=SIMPLE_OBJECT_SCHEMA,
            arguments={"path": ""},
        )


def test_validate_arguments_enforces_string_enum():
    with pytest.raises(ToolArgumentError, match="not in allowed set"):
        validate_arguments(
            tool_name="read",
            schema=SIMPLE_OBJECT_SCHEMA,
            arguments={"path": "x", "mode": "raw"},
        )


def test_validate_arguments_validates_array_items():
    with pytest.raises(ToolArgumentError) as info:
        validate_arguments(
            tool_name="read",
            schema=SIMPLE_OBJECT_SCHEMA,
            arguments={"path": "x", "patterns": ["ok", 1]},
        )

    assert info.value.field_path == ("patterns", "[1]")
    assert "expected string" in str(info.value)


def test_validate_arguments_rejects_non_object_top_level_schema():
    with pytest.raises(ValueError, match="only supports object"):
        validate_arguments(
            tool_name="read",
            schema={"type": "string"},
            arguments={},
        )


def test_validate_arguments_rejects_non_mapping_arguments():
    with pytest.raises(ToolArgumentError, match="expected object"):
        validate_arguments(
            tool_name="read",
            schema=SIMPLE_OBJECT_SCHEMA,
            arguments=["not", "an", "object"],
        )


# -------------------------------- ToolPort --------------------------------


class _FixtureEchoTool:
    """Test-only tool used to verify the `ToolPort` Protocol contract."""

    def __init__(self) -> None:
        self._definition = ToolDefinition(
            name="echo",
            description="Return the provided text verbatim.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def invoke(
        self, request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        text = str(request.arguments["text"])
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=text,
            provider_correlation_id=request.provider_correlation_id,
        )


def test_tool_port_protocol_is_runtime_checkable_with_fixture(tmp_path: Path):
    tool = _FixtureEchoTool()

    assert isinstance(tool, ToolPort)

    validated = validate_arguments(
        tool_name=tool.definition.name,
        schema=tool.definition.input_schema,
        arguments={"text": "hi"},
    )
    request = ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name=tool.definition.name,
        arguments=validated,
        provider_correlation_id="call_abc",
    )
    context = ToolContext(workspace_root=tmp_path)

    result = tool.invoke(request, context)

    assert result.tool_request_id == request.tool_request_id
    assert result.output_text == "hi"
    assert result.provider_correlation_id == "call_abc"
    assert result.is_error is False


# --------------------------- Module-level guards --------------------------


def test_tools_subpackage_does_not_export_archive_safe_native_tool_result():
    import pipy_harness.native.tools as tools_pkg

    assert "NativeToolResult" not in tools_pkg.__all__
    assert "NativeToolRequest" not in tools_pkg.__all__


def test_module_does_not_introduce_runtime_dependencies():
    """The Tool-Loop Parity Track must use only the standard library.

    The slice 2 module imports only stdlib names, so a quick sanity check on
    its source file pins the rule before later slices grow the surface.
    """

    base_source = (
        Path(__file__).parents[1]
        / "src/pipy_harness/native/tools/base.py"
    ).read_text(encoding="utf-8")

    forbidden_imports = (
        "import pydantic",
        "from pydantic",
        "import jsonschema",
        "from jsonschema",
        "import attrs",
        "from attrs",
    )
    for forbidden in forbidden_imports:
        assert forbidden not in base_source, (
            f"unexpected runtime dependency: {forbidden}"
        )

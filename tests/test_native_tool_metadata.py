"""Tool metadata, foreign-schema definitions and structured results (MCP1 S01).

The fields Pi's MCP support rests on exist on `ToolDefinition` with defaults
that leave existing tools unchanged. A foreign definition carries an arbitrary
JSON Schema to every provider family unchanged and has its arguments checked
by the standard-library validator. `structured_content` travels from the tool
to the result message and goes neither to a provider nor to the session file.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from pipy_harness.native._provider_helpers import (
    envelope_to_chat_message,
    serialize_tool_for_anthropic,
    serialize_tool_for_chat_completions,
    serialize_tool_for_responses,
)
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.agent.system_messages import tool_declaration
from pipy_harness.native.agent.tools import ToolExecutor
from pipy_harness.native.agent_loop_policy import materialize_provider_request
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.providers.google_generate_content_wire import (
    serialize_tool_for_gemini,
)
from pipy_harness.native.providers.transcript import declaration_definition
from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree
from pipy_harness.native.tools import (
    ToolAnnotations,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolNamespace,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.base import TOOL_EXPOSURES
from pipy_harness.native.tools.registry import ProductionToolRegistry

# What an MCP server may send: `anyOf`, a local `$ref`, `number` with a
# fractional bound, nested arrays and keywords pipy does not interpret.
FOREIGN_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "query": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "ratio": {"type": "number", "minimum": 0.5, "default": 1.5},
        "point": {"$ref": "#/$defs/point"},
        "grid": {
            "type": "array",
            "items": {"type": "array", "items": {"type": ["integer", "null"]}},
        },
        "mode": {"enum": ["fast", 2, None], "x-vendor": {"hint": [1, 2.5]}},
    },
    "required": ["query"],
    "additionalProperties": {"type": "string", "format": "uri"},
    "$defs": {
        "point": {
            "type": "object",
            "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
            "required": ["x", "y"],
        }
    },
}


def _foreign(**overrides: Any) -> ToolDefinition:
    values: dict[str, Any] = {
        "name": "mcp__docs__search",
        "description": "Search the docs.",
        "input_schema": FOREIGN_SCHEMA,
        "schema_mode": "foreign",
    }
    values.update(overrides)
    return ToolDefinition(**values)


# --- Metadata fields -------------------------------------------------------


def test_metadata_defaults_leave_a_definition_as_it_was() -> None:
    definition = ToolDefinition("echo", "Echo.", {"type": "object", "properties": {}})

    assert definition.exposure == "direct"
    assert definition.namespace is None
    assert definition.annotations is None
    assert definition.output_schema is None
    assert definition.reaches_tools is None
    assert definition.schema_mode == "strict"
    assert definition == ToolDefinition(
        name="echo",
        description="Echo.",
        input_schema={"type": "object", "properties": {}},
    )


def test_built_in_tools_keep_the_default_metadata() -> None:
    for port in ProductionToolRegistry().values():
        definition = port.definition
        assert (definition.exposure, definition.schema_mode) == ("direct", "strict")
        assert definition.namespace is None and definition.annotations is None
        assert definition.output_schema is None and definition.reaches_tools is None


def test_the_exposures_are_pis_five() -> None:
    assert TOOL_EXPOSURES == ("direct", "model-only", "codemode", "deferred", "hidden")
    for exposure in TOOL_EXPOSURES:
        assert _foreign(exposure=exposure).exposure == exposure
    with pytest.raises(ValueError, match="exposure"):
        _foreign(exposure="codemode-deferred")


def test_a_definition_carries_namespace_annotations_output_schema_and_reach() -> None:
    namespace = ToolNamespace(
        name="mcp__docs",
        aliases=("docs",),
        description="Product documentation",
        instructions="Search before you read.",
    )
    annotations = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    output_schema = {"type": "object", "properties": {"hits": {"type": "array"}}}

    def reach(arguments: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(str(arguments.get("code", "")).split())

    definition = _foreign(
        namespace=namespace,
        annotations=annotations,
        output_schema=output_schema,
        reaches_tools=reach,
    )

    assert definition.namespace == namespace
    assert definition.annotations == annotations
    assert annotations.destructive_hint is None and annotations.idempotent_hint is None
    assert definition.output_schema == output_schema
    assert definition.reaches_tools is not None
    assert definition.reaches_tools({"code": "a b"}) == ("a", "b")


def test_metadata_fields_are_keyword_only() -> None:
    with pytest.raises(TypeError):
        cast(Any, ToolDefinition)("echo", "Echo.", {"type": "object"}, "hidden")


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"namespace": "mcp__docs"}, "namespace"),
        ({"annotations": {"readOnlyHint": True}}, "annotations"),
        ({"output_schema": ["not", "an", "object"]}, "output_schema"),
        ({"output_schema": {"type": object()}}, "output_schema must be JSON"),
        ({"reaches_tools": "all"}, "reaches_tools"),
        ({"schema_mode": "lenient"}, "schema_mode"),
    ],
)
def test_malformed_metadata_is_rejected(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _foreign(**overrides)


def test_namespace_and_annotation_values_are_checked() -> None:
    with pytest.raises(ValueError, match="name"):
        ToolNamespace(name="")
    with pytest.raises(ValueError, match="aliases"):
        ToolNamespace(name="mcp__docs", aliases=cast(Any, ["docs"]))
    with pytest.raises(ValueError, match="instructions"):
        ToolNamespace(name="mcp__docs", instructions=cast(Any, 3))
    with pytest.raises(ValueError, match="read_only_hint"):
        ToolAnnotations(read_only_hint=cast(Any, "yes"))


# --- Foreign-schema definitions --------------------------------------------


def test_a_foreign_definition_accepts_an_arbitrary_schema_and_long_description() -> (
    None
):
    description = "d" * (10 * 1024)

    definition = _foreign(description=description)

    assert definition.input_schema == FOREIGN_SCHEMA
    assert len(definition.description) == 10 * 1024
    # The same definition is not a strict one.
    with pytest.raises(ValueError, match="description exceeds"):
        _foreign(description=description, schema_mode="strict")
    with pytest.raises(ValueError, match="Unsupported"):
        _foreign(schema_mode="strict")


@pytest.mark.parametrize(
    ("schema", "match"),
    [
        ({"properties": {}}, "top-level schema must be an object"),
        ({"type": "array", "items": {}}, "top-level schema must be an object"),
        ({"type": "object", "default": float("nan")}, "input_schema must be JSON"),
        ({"type": "object", "x": {1: "a"}}, "input_schema must be JSON"),
        ({"type": "object", "x": object()}, "input_schema must be JSON"),
    ],
)
def test_a_foreign_definition_still_needs_a_json_object_schema(
    schema: dict[str, Any], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        _foreign(input_schema=schema)


def test_a_foreign_definition_keeps_the_name_rules() -> None:
    with pytest.raises(ValueError, match="alphanumeric"):
        _foreign(name="mcp__docs__search-all")
    with pytest.raises(ValueError, match="exceeds 64"):
        _foreign(name="m" * 65)
    with pytest.raises(ValueError, match="non-empty description"):
        _foreign(description="")


# --- A foreign schema reaches every provider family unchanged ---------------


def _chat_schema(payload: Mapping[str, Any]) -> object:
    return payload["function"]["parameters"]


def _flat_parameters(payload: Mapping[str, Any]) -> object:
    return payload["parameters"]


def _anthropic_schema(payload: Mapping[str, Any]) -> object:
    return payload["input_schema"]


def _provider_bound(definition: ToolDefinition, tmp_path: Path) -> ToolDefinition:
    """The definition as a provider adapter receives it: frozen in a request
    snapshot and projected into the provider-bound request."""

    request = ProviderRequest(
        system_prompt="system",
        user_prompt="prompt",
        provider_name="fake",
        model_id="fake-model",
        cwd=tmp_path,
        messages=(AgentUserMessage(ProductContent("prompt")),),
        available_tools=(definition,),
    )
    snapshot = snapshot_provider_request(request)
    assert snapshot.advertised_tool_names == (definition.name,)
    (bound,) = materialize_provider_request(snapshot).available_tools
    return bound


@pytest.mark.parametrize(
    ("serializer", "schema_from_payload"),
    [
        (serialize_tool_for_chat_completions, _chat_schema),
        (serialize_tool_for_anthropic, _anthropic_schema),
        (serialize_tool_for_responses, _flat_parameters),
        (serialize_tool_for_gemini, _flat_parameters),
    ],
)
def test_each_provider_family_sends_a_foreign_schema_unchanged(
    serializer: Callable[[object], dict[str, Any]],
    schema_from_payload: Callable[[Mapping[str, Any]], object],
    tmp_path: Path,
) -> None:
    description = "d" * (10 * 1024)
    bound = _provider_bound(_foreign(description=description), tmp_path)

    payload = serializer(bound)

    assert bound.schema_mode == "foreign"
    assert json.dumps(schema_from_payload(payload)) == json.dumps(FOREIGN_SCHEMA)
    assert description in json.dumps(payload)


def test_the_request_snapshot_carries_only_the_model_visible_part(
    tmp_path: Path,
) -> None:
    definition = _foreign(
        exposure="direct",
        namespace=ToolNamespace(name="mcp__docs"),
        annotations=ToolAnnotations(read_only_hint=True),
        output_schema={"type": "object"},
        reaches_tools=lambda _arguments: "all",
    )

    bound = _provider_bound(definition, tmp_path)

    assert (bound.name, bound.description) == (definition.name, definition.description)
    assert bound.namespace is None and bound.annotations is None
    assert bound.output_schema is None and bound.reaches_tools is None


def test_a_strict_schema_is_still_frozen_strictly(tmp_path: Path) -> None:
    strict = ToolDefinition("echo", "Echo.", {"type": "object", "properties": {}})
    object.__setattr__(
        strict,
        "input_schema",
        {"type": "object", "properties": {}, "default": 1.5},
    )

    with pytest.raises(TypeError, match="immutable JSON values"):
        _provider_bound(strict, tmp_path)


def test_a_foreign_declaration_replays_from_the_transcript_unchanged() -> None:
    declaration = tool_declaration(_foreign())

    replayed = declaration_definition(declaration)

    assert json.loads(declaration.parameters_json) == FOREIGN_SCHEMA
    assert replayed.input_schema == FOREIGN_SCHEMA
    assert serialize_tool_for_anthropic(replayed)["input_schema"] == FOREIGN_SCHEMA


# --- Execution: validation by mode, structured content ----------------------


class _Tool:
    def __init__(self, definition: ToolDefinition, structured: Any = None) -> None:
        self._definition = definition
        self._structured = structured
        self.requests: list[ToolRequest] = []

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        self.requests.append(request)
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text="2 hits",
            structured_content=self._structured,
        )


def _call(name: str, arguments: Mapping[str, Any]) -> AgentToolCall:
    return AgentToolCall("call-1", name, ProductContent(json.dumps(arguments)))


def test_the_executor_validates_a_foreign_tool_with_the_foreign_validator(
    tmp_path: Path,
) -> None:
    tool = _Tool(_foreign())
    executor = ToolExecutor({"mcp__docs__search": tool})
    context = ToolContext(workspace_root=tmp_path)
    arguments = {"query": None, "ratio": 0.75, "point": {"x": 1, "y": 2.5}, "u": "v"}

    accepted = executor.execute(_call("mcp__docs__search", arguments), context)
    rejected = executor.execute(
        _call("mcp__docs__search", {"query": "q", "point": {"x": 1}}), context
    )

    assert not accepted.result.is_error and not accepted.malformed_arguments
    # Arguments reach the tool as sent, undeclared properties included.
    assert [dict(request.arguments) for request in tool.requests] == [arguments]
    assert rejected.result.is_error and rejected.malformed_arguments
    assert rejected.result.content.value == (
        "mcp__docs__search.point: missing required argument(s): y"
    )


def test_structured_content_travels_from_the_tool_to_the_result_message(
    tmp_path: Path,
) -> None:
    structured = {"hits": [{"title": "Install", "score": 0.5}], "total": 2}
    tool = _Tool(_foreign(output_schema={"type": "object"}), structured)
    executor = ToolExecutor({"mcp__docs__search": tool})

    outcome = executor.execute(
        _call("mcp__docs__search", {"query": "install"}),
        ToolContext(workspace_root=tmp_path),
    )

    assert outcome.result.structured_content == structured
    assert outcome.result.content.value == "2 hits"


def test_a_result_without_structured_content_has_none(tmp_path: Path) -> None:
    executor = ToolExecutor({"mcp__docs__search": _Tool(_foreign())})

    outcome = executor.execute(
        _call("mcp__docs__search", {"query": "q"}), ToolContext(workspace_root=tmp_path)
    )

    assert outcome.result.structured_content is None


@pytest.mark.parametrize("value", [{"a": [1, 2.5, None, True]}, [1, "two"], "text", 0])
def test_structured_content_is_any_json_value(value: Any) -> None:
    result = ToolExecutionResult(
        tool_request_id=make_tool_request_id(),
        output_text="ok",
        structured_content=value,
    )

    assert result.structured_content == value


@pytest.mark.parametrize(
    "value", [{"a": object()}, {"a": float("nan")}, {1: "a"}, {"a": [{2: "b"}]}]
)
def test_structured_content_that_is_not_json_is_rejected(value: Any) -> None:
    with pytest.raises(ValueError, match="structured_content"):
        ToolExecutionResult(
            tool_request_id=make_tool_request_id(),
            output_text="ok",
            structured_content=value,
        )


def _result_message(structured: Any) -> AgentToolResultMessage:
    return AgentToolResultMessage(
        tool_request_id="pipy-tool-1",
        tool_name="mcp__docs__search",
        content=ProductContent("2 hits"),
        provider_correlation_id="call-1",
        structured_content=structured,
    )


def test_structured_content_takes_no_part_in_history_equality() -> None:
    assert _result_message({"total": 2}) == _result_message(None)


def test_structured_content_is_not_sent_to_a_provider() -> None:
    message = envelope_to_chat_message(_result_message({"secret_marker": 2}))

    assert message == {"role": "tool", "tool_call_id": "call-1", "content": "2 hits"}


def test_structured_content_is_not_written_to_the_session_file(tmp_path: Path) -> None:
    # Pi `createToolResultMessage` (agent-loop.ts) stores content, details,
    # usage and isError; `structuredContent` stays on the in-memory result.
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "sessions")
    call = AgentToolCall("call-1", "mcp__docs__search", ProductContent("{}"))
    tree.append_message(AgentAssistantMessage(ProductContent(""), tool_calls=(call,)))
    tree.append_message(_result_message({"secret_marker": 2}))
    assert tree.path is not None

    rows = [json.loads(line) for line in tree.path.read_text().splitlines()]
    stored = [row["message"] for row in rows if row.get("type") == "message"]
    reopened = NativeSessionTree.open(tree.path, strict=True)
    results = [
        entry.message
        for entry in reopened.get_entries()
        if isinstance(entry, MessageEntry)
        and isinstance(entry.message, AgentToolResultMessage)
    ]

    assert stored[1] == {
        "role": "tool",
        "tool_request_id": "pipy-tool-1",
        "output_text": "2 hits",
        "is_error": False,
        "provider_correlation_id": "call-1",
    }
    assert [result.structured_content for result in results] == [None]

"""Provider-family tool schema materialization contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

import pytest

from pipy_harness.native._provider_helpers import (
    serialize_tool_for_anthropic,
    serialize_tool_for_chat_completions,
    serialize_tool_for_responses,
)
from pipy_harness.native.google_provider import _serialize_tool_for_gemini
from pipy_harness.native.google_vertex_provider import (
    _serialize_tool_for_gemini as _serialize_tool_for_vertex_gemini,
)
from pipy_harness.native.tools.base import ToolDefinition


def _immutable_schema_tool() -> ToolDefinition:
    definition = ToolDefinition(
        "search",
        "Search fixture.",
        {"type": "object", "properties": {}},
    )
    object.__setattr__(
        definition,
        "input_schema",
        MappingProxyType(
            {
                "type": "object",
                "properties": MappingProxyType(
                    {
                        "query": MappingProxyType(
                            {"type": "string", "enum": ("one", "two")}
                        )
                    }
                ),
                "required": ("query",),
            }
        ),
    )
    return definition


def _chat_schema(payload: Mapping[str, object]) -> object:
    function = payload["function"]
    assert isinstance(function, Mapping)
    return function["parameters"]


def _flat_parameters(payload: Mapping[str, object]) -> object:
    return payload["parameters"]


def _anthropic_schema(payload: Mapping[str, object]) -> object:
    return payload["input_schema"]


@pytest.mark.parametrize(
    ("serializer", "schema_from_payload"),
    [
        (serialize_tool_for_chat_completions, _chat_schema),
        (serialize_tool_for_anthropic, _anthropic_schema),
        (serialize_tool_for_responses, _flat_parameters),
        (_serialize_tool_for_gemini, _flat_parameters),
        (_serialize_tool_for_vertex_gemini, _flat_parameters),
    ],
)
def test_provider_serializers_materialize_nested_immutable_schema(
    serializer: Callable[[object], dict[str, object]],
    schema_from_payload: Callable[[Mapping[str, object]], object],
) -> None:
    payload = serializer(_immutable_schema_tool())

    assert schema_from_payload(payload) == {
        "type": "object",
        "properties": {
            "query": {"type": "string", "enum": ["one", "two"]}
        },
        "required": ["query"],
    }

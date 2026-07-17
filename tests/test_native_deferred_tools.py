"""Provider-neutral dynamic-tool split and Pi hash parity tests."""

from pathlib import Path

from pipy_harness.native import ProviderRequest, ProviderToolCall
from pipy_harness.native.deferred_tools import (
    responses_tool_search_items,
    short_hash,
    split_deferred_tools,
)
from pipy_harness.native.tools import AssistantMessage, ToolResultMessage, UserMessage
from pipy_harness.native.tools.base import ToolDefinition


def _tool(name: str, description: str | None = None) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description or f"{name} description",
        input_schema={"type": "object", "properties": {}},
    )


def _request(
    messages: tuple[object, ...], tools: tuple[ToolDefinition, ...]
) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="sys",
        user_prompt="load",
        provider_name="test",
        model_id="test",
        cwd=Path("."),
        messages=messages,  # type: ignore[arg-type]
        available_tools=tools,
    )


def test_short_hash_matches_pi_utf16_vectors() -> None:
    assert short_hash("call_abc:late_tool") == "1o0l89w1i7wxtx"
    assert short_hash("call_abc|fc_abc:late_tool") == "xvuydyik9a48"
    assert short_hash("call_loader:late_tool,later_tool") == "dulyo1k6qd28"
    assert short_hash("call_😀:late_tool") == "1ee5wtp1l226u7"


def test_split_deferred_tools_pins_history_matrix() -> None:
    marker = ToolResultMessage(
        tool_request_id="pipy-tool-load",
        output_text="loaded",
        provider_correlation_id="call_loader",
        added_tool_names=("late_tool", "missing_tool"),
    )
    tools = (
        _tool("base_tool"),
        _tool("late_tool", "old definition"),
        _tool("late_tool", "current definition"),
    )

    immediate, deferred = split_deferred_tools(
        _request((UserMessage("load"), marker), tools), enabled=True
    )
    assert [tool.name for tool in immediate] == ["base_tool"]
    assert [(tool.name, tool.description) for tool in deferred] == [
        ("late_tool", "current definition")
    ]

    immediate, deferred = split_deferred_tools(
        _request((marker,), tools), enabled=False
    )
    assert [tool.name for tool in immediate] == ["base_tool", "late_tool"]
    assert deferred == ()


def test_split_keeps_prior_used_tool_immediate_and_allows_all_deferred() -> None:
    messages = (
        AssistantMessage(
            tool_calls=(ProviderToolCall("call_late", "late_tool", "{}"),)
        ),
        ToolResultMessage(
            tool_request_id="pipy-tool-used",
            output_text="used",
            provider_correlation_id="call_late",
            added_tool_names=("late_tool", "later_tool"),
        ),
    )
    immediate, deferred = split_deferred_tools(
        _request(messages, (_tool("late_tool"), _tool("later_tool"))),
        enabled=True,
    )
    assert [tool.name for tool in immediate] == ["late_tool"]
    assert [tool.name for tool in deferred] == ["later_tool"]

    immediate, deferred = split_deferred_tools(
        _request((messages[-1],), (_tool("late_tool"), _tool("later_tool"))),
        enabled=True,
    )
    assert immediate == ()
    assert [tool.name for tool in deferred] == ["late_tool", "later_tool"]


def test_tool_search_missing_correlation_does_not_consume_deferred_name() -> None:
    message = ToolResultMessage(
        tool_request_id="pipy-tool-load",
        output_text="loaded",
        added_tool_names=("late_tool",),
    )
    loaded: set[str] = set()
    request = _request((message,), (_tool("late_tool"),))

    immediate, deferred = split_deferred_tools(
        request,
        enabled=True,
        require_result_correlation=True,
    )
    assert [tool.name for tool in immediate] == ["late_tool"]
    assert deferred == ()

    assert responses_tool_search_items(
        message,
        deferred_tools={"late_tool": _tool("late_tool")},
        loaded_tool_names=loaded,
    ) == []
    assert loaded == set()

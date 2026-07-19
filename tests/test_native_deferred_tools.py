"""Provider-neutral dynamic-tool split and Pi hash parity tests."""

from pathlib import Path

from pipy_harness.native import ProviderRequest
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.deferred_tools import (
    short_hash,
    split_deferred_tools,
)
from pipy_harness.native.tools.base import ToolDefinition


def _tool(name: str, description: str | None = None) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description or f"{name} description",
        input_schema={"type": "object", "properties": {}},
    )


def _request(
    messages: tuple[AgentMessage, ...], tools: tuple[ToolDefinition, ...]
) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="sys",
        user_prompt="load",
        provider_name="test",
        model_id="test",
        cwd=Path("."),
        messages=messages,
        available_tools=tools,
    )


def test_short_hash_matches_pi_utf16_vectors() -> None:
    assert short_hash("call_abc:late_tool") == "1o0l89w1i7wxtx"
    assert short_hash("call_abc|fc_abc:late_tool") == "xvuydyik9a48"
    assert short_hash("call_loader:late_tool,later_tool") == "dulyo1k6qd28"
    assert short_hash("call_😀:late_tool") == "1ee5wtp1l226u7"


def test_split_deferred_tools_pins_history_matrix() -> None:
    marker = AgentToolResultMessage(
        tool_request_id="pipy-tool-load",
        tool_name="loader",
        content=ProductContent("loaded"),
        provider_correlation_id="call_loader",
        added_tool_names=("late_tool", "missing_tool"),
    )
    tools = (
        _tool("base_tool"),
        _tool("late_tool", "old definition"),
        _tool("late_tool", "current definition"),
    )

    immediate, deferred = split_deferred_tools(
        _request((AgentUserMessage(ProductContent("load")), marker), tools), enabled=True
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
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(
                AgentToolCall("call_late", "late_tool", ProductContent("{}")),
            ),
        ),
        AgentToolResultMessage(
            tool_request_id="pipy-tool-used",
            tool_name="late_tool",
            content=ProductContent("used"),
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

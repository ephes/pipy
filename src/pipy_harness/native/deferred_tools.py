"""Shared dynamic-tool placement helpers for native providers."""

from __future__ import annotations

from collections.abc import Mapping

from pipy_harness.native.agent import AgentAssistantMessage, AgentToolResultMessage
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.tools.base import ToolDefinition


def split_deferred_tools(
    request: ProviderRequest,
    *,
    enabled: bool,
) -> tuple[tuple[ToolDefinition, ...], tuple[ToolDefinition, ...]]:
    """Split current definitions using durable message load-point markers."""

    unique_tools: dict[str, ToolDefinition] = {}
    for tool in request.available_tools:
        unique_tools[tool.name] = tool
    if not enabled:
        return tuple(unique_tools.values()), ()

    deferred_names: set[str] = set()
    used_names: set[str] = set()
    for message in request.messages:
        if isinstance(message, AgentAssistantMessage):
            used_names.update(call.tool_name for call in message.tool_calls)
        elif isinstance(message, AgentToolResultMessage):
            for name in message.added_tool_names:
                if name not in used_names:
                    deferred_names.add(name)

    immediate: list[ToolDefinition] = []
    deferred: list[ToolDefinition] = []
    for name, tool in unique_tools.items():
        (deferred if name in deferred_names else immediate).append(tool)
    return tuple(immediate), tuple(deferred)


def short_hash(value: str) -> str:
    """Port Pi's deterministic two-accumulator JavaScript ``shortHash``."""

    mask = 0xFFFFFFFF
    h1 = 0xDEADBEEF
    h2 = 0x41C6CE57
    encoded = value.encode("utf-16-le", errors="surrogatepass")
    for index in range(0, len(encoded), 2):
        code_unit = encoded[index] | (encoded[index + 1] << 8)
        h1 = ((h1 ^ code_unit) * 2654435761) & mask
        h2 = ((h2 ^ code_unit) * 1597334677) & mask
    h1 = (((h1 ^ (h1 >> 16)) * 2246822507) ^ ((h2 ^ (h2 >> 13)) * 3266489909)) & mask
    h2 = (((h2 ^ (h2 >> 16)) * 2246822507) ^ ((h1 ^ (h1 >> 13)) * 3266489909)) & mask
    return _base36(h2) + _base36(h1)


def responses_tool_search_items(
    message: AgentToolResultMessage,
    *,
    deferred_tools: Mapping[str, ToolDefinition],
    loaded_tool_names: set[str],
) -> list[dict[str, object]]:
    """Build Pi's completed client tool-search pair for one result marker."""

    correlation_id = message.provider_correlation_id
    selected: list[ToolDefinition] = []
    for name in message.added_tool_names:
        tool = deferred_tools.get(name)
        if tool is None or name in loaded_tool_names:
            continue
        loaded_tool_names.add(name)
        selected.append(tool)
    if not selected:
        return []
    names = [tool.name for tool in selected]
    seed = f"{correlation_id}:{','.join(names)}"
    call_id = f"pi_tool_load_{short_hash(seed)}"
    return [
        {
            "type": "tool_search_call",
            "call_id": call_id,
            "execution": "client",
            "status": "completed",
            "arguments": {"query": " ".join(names), "limit": len(names)},
        },
        {
            "type": "tool_search_output",
            "call_id": call_id,
            "execution": "client",
            "status": "completed",
            "tools": [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.input_schema),
                    "strict": False,
                    "defer_loading": True,
                }
                for tool in selected
            ],
        },
    ]


def _base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    digits: list[str] = []
    while value:
        value, remainder = divmod(value, 36)
        digits.append(alphabet[remainder])
    return "".join(reversed(digits))

"""Canonical snapshots pairing one provider request with its authorization set."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from pipy_harness.native.agent.messages import AgentMessage
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.tools.base import ToolDefinition


@dataclass(frozen=True, slots=True)
class AgentProviderRequestSnapshot:
    """One exact provider request and the tool names it advertised."""

    request: ProviderRequest
    advertised_tool_names: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.request, ProviderRequest):
            raise TypeError("request must be ProviderRequest")
        if not isinstance(self.advertised_tool_names, tuple) or not all(
            isinstance(name, str) and name for name in self.advertised_tool_names
        ):
            raise TypeError("advertised_tool_names must be a tuple of names")
        if len(set(self.advertised_tool_names)) != len(self.advertised_tool_names):
            raise ValueError("advertised_tool_names must not contain duplicates")
        request_names = tuple(tool.name for tool in self.request.available_tools)
        if request_names != self.advertised_tool_names:
            raise ValueError("advertised tool names must match the provider request")

    def authorizes(self, tool_name: str) -> bool:
        """Return whether this exact request advertised ``tool_name``."""

        return tool_name in self.advertised_tool_names


def snapshot_provider_request(
    request: ProviderRequest,
    *,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    messages: tuple[AgentMessage, ...] | None = None,
    available_tool_names: Iterable[str] | None = None,
) -> AgentProviderRequestSnapshot:
    """Apply one monotonic request transform and freeze its authorization set."""

    if not isinstance(request, ProviderRequest):
        raise TypeError("request must be ProviderRequest")
    final_system_prompt = (
        system_prompt if system_prompt is not None else request.system_prompt
    )
    final_user_prompt = user_prompt if user_prompt is not None else request.user_prompt
    if final_user_prompt != request.user_prompt and messages is None:
        raise ValueError("messages are required when user_prompt changes")
    final_tools = _narrow_tool_definitions(
        request.available_tools,
        available_tool_names,
    )
    final_request = replace(
        request,
        system_prompt=final_system_prompt,
        user_prompt=final_user_prompt,
        messages=request.messages if messages is None else messages,
        available_tools=final_tools,
    )
    return AgentProviderRequestSnapshot(
        final_request,
        tuple(tool.name for tool in final_tools),
    )


def _narrow_tool_definitions(
    current: tuple[ToolDefinition, ...],
    requested_names: Iterable[str] | None,
) -> tuple[ToolDefinition, ...]:
    if requested_names is None:
        requested = {definition.name for definition in current}
    else:
        requested = {name for name in requested_names if isinstance(name, str) and name}
    seen: set[str] = set()
    narrowed: list[ToolDefinition] = []
    for definition in current:
        if definition.name not in requested or definition.name in seen:
            continue
        seen.add(definition.name)
        narrowed.append(definition)
    return tuple(narrowed)

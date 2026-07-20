"""Product callback adapters for canonical agent-loop policy ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.loop_policy import (
    AgentProviderRequestPolicyInput,
    AgentToolPolicyDecision,
    validate_agent_tool_policy_decision,
)
from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.request import AgentProviderRequestSnapshot
from pipy_harness.native.agent.request import (
    validate_product_content,
    validate_provider_request_snapshot,
)
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.tools.base import (
    ToolDefinition,
    materialize_tool_input_schema,
)


def materialize_provider_request(
    snapshot: AgentProviderRequestSnapshot,
) -> ProviderRequest:
    """Project an immutable snapshot into one detached provider-bound request."""

    validate_provider_request_snapshot(snapshot)
    tools = tuple(
        ToolDefinition(
            name=definition.name,
            description=definition.description,
            input_schema=materialize_tool_input_schema(definition.input_schema),
        )
        for definition in snapshot.request.available_tools
    )
    request = replace(snapshot.request, available_tools=tools)
    if type(request) is not ProviderRequest:
        raise TypeError("provider projection must produce an exact ProviderRequest")
    return request


class NativeAgentProviderRequestPolicy:
    """Prepare canonical request snapshots through one product callback."""

    __slots__ = ("_prepare",)

    def __init__(
        self,
        prepare: Callable[
            [AgentProviderRequestPolicyInput], AgentProviderRequestSnapshot
        ],
    ) -> None:
        if not callable(prepare):
            raise TypeError("prepare must be callable")
        self._prepare = prepare

    def prepare(
        self,
        policy_input: AgentProviderRequestPolicyInput,
        /,
    ) -> AgentProviderRequestSnapshot:
        if type(policy_input) is not AgentProviderRequestPolicyInput:
            raise TypeError("policy_input must be AgentProviderRequestPolicyInput")
        snapshot = self._prepare(policy_input)
        if type(snapshot) is not AgentProviderRequestSnapshot:
            raise TypeError("prepare callback must return AgentProviderRequestSnapshot")
        validate_provider_request_snapshot(snapshot)
        return snapshot


class NativeAgentToolPolicy:
    """Apply product tool admission and result transforms synchronously."""

    __slots__ = ("_before_execute", "_transform_result")

    def __init__(
        self,
        before_execute: Callable[[AgentToolCall], AgentToolPolicyDecision],
        transform_result: Callable[
            [AgentToolCall, AgentToolResultMessage], ProductContent
        ],
    ) -> None:
        if not callable(before_execute):
            raise TypeError("before_execute must be callable")
        if not callable(transform_result):
            raise TypeError("transform_result must be callable")
        self._before_execute = before_execute
        self._transform_result = transform_result

    def before_execute(self, call: AgentToolCall, /) -> AgentToolPolicyDecision:
        if type(call) is not AgentToolCall:
            raise TypeError("call must be AgentToolCall")
        decision = self._before_execute(call)
        if type(decision) is not AgentToolPolicyDecision:
            raise TypeError(
                "before_execute callback must return AgentToolPolicyDecision"
            )
        validate_agent_tool_policy_decision(decision)
        return decision

    def transform_result(
        self,
        call: AgentToolCall,
        result: AgentToolResultMessage,
        /,
    ) -> ProductContent:
        if type(call) is not AgentToolCall:
            raise TypeError("call must be AgentToolCall")
        if type(result) is not AgentToolResultMessage:
            raise TypeError("result must be AgentToolResultMessage")
        content = self._transform_result(call, result)
        if type(content) is not ProductContent:
            raise TypeError("transform_result callback must return ProductContent")
        validate_product_content(content, "transform_result callback result")
        return content

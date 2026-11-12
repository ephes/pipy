"""Pure turn semantics shared by the agent loop and, later, the durable tasks.

The kernel decides what one provider result becomes and what a tool policy
makes of one result, from canonical values only: no I/O, no events, no Session
access. ``AgentLoop`` calls it and emits its events around the calls; the
durable generation still has its own copy of the provider settlement until
DE22/DE25 adopt the kernel (see ``docs/durable-loop-integration-design.md``,
section 4.2, and ``tests/durable/test_generation_kernel_equivalence.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from pipy_harness.native.agent.assistant_blocks import (
    assistant_message,
    failed_provider_blocks,
    stopped_assistant,
)
from pipy_harness.native.agent.content import (
    NO_STRUCTURED_CONTENT,
    TextContent,
    ThinkingContent,
    ToolResultBlock,
)
from pipy_harness.native.agent.loop_policy import (
    AgentProviderStatusAction,
    AgentProviderStatusDecision,
    AgentToolInvocationMode,
    AgentToolPolicy,
    AgentToolPolicyAction,
    AgentToolPolicyState,
    AgentToolPolicyTransition,
    AgentToolResultTransform,
    decide_tool_admission,
    normalize_provider_status,
)
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentStopReason,
    AgentToolCall,
    AgentToolResultMessage,
)
from pipy_harness.native.agent.nested_record import retarget_nested_evidence
from pipy_harness.native.agent.provider_result_validation import (
    validate_provider_result,
)
from pipy_harness.native.agent.request import (
    AgentProviderRequestSnapshot,
    validate_product_content,
)
from pipy_harness.native.agent.tool_details import (
    UNCHANGED_TOOL_DETAILS,
    copy_tool_details,
)
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentTokenPricing,
    message_usage,
)
from pipy_harness.native.models import ProviderResult
from pipy_harness.status import HarnessStatus


@dataclass(frozen=True, slots=True)
class ProviderSettlement:
    """What one provider result becomes.

    ``sample`` is the usage the caller accounts, and ``counts_for_context``
    whether it counts for the context size (Pi takes context usage only from a
    successful response). ``assistant`` is the completed assistant message
    when ``status`` succeeded, otherwise Pi's ``stopReason: "error"`` message
    with what the failed turn kept; both carry the response's priced usage.
    """

    sample: AgentProviderUsageSample
    counts_for_context: bool
    status: AgentProviderStatusDecision
    assistant: AgentAssistantMessage


def settle_provider_result(
    snapshot: AgentProviderRequestSnapshot,
    result: ProviderResult,
    pricing: AgentTokenPricing | None,
    streamed: Sequence[TextContent | ThinkingContent] = (),
) -> ProviderSettlement:
    """Settle one provider result of the request ``snapshot``.

    ``streamed`` are the text and thinking deltas the turn received before
    the result. Pi prices each response from the request's model
    (``calculateCost``) and stores the usage on the assistant message.
    """

    validate_provider_result(result, snapshot)
    sample = AgentProviderUsageSample.from_mapping(result.usage)
    usage = message_usage(sample, pricing)
    status = normalize_provider_status(
        result, provider_name=snapshot.request.provider_name
    )
    if status.action is AgentProviderStatusAction.SUCCEEDED:
        assistant = assistant_message(result, usage, snapshot.request, streamed)
    else:
        assert status.failure is not None
        assistant = stopped_assistant(
            failed_provider_blocks(result, streamed),
            AgentStopReason.ERROR,
            status.failure.message.value,
            usage=usage,
            request=snapshot.request,
            provider_thinking_level=result.provider_thinking_level,
        )
    return ProviderSettlement(
        sample,
        result.status is HarnessStatus.SUCCEEDED,
        status,
        assistant,
    )


@dataclass(frozen=True, slots=True)
class ToolAdmission:
    """One call's admission and, when it may not run, its error text."""

    transition: AgentToolPolicyTransition
    refusal: str | None = None


def admit_tool_call(
    state: AgentToolPolicyState,
    snapshot: AgentProviderRequestSnapshot,
    call: AgentToolCall,
    *,
    mode: AgentToolInvocationMode = AgentToolInvocationMode.DIRECT,
    callable_names: frozenset[str] | None = None,
) -> ToolAdmission:
    """Admit one call through ``decide_tool_admission`` on the current state.

    Admission is per call, not per round: the loop runs a call before it
    admits the next, and a settled call takes its place in the budget only
    when it settles. A refused call gets the loop's error text: over the
    budget, ``tool budget exhausted (limit N)``; a tool the request did not
    offer, ``Tool <name> not found``.
    """

    transition = decide_tool_admission(
        state, snapshot, call, mode=mode, callable_names=callable_names
    )
    if transition.action is AgentToolPolicyAction.BUDGET_EXHAUSTED:
        return ToolAdmission(
            transition,
            f"tool budget exhausted (limit {transition.state.tool_budget})",
        )
    if transition.action is AgentToolPolicyAction.UNAUTHORIZED:
        return ToolAdmission(transition, f"Tool {call.tool_name} not found")
    return ToolAdmission(transition)


def transform_tool_result(
    policy: AgentToolPolicy,
    call: AgentToolCall,
    result: AgentToolResultMessage,
    arguments: dict[str, Any],
    parent_call_id: str | None,
) -> AgentToolResultMessage:
    """The result as the policy leaves it (Pi ``afterToolCall``)."""

    transformed = policy.transform_result(call, result, arguments, parent_call_id)
    blocks: tuple[ToolResultBlock, ...] | None = None
    is_error: bool | None = None
    details: Any = UNCHANGED_TOOL_DETAILS
    if isinstance(transformed, AgentToolResultTransform):
        content = transformed.content
        validate_product_content(content, "AgentToolPolicy.transform_result result")
        structured_content = transformed.structured_content
        blocks, is_error, details = (
            transformed.blocks,
            transformed.is_error,
            transformed.details,
        )
    else:
        content = transformed
        validate_product_content(content, "AgentToolPolicy.transform_result result")
        # Pi: structured content that is not replaced along with the content
        # may no longer match it, so replaced content alone drops it.
        structured_content = (
            result.structured_content
            if content == result.content
            else NO_STRUCTURED_CONTENT
        )
    if blocks is None:
        # Pi: a handler's ``content`` replaces the whole content, images
        # included; a policy that leaves the text as it is keeps them.
        blocks = result.blocks if content == result.content else ()
    new_is_error = result.is_error if is_error is None else is_error
    new_details = (
        result.details
        if details is UNCHANGED_TOOL_DETAILS
        else copy_tool_details(details)
    )
    if (
        content == result.content
        and structured_content is result.structured_content
        and blocks == result.blocks
        and new_is_error == result.is_error
        and new_details is result.details
    ):
        return result
    new_details, evidence = retarget_nested_evidence(
        new_details, result.nested_call_evidence
    )
    return replace(
        result,
        content=content,
        structured_content=structured_content,
        blocks=blocks,
        is_error=new_is_error,
        details=new_details,
        nested_call_evidence=evidence,
    )

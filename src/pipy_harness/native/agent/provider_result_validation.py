"""Pure completed-provider validation shared by volatile and durable runs."""

from __future__ import annotations

from datetime import datetime
from math import isfinite

from pipy_harness.native.agent.content import (
    ProductContent,
    TextContent,
    ThinkingContent,
)
from pipy_harness.native.agent.messages import AgentAssistantMessage
from pipy_harness.native.agent.request import AgentProviderRequestSnapshot
from pipy_harness.native.agent.usage import AgentProviderUsageSample
from pipy_harness.native.models import ProviderResult, ProviderToolCall
from pipy_harness.status import HarnessStatus


def validate_provider_result(
    result: object,
    snapshot: AgentProviderRequestSnapshot,
) -> None:
    validated = _validate_provider_result_identity(result, snapshot)
    _validate_provider_result_scalars(validated)
    _validate_provider_result_optional_mappings(validated)
    _validate_provider_result_tool_calls(validated)


def _validate_provider_result_identity(
    result: object,
    snapshot: AgentProviderRequestSnapshot,
) -> ProviderResult:
    if type(result) is not ProviderResult:
        raise TypeError("provider turn result must be an exact ProviderResult")
    if type(result.status) is not HarnessStatus:
        raise TypeError("ProviderResult.status must be an exact HarnessStatus")
    if result.status not in {
        HarnessStatus.SUCCEEDED,
        HarnessStatus.FAILED,
        HarnessStatus.ABORTED,
    }:
        raise ValueError("ProviderResult.status must be terminal")
    for field_name in ("provider_name", "model_id"):
        if type(getattr(result, field_name)) is not str:
            raise TypeError(f"ProviderResult.{field_name} must be an exact string")
    if result.provider_name != snapshot.request.provider_name:
        raise ValueError("ProviderResult.provider_name must match the request")
    if result.model_id != snapshot.request.model_id:
        raise ValueError("ProviderResult.model_id must match the request")
    return result


def _validate_provider_result_scalars(result: ProviderResult) -> None:
    for field_name in ("started_at", "ended_at"):
        if type(getattr(result, field_name)) is not datetime:
            raise TypeError(f"ProviderResult.{field_name} must be an exact datetime")
    for field_name in (
        "final_text",
        "error_type",
        "error_message",
        "provider_thinking_level",
    ):
        value = getattr(result, field_name)
        if value is not None and type(value) is not str:
            raise TypeError(
                f"ProviderResult.{field_name} must be an exact string or None"
            )
    AgentAssistantMessage(ProductContent(result.final_text or ""))


def _validate_provider_result_optional_mappings(result: ProviderResult) -> None:
    if result.usage is not None:
        _validate_json_mapping(result.usage, "ProviderResult.usage")
        _validate_usage_sample(AgentProviderUsageSample.from_mapping(result.usage))
    if result.metadata is not None:
        _validate_json_mapping(result.metadata, "ProviderResult.metadata")


def _validate_provider_result_tool_calls(result: ProviderResult) -> None:
    if type(result.tool_calls) is not tuple:
        raise TypeError("ProviderResult.tool_calls must be an exact tuple")
    for call in result.tool_calls:
        _validate_provider_tool_call(call)
    _validate_provider_result_blocks(result)


def _validate_provider_result_blocks(result: ProviderResult) -> None:
    """``content_blocks`` hold Pi blocks; a success's match its text and calls."""

    if type(result.content_blocks) is not tuple:
        raise TypeError("ProviderResult.content_blocks must be an exact tuple")
    if result.api is not None and type(result.api) is not str:
        raise TypeError("ProviderResult.api must be an exact string or None")
    for block in result.content_blocks:
        if type(block) is ProviderToolCall:
            _validate_provider_tool_call(block)
        elif type(block) not in (TextContent, ThinkingContent):
            raise TypeError(
                "ProviderResult.content_blocks must contain text, thinking or "
                "tool-call blocks"
            )
    if result.status is not HarnessStatus.SUCCEEDED or not result.content_blocks:
        return
    calls = tuple(b for b in result.content_blocks if type(b) is ProviderToolCall)
    text = "".join(b.text for b in result.content_blocks if type(b) is TextContent)
    if calls != result.tool_calls or text != (result.final_text or ""):
        raise ValueError(
            "ProviderResult.content_blocks must match its final_text and tool_calls"
        )


def _validate_provider_tool_call(call: object) -> None:
    if type(call) is not ProviderToolCall:
        raise TypeError(
            "ProviderResult.tool_calls must contain exact ProviderToolCall values"
        )
    for field_name in (
        "provider_correlation_id",
        "tool_name",
        "arguments_json",
    ):
        value = getattr(call, field_name)
        if type(value) is not str:
            raise TypeError(f"ProviderToolCall.{field_name} must be an exact string")
    if not call.provider_correlation_id:
        raise ValueError("ProviderToolCall.provider_correlation_id must not be empty")
    if not call.tool_name:
        raise ValueError("ProviderToolCall.tool_name must not be empty")
    if (
        len(call.provider_correlation_id)
        > ProviderToolCall.PROVIDER_CORRELATION_ID_MAX_LENGTH
    ):
        raise ValueError("ProviderToolCall.provider_correlation_id is too long")
    if len(call.tool_name) > ProviderToolCall.TOOL_NAME_MAX_LENGTH:
        raise ValueError("ProviderToolCall.tool_name is too long")
    if len(call.arguments_json) > ProviderToolCall.ARGUMENTS_JSON_MAX_LENGTH:
        raise ValueError("ProviderToolCall.arguments_json is too long")


def _validate_usage_sample(sample: AgentProviderUsageSample) -> None:
    if any(
        getattr(sample, field_name) < 0 for field_name in sample.__dataclass_fields__
    ):
        raise ValueError("ProviderResult.usage counters must not be negative")


def _validate_json_mapping(value: object, field_name: str) -> None:
    if type(value) is not dict:
        raise TypeError(f"{field_name} must be an exact dict")
    _validate_json_value(value, field_name, set())


def _validate_json_value(value: object, field_name: str, seen: set[int]) -> None:
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is float:
        if not isfinite(value):
            raise ValueError(f"{field_name} must contain finite numbers")
        return
    if type(value) not in {dict, list}:
        raise TypeError(f"{field_name} must contain exact JSON values")
    identity = id(value)
    if identity in seen:
        raise ValueError(f"{field_name} must not contain cycles")
    seen.add(identity)
    try:
        if isinstance(value, dict):
            for key, child in value.items():
                if type(key) is not str:
                    raise TypeError(f"{field_name} keys must be exact strings")
                _validate_json_value(child, field_name, seen)
        else:
            assert isinstance(value, list)
            for child in value:
                _validate_json_value(child, field_name, seen)
    finally:
        seen.remove(identity)

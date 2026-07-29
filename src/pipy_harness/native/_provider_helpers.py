"""Shared helpers for the native provider adapters.

Provider modules share a small set of helpers: a UTC clock, label
sanitizer, OpenAI tool-call/serializer parsers, message-envelope
serialization, and a ``HarnessStatus.FAILED`` `ProviderResult` builder.
The HTTP transport boundary — cancellable ``urlopen``, the JSON response
boundary, body decoding, and safe usage extraction — lives in
:mod:`pipy_harness.native.http`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pipy_harness.capture import sanitize_text
from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.tools.base import materialize_tool_input_schema


def utc_now() -> datetime:
    """Return the current UTC timestamp."""

    return datetime.now(UTC)


def safe_response_label(value: Any, *, default: str) -> str:
    """Return ``value`` if it sanitizes to a non-redacted label, else ``default``."""

    if not isinstance(value, str) or not value:
        return default
    sanitized = sanitize_text(value)
    return sanitized if sanitized != "[REDACTED]" else default


def extract_responses_tool_calls(
    value: Any, *, provider_prefix: str
) -> tuple[Any, ...]:
    """Parse OpenAI Responses-API ``function_call`` output items into `ProviderToolCall`s."""

    from pipy_harness.native.models import ProviderToolCall

    if not isinstance(value, list):
        return ()
    calls: list[ProviderToolCall] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "function_call":
            continue
        name = item.get("name")
        arguments = item.get("arguments")
        call_id = item.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            candidate_id = item.get("id")
            call_id = (
                candidate_id if isinstance(candidate_id, str) and candidate_id else None
            )
        if not isinstance(name, str) or not name:
            continue
        if isinstance(arguments, Mapping):
            arguments = json.dumps(arguments, sort_keys=True)
        if not isinstance(arguments, str):
            arguments = ""
        correlation = call_id if call_id else f"{provider_prefix}-tool-{index}"
        try:
            calls.append(
                ProviderToolCall(
                    provider_correlation_id=correlation[
                        : ProviderToolCall.PROVIDER_CORRELATION_ID_MAX_LENGTH
                    ],
                    tool_name=name[: ProviderToolCall.TOOL_NAME_MAX_LENGTH],
                    arguments_json=arguments[
                        : ProviderToolCall.ARGUMENTS_JSON_MAX_LENGTH
                    ],
                )
            )
        except ValueError:
            continue
    return tuple(calls)


def extract_chat_completions_tool_calls(
    value: Any, *, provider_prefix: str
) -> tuple[Any, ...]:
    """Parse OpenAI Chat-Completions ``tool_calls`` arrays into `ProviderToolCall`s."""

    from pipy_harness.native.models import ProviderToolCall

    if not isinstance(value, list):
        return ()
    calls: list[ProviderToolCall] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        if item.get("type") not in (None, "function"):
            continue
        identifier = item.get("id")
        function = item.get("function")
        if not isinstance(function, Mapping):
            continue
        name = function.get("name")
        arguments_json = function.get("arguments")
        if not isinstance(name, str) or not name:
            continue
        if isinstance(arguments_json, Mapping):
            arguments_json = json.dumps(arguments_json, sort_keys=True)
        if not isinstance(arguments_json, str):
            arguments_json = ""
        correlation = (
            identifier
            if isinstance(identifier, str) and identifier
            else f"{provider_prefix}-tool-{index}"
        )
        try:
            calls.append(
                ProviderToolCall(
                    provider_correlation_id=correlation[
                        : ProviderToolCall.PROVIDER_CORRELATION_ID_MAX_LENGTH
                    ],
                    tool_name=name[: ProviderToolCall.TOOL_NAME_MAX_LENGTH],
                    arguments_json=arguments_json[
                        : ProviderToolCall.ARGUMENTS_JSON_MAX_LENGTH
                    ],
                )
            )
        except ValueError:
            continue
    return tuple(calls)


def serialize_tool_for_chat_completions(tool: Any) -> dict[str, Any]:
    """OpenAI Chat-Completions tool shape: nested ``function`` object."""

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": materialize_tool_input_schema(tool.input_schema),
        },
    }


def serialize_tool_for_anthropic(tool: Any) -> dict[str, Any]:
    """Anthropic Messages tool shape: ``input_schema`` on a flat object."""

    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": materialize_tool_input_schema(tool.input_schema),
    }


def serialize_tool_for_responses(tool: Any) -> dict[str, Any]:
    """OpenAI Responses-API tool shape: flat object with ``parameters``."""

    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": materialize_tool_input_schema(tool.input_schema),
    }


def envelope_to_chat_message(envelope: Any) -> dict[str, Any]:
    """Serialize an ``AgentMessage`` into the OpenAI Chat-Completions shape."""

    from pipy_harness.native.agent import (
        AgentAssistantMessage,
        AgentToolResultMessage,
        AgentUserMessage,
    )

    if isinstance(envelope, AgentUserMessage):
        return {"role": "user", "content": envelope.content.value}
    if isinstance(envelope, AgentAssistantMessage):
        message: dict[str, Any] = {"role": "assistant"}
        if envelope.content.value:
            message["content"] = envelope.content.value
        if envelope.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.provider_correlation_id,
                    "type": "function",
                    "function": {
                        "name": call.tool_name,
                        "arguments": call.arguments_json.value,
                    },
                }
                for call in envelope.tool_calls
            ]
        if "content" not in message:
            message["content"] = ""
        return message
    if isinstance(envelope, AgentToolResultMessage):
        return {
            "role": "tool",
            "tool_call_id": envelope.provider_correlation_id,
            "content": envelope.content.value,
        }
    raise ValueError(f"unsupported message envelope: {type(envelope).__name__}")


def extract_text_content(value: Any) -> str | None:
    """Extract the assistant ``text`` content from an OpenAI-shape message."""

    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return None
    chunks: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            chunks.append(item["text"])
    if not chunks:
        return None
    return "".join(chunks)


def failed_provider_result(
    request: ProviderRequest,
    *,
    provider_name: str,
    started_at: datetime,
    error_type: str,
    error_message: str,
    metadata: Mapping[str, Any] | None = None,
) -> ProviderResult:
    """Build a sanitized ``HarnessStatus.FAILED`` `ProviderResult`."""

    return ProviderResult(
        status=HarnessStatus.FAILED,
        provider_name=provider_name,
        model_id=request.model_id,
        started_at=started_at,
        ended_at=utc_now(),
        metadata=dict(metadata or {}),
        error_type=sanitize_text(error_type),
        error_message=sanitize_text(error_message),
    )

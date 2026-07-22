"""Shared Chat Completions wire translation for native OpenAI-compatible providers.

This module is the single owner of the byte-identical Chat Completions request
and response translation shared by the canonical OpenAI Chat Completions adapter
(`OpenAIChatCompletionsProvider`, reused by ds4) and its compatible clones
(`MistralProvider`, `OpenRouterChatCompletionsProvider`,
`CloudflareWorkersAIProvider`). Each adapter keeps its own auth/URL logic,
provider dataclass, and sanitized error hierarchy; only the per-provider
parse-error class, the human-readable response label used in parse errors, the
tool-call provider prefix, and the usage-field remap vary and are passed in as
arguments here. No wire shape, tool-call id, usage key, or event ordering is
decided anywhere else.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pipy_harness.capture import sanitize_text
from pipy_harness.native._provider_helpers import (
    envelope_to_chat_message,
    extract_chat_completions_tool_calls,
    extract_text_content,
    safe_response_label,
)
from pipy_harness.native.http import ProviderHTTPError, extract_usage_from_fields
from pipy_harness.native.models import ProviderRequest, ProviderToolCall


@dataclass(frozen=True, slots=True)
class ParsedChatCompletion:
    """Shared parsed result of a Chat Completions success response."""

    final_text: str | None
    usage: dict[str, int | float]
    response_object: str
    finish_reason: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


def chat_messages(request: ProviderRequest) -> list[dict[str, Any]]:
    """Translate a canonical ``ProviderRequest`` into Chat Completions messages.

    Emits the system envelope, then either the canonical message list (with
    ``tool_calls``/``tool`` roles) or the legacy single-turn payload built from
    ``system_prompt``/``user_prompt`` plus any no-tool REPL context.
    """

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": request.system_prompt}
    ]
    if request.messages:
        for envelope in request.messages:
            messages.append(envelope_to_chat_message(envelope))
        return messages
    if request.no_tool_repl_context is not None:
        for exchange in request.no_tool_repl_context.exchanges:
            messages.append({"role": "user", "content": exchange.user_prompt})
            messages.append(
                {"role": "assistant", "content": exchange.provider_final_text}
            )
    messages.append({"role": "user", "content": request.user_prompt})
    return messages


def parse_response(
    body: Mapping[str, Any],
    *,
    parse_error_class: type[ProviderHTTPError],
    response_label: str,
    tool_call_provider_prefix: str,
    usage_fields: tuple[tuple[str, str], ...],
) -> ParsedChatCompletion:
    """Parse a Chat Completions response body into a shared parsed result.

    ``parse_error_class`` is the adapter's sanitized parse-error type,
    ``response_label`` is the human-readable provider name used in parse error
    messages, ``tool_call_provider_prefix`` prefixes synthesized tool-call ids,
    and ``usage_fields`` is the adapter's usage-key remap.
    """

    error = body.get("error")
    if isinstance(error, Mapping):
        error_code = error.get("code")
        metadata: dict[str, Any] = {"provider_response_store_requested": False}
        if isinstance(error_code, str | int):
            metadata["api_error_code"] = sanitize_text(str(error_code))
        raise parse_error_class(
            f"{response_label} response included an error.", metadata=metadata
        )

    response_object = safe_response_label(body.get("object"), default="unknown")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise parse_error_class(
            f"{response_label} response did not include a completion choice.",
            metadata={
                "provider_response_store_requested": False,
                "response_object": response_object,
            },
        )

    first_choice = choices[0]
    if not isinstance(first_choice, Mapping):
        raise parse_error_class(
            f"{response_label} response included an unsupported completion choice.",
            metadata={
                "provider_response_store_requested": False,
                "response_object": response_object,
            },
        )
    finish_reason = safe_response_label(
        first_choice.get("finish_reason"), default="unknown"
    )
    message = first_choice.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    final_text = extract_text_content(content)
    tool_calls = extract_chat_completions_tool_calls(
        message.get("tool_calls") if isinstance(message, Mapping) else None,
        provider_prefix=tool_call_provider_prefix,
    )
    if not final_text and not tool_calls:
        raise parse_error_class(
            f"{response_label} response did not include final message content or tool calls.",
            metadata={
                "provider_response_store_requested": False,
                "response_object": response_object,
                "finish_reason": finish_reason,
            },
        )

    return ParsedChatCompletion(
        final_text=final_text,
        usage=extract_usage_from_fields(body.get("usage"), usage_fields),
        response_object=response_object,
        finish_reason=finish_reason,
        tool_calls=tool_calls,
    )

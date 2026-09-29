"""Shared Chat Completions wire translation for native OpenAI-compatible providers.

This module is the single owner of the byte-identical Chat Completions request
and response translation shared by the canonical OpenAI Chat Completions adapter
(`OpenAIChatCompletionsProvider`, reused by ds4) and its compatible clones
(`MistralProvider`, `OpenRouterChatCompletionsProvider`,
`CloudflareWorkersAIProvider`). Each adapter keeps its own auth/URL logic,
provider dataclass, and sanitized error hierarchy; only the per-provider
parse-error class, the human-readable response label used in parse errors, the
tool-call provider prefix, and the usage extractor (Pi's OpenAI-compatible or
Mistral cache-counter reading) vary and are passed in as arguments here. No wire shape, tool-call id, usage key, or event ordering is
decided anywhere else.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pipy_harness.capture import sanitize_text
from pipy_harness.native._provider_helpers import (
    envelope_to_chat_message,
    extract_chat_completions_tool_calls,
    extract_text_content,
    safe_response_label,
)
from pipy_harness.native.http import ProviderHTTPError
from pipy_harness.native.models import ProviderRequest, ProviderToolCall
from pipy_harness.native.usage import normalize_provider_usage


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
    ``tool_calls``/``tool`` roles) or the single-turn payload built from
    ``system_prompt``/``user_prompt``.
    """

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": request.system_prompt}
    ]
    if request.messages:
        for envelope in request.messages:
            messages.append(envelope_to_chat_message(envelope))
        return messages
    messages.append({"role": "user", "content": request.user_prompt})
    return messages


def parse_response(
    body: Mapping[str, Any],
    *,
    parse_error_class: type[ProviderHTTPError],
    response_label: str,
    tool_call_provider_prefix: str,
    extract_usage: Callable[[Any], dict[str, int | float]],
) -> ParsedChatCompletion:
    """Parse a Chat Completions response body into a shared parsed result.

    ``parse_error_class`` is the adapter's sanitized parse-error type,
    ``response_label`` is the human-readable provider name used in parse error
    messages, ``tool_call_provider_prefix`` prefixes synthesized tool-call ids,
    and ``extract_usage`` normalizes the ``usage`` object
    (:func:`extract_chat_completions_usage` or :func:`extract_mistral_usage`).
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
        usage=extract_usage(body.get("usage")),
        response_object=response_object,
        finish_reason=finish_reason,
        tool_calls=tool_calls,
    )


def extract_chat_completions_usage(value: Any) -> dict[str, int | float]:
    """Normalize OpenAI-compatible ``usage`` as Pi's ``parseChunkUsage`` reads it.

    Cache reads come from ``prompt_tokens_details.cached_tokens``, then
    DeepSeek's ``prompt_cache_hit_tokens``, then a top-level ``cached_tokens``;
    cache writes from ``prompt_tokens_details.cache_write_tokens``; reasoning
    from ``completion_tokens_details.reasoning_tokens`` (already inside
    ``completion_tokens``). Cache counters sit inside ``prompt_tokens``.
    """

    if not isinstance(value, Mapping):
        return {}
    prompt_details = value.get("prompt_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, Mapping) else {}
    completion_details = value.get("completion_tokens_details")
    completion_details = (
        completion_details if isinstance(completion_details, Mapping) else {}
    )
    cached = prompt_details.get("cached_tokens")
    if cached is None:
        cached = value.get("prompt_cache_hit_tokens")
    if cached is None:
        cached = value.get("cached_tokens")
    reasoning = completion_details.get("reasoning_tokens")
    if reasoning is None:
        # pipy keeps a top-level reasoning counter some OpenRouter-compatible
        # hosts send. Pi does not read it; it is display-only and never priced.
        reasoning = value.get("reasoning_tokens")
    return normalize_provider_usage(
        {
            "input_tokens": value.get("prompt_tokens"),
            "output_tokens": value.get("completion_tokens"),
            "total_tokens": value.get("total_tokens"),
            "cached_tokens": cached,
            "cache_write_tokens": prompt_details.get("cache_write_tokens"),
            "reasoning_tokens": reasoning,
        }
    )


def extract_mistral_usage(value: Any) -> dict[str, int | float]:
    """Normalize Mistral ``usage`` as Pi's ``getMistralCachedPromptTokens`` does.

    The cache read is the first present of the prompt-details cached counters
    or ``num_cached_tokens``, clamped to ``[0, prompt_tokens]``.
    """

    if not isinstance(value, Mapping):
        return {}
    cached: Any = None
    # Pi's order, camelCase (Mistral SDK) and snake_case (REST) alike.
    for details_key, cached_key in (
        ("promptTokensDetails", "cachedTokens"),
        ("prompt_tokens_details", "cached_tokens"),
        ("promptTokenDetails", "cachedTokens"),
        ("prompt_token_details", "cached_tokens"),
    ):
        details = value.get(details_key)
        if isinstance(details, Mapping) and details.get(cached_key) is not None:
            cached = details.get(cached_key)
            break
    if cached is None:
        cached = value.get("numCachedTokens")
    if cached is None:
        cached = value.get("num_cached_tokens")
    prompt = value.get("prompt_tokens")
    if isinstance(cached, float) and not math.isfinite(cached):
        cached = None  # Pi discards a non-finite count
    if (
        isinstance(cached, int | float)
        and not isinstance(cached, bool)
        and isinstance(prompt, int)
        and not isinstance(prompt, bool)
    ):
        cached = min(prompt, max(0, int(cached)))
    return normalize_provider_usage(
        {
            "input_tokens": prompt,
            "output_tokens": value.get("completion_tokens"),
            "total_tokens": value.get("total_tokens"),
            "cached_tokens": cached,
        }
    )

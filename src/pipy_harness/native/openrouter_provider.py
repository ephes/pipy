"""OpenRouter Chat Completions provider for the native pipy runtime."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pipy_harness.capture import sanitize_text
from pipy_harness.native._provider_helpers import utc_now, safe_response_label, failed_provider_result, extract_text_content, safe_http_status_metadata, JsonResponse, JsonHTTPClient, envelope_to_chat_message, extract_chat_completions_tool_calls, serialize_tool_for_chat_completions, extract_usage_from_fields, decode_json_object, urlopen_read_cancellable
from pipy_harness.models import HarnessStatus
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.provider import StreamChunkSink

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("prompt_tokens", "input_tokens"),
    ("completion_tokens", "output_tokens"),
    ("total_tokens", "total_tokens"),
    ("cached_tokens", "cached_tokens"),
    ("reasoning_tokens", "reasoning_tokens"),
)


@dataclass(frozen=True, slots=True)
class UrllibJsonHTTPClient:
    """Standard-library JSON client for OpenRouter Chat Completions calls."""

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
        cancel_token: CancelToken | None = None,
    ) -> JsonResponse:
        encoded = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=encoded,
            headers=dict(headers),
            method="POST",
        )
        try:
            status_code, payload = urlopen_read_cancellable(
                request,
                timeout_seconds=timeout_seconds,
                cancel_token=cancel_token,
            )
        except urllib.error.HTTPError as exc:
            raise OpenRouterHTTPStatusError.from_http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = sanitize_text(str(exc.reason)) if getattr(exc, "reason", None) else "request failed"
            raise OpenRouterTransportError(f"OpenRouter API request failed: {reason}") from exc

        return JsonResponse(status_code=status_code, body=decode_json_object(payload, error_class=OpenRouterResponseParseError, provider_label="OpenRouter API"))


@dataclass(frozen=True, slots=True)
class OpenRouterChatCompletionsProvider:
    """OpenRouter Chat Completions provider behind ProviderPort.

    OpenRouter is the first real provider with `supports_tool_calls=True`.
    When `ProviderRequest.messages` is non-empty the provider serializes
    them in the OpenAI chat completions format (with `tool_calls` and
    `tool` roles); otherwise it falls back to the legacy single-turn
    payload built from `system_prompt`/`user_prompt`.
    """

    model_id: str
    api_key: str | None = field(default_factory=lambda: os.environ.get("OPENROUTER_API_KEY"))
    http_client: JsonHTTPClient = field(default_factory=UrllibJsonHTTPClient)
    endpoint: str = OPENROUTER_CHAT_COMPLETIONS_URL
    timeout_seconds: float = 60.0
    supports_tool_calls: bool = True

    @property
    def name(self) -> str:
        return "openrouter"

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del stream_sink, reasoning_sink
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        started_at = utc_now()
        if not self.model_id or not self.model_id.strip():
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenRouterConfigurationError",
                error_message="--native-model is required for native provider openrouter.",
            )
        api_key = self.api_key.strip() if self.api_key is not None else ""
        if not api_key:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenRouterAuthError",
                error_message=(
                    "OpenRouter API key is required in the environment for native provider openrouter."
                ),
            )

        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": _chat_messages(request),
            "stream": False,
        }
        if request.available_tools:
            body["tools"] = [
                serialize_tool_for_chat_completions(tool) for tool in request.available_tools
            ]
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = self.http_client.post_json(
                self.endpoint,
                headers=headers,
                body=body,
                timeout_seconds=self.timeout_seconds,
                cancel_token=cancel_token,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise OpenRouterHTTPStatusError(
                    f"OpenRouter API request failed with HTTP status {response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = _parse_response(response.body)
        except OpenRouterProviderError as exc:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type=type(exc).__name__,
                error_message=str(exc),
                metadata=exc.metadata,
            )

        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=started_at,
            ended_at=utc_now(),
            final_text=result.final_text,
            usage=result.usage,
            metadata={
                "provider_response_store_requested": False,
                "response_object": result.response_object,
                "finish_reason": result.finish_reason,
            },
            tool_calls=result.tool_calls,
        )


@dataclass(frozen=True, slots=True)
class ParsedOpenRouterResponse:
    final_text: str | None
    usage: dict[str, int | float]
    response_object: str
    finish_reason: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


class OpenRouterProviderError(Exception):
    """Base class for sanitized OpenRouter provider errors."""

    def __init__(self, message: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        super().__init__(sanitize_text(message))
        self.metadata = dict(metadata or {})


class OpenRouterHTTPStatusError(OpenRouterProviderError):
    """Raised when OpenRouter returns a non-success HTTP status."""

    @classmethod
    def from_http_error(cls, exc: urllib.error.HTTPError) -> OpenRouterHTTPStatusError:
        metadata = safe_http_status_metadata(exc.code)
        try:
            body = decode_json_object(exc.read(), error_class=OpenRouterResponseParseError, provider_label="OpenRouter API")
        except OpenRouterResponseParseError:
            body = {}
        error = body.get("error")
        if isinstance(error, Mapping):
            error_code = error.get("code")
            if isinstance(error_code, str | int):
                metadata["api_error_code"] = sanitize_text(str(error_code))
        return cls(f"OpenRouter API request failed with HTTP status {exc.code}.", metadata=metadata)


class OpenRouterTransportError(OpenRouterProviderError):
    """Raised when the HTTP request cannot reach OpenRouter."""


class OpenRouterResponseParseError(OpenRouterProviderError):
    """Raised when the OpenRouter response shape is unsupported."""


def _parse_response(body: Mapping[str, Any]) -> ParsedOpenRouterResponse:
    error = body.get("error")
    if isinstance(error, Mapping):
        error_code = error.get("code")
        metadata: dict[str, Any] = {"provider_response_store_requested": False}
        if isinstance(error_code, str | int):
            metadata["api_error_code"] = sanitize_text(str(error_code))
        raise OpenRouterResponseParseError("OpenRouter response included an error.", metadata=metadata)

    response_object = safe_response_label(body.get("object"), default="unknown")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OpenRouterResponseParseError(
            "OpenRouter response did not include a completion choice.",
            metadata={
                "provider_response_store_requested": False,
                "response_object": response_object,
            },
        )

    first_choice = choices[0]
    if not isinstance(first_choice, Mapping):
        raise OpenRouterResponseParseError(
            "OpenRouter response included an unsupported completion choice.",
            metadata={
                "provider_response_store_requested": False,
                "response_object": response_object,
            },
        )
    finish_reason = safe_response_label(first_choice.get("finish_reason"), default="unknown")
    message = first_choice.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    final_text = extract_text_content(content)
    tool_calls = extract_chat_completions_tool_calls(
        message.get("tool_calls") if isinstance(message, Mapping) else None,
        provider_prefix="openrouter",
    )
    if not final_text and not tool_calls:
        raise OpenRouterResponseParseError(
            "OpenRouter response did not include final message content or tool calls.",
            metadata={
                "provider_response_store_requested": False,
                "response_object": response_object,
                "finish_reason": finish_reason,
            },
        )

    return ParsedOpenRouterResponse(
        final_text=final_text,
        usage=extract_usage_from_fields(body.get("usage"), OPENROUTER_USAGE_FIELDS),
        response_object=response_object,
        finish_reason=finish_reason,
        tool_calls=tool_calls,
    )


def _chat_messages(request: ProviderRequest) -> list[dict[str, Any]]:
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
            messages.append({"role": "assistant", "content": exchange.provider_final_text})
    messages.append({"role": "user", "content": request.user_prompt})
    return messages

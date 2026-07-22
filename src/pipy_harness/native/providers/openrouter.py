"""OpenRouter Chat Completions provider for the native pipy runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from pipy_harness.native._provider_helpers import utc_now, failed_provider_result, serialize_tool_for_chat_completions
from pipy_harness.native.http import (
    ApiErrorField,
    JsonResponse as JsonResponse,
    JsonHTTPClient,
    ProviderHTTPError,
    UrllibJsonHTTPClient,
)
from pipy_harness.models import HarnessStatus
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import StreamChunkSink, apply_provider_headers
from pipy_harness.native.providers.chat_completions_wire import chat_messages, parse_response

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("prompt_tokens", "input_tokens"),
    ("completion_tokens", "output_tokens"),
    ("total_tokens", "total_tokens"),
    ("cached_tokens", "cached_tokens"),
    ("reasoning_tokens", "reasoning_tokens"),
)


def openrouter_http_client() -> UrllibJsonHTTPClient:
    """Build the shared JSON client wired with OpenRouter error types."""

    return UrllibJsonHTTPClient(
        provider_label="OpenRouter API",
        status_error_class=OpenRouterHTTPStatusError,
        transport_error_class=OpenRouterTransportError,
        parse_error_class=OpenRouterResponseParseError,
    )


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
    http_client: JsonHTTPClient = field(default_factory=openrouter_http_client)
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
            "messages": chat_messages(request),
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
        headers = apply_provider_headers(request, headers)

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
            result = parse_response(
                response.body,
                parse_error_class=OpenRouterResponseParseError,
                response_label="OpenRouter",
                tool_call_provider_prefix="openrouter",
                usage_fields=OPENROUTER_USAGE_FIELDS,
            )
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


class OpenRouterProviderError(ProviderHTTPError):
    """Base class for sanitized OpenRouter provider errors."""


class OpenRouterHTTPStatusError(OpenRouterProviderError):
    """Raised when OpenRouter returns a non-success HTTP status."""

    provider_label = "OpenRouter API"
    api_error_fields = (
        ApiErrorField("code", "api_error_code", sanitize=True, allow_int=True),
    )


class OpenRouterTransportError(OpenRouterProviderError):
    """Raised when the HTTP request cannot reach OpenRouter."""


class OpenRouterResponseParseError(OpenRouterProviderError):
    """Raised when the OpenRouter response shape is unsupported."""

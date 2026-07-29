"""Mistral Chat Completions provider for the native pipy runtime."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pipy_harness.native._provider_helpers import (
    utc_now,
    failed_provider_result,
    serialize_tool_for_chat_completions,
)
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
from pipy_harness.native.providers.chat_completions_wire import (
    chat_messages,
    parse_response,
)

MISTRAL_CHAT_COMPLETIONS_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("prompt_tokens", "input_tokens"),
    ("completion_tokens", "output_tokens"),
    ("total_tokens", "total_tokens"),
)


def mistral_http_client() -> UrllibJsonHTTPClient:
    """Build the shared JSON client wired with Mistral error types."""

    return UrllibJsonHTTPClient(
        provider_label="Mistral API",
        status_error_class=MistralHTTPStatusError,
        transport_error_class=MistralTransportError,
        parse_error_class=MistralResponseParseError,
    )


@dataclass(frozen=True, slots=True)
class MistralProvider:
    """Mistral Chat Completions provider behind ProviderPort.

    Mistral exposes an OpenAI-compatible Chat Completions API. When
    `ProviderRequest.messages` is non-empty the provider serializes them in the
    OpenAI chat completions format (with `tool_calls` and `tool` roles);
    otherwise it falls back to the legacy single-turn payload built from
    `system_prompt`/`user_prompt`.
    """

    model_id: str
    api_key: str | None = field(
        default_factory=lambda: os.environ.get("MISTRAL_API_KEY"), repr=False
    )
    http_client: JsonHTTPClient = field(default_factory=mistral_http_client)
    endpoint: str = MISTRAL_CHAT_COMPLETIONS_URL
    timeout_seconds: float = 60.0
    supports_tool_calls: bool = True
    provider_name: str = "mistral"
    # Catalog-resolved request config (parity with the completions adapter).
    # ``extra_headers`` are merged models.json/model headers (an explicit
    # Authorization wins over ``Bearer api_key``); ``reasoning_effort`` is the
    # mapped thinking value (Mistral's OpenAI-compatible ``reasoning_effort``).
    extra_headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    reasoning_effort: str | None = None

    @property
    def name(self) -> str:
        return self.provider_name

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
                error_type="MistralConfigurationError",
                error_message=f"--native-model is required for native provider {self.name}.",
            )
        api_key = self.api_key.strip() if self.api_key is not None else ""
        has_explicit_authorization = any(
            header_name.lower() == "authorization" for header_name in self.extra_headers
        )
        if not api_key and not has_explicit_authorization:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="MistralAuthError",
                error_message=(
                    "Mistral API key is required in the environment for native "
                    f"provider {self.name}."
                ),
            )

        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": chat_messages(request),
        }
        if request.available_tools:
            body["tools"] = [
                serialize_tool_for_chat_completions(tool)
                for tool in request.available_tools
            ]
        if self.reasoning_effort is not None:
            body["reasoning_effort"] = self.reasoning_effort
        headers = {"Content-Type": "application/json"}
        # Merged models.json/model headers (may include an explicit Authorization).
        for header_name, header_value in self.extra_headers.items():
            headers[header_name] = header_value
        # Apply ``Bearer api_key`` only when no explicit Authorization is present.
        if api_key and not has_explicit_authorization:
            headers["Authorization"] = f"Bearer {api_key}"
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
                raise MistralHTTPStatusError(
                    f"Mistral API request failed with HTTP status {response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = parse_response(
                response.body,
                parse_error_class=MistralResponseParseError,
                response_label="Mistral",
                tool_call_provider_prefix="mistral",
                usage_fields=MISTRAL_USAGE_FIELDS,
            )
        except MistralProviderError as exc:
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


class MistralProviderError(ProviderHTTPError):
    """Base class for sanitized Mistral provider errors."""


class MistralHTTPStatusError(MistralProviderError):
    """Raised when Mistral returns a non-success HTTP status."""

    provider_label = "Mistral API"
    api_error_fields = (
        ApiErrorField("code", "api_error_code", sanitize=True, allow_int=True),
    )


class MistralTransportError(MistralProviderError):
    """Raised when the HTTP request cannot reach Mistral."""


class MistralResponseParseError(MistralProviderError):
    """Raised when the Mistral response shape is unsupported."""

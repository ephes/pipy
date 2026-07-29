"""OpenAI Chat Completions API provider for the native pipy runtime.

This provider targets the `/v1/chat/completions` endpoint, which is distinct
from the Responses API surfaced by `OpenAIResponsesProvider`. Its request and
response shapes follow the universal Chat Completions contract (the same
contract OpenRouter speaks), so the on-the-wire envelope mirrors
`OpenRouterChatCompletionsProvider`. It is wired up as the
`openai-completions` native provider.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
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

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_COMPLETIONS_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("prompt_tokens", "input_tokens"),
    ("completion_tokens", "output_tokens"),
    ("total_tokens", "total_tokens"),
)


@dataclass(frozen=True, slots=True)
class _OpenAICompletionsRequestConfiguration:
    """Validated, single-read configuration for one completion request."""

    model_id: str
    api_key: str


def openai_completions_http_client(
    provider_label: str = "OpenAI API",
) -> UrllibJsonHTTPClient:
    """Build the shared JSON client wired with Chat Completions error types.

    ``provider_label`` names the provider in transport/parse messages so
    OpenAI-compatible reuses (e.g. ds4) surface their own label there; the
    HTTP-status message stays labelled ``OpenAI API`` (see
    :class:`OpenAICompletionsHTTPStatusError`).
    """

    return UrllibJsonHTTPClient(
        provider_label=provider_label,
        status_error_class=OpenAICompletionsHTTPStatusError,
        transport_error_class=OpenAICompletionsTransportError,
        parse_error_class=OpenAICompletionsResponseParseError,
    )


@dataclass(frozen=True, slots=True)
class OpenAIChatCompletionsProvider:
    """OpenAI Chat Completions provider behind ProviderPort.

    Real adapter with `supports_tool_calls=True`. When
    `ProviderRequest.messages` is non-empty the provider serializes them
    in the OpenAI chat completions format (with `tool_calls` and `tool`
    roles); otherwise it falls back to the legacy single-turn payload
    built from `system_prompt`/`user_prompt`.
    """

    model_id: str
    # ``repr=False`` on credential-bearing fields so a stray repr/log of the
    # constructed adapter never leaks the api key or auth headers.
    api_key: str | None = field(
        default_factory=lambda: os.environ.get("OPENAI_API_KEY"), repr=False
    )
    http_client: JsonHTTPClient = field(default_factory=openai_completions_http_client)
    endpoint: str = OPENAI_CHAT_COMPLETIONS_URL
    timeout_seconds: float = 60.0
    supports_tool_calls: bool = True
    provider_name: str = "openai-completions"
    auth_required: bool = True
    # Catalog-resolved request config (M-item-18). ``extra_headers`` are merged
    # provider/model headers (``Bearer api_key`` is applied only when no
    # Authorization header is already present); ``extra_body`` carries routing
    # (OpenRouter ``provider`` / Vercel ``providerOptions``); ``reasoning_effort``
    # is the mapped thinking value.
    extra_headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    reasoning_effort: str | None = None

    @property
    def name(self) -> str:
        return self.provider_name

    def _configuration_preflight(
        self,
        request: ProviderRequest,
        started_at: datetime,
    ) -> _OpenAICompletionsRequestConfiguration | ProviderResult:
        """Validate and normalize request configuration in failure-order."""

        if not self.model_id or not self.model_id.strip():
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenAICompletionsConfigurationError",
                error_message=(
                    f"--native-model is required for native provider {self.name}."
                ),
            )
        api_key = self.api_key.strip() if self.api_key is not None else ""
        if not api_key and self.auth_required:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenAICompletionsAuthError",
                error_message=(
                    "OpenAI API key is required in the environment for native "
                    f"provider {self.name}."
                ),
            )
        return _OpenAICompletionsRequestConfiguration(
            model_id=self.model_id,
            api_key=api_key,
        )

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
        configuration = self._configuration_preflight(request, started_at)
        if isinstance(configuration, ProviderResult):
            return configuration

        body: dict[str, Any] = {
            "model": configuration.model_id,
            "messages": chat_messages(request),
            "stream": False,
        }
        if request.available_tools:
            body["tools"] = [
                serialize_tool_for_chat_completions(tool)
                for tool in request.available_tools
            ]
        # Catalog-resolved routing/compat (e.g. OpenRouter ``provider`` block,
        # Vercel ``providerOptions``) and the mapped thinking value.
        for key, value in self.extra_body.items():
            body[key] = value
        if self.reasoning_effort is not None:
            body["reasoning_effort"] = self.reasoning_effort
        headers = {"Content-Type": "application/json"}
        # Merged provider/model headers (may include an explicit Authorization).
        for header_name, header_value in self.extra_headers.items():
            headers[header_name] = header_value
        # Apply Bearer api_key only when no Authorization header is already
        # present, so an explicit models.json Authorization is preserved.
        has_authorization = any(name.lower() == "authorization" for name in headers)
        if configuration.api_key and not has_authorization:
            headers["Authorization"] = f"Bearer {configuration.api_key}"
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
                raise OpenAICompletionsHTTPStatusError(
                    f"OpenAI API request failed with HTTP status {response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = parse_response(
                response.body,
                parse_error_class=OpenAICompletionsResponseParseError,
                response_label="OpenAI",
                tool_call_provider_prefix="openai-completions",
                usage_fields=OPENAI_COMPLETIONS_USAGE_FIELDS,
            )
        except OpenAICompletionsProviderError as exc:
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


class OpenAICompletionsProviderError(ProviderHTTPError):
    """Base class for sanitized OpenAI Chat Completions provider errors."""


class OpenAICompletionsHTTPStatusError(OpenAICompletionsProviderError):
    """Raised when OpenAI returns a non-success HTTP status."""

    provider_label = "OpenAI API"
    api_error_fields = (
        ApiErrorField("type", "api_error_type", sanitize=True, allow_int=False),
        ApiErrorField("code", "api_error_code", sanitize=True, allow_int=True),
    )


class OpenAICompletionsTransportError(OpenAICompletionsProviderError):
    """Raised when the HTTP request cannot reach OpenAI."""


class OpenAICompletionsResponseParseError(OpenAICompletionsProviderError):
    """Raised when the OpenAI Chat Completions response shape is unsupported."""

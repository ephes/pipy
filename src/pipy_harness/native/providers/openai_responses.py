"""OpenAI Responses API provider for the native pipy runtime."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native._provider_helpers import (
    failed_provider_result,
    serialize_tool_for_responses,
    utc_now,
)
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.deferred_tools import split_deferred_tools
from pipy_harness.native.http import (
    ApiErrorField,
    JsonHTTPClient,
    ProviderHTTPError,
    UrllibJsonHTTPClient,
)
from pipy_harness.native.http import (
    JsonResponse as JsonResponse,
)
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import StreamChunkSink, apply_provider_headers
from pipy_harness.native.providers.openai_responses_wire import (
    parse_response,
    responses_input,
)

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_NESTED_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("input_tokens_details", "cached_tokens"),
    ("output_tokens_details", "reasoning_tokens"),
)


def openai_http_client() -> UrllibJsonHTTPClient:
    """Build the shared JSON client wired with OpenAI Responses error types."""

    return UrllibJsonHTTPClient(
        provider_label="OpenAI API",
        status_error_class=OpenAIHTTPStatusError,
        transport_error_class=OpenAITransportError,
        parse_error_class=OpenAIResponseParseError,
    )


@dataclass(frozen=True, slots=True)
class OpenAIResponsesProvider:
    """OpenAI Responses API provider behind ProviderPort.

    Real adapter with `supports_tool_calls=True`. When
    `ProviderRequest.messages` is non-empty the provider serializes them
    into the Responses API `input` list (with `function_call` and
    `function_call_output` items) and declares `tools` from
    `available_tools`. Legacy single-turn callers leave `messages` empty
    and keep the previous string/list `input` body builder.
    """

    model_id: str
    api_key: str | None = field(
        default_factory=lambda: os.environ.get("OPENAI_API_KEY"), repr=False
    )
    http_client: JsonHTTPClient = field(default_factory=openai_http_client)
    endpoint: str = OPENAI_RESPONSES_URL
    timeout_seconds: float = 60.0
    supports_tool_calls: bool = True
    provider_name: str = "openai"
    # Catalog-resolved request config (parity with the completions adapter).
    # ``extra_headers`` are merged models.json/model headers (an explicit
    # Authorization wins over ``Bearer api_key``); ``reasoning_effort`` is the
    # mapped thinking value, placed in the Responses ``reasoning.effort`` key.
    extra_headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    reasoning_effort: str | None = None
    supports_tool_search: bool = False

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
        if not self.model_id:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenAIConfigurationError",
                error_message=f"--native-model is required for native provider {self.name}.",
            )
        has_explicit_authorization = any(
            header_name.lower() == "authorization" for header_name in self.extra_headers
        )
        if not self.api_key and not has_explicit_authorization:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenAIAuthError",
                error_message=(
                    "OpenAI API key is required in the environment for native "
                    f"provider {self.name}."
                ),
            )

        immediate_tools, deferred_tools = split_deferred_tools(
            request,
            enabled=self.supports_tool_search,
        )
        body: dict[str, Any] = {
            "model": self.model_id,
            "instructions": request.system_prompt,
            "input": responses_input(
                request,
                parse_error_class=OpenAIResponseParseError,
                deferred_tools={tool.name: tool for tool in deferred_tools},
                attach_images=True,
            ),
            "store": False,
        }
        if immediate_tools:
            body["tools"] = [
                serialize_tool_for_responses(tool) for tool in immediate_tools
            ]
        # Responses-native thinking: the mapped effort goes in ``reasoning.effort``.
        if self.reasoning_effort is not None:
            body["reasoning"] = {"effort": self.reasoning_effort}
        headers = {"Content-Type": "application/json"}
        # Merged models.json/model headers (may include an explicit Authorization).
        for header_name, header_value in self.extra_headers.items():
            headers[header_name] = header_value
        # Apply ``Bearer api_key`` only when no explicit Authorization is present.
        if self.api_key and not has_explicit_authorization:
            headers["Authorization"] = f"Bearer {self.api_key}"
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
                raise OpenAIHTTPStatusError(
                    f"OpenAI API request failed with HTTP status {response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = parse_response(
                response.body,
                parse_error_class=OpenAIResponseParseError,
                response_label="OpenAI",
                nested_usage_fields=OPENAI_NESTED_USAGE_FIELDS,
                tool_call_provider_prefix="openai",
            )
        except OpenAIProviderError as exc:
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
                "response_status": result.response_status,
            },
            tool_calls=result.tool_calls,
        )


class OpenAIProviderError(ProviderHTTPError):
    """Base class for sanitized OpenAI provider errors."""


class OpenAIHTTPStatusError(OpenAIProviderError):
    """Raised when OpenAI returns a non-success HTTP status."""

    provider_label = "OpenAI API"
    api_error_fields = (
        ApiErrorField("type", "api_error_type", sanitize=False, allow_int=False),
        ApiErrorField("code", "api_error_code", sanitize=False, allow_int=False),
    )


class OpenAITransportError(OpenAIProviderError):
    """Raised when the HTTP request cannot reach OpenAI."""


class OpenAIResponseParseError(OpenAIProviderError):
    """Raised when the OpenAI response shape is unsupported."""

"""Cloudflare Workers AI Chat Completions provider for the native pipy runtime."""

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

CLOUDFLARE_CHAT_COMPLETIONS_URL_TEMPLATE = (
    "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"
)
CLOUDFLARE_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("prompt_tokens", "input_tokens"),
    ("completion_tokens", "output_tokens"),
    ("total_tokens", "total_tokens"),
)


@dataclass(frozen=True, slots=True)
class _CloudflareRequestConfiguration:
    """Validated, single-read configuration for one completion request."""

    model_id: str
    url: str
    api_token: str
    has_explicit_authorization: bool
    extra_headers: tuple[tuple[str, str], ...]


def cloudflare_http_client() -> UrllibJsonHTTPClient:
    """Build the shared JSON client wired with Cloudflare Workers AI error types."""

    return UrllibJsonHTTPClient(
        provider_label="Cloudflare Workers AI",
        status_error_class=CloudflareHTTPStatusError,
        transport_error_class=CloudflareTransportError,
        parse_error_class=CloudflareResponseParseError,
    )


@dataclass(frozen=True, slots=True)
class CloudflareWorkersAIProvider:
    """Cloudflare Workers AI Chat Completions provider behind ProviderPort.

    Cloudflare Workers AI exposes an OpenAI-compatible Chat Completions API at
    ``/accounts/{account_id}/ai/v1/chat/completions``. When
    `ProviderRequest.messages` is non-empty the provider serializes them in the
    OpenAI chat completions format (with `tool_calls` and `tool` roles);
    otherwise it falls back to the legacy single-turn payload built from
    `system_prompt`/`user_prompt`.
    """

    model_id: str
    account_id: str | None = field(
        default_factory=lambda: os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    )
    api_token: str | None = field(
        default_factory=lambda: os.environ.get("CLOUDFLARE_API_TOKEN"), repr=False
    )
    http_client: JsonHTTPClient = field(default_factory=cloudflare_http_client)
    endpoint_template: str = CLOUDFLARE_CHAT_COMPLETIONS_URL_TEMPLATE
    timeout_seconds: float = 60.0
    supports_tool_calls: bool = True
    provider_name: str = "cloudflare"
    # Catalog-resolved request config. ``endpoint`` is the fully-resolved request
    # URL (account id already substituted into the catalog base_url); when set it
    # is used directly and the separate ``account_id`` env is not required.
    # ``extra_headers`` are merged models.json/model headers (an explicit
    # Authorization wins); ``reasoning_effort`` is the mapped thinking value
    # (Cloudflare's OpenAI-compatible top-level ``reasoning_effort``).
    endpoint: str | None = None
    extra_headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    reasoning_effort: str | None = None

    @property
    def name(self) -> str:
        return self.provider_name

    def _configuration_preflight(
        self,
        request: ProviderRequest,
        started_at: datetime,
    ) -> _CloudflareRequestConfiguration | ProviderResult:
        """Validate and resolve request configuration in failure-order."""

        model_id = self.model_id
        if not model_id or not model_id.strip():
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="CloudflareConfigurationError",
                error_message=f"--native-model is required for native provider {self.name}.",
            )

        # Catalog path: ``endpoint`` already has the account id substituted, so
        # the separate CLOUDFLARE_ACCOUNT_ID env is not required. Legacy path:
        # compose the URL from the account id env.
        endpoint = self.endpoint
        if endpoint:
            url = endpoint
        else:
            raw_account_id = self.account_id
            account_id = raw_account_id.strip() if raw_account_id is not None else ""
            if not account_id:
                return failed_provider_result(
                    request,
                    provider_name=self.name,
                    started_at=started_at,
                    error_type="CloudflareAuthError",
                    error_message=(
                        "Cloudflare account id is required in the environment "
                        f"(CLOUDFLARE_ACCOUNT_ID) for native provider {self.name}."
                    ),
                )
            endpoint_template = self.endpoint_template
            url = endpoint_template.format(account_id=account_id)

        raw_api_token = self.api_token
        api_token = raw_api_token.strip() if raw_api_token is not None else ""
        extra_headers = tuple(self.extra_headers.items())
        has_explicit_authorization = any(
            header_name.lower() == "authorization" for header_name, _ in extra_headers
        )
        if not api_token and not has_explicit_authorization:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="CloudflareAuthError",
                error_message=(
                    "Cloudflare API auth is required in the environment "
                    f"for native provider {self.name}."
                ),
            )
        return _CloudflareRequestConfiguration(
            model_id=model_id,
            url=url,
            api_token=api_token,
            has_explicit_authorization=has_explicit_authorization,
            extra_headers=extra_headers,
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
        for header_name, header_value in configuration.extra_headers:
            headers[header_name] = header_value
        # Apply ``Bearer api_token`` only when no explicit Authorization present.
        if configuration.api_token and not configuration.has_explicit_authorization:
            headers["Authorization"] = f"Bearer {configuration.api_token}"
        headers = apply_provider_headers(request, headers)

        try:
            response = self.http_client.post_json(
                configuration.url,
                headers=headers,
                body=body,
                timeout_seconds=self.timeout_seconds,
                cancel_token=cancel_token,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise CloudflareHTTPStatusError(
                    f"Cloudflare Workers AI request failed with HTTP status {response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = parse_response(
                response.body,
                parse_error_class=CloudflareResponseParseError,
                response_label="Cloudflare Workers AI",
                tool_call_provider_prefix="cloudflare",
                usage_fields=CLOUDFLARE_USAGE_FIELDS,
            )
        except CloudflareProviderError as exc:
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
            model_id=configuration.model_id,
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


class CloudflareProviderError(ProviderHTTPError):
    """Base class for sanitized Cloudflare Workers AI provider errors."""


class CloudflareHTTPStatusError(CloudflareProviderError):
    """Raised when Cloudflare Workers AI returns a non-success HTTP status."""

    provider_label = "Cloudflare Workers AI"
    api_error_fields = (
        ApiErrorField("code", "api_error_code", sanitize=True, allow_int=True),
    )


class CloudflareTransportError(CloudflareProviderError):
    """Raised when the HTTP request cannot reach Cloudflare Workers AI."""


class CloudflareResponseParseError(CloudflareProviderError):
    """Raised when the Cloudflare Workers AI response shape is unsupported."""

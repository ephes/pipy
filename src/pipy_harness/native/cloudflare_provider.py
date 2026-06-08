"""Cloudflare Workers AI Chat Completions provider for the native pipy runtime."""

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

CLOUDFLARE_CHAT_COMPLETIONS_URL_TEMPLATE = (
    "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"
)
CLOUDFLARE_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("prompt_tokens", "input_tokens"),
    ("completion_tokens", "output_tokens"),
    ("total_tokens", "total_tokens"),
)


@dataclass(frozen=True, slots=True)
class UrllibJsonHTTPClient:
    """Standard-library JSON client for Cloudflare Workers AI calls."""

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
            raise CloudflareHTTPStatusError.from_http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = sanitize_text(str(exc.reason)) if getattr(exc, "reason", None) else "request failed"
            raise CloudflareTransportError(
                f"Cloudflare Workers AI request failed: {reason}"
            ) from exc

        return JsonResponse(status_code=status_code, body=decode_json_object(payload, error_class=CloudflareResponseParseError, provider_label="Cloudflare Workers AI"))


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
    http_client: JsonHTTPClient = field(default_factory=UrllibJsonHTTPClient)
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
                error_type="CloudflareConfigurationError",
                error_message=f"--native-model is required for native provider {self.name}.",
            )
        # Catalog path: ``endpoint`` already has the account id substituted, so
        # the separate CLOUDFLARE_ACCOUNT_ID env is not required. Legacy path:
        # compose the URL from the account id env.
        if self.endpoint:
            url = self.endpoint
        else:
            account_id = self.account_id.strip() if self.account_id is not None else ""
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
            url = self.endpoint_template.format(account_id=account_id)
        api_token = self.api_token.strip() if self.api_token is not None else ""
        has_explicit_authorization = any(
            header_name.lower() == "authorization" for header_name in self.extra_headers
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

        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": _chat_messages(request),
        }
        if request.available_tools:
            body["tools"] = [
                serialize_tool_for_chat_completions(tool) for tool in request.available_tools
            ]
        if self.reasoning_effort is not None:
            body["reasoning_effort"] = self.reasoning_effort
        headers = {"Content-Type": "application/json"}
        # Merged models.json/model headers (may include an explicit Authorization).
        for header_name, header_value in self.extra_headers.items():
            headers[header_name] = header_value
        # Apply ``Bearer api_token`` only when no explicit Authorization present.
        if api_token and not has_explicit_authorization:
            headers["Authorization"] = f"Bearer {api_token}"

        try:
            response = self.http_client.post_json(
                url,
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
            result = _parse_response(response.body)
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
class ParsedCloudflareResponse:
    final_text: str | None
    usage: dict[str, int | float]
    response_object: str
    finish_reason: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


class CloudflareProviderError(Exception):
    """Base class for sanitized Cloudflare Workers AI provider errors."""

    def __init__(self, message: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        super().__init__(sanitize_text(message))
        self.metadata = dict(metadata or {})


class CloudflareHTTPStatusError(CloudflareProviderError):
    """Raised when Cloudflare Workers AI returns a non-success HTTP status."""

    @classmethod
    def from_http_error(cls, exc: urllib.error.HTTPError) -> CloudflareHTTPStatusError:
        metadata = safe_http_status_metadata(exc.code)
        try:
            body = decode_json_object(exc.read(), error_class=CloudflareResponseParseError, provider_label="Cloudflare Workers AI")
        except CloudflareResponseParseError:
            body = {}
        error = body.get("error")
        if isinstance(error, Mapping):
            error_code = error.get("code")
            if isinstance(error_code, str | int):
                metadata["api_error_code"] = sanitize_text(str(error_code))
        return cls(
            f"Cloudflare Workers AI request failed with HTTP status {exc.code}.",
            metadata=metadata,
        )


class CloudflareTransportError(CloudflareProviderError):
    """Raised when the HTTP request cannot reach Cloudflare Workers AI."""


class CloudflareResponseParseError(CloudflareProviderError):
    """Raised when the Cloudflare Workers AI response shape is unsupported."""


def _parse_response(body: Mapping[str, Any]) -> ParsedCloudflareResponse:
    error = body.get("error")
    if isinstance(error, Mapping):
        error_code = error.get("code")
        metadata: dict[str, Any] = {"provider_response_store_requested": False}
        if isinstance(error_code, str | int):
            metadata["api_error_code"] = sanitize_text(str(error_code))
        raise CloudflareResponseParseError(
            "Cloudflare Workers AI response included an error.", metadata=metadata
        )

    response_object = safe_response_label(body.get("object"), default="unknown")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise CloudflareResponseParseError(
            "Cloudflare Workers AI response did not include a completion choice.",
            metadata={
                "provider_response_store_requested": False,
                "response_object": response_object,
            },
        )

    first_choice = choices[0]
    if not isinstance(first_choice, Mapping):
        raise CloudflareResponseParseError(
            "Cloudflare Workers AI response included an unsupported completion choice.",
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
        provider_prefix="cloudflare",
    )
    if not final_text and not tool_calls:
        raise CloudflareResponseParseError(
            "Cloudflare Workers AI response did not include final message content or tool calls.",
            metadata={
                "provider_response_store_requested": False,
                "response_object": response_object,
                "finish_reason": finish_reason,
            },
        )

    return ParsedCloudflareResponse(
        final_text=final_text,
        usage=extract_usage_from_fields(body.get("usage"), CLOUDFLARE_USAGE_FIELDS),
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

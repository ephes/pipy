"""Cloudflare Workers AI Chat Completions provider for the native pipy runtime."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from pipy_harness.capture import sanitize_text
from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)
from pipy_harness.native.usage import normalize_provider_usage

CLOUDFLARE_CHAT_COMPLETIONS_URL_TEMPLATE = (
    "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"
)
CLOUDFLARE_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("prompt_tokens", "input_tokens"),
    ("completion_tokens", "output_tokens"),
    ("total_tokens", "total_tokens"),
)


@dataclass(frozen=True, slots=True)
class JsonResponse:
    """Small JSON response boundary used by provider tests."""

    status_code: int
    body: Mapping[str, Any]


class JsonHTTPClient(Protocol):
    """Minimal injectable JSON HTTP client."""

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
    ) -> JsonResponse:
        """POST JSON and return parsed JSON metadata."""


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
    ) -> JsonResponse:
        encoded = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=encoded,
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = response.read()
                status_code = response.getcode()
        except urllib.error.HTTPError as exc:
            raise CloudflareHTTPStatusError.from_http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = sanitize_text(str(exc.reason)) if getattr(exc, "reason", None) else "request failed"
            raise CloudflareTransportError(
                f"Cloudflare Workers AI request failed: {reason}"
            ) from exc

        return JsonResponse(status_code=status_code, body=_decode_json_object(payload))


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
        default_factory=lambda: os.environ.get("CLOUDFLARE_API_TOKEN")
    )
    http_client: JsonHTTPClient = field(default_factory=UrllibJsonHTTPClient)
    endpoint_template: str = CLOUDFLARE_CHAT_COMPLETIONS_URL_TEMPLATE
    timeout_seconds: float = 60.0
    supports_tool_calls: bool = True

    @property
    def name(self) -> str:
        return "cloudflare"

    def complete(self, request: ProviderRequest) -> ProviderResult:
        started_at = _utc_now()
        if not self.model_id or not self.model_id.strip():
            return _failed_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="CloudflareConfigurationError",
                error_message="--native-model is required for native provider cloudflare.",
            )
        account_id = self.account_id.strip() if self.account_id is not None else ""
        if not account_id:
            return _failed_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="CloudflareAuthError",
                error_message=(
                    "Cloudflare account id is required in the environment "
                    "(CLOUDFLARE_ACCOUNT_ID) for native provider cloudflare."
                ),
            )
        api_token = self.api_token.strip() if self.api_token is not None else ""
        if not api_token:
            return _failed_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="CloudflareAuthError",
                error_message=(
                    "Cloudflare API auth is required in the environment "
                    "for native provider cloudflare."
                ),
            )

        url = self.endpoint_template.format(account_id=account_id)
        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": _chat_messages(request),
        }
        if request.available_tools:
            body["tools"] = [
                _serialize_tool_for_cloudflare(tool) for tool in request.available_tools
            ]
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

        try:
            response = self.http_client.post_json(
                url,
                headers=headers,
                body=body,
                timeout_seconds=self.timeout_seconds,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise CloudflareHTTPStatusError(
                    f"Cloudflare Workers AI request failed with HTTP status {response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = _parse_response(response.body)
        except CloudflareProviderError as exc:
            return _failed_result(
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
            ended_at=_utc_now(),
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
        metadata = _safe_error_metadata(exc.code)
        try:
            body = _decode_json_object(exc.read())
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

    response_object = _safe_response_label(body.get("object"), default="unknown")
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
    finish_reason = _safe_response_label(first_choice.get("finish_reason"), default="unknown")
    message = first_choice.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    final_text = _extract_text_content(content)
    tool_calls = _extract_tool_calls(
        message.get("tool_calls") if isinstance(message, Mapping) else None
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
        usage=_extract_usage(body.get("usage")),
        response_object=response_object,
        finish_reason=finish_reason,
        tool_calls=tool_calls,
    )


def _extract_tool_calls(value: Any) -> tuple[ProviderToolCall, ...]:
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
            else f"cloudflare-tool-{index}"
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


def _chat_messages(request: ProviderRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": request.system_prompt}
    ]
    if request.messages:
        for envelope in request.messages:
            messages.append(_envelope_to_chat_message(envelope))
        return messages
    if request.no_tool_repl_context is not None:
        for exchange in request.no_tool_repl_context.exchanges:
            messages.append({"role": "user", "content": exchange.user_prompt})
            messages.append({"role": "assistant", "content": exchange.provider_final_text})
    messages.append({"role": "user", "content": request.user_prompt})
    return messages


def _envelope_to_chat_message(envelope: Any) -> dict[str, Any]:
    if isinstance(envelope, UserMessage):
        return {"role": "user", "content": envelope.content}
    if isinstance(envelope, AssistantMessage):
        message: dict[str, Any] = {"role": "assistant"}
        if envelope.content:
            message["content"] = envelope.content
        if envelope.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.provider_correlation_id,
                    "type": "function",
                    "function": {
                        "name": call.tool_name,
                        "arguments": call.arguments_json,
                    },
                }
                for call in envelope.tool_calls
            ]
        if "content" not in message:
            message["content"] = ""
        return message
    if isinstance(envelope, ToolResultMessage):
        return {
            "role": "tool",
            "tool_call_id": _require_provider_correlation_id(envelope),
            "content": envelope.output_text,
        }
    raise CloudflareResponseParseError(
        f"unsupported message envelope: {type(envelope).__name__}"
    )


def _serialize_tool_for_cloudflare(tool: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.input_schema),
        },
    }


def _require_provider_correlation_id(envelope: ToolResultMessage) -> str:
    if envelope.provider_correlation_id:
        return envelope.provider_correlation_id
    raise CloudflareResponseParseError(
        "ToolResultMessage is missing provider_correlation_id."
    )


def _extract_text_content(value: Any) -> str | None:
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


def _extract_usage(value: Any) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        return {}
    usage: dict[str, Any] = {}
    for provider_key, normalized_key in CLOUDFLARE_USAGE_FIELDS:
        usage[normalized_key] = value.get(provider_key)
    return normalize_provider_usage(usage)


def _decode_json_object(payload: bytes) -> Mapping[str, Any]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CloudflareResponseParseError(
            "Cloudflare Workers AI returned non-JSON response metadata."
        ) from exc
    if not isinstance(decoded, Mapping):
        raise CloudflareResponseParseError(
            "Cloudflare Workers AI returned unsupported JSON response metadata."
        )
    return decoded


def _safe_response_label(value: Any, *, default: str) -> str:
    if not isinstance(value, str) or not value:
        return default
    sanitized = sanitize_text(value)
    return sanitized if sanitized != "[REDACTED]" else default


def _safe_error_metadata(status_code: int) -> dict[str, Any]:
    return {"http_status": status_code}


def _failed_result(
    request: ProviderRequest,
    *,
    provider_name: str,
    started_at: datetime,
    error_type: str,
    error_message: str,
    metadata: Mapping[str, Any] | None = None,
) -> ProviderResult:
    return ProviderResult(
        status=HarnessStatus.FAILED,
        provider_name=provider_name,
        model_id=request.model_id,
        started_at=started_at,
        ended_at=_utc_now(),
        metadata=dict(metadata or {}),
        error_type=sanitize_text(error_type),
        error_message=sanitize_text(error_message),
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)

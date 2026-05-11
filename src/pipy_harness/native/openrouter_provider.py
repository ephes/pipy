"""OpenRouter Chat Completions provider for the native pipy runtime."""

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
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.usage import normalize_provider_usage

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("prompt_tokens", "input_tokens"),
    ("completion_tokens", "output_tokens"),
    ("total_tokens", "total_tokens"),
    ("cached_tokens", "cached_tokens"),
    ("reasoning_tokens", "reasoning_tokens"),
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
    """Standard-library JSON client for OpenRouter Chat Completions calls."""

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
            raise OpenRouterHTTPStatusError.from_http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = sanitize_text(str(exc.reason)) if getattr(exc, "reason", None) else "request failed"
            raise OpenRouterTransportError(f"OpenRouter API request failed: {reason}") from exc

        return JsonResponse(status_code=status_code, body=_decode_json_object(payload))


@dataclass(frozen=True, slots=True)
class OpenRouterChatCompletionsProvider:
    """One-turn OpenRouter Chat Completions provider behind ProviderPort."""

    model_id: str
    api_key: str | None = field(default_factory=lambda: os.environ.get("OPENROUTER_API_KEY"))
    http_client: JsonHTTPClient = field(default_factory=UrllibJsonHTTPClient)
    endpoint: str = OPENROUTER_CHAT_COMPLETIONS_URL
    timeout_seconds: float = 60.0

    @property
    def name(self) -> str:
        return "openrouter"

    def complete(self, request: ProviderRequest) -> ProviderResult:
        started_at = _utc_now()
        if not self.model_id or not self.model_id.strip():
            return _failed_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenRouterConfigurationError",
                error_message="--native-model is required for native provider openrouter.",
            )
        api_key = self.api_key.strip() if self.api_key is not None else ""
        if not api_key:
            return _failed_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenRouterAuthError",
                error_message=(
                    "OpenRouter API key is required in the environment for native provider openrouter."
                ),
            )

        body = {
            "model": self.model_id,
            "messages": _chat_messages(request),
            "stream": False,
        }
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
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise OpenRouterHTTPStatusError(
                    f"OpenRouter API request failed with HTTP status {response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = _parse_response(response.body)
        except OpenRouterProviderError as exc:
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
        )


@dataclass(frozen=True, slots=True)
class ParsedOpenRouterResponse:
    final_text: str
    usage: dict[str, int | float]
    response_object: str
    finish_reason: str


class OpenRouterProviderError(Exception):
    """Base class for sanitized OpenRouter provider errors."""

    def __init__(self, message: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        super().__init__(sanitize_text(message))
        self.metadata = dict(metadata or {})


class OpenRouterHTTPStatusError(OpenRouterProviderError):
    """Raised when OpenRouter returns a non-success HTTP status."""

    @classmethod
    def from_http_error(cls, exc: urllib.error.HTTPError) -> OpenRouterHTTPStatusError:
        metadata = _safe_error_metadata(exc.code)
        try:
            body = _decode_json_object(exc.read())
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

    response_object = _safe_response_label(body.get("object"), default="unknown")
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
    finish_reason = _safe_response_label(first_choice.get("finish_reason"), default="unknown")
    message = first_choice.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    final_text = _extract_text_content(content)
    if not final_text:
        raise OpenRouterResponseParseError(
            "OpenRouter response did not include final message content.",
            metadata={
                "provider_response_store_requested": False,
                "response_object": response_object,
                "finish_reason": finish_reason,
            },
        )

    return ParsedOpenRouterResponse(
        final_text=final_text,
        usage=_extract_usage(body.get("usage")),
        response_object=response_object,
        finish_reason=finish_reason,
    )


def _chat_messages(request: ProviderRequest) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": request.system_prompt}]
    if request.no_tool_repl_context is not None:
        for exchange in request.no_tool_repl_context.exchanges:
            messages.append({"role": "user", "content": exchange.user_prompt})
            messages.append({"role": "assistant", "content": exchange.provider_final_text})
    messages.append({"role": "user", "content": request.user_prompt})
    return messages


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
    for provider_key, normalized_key in OPENROUTER_USAGE_FIELDS:
        usage[normalized_key] = value.get(provider_key)
    return normalize_provider_usage(usage)


def _decode_json_object(payload: bytes) -> Mapping[str, Any]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenRouterResponseParseError("OpenRouter API returned non-JSON response metadata.") from exc
    if not isinstance(decoded, Mapping):
        raise OpenRouterResponseParseError("OpenRouter API returned unsupported JSON response metadata.")
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

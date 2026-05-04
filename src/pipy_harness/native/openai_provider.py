"""OpenAI Responses API provider for the native pipy runtime."""

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

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


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
    """Standard-library JSON client for OpenAI Responses calls."""

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
            raise OpenAIHTTPStatusError.from_http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = sanitize_text(str(exc.reason)) if getattr(exc, "reason", None) else "request failed"
            raise OpenAITransportError(f"OpenAI API request failed: {reason}") from exc

        return JsonResponse(status_code=status_code, body=_decode_json_object(payload))


@dataclass(frozen=True, slots=True)
class OpenAIResponsesProvider:
    """One-turn OpenAI Responses API provider behind ProviderPort."""

    model_id: str
    api_key: str | None = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY"))
    http_client: JsonHTTPClient = field(default_factory=UrllibJsonHTTPClient)
    endpoint: str = OPENAI_RESPONSES_URL
    timeout_seconds: float = 60.0

    @property
    def name(self) -> str:
        return "openai"

    def complete(self, request: ProviderRequest) -> ProviderResult:
        started_at = _utc_now()
        if not self.model_id:
            return _failed_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenAIConfigurationError",
                error_message="--native-model is required for native provider openai.",
            )
        if not self.api_key:
            return _failed_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenAIAuthError",
                error_message="OpenAI API key is required in the environment for native provider openai.",
            )

        body = {
            "model": self.model_id,
            "instructions": request.system_prompt,
            "input": request.user_prompt,
            "store": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
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
                raise OpenAIHTTPStatusError(
                    f"OpenAI API request failed with HTTP status {response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = _parse_response(response.body)
        except OpenAIProviderError as exc:
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
                "response_status": result.response_status,
            },
        )


@dataclass(frozen=True, slots=True)
class ParsedOpenAIResponse:
    final_text: str
    usage: dict[str, int | float]
    response_status: str


class OpenAIProviderError(Exception):
    """Base class for sanitized OpenAI provider errors."""

    def __init__(self, message: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        super().__init__(sanitize_text(message))
        self.metadata = dict(metadata or {})


class OpenAIHTTPStatusError(OpenAIProviderError):
    """Raised when OpenAI returns a non-success HTTP status."""

    @classmethod
    def from_http_error(cls, exc: urllib.error.HTTPError) -> OpenAIHTTPStatusError:
        metadata: dict[str, Any] = {"http_status": exc.code}
        try:
            body = _decode_json_object(exc.read())
        except OpenAIResponseParseError:
            body = {}
        error = body.get("error")
        if isinstance(error, Mapping):
            error_type = error.get("type")
            error_code = error.get("code")
            if isinstance(error_type, str):
                metadata["api_error_type"] = error_type
            if isinstance(error_code, str):
                metadata["api_error_code"] = error_code
        return cls(f"OpenAI API request failed with HTTP status {exc.code}.", metadata=metadata)


class OpenAITransportError(OpenAIProviderError):
    """Raised when the HTTP request cannot reach OpenAI."""


class OpenAIResponseParseError(OpenAIProviderError):
    """Raised when the OpenAI response shape is unsupported."""


def _parse_response(body: Mapping[str, Any]) -> ParsedOpenAIResponse:
    status = body.get("status")
    response_status = sanitize_text(status) if isinstance(status, str) else "unknown"
    if response_status and response_status != "completed":
        raise OpenAIResponseParseError(
            f"OpenAI response status was {response_status}.",
            metadata={
                "provider_response_store_requested": False,
                "response_status": response_status,
            },
        )

    final_text = _extract_final_text(body)
    if not final_text:
        raise OpenAIResponseParseError(
            "OpenAI response did not include final output text.",
            metadata={
                "provider_response_store_requested": False,
                "response_status": response_status,
            },
        )

    return ParsedOpenAIResponse(
        final_text=final_text,
        usage=_extract_usage(body.get("usage")),
        response_status=response_status,
    )


def _extract_final_text(body: Mapping[str, Any]) -> str | None:
    output_text = body.get("output_text")
    if isinstance(output_text, str):
        return output_text

    output = body.get("output")
    if not isinstance(output, list):
        return None

    chunks: list[str] = []
    for item in output:
        if not isinstance(item, Mapping):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for content_item in content:
            if not isinstance(content_item, Mapping):
                continue
            if content_item.get("type") == "output_text" and isinstance(content_item.get("text"), str):
                chunks.append(content_item["text"])
    if not chunks:
        return None
    return "".join(chunks)


def _extract_usage(value: Any) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        return {}
    usage: dict[str, int | float] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        item = value.get(key)
        if isinstance(item, int | float):
            usage[key] = item

    input_details = value.get("input_tokens_details")
    if isinstance(input_details, Mapping):
        cached_tokens = input_details.get("cached_tokens")
        if isinstance(cached_tokens, int | float):
            usage["cached_tokens"] = cached_tokens

    output_details = value.get("output_tokens_details")
    if isinstance(output_details, Mapping):
        reasoning_tokens = output_details.get("reasoning_tokens")
        if isinstance(reasoning_tokens, int | float):
            usage["reasoning_tokens"] = reasoning_tokens

    return usage


def _decode_json_object(payload: bytes) -> Mapping[str, Any]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenAIResponseParseError("OpenAI API returned non-JSON response metadata.") from exc
    if not isinstance(decoded, Mapping):
        raise OpenAIResponseParseError("OpenAI API returned unsupported JSON response metadata.")
    return decoded


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

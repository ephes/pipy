"""Google Vertex AI provider for the native pipy runtime.

This provider targets Gemini models served via Google Cloud's Vertex AI
``generateContent`` endpoint. The request/response body shape is the same as
the Google Generative AI surface (see ``google_provider.py``) because both
front the same Gemini models. The two providers differ in:

- **Endpoint**: Vertex uses
  ``https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/``
  ``locations/{location}/publishers/google/models/{model_id}:generateContent``
  whereas the generative-language surface uses an API key in the URL.
- **Auth**: Vertex requires an OAuth2 Bearer access token in the
  ``Authorization`` header. The production path obtains the token by signing
  a Google service-account JWT (RS256) and exchanging it at
  ``https://oauth2.googleapis.com/token``. Implementing RS256 in pure stdlib
  requires hand-rolled ASN.1 parsing of PKCS#8 private keys, which the
  parity track's "no new runtime dependencies" invariant rules out for this
  slice. Instead this provider accepts a pre-obtained access token via the
  ``GOOGLE_ACCESS_TOKEN`` environment variable. Callers can produce one with
  ``gcloud auth print-access-token`` or any other external means. Native
  JWT/service-account auth is left as a future extension.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
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

GOOGLE_VERTEX_ENDPOINT_TEMPLATE = (
    "https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/"
    "locations/{location}/publishers/google/models/{model_id}:generateContent"
)
GOOGLE_VERTEX_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("promptTokenCount", "input_tokens"),
    ("candidatesTokenCount", "output_tokens"),
    ("totalTokenCount", "total_tokens"),
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
    """Standard-library JSON client for Vertex AI ``generateContent`` calls."""

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
            raise GoogleVertexHTTPStatusError.from_http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = (
                sanitize_text(str(exc.reason))
                if getattr(exc, "reason", None)
                else "request failed"
            )
            raise GoogleVertexTransportError(
                f"Google Vertex AI request failed: {reason}"
            ) from exc

        return JsonResponse(status_code=status_code, body=_decode_json_object(payload))


@dataclass(frozen=True, slots=True)
class GoogleVertexProvider:
    """Google Vertex AI ``generateContent`` provider behind ProviderPort.

    Targets Gemini models on Vertex AI. The request/response body shape is the
    same as the Google Generative AI surface, but the endpoint is built from
    ``project_id``, ``location``, and ``model_id``, and auth is an OAuth2
    Bearer access token (not an API key embedded in the URL).

    For PIPY this provider accepts a pre-obtained access token via the
    ``GOOGLE_ACCESS_TOKEN`` environment variable to keep within the parity
    track's "no new runtime dependencies" invariant; native service-account
    JWT signing is a future extension. The access token is never logged or
    archived; only sanitized metadata leaves the provider boundary.
    """

    model_id: str
    project_id: str | None = field(
        default_factory=lambda: os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GOOGLE_PROJECT_ID")
    )
    location: str = field(
        default_factory=lambda: os.environ.get("GOOGLE_CLOUD_LOCATION")
        or "us-central1"
    )
    access_token: str | None = field(
        default_factory=lambda: os.environ.get("GOOGLE_ACCESS_TOKEN")
    )
    http_client: JsonHTTPClient = field(default_factory=UrllibJsonHTTPClient)
    endpoint_template: str = GOOGLE_VERTEX_ENDPOINT_TEMPLATE
    timeout_seconds: float = 60.0
    supports_tool_calls: bool = True

    @property
    def name(self) -> str:
        return "google-vertex"

    def complete(self, request: ProviderRequest) -> ProviderResult:
        started_at = _utc_now()
        if not self.model_id or not self.model_id.strip():
            return _failed_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="GoogleVertexConfigurationError",
                error_message=(
                    "--native-model is required for native provider google-vertex."
                ),
            )
        project_id = self.project_id.strip() if self.project_id is not None else ""
        if not project_id:
            return _failed_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="GoogleVertexConfigurationError",
                error_message=(
                    "Google Cloud project id is required in the environment "
                    "for native provider google-vertex."
                ),
            )
        access_token = (
            self.access_token.strip() if self.access_token is not None else ""
        )
        if not access_token:
            return _failed_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="GoogleVertexAuthError",
                error_message=(
                    "Google Vertex AI bearer access value must be set in "
                    "the environment for native provider google-vertex."
                ),
            )
        location = self.location.strip() if self.location else ""
        if not location:
            return _failed_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="GoogleVertexConfigurationError",
                error_message=(
                    "Google Cloud location is required for native provider "
                    "google-vertex."
                ),
            )

        url = self.endpoint_template.format(
            location=urllib.parse.quote(location, safe=""),
            project_id=urllib.parse.quote(project_id, safe=""),
            model_id=urllib.parse.quote(self.model_id, safe=""),
        )
        body: dict[str, Any] = {
            "contents": _gemini_contents(request),
        }
        if request.system_prompt:
            body["systemInstruction"] = {
                "parts": [{"text": request.system_prompt}],
            }
        if request.available_tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        _serialize_tool_for_gemini(tool)
                        for tool in request.available_tools
                    ],
                }
            ]
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        }

        try:
            response = self.http_client.post_json(
                url,
                headers=headers,
                body=body,
                timeout_seconds=self.timeout_seconds,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise GoogleVertexHTTPStatusError(
                    "Google Vertex AI request failed with HTTP status "
                    f"{response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = _parse_response(response.body)
        except GoogleVertexProviderError as exc:
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
                "finish_reason": result.finish_reason,
                "google_cloud_location": location,
            },
            tool_calls=result.tool_calls,
        )


@dataclass(frozen=True, slots=True)
class ParsedGoogleVertexResponse:
    final_text: str | None
    usage: dict[str, int | float]
    finish_reason: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


class GoogleVertexProviderError(Exception):
    """Base class for sanitized Google Vertex AI provider errors."""

    def __init__(
        self, message: str, *, metadata: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(sanitize_text(message))
        self.metadata = dict(metadata or {})


class GoogleVertexHTTPStatusError(GoogleVertexProviderError):
    """Raised when Vertex AI returns a non-success HTTP status."""

    @classmethod
    def from_http_error(
        cls, exc: urllib.error.HTTPError
    ) -> GoogleVertexHTTPStatusError:
        metadata: dict[str, Any] = {"http_status": exc.code}
        try:
            body = _decode_json_object(exc.read())
        except GoogleVertexResponseParseError:
            body = {}
        error = body.get("error")
        if isinstance(error, Mapping):
            error_status = error.get("status")
            error_code = error.get("code")
            if isinstance(error_status, str):
                metadata["api_error_status"] = sanitize_text(error_status)
            if isinstance(error_code, str | int):
                metadata["api_error_code"] = sanitize_text(str(error_code))
        return cls(
            f"Google Vertex AI request failed with HTTP status {exc.code}.",
            metadata=metadata,
        )


class GoogleVertexTransportError(GoogleVertexProviderError):
    """Raised when the HTTP request cannot reach Vertex AI."""


class GoogleVertexResponseParseError(GoogleVertexProviderError):
    """Raised when the Vertex AI response shape is unsupported."""


def _gemini_contents(request: ProviderRequest) -> list[dict[str, Any]]:
    """Build Gemini ``contents`` from a ProviderRequest.

    Mirrors ``google_provider._gemini_contents``: when ``request.messages``
    is non-empty translate the envelope; otherwise fall back to
    ``no_tool_repl_context`` (if any) followed by the current
    ``user_prompt``. The previous AssistantMessage values are passed in so
    ``ToolResultMessage`` can recover the original tool name from the
    matching ``provider_correlation_id`` (Gemini's ``functionResponse`` part
    needs the tool name, but pipy's ``ToolResultMessage`` only carries the
    pipy-owned ``tool_request_id`` and the opaque
    ``provider_correlation_id``).
    """

    contents: list[dict[str, Any]] = []
    if request.messages:
        prior_assistants: list[AssistantMessage] = []
        for envelope in request.messages:
            contents.append(_envelope_to_content(envelope, prior_assistants))
            if isinstance(envelope, AssistantMessage):
                prior_assistants.append(envelope)
        return contents
    if request.no_tool_repl_context is not None:
        for exchange in request.no_tool_repl_context.exchanges:
            contents.append(
                {
                    "role": "user",
                    "parts": [{"text": exchange.user_prompt}],
                }
            )
            contents.append(
                {
                    "role": "model",
                    "parts": [{"text": exchange.provider_final_text}],
                }
            )
    contents.append(
        {"role": "user", "parts": [{"text": request.user_prompt}]}
    )
    return contents


def _envelope_to_content(
    envelope: Any,
    prior_assistants: list[AssistantMessage],
) -> dict[str, Any]:
    """Translate one LoopMessage into a Gemini ``contents`` entry."""

    if isinstance(envelope, UserMessage):
        return {"role": "user", "parts": [{"text": envelope.content}]}
    if isinstance(envelope, AssistantMessage):
        parts: list[dict[str, Any]] = []
        if envelope.content:
            parts.append({"text": envelope.content})
        for call in envelope.tool_calls:
            try:
                parsed_args: Any = (
                    json.loads(call.arguments_json) if call.arguments_json else {}
                )
            except json.JSONDecodeError:
                parsed_args = {}
            if not isinstance(parsed_args, Mapping):
                parsed_args = {}
            parts.append(
                {
                    "functionCall": {
                        "name": call.tool_name,
                        "args": dict(parsed_args),
                    }
                }
            )
        if not parts:
            parts.append({"text": ""})
        return {"role": "model", "parts": parts}
    if isinstance(envelope, ToolResultMessage):
        # Gemini's functionResponse requires the original tool name. pipy's
        # ToolResultMessage only carries `tool_request_id` and
        # `provider_correlation_id`, so we recover the name from the matching
        # AssistantMessage tool call. If no match is found we fall back to
        # "unknown_tool" rather than failing the loop.
        tool_name = _lookup_tool_name(envelope, prior_assistants) or "unknown_tool"
        return {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": tool_name,
                        "response": {"result": envelope.output_text},
                    }
                }
            ],
        }
    raise GoogleVertexResponseParseError(
        f"unsupported message envelope: {type(envelope).__name__}"
    )


def _lookup_tool_name(
    envelope: ToolResultMessage,
    prior_assistants: list[AssistantMessage],
) -> str | None:
    correlation_id = envelope.provider_correlation_id
    if not correlation_id:
        return None
    for assistant in prior_assistants:
        for call in assistant.tool_calls:
            if call.provider_correlation_id == correlation_id:
                return call.tool_name
    return None


def _serialize_tool_for_gemini(tool: Any) -> dict[str, Any]:
    """Translate a ``ToolDefinition`` into the Gemini function declaration shape."""

    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": dict(tool.input_schema),
    }


def _parse_response(body: Mapping[str, Any]) -> ParsedGoogleVertexResponse:
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise GoogleVertexResponseParseError(
            "Google Vertex AI response did not include a candidate.",
            metadata={"provider_response_store_requested": False},
        )
    first_candidate = candidates[0]
    if not isinstance(first_candidate, Mapping):
        raise GoogleVertexResponseParseError(
            "Google Vertex AI response included an unsupported candidate.",
            metadata={"provider_response_store_requested": False},
        )

    finish_reason = _safe_response_label(
        first_candidate.get("finishReason"), default="unknown"
    )
    content = first_candidate.get("content")
    parts = content.get("parts") if isinstance(content, Mapping) else None

    final_text = _extract_final_text(parts)
    tool_calls = _extract_tool_calls(parts)

    if not final_text and not tool_calls:
        raise GoogleVertexResponseParseError(
            "Google Vertex AI response did not include final output text or tool calls.",
            metadata={
                "provider_response_store_requested": False,
                "finish_reason": finish_reason,
            },
        )

    return ParsedGoogleVertexResponse(
        final_text=final_text,
        usage=_extract_usage(body.get("usageMetadata")),
        finish_reason=finish_reason,
        tool_calls=tool_calls,
    )


def _extract_final_text(parts: Any) -> str | None:
    if not isinstance(parts, list):
        return None
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            chunks.append(text)
    if not chunks:
        return None
    return "".join(chunks)


def _extract_tool_calls(parts: Any) -> tuple[ProviderToolCall, ...]:
    """Parse Gemini ``functionCall`` parts into ProviderToolCall values."""

    if not isinstance(parts, list):
        return ()
    calls: list[ProviderToolCall] = []
    for index, part in enumerate(parts):
        if not isinstance(part, Mapping):
            continue
        function_call = part.get("functionCall")
        if not isinstance(function_call, Mapping):
            continue
        name = function_call.get("name")
        args = function_call.get("args")
        if not isinstance(name, str) or not name:
            continue
        if isinstance(args, Mapping):
            arguments_json = json.dumps(dict(args), sort_keys=True)
        elif isinstance(args, str):
            arguments_json = args
        else:
            arguments_json = "{}"
        correlation = f"google-vertex-tool-{index}"
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


def _extract_usage(value: Any) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        return {}
    usage: dict[str, Any] = {}
    for provider_key, normalized_key in GOOGLE_VERTEX_USAGE_FIELDS:
        usage[normalized_key] = value.get(provider_key)
    return normalize_provider_usage(usage)


def _decode_json_object(payload: bytes) -> Mapping[str, Any]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoogleVertexResponseParseError(
            "Google Vertex AI returned non-JSON response metadata."
        ) from exc
    if not isinstance(decoded, Mapping):
        raise GoogleVertexResponseParseError(
            "Google Vertex AI returned unsupported JSON response metadata."
        )
    return decoded


def _safe_response_label(value: Any, *, default: str) -> str:
    if not isinstance(value, str) or not value:
        return default
    sanitized = sanitize_text(value)
    return sanitized if sanitized != "[REDACTED]" else default


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

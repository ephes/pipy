"""Google Gemini Generative AI provider for the native pipy runtime."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pipy_harness.capture import sanitize_text
from pipy_harness.native._provider_helpers import utc_now, safe_response_label, failed_provider_result, JsonResponse, JsonHTTPClient, extract_usage_from_fields, decode_json_object
from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)

GOOGLE_GENERATIVE_AI_ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
)
GOOGLE_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("promptTokenCount", "input_tokens"),
    ("candidatesTokenCount", "output_tokens"),
    ("totalTokenCount", "total_tokens"),
)


@dataclass(frozen=True, slots=True)
class UrllibJsonHTTPClient:
    """Standard-library JSON client for Google Generative AI calls."""

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
            raise GoogleHTTPStatusError.from_http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = sanitize_text(str(exc.reason)) if getattr(exc, "reason", None) else "request failed"
            raise GoogleTransportError(f"Google API request failed: {reason}") from exc

        return JsonResponse(status_code=status_code, body=decode_json_object(payload, error_class=GoogleResponseParseError, provider_label="Google API"))


@dataclass(frozen=True, slots=True)
class GoogleGenerativeAIProvider:
    """Google Gemini Generative AI provider behind ProviderPort.

    Real adapter with `supports_tool_calls=True`. When
    `ProviderRequest.messages` is non-empty the provider serializes them
    into the Gemini `contents` list (with `functionCall` and
    `functionResponse` parts) and declares `tools` from
    `available_tools`. Legacy single-turn callers leave `messages` empty
    and the provider falls back to the `user_prompt`/no-tool REPL context.

    Authentication uses Google's URL-embedded API key style
    (`?key=...`). No `Authorization` header is sent. The key is never
    logged or archived; only sanitized metadata leaves the provider
    boundary.
    """

    model_id: str
    api_key: str | None = field(
        default_factory=lambda: os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
    )
    http_client: JsonHTTPClient = field(default_factory=UrllibJsonHTTPClient)
    endpoint_template: str = GOOGLE_GENERATIVE_AI_ENDPOINT_TEMPLATE
    timeout_seconds: float = 60.0
    supports_tool_calls: bool = True

    @property
    def name(self) -> str:
        return "google"

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
    ) -> ProviderResult:
        del stream_sink, reasoning_sink
        started_at = utc_now()
        if not self.model_id or not self.model_id.strip():
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="GoogleConfigurationError",
                error_message="--native-model is required for native provider google.",
            )
        api_key = self.api_key.strip() if self.api_key is not None else ""
        if not api_key:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="GoogleAuthError",
                error_message=(
                    "Google API key is required in the environment for native provider google."
                ),
            )

        url = self.endpoint_template.format(model=self.model_id, key=api_key)
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
        headers = {"Content-Type": "application/json"}

        try:
            response = self.http_client.post_json(
                url,
                headers=headers,
                body=body,
                timeout_seconds=self.timeout_seconds,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise GoogleHTTPStatusError(
                    f"Google API request failed with HTTP status {response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = _parse_response(response.body)
        except GoogleProviderError as exc:
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
                "finish_reason": result.finish_reason,
            },
            tool_calls=result.tool_calls,
        )


@dataclass(frozen=True, slots=True)
class ParsedGoogleResponse:
    final_text: str | None
    usage: dict[str, int | float]
    finish_reason: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


class GoogleProviderError(Exception):
    """Base class for sanitized Google provider errors."""

    def __init__(self, message: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        super().__init__(sanitize_text(message))
        self.metadata = dict(metadata or {})


class GoogleHTTPStatusError(GoogleProviderError):
    """Raised when Google returns a non-success HTTP status."""

    @classmethod
    def from_http_error(cls, exc: urllib.error.HTTPError) -> GoogleHTTPStatusError:
        metadata: dict[str, Any] = {"http_status": exc.code}
        try:
            body = decode_json_object(exc.read(), error_class=GoogleResponseParseError, provider_label="Google API")
        except GoogleResponseParseError:
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
            f"Google API request failed with HTTP status {exc.code}.",
            metadata=metadata,
        )


class GoogleTransportError(GoogleProviderError):
    """Raised when the HTTP request cannot reach Google."""


class GoogleResponseParseError(GoogleProviderError):
    """Raised when the Google response shape is unsupported."""


def _gemini_contents(request: ProviderRequest) -> list[dict[str, Any]]:
    """Build Gemini `contents` from a ProviderRequest.

    When `request.messages` is non-empty, translate the envelope. Otherwise
    fall back to `no_tool_repl_context` (if any) followed by the current
    `user_prompt`. The previous AssistantMessage values are passed in so
    `ToolResultMessage` can recover the original tool name from the
    matching `provider_correlation_id` (Gemini's `functionResponse` part
    needs the tool name, but pipy's `ToolResultMessage` only carries the
    pipy-owned `tool_request_id` and the opaque
    `provider_correlation_id`).
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
    """Translate one LoopMessage into a Gemini `contents` entry."""

    if isinstance(envelope, UserMessage):
        return {"role": "user", "parts": [{"text": envelope.content}]}
    if isinstance(envelope, AssistantMessage):
        parts: list[dict[str, Any]] = []
        if envelope.content:
            parts.append({"text": envelope.content})
        for call in envelope.tool_calls:
            try:
                parsed_args = json.loads(call.arguments_json) if call.arguments_json else {}
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
        # "unknown_tool" rather than failing the loop: the provider will
        # still receive a well-formed functionResponse part and the loop's
        # observation layer surfaces the mismatch separately.
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
    raise GoogleResponseParseError(
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
    """Translate a `ToolDefinition` into the Gemini function declaration shape."""

    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": dict(tool.input_schema),
    }


def _parse_response(body: Mapping[str, Any]) -> ParsedGoogleResponse:
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise GoogleResponseParseError(
            "Google response did not include a candidate.",
            metadata={"provider_response_store_requested": False},
        )
    first_candidate = candidates[0]
    if not isinstance(first_candidate, Mapping):
        raise GoogleResponseParseError(
            "Google response included an unsupported candidate.",
            metadata={"provider_response_store_requested": False},
        )

    finish_reason = safe_response_label(
        first_candidate.get("finishReason"), default="unknown"
    )
    content = first_candidate.get("content")
    parts = content.get("parts") if isinstance(content, Mapping) else None

    final_text = _extract_final_text(parts)
    tool_calls = _extract_tool_calls(parts)

    if not final_text and not tool_calls:
        raise GoogleResponseParseError(
            "Google response did not include final output text or tool calls.",
            metadata={
                "provider_response_store_requested": False,
                "finish_reason": finish_reason,
            },
        )

    return ParsedGoogleResponse(
        final_text=final_text,
        usage=extract_usage_from_fields(body.get("usageMetadata"), GOOGLE_USAGE_FIELDS),
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
    """Parse Gemini `functionCall` parts into ProviderToolCall values."""

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
        correlation = f"google-tool-{index}"
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

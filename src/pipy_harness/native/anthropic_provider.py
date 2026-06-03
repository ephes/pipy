"""Anthropic Messages API provider for the native pipy runtime."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pipy_harness.capture import sanitize_text
from pipy_harness.native._provider_helpers import utc_now, failed_provider_result, JsonResponse, JsonHTTPClient, serialize_tool_for_anthropic, decode_json_object
from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)
from pipy_harness.native.usage import NORMALIZED_PROVIDER_USAGE_KEYS, normalize_provider_usage

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_DEFAULT_MAX_TOKENS = 4096
# Default per-effort thinking token budgets (Pi's amazon-bedrock.ts default
# budgets, the universally-valid ``budget_tokens`` path). Claude's budget path
# has no xhigh, so Pi clamps xhigh down to high (simple-options.ts); we match.
ANTHROPIC_THINKING_BUDGETS: dict[str, int] = {
    "minimal": 1024,
    "low": 2048,
    "medium": 8192,
    "high": 16384,
    "xhigh": 16384,
}
ANTHROPIC_DEFAULT_THINKING_BUDGET = 16384
ANTHROPIC_USAGE_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("input_tokens", "input_tokens"),
    ("output_tokens", "output_tokens"),
    ("cache_creation_input_tokens", "cached_tokens"),
    ("cache_read_input_tokens", "cached_tokens"),
)


@dataclass(frozen=True, slots=True)
class UrllibJsonHTTPClient:
    """Standard-library JSON client for Anthropic Messages calls."""

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
            raise AnthropicHTTPStatusError.from_http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = (
                sanitize_text(str(exc.reason))
                if getattr(exc, "reason", None)
                else "request failed"
            )
            raise AnthropicTransportError(
                f"Anthropic API request failed: {reason}"
            ) from exc

        return JsonResponse(status_code=status_code, body=decode_json_object(payload, error_class=AnthropicResponseParseError, provider_label="Anthropic API"))


@dataclass(frozen=True, slots=True)
class AnthropicProvider:
    """Anthropic Messages API provider behind ProviderPort.

    Real adapter with `supports_tool_calls=True`. When
    `ProviderRequest.messages` is non-empty the provider serializes them into
    Anthropic's `messages` list (with `tool_use` and `tool_result` blocks).
    Legacy single-turn callers leave `messages` empty and get a single user
    turn carrying `request.user_prompt`.
    """

    model_id: str
    # ``repr=False`` on credential-bearing fields so a stray repr/log never
    # leaks the api key or auth headers.
    api_key: str | None = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY"), repr=False
    )
    http_client: JsonHTTPClient = field(default_factory=UrllibJsonHTTPClient)
    endpoint: str = ANTHROPIC_MESSAGES_URL
    timeout_seconds: float = 60.0
    supports_tool_calls: bool = True
    anthropic_version: str = "2023-06-01"
    max_tokens: int = ANTHROPIC_DEFAULT_MAX_TOKENS
    provider_name: str = "anthropic"
    # Catalog-resolved request config (parity with the completions adapter).
    # ``extra_headers`` are merged models.json/model headers (an explicit
    # Authorization wins over the native ``x-api-key``); ``reasoning_effort`` is
    # the mapped thinking value, placed in Anthropic's native ``thinking`` key.
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
    ) -> ProviderResult:
        del stream_sink, reasoning_sink
        started_at = utc_now()
        if not self.model_id:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="AnthropicConfigurationError",
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
                error_type="AnthropicAuthError",
                error_message=(
                    "Anthropic API key is required in the environment for native "
                    f"provider {self.name}."
                ),
            )

        body: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self.max_tokens,
            "system": request.system_prompt,
            "messages": _messages_payload(request),
        }
        if request.available_tools:
            body["tools"] = [
                serialize_tool_for_anthropic(tool)
                for tool in request.available_tools
            ]
        # Anthropic-native thinking: the mapped effort maps to a token budget
        # (Pi's universally-valid ``type: enabled``/``budget_tokens`` path with
        # Pi's default per-level budgets). The adaptive ``output_config`` path is
        # model-gated and tracked as a follow-on.
        if self.reasoning_effort is not None:
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": ANTHROPIC_THINKING_BUDGETS.get(
                    self.reasoning_effort, ANTHROPIC_DEFAULT_THINKING_BUDGET
                ),
            }
        headers = {
            "anthropic-version": self.anthropic_version,
            "Content-Type": "application/json",
        }
        # Merged models.json/model headers (may include an explicit Authorization).
        for header_name, header_value in self.extra_headers.items():
            headers[header_name] = header_value
        # Apply the native ``x-api-key`` only when no explicit Authorization
        # header is present, so an explicit models.json auth header wins.
        if self.api_key and not has_explicit_authorization:
            headers["x-api-key"] = self.api_key

        try:
            response = self.http_client.post_json(
                self.endpoint,
                headers=headers,
                body=body,
                timeout_seconds=self.timeout_seconds,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise AnthropicHTTPStatusError(
                    f"Anthropic API request failed with HTTP status {response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = _parse_response(response.body)
        except AnthropicProviderError as exc:
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
                "stop_reason": result.stop_reason,
            },
            tool_calls=result.tool_calls,
        )


def _messages_payload(request: ProviderRequest) -> list[dict[str, object]]:
    if request.messages:
        items: list[dict[str, object]] = []
        for envelope in request.messages:
            items.append(_envelope_to_message(envelope))
    else:
        items = [
            {
                "role": "user",
                "content": [{"type": "text", "text": request.user_prompt}],
            }
        ]
    _attach_images(items, request)
    return items


def _attach_images(items: list[dict[str, object]], request: ProviderRequest) -> None:
    """Append base64 image blocks to the latest user message in ``items``.

    Image attachments belong to the current user turn, so they ride on the last
    user message. Anthropic accepts ``image`` content blocks with a base64
    source alongside text blocks.
    """

    if not request.attachments:
        return
    for item in reversed(items):
        if item.get("role") != "user":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            return
        for attachment in request.attachments:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": attachment.media_type,
                        "data": attachment.data_base64,
                    },
                }
            )
        return


def _envelope_to_message(envelope: Any) -> dict[str, object]:
    """Translate one LoopMessage into one Anthropic message dict."""

    if isinstance(envelope, UserMessage):
        return {
            "role": "user",
            "content": [{"type": "text", "text": envelope.content}],
        }
    if isinstance(envelope, AssistantMessage):
        content: list[dict[str, object]] = []
        if envelope.content:
            content.append({"type": "text", "text": envelope.content})
        for call in envelope.tool_calls:
            try:
                parsed_input = json.loads(call.arguments_json) if call.arguments_json else {}
            except json.JSONDecodeError:
                parsed_input = {}
            if not isinstance(parsed_input, Mapping):
                parsed_input = {}
            content.append(
                {
                    "type": "tool_use",
                    "id": call.provider_correlation_id,
                    "name": call.tool_name,
                    "input": dict(parsed_input),
                }
            )
        return {"role": "assistant", "content": content}
    if isinstance(envelope, ToolResultMessage):
        correlation = _require_provider_correlation_id(envelope)
        block: dict[str, object] = {
            "type": "tool_result",
            "tool_use_id": correlation,
            "content": envelope.output_text,
        }
        if envelope.is_error:
            block["is_error"] = True
        return {"role": "user", "content": [block]}
    raise AnthropicResponseParseError(
        f"unsupported message envelope: {type(envelope).__name__}"
    )


def _require_provider_correlation_id(envelope: ToolResultMessage) -> str:
    if envelope.provider_correlation_id:
        return envelope.provider_correlation_id
    raise AnthropicResponseParseError(
        "ToolResultMessage is missing provider_correlation_id."
    )


@dataclass(frozen=True, slots=True)
class ParsedAnthropicResponse:
    final_text: str | None
    usage: dict[str, int | float]
    stop_reason: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


class AnthropicProviderError(Exception):
    """Base class for sanitized Anthropic provider errors."""

    def __init__(self, message: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        super().__init__(sanitize_text(message))
        self.metadata = dict(metadata or {})


class AnthropicHTTPStatusError(AnthropicProviderError):
    """Raised when Anthropic returns a non-success HTTP status."""

    @classmethod
    def from_http_error(cls, exc: urllib.error.HTTPError) -> AnthropicHTTPStatusError:
        metadata: dict[str, Any] = {"http_status": exc.code}
        try:
            body = decode_json_object(exc.read(), error_class=AnthropicResponseParseError, provider_label="Anthropic API")
        except AnthropicResponseParseError:
            body = {}
        error = body.get("error")
        if isinstance(error, Mapping):
            error_type = error.get("type")
            if isinstance(error_type, str):
                metadata["api_error_type"] = error_type
        return cls(
            f"Anthropic API request failed with HTTP status {exc.code}.",
            metadata=metadata,
        )


class AnthropicTransportError(AnthropicProviderError):
    """Raised when the HTTP request cannot reach Anthropic."""


class AnthropicResponseParseError(AnthropicProviderError):
    """Raised when the Anthropic response shape is unsupported."""


def _parse_response(body: Mapping[str, Any]) -> ParsedAnthropicResponse:
    stop_reason_raw = body.get("stop_reason")
    stop_reason = (
        sanitize_text(stop_reason_raw)
        if isinstance(stop_reason_raw, str)
        else "unknown"
    )

    content = body.get("content")
    final_text = _extract_final_text(content)
    tool_calls = _extract_tool_calls(content)

    if not final_text and not tool_calls:
        raise AnthropicResponseParseError(
            "Anthropic response did not include final output text or tool calls.",
            metadata={"stop_reason": stop_reason},
        )

    return ParsedAnthropicResponse(
        final_text=final_text,
        usage=_extract_usage(body.get("usage")),
        stop_reason=stop_reason,
        tool_calls=tool_calls,
    )


def _extract_final_text(content: Any) -> str | None:
    if not isinstance(content, list):
        return None
    chunks: list[str] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            chunks.append(text)
    if not chunks:
        return None
    return "".join(chunks)


def _extract_tool_calls(content: Any) -> tuple[ProviderToolCall, ...]:
    """Parse Anthropic `content` items of type `tool_use`."""

    if not isinstance(content, list):
        return ()
    calls: list[ProviderToolCall] = []
    for index, item in enumerate(content):
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "tool_use":
            continue
        name = item.get("name")
        tool_input = item.get("input")
        call_id = item.get("id")
        if not isinstance(name, str) or not name:
            continue
        if isinstance(tool_input, Mapping):
            arguments_json = json.dumps(dict(tool_input), sort_keys=True)
        else:
            arguments_json = "{}"
        correlation: str
        if isinstance(call_id, str) and call_id:
            correlation = call_id
        else:
            correlation = f"anthropic-tool-{index}"
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
    for key in NORMALIZED_PROVIDER_USAGE_KEYS:
        usage[key] = value.get(key)

    # Anthropic exposes cache-related counters under cache_creation_input_tokens
    # and cache_read_input_tokens. Map either to cached_tokens when present.
    for anthropic_key, normalized_key in ANTHROPIC_USAGE_FIELD_MAP:
        if anthropic_key == normalized_key:
            continue
        item = value.get(anthropic_key)
        if item is not None and usage.get(normalized_key) is None:
            usage[normalized_key] = item

    # Synthesize total_tokens when the provider omits it but supplies inputs+outputs.
    if usage.get("total_tokens") is None:
        input_tokens = value.get("input_tokens")
        output_tokens = value.get("output_tokens")
        if isinstance(input_tokens, int) and not isinstance(input_tokens, bool) and (
            isinstance(output_tokens, int) and not isinstance(output_tokens, bool)
        ):
            usage["total_tokens"] = input_tokens + output_tokens

    return normalize_provider_usage(usage)

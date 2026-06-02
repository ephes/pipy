"""OpenAI Chat Completions API provider for the native pipy runtime.

This provider targets the `/v1/chat/completions` endpoint, which is distinct
from the Responses API surfaced by `OpenAIResponsesProvider`. Its request and
response shapes follow the universal Chat Completions contract (the same
contract OpenRouter speaks), so the on-the-wire envelope mirrors
`OpenRouterChatCompletionsProvider`. It is wired up as the
`openai-completions` native provider.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pipy_harness.capture import sanitize_text
from pipy_harness.native._provider_helpers import utc_now, safe_response_label, failed_provider_result, extract_text_content, JsonResponse, JsonHTTPClient, envelope_to_chat_message, extract_chat_completions_tool_calls, serialize_tool_for_chat_completions, extract_usage_from_fields, decode_json_object
from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.provider import StreamChunkSink

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_COMPLETIONS_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("prompt_tokens", "input_tokens"),
    ("completion_tokens", "output_tokens"),
    ("total_tokens", "total_tokens"),
)


@dataclass(frozen=True, slots=True)
class UrllibJsonHTTPClient:
    """Standard-library JSON client for OpenAI Chat Completions calls."""

    provider_label: str = "OpenAI API"

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
            raise OpenAICompletionsHTTPStatusError.from_http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = (
                sanitize_text(str(exc.reason))
                if getattr(exc, "reason", None)
                else "request failed"
            )
            raise OpenAICompletionsTransportError(
                f"{self.provider_label} request failed: {reason}"
            ) from exc

        return JsonResponse(
            status_code=status_code,
            body=decode_json_object(
                payload,
                error_class=OpenAICompletionsResponseParseError,
                provider_label=self.provider_label,
            ),
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
    http_client: JsonHTTPClient = field(default_factory=UrllibJsonHTTPClient)
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

        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": _chat_messages(request),
            "stream": False,
        }
        if request.available_tools:
            body["tools"] = [
                serialize_tool_for_chat_completions(tool) for tool in request.available_tools
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
        if api_key and not has_authorization:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            response = self.http_client.post_json(
                self.endpoint,
                headers=headers,
                body=body,
                timeout_seconds=self.timeout_seconds,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise OpenAICompletionsHTTPStatusError(
                    f"OpenAI API request failed with HTTP status {response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = _parse_response(response.body)
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


@dataclass(frozen=True, slots=True)
class ParsedOpenAICompletionsResponse:
    final_text: str | None
    usage: dict[str, int | float]
    response_object: str
    finish_reason: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


class OpenAICompletionsProviderError(Exception):
    """Base class for sanitized OpenAI Chat Completions provider errors."""

    def __init__(
        self, message: str, *, metadata: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(sanitize_text(message))
        self.metadata = dict(metadata or {})


class OpenAICompletionsHTTPStatusError(OpenAICompletionsProviderError):
    """Raised when OpenAI returns a non-success HTTP status."""

    @classmethod
    def from_http_error(
        cls, exc: urllib.error.HTTPError
    ) -> OpenAICompletionsHTTPStatusError:
        metadata: dict[str, Any] = {"http_status": exc.code}
        try:
            body = decode_json_object(exc.read(), error_class=OpenAICompletionsResponseParseError, provider_label="OpenAI API")
        except OpenAICompletionsResponseParseError:
            body = {}
        error = body.get("error")
        if isinstance(error, Mapping):
            error_type = error.get("type")
            error_code = error.get("code")
            if isinstance(error_type, str):
                metadata["api_error_type"] = sanitize_text(error_type)
            if isinstance(error_code, str | int):
                metadata["api_error_code"] = sanitize_text(str(error_code))
        return cls(
            f"OpenAI API request failed with HTTP status {exc.code}.",
            metadata=metadata,
        )


class OpenAICompletionsTransportError(OpenAICompletionsProviderError):
    """Raised when the HTTP request cannot reach OpenAI."""


class OpenAICompletionsResponseParseError(OpenAICompletionsProviderError):
    """Raised when the OpenAI Chat Completions response shape is unsupported."""


def _parse_response(body: Mapping[str, Any]) -> ParsedOpenAICompletionsResponse:
    error = body.get("error")
    if isinstance(error, Mapping):
        error_code = error.get("code")
        metadata: dict[str, Any] = {"provider_response_store_requested": False}
        if isinstance(error_code, str | int):
            metadata["api_error_code"] = sanitize_text(str(error_code))
        raise OpenAICompletionsResponseParseError(
            "OpenAI response included an error.", metadata=metadata
        )

    response_object = safe_response_label(body.get("object"), default="unknown")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OpenAICompletionsResponseParseError(
            "OpenAI response did not include a completion choice.",
            metadata={
                "provider_response_store_requested": False,
                "response_object": response_object,
            },
        )

    first_choice = choices[0]
    if not isinstance(first_choice, Mapping):
        raise OpenAICompletionsResponseParseError(
            "OpenAI response included an unsupported completion choice.",
            metadata={
                "provider_response_store_requested": False,
                "response_object": response_object,
            },
        )
    finish_reason = safe_response_label(
        first_choice.get("finish_reason"), default="unknown"
    )
    message = first_choice.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    final_text = extract_text_content(content)
    tool_calls = extract_chat_completions_tool_calls(
        message.get("tool_calls") if isinstance(message, Mapping) else None,
        provider_prefix="openai-completions",
    )
    if not final_text and not tool_calls:
        raise OpenAICompletionsResponseParseError(
            "OpenAI response did not include final message content or tool calls.",
            metadata={
                "provider_response_store_requested": False,
                "response_object": response_object,
                "finish_reason": finish_reason,
            },
        )

    return ParsedOpenAICompletionsResponse(
        final_text=final_text,
        usage=extract_usage_from_fields(body.get("usage"), OPENAI_COMPLETIONS_USAGE_FIELDS),
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
            messages.append(
                {"role": "assistant", "content": exchange.provider_final_text}
            )
    messages.append({"role": "user", "content": request.user_prompt})
    return messages

"""Anthropic Messages API provider for the native pipy runtime."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pipy_harness.native._provider_helpers import (
    utc_now,
    failed_provider_result,
    serialize_tool_for_anthropic,
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
from pipy_harness.native.deferred_tools import split_deferred_tools
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import StreamChunkSink, apply_provider_headers
from pipy_harness.native.tools.base import ToolDefinition
from pipy_harness.native.providers.anthropic_messages_wire import (
    messages_payload,
    parse_response,
)

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
# Pi forces ``display: "summarized"`` on every thinking request so the adaptive
# Claude models (Opus 4.7+, whose API default is ``"omitted"``) return a thinking
# summary like the older Claude 4 models (anthropic.ts:219-222, :954, :969-973).
ANTHROPIC_THINKING_DISPLAY_DEFAULT = "summarized"
# Claude model families that take adaptive thinking (``type: adaptive`` +
# ``output_config.effort``) rather than the ``budget_tokens`` path. These are the
# anthropic provider models Pi marks ``compat.forceAdaptiveThinking: true``
# (models.generated.ts) and the same set the bedrock adapter matches
# (Pi: supportsAdaptiveThinking). Shared with the ``providers.bedrock`` adapter.
ANTHROPIC_ADAPTIVE_MODEL_MARKERS = ("opus-4-6", "opus-4-7", "opus-4-8", "sonnet-4-6")
# Adaptive effort accepts low/medium/high/xhigh/max; minimal clamps to low
# (Pi: mapThinkingLevelToEffort). Other levels pass through unchanged.
ANTHROPIC_ADAPTIVE_EFFORT = {"minimal": "low"}


def supports_adaptive_thinking(model_id: str) -> bool:
    """Whether ``model_id`` takes adaptive thinking rather than the budget path.

    Substring match on the lowered id against the adaptive Claude families
    (Pi: ``supportsAdaptiveThinking`` / ``compat.forceAdaptiveThinking``).
    """

    lowered = model_id.lower()
    return any(marker in lowered for marker in ANTHROPIC_ADAPTIVE_MODEL_MARKERS)


def _apply_anthropic_thinking(
    body: dict[str, Any],
    *,
    model_id: str,
    reasoning_effort: str | None,
    thinking_disabled: bool,
) -> None:
    """Mutate ``body`` with Anthropic's model-specific thinking wire shape."""

    if reasoning_effort is not None:
        if supports_adaptive_thinking(model_id):
            body["thinking"] = {
                "type": "adaptive",
                "display": ANTHROPIC_THINKING_DISPLAY_DEFAULT,
            }
            body["output_config"] = {
                "effort": ANTHROPIC_ADAPTIVE_EFFORT.get(
                    reasoning_effort, reasoning_effort
                )
            }
        else:
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": ANTHROPIC_THINKING_BUDGETS.get(
                    reasoning_effort, ANTHROPIC_DEFAULT_THINKING_BUDGET
                ),
                "display": ANTHROPIC_THINKING_DISPLAY_DEFAULT,
            }
    elif thinking_disabled:
        # Reasoning-capable model run with thinking off: Pi makes the off state
        # explicit on the wire (anthropic.ts:975-976). The disabled shape
        # carries no ``display`` or budget.
        body["thinking"] = {"type": "disabled"}


def _build_anthropic_request_body(
    request: ProviderRequest,
    *,
    model_id: str,
    max_tokens: int,
    immediate_tools: tuple[ToolDefinition, ...],
    deferred_tools: tuple[ToolDefinition, ...],
    reasoning_effort: str | None,
    thinking_disabled: bool,
) -> dict[str, Any]:
    """Build the Messages body, including Anthropic-specific thinking shapes."""

    deferred_tool_names = frozenset(tool.name for tool in deferred_tools)
    body: dict[str, Any] = {
        "model": model_id,
        "max_tokens": max_tokens,
        "system": request.system_prompt,
        "messages": messages_payload(
            request,
            parse_error_class=AnthropicResponseParseError,
            deferred_tool_names=deferred_tool_names,
            attach_images=True,
            coalesce_tool_results=True,
        ),
    }
    if request.available_tools:
        serialized_tools = [
            serialize_tool_for_anthropic(tool) for tool in immediate_tools
        ]
        for tool in deferred_tools:
            serialized = serialize_tool_for_anthropic(tool)
            serialized["defer_loading"] = True
            serialized_tools.append(serialized)
        body["tools"] = serialized_tools

    _apply_anthropic_thinking(
        body,
        model_id=model_id,
        reasoning_effort=reasoning_effort,
        thinking_disabled=thinking_disabled,
    )
    return body


def anthropic_http_client() -> UrllibJsonHTTPClient:
    """Build the shared JSON client wired with Anthropic Messages error types."""

    return UrllibJsonHTTPClient(
        provider_label="Anthropic API",
        status_error_class=AnthropicHTTPStatusError,
        transport_error_class=AnthropicTransportError,
        parse_error_class=AnthropicResponseParseError,
    )


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
    http_client: JsonHTTPClient = field(default_factory=anthropic_http_client)
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
    # ``True`` when the model is reasoning-capable but thinking is off/unset for
    # this request. Pi's product path (``streamSimpleAnthropic`` -> ``buildParams``
    # ``thinkingEnabled === false``) emits an explicit ``thinking:{type:"disabled"}``
    # in that case rather than omitting the key; mutually exclusive with
    # ``reasoning_effort`` (see provider_construction.resolve_construction).
    thinking_disabled: bool = False
    supports_tool_references: bool = False

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

        immediate_tools, deferred_tools = split_deferred_tools(
            request,
            enabled=self.supports_tool_references,
        )
        # Anthropic requires at least one immediate definition when tools are
        # present. Pi falls back to the ordinary list when every current tool
        # would otherwise be deferred.
        if not immediate_tools and deferred_tools:
            immediate_tools = deferred_tools
            deferred_tools = ()
        # Anthropic-native thinking. Pi switches the adaptive Claude models
        # (Opus 4.6/4.7/4.8, Sonnet 4.6 — compat.forceAdaptiveThinking) to the
        # adaptive shape (``type: adaptive`` + ``output_config.effort``) and uses
        # the ``type: enabled``/``budget_tokens`` path for older reasoning models;
        # we mirror that split. ``display`` is forced to "summarized" on both
        # paths, matching Pi (anthropic.ts:954, :969-973), so the adaptive models
        # (API default "omitted") still return a thinking summary.
        body = _build_anthropic_request_body(
            request,
            model_id=self.model_id,
            max_tokens=self.max_tokens,
            immediate_tools=immediate_tools,
            deferred_tools=deferred_tools,
            reasoning_effort=self.reasoning_effort,
            thinking_disabled=self.thinking_disabled,
        )
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
                raise AnthropicHTTPStatusError(
                    f"Anthropic API request failed with HTTP status {response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = parse_response(
                response.body,
                parse_error_class=AnthropicResponseParseError,
                response_label="Anthropic",
                tool_call_provider_prefix="anthropic",
            )
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


class AnthropicProviderError(ProviderHTTPError):
    """Base class for sanitized Anthropic provider errors."""


class AnthropicHTTPStatusError(AnthropicProviderError):
    """Raised when Anthropic returns a non-success HTTP status."""

    provider_label = "Anthropic API"
    api_error_fields = (
        ApiErrorField("type", "api_error_type", sanitize=False, allow_int=False),
    )


class AnthropicTransportError(AnthropicProviderError):
    """Raised when the HTTP request cannot reach Anthropic."""


class AnthropicResponseParseError(AnthropicProviderError):
    """Raised when the Anthropic response shape is unsupported."""

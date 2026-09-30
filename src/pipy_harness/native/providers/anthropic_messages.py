"""Anthropic Messages API provider for the native pipy runtime."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native._provider_helpers import (
    failed_provider_result,
    serialize_tool_for_anthropic,
    utc_now,
)
from pipy_harness.native.agent.messages import AgentAssistantMessage
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.http import (
    ApiErrorField,
    JsonHTTPClient,
    ProviderHTTPError,
    UrllibJsonHTTPClient,
)
from pipy_harness.native.http import (
    JsonResponse as JsonResponse,
)
from pipy_harness.native.models import CacheRetention, ProviderRequest, ProviderResult
from pipy_harness.native.provider import (
    COPILOT_PROVIDER_NAME,
    StreamChunkSink,
    apply_provider_headers,
    copilot_dynamic_headers,
)
from pipy_harness.native.providers.anthropic_messages_wire import (
    ANTHROPIC_CACHEABLE_LAST_BLOCK_TYPES,
    anthropic_cache_control,
    apply_last_user_cache_breakpoint,
    messages_payload,
    parse_response,
    system_blocks,
)
from pipy_harness.native.providers.openai_prompt_cache import resolve_cache_retention
from pipy_harness.native.providers.transcript import (
    ResolvedTranscript,
    declaration_definition,
    declared_tools,
    has_tool_redefinitions,
    resolve_request_transcript,
)
from pipy_harness.native.tools.base import ToolDefinition

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
MID_CONVERSATION_TOOL_CHANGES_BETA = "mid-conversation-tool-changes-2026-07-01"
# Pi ``getBetaFeatures`` for ``supportsMidConvoEffort`` rows (``4e69b0c28``).
MID_CONVERSATION_OUTPUT_CONFIG_BETA = "mid-conversation-output-config-2026-07-01"
THINKING_BINDING_CONTROLS_BETA = "thinking-binding-controls-2026-08-01"
# Pi ``isAnthropicEffort``: the levels a recorded ``providerThinkingLevel``
# may replay as.
ANTHROPIC_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
# Pi ``DEFERRED_TOOL_PLACEHOLDER``: declared whenever native tool changes are
# used, so the scaffolding Anthropic adds for ``defer_loading`` tools is in the
# cached prefix from the first request. It is never activated.
DEFERRED_TOOL_PLACEHOLDER: Mapping[str, object] = {
    "name": "__pi_deferred_placeholder__",
    "description": "Reserved placeholder. Never available. Never call this.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
    "defer_loading": True,
}
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
# ``output_config.effort``) rather than the ``budget_tokens`` path. This mirrors
# Pi's generator predicate ``isAnthropicAdaptiveThinkingModel``
# (generate-models.ts:607-624), which sets ``compat.forceAdaptiveThinking`` on
# the built-in rows. Catalog construction passes that compat flag explicitly
# (Pi's runtime gate); the markers only decide for a directly constructed
# adapter. Bedrock uses its own runtime list (``providers.bedrock``).
ANTHROPIC_ADAPTIVE_MODEL_MARKERS = (
    "opus-4-6",
    "opus-4.6",
    "opus-4-7",
    "opus-4.7",
    "opus-4-8",
    "opus-4.8",
    "opus-5",
    "opus.5",
    "sonnet-4-6",
    "sonnet-4.6",
    "sonnet-5",
    "sonnet.5",
    "fable-5",
    "mythos-5",
)
# Adaptive effort accepts low/medium/high/xhigh/max; minimal clamps to low
# (Pi: mapThinkingLevelToEffort). Other levels, including ``max``, pass through.
ANTHROPIC_ADAPTIVE_EFFORT = {"minimal": "low"}


def supports_adaptive_thinking(model_id: str) -> bool:
    """Whether ``model_id`` matches Pi's adaptive-thinking Claude families.

    Substring match on the lowered id (Pi: ``isAnthropicAdaptiveThinkingModel``).
    """

    lowered = model_id.lower()
    return any(marker in lowered for marker in ANTHROPIC_ADAPTIVE_MODEL_MARKERS)


def _apply_anthropic_thinking(
    body: dict[str, Any],
    *,
    adaptive: bool,
    reasoning_effort: str | None,
    thinking_disabled: bool,
) -> None:
    """Mutate ``body`` with Anthropic's model-specific thinking wire shape."""

    if reasoning_effort is not None:
        if adaptive:
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


def _apply_managed_effort_thinking(body: dict[str, Any]) -> None:
    """Pi ``buildParams`` for mid-conversation effort models.

    They always think adaptively so a thinking-block prefix mismatch is
    dropped instead of failing the request; the top-level effort is Pi's
    constant ``high`` and the active effort rides on the trailing
    ``output_config`` system message.
    """

    body["thinking"] = {
        "type": "adaptive",
        "display": ANTHROPIC_THINKING_DISPLAY_DEFAULT,
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }
    body["output_config"] = {"effort": "high"}


def insert_thinking_level_messages(
    messages: list[dict[str, object]],
    assistant_levels: list[str | None],
    active_effort: str,
) -> list[dict[str, object]]:
    """Pi ``insertThinkingLevelMessages``.

    ``assistant_levels`` holds, per serialized assistant message in order,
    the effort it answered at (``None`` when it has none to replay). Each
    recorded one is preceded by an ``output_config`` system message, and one
    with the active effort ends the list.
    """

    levels = iter(assistant_levels)
    result: list[dict[str, object]] = []
    for message in messages:
        if message.get("role") == "assistant":
            level = next(levels, None)
            if level is not None:
                result.append(_effort_message(level))
        result.append(message)
    result.append(_effort_message(active_effort))
    return result


def _effort_message(effort: str) -> dict[str, object]:
    return {"role": "system", "content": [], "output_config": {"effort": effort}}


def _assistant_levels(items: Sequence[object], provider_name: str) -> list[str | None]:
    """Pi ``convertMessages`` effort record: same provider, a valid level.

    Only this adapter records ``provider_thinking_level``, so Pi's
    ``api === "anthropic-messages"`` check is implied.
    """

    return [
        item.provider_thinking_level
        if item.provider == provider_name
        and item.provider_thinking_level in ANTHROPIC_EFFORTS
        else None
        for item in items
        if isinstance(item, AgentAssistantMessage)
    ]


@dataclass(frozen=True, slots=True)
class _AnthropicTranscript:
    """The resolved transcript and tool lists of one Messages request."""

    resolved: ResolvedTranscript
    native_tool_changes: bool
    immediate_tools: tuple[ToolDefinition, ...]
    deferred_tools: tuple[ToolDefinition, ...]


def _anthropic_transcript(
    request: ProviderRequest,
    *,
    supports_mid_convo_system_messages: bool,
    supports_mid_convo_tool_changes: bool,
) -> _AnthropicTranscript:
    """Pi ``buildParams`` tool selection (``anthropic-messages.ts:1043-1145``).

    Native tool changes reference tools by name, so a redefined name cannot
    be expressed, and Anthropic rejects a tool list where every tool is
    deferred: they need an initial tool and no redefinition. Then the initial
    tools stay immediate and every later declaration (removed ones included)
    is deferred, surfaced by its ``tool_addition`` block, so the tool list
    only grows. Otherwise the current tools are sent.
    """

    resolved = resolve_request_transcript(
        request, supports_mid_convo=supports_mid_convo_system_messages
    )
    initial = resolved.initial_tools
    native = (
        resolved.mid_convo
        and supports_mid_convo_tool_changes
        and bool(initial)
        and not has_tool_redefinitions(resolved.system_messages)
    )
    if not native:
        return _AnthropicTranscript(resolved, False, tuple(request.available_tools), ())
    initial_names = {tool.name for tool in initial}
    later = tuple(
        declaration_definition(tool)
        for tool in declared_tools(resolved.system_messages)
        if tool.name not in initial_names
    )
    return _AnthropicTranscript(
        resolved,
        True,
        tuple(declaration_definition(tool) for tool in initial),
        later,
    )


def _build_anthropic_request_body(
    request: ProviderRequest,
    *,
    model_id: str,
    max_tokens: int,
    transcript: _AnthropicTranscript,
    reasoning_effort: str | None,
    thinking_disabled: bool,
    adaptive: bool,
    cache_control: Mapping[str, str] | None = None,
    cache_control_on_tools: bool = True,
    managed_effort: str | None = None,
    provider_name: str = "anthropic",
) -> dict[str, Any]:
    """Build the Messages body, including Anthropic-specific thinking shapes.

    ``managed_effort`` is the active effort of a mid-conversation effort
    model (``None`` otherwise): it replaces the thinking shape and inserts
    Pi's ``output_config`` system messages after the cache breakpoint.

    ``cache_control`` places Pi's prompt-cache breakpoints
    (``anthropic-messages.ts:1086-1145``, ``:1407-1432``): on the system block,
    on the last immediate tool (never a ``defer_loading`` one; skipped when
    ``cache_control_on_tools`` is off), and on the last block of a trailing
    user or system message. That is at most three of Anthropic's four
    breakpoints.
    """

    messages = messages_payload(
        request,
        parse_error_class=AnthropicResponseParseError,
        items=transcript.resolved.items,
        native_tool_changes=transcript.native_tool_changes,
        attach_images=True,
        coalesce_tool_results=True,
    )
    apply_last_user_cache_breakpoint(
        messages,
        cache_control,
        eligible_types=ANTHROPIC_CACHEABLE_LAST_BLOCK_TYPES,
    )
    if managed_effort is not None:
        messages = insert_thinking_level_messages(
            messages,
            _assistant_levels(transcript.resolved.items, provider_name),
            managed_effort,
        )
    body: dict[str, Any] = {"model": model_id, "max_tokens": max_tokens}
    system = system_blocks(transcript.resolved.leading_text, cache_control)
    if system is not None:
        body["system"] = system
    body["messages"] = messages
    if transcript.immediate_tools:
        serialized_tools = [
            serialize_tool_for_anthropic(tool) for tool in transcript.immediate_tools
        ]
        if cache_control is not None and cache_control_on_tools:
            serialized_tools[-1]["cache_control"] = dict(cache_control)
        if transcript.native_tool_changes:
            serialized_tools.append(dict(DEFERRED_TOOL_PLACEHOLDER))
        for tool in transcript.deferred_tools:
            serialized = serialize_tool_for_anthropic(tool)
            serialized["defer_loading"] = True
            serialized_tools.append(serialized)
        body["tools"] = serialized_tools

    if managed_effort is not None:
        _apply_managed_effort_thinking(body)
        return body
    _apply_anthropic_thinking(
        body,
        adaptive=adaptive,
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
    # Pi's ``compat.forceAdaptiveThinking`` gate, resolved by catalog
    # construction (always a bool there). ``None`` — only for a directly
    # constructed adapter — falls back to the id-marker predicate.
    force_adaptive_thinking: bool | None = None
    # Pi ``AnthropicMessagesCompat`` system-message bits (SYS1b), resolved per
    # flag by catalog construction: later system messages stay in place, and
    # with tool changes they add and remove tools natively.
    supports_mid_convo_system_messages: bool = False
    supports_mid_convo_tool_changes: bool = False
    # Pi ``compat.supportsMidConvoEffort`` (``4e69b0c28``): the effort is
    # sent per turn as ``output_config`` system messages and recorded on
    # each answer (``provider_thinking_level``).
    supports_mid_convo_effort: bool = False
    # Pi ``AnthropicMessagesCompat`` prompt-cache bits, resolved per flag by
    # catalog construction (``anthropic-messages.ts:207-213``): long (1h)
    # retention and tool breakpoints default on; session-affinity headers
    # default on only for OpenRouter endpoints (``None`` -> that default).
    supports_long_cache_retention: bool = True
    supports_cache_control_on_tools: bool = True
    send_session_affinity_headers: bool | None = None
    session_affinity_format: str | None = None

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

        transcript = _anthropic_transcript(
            request,
            supports_mid_convo_system_messages=self.supports_mid_convo_system_messages,
            supports_mid_convo_tool_changes=self.supports_mid_convo_tool_changes,
        )
        # Anthropic-native thinking. Pi switches the adaptive Claude models
        # (``compat.forceAdaptiveThinking``: Opus 4.6+, Opus/Sonnet 5.x, Sonnet
        # 4.6, Fable/Mythos 5) to the adaptive shape (``type: adaptive`` +
        # ``output_config.effort``) and uses the ``type: enabled``/
        # ``budget_tokens`` path for older reasoning models; we mirror that
        # split. ``display`` is forced to "summarized" on both paths, matching Pi
        # (anthropic.ts:954, :969-973), so the adaptive models (API default
        # "omitted") still return a thinking summary.
        retention = resolve_cache_retention(request.cache_retention)
        adaptive = (
            self.force_adaptive_thinking
            if self.force_adaptive_thinking is not None
            else supports_adaptive_thinking(self.model_id)
        )
        # Pi ``stream``: ``options.effort ?? "high"`` (the mapped level).
        managed_effort = (
            (
                ANTHROPIC_ADAPTIVE_EFFORT.get(
                    self.reasoning_effort, self.reasoning_effort
                )
                if self.reasoning_effort is not None
                else "high"
            )
            if self.supports_mid_convo_effort
            else None
        )
        body = _build_anthropic_request_body(
            request,
            model_id=self.model_id,
            max_tokens=self.max_tokens,
            transcript=transcript,
            reasoning_effort=self.reasoning_effort,
            thinking_disabled=self.thinking_disabled,
            adaptive=adaptive,
            cache_control=anthropic_cache_control(
                retention, long_ttl=self.supports_long_cache_retention
            ),
            cache_control_on_tools=self.supports_cache_control_on_tools,
            managed_effort=managed_effort,
            provider_name=self.name,
        )
        headers = self._request_headers(
            request, retention, has_explicit_authorization=has_explicit_authorization
        )
        # Pi ``getBetaFeatures``: mid-conversation effort and native tool
        # changes need their betas (sent comma-joined) unless a configured
        # ``anthropic-beta`` header replaces the computed list.
        betas: list[str] = []
        if managed_effort is not None:
            betas.extend(
                (MID_CONVERSATION_OUTPUT_CONFIG_BETA, THINKING_BINDING_CONTROLS_BETA)
            )
        if transcript.native_tool_changes:
            betas.append(MID_CONVERSATION_TOOL_CHANGES_BETA)
        if betas and not any(name.lower() == "anthropic-beta" for name in headers):
            headers["anthropic-beta"] = ",".join(betas)
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
            provider_thinking_level=managed_effort,
        )

    def _request_headers(
        self,
        request: ProviderRequest,
        retention: CacheRetention,
        *,
        has_explicit_authorization: bool,
    ) -> dict[str, str]:
        headers = {
            "anthropic-version": self.anthropic_version,
            "Content-Type": "application/json",
        }
        copilot = self.provider_name == COPILOT_PROVIDER_NAME
        # Pi merges the affinity header before the model and request headers,
        # so both can override it (``anthropic-messages.ts:971-983``). Pi's
        # Copilot client branch sends no affinity header.
        if not copilot:
            headers.update(self._session_affinity_headers(request, retention))
        # Merged models.json/model headers (may include an explicit Authorization).
        for header_name, header_value in self.extra_headers.items():
            headers[header_name] = header_value
        if copilot:
            # Pi ``createClient`` Copilot branch: model headers, then the
            # per-request Copilot headers; this adapter sends attachments.
            headers.update(
                copilot_dynamic_headers(request, images_sent=bool(request.attachments))
            )
        # Apply the native credential only when no explicit Authorization
        # header is present, so an explicit models.json auth header wins.
        # Copilot authenticates with a Bearer token, never ``x-api-key``.
        if self.api_key and not has_explicit_authorization:
            if copilot:
                headers["Authorization"] = f"Bearer {self.api_key}"
            else:
                headers["x-api-key"] = self.api_key
        return headers

    def _session_affinity_headers(
        self, request: ProviderRequest, retention: CacheRetention
    ) -> dict[str, str]:
        """Pi ``createClient`` session-affinity header for the API-key path.

        Sent only with a session id, retention other than ``none`` and
        ``sendSessionAffinityHeaders`` (default: OpenRouter endpoints).
        """

        session_id = request.session_id if retention != "none" else None
        if not session_id:
            return {}
        is_openrouter = (
            self.provider_name == "openrouter" or "openrouter.ai" in self.endpoint
        )
        send = (
            self.send_session_affinity_headers
            if self.send_session_affinity_headers is not None
            else is_openrouter
        )
        if not send:
            return {}
        affinity_format = (
            self.session_affinity_format
            if self.session_affinity_format is not None
            else ("openrouter" if is_openrouter else None)
        )
        header = (
            "x-session-id" if affinity_format == "openrouter" else "x-session-affinity"
        )
        return {header: session_id}


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

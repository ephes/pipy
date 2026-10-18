"""Responses-family SSE decoding and ordered output, independent of Codex policy.

The adapter owns authentication, headers, body construction and error classes.
The shared HTTP seam owns framing/closure. This consumer makes one attempt;
agent-managed retries may start a fresh attempt even after partial output.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pipy_harness.native.agent.content import ThinkingContent
from pipy_harness.native.cancellation import (
    CancelToken,
    ProviderCancelledError,
    _safe_close,
)
from pipy_harness.native.http import (
    CANCELLED_READ_ERRORS,
    ProviderHTTPError,
    SsePayloadStream,
    extract_responses_usage,
    open_url_cancellable,
    transport_exception_retryable,
)
from pipy_harness.native.models import (
    ProviderContentBlock,
    ProviderPartial,
    ProviderToolCall,
)
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.providers.openai_responses_wire import (
    ParsedResponse,
    response_error_metadata,
)
from pipy_harness.native.providers.responses_output import (
    ResponsesStreamAssembler,
    joined_text,
    output_blocks,
)


@dataclass(frozen=True, slots=True)
class ResponsesStreamOptions:
    """Protocol/error configuration, reusable by the Azure adapter."""

    label: str
    parse_error_class: type[ProviderHTTPError]
    status_error_class: type[ProviderHTTPError]
    transport_error_class: type[ProviderHTTPError]
    nested_usage_fields: tuple[tuple[str, str], ...]
    tool_call_provider_prefix: str
    error_metadata: Callable[[Mapping[str, Any]], dict[str, Any]] = (
        response_error_metadata
    )


class _CallbackFailure(BaseException):
    """Preserve sink provenance across the transport-error normalization boundary."""

    def __init__(self, error: BaseException) -> None:
        self.error = error


class ResponsesStreamFailure(Exception):
    """A typed adapter error plus non-executable partial history."""

    def __init__(
        self, error: ProviderHTTPError, blocks: tuple[ProviderContentBlock, ...]
    ) -> None:
        super().__init__(str(error))
        self.error = error
        self.content_blocks = blocks


_DELTAS = {
    "response.output_text.delta": "text",
    "response.refusal.delta": "text",
    "response.reasoning_summary_text.delta": "thinking",
    "response.reasoning_text.delta": "thinking",
    "response.function_call_arguments.delta": "call",
}
_TERMINALS = frozenset(
    {
        "response.completed",
        "response.incomplete",
        "response.failed",
        "response.cancelled",
    }
)


@dataclass(slots=True)
class _ResponseState:
    assembler: ResponsesStreamAssembler = field(
        default_factory=ResponsesStreamAssembler
    )
    received: bool = False


def stream_response(
    url: str,
    *,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    timeout_seconds: float,
    options: ResponsesStreamOptions,
    stream_sink: StreamChunkSink | None = None,
    reasoning_sink: StreamChunkSink | None = None,
    cancel_token: CancelToken | None = None,
) -> ParsedResponse:
    """Consume one request through the first terminal, closing even before EOF."""

    state = _ResponseState()
    payloads: SsePayloadStream | None = None
    try:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        response = open_url_cancellable(
            request, timeout_seconds=timeout_seconds, cancel_token=cancel_token
        )
        payloads = SsePayloadStream(response, cancel_token=cancel_token)
        return _consume(
            payloads, state, options, stream_sink, reasoning_sink, cancel_token
        )
    except _CallbackFailure as exc:
        # A local output pipe/observer can raise network-shaped exceptions too.
        # They are never provider failures and must not start a managed retry.
        if isinstance(exc.error, ProviderCancelledError):
            exc.error.partial = ProviderPartial(content_blocks=state.assembler.blocks())
        raise exc.error from None
    except ProviderCancelledError as exc:
        exc.partial = ProviderPartial(content_blocks=state.assembler.blocks())
        raise
    except urllib.error.HTTPError as exc:
        raise _http_status_failure(exc, options, cancel_token, state) from exc
    except ProviderHTTPError as exc:
        raise _failure(exc, state.assembler, state.received) from exc
    except (
        Exception
    ) as exc:  # recognized transport failures only; callbacks retain provenance
        if cancel_token is not None and cancel_token.cancelled:
            cancelled = ProviderCancelledError("native provider turn cancelled")
            cancelled.partial = ProviderPartial(content_blocks=state.assembler.blocks())
            raise cancelled from exc
        retryable = transport_exception_retryable(exc)
        if retryable is None:
            raise
        error = options.transport_error_class(
            f"{options.label} streaming request failed.",
            metadata={"retryable": retryable},
        )
        raise _failure(error, state.assembler, state.received) from exc
    finally:
        if payloads is not None:
            payloads.close()


def _http_status_failure(
    error: urllib.error.HTTPError,
    options: ResponsesStreamOptions,
    cancel_token: CancelToken | None,
    state: _ResponseState,
) -> ResponsesStreamFailure:
    """Cancellation also owns status-body reads, including shutdown-induced EOF."""

    try:
        try:
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            normalized = options.status_error_class.from_http_error(error)
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
        except CANCELLED_READ_ERRORS:
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            # With no cancellation, keep the original normalization/read error;
            # an unrelated failure must not be relabeled as an operator abort.
            raise
    except ProviderCancelledError as exc:
        exc.partial = ProviderPartial(content_blocks=state.assembler.blocks())
        raise
    finally:
        _safe_close(error)
    return _failure(normalized, state.assembler, state.received)


def _deliver(sink: StreamChunkSink | None, delta: str) -> None:
    if sink is None or not delta:
        return
    try:
        sink(delta)
    except BaseException as exc:  # noqa: BLE001 - retain exact callback provenance/identity
        raise _CallbackFailure(exc) from exc


def _consume(
    payloads: SsePayloadStream,
    state: _ResponseState,
    options: ResponsesStreamOptions,
    stream_sink: StreamChunkSink | None,
    reasoning_sink: StreamChunkSink | None,
    cancel_token: CancelToken | None,
) -> ParsedResponse:
    for payload in payloads:
        event = _decode_event(payload, options)
        state.received = True
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        kind = event.get("type")
        _assemble(state.assembler, event, stream_sink, reasoning_sink)
        if kind == "error":
            raise options.parse_error_class(
                f"{options.label} stream returned an error.",
                metadata=options.error_metadata({"error": event}),
            )
        if kind in _TERMINALS:
            result = _finalize(state.assembler, event, options)
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            return result
    raise options.transport_error_class(
        f"{options.label} stream ended before a terminal response event.",
        metadata={"retryable": True, "response_status": "unknown"},
    )


def _failure(
    error: ProviderHTTPError, assembler: ResponsesStreamAssembler, received: bool
) -> ResponsesStreamFailure:
    error.metadata.update(
        {
            "provider_response_store_requested": False,
            "provider_event_received": received,
            "transport": "sse",
        }
    )
    return ResponsesStreamFailure(error, assembler.blocks())


def _decode_event(payload: str, options: ResponsesStreamOptions) -> Mapping[str, Any]:
    try:
        event = json.loads(payload)
    except ValueError as exc:
        raise options.parse_error_class(
            f"{options.label} stream event was not JSON."
        ) from exc
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise options.parse_error_class(
            f"{options.label} stream event was not a typed object."
        )
    return event


def _assemble(
    assembler: ResponsesStreamAssembler,
    event: Mapping[str, Any],
    text_sink: StreamChunkSink | None,
    reasoning_sink: StreamChunkSink | None,
) -> None:
    kind = event.get("type")
    delta = event.get("delta")
    channel = _DELTAS.get(str(kind))
    if channel is not None and isinstance(delta, str):
        assembler.delta(event, channel, delta)
        sink = text_sink if channel == "text" else reasoning_sink
        if channel != "call":
            _deliver(sink, delta)
    elif kind == "response.reasoning_summary_part.done":
        assembler.delta(event, "thinking", "\n\n")
        _deliver(reasoning_sink, "\n\n")
    elif kind == "response.output_item.added":
        assembler.item_added(event)
    elif kind == "response.output_item.done":
        assembler.item_done(event)
    elif kind == "response.function_call_arguments.done":
        assembler.arguments_done(event)


def _finalize(
    assembler: ResponsesStreamAssembler,
    event: Mapping[str, Any],
    options: ResponsesStreamOptions,
) -> ParsedResponse:
    response = event.get("response")
    if not isinstance(response, Mapping):
        raise options.parse_error_class(
            f"{options.label} terminal response was missing."
        )
    status = response.get("status")
    if event.get("type") != "response.completed" or status != "completed":
        raise options.parse_error_class(
            f"{options.label} response did not complete successfully.",
            metadata={
                "response_status": status if isinstance(status, str) else "unknown",
                **options.error_metadata(response),
            },
        )
    if "output" in response and not isinstance(response["output"], list):
        raise options.parse_error_class(
            f"{options.label} terminal output was not a list."
        )
    blocks = _final_blocks(assembler, response, options)
    calls = tuple(block for block in blocks if isinstance(block, ProviderToolCall))
    if not assembler.tool_calls_complete(calls):
        raise options.parse_error_class(
            f"{options.label} stream completed with an unfinished tool call."
        )
    text = joined_text(blocks)
    if not text and not calls:
        raise options.parse_error_class(
            f"{options.label} response had no final text or tool calls."
        )
    return ParsedResponse(
        final_text=text,
        usage=extract_responses_usage(
            response.get("usage"), options.nested_usage_fields
        ),
        response_status="completed",
        tool_calls=calls,
        content_blocks=blocks,
    )


def _final_blocks(
    assembler: ResponsesStreamAssembler,
    response: Mapping[str, Any],
    options: ResponsesStreamOptions,
) -> tuple[ProviderContentBlock, ...]:
    assembler.backfill(response)
    previous = assembler.blocks()
    # Full terminal output, when supplied, is authoritative. Empty terminal
    # reasoning summaries may carry only encryption; retain the completed text.
    # Neither terminal reconciliation nor item.done re-emits live deltas.
    if not isinstance(response.get("output"), list):
        return previous
    blocks = output_blocks(
        response["output"],
        provider_prefix=options.tool_call_provider_prefix,
        output_text=response.get("output_text"),
    )
    thinking = {
        _thinking_id(block): block
        for block in previous
        if isinstance(block, ThinkingContent) and _thinking_id(block) is not None
    }
    return tuple(_with_reasoning_fallback(block, thinking) for block in blocks)


def _thinking_id(block: ThinkingContent) -> str | None:
    if not block.signature:
        return None
    item = json.loads(block.signature)
    identifier = item.get("id")
    return identifier if isinstance(identifier, str) else None


def _with_reasoning_fallback(
    block: ProviderContentBlock, previous: Mapping[str | None, ThinkingContent]
) -> ProviderContentBlock:
    if isinstance(block, ThinkingContent) and not block.thinking:
        prior = previous.get(_thinking_id(block))
        if prior is not None:
            return ThinkingContent(prior.thinking, block.signature)
    return block

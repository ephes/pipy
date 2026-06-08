"""Shared helpers for the native provider adapters.

Provider modules share a small set of helpers: a UTC clock, label
sanitizer, JSON response boundary, OpenAI tool-call/serializer
parsers, and a ``HarnessStatus.FAILED`` `ProviderResult` builder.
"""

from __future__ import annotations

import http.client
import json
import socket
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from pipy_harness.capture import sanitize_text
from pipy_harness.models import HarnessStatus
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError, _safe_close
from pipy_harness.native.models import ProviderRequest, ProviderResult

# Exceptions that a concurrent :meth:`CancelToken.cancel` can surface from a
# blocked ``http.client`` read once it shuts the socket down. The expected
# symptom is ``OSError`` (the shut-down ``recv`` returns/raises), but the same
# shutdown also races ``http.client.HTTPResponse._close_conn``: one path sets
# ``self.fp = None`` while another calls ``fp.close()``, yielding
# ``AttributeError: 'NoneType' object has no attribute 'close'``. ``ValueError``
# / ``HTTPException`` cover partially-read or torn-down responses. All are benign
# artifacts of the deliberate cancellation, so callers map them to
# :class:`ProviderCancelledError` *only* when the token is actually cancelled;
# otherwise they re-raise so a genuine bug still surfaces.
CANCELLED_READ_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    ValueError,
    http.client.HTTPException,
    AttributeError,
)


@dataclass(frozen=True, slots=True)
class JsonResponse:
    """Small JSON response boundary used by provider HTTP adapters."""

    status_code: int
    body: Mapping[str, Any]


class JsonHTTPClient(Protocol):
    """Minimal injectable JSON HTTP client used by every provider adapter."""

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
        cancel_token: CancelToken | None = None,
    ) -> JsonResponse:
        """POST JSON and return parsed JSON metadata.

        ``cancel_token`` is threaded down from the native tool loop's
        active-turn cancellation path. When supplied, the client registers its
        in-flight connection on the token so an Escape / Ctrl-C during the turn
        shuts the socket down — during the header wait *or* the body read — and
        raises :class:`ProviderCancelledError` instead of letting the request
        finish.
        """


class _ConnectionCloser:
    """Closeable that force-unblocks a blocked socket read on an HTTP connection.

    ``socket.shutdown(SHUT_RDWR)`` is used rather than ``close()`` because a
    blocked ``recv`` in the worker thread returns immediately on shutdown
    regardless of outstanding ``makefile()`` io-refs — whereas ``socket.close()``
    defers the real fd close while a response's ``makefile`` reader is open, so
    it would not interrupt a blocked body read. Shutdown therefore unblocks both
    the header wait (``getresponse``) and a later body/stream read.

    The socket is captured at ``connect()`` time, not read from the connection at
    cancel time: for ``Connection: close`` / close-delimited responses
    ``http.client.getresponse()`` hands the socket to the ``HTTPResponse`` and
    sets ``conn.sock = None`` while ``HTTPResponse.fp`` keeps the fd open, so a
    cancel-time ``conn.sock`` lookup would find ``None`` and fail to interrupt
    the blocked body/stream read. The captured socket's fd is still open (the
    response's ``makefile`` holds an io-ref), so ``shutdown`` still unblocks it.
    """

    __slots__ = ("_conn", "_sock")

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._sock = getattr(conn, "sock", None)

    def close(self) -> None:
        sock = self._sock if self._sock is not None else getattr(self._conn, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            self._conn.close()
        except OSError:
            pass


def _registering_connection(base: type, cancel_token: CancelToken) -> type:
    """Subclass an ``http.client`` connection that registers itself on connect.

    Registering the *connection* (not just the post-``urlopen`` response) is
    what makes cancellation reach a non-streaming JSON request: such a request
    blocks inside ``getresponse()`` waiting for response headers for the entire
    time the model generates, long before any response object exists. By
    registering as soon as the socket is established, :meth:`CancelToken.cancel`
    can shut it down during that header wait — the same shutdown also unblocks a
    later body/stream ``read()`` on the same connection.
    """

    class _RegisteringConnection(base):  # type: ignore[valid-type, misc]
        def connect(self) -> None:
            super().connect()  # type: ignore[misc]
            # register() closes + raises immediately if cancel() already fired,
            # so a worker that lost the race never blocks on a doomed request.
            cancel_token.register(_ConnectionCloser(self))

    return _RegisteringConnection


def _build_cancellable_opener(
    cancel_token: CancelToken,
) -> urllib.request.OpenerDirector:
    """Build a urllib opener whose connections register on ``cancel_token``.

    Subclassing the default HTTP/HTTPS handlers keeps urllib's proxy, redirect,
    and HTTP-error handling intact; only the connection class is swapped for one
    that registers its socket on the token at ``connect()`` time.
    """

    class _CancelHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req: urllib.request.Request) -> Any:
            return self.do_open(
                _registering_connection(http.client.HTTPConnection, cancel_token),  # type: ignore[arg-type]
                req,
            )

    class _CancelHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req: urllib.request.Request) -> Any:
            return self.do_open(
                _registering_connection(http.client.HTTPSConnection, cancel_token),  # type: ignore[arg-type]
                req,
                # urllib sets these in HTTPSHandler.__init__; typeshed omits them.
                context=self._context,  # type: ignore[attr-defined]
                check_hostname=self._check_hostname,  # type: ignore[attr-defined]
            )

    return urllib.request.build_opener(_CancelHTTPSHandler(), _CancelHTTPHandler())


def open_url_cancellable(
    request: urllib.request.Request,
    *,
    timeout_seconds: float,
    cancel_token: CancelToken | None = None,
) -> Any:
    """``urlopen`` that registers the underlying connection on the token.

    Returns the open response. With a token, a concurrent
    :meth:`CancelToken.cancel` closes the connection during the header wait or
    while the body/stream is read, surfacing :class:`ProviderCancelledError`.
    ``HTTPError`` propagates unchanged so providers keep their status handling.
    """

    if cancel_token is None:
        return urllib.request.urlopen(  # noqa: S310 - URL fixed at provider construction
            request, timeout=timeout_seconds
        )
    cancel_token.raise_if_cancelled()
    opener = _build_cancellable_opener(cancel_token)
    try:
        return opener.open(request, timeout=timeout_seconds)
    except urllib.error.HTTPError:
        raise
    except CANCELLED_READ_ERRORS as exc:
        if cancel_token.cancelled:
            raise ProviderCancelledError("native provider turn cancelled") from exc
        raise


def urlopen_read_cancellable(
    request: urllib.request.Request,
    *,
    timeout_seconds: float,
    cancel_token: CancelToken | None = None,
) -> tuple[int, bytes]:
    """``urlopen`` + ``read`` that honors a :class:`CancelToken`.

    The underlying connection is registered on the token (see
    :func:`open_url_cancellable`) so a concurrent :meth:`CancelToken.cancel`
    closes the socket during the header wait *or* the body read, unblocking the
    worker and surfacing :class:`ProviderCancelledError`. ``HTTPError`` /
    ``URLError`` propagate unchanged so each provider keeps its existing
    status/transport error handling.
    """

    response = open_url_cancellable(
        request, timeout_seconds=timeout_seconds, cancel_token=cancel_token
    )
    if cancel_token is None:
        try:
            return response.getcode(), response.read()
        finally:
            _safe_close(response)
    try:
        status_code = response.getcode()
        payload = response.read()
    except urllib.error.HTTPError:
        raise
    except CANCELLED_READ_ERRORS as exc:
        if cancel_token.cancelled:
            raise ProviderCancelledError(
                "native provider turn cancelled"
            ) from exc
        raise
    finally:
        _safe_close(response)
    cancel_token.raise_if_cancelled()
    return status_code, payload


def utc_now() -> datetime:
    """Return the current UTC timestamp."""

    return datetime.now(UTC)


def safe_response_label(value: Any, *, default: str) -> str:
    """Return ``value`` if it sanitizes to a non-redacted label, else ``default``."""

    if not isinstance(value, str) or not value:
        return default
    sanitized = sanitize_text(value)
    return sanitized if sanitized != "[REDACTED]" else default


def extract_responses_tool_calls(
    value: Any, *, provider_prefix: str
) -> tuple[Any, ...]:
    """Parse OpenAI Responses-API ``function_call`` output items into `ProviderToolCall`s."""

    from pipy_harness.native.models import ProviderToolCall

    if not isinstance(value, list):
        return ()
    calls: list[ProviderToolCall] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "function_call":
            continue
        name = item.get("name")
        arguments = item.get("arguments")
        call_id = item.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            candidate_id = item.get("id")
            call_id = (
                candidate_id
                if isinstance(candidate_id, str) and candidate_id
                else None
            )
        if not isinstance(name, str) or not name:
            continue
        if isinstance(arguments, Mapping):
            arguments = json.dumps(arguments, sort_keys=True)
        if not isinstance(arguments, str):
            arguments = ""
        correlation = call_id if call_id else f"{provider_prefix}-tool-{index}"
        try:
            calls.append(
                ProviderToolCall(
                    provider_correlation_id=correlation[
                        : ProviderToolCall.PROVIDER_CORRELATION_ID_MAX_LENGTH
                    ],
                    tool_name=name[: ProviderToolCall.TOOL_NAME_MAX_LENGTH],
                    arguments_json=arguments[
                        : ProviderToolCall.ARGUMENTS_JSON_MAX_LENGTH
                    ],
                )
            )
        except ValueError:
            continue
    return tuple(calls)


def extract_chat_completions_tool_calls(
    value: Any, *, provider_prefix: str
) -> tuple[Any, ...]:
    """Parse OpenAI Chat-Completions ``tool_calls`` arrays into `ProviderToolCall`s."""

    from pipy_harness.native.models import ProviderToolCall

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
            else f"{provider_prefix}-tool-{index}"
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


def extract_usage_from_fields(
    value: Any,
    fields: tuple[tuple[str, str], ...],
) -> dict[str, int | float]:
    """Extract usage counters from a response using a ``(provider_key, normalized_key)`` map."""

    from pipy_harness.native.usage import normalize_provider_usage

    if not isinstance(value, Mapping):
        return {}
    usage: dict[str, Any] = {}
    for provider_key, normalized_key in fields:
        usage[normalized_key] = value.get(provider_key)
    return normalize_provider_usage(usage)


def serialize_tool_for_chat_completions(tool: Any) -> dict[str, Any]:
    """OpenAI Chat-Completions tool shape: nested ``function`` object."""

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.input_schema),
        },
    }


def serialize_tool_for_anthropic(tool: Any) -> dict[str, Any]:
    """Anthropic Messages tool shape: ``input_schema`` on a flat object."""

    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": dict(tool.input_schema),
    }


def serialize_tool_for_responses(tool: Any) -> dict[str, Any]:
    """OpenAI Responses-API tool shape: flat object with ``parameters``."""

    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": dict(tool.input_schema),
    }


def envelope_to_chat_message(envelope: Any) -> dict[str, Any]:
    """Serialize a `LoopMessage` envelope into the OpenAI Chat-Completions shape."""

    from pipy_harness.native.tools.messages import (
        AssistantMessage,
        ToolResultMessage,
        UserMessage,
    )

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
        correlation_id = envelope.provider_correlation_id
        if not correlation_id:
            raise ValueError("ToolResultMessage is missing provider_correlation_id.")
        return {
            "role": "tool",
            "tool_call_id": correlation_id,
            "content": envelope.output_text,
        }
    raise ValueError(f"unsupported message envelope: {type(envelope).__name__}")


def extract_text_content(value: Any) -> str | None:
    """Extract the assistant ``text`` content from an OpenAI-shape message."""

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


def safe_http_status_metadata(status_code: int) -> dict[str, Any]:
    """Build the metadata dict carried on every HTTP-error `ProviderResult`."""

    return {"http_status": status_code}


def decode_json_object(
    payload: bytes,
    *,
    error_class: type[Exception],
    provider_label: str,
) -> Mapping[str, Any]:
    """Decode an HTTP response body as a JSON object; raise ``error_class`` on failure."""

    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise error_class(f"{provider_label} returned non-JSON response metadata.") from exc
    if not isinstance(decoded, Mapping):
        raise error_class(f"{provider_label} returned unsupported JSON response metadata.")
    return decoded


def failed_provider_result(
    request: ProviderRequest,
    *,
    provider_name: str,
    started_at: datetime,
    error_type: str,
    error_message: str,
    metadata: Mapping[str, Any] | None = None,
) -> ProviderResult:
    """Build a sanitized ``HarnessStatus.FAILED`` `ProviderResult`."""

    return ProviderResult(
        status=HarnessStatus.FAILED,
        provider_name=provider_name,
        model_id=request.model_id,
        started_at=started_at,
        ended_at=utc_now(),
        metadata=dict(metadata or {}),
        error_type=sanitize_text(error_type),
        error_message=sanitize_text(error_message),
    )

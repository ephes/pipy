"""The streamable HTTP transport: an MCP server reached over HTTP.

Every message is one POST. A request is answered in the response to its POST,
as one JSON body or as a server-sent event stream. What the server sends on
its own (notifications, requests) arrives on one more event stream, which the
transport opens with a GET once the session is initialized.

Threads, all daemons: each POST runs on an I/O thread of its own, so no
network read happens on a thread that waits for a result.

- ``send`` of a request starts the I/O thread and returns. A request that
  fails before it is answered (the connection, the status, the content type)
  is reported to the request-failure listeners with its error, which is how
  ``McpClient`` ends it. The response messages reach the message listeners
  from the I/O thread. The thread reads a response stream to its end, also
  after the answer arrived. A response stream that ends or breaks before the
  answer is resumed on the same thread, with a GET that names the last event
  id, when the server gave its events ids; otherwise, and when resuming
  fails, only that request fails.
- ``send`` of a notification or a response returns when the server accepted
  the POST (status and headers received and checked) and raises when it
  failed. It waits at most ``send_timeout_ms``.
- ``send`` of ``notifications/cancelled`` ends the I/O thread of the request
  it names by shutting its socket down, starts the POST of the notice and
  returns. The notice is given one second, and its failure is reported to the
  error listeners. The client sends it when a request times out or is
  cancelled, so neither leaves a thread or a socket behind. ``close()`` ends
  every I/O thread the same way.
- The server-to-client stream has one thread from the accepted
  ``notifications/initialized`` to ``close()``. A server that answers the GET
  with 405 has no such stream, and the thread ends. A stream that drops is
  opened again, after the delay the server asked for with ``retry`` or else
  after one second, doubling up to thirty; after five failures in a row the
  thread reports an error and ends. The waits end at once on ``close()``.

Authorization. With an auth provider every POST and GET carries the
provider's token. A provider that has ``on_unauthorized`` is handed the first
answer with status 401, and the first with status 403 whose
``WWW-Authenticate`` challenge reports ``insufficient_scope``, together with
the token the request carried. When it returns, the request is sent once
more with the token the provider has then, and that answer counts, whatever
it is. What the provider raises fails the request instead. The provider runs
on the request's I/O thread and may take as long as it needs: a request that
was given up meanwhile is not sent again.

Only a network failure and the statuses 408, 429 and 5xx are worth another
attempt to open a stream. Authentication, session and protocol errors end the
server-to-client stream (reported to the error listeners) and fail the request
whose stream was being resumed.

One lock guards the transport's state. It is never held during network I/O or
while a listener runs.

Limits of what is promised:

- There is no proxy support, and a redirect is followed only to the server's
  own origin, so configured headers and tokens are sent nowhere else.
- A connection attempt cannot be interrupted. An I/O thread that is still
  connecting when its request is given up ends when the attempt does, after
  at most ``send_timeout_ms`` (one second for a cancellation notice and for
  the DELETE of a session).
- A delay the server asks for with ``retry`` is waited as it is, however
  long; a request's own timeout, its cancellation and ``close()`` end the
  wait.
"""

from __future__ import annotations

import http.client
import json
import math
import re
import socket
import ssl
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Final, NoReturn, Protocol

from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.mcp.protocol import (
    INTERNAL_ERROR,
    JsonRpcId,
    JsonRpcMessage,
    McpConnectionClosedError,
    McpProtocolError,
    McpTimeoutError,
    is_json_rpc_id,
    is_json_rpc_request,
    is_json_rpc_response,
    is_object,
    parse_json_rpc_message,
)
from pipy_harness.native.mcp.sse import SseEvent, consume_sse_stream
from pipy_harness.native.mcp.transport import DEFAULT_MAX_MESSAGE_BYTES, TransportEvents

MAX_ERROR_BODY_BYTES: Final = 8 * 1024
ERROR_MESSAGE_BODY_CHARS: Final = 500
DEFAULT_SEND_TIMEOUT_MS: Final = 30_000
# What a cancellation notice and the DELETE of a session get, so a dead server
# holds neither the caller that gave a request up nor ``close()``.
CANCEL_NOTICE_TIMEOUT_MS: Final = 1_000
CLOSE_TIMEOUT_MS: Final = 1_000
MAX_REDIRECTS: Final = 20
DEFAULT_RECONNECT_INITIAL_DELAY_MS: Final = 1_000
DEFAULT_RECONNECT_MAX_DELAY_MS: Final = 30_000
DEFAULT_RECONNECT_MAX_RETRIES: Final = 5
# No backoff delay doubles more often than this, whatever `max_retries` is.
_MAX_BACKOFF_DOUBLINGS: Final = 32
_READ_BYTES: Final = 64 * 1024
# A bearer challenge that asks for more scope (step-up authorization).
_INSUFFICIENT_SCOPE: Final = re.compile(
    r'(?:^|[\s,])error="?insufficient_scope"?', re.IGNORECASE
)
_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
_URL_PATH_SAFE: Final = "/%:@!$&'()*+,;=-._~"


class McpHttpError(Exception):
    """The server answered a message with a status or a body that is no answer."""

    def __init__(self, status: int, message: str, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class McpAuthRequiredError(McpHttpError):
    """Status 401. ``www_authenticate`` is the challenge header, when sent."""

    def __init__(self, www_authenticate: str | None, body: str = "") -> None:
        super().__init__(401, "MCP server requires authentication", body)
        self.www_authenticate = www_authenticate


class McpSessionExpiredError(McpHttpError):
    """Status 404 for a request that carried a session id."""

    def __init__(self, body: str = "") -> None:
        super().__init__(404, "MCP session expired", body)


@dataclass(frozen=True, slots=True)
class UnauthorizedContext:
    """An answer that asks for authorization, as an auth provider is given it.

    ``status`` is 401, or 403 for a challenge that reports
    ``insufficient_scope``. ``www_authenticate`` is the answer's challenge
    header, ``None`` without one. ``token`` is the access token the rejected
    request carried, ``None`` when it carried none; a provider that holds
    another token by now knows that another request renewed it already.
    """

    status: int
    www_authenticate: str | None
    server_url: str
    token: str | None = None


class McpAuthProvider(Protocol):
    """Supplies the bearer token of an HTTP transport.

    A provider may also have ``on_unauthorized(context: UnauthorizedContext)
    -> None``, which the transport calls with a rejected request; see the
    module text. It is looked up on the object.
    """

    def token(self) -> str | None:
        """The token for the next request, or ``None`` to send none.

        It may refresh the token over the network first, so the transport
        calls it on an I/O thread and never from ``close()``.
        """
        ...


def _needs_authorization(response: _HttpResponse) -> bool:
    """Status 401, or 403 with a bearer challenge for more scope."""

    if response.status == 401:
        return True
    if response.status != 403:
        return False
    challenge = response.header("www-authenticate") or ""
    return _INSUFFICIENT_SCOPE.search(challenge) is not None


@dataclass(frozen=True, slots=True)
class StreamableHttpReconnectOptions:
    """How a dropped event stream is opened again.

    ``initial_delay_ms`` is the wait before the first attempt, doubled for
    each further one up to ``max_delay_ms``; a ``retry`` value from the server
    replaces both. ``max_retries`` failed attempts in a row end the stream.
    """

    initial_delay_ms: float = DEFAULT_RECONNECT_INITIAL_DELAY_MS
    max_delay_ms: float = DEFAULT_RECONNECT_MAX_DELAY_MS
    max_retries: int = DEFAULT_RECONNECT_MAX_RETRIES


@dataclass(frozen=True, slots=True)
class StreamableHttpTransportOptions:
    """Where the server is and what every request carries.

    ``send_timeout_ms`` bounds a connection attempt and the wait for the
    server to accept a notification or a response; a value that is not a
    positive finite number removes the bound. Requests are bounded by the
    client's request timeout instead. ``open_get_stream`` set to ``False``
    leaves the server-to-client stream unopened. ``reconnect`` applies to that
    stream and to response streams whose events carry ids.
    """

    url: str
    headers: Mapping[str, str] | None = None
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES
    auth_provider: McpAuthProvider | None = None
    send_timeout_ms: float = DEFAULT_SEND_TIMEOUT_MS
    open_get_stream: bool = True
    reconnect: StreamableHttpReconnectOptions = field(
        default_factory=StreamableHttpReconnectOptions
    )


@dataclass(frozen=True, slots=True)
class _Target:
    """One URL, split into what a connection and a request line need."""

    url: str
    secure: bool
    host: str
    port: int
    path: str

    @property
    def origin(self) -> tuple[bool, str, int]:
        return (self.secure, self.host, self.port)


def _parse_target(url: str) -> _Target | None:
    """Split an ``http`` or ``https`` URL; ``None`` when it is not one."""

    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    secure = parts.scheme == "https"
    path = urllib.parse.quote(parts.path or "/", safe=_URL_PATH_SAFE)
    if parts.query:
        path += "?" + urllib.parse.quote(parts.query, safe=_URL_PATH_SAFE + "?")
    if port is None:
        port = 443 if secure else 80
    elif port == 0:
        return None
    return _Target(url, secure, host, port, path)


def _seconds(timeout_ms: float) -> float | None:
    """A timeout in seconds, ``None`` when ``timeout_ms`` sets no bound."""

    if math.isfinite(timeout_ms) and timeout_ms > 0:
        return timeout_ms / 1000
    return None


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"Invalid JSON constant {name}")


def _is_retryable(error: Exception) -> bool:
    """Whether opening a stream again can help after ``error``.

    A network failure, also one in the middle of a body, and the statuses
    408, 429 and 5xx. Authentication, session and protocol errors are final.
    """

    if isinstance(error, McpHttpError):
        return error.status in (408, 429) or error.status >= 500
    return isinstance(error, (OSError, http.client.HTTPException))


@dataclass(slots=True)
class _StreamCursor:
    """Where one event stream stands, across the connections that carry it."""

    last_event_id: str | None = None
    retry_ms: int | None = None
    # Whether an event arrived since the stream was opened, or opened again.
    received: bool = False


def _describe_http_failure(status: int, body: str) -> str:
    text = body.strip()
    if len(text) > ERROR_MESSAGE_BODY_CHARS:
        text = text[: ERROR_MESSAGE_BODY_CHARS - 3] + "..."
    return f"MCP HTTP request failed with status {status}" + (
        f": {text}" if text else ""
    )


class _SocketShutdown:
    """Ends a blocked read or write on a connection from another thread.

    ``shutdown`` rather than ``close``: a blocked ``recv`` returns at once on
    shutdown, while ``close`` is deferred as long as the response's reader
    holds the socket. The thread that owns the connection closes it.
    """

    __slots__ = ("_socket",)

    def __init__(self, sock: socket.socket) -> None:
        self._socket = sock

    def close(self) -> None:
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass  # Not connected any more.


class _HttpResponse:
    """A response whose body has not been read, and its connection.

    ``on_close`` runs once, when the response is closed.
    """

    def __init__(
        self,
        connection: http.client.HTTPConnection,
        response: http.client.HTTPResponse,
        on_close: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._response = response
        self._on_close: Callable[[], None] | None = on_close
        self.status = response.status

    def header(self, name: str) -> str | None:
        return self._response.getheader(name)

    @property
    def content_type(self) -> str | None:
        value = self.header("content-type")
        return None if value is None else value.split(";", 1)[0].strip().lower()

    def read(self, limit: int) -> bytes:
        """The start of the body, up to ``limit`` bytes."""

        return self._response.read(limit)

    def read_body(self, limit: int) -> bytes | None:
        """The whole body, or ``None`` when it is longer than ``limit`` bytes.

        Raises ``http.client.IncompleteRead`` when the connection ends before
        the length the server announced.
        """

        announced = self._response.length
        data = self._response.read(limit + 1)
        if len(data) > limit:
            return None
        if announced is not None and len(data) < announced:
            raise http.client.IncompleteRead(data, announced - len(data))
        return data

    def chunks(self) -> Iterator[bytes]:
        """The body as it arrives."""

        while chunk := self._response.read1(_READ_BYTES):
            yield chunk

    def close(self) -> None:
        on_close, self._on_close = self._on_close, None
        if on_close is None:
            return
        try:
            self._response.close()
        finally:
            try:
                self._connection.close()
            finally:
                on_close()


class _Exchange:
    """One I/O thread's work: what ends it, and how it went for a waiting sender.

    That is one POST, with the GETs that resume its response stream, or the
    server-to-client stream.
    """

    __slots__ = ("_lock", "done", "error", "request_id", "token")

    def __init__(self, request_id: JsonRpcId | None) -> None:
        self.request_id = request_id
        self.token = CancelToken()
        self.done = threading.Event()
        self.error: Exception | None = None
        self._lock = threading.Lock()

    def finish(self, error: Exception | None = None) -> None:
        """Record how the POST went. The first call counts."""

        with self._lock:
            if not self.done.is_set():
                self.error = error
                self.done.set()


class StreamableHttpTransport(TransportEvents):
    """A connection to one MCP server over streamable HTTP."""

    def __init__(self, options: StreamableHttpTransportOptions) -> None:
        super().__init__()
        target = _parse_target(options.url)
        if target is None:
            raise ValueError(f"Invalid MCP server URL: {options.url}")
        self.options = options
        self._target = target
        self._headers = dict(options.headers or {})
        self._tls = ssl.create_default_context() if target.secure else None
        self._lock = threading.Lock()
        self._started = False
        self._closed = False
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        # The token of the latest POST, which `close()` reuses instead of
        # asking the auth provider.
        self._last_token: str | None = None
        self._exchanges: set[_Exchange] = set()
        self._requests: dict[JsonRpcId, _Exchange] = {}
        self._get_stream_started = False

    @property
    def session_id(self) -> str | None:
        with self._lock:
            return self._session_id

    def start(self) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("MCP Streamable HTTP transport already started")
            if self._closed:
                raise McpConnectionClosedError()
            self._started = True

    def set_protocol_version(self, version: str) -> None:
        with self._lock:
            self._protocol_version = version

    def send(self, message: JsonRpcMessage) -> None:
        body = json.dumps(message, allow_nan=False, separators=(",", ":")).encode()
        if is_json_rpc_request(message):
            request_id = message["id"]
            assert is_json_rpc_id(request_id)
            exchange = self._begin(request_id)
            self._start_thread(
                exchange, "mcp-http-request", self._run_request, message, body, exchange
            )
            return
        if message.get("method") == "notifications/cancelled":
            self._abandon(message.get("params"))
            exchange = self._begin(None)
            self._start_thread(
                exchange, "mcp-http-cancel", self._run_cancel_notice, body, exchange
            )
            return
        timeout_ms: float = self.options.send_timeout_ms
        exchange = self._begin(None)
        self._start_thread(exchange, "mcp-http-send", self._run_send, body, exchange)
        if not exchange.done.wait(_seconds(timeout_ms)):
            exchange.token.cancel()
            raise McpTimeoutError(timeout_ms)
        if exchange.error is not None:
            raise exchange.error
        # The server opens its stream only for an initialized session.
        if message.get("method") == "notifications/initialized":
            self._start_get_stream()

    def close(self) -> None:
        """End every I/O thread and the server's session. Idempotent.

        A session is ended with a DELETE that is given one second and whose
        failure is ignored.
        """

        with self._lock:
            if self._closed:
                return
            self._closed = True
            exchanges = list(self._exchanges)
            self._exchanges.clear()
            self._requests.clear()
            session_id = self._session_id if self._started else None
        for exchange in exchanges:
            # A sender waiting for this POST gets the close, not the error of
            # its broken connection.
            exchange.finish(McpConnectionClosedError())
            exchange.token.cancel()
        if session_id is not None:
            self._delete_session()
        self.emit_close()

    # -- one POST -----------------------------------------------------------

    def _begin(self, request_id: JsonRpcId | None) -> _Exchange:
        exchange = _Exchange(request_id)
        with self._lock:
            if not self._started or self._closed:
                raise McpConnectionClosedError()
            self._exchanges.add(exchange)
            if request_id is not None:
                self._requests[request_id] = exchange
        return exchange

    def _end(self, exchange: _Exchange) -> None:
        with self._lock:
            self._exchanges.discard(exchange)
            request_id = exchange.request_id
            if request_id is not None and self._requests.get(request_id) is exchange:
                del self._requests[request_id]

    def _start_thread(
        self,
        exchange: _Exchange,
        name: str,
        target: Callable[..., None],
        *args: object,
    ) -> None:
        try:
            threading.Thread(target=target, args=args, name=name, daemon=True).start()
        except BaseException:
            self._end(exchange)
            raise

    def _abandon(self, params: object) -> None:
        """End the I/O thread of the request a cancellation notice names."""

        request_id = params.get("requestId") if is_object(params) else None
        if not is_json_rpc_id(request_id):
            return
        with self._lock:
            exchange = self._requests.pop(request_id, None)
        if exchange is not None:
            exchange.token.cancel()

    def _run_cancel_notice(self, body: bytes, exchange: _Exchange) -> None:
        """POST a cancellation notice, giving it one second.

        Nobody waits for it, so the notices of several requests do not queue
        behind a server that stopped answering. A failure is reported to the
        error listeners.
        """

        timer = threading.Timer(CANCEL_NOTICE_TIMEOUT_MS / 1000, exchange.token.cancel)
        timer.name = "mcp-http-cancel-timer"
        timer.daemon = True
        error: Exception | None = None
        try:
            timer.start()
            self._post(body, exchange.token, CANCEL_NOTICE_TIMEOUT_MS).close()
        except Exception as caught:  # noqa: BLE001 - cross-thread capture: reported below
            error = caught
        finally:
            timer.cancel()
            self._end(exchange)
        with self._lock:
            closed = self._closed
        if error is None or closed:
            return
        if exchange.token.cancelled:
            error = McpTimeoutError(CANCEL_NOTICE_TIMEOUT_MS)
        self._report(error)

    def _run_send(self, body: bytes, exchange: _Exchange) -> None:
        """POST a notification or a response; its body, if any, is not read."""

        error: Exception | None = None
        try:
            self._post(body, exchange.token, self.options.send_timeout_ms).close()
        except Exception as caught:  # noqa: BLE001 - cross-thread capture: raised by the waiting send()
            error = caught
        finally:
            self._end(exchange)
            exchange.finish(error)

    def _run_request(
        self, message: JsonRpcMessage, body: bytes, exchange: _Exchange
    ) -> None:
        """POST a request and deliver what the server answers."""

        request_id = message["id"]
        assert is_json_rpc_id(request_id)
        try:
            response = self._post(body, exchange.token, self.options.send_timeout_ms)
            try:
                self._deliver(message, request_id, response, exchange)
            finally:
                response.close()
        except Exception as error:  # noqa: BLE001 - cross-thread capture: ends the request with this error
            # A request that was given up, or a closed transport, has nobody
            # left to tell.
            if not exchange.token.cancelled:
                self._report_request_failure(request_id, error)
        finally:
            self._end(exchange)

    def _deliver(
        self,
        message: JsonRpcMessage,
        request_id: JsonRpcId,
        response: _HttpResponse,
        exchange: _Exchange,
    ) -> None:
        if response.status in (202, 204):
            raise McpHttpError(
                response.status,
                f"MCP server accepted request {message['method']} without a response",
            )
        content_type = response.content_type
        if content_type == "application/json":
            limit = self.options.max_message_bytes
            data = response.read_body(limit)
            if data is None:
                raise McpProtocolError(f"MCP HTTP message exceeds {limit} bytes")
            value = json.loads(data, parse_constant=_reject_constant)
            for item in value if isinstance(value, list) else [value]:
                self._emit(exchange, parse_json_rpc_message(item))
        elif content_type == "text/event-stream":
            self._consume_response_stream(response, request_id, exchange)
        else:
            shown = "missing" if content_type is None else content_type
            raise McpHttpError(
                response.status, f"Unsupported MCP response content type: {shown}"
            )

    def _consume_response_stream(
        self, response: _HttpResponse, request_id: JsonRpcId, exchange: _Exchange
    ) -> None:
        """Read the event stream answering one request to its end.

        A server may close a response stream when it likes. When the stream
        ends or breaks before the answer and its events carried ids, it is
        resumed with a GET that names the last id. Otherwise, and when
        resuming fails, only this request fails.
        """

        token = exchange.token
        cursor = _StreamCursor()
        answered = False

        def emit(message: JsonRpcMessage) -> None:
            nonlocal answered
            if is_json_rpc_response(message) and message["id"] == request_id:
                answered = True
            self._emit(exchange, message)

        failure = self._read_response_stream(response, cursor, emit)
        attempt = 0
        while not (answered or token.cancelled) and self._may_resume(
            failure, cursor, attempt
        ):
            if cursor.received:
                attempt = 0
            cursor.received = False
            if not self._sleep(token, self._reconnect_delay(attempt, cursor.retry_ms)):
                return
            attempt += 1
            failure = self._resume_response_stream(cursor, token, emit, failure)
        if answered or token.cancelled:
            return
        reason = "stream ended without a response" if failure is None else str(failure)
        self._emit(
            exchange,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": INTERNAL_ERROR,
                    "message": f"MCP response stream failed: {reason}",
                },
            },
        )

    def _may_resume(
        self, failure: Exception | None, cursor: _StreamCursor, attempt: int
    ) -> bool:
        """Whether a response stream that gave no answer is opened again."""

        if failure is not None and not _is_retryable(failure):
            return False
        return (
            cursor.last_event_id is not None
            and attempt < self.options.reconnect.max_retries
        )

    def _resume_response_stream(
        self,
        cursor: _StreamCursor,
        token: CancelToken,
        emit: Callable[[JsonRpcMessage], None],
        failure: Exception | None,
    ) -> Exception | None:
        """Open a response stream again and read it; what broke it this time."""

        try:
            stream = self._open_sse_stream(cursor.last_event_id, token)
        except Exception as error:  # noqa: BLE001 - cross-thread capture: the caller retries or fails its request
            return error
        if stream is None:
            # The server has no GET streams. Nothing was read, so what broke
            # the stream before still stands.
            return failure
        return self._read_response_stream(stream, cursor, emit)

    def _read_response_stream(
        self,
        stream: _HttpResponse,
        cursor: _StreamCursor,
        emit: Callable[[JsonRpcMessage], None],
    ) -> Exception | None:
        """Read and close one connection of a response stream; what broke it."""

        try:
            self._consume_sse(stream, cursor, emit)
        except Exception as error:  # noqa: BLE001 - cross-thread capture: the caller retries or fails its request
            return error
        finally:
            stream.close()
        return None

    def _consume_sse(
        self,
        response: _HttpResponse,
        cursor: _StreamCursor,
        emit: Callable[[JsonRpcMessage], None],
    ) -> None:
        """Read one event stream to its end, keeping ``cursor`` up to date."""

        def on_id(event_id: str) -> None:
            cursor.last_event_id = event_id

        def on_retry(delay_ms: int) -> None:
            cursor.retry_ms = delay_ms

        def on_event(event: SseEvent) -> None:
            cursor.received = True
            # An event without data primes resumption with its id, and an
            # event of another type is no message.
            if not event.data.strip() or event.event not in (None, "message"):
                return
            try:
                message = parse_json_rpc_message(
                    json.loads(event.data, parse_constant=_reject_constant)
                )
            except Exception as error:  # noqa: BLE001 - cross-thread capture: reported, the stream continues
                self._report(error)
                return
            emit(message)

        consume_sse_stream(
            response.chunks(),
            on_event=on_event,
            on_id=on_id,
            on_retry=on_retry,
            max_event_bytes=self.options.max_message_bytes,
        )

    # -- the server-to-client stream, and opening a stream again -----------

    def _start_get_stream(self) -> None:
        if not self.options.open_get_stream:
            return
        exchange = _Exchange(None)
        with self._lock:
            if self._get_stream_started or self._closed:
                return
            self._get_stream_started = True
            self._exchanges.add(exchange)
        self._start_thread(exchange, "mcp-http-get", self._run_get_stream, exchange)

    def _run_get_stream(self, exchange: _Exchange) -> None:
        """Keep the server-to-client stream open until the transport closes."""

        try:
            self._read_get_stream(exchange)
        finally:
            self._end(exchange)

    def _read_get_stream(self, exchange: _Exchange) -> None:
        token = exchange.token
        reconnect = self.options.reconnect
        cursor = _StreamCursor()
        attempt = 0
        while not token.cancelled:
            try:
                response = self._open_sse_stream(cursor.last_event_id, token)
                if response is None:
                    return  # The server has no such stream.
                opened_at = time.monotonic()
                try:
                    self._consume_sse(
                        response, cursor, lambda message: self._emit(exchange, message)
                    )
                finally:
                    response.close()
                # A stream that stayed up for a while was healthy, also when
                # nothing arrived on it.
                open_ms = (time.monotonic() - opened_at) * 1000
                if cursor.received or open_ms > reconnect.max_delay_ms:
                    attempt = 0
            except Exception as error:  # noqa: BLE001 - cross-thread capture: retried, or reported and the stream ends
                if token.cancelled:
                    return
                if not _is_retryable(error):
                    self._report(error)
                    return
            if token.cancelled:
                return
            cursor.received = False
            if attempt >= reconnect.max_retries:
                self._report(
                    McpProtocolError(
                        "MCP server-to-client stream dropped and could not be reopened"
                    )
                )
                return
            if not self._sleep(token, self._reconnect_delay(attempt, cursor.retry_ms)):
                return
            attempt += 1

    def _open_sse_stream(
        self, last_event_id: str | None, token: CancelToken
    ) -> _HttpResponse | None:
        """GET an event stream; ``None`` when the server answers 405 (it has none)."""

        extra = {"Accept": "text/event-stream"}
        if last_event_id is not None:
            extra["Last-Event-ID"] = last_event_id
        response = self._authorized_fetch(
            "GET", extra, None, token, self.options.send_timeout_ms
        )
        try:
            if response.status == 405:
                response.close()
                return None
            self._check_response(response)
            self._capture_session(response)
            content_type = response.content_type
            if content_type != "text/event-stream":
                shown = "missing" if content_type is None else content_type
                raise McpHttpError(
                    response.status,
                    f"Unsupported MCP GET response content type: {shown}",
                )
        except BaseException:
            response.close()
            raise
        return response

    def _reconnect_delay(self, attempt: int, server_delay_ms: int | None) -> float:
        """Milliseconds to wait before attempt number ``attempt``, from 0."""

        if server_delay_ms is not None:
            return server_delay_ms
        reconnect = self.options.reconnect
        doublings = min(attempt, _MAX_BACKOFF_DOUBLINGS)
        delay = reconnect.initial_delay_ms * (1 << doublings)
        return min(delay, reconnect.max_delay_ms)

    @staticmethod
    def _sleep(token: CancelToken, delay_ms: float) -> bool:
        """Wait ``delay_ms``; ``False`` when ``token`` was cancelled meanwhile."""

        seconds = min(max(delay_ms, 0) / 1000, threading.TIMEOUT_MAX)
        return not token.event.wait(seconds)

    def _emit(self, exchange: _Exchange, message: JsonRpcMessage) -> None:
        """Deliver a message, unless its request was given up meanwhile."""

        if not exchange.token.cancelled:
            self.emit_message(message)

    def _post(
        self, body: bytes, token: CancelToken, connect_timeout_ms: float
    ) -> _HttpResponse:
        """POST one message; return the response once its status was accepted."""

        extra = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        response = self._authorized_fetch(
            "POST", extra, body, token, connect_timeout_ms
        )
        try:
            self._check_response(response)
            self._capture_session(response)
        except BaseException:
            response.close()
            raise
        return response

    def _authorized_fetch(
        self,
        method: str,
        extra: Mapping[str, str],
        body: bytes | None,
        token: CancelToken,
        connect_timeout_ms: float,
    ) -> _HttpResponse:
        """Send a POST or a GET with the auth provider's token.

        An answer that asks for authorization is handed to the provider
        once, and the request is sent once more with the token the provider
        has then (Pi ``authorizedFetch``).
        """

        provider = self.options.auth_provider
        on_unauthorized: Callable[[UnauthorizedContext], None] | None = getattr(
            provider, "on_unauthorized", None
        )
        bearer = provider.token() if provider is not None else None
        response = self._send_with_token(
            method, extra, body, token, connect_timeout_ms, bearer
        )
        if on_unauthorized is None or not _needs_authorization(response):
            return response
        try:
            on_unauthorized(
                UnauthorizedContext(
                    status=response.status,
                    www_authenticate=response.header("www-authenticate"),
                    server_url=self._target.url,
                    token=bearer or None,
                )
            )
        finally:
            response.close()
        if token.cancelled:
            # Given up while the provider worked: nobody waits for an answer.
            raise McpConnectionClosedError()
        assert provider is not None
        return self._send_with_token(
            method, extra, body, token, connect_timeout_ms, provider.token()
        )

    def _send_with_token(
        self,
        method: str,
        extra: Mapping[str, str],
        body: bytes | None,
        token: CancelToken,
        connect_timeout_ms: float,
        bearer: str | None,
    ) -> _HttpResponse:
        with self._lock:
            self._last_token = bearer
        headers = self._build_headers(extra, bearer)
        return self._fetch(method, headers, body, token, connect_timeout_ms)

    def _capture_session(self, response: _HttpResponse) -> None:
        session_id = response.header("mcp-session-id")
        if session_id:
            with self._lock:
                self._session_id = session_id

    def _check_response(self, response: _HttpResponse) -> None:
        if 200 <= response.status < 300:
            return
        try:
            body = response.read(MAX_ERROR_BODY_BYTES).decode("utf-8", errors="replace")
        except (OSError, http.client.HTTPException):
            body = ""
        if response.status == 401:
            raise McpAuthRequiredError(response.header("www-authenticate"), body)
        if response.status == 404 and self.session_id:
            raise McpSessionExpiredError(body)
        raise McpHttpError(
            response.status, _describe_http_failure(response.status, body), body
        )

    def _build_headers(
        self, extra: Mapping[str, str], bearer: str | None
    ) -> dict[str, str]:
        """The configured headers, then ``extra``, then what the transport owns."""

        with self._lock:
            session_id = self._session_id
            protocol_version = self._protocol_version
        merged: dict[str, tuple[str, str]] = {}
        for name, value in (*self._headers.items(), *extra.items()):
            merged[name.lower()] = (name, value)
        if session_id:
            merged["mcp-session-id"] = ("Mcp-Session-Id", session_id)
        if protocol_version:
            merged["mcp-protocol-version"] = ("MCP-Protocol-Version", protocol_version)
        if bearer:
            merged["authorization"] = ("Authorization", f"Bearer {bearer}")
        return dict(merged.values())

    def _delete_session(self) -> None:
        """Tell the server the session is over; the session expires anyway."""

        with self._lock:
            bearer = self._last_token
        headers = self._build_headers({}, bearer)
        token = CancelToken()
        done = threading.Event()

        def run() -> None:
            try:
                self._fetch("DELETE", headers, None, token, CLOSE_TIMEOUT_MS).close()
            except Exception:  # noqa: BLE001 - best effort: closing ignores a failed DELETE
                pass
            finally:
                done.set()

        try:
            threading.Thread(target=run, name="mcp-http-close", daemon=True).start()
        except RuntimeError:
            return  # No thread can be started any more.
        if not done.wait(CLOSE_TIMEOUT_MS / 1000):
            token.cancel()

    # -- HTTP ---------------------------------------------------------------

    def _fetch(
        self,
        method: str,
        headers: Mapping[str, str],
        body: bytes | None,
        token: CancelToken,
        connect_timeout_ms: float,
    ) -> _HttpResponse:
        """Send one HTTP request, following redirects within the server's origin."""

        target = self._target
        headers = dict(headers)
        connect_timeout = _seconds(connect_timeout_ms)
        followed = 0
        while True:
            connection, shutdown = self._connect(target, token, connect_timeout)

            def release(shutdown: _SocketShutdown = shutdown) -> None:
                # A token outlives its connections when a stream is opened
                # again and again, so it must not keep them.
                token.unregister(shutdown)

            try:
                connection.request(method, target.path, body=body, headers=headers)
                response = _HttpResponse(connection, connection.getresponse(), release)
            except BaseException:
                connection.close()
                release()
                raise
            location = response.header("location")
            if (
                response.status not in _REDIRECT_STATUSES
                or location is None
                or followed == MAX_REDIRECTS
            ):
                return response
            redirected = _parse_target(urllib.parse.urljoin(target.url, location))
            if redirected is None or redirected.origin != self._target.origin:
                return response
            response.close()
            followed += 1
            target = redirected
            # As a browser's fetch: 303 turns everything but GET and HEAD
            # into a GET, and so do 301 and 302 for a POST.
            if (response.status == 303 and method not in ("GET", "HEAD")) or (
                response.status in (301, 302) and method == "POST"
            ):
                method = "GET"
                body = None
                headers = {
                    name: value
                    for name, value in headers.items()
                    if name.lower() != "content-type"
                }

    def _connect(
        self, target: _Target, token: CancelToken, timeout: float | None
    ) -> tuple[http.client.HTTPConnection, _SocketShutdown]:
        connection: http.client.HTTPConnection
        if target.secure:
            connection = http.client.HTTPSConnection(
                target.host, target.port, timeout=timeout, context=self._tls
            )
        else:
            connection = http.client.HTTPConnection(
                target.host, target.port, timeout=timeout
            )
        try:
            connection.connect()
            # The timeout was for the connection attempt. From here on the
            # token ends a blocked read or write.
            connection.sock.settimeout(None)
            shutdown = _SocketShutdown(connection.sock)
            token.register(shutdown)
        except BaseException:
            connection.close()
            raise
        return connection, shutdown

    # -- reporting ----------------------------------------------------------

    def _report(self, error: Exception) -> None:
        try:
            self.emit_error(error)
        except Exception:  # noqa: BLE001 - a raising error listener must not end the I/O thread
            pass

    def _report_request_failure(self, request_id: JsonRpcId, error: Exception) -> None:
        try:
            self.emit_request_failure(request_id, error)
        except Exception as listener_error:  # noqa: BLE001 - a raising listener must not end the I/O thread
            self._report(listener_error)

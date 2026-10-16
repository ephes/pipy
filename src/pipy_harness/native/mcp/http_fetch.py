"""The HTTP requests of the streamable HTTP transport.

The transport sends every request through one function, its fetch (Pi
``options.fetch``): ``StreamableHttpTransportOptions.fetch``, or the one
``create_default_fetch`` makes. A program that needs a proxy, another TLS
setup or a network of its own passes a fetch.

What a fetch has to do:

- It is called with an ``McpHttpRequest`` on an I/O thread of the transport,
  from several threads at the same time, and returns an ``McpHttpResponse``
  once the status and the headers of the answer are known. The body is read
  by the transport, as a whole or as it arrives.
- It follows redirects itself. The transport takes the response it is handed
  as the answer, whatever its status.
- ``request.cancel`` is the request's cancellation (``signal`` of
  JavaScript's ``fetch``): it is cancelled when the transport closes, and
  when the bound of a notification or a response (``send_timeout_ms``), of a
  cancellation notice or of the DELETE of a session (one second each) runs
  out. A request whose caller timed out or was cancelled is not given up
  here: as in Pi it goes on until its answer ends. A fetch ends a blocked connect, send or read
  then, also one of the body of a response it returned earlier, by raising
  from the blocked call. ``CancelToken.register`` takes an object whose
  ``close()`` does that, and raises at once when the token is cancelled
  already. What the fetch raises after a cancellation is not reported.
- ``request.connect_timeout_ms`` bounds a connection attempt; a value that is
  not a positive finite number sets no bound. Reading the answer has no time
  limit of the fetch's own.
- A network failure is an ``OSError`` or an ``http.client.HTTPException``.
  The transport opens an event stream again after one of those, and fails
  the request with anything else that is raised. A body that ends before
  the length the server announced is a network failure too, raised by
  ``read`` without a limit and by ``chunks`` (Node's ``fetch`` fails such
  a body with a ``TypeError``).

The default fetch opens one connection per request with ``http.client``. It
has no proxy support. It asks for ``Accept-Encoding: gzip, deflate`` unless
the request names that header, and decodes an answer in those codings as
its body is read, also an event stream piece by piece (``content_coding``
has the rules); ``br`` and ``zstd`` are not asked for and are left as they
arrived. Data that is not in the coding is a network failure while the body
is read.

Connecting. The TCP connect and the TLS handshake wait for the socket
together with the request's cancellation, so closing the transport, or the
end of one of the bounds above, ends the attempt at once and closes its
socket. Each
of the two steps is given ``connect_timeout_ms``, the connect once per
address the host name has. Only the name lookup before them cannot be
interrupted: its thread ends when the resolver answers, and opens no socket
then.

Redirects are followed as the ``fetch`` of Node does, which Pi uses (the
fetch standard's HTTP-redirect fetch, and undici's addition to it):

- A 301, 302, 303, 307 or 308 answer with a ``Location`` header is followed,
  to any origin, at most ``MAX_REDIRECTS`` times. Without the header the
  answer is the response.
- A 303 turns every method but GET and HEAD into a GET, and a 301 or 302
  turns a POST into one; the body and the headers ``Content-Encoding``,
  ``Content-Language``, ``Content-Location`` and ``Content-Type`` are
  dropped then. Otherwise the method and the body are sent again.
- A redirect to another origin (scheme, host or port) drops the headers
  ``Authorization``, ``Proxy-Authorization``, ``Cookie`` and ``Host`` for
  the rest of the request, also when a later redirect leads back. Every
  other header is sent on, configured ones and the session id included.
- A ``Location`` that is no ``http`` or ``https`` URL, or that has user
  info, and a twenty-first redirect are network failures
  (``http.client.InvalidURL``, ``http.client.HTTPException``).
"""

from __future__ import annotations

import errno
import http.client
import math
import os
import selectors
import socket
import ssl
import threading
import time
import urllib.parse
import zlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.mcp.config import parse_http_url
from pipy_harness.native.mcp.content_coding import (
    ContentDecoder,
    asking_for_codings,
    content_codings,
)

MAX_REDIRECTS: Final = 20
_READ_BYTES: Final = 64 * 1024
_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
_URL_PATH_SAFE: Final = "/%:@!$&'()*+,;=-._~"
# The fetch standard's request-body-header names.
_BODY_HEADERS: Final = frozenset(
    {"content-encoding", "content-language", "content-location", "content-type"}
)
# What a redirect to another origin drops: `Authorization` by the fetch
# standard, the other three by undici.
_ORIGIN_BOUND_HEADERS: Final = frozenset(
    {"authorization", "proxy-authorization", "cookie", "host"}
)


@dataclass(frozen=True, slots=True)
class McpHttpRequest:
    """One HTTP request of the transport, as its fetch is handed it.

    ``headers`` are complete: the configured ones, the session and protocol
    headers and the bearer token. ``body`` is ``None`` for a GET and a
    DELETE. ``cancel`` and ``connect_timeout_ms`` are described in the
    module text.
    """

    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None
    cancel: CancelToken
    connect_timeout_ms: float


class McpHttpResponse(Protocol):
    """The answer to an ``McpHttpRequest``: status and headers, and a body
    that has not been read."""

    @property
    def status(self) -> int: ...

    def header(self, name: str) -> str | None:
        """The value of a header, whatever the case of ``name``; ``None``
        when the answer has none."""
        ...

    def read(self, limit: int | None = None) -> bytes:
        """The rest of the body, or at most ``limit`` bytes of it.

        Without ``limit`` a body that ends before the length the server
        announced is a network failure.
        """
        ...

    def chunks(self) -> Iterator[bytes]:
        """The rest of the body in pieces, each as soon as it arrived.

        A body that ends before the length the server announced is a
        network failure, raised after the last piece.
        """
        ...

    def close(self) -> None:
        """Release the connection. The transport may call it more than once,
        and also while the body has not been read to its end."""
        ...


McpFetch = Callable[[McpHttpRequest], McpHttpResponse]


@dataclass(frozen=True, slots=True)
class HttpTarget:
    """One URL, split into what a connection and a request line need."""

    url: str
    secure: bool
    host: str
    port: int
    path: str

    @property
    def origin(self) -> tuple[bool, str, int]:
        return (self.secure, self.host, self.port)


def parse_http_target(url: str) -> HttpTarget | None:
    """Split an ``http`` or ``https`` URL; ``None`` when it is not one.

    The URL is read as ``config.parse_http_url`` reads it, as Pi's transport
    reads its URL with ``new URL``: ``http://127.1/mcp`` connects to
    ``127.0.0.1``, and dot segments are removed from the path. ``url`` of
    the result is the URL written that way.
    """

    parsed = parse_http_url(url)
    if parsed is None or parsed.has_user_info or parsed.port == 0:
        return None
    secure = parsed.scheme == "https"
    path = urllib.parse.quote(parsed.pathname, safe=_URL_PATH_SAFE)
    if parsed.query:
        path += "?" + urllib.parse.quote(parsed.query, safe=_URL_PATH_SAFE + "?")
    port = parsed.port if parsed.port is not None else (443 if secure else 80)
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return HttpTarget(
        parsed.href + fragment, secure, parsed.hostname.strip("[]"), port, path
    )


def timeout_seconds(timeout_ms: float) -> float | None:
    """A timeout in seconds, ``None`` when ``timeout_ms`` sets no bound."""

    if math.isfinite(timeout_ms) and timeout_ms > 0:
        return timeout_ms / 1000
    return None


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
    """A response of ``http.client`` whose body has not been read, and its
    connection.

    ``on_close`` runs once, when the response is closed. With ``codings``
    the body is decoded as it is read (``content_coding``).
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
        self.status: int = response.status
        self._decoded: Iterator[bytes] | None = None
        # Decoded bytes that a `read` with a limit did not return.
        self._pending = b""

    def decode(self, codings: tuple[str, ...], url: str) -> None:
        """Decode the body from ``codings`` from now on."""

        self._decoded = self._decode(ContentDecoder(codings), url)

    def _raw(self) -> Iterator[bytes]:
        """The body as it arrives, not decoded.

        A body that ends before the ``Content-Length`` the server announced
        is a network failure, as it is for Node's ``fetch``: ``read1`` of
        ``http.client`` only closes the connection then.
        """

        while raw := self._response.read1(_READ_BYTES):
            yield raw
        left = self._response.length
        if left:
            raise http.client.IncompleteRead(b"", left)

    def _decode(self, decoder: ContentDecoder, url: str) -> Iterator[bytes]:
        for raw in self._raw():
            try:
                yield from decoder.feed(raw)
            except zlib.error as error:
                encoding = self.header("content-encoding")
                raise http.client.HTTPException(
                    f"Cannot decode the {encoding} response of {url}: {error}"
                ) from error

    def header(self, name: str) -> str | None:
        return self._response.getheader(name)

    def read(self, limit: int | None = None) -> bytes:
        if self._decoded is None:
            return self._response.read(limit)
        body = bytearray(self._pending)
        while limit is None or len(body) < limit:
            piece = next(self._decoded, None)
            if piece is None:
                break
            body += piece
        if limit is None:
            self._pending = b""
            return bytes(body)
        self._pending = bytes(body[limit:])
        return bytes(body[:limit])

    def chunks(self) -> Iterator[bytes]:
        if self._decoded is None:
            yield from self._raw()
            return
        if self._pending:
            pending, self._pending = self._pending, b""
            yield pending
        yield from self._decoded

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


class _ConnectWake:
    """Ends the wait of a connection attempt from another thread.

    ``reader`` becomes readable when ``close()`` was called, which is what a
    cancelled token does with what is registered on it.
    """

    def __init__(self) -> None:
        self.reader, self._writer = socket.socketpair()
        # Guards the two sockets against a wake while they are closed.
        self._lock = threading.Lock()
        self._freed = False

    def close(self) -> None:
        with self._lock:
            if not self._freed:
                try:
                    self._writer.send(b"\0")
                except OSError:
                    pass  # Woken before; one byte is enough.

    def free(self) -> None:
        """Close the two sockets; a wake after it does nothing."""

        with self._lock:
            self._freed = True
            self.reader.close()
            self._writer.close()


def _cancelled() -> ProviderCancelledError:
    return ProviderCancelledError("MCP HTTP request cancelled while connecting")


def _await_socket(
    sock: socket.socket, events: int, wake: _ConnectWake, deadline: float | None
) -> None:
    """Wait until ``sock`` is ready for ``events``. Raises ``TimeoutError``
    at ``deadline`` and ``ProviderCancelledError`` when ``wake`` was woken."""

    with selectors.DefaultSelector() as selector:
        selector.register(sock, events)
        selector.register(wake.reader, selectors.EVENT_READ)
        while True:
            left = None if deadline is None else max(deadline - time.monotonic(), 0)
            ready = selector.select(left)
            if any(key.fileobj is wake.reader for key, _events in ready):
                raise _cancelled()
            if ready:
                return
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("timed out")


def _deadline(timeout: float | None) -> float | None:
    return None if timeout is None else time.monotonic() + timeout


def _tcp_connect(
    sock: socket.socket,
    address: tuple[object, ...],
    wake: _ConnectWake,
    timeout: float | None,
) -> None:
    """Connect a non-blocking socket, waiting as ``_await_socket`` does."""

    code = sock.connect_ex(address)
    if code in (errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EINTR):
        _await_socket(sock, selectors.EVENT_WRITE, wake, _deadline(timeout))
        code = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
    if code:
        raise OSError(code, os.strerror(code))


def _tls_handshake(
    sock: ssl.SSLSocket, wake: _ConnectWake, timeout: float | None
) -> None:
    """The TLS handshake of a non-blocking socket, waiting likewise."""

    deadline = _deadline(timeout)
    while True:
        try:
            sock.do_handshake()
            return
        except ssl.SSLWantReadError:
            _await_socket(sock, selectors.EVENT_READ, wake, deadline)
        except ssl.SSLWantWriteError:
            _await_socket(sock, selectors.EVENT_WRITE, wake, deadline)


def _open_socket(
    target: HttpTarget, token: CancelToken, wake: _ConnectWake, timeout: float | None
) -> socket.socket:
    """A non-blocking TCP connection to the first address of the host that
    takes one. Raises the error of the last address."""

    infos = socket.getaddrinfo(target.host, target.port, 0, socket.SOCK_STREAM)
    # The lookup cannot be interrupted; nothing is opened after it for a
    # request that was cancelled meanwhile.
    token.raise_if_cancelled()
    failure: OSError | None = None
    for family, kind, protocol, _name, address in infos:
        try:
            # A family this machine has no sockets for is one more address
            # that failed.
            sock = socket.socket(family, kind, protocol)
        except OSError as error:
            failure = error
            continue
        try:
            sock.setblocking(False)
            _tcp_connect(sock, address, wake, timeout)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return sock
        except OSError as error:
            sock.close()
            failure = error
        except BaseException:
            sock.close()
            raise
    if failure is None:
        raise OSError("getaddrinfo returns an empty list")
    raise failure


def _open_stream(
    target: HttpTarget,
    token: CancelToken,
    wake: _ConnectWake,
    timeout: float | None,
    tls: Callable[[], ssl.SSLContext],
) -> socket.socket:
    """A connection to ``target``, with TLS for ``https``, that blocks
    without a timeout from here on."""

    sock = _open_socket(target, token, wake, timeout)
    try:
        if target.secure:
            sock = tls().wrap_socket(
                sock, server_hostname=target.host, do_handshake_on_connect=False
            )
            _tls_handshake(sock, wake, timeout)
        sock.settimeout(None)
    except BaseException:
        sock.close()
        raise
    return sock


def _connect(
    target: HttpTarget,
    token: CancelToken,
    timeout: float | None,
    tls: Callable[[], ssl.SSLContext],
) -> tuple[http.client.HTTPConnection, _SocketShutdown]:
    """Connect to ``target``; ``token`` ends the attempt, and afterwards a
    blocked read or write on the connection."""

    # Made first: it refuses a host that no request line can name, before
    # anything is opened.
    connection: http.client.HTTPConnection
    if target.secure:
        # For the `Host` header of the default port; the context is not used.
        connection = http.client.HTTPSConnection(
            target.host, target.port, context=tls()
        )
    else:
        connection = http.client.HTTPConnection(target.host, target.port)
    wake = _ConnectWake()
    try:
        token.register(wake)
        try:
            sock = _open_stream(target, token, wake, timeout, tls)
        finally:
            token.unregister(wake)
    finally:
        wake.free()
    shutdown = _SocketShutdown(sock)
    try:
        token.register(shutdown)
    except BaseException:
        sock.close()
        raise
    # A connection that has a socket does not connect by itself.
    connection.sock = sock
    return connection, shutdown


def _send(
    target: HttpTarget,
    method: str,
    headers: Mapping[str, str],
    body: bytes | None,
    token: CancelToken,
    timeout: float | None,
    tls: Callable[[], ssl.SSLContext],
) -> _HttpResponse:
    """Send one request on a connection of its own; the answer's body is
    not read."""

    connection, shutdown = _connect(target, token, timeout, tls)

    def release() -> None:
        # A token outlives its connections when a stream is opened again
        # and again, so it must not keep them.
        token.unregister(shutdown)

    try:
        connection.request(method, target.path, body=body, headers=headers)
        return _HttpResponse(connection, connection.getresponse(), release)
    except BaseException:
        connection.close()
        release()
        raise


def _redirect_target(response: _HttpResponse, target: HttpTarget) -> HttpTarget | None:
    """Where ``response`` sends the request to ``target`` next; ``None``
    when it is the answer. Raises ``http.client.InvalidURL`` for a place no
    request can go to."""

    location = response.header("location")
    if response.status not in _REDIRECT_STATUSES or location is None:
        return None
    try:
        redirected = parse_http_target(urllib.parse.urljoin(target.url, location))
    except ValueError:
        redirected = None
    if redirected is None:
        raise http.client.InvalidURL(
            f"Cannot follow the redirect of {target.url} to {location}"
        )
    return redirected


def _decoding(
    response: _HttpResponse, method: str, target: HttpTarget
) -> _HttpResponse:
    """``response``, set to decode its body when it has a content coding."""

    try:
        codings = content_codings(
            response.header("content-encoding"), method, response.status, target.url
        )
    except BaseException:
        response.close()
        raise
    if codings is not None:
        response.decode(codings, target.url)
    return response


def _without(headers: Mapping[str, str], names: frozenset[str]) -> dict[str, str]:
    return {name: value for name, value in headers.items() if name.lower() not in names}


def create_default_fetch() -> McpFetch:
    """The fetch of a transport that is given none; see the module text.

    Each one has a TLS context of its own, made with
    ``ssl.create_default_context`` for its first ``https`` request.
    """

    lock = threading.Lock()
    contexts: list[ssl.SSLContext] = []

    def tls() -> ssl.SSLContext:
        with lock:
            if not contexts:
                contexts.append(ssl.create_default_context())
            return contexts[0]

    def fetch(request: McpHttpRequest) -> McpHttpResponse:
        """Send one HTTP request, following redirects as Node's fetch does."""

        target = parse_http_target(request.url)
        if target is None:
            raise http.client.InvalidURL(f"Cannot request {request.url}")
        method = request.method
        body = request.body
        headers = asking_for_codings(request.headers)
        timeout = timeout_seconds(request.connect_timeout_ms)
        for _hop in range(MAX_REDIRECTS + 1):
            response = _send(
                target, method, headers, body, request.cancel, timeout, tls
            )
            try:
                redirected = _redirect_target(response, target)
            except BaseException:
                response.close()
                raise
            if redirected is None:
                return _decoding(response, method, target)
            response.close()
            if (response.status == 303 and method not in ("GET", "HEAD")) or (
                response.status in (301, 302) and method == "POST"
            ):
                method = "GET"
                body = None
                headers = _without(headers, _BODY_HEADERS)
            if redirected.origin != target.origin:
                headers = _without(headers, _ORIGIN_BOUND_HEADERS)
            target = redirected
        raise http.client.HTTPException(f"Too many redirects requesting {request.url}")

    return fetch

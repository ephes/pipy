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
  JavaScript's ``fetch``): it is cancelled when the request is given up and
  when the transport closes. A fetch ends a blocked connect, send or read
  then, also one of the body of a response it returned earlier, by raising
  from the blocked call. ``CancelToken.register`` takes an object whose
  ``close()`` does that, and raises at once when the token is cancelled
  already. What the fetch raises after a cancellation is not reported.
- ``request.connect_timeout_ms`` bounds a connection attempt; a value that is
  not a positive finite number sets no bound. Reading the answer has no time
  limit of the fetch's own.
- A network failure is an ``OSError`` or an ``http.client.HTTPException``.
  The transport opens an event stream again after one of those, and fails
  the request with anything else that is raised.

The default fetch opens one connection per request with ``http.client``. It
follows a redirect only to the origin of the request's URL, at most
``MAX_REDIRECTS`` times, has no proxy support and does not decompress
responses. A connection attempt cannot be interrupted: it ends after at most
``connect_timeout_ms``.
"""

from __future__ import annotations

import http.client
import math
import socket
import ssl
import threading
import urllib.parse
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from pipy_harness.native.cancellation import CancelToken

MAX_REDIRECTS: Final = 20
_READ_BYTES: Final = 64 * 1024
_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
_URL_PATH_SAFE: Final = "/%:@!$&'()*+,;=-._~"


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
        """The rest of the body in pieces, each as soon as it arrived."""
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
    return HttpTarget(url, secure, host, port, path)


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
        self.status: int = response.status

    def header(self, name: str) -> str | None:
        return self._response.getheader(name)

    def read(self, limit: int | None = None) -> bytes:
        return self._response.read(limit)

    def chunks(self) -> Iterator[bytes]:
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


def _connect(
    target: HttpTarget,
    token: CancelToken,
    timeout: float | None,
    tls: Callable[[], ssl.SSLContext],
) -> tuple[http.client.HTTPConnection, _SocketShutdown]:
    connection: http.client.HTTPConnection
    if target.secure:
        connection = http.client.HTTPSConnection(
            target.host, target.port, timeout=timeout, context=tls()
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


def _redirect_target(
    response: _HttpResponse, target: HttpTarget, origin: HttpTarget
) -> HttpTarget | None:
    """Where ``response`` sends the request to ``target`` next; ``None``
    when it is the answer."""

    location = response.header("location")
    if response.status not in _REDIRECT_STATUSES or location is None:
        return None
    redirected = parse_http_target(urllib.parse.urljoin(target.url, location))
    if redirected is None or redirected.origin != origin.origin:
        return None
    return redirected


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
        """Send one HTTP request, following redirects within its origin."""

        origin = parse_http_target(request.url)
        if origin is None:
            raise http.client.InvalidURL(f"Cannot request {request.url}")
        target = origin
        method = request.method
        body = request.body
        headers = dict(request.headers)
        timeout = timeout_seconds(request.connect_timeout_ms)
        followed = 0
        while True:
            response = _send(
                target, method, headers, body, request.cancel, timeout, tls
            )
            redirected = _redirect_target(response, target, origin)
            if redirected is None or followed == MAX_REDIRECTS:
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

    return fetch

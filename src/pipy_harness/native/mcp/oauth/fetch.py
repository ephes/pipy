"""The HTTP requests of the OAuth client library.

Every function of the library that talks to a server takes a ``fetch``
argument, an ``OAuthFetch``: one call sends one request and returns the whole
response. ``create_oauth_fetch`` makes the one used when none is given.

A fetch reports what happened to the request in three ways, and discovery
depends on the difference:

- a response, whatever its status;
- a network failure: an ``OSError`` or an ``http.client.HTTPException``
  (``is_network_error``). A URL that cannot be requested counts as one
  (``http.client.InvalidURL``), as it does for JavaScript's ``fetch``;
- ``McpAbortError`` when the caller's cancel event was set, and any other
  exception for anything else. The default fetch raises ``McpTimeoutError``
  when its time limit passed and ``McpProtocolError`` for a response body
  over its size limit.

The default fetch runs each request on a daemon thread and waits for it, so a
cancel event or the time limit ends the wait at once, also while the thread is
still connecting; the thread then ends when its connection attempt does, after
at most ``CONNECT_TIMEOUT_MS``.

Where the default fetch differs from JavaScript's ``fetch``, which Pi uses:

- A redirect is followed for a request without a body only, at most 20 times.
  The redirect answer to a request with a body is returned as the response,
  so a token request and its credentials never follow a server elsewhere.
  As with ``fetch``, a redirect to another origin drops the ``Authorization``,
  ``Proxy-Authorization`` and ``Cookie`` headers.
- A response body is read as a whole and limited to
  ``MAX_OAUTH_RESPONSE_BYTES``.
- There is no proxy support and no transparent decompression.
- A URL with user info is refused.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import threading
import time
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, NoReturn, Protocol

from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.mcp.config import parse_http_url
from pipy_harness.native.mcp.protocol import (
    McpAbortError,
    McpProtocolError,
    McpTimeoutError,
)

MAX_OAUTH_RESPONSE_BYTES: Final = 1024 * 1024
MAX_REDIRECTS: Final = 20
CONNECT_TIMEOUT_MS: Final = 30_000
_POLL_SECONDS: Final = 0.05
_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
_URL_PATH_SAFE: Final = "/%:@!$&'()*+,;=-._~"
# Request headers that stay with the origin they were meant for.
_ORIGIN_BOUND_HEADERS: Final = frozenset(
    {"authorization", "proxy-authorization", "cookie"}
)


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"Invalid JSON constant {name}")


@dataclass(frozen=True, slots=True)
class OAuthResponse:
    """A complete HTTP response. Header names are lower case."""

    status: int
    headers: Mapping[str, str]
    body: bytes = b""

    @property
    def ok(self) -> bool:
        return 200 <= self.status <= 299

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def text(self) -> str:
        return self.body.decode("utf-8-sig", errors="replace")

    def json(self) -> object:
        """The body as JSON. Raises ``ValueError`` when it is not JSON."""

        try:
            return json.loads(self.text(), parse_constant=_reject_constant)
        except RecursionError:
            raise ValueError("JSON is nested too deeply") from None


class OAuthFetch(Protocol):
    """Sends one HTTP request and returns its response; see the module text."""

    def __call__(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        cancel_event: threading.Event | None = None,
    ) -> OAuthResponse: ...


def is_network_error(error: BaseException) -> bool:
    """Whether ``error`` means the request reached no answer over the network."""

    return isinstance(error, (OSError, http.client.HTTPException))


@dataclass(frozen=True, slots=True)
class _Target:
    url: str
    secure: bool
    host: str
    port: int
    path: str

    @property
    def origin(self) -> tuple[bool, str, int]:
        return (self.secure, self.host, self.port)


def _cannot_request(url: str) -> http.client.InvalidURL:
    return http.client.InvalidURL(f"Cannot request URL {url}")


def _parse_target(url: str) -> _Target:
    parsed = parse_http_url(url)
    if parsed is None or parsed.has_user_info:
        raise _cannot_request(url)
    secure = parsed.scheme == "https"
    path = urllib.parse.quote(parsed.pathname, safe=_URL_PATH_SAFE)
    if parsed.query:
        path += "?" + urllib.parse.quote(parsed.query, safe=_URL_PATH_SAFE + "?")
    port = parsed.port if parsed.port is not None else (443 if secure else 80)
    return _Target(parsed.href, secure, parsed.hostname.strip("[]"), port, path)


class _SocketShutdown:
    """Ends a blocked read or write on a connection from another thread.

    It keeps the socket itself: ``http.client`` forgets the socket of a
    connection once the response headers say the connection will close, while
    the response goes on reading its body from it.
    """

    __slots__ = ("_socket",)

    def __init__(self, sock: socket.socket) -> None:
        self._socket = sock

    def close(self) -> None:
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass  # Not connected any more.


def _read_response(connection: http.client.HTTPConnection, limit: int) -> OAuthResponse:

    response = connection.getresponse()
    headers: dict[str, str] = {}
    for name, value in response.getheaders():
        key = name.lower()
        headers[key] = f"{headers[key]}, {value}" if key in headers else value
    announced = response.length
    body = response.read(limit + 1)
    if len(body) > limit:
        raise McpProtocolError(f"OAuth response exceeds {limit} bytes")
    if announced is not None and len(body) < announced:
        raise http.client.IncompleteRead(body, announced - len(body))
    return OAuthResponse(response.status, headers, body)


def _send(
    target: _Target,
    method: str,
    headers: Mapping[str, str],
    body: bytes | None,
    token: CancelToken,
    limit: int,
) -> OAuthResponse:
    """One request on a connection of its own."""

    timeout = CONNECT_TIMEOUT_MS / 1000
    connection: http.client.HTTPConnection
    if target.secure:
        connection = http.client.HTTPSConnection(
            target.host,
            target.port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
    else:
        connection = http.client.HTTPConnection(
            target.host, target.port, timeout=timeout
        )
    shutdown: _SocketShutdown | None = None
    try:
        connection.connect()
        # The timeout was for the connection attempt. From here on the token
        # ends a blocked read or write.
        connection.sock.settimeout(None)
        shutdown = _SocketShutdown(connection.sock)
        token.register(shutdown)
        connection.request(method, target.path, body=body, headers=dict(headers))
        return _read_response(connection, limit)
    except UnicodeError as error:
        # A host or a header that cannot be written in a request.
        raise _cannot_request(target.url) from error
    finally:
        if shutdown is not None:
            token.unregister(shutdown)
        connection.close()


def _exchange(
    url: str,
    method: str,
    headers: Mapping[str, str],
    body: bytes | None,
    token: CancelToken,
    limit: int,
) -> OAuthResponse:
    """One request, following redirects when it has no body."""

    target = _parse_target(url)
    for _ in range(MAX_REDIRECTS + 1):
        response = _send(target, method, headers, body, token, limit)
        location = response.header("location")
        if (
            response.status not in _REDIRECT_STATUSES
            or location is None
            or body is not None
        ):
            return response
        try:
            redirected = _parse_target(urllib.parse.urljoin(target.url, location))
        except ValueError as error:
            raise _cannot_request(location) from error
        if redirected.origin != target.origin:
            headers = {
                name: value
                for name, value in headers.items()
                if name.lower() not in _ORIGIN_BOUND_HEADERS
            }
        target = redirected
    raise http.client.HTTPException(f"Too many redirects requesting {url}")


def _await(
    done: threading.Event,
    token: CancelToken,
    cancel_event: threading.Event | None,
    timeout_ms: float | None,
) -> None:
    """Wait for ``done``; cancel ``token`` and raise on a cancel or the time limit."""

    deadline = time.monotonic() + (0 if timeout_ms is None else timeout_ms / 1000)
    while True:
        wait = _POLL_SECONDS
        if timeout_ms is not None:
            wait = max(0.0, min(wait, deadline - time.monotonic()))
        finished = done.wait(wait)
        # A cancel wins over a response that arrived with it.
        if cancel_event is not None and cancel_event.is_set():
            token.cancel()
            raise McpAbortError()
        # A response counts only when it is seen before the time limit, so
        # the limit holds also when this thread is scheduled late.
        if timeout_ms is not None and time.monotonic() >= deadline:
            token.cancel()
            raise McpTimeoutError(timeout_ms)
        if finished:
            return


def create_oauth_fetch(
    *,
    timeout_ms: float | None = None,
    max_response_bytes: int = MAX_OAUTH_RESPONSE_BYTES,
) -> OAuthFetch:
    """A fetch over ``http.client``.

    ``timeout_ms`` limits one call, from the request to the end of the
    response body, redirects included; ``None`` sets no limit.
    """

    def fetch(
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        cancel_event: threading.Event | None = None,
    ) -> OAuthResponse:
        if cancel_event is not None and cancel_event.is_set():
            raise McpAbortError()
        token = CancelToken()
        outcome: list[OAuthResponse | Exception] = []
        done = threading.Event()

        def run() -> None:
            try:
                outcome.append(
                    _exchange(
                        url, method, headers or {}, body, token, max_response_bytes
                    )
                )
            except Exception as caught:  # noqa: BLE001 - cross-thread capture: raised by the waiting caller
                outcome.append(caught)
            finally:
                done.set()

        threading.Thread(target=run, name="mcp-oauth-fetch", daemon=True).start()
        _await(done, token, cancel_event, timeout_ms)
        result = outcome[0]
        if isinstance(result, Exception):
            raise result
        return result

    return fetch


oauth_fetch: Final[OAuthFetch] = create_oauth_fetch()
"""The fetch used when a function is given none. It sets no time limit."""

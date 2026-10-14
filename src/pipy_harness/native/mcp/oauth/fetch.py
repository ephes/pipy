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

Redirects are followed as the ``fetch`` of Node does, which Pi uses (the
fetch standard's HTTP-redirect fetch, and undici's addition to it):

- A 301, 302, 303, 307 or 308 answer with a ``Location`` header is followed,
  to any origin, at most ``MAX_REDIRECTS`` times. Without the header the
  answer is the response.
- A 307 and a 308 send the method and the body again, so a registration or
  a token request follows the server to where it moved. A 303 turns every
  method but GET and HEAD into a GET, and a 301 or 302 turns a POST into
  one; the body and the headers ``Content-Encoding``, ``Content-Language``,
  ``Content-Location`` and ``Content-Type`` are dropped then.
- A redirect to another origin (scheme, host or port) drops the headers
  ``Authorization``, ``Proxy-Authorization``, ``Cookie`` and ``Host`` for
  the rest of the request, also when a later redirect leads back. Every
  other header and the body are sent on: the form fields of a token request
  (a code, a refresh token, a ``client_secret``) reach the other origin, as
  with ``fetch``.

A compressed answer is decoded as ``fetch`` decodes it. A request asks for
``Accept-Encoding: gzip, deflate`` unless the caller set that header. The
codings of the answer's ``Content-Encoding`` are undone, the last one
first: ``gzip`` and ``x-gzip``, and ``deflate`` with or without the zlib
wrapper. A compressed body that ends early gives what it held up to there; after a
gzip member comes another one or zero bytes, after a deflate stream
anything.
A list with another coding leaves the body as it arrived. The answer of a
HEAD request, of the statuses 101, 204, 205 and 304 and of a redirect that
is followed is not decoded. Data that is not what the coding says, and more
than five codings, are network failures.

Where the default fetch differs from JavaScript's ``fetch``:

- A response body is read as a whole and limited to
  ``MAX_OAUTH_RESPONSE_BYTES``, as it arrived and after each decoding.
- There is no proxy support.
- ``br`` and ``zstd`` are not asked for and not decoded (Node asks for them
  over ``https``); a body in one of them is left as it arrived.
- A URL with user info is refused, also as the target of a redirect.
- A ``Location`` is resolved with ``urllib.parse.urljoin`` and the config
  parser, not with a WHATWG URL parser.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import threading
import time
import urllib.parse
import zlib
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
# The fetch standard's request-body-header names.
_BODY_HEADERS: Final = frozenset(
    {"content-encoding", "content-language", "content-location", "content-type"}
)
# What a redirect to another origin drops: `Authorization` by the fetch
# standard, the other three by undici.
_ORIGIN_BOUND_HEADERS: Final = frozenset(
    {"authorization", "proxy-authorization", "cookie", "host"}
)
ACCEPT_ENCODING: Final = "gzip, deflate"
MAX_CONTENT_ENCODINGS: Final = 5
# Statuses whose answer has no body to decode (the fetch standard's null
# body statuses).
_NULL_BODY_STATUSES: Final = frozenset({101, 204, 205, 304})
# `wbits` of zlib: a gzip member, a zlib stream, a raw deflate stream.
_GZIP_WBITS: Final = 16 + zlib.MAX_WBITS
_ZLIB_WBITS: Final = zlib.MAX_WBITS
_RAW_WBITS: Final = -zlib.MAX_WBITS


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


def _too_large(limit: int) -> McpProtocolError:
    return McpProtocolError(f"OAuth response exceeds {limit} bytes")


def _inflate(data: bytes, wbits: int, limit: int) -> bytes:
    """``data`` decompressed. A stream that ends early gives what it held."""

    out = b""
    while True:
        decoder = zlib.decompressobj(wbits)
        out += decoder.decompress(data, limit + 1 - len(out))
        if len(out) > limit:
            raise _too_large(limit)
        # A gzip body may be several members, one after the other. As in
        # Node's zlib, data after a member is the next member unless it
        # starts with a zero byte, which ends the body as padding. Deflate
        # ignores data after its stream.
        data = decoder.unused_data
        if wbits != _GZIP_WBITS or not data or data[0] == 0:
            return out


def _decode(coding: str, data: bytes, limit: int) -> bytes:
    if coding == "deflate":
        # As undici tells the two forms apart: by the first byte.
        zlib_wrapped = bool(data) and data[0] & 0x0F == 0x08
        return _inflate(data, _ZLIB_WBITS if zlib_wrapped else _RAW_WBITS, limit)
    return _inflate(data, _GZIP_WBITS, limit)


def _decoded(
    response: OAuthResponse, method: str, url: str, limit: int
) -> OAuthResponse:
    """``response`` with the codings of its ``Content-Encoding`` undone."""

    encoding = response.header("content-encoding")
    if (
        not encoding
        or method.upper() in ("HEAD", "CONNECT")
        or response.status in _NULL_BODY_STATUSES
    ):
        return response
    codings = [coding.strip() for coding in encoding.lower().split(",")]
    if len(codings) > MAX_CONTENT_ENCODINGS:
        raise http.client.HTTPException(
            f"Too many content encodings in the response of {url}: {len(codings)}"
        )
    if any(coding not in ("gzip", "x-gzip", "deflate") for coding in codings):
        return response
    body = response.body
    try:
        for coding in reversed(codings):
            body = _decode(coding, body, limit)
    except zlib.error as error:
        raise http.client.HTTPException(
            f"Cannot decode the {encoding} response of {url}: {error}"
        ) from error
    return OAuthResponse(response.status, response.headers, body)


def _read_response(connection: http.client.HTTPConnection, limit: int) -> OAuthResponse:

    response = connection.getresponse()
    headers: dict[str, str] = {}
    for name, value in response.getheaders():
        key = name.lower()
        headers[key] = f"{headers[key]}, {value}" if key in headers else value
    announced = response.length
    body = response.read(limit + 1)
    if len(body) > limit:
        raise _too_large(limit)
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
    """One request on a connection of its own. The body of the answer is as
    it arrived."""

    if not any(name.lower() == "accept-encoding" for name in headers):
        headers = {**headers, "Accept-Encoding": ACCEPT_ENCODING}
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


def _without(headers: Mapping[str, str], names: frozenset[str]) -> dict[str, str]:
    return {name: value for name, value in headers.items() if name.lower() not in names}


def _exchange(
    url: str,
    method: str,
    headers: Mapping[str, str],
    body: bytes | None,
    token: CancelToken,
    limit: int,
) -> OAuthResponse:
    """One request, following redirects as Node's fetch does."""

    target = _parse_target(url)
    for _ in range(MAX_REDIRECTS + 1):
        response = _send(target, method, headers, body, token, limit)
        location = response.header("location")
        if response.status not in _REDIRECT_STATUSES or location is None:
            return _decoded(response, method, target.url, limit)
        try:
            redirected = _parse_target(urllib.parse.urljoin(target.url, location))
        except ValueError as error:
            raise _cannot_request(location) from error
        if (response.status == 303 and method not in ("GET", "HEAD")) or (
            response.status in (301, 302) and method == "POST"
        ):
            method = "GET"
            body = None
            headers = _without(headers, _BODY_HEADERS)
        if redirected.origin != target.origin:
            headers = _without(headers, _ORIGIN_BOUND_HEADERS)
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

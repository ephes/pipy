"""The loopback HTTP server that receives an authorization response.

A sign-in sends the browser to the authorization server with a redirect URI
that points at this server. ``OAuthCallbackServer`` listens there,
``wait_for_callback`` registers the ``state`` value of one sign-in, and the
request the browser makes after the redirect settles it: with the
authorization code, or with the reason there is none.

The server runs on its own daemon thread and answers every connection on a
further one. ``wait_for_callback`` does not block: it returns a
``PendingOAuthCallback`` whose ``result`` blocks its caller, so the caller can
register the state before it opens the browser and can wait for something
else at the same time (``done`` is set when there is an outcome).

Where this differs from Pi's ``OAuthCallbackServer``:

- Pi's plain ``Error`` is ``OAuthFlowError``; a failure to listen is the
  ``OSError`` of the bind.
- ``result`` takes a cancel event and raises ``McpAbortError`` when it is set.
  The state stays registered then; the caller closes the server.
- ``wait_for_callback`` on a closed server raises at once, where Pi registers
  a state that can only time out.
- The request target is read with ``config.parse_http_url``, not a WHATWG
  parser. A target that parser does not accept is answered 404.
- A ``render_page`` that raises gets the plain-text page sent in its place.
- ``close`` also drops connections that are still open, so a browser's spare
  connection does not outlive the server. It first gives the pages that are
  being sent two seconds to finish (Pi's ``close`` waits for every open
  connection without a limit).
"""

from __future__ import annotations

import socket
import socketserver
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final

from pipy_harness.native.mcp.config import parse_http_url
from pipy_harness.native.mcp.oauth.errors import OAuthFlowError
from pipy_harness.native.mcp.protocol import McpAbortError

DEFAULT_CALLBACK_HOST: Final = "127.0.0.1"
DEFAULT_CALLBACK_PATH: Final = "/callback"
DEFAULT_CALLBACK_TIMEOUT_MS: Final = 5 * 60_000

_POLL_SECONDS: Final = 0.05
# How long `close` lets requests that are being answered finish.
_CLOSE_GRACE_SECONDS = 2.0


@dataclass(frozen=True)
class OAuthCallback:
    """An authorization response: the code, its ``state`` and the issuer, if sent."""

    code: str
    state: str
    iss: str | None = None


@dataclass(frozen=True)
class OAuthCallbackPage:
    """The outcome shown on the browser page after the redirect.

    ``message`` and ``details`` belong to a failure; a success has neither.
    """

    ok: bool
    message: str = ""
    details: str | None = None


@dataclass(frozen=True)
class OAuthCallbackServerOptions:
    # The address to listen on.
    host: str = DEFAULT_CALLBACK_HOST
    # The host name in `redirect_url`, for example `localhost` for a client
    # registered with it while listening on 127.0.0.1. Default: `host`.
    redirect_host: str | None = None
    # None or 0: a free port.
    port: int | None = None
    path: str = DEFAULT_CALLBACK_PATH
    # More paths that receive the callback, for example the server-specific
    # path of a redirect URI.
    extra_paths: tuple[str, ...] = ()
    timeout_ms: int | float = DEFAULT_CALLBACK_TIMEOUT_MS
    # Renders the browser page as HTML. Default: a plain-text message.
    render_page: Callable[[OAuthCallbackPage], str] | None = None


def _plain_text(page: OAuthCallbackPage) -> str:
    if page.ok:
        return "Authorization complete. You may close this window."
    return f"{page.message}\n\n{page.details}" if page.details else page.message


class PendingOAuthCallback:
    """The authorization response one sign-in waits for."""

    def __init__(self, path: str | None) -> None:
        self.done: Final = threading.Event()
        self._path = path
        self._timer: threading.Timer | None = None
        self._callback: OAuthCallback | None = None
        self._error: Exception | None = None

    def result(self, cancel_event: threading.Event | None = None) -> OAuthCallback:
        """Block until there is an outcome; return the response or raise the failure.

        Raises ``McpAbortError`` when ``cancel_event`` is set first.
        """

        if cancel_event is None:
            self.done.wait()
        else:
            while not self.done.wait(_POLL_SECONDS):
                if cancel_event.is_set():
                    raise McpAbortError("OAuth callback wait aborted")
        if self._error is not None:
            raise self._error
        assert self._callback is not None
        return self._callback

    # Called once, by whoever removed this entry from the server's pending map.
    def _settle(
        self, callback: OAuthCallback | None = None, error: Exception | None = None
    ) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._callback = callback
        self._error = error
        self.done.set()


class _HttpServer(ThreadingHTTPServer):
    """One daemon thread per connection; open connections can be dropped."""

    daemon_threads = True
    block_on_close = False

    def __init__(
        self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]
    ) -> None:
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self._connections: set[socket.socket] = set()
        self._connections_lock = threading.Lock()
        super().__init__(address, handler)

    def server_bind(self) -> None:
        # HTTPServer.server_bind also looks up the host's name, which can take
        # seconds and is not needed here.
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])

    def process_request(self, request: Any, client_address: Any) -> None:
        with self._connections_lock:
            self._connections.add(request)
        super().process_request(request, client_address)

    def shutdown_request(self, request: Any) -> None:
        with self._connections_lock:
            self._connections.discard(request)
        super().shutdown_request(request)

    def close_all_connections(self) -> None:
        with self._connections_lock:
            connections = list(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A browser that went away is not worth a traceback on stderr.
        return


def _first(params: dict[str, list[str]], name: str) -> str | None:
    values = params.get(name)
    return values[0] if values else None


class OAuthCallbackServer:
    """Listens for authorization responses until ``close``.

    Raises ``OSError`` when it cannot listen, for example on a port in use.
    """

    def __init__(self, options: OAuthCallbackServerOptions | None = None) -> None:
        options = options or OAuthCallbackServerOptions()
        self._paths: Final = (options.path, *options.extra_paths)
        self._timeout_seconds: Final = min(
            max(options.timeout_ms, 0) / 1000, threading.TIMEOUT_MAX
        )
        self._render_page: Final = options.render_page
        # Guards `_pending`, `_closed` and `_answering`; an entry is settled by
        # whoever removed it from `_pending` while holding the lock.
        self._lock: Final = threading.Lock()
        self._pending: dict[str, PendingOAuthCallback] = {}
        self._closed = False
        # Requests between their first look at `_pending` and the end of their
        # reply. A wait is settled before its page is sent, so its caller may
        # close the server while the page is on its way.
        self._answering = 0
        self._idle: Final = threading.Condition(self._lock)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                with owner._lock:
                    owner._answering += 1
                try:
                    status, page = owner._handle(self.path)
                    owner._reply(self, status, page)
                finally:
                    with owner._idle:
                        owner._answering -= 1
                        owner._idle.notify_all()

            # As in Pi, the method of the request does not matter.
            do_GET = do_HEAD = do_POST = do_PUT = _answer
            do_PATCH = do_DELETE = do_OPTIONS = _answer

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self._server: Final = _HttpServer((options.host, options.port or 0), Handler)
        self.port: Final = int(self._server.server_address[1])
        redirect_host = (
            options.host if options.redirect_host is None else options.redirect_host
        )
        if ":" in redirect_host:
            redirect_host = f"[{redirect_host}]"
        self.redirect_url: Final = f"http://{redirect_host}:{self.port}{options.path}"
        self._thread: Final = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": _POLL_SECONDS},
            name="mcp-oauth-callback",
            daemon=True,
        )
        self._thread.start()

    def wait_for_callback(
        self, state: str, path: str | None = None
    ) -> PendingOAuthCallback:
        """Register ``state`` and return the response that will carry it.

        With ``path``, a response on another path fails the wait, so a
        server-specific redirect URI can tell authorization servers apart
        (RFC 9700, section 4.4.2.2). The wait fails when no response arrived
        within the server's timeout, counted from this call.
        """

        pending = PendingOAuthCallback(path)
        timer = threading.Timer(
            self._timeout_seconds, self._time_out, args=(state, pending)
        )
        timer.name = "mcp-oauth-callback-timeout"
        timer.daemon = True
        pending._timer = timer
        with self._lock:
            if self._closed:
                raise OAuthFlowError("OAuth callback server closed")
            if state in self._pending:
                raise OAuthFlowError("OAuth state is already pending")
            self._pending[state] = pending
            # Started under the lock, so `_settle` never cancels an unstarted timer.
            timer.start()
        return pending

    def close(self) -> None:
        """Fail every wait, stop listening and drop the open connections.

        Requests that are being answered get a moment to finish first, so the
        browser of a sign-in that just completed still receives its page.
        """

        with self._lock:
            if self._closed:
                return
            self._closed = True
            waits = list(self._pending.values())
            self._pending.clear()
        for pending in waits:
            pending._settle(error=OAuthFlowError("OAuth callback server closed"))
        self._server.shutdown()
        self._server.server_close()
        with self._idle:
            self._idle.wait_for(lambda: self._answering == 0, _CLOSE_GRACE_SECONDS)
        self._server.close_all_connections()
        self._thread.join()

    def _time_out(self, state: str, pending: PendingOAuthCallback) -> None:
        with self._lock:
            if self._pending.get(state) is not pending:
                return
            del self._pending[state]
        pending._settle(error=OAuthFlowError("OAuth callback timed out"))

    def _handle(self, target: str) -> tuple[int, OAuthCallbackPage]:
        try:
            url = parse_http_url(urllib.parse.urljoin("http://localhost/", target))
        except ValueError:
            url = None
        if url is None or url.pathname not in self._paths:
            return 404, OAuthCallbackPage(False, "Not found")
        params = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        state = _first(params, "state")
        with self._lock:
            pending = self._pending.pop(state, None) if state else None
        if not state or pending is None:
            return 400, OAuthCallbackPage(False, "Invalid or expired OAuth state")
        if pending._path is not None and url.pathname != pending._path:
            pending._settle(
                error=OAuthFlowError(
                    "The authorization response arrived on another redirect URI"
                )
            )
            return 400, OAuthCallbackPage(False, "Unexpected redirect URI")
        error = _first(params, "error")
        if error:
            description = _first(params, "error_description")
            if description is None:
                description = error
            pending._settle(error=OAuthFlowError(description))
            return 200, OAuthCallbackPage(
                False, "Authorization failed. You may close this window.", description
            )
        code = _first(params, "code")
        if not code:
            pending._settle(
                error=OAuthFlowError(
                    "OAuth callback did not include an authorization code"
                )
            )
            return 400, OAuthCallbackPage(False, "Missing authorization code")
        pending._settle(OAuthCallback(code, state, _first(params, "iss") or None))
        return 200, OAuthCallbackPage(True)

    def _reply(
        self, handler: BaseHTTPRequestHandler, status: int, page: OAuthCallbackPage
    ) -> None:
        headers = {"Content-Type": "text/plain; charset=utf-8"}
        text = _plain_text(page)
        if self._render_page is not None:
            try:
                text = self._render_page(page)
            except Exception:  # noqa: BLE001 - the browser still gets an answer
                pass
            else:
                headers = {
                    "Content-Type": "text/html; charset=utf-8",
                    "Cache-Control": "no-store",
                }
        body = text.encode("utf-8", "replace")
        handler.send_response(status)
        for name, value in headers.items():
            handler.send_header(name, value)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)

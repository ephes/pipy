"""A fake authorization server and protected-resource metadata endpoint.

Standard library only, and no code shared with ``pipy_harness``. Import it
with ``tests/fixtures`` on ``sys.path``; it runs in the test's own process:

    with FakeOAuthServer() as server:
        server.json("/.well-known/oauth-authorization-server", {...})
        ... server.origin, server.requests, server.paths

A request is answered by the route of its path: a ``Reply``, or a function
that is given the ``OAuthRequest`` and returns one. A path without a route is
answered 404. Every response closes its connection.

So far it serves documents only. The endpoints of a sign-in (registration,
authorize, token) are routes a test or a later version of this file adds.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass(frozen=True)
class OAuthRequest:
    """One received HTTP request. Header names are lower case."""

    method: str
    path: str
    query: str
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class Reply:
    """The answer to one request.

    ``content_length`` announces that length, whatever the body's. With
    ``hang`` the request is never answered, until the server is closed; with
    ``drop`` its connection is cut without an answer. With ``stall`` the
    connection stays open after the body until the server is closed, which
    holds a client that was announced a longer body.
    """

    status: int = 200
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    content_length: int | None = None
    hang: bool = False
    drop: bool = False
    stall: bool = False


Route = Reply | Callable[[OAuthRequest], Reply]


def json_reply(document: Any, status: int = 200) -> Reply:
    return Reply(
        status,
        {"Content-Type": "application/json"},
        json.dumps(document).encode("utf-8"),
    )


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: _Server

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _handle(self) -> None:
        fake = self.server.fake
        parts = urllib.parse.urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        request = OAuthRequest(
            self.command,
            parts.path,
            parts.query,
            {name.lower(): value for name, value in self.headers.items()},
            self.rfile.read(length) if length else b"",
        )
        reply = fake.answer(request)
        self.close_connection = True
        if reply.hang:
            fake.stopped.wait()
            return
        if reply.drop:
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.send_response(reply.status)
        for name, value in reply.headers.items():
            self.send_header(name, value)
        length = (
            len(reply.body) if reply.content_length is None else reply.content_length
        )
        self.send_header("Content-Length", str(length))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(reply.body)
        self.wfile.flush()
        if reply.stall:
            fake.stopped.wait()

    do_GET = do_POST = _handle


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    fake: FakeOAuthServer

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A client that gave its request up has cut the connection.
        if not isinstance(sys.exc_info()[1], OSError):
            super().handle_error(request, client_address)


class FakeOAuthServer:
    """One scripted HTTP server on ``127.0.0.1``, started by the constructor."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self._routes: dict[str, Route] = {}
        self._requests: list[OAuthRequest] = []
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.fake = self
        self.port: int = self._server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.01},
            name="fake-oauth-server",
            daemon=True,
        )
        self._thread.start()

    def __enter__(self) -> FakeOAuthServer:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def route(self, path: str, route: Route) -> None:
        """Answer requests for ``path`` with ``route`` from now on."""

        with self.lock:
            self._routes[path] = route

    def json(self, path: str, document: Any, status: int = 200) -> None:
        """Serve ``document`` as JSON at ``path``."""

        self.route(path, json_reply(document, status))

    def answer(self, request: OAuthRequest) -> Reply:
        with self.lock:
            self._requests.append(request)
            route = self._routes.get(request.path)
        if route is None:
            return Reply(404)
        return route if isinstance(route, Reply) else route(request)

    @property
    def requests(self) -> list[OAuthRequest]:
        """Every request received so far, in order."""

        with self.lock:
            return list(self._requests)

    @property
    def paths(self) -> list[str]:
        """The paths of the requests received so far, in order."""

        return [request.path for request in self.requests]

    def close(self) -> None:
        """Stop serving and end every hanging request."""

        self.stopped.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()

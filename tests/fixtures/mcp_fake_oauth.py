"""A fake authorization server and protected-resource metadata endpoint.

Standard library only, and no code shared with ``pipy_harness``. Import it
with ``tests/fixtures`` on ``sys.path``; it runs in the test's own process:

    with FakeOAuthServer() as server:
        server.json("/.well-known/oauth-authorization-server", {...})
        ... server.origin, server.requests, server.paths

A request is answered by the route of its path: a ``Reply``, or a function
that is given the ``OAuthRequest`` and returns one. A path without a route is
answered 404. Every response closes its connection.

``FakeAuthorizationServer`` is such a server with the endpoints of a sign-in:
metadata, dynamic registration, authorize and token.
``FakeOAuthMcpServer`` is such an authorization server with an MCP server at
``/mcp`` that accepts the access tokens it issued.
``authorization_response`` asks its authorize endpoint as a browser would and
returns the parameters of the redirect. ``FakeBrowser`` follows the redirect
too, to the page a loopback callback server answers with.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import socket
import sys
import threading
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self


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

    do_GET = do_POST = do_DELETE = _handle


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

    def __enter__(self) -> Self:
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


def error_reply(code: str, status: int = 400, description: str | None = None) -> Reply:
    """An OAuth error answer (RFC 6749, section 5.2)."""

    document = {"error": code}
    if description is not None:
        document["error_description"] = description
    return json_reply(document, status)


def form(request: OAuthRequest) -> dict[str, str]:
    """The fields of a form-encoded request body."""

    return dict(urllib.parse.parse_qsl(request.body.decode("utf-8"), True))


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class FakeAuthorizationServer(FakeOAuthServer):
    """An authorization server whose issuer is its origin.

    It serves its metadata at ``/.well-known/oauth-authorization-server``,
    registers clients at ``/register``, redirects from ``/authorize`` with a
    code, and at ``/token`` exchanges a code (checking client, redirect URI
    and the PKCE verifier) or a refresh token for tokens named
    ``access-<n>`` and ``refresh-<n>``.

    ``metadata`` changes fields of the metadata document; a ``None`` value
    removes one. With ``serve_metadata=False`` there is no document. A server
    whose metadata has ``client_id_metadata_document_supported`` knows every
    client whose ID is an ``https`` URL, without a registration.

    A test may set, at any time:

    - ``rotate_refresh_tokens``: a refresh answers with a new refresh token
      and the old one stops working (the default); else it answers with none.
    - ``send_iss``: the authorization response carries ``iss``.
    - ``response_iss``: the ``iss`` it carries instead of the server's own.
    - ``redirect_path``: the path the browser is redirected to instead of
      the redirect URI's, as a mixed-up authorization server would.
    - ``granted_scope``: the ``scope`` of token answers; ``None`` for none.
    - ``expires_in``: the ``expires_in`` of token answers; ``None`` for none.
    - ``token_replies``: answers to the next token requests, used up in order
      before the server answers by itself again.
    """

    def __init__(
        self,
        *,
        metadata: Mapping[str, Any] | None = None,
        serve_metadata: bool = True,
    ) -> None:
        super().__init__()
        self.rotate_refresh_tokens = True
        self.send_iss = False
        self.response_iss: str | None = None
        self.redirect_path: str | None = None
        self.granted_scope: str | None = None
        self.expires_in: int | None = 3600
        self.token_replies: list[Reply] = []
        self.clients: dict[str, dict[str, Any]] = {}
        self.refresh_tokens: dict[str, str] = {}
        self._codes: dict[str, dict[str, str]] = {}
        self._count = 0
        document: dict[str, Any] = {
            "issuer": self.origin,
            "authorization_endpoint": f"{self.origin}/authorize",
            "token_endpoint": f"{self.origin}/token",
            "registration_endpoint": f"{self.origin}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
        }
        for key, value in (metadata or {}).items():
            if value is None:
                document.pop(key, None)
            else:
                document[key] = value
        self.metadata = document
        if serve_metadata:
            self.json("/.well-known/oauth-authorization-server", document)
        self.route("/register", self._register)
        self.route("/authorize", self._authorize)
        self.route("/token", self._token)

    def _next(self, kind: str) -> str:
        self._count += 1
        return f"{kind}-{self._count}"

    def add_client(self, client_id: str, **fields: Any) -> None:
        """Know ``client_id`` without a registration request."""

        with self.lock:
            self.clients[client_id] = {"client_id": client_id, **fields}

    def requests_to(self, path: str) -> list[OAuthRequest]:
        return [request for request in self.requests if request.path == path]

    @property
    def registrations(self) -> list[dict[str, Any]]:
        """The documents of the registration requests received so far."""

        return [json.loads(request.body) for request in self.requests_to("/register")]

    @property
    def authorizations(self) -> list[dict[str, str]]:
        """The parameters of the authorization requests received so far."""

        return [
            dict(urllib.parse.parse_qsl(request.query, True))
            for request in self.requests_to("/authorize")
        ]

    def _knows(self, client_id: str | None) -> bool:
        if client_id in self.clients:
            return True
        return bool(
            client_id
            and client_id.startswith("https://")
            and self.metadata.get("client_id_metadata_document_supported")
        )

    @property
    def token_forms(self) -> list[dict[str, str]]:
        """The form fields of the token requests received so far."""

        return [form(request) for request in self.requests_to("/token")]

    def _register(self, request: OAuthRequest) -> Reply:
        document = json.loads(request.body)
        with self.lock:
            client_id = self._next("client")
            self.clients[client_id] = {**document, "client_id": client_id}
            return json_reply(self.clients[client_id], 201)

    def _authorize(self, request: OAuthRequest) -> Reply:
        query = dict(urllib.parse.parse_qsl(request.query, True))
        with self.lock:
            if not self._knows(query.get("client_id")):
                return error_reply("invalid_client")
            if (
                query.get("response_type") != "code"
                or query.get("code_challenge_method") != "S256"
                or not query.get("code_challenge")
                or not query.get("redirect_uri")
            ):
                return error_reply("invalid_request")
            code = self._next("code")
            self._codes[code] = query
            answer = {"code": code}
            if "state" in query:
                answer["state"] = query["state"]
            if self.response_iss is not None:
                answer["iss"] = self.response_iss
            elif self.send_iss:
                answer["iss"] = self.origin
            redirect = urllib.parse.urlsplit(query["redirect_uri"])
            if self.redirect_path is not None:
                redirect = redirect._replace(path=self.redirect_path)
        location = f"{redirect.geturl()}?{urllib.parse.urlencode(answer)}"
        return Reply(302, {"Location": location})

    def _client_of(self, request: OAuthRequest, fields: dict[str, str]) -> str | None:
        authorization = request.headers.get("authorization", "")
        if authorization.startswith("Basic "):
            return base64.b64decode(authorization[6:]).decode().split(":")[0]
        return fields.get("client_id")

    def _new_refresh_token(self, client_id: str) -> str:
        refresh_token = self._next("refresh")
        self.refresh_tokens[refresh_token] = client_id
        return refresh_token

    def _grant(self, client_id: str, fields: dict[str, str]) -> dict[str, Any] | str:
        """The grant's part of a token answer, or an error code."""

        if fields.get("grant_type") == "authorization_code":
            grant = self._codes.pop(fields.get("code", ""), None)
            if (
                grant is None
                or grant["client_id"] != client_id
                or grant["redirect_uri"] != fields.get("redirect_uri")
                or grant["code_challenge"] != _s256(fields.get("code_verifier", ""))
            ):
                return "invalid_grant"
            return {"refresh_token": self._new_refresh_token(client_id)}
        if fields.get("grant_type") == "refresh_token":
            refresh_token = fields.get("refresh_token", "")
            if self.refresh_tokens.get(refresh_token) != client_id:
                return "invalid_grant"
            if not self.rotate_refresh_tokens:
                return {}
            del self.refresh_tokens[refresh_token]
            return {"refresh_token": self._new_refresh_token(client_id)}
        return "unsupported_grant_type"

    def _token(self, request: OAuthRequest) -> Reply:
        fields = form(request)
        with self.lock:
            if self.token_replies:
                return self.token_replies.pop(0)
            client_id = self._client_of(request, fields)
            if client_id is None or not self._knows(client_id):
                return error_reply("invalid_client", 401)
            grant = self._grant(client_id, fields)
            if isinstance(grant, str):
                return error_reply(grant)
            answer = {
                "token_type": "Bearer",
                "access_token": self._next("access"),
                **grant,
            }
            if self.granted_scope is not None:
                answer["scope"] = self.granted_scope
            if self.expires_in is not None:
                answer["expires_in"] = self.expires_in
            return json_reply(answer)


class FakeOAuthMcpServer(FakeAuthorizationServer):
    """An MCP server at ``/mcp`` that this authorization server protects.

    A POST without an access token the server issued and has not expired is
    answered 401 with a challenge that names the protected-resource metadata
    at ``/.well-known/oauth-protected-resource/mcp``. The MCP server has one
    tool, ``whoami``, whose result is ``token <access token>``, and assigns
    the session ``session-1``, so closing a connection sends a DELETE. A GET
    is answered 405.

    ``log`` lists what happened, in order: ``401 <token or none>``,
    ``register``, ``token code``, ``token refresh``, ``call <token>``. ``deletes`` lists
    the access tokens of the DELETE requests.

    A test may set ``insufficient_scope`` to a scope: a tool call is then
    answered 403 with a challenge that asks for it.
    """

    def __init__(self, **options: Any) -> None:
        super().__init__(**options)
        self.url = f"{self.origin}/mcp"
        self.log: list[str] = []
        self.deletes: list[str | None] = []
        self.insufficient_scope: str | None = None
        self._valid_tokens: set[str] = set()
        metadata_path = "/.well-known/oauth-protected-resource/mcp"
        self.challenge = f'Bearer resource_metadata="{self.origin}{metadata_path}"'
        self.json(
            metadata_path,
            {"resource": self.url, "authorization_servers": [self.origin]},
        )
        self.route("/mcp", self._mcp)

    def accept(self, token: str) -> None:
        """Accept ``token`` although this server did not issue it."""

        with self.lock:
            self._valid_tokens.add(token)

    def expire_access_tokens(self) -> None:
        """Stop accepting every access token issued so far."""

        with self.lock:
            self._valid_tokens.clear()

    def _register(self, request: OAuthRequest) -> Reply:
        with self.lock:
            self.log.append("register")
        return super()._register(request)

    def _token(self, request: OAuthRequest) -> Reply:
        reply = super()._token(request)
        if reply.status == 200 and reply.body:
            with self.lock:
                self._valid_tokens.add(json.loads(reply.body)["access_token"])
                grant = form(request).get("grant_type")
                kind = "code" if grant == "authorization_code" else "refresh"
                self.log.append(f"token {kind}")
        return reply

    def _mcp(self, request: OAuthRequest) -> Reply:
        authorization = request.headers.get("authorization")
        token = None if authorization is None else authorization.removeprefix("Bearer ")
        if request.method == "DELETE":
            with self.lock:
                self.deletes.append(token)
            return Reply(200)
        if request.method != "POST":
            return Reply(405)
        with self.lock:
            if token not in self._valid_tokens:
                self.log.append(f"401 {token or 'none'}")
                return Reply(401, {"WWW-Authenticate": self.challenge})
        message = json.loads(request.body)
        if "id" not in message:
            return Reply(202)
        method = message["method"]
        result: dict[str, Any] = {}
        if method == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "issues", "version": "1.0.0"},
            }
        elif method == "tools/list":
            schema = {"type": "object", "properties": {}}
            result = {"tools": [{"name": "whoami", "inputSchema": schema}]}
        elif method == "tools/call":
            scope = self.insufficient_scope
            if scope is not None:
                challenge = f'Bearer error="insufficient_scope", scope="{scope}"'
                return Reply(403, {"WWW-Authenticate": challenge})
            with self.lock:
                self.log.append(f"call {token}")
            result = {"content": [{"type": "text", "text": f"token {token}"}]}
        reply = json_reply({"jsonrpc": "2.0", "id": message["id"], "result": result})
        return Reply(
            reply.status, {**reply.headers, "Mcp-Session-Id": "session-1"}, reply.body
        )


def authorization_response(authorization_url: str) -> dict[str, str]:
    """Ask an authorize endpoint as a browser would, without following it.

    Returns the query parameters of the redirect the server answered with.
    """

    parts = urllib.parse.urlsplit(authorization_url)
    assert parts.hostname is not None
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        connection.request("GET", f"{parts.path}?{parts.query}")
        response = connection.getresponse()
        body = response.read()
        assert response.status == 302, (response.status, body)
        location = response.getheader("Location") or ""
    finally:
        connection.close()
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(location).query, True))


@dataclass(frozen=True)
class BrowserPage:
    """What a ``FakeBrowser`` was answered with at ``url``."""

    url: str
    status: int
    headers: dict[str, str]
    text: str


class FakeBrowser:
    """Opens a URL as a browser would and follows its redirects.

    ``visited`` lists every URL it asked, in order.
    """

    def __init__(self) -> None:
        self.visited: list[str] = []

    def get(self, url: str, method: str = "GET") -> BrowserPage:
        """One request, without following a redirect. Header names are lower case."""

        parts = urllib.parse.urlsplit(url)
        assert parts.scheme == "http" and parts.hostname is not None, url
        target = parts.path or "/"
        if parts.query:
            target += f"?{parts.query}"
        self.visited.append(url)
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
        try:
            connection.request(method, target)
            response = connection.getresponse()
            body = response.read()
            headers = {name.lower(): value for name, value in response.getheaders()}
            return BrowserPage(url, response.status, headers, body.decode("utf-8"))
        finally:
            connection.close()

    def open(self, url: str) -> BrowserPage:
        """Ask ``url`` and follow redirects to the page a user would end on."""

        for _ in range(10):
            page = self.get(url)
            if page.status not in (301, 302, 303, 307, 308):
                return page
            url = urllib.parse.urljoin(url, page.headers["location"])
        raise AssertionError(f"too many redirects, last to {url}")

"""Tests for connections to MCP servers that sign in with OAuth (MCP parity
plan S30).

Rows CON-13, CON-14, OAU-01 and OAU-23: which servers use OAuth, the
``needs-auth`` state, the stored token on a connection's requests and its
refresh, and what closing waits for. The rules are tested against an
in-memory server whose transport fails the way the HTTP transport does, as in
Pi's ``mcp-extension.test.ts``, and then with the default transport factory
against the fake MCP server that an authorization server protects. A mirrored
Pi case is named in a ``Pi:`` comment. The sign-in itself is made with the
flow of the OAuth library.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from test_native_mcp_auth_provider import (
    _gate_token_endpoint,
    _in_thread,
    _sign_in,
    _token_requests,
)
from test_native_mcp_connection import _OK, _Harness, _Server, _tool
from test_native_mcp_connection_http import _always, _Failing, _once_per_method

from pipy_harness.native.mcp.config import McpServerEntry
from pipy_harness.native.mcp.config_values import McpConfigValueError
from pipy_harness.native.mcp.connection import (
    McpServerConnection,
    McpServerConnectionError,
    sign_in_required_message,
    uses_oauth,
)
from pipy_harness.native.mcp.credentials import (
    McpOAuthCredentialStore,
    McpOAuthServerStore,
    McpOAuthSettings,
    McpServerAuthProvider,
    mcp_auth_path,
)
from pipy_harness.native.mcp.http import (
    McpAuthRequiredError,
    McpHttpError,
    McpSessionExpiredError,
    UnauthorizedContext,
)
from pipy_harness.native.mcp.oauth.errors import McpOAuthAuthorizationRequiredError
from pipy_harness.native.mcp.oauth.types import OAuthChallenge
from pipy_harness.native.mcp.protocol import (
    JsonRpcId,
    JsonRpcMessage,
    McpConnectionClosedError,
    is_json_rpc_id,
)
from pipy_harness.native.mcp.transport import InMemoryTransport, McpTransport

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import FakeOAuthMcpServer, Reply, error_reply  # noqa: E402

_WAIT_SECONDS = 10.0
_OAUTH = {"url": "http://unused.invalid/mcp"}
_SIGN_IN = 'MCP server "fake" requires sign-in. Run /mcp to sign in.'


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


@pytest.fixture
def credentials(tmp_path: Path) -> McpOAuthCredentialStore:
    return McpOAuthCredentialStore(mcp_auth_path(tmp_path / "home"))


def _entry(config: dict[str, Any]) -> McpServerEntry:
    return McpServerEntry(name="fake", config=config, source="test")  # type: ignore[arg-type]


def _rejecting(server: _Server | None = None, *methods: str) -> Callable[[], _Failing]:
    """A transport that answers 401: always, or once for each of ``methods``."""

    error = McpAuthRequiredError("Bearer")
    fail = _once_per_method(error, *methods) if methods else _always(error)
    return lambda: _Failing((server or _Server(tools=[_tool("echo")])).client_end, fail)


# -- CON-13: which servers sign in with OAuth ---------------------------------


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"url": "https://mcp.example.com/mcp"}, True),
        ({"url": "https://mcp.example.com/mcp", "headers": {"X-Key": "k"}}, True),
        (
            {"url": "https://mcp.example.com/mcp", "headers": {"Authorization": "x"}},
            False,
        ),
        (
            {"url": "https://mcp.example.com/mcp", "headers": {"AUTHORIZATION": "x"}},
            False,
        ),
        ({"url": "https://mcp.example.com/mcp", "auth": {"provider": "radius"}}, False),
        ({"command": "server"}, False),
    ],
)
def test_an_http_server_uses_oauth_unless_it_has_an_authorization_header_or_auth(
    config: dict[str, Any], expected: bool, credentials: McpOAuthCredentialStore
) -> None:
    assert uses_oauth(_entry(config)) is expected
    harness = _Harness(
        [lambda: _Server(tools=[_tool("echo")]).client_end],
        config=config,
        credentials=credentials,
    )
    connection = harness.connection
    url = config["url"] if expected else None
    assert connection.oauth_url == url
    assert connection.view().oauth_url == url

    connection.get_client()

    (provider,) = harness.auth_providers
    assert isinstance(provider, McpServerAuthProvider) is expected
    # A server with ``auth`` has the provider's token instead (S33).
    assert expected or (provider is None) is ("auth" not in config)
    connection.close()


def test_a_connection_without_a_credential_store_sends_no_token() -> None:
    harness = _Harness([_rejecting()], config=_OAUTH)
    connection = harness.connection
    assert connection.oauth_url == _OAUTH["url"]

    with pytest.raises(McpServerConnectionError, match="requires authentication"):
        connection.get_client()

    assert harness.auth_providers == [None]
    assert connection.state == "failed"
    assert connection.error == "MCP server requires authentication"
    connection.close()


def test_a_server_url_no_store_can_keep_gets_no_auth_provider(
    credentials: McpOAuthCredentialStore,
) -> None:
    harness = _Harness(
        [_rejecting()],
        config={"url": "https://user:secret@mcp.example.com/mcp"},
        credentials=credentials,
    )

    with pytest.raises(McpServerConnectionError, match="requires authentication"):
        harness.connection.get_client()

    assert harness.auth_providers == [None]
    assert harness.connection.state == "failed"
    harness.connection.close()


# -- CON-14: a server that needs a sign-in ------------------------------------


def test_asks_oauth_servers_that_keep_rejecting_requests_for_a_new_sign_in(
    credentials: McpOAuthCredentialStore,
) -> None:
    # Pi: asks OAuth servers that keep rejecting requests for a new sign-in
    harness = _Harness([_rejecting()], config=_OAUTH, credentials=credentials)
    connection = harness.connection

    with pytest.raises(McpServerConnectionError) as failure:
        connection.get_client()

    assert str(failure.value) == _SIGN_IN
    assert sign_in_required_message(connection.entry) == _SIGN_IN
    assert isinstance(failure.value.__cause__, McpAuthRequiredError)
    assert connection.state == "needs-auth"
    assert connection.error is None
    assert harness.states == ["connecting", "needs-auth"]
    assert len(harness.opened) == 1
    connection.close()
    assert connection.state == "closed"


def test_a_server_with_an_authorization_header_that_answers_401_has_failed(
    credentials: McpOAuthCredentialStore,
) -> None:
    config = {"url": "http://unused.invalid/mcp", "headers": {"authorization": "x"}}
    harness = _Harness([_rejecting()], config=config, credentials=credentials)

    with pytest.raises(McpServerConnectionError, match="failed to connect"):
        harness.connection.get_client()

    assert harness.connection.state == "failed"
    assert harness.connection.error == "MCP server requires authentication"
    harness.connection.close()


def test_the_auth_providers_need_to_sign_in_is_needs_auth_for_any_server() -> None:
    def refuse() -> McpTransport:
        return _Failing(
            _Server(tools=[_tool("echo")]).client_end,
            _always(McpOAuthAuthorizationRequiredError()),
        )

    harness = _Harness([refuse], config={"command": "unused"})

    with pytest.raises(McpServerConnectionError) as failure:
        harness.connection.get_client()

    assert str(failure.value) == _SIGN_IN
    assert harness.connection.state == "needs-auth"
    harness.connection.close()


def test_a_request_that_needs_a_sign_in_closes_the_client_and_the_next_connects_again(
    credentials: McpOAuthCredentialStore,
) -> None:
    first = _Server(tools=[_tool("echo")])
    second = _Server(tools=[_tool("echo")], handlers={"tools/call": lambda m: _OK})
    transports: list[_Failing] = []

    def rejecting() -> McpTransport:
        transports.append(_rejecting(first, "tools/call")())
        return transports[-1]

    harness = _Harness(
        [rejecting, lambda: second.client_end], config=_OAUTH, credentials=credentials
    )
    connection = harness.connection
    connection.get_client()

    with pytest.raises(McpServerConnectionError) as failure:
        connection.call_tool("echo", {})

    assert str(failure.value) == _SIGN_IN
    assert connection.state == "needs-auth"
    assert connection.error is None
    assert transports[0].closes == 1
    # The request was not made a second time.
    assert first.methods.count("tools/call") == 0
    assert len(harness.opened) == 1
    # Its tools are kept, as for a server that is disconnected.
    assert [tool["name"] for tool in connection.tools] == ["echo"]

    # The next call tries again, with what is stored by then.
    assert connection.call_tool("echo", {}) == _OK
    assert connection.state == "connected"
    assert harness.states[-3:] == ["needs-auth", "connecting", "connected"]
    connection.close()


class _Deferred(_Failing):
    """A transport that keeps calls of the tool ``slow`` unanswered until the
    test ends them with ``fail_held``, as an HTTP request whose answer is
    late, and answers the first call of the tool ``gone`` with "session
    expired"."""

    def __init__(self, transport: InMemoryTransport) -> None:
        super().__init__(transport, self._expire_once)
        self.held: list[JsonRpcId] = []
        self._expired = False

    def _expire_once(self, message: JsonRpcMessage) -> Exception | None:
        if self._tool(message) != "gone" or self._expired:
            return None
        self._expired = True
        return McpSessionExpiredError()

    @staticmethod
    def _tool(message: JsonRpcMessage) -> object:
        if message.get("method") != "tools/call":
            return None
        params: Any = message["params"]
        return params["name"]

    def send(self, message: JsonRpcMessage) -> None:
        if self._tool(message) == "slow":
            request_id = message["id"]
            assert is_json_rpc_id(request_id)
            self.held.append(request_id)
            return
        super().send(message)

    def fail_held(self, error: Exception) -> None:
        for request_id in self.held:
            self._transport.emit_request_failure(request_id, error)


def test_a_late_need_to_sign_in_leaves_a_newer_connection_alone(
    credentials: McpOAuthCredentialStore,
) -> None:
    first = _Deferred(_Server(tools=[_tool("slow"), _tool("gone")]).client_end)
    second = _Server(tools=[_tool("gone")], handlers={"tools/call": lambda m: _OK})
    harness = _Harness(
        [lambda: first, lambda: second.client_end],
        config=_OAUTH,
        credentials=credentials,
    )
    connection = harness.connection
    slow, _, raised = _in_thread(lambda: connection.call_tool("slow", {}))
    _wait_until(lambda: len(first.held) == 1)
    # The server forgot the session: this call moves to a new connection,
    # while the slow one is still in flight on the old client.
    assert connection.call_tool("gone", {}) == _OK
    assert connection.state == "connected"
    assert first.closes == 0

    first.fail_held(McpAuthRequiredError("Bearer"))
    slow.join(_WAIT_SECONDS)

    # The late request fails as it must, and its client is closed.
    assert [str(error) for error in raised] == [_SIGN_IN]
    assert first.closes == 1
    # The connection that replaced it is not the one that was rejected.
    assert connection.state == "connected"
    assert "needs-auth" not in harness.states
    assert connection.call_tool("gone", {}) == _OK
    assert len(harness.opened) == 2
    connection.close()


def test_a_request_rejected_while_its_client_is_announced_needs_a_sign_in(
    credentials: McpOAuthCredentialStore,
) -> None:
    # The connect that made the client is still in progress while the
    # change to `connected` is announced. It is not a newer connect.
    failures: list[str] = []

    def call_at_once(connection: McpServerConnection) -> None:
        if connection.state == "connected" and not failures:
            try:
                connection.call_tool("echo", {})
            except McpServerConnectionError as error:
                failures.append(str(error))

    transports: list[_Failing] = []

    def rejecting() -> McpTransport:
        transports.append(_rejecting(None, "tools/call")())
        return transports[-1]

    harness = _Harness(
        [rejecting], config=_OAUTH, credentials=credentials, on_change=call_at_once
    )
    connection = harness.connection

    connection.get_client()

    assert failures == [_SIGN_IN]
    assert transports[0].closes == 1
    assert connection.state == "needs-auth"
    assert harness.states[-2:] == ["connected", "needs-auth"]
    connection.close()


def test_a_resource_request_that_needs_a_sign_in_is_not_made_again(
    credentials: McpOAuthCredentialStore,
) -> None:
    server = _Server(tools=[_tool("echo")])
    harness = _Harness(
        [_rejecting(server, "resources/read"), lambda: server.client_end],
        config=_OAUTH,
        credentials=credentials,
    )

    with pytest.raises(McpServerConnectionError) as failure:
        harness.connection.read_resource("docs://a")

    assert str(failure.value) == _SIGN_IN
    assert harness.connection.state == "needs-auth"
    assert len(harness.opened) == 1
    harness.connection.close()


def test_a_401_of_a_server_without_oauth_fails_only_the_request() -> None:
    server = _Server(tools=[_tool("echo")], handlers={"tools/call": lambda m: _OK})
    harness = _Harness(
        [_rejecting(server, "tools/call")],
        config={"url": "http://unused.invalid", "headers": {"Authorization": "x"}},
    )
    connection = harness.connection

    with pytest.raises(McpAuthRequiredError):
        connection.call_tool("echo", {})

    assert connection.state == "connected"
    assert connection.call_tool("echo", {}) == _OK
    connection.close()


def test_a_connect_that_needs_a_sign_in_after_close_is_closed(
    credentials: McpOAuthCredentialStore,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def slow() -> McpTransport:
        entered.set()
        release.wait(_WAIT_SECONDS)
        raise McpOAuthAuthorizationRequiredError()

    harness = _Harness([slow], config=_OAUTH, credentials=credentials)
    thread, _, raised = _in_thread(harness.connection.get_client)
    assert entered.wait(_WAIT_SECONDS)
    closing, _, _ = _in_thread(harness.connection.close)
    _wait_until(lambda: harness.connection.state == "closed")

    release.set()
    thread.join(_WAIT_SECONDS)
    closing.join(_WAIT_SECONDS)

    assert [str(error) for error in raised] == [
        'MCP server "fake" failed to connect: '
        "MCP OAuth authorization requires user interaction"
    ]
    assert harness.connection.state == "closed"
    assert "needs-auth" not in harness.states


def test_sign_out_disconnects_and_leaves_the_server_in_need_of_a_sign_in(
    credentials: McpOAuthCredentialStore,
) -> None:
    server = _Server(tools=[_tool("echo")])
    closes: list[int] = []

    def transport() -> McpTransport:
        end = _Failing(server.client_end, lambda message: None)
        closes.append(0)
        end._transport.on_close(lambda: closes.append(1))
        return end

    harness = _Harness([transport], config=_OAUTH, credentials=credentials)
    connection = harness.connection
    connection.get_client()

    connection.sign_out()

    assert connection.state == "needs-auth"
    assert connection.error is None
    assert closes == [0, 1]
    # Not reported as a connection that dropped.
    assert "disconnected" not in harness.states

    connection.close()
    connection.sign_out()
    assert connection.state == "closed"


# -- OAU-01, OAU-23: against a protected server -------------------------------


class _Protected:
    """A connection to the fake protected server, with the server's store."""

    def __init__(
        self,
        server: FakeOAuthMcpServer,
        credentials: McpOAuthCredentialStore,
        oauth: dict[str, Any] | None = None,
    ) -> None:
        self.server = server
        self.store: McpOAuthServerStore = credentials.for_server("fake", server.url)
        self.states: list[str] = []
        config: dict[str, Any] = {"url": server.url}
        if oauth is not None:
            config["oauth"] = oauth
        self.connection = McpServerConnection(
            entry=_entry(config),
            cwd=Path.cwd(),
            client_version="1.2.3",
            on_tools=lambda connection: None,
            on_change=lambda connection: self.states.append(connection.state),
            credentials=credentials,
        )

    def whoami(self) -> str:
        result: Any = self.connection.call_tool("whoami", {})
        text: str = result["content"][0]["text"]
        return text.removeprefix("token ")

    def expire_in(self, seconds: float) -> None:
        state = self.store.load()
        assert state is not None
        state["tokensExpireAt"] = int((time.time() + seconds) * 1000)
        self.store.save(state)


@pytest.fixture
def server() -> Iterator[FakeOAuthMcpServer]:
    with FakeOAuthMcpServer() as fake:
        yield fake
    _wait_until(
        lambda: (
            not [
                thread
                for thread in threading.enumerate()
                if thread.name.startswith(("mcp-oauth-fetch", "mcp-http"))
            ]
        )
    )


@pytest.fixture
def protected(
    server: FakeOAuthMcpServer, credentials: McpOAuthCredentialStore
) -> Iterator[_Protected]:
    fixture = _Protected(server, credentials)
    try:
        yield fixture
    finally:
        server.route("/token", server._token)
        fixture.connection.close()


def test_a_server_nobody_signed_in_to_needs_a_sign_in(protected: _Protected) -> None:
    server = protected.server
    connection = protected.connection

    with pytest.raises(McpServerConnectionError) as failure:
        connection.get_client()

    assert str(failure.value) == _SIGN_IN
    assert connection.state == "needs-auth"
    assert connection.view().oauth_url == server.url
    # The challenge is kept for the sign-in.
    assert connection.challenge == OAuthChallenge(
        resource_metadata_url=f"{server.origin}/.well-known/oauth-protected-resource/mcp"
    )
    # The request went out without a token, and nothing else was asked: a
    # connection opens no browser and registers no client.
    assert server.log == ["401 none"]
    assert server.paths == ["/mcp"]
    assert not protected.store._credentials.path.exists()


def test_connects_with_the_stored_token_and_refreshes_it_when_it_is_rejected(
    protected: _Protected,
) -> None:
    server = protected.server
    first = _sign_in(server, protected.store)

    assert protected.whoami() == first
    assert protected.connection.state == "connected"
    assert protected.connection.challenge is None

    server.expire_access_tokens()
    refreshed = protected.whoami()

    assert refreshed != first
    assert server.log.count("token refresh") == 1
    assert protected.connection.state == "connected"
    assert "needs-auth" not in protected.states
    # It was the same client: nothing connected again.
    assert protected.states.count("connected") == 1
    protected.connection.close()
    assert server.deletes == [refreshed]


def test_a_refresh_the_server_refuses_needs_a_sign_in_and_a_new_one_is_used(
    protected: _Protected,
) -> None:
    server = protected.server
    _sign_in(server, protected.store)
    connection = protected.connection
    connection.get_client()
    server.expire_access_tokens()
    server.token_replies.append(error_reply("invalid_grant"))

    with pytest.raises(McpServerConnectionError) as failure:
        connection.call_tool("whoami", {})

    assert str(failure.value) == _SIGN_IN
    assert isinstance(failure.value.__cause__, McpOAuthAuthorizationRequiredError)
    assert connection.state == "needs-auth"
    with pytest.raises(McpServerConnectionError, match="requires sign-in"):
        connection.get_client()

    # The user signs in again, somewhere: the next call finds the tokens.
    again = _sign_in(server, protected.store)
    assert protected.whoami() == again
    assert connection.state == "connected"


def test_a_server_that_asks_for_more_scope_needs_a_sign_in(
    protected: _Protected,
) -> None:
    server = protected.server
    _sign_in(server, protected.store)
    protected.connection.get_client()
    server.insufficient_scope = "admin"
    before = _token_requests(server)

    with pytest.raises(McpServerConnectionError) as failure:
        protected.connection.call_tool("whoami", {})

    assert str(failure.value) == _SIGN_IN
    assert protected.connection.state == "needs-auth"
    assert protected.connection.challenge == OAuthChallenge(
        scope="admin", error="insufficient_scope"
    )
    assert _token_requests(server) == before


def test_closes_without_refreshing_an_expiring_token(protected: _Protected) -> None:
    # Pi: closes the session without refreshing an expiring token
    server = protected.server
    first = _sign_in(server, protected.store)
    protected.connection.get_client()
    # Still accepted, but so close to its expiry that the next request would
    # refresh it first.
    protected.expire_in(10)
    server.route("/token", Reply(hang=True))
    before = _token_requests(server)
    started = time.monotonic()

    protected.connection.close()

    assert time.monotonic() - started < 2
    assert _token_requests(server) == before
    assert server.deletes == [first]


def test_closing_lets_no_refresh_start_any_more(
    credentials: McpOAuthCredentialStore,
) -> None:
    harness = _Harness(
        [lambda: _Server(tools=[_tool("echo")]).client_end],
        config=_OAUTH,
        credentials=credentials,
    )
    harness.connection.get_client()
    (provider,) = harness.auth_providers
    assert isinstance(provider, McpServerAuthProvider)

    harness.connection.close()

    # A request thread that outlived the close gets no refresh.
    with pytest.raises(McpConnectionClosedError):
        provider.on_unauthorized(
            UnauthorizedContext(401, "Bearer", _OAUTH["url"], None)
        )
    assert harness.connection.challenge == OAuthChallenge()


def test_closing_waits_for_a_running_refresh_to_save_its_tokens(
    protected: _Protected,
) -> None:
    server = protected.server
    first = _sign_in(server, protected.store)
    connection = protected.connection
    connection.get_client()
    server.expire_access_tokens()
    gate = _gate_token_endpoint(server)
    before = _token_requests(server)
    call, _, raised = _in_thread(lambda: connection.call_tool("whoami", {}))
    _wait_until(lambda: _token_requests(server) == before + 1)

    closing, _, _ = _in_thread(connection.close)
    closing.join(0.2)
    assert closing.is_alive()
    # The session is over for the server already.
    assert connection.state == "closed"
    assert server.deletes == [first]

    gate.set()
    closing.join(_WAIT_SECONDS)
    assert not closing.is_alive()
    state = protected.store.load()
    assert state is not None and state["tokens"]["access_token"] != first
    call.join(_WAIT_SECONDS)
    assert len(raised) == 1


def test_the_oauth_settings_come_from_the_entry(
    server: FakeOAuthMcpServer,
    credentials: McpOAuthCredentialStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oauth = {
        "clientId": "client",
        "clientSecret": "${PIPY_TEST_OAUTH_SECRET}",
        "callbackPort": 8123,
        "callbackUrl": "http://localhost/cb",
        "scope": "issues:read",
        "clientName": "Claude Code",
        "clientRegistration": "dcr",
        "clientMetadataBaseUrl": "https://id.example/oauth",
        "authServerMetadataUrl": "https://as.example/metadata",
    }
    fixture = _Protected(server, credentials, oauth)
    monkeypatch.setenv("PIPY_TEST_OAUTH_SECRET", "s3cret")

    assert fixture.connection.oauth_settings() == McpOAuthSettings(
        client_id="client",
        client_secret="s3cret",
        callback_port=8123,
        callback_url="http://localhost/cb",
        scope="issues:read",
        client_name="Claude Code",
        client_registration="dcr",
        client_metadata_base_url="https://id.example/oauth",
        auth_server_metadata_url="https://as.example/metadata",
    )
    assert _Protected(server, credentials).connection.oauth_settings() == (
        McpOAuthSettings()
    )
    fixture.connection.close()


def test_a_client_secret_that_cannot_be_resolved_fails_the_refresh_only(
    server: FakeOAuthMcpServer,
    credentials: McpOAuthCredentialStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PIPY_TEST_OAUTH_SECRET", raising=False)
    fixture = _Protected(
        server, credentials, {"clientSecret": "${PIPY_TEST_OAUTH_SECRET}"}
    )
    first = _sign_in(server, fixture.store)
    try:
        # Connecting and calling need no secret.
        assert fixture.whoami() == first

        server.expire_access_tokens()
        with pytest.raises(McpConfigValueError, match='"fake" oauth.clientSecret'):
            fixture.connection.call_tool("whoami", {})
        assert fixture.connection.state == "connected"

        monkeypatch.setenv("PIPY_TEST_OAUTH_SECRET", "s3cret")
        assert fixture.whoami() != first
    finally:
        fixture.connection.close()


def test_other_http_errors_of_an_oauth_server_are_errors(
    protected: _Protected,
) -> None:
    server = protected.server
    _sign_in(server, protected.store)
    protected.connection.get_client()
    server.route("/mcp", Reply(500, body=b"broken"))

    with pytest.raises(McpHttpError, match="status 500: broken"):
        protected.connection.call_tool("whoami", {})

    assert protected.connection.state == "connected"
    server.route("/mcp", server._mcp)

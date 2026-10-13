"""Tests for the auth provider of an OAuth server's connections.

Tests that mirror a case of Pi's
``packages/coding-agent/test/mcp-oauth-refresh.test.ts`` name it in a ``Pi:``
comment. A sign-in is made with the flow of the OAuth library, since the
sign-in of a session is not part of what is tested here.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.mcp import credentials as credentials_module
from pipy_harness.native.mcp.client import McpClient, McpClientOptions
from pipy_harness.native.mcp.credentials import (
    McpOAuthCredentialStore,
    McpOAuthServerStore,
    McpOAuthSettings,
    McpRefreshLockTimeoutError,
    McpServerAuthProvider,
    create_oauth_provider,
    fixed_redirect_url,
    mcp_auth_path,
)
from pipy_harness.native.mcp.http import (
    StreamableHttpTransport,
    StreamableHttpTransportOptions,
    UnauthorizedContext,
)
from pipy_harness.native.mcp.oauth.errors import McpOAuthAuthorizationRequiredError
from pipy_harness.native.mcp.oauth.flow import OAuthFlowOptions, authorize_mcp
from pipy_harness.native.mcp.oauth.types import OAuthChallenge

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import (  # noqa: E402
    FakeOAuthMcpServer,
    OAuthRequest,
    Reply,
    authorization_response,
    error_reply,
)

_WAIT_SECONDS = 10.0
_REDIRECT = "http://127.0.0.1/callback"

# One process of the two-process test: it finds the token named on its
# command line rejected and prints the token it has afterwards.
_REFRESH_IN_A_PROCESS = """
import sys
from pipy_harness.native.mcp.credentials import (
    McpOAuthCredentialStore,
    McpOAuthSettings,
    McpServerAuthProvider,
)
from pipy_harness.native.mcp.http import UnauthorizedContext

path, url, stale = sys.argv[1:4]
provider = McpServerAuthProvider(
    server_url=url,
    store=McpOAuthCredentialStore(path).for_server("test", url),
    settings=McpOAuthSettings,
    on_challenge=lambda challenge: None,
)
print("ready", flush=True)
sys.stdin.readline()
provider.on_unauthorized(UnauthorizedContext(401, None, url, stale))
print(provider.token(), flush=True)
"""


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


@pytest.fixture
def server() -> Iterator[FakeOAuthMcpServer]:
    with FakeOAuthMcpServer() as fake:
        yield fake
    # No test leaves a request thread behind.
    _wait_until(
        lambda: not [t for t in threading.enumerate() if t.name == "mcp-oauth-fetch"]
    )


class _Process:
    """What one process has: its own store object and provider on the
    credentials file every process shares."""

    def __init__(
        self,
        server: FakeOAuthMcpServer,
        path: Path,
        settings: Callable[[], McpOAuthSettings] = McpOAuthSettings,
        **store_options: Any,
    ) -> None:
        self.url = server.url
        self.store: McpOAuthServerStore = McpOAuthCredentialStore(
            path, **store_options
        ).for_server("test", server.url)
        self.challenges: list[OAuthChallenge] = []
        self.provider = McpServerAuthProvider(
            server_url=server.url,
            store=self.store,
            settings=settings,
            on_challenge=self.challenges.append,
        )

    def access_token(self) -> str | None:
        state = self.store.load()
        tokens = (state or {}).get("tokens") or {}
        token: str | None = tokens.get("access_token")
        return token

    def reject(self, token: str | None, challenge: str | None = None) -> None:
        """The server answered a request that carried ``token`` with 401."""

        self.provider.on_unauthorized(
            UnauthorizedContext(401, challenge, self.url, token)
        )

    def change(self, **fields: Any) -> None:
        """Change fields of the stored state; ``None`` removes one."""

        state = self.store.load()
        assert state is not None
        for key, value in fields.items():
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value
        self.store.save(state)

    def expire_in(self, seconds: float) -> None:
        self.change(tokensExpireAt=int((time.time() + seconds) * 1000))


class _SignedIn:
    """A server the user signed in to, and the processes that use it."""

    def __init__(self, server: FakeOAuthMcpServer, path: Path) -> None:
        self.server = server
        self.path = path
        first = self.process()
        urls: list[str] = []
        provider = create_oauth_provider(
            server.url, first.store, McpOAuthSettings(), _REDIRECT, urls.append
        )
        assert authorize_mcp(provider, OAuthFlowOptions(server.url)) == "REDIRECT"
        code = authorization_response(urls[0])["code"]
        again = OAuthFlowOptions(server.url, authorization_code=code)
        assert authorize_mcp(provider, again) == "AUTHORIZED"
        token = first.access_token()
        assert token is not None
        self.first_token = token

    def process(self, **options: Any) -> _Process:
        return _Process(self.server, self.path, **options)

    def refreshes(self) -> int:
        return self.server.log.count("token refresh")

    def lock_files(self) -> list[str]:
        return sorted(
            path.name
            for path in self.path.parent.iterdir()
            if path.name.startswith("mcp-auth-refresh-")
        )


@pytest.fixture
def signed_in(server: FakeOAuthMcpServer, tmp_path: Path) -> _SignedIn:
    return _SignedIn(server, mcp_auth_path(tmp_path / "home"))


def _gate_token_endpoint(server: FakeOAuthMcpServer) -> threading.Event:
    """Hold every token request until the returned event is set."""

    gate = threading.Event()
    answer = server._token

    def held(request: OAuthRequest) -> Reply:
        gate.wait(_WAIT_SECONDS)
        return answer(request)

    server.route("/token", held)
    return gate


def _token_requests(server: FakeOAuthMcpServer) -> int:
    return len(server.requests_to("/token"))


def _in_thread(
    call: Callable[[], object],
) -> tuple[threading.Thread, list[object], list[BaseException]]:
    results: list[object] = []
    raised: list[BaseException] = []

    def run() -> None:
        try:
            results.append(call())
        except Exception as error:  # noqa: BLE001 - cross-thread capture for the assertion
            raised.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, results, raised


# -- the token of a request ---------------------------------------------------


def test_sends_no_token_before_a_sign_in(
    server: FakeOAuthMcpServer, tmp_path: Path
) -> None:
    process = _Process(server, mcp_auth_path(tmp_path))

    assert process.provider.token() is None
    assert server.requests == []
    assert not mcp_auth_path(tmp_path).exists()


def test_sends_the_stored_access_token(signed_in: _SignedIn) -> None:
    process = signed_in.process()
    before = len(signed_in.server.requests)

    assert process.provider.token() == signed_in.first_token
    # Its expiry is an hour away, so nothing is asked of the server.
    assert len(signed_in.server.requests) == before


def test_refreshes_a_token_that_is_about_to_expire(signed_in: _SignedIn) -> None:
    process = signed_in.process()
    process.expire_in(31)
    assert process.provider.token() == signed_in.first_token
    assert signed_in.refreshes() == 0

    process.expire_in(29)
    refreshed = process.provider.token()

    assert refreshed not in (None, signed_in.first_token)
    assert refreshed == process.access_token()
    assert signed_in.refreshes() == 1
    form = signed_in.server.token_forms[-1]
    assert form["grant_type"] == "refresh_token"
    # The new token is an hour from its expiry.
    assert process.provider.token() == refreshed
    assert signed_in.refreshes() == 1


def test_an_expiring_token_without_a_refresh_token_is_sent_as_it_is(
    signed_in: _SignedIn,
) -> None:
    process = signed_in.process()
    process.change(
        tokens={"access_token": signed_in.first_token, "token_type": "Bearer"}
    )
    process.expire_in(-60)
    before = _token_requests(signed_in.server)

    assert process.provider.token() == signed_in.first_token
    assert _token_requests(signed_in.server) == before


def test_a_failed_refresh_sends_the_old_token(signed_in: _SignedIn) -> None:
    process = signed_in.process()
    process.expire_in(10)
    signed_in.server.token_replies.append(error_reply("server_error", 500))

    # The server decides what to make of the old token.
    assert process.provider.token() == signed_in.first_token
    assert signed_in.refreshes() == 0


def test_a_refresh_the_server_refuses_leaves_no_token_to_send(
    signed_in: _SignedIn,
) -> None:
    process = signed_in.process()
    process.expire_in(10)
    signed_in.server.token_replies.append(error_reply("invalid_grant"))

    assert process.provider.token() is None
    # The request then goes out without a token, and its 401 asks for a
    # sign-in.
    with pytest.raises(McpOAuthAuthorizationRequiredError):
        process.reject(None)


# -- a rejected request -------------------------------------------------------


def test_refreshes_the_tokens_after_a_401(signed_in: _SignedIn) -> None:
    server = signed_in.server
    process = signed_in.process()
    server.expire_access_tokens()

    process.reject(signed_in.first_token, server.challenge)

    refreshed = process.provider.token()
    assert refreshed not in (None, signed_in.first_token)
    assert signed_in.refreshes() == 1
    assert process.challenges == [
        OAuthChallenge(
            resource_metadata_url=f"{server.origin}/.well-known/oauth-protected-resource/mcp"
        )
    ]
    assert signed_in.lock_files() == []


def test_a_challenge_for_more_scope_needs_a_sign_in(signed_in: _SignedIn) -> None:
    process = signed_in.process()
    before = _token_requests(signed_in.server)

    with pytest.raises(McpOAuthAuthorizationRequiredError):
        process.provider.on_unauthorized(
            UnauthorizedContext(
                403,
                'Bearer error="insufficient_scope", scope="admin"',
                signed_in.server.url,
                signed_in.first_token,
            )
        )

    # A refresh keeps the granted scope, so none is tried.
    assert _token_requests(signed_in.server) == before
    assert process.challenges == [
        OAuthChallenge(scope="admin", error="insufficient_scope")
    ]
    assert process.provider.token() == signed_in.first_token


def test_a_401_before_a_sign_in_needs_a_sign_in(
    server: FakeOAuthMcpServer, tmp_path: Path
) -> None:
    process = _Process(server, mcp_auth_path(tmp_path))

    with pytest.raises(McpOAuthAuthorizationRequiredError):
        process.reject(None, server.challenge)

    assert [request.path for request in server.requests] == []
    assert len(process.challenges) == 1


def test_a_401_without_a_refresh_token_needs_a_sign_in(
    signed_in: _SignedIn,
) -> None:
    process = signed_in.process()
    process.change(
        tokens={"access_token": signed_in.first_token, "token_type": "Bearer"}
    )
    before = _token_requests(signed_in.server)

    with pytest.raises(McpOAuthAuthorizationRequiredError):
        process.reject(signed_in.first_token)

    assert _token_requests(signed_in.server) == before


def test_a_refresh_the_server_refuses_needs_a_sign_in(signed_in: _SignedIn) -> None:
    process = signed_in.process()
    signed_in.server.token_replies.append(error_reply("invalid_grant"))

    with pytest.raises(McpOAuthAuthorizationRequiredError):
        process.reject(signed_in.first_token)

    assert process.access_token() is None
    assert signed_in.lock_files() == []


def test_tokens_that_changed_meanwhile_are_used_without_refreshing(
    signed_in: _SignedIn,
) -> None:
    process = signed_in.process()
    before = _token_requests(signed_in.server)

    # The rejected token is not the stored one any more.
    process.reject("an-older-token")

    assert _token_requests(signed_in.server) == before
    assert process.provider.token() == signed_in.first_token


def test_refreshes_once_when_several_processes_find_the_same_token_rejected(
    signed_in: _SignedIn,
) -> None:
    # Pi: refreshes once when several processes find the same token rejected
    signed_in.server.expire_access_tokens()
    processes = [signed_in.process() for _ in range(3)]
    start = threading.Barrier(len(processes))

    def reject(process: _Process) -> Callable[[], None]:
        def run() -> None:
            start.wait(_WAIT_SECONDS)
            process.reject(signed_in.first_token)

        return run

    # The server rotates refresh tokens: a second refresh with the first
    # refresh token would fail with invalid_grant.
    calls = [_in_thread(reject(process)) for process in processes]
    for thread, _, raised in calls:
        thread.join(_WAIT_SECONDS)
        assert raised == []

    assert signed_in.refreshes() == 1
    tokens = {process.provider.token() for process in processes}
    assert len(tokens) == 1 and tokens.isdisjoint({None, signed_in.first_token})
    # The lock is released.
    assert signed_in.lock_files() == []


def test_waits_for_a_running_refresh_to_save_the_new_tokens(
    signed_in: _SignedIn,
) -> None:
    # Pi: waits for a running refresh to save the new tokens
    gate = _gate_token_endpoint(signed_in.server)
    process = signed_in.process()
    before = _token_requests(signed_in.server)
    refresh, _, raised = _in_thread(lambda: process.reject(signed_in.first_token))
    _wait_until(lambda: _token_requests(signed_in.server) == before + 1)
    settled, _, _ = _in_thread(process.provider.settled)

    settled.join(0.1)
    assert settled.is_alive()
    assert process.access_token() == signed_in.first_token
    gate.set()
    settled.join(_WAIT_SECONDS)

    assert not settled.is_alive()
    assert process.access_token() not in (None, signed_in.first_token)
    refresh.join(_WAIT_SECONDS)
    assert raised == []
    # Nothing is running now.
    process.provider.settled()


def test_the_requests_of_one_process_share_one_refresh(signed_in: _SignedIn) -> None:
    gate = _gate_token_endpoint(signed_in.server)
    process = signed_in.process()
    before = _token_requests(signed_in.server)
    first, _, first_raised = _in_thread(lambda: process.reject(signed_in.first_token))
    _wait_until(lambda: _token_requests(signed_in.server) == before + 1)
    others = [
        _in_thread(lambda: process.reject(signed_in.first_token)) for _ in range(4)
    ]
    # A request that is about to be sent waits for the refresh too.
    asking, tokens, _ = _in_thread(process.provider.token)

    asking.join(0.1)
    assert asking.is_alive()
    gate.set()
    for thread, _, raised in [(first, None, first_raised), *others]:
        thread.join(_WAIT_SECONDS)
        assert raised == []
    asking.join(_WAIT_SECONDS)

    assert _token_requests(signed_in.server) == before + 1
    assert tokens == [process.access_token()]
    assert tokens[0] != signed_in.first_token


def test_requests_that_share_a_refresh_share_its_failure(
    signed_in: _SignedIn,
) -> None:
    gate = _gate_token_endpoint(signed_in.server)
    signed_in.server.token_replies.append(error_reply("invalid_grant"))
    process = signed_in.process()
    before = _token_requests(signed_in.server)
    calls = [_in_thread(lambda: process.reject(signed_in.first_token))]
    _wait_until(lambda: _token_requests(signed_in.server) == before + 1)
    calls += [
        _in_thread(lambda: process.reject(signed_in.first_token)) for _ in range(3)
    ]
    time.sleep(0.05)

    gate.set()
    for thread, _, raised in calls:
        thread.join(_WAIT_SECONDS)
        assert [type(error) for error in raised] == [McpOAuthAuthorizationRequiredError]
    assert _token_requests(signed_in.server) == before + 1


def test_a_refresh_waits_for_the_lock_and_uses_tokens_saved_meanwhile(
    signed_in: _SignedIn,
) -> None:
    holder = signed_in.process()
    process = signed_in.process(lock_retry_ms=5)
    before = _token_requests(signed_in.server)
    with holder.store.refresh_lock():
        refresh, _, raised = _in_thread(lambda: process.reject(signed_in.first_token))
        refresh.join(0.1)
        assert refresh.is_alive()
        # What the holder of the lock does: it saves new tokens.
        tokens = {"access_token": "from-the-holder", "token_type": "Bearer"}
        holder.change(tokens=tokens)
    refresh.join(_WAIT_SECONDS)

    assert raised == []
    assert _token_requests(signed_in.server) == before
    assert process.provider.token() == "from-the-holder"


def test_a_refresh_gives_up_when_the_lock_stays_taken(signed_in: _SignedIn) -> None:
    holder = signed_in.process()
    process = signed_in.process(lock_wait_ms=50, lock_retry_ms=5)
    process.expire_in(10)
    before = _token_requests(signed_in.server)
    with holder.store.refresh_lock():
        with pytest.raises(McpRefreshLockTimeoutError):
            process.reject(signed_in.first_token)
        # Before a request the old token is sent instead.
        assert process.provider.token() == signed_in.first_token

    assert _token_requests(signed_in.server) == before
    process.reject(signed_in.first_token)
    assert signed_in.refreshes() == 1


def test_each_request_to_the_authorization_server_has_a_time_limit(
    signed_in: _SignedIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert credentials_module.OAUTH_REQUEST_TIMEOUT_MS == 15_000
    monkeypatch.setattr(credentials_module, "OAUTH_REQUEST_TIMEOUT_MS", 100)
    process = signed_in.process()
    process.expire_in(10)
    signed_in.server.route("/token", Reply(hang=True))
    started = time.monotonic()

    # A refresh that got no answer ends in a new authorization.
    with pytest.raises(McpOAuthAuthorizationRequiredError):
        process.reject(signed_in.first_token)
    assert process.provider.token() == signed_in.first_token

    assert time.monotonic() - started < 5
    assert signed_in.lock_files() == []
    # The refresh token is kept for the next attempt.
    state = process.store.load()
    assert state is not None and state["tokens"]["refresh_token"]


# -- the settings of a refresh ------------------------------------------------


def test_the_settings_are_read_only_for_a_refresh(signed_in: _SignedIn) -> None:
    calls = 0

    def settings() -> McpOAuthSettings:
        nonlocal calls
        calls += 1
        raise ValueError('MCP server "test" oauth.clientSecret: not set')

    process = signed_in.process(settings=settings)

    assert process.provider.token() == signed_in.first_token
    process.reject("an-older-token")
    assert calls == 0

    # A secret that cannot be resolved fails the refresh, nothing else.
    with pytest.raises(ValueError, match="oauth.clientSecret"):
        process.reject(signed_in.first_token)
    process.expire_in(10)
    assert process.provider.token() == signed_in.first_token
    assert calls == 2
    assert signed_in.refreshes() == 0
    assert signed_in.lock_files() == []


def test_a_refresh_uses_the_configured_metadata_of_the_authorization_server(
    signed_in: _SignedIn,
) -> None:
    server = signed_in.server
    server.json(
        "/custom-metadata",
        {**server.metadata, "token_endpoint": f"{server.origin}/token2"},
    )
    server.route("/token2", server._token)
    settings = McpOAuthSettings(
        auth_server_metadata_url=f"{server.origin}/custom-metadata"
    )
    process = signed_in.process(settings=lambda: settings)

    process.reject(signed_in.first_token)

    assert len(server.requests_to("/token2")) == 1
    assert process.provider.token() != signed_in.first_token


def test_the_client_of_a_registration_is_named_pipy_unless_configured() -> None:
    def name(settings: McpOAuthSettings) -> object:
        store = McpOAuthCredentialStore("unused").for_server(
            "test", "https://a.example/"
        )
        provider = create_oauth_provider(
            "https://a.example/", store, settings, _REDIRECT, lambda url: None
        )
        return provider.client_metadata["client_name"]

    assert name(McpOAuthSettings()) == "pipy"
    assert name(McpOAuthSettings(client_name="Claude Code")) == "Claude Code"


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        (McpOAuthSettings(), None),
        (McpOAuthSettings(callback_port=8123), "http://127.0.0.1:8123/callback"),
        (McpOAuthSettings(callback_port=80), "http://127.0.0.1/callback"),
        (McpOAuthSettings(callback_url="http://localhost/cb"), None),
        (
            McpOAuthSettings(
                callback_url="http://localhost/cb?x=1", callback_port=9000
            ),
            "http://localhost:9000/cb?x=1",
        ),
        # A URI that names its port is sent exactly as written.
        (
            McpOAuthSettings(
                callback_url="http://LOCALHOST:9000/cb", callback_port=9000
            ),
            "http://LOCALHOST:9000/cb",
        ),
        (
            McpOAuthSettings(callback_url="http://[::1]:7000/callback"),
            "http://[::1]:7000/callback",
        ),
    ],
)
def test_the_redirect_uri_the_settings_fix(
    settings: McpOAuthSettings, expected: str | None
) -> None:
    assert fixed_redirect_url(settings) == expected


# -- on the transport of a connection -------------------------------------------


def _connect(process: _Process) -> McpClient:
    client = McpClient(McpClientOptions(name="test", version="1.0.0"))
    client.connect(
        StreamableHttpTransport(
            StreamableHttpTransportOptions(
                url=process.url, auth_provider=process.provider
            )
        )
    )
    return client


def _whoami(client: McpClient) -> str:
    result: Any = client.call_tool("whoami")
    text: str = result["content"][0]["text"]
    return text


def test_a_connection_sends_the_token_and_refreshes_it_when_it_is_rejected(
    signed_in: _SignedIn,
) -> None:
    server = signed_in.server
    process = signed_in.process()
    client = _connect(process)
    try:
        assert _whoami(client) == f"token {signed_in.first_token}"

        server.expire_access_tokens()
        refreshed = _whoami(client).removeprefix("token ")

        assert refreshed == process.access_token() != signed_in.first_token
        assert signed_in.refreshes() == 1
        assert f"401 {signed_in.first_token}" in server.log
        assert process.challenges[-1].resource_metadata_url is not None
    finally:
        client.close()
    assert server.deletes == [refreshed]


def test_a_connection_whose_refresh_is_refused_fails_with_the_need_to_sign_in(
    signed_in: _SignedIn,
) -> None:
    server = signed_in.server
    process = signed_in.process()
    client = _connect(process)
    try:
        server.expire_access_tokens()
        server.token_replies.append(error_reply("invalid_grant"))
        with pytest.raises(McpOAuthAuthorizationRequiredError):
            client.call_tool("whoami")

        # No token is left, and the provider cannot get one.
        with pytest.raises(McpOAuthAuthorizationRequiredError):
            client.call_tool("whoami")
        assert server.log[-1] == "401 none"
    finally:
        client.close()


def test_a_connection_that_needs_more_scope_fails_with_the_need_to_sign_in(
    signed_in: _SignedIn,
) -> None:
    process = signed_in.process()
    client = _connect(process)
    try:
        signed_in.server.insufficient_scope = "admin"
        with pytest.raises(McpOAuthAuthorizationRequiredError):
            client.call_tool("whoami")
        assert process.challenges[-1] == OAuthChallenge(
            scope="admin", error="insufficient_scope"
        )
    finally:
        client.close()
    assert signed_in.refreshes() == 0


def test_closing_does_not_refresh_an_expiring_token(signed_in: _SignedIn) -> None:
    # Pi: closes the session without refreshing an expiring token (the
    # transport's part)
    server = signed_in.server
    process = signed_in.process()
    client = _connect(process)
    # Still accepted, but so close to its expiry that the next request
    # would refresh it first.
    process.expire_in(10)
    server.route("/token", Reply(hang=True))
    before = _token_requests(server)
    started = time.monotonic()

    client.close()

    assert time.monotonic() - started < 2
    assert _token_requests(server) == before
    assert server.deletes == [signed_in.first_token]


# -- two processes --------------------------------------------------------------


def test_two_processes_refresh_a_rejected_token_once(signed_in: _SignedIn) -> None:
    # Pi: refreshes once when several processes find the same token
    # rejected, with processes of their own.
    server = signed_in.server
    server.expire_access_tokens()
    answer = server._token

    def slow(request: OAuthRequest) -> Reply:
        # Long enough for every process to be waiting for the lock.
        time.sleep(0.3)
        return answer(request)

    server.route("/token", slow)
    arguments = [str(signed_in.path), server.url, signed_in.first_token]
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", _REFRESH_IN_A_PROCESS, *arguments],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(3)
    ]
    try:
        for process in processes:
            assert process.stdout is not None
            assert process.stdout.readline() == "ready\n"
        outputs = []
        for process in processes:
            assert process.stdin is not None
            process.stdin.write("\n")
            process.stdin.flush()
        for process in processes:
            out, err = process.communicate(timeout=_WAIT_SECONDS * 3)
            assert process.returncode == 0, err
            outputs.append(out.strip())
    finally:
        for process in processes:
            process.kill()
            process.wait(_WAIT_SECONDS)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    assert signed_in.refreshes() == 1
    refreshed = signed_in.process().access_token()
    assert refreshed not in (None, signed_in.first_token)
    assert outputs == [refreshed] * 3
    assert signed_in.lock_files() == []

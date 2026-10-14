"""Tests for ``adapt_oauth_provider``, the auth provider of an HTTP transport
for any OAuth client provider, against the fake servers.

Tests that mirror a case of Pi's ``packages/mcp/test/oauth.test.ts`` name it
in a ``Pi:`` comment.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.mcp.client import McpClient, McpClientOptions
from pipy_harness.native.mcp.http import (
    StreamableHttpTransport,
    StreamableHttpTransportOptions,
    UnauthorizedContext,
)
from pipy_harness.native.mcp.oauth.errors import McpOAuthAuthorizationRequiredError
from pipy_harness.native.mcp.oauth.flow import (
    OAuthFlowOptions,
    adapt_oauth_provider,
    authorize_mcp,
)
from pipy_harness.native.mcp.oauth.provider import (
    McpOAuthProvider,
    McpOAuthProviderOptions,
    MemoryOAuthStateStore,
)
from pipy_harness.native.mcp.oauth.types import OAuthTokens

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import (  # noqa: E402
    FakeAuthorizationServer,
    FakeOAuthMcpServer,
    OAuthRequest,
    Reply,
    authorization_response,
    form,
    json_reply,
)

_WAIT_SECONDS = 10.0
_REDIRECT = "http://127.0.0.1/callback"


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


def _no_request_threads() -> bool:
    return not [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith(("mcp-oauth-fetch", "mcp-http"))
    ]


@pytest.fixture
def mcp_server() -> Iterator[FakeOAuthMcpServer]:
    with FakeOAuthMcpServer() as fake:
        yield fake
    _wait_until(_no_request_threads)


@pytest.fixture
def server() -> Iterator[FakeAuthorizationServer]:
    with FakeAuthorizationServer() as fake:
        yield fake
    _wait_until(_no_request_threads)


class _Redirects:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> None:
        self.urls.append(url)


def _provider(
    server_url: str, store: MemoryOAuthStateStore, **options: Any
) -> tuple[McpOAuthProvider, _Redirects]:
    redirects = _Redirects()
    provider = McpOAuthProvider(
        McpOAuthProviderOptions(
            server_url=server_url,
            redirect_url=_REDIRECT,
            client_metadata={"client_name": "test"},
            store=store,
            on_redirect=redirects,
            **options,
        )
    )
    return provider, redirects


def _connect(url: str, provider: McpOAuthProvider) -> McpClient:
    client = McpClient(McpClientOptions(name="oauth-test", version="1.0.0"))
    transport = StreamableHttpTransport(
        StreamableHttpTransportOptions(
            url=url,
            # The provider's token replaces a configured one.
            headers={"Authorization": "Bearer caller-supplied-stale-token"},
            auth_provider=adapt_oauth_provider(provider),
            open_get_stream=False,
        )
    )
    client.connect(transport)
    return client


def _whoami(client: McpClient) -> str:
    result: Any = client.call_tool("whoami", {})
    text: str = result["content"][0]["text"]
    return text.removeprefix("token ")


def test_authorizes_a_transport_and_refreshes_after_a_401(
    mcp_server: FakeOAuthMcpServer,
) -> None:
    # Pi: "discovers, registers, authorizes with PKCE, and refreshes on 401",
    # the half that connects through adaptOAuthProvider, as Pi's README shows.
    store = MemoryOAuthStateStore()
    provider, redirects = _provider(mcp_server.url, store)

    with pytest.raises(McpOAuthAuthorizationRequiredError):
        _connect(mcp_server.url, provider)
    [authorization_url] = redirects.urls
    assert mcp_server.log == ["401 caller-supplied-stale-token", "register"]

    code = authorization_response(authorization_url)["code"]
    options = OAuthFlowOptions(server_url=mcp_server.url, authorization_code=code)
    assert authorize_mcp(provider, options) == "AUTHORIZED"
    tokens = provider.tokens()
    assert tokens is not None
    first = tokens["access_token"]

    client = _connect(mcp_server.url, provider)
    try:
        assert [tool["name"] for tool in client.list_tools()] == ["whoami"]
        assert _whoami(client) == first

        mcp_server.expire_access_tokens()
        refreshed = _whoami(client)
    finally:
        client.close()
    assert refreshed != first
    assert mcp_server.log.count("token refresh") == 1
    # No new authorization was asked for.
    assert len(redirects.urls) == 1


def _rotating_token_route(
    server: FakeAuthorizationServer, grants: list[str]
) -> Callable[[OAuthRequest], Reply]:
    def token(request: OAuthRequest) -> Reply:
        refresh_token = form(request).get("refresh_token", "")
        with server.lock:
            grants.append(refresh_token)
        if refresh_token != "r1":
            return json_reply({"error": "invalid_grant"}, 400)
        # Long enough for the other requests to arrive meanwhile.
        time.sleep(0.2)
        return json_reply(
            {
                "access_token": "a2",
                "refresh_token": "r2",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )

    return token


def _rejected(server_url: str, token: str | None, header: str) -> UnauthorizedContext:
    return UnauthorizedContext(
        status=401, www_authenticate=header, server_url=server_url, token=token
    )


def test_shares_one_refresh_between_concurrent_401s_when_refresh_tokens_rotate(
    server: FakeAuthorizationServer,
) -> None:
    # Pi: "shares one refresh between concurrent 401s when refresh tokens rotate"
    grants: list[str] = []
    server.route("/token", _rotating_token_route(server, grants))
    server_url = f"{server.origin}/mcp"
    store = MemoryOAuthStateStore()
    provider, redirects = _provider(server_url, store, client_id="client")
    provider.save_tokens(
        {"access_token": "a1", "refresh_token": "r1", "token_type": "Bearer"}
    )
    auth = adapt_oauth_provider(provider)
    errors: list[BaseException] = []

    def reject() -> None:
        try:
            auth.on_unauthorized(_rejected(server_url, "a1", "Bearer"))
        except BaseException as error:  # noqa: BLE001 - cross-thread capture for the assertion
            errors.append(error)

    threads = [threading.Thread(target=reject) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(_WAIT_SECONDS)
    assert errors == []
    # A late 401 for a request that still carried the old token must not
    # refresh again.
    auth.on_unauthorized(_rejected(server_url, "a1", "Bearer"))
    assert grants == ["r1"]
    assert auth.token() == "a2"
    state = store.load()
    assert state is not None
    assert state["tokens"]["refresh_token"] == "r2"
    assert state["tokensExpireAt"] > time.time() * 1000 + 3_500_000
    assert redirects.urls == []


def test_the_waiters_of_a_failed_run_get_its_error(
    server: FakeAuthorizationServer,
) -> None:
    server_url = f"{server.origin}/mcp"
    entered = threading.Event()
    release = threading.Event()

    def token(request: OAuthRequest) -> Reply:
        entered.set()
        release.wait(_WAIT_SECONDS)
        return json_reply({"error": "invalid_request"}, 400)

    server.route("/token", token)
    store = MemoryOAuthStateStore()
    provider, _ = _provider(server_url, store, client_id="client")
    provider.save_tokens(
        {"access_token": "a1", "refresh_token": "r1", "token_type": "Bearer"}
    )
    auth = adapt_oauth_provider(provider)
    errors: list[BaseException] = []

    def reject() -> None:
        try:
            auth.on_unauthorized(_rejected(server_url, "a1", "Bearer"))
        except BaseException as error:  # noqa: BLE001 - cross-thread capture for the assertion
            errors.append(error)

    first = threading.Thread(target=reject)
    first.start()
    assert entered.wait(_WAIT_SECONDS)
    # Know when the second request waits for the run of the first.
    running = auth._running
    assert running is not None
    waiting = threading.Event()
    done = running.done

    class _Done(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            waiting.set()
            return done.wait(timeout)

        def set(self) -> None:
            done.set()

    running.done = _Done()
    second = threading.Thread(target=reject)
    second.start()
    assert waiting.wait(_WAIT_SECONDS)
    release.set()
    first.join(_WAIT_SECONDS)
    second.join(_WAIT_SECONDS)
    assert len(errors) == 2
    assert errors[0] is errors[1]
    assert len(server.requests_to("/token")) == 1


def test_asks_for_authorization_instead_of_refreshing_when_the_server_needs_more_scope(
    server: FakeAuthorizationServer,
) -> None:
    # Pi: "asks for authorization instead of refreshing when the server needs more scope"
    server.route("/token", Reply(500))
    server_url = f"{server.origin}/mcp"
    store = MemoryOAuthStateStore()
    provider, redirects = _provider(server_url, store, client_id="client")
    granted: OAuthTokens = {
        "access_token": "a1",
        "refresh_token": "r1",
        "token_type": "Bearer",
        "scope": "repo read:org",
    }
    provider.save_tokens(granted)
    auth = adapt_oauth_provider(provider)
    with pytest.raises(McpOAuthAuthorizationRequiredError):
        auth.on_unauthorized(
            UnauthorizedContext(
                status=403,
                www_authenticate='Bearer error="insufficient_scope", scope="repo admin"',
                server_url=server_url,
                token="a1",
            )
        )
    [url] = redirects.urls
    # The challenge may list only the missing scopes; the new grant keeps the
    # old ones too.
    assert "scope=repo+read%3Aorg+admin" in url
    # The working grant is kept until the user authorizes the new scope.
    assert auth.token() == "a1"
    assert server.requests_to("/token") == []


def test_a_request_whose_token_was_replaced_is_sent_again_without_a_refresh(
    server: FakeAuthorizationServer,
) -> None:
    server_url = f"{server.origin}/mcp"
    store = MemoryOAuthStateStore()
    provider, _ = _provider(server_url, store, client_id="client")
    provider.save_tokens({"access_token": "new", "token_type": "Bearer"})
    auth = adapt_oauth_provider(provider)
    auth.on_unauthorized(_rejected(server_url, "old", "Bearer"))
    assert server.requests == []


def test_without_a_refresh_token_the_user_has_to_authorize(
    server: FakeAuthorizationServer,
) -> None:
    server_url = f"{server.origin}/mcp"
    store = MemoryOAuthStateStore()
    provider, redirects = _provider(server_url, store, client_id="client")
    auth = adapt_oauth_provider(provider)
    assert auth.token() is None
    with pytest.raises(McpOAuthAuthorizationRequiredError):
        auth.on_unauthorized(_rejected(server_url, None, 'Bearer scope="read"'))
    [url] = redirects.urls
    assert "scope=read" in url
    assert server.requests_to("/token") == []


def test_a_401_that_arrives_after_a_run_ended_does_not_refresh_again(
    server: FakeAuthorizationServer,
) -> None:
    grants: list[str] = []
    server.route("/token", _rotating_token_route(server, grants))
    server_url = f"{server.origin}/mcp"
    store = MemoryOAuthStateStore()
    provider, _ = _provider(server_url, store, client_id="client")
    provider.save_tokens(
        {"access_token": "a1", "refresh_token": "r1", "token_type": "Bearer"}
    )
    auth = adapt_oauth_provider(provider)
    auth.on_unauthorized(_rejected(server_url, "a1", "Bearer"))
    # Requests that carried the old token and were rejected meanwhile.
    for _ in range(2):
        auth.on_unauthorized(_rejected(server_url, "a1", "Bearer"))
    assert grants == ["r1"]
    assert auth.token() == "a2"

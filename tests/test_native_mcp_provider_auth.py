"""Tests for MCP servers that are sent a provider's token (MCP parity plan
S33).

Rows OAU-36 and CON-14: a server whose entry has ``auth.provider`` is sent
the current token of that provider on every request, nothing of it is stored,
MCP OAuth never runs for it, and when the server rejects the token the
connection needs a sign-in whose text names ``/login <provider>``. Tested at
the connection against the fake protected MCP server, at the manager, at
``pipy mcp list``, at the function that answers with a provider's token, and
in a session. A mirrored Pi case is named in a ``Pi:`` comment.
"""

from __future__ import annotations

import io
import json
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from test_native_extension_tool_metadata import _result, _tool_names
from test_native_mcp_session import _mcp_threads, _Provider, _write_mcp_json
from test_native_openai_codex_provider import fake_jwt

from pipy_harness.models import HarnessStatus
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.coding.session import (
    CodingSession,
    production_tool_registry,
)
from pipy_harness.native.extension_types import (
    ExtensionOAuthConfig,
    ExtensionProvider,
    RegisteredProvider,
)
from pipy_harness.native.mcp.config import LoadedMcpConfig, McpServerEntry
from pipy_harness.native.mcp.connection import (
    McpServerConnection,
    McpServerConnectionError,
    _ProviderTokenAuth,
    sign_in_required_message,
)
from pipy_harness.native.mcp.credentials import (
    McpOAuthCredentialStore,
    mcp_auth_path,
)
from pipy_harness.native.mcp.manager import McpManager
from pipy_harness.native.mcp.protocol import McpConnectionClosedError
from pipy_harness.native.mcp.shell_cli import McpCommandOptions, run_mcp_command
from pipy_harness.native.oauth_providers import (
    OAuthCredentialCache,
    OAuthError,
    get_oauth_provider_ids,
)
from pipy_harness.native.openai_codex_provider import (
    FileOpenAICodexCredentialStore,
    OAuthTokenResponse,
    OpenAICodexAuthError,
    OpenAICodexAuthManager,
    OpenAICodexCredentials,
)
from pipy_harness.native.provider_login_token import provider_login_token
from pipy_harness.native.repl.mcp_loading import FollowedExtensions
from pipy_harness.native.repl.mcp_loading import provider_token as _provider_token
from pipy_harness.native.repl_state import (
    ModelRuntime,
    NativeModelSelection,
    NativeReplProviderState,
    StaticNativeReplProviderState,
)
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.tool_capabilities import ToolFilterOptions

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import FakeOAuthMcpServer  # noqa: E402

_WAIT_SECONDS = 10.0
_LOGIN = 'MCP server "radius" requires sign-in. Run /login radius to sign in.'


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


@pytest.fixture
def server() -> Iterator[FakeOAuthMcpServer]:
    before = set(threading.enumerate())
    with FakeOAuthMcpServer() as fake:
        yield fake
    _wait_until(lambda: _mcp_threads(before) == [])


def _entry(server: FakeOAuthMcpServer, **config: Any) -> McpServerEntry:
    return McpServerEntry(
        name="radius",
        config={"url": server.url, "auth": {"provider": "radius"}, **config},  # type: ignore[typeddict-item]
        source="test",
    )


class _Tokens:
    """The provider's token as it is now, and who was asked for one."""

    def __init__(self, token: str | None) -> None:
        self.token = token
        self.asked: list[str] = []

    def __call__(self, provider: str) -> str | None:
        self.asked.append(provider)
        return self.token


def _connection(
    server: FakeOAuthMcpServer,
    provider_token: Callable[[str], str | None] | None,
    credentials: McpOAuthCredentialStore | None = None,
    states: list[str] | None = None,
) -> McpServerConnection:
    return McpServerConnection(
        entry=_entry(server),
        cwd=Path.cwd(),
        client_version="1.2.3",
        on_tools=lambda connection: None,
        on_change=lambda connection: (
            None if states is None else states.append(connection.state)
        ),
        credentials=credentials,
        provider_token=provider_token,
    )


def _whoami(connection: McpServerConnection) -> str:
    result: Any = connection.call_tool("whoami", {})
    text: str = result["content"][0]["text"]
    return text.removeprefix("token ")


# -- the connection -----------------------------------------------------------


def test_sends_the_provider_token_and_asks_for_the_provider_login_when_the_server_rejects_it(
    server: FakeOAuthMcpServer, tmp_path: Path
) -> None:
    # Pi: sends the provider token and asks for the provider login when the
    # server rejects it
    credentials = McpOAuthCredentialStore(mcp_auth_path(tmp_path / "home"))
    states: list[str] = []
    connection = _connection(
        server,
        lambda provider: "tok" if provider == "radius" else None,
        credentials,
        states,
    )
    try:
        assert connection.oauth_url is None
        assert connection.view().oauth_url is None

        with pytest.raises(McpServerConnectionError) as failure:
            connection.get_client()

        assert str(failure.value) == _LOGIN
        assert connection.state == "needs-auth"
        assert connection.error is None
        assert states == ["connecting", "needs-auth"]
        # One request with the token: it is not made again, and MCP OAuth
        # does not run (no metadata is read, no challenge kept, nothing
        # stored).
        assert server.log == ["401 tok"]
        assert server.paths == ["/mcp"]
        assert connection.challenge is None
        assert not mcp_auth_path(tmp_path / "home").exists()
    finally:
        connection.close()


def test_the_token_is_read_for_every_request(server: FakeOAuthMcpServer) -> None:
    tokens = _Tokens("first")
    server.accept("first")
    server.accept("second")
    connection = _connection(server, tokens)
    try:
        assert _whoami(connection) == "first"
        asked = len(tokens.asked)
        assert asked > 0 and set(tokens.asked) == {"radius"}

        # The provider refreshed its token: the next request carries it.
        tokens.token = "second"
        assert _whoami(connection) == "second"
        assert len(tokens.asked) == asked + 1
        assert connection.state == "connected"
    finally:
        connection.close()
    # Closing sends the DELETE with the token of the last request and asks
    # for no new one.
    assert server.deletes == ["second"]
    assert len(tokens.asked) == asked + 1


def test_a_token_rejected_in_the_middle_needs_the_provider_login_and_the_next_call_connects(
    server: FakeOAuthMcpServer,
) -> None:
    tokens = _Tokens("first")
    server.accept("first")
    connection = _connection(server, tokens)
    try:
        assert _whoami(connection) == "first"
        server.expire_access_tokens()

        with pytest.raises(McpServerConnectionError) as failure:
            connection.call_tool("whoami", {})

        assert str(failure.value) == _LOGIN
        assert connection.state == "needs-auth"
        # The rejected request was made once.
        assert server.log == ["call first", "401 first"]

        # After /login the provider has a new token; the next call connects.
        tokens.token = "second"
        server.accept("second")
        assert _whoami(connection) == "second"
        assert connection.state == "connected"
    finally:
        connection.close()


def test_closing_asks_for_no_token_any_more_and_waits_for_a_running_call(
    server: FakeOAuthMcpServer,
) -> None:
    # Asking may refresh the provider's login and save rotated credentials.
    asked = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def slow(provider: str) -> str | None:
        calls.append(provider)
        asked.set()
        assert release.wait(_WAIT_SECONDS)
        return "tok"

    server.accept("tok")
    connection = _connection(server, slow)
    connecting = threading.Thread(target=lambda: _quietly(connection.get_client))
    connecting.start()
    assert asked.wait(_WAIT_SECONDS)
    closed = threading.Event()

    def close() -> None:
        connection.close()
        closed.set()

    closing = threading.Thread(target=close)
    closing.start()
    try:
        # The call for the token is still running, so close() has not returned.
        assert not closed.wait(0.2)
    finally:
        release.set()
    closing.join(_WAIT_SECONDS)
    connecting.join(_WAIT_SECONDS)
    assert closed.is_set()
    assert calls == ["radius"]


def test_a_closed_auth_provider_asks_for_nothing() -> None:
    tokens = _Tokens("tok")
    auth = _ProviderTokenAuth("radius", tokens)
    assert auth.token() == "tok"

    auth.close()
    auth.settled()

    with pytest.raises(McpConnectionClosedError):
        auth.token()
    assert tokens.asked == ["radius"]


def _quietly(call: Callable[[], object]) -> None:
    try:
        call()
    except Exception:  # noqa: BLE001 - the connect is closed under the test
        pass


@pytest.mark.parametrize("provider_token", [None, _Tokens(None)])
def test_without_a_token_the_request_carries_none_and_the_server_needs_the_login(
    provider_token: _Tokens | None, server: FakeOAuthMcpServer
) -> None:
    # ``pipy mcp`` passes no function, as Pi's command does not; a provider
    # nobody logged in to has no token.
    connection = _connection(server, provider_token)
    try:
        with pytest.raises(McpServerConnectionError) as failure:
            connection.get_client()

        assert str(failure.value) == _LOGIN
        assert connection.state == "needs-auth"
        assert server.log == ["401 none"]
    finally:
        connection.close()


def test_a_token_that_cannot_be_read_fails_the_connect(
    server: FakeOAuthMcpServer,
) -> None:
    def broken(provider: str) -> str | None:
        raise OSError("keychain is locked")

    connection = _connection(server, broken)
    try:
        with pytest.raises(McpServerConnectionError, match="keychain is locked"):
            connection.get_client()

        assert connection.state == "failed"
        assert server.log == []
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("config", "command"),
    [
        ({"url": "https://mcp.example.com/mcp"}, "/mcp"),
        (
            {"url": "https://mcp.example.com/mcp", "auth": {"provider": "radius"}},
            "/login radius",
        ),
        ({"command": "server"}, "/mcp"),
    ],
)
def test_the_sign_in_text_names_the_provider_login_for_a_server_with_auth(
    config: dict[str, Any], command: str
) -> None:
    entry = McpServerEntry(name="docs", config=config, source="test")  # type: ignore[arg-type]

    assert sign_in_required_message(entry) == (
        f'MCP server "docs" requires sign-in. Run {command} to sign in.'
    )


# -- the manager and `pipy mcp list` ------------------------------------------


def test_a_manager_gives_its_servers_the_provider_token(
    server: FakeOAuthMcpServer, tmp_path: Path
) -> None:
    tokens = _Tokens("tok")
    server.accept("tok")
    published: list[tuple[str, ...]] = []

    def publish(registry: Any) -> tuple[str, ...]:
        published.append(tuple(registry))
        return ()

    manager = McpManager(
        config=LoadedMcpConfig(servers=[_entry(server, exposure="direct")], errors=[]),
        cwd=tmp_path,
        client_version="1.2.3",
        publish_tools=publish,
        credentials=McpOAuthCredentialStore(mcp_auth_path(tmp_path / "home")),
        provider_token=tokens,
    )
    try:
        manager.start()
        assert manager.wait_until_settled(_WAIT_SECONDS)
        (status,) = manager.servers()
        assert status.connection is not None
        assert status.connection.state == "connected"
        assert published[-1] == ("mcp__radius__whoami",)

        # The provider's login ended: the call needs the provider login, and
        # there is no MCP sign-in to make or to wait for.
        server.expire_access_tokens()
        with pytest.raises(McpServerConnectionError) as failure:
            manager._caller("radius", "whoami").call_tool("whoami", {})

        assert str(failure.value) == _LOGIN
        (status,) = manager.servers()
        assert status.connection is not None
        assert status.connection.state == "needs-auth"
        assert manager.take_signed_in() == ()
        assert not mcp_auth_path(tmp_path / "home").exists()
    finally:
        manager.close()


def test_pipy_mcp_list_sends_no_provider_token(
    server: FakeOAuthMcpServer, tmp_path: Path
) -> None:
    # As `pi mcp list`: the command knows no provider logins.
    server.accept("tok")
    AuthStore(tmp_path / "auth.json").set("radius", {"type": "api_key", "key": "tok"})
    _write_mcp_json(
        tmp_path / "mcp.json",
        {"radius": {"url": server.url, "auth": {"provider": "radius"}}},
    )
    output: list[str] = []

    code = run_mcp_command(
        ["list"],
        McpCommandOptions(
            cwd=tmp_path,
            config_home=tmp_path,
            client_version="0.0.0-test",
            log=output.append,
            error=output.append,
        ),
    )

    assert code == 1
    assert output == [
        "radius: needs sign-in (codemode, global)",
        f"  {server.url}",
        "  sign in with: pipy mcp login radius",
    ]
    assert server.log == ["401 none"]


# -- the provider's token -----------------------------------------------------


class _Refreshing:
    """An OAuth provider whose refresh answers ``<access>+``."""

    id = "refreshing"

    def __init__(self) -> None:
        self.refreshed: list[str] = []

    def refresh_token(self, credentials: Mapping[str, object]) -> dict[str, object]:
        access = str(credentials["access"])
        self.refreshed.append(access)
        return {
            "type": "oauth",
            "access": f"{access}+",
            "refresh": credentials["refresh"],
            "expires": _NOW_MS + 3_600_000,
        }

    def get_api_key(self, credentials: Mapping[str, object]) -> str:
        return str(credentials["access"])


_NOW_MS = 1_800_000_000_000


def _codex(access: str) -> OpenAICodexCredentials:
    return OpenAICodexCredentials(
        access_token=access,
        refresh_token="codex-rt",
        expires_at=int(time.time()) + 3600,
        account_id="acct",
    )


class _Logins:
    """The credentials a model request reads, and what answers with a
    provider's token from them."""

    def __init__(self, tmp_path: Path, env: dict[str, str] | None = None) -> None:
        self.path = tmp_path / "auth.json"
        self.env = env or {}
        self.oauth = _Refreshing()
        # The built-in OAuth providers, each refreshing through `oauth`.
        self.cache = OAuthCredentialCache(
            providers=lambda provider: (
                self.oauth if provider in get_oauth_provider_ids() else None
            ),
            now_ms=lambda: _NOW_MS,
        )
        self.extension_providers: dict[str, ExtensionProvider] = {}
        self.runtime_api_key: str | None = None
        # The provider of the model the session started with.
        self.runtime_api_key_provider: str | None = None
        self.models_json: dict[str, str] = {}
        self.codex: OpenAICodexCredentials | None = None
        self.codex_asked = 0

    def store(self, provider: str, entry: dict[str, object]) -> None:
        AuthStore(self.path).set(provider, entry)

    def runtime_key_for(self, provider: str) -> str | None:
        if provider == self.runtime_api_key_provider:
            return self.runtime_api_key
        return None

    def register(self, name: str, oauth: ExtensionOAuthConfig | None = None) -> None:
        self.extension_providers[name] = ExtensionProvider(
            name=name,
            default_model="m",
            models=("m",),
            factory=lambda ctx: None,
            oauth=oauth,
        )

    def stored(self, provider: str) -> object:
        return json.loads(self.path.read_text(encoding="utf-8")).get(provider)

    def codex_login(self) -> OpenAICodexCredentials | None:
        self.codex_asked += 1
        return self.codex

    def token(self, provider: str) -> str | None:
        return provider_login_token(
            provider,
            auth_path=self.path,
            env=self.env,
            oauth_credentials=self.cache,
            extension_provider=self.extension_providers.get,
            runtime_api_key=self.runtime_key_for,
            models_json_key=self.models_json.get,
            codex_login=self.codex_login,
        )


def test_a_stored_api_key_is_the_token(tmp_path: Path) -> None:
    logins = _Logins(tmp_path, {"RADIUS_KEY": "from-env"})
    logins.store("radius", {"type": "api_key", "key": "literal"})
    logins.store("named", {"type": "api_key", "key": "RADIUS_KEY"})
    logins.store("empty", {"type": "api_key"})

    assert logins.token("radius") == "literal"
    # The name of a set environment variable is its value, as for a model
    # request.
    assert logins.token("named") == "from-env"
    assert logins.token("empty") is None
    assert logins.token("other") is None


def test_an_auth_file_with_a_byte_order_mark_is_read(tmp_path: Path) -> None:
    # Goal check r3 Q4, Pi `JSON.parse(stripBom(...))` in auth-storage.ts.
    logins = _Logins(tmp_path)
    logins.path.write_text(
        "\ufeff" + json.dumps({"radius": {"type": "api_key", "key": "tok"}}),
        encoding="utf-8",
    )

    assert logins.token("radius") == "tok"
    assert AuthStore(logins.path).get("radius") == {"type": "api_key", "key": "tok"}


def test_a_login_or_logout_made_meanwhile_applies_to_the_next_request(
    tmp_path: Path,
) -> None:
    logins = _Logins(tmp_path)
    assert logins.token("radius") is None

    logins.store("radius", {"type": "api_key", "key": "one"})
    assert logins.token("radius") == "one"

    logins.store("radius", {"type": "api_key", "key": "two"})
    assert logins.token("radius") == "two"

    AuthStore(logins.path).remove("radius")
    assert logins.token("radius") is None


def test_a_valid_stored_login_is_the_token_and_one_without_an_oauth_provider_none(
    tmp_path: Path,
) -> None:
    logins = _Logins(tmp_path)
    logins.store(
        "anthropic",
        {"type": "oauth", "access": "at", "refresh": "rt", "expires": _NOW_MS * 2},
    )
    logins.store("openai", {"type": "oauth", "refresh": "rt", "expires": _NOW_MS * 2})
    # Pi: a stored login of a provider without `oauth` auth is no token.
    logins.store(
        "radius", {"type": "oauth", "access": "at", "refresh": "rt", "expires": 1}
    )

    assert logins.token("anthropic") == "at"
    assert logins.token("openai") is None
    assert logins.token("radius") is None
    # An access token that is no string is no token, not its text.
    for access in (None, 123, {"a": 1}, ""):
        logins.store(
            "github-copilot",
            {
                "type": "oauth",
                "access": access,
                "refresh": "rt",
                "expires": _NOW_MS * 2,
            },
        )
        assert logins.token("github-copilot") is None
        logins.store(
            "anthropic",
            {
                "type": "oauth",
                "access": access,
                "refresh": "rt",
                "expires": _NOW_MS * 2,
            },
        )
        assert logins.token("anthropic") is None
    assert logins.oauth.refreshed == []


def test_every_built_in_login_is_refreshed_and_saved_once_it_expires_soon(
    tmp_path: Path,
) -> None:
    # Pi refreshes every stored login before network access
    # (`resolveStoredOAuth`) and saves it under the credential-store lock.
    logins = _Logins(tmp_path)
    for provider in ("anthropic", "openai-codex"):
        logins.store(
            provider,
            {
                "type": "oauth",
                "access": f"{provider}-at",
                "refresh": "rt",
                "expires": _NOW_MS + 60_000,
            },
        )

    assert logins.token("anthropic") == "anthropic-at+"
    assert logins.token("openai-codex") == "openai-codex-at+"
    assert logins.stored("anthropic") == {
        "type": "oauth",
        "access": "anthropic-at+",
        "refresh": "rt",
        "expires": _NOW_MS + 3_600_000,
    }
    # The saved login is valid now: no second refresh.
    assert logins.token("anthropic") == "anthropic-at+"
    assert logins.oauth.refreshed == ["anthropic-at", "openai-codex-at"]


def test_a_login_another_process_refreshed_meanwhile_is_not_refreshed_again(
    tmp_path: Path,
) -> None:
    logins = _Logins(tmp_path)
    expiring = {"type": "oauth", "access": "old", "refresh": "rt1", "expires": 1}
    valid = {"type": "oauth", "access": "new", "refresh": "rt2", "expires": _NOW_MS * 2}
    logins.store("anthropic", valid)

    # The entry read before the lock expires; the one under the lock does not.
    fresh = logins.cache.fresh_stored(
        logins.path,
        "anthropic",
        expiring,
        logins.oauth,  # type: ignore[arg-type]
    )
    assert fresh == valid
    assert logins.oauth.refreshed == []

    # A login removed meanwhile is no login.
    AuthStore(logins.path).remove("anthropic")
    assert (
        logins.cache.fresh_stored(
            logins.path,
            "anthropic",
            expiring,
            logins.oauth,  # type: ignore[arg-type]
        )
        is None
    )
    assert logins.oauth.refreshed == []


def test_a_login_pipy_refreshes_per_request_is_refreshed_once_it_expires_soon(
    tmp_path: Path,
) -> None:
    logins = _Logins(tmp_path)
    logins.store(
        "github-copilot",
        {
            "type": "oauth",
            "access": "at",
            "refresh": "rt",
            "expires": _NOW_MS + 60_000,
        },
    )

    assert logins.token("github-copilot") == "at+"
    # The session's cache keeps the refreshed credential, so model requests
    # and MCP requests refresh once.
    assert logins.token("github-copilot") == "at+"
    assert logins.oauth.refreshed == ["at"]


def test_a_refresh_that_fails_is_no_token(tmp_path: Path) -> None:
    logins = _Logins(tmp_path, {"ANTHROPIC_API_KEY": "env-key"})

    def refuse(credentials: Mapping[str, object]) -> dict[str, object]:
        raise OAuthError("refused")

    logins.oauth.refresh_token = refuse  # type: ignore[method-assign]
    for provider in ("github-copilot", "anthropic"):
        logins.store(
            provider,
            {"type": "oauth", "access": "at", "refresh": "rt", "expires": 1},
        )

    assert logins.token("github-copilot") is None
    # Neither the expired token nor the environment's key goes out instead.
    assert logins.token("anthropic") is None
    assert logins.stored("anthropic") == {
        "type": "oauth",
        "access": "at",
        "refresh": "rt",
        "expires": 1,
    }


def test_the_environment_is_asked_only_when_nothing_is_stored(tmp_path: Path) -> None:
    logins = _Logins(tmp_path, {"OPENROUTER_API_KEY": "env-key"})

    assert logins.token("openrouter") == "env-key"
    # A provider without an API-key variable, and ambient cloud credentials.
    assert logins.token("radius") is None
    logins.env["AWS_PROFILE"] = "default"
    assert logins.token("amazon-bedrock") is None

    # A stored entry owns the provider, also one that yields no token.
    logins.store("openrouter", {"type": "oauth", "refresh": "rt"})
    assert logins.token("openrouter") is None


def test_the_api_key_given_to_the_session_is_the_token_of_its_provider_only(
    tmp_path: Path,
) -> None:
    # Pi main.ts: `--api-key` is the runtime key of the starting model's
    # provider; it comes before that provider's stored login, which is not
    # refreshed then.
    logins = _Logins(tmp_path, {"OPENROUTER_API_KEY": "env-key"})
    logins.store("radius", {"type": "api_key", "key": "stored"})
    logins.store(
        "github-copilot",
        {"type": "oauth", "access": "at", "refresh": "rt", "expires": 1},
    )
    logins.models_json["radius"] = "configured"
    logins.runtime_api_key = "runtime"
    logins.runtime_api_key_provider = "github-copilot"

    assert logins.token("github-copilot") == "runtime"
    assert logins.oauth.refreshed == []

    # Read at each call.
    logins.runtime_api_key = None
    assert logins.token("github-copilot") == "at+"


def test_the_api_key_given_to_the_session_is_never_sent_for_another_provider(
    tmp_path: Path,
) -> None:
    # A server configured with `auth.provider` for another provider is sent
    # that provider's own credential, or nothing; never the session's key.
    logins = _Logins(tmp_path, {"OPENROUTER_API_KEY": "env-key"})
    logins.store("radius", {"type": "api_key", "key": "stored"})
    logins.store(
        "github-copilot",
        {"type": "oauth", "access": "at", "refresh": "rt", "expires": _NOW_MS * 2},
    )
    logins.models_json["configured"] = "from-models-json"
    logins.runtime_api_key = "runtime"
    logins.runtime_api_key_provider = "openai"

    assert logins.token("openai") == "runtime"
    assert logins.token("radius") == "stored"
    assert logins.token("github-copilot") == "at"
    assert logins.token("openrouter") == "env-key"
    assert logins.token("configured") == "from-models-json"
    assert logins.token("anthropic") is None
    assert logins.token("OpenAI") is None
    assert logins.token("openai-codex") is None

    # A key whose provider is not known belongs to no provider.
    logins.runtime_api_key_provider = None
    assert logins.token("openai") is None


def test_the_models_json_key_is_the_token_when_no_other_source_has_one(
    tmp_path: Path,
) -> None:
    logins = _Logins(tmp_path, {"RADIUS_KEY": "from-env"})
    logins.models_json.update(
        {"radius": "configured", "named": "RADIUS_KEY", "command": "!printf ran"}
    )

    assert logins.token("radius") == "configured"
    # Resolved like a stored key.
    assert logins.token("named") == "from-env"
    assert logins.token("command") == "ran"

    # The environment and a stored entry come first.
    logins.models_json["openrouter"] = "configured"
    logins.env["OPENROUTER_API_KEY"] = "env-key"
    assert logins.token("openrouter") == "env-key"
    logins.store("radius", {"type": "oauth", "refresh": "rt"})
    assert logins.token("radius") is None


def test_the_codex_login_is_the_token_of_openai_codex(tmp_path: Path) -> None:
    # A model request to a Codex model uses the Codex login and nothing else.
    logins = _Logins(tmp_path)
    logins.store("openai-codex", {"type": "api_key", "key": "stored"})
    logins.models_json["openai-codex"] = "configured"
    logins.codex = _codex("codex-at")

    assert logins.token("openai-codex") == "codex-at"

    # Without a login, the sources of a models.json model of the provider
    # with another api.
    logins.codex = None
    assert logins.token("openai-codex") == "stored"
    assert logins.codex_asked == 2

    # A login that cannot be read or refreshed is no token.
    def fail() -> OpenAICodexCredentials | None:
        raise OpenAICodexAuthError("OpenAI Codex auth state could not be read.")

    logins.codex_login = fail  # type: ignore[method-assign]
    assert logins.token("openai-codex") is None


def test_a_provider_an_extension_registered_is_answered_from_its_login_only(
    tmp_path: Path,
) -> None:
    # pipy's model requests leave its credentials to the extension: no
    # models.json key and no Codex login.
    logins = _Logins(tmp_path, {"OPENROUTER_API_KEY": "env-key"})
    for name in ("radius", "openrouter", "openai-codex"):
        logins.register(name)
    logins.models_json["radius"] = "configured"
    logins.codex = _codex("codex-at")

    assert logins.token("radius") is None
    assert logins.token("openrouter") == "env-key"
    assert logins.token("openai-codex") is None
    assert logins.codex_asked == 0

    # A stored login of an extension provider without `oauth` is no token
    # (Pi: no `oauth` auth), also for a built-in name it replaces.
    logins.register("github-copilot")
    logins.store(
        "github-copilot",
        {"type": "oauth", "access": "old", "refresh": "rt", "expires": _NOW_MS * 2},
    )
    logins.store("openai-codex", {"type": "api_key", "key": "stored"})
    assert logins.token("github-copilot") is None
    assert logins.token("openai-codex") == "stored"
    assert logins.oauth.refreshed == []


def _oauth_config(get_api_key: Callable[..., object]) -> ExtensionOAuthConfig:
    return ExtensionOAuthConfig(
        name="Login",
        login=lambda callbacks: {},
        refresh_token=lambda credentials: dict(credentials),
        get_api_key=get_api_key,
    )


def test_the_api_key_given_to_the_session_replaces_the_stored_login(
    tmp_path: Path,
) -> None:
    # Pi `RuntimeCredentials.read` returns the runtime key in place of the
    # stored credential, for an extension's provider too, and
    # `resolveProviderAuth` sends it through the provider's API-key auth.
    logins = _Logins(tmp_path, {"OPENROUTER_API_KEY": "env-key"})
    login = {"type": "oauth", "access": "at", "refresh": "rt", "expires": _NOW_MS * 2}
    asked: list[str] = []

    def api_key(credentials: Mapping[str, object]) -> str:
        asked.append("get_api_key")
        return "login-key"

    logins.runtime_api_key = "runtime"
    for name, oauth in (
        ("radius", None),
        ("anthropic", _oauth_config(api_key)),
        ("openrouter", None),
    ):
        logins.register(name, oauth)
        logins.store(name, login)
        logins.runtime_api_key_provider = name
        assert logins.token(name) == "runtime"
    assert asked == []

    # An extension provider that signs in with OAuth only takes no key
    # (`composeApiKeyAuth` gives it no API-key auth), so it has no token
    # while the key is set, and its stored login is not sent.
    logins.register("radius", _oauth_config(api_key))
    logins.runtime_api_key_provider = "radius"
    assert logins.token("radius") is None
    assert asked == []
    # A models.json `apiKey` gives it API-key auth.
    logins.models_json["radius"] = "configured"
    assert logins.token("radius") == "runtime"

    logins.runtime_api_key = None
    assert logins.token("radius") == "login-key"


def test_the_api_key_given_to_the_session_gives_openai_codex_no_token(
    tmp_path: Path,
) -> None:
    # Pi's `openai-codex` provider has only `oauth`: with the runtime key
    # set, `resolveProviderAuth` finds an API key the provider cannot take
    # and answers with no auth, unless models.json configures an `apiKey`.
    logins = _Logins(tmp_path)
    logins.store("openai-codex", {"type": "api_key", "key": "stored"})
    logins.codex = _codex("codex-at")
    logins.runtime_api_key = "runtime"
    logins.runtime_api_key_provider = "openai-codex"

    assert logins.token("openai-codex") is None
    assert logins.codex_asked == 0

    logins.models_json["openai-codex"] = "configured"
    assert logins.token("openai-codex") == "runtime"
    assert logins.codex_asked == 0

    # The same for an extension that registers `openai-codex`.
    logins.register("openai-codex")
    assert logins.token("openai-codex") == "runtime"
    del logins.models_json["openai-codex"]
    assert logins.token("openai-codex") is None

    logins.runtime_api_key = None
    assert logins.token("openai-codex") == "stored"


def test_a_stored_command_runs_once_per_process_and_a_models_json_one_every_time(
    tmp_path: Path,
) -> None:
    # Pi `AuthStorage.read` resolves a stored key with the cached
    # `resolveConfigValue`; models.json keys go through the uncached
    # `resolveConfigValueOrThrow`.
    runs = tmp_path / "runs"
    logins = _Logins(tmp_path)
    stored = f"!printf x >> {runs}; printf stored-key"
    logins.store("radius", {"type": "api_key", "key": stored})
    logins.models_json["other"] = f"!printf y >> {runs}; printf configured"

    for _ in range(3):
        assert logins.token("radius") == "stored-key"
        assert logins.token("other") == "configured"
    assert sorted(runs.read_text()) == ["x", "y", "y", "y"]

    # A command that failed stays failed.
    logins.store("failing", {"type": "api_key", "key": f"!printf z >> {runs}; exit 1"})
    assert logins.token("failing") is None
    assert logins.token("failing") is None
    assert runs.read_text().count("z") == 1


def test_an_extension_login_is_refreshed_and_saved_through_the_extension(
    tmp_path: Path,
) -> None:
    # Pi `adaptOAuth`: the extension's `refreshToken` result is the saved
    # login, and its `getApiKey` is the token.
    refreshed: list[dict[str, object]] = []

    def refresh(credentials: Mapping[str, object]) -> dict[str, object]:
        refreshed.append(dict(credentials))
        return {"access": "new", "refresh": "rt2", "expires": _NOW_MS * 2}

    logins = _Logins(tmp_path)
    logins.register(
        "radius",
        ExtensionOAuthConfig(
            name="Radius",
            login=lambda callbacks: {},
            refresh_token=refresh,
            get_api_key=lambda credentials: f"key-{credentials['access']}",
        ),
    )
    logins.store(
        "radius", {"type": "oauth", "access": "old", "refresh": "rt1", "expires": 1}
    )

    assert logins.token("radius") == "key-new"
    assert refreshed == [
        {"type": "oauth", "access": "old", "refresh": "rt1", "expires": 1}
    ]
    assert logins.stored("radius") == {
        "type": "oauth",
        "access": "new",
        "refresh": "rt2",
        "expires": _NOW_MS * 2,
    }
    assert logins.token("radius") == "key-new"
    assert len(refreshed) == 1
    # The built-in OAuth providers are not asked for an extension's login.
    assert logins.oauth.refreshed == []


def test_an_extension_login_that_cannot_be_refreshed_or_read_is_no_token(
    tmp_path: Path,
) -> None:
    logins = _Logins(tmp_path)

    async def later(credentials: Mapping[str, object]) -> dict[str, object]:
        return {}

    def refuse(credentials: Mapping[str, object]) -> dict[str, object]:
        raise RuntimeError("refused")

    expired = {"type": "oauth", "access": "old", "refresh": "rt", "expires": 1}
    valid = {**expired, "expires": _NOW_MS * 2}
    cases: list[
        tuple[Callable[..., object], Callable[..., object], dict[str, object]]
    ] = [
        (refuse, lambda credentials: "key", expired),
        (later, lambda credentials: "key", expired),
        (lambda credentials: "not a mapping", lambda credentials: "key", expired),
        (refuse, lambda credentials: None, valid),
        (refuse, lambda credentials: "", valid),
    ]
    for refresh, get_api_key, stored in cases:
        logins.register(
            "radius",
            ExtensionOAuthConfig(
                name="Radius",
                login=lambda callbacks: {},
                refresh_token=refresh,
                get_api_key=get_api_key,
            ),
        )
        logins.store("radius", stored)
        assert logins.token("radius") is None
        assert logins.stored("radius") == stored


def test_an_auth_file_that_cannot_be_read_is_no_token_also_not_from_the_environment(
    tmp_path: Path,
) -> None:
    # What the file stores for the provider is unknown then, so the key of
    # the environment or of models.json must not go out in its place.
    logins = _Logins(tmp_path, {"OPENROUTER_API_KEY": "env-key"})
    logins.models_json["radius"] = "configured"
    assert logins.token("openrouter") == "env-key"
    assert logins.token("radius") == "configured"

    for damaged in ("{not json", "[]", "\xff"):
        logins.path.write_bytes(damaged.encode("latin-1"))
        assert logins.token("openrouter") is None
        assert logins.token("radius") is None

    logins.path.unlink()
    logins.path.mkdir()
    assert logins.token("openrouter") is None

    # An entry that is no object stores nothing, as for a model request.
    logins.path.rmdir()
    logins.path.write_text('{"openrouter": "x"}', encoding="utf-8")
    assert logins.token("openrouter") == "env-key"


# -- the session --------------------------------------------------------------


def _provider_state(
    tmp_path: Path,
    env: dict[str, str],
    *,
    runtime_api_key: str | None = None,
    runtime_api_key_provider: str | None = None,
) -> NativeReplProviderState:
    return NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        model_runtime=ModelRuntime(
            ProviderCatalogState(
                models_json_path=tmp_path / "models.json",
                auth_store=AuthStore(path=tmp_path / "auth.json"),
                env=env,
                openai_codex_auth_path=tmp_path / "codex.json",
                runtime_api_key=runtime_api_key,
                runtime_api_key_provider=runtime_api_key_provider,
            )
        ),
        persist_defaults=False,
    )


def test_the_sessions_token_function_reads_the_sessions_logins(tmp_path: Path) -> None:
    env = {"OPENROUTER_API_KEY": "env-key"}
    state = _provider_state(tmp_path, env)

    token = _provider_token(state)

    assert token is not None
    assert token("radius") is None
    # A login made after the function was built, through the session's store.
    catalog = state.model_runtime.catalog
    assert catalog.auth_store is not None
    catalog.auth_store.set("radius", {"type": "api_key", "key": "tok"})
    assert token("radius") == "tok"
    assert token("openrouter") == "env-key"
    # It was given the cache model requests refresh through.
    assert token.keywords["oauth_credentials"] is catalog.oauth_credentials  # type: ignore[attr-defined]


def test_the_session_sends_the_codex_login_model_requests_use(tmp_path: Path) -> None:
    state = _provider_state(tmp_path, {})
    token = _provider_token(state)
    assert token is not None
    assert token("openai-codex") is None

    # A login made after the function was built, in the Codex login file.
    FileOpenAICodexCredentialStore(tmp_path / "codex.json").save(_codex("codex-at"))
    assert token("openai-codex") == "codex-at"

    # A login that is about to expire is refreshed and saved, as before a
    # model request.
    expiring = OpenAICodexCredentials(
        access_token="old",
        refresh_token="codex-rt",
        expires_at=0,
        account_id="acct",
    )
    FileOpenAICodexCredentialStore(tmp_path / "codex.json").save(expiring)
    manager = token.keywords["codex_login"].__self__  # type: ignore[attr-defined]
    refreshed: list[str] = []

    class _TokenEndpoint:
        def post_form(
            self, url: str, *, fields: Mapping[str, str], timeout_seconds: float
        ) -> OAuthTokenResponse:
            refreshed.append(fields["refresh_token"])
            return OAuthTokenResponse(
                access_token=new_access, refresh_token="rt2", expires_in=3600
            )

    new_access = fake_jwt("acct")
    manager.oauth_client = _TokenEndpoint()
    assert token("openai-codex") == new_access
    assert refreshed == ["codex-rt"]
    stored = FileOpenAICodexCredentialStore(tmp_path / "codex.json").load()
    assert stored is not None and stored.access_token == new_access


def test_model_requests_and_mcp_requests_refresh_the_codex_login_once(
    tmp_path: Path,
) -> None:
    # The refresh token rotates: a second refresh with the used one would
    # fail. Both paths load, refresh and save under one lock.
    path = tmp_path / "codex.json"
    FileOpenAICodexCredentialStore(path).save(
        OpenAICodexCredentials(
            access_token="old",
            refresh_token="rt1",
            expires_at=0,
            account_id="acct",
        )
    )
    used: list[str] = []
    new_access = fake_jwt("acct")
    entered = threading.Event()

    class _RotatingEndpoint:
        def post_form(
            self, url: str, *, fields: Mapping[str, str], timeout_seconds: float
        ) -> OAuthTokenResponse:
            entered.set()
            time.sleep(0.2)
            token = fields["refresh_token"]
            if token in used:
                raise OpenAICodexAuthError("refresh token already used")
            used.append(token)
            return OAuthTokenResponse(
                access_token=new_access, refresh_token="rt2", expires_in=3600
            )

    def manager() -> OpenAICodexAuthManager:
        return OpenAICodexAuthManager(
            store=FileOpenAICodexCredentialStore(path),
            oauth_client=_RotatingEndpoint(),
        )

    model_request: list[object] = []
    thread = threading.Thread(
        target=lambda: model_request.append(manager().get_credentials())
    )
    thread.start()
    entered.wait(_WAIT_SECONDS)
    login = manager().get_credentials()
    thread.join(_WAIT_SECONDS)

    assert used == ["rt1"]
    assert login is not None and login.access_token == new_access
    assert model_request == [login]


def test_the_session_sends_its_api_key_and_the_models_json_key(
    tmp_path: Path,
) -> None:
    (tmp_path / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "radius": {
                        "baseUrl": "https://radius.example/v1",
                        "api": "openai-completions",
                        "apiKey": "configured",
                        "models": [{"id": "m"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    state = _provider_state(tmp_path, {})
    token = _provider_token(state)
    assert token is not None
    assert token("radius") == "configured"
    assert token("other") is None

    state = _provider_state(
        tmp_path, {}, runtime_api_key="runtime", runtime_api_key_provider="other"
    )
    token = _provider_token(state)
    assert token is not None
    assert token("other") == "runtime"
    # Not the key of another provider.
    assert token("radius") == "configured"
    assert token("openai") is None


def test_the_session_reads_which_providers_extensions_registered_at_each_call(
    tmp_path: Path,
) -> None:
    state = _provider_state(
        tmp_path, {}, runtime_api_key="runtime", runtime_api_key_provider="radius"
    )
    catalog = state.model_runtime.catalog
    token = _provider_token(state)
    assert token is not None
    assert token("radius") == "runtime"

    # An extension that registers `radius` with an OAuth sign-in after the
    # function was built replaces the provider, which then takes no key.
    catalog.set_extension_provider_contributions(
        (
            RegisteredProvider(
                extension="ext",
                provider=ExtensionProvider(
                    name="Radius",
                    default_model="m",
                    models=("m",),
                    factory=lambda ctx: None,
                    oauth=_oauth_config(lambda credentials: "login-key"),
                ),
            ),
        ),
        (),
    )
    assert token("radius") is None


def test_the_session_api_key_belongs_to_the_provider_the_session_starts_with(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipy_harness.cli import _tool_repl_adapter_for

    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "defaults.json"))
    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        openai_codex_auth_path=tmp_path / "codex.json",
        runtime_api_key="sk-anthropic",
    )
    adapter = _tool_repl_adapter_for(
        "anthropic",
        "claude-opus-4-8",
        cwd=tmp_path,
        tool_budget=5,
        api_key="sk-anthropic",
        catalog_state=catalog,
    )
    state = adapter.provider_state
    assert isinstance(state, NativeReplProviderState)
    token = _provider_token(state)
    assert token is not None

    assert token("anthropic") == "sk-anthropic"
    for other in ("github-copilot", "openai", "openai-codex", "radius"):
        assert token(other) is None


class _Loaded:
    """The providers loaded extensions registered (``_ExtensionRuntime``)."""

    def __init__(self) -> None:
        self.providers: tuple[RegisteredProvider, ...] = ()
        self.unregistered_providers: tuple[str, ...] = ()


def _default_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The default config home and auth directory of this test; returns the
    auth directory."""

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PIPY_AUTH_DIR", str(tmp_path / "auth"))
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "config").mkdir()
    (tmp_path / "auth").mkdir()
    return tmp_path / "auth"


def test_a_session_with_an_injected_provider_resolves_tokens_from_the_default_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pi's SDK always builds a model runtime over the default auth.json and
    # models.json (sdk.ts `ModelRuntime.create`), and the MCP extension asks
    # it for `providerToken`.
    auth = _default_files(tmp_path, monkeypatch)
    extensions = FollowedExtensions()
    provider = _Provider([])
    token = _provider_token(StaticNativeReplProviderState(provider), extensions)  # type: ignore[arg-type]
    assert token is not None
    assert _provider_token(None) is not None

    # Nothing is read before the first token is asked for.
    AuthStore(auth / "auth.json").set("radius", {"type": "api_key", "key": "tok"})
    (tmp_path / "config" / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "configured": {
                        "baseUrl": "https://configured.example/v1",
                        "api": "openai-completions",
                        "apiKey": "from-models-json",
                        "models": [{"id": "m"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    FileOpenAICodexCredentialStore(auth / "openai-codex.json").save(_codex("codex-at"))

    assert token("radius") == "tok"
    assert token("openrouter") == "env-key"
    assert token("configured") == "from-models-json"
    assert token("openai-codex") == "codex-at"
    assert token("openai") is None
    # A login made later applies to the next request.
    AuthStore(auth / "auth.json").set("openai", {"type": "api_key", "key": "later"})
    assert token("openai") == "later"


def test_a_session_with_an_injected_provider_knows_its_extensions_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _default_files(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    AuthStore(auth / "auth.json").set(
        "radius",
        {"type": "oauth", "access": "at", "refresh": "rt", "expires": 2**53},
    )
    extensions = FollowedExtensions()
    token = _provider_token(None, extensions)
    assert token is not None
    # Before the extensions are named, and for a provider no extension
    # registered: a login of a provider without an OAuth sign-in.
    assert token("radius") is None

    loaded = _Loaded()
    extensions.follow(lambda: loaded)
    assert token("radius") is None
    # Read at each call, as the extensions loaded now registered them.
    loaded.providers = (
        RegisteredProvider(
            extension="ext",
            provider=ExtensionProvider(
                name="Radius",
                default_model="m",
                models=("m",),
                factory=lambda ctx: None,
                oauth=_oauth_config(lambda credentials: f"key-{credentials['access']}"),
            ),
        ),
    )
    assert token("radius") == "key-at"
    loaded.unregistered_providers = ("radius",)
    assert token("radius") is None
    assert token("openrouter") == "env-key"


def test_an_sdk_session_sends_the_provider_token_a_pipy_session_would(
    server: FakeOAuthMcpServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipy_harness.native.mcp.options import McpOptions
    from pipy_harness.sdk import create_product_session

    _default_files(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "env-tok")
    _write_mcp_json(
        tmp_path / "config" / "mcp.json",
        {
            "radius": {
                "url": server.url,
                "auth": {"provider": "openai"},
                "exposure": "direct",
            }
        },
    )
    server.accept("env-tok")
    workspace = tmp_path / "work"
    workspace.mkdir()
    provider = _Provider([_result(final_text="done")])
    diagnostics: list[str] = []

    with create_product_session(
        workspace=workspace,
        provider=provider,
        diagnostic_sink=diagnostics.append,
        load_context_files=False,
        mcp=McpOptions(),
    ) as session:
        result = session.submit("go")
        assert result.provider_failure is result.preparation_failure is None

    assert "need attention" not in "".join(diagnostics)
    assert _tool_names(provider.requests[0])[-1] == "mcp__radius__whoami"
    # The session closed the server with the provider's token.
    assert server.deletes == ["env-tok"]
    assert server.log == []


def test_a_session_whose_auth_store_is_unavailable_has_no_token_function(
    tmp_path: Path,
) -> None:
    # A reload can find the auth store gone; the servers then get no token.
    state = _provider_state(tmp_path, {})
    state.model_runtime.catalog.auth_store = None

    assert _provider_token(state) is None


def _run(
    workspace: Path, provider: _Provider, state: NativeReplProviderState
) -> tuple[HarnessStatus, str]:
    errors = io.StringIO()
    session = CodingSession(
        provider=provider,
        provider_state=state,
        tool_registry=production_tool_registry(),
        tool_budget=8,
        resource_options=RuntimeResourceOptions.empty(),
        tool_filter_options=ToolFilterOptions.empty(),
    )
    result = session.run(
        workspace_root=workspace,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=errors,
    )
    return result.status, errors.getvalue()


def test_a_session_sends_the_login_of_the_provider_and_reports_a_missing_one(
    server: FakeOAuthMcpServer, tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    workspace.mkdir()
    _write_mcp_json(
        config / "mcp.json",
        {
            "radius": {
                "url": server.url,
                "auth": {"provider": "radius"},
                "exposure": "direct",
            }
        },
    )
    state = _provider_state(tmp_path, {})

    # Nobody logged in to the provider.
    provider = _Provider([_result(final_text="done")])
    status, errors = _run(workspace, provider, state)

    assert status is HarnessStatus.SUCCEEDED, errors
    assert (
        errors.count(
            "MCP servers need attention:\n  radius: needs sign-in\nRun /mcp to fix."
        )
        == 1
    )
    assert "mcp__radius__whoami" not in _tool_names(provider.requests[0])
    assert server.log == ["401 none"]

    # After the login the server is sent the provider's token.
    server.accept("tok")
    catalog = state.model_runtime.catalog
    assert catalog.auth_store is not None
    catalog.auth_store.set("radius", {"type": "api_key", "key": "tok"})
    provider = _Provider([_result(final_text="done")])
    status, errors = _run(workspace, provider, state)

    assert status is HarnessStatus.SUCCEEDED, errors
    assert "need attention" not in errors
    assert _tool_names(provider.requests[0])[-1] == "mcp__radius__whoami"
    assert server.deletes == ["tok"]
    assert server.paths.count("/mcp") == len(server.paths)
    assert not mcp_auth_path(config).exists()
    assert json.loads((tmp_path / "auth.json").read_text()) == {
        "radius": {"type": "api_key", "key": "tok"}
    }

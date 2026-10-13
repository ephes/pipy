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

from pipy_harness.models import HarnessStatus
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.coding.session import (
    CodingSession,
    production_tool_registry,
)
from pipy_harness.native.extension_types import (
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
from pipy_harness.native.oauth_providers import OAuthCredentialCache, OAuthError
from pipy_harness.native.provider_login_token import provider_login_token
from pipy_harness.native.repl.wiring import _provider_token
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


class _Logins:
    """An ``auth.json`` and what answers with a provider's token from it."""

    def __init__(self, tmp_path: Path, env: dict[str, str] | None = None) -> None:
        self.path = tmp_path / "auth.json"
        self.env = env or {}
        self.oauth = _Refreshing()
        self.refreshed_providers = {"github-copilot"}
        self.cache = OAuthCredentialCache(
            providers=lambda provider: self.oauth,  # type: ignore[arg-type,return-value]
            now_ms=lambda: _NOW_MS,
        )

    def store(self, provider: str, entry: dict[str, object]) -> None:
        AuthStore(self.path).set(provider, entry)

    def token(self, provider: str) -> str | None:
        return provider_login_token(
            provider,
            auth_path=self.path,
            env=self.env,
            oauth_credentials=self.cache,
            refreshes=lambda provider: provider in self.refreshed_providers,
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


def test_a_stored_oauth_login_is_sent_as_stored(tmp_path: Path) -> None:
    # An extension provider's login, and any provider pipy does not refresh
    # before its own requests.
    logins = _Logins(tmp_path)
    logins.store(
        "radius", {"type": "oauth", "access": "at", "refresh": "rt", "expires": 1}
    )
    logins.store("broken", {"type": "oauth", "refresh": "rt"})

    assert logins.token("radius") == "at"
    assert logins.token("broken") is None
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

    # A provider nobody refreshes for is sent as stored, however old.
    logins.refreshed_providers.clear()
    logins.store(
        "github-copilot",
        {"type": "oauth", "access": "old", "refresh": "other", "expires": 1},
    )
    assert logins.token("github-copilot") == "old"
    assert logins.oauth.refreshed == ["at"]


def test_a_refresh_that_fails_is_no_token(tmp_path: Path) -> None:
    logins = _Logins(tmp_path)

    def refuse(credentials: Mapping[str, object]) -> dict[str, object]:
        raise OAuthError("refused")

    logins.oauth.refresh_token = refuse  # type: ignore[method-assign]
    logins.store(
        "github-copilot",
        {"type": "oauth", "access": "at", "refresh": "rt", "expires": 1},
    )

    assert logins.token("github-copilot") is None


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


def test_an_auth_file_that_cannot_be_read_is_no_token_also_not_from_the_environment(
    tmp_path: Path,
) -> None:
    # What the file stores for the provider is unknown then, so the
    # environment's key must not go out in its place.
    logins = _Logins(tmp_path, {"OPENROUTER_API_KEY": "env-key"})
    assert logins.token("openrouter") == "env-key"

    for damaged in ("{not json", "[]", "\xff"):
        logins.path.write_bytes(damaged.encode("latin-1"))
        assert logins.token("openrouter") is None

    logins.path.unlink()
    logins.path.mkdir()
    assert logins.token("openrouter") is None

    # An entry that is no object stores nothing, as for a model request.
    logins.path.rmdir()
    logins.path.write_text('{"openrouter": "x"}', encoding="utf-8")
    assert logins.token("openrouter") == "env-key"


# -- the session --------------------------------------------------------------


def _provider_state(tmp_path: Path, env: dict[str, str]) -> NativeReplProviderState:
    return NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        model_runtime=ModelRuntime(
            ProviderCatalogState(
                models_json_path=tmp_path / "models.json",
                auth_store=AuthStore(path=tmp_path / "auth.json"),
                env=env,
                openai_codex_auth_path=tmp_path / "missing-codex.json",
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


def test_the_session_refreshes_only_logins_of_the_built_in_providers(
    tmp_path: Path,
) -> None:
    state = _provider_state(tmp_path, {})
    catalog = state.model_runtime.catalog
    token = _provider_token(state)
    assert token is not None
    refreshes = token.keywords["refreshes"]  # type: ignore[attr-defined]

    assert refreshes("github-copilot") and refreshes("openai")
    assert not refreshes("anthropic") and not refreshes("radius")

    # An extension that registers `openai` after the function was built
    # replaces the provider: its login is not the built-in one any more.
    catalog.set_extension_provider_contributions(
        (
            RegisteredProvider(
                extension="ext",
                provider=ExtensionProvider(
                    name="OpenAI",
                    default_model="m",
                    models=("m",),
                    factory=lambda ctx: None,
                ),
            ),
        ),
        (),
    )
    assert not refreshes("openai")
    assert refreshes("github-copilot")


def test_a_session_with_an_injected_provider_has_no_token_function() -> None:
    provider = _Provider([])

    assert _provider_token(None) is None
    assert _provider_token(StaticNativeReplProviderState(provider)) is None  # type: ignore[arg-type]


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

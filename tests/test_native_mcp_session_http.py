"""An HTTP MCP server in a session (MCP parity plan S26).

A coding session with a scripted provider and the fake HTTP server in the
test's own process: the server of ``mcp.json`` is connected over streamable
HTTP with its configured headers, its tools are called like any other, a
server that forgot its session gets a new one in the middle of the session
(CON-12), and the session's end closes the connection. A server that signs
in with OAuth is sent the token stored for it, refreshed when the server
rejects it, and is reported once when it needs a sign-in (S30). A sign-in
made by ``pipy mcp login`` while the session runs is used from the next
turn on, and ``/mcp logout`` deletes the credentials (S31).
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

from test_native_extension_tool_metadata import (
    _result,
    _tool_names,
    _tool_results,
)
from test_native_mcp_auth_provider import _sign_in
from test_native_mcp_session import (
    _RESULTS,
    _SEARCH,
    _WIPE,
    _mcp_threads,
    _Provider,
    _run,
    _wait_until,
    _write_mcp_json,
)

from pipy_harness.models import HarnessStatus
from pipy_harness.native.mcp.credentials import (
    McpOAuthCredentialStore,
    mcp_auth_path,
)
from pipy_harness.native.mcp.shell_cli import McpCommandOptions, run_mcp_command
from pipy_harness.native.models import ProviderToolCall

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_http_server import FakeMcpHttpServer  # noqa: E402
from mcp_fake_oauth import FakeBrowser, FakeOAuthMcpServer  # noqa: E402

_SCENARIO: dict[str, Any] = {
    "tools": [_SEARCH, _WIPE],
    "toolResults": _RESULTS,
    "sessions": True,
}


def test_an_http_server_is_used_like_a_stdio_server_and_gets_a_new_session(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    monkeypatch.setenv("PIPY_TEST_MCP_TOKEN", "secret")
    workspace = tmp_path / "work"
    workspace.mkdir()
    before = set(threading.enumerate())
    with FakeMcpHttpServer(_SCENARIO) as server:
        _write_mcp_json(
            config / "mcp.json",
            {
                "docs": {
                    "url": server.url,
                    "headers": {"Authorization": "Bearer ${PIPY_TEST_MCP_TOKEN}"},
                    "exposure": "direct",
                }
            },
        )
        search = ProviderToolCall("c1", "mcp__docs__search", '{"query": "pipy"}')
        provider = _Provider(
            [
                _result(tool_calls=(search,)),
                # The server restarts between the two calls.
                server.expire_sessions,
                _result(
                    tool_calls=(
                        ProviderToolCall("c2", "mcp__docs__search", '{"query": "x"}'),
                    )
                ),
                _result(final_text="done"),
            ]
        )

        status, errors = _run(workspace, provider)

        assert status is HarnessStatus.SUCCEEDED, errors
        # The first prompt waited for the server, whose tools are direct.
        assert _tool_names(provider.requests[0])[-2:] == [
            "mcp__docs__search",
            "mcp__docs__wipe",
        ]
        for request in provider.requests[1:]:
            found = _tool_results(request)[-1]
            assert (found.is_error, found.content.value) == (
                False,
                "pipy guide\npipy faq",
            )
        calls = server.messages("tools/call")
        assert [call.headers["mcp-session-id"] for call in calls] == [
            "session-1",
            "session-1",
            "session-2",
        ]
        assert len(server.messages("initialize")) == 2
        assert {
            request.headers.get("authorization") for request in server.requests
        } == {"Bearer secret"}
        # The session's end closed the connection and ended the server's session.
        last = server.requests[-1]
        assert (last.method, last.headers["mcp-session-id"]) == ("DELETE", "session-2")
        _wait_until(lambda: _mcp_threads(before) == [])


def _whoami(call_id: str) -> ProviderToolCall:
    return ProviderToolCall(call_id, "mcp__issues__whoami", "{}")


def test_an_oauth_server_is_sent_its_stored_token_which_is_refreshed_when_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    workspace.mkdir()
    before = set(threading.enumerate())
    with FakeOAuthMcpServer() as server:
        _write_mcp_json(
            config / "mcp.json", {"issues": {"url": server.url, "exposure": "direct"}}
        )
        # Somebody signed in before the session started.
        store = McpOAuthCredentialStore(mcp_auth_path(config))
        first = _sign_in(server, store.for_server("issues", server.url))
        provider = _Provider(
            [
                _result(tool_calls=(_whoami("c1"),)),
                # The access token expires between the two calls.
                server.expire_access_tokens,
                _result(tool_calls=(_whoami("c2"),)),
                _result(final_text="done"),
            ]
        )

        status, errors = _run(workspace, provider)

        assert status is HarnessStatus.SUCCEEDED, errors
        assert "need attention" not in errors
        assert _tool_names(provider.requests[0])[-1] == "mcp__issues__whoami"
        tokens = store.tokens("issues", server.url)
        assert tokens is not None
        refreshed = tokens["access_token"]
        assert refreshed != first
        found = [_tool_results(request)[-1] for request in provider.requests[1:]]
        assert [(result.is_error, result.content.value) for result in found] == [
            (False, f"token {first}"),
            (False, f"token {refreshed}"),
        ]
        assert server.log.count("token refresh") == 1
        # The session's end closed the connection with the token in use.
        assert server.deletes == [refreshed]
        _wait_until(lambda: _mcp_threads(before) == [])


def test_an_oauth_server_nobody_signed_in_to_is_reported_once(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    workspace.mkdir()
    before = set(threading.enumerate())
    with FakeOAuthMcpServer() as server:
        _write_mcp_json(
            config / "mcp.json", {"issues": {"url": server.url, "exposure": "direct"}}
        )
        provider = _Provider([_result(final_text="done")])

        status, errors = _run(workspace, provider)

        assert status is HarnessStatus.SUCCEEDED, errors
        assert (
            errors.count(
                "MCP servers need attention:\n  issues: needs sign-in\nRun /mcp to fix."
            )
            == 1
        )
        assert "mcp__issues__whoami" not in _tool_names(provider.requests[0])
        # One request without a token; no browser, no registration.
        assert server.paths == ["/mcp"]
        assert not mcp_auth_path(config).exists()
        _wait_until(lambda: _mcp_threads(before) == [])


def test_uses_credentials_from_pipy_mcp_login_on_the_next_turn(
    tmp_path: Path, monkeypatch
) -> None:
    # Pi: "uses credentials from pi mcp login on the next turn". SES-18.
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    workspace.mkdir()
    before = set(threading.enumerate())
    with FakeOAuthMcpServer() as server:
        _write_mcp_json(
            config / "mcp.json", {"issues": {"url": server.url, "exposure": "direct"}}
        )
        output: list[str] = []

        def login() -> None:
            # The agent runs `pipy mcp login issues` through bash; the user
            # approves in the browser.
            status = run_mcp_command(
                ["login", "issues"],
                McpCommandOptions(
                    cwd=workspace,
                    config_home=config,
                    client_version="0.0.0-test",
                    log=output.append,
                    error=output.append,
                    open_url=lambda url: FakeBrowser().open(url),
                ),
            )
            assert status == 0, output

        provider = _Provider(
            [
                login,
                # The model does not have the tool in this turn yet.
                _result(tool_calls=(_whoami("c0"),)),
                _result(tool_calls=(_whoami("c1"),)),
                _result(final_text="done"),
            ]
        )

        status, errors = _run(workspace, provider)

        assert status is HarnessStatus.SUCCEEDED, errors
        assert output[-1] == 'Signed in to MCP server "issues" (1 tools).'
        # The session waited for a sign-in, and reconnected when the next
        # turn started: that turn's request declares the tool.
        assert "mcp__issues__whoami" not in _tool_names(provider.requests[0])
        assert _tool_names(provider.requests[1])[-1] == "mcp__issues__whoami"
        tokens = McpOAuthCredentialStore(mcp_auth_path(config)).tokens(
            "issues", server.url
        )
        assert tokens is not None
        answered = _tool_results(provider.requests[2])[-1]
        assert (answered.is_error, answered.content.value) == (
            False,
            f"token {tokens['access_token']}",
        )
        assert _tool_results(provider.requests[1])[-1].is_error
        _wait_until(lambda: _mcp_threads(before) == [])


def test_logout_in_a_session_and_login_only_with_a_terminal(
    tmp_path: Path, monkeypatch
) -> None:
    # Pi: "signs in through the browser, refreshes expired tokens, and signs
    # out" (the sign-out). SLH-04: a session without a terminal cannot sign
    # in.
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    workspace.mkdir()
    before = set(threading.enumerate())
    with FakeOAuthMcpServer() as server:
        _write_mcp_json(
            config / "mcp.json", {"issues": {"url": server.url, "exposure": "direct"}}
        )
        store = McpOAuthCredentialStore(mcp_auth_path(config))
        token = _sign_in(server, store.for_server("issues", server.url))
        provider = _Provider(
            [
                _result(tool_calls=(_whoami("c1"),)),
                _result(final_text="one"),
                _result(tool_calls=(_whoami("c2"),)),
                _result(final_text="two"),
            ]
        )

        status, errors = _run(
            workspace,
            provider,
            "go\n/mcp logout issues\n/mcp logout\n/mcp login issues\n/mcp\nagain\n",
        )

        assert status is HarnessStatus.SUCCEEDED, errors
        signed_out = errors.index('Signed out of MCP server "issues".')
        nothing = errors.index(
            'No stored credentials for MCP server "issues".', signed_out
        )
        refused = errors.index(
            'Signing in to MCP server "issues" requires interactive mode.', nothing
        )
        assert errors.index(
            "issues: needs sign-in, run /mcp login issues (direct)", refused
        )
        assert store.tokens("issues", server.url) is None
        first = _tool_results(provider.requests[1])[-1]
        assert (first.is_error, first.content.value) == (False, f"token {token}")
        # The tool is still declared, and a call says what to do.
        second = _tool_results(provider.requests[3])[-1]
        assert (second.is_error, second.content.value) == (
            True,
            'MCP server "issues" requires sign-in. Run /mcp to sign in.',
        )
        # The refused login contacted nobody.
        assert server.log == ["register", "token code", f"call {token}", "401 none"]
        _wait_until(lambda: _mcp_threads(before) == [])

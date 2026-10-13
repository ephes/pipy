"""An HTTP MCP server in a session (MCP parity plan S26).

A coding session with a scripted provider and the fake HTTP server in the
test's own process: the server of ``mcp.json`` is connected over streamable
HTTP with its configured headers, its tools are called like any other, a
server that forgot its session gets a new one in the middle of the session
(CON-12), and the session's end closes the connection. A server that signs
in with OAuth is sent the token stored for it, refreshed when the server
rejects it, and is reported once when it needs a sign-in (S30).
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
from pipy_harness.native.models import ProviderToolCall

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_http_server import FakeMcpHttpServer  # noqa: E402
from mcp_fake_oauth import FakeOAuthMcpServer  # noqa: E402

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

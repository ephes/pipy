"""An HTTP MCP server in a session (MCP parity plan S26).

A coding session with a scripted provider and the fake HTTP server in the
test's own process: the server of ``mcp.json`` is connected over streamable
HTTP with its configured headers, its tools are called like any other, a
server that forgot its session gets a new one in the middle of the session
(CON-12), and the session's end closes the connection.
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
from pipy_harness.native.models import ProviderToolCall

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_http_server import FakeMcpHttpServer  # noqa: E402

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

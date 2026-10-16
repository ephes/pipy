"""The notices of the MCP servers while the session waits for input (goal
check E9).

Pi's MCP extension calls ``reportProblems`` when the startup connections
settle and when servers registered later settle, whatever the user is doing
then (``extensions/mcp/index.ts``). pipy keeps the notices for the session
thread; the editor and the RPC worker show them while they wait for input.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from pty_sync import wait_for_output
from test_native_automation_prompt_dispatch import _mcp_json
from test_native_automation_rpc_prompt_dispatch import _Client
from test_native_extension_mcp_servers import _A, _Extensions
from test_native_mcp_readiness_pty import _finish, _launch
from test_native_mcp_reload import _Loads, _started
from test_native_mcp_session import _Provider

from pipy_harness.native.automation import rpc
from pipy_harness.native.coding.session_controller import (
    _NATIVE_CONTROL_EOF,
    _NATIVE_CONTROL_IDLE,
    _NATIVE_CONTROL_WAKE,
    _NativeSessionControlBridge,
)
from pipy_harness.native.mcp.registry import McpServerRegistry, RegisteredMcpServer
from pipy_harness.native.repl.mcp_session import McpSession

_TIMEOUT = 20


@pytest.fixture
def loads(tmp_path: Path) -> Iterator[_Loads]:
    value = _Loads(tmp_path)
    yield value
    for loaded in value.loaded:
        loaded.manager.close()


def test_idle_delivery_shows_the_notices_and_runs_no_hook(loads: _Loads) -> None:
    registry = McpServerRegistry()
    session = McpSession(loads, tools=loads.tools, notify=loads.notify)
    session.follow_extensions(lambda: _Extensions(registry))
    try:
        session.start()
        off = {"command": sys.executable, "enabled": False}
        registry.register(RegisteredMcpServer("ext", off, _A))
        session.servers.manager.post_notice("warning", "a server failed")

        session.deliver_idle_notices()

        assert loads.notices == [("warning", "a server failed")]
        # The change of the registrations is left for the session's own
        # point, where its `mcp_servers_change` hooks run.
        assert len(registry.take_changes()) == 1
    finally:
        session.close()


def test_idle_delivery_of_a_session_without_notices_shows_nothing(
    loads: _Loads,
) -> None:
    session = _started(loads)
    session.deliver_idle_notices()
    assert loads.notices == []
    session.close()


# -- RPC ------------------------------------------------------------------------


def test_the_rpc_worker_does_its_idle_work_while_it_waits() -> None:
    bridge = _NativeSessionControlBridge()
    reads = iter(
        [_NATIVE_CONTROL_IDLE, _NATIVE_CONTROL_IDLE, _NATIVE_CONTROL_WAKE]
        + [_NATIVE_CONTROL_IDLE, _NATIVE_CONTROL_EOF]
    )
    done: list[str] = []
    bridge.bind_idle_work(lambda: done.append("idle"))
    with pytest.raises(RuntimeError, match="already bound"):
        bridge.bind_idle_work(lambda: None)

    assert bridge._read_wake(lambda: next(reads)) is False
    assert done == ["idle", "idle"]
    assert bridge._read_wake(lambda: next(reads)) is True
    assert done == ["idle", "idle", "idle"]


def test_the_wake_channel_says_when_nothing_came(monkeypatch) -> None:
    monkeypatch.setattr(rpc, "_IDLE_POLL_SECONDS", 0.01)
    channel = rpc._WakeChannel()
    assert channel.readline() == _NATIVE_CONTROL_IDLE
    channel.wake()
    assert channel.readline() == _NATIVE_CONTROL_WAKE
    channel.signal_eof()
    assert channel.readline() == _NATIVE_CONTROL_EOF
    assert channel.readline() == _NATIVE_CONTROL_EOF


def test_an_rpc_client_gets_the_startup_report_without_a_command(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "work"
    _mcp_json(
        workspace,
        {"gone": {"command": str(tmp_path / "no-such-server"), "exposure": "direct"}},
    )
    client = _Client(workspace, _Provider([]))
    notice = client.until(lambda record: record.get("method") == "notify")[-1]
    rest = client.close()

    assert notice["notifyType"] == "warning"
    assert notice["message"].startswith("MCP servers need attention:\n  gone: ")
    assert not [record for record in rest if record.get("method") == "notify"]


# -- Terminal -------------------------------------------------------------------


def test_the_editor_shows_the_startup_report_while_it_waits(tmp_path: Path) -> None:
    # The server needs a sign-in; nothing is typed until the report is there.
    root = tmp_path / "session"
    process, fd, chunks, stop, reader = _launch(root, "login-cancel")
    try:
        assert wait_for_output(chunks, b"\x1b[?2004h", timeout=_TIMEOUT)
        assert wait_for_output(chunks, "MCP servers need attention:", timeout=_TIMEOUT)
        os.write(fd, b"/exit\r")
    finally:
        _finish(process, fd, chunks, stop, reader)
    assert (root / "finished").exists()

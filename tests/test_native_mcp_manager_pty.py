"""The ``/mcp`` manager in the real terminal UI (MCP parity plan S34,
MGR-01 to MGR-04, MGR-08, MGR-10)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pty_sync import wait_for_input_ready_after, wait_for_output
from test_native_mcp_readiness_pty import _TIMEOUT, _finish, _launch


def test_the_manager_disables_a_server_and_the_list_follows(tmp_path: Path) -> None:
    root = tmp_path / "session"
    process, fd, chunks, stop, reader = _launch(root, "manager")
    try:
        assert wait_for_output(chunks, b"\x1b[?2004h", timeout=_TIMEOUT)
        os.write(fd, b"/mcp\r")
        # MGR-01, MGR-02: the list, which follows the server until it is
        # connected, and what the keys do (MGR-10).
        listed = wait_for_output(
            chunks, "connected · 1 tool · direct · global", timeout=_TIMEOUT
        )
        assert listed is not None
        assert wait_for_output(chunks, " manage", timeout=_TIMEOUT)
        assert wait_for_output(chunks, " close", timeout=_TIMEOUT)
        # The view reads keys once its first frame was painted.
        ready = wait_for_input_ready_after(chunks, "MCP servers", timeout=_TIMEOUT)
        assert ready is not None
        os.write(fd, b"\r")
        # MGR-03, MGR-04: the server with its state and its actions.
        opened = wait_for_output(
            chunks, "MCP server slow", after=listed, timeout=_TIMEOUT
        )
        assert opened is not None
        assert wait_for_output(
            chunks, "State: connected · 1 tool", after=listed, timeout=_TIMEOUT
        )
        saved = wait_for_output(
            chunks, "saved to the global mcp.json", after=listed, timeout=_TIMEOUT
        )
        assert saved is not None
        # Tools, Reconnect, Exposure, Disable.
        os.write(fd, b"\x1b[B\x1b[B\x1b[B\r")
        # MGR-08: the menu stays and shows the server disabled.
        disabled = wait_for_output(
            chunks, "State: disabled", after=saved, timeout=_TIMEOUT
        )
        assert disabled is not None
        assert wait_for_output(chunks, "Enable", after=saved, timeout=_TIMEOUT)
        os.write(fd, b"\x1b")
        relisted = wait_for_output(
            chunks,
            "disabled · direct · global",
            after=disabled,
            timeout=_TIMEOUT,
        )
        assert relisted is not None
        os.write(fd, b"\x1b")
        # The editor is back.
        assert wait_for_output(
            chunks, b"\x1b[?2004h", after=max(ready, relisted), timeout=_TIMEOUT
        )
        os.write(fd, b"/exit\r")
    finally:
        _finish(process, fd, chunks, stop, reader)
    assert (root / "finished").exists()
    config = json.loads((root / "config" / "mcp.json").read_text())
    assert config["mcpServers"]["slow"]["enabled"] is False
    # Nothing was printed as a status line.
    assert b"slow: " not in b"".join(chunks)

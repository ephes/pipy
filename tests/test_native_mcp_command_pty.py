"""``/mcp`` in the real terminal UI (MCP parity plan S21, SLH-01; S34,
MGR-02).

The server never finishes connecting: the manager opens all the same, and
the wait of a subcommand has to be ended with Escape.
"""

from __future__ import annotations

import os
from pathlib import Path

from pty_sync import wait_for_input_ready_after, wait_for_output
from test_native_mcp_readiness_pty import (
    _TIMEOUT,
    _finish,
    _launch,
    _wait_for_initialize,
)


def test_mcp_is_in_the_menu_completes_and_can_be_interrupted(tmp_path: Path) -> None:
    root = tmp_path / "session"
    process, fd, chunks, stop, reader = _launch(root, "command")
    try:
        assert wait_for_output(chunks, b"\x1b[?2004h", timeout=_TIMEOUT)
        _wait_for_initialize(root)
        # SLH-01: the slash menu lists the command with Pi's description.
        os.write(fd, b"/mc")
        listed = wait_for_output(chunks, "Manage MCP servers", timeout=_TIMEOUT)
        assert listed is not None
        os.write(fd, b"p\r")
        # MGR-02: the manager opens without waiting for the server, which
        # is listed as it is.
        opened = wait_for_input_ready_after(
            chunks,
            "connecting\u2026 \u00b7 deferred \u00b7 global",
            after=listed,
            timeout=_TIMEOUT,
        )
        assert opened is not None
        # Escape closes it, and the editor is back.
        os.write(fd, b"\x1b")
        shown = wait_for_output(chunks, b"\x1b[?2004h", after=opened, timeout=_TIMEOUT)
        assert shown is not None
        # SLH-01: the arguments complete the action, then the server with
        # its state.
        os.write(fd, b"/mcp rec")
        action = wait_for_output(chunks, "reconnect", after=shown, timeout=_TIMEOUT)
        assert action is not None
        os.write(fd, b"onnect ")
        offered = wait_for_output(
            chunks, "connecting\u2026", after=action, timeout=_TIMEOUT
        )
        assert offered is not None
        # Enter takes the completion, a second Enter runs the command. It
        # waits for the startup connections too; ended with Escape, it is
        # not run.
        os.write(fd, b"\r")
        completed = wait_for_output(
            chunks, "reconnect slow", after=offered, timeout=_TIMEOUT
        )
        assert completed is not None
        os.write(fd, b"\r")
        assert (
            wait_for_output(chunks, "was not run", after=completed, timeout=1.0) is None
        )
        os.write(fd, b"\x1b")
        assert wait_for_input_ready_after(
            chunks,
            "/mcp reconnect was not run",
            after=completed,
            timeout=_TIMEOUT,
        )
        os.write(fd, b"/exit\r")
    finally:
        _finish(process, fd, chunks, stop, reader)
    assert (root / "finished").exists()

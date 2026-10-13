"""``/mcp login`` in the real terminal UI (MCP parity plan S31, SLH-04).

The server is the fake OAuth MCP server inside the session's process, and
the browser is a double (``mcp_tui_driver.py``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pty_sync import wait_for_input_ready_after, wait_for_output
from test_native_mcp_readiness_pty import _TIMEOUT, _finish, _launch


def _type(
    fd: int, chunks: list[bytes], action: str, state: str, after: int = 0
) -> None:
    """Type ``/mcp <action>``, take the server the completion offers with
    its ``state`` (SLH-01) and run the command."""

    os.write(fd, f"/mcp {action} ".encode())
    offered = wait_for_output(chunks, state, after=after, timeout=_TIMEOUT)
    assert offered is not None
    # Enter takes the completion, a second Enter runs the command.
    os.write(fd, b"\r")
    completed = wait_for_output(
        chunks, f"{action} issues", after=offered, timeout=_TIMEOUT
    )
    assert completed is not None
    os.write(fd, b"\r")


def test_mcp_login_signs_in_through_the_browser(tmp_path: Path) -> None:
    root = tmp_path / "session"
    process, fd, chunks, stop, reader = _launch(root, "login")
    try:
        assert wait_for_output(chunks, b"\x1b[?2004h", timeout=_TIMEOUT)
        _type(fd, chunks, "login", "needs sign-in")
        # The URL is shown while the sign-in waits for the browser ...
        shown = wait_for_output(
            chunks,
            'Sign in to MCP server "issues" in your browser:',
            timeout=_TIMEOUT,
        )
        assert shown is not None
        # ... and the browser's callback ends it.
        signed_in = wait_for_input_ready_after(
            chunks,
            'Signed in to MCP server "issues" (1 tools).',
            after=shown,
            timeout=_TIMEOUT,
        )
        assert signed_in is not None
        os.write(fd, b"/mcp\r")
        connected = wait_for_input_ready_after(
            chunks,
            "issues: connected, 1 tools (direct)",
            after=signed_in,
            timeout=_TIMEOUT,
        )
        assert connected is not None
        _type(fd, chunks, "logout", "connected \u00b7 1 tool", after=connected)
        assert wait_for_input_ready_after(
            chunks,
            'Signed out of MCP server "issues".',
            after=connected,
            timeout=_TIMEOUT,
        )
        os.write(fd, b"/exit\r")
    finally:
        _finish(process, fd, chunks, stop, reader)
    assert (root / "finished").exists()
    log = json.loads((root / "log.json").read_text())
    assert log[:3] == ["401 none", "register", "token code"]


def test_escape_cancels_a_login_that_waits_for_the_browser(tmp_path: Path) -> None:
    # OAU-28: the user never approves, and Escape ends the waiting.
    root = tmp_path / "session"
    process, fd, chunks, stop, reader = _launch(root, "login-cancel")
    try:
        assert wait_for_output(chunks, b"\x1b[?2004h", timeout=_TIMEOUT)
        _type(fd, chunks, "login", "needs sign-in")
        shown = wait_for_output(
            chunks,
            'Sign in to MCP server "issues" in your browser:',
            timeout=_TIMEOUT,
        )
        assert shown is not None
        assert (
            wait_for_output(chunks, "Sign-in cancelled.", after=shown, timeout=1.0)
            is None
        )
        os.write(fd, b"\x1b")
        cancelled = wait_for_input_ready_after(
            chunks, "Sign-in cancelled.", after=shown, timeout=_TIMEOUT
        )
        assert cancelled is not None
        os.write(fd, b"/mcp\r")
        assert wait_for_input_ready_after(
            chunks,
            "issues: needs sign-in, run /mcp login issues (direct)",
            after=cancelled,
            timeout=_TIMEOUT,
        )
        os.write(fd, b"/exit\r")
    finally:
        _finish(process, fd, chunks, stop, reader)
    assert (root / "finished").exists()
    log = json.loads((root / "log.json").read_text())
    assert "token code" not in log

"""``/mcp login`` in the real terminal UI (MCP parity plan S31, SLH-04; S34,
MGR-09: the sign-in screen).

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
        # The sign-in screen is shown while the sign-in runs; the browser's
        # callback ends it and closes the screen.
        shown = wait_for_output(chunks, "Sign in to issues", timeout=_TIMEOUT)
        assert shown is not None
        signed_in = wait_for_input_ready_after(
            chunks,
            'Signed in to MCP server "issues" (1 tools).',
            after=shown,
            timeout=_TIMEOUT,
        )
        assert signed_in is not None
        # The manager lists the server as connected.
        os.write(fd, b"/mcp\r")
        listed = wait_for_input_ready_after(
            chunks,
            "connected \u00b7 1 tool \u00b7 direct \u00b7 global",
            after=signed_in,
            timeout=_TIMEOUT,
        )
        assert listed is not None
        os.write(fd, b"\x1b")
        connected = wait_for_output(
            chunks, b"\x1b[?2004h", after=listed, timeout=_TIMEOUT
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
        # MGR-09: the sign-in screen has the URL, the copy key and the
        # input for a pasted redirect URL.
        # The screen opens with a status line, and reads keys from then on.
        opened = wait_for_input_ready_after(
            chunks, "Sign in to issues", timeout=_TIMEOUT
        )
        assert opened is not None
        shown = wait_for_output(
            chunks, "Approve access in your browser.", timeout=_TIMEOUT
        )
        assert shown is not None
        assert wait_for_output(chunks, "/authorize?", after=shown, timeout=_TIMEOUT)
        assert wait_for_output(chunks, " to copy", after=shown, timeout=_TIMEOUT)
        assert wait_for_output(chunks, "paste the URL", after=shown, timeout=_TIMEOUT)
        assert (
            wait_for_output(chunks, "Sign-in cancelled.", after=shown, timeout=1.0)
            is None
        )
        # A paste goes into the input; Escape cancels the sign-in.
        os.write(fd, b"\x1b[200~http://pasted.example/\x1b[201~")
        pasted = wait_for_output(
            chunks, "http://pasted.example/", after=shown, timeout=_TIMEOUT
        )
        assert pasted is not None
        os.write(fd, b"\x1b")
        cancelled = wait_for_input_ready_after(
            chunks, "Sign-in cancelled.", after=pasted, timeout=_TIMEOUT
        )
        assert cancelled is not None
        os.write(fd, b"/mcp\r")
        listed = wait_for_input_ready_after(
            chunks,
            "needs sign-in \u00b7 direct \u00b7 global",
            after=cancelled,
            timeout=_TIMEOUT,
        )
        assert listed is not None
        os.write(fd, b"\x1b")
        assert wait_for_output(chunks, b"\x1b[?2004h", after=listed, timeout=_TIMEOUT)
        os.write(fd, b"/exit\r")
    finally:
        _finish(process, fd, chunks, stop, reader)
    assert (root / "finished").exists()
    log = json.loads((root / "log.json").read_text())
    assert "token code" not in log

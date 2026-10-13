"""The row of an MCP tool call in the real terminal UI (MCP parity plan S35,
RND-01 and RND-02)."""

from __future__ import annotations

import os
from pathlib import Path

from pty_sync import wait_for_input_ready_after, wait_for_output
from test_native_mcp_readiness_pty import _TIMEOUT, _finish, _launch


def test_an_mcp_call_has_its_own_row_and_ctrl_o_expands_it(tmp_path: Path) -> None:
    root = tmp_path / "session"
    process, fd, chunks, stop, reader = _launch(root, "rows")
    try:
        assert wait_for_output(chunks, b"\x1b[?2004h", timeout=_TIMEOUT)
        os.write(fd, b"search the docs\r")
        # The first prompt waits for the server's `direct` tool. The title
        # is the server and the tool, not `mcp__slow__search`.
        titled = wait_for_output(chunks, "slow/search", timeout=_TIMEOUT)
        assert titled is not None
        assert wait_for_output(chunks, 'query="pi"', timeout=_TIMEOUT)
        # Collapsed: the first five of the eight lines and the hint.
        hinted = wait_for_output(
            chunks, "... (3 more lines,", after=titled, timeout=_TIMEOUT
        )
        assert hinted is not None
        assert wait_for_output(chunks, "hit 4", after=titled, timeout=_TIMEOUT)
        done = wait_for_input_ready_after(
            chunks, "S18_DONE", after=hinted, timeout=_TIMEOUT
        )
        assert done is not None
        output = b"".join(chunks).decode(errors="replace")
        assert "hit 5" not in output and "mcp__slow__search" not in output
        os.write(fd, b"\x0f")
        # Expanded: one line per argument and the whole result.
        assert wait_for_output(chunks, "query: pi", after=done, timeout=_TIMEOUT)
        assert wait_for_output(chunks, "hit 7", after=done, timeout=_TIMEOUT)
        os.write(fd, b"/exit\r")
    finally:
        _finish(process, fd, chunks, stop, reader)
    assert (root / "finished").exists()

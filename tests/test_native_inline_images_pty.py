"""Tool-result images drawn inline in a real terminal (Pi
``ToolExecutionComponent.updateDisplay`` with pi-tui ``Image``): an MCP
screenshot is placed with the Kitty graphics protocol after its row, Ctrl+O
deletes and places it again, and without an image protocol the row keeps its
text line."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pty_sync import wait_for_input_ready_after, wait_for_output
from test_native_mcp_readiness_pty import _TIMEOUT, _finish, _launch


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, protocol: str) -> str:
    monkeypatch.setenv("PIPY_IMAGE_PROTOCOL", protocol)
    root = tmp_path / "session"
    process, fd, chunks, stop, reader = _launch(root, "image")
    try:
        assert wait_for_output(chunks, b"\x1b[?2004h", timeout=_TIMEOUT)
        os.write(fd, b"take a screenshot\r")
        titled = wait_for_output(chunks, "slow/search", timeout=_TIMEOUT)
        assert titled is not None
        assert wait_for_output(chunks, "screenshot taken", timeout=_TIMEOUT)
        done = wait_for_input_ready_after(
            chunks, "S18_DONE", after=titled, timeout=_TIMEOUT
        )
        assert done is not None
        if protocol == "kitty":
            os.write(fd, b"\x0f")
            assert wait_for_output(
                chunks, "\x1b_Ga=d,d=I,", after=done, timeout=_TIMEOUT
            )
        os.write(fd, b"/exit\r")
    finally:
        _finish(process, fd, chunks, stop, reader)
    assert (root / "finished").exists()
    return b"".join(chunks).decode(errors="replace")


def test_a_kitty_terminal_draws_the_screenshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _run(tmp_path, monkeypatch, "kitty")
    placed = output.index("\x1b_Ga=T,f=100,q=2,C=1,c=60,r=")
    deleted = output.index("\x1b_Ga=d,d=I,")
    image_id = output[placed:].split(",i=", 1)[1].split(";", 1)[0]
    assert output[deleted:].startswith(f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\")
    # Ctrl+O placed it again with the same id, after the deletion.
    again = output.index(f",i={image_id};", deleted)
    assert again > deleted
    assert "[Image: [image/png]" not in output


def test_without_an_image_protocol_the_row_names_the_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _run(tmp_path, monkeypatch, "none")
    assert "[Image: [image/png] 90x36]" in output
    assert "\x1b_G" not in output and "\x1b]1337;" not in output

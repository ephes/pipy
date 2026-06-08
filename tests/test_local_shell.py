"""Tests for the local ``!``/``!!`` editor shell runner (Pi parity)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from pipy_harness.native.tools.bash import run_local_command


def test_runs_command_and_returns_output(tmp_path: Path) -> None:
    result = run_local_command("echo hello-shell", workspace_root=tmp_path)
    assert result.started
    assert result.exit_code == 0
    assert "hello-shell" in result.output
    assert not result.cancelled
    assert not result.timed_out


def test_streams_output_to_sink(tmp_path: Path) -> None:
    chunks: list[str] = []
    run_local_command(
        "printf 'a\\nb\\n'",
        workspace_root=tmp_path,
        output_sink=chunks.append,
    )
    assert "a" in "".join(chunks)


def test_nonzero_exit_code_is_reported(tmp_path: Path) -> None:
    result = run_local_command("exit 3", workspace_root=tmp_path)
    assert result.exit_code == 3
    assert not result.cancelled


def test_runs_in_workspace_root(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("x\n")
    result = run_local_command("ls", workspace_root=tmp_path)
    assert "marker.txt" in result.output


@pytest.mark.skipif(
    not Path("/bin/sh").exists(), reason="requires a POSIX shell"
)
def test_cancel_event_terminates_long_command(tmp_path: Path) -> None:
    cancel = threading.Event()

    def _cancel_soon() -> None:
        time.sleep(0.3)
        cancel.set()

    threading.Thread(target=_cancel_soon, daemon=True).start()
    start = time.monotonic()
    result = run_local_command(
        "sleep 30", workspace_root=tmp_path, cancel_event=cancel
    )
    elapsed = time.monotonic() - start
    assert result.cancelled
    assert elapsed < 5.0, "cancel did not interrupt the sleep promptly"

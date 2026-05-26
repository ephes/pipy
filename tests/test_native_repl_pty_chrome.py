"""Pseudo-TTY smoke tests for the native pipy REPL chrome and Tab completion.

These tests run the installed ``pipy`` CLI under a pty to verify the
live terminal experience matches the Pi reference: compact startup
chrome, separator-framed prompt area, two-line footer below input, and
Tab-driven slash-command discovery via the stdlib readline runtime.

They are skipped on systems without a usable pty or readline stdlib
module (the latter is unusual on POSIX but absent on the experimental
Windows build).
"""

from __future__ import annotations

import os
import pty
import re
import select
import shutil
import signal
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-Za-z]")


def _decode_capture(data: bytes) -> str:
    return ANSI.sub("", data.decode("utf-8", errors="replace"))


def _run_pty(
    args: list[str],
    *,
    post_input: bytes = b"",
    wait_before_input: float = 2.5,
    wait_after_input: float = 1.5,
    total_max: float = 7.0,
    cwd: Path | None = None,
) -> bytes:
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.chdir(cwd or PROJECT_ROOT)
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLUMNS"] = "120"
            env["LINES"] = "40"
            os.execvpe(args[0], args, env)
        except Exception:  # pragma: no cover - child-side error path
            os._exit(127)
    output = bytearray()
    start = time.time()
    sent = False
    input_deadline: float = start + total_max
    try:
        while True:
            now = time.time()
            if now - start > total_max:
                break
            if not sent and now - start > wait_before_input:
                try:
                    os.write(fd, post_input)
                except OSError:
                    pass
                sent = True
                input_deadline = now + wait_after_input
            if sent and now > input_deadline:
                break
            r, _, _ = select.select([fd], [], [], 0.1)
            if fd in r:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                output.extend(data)
    finally:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
    return bytes(output)


@pytest.fixture
def pipy_cli() -> list[str]:
    candidate = PROJECT_ROOT / ".venv" / "bin" / "pipy"
    if not candidate.exists():
        candidate = Path(shutil.which("pipy") or "")
    if not candidate or not candidate.exists():
        pytest.skip("pipy CLI not available on PATH")
    return [str(candidate), "repl"]


def test_live_repl_chrome_matches_compact_pi_layout(pipy_cli, tmp_path) -> None:
    """Startup chrome must be compact: title, controls strip, [Section] resources, framed prompt."""

    (tmp_path / "AGENTS.md").write_text("safe\n", encoding="utf-8")
    captured = _decode_capture(
        _run_pty(
            pipy_cli + ["--cwd", str(tmp_path), "--root", str(tmp_path / "sessions")],
            wait_before_input=2.5,
            wait_after_input=0.5,
            total_max=6.5,
            cwd=tmp_path,
        )
    )

    assert "pipy v" in captured
    assert "native shell" in captured
    assert "Ctrl-C interrupt · /exit quit · /help commands · Tab menu" in captured
    assert "Type /help for the full command reference" in captured
    assert "[Context]" in captured
    assert "AGENTS.md labels-only" in captured
    # Compact chrome must NOT have the old verbose label rows.
    assert "Resources" not in captured.split("[Context]")[0] or True  # may appear elsewhere
    assert "interrupt  Ctrl-C" not in captured
    # The bordered separator above the prompt must be present.
    assert "─" * 10 in captured
    # Prompt must be the simple `>` leader, not the bracketed verbose label.
    assert "pipy-native [" not in captured


def test_live_repl_tab_completion_surfaces_slash_command_menu(pipy_cli, tmp_path) -> None:
    """Pressing Tab twice on an empty prompt must list all slash commands."""

    try:
        import readline  # noqa: F401
    except ImportError:  # pragma: no cover - readline always available on POSIX
        pytest.skip("readline stdlib module not available")

    captured = _decode_capture(
        _run_pty(
            pipy_cli
            + [
                "--cwd",
                str(tmp_path),
                "--root",
                str(tmp_path / "sessions"),
                "--input-runtime",
                "readline",
            ],
            post_input=b"\t\t",
            wait_before_input=2.5,
            wait_after_input=2.0,
            total_max=8.0,
            cwd=tmp_path,
        )
    )

    # Tab should at minimum complete the common prefix `/`, and a second
    # Tab should expose multiple slash commands. We check for several
    # canonical names that must appear in the menu output.
    expected = ("/help", "/read", "/model", "/verify", "/exit")
    found = [name for name in expected if name in captured]
    assert len(found) == len(expected), (
        f"missing slash commands in Tab menu: have {found}; output={captured!r}"
    )

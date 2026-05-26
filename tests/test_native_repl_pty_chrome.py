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
    """Startup chrome must be Pi-shape: title, controls strip, [Section] listings,
    separator-framed prompt, and persistent bottom status block (cwd + status line).
    """

    (tmp_path / "AGENTS.md").write_text("safe\n", encoding="utf-8")
    skills_root = tmp_path / ".claude" / "skills"
    (skills_root / "my-skill").mkdir(parents=True)
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
    # Controls strip uses Pi-style lowercase shortcuts and middot separators.
    assert "ctrl+c interrupt" in captured
    assert "/ commands" in captured
    assert "Type / to open the command menu" in captured
    assert "[Context]" in captured
    # Context lists the project-local AGENTS.md source by name.
    assert "AGENTS.md" in captured.split("[Context]")[1].split("[")[0]
    # Skills section must surface a project-local skill the workspace provides.
    assert "[Skills]" in captured
    assert "my-skill" in captured.split("[Skills]")[1]
    # The bordered separator above the prompt must be present.
    assert "─" * 10 in captured
    # The persistent bottom status block (cwd + status line) must render below
    # the prompt area, not above it. The status line must include cost,
    # plan/subscription tag, context meter, provider, model, and effort label.
    assert "$0.000" in captured
    assert "(sub)" in captured or "(api)" in captured
    # Provider/model appear in parenthesized form `(provider) model`. The
    # exact provider id depends on credentials available to the subprocess.
    import re

    provider_match = re.search(r"\(([\w\-]+)\)\s+\S+\s+•\s+\w+", captured)
    assert provider_match, (
        f"expected '(provider) model • effort' status segment; output={captured!r}"
    )
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


def test_live_repl_slash_keystroke_opens_command_menu_with_descriptions(
    pipy_cli, tmp_path
) -> None:
    """Pressing `/` on the default runtime must open a Pi-like command menu.

    The default ``pipy repl`` invocation must select the stdlib slash-menu
    input runtime when stdin/stderr are real TTYs. A single ``/`` keystroke
    should surface a popup menu with command names and dim descriptions
    rendered beneath the input line. We do not force ``--input-runtime``
    here: the test verifies the default user-facing experience.
    """

    captured = _decode_capture(
        _run_pty(
            pipy_cli
            + [
                "--cwd",
                str(tmp_path),
                "--root",
                str(tmp_path / "sessions"),
                "--native-provider",
                "fake",
            ],
            post_input=b"/",
            wait_before_input=2.5,
            wait_after_input=1.5,
            total_max=7.0,
            cwd=tmp_path,
        )
    )

    # The `/` keystroke alone should surface the top slash commands. Tab
    # is not required. The menu caps the visible row count to keep the
    # popup compact (matching Pi); long lists overflow to a "… N more"
    # tail handled by the editor.
    expected_names = ("/help", "/clear", "/status", "/model")
    found_names = [name for name in expected_names if name in captured]
    assert len(found_names) == len(expected_names), (
        f"missing slash commands in `/` menu: have {found_names}; "
        f"output={captured!r}"
    )
    # The overflow tail must indicate that there are additional commands.
    assert "… " in captured and " more" in captured, (
        f"expected '… N more' overflow tail; output={captured!r}"
    )
    # The menu must include descriptive metadata next to at least one name,
    # not just the bare list (this is what distinguishes the Pi-style
    # popup from a flat Tab completion list).
    assert "Show pipy command reference" in captured or (
        "Show REPL state" in captured
    ), (
        "slash menu must render command descriptions, not bare names; "
        f"output={captured!r}"
    )
    # The slash menu must include a Pi-style position indicator like (1/N)
    # on the highlighted row so the user knows the menu offset.
    import re

    assert re.search(r"\(1/\d+\)", captured), (
        f"slash menu must show (selected/total) counter; output={captured!r}"
    )


def test_live_repl_default_runtime_does_not_force_alternate_input(
    pipy_cli, tmp_path
) -> None:
    """The default ``pipy repl`` invocation must select the slash-menu adapter.

    The session start event records the resolved ``input_runtime`` in the
    finalized JSONL. The default invocation must report ``slash-menu`` —
    falling back to readline or plain would indicate the goal's
    Pi-parity contract is not actually exercised by default users.
    """

    import json

    _run_pty(
        pipy_cli
        + [
            "--cwd",
            str(tmp_path),
            "--root",
            str(tmp_path / "sessions"),
            "--native-provider",
            "fake",
        ],
        post_input=b"/exit\r",
        wait_before_input=2.5,
        wait_after_input=2.5,
        total_max=8.0,
        cwd=tmp_path,
    )

    finalized = list((tmp_path / "sessions" / "pipy").glob("*/*/*.jsonl"))
    assert finalized, "the REPL must finalize a session record"
    events = [
        json.loads(line)
        for line in finalized[0].read_text(encoding="utf-8").splitlines()
    ]
    start_events = [event for event in events if event["type"] == "native.session.started"]
    assert start_events, "missing native.session.started event"
    payload = start_events[0]["payload"]
    assert payload.get("input_runtime") == "slash-menu", (
        f"default invocation should resolve to slash-menu; got {payload.get('input_runtime')!r}"
    )

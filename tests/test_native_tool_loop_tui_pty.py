"""Real-PTY integration tests for the inline product TUI.

These exercise the actual product paint path (`ToolLoopTerminalUi.paint` and
`read_line` over a real pseudo-TTY), not `render_lines()` internals. They prove
the ergonomics the goal requires: the inline renderer never enters the
alternate screen (so native scrollback in Ghostty/zellij can review prior
output), long answers scroll into that scrollback so the full window height is
used with the input/footer pinned at the bottom, and `/copy` executes locally
through the real session command path without an extra provider turn.

Input is synchronized around the active-turn Escape watcher: while a provider
turn runs, the watcher consumes stdin looking for Escape, so any follow-up
input (here `/copy`) must be sent only after the turn's answer is on screen.
"""

from __future__ import annotations

import os
import pty
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TextIO, cast

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import FakeNativeProvider, NativeToolReplSession
from pipy_harness.native.clipboard import ClipboardResult
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.repl_state import (
    NativeReplProviderState,
    StaticNativeReplProviderState,
)
from pipy_harness.native.terminal_screen import parse_ansi_screen
from pipy_harness.native.tui import ToolLoopTerminalUi


class _ClipboardRecorder:
    def __init__(self) -> None:
        self.copies: list[str] = []

    def __call__(self, text: str, **kwargs: object) -> ClipboardResult:
        self.copies.append(text)
        return ClipboardResult(
            copied=True,
            method="osc52",
            byte_count=len(text.encode("utf-8")),
            detail="copied",
        )


def _spawn_live_drainer(fd: int) -> tuple[threading.Thread, list[bytes]]:
    collected: list[bytes] = []

    def _drain() -> None:
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            collected.append(chunk)

    thread = threading.Thread(target=_drain, daemon=True)
    thread.start()
    return thread, collected


def _wait_for(collected: list[bytes], needle: str, *, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    encoded = needle.encode("utf-8")
    while time.monotonic() < deadline:
        if encoded in b"".join(collected):
            return True
        time.sleep(0.02)
    return False


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("columns", "rows", "label"),
    [(100, 40, "ghostty"), (80, 24, "zellij")],
)
def test_pty_inline_tui_full_height_scrollback_and_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    columns: int,
    rows: int,
    label: str,
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    # `_dimensions()` reads the terminal size via ``shutil.get_terminal_size``,
    # which honors COLUMNS/LINES first — pin the viewport deterministically.
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8")
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    # A long answer that overflows the window once committed, so it must scroll
    # into native scrollback (proving full-height use, not an upper-half cap).
    # It is delivered as buffered final_text (not streamed) so it is appended
    # only at end_provider_turn — after the active-turn Escape watcher has
    # stopped — which makes SCROLL_MARKER_DONE a safe point to send `/copy`.
    answer = "\n".join(f"answer line {index:02d}" for index in range(60))
    answer += "\nSCROLL_MARKER_DONE"
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=((),),
        final_text=answer,
    )
    recorder = _ClipboardRecorder()
    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = NativeToolReplSession(
        provider=provider,
        tool_registry={},
        clipboard_copy=recorder,
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None: ui,
    )

    result_holder: list[object] = []

    def _run() -> None:
        result_holder.append(
            session.run(
                workspace_root=tmp_path,
                input_stream=cast(TextIO, stdin),
                output_stream=cast(TextIO, terminal),
                error_stream=cast(TextIO, terminal),
            )
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        os.write(in_master, b"tell me everything\n")
        # Wait for the turn to finish (answer committed) before sending more
        # input, so the active-turn Escape watcher does not eat `/copy`.
        assert _wait_for(err_chunks, "SCROLL_MARKER_DONE"), "answer never rendered"
        os.write(in_master, b"/copy\n")
        assert _wait_for(err_chunks, "copied last answer"), "/copy notice never shown"
        os.write(in_master, b"\x04")  # ctrl-d on an empty prompt ends the loop
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        terminal.flush()
        terminal.close()
        stdin.close()
        err_thread.join(timeout=8.0)
        os.close(in_master)
        os.close(err_master)

    assert not worker.is_alive(), f"{label} session did not exit"
    assert result_holder, f"{label} session produced no result"
    result = result_holder[0]
    assert getattr(result, "status") == HarnessStatus.SUCCEEDED

    captured = b"".join(err_chunks).decode("utf-8", errors="replace")

    # Inline model: never the alternate screen, so native scrollback works.
    assert "\x1b[?1049h" not in captured

    # `/copy` executed locally through the real command path, copied the last
    # answer, and added no extra provider turn.
    assert recorder.copies, f"{label}: /copy did not copy anything"
    assert "SCROLL_MARKER_DONE" in recorder.copies[-1]
    assert provider._call_counter[0] == 1

    # The committed answer overflowed and scrolled into native scrollback:
    # the final viewport uses the full window with the input/footer at the
    # bottom rather than capping the content to the upper half.
    snapshot = parse_ansi_screen(captured, columns=columns, rows=rows)
    assert snapshot.viewport_y > 0, f"{label}: history never scrolled"
    separator_rows = [
        index
        for index, line in enumerate(snapshot.viewport)
        if line.strip() and set(line.strip()) == {"─"}
    ]
    assert separator_rows, f"{label}: input frame separators missing"
    # The input frame lives in the lower portion of the window (full height).
    assert max(separator_rows) >= rows - 6

    # Scroll/review: the earliest answer line was printed into the buffer but
    # has scrolled off the live viewport. Because the renderer never used the
    # alternate screen, it remains in the terminal's native scrollback for the
    # user to scroll up and review while the input/footer frame stays put.
    final_viewport = "\n".join(snapshot.viewport)
    assert "answer line 00" in captured
    assert "answer line 00" not in final_viewport


class _RecordingProvider:
    """Tool-capable provider recording the (provider, model) of each turn."""

    def __init__(
        self, provider_name: str, model_id: str, seen: list[tuple[str, str]]
    ) -> None:
        self._provider_name = provider_name
        self.model_id = model_id
        self._seen = seen
        self.supports_tool_calls = True

    @property
    def name(self) -> str:
        return self._provider_name

    def complete(self, request, *, stream_sink=None, reasoning_sink=None):
        from datetime import UTC, datetime

        from pipy_harness.native.models import ProviderResult

        del stream_sink, reasoning_sink
        self._seen.append((request.provider_name, request.model_id))
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=request.provider_name,
            model_id=request.model_id,
            started_at=now,
            ended_at=now,
            final_text="RESPONSE_MARKER_DONE",
            tool_calls=(),
        )


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_inline_tui_model_selector_selects_and_rebinds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Drive the real product `/model` selector over a PTY end-to-end.

    Proves the acceptance path: opening `/model` shows a keyboard-navigable
    selector with availability state, no provider turn happens during selection,
    choosing an available provider updates the footer model label, and the very
    next prompt's provider turn is constructed with the newly selected
    provider/model.
    """

    from pipy_harness.native import NativeModelSelection, NativeReplProviderState

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8")
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    seen: list[tuple[str, str]] = []

    def factory(selection: NativeModelSelection) -> _RecordingProvider:
        return _RecordingProvider(selection.provider_name, selection.model_id, seen)

    provider_state = NativeReplProviderState(
        selection=NativeModelSelection("openrouter", "openai/gpt-5.1-codex"),
        provider_factory=factory,
        env={"OPENROUTER_API_KEY": "k", "OPENAI_API_KEY": "k2"},
        openai_codex_auth_path=tmp_path / "missing-openai-codex.json",
        persist_defaults=False,
    )
    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = NativeToolReplSession(
        provider=provider_state.current_provider(),
        provider_state=provider_state,
        tool_registry={},
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None: ui,
    )

    result_holder: list[object] = []

    def _run() -> None:
        result_holder.append(
            session.run(
                workspace_root=tmp_path,
                input_stream=cast(TextIO, stdin),
                output_stream=cast(TextIO, terminal),
                error_stream=cast(TextIO, terminal),
            )
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        # Open the interactive selector.
        os.write(in_master, b"/model\n")
        assert _wait_for(err_chunks, "Select provider/model"), "selector never opened"
        # The selector lists availability state and reasons.
        assert _wait_for(err_chunks, "[available]"), "availability state missing"
        # Opening the selector runs no provider turn.
        assert seen == [], "selector opened a provider turn"
        # Navigate up from the current openrouter row to the openai row, then
        # choose it. (model_options order: fake, openai-codex, openai,
        # openrouter, ... — current is openrouter, one step up is openai.)
        os.write(in_master, b"\x1b[A")  # up arrow
        os.write(in_master, b"\r")  # enter selects the highlighted available row
        assert _wait_for(
            err_chunks, "selected model openai/gpt-5.5"
        ), "selection notice never shown"
        # No provider turn during selection.
        assert seen == [], "selection itself ran a provider turn"
        # The next prompt is constructed with the newly selected provider/model.
        os.write(in_master, b"hi\n")
        assert _wait_for(err_chunks, "RESPONSE_MARKER_DONE"), "turn never ran"
        os.write(in_master, b"\x04")  # ctrl-d on an empty prompt ends the loop
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        terminal.flush()
        terminal.close()
        stdin.close()
        err_thread.join(timeout=8.0)
        os.close(in_master)
        os.close(err_master)

    assert not worker.is_alive(), "selector session did not exit"
    assert result_holder, "selector session produced no result"
    result = result_holder[0]
    assert getattr(result, "status") == HarnessStatus.SUCCEEDED
    assert getattr(result, "provider_name") == "openai"
    assert getattr(result, "model_id") == "gpt-5.5"

    captured = b"".join(err_chunks).decode("utf-8", errors="replace")
    # Inline model: never the alternate screen.
    assert "\x1b[?1049h" not in captured
    # Exactly one provider turn ran, and it used the newly selected selection.
    assert seen == [("openai", "gpt-5.5")]

    # The footer/status model label updated to the new selection.
    snapshot = parse_ansi_screen(captured, columns=100, rows=40)
    footer_text = "\n".join(snapshot.viewport)
    assert "gpt-5.5" in footer_text


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_inline_tui_slash_menu_is_honest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "30")

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8")
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    provider = FakeNativeProvider(supports_tool_calls=True)
    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = NativeToolReplSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None: ui,
    )

    worker = threading.Thread(
        target=lambda: session.run(
            workspace_root=tmp_path,
            input_stream=cast(TextIO, stdin),
            output_stream=cast(TextIO, terminal),
            error_stream=cast(TextIO, terminal),
        ),
        daemon=True,
    )
    worker.start()
    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        # Open the slash menu; it must list only executable commands.
        os.write(in_master, b"/")
        assert _wait_for(err_chunks, "copy"), "slash menu never offered /copy"
        # Snapshot the live menu before tearing down.
        snapshot = parse_ansi_screen(
            b"".join(err_chunks).decode("utf-8", errors="replace"),
            columns=100,
            rows=30,
        )
        os.write(in_master, b"\x03")  # ctrl-c clears the prompt and exits
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x03")
        except OSError:
            pass
        terminal.flush()
        terminal.close()
        stdin.close()
        err_thread.join(timeout=8.0)
        os.close(in_master)
        os.close(err_master)

    menu_text = "\n".join(snapshot.viewport)
    assert provider._call_counter[0] == 0  # opening the menu runs no turn
    # Honest menu: the leading executable local commands are offered — now
    # including the executable /login and /logout auth commands (the menu
    # windows to six rows, so /exit and /quit scroll below the fold but are
    # still reachable; the unit tests cover the full advertised set).
    for executable in ("help", "model", "settings", "login", "logout", "copy"):
        assert executable in menu_text, f"menu missing /{executable}"


class _PromptRecordingProvider:
    """Tool-capable provider recording each turn's submitted user prompt."""

    name = "fake"
    model_id = "fake-native-bootstrap"
    supports_tool_calls = True

    def __init__(self, prompts: list[str]) -> None:
        self._prompts = prompts
        self._turn = 0

    def complete(self, request, *, stream_sink=None, reasoning_sink=None):
        from datetime import UTC, datetime

        from pipy_harness.native.models import ProviderResult

        del stream_sink, reasoning_sink
        self._prompts.append(request.user_prompt)
        self._turn += 1
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=request.provider_name,
            model_id=request.model_id,
            started_at=now,
            ended_at=now,
            final_text=f"TURN_{self._turn}_DONE",
            tool_calls=(),
        )


def _run_editor_pty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider,
    drive,
    *,
    columns: int = 100,
    rows: int = 40,
):
    """Drive the real product TUI over a PTY; ``drive(in_master, chunks)``."""

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8")
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = NativeToolReplSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None: ui,
    )

    worker = threading.Thread(
        target=lambda: session.run(
            workspace_root=tmp_path,
            input_stream=cast(TextIO, stdin),
            output_stream=cast(TextIO, terminal),
            error_stream=cast(TextIO, terminal),
        ),
        daemon=True,
    )
    worker.start()
    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        drive(in_master, err_chunks)
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        terminal.flush()
        terminal.close()
        stdin.close()
        err_thread.join(timeout=8.0)
        os.close(in_master)
        os.close(err_master)

    assert not worker.is_alive(), "editor session did not exit"
    return b"".join(err_chunks).decode("utf-8", errors="replace")


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_prompt_history_recall_edits_and_submits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    prompts: list[str] = []
    provider = _PromptRecordingProvider(prompts)

    def drive(in_master: int, chunks: list[bytes]) -> None:
        os.write(in_master, b"recall me\n")
        assert _wait_for(chunks, "TURN_1_DONE"), "first turn never ran"
        # Up recalls the prior prompt into the editable buffer; appending text
        # and submitting proves recall populated the editor (not a fresh line).
        os.write(in_master, b"\x1b[A again\n")
        assert _wait_for(chunks, "TURN_2_DONE"), "recalled turn never ran"

    captured = _run_editor_pty(monkeypatch, tmp_path, provider, drive)
    assert "\x1b[?1049h" not in captured
    assert prompts == ["recall me", "recall me again"]


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_bracketed_paste_inserts_multiline_without_submitting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    prompts: list[str] = []
    provider = _PromptRecordingProvider(prompts)

    def drive(in_master: int, chunks: list[bytes]) -> None:
        # A bracketed paste carrying an embedded newline must insert literally
        # and must NOT submit on that newline. Only the trailing real Enter
        # submits, so the provider sees the whole multi-line text as one prompt.
        os.write(in_master, b"\x1b[200~line one\nline two\x1b[201~")
        os.write(in_master, b"\n")
        assert _wait_for(chunks, "TURN_1_DONE"), "pasted prompt never submitted"

    captured = _run_editor_pty(monkeypatch, tmp_path, provider, drive)
    assert "\x1b[?1049h" not in captured
    assert prompts == ["line one\nline two"]


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("columns", "rows", "label"),
    [(100, 40, "ghostty"), (80, 24, "zellij")],
)
def test_pty_multiline_paste_keeps_frame_coherent_before_submit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    columns: int,
    rows: int,
    label: str,
):
    """A multi-line paste sitting in the editor keeps the live frame coherent.

    This parses the real terminal screen *before* Enter is pressed: the pasted
    newline renders as one visible glyph on a single input row framed by
    separators with the footer pinned below, so the inline live-region math
    stays correct. Only then is Enter pressed, and the provider must receive the
    exact literal multi-line prompt.
    """

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))

    prompts: list[str] = []
    provider = _PromptRecordingProvider(prompts)

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8")
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = NativeToolReplSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None: ui,
    )

    worker = threading.Thread(
        target=lambda: session.run(
            workspace_root=tmp_path,
            input_stream=cast(TextIO, stdin),
            output_stream=cast(TextIO, terminal),
            error_stream=cast(TextIO, terminal),
        ),
        daemon=True,
    )
    worker.start()
    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        # Paste a multi-line prompt but do NOT submit yet.
        os.write(in_master, b"\x1b[200~line one\nline two\x1b[201~")
        assert _wait_for(err_chunks, "line one⏎line two"), "paste glyph never rendered"
        # Parse the live screen while the paste is still in the editor.
        before_submit = parse_ansi_screen(
            b"".join(err_chunks).decode("utf-8", errors="replace"),
            columns=columns,
            rows=rows,
        )
        viewport = before_submit.viewport
        input_rows = [
            index for index, line in enumerate(viewport) if "line one" in line
        ]
        assert len(input_rows) == 1, f"{label}: input not on exactly one row"
        input_index = input_rows[0]
        # Both halves of the paste live on the SAME physical row (one glyph
        # joins them) — the newline never spilled the cell onto a second row.
        assert "line two" in viewport[input_index]
        assert "line one⏎line two" in viewport[input_index]
        # The input row is framed by separators with the footer pinned below.
        assert set(viewport[input_index - 1].strip()) == {"─"}
        assert set(viewport[input_index + 1].strip()) == {"─"}
        assert any(line.strip() for line in viewport[input_index + 2 :]), (
            f"{label}: footer row missing below the input frame"
        )
        # Now submit; the provider must receive the exact literal multi-line text.
        os.write(in_master, b"\n")
        assert _wait_for(err_chunks, "TURN_1_DONE"), "pasted prompt never submitted"
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        terminal.flush()
        terminal.close()
        stdin.close()
        err_thread.join(timeout=8.0)
        os.close(in_master)
        os.close(err_master)

    assert not worker.is_alive(), f"{label}: paste session did not exit"
    captured = b"".join(err_chunks).decode("utf-8", errors="replace")
    assert "\x1b[?1049h" not in captured
    assert prompts == ["line one\nline two"]


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_undo_redo_restores_line_before_submit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    prompts: list[str] = []
    provider = _PromptRecordingProvider(prompts)

    def drive(in_master: int, chunks: list[bytes]) -> None:
        # Type "hi", undo (ctrl-z) -> "h", redo (ctrl-y) -> "hi", submit.
        os.write(in_master, b"hi\x1a\x19\n")
        assert _wait_for(chunks, "TURN_1_DONE"), "first turn never ran"
        # Type "abx", undo twice -> "a", submit.
        os.write(in_master, b"abx\x1a\x1a\n")
        assert _wait_for(chunks, "TURN_2_DONE"), "second turn never ran"

    captured = _run_editor_pty(monkeypatch, tmp_path, provider, drive)
    assert "\x1b[?1049h" not in captured
    assert prompts == ["hi", "a"]


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_at_file_reference_loads_bounded_context_into_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A typed `@file` reference in the product TUI loads bounded context.

    This drives the real `ToolLoopTerminalUi.read_line` over a PTY (not the
    captured-stream fallback used by the adapter-level test), proving the
    user-directed `@file` context resolves on the same submitted-prompt path the
    TUI uses: the user's literal text is preserved and the bounded excerpt is
    appended into the provider turn.
    """

    (tmp_path / "notes.txt").write_text(
        "AT_FILE_TUI_ALPHA\nAT_FILE_TUI_BETA\n", encoding="utf-8"
    )
    prompts: list[str] = []
    provider = _PromptRecordingProvider(prompts)

    def drive(in_master: int, chunks: list[bytes]) -> None:
        os.write(in_master, b"summarize @notes.txt please\n")
        assert _wait_for(chunks, "TURN_1_DONE"), "turn with @file never ran"

    captured = _run_editor_pty(monkeypatch, tmp_path, provider, drive)
    assert "\x1b[?1049h" not in captured
    assert len(prompts) == 1
    # The user's literal prompt text is preserved, and the bounded excerpt for
    # the @file reference is loaded into the provider turn.
    assert "summarize @notes.txt please" in prompts[0]
    assert "AT_FILE_TUI_ALPHA" in prompts[0]
    assert "AT_FILE_TUI_BETA" in prompts[0]


class _FileBackedAuthManager:
    """Fake openai-codex auth manager backed by a credentials file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def login_interactive(self, *, input_stream, output_stream, open_browser=True):
        del input_stream, open_browser
        # Safe status notice only — never an OAuth URL/token in tests.
        output_stream.write("pipy: completing fake openai-codex login\n")
        output_stream.flush()
        self._path.write_text("{}", encoding="utf-8")
        return None

    def logout(self) -> bool:
        if self._path.exists():
            self._path.unlink()
            return True
        return False


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_login_then_logout_updates_availability_without_provider_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from pipy_harness.native import NativeModelSelection, NativeReplProviderState
    from pipy_harness.native.openai_codex_provider import OpenAICodexAuthManager

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8")
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    auth_path = tmp_path / "openai-codex.json"
    seen: list[tuple[str, str]] = []

    def factory(selection: NativeModelSelection) -> _RecordingProvider:
        return _RecordingProvider(selection.provider_name, selection.model_id, seen)

    manager = _FileBackedAuthManager(auth_path)
    provider_state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        provider_factory=factory,
        auth_manager_factory=lambda: cast(OpenAICodexAuthManager, manager),
        env={},
        openai_codex_auth_path=auth_path,
        persist_defaults=False,
    )

    def codex_available() -> bool:
        return next(
            option.available
            for option in provider_state.model_options()
            if option.selection.provider_name == "openai-codex"
        )

    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = NativeToolReplSession(
        provider=provider_state.current_provider(),
        provider_state=provider_state,
        tool_registry={},
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None: ui,
    )

    worker = threading.Thread(
        target=lambda: session.run(
            workspace_root=tmp_path,
            input_stream=cast(TextIO, stdin),
            output_stream=cast(TextIO, terminal),
            error_stream=cast(TextIO, terminal),
        ),
        daemon=True,
    )
    worker.start()
    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        assert codex_available() is False
        os.write(in_master, b"/login\n")
        assert _wait_for(err_chunks, "login stored"), "/login notice never shown"
        assert codex_available() is True, "login did not refresh availability"
        os.write(in_master, b"/logout\n")
        assert _wait_for(err_chunks, "credentials removed"), "/logout notice never shown"
        assert codex_available() is False, "logout did not refresh availability"
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        terminal.flush()
        terminal.close()
        stdin.close()
        err_thread.join(timeout=8.0)
        os.close(in_master)
        os.close(err_master)

    assert not worker.is_alive(), "auth session did not exit"
    captured = b"".join(err_chunks).decode("utf-8", errors="replace")
    # Auth commands never enter the alternate screen and never run a turn.
    assert "\x1b[?1049h" not in captured
    assert seen == [], "/login or /logout ran a provider turn"


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    import fcntl
    import struct
    import termios

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("start", "end"),
    [((100, 40), (80, 24)), ((80, 24), (100, 40))],
)
def test_pty_resize_repaints_inline_with_overlay_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    start: tuple[int, int],
    end: tuple[int, int],
):
    """Resize while idle and while the slash overlay is open repaints inline.

    The size is read from the real output terminal's winsize (no COLUMNS/LINES
    pin here), so a TIOCSWINSZ change is observed by the resize poll. The inline
    contract holds across the resize: no alternate screen, the input/footer
    frame stays pinned, and a width-correct separator row is painted at both
    sizes.
    """

    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.delenv("LINES", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    start_cols, start_rows = start
    end_cols, end_rows = end

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    _set_winsize(err_slave, start_rows, start_cols)
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8")
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    provider = FakeNativeProvider(supports_tool_calls=True)
    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = NativeToolReplSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None: ui,
    )

    worker = threading.Thread(
        target=lambda: session.run(
            workspace_root=tmp_path,
            input_stream=cast(TextIO, stdin),
            output_stream=cast(TextIO, terminal),
            error_stream=cast(TextIO, terminal),
        ),
        daemon=True,
    )
    worker.start()
    sep_start = "─" * start_cols
    sep_end = "─" * end_cols
    overlay_snapshot = None
    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        assert _wait_for(err_chunks, sep_start), "initial-size separator missing"
        # Open the slash overlay, then resize while it is open.
        os.write(in_master, b"/")
        assert _wait_for(err_chunks, "copy"), "slash menu never opened"
        _set_winsize(err_slave, end_rows, end_cols)
        # The resize repaint clears the screen and redraws the full frame at the
        # new size; the clear + redraw are flushed together, so observing the
        # clear means the fresh frame is already in the stream. The clear also
        # resets the screen model's grid, so the parsed viewport reflects only
        # the post-resize frame (no reflowed pre-resize rows).
        assert _wait_for(err_chunks, "\x1b[2J"), "resize did not trigger a redraw"
        overlay_snapshot = parse_ansi_screen(
            b"".join(err_chunks).decode("utf-8", errors="replace"),
            columns=end_cols,
            rows=end_rows,
        )
        os.write(in_master, b"\x03")  # ctrl-c clears the prompt
        os.write(in_master, b"\x04")  # ctrl-d exits
        worker.join(timeout=8.0)
    finally:
        for byte in (b"\x03", b"\x04"):
            try:
                os.write(in_master, byte)
            except OSError:
                pass
        terminal.flush()
        terminal.close()
        stdin.close()
        err_thread.join(timeout=8.0)
        os.close(in_master)
        os.close(err_master)

    assert not worker.is_alive(), "resize session did not exit"
    captured = b"".join(err_chunks).decode("utf-8", errors="replace")
    # Inline model preserved across the resize: never the alternate screen.
    assert "\x1b[?1049h" not in captured
    # Both width-correct separators were painted (coherent repaint at each size).
    assert sep_start in captured
    assert sep_end in captured
    assert provider._call_counter[0] == 0  # resize/menu never run a turn

    # The post-resize viewport is a single coherent frame: exactly two
    # separators (above and below the input), the input row between them, the
    # still-open slash overlay below, and a footer — no stale rows left behind.
    assert overlay_snapshot is not None
    viewport = overlay_snapshot.viewport
    separator_rows = [
        index
        for index, line in enumerate(viewport)
        if line.strip() and set(line.strip()) == {"─"}
    ]
    assert len(separator_rows) == 2, f"stale separators: {separator_rows}"
    input_index = separator_rows[0] + 1
    assert separator_rows[1] == input_index + 1
    joined = "\n".join(viewport)
    assert "copy" in joined  # the overlay is still open at the new width
    assert any(line.strip() for line in viewport[separator_rows[1] + 1 :]), (
        "footer row missing below the resized frame"
    )


def _separator_rows(viewport: list[str]) -> list[int]:
    return [
        index
        for index, line in enumerate(viewport)
        if line.strip() and set(line.strip()) == {"─"}
    ]


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("start", "end"),
    [((100, 40), (80, 24)), ((80, 24), (100, 40))],
)
def test_pty_resize_after_multiline_paste_single_coherent_frame(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    start: tuple[int, int],
    end: tuple[int, int],
):
    """Resize with a multi-line paste in the editor leaves no stale rows.

    This is the regression case for a width shrink after a multi-line paste:
    the old (wider) frame would reflow and the relative-cursor erase would leave
    a stale ``line one⏎line two`` row behind the live frame. The resize repaint
    now clears and redraws the whole frame, so the post-resize viewport — and
    the viewport after a further keypress — is a single coherent frame with one
    input row, and Enter still submits the exact literal multi-line prompt.
    """

    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.delenv("LINES", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    start_cols, start_rows = start
    end_cols, end_rows = end
    prompts: list[str] = []
    provider = _PromptRecordingProvider(prompts)

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    _set_winsize(err_slave, start_rows, start_cols)
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8")
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = NativeToolReplSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None: ui,
    )

    worker = threading.Thread(
        target=lambda: session.run(
            workspace_root=tmp_path,
            input_stream=cast(TextIO, stdin),
            output_stream=cast(TextIO, terminal),
            error_stream=cast(TextIO, terminal),
        ),
        daemon=True,
    )
    worker.start()

    def assert_single_input_row(needle: str) -> None:
        snapshot = parse_ansi_screen(
            b"".join(err_chunks).decode("utf-8", errors="replace"),
            columns=end_cols,
            rows=end_rows,
        )
        viewport = snapshot.viewport
        rows_with_paste = [i for i, line in enumerate(viewport) if "line one" in line]
        assert len(rows_with_paste) == 1, f"stale/duplicate paste rows: {rows_with_paste}"
        input_index = rows_with_paste[0]
        # The whole paste sits on one physical row joined by the ⏎ glyph.
        assert needle in viewport[input_index]
        separators = _separator_rows(viewport)
        assert len(separators) == 2, f"stale separators: {separators}"
        assert separators[0] == input_index - 1
        assert separators[1] == input_index + 1

    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        os.write(in_master, b"\x1b[200~line one\nline two\x1b[201~")
        assert _wait_for(err_chunks, "line one⏎line two"), "paste never rendered"
        # Resize while the multi-line paste is sitting in the editor.
        _set_winsize(err_slave, end_rows, end_cols)
        assert _wait_for(err_chunks, "\x1b[2J"), "resize did not trigger a redraw"
        assert_single_input_row("line one⏎line two")
        # After another keypress the frame is still a single coherent row.
        os.write(in_master, b"!")
        assert _wait_for(err_chunks, "line one⏎line two!"), "keypress never rendered"
        assert_single_input_row("line one⏎line two!")
        # Enter submits the exact literal multi-line prompt (glyph is display-only).
        os.write(in_master, b"\n")
        assert _wait_for(err_chunks, "TURN_1_DONE"), "prompt never submitted"
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        terminal.flush()
        terminal.close()
        stdin.close()
        err_thread.join(timeout=8.0)
        os.close(in_master)
        os.close(err_master)

    assert not worker.is_alive(), "resize/paste session did not exit"
    captured = b"".join(err_chunks).decode("utf-8", errors="replace")
    assert "\x1b[?1049h" not in captured
    assert prompts == ["line one\nline two!"]


def _start_pty_repl_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    provider: ProviderPort,
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None,
    store: PromptHistoryStore,
    winsize: tuple[int, int] | None = None,
) -> SimpleNamespace:
    """Spawn one real-PTY product-TUI session in a worker thread.

    Returns a namespace with the master fds, the live-output chunk buffer, the
    worker thread, and a result holder. ``winsize`` (cols, rows) sets the slave
    terminal size via TIOCSWINSZ when given (used by the resize test); callers
    that pin COLUMNS/LINES can omit it.
    """

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    if winsize is not None:
        _set_winsize(err_slave, winsize[1], winsize[0])
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8")
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    err_thread, err_chunks = _spawn_live_drainer(err_master)
    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = NativeToolReplSession(
        provider=provider,
        provider_state=provider_state,
        tool_registry={},
        prompt_history_store=store,
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None: ui,
    )
    result_holder: list[object] = []

    def _run() -> None:
        result_holder.append(
            session.run(
                workspace_root=tmp_path,
                input_stream=cast(TextIO, stdin),
                output_stream=cast(TextIO, terminal),
                error_stream=cast(TextIO, terminal),
            )
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    return SimpleNamespace(
        in_master=in_master,
        err_master=err_master,
        err_slave=err_slave,
        stdin=stdin,
        terminal=terminal,
        err_thread=err_thread,
        err_chunks=err_chunks,
        worker=worker,
        result_holder=result_holder,
        ui=ui,
    )


def _finish_pty_repl_session(ctx: SimpleNamespace) -> str:
    ctx.worker.join(timeout=8.0)
    for byte in (b"\x03", b"\x04"):
        try:
            os.write(ctx.in_master, byte)
        except OSError:
            pass
    try:
        ctx.terminal.flush()
        ctx.terminal.close()
    except OSError:
        pass
    try:
        ctx.stdin.close()
    except OSError:
        pass
    ctx.err_thread.join(timeout=8.0)
    os.close(ctx.in_master)
    os.close(ctx.err_master)
    return b"".join(ctx.err_chunks).decode("utf-8", errors="replace")


def _native_state_logged_out(
    tmp_path: Path, provider: ProviderPort
) -> NativeReplProviderState:
    from pipy_harness.native import NativeModelSelection

    return NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        provider_factory=lambda selection: provider,
        env={},
        openai_codex_auth_path=tmp_path / "missing-openai-codex.json",
        persist_defaults=False,
    )


def _highlighted_row(viewport: list[str]) -> str | None:
    for line in viewport:
        stripped = line.strip()
        if stripped.startswith("→"):
            return stripped
    return None


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("start", "end"),
    [((100, 40), (80, 24)), ((80, 24), (100, 40))],
)
def test_pty_settings_dialog_live_navigate_toggle_clear_and_resize(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    start: tuple[int, int],
    end: tuple[int, int],
):
    """Drive the interactive `/settings` dialog over a real PTY end-to-end.

    Opens the dialog, inspects the live overlay before any action, navigates
    between actionable rows, toggles persistent prompt history, clears it, and
    resizes the terminal while the dialog is still open — asserting at the
    fragile live intermediate screen (not only after final submission) that the
    inline contract holds: no alternate screen, the highlighted row is correct,
    the overlay re-renders coherently with a footer and no stale rows after the
    resize, and Esc returns to a separator-framed input. No provider turn or
    tool call runs from any `/settings` action.
    """

    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.delenv("LINES", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    start_cols, start_rows = start
    end_cols, end_rows = end

    store = PromptHistoryStore(tmp_path / "history.json")
    store.set_enabled(True)
    store.record("seeded-entry")

    provider = FakeNativeProvider(supports_tool_calls=True)
    provider_state = _native_state_logged_out(tmp_path, provider)
    ctx = _start_pty_repl_session(
        monkeypatch,
        tmp_path,
        provider=provider,
        provider_state=provider_state,
        store=store,
        winsize=start,
    )

    sep_end = "─" * end_cols
    before_snapshot = None
    after_snapshot = None
    try:
        assert _wait_for(ctx.err_chunks, "escape interrupt"), "startup chrome missing"
        assert _wait_for(ctx.err_chunks, "─" * start_cols), "initial separator missing"
        os.write(ctx.in_master, b"/settings\n")
        assert _wait_for(ctx.err_chunks, "Settings —"), "settings dialog never opened"
        # Inspect the live overlay BEFORE any action.
        before_snapshot = parse_ansi_screen(
            b"".join(ctx.err_chunks).decode("utf-8", errors="replace"),
            columns=start_cols,
            rows=start_rows,
        )
        before_view = before_snapshot.viewport
        before_joined = "\n".join(before_view)
        assert "Provider / model" in before_joined
        assert "Authentication" in before_joined
        assert "Prompt history" in before_joined
        assert "persistent prompt history: on" in before_joined
        assert "clear persisted history (1 saved)" in before_joined
        # The first actionable row (provider/model) is highlighted on open.
        assert _highlighted_row(before_view) == "→ change provider/model…"
        # Windowing: at the short 80x24 size the row list overflows and shows a
        # scroll indicator; at 100x40 it fits without one.
        if start == (80, 24):
            import re as _re

            assert _re.search(r"\(\d+/\d+\)", before_joined), "scroll indicator missing"

        # Navigate down across actionable rows (skipping headers/status) to the
        # persistent-history toggle, then toggle it OFF with Space.
        os.write(ctx.in_master, b"\x1b[B")  # → login
        os.write(ctx.in_master, b"\x1b[B")  # → toggle
        assert _wait_for(ctx.err_chunks, "→ persistent prompt history"), "nav failed"
        os.write(ctx.in_master, b" ")  # space activates the toggle
        assert _wait_for(
            ctx.err_chunks, "persistent prompt history: off"
        ), "toggle did not flip live"

        # Navigate to the clear row and clear with Enter.
        os.write(ctx.in_master, b"\x1b[B")  # → clear
        assert _wait_for(ctx.err_chunks, "→ clear persisted history"), "nav to clear failed"
        os.write(ctx.in_master, b"\r")  # enter clears
        assert _wait_for(
            ctx.err_chunks, "clear persisted history (0 saved)"
        ), "clear did not update live"

        # Resize while the dialog is still open.
        _set_winsize(ctx.err_slave, end_rows, end_cols)
        assert _wait_for(ctx.err_chunks, "\x1b[2J"), "resize did not trigger a redraw"
        after_snapshot = parse_ansi_screen(
            b"".join(ctx.err_chunks).decode("utf-8", errors="replace"),
            columns=end_cols,
            rows=end_rows,
        )

        # Close the dialog with Esc; the separator-framed input returns at the
        # new size.
        os.write(ctx.in_master, b"\x1b")
        assert _wait_for(ctx.err_chunks, sep_end), "input frame did not return after esc"
        # Let the resumed read_line re-enter raw mode before the exit keystroke
        # so the byte is read as ctrl-d rather than racing the mode transition.
        time.sleep(0.3)
        os.write(ctx.in_master, b"\x04")  # ctrl-d exits on the empty prompt
    finally:
        captured = _finish_pty_repl_session(ctx)

    assert not ctx.worker.is_alive(), "settings dialog session did not exit"
    # Inline model preserved throughout: never the alternate screen.
    assert "\x1b[?1049h" not in captured

    # The post-resize overlay is a single coherent frame: the title and the
    # cleared row each appear exactly once (no stale pre-resize rows), the
    # correct row stays highlighted, and a footer is present below the list.
    assert after_snapshot is not None
    after_view = after_snapshot.viewport
    after_joined = "\n".join(after_view)
    assert after_joined.count("Settings —") == 1, "stale dialog title after resize"
    assert after_joined.count("clear persisted history (0 saved)") == 1, "stale rows"
    assert _highlighted_row(after_view) == "→ clear persisted history (0 saved)"
    title_rows = [i for i, line in enumerate(after_view) if "Settings —" in line]
    assert title_rows, "dialog title missing after resize"
    assert any(line.strip() for line in after_view[title_rows[0] + 1 :]), (
        "footer/rows missing below the resized dialog"
    )

    # No provider turn and no tool call ran from any /settings action.
    assert provider._call_counter[0] == 0
    result = ctx.result_holder[0]
    assert getattr(result, "status") == HarnessStatus.SUCCEEDED
    assert getattr(result, "user_turn_count") == 0
    assert getattr(result, "tool_invocation_count") == 0

    # The local toggle/clear mutated only the local store.
    assert store.enabled is False
    assert store.entries() == []


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_settings_persistent_history_cross_session_recall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Prove persistent prompt history end to end across fresh product sessions.

    Session 1 enables persistence from `/settings` and submits a prompt.
    Session 2 (a fresh TUI process) recalls that prompt with Up, then disables
    and clears persistence from `/settings`. Session 3 (fresh again) proves the
    prompt is no longer recalled. The store file is the only cross-session state
    and is never the metadata-first session archive.
    """

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")

    history_path = tmp_path / "state" / "history.json"
    token = "recall-me-token"

    # --- Session 1: enable persistence via /settings, then submit a prompt. ---
    seen1: list[tuple[str, str]] = []
    provider1 = _RecordingProvider("fake", "fake-native-bootstrap", seen1)
    store1 = PromptHistoryStore(history_path)
    assert store1.enabled is False  # off by default
    ctx1 = _start_pty_repl_session(
        monkeypatch, tmp_path, provider=provider1, provider_state=None, store=store1
    )
    try:
        assert _wait_for(ctx1.err_chunks, "escape interrupt")
        os.write(ctx1.in_master, b"/settings\n")
        assert _wait_for(ctx1.err_chunks, "Settings —"), "session1 dialog never opened"
        # With the static single-provider state the toggle is the first
        # actionable row, so it is highlighted on open.
        assert _wait_for(ctx1.err_chunks, "→ persistent prompt history: off")
        os.write(ctx1.in_master, b" ")  # enable
        assert _wait_for(ctx1.err_chunks, "persistent prompt history: on")
        os.write(ctx1.in_master, b"\x1b")  # esc closes the dialog
        # Let the resumed read_line re-enter raw mode before submitting, so the
        # prompt is not consumed by the still-closing dialog.
        time.sleep(0.3)
        # Submit a real prompt; it should be persisted.
        os.write(ctx1.in_master, token.encode("utf-8") + b"\n")
        assert _wait_for(ctx1.err_chunks, "RESPONSE_MARKER_DONE"), "turn never ran"
        time.sleep(0.1)
        os.write(ctx1.in_master, b"\x04")  # exit on empty prompt
    finally:
        captured1 = _finish_pty_repl_session(ctx1)
    assert not ctx1.worker.is_alive()
    assert "\x1b[?1049h" not in captured1

    reloaded = PromptHistoryStore(history_path)
    assert reloaded.enabled is True
    assert reloaded.entries() == [token]

    # --- Session 2: fresh session recalls with Up, then disables + clears. ---
    seen2: list[tuple[str, str]] = []
    provider2 = _RecordingProvider("fake", "fake-native-bootstrap", seen2)
    store2 = PromptHistoryStore(history_path)
    ctx2 = _start_pty_repl_session(
        monkeypatch, tmp_path, provider=provider2, provider_state=None, store=store2
    )
    try:
        assert _wait_for(ctx2.err_chunks, "escape interrupt")
        # The fresh session seeded recall from disk; Up surfaces the prompt.
        os.write(ctx2.in_master, b"\x1b[A")
        assert _wait_for(ctx2.err_chunks, token), "prompt was not recalled in a fresh session"
        # Clear the recalled text, then disable + clear persistence via /settings.
        os.write(ctx2.in_master, b"\x15")  # ctrl-u kills to line start
        os.write(ctx2.in_master, b"/settings\n")
        assert _wait_for(ctx2.err_chunks, "Settings —"), "session2 dialog never opened"
        assert _wait_for(ctx2.err_chunks, "→ persistent prompt history: on")
        os.write(ctx2.in_master, b" ")  # disable
        assert _wait_for(ctx2.err_chunks, "persistent prompt history: off")
        os.write(ctx2.in_master, b"\x1b[B")  # → clear row
        assert _wait_for(ctx2.err_chunks, "→ clear persisted history")
        os.write(ctx2.in_master, b"\r")  # clear
        assert _wait_for(ctx2.err_chunks, "clear persisted history (0 saved)")
        os.write(ctx2.in_master, b"\x1b")  # esc closes the dialog
        time.sleep(0.3)
        os.write(ctx2.in_master, b"\x04")  # exit on empty prompt
    finally:
        captured2 = _finish_pty_repl_session(ctx2)
    assert not ctx2.worker.is_alive()
    assert "\x1b[?1049h" not in captured2
    # /settings actions ran no provider turn.
    assert seen2 == []

    reloaded2 = PromptHistoryStore(history_path)
    assert reloaded2.enabled is False
    assert reloaded2.entries() == []

    # --- Session 3: fresh session must NOT recall the cleared prompt. ---
    seen3: list[tuple[str, str]] = []
    provider3 = _RecordingProvider("fake", "fake-native-bootstrap", seen3)
    store3 = PromptHistoryStore(history_path)
    ctx3 = _start_pty_repl_session(
        monkeypatch, tmp_path, provider=provider3, provider_state=None, store=store3
    )
    try:
        assert _wait_for(ctx3.err_chunks, "escape interrupt")
        os.write(ctx3.in_master, b"\x1b[A")  # Up — nothing to recall
        # Give the (empty) recall a moment; there is nothing to surface.
        time.sleep(0.3)
        os.write(ctx3.in_master, b"\x04")  # exit on empty prompt
    finally:
        captured3 = _finish_pty_repl_session(ctx3)
    assert not ctx3.worker.is_alive()
    assert "\x1b[?1049h" not in captured3
    # The cleared prompt is not recalled in the fresh, disabled session.
    assert token not in captured3
    assert seen3 == []

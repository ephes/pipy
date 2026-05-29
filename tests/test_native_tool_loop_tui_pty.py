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
from typing import TextIO, cast

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import FakeNativeProvider, NativeToolReplSession
from pipy_harness.native.clipboard import ClipboardResult
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
        lambda self, input_stream, error_stream, workspace: ui,
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
        lambda self, input_stream, error_stream, workspace: ui,
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
        lambda self, input_stream, error_stream, workspace: ui,
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
    # Honest menu: the executable local commands are offered (including the
    # now-interactive /model selector)...
    for executable in ("help", "model", "settings", "copy", "exit", "quit"):
        assert executable in menu_text, f"menu missing /{executable}"
    # ...and the not-yet-executable provider/auth commands are absent.
    for absent in ("/login", "/logout"):
        assert absent not in menu_text

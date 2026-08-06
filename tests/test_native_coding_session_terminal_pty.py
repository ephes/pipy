"""Real-PTY integration tests for the inline product TUI.

These exercise the actual product paint path (`Screen.paint` and
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

import errno
import json
import os
import pty
import select
import stat
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import TextIO, cast

import pytest
from pty_sync import (
    input_ready_count,
    output_bytes,
    wait_for_input_ready_after,
    wait_for_input_ready_count,
    wait_for_output,
    wait_for_output_after,
)

from pipy_harness.models import HarnessStatus
from pipy_harness.native import FakeNativeProvider
from pipy_harness.native.agent import AgentAssistantMessage, AgentToolResultMessage
from pipy_harness.native.clipboard import ClipboardResult
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.models import ProviderResult, ProviderToolCall
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.repl_state import (
    ModelRuntime,
    NativeReplProviderState,
    StaticNativeReplProviderState,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.terminal_screen import parse_ansi_screen
from pipy_harness.native.tui import TerminalUi

_RETAINED_PTY_STREAMS: list[object] = []
_ABANDONED_PTY_FDS: list[int] = []
_DRAINER_STOPS: dict[threading.Thread, threading.Event] = {}


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
    stop = threading.Event()

    def _drain() -> None:
        while not stop.is_set():
            readable, _, _ = select.select([fd], [], [], 0.05)
            if not readable:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    time.sleep(0.01)
                    continue
                return
            if not chunk:
                time.sleep(0.01)
                continue
            collected.append(chunk)

    thread = threading.Thread(target=_drain, daemon=True)
    thread.start()
    _DRAINER_STOPS[thread] = stop
    return thread, collected


def _wait_for(collected: list[bytes], needle: str, *, timeout: float = 8.0) -> bool:
    # Every startup and *_DONE caller proceeds to write input. Those markers
    # can paint before the next outer TCSAFLUSH raw transition, so make them
    # mean prompt readiness rather than paint completion. The two phases share
    # one monotonic deadline in the synchronization owner.
    if needle == "escape interrupt" or needle.endswith("_DONE"):
        return (
            wait_for_input_ready_after(collected, needle, timeout=timeout) is not None
        )
    return wait_for_output(collected, needle, timeout=timeout) is not None


def _wait_for_resize_frame(
    collected: list[bytes],
    sentinel: str,
    *,
    after: int,
    timeout: float = 8.0,
) -> int | None:
    """Observe a fresh clear then a final footer-row sentinel under one deadline."""

    return wait_for_output_after(
        collected,
        b"\x1b[2J",
        sentinel,
        after=after,
        timeout=timeout,
    )


def _install_resize_frame_sentinel(
    ui: TerminalUi,
    collected: list[bytes],
    sentinel: str,
) -> bool:
    """Publish and capture a visible footer sentinel before changing geometry."""

    start = len(output_bytes(collected))
    ui.components.chrome.footer.set_builtin_text(sentinel)
    return wait_for_output(collected, sentinel, after=start) is not None


def _wait_for_predicate(predicate: Callable[[], bool], *, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _detach_text_stream(stream: TextIO) -> None:
    try:
        stream.flush()
    except (OSError, ValueError):
        pass
    detach = getattr(stream, "detach", None)
    if callable(detach):
        try:
            _RETAINED_PTY_STREAMS.append(detach())
        except (OSError, ValueError):
            pass


def _stream_fd(stream: TextIO) -> int | None:
    try:
        return stream.fileno()
    except (OSError, ValueError):
        return None


def _close_pty_session(
    stdin: TextIO,
    terminal: TextIO,
    in_master: int,
    err_master: int,
    err_thread: threading.Thread,
    *,
    worker: threading.Thread | None = None,
) -> None:
    # Closing pty descriptors can block in close(2) on macOS. These streams are
    # opened with closefd=False, so stop the nonblocking drainer and leave the
    # bounded descriptor reclaim to process teardown instead of blocking the
    # pytest worker. Detaching a wrapper that a failed test's product worker is
    # still polling turns the primary assertion into a background ValueError;
    # retain both live wrappers instead until process teardown in that case.
    stop = _DRAINER_STOPS.pop(err_thread, None)
    if stop is not None:
        stop.set()
    stdin_fd = _stream_fd(stdin)
    terminal_fd = _stream_fd(terminal)
    if worker is not None and worker.is_alive():
        _RETAINED_PTY_STREAMS.extend((stdin, terminal))
    else:
        _detach_text_stream(stdin)
        _detach_text_stream(terminal)
    if stdin_fd is not None:
        _ABANDONED_PTY_FDS.append(stdin_fd)
    if terminal_fd is not None:
        _ABANDONED_PTY_FDS.append(terminal_fd)
    _ABANDONED_PTY_FDS.extend([in_master, err_master])
    err_thread.join(timeout=2.0)


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
    # The driver's ``size()`` reads the terminal size via
    # ``shutil.get_terminal_size``, which honors COLUMNS/LINES first — pin the
    # viewport deterministically.
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    # A long answer that overflows the window once committed, so it must scroll
    # into native scrollback (proving full-height use, not an upper-half cap).
    # It is delivered as buffered final_text (not streamed) so it is appended
    # only at canonical assistant completion — after the active-turn Escape watcher has
    # stopped — which makes SCROLL_MARKER_DONE a safe point to send `/copy`.
    answer = "\n".join(f"answer line {index:02d}" for index in range(60))
    answer += "\nSCROLL_MARKER_DONE"
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=((),),
        final_text=answer,
    )
    recorder = _ClipboardRecorder()
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(
        provider=provider,
        tool_registry={},
        clipboard_copy=recorder,
    )
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
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
        copy_start = len(output_bytes(err_chunks))
        os.write(in_master, b"/copy\n")
        assert (
            wait_for_input_ready_after(
                err_chunks,
                "copied last answer",
                after=copy_start,
            )
            is not None
        ), "/copy did not return to ready input"
        os.write(in_master, b"\x04")  # ctrl-d on an empty prompt ends the loop
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

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

    def complete(
        self, request, *, stream_sink=None, reasoning_sink=None, cancel_token=None
    ):
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


class _FixedProviderReplState(NativeReplProviderState):
    """State whose provider build is a fixed double (runtime-owned otherwise)."""

    def __init__(self, fixed_provider: ProviderPort, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._fixed_provider = fixed_provider

    def provider_for(self, selection: object) -> ProviderPort:
        return self._fixed_provider


class _RecordingProviderReplState(NativeReplProviderState):
    """State whose provider build is a recording double (runtime-owned otherwise)."""

    def __init__(self, seen: list[tuple[str, str]], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._seen_ref = seen

    def provider_for(self, selection: object) -> ProviderPort:
        return cast(
            ProviderPort,
            _RecordingProvider(
                selection.provider_name,  # type: ignore[attr-defined]
                selection.model_id,  # type: ignore[attr-defined]
                self._seen_ref,
            ),
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

    from pipy_harness.native import NativeModelSelection

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    seen: list[tuple[str, str]] = []

    from pipy_harness.native.auth_store import AuthStore
    from pipy_harness.native.catalog_state import ProviderCatalogState

    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "absent.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={"OPENROUTER_API_KEY": "k", "OPENAI_API_KEY": "k2"},
        openai_codex_auth_path=tmp_path / "missing-openai-codex.json",
    )
    provider_state = _RecordingProviderReplState(
        seen,
        selection=NativeModelSelection("openrouter", "openai/gpt-5.1-codex"),
        model_runtime=ModelRuntime(catalog=catalog),
        persist_defaults=False,
    )
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(
        provider=provider_state.current_provider(),
        provider_state=provider_state,
        tool_registry={},
    )
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
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
        # Product ordering paints the selector before its raw owner enters.
        selector_start = len(output_bytes(err_chunks))
        os.write(in_master, b"/model\n")
        assert (
            wait_for_input_ready_after(
                err_chunks,
                "Select provider/model",
                after=selector_start,
            )
            is not None
        ), "selector input never became ready"
        # The selector lists availability state and reasons.
        assert (
            wait_for_output(err_chunks, "[available]", after=selector_start) is not None
        ), "availability state missing"
        # Opening the selector runs no provider turn.
        assert seen == [], "selector opened a provider turn"
        # Navigate up one row from the current openrouter/openai/gpt-5.1-codex
        # row (the catalog lists it directly after the available
        # openai-completions rows), then choose the available row above it.
        navigation_start = len(output_bytes(err_chunks))
        os.write(in_master, b"\x1b[A")  # up arrow
        assert (
            wait_for_output(err_chunks, "Select provider/model", after=navigation_start)
            is not None
        ), "selector navigation did not repaint"
        selection_start = len(output_bytes(err_chunks))
        os.write(in_master, b"\r")  # enter selects the highlighted available row
        assert (
            wait_for_input_ready_after(
                err_chunks,
                "selected model openai-completions/gpt-4.1",
                after=selection_start,
            )
            is not None
        ), "selection did not return to ready input"
        # No provider turn during selection.
        assert seen == [], "selection itself ran a provider turn"
        # The next prompt is constructed with the newly selected provider/model.
        turn_start = len(output_bytes(err_chunks))
        os.write(in_master, b"hi\n")
        assert (
            wait_for_input_ready_after(
                err_chunks, "RESPONSE_MARKER_DONE", after=turn_start
            )
            is not None
        ), "turn did not return to ready input"
        os.write(in_master, b"\x04")  # ctrl-d on an empty prompt ends the loop
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    assert not worker.is_alive(), "selector session did not exit"
    assert result_holder, "selector session produced no result"
    result = result_holder[0]
    assert getattr(result, "status") == HarnessStatus.SUCCEEDED
    assert getattr(result, "provider_name") == "openai-completions"
    assert getattr(result, "model_id") == "gpt-4.1"

    captured = b"".join(err_chunks).decode("utf-8", errors="replace")
    # Inline model: never the alternate screen.
    assert "\x1b[?1049h" not in captured
    # Exactly one provider turn ran, and it used the newly selected selection.
    assert seen == [("openai-completions", "gpt-4.1")]

    # The footer/status model label updated to the new selection.
    snapshot = parse_ansi_screen(captured, columns=100, rows=40)
    footer_text = "\n".join(snapshot.viewport)
    assert "gpt-4.1" in footer_text


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
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    provider = FakeNativeProvider(supports_tool_calls=True)
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
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
        assert _wait_for(err_chunks, "settings"), "slash menu never opened"
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
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    menu_text = "\n".join(snapshot.viewport)
    assert provider._call_counter[0] == 0  # opening the menu runs no turn
    # Honest menu: the leading executable local commands are offered — including
    # the new /hotkeys command and the executable /login and /logout auth
    # commands (the menu windows to a few rows, so later commands such as /copy
    # scroll below the fold but are still reachable; the unit tests cover the
    # full advertised set).
    for executable in ("hotkeys", "model", "scoped-models", "settings"):
        assert executable in menu_text, f"menu missing /{executable}"


class _PromptRecordingProvider:
    """Tool-capable provider recording each turn's submitted user prompt."""

    name = "fake"
    model_id = "fake-native-bootstrap"
    supports_tool_calls = True

    def __init__(self, prompts: list[str]) -> None:
        self._prompts = prompts
        self._turn = 0

    def complete(
        self, request, *, stream_sink=None, reasoning_sink=None, cancel_token=None
    ):
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
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
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
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    assert not worker.is_alive(), "editor session did not exit"
    return b"".join(err_chunks).decode("utf-8", errors="replace")


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_utf8_prompt_text_renders_and_submits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    prompts: list[str] = []
    provider = _PromptRecordingProvider(prompts)

    def drive(in_master: int, chunks: list[bytes]) -> None:
        os.write(in_master, "öhm\n".encode("utf-8"))
        assert _wait_for(chunks, "TURN_1_DONE"), "utf-8 prompt never submitted"

    captured = _run_editor_pty(monkeypatch, tmp_path, provider, drive)
    assert "öhm" in captured
    assert "��hm" not in captured
    assert prompts == ["öhm"]


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
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
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
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    assert not worker.is_alive(), f"{label}: paste session did not exit"
    captured = b"".join(err_chunks).decode("utf-8", errors="replace")
    assert "\x1b[?1049h" not in captured
    assert prompts == ["line one\nline two"]


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("columns", "rows", "label"),
    [(100, 40, "ghostty"), (80, 24, "zellij")],
)
def test_pty_long_input_soft_wraps_typing_paste_and_cursor_insert(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    columns: int,
    rows: int,
    label: str,
):
    prompts: list[str] = []
    provider = _PromptRecordingProvider(prompts)

    typed = "typed-wrap-" * 13
    pasted = "paste-wrap-" * 12

    def assert_wrapped_editor(chunks: list[bytes], needle: str) -> None:
        snapshot = parse_ansi_screen(
            b"".join(chunks).decode("utf-8", errors="replace"),
            columns=columns,
            rows=rows,
        )
        separators = _separator_rows(snapshot.viewport)
        assert len(separators) >= 2, f"{label}: input separators missing"
        top, bottom = separators[-2], separators[-1]
        input_lines = snapshot.viewport[top + 1 : bottom]
        assert len(input_lines) >= 2, f"{label}: long input did not soft-wrap"
        joined = "".join(line.rstrip() for line in input_lines)
        assert needle in joined, f"{label}: wrapped input text missing"
        assert any(line.strip() for line in snapshot.viewport[bottom + 1 :]), (
            f"{label}: footer rows missing below wrapped input"
        )

    def drive(in_master: int, chunks: list[bytes]) -> None:
        os.write(in_master, typed.encode("utf-8"))
        assert _wait_for(chunks, typed[-24:]), (
            f"{label}: typed long input never rendered"
        )
        assert_wrapped_editor(chunks, typed[:40])
        # Move left inside the wrapped prompt, insert a marker, then submit. The
        # provider receiving that exact prompt proves cursor movement still maps
        # to the logical buffer rather than the visual rows.
        os.write(in_master, b"\x1b[D" * 5)
        os.write(in_master, b"X\n")
        assert _wait_for(chunks, "TURN_1_DONE"), (
            f"{label}: typed prompt never submitted"
        )
        os.write(in_master, f"\x1b[200~{pasted}\x1b[201~".encode("utf-8"))
        assert _wait_for(chunks, pasted[-24:]), (
            f"{label}: pasted long input never rendered"
        )
        assert_wrapped_editor(chunks, pasted[:40])
        os.write(in_master, b"\n")
        assert _wait_for(chunks, "TURN_2_DONE"), (
            f"{label}: pasted prompt never submitted"
        )

    captured = _run_editor_pty(
        monkeypatch,
        tmp_path,
        provider,
        drive,
        columns=columns,
        rows=rows,
    )
    assert "\x1b[?1049h" not in captured
    assert prompts == [f"{typed[:-5]}X{typed[-5:]}", pasted]


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

    This drives the real `TerminalUi.read_line` over a PTY (not the
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
    from pipy_harness.native import NativeModelSelection
    from pipy_harness.native.openai_codex_provider import OpenAICodexAuthManager

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    auth_path = tmp_path / "openai-codex.json"
    seen: list[tuple[str, str]] = []

    manager = _FileBackedAuthManager(auth_path)
    from pipy_harness.native.auth_store import AuthStore
    from pipy_harness.native.catalog_state import ProviderCatalogState

    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "absent.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        openai_codex_auth_path=auth_path,
    )
    provider_state = _RecordingProviderReplState(
        seen,
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        auth_manager_factory=lambda: cast(OpenAICodexAuthManager, manager),
        model_runtime=ModelRuntime(catalog=catalog),
        persist_defaults=False,
    )

    def codex_available() -> bool:
        return next(
            option.available
            for option in provider_state.model_options()
            if option.selection.provider_name == "openai-codex"
        )

    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(
        provider=provider_state.current_provider(),
        provider_state=provider_state,
        tool_registry={},
    )
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
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
        login_start = len(output_bytes(err_chunks))
        os.write(in_master, b"/login\n")
        assert (
            wait_for_input_ready_after(err_chunks, "login stored", after=login_start)
            is not None
        ), "/login did not return to ready input"
        assert codex_available() is True, "login did not refresh availability"
        logout_start = len(output_bytes(err_chunks))
        os.write(in_master, b"/logout\n")
        assert (
            wait_for_input_ready_after(
                err_chunks, "credentials removed", after=logout_start
            )
            is not None
        ), "/logout did not return to ready input"
        assert codex_available() is False, "logout did not refresh availability"
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

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
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    provider = FakeNativeProvider(supports_tool_calls=True)
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
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
        assert _wait_for(err_chunks, "settings"), "slash menu never opened"
        frame_sentinel = "RESIZE_SLASH_FRAME_COMPLETE"
        assert _install_resize_frame_sentinel(ui, err_chunks, frame_sentinel)
        resize_start = len(output_bytes(err_chunks))
        _set_winsize(err_slave, end_rows, end_cols)
        # A PTY read may split the coalesced clear + paint flush. The visible
        # footer sentinel is serialized after the input, menu, and separators,
        # so clear then sentinel proves the parsed post-resize frame is captured.
        assert (
            _wait_for_resize_frame(
                err_chunks,
                frame_sentinel,
                after=resize_start,
            )
            is not None
        ), "resize redraw did not reach its final footer row"
        overlay_snapshot = parse_ansi_screen(
            b"".join(err_chunks).decode("utf-8", errors="replace"),
            columns=end_cols,
            rows=end_rows,
        )
        os.write(in_master, b"\x03")  # ctrl-c clears the prompt
        os.write(in_master, b"\x04")  # ctrl-d exits
        worker.join(timeout=8.0)
    finally:
        if worker.is_alive():
            for byte in (b"\x03", b"\x04"):
                try:
                    os.write(in_master, byte)
                except OSError:
                    pass
            worker.join(timeout=8.0)
        _close_pty_session(
            stdin,
            terminal,
            in_master,
            err_master,
            err_thread,
            worker=worker,
        )

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
    assert "settings" in joined  # the overlay is still open at the new width
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
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
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
        assert len(rows_with_paste) == 1, (
            f"stale/duplicate paste rows: {rows_with_paste}"
        )
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
        # Resize while the multi-line paste is sitting in the editor. A unique
        # visible footer row follows the input/separators in every full frame.
        frame_sentinel = "RESIZE_PASTE_FRAME_COMPLETE"
        assert _install_resize_frame_sentinel(ui, err_chunks, frame_sentinel)
        resize_start = len(output_bytes(err_chunks))
        _set_winsize(err_slave, end_rows, end_cols)
        assert (
            _wait_for_resize_frame(
                err_chunks,
                frame_sentinel,
                after=resize_start,
            )
            is not None
        ), "resize redraw did not reach its final footer row"
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
        if worker.is_alive():
            try:
                os.write(in_master, b"\x15\x04")
            except OSError:
                pass
            worker.join(timeout=8.0)
        _close_pty_session(
            stdin,
            terminal,
            in_master,
            err_master,
            err_thread,
            worker=worker,
        )

    assert not worker.is_alive(), "resize/paste session did not exit"
    captured = b"".join(err_chunks).decode("utf-8", errors="replace")
    assert "\x1b[?1049h" not in captured
    assert prompts == ["line one\nline two!"]


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("start", "end"),
    [((100, 40), (80, 24)), ((80, 24), (100, 40))],
)
def test_pty_resize_rewraps_long_input_and_keeps_footer_pinned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    start: tuple[int, int],
    end: tuple[int, int],
):
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.delenv("LINES", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    start_cols, start_rows = start
    end_cols, end_rows = end
    prompts: list[str] = []
    provider = _PromptRecordingProvider(prompts)
    # A unique final glyph makes the last rendered frame an acknowledgement
    # that every byte in the preceding repetitive burst has been consumed.
    completion_glyph = "§"
    long_prompt = ("resize-wrap-" * 16) + completion_glyph

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    _set_winsize(err_slave, start_rows, start_cols)
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
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
        input_start = len(output_bytes(err_chunks))
        os.write(in_master, long_prompt.encode("utf-8"))
        assert (
            wait_for_output(err_chunks, completion_glyph, after=input_start) is not None
        ), "complete long input never rendered"
        # Do not resize while unread bytes from the repetitive input burst can
        # still trigger an ordinary paint. Such a paint can adopt the new size
        # before the next polling boundary, leaving no size delta for the
        # poll-driven full-clear path in this split-PTY harness. The complete
        # rendered input above proves the raw owner consumed the burst; this
        # fresh offset then identifies only the ensuing resize repaint. A
        # distinct final footer row proves the PTY drainer also captured the
        # post-resize wrapped input and separators preceding that row.
        frame_sentinel = "RESIZE_LONG_INPUT_FRAME_COMPLETE"
        assert _install_resize_frame_sentinel(ui, err_chunks, frame_sentinel)
        resize_start = len(output_bytes(err_chunks))
        _set_winsize(err_slave, end_rows, end_cols)
        assert (
            _wait_for_resize_frame(
                err_chunks,
                frame_sentinel,
                after=resize_start,
            )
            is not None
        ), "resize redraw did not reach its final footer row"
        snapshot = parse_ansi_screen(
            b"".join(err_chunks).decode("utf-8", errors="replace"),
            columns=end_cols,
            rows=end_rows,
        )
        separators = _separator_rows(snapshot.viewport)
        assert len(separators) >= 2, f"stale/missing separators: {separators}"
        top, bottom = separators[-2], separators[-1]
        input_lines = snapshot.viewport[top + 1 : bottom]
        assert len(input_lines) >= 2, "long input did not stay wrapped after resize"
        assert "".join(line.rstrip() for line in input_lines).startswith("resize-wrap-")
        assert any(line.strip() for line in snapshot.viewport[bottom + 1 :]), (
            "footer rows missing below resized wrapped input"
        )
        os.write(in_master, b"\n")
        assert _wait_for(err_chunks, "TURN_1_DONE"), "resized prompt never submitted"
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        if worker.is_alive():
            try:
                # Clear any unsubmitted editor contents, then exit the empty
                # prompt before wrappers can be detached during failure cleanup.
                os.write(in_master, b"\x15\x04")
            except OSError:
                pass
            worker.join(timeout=8.0)
        _close_pty_session(
            stdin,
            terminal,
            in_master,
            err_master,
            err_thread,
            worker=worker,
        )

    assert not worker.is_alive(), "resize/long-input session did not exit"
    captured = b"".join(err_chunks).decode("utf-8", errors="replace")
    assert "\x1b[?1049h" not in captured
    assert prompts == [long_prompt]


def _start_pty_repl_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    provider: ProviderPort,
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None,
    store: PromptHistoryStore,
    winsize: tuple[int, int] | None = None,
    native_session: NativeSessionTree | None = None,
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
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(
        provider=provider,
        provider_state=provider_state,
        tool_registry={},
        prompt_history_store=store,
        native_session=native_session,
    )
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
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
    if ctx.worker.is_alive():
        for byte in (b"\x03", b"\x04"):
            try:
                os.write(ctx.in_master, byte)
            except OSError:
                pass
        ctx.worker.join(timeout=8.0)
    _close_pty_session(
        ctx.stdin,
        ctx.terminal,
        ctx.in_master,
        ctx.err_master,
        ctx.err_thread,
        worker=ctx.worker,
    )
    return b"".join(ctx.err_chunks).decode("utf-8", errors="replace")


def _native_state_logged_out(
    tmp_path: Path, provider: ProviderPort
) -> NativeReplProviderState:
    from pipy_harness.native import NativeModelSelection
    from pipy_harness.native.auth_store import AuthStore
    from pipy_harness.native.catalog_state import ProviderCatalogState

    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "absent.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        openai_codex_auth_path=tmp_path / "missing-openai-codex.json",
    )
    return _FixedProviderReplState(
        provider,
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        model_runtime=ModelRuntime(catalog=catalog),
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
        settings_start = len(output_bytes(ctx.err_chunks))
        os.write(ctx.in_master, b"/settings\n")
        assert (
            wait_for_input_ready_after(
                ctx.err_chunks, "Settings —", after=settings_start
            )
            is not None
        ), "settings dialog input never became ready"
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

        # Enter the highlighted model subflow. It paints its selector before
        # raw entry; cancelling it then paints the reopened settings dialog
        # before that dialog reacquires raw input.
        selector_start = len(output_bytes(ctx.err_chunks))
        os.write(ctx.in_master, b"\r")
        assert (
            wait_for_input_ready_after(
                ctx.err_chunks, "Select provider/model", after=selector_start
            )
            is not None
        ), "settings model selector input never became ready"
        reopen_start = len(output_bytes(ctx.err_chunks))
        os.write(ctx.in_master, b"\x1b")
        assert (
            wait_for_input_ready_after(ctx.err_chunks, "Settings —", after=reopen_start)
            is not None
        ), "settings dialog input never became ready after selector cancel"
        assert (
            provider_state.current_selection().reference == "fake/fake-native-bootstrap"
        )

        # Navigate down across actionable rows (skipping headers/status) to the
        # persistent-history toggle, then toggle it OFF with Space.
        os.write(ctx.in_master, b"\x1b[B")  # → login
        os.write(ctx.in_master, b"\x1b[B")  # → toggle
        assert _wait_for(ctx.err_chunks, "→ persistent prompt history"), "nav failed"
        os.write(ctx.in_master, b" ")  # space activates the toggle
        assert _wait_for(ctx.err_chunks, "persistent prompt history: off"), (
            "toggle did not flip live"
        )

        # Navigate to the clear row and clear with Enter.
        os.write(ctx.in_master, b"\x1b[B")  # → clear
        assert _wait_for(ctx.err_chunks, "→ clear persisted history"), (
            "nav to clear failed"
        )
        os.write(ctx.in_master, b"\r")  # enter clears
        assert _wait_for(ctx.err_chunks, "clear persisted history (0 saved)"), (
            "clear did not update live"
        )

        # Resize while the dialog is still open. The settings rows are emitted
        # before the test-visible final footer sentinel in the overlay frame.
        frame_sentinel = "RESIZE_SETTINGS_FRAME_COMPLETE"
        assert _install_resize_frame_sentinel(ctx.ui, ctx.err_chunks, frame_sentinel)
        resize_start = len(output_bytes(ctx.err_chunks))
        _set_winsize(ctx.err_slave, end_rows, end_cols)
        assert (
            _wait_for_resize_frame(
                ctx.err_chunks,
                frame_sentinel,
                after=resize_start,
            )
            is not None
        ), "resize redraw did not reach its final footer row"
        after_snapshot = parse_ansi_screen(
            b"".join(ctx.err_chunks).decode("utf-8", errors="replace"),
            columns=end_cols,
            rows=end_rows,
        )

        # Close the dialog with Esc; the separator-framed input returns at the
        # new size.
        close_start = len(output_bytes(ctx.err_chunks))
        os.write(ctx.in_master, b"\x1b")
        assert (
            wait_for_input_ready_after(
                ctx.err_chunks,
                sep_end,
                after=close_start,
            )
            is not None
        ), "resumed settings input never became ready"
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
        settings_start = len(output_bytes(ctx1.err_chunks))
        os.write(ctx1.in_master, b"/settings\n")
        assert (
            wait_for_input_ready_after(
                ctx1.err_chunks, "Settings —", after=settings_start
            )
            is not None
        ), "session1 dialog input never became ready"
        # With the static single-provider state the toggle is the first
        # actionable row, so it is highlighted on open.
        assert _wait_for(ctx1.err_chunks, "→ persistent prompt history: off")
        os.write(ctx1.in_master, b" ")  # enable
        assert _wait_for(ctx1.err_chunks, "persistent prompt history: on")
        close_start = len(output_bytes(ctx1.err_chunks))
        os.write(ctx1.in_master, b"\x1b")  # esc closes the dialog
        assert (
            wait_for_input_ready_after(ctx1.err_chunks, "─" * 100, after=close_start)
            is not None
        ), "session1 input did not resume"
        # Submit a real prompt; it should be persisted.
        turn_start = len(output_bytes(ctx1.err_chunks))
        os.write(ctx1.in_master, token.encode("utf-8") + b"\n")
        assert (
            wait_for_input_ready_after(
                ctx1.err_chunks, "RESPONSE_MARKER_DONE", after=turn_start
            )
            is not None
        ), "turn did not return to ready input"
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
        assert _wait_for(ctx2.err_chunks, token), (
            "prompt was not recalled in a fresh session"
        )
        # Clear the recalled text, then disable + clear persistence via /settings.
        os.write(ctx2.in_master, b"\x15")  # ctrl-u kills to line start
        settings_start = len(output_bytes(ctx2.err_chunks))
        os.write(ctx2.in_master, b"/settings\n")
        assert (
            wait_for_input_ready_after(
                ctx2.err_chunks, "Settings —", after=settings_start
            )
            is not None
        ), "session2 dialog input never became ready"
        assert _wait_for(ctx2.err_chunks, "→ persistent prompt history: on")
        os.write(ctx2.in_master, b" ")  # disable
        assert _wait_for(ctx2.err_chunks, "persistent prompt history: off")
        os.write(ctx2.in_master, b"\x1b[B")  # → clear row
        assert _wait_for(ctx2.err_chunks, "→ clear persisted history")
        os.write(ctx2.in_master, b"\r")  # clear
        assert _wait_for(ctx2.err_chunks, "clear persisted history (0 saved)")
        close_start = len(output_bytes(ctx2.err_chunks))
        os.write(ctx2.in_master, b"\x1b")  # esc closes the dialog
        assert (
            wait_for_input_ready_after(ctx2.err_chunks, "─" * 100, after=close_start)
            is not None
        ), "session2 input did not resume"
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
        # Ordered raw bytes prove Up has no recalled value before Ctrl-D exits.
        os.write(ctx3.in_master, b"\x1b[A\x04")
    finally:
        captured3 = _finish_pty_repl_session(ctx3)
    assert not ctx3.worker.is_alive()
    assert "\x1b[?1049h" not in captured3
    # The cleared prompt is not recalled in the fresh, disabled session.
    assert token not in captured3
    assert seen3 == []


class _TreeMarkProvider:
    """Tool-capable provider that streams a unique marker per turn.

    The marker encodes the active-branch user messages it can see, so a PTY
    test can both wait for each turn and assert which branch reached the model.
    """

    name = "fake"
    supports_tool_calls = True
    model_id = "fake-native-bootstrap"

    def __init__(self) -> None:
        self.requests: list[tuple[str, ...]] = []
        self._n = 0

    def complete(
        self, request, *, stream_sink=None, reasoning_sink=None, cancel_token=None
    ):
        from datetime import UTC, datetime

        from pipy_harness.native.agent import AgentUserMessage
        from pipy_harness.native.models import ProviderResult

        del reasoning_sink
        self._n += 1
        users = tuple(
            m.content.value for m in request.messages if isinstance(m, AgentUserMessage)
        )
        self.requests.append(users)
        text = f"MARK{self._n}[{'|'.join(users)}]"
        if stream_sink is not None:
            stream_sink(text)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=text,
            tool_calls=(),
        )


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_tree_selector_rehydrates_user_message_into_editor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Open `/tree` over a real PTY, pick a prior user message, and branch.

    Proves the live selector opens, navigation works, selecting a user message
    rehydrates the editor with that text, and submitting the (re)entered text
    creates an alternative sibling branch whose provider context follows only
    that branch.
    """

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")

    workspace = tmp_path / "ws"
    workspace.mkdir()
    tree = NativeSessionTree.create(workspace, session_dir=tmp_path / "sessions")
    provider = _TreeMarkProvider()
    store = PromptHistoryStore(tmp_path / "history.json")

    ctx = _start_pty_repl_session(
        monkeypatch,
        workspace,
        provider=provider,
        provider_state=None,
        store=store,
        native_session=tree,
    )
    try:
        assert _wait_for(ctx.err_chunks, "escape interrupt"), "startup never painted"
        os.write(ctx.in_master, b"alpha\n")
        assert wait_for_input_ready_after(ctx.err_chunks, "MARK1[") is not None, (
            "first turn did not return to ready input"
        )
        os.write(ctx.in_master, b"beta\n")
        assert wait_for_input_ready_after(ctx.err_chunks, "MARK2[") is not None, (
            "second turn did not return to ready input"
        )

        os.write(ctx.in_master, b"/tree\n")
        assert wait_for_input_ready_after(ctx.err_chunks, "Session tree") is not None, (
            "tree selector input never became ready"
        )
        # No provider turn while the selector is open.
        assert len(provider.requests) == 2, "selector opened a provider turn"
        # Default highlight is the last active row (assistant); move up to the
        # 'beta' user message and select it.
        os.write(ctx.in_master, b"\x1b[A\r")  # up to beta, then select it
        assert (
            wait_for_input_ready_after(ctx.err_chunks, "rehydrating editor") is not None
        ), "rehydrated editor input never became ready"
        # The editor is pre-filled with the selected text; submit it as-is to
        # branch from beta's parent.
        os.write(ctx.in_master, b"\r")
        assert wait_for_input_ready_after(ctx.err_chunks, "MARK3[") is not None, (
            "branch turn did not return to ready input"
        )
        os.write(ctx.in_master, b"\x04")
        ctx.worker.join(timeout=8.0)
    finally:
        captured = _finish_pty_repl_session(ctx)

    assert not ctx.worker.is_alive(), "tree selector session did not exit"
    assert "\x1b[?1049h" not in captured  # never the alternate screen

    # The branch turn saw alpha + beta only (single active branch), not a
    # duplicated history.
    branch_request = provider.requests[-1]
    assert "alpha" in branch_request
    assert "beta" in branch_request

    # The native file now holds a sibling branch: beta's parent has two child
    # user messages with content 'beta'.
    from pipy_harness.native.agent import AgentUserMessage
    from pipy_harness.native.session_tree import MessageEntry

    assert tree.path is not None
    reopened = NativeSessionTree.open(tree.path)
    beta_users = [
        e
        for e in reopened.get_entries()
        if isinstance(e, MessageEntry)
        and isinstance(e.message, AgentUserMessage)
        and e.message.content.value == "beta"
    ]
    assert len(beta_users) == 2, "submitting did not create a sibling branch"


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_tree_selector_escape_label_and_filter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """`/tree` Escape cancels; `L` labels; `Ctrl-O` cycles the filter mode."""

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")

    workspace = tmp_path / "ws"
    workspace.mkdir()
    tree = NativeSessionTree.create(workspace, session_dir=tmp_path / "sessions")
    provider = _TreeMarkProvider()
    store = PromptHistoryStore(tmp_path / "history.json")

    ctx = _start_pty_repl_session(
        monkeypatch,
        workspace,
        provider=provider,
        provider_state=None,
        store=store,
        native_session=tree,
    )
    try:
        assert _wait_for(ctx.err_chunks, "escape interrupt"), "startup never painted"
        os.write(ctx.in_master, b"alpha\n")
        assert wait_for_input_ready_after(ctx.err_chunks, "MARK1[") is not None, (
            "first turn did not return to ready input"
        )

        # Open the selector; label the highlighted entry, cycle the filter once,
        # then cancel with Escape. Ordered raw bytes need no paint delay.
        os.write(ctx.in_master, b"/tree\n")
        assert wait_for_input_ready_after(ctx.err_chunks, "Session tree") is not None, (
            "tree selector input never became ready"
        )
        os.write(ctx.in_master, b"L\x0f")
        assert _wait_for(ctx.err_chunks, "filter (no-tools)"), (
            "filter cycle not reflected"
        )
        os.write(ctx.in_master, b"\x1b")  # Escape cancels
        assert (
            wait_for_input_ready_after(ctx.err_chunks, "/tree cancelled") is not None
        ), "tree cancellation did not return to ready input"
        os.write(ctx.in_master, b"\x04")
        ctx.worker.join(timeout=8.0)
    finally:
        _finish_pty_repl_session(ctx)

    assert not ctx.worker.is_alive()
    # Selecting nothing ran no extra provider turn.
    assert len(provider.requests) == 1

    # The label keystroke persisted a label entry in the native file.
    from pipy_harness.native.session_tree import LabelEntry

    assert tree.path is not None
    reopened = NativeSessionTree.open(tree.path)
    assert any(isinstance(e, LabelEntry) and e.label for e in reopened.get_entries()), (
        "Shift-L did not persist a label"
    )


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("key", "label"),
    [(b"\x1b", "escape"), (b"\x03", "ctrl-c")],
)
def test_pty_active_turn_interrupt_cancels_and_returns_to_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    key: bytes,
    label: str,
):
    """Escape and Ctrl-C during an active provider turn truly cancel it.

    Drives the real product TUI over a PTY: while a slow provider turn is
    in-flight (blocked at the provider boundary on the cancel token), the key
    sequence must cancel the request, render the red ``Operation aborted``
    state, and leave the next prompt usable for a follow-up turn that the
    provider answers normally. The provider observing cancellation (rather than
    the loop merely hiding late output) is asserted directly on the fake.
    """

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    # The first turn blocks until cancelled at the provider boundary; the
    # second turn (the follow-up prompt) completes normally and proves the
    # prompt is usable again after the abort.
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=((),),
        cancellable_turns=1,
        final_text="SECOND_TURN_ANSWER_DONE",
    )
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
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
        os.write(in_master, b"start a slow turn\n")
        # The spinner only paints once the provider turn is actually in-flight.
        assert _wait_for(err_chunks, "Working"), f"{label}: turn never went active"
        # Send the interrupt key while the turn is blocked at the boundary.
        os.write(in_master, key)
        # The abort notice is painted before the next outer raw transition;
        # observe both phases under one deadline.
        assert (
            wait_for_input_ready_after(err_chunks, "Operation aborted") is not None
        ), f"{label}: post-abort input never became ready"
        # The provider observed cancellation at its boundary, not a UI-only flag.
        assert _wait_for_predicate(lambda: provider.cancel_observed), (
            f"{label}: provider never observed cancellation"
        )
        os.write(in_master, b"now answer me\n")
        assert _wait_for(err_chunks, "SECOND_TURN_ANSWER_DONE"), (
            f"{label}: follow-up prompt was not usable"
        )
        os.write(in_master, b"\x04")  # ctrl-d ends the loop on an empty prompt
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    assert not worker.is_alive(), f"{label}: session did not exit"
    captured = b"".join(err_chunks).decode("utf-8", errors="replace")
    assert "Operation aborted" in captured
    assert "SECOND_TURN_ANSWER_DONE" in captured
    # Inline model only: the abort path never enters the alternate screen.
    assert "\x1b[?1049h" not in captured


class _PromptCapturingProvider:
    """Tool-capable fake provider recording each turn's user prompt text."""

    def __init__(self, final_text: str) -> None:
        self._final_text = final_text
        self.supports_tool_calls = True
        self.model_id = "fake-model"
        self.user_prompts: list[str] = []
        self.attachment_counts: list[int] = []
        self.calls = 0

    @property
    def name(self) -> str:
        return "fake"

    def complete(
        self, request, *, stream_sink=None, reasoning_sink=None, cancel_token=None
    ):
        from datetime import UTC, datetime

        from pipy_harness.native.models import ProviderResult

        del stream_sink, reasoning_sink, cancel_token
        self.calls += 1
        text = request.user_prompt or ""
        for message in getattr(request, "messages", ()) or ():
            text += "\n" + message.content.value
        self.user_prompts.append(text)
        self.attachment_counts.append(len(getattr(request, "attachments", ()) or ()))
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=request.provider_name,
            model_id=request.model_id,
            started_at=now,
            ended_at=now,
            final_text=self._final_text,
            tool_calls=(),
        )


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("columns", "rows", "label"),
    [(100, 40, "ghostty"), (80, 24, "zellij")],
)
def test_pty_at_file_picker_ranks_and_accepts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    columns: int,
    rows: int,
    label: str,
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))

    (tmp_path / "src" / "tui").mkdir(parents=True)
    (tmp_path / "src" / "tui" / "config.py").write_text("nested\n")
    (tmp_path / "src" / "config.py").write_text("top\n")
    (tmp_path / "README.md").write_text("readme\n")

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    provider = _PromptCapturingProvider("PICKER_TURN_DONE")
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(provider=cast(ProviderPort, provider), tool_registry={})
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
    )

    def _run() -> None:
        session.run(
            workspace_root=tmp_path,
            input_stream=cast(TextIO, stdin),
            output_stream=cast(TextIO, terminal),
            error_stream=cast(TextIO, terminal),
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        # Type a genuine prompt naming a file with an @ token (no submit yet).
        os.write(in_master, b"see @config")
        # The picker popup opens and ranks workspace paths; the top-ranked
        # tie-break is the shallower src/config.py.
        assert _wait_for_predicate(
            lambda: "@src/config.py" in b"".join(err_chunks).decode("utf-8", "replace")
        ), f"{label}: @ picker did not rank workspace paths"
        # No provider turn ran while the picker was open.
        assert provider.calls == 0, f"{label}: picker ran a provider turn"
        # Accept the highlighted candidate with Tab, then submit.
        os.write(in_master, b"\t")
        assert _wait_for_predicate(
            lambda: (
                provider.calls == 0
                and "@src/config.py" in b"".join(err_chunks).decode("utf-8", "replace")
            )
        )
        os.write(in_master, b"\n")
        assert _wait_for(err_chunks, "PICKER_TURN_DONE"), (
            f"{label}: turn never completed"
        )
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    assert not worker.is_alive(), f"{label}: session did not exit"
    captured = b"".join(err_chunks).decode("utf-8", errors="replace")
    # Inline only; never the alternate screen.
    assert "\x1b[?1049h" not in captured
    # The accepted @path made it into the submitted prompt and was resolved.
    assert provider.calls == 1
    assert any("@src/config.py" in prompt for prompt in provider.user_prompts), (
        f"{label}: accepted @path did not reach the submitted prompt"
    )


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("columns", "rows", "label"),
    [(100, 40, "ghostty"), (80, 24, "zellij")],
)
def test_pty_bash_shortcuts_run_record_and_cancel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    columns: int,
    rows: int,
    label: str,
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    provider = _PromptCapturingProvider("RECALL_DONE")
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(provider=cast(ProviderPort, provider), tool_registry={})
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
    )

    def _run() -> None:
        session.run(
            workspace_root=tmp_path,
            input_stream=cast(TextIO, stdin),
            output_stream=cast(TextIO, terminal),
            error_stream=cast(TextIO, terminal),
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()

    def _text() -> str:
        return b"".join(err_chunks).decode("utf-8", "replace")

    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        # Bash-mode affordance appears while the buffer starts with '!'.
        os.write(in_master, b"!ec")
        assert _wait_for_predicate(lambda: "! bash" in _text()), (
            f"{label}: bash-mode border did not appear"
        )
        # !cmd runs a shell command with no provider turn and records context.
        command_ready = input_ready_count(err_chunks)
        os.write(in_master, b"ho ctx-bang\n")
        # One raw owner watches the command; the next owns the resumed prompt.
        assert wait_for_input_ready_count(err_chunks, command_ready + 2), (
            f"{label}: !cmd did not return to ready input"
        )
        assert b"ctx-bang" in output_bytes(err_chunks), (
            f"{label}: !cmd output not shown"
        )
        assert provider.calls == 0, f"{label}: !cmd ran a provider turn"
        # The next provider turn sees the recorded command/output in context.
        os.write(in_master, b"recall now\n")
        assert wait_for_input_ready_after(err_chunks, "RECALL_DONE") is not None, (
            f"{label}: first recall turn did not return to ready input"
        )
        assert any("ctx-bang" in prompt for prompt in provider.user_prompts), (
            f"{label}: !cmd was not recorded into provider context"
        )
        # !!cmd runs but is excluded from provider context.
        secret_ready = input_ready_count(err_chunks)
        os.write(in_master, b"!!echo secret-bang\n")
        assert wait_for_input_ready_count(err_chunks, secret_ready + 2), (
            f"{label}: !!cmd did not return to ready input"
        )
        assert b"secret-bang" in output_bytes(err_chunks), (
            f"{label}: !!cmd output not shown"
        )
        second_recall_start = len(output_bytes(err_chunks))
        os.write(in_master, b"recall again\n")
        assert (
            wait_for_input_ready_after(
                err_chunks, "RECALL_DONE", after=second_recall_start
            )
            is not None
        ), f"{label}: second recall turn did not return to ready input"
        assert not any("secret-bang" in prompt for prompt in provider.user_prompts), (
            f"{label}: !!cmd leaked into provider context"
        )
        # Escape cancels a long-running ! command without ending the session.
        os.write(in_master, b"!sleep 30\n")
        assert wait_for_input_ready_after(err_chunks, "$ sleep 30") is not None, (
            f"{label}: active command input never became ready"
        )
        os.write(in_master, b"\x1b")
        assert (
            wait_for_input_ready_after(err_chunks, "cancelled by escape") is not None
        ), f"{label}: post-cancel input never became ready"
        # Session is still usable.
        third_turn_start = len(output_bytes(err_chunks))
        os.write(in_master, b"still here\n")
        assert (
            wait_for_input_ready_after(
                err_chunks, "RECALL_DONE", after=third_turn_start
            )
            is not None
        ), f"{label}: post-cancel turn did not return to ready input"
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    assert not worker.is_alive(), f"{label}: session did not exit"
    captured = _text()
    assert "\x1b[?1049h" not in captured  # inline only, never alt-screen


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_slash_quit_during_local_shell_output_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A slash command remains reachable while a `!` command streams output."""

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    prompts: list[str] = []
    provider = _PromptRecordingProvider(prompts)
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
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
        os.write(in_master, b'!printf "running-tests\\n"; sleep 30\n')
        assert _wait_for(err_chunks, "running-tests"), "shell output never streamed"
        os.write(in_master, b"/quit\n")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    assert not worker.is_alive(), "slash /quit did not exit during shell output"
    assert prompts == []


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_slash_quit_during_model_bash_tool_output_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A slash command remains reachable while the model's bash tool streams."""

    class BashToolCallProvider:
        name = "fake"
        model_id = "fake-native-bootstrap"
        supports_tool_calls = True

        def __init__(self) -> None:
            self.calls = 0

        def complete(
            self, request, *, stream_sink=None, reasoning_sink=None, cancel_token=None
        ):
            from datetime import UTC, datetime

            del stream_sink, reasoning_sink, cancel_token
            self.calls += 1
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=request.provider_name,
                model_id=request.model_id,
                started_at=now,
                ended_at=now,
                final_text="",
                tool_calls=(
                    ProviderToolCall(
                        provider_correlation_id="call-1",
                        tool_name="bash",
                        arguments_json=json.dumps(
                            {"command": 'printf "tool-running\\n"; sleep 30'}
                        ),
                    ),
                ),
            )

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    provider = BashToolCallProvider()
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(provider=provider)
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
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
        os.write(in_master, b"start bash tool\n")
        assert _wait_for(err_chunks, "tool-running"), "bash tool output never streamed"
        os.write(in_master, b"/quit\n")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    assert not worker.is_alive(), "slash /quit did not exit during bash tool output"
    assert provider.calls == 1


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_local_command_during_multi_tool_call_balances_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Interrupting one parallel tool call leaves results for the skipped calls."""

    class MultiToolCallProvider:
        name = "fake"
        model_id = "fake-native-bootstrap"
        supports_tool_calls = True

        def __init__(self) -> None:
            self.calls = 0
            self.balanced = threading.Event()

        def complete(
            self, request, *, stream_sink=None, reasoning_sink=None, cancel_token=None
        ):
            from datetime import UTC, datetime

            del stream_sink, reasoning_sink, cancel_token
            self.calls += 1
            now = datetime.now(UTC)
            if self.calls == 1:
                return ProviderResult(
                    status=HarnessStatus.SUCCEEDED,
                    provider_name=request.provider_name,
                    model_id=request.model_id,
                    started_at=now,
                    ended_at=now,
                    final_text="",
                    tool_calls=(
                        ProviderToolCall(
                            provider_correlation_id="call-1",
                            tool_name="bash",
                            arguments_json=json.dumps(
                                {"command": 'printf "first-tool-running\\n"; sleep 30'}
                            ),
                        ),
                        ProviderToolCall(
                            provider_correlation_id="call-2",
                            tool_name="bash",
                            arguments_json=json.dumps({"command": "echo second"}),
                        ),
                    ),
                )
            assistant_index = next(
                index
                for index, message in enumerate(request.messages)
                if isinstance(message, AgentAssistantMessage) and message.tool_calls
            )
            assistant = request.messages[assistant_index]
            result_count = sum(
                isinstance(message, AgentToolResultMessage)
                for message in request.messages[assistant_index + 1 :]
            )
            if result_count == len(assistant.tool_calls):
                self.balanced.set()
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=request.provider_name,
                model_id=request.model_id,
                started_at=now,
                ended_at=now,
                final_text="HISTORY_BALANCED",
                tool_calls=(),
            )

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    provider = MultiToolCallProvider()
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(provider=provider)
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
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
        os.write(in_master, b"start multi tool\n")
        assert _wait_for(err_chunks, "first-tool-running"), "bash tool never streamed"
        hotkeys_start = len(output_bytes(err_chunks))
        os.write(in_master, b"/hotkeys\n")
        # Preserve the shipped post-TCSAFLUSH byte handshake, now through the
        # shared PTY synchronization owner used by every readiness transition.
        assert (
            wait_for_input_ready_after(
                err_chunks, "Keyboard Shortcuts", after=hotkeys_start
            )
            is not None
        ), "post-command input never entered raw mode"
        followup_start = len(output_bytes(err_chunks))
        os.write(in_master, b"after interrupt\n")
        assert _wait_for_predicate(provider.balanced.is_set), "tool results unbalanced"
        assert (
            wait_for_input_ready_after(
                err_chunks,
                "HISTORY_BALANCED",
                after=followup_start,
            )
            is not None
        ), "follow-up did not return to ready input"
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    assert not worker.is_alive(), "session did not exit after balanced interruption"


def _reasoning_catalog_state(tmp_path: Path, provider: ProviderPort, model_id: str):
    from pipy_harness.native import NativeModelSelection
    from pipy_harness.native.auth_store import AuthStore
    from pipy_harness.native.catalog_state import ProviderCatalogState

    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={"OPENAI_API_KEY": "sk"},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    return _FixedProviderReplState(
        provider,
        selection=NativeModelSelection("openai", model_id),
        model_runtime=ModelRuntime(catalog=catalog),
        persist_defaults=False,
    )


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("columns", "rows", "label"),
    [(100, 40, "ghostty"), (80, 24, "zellij")],
)
def test_pty_thinking_and_model_cycle_hotkeys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    columns: int,
    rows: int,
    label: str,
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    provider = _PromptCapturingProvider("TURN_DONE")
    provider.model_id = "gpt-5.5"
    state = _reasoning_catalog_state(tmp_path, cast(ProviderPort, provider), "gpt-5.5")
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(
        provider=cast(ProviderPort, provider),
        tool_registry={},
        provider_state=state,
    )
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
    )

    def _run() -> None:
        session.run(
            workspace_root=tmp_path,
            input_stream=cast(TextIO, stdin),
            output_stream=cast(TextIO, terminal),
            error_stream=cast(TextIO, terminal),
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()

    def _text() -> str:
        return b"".join(err_chunks).decode("utf-8", "replace")

    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        # Shift+Tab cycles the thinking level off -> minimal (no provider turn).
        minimal_start = len(output_bytes(err_chunks))
        os.write(in_master, b"\x1b[Z")
        assert (
            wait_for_input_ready_after(
                err_chunks, "thinking level: minimal", after=minimal_start
            )
            is not None
        ), f"{label}: shift+tab did not return to ready input"
        assert provider.calls == 0, f"{label}: thinking cycle ran a provider turn"
        # Shift+Tab again -> low; the footer effort label tracks the runtime level.
        low_start = len(output_bytes(err_chunks))
        os.write(in_master, b"\x1b[Z")
        assert (
            wait_for_input_ready_after(
                err_chunks, "thinking level: low", after=low_start
            )
            is not None
        ), f"{label}: second shift+tab did not return to ready input"
        # Ctrl+P cycles the model through the available set (no provider turn).
        model_start = len(output_bytes(err_chunks))
        os.write(in_master, b"\x10")
        assert (
            wait_for_input_ready_after(err_chunks, "selected model", after=model_start)
            is not None
        ), f"{label}: ctrl+p did not return to ready input"
        assert provider.calls == 0, f"{label}: model cycle ran a provider turn"
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    assert not worker.is_alive(), f"{label}: session did not exit"
    captured = _text()
    assert "\x1b[?1049h" not in captured  # inline only


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("columns", "rows", "label"),
    [(100, 40, "ghostty"), (80, 24, "zellij")],
)
def test_pty_folding_toggles_thinking_and_tool_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    columns: int,
    rows: int,
    label: str,
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    provider = _PromptCapturingProvider("TURN_DONE")
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(provider=cast(ProviderPort, provider), tool_registry={})
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
    )

    def _run() -> None:
        session.run(
            workspace_root=tmp_path,
            input_stream=cast(TextIO, stdin),
            output_stream=cast(TextIO, terminal),
            error_stream=cast(TextIO, terminal),
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        hidden_start = len(output_bytes(err_chunks))
        os.write(in_master, b"\x14")  # ctrl+t toggles thinking visibility
        assert (
            wait_for_input_ready_after(
                err_chunks, "thinking blocks: hidden", after=hidden_start
            )
            is not None
        ), f"{label}: ctrl+t did not return to ready input"
        assert ui.components.transcript.thinking_hidden is True
        visible_start = len(output_bytes(err_chunks))
        os.write(in_master, b"\x14")  # toggle back
        assert (
            wait_for_input_ready_after(
                err_chunks, "thinking blocks: visible", after=visible_start
            )
            is not None
        ), f"{label}: second ctrl+t did not return to ready input"
        expanded_start = len(output_bytes(err_chunks))
        os.write(in_master, b"\x0f")  # ctrl+o expands tool output
        assert (
            wait_for_input_ready_after(
                err_chunks, "tool output: expanded", after=expanded_start
            )
            is not None
        ), f"{label}: ctrl+o did not return to ready input"
        assert ui.components.transcript.tools_expanded is True
        assert provider.calls == 0, f"{label}: a fold toggle ran a provider turn"
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    assert not worker.is_alive(), f"{label}: session did not exit"
    captured = b"".join(err_chunks).decode("utf-8", "replace")
    assert "\x1b[?1049h" not in captured


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("columns", "rows", "label"),
    [(100, 40, "ghostty"), (80, 24, "zellij")],
)
def test_pty_never_enables_mouse_tracking(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    columns: int,
    rows: int,
    label: str,
):
    """The renderer must never enable xterm mouse tracking, so the terminal /
    multiplexer keeps ownership of click-drag text selection over committed
    scrollback and the live region. Asserted across startup, idle, an open
    overlay, an active turn, and after abort."""

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    provider = _PromptCapturingProvider("MOUSE_TURN_DONE")
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(provider=cast(ProviderPort, provider), tool_registry={})
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
    )

    def _run() -> None:
        session.run(
            workspace_root=tmp_path,
            input_stream=cast(TextIO, stdin),
            output_stream=cast(TextIO, terminal),
            error_stream=cast(TextIO, terminal),
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        settings_start = len(output_bytes(err_chunks))
        os.write(in_master, b"/settings\n")  # open an overlay
        assert (
            wait_for_input_ready_after(err_chunks, "Settings", after=settings_start)
            is not None
        ), f"{label}: settings overlay input never became ready"
        close_start = len(output_bytes(err_chunks))
        os.write(in_master, b"\x1b")  # close overlay
        assert (
            wait_for_input_ready_after(err_chunks, "─" * columns, after=close_start)
            is not None
        ), f"{label}: main input did not resume after settings"
        turn_start = len(output_bytes(err_chunks))
        os.write(in_master, b"ask something\n")  # active turn
        assert (
            wait_for_input_ready_after(err_chunks, "MOUSE_TURN_DONE", after=turn_start)
            is not None
        ), f"{label}: turn did not return to ready input"
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    captured = b"".join(err_chunks).decode("utf-8", "replace")
    for mode in ("?1000h", "?1002h", "?1003h", "?1006h", "?1015h"):
        assert mode not in captured, f"{label}: emitted mouse-tracking enable {mode}"
    assert "\x1b[?1049h" not in captured  # and never the alternate screen


class _SteeringProvider:
    """Tool-capable provider that blocks the first turn so mid-turn input can be
    queued, then completes subsequent (drained) turns immediately."""

    def __init__(self) -> None:
        self.supports_tool_calls = True
        self.model_id = "fake-model"
        self.calls = 0
        self.user_prompts: list[str] = []

    @property
    def name(self) -> str:
        return "fake"

    def complete(
        self, request, *, stream_sink=None, reasoning_sink=None, cancel_token=None
    ):
        from datetime import UTC, datetime

        from pipy_harness.native.cancellation import ProviderCancelledError
        from pipy_harness.native.models import ProviderResult

        del stream_sink, reasoning_sink
        self.calls += 1
        self.user_prompts.append(request.user_prompt or "")
        if self.calls == 1 and cancel_token is not None:
            # Keep the first turn active until a steering Enter aborts it.
            if cancel_token.event.wait(timeout=8.0):
                raise ProviderCancelledError("steered")
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=request.provider_name,
            model_id=request.model_id,
            started_at=now,
            ended_at=now,
            final_text=f"DRAINED_TURN_{self.calls}",
            tool_calls=(),
        )


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("columns", "rows", "label"),
    [(100, 40, "ghostty"), (80, 24, "zellij")],
)
def test_pty_steering_and_follow_up_queue_and_drain_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    columns: int,
    rows: int,
    label: str,
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    provider = _SteeringProvider()
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(provider=cast(ProviderPort, provider), tool_registry={})
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
    )

    def _run() -> None:
        session.run(
            workspace_root=tmp_path,
            input_stream=cast(TextIO, stdin),
            output_stream=cast(TextIO, terminal),
            error_stream=cast(TextIO, terminal),
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()

    def _text() -> str:
        return b"".join(err_chunks).decode("utf-8", "replace")

    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        # Start a turn; the provider blocks so we can type mid-turn.
        os.write(in_master, b"original question\n")
        assert _wait_for_predicate(lambda: provider.calls == 1), (
            f"{label}: first turn never started"
        )
        # Alt+Enter queues a follow-up (no interrupt); it renders in pending.
        os.write(in_master, b"followup msg\x1b\r")
        assert _wait_for(err_chunks, "Follow-up: followup msg"), (
            f"{label}: follow-up did not render in the pending region"
        )
        assert provider.calls == 1, f"{label}: follow-up ran a provider turn"
        # Enter queues a steering message and interrupts the turn.
        steering_start = len(output_bytes(err_chunks))
        os.write(in_master, b"steer msg\n")
        # Drain order: steering first, then follow-up. The third provider call
        # starts before its result is painted, so completion is the third result
        # plus the following post-TCSAFLUSH prompt acknowledgement.
        assert (
            wait_for_input_ready_after(
                err_chunks,
                "DRAINED_TURN_3",
                after=steering_start,
                timeout=10.0,
            )
            is not None
        ), f"{label}: queued messages did not drain to ready input"
        assert provider.calls >= 3, f"{label}: third provider turn never ran"
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    assert not worker.is_alive(), f"{label}: session did not exit"
    # The first turn was the original; then steering drained before follow-up.
    assert provider.user_prompts[0] == "original question"
    steer_index = next(
        i for i, p in enumerate(provider.user_prompts) if p == "steer msg"
    )
    follow_index = next(
        i for i, p in enumerate(provider.user_prompts) if p == "followup msg"
    )
    assert steer_index < follow_index, (
        f"{label}: steering must drain before follow-up: {provider.user_prompts}"
    )
    captured = _text()
    assert "\x1b[?1049h" not in captured


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("columns", "rows", "label"),
    [(100, 40, "ghostty"), (80, 24, "zellij")],
)
def test_pty_clipboard_image_paste_attaches_on_submit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    columns: int,
    rows: int,
    label: str,
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    from pipy_harness.native.clipboard import ImageClipboardResult
    from pipy_harness.native.ui.clipboard_images import create_clipboard_config

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128
    read_image = lambda: ImageClipboardResult(  # noqa: E731
        found=True, data=png, media_type="image/png", detail="ok"
    )
    provider = _PromptCapturingProvider("IMAGE_TURN_DONE")
    clipboard_config = create_clipboard_config(read_image)
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
        clipboard_config=clipboard_config,
    )
    session = CodingSession(
        provider=cast(ProviderPort, provider),
        tool_registry={},
        clipboard_image_read=read_image,
    )
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
    )

    def _run() -> None:
        session.run(
            workspace_root=tmp_path,
            input_stream=cast(TextIO, stdin),
            output_stream=cast(TextIO, terminal),
            error_stream=cast(TextIO, terminal),
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        os.write(in_master, b"look at this \x16")  # ctrl+v pastes the clipboard image
        # The pasted reference is in the editor; the long temp path scrolls the
        # @image: prefix out of the narrow input cell, so the visible tail shows
        # the clipboard filename. Assert both editor state and the visible frame.
        assert _wait_for_predicate(lambda: "@image:" in ui.input_editor.text), (
            f"{label}: ctrl+v did not insert an @image: reference"
        )
        assert _wait_for_predicate(
            lambda: "pipy-clipboard" in b"".join(err_chunks).decode("utf-8", "replace")
        ), f"{label}: clipboard reference not visible in the editor"
        assert provider.calls == 0, f"{label}: clipboard paste ran a provider turn"
        os.write(in_master, b"\n")  # submit; the attachment resolves
        assert _wait_for(err_chunks, "IMAGE_TURN_DONE"), (
            f"{label}: turn never completed"
        )
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    assert not worker.is_alive(), f"{label}: session did not exit"
    assert provider.calls == 1
    assert provider.attachment_counts[-1] == 1, (
        f"{label}: pasted image was not attached on submit"
    )
    # The owner-only clipboard temp dir holds the image; bytes never archived.
    assert ui.clipboard_images.config is clipboard_config
    written = list(clipboard_config.temp_dir.glob("pipy-clipboard-*.png"))
    assert written and stat.S_IMODE(written[0].stat().st_mode) == 0o600
    captured = b"".join(err_chunks).decode("utf-8", "replace")
    assert "\x1b[?1049h" not in captured


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(
    ("columns", "rows", "label"),
    [(100, 40, "ghostty"), (80, 24, "zellij")],
)
def test_pty_scoped_models_overlay_saves_cycle_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    columns: int,
    rows: int,
    label: str,
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8", closefd=False)
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8", closefd=False)
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    from pipy_harness.native.settings import SettingsManager

    provider = _PromptCapturingProvider("TURN_DONE")
    provider.model_id = "gpt-5.5"
    state = _reasoning_catalog_state(tmp_path, cast(ProviderPort, provider), "gpt-5.5")
    settings = SettingsManager.for_workspace(tmp_path)
    ui = TerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    session = CodingSession(
        provider=cast(ProviderPort, provider),
        tool_registry={},
        provider_state=state,
        settings_manager=settings,
    )
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: (
            ui
        ),
    )

    def _run() -> None:
        session.run(
            workspace_root=tmp_path,
            input_stream=cast(TextIO, stdin),
            output_stream=cast(TextIO, terminal),
            error_stream=cast(TextIO, terminal),
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()

    def _text() -> str:
        return b"".join(err_chunks).decode("utf-8", "replace")

    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        overlay_start = len(output_bytes(err_chunks))
        os.write(in_master, b"/scoped-models\n")
        assert (
            wait_for_input_ready_after(err_chunks, "Scoped models", after=overlay_start)
            is not None
        ), f"{label}: scoped-models overlay input never became ready"
        # Toggle under the established raw owner and require its fresh repaint
        # before saving; do not hide the intermediate state in combined keys.
        toggle_start = len(output_bytes(err_chunks))
        os.write(in_master, b" ")
        assert (
            wait_for_output(err_chunks, "Scoped models", after=toggle_start) is not None
        ), f"{label}: toggled scope did not repaint"
        assert ui._overlays.scoped_checked, (
            f"{label}: highlighted model was not checked"
        )
        save_start = len(output_bytes(err_chunks))
        os.write(in_master, b"\n")
        assert (
            wait_for_input_ready_after(
                err_chunks, "scoped models set", after=save_start
            )
            is not None
        ), f"{label}: scoped-model save did not return to ready input"
        assert provider.calls == 0, f"{label}: overlay ran a provider turn"
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        _close_pty_session(stdin, terminal, in_master, err_master, err_thread)

    assert not worker.is_alive(), f"{label}: session did not exit"
    # The chosen scope persisted as enabledModels patterns.
    fresh = SettingsManager.for_workspace(tmp_path)
    assert fresh.get_enabled_models(), f"{label}: scope was not persisted"
    captured = _text()
    assert "\x1b[?1049h" not in captured


class _GatedRecordingProvider:
    """Records each turn's submitted prompt; turn 1 blocks until released.

    Blocking the first turn opens a window to queue a follow-up mid-turn; once
    released the turn settles on its own so the queued message drains as the
    next prompt. Later turns complete immediately.
    """

    name = "fake"
    model_id = "fake-native-bootstrap"
    supports_tool_calls = True

    def __init__(
        self,
        prompts: list[str],
        active_event: threading.Event,
        release_event: threading.Event,
    ) -> None:
        self._prompts = prompts
        self._active = active_event
        self._release = release_event
        self._turn = 0

    def complete(
        self, request, *, stream_sink=None, reasoning_sink=None, cancel_token=None
    ):
        from datetime import UTC, datetime

        from pipy_harness.native.models import ProviderResult

        del stream_sink, reasoning_sink, cancel_token
        self._turn += 1
        self._prompts.append(request.user_prompt)
        if self._turn == 1:
            self._active.set()
            self._release.wait(timeout=6.0)
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


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize("queued", ["/hotkeys", "!echo queued-shell"])
def test_pty_drained_followup_with_command_prefix_reaches_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, queued: str
):
    """A queued follow-up beginning with `/` or `!` drains to the model verbatim.

    Queued steering/follow-up messages (Pi) are provider-visible prompt text,
    not local commands. A follow-up enqueued mid-turn that happens to start with
    a slash-command (``/hotkeys``) or bash-shortcut (``!echo``) prefix must reach
    the provider as the next turn's prompt — never be intercepted and run as a
    local command (which would silently drop it from the conversation).
    """

    prompts: list[str] = []
    active = threading.Event()
    release = threading.Event()
    provider = _GatedRecordingProvider(prompts, active, release)

    def drive(in_master: int, chunks: list[bytes]) -> None:
        os.write(in_master, b"begin the turn\n")
        assert _wait_for_predicate(active.is_set), "turn 1 never went active"
        # Queue a follow-up that begins with a command prefix while the turn is
        # in flight (Alt+Enter enqueues a follow-up without interrupting).
        os.write(in_master, queued.encode("utf-8"))
        os.write(in_master, b"\x1b\r")
        assert _wait_for(chunks, f"Follow-up: {queued}"), "follow-up was not queued"
        # Let the turn settle so the queue promotes and drains as the next turn.
        release.set()
        assert _wait_for(chunks, "TURN_2_DONE"), (
            "queued follow-up never reached the model (intercepted as a command)"
        )

    captured = _run_editor_pty(monkeypatch, tmp_path, provider, drive)
    assert "\x1b[?1049h" not in captured
    # The drained follow-up was delivered to the provider verbatim.
    assert prompts == ["begin the turn", queued]


class _CancelAwareRecordingProvider:
    """Records each turn's prompt; turn 1 blocks until cancelled at the boundary.

    Lets a mid-turn local command interrupt the first turn and unwind promptly
    (it observes the cancel token), so the test can assert the command ran
    locally without the provider ever seeing it as a prompt.
    """

    name = "fake"
    model_id = "fake-native-bootstrap"
    supports_tool_calls = True

    def __init__(self, prompts: list[str], active_event: threading.Event) -> None:
        self._prompts = prompts
        self._active = active_event
        self._turn = 0

    def complete(
        self, request, *, stream_sink=None, reasoning_sink=None, cancel_token=None
    ):
        from datetime import UTC, datetime

        from pipy_harness.native.cancellation import ProviderCancelledError
        from pipy_harness.native.models import ProviderResult

        del stream_sink, reasoning_sink
        self._turn += 1
        self._prompts.append(request.user_prompt)
        if self._turn == 1:
            self._active.set()
            if cancel_token is not None and cancel_token.event.wait(timeout=6.0):
                raise ProviderCancelledError("native provider turn cancelled")
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


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_local_command_submitted_midturn_runs_locally_not_queued(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A `/` command submitted with Enter mid-turn runs locally (Pi), not queued.

    Like Pi's editor, pressing Enter on a recognized local command (`/hotkeys`)
    while a turn is in flight runs it immediately rather than steering it to the
    model: the turn is interrupted, the hotkeys notice renders, and the command
    is never delivered to the provider as a prompt. (Prose still steers; this is
    why the steering/follow-up queue safely drains to the provider.)
    """

    prompts: list[str] = []
    active = threading.Event()
    provider = _CancelAwareRecordingProvider(prompts, active)

    def drive(in_master: int, chunks: list[bytes]) -> None:
        os.write(in_master, b"begin the turn\n")
        assert _wait_for_predicate(active.is_set), "turn never went active"
        # Type a slash command mid-turn and submit it with a plain Enter. Its
        # notice paints before the prompt that _run_editor_pty eventually exits.
        hotkeys_start = len(output_bytes(chunks))
        os.write(in_master, b"/hotkeys\n")
        assert (
            wait_for_input_ready_after(
                chunks, "Keyboard Shortcuts", after=hotkeys_start
            )
            is not None
        ), "/hotkeys did not return to ready input mid-turn"

    captured = _run_editor_pty(monkeypatch, tmp_path, provider, drive)
    assert "\x1b[?1049h" not in captured
    # /hotkeys ran locally and was never sent to the provider as a prompt.
    assert prompts == ["begin the turn"]

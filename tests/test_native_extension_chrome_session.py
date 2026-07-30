"""Session-level tests for slice-B extension chrome wiring.

`test_chrome_calls_do_not_leak_to_archive` proves a captured-stream run (no
TTY, so no live driver) never persists chrome content into the on-disk
session archive. `test_pty_session_renders_then_reload_clears_chrome` boots a
real PTY-backed session and exercises the LIVE driver end to end: an extension
*command* sets a widget + title through `ctx.ui`, the region renders, then
`/reload` clears it. `test_pty_session_start_hook_renders_chrome_live` proves
that a `session_start` lifecycle hook now also carries the live `ui_driver`, so
its chrome renders with no command needed (slice B lifecycle wiring).
"""

from __future__ import annotations

import io
import os
import pty
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, cast

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.terminal_screen import parse_ansi_screen
from pipy_harness.native.tool_loop_session import (
    NativeToolReplSession,
    production_tool_registry,
)
from pipy_harness.native.tui import ToolLoopTerminalUi


# Sets a widget + title on session_start. In a captured-stream run there is no
# live ui_driver, so this never reaches the screen/archive (the no-leak case).
# Over a real PTY the live driver is wired into lifecycle dispatch, so the same
# hook renders the widget (the session_start render case).
_EXT = """
def activate(api):
    @api.on("session_start")
    def _s(event, ctx):
        ctx.ui.set_widget("demo", ["DEMO_WIDGET"])
        ctx.ui.set_title("demo-title")
"""

# A custom command is the lifecycle path that carries the live ui_driver, so
# its chrome reaches the real TUI. Used by the PTY render+reload-clear test.
_EXT_CMD = """
def activate(api):
    def _demo(ctx, args):
        ctx.ui.set_widget("demo", ["DEMO_WIDGET"], placement="above_editor")
        ctx.ui.set_title("demo-title")

    api.register_command("demo", "set demo chrome", _demo)
"""


class _Provider:
    name = "stub"
    model_id = "m"

    @property
    def supports_tool_calls(self):
        return True

    def complete(self, request: ProviderRequest, **_k) -> ProviderResult:
        now = datetime(2026, 6, 21, tzinfo=UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="ok",
            tool_calls=(),
        )


def test_chrome_calls_do_not_leak_to_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("PIPY_NATIVE_SESSIONS_ROOT", str(tmp_path / "sessions"))
    ws = tmp_path / "work"
    (ws / ".pipy" / "extensions").mkdir(parents=True)
    (ws / ".pipy" / "extensions" / "chrome-demo.py").write_text(_EXT, encoding="utf-8")

    session = NativeToolReplSession(
        provider=_Provider(), tool_registry=production_tool_registry(), tool_budget=3
    )
    result = session.run(
        workspace_root=ws,
        input_stream=io.StringIO("hi\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    assert result.status is HarnessStatus.SUCCEEDED
    blob = ""
    sroot = tmp_path / "sessions"
    if sroot.exists():
        for p in sroot.rglob("*"):
            if p.is_file():
                blob += p.read_text(encoding="utf-8", errors="replace")
    assert "DEMO_WIDGET" not in blob
    assert "demo-title" not in blob


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


def _wait_until_absent(
    collected: list[bytes],
    needle: str,
    *,
    columns: int,
    rows: int,
    timeout: float = 8.0,
) -> bool:
    """Wait until ``needle`` is absent from the *current* rendered frame.

    The raw byte stream is append-only, so an old paint keeps the needle in
    history forever; we must look at the latest screen state instead.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = parse_ansi_screen(
            b"".join(collected).decode("utf-8", errors="replace"),
            columns=columns,
            rows=rows,
        )
        if needle not in "\n".join(snap.viewport):
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_session_renders_then_reload_clears_chrome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A PTY-backed session builds a real terminal_ui, so the live driver +
    # region rendering + reload-clear are exercised. An extension *command*
    # carries the live ui_driver (the session_start path does not), so the
    # command is what drives the chrome.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("PIPY_NATIVE_SESSIONS_ROOT", str(tmp_path / "sessions"))

    ext_dir = tmp_path / ".pipy" / "extensions"
    ext_dir.mkdir(parents=True)
    ext_file = ext_dir / "chrome-demo.py"
    ext_file.write_text(_EXT_CMD, encoding="utf-8")

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
    session = NativeToolReplSession(provider=_Provider(), tool_registry={})
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kw: ui,
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
        assert _wait_for(err_chunks, "escape interrupt"), "startup never painted"

        # Run the extension command: it sets the widget via the live driver.
        os.write(in_master, b"/demo\n")
        assert _wait_for(err_chunks, "DEMO_WIDGET"), "widget never painted"

        # Remove the extension, then /reload: chrome must be cleared. The
        # reloaded extension generation has no session_start hook because the
        # file is gone, so nothing re-sets the widget -> it must vanish.
        ext_file.unlink()
        os.write(in_master, b"/reload\n")
        assert _wait_for(err_chunks, "reloaded settings"), "reload never finished"
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and ui.extension_widgets_above:
            time.sleep(0.02)
        assert ui.extension_widgets_above == {}
        # Delayed acceptance intentionally lets the old live frame become host
        # scrollback when /reload is submitted; only the post-reload live region
        # is replaceable. The accepted sink must not repaint the removed widget.
        snapshot = parse_ansi_screen(
            b"".join(err_chunks).decode("utf-8", errors="replace"),
            columns=100,
            rows=40,
        )
        rows = list(snapshot.viewport)
        reload_row = next(i for i, row in enumerate(rows) if "reloaded settings" in row)
        assert all("DEMO_WIDGET" not in row for row in rows[reload_row:])

        os.write(in_master, b"\x03")  # ctrl-c exits the prompt
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


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_invalid_reload_flags_retain_old_widget_title_and_listener(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("PIPY_NATIVE_SESSIONS_ROOT", str(tmp_path / "sessions"))

    ext_dir = tmp_path / ".pipy" / "extensions"
    ext_dir.mkdir(parents=True)
    ext_file = ext_dir / "chrome-flags.py"
    callback_marker = tmp_path / "listener_calls.txt"
    ext_file.write_text(
        "from pathlib import Path\n"
        "from pipy_harness.extensions import ExtensionFlag\n"
        "def activate(api):\n"
        f"    callback_marker = Path({str(callback_marker)!r})\n"
        "    api.register_flag(ExtensionFlag('keep', 'boolean'))\n"
        "    def listener(key):\n"
        "        if key == 'x':\n"
        "            with callback_marker.open('a', encoding='utf-8') as fh:\n"
        "                fh.write(key)\n"
        "            return {'data': 'z'}\n"
        "        return None\n"
        "    def autocomplete(base):\n"
        "        return base\n"
        "    def editor(*_args):\n"
        "        return None\n"
        "    @api.on('session_start')\n"
        "    def _start(event, ctx):\n"
        "        ctx.ui.set_widget('old', ['OLD_FLAG_WIDGET'])\n"
        "        ctx.ui.set_title('OLD_FLAG_TITLE')\n"
        "        ctx.ui.on_terminal_input(listener)\n"
        "        ctx.ui.add_autocomplete_provider(autocomplete)\n"
        "        ctx.ui.set_editor_component(editor)\n",
        encoding="utf-8",
    )

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
    session = NativeToolReplSession(
        provider=_Provider(),
        tool_registry={},
        resource_options=RuntimeResourceOptions(extension_flag_tokens=("--keep",)),
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kw: ui,
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
        assert _wait_for(err_chunks, "OLD_FLAG_WIDGET"), (
            "startup session_start chrome never painted"
        )
        listeners_before = tuple(ui._chrome.terminal_input_listeners.values())
        providers_before = tuple(ui._autocomplete_provider_factories)
        editor_before = ui.get_editor_component()
        assert len(listeners_before) == len(providers_before) == 1
        assert editor_before is not None

        ext_file.write_text(
            "def activate(api):\n"
            "    api.register_command('new', 'candidate without keep flag', lambda ctx, args: None)\n",
            encoding="utf-8",
        )
        for reload_index in range(2):
            before_bytes = len(b"".join(err_chunks))
            os.write(in_master, b"/reload\n")
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                rendered = b"".join(err_chunks)
                if (
                    len(rendered) > before_bytes
                    and rendered.count(b"keeping the previous extensions")
                    >= reload_index + 1
                ):
                    break
                time.sleep(0.02)
            else:
                pytest.fail("invalid candidate flag was not reported")

            assert tuple(ui._chrome.terminal_input_listeners.values()) == (
                listeners_before
            )
            assert tuple(ui._autocomplete_provider_factories) == providers_before
            assert ui.get_editor_component() is editor_before
            assert ui.extension_title == "OLD_FLAG_TITLE"
            assert "old" in ui.extension_widgets_above
            assert ui._apply_extension_terminal_input_listeners("x") == "z"
            assert callback_marker.read_text(encoding="utf-8") == "x" * (
                reload_index + 1
            )
            assert not _wait_until_absent(
                err_chunks, "OLD_FLAG_WIDGET", columns=100, rows=40, timeout=0.3
            )

        os.write(in_master, b"\x03")
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


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_reload_session_start_hook_restores_chrome_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("PIPY_NATIVE_SESSIONS_ROOT", str(tmp_path / "sessions"))

    ext_dir = tmp_path / ".pipy" / "extensions"
    ext_dir.mkdir(parents=True)
    ext_file = ext_dir / "chrome-reload.py"
    ext_file.write_text(
        "from pathlib import Path\n"
        "def activate(api):\n"
        "    marker = Path(__file__).with_name('reload_marker.txt')\n"
        "    generation = marker.read_text(encoding='utf-8') if marker.exists() else 'initial'\n"
        "    @api.on('session_start')\n"
        "    def _s(event, ctx):\n"
        "        ctx.ui.set_widget('demo', ['RELOAD_WIDGET_' + (event.reason or '') + '_' + generation])\n"
        "    def _flip(ctx, args):\n"
        "        marker.write_text('reloaded', encoding='utf-8')\n"
        "        ctx.ui.notify('reload-widget-flipped')\n"
        "    api.register_command('flip-reload-widget', 'flip reload widget', _flip)\n",
        encoding="utf-8",
    )

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
    session = NativeToolReplSession(provider=_Provider(), tool_registry={})
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kw: ui,
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
        assert _wait_for(err_chunks, "RELOAD_WIDGET_startup_initial"), (
            "startup session_start widget never painted"
        )
        os.write(in_master, b"/flip-reload-widget\n")
        assert _wait_for(err_chunks, "reload-widget-flipped"), (
            "extension command did not finish before reload"
        )
        os.write(in_master, b"/reload\n")
        assert _wait_for(err_chunks, "RELOAD_WIDGET_reload_reloaded"), (
            "reload session_start widget never repainted with the reloaded generation"
        )

        os.write(in_master, b"\x03")
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


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_session_start_hook_renders_chrome_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A PTY-backed session builds a real terminal_ui. A `session_start`
    # lifecycle hook now carries the live ui_driver, so the widget it sets
    # renders with NO command issued -- proving the lifecycle gap is closed.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("PIPY_NATIVE_SESSIONS_ROOT", str(tmp_path / "sessions"))

    ext_dir = tmp_path / ".pipy" / "extensions"
    ext_dir.mkdir(parents=True)
    ext_file = ext_dir / "chrome-demo.py"
    ext_file.write_text(_EXT, encoding="utf-8")

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
    session = NativeToolReplSession(provider=_Provider(), tool_registry={})
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kw: ui,
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
        assert _wait_for(err_chunks, "escape interrupt"), "startup never painted"
        # No command issued: the session_start hook alone must render the widget.
        assert _wait_for(err_chunks, "DEMO_WIDGET"), (
            "session_start widget never painted -- live ui_driver not wired "
            "into lifecycle dispatch"
        )

        os.write(in_master, b"\x03")  # ctrl-c exits the prompt
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

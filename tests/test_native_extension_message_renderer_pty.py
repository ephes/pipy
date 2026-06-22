"""Real-PTY color test for rich extension message renderers (slice C).

Boots a real PTY-backed `NativeToolReplSession` with an extension that
registers a 2-arg component message renderer and a `/mkcard` command calling
`ctx.append_entry`. On a real terminal the live `ui_driver` runs the registered
renderer, commits the component under the SGR-preserving `custom_message_custom`
TUI line-kind, and paints it. This proves end to end that:

* the styled body sentinel renders with VISIBLE color (SGR escapes present);
* NO forced `[card]` label is injected (judgment 2: the component owns its box).

It is the live-only complement to the unit-level dispatch gate
`scripts/parity_checks/extension_message_renderer_conformance.py`.
"""

from __future__ import annotations

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
from pipy_harness.native.tool_loop_session import NativeToolReplSession
from pipy_harness.native.tui import ToolLoopTerminalUi

# A 2-arg `(data, ctx)` renderer REQUIRES a MessageRenderContext, so it takes
# the rich styled path: it themes a known body sentinel via ctx.theme.fg. The
# `/mkcard` command appends a custom entry whose registered renderer runs
# synchronously through the live ui_driver.
_EXT = '''
from pipy_harness.extensions import lines_component


def activate(api):
    def _render_card(data, ctx):
        body = (
            ctx.theme.fg("accent", "PTYBODY")
            if ctx.theme
            else "PTYBODY"
        )
        return lines_component([body])

    api.register_message_renderer("card", _render_card)

    def _mkcard(ctx, args):
        ctx.append_entry("card", {"k": "v"})

    api.register_command("mkcard", "append a styled card entry", _mkcard)
'''


class _Provider:
    name = "stub"
    model_id = "m"

    @property
    def supports_tool_calls(self) -> bool:
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
def test_rich_message_renderer_color_visible_over_pty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Color is enabled only when the stream is a real TTY (the PTY slave is),
    # NO_COLOR is unset, and TERM is not "dumb" -- mirror the chrome-session
    # PTY harness. PIPY_CONFIG_HOME is isolated so a persisted theme cannot
    # pollute the rendered colors.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "40")
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("PIPY_NATIVE_SESSIONS_ROOT", str(tmp_path / "sessions"))

    ext_dir = tmp_path / ".pipy" / "extensions"
    ext_dir.mkdir(parents=True)
    (ext_dir / "card-demo.py").write_text(_EXT, encoding="utf-8")

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

        # Dispatch the extension command: it calls ctx.append_entry("card", ...),
        # which runs the registered 2-arg renderer through the live ui_driver.
        os.write(in_master, b"/mkcard\n")
        assert _wait_for(err_chunks, "PTYBODY"), "styled card body never painted"

        frame = b"".join(err_chunks).decode("utf-8", "replace")
        # Color/SGR is visible on the styled path on a real terminal.
        assert "\x1b[" in frame, "expected SGR color escapes in the styled card"
        # Prove the COLOR sits on the body itself, not just unrelated chrome:
        # an SGR escape immediately precedes the body sentinel and an SGR reset
        # follows it (theme.fg wraps the text in \x1b[..m...\x1b[0m).
        body_at = frame.index("PTYBODY")
        before = frame[:body_at]
        after = frame[body_at + len("PTYBODY"):]
        # An SGR escape (\x1b[..m) sits immediately before the body with no other
        # text between the escape and the sentinel: the color is ON the body.
        last_esc = before.rfind("\x1b[")
        assert last_esc != -1 and before.endswith("m"), (
            "styled body sentinel is not preceded by an SGR escape"
        )
        between = before[last_esc + 2 : -1]
        assert all(c.isdigit() or c in ";:" for c in between), (
            "an SGR escape does not sit immediately before the body sentinel"
        )
        # ...and an SGR reset closes the body right after it.
        assert "\x1b[0m" in after[:8], "styled body sentinel has no trailing SGR reset"
        # The component owns its box: no forced [card] label (judgment 2).
        assert "[card]" not in frame, "styled renderer must not inject a [label]"

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

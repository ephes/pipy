# tests/test_native_extension_tool_renderer_pty.py
import io
import os
import pty
import threading
import time
from pathlib import Path
from typing import TextIO, cast

import pytest

from pipy_harness.extensions import (
    ExtensionTool,
    ToolRenderContext,
    ToolRenderTheme,
    ToolResult,
    lines_component,
)
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.tool_loop_session import _TuiToolLoopRenderer
from pipy_harness.native.tui import ToolLoopTerminalUi


def _render_result(ctx: ToolRenderContext):
    theme = cast(ToolRenderTheme, ctx.theme)
    details = ctx.details or {}
    return lines_component([theme.fg("success", f"KV-OK:{details['k']}")])


def _spawn_drainer(fd: int):
    chunks: list[bytes] = []

    def drain():
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            chunks.append(chunk)

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    return chunks


def _wait_for(chunks, needle: str, timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    enc = needle.encode("utf-8")
    while time.monotonic() < deadline:
        if enc in b"".join(chunks):
            return True
        time.sleep(0.02)
    return False


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_custom_tool_result_renders_colored(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.delenv("NO_COLOR", raising=False)
    err_master, err_slave = pty.openpty()
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    chunks = _spawn_drainer(err_master)
    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, io.StringIO()),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    tool = ExtensionTool(
        name="kv", description="d", input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="x", details={"k": "v"}),
        render_result=_render_result,
    )
    renderer = _TuiToolLoopRenderer(
        ui=ui, tool_renderers={"kv": tool},
        render_details_sink={"c": {"k": "v"}},
    )
    renderer.render_tool_call(
        ProviderToolCall(provider_correlation_id="c", tool_name="kv",
                         arguments_json="{}")
    )
    renderer.render_tool_result(output_text="x", is_error=False)
    try:
        assert _wait_for(chunks, "KV-OK:v"), "custom tool row never rendered"
        captured = b"".join(chunks).decode("utf-8", "replace")
        assert "\x1b[" in captured, "expected SGR color in the custom row"
    finally:
        terminal.close()
        os.close(err_master)

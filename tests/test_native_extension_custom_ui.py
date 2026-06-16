"""Slice C: custom interactive UI for extension command handlers.

`ctx.ui.custom(factory)` lets a command handler take over the terminal with a
custom component (its own `render(width)->lines` / `handle_input(key)`), used by
the ported `answer` extension for its Q&A overlay. This file covers the
non-PTY paths: the terminal renders an open custom overlay's lines, and the
mode-aware `ExtensionUi.custom` wiring delegates to the live driver (and is a
deterministic no-op without one). The full raw-mode loop is covered by the PTY
test.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TextIO, cast

from pipy_harness.native.extension_runtime import (
    CustomComponent,
    ExtensionUi,
    _CollectingUi,
)
from pipy_harness.native.tui import ToolLoopTerminalUi


class _TtyBuffer:
    def __init__(self) -> None:
        self._buffer = io.StringIO()

    def write(self, text: str) -> int:
        return self._buffer.write(text)

    def flush(self) -> None:
        self._buffer.flush()

    def isatty(self) -> bool:
        return True

    def getvalue(self) -> str:
        return self._buffer.getvalue()


class _ScriptedComponent:
    """A minimal custom component: renders a marker, finishes on Enter."""

    def __init__(self, done) -> None:
        self._done = done
        self.keys: list[str] = []

    def render(self, width: int) -> list[str]:
        return ["CUSTOM-OVERLAY-LINE", f"width={width}"]

    def handle_input(self, key: str) -> None:
        self.keys.append(key)
        if key == "enter":
            self._done("submitted")
        elif key == "esc":
            self._done(None)


def _ui(tmp_path: Path) -> ToolLoopTerminalUi:
    return ToolLoopTerminalUi(
        input_stream=cast(TextIO, io.StringIO()),
        terminal_stream=cast(TextIO, _TtyBuffer()),
        cwd=tmp_path,
    )


def test_open_custom_overlay_renders_component_lines(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui._custom_component = _ScriptedComponent(lambda _v=None: None)
    ui.custom_overlay_open = True
    frame = "\n".join(ui.render_lines())
    assert "CUSTOM-OVERLAY-LINE" in frame


def test_collecting_ui_custom_delegates_to_driver() -> None:
    captured: dict[str, object] = {}

    def driver(factory):
        # Drive the factory like the real overlay would, feeding one Enter.
        result_box: list[object] = []
        component = factory(lambda v=None: result_box.append(v))
        component.handle_input("enter")
        captured["component"] = component
        return result_box[0] if result_box else None

    ui = _CollectingUi(has_ui=True, custom_driver=driver)
    result = ui.custom(lambda done: _ScriptedComponent(done))
    assert result == "submitted"
    component = captured["component"]
    assert isinstance(component, _ScriptedComponent)
    assert component.keys == ["enter"]


def test_extension_ui_and_component_protocols_are_runtime_checkable() -> None:
    # `ExtensionUi` and `CustomComponent` must stay runtime-checkable so
    # `isinstance(...)` keeps working for callers and tests.
    ui = _CollectingUi(has_ui=True)
    assert isinstance(ui, ExtensionUi)
    assert isinstance(_ScriptedComponent(lambda _v=None: None), CustomComponent)


def test_collecting_ui_custom_is_noop_without_driver_or_ui() -> None:
    # No driver wired -> deterministic None (non-interactive / headless).
    ui = _CollectingUi(has_ui=True)
    assert ui.custom(lambda done: _ScriptedComponent(done)) is None

    # UI driver present but has_ui False -> still None (no interactive takeover).
    def driver(factory):  # pragma: no cover - must not be called
        raise AssertionError("driver must not run without a UI")

    ui2 = _CollectingUi(has_ui=False, custom_driver=driver)
    assert ui2.custom(lambda done: _ScriptedComponent(done)) is None

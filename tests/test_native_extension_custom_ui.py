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
import subprocess
from pathlib import Path
from typing import TextIO, cast

from pipy_harness.native.extension_runtime import (
    CustomComponent,
    ExtensionUi,
    RegisteredCommand,
    _CollectingUi,
    dispatch_extension_command,
)
from pipy_harness.native.tui import ToolLoopTerminalUi
from pipy_harness.native.tui import (
    _ExtensionConfirmComponent,
    _ExtensionEditorComponent,
    _ExtensionInputComponent,
    _ExtensionSelectComponent,
)


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


def test_extension_external_editor_guards_exact_mode_transition_calls(
    monkeypatch, tmp_path: Path
) -> None:
    ui = _ui(tmp_path)
    mode_calls: list[str] = []

    def raise_restore(_self: ToolLoopTerminalUi) -> None:
        mode_calls.append("restore")
        raise OSError("restore unavailable")

    def raise_enter(_self: ToolLoopTerminalUi) -> None:
        mode_calls.append("enter")
        raise OSError("raw mode unavailable")

    def fake_run(argv, **_kwargs):
        path = Path(argv[-1])
        assert path.read_text(encoding="utf-8") == "seed"
        path.write_text("edited\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(ToolLoopTerminalUi, "_restore_terminal_mode", raise_restore)
    monkeypatch.setattr(ToolLoopTerminalUi, "_enter_raw_mode", raise_enter)
    monkeypatch.setattr("pipy_harness.native.tui.subprocess.run", fake_run)

    assert ui._run_extension_external_editor("fake-editor", "seed") == "edited"
    assert mode_calls == ["restore", "enter"]


def test_open_custom_overlay_renders_component_lines(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui._custom_component = _ScriptedComponent(lambda _v=None: None)
    ui.custom_overlay_open = True
    frame = "\n".join(ui.render_lines())
    assert "CUSTOM-OVERLAY-LINE" in frame


def test_extension_select_component_navigation_and_cancel() -> None:
    result: list[object] = []
    component = _ExtensionSelectComponent(
        "Pick\x1b[31m",
        ["one", "two\rbad", "three"],
        lambda value=None: result.append(value),
    )

    rendered = "\n".join(component.render(80))
    assert "\x1b" not in rendered
    assert "\r" not in rendered
    assert "two bad" in rendered
    component.handle_input("down")
    assert "-> two bad" in "\n".join(component.render(80))
    component.handle_input("down")
    component.handle_input("down")
    assert "-> one" in "\n".join(component.render(80))
    component.handle_input("enter")
    assert result == ["one"]

    cancelled: list[object] = []
    component = _ExtensionSelectComponent(
        "Pick", ["one"], lambda value=None: cancelled.append(value)
    )
    component.handle_input("esc")
    assert cancelled == [None]


def test_extension_select_component_windows_around_highlight() -> None:
    component = _ExtensionSelectComponent(
        "Pick",
        [f"option-{index:02d}" for index in range(20)],
        lambda value=None: None,
    )

    for _ in range(13):
        component.handle_input("down")
    rendered = "\n".join(component.render(80))

    assert "-> option-13" in rendered
    assert "option-00" not in rendered
    assert "(14/20)" in rendered


def test_extension_confirm_component_keeps_body_and_choices_visible() -> None:
    result: list[object] = []
    component = _ExtensionConfirmComponent(
        "Delete",
        "This operation removes a generated file.\n"
        "Review the path carefully before continuing because this message is long.",
        lambda value=None: result.append(value),
    )

    rendered = "\n".join(component.render(44))

    assert "Delete" in rendered
    assert "This operation removes" in rendered
    assert "Review the path carefully" in rendered
    assert "-> Yes" in rendered
    assert "   No" in rendered

    component.handle_input("down")
    component.handle_input("enter")
    assert result == ["No"]


def test_extension_input_component_edits_sanitizes_display_and_submits_raw() -> None:
    result: list[object] = []
    component = _ExtensionInputComponent(
        "Name\x1b[31m",
        "place\rholder",
        lambda value=None: result.append(value),
    )

    assert "Name [31m" in "\n".join(component.render(80))
    assert "place holder" in "\n".join(component.render(80))

    component.handle_input("a")
    component.handle_input("\x1b")
    component.handle_input("b")
    component.handle_input("backspace")
    component.handle_input("enter")

    assert result == ["a"]


def test_extension_editor_component_edits_multiline_and_submits_raw() -> None:
    result: list[object] = []
    component = _ExtensionEditorComponent(
        "Draft\x1b[31m",
        "alpha\nbeta",
        lambda value=None: result.append(value),
    )

    rendered = "\n".join(component.render(80))
    assert "Draft [31m" in rendered
    assert "> alpha" not in rendered
    assert "> beta" in rendered

    component.handle_input("home")
    component.handle_input("shift-enter")
    component.handle_input("x")
    component.handle_input("up")
    component.handle_input("z")
    component.handle_input("down")
    component.handle_input("end")
    component.handle_input("alt-enter")
    component.handle_input("y")
    component.handle_input("left")
    component.handle_input("backspace")
    component.handle_input("enter")

    assert result == ["alpha\nz\nxbetay"]


def test_extension_editor_component_cancels() -> None:
    result: list[object] = []
    component = _ExtensionEditorComponent(
        "Draft", "prefill", lambda value=None: result.append(value)
    )

    component.handle_input("esc")

    assert result == [None]


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


class _FakeUiDriver:
    def __init__(self) -> None:
        self.status: list[tuple[str, str | None]] = []
        self.working_messages: list[str | None] = []
        self.working_visible: list[bool] = []
        self.chrome: list[tuple[str, object]] = []

    def select(self, title: str, options) -> str | None:
        return f"{title}:{options[1]}"

    def input(self, title: str, placeholder: str | None = None) -> str | None:
        return f"{title}:{placeholder}"

    def editor(self, title: str, prefill: str | None = None) -> str | None:
        return f"{title}:{prefill}"

    def confirm(self, title: str, message: str) -> bool:
        return title == "confirm" and bool(message)

    def set_status(self, key: str, text: str | None) -> None:
        self.status.append((key, text))

    def set_working_message(self, message: str | None = None) -> None:
        self.working_messages.append(message)

    def set_working_visible(self, visible: bool) -> None:
        self.working_visible.append(visible)

    def set_widget(self, key: str, content: object, placement: str) -> None:
        self.chrome.append(("set_widget", (key, content, placement)))

    def set_header(self, factory: object | None) -> None:
        self.chrome.append(("set_header", factory))

    def set_footer(self, factory: object | None) -> None:
        self.chrome.append(("set_footer", factory))

    def set_title(self, title: str) -> None:
        self.chrome.append(("set_title", title))

    def set_working_indicator(self, frames, interval_ms: int | None) -> None:
        self.chrome.append(("set_working_indicator", (frames, interval_ms)))


def test_collecting_ui_dialogs_and_status_delegate_to_driver() -> None:
    driver = _FakeUiDriver()
    ui = _CollectingUi(has_ui=True, ui_driver=driver)

    assert ui.select("pick", ["a", "b"]) == "pick:b"
    assert ui.input("name", "placeholder") == "name:placeholder"
    assert ui.editor("draft", "prefill") == "draft:prefill"
    assert ui.confirm("confirm", "continue?") is True
    ui.set_status("build status", "green")
    ui.set_status("build status", None)
    ui.set_working_message("Thinking")
    ui.set_working_visible(False)

    assert driver.status == [("build-status", "green"), ("build-status", None)]
    assert driver.working_messages == ["Thinking"]
    assert driver.working_visible == [False]
    assert ui.statuses == {}
    assert ui.working_message == "Thinking"
    assert ui.working_visible is False


def test_collecting_ui_dialogs_are_deterministic_without_ui() -> None:
    def fail_driver(*_args, **_kwargs):  # pragma: no cover - must not be called
        raise AssertionError("driver must not run without UI")

    driver = _FakeUiDriver()
    driver.select = fail_driver  # type: ignore[method-assign]
    ui = _CollectingUi(has_ui=False, ui_driver=driver)

    assert ui.select("pick", ["a"]) is None
    assert ui.input("name") is None
    assert ui.editor("draft", "prefill") is None
    assert ui.confirm("confirm", "continue?") is False
    ui.set_status("build", "green")

    assert driver.status == []
    assert ui.statuses == {"build": "green"}


def test_extension_command_ui_methods_reach_driver(tmp_path: Path) -> None:
    driver = _FakeUiDriver()
    seen: dict[str, object] = {}

    def handler(ctx, _args):
        seen["selected"] = ctx.ui.select("pick", ["a", "b"])
        seen["answer"] = ctx.ui.input("name", "default")
        seen["draft"] = ctx.ui.editor("draft", "prefill")
        seen["confirmed"] = ctx.ui.confirm("confirm", "continue?")
        ctx.ui.set_status("task", "running")
        ctx.ui.set_working_message("Custom work")
        ctx.ui.set_working_visible(False)

    command = RegisteredCommand("probe", "probe ui", handler, "ext")
    dispatch = dispatch_extension_command(
        "/probe",
        {"probe": command},
        cwd=str(tmp_path),
        has_ui=True,
        ui_driver=driver,
    )

    assert dispatch is not None
    assert dispatch.ran
    assert seen == {
        "selected": "pick:b",
        "answer": "name:default",
        "draft": "draft:prefill",
        "confirmed": True,
    }
    assert driver.status == [("task", "running")]
    assert driver.working_messages == ["Custom work"]
    assert driver.working_visible == [False]

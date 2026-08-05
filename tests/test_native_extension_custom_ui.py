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
import termios
import tty
from collections.abc import Callable
from pathlib import Path
from typing import TextIO, cast

import pytest

from pipy_harness.native.extension_runtime import (
    CustomComponent,
    ExtensionUi,
    ExtensionUiDriver,
    RegisteredCommand,
    _CollectingUi,
    dispatch_extension_command,
)
from pipy_harness.native.terminal_driver import TerminalDriver
from pipy_harness.native.tui import ToolLoopTerminalUi
from pipy_harness.native.ui.components.custom_editor import (
    ExtensionEditorComponent,
)
from pipy_harness.native.ui.components.extension_prompts import (
    ExtensionConfirmComponent,
    ExtensionExternalEditor,
    ExtensionInputComponent,
    ExtensionSelectComponent,
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


class _InputBuffer:
    def fileno(self) -> int:
        return 0

    def isatty(self) -> bool:
        return True


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


def _external_editor(ui: ToolLoopTerminalUi) -> ExtensionExternalEditor:
    return ExtensionExternalEditor(
        external_io_suspension=ui.external_io_suspension,
        terminal_write=ui._driver.write,
        input_stream=ui.input_stream,
        terminal_stream=ui.terminal_stream,
    )


def test_extension_external_editor_uses_temporary_suspend_resume_contract(
    monkeypatch, tmp_path: Path
) -> None:
    ui = _ui(tmp_path)
    mode_calls: list[str] = []

    def suspend(_self: TerminalDriver) -> None:
        mode_calls.append("suspend")

    def resume(_self: TerminalDriver) -> bool:
        mode_calls.append("resume")
        return True

    def fake_run(argv, **_kwargs):
        path = Path(argv[-1])
        assert path.read_text(encoding="utf-8") == "seed"
        path.write_text("edited\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(TerminalDriver, "suspend_terminal_mode", suspend)
    monkeypatch.setattr(TerminalDriver, "resume_terminal_mode", resume)
    monkeypatch.setattr(
        "pipy_harness.native.ui.components.extension_prompts.subprocess.run", fake_run
    )

    assert _external_editor(ui).run("fake-editor", "seed") == "edited"
    assert mode_calls == ["suspend", "resume"]


@pytest.mark.parametrize(
    "error_type", (OSError, termios.error), ids=("os-error", "termios-error")
)
def test_extension_external_editor_does_not_launch_after_failed_suspend(
    monkeypatch, tmp_path: Path, error_type: type[Exception]
) -> None:
    ui = _ui(tmp_path)
    ui._live_height = 4
    ui._live_input_row = 2
    launched = False

    def fail_suspend(_self: TerminalDriver) -> None:
        raise error_type("cooked handoff failed")

    def fake_run(_argv, **_kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("editor must not launch without cooked mode")

    monkeypatch.setattr(TerminalDriver, "suspend_terminal_mode", fail_suspend)
    monkeypatch.setattr(
        "pipy_harness.native.ui.components.extension_prompts.subprocess.run", fake_run
    )

    assert _external_editor(ui).run("fake-editor", "seed") is None
    assert launched is False
    assert ui._live_height == 4
    assert ui._live_input_row == 2


@pytest.mark.parametrize(
    "error_type", (OSError, termios.error), ids=("os-error", "termios-error")
)
def test_extension_external_editor_keeps_completed_edit_after_failed_resume(
    monkeypatch, tmp_path: Path, error_type: type[Exception]
) -> None:
    ui = _ui(tmp_path)
    mode_calls: list[str] = []

    def suspend(_self: TerminalDriver) -> None:
        mode_calls.append("suspend")

    def fail_resume(_self: TerminalDriver) -> bool:
        mode_calls.append("resume")
        raise error_type("raw resumption failed")

    def fake_run(argv, **_kwargs):
        path = Path(argv[-1])
        path.write_text("completed edit\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(TerminalDriver, "suspend_terminal_mode", suspend)
    monkeypatch.setattr(TerminalDriver, "resume_terminal_mode", fail_resume)
    monkeypatch.setattr(
        "pipy_harness.native.ui.components.extension_prompts.subprocess.run", fake_run
    )

    assert _external_editor(ui).run("fake-editor", "seed") == "completed edit"
    assert mode_calls == ["suspend", "resume"]


def test_custom_component_failed_nested_acquisition_preserves_outer_raw_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    restore_calls: list[tuple[int, int, object]] = []
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: "saved")
    monkeypatch.setattr(tty, "setraw", lambda _fd: None)
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, attrs: restore_calls.append((fd, when, attrs)),
    )
    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, _InputBuffer()),
        terminal_stream=cast(TextIO, _TtyBuffer()),
        cwd=tmp_path,
    )
    monkeypatch.setattr(ToolLoopTerminalUi, "paint", lambda _self: None)
    ui._driver.enter_raw_mode()
    ui._driver.suspend_terminal_mode()

    with pytest.raises(RuntimeError, match="while terminal I/O is suspended"):
        ui.run_custom_component(lambda done: _ScriptedComponent(done))

    assert ui._driver._raw_mode_depth == 1
    assert ui._driver._terminal_mode_suspend_depth == 1
    assert restore_calls == [(0, termios.TCSADRAIN, "saved")]

    assert ui._driver.resume_terminal_mode() is True
    ui._driver.restore_terminal_mode()
    assert restore_calls == [
        (0, termios.TCSADRAIN, "saved"),
        (0, termios.TCSADRAIN, "saved"),
    ]


def test_custom_component_failed_physical_acquisition_has_no_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    restore_calls: list[tuple[int, int, object]] = []
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: "saved")

    def fail_raw(_fd: int) -> None:
        raise termios.error("raw transition failed")

    monkeypatch.setattr(tty, "setraw", fail_raw)
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, attrs: restore_calls.append((fd, when, attrs)),
    )
    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, _InputBuffer()),
        terminal_stream=cast(TextIO, _TtyBuffer()),
        cwd=tmp_path,
    )
    monkeypatch.setattr(ToolLoopTerminalUi, "paint", lambda _self: None)

    with pytest.raises(termios.error, match="raw transition failed"):
        ui.run_custom_component(lambda done: _ScriptedComponent(done))

    assert ui._driver._raw_mode_depth == 0
    assert ui._driver._old_termios is None
    assert restore_calls == [(0, termios.TCSADRAIN, "saved")]


def test_open_custom_overlay_renders_component_lines(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui._overlays.custom_component = _ScriptedComponent(lambda _v=None: None)
    ui.custom_overlay_open = True
    frame = "\n".join(ui.render_lines())
    assert "CUSTOM-OVERLAY-LINE" in frame


def test_extension_select_component_navigation_and_cancel() -> None:
    result: list[object] = []
    component = ExtensionSelectComponent(
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
    component = ExtensionSelectComponent(
        "Pick", ["one"], lambda value=None: cancelled.append(value)
    )
    component.handle_input("esc")
    assert cancelled == [None]


def test_extension_select_component_windows_around_highlight() -> None:
    component = ExtensionSelectComponent(
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
    component = ExtensionConfirmComponent(
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
    component = ExtensionInputComponent(
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
    component = ExtensionEditorComponent(
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
    component = ExtensionEditorComponent(
        "Draft", "prefill", lambda value=None: result.append(value)
    )

    component.handle_input("esc")

    assert result == [None]


def test_extension_editor_component_uses_resolved_external_editor_key() -> None:
    result: list[object] = []
    calls: list[str] = []

    def external_editor(text: str) -> str:
        calls.append(text)
        return "edited"

    component = ExtensionEditorComponent(
        "Draft",
        "prefill",
        lambda value=None: result.append(value),
        external_editor,
        ("ctrl+x",),
    )

    rendered = "\n".join(component.render(80))
    assert "ctrl-x external edit" in rendered

    component.handle_input("ctrl-g")
    assert component.text == "prefill"
    assert calls == []

    component.handle_input("ctrl-x")
    assert component.text == "edited"
    assert calls == ["prefill"]


def test_extension_editor_component_hides_external_editor_hint_when_unbound() -> None:
    calls: list[str] = []

    def external_editor(text: str) -> str:
        calls.append(text)
        return "edited"

    component = ExtensionEditorComponent(
        "Draft",
        "prefill",
        lambda value=None: None,
        external_editor,
        (),
    )

    rendered = "\n".join(component.render(80))
    assert "external edit" not in rendered

    component.handle_input("ctrl-g")
    assert component.text == "prefill"
    assert calls == []


def test_collecting_ui_custom_delegates_to_driver() -> None:
    captured: dict[str, object] = {}

    def driver(factory, options=None):
        # Drive the factory like the real overlay would, feeding one Enter.
        result_box: list[object] = []
        component = factory(lambda v=None: result_box.append(v))
        component.handle_input("enter")
        captured["component"] = component
        captured["options"] = options
        return result_box[0] if result_box else None

    ui = _CollectingUi(has_ui=True, custom_driver=driver)
    result = ui.custom(
        lambda done: _ScriptedComponent(done),
        {"overlay": True, "overlayOptions": {"width": 42}},
    )
    assert result == "submitted"
    component = captured["component"]
    assert isinstance(component, _ScriptedComponent)
    assert component.keys == ["enter"]
    assert captured["options"] == {"overlay": True, "overlayOptions": {"width": 42}}


def test_tui_custom_component_options_width_handle_and_dispose(
    monkeypatch, tmp_path: Path
) -> None:
    ui = _ui(tmp_path)
    ui.input_stream = cast(TextIO, _InputBuffer())
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda _self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda _self: None)

    keys = iter(["enter"])
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda _self, _fd: next(keys)
    )

    seen: dict[str, object] = {}

    class Component:
        def __init__(self, done) -> None:
            self._done = done
            self.disposed = False

        def render(self, width: int) -> list[str]:
            seen["render_width"] = width
            return [f"width={width}"]

        def handle_input(self, key: str) -> None:
            self._done(f"key:{key}")

        def dispose(self) -> None:
            self.disposed = True
            seen["disposed"] = True

    def on_handle(handle) -> None:
        seen["handle"] = handle
        handle.update()
        handle.requestRender()

    result = ui.run_custom_component(
        lambda done: Component(done),
        {
            "overlay": True,
            "overlayOptions": {"width": 23},
            "onHandle": on_handle,
        },
    )

    assert result == "key:enter"
    assert seen["render_width"] == 23
    assert seen["disposed"] is True
    handle = seen["handle"]
    for method in (
        "hide",
        "setHidden",
        "isHidden",
        "focus",
        "unfocus",
        "isFocused",
        "requestRender",
        "request_render",
    ):
        assert hasattr(handle, method)


def test_tui_custom_component_callable_snake_case_options(
    monkeypatch, tmp_path: Path
) -> None:
    ui = _ui(tmp_path)
    ui.input_stream = cast(TextIO, _InputBuffer())
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda _self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda _self: None)

    keys = iter(["enter"])
    monkeypatch.setattr(
        ToolLoopTerminalUi, "_read_key_polling_resize", lambda _self, _fd: next(keys)
    )

    widths: list[int] = []

    class Component:
        def __init__(self, done) -> None:
            self._done = done

        def render(self, width: int) -> list[str]:
            widths.append(width)
            return [f"width={width}"]

        def handle_input(self, _key: str) -> None:
            self._done("done")

    result = ui.run_custom_component(
        lambda done: Component(done),
        {"overlay_options": lambda: {"width": 31.8}},
    )

    assert result == "done"
    assert widths == [31]


def test_tui_custom_component_factory_can_finish_repeated_runs_before_return(
    monkeypatch, tmp_path: Path
) -> None:
    ui = _ui(tmp_path)
    ui.input_stream = cast(TextIO, _InputBuffer())
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda _self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda _self: None)

    def fail_read(_self, _fd):
        raise AssertionError("factory completion should finish before reading input")

    monkeypatch.setattr(ToolLoopTerminalUi, "_read_key_polling_resize", fail_read)

    class Component:
        def render(self, _width: int) -> list[str]:
            return ["done"]

        def handle_input(self, _key: str) -> None:
            raise AssertionError("completed component must not receive input")

    for expected in ("first", "second"):

        def factory(done, result=expected):
            done(result)
            done("ignored")
            return Component()

        assert ui.run_custom_component(factory) == expected


def test_tui_custom_component_handle_hide_cancels(monkeypatch, tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui.input_stream = cast(TextIO, _InputBuffer())
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda _self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda _self: None)

    def fail_read(_self, _fd):
        raise AssertionError("hide should finish before reading input")

    monkeypatch.setattr(ToolLoopTerminalUi, "_read_key_polling_resize", fail_read)

    result = ui.run_custom_component(
        lambda done: _ScriptedComponent(done),
        {"on_handle": lambda handle: handle.hide()},
    )

    assert result is None


def test_tui_custom_component_handle_visibility_and_focus(
    monkeypatch, tmp_path: Path
) -> None:
    ui = _ui(tmp_path)
    ui.input_stream = cast(TextIO, _InputBuffer())
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda _self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda _self: None)

    keys = iter(["hidden-key", "shown-key", "unfocused-key", "focused-key"])

    def read_key(_self, _fd):
        key = next(keys)
        handle = handle_box.get("handle")
        if key == "shown-key" and handle is not None:
            handle.setHidden(False)  # type: ignore[attr-defined]
        elif key == "focused-key" and handle is not None:
            handle.focus()  # type: ignore[attr-defined]
        return key

    monkeypatch.setattr(ToolLoopTerminalUi, "_read_key_polling_resize", read_key)

    events: list[tuple[str, object]] = []
    handle_box: dict[str, object] = {}

    class Component:
        def __init__(self, done) -> None:
            self._done = done

        def render(self, width: int) -> list[str]:
            events.append(("render", width))
            return ["custom-visible"]

        def handle_input(self, key: str) -> None:
            events.append(("key", key))
            handle = handle_box["handle"]
            if key == "shown-key":
                handle.unfocus({"target": object()})  # type: ignore[attr-defined]
            elif key == "focused-key":
                self._done("done")

    def on_handle(handle) -> None:
        handle_box["handle"] = handle
        assert handle.isFocused() is True
        assert handle.isHidden() is False
        handle.setHidden(True)
        assert handle.isHidden() is True

    result = ui.run_custom_component(
        lambda done: Component(done), {"onHandle": on_handle}
    )

    assert result == "done"
    assert ("key", "hidden-key") not in events
    assert ("key", "unfocused-key") not in events
    assert ("key", "shown-key") in events
    assert ("key", "focused-key") in events
    # The unfocused overlay remains visible: paint after unfocus still renders.
    assert [event[0] for event in events].count("render") >= 3


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
    def driver(factory, options=None):  # pragma: no cover - must not be called
        raise AssertionError("driver must not run without a UI")

    ui2 = _CollectingUi(has_ui=False, custom_driver=driver)
    assert ui2.custom(lambda done: _ScriptedComponent(done)) is None


class _FakeUiDriver:
    def __init__(self) -> None:
        self.status: list[tuple[str, str | None]] = []
        self.working_messages: list[str | None] = []
        self.working_visible: list[bool] = []
        self.chrome: list[tuple[str, object]] = []
        self.terminal_input_handlers: list[object] = []

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

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        self.chrome.append(("set_hidden_thinking_label", label))

    def add_terminal_input_listener(self, handler: object):
        self.terminal_input_handlers.append(handler)

        def dispose():
            if handler in self.terminal_input_handlers:
                self.terminal_input_handlers.remove(handler)

        return dispose

    def get_tools_expanded(self) -> bool:
        return False

    def set_tools_expanded(self, expanded: bool) -> None:
        self.chrome.append(("set_tools_expanded", expanded))

    def get_editor_text(self) -> str:
        return ""

    def set_editor_text(self, text: str) -> None:
        self.chrome.append(("set_editor_text", text))

    def paste_to_editor(self, text: str) -> None:
        self.chrome.append(("paste_to_editor", text))

    def apply_theme(self, name: str) -> tuple[bool, str | None]:
        self.chrome.append(("apply_theme", name))
        return True, None

    def add_autocomplete_provider(self, factory: object) -> None:
        self.chrome.append(("add_autocomplete_provider", factory))

    def set_editor_component(self, factory: object | None) -> None:
        self.chrome.append(("set_editor_component", factory))

    def get_editor_component(self) -> object | None:
        return None


def test_fake_ui_driver_is_complete_extension_ui_driver() -> None:
    """Guard the custom-UI structural fake as `ExtensionUiDriver` grows."""
    assert isinstance(_FakeUiDriver(), ExtensionUiDriver)


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


def test_collecting_ui_terminal_input_live_and_headless_disposer() -> None:
    driver = _FakeUiDriver()
    ui = _CollectingUi(has_ui=True, ui_driver=driver)
    seen: list[str] = []

    def record_key(key: str) -> None:
        seen.append(key)

    dispose = ui.on_terminal_input(record_key)
    ui.onTerminalInput(lambda key: {"consume": key == "esc"})
    assert len(driver.terminal_input_handlers) == 2

    handler = cast("Callable[[str], object]", driver.terminal_input_handlers[0])
    handler("a")
    assert seen == ["a"]
    dispose()
    dispose()
    assert len(driver.terminal_input_handlers) == 1

    headless = _CollectingUi(has_ui=False, ui_driver=driver)
    noop = headless.onTerminalInput(lambda key: seen.append(key))
    noop()
    assert len(driver.terminal_input_handlers) == 1


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

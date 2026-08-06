"""Ownership and boundary gates for the extracted terminal screen."""

from __future__ import annotations

import ast
import io
import threading
from pathlib import Path

import pytest

from pipy_harness.native.tui import TerminalUi

REPO_ROOT = Path(__file__).resolve().parents[1]
SCREEN_PATH = REPO_ROOT / "src/pipy_harness/native/ui/screen.py"
TUI_PATH = REPO_ROOT / "src/pipy_harness/native/tui.py"
MODAL_PATH = REPO_ROOT / "src/pipy_harness/native/ui/modal_driver.py"
PAINT_LOCK_PATH = REPO_ROOT / "src/pipy_harness/native/ui/paint_lock.py"
MODAL_METHODS = (
    "run_model_selector",
    "run_scoped_models_selector",
    "run_settings_dialog",
    "run_tree_selector",
    "run_custom_component",
    "run_extension_select",
    "run_extension_input",
    "run_extension_editor",
    "run_extension_confirm",
    "run_session_picker",
)
RETIRED_FACADE_METHODS = {
    "autocomplete",
    "custom_overlay_open",
    *MODAL_METHODS,
    "clear_extension_chrome",
    "reconcile_extension_chrome",
    "close",
    "external_io_suspension",
    "set_footer_text",
    "tools_expanded",
    "thinking_hidden",
    "submit_user_message",
    "begin_assistant_turn",
    "set_working",
    "append_assistant",
    "settle_assistant",
    "append_reasoning",
    "set_thinking_hidden",
    "set_tools_expanded",
    "add_notice",
    "custom_entry_render_target",
    "create_tool_loop_renderer",
    "add_tool_call",
    "append_tool_output",
    "add_tool_result",
    "rerender_custom_messages",
    "_is_bash_mode",
}
OLD_SCREEN_MEMBERS = {
    "_force_full_redraw",
    "render_lines",
    "_frame_lines",
    "request_extension_chrome_render",
    "paint",
    "_paint_locked",
    "_live_region_lines",
    "_frame_snapshot",
    "_standard_frame_inputs",
    "_active_overlay_region_lines",
    "_read_driver_key",
    "_read_key_polling_resize",
    "_poll_resize_repaint",
    "_repaint_after_resize",
}


def _class(source: str, name: str) -> ast.ClassDef:
    return next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _method(class_node: ast.ClassDef, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_screen_has_no_session_or_facade_backedge() -> None:
    source = SCREEN_PATH.read_text(encoding="utf-8")
    assert "native.tui" not in source
    assert "native.repl" not in source
    retired_module = "native.tool_" + "loop_session"
    assert retired_module not in source
    assert "native.coding.session" not in source


def test_paint_lock_requires_the_one_explicit_production_rlock() -> None:
    lock_class = _class(PAINT_LOCK_PATH.read_text(encoding="utf-8"), "PaintLock")
    init = _method(lock_class, "__init__")
    assert [argument.arg for argument in init.args.args] == ["self", "lock"]
    assert init.args.defaults == []

    calls: list[tuple[Path, ast.Call]] = []
    for path in (REPO_ROOT / "src/pipy_harness").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "PaintLock":
                    calls.append((path, node))
    assert [path.relative_to(REPO_ROOT) for path, _node in calls] == [
        Path("src/pipy_harness/native/ui/screen.py")
    ]
    call = calls[0][1]
    assert len(call.args) == 1
    supplied = call.args[0]
    assert isinstance(supplied, ast.Call)
    assert isinstance(supplied.func, ast.Attribute)
    assert isinstance(supplied.func.value, ast.Name)
    assert (supplied.func.value.id, supplied.func.attr) == ("threading", "RLock")


def test_facade_screen_state_and_private_implementation_are_gone() -> None:
    source = TUI_PATH.read_text(encoding="utf-8")
    class_node = _class(source, "TerminalUi")
    definitions = {
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    }
    assert not definitions & OLD_SCREEN_MEMBERS
    assert not {
        "_closed",
        "_painted_block_count",
        "_live_height",
        "_live_input_row",
        "_paint_lock",
        "_painting",
        "_paint_requested_during_paint",
        "_last_painted_size",
    } & {
        node.target.id
        for node in class_node.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }


def test_ten_modal_methods_use_the_one_screen_drive_loop() -> None:
    modal_class = _class(MODAL_PATH.read_text(encoding="utf-8"), "TerminalModalDriver")
    definitions = {
        node.name for node in modal_class.body if isinstance(node, ast.FunctionDef)
    }
    assert set(MODAL_METHODS) <= definitions
    for name in MODAL_METHODS:
        method = _method(modal_class, name)
        assert not any(
            isinstance(node, (ast.For, ast.While)) for node in ast.walk(method)
        )

    screen_class = _class(SCREEN_PATH.read_text(encoding="utf-8"), "Screen")
    loops = [
        node
        for method_name in ("drive", "_drive_keys")
        for node in ast.walk(_method(screen_class, method_name))
        if isinstance(node, ast.While)
    ]
    assert len(loops) == 1


def test_retired_terminal_facade_has_no_definition_or_production_call_site() -> None:
    tui_class = _class(TUI_PATH.read_text(encoding="utf-8"), "TerminalUi")
    definitions = {
        node.name for node in tui_class.body if isinstance(node, ast.FunctionDef)
    }
    assert definitions.isdisjoint(RETIRED_FACADE_METHODS)

    offenders: list[tuple[Path, int, str]] = []
    for path in (REPO_ROOT / "src/pipy_harness").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if node.func.attr not in RETIRED_FACADE_METHODS:
                continue
            receiver = node.func.value
            if isinstance(receiver, ast.Name) and receiver.id in {
                "terminal_ui",
                "live_ui",
                "ui",
            }:
                offenders.append(
                    (path.relative_to(REPO_ROOT), node.lineno, node.func.attr)
                )
            if (
                isinstance(receiver, ast.Attribute)
                and isinstance(receiver.value, ast.Name)
                and receiver.value.id == "self"
                and receiver.attr == "terminal_ui"
            ):
                offenders.append(
                    (path.relative_to(REPO_ROOT), node.lineno, node.func.attr)
                )
    assert offenders == []


def test_deferred_input_loops_stay_in_facade_and_use_screen_services() -> None:
    tui_class = _class(TUI_PATH.read_text(encoding="utf-8"), "TerminalUi")
    for name in ("read_line", "wait_for_active_turn_interrupt"):
        method = _method(tui_class, name)
        assert any(isinstance(node, ast.While) for node in ast.walk(method))
        assignments = {
            ast.unparse(node.targets[0]): ast.unparse(node.value)
            for node in method.body
            if isinstance(node, ast.Assign) and len(node.targets) == 1
        }
        assert assignments["components"] == "self.components"
        assert assignments["screen"] == "components.screen"
        assert not {
            "_screen",
            "_driver",
            "input_editor",
            "pending_messages",
            "clipboard_images",
            "_autocomplete",
            "_custom_editor",
        } & {
            node.attr
            for node in ast.walk(method)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        }


def test_contributor_order_and_shared_render_inputs_are_exact(tmp_path: Path) -> None:
    ui = TerminalUi(
        input_stream=io.StringIO(), terminal_stream=io.StringIO(), cwd=tmp_path
    )
    components = ui.components
    assert tuple(item.name for item in components.screen.contributors.ordinary) == (
        "popup",
        "pending",
        "status",
        "header",
        "above_editor",
        "below_editor",
        "footer",
        "custom_editor",
    )
    assert tuple(item.name for item in components.screen.contributors.overlays) == (
        "custom",
        "settings",
        "project_trust",
        "session_picker",
        "tree",
        "scoped_models",
        "model",
    )
    lock = components.screen.paint_lock
    assert components.transcript._paint_lock is lock  # noqa: SLF001
    assert components.custom_editor._paint_lock is lock  # noqa: SLF001
    assert (  # noqa: SLF001
        components.transcript._render_inputs is components.screen.render_inputs
    )
    assert (  # noqa: SLF001
        components.chrome.component._render_inputs is components.screen.render_inputs
    )
    sources = components.screen._sources  # noqa: SLF001
    assert sources is not None
    assert sources.transcript is components.transcript
    assert sources.input_editor is components.input_editor
    assert getattr(sources.footer_lines, "__self__", None) is components.chrome.footer


def test_paint_lock_has_no_zero_argument_constructor() -> None:
    from pipy_harness.native.ui.paint_lock import PaintLock

    with pytest.raises(TypeError):
        PaintLock()  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="requires an RLock"):
        PaintLock(threading.Lock())  # type: ignore[arg-type]

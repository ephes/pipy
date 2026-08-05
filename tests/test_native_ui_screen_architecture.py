"""Ownership and boundary gates for the extracted terminal screen."""

from __future__ import annotations

import ast
import io
import threading
from pathlib import Path

import pytest

from pipy_harness.native.tui import ToolLoopTerminalUi

REPO_ROOT = Path(__file__).resolve().parents[1]
SCREEN_PATH = REPO_ROOT / "src/pipy_harness/native/ui/screen.py"
TUI_PATH = REPO_ROOT / "src/pipy_harness/native/tui.py"
PAINT_LOCK_PATH = REPO_ROOT / "src/pipy_harness/native/ui/paint_lock.py"
MODAL_METHODS = (
    "run_model_selector",
    "run_scoped_models_selector",
    "run_settings_dialog",
    "run_tree_selector",
    "run_custom_component",
    "run_session_picker",
)
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
    assert "native.tool_loop_session" not in source


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
    class_node = _class(source, "ToolLoopTerminalUi")
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


def test_six_modal_methods_delegate_to_one_drive_loop() -> None:
    tui_class = _class(TUI_PATH.read_text(encoding="utf-8"), "ToolLoopTerminalUi")
    for name in MODAL_METHODS:
        method = _method(tui_class, name)
        assert not any(
            isinstance(node, (ast.For, ast.While)) for node in ast.walk(method)
        )
        calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]
        assert any(
            isinstance(call.func, ast.Attribute) and call.func.attr == "drive"
            for call in calls
        )

    screen_class = _class(SCREEN_PATH.read_text(encoding="utf-8"), "Screen")
    loops = [
        node
        for method_name in ("drive", "_drive_keys")
        for node in ast.walk(_method(screen_class, method_name))
        if isinstance(node, ast.While)
    ]
    assert len(loops) == 1


def test_deferred_input_loops_stay_in_facade_and_use_screen_services() -> None:
    tui_class = _class(TUI_PATH.read_text(encoding="utf-8"), "ToolLoopTerminalUi")
    for name in ("read_line", "wait_for_active_turn_interrupt"):
        method = _method(tui_class, name)
        assert any(isinstance(node, ast.While) for node in ast.walk(method))
        assert any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr == "_screen"
            for node in ast.walk(method)
        )


def test_contributor_order_and_shared_render_inputs_are_exact(tmp_path: Path) -> None:
    ui = ToolLoopTerminalUi(
        input_stream=io.StringIO(), terminal_stream=io.StringIO(), cwd=tmp_path
    )
    assert tuple(item.name for item in ui._screen.contributors.ordinary) == (
        "popup",
        "pending",
        "status",
        "header",
        "above_editor",
        "below_editor",
        "footer",
        "custom_editor",
    )
    assert tuple(item.name for item in ui._screen.contributors.overlays) == (
        "custom",
        "settings",
        "project_trust",
        "session_picker",
        "tree",
        "scoped_models",
        "model",
    )
    lock = ui._screen.paint_lock
    assert ui._transcript._paint_lock is lock  # noqa: SLF001
    assert ui._custom_editor._paint_lock is lock  # noqa: SLF001
    assert ui._transcript._render_inputs is ui._screen.render_inputs  # noqa: SLF001
    assert ui._chrome.component._render_inputs is ui._screen.render_inputs  # noqa: SLF001


def test_paint_lock_has_no_zero_argument_constructor() -> None:
    from pipy_harness.native.ui.paint_lock import PaintLock

    with pytest.raises(TypeError):
        PaintLock()  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="requires an RLock"):
        PaintLock(threading.Lock())  # type: ignore[arg-type]

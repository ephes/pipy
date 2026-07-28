"""Focused contracts for the read-only architecture metrics helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "architecture_metrics.py"
_spec = importlib.util.spec_from_file_location("architecture_metrics", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
architecture_metrics = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = architecture_metrics
_spec.loader.exec_module(architecture_metrics)


EXPECTED_TOOL_LOOP_TERMINAL_UI_FIELDS = (
    "_clipboard_image_count",
    "_closed",
    "_custom_component",
    "_custom_component_render_width",
    "_custom_done",
    "_custom_editor_action",
    "_custom_editor_active",
    "_custom_editor_changed_text",
    "_custom_editor_component",
    "_custom_editor_exit_requested",
    "_custom_editor_factory",
    "_custom_editor_submitted",
    "_custom_overlay_focused",
    "_custom_overlay_hidden",
    "_custom_result",
    "_deferred_reasoning",
    "_driver",
    "_editor",
    "_extension_footer_branch",
    "_extension_footer_factory",
    "_extension_terminal_input_last_replaced",
    "_extension_terminal_input_listeners",
    "_extension_terminal_input_next_id",
    "_footer_branch_callback_next_id",
    "_footer_branch_callbacks",
    "_footer_branch_check_interval",
    "_footer_branch_last_check",
    "_footer_branch_rebuild_active_ids",
    "_footer_branch_rebuild_fire_ids",
    "_footer_branch_rebuild_index",
    "_footer_branch_rebuild_new_slots",
    "_footer_branch_rebuild_slots",
    "_footer_branch_slots",
    "_history_blocks",
    "_last_painted_size",
    "_live_height",
    "_live_input_row",
    "_paint_lock",
    "_paint_requested_during_paint",
    "_painted_block_count",
    "_painting",
    "_session_picker_all",
    "_session_picker_now",
    "_session_picker_project",
    "assistant_text",
    "autocomplete_max_visible",
    "available_provider_count",
    "clipboard_image_read",
    "clipboard_temp_dir",
    "command_descriptions",
    "command_names",
    "custom_overlay_open",
    "cwd",
    "extension_footer",
    "extension_header",
    "extension_indicator_frames",
    "extension_indicator_interval_ms",
    "extension_shortcut_keys",
    "extension_status",
    "extension_title",
    "extension_widgets_above",
    "extension_widgets_below",
    "extension_working_message",
    "extension_working_visible",
    "footer_lines",
    "hidden_thinking_label",
    "include_workspace_defaults",
    "input_stream",
    "keybindings_manager",
    "model_selector_open",
    "model_selector_options",
    "model_selector_selection",
    "model_selector_title",
    "reasoning_text",
    "runtime_label",
    "scoped_models_checked",
    "scoped_models_open",
    "scoped_models_rows",
    "scoped_models_selection",
    "session_picker_current",
    "session_picker_input",
    "session_picker_mode",
    "session_picker_named_only",
    "session_picker_open",
    "session_picker_query",
    "session_picker_rows",
    "session_picker_scope",
    "session_picker_selection",
    "session_picker_show_path",
    "session_picker_sort",
    "session_picker_status",
    "settings_dialog_open",
    "settings_dialog_rows",
    "settings_dialog_selection",
    "settings_dialog_title",
    "terminal_stream",
    "thinking_hidden",
    "tool_output_text",
    "tools_expanded",
    "tree_selector_filter",
    "tree_selector_open",
    "tree_selector_rows",
    "tree_selector_selection",
    "working_text",
)


def test_class_state_fields_deduplicates_sources_and_excludes_classvars() -> None:
    source = """
from typing import ClassVar

class Synthetic:
    annotated: int
    repeated: str
    ignored: ClassVar[int] = 1
    qualified: typing.ClassVar[str]

    def update(self) -> None:
        self.stored = 1
        self.repeated = "again"
        self.stored = 2
        del self.deleted
        del self.repeated

        def nested() -> None:
            self.nested_store = 3

    async def update_async(self) -> None:
        self.async_store = 4
"""

    assert architecture_metrics.class_state_fields(source, "Synthetic") == (
        "annotated",
        "async_store",
        "deleted",
        "repeated",
        "stored",
    )


def test_class_state_fields_requires_one_top_level_class() -> None:
    try:
        architecture_metrics.class_state_fields("class Other: pass\n", "Missing")
    except ValueError as exc:
        assert "exactly one top-level class" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("missing class did not fail closed")


def test_product_tool_loop_terminal_ui_field_baseline_is_stable() -> None:
    source = (REPO_ROOT / "src/pipy_harness/native/tui.py").read_text(encoding="utf-8")

    fields = architecture_metrics.class_state_fields(source, "ToolLoopTerminalUi")

    assert fields == EXPECTED_TOOL_LOOP_TERMINAL_UI_FIELDS
    assert len(fields) == 104


def test_physical_lines_match_newline_delimited_baseline(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    path.write_bytes(b"one\ntwo\nunterminated")

    assert architecture_metrics._physical_lines(path) == 2

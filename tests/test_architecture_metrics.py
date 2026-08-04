"""Focused contracts for the read-only architecture metrics helper."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "architecture_metrics.py"
_spec = importlib.util.spec_from_file_location("architecture_metrics", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
architecture_metrics = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = architecture_metrics
_spec.loader.exec_module(architecture_metrics)


EXPECTED_TOOL_LOOP_TERMINAL_UI_FIELDS = (
    "_chrome",
    "_clipboard_image_count",
    "_closed",
    "_custom_editor_action",
    "_custom_editor_active",
    "_custom_editor_changed_text",
    "_custom_editor_component",
    "_custom_editor_exit_requested",
    "_custom_editor_factory",
    "_custom_editor_submitted",
    "_deferred_reasoning",
    "_driver",
    "_editor",
    "_history_blocks",
    "_last_painted_size",
    "_live_height",
    "_live_input_row",
    "_overlays",
    "_paint_lock",
    "_paint_requested_during_paint",
    "_painted_block_count",
    "_painting",
    "assistant_text",
    "autocomplete_max_visible",
    "available_provider_count",
    "clipboard_image_read",
    "clipboard_temp_dir",
    "command_descriptions",
    "command_names",
    "cwd",
    "extension_shortcut_keys",
    "footer_lines",
    "hidden_thinking_label",
    "include_workspace_defaults",
    "input_stream",
    "keybindings_manager",
    "reasoning_text",
    "runtime_label",
    "terminal_stream",
    "thinking_hidden",
    "tool_output_text",
    "tools_expanded",
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
    assert len(fields) == 43
    assert len(fields) <= 89  # Slice 12 ceiling: floor(128 * 0.70)


def test_class_size_spans_decorators_and_counts_direct_defs_once() -> None:
    source = """\
# a comment above the class keeps the span anchored to the decorator

@decorator_a
@decorator_b
class Synthetic:
    field: int

    @property
    def value(self) -> int:
        return 1

    @value.setter
    def value(self, new: int) -> None:
        self._value = new

        def nested() -> None:
            pass

    async def run(self) -> None:
        class Inner:
            def inner_method(self) -> None:
                pass
"""

    span, defs = architecture_metrics.class_size(source, "Synthetic")

    # Lines 3 (first decorator) through 22 (class end), not from `class` at 5.
    assert span == 20
    # Getter, setter (each decorated def counted once), and the async def;
    # `nested` and `Inner.inner_method` live in nested scopes and are excluded.
    assert defs == 3


# --- ToolLoopTerminalUi class ratchet ----------------------------------------
#
# §2d of the decomposition plan: of the 922 lines that left `tui.py`, the god
# class itself lost 31 — the file ratchet in
# tests/test_architecture_quality_gates.py cannot see the class regrowing while
# helper bands shrink around it. These bounds are the mass gate for the class.
# Lower them in any slice that shrinks the class; never raise one. A slice that
# needs a bound raised is a slice that put code back into the class.
_TUI_CLASS_SPAN_RATCHET = 4091
_TUI_CLASS_DEF_RATCHET = 284


def test_tool_loop_terminal_ui_class_ratchet_never_grows() -> None:
    source = (REPO_ROOT / "src/pipy_harness/native/tui.py").read_text(encoding="utf-8")

    span, defs = architecture_metrics.class_size(source, "ToolLoopTerminalUi")

    assert span <= _TUI_CLASS_SPAN_RATCHET, (
        f"ToolLoopTerminalUi grew to {span} ast-lines, above its ratchet of "
        f"{_TUI_CLASS_SPAN_RATCHET}. Move code out of the class rather than "
        "raising the bound."
    )
    assert defs <= _TUI_CLASS_DEF_RATCHET, (
        f"ToolLoopTerminalUi grew to {defs} defs, above its ratchet of "
        f"{_TUI_CLASS_DEF_RATCHET}. Move code out of the class rather than "
        "raising the bound."
    )


def test_physical_lines_match_newline_delimited_baseline(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    path.write_bytes(b"one\ntwo\nunterminated")

    assert architecture_metrics._physical_lines(path) == 2

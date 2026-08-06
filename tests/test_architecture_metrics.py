"""Focused contracts for the read-only architecture metrics helper."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "architecture_metrics.py"
_spec = importlib.util.spec_from_file_location("architecture_metrics", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
architecture_metrics = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = architecture_metrics
_spec.loader.exec_module(architecture_metrics)


EXPECTED_TERMINAL_UI_FIELDS = (
    "available_provider_count",
    "components",
    "cwd",
    "footer_lines",
    "include_workspace_defaults",
    "input_stream",
    "keybindings_manager",
    "runtime_label",
    "terminal_stream",
)


def test_class_state_fields_deduplicates_sources_and_excludes_classvars() -> None:
    source = """
from dataclasses import InitVar
from typing import ClassVar

class Synthetic:
    annotated: int
    constructor_only: InitVar[str]
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


def test_product_terminal_ui_field_baseline_is_stable() -> None:
    source = (REPO_ROOT / "src/pipy_harness/native/tui.py").read_text(encoding="utf-8")

    fields = architecture_metrics.class_state_fields(source, "TerminalUi")

    assert fields == EXPECTED_TERMINAL_UI_FIELDS
    assert len(fields) == 9
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


# --- TerminalUi class ratchet ------------------------------------------------
#
# §2d of the decomposition plan: of the 922 lines that left `tui.py`, the god
# class itself lost 31 — the file ratchet in
# tests/test_architecture_quality_gates.py cannot see the class regrowing while
# helper bands shrink around it. These bounds are the mass gate for the class.
# Lower them in any slice that shrinks the class; never raise one. A slice that
# needs a bound raised is a slice that put code back into the class.
_TUI_CLASS_SPAN_RATCHET = 230
_TUI_CLASS_DEF_RATCHET = 5


def test_terminal_ui_class_ratchet_never_grows() -> None:
    source = (REPO_ROOT / "src/pipy_harness/native/tui.py").read_text(encoding="utf-8")

    span, defs = architecture_metrics.class_size(source, "TerminalUi")

    assert span <= _TUI_CLASS_SPAN_RATCHET, (
        f"TerminalUi grew to {span} ast-lines, above its ratchet of "
        f"{_TUI_CLASS_SPAN_RATCHET}. Move code out of the class rather than "
        "raising the bound."
    )
    assert defs <= _TUI_CLASS_DEF_RATCHET, (
        f"TerminalUi grew to {defs} defs, above its ratchet of "
        f"{_TUI_CLASS_DEF_RATCHET}. Move code out of the class rather than "
        "raising the bound."
    )


def test_physical_lines_match_newline_delimited_baseline(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    path.write_bytes(b"one\ntwo\nunterminated")

    assert architecture_metrics._physical_lines(path) == 2


def test_metrics_publish_the_coding_session_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = tmp_path / "src/pipy_harness/native"
    (native / "coding").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (native / "coding/session.py").write_text("# facade\n", encoding="utf-8")
    (native / "tui.py").write_text("class TerminalUi:\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(
        architecture_metrics,
        "_ruff_c901_counts",
        lambda _repo_root: {"repository": 0, "src": 0},
    )

    metrics = architecture_metrics.collect_metrics(tmp_path)

    assert metrics["physical_lines"]["coding_session"] == 1
    assert "tool_" + "loop_session" not in metrics["physical_lines"]
    assert metrics["terminal_ui_size"] == {"ast_line_span": 2, "defs": 0}
    assert metrics["terminal_ui_state_fields"] == {"count": 0, "names": []}
    assert "tool_loop_" + "terminal_ui_size" not in metrics
    assert "tool_loop_" + "terminal_ui_state_fields" not in metrics

"""Terminal-independent contracts for immutable native frame composition."""

from __future__ import annotations

import gc
import io
import weakref
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import MappingProxyType
from typing import TextIO, cast

import pytest

from pipy_harness.native import frame_renderer
from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.extension_runtime import RegisteredMessageRenderer
from pipy_harness.native.frame_renderer import (
    ChromeSnapshot,
    FrameBlock,
    FrameLine,
    FrameSnapshot,
    InputSnapshot,
    PaintState,
    ResolvedCustomEditorLine,
    block_lines,
    build_paint_plan,
    input_lines,
    render_full_frame,
    render_live_region,
    style_line,
)
from pipy_harness.native.terminal_driver import TerminalDriver
from pipy_harness.native.tui import ToolLoopTerminalUi


def _snapshot(
    *,
    input_text: str = "x" * 25,
    cursor: int | None = None,
    overlay: tuple[FrameLine, ...] | None = None,
) -> FrameSnapshot:
    return FrameSnapshot(
        width=20,
        height=10,
        history=(FrameBlock("user", ("hello world",)),),
        assistant_text="",
        reasoning_text="",
        tool_output_text="",
        working_text="",
        thinking_hidden=False,
        hidden_thinking_label="Thinking...",
        tools_expanded=False,
        input=InputSnapshot(
            input_text,
            len(input_text) if cursor is None else cursor,
        ),
        popup=(),
        pending=(),
        chrome=ChromeSnapshot(
            footer=(FrameLine("cwd", "footer"), FrameLine("status", "footer"))
        ),
        overlay=overlay,
        cursor_visible=overlay is None,
    )


def test_pure_renderer_is_deterministic_and_does_not_mutate_snapshot() -> None:
    snapshot = _snapshot()
    before = repr(snapshot)

    first = render_full_frame(snapshot, pad=False)
    second = render_full_frame(snapshot, pad=False)
    first_plan = build_paint_plan(snapshot, PaintState(0, 0, 0), ChromeStyle(False))
    second_plan = build_paint_plan(snapshot, PaintState(0, 0, 0), ChromeStyle(False))

    assert first == second
    assert first_plan == second_plan
    assert repr(snapshot) == before
    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, "width", 40)
    line_with_meta = next(line for line in first if line.meta is not None)
    assert isinstance(line_with_meta.meta, MappingProxyType)
    source_meta = {"cursor_col": 7}
    detached_line = FrameLine("input", "input", source_meta)
    source_meta["cursor_col"] = 0
    assert detached_line.meta == {"cursor_col": 7}


def test_renderer_wraps_input_pins_footer_and_places_cursor_metadata() -> None:
    frame = render_full_frame(_snapshot(), pad=False)
    input_rows = [row for row in frame if row.kind == "input"]

    assert [row.text for row in input_rows] == ["x" * 19, "x" * 6]
    assert input_rows[0].meta is None
    assert input_rows[1].meta == {"cursor_col": 6}
    assert [row.text for row in frame[-2:]] == ["cwd", "status"]
    assert frame[frame.index(input_rows[0]) - 1].text == "─" * 20
    assert frame[frame.index(input_rows[-1]) + 1].text == "─" * 20


def test_renderer_clips_wraps_and_maps_styles_without_a_terminal() -> None:
    wrapped = block_lines(
        FrameBlock("working", ("⠋ " + "working " * 8,)),
        20,
    )
    styled = style_line(wrapped[0], ChromeStyle(enabled=True), 20)

    assert all(len(row.text) <= 20 for row in wrapped)
    assert styled.startswith("\x1b[38;5;244m ")
    assert "\x1b[38;5;109m⠋\x1b[0m" in styled


def test_paint_plan_commits_history_once_and_redraws_only_live_rows() -> None:
    snapshot = _snapshot(input_text="draft")
    first = build_paint_plan(snapshot, PaintState(0, 0, 0), ChromeStyle(False))
    second = build_paint_plan(
        snapshot,
        PaintState(
            first.painted_block_count,
            first.live_height,
            first.live_input_row,
        ),
        ChromeStyle(False),
    )

    assert sum("hello world" in row.text for row in first.committed_rows) == 1
    assert second.committed_rows == ()
    assert first.painted_block_count == second.painted_block_count == 1
    assert first.cursor_col == 5
    assert first.cursor_visible is True
    assert first.live_input_row < first.live_height


def test_overlay_replaces_input_and_keeps_hardware_cursor_hidden() -> None:
    snapshot = _snapshot(overlay=(FrameLine("selector", "selector_title"),))

    live = render_live_region(snapshot)
    plan = build_paint_plan(snapshot, PaintState(0, 0, 0), ChromeStyle(False))

    assert live == snapshot.overlay
    assert any("selector" in row.text for row in plan.live_rows)
    assert plan.cursor_visible is False


def test_empty_overlay_is_safe_with_and_without_new_history() -> None:
    snapshot = replace(_snapshot(overlay=()), cursor_visible=False)

    no_commit = build_paint_plan(
        snapshot,
        PaintState(len(snapshot.history), 0, 0),
        ChromeStyle(False),
    )
    with_commit = build_paint_plan(
        snapshot,
        PaintState(0, 0, 0),
        ChromeStyle(False),
    )

    assert no_commit.committed_rows == ()
    assert len(no_commit.live_rows) == 1
    assert with_commit.committed_rows
    assert with_commit.live_rows == ()
    assert no_commit.cursor_visible is with_commit.cursor_visible is False
    assert with_commit.live_input_row == -1
    assert with_commit.cursor_col == with_commit.cursor_lines_up == 0


def test_custom_editor_rows_keep_resolved_kind_cursor_and_tail_window() -> None:
    rows = (
        ResolvedCustomEditorLine("first", "input"),
        ResolvedCustomEditorLine("second", "custom-editor-row"),
        ResolvedCustomEditorLine("third", "input", {"cursor_col": 2}),
    )

    rendered = input_lines(InputSnapshot("ignored", 0, rows), width=3, max_rows=2)

    assert rendered == rows[-2:]
    assert rendered[0].kind == "custom-editor-row"
    assert rendered[1].meta == {"cursor_col": 2}
    assert rendered[1].text == "third"  # already-resolved rows are not re-clipped


@pytest.mark.parametrize("max_rows", [-4, 0])
@pytest.mark.parametrize("width", [0, 1])
def test_input_projection_restores_one_cursor_row_at_tiny_bounds(
    width: int, max_rows: int
) -> None:
    rows = input_lines(InputSnapshot("abcdef", 3), width, max_rows)

    assert len(rows) == 1
    assert rows[0].kind == "input"
    assert rows[0].meta == {"cursor_col": 0}
    assert len(rows[0].text) <= max(0, width)


def test_empty_custom_editor_projection_restores_one_cursor_row() -> None:
    rows = input_lines(InputSnapshot("ignored", 0, ()), width=0, max_rows=0)

    assert rows == (FrameLine(" ", "input", {"cursor_col": 0}),)


def test_full_frame_preserves_resolved_custom_editor_bytes_and_only_pads() -> None:
    sentinel = "resolved\r\x1b]unsafe\x07"
    snapshot = replace(
        _snapshot(input_text="ignored"),
        input=InputSnapshot(
            "ignored",
            0,
            (
                ResolvedCustomEditorLine(
                    sentinel, "custom-editor-row", {"cursor_col": 4}
                ),
            ),
        ),
    )

    unpadded = render_full_frame(snapshot, pad=False)
    padded = render_full_frame(snapshot, pad=True)
    unpadded_row = next(row for row in unpadded if row.kind == "custom-editor-row")
    padded_row = next(row for row in padded if row.kind == "custom-editor-row")

    assert unpadded_row.text == sentinel
    assert padded_row.text == sentinel + " " * (snapshot.width - len(sentinel))
    assert unpadded_row.meta == padded_row.meta == {"cursor_col": 4}


def test_history_overflow_retains_last_user_and_bounded_recent_output() -> None:
    snapshot = replace(
        _snapshot(input_text=""),
        height=16,
        history=(
            FrameBlock("normal", tuple(f"ctx{index}" for index in range(20))),
            FrameBlock("user", ("KEEP USER",)),
            FrameBlock("assistant", tuple(f"after{index}" for index in range(8))),
        ),
    )

    frame = render_full_frame(snapshot, pad=False)
    history = frame[: next(i for i, row in enumerate(frame) if row.kind == "separator")]

    assert [row.text for row in history] == [
        "ctx16",
        "ctx17",
        "ctx18",
        "ctx19",
        "",
        " KEEP USER",
        "",
        " after6",
        " after7",
    ]


def test_history_overflow_compacts_blank_tail_before_selection() -> None:
    snapshot = replace(
        _snapshot(input_text=""),
        height=16,
        history=(
            FrameBlock("normal", tuple(f"ctx{index}" for index in range(20))),
            FrameBlock("user", ("KEEP USER",)),
            FrameBlock("assistant", ("after0", "", "", "", "after4")),
        ),
    )

    frame = render_full_frame(snapshot, pad=False)
    history = frame[: next(i for i, row in enumerate(frame) if row.kind == "separator")]

    assert " KEEP USER" in [row.text for row in history]
    assert [row.text for row in history[-2:]] == [" after0", " after4"]
    assert sum(not row.text.strip() and row.kind == "normal" for row in history) == 0


def test_chrome_truncates_footer_and_prioritizes_marker_regions() -> None:
    footer_snapshot = replace(
        _snapshot(input_text=""),
        height=6,
        history=(),
        chrome=ChromeSnapshot(
            footer=tuple(FrameLine(f"F{index}", "footer") for index in range(8))
        ),
    )
    footer_frame = render_full_frame(footer_snapshot, pad=False)
    assert [row.text for row in footer_frame if row.kind == "footer"] == [
        "F0",
        "F1",
        "F2",
    ]

    marker_snapshot = replace(
        footer_snapshot,
        height=10,
        chrome=ChromeSnapshot(
            header=(FrameLine("H0"), FrameLine("H1")),
            above=(FrameLine("A0"), FrameLine("A1")),
            below=(FrameLine("B0"), FrameLine("B1")),
            footer=(FrameLine("F0", "footer"), FrameLine("F1", "footer")),
        ),
    )
    marker_frame = render_full_frame(marker_snapshot, pad=False)
    marker_text = [row.text for row in marker_frame]
    assert marker_text[1:5] == ["H0", "H1", "A0", "  … (chrome clipped)"]
    assert "A1" not in marker_text
    assert "B0" not in marker_text
    assert marker_frame[4].kind == "slash_menu_scroll"


@pytest.mark.parametrize("height", [0, 1, 2, 3])
@pytest.mark.parametrize("width", [0, 1])
def test_full_frame_tiny_geometry_is_bounded(width: int, height: int) -> None:
    snapshot = replace(_snapshot(input_text=""), width=width, height=height, history=())

    frame = render_full_frame(snapshot, pad=False)

    assert len(frame) == height
    assert all(len(row.text) <= width for row in frame)


class _TtyBuffer:
    def __init__(self) -> None:
        self.value = ""

    def write(self, text: str) -> int:
        self.value += text
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True


def _ui(tmp_path: Path) -> ToolLoopTerminalUi:
    return ToolLoopTerminalUi(
        input_stream=cast(TextIO, io.StringIO()),
        terminal_stream=cast(TextIO, _TtyBuffer()),
        cwd=tmp_path,
    )


class _RowsEditor:
    def __init__(self, rows: list[str]) -> None:
        self.rows = rows
        self.text = ""

    def render(self, width: int) -> list[str]:
        del width
        return self.rows

    def set_text(self, text: str) -> None:
        self.text = text

    def get_text(self) -> str:
        return self.text

    def handle_input(self, key: str) -> None:
        del key


def test_facade_custom_editor_snapshot_preserves_rows_cursor_and_window(
    tmp_path: Path,
) -> None:
    ui = _ui(tmp_path)
    component = _RowsEditor([f"row{index}" for index in range(10)])
    ui.set_editor_component(lambda _tui, _theme, _keys: component)

    snapshot = ui._frame_snapshot(width=60, height=12, include_session_picker=False)
    direct = render_live_region(snapshot)
    facade = ui._frame_lines(width=60, height=12, pad=False)
    snapshot_rows = snapshot.input.custom_rows

    assert snapshot_rows is not None
    assert [row.text for row in snapshot_rows] == [f"row{index}" for index in range(10)]
    assert [row.kind for row in snapshot_rows] == ["input"] * 10
    assert [row.meta for row in snapshot_rows] == [
        *(None for _ in range(9)),
        {"cursor_col": 4},
    ]
    expected = [f"row{index}" for index in range(2, 10)]
    assert [row.text for row in direct if row.kind == "input"] == expected
    assert [row.text for row in facade if row.kind == "input"] == expected
    assert [row.meta for row in direct if row.kind == "input"] == [
        *(None for _ in range(7)),
        {"cursor_col": 4},
    ]


def test_facade_full_frame_does_not_finish_custom_editor_rows_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = _ui(tmp_path)
    component = _RowsEditor(["ONCE\rRESOLVED"])
    ui.set_editor_component(lambda _tui, _theme, _keys: component)

    def non_idempotent_finish(text: str, width: int) -> str:
        del width
        return f"SECOND-PASS<{text}>"

    monkeypatch.setattr(frame_renderer, "clip_custom_text", non_idempotent_finish)

    detailed = ui._frame_lines(width=60, height=12, pad=False)
    captured = ui.render_lines(width=60, height=12, pad=False)

    assert [row.text for row in detailed if row.kind == "input"] == ["ONCE RESOLVED"]
    assert "ONCE RESOLVED" in captured
    assert not any("SECOND-PASS<ONCE RESOLVED>" in text for text in captured)


def test_facade_custom_editor_keeps_head_plain_control_policy_at_narrow_width(
    tmp_path: Path,
) -> None:
    ui = _ui(tmp_path)
    component = _RowsEditor(["\x1b[31mRED\x1b[0m", "A\rB\x1b]0;X\x07"])
    ui.set_editor_component(lambda _tui, _theme, _keys: component)

    snapshot = ui._frame_snapshot(width=6, height=6, include_session_picker=False)
    snapshot_rows = snapshot.input.custom_rows
    direct = render_live_region(snapshot)
    facade = ui._input_frame_lines(6, max_rows=2)

    assert snapshot_rows is not None
    assert [row.text for row in snapshot_rows] == [" [31m…", "A B ]…"]
    assert [row.text for row in direct if row.kind == "input"] == [
        " [31m…",
        "A B ]…",
    ]
    assert facade == list(snapshot_rows)
    assert all("\x1b" not in row.text for row in snapshot_rows)
    assert all("\r" not in row.text and "\x07" not in row.text for row in snapshot_rows)


def test_facade_input_wrapper_treats_zero_as_one_row(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui.input_text = "abcdef"
    ui.input_cursor = 3

    ordinary = ui._input_frame_lines(3, max_rows=0)
    component = _RowsEditor(["first", "second"])
    ui.set_editor_component(lambda _tui, _theme, _keys: component)
    custom = ui._input_frame_lines(6, max_rows=0)

    assert len(ordinary) == len(custom) == 1
    assert ordinary[0].meta == {"cursor_col": 1}
    assert custom == [ResolvedCustomEditorLine("second", "input", {"cursor_col": 5})]


def test_facade_publishes_detached_immutable_snapshot(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui.footer_lines = ("workspace", "status")
    ui.submit_user_message("snapshot message")
    snapshot = ui._frame_snapshot(width=60, height=12, include_session_picker=False)

    ui._history_blocks.clear()
    ui.input_text = "later mutation"
    rendered = "\n".join(row.text for row in render_full_frame(snapshot, pad=False))

    assert "snapshot message" in rendered
    assert "later mutation" not in rendered
    assert snapshot.history == (FrameBlock("user", ("snapshot message",)),)


def test_state_bearing_custom_history_snapshot_keeps_lines_not_callbacks(
    tmp_path: Path,
) -> None:
    ui = _ui(tmp_path)

    class Callback:
        def __call__(self, *_args: object, **_kwargs: object) -> object:
            return object()

    callback = Callback()
    callback_ref = weakref.ref(callback)
    registered = RegisteredMessageRenderer("card", callback, "test")
    ui.add_custom_entry_styled(
        ["\x1b[1mCURRENT\x1b[0m"],
        custom_type="card",
        renderers={"card": registered},
    )

    snapshot = ui._frame_snapshot(width=60, height=12, include_session_picker=False)
    ui._history_blocks.clear()
    del registered
    del callback
    gc.collect()

    rendered = render_full_frame(snapshot, pad=False)
    assert callback_ref() is None
    assert snapshot.history == (
        FrameBlock("custom_message_custom", ("\x1b[1mCURRENT\x1b[0m",)),
    )
    assert any("CURRENT" in row.text for row in rendered)


def test_overlay_snapshot_does_not_execute_hidden_extension_chrome(
    tmp_path: Path,
) -> None:
    ui = _ui(tmp_path)
    renders: list[int] = []

    class Component:
        def render(self, width: int) -> list[str]:
            renders.append(width)
            return ["chrome"]

    ui.set_extension_widget("widget", lambda _tui, _theme: Component())
    ui.model_selector_open = True
    before = len(renders)

    snapshot = ui._frame_snapshot(width=60, height=12, include_session_picker=True)

    assert snapshot.overlay is not None
    assert snapshot.chrome == ChromeSnapshot()
    assert len(renders) == before


def test_failed_paint_write_keeps_preexisting_publication_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = _ui(tmp_path)
    ui._history_blocks.append(("notice", ("WRITE_FAILURE_MARKER",)))
    writes: list[str] = []

    def fail_write(_driver: TerminalDriver, text: str) -> bool:
        writes.append(text)
        return False

    monkeypatch.setattr(TerminalDriver, "write", fail_write)
    ui.paint()

    assert len(writes) == 1
    assert "WRITE_FAILURE_MARKER" in writes[0]
    assert ui._painted_block_count == len(ui._history_blocks)
    assert ui._live_height > 0
    assert ui._last_painted_size == (88, 24)


def test_failed_deferred_clear_does_not_reset_or_paint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = _ui(tmp_path)
    ui._painted_block_count = 3
    ui._live_height = 4
    ui._live_input_row = 2
    writes: list[str] = []

    def fail_deferred(_driver: TerminalDriver, text: str) -> bool:
        writes.append(text)
        return False

    monkeypatch.setattr(TerminalDriver, "write_deferred", fail_deferred)
    ui._force_full_redraw()

    assert writes == ["\x1b[2J\x1b[H"]
    assert (ui._painted_block_count, ui._live_height, ui._live_input_row) == (3, 4, 2)

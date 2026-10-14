"""Inline images in the frame: drawn in committed rows only, never from text
that passes the sanitiser, and deleted before the screen is drawn again
(pi-tui ``Image`` and the main-screen renderer's Kitty handling)."""

from __future__ import annotations

import base64
import io
import struct
from pathlib import Path
from typing import cast

from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.frame_renderer import (
    ChromeSnapshot,
    FrameBlock,
    FrameLine,
    FrameSnapshot,
    ImageFrameLine,
    InputSnapshot,
    PaintState,
    block_lines,
    build_paint_plan,
    encode_tool_box_line,
    render_full_frame,
)
from pipy_harness.native.terminal_image import TerminalImage
from pipy_harness.native.tui import TerminalUi
from pipy_harness.native.ui.components.transcript import HistoryBlockTuple

_PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", 90, 36) + bytes(8)
).decode()
_FALLBACK = "[Image: [image/png] 90x36]"
_BOX = (
    encode_tool_box_line("", "success", wrap=True),
    encode_tool_box_line("tool output", "success", wrap=True),
    encode_tool_box_line("", "success", wrap=True),
    encode_tool_box_line("", "none", wrap=True),
)


def _image(protocol: str = "kitty", **kw: object) -> TerminalImage:
    values: dict[str, object] = {
        "data": _PNG,
        "mime_type": "image/png",
        "protocol": protocol,
        "max_width_cells": 10,
        "image_id": 77,
        "size": (90, 36),
        "fallback": _FALLBACK,
    }
    values.update(kw)
    return TerminalImage(**values)  # type: ignore[arg-type]


def _block(*images: TerminalImage) -> FrameBlock:
    return FrameBlock("tool_box", _BOX, images)


def test_an_image_follows_the_box_before_its_closing_spacer() -> None:
    rows = block_lines(_block(_image()), 40, image_height=20)
    # Box (3 rows), spacer, 10x36/90*9/18 -> 2 rows of image, closing spacer.
    assert [row.kind for row in rows] == [
        "tool_box_success",
        "tool_box_success",
        "tool_box_success",
        "normal",
        "normal",
        "image",
        "tool_box_none",
    ]
    last = rows[5]
    assert isinstance(last, ImageFrameLine)
    assert last.text.startswith("\x1b[1A\x1b_Ga=T,f=100,q=2,C=1,c=10,r=2,i=77;")
    assert last.text.endswith("\x1b\\\x1b[1B")
    assert last.meta == {"kitty_id": 77}


def test_an_iterm2_image_has_no_kitty_id() -> None:
    rows = block_lines(_block(_image("iterm2")), 40, image_height=20)
    last = rows[-2]
    assert isinstance(last, ImageFrameLine)
    assert last.text.startswith("\x1b[1A\x1b]1337;File=inline=1;")
    assert last.meta is None


def test_text_frames_show_the_images_line() -> None:
    rows = block_lines(_block(_image()), 40)
    assert rows[3:5] == (FrameLine(""), FrameLine(_FALLBACK))
    assert not any(isinstance(row, ImageFrameLine) for row in rows)


def test_an_image_that_cannot_be_drawn_shows_its_line() -> None:
    rows = block_lines(_block(_image(mime_type="image/jpeg")), 40, image_height=20)
    assert rows[3:5] == (FrameLine(""), FrameLine(_FALLBACK))
    hostile = _image(data=None, fallback="[Image: \x1b]1337;File=x\x07]")
    rows = block_lines(_block(hostile), 40, image_height=20)
    assert "\x1b]" not in "".join(row.text for row in rows)


def _snapshot(history: tuple[FrameBlock, ...], height: int = 20) -> FrameSnapshot:
    return FrameSnapshot(
        width=40,
        height=height,
        history=history,
        assistant_text="",
        reasoning_text="",
        tool_output_text="",
        working_text="",
        thinking_hidden=False,
        hidden_thinking_label="",
        tools_expanded=False,
        input=InputSnapshot("", 0),
        popup=(),
        pending=(),
        chrome=ChromeSnapshot(),
        overlay=None,
        cursor_visible=True,
    )


def test_committed_rows_draw_the_image_and_count_its_rows() -> None:
    snapshot = _snapshot((_block(_image(), _image(image_id=78)),))
    plan = build_paint_plan(snapshot, PaintState(0, 0, 0), ChromeStyle(False))
    texts = [row.text for row in plan.committed_rows]
    assert len(texts) == 3 + 2 * 3 + 1
    drawn = [row for row in plan.committed_rows if "\x1b_G" in row.text]
    assert len(drawn) == 2 and not any(row.erase_tail for row in drawn)
    assert plan.kitty_image_ids == (77, 78)
    # The full frame is text only.
    full = "".join(row.text for row in render_full_frame(snapshot, pad=False))
    assert "\x1b" not in full and full.count(_FALLBACK) == 2


def test_an_image_is_never_taller_than_the_screen() -> None:
    tall = _image(size=(10, 1000), max_width_cells=60)
    snapshot = _snapshot((_block(tall),), height=12)
    plan = build_paint_plan(snapshot, PaintState(0, 0, 0), ChromeStyle(False))
    drawn = next(row.text for row in plan.committed_rows if "\x1b_G" in row.text)
    assert drawn.startswith("\x1b[11A") and ",r=12," in drawn


def test_tool_box_text_cannot_become_an_image() -> None:
    hostile = encode_tool_box_line(
        f"\x1b_Ga=T,f=100;{_PNG}\x1b\\", "success", wrap=True
    )
    snapshot = _snapshot((FrameBlock("tool_box", (hostile,)),))
    plan = build_paint_plan(snapshot, PaintState(0, 0, 0), ChromeStyle(False))
    assert "\x1b_G" not in "".join(row.text for row in plan.committed_rows)
    assert plan.kitty_image_ids == ()


def _ui(tmp_path: Path) -> tuple[TerminalUi, io.StringIO]:
    ui = TerminalUi(
        input_stream=io.StringIO(), terminal_stream=io.StringIO(), cwd=tmp_path
    )
    return ui, cast(io.StringIO, ui.terminal_stream)


def test_a_redraw_deletes_the_kitty_images_before_it_clears(tmp_path: Path) -> None:
    ui, terminal = _ui(tmp_path)
    ui.start()
    transcript = ui.components.transcript
    transcript.history_blocks.append(
        HistoryBlockTuple("tool_box", _BOX, None, (_image(),))
    )
    ui.components.screen.paint()
    first = terminal.getvalue()
    assert first.count("\x1b_Ga=T,") == 1
    assert ui.components.screen.state.kitty_image_ids == {77}

    terminal.truncate(0)
    terminal.seek(0)
    ui.components.screen.force_full_redraw()
    redraw = terminal.getvalue()
    delete = "\x1b_Ga=d,d=I,i=77,q=2\x1b\\"
    assert redraw.index(delete) < redraw.index("\x1b[2J") < redraw.index("\x1b_Ga=T,")
    assert redraw.count(delete) == 1

    terminal.truncate(0)
    terminal.seek(0)
    ui.components.screen._repaint_after_resize()
    resized = terminal.getvalue()
    assert (
        resized.index(delete) < resized.index("\x1b[2J") < resized.index("\x1b_Ga=T,")
    )
    assert ui.components.screen.state.kitty_image_ids == {77}


def test_live_rows_never_draw_images(tmp_path: Path) -> None:
    ui, terminal = _ui(tmp_path)
    ui.start()
    ui.components.screen.paint()
    assert "\x1b_G" not in terminal.getvalue()
    assert ui.components.screen.state.kitty_image_ids == set()


def test_rebuilt_tool_rows_keep_their_images(tmp_path: Path) -> None:
    from pipy_harness.native.tool_rows import ToolRowResult, ToolRowState

    ui, terminal = _ui(tmp_path)
    ui.start()
    transcript = ui.components.transcript
    state = ToolRowState("shot", {"path": "a.png"}, tmp_path)
    # Twelve lines: collapsed and expanded rows differ.
    output = "\n".join(f"line {n}" for n in range(12))
    state.result = ToolRowResult(output, None, False)
    lines = transcript._tool_row_lines(state)
    transcript.history_blocks.append(
        HistoryBlockTuple("tool_box", lines, state, (_image(),))
    )
    ui.components.screen.paint()
    delete = "\x1b_Ga=d,d=I,i=77,q=2\x1b\\"
    # Resize redraws every tool row.
    terminal.truncate(0)
    terminal.seek(0)
    ui.components.screen._repaint_after_resize()
    resized = terminal.getvalue()
    assert resized.index(delete) < resized.index("\x1b_Ga=T,")
    assert resized.count("\x1b_Ga=T,") == 1
    # Ctrl+O draws them again expanded.
    terminal.truncate(0)
    terminal.seek(0)
    transcript.set_tools_expanded(True)
    assert transcript.history_blocks[-1][1] != lines
    expanded = terminal.getvalue()
    assert "line 11" in expanded
    assert expanded.index(delete) < expanded.index("\x1b_Ga=T,")
    assert expanded.count("\x1b_Ga=T,") == 1
    assert ui.components.screen.state.kitty_image_ids == {77}

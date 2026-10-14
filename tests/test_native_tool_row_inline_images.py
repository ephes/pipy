"""A tool row's images drawn inline (Pi ``ToolExecutionComponent.updateDisplay``
and ``getTextOutput``): with an image protocol and ``showImages`` the row
draws each image after its box and its text has no image lines; otherwise
the text lines stay."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from test_native_session_history_render import _box, _Terminal
from test_native_tool_result_image_rows import GIF, IMAGE, PNG, _result

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.content import ImageContent, TextContent
from pipy_harness.native.agent.events import ToolCallCompleted
from pipy_harness.native.terminal_image import terminal_image_protocol
from pipy_harness.native.tool_result_display import ResultImages, result_images
from pipy_harness.native.ui.state import RenderToolResult, UiState, reduce


def _resume(tmp_path: Path, *blocks: Any, options: tuple[Any, ...]) -> _Terminal:
    terminal = _Terminal(tmp_path)
    protocol, show, width = options
    terminal.transcript.set_image_options(protocol, show=show, width_cells=width)
    call = AgentToolCall("c1", "mcp__browser__shot", ProductContent("{}"))
    terminal.tree.append_message(AgentUserMessage(ProductContent("look")))
    terminal.tree.append_message(AgentAssistantMessage(ProductContent(""), (call,)))
    terminal.tree.append_message(_result(*blocks, name="mcp__browser__shot"))
    terminal.history.render_active_branch()
    return terminal


def _tool_block(terminal: _Terminal) -> Any:
    return next(b for b in terminal.transcript.history_blocks if b[0] == "tool_box")


def test_the_result_carries_its_images() -> None:
    result = _result(TextContent("saved"), IMAGE)
    assert result_images(result) == ResultImages("saved", (IMAGE,))
    assert result_images(_result(TextContent("plain"))) is None
    _state, decisions = reduce(
        UiState(assistant_active=True), ToolCallCompleted(0, result)
    )
    (decision,) = decisions
    assert isinstance(decision, RenderToolResult)
    assert decision.images == ResultImages("saved", (IMAGE,))


def test_a_drawn_image_leaves_the_rows_text(tmp_path: Path) -> None:
    terminal = _resume(
        tmp_path, TextContent("saved"), IMAGE, options=("kitty", True, 40)
    )
    block = _tool_block(terminal)
    assert _box(block[1]) == [("success", "browser/shot"), ("success", "saved")]
    (image,) = block.images
    assert (image.data, image.mime_type, image.protocol) == (PNG, "image/png", "kitty")
    assert (image.max_width_cells, image.size) == (40, (640, 480))
    assert "[Image: [image/png] 640x480]" in image.fallback
    assert image.image_id > 0


@pytest.mark.parametrize(
    "options", [(None, True, 60), ("kitty", False, 60), ("iterm2", False, 60)]
)
def test_without_a_protocol_or_with_show_images_off_the_line_stays(
    tmp_path: Path, options: tuple[Any, ...]
) -> None:
    terminal = _resume(tmp_path, TextContent("saved"), IMAGE, options=options)
    block = _tool_block(terminal)
    assert ("success", "[Image: [image/png] 640x480]") in _box(block[1])
    assert block.images == ()


def test_an_image_pi_cannot_size_assumes_800x600_and_bad_data_is_not_drawn(
    tmp_path: Path,
) -> None:
    hostile = ImageContent("QUJD\x1b\\", "image/png")
    terminal = _resume(
        tmp_path, ImageContent(GIF, "image/gif"), hostile, options=("iterm2", True, 60)
    )
    gif, bad = _tool_block(terminal).images
    assert gif.data == GIF and gif.size == (320, 200)
    assert bad.data is None and bad.size == (800, 600)
    assert "[Image: [image/png] 800x600]" in bad.fallback


def test_changing_the_settings_draws_the_rows_again(tmp_path: Path) -> None:
    terminal = _resume(
        tmp_path, TextContent("saved"), IMAGE, options=("kitty", True, 60)
    )
    first = _tool_block(terminal).images[0].image_id
    resets = terminal.resets
    terminal.transcript.set_image_options("kitty", show=False, width_cells=60)
    block = _tool_block(terminal)
    assert block.images == () and terminal.resets == resets + 1
    assert ("success", "[Image: [image/png] 640x480]") in _box(block[1])
    terminal.transcript.set_image_options("kitty", show=True, width_cells=30)
    (image,) = _tool_block(terminal).images
    # The same Kitty id, so the redraw replaces the image.
    assert (image.image_id, image.max_width_cells) == (first, 30)
    # No change, no redraw.
    resets = terminal.resets
    terminal.transcript.set_image_options("kitty", show=True, width_cells=30)
    assert terminal.resets == resets


def test_a_live_result_draws_its_images(tmp_path: Path) -> None:
    terminal = _Terminal(tmp_path)
    terminal.transcript.set_image_options("kitty", show=True, width_cells=60)
    renderer = terminal.tool_renderer
    renderer.render_tool_call(
        AgentToolCall("c1", "read", ProductContent('{"path": "a.png"}'))
    )
    result = _result(TextContent("Read image file [image/png]"), IMAGE, name="read")
    renderer.render_tool_result(
        output_text="ignored when drawn",
        is_error=False,
        images=result_images(result),
    )
    block = _tool_block(terminal)
    assert len(block.images) == 1
    assert "[Image:" not in "".join(block[1])


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_only_a_terminal_gets_a_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPY_IMAGE_PROTOCOL", "kitty")
    assert terminal_image_protocol(io.StringIO()) is None
    assert terminal_image_protocol(object()) is None
    assert terminal_image_protocol(_Tty()) == "kitty"
    monkeypatch.setenv("PIPY_IMAGE_PROTOCOL", "none")
    assert terminal_image_protocol(_Tty()) is None

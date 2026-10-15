"""What a tool renderer is told about its call (goal-check finding M24; Pi
``ToolRenderContext`` and ``ToolExecutionComponent.getRenderContext``)."""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from typing import Any

from pipy_harness.extensions import ToolRenderers, lines_component
from pipy_harness.native.agent import AgentToolCall, ProductContent
from pipy_harness.native.extension_chrome_state import ExtensionChromeState
from pipy_harness.native.extension_types import ToolRenderContext
from pipy_harness.native.tool_renderer_lookup import ToolRendererLookup
from pipy_harness.native.tool_renderers import _ToolLoopRenderer
from pipy_harness.native.tool_result_display import ResultImages
from pipy_harness.native.ui.components.tool_loop_renderer import TuiToolLoopRenderer
from pipy_harness.native.ui.components.transcript import TranscriptComponent
from pipy_harness.native.ui.paint_lock import PaintLock
from pipy_harness.native.ui.screen import ScreenRenderInputs


class _Recorder:
    """Renderers that record every context and draw ``state['text']``."""

    def __init__(self) -> None:
        self.seen: list[ToolRenderContext] = []
        self.components: list[object] = []
        self.on_render: Any = None

    def _render(self, ctx: ToolRenderContext) -> object:
        self.seen.append(ctx)
        if self.on_render is not None:
            self.on_render(ctx)
        component = lines_component([f"{ctx.state.get('text', 'start')}"])
        self.components.append(component)
        return component

    def renderers(self) -> ToolRenderers:
        return ToolRenderers(render_call=self._render, render_result=self._render)


class _Live:
    def __init__(self, tmp_path: Path, recorder: _Recorder) -> None:
        self.repaints = 0
        self.scrollback_resets = 0
        inputs = ScreenRenderInputs(
            lambda: 60, io.StringIO(), lambda: self.transcript.tools_expanded
        )

        def repaint() -> None:
            self.repaints += 1

        def reset() -> None:
            self.scrollback_resets += 1

        self.transcript = TranscriptComponent(
            PaintLock(threading.RLock()),
            repaint,
            reset_scrollback=reset,
            render_inputs=inputs,
        )
        self.renderer = TuiToolLoopRenderer(
            transcript=self.transcript,
            chrome=ExtensionChromeState(),
            render_inputs=inputs,
            cwd=tmp_path,
            tool_renderers=ToolRendererLookup(
                resolvers=(lambda name, _next: recorder.renderers(),)
            ),
        )

    def call(self) -> None:
        self.renderer.render_tool_call(
            AgentToolCall("call-7", "deploy", ProductContent(json.dumps({"to": "x"})))
        )


def test_a_live_row_tells_the_renderer_about_the_call(tmp_path: Path) -> None:
    recorder = _Recorder()
    live = _Live(tmp_path, recorder)

    live.call()
    call = recorder.seen[-1]
    assert call.tool_call_id == "call-7"
    assert call.cwd == str(tmp_path)
    assert call.execution_started is True
    assert call.args_complete is True
    assert call.is_partial is True and call.is_result is False
    assert call.show_images is True  # Pi's terminal.showImages default
    assert call.duration_ms is None
    assert call.last_component is None

    live.renderer.tool_output_sink("half")
    partial = recorder.seen[-1]
    assert partial.is_result is True and partial.is_partial is True
    assert partial.content == "half"
    # The call phase drawn again gets what it returned the time before.
    calls = [ctx for ctx in recorder.seen if not ctx.is_result]
    assert calls[-1].last_component is not None

    live.renderer.render_tool_result(
        output_text="done", is_error=False, duration_seconds=1.5
    )
    final = recorder.seen[-1]
    assert final.is_partial is False and final.is_result is True
    assert final.duration_ms == 1500
    assert final.last_component is not None
    assert final.tool_call_id == "call-7"


def test_invalidate_draws_the_row_again(tmp_path: Path) -> None:
    # Pi `invalidate`: the component renders again and the TUI repaints.
    recorder = _Recorder()
    live = _Live(tmp_path, recorder)
    live.call()
    ctx = recorder.seen[-1]
    assert "start" in "".join(live.transcript.pending_tool_lines)

    ctx.state["text"] = "loaded"
    repaints = live.repaints
    worker = threading.Thread(target=ctx.invalidate)
    worker.start()
    worker.join(5)

    assert "loaded" in "".join(live.transcript.pending_tool_lines)
    assert live.repaints == repaints + 1

    # A committed row is drawn again with the scrollback when it changed.
    live.renderer.render_tool_result(output_text="ok", is_error=False)
    committed = recorder.seen[-1]
    committed.state["text"] = "final"
    committed.invalidate()
    assert "final" in "".join(live.transcript.history_blocks[-1][1])
    assert live.scrollback_resets == 1
    committed.invalidate()  # Nothing changed: nothing is redrawn.
    assert live.scrollback_resets == 1


def test_invalidate_while_the_row_is_drawn_does_not_draw_it_again(
    tmp_path: Path,
) -> None:
    recorder = _Recorder()
    recorder.on_render = lambda ctx: ctx.invalidate()
    live = _Live(tmp_path, recorder)

    live.call()

    assert len(recorder.seen) == 1


def test_captured_rows_tell_the_renderer_about_the_call(tmp_path: Path) -> None:
    recorder = _Recorder()
    renderer = _ToolLoopRenderer(
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
        tool_renderers=ToolRendererLookup(
            resolvers=(lambda name, _next: recorder.renderers(),)
        ),
        cwd=tmp_path,
    )

    renderer.render_tool_call(
        AgentToolCall("call-9", "deploy", ProductContent("{}")), display_only=True
    )
    renderer.render_tool_result(output_text="ok", is_error=True, duration_seconds=2)

    call, result = recorder.seen
    assert (call.tool_call_id, result.tool_call_id) == ("call-9", "call-9")
    assert call.cwd == str(tmp_path)
    assert call.execution_started is False  # a call drawn from the file
    assert call.is_partial is True and result.is_partial is False
    assert result.is_error is True
    assert result.duration_ms == 2000
    assert result.last_component is None
    result.invalidate()  # a no-op for rows written once


def test_a_replayed_row_is_drawn_again_where_it_is_shown(tmp_path: Path) -> None:
    # A resumed session replays its rows into a scratch transcript; their
    # `invalidate` draws them in the transcript that shows them.
    recorder = _Recorder()
    scratch = _Live(tmp_path, recorder)
    scratch.call()
    scratch.renderer.render_tool_result(output_text="ok", is_error=False)
    shown = _Live(tmp_path, recorder)
    shown.transcript.replace_conversation(scratch.transcript.history_blocks)

    ctx = recorder.seen[-1]
    ctx.state["text"] = "resumed"
    ctx.invalidate()

    assert "resumed" in "".join(shown.transcript.history_blocks[-1][1])
    assert shown.scrollback_resets == 1
    assert scratch.scrollback_resets == 0


def test_a_redraw_asked_for_during_the_transfer_is_kept(tmp_path: Path) -> None:
    # The rows are copied out of the scratch transcript first; a redraw that
    # arrives before the displayed transcript takes them is not lost.
    recorder = _Recorder()
    scratch = _Live(tmp_path, recorder)
    scratch.call()
    scratch.renderer.render_tool_result(output_text="ok", is_error=False)
    blocks = list(scratch.transcript.history_blocks)
    ctx = recorder.seen[-1]
    ctx.state["text"] = "loaded"
    ctx.invalidate()  # still drawn by the scratch transcript
    shown = _Live(tmp_path, recorder)

    shown.transcript.replace_conversation(blocks)

    assert "loaded" in "".join(shown.transcript.history_blocks[-1][1])


class _BrokenRender:
    def __init__(self, produced: object) -> None:
        self.produced = produced

    def render(self, width: int) -> object:
        if isinstance(self.produced, Exception):
            raise self.produced
        return self.produced


def test_a_component_that_fails_to_draw_is_not_kept() -> None:
    from pipy_harness.native.tool_renderers import render_tool_phase

    ctx = ToolRenderContext("t", {}, False, False, None, None, False, 40, None, {})
    for produced in (RuntimeError("x"), object()):
        kept: list[object | None] = ["previous"]
        component = _BrokenRender(produced)

        lines = render_tool_phase(lambda c: component, ctx, keep=kept.append)

        assert lines is None
        assert kept[-1] is None
    good: list[object | None] = []
    drawn = lines_component(["fine"])
    assert render_tool_phase(lambda c: drawn, ctx, keep=good.append) == ["fine"]
    assert good == [drawn]


def test_the_renderer_is_told_the_show_images_setting(tmp_path: Path) -> None:
    # Pi `showImages: this.showImages`: the setting, whatever the terminal.
    recorder = _Recorder()
    live = _Live(tmp_path, recorder)
    live.transcript.set_image_options(None, show=False, width_cells=60)
    live.call()
    assert recorder.seen[-1].show_images is False
    live.transcript.set_image_options(None, show=True, width_cells=60)
    live.renderer.render_tool_result(output_text="ok", is_error=False)
    assert recorder.seen[-1].show_images is True


def _last_images(live: _Live) -> tuple[object, ...]:
    block: Any = live.transcript.history_blocks[-1]
    return tuple(block.images)


def test_an_extension_rows_images_stay_through_a_redraw_and_a_resume(
    tmp_path: Path,
) -> None:
    from test_native_tool_result_image_rows import IMAGE

    recorder = _Recorder()
    scratch = _Live(tmp_path, recorder)
    scratch.transcript.set_image_options("kitty", show=True, width_cells=60)
    scratch.call()
    scratch.renderer.render_tool_result(
        output_text="shot", is_error=False, images=ResultImages("shot", (IMAGE,))
    )
    assert len(_last_images(scratch)) == 1

    ctx = recorder.seen[-1]
    ctx.state["text"] = "loaded"
    ctx.invalidate()
    assert "loaded" in "".join(scratch.transcript.history_blocks[-1][1])
    assert len(_last_images(scratch)) == 1

    shown = _Live(tmp_path, recorder)
    shown.transcript.set_image_options("kitty", show=True, width_cells=60)
    shown.transcript.replace_conversation(scratch.transcript.history_blocks)
    assert len(_last_images(shown)) == 1


def test_a_changed_show_images_setting_draws_extension_rows_again(
    tmp_path: Path,
) -> None:
    # Pi `setShowImages` updates every tool component, so a renderer that
    # reads `show_images` is drawn again also for a result without images.
    recorder = _Recorder()
    recorder.on_render = lambda ctx: ctx.state.__setitem__(
        "text", f"show={ctx.show_images}"
    )
    live = _Live(tmp_path, recorder)
    live.call()
    live.renderer.render_tool_result(output_text="ok", is_error=False)
    assert "show=True" in "".join(live.transcript.history_blocks[-1][1])

    live.transcript.set_image_options(None, show=False, width_cells=60)
    assert "show=False" in "".join(live.transcript.history_blocks[-1][1])
    assert live.scrollback_resets == 1


def test_a_changed_show_images_setting_draws_a_pending_row_again(
    tmp_path: Path,
) -> None:
    recorder = _Recorder()
    recorder.on_render = lambda ctx: ctx.state.__setitem__(
        "text", f"show={ctx.show_images}"
    )
    live = _Live(tmp_path, recorder)
    live.call()
    assert "show=True" in "".join(live.transcript.pending_tool_lines)
    repaints = live.repaints

    live.transcript.set_image_options(None, show=False, width_cells=60)
    assert "show=False" in "".join(live.transcript.pending_tool_lines)
    assert live.repaints == repaints + 1
    live.transcript.set_image_options(None, show=False, width_cells=40)
    assert live.repaints == repaints + 1  # nothing a renderer is told changed

"""Agent-event rendering for tool-loop turns behind the transcript.

Same ownership contract as the sibling components: the renderer holds no
terminal state of its own. It is the :class:`AgentEventRenderer`
implementation for real TTY sessions — every transcript verb (user/assistant
text, reasoning, tool call/result blocks, the transient working row) lands on
the :class:`~pipy_harness.native.ui.components.transcript.TranscriptComponent`,
spinner and working chrome are read straight off the shared
:class:`~pipy_harness.native.extension_chrome_state.ExtensionChromeState`
record, and the two live values a custom tool render needs beyond the
transcript — the frame width and the styling stream — arrive as injected
values built by the terminal shell, exactly like the custom-entry renderer's
target. The renderer never sees the terminal shell.

Locking is owned where the state is owned: transcript verbs take the shared
paint lock inside the component, so this renderer performs no locking of its
own. The working-spinner thread only reads the chrome record and calls the
transcript's ``set_working`` verb, which serializes against painters the same
way every other transcript write does.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, MutableMapping
from typing import TYPE_CHECKING, ClassVar, TextIO, TypedDict

from pipy_harness.native.agent import (
    AgentCancellationReason,
    AgentToolCall,
)
from pipy_harness.native.extension_chrome_state import ExtensionChromeState
from pipy_harness.native.extension_runtime import (
    ExtensionTool,
    ToolRenderDetailsSink,
)
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.tool_renderers import (
    _parse_tool_input,
    _plain_tool_call_header,
    _ToolLoopRenderer,
)
from pipy_harness.native.ui.components.transcript import TranscriptComponent

if TYPE_CHECKING:
    from pipy_harness.native.extension_types import ToolRenderContext


class _PendingToolRender(TypedDict):
    corr: str
    args: dict[str, object]
    state: dict[str, object]
    # The renderer resolved when the call was rendered. Pinning it here keeps a
    # result bound to the tool set advertised for its request: `/reload` may
    # replace the live renderer map while a tool call is in flight, and a
    # second lookup at result time would then render the result with a
    # different extension's renderer, or with none.
    tool: "ExtensionTool"


def _forward_legacy_render_details(ctx: ToolRenderContext, details: object) -> None:
    """Preserve opaque values manually inserted into the internal reader sink."""

    # The public context deliberately remains mapping-only. Older/manual callers
    # could still insert an opaque value into this internal handoff, so bypass the
    # frozen field only at this compatibility seam rather than widening its type.
    object.__setattr__(ctx, "details", details)


class TuiToolLoopRenderer:
    """Tool-loop renderer backed by the pipy-owned terminal UI shell."""

    _SPINNER_FRAMES: ClassVar[tuple[str, ...]] = _ToolLoopRenderer._SPINNER_FRAMES
    _SPINNER_INTERVAL_SECONDS: ClassVar[float] = (
        _ToolLoopRenderer._SPINNER_INTERVAL_SECONDS
    )
    _RESULT_LINE_PREVIEW_MAX_LENGTH: ClassVar[int] = 5

    def __init__(
        self,
        *,
        transcript: TranscriptComponent,
        chrome: ExtensionChromeState,
        terminal_stream: TextIO,
        frame_width: Callable[[], int],
        tool_renderers: Mapping[str, ExtensionTool] | None = None,
        render_details_sink: ToolRenderDetailsSink | None = None,
    ) -> None:
        self._transcript = transcript
        self._chrome = chrome
        self._terminal_stream = terminal_stream
        self._frame_width = frame_width
        self._streamed_any = False
        self._stop_working_event: threading.Event | None = None
        self._working_thread: threading.Thread | None = None
        self._last_tool_name = ""
        self._tool_renderers = dict(tool_renderers or {})
        self._render_details_sink = render_details_sink
        self._pending_render: _PendingToolRender | None = None

    @property
    def streamed_any(self) -> bool:
        return self._streamed_any

    def refresh_tool_renderers(
        self, tool_renderers: Mapping[str, ExtensionTool]
    ) -> None:
        self._tool_renderers = dict(tool_renderers)

    @property
    def stream_sink(self) -> StreamChunkSink:
        return self._handle_stream_chunk

    @property
    def reasoning_sink(self) -> StreamChunkSink:
        return self.handle_reasoning_chunk

    def start_assistant_message(self) -> None:
        """Reset and display provider-turn chrome for a canonical message start."""

        self.begin_provider_turn()
        self.show_working()

    def begin_provider_turn(self) -> None:
        self._stop_working(clear=True)
        self._streamed_any = False
        self._transcript.begin_assistant_turn()

    def _effective_spinner(self) -> tuple[tuple[str, ...], float]:
        frames = self._chrome.indicator_frames
        interval = self._chrome.indicator_interval_ms
        if frames is None:
            eff_frames = self._SPINNER_FRAMES
        elif len(frames) == 0:
            eff_frames = ("",)  # hide the glyph, keep the message
        else:
            eff_frames = tuple(frames)
        eff_interval = (
            self._SPINNER_INTERVAL_SECONDS if interval is None else interval / 1000.0
        )
        return eff_frames, eff_interval

    def show_working(self) -> None:
        self._stop_working(clear=True)
        if not self._chrome.working_visible:
            return
        stop_event = threading.Event()
        self._stop_working_event = stop_event

        def _animate() -> None:
            frames, interval = self._effective_spinner()
            frame_index = 0
            while not stop_event.is_set():
                glyph = frames[frame_index % len(frames)]
                message = self._chrome.working_message or "Working..."
                # An empty glyph hides the spinner: show the message with no
                # leading space/prefix.
                self._transcript.set_working(
                    message if glyph == "" else f"{glyph} {message}"
                )
                frame_index += 1
                stop_event.wait(interval)

        thread = threading.Thread(
            target=_animate,
            name="pipy-tool-loop-tui-spinner",
            daemon=True,
        )
        self._working_thread = thread
        thread.start()

    def complete_assistant_message(self, *, has_tool_calls: bool) -> None:
        del has_tool_calls
        self._finish_provider_turn()

    def _finish_provider_turn(self) -> None:
        self._stop_working(clear=True)
        self._transcript.settle_assistant()

    def fail_assistant_message(self) -> None:
        self._finish_provider_turn()

    def cancel_assistant_message(self, reason: AgentCancellationReason) -> None:
        self._stop_working(clear=True)
        if reason is AgentCancellationReason.OPERATOR_ABORT:
            self._transcript.show_operation_aborted()

    def render_user_message(self, text: str) -> None:
        self._transcript.submit_user_message(text)

    def render_buffered_assistant_text(
        self, text: str, *, has_tool_calls: bool
    ) -> None:
        """Render a non-streamed assistant completion from its canonical event."""

        del has_tool_calls
        self._transcript.append_assistant(text)
        self._streamed_any = True

    def render_tool_call(self, call: AgentToolCall) -> None:
        self._stop_working(clear=True)
        self._last_tool_name = call.tool_name
        self._pending_render = None
        tool = self._tool_renderers.get(call.tool_name)
        if tool is not None:
            args = _parse_tool_input(call.arguments_json.value)
            state: dict[str, object] = {}
            self._pending_render = {
                "corr": call.provider_correlation_id,
                "args": args,
                "state": state,
                "tool": tool,
            }
            if tool.render_call is not None:
                lines = self._dispatch_render(
                    tool.render_call,
                    args,
                    state,
                    is_result=False,
                    content=None,
                    details=None,
                    is_error=False,
                )
                if lines is not None:
                    self._transcript.add_tool_call_custom(lines)
                    return
        self._transcript.add_tool_call(_plain_tool_call_header(call))

    def tool_output_sink(self, chunk: str) -> None:
        self._transcript.append_tool_output(chunk)

    def render_tool_result(
        self,
        *,
        output_text: str,
        is_error: bool,
        duration_seconds: float | None = None,
    ) -> None:
        pending = self._pending_render
        self._pending_render = None
        if pending is not None:
            tool = pending["tool"]
            if tool.render_result is not None:
                details: object | None = None
                if self._render_details_sink is not None:
                    details = self._render_details_sink.pop(pending["corr"], None)
                lines = self._dispatch_render(
                    tool.render_result,
                    pending["args"],
                    pending["state"],
                    is_result=True,
                    content=output_text,
                    details=details,
                    is_error=is_error,
                )
                if lines is not None:
                    self._transcript.add_tool_result_custom(
                        lines, duration_seconds=duration_seconds
                    )
                    return
        if self._last_tool_name == "read" and not is_error:
            return
        lines = self._visible_tool_result_lines(output_text.splitlines() or [""])
        # Ctrl+O tool-output expansion: when expanded, commit the full retained
        # (already tool-bounded) output instead of the 5-line collapsed preview.
        if self._transcript.tools_expanded:
            rendered = lines
        else:
            preview_lines = lines[: self._RESULT_LINE_PREVIEW_MAX_LENGTH]
            earlier = len(lines) - len(preview_lines)
            if earlier > 0:
                rendered = [
                    f"... ({earlier} earlier lines, ctrl+o to expand)",
                    *lines[-self._RESULT_LINE_PREVIEW_MAX_LENGTH :],
                ]
            else:
                rendered = preview_lines
        self._transcript.add_tool_result(
            lines=rendered,
            is_error=is_error,
            duration_seconds=duration_seconds,
        )

    def _dispatch_render(
        self,
        renderer: Callable[[ToolRenderContext], object],
        args: Mapping[str, object],
        state: MutableMapping[str, object],
        *,
        is_result: bool,
        content: str | None,
        details: object | None,
        is_error: bool,
    ) -> list[str] | None:
        # Local imports: the render-theme machinery is only needed on the
        # rarely-hit custom-renderer branch, so it is imported here rather than
        # at module top to keep this module's import-time dependency surface
        # focused on the loop's hot path.
        from pipy_harness.extensions import ToolRenderContext
        from pipy_harness.native.chrome import chrome_style_for
        from pipy_harness.native.tool_renderers import (
            build_tool_render_theme,
            render_tool_phase,
        )

        style = chrome_style_for(self._terminal_stream)
        typed_details = details if isinstance(details, Mapping) else None
        ctx = ToolRenderContext(
            tool_name=self._last_tool_name,
            args=args,
            is_result=is_result,
            is_error=is_error,
            content=content,
            details=typed_details,
            expanded=self._transcript.tools_expanded,
            width=self._frame_width(),
            theme=build_tool_render_theme(style),
            state=state,
        )
        if details is not None and typed_details is None:
            _forward_legacy_render_details(ctx, details)
        return render_tool_phase(renderer, ctx)

    def _visible_tool_result_lines(self, lines: list[str]) -> list[str]:
        if self._last_tool_name != "ls":
            return lines
        rendered: list[str] = []
        for line in lines:
            if line.startswith("file "):
                rendered.append(line[len("file ") :])
            elif line.startswith("directory "):
                rendered.append(line[len("directory ") :])
            elif line.startswith("other "):
                rendered.append(line[len("other ") :])
            else:
                rendered.append(line)
        return rendered

    def handle_reasoning_chunk(self, chunk: str) -> None:
        self._stop_working(clear=True)
        self._transcript.append_reasoning(chunk)

    def _handle_stream_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        self._stop_working(clear=False)
        self._transcript.append_assistant(chunk)
        self._streamed_any = True

    def _stop_working(self, *, clear: bool = True) -> None:
        if self._stop_working_event is not None:
            self._stop_working_event.set()
        if self._working_thread is not None:
            self._working_thread.join(timeout=0.2)
        self._stop_working_event = None
        self._working_thread = None
        if clear:
            self._transcript.clear_working()

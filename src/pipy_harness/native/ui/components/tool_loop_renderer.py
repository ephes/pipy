"""Agent-event rendering for tool-loop turns behind the transcript.

Same ownership contract as the sibling components: the renderer holds no
terminal state of its own. It is the :class:`AgentEventRenderer`
implementation for real TTY sessions — every transcript verb (user/assistant
text, reasoning, tool call/result blocks, the transient working row) lands on
the :class:`~pipy_harness.native.ui.components.transcript.TranscriptComponent`,
spinner and working chrome are read straight off the shared
:class:`~pipy_harness.native.extension_chrome_state.ExtensionChromeState`
record, and the three live values a custom tool render needs beyond the transcript —
frame width, expansion, and styling stream — arrive through the screen-owned
render-input record, exactly like the custom-entry renderer's target. The
renderer never sees the composition facade.

Locking is owned where the state is owned: transcript verbs take the shared
paint lock inside the component, so this renderer performs no locking of its
own. The working-spinner thread only reads the chrome record and calls the
transcript's ``set_working`` verb, which serializes against painters the same
way every other transcript write does.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from pipy_harness.native.agent import (
    AgentCancellationReason,
    AgentToolCall,
)
from pipy_harness.native.extension_chrome_state import ExtensionChromeState
from pipy_harness.native.extension_types import ExtensionTool
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.skills import parse_skill_block
from pipy_harness.native.tool_renderers import _ToolLoopRenderer
from pipy_harness.native.tool_rows import (
    BUILTIN_RENDERED_TOOLS,
    SkillInvocationBox,
    ToolRowResult,
    ToolRowState,
    compute_edit_preview,
    parse_arguments,
)
from pipy_harness.native.ui.components.transcript import TranscriptComponent
from pipy_harness.native.ui.screen import ScreenRenderInputs

# How long a finished edit waits for its still-running diff preview.
_PREVIEW_JOIN_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class _RetryCountdown:
    """One scheduled retry: its Pi label and the monotonic end of the backoff."""

    attempt: int
    max_attempts: int
    deadline: float
    cancel_key: str


class TuiToolLoopRenderer:
    """Tool-loop renderer backed by the pipy-owned terminal UI shell."""

    _SPINNER_FRAMES: ClassVar[tuple[str, ...]] = _ToolLoopRenderer._SPINNER_FRAMES
    _SPINNER_INTERVAL_SECONDS: ClassVar[float] = (
        _ToolLoopRenderer._SPINNER_INTERVAL_SECONDS
    )

    def __init__(
        self,
        *,
        transcript: TranscriptComponent,
        chrome: ExtensionChromeState,
        render_inputs: ScreenRenderInputs,
        tool_renderers: Mapping[str, ExtensionTool] | None = None,
        cwd: Path | None = None,
        interrupt_key_text: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        replaying: bool = False,
    ) -> None:
        self._transcript = transcript
        self._chrome = chrome
        self._render_inputs = render_inputs
        # Pi ``keyText("app.interrupt")`` in the retry loader's cancel hint.
        self._interrupt_key_text = interrupt_key_text or (lambda: "escape")
        self._clock = clock
        self._retry: _RetryCountdown | None = None
        self._streamed_any = False
        self._stop_working_event: threading.Event | None = None
        self._working_thread: threading.Thread | None = None
        self._tool_renderers = dict(tool_renderers or {})
        # The session's working directory: tool paths resolve against it
        # (Pi's renderer `context.cwd`).
        self._cwd = cwd if cwd is not None else Path.cwd()
        # A session replay draws stored calls: no edit preview (Pi never
        # marks a restored call's arguments complete).
        self._replaying = replaying
        self._preview_thread: threading.Thread | None = None

    def detached(self, transcript: TranscriptComponent) -> "TuiToolLoopRenderer":
        """A renderer with this one's tool renderers that writes to ``transcript``.

        Restored history is replayed through the same verbs as live turns, into
        a scratch transcript, without touching this renderer's in-flight state.
        """

        return TuiToolLoopRenderer(
            transcript=transcript,
            chrome=self._chrome,
            render_inputs=self._render_inputs,
            tool_renderers=self._tool_renderers,
            cwd=self._cwd,
            interrupt_key_text=self._interrupt_key_text,
            clock=self._clock,
            replaying=True,
        )

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
        self._retry = None
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

    def _working_row(self) -> tuple[str, bool] | None:
        """The live row text and whether it is Pi's warning retry loader.

        While a retry backoff runs, Pi's ``RetryStatusIndicator`` replaces the
        working loader, counting whole seconds down; the retried request starts
        when it reaches zero, so the normal working row returns then.
        """

        retry = self._retry
        if retry is not None:
            remaining = retry.deadline - self._clock()
            if remaining > 0:
                return (
                    f"Retrying ({retry.attempt}/{retry.max_attempts}) in "
                    f"{math.ceil(remaining)}s... ({retry.cancel_key} to cancel)",
                    True,
                )
        if not self._chrome.working_visible:
            return None
        return (self._chrome.working_message or "Working...", False)

    def show_working(self) -> None:
        self._stop_working(clear=True)
        if not self._chrome.working_visible and self._retry is None:
            return
        stop_event = threading.Event()
        self._stop_working_event = stop_event

        def _animate() -> None:
            frames, interval = self._effective_spinner()
            frame_index = 0
            while not stop_event.is_set():
                row = self._working_row()
                if row is None:
                    self._transcript.clear_working()
                else:
                    message, warning = row
                    glyph = frames[frame_index % len(frames)]
                    # An empty glyph hides the spinner: show the message with
                    # no leading space/prefix.
                    self._transcript.set_working(
                        message if glyph == "" else f"{glyph} {message}",
                        warning=warning,
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
        self._retry = None
        self._stop_working(clear=True)
        self._transcript.settle_assistant()

    def fail_assistant_message(self) -> None:
        self._finish_provider_turn()

    def cancel_assistant_message(self, reason: AgentCancellationReason) -> None:
        self._retry = None
        self._stop_working(clear=True)
        if reason is AgentCancellationReason.OPERATOR_ABORT:
            self._transcript.show_operation_aborted()

    def schedule_retry(
        self, *, attempt: int, max_attempts: int, delay_ms: int, error_message: str
    ) -> None:
        """Pi ``auto_retry_start``: the failed attempt, then the retry loader."""

        self._stop_working(clear=True)
        self._transcript.add_error(f"Error: {error_message or 'Unknown error'}")
        self._streamed_any = False
        # An immutable snapshot, replaced whole: the spinner thread reads the
        # reference once per frame and never sees a half-updated countdown.
        self._retry = _RetryCountdown(
            attempt,
            max_attempts,
            self._clock() + max(0, delay_ms) / 1000.0,
            self._interrupt_key_text(),
        )
        self.show_working()

    def finish_retry(
        self, *, succeeded: bool, attempt: int, final_error: str | None
    ) -> None:
        """Pi ``auto_retry_end``: drop the loader; show a final failure."""

        self._retry = None
        if not succeeded:
            self._stop_working(clear=True)
            self._transcript.add_error(
                f"Retry failed after {attempt} attempts: "
                f"{final_error or 'Unknown error'}"
            )

    def render_user_message(self, text: str) -> None:
        """Draw a user message; a ``/skill:<name>`` block is Pi's skill box.

        Like Pi's ``SkillInvocationMessageComponent``, the block is drawn
        collapsed (Ctrl+O expands it) and the arguments after it follow as a
        normal user message, live and when a session is resumed.
        """

        block = parse_skill_block(text)
        if block is None:
            self._transcript.submit_user_message(text)
            return
        self._transcript.add_summary_box(SkillInvocationBox(block.name, block.content))
        if block.user_message:
            self._transcript.submit_user_message(block.user_message)

    def render_buffered_assistant_text(
        self, text: str, *, has_tool_calls: bool
    ) -> None:
        """Render a non-streamed assistant completion from its canonical event."""

        del has_tool_calls
        self._transcript.append_assistant(text)
        self._streamed_any = True

    def render_tool_call(self, call: AgentToolCall) -> None:
        """Start the call's row (Pi ``ToolExecutionComponent``), pending.

        Built-in tools draw with Pi's renderers; an extension tool with its
        ``render_call``/``render_result`` (pinned here, so a ``/reload``
        during the call cannot swap the renderer), otherwise with Pi's
        generic fallbacks. A live ``edit`` starts its diff preview, as Pi
        does once the arguments are complete: on a worker thread (Pi's
        ``computeEditsDiff`` is asynchronous), so a slow or blocking file
        never holds the loop or the interrupt keys. A replayed session never
        computes one.
        """

        self._stop_working(clear=True)
        args = parse_arguments(call.arguments_json.value)
        name = call.tool_name
        builtin = name in BUILTIN_RENDERED_TOOLS
        extension = None if builtin else self._tool_renderers.get(name)
        state = ToolRowState(
            tool_name=name,
            args=args,
            cwd=self._cwd,
            extension=extension,
            # Pi's bash renderer records `startedAt` once execution starts;
            # a replayed call never started here.
            started_at=None if self._replaying else self._transcript.now(),
        )
        self._transcript.start_tool(state)
        self._preview_thread = None
        if builtin and name == "edit" and not self._replaying:
            thread = threading.Thread(
                target=self._compute_edit_preview,
                args=(state,),
                name="pipy-edit-preview",
                daemon=True,
            )
            self._preview_thread = thread
            thread.start()

    def _compute_edit_preview(self, state: ToolRowState) -> None:
        preview = compute_edit_preview(state.args, state.cwd)
        if preview is not None:
            self._transcript.apply_tool_preview(state, preview)

    def tool_output_sink(self, chunk: str) -> None:
        self._transcript.append_tool_output(chunk)

    def render_tool_result(
        self,
        *,
        output_text: str,
        is_error: bool,
        duration_seconds: float | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        # Give a running edit preview a short, bounded moment to land first:
        # the preview normally resolves before the edit finishes (in Pi too),
        # and it decides where a failure's text is drawn.
        thread, self._preview_thread = self._preview_thread, None
        if thread is not None:
            thread.join(timeout=_PREVIEW_JOIN_SECONDS)
        self._transcript.finish_tool(
            ToolRowResult(output_text, details, is_error),
            duration_seconds=duration_seconds,
        )

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

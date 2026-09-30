"""The transcript: committed history blocks plus the live stream buffers.

Same ownership contract as the sibling components, with one difference: the
transcript's state never lived on a shared record, so the component owns its
fields directly -- the committed ``history_blocks`` list, the four live stream
buffers (``assistant_text``, ``reasoning_text``, ``tool_output_text``,
``working_text``), the running tool call's row (``pending_tool`` and its
rendered ``pending_tool_lines``), the Ctrl+T thinking-fold trio
(``thinking_hidden``, ``hidden_thinking_label``, ``deferred_reasoning``) and
the Ctrl+O ``tools_expanded`` flag. The screen reads these buffers through its
explicit frame-source protocol and owns the shared live render-input
projection.

Every verb applies its whole transition in ONE :class:`PaintLock` section --
mutating painters share that reentrant lock, so a concurrent frame never
observes a half-applied commit (an assistant buffer cleared but its history
block not yet appended). Repainting happens *outside* the lock through the
injected ``repaint`` callable. Verbs that replace committed rows wholesale
(:meth:`redraw_custom_entries`, :meth:`rerender_custom_messages`) call the
injected ``reset_scrollback`` callable instead -- the screen-owned full redraw
that clears inline-scrollback bookkeeping stays on the screen.
:meth:`replace_conversation` (a session switch, fork or tree navigation)
and :meth:`set_tools_expanded` (Ctrl+O, Pi's full render) call
``replace_scrollback``, the screen's full redraw that also clears the
terminal scrollback. These are the component's only effectful ports besides
repainting. The retained-row rerender (rich custom rows, summary rows, tool
rows) uses the same screen-owned render-input record as other renderers.

Two verbs deliberately do not repaint: :meth:`discard_working_text` and
:meth:`reset_hidden_thinking_label` run inside a caller's enclosing lock
section (extension-chrome transitions) whose caller paints once at the end.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Self, cast

from pipy_harness.native.chrome import chrome_style_for
from pipy_harness.native.extensions.contracts import (
    RegisteredEntryRenderer,
    RegisteredMessageRenderer,
)
from pipy_harness.native.extensions.custom_payloads import (
    render_extension_entry,
    render_extension_message,
)
from pipy_harness.native.session_tree_commands import sanitize_label_text
from pipy_harness.native.tool_rows import (
    EditPreview,
    RowRenderInputs,
    SummaryBox,
    ToolRowResult,
    ToolRowState,
    apply_edit_result,
    encode_rows,
    is_builtin_edit,
    is_running_builtin_bash,
    render_summary_box,
    render_tool_row,
    row_theme,
)
from pipy_harness.native.ui.paint_lock import PaintLock
from pipy_harness.native.ui.screen import ScreenRenderInputs

# Pi's default label shown in place of live reasoning while thinking is folded.
DEFAULT_HIDDEN_THINKING_LABEL = "Thinking..."
# Live streaming tool output stays character-bounded before the pure frame
# renderer applies its row-tail policy.
_TOOL_STREAM_LIVE_MAX_CHARS = 8 * 1024
# The app prefix of a diagnostic written to stderr; a chat status drops it.
_STDERR_PREFIX = "pipy: "

HistoryBlock = tuple[str, tuple[str, ...]]


class HistoryBlockTuple(tuple[str, tuple[str, ...]]):
    """Tuple-compatible history block with optional live render metadata."""

    state: object | None

    def __new__(
        cls, kind: str, lines: tuple[str, ...], state: object | None = None
    ) -> Self:
        obj = tuple.__new__(cls, (kind, lines))
        obj.state = state
        return obj


@dataclass(slots=True)
class CustomMessageRenderState:
    """Live-only state needed to refresh a custom component in place."""

    custom_type: str
    data: object | None
    renderers: Mapping[str, RegisteredMessageRenderer]
    styled: bool
    lines: tuple[str, ...]
    entry_renderers: Mapping[str, RegisteredEntryRenderer] | None = None


#: Start calling ``tick`` once a second; the returned callable stops it.
TickerScheduler = Callable[[Callable[[], None]], Callable[[], None]]

_TICK_SECONDS = 1.0


def _thread_ticker(tick: Callable[[], None]) -> Callable[[], None]:
    """Call ``tick`` every second on a daemon thread until cancelled."""

    stop = threading.Event()

    def run() -> None:
        while not stop.wait(_TICK_SECONDS):
            tick()

    threading.Thread(target=run, name="pipy-bash-elapsed", daemon=True).start()
    return stop.set


@dataclass(frozen=True, slots=True)
class SummaryRenderState:
    """A collapsible row with fixed collapsed/expanded lines (``!`` results)."""

    collapsed: tuple[str, ...]
    expanded: tuple[str, ...]


class TranscriptComponent:
    """Owner of committed history and live stream state behind the frame."""

    def __init__(
        self,
        paint_lock: PaintLock,
        repaint: Callable[[], None],
        *,
        reset_scrollback: Callable[[], None],
        render_inputs: ScreenRenderInputs,
        replace_scrollback: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        schedule_ticker: TickerScheduler | None = None,
    ) -> None:
        self._paint_lock = paint_lock
        # A running `bash` row's `Elapsed` reads `clock`; `schedule_ticker`
        # re-renders it every second (Pi's `setInterval(invalidate, 1000)`).
        self._clock = clock
        self._schedule_ticker = schedule_ticker or _thread_ticker
        self._cancel_ticker: Callable[[], None] | None = None
        self._repaint = repaint
        self._reset_scrollback = reset_scrollback
        # The full redraw that also clears the terminal scrollback, used when
        # a session replacement swaps the whole conversation (Pi's clearing
        # full render). Defaults to the plain full redraw.
        self._replace_scrollback = replace_scrollback or reset_scrollback
        self._render_inputs = render_inputs
        self.history_blocks: list[HistoryBlock] = []
        # Rows seeded by ``seed_history`` (the startup header and resource
        # listing). A conversation replacement keeps them, like Pi keeps its
        # header containers while it clears the chat container.
        self.startup_block_count = 0
        self._seeded = False
        self.assistant_text = ""
        self.reasoning_text = ""
        self.tool_output_text = ""
        self.working_text = ""
        # The retry loader (Pi ``RetryStatusIndicator``) uses a warning spinner.
        self.working_warning = False
        self.thinking_hidden = False
        self.hidden_thinking_label = DEFAULT_HIDDEN_THINKING_LABEL
        self.tools_expanded = False
        # The running tool call's row (Pi's pending `ToolExecutionComponent`):
        # drawn in the live region until its result commits the whole row.
        self.pending_tool: ToolRowState | None = None
        self.pending_tool_lines: tuple[str, ...] = ()
        # Reasoning blocks that settled while thinking was folded (Ctrl+T).
        # Retained rather than dropped so toggling visibility back reveals them
        # (committed fresh at toggle time, not retro-written into scrollback).
        self.deferred_reasoning: list[str] = []

    # -- seeding -------------------------------------------------------------

    def seed_history(self, blocks: Iterable[HistoryBlock]) -> None:
        """Seed the startup blocks once, above anything added before start.

        Rows added before the terminal UI started (an extension's load
        notice) follow the header and count as startup rows, so the first
        conversation render keeps them.
        """

        with self._paint_lock:
            if self._seeded:
                return
            self._seeded = True
            self.history_blocks[:0] = list(blocks)
            self.startup_block_count = len(self.history_blocks)

    def replace_conversation(self, blocks: Iterable[HistoryBlock]) -> None:
        """Replace every row after the startup rows with ``blocks``.

        Pi clears its chat container and renders the active branch again after
        a session switch, fork or tree navigation. When nothing follows the
        startup rows yet (the startup render) the rows are appended and
        painted like any other commit; otherwise the screen and the terminal
        scrollback are redrawn from the replaced rows.
        """

        replacement = list(blocks)
        with self._paint_lock:
            boundary = min(self.startup_block_count, len(self.history_blocks))
            replaced_rows = len(self.history_blocks) > boundary
            self.assistant_text = ""
            self.reasoning_text = ""
            self.tool_output_text = ""
            self.working_text = ""
            self.pending_tool = None
            self.pending_tool_lines = ()
            self.deferred_reasoning.clear()
            self.history_blocks = [*self.history_blocks[:boundary], *replacement]
            cancel = self._take_ticker_locked()
        if cancel is not None:
            cancel()
        if replaced_rows:
            self._replace_scrollback()
        else:
            self._repaint()

    # -- user / assistant / reasoning stream ---------------------------------

    def submit_user_message(self, text: str) -> None:
        with self._paint_lock:
            self._settle_reasoning_locked()
            self.assistant_text = ""
            self.working_text = ""
            self.history_blocks.append(
                HistoryBlockTuple("user", tuple(text.splitlines() or [""]))
            )
        self._repaint()

    def begin_assistant_turn(self) -> None:
        with self._paint_lock:
            self._settle_reasoning_locked()
            self.assistant_text = ""
            self.working_text = ""
        self._repaint()

    def set_working(self, text: str, *, warning: bool = False) -> None:
        with self._paint_lock:
            self.working_text = text
            self.working_warning = warning
        self._repaint()

    def clear_working(self) -> None:
        with self._paint_lock:
            if not self.working_text:
                return
            self.working_text = ""
        self._repaint()

    def discard_working_text(self) -> None:
        """Clear the live working row without repainting.

        For callers running their own enclosing paint-lock transition (the
        extension-chrome working-visibility verb) that paint once at the end.
        """

        with self._paint_lock:
            self.working_text = ""

    def append_assistant(self, chunk: str) -> None:
        if not chunk:
            return
        with self._paint_lock:
            self._settle_reasoning_locked()
            self.assistant_text += chunk
        self._repaint()

    def settle_assistant(self, final_text: str = "") -> None:
        with self._paint_lock:
            self.working_text = ""
            self._settle_reasoning_locked()
            if final_text and not self.assistant_text:
                self.assistant_text = final_text
            if self.assistant_text:
                self.history_blocks.append(
                    HistoryBlockTuple(
                        "assistant", tuple(self.assistant_text.splitlines() or [""])
                    )
                )
                self.assistant_text = ""
        self._repaint()

    def show_operation_aborted(self) -> None:
        self.add_error("Operation aborted")

    def add_error(self, text: str) -> None:
        """Settle any partial assistant text, then show ``text`` as an error.

        Pi's assistant component draws an aborted or failed turn's marker
        (``Operation aborted``, ``Error: …``) after its partial content.
        """

        with self._paint_lock:
            self.working_text = ""
            self._settle_reasoning_locked()
            if self.assistant_text:
                self.history_blocks.append(
                    HistoryBlockTuple(
                        "assistant", tuple(self.assistant_text.splitlines() or [""])
                    )
                )
                self.assistant_text = ""
            safe_lines = tuple(
                sanitize_label_text(line) for line in str(text).splitlines()
            ) or ("",)
            self.history_blocks.append(HistoryBlockTuple("error", safe_lines))
        self._repaint()

    def append_reasoning(self, chunk: str) -> None:
        if not chunk:
            return
        with self._paint_lock:
            self.working_text = ""
            self.reasoning_text += chunk.replace("**", "")
        self._repaint()

    def _settle_reasoning_locked(self) -> None:
        if not self.reasoning_text:
            return
        # When thinking blocks are folded (Ctrl+T), the settled reasoning is
        # deferred (retained, not committed to scrollback) so the fold holds but
        # the content is not lost -- toggling visibility back reveals it.
        if self.thinking_hidden:
            self.deferred_reasoning.append(self.reasoning_text)
        else:
            self.history_blocks.append(
                HistoryBlockTuple(
                    "reasoning", tuple(self.reasoning_text.splitlines() or [""])
                )
            )
        self.reasoning_text = ""

    def settle_reasoning(self) -> None:
        """Commit (or defer, while folded) the live reasoning buffer."""

        with self._paint_lock:
            self._settle_reasoning_locked()

    # -- thinking fold (Ctrl+T) ----------------------------------------------

    def set_thinking_hidden(self, hidden: bool) -> None:
        """Set the Ctrl+T thinking-fold flag, revealing deferred reasoning.

        Folding hides subsequent/live reasoning and defers settled blocks;
        unfolding commits any deferred reasoning into history so it becomes
        visible (committed fresh now rather than retro-written into the host
        terminal's existing scrollback, preserving the inline contract).
        """

        with self._paint_lock:
            self.thinking_hidden = hidden
            revealed = bool(not hidden and self.deferred_reasoning)
            if revealed:
                for text in self.deferred_reasoning:
                    self.history_blocks.append(
                        HistoryBlockTuple("reasoning", tuple(text.splitlines() or [""]))
                    )
                self.deferred_reasoning.clear()
        if revealed:
            self._repaint()

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        """Set the live folded-thinking label; ``None`` restores Pi's default."""

        with self._paint_lock:
            self.hidden_thinking_label = (
                DEFAULT_HIDDEN_THINKING_LABEL if label is None else str(label)
            )
        self._repaint()

    def reset_hidden_thinking_label(self) -> None:
        """Restore the default label without repainting.

        For the extension-chrome teardown, which resets the label inside its
        own enclosing paint-lock transition and paints once at the end.
        """

        with self._paint_lock:
            self.hidden_thinking_label = DEFAULT_HIDDEN_THINKING_LABEL

    # -- notices / settings overlay ------------------------------------------

    def add_notice(self, text: str) -> None:
        """Show a status line (Pi ``showStatus``: dim, one column in).

        The ``pipy: `` prefix marks a line on stderr; in the chat it is
        dropped, as Pi's statuses carry none.
        """

        with self._paint_lock:
            self._settle_reasoning_locked()
            safe_lines = tuple(
                sanitize_label_text(line.removeprefix(_STDERR_PREFIX))
                for line in str(text).splitlines()
            ) or ("",)
            self.history_blocks.append(HistoryBlockTuple("notice", safe_lines))
        self._repaint()

    def show_settings(self, lines: Iterable[str]) -> None:
        """Render a read-only settings/status listing into the history region.

        Display-only: it shows safe provider/model/status information and never
        switches models, mutates auth state, invokes tools, or creates a
        provider turn. It renders through the same whole-frame paint path as
        every other history block.
        """

        with self._paint_lock:
            self._settle_reasoning_locked()
            self.working_text = ""
            self.history_blocks.append(
                HistoryBlockTuple("settings", tuple(lines) or ("",))
            )
        self._repaint()

    # -- tool call / result stream -------------------------------------------

    def add_tool_call(self, header: str) -> None:
        """Commit a ``!`` shell command's ``$ command`` row."""

        with self._paint_lock:
            self._settle_reasoning_locked()
            self.working_text = ""
            self.tool_output_text = ""
            self.history_blocks.append(HistoryBlockTuple("tool", (header,)))
        self._repaint()

    def start_tool(self, state: ToolRowState) -> None:
        """Show a model tool call's row, pending, in the live region.

        Pi adds a pending ``ToolExecutionComponent`` whose box turns green or
        red when the result arrives; the inline scrollback cannot change rows
        it has printed, so the row stays live until :meth:`finish_tool`
        commits it once.
        """

        with self._paint_lock:
            self._settle_reasoning_locked()
            self.working_text = ""
            self.tool_output_text = ""
            self._flush_pending_tool_locked()
            self.pending_tool = state
            self.pending_tool_lines = self._tool_row_lines(state)
            cancel = self._take_ticker_locked()
        if cancel is not None:
            cancel()
        if is_running_builtin_bash(state):
            self._start_ticker(state)
        self._repaint()

    def now(self) -> float:
        """The clock a live ``bash`` row's ``started_at`` is taken from."""

        return self._clock()

    def _start_ticker(self, state: ToolRowState) -> None:
        def tick() -> None:
            with self._paint_lock:
                if self.pending_tool is not state or not is_running_builtin_bash(state):
                    return
                self.pending_tool_lines = self._tool_row_lines(state)
            self._repaint()

        cancel = self._schedule_ticker(tick)
        with self._paint_lock:
            if self.pending_tool is state and self._cancel_ticker is None:
                self._cancel_ticker = cancel
                return
        # The row settled while the ticker started.
        cancel()

    def _take_ticker_locked(self) -> Callable[[], None] | None:
        cancel, self._cancel_ticker = self._cancel_ticker, None
        return cancel

    def finish_tool(
        self, result: ToolRowResult, *, duration_seconds: float | None = None
    ) -> None:
        """Commit the running call's row with its result (Pi ``updateResult``)."""

        with self._paint_lock:
            self._settle_reasoning_locked()
            self.tool_output_text = ""
            cancel = self._take_ticker_locked()
            state = self.pending_tool
            if state is not None:
                state.result = result
                state.partial_output = ""
                state.duration_seconds = duration_seconds
                if state.started_at is not None:
                    state.ended_at = self._clock()
                if is_builtin_edit(state):
                    apply_edit_result(state)
                self.pending_tool = None
                self.pending_tool_lines = ()
                self.history_blocks.append(
                    HistoryBlockTuple("tool_box", self._tool_row_lines(state), state)
                )
        if cancel is not None:
            cancel()
        if state is not None:
            self._repaint()

    def refresh_tool_rows(self) -> None:
        """Redraw every tool row at the current width; the caller paints.

        Collapsed ``bash`` output counts wrapped rows and extension renderers
        draw at a width, so a terminal resize redraws the rows, as Pi renders
        every component again at the new width.
        """

        with self._paint_lock:
            self.history_blocks = [
                HistoryBlockTuple(block[0], self._tool_row_lines(state), state)
                if isinstance(
                    state := getattr(block, "state", None), ToolRowState | SummaryBox
                )
                else block
                for block in self.history_blocks
            ]
            if self.pending_tool is not None:
                self.pending_tool_lines = self._tool_row_lines(self.pending_tool)

    def apply_tool_preview(self, state: ToolRowState, preview: EditPreview) -> None:
        """Pi's asynchronous edit preview arriving for a still-running call.

        The preview is computed off the UI thread (Pi schedules
        ``computeEditsDiff`` and invalidates the component when it resolves).
        Once the result has arrived the row is committed and the result's diff
        is authoritative, so a late preview is dropped.
        """

        with self._paint_lock:
            if state is not self.pending_tool or state.result is not None:
                return
            state.preview = preview
            self.pending_tool_lines = self._tool_row_lines(state)
        self._repaint()

    def flush_pending_tool(self) -> None:
        """Commit a call that never got a result as it stands (pending)."""

        with self._paint_lock:
            flushed = self._flush_pending_tool_locked()
            cancel = self._take_ticker_locked()
        if cancel is not None:
            cancel()
        if flushed:
            self._repaint()

    def _flush_pending_tool_locked(self) -> bool:
        state = self.pending_tool
        if state is None:
            return False
        if state.started_at is not None and state.ended_at is None:
            # The committed row cannot tick any more: freeze its `Elapsed`.
            state.ended_at = self._clock()
        self.history_blocks.append(
            HistoryBlockTuple("tool_box", self._tool_row_lines(state), state)
        )
        self.pending_tool = None
        self.pending_tool_lines = ()
        return True

    def _tool_row_lines(self, state: ToolRowState | SummaryBox) -> tuple[str, ...]:
        style = chrome_style_for(self._render_inputs.stream)
        if isinstance(state, SummaryBox):
            return encode_rows(
                render_summary_box(
                    state, row_theme(style), expanded=self.tools_expanded
                )
            )
        inputs = RowRenderInputs(
            expanded=self.tools_expanded,
            width=self._render_inputs.width(),
            theme=row_theme(style),
            extension_theme=self._render_inputs.theme(),
            now=self._clock(),
        )
        return encode_rows(render_tool_row(state, inputs))

    def append_tool_output(self, chunk: str) -> None:
        """Stream incremental tool output into the live region as produced.

        A running model tool call (`bash`) shows it inside its pending row
        through the tool's result renderer, like Pi's partial results; the
        ``!`` shortcut, which has no row, shows it as a live tail. Only a
        bounded tail is kept live; the full bounded result arrives with the
        result.
        """

        if not chunk:
            return
        with self._paint_lock:
            self._settle_reasoning_locked()
            self.working_text = ""
            state = self.pending_tool
            if state is not None:
                state.partial_output = (state.partial_output + chunk)[
                    -_TOOL_STREAM_LIVE_MAX_CHARS:
                ]
                self.pending_tool_lines = self._tool_row_lines(state)
            else:
                self.tool_output_text += chunk
                if len(self.tool_output_text) > _TOOL_STREAM_LIVE_MAX_CHARS:
                    self.tool_output_text = self.tool_output_text[
                        -_TOOL_STREAM_LIVE_MAX_CHARS:
                    ]
        self._repaint()

    def add_tool_result(
        self,
        *,
        lines: Iterable[str],
        is_error: bool,
        duration_seconds: float | None = None,
    ) -> None:
        with self._paint_lock:
            self._settle_reasoning_locked()
            self.tool_output_text = ""
            rendered = list(lines)
            if is_error:
                rendered.append("[error] tool reported a failure")
            if duration_seconds is not None:
                rendered.extend(("", f"Took {duration_seconds:.1f}s"))
            self.history_blocks.append(
                HistoryBlockTuple("tool_result", tuple(rendered or [""]))
            )
        self._repaint()

    def add_shell_result(
        self, *, collapsed: Iterable[str], expanded: Iterable[str]
    ) -> None:
        """Commit a ``!`` command's rows, which follow the Ctrl+O flag.

        Like Pi's ``BashExecutionComponent.setExpanded``: collapsed shows the
        last lines and a ``more lines`` hint, expanded shows every line.
        """

        state = SummaryRenderState(tuple(collapsed) or ("",), tuple(expanded) or ("",))
        with self._paint_lock:
            self._settle_reasoning_locked()
            self.tool_output_text = ""
            lines = state.expanded if self.tools_expanded else state.collapsed
            self.history_blocks.append(HistoryBlockTuple("tool_result", lines, state))
        self._repaint()

    def add_summary_box(self, box: SummaryBox) -> None:
        """Commit a collapsible compaction or branch summary box.

        Pi's summary components draw a padded ``customMessageBg`` box; like a
        tool row it is drawn again on Ctrl+O and resize, with the active theme.
        """

        with self._paint_lock:
            self._settle_reasoning_locked()
            self.working_text = ""
            self.history_blocks.append(
                HistoryBlockTuple("tool_box", self._tool_row_lines(box), box)
            )
        self._repaint()

    # -- extension custom entries --------------------------------------------

    def add_custom_entry(self, custom_type: str, lines: Iterable[str]) -> None:
        """Render an extension custom session entry into committed history."""

        with self._paint_lock:
            self._settle_reasoning_locked()
            self.working_text = ""
            label = sanitize_label_text(str(custom_type).strip()) or "custom"
            safe_lines = tuple(sanitize_label_text(line) for line in lines) or ("",)
            self.history_blocks.append(
                HistoryBlockTuple("custom", (f"[{label}]", *safe_lines))
            )
        self._repaint()

    def add_custom_entry_styled(
        self,
        lines: Iterable[str],
        *,
        custom_type: str | None = None,
        data: object | None = None,
        renderers: Mapping[str, RegisteredMessageRenderer] | None = None,
    ) -> None:
        """Commit extension-rendered custom-entry lines (pre-styled, SGR-safe).

        Unlike ``add_custom_entry`` (sanitized + ``[label]`` prefix), the rich
        renderer's component owns its full styling; no label line is injected
        (matches Pi's custom-message component replacing the default box). When
        renderer metadata is supplied, the block can be refreshed in-place when
        Ctrl+O changes the live expanded flag."""

        with self._paint_lock:
            self._settle_reasoning_locked()
            self.working_text = ""
            self.tool_output_text = ""
            rendered_lines = tuple(lines) or ("",)
            state = None
            if custom_type is not None and renderers is not None:
                state = CustomMessageRenderState(
                    custom_type=custom_type,
                    data=data,
                    renderers=renderers,
                    styled=True,
                    lines=rendered_lines,
                )
            self.history_blocks.append(
                HistoryBlockTuple("custom_message_custom", rendered_lines, state)
            )
        self._repaint()

    def add_entry_renderer_component(
        self,
        lines: Iterable[str],
        *,
        custom_type: str,
        entry: Mapping[str, object],
        renderers: Mapping[str, RegisteredEntryRenderer],
    ) -> None:
        """Commit a durable-entry renderer's live-only component snapshot."""

        with self._paint_lock:
            self._settle_reasoning_locked()
            self.working_text = ""
            self.tool_output_text = ""
            rendered_lines = tuple(lines) or ("",)
            state = CustomMessageRenderState(
                custom_type=custom_type,
                data=dict(entry),
                renderers={},
                styled=True,
                lines=rendered_lines,
                entry_renderers=renderers,
            )
            self.history_blocks.append(
                HistoryBlockTuple("custom_message_custom", rendered_lines, state)
            )
        self._repaint()

    def custom_entry_blocks(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Return committed custom-entry blocks for focused conformance tests."""

        with self._paint_lock:
            return tuple(
                (kind, lines)
                for kind, lines in self.history_blocks
                if kind in {"custom", "custom_message_custom"}
            )

    def redraw_custom_entries(
        self,
        entries: Iterable[
            tuple[str, str, tuple[str, ...]]
            | tuple[
                str,
                str,
                tuple[str, ...],
                object | None,
                Mapping[str, RegisteredMessageRenderer]
                | Mapping[str, RegisteredEntryRenderer],
            ]
        ],
    ) -> None:
        """Replace committed custom-entry rows with a freshly rendered branch.

        Pi clears and rebuilds the chat when an interactive session switch
        completes. Pipy keeps normal scrollback committed, but extension custom
        entries are live renderer snapshots and need the same active-branch
        replacement semantics on ``/resume``. ``entries`` contains already
        rendered/sanitized rows tagged as ``plain`` (label + sanitized body) or
        ``styled`` (renderer-owned SGR-safe rows).
        """

        replacement_blocks = [self._redraw_replacement_block(row) for row in entries]
        with self._paint_lock:
            self._settle_reasoning_locked()
            self.working_text = ""
            self.tool_output_text = ""
            next_replacement = iter(replacement_blocks)
            rebuilt: list[HistoryBlock] = []
            inserted_remaining = False
            for block in self.history_blocks:
                if block[0] not in {"custom", "custom_message_custom"}:
                    rebuilt.append(block)
                    continue
                if not inserted_remaining:
                    replacement = next(next_replacement, None)
                    if replacement is not None:
                        rebuilt.append(replacement)
                        continue
                    inserted_remaining = True
            rebuilt.extend(next_replacement)
            self.history_blocks = rebuilt
        self._reset_scrollback()

    def _redraw_replacement_block(
        self,
        row: tuple[str, str, tuple[str, ...]]
        | tuple[
            str,
            str,
            tuple[str, ...],
            object | None,
            Mapping[str, RegisteredMessageRenderer]
            | Mapping[str, RegisteredEntryRenderer],
        ],
    ) -> HistoryBlock:
        render_kind, custom_type, lines = row[:3]
        if render_kind in {"styled", "entry"}:
            rendered_lines = tuple(lines) or ("",)
            state = None
            if len(row) >= 5:
                state = CustomMessageRenderState(
                    custom_type=str(custom_type),
                    data=row[3],
                    renderers=(
                        {}
                        if render_kind == "entry"
                        else cast(Mapping[str, RegisteredMessageRenderer], row[4])
                    ),
                    styled=True,
                    lines=rendered_lines,
                    entry_renderers=(
                        cast(Mapping[str, RegisteredEntryRenderer], row[4])
                        if render_kind == "entry"
                        else None
                    ),
                )
            return HistoryBlockTuple("custom_message_custom", rendered_lines, state)
        label = sanitize_label_text(str(custom_type).strip()) or "custom"
        safe_lines = tuple(sanitize_label_text(line) for line in lines) or ("",)
        return HistoryBlockTuple("custom", (f"[{label}]", *safe_lines))

    # -- view flags (Ctrl+O) and retained rich-row refresh ---------------------

    def set_tools_expanded(self, expanded: bool) -> None:
        """Set the Ctrl+O expansion flag and refresh retained rich rows.

        Bundles the flag write and the rerender in one lock section so no
        frame paints against the new flag with stale retained rows. Changed
        rows are redrawn with the terminal scrollback cleared, as Pi's full
        render does when a row above the viewport changes, so the collapsed
        copy does not stay above the expanded one.
        """

        with self._paint_lock:
            self.tools_expanded = bool(expanded)
            changed = self._rerender_custom_messages_locked()
        if changed:
            self._replace_scrollback()
        else:
            self._repaint()

    def rerender_custom_messages(self) -> None:
        """Refresh retained rich custom-message rows for the current view flag."""

        with self._paint_lock:
            changed = self._rerender_custom_messages_locked()
        if changed:
            self._reset_scrollback()
        else:
            self._repaint()

    def _rerender_custom_messages_locked(self) -> bool:
        width = self._render_inputs.width()
        theme = self._render_inputs.theme()
        changed = False
        rebuilt: list[HistoryBlock] = []
        for block in self.history_blocks:
            kind, lines = block
            state = getattr(block, "state", None)
            if isinstance(state, SummaryRenderState):
                next_lines = state.expanded if self.tools_expanded else state.collapsed
                changed = changed or next_lines != lines
                rebuilt.append(HistoryBlockTuple(kind, next_lines, state))
                continue
            if isinstance(state, ToolRowState | SummaryBox):
                # Pi `setExpanded` on every ToolExecutionComponent and summary.
                next_lines = self._tool_row_lines(state)
                changed = changed or next_lines != lines
                rebuilt.append(HistoryBlockTuple(kind, next_lines, state))
                continue
            if state is None:
                rebuilt.append(HistoryBlockTuple(kind, lines, state))
                continue
            state = cast(CustomMessageRenderState, state)
            next_block = self._rerendered_block(state, width=width, theme=theme)
            changed = changed or next_block[:2] != (kind, lines)
            rebuilt.append(next_block)
        if changed:
            self.history_blocks = rebuilt
        if self.pending_tool is not None:
            # The live row only needs a repaint, not a scrollback redraw.
            self.pending_tool_lines = self._tool_row_lines(self.pending_tool)
        return changed

    def _rerendered_block(
        self,
        state: CustomMessageRenderState,
        *,
        width: int,
        theme: object,
    ) -> HistoryBlockTuple:
        if state.entry_renderers is not None:
            entry = state.data if isinstance(state.data, Mapping) else {}
            rendered = render_extension_entry(
                state.entry_renderers,
                entry,
                width=width,
                expanded=self.tools_expanded,
                theme=theme,
            )
            if rendered is None:
                next_state = CustomMessageRenderState(
                    custom_type=state.custom_type,
                    data=state.data,
                    renderers={},
                    styled=True,
                    lines=(),
                    entry_renderers=state.entry_renderers,
                )
                # An omitted live row: empty lines replace whatever was shown.
                return HistoryBlockTuple("custom_message_custom", (), next_state)
        else:
            rendered = render_extension_message(
                state.renderers,
                state.custom_type,
                state.data,
                width=width,
                expanded=self.tools_expanded,
                theme=theme,
            )
        if rendered.styled:
            next_kind = "custom_message_custom"
            next_lines = tuple(rendered.lines) or ("",)
            styled = True
        else:
            label = sanitize_label_text(str(state.custom_type).strip()) or "custom"
            safe_lines = tuple(
                sanitize_label_text(line) for line in rendered.lines
            ) or ("",)
            next_kind = "custom"
            next_lines = (f"[{label}]", *safe_lines)
            styled = False
        next_state = CustomMessageRenderState(
            custom_type=state.custom_type,
            data=state.data,
            renderers=state.renderers,
            styled=styled,
            lines=next_lines,
            entry_renderers=state.entry_renderers,
        )
        return HistoryBlockTuple(next_kind, next_lines, next_state)

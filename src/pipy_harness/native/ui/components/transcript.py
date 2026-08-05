"""The transcript: committed history blocks plus the live stream buffers.

Same ownership contract as the sibling components, with one difference: the
transcript's state never lived on a shared record, so the component owns its
fields directly -- the committed ``history_blocks`` list, the four live stream
buffers (``assistant_text``, ``reasoning_text``, ``tool_output_text``,
``working_text``), the Ctrl+T thinking-fold trio (``thinking_hidden``,
``hidden_thinking_label``, ``deferred_reasoning``) and the Ctrl+O
``tools_expanded`` flag. The terminal shell keeps thin facade projections over
these fields for its frame snapshot and for the renderer adapters that later
slices repoint.

Every verb applies its whole transition in ONE :class:`PaintLock` section --
mutating painters share that reentrant lock, so a concurrent frame never
observes a half-applied commit (an assistant buffer cleared but its history
block not yet appended). Repainting happens *outside* the lock through the
injected ``repaint`` callable. Two verbs replace committed rows wholesale
(:meth:`redraw_custom_entries`, :meth:`rerender_custom_messages`); those call
the injected ``reset_scrollback`` callable instead -- the screen-owned
full-redraw that clears inline-scrollback bookkeeping stays in the shell and
is the component's only effectful port besides repainting. ``frame_width`` and
``render_theme`` are read-only providers for the retained rich-row rerender.

Two verbs deliberately do not repaint: :meth:`discard_working_text` and
:meth:`reset_hidden_thinking_label` run inside a caller's enclosing lock
section (extension-chrome transitions) whose caller paints once at the end.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Self, cast

from pipy_harness.native.extension_runtime import (
    RegisteredEntryRenderer,
    RegisteredMessageRenderer,
    render_extension_entry,
    render_extension_message,
)
from pipy_harness.native.session_tree_commands import sanitize_label_text
from pipy_harness.native.ui.paint_lock import PaintLock

# Pi's default label shown in place of live reasoning while thinking is folded.
DEFAULT_HIDDEN_THINKING_LABEL = "Thinking..."
# Live streaming tool output stays character-bounded before the pure frame
# renderer applies its row-tail policy.
_TOOL_STREAM_LIVE_MAX_CHARS = 8 * 1024

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


def _compact_read_header(header: str) -> str:
    return re.sub(r":\d+-\d+(?:\s+\(ctrl\+o to expand\))?$", "", header)


class TranscriptComponent:
    """Owner of committed history and live stream state behind the frame."""

    def __init__(
        self,
        paint_lock: PaintLock,
        repaint: Callable[[], None],
        *,
        reset_scrollback: Callable[[], None],
        frame_width: Callable[[], int],
        render_theme: Callable[[], object],
    ) -> None:
        self._paint_lock = paint_lock
        self._repaint = repaint
        self._reset_scrollback = reset_scrollback
        self._frame_width = frame_width
        self._render_theme = render_theme
        self.history_blocks: list[HistoryBlock] = []
        self.assistant_text = ""
        self.reasoning_text = ""
        self.tool_output_text = ""
        self.working_text = ""
        self.thinking_hidden = False
        self.hidden_thinking_label = DEFAULT_HIDDEN_THINKING_LABEL
        self.tools_expanded = False
        # Reasoning blocks that settled while thinking was folded (Ctrl+T).
        # Retained rather than dropped so toggling visibility back reveals them
        # (committed fresh at toggle time, not retro-written into scrollback).
        self.deferred_reasoning: list[str] = []

    # -- seeding -------------------------------------------------------------

    def seed_history(self, blocks: Iterable[HistoryBlock]) -> None:
        """Seed startup blocks once; a non-empty transcript stays untouched."""

        with self._paint_lock:
            if not self.history_blocks:
                self.history_blocks.extend(blocks)

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

    def set_working(self, text: str) -> None:
        with self._paint_lock:
            self.working_text = text
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
            self.history_blocks.append(
                HistoryBlockTuple("error", ("Operation aborted",))
            )
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
        with self._paint_lock:
            self._settle_reasoning_locked()
            safe_lines = tuple(
                sanitize_label_text(line) for line in str(text).splitlines()
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
        with self._paint_lock:
            self._settle_reasoning_locked()
            self.working_text = ""
            self.tool_output_text = ""
            if header.startswith("read ") or header.startswith("read resource "):
                self.history_blocks.append(
                    HistoryBlockTuple("tool_read", (_compact_read_header(header),))
                )
            else:
                self.history_blocks.append(HistoryBlockTuple("tool", (header,)))
        self._repaint()

    def append_tool_output(self, chunk: str) -> None:
        """Stream incremental tool output into the live region as produced.

        Used by long-running tools (`bash`) so the live frame shows e.g. pytest
        dots scrolling in real time, matching Pi. Only a bounded tail is kept
        live; the full bounded result is committed by `add_tool_result` when
        the tool settles.
        """

        if not chunk:
            return
        with self._paint_lock:
            self._settle_reasoning_locked()
            self.working_text = ""
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

    def add_tool_call_custom(self, lines: Iterable[str]) -> None:
        """Commit extension-rendered call-row lines (pre-styled, SGR-safe)."""

        with self._paint_lock:
            self._settle_reasoning_locked()
            self.working_text = ""
            self.tool_output_text = ""
            self.history_blocks.append(
                HistoryBlockTuple("tool_call_custom", tuple(lines) or ("",))
            )
        self._repaint()

    def add_tool_result_custom(
        self, lines: Iterable[str], *, duration_seconds: float | None = None
    ) -> None:
        """Commit extension-rendered result-row lines (pre-styled, SGR-safe)."""

        with self._paint_lock:
            self._settle_reasoning_locked()
            self.tool_output_text = ""
            rendered = list(lines)
            if duration_seconds is not None:
                rendered.extend(("", f"Took {duration_seconds:.1f}s"))
            self.history_blocks.append(
                HistoryBlockTuple("tool_result_custom", tuple(rendered or [""]))
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
        frame paints against the new flag with stale retained rows.
        """

        with self._paint_lock:
            self.tools_expanded = bool(expanded)
            changed = self._rerender_custom_messages_locked()
        if changed:
            self._reset_scrollback()
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
        width = self._frame_width()
        theme = self._render_theme()
        changed = False
        rebuilt: list[HistoryBlock] = []
        for block in self.history_blocks:
            kind, lines = block
            state = cast(CustomMessageRenderState | None, getattr(block, "state", None))
            if state is None:
                rebuilt.append(HistoryBlockTuple(kind, lines, state))
                continue
            next_block = self._rerendered_block(state, width=width, theme=theme)
            changed = changed or next_block[:2] != (kind, lines)
            rebuilt.append(next_block)
        if changed:
            self.history_blocks = rebuilt
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

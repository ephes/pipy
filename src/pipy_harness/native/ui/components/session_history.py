"""Render the active session branch into the transcript.

Pi's ``renderInitialMessages`` (``interactive-mode.ts:4084``): at startup,
after every session replacement (``/resume``, ``/new``, ``/fork``,
``/clone``, import) and after ``/tree`` navigation, Pi clears the chat and
renders ``buildContextEntries()`` again. pipy used to leave the transcript as
it was, so a resumed conversation showed nothing.

The owner replays the branch through the same verbs that draw live turns --
a :class:`TuiToolLoopRenderer` for user, assistant and tool rows, the
:class:`CustomEntryRenderer` for extension entries -- into a scratch
transcript, then hands the finished rows to the live transcript in one
replacement. Nothing is painted half-replayed, and a replay never touches the
live renderer's in-flight tool state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentStopReason,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.content import display_segments
from pipy_harness.native.local_shell_record import (
    parse_local_shell_record,
    shell_result_lines,
)
from pipy_harness.native.session_tree import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    CustomMessageEntry,
    MessageEntry,
    NativeSessionTree,
    SessionEntry,
)
from pipy_harness.native.tool_rows import SummaryBox
from pipy_harness.native.ui.components.custom_entry_renderer import (
    CustomEntryRenderer,
    CustomEntryTerminalTarget,
    CustomRendererProjectionSnapshot,
)
from pipy_harness.native.ui.components.tool_loop_renderer import TuiToolLoopRenderer
from pipy_harness.native.ui.components.transcript import (
    HistoryBlock,
    TranscriptComponent,
)
from pipy_harness.native.ui.paint_lock import PaintLock
from pipy_harness.native.ui.screen import ScreenRenderInputs


def compaction_summary_box(entry: CompactionEntry) -> SummaryBox:
    """Pi ``CompactionSummaryMessageComponent``'s texts."""

    tokens = f"{entry.tokens_before:,}"
    return SummaryBox(
        label="[compaction]",
        collapsed=f"Compacted from {tokens} tokens (",
        header=f"Compacted from {tokens} tokens",
        summary=entry.summary,
    )


def branch_summary_box(entry: BranchSummaryEntry) -> SummaryBox:
    """Pi ``BranchSummaryMessageComponent``'s texts."""

    return SummaryBox(
        label="[branch]",
        collapsed="Branch summary (",
        header="Branch Summary",
        summary=entry.summary,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionHistoryRenderer:
    """Replace the transcript's conversation with the active branch."""

    session_tree: Callable[[], NativeSessionTree]
    transcript: TranscriptComponent
    # The screen's one paint lock; the scratch transcript shares it.
    paint_lock: PaintLock
    render_inputs: ScreenRenderInputs
    tool_renderer: TuiToolLoopRenderer
    custom_renderer: CustomEntryRenderer

    def render_active_branch(self) -> None:
        # The tree's guarded API reads entries and leaf under its write lock.
        entries = self.session_tree().build_context_entries()
        self.transcript.replace_conversation(self.render_entries(entries))

    def render_after_compaction(
        self, pending_user: AgentUserMessage | None = None
    ) -> bool:
        """Pi ``compaction_end``: redraw the chat after a completed compaction.

        The kept entries are drawn, then the compaction row last, at its
        chronological position (the context lists it first). pipy compacts
        before a request, so the prompt being sent (``pending_user``) may not
        be recorded yet; it is drawn after the kept entries unless the branch
        already holds it, as Pi's kept entries end with the latest prompt.
        ``False`` when the branch does not start with a compaction (it was not
        recorded), so the caller can show its notice instead.
        """

        entries = self.session_tree().build_context_entries()
        if not entries or not isinstance(entries[0], CompactionEntry):
            return False
        kept = entries[1:]
        if pending_user is not None and any(
            isinstance(entry, MessageEntry) and entry.message is pending_user
            for entry in kept
        ):
            pending_user = None
        blocks = self.render_entries(kept, pending_user=pending_user)
        blocks.extend(self.render_entries(entries[:1]))
        self.transcript.replace_conversation(blocks)
        return True

    def render_entries(
        self,
        entries: list[SessionEntry],
        *,
        pending_user: AgentUserMessage | None = None,
    ) -> list[HistoryBlock]:
        """Replay ``entries`` (then ``pending_user``) into a scratch transcript
        and return its rows."""

        scratch = TranscriptComponent(
            self.paint_lock,
            lambda: None,
            reset_scrollback=lambda: None,
            render_inputs=self.render_inputs,
        )
        scratch.tools_expanded = self.transcript.tools_expanded
        # Thinking rows follow the live fold and label (Pi hideThinkingBlock).
        scratch.thinking_hidden = self.transcript.thinking_hidden
        scratch.hidden_thinking_label = self.transcript.hidden_thinking_label
        renderer = self.tool_renderer.detached(scratch)
        custom = replace(
            self.custom_renderer,
            terminal=CustomEntryTerminalTarget(
                transcript=scratch, render_inputs=self.render_inputs
            ),
        )
        # One extension generation renders the whole branch; taken only when
        # the branch has an extension entry to render.
        projection: CustomRendererProjectionSnapshot | None = None
        results = _tool_results_by_call(entries)
        for index, entry in enumerate(entries):
            if isinstance(entry, MessageEntry):
                _render_message(
                    entry.message, renderer, scratch, results.get(index, {})
                )
            elif isinstance(entry, CompactionEntry):
                scratch.add_summary_box(compaction_summary_box(entry))
            elif isinstance(entry, BranchSummaryEntry) and entry.summary:
                scratch.add_summary_box(branch_summary_box(entry))
            elif isinstance(entry, CustomEntry):
                projection = projection or custom.renderer_projection()
                custom.add_rendered_custom_entry_to_terminal(entry, projection)
            elif isinstance(entry, CustomMessageEntry) and entry.display:
                projection = projection or custom.renderer_projection()
                custom.add_custom_message_entry_to_terminal(entry, projection)
        if pending_user is not None:
            _render_message(pending_user, renderer, scratch, {})
        return list(scratch.history_blocks)


def _tool_results_by_call(
    entries: list[SessionEntry],
) -> dict[int, dict[str, AgentToolResultMessage]]:
    """Map each assistant entry's index to the results of its tool calls.

    Pi ``renderSessionItems`` keeps every unanswered call pending for the
    whole render and completes it with the first later result naming its id,
    whatever entries come between. A later call reusing an id takes it over.
    """

    matched: dict[int, dict[str, AgentToolResultMessage]] = {}
    pending: dict[str, int] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, MessageEntry):
            continue
        message = entry.message
        if isinstance(message, AgentAssistantMessage):
            matched[index] = {}
            for call in message.tool_calls:
                pending[call.provider_correlation_id] = index
        elif isinstance(message, AgentToolResultMessage):
            owner = pending.pop(message.provider_correlation_id, None)
            if owner is not None:
                matched[owner][message.provider_correlation_id] = message
    return matched


def _render_message(
    message: object,
    renderer: TuiToolLoopRenderer,
    scratch: TranscriptComponent,
    results: dict[str, AgentToolResultMessage],
) -> None:
    if isinstance(message, AgentUserMessage):
        record = parse_local_shell_record(message.content.value)
        if record is None:
            renderer.render_user_message(message.content.value)
            return
        # The `!` shell shortcut's own rows: `$ command`, output, status.
        scratch.add_tool_call(record.command)
        scratch.add_shell_result(
            collapsed=shell_result_lines(record, expanded=False),
            expanded=shell_result_lines(record, expanded=True),
        )
    elif isinstance(message, AgentAssistantMessage):
        _render_assistant(message, renderer, scratch, results)


def stopped_assistant_marker(message: AgentAssistantMessage) -> str | None:
    """Pi ``AssistantMessageComponent``'s line after an aborted/failed turn."""

    if message.stop_reason is AgentStopReason.ABORTED:
        if message.error_message and message.error_message != "Request was aborted":
            return message.error_message
        return "Operation aborted"
    if message.stop_reason is AgentStopReason.ERROR:
        return f"Error: {message.error_message or 'Unknown error'}"
    return None


def _stopped_call_text(message: AgentAssistantMessage) -> str:
    if message.stop_reason is AgentStopReason.ABORTED:
        return "Operation aborted"
    return message.error_message or "Error"


def _render_assistant(
    message: AgentAssistantMessage,
    renderer: TuiToolLoopRenderer,
    scratch: TranscriptComponent,
    results: dict[str, AgentToolResultMessage],
) -> None:
    has_tool_calls = bool(message.tool_calls)
    # Pi's AssistantMessageComponent: text and thinking runs in order.
    for thinking, text in display_segments(message.ordered_content()):
        if thinking:
            scratch.add_reasoning(text)
        else:
            renderer.render_buffered_assistant_text(text, has_tool_calls=has_tool_calls)
    marker = stopped_assistant_marker(message)
    if marker is not None and not has_tool_calls:
        scratch.add_error(marker)
        return
    renderer.complete_assistant_message(has_tool_calls=has_tool_calls)
    for call in message.tool_calls:
        renderer.render_tool_call(call)
        result = results.get(call.provider_correlation_id)
        if message.stop_reason is not None:
            # Pi renderSessionItems: a stopped turn's partial tool call ends
            # with the abort or error as its (error) result.
            renderer.render_tool_result(
                output_text=_stopped_call_text(message), is_error=True
            )
        elif result is not None:
            renderer.render_tool_result(
                output_text=result.content.value,
                is_error=result.is_error,
                details=result.details,
            )
        else:
            # No stored result: the row stays pending, at its own position.
            scratch.flush_pending_tool()

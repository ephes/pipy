"""Live in-session compaction for native provider-visible context.

Compaction reduces the provider-visible conversation that pipy sends on the
next request while keeping enough recent context (plus a safe, metadata-only
summary) for the model to stay coherent. It is a pure transformation over the
in-memory message/exchange values — it never reads or writes the metadata-first
session archive, never copies raw transcript sidecars, and never folds raw
prompts, model text, tool payloads, file contents, or diffs into the summary it
produces.

Two surfaces are supported:

- ``compact_tool_loop_messages`` operates on the tool-loop ``LoopMessage`` list
  (``UserMessage`` / ``AssistantMessage`` / ``ToolResultMessage``). It cuts the
  history only at *user-turn group boundaries* — the index of each
  ``UserMessage`` — so a retained ``ToolResultMessage`` is always preceded by
  the ``AssistantMessage`` that emitted the matching tool call. This preserves
  provider message-protocol validity: compaction cannot orphan a tool result,
  reorder a tool-call/observation pair, or split a group.
- ``compact_no_tool_context`` operates on the no-tool REPL's bounded
  ``NativeNoToolReplConversationContext`` exchanges.

The ``summary_block`` each result carries is **metadata only** (counts of the
dropped turns). It is safe to inject into a provider system prompt and safe to
record (as counters) into the archive. The dropped raw content is simply
discarded from the in-memory provider context.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from pipy_harness.native.conversation import NativeNoToolReplConversationContext
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    LoopMessage,
    ToolResultMessage,
    UserMessage,
)

# Keep this many of the most recent user-turn groups (tool-loop) or exchanges
# (no-tool) intact when compacting. Two recent turns plus the safe summary keep
# the next provider request coherent without re-sending stale history.
DEFAULT_KEEP_RECENT_GROUPS = 2
DEFAULT_KEEP_RECENT_EXCHANGES = 2

# Automatic thresholds. The tool loop can accumulate many tool-result messages
# in a single user turn, so the message-count threshold is the primary trigger;
# the byte threshold catches a few very large turns.
DEFAULT_TOOL_LOOP_MAX_MESSAGES = 40
DEFAULT_TOOL_LOOP_MAX_BYTES = 48 * 1024
DEFAULT_NO_TOOL_MAX_EXCHANGES = 4
DEFAULT_NO_TOOL_MAX_BYTES = 3072


@dataclass(frozen=True, slots=True)
class ToolLoopCompactionResult:
    """Result of compacting a tool-loop ``LoopMessage`` history."""

    messages: tuple[LoopMessage, ...]
    summary_block: str
    changed: bool
    dropped_group_count: int
    dropped_message_count: int
    dropped_user_count: int
    dropped_assistant_count: int
    dropped_tool_call_count: int
    dropped_tool_result_count: int
    retained_group_count: int
    retained_message_count: int
    bytes_before: int
    bytes_after: int

    def safe_metadata(self) -> dict[str, object]:
        """Return metadata-only counters safe for the session archive."""

        return {
            "compaction_applied": self.changed,
            "compaction_dropped_group_count": self.dropped_group_count,
            "compaction_dropped_message_count": self.dropped_message_count,
            "compaction_dropped_user_count": self.dropped_user_count,
            "compaction_dropped_assistant_count": self.dropped_assistant_count,
            "compaction_dropped_tool_call_count": self.dropped_tool_call_count,
            "compaction_dropped_tool_result_count": self.dropped_tool_result_count,
            "compaction_retained_group_count": self.retained_group_count,
            "compaction_retained_message_count": self.retained_message_count,
            "compaction_bytes_before": self.bytes_before,
            "compaction_bytes_after": self.bytes_after,
        }


@dataclass(frozen=True, slots=True)
class NoToolCompactionResult:
    """Result of compacting the no-tool REPL conversation context."""

    context: NativeNoToolReplConversationContext
    summary_block: str
    changed: bool
    dropped_exchange_count: int
    retained_exchange_count: int
    bytes_before: int
    bytes_after: int

    def safe_metadata(self) -> dict[str, object]:
        """Return metadata-only counters safe for the session archive."""

        return {
            "compaction_applied": self.changed,
            "compaction_dropped_exchange_count": self.dropped_exchange_count,
            "compaction_retained_exchange_count": self.retained_exchange_count,
            "compaction_bytes_before": self.bytes_before,
            "compaction_bytes_after": self.bytes_after,
        }


def _message_bytes(message: LoopMessage) -> int:
    if isinstance(message, UserMessage):
        return len(message.content.encode("utf-8"))
    if isinstance(message, AssistantMessage):
        total = len(message.content.encode("utf-8"))
        for call in message.tool_calls:
            total += len(call.arguments_json.encode("utf-8"))
            total += len(call.tool_name.encode("utf-8"))
        return total
    if isinstance(message, ToolResultMessage):
        return len(message.output_text.encode("utf-8"))
    return 0


def _messages_bytes(messages: tuple[LoopMessage, ...] | list[LoopMessage]) -> int:
    return sum(_message_bytes(message) for message in messages)


def _user_group_boundaries(messages: list[LoopMessage]) -> list[int]:
    return [
        index
        for index, message in enumerate(messages)
        if isinstance(message, UserMessage)
    ]


def compact_tool_loop_messages(
    messages: list[LoopMessage] | tuple[LoopMessage, ...],
    *,
    keep_recent_groups: int = DEFAULT_KEEP_RECENT_GROUPS,
) -> ToolLoopCompactionResult:
    """Compact a tool-loop message history at user-turn group boundaries.

    The most recent ``keep_recent_groups`` user-turn groups are retained
    verbatim; earlier groups are dropped and replaced by a metadata-only
    ``summary_block``. The cut is always at a ``UserMessage`` index, so the
    retained history begins with a user turn and no tool result is orphaned.
    """

    if keep_recent_groups < 1:
        raise ValueError("keep_recent_groups must be >= 1")

    message_list = list(messages)
    bytes_before = _messages_bytes(message_list)
    boundaries = _user_group_boundaries(message_list)

    if len(boundaries) <= keep_recent_groups:
        return ToolLoopCompactionResult(
            messages=tuple(message_list),
            summary_block="",
            changed=False,
            dropped_group_count=0,
            dropped_message_count=0,
            dropped_user_count=0,
            dropped_assistant_count=0,
            dropped_tool_call_count=0,
            dropped_tool_result_count=0,
            retained_group_count=len(boundaries),
            retained_message_count=len(message_list),
            bytes_before=bytes_before,
            bytes_after=bytes_before,
        )

    cut_index = boundaries[len(boundaries) - keep_recent_groups]
    dropped = message_list[:cut_index]
    retained = message_list[cut_index:]

    dropped_user = sum(1 for m in dropped if isinstance(m, UserMessage))
    dropped_assistant = sum(1 for m in dropped if isinstance(m, AssistantMessage))
    dropped_tool_results = sum(
        1 for m in dropped if isinstance(m, ToolResultMessage)
    )
    dropped_tool_calls = sum(
        len(m.tool_calls) for m in dropped if isinstance(m, AssistantMessage)
    )
    dropped_group_count = len(boundaries) - keep_recent_groups

    summary_block = _tool_loop_summary_block(
        dropped_group_count=dropped_group_count,
        dropped_assistant_count=dropped_assistant,
        dropped_tool_call_count=dropped_tool_calls,
    )

    return ToolLoopCompactionResult(
        messages=tuple(retained),
        summary_block=summary_block,
        changed=True,
        dropped_group_count=dropped_group_count,
        dropped_message_count=len(dropped),
        dropped_user_count=dropped_user,
        dropped_assistant_count=dropped_assistant,
        dropped_tool_call_count=dropped_tool_calls,
        dropped_tool_result_count=dropped_tool_results,
        retained_group_count=keep_recent_groups,
        retained_message_count=len(retained),
        bytes_before=bytes_before,
        bytes_after=_messages_bytes(retained),
    )


def should_compact_tool_loop_messages(
    messages: list[LoopMessage] | tuple[LoopMessage, ...],
    *,
    max_messages: int = DEFAULT_TOOL_LOOP_MAX_MESSAGES,
    max_bytes: int = DEFAULT_TOOL_LOOP_MAX_BYTES,
    keep_recent_groups: int = DEFAULT_KEEP_RECENT_GROUPS,
) -> bool:
    """Return whether the tool-loop history should be auto-compacted.

    Returns ``True`` only when the history is over a threshold *and* there is
    something to drop (more user-turn groups than ``keep_recent_groups``).
    """

    message_list = list(messages)
    boundaries = _user_group_boundaries(message_list)
    if len(boundaries) <= keep_recent_groups:
        return False
    if len(message_list) > max_messages:
        return True
    if _messages_bytes(message_list) > max_bytes:
        return True
    return False


def compact_no_tool_context(
    context: NativeNoToolReplConversationContext,
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT_EXCHANGES,
) -> NoToolCompactionResult:
    """Compact the no-tool REPL conversation context.

    Retains the most recent ``keep_recent`` exchanges and drops the rest,
    returning a metadata-only ``summary_block``. The provider-visible byte and
    exchange bounds are preserved because the retained tail is a subset of an
    already-bounded value.
    """

    if keep_recent < 1:
        raise ValueError("keep_recent must be >= 1")

    bytes_before = context.byte_count
    exchanges = context.exchanges
    if len(exchanges) <= keep_recent:
        return NoToolCompactionResult(
            context=context,
            summary_block="",
            changed=False,
            dropped_exchange_count=0,
            retained_exchange_count=len(exchanges),
            bytes_before=bytes_before,
            bytes_after=bytes_before,
        )

    retained = exchanges[-keep_recent:]
    dropped_count = len(exchanges) - len(retained)
    new_context = replace(context, exchanges=retained)
    summary_block = _no_tool_summary_block(dropped_exchange_count=dropped_count)
    return NoToolCompactionResult(
        context=new_context,
        summary_block=summary_block,
        changed=True,
        dropped_exchange_count=dropped_count,
        retained_exchange_count=len(retained),
        bytes_before=bytes_before,
        bytes_after=new_context.byte_count,
    )


def should_compact_no_tool_context(
    context: NativeNoToolReplConversationContext,
    *,
    max_exchanges: int = DEFAULT_NO_TOOL_MAX_EXCHANGES,
    max_bytes: int = DEFAULT_NO_TOOL_MAX_BYTES,
    keep_recent: int = DEFAULT_KEEP_RECENT_EXCHANGES,
) -> bool:
    """Return whether the no-tool context should be auto-compacted."""

    if len(context.exchanges) <= keep_recent:
        return False
    if len(context.exchanges) > max_exchanges:
        return True
    if context.byte_count > max_bytes:
        return True
    return False


def _tool_loop_summary_block(
    *,
    dropped_group_count: int,
    dropped_assistant_count: int,
    dropped_tool_call_count: int,
) -> str:
    return (
        "[Context compacted to save space: "
        f"{dropped_group_count} earlier exchange(s) "
        f"({dropped_assistant_count} assistant turn(s), "
        f"{dropped_tool_call_count} tool call(s)) were summarized and removed "
        "from this request. Their details are no longer available; continue "
        "from the retained recent turns below.]"
    )


def _no_tool_summary_block(*, dropped_exchange_count: int) -> str:
    return (
        "[Context compacted to save space: "
        f"{dropped_exchange_count} earlier exchange(s) were summarized and "
        "removed from this request. Continue from the retained recent turns "
        "below.]"
    )

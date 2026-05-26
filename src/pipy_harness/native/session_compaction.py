"""Bounded LLM-summarize compaction pass for the native tool loop.

Pi exposes session compaction through `agent-session.ts` to shrink an
oversized message envelope back into bounded form. Pipy slopforks the
useful subset — one bounded provider call that returns a single
summary `AssistantMessage` standing in for several older turns —
through pipy-owned Python boundaries, not as a literal port.

The boundary is intentionally narrow:

- `compact_loop_messages(messages, *, provider, keep_tail, ...)`
  inspects a `tuple[LoopMessage, ...]` and, when the total message
  count exceeds the configured ceiling, calls the supplied provider
  once with a deterministic compaction prompt and returns a shorter
  tuple: one synthetic `AssistantMessage` carrying the provider's
  summary text, followed by the most recent `keep_tail` messages
  unchanged. When the input is already small enough, the helper
  returns the original tuple by identity.
- `CompactionResult` carries the new message tuple plus
  archive-safe metadata about the compaction pass (counts only —
  no summary body, no raw turn text). Callers may record the
  metadata in pipy session records; the summary text itself stays
  in memory only.
- `CompactionError` is raised when the provider call fails. The
  caller is responsible for falling back to the unsummarized
  envelope; this module never silently drops turns.

`compact_loop_messages` deliberately does not call out to the model
provider unless a compaction is actually required. When the input
is bounded, the function is a constant-time identity check. The
helper is also test-friendly: any object exposing the synchronous
`ProviderPort.complete(...)` shape works, including
`FakeNativeProvider`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    LoopMessage,
    ToolResultMessage,
    UserMessage,
)


if TYPE_CHECKING:
    from pipy_harness.native.provider import ProviderPort


COMPACTION_PROMPT_LABEL: str = "compaction"
"""Provider turn label used when calling the model for a compaction."""


COMPACTION_SYSTEM_PROMPT: str = (
    "You are summarizing a multi-turn coding-agent conversation for compaction. "
    "Return a single concise paragraph that preserves user requests, decisions, "
    "and pending follow-ups. Do not include raw code, secrets, file contents, "
    "or tool payloads."
)


@dataclass(frozen=True, slots=True)
class CompactionResult:
    """Outcome of one compaction attempt over a `LoopMessage` tuple."""

    messages: tuple[LoopMessage, ...]
    original_message_count: int
    summarized_message_count: int
    kept_tail_count: int
    skipped: bool

    KEEP_TAIL_MIN: ClassVar[int] = 1
    KEEP_TAIL_MAX: ClassVar[int] = 32

    def archive_metadata(self) -> dict[str, int | bool]:
        """Return safe metadata for the pipy session archive.

        The dict carries only counts and the `skipped` flag; the
        summary text, the original turn text, and the prompt
        sent to the provider are never included.
        """

        return {
            "original_message_count": self.original_message_count,
            "summarized_message_count": self.summarized_message_count,
            "kept_tail_count": self.kept_tail_count,
            "skipped": self.skipped,
        }


class CompactionError(RuntimeError):
    """Raised when the provider summary call fails or returns no text."""


def compact_loop_messages(
    messages: tuple[LoopMessage, ...],
    *,
    provider: "ProviderPort",
    keep_tail: int = 4,
    threshold: int = 12,
    workspace_root: Path,
) -> CompactionResult:
    """Compact `messages` to at most `keep_tail + 1` entries via the provider.

    The helper is a no-op when the input is at most `threshold`
    messages long. When compaction is required, it calls
    `provider.complete(...)` once with a deterministic system prompt
    and user prompt; the returned text becomes the content of one
    synthetic `AssistantMessage` that replaces the
    `len(messages) - keep_tail` oldest entries. The most recent
    `keep_tail` messages are preserved unchanged.
    """

    if not isinstance(messages, tuple):
        raise TypeError("compact_loop_messages requires a tuple of LoopMessage")
    if keep_tail < CompactionResult.KEEP_TAIL_MIN or keep_tail > CompactionResult.KEEP_TAIL_MAX:
        raise ValueError(
            "keep_tail must be in "
            f"[{CompactionResult.KEEP_TAIL_MIN}, {CompactionResult.KEEP_TAIL_MAX}]"
        )
    if threshold <= keep_tail:
        raise ValueError("threshold must be strictly greater than keep_tail")

    original_count = len(messages)
    if original_count <= threshold:
        return CompactionResult(
            messages=messages,
            original_message_count=original_count,
            summarized_message_count=0,
            kept_tail_count=original_count,
            skipped=True,
        )

    summarize_count = original_count - keep_tail
    head = messages[:summarize_count]
    tail = messages[-keep_tail:]

    user_prompt = _build_compaction_prompt(head)

    result = provider.complete(
        ProviderRequest(
            system_prompt=COMPACTION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            provider_name=provider.name,
            model_id=provider.model_id,
            cwd=workspace_root,
            provider_turn_label=COMPACTION_PROMPT_LABEL,
        )
    )

    if result.status != HarnessStatus.SUCCEEDED or not result.final_text:
        raise CompactionError(
            "compaction provider call did not return text "
            f"(status={result.status.value})"
        )

    summary_message = AssistantMessage(content=result.final_text)
    compacted = (summary_message, *tail)

    return CompactionResult(
        messages=compacted,
        original_message_count=original_count,
        summarized_message_count=summarize_count,
        kept_tail_count=keep_tail,
        skipped=False,
    )


def _build_compaction_prompt(head: tuple[LoopMessage, ...]) -> str:
    """Render a deterministic compaction prompt from older turns.

    The shape is intentionally small: each message becomes one
    labeled line with truncated content; tool results and tool
    calls collapse to short markers. This is in-memory only;
    the rendered text never reaches the pipy session archive.
    """

    rendered: list[str] = []
    for index, message in enumerate(head):
        if isinstance(message, UserMessage):
            rendered.append(f"[{index}] user: {_truncate(message.content)}")
        elif isinstance(message, AssistantMessage):
            tool_note = (
                f" (tool_calls={len(message.tool_calls)})"
                if message.tool_calls
                else ""
            )
            rendered.append(
                f"[{index}] assistant: {_truncate(message.content)}{tool_note}"
            )
        elif isinstance(message, ToolResultMessage):
            label = "tool_error" if message.is_error else "tool_result"
            rendered.append(
                f"[{index}] {label}: {_truncate(message.output_text)}"
            )
        else:  # pragma: no cover - exhaustive over the LoopMessage union
            raise TypeError(f"unsupported LoopMessage shape: {type(message)!r}")
    return "\n".join(rendered)


def _truncate(text: str, *, max_length: int = 240) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length] + "…"

"""Session usage from the stored assistant messages (Pi ``getSessionStats``).

Pi sums the ``usage`` of every assistant message in the session file -- every
branch, and history a compaction dropped from the context -- so the totals
survive a resume and a model switch and reset only with a new session
(``core/agent-session.ts`` ``getSessionStats``, ``components/footer.ts``,
``core/usage-totals.ts``, ``core/cache-stats.ts``). The footer, RPC
``get_session_stats`` and ``/session`` all read these functions.

pipy records usage on assistant messages only: it has no usage for
compaction or branch summaries, tool results, or ``usage`` entries.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.messages import AgentMessageUsage
from pipy_harness.native.session_tree import (
    BranchSummaryEntry,
    CompactionEntry,
    MessageEntry,
    NativeSessionTree,
    SessionEntry,
    _UnresolvedToolResultMessage,
)

# Pi cache-stats.ts: per-turn misses at or below this are breakpoint noise.
_CACHE_MISS_NOISE_FLOOR_TOKENS = 1024


@dataclass(frozen=True, slots=True)
class SessionUsageTotals:
    """Pi ``UsageTotals`` plus the footer's ``latestCacheHitRate``."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0
    latest_cache_hit_rate: float | None = None


def _assistant_usages(
    entries: Sequence[SessionEntry],
) -> list[tuple[AgentAssistantMessage, AgentMessageUsage]]:
    return [
        (entry.message, entry.message.usage or AgentMessageUsage())
        for entry in entries
        if isinstance(entry, MessageEntry)
        and isinstance(entry.message, AgentAssistantMessage)
    ]


def usage_totals(entries: Sequence[SessionEntry]) -> SessionUsageTotals:
    """Sum every stored assistant usage (Pi ``addUsageToTotals``)."""

    input_tokens = output = cache_read = cache_write = 0
    cost = 0.0
    latest_rate: float | None = None
    for _message, usage in _assistant_usages(entries):
        input_tokens += usage.input
        output += usage.output
        cache_read += usage.cache_read
        cache_write += usage.cache_write
        cost += usage.cost.total
        prompt = usage.input + usage.cache_read + usage.cache_write
        latest_rate = usage.cache_read / prompt * 100 if prompt > 0 else None
    return SessionUsageTotals(
        input=input_tokens,
        output=output,
        cache_read=cache_read,
        cache_write=cache_write,
        cost=cost,
        latest_cache_hit_rate=latest_rate,
    )


def session_stats(tree: NativeSessionTree) -> dict[str, Any]:
    """Pi ``SessionStats`` over every stored entry (RPC ``get_session_stats``).

    ``contextUsage`` is not reported (see ``docs/automation-rpc.md``).
    """

    entries = tree.get_entries()
    user = assistant = tool_results = tool_calls = total = 0
    for entry in entries:
        if not isinstance(entry, MessageEntry):
            continue
        total += 1
        message = entry.message
        if isinstance(message, AgentUserMessage):
            user += 1
        elif isinstance(message, AgentAssistantMessage):
            assistant += 1
            tool_calls += len(message.tool_calls)
        elif isinstance(
            message, (AgentToolResultMessage, _UnresolvedToolResultMessage)
        ):
            tool_results += 1
    totals = usage_totals(entries)
    return {
        "sessionFile": str(tree.path) if tree.path is not None else None,
        "sessionId": tree.session_id,
        "userMessages": user,
        "assistantMessages": assistant,
        "toolCalls": tool_calls,
        "toolResults": tool_results,
        "totalMessages": total,
        "tokens": {
            "input": totals.input,
            "output": totals.output,
            "cacheRead": totals.cache_read,
            "cacheWrite": totals.cache_write,
            "total": totals.input
            + totals.output
            + totals.cache_read
            + totals.cache_write,
        },
        "cost": totals.cost,
    }


@dataclass(frozen=True, slots=True)
class UsageCostBreakdownEntry:
    key: str
    cost: float
    tokens: int


def usage_cost_breakdown(
    entries: Sequence[SessionEntry],
) -> list[UsageCostBreakdownEntry]:
    """Pi ``getUsageCostBreakdown``: usage grouped by ``provider/model``.

    A message without attribution has no recorded usage (the loop stores both
    together), so it would form an empty group, which Pi drops anyway.
    """

    groups: dict[str, list[float]] = {}
    for message, usage in _assistant_usages(entries):
        if message.provider is None or message.model is None:
            continue
        group = groups.setdefault(f"{message.provider}/{message.model}", [0.0, 0])
        group[0] += usage.cost.total
        group[1] += usage.input + usage.output + usage.cache_read + usage.cache_write
    breakdown = [
        UsageCostBreakdownEntry(key, cost, int(tokens))
        for key, (cost, tokens) in groups.items()
        if cost > 0 or tokens > 0
    ]
    # Pi sorts by cost, descending; Array.prototype.sort is stable.
    breakdown.sort(key=lambda entry: -entry.cost)
    return breakdown


@dataclass(frozen=True, slots=True)
class CacheWasteTotals:
    missed_tokens: int = 0
    missed_cost: float = 0.0
    miss_count: int = 0


def cache_waste(
    entries: Sequence[SessionEntry],
    cache_read_rate: Callable[[str, str], float],
) -> CacheWasteTotals:
    """Pi ``computeCacheWaste``: prompt tokens re-billed instead of read.

    ``cache_read_rate(provider, model)`` is the row's cache-read price in
    dollars per million tokens (Pi ``models.getModel(...).cost.cacheRead``),
    used when the missing turn read nothing from the cache.
    """

    prev_prompt: int | None = None
    reported_cache = False
    missed_tokens = 0
    missed_cost = 0.0
    miss_count = 0
    for entry in entries:
        if isinstance(entry, (CompactionEntry, BranchSummaryEntry)):
            # The context legitimately changed; the next prompt is new content.
            prev_prompt = None
            reported_cache = False
            continue
        if not (
            isinstance(entry, MessageEntry)
            and isinstance(entry.message, AgentAssistantMessage)
        ):
            continue
        message = entry.message
        usage = message.usage or AgentMessageUsage()
        prompt = usage.input + usage.cache_read + usage.cache_write
        cached = usage.cache_read + usage.cache_write
        if prev_prompt is not None and prompt > 0 and (cached > 0 or reported_cache):
            missed = min(prev_prompt, prompt) - usage.cache_read
            if missed > _CACHE_MISS_NOISE_FLOOR_TOKENS:
                paid_tokens = usage.input + usage.cache_write
                paid_per_token = (
                    (usage.cost.input + usage.cost.cache_write) / paid_tokens
                    if paid_tokens > 0
                    else 0.0
                )
                if usage.cache_read > 0:
                    read_per_token = usage.cost.cache_read / usage.cache_read
                elif message.provider is not None and message.model is not None:
                    read_per_token = (
                        cache_read_rate(message.provider, message.model) / 1_000_000
                    )
                else:
                    read_per_token = 0.0
                missed_tokens += missed
                missed_cost += missed * max(0.0, paid_per_token - read_per_token)
                miss_count += 1
        if prompt > 0:
            # Pi asPreviousRequest: the sticky flag rides on the new request.
            reported_cache = reported_cache or cached > 0
            prev_prompt = prompt
    return CacheWasteTotals(missed_tokens, missed_cost, miss_count)


# -- Pi number formatting --------------------------------------------------


def to_fixed(value: float, digits: int) -> str:
    """JavaScript ``Number.prototype.toFixed`` for finite nonnegative values.

    JS rounds the float's exact binary value half up, so the ``Decimal`` is
    built from the float itself: ``to_fixed(0.0045, 3)`` is ``0.004`` (stored
    just below the tie) and ``to_fixed(0.0625, 3)`` is ``0.063`` (an exact
    tie).
    """

    quantum = Decimal(1).scaleb(-digits)
    return str(Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP))


def _js_round(value: float) -> int:
    return math.floor(value + 0.5)


def format_tokens(count: int) -> str:
    """Pi ``formatTokens`` (``components/footer.ts``)."""

    if count < 1000:
        return str(count)
    if count < 10000:
        return f"{to_fixed(count / 1000, 1)}k"
    if count < 1000000:
        return f"{_js_round(count / 1000)}k"
    if count < 10000000:
        return f"{to_fixed(count / 1000000, 1)}M"
    return f"{_js_round(count / 1000000)}M"


def _locale(count: int) -> str:
    """``Number.prototype.toLocaleString()`` in an en-US locale."""

    return f"{count:,}"


# -- /session ---------------------------------------------------------------


def format_session_info(
    tree: NativeSessionTree,
    *,
    selected_model: tuple[str, str],
    cache_read_rate: Callable[[str, str], float],
) -> str:
    """Pi's ``/session`` block (``interactive-mode.ts`` ``handleSessionCommand``).

    Pi's ``Cache Warming`` section is left out: pipy has no cache warming.
    """

    stats = session_stats(tree)
    entries = tree.get_entries()
    waste = cache_waste(entries, cache_read_rate)
    breakdown = usage_cost_breakdown(entries)
    lines = ["Session Info", ""]
    if tree.name:
        lines.append(f"Name: {tree.name}")
    lines.append(f"File: {stats['sessionFile'] or 'In-memory'}")
    lines.append(f"ID: {stats['sessionId']}")
    lines.append("")
    lines.append("Messages")
    lines.append(f"Total: {stats['totalMessages']}")
    lines.append(f"User: {stats['userMessages']}")
    lines.append(f"Assistant: {stats['assistantMessages']}")
    lines.append(f"Tools: {stats['toolCalls']} calls, {stats['toolResults']} results")
    lines.append("")
    lines.append("Tokens")
    tokens = stats["tokens"]
    input_tokens = tokens["input"]
    cache_read = tokens["cacheRead"]
    cache_write = tokens["cacheWrite"]
    prompt = input_tokens + cache_read + cache_write
    lines.append(f"Input: {_locale(prompt)}")
    if prompt > 0 and (cache_read > 0 or cache_write > 0):
        hit_rate = to_fixed(cache_read / prompt * 100, 1)
        lines.append(f"  Cached: {_locale(cache_read)} ({hit_rate}%)")
        written = (
            f" ({_locale(cache_write)} written to cache)" if cache_write > 0 else ""
        )
        lines.append(f"  Uncached: {_locale(input_tokens + cache_write)}{written}")
    lines.append(f"Output: {_locale(tokens['output'])}")
    lines.append(f"Total: {_locale(tokens['total'])}")
    cost = stats["cost"]
    if cost > 0 or waste.missed_tokens > 0:
        lines.append("")
        lines.append("Cost")
        lines.append(f"Total: ${to_fixed(cost, 3)}")
        selected_key = f"{selected_model[0]}/{selected_model[1]}"
        if len(breakdown) > 1 or (not breakdown or breakdown[0].key != selected_key):
            for item in breakdown:
                lines.append(
                    f"  {item.key}: ${to_fixed(item.cost, 3)} "
                    f"({format_tokens(item.tokens)} tokens)"
                )
        if waste.missed_tokens > 0:
            label = "1 miss" if waste.miss_count == 1 else f"{waste.miss_count} misses"
            detail = f"{_locale(waste.missed_tokens)} tokens, {label}"
            if waste.missed_cost >= 0.0001:
                lines.append(
                    f"Cache Re-billed: ${to_fixed(waste.missed_cost, 3)} ({detail})"
                )
            else:
                lines.append(f"Cache Re-billed: {detail}")
    return "\n".join(lines)

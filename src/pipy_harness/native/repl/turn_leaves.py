"""Leaf helpers for one agent turn: interrupt translation and token pricing.

These are the pieces of a turn that depend on nothing in the session. The two
`wait_for_*_interrupt` functions exist at the composition seam: the terminal
driver reports an interruption as one of four strings, while the agent tiers
speak in typed enums, and translating at the seam is what keeps those strings
out of the agent loop. An unrecognized outcome raises rather than defaulting --
a new terminal outcome silently mapping to SETTLED would end turns early.

The cancel-join bound is the other half of the same seam: once an interrupt is
translated, every caller that cancelled a worker waits on this one value. The
two error helpers are the third: when a turn or a generation unwinds, several
disposals must all run and the *first* failure is the one that propagates.

Pricing is deliberately approximate and prefix-matched, so a new model in a
known family (``gpt-5.5`` under ``gpt-5``) keeps rendering a cost instead of
falling back to zero. `None` disables cost rendering for that selection.
"""

from __future__ import annotations

import threading

from pipy_harness.native.agent.provider_turn import ProviderTurnInterruption
from pipy_harness.native.agent.tools import ToolExecutionInterruption
from pipy_harness.native.agent.usage import AgentTokenPricing
from pipy_harness.native.extension_chrome_state import ExtensionChromeRetirement
from pipy_harness.native.tui import (
    TURN_ABORTED,
    TURN_LOCAL_COMMAND,
    TURN_SETTLED,
    TURN_STEERED,
    ToolLoopTerminalUi,
)

# Bound on how long the main thread waits for a cancelled worker to unwind
# after its connection is closed. The worker is a daemon thread, so if the join
# times out the process can still exit and -- because the turn returns ``None``
# -- the worker can no longer mutate provider/tool/context state regardless.
# The provider turn, the share command and the ``!`` shell shortcut all wait on
# the same bound; it is one value so a cancelled worker never gets two answers.
CANCEL_JOIN_TIMEOUT_SECONDS = 2.0

AGENT_HISTORY_KEEP_RECENT_GROUPS = 2
AGENT_HISTORY_MAX_MESSAGES = 40
AGENT_HISTORY_MAX_BYTES = 48 * 1024

PRICING_TABLE: dict[tuple[str, str], AgentTokenPricing] = {
    # OpenAI Codex subscription (GPT-5.x family) — approximate.
    ("openai-codex", "gpt-5"): AgentTokenPricing(
        input_per_million=1.25, output_per_million=10.00, reasoning_per_million=10.00
    ),
}


def finish_chrome_retirement(
    retirement: ExtensionChromeRetirement | None,
) -> BaseException | None:
    return None if retirement is None else retirement.finalize_nonraising()


def raise_first(errors: tuple[BaseException | None, ...]) -> None:
    for error in errors:
        if error is not None:
            raise error


def wait_for_tool_interrupt(
    terminal_ui: ToolLoopTerminalUi,
    done_event: threading.Event,
    cancel_event: threading.Event,
) -> ToolExecutionInterruption:
    """Translate the terminal driver's string outcome at the composition seam."""

    outcome = terminal_ui.wait_for_active_turn_interrupt(
        done_event,
        cancel_event,
        accept_commands=True,
    )
    if outcome == TURN_SETTLED:
        return ToolExecutionInterruption.SETTLED
    if outcome == TURN_ABORTED:
        return ToolExecutionInterruption.OPERATOR_ABORT
    if outcome == TURN_LOCAL_COMMAND:
        return ToolExecutionInterruption.LOCAL_COMMAND
    raise RuntimeError(f"unexpected tool interrupt outcome: {outcome!r}")


def wait_for_provider_interrupt(
    terminal_ui: ToolLoopTerminalUi,
    done_event: threading.Event,
    cancel_event: threading.Event,
) -> ProviderTurnInterruption:
    """Translate terminal-driver strings into the provider-loop contract."""

    try:
        outcome = terminal_ui.wait_for_active_turn_interrupt(
            done_event, cancel_event, accept_queue=True
        )
    except KeyboardInterrupt:
        cancel_event.set()
        return ProviderTurnInterruption.OPERATOR_ABORT
    if outcome == TURN_SETTLED:
        return ProviderTurnInterruption.SETTLED
    if outcome == TURN_ABORTED:
        return ProviderTurnInterruption.OPERATOR_ABORT
    if outcome == TURN_STEERED:
        return ProviderTurnInterruption.STEERING
    if outcome == TURN_LOCAL_COMMAND:
        return ProviderTurnInterruption.LOCAL_COMMAND
    raise RuntimeError(f"unexpected provider interrupt outcome: {outcome!r}")


def pricing_for(provider_name: str, model_id: str) -> AgentTokenPricing | None:
    """Return per-million-token pricing for (provider, model), or None.

    Falls back to a model-family prefix lookup so e.g. ``gpt-5.5`` reuses
    the ``gpt-5`` entry. ``None`` disables cost rendering for that
    selection; the bottom status keeps showing ``$0.000``.
    """

    direct = PRICING_TABLE.get((provider_name, model_id))
    if direct is not None:
        return direct
    for (entry_provider, entry_model), price in PRICING_TABLE.items():
        if entry_provider != provider_name:
            continue
        if model_id.startswith(entry_model):
            return price
    return None

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

Pricing comes from the selection's catalog row, as Pi prices each response
from its model object; a selection with no row prices at zero.
"""

from __future__ import annotations

import threading
from functools import partial

from pipy_harness.native.agent.provider_turn import (
    ProviderTurnInterruption,
    ProviderTurnWaiter,
    _AbortCallbackSignal,
    _StartGatedProvider,
    _wait_for_external_abort,
)
from pipy_harness.native.agent.tools import ToolExecutionInterruption
from pipy_harness.native.agent.usage import AgentTokenPricing, AgentTokenPricingTier
from pipy_harness.native.catalog import NativeModelCost, build_builtin_catalog
from pipy_harness.native.extension_chrome_state import ExtensionChromeRetirement
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.repl_state import NativeModelSelection, NativeReplProviderState
from pipy_harness.native.tui import (
    TURN_ABORTED,
    TURN_LOCAL_COMMAND,
    TURN_SETTLED,
    TURN_STEERED,
    TerminalUi,
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


def finish_chrome_retirement(
    retirement: ExtensionChromeRetirement | None,
) -> BaseException | None:
    return None if retirement is None else retirement.finalize_nonraising()


def raise_first(errors: tuple[BaseException | None, ...]) -> None:
    for error in errors:
        if error is not None:
            raise error


def wait_for_tool_interrupt(
    terminal_ui: TerminalUi,
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


def wait_for_external_tool_interrupt(
    abort_event: threading.Event | _AbortCallbackSignal,
    done_event: threading.Event,
    cancel_event: threading.Event,
) -> ToolExecutionInterruption:
    """Bridge headless abort into the existing tool worker's ordered signal.

    The worker may already have completed; the executor retains its existing
    completion-versus-cancellation policy, including accepted tool effects.
    """

    outcome = _wait_for_external_abort(abort_event, None, done_event, cancel_event)
    if outcome is ProviderTurnInterruption.OPERATOR_ABORT:
        return ToolExecutionInterruption.OPERATOR_ABORT
    return ToolExecutionInterruption.SETTLED


def wait_for_provider_interrupt(
    terminal_ui: TerminalUi,
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


def provider_turn_inputs(
    provider: ProviderPort,
    terminal_ui: TerminalUi | None,
    abort_event: threading.Event | _AbortCallbackSignal | None,
) -> tuple[ProviderPort, ProviderTurnWaiter | None]:
    """Share canonical terminal/external cancellation without rebinding a provider."""

    if terminal_ui is not None:
        return provider, partial(wait_for_provider_interrupt, terminal_ui)
    if abort_event is None:
        return provider, None
    start = None
    if isinstance(abort_event, _AbortCallbackSignal):
        start = threading.Event()
        provider = _StartGatedProvider(provider, start)
    return provider, partial(_wait_for_external_abort, abort_event, start)


def pricing_for(
    provider_state: object, provider_name: str, model_id: str
) -> AgentTokenPricing | None:
    """Price a selection from the catalog row the session resolves for it.

    Pi prices every response from the request's model object
    (``calculateCost(model, usage)``), which already carries ``models.json``
    overrides. A live :class:`NativeReplProviderState` resolves that row
    (built-in, ``models.json``, or Pi's fallback row); any other state -- an
    injected or static provider -- uses the built-in row. ``None`` (no row)
    prices at zero.
    """

    selection = NativeModelSelection(provider_name, model_id)
    if isinstance(provider_state, NativeReplProviderState):
        spec = provider_state.model_runtime.resolve_spec(selection)
    else:
        spec = build_builtin_catalog().find(provider_name, model_id)
    return None if spec is None else pricing_from_cost(spec.cost)


def pricing_from_cost(cost: NativeModelCost) -> AgentTokenPricing:
    """Map a row's Pi-shaped ``cost`` onto the agent tier's pricing value."""

    return AgentTokenPricing(
        input_per_million=cost.input,
        output_per_million=cost.output,
        cache_read_per_million=cost.cache_read,
        cache_write_per_million=cost.cache_write,
        tiers=tuple(
            AgentTokenPricingTier(
                input_tokens_above=tier.input_tokens_above,
                input_per_million=tier.input,
                output_per_million=tier.output,
                cache_read_per_million=tier.cache_read,
                cache_write_per_million=tier.cache_write,
            )
            for tier in cost.tiers
        ),
    )

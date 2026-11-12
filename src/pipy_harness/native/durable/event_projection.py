"""Committed completed facts as the agent loop's events (pre-DE26 skeleton).

``CommittedEventProjection`` turns the replacement snapshots of
``observe_completed()`` into the existing ``AgentEvent`` vocabulary for an
``AgentEventSink``, so the controller, reducer, TUI and serializers need no
second vocabulary (``docs/durable-loop-integration-design.md``, section 4.3).
It reads only committed snapshots, never scheduler state, and runs no effect.

Every event belongs to one committed fact with a stable key in its
conversation (``entry:<id>``, ``round:<assistant entry id>``,
``outcome:<task id>``). A fact's events are emitted together, once: the
projection skips facts whose key it has delivered, so a redelivered or
reconnected snapshot emits nothing again. The keys are the caller's cursor;
a caller that persists them after each delivered fact and passes them back on
reopen gets each event once across a crash, except for a fact whose delivery
the crash itself cut, which is delivered again (at least once, as DE09's
consumer ledger).

Order: new facts are emitted in the snapshot's committed order, which is
commit order except within a tool round, where the projection follows the
committed round order (the loop's call order) and holds results behind an
unfinished earlier call. No product wiring yet: the run route is DE42/DE43,
streaming partials DE26, and queued-input, retry and provider-failure events
wait for DE18/DE19's run records.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.events import (
    AgentEvent,
    AgentRunCompleted,
    AgentRunSettled,
    AgentRunStarted,
    MessageCompleted,
    MessageStarted,
    RunCancelled,
    ToolCallCompleted,
    ToolCallStarted,
    TurnCompleted,
    TurnStarted,
)
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentStopReason,
    AgentToolResultMessage,
    AgentTranscriptMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.ports import AgentEventSink
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.agent.results import (
    AgentCancellationReason,
    AgentFailure,
    AgentRunOutcome,
    AgentRunResult,
    AgentTurnOutcome,
    AgentUsage,
)
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentUsageAccumulator,
)

from .canonical_codec import decode_canonical
from .completed_projection import CompletedWatch
from .completed_values import CompletedMessage, CompletedSnapshot, TerminalOutcome
from .generation_tasks import GENERATION_NAME
from .tool_values import read_native_tool_output


@dataclass(frozen=True, slots=True)
class ProjectedEvent:
    """One emitted event with the committed fact it belongs to.

    ``seq`` is the fact's commit sequence: the entry's own for a message,
    the round's last committed result for a closed round, and for a run
    outcome, which has no entry, the publication that made it visible (the
    latest message's sequence in a baseline), so an outcome's sequence is a
    lower bound tied to the snapshot that showed it. It is not an ordering
    key: a tool round's results follow the round order, not their commits.
    """

    conversation_id: int
    key: str
    seq: int
    event: AgentEvent


@dataclass(slots=True)
class _Run:
    user: AgentUserMessage
    messages: list[AgentTranscriptMessage]
    turn: int = -1
    task_id: int | None = None
    calls: dict[str, Any] | None = None
    assistant: AgentAssistantMessage | None = None
    samples: list[CompletedMessage] | None = None
    cancelled: AgentCancellationReason | None = None


class CommittedEventProjection:
    """Emit each committed fact's events once, in committed order."""

    def __init__(
        self,
        conversation_id: int,
        sink: AgentEventSink,
        *,
        delivered: Iterable[str] = (),
        on_delivered: Callable[[str], None] | None = None,
    ) -> None:
        """``delivered`` are the keys a previous projection emitted;
        ``on_delivered`` is called with each fact's key after its events, so
        the caller can persist its cursor fact by fact."""
        self._cid = conversation_id
        self._sink = sink
        self._delivered = set(delivered)
        self._on_delivered = on_delivered

    @property
    def delivered(self) -> frozenset[str]:
        """Keys of the facts whose events this projection has emitted."""
        return frozenset(self._delivered)

    def apply(self, snapshot: CompletedSnapshot) -> tuple[ProjectedEvent, ...]:
        """Emit the events of every fact not delivered yet; return them."""

        if snapshot.conversation_id != self._cid:
            raise ValueError("snapshot belongs to another conversation")
        emitted: list[ProjectedEvent] = []
        for key, seq, events in _facts(snapshot):
            if key in self._delivered:
                continue
            for event in events:
                self._sink.emit(event)
                emitted.append(ProjectedEvent(self._cid, key, seq, event))
            self._delivered.add(key)
            if self._on_delivered is not None:
                self._on_delivered(key)
        return tuple(emitted)

    def attach(self, watch: CompletedWatch) -> None:
        """Project the watch's baseline now and each later snapshot.

        Call on the watch's loop before ``watch.start``; the watch then owns
        delivery, coalescing and its end, as for any listener.
        """

        self.apply(watch.snapshot)

        async def listen(snapshot: CompletedSnapshot) -> None:
            self.apply(snapshot)

        watch.start(listen)


Fact = tuple[str, int, tuple[AgentEvent, ...]]


def _facts(snapshot: CompletedSnapshot) -> list[Fact]:
    """Every committed fact of the snapshot with its events, in order."""

    builder = _Facts(snapshot)
    for message in snapshot.messages:
        builder.add(message)
    return builder.finish()


class _Facts:
    def __init__(self, snapshot: CompletedSnapshot) -> None:
        self.closed = {r.assistant_id: r.result_ids for r in snapshot.rounds}
        self.outcomes = {
            o.task_id: o for o in snapshot.outcomes if o.kind == GENERATION_NAME
        }
        claimed = {
            m.by_task_id
            for m in snapshot.messages
            if m.provider_outcome is not None and m.by_task_id is not None
        }
        # Generations that ended before committing an assistant (for example
        # at the restart limit in their prepare phase), oldest first. Each
        # ends the oldest run that has no generation of its own.
        self.unclaimed = sorted(
            task_id for task_id in self.outcomes if task_id not in claimed
        )
        latest = max((m.commit_seq for m in snapshot.messages), default=0)
        self.derived = (
            latest if snapshot.publication_seq is None else snapshot.publication_seq
        )
        self.facts: list[Fact] = []
        self.run: _Run | None = None
        self.round: tuple[int, tuple[int, ...]] | None = None
        self.results: list[AgentToolResultMessage] = []
        self.round_seq = 0

    def add(self, message: CompletedMessage) -> None:
        value = message.decode()
        key = f"entry:{message.entry_id}"
        if type(value) is AgentUserMessage:
            if self.run is not None:
                self.settle(self.run)
            self.run = _Run(value, [value], turn=0)
            events: tuple[AgentEvent, ...] = (
                AgentRunStarted(),
                TurnStarted(0),
                MessageStarted(0, value),
                MessageCompleted(0, value),
            )
            self.facts.append((key, message.commit_seq, events))
            return
        run = self.run
        if run is None:
            raise ValueError("committed facts start with an input")
        if type(value) is AgentAssistantMessage and (
            (run.task_id is not None and message.by_task_id != run.task_id)
            or (
                run.task_id is None
                and any(
                    task_id < (message.by_task_id or 0) for task_id in self.unclaimed
                )
            )
        ):
            # An operator retry's new generation continues a run whose
            # generation ended (with or without an assistant); its events
            # wait for DE18/DE19's run records.
            raise ValueError("a second generation in one run is not projected")
        if type(value) is AgentAssistantMessage:
            run.messages.append(value)
            run.samples = [*(run.samples or ()), message]
            self.facts.append(
                (key, message.commit_seq, _assistant(run, message, value))
            )
            if value.tool_calls:
                self.round = (message.entry_id, self.closed.get(message.entry_id, ()))
                self.results = []
                self.round_seq = message.commit_seq
            return
        if type(value) is not AgentToolResultMessage or run.calls is None:
            raise ValueError("committed tool result outside a tool round")
        run.messages.append(value)
        call = run.calls[value.provider_correlation_id]
        events = (
            ToolCallStarted(run.turn, call),
            ToolCallCompleted(run.turn, value, *_durations(message)),
        )
        self.facts.append((key, message.commit_seq, events))
        self.results.append(value)
        self.round_seq = max(self.round_seq, message.commit_seq)
        if self.round is not None and self.round[1][-1:] == (message.entry_id,):
            assert run.assistant is not None
            completed = TurnCompleted(
                run.turn,
                AgentTurnOutcome.SUCCEEDED,
                run.assistant,
                tuple(self.results),
            )
            self.facts.append((f"round:{self.round[0]}", self.round_seq, (completed,)))
            self.round = None

    def settle(self, run: _Run) -> None:
        events: tuple[AgentEvent, ...] = ()
        if run.task_id is None and self.unclaimed:
            # The loop's preparation failure: a failed turn without a message.
            run.task_id = self.unclaimed.pop(0)
            empty = AgentAssistantMessage(ProductContent(""))
            events = (TurnCompleted(0, AgentTurnOutcome.FAILED, empty),)
        if run.task_id is None or run.task_id not in self.outcomes:
            return
        outcome = self.outcomes.pop(run.task_id)
        self.facts.append(
            (
                f"outcome:{outcome.task_id}",
                self.derived,
                events + _run_end(run, outcome),
            )
        )

    def finish(self) -> list[Fact]:
        if self.run is not None:
            self.settle(self.run)
        return self.facts


def _assistant(
    run: _Run, message: CompletedMessage, value: AgentAssistantMessage
) -> tuple[AgentEvent, ...]:
    events: list[AgentEvent] = []
    if run.assistant is not None:
        # A generation after a closed tool round is the run's next turn.
        run.turn += 1
        events.append(TurnStarted(run.turn))
    run.assistant = value
    run.task_id = message.by_task_id
    run.calls = {call.provider_correlation_id: call for call in value.tool_calls}
    events.append(MessageStarted(run.turn, AgentAssistantMessage(ProductContent(""))))
    if value.stop_reason is AgentStopReason.ABORTED:
        # The loop's provider cancellation: the run is cancelled with the
        # adapter's reason before the aborted message.
        run.cancelled = _cancellation(message)
        return (
            *events,
            RunCancelled(run.cancelled),
            MessageCompleted(run.turn, value),
            TurnCompleted(run.turn, AgentTurnOutcome.CANCELLED, value),
        )
    events.append(MessageCompleted(run.turn, value))
    if not value.tool_calls:
        failed = value.stop_reason is AgentStopReason.ERROR
        events.append(
            TurnCompleted(
                run.turn,
                AgentTurnOutcome.FAILED if failed else AgentTurnOutcome.SUCCEEDED,
                value,
            )
        )
    return tuple(events)


def _provider_outcome(message: CompletedMessage) -> ProviderTurnOutcome | None:
    if message.provider_outcome is None:
        return None
    outcome = decode_canonical(message.provider_outcome.thaw())
    if type(outcome) is not ProviderTurnOutcome:
        raise ValueError("committed provider outcome is not a provider turn")
    return outcome


def _cancellation(message: CompletedMessage) -> AgentCancellationReason:
    outcome = _provider_outcome(message)
    if outcome is None or outcome.cancellation_reason is None:
        return AgentCancellationReason.PROVIDER_CANCELLED
    return outcome.cancellation_reason


def _durations(message: CompletedMessage) -> tuple[float | None, float | None]:
    """The committed tool run time; the row's own span is not committed, so
    both report how long the tool itself ran (Pi ``durationMs``)."""

    if message.tool_output is None:
        return None, None
    _, outcome = read_native_tool_output(message.tool_output.thaw())
    return outcome.execution_seconds, outcome.execution_seconds


def _run_end(run: _Run, outcome: TerminalOutcome) -> tuple[AgentEvent, ...]:
    tree = outcome.outcome.thaw()
    status = tree.get("status") if type(tree) is dict else None
    messages = tuple(run.messages)
    usage = _usage(messages, run.samples or ())
    if run.cancelled is not None:
        # The generation fails its task, but the loop's run is cancelled.
        result = AgentRunResult(
            AgentRunOutcome.CANCELLED,
            messages,
            usage,
            cancellation_reason=run.cancelled,
        )
        return AgentRunCompleted(result), AgentRunSettled(result)
    if status == "completed":
        result = AgentRunResult(AgentRunOutcome.SUCCEEDED, messages, usage)
        return AgentRunCompleted(result), AgentRunSettled(result)
    reason = _reason(tree, status)
    if status == "aborted":
        detail = ProductContent(reason)
        result = AgentRunResult(
            AgentRunOutcome.CANCELLED,
            messages,
            usage,
            cancellation_reason=AgentCancellationReason.OPERATOR_ABORT,
            cancellation_detail=detail,
        )
        return (
            RunCancelled(AgentCancellationReason.OPERATOR_ABORT, detail),
            AgentRunCompleted(result),
            AgentRunSettled(result),
        )
    failure = AgentFailure(f"durable_{status or 'unknown'}", ProductContent(reason))
    result = AgentRunResult(AgentRunOutcome.FAILED, messages, usage, failure)
    return AgentRunCompleted(result), AgentRunSettled(result)


def _reason(tree: Any, status: object) -> str:
    """The durable cause: the outcome's error message or its reason code."""

    if type(tree) is dict:
        error = tree.get("error")
        if type(error) is dict and type(error.get("message")) is str:
            return str(error["message"])
        if type(tree.get("reason")) is str:
            return str(tree["reason"])
    return f"The durable run ended {status or 'without a status'}"


def _usage(
    messages: tuple[AgentTranscriptMessage, ...],
    assistants: Iterable[CompletedMessage],
) -> AgentUsage:
    """The run's usage as the loop accumulates it: the raw provider samples
    of its committed provider outcomes, and the cost each assistant was
    priced at when it settled."""

    accumulator = AgentUsageAccumulator()
    for message in assistants:
        outcome = _provider_outcome(message)
        if outcome is None or outcome.result is None:
            # A cancelled provider turn adds no usage, as in the loop.
            continue
        accumulator.absorb(AgentProviderUsageSample.from_mapping(outcome.result.usage))
    cost = sum(
        message.usage.cost.total
        for message in messages
        if type(message) is AgentAssistantMessage and message.usage is not None
    )
    return replace(accumulator.agent_usage(), cost_usd=cost)

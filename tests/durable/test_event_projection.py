"""Committed facts as the loop's events: order, once-only keys, reconnect."""

import asyncio
import threading
from dataclasses import replace

import pytest
from generation_support import admit, definition, seed
from test_completed_projection import isolate, store_at
from tool_support import definitions

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.events import (
    AgentRunCompleted,
    AgentRunSettled,
    MessageCompleted,
    MessageStarted,
    ToolCallCompleted,
    ToolCallStarted,
    TurnCompleted,
    TurnStarted,
)
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.results import (
    AgentRunOutcome,
    AgentTurnOutcome,
    AgentUsage,
)
from pipy_harness.native.durable.completed_projection import observe_completed
from pipy_harness.native.durable.event_projection import CommittedEventProjection
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session

# The loop's events for one input, a two-call round and the following answer.
TOOL_RUN = [
    "AgentRunStarted",
    "TurnStarted",
    "MessageStarted",
    "MessageCompleted",
    "MessageStarted",
    "MessageCompleted",
    "ToolCallStarted",
    "ToolCallCompleted",
    "ToolCallStarted",
    "ToolCallCompleted",
    "TurnCompleted",
    "TurnStarted",
    "MessageStarted",
    "MessageCompleted",
    "TurnCompleted",
    "AgentRunCompleted",
    "AgentRunSettled",
]


class Sink:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def names(events):
    return [type(event).__name__ for event in events]


def run_to_terminal(session, tasks, parent):
    terminal = threading.Event()

    def observe(publication):
        for change in publication.changes:
            if hasattr(change, "to_tree"):
                tree = change.to_tree()
                if (
                    tree["type"] == "task"
                    and tree["value"]["id"] == parent
                    and tree["value"]["state"]["status"] == "terminal"
                ):
                    terminal.set()

    session.subscribe_commits(observe)
    scheduler = TaskScheduler(session, tasks)
    scheduler.open()
    return scheduler, terminal


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_a_tool_run_projects_the_loop_events_once(tmp_path, monkeypatch, backend):
    isolate(tmp_path, monkeypatch)
    session = Session(store_at(tmp_path, backend))
    generation, tool, snapshot = definitions(tmp_path, replay="safe")
    cid, _, parent = admit(session, generation, snapshot)
    scheduler, terminal = run_to_terminal(session, [generation, tool], parent)

    async def run():
        watch = await observe_completed(session, cid)
        sink = Sink()
        projection = CommittedEventProjection(cid, sink)
        projection.attach(watch)
        # The baseline holds the admitted input only.
        assert names(sink.events) == TOOL_RUN[:4]
        scheduler.resume()
        assert await asyncio.to_thread(terminal.wait, 10)
        final = await asyncio.wait_for(watch.drain(), 10)
        watch.stop()
        await watch.wait_closed()
        return sink, projection, final

    try:
        sink, projection, final = asyncio.run(run())
    finally:
        session.close(timeout=10)
    assert names(sink.events) == TOOL_RUN
    events = sink.events
    user, assistant, first, second, answer = (m.decode() for m in final.messages)
    assert type(user) is AgentUserMessage and events[3] == MessageCompleted(0, user)
    assert type(assistant) is AgentAssistantMessage
    assert events[4] == MessageStarted(0, AgentAssistantMessage(ProductContent("")))
    assert events[5] == MessageCompleted(0, assistant)
    assert events[6] == ToolCallStarted(0, assistant.tool_calls[0])
    assert type(first) is AgentToolResultMessage
    assert type(events[7]) is ToolCallCompleted and events[7].result == first
    assert events[7].execution_seconds is not None
    assert events[8] == ToolCallStarted(0, assistant.tool_calls[1])
    assert events[9].result == second
    assert events[10] == TurnCompleted(
        0, AgentTurnOutcome.SUCCEEDED, assistant, (first, second)
    )
    assert events[11] == TurnStarted(1)
    assert events[13] == MessageCompleted(1, answer)
    assert events[14] == TurnCompleted(1, AgentTurnOutcome.SUCCEEDED, answer)
    completed, settled = events[15:]
    assert type(completed) is AgentRunCompleted and type(settled) is AgentRunSettled
    assert completed.result == settled.result
    assert completed.result.outcome is AgentRunOutcome.SUCCEEDED
    assert completed.result.messages == (user, assistant, first, second, answer)
    # The loop's run usage: raw provider tokens (two turns of 20 prompt
    # tokens, 4 of them cached), not Pi's uncached message input.
    assert completed.result.usage == AgentUsage(40, 20, 6, 8, 0, 0.0)
    assert sum(m.usage.input for m in (assistant, answer)) == 32
    assert projection.delivered == {
        *(f"entry:{m.entry_id}" for m in final.messages),
        f"round:{final.messages[1].entry_id}",
        f"outcome:{parent}",
    }
    # A redelivered snapshot emits nothing.
    assert projection.apply(final) == ()
    assert len(sink.events) == len(TOOL_RUN)
    # Reconnect with the delivered keys emits nothing; without them the same
    # events again, each with its fact's key and commit sequence.
    again = Sink()
    assert (
        CommittedEventProjection(cid, again, delivered=projection.delivered).apply(
            final
        )
        == ()
    )
    fresh = Sink()
    projected = CommittedEventProjection(cid, fresh).apply(final)
    assert fresh.events == sink.events
    assert [p.event for p in projected] == fresh.events
    sequences = {f"entry:{m.entry_id}": m.commit_seq for m in final.messages}
    sequences[f"round:{final.messages[1].entry_id}"] = max(
        m.commit_seq for m in final.messages[2:4]
    )
    for item in projected:
        if item.key in sequences:
            assert item.seq == sequences[item.key]
        else:
            assert item.key == f"outcome:{parent}"
            assert item.seq >= max(sequences.values())
    # An operator retry's new generation in the same run is refused, not
    # projected as a second run end (DE18/DE19).
    retried = replace(
        final,
        messages=(
            *final.messages[:4],
            replace(final.messages[4], by_task_id=parent + 100),
        ),
    )
    with pytest.raises(ValueError, match="second generation"):
        CommittedEventProjection(cid, Sink()).apply(retried)


def test_a_failed_provider_response_fails_the_turn_and_the_run(tmp_path, monkeypatch):
    from test_generation_kernel_equivalence import _FailingWire

    isolate(tmp_path, monkeypatch)
    session = Session(store_at(tmp_path, "memory"))
    task = definition(tmp_path, _FailingWire())
    cid, _, parent = admit(session, task, seed(tmp_path))
    scheduler, terminal = run_to_terminal(session, [task], parent)
    scheduler.resume()
    assert terminal.wait(10)

    async def run():
        watch = await observe_completed(session, cid)
        snapshot = watch.snapshot
        watch.stop()
        await watch.wait_closed()
        return snapshot

    try:
        snapshot = asyncio.run(run())
    finally:
        session.close(timeout=10)
    sink = Sink()
    CommittedEventProjection(cid, sink).apply(snapshot)
    assert names(sink.events) == [
        *TOOL_RUN[:6],
        "TurnCompleted",
        "AgentRunCompleted",
        "AgentRunSettled",
    ]
    assert sink.events[6].outcome is AgentTurnOutcome.FAILED
    result = sink.events[7].result
    assert result.outcome is AgentRunOutcome.FAILED
    assert result.failure.error_type == "durable_failed"
    assert result.failure.message.value == "Durable provider response did not complete"


def test_a_snapshot_of_another_conversation_is_refused(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    session = Session(store_at(tmp_path, "memory"))
    task = definition(tmp_path, _never())
    cid, _, _ = admit(session, task, seed(tmp_path))

    async def run():
        watch = await observe_completed(session, cid)
        snapshot = watch.snapshot
        watch.stop()
        await watch.wait_closed()
        return snapshot

    try:
        snapshot = asyncio.run(run())
    finally:
        session.close(timeout=10)
    sink = Sink()
    with pytest.raises(ValueError, match="another conversation"):
        CommittedEventProjection(cid + 1, sink).apply(snapshot)
    assert sink.events == []


def _never():
    from generation_support import Wire

    return Wire(())


def _finished_tool_run(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    session = Session(store_at(tmp_path, "memory"))
    generation, tool, snapshot = definitions(tmp_path, replay="safe")
    cid, _, parent = admit(session, generation, snapshot)
    scheduler, terminal = run_to_terminal(session, [generation, tool], parent)
    scheduler.resume()
    assert terminal.wait(10)

    async def run():
        watch = await observe_completed(session, cid)
        snapshot = watch.snapshot
        watch.stop()
        await watch.wait_closed()
        return snapshot

    try:
        return cid, parent, asyncio.run(run())
    finally:
        session.close(timeout=10)


def _ended(snapshot, task_id, tree):
    from pipy_harness.native.durable.codec import OwnedJson
    from pipy_harness.native.durable.completed_values import TerminalOutcome
    from pipy_harness.native.durable.generation_tasks import (
        GENERATION_NAME,
        GENERATION_VERSION,
    )

    return TerminalOutcome(
        task_id, GENERATION_NAME, GENERATION_VERSION, OwnedJson(tree)
    )


def test_an_aborted_generation_cancels_the_run(tmp_path, monkeypatch):
    from pipy_harness.native.agent.events import RunCancelled
    from pipy_harness.native.agent.results import AgentCancellationReason

    cid, parent, final = _finished_tool_run(tmp_path, monkeypatch)
    aborted = replace(
        final,
        outcomes=(
            _ended(final, parent, {"status": "aborted", "reason": "operator_retry"}),
        ),
    )
    sink = Sink()
    CommittedEventProjection(cid, sink).apply(aborted)
    assert names(sink.events) == [
        *TOOL_RUN[:-2],
        "RunCancelled",
        "AgentRunCompleted",
        "AgentRunSettled",
    ]
    cancelled = sink.events[-3]
    assert cancelled == RunCancelled(
        AgentCancellationReason.OPERATOR_ABORT, ProductContent("operator_retry")
    )
    result = sink.events[-1].result
    assert result.outcome is AgentRunOutcome.CANCELLED
    assert result.cancellation_reason is AgentCancellationReason.OPERATOR_ABORT
    assert result.cancellation_detail == ProductContent("operator_retry")
    assert sink.events[-2].result == result


def test_a_generation_that_ends_before_its_assistant_fails_the_turn(
    tmp_path, monkeypatch
):
    cid, parent, final = _finished_tool_run(tmp_path, monkeypatch)
    failed = {
        "status": "failed",
        "error": {
            "message": "The generation was interrupted in its prepare phase",
            "detail": {"reason": "restart_limit"},
        },
    }
    early = replace(
        final, messages=final.messages[:1], outcomes=(_ended(final, parent, failed),)
    )
    sink = Sink()
    CommittedEventProjection(cid, sink).apply(early)
    assert names(sink.events) == [
        *TOOL_RUN[:4],
        "TurnCompleted",
        "AgentRunCompleted",
        "AgentRunSettled",
    ]
    assert sink.events[4] == TurnCompleted(
        0, AgentTurnOutcome.FAILED, AgentAssistantMessage(ProductContent(""))
    )
    result = sink.events[-1].result
    assert result.outcome is AgentRunOutcome.FAILED
    assert result.failure.error_type == "durable_failed"
    assert result.failure.message == ProductContent(
        "The generation was interrupted in its prepare phase"
    )
    # A retried generation after it is refused, not projected as a second
    # run in the same run (DE18/DE19): the failed generation was created
    # after the input (ids are global), the retry after the failed one.
    retry = 50
    relabelled = tuple(
        replace(m, by_task_id=retry) if m.by_task_id == parent else m
        for m in final.messages
    )
    retried = replace(
        final,
        messages=relabelled,
        outcomes=(
            _ended(final, parent, failed),
            _ended(final, retry, {"status": "completed"}),
        ),
    )
    with pytest.raises(ValueError, match="second generation"):
        CommittedEventProjection(cid, Sink()).apply(retried)


def _second_input(final, entry_id):
    first = final.messages[0]
    return replace(
        first,
        entry_id=entry_id,
        commit_seq=max(m.commit_seq for m in final.messages) + 1,
    )


_PREPARE_FAILED = {
    "status": "failed",
    "error": {"message": "The generation was interrupted in its prepare phase"},
}


@pytest.mark.parametrize("next_input", [None, 30])
def test_a_retry_that_ends_before_its_assistant_is_refused(
    tmp_path, monkeypatch, next_input
):
    # The first generation committed its answer and ended; an operator retry
    # of the same run then died in its prepare phase (no assistant). It is a
    # second generation of the first run, with or without a later input, and
    # never ends the next run.
    cid, parent, final = _finished_tool_run(tmp_path, monkeypatch)
    messages = final.messages
    retry = max(m.entry_id for m in messages) + 4
    if next_input is not None:
        messages = (*messages, _second_input(final, retry + 10))
    snapshot = replace(
        final,
        messages=messages,
        outcomes=(*final.outcomes, _ended(final, retry, _PREPARE_FAILED)),
    )
    with pytest.raises(ValueError, match="second generation"):
        CommittedEventProjection(cid, Sink()).apply(snapshot)


def test_the_next_runs_generation_that_ends_before_its_assistant_ends_that_run(
    tmp_path, monkeypatch
):
    cid, parent, final = _finished_tool_run(tmp_path, monkeypatch)
    second = _second_input(final, max(m.entry_id for m in final.messages) + 4)
    generation = second.entry_id + 1
    snapshot = replace(
        final,
        messages=(*final.messages, second),
        outcomes=(*final.outcomes, _ended(final, generation, _PREPARE_FAILED)),
    )
    sink = Sink()
    emitted = CommittedEventProjection(cid, sink).apply(snapshot)
    assert names(sink.events) == [
        *TOOL_RUN,
        *TOOL_RUN[:4],
        "TurnCompleted",
        "AgentRunCompleted",
        "AgentRunSettled",
    ]
    assert list(dict.fromkeys(item.key for item in emitted))[-2:] == [
        f"entry:{second.entry_id}",
        f"outcome:{generation}",
    ]
    assert sink.events[-1].result.outcome is AgentRunOutcome.FAILED
    # The first run keeps its own completed end.
    first_end = sink.events[len(TOOL_RUN) - 1]
    assert first_end.result.outcome is AgentRunOutcome.SUCCEEDED


def test_a_generation_after_a_later_input_that_may_retry_is_refused(
    tmp_path, monkeypatch
):
    # Run 1's generation failed after its answer; input 2 is appended, and a
    # generation created after it then dies in its prepare phase. It may be
    # input 2's own generation or an operator retry of run 1 (a retry is
    # allowed after a later input), so it is refused rather than ending run 2.
    cid, parent, final = _finished_tool_run(tmp_path, monkeypatch)
    second = _second_input(final, max(m.entry_id for m in final.messages) + 4)
    generation = second.entry_id + 1
    snapshot = replace(
        final,
        messages=(*final.messages, second),
        outcomes=(
            *(o for o in final.outcomes if o.task_id != parent),
            _ended(final, parent, _PREPARE_FAILED),
            _ended(final, generation, _PREPARE_FAILED),
        ),
    )
    sink = Sink()
    with pytest.raises(ValueError, match="may retry an earlier run"):
        CommittedEventProjection(cid, sink).apply(snapshot)
    assert sink.events == []


def test_a_generation_with_an_answer_after_a_later_input_that_may_retry_is_refused(
    tmp_path, monkeypatch
):
    # The same ambiguity when the generation after input 2 commits an
    # answer: it may be an operator retry of run 1's failed generation.
    cid, parent, final = _finished_tool_run(tmp_path, monkeypatch)
    second = _second_input(final, max(m.entry_id for m in final.messages) + 4)
    generation = second.entry_id + 1
    answer = replace(
        final.messages[-1],
        entry_id=generation + 1,
        commit_seq=second.commit_seq + 1,
        by_task_id=generation,
    )
    snapshot = replace(
        final,
        messages=(*final.messages, second, answer),
        outcomes=(
            *(o for o in final.outcomes if o.task_id != parent),
            _ended(final, parent, _PREPARE_FAILED),
            _ended(final, generation, {"status": "completed"}),
        ),
    )
    sink = Sink()
    with pytest.raises(ValueError, match="may retry an earlier run"):
        CommittedEventProjection(cid, sink).apply(snapshot)
    assert sink.events == []
    # After a completed run 1 the same generation ends run 2.
    settled = replace(
        snapshot,
        outcomes=(*final.outcomes, _ended(final, generation, {"status": "completed"})),
    )
    sink = Sink()
    CommittedEventProjection(cid, sink).apply(settled)
    assert names(sink.events)[-2:] == ["AgentRunCompleted", "AgentRunSettled"]
    assert sink.events[-1].result.outcome is AgentRunOutcome.SUCCEEDED


def test_an_earlier_runs_answer_after_a_later_input_is_refused(tmp_path, monkeypatch):
    # Run 1's generation commits its answer after input 2: it belongs to
    # run 1, not to run 2's first turn.
    cid, parent, final = _finished_tool_run(tmp_path, monkeypatch)
    second = _second_input(final, max(m.entry_id for m in final.messages) + 4)
    late = replace(
        final.messages[-1],
        entry_id=second.entry_id + 1,
        commit_seq=second.commit_seq + 1,
    )
    assert late.by_task_id == parent < second.entry_id
    snapshot = replace(final, messages=(*final.messages[:-1], second, late))
    sink = Sink()
    with pytest.raises(ValueError, match="earlier run's generation"):
        CommittedEventProjection(cid, sink).apply(snapshot)
    assert sink.events == []


def test_a_generation_before_any_input_is_refused(tmp_path, monkeypatch):
    cid, parent, final = _finished_tool_run(tmp_path, monkeypatch)
    snapshot = replace(
        final,
        messages=final.messages[:1],
        outcomes=(_ended(final, final.messages[0].entry_id - 1, _PREPARE_FAILED),),
    )
    with pytest.raises(ValueError, match="before any input"):
        CommittedEventProjection(cid, Sink()).apply(snapshot)


def test_a_provider_cancelled_generation_cancels_the_turn_and_the_run(
    tmp_path, monkeypatch
):
    from pipy_harness.native.agent.events import RunCancelled
    from pipy_harness.native.agent.messages import AgentStopReason
    from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
    from pipy_harness.native.agent.results import AgentCancellationReason
    from pipy_harness.native.durable.canonical_codec import encode_canonical
    from pipy_harness.native.durable.codec import OwnedJson

    cid, parent, final = _finished_tool_run(tmp_path, monkeypatch)
    answer = final.messages[4]
    aborted = replace(
        answer.decode(),
        content=ProductContent(""),
        stop_reason=AgentStopReason.ABORTED,
    )
    reason = AgentCancellationReason.PROVIDER_CANCELLED
    cancelled_turn = replace(
        answer,
        canonical=OwnedJson(encode_canonical(aborted)),
        provider_outcome=OwnedJson(
            encode_canonical(ProviderTurnOutcome(cancellation_reason=reason))
        ),
    )
    failed = {
        "status": "failed",
        "error": {"message": "Durable provider response did not complete"},
    }
    snapshot = replace(
        final,
        messages=(*final.messages[:4], cancelled_turn),
        outcomes=(_ended(final, parent, failed),),
    )
    sink = Sink()
    CommittedEventProjection(cid, sink).apply(snapshot)
    assert names(sink.events) == [
        *TOOL_RUN[:13],
        "RunCancelled",
        "MessageCompleted",
        "TurnCompleted",
        "AgentRunCompleted",
        "AgentRunSettled",
    ]
    assert sink.events[13] == RunCancelled(reason)
    assert sink.events[15] == TurnCompleted(1, AgentTurnOutcome.CANCELLED, aborted)
    result = sink.events[-1].result
    assert result.outcome is AgentRunOutcome.CANCELLED
    assert result.cancellation_reason is reason
    # The cancelled turn adds no usage: only the first turn's raw tokens.
    assert result.usage == AgentUsage(20, 10, 3, 4, 0, 0.0)

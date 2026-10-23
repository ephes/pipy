"""Actual inner-provider/finalizer ownership, abort gates and atomic rollback."""

import asyncio
import threading
from datetime import UTC, datetime

import pytest
from generation_support import Wire, admit, completion, definition, seed
from test_generation import (
    rig as rig_fixture,  # noqa:F401 - named isolated backend fixture
)

from pipy_harness.native.durable import generation, generation_transport
from pipy_harness.native.durable.generation import USAGE_DOC
from pipy_harness.native.durable.generation_tasks import TOOL_TASK
from pipy_harness.native.durable.ownership import StorageOwnershipError
from pipy_harness.native.durable.scheduler import (
    TaskContext,
    TaskScheduler,
    TaskSignal,
    UnsupportedTaskExecution,
)
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import EntryQuery
from pipy_harness.native.durable.tasks import define_task
from pipy_harness.native.models import ProviderResult
from pipy_harness.status import HarnessStatus


class HeldProvider:
    name = "openai"
    model_id = "gpt-test"
    supports_tool_calls = True

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finalizing = threading.Event()
        self.final_release = threading.Event()
        self.cancelled = threading.Event()
        self.worker = None

    def complete(self, request, **kwargs):
        self.worker = threading.current_thread()
        kwargs["reasoning_sink"]("private thinking")
        kwargs["stream_sink"]("private delta")
        kwargs["cancel_token"].register(self)
        self.entered.set()
        try:
            assert self.release.wait(10)
            now = datetime.now(UTC)
            return ProviderResult(
                HarnessStatus.SUCCEEDED, self.name, self.model_id, now, now, "done"
            )
        finally:
            self.finalizing.set()
            assert self.final_release.wait(10)

    def close(self):
        # Deliberately noncooperative: the real provider thread stays alive.
        self.cancelled.set()


@pytest.mark.parametrize("cancel_waiter", [False, True])
def test_close_waits_through_actual_inner_thread_and_finalizer(
    tmp_path, rig, monkeypatch, cancel_waiter
):
    session, path = rig
    provider = HeldProvider()
    monkeypatch.setattr(
        generation, "reconstruct_provider", lambda *args, **kwargs: provider
    )
    executor = generation_transport.ProviderTurnExecutor
    monkeypatch.setattr(
        generation_transport,
        "ProviderTurnExecutor",
        lambda **kwargs: executor(cancel_join_timeout_seconds=0, **kwargs),
    )
    task = definition(tmp_path, Wire())
    conversation, _, task_id = admit(session, task, seed(tmp_path))
    done, publications = completion(session, task_id)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    scheduler.resume()
    try:
        assert provider.entered.wait(5)
        if cancel_waiter:

            async def cancelled_close():
                waiter = asyncio.create_task(session.aclose())
                assert await asyncio.to_thread(provider.cancelled.wait, 5)
                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter

            asyncio.run(cancelled_close())
        else:
            with pytest.raises(TimeoutError):
                session.close(timeout=0.05)
        assert provider.cancelled.wait(5)
        assert provider.worker.is_alive()
        assert session.end is None
        assert not done.is_set() and publications == []
        if path is not None:
            with pytest.raises(StorageOwnershipError):
                open_sqlite_storage(str(path), mode="open")
        provider.release.set()
        assert provider.finalizing.wait(5)
        with pytest.raises(TimeoutError):
            session.close(timeout=0.05)
        assert provider.worker.is_alive() and session.end is None
    finally:
        provider.release.set()
        provider.final_release.set()
        session.close(timeout=10)
    assert not provider.worker.is_alive()
    assert scheduler._host_done
    row = next(r.to_tree() for r in scheduler.inspect() if r.id == task_id)
    # Healthy close rewrites storage without publication; inspect retains its
    # last committed publication snapshot, as in the DE05b close contract.
    assert row["state"]["status"] == "running"
    assert row["state"]["checkpoint"]["phase"] == "request"

    assert publications == []
    if path is not None:
        store = open_sqlite_storage(str(path), mode="open")
        try:
            from pipy_harness.native.durable.records import TaskId

            assert store.task(TaskId(task_id)).to_tree()["state"]["status"] == "pending"
            assert (
                len(
                    store.scan_entries(
                        EntryQuery({"conversationId": conversation}), 100
                    ).items
                )
                == 1
            )
        finally:
            store.close()


def test_actual_codex_transient_sse_has_one_admitted_logical_attempt(
    tmp_path, monkeypatch
):
    from test_native_openai_codex_provider import (
        FakeSseHTTPClient,
        auth_manager_with,
        credentials,
        provider_request,
    )

    from pipy_harness.native.openai_codex_provider import (
        OpenAICodexHTTPStatusError,
        OpenAICodexResponsesProvider,
    )

    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    wire = FakeSseHTTPClient(
        error=OpenAICodexHTTPStatusError(
            "Synthetic service unavailable",
            metadata={"http_status": 503},
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=wire,
        retry_sleep=lambda delay: None,
        retry_jitter=lambda: 0,
    )
    request = provider_request(tmp_path)
    standalone = provider.complete(request)
    assert standalone.status is HarnessStatus.FAILED
    assert len(wire.requests) == 4  # The actual old None/standalone behavior.
    wire.requests.clear()
    outcome, buffered = asyncio.run(
        generation_transport.execute_provider(
            provider,
            request,
            TaskContext(TaskSignal()),
        )
    )
    assert outcome.result.status is HarnessStatus.FAILED
    assert (
        outcome.result.metadata["attempt"]
        == outcome.result.metadata["max_attempts"]
        == 1
    )
    assert len(wire.requests) == 1
    assert buffered == ()


def test_actual_second_child_initializer_failure_rolls_back_every_admission(
    tmp_path, rig, monkeypatch
):
    session, _ = rig
    initialized = []

    def initial(value):
        initialized.append(value["callId"])
        if len(initialized) == 2:
            raise ValueError("private initializer text")
        return TOOL_TASK.initial(value)

    bad = define_task(TOOL_TASK.name, TOOL_TASK.version, initial, {}, TOOL_TASK.abort)
    monkeypatch.setattr(generation, "TOOL_TASK", bad)
    wire = Wire(("read", "read"))
    task = definition(tmp_path, wire)
    conversation, _, task_id = admit(session, task, seed(tmp_path, ("read",)))
    done, _ = completion(session, task_id)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    scheduler.resume()
    assert done.wait(10)
    assert initialized == ["call:0", "call:1"]
    rows = [r.to_tree() for r in scheduler.inspect()]
    assert len(rows) == 1
    assert rows[0]["state"]["outcome"]["status"] == "faulted"
    assert (
        len(
            session.commit(
                lambda tx: tx.scan_entries(
                    EntryQuery({"conversationId": conversation}), 100
                )
            ).items
        )
        == 1
    )
    assert session.snapshot(USAGE_DOC, conversation_id=conversation) is None
    assert len(wire.calls) == 1


def test_abort_winner_during_reconstruction_refuses_before_provider(
    tmp_path, rig, monkeypatch
):
    session, _ = rig
    provider = HeldProvider()
    task = definition(tmp_path, Wire())
    _, _, task_id = admit(session, task, seed(tmp_path))
    reconstructed = threading.Event()

    def reconstruct(*args, **kwargs):
        def mark(tx):
            current = tx.task(task_id).to_tree()
            tx.set_task({**current, "abortRequested": True})

        session.commit(mark)
        reconstructed.set()
        return provider

    monkeypatch.setattr(generation, "reconstruct_provider", reconstruct)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    scheduler.resume()
    assert reconstructed.wait(5)
    session.close(timeout=10)
    assert not provider.entered.is_set()
    row = next(r.to_tree() for r in scheduler.inspect() if r.id == task_id)
    assert row["abortRequested"] is True
    assert row["state"]["checkpoint"]["phase"] == "request"


def test_scoped_foreign_read_and_terminal_live_child_refusal(tmp_path, rig):
    from pipy_harness.native.agent.content import ProductContent
    from pipy_harness.native.agent.messages import AgentUserMessage
    from pipy_harness.native.durable.generation import message_entry

    session, _ = rig
    wire = Wire(("read", "read"))
    original = definition(tmp_path, wire)
    other = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    foreign = session.commit(
        lambda tx: tx.append_entry(
            other, message_entry(AgentUserMessage(ProductContent("foreign")))
        )
    )
    refusals = []

    async def prepare(record, runtime, context):
        async def read(tx, current):
            await tx.entry(foreign.id)

        with pytest.raises(UnsupportedTaskExecution, match="outside"):
            await runtime.commit(read)
        refusals.append("foreign read")
        await original.phases["prepare"](record, runtime, context)

    async def settle(record, runtime, context):
        async def terminal(tx, current):
            await tx.create_task(
                TOOL_TASK,
                {"kind": "model", "assistant": foreign.id, "callId": "uncommitted"},
            )
            return {
                "status": "terminal",
                "outcome": {"status": "completed", "result": None},
            }

        with pytest.raises(UnsupportedTaskExecution, match="ownership"):
            await runtime.commit(terminal)
        refusals.append("live child terminal")
        await original.phases["settle"](record, runtime, context)

    task = define_task(
        original.name,
        original.version,
        original.initial,
        {**original.phases, "prepare": prepare, "settle": settle},
        original.abort,
    )
    _, _, task_id = admit(session, task, seed(tmp_path, ("read",)))
    done, _ = completion(session, task_id)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    scheduler.resume()
    assert done.wait(10)
    assert refusals == ["foreign read", "live child terminal"]
    children = [
        row.to_tree()
        for row in scheduler.inspect()
        if row.to_tree()["kind"] == "pi.tool"
    ]
    assert len(children) == 2 and [row["input"]["callId"] for row in children] == [
        "call:0",
        "call:1",
    ]
    assert "foreign" not in str(wire.calls[0][1]["body"])


def test_terminal_winner_during_provider_cannot_be_overwritten_by_late_response(
    tmp_path, rig
):
    session, _ = rig
    wire = Wire()
    wire.release.clear()
    task = definition(tmp_path, wire)
    conversation, _, task_id = admit(session, task, seed(tmp_path))
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    scheduler.resume()
    try:
        assert wire.entered.wait(5)

        def terminal(tx):
            current = tx.task(task_id).to_tree()
            current.pop("memos", None)
            current.pop("restarts", None)
            tx.set_task(
                {
                    **current,
                    "state": {
                        "status": "terminal",
                        "outcome": {"status": "completed", "result": "winner"},
                    },
                }
            )

        session.commit(terminal)
    finally:
        wire.release.set()
    session.close(timeout=10)
    current = next(row.to_tree() for row in scheduler.inspect() if row.id == task_id)
    assert current["state"] == {
        "status": "terminal",
        "outcome": {"status": "completed", "result": "winner"},
    }
    assert len(wire.calls) == 1 and scheduler._host_done
    # Inspect the actual backend after SQLite reopen; the owner result and no
    # assistant/usage receipt survive, rather than a late provider settlement.
    if rig[1] is not None:
        from pipy_harness.native.durable.session_core import Session

        reopened = Session(open_sqlite_storage(str(rig[1]), mode="open"))
        try:
            rows = reopened.commit(
                lambda tx: tx.scan_entries(
                    EntryQuery({"conversationId": conversation}), 100
                )
            ).items
            assert len(rows) == 1
            assert reopened.snapshot(USAGE_DOC, conversation_id=conversation) is None
        finally:
            reopened.close(timeout=10)

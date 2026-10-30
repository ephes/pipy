"""DE05b real runtime, ordering and lifecycle barriers."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.scheduler import InvocationEnded, TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import TaskQuery
from pipy_harness.native.durable.task_values import json_equal
from pipy_harness.native.durable.tasks import define_task


async def abort(*args):
    raise AssertionError("Abort is retained for DE16")


def create(session, definition, checkpoint=None):
    conversation = session.commit(
        lambda tx: tx.create_conversation({"kind": "ownerless"})
    ).id
    return session.commit(
        lambda tx: tx.create_task(
            definition,
            {"input": [1]},
            ownership={"kind": "conversation"},
            conversation_id=conversation,
        )
    )


class CaptureClose:
    def __init__(self, inner):
        self.inner = inner
        self.latest = ()
        self.commits = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def commit(self, writes):
        result = self.inner.commit(writes)
        self.commits.append(writes)
        return result

    def close(self):
        self.latest = self.inner.scan_tasks(TaskQuery({}), 100).items
        self.inner.close()


@pytest.fixture(params=["memory", "sqlite"])
def rig(request, tmp_path):
    store = (
        MemoryStorage()
        if request.param == "memory"
        else open_sqlite_storage(str(tmp_path / "task.sqlite"), mode="create")
    )
    store = CaptureClose(store)
    session = Session(store)
    yield session, store
    session.close(timeout=5)


def test_runs_only_after_resume_and_cannot_write_after_terminal(rig):
    session, store = rig
    done = threading.Event()
    called = []
    runtimes = []

    async def start(task, runtime, context):
        called.append(task["state"]["checkpoint"])
        runtimes.append(runtime)
        assert await runtime.memo("key", None) is None
        assert await runtime.memo("key", 2) is None
        await runtime.commit(
            lambda tx, current: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": 7},
            }
        )
        done.set()

    definition = define_task(
        "work", 1, lambda _: {"phase": "start"}, {"start": start}, abort
    )
    create(session, definition)
    scheduler = TaskScheduler(session, [definition])
    scheduler.open()
    assert called == []
    scheduler.resume()
    scheduler.resume()
    assert done.wait(5)
    session.close(timeout=5)
    assert len(called) == 1
    assert scheduler.inspect()[0].to_tree()["state"]["outcome"]["result"] == 7
    with pytest.raises(InvocationEnded):
        asyncio.run(runtimes[0].memo("late", 3))


def test_close_joins_finally_and_latest_checkpoint(rig):
    session, store = rig
    entered = threading.Event()
    finally_entered = threading.Event()
    release = threading.Event()

    async def start(task, runtime, context):
        try:
            await runtime.commit(
                lambda tx, current: {
                    "status": "running",
                    "checkpoint": {"phase": "start", "latest": True},
                }
            )
            entered.set()
            await context.signal.wait()
        finally:
            finally_entered.set()
            await asyncio.to_thread(release.wait)

    definition = define_task(
        "work", 1, lambda _: {"phase": "start"}, {"start": start}, abort
    )
    id = create(session, definition)
    scheduler = TaskScheduler(session, [definition])
    scheduler.open()
    scheduler.resume()
    assert entered.wait(5)
    with ThreadPoolExecutor() as pool:
        closer = pool.submit(session.close, timeout=5)
        assert finally_entered.wait(5)
        assert not closer.done()
        assert session.end is None
        release.set()
        closer.result(5)
    assert scheduler._host_done
    row = next(r.to_tree() for r in store.latest if r.id == id)
    assert row["state"] == {
        "status": "pending",
        "checkpoint": {"phase": "start", "latest": True},
    }
    assert "restarts" not in row


@pytest.mark.parametrize(
    ("a", "b", "equal"),
    [
        ({"a": 1, "b": 2}, {"b": 2.0, "a": 1.0}, True),
        (-0.0, 0, True),
        (True, 1, False),
        ({}, {"a": None}, False),
        ([1, 2], [2, 1], False),
        ("\U0001f600", "\ud83d\ude00", True),
    ],
)
def test_task_progress_equality_is_separate_from_document_representation(a, b, equal):
    assert json_equal(OwnedJson(a).thaw(), OwnedJson(b).thaw()) is equal


def test_utf16_phase_name_survives_checkpoint_storage(rig):
    session, _ = rig
    done = threading.Event()

    async def phase(task, runtime, context):
        await runtime.commit(
            lambda tx, current: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": None},
            }
        )
        done.set()

    definition = define_task(
        "\ud83d\ude00",
        -0.0,
        lambda _: {"phase": "\U0001f600"},
        {"\ud83d\ude00": phase},
        abort,
    )
    create(session, definition)
    scheduler = TaskScheduler(session, [definition])
    scheduler.open()
    scheduler.resume()
    assert done.wait(5)


def test_close_owns_tentative_reservation_before_ack_even_with_held_listener(rig):
    session, store = rig
    ack, release_ack = threading.Event(), threading.Event()
    listener, release_listener = threading.Event(), threading.Event()
    retired = threading.Event()
    calls = []

    async def start(task, runtime, context):
        calls.append("started")
        await context.signal.wait()

    definition = define_task(
        "work", 1, lambda _: {"phase": "start"}, {"start": start}, abort
    )
    id = create(session, definition)
    scheduler = TaskScheduler(session, [definition])
    scheduler.open()
    real_commit = store.commit

    def commit(writes):
        seq = real_commit(writes)
        if any(
            w.to_tree().get("value", {}).get("state", {}).get("status") == "running"
            for w in writes
        ):
            ack.set()
            assert release_ack.wait(5)
        return seq

    store.commit = commit
    real_retire = scheduler._retire

    def retire(invocation):
        real_retire(invocation)
        retired.set()

    scheduler._retire = retire

    def hold_listener():
        listener.set()
        assert release_listener.wait(5)

    session.subscribe_close(hold_listener)
    scheduler.resume()
    assert ack.wait(5)
    with ThreadPoolExecutor() as pool:
        closer = pool.submit(session.close, timeout=5)
        try:
            assert listener.wait(5)
            assert session.end is None
            with scheduler._guard:
                assert scheduler._invocation.task_id == id
            release_ack.set()
            assert retired.wait(5)
            assert calls == []
            assert session.end is None
        finally:
            release_ack.set()
            release_listener.set()
        closer.result(5)
    assert session.end.reason == "closed"
    assert store.latest[0].to_tree()["state"]["status"] == "pending"


def test_zero_invocation_close_joins_host():
    session = Session(MemoryStorage())
    scheduler = TaskScheduler(session, [])
    session.close(timeout=5)
    assert scheduler._host_done
    assert not scheduler._thread.is_alive()


def test_host_creation_failure_closes_owner(monkeypatch):
    session = Session(MemoryStorage())
    real_start = threading.Thread.start

    def start(thread):
        if thread.name == "pipy-durable-phases":
            raise RuntimeError("host startup refused")
        real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    with pytest.raises(RuntimeError, match="host startup refused"):
        TaskScheduler(session, [])
    assert session.wait_closed(timeout=5).reason == "failed"


def test_close_during_host_startup(monkeypatch):
    session = Session(MemoryStorage())
    reached, release = threading.Event(), threading.Event()
    real_start = threading.Thread.start

    def start(thread):
        if thread.name == "pipy-durable-phases":
            reached.set()
            assert release.wait(5)
        real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    with ThreadPoolExecutor() as pool:
        created = pool.submit(TaskScheduler, session, [])
        try:
            assert reached.wait(5)
            session.request_close()
            assert session.end is None
        finally:
            release.set()
        scheduler = created.result(5)
    session.close(timeout=5)
    assert scheduler._host_done


def test_join_worker_creation_failure_retains_hold_and_retry_closes(monkeypatch):
    session = Session(MemoryStorage())
    scheduler = TaskScheduler(session, [])
    real_start = threading.Thread.start
    attempts = []

    def start(thread):
        if thread.name == "pipy-durable-join" and not attempts:
            attempts.append(1)
            raise RuntimeError("join launch refused")
        real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    with pytest.raises(RuntimeError, match="join launch refused"):
        session.request_close()
    assert session.end is None
    assert scheduler._thread.is_alive()
    session.close(timeout=5)
    assert session.end.reason == "failed"
    assert not scheduler._thread.is_alive()


@pytest.mark.parametrize("changed", [False, True])
def test_task_progress_resets_restarts_only_for_structural_state_change(rig, changed):
    session, store = rig
    reached = threading.Event()

    async def phase(task, runtime, context):
        await runtime.memo("memo", {"write": True})
        await runtime.commit(
            lambda tx, current: {
                "status": "running",
                "checkpoint": {"number": True if changed else 0.0, "phase": "start"},
            }
        )
        reached.set()
        await context.signal.wait()

    definition = define_task(
        "work", 1, lambda _: {"phase": "start", "number": -0.0}, {"start": phase}, abort
    )
    id = create(session, definition)

    def seed(tx):
        tx.set_task({**tx.task(id).to_tree(), "restarts": 3})

    session.commit(seed)
    scheduler = TaskScheduler(session, [definition])
    scheduler.open()
    scheduler.resume()
    assert reached.wait(5)
    row = session.commit(lambda tx: tx.task(id)).to_tree()
    assert row.get("restarts") == (None if changed else 3)
    session.close(timeout=5)


@pytest.mark.parametrize("scope", ["session", "conversation", "task"])
def test_runtime_documents_use_invocation_scope_for_acquire_and_retire(rig, scope):
    from pipy_harness.native.durable.documents import define_doc, define_doc_family

    session, store = rig
    policy = {"history": "latest", "fork": "current"} if scope == "conversation" else {}
    singleton = define_doc(
        "runtime.single",
        1,
        scope,
        lambda: {"n": 0},
        **policy,
        checkpoint_when=lambda _value, _ops, _info: False,
    )
    family = define_doc_family(
        "runtime.family",
        1,
        scope,
        lambda seed: seed,
        **policy,
        checkpoint_when=lambda _value, _ops, _info: False,
    )
    acquired, retired, release = threading.Event(), threading.Event(), threading.Event()

    async def phase(task, runtime, context):
        async def acquire(tx, current):
            (await tx.doc(singleton))["n"] = 7
            (await tx.doc(family, key="", seed={"n": 1}))["n"] = 8
            await tx.doc(family, key="keep", seed={"n": 9})

        await runtime.commit(acquire)
        acquired.set()
        await asyncio.to_thread(release.wait)

        async def retire(tx, current):
            await tx.retire_doc(singleton)
            await tx.retire_doc(family, key="")

        await runtime.commit(retire)
        retired.set()
        await context.signal.wait()

    definition = define_task(
        "work", 1, lambda _: {"phase": "start"}, {"start": phase}, abort
    )
    id = create(session, definition)
    conversation = session.commit(lambda tx: tx.task(id)).to_tree()["conversationId"]
    ids = (
        {"task_id": id}
        if scope == "task"
        else {"conversation_id": conversation}
        if scope == "conversation"
        else {}
    )
    scheduler = TaskScheduler(session, [definition])
    scheduler.open()
    scheduler.resume()
    try:
        assert acquired.wait(5)
        assert session.snapshot(singleton, **ids).value.thaw() == {"n": 7}
        assert session.snapshot(family, key="", **ids).value.thaw() == {"n": 8}
    finally:
        release.set()
    assert retired.wait(5)
    assert session.snapshot(singleton, **ids) is None
    assert session.snapshot(family, key="", **ids) is None
    assert session.snapshot(family, key="keep", **ids).value.thaw() == {"n": 9}
    session.close(timeout=5)


def test_atomic_terminal_entry_and_all_task_document_retirements(rig):
    from pipy_harness.native.durable.documents import define_doc
    from pipy_harness.native.durable.storage import DocumentQuery, StorageWrite

    session, store = rig
    done = threading.Event()
    publications = []
    session.subscribe_commits(publications.append)

    async def phase(task, runtime, context):
        async def finish(tx, current):
            await tx.append_entry({"kind": "result", "data": {"done": True}})
            return {
                "status": "terminal",
                "outcome": {"status": "completed", "result": 42},
            }

        await runtime.commit(finish)
        done.set()

    definition = define_task(
        "work", 1, lambda _: {"phase": "start"}, {"start": phase}, abort
    )
    id = create(session, definition)

    def seed(tx):
        for index in range(132):
            tx.doc(
                define_doc(
                    "unknown." + str(index),
                    1,
                    "task",
                    lambda: {"x": 1},
                    checkpoint_when=lambda _value, _ops, _info: False,
                ),
                task_id=id,
            )

    session.commit(seed)
    scheduler = TaskScheduler(session, [definition])
    scheduler.open()
    scheduler.resume()
    assert done.wait(5)
    terminal = [
        p
        for p in publications
        if any(
            type(c) is StorageWrite
            and c.to_tree().get("value", {}).get("state", {}).get("status")
            == "terminal"
            for c in p.changes
        )
    ]
    assert len(terminal) == 1
    batch = next(
        batch
        for batch in store.commits
        if any(
            w.to_tree().get("value", {}).get("state", {}).get("status") == "terminal"
            for w in batch
        )
    )
    writes = [c.to_tree() for c in batch]
    assert len([w for w in writes if w["type"] == "document.retire"]) == 132
    entry = next(w["value"] for w in writes if w["type"] == "entry")
    assert entry["byTaskId"] == id and entry["data"] == {"done": True}
    assert (
        store.scan_documents(
            DocumentQuery({"scope": {"kind": "task", "taskId": id}, "at": "current"}),
            200,
        ).items
        == ()
    )


def test_runtime_queued_before_end_refuses_when_executed_after_end(rig, monkeypatch):
    from pipy_harness.native.durable.session import SessionLifecycle

    session, _ = rig
    ready, finish_phase = threading.Event(), threading.Event()
    line_held, release_line, late_queued = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    runtime_holder = []
    callbacks = []

    async def phase(task, runtime, context):
        runtime_holder.append(runtime)
        ready.set()
        await asyncio.to_thread(finish_phase.wait)

    definition = define_task(
        "work", 1, lambda _: {"phase": "start"}, {"start": phase}, abort
    )
    create(session, definition)
    scheduler = TaskScheduler(session, [definition])
    scheduler.open()
    scheduler.resume()
    assert ready.wait(5)
    real_enter = SessionLifecycle._line_enter
    step_queued = threading.Event()

    def enter(owner, ticket):
        real_enter(owner, ticket)
        with owner._cond:
            count = len(owner._queue)
        if count == 1:
            step_queued.set()
        elif count == 2:
            late_queued.set()

    monkeypatch.setattr(SessionLifecycle, "_line_enter", enter)

    def hold(tx):
        line_held.set()
        assert release_line.wait(5)

    async def late():
        await runtime_holder[0].commit(
            lambda tx, current: callbacks.append("forbidden")
        )

    with ThreadPoolExecutor(2) as pool:
        held = pool.submit(session.commit, hold)
        try:
            assert line_held.wait(5)
            finish_phase.set()
            assert step_queued.wait(5)
            operation = pool.submit(asyncio.run, late())
            assert late_queued.wait(5)
        finally:
            finish_phase.set()
            release_line.set()
        held.result(5)
        with pytest.raises(InvocationEnded):
            operation.result(5)
    assert callbacks == []
    session.close(timeout=5)


def test_unsupported_records_preserved_without_dispatch(rig):
    session, store = rig
    calls = []

    async def forbidden(*args):
        calls.append("forbidden")

    definition = define_task(
        "known", 1, lambda _: {"phase": "start"}, {"start": forbidden}, abort
    )
    rows = []
    for index, state in enumerate(
        [
            {
                "status": "waiting",
                "checkpoint": {"phase": "start"},
                "on": [],
                "policy": "allSettled",
            },
            {"status": "sleeping", "checkpoint": {"phase": "start"}, "until": 0},
            {"status": "completing", "outcome": {"status": "completed", "result": 1}},
            {"status": "pending", "checkpoint": {"phase": "start"}},
            {"status": "pending", "checkpoint": {"phase": "start"}},
            {"status": "pending", "checkpoint": {"phase": "start"}},
        ]
    ):
        id = create(session, definition)

        def seed(tx):
            row = tx.task(id).to_tree()
            row["state"] = state
            if index == 3:
                row["kind"] = "missing"
            elif index == 4:
                row["version"] = 2
            elif index == 5:
                row["abortRequested"] = True
            tx.set_task(row)

        session.commit(seed)
        rows.append(store.task(id))
    scheduler = TaskScheduler(session, [definition])
    scheduler.open()
    drained = threading.Event()
    original = scheduler._reserve

    async def reserve():
        result = await original()
        if result is None:
            drained.set()
        return result

    scheduler._reserve = reserve
    scheduler.resume()
    assert drained.wait(5)
    assert calls == []
    assert scheduler.inspect() == tuple(rows)


def test_unknown_phase_and_memo_only_no_progress_fault(rig):
    session, store = rig

    async def chatter(task, runtime, context):
        await runtime.memo("chat", 1)
        await runtime.commit(
            lambda tx, current: {"status": "running", "checkpoint": {"phase": "start"}}
        )

    definitions = [
        define_task("missing-phase", 1, lambda _: {"phase": "absent"}, {}, abort),
        define_task(
            "chatter", 1, lambda _: {"phase": "start"}, {"start": chatter}, abort
        ),
    ]
    ids = [create(session, d) for d in definitions]
    ended = threading.Event()

    def observe(publication):
        if store.task(ids[-1]).to_tree()["state"]["status"] == "terminal":
            ended.set()

    session.subscribe_commits(observe)
    scheduler = TaskScheduler(session, definitions)
    scheduler.open()
    scheduler.resume()
    assert ended.wait(5)
    assert [store.task(id).to_tree()["state"]["outcome"]["status"] for id in ids] == [
        "faulted",
        "faulted",
    ]


def test_live_owned_child_prevents_terminal_parent(rig):
    session, store = rig
    reached, release, stopped = threading.Event(), threading.Event(), threading.Event()

    async def parent(task, runtime, context):
        reached.set()
        await asyncio.to_thread(release.wait)
        try:
            await runtime.commit(
                lambda tx, current: {
                    "status": "terminal",
                    "outcome": {"status": "completed", "result": None},
                }
            )
        finally:
            stopped.set()

    definition = define_task(
        "parent", 1, lambda _: {"phase": "start"}, {"start": parent}, abort
    )
    id = create(session, definition)
    scheduler = TaskScheduler(session, [definition])
    scheduler.open()
    scheduler.resume()
    assert reached.wait(5)
    child = define_task("child", 1, lambda _: {"phase": "later"}, {}, abort)
    session.commit(
        lambda tx: tx.create_task(child, None, ownership={"kind": "task", "taskId": id})
    )
    release.set()
    assert stopped.wait(5)
    session.close(timeout=5)
    row = next(r.to_tree() for r in store.latest if r.id == id)
    assert row["state"]["status"] == "pending"


def test_cancelled_close_waiter_cannot_cancel_invocation_cleanup(rig):
    session, _ = rig
    ready, finally_entered, release = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )

    async def phase(task, runtime, context):
        ready.set()
        try:
            await context.signal.wait()
        finally:
            finally_entered.set()
            await asyncio.to_thread(release.wait)

    definition = define_task(
        "work", 1, lambda _: {"phase": "start"}, {"start": phase}, abort
    )
    create(session, definition)
    scheduler = TaskScheduler(session, [definition])
    scheduler.open()
    scheduler.resume()
    assert ready.wait(5)

    async def close():
        closer = asyncio.create_task(session.aclose())
        assert await asyncio.to_thread(finally_entered.wait, 5)
        closer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closer
        assert session.end is None
        release.set()
        assert (await asyncio.wait_for(session.await_closed(), 5)).reason == "closed"

    try:
        asyncio.run(close())
    finally:
        release.set()


def test_initial_scan_and_index_installation_share_session_line(rig, monkeypatch):
    from pipy_harness.native.durable import scheduler as module
    from pipy_harness.native.durable.session import SessionLifecycle

    session, _ = rig
    definition = define_task("work", 1, lambda _: {"phase": "later"}, {}, abort)
    first = create(session, definition)
    scheduler = TaskScheduler(session, [definition])
    scanned, release, queued = threading.Event(), threading.Event(), threading.Event()
    original = module._tasks

    async def scan(tx):
        result = await original(tx)
        scanned.set()
        assert await asyncio.to_thread(release.wait, 5)
        return result

    monkeypatch.setattr(module, "_tasks", scan)
    enter = SessionLifecycle._line_enter

    def record(owner, ticket):
        enter(owner, ticket)
        with owner._cond:
            waiting = bool(owner._queue)
        if waiting:
            queued.set()

    monkeypatch.setattr(SessionLifecycle, "_line_enter", record)
    with ThreadPoolExecutor(2) as pool:
        opened = pool.submit(scheduler.open)
        try:
            assert scanned.wait(5)
            created = pool.submit(create, session, definition)
            assert queued.wait(5)
        finally:
            release.set()
        opened.result(5)
        second = created.result(5)
    assert {r.id for r in scheduler.inspect()} == {first, second}


def test_close_rewrite_keeps_admitted_checkpoint_and_abort_mark_without_publication(
    rig,
):
    session, store = rig
    phase_ready, held, release = threading.Event(), threading.Event(), threading.Event()

    async def phase(task, runtime, context):
        phase_ready.set()
        await context.signal.wait()

    definition = define_task(
        "work", 1, lambda _: {"phase": "start"}, {"start": phase}, abort
    )
    id = create(session, definition)
    scheduler = TaskScheduler(session, [definition])
    scheduler.open()
    scheduler.resume()
    assert phase_ready.wait(5)
    publications = []
    session.subscribe_commits(publications.append)

    def mark(tx):
        current = tx.task(id).to_tree()
        held.set()
        assert release.wait(5)
        tx.set_task(
            {
                **current,
                "abortRequested": True,
                "state": {
                    "status": "running",
                    "checkpoint": {"phase": "latest", "preserve": 42},
                },
            }
        )

    with ThreadPoolExecutor() as pool:
        admitted = pool.submit(session.commit, mark)
        try:
            assert held.wait(5)
            session.request_close()
            assert session.end is None
        finally:
            release.set()
        admitted.result(5)
    session.close(timeout=5)
    row = next(r.to_tree() for r in store.latest if r.id == id)
    assert row["abortRequested"] is True
    assert row["state"] == {
        "status": "pending",
        "checkpoint": {"phase": "latest", "preserve": 42},
    }
    assert "restarts" not in row
    assert len(publications) == 1

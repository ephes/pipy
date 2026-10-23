"""Actual unload participates in the existing FIFO line and cold reads."""

from __future__ import annotations

import asyncio

import pytest
from storage_contract_cases import backend as backend
from test_session_callback_authority import core_mutant
from test_session_checkpoints import unload
from test_session_commit import Store, Worker, auntil, entered
from test_session_documents import address, commit, snapshot, token
from test_session_documents import mode as mode
from test_session_documents import rig as rig

from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.documents import define_doc
from pipy_harness.native.durable.session import SessionClosedError, SessionFailed
from pipy_harness.native.durable.session_core import ReentrantSessionCall, Session

pytestmark = pytest.mark.parametrize(
    "backend", ["memory", "sqlite", "reopen"], indirect=True
)
WAIT = 5


def test_unload_all_scopes_family_keys_preserves_old_snapshots_and_does_no_io(rig):
    r = rig
    specs = [
        (
            token(scope, family=family),
            address(scope, **({"key": key} if family else {})),
            family,
        )
        for scope in ("session", "conversation", "task")
        for family in (False, True)
        for key in (["", "a", "\ud800"] if family else [None])
    ]

    async def edit(tx):
        for spec, options, family in specs:
            draft = await tx.doc(
                spec, **options, **({"seed": "seed"} if family else {})
            )
            if family:
                draft["hits"] += 1
            else:
                draft["lines"].append(1)

    async def run():
        await unload(r)  # Empty unload is still an admitted line operation.
        await commit(r, edit)
        for _ in range(8):
            await commit(r, edit)
        old = [await snapshot(r, spec, **options) for spec, options, _ in specs]
        assert all(value.deltas_since_base == 8 for value in old)
        before = len(r.store.calls), len(r.publications)
        await unload(r)
        assert before == (len(r.store.calls), len(r.publications))
        for (spec, options, _), previous in zip(specs, old, strict=True):
            cold = await snapshot(r, spec, **options)
            assert cold is not previous
            assert cold.value == previous.value and cold.version == previous.version
            assert cold.deltas_since_base == previous.deltas_since_base == 8
        assert r.store.count("mint_id") == len(specs)

    r.mode.run(run)


def test_unload_after_ack_adoption_publication_and_cancelled_queue_detaches(backend):
    async def run():
        store = Store(backend.new())
        session = Session(store)
        spec = define_doc("unload.ordered", 1, "session", lambda: {"n": 0})
        events = []

        async def edit(tx):
            (await tx.doc(spec))["n"] += 1

        try:
            await session.acommit(edit)
            old = await session.asnapshot(spec)
            session.observe_commits(
                lambda _: events.append(("publication", session.snapshot(spec)))
            )
            gate = store.hold("commit")
            owner = asyncio.create_task(session.acommit(edit))
            await entered(gate)
            cancelled = asyncio.create_task(session.aunload_documents())
            await auntil(lambda: len(session._owner._queue) == 1, "unload queued")
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            assert not session._owner._queue
            queued = asyncio.create_task(session.aunload_documents())
            await auntil(lambda: len(session._owner._queue) == 1, "accepted unload")
            assert await session.asnapshot(spec) is old and not queued.done()
            gate.release.set()
            async with asyncio.timeout(WAIT):
                await owner
                await queued
            assert events[0][0] == "publication"
            adopted = events[0][1]
            assert (
                adopted.value == OwnedJson({"n": 2}) and adopted.deltas_since_base == 1
            )
            before = store.count("document")
            cold = await session.asnapshot(spec)
            assert store.count("document") == before + 1
            assert cold is not adopted and cold.value == adopted.value
            assert cold.deltas_since_base == adopted.deltas_since_base
            assert old.value == OwnedJson({"n": 1}) and old.deltas_since_base == 0
            assert len(events) == 1 and store.count("commit") == 2
        finally:
            store.release_all()
            await session.aclose()

    asyncio.run(run())


def test_unload_foreign_sync_waits_own_loop_refuses_and_accepted_close_drains(backend):
    async def run():
        store = Store(backend.new())
        session = Session(store)
        gate = asyncio.Event()
        started = asyncio.Event()

        async def hold(_tx):
            with pytest.raises(ReentrantSessionCall):
                session.unload_documents()
            started.set()
            await gate.wait()

        owner = asyncio.create_task(session.acommit(hold))
        worker = None
        try:
            await started.wait()
            with pytest.raises(RuntimeError, match="would block the event loop"):
                session.unload_documents()
            worker = Worker("foreign-unload", session.unload_documents)
            worker.thread.start()
            await auntil(
                lambda: len(session._owner._queue) == 1, "foreign unload queued"
            )
            accepted = asyncio.create_task(session.aunload_documents())
            await auntil(lambda: len(session._owner._queue) == 2, "async unload queued")
            session.request_close()
            assert store.count("close") == 0
            gate.set()
            async with asyncio.timeout(WAIT):
                await owner
                await accepted
                await session.await_closed()
            worker.value()
            assert store.count("commit") == 0 and store.count("close") == 1
            with pytest.raises(SessionClosedError):
                await session.aunload_documents()
            with pytest.raises(SessionClosedError):
                session.unload_documents()
        finally:
            gate.set()
            if worker is not None:
                worker.join()
            await session.aclose()

    asyncio.run(run())


def test_unload_failed_owner_retains_first_cause_and_rejects_before_job(backend):
    session = Session(backend.new())
    first, second = ValueError("first"), ValueError("second")
    session.fail(first)
    session.fail(second)
    try:
        with pytest.raises(SessionFailed) as caught:
            session.unload_documents()
        assert caught.value.__cause__ is first

        async def run():
            with pytest.raises(SessionFailed) as caught:
                await session.aunload_documents()
            assert caught.value.__cause__ is first

        asyncio.run(run())
        assert session.failure is first
    finally:
        session.close()


async def unload_witness(session_type, inner, reached):
    store = Store(inner)
    session = session_type(store)
    spec = define_doc("mutant.unload", 1, "session", lambda: {"n": 0})
    gate, started = asyncio.Event(), asyncio.Event()
    tasks = []

    async def hold(_tx):
        started.set()
        await gate.wait()

    try:
        await session.acommit(lambda tx: tx.doc(spec))
        old = await session.asnapshot(spec)
        tasks.append(asyncio.create_task(session.acommit(hold)))
        await started.wait()
        tasks.append(asyncio.create_task(session.aunload_documents()))
        await auntil(lambda: len(session._owner._queue) == 1, "real unload admission")
        reached.append("queued")
        assert session._cache.peek(spec) is old
        gate.set()
        async with asyncio.timeout(WAIT):
            await asyncio.gather(*tasks)
        assert store.count("commit") == 1
        cold = await session.asnapshot(spec)
        assert cold is not old and cold.value == old.value
    finally:
        gate.set()
        if tasks:
            async with asyncio.timeout(WAIT):
                await asyncio.gather(*tasks, return_exceptions=True)
        await session.aclose()


@pytest.mark.parametrize("mutation", ["no-clear", "eager-clear", "write"])
def test_unload_production_mutants_fail_real_queued_witness(backend, mutation):
    old = '        await self._on_line_async("aunload_documents", self._cache.clear)'
    changed = {
        "no-clear": '        await self._on_line_async("aunload_documents", lambda: None)',
        "eager-clear": "        self._cache.clear()\n" + old,
        "write": '        await self._on_line_async("aunload_documents", lambda: self._owner.storage.commit(()))',
    }
    reached = []
    asyncio.run(unload_witness(Session, backend.new(), reached))
    assert reached == ["queued"]
    mutant = core_mutant(mutation.replace("-", "_"), old, changed[mutation])
    reached.clear()
    with pytest.raises(AssertionError):
        asyncio.run(unload_witness(mutant.Session, backend.new(), reached))
    assert reached == ["queued"]

"""DE02b2b first exposed internal Session over the b1a owner, b2a core and real Memory.

Pi anchors (pinned 2ec55d80, read via git show): src/session/session.ts
(``commitWith``, ``#runCommit`` 716–767, ``#publish`` 770–817, ``close``
495–528, ``fail`` 222–229, ``contained`` 920–927, ``assertUsable``),
test/harness-storage-failure.test.ts 267, 276, 291, 328, 431, 466 and
test/session-documents.test.ts 120, 137, 250, 284, 311, 337, 352, 440, 459,
549, 591, 605, 622, 632. Every behaviour has a sync (``commit``) and an async
(``acommit``) variant unless it has no meaning in one mode (pending operations,
``Task.cancel`` and awaitable listeners are async-only; a sync callback
returning an awaitable is sync-only). Ordering evidence is owned Events, bounded
joins, owner-state witnesses (queue length, holder) and event-loop scheduling;
no sleep is ordering evidence. Every scenario takes the modules under test, so
the same bodies run against private source mutants of ``session.py``, ``tx.py``
and ``session_core.py`` (negative controls at the end).
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import gc
import io
import sys
import threading
import time
import types
import uuid
import warnings
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from task_support import task_definition

from pipy_harness.native.durable import session as real_session
from pipy_harness.native.durable import session_core as real_core
from pipy_harness.native.durable import tx as real_tx
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.storage import (
    EntryQuery,
    StorageClosedError,
    StorageRequestError,
    StorageWrite,
)

WAIT = 5.0
"""Bound for every wait/join; the mutant matrix lowers it."""

STORAGE_METHODS = frozenset(
    {
        "commit",
        "mint_id",
        "conversation",
        "scan_conversations",
        "entry",
        "find_latest_head_marker",
        "scan_entries",
        "task",
        "scan_tasks",
        "submission",
        "scan_submissions",
        "submission_by_request",
        "find_document",
        "document",
        "scan_documents",
        "close",
    }
)


@dataclass(frozen=True)
class Mods:
    """The modules under test: real, or a consistent mutant build."""

    session: types.ModuleType
    tx: types.ModuleType
    core: types.ModuleType


REAL = Mods(real_session, real_tx, real_core)


# -- controlled delegating backend ----------------------------------------------------


class GateTimeout(Exception):
    """A held backend call was never released (test failure, not product)."""


class Gate:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def hold(self) -> None:
        self.entered.set()
        if not self.release.wait(WAIT):
            raise GateTimeout("held backend call was never released")


class Store:
    """Explicit delegating wrapper around one real MemoryStorage (test only).

    ``fault`` raises before delegating; ``fault_after`` delegates to the real
    Memory first (the write is durable) and then raises. ``commits`` and
    ``returned`` record only batches the real Memory committed.
    """

    def __init__(self, inner: MemoryStorage) -> None:
        self.inner = inner
        self.lock = threading.Lock()
        self.calls: list[str] = []
        self.commits: list[tuple[StorageWrite, ...]] = []
        self.returned: list[int] = []
        self.probes: list[Any] = []
        self.probe: Callable[[MemoryStorage], Any] | None = None
        self._holds: dict[str, Gate] = {}
        self._gates: list[Gate] = []
        self._faults: dict[str, BaseException] = {}
        self._after: dict[str, BaseException] = {}
        self.before: dict[str, Callable[[], None]] = {}

    def hold(self, name: str) -> Gate:
        gate = Gate()
        with self.lock:
            self._holds[name] = gate
            self._gates.append(gate)
        return gate

    def fault(self, name: str, error: BaseException) -> None:
        with self.lock:
            self._faults[name] = error

    def fault_after(self, name: str, error: BaseException) -> None:
        with self.lock:
            self._after[name] = error

    def release_all(self) -> None:
        with self.lock:
            gates = list(self._gates)
        for gate in gates:
            gate.release.set()

    def count(self, name: str) -> int:
        with self.lock:
            return self.calls.count(name)

    def _run(self, name: str, *args: Any, **kwargs: Any) -> Any:
        with self.lock:
            self.calls.append(name)
            gate = self._holds.pop(name, None)
            fault = self._faults.pop(name, None)
            after = self._after.pop(name, None)
            before = self.before.pop(name, None)
        if before is not None:
            before()
        if gate is not None:
            gate.hold()
        if fault is not None:
            raise fault
        result = getattr(self.inner, name)(*args, **kwargs)
        if name == "commit":
            with self.lock:
                self.commits.append(args[0])
                self.returned.append(result)
            if self.probe is not None:
                self.probes.append(self.probe(self.inner))
        if after is not None:
            raise after
        return result

    def __getattr__(self, name: str) -> Any:
        if name in STORAGE_METHODS:
            return partial(self._run, name)
        raise AttributeError(name)


# -- owned threads, waits and async helpers ---------------------------------------------


class Worker:
    def __init__(self, name: str, fn: Callable[[], Any]) -> None:
        self.fn = fn
        self.result: Any = None
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, name="de02b2b-" + name)
        self.thread.daemon = True

    def _run(self) -> None:
        try:
            self.result = self.fn()
        except BaseException as error:  # noqa: BLE001 - delivered to the test thread
            self.error = error

    def join(self) -> Worker:
        self.thread.join(WAIT)
        assert not self.thread.is_alive(), f"{self.thread.name} was not reaped"
        return self

    def value(self) -> Any:
        self.join()
        if self.error is not None:
            raise self.error
        return self.result


def until(predicate: Callable[[], bool], what: str) -> None:
    """Bounded wait for an owned-state witness; the yield is not evidence."""
    deadline = time.monotonic() + WAIT
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.001)


async def auntil(predicate: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + WAIT
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.001)


async def entered(gate: Gate) -> None:
    """Wait on the loop for a held backend call on an executor thread."""
    loop = asyncio.get_running_loop()
    assert await loop.run_in_executor(None, gate.entered.wait, WAIT)


async def spin(turns: int = 25) -> None:
    """Let the event loop run ready callbacks (deterministic scheduling)."""
    for _ in range(turns):
        await asyncio.sleep(0)


def run_async(main: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """``asyncio.run`` with loop exception reports (never-retrieved) as failures."""
    reports: list[dict[str, Any]] = []

    async def wrapper() -> Any:
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, context: reports.append(context)
        )
        # A bound for the whole body, so a mutant can never hang the run.
        return await asyncio.wait_for(main(), 6 * WAIT)

    result = asyncio.run(wrapper())
    gc.collect()
    assert not reports, reports
    return result


class Ready:
    """A sync value as an awaitable that never suspends (sync-mode bodies)."""

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value

    def __await__(self) -> Any:
        return self.value
        yield  # pragma: no cover - makes this a generator


def drive(body: Coroutine[Any, Any, Any]) -> Any:
    """Run a sync-mode body that awaits only ``Ready`` values."""
    try:
        step = body.send(None)
    except StopIteration as stop:
        return stop.value
    body.close()
    raise AssertionError(f"sync-mode body suspended on {step!r}")


def ready(value: Ready) -> Any:
    """The value of a sync-mode entry point outside a body."""
    assert type(value) is Ready
    return value.value


class Adapter:
    """Sync-mode view of a ``SyncTx``: every result is awaitable at once."""

    def __init__(self, tx: Any) -> None:
        self.tx = tx

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.tx, name)

        def call(*args: Any, **kwargs: Any) -> Ready:
            return Ready(method(*args, **kwargs))

        return call


Callback = Callable[[Any], Coroutine[Any, Any, Any]]


class Window(threading.Condition):
    """The owner's Condition with one test barrier after a lock exit.

    When ``predicate`` first holds right after a release, the releasing thread
    signals ``reached`` and waits (bounded) for ``resume``: a deterministic
    pause at an exact window between two locked steps.
    """

    def __init__(self, predicate: Callable[[], bool]) -> None:
        super().__init__(threading.Lock())
        self.predicate = predicate
        self.reached = threading.Event()
        self.resume = threading.Event()

    def __exit__(self, *exc: Any) -> None:
        super().__exit__(*exc)
        if not self.reached.is_set() and self.predicate():
            self.reached.set()
            self.resume.wait(WAIT)


class Mode:
    """Uniform entry points; scenario bodies are ``async def`` in both modes."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.sync = name == "sync"

    def run(self, body: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
        return drive(body()) if self.sync else run_async(body)

    def commit(self, rig: Rig, cb: Callback, **kw: Any) -> Any:
        if self.sync:
            return Ready(rig.session.commit(lambda tx: drive(cb(Adapter(tx))), **kw))
        return rig.session.acommit(cb, **kw)

    def snapshot(self, rig: Rig, spec: Any, conversation: int | None = None) -> Any:
        if self.sync:
            return Ready(rig.session.snapshot(spec, conversation_id=conversation))
        return rig.session.asnapshot(spec, conversation_id=conversation)

    def unload(self, rig: Rig) -> Any:
        if self.sync:
            return Ready(rig.session.unload_documents())
        return rig.session.aunload_documents()

    def close(self, rig: Rig) -> Any:
        if self.sync:
            return Ready(rig.session.close(timeout=WAIT))
        return asyncio.wait_for(rig.session.aclose(), WAIT)

    def closed(self, rig: Rig) -> Any:
        if self.sync:
            return Ready(rig.session.wait_closed(timeout=WAIT))
        return asyncio.wait_for(rig.session.await_closed(), WAIT)


SYNC, ASYNC = Mode("sync"), Mode("async")


# -- the rig -------------------------------------------------------------------------------


class Rig:
    def __init__(self, M: Mods) -> None:
        self.M = M
        self.inner = MemoryStorage()
        self.store = Store(self.inner)
        self.reports: list[tuple[BaseException, str]] = []
        self.log: list[str] = []
        self.on_report: Callable[[BaseException, str], None] | None = None
        self.session: Any = M.core.Session(
            self.store, reporter=self._report, now=lambda: 1000.0
        )
        self.publications: list[Any] = []
        self.session.subscribe_commits(self.publications.append)
        self.workers: list[Worker] = []
        self.events: list[threading.Event] = []
        self.inits: dict[str, int] = {}

    def _report(self, error: BaseException, info: str) -> None:
        self.reports.append((error, info))
        self.log.append("report")
        if self.on_report is not None:
            self.on_report(error, info)

    @property
    def owner(self) -> Any:
        return self.session._owner

    def queued(self) -> int:
        with self.owner._cond:
            return len(self.owner._queue)

    def holding(self) -> bool:
        with self.owner._cond:
            return self.owner._holder is not None

    def event(self) -> threading.Event:
        event = threading.Event()
        self.events.append(event)
        return event

    def start(self, name: str, fn: Callable[[], Any]) -> Worker:
        worker = Worker(name, fn)
        self.workers.append(worker)
        worker.thread.start()
        return worker

    def spec(self, kind: str, scope: str = "conversation", **extra: Any) -> Any:
        semantics = (
            {"history": "latest", "fork": "initial"} if scope == "conversation" else {}
        )
        value = extra.pop(
            "value", {"items": [], "nested": {"count": 0}, "other": {"label": "x"}}
        )

        def initial() -> Any:
            self.inits[kind] = self.inits.get(kind, 0) + 1
            return copy.deepcopy(value)

        return self.M.tx.Doc(kind, 1, scope, initial, **semantics)

    def __enter__(self) -> Rig:
        return self

    def __exit__(self, *exc: object) -> None:
        self.store.release_all()
        for event in self.events:
            event.set()
        for worker in self.workers:
            worker.thread.join(WAIT)
        alive = [w.thread.name for w in self.workers if w.thread.is_alive()]
        try:
            self.owner.close(timeout=WAIT)
        except BaseException:  # noqa: BLE001, S110 - close outcome is not under test
            pass
        assert not alive, f"unreaped workers: {alive}"


async def conversation(rig: Rig, mode: Mode) -> int:
    async def cb(tx: Any) -> int:
        return (await tx.create_conversation({"kind": "ownerless"})).id

    return int(await mode.commit(rig, cb))


async def live(rig: Rig, mode: Mode, conv: int) -> Any:
    """Pi ``setupLive``: a live document with items ["a", "b"]."""
    spec = rig.spec("test.live")

    async def seed(tx: Any) -> None:
        (await tx.doc(spec, conversation_id=conv))["items"].extend(["a", "b"])

    await mode.commit(rig, seed)
    return spec


async def note(tx: Any, conv: int, kind: str = "note") -> int:
    return int((await tx.append_entry(conv, {"kind": kind})).id)


def entry_kinds(rig: Rig, conv: int) -> list[str]:
    """Committed entry kinds, read straight from the real Memory backend."""
    page = rig.inner.scan_entries(
        EntryQuery({"conversationId": conv, "order": "ascending"}), 100
    )
    return [str(item.to_tree()["kind"]) for item in page.items]


def kinds(writes: tuple[Any, ...]) -> list[str]:
    return [str(write.to_tree()["type"]) for write in writes]


# -- scenario registry ----------------------------------------------------------------------

Scenario = Callable[[Mods], None]
SCENARIOS: dict[str, Scenario] = {}


def _bound(fn: Callable[[Mods, Mode], None], mode: Mode) -> Scenario:
    return lambda M: fn(M, mode)


def scenario(name: str, modes: tuple[Mode, ...] = (SYNC, ASYNC)) -> Callable[..., Any]:
    def register(fn: Callable[[Mods, Mode], None]) -> Callable[[Mods, Mode], None]:
        for mode in modes:
            key = f"{name}[{mode.name}]"
            assert key not in SCENARIOS
            SCENARIOS[key] = _bound(fn, mode)
        return fn

    return register


# -- publication, snapshots and identity ----------------------------------------------------


@scenario("docs605-137-synchronous-publication-and-identities")
def _docs605(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            spec = rig.spec("test.live")
            seen: list[Any] = []
            rig.session.subscribe_commits(seen.append)

            async def cb(tx: Any) -> str:
                (await tx.doc(spec, conversation_id=conv))["message"] = "m"
                await note(tx, conv)
                assert seen == []  # nothing publishes before adoption
                return "done"

            assert await mode.commit(rig, cb) == "done"
            assert len(seen) == 1  # delivered before the commit returned
            publication = seen[0]
            assert publication.seq == rig.store.returned[-1]
            batch = rig.store.commits[-1]
            # Table changes are the very committed StorageWrite objects.
            tables = [c for c in publication.changes if type(c) is StorageWrite]
            assert [t for t in tables] == [w for w in batch if kinds((w,)) == ["entry"]]
            assert all(any(t is w for w in batch) for t in tables)
            documents = [
                c for c in publication.changes if type(c) is M.tx.RawDocumentChange
            ]
            assert publication.changes == (*tables, *documents)
            snapshot = await mode.snapshot(rig, spec, conv)
            # Document changes carry the adopted value shared with snapshots.
            assert documents[0].value is snapshot.value
            assert (await mode.snapshot(rig, spec, conv)) is snapshot
            assert snapshot.value.thaw()["message"] == "m"

        mode.run(body)


@scenario("docs459-207-old-revision-while-commit-held-escaped-draft-revoked")
def _docs459(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        escaped: list[Any] = []

        async def change(tx: Any) -> None:
            draft = await tx.doc(spec, conversation_id=conv)
            draft["items"].append("c")
            draft["nested"]["count"] = 9
            escaped.append(draft["nested"])

        if mode.sync:
            conv = drive(conversation(rig, mode))
            spec = drive(live(rig, mode, conv))
            before = rig.session.snapshot(spec, conversation_id=conv)
            copy_before = before.value.thaw()
            gate = rig.store.hold("commit")
            worker = rig.start("held", lambda: ready(mode.commit(rig, change)))
            assert gate.entered.wait(WAIT)
            assert rig.session.snapshot(spec, conversation_id=conv) is before
            with pytest.raises(M.tx.DraftRevoked):
                escaped[0]["count"]
            gate.release.set()
            worker.value()
        else:

            async def body() -> tuple[Any, Any]:
                nonlocal conv, spec
                conv = await conversation(rig, mode)
                spec = await live(rig, mode, conv)
                first = await rig.session.asnapshot(spec, conversation_id=conv)
                gate = rig.store.hold("commit")
                task = asyncio.create_task(rig.session.acommit(change))
                await entered(gate)
                assert await rig.session.asnapshot(spec, conversation_id=conv) is first
                loop = asyncio.get_running_loop()
                with pytest.raises(M.tx.DraftRevoked):
                    await loop.run_in_executor(None, lambda: escaped[0]["count"])
                with pytest.raises(M.tx.DraftRevoked):
                    escaped[0]["count"]
                gate.release.set()
                await task
                return first, first.value.thaw()

            conv, spec = 0, None
            before, copy_before = run_async(body)
        after = rig.session.snapshot(spec, conversation_id=conv)
        assert after is not before
        assert after.value.thaw()["items"] == ["a", "b", "c"]
        assert before.value.thaw() == copy_before


@scenario("docs591-cold-cache-reloads-from-storage")
def _docs591(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            spec = await live(rig, mode, conv)
            loaded = await mode.snapshot(rig, spec, conv)
            await mode.unload(rig)
            reloaded = await mode.snapshot(rig, spec, conv)
            assert reloaded is not loaded
            assert reloaded.value == loaded.value

            async def push(tx: Any) -> None:
                (await tx.doc(spec, conversation_id=conv))["items"].append("c")

            await mode.commit(rig, push)
            await mode.unload(rig)
            again = await mode.snapshot(rig, spec, conv)
            assert again.value.thaw()["items"] == ["a", "b", "c"]
            absent = rig.spec("test.absent", "session")
            assert await mode.snapshot(rig, absent) is None

        mode.run(body)


@scenario("docs352-expired-tx-rejects-every-use")
def _docs352(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            spec = await live(rig, mode, conv)
            captured: list[Any] = []

            async def keep(tx: Any) -> None:
                captured.append(tx)

            await mode.commit(rig, keep)
            tx = captured[0]
            with pytest.raises(M.tx.TransactionSettled):
                await tx.doc(spec, conversation_id=conv)
            with pytest.raises(M.tx.TransactionSettled):
                await tx.conversation(conv)
            with pytest.raises(M.tx.TransactionSettled):
                tx.set_task({})

        mode.run(body)


@scenario("docs207-returned-draft-revoked")
def _docs207_returned(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            spec = await live(rig, mode, conv)
            if mode.sync:
                returned = rig.session.commit(
                    lambda tx: tx.doc(spec, conversation_id=conv)
                )
            else:
                # The callback returns the op handle; acommit flattens it.
                returned = await rig.session.acommit(
                    lambda tx: tx.doc(spec, conversation_id=conv)
                )
            assert type(returned) is M.tx.Draft
            with pytest.raises(M.tx.DraftRevoked):
                returned["items"]

        mode.run(body)


@scenario("docs174-228-479-publication-shapes")
def _publication_shapes(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            spec = await live(rig, mode, conv)
            old = rig.publications[-1].changes[-1].record.id
            published, commits = len(rig.publications), len(rig.store.commits)

            async def no_op(tx: Any) -> None:
                draft = await tx.doc(spec, conversation_id=conv)
                draft["items"].append("z")
                draft["items"].pop()

            await mode.commit(rig, no_op)  # docs 174: no write, no publication

            async def failing(tx: Any) -> None:
                (await tx.doc(spec, conversation_id=conv))["message"] = "lost"
                raise RuntimeError("callback failed")

            with pytest.raises(RuntimeError, match="callback failed"):
                await mode.commit(rig, failing)  # docs 228: nothing published
            assert len(rig.publications) == published
            assert len(rig.store.commits) == commits

            async def replace(tx: Any) -> None:
                (await tx.doc(spec, conversation_id=conv))["message"] = "final"
                await tx.retire_doc(spec, conversation_id=conv)
                (await tx.doc(spec, conversation_id=conv))["message"] = "new"

            await mode.commit(rig, replace)  # docs 479
            publication = rig.publications[-1]
            retired, created = publication.changes
            assert retired.record.id == old and retired.value is None
            assert retired.version is None and retired.conversation_id == conv
            assert retired.record.to_tree()["retiredAt"] == publication.seq
            assert created.record.to_tree()["createdAt"] == publication.seq
            assert created.value.thaw()["message"] == "new"
            assert (await mode.snapshot(rig, spec, conv)).value is created.value

        mode.run(body)


@scenario("txop-await-is-shielded", (ASYNC,))
def _txop_shield(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            gate = rig.store.hold("conversation")

            async def cb(tx: Any) -> int:
                op = tx.conversation(conv)

                async def wait() -> Any:
                    return await op

                waiter = asyncio.create_task(wait())
                await entered(gate)
                await spin()
                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter
                assert not op.done()  # a cancelled awaiter never cancels the op
                gate.release.set()
                return int((await op).id)

            assert await rig.session.acommit(cb) == conv

        mode.run(body)


@scenario("process-control-in-callback-rolls-back", (SYNC,))
def _process_control_callback(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        conv = drive(conversation(rig, mode))

        def cb(tx: Any) -> None:
            tx.append_entry(conv, {"kind": "lost"})
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            rig.session.commit(cb)
        assert entry_kinds(rig, conv) == []
        assert rig.owner.failure is None and not rig.holding()
        rig.session.commit(lambda tx: tx.append_entry(conv, {"kind": "kept"}))
        assert entry_kinds(rig, conv) == ["kept"]


@scenario("tables82-unawaited-write-sets-phase-at-once", (ASYNC,))
def _tables82_async(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)

            async def cb(tx: Any) -> None:
                handle = tx.append_entry(conv, {"kind": "note"})  # not yet awaited
                with pytest.raises(M.tx.ReadAfterWrite):
                    tx.conversation(conv)
                await handle

            await rig.session.acommit(cb)
            assert entry_kinds(rig, conv) == ["note"]

        mode.run(body)


# -- pending operations and the async driver (async only) --------------------------------


@scenario("docs284-pending-acquisition-line-held-until-drained", (ASYNC,))
def _docs284(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            spec = await live(rig, mode, conv)
            await mode.unload(rig)
            gate = rig.store.hold("find_document")
            commits = len(rig.store.commits)
            pending: list[Any] = []
            entries: list[str] = []

            async def first_cb(tx: Any) -> None:
                pending.append(tx.doc(spec, conversation_id=conv))

            async def second_cb(tx: Any) -> None:
                entries.append("second")
                (await tx.doc(spec, conversation_id=conv))["message"] = "after"

            first = asyncio.create_task(rig.session.acommit(first_cb))
            await entered(gate)
            second = asyncio.create_task(rig.session.acommit(second_cb))
            await spin()
            # The line stays held until the late acquisition settles.
            assert not first.done() and entries == []
            assert rig.holding() and rig.queued() == 1
            gate.release.set()
            with pytest.raises(M.tx.PendingTxOperations, match="pending Tx operations"):
                await first
            with pytest.raises(M.tx.TransactionSettled):
                await pending[0]
            await second
            assert entries == ["second"]
            assert len(rig.store.commits) == commits + 1
            snap = await rig.session.asnapshot(spec, conversation_id=conv)
            assert snap.value.thaw()["message"] == "after"

        mode.run(body)


@scenario("docs311-no-late-initializer-or-mint", (ASYNC,))
def _docs311(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            late = rig.spec("test.late", "session", value={"count": 0})
            gate = rig.store.hold("find_document")
            mints = rig.store.count("mint_id")
            pending: list[Any] = []

            async def cb(tx: Any) -> None:
                pending.append(tx.doc(late))

            task = asyncio.create_task(rig.session.acommit(cb))
            await entered(gate)
            gate.release.set()
            with pytest.raises(M.tx.PendingTxOperations):
                await task
            with pytest.raises(M.tx.TransactionSettled):
                await pending[0]
            assert rig.inits.get("test.late", 0) == 0
            assert rig.store.count("mint_id") == mints

        mode.run(body)


@scenario("docs337-callback-error-wins-over-pending", (ASYNC,))
def _docs337(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            spec = await live(rig, mode, conv)
            await mode.unload(rig)
            gate = rig.store.hold("find_document")
            pending: list[Any] = []

            async def cb(tx: Any) -> None:
                pending.append(tx.doc(spec, conversation_id=conv))
                raise RuntimeError("callback failed")

            task = asyncio.create_task(rig.session.acommit(cb))
            await entered(gate)
            await spin()
            assert not task.done() and rig.holding()  # drained before rejecting
            gate.release.set()
            with pytest.raises(RuntimeError, match="callback failed"):
                await task
            with pytest.raises(M.tx.TransactionSettled):
                await pending[0]

        mode.run(body)


@scenario("docs250-memoized-concurrent-acquisition", (ASYNC,))
def _docs250(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            spec = rig.spec("test.live")
            mints = rig.store.count("mint_id")

            async def cb(tx: Any) -> None:
                one = tx.doc(spec, conversation_id=conv)
                two = tx.doc(spec, conversation_id=conv)
                assert one is two  # the same handle (Pi X544)
                first, second = await asyncio.gather(one, two)
                assert first is second
                assert await tx.doc(spec, conversation_id=conv) is first
                first["message"] = "once"

            await rig.session.acommit(cb)
            assert rig.inits["test.live"] == 1
            assert rig.store.count("mint_id") == mints + 1
            assert kinds(rig.store.commits[-1]).count("document.create") == 1

        mode.run(body)


@scenario("docs549-retire-races-pending-acquisition", (ASYNC,))
def _docs549(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            spec = await live(rig, mode, conv)
            old = rig.publications[-1].changes[-1].record.id

            async def first(tx: Any) -> None:
                acquired = tx.doc(spec, conversation_id=conv)
                retired = tx.retire_doc(spec, conversation_id=conv)
                (await acquired)["message"] = "final"
                await retired

            await rig.session.acommit(first)
            writes = [w.to_tree() for w in rig.store.commits[-1]]
            assert sorted(str(w["type"]) for w in writes) == [
                "document.change",
                "document.retire",
            ]
            assert {"type": "document.retire", "id": old} in writes
            assert await rig.session.asnapshot(spec, conversation_id=conv) is None

            async def second(tx: Any) -> None:
                (await tx.doc(spec, conversation_id=conv))["message"] = "second"

            await rig.session.acommit(second)
            second_id = rig.publications[-1].changes[-1].record.id

            async def third(tx: Any) -> None:
                acquired = tx.doc(spec, conversation_id=conv)
                retired = tx.retire_doc(spec, conversation_id=conv)
                recreated = tx.doc(spec, conversation_id=conv)
                old_draft, fresh, _ = await asyncio.gather(acquired, recreated, retired)
                assert fresh is not old_draft
                fresh["message"] = "third"

            await rig.session.acommit(third)
            writes = [w.to_tree() for w in rig.store.commits[-1]]
            assert sorted(str(w["type"]) for w in writes) == [
                "document.create",
                "document.retire",
            ]
            assert {"type": "document.retire", "id": second_id} in writes
            snap = await rig.session.asnapshot(spec, conversation_id=conv)
            assert snap.value.thaw()["message"] == "third"

        mode.run(body)


@scenario("async-driver-awaits-in-flight-memoized-call", (ASYNC,))
def _inflight(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            # Two workers: one holds the read; the other runs every later job
            # in FIFO order, so a finished sentinel proves earlier jobs ran.
            loop = asyncio.get_running_loop()
            loop.set_default_executor(
                ThreadPoolExecutor(max_workers=2, thread_name_prefix="asyncio")
            )
            conv = await conversation(rig, mode)

            async def make(tx: Any) -> int:
                return int(
                    await tx.create_task(
                        task_definition("test.work", 1, {"phase": "start"}),
                        None,
                        ownership={"kind": "conversation"},
                        conversation_id=conv,
                    )
                )

            owner = await rig.session.acommit(make)
            gate = rig.store.hold("task")
            reads = rig.store.count("task")

            async def cb(tx: Any) -> tuple[Any, Any]:
                read = tx.task(owner)  # committed read, held in the executor
                await entered(gate)
                # Owner resolution awaits the same memoized read at its first step.
                child = tx.create_task(
                    task_definition("test.child", 1, {"phase": "start"}),
                    None,
                    ownership={"kind": "task", "taskId": owner},
                )
                await loop.run_in_executor(None, lambda: None)  # FIFO sentinel
                assert not read.done()  # still in flight when the child awaited it
                gate.release.set()
                return await read, await child

            record, child = await rig.session.acommit(cb)
            assert record.id == owner and child > owner
            assert rig.store.count("task") == reads + 1

        mode.run(body)


@scenario("async-tx-and-drafts-are-loop-bound", (ASYNC,))
def _async_affinity(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            spec = rig.spec("test.live")
            loop = asyncio.get_running_loop()

            async def cb(tx: Any) -> None:
                draft = await tx.doc(spec, conversation_id=conv)
                with pytest.raises(M.tx.TxAffinityError):
                    await loop.run_in_executor(None, tx.conversation, conv)
                with pytest.raises(M.tx.TxAffinityError):
                    await loop.run_in_executor(None, lambda: draft["items"])
                draft["message"] = "loop"

            await rig.session.acommit(cb)
            snap = await rig.session.asnapshot(spec, conversation_id=conv)
            assert snap.value.thaw()["message"] == "loop"

        mode.run(body)


@scenario("sync-tx-is-thread-bound", (SYNC,))
def _sync_affinity(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        conv = drive(conversation(rig, mode))

        def cb(tx: Any) -> None:
            other = Worker("foreign", lambda: tx.conversation(conv))
            other.thread.start()
            with pytest.raises(M.tx.TxAffinityError):
                other.value()
            tx.append_entry(conv, {"kind": "note"})

        rig.session.commit(cb)
        assert entry_kinds(rig, conv) == ["note"]


# -- the cancellation matrix -------------------------------------------------------------


@scenario("cancel-view-queued-never-enters-fifo-kept")
def _cancel_view_queued(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        order: list[str] = []
        latch = M.session.CancelLatch()
        if mode.sync:
            conv = drive(conversation(rig, mode))
            go = rig.event()

            def holder(tx: Any) -> None:
                order.append("A")
                assert go.wait(WAIT)
                tx.append_entry(conv, {"kind": "a"})

            def queued(name: str) -> Callable[[Any], None]:
                def cb(tx: Any) -> None:
                    order.append(name)
                    tx.append_entry(conv, {"kind": name.lower()})

                return cb

            a = rig.start("A", lambda: rig.session.commit(holder))
            until(lambda: order == ["A"], "holder entered")
            b = rig.start(
                "B", lambda: rig.session.commit(queued("B"), cancel=latch.view())
            )
            until(lambda: rig.queued() == 1, "B queued")
            c = rig.start("C", lambda: rig.session.commit(queued("C")))
            until(lambda: rig.queued() == 2, "C queued")
            latch.cancel()
            go.set()
            a.value()
            with pytest.raises(M.core.CommitCancelled):
                b.value()
            c.value()
        else:

            async def body() -> int:
                conv = await conversation(rig, mode)
                go = asyncio.Event()

                async def holder(tx: Any) -> None:
                    order.append("A")
                    await go.wait()
                    await note(tx, conv, "a")

                def queued(name: str) -> Callback:
                    async def cb(tx: Any) -> None:
                        order.append(name)
                        await note(tx, conv, name.lower())

                    return cb

                a = asyncio.create_task(rig.session.acommit(holder))
                await auntil(lambda: order == ["A"], "holder entered")
                b = asyncio.create_task(
                    rig.session.acommit(queued("B"), cancel=latch.view())
                )
                await auntil(lambda: rig.queued() == 1, "B queued")
                c = asyncio.create_task(rig.session.acommit(queued("C")))
                await auntil(lambda: rig.queued() == 2, "C queued")
                latch.cancel()
                go.set()
                await a
                with pytest.raises(M.core.CommitCancelled):
                    await b
                await c
                return conv

            conv = run_async(body)
        assert order == ["A", "C"]
        assert entry_kinds(rig, conv) == ["a", "c"]


@scenario("task-cancel-queued-detaches-at-once", (ASYNC,))
def _task_cancel_queued(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            go = asyncio.Event()
            order: list[str] = []

            async def holder(tx: Any) -> None:
                order.append("A")
                await go.wait()
                await note(tx, conv, "a")

            async def never(tx: Any) -> None:
                order.append("B")

            a = asyncio.create_task(rig.session.acommit(holder))
            await auntil(lambda: order == ["A"], "holder entered")
            b = asyncio.create_task(rig.session.acommit(never))
            await auntil(lambda: rig.queued() == 1, "B queued")
            b.cancel()
            with pytest.raises(asyncio.CancelledError):
                await b
            assert rig.queued() == 0 and not a.done()  # detached while A holds
            go.set()
            await a

            async def after(tx: Any) -> None:
                await note(tx, conv, "c")

            await rig.session.acommit(after)
            assert order == ["A"]
            assert entry_kinds(rig, conv) == ["a", "c"]

        mode.run(body)


@scenario("task-cancel-granted-unused-ticket-passed-on", (ASYNC,))
def _task_cancel_granted(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        conv = drive(conversation(rig, SYNC))
        go = rig.event()
        order: list[str] = []

        def holder(tx: Any) -> None:
            order.append("A")
            assert go.wait(WAIT)
            tx.append_entry(conv, {"kind": "a"})

        a = rig.start("A", lambda: rig.session.commit(holder))
        until(lambda: order == ["A"], "holder entered")

        async def body() -> None:
            async def queued(name: str, tx: Any) -> None:
                order.append(name)
                await note(tx, conv, name.lower())

            b = asyncio.create_task(rig.session.acommit(partial(queued, "B")))
            await auntil(lambda: rig.queued() == 1, "B queued")
            c = asyncio.create_task(rig.session.acommit(partial(queued, "C")))
            await auntil(lambda: rig.queued() == 2, "C queued")
            # Block the loop while A leaves on its thread: B is granted and its
            # wake-up is scheduled, then B is cancelled before it resumes.
            go.set()
            a.value()
            assert rig.holding() and rig.queued() == 1
            b.cancel()
            with pytest.raises(asyncio.CancelledError):
                await b
            await c

        run_async(body)
        assert order == ["A", "C"]
        assert entry_kinds(rig, conv) == ["a", "c"]


@scenario("cancel-view-after-grant-is-ignored")
def _cancel_view_running(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        latch = M.session.CancelLatch()

        async def cb(tx: Any) -> str:
            latch.cancel()  # Pi has no second abort check
            await note(tx, conv)
            return "kept"

        if mode.sync:
            conv = drive(conversation(rig, mode))
            assert rig.session.commit(
                lambda tx: drive(cb(Adapter(tx))), cancel=latch.view()
            ) == ("kept")
            held = M.session.CancelLatch()
            gate = rig.store.hold("commit")
            worker = rig.start(
                "held",
                lambda: rig.session.commit(
                    lambda tx: tx.append_entry(conv, {"kind": "held"}),
                    cancel=held.view(),
                ),
            )
            assert gate.entered.wait(WAIT)
            held.cancel()  # the backend commit uses no cancel view
            gate.release.set()
            worker.value()
        else:

            async def body() -> int:
                nonlocal conv
                conv = await conversation(rig, mode)
                assert await rig.session.acommit(cb, cancel=latch.view()) == "kept"
                held = M.session.CancelLatch()
                gate = rig.store.hold("commit")
                task = asyncio.create_task(
                    rig.session.acommit(
                        partial(note, conv=conv, kind="held"), cancel=held.view()
                    )
                )
                await entered(gate)
                held.cancel()
                gate.release.set()
                await task
                return conv

            conv = 0
            run_async(body)
        assert entry_kinds(rig, conv) == ["note", "held"]
        assert rig.owner.failure is None


@scenario("task-cancel-in-callback-propagated-rolls-back", (ASYNC,))
def _task_cancel_callback(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            started, never = asyncio.Event(), asyncio.Event()
            commits = len(rig.store.commits)

            async def cb(tx: Any) -> None:
                await note(tx, conv)
                started.set()
                await never.wait()

            task = asyncio.create_task(rig.session.acommit(cb))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(rig.store.commits) == commits
            assert not rig.holding() and rig.owner.failure is None
            await rig.session.acommit(partial(note, conv=conv, kind="next"))
            assert entry_kinds(rig, conv) == ["next"]

        mode.run(body)


@scenario("task-cancel-in-callback-swallowed-commits", (ASYNC,))
def _task_cancel_swallowed(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            started, never = asyncio.Event(), asyncio.Event()

            async def cb(tx: Any) -> str:
                await note(tx, conv)
                started.set()
                try:
                    await never.wait()
                except asyncio.CancelledError:
                    pass  # the callback's own decision
                return "kept"

            task = asyncio.create_task(rig.session.acommit(cb))
            await started.wait()
            task.cancel()
            assert await task == "kept"
            assert entry_kinds(rig, conv) == ["note"]

        mode.run(body)


@scenario("task-cancel-during-held-commit-stays-durable", (ASYNC,))
def _task_cancel_held(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            published = len(rig.publications)
            gate = rig.store.hold("commit")
            task = asyncio.create_task(rig.session.acommit(partial(note, conv=conv)))
            await entered(gate)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task  # the waiter detaches while the commit is held
            assert rig.holding()
            gate.release.set()

            async def read(tx: Any) -> list[str]:
                page = await tx.scan_entries(EntryQuery({"conversationId": conv}), 10)
                return [item.to_tree()["kind"] for item in page.items]

            assert await rig.session.acommit(read) == ["note"]
            assert len(rig.publications) == published + 1
            assert rig.owner.failure is None

        mode.run(body)


# -- failure and acknowledgement cuts --------------------------------------------------------


@scenario("h276-caught-failed-read-then-write-fails")
def _h276_write(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            commits = len(rig.store.commits)
            error = RuntimeError("disk gone")
            rig.store.fault("task", error)

            async def cb(tx: Any) -> None:
                try:
                    await tx.task(1)
                except RuntimeError:
                    pass
                await note(tx, conv)

            with pytest.raises(M.session.SessionFailed) as caught:
                await mode.commit(rig, cb)
            assert caught.value.cause is error
            assert len(rig.store.commits) == commits

        mode.run(body)


@scenario("h276-caught-failed-read-then-return-health-checked")
def _h276_return(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            await conversation(rig, mode)
            error = RuntimeError("disk gone")
            rig.store.fault("task", error)

            async def cb(tx: Any) -> str:
                try:
                    await tx.task(1)
                except RuntimeError:
                    pass
                return "would succeed"

            with pytest.raises(M.session.SessionFailed) as caught:
                await mode.commit(rig, cb)
            assert caught.value.cause is error

        mode.run(body)


@scenario("late-read-discarded-after-off-line-failure")
def _late_read(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        error = RuntimeError("mint gone")

        async def cb(tx: Any) -> None:
            await tx.conversation(conv)
            await note(tx, conv)

        if mode.sync:
            conv = drive(conversation(rig, mode))
            commits = len(rig.store.commits)
            gate = rig.store.hold("conversation")
            worker = rig.start("held", lambda: ready(mode.commit(rig, cb)))
            assert gate.entered.wait(WAIT)
            rig.store.fault("mint_id", error)
            with pytest.raises(RuntimeError):
                rig.owner.storage.mint_id()  # an off-line call fails the owner
            gate.release.set()
            with pytest.raises(M.session.SessionFailed):
                worker.value()
        else:

            async def body() -> int:
                nonlocal conv
                conv = await conversation(rig, mode)
                gate = rig.store.hold("conversation")
                task = asyncio.create_task(rig.session.acommit(cb))
                await entered(gate)
                rig.store.fault("mint_id", error)
                with pytest.raises(RuntimeError):
                    rig.owner.storage.mint_id()
                gate.release.set()
                with pytest.raises(M.session.SessionFailed) as caught:
                    await task
                assert caught.value.cause is error
                return len(rig.store.commits)

            conv = 0
            commits = run_async(body)
        assert len(rig.store.commits) == commits
        assert rig.session.wait_closed(timeout=WAIT).error is error


def _ack_cut(after: bool, request: bool = False) -> Callable[[Mods, Mode], None]:
    def run(M: Mods, mode: Mode) -> None:
        with Rig(M) as rig:

            async def body() -> None:
                conv = await conversation(rig, mode)
                spec = await live(rig, mode, conv)
                cached = rig.session._cache.peek(spec, conv)
                published = len(rig.publications)
                calls = rig.store.count("commit")
                error = (
                    StorageRequestError("bad batch")
                    if request
                    else RuntimeError("acknowledgement lost")
                )
                rig.store.probe = lambda inner: [
                    i.to_tree()["kind"]
                    for i in inner.scan_entries(
                        EntryQuery({"conversationId": conv}), 10
                    ).items
                ]
                (rig.store.fault_after if after else rig.store.fault)("commit", error)

                async def cb(tx: Any) -> None:
                    (await tx.doc(spec, conversation_id=conv))["message"] = "uncertain"
                    await note(tx, conv)

                with pytest.raises(type(error)) as caught:
                    await mode.commit(rig, cb)
                assert caught.value is error  # the original error, not SessionFailed
                assert rig.owner.failure is error
                assert rig.store.count("commit") == calls + 1  # never retried
                assert rig.session._cache.peek(spec, conv) in (cached, None)
                assert len(rig.publications) == published
                # Commit-then-throw: the captured backend state has the write.
                assert rig.store.probes == ([["note"]] if after else [])
                with pytest.raises(M.session.SessionFailed):
                    await mode.snapshot(rig, spec, conv)
                with pytest.raises(M.session.SessionFailed):
                    await mode.commit(rig, partial(note, conv=conv))
                end = await mode.closed(rig)
                assert end.reason == "failed" and end.error is error
                assert rig.reports == [(error, "session_failed")]

            mode.run(body)

    return run


scenario("h267-h291-docs440-cut-before-delegating")(_ack_cut(False))
scenario("h267-h291-docs440-cut-after-real-commit")(_ack_cut(True))
scenario("h291-request-error-from-commit-is-never-exempt")(_ack_cut(False, True))


@scenario("adopt-failure-rejects-durable-commit-and-fails")
def _adopt_failure(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        bug = RuntimeError("adopt bug")
        original = M.tx._TxCore.adopt

        def failing(self: Any, seq: int) -> Any:
            raise bug

        async def body() -> None:
            conv = await conversation(rig, mode)
            published = len(rig.publications)
            commits = len(rig.store.commits)
            M.tx._TxCore.adopt = failing
            try:
                with pytest.raises(RuntimeError) as caught:
                    await mode.commit(rig, partial(note, conv=conv))
            finally:
                M.tx._TxCore.adopt = original
            assert caught.value is bug
            assert len(rig.store.commits) == commits + 1  # durable
            assert len(rig.publications) == published
            end = await mode.closed(rig)
            assert end.reason == "failed" and end.error is bug
            assert rig.reports == [(bug, "session_failed")]

        mode.run(body)


@scenario("commit-returned-and-adopted-before-publication")
def _before_publication(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        observed: list[tuple[bool, bool]] = []

        async def body() -> None:
            conv = await conversation(rig, mode)
            spec = rig.spec("test.live")

            def internal(publication: Any) -> None:
                document = publication.changes[-1]
                cached = rig.session._cache.peek(spec, conv)
                observed.append(
                    (
                        rig.store.returned[-1] == publication.seq,
                        cached is not None and cached.value is document.value,
                    )
                )

            rig.session.observe_commits(internal)

            async def cb(tx: Any) -> None:
                (await tx.doc(spec, conversation_id=conv))["message"] = "x"

            await mode.commit(rig, cb)
            assert observed == [(True, True)]

        mode.run(body)


# -- listeners --------------------------------------------------------------------------------


@scenario("h328-internal-listener-poisons-durable-commit-resolves")
def _h328(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            bug = RuntimeError("listener bug")
            seen: list[str] = []

            def internal(publication: Any) -> None:
                raise bug

            rig.session.observe_commits(internal)
            rig.session.subscribe_commits(lambda publication: seen.append("host"))
            assert await mode.commit(rig, partial(note, conv=conv)) > 0
            assert seen == ["host"]  # the rest of the publication is delivered
            end = await mode.closed(rig)
            assert end.reason == "failed" and end.error is bug
            assert rig.reports == [(bug, "session_failed")]

        mode.run(body)


@scenario("h466-host-commit-and-close-listeners-contained")
def _h466(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            seen: list[str] = []
            commit_error = RuntimeError("commit listener")
            close_error = RuntimeError("close listener")

            def bad_commit(publication: Any) -> None:
                raise commit_error

            def bad_close() -> None:
                raise close_error

            rig.session.subscribe_commits(bad_commit)
            rig.session.subscribe_commits(lambda publication: seen.append("commit"))
            rig.session.subscribe_close(bad_close)
            rig.session.subscribe_close(lambda: seen.append("close"))
            await mode.commit(rig, partial(note, conv=conv))
            await mode.close(rig)
            assert seen == ["commit", "close"]
            assert rig.reports == [
                (commit_error, "listener_error"),
                (close_error, "listener_error"),
            ]
            assert rig.session.end.reason == "closed"

        mode.run(body)


@scenario("h431-awaitable-listeners-reported-never-awaited", (ASYNC,))
def _h431(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)

            async def failing() -> None:
                raise RuntimeError("async listener")

            rig.session.subscribe_commits(
                lambda publication: asyncio.ensure_future(failing())
            )
            rig.session.subscribe_commits(lambda publication: failing())
            rig.session.subscribe_close(lambda: asyncio.ensure_future(failing()))
            await rig.session.acommit(partial(note, conv=conv))
            await rig.session.aclose()
            await auntil(lambda: len(rig.reports) == 3, "three reports")
            messages = sorted(
                (type(e).__name__, str(e), info) for e, info in rig.reports
            )
            assert messages == [
                ("RuntimeError", "async listener", "listener_error"),
                ("RuntimeError", "async listener", "listener_error"),
                ("TypeError", "Session listeners are synchronous", "listener_error"),
            ]

        mode.run(body)


@scenario("internal-coroutine-listener-closed-and-poisons")
def _internal_coroutine(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)

            async def internal(publication: Any) -> None:
                raise AssertionError("never runs")

            rig.session.observe_commits(internal)
            assert await mode.commit(rig, partial(note, conv=conv)) > 0
            end = await mode.closed(rig)
            assert end.reason == "failed" and type(end.error) is TypeError

        mode.run(body)


@scenario("listener-process-control-exception-fails-and-propagates", (SYNC,))
def _listener_base(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        conv = drive(conversation(rig, mode))
        interrupt = KeyboardInterrupt()

        def listener(publication: Any) -> None:
            raise interrupt

        rig.session.subscribe_commits(listener)
        with pytest.raises(KeyboardInterrupt):
            rig.session.commit(lambda tx: tx.append_entry(conv, {"kind": "note"}))
        end = rig.session.wait_closed(timeout=WAIT)
        assert end.reason == "failed" and end.error is interrupt


# -- reentry ------------------------------------------------------------------------------------


@scenario("reentry-from-callback-refused-before-sealing")
def _reentry(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        reentrant = M.core.ReentrantSessionCall

        async def body() -> None:
            conv = await conversation(rig, mode)
            spec = await live(rig, mode, conv)
            absent = rig.spec("test.absent", "session")

            async def cb(tx: Any) -> None:
                if mode.sync:
                    with pytest.raises(reentrant):
                        rig.session.commit(lambda t: None)
                    with pytest.raises(reentrant):
                        rig.session.close()
                    with pytest.raises(reentrant):
                        rig.session.read_on_line(lambda: 1)
                    with pytest.raises(reentrant):
                        rig.session.snapshot(absent)
                    hit = rig.session.snapshot(spec, conversation_id=conv)
                else:
                    with pytest.raises(reentrant):
                        await rig.session.acommit(lambda t: None)
                    with pytest.raises(reentrant):
                        await rig.session.aclose()
                    with pytest.raises(reentrant):
                        rig.session.commit(lambda t: None)
                    with pytest.raises(reentrant):
                        await rig.session.aread_on_line(lambda: 1)
                    with pytest.raises(reentrant):
                        await rig.session.asnapshot(absent)
                    hit = await rig.session.asnapshot(spec, conversation_id=conv)
                assert hit is not None  # a cache hit never queues
                await note(tx, conv)

            await mode.commit(rig, cb)
            assert entry_kinds(rig, conv) == ["note"]
            await mode.commit(rig, partial(note, conv=conv, kind="after"))
            assert rig.owner.failure is None and not rig.owner._line_sealed

        mode.run(body)


@scenario("reentry-from-listener-reported-request-close-allowed")
def _reentry_listener(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        reentrant = M.core.ReentrantSessionCall

        async def body() -> None:
            conv = await conversation(rig, mode)
            closes: list[str] = []
            rig.session.subscribe_close(lambda: closes.append("close"))

            def listener(publication: Any) -> None:
                rig.session.commit(lambda tx: None)

            def closer(publication: Any) -> None:
                rig.session.close()

            rig.session.subscribe_commits(listener)
            rig.session.subscribe_commits(closer)
            rig.session.subscribe_commits(
                lambda publication: rig.session.request_close()
            )
            await mode.commit(rig, partial(note, conv=conv))
            assert [type(e) for e, _ in rig.reports] == [reentrant, reentrant]
            assert closes == ["close"]
            end = await mode.closed(rig)
            assert end.reason == "closed"

        mode.run(body)


@scenario("spawned-task-queues-behind-its-spawner", (ASYNC,))
def _spawned(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            spawned: list[Any] = []

            async def cb(tx: Any) -> None:
                await note(tx, conv, "first")
                spawned.append(
                    asyncio.create_task(
                        rig.session.acommit(partial(note, conv=conv, kind="second"))
                    )
                )

            await rig.session.acommit(cb)
            await spawned[0]
            assert entry_kinds(rig, conv) == ["first", "second"]

        mode.run(body)


@scenario("sync-entries-refused-on-event-loop-thread", (ASYNC,))
def _loop_thread(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            absent = rig.spec("test.absent", "session")
            for call in (
                lambda: rig.session.commit(lambda tx: None),
                lambda: rig.session.read_on_line(lambda: 1),
                lambda: rig.session.snapshot(absent),
                lambda: rig.session.close(),
                lambda: rig.session.wait_closed(),
            ):
                with pytest.raises(RuntimeError, match="would block the event loop"):
                    call()
            await rig.session.acommit(partial(note, conv=conv))
            assert entry_kinds(rig, conv) == ["note"]

        mode.run(body)


@scenario("sync-callback-returning-awaitable-rolls-back", (SYNC,))
def _sync_awaitable(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        conv = drive(conversation(rig, mode))

        async def later() -> None:
            raise AssertionError("never runs")

        def returns_coroutine(tx: Any) -> Any:
            tx.append_entry(conv, {"kind": "lost"})
            return later()

        with pytest.raises(TypeError, match="use acommit"):
            rig.session.commit(returns_coroutine)
        loop = asyncio.new_event_loop()
        try:
            future = loop.create_future()
            with pytest.raises(TypeError, match="use acommit"):
                rig.session.commit(lambda tx: future)
            assert not future.done()  # a caller-owned Future is left untouched
        finally:
            loop.close()
        assert entry_kinds(rig, conv) == []
        assert rig.owner.failure is None


@scenario("read-on-line-queues-behind-holder")
def _read_on_line(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        order: list[str] = []
        if mode.sync:
            conv = drive(conversation(rig, mode))
            go = rig.event()

            def holder(tx: Any) -> None:
                order.append("commit")
                assert go.wait(WAIT)
                tx.append_entry(conv, {"kind": "a"})

            def reading(conv: int) -> list[str]:
                order.append("read")
                return entry_kinds(rig, conv)

            a = rig.start("A", lambda: rig.session.commit(holder))
            until(lambda: order == ["commit"], "holder entered")
            reader = rig.start(
                "reader",
                lambda: rig.session.read_on_line(partial(reading, conv)),
            )
            until(lambda: rig.queued() == 1, "reader queued")
            go.set()
            a.value()
            assert reader.value() == ["a"]
            with pytest.raises(TypeError, match="use aread_on_line"):
                rig.session.read_on_line(lambda: later())
        else:

            async def body() -> None:
                conv = await conversation(rig, mode)
                go = asyncio.Event()

                async def holder(tx: Any) -> None:
                    order.append("commit")
                    await go.wait()
                    await note(tx, conv, "a")

                async def job() -> list[str]:
                    order.append("read")
                    return entry_kinds(rig, conv)

                a = asyncio.create_task(rig.session.acommit(holder))
                await auntil(lambda: order == ["commit"], "holder entered")
                reader = asyncio.create_task(rig.session.aread_on_line(job))
                await auntil(lambda: rig.queued() == 1, "reader queued")
                go.set()
                await a
                assert await reader == ["a"]

            run_async(body)
        assert order == ["commit", "read"]


async def later() -> None:
    raise AssertionError("never runs")


# -- close ----------------------------------------------------------------------------------------


@scenario("docs622-close-listeners-once-and-unsubscribe")
def _docs622(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            calls: list[str] = []
            rig.session.subscribe_close(lambda: calls.append("active"))
            unsubscribe = rig.session.subscribe_close(lambda: calls.append("removed"))
            unsubscribe()
            unsubscribe()
            await mode.close(rig)
            await mode.close(rig)
            assert calls == ["active"]
            for subscribe in (
                rig.session.subscribe_close,
                rig.session.subscribe_commits,
                rig.session.observe_commits,
            ):
                with pytest.raises(M.session.SessionClosedError, match="closed"):
                    subscribe(lambda *args: None)
            assert rig.store.count("close") == 1

        mode.run(body)


@scenario("docs632-close-settles-admitted-and-queued-guard-reads-until-drain")
def _docs632(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        absent = rig.spec("test.member", "session")

        async def admitted(tx: Any) -> None:
            (await tx.doc(spec, conversation_id=conv))["message"] = "admitted"

        async def queued(tx: Any) -> None:
            (await tx.doc(spec, conversation_id=conv))["message"] = "queued"

        def window() -> None:
            # Pi: off-line guard reads stay admitted until the close line job.
            assert rig.owner.storage.conversation(conv) is not None
            assert rig.store.count("close") == 0 and rig.session.end is None
            with pytest.raises(M.session.SessionClosedError, match="closed"):
                rig.session.snapshot(spec, conversation_id=conv)
            if mode.sync:
                with pytest.raises(M.session.SessionClosedError, match="closed"):
                    rig.session.commit(lambda tx: None)

        if mode.sync:
            conv = drive(conversation(rig, mode))
            spec = drive(live(rig, mode, conv))
            gate = rig.store.hold("commit")
            first = rig.start("admitted", lambda: ready(mode.commit(rig, admitted)))
            assert gate.entered.wait(WAIT)
            second = rig.start("queued", lambda: ready(mode.commit(rig, queued)))
            until(lambda: rig.queued() == 1, "commit queued")
            snapshot = rig.start("snapshot", lambda: rig.session.snapshot(absent))
            until(lambda: rig.queued() == 2, "snapshot queued")
            closer = rig.start("closer", lambda: rig.session.close(timeout=WAIT))
            until(lambda: rig.owner._line_sealed, "line sealed")
            window()
            gate.release.set()
            first.value()
            second.value()
            assert snapshot.value() is None
            closer.value()
        else:

            async def body() -> None:
                nonlocal conv, spec
                conv = await conversation(rig, mode)
                spec = await live(rig, mode, conv)
                gate = rig.store.hold("commit")
                first = asyncio.create_task(rig.session.acommit(admitted))
                await entered(gate)
                second = asyncio.create_task(rig.session.acommit(queued))
                await auntil(lambda: rig.queued() == 1, "commit queued")
                snapshot = asyncio.create_task(rig.session.asnapshot(absent))
                await auntil(lambda: rig.queued() == 2, "snapshot queued")
                closer = asyncio.create_task(rig.session.aclose())
                await auntil(lambda: rig.owner._line_sealed, "line sealed")
                window()
                with pytest.raises(M.session.SessionClosedError, match="closed"):
                    await rig.session.acommit(lambda tx: None)
                gate.release.set()
                await first
                await second
                assert await snapshot is None
                await closer

            conv, spec = 0, None
            run_async(body)
        assert rig.store.count("close") == 1
        assert rig.session.end.reason == "closed"
        with pytest.raises(M.session.SessionClosedError):
            rig.owner.storage.conversation(conv)
        with pytest.raises(StorageClosedError):
            rig.inner.conversation(conv)
        assert rig.publications[-1].changes[-1].value.thaw()["message"] == "queued"


@scenario("failure-while-line-held-backend-close-waits-for-release")
def _failure_held(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        error = RuntimeError("mint gone")
        rig.session.subscribe_close(lambda: rig.log.append("close"))

        def fail_off_line() -> None:
            rig.store.fault("mint_id", error)
            with pytest.raises(RuntimeError):
                rig.owner.storage.mint_id()
            # Pi enqueues the close job behind the running job.
            assert rig.log == ["close", "report"]
            # The backend close is not even claimed while the holder holds.
            assert rig.owner._close_state == "requested"
            assert rig.store.count("close") == 0 and rig.session.end is None

        if mode.sync:
            drive(conversation(rig, mode))
            go = rig.event()
            started = rig.event()

            def holder(tx: Any) -> None:
                started.set()
                assert go.wait(WAIT)

            worker = rig.start("holder", lambda: rig.session.commit(holder))
            assert started.wait(WAIT)
            fail_off_line()
            go.set()
            with pytest.raises(M.session.SessionFailed):
                worker.value()
        else:

            async def body() -> None:
                await conversation(rig, mode)
                go, started = asyncio.Event(), asyncio.Event()

                async def holder(tx: Any) -> None:
                    started.set()
                    await go.wait()

                task = asyncio.create_task(rig.session.acommit(holder))
                await started.wait()
                fail_off_line()
                go.set()
                with pytest.raises(M.session.SessionFailed):
                    await task

            run_async(body)
        assert rig.store.count("close") == 1
        end = rig.session.wait_closed(timeout=WAIT)
        assert end.reason == "failed" and end.error is error


@scenario("close-listeners-run-before-the-failure-report")
def _close_before_report(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            rig.session.subscribe_close(lambda: rig.log.append("close"))
            error = RuntimeError("disk gone")
            rig.store.fault("commit", error)
            with pytest.raises(RuntimeError):
                await mode.commit(rig, partial(note, conv=conv))
            assert rig.log == ["close", "report"]
            assert (await mode.closed(rig)).error is error

        mode.run(body)


@scenario("bulk-reject-queued-tickets-on-failure")
def _bulk_reject(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        error = RuntimeError("external failure")
        if mode.sync:
            conv = drive(conversation(rig, mode))
            go = rig.event()
            started = rig.event()

            def holder(tx: Any) -> None:
                started.set()
                assert go.wait(WAIT)
                tx.append_entry(conv, {"kind": "late"})

            a = rig.start("A", lambda: rig.session.commit(holder))
            assert started.wait(WAIT)
            b = rig.start("B", lambda: rig.session.commit(lambda tx: None))
            until(lambda: rig.queued() == 1, "B queued")
            rig.session.fail(error)
            with pytest.raises(M.session.SessionFailed):
                b.value()  # at once, while A still holds the line
            assert a.thread.is_alive()
            go.set()
            with pytest.raises(M.session.SessionFailed):
                a.value()
        else:

            async def body() -> None:
                conv = await conversation(rig, mode)
                go, started = asyncio.Event(), asyncio.Event()

                async def holder(tx: Any) -> None:
                    started.set()
                    await go.wait()
                    await note(tx, conv, "late")

                a = asyncio.create_task(rig.session.acommit(holder))
                await started.wait()
                b = asyncio.create_task(rig.session.acommit(lambda tx: None))
                await auntil(lambda: rig.queued() == 1, "B queued")
                rig.session.fail(error)
                with pytest.raises(M.session.SessionFailed):
                    await asyncio.wait_for(b, WAIT)
                assert not a.done()
                go.set()
                with pytest.raises(M.session.SessionFailed):
                    await a

            run_async(body)
        assert rig.session.wait_closed(timeout=WAIT).error is error


@scenario("aclose-on-holder-loop-does-not-deadlock", (ASYNC,))
def _aclose_same_loop(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            go, started = asyncio.Event(), asyncio.Event()

            async def holder(tx: Any) -> None:
                started.set()
                await go.wait()
                await note(tx, conv)

            task = asyncio.create_task(rig.session.acommit(holder))
            await started.wait()
            closer = asyncio.create_task(rig.session.aclose())
            await auntil(lambda: rig.owner._line_sealed, "line sealed")
            assert not closer.done()
            go.set()
            await task
            await closer
            assert (await rig.session.await_closed()).reason == "closed"

        mode.run(body)


@scenario("cancelled-close-wait-keeps-seal-and-drain")
def _cancelled_close(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        if mode.sync:
            conv = drive(conversation(rig, mode))
            go = rig.event()
            started = rig.event()

            def holder(tx: Any) -> None:
                started.set()
                assert go.wait(WAIT)
                tx.append_entry(conv, {"kind": "admitted"})

            a = rig.start("A", lambda: rig.session.commit(holder))
            assert started.wait(WAIT)
            latch = M.session.CancelLatch()
            closer = rig.start("closer", lambda: rig.session.close(cancel=latch.view()))
            until(lambda: rig.owner._line_sealed, "line sealed")
            latch.cancel()
            with pytest.raises(M.session.WaitCancelled):
                closer.value()
            with pytest.raises(M.session.SessionClosedError):
                rig.session.commit(lambda tx: None)
            assert rig.store.count("close") == 0
            go.set()
            a.value()
        else:

            async def body() -> int:
                conv = await conversation(rig, mode)
                go, started = asyncio.Event(), asyncio.Event()

                async def holder(tx: Any) -> None:
                    started.set()
                    await go.wait()
                    await note(tx, conv, "admitted")

                a = asyncio.create_task(rig.session.acommit(holder))
                await started.wait()
                closer = asyncio.create_task(rig.session.aclose())
                await auntil(lambda: rig.owner._line_sealed, "line sealed")
                closer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await closer
                with pytest.raises(M.session.SessionClosedError):
                    await rig.session.acommit(lambda tx: None)
                assert rig.store.count("close") == 0
                go.set()
                await a
                assert (await rig.session.await_closed()).reason == "closed"
                return conv

            conv = run_async(body)
        assert rig.session.wait_closed(timeout=WAIT).reason == "closed"
        assert rig.store.count("close") == 1
        assert rig.store.returned  # the admitted commit settled before close


# -- round-1 review regressions --------------------------------------------------------------------


@scenario("hand-off-never-loses-a-wake-up")
def _hand_off(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        owner = rig.owner
        conv = drive(conversation(rig, SYNC))
        go, started = rig.event(), rig.event()
        holder_thread: list[threading.Thread] = []
        # The window: A has left the line, nobody holds it, B is still queued.
        window = Window(
            lambda: (
                bool(holder_thread)
                and threading.current_thread() is holder_thread[0]
                and owner._holder is None
                and bool(owner._queue)
            )
        )
        owner._cond = window
        rig.events.append(window.resume)

        def holder(tx: Any) -> None:
            started.set()
            assert go.wait(WAIT)
            tx.append_entry(conv, {"kind": "a"})

        def entrant(kind: str) -> Callable[[], Any]:
            return lambda: rig.session.commit(
                lambda tx: tx.append_entry(conv, {"kind": kind})
            )

        a = rig.start("A", lambda: rig.session.commit(holder))
        holder_thread.append(a.thread)
        assert started.wait(WAIT)

        def between() -> Worker | None:
            """Release A; if it pauses in the window, let C enter there."""
            go.set()
            until(lambda: window.reached.is_set() or not a.thread.is_alive(), "A left")
            if not window.reached.is_set():
                return None
            c = rig.start("C", entrant("c"))
            until(lambda: owner._holder is not None or rig.queued() == 2, "C entered")
            window.resume.set()
            return c

        def unstick() -> None:
            with window:  # a lost sync wake-up must not leak a thread
                window.notify_all()

        if mode.sync:
            b = rig.start("B", entrant("b"))
            until(lambda: rig.queued() == 1, "B queued")
            c = between()
            try:
                a.value()
                b.value()
            finally:
                unstick()
            if c is None:
                rig.session.commit(lambda tx: tx.append_entry(conv, {"kind": "c"}))
            else:
                c.value()
        else:

            async def body() -> Worker | None:
                b = asyncio.create_task(
                    rig.session.acommit(partial(note, conv=conv, kind="b"))
                )
                await auntil(lambda: rig.queued() == 1, "B queued")
                loop = asyncio.get_running_loop()
                c = await loop.run_in_executor(None, between)
                a.value()
                await asyncio.wait_for(b, WAIT)
                return c

            c = run_async(body)
            if c is None:
                rig.session.commit(lambda tx: tx.append_entry(conv, {"kind": "c"}))
            else:
                c.value()
        assert sorted(entry_kinds(rig, conv)) == ["a", "b", "c"]
        assert entry_kinds(rig, conv)[:2] == ["a", "b"]  # FIFO: B before C


@scenario("second-closer-never-drains-under-a-running-close-hook", (SYNC,))
def _two_closers(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        owner = rig.owner
        calls: list[str] = []
        rig.session.subscribe_close(lambda: calls.append("close"))
        first: list[threading.Thread] = []
        # The window: close has begun, its hook has not run yet.
        window = Window(
            lambda: (
                bool(first)
                and threading.current_thread() is first[0]
                and owner._close_begun
                and not owner._close_hook_done
            )
        )
        owner._cond = window
        rig.events.append(window.resume)
        closer = Worker("closer-1", lambda: rig.session.close(timeout=WAIT))
        rig.workers.append(closer)
        first.append(closer.thread)
        closer.thread.start()
        assert window.reached.wait(WAIT)
        second = rig.start("closer-2", rig.session.request_close)
        second.value()
        window.resume.set()
        closer.value()
        assert calls == ["close"]
        assert rig.session.end.reason == "closed" and rig.store.count("close") == 1


@scenario("blocking-backend-close-runs-off-the-event-loop", (ASYNC,))
def _close_off_loop_blocking(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            await conversation(rig, mode)
            gate = rig.store.hold("close")
            closer = asyncio.create_task(rig.session.aclose())
            await entered(gate)  # the loop still runs while close is held
            ticks: list[int] = []

            async def tick() -> None:
                ticks.append(1)

            await asyncio.create_task(tick())
            closer.cancel()  # the waiter can give up while close is held
            with pytest.raises(asyncio.CancelledError):
                await closer
            assert ticks == [1] and rig.session.end is None
            gate.release.set()
            end = await asyncio.wait_for(rig.session.await_closed(), WAIT)
            assert end.reason == "closed"

        mode.run(body)
        assert rig.store.count("close") == 1


@scenario("backend-close-awaiting-a-loop-task-completes", (ASYNC,))
def _close_off_loop_waits(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:

        async def body() -> None:
            conv = await conversation(rig, mode)
            loop = asyncio.get_running_loop()
            flushed: list[str] = []

            async def flush() -> None:
                flushed.append("flushed")

            # The backend close waits for a task on this very loop.
            rig.store.before["close"] = lambda: asyncio.run_coroutine_threadsafe(
                flush(), loop
            ).result(WAIT)
            # A failing commit leaves the line on the loop and claims close.
            rig.store.fault("commit", RuntimeError("disk gone"))
            with pytest.raises(RuntimeError, match="disk gone"):
                await rig.session.acommit(partial(note, conv=conv))
            end = await asyncio.wait_for(rig.session.await_closed(), WAIT)
            assert end.reason == "failed" and flushed == ["flushed"]
            assert rig.owner._close_error is None

        mode.run(body)


@scenario("wait-closed-from-the-holder-is-reentrant")
def _wait_closed_reentry(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        reentrant = M.core.ReentrantSessionCall

        async def body() -> None:
            conv = await conversation(rig, mode)

            async def cb(tx: Any) -> None:
                rig.session.request_close()
                if mode.sync:
                    with pytest.raises(reentrant):
                        rig.session.wait_closed(timeout=WAIT)
                else:
                    with pytest.raises(reentrant):
                        # Preserve the holder Task on every supported Python.
                        async with asyncio.timeout(WAIT):
                            await rig.session.await_closed()
                await note(tx, conv)

            await mode.commit(rig, cb)  # admitted before the seal: it settles
            assert (await mode.closed(rig)).reason == "closed"

        mode.run(body)
        assert rig.store.returned  # the admitted commit is durable


class Finalized:
    """A listener whose finalizer touches the owner lock (bounded acquire)."""

    def __init__(self, owner: Any, seen: list[bool]) -> None:
        self.owner = owner
        self.seen = seen

    def __call__(self, *args: Any) -> None:
        pass

    def __del__(self) -> None:
        got = self.owner._cond.acquire(timeout=min(1.0, WAIT))
        if got:
            self.owner._cond.release()
        self.seen.append(got)


@scenario("listener-finalizers-never-run-under-the-owner-lock")
def _finalizers(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        seen: list[bool] = []

        async def body() -> None:
            conv = await conversation(rig, mode)
            # The registries hold the only references (disposers dropped).
            rig.session.subscribe_commits(Finalized(rig.owner, seen))
            rig.session.observe_commits(Finalized(rig.owner, seen))
            rig.session.subscribe_close(Finalized(rig.owner, seen))
            await mode.commit(rig, partial(note, conv=conv))
            await mode.close(rig)

        mode.run(body)
        gc.collect()
        assert seen == [True, True, True]


# -- round-2 review regressions --------------------------------------------------------------------


@scenario("close-listener-calling-close-completes", (SYNC,))
def _nested_close_exact(M: Mods, mode: Mode) -> None:
    # The reviewer's case: subscribe_close(close); close() must not hang.
    with Rig(M) as rig:
        rig.session.subscribe_close(rig.session.close)
        rig.session.close(timeout=WAIT)
        assert rig.session.end.reason == "closed" and rig.reports == []


@scenario("close-listeners-reentering-the-session")
def _nested_close(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        reentrant = M.core.ReentrantSessionCall
        session = rig.session
        tasks: list[Any] = []
        conv = drive(conversation(rig, SYNC))
        # Pi: a nested close() returns the same pending closing promise.
        session.subscribe_close(lambda: session.close(timeout=WAIT))
        session.subscribe_close(session.request_close)
        session.subscribe_close(lambda: session.wait_closed(timeout=WAIT))
        if mode.sync:
            session.subscribe_close(lambda: session.commit(lambda tx: None))
            session.subscribe_close(lambda: asyncio.run(session.aclose()))
            session.subscribe_close(lambda: asyncio.run(session.await_closed()))
            session.close(timeout=WAIT)
            expected = [
                reentrant,
                M.session.SessionClosedError,
                reentrant,
            ]
        else:

            def spawn(make: Callable[[], Any]) -> Callable[[], None]:
                return lambda: tasks.append(asyncio.ensure_future(make()))

            session.subscribe_close(spawn(session.aclose))
            session.subscribe_close(spawn(session.await_closed))
            session.subscribe_close(
                spawn(lambda: session.acommit(partial(note, conv=conv)))
            )

            async def body() -> None:
                await asyncio.wait_for(session.aclose(), WAIT)
                closing, closed, late = tasks
                await asyncio.wait_for(closing, WAIT)
                assert (await asyncio.wait_for(closed, WAIT)).reason == "closed"
                with pytest.raises(M.session.SessionClosedError):
                    await late

            run_async(body)
            expected = [reentrant]
        assert [type(error) for error, _ in rig.reports] == expected
        assert {info for _, info in rig.reports} <= {"listener_error"}
        assert session.end.reason == "closed" and rig.store.count("close") == 1


class Stop(BaseException):
    """A process-control exception the reporter raises (test only)."""


def _reporter_stop(rig: Rig, broken: RuntimeError) -> None:
    def on_report(error: BaseException, info: str) -> None:
        if error is broken:
            raise Stop

    rig.on_report = on_report
    rig.store.fault("close", broken)


class Handled:
    """A loop exception handler that records what reaches it (test only)."""

    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []

    def __call__(self, loop: Any, context: dict[str, Any]) -> None:
        self.contexts.append(context)


def _off_loop_reporter(M: Mods, cancel: bool) -> Callable[[Mods, Mode], None]:
    def run(M: Mods, mode: Mode) -> None:
        with Rig(M) as rig:
            broken = RuntimeError("close broke")
            _reporter_stop(rig, broken)
            gate = rig.store.hold("close")
            handled = Handled()

            async def body() -> None:
                asyncio.get_running_loop().set_exception_handler(handled)
                closer = asyncio.create_task(rig.session.aclose())
                await entered(gate)
                if cancel:
                    closer.cancel()  # giving up never loses the outcome
                    with pytest.raises(asyncio.CancelledError):
                        await closer
                gate.release.set()
                if not cancel:
                    # Closers get the recorded close error, not the reporter's.
                    with pytest.raises(RuntimeError) as caught:
                        await asyncio.wait_for(closer, WAIT)
                    assert caught.value is broken
                with pytest.raises(RuntimeError) as again:
                    await asyncio.wait_for(rig.session.aclose(), WAIT)
                assert again.value is broken
                await auntil(lambda: bool(handled.contexts), "handler reached")
                await spin()

            mode.run(body)
            # Exactly once, and it is the reporter's process-control exception.
            assert [type(c["exception"]) for c in handled.contexts] == [Stop]
            assert rig.session.end == M.session.SessionEnd("failed", broken)
            assert rig.reports == [(broken, "session_failed")]

    return run


scenario("off-loop-close-reporter-outcome-reaches-loop-handler", (ASYNC,))(
    _off_loop_reporter(REAL, False)
)
scenario("cancelled-aclose-never-loses-the-reporter-outcome", (ASYNC,))(
    _off_loop_reporter(REAL, True)
)


def _closed_loop_outcome(failing_hook: bool) -> Callable[[Mods, Mode], None]:
    """Background close outcome after its loop closed: excepthook, then stderr."""

    def run(M: Mods, mode: Mode) -> None:
        with Rig(M) as rig:
            marker = f"background-close-{uuid.uuid4().hex}"
            broken = RuntimeError("close broke")
            _reporter_stop(rig, broken)
            stop = Stop(marker)

            def on_report(error: BaseException, info: str) -> None:
                if error is broken:
                    raise stop

            rig.on_report = on_report
            gate = rig.store.hold("close")
            loop = asyncio.new_event_loop()
            executor = ThreadPoolExecutor(1, thread_name_prefix="asyncio")
            loop.set_default_executor(executor)
            buffer = io.StringIO()
            # pytest replaces threading.excepthook; install the stock default
            # (or a failing hook) only for this scenario.
            saved = threading.excepthook

            def broken_hook(args: Any) -> None:
                raise RuntimeError("excepthook broke")

            async def start() -> None:
                rig.session.request_close()  # claimed on the loop: background

            try:
                threading.excepthook = (
                    broken_hook if failing_hook else threading.__excepthook__
                )
                with contextlib.redirect_stderr(buffer):
                    loop.run_until_complete(start())
                    assert gate.entered.wait(WAIT)
                    loop.close()  # its executor is shut down without waiting
                    gate.release.set()
                    executor.shutdown(wait=True)
            finally:
                threading.excepthook = saved
                if not loop.is_closed():
                    loop.close()
            # Reported exactly once, and nothing raised from the fallback.
            assert buffer.getvalue().count(marker) == 1, buffer.getvalue()
            assert rig.session.end == M.session.SessionEnd("failed", broken)
            with pytest.raises(RuntimeError) as caught:
                rig.session.close(timeout=WAIT)
            assert caught.value is broken

    return run


scenario("closed-loop-background-outcome-reaches-default-excepthook", (SYNC,))(
    _closed_loop_outcome(False)
)
scenario("closed-loop-background-outcome-survives-a-failing-excepthook", (SYNC,))(
    _closed_loop_outcome(True)
)


@scenario("commit-result-independent-of-backend-close", (ASYNC,))
def _commit_independent(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        released = rig.event()
        # The backend close waits for an event set only after acommit returns.
        rig.store.before["close"] = lambda: (
            None
            if released.wait(WAIT)
            else (_raise(AssertionError("backend close waited for the commit")))
        )

        async def body() -> None:
            conv = await conversation(rig, mode)
            rig.session.subscribe_commits(
                lambda publication: rig.session.request_close()
            )
            result = await asyncio.wait_for(
                rig.session.acommit(partial(note, conv=conv)), WAIT
            )
            assert result > 0
            released.set()
            end = await asyncio.wait_for(rig.session.await_closed(), WAIT)
            assert end.reason == "closed" and rig.owner._close_error is None

        mode.run(body)


def _raise(error: BaseException) -> None:
    raise error


@scenario("concurrent-aclose-on-different-loops", (SYNC,))
def _two_loops(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        gate = rig.store.hold("close")
        first = rig.start("loop-1", lambda: asyncio.run(rig.session.aclose()))
        assert gate.entered.wait(WAIT)
        second = rig.start("loop-2", lambda: asyncio.run(rig.session.aclose()))
        until(lambda: second.thread.is_alive(), "second closer waiting")
        gate.release.set()
        first.value()
        second.value()
        assert rig.session.end.reason == "closed" and rig.store.count("close") == 1


@scenario("callback-error-survives-a-process-control-close-error")
def _callback_error_survives(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        stop = Stop()
        rig.store.fault("close", stop)

        async def body() -> None:
            conv = await conversation(rig, mode)
            spec = await live(rig, mode, conv)
            await mode.unload(rig)
            gate = rig.store.hold("find_document")

            async def cb(tx: Any) -> None:
                rig.session.request_close()  # close drains when this leaves
                if not mode.sync:
                    tx.doc(spec, conversation_id=conv)  # pending: drained
                raise ValueError("callback failed")

            if mode.sync:
                gate.release.set()
                with pytest.raises(ValueError, match="callback failed"):
                    rig.session.commit(lambda tx: drive(cb(Adapter(tx))))
            else:
                task = asyncio.create_task(rig.session.acommit(cb))
                await entered(gate)
                gate.release.set()
                with pytest.raises(ValueError, match="callback failed"):
                    await task
            # Every closer gets the recorded process-control close error.
            with pytest.raises(Stop):
                await mode.close(rig)

        mode.run(body)
        assert rig.session.end == M.session.SessionEnd("failed", stop)


@scenario("inline-close-reporter-exception-still-propagates", (SYNC,))
def _inline_reporter(M: Mods, mode: Mode) -> None:
    with Rig(M) as rig:
        broken = RuntimeError("close broke")
        _reporter_stop(rig, broken)
        with pytest.raises(Stop):
            rig.session.close(timeout=WAIT)
        with pytest.raises(RuntimeError) as caught:
            rig.session.close(timeout=WAIT)
        assert caught.value is broken
        assert rig.session.end == M.session.SessionEnd("failed", broken)


# -- pytest entry points ------------------------------------------------------------------------


def _alive() -> list[str]:
    return [
        t.name
        for t in threading.enumerate()
        if (t.name.startswith("de02b2b-") or t.name.startswith("asyncio_"))
        and t.is_alive()
    ]


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario(name: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        SCENARIOS[name](REAL)
        gc.collect()
    assert not _alive()


def test_session_is_private_and_not_exported() -> None:
    package = sys.modules["pipy_harness.native.durable"]
    for name in ("Session", "SyncTx", "AsyncTx", "TxOp", "CommitPublication"):
        assert name not in vars(package)
    assert real_core.Session.__module__ == "pipy_harness.native.durable.session_core"


def test_owner_without_line_keeps_b1a_close() -> None:
    # No ticket ever admitted: request_close seals the guard and closes at once.
    store = Store(MemoryStorage())
    owner = real_session.SessionLifecycle(store)
    owner.request_close()
    assert owner.sealed and store.count("close") == 1
    assert owner.end == real_session.SessionEnd("closed")


def test_owner_line_abandon_and_closed_loop_wake_pass_the_line_on() -> None:
    owner = real_session.SessionLifecycle(Store(MemoryStorage()))
    holder, queued = real_session._LineTicket(), real_session._LineTicket()
    owner._line_enter(holder)
    owner._line_enter(queued)
    assert owner._line_holder() is holder and len(owner._queue) == 1
    # An interrupted sync waiter is unqueued and never granted later.
    assert owner._line_abandon(queued) is None
    assert queued.state == "cancelled" and not owner._queue

    def closed_loop() -> None:
        raise RuntimeError("Event loop is closed")

    woken: list[str] = []
    dead = real_session._LineTicket(closed_loop)
    alive = real_session._LineTicket(lambda: woken.append("alive"))
    owner._line_enter(dead)
    owner._line_enter(alive)
    assert owner._line_leave(holder) is None
    # The dead loop's ticket counts as cancelled; the line moves on.
    assert dead.state == "cancelled" and alive.state == "granted"
    assert owner._line_holder() is alive and woken == ["alive"]
    assert owner._line_leave(alive) is None and owner._line_holder() is None
    owner.close(timeout=WAIT)


# -- private negative controls --------------------------------------------------------------------

MODULE_FILES = {"session": "session", "tx": "tx", "core": "session_core"}
SOURCES = {
    name: Path(getattr(REAL, name).__file__).read_text() for name in MODULE_FILES
}
PACKAGE = "pipy_harness.native.durable"

MUTANTS: dict[str, dict[str, list[tuple[str, str]]]] = {
    # The line is left before pending operations settle.
    "line-hold-during-drain": {
        "core": [
            (
                "    if futures:\n        await asyncio.wait(futures)\n",
                "    if futures:\n        pass\n",
            )
        ]
    },
    "pending-check": {
        "tx": [
            (
                "        if self._pending:\n"
                "            self._abort()\n"
                "            raise PendingTxOperations(tuple(self._pending))\n",
                "",
            )
        ]
    },
    "shield": {
        "core": [
            (
                "        return await asyncio.shield(settle)\n",
                "        return await settle\n",
            )
        ]
    },
    "queued-cancel-cut": {
        "core": [
            (
                "        if view is not None and view.cancelled:\n"
                "            raise CommitCancelled()\n",
                "",
            )
        ]
    },
    "post-callback-health-check": {
        "core": [
            (
                "            # A callback that caught a failed read must not commit.\n"
                "            self._assert_healthy()\n"
                "        except BaseException:\n",
                "        except BaseException:\n",
            ),
            (
                "            # A callback that caught a failed read must not commit.\n"
                "            self._assert_healthy()\n"
                "            # Seal, check pending work",
                "            # Seal, check pending work",
            ),
        ]
    },
    "adopt-fail-to-fail": {
        "core": [
            (
                "            # Storage already committed; memory would stay behind it.\n"
                "            self._owner.fail(error)\n",
                "",
            )
        ]
    },
    "internal-host-routing": {
        "core": [
            (
                "            self._contained(partial(listener, publication), self._owner.fail)\n",
                "            self._contained(\n"
                "                partial(listener, publication), self._listener_error\n"
                "            )\n",
            )
        ]
    },
    "line-drain-close-predicate": {
        "session": [
            (
                "            claim = claim and self._holder is None and not self._queue\n",
                "",
            )
        ]
    },
    "close-listener-ordering": {
        "session": [
            (
                "                try:\n"
                "                    self._begin_close()\n"
                "                finally:\n"
                "                    self._report(first)\n",
                "                try:\n"
                "                    self._report(first)\n"
                "                finally:\n"
                "                    self._begin_close()\n",
            )
        ]
    },
    "bulk-reject": {
        "session": [
            (
                "        if self._queue:\n            for ticket in self._queue:\n",
                "        if False:\n            for ticket in self._queue:\n",
            )
        ]
    },
    # Round-1 review regressions.
    "line-hand-off-atomic": {
        "session": [
            (
                "            ticket.state = _LEFT\n"
                "            # Leaving and granting the next ticket are one step, so no entrant\n"
                "            # can grant a queued ticket without owning its wake-up.\n"
                "            granted = self._pass_line()\n",
                "            ticket.state = _LEFT\n"
                "            self._holder = None\n"
                "        with self._cond:\n"
                "            granted = None if self._holder is not None else self._pass_line()\n",
            )
        ]
    },
    "close-hook-done-gate": {
        "session": [("                and self._close_hook_done\n", "")]
    },
    "close-off-loop": {
        "core": [
            (
                "    loop = _running_loop()\n    if loop is None:\n        return False\n",
                "    return False\n",
            )
        ]
    },
    "wait-closed-reentry": {
        "core": [
            ('        self._refuse_reentry("wait_closed")\n', ""),
            ('        self._refuse_reentry("await_closed")\n', ""),
        ]
    },
    "detach-outside-lock": {
        "core": [
            (
                "                self._close_listeners.take(),\n            )\n"
                "        del dropped",
                "                self._close_listeners.take(),\n            )\n"
                "            del dropped",
            )
        ]
    },
    # Round-2 review regressions.
    "nested-close-returns": {
        "session": [
            (
                "        if self._in_close_hook():\n"
                "            # Pi: a close from a close listener returns the same pending\n"
                "            # closing promise; waiting here would wait for this very hook.\n"
                "            return\n",
                "",
            )
        ]
    },
    "background-close-outcome-reported": {
        "core": [
            (
                "            loop.call_soon_threadsafe(loop.call_exception_handler, context)\n",
                "            pass\n",
            ),
            ("            _report_unhandled(error)\n", "            pass\n"),
        ]
    },
    "txop-shield": {
        "core": [
            (
                "        return asyncio.shield(self._future).__await__()\n",
                "        return self._future.__await__()\n",
            )
        ]
    },
    "in-flight-memo": {
        "core": [
            ("        future = self._calls.get(request)\n", "        future = None\n")
        ]
    },
}

EXPECTED_FAILURES: dict[str, list[str]] = {
    "line-hold-during-drain": [
        "docs284-pending-acquisition-line-held-until-drained[async]",
        "docs337-callback-error-wins-over-pending[async]",
    ],
    "pending-check": [
        "docs284-pending-acquisition-line-held-until-drained[async]",
        "docs311-no-late-initializer-or-mint[async]",
    ],
    "shield": ["task-cancel-during-held-commit-stays-durable[async]"],
    "queued-cancel-cut": [
        "cancel-view-queued-never-enters-fifo-kept[sync]",
        "cancel-view-queued-never-enters-fifo-kept[async]",
    ],
    "post-callback-health-check": [
        "h276-caught-failed-read-then-return-health-checked[sync]",
        "h276-caught-failed-read-then-return-health-checked[async]",
    ],
    "adopt-fail-to-fail": [
        "adopt-failure-rejects-durable-commit-and-fails[sync]",
        "adopt-failure-rejects-durable-commit-and-fails[async]",
    ],
    "internal-host-routing": [
        "h328-internal-listener-poisons-durable-commit-resolves[sync]",
        "h328-internal-listener-poisons-durable-commit-resolves[async]",
    ],
    "line-drain-close-predicate": [
        "failure-while-line-held-backend-close-waits-for-release[sync]",
        "failure-while-line-held-backend-close-waits-for-release[async]",
    ],
    "close-listener-ordering": [
        "close-listeners-run-before-the-failure-report[sync]",
        "close-listeners-run-before-the-failure-report[async]",
        "failure-while-line-held-backend-close-waits-for-release[sync]",
    ],
    "bulk-reject": [
        "bulk-reject-queued-tickets-on-failure[sync]",
        "bulk-reject-queued-tickets-on-failure[async]",
    ],
    "in-flight-memo": ["async-driver-awaits-in-flight-memoized-call[async]"],
    "txop-shield": ["txop-await-is-shielded[async]"],
    "nested-close-returns": [
        "close-listeners-reentering-the-session[sync]",
        "close-listeners-reentering-the-session[async]",
    ],
    "background-close-outcome-reported": [
        "off-loop-close-reporter-outcome-reaches-loop-handler[async]",
        "cancelled-aclose-never-loses-the-reporter-outcome[async]",
        "closed-loop-background-outcome-reaches-default-excepthook[sync]",
        "closed-loop-background-outcome-survives-a-failing-excepthook[sync]",
    ],
    "line-hand-off-atomic": [
        "hand-off-never-loses-a-wake-up[sync]",
        "hand-off-never-loses-a-wake-up[async]",
    ],
    "close-hook-done-gate": [
        "second-closer-never-drains-under-a-running-close-hook[sync]"
    ],
    "close-off-loop": [
        "blocking-backend-close-runs-off-the-event-loop[async]",
        "backend-close-awaiting-a-loop-task-completes[async]",
    ],
    "wait-closed-reentry": [
        "wait-closed-from-the-holder-is-reentrant[sync]",
        "wait-closed-from-the-holder-is-reentrant[async]",
    ],
    "detach-outside-lock": [
        "listener-finalizers-never-run-under-the-owner-lock[sync]",
        "listener-finalizers-never-run-under-the-owner-lock[async]",
    ],
}


def mutant(name: str) -> Mods:
    """Build a consistent mutant: every module from the mutated one onward."""
    mutations = MUTANTS[name]
    order = ["session", "tx", "core"]
    first = min(order.index(module) for module in mutations)
    built: dict[str, types.ModuleType] = {}
    saved: dict[str, types.ModuleType] = {}
    tag = name.replace("-", "_")
    try:
        for index, module_name in enumerate(order):
            full = f"{PACKAGE}.{MODULE_FILES[module_name]}"
            real = sys.modules[full]
            if index < first:
                built[module_name] = real
                continue
            source = SOURCES[module_name]
            for old, new in mutations.get(module_name, []):
                assert source.count(old) == 1, (name, old)
                source = source.replace(old, new)
            own = f"{PACKAGE}._de02b2b_mutant_{tag}_{MODULE_FILES[module_name]}"
            module = types.ModuleType(own)
            module.__package__ = PACKAGE
            module.__file__ = real.__file__
            saved.setdefault(full, real)
            # Later modules' relative imports resolve to this mutant.
            sys.modules[full] = module
            sys.modules[own] = module
            try:
                exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)
            finally:
                del sys.modules[own]
            built[module_name] = module
    finally:
        for full, real in saved.items():
            sys.modules[full] = real
    return Mods(built["session"], built["tx"], built["core"])


def run_scenario(name: str, mods: Mods) -> BaseException | None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            SCENARIOS[name](mods)
    except (Exception, pytest.fail.Exception) as error:  # noqa: BLE001 - recorded
        return error
    return None


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_private_negative_control_fails_designated_scenarios(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "WAIT", 1.0)
    mods = mutant(name)
    for expected in EXPECTED_FAILURES[name]:
        assert run_scenario(expected, REAL) is None, expected
        assert run_scenario(expected, mods) is not None, (name, expected)
    gc.collect()
    assert not _alive()


def test_every_designated_scenario_exists() -> None:
    for names in EXPECTED_FAILURES.values():
        assert set(names) <= set(SCENARIOS)
    assert set(EXPECTED_FAILURES) == set(MUTANTS)

"""DE02b2a private Tx core and raw-base drafts over the b1a guard and real Memory.

The product has no commit entrypoint; ``Rig.commit`` below composes the b1a
guard, the core, ``guard.commit`` and ``adopt`` only inside this test module, in
the settlement order of Pi ``#runCommit`` (session.ts:716–767). Pi anchors
(pinned 2ec55d80, read via git show): test/session-tables.test.ts 82, 104, 121,
132, 188, 213, 303, 330, 354 and test/session-documents.test.ts 78, 120, 137,
158, 174, 207, 228, 250, 284, 311, 352, 363, 380, 405, 459, 479, 519, 549, 591.
Ordering evidence uses owned Events and bounded joins, or a test-only stepping
driver that suspends bodies at their ``_Io``; no sleep is ordering evidence.
Every scenario takes the module under test so the same bodies run against
private source mutants (negative controls at the end).
"""

from __future__ import annotations

import copy
import pickle
import sys
import threading
import types
import warnings
from collections.abc import Callable, Coroutine
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.durable import tx as real_tx
from pipy_harness.native.durable.codec import JsonValueError, OwnedJson
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.records import OMITTED, RecordError
from pipy_harness.native.durable.session import SessionFailed, SessionLifecycle
from pipy_harness.native.durable.storage import (
    ConversationQuery,
    DocumentAddress,
    EntryQuery,
    StorageWrite,
    TaskQuery,
    UnsupportedStorageOperation,
)

WAIT = 5.0
"""Bound for every wait/join; mutant matrices may lower it."""

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


# -- controlled delegating backend -----------------------------------------------


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
    """Explicit delegating wrapper around one real MemoryStorage (test only)."""

    def __init__(self, inner: MemoryStorage) -> None:
        self.inner = inner
        self.lock = threading.Lock()
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.commits: list[tuple[StorageWrite, ...]] = []
        self._holds: dict[str, Gate] = {}
        self._gates: list[Gate] = []
        self._faults: dict[str, BaseException] = {}

    def hold(self, name: str) -> Gate:
        gate = Gate()
        with self.lock:
            self._holds[name] = gate
            self._gates.append(gate)
        return gate

    def fault(self, name: str, error: BaseException) -> None:
        with self.lock:
            self._faults[name] = error

    def release_all(self) -> None:
        with self.lock:
            gates = list(self._gates)
        for gate in gates:
            gate.release.set()

    def count(self, name: str) -> int:
        with self.lock:
            return [call for call, _ in self.calls].count(name)

    def _run(self, name: str, *args: Any, **kwargs: Any) -> Any:
        with self.lock:
            self.calls.append((name, args))
            gate = self._holds.pop(name, None)
            fault = self._faults.pop(name, None)
        if gate is not None:
            gate.hold()
        if fault is not None:
            raise fault
        result = getattr(self.inner, name)(*args, **kwargs)
        if name == "commit":
            with self.lock:
                self.commits.append(args[0])
        return result

    def __getattr__(self, name: str) -> Any:
        if name in STORAGE_METHODS:
            return partial(self._run, name)
        raise AttributeError(name)


# -- owned threads ------------------------------------------------------------------


class Worker:
    def __init__(self, name: str, fn: Callable[[], Any]) -> None:
        self.fn = fn
        self.result: Any = None
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, name="de02b2a-" + name)
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


# -- the test-only commit composition -------------------------------------------------


class Tx:
    """Test-only sync view: unwraps settled sync handles, as b2b's SyncTx will."""

    def __init__(self, T: types.ModuleType, core: Any) -> None:
        self._T = T
        self.core = core

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.core, name)

        def call(*args: Any, **kwargs: Any) -> Any:
            out = method(*args, **kwargs)
            return out.result() if type(out) is self._T._Done else out

        return call


class Publication:
    def __init__(self, seq: int, writes: tuple[Any, ...], changes: tuple[Any, ...]):
        self.seq = seq
        self.writes = writes
        self.changes = changes


class Rig:
    def __init__(self, T: types.ModuleType) -> None:
        self.T = T
        self.inner: Any = MemoryStorage()
        self.store = Store(self.inner)
        self.reports: list[tuple[BaseException, str]] = []
        self.owner = SessionLifecycle(self.store, self._report)
        self.cache = T._DocumentCache()
        self.clock = 1000.0
        self.publications: list[Publication] = []
        self.workers: list[Worker] = []
        self.inits: dict[str, int] = {}

    def _report(self, error: BaseException, info: str) -> None:
        self.reports.append((error, info))

    def now(self) -> float:
        return self.clock

    def core(self, driver: Any = None, **scope: Any) -> Any:
        driver = driver if driver is not None else self.T._SyncDriver()
        return self.T._TxCore(
            self.owner.storage, self.cache, driver, now=self.now, **scope
        )

    def commit(self, callback: Callable[[Tx], Any], **scope: Any) -> Any:
        """Pi ``#runCommit`` order over the sync driver (test composition only)."""
        driver = self.T._SyncDriver()
        core = self.core(driver, **scope)
        try:
            result = callback(Tx(self.T, core))
            failure = self.owner.failure
            if failure is not None:
                raise SessionFailed(failure)
        except BaseException:
            assert core.settle_failure() == ()
            raise
        writes = driver.run(core.settle_success())
        if not writes:
            core.discard()
            return result
        try:
            seq = self.owner.storage.commit(writes)
        except BaseException:
            core.discard()
            raise
        try:
            changes = core.adopt(seq)
        except BaseException as error:
            self.owner.fail(error)
            raise
        self.publications.append(Publication(seq, writes, changes))
        return result

    def start(self, name: str, fn: Callable[[], Any]) -> Worker:
        worker = Worker(name, fn)
        self.workers.append(worker)
        worker.thread.start()
        return worker

    def conversation(self) -> int:
        return self.commit(lambda tx: tx.create_conversation({"kind": "ownerless"}).id)

    def task(self, conversation: int, **options: Any) -> int:
        return self.commit(
            lambda tx: tx.create_task(
                "test.work",
                1,
                {"path": "a"},
                {"phase": "start"},
                ownership={"kind": "conversation"},
                conversation_id=conversation,
                **options,
            )
        )

    def spec(self, kind: str, scope: str = "conversation", **extra: Any) -> Any:
        semantics = (
            {"history": "latest", "fork": "initial"} if scope == "conversation" else {}
        )
        semantics |= extra.pop("semantics", {})
        value = extra.pop(
            "value", {"items": [], "nested": {"count": 0}, "other": {"label": "x"}}
        )

        def initial() -> Any:
            self.inits[kind] = self.inits.get(kind, 0) + 1
            return copy.deepcopy(value)

        return self.T.RawDoc(kind, extra.pop("version", 1), scope, initial, **semantics)

    def live(self, conversation: int) -> Any:
        """Pi ``setupLive``: a live document with items ["a", "b"]."""
        spec = self.spec("test.live")

        def seed(tx: Tx) -> None:
            tx.doc(spec, conversation_id=conversation)["items"].extend(["a", "b"])

        self.commit(seed)
        return spec

    def value(self, spec: Any, conversation: int | None = None) -> Any:
        cached = self.cache.peek(spec, conversation)
        return None if cached is None else cached.value.thaw()

    def __enter__(self) -> Rig:
        return self

    def __exit__(self, *exc: object) -> None:
        self.store.release_all()
        for worker in self.workers:
            worker.thread.join(WAIT)
        alive = [w.thread.name for w in self.workers if w.thread.is_alive()]
        try:
            self.owner.close(timeout=WAIT)
        except BaseException:  # noqa: BLE001, S110 - close outcome is not under test
            pass
        assert not alive, f"unreaped workers: {alive}"


def tree(record: Any) -> Any:
    return None if record is None else record.to_tree()


def trees(writes: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [write.to_tree() for write in writes]


def kinds(writes: tuple[Any, ...]) -> list[str]:
    return [write.to_tree()["type"] for write in writes]


TERMINAL = {
    "status": "terminal",
    "outcome": {"status": "completed", "result": {"ok": True}},
}


def terminal(record: Any) -> dict[str, Any]:
    """A terminal replacement; memos belong only to live tasks."""
    value = record.to_tree()
    value.pop("memos", None)
    return value | {"state": TERMINAL}


# -- test-only stepping driver ----------------------------------------------------------


class Step:
    """One body suspended at its ``_Io`` until the test resumes it."""

    def __init__(self, body: Coroutine[Any, Any, Any]) -> None:
        self.body = body
        self.request: Any = None
        self.outcome: tuple[Any, BaseException | None] | None = None
        self.callbacks: list[Callable[[], object]] = []
        self.advance(None, None)

    def advance(self, send: Any, error: BaseException | None) -> None:
        try:
            request = (
                self.body.throw(error) if error is not None else self.body.send(send)
            )
        except StopIteration as stop:
            self.settle(stop.value, None)
        except Exception as caught:  # noqa: BLE001 - the operation's outcome
            self.settle(None, caught)
        else:
            self.request = request

    def settle(self, value: Any, error: BaseException | None) -> None:
        self.outcome = (value, error)
        for callback in self.callbacks:
            callback()

    def resume(self) -> None:
        request, self.request = self.request, None
        try:
            value = request.run()
        except BaseException as error:  # noqa: BLE001 - delivered into the body
            self.advance(None, error)
        else:
            self.advance(value, None)

    def done(self) -> bool:
        return self.outcome is not None

    def result(self) -> Any:
        assert self.outcome is not None
        value, error = self.outcome
        if error is not None:
            raise error
        return value

    def add_done_callback(self, fn: Callable[[], object]) -> None:
        if self.outcome is not None:
            fn()
        else:
            self.callbacks.append(fn)


class Stepped:
    """Bodies run eagerly to their first ``_Io`` (as b2b's async driver will)."""

    def __init__(self, T: types.ModuleType) -> None:
        self.T = T
        self.steps: list[Step] = []

    def check(self) -> None:
        pass

    def spawn(self, body: Coroutine[Any, Any, Any]) -> Step:
        step = Step(body)
        self.steps.append(step)
        return step

    def follow(self, handle: Any) -> Any:
        raise AssertionError("not used by stepping scenarios")

    def ready(self, value: Any) -> Any:
        return self.T._Done(value)

    def drain(self, handles: tuple[Any, ...]) -> None:
        for handle in handles:
            while not handle.done():
                handle.resume()


# -- scenario registry ------------------------------------------------------------------

Scenario = Callable[[types.ModuleType], None]
SCENARIOS: dict[str, Scenario] = {}


def scenario(name: str) -> Callable[[Scenario], Scenario]:
    def register(fn: Scenario) -> Scenario:
        assert name not in SCENARIOS
        SCENARIOS[name] = fn
        return fn

    return register


# -- tables -------------------------------------------------------------------------------


@scenario("tables82-reads-only-before-first-table-write")
def _tables82(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        notes = rig.spec(
            "test.notes",
            semantics={"history": "rewindable", "fork": "asOf"},
            value={"text": ""},
        )

        def change(tx: Tx) -> None:
            assert tree(tx.conversation(conversation)) == {"id": conversation}
            page = tx.scan_conversations(ConversationQuery({}), 1)
            assert [tree(item) for item in page.items] == [{"id": conversation}]
            assert (
                tx.scan_tasks(TaskQuery({"conversationId": conversation}), 10).items
                == ()
            )
            query = EntryQuery({"conversationId": conversation})
            assert tx.scan_entries(query, 10).items == ()
            tx.append_entry(conversation, {"kind": "note"})
            with pytest.raises(T.ReadAfterWrite):
                tx.conversation(conversation)
            with pytest.raises(
                T.ReadAfterWrite,
                match=r"^Tx\.task\(\) cannot read tables after the first table write$",
            ):
                tx.task(1)
            for read in (
                lambda: tx.entry(1),
                lambda: tx.scan_conversations(ConversationQuery({}), 10),
                lambda: tx.scan_entries(query, 10),
                lambda: tx.scan_tasks(TaskQuery({}), 10),
                lambda: tx.latest_head_marker(conversation),
                lambda: tx.submission(1),
                lambda: tx.submission_by_request(conversation, "r"),
            ):
                with pytest.raises(T.ReadAfterWrite):
                    read()
            # Document access remains available after table writes.
            tx.doc(notes, conversation_id=conversation)["text"] = "after write"

        before = rig.store.count("task")
        rig.commit(change)
        assert (
            rig.store.count("task") == before
        )  # rejected reads never reached the guard
        assert rig.value(notes, conversation) == {"text": "after write"}


def _sync_write(op: str) -> Scenario:
    def run(T: types.ModuleType) -> None:
        with Rig(T) as rig:
            conversation = rig.conversation()
            task_id = rig.task(conversation)
            submission = rig.commit(
                lambda tx: (
                    tx.create_submission(
                        {
                            "conversationId": conversation,
                            "type": "write",
                            "status": "queued",
                        }
                    ).id
                )
            )

            def change(tx: Tx) -> None:
                task = tx.task(task_id)
                if op == "set_task":
                    tx.set_task(task)
                elif op == "settle_submission":
                    tx.settle_submission(
                        submission, {"status": "unanswered", "reason": "x"}
                    )
                else:
                    tx.place_submission(submission, 3)
                with pytest.raises(T.ReadAfterWrite):
                    tx.task(task_id)

            rig.commit(change)

            # Pi sets the write phase before it touches the arguments, so an
            # invalid synchronous write has already ended the read phase.
            def invalid(tx: Tx) -> None:
                assert tree(tx.task(task_id))["id"] == task_id
                with pytest.raises((TypeError, RecordError)):
                    if op == "set_task":
                        tx.set_task({"id": "not an id"})
                    else:
                        getattr(tx, op)("not an id", {"status": "done"})
                with pytest.raises(T.ReadAfterWrite):
                    tx.task(task_id)

            commits = len(rig.store.commits)
            rig.commit(invalid)
            assert len(rig.store.commits) == commits

    return run


for _op in ("set_task", "settle_submission", "place_submission"):
    scenario(f"tables121-sync-write-sets-phase[{_op}]")(_sync_write(_op))


INVALID_WRITES: dict[str, Callable[[Tx, int], Any]] = {
    "append_entry-ingress": lambda tx, c: tx.append_entry(c, ["not", "a", "draft"]),
    "append_entry-id": lambda tx, c: tx.append_entry("not an id", {"kind": "note"}),
    "create_conversation": lambda tx, c: tx.create_conversation({"kind": "task"}),
    "create_task": lambda tx, c: tx.create_task(
        "k", 1, None, None, ownership={"kind": "bogus"}, conversation_id=c
    ),
    "create_submission": lambda tx, c: tx.create_submission("not a create"),
    # Validation inside the body, after minting, as Pi's copyJson.
    "append_entry-body": lambda tx, c: tx.append_entry(
        c, {"kind": "bad", "data": float("nan")}
    ),
}


def _invalid_write(op: str) -> Scenario:
    def run(T: types.ModuleType) -> None:
        with Rig(T) as rig:
            conversation = rig.conversation()

            def change(tx: Tx) -> None:
                with pytest.raises((TypeError, JsonValueError)):
                    INVALID_WRITES[op](tx, conversation)
                with pytest.raises(T.ReadAfterWrite):
                    tx.conversation(conversation)

            commits = len(rig.store.commits)
            rig.commit(change)
            assert len(rig.store.commits) == commits
            assert rig.owner.failure is None

    return run


for _op in INVALID_WRITES:
    scenario(f"invalid-write-still-sets-phase[{_op}]")(_invalid_write(_op))


@scenario("tables104-limits-and-cursors-pass-through")
def _tables104(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        ids = [rig.conversation() for _ in range(3)]

        def change(tx: Tx) -> None:
            query = ConversationQuery({})
            first = tx.scan_conversations(query, 2)
            assert [item.id for item in first.items] == ids[:2]
            assert first.next is not None
            second = tx.scan_conversations(query, 2, first.next)
            assert [item.id for item in second.items] == ids[2:]
            assert second.next is None
            name, args = rig.store.calls[-1]
            assert name == "scan_conversations"
            assert args[0] is query and args[1] == 2 and args[2] is first.next

        rig.commit(change)


@scenario("tables132-minted-ids-and-staging")
def _tables132(T: types.ModuleType) -> None:
    with Rig(T) as rig:

        def change(tx: Tx) -> dict[str, Any]:
            conversation = tx.create_conversation({"kind": "ownerless"})
            first = tx.append_entry(conversation.id, {"kind": "note", "data": "one"})
            headed = tx.append_entry(
                conversation.id, {"kind": "summary", "head": "self"}
            )
            task = tx.create_task(
                "test.work",
                1,
                {"path": "x"},
                {"phase": "start"},
                ownership={"kind": "conversation"},
                conversation_id=conversation.id,
                background=True,
            )
            assert tx.staged_conversations() == (conversation,)
            assert [tree(t) for t in tx.staged_tasks()][0]["id"] == task
            return {
                "conversation": conversation,
                "first": first,
                "headed": headed,
                "task": task,
            }

        created = rig.commit(change)
        ids = [
            created["conversation"].id,
            created["first"].id,
            created["headed"].id,
            created["task"],
        ]
        assert len(set(ids)) == 4
        assert tree(created["headed"])["head"] == created["headed"].id
        assert tree(created["first"]) == {
            "kind": "note",
            "data": "one",
            "id": created["first"].id,
            "conversationId": created["conversation"].id,
        }
        assert tree(rig.inner.entry(created["headed"].id).entry) == tree(
            created["headed"]
        )
        assert tree(rig.inner.task(created["task"])) == {
            "id": created["task"],
            "conversationId": created["conversation"].id,
            "kind": "test.work",
            "version": 1,
            "input": {"path": "x"},
            "background": True,
            "abortRequested": False,
            "state": {"status": "pending", "checkpoint": {"phase": "start"}},
        }
        writes = rig.publications[-1].writes
        assert sorted(kinds(writes)) == ["conversation", "entry", "entry", "task"]
        assert writes == rig.store.commits[-1]
        commits = len(rig.store.commits)
        with pytest.raises(TypeError, match="requires conversation_id"):
            rig.commit(
                lambda tx: tx.create_task(
                    "test.work", 1, None, None, ownership={"kind": "conversation"}
                )
            )
        with pytest.raises(RuntimeError, match="^Conversation 12345 does not exist$"):
            rig.commit(lambda tx: tx.append_entry(12345, {"kind": "note"}))
        assert len(rig.store.commits) == commits


@scenario("tables188-staged-task-owner-and-conversation-change")
def _tables188(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        parent = rig.conversation()

        def change(tx: Tx) -> tuple[int, Any]:
            supervisor = tx.create_task(
                "test.work",
                1,
                {"path": "background"},
                {"phase": "start"},
                ownership={"kind": "conversation"},
                conversation_id=parent,
                background=True,
            )
            child = tx.create_conversation({"kind": "task", "taskId": supervisor})
            return supervisor, child

        supervisor, child = rig.commit(change)
        assert tree(child)["owner"] == {"conversationId": parent, "taskId": supervisor}
        assert tree(rig.inner.conversation(child.id)) == tree(child)

        def move(tx: Tx) -> None:
            value = tree(tx.task(supervisor))
            tx.set_task(value | {"conversationId": child.id})

        with pytest.raises(
            RuntimeError, match=f"^Task {supervisor} cannot change conversations$"
        ):
            rig.commit(move)
        assert tree(rig.inner.task(supervisor))["conversationId"] == parent


def _staged_task(tx: Tx, parent: int, path: str) -> int:
    return tx.create_task(
        "test.work",
        1,
        {"path": path},
        {"phase": "start"},
        ownership={"kind": "conversation"},
        conversation_id=parent,
    )


@scenario("tables213-final-owner-missing-terminal-completing-abort-atomic")
def _tables213(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        parent = rig.conversation()
        staged: dict[str, int] = {}

        def moved(tx: Tx) -> None:
            staged["moved"] = _staged_task(tx, parent, "move")
            value = tree(tx.staged_tasks()[0]) | {"conversationId": 998}
            tx.set_task(value)

        with pytest.raises(RuntimeError, match="cannot change conversations"):
            rig.commit(moved)
        assert rig.inner.task(staged["moved"]) is None
        with pytest.raises(
            RuntimeError, match="^Conversation owner task 999 does not exist$"
        ):
            rig.commit(
                lambda tx: tx.create_conversation({"kind": "task", "taskId": 999})
            )

        # The owner changes later in the same callback; validation uses its final candidate.
        for label, state, abort, message in (
            (
                "aborting",
                {"status": "pending", "checkpoint": {"phase": "start"}},
                True,
                "is abort-marked",
            ),
            (
                "terminal",
                {
                    "status": "terminal",
                    "outcome": {"status": "completed", "result": {"ok": True}},
                },
                False,
                "is terminal",
            ),
            (
                "completing",
                {
                    "status": "completing",
                    "outcome": {"status": "completed", "result": {"ok": True}},
                },
                False,
                "is completing",
            ),
        ):
            commits = len(rig.store.commits)

            def owned(
                tx: Tx, label: str = label, state: Any = state, abort: bool = abort
            ) -> None:
                supervisor = _staged_task(tx, parent, label)
                staged[label] = tx.create_conversation(
                    {"kind": "task", "taskId": supervisor}
                ).id
                staged[label + "-task"] = supervisor
                value = tree(tx.staged_tasks()[0])
                tx.set_task(value | {"abortRequested": abort, "state": state})

            with pytest.raises(RuntimeError, match=message):
                rig.commit(owned)
            assert len(rig.store.commits) == commits
            assert rig.inner.conversation(staged[label]) is None
            assert rig.inner.task(staged[label + "-task"]) is None

        owner = rig.task(parent)

        def finish(tx: Tx) -> None:
            tx.set_task(terminal(tx.task(owner)))

        rig.commit(finish)
        with pytest.raises(
            RuntimeError, match=f"^Conversation owner task {owner} is terminal$"
        ):
            rig.commit(
                lambda tx: tx.create_conversation({"kind": "task", "taskId": owner})
            )


@scenario("tables213-task-owner-final-candidate")
def _tables213_task_owner(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        parent = rig.conversation()
        owner = rig.task(parent)
        before = rig.inner._state

        def child(tx: Tx) -> None:
            value = tree(tx.task(owner))
            child_id = tx.create_task(
                "test.child", 1, None, None, ownership={"kind": "task", "taskId": owner}
            )
            assert tree(tx.staged_tasks()[0])["owner"] == owner and child_id
            tx.set_task(value | {"abortRequested": True})

        with pytest.raises(RuntimeError, match=f"^Task owner {owner} is abort-marked$"):
            rig.commit(child)
        assert rig.inner._state.tables == before.tables
        with pytest.raises(RuntimeError, match="^Task owner 4242 does not exist$"):
            rig.commit(
                lambda tx: tx.create_task(
                    "test.child",
                    1,
                    None,
                    None,
                    ownership={"kind": "task", "taskId": 4242},
                )
            )
        with pytest.raises(TypeError, match="cannot be background"):
            rig.commit(
                lambda tx: tx.create_task(
                    "test.child",
                    1,
                    None,
                    None,
                    ownership={"kind": "task", "taskId": owner},
                    background=True,
                )
            )


@scenario("tables303-owned-strict-table-json")
def _tables303(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        payload = {"nested": {"value": 1}}

        def append(tx: Tx) -> Any:
            created = tx.append_entry(conversation, {"kind": "data", "data": payload})
            payload["nested"]["value"] = 2
            return created

        entry = rig.commit(append)
        assert tree(rig.inner.entry(entry.id).entry)["data"] == {"nested": {"value": 1}}
        omitted = rig.commit(
            lambda tx: tx.append_entry(
                conversation, {"kind": "omitted", "data": OMITTED}
            )
        )
        assert "data" not in tree(omitted)
        assert "data" not in tree(rig.inner.entry(omitted.id).entry)
        commits = len(rig.store.commits)
        with pytest.raises(JsonValueError):
            rig.commit(
                lambda tx: tx.append_entry(
                    conversation, {"kind": "bad", "data": float("nan")}
                )
            )
        assert len(rig.store.commits) == commits
        assert rig.owner.failure is None


@scenario("tables330-complete-replacement-and-times")
def _tables330(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        task_id = rig.task(conversation)

        def running(tx: Tx) -> None:
            value = tree(tx.task(task_id))
            value["state"] = {
                "status": "running",
                "checkpoint": {"phase": "next", "step": 2},
            }
            value["memos"] = {"choice": "b"}
            tx.set_task(value)

        rig.clock = 1500.0
        rig.commit(running)
        stored = tree(rig.inner.task(task_id))
        assert stored["state"] == {
            "status": "running",
            "checkpoint": {"phase": "next", "step": 2},
        }
        assert stored["memos"] == {"choice": "b"} and stored["startedAt"] == 1500.0
        assert "endedAt" not in stored

        rig.clock = 2500.0
        rig.commit(lambda tx: tx.set_task(terminal(tx.task(task_id))))
        stored = tree(rig.inner.task(task_id))
        assert stored["startedAt"] == 1500.0 and stored["endedAt"] == 2500.0
        assert "memos" not in stored

        def again(tx: Tx) -> None:
            tx.set_task(terminal(tx.task(task_id)))

        with pytest.raises(RuntimeError, match=f"^Task {task_id} is already terminal$"):
            rig.commit(again)

        def twice(tx: Tx) -> int:
            created = _staged_task(tx, conversation, "twice")
            value = tree(tx.staged_tasks()[0])
            rig.clock = 3000.0
            tx.set_task(value | {"state": TERMINAL})
            with pytest.raises(
                RuntimeError, match=f"^Task {created} already has a terminal candidate$"
            ):
                tx.set_task(value)
            return created

        created = rig.commit(twice)
        stored = tree(rig.inner.task(created))
        assert stored["state"] == TERMINAL and stored["endedAt"] == 3000.0
        assert kinds(rig.publications[-1].writes) == ["task"]


@scenario("set-task-follows-pi-order")
def _set_task_order(T: types.ModuleType) -> None:
    """Pi setTask: phase, task entry, candidate checks, stamps, then the copy."""
    with Rig(T) as rig:
        conversation = rig.conversation()
        existing = rig.task(conversation)

        def change(tx: Tx) -> None:
            value = tree(tx.task(existing))
            # Invalid shape: the task entry exists before validation fails.
            with pytest.raises(RecordError):
                tx.set_task({"id": existing, "state": "not a state"})
            created = _staged_task(tx, conversation, "later")
            tx.set_task(value | {"state": TERMINAL})
            # Candidate checks precede the shape validation of the new value.
            with pytest.raises(RuntimeError, match="already has a terminal candidate$"):
                tx.set_task({"id": existing})
            with pytest.raises(RuntimeError, match="cannot change conversations$"):
                tx.set_task({"id": created, "conversationId": conversation + 1000})

        rig.commit(change)
        writes = trees(rig.publications[-1].writes)
        assert [w["value"]["id"] for w in writes if w["type"] == "task"][0] == existing


@scenario("tables354-document-after-table-write")
def _tables354(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        progress = rig.spec("test.progress", value={"lines": []})

        def change(tx: Tx) -> int:
            _staged_task(tx, conversation, "a")
            # Validation uses the candidate conversation, not a caller table read.
            other = tx.create_conversation({"kind": "ownerless"}).id
            tx.doc(progress, conversation_id=other)["lines"].append("created")
            return other

        other = rig.commit(change)
        assert rig.value(progress, other) == {"lines": ["created"]}
        publication = rig.publications[-1]
        assert "task" in kinds(publication.writes)
        assert [c.conversation_id for c in publication.changes] == [other]

        def second(tx: Tx) -> None:
            tx.create_conversation({"kind": "ownerless"})
            tx.doc(progress, conversation_id=other)["lines"].append("committed")

        rig.commit(second)
        assert [c.conversation_id for c in rig.publications[-1].changes] == [other]
        assert rig.value(progress, other) == {"lines": ["created", "committed"]}


@scenario("submission-resolution-against-candidates")
def _submissions(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()

        requests = iter(range(100))

        def create(tx: Tx, kind: str) -> int:
            request = f"r{next(requests)}"
            return tx.create_submission(
                {
                    "conversationId": conversation,
                    "type": kind,
                    "status": "queued",
                    "requestId": request,
                }
            ).id

        queued_input = rig.commit(lambda tx: create(tx, "input"))

        def staged(tx: Tx) -> int:
            write = create(tx, "write")
            entry = tx.append_entry(conversation, {"kind": "note"}).id
            tx.place_submission(write, entry)
            tx.place_submission(queued_input, entry)
            tx.settle_submission(queued_input, {"status": "done", "answer": entry})
            return write

        write = rig.commit(staged)
        assert tree(rig.inner.submission(write))["status"] == "done"
        answered = tree(rig.inner.submission(queued_input))
        assert answered["status"] == "done" and answered["answer"] == answered["entry"]
        assert (
            tree(rig.commit(lambda tx: tx.submission_by_request(conversation, "r0")))[
                "status"
            ]
            == "done"
        )
        commits = len(rig.store.commits)
        # A settled submission stays unchanged and writes nothing.
        rig.commit(
            lambda tx: tx.settle_submission(
                queued_input, {"status": "unanswered", "reason": "late"}
            )
        )
        assert len(rig.store.commits) == commits
        for change, message in (
            (
                lambda tx: tx.settle_submission(
                    4242, {"status": "unanswered", "reason": "x"}
                ),
                "^Submission 4242 does not exist$",
            ),
            (
                lambda tx: tx.settle_submission(
                    create(tx, "input"), {"status": "done", "answer": 3}
                ),
                "is not a placed input$",
            ),
        ):
            with pytest.raises(RuntimeError, match=message):
                rig.commit(change)

        def placed_twice(tx: Tx) -> None:
            fresh = create(tx, "input")
            tx.place_submission(fresh, 3)
            tx.place_submission(fresh, 4)

        with pytest.raises(RuntimeError, match="is not queued$"):
            rig.commit(placed_twice)
        assert len(rig.store.commits) == commits


@scenario("ingress-before-guard-and-guard-only-core")
def _ingress(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        calls = len(rig.store.calls)

        def change(tx: Tx) -> None:
            for bad in ("1", 1.0, True, 0, -1, 2**53):
                for op in (
                    tx.conversation,
                    tx.entry,
                    tx.task,
                    tx.submission,
                    tx.latest_head_marker,
                ):
                    with pytest.raises(TypeError):
                        op(bad)
            # Reads never set the phase, even with invalid arguments.
            assert tree(tx.conversation(conversation)) == {"id": conversation}

        rig.commit(change)
        assert [name for name, _ in rig.store.calls[calls:]] == ["conversation"]
        assert rig.owner.failure is None

        def after_write(tx: Tx) -> None:
            tx.append_entry(conversation, {"kind": "note"})
            # Pi #read applies the phase rule before it touches the arguments.
            with pytest.raises(T.ReadAfterWrite):
                tx.conversation("not an id")

        rig.commit(after_write)
        with pytest.raises(TypeError, match="only through the guard"):
            T._TxCore(rig.inner, rig.cache, T._SyncDriver())  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            T._TxCore(rig.owner.storage, {}, T._SyncDriver())


@scenario("expired-tx-rejects-every-use")
def _expired(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)
        captured: list[Tx] = []
        rig.commit(captured.append)
        tx = captured[0]
        for use in (
            lambda: tx.doc(live, conversation_id=conversation),
            lambda: tx.retire_doc(live, conversation_id=conversation),
            lambda: tx.conversation(conversation),
            lambda: tx.set_task({}),
            lambda: tx.settle_submission(1, {"status": "done", "answer": 1}),
            lambda: tx.append_entry(conversation, {"kind": "late"}),
        ):
            with pytest.raises(T.TransactionSettled, match="^Transaction has settled$"):
                use()


# -- documents --------------------------------------------------------------------------


@scenario("docs78-create-on-first-access-and-adopt")
def _docs78(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.spec("test.live")
        mints = rig.store.count("mint_id")

        def change(tx: Tx) -> None:
            draft = tx.doc(live, conversation_id=conversation)
            draft["message"] = "hello"
            assert rig.cache.peek(live, conversation) is None  # never a candidate

        rig.commit(change)
        assert rig.store.count("mint_id") == mints + 1
        publication = rig.publications[-1]
        (write,) = publication.writes
        value = write.to_tree()
        assert value["type"] == "document.create"
        assert value["record"] == {
            "id": value["record"]["id"],
            "kind": "test.live",
            "scope": {"kind": "conversation", "conversationId": conversation},
            "history": "latest",
            "fork": "initial",
        }
        expected = {
            "items": [],
            "nested": {"count": 0},
            "other": {"label": "x"},
            "message": "hello",
        }
        assert value["content"] == {"version": 1, "kind": "base", "value": expected}
        cached = rig.cache.peek(live, conversation)
        assert cached.value.thaw() == expected
        (published,) = publication.changes
        assert tree(published.record)["createdAt"] == publication.seq
        assert (
            published.value is cached.value
        )  # publication shares the snapshot (docs 137)
        assert published.conversation_id == conversation and published.version == 1
        assert (
            rig.cache.peek(live, conversation) is cached
        )  # identity-stable (docs 120)


@scenario("docs120-snapshots-shared-and-prior-revision-stable")
def _docs120(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)
        first = rig.cache.peek(live, conversation)

        def change(tx: Tx) -> None:
            tx.doc(live, conversation_id=conversation)["nested"]["count"] = 1

        rig.commit(change)
        second = rig.cache.peek(live, conversation)
        assert second is not first
        assert first.value.thaw()["nested"] == {"count": 0}
        assert second.value.thaw()["nested"] == {"count": 1}
        assert first.record is second.record


@scenario("docs158-copies-assigned-values-per-placement")
def _docs158(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)
        value = {"label": "shared"}

        def change(tx: Tx) -> None:
            draft = tx.doc(live, conversation_id=conversation)
            draft["other"] = value
            draft["copy"] = value
            draft["items"].append(value)
            draft["from_view"] = draft["other"]
            value["label"] = "mutated"
            assert draft["other"] is not draft["copy"]
            draft["other"]["label"] = "view-write"
            assert draft["copy"] == {"label": "shared"}
            assert draft["from_view"] == {"label": "shared"}

        rig.commit(change)
        snapshot = rig.value(live, conversation)
        assert snapshot["other"] == {"label": "view-write"}
        assert snapshot["copy"] == {"label": "shared"}
        assert snapshot["items"][-1] == {"label": "shared"}
        assert snapshot["from_view"] == {"label": "shared"}


@scenario("docs174-value-no-op-writes-and-publishes-nothing")
def _docs174(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)
        before = rig.cache.peek(live, conversation)
        commits, published = len(rig.store.commits), len(rig.publications)

        def change(tx: Tx) -> None:
            draft = tx.doc(live, conversation_id=conversation)
            draft["nested"]["count"] = 0
            draft["items"].append("z")
            draft["items"].pop()

        rig.commit(change)
        assert len(rig.store.commits) == commits
        assert len(rig.publications) == published
        assert rig.cache.peek(live, conversation) is before


@scenario("docs207-escaped-nested-returned-drafts-revoked")
def _docs207(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)
        escaped: dict[str, Any] = {}

        def change(tx: Tx) -> None:
            draft = tx.doc(live, conversation_id=conversation)
            escaped["root"], escaped["items"], escaped["nested"] = (
                draft,
                draft["items"],
                draft["nested"],
            )
            assert draft["items"] is escaped["items"]  # child views are cached
            draft["message"] = "inside"

        rig.commit(change)
        for use in (
            lambda: escaped["root"]["message"],
            lambda: escaped["root"].__setitem__("message", "outside"),
            lambda: len(escaped["items"]),
            lambda: escaped["items"].append("outside"),
            lambda: escaped["nested"]["count"],
            lambda: list(escaped["root"]),
            lambda: escaped["root"].snapshot(),
            lambda: "message" in escaped["root"],
            lambda: escaped["root"] == {},
        ):
            with pytest.raises(T.DraftRevoked):
                use()
        assert repr(escaped["root"]) == "Draft(<revoked>)"
        assert rig.value(live, conversation)["message"] == "inside"
        returned = rig.commit(lambda tx: tx.doc(live, conversation_id=conversation))
        with pytest.raises(T.DraftRevoked):
            returned["message"]


@scenario("docs207-459-escaped-draft-revoked-while-commit-held")
def _docs207_held(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)
        before = rig.cache.peek(live, conversation)
        frozen = before.value.thaw()
        escaped: dict[str, Any] = {}
        gate = rig.store.hold("commit")

        def change(tx: Tx) -> None:
            draft = tx.doc(live, conversation_id=conversation)
            escaped["root"], escaped["items"] = draft, draft["items"]
            draft["items"].append("c")
            draft["nested"]["count"] = 9

        committing = rig.start("commit", lambda: rig.commit(change))
        assert gate.entered.wait(WAIT)
        # Prepare precedes Storage admission: every view is revoked, even on this thread.
        with pytest.raises(T.DraftRevoked):
            escaped["root"]["items"]
        with pytest.raises(T.DraftRevoked):
            escaped["items"].append("x")
        assert rig.cache.peek(live, conversation) is before  # old revision (docs 459)
        assert before.value.thaw() == frozen
        gate.release.set()
        committing.value()
        assert rig.value(live, conversation)["items"] == ["a", "b", "c"]
        assert before.value.thaw() == frozen


@scenario("draft-affinity-and-api")
def _draft_api(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)
        inside, release = threading.Event(), threading.Event()
        held: dict[str, Any] = {}

        def change(tx: Tx) -> None:
            draft = tx.doc(live, conversation_id=conversation)
            held["draft"], held["tx"] = draft, tx
            assert draft["items"] == ["a", "b"] and draft["items"][0:1] == ["a"]
            with pytest.raises(TypeError):
                hash(draft)
            for bad in (copy.copy, copy.deepcopy, pickle.dumps):
                with pytest.raises(TypeError):
                    bad(draft)
            with pytest.raises(AttributeError):
                draft.message = "x"  # type: ignore[attr-defined]
            with pytest.raises(TypeError):
                draft[1] = "x"
            with pytest.raises(TypeError):
                draft["items"][True] = "x"
            with pytest.raises(JsonValueError):
                draft["bad"] = {1, 2}
            with pytest.raises(JsonValueError):
                draft["\ud83d\ude00"] = 1
                draft["😀"] = 2
            del draft["\ud83d\ude00"]
            assert draft.snapshot() == OwnedJson(
                {"items": ["a", "b"], "nested": {"count": 0}, "other": {"label": "x"}}
            )
            assert draft["nested"].snapshot() == OwnedJson({"count": 0})
            draft["items"].reverse()
            assert draft["items"].pop() == "a"
            inside.set()
            assert release.wait(WAIT)

        committing = rig.start("affinity", lambda: rig.commit(change))
        assert inside.wait(WAIT), committing.join().error
        with pytest.raises(T.TxAffinityError):
            held["draft"]["items"]
        with pytest.raises(T.TxAffinityError):
            held["tx"].conversation(conversation)
        assert repr(held["draft"]) == "Draft(<open>)"
        release.set()
        committing.value()
        assert rig.value(live, conversation)["items"] == ["b"]


@scenario("docs228-callback-failure-aborts-every-change")
def _docs228(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)
        counter = rig.spec("test.counter", "session", value={"count": 0})
        before = rig.cache.peek(live, conversation)
        commits = len(rig.store.commits)
        drafts: list[Any] = []

        def change(tx: Tx) -> None:
            drafts.append(tx.doc(live, conversation_id=conversation))
            drafts.append(tx.doc(counter))
            drafts[0]["message"] = "lost"
            drafts[1]["count"] = 5
            raise ValueError("callback failed")

        with pytest.raises(ValueError, match="callback failed"):
            rig.commit(change)
        assert len(rig.store.commits) == commits
        assert rig.cache.peek(live, conversation) is before
        assert (
            rig.cache.peek(counter) is None
            and rig.inner.find_document(
                DocumentAddress({"kind": "test.counter", "scope": {"kind": "session"}})
            )
            is None
        )
        for draft in drafts:
            with pytest.raises(T.DraftRevoked):
                len(draft)
        with pytest.raises(T.DraftRevoked):
            drafts[0]["message"]

        def keep(tx: Tx) -> None:
            tx.doc(live, conversation_id=conversation)["message"] = "kept"

        rig.commit(keep)
        assert rig.value(live, conversation)["message"] == "kept"


@scenario("docs250-memoized-duplicate-acquisition")
def _docs250(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.spec("test.live")
        mints = rig.store.count("mint_id")

        def change(tx: Tx) -> None:
            first = tx.core.doc(live, conversation_id=conversation)
            assert tx.core.doc(live, conversation_id=conversation) is first
            first.result()["message"] = "once"

        rig.commit(change)
        assert rig.inits["test.live"] == 1
        assert rig.store.count("mint_id") == mints + 1
        assert kinds(rig.store.commits[-1]) == ["document.create"]


@scenario("docs363-scope-and-version-mismatch")
def _docs363(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        rig.live(conversation)
        rewindable = rig.spec(
            "test.live", semantics={"history": "rewindable", "fork": "asOf"}
        )
        v2 = rig.spec("test.live", version=2)
        with pytest.raises(
            TypeError, match="does not match the supplied definition semantics$"
        ):
            rig.commit(lambda tx: tx.doc(rewindable, conversation_id=conversation))
        with pytest.raises(RuntimeError, match="requires migration from version 1$"):
            rig.commit(lambda tx: tx.doc(v2, conversation_id=conversation))
        # A dropped cache entry reloads; a newer stored version refuses older specs.
        counter = rig.spec("test.counter", "session", version=2, value={"count": 0})
        rig.commit(lambda tx: tx.doc(counter).__setitem__("count", 1))
        old = rig.spec("test.counter", "session", value={"count": 0})
        with pytest.raises(RuntimeError, match="has newer version 2 than 1$"):
            rig.commit(lambda tx: tx.doc(old))
        assert rig.owner.failure is None


@scenario("docs380-non-json-rejected-before-admission")
def _docs380(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        dated = T.RawDoc("test.date", 1, "session", lambda: {"at": object()})
        mints, commits = rig.store.count("mint_id"), len(rig.store.commits)
        with pytest.raises(JsonValueError):
            rig.commit(lambda tx: tx.doc(dated))
        listed = T.RawDoc("test.list", 1, "session", lambda: [1])
        with pytest.raises(JsonValueError):
            rig.commit(lambda tx: tx.doc(listed))
        assert rig.store.count("mint_id") == mints
        live = rig.spec("test.live")
        with pytest.raises(JsonValueError):
            rig.commit(
                lambda tx: tx.doc(live, conversation_id=conversation)["items"].append(
                    object()
                )
            )
        assert len(rig.store.commits) == commits
        assert rig.cache.peek(live, conversation) is None
        assert rig.owner.failure is None


@scenario("docs405-assembly-failure-rolls-back-prepared-documents")
def _docs405(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)
        counter = rig.spec("test.counter", "session", value={"count": 0})
        rig.commit(lambda tx: tx.doc(counter).__setitem__("count", 1))
        before_live, before_counter = (
            rig.cache.peek(live, conversation),
            rig.cache.peek(counter),
        )
        commits = len(rig.store.commits)
        drafts: list[Any] = []

        def change(tx: Tx) -> None:
            drafts.append(tx.doc(live, conversation_id=conversation))
            drafts.append(tx.doc(counter))
            drafts[0]["message"] = "lost"
            drafts[1]["count"] = 2
            # Replacing a missing task fails during assembly, after every prepare.
            tx.set_task(
                {
                    "id": 999,
                    "conversationId": conversation,
                    "kind": "missing",
                    "version": 1,
                    "input": None,
                    "background": False,
                    "abortRequested": False,
                    "state": {"status": "pending", "checkpoint": {"phase": "start"}},
                }
            )

        with pytest.raises(RuntimeError, match="^Task 999 does not exist$"):
            rig.commit(change)
        assert len(rig.store.commits) == commits
        assert rig.cache.peek(live, conversation) is before_live
        assert rig.cache.peek(counter) is before_counter
        with pytest.raises(T.DraftRevoked):
            drafts[0]["message"]

        def next_(tx: Tx) -> None:
            tx.doc(live, conversation_id=conversation)["message"] = "next"
            tx.doc(counter)["count"] = 3

        rig.commit(next_)
        assert rig.value(counter)["count"] == 3


@scenario("docs479-retire-and-recreate-at-same-address")
def _docs479(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)
        old = rig.cache.peek(live, conversation).record.id

        def change(tx: Tx) -> None:
            draft = tx.doc(live, conversation_id=conversation)
            draft["message"] = "final"
            assert tx.retire_doc(live, conversation_id=conversation) is None
            replacement = tx.doc(live, conversation_id=conversation)
            assert replacement is not draft
            replacement["message"] = "new"

        rig.commit(change)
        publication = rig.publications[-1]
        writes = trees(publication.writes)
        assert [w["type"] for w in writes] == [
            "document.change",
            "document.retire",
            "document.create",
        ]
        assert writes[0]["id"] == old and writes[1] == {
            "type": "document.retire",
            "id": old,
        }
        retired, created = publication.changes
        assert retired.value is None and retired.version is None
        assert (
            tree(retired.record)["id"] == old
            and tree(retired.record)["retiredAt"] == publication.seq
        )
        assert (
            created.record.id != old
            and tree(created.record)["createdAt"] == publication.seq
        )
        assert (
            created.value.thaw()["message"] == "new"
            and created.value.thaw()["items"] == []
        )
        assert rig.cache.peek(live, conversation).value is created.value

        rig.commit(lambda tx: tx.retire_doc(live, conversation_id=conversation))
        assert rig.cache.peek(live, conversation) is None
        rig.cache.clear()
        commits = len(rig.store.commits)
        rig.commit(lambda tx: tx.retire_doc(live, conversation_id=conversation))
        assert (
            len(rig.store.commits) == commits
        )  # retiring an absent address is a no-op


@scenario("docs519-retire-without-acquisition-and-recreate")
def _docs519(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)
        old = rig.cache.peek(live, conversation).record.id
        rig.cache.clear()

        def change(tx: Tx) -> None:
            tx.retire_doc(live, conversation_id=conversation)
            tx.doc(live, conversation_id=conversation)["message"] = "replacement"

        rig.commit(change)
        writes = trees(rig.store.commits[-1])
        assert sorted(w["type"] for w in writes) == [
            "document.create",
            "document.retire",
        ]
        assert {"type": "document.retire", "id": old} in writes
        absent = rig.spec("test.member", "session", value={"seed": "seed", "hits": 0})

        def recreate(tx: Tx) -> None:
            tx.retire_doc(absent)
            tx.doc(absent)["hits"] = 1

        rig.commit(recreate)
        assert kinds(rig.store.commits[-1]) == ["document.create"]
        assert rig.value(absent) == {"seed": "seed", "hits": 1}


@scenario("docs549-retire-after-acquisition-sync")
def _docs549(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)
        old = rig.cache.peek(live, conversation).record.id

        def change(tx: Tx) -> None:
            acquired = tx.doc(live, conversation_id=conversation)
            tx.retire_doc(live, conversation_id=conversation)
            acquired["message"] = "final"

        rig.commit(change)
        writes = trees(rig.store.commits[-1])
        assert [w["type"] for w in writes] == ["document.change", "document.retire"]
        assert writes[0]["id"] == old and writes[0]["content"]["kind"] == "base"
        assert rig.cache.peek(live, conversation) is None

        rig.commit(
            lambda tx: tx.doc(live, conversation_id=conversation).__setitem__(
                "message", "second"
            )
        )
        second = rig.cache.peek(live, conversation).record.id

        def recreate(tx: Tx) -> None:
            old_draft = tx.doc(live, conversation_id=conversation)
            tx.retire_doc(live, conversation_id=conversation)
            fresh = tx.doc(live, conversation_id=conversation)
            assert fresh is not old_draft
            fresh["message"] = "third"

        rig.commit(recreate)
        writes = trees(rig.store.commits[-1])
        assert {"type": "document.retire", "id": second} in writes
        assert sorted(w["type"] for w in writes) == [
            "document.create",
            "document.retire",
        ]
        assert rig.value(live, conversation)["message"] == "third"


@scenario("docs591-cold-cache-reloads-from-storage")
def _docs591(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)
        loaded = rig.cache.peek(live, conversation)
        rig.cache.clear()
        reads = rig.store.count("find_document")
        rig.commit(
            lambda tx: tx.doc(live, conversation_id=conversation)["items"].append("c")
        )
        assert rig.store.count("find_document") == reads + 1
        assert rig.cache.peek(live, conversation) is not loaded
        rig.cache.clear()
        commits = len(rig.store.commits)
        rig.commit(
            lambda tx: tx.doc(live, conversation_id=conversation)["nested"].__setitem__(
                "count", 0
            )
        )
        assert len(rig.store.commits) == commits  # reloaded baseline, value no-op
        assert rig.value(live, conversation)["items"] == ["a", "b", "c"]


@scenario("docs284-pending-acquisition-rejected-and-drained")
def _docs284(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)
        rig.cache.clear()
        driver = Stepped(T)
        core = rig.core(driver)
        pending = core.doc(live, conversation_id=conversation)
        assert not pending.done() and pending.request is not None
        commits = len(rig.store.commits)
        with pytest.raises(
            T.PendingTxOperations, match="before its pending Tx operations"
        ) as raised:
            T._SyncDriver().run(core.settle_success())
        assert raised.value.pending == (pending,)
        driver.drain(raised.value.pending)
        with pytest.raises(T.TransactionSettled):
            pending.result()
        assert len(rig.store.commits) == commits
        rig.commit(
            lambda tx: tx.doc(live, conversation_id=conversation).__setitem__(
                "message", "after"
            )
        )
        assert rig.value(live, conversation)["message"] == "after"


@scenario("docs311-no-late-initializer-or-mint")
def _docs311(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        for spec, owner in (
            (rig.spec("test.late", "session", value={"count": 0}), None),
            (rig.spec("test.late.conversation"), conversation),
        ):
            driver = Stepped(T)
            core = rig.core(driver)
            mints = rig.store.count("mint_id")
            pending = core.doc(spec, conversation_id=owner)
            assert not pending.done()
            # The callback fails while the acquisition waits on its backend read.
            leftover = core.settle_failure()
            assert leftover == (pending,)
            driver.drain(leftover)
            with pytest.raises(T.TransactionSettled):
                pending.result()
            assert rig.inits.get(spec.kind, 0) == 0
            assert rig.store.count("mint_id") == mints
            assert rig.cache.peek(spec, owner) is None


@scenario("late-table-write-never-stages")
def _late_write(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        driver = Stepped(T)
        core = rig.core(driver)
        entry = core.append_entry(conversation, {"kind": "late"})
        task = core.create_task(
            "late",
            1,
            None,
            None,
            ownership={"kind": "conversation"},
            conversation_id=conversation,
        )
        mints = rig.store.count("mint_id")
        driver.drain(core.settle_failure())
        for handle in (entry, task):
            with pytest.raises(T.TransactionSettled):
                handle.result()
        assert rig.store.count("mint_id") == mints
        assert core.staged_tasks() == () and core.staged_conversations() == ()


@scenario("adopt-requires-one-settled-transaction")
def _adopt_once(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        core = rig.core()
        with pytest.raises(RuntimeError, match="assembled, unadopted"):
            core.adopt(1)
        failed = rig.core()
        assert failed.settle_failure() == ()
        with pytest.raises(RuntimeError, match="assembled, unadopted"):
            failed.adopt(1)
        assert T._SyncDriver().run(core.settle_success()) == ()
        assert core.adopt(1) == ()
        with pytest.raises(RuntimeError, match="assembled, unadopted"):
            core.adopt(2)


@scenario("document-cache-pointer-swap-and-evict")
def _cache(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)
        key = T._resolve(live, conversation)[0]
        current = rig.cache.get(key)
        stale = T.CommittedDocument(current.record, 1, OwnedJson({}))
        rig.cache.swap(key, stale, stale)  # not the occupant: no swap
        assert rig.cache.get(key) is current
        rig.cache.evict(key, current.record.id + 1000)  # another incarnation: kept
        assert rig.cache.get(key) is current
        rig.cache.evict(key, current.record.id)
        assert rig.cache.get(key) is None


# -- foreign-object escape and hook regressions (round-1 review) ---------------------


class Retain:
    """A matcher that keeps whatever it is compared with (a would-be escape)."""

    __hash__ = None  # type: ignore[assignment]

    def __init__(self) -> None:
        self.kept: list[Any] = []

    def __eq__(self, other: object) -> bool:
        self.kept.append(other)
        return False


ESCAPE_VALUE = {
    "items": [{"x": 1}],
    "nested": {"count": 0},
    "other": {"label": "x"},
}


def _poison(kept: list[Any]) -> int:
    """Mutate every retained container; returns how many containers were kept."""
    containers = 0
    for item in kept:
        if type(item) is dict:
            item["escaped"] = True
            containers += 1
        elif type(item) is list:
            item.append("escaped")
            containers += 1
    return containers


@scenario("draft-comparison-never-hands-out-live-containers")
def _escape_compare(T: types.ModuleType) -> None:
    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.spec("test.escape")
        matcher = Retain()

        def change(tx: Tx) -> None:
            draft = tx.doc(live, conversation_id=conversation)
            draft["items"].append({"x": 1})
            items = draft["items"]
            assert (draft == matcher) is False
            assert (items == matcher) is False
            # Nested operand: the matcher sits inside a plain comparison value.
            assert (draft == {"items": matcher, "nested": {}, "other": {}}) is False
            assert (items == [matcher]) is False
            assert matcher not in items
            assert items.count(matcher) == 0
            with pytest.raises(ValueError):
                items.index(matcher)
            with pytest.raises(ValueError):
                items.remove(matcher)
            assert matcher not in draft.values()
            assert ("items", matcher) not in draft.items()
            assert _poison(matcher.kept) >= 6
            assert draft.snapshot() == OwnedJson(ESCAPE_VALUE)

        rig.commit(change)
        assert rig.value(live, conversation) == ESCAPE_VALUE
        # After prepare every retained reference is a detached plain copy.
        _poison(matcher.kept)
        assert rig.value(live, conversation) == ESCAPE_VALUE
        assert not any(T._is_view(item) for item in matcher.kept)


@scenario("draft-foreign-keys-indices-and-spoofs-run-no-hook")
def _foreign_hooks(T: types.ModuleType) -> None:
    calls: list[str] = []

    class Key:
        def __hash__(self) -> int:
            calls.append("hash")
            return hash("items")

        def __eq__(self, other: object) -> bool:
            calls.append("eq")
            return True

    class SubKey(str):
        def __hash__(self) -> int:
            calls.append("str-hash")
            return str.__hash__(self)

    class Index:
        def __index__(self) -> int:
            calls.append("index")
            return 0

    class Spoof:
        @property  # type: ignore[misc]
        def __class__(self) -> type:  # type: ignore[override]
            calls.append("class")
            return T.Draft

    with Rig(T) as rig:
        conversation = rig.conversation()
        live = rig.live(conversation)

        def change(tx: Tx) -> None:
            draft = tx.doc(live, conversation_id=conversation)
            items = draft["items"]
            for key in (Key(), SubKey("items")):
                with pytest.raises(KeyError):
                    draft[key]
                with pytest.raises(KeyError):
                    del draft[key]
                assert key not in draft
                assert draft.get(key, 7) == 7
                assert draft.pop(key, 7) == 7
                with pytest.raises(TypeError):
                    draft[key] = 1
            for use in (
                lambda: items[Index()],
                lambda: items[Index() :],
                lambda: items.__setitem__(slice(None, Index()), []),
                lambda: items.insert(Index(), "x"),
                lambda: items.index("a", Index()),
            ):
                with pytest.raises(TypeError):
                    use()
            with pytest.raises(JsonValueError):
                draft["spoof"] = Spoof()
            for refused in (draft.__getstate__, draft.__reduce__):
                with pytest.raises(TypeError):
                    refused()

        rig.commit(change)
        assert calls == []
        assert rig.value(live, conversation)["items"] == ["a", "b"]


@scenario("raw-doc-exact-builtins-and-no-hook-under-cache-lock")
def _raw_doc_exact(T: types.ModuleType) -> None:
    calls: list[str] = []
    cache = T._DocumentCache()

    class Evil(str):
        def __hash__(self) -> int:
            # The reported deadlock shape: a hook re-entering the cache lock.
            # A bounded acquire records a regression instead of hanging.
            if cache._lock.acquire(timeout=0.2):
                cache._lock.release()
                calls.append("hash")
            else:
                calls.append("hash-under-cache-lock")
            return str.__hash__(self)

        def __eq__(self, other: object) -> bool:
            calls.append("eq")
            return str.__eq__(self, other)

    class Version(int):
        pass

    init = dict
    # A spec tampered with after construction is re-checked before any key
    # is built, so the hook never runs under the cache lock (first, so that a
    # regression shows the reported deadlock shape).
    for field, value in (("scope", Evil("session")), ("kind", Evil("k"))):
        tampered = T.RawDoc("k", 1, "session", init)
        object.__setattr__(tampered, field, value)
        worker = Worker("cache-peek", partial(cache.peek, tampered))
        worker.thread.start()
        worker.join()
        assert type(worker.error) is TypeError, worker.error
    for build in (
        lambda: T.RawDoc(Evil("k"), 1, "session", init),
        lambda: T.RawDoc("k", Version(1), "session", init),
        lambda: T.RawDoc("k", 1, Evil("session"), init),
        lambda: T.RawDoc("k", 1, "conversation", init, Evil("latest"), "initial"),
        lambda: T.RawDoc("k", 1, "conversation", init, "latest", Evil("initial")),
    ):
        with pytest.raises(TypeError):
            build()
    spec = T.RawDoc("k", 1, "conversation", init, "rewindable", "asOf")
    assert all(type(v) is str for v in (spec.scope, spec.history, spec.fork))
    assert calls == []


@scenario("raw-doc-spec-validation")
def _raw_doc(T: types.ModuleType) -> None:
    init = dict
    with pytest.raises(UnsupportedStorageOperation):
        T.RawDoc("t", 1, "task", init)
    for bad in (0, -1, 1.0, True, 2**53):
        with pytest.raises(TypeError, match="positive integer"):
            T.RawDoc("t", bad, "session", init)
    for extra in ({"history": "latest"}, {"fork": "initial"}):
        with pytest.raises(TypeError):
            T.RawDoc("t", 1, "session", init, **extra)
    for semantics in (
        {},
        {"history": "latest", "fork": "asOf"},
        {"history": "rewindable"},
        {"history": "other", "fork": "initial"},
    ):
        with pytest.raises(TypeError):
            T.RawDoc("t", 1, "conversation", init, **semantics)
    with pytest.raises(TypeError):
        T.RawDoc("t", 1, "session", None)
    with pytest.raises(TypeError):
        T.RawDoc(b"t", 1, "session", init)
    spec = T.RawDoc("t", 1, "conversation", init, history="rewindable", fork="asOf")
    cache = T._DocumentCache()
    with pytest.raises(TypeError, match="requires a conversation ID"):
        cache.peek(spec)
    with pytest.raises(TypeError, match="session-scoped"):
        cache.peek(T.RawDoc("t", 1, "session", init), 1)


# -- pytest entry points ------------------------------------------------------------------


def _alive() -> list[str]:
    return [
        t.name
        for t in threading.enumerate()
        if t.name.startswith("de02b2a-") and t.is_alive()
    ]


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario(name: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        SCENARIOS[name](real_tx)
    assert not _alive()


def test_core_is_private_and_not_exported() -> None:
    package = sys.modules["pipy_harness.native.durable"]
    for name in ("_TxCore", "RawDoc", "Draft", "DraftList", "CommittedDocument"):
        assert name not in vars(package)
    assert not any(hasattr(real_tx, name) for name in ("commit", "acommit", "Session"))
    assert not any(hasattr(real_tx._TxCore, name) for name in ("commit", "acommit"))


# -- private negative controls --------------------------------------------------------------

SOURCE = Path(real_tx.__file__).read_text()

MUTANTS: dict[str, list[tuple[str, str]]] = {
    "phase-check": [
        (
            "        if self._has_table_write:\n            raise ReadAfterWrite(method)\n",
            "",
        )
    ],
    "owner-validation": [("        await self._validate_owners()\n", "")],
    # Restores "ingress before phase": the phase is set only after admission.
    "ingress-before-phase": [
        (
            "        self._enter()\n        self._has_table_write = True\n",
            "        self._enter()\n",
        ),
        (
            "        return self._run(self._stage_conversation(owner))\n",
            "        self._has_table_write = True\n"
            "        return self._run(self._stage_conversation(owner))\n",
        ),
        (
            "        return self._run(self._append_entry(key, dict(draft)))\n",
            "        self._has_table_write = True\n"
            "        return self._run(self._append_entry(key, dict(draft)))\n",
        ),
        (
            "        return self._run(body)\n",
            "        self._has_table_write = True\n        return self._run(body)\n",
        ),
        (
            "        return self._run(self._create_submission(conversation, dict(create)))\n",
            "        self._has_table_write = True\n"
            "        return self._run(self._create_submission(conversation, dict(create)))\n",
        ),
        (
            "        self._submission_changes.append((key, value))\n",
            "        self._has_table_write = True\n"
            "        self._submission_changes.append((key, value))\n",
        ),
        (
            '        self._submission_changes.append((key, {"status": "placed", "entry": at}))\n',
            "        self._has_table_write = True\n"
            '        self._submission_changes.append((key, {"status": "placed", "entry": at}))\n',
        ),
        (
            '        id = _id(tree.get("id"), "task")\n',
            '        id = _id(tree.get("id"), "task")\n'
            "        self._has_table_write = True\n",
        ),
    ],
    "open-recheck-after-io": [
        (
            '        """After each ``_Io``: never initialize, mint or stage once settled."""\n'
            "        if self._sealed:\n            raise TransactionSettled()\n",
            '        """After each ``_Io``: never initialize, mint or stage once settled."""\n',
        )
    ],
    "revocation-check": [
        (
            "        if self.state is not _OPEN:\n            raise DraftRevoked()\n",
            "",
        )
    ],
    # Round-1 review: live containers to foreign code; non-exact keys/values.
    "detached-comparison": [
        ("    return OwnedJson(node).thaw()\n", "    return node\n"),
    ],
    "draft-exact-keys": [
        (
            "    if type(key) is not str:\n"
            '        raise KeyError("Draft object keys are exact strings")\n',
            "",
        )
    ],
    "raw-doc-exact-builtins": [
        (
            "    if type(value) is not str:\n"
            '        raise TypeError(f"Document {what} must be an exact string")\n',
            "",
        ),
        ("            return choice\n", "            return cast(str, value)\n"),
    ],
    "copy-on-placement": [
        (
            "        return OwnedJson(value).thaw()\n",
            "        OwnedJson(value)\n        return value\n",
        )
    ],
    "no-op-suppression": [
        (
            "self.baseline is None or value != self.baseline",
            "True",
        )
    ],
}

EXPECTED_FAILURES: dict[str, list[str]] = {
    "phase-check": [
        "tables82-reads-only-before-first-table-write",
        "tables121-sync-write-sets-phase[set_task]",
        "tables121-sync-write-sets-phase[settle_submission]",
        "tables121-sync-write-sets-phase[place_submission]",
    ],
    "ingress-before-phase": [
        "tables121-sync-write-sets-phase[set_task]",
        "tables121-sync-write-sets-phase[settle_submission]",
        "tables121-sync-write-sets-phase[place_submission]",
        "invalid-write-still-sets-phase[append_entry-ingress]",
        "invalid-write-still-sets-phase[append_entry-id]",
        "invalid-write-still-sets-phase[create_conversation]",
        "invalid-write-still-sets-phase[create_task]",
        "invalid-write-still-sets-phase[create_submission]",
    ],
    "owner-validation": [
        "tables213-final-owner-missing-terminal-completing-abort-atomic",
        "tables213-task-owner-final-candidate",
    ],
    "open-recheck-after-io": [
        "docs311-no-late-initializer-or-mint",
        "docs284-pending-acquisition-rejected-and-drained",
        "late-table-write-never-stages",
    ],
    "revocation-check": [
        "docs207-escaped-nested-returned-drafts-revoked",
        "docs207-459-escaped-draft-revoked-while-commit-held",
        "docs228-callback-failure-aborts-every-change",
    ],
    "detached-comparison": ["draft-comparison-never-hands-out-live-containers"],
    "draft-exact-keys": ["draft-foreign-keys-indices-and-spoofs-run-no-hook"],
    "raw-doc-exact-builtins": ["raw-doc-exact-builtins-and-no-hook-under-cache-lock"],
    "copy-on-placement": ["docs158-copies-assigned-values-per-placement"],
    "no-op-suppression": ["docs174-value-no-op-writes-and-publishes-nothing"],
}


def mutant(name: str) -> types.ModuleType:
    source = SOURCE
    for old, new in MUTANTS[name]:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module_name = "pipy_harness.native.durable._de02b2a_mutant_" + name.replace(
        "-", "_"
    )
    module = types.ModuleType(module_name)
    module.__package__ = "pipy_harness.native.durable"
    module.__file__ = real_tx.__file__
    sys.modules[module_name] = module
    try:
        exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)
    finally:
        del sys.modules[module_name]
    return module


def run_scenario(name: str, module: types.ModuleType) -> BaseException | None:
    try:
        SCENARIOS[name](module)
    except (Exception, pytest.fail.Exception) as error:  # noqa: BLE001 - recorded
        return error
    return None


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_private_negative_control_fails_designated_scenarios(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "WAIT", 1.0)
    module = mutant(name)
    for expected in EXPECTED_FAILURES[name]:
        assert run_scenario(expected, real_tx) is None, expected
        assert run_scenario(expected, module) is not None, (name, expected)
    assert not _alive()


def test_every_designated_scenario_exists() -> None:
    for names in EXPECTED_FAILURES.values():
        assert set(names) <= set(SCENARIOS)
    assert set(EXPECTED_FAILURES) == set(MUTANTS)


def test_record_error_is_not_swallowed_by_ingress() -> None:
    # A malformed complete task record is a caller error, not a backend failure.
    with Rig(real_tx) as rig:
        with pytest.raises(RecordError):
            rig.commit(lambda tx: tx.set_task({"id": 1}))
        assert rig.owner.failure is None

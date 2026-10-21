"""DE02b1a private guarded Storage + lifecycle owner over the real MemoryStorage.

Pi anchors (pinned 2ec55d80, read via git show): session/session.ts:208–229,
495–540, 950–1123; test/harness-storage-failure.test.ts:132, 156, 192, 267, 291,
301, 410. Ordering evidence uses owned Events, a witnessed owner Condition and
bounded joins; no sleep is ordering evidence. Every scenario takes the module
under test so the same bodies run against private source mutants (item 8).
"""

from __future__ import annotations

import inspect
import sys
import threading
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.durable import session as real_session
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.records import RecordError
from pipy_harness.native.durable.storage import (
    ConversationQuery,
    Cursor,
    DocumentAddress,
    DocumentQuery,
    EntryQuery,
    Storage,
    StorageClosedError,
    StorageRequestError,
    StorageWrite,
    SubmissionQuery,
    TaskQuery,
    UnsupportedStorageOperation,
)

WAIT = 5.0
"""Bound for every wait/join; mutant matrices may lower it."""

SESSION = {"kind": "session"}


# -- seeding through actual DTOs/operations (test setup only) -----------------


def write(*items: dict[str, Any]) -> tuple[StorageWrite, ...]:
    return tuple(StorageWrite(item) for item in items)


def entry(id: int, conversation: int = 1, **extra: Any) -> dict[str, Any]:
    value = {"id": id, "conversationId": conversation, "kind": "", **extra}
    return {"type": "entry", "value": value}


def seeded() -> MemoryStorage:
    store = MemoryStorage()
    assert store.commit(write({"type": "conversation", "value": {"id": 1}})) == 1
    seq = store.commit(
        write(
            entry(2, head=2),
            entry(3),
            entry(4),
            {
                "type": "task",
                "value": {
                    "id": 5,
                    "conversationId": 1,
                    "kind": "",
                    "version": 1,
                    "input": None,
                    "background": False,
                    "abortRequested": False,
                    "state": {"status": "pending", "checkpoint": None},
                },
            },
            {
                "type": "submission",
                "value": {
                    "id": 6,
                    "conversationId": 1,
                    "type": "input",
                    "status": "queued",
                    "requestId": "req",
                },
            },
            {
                "type": "document.create",
                "record": {"id": 7, "kind": "cache", "scope": SESSION},
                "content": {"kind": "base", "version": 1, "value": {"x": [1]}},
            },
            {
                "type": "conversation",
                "value": {"id": 8, "parent": {"conversationId": 1, "at": 3}},
            },
        )
    )
    assert seq == 2
    return store


ENTRY_Q = EntryQuery({"conversationId": 1})
ADDRESS = DocumentAddress({"scope": SESSION, "kind": "cache"})
DOC_Q = DocumentQuery({"scope": SESSION, "at": "current"})


def note(id: int) -> tuple[StorageWrite, ...]:
    return write(entry(id))


# -- controlled delegating wrapper --------------------------------------------


class GateTimeout(Exception):
    """A held backend call was never released (test failure, not product)."""


class Gate:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.completed = threading.Event()

    def hold(self) -> None:
        self.entered.set()
        if not self.release.wait(WAIT):
            raise GateTimeout("held backend call was never released")


class Controlled:
    """Explicit delegating wrapper around one real MemoryStorage (test only).

    Records exactly how each method was called, may hold one matching call at
    an owned Gate, may inject one fault after release, and returns the real
    backend result object unchanged.
    """

    def __init__(self, inner: MemoryStorage) -> None:
        self.inner = inner
        self.lock = threading.Lock()
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.results: list[tuple[str, Any]] = []
        self.gates: list[Gate] = []
        self._holds: list[tuple[str, Callable[..., bool], Gate]] = []
        self._faults: dict[str, Callable[[], None]] = {}
        self.before_close: Callable[[], None] | None = None

    def hold(self, name: str, when: Callable[..., bool] | None = None) -> Gate:
        gate = Gate()
        with self.lock:
            self.gates.append(gate)
            self._holds.append((name, when or (lambda *a, **k: True), gate))
        return gate

    def fault(self, name: str, error: BaseException | Callable[[], None]) -> None:
        """One-shot fault, read after any hold: before the call, or after close."""
        if isinstance(error, BaseException):
            raised = error

            def action() -> None:
                raise raised

        else:
            action = error
        with self.lock:
            self._faults[name] = action

    def release_all(self) -> None:
        with self.lock:
            gates = list(self.gates)
        for gate in gates:
            gate.release.set()

    def names(self) -> list[str]:
        with self.lock:
            return [name for name, _, _ in self.calls]

    def count(self, name: str) -> int:
        return self.names().count(name)

    def _run(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        with self.lock:
            self.calls.append((name, args, kwargs))
            gate = None
            for index, (held, when, candidate) in enumerate(self._holds):
                if held == name and when(*args, **kwargs):
                    gate = candidate
                    del self._holds[index]
                    break
        try:
            if gate is not None:
                gate.hold()
            with self.lock:
                fault = self._faults.pop(name, None)
            if name == "close" and self.before_close is not None:
                self.before_close()
            if fault is not None and name != "close":
                fault()
            result = getattr(self.inner, name)(*args, **kwargs)
            if fault is not None and name == "close":
                fault()
        finally:
            if gate is not None:
                gate.completed.set()
        with self.lock:
            self.results.append((name, result))
        return result

    def commit(self, *args, **kwargs):
        return self._run("commit", args, kwargs)

    def mint_id(self, *args, **kwargs):
        return self._run("mint_id", args, kwargs)

    def conversation(self, *args, **kwargs):
        return self._run("conversation", args, kwargs)

    def scan_conversations(self, *args, **kwargs):
        return self._run("scan_conversations", args, kwargs)

    def entry(self, *args, **kwargs):
        return self._run("entry", args, kwargs)

    def find_latest_head_marker(self, *args, **kwargs):
        return self._run("find_latest_head_marker", args, kwargs)

    def scan_entries(self, *args, **kwargs):
        return self._run("scan_entries", args, kwargs)

    def task(self, *args, **kwargs):
        return self._run("task", args, kwargs)

    def scan_tasks(self, *args, **kwargs):
        return self._run("scan_tasks", args, kwargs)

    def submission(self, *args, **kwargs):
        return self._run("submission", args, kwargs)

    def scan_submissions(self, *args, **kwargs):
        return self._run("scan_submissions", args, kwargs)

    def submission_by_request(self, *args, **kwargs):
        return self._run("submission_by_request", args, kwargs)

    def find_document(self, *args, **kwargs):
        return self._run("find_document", args, kwargs)

    def document(self, *args, **kwargs):
        return self._run("document", args, kwargs)

    def scan_documents(self, *args, **kwargs):
        return self._run("scan_documents", args, kwargs)

    def close(self, *args, **kwargs):
        return self._run("close", args, kwargs)


# -- owned threads, witnessed waits and the rig -------------------------------


class Worker:
    def __init__(self, name: str, fn: Callable[[], Any]) -> None:
        self.fn = fn
        self.result: Any = None
        self.error: BaseException | None = None
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, name="de02b1a-" + name)
        self.thread.daemon = True

    def _run(self) -> None:
        try:
            self.result = self.fn()
        except BaseException as error:  # noqa: BLE001 - delivered to the test thread
            self.error = error
        finally:
            self.done.set()

    def join(self) -> Worker:
        self.thread.join(WAIT)
        assert not self.thread.is_alive(), f"{self.thread.name} was not reaped"
        return self

    def value(self) -> Any:
        self.join()
        if self.error is not None:
            raise self.error
        return self.result

    def raised(self) -> Any:
        self.join()
        assert self.error is not None, f"{self.thread.name} returned {self.result!r}"
        return self.error


class WitnessCondition(threading.Condition):
    """Test-only observation of an actual blocked owner wait, never product API."""

    def __init__(self) -> None:
        super().__init__(threading.Lock())
        self.waiting = threading.Event()

    def wait(self, timeout: float | None = None) -> bool:
        self.waiting.set()
        return super().wait(timeout)


class Rig:
    def __init__(self, S: types.ModuleType, *, reporter: Any = None) -> None:
        self.S = S
        self.inner: Any = seeded()
        self.state = self.inner._state
        self.store = Controlled(self.inner)
        self.reports: list[tuple[BaseException, str, str]] = []
        self.report_hook: Callable[[BaseException], None] | None = None
        self.owner = S.SessionLifecycle(self.store, reporter or self._report)
        self.guard = self.owner.storage
        self.workers: list[Worker] = []

    def _report(self, error: BaseException, info: str) -> None:
        self.reports.append((error, info, threading.current_thread().name))
        if self.report_hook is not None:
            self.report_hook(error)

    def witness(self) -> WitnessCondition:
        condition = WitnessCondition()
        self.owner._cond = condition
        return condition

    def start(self, name: str, fn: Callable[[], Any]) -> Worker:
        worker = Worker(name, fn)
        self.workers.append(worker)
        worker.thread.start()
        return worker

    def reported(self) -> list[BaseException]:
        return [error for error, _, _ in self.reports]

    def __enter__(self) -> Rig:
        return self

    def __exit__(self, *exc: object) -> None:
        self.store.release_all()
        for worker in self.workers:
            worker.thread.join(WAIT)
        alive = [w.thread.name for w in self.workers if w.thread.is_alive()]
        try:
            self.owner.close(timeout=WAIT)
        except BaseException:  # noqa: BLE001, S110 - recorded close outcomes, asserted
            pass
        assert not alive, f"unreaped workers: {alive}"


# -- scenario registry ----------------------------------------------------------

Scenario = Callable[[types.ModuleType], None]
SCENARIOS: dict[str, Scenario] = {}


def scenario(name: str) -> Callable[[Scenario], Scenario]:
    def register(fn: Scenario) -> Scenario:
        assert name not in SCENARIOS
        SCENARIOS[name] = fn
        return fn

    return register


# Item 1: all 16 delegates -----------------------------------------------------

CONV_Q = ConversationQuery({})
TASK_Q = TaskQuery({"conversationId": 1})
SUB_Q = SubmissionQuery({"conversationId": 1})
CURSOR = Cursor(4, "descending")
BATCH = note(30)

DELEGATES: list[tuple[str, Callable[[Any], Any], tuple[Any, ...], dict[str, Any]]] = [
    ("commit", lambda g: g.commit(BATCH), ("BATCH",), {}),
    ("mint_id", lambda g: g.mint_id(), (), {}),
    ("conversation", lambda g: g.conversation(1), (1,), {}),
    (
        "scan_conversations",
        lambda g: g.scan_conversations(CONV_Q, 1),
        ("CONV_Q", 1, None),
        {},
    ),
    (
        "scan_conversations",
        lambda g: g.scan_conversations(CONV_Q, 5, CURSOR),
        ("CONV_Q", 5, "CURSOR"),
        {},
    ),
    ("entry", lambda g: g.entry(3), (3,), {"conversation_id": None}),
    (
        "entry",
        lambda g: g.entry(3, conversation_id=8),
        (3,),
        {"conversation_id": 8},
    ),
    ("find_latest_head_marker", lambda g: g.find_latest_head_marker(1), (1, None), {}),
    (
        "find_latest_head_marker",
        lambda g: g.find_latest_head_marker(1, 3),
        (1, 3),
        {},
    ),
    ("scan_entries", lambda g: g.scan_entries(ENTRY_Q, 1), ("ENTRY_Q", 1, None), {}),
    (
        "scan_entries",
        lambda g: g.scan_entries(ENTRY_Q, 2, CURSOR),
        ("ENTRY_Q", 2, "CURSOR"),
        {},
    ),
    ("task", lambda g: g.task(5), (5,), {}),
    ("scan_tasks", lambda g: g.scan_tasks(TASK_Q, 3), ("TASK_Q", 3, None), {}),
    ("submission", lambda g: g.submission(6), (6,), {}),
    (
        "scan_submissions",
        lambda g: g.scan_submissions(SUB_Q, 2, None),
        ("SUB_Q", 2, None),
        {},
    ),
    (
        "submission_by_request",
        lambda g: g.submission_by_request(1, "req"),
        (1, "req"),
        {},
    ),
    ("find_document", lambda g: g.find_document(ADDRESS), ("ADDRESS", "current"), {}),
    (
        "find_document",
        lambda g: g.find_document(ADDRESS, "current"),
        ("ADDRESS", "current"),
        {},
    ),
    ("document", lambda g: g.document(7), (7, "current"), {}),
    ("scan_documents", lambda g: g.scan_documents(DOC_Q, 4), ("DOC_Q", 4, None), {}),
    ("close", lambda g: g.close(), (), {}),
]
NAMED = {
    "BATCH": BATCH,
    "CONV_Q": CONV_Q,
    "ENTRY_Q": ENTRY_Q,
    "TASK_Q": TASK_Q,
    "SUB_Q": SUB_Q,
    "ADDRESS": ADDRESS,
    "DOC_Q": DOC_Q,
    "CURSOR": CURSOR,
}


def _delegate(index: int) -> Scenario:
    name, call, args, kwargs = DELEGATES[index]

    def run(S: types.ModuleType) -> None:
        expected = tuple(NAMED.get(a, a) if type(a) is str else a for a in args)
        for facade in ("default", "reading"):
            with Rig(S) as rig:
                target = (
                    rig.guard
                    if facade == "default"
                    else rig.guard.reading(S.CancelLatch().view())
                )
                result = call(target)
                assert len(rig.store.calls) == 1
                called, got_args, got_kwargs = rig.store.calls[0]
                assert called == name
                assert got_args == expected and got_kwargs == kwargs
                for got, want in zip(got_args, expected, strict=True):
                    assert got is want or type(want) in (int, str, type(None))
                # The real backend result is returned unchanged (same object).
                assert rig.store.results == [(name, result)]
                assert rig.store.results[0][1] is result
                assert rig.owner.failure is None and not rig.reports
                if name == "close":
                    assert result is None
                    assert rig.owner.end == S.SessionEnd("closed")
                    with pytest.raises(StorageClosedError):
                        rig.inner.conversation(1)
                else:
                    assert rig.owner.end is None and not rig.owner.sealed
                    # Every delegate here reads or writes a real seeded value.
                    assert result is not None
                    assert getattr(result, "items", (result,)) != ()

    return run


for _index, _case in enumerate(DELEGATES):
    scenario(f"delegate[{_index:02}-{_case[0]}]")(_delegate(_index))


@scenario("delegate-commit-and-mint-real-values")
def _commit_and_mint(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        batch = note(30)
        assert rig.guard.mint_id() == 9
        assert rig.store.calls[-1] == ("mint_id", (), {})
        assert rig.guard.commit(batch) == 3
        committed: Any = rig.store.calls[-1]
        assert committed[0] == "commit" and committed[1][0] is batch
        assert committed[1:] == ((batch,), {})
        assert rig.inner.entry(30).commit_seq == 3
        page = rig.guard.scan_entries(ENTRY_Q, 1)
        assert [item.id for item in page.items] == [30]
        assert page.next == Cursor(30, "descending")


# Item 2: 13 read roles ----------------------------------------------------------

READS: dict[str, Callable[[Any], Any]] = {
    "conversation": lambda g: g.conversation(1),
    "scan_conversations": lambda g: g.scan_conversations(CONV_Q, 2),
    "entry": lambda g: g.entry(3, conversation_id=8),
    "find_latest_head_marker": lambda g: g.find_latest_head_marker(1),
    "scan_entries": lambda g: g.scan_entries(ENTRY_Q, 2),
    "task": lambda g: g.task(5),
    "scan_tasks": lambda g: g.scan_tasks(TASK_Q, 2),
    "submission": lambda g: g.submission(6),
    "scan_submissions": lambda g: g.scan_submissions(SUB_Q, 2),
    "submission_by_request": lambda g: g.submission_by_request(1, "req"),
    "find_document": lambda g: g.find_document(ADDRESS),
    "document": lambda g: g.document(7),
    "scan_documents": lambda g: g.scan_documents(DOC_Q, 2),
}
assert len(READS) == 13


def _healthy_after(rig: Rig) -> None:
    assert rig.owner.failure is None and not rig.owner.sealed
    assert rig.guard.commit(note(40)) == 3
    assert rig.owner.end is None and not rig.reports


def _read_request_error(role: str) -> Scenario:
    def run(S: types.ModuleType) -> None:
        with Rig(S) as rig:
            error = StorageRequestError("bad request")
            rig.store.fault(role, error)
            with pytest.raises(StorageRequestError) as raised:
                READS[role](rig.guard)
            assert raised.value is error
            _healthy_after(rig)

    return run


def _read_cancelled(role: str) -> Scenario:
    def run(S: types.ModuleType) -> None:
        with Rig(S) as rig:
            latch = S.CancelLatch()
            error = RuntimeError("caller gave up")

            def cancel_then_raise() -> None:
                latch.cancel()
                raise error

            rig.store.fault(role, cancel_then_raise)
            with pytest.raises(RuntimeError) as raised:
                READS[role](rig.guard.reading(latch.view()))
            assert raised.value is error
            _healthy_after(rig)

    return run


def _read_ordinary(role: str) -> Scenario:
    def run(S: types.ModuleType) -> None:
        with Rig(S) as rig:
            other = S.CancelLatch()
            other.cancel()  # an unrelated cancelled latch never exempts this read
            error = RuntimeError("disk gone")
            rig.store.fault(role, error)
            with pytest.raises(RuntimeError) as raised:
                READS[role](rig.guard)
            assert raised.value is error
            assert rig.owner.failure is error and rig.owner.sealed
            with pytest.raises(S.SessionFailed) as later:
                rig.guard.conversation(1)
            assert later.value.cause is error and later.value.__cause__ is error
            assert rig.owner.end == S.SessionEnd("failed", error)
            assert rig.store.count("close") == 1
            assert rig.reported() == [error]

    return run


def _read_cancelled_success(role: str) -> Scenario:
    def run(S: types.ModuleType) -> None:
        with Rig(S) as rig:
            latch = S.CancelLatch()
            latch.cancel()
            result = READS[role](rig.guard.reading(latch.view()))
            assert rig.store.results[-1][1] is result
            _healthy_after(rig)

    return run


for _role in READS:
    scenario(f"read-request-error-exempt[{_role}]")(_read_request_error(_role))
    scenario(f"read-cancelled-exempt[{_role}]")(_read_cancelled(_role))
    scenario(f"read-ordinary-poisons[{_role}]")(_read_ordinary(_role))
    scenario(f"read-cancelled-success-returned[{_role}]")(
        _read_cancelled_success(_role)
    )


def _mutation_never_exempt(method: str, cancelled: bool) -> Scenario:
    def run(S: types.ModuleType) -> None:
        with Rig(S) as rig:
            latch = S.CancelLatch()
            if cancelled:
                error: BaseException = RuntimeError("cancelled-shaped")

                def cancel_then_raise() -> None:
                    latch.cancel()
                    raise error

                rig.store.fault(method, cancel_then_raise)
            else:
                error = StorageRequestError("bad batch")
                rig.store.fault(method, error)
            facade = rig.guard.reading(latch.view())
            call = (
                (lambda: facade.commit(note(40)))
                if method == "commit"
                else facade.mint_id
            )
            with pytest.raises(type(error)) as raised:
                call()
            assert raised.value is error
            assert rig.owner.failure is error
            assert rig.owner.end == S.SessionEnd("failed", error)
            assert rig.reported() == [error]

    return run


for _method in ("commit", "mint_id"):
    for _cancelled in (False, True):
        _kind = "cancelled" if _cancelled else "request-error"
        scenario(f"mutation-never-exempt[{_method}-{_kind}]")(
            _mutation_never_exempt(_method, _cancelled)
        )


# Item 3: Pi 132 -----------------------------------------------------------------


def _pi132(failing: str, late: str) -> Scenario:
    def run(S: types.ModuleType) -> None:
        with Rig(S) as rig:
            gate = rig.store.hold("scan_entries", lambda q, limit, cursor: limit > 1)
            reader = rig.start("reader", lambda: rig.guard.scan_entries(ENTRY_Q, 10))
            assert gate.entered.wait(WAIT)
            error = RuntimeError("disk gone")
            rig.store.fault(failing, error)
            call = (
                (lambda: rig.guard.commit(note(40)))
                if failing == "commit"
                else rig.guard.mint_id
            )
            with pytest.raises(RuntimeError) as raised:
                call()
            assert raised.value is error
            assert rig.owner.failure is error and rig.owner.sealed
            assert rig.reported() == [error]
            before = len(rig.store.calls)
            with pytest.raises(S.SessionFailed) as refused:
                rig.guard.task(5)
            assert refused.value.cause is error
            assert len(rig.store.calls) == before  # refused before the backend
            # Backend close waits for the read underway.
            assert rig.store.count("close") == 0 and rig.owner.end is None
            assert not reader.done.is_set()
            if late == "error":
                rig.store.fault("scan_entries", RuntimeError("late read error"))
            gate.release.set()
            failed = reader.raised()
            assert type(failed) is S.SessionFailed and failed.cause is error
            assert gate.completed.is_set()
            if late == "success":
                assert rig.store.results[-1][0] == "close"
                assert any(name == "scan_entries" for name, _ in rig.store.results)
            assert rig.store.count("close") == 1
            assert rig.owner.end == S.SessionEnd("failed", error)
            assert rig.owner.failure is error and rig.reported() == [error]
            rig.owner.close(timeout=WAIT)  # shares the settled outcome
            assert rig.store.count("close") == 1

    return run


for _failing in ("mint_id", "commit"):
    for _late in ("success", "error"):
        scenario(f"pi132-underway-ends-failed[{_failing}-{_late}]")(
            _pi132(_failing, _late)
        )


# Item 4: Pi 156 -----------------------------------------------------------------


@scenario("pi156-paged-read-cannot-start-next-page")
def _pi156(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        condition = rig.witness()
        state_at_close: list[bool] = []

        def check_state() -> None:
            state_at_close.append(rig.inner._state is rig.state)
            assert rig.inner.conversation(1) is not None
            assert rig.inner.document(7).to_tree()["value"] == {"x": [1]}

        rig.store.before_close = check_state
        gate = rig.store.hold("scan_entries", lambda q, limit, cursor: cursor is None)

        def read_pages() -> tuple[Any, BaseException | None]:
            first = rig.guard.scan_entries(ENTRY_Q, 1)
            try:
                rig.guard.scan_entries(ENTRY_Q, 1, first.next)
            except BaseException as error:  # noqa: BLE001 - asserted below
                return first, error
            return first, None

        reader = rig.start("pager", read_pages)
        assert gate.entered.wait(WAIT)
        closer = rig.start("closer", lambda: rig.owner.close(timeout=WAIT))
        assert condition.waiting.wait(WAIT)  # close is blocked on the drain
        assert rig.owner.sealed and rig.store.count("close") == 0
        assert not closer.done.is_set()
        gate.release.set()
        first, refused = reader.value()
        assert [item.id for item in first.items] == [4]
        assert first.next == Cursor(4, "descending")
        assert type(refused) is S.SessionClosedError
        assert str(refused) == "Session is closed"
        assert closer.value() is None
        scans = [args for name, args, _ in rig.store.calls if name == "scan_entries"]
        assert scans == [(ENTRY_Q, 1, None)]  # the next page never reached it
        assert rig.store.names()[-2:] == ["scan_entries", "close"]
        assert state_at_close == [True]
        assert rig.owner.end == S.SessionEnd("closed") and rig.owner.failure is None
        assert not rig.reports


# Item 5: Pi 192 and a deterministic two-failure race ------------------------------


@scenario("pi192-first-failure-wins-then-close-throws")
def _pi192_race(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        first_gate = rig.store.hold("mint_id")
        second_gate = rig.store.hold("task")
        first_error, second_error = RuntimeError("first"), RuntimeError("second")
        close_error = RuntimeError("close failed")
        rig.store.fault("mint_id", first_error)
        rig.store.fault("task", second_error)
        minting = rig.start("mint", rig.guard.mint_id)
        reading = rig.start("task", lambda: rig.guard.task(5))
        assert first_gate.entered.wait(WAIT) and second_gate.entered.wait(WAIT)
        first_gate.release.set()
        assert minting.raised() is first_error
        assert rig.owner.failure is first_error
        rig.store.fault("close", close_error)
        second_gate.release.set()
        late = reading.raised()
        assert type(late) is S.SessionFailed and late.cause is first_error
        assert rig.store.count("close") == 1
        assert rig.owner.end == S.SessionEnd("failed", first_error)
        assert rig.owner.failure is first_error
        assert rig.reported() == [first_error]
        # The close error still reaches closers; the cause is unchanged.
        with pytest.raises(RuntimeError) as closing:
            rig.owner.close(timeout=WAIT)
        assert closing.value is close_error
        assert rig.store.count("close") == 1 and rig.reported() == [first_error]


@scenario("pi192-healthy-close-failure-becomes-cause")
def _pi192_healthy(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        close_error = RuntimeError("close failed")
        rig.store.fault("close", close_error)
        with pytest.raises(RuntimeError) as closing:
            rig.owner.close(timeout=WAIT)
        assert closing.value is close_error
        assert rig.owner.end == S.SessionEnd("failed", close_error)
        assert rig.owner.failure is close_error
        assert rig.reported() == [close_error]
        with pytest.raises(S.SessionFailed) as refused:
            rig.guard.conversation(1)
        assert refused.value.cause is close_error
        with pytest.raises(RuntimeError) as again:
            rig.guard.close()
        assert again.value is close_error
        assert rig.store.count("close") == 1 and rig.reported() == [close_error]


@scenario("pi192-external-fail-twice-keeps-first")
def _fail_twice(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        gate = rig.store.hold("conversation")
        reader = rig.start("reader", lambda: rig.guard.conversation(1))
        assert gate.entered.wait(WAIT)
        first, second = RuntimeError("adopt failed"), RuntimeError("later bug")
        rig.owner.fail(first)  # never waits for the read underway
        rig.owner.fail(second)
        assert rig.owner.failure is first and rig.reported() == [first]
        assert rig.store.count("close") == 0
        close_error = RuntimeError("close failed")
        rig.store.fault("close", close_error)
        gate.release.set()
        assert reader.raised().cause is first
        assert rig.owner.end == S.SessionEnd("failed", first)
        assert rig.reported() == [first]


# Item 6: concurrent closers and cancelled waits -------------------------------------


@scenario("closers-share-one-backend-close-and-cancel-one-wait")
def _closers(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        close_gate = rig.store.hold("close")
        first = rig.start("closer-a", lambda: rig.owner.close(timeout=WAIT))
        assert close_gate.entered.wait(WAIT)
        condition = rig.witness()
        latch = S.CancelLatch()
        cancelled = rig.start(
            "closer-b", lambda: rig.owner.close(cancel=latch.view(), timeout=WAIT)
        )
        assert condition.waiting.wait(WAIT)
        condition.waiting.clear()
        patient = rig.start("closer-c", lambda: rig.owner.close(timeout=WAIT))
        assert condition.waiting.wait(WAIT)
        latch.cancel()
        assert type(cancelled.raised()) is S.WaitCancelled
        # Cancelling one waiter undoes nothing: seal, close and other waiters stay.
        assert not patient.done.is_set() and not first.done.is_set()
        assert rig.owner.sealed and rig.owner.end is None
        before = len(rig.store.calls)
        with pytest.raises(S.SessionClosedError):
            rig.guard.conversation(1)
        assert len(rig.store.calls) == before and rig.store.count("close") == 1
        close_gate.release.set()
        assert first.value() is None and patient.value() is None
        assert rig.store.count("close") == 1
        assert rig.owner.end == S.SessionEnd("closed")
        rig.owner.close(timeout=WAIT)
        rig.guard.close()
        assert rig.store.count("close") == 1 and not rig.reports


@scenario("cancelled-close-requester-keeps-seal-and-drain")
def _cancelled_requester(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        gate = rig.store.hold("scan_documents")
        reader = rig.start("reader", lambda: rig.guard.scan_documents(DOC_Q, 3))
        assert gate.entered.wait(WAIT)
        condition = rig.witness()
        latch = S.CancelLatch()
        closer = rig.start(
            "closer", lambda: rig.owner.close(cancel=latch.view(), timeout=WAIT)
        )
        assert condition.waiting.wait(WAIT)
        latch.cancel()
        assert type(closer.raised()) is S.WaitCancelled
        with pytest.raises(TimeoutError):
            rig.owner.close(timeout=0)
        assert rig.owner.sealed and rig.store.count("close") == 0
        with pytest.raises(S.SessionClosedError):
            rig.guard.mint_id()
        gate.release.set()
        page = reader.value()  # healthy close: the drained read still succeeds
        assert [item.id for item in page.items] == [7]
        assert rig.store.count("close") == 1
        assert rig.owner.wait_closed(timeout=WAIT) == S.SessionEnd("closed")


@scenario("closer-synchronous-backend-throw-shared")
def _closer_throw(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        close_gate = rig.store.hold("close")
        close_error = RuntimeError("close failed")
        rig.store.fault("close", close_error)
        first = rig.start("closer-a", lambda: rig.owner.close(timeout=WAIT))
        assert close_gate.entered.wait(WAIT)
        condition = rig.witness()
        second = rig.start("closer-b", lambda: rig.owner.close(timeout=WAIT))
        assert condition.waiting.wait(WAIT)
        close_gate.release.set()
        assert first.raised() is close_error and second.raised() is close_error
        assert rig.store.count("close") == 1
        assert rig.reported() == [close_error]


# Item 7: Pi 267/291/301/410, reporter, BaseException, a2b boundaries ---------------


@scenario("pi267-failed-mint")
def _pi267(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        error = RuntimeError("disk gone")
        rig.store.fault("mint_id", error)
        with pytest.raises(RuntimeError) as raised:
            rig.guard.mint_id()
        assert raised.value is error
        assert rig.owner.wait_closed(timeout=WAIT) == S.SessionEnd("failed", error)
        rig.owner.close(timeout=WAIT)


@scenario("pi291-request-error-from-commit")
def _pi291(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        error = StorageRequestError("bad batch")
        rig.store.fault("commit", error)
        with pytest.raises(StorageRequestError) as raised:
            rig.guard.commit(note(40))
        assert raised.value is error
        assert rig.owner.wait_closed(timeout=WAIT) == S.SessionEnd("failed", error)


@scenario("pi301-invalid-requests-fail-only-the-call")
def _pi301(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        ascending = EntryQuery({"conversationId": 1, "order": "ascending"})
        descending = EntryQuery({"conversationId": 1, "order": "descending"})
        page = rig.guard.scan_entries(ascending, 1)
        assert page.next == Cursor(2, "ascending")
        requests: list[Callable[[], Any]] = [
            lambda: rig.guard.scan_entries(descending, 10, page.next),  # wrong order
            lambda: rig.guard.scan_entries(EntryQuery({"conversationId": 999}), 10),
            lambda: rig.guard.document(7, 1),  # a2b numeric history unsupported
            lambda: rig.guard.find_document(ADDRESS, 1),
            lambda: rig.guard.scan_documents(
                DocumentQuery({"scope": SESSION, "at": 1}), 1
            ),
        ]
        for request in requests:
            with pytest.raises(StorageRequestError):
                request()
        with pytest.raises(UnsupportedStorageOperation):
            rig.guard.document(7, 1)
        assert rig.guard.commit(note(40)) == 3
        assert rig.owner.failure is None and rig.owner.end is None
        assert not rig.reports


@scenario("pi410-cancelled-read-real-backend")
def _pi410(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        latch = S.CancelLatch()
        cancelled = RuntimeError("caller gave up")

        def cancel_then_raise() -> None:
            latch.cancel()
            raise cancelled

        rig.store.fault("scan_entries", cancel_then_raise)
        with pytest.raises(RuntimeError) as raised:
            rig.guard.reading(latch.view()).scan_entries(ENTRY_Q, 50)
        assert raised.value is cancelled
        assert rig.guard.commit(note(40)) == 3
        assert rig.owner.failure is None and not rig.reports


@scenario("reporter-runs-outside-owner-lock")
def _reporter(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        seen: list[Any] = []

        def hook(error: BaseException) -> None:
            # Each property takes the owner lock: a held lock would deadlock here.
            seen.append((rig.owner.failure is error, rig.owner.sealed, rig.owner.end))
            try:
                rig.guard.conversation(1)
            except BaseException as refused:  # noqa: BLE001 - asserted below
                seen.append(refused)
            rig.owner.close(timeout=WAIT)  # may close; never waits on its own call
            seen.append(rig.owner.end)
            raise RuntimeError("reporter bug is contained")

        rig.report_hook = hook
        error = RuntimeError("disk gone")
        rig.store.fault("task", error)
        failing = rig.start("failing", lambda: rig.guard.task(5))
        assert failing.raised() is error
        assert seen[0] == (True, True, None)
        assert type(seen[1]) is S.SessionFailed and seen[1].cause is error
        assert seen[2] == S.SessionEnd("failed", error)
        assert rig.reports[0][2] == "de02b1a-failing" and len(rig.reports) == 1
        assert rig.reports[0][1] == "session_failed"
        assert rig.store.count("close") == 1


@scenario("reporter-process-control-propagates-after-close")
def _reporter_base(S: types.ModuleType) -> None:
    with Rig(S) as rig:

        def hook(error: BaseException) -> None:
            raise KeyboardInterrupt

        rig.report_hook = hook
        error = RuntimeError("disk gone")
        with pytest.raises(KeyboardInterrupt):
            rig.owner.fail(error)
        assert rig.owner.failure is error
        assert rig.owner.end == S.SessionEnd("failed", error)
        assert rig.store.count("close") == 1


@scenario("base-exception-latches-and-reraises-unchanged")
def _base_exception(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        interrupt = KeyboardInterrupt()
        rig.store.fault("conversation", interrupt)
        latch = S.CancelLatch()
        latch.cancel()  # a cancelled reader never exempts process control
        with pytest.raises(KeyboardInterrupt) as raised:
            rig.guard.reading(latch.view()).conversation(1)
        assert raised.value is interrupt
        assert rig.owner.failure is interrupt
        assert rig.store.count("close") == 1  # retirement ran in finally
        assert rig.owner.end == S.SessionEnd("failed", interrupt)
        assert rig.reported() == [interrupt]


@scenario("base-exception-after-failure-keeps-cause")
def _base_exception_late(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        gate = rig.store.hold("submission")
        reader = rig.start("reader", lambda: rig.guard.submission(6))
        assert gate.entered.wait(WAIT)
        cause = RuntimeError("first")
        rig.owner.fail(cause)
        exit_request = SystemExit(3)
        rig.store.fault("submission", exit_request)
        gate.release.set()
        assert reader.raised() is exit_request
        assert rig.owner.failure is cause and rig.reported() == [cause]
        assert rig.owner.end == S.SessionEnd("failed", cause)


@scenario("a2b-foreign-point-passes-unchanged-without-hooks")
def _foreign_point(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        hooks: list[str] = []

        class Foreign:
            def __eq__(self, other: object) -> bool:
                hooks.append("eq")
                return True

            def __ne__(self, other: object) -> bool:
                hooks.append("ne")
                return False

            def __hash__(self) -> int:
                hooks.append("hash")
                return 0

            def __repr__(self) -> str:
                hooks.append("repr")
                return "current"

        point = Foreign()
        with pytest.raises(RecordError) as raised:
            rig.guard.document(7, point)
        assert rig.store.calls[0] == ("document", (7, point), {})
        assert rig.store.calls[0][1][1] is point and hooks == []
        # No error normalisation: a raw RecordError is not a request error.
        assert rig.owner.failure is raised.value
        assert rig.owner.end == S.SessionEnd("failed", raised.value)
        with pytest.raises(StorageClosedError):
            rig.inner.document(7)  # Memory close linearization unchanged


@scenario("closed-guard-refuses-before-backend-memory-closed")
def _closed_refusal(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        rig.guard.close()
        before = len(rig.store.calls)
        for read in READS.values():
            with pytest.raises(S.SessionClosedError):
                read(rig.guard)
        with pytest.raises(S.SessionClosedError):
            rig.guard.commit(note(40))
        with pytest.raises(S.SessionClosedError):
            rig.guard.mint_id()
        assert len(rig.store.calls) == before
        with pytest.raises(StorageClosedError):
            rig.inner.mint_id()
        assert rig.owner.end == S.SessionEnd("closed")


# Repair r1: final classification and unregistration are one step --------------


class RaceCondition(threading.Condition):
    """Test-only: run ``action`` once at the target thread's first owner-lock
    acquire (before it) or release (after it) once ``armed``; never product API."""

    def __init__(
        self, target: str, armed: threading.Event, at: str, action: Callable[[], None]
    ) -> None:
        super().__init__(threading.Lock())
        self.target, self.armed, self.at, self.action = target, armed, at, action
        self.fired = threading.Event()

    def _maybe(self, point: str) -> None:
        if (
            point == self.at
            and self.armed.is_set()
            and not self.fired.is_set()
            and threading.current_thread().name == self.target
        ):
            self.fired.set()
            self.action()

    def __enter__(self) -> bool:
        self._maybe("acquire")
        return super().__enter__()

    def __exit__(self, *exc: Any) -> None:
        super().__exit__(*exc)
        self._maybe("release")


def _settle_race(at: str, path: str) -> Scenario:
    def run(S: types.ModuleType) -> None:
        with Rig(S) as rig:
            cause = RuntimeError("failed elsewhere")
            registered: list[bool] = []
            rig.report_hook = lambda error: registered.append(rig.owner._underway > 0)
            gate = rig.store.hold("task")
            gate.release.set()  # pass through; ``completed`` arms the race
            if path == "exempt-error":
                rig.store.fault("task", StorageRequestError("bad request"))

            def fail_elsewhere() -> None:
                rig.start("failer", lambda: rig.owner.fail(cause)).value()

            race = RaceCondition("de02b1a-target", gate.completed, at, fail_elsewhere)
            rig.owner._cond = race
            target = rig.start("target", lambda: rig.guard.task(5)).join()
            assert race.fired.is_set() and registered and len(registered) == 1
            outcome: Any = target.error if target.error is not None else target.result
            failed = type(outcome) is S.SessionFailed
            # Pi: a call still registered when the owner fails ends SessionFailed.
            assert failed == registered[0], (registered, outcome)
            if at == "acquire":  # failure latched before the single settle step
                assert registered == [True] and failed and outcome.cause is cause
            else:  # failure latched after it: the call had already retired
                assert registered == [False] and not failed
                if path == "success":
                    assert outcome is rig.store.results[0][1]
                else:
                    assert type(outcome) is StorageRequestError
            assert rig.owner.failure is cause and rig.reported() == [cause]
            assert rig.owner.wait_closed(timeout=WAIT) == S.SessionEnd("failed", cause)
            assert rig.store.count("close") == 1

    return run


for _at in ("acquire", "release"):
    for _path in ("success", "exempt-error"):
        scenario(f"settle-atomic-race[{_at}-{_path}]")(_settle_race(_at, _path))


# Repair r1: cleanup never replaces an in-flight exception ------------------------


@scenario("close-error-never-replaces-inflight-base-exception")
def _close_error_inflight(S: types.ModuleType) -> None:
    with Rig(S) as rig:
        interrupt, exit_request = KeyboardInterrupt(), SystemExit(4)
        rig.store.fault("mint_id", interrupt)
        rig.store.fault("close", exit_request)
        with pytest.raises(KeyboardInterrupt) as raised:
            rig.guard.mint_id()  # last retirer: its close raises SystemExit
        assert raised.value is interrupt
        assert rig.store.count("close") == 1
        assert rig.owner.failure is interrupt and rig.reported() == [interrupt]
        assert rig.owner.end == S.SessionEnd("failed", interrupt)
        for _ in range(2):  # every closer sees the recorded close error
            with pytest.raises(SystemExit) as closing:
                rig.owner.close(timeout=WAIT)
            assert closing.value is exit_request
        assert rig.store.count("close") == 1 and rig.reported() == [interrupt]


@scenario("close-process-control-raised-when-nothing-else-raises")
def _close_error_claimers(S: types.ModuleType) -> None:
    with Rig(S) as rig:  # fail() claims close with nothing else to raise
        cause, exit_request = RuntimeError("adopt failed"), SystemExit(5)
        rig.store.fault("close", exit_request)
        with pytest.raises(SystemExit) as raised:
            rig.owner.fail(cause)
        assert raised.value is exit_request
        assert rig.owner.failure is cause and rig.reported() == [cause]
        with pytest.raises(SystemExit) as closing:
            rig.owner.close(timeout=WAIT)
        assert closing.value is exit_request
    with Rig(S) as rig:  # a successful read retiring last claims the close
        exit_request = SystemExit(6)
        gate = rig.store.hold("conversation")
        reader = rig.start("reader", lambda: rig.guard.conversation(1))
        assert gate.entered.wait(WAIT)
        rig.owner.request_close()
        rig.store.fault("close", exit_request)
        gate.release.set()
        assert reader.raised() is exit_request
        assert rig.owner.failure is exit_request
        assert rig.reported() == [exit_request]
        with pytest.raises(SystemExit) as closing:
            rig.owner.close(timeout=WAIT)
        assert closing.value is exit_request


# -- pytest entry points -------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario(name: str) -> None:
    SCENARIOS[name](real_session)
    assert not [
        t.name
        for t in threading.enumerate()
        if t.name.startswith("de02b1a-") and t.is_alive()
    ]


def _as_storage(guard: real_session.GuardedStorage) -> Storage:
    return guard  # static Protocol conformance (mypy)


def test_static_and_signature_conformance_of_both_facades() -> None:
    owner = real_session.SessionLifecycle(MemoryStorage())
    facades = [owner.storage, owner.storage.reading(real_session.CancelLatch().view())]
    names = [
        name
        for name, value in vars(Storage).items()
        if callable(value) and not name.startswith("_")
    ]
    assert len(names) == 16
    for facade in facades:
        _as_storage(facade)
        assert type(facade) is real_session.GuardedStorage
        for name in names:
            assert inspect.signature(getattr(Storage, name)) == inspect.signature(
                getattr(real_session.GuardedStorage, name)
            ), name
    assert "SessionLifecycle" not in vars(sys.modules["pipy_harness.native.durable"])
    owner.close()


def test_owner_rejects_untrusted_views_and_timeouts() -> None:
    owner = real_session.SessionLifecycle(MemoryStorage())

    class Lookalike:
        cancelled = True

    with pytest.raises(TypeError):
        owner.storage.reading(Lookalike())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        real_session.CancelView(Lookalike())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        owner.close(cancel=Lookalike())  # type: ignore[arg-type]
    for bad in (-1, float("nan"), float("inf"), True, "1"):
        with pytest.raises(ValueError):
            owner.close(timeout=bad)  # type: ignore[arg-type]
    view = real_session.CancelLatch().view()
    with pytest.raises(AttributeError):
        view._latch = real_session.CancelLatch()  # type: ignore[misc]
    with pytest.raises(TypeError):
        real_session.GuardedStorage(object(), None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        real_session.GuardedStorage(owner, Lookalike())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        owner.fail("not an exception")  # type: ignore[arg-type]
    assert not owner.sealed and owner.failure is None  # invalid input sealed nothing
    owner.close()
    assert owner.end == real_session.SessionEnd("closed")


# Item 8: private negative controls ----------------------------------------------------

SOURCE = Path(real_session.__file__).read_text()

MUTANTS: dict[str, list[tuple[str, str]]] = {
    "read-role-exemption": [
        (
            "            read\n            and issubclass(type(error), Exception)\n",
            "            False\n            and issubclass(type(error), Exception)\n",
        )
    ],
    "post-call-health-check": [
        ("            if settled is not None:\n", "            if False:\n")
    ],
    "late-error-health-check": [
        (
            "                if late is not None and issubclass(type(error), Exception):\n",
            "                if False:\n",
        )
    ],
    "admission-seal": [
        ("        if self._sealed:\n            return None, True\n", "")
    ],
    "registration": [
        (
            "        self._underway += 1\n        return None, False\n",
            "        return None, False\n",
        ),
        (
            "read its health.\n        self._underway -= 1\n",
            "read its health.\n",
        ),
        (
            "classify it.\n        self._underway -= 1\n",
            "classify it.\n",
        ),
    ],
    "split-success-settle": [
        (
            "                registered = False\n"
            "                settled = self._settle()\n",
            "                settled = self._failure\n"
            "            with self._cond:\n"
            "                registered = False\n"
            "                self._underway -= 1\n",
        )
    ],
    "split-error-settle": [
        (
            "                    registered = False\n"
            "                    late, first = self._settle_error(error, exempt)\n",
            "                    late, first = self._classify_error(error, exempt)\n"
            "                with self._cond:\n"
            "                    registered = False\n"
            "                    self._underway -= 1\n",
        )
    ],
    "close-error-replaces-inflight": [
        (
            "if pending is not None and not raising:",
            "if pending is not None:",
        )
    ],
    "drain-wait": [
        (
            "claim = self._close_state is _REQUESTED and self._underway == 0",
            "claim = self._close_state is _REQUESTED",
        )
    ],
    "first-cause-only": [
        ("        if self._failure is not None:\n            return False\n", "")
    ],
    "close-once": [
        (
            "claim = self._close_state is _REQUESTED and self._underway == 0",
            "claim = self._close_state is not _IDLE and self._underway == 0",
        )
    ],
}

# Each private mutant must make at least these reached guards fail.
EXPECTED_FAILURES: dict[str, list[str]] = {
    "read-role-exemption": [
        "read-request-error-exempt[scan_entries]",
        "read-cancelled-exempt[document]",
        "pi301-invalid-requests-fail-only-the-call",
        "pi410-cancelled-read-real-backend",
    ],
    "post-call-health-check": [
        "pi132-underway-ends-failed[mint_id-success]",
        "pi132-underway-ends-failed[commit-success]",
    ],
    "late-error-health-check": [
        "pi132-underway-ends-failed[mint_id-error]",
        "pi192-first-failure-wins-then-close-throws",
    ],
    "admission-seal": [
        "pi156-paged-read-cannot-start-next-page",
        "closed-guard-refuses-before-backend-memory-closed",
    ],
    "registration": [
        "pi132-underway-ends-failed[mint_id-success]",
        "pi156-paged-read-cannot-start-next-page",
    ],
    "split-success-settle": ["settle-atomic-race[release-success]"],
    "split-error-settle": ["settle-atomic-race[release-exempt-error]"],
    "close-error-replaces-inflight": [
        "close-error-never-replaces-inflight-base-exception"
    ],
    "drain-wait": [
        "pi132-underway-ends-failed[commit-success]",
        "cancelled-close-requester-keeps-seal-and-drain",
    ],
    "first-cause-only": [
        "pi192-external-fail-twice-keeps-first",
        "pi192-first-failure-wins-then-close-throws",
    ],
    "close-once": [
        "closers-share-one-backend-close-and-cancel-one-wait",
        "pi192-healthy-close-failure-becomes-cause",
    ],
}


def mutant(name: str) -> types.ModuleType:
    source = SOURCE
    for old, new in MUTANTS[name]:
        # Exactly once, and starting at a line boundary or after a unique prefix.
        assert source.count(old) == 1, (name, old)
        assert source.count("\n" + old) <= 1, (name, old)
        source = source.replace(old, new)
    module_name = "pipy_harness.native.durable._de02b1a_mutant_" + name.replace(
        "-", "_"
    )
    module = types.ModuleType(module_name)
    module.__package__ = "pipy_harness.native.durable"
    module.__file__ = real_session.__file__
    sys.modules[module_name] = module
    try:
        exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)
    finally:
        del sys.modules[module_name]
    return module


def run_scenario(name: str, module: types.ModuleType) -> BaseException | None:
    try:
        SCENARIOS[name](module)
    # Scenarios inject process-control exceptions; a mutant may leak one.
    except (Exception, pytest.fail.Exception, SystemExit, KeyboardInterrupt) as error:  # noqa: BLE001 - recorded
        return error
    return None


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_private_negative_control_fails_reached_guard(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Mutants may leave a bounded wait unsatisfied; keep that bound short.
    monkeypatch.setattr(sys.modules[__name__], "WAIT", 1.0)
    module = mutant(name)
    for expected in EXPECTED_FAILURES[name]:
        assert run_scenario(expected, real_session) is None, expected
        assert run_scenario(expected, module) is not None, (name, expected)
    assert not [
        t.name
        for t in threading.enumerate()
        if t.name.startswith("de02b1a-") and t.is_alive()
    ]

"""Private first internal Session over the DE02b1a owner and the DE02b2a core (DE02b2b).

``Session`` runs one atomic transaction at a time on the owner's FIFO line,
through a sync ``commit()`` and an async ``acommit()`` that drive the one
sans-IO ``_TxCore``; it publishes committed changes, serves raw committed
snapshots and closes behind accepted work. It is not exported from the package
and has no product, factory or SDK integration.

Semantics adapted from Pi durable 1.3.0 ``packages/durable/src/session/session.ts``
(``commitWith``, ``#runCommit``, ``#publish``, ``readOnLine``, ``snapshot``,
``close``, ``#notifyClose``, ``subscribeCommits``, ``observeCommits``,
``subscribeClose``, ``contained``, ``assertUsable``) and ``src/types.ts``
(``Session``) at 2ec55d808d455b1a64a2bf7888198207a096120a, MIT Copyright (c)
2025 Mario Zechner; notice: tests/fixtures/durable/UPSTREAM-LICENSE.txt.

Python spelling, documented decisions:

* Settlement order is Pi's ``#runCommit``: at the grant, health then the
  cancel view; the callback; health again inside the callback ``try``; on
  error, seal, abort, drain pending operations and re-raise the callback error;
  on success, seal, then ``PendingTxOperations`` (drained) or prepare and
  assemble; an empty batch discards; the guarded commit uses no cancel view and
  a failure there discards (the guard failed the owner); an adoption failure
  fails the owner and rejects the durable commit; then publication. The line is
  left only after all of it, drains included.
* ``commit()`` runs the callback and settlement inline on the calling thread;
  a callback returning an awaitable is a callback failure (``TypeError``; a
  never-started coroutine is closed). Every sync entry that may wait for the
  line (``commit``, ``read_on_line``, a snapshot miss, ``close``,
  ``wait_closed``) refuses to run on a thread with a running event loop.
* ``acommit()`` binds the transaction to the running loop. ``AsyncTx``
  operations run their body eagerly up to the first backend call (as a JS
  async function does), send each guarded backend call to the loop's default
  executor, and continue on the loop; an in-flight memoized call is awaited,
  never re-run. Handles are ``TxOp`` awaitables whose ``await`` is shielded,
  so a cancelled awaiter never cancels the owned operation. The callback runs
  in the caller's task: a ``Task.cancel`` reaching it is the callback's own
  outcome. Once the callback settles, settlement is a shielded owned task:
  caller cancellation cannot cut it, and the waiter alone gets
  ``CancelledError``. A process-control exception after backend admission
  fails the owner (the outcome is unknown).
* Cancellation cuts: a ``CancelView`` is checked only when the ticket is
  granted (``CommitCancelled``; Pi checks the signal at job start and never
  unqueues). ``Task.cancel`` of a queued async waiter unqueues it at once; a
  granted-but-unused ticket is passed on.
* Reentry is detected by holder identity, never with an ``RLock``: the
  holder thread (sync) or the holder's caller, settlement and scoped driving tasks (async)
  get ``ReentrantSessionCall`` from every entry that would queue behind itself.
  A task spawned by the callback queues normally.
* Publication is built after adoption, before the line is left, only when a
  listener exists. Table changes are the committed ``StorageWrite`` objects;
  document changes carry the adopted ``OwnedJson`` shared with snapshots.
  Internal listeners run first and an ``Exception`` fails the owner while the
  durable commit still resolves; host commit and close listeners are contained
  and reported as ``"listener_error"``. A listener returning a Future is
  reported through a done-callback; a returned coroutine is closed and treated
  as a ``TypeError``. A process-control exception from a listener fails the
  owner and propagates. Listener registries compare callables by identity only.
* Every cache write (cold load, adoption, explicit unload, close clear) runs while the line is
  held or after it drained; off-line snapshot hits see the old or the new
  committed incarnation, never a candidate.
* No caller object is released under the owner lock: listener registries are
  detached under it and dropped after unlocking, so a callable's ``__del__``
  never runs there. The cache lock only ever drops owned committed values.
* A backend close claimed on an event-loop thread (``aclose``, or a settlement
  leaving the line) runs as owned executor work, never on the loop, and nothing
  waits for it but closers. A process-control exception escaping that
  background work (a reporter's) goes to the loop's exception handler (or,
  once the loop is closed, to ``threading.excepthook``, falling back to a
  stderr traceback; never raising); it is not delivered to a specific closer. Without a running loop the claimer closes inline (b1a).
* ``wait_closed``/``await_closed`` refuse the line holder, like ``close``,
  and a close listener (no end exists yet); a ``close``/``aclose`` from a
  close listener requests close and returns without waiting (Pi's nested
  ``close()`` returns the same pending closing promise).
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import threading
import traceback
from collections.abc import Awaitable, Callable, Coroutine, Generator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from functools import partial
from typing import Any, Generic, TypeVar, cast, final

from .documents import Token
from .documents import check_scope as _check_scope
from .documents import resolve as _resolve
from .records import (
    OMITTED,
    ConversationRecord,
    EntryRecord,
    Seq,
    SubmissionRecord,
    TaskRecord,
)
from .session import (
    CancelView,
    Reporter,
    SessionClosedError,
    SessionEnd,
    SessionFailed,
    SessionLifecycle,
    _LineTicket,
    _view,
)
from .storage import (
    ConversationQuery,
    Cursor,
    EntryQuery,
    Page,
    Storage,
    StorageWrite,
    TaskQuery,
)
from .tasks import Task
from .tx import (
    CommittedDocument,
    Draft,
    PendingTxOperations,
    RawDocumentChange,
    TxAffinityError,
    _check_version,
    _clock,
    _DocumentCache,
    _Done,
    _drive,
    _Handle,
    _Io,
    _load_document,
    _SyncDriver,
    _TxCore,
)

T = TypeVar("T")

LISTENER_ERROR_REPORT = "listener_error"


class CommitCancelled(Exception):
    """A commit whose cancel view was cancelled before its turn (Pi aborted signal)."""

    def __init__(self) -> None:
        super().__init__("Session commit was cancelled before it started")


class ReentrantSessionCall(RuntimeError):
    """A Session entry from the line holder would wait for itself (Python-only)."""

    def __init__(self, method: str) -> None:
        super().__init__(
            f"Session.{method}() called from the transaction holding the Session line"
        )
        self.method = method


@dataclass(frozen=True, slots=True)
class CommitPublication:
    """One committed publication: table writes, then adopted document changes."""

    seq: int
    changes: tuple[StorageWrite | RawDocumentChange, ...]


# -- async driver and handles ---------------------------------------------------


def _retrieved(future: asyncio.Future[Any]) -> None:
    """Observe an outcome, as Pi ``#track`` handles every operation's rejection."""
    if not future.cancelled():
        future.exception()


def _resolve_wake(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _current_task() -> object | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


@contextmanager
def _driving(ticket: _LineTicket) -> Generator[None, None, None]:
    """Loop-confined authority for one synchronous send/throw, never an await.

    Keep the displaced identity alive locally and restore it even for nested
    eager advances and process-control exceptions. No owner/cache lock is held.
    """
    previous = ticket.actor
    ticket.actor = _current_task()
    try:
        yield
    finally:
        ticket.actor = previous


@final
class TxOp(Generic[T]):
    """Handle of one ``AsyncTx`` operation; awaiting it never cancels it."""

    __slots__ = ("_future",)

    def __init__(self, future: asyncio.Future[T]) -> None:
        self._future = future

    def __await__(self) -> Generator[Any, None, T]:
        return asyncio.shield(self._future).__await__()

    def done(self) -> bool:
        return self._future.done()

    def result(self) -> T:
        return self._future.result()

    def add_done_callback(self, fn: Callable[[], object]) -> None:
        self._future.add_done_callback(lambda _future: fn())

    def __repr__(self) -> str:
        return f"TxOp(<{'done' if self._future.done() else 'pending'}>)"


@final
class _AsyncDriver:
    """Drives core bodies on one loop; backend calls run in its default executor."""

    __slots__ = ("_calls", "_loop", "_scope", "_thread")

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        scope: Callable[[], AbstractContextManager[None]] = nullcontext,
    ) -> None:
        self._loop = loop
        self._thread = threading.get_ident()
        self._calls: dict[_Io[Any], asyncio.Future[Any]] = {}
        self._scope = scope

    def check(self) -> None:
        if threading.get_ident() != self._thread or _running_loop() is not self._loop:
            raise TxAffinityError()

    def spawn(self, body: Coroutine[Any, Any, T]) -> TxOp[T]:
        """Run ``body`` eagerly to its first backend call, then on the loop."""
        try:
            with self._scope():
                request = body.send(None)
        except StopIteration as stop:
            return self.ready(cast(T, stop.value))
        except Exception as error:  # noqa: BLE001 - the operation's outcome
            return TxOp(self._failed(error))
        first: asyncio.Future[Any] | None = None
        continuation: Coroutine[Any, Any, T] | None = None
        try:
            first = self._submit(body, request)
            continuation = self._resume(body, first)
            task = self._loop.create_task(continuation)
        except BaseException as error:
            if continuation is not None:
                continuation.close()
            body.close()
            if not issubclass(type(error), Exception):
                raise
            # Submission may already be running. Keep this operation pending
            # until it finishes, including when the caller catches the error.
            failed = self._loop.create_future()
            failed.add_done_callback(_retrieved)
            if first is None or first.done():
                failed.set_exception(error)
            else:

                def rejected(
                    _done: asyncio.Future[Any], error: BaseException = error
                ) -> None:
                    failed.set_exception(error)

                first.add_done_callback(rejected)
            return TxOp(failed)
        task.add_done_callback(_retrieved)
        return TxOp(task)

    def follow(self, handle: _Handle[object]) -> TxOp[None]:
        source = cast(TxOp[object], handle)._future
        if source.done():
            if source.cancelled() or source.exception() is None:
                return self.ready(None)
            return TxOp(self._failed(cast(Exception, source.exception())))
        target: asyncio.Future[None] = self._loop.create_future()

        def copy(done: asyncio.Future[object]) -> None:
            if done.cancelled():
                target.cancel()
            elif (error := done.exception()) is not None:
                target.set_exception(error)
            else:
                target.set_result(None)

        source.add_done_callback(copy)
        target.add_done_callback(_retrieved)
        return TxOp(target)

    def ready(self, value: T) -> TxOp[T]:
        future: asyncio.Future[T] = self._loop.create_future()
        future.set_result(value)
        return TxOp(future)

    async def run(self, body: Coroutine[Any, Any, T]) -> T:
        """Run a settlement or load coroutine inside the current task."""
        self.check()
        try:
            try:
                with self._scope():
                    request = body.send(None)
            except StopIteration as stop:
                return cast(T, stop.value)
            return await self._resume(body, self._submit(body, request))
        finally:
            body.close()

    def pending(self) -> tuple[_Handle[object], ...]:
        """Backend work surviving an exceptional prologue/handoff, loop-only."""
        return tuple(
            TxOp(future) for future in self._calls.values() if not future.done()
        )

    def _failed(self, error: BaseException) -> asyncio.Future[Any]:
        future: asyncio.Future[Any] = self._loop.create_future()
        future.set_exception(error)
        future.exception()  # observed: an unawaited failed op never logs
        return future

    def _submit(
        self, body: Coroutine[Any, Any, Any], request: object
    ) -> asyncio.Future[Any]:
        if type(request) is not _Io:
            body.close()
            raise RuntimeError("Tx core awaited something other than a backend call")
        future = self._calls.get(request)
        if future is None:
            # Executor threads run only the guarded backend call.
            future = self._loop.run_in_executor(None, request.run)
            future.add_done_callback(_retrieved)
            self._calls[request] = future
        return future

    async def _resume(
        self, body: Coroutine[Any, Any, T], future: asyncio.Future[Any]
    ) -> T:
        try:
            while True:
                send: object = None
                error: BaseException | None = None
                try:
                    send = await future
                except BaseException as caught:  # noqa: BLE001 - delivered into the body
                    error = caught
                try:
                    with self._scope():
                        request = (
                            body.throw(error) if error is not None else body.send(send)
                        )
                except StopIteration as stop:
                    return cast(T, stop.value)
                future = self._submit(body, request)
        finally:
            body.close()


async def _wait_all(handles: tuple[_Handle[object], ...]) -> None:
    """Drain pending operations; their outcomes are already observed."""
    futures = {cast(TxOp[object], handle)._future for handle in handles}
    if futures:
        await asyncio.wait(futures)


# -- Tx facades ---------------------------------------------------------------------


class _Tx:
    __slots__ = ("_core",)

    def __init__(self, core: _TxCore) -> None:
        self._core = core

    def settle_submission(self, id: int, settlement: object) -> None:
        self._core.settle_submission(id, settlement)

    def place_submission(self, id: int, entry: int) -> None:
        self._core.place_submission(id, entry)

    def set_task(self, record: object) -> None:
        self._core.set_task(record)

    def staged_tasks(self) -> tuple[TaskRecord, ...]:
        return self._core.staged_tasks()

    def staged_conversations(self) -> tuple[ConversationRecord, ...]:
        return self._core.staged_conversations()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<transaction>)"


def _value(handle: _Handle[T]) -> T:
    assert isinstance(handle, _Done), "sync handles are settled when returned"
    return cast(T, handle.result())


@final
class SyncTx(_Tx):
    """Transaction view of ``commit()``: every operation returns its value."""

    __slots__ = ()

    def conversation(self, id: int) -> ConversationRecord | None:
        return _value(self._core.conversation(id))

    def entry(self, id: int) -> EntryRecord | None:
        return _value(self._core.entry(id))

    def task(self, id: int) -> TaskRecord | None:
        return _value(self._core.task(id))

    def submission(self, id: int) -> SubmissionRecord | None:
        return _value(self._core.submission(id))

    def submission_by_request(
        self, conversation_id: int, request_id: str
    ) -> SubmissionRecord | None:
        return _value(self._core.submission_by_request(conversation_id, request_id))

    def latest_head_marker(self, conversation_id: int) -> EntryRecord | None:
        return _value(self._core.latest_head_marker(conversation_id))

    def scan_conversations(
        self, query: ConversationQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[ConversationRecord]:
        return _value(self._core.scan_conversations(query, limit, cursor))

    def scan_entries(
        self, query: EntryQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[EntryRecord]:
        return _value(self._core.scan_entries(query, limit, cursor))

    def scan_tasks(
        self, query: TaskQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[TaskRecord]:
        return _value(self._core.scan_tasks(query, limit, cursor))

    def create_conversation(self, ownership: object) -> ConversationRecord:
        return _value(self._core.create_conversation(ownership))

    def append_entry(self, conversation_id: int, draft: object) -> EntryRecord:
        return _value(self._core.append_entry(conversation_id, draft))

    def create_task(
        self,
        task: Task[Any, Any, Any],
        input: object,
        *,
        ownership: object,
        conversation_id: int | None = None,
        background: bool = False,
        abandon_on_restart: bool = False,
        trace: object = None,
    ) -> int:
        return _value(
            self._core.create_task(
                task,
                input,
                ownership=ownership,
                conversation_id=conversation_id,
                background=background,
                abandon_on_restart=abandon_on_restart,
                trace=trace,
            )
        )

    def create_submission(self, create: object) -> SubmissionRecord:
        return _value(self._core.create_submission(create))

    def doc(
        self,
        raw: Token,
        *,
        conversation_id: int | None = None,
        task_id: int | None = None,
        key: object = OMITTED,
        seed: object = OMITTED,
    ) -> Draft:
        return _value(
            self._core.doc(
                raw,
                conversation_id=conversation_id,
                task_id=task_id,
                key=key,
                seed=seed,
            )
        )

    def retire_doc(
        self,
        raw: Token,
        *,
        conversation_id: int | None = None,
        task_id: int | None = None,
        key: object = OMITTED,
    ) -> None:
        _value(
            self._core.retire_doc(
                raw, conversation_id=conversation_id, task_id=task_id, key=key
            )
        )


def _op(handle: _Handle[T]) -> TxOp[T]:
    assert isinstance(handle, TxOp), "async handles are TxOp"
    return cast(TxOp[T], handle)


@final
class AsyncTx(_Tx):
    """Transaction view of ``acommit()``: operations return ``TxOp`` handles.

    ``settle_submission``, ``place_submission``, ``set_task`` and the staged
    views stay synchronous, as in Pi.
    """

    __slots__ = ()

    def conversation(self, id: int) -> TxOp[ConversationRecord | None]:
        return _op(self._core.conversation(id))

    def entry(self, id: int) -> TxOp[EntryRecord | None]:
        return _op(self._core.entry(id))

    def task(self, id: int) -> TxOp[TaskRecord | None]:
        return _op(self._core.task(id))

    def submission(self, id: int) -> TxOp[SubmissionRecord | None]:
        return _op(self._core.submission(id))

    def submission_by_request(
        self, conversation_id: int, request_id: str
    ) -> TxOp[SubmissionRecord | None]:
        return _op(self._core.submission_by_request(conversation_id, request_id))

    def latest_head_marker(self, conversation_id: int) -> TxOp[EntryRecord | None]:
        return _op(self._core.latest_head_marker(conversation_id))

    def scan_conversations(
        self, query: ConversationQuery, limit: int, cursor: Cursor | None = None
    ) -> TxOp[Page[ConversationRecord]]:
        return _op(self._core.scan_conversations(query, limit, cursor))

    def scan_entries(
        self, query: EntryQuery, limit: int, cursor: Cursor | None = None
    ) -> TxOp[Page[EntryRecord]]:
        return _op(self._core.scan_entries(query, limit, cursor))

    def scan_tasks(
        self, query: TaskQuery, limit: int, cursor: Cursor | None = None
    ) -> TxOp[Page[TaskRecord]]:
        return _op(self._core.scan_tasks(query, limit, cursor))

    def create_conversation(self, ownership: object) -> TxOp[ConversationRecord]:
        return _op(self._core.create_conversation(ownership))

    def append_entry(self, conversation_id: int, draft: object) -> TxOp[EntryRecord]:
        return _op(self._core.append_entry(conversation_id, draft))

    def create_task(
        self,
        task: Task[Any, Any, Any],
        input: object,
        *,
        ownership: object,
        conversation_id: int | None = None,
        background: bool = False,
        abandon_on_restart: bool = False,
        trace: object = None,
    ) -> TxOp[int]:
        return _op(
            self._core.create_task(
                task,
                input,
                ownership=ownership,
                conversation_id=conversation_id,
                background=background,
                abandon_on_restart=abandon_on_restart,
                trace=trace,
            )
        )

    def create_submission(self, create: object) -> TxOp[SubmissionRecord]:
        return _op(self._core.create_submission(create))

    def doc(
        self,
        raw: Token,
        *,
        conversation_id: int | None = None,
        task_id: int | None = None,
        key: object = OMITTED,
        seed: object = OMITTED,
    ) -> TxOp[Draft]:
        return _op(
            self._core.doc(
                raw,
                conversation_id=conversation_id,
                task_id=task_id,
                key=key,
                seed=seed,
            )
        )

    def retire_doc(
        self,
        raw: Token,
        *,
        conversation_id: int | None = None,
        task_id: int | None = None,
        key: object = OMITTED,
    ) -> TxOp[None]:
        return _op(
            self._core.retire_doc(
                raw, conversation_id=conversation_id, task_id=task_id, key=key
            )
        )


# -- listeners ------------------------------------------------------------------------


@final
class _Listeners:
    """Insertion-ordered listener set by identity (Pi ``Set``); runs no caller hook."""

    __slots__ = ("_items",)

    def __init__(self) -> None:
        self._items: list[Callable[..., object]] = []

    def add(self, fn: Callable[..., object]) -> None:
        if not any(item is fn for item in self._items):
            self._items.append(fn)

    def discard(self, fn: Callable[..., object]) -> list[Callable[..., object]]:
        """Remove ``fn``; returns the old list for the caller to drop unlocked."""
        old = self._items
        self._items = [item for item in old if item is not fn]
        return old

    def copy(self) -> tuple[Callable[..., object], ...]:
        return tuple(self._items)

    def take(self) -> list[Callable[..., object]]:
        """Detach every listener; the caller drops them after unlocking.

        Dropping a last reference can run a caller ``__del__``, which must
        never run under the owner lock.
        """
        items, self._items = self._items, []
        return items


def _is_unstarted(value: object) -> bool:
    return (
        inspect.iscoroutine(value)
        and inspect.getcoroutinestate(value) == inspect.CORO_CREATED
    )


def _refused_awaitable(result: object, message: str) -> TypeError:
    """A sync callback returned an awaitable: close it if it never started."""
    if _is_unstarted(result):
        cast(Coroutine[Any, Any, Any], result).close()
    return TypeError(message)


# -- the Session -------------------------------------------------------------------------


@final
class Session:
    """Private first internal Session: one mutation line over one owner."""

    __slots__ = (
        "_cache",
        "_close_listeners",
        "_commit_listeners",
        "_internal_listeners",
        "_now",
        "_owner",
        "_reporter",
    )

    def __init__(
        self,
        storage: Storage,
        *,
        reporter: Reporter | None = None,
        now: Callable[[], float] = _clock,
    ) -> None:
        if not callable(now):
            raise TypeError("Session now must be a millisecond clock")
        self._reporter = reporter
        self._now = now
        self._owner = SessionLifecycle(storage, reporter)
        self._cache = _DocumentCache()
        self._commit_listeners = _Listeners()
        self._internal_listeners = _Listeners()
        self._close_listeners = _Listeners()
        self._owner._bind_line(self._close_began, self._line_drained, _close_off_loop)

    # -- lifecycle views -------------------------------------------------------

    @property
    def failure(self) -> BaseException | None:
        """The first failure, if the Session failed (internal)."""
        return self._owner.failure

    @property
    def end(self) -> SessionEnd | None:
        """The terminal outcome once Storage closed, else ``None``."""
        return self._owner.end

    def fail(self, error: BaseException) -> None:
        """Internal: fail with ``error``; the first wins and is reported once."""
        self._owner.fail(error)

    # -- commit ----------------------------------------------------------------

    def commit(
        self, change: Callable[[SyncTx], T], *, cancel: CancelView | None = None
    ) -> T:
        """Run one atomic transaction on the line from this thread (Pi ``commit``)."""
        view = _view(cancel)
        self._refuse_reentry("commit")
        _refuse_loop_thread("commit", "acommit")
        ticket = self._enter_sync()
        try:
            result = self._run_sync(change, view)
        except BaseException:
            self._leave(ticket, raising=True)
            raise
        self._leave(ticket, raising=False)
        return result

    def _run_sync(self, change: Callable[[SyncTx], T], view: CancelView | None) -> T:
        self._grant_checks(view)
        driver = _SyncDriver()
        core = self._core(driver, view)
        try:
            result = change(SyncTx(core))
            if inspect.isawaitable(result):
                raise _refused_awaitable(
                    result, "commit() callback returned an awaitable; use acommit()"
                )
            # A callback that caught a failed read must not commit.
            self._assert_healthy()
        except BaseException:
            core.settle_failure()  # a SyncTx has no pending operations
            raise
        writes = driver.run(core.settle_success())
        if not writes:
            core.discard()
            return result
        try:
            # Once admitted, caller cancellation does not interrupt settlement.
            seq = self._owner.storage.commit(writes)
        except BaseException as error:
            core.discard()  # the guard failed the owner
            self._unknown_outcome(error)
            raise
        try:
            self._settle_admitted(core, writes, seq)
        except BaseException as error:
            self._unknown_outcome(error)
            raise
        return result

    async def acommit(
        self,
        change: Callable[[AsyncTx], T | Awaitable[T]],
        *,
        cancel: CancelView | None = None,
    ) -> T:
        """Run one atomic transaction on the line from this task (Pi ``commit``)."""
        view = _view(cancel)
        loop = asyncio.get_running_loop()
        caller = asyncio.current_task()
        self._refuse_reentry("acommit")
        ticket = await self._enter_async(loop, caller)
        owned = False
        core: _TxCore | None = None
        driver: _AsyncDriver | None = None
        prepared: TxOp[tuple[StorageWrite, ...]] | None = None

        def handed_off() -> None:
            # An eager factory can run (and even finish) settlement before
            # create_task returns or raises. The body claims ownership first.
            nonlocal owned
            owned = True

        try:
            self._grant_checks(view)
            driver = _AsyncDriver(loop, partial(_driving, ticket))
            core = self._core(driver, view)
            value: object = change(AsyncTx(core))
            while inspect.isawaitable(value):
                value = await value
            # A callback that caught a failed read must not commit.
            self._assert_healthy()
            # Seal, check pending work and prepare now (eagerly, to the first
            # backend call); the rest is owned, shielded settlement.
            prepared = driver.spawn(core.settle_success())
            settlement = self._settle_async(
                core, prepared, cast(T, value), ticket, handed_off
            )
            try:
                settle = loop.create_task(settlement)
            except BaseException:
                if not owned:
                    settlement.close()
                raise
            owned = True
            return await self._own(settle, ticket)
        except BaseException:
            if not owned:
                if core is None or driver is None:
                    self._leave(ticket, raising=True)
                else:
                    pending = (*core.settle_failure(), *driver.pending())
                    if prepared is not None and not prepared.done():
                        pending = (*pending, prepared)
                    # Exceptional cleanup must work even when create_task's
                    # factory caused the failure. Bypass that hook only here;
                    # the owned drain retains the line through cancellation.
                    drain = asyncio.Task(self._drain_async(pending, ticket), loop=loop)
                    await self._own(drain, ticket)
            raise

    def _enter_sync(self) -> _LineTicket:
        ticket = _LineTicket()
        self._owner._line_enter(ticket)
        try:
            self._owner._line_wait(ticket)
        except BaseException:
            # An interrupted waiter never strands the line (a rejected one is
            # gone); a close error stays recorded, never replacing this one.
            self._owner._line_abandon(ticket)
            raise
        return ticket

    async def _enter_async(
        self, loop: asyncio.AbstractEventLoop, caller: object
    ) -> _LineTicket:
        granted: asyncio.Future[None] = loop.create_future()
        ticket = _LineTicket(partial(loop.call_soon_threadsafe, _resolve_wake, granted))
        ticket.tasks = (caller,)
        self._owner._line_enter(ticket)
        try:
            if not self._owner._line_granted(ticket):
                await granted
                if not self._owner._line_granted(ticket):
                    raise RuntimeError("Session line woke a ticket it did not grant")
        except asyncio.CancelledError:
            # Detach at once: unqueue, or pass an unused grant on.
            self._owner._line_abandon(ticket)
            raise
        return ticket

    async def _own(self, settle: asyncio.Task[T], ticket: _LineTicket) -> T:
        """Await owned settlement; a cancelled waiter never cuts it."""
        settle.add_done_callback(_retrieved)
        return await asyncio.shield(settle)

    def _settlement_actor(self, ticket: _LineTicket) -> None:
        # Run at the first instruction of the owned body, before caller hooks;
        # registering after create_task returns is too late for eager factories.
        current = _current_task()
        if not any(task is current for task in ticket.tasks):
            ticket.tasks = (*ticket.tasks, current)

    async def _drain_async(
        self, pending: tuple[_Handle[object], ...], ticket: _LineTicket
    ) -> None:
        """After a callback failure: drain pending operations, then leave.

        The callback's error is in flight, so a close error never replaces it.
        """
        self._settlement_actor(ticket)
        try:
            await _wait_all(pending)
        finally:
            self._leave(ticket, raising=True)

    async def _settle_async(
        self,
        core: _TxCore,
        prepared: TxOp[tuple[StorageWrite, ...]],
        result: T,
        ticket: _LineTicket,
        handed_off: Callable[[], None],
    ) -> T:
        handed_off()
        self._settlement_actor(ticket)
        try:
            try:
                writes = await prepared
            except PendingTxOperations as pending:
                await _wait_all(pending.pending)
                raise
            if not writes:
                core.discard()
            else:
                commit = partial(self._owner.storage.commit, writes)
                try:
                    seq = await asyncio.get_running_loop().run_in_executor(None, commit)
                except BaseException as error:
                    core.discard()  # the guard failed the owner
                    self._unknown_outcome(error)
                    raise
                try:
                    self._settle_admitted(core, writes, seq)
                except BaseException as error:
                    self._unknown_outcome(error)
                    raise
        except BaseException:
            self._leave(ticket, raising=True)
            raise
        self._leave(ticket, raising=False)
        return result

    def _settle_admitted(
        self, core: _TxCore, writes: tuple[StorageWrite, ...], seq: Seq
    ) -> None:
        """Adopt the durable commit, then publish it (Pi #runCommit 757–766)."""
        try:
            documents = core.adopt(seq)
        except BaseException as error:
            # Storage already committed; memory would stay behind it.
            self._owner.fail(error)
            raise
        self._publish(seq, core.table_changes(writes), documents)

    def _unknown_outcome(self, error: BaseException) -> None:
        # A process-control exception during settlement leaves the durable
        # outcome unknown (Python-only): fail the owner; the first cause wins.
        if not issubclass(type(error), Exception):
            self._owner.fail(error)

    def _core(self, driver: Any, view: CancelView | None) -> _TxCore:
        storage = self._owner.storage
        return _TxCore(
            storage if view is None else storage.reading(view),
            self._cache,
            driver,
            now=self._now,
            healthy=self._assert_healthy,
        )

    def _grant_checks(self, view: CancelView | None) -> None:
        """Pi ``#runCommit``: health first, then the caller's cancel view."""
        self._assert_healthy()
        if view is not None and view.cancelled:
            raise CommitCancelled()

    def _assert_healthy(self) -> None:
        failure = self._owner.failure
        if failure is not None:
            raise SessionFailed(failure) from failure

    def _leave(self, ticket: _LineTicket, *, raising: bool) -> None:
        pending = self._owner._line_leave(ticket)
        if pending is not None and not raising:
            raise pending

    def _refuse_reentry(self, method: str) -> None:
        thread, current = threading.get_ident(), _current_task()
        holder = self._owner._line_holder()
        if holder is None:
            return
        # Async identities are loop-thread confined. No foreign thread reads
        # tasks/actor; no await or caller hook separates the snapshot and test.
        if holder.thread != thread:
            return
        if holder.wake is None:
            raise ReentrantSessionCall(method)
        if current is not None and (
            current is holder.actor or any(task is current for task in holder.tasks)
        ):
            raise ReentrantSessionCall(method)

    # -- read-only line jobs and snapshots ----------------------------------------

    def unload_documents(self) -> None:
        """Drop committed document residency on the line; retained snapshots live."""
        self._owner._assert_usable()
        self._on_line_sync("unload_documents", self._cache.clear)

    async def aunload_documents(self) -> None:
        """Queue committed document unloading on the same line as acommit()."""
        self._owner._assert_usable()
        await self._on_line_async("aunload_documents", self._cache.clear)

    def read_on_line(self, job: Callable[[], T]) -> T:
        """Run a read-only job on the line (Pi ``readOnLine``)."""
        self._owner._assert_usable()
        return self._on_line_sync("read_on_line", job)

    async def aread_on_line(self, job: Callable[[], T | Awaitable[T]]) -> T:
        """Run a read-only job on the line from this task (Pi ``readOnLine``)."""
        self._owner._assert_usable()
        return await self._on_line_async("aread_on_line", job)

    def snapshot(
        self,
        raw: Token,
        *,
        conversation_id: int | None = None,
        task_id: int | None = None,
        key: object = OMITTED,
    ) -> CommittedDocument | None:
        """The committed incarnation at ``raw``'s address (Pi raw ``snapshot``)."""
        self._owner._assert_usable()
        key, address = _resolve(raw, conversation_id, task_id, key)
        loaded = self._hit(raw, key)
        if loaded is None:
            storage, cache = self._owner.storage, self._cache
            loaded = self._on_line_sync(
                "snapshot",
                lambda: _drive(_load_document(storage, cache, raw, key, address)),
            )
        return _checked(raw, loaded)

    async def asnapshot(
        self,
        raw: Token,
        *,
        conversation_id: int | None = None,
        task_id: int | None = None,
        key: object = OMITTED,
    ) -> CommittedDocument | None:
        """``snapshot()`` from this task; a miss loads on the line."""
        self._owner._assert_usable()
        key, address = _resolve(raw, conversation_id, task_id, key)
        loaded = self._hit(raw, key)
        if loaded is None:
            storage, cache = self._owner.storage, self._cache
            driver = _AsyncDriver(asyncio.get_running_loop())
            loaded = await self._on_line_async(
                "asnapshot",
                lambda: driver.run(_load_document(storage, cache, raw, key, address)),
            )
        return _checked(raw, loaded)

    def _hit(self, raw: Token, key: Any) -> CommittedDocument | None:
        cached = self._cache.get(key)
        return cached if cached is not None and cached.version == raw.version else None

    def _on_line_sync(self, method: str, job: Callable[[], T]) -> T:
        self._refuse_reentry(method)
        _refuse_loop_thread(method, "a" + method)
        ticket = self._enter_sync()
        try:
            self._assert_healthy()
            value = job()
            if inspect.isawaitable(value):
                raise _refused_awaitable(
                    value, f"{method}() job returned an awaitable; use a{method}()"
                )
        except BaseException:
            self._leave(ticket, raising=True)
            raise
        self._leave(ticket, raising=False)
        return value

    async def _on_line_async(
        self, method: str, job: Callable[[], T | Awaitable[T]]
    ) -> T:
        self._refuse_reentry(method)
        loop = asyncio.get_running_loop()
        ticket = await self._enter_async(loop, asyncio.current_task())
        try:
            self._assert_healthy()
            value: object = job()
            while inspect.isawaitable(value):
                value = await value
        except BaseException:
            self._leave(ticket, raising=True)
            raise
        self._leave(ticket, raising=False)
        return cast(T, value)

    # -- listeners and publication -----------------------------------------------

    def subscribe_commits(
        self, listener: Callable[[CommitPublication], object]
    ) -> Callable[[], None]:
        """Observe complete commits synchronously after adoption; errors reported."""
        return self._subscribe(self._commit_listeners, listener)

    def observe_commits(
        self, listener: Callable[[CommitPublication], object]
    ) -> Callable[[], None]:
        """Internal listeners: run first; an exception fails the Session."""
        return self._subscribe(self._internal_listeners, listener)

    def subscribe_close(self, listener: Callable[[], object]) -> Callable[[], None]:
        """Observe close synchronously when it begins, also on failure."""
        return self._subscribe(self._close_listeners, listener)

    def _subscribe(
        self, listeners: _Listeners, listener: Callable[..., object]
    ) -> Callable[[], None]:
        if not callable(listener):
            raise TypeError("Expected a listener callable")
        owner = self._owner
        with owner._cond:
            # Pi ``assertUsable`` and the add are one step under the owner lock.
            failure, sealed = owner._failure, owner._line_sealed
            if failure is None and not sealed:
                listeners.add(listener)
        if failure is not None:
            raise SessionFailed(failure) from failure
        if sealed:
            raise SessionClosedError()

        def unsubscribe() -> None:
            with owner._cond:
                dropped = listeners.discard(listener)
            del dropped  # released outside the owner lock

        return unsubscribe

    def _publish(
        self,
        seq: Seq,
        tables: tuple[StorageWrite, ...],
        documents: tuple[RawDocumentChange, ...],
    ) -> None:
        with self._owner._cond:
            internal = self._internal_listeners.copy()
            host = self._commit_listeners.copy()
        if not internal and not host:
            return  # Pi: no listener, no publication
        publication = CommitPublication(seq, (*tables, *documents))
        # The commit is durable: a failing listener neither fails the commit
        # nor skips the others (Pi #publish 806–816).
        for listener in internal:
            self._contained(partial(listener, publication), self._owner.fail)
        for listener in host:
            self._contained(partial(listener, publication), self._listener_error)

    def _close_began(self) -> None:
        """Owner close-begin hook: run the close listeners once (Pi #notifyClose)."""
        with self._owner._cond:
            listeners = self._close_listeners.take()
        for listener in listeners:
            self._contained(listener, self._listener_error)

    def _line_drained(self) -> None:
        """Owner line-drained hook: Pi's close job clears listeners and documents."""
        with self._owner._cond:
            dropped = (
                self._commit_listeners.take(),
                self._internal_listeners.take(),
                self._close_listeners.take(),
            )
        del dropped  # a listener's __del__ never runs under the owner lock
        self._cache.clear()

    def _contained(
        self, run: Callable[[], object], on_error: Callable[[BaseException], None]
    ) -> None:
        """Pi ``contained``: a throw, or a Future that fails later, goes to ``on_error``."""
        try:
            result = run()
        except Exception as error:  # noqa: BLE001 - routed, never fails the runner
            on_error(error)
            return
        except BaseException as error:
            # Memory may now be behind storage: fail, and let it propagate.
            self._owner.fail(error)
            raise
        if inspect.iscoroutine(result):
            if _is_unstarted(result):
                result.close()
            on_error(TypeError("Session listeners are synchronous"))
        elif asyncio.isfuture(result):
            result.add_done_callback(partial(_future_outcome, on_error))
        elif inspect.isawaitable(result):
            on_error(TypeError("Session listeners are synchronous"))

    def _listener_error(self, error: BaseException) -> None:
        reporter = self._reporter
        if reporter is None:
            return
        try:
            reporter(error, LISTENER_ERROR_REPORT)
        except Exception:  # noqa: BLE001 - reports never fail what ran them
            pass

    # -- close ---------------------------------------------------------------------

    def close(
        self, *, cancel: CancelView | None = None, timeout: float | None = None
    ) -> None:
        """Seal the line, settle accepted work, close Storage once, and wait.

        From inside a close listener (Pi's nested ``close()``) it requests close
        and returns without waiting.
        """
        if not self._owner._in_close_hook():
            self._refuse_reentry("close")
            _refuse_loop_thread("close", "aclose")
        self._owner.close(cancel, timeout)

    async def aclose(self) -> None:
        """``close()`` from this task; cancelling the wait keeps the seal and drain."""
        if self._owner._in_close_hook():
            self._owner.request_close()  # Pi's nested close: never waits on itself
            return
        self._refuse_reentry("aclose")
        self._owner.request_close()
        await self._closed()
        error = self._owner._close_error
        if error is not None:
            raise error

    def request_close(self) -> None:
        """Seal the line and start close without waiting; allowed anywhere."""
        self._owner.request_close()

    def wait_closed(
        self, *, cancel: CancelView | None = None, timeout: float | None = None
    ) -> SessionEnd:
        """Wait for the terminal outcome without requesting close."""
        self._refuse_close_hook("wait_closed")
        self._refuse_reentry("wait_closed")
        _refuse_loop_thread("wait_closed", "await_closed")
        return self._owner.wait_closed(cancel, timeout)

    async def await_closed(self) -> SessionEnd:
        """Wait from this task for the terminal outcome (Pi ``closed``)."""
        self._refuse_close_hook("await_closed")
        self._refuse_reentry("await_closed")
        await self._closed()
        end = self._owner.end
        assert end is not None
        return end

    def _refuse_close_hook(self, method: str) -> None:
        # No end exists while a close listener runs: a wait would wait for itself.
        if self._owner._in_close_hook():
            raise ReentrantSessionCall(method)

    async def _closed(self) -> None:
        loop = asyncio.get_running_loop()
        done: asyncio.Future[None] = loop.create_future()
        if self._owner._when_closed(
            partial(loop.call_soon_threadsafe, _resolve_wake, done)
        ):
            await done


def _checked(raw: Token, loaded: CommittedDocument | None) -> CommittedDocument | None:
    if loaded is None:
        return None
    _check_scope(raw, loaded.record)
    _check_version(raw, loaded.record, loaded.version)
    return loaded


def _future_outcome(
    on_error: Callable[[BaseException], None], future: asyncio.Future[Any]
) -> None:
    if future.cancelled():
        return
    error = future.exception()
    if error is not None:
        on_error(error)


def _close_off_loop(run: Callable[[], object]) -> bool:
    """Owner close runner: never run a backend close on an event-loop thread.

    On a running loop the claimed close becomes owned work in the loop's
    default executor (joined by ``asyncio.run``), so a blocking backend close
    never blocks the loop and a close that waits for a loop task completes. Its
    outcome is shared through the owner (``_close_error``, the end); no line
    leave or commit waits for it. A process-control exception escaping it (a
    reporter's) is not delivered to any one closer: it goes to the loop's
    exception handler. Elsewhere, or once that executor is shut down, the
    claimer runs the close inline.
    """
    loop = _running_loop()
    if loop is None:
        return False
    try:
        future = loop.run_in_executor(None, partial(_background_close, loop, run))
    except RuntimeError:  # the default executor is already shut down
        return False
    future.add_done_callback(_retrieved)
    return True


def _background_close(
    loop: asyncio.AbstractEventLoop, run: Callable[[], object]
) -> None:
    """Run a claimed close on an executor thread; never drop what escapes it."""
    try:
        run()
    except BaseException as error:  # noqa: BLE001 - reported, never swallowed
        context = {
            "message": "Session background close raised; it reaches no closer",
            "exception": error,
        }
        try:
            loop.call_soon_threadsafe(loop.call_exception_handler, context)
        except RuntimeError:  # the loop is closed: no handler can run any more
            _report_unhandled(error)


def _report_unhandled(error: BaseException) -> None:
    """Last resort once the loop is closed; never raises.

    The thread excepthook (public, accepts ``threading.ExceptHookArgs``; the
    default one prints the traceback to stderr). If a custom hook fails, the
    traceback is printed to stderr directly.
    """
    try:
        threading.excepthook(
            threading.ExceptHookArgs(
                (type(error), error, error.__traceback__, threading.current_thread())
            )
        )
    except BaseException:  # noqa: BLE001 - a failing hook falls back to stderr
        try:
            traceback.print_exception(error, file=sys.stderr)
        except BaseException:  # noqa: BLE001, S110 - the fallback never raises
            pass


def _refuse_loop_thread(method: str, alternative: str) -> None:
    """Sync entries that may wait for the line never block an event loop."""
    if _running_loop() is not None:
        raise RuntimeError(
            f"Session.{method}() would block the event loop; use {alternative}()"
        )

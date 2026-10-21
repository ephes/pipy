"""Private Session lifecycle owner and guarded Storage (DE02b1a); no Session/Tx.

One owner governs the first failure, report-once, guarded-call registration,
the final-stage guard admission seal, drain and backend-close-once. Every
future Session/Tx component must read and write through ``owner.storage`` (or a
``reading`` facade of it) and consume this same owner; no second failure latch
or raw-backend route exists. The Session-line admission seal (queued commits
and on-line reads, DE02b2) is a distinct, earlier phase; this module provides
only the final guard seal that ``close`` and ``fail`` apply.

Semantics adapted from Pi durable 1.3.0 ``packages/durable/src/session/session.ts``
(``fail``, ``close``, ``FailureLatch``, ``GuardedStorage``; git blob
b22e9c56a9f888e007f9697306a0b182092c006a, sha256
cc0e6af2159b5e746bee809c478a4f3946573b97314c9c6995e8ab3d690a9186) and
``src/errors.ts`` (``SessionFailed``; blob c66a1cee2c5a8b8d7736d83448bc6d680840a755),
MIT Copyright (c) 2025 Mario Zechner; notice:
tests/fixtures/durable/UPSTREAM-LICENSE.txt.

Python spelling, documented decisions:

* Threads replace Pi's promise chain. One ordinary ``Condition`` guards only
  owner state. Admission (health, then seal) and registration are one locked
  step; the backend call runs without the lock; final classification (health
  check, or late-failure check, exemption and latch) and unregistration are
  again ONE locked step, so a failure latched while a call is registered always
  ends that call with ``SessionFailed`` (Pi runs catch/finally/assert in one
  synchronous step). Report and close claim follow outside the lock.
  No backend call, reporter, waiter wake-up or foreign exception inspection runs
  under the lock.
* Classification mirrors ``GuardedStorage.#call``: after a backend exception,
  an already failed owner raises ``SessionFailed(first cause)``; a read that
  raised ``StorageRequestError`` or whose caller view is cancelled is exempt and
  re-raises the original; anything else latches the first failure and re-raises
  the original. ``commit``/``mint_id`` are never exempt. A success on a failed
  owner raises ``SessionFailed`` and discards the result. A non-``Exception``
  ``BaseException`` (KeyboardInterrupt, SystemExit, ...) is never exempt (the
  outcome is unknown), latches the first failure if none exists and is always
  re-raised unchanged. A ``finally`` guard still unregisters if anything
  escapes before the settle step.
* Cleanup ownership is "last retirer closes": once close is requested, the
  thread that retires the final registered call, or the requester itself when
  none is underway, runs backend ``close()`` exactly once outside the lock.
  ``fail`` never waits for drain. No cleanup thread is started.
* The first failure is reported once, outside the lock, after the seal and after
  the failing call retired, so a reporter that calls back finds the owner failed
  and may itself request or perform close without waiting on its own call.
  Reporter ``Exception``s are contained (as Pi's Harness ``safeReport``); other
  ``BaseException``s propagate. A healthy backend-close failure becomes the
  first cause and is reported once after the close outcome is published, so a
  reporter calling ``close`` cannot wait on its own cleanup. Pi reports before
  its asynchronous backend close; here the reporting thread reports before it
  may claim close, but another thread may close a drained backend concurrently.
* Arguments, DTOs and results pass through unchanged: no admission, coercion,
  positivity rule or error normalisation. A raw backend error for an ill-typed
  argument (Memory's ``RecordError``) is not a ``StorageRequestError`` and fails
  the owner like any backend error; typed ingress belongs to Session/Tx.
* Backend ``close()`` is synchronous and runs on the thread that claims it; it
  must not itself wait for this owner's close. A close error never replaces an
  exception the claiming thread is already raising (a guarded call's original
  error, ``SessionFailed`` or a reporter's exception): it is recorded as the
  first cause if none exists, reported once then, and raised to every
  ``close()`` caller. A process-control (non-``Exception``) close error is also
  raised by a claimer with nothing else to raise (``fail``, ``request_close``,
  or a guarded call that would otherwise return), so it is never lost silently.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Condition, Lock
from typing import Final, Literal, TypeVar, final

from .records import (
    ConversationId,
    ConversationRecord,
    DocumentId,
    DocumentRecord,
    EntryId,
    EntryRecord,
    Seq,
    SubmissionId,
    SubmissionRecord,
    TaskId,
    TaskRecord,
)
from .storage import (
    ConversationQuery,
    Cursor,
    DocumentAddress,
    DocumentPoint,
    DocumentQuery,
    EntryLookup,
    EntryQuery,
    Page,
    Storage,
    StorageRequestError,
    StorageWrite,
    StoredDocument,
    SubmissionQuery,
    TaskQuery,
)

T = TypeVar("T")

Reporter = Callable[[BaseException, str], None]
"""Receives ``(error, "session_failed")`` once for the first failure."""

SESSION_FAILED_REPORT: Final = "session_failed"


class SessionFailed(Exception):
    """The Session failed; nothing runs or commits any more. ``cause`` is first.

    Python spelling of Pi ``SessionFailed`` (``errors.ts:41–46``): same message,
    the first error kept as ``cause`` and as ``__cause__``.
    """

    def __init__(self, cause: BaseException) -> None:
        super().__init__("Session failed after a storage error; close and reopen it")
        self.cause = cause
        self.__cause__ = cause


class SessionClosedError(RuntimeError):
    """Guarded admission after the seal; Pi's plain ``Error("Session is closed")``."""

    def __init__(self) -> None:
        super().__init__("Session is closed")


class WaitCancelled(Exception):
    """A close/closed waiter gave up; cleanup, seal and drain continue."""


@final
class CancelLatch:
    """Trusted, owned one-way caller-cancellation latch (no provider hooks)."""

    __slots__ = ("_cancelled", "_lock", "_waiters")

    def __init__(self) -> None:
        self._lock = Lock()
        self._cancelled = False
        self._waiters: list[Condition] = []

    def cancel(self) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            waiters = list(self._waiters)
        # Wake owner waiters outside this latch's lock; never under an owner lock.
        for waiter in waiters:
            with waiter:
                waiter.notify_all()

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def view(self) -> CancelView:
        return CancelView(self)

    def _watch(self, waiter: Condition) -> bool:
        with self._lock:
            if self._cancelled:
                return False
            self._waiters.append(waiter)
            return True

    def _unwatch(self, waiter: Condition) -> None:
        with self._lock:
            self._waiters.remove(waiter)


@final
class CancelView:
    """Immutable read-only view of one owned ``CancelLatch``."""

    __slots__ = ("_latch",)
    _latch: CancelLatch

    def __init__(self, latch: CancelLatch) -> None:
        if type(latch) is not CancelLatch:
            raise TypeError("CancelView requires an owned CancelLatch")
        object.__setattr__(self, "_latch", latch)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("CancelView is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("CancelView is immutable")

    @property
    def cancelled(self) -> bool:
        return self._latch.cancelled


@dataclass(frozen=True, slots=True)
class SessionEnd:
    """Terminal outcome: ``closed``, or ``failed`` with the first cause."""

    reason: Literal["closed", "failed"]
    error: BaseException | None = None


_IDLE: Final = "idle"
_REQUESTED: Final = "requested"
_RUNNING: Final = "running"
_DONE: Final = "done"


def _view(value: CancelView | None) -> CancelView | None:
    if value is not None and type(value) is not CancelView:
        raise TypeError("Expected an owned CancelView or None")
    return value


def _deadline(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    if type(timeout) not in (int, float) or not 0 <= timeout < math.inf:
        raise ValueError("Expected a finite non-negative timeout in seconds")
    return time.monotonic() + timeout


@final
class SessionLifecycle:
    """Private owner of one backend: first failure, guard registration and close."""

    __slots__ = (
        "_backend",
        "_close_error",
        "_close_state",
        "_cond",
        "_end",
        "_failure",
        "_reporter",
        "_sealed",
        "_storage",
        "_underway",
    )

    def __init__(self, backend: Storage, reporter: Reporter | None = None) -> None:
        self._backend = backend
        self._reporter = reporter
        self._cond = Condition(Lock())
        self._failure: BaseException | None = None
        self._sealed = False
        self._underway = 0
        self._close_state: str = _IDLE
        self._close_error: BaseException | None = None
        self._end: SessionEnd | None = None
        self._storage = GuardedStorage(self, None)

    @property
    def storage(self) -> GuardedStorage:
        """The Storage behind the guard; every component reads and writes through it."""
        return self._storage

    @property
    def failure(self) -> BaseException | None:
        """The first failure, if the owner failed."""
        with self._cond:
            return self._failure

    @property
    def sealed(self) -> bool:
        """Whether guarded admission is sealed (by close or failure)."""
        with self._cond:
            return self._sealed

    @property
    def end(self) -> SessionEnd | None:
        """The terminal outcome once backend close has settled, else ``None``."""
        with self._cond:
            return self._end

    def fail(self, error: BaseException) -> None:
        """Fail with ``error``; the first wins, is reported once, and starts close.

        Never waits: close runs now only when no guarded call is underway,
        otherwise the last call to retire runs it.
        """
        if not issubclass(type(error), BaseException):
            raise TypeError("Session failure requires an exception")
        with self._cond:
            first = self._latch(error)
        if first:
            self._finish(error, raising=False)

    def request_close(self) -> None:
        """Seal guarded admission and start close without waiting (idempotent)."""
        with self._cond:
            self._sealed = True
            if self._close_state is _IDLE:
                self._close_state = _REQUESTED
        pending = self._close_if_drained()
        if pending is not None:
            raise pending

    def close(
        self, cancel: CancelView | None = None, timeout: float | None = None
    ) -> None:
        """Seal, drain every registered call, close the backend once, and wait.

        Every closer shares one outcome and receives the backend close error, if
        any. A waiter that gives up (``cancel`` or ``timeout``) raises
        ``WaitCancelled``/``TimeoutError`` without undoing the seal, the drain or
        the pending backend close.
        """
        view = _view(cancel)
        deadline = _deadline(timeout)
        self.request_close()
        self._wait_done(view, deadline)
        error = self._close_error
        if error is not None:
            raise error

    def wait_closed(
        self, cancel: CancelView | None = None, timeout: float | None = None
    ) -> SessionEnd:
        """Wait for the terminal outcome without requesting close."""
        view = _view(cancel)
        deadline = _deadline(timeout)
        self._wait_done(view, deadline)
        end = self._end
        assert end is not None
        return end

    # -- guarded calls -------------------------------------------------------

    def _call(self, read: bool, view: CancelView | None, run: Callable[[], T]) -> T:
        with self._cond:
            refused, closed = self._admit()
        if refused is not None:
            raise SessionFailed(refused) from refused
        if closed:
            raise SessionClosedError()
        registered = True
        try:
            try:
                result = run()
            except BaseException as error:
                # Exemption is decided outside the owner lock: exact type tests
                # and a trusted latch flag, never a foreign hook.
                exempt = self._exempt(read, view, error)
                # One step: unregister, then classify against the failure state
                # of that same instant (Pi: catch, then finally, synchronously).
                with self._cond:
                    registered = False
                    late, first = self._settle_error(error, exempt)
                self._finish(first, raising=True)
                if late is not None and issubclass(type(error), Exception):
                    raise SessionFailed(late) from late
                raise
            # One step: unregister and read health, so a failure latched while
            # this call was registered always ends it with SessionFailed.
            with self._cond:
                registered = False
                settled = self._settle()
            self._finish(None, raising=settled is not None)
            if settled is not None:
                raise SessionFailed(settled) from settled
            return result
        finally:
            if registered:  # only if something escaped before the settle step
                with self._cond:
                    self._underway -= 1
                self._finish(None, raising=True)

    def _admit(self) -> tuple[BaseException | None, bool]:
        # Caller holds the lock. Health first, then the guard seal (Pi #call);
        # admission and registration are one step.
        if self._failure is not None:
            return self._failure, False
        if self._sealed:
            return None, True
        self._underway += 1
        return None, False

    @staticmethod
    def _exempt(read: bool, view: CancelView | None, error: BaseException) -> bool:
        return (
            read
            and issubclass(type(error), Exception)
            and (
                issubclass(type(error), StorageRequestError)
                or (view is not None and view.cancelled)
            )
        )

    def _latch(self, error: BaseException) -> bool:
        # Caller holds the lock. First failure only; seal before any report.
        if self._failure is not None:
            return False
        self._failure = error
        self._sealed = True
        if self._close_state is _IDLE:
            self._close_state = _REQUESTED
        return True

    def _settle(self) -> BaseException | None:
        # Caller holds the lock: retire a successful call and read its health.
        self._underway -= 1
        return self._failure

    def _settle_error(
        self, error: BaseException, exempt: bool
    ) -> tuple[BaseException | None, BaseException | None]:
        # Caller holds the lock: retire a failed call and classify it.
        self._underway -= 1
        return self._classify_error(error, exempt)

    def _classify_error(
        self, error: BaseException, exempt: bool
    ) -> tuple[BaseException | None, BaseException | None]:
        # Caller holds the lock. Returns (earlier failure, newly latched first).
        late = self._failure
        if late is None and not exempt and self._latch(error):
            return None, error
        return late, None

    def _finish(self, first: BaseException | None, *, raising: bool) -> None:
        """After retirement: report a new first failure, then claim close.

        A process-control close error is raised here only when the caller is
        not already raising; otherwise it stays recorded for every closer.
        """
        pending: BaseException | None = None
        try:
            if first is not None:
                self._report(first)
        finally:
            pending = self._close_if_drained()
        if pending is not None and not raising:
            raise pending

    # -- close ---------------------------------------------------------------

    def _close_if_drained(self) -> BaseException | None:
        """Claim and run a requested close once drained; never raises.

        Returns a non-``Exception`` close error for the claimer to raise when it
        is not already raising another exception.
        """
        with self._cond:
            claim = self._close_state is _REQUESTED and self._underway == 0
            if claim:
                self._close_state = _RUNNING
        return self._run_close() if claim else None

    def _run_close(self) -> BaseException | None:
        error: BaseException | None = None
        try:
            self._backend.close()
        except BaseException as caught:  # noqa: BLE001 - outcome shared by all closers
            error = caught
        with self._cond:
            # A Storage that cannot close is a failed one; an earlier failure
            # stays the cause.
            first = error is not None and self._latch(error)
            self._close_error = error
            failure = self._failure
            self._end = (
                SessionEnd("closed")
                if failure is None
                else SessionEnd("failed", failure)
            )
            self._close_state = _DONE
            self._cond.notify_all()
        if first:
            assert error is not None
            self._report(error)
        if error is not None and not issubclass(type(error), Exception):
            return error
        return None

    def _wait_done(self, view: CancelView | None, deadline: float | None) -> None:
        cond = self._cond
        with cond:
            watched = False
            try:
                while self._close_state is not _DONE:
                    if view is not None:
                        if not watched:
                            watched = view._latch._watch(cond)
                        if view.cancelled:
                            raise WaitCancelled("close wait was cancelled")
                    remaining = None
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("close wait timed out")
                    cond.wait(remaining)
            finally:
                if watched:
                    assert view is not None
                    view._latch._unwatch(cond)

    def _report(self, error: BaseException) -> None:
        reporter = self._reporter
        if reporter is None:
            return
        try:
            reporter(error, SESSION_FAILED_REPORT)
        except Exception:  # noqa: BLE001 - reports never fail what ran them
            pass


@final
class GuardedStorage:
    """Storage-shaped facade over the owner's guard; arguments pass unchanged.

    The default facade has no cancelled caller. ``reading(view)`` returns a
    facade bound to the same owner whose reads consult ``view`` only when a
    read raises; ``commit``/``mint_id`` stay never exempt and ``close`` is the
    owner's close.
    """

    __slots__ = ("_owner", "_view")

    def __init__(self, owner: SessionLifecycle, view: CancelView | None) -> None:
        if type(owner) is not SessionLifecycle:
            raise TypeError("GuardedStorage requires its SessionLifecycle owner")
        self._owner = owner
        self._view = _view(view)

    def reading(self, view: CancelView) -> GuardedStorage:
        if type(view) is not CancelView:
            raise TypeError("Expected an owned CancelView")
        return GuardedStorage(self._owner, view)

    def _read(self, run: Callable[[], T]) -> T:
        return self._owner._call(True, self._view, run)

    def commit(self, writes: tuple[StorageWrite, ...]) -> Seq:
        """Never exempt: once admitted, whether a failed batch committed is unknown."""
        backend = self._owner._backend
        return self._owner._call(False, None, lambda: backend.commit(writes))

    def mint_id(self) -> int:
        backend = self._owner._backend
        return self._owner._call(False, None, lambda: backend.mint_id())

    def conversation(self, id: ConversationId) -> ConversationRecord | None:
        backend = self._owner._backend
        return self._read(lambda: backend.conversation(id))

    def scan_conversations(
        self, query: ConversationQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[ConversationRecord]:
        backend = self._owner._backend
        return self._read(lambda: backend.scan_conversations(query, limit, cursor))

    def entry(
        self, id: EntryId, *, conversation_id: ConversationId | None = None
    ) -> EntryLookup | None:
        backend = self._owner._backend
        return self._read(lambda: backend.entry(id, conversation_id=conversation_id))

    def find_latest_head_marker(
        self,
        conversation_id: ConversationId,
        at_or_before_entry_id: EntryId | None = None,
    ) -> EntryRecord | None:
        backend = self._owner._backend
        return self._read(
            lambda: backend.find_latest_head_marker(
                conversation_id, at_or_before_entry_id
            )
        )

    def scan_entries(
        self, query: EntryQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[EntryRecord]:
        backend = self._owner._backend
        return self._read(lambda: backend.scan_entries(query, limit, cursor))

    def task(self, id: TaskId) -> TaskRecord | None:
        backend = self._owner._backend
        return self._read(lambda: backend.task(id))

    def scan_tasks(
        self, query: TaskQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[TaskRecord]:
        backend = self._owner._backend
        return self._read(lambda: backend.scan_tasks(query, limit, cursor))

    def submission(self, id: SubmissionId) -> SubmissionRecord | None:
        backend = self._owner._backend
        return self._read(lambda: backend.submission(id))

    def scan_submissions(
        self, query: SubmissionQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[SubmissionRecord]:
        backend = self._owner._backend
        return self._read(lambda: backend.scan_submissions(query, limit, cursor))

    def submission_by_request(
        self, conversation_id: ConversationId, request_id: str
    ) -> SubmissionRecord | None:
        backend = self._owner._backend
        return self._read(
            lambda: backend.submission_by_request(conversation_id, request_id)
        )

    def find_document(
        self, address: DocumentAddress, at: DocumentPoint = "current"
    ) -> DocumentRecord | None:
        backend = self._owner._backend
        return self._read(lambda: backend.find_document(address, at))

    def document(
        self, id: DocumentId, at: DocumentPoint = "current"
    ) -> StoredDocument | None:
        backend = self._owner._backend
        return self._read(lambda: backend.document(id, at))

    def scan_documents(
        self, query: DocumentQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[DocumentRecord]:
        backend = self._owner._backend
        return self._read(lambda: backend.scan_documents(query, limit, cursor))

    def close(self) -> None:
        """Seal, drain every registered call, then close the backend once."""
        self._owner.close()

"""Private Session lifecycle owner and guarded Storage (DE02b1a, line DE02b2b).

One owner governs the first failure, report-once, guarded-call registration,
the final-stage guard admission seal, drain and backend-close-once. Every
Session/Tx component reads and writes through ``owner.storage`` (or a
``reading`` facade of it) and consumes this same owner; no second failure latch
or raw-backend route exists.

DE02b2b adds the Session mutation line to this owner (it has no failure state
of its own): one FIFO of tickets, at most one holder, and a line seal that is a
distinct, earlier phase than the guard seal. Close seals the line, runs the
close-begin hook once, and seals the guard only when the line has no holder and
no queued ticket (Pi enqueues its close job behind accepted work); the backend
close claim waits for that same drained line. A first failure seals the line,
rejects every queued ticket at once with ``SessionFailed``, runs the close-begin
hook before the report, and closes the backend only after the holder leaves.
With no ticket ever admitted the line is always drained, so the DE02b1a
behaviour is unchanged.

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
* Line tickets are owned objects: the lock compares them by identity only and
  wakes a sync ticket through this Condition, an async one through its owned
  wake callback outside the lock (a callback raising ``RuntimeError`` because
  its loop closed counts as a cancelled ticket and passes the line on).
  Leaving the line and granting the next ticket are one locked step, and the
  leaver owns that wake-up, so no hand-off is lost. The close-begin and
  line-drained hooks and async closed-wakes also run outside the lock; the line
  drains only after the close-begin hook finished. A ``close()`` from inside
  that hook (on its thread) requests close and returns without waiting, as
  Pi's nested ``close()`` returns the same pending closing promise. A bound close runner may move
  a claimed backend close off an event-loop thread into owned executor work.
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
import threading
import time
from collections import deque
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

_QUEUED: Final = "queued"
_GRANTED: Final = "granted"
_CANCELLED: Final = "cancelled"
_REJECTED: Final = "rejected"
_LEFT: Final = "left"


@final
class _LineTicket:
    """One admission to the Session line (DE02b2b); compared by identity only.

    ``wake`` is ``None`` for a sync waiter (woken through the owner Condition)
    or an owned callback that schedules an async waiter's wake-up on its loop.
    ``tasks`` holds the async holder's caller and settlement tasks. ``actor``
    holds its driving Task only during a synchronous core advance. Both are
    confined to ``thread``: Session writes them before publication or on that
    event-loop thread, and refuses foreign-thread inspection before reading.
    Publication/retirement of the ticket itself uses the owner's condition.
    """

    __slots__ = ("actor", "state", "tasks", "thread", "wake")

    def __init__(self, wake: Callable[[], object] | None = None) -> None:
        self.state: str = _QUEUED
        self.thread = threading.get_ident()
        self.wake = wake
        self.tasks: tuple[object, ...] = ()
        self.actor: object | None = None


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
        "_close_begun",
        "_close_hook_done",
        "_close_hook_thread",
        "_close_runner",
        "_close_error",
        "_close_state",
        "_closed_wakes",
        "_cond",
        "_end",
        "_failure",
        "_holder",
        "_line_done",
        "_line_sealed",
        "_on_close_begin",
        "_on_line_drained",
        "_queue",
        "_rejected_wakes",
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
        # DE02b2b Session line: FIFO tickets, one holder, line seal before guard seal.
        self._line_sealed = False
        self._line_done = False
        self._close_begun = False
        self._close_hook_done = False
        self._close_hook_thread: int | None = None
        self._close_runner: Callable[[Callable[[], object]], bool] | None = None
        self._queue: deque[_LineTicket] = deque()
        self._holder: _LineTicket | None = None
        self._rejected_wakes: list[Callable[[], object]] = []
        self._closed_wakes: list[Callable[[], object]] = []
        self._on_close_begin: Callable[[], None] | None = None
        self._on_line_drained: Callable[[], None] | None = None
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
        """Seal the line and start close without waiting (idempotent).

        The close-begin hook runs once, here. Guarded admission is sealed and
        backend close requested once the line is drained: at once without a
        holder or queued ticket, otherwise when the last ticket leaves.
        """
        with self._cond:
            self._line_sealed = True
        try:
            self._begin_close()
        finally:
            try:
                self._drain_line()
            finally:
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
        if self._in_close_hook():
            # Pi: a close from a close listener returns the same pending
            # closing promise; waiting here would wait for this very hook.
            return
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
        # Seal the line and reject every queued ticket at once (Pi runs each
        # into its health check); async wakes follow outside the lock.
        self._line_sealed = True
        if self._queue:
            for ticket in self._queue:
                ticket.state = _REJECTED
                if ticket.wake is not None:
                    self._rejected_wakes.append(ticket.wake)
            self._queue.clear()
            self._cond.notify_all()
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
                # Pi ``fail``: close begins (its listeners run) before the report.
                try:
                    self._begin_close()
                finally:
                    self._report(first)
        finally:
            try:
                self._wake_rejected()
                self._drain_line()
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
            # The backend closes behind the line (Pi's close job on the line).
            claim = claim and self._holder is None and not self._queue
            if claim:
                self._close_state = _RUNNING
            runner = self._close_runner
        if not claim:
            return None
        # A claimer on an event loop hands the claimed close to owned executor
        # work; the outcome is shared through ``_close_error`` and ``_end``.
        if runner is not None and runner(self._run_close):
            return None
        return self._run_close()

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
            wakes, self._closed_wakes = self._closed_wakes, []
        _wake_all(wakes)
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

    # -- the Session line (DE02b2b) ---------------------------------------------

    def _bind_line(
        self,
        on_close_begin: Callable[[], None],
        on_line_drained: Callable[[], None],
        close_runner: Callable[[Callable[[], object]], bool] | None = None,
    ) -> None:
        """Install the Session's close-begin and line-drained hooks once.

        ``close_runner`` may take a claimed backend close off the calling thread
        (returning True); otherwise the claimer runs it inline.
        """
        with self._cond:
            if self._on_close_begin is not None or self._line_sealed:
                raise RuntimeError("The Session line is already bound or sealed")
            self._on_close_begin = on_close_begin
            self._on_line_drained = on_line_drained
            self._close_runner = close_runner

    def _assert_usable(self) -> None:
        """Pi ``assertUsable``: health first, then the line seal."""
        with self._cond:
            failure, sealed = self._failure, self._line_sealed
        if failure is not None:
            raise SessionFailed(failure) from failure
        if sealed:
            raise SessionClosedError()

    def _line_holder(self) -> _LineTicket | None:
        with self._cond:
            return self._holder

    def _line_enter(self, ticket: _LineTicket) -> None:
        """Admit ``ticket`` (health, then the line seal); grant it if idle."""
        with self._cond:
            refused, closed = self._failure, self._line_sealed
            if refused is None and not closed:
                self._queue.append(ticket)
                if self._holder is None:
                    self._grant()
        if refused is not None:
            raise SessionFailed(refused) from refused
        if closed:
            raise SessionClosedError()

    def _line_wait(self, ticket: _LineTicket) -> None:
        """Block a sync ticket until it holds the line or was rejected."""
        with self._cond:
            while ticket.state is _QUEUED:
                self._cond.wait()
            rejected = ticket.state is _REJECTED
            failure = self._failure
        if rejected:
            assert failure is not None
            raise SessionFailed(failure) from failure

    def _line_granted(self, ticket: _LineTicket) -> bool:
        """After an async wake-up: True when held, raises when rejected."""
        with self._cond:
            state, failure = ticket.state, self._failure
        if state is _REJECTED:
            assert failure is not None
            raise SessionFailed(failure) from failure
        return state is _GRANTED

    def _line_leave(self, ticket: _LineTicket) -> BaseException | None:
        """The holder leaves: grant the next ticket, then drain and claim close.

        Returns a process-control close error for a caller not already raising.
        """
        with self._cond:
            if self._holder is not ticket:
                raise RuntimeError("Only the line holder can leave the line")
            ticket.state = _LEFT
            # Leaving and granting the next ticket are one step, so no entrant
            # can grant a queued ticket without owning its wake-up.
            granted = self._pass_line()
        try:
            self._wake_granted(granted)
            self._drain_line()
        finally:
            pending = self._close_if_drained()
        return pending

    def _line_abandon(self, ticket: _LineTicket) -> BaseException | None:
        """A cancelled async waiter: unqueue it, or pass an unused grant on."""
        with self._cond:
            if ticket.state is _QUEUED:
                self._queue.remove(ticket)
                ticket.state = _CANCELLED
            held = ticket.state is _GRANTED and self._holder is ticket
        if held:
            return self._line_leave(ticket)
        return None

    def _grant(self) -> _LineTicket | None:
        # Caller holds the lock and the line has no holder. Leaving grants the
        # next ticket in the same step, so a line without a holder has an empty
        # queue: an entrant can only ever grant itself (FIFO is kept).
        while self._queue:
            ticket = self._queue.popleft()
            if ticket.state is _QUEUED:
                ticket.state = _GRANTED
                self._holder = ticket
                return ticket
        return None

    def _pass_line(self) -> _LineTicket | None:
        """Caller holds the lock: clear the holder and grant the next ticket.

        A sync grant is notified here; an async grant is returned and the
        caller must wake it outside the lock (``_wake_granted``).
        """
        self._holder = None
        ticket = self._grant()
        if ticket is None:
            return None
        if ticket.wake is None:
            self._cond.notify_all()
            return None
        return ticket

    def _wake_granted(self, ticket: _LineTicket | None) -> None:
        while ticket is not None:
            wake = ticket.wake
            assert wake is not None, "only async grants are woken here"
            try:
                wake()
                return
            except RuntimeError:  # its loop is closed: a cancelled ticket
                with self._cond:
                    if self._holder is not ticket:
                        return
                    ticket.state = _CANCELLED
                    ticket = self._pass_line()

    def _wake_rejected(self) -> None:
        with self._cond:
            wakes, self._rejected_wakes = self._rejected_wakes, []
        _wake_all(wakes)

    def _begin_close(self) -> None:
        """Run the close-begin hook once (Pi ``#notifyClose``)."""
        with self._cond:
            first = not self._close_begun
            hook = self._on_close_begin
            self._close_begun = True
            if first and hook is not None:
                self._close_hook_thread = threading.get_ident()
        if not first:
            return
        try:
            if hook is not None:
                hook()
        finally:
            # The line may drain (and clear what the hook reads) only now.
            with self._cond:
                self._close_hook_done = True
                self._close_hook_thread = None

    def _in_close_hook(self) -> bool:
        """Whether this thread is running the close-begin hook right now."""
        with self._cond:
            return self._close_hook_thread == threading.get_ident()

    def _drain_line(self) -> None:
        """Once the sealed line has no holder or ticket: Pi's close line job.

        It waits for the close-begin hook to finish (a second closer never
        drains under a running hook). The line-drained hook runs once, then the
        guard is sealed and backend close requested; the claim follows in
        ``_close_if_drained``.
        """
        with self._cond:
            run = (
                self._line_sealed
                and self._close_hook_done
                and not self._line_done
                and self._holder is None
                and not self._queue
            )
            if run:
                self._line_done = True
        if not run:
            return
        hook = self._on_line_drained
        try:
            if hook is not None:
                hook()
        finally:
            with self._cond:
                self._sealed = True
                if self._close_state is _IDLE:
                    self._close_state = _REQUESTED

    def _when_closed(self, wake: Callable[[], object]) -> bool:
        """Register an async closed-wake; False when close is already done."""
        with self._cond:
            if self._close_state is _DONE:
                return False
            self._closed_wakes.append(wake)
            return True


def _wake_all(wakes: list[Callable[[], object]]) -> None:
    for wake in wakes:
        try:
            wake()
        except RuntimeError:  # noqa: S110 - that waiter's loop is closed
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

"""Private completed-only attachment to Session's committed conversation line.

Notifications carry no execution authority. Reconnect means a fresh baseline;
consumer callbacks are caller-owned and may observe the same facts again.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import replace
from typing import Any, Literal, TypeAlias

from .completed_values import CompletedSnapshot, capture
from .generation_transport import owned_call
from .session import SessionClosedError, SessionFailed
from .session_core import CommitPublication, Session
from .storage import StorageWrite
from .tx import RawDocumentChange

EndReason = Literal[
    "stopped", "session_closed", "listener_error", "capture_error", "loop_closed"
]
Listener = Callable[[CompletedSnapshot], Awaitable[object]]
ReadResult: TypeAlias = tuple[CompletedSnapshot, int | None] | EndReason


class ProjectionClosed(RuntimeError):
    def __init__(self, reason: EndReason) -> None:
        super().__init__("Committed projection ended: " + reason)
        self.reason = reason


def _relevant(publication: CommitPublication, cid: int) -> bool:
    for change in publication.changes:
        if type(change) is RawDocumentChange:
            if change.conversation_id == cid and change.record.to_tree()["kind"] in (
                "pi.tool.round-order",
                "pi.generation.round",
                "pi.usage",
            ):
                return True
        elif type(change) is StorageWrite:
            tree = change.to_tree()
            if tree["type"] in ("entry", "task", "conversation"):
                row = tree["value"]
                assert type(row) is dict
                if (
                    row.get(
                        "conversationId",
                        row.get("id") if tree["type"] == "conversation" else None,
                    )
                    == cid
                ):
                    if tree["type"] != "task" or row["state"]["status"] == "terminal":
                        return True
    return False


class CompletedWatch:
    """One capture, one callback and one coalesced pending replacement."""

    def __init__(self, session: Session, conversation_id: int) -> None:
        self._session, self._cid = session, conversation_id
        self._loop = asyncio.get_running_loop()
        self._lock = threading.Lock()
        # Only these fields are shared with publisher/read workers.
        self._reason: EndReason | None = None
        self._requested = 0
        self._seq: int | None = None
        self._undos: tuple[Callable[[], None], ...] = ()
        self._wake_pending = False
        # Everything below is confined to the acquiring event loop.
        self._ready = False
        self._listener: Listener | None = None
        self._capture_task: asyncio.Task[None] | None = None
        self._acquisition: asyncio.Task[ReadResult] | None = None
        self._consumer_task: asyncio.Task[None] | None = None
        self._pending: tuple[int, CompletedSnapshot] | None = None
        self._captured = 0
        self._revision = 0
        self._snapshot: CompletedSnapshot | None = None
        self._closed: asyncio.Future[EndReason] = self._loop.create_future()
        self._drain_lock = asyncio.Lock()
        self._goal: tuple[int, asyncio.Future[CompletedSnapshot]] | None = None

    def _on_loop(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("Committed projection belongs to its acquiring loop")

    @property
    def snapshot(self) -> CompletedSnapshot:
        self._on_loop()
        assert self._snapshot is not None
        return self._snapshot

    def _signal(self) -> None:
        with self._lock:
            if self._wake_pending:
                return
            self._wake_pending = True
        try:
            self._loop.call_soon_threadsafe(self._dispatch)
        except RuntimeError:
            self._end("loop_closed", wake=False)

    def _dispatch(self) -> None:
        with self._lock:
            self._wake_pending = False
        self._kick()

    def _end(self, reason: EndReason, *, wake: bool = True) -> None:
        with self._lock:
            if self._reason is None:
                self._reason = reason
            undos, self._undos = self._undos, ()
        for undo in undos:
            undo()
        if wake:
            self._signal()

    def _notify(self, publication: CommitPublication) -> None:
        # Public loop state is a plain closed flag, not a foreign Task/Future.
        # An abandoned observer must detach even on another conversation's
        # publication, before thawing that publication's potentially rich data.
        if self._loop.is_closed():
            self._end("loop_closed", wake=False)
            return
        if not _relevant(publication, self._cid):
            return
        with self._lock:
            if self._reason is not None:
                return
            self._requested += 1
            self._seq = publication.seq
        self._signal()

    def _register(self) -> CompletedSnapshot:
        """Entire body owns an actual synchronous Session line ticket."""
        snapshot = capture(self._session._owner.storage, self._cid)
        first = self._session.subscribe_commits(self._notify)
        try:
            second = self._session.subscribe_close(lambda: self._end("session_closed"))
        except BaseException:
            first()
            raise
        undos = (first, second)
        with self._lock:
            accepted = self._reason is None
            if accepted:
                self._undos = undos
        if not accepted:
            for undo in undos:
                undo()
        return snapshot

    def start(self, listener: Listener) -> None:
        self._on_loop()
        if self._listener is not None:
            raise RuntimeError("Committed projection has already started")
        if not callable(listener):
            raise TypeError("Expected an asynchronous completed listener")
        with self._lock:
            reason = self._reason
        if reason is not None:
            raise ProjectionClosed(reason)
        self._listener = listener
        self._kick()

    def stop(self) -> None:
        """Seal callback admission; an admitted callback remains caller-owned."""
        self._end("stopped")

    async def wait_closed(self) -> EndReason:
        self._on_loop()
        return await asyncio.shield(self._closed)

    def _finish(self, reason: EndReason) -> None:
        self._pending = None
        if not self._closed.done():
            self._closed.set_result(reason)
        if self._goal is not None:
            _, goal = self._goal
            if not goal.done():
                goal.set_exception(ProjectionClosed(reason))

    def _kick(self) -> None:
        with self._lock:
            reason, requested = self._reason, self._requested
        if reason is not None:
            self._finish(reason)
            return
        if not self._ready:
            return
        if requested > self._captured and self._capture_task is None:
            self._capture_task = self._spawn(lambda: self._produce(requested))
        if (
            self._listener is not None
            and self._pending is not None
            and self._consumer_task is None
        ):
            self._consumer_task = self._spawn(self._consume)

    def _spawn(
        self, body: Callable[[], Coroutine[Any, Any, None]]
    ) -> asyncio.Task[None] | None:
        gate: asyncio.Future[None] = self._loop.create_future()

        async def registered() -> None:
            await gate
            await body()

        wrapper = registered()
        try:
            work = self._loop.create_task(wrapper)
        except Exception:  # noqa: BLE001 - failed task handoff must not leak a coroutine/subscription
            wrapper.close()
            self._end("loop_closed")
            return None
        # The body is created only after the gate. Eager execution cannot
        # finish before _kick installs its task; pre-start cancellation owns
        # no unawaited inner coroutine.
        gate.set_result(None)
        return work

    async def _produce(self, target: int) -> None:
        try:
            result = await owned_call(self._read)
            if isinstance(result, str):
                self._end(result)
                return
            value, seq = result
            with self._lock:
                reason = self._reason
            if reason is None:
                self._revision += 1
                self._snapshot = replace(
                    value, revision=self._revision, publication_seq=seq
                )
                self._captured = target
                self._pending = (target, self._snapshot)
        except asyncio.CancelledError:
            self._end("loop_closed")
            raise
        except Exception:  # noqa: BLE001 - observer errors are payload-free and contained
            self._end("capture_error")
        finally:
            self._capture_task = None
            self._kick()

    def _read(self, *, register: bool = False) -> ReadResult:
        """Actual worker returns only owned facts or a fixed failure tag.

        In particular, a late read refusal cannot replace cancellation with a
        private backend exception while owned_call waits for the real worker.
        """
        try:
            return self._session.read_on_line(
                lambda: (self._register(), None) if register else self._capture()
            )
        except (SessionClosedError, SessionFailed):
            return "session_closed"
        except Exception:  # noqa: BLE001 - payload-free read refusal, no execution or retry
            return "capture_error"

    def _acquired(self, work: asyncio.Task[ReadResult]) -> None:
        # Loop-local receipt only. The worker's actual Session ticket survives
        # even teardown cancellation of this asyncio wrapper.
        if not work.cancelled():
            work.exception()
        self._acquisition = None

    def _capture(self) -> tuple[CompletedSnapshot, int | None]:
        value = capture(self._session._owner.storage, self._cid)
        # The read still owns the Session line; no later publication can be
        # advertised as belonging to this snapshot after the ticket leaves.
        with self._lock:
            seq = self._seq
        return value, seq

    async def _consume(self) -> None:
        try:
            while self._pending is not None:
                tick, snapshot = self._pending
                self._pending = None
                with self._lock:
                    admitted = self._reason is None
                if not admitted:
                    return
                assert self._listener is not None
                await self._listener(snapshot)
                with self._lock:
                    reason = self._reason
                if reason is None:
                    self._ack(tick, snapshot)
        except asyncio.CancelledError:
            self._end("loop_closed")
            raise
        except Exception:  # noqa: BLE001 - one caller-owned callback cannot fail the Session
            self._end("listener_error")
        finally:
            self._consumer_task = None
            self._kick()

    def _ack(self, tick: int, snapshot: CompletedSnapshot) -> None:
        if self._goal is not None:
            target, goal = self._goal
            if tick >= target and not goal.done():
                goal.set_result(snapshot)

    async def drain(self) -> CompletedSnapshot:
        """Capture after this call and await a successful consumer delivery."""
        self._on_loop()
        if asyncio.current_task() is self._consumer_task:
            raise RuntimeError("A completed listener cannot drain itself")
        self._session._refuse_reentry("completed_drain")
        if self._listener is None:
            raise RuntimeError("Start a completed listener before draining")
        async with self._drain_lock:
            with self._lock:
                reason = self._reason
                if reason is None:
                    self._requested += 1
                target = self._requested
            if reason is not None:
                raise ProjectionClosed(reason)
            goal: asyncio.Future[CompletedSnapshot] = self._loop.create_future()
            # Cancellation detaches this waiter; retrieve a later failure too.
            goal.add_done_callback(
                lambda done: None if done.cancelled() else done.exception()
            )
            self._goal = (target, goal)
            self._kick()
            return await asyncio.shield(goal)


async def observe_completed(session: Session, conversation_id: int) -> CompletedWatch:
    """Attach baseline and notifications without a gap or execution on open."""
    # Preserve the caller's real async-line identity before moving the entire
    # synchronous read job to its own worker/ticket. No context impersonation.
    session._refuse_reentry("observe_completed")
    watch = CompletedWatch(session, conversation_id)
    body = owned_call(lambda: watch._read(register=True))
    try:
        work = watch._loop.create_task(body)
    except Exception:  # noqa: BLE001 - refused handoff owns no running read
        body.close()
        watch._end("loop_closed")
        raise ProjectionClosed("loop_closed") from None
    watch._acquisition = work
    work.add_done_callback(watch._acquired)
    try:
        # An API cancellation abandons acquisition immediately. The shielded
        # owned_call and its actual line ticket retain already accepted work;
        # _register rejects any later resource offer after _end has latched.
        result = await asyncio.shield(work)
        if isinstance(result, str):
            raise ProjectionClosed(result)
        watch._snapshot = result[0]
        watch._ready = True
        watch._kick()
        with watch._lock:
            reason = watch._reason
        if reason is not None:
            raise ProjectionClosed(reason)
        return watch
    except BaseException:
        watch._end("stopped")
        raise

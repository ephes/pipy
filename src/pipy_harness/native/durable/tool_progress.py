"""One retained reporter and one host-owned progress writer per invocation."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from time import monotonic
from typing import Any, TypeVar

from pipy_harness.native.agent.content import ToolOutput, ToolPartialResult
from pipy_harness.native.agent.tool_details import NO_TOOL_DETAILS
from pipy_harness.native.agent.tools import _InvocationOutputGate

from .codec import OwnedJson
from .tool_progress_values import (
    CAPTURE_PERMITS,
    PERIOD_BYTES_PER_SECOND,
    PERIOD_SECONDS,
    SNAPSHOT_BYTES,
    Diagnostic,
    FailureCategory,
    ProgressRefused,
    Tail,
    _capture_tree,
    _CapturedTree,
    capture_forwarded_details,
    capture_json,
    diagnostics,
)

_T = TypeVar("_T")


def _borrowed_candidate_details(value: object) -> dict[str, Any]:
    """Ephemeral only: whole-snapshot capture must own it before installation."""
    return (
        {"present": False}
        if value is NO_TOOL_DETAILS
        else {"present": True, "value": value}
    )


@dataclass(frozen=True, slots=True)
class _PreparedProgress:
    captured: _CapturedTree
    tail: Tail
    epoch: int


class ProgressEnded(RuntimeError):
    """No persistence acknowledgement is available for this invocation."""

    def __init__(self) -> None:
        super().__init__("Durable tool reporting authority ended")


class RetainedToolProgress(_InvocationOutputGate):
    """All mutable reporter state shares the inherited Condition.

    Only the owning host runs flush()/finish() and operates the asyncio Event.
    Foreign threads hold no asyncio task/future. Immutable snapshots cross the
    guard; capture, commit, wakes and foreign-reference disposal do not.
    """

    def __init__(
        self,
        initial: OwnedJson,
        commit: Callable[[OwnedJson], Coroutine[Any, Any, None]],
        barrier_preflight: Callable[[], None],
    ) -> None:
        super().__init__(self, retain=True, permits=CAPTURE_PERMITS)
        self._loop = asyncio.get_running_loop()
        self._host_thread = threading.get_ident()
        self._event = asyncio.Event()
        self._commit = commit
        self._barrier_preflight = barrier_preflight
        self._preparing = False
        self._working = initial
        self._working_size = capture_json(initial.thaw()).encoded_bytes
        self._tail = Tail()
        self._epoch = 0
        self._acknowledged = 0
        self._forced = 0
        self._inflight: tuple[int, int] | None = None
        self._domain_failure: FailureCategory | None = None
        self._infrastructure_failed = False
        self._wake_pending = False
        self._finishing = False
        self._flush_exited = False
        self._tokens = float(SNAPSHOT_BYTES)
        self._token_time = monotonic()
        self._last_periodic: float | None = None

    def _prepare(self, callback: Callable[[], _T]) -> _T:
        # Capacity is reserved before wrapper conversion or any payload copy.
        # It never waits and is separate from the callback/barrier lifetime.
        with self._condition:
            self._check_reporting()
            if self._preparing:
                raise ProgressRefused("progress_capacity")
            self._preparing = True
        try:
            return callback()
        finally:
            with self._condition:
                self._preparing = False
                self._condition.notify_all()

    def _dispatch(self, capture: Callable[[], ToolOutput]) -> None:
        self._run_admitted(
            lambda sink: self._prepare(
                lambda: self._install(self._capture_output(capture()))
            )
        )

    def forward_details(self, value: object) -> object:
        # The extension calls this only within the retained dispatch capture.
        return capture_forwarded_details(value)

    def _capture_failure(self, error: Exception) -> None:
        with self._condition:
            if isinstance(error, ProgressRefused):
                if self._domain_failure is None:
                    self._domain_failure = error.category
            else:
                self._infrastructure_failed = True
            self._condition.notify_all()
        try:
            self._schedule()
        except Exception:  # noqa: BLE001 - the failure wake must not escape void reporting
            # Do not retry a failed wake from its own error path. The host
            # still owns the worker/flusher and performs its final drain.
            with self._condition:
                self._infrastructure_failed = True
                self._condition.notify_all()

    def _refused_capacity(self) -> None:
        self._capture_failure(ProgressRefused("progress_capacity"))

    def _schedule(self) -> None:
        with self._condition:
            send = not self._wake_pending and not self._flush_exited
            if send:
                self._wake_pending = True
        if send:
            try:
                self._loop.call_soon_threadsafe(self._woken)
            except RuntimeError:
                with self._condition:
                    self._infrastructure_failed = True
                    self._condition.notify_all()

    def _woken(self) -> None:
        with self._condition:
            self._wake_pending = False
        self._event.set()

    def _check_reporting(self) -> None:
        # Called only with the Condition held.
        if self._infrastructure_failed or self._finishing:
            raise ProgressEnded()
        if self._domain_failure is not None:
            raise ProgressRefused(self._domain_failure)

    def _capture_update(
        self, change: Callable[[dict[str, Any], Tail], Tail]
    ) -> _PreparedProgress:
        with self._condition:
            self._check_reporting()
            previous, epoch, tail = self._working, self._epoch, self._tail
        tree = previous.thaw()
        del previous
        assert type(tree) is dict
        candidate_tail = change(tree, tail)
        return _PreparedProgress(_capture_tree(tree), candidate_tail, epoch)

    def _install(self, prepared: _PreparedProgress, *, force: bool = False) -> int:
        # Wrapper DTO, borrowed tree and change closures have returned before
        # strict construction begins. Only this detached candidate remains.
        captured, candidate_tail, epoch = (
            prepared.captured,
            prepared.tail,
            prepared.epoch,
        )
        del prepared
        try:
            candidate = OwnedJson(captured.tree)
        except (ValueError, TypeError):
            raise ProgressRefused("invalid_progress") from None
        size = captured.encoded_bytes
        del captured
        with self._condition:
            self._check_reporting()
            # One allocating workspace excludes competing candidate builders.
            assert epoch == self._epoch and self._preparing
            retired, old_tail = self._working, self._tail
            self._working, self._tail = candidate, candidate_tail
            self._working_size = size
            self._epoch += 1
            accepted = self._epoch
            if force:
                self._forced = accepted
        del retired, old_tail
        self._schedule()
        return accepted

    def _capture_output(self, output: ToolOutput) -> _PreparedProgress:
        def change(tree: dict[str, Any], tail: Tail) -> Tail:
            if type(output) is ToolPartialResult:
                tail = Tail().append(output.content)
                tree["details"] = _borrowed_candidate_details(output.details)
            elif type(output) is str:
                tail = tail.append(output)
            else:
                raise ProgressRefused("invalid_progress")
            tree["output"] = tail.text
            tree["droppedBytes"] = tail.dropped_bytes
            tree["droppedLines"] = tail.dropped_lines
            return tail

        return self._capture_update(change)

    def report_diagnostic(
        self, *, severity: Any, message: Any, code: Any = None
    ) -> None:
        def capture() -> _PreparedProgress:
            diagnostic = Diagnostic(severity, message, code)

            def change(tree: dict[str, Any], tail: Tail) -> Tail:
                tree["diagnostics"].append(diagnostic.tree())
                diagnostics(tree["diagnostics"])
                return tail

            return self._capture_update(change)

        def report(sink: Any) -> None:
            self._prepare(lambda: self._install(capture()))

        try:
            self._run_admitted(report)
        except Exception as error:  # noqa: BLE001 - diagnostic is a void callback
            self._capture_failure(error)

    def report_details(self, value: object) -> None:
        if threading.get_ident() == self._host_thread:
            raise ProgressEnded()
        try:
            self._barrier_preflight()
        except Exception:  # noqa: BLE001 - held-line/ended checks expose only a fixed failure
            raise ProgressEnded() from None

        def capture() -> _PreparedProgress:
            details = _borrowed_candidate_details(value)

            def change(tree: dict[str, Any], tail: Tail) -> Tail:
                tree["details"] = details
                return tail

            return self._capture_update(change)

        def report(sink: Any) -> None:
            # Preparation and strict construction both finish before waiting;
            # the barrier retains only an epoch and lifetime permit.
            epoch = self._prepare(lambda: self._install(capture(), force=True))
            self._await_prefix(epoch)

        try:
            admitted = self._run_admitted(report)
        except Exception as error:  # noqa: BLE001 - preserve fixed reporter failures
            self._capture_failure(error)
            if isinstance(error, ProgressRefused):
                raise
            raise ProgressEnded() from None
        if not admitted:
            with self._condition:
                if self._domain_failure is not None:
                    raise ProgressRefused(self._domain_failure)
            raise ProgressEnded()

    def _await_prefix(self, epoch: int) -> None:
        with self._condition:
            while self._acknowledged < epoch:
                if self._infrastructure_failed or self._flush_exited:
                    raise ProgressEnded()
                self._condition.wait()

    def _next_flush(self) -> tuple[OwnedJson, int, float, bool] | None:
        with self._condition:
            if self._infrastructure_failed or self._epoch == self._acknowledged:
                return None
            snapshot, epoch, size = self._working, self._epoch, self._working_size
            forced = self._finishing or self._forced > self._acknowledged
        now = monotonic()
        with self._condition:
            self._tokens = min(
                SNAPSHOT_BYTES,
                self._tokens + (now - self._token_time) * PERIOD_BYTES_PER_SECOND,
            )
            self._token_time = now
            delay = (
                0.0
                if forced
                else max(
                    0.0,
                    (size - self._tokens) / PERIOD_BYTES_PER_SECOND,
                    0.0
                    if self._last_periodic is None
                    else self._last_periodic + PERIOD_SECONDS - now,
                )
            )
            if not delay:
                self._inflight = (epoch, size)
                if not forced:
                    self._tokens -= size
                    self._last_periodic = now
            return snapshot, epoch, delay, forced

    async def flush(self) -> None:
        try:
            while True:
                self._event.clear()
                work = self._next_flush()
                if work is None:
                    with self._condition:
                        done = self._finishing or self._infrastructure_failed
                    if done:
                        return
                    await self._event.wait()
                    continue
                snapshot, epoch, delay, _ = work
                del work
                if delay:
                    del snapshot
                    try:
                        await asyncio.wait_for(self._event.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    continue
                # The operation Task owns the payload until the concrete
                # Session change consumes it. Retain the Task through commit
                # and adoption, without retaining another host payload root.
                operation = asyncio.create_task(self._commit(snapshot))
                del snapshot
                await self._commit_owned(operation)
                del operation
                with self._condition:
                    self._acknowledged = epoch
                    self._inflight = None
                    self._condition.notify_all()
        except Exception as error:  # noqa: BLE001 - commit failure cannot become a tool error
            # Even a callback's ProgressRefused raised from commit is infra.
            with self._condition:
                self._infrastructure_failed = True
                self._condition.notify_all()
            del error
        finally:
            with self._condition:
                self._flush_exited = True
                self._condition.notify_all()

    async def _commit_owned(self, work: asyncio.Task[None]) -> None:
        try:
            await asyncio.shield(work)
        finally:
            # A cancelled flusher waiter does not detach an admitted commit.
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    continue
            if not work.cancelled():
                work.exception()

    def finish(self) -> None:
        """Host-only final flush request, after callback/physical-worker drain."""
        with self._condition:
            self._finishing = True
        self._event.set()

    def result(self) -> tuple[OwnedJson, FailureCategory | None]:
        with self._condition:
            if (
                not self._flush_exited
                or self._infrastructure_failed
                or self._active_callbacks
                or self._acknowledged != self._epoch
            ):
                raise ProgressEnded()
            # Successful final prefix ACK covers this exact working snapshot.
            # The actual Session document/cache owns committed authority; an
            # uncertain flush cannot reach this branch or reconstruct a result.
            return self._working, self._domain_failure

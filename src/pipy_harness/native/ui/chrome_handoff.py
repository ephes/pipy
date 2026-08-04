"""The value types of the extension-chrome ownership transaction.

Chrome ownership changes hands when a reload candidate attaches: writes that
arrive while ownership is undecided are queued on a handoff, then either replayed
onto the accepted sink or discarded with the refused candidate. These five
records are that transaction's vocabulary, and they are declaration-only -- no
behaviour, no terminal, no session.

They are public here because the transaction that consumes them is moving out of
the terminal-UI shell; a leading underscore would only mean "private to a file
they no longer live in".
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import cast

from pipy_harness.native.extension_chrome_state import (
    ExtensionChromeAttachResult,
    ExtensionChromeCommitToken,
    ExtensionChromeDelivery,
    ExtensionChromePrepareInput,
    ExtensionChromeSink,
    ExtensionChromeSnapshot,
)


@dataclass(frozen=True, slots=True)
class ChromeAcceptanceResult:
    """Ownership result for one post-commit chrome acceptance attempt."""

    accepted: bool
    diagnostic: str | None = None
    retired_sink: ExtensionChromeSink | None = None
    candidate_closed: bool = False


@dataclass(slots=True)
class ChromeHandoffOperation:
    """One retained write admitted while chrome ownership is undecided."""

    kind: str
    values: tuple[object, ...]
    cancelled: bool = False
    live_disposer: Callable[[], None] | None = None


@dataclass(slots=True)
class ChromeHandoff:
    """Short-guard state that queues writes until acceptance selects an owner."""

    candidate: ExtensionChromeSink
    pending: list[ChromeHandoffOperation] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ChromeHandoffLease:
    """Exact installed handoff and the owner retained during acquisition."""

    previous: ExtensionChromeSink
    handoff: ChromeHandoff


@dataclass(frozen=True, slots=True)
class ChromeRoutingLease:
    """Explicit source route for synchronous reentrant chrome writes."""

    source: str
    sink: ExtensionChromeSink


class ExtensionChromeRouter:
    """Owns which sink receives extension chrome writes, and the handoff between them.

    A reload candidate takes chrome ownership from the live sink when it attaches.
    Between admitting a candidate and acceptance choosing a winner, ownership is
    undecided: writes arriving in that window queue on a :class:`ChromeHandoff` and
    are later replayed onto the accepted sink or dropped with the refused one. This
    class is that transaction and nothing else -- it never touches the terminal.

    Its single outward edge is the ``delivery`` callable given at construction. The
    live sink invokes it to apply an accepted event; everything the terminal does in
    response lives on the far side of it. That is why the transaction can live here
    while the verbs that produce events stay in the shell.
    """

    def __init__(self, delivery: ExtensionChromeDelivery) -> None:
        # Retained so a restored or re-attached sink rebinds the same edge.
        self._delivery = delivery
        self._sink_guard = threading.RLock()
        self._sink_idle = threading.Condition(self._sink_guard)
        self._active_sink = ExtensionChromeSink(delivery)
        self._active_sink_leases = 0
        self._handoff: ChromeHandoff | None = None
        self._routing_leases: ContextVar[tuple[ChromeRoutingLease, ...]] = ContextVar(
            "extension_chrome_routing_leases", default=()
        )
        self._retirement_drop_sink = ExtensionChromeSink()
        self._retirement_drop_sink.close()

    def new_candidate_sink(self) -> ExtensionChromeSink:
        """Create a detached retained-chrome sink for one reload candidate."""

        return ExtensionChromeSink()

    def startup_chrome_sink(self) -> ExtensionChromeSink:
        return self._active_sink

    def prepare_candidate(
        self, prepared: ExtensionChromePrepareInput
    ) -> ExtensionChromeCommitToken | None:
        candidate = prepared.candidate
        with candidate._guard:  # noqa: SLF001 - exact R2 sidecar owner check
            if candidate._closed:  # noqa: SLF001 - refusal precedes publication
                return None
        return ExtensionChromeCommitToken(prepared)

    def accept_candidate(
        self,
        candidate: ExtensionChromeSink,
        *,
        rollback_snapshot: ExtensionChromeSnapshot | None = None,
    ) -> ChromeAcceptanceResult:
        """Reconcile without holding the owner guard, then select one live sink.

        The short ``_sink_guard`` only starts/completes the handoff and accounts
        for already-selected writes. Writes admitted while effects run are
        queued in the handoff and replayed exactly once to whichever sink wins.
        The accepted result transfers the retired sink to the caller so cleanup
        can propagate interrupts without making candidate ownership ambiguous.
        """

        acquired = self._acquire_candidate_handoff(candidate)
        if isinstance(acquired, ChromeAcceptanceResult):
            return acquired
        previous = acquired.previous
        handoff = acquired.handoff

        try:
            try:
                attach_result = candidate.attach(self._delivery)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as candidate_error:  # noqa: BLE001 - attach failure must still roll back
                return self._recover_failed_attach(
                    candidate,
                    previous,
                    handoff,
                    candidate_error,
                    rollback_snapshot=rollback_snapshot,
                )
            if not attach_result.attached:
                return self._finish_refused_attach(
                    candidate,
                    previous,
                    attach_result,
                    handoff,
                    restore_required=attach_result.reconciled,
                    rollback_snapshot=rollback_snapshot,
                )
            self._complete_handoff(candidate, handoff)
            return ChromeAcceptanceResult(
                accepted=True,
                retired_sink=previous,
            )
        except (KeyboardInterrupt, SystemExit):
            if not self.owns_sink(candidate):
                self._complete_handoff(previous, handoff)
            raise
        except BaseException:
            if not self.owns_sink(candidate):
                self._complete_handoff(previous, handoff)
            raise

    def _recover_failed_attach(
        self,
        candidate: ExtensionChromeSink,
        previous: ExtensionChromeSink,
        handoff: ChromeHandoff,
        candidate_error: BaseException,
        *,
        rollback_snapshot: ExtensionChromeSnapshot | None,
    ) -> ChromeAcceptanceResult:
        """Roll back, retry once, and report which chrome ended up owning.

        Split out of :meth:`accept_candidate` so the happy path reads as three
        steps. Every branch here ends the handoff exactly once, and the retry is
        attempted only when the rollback itself failed -- restoring the previous
        chrome is preferred to owning a candidate whose attach already raised.
        """

        restored, restore_error = self._restore_previous_chrome(
            candidate, previous, rollback_snapshot=rollback_snapshot
        )
        if restore_error is None:
            self._complete_handoff(restored, handoff)
            return ChromeAcceptanceResult(
                accepted=False,
                diagnostic=(
                    "pipy: extension chrome reconciliation failed; kept "
                    "the previous chrome "
                    f"({type(candidate_error).__name__})."
                ),
            )
        try:
            attach_result = candidate.attach(self._delivery)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as retry_error:  # noqa: BLE001 - retry failure must still roll back
            restored, retry_restore_error = self._restore_previous_chrome(
                candidate, previous, rollback_snapshot=rollback_snapshot
            )
            self._complete_handoff(restored, handoff)
            if retry_restore_error is not None:
                return ChromeAcceptanceResult(
                    accepted=False,
                    diagnostic=(
                        "pipy: extension chrome reconciliation and "
                        "bounded recovery failed; previous chrome "
                        "ownership was retained "
                        f"({type(retry_error).__name__})."
                    ),
                )
            return ChromeAcceptanceResult(
                accepted=False,
                diagnostic=(
                    "pipy: extension chrome reconciliation failed; "
                    "restored the previous chrome after retry "
                    f"({type(candidate_error).__name__})."
                ),
            )
        if not attach_result.attached:
            return self._finish_refused_attach(
                candidate,
                previous,
                attach_result,
                handoff,
                restore_required=True,
                rollback_snapshot=rollback_snapshot,
            )
        self._complete_handoff(candidate, handoff)
        return ChromeAcceptanceResult(
            accepted=True,
            diagnostic=(
                "pipy: previous chrome restoration failed; accepted "
                "the reconciled candidate "
                f"({type(restore_error).__name__})."
            ),
            retired_sink=previous,
        )

    def _acquire_candidate_handoff(
        self, candidate: ExtensionChromeSink
    ) -> ChromeHandoffLease | ChromeAcceptanceResult:
        """Install one handoff and drain selected writes exception-safely."""

        acquired: ChromeHandoffLease | None = None
        try:
            with self._sink_guard:
                previous = self._active_sink
                if candidate is previous:
                    return ChromeAcceptanceResult(accepted=True)
                if self._handoff is not None:
                    return ChromeAcceptanceResult(
                        accepted=False,
                        diagnostic=(
                            "pipy: extension chrome handoff is already active."
                        ),
                    )
                handoff = ChromeHandoff(candidate)
                acquired = ChromeHandoffLease(previous, handoff)
                self._handoff = handoff
                while self._active_sink_leases:
                    self._sink_idle.wait()
        except BaseException:
            # Condition.wait() may raise after writes have joined the handoff.
            # Complete this exact installation only after the guard is released,
            # so retained effects replay once to the still-selected owner.
            if acquired is not None:
                self._complete_handoff(acquired.previous, acquired.handoff)
            raise
        assert acquired is not None
        return acquired

    def _restore_previous_chrome(
        self,
        candidate: ExtensionChromeSink,
        previous: ExtensionChromeSink,
        *,
        rollback_snapshot: ExtensionChromeSnapshot | None,
    ) -> tuple[ExtensionChromeSink, BaseException | None]:
        """Repaint an open owner or a detached copy of its retirement snapshot."""

        restored = previous
        try:
            if rollback_snapshot is None:
                previous.reconcile_attached(self._delivery)
            else:
                restored = ExtensionChromeSink.from_snapshot(rollback_snapshot)
                if not restored.attach(self._delivery).attached:
                    raise RuntimeError(
                        "retired chrome snapshot restoration was refused"
                    )
        except (KeyboardInterrupt, SystemExit):
            if restored is not previous:
                restored.close()
            raise
        except BaseException as restore_error:  # noqa: BLE001 - rollback reports, never raises mid-handoff
            if restored is not previous:
                try:
                    restored.close()
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException:  # noqa: BLE001 - close must not mask the restore failure
                    pass
            return previous, restore_error
        candidate.discard_reconciled_disposers()
        return restored, None

    def _finish_refused_attach(
        self,
        candidate: ExtensionChromeSink,
        previous: ExtensionChromeSink,
        attach_result: ExtensionChromeAttachResult,
        handoff: ChromeHandoff,
        *,
        restore_required: bool,
        rollback_snapshot: ExtensionChromeSnapshot | None,
    ) -> ChromeAcceptanceResult:
        """Finish an attach refusal with rollback only after candidate paint."""

        if restore_required:
            restored, restore_error = self._restore_previous_chrome(
                candidate, previous, rollback_snapshot=rollback_snapshot
            )
            self._complete_handoff(restored, handoff)
            if restore_error is not None:
                return ChromeAcceptanceResult(
                    accepted=False,
                    diagnostic=(
                        "pipy: closed extension chrome candidate and previous "
                        "chrome recovery both failed."
                    ),
                    candidate_closed=attach_result.candidate_closed,
                )
            diagnostic = (
                "pipy: extension chrome candidate closed during reconciliation; "
                "restored the previous chrome."
            )
        else:
            self._complete_handoff(previous, handoff)
            diagnostic = (
                "pipy: extension chrome candidate is closed."
                if attach_result.candidate_closed
                else "pipy: extension chrome candidate refused before reconciliation."
            )
        return ChromeAcceptanceResult(
            accepted=False,
            diagnostic=diagnostic,
            candidate_closed=attach_result.candidate_closed,
        )

    def owns_sink(self, sink: ExtensionChromeSink) -> bool:
        """Return whether ``sink`` is the selected live owner."""

        with self._sink_guard:
            return self._active_sink is sink

    def dispose_retired_sink(self, retired: ExtensionChromeSink) -> str | None:
        """Dispose a transferred owner under an explicit retiring source route."""

        try:
            with self._retiring_disposal_route():
                retired.close()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as close_error:  # noqa: BLE001 - retired-chrome cleanup is never fatal
            return (
                "pipy: accepted extension chrome; retired chrome cleanup "
                f"was incomplete ({type(close_error).__name__})."
            )
        return None

    def _complete_handoff(
        self, owner: ExtensionChromeSink, handoff: ChromeHandoff
    ) -> None:
        """Select ``owner`` and drain one exact handoff outside the guard."""

        with self._sink_guard:
            if self._handoff is not handoff:
                return
            self._active_sink = owner
        try:
            while True:
                with self._sink_guard:
                    if self._handoff is not handoff:
                        return
                    if not handoff.pending:
                        self._handoff = None
                        self._sink_idle.notify_all()
                        return
                    operation = handoff.pending.pop(0)
                self._replay_handoff_operation(owner, operation)
        except (KeyboardInterrupt, SystemExit):
            with self._sink_guard:
                if self._handoff is handoff:
                    self._handoff = None
                    self._sink_idle.notify_all()
            raise

    def _replay_handoff_operation(
        self, sink: ExtensionChromeSink, operation: ChromeHandoffOperation
    ) -> None:
        with self._sink_guard:
            if operation.cancelled:
                return
        try:
            result = self._apply_sink_operation(sink, operation)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - one bad delayed paint stays bounded
            # The originating void setter already returned while ownership was
            # undecided. Keep one bad delayed paint bounded like other TUI
            # extension effects and continue the handoff.
            return
        if operation.kind != "listener" or not callable(result):
            return
        with self._sink_guard:
            if operation.cancelled:
                stale_disposer = result
            else:
                operation.live_disposer = result
                stale_disposer = None
        if stale_disposer is not None:
            try:
                stale_disposer()
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - a stale disposer must not break the handoff
                pass

    @staticmethod
    def _apply_sink_operation(
        sink: ExtensionChromeSink, operation: ChromeHandoffOperation
    ) -> object:
        values = operation.values
        if operation.kind == "widget":
            sink.set_widget(cast(str, values[0]), values[1], cast(str, values[2]))
            return None
        if operation.kind == "header":
            sink.set_header(values[0])
            return None
        if operation.kind == "footer":
            sink.set_footer(values[0])
            return None
        if operation.kind == "title":
            sink.set_title(cast(str, values[0]))
            return None
        if operation.kind == "indicator":
            sink.set_working_indicator(values[0], values[1])
            return None
        if operation.kind == "hidden-thinking-label":
            sink.set_hidden_thinking_label(cast("str | None", values[0]))
            return None
        if operation.kind == "autocomplete":
            sink.add_autocomplete_provider(values[0])
            return None
        if operation.kind == "editor-component":
            sink.set_editor_component(values[0])
            return None
        if operation.kind == "listener":
            return sink.add_terminal_input_listener(
                cast("Callable[[str], object]", values[0])
            )
        raise AssertionError(f"unknown chrome handoff operation: {operation.kind}")

    def _route_bound_sink_operation(
        self, sink: ExtensionChromeSink, operation: ChromeHandoffOperation
    ) -> object:
        """Route a generation-bound write, honoring an explicit callback source."""

        routing_leases = self._routing_leases.get()
        target = routing_leases[-1].sink if routing_leases else sink
        return self._apply_sink_operation(target, operation)

    def _route_sink_operation(self, operation: ChromeHandoffOperation) -> object:
        routing_leases = self._routing_leases.get()
        if routing_leases:
            # A synchronous callback inherits its explicit source route. In
            # particular, writes from retiring component disposal target a
            # closed sink instead of joining candidate/retained handoff traffic.
            return self._apply_sink_operation(routing_leases[-1].sink, operation)
        with self._sink_guard:
            if self._handoff is not None:
                self._handoff.pending.append(operation)
                return None
            sink = self._active_sink
            self._active_sink_leases += 1
        try:
            return self._apply_sink_operation(sink, operation)
        finally:
            with self._sink_guard:
                self._active_sink_leases -= 1
                if not self._active_sink_leases:
                    self._sink_idle.notify_all()

    def _dispose_handoff_listener(self, operation: ChromeHandoffOperation) -> None:
        with self._sink_guard:
            operation.cancelled = True
            live_disposer = operation.live_disposer
            operation.live_disposer = None
        if live_disposer is not None:
            live_disposer()

    @contextmanager
    def _retiring_disposal_route(self) -> Iterator[None]:
        """Drop writes synchronously reentered by retiring TUI disposal.

        ``ContextVar`` tokens provide explicit nesting and exception cleanup
        without guessing callback ownership from a thread id. Other contexts,
        including concurrent retained/candidate writers, do not inherit this
        route and continue through the ordinary handoff exactly once.
        """

        current = self._routing_leases.get()
        token = self._routing_leases.set(
            (
                *current,
                ChromeRoutingLease("retiring-disposal", self._retirement_drop_sink),
            )
        )
        try:
            yield
        finally:
            self._routing_leases.reset(token)

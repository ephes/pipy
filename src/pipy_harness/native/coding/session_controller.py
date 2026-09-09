"""Headless controller for a product coding session's outer transitions.

This module owns the outer transitions of ``CodingSession.run``. The
loop skeleton and start/shutdown lifecycle live in the controller-owned
``_CodingSessionLifetime``. Both :meth:`CodingSessionController.run_loop` and
the internal persistent composition enter :meth:`CodingSessionController.open_lifetime`:
the controller fires ``session_start`` once and drives the same ``while True``
skeleton, calling the injected per-iteration
``step_once`` port and routing its returned :class:`LoopStepSignal`
(``CONTINUE`` re-enters the loop, ``BREAK`` finalizes the post-loop
``SUCCEEDED`` projection through the injected ``finalize`` port, ``RETURN_RESULT``
returns the terminate ``FAILED`` projection the step selected) — and guarantees
the once-only true-idle settle, the ``session_shutdown`` fire, terminal
extension-session close, and extension-chrome clear on every exit path (normal,
fatal, or exception). Within
each step it owns input selection and the true-idle (``agent_settled``)
boundary. A single
:meth:`CodingSessionController.select_next_step`
call reproduces the exact top-of-loop policy that previously lived inline in the
monolith:

* drain the extension outboxes, then take one queued input using the product
  priority owned by :class:`~pipy_harness.native.coding.input_queue.CodingInputQueue`;
* if nothing local/retained/provider-visible is pending and a prior run armed the
  true-idle boundary, fire ``agent_settled`` exactly once, re-drain, and re-poll
  so a settled observer's freshly enqueued prompt becomes the next run instead of
  blocking on input;
* otherwise read one fresh line through an injected reader and apply the
  ``classify_external_wake`` overlay for a registered input-stream source under a
  single ``KeyboardInterrupt`` guard spanning both, matching the deleted inline
  block; and
* return a typed, frozen :class:`CodingLoopStep` describing the selected input or
  an EOF/Ctrl-C sentinel, or idle when no fresh reader is supplied.

The controller is headless: it drives only the injected ports (the input queue,
an outbox-drain callable, a fresh-line reader callable, and the settled-event
emitter) plus its exact session-state anchor. It never touches the terminal,
renderer, ``repl_input``, extensions, providers, tools, persistence, automation,
the SDK, capture, or the workflow archive; the read_line call, footer text,
prefill rehydration, and separator printing stay in the composition root.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

import pipy_harness.native.coding.input_queue as _input_queue
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentQueuedInputPort,
)
from pipy_harness.native.coding.command_registry import classify_coding_command
from pipy_harness.native.coding.commands import (
    CodingCommandOutcome,
    CodingCommandOutcomeKind,
    CommandDispatchResolution,
    ExtensionDispatchResolution,
    ResourceDispatchKind,
    ResourceDispatchResolution,
)
from pipy_harness.native.coding.input_queue import (
    CodingInputQueue,
    CodingInputSelection,
    CodingInputSource,
)
from pipy_harness.native.coding.result import CodingSessionResult
from pipy_harness.native.coding.state import CodingSessionState

_NativeControlAbortView = _input_queue._NativeControlAbortView
_NativeControlReservation = _input_queue._NativeControlReservation
_NativeControlSnapshot = _input_queue._NativeControlSnapshot
_NativeRunClaim = _input_queue._NativeRunClaim
_NativeSessionControl = _input_queue._NativeSessionControl

_NOT_HANDLED_COMMAND_NOTICE = (
    "supported local commands are /hotkeys, /reload, "
    "/changelog, /model, /scoped-models, /settings, /trust, "
    "/login, /logout, /copy, /compact, /export, /import, "
    "/share, /session, /name, "
    "/new, /tree, /resume, /fork, /clone, /skill, "
    "/exit, /quit "
    "(plus any workspace prompt templates and custom "
    "commands as /<name>, and activated extension "
    "commands). Other prompts are sent to the model."
)


@runtime_checkable
class SettledEventEmitter(Protocol):
    """Narrow emitter port for the once-only true-idle notification."""

    def agent_settled(self) -> None: ...


class _ControllerReadinessPort:
    """Private callback port for the post-settlement, re-polled idle point."""

    __slots__ = ("_callback", "_control", "_lock")

    def __init__(self, control: _NativeSessionControl) -> None:
        if type(control) is not _NativeSessionControl:
            raise TypeError("control must be a _NativeSessionControl")
        self._lock = _input_queue._new_native_control_lock()
        self._control = control
        self._callback: Callable[[], None] | None = None

    @property
    def control(self) -> _NativeSessionControl:
        return self._control

    def bind(self, callback: Callable[[], None]) -> None:
        if not callable(callback):
            raise TypeError("readiness callback must be callable")
        with self._lock:
            if self._callback is not None:
                raise RuntimeError("controller readiness port is already bound")
            self._callback = callback

    def publish(self) -> None:
        """Invoke a bound publication callback after queue polling has completed."""

        with self._lock:
            callback = self._callback
        if callback is not None:
            callback()


@dataclass(frozen=True, slots=True)
class _NativeControlReady:
    control: _NativeSessionControl
    abort_view: _NativeControlAbortView
    readiness_port: _ControllerReadinessPort
    configuration_port: object
    compaction_port: object
    retry_port: object
    transition_port: object


@dataclass(frozen=True, slots=True)
class _NativeControlFailed:
    error: BaseException


class _NativeTransitionControlError(RuntimeError):
    """A private RPC transition control invariant failed after admission."""


class _NativeSessionControlBridge:
    """One-shot startup and exact-run handoff for a future transport worker.

    The bridge is deliberately private and dormant until a transport provides it
    through the existing abort/input composition path.  Its only mutable run
    field is the currently attached immutable queue claim.
    """

    __slots__ = (
        "_claim",
        "_event",
        "_fatal",
        "_manual_handler",
        "_outcome",
        "_outcome_lock",
        "_pending_selected",
        "_retired",
        "_transition_admit",
        "_transition_settle",
        "_wake_reader",
        "_worker",
    )

    def __init__(self) -> None:
        self._event = _input_queue._new_native_control_event()
        self._outcome_lock = _input_queue._new_native_control_lock()
        self._outcome: _NativeControlReady | _NativeControlFailed | None = None
        self._worker: object | None = None
        self._claim: _NativeRunClaim | None = None
        self._pending_selected: (
            tuple[ProductContent, AgentQueuedInput | None] | None
        ) = None
        self._retired = False
        self._transition_admit: (
            Callable[[Callable[[], None]], _NativeRunClaim | None] | None
        ) = None
        self._transition_settle: (
            Callable[[_NativeRunClaim], _NativeControlSnapshot] | None
        ) = None
        self._wake_reader: Callable[[], str] | None = None
        self._fatal = False
        self._manual_handler: (
            Callable[
                [_NativeRunClaim, ProductContent | None],
                Callable[[_NativeControlSnapshot], None],
            ]
            | None
        ) = None

    def bind_manual_compaction_handler(
        self,
        handler: Callable[
            [_NativeRunClaim, ProductContent | None],
            Callable[[_NativeControlSnapshot], None],
        ],
    ) -> None:
        if not callable(handler):
            raise TypeError("manual compaction handler must be callable")
        with self._outcome_lock:
            if self._manual_handler is not None:
                raise RuntimeError("manual compaction handler is already bound")
            self._manual_handler = handler

    def bind_wake_reader(self, reader: Callable[[], str]) -> None:
        """Bind the transport's wake/EOF reader before worker startup."""

        if not callable(reader):
            raise TypeError("wake reader must be callable")
        with self._outcome_lock:
            if self._wake_reader is not None:
                raise RuntimeError("native control bridge wake reader is already bound")
            self._wake_reader = reader

    def bind_transition_control(
        self,
        admit: Callable[[Callable[[], None]], _NativeRunClaim | None],
        settle: Callable[[_NativeRunClaim], _NativeControlSnapshot],
    ) -> None:
        """Bind controller-owned transition admission and exact settlement."""

        if not callable(admit) or not callable(settle):
            raise TypeError("native RPC transition control ports must be callable")
        with self._outcome_lock:
            if (
                self._transition_admit is not None
                or self._transition_settle is not None
            ):
                raise RuntimeError("native RPC transition control is already bound")
            self._transition_admit = admit
            self._transition_settle = settle

    def readline(self, *_args: object) -> str:
        """Wait for a wake, then select one exact native reservation."""

        reader = self._wake_reader
        if reader is None:
            raise RuntimeError("native control bridge wake reader is not bound")
        wait_for_wake = True
        for _ in iter(lambda: True, False):
            if wait_for_wake:
                wake = reader()
                if wake:
                    raise RuntimeError(
                        "native control wake reader returned payload content"
                    )
            with self._outcome_lock:
                if self._retired:
                    return ""
            ready = self._ready()
            reservation = ready.control.snapshot().reservation
            if reservation is None:
                return ""
            claim = self.attach_selected_claim(reservation)
            if claim is None:
                continue
            if (
                claim.manual_compaction is not None
                or reservation.manual_compaction is not None
            ):
                handler = self._manual_handler
                if handler is None:
                    raise RuntimeError("native manual compaction handler is not bound")
                publish = handler(claim, claim.manual_compaction)
                snapshot = self.consume_attached_agent_end_and_publish(publish)
                # A prompt admitted behind compaction is promoted under the
                # same settlement gate. Consume it directly on this worker;
                # enqueueing another wake here would leave stale transport work
                # after the successor has already been selected.
                wait_for_wake = snapshot.reservation is None
                continue
            queued = (
                None
                if claim.kind is None
                else AgentQueuedInput(claim.content, claim.kind)
            )
            self._pending_selected = (claim.content, queued)
            return ""
        raise AssertionError("infinite native control drain terminated")

    def take_next(self) -> AgentQueuedInput | None:
        """Active-loop polling cannot consume a transport-owned reservation."""

        return None

    def take_selected(
        self,
    ) -> tuple[ProductContent, AgentQueuedInput | None] | None:
        selected = self._pending_selected
        self._pending_selected = None
        return selected

    def snapshot(self) -> _NativeControlSnapshot:
        return self._ready().control.snapshot()

    def publish_if_true_idle(self, publish: Callable[[], None]) -> bool:
        return self._ready().control.publish_if_true_idle(publish)

    def retire(self) -> None:
        """Stop bridge intake after an unrecoverable transport transition error."""

        with self._outcome_lock:
            self._retired = True

    def publish_ready(
        self,
        control: _NativeSessionControl,
        abort_view: _NativeControlAbortView,
        readiness_port: _ControllerReadinessPort,
        configuration_port: object,
        compaction_port: object,
        retry_port: object,
        transition_port: object,
    ) -> None:
        if type(control) is not _NativeSessionControl:
            raise TypeError("control must be a _NativeSessionControl")
        if type(abort_view) is not _NativeControlAbortView:
            raise TypeError("abort_view must be a _NativeControlAbortView")
        if type(readiness_port) is not _ControllerReadinessPort:
            raise TypeError("readiness_port must be a _ControllerReadinessPort")
        if abort_view is not control.abort_view:
            raise ValueError("abort_view must belong to the published control")
        if readiness_port.control is not control:
            raise ValueError("readiness_port must belong to the published control")
        if configuration_port is None:
            raise TypeError("configuration_port must not be None")
        if compaction_port is None:
            raise TypeError("compaction_port must not be None")
        if retry_port is None:
            raise TypeError("retry_port must not be None")
        if transition_port is None:
            raise TypeError("transition_port must not be None")
        self._publish(
            _NativeControlReady(
                control,
                abort_view,
                readiness_port,
                configuration_port,
                compaction_port,
                retry_port,
                transition_port,
            )
        )

    def publish_failure(self, error: BaseException) -> None:
        if not isinstance(error, BaseException):
            raise TypeError("error must be a BaseException")
        self._publish(_NativeControlFailed(error))

    def publish_failure_if_unpublished(self, error: BaseException) -> bool:
        """Publish a startup failure unless composition already published an outcome."""

        if not isinstance(error, BaseException):
            raise TypeError("error must be a BaseException")
        with self._outcome_lock:
            if self._outcome is not None:
                return False
            self._outcome = _NativeControlFailed(error)
            self._event.set()
            return True

    def _publish(self, outcome: _NativeControlReady | _NativeControlFailed) -> None:
        with self._outcome_lock:
            if self._outcome is not None:
                raise RuntimeError("native control bridge outcome is already published")
            self._outcome = outcome
            self._event.set()

    def wait_ready(
        self, timeout: float | None = None
    ) -> _NativeControlReady | _NativeControlFailed | None:
        if not self._event.wait(timeout):
            return None
        with self._outcome_lock:
            outcome = self._outcome
        if outcome is None:
            raise RuntimeError("signalled native control bridge has no outcome")
        return outcome

    def admit_prompt(
        self, content: ProductContent, *, steer_active: bool = False
    ) -> _NativeControlSnapshot:
        ready = self._ready()
        return ready.control._bridge_admit_prompt(
            content,
            steer_active=steer_active,
            verify_authorized=self._require_authorized,
        )

    def request_abort(self) -> _NativeControlSnapshot:
        return self._ready().control.request_abort()

    def admit_steering(self, content: ProductContent) -> _NativeControlSnapshot:
        return self._ready().control._bridge_admit_steering(
            content, self._require_authorized
        )

    def admit_follow_up(self, content: ProductContent) -> _NativeControlSnapshot:
        return self._ready().control._bridge_admit_follow_up(
            content, self._require_authorized
        )

    def admit_manual_compaction(
        self, custom_instructions: ProductContent | None
    ) -> _NativeControlSnapshot | None:
        return self._ready().control.admit_manual_compaction(custom_instructions)

    def admit_transition_operation(self) -> _NativeRunClaim | None:
        """Reserve and claim an idle-only transport control operation.

        The opaque claim is returned only to the native transition port.  The
        queue and its admission gate are released before the port invokes any
        extension, filesystem, lease, publication, or rebuild callback.
        """

        self._ready()
        admit = self._transition_admit
        if admit is None:
            raise RuntimeError("native RPC transition control is not bound")
        return admit(self._require_authorized)

    def settle_transition_operation(
        self, claim: _NativeRunClaim
    ) -> _NativeControlSnapshot:
        self._ready()
        settle = self._transition_settle
        if settle is None:
            raise RuntimeError("native RPC transition control is not bound")
        return settle(claim)

    def attach_selected_claim(
        self, reservation: _NativeControlReservation
    ) -> _NativeRunClaim | None:
        ready = self._ready()
        worker = _input_queue._native_control_current_thread()

        def attach(claim: _NativeRunClaim) -> None:
            if self._worker is None:
                self._worker = worker
            self._claim = claim

        def verify_attachable() -> None:
            self._require_authorized()
            if self._claim is not None:
                raise RuntimeError("native worker already has an attached run claim")
            if self._worker is not None and self._worker is not worker:
                self._fail("native worker changed while attaching a claim")

        return ready.control._claim_and_attach(reservation, verify_attachable, attach)

    def consume_agent_end(self, claim: _NativeRunClaim) -> _NativeControlSnapshot:
        ready = self._ready()
        return ready.control._consume_and_settle(claim, lambda: self._consume(claim))

    def consume_attached_agent_end(self) -> _NativeControlSnapshot:
        claim = self._claim
        if claim is None:
            raise RuntimeError("native worker has no claim for agent_end")
        return self.consume_agent_end(claim)

    def consume_attached_agent_end_and_publish(
        self, publish: Callable[[_NativeControlSnapshot], None]
    ) -> _NativeControlSnapshot:
        if not callable(publish):
            raise TypeError("publish must be callable")
        claim = self._claim
        if claim is None:
            raise RuntimeError("native worker has no claim for agent_end")
        ready = self._ready()
        return ready.control._consume_settle_and_publish(
            claim, lambda: self._consume(claim), publish
        )

    def cleanup_attached_failed_run(self, primary: BaseException) -> None:
        """Settle a pre-end claim while preserving the worker's primary failure."""

        if not isinstance(primary, BaseException):
            raise TypeError("primary must be a BaseException")
        if self._claim is None:
            return
        try:
            self.consume_attached_agent_end()
        except BaseException:  # noqa: BLE001 - preserve the original failure
            primary.add_note("native control cleanup settlement also failed")

    def cleanup_failed_run(
        self,
        claim: _NativeRunClaim,
        primary: BaseException,
        retire_lifetime: Callable[[], None],
    ) -> None:
        """Settle the exact attached claim, then retire after a pre-end failure."""

        if not isinstance(primary, BaseException):
            raise TypeError("primary must be a BaseException")
        if not callable(retire_lifetime):
            raise TypeError("retire_lifetime must be callable")
        try:
            self.consume_agent_end(claim)
        except BaseException:  # noqa: BLE001 - preserve the original run failure
            primary.add_note("native control cleanup settlement also failed")
        try:
            retire_lifetime()
        except BaseException:  # noqa: BLE001 - preserve the original run failure
            primary.add_note("native control cleanup lifetime retirement also failed")

    def _ready(self) -> _NativeControlReady:
        with self._outcome_lock:
            outcome = self._outcome
        if type(outcome) is not _NativeControlReady:
            raise RuntimeError("native control bridge is not ready")
        return outcome

    def _require_authorized(self) -> None:
        """Check bridge-fatal state while the matching control gate is held."""

        if self._fatal:
            raise RuntimeError("native control bridge has failed an invariant")

    def _consume(self, claim: _NativeRunClaim) -> None:
        self._require_authorized()
        if self._worker is not _input_queue._native_control_current_thread():
            self._fail("native claim consumption is on a foreign worker thread")
        if self._claim is None:
            self._fail("native worker has no attached run claim")
        if self._claim is not claim or self._claim.token is not claim.token:
            self._fail("native worker claim does not match the selected run")
        self._claim = None

    def _fail(self, message: str) -> None:
        self._fatal = True
        raise RuntimeError(message)


@runtime_checkable
class CodingCommandEffects(Protocol):
    """Injected effect port for the command-dispatch precedence.

    Every method performs a product effect the headless controller may not reach
    directly (terminal/renderer diagnostics, footer painting, resource/extension
    dispatch, the resource-invocation counter, and the continuing built-in's
    imperative per-action effect chain). The controller owns only the ordering/
    precedence and the resulting
    :class:`~pipy_harness.native.coding.commands.CommandDispatchResolution`.
    The concrete callable adapter is owned beside this port, while the product
    effects it invokes remain injected from the composition root.
    """

    def emit_diagnostic(self, message: str) -> None: ...

    def refresh_footer(self) -> None: ...

    def interpret_builtin(self, outcome: CodingCommandOutcome) -> None: ...

    def record_resource_invocation(self) -> None: ...

    def dispatch_resource(
        self, command_text: str
    ) -> ResourceDispatchResolution | None: ...

    def dispatch_extension(
        self, command_text: str
    ) -> ExtensionDispatchResolution | None: ...


class _CallableCodingCommandEffects:
    """Composition-root :class:`CodingCommandEffects` port over run() closures.

    The controller owns the built-in>resource>extension precedence; this adapter
    performs each effect (diagnostics, footer painting, the resource-invocation
    counter, and resource/extension dispatch) through callables that close over
    the live run-loop state, so a ``/reload`` that rebinds the workspace
    resources or extension registry is reflected on the next dispatch.
    """

    __slots__ = (
        "_emit",
        "_footer",
        "_interpret",
        "_record_resource",
        "_resolve_extension",
        "_resolve_resource",
    )

    def __init__(
        self,
        *,
        emit: Callable[[str], None],
        footer: Callable[[], None],
        interpret: Callable[[CodingCommandOutcome], None],
        record_resource: Callable[[], None],
        resolve_resource: Callable[[str], ResourceDispatchResolution | None],
        resolve_extension: Callable[[str], ExtensionDispatchResolution | None],
    ) -> None:
        self._emit = emit
        self._footer = footer
        self._interpret = interpret
        self._record_resource = record_resource
        self._resolve_resource = resolve_resource
        self._resolve_extension = resolve_extension

    def emit_diagnostic(self, message: str) -> None:
        self._emit(message)

    def refresh_footer(self) -> None:
        self._footer()

    def interpret_builtin(self, outcome: CodingCommandOutcome) -> None:
        self._interpret(outcome)

    def record_resource_invocation(self) -> None:
        self._record_resource()

    def dispatch_resource(self, command_text: str) -> ResourceDispatchResolution | None:
        return self._resolve_resource(command_text)

    def dispatch_extension(
        self, command_text: str
    ) -> ExtensionDispatchResolution | None:
        return self._resolve_extension(command_text)


class CodingLoopStepKind(Enum):
    """Closed classification of one outer-loop step selected by the controller."""

    LOCAL_COMMAND = "local_command"
    RETAINED_FRESH = "retained_fresh"
    PROVIDER_CONTENT = "provider_content"
    FRESH_LINE = "fresh_line"
    EOF = "eof"
    IDLE = "idle"


@dataclass(frozen=True, slots=True)
class CodingLoopStep:
    """One outer-loop step: selected input, EOF/Ctrl-C, or explicit idle.

    ``line`` is the exact line the composition loop consumes next. It is empty
    only for :attr:`CodingLoopStepKind.EOF` or :attr:`CodingLoopStepKind.IDLE`.
    ``selected_provider_content`` and
    ``queued_input`` are carried only for provider-visible content. ``settle_pending``
    is the post-boundary value of the run's true-idle flag; the composition loop
    assigns it straight back so the inline ``try/finally`` observes the exact
    once-only ``agent_settled`` timing. ``keyboard_interrupt`` is set only for an
    EOF sentinel produced by a ``KeyboardInterrupt`` during the fresh read.
    """

    kind: CodingLoopStepKind
    line: str
    settle_pending: bool
    selected_provider_content: ProductContent | None = None
    queued_input: AgentQueuedInput | None = None
    keyboard_interrupt: bool = False

    def __post_init__(self) -> None:
        _require_exact_coding_loop_step_fields(self)
        _require_coding_loop_step_payload(self)

    @classmethod
    def local_command(cls, line: str, settle_pending: bool) -> CodingLoopStep:
        return cls(CodingLoopStepKind.LOCAL_COMMAND, line, settle_pending)

    @classmethod
    def retained_fresh(cls, line: str, settle_pending: bool) -> CodingLoopStep:
        return cls(CodingLoopStepKind.RETAINED_FRESH, line, settle_pending)

    @classmethod
    def provider_content(
        cls,
        line: str,
        content: ProductContent,
        queued_input: AgentQueuedInput | None,
        settle_pending: bool,
    ) -> CodingLoopStep:
        return cls(
            CodingLoopStepKind.PROVIDER_CONTENT,
            line,
            settle_pending,
            selected_provider_content=content,
            queued_input=queued_input,
        )

    @classmethod
    def fresh_line(cls, line: str, settle_pending: bool) -> CodingLoopStep:
        return cls(CodingLoopStepKind.FRESH_LINE, line, settle_pending)

    @classmethod
    def idle(cls, *, settle_pending: bool) -> CodingLoopStep:
        return cls(CodingLoopStepKind.IDLE, "", settle_pending)

    @classmethod
    def eof(cls, *, settle_pending: bool, keyboard_interrupt: bool) -> CodingLoopStep:
        return cls(
            CodingLoopStepKind.EOF,
            "",
            settle_pending,
            keyboard_interrupt=keyboard_interrupt,
        )


class LoopStepSignalKind(Enum):
    """Closed classification of one per-iteration outcome the step port returns."""

    CONTINUE = "continue"
    IDLE = "idle"
    BREAK = "break"
    RETURN_RESULT = "return_result"


@dataclass(frozen=True, slots=True)
class LoopStepSignal:
    """The routing signal one ``step_once`` call returns to :meth:`run_loop`.

    ``IDLE`` yields without finalizing. ``CONTINUE`` re-enters the loop (the
    composition step handled the iteration itself), ``BREAK`` ends the loop through the ``finalize`` port (the post-loop
    ``SUCCEEDED`` projection), and ``RETURN_RESULT`` ends the loop returning the
    exact bounded :class:`CodingSessionResult` the step already built (the
    terminate ``FAILED`` projection). ``result`` is carried only for
    ``RETURN_RESULT``.
    """

    kind: LoopStepSignalKind
    result: CodingSessionResult | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not LoopStepSignalKind:
            raise TypeError("kind must be an exact LoopStepSignalKind")
        if self.kind is LoopStepSignalKind.RETURN_RESULT:
            if type(self.result) is not CodingSessionResult:
                raise TypeError("RETURN_RESULT requires an exact CodingSessionResult")
        elif self.result is not None:
            raise ValueError("only RETURN_RESULT carries a result")

    @classmethod
    def continue_loop(cls) -> LoopStepSignal:
        return cls(LoopStepSignalKind.CONTINUE)

    @classmethod
    def idle(cls) -> LoopStepSignal:
        return cls(LoopStepSignalKind.IDLE)

    @classmethod
    def break_loop(cls) -> LoopStepSignal:
        return cls(LoopStepSignalKind.BREAK)

    @classmethod
    def return_result(cls, result: CodingSessionResult) -> LoopStepSignal:
        return cls(LoopStepSignalKind.RETURN_RESULT, result)


class CodingSessionController:
    """Own the input-selection and true-idle transitions for one coding session.

    The controller is constructed once per run from the session's already-owned
    input queue, its exact :class:`CodingSessionState` anchor, and the
    settled-event emitter. The composition loop calls :meth:`select_next_step`
    each iteration and consumes the returned :class:`CodingLoopStep`; the loop
    skeleton, lifecycle firing, command dispatch, run transition, and result
    building remain in the composition root.
    """

    __slots__ = (
        "_cleanup_failed_run",
        "_configuration_port",
        "_compaction_port",
        "_control_bridge",
        "_coding_state",
        "_control",
        "_emitter",
        "_input_queue",
        "_readiness_port",
        "_publish_ready",
        "_retry_port",
        "_finish_transition_lease",
        "_transition_port",
    )

    def __init__(
        self,
        *,
        input_queue: CodingInputQueue,
        coding_state: CodingSessionState,
        emitter: SettledEventEmitter,
        control: _NativeSessionControl | None = None,
    ) -> None:
        if not isinstance(input_queue, CodingInputQueue):
            raise TypeError("input_queue must be a CodingInputQueue")
        if type(coding_state) is not CodingSessionState:
            raise TypeError("coding_state must be an exact CodingSessionState")
        self._input_queue = input_queue
        self._coding_state = coding_state
        self._emitter = emitter
        if control is not None and type(control) is not _NativeSessionControl:
            raise TypeError("control must be a _NativeSessionControl or None")
        if control is not None and control._queue is not input_queue:
            raise ValueError("control must own the controller input queue")
        self._control = control or _NativeSessionControl(input_queue)
        self._readiness_port = _ControllerReadinessPort(self._control)
        self._publish_ready: Callable[[], None] | None = None
        self._cleanup_failed_run: Callable[[BaseException], None] | None = None
        self._configuration_port: object | None = None
        self._compaction_port: object | None = None
        self._control_bridge: _NativeSessionControlBridge | None = None
        self._retry_port: object | None = None
        self._transition_port: object | None = None
        self._finish_transition_lease: Callable[[], None] | None = None

    @property
    def control(self) -> _NativeSessionControl:
        """Return the private native control composed for this lifetime."""

        return self._control

    @property
    def readiness_port(self) -> _ControllerReadinessPort:
        """Return the unbound post-settlement true-idle publication port."""

        return self._readiness_port

    def bind_native_control_bridge(self, bridge: _NativeSessionControlBridge) -> None:
        """Bind the private transport bridge before the stream loop starts."""

        if type(bridge) is not _NativeSessionControlBridge:
            raise TypeError("bridge must be a _NativeSessionControlBridge")
        if self._publish_ready is not None or self._cleanup_failed_run is not None:
            raise RuntimeError("native control bridge is already bound")

        def publish_ready() -> None:
            if self._configuration_port is None:
                raise RuntimeError("native RPC configuration port is not bound")
            if self._compaction_port is None:
                raise RuntimeError("native RPC compaction port is not bound")
            if self._retry_port is None:
                raise RuntimeError("native RPC retry port is not bound")
            if self._transition_port is None:
                raise RuntimeError("native RPC transition port is not bound")
            bridge.publish_ready(
                self._control,
                self._control.abort_view,
                self._readiness_port,
                self._configuration_port,
                self._compaction_port,
                self._retry_port,
                self._transition_port,
            )

        self._publish_ready = publish_ready
        self._cleanup_failed_run = bridge.cleanup_attached_failed_run
        self._control_bridge = bridge

    def bind_rpc_configuration_port(self, port: object) -> None:
        """Bind the once-composed private configuration projection for RPC.

        The bridge publishes readiness only after this value and its queue
        control are both available.  It remains opaque to the controller.
        """

        if port is None:
            raise TypeError("native RPC configuration port must not be None")
        if self._configuration_port is not None:
            raise RuntimeError("native RPC configuration port is already bound")
        self._configuration_port = port

    def bind_rpc_compaction_port(self, port: object) -> None:
        if port is None:
            raise TypeError("native RPC compaction port must not be None")
        if self._compaction_port is not None:
            raise RuntimeError("native RPC compaction port is already bound")
        self._compaction_port = port

    def bind_rpc_retry_port(self, port: object) -> None:
        if port is None:
            raise TypeError("native RPC retry port must not be None")
        if self._retry_port is not None:
            raise RuntimeError("native RPC retry port is already bound")
        self._retry_port = port

    def bind_rpc_transition_port(
        self, port: object, finish_transition_lease: Callable[[], None]
    ) -> None:
        if port is None:
            raise TypeError("native RPC transition port must not be None")
        if not callable(finish_transition_lease):
            raise TypeError("native RPC transition lease finisher must be callable")
        if self._transition_port is not None:
            raise RuntimeError("native RPC transition port is already bound")
        self._transition_port = port
        self.bind_transition_lease_finisher(finish_transition_lease)
        bridge = self._control_bridge
        if bridge is not None:
            bridge.bind_transition_control(
                self._admit_rpc_transition_operation,
                self._settle_rpc_transition_operation,
            )

    def bind_transition_lease_finisher(
        self, finish_transition_lease: Callable[[], None]
    ) -> None:
        """Bind the one active canonical-session lease to this lifetime."""

        if not callable(finish_transition_lease):
            raise TypeError("native transition lease finisher must be callable")
        if self._finish_transition_lease is not None:
            raise RuntimeError("native transition lease finisher is already bound")
        self._finish_transition_lease = finish_transition_lease

    def _admit_rpc_transition_operation(
        self, verify_authorized: Callable[[], None]
    ) -> _NativeRunClaim | None:
        if not callable(verify_authorized):
            raise TypeError("verify_authorized must be callable")
        box: list[_input_queue._ExternalClaim] = []

        def reserve() -> None:
            verify_authorized()
            claim = self._input_queue._begin_external_operation(ProductContent(""))
            if claim is None:
                raise RuntimeError("native transition admission invariant failed")
            box.append(claim)

        if not self._control.publish_if_true_idle(reserve):
            return None
        return _NativeRunClaim(box[0])

    def _settle_rpc_transition_operation(
        self, claim: _NativeRunClaim
    ) -> _NativeControlSnapshot:
        return self._control.settle(claim)

    def run_loop(
        self,
        *,
        step_once: Callable[[], LoopStepSignal],
        finalize: Callable[[], CodingSessionResult],
        fire_session_start: Callable[[], None],
        fire_session_shutdown: Callable[[], None],
        consume_settle_pending: Callable[[], bool],
        close_extension_session: Callable[[], None],
        clear_extension_chrome: Callable[[], None],
    ) -> CodingSessionResult:
        """Own the ``while True`` skeleton and the start/shutdown lifecycle.

        The controller owns the loop skeleton and the lifecycle bookends of one
        product coding session: it fires ``session_start`` once the session is
        set up, runs the ``while True`` itself, and on each iteration calls the
        injected ``step_once`` port and routes its returned
        :class:`LoopStepSignal` — ``CONTINUE`` re-enters the loop, ``BREAK`` ends
        it through the injected ``finalize`` port (the post-loop ``SUCCEEDED``
        projection), and ``RETURN_RESULT`` ends it returning the exact bounded
        :class:`CodingSessionResult` the step already built (the terminate
        ``FAILED`` projection). On every exit path (normal return, fatal return,
        or a propagated exception) it guarantees the once-only true-idle settle,
        the ``session_shutdown`` fire, terminal extension-session close, and the
        extension-chrome clear — in that order.

        Every effect is performed through an injected port so the controller
        never touches the terminal, renderer, ``repl_input``, extensions,
        providers, tools, persistence, automation, the SDK, capture, or the
        workflow archive: ``step_once`` performs one iteration's composition-root
        work and returns only the routing signal, ``finalize`` builds the
        post-loop ``SUCCEEDED`` projection, ``fire_session_start``/
        ``fire_session_shutdown`` fire the composition root's lifecycle emitter,
        ``consume_settle_pending`` reads-and-resets the run's armed true-idle flag
        (the controller fires the once-only ``agent_settled`` through its own
        settled emitter when it returns ``True``), ``close_extension_session``
        closes coding-effect admission and generation sidecars, and
        ``clear_extension_chrome`` clears any live TUI chrome.
        """

        _require_run_loop_ports(
            step_once=step_once,
            finalize=finalize,
            fire_session_start=fire_session_start,
            fire_session_shutdown=fire_session_shutdown,
            consume_settle_pending=consume_settle_pending,
            close_extension_session=close_extension_session,
            clear_extension_chrome=clear_extension_chrome,
        )

        with self.open_lifetime(
            finalize=finalize,
            fire_session_start=fire_session_start,
            fire_session_shutdown=fire_session_shutdown,
            consume_settle_pending=consume_settle_pending,
            close_extension_session=close_extension_session,
            clear_extension_chrome=clear_extension_chrome,
            publish_ready=self._publish_ready,
            cleanup_failed_run=self._cleanup_failed_run,
        ) as lifetime:
            result = lifetime.drive(step_once)
            if result is None:
                raise RuntimeError("a stream-driven session cannot yield idle")
            return result

    @contextmanager
    def open_lifetime(
        self,
        *,
        finalize: Callable[[], CodingSessionResult],
        fire_session_start: Callable[[], None],
        fire_session_shutdown: Callable[[], None],
        consume_settle_pending: Callable[[], bool],
        close_extension_session: Callable[[], None],
        clear_extension_chrome: Callable[[], None],
        publish_ready: Callable[[], None] | None = None,
        cleanup_failed_run: Callable[[BaseException], None] | None = None,
    ) -> Iterator[_CodingSessionLifetime]:
        """Keep one controller lifetime open across explicit idle yields.

        Startup failure has no shutdown bookend, matching the stream driver.
        All exits after successful startup retire through the same once-only
        cleanup, including exceptions between drives.
        """

        _require_run_loop_ports(
            step_once=LoopStepSignal.idle,
            finalize=finalize,
            fire_session_start=fire_session_start,
            fire_session_shutdown=fire_session_shutdown,
            consume_settle_pending=consume_settle_pending,
            close_extension_session=close_extension_session,
            clear_extension_chrome=clear_extension_chrome,
        )
        lifetime = _CodingSessionLifetime(
            input_queue=self._input_queue,
            emitter=self._emitter,
            finalize=finalize,
            fire_session_shutdown=fire_session_shutdown,
            consume_settle_pending=consume_settle_pending,
            close_extension_session=close_extension_session,
            clear_extension_chrome=clear_extension_chrome,
            cleanup_failed_run=cleanup_failed_run,
            finish_transition_lease=self._finish_transition_lease,
        )
        started = False
        try:
            fire_session_start()
            started = True
            if publish_ready is not None:
                publish_ready()
        except BaseException:
            if started:
                lifetime._retire_lifetime()
            else:
                lifetime._finish_transition_lease()
            raise
        try:
            yield lifetime
        except BaseException:
            lifetime._retire_lifetime()
            raise
        else:
            lifetime.close()

    def select_next_step(
        self,
        *,
        settle_pending: bool,
        drain_outbox: Callable[[], None],
        read_fresh_line: Callable[[], str] | None,
        input_queued_input_port: AgentQueuedInputPort | None,
    ) -> CodingLoopStep:
        """Select input or idle using the exact product priority.

        A missing fresh reader yields idle after settlement and re-poll; an
        actual reader returning an empty string remains EOF.

        ``settle_pending`` is the run's current true-idle flag; the returned step
        carries its post-boundary value. ``drain_outbox`` drains extension-enqueued
        messages, ``read_fresh_line`` reads one fresh line (returning ``""`` at EOF
        and raising ``KeyboardInterrupt`` on Ctrl-C), and ``input_queued_input_port``
        is the optional registered input-stream source used for the
        ``classify_external_wake`` overlay.
        """

        if not callable(drain_outbox):
            raise TypeError("drain_outbox must be callable")
        if read_fresh_line is not None and not callable(read_fresh_line):
            raise TypeError("read_fresh_line must be callable")

        drain_outbox()
        step = self._step_from_selection(self._input_queue.take_next(), settle_pending)
        if step is not None:
            return step

        # No local/retained/provider-visible input is pending. If a prior run
        # armed the true-idle boundary, fire ``agent_settled`` exactly once, then
        # re-drain and re-poll so a settled observer's newly scheduled prompt
        # becomes the next run instead of blocking on fresh input.
        if settle_pending:
            settle_pending = False
            self._emitter.agent_settled()
            drain_outbox()
            step = self._step_from_selection(
                self._input_queue.take_next(), settle_pending
            )
            if step is not None:
                return step
            # The later transport adoption publishes its protocol-idle record
            # through this private port.  Selection has re-drained and re-polled;
            # no queue guard is held while a bound callback runs.
            self._readiness_port.publish()

        if read_fresh_line is None:
            return CodingLoopStep.idle(settle_pending=settle_pending)

        # The fresh read AND the external-wake overlay share one
        # ``KeyboardInterrupt`` guard, exactly as the superseded inline block did:
        # a Ctrl-C landing during ``classify_external_wake``'s non-blocking poll /
        # line comparison converts to the same clean EOF-break-with-newline path as
        # one landing during the blocking read, rather than propagating out through
        # the run's ``finally`` as an observably different exit.
        try:
            line = read_fresh_line()
            wake = (
                self._input_queue.classify_external_wake(input_queued_input_port, line)
                if input_queued_input_port is not None
                else None
            )
        except KeyboardInterrupt:
            return CodingLoopStep.eof(
                settle_pending=settle_pending, keyboard_interrupt=True
            )
        if wake is not None:
            return self._step_from_wake(wake, settle_pending)
        if not line:
            return CodingLoopStep.eof(
                settle_pending=settle_pending, keyboard_interrupt=False
            )
        return CodingLoopStep.fresh_line(line, settle_pending)

    def _step_from_selection(
        self,
        selection: CodingInputSelection | None,
        settle_pending: bool,
    ) -> CodingLoopStep | None:
        if selection is None:
            return None
        if selection.source is CodingInputSource.LOCAL_COMMAND:
            return CodingLoopStep.local_command(
                f"{selection.content.value}\n", settle_pending
            )
        if selection.source is CodingInputSource.RETAINED_FRESH_INPUT:
            return CodingLoopStep.retained_fresh(
                selection.content.value, settle_pending
            )
        return CodingLoopStep.provider_content(
            f"{selection.content.value}\n",
            selection.content,
            selection.queued_input,
            settle_pending,
        )

    def dispatch_command(
        self,
        *,
        command_text: str,
        stripped: str,
        user_input: str,
        selected_provider_content: ProductContent | None,
        effects: CodingCommandEffects,
    ) -> CommandDispatchResolution:
        """Resolve the built-in>resource>extension command precedence.

        The controller owns the full ordering/precedence and outcome routing.
        Built-in commands are classified FIRST so a resource or extension can
        never shadow them: ``/exit``/``/quit`` resolve to ``EXIT_LOOP`` (the
        composition loop breaks), and every other continuing built-in is
        interpreted through the injected :meth:`CodingCommandEffects.interpret_builtin`
        port — the composition-root per-action effect chain that reassigns the
        run's control state — after which the controller returns ``CONTINUE_LOOP``,
        so built-in interpretation runs through the same port as resource and
        extension dispatch and the outcome never crosses back to the composition
        loop as data. The classification runs only when the composition loop
        would have run it inline — for typed input, or an empty submission —
        never for non-empty provider/queued content, whose blank ``command_text``
        must reach the provider verbatim and therefore falls straight through to
        ``PROCEED_TO_RUN``.

        A non-built-in ``/…`` (or plain prompt) then runs resource dispatch
        (a list/reject is consumed locally; a run records the invocation counter
        and carries the bounded provider text), then extension dispatch (so a
        custom command can never shadow a built-in or a resource), then the
        unhandled ``/…`` fallback; every effect is performed through the injected
        :class:`CodingCommandEffects` port.
        """

        if not isinstance(effects, CodingCommandEffects):
            raise TypeError("effects must implement CodingCommandEffects")

        builtin = _dispatch_builtin_command(
            command_text=command_text,
            stripped=stripped,
            selected_provider_content=selected_provider_content,
            effects=effects,
        )
        if builtin is not None:
            return builtin

        resource, resource_provider_text = _dispatch_resource_command(
            command_text, effects
        )
        if resource is not None:
            return resource

        if resource_provider_text is None:
            extension = _dispatch_extension_command(command_text, effects)
            if extension is not None:
                return extension
        if command_text.startswith("/") and resource_provider_text is None:
            return _dispatch_unhandled_command(command_text, effects)
        return CommandDispatchResolution.proceed_to_run(
            user_input=user_input,
            resource_provider_text=resource_provider_text,
            selected_provider_content=selected_provider_content,
        )

    def _step_from_wake(
        self,
        wake: CodingInputSelection,
        settle_pending: bool,
    ) -> CodingLoopStep:
        if wake.source is CodingInputSource.LOCAL_COMMAND:
            return CodingLoopStep.local_command(
                f"{wake.content.value}\n", settle_pending
            )
        return CodingLoopStep.provider_content(
            f"{wake.content.value}\n",
            wake.content,
            wake.queued_input,
            settle_pending,
        )


@dataclass
class _CodingSessionLifetime:
    """Controller-owned lifecycle; input/state still belong to their owners.

    This internal driver is confined to its caller's session thread. Its closed
    flag prevents repeated cleanup; it is not an agent-run witness or RPC
    admission state. The accepted run installs and releases its own witness.
    """

    input_queue: CodingInputQueue
    emitter: SettledEventEmitter
    finalize: Callable[[], CodingSessionResult]
    fire_session_shutdown: Callable[[], None]
    consume_settle_pending: Callable[[], bool]
    close_extension_session: Callable[[], None]
    clear_extension_chrome: Callable[[], None]
    cleanup_failed_run: Callable[[BaseException], None] | None = None
    finish_transition_lease: Callable[[], None] | None = None
    closed: bool = False
    result: CodingSessionResult | None = None

    def enqueue_seed(self, content: ProductContent) -> None:
        if self.closed:
            raise RuntimeError("coding session lifetime is closed")
        self.input_queue.enqueue_seed(content)

    def bind_external_abort_signal(
        self, signal: _input_queue._ExternalAbortSignalView
    ) -> None:
        """Bind the stable signal view once after successful startup."""

        signal.bind(self.input_queue)

    def drive_external_operation(
        self,
        content: ProductContent,
        step_once: Callable[[], LoopStepSignal],
    ) -> CodingSessionResult | None:
        """Drive one atomically claimed ordinary operation through true idle."""

        if self.closed:
            raise RuntimeError("coding session lifetime is closed")
        claim = self.input_queue._begin_external_operation(content)
        if claim is None:
            raise RuntimeError("product session operation already active")
        try:
            self.enqueue_seed(claim.content)
            result = self.drive(step_once)
        except BaseException as primary:
            try:
                self._settle_external_claim(claim)
            except BaseException:  # noqa: BLE001 - preserve primary
                primary.add_note("managed operation settlement also failed")
                try:
                    self._retire_lifetime()
                except BaseException:  # noqa: BLE001 - preserve primary
                    primary.add_note("managed lifetime retirement also failed")
            raise
        try:
            self._settle_external_claim(claim)
        except BaseException as cleanup:
            try:
                self._retire_lifetime()
            except BaseException:  # noqa: BLE001 - preserve cleanup
                cleanup.add_note("managed lifetime retirement also failed")
            raise
        return result

    def _settle_external_claim(self, claim: _input_queue._ExternalClaim) -> None:
        settled = self.input_queue._settle_external(claim.token)
        if settled is None:
            raise RuntimeError("managed operation settlement invariant failed")

    def drive(
        self, step_once: Callable[[], LoopStepSignal]
    ) -> CodingSessionResult | None:
        """Drive to idle (None) or terminal result, retiring on any failure."""

        if self.closed:
            raise RuntimeError("coding session lifetime is closed")
        try:
            while True:
                signal = step_once()
                if type(signal) is not LoopStepSignal:
                    raise TypeError("step_once must return a LoopStepSignal")
                if signal.kind is LoopStepSignalKind.IDLE:
                    return None
                if signal.kind is LoopStepSignalKind.RETURN_RESULT:
                    self.result = signal.result
                    self._retire_lifetime()
                    return self.result
                if signal.kind is LoopStepSignalKind.BREAK:
                    return self.close()
        except BaseException as primary:
            if self.cleanup_failed_run is not None:
                self.cleanup_failed_run(primary)
            try:
                self._retire_lifetime()
            except BaseException:  # noqa: BLE001 - preserve drive failure
                primary.add_note("coding lifetime retirement also failed")
            raise

    def close(self) -> CodingSessionResult | None:
        """Finalize normal disposal once, before lifecycle retirement."""

        if not self.closed:
            try:
                self.result = self.finalize()
            finally:
                self._retire_lifetime()
        return self.result

    def _retire_lifetime(self) -> None:
        """Retire without successful finalization after fatal/exception exits."""

        if self.closed:
            return
        self.closed = True
        try:
            if self.consume_settle_pending():
                self.emitter.agent_settled()
            self.fire_session_shutdown()
        finally:
            try:
                self.close_extension_session()
            finally:
                try:
                    self.clear_extension_chrome()
                finally:
                    self._finish_transition_lease()

    def _finish_transition_lease(self) -> None:
        finisher = self.finish_transition_lease
        if finisher is not None:
            finisher()


def _require_exact_coding_loop_step_fields(step: CodingLoopStep) -> None:
    """Validate the step's exact closed field types in declaration order."""

    if type(step.kind) is not CodingLoopStepKind:
        raise TypeError("kind must be an exact CodingLoopStepKind")
    if type(step.line) is not str:
        raise TypeError("line must be an exact str")
    if type(step.settle_pending) is not bool:
        raise TypeError("settle_pending must be an exact bool")
    if type(step.keyboard_interrupt) is not bool:
        raise TypeError("keyboard_interrupt must be an exact bool")


def _require_coding_loop_step_payload(step: CodingLoopStep) -> None:
    """Validate content ownership and EOF invariants after exact field types."""

    if step.kind is CodingLoopStepKind.PROVIDER_CONTENT:
        if type(step.selected_provider_content) is not ProductContent:
            raise TypeError("provider-content steps require an exact ProductContent")
        if (
            step.queued_input is not None
            and type(step.queued_input) is not AgentQueuedInput
        ):
            raise TypeError("queued_input must be an exact AgentQueuedInput")
    else:
        if step.selected_provider_content is not None:
            raise ValueError(
                "only provider-content steps carry selected_provider_content"
            )
        if step.queued_input is not None:
            raise ValueError("only provider-content steps carry queued_input")
    if step.kind in (CodingLoopStepKind.IDLE, CodingLoopStepKind.EOF):
        _require_empty_step(step)
        return
    if step.line == "":
        raise ValueError("a non-EOF step requires a non-empty line")
    if step.keyboard_interrupt:
        raise ValueError("only an EOF step may record a keyboard interrupt")


def _require_empty_step(step: CodingLoopStep) -> None:
    if step.line != "":
        raise ValueError(
            "an EOF step has no line"
            if step.kind is CodingLoopStepKind.EOF
            else "an idle step has no line"
        )
    if step.kind is CodingLoopStepKind.IDLE and step.keyboard_interrupt:
        raise ValueError("an idle step has no keyboard interrupt")


def _require_run_loop_ports(
    *,
    step_once: Callable[[], LoopStepSignal],
    finalize: Callable[[], CodingSessionResult],
    fire_session_start: Callable[[], None],
    fire_session_shutdown: Callable[[], None],
    consume_settle_pending: Callable[[], bool],
    close_extension_session: Callable[[], None],
    clear_extension_chrome: Callable[[], None],
) -> None:
    """Validate lifecycle ports in the original fail-fast order."""

    if not callable(step_once):
        raise TypeError("step_once must be callable")
    if not callable(finalize):
        raise TypeError("finalize must be callable")
    if not callable(fire_session_start):
        raise TypeError("fire_session_start must be callable")
    if not callable(fire_session_shutdown):
        raise TypeError("fire_session_shutdown must be callable")
    if not callable(consume_settle_pending):
        raise TypeError("consume_settle_pending must be callable")
    if not callable(close_extension_session):
        raise TypeError("close_extension_session must be callable")
    if not callable(clear_extension_chrome):
        raise TypeError("clear_extension_chrome must be callable")


def _dispatch_builtin_command(
    *,
    command_text: str,
    stripped: str,
    selected_provider_content: ProductContent | None,
    effects: CodingCommandEffects,
) -> CommandDispatchResolution | None:
    """Intercept a built-in under the exact typed/provider-content gate."""

    if selected_provider_content is not None and stripped:
        return None
    outcome = classify_coding_command(ProductContent(command_text))
    if outcome.kind is CodingCommandOutcomeKind.EXIT:
        return CommandDispatchResolution.exit_loop()
    if outcome.kind is CodingCommandOutcomeKind.CONTINUE:
        effects.interpret_builtin(outcome)
        return CommandDispatchResolution.continue_loop()
    return None


def _dispatch_resource_command(
    command_text: str,
    effects: CodingCommandEffects,
) -> tuple[CommandDispatchResolution | None, str | None]:
    """Route LIST/REJECT locally and return RUN's provider-visible text."""

    resource = effects.dispatch_resource(command_text)
    if resource is None:
        return None, None
    if type(resource) is not ResourceDispatchResolution:
        raise TypeError("dispatch_resource must return a ResourceDispatchResolution")
    if resource.kind is ResourceDispatchKind.LIST:
        effects.emit_diagnostic(resource.message)
        effects.refresh_footer()
        return CommandDispatchResolution.continue_loop(), None
    if resource.kind is ResourceDispatchKind.REJECT:
        effects.emit_diagnostic(resource.message)
        effects.refresh_footer()
        return CommandDispatchResolution.continue_loop(), None
    effects.record_resource_invocation()
    provider_text = resource.provider_text or ""
    effects.emit_diagnostic(resource.message)
    return None, provider_text


def _dispatch_extension_command(
    command_text: str,
    effects: CodingCommandEffects,
) -> CommandDispatchResolution | None:
    """Consume a handled or failed extension command after resources."""

    extension = effects.dispatch_extension(command_text)
    if extension is None:
        return None
    if type(extension) is not ExtensionDispatchResolution:
        raise TypeError("dispatch_extension must return an ExtensionDispatchResolution")
    if not extension.ran and extension.error:
        effects.emit_diagnostic(
            f"pipy: extension command /{extension.name} failed ({extension.error})"
        )
    effects.refresh_footer()
    return CommandDispatchResolution.continue_loop()


def _dispatch_unhandled_command(
    command_text: str,
    effects: CodingCommandEffects,
) -> CommandDispatchResolution:
    """Consume an unhandled slash command with the product diagnostic."""

    effects.emit_diagnostic(
        f"pipy: {command_text!r} is not handled in tool-loop mode; "
        + _NOT_HANDLED_COMMAND_NOTICE
    )
    effects.refresh_footer()
    return CommandDispatchResolution.continue_loop()


__all__ = [
    "CodingCommandEffects",
    "CodingLoopStep",
    "CodingLoopStepKind",
    "CodingSessionController",
    "LoopStepSignal",
    "LoopStepSignalKind",
    "SettledEventEmitter",
    "classify_coding_command",
]

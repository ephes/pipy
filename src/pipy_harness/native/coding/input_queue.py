"""Headless queued-input policy for a product coding session."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from functools import wraps
from typing import TYPE_CHECKING, Concatenate, ParamSpec, TypeVar, cast

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentQueuedInputKind,
    AgentQueuedInputPort,
)

if TYPE_CHECKING:
    from pipy_harness.native.cancellation import _AcceptedAbortSignal


class CodingInputSource(StrEnum):
    """Closed origins for one controller-selected input."""

    LOCAL_COMMAND = "local_command"
    RETAINED_FRESH_INPUT = "retained_fresh_input"
    RETAINED_AGENT_INPUT = "retained_agent_input"
    EXTERNAL_QUEUE = "external_queue"
    EXTERNAL_PROMPT = "external_prompt"
    POSITIONAL_SEED = "positional_seed"
    EXTENSION_STEERING = "extension_steering"
    EXTENSION_FOLLOW_UP = "extension_follow_up"
    EXTENSION_PROMPT = "extension_prompt"


@dataclass(frozen=True, slots=True)
class CodingInputSelection:
    """One local-dispatch input or provider-visible prompt chosen by policy."""

    content: ProductContent
    source: CodingInputSource
    queued_input: AgentQueuedInput | None = None

    def __post_init__(self) -> None:
        _require_content(self.content, "content")
        if type(self.source) is not CodingInputSource:
            raise TypeError("source must be an exact CodingInputSource")
        if self.queued_input is not None:
            _require_queued_input(self.queued_input, "queued_input")
            if self.queued_input.content is not self.content:
                raise ValueError("queued_input content must be the selected content")
        if self.source in _LOCAL_DISPATCH_SOURCES:
            if self.queued_input is not None:
                raise ValueError("local-dispatch inputs cannot carry queued state")
        elif self.source in _TYPED_QUEUE_SOURCES:
            if self.queued_input is None:
                raise ValueError("typed queue sources require queued_input")
        elif self.queued_input is not None:
            raise ValueError("only typed queue sources may carry queued_input")

    @property
    def bypass_local_command_dispatch(self) -> bool:
        """Whether the selection must be sent to the provider verbatim."""

        return self.source not in _LOCAL_DISPATCH_SOURCES


@dataclass(frozen=True, slots=True, eq=False)
class _ExternalReservationToken:
    """Opaque identity for one reservation owned by one input queue."""


@dataclass(frozen=True, slots=True)
class _ExternalReservation:
    """Immutable externally admissible work reserved by this queue."""

    token: _ExternalReservationToken
    content: ProductContent
    kind: AgentQueuedInputKind | None
    claimed: bool
    manual_compaction: ProductContent | None = None


@dataclass(frozen=True, slots=True)
class _ExternalAdmissionSnapshot:
    """One guarded snapshot of the managed external lanes and active slot."""

    reservation: _ExternalReservation | None
    steering: tuple[ProductContent, ...]
    follow_ups: tuple[ProductContent, ...]

    @property
    def pending_count(self) -> int:
        return len(self.steering) + len(self.follow_ups)


@dataclass(frozen=True, slots=True)
class _ExternalClaim:
    """Claimed work and its fresh accepted-abort latch."""

    token: _ExternalReservationToken
    content: ProductContent
    kind: AgentQueuedInputKind | None
    abort_signal: _AcceptedAbortSignal
    manual_compaction: ProductContent | None = None


@dataclass(slots=True)
class _ExternalActiveSlot:
    token: _ExternalReservationToken
    content: ProductContent
    kind: AgentQueuedInputKind | None
    abort_signal: _AcceptedAbortSignal
    claimed: bool = False
    manual_compaction: ProductContent | None = None


class _ExternalAbortSignalView:
    """Stable event-like view over one queue's currently claimed operation."""

    __slots__ = ("_binding_lock", "_queue")

    def __init__(self) -> None:
        self._binding_lock = threading.Lock()
        self._queue: CodingInputQueue | None = None

    def bind(self, queue: CodingInputQueue) -> None:
        if not isinstance(queue, CodingInputQueue):
            raise TypeError("queue must be a CodingInputQueue")
        with self._binding_lock:
            if self._queue is not None:
                raise RuntimeError("external abort signal is already bound")
            self._queue = queue

    def _capture_queue(self) -> CodingInputQueue | None:
        with self._binding_lock:
            return self._queue

    def _capture_latch(self) -> _AcceptedAbortSignal | None:
        queue = self._capture_queue()
        return None if queue is None else queue._capture_external_claimed_latch()

    def cancel(self) -> None:
        queue = self._capture_queue()
        if queue is not None:
            queue._abort_external()

    def is_set(self) -> bool:
        latch = self._capture_latch()
        return latch is not None and latch.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        latch = self._capture_latch()
        return False if latch is None else latch.wait(timeout)

    def register_cancel_callback(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
        latch = self._capture_latch()
        if latch is None:
            return lambda: None
        return latch.register_cancel_callback(callback)


@dataclass(frozen=True, slots=True)
class _NativeControlReservation:
    """Detached reservation projection returned by the native control."""

    token: _ExternalReservationToken
    content: ProductContent
    kind: AgentQueuedInputKind | None
    claimed: bool
    manual_compaction: ProductContent | None = None


@dataclass(frozen=True, slots=True)
class _NativeControlSnapshot:
    """Immutable transport-neutral projection of managed queue state."""

    reservation: _NativeControlReservation | None
    steering: tuple[ProductContent, ...]
    follow_ups: tuple[ProductContent, ...]

    @property
    def pending_count(self) -> int:
        return len(self.steering) + len(self.follow_ups)


@dataclass(frozen=True, slots=True)
class _NativeRunClaim:
    """Opaque exact capability carried by one selected native worker run."""

    _claim: _ExternalClaim

    @property
    def token(self) -> _ExternalReservationToken:
        return self._claim.token

    @property
    def content(self) -> ProductContent:
        return self._claim.content

    @property
    def kind(self) -> AgentQueuedInputKind | None:
        return self._claim.kind

    @property
    def manual_compaction(self) -> ProductContent | None:
        return self._claim.manual_compaction

    @property
    def is_aborted(self) -> bool:
        """Read only this exact claim's latch, never a successor's latch."""

        return self._claim.abort_signal.is_set()


class _NativeControlAbortView:
    """Stable, guard-free abort view over a native control's claimed latch."""

    __slots__ = ("_control",)

    def __init__(self, control: "_NativeSessionControl") -> None:
        self._control = control

    def cancel(self) -> None:
        self._control.request_abort()

    def is_set(self) -> bool:
        latch = self._control._capture_claimed_latch()
        return latch is not None and latch.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        latch = self._control._capture_claimed_latch()
        return False if latch is None else latch.wait(timeout)

    def register_cancel_callback(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
        latch = self._control._capture_claimed_latch()
        if latch is None:
            return lambda: None
        return latch.register_cancel_callback(callback)


class _NativeSessionControl:
    """Private admission/publication gate over one queue-owned managed state.

    This object deliberately contains no product payload, active flag, token, or
    latch.  It only establishes the outer lock order required by a future
    transport adoption; :class:`CodingInputQueue` remains the mutable owner.
    """

    __slots__ = ("_abort_view", "_gate", "_queue")

    def __init__(self, queue: "CodingInputQueue") -> None:
        if not isinstance(queue, CodingInputQueue):
            raise TypeError("queue must be a CodingInputQueue")
        self._gate = threading.RLock()
        self._queue = queue
        self._abort_view = _NativeControlAbortView(self)

    @property
    def abort_view(self) -> _NativeControlAbortView:
        return self._abort_view

    def admit_prompt(
        self, content: ProductContent, *, steer_active: bool = False
    ) -> _NativeControlSnapshot:
        with self._gate:
            return _native_control_snapshot(
                self._queue._admit_external(content, steer_active=steer_active)
            )

    def admit_queued(
        self, content: ProductContent, kind: AgentQueuedInputKind
    ) -> _NativeControlSnapshot:
        with self._gate:
            return _native_control_snapshot(self._queue._admit_external(content, kind))

    def admit_manual_compaction(
        self, custom_instructions: ProductContent | None
    ) -> _NativeControlSnapshot | None:
        """Reserve one idle-only private compaction operation."""

        with self._gate:
            snapshot = self._queue._admit_manual_compaction(custom_instructions)
            return None if snapshot is None else _native_control_snapshot(snapshot)

    def _bridge_admit_steering(
        self, content: ProductContent, verify_authorized: Callable[[], None]
    ) -> _NativeControlSnapshot:
        return self._bridge_admit_queued(
            content, AgentQueuedInputKind.STEERING, verify_authorized
        )

    def _bridge_admit_follow_up(
        self, content: ProductContent, verify_authorized: Callable[[], None]
    ) -> _NativeControlSnapshot:
        return self._bridge_admit_queued(
            content, AgentQueuedInputKind.FOLLOW_UP, verify_authorized
        )

    def snapshot(self) -> _NativeControlSnapshot:
        with self._gate:
            return _native_control_snapshot(self._queue._external_admission_snapshot())

    def claim(self, reservation: _NativeControlReservation) -> _NativeRunClaim | None:
        if type(reservation) is not _NativeControlReservation:
            raise TypeError("reservation must be a _NativeControlReservation")
        with self._gate:
            claim = self._queue._claim_external(reservation.token)
            return None if claim is None else _NativeRunClaim(claim)

    def _claim_and_attach(
        self,
        reservation: _NativeControlReservation,
        verify_attachable: Callable[[], None],
        attach: Callable[[_NativeRunClaim], None],
    ) -> _NativeRunClaim | None:
        """Claim and attach one worker capability under the publication gate."""

        if type(reservation) is not _NativeControlReservation:
            raise TypeError("reservation must be a _NativeControlReservation")
        if not callable(verify_attachable):
            raise TypeError("verify_attachable must be callable")
        if not callable(attach):
            raise TypeError("attach must be callable")
        with self._gate:
            verify_attachable()
            claim = self._queue._claim_external(reservation.token)
            if claim is None:
                return None
            result = _NativeRunClaim(claim)
            try:
                attach(result)
            except BaseException as primary:
                try:
                    self._settle_claim_locked(result)
                except BaseException:  # noqa: BLE001 - preserve attachment failure
                    primary.add_note("native claim attachment cleanup also failed")
                raise
            return result

    def _bridge_admit_prompt(
        self,
        content: ProductContent,
        *,
        steer_active: bool,
        verify_authorized: Callable[[], None],
    ) -> _NativeControlSnapshot:
        """Admit through a bridge after its fatal-state check under this gate."""

        if not callable(verify_authorized):
            raise TypeError("verify_authorized must be callable")
        with self._gate:
            verify_authorized()
            return _native_control_snapshot(
                self._queue._admit_external(content, steer_active=steer_active)
            )

    def _bridge_admit_queued(
        self,
        content: ProductContent,
        kind: AgentQueuedInputKind,
        verify_authorized: Callable[[], None],
    ) -> _NativeControlSnapshot:
        if not callable(verify_authorized):
            raise TypeError("verify_authorized must be callable")
        with self._gate:
            verify_authorized()
            return _native_control_snapshot(self._queue._admit_external(content, kind))

    def settle(self, claim: _NativeRunClaim) -> _NativeControlSnapshot:
        if type(claim) is not _NativeRunClaim:
            raise TypeError("claim must be a _NativeRunClaim")
        with self._gate:
            return self._settle_claim_locked(claim)

    def _consume_and_settle(
        self,
        claim: _NativeRunClaim,
        consume: Callable[[], None],
    ) -> _NativeControlSnapshot:
        """Clear a bridge-held capability and settle it in one publication turn."""

        if type(claim) is not _NativeRunClaim:
            raise TypeError("claim must be a _NativeRunClaim")
        if not callable(consume):
            raise TypeError("consume must be callable")
        with self._gate:
            consume()
            return self._settle_claim_locked(claim)

    def _consume_settle_and_publish(
        self,
        claim: _NativeRunClaim,
        consume: Callable[[], None],
        publish: Callable[[_NativeControlSnapshot], None],
    ) -> _NativeControlSnapshot:
        """Settle and project one run boundary under the outer control gate."""

        if type(claim) is not _NativeRunClaim:
            raise TypeError("claim must be a _NativeRunClaim")
        if not callable(consume) or not callable(publish):
            raise TypeError("consume and publish must be callable")
        with self._gate:
            consume()
            snapshot = self._settle_claim_locked(claim)
            publish(snapshot)
            return snapshot

    def _settle_claim_locked(self, claim: _NativeRunClaim) -> _NativeControlSnapshot:
        """Settle exactly one claim while the caller owns the outer gate."""

        settled = self._queue._settle_external(claim.token)
        if settled is None:
            raise RuntimeError("native control exact settlement invariant failed")
        return _native_control_snapshot(settled)

    def _capture_claimed_latch(self) -> _AcceptedAbortSignal | None:
        with self._gate:
            return self._queue._capture_external_claimed_latch()

    def request_abort(self) -> _NativeControlSnapshot:
        """Clear steering under both guards and signal its captured latch after."""

        with self._gate:
            snapshot, latch = self._queue._prepare_external_abort()
            result = _native_control_snapshot(snapshot)
        if latch is not None:
            latch.set()
        return result

    def publish_if_true_idle(self, publish: Callable[[], None]) -> bool:
        """Publish a transport idle record under the admission gate if still idle."""

        if not callable(publish):
            raise TypeError("publish must be callable")
        with self._gate:
            snapshot = _native_control_snapshot(
                self._queue._external_admission_snapshot()
            )
            if snapshot.reservation is not None or snapshot.pending_count:
                return False
            publish()
            return True


def _native_control_snapshot(
    snapshot: _ExternalAdmissionSnapshot,
) -> _NativeControlSnapshot:
    reservation = snapshot.reservation
    return _NativeControlSnapshot(
        reservation=(
            None
            if reservation is None
            else _NativeControlReservation(
                reservation.token,
                reservation.content,
                reservation.kind,
                reservation.claimed,
                reservation.manual_compaction,
            )
        ),
        steering=tuple(snapshot.steering),
        follow_ups=tuple(snapshot.follow_ups),
    )


_LOCAL_DISPATCH_SOURCES = frozenset(
    {
        CodingInputSource.LOCAL_COMMAND,
        CodingInputSource.RETAINED_FRESH_INPUT,
    }
)


_TYPED_QUEUE_SOURCES = frozenset(
    {
        CodingInputSource.RETAINED_AGENT_INPUT,
        CodingInputSource.EXTERNAL_QUEUE,
        CodingInputSource.EXTENSION_STEERING,
        CodingInputSource.EXTENSION_FOLLOW_UP,
    }
)


_P = ParamSpec("_P")
_R = TypeVar("_R")
_RLOCK_TYPE = type(threading.RLock())


def _new_native_control_lock() -> threading.Lock:
    """Construct a private bridge publication lock without widening imports."""

    return threading.Lock()


def _new_native_control_event() -> threading.Event:
    """Construct a private bridge readiness event without widening imports."""

    return threading.Event()


def _native_control_current_thread() -> threading.Thread:
    """Return worker identity for the private exact-claim bridge."""

    return threading.current_thread()


def _guarded_queue_api(
    method: Callable[Concatenate["CodingInputQueue", _P], _R],
) -> Callable[Concatenate["CodingInputQueue", _P], _R]:
    """Take the queue's one mutation lock for a complete API operation."""

    @wraps(method)
    def guarded(self: "CodingInputQueue", *args: _P.args, **kwargs: _P.kwargs) -> _R:
        with self._mutation_lock:
            return method(self, *args, **kwargs)

    return cast(Callable[Concatenate["CodingInputQueue", _P], _R], guarded)


class _AgentLoopQueuedInputPort:
    """Narrow port offered to an active reusable agent loop."""

    __slots__ = ("_queue",)

    def __init__(self, queue: CodingInputQueue) -> None:
        self._queue = queue

    def take_next(self) -> AgentQueuedInput | None:
        return self._queue.take_next_for_agent_loop()


class CodingInputQueue:
    """Mutable, synchronous product policy for queued coding-session inputs."""

    __slots__ = (
        "_agent_loop_port",
        "_extension_follow_ups",
        "_extension_prompts",
        "_extension_steering",
        "_external_active_slot",
        "_external_follow_ups",
        "_external_steering",
        "_external_inputs",
        "_mutation_lock",
        "_next_turn_context",
        "_pending_local_command",
        "_pending_local_command_source",
        "_retained_agent_inputs",
        "_retained_external_input",
        "_retained_fresh_input",
        "_seeds",
    )

    def __init__(
        self,
        *,
        external_inputs: Iterable[AgentQueuedInputPort] = (),
        mutation_lock: threading.RLock | None = None,
        pending_local_command_source: Callable[[], ProductContent | None] | None = None,
        seeds: Iterable[ProductContent] = (),
    ) -> None:
        self._mutation_lock = mutation_lock or threading.RLock()
        if not isinstance(self._mutation_lock, _RLOCK_TYPE):
            raise TypeError("mutation_lock must be an RLock")
        self._external_inputs = tuple(external_inputs)
        if not all(
            isinstance(source, AgentQueuedInputPort) for source in self._external_inputs
        ):
            raise TypeError("external inputs must implement AgentQueuedInputPort")
        if pending_local_command_source is not None and not callable(
            pending_local_command_source
        ):
            raise TypeError("pending_local_command_source must be callable")
        self._pending_local_command_source = pending_local_command_source
        self._seeds: deque[ProductContent] = deque()
        self._extension_steering: deque[AgentQueuedInput] = deque()
        self._extension_follow_ups: deque[AgentQueuedInput] = deque()
        self._extension_prompts: deque[ProductContent] = deque()
        self._external_active_slot: _ExternalActiveSlot | None = None
        self._external_steering: deque[ProductContent] = deque()
        self._external_follow_ups: deque[ProductContent] = deque()
        self._next_turn_context: deque[ProductContent] = deque()
        self._pending_local_command: ProductContent | None = None
        self._retained_agent_inputs: deque[AgentQueuedInput] = deque()
        self._retained_external_input: (
            tuple[AgentQueuedInputPort, AgentQueuedInput] | None
        ) = None
        self._retained_fresh_input: ProductContent | None = None
        self._agent_loop_port = _AgentLoopQueuedInputPort(self)
        for seed in seeds:
            self.enqueue_seed(seed)

    @_guarded_queue_api
    def _admit_external(
        self,
        content: ProductContent,
        kind: AgentQueuedInputKind | None = None,
        *,
        steer_active: bool = False,
    ) -> _ExternalAdmissionSnapshot:
        """Admit private external work without exposing it to existing selectors."""

        _require_content(content, "content")
        if kind is not None and type(kind) is not AgentQueuedInputKind:
            raise TypeError("kind must be None or an exact AgentQueuedInputKind")
        if type(steer_active) is not bool:
            raise TypeError("steer_active must be an exact bool")
        if kind is not None and steer_active:
            raise ValueError("steer_active applies only to ordinary input")
        if self._external_active_slot is None:
            self._reserve_external(content, kind)
        else:
            pending_kind = (
                kind
                if kind is not None
                else (
                    AgentQueuedInputKind.STEERING
                    if steer_active
                    else AgentQueuedInputKind.FOLLOW_UP
                )
            )
            lane = (
                self._external_steering
                if pending_kind is AgentQueuedInputKind.STEERING
                else self._external_follow_ups
            )
            lane.append(content)
        return self._external_snapshot()

    @_guarded_queue_api
    def _begin_external_operation(
        self, content: ProductContent
    ) -> _ExternalClaim | None:
        """Atomically reserve and claim one ordinary operation only while idle."""

        _require_content(content, "content")
        if (
            self._external_active_slot is not None
            or self._external_steering
            or self._external_follow_ups
        ):
            return None
        self._reserve_external(content, None)
        slot = self._external_active_slot
        assert slot is not None
        slot.claimed = True
        return _ExternalClaim(slot.token, slot.content, slot.kind, slot.abort_signal)

    @_guarded_queue_api
    def _admit_manual_compaction(
        self, custom_instructions: ProductContent | None
    ) -> _ExternalAdmissionSnapshot | None:
        if custom_instructions is not None:
            _require_content(custom_instructions, "custom_instructions")
        if (
            self._external_active_slot is not None
            or self._external_steering
            or self._external_follow_ups
        ):
            return None
        self._reserve_external(ProductContent(""), None)
        assert self._external_active_slot is not None
        # An empty marker distinguishes this control operation from an ordinary
        # unclassified prompt while remaining private to the queue/control seam.
        self._external_active_slot.manual_compaction = (
            custom_instructions
            if custom_instructions is not None
            else ProductContent("")
        )
        return self._external_snapshot()

    @_guarded_queue_api
    def _claim_external(
        self, token: _ExternalReservationToken
    ) -> _ExternalClaim | None:
        """Claim the exact current managed reservation once."""

        slot = self._matching_external_slot(token)
        if slot is None or slot.claimed:
            return None
        slot.claimed = True
        return _ExternalClaim(
            slot.token,
            slot.content,
            slot.kind,
            slot.abort_signal,
            slot.manual_compaction,
        )

    @_guarded_queue_api
    def _settle_external(
        self, token: _ExternalReservationToken
    ) -> _ExternalAdmissionSnapshot | None:
        """Settle one claimed reservation and atomically reserve its successor."""

        slot = self._matching_external_slot(token)
        if slot is None or not slot.claimed:
            return None
        self._external_active_slot = None
        if self._external_steering:
            self._reserve_external(
                self._external_steering.popleft(), AgentQueuedInputKind.STEERING
            )
        elif self._external_follow_ups:
            self._reserve_external(
                self._external_follow_ups.popleft(), AgentQueuedInputKind.FOLLOW_UP
            )
        return self._external_snapshot()

    @_guarded_queue_api
    def _external_admission_snapshot(self) -> _ExternalAdmissionSnapshot:
        return self._external_snapshot()

    @_guarded_queue_api
    def _capture_external_claimed_latch(self) -> _AcceptedAbortSignal | None:
        slot = self._external_active_slot
        return None if slot is None or not slot.claimed else slot.abort_signal

    def _abort_external(self) -> _ExternalAdmissionSnapshot:
        """Discard pending steering, then signal the captured latch after unlock.

        Callers must enter without already holding the queue mutation guard.
        """

        snapshot, latch = self._prepare_external_abort()
        if latch is not None:
            latch.set()
        return snapshot

    @_guarded_queue_api
    def _prepare_external_abort(
        self,
    ) -> tuple[_ExternalAdmissionSnapshot, _AcceptedAbortSignal | None]:
        """Clear steering and capture a latch without observing or signaling it."""

        self._external_steering.clear()
        slot = self._external_active_slot
        latch = None if slot is None else slot.abort_signal
        return self._external_snapshot(), latch

    @property
    @_guarded_queue_api
    def agent_loop_port(self) -> AgentQueuedInputPort:
        """Return the stable queue port used by the active agent loop."""

        return self._agent_loop_port

    @property
    @_guarded_queue_api
    def has_pending_local_command(self) -> bool:
        return self._pending_local_command is not None

    @_guarded_queue_api
    def defer_local_command(self, content: ProductContent) -> None:
        """Give a local command precedence without consuming queued prompts."""

        _require_content(content, "content")
        if self._pending_local_command is not None:
            raise RuntimeError("a local command is already pending")
        self._pending_local_command = content

    @_guarded_queue_api
    def retain_agent_input(self, queued_input: AgentQueuedInput | None) -> None:
        """Append one whole agent-loop handoff for later separate runs, FIFO."""

        if queued_input is None:
            return
        _require_queued_input(queued_input, "queued_input")
        self._retained_agent_inputs.append(queued_input)

    @_guarded_queue_api
    def enqueue_seed(self, content: ProductContent) -> None:
        _require_content(content, "content")
        self._seeds.append(content)

    @_guarded_queue_api
    def enqueue_extension_steering(self, content: ProductContent) -> None:
        self._extension_steering.append(
            _new_queued_input(content, AgentQueuedInputKind.STEERING)
        )

    @_guarded_queue_api
    def enqueue_extension_follow_up(self, content: ProductContent) -> None:
        self._extension_follow_ups.append(
            _new_queued_input(content, AgentQueuedInputKind.FOLLOW_UP)
        )

    @_guarded_queue_api
    def enqueue_extension_prompt(self, content: ProductContent) -> None:
        _require_content(content, "content")
        self._extension_prompts.append(content)

    @_guarded_queue_api
    def enqueue_next_turn_context(self, content: ProductContent) -> None:
        """Attach extension context once to the next provider-visible prompt."""

        _require_content(content, "content")
        self._next_turn_context.append(content)

    @_guarded_queue_api
    def take_next(self) -> CodingInputSelection | None:
        """Take one input using exact product priority and one-shot clearing.

        Local commands precede retained fresh lines, retained agent handoffs,
        external queues, positional seeds, and extension deliveries, in order.
        """

        self._poll_pending_local_command()
        if self._pending_local_command is not None:
            content = self._pending_local_command
            self._pending_local_command = None
            return CodingInputSelection(content, CodingInputSource.LOCAL_COMMAND)
        if self._retained_fresh_input is not None:
            content = self._retained_fresh_input
            self._retained_fresh_input = None
            return CodingInputSelection(
                content,
                CodingInputSource.RETAINED_FRESH_INPUT,
            )
        if self._retained_agent_inputs:
            retained = self._retained_agent_inputs.popleft()
            return self._queued_selection(
                retained, CodingInputSource.RETAINED_AGENT_INPUT
            )
        external = self._take_external_once()
        if external is not None:
            return self._queued_selection(external, CodingInputSource.EXTERNAL_QUEUE)
        if self._seeds:
            return self._prompt_selection(
                self._seeds.popleft(), CodingInputSource.POSITIONAL_SEED
            )
        if self._extension_steering:
            return self._queued_selection(
                self._extension_steering.popleft(),
                CodingInputSource.EXTENSION_STEERING,
            )
        if self._extension_follow_ups:
            return self._queued_selection(
                self._extension_follow_ups.popleft(),
                CodingInputSource.EXTENSION_FOLLOW_UP,
            )
        if self._extension_prompts:
            return self._prompt_selection(
                self._extension_prompts.popleft(),
                CodingInputSource.EXTENSION_PROMPT,
            )
        return None

    @_guarded_queue_api
    def take_next_for_agent_loop(self) -> AgentQueuedInput | None:
        """Take one active-run continuation without overtaking local policy."""

        self._poll_pending_local_command()
        if self._pending_local_command is not None:
            return None
        if self._retained_fresh_input is not None:
            return None
        external = self._take_external_once()
        if external is not None:
            return external
        if self._seeds:
            return None
        if self._extension_steering:
            return self._extension_steering.popleft()
        if self._extension_follow_ups:
            return self._extension_follow_ups.popleft()
        return None

    @_guarded_queue_api
    def classify_external_wake(
        self,
        source: AgentQueuedInputPort,
        line: str,
    ) -> CodingInputSelection | None:
        """Classify one line returned after a registered external source woke input.

        A source returning ``None`` identifies an ordinary freshly typed line.
        A queued DTO whose content does not describe the exact line is retained,
        leaving the just-read line fresh rather than losing either input.
        EOF has no fresh input to preserve, so a valid DTO is selected directly.
        A local command accepted during the blocking read keeps product priority;
        the already classified queued input is retained for the next selection.
        """

        if not any(registered is source for registered in self._external_inputs):
            raise ValueError("external wake source is not registered")
        if type(line) is not str:
            raise TypeError("line must be an exact str")
        self._require_no_retained_wake_input()
        native_selected = self._take_native_control_selected(source, line)
        if native_selected is not None:
            return native_selected
        queued_input = source.take_next()
        if queued_input is not None:
            _require_queued_input(queued_input, "external wake queued input")
        matches_line = queued_input is not None and line == _line_framing(
            queued_input.content.value
        )
        self._poll_pending_local_command()
        if self._pending_local_command is None:
            if queued_input is None:
                return None
            if not line or matches_line:
                return self._queued_selection(
                    queued_input,
                    CodingInputSource.EXTERNAL_QUEUE,
                )
            self._retain_external_input(source, queued_input)
            return None
        if not matches_line and line:
            self._retain_fresh_line(line)
        if queued_input is not None:
            self._retain_external_input(source, queued_input)
        content = self._pending_local_command
        self._pending_local_command = None
        return CodingInputSelection(content, CodingInputSource.LOCAL_COMMAND)

    @_guarded_queue_api
    def clear_extension_inputs(self) -> None:
        """Clear session-bound extension delivery without touching other owners."""

        self._extension_steering.clear()
        self._extension_follow_ups.clear()
        self._extension_prompts.clear()
        self._next_turn_context.clear()

    @_guarded_queue_api
    def take_next_turn_context(self) -> tuple[ProductContent, ...]:
        """Take request-only extension context for the next accepted run."""

        context = tuple(self._next_turn_context)
        self._next_turn_context.clear()
        return context

    @_guarded_queue_api
    def _take_external_once(self) -> AgentQueuedInput | None:
        retained = self._retained_external_input
        if retained is not None:
            self._retained_external_input = None
            source, queued_input = retained
            if not any(registered is source for registered in self._external_inputs):
                raise RuntimeError("retained external wake source is not registered")
            return queued_input
        for source in self._external_inputs:
            polled_input = source.take_next()
            if polled_input is None:
                continue
            _require_queued_input(polled_input, "external queued input")
            return polled_input
        return None

    @_guarded_queue_api
    def _poll_pending_local_command(self) -> None:
        if (
            self._pending_local_command is not None
            or self._pending_local_command_source is None
        ):
            return
        content = self._pending_local_command_source()
        if content is not None:
            _require_content(content, "pending local command")
            self._pending_local_command = content

    @_guarded_queue_api
    def _retain_external_input(
        self,
        source: AgentQueuedInputPort,
        queued_input: AgentQueuedInput,
    ) -> None:
        if self._retained_external_input is not None:
            raise RuntimeError("an external wake input is already retained")
        self._retained_external_input = (source, queued_input)

    def _take_native_control_selected(
        self, source: AgentQueuedInputPort, line: str
    ) -> CodingInputSelection | None:
        """Take a bridge-held exact selection without line-content framing."""

        take_selected = getattr(source, "take_selected", None)
        if not callable(take_selected):
            return None
        selected = take_selected()
        if selected is None:
            return None
        content, queued_input = selected
        _require_content(content, "external selected content")
        if queued_input is not None:
            _require_queued_input(queued_input, "external selected input")
            if queued_input.content is not content:
                raise ValueError(
                    "external selected input must retain its exact content"
                )
        if line:
            raise RuntimeError("native external wake must not carry line content")
        return CodingInputSelection(
            content,
            (
                CodingInputSource.EXTERNAL_QUEUE
                if queued_input is not None
                else CodingInputSource.EXTERNAL_PROMPT
            ),
            queued_input,
        )

    @_guarded_queue_api
    def _retain_fresh_line(self, line: str) -> None:
        if self._retained_fresh_input is not None:
            raise RuntimeError("a fresh wake input is already retained")
        self._retained_fresh_input = ProductContent(line)

    @_guarded_queue_api
    def _require_no_retained_wake_input(self) -> None:
        if self._retained_external_input is not None:
            raise RuntimeError("an external wake input is already retained")
        if self._retained_fresh_input is not None:
            raise RuntimeError("a fresh wake input is already retained")

    def _reserve_external(
        self, content: ProductContent, kind: AgentQueuedInputKind | None
    ) -> None:
        from pipy_harness.native.cancellation import _AcceptedAbortSignal

        if self._external_active_slot is not None:
            raise RuntimeError("managed external input is already reserved")
        self._external_active_slot = _ExternalActiveSlot(
            token=_ExternalReservationToken(),
            content=content,
            kind=kind,
            abort_signal=_AcceptedAbortSignal(),
        )

    def _matching_external_slot(
        self, token: _ExternalReservationToken
    ) -> _ExternalActiveSlot | None:
        if type(token) is not _ExternalReservationToken:
            return None
        slot = self._external_active_slot
        if slot is None:
            return None
        if token is not slot.token:
            return None
        return slot

    def _external_snapshot(self) -> _ExternalAdmissionSnapshot:
        slot = self._external_active_slot
        reservation = (
            None
            if slot is None
            else _ExternalReservation(
                slot.token,
                slot.content,
                slot.kind,
                slot.claimed,
                slot.manual_compaction,
            )
        )
        return _ExternalAdmissionSnapshot(
            reservation,
            tuple(self._external_steering),
            tuple(self._external_follow_ups),
        )

    def _queued_selection(
        self,
        queued_input: AgentQueuedInput,
        source: CodingInputSource,
    ) -> CodingInputSelection:
        return CodingInputSelection(
            queued_input.content,
            source,
            queued_input,
        )

    def _prompt_selection(
        self,
        content: ProductContent,
        source: CodingInputSource,
    ) -> CodingInputSelection:
        return CodingInputSelection(content, source)


def _new_queued_input(
    content: ProductContent,
    kind: AgentQueuedInputKind,
) -> AgentQueuedInput:
    _require_content(content, "content")
    if type(kind) is not AgentQueuedInputKind:
        raise TypeError("kind must be an exact AgentQueuedInputKind")
    return AgentQueuedInput(content, kind)


def _line_framing(content: str) -> str:
    return content if content.endswith("\n") else f"{content}\n"


def _require_content(content: object, field_name: str) -> None:
    if type(content) is not ProductContent:
        raise TypeError(f"{field_name} must be an exact ProductContent")


def _require_queued_input(queued_input: object, field_name: str) -> None:
    if type(queued_input) is not AgentQueuedInput:
        raise TypeError(f"{field_name} must be an exact AgentQueuedInput")
    _require_content(queued_input.content, f"{field_name}.content")
    if type(queued_input.kind) is not AgentQueuedInputKind:
        raise TypeError(f"{field_name}.kind must be an exact AgentQueuedInputKind")

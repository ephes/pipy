"""Generation-owned extension message routing and delivery ordering."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias, TypeVar

from pipy_harness.native.extension_types import QueuedCustomMessage, QueuedUserMessage

_MessageDelivery: TypeAlias = Callable[[], None]
_DrainedMessageDelivery: TypeAlias = Callable[
    [tuple[QueuedUserMessage, ...], tuple[QueuedCustomMessage, ...]], None
]
_DeliveryValue = TypeVar("_DeliveryValue")
_RLOCK_TYPE = type(threading.RLock())


class OrderedMessageDeliveryGate(Protocol):
    def submit(self, delivery: _MessageDelivery) -> None: ...

    def append_reserved(self, deliveries: deque[_MessageDelivery]) -> None: ...


@dataclass(frozen=True, slots=True)
class GenerationMessageReservation:
    owner: "GenerationMessageRouting"
    delivery: _MessageDelivery
    forwarding: _MessageDelivery
    live_forwarding: _MessageDelivery
    allow_uninstalled_fallback: bool


@dataclass(slots=True)
class GenerationMessageRetirement:
    """References detached by the locked mark phase and finalized unlocked."""

    pending: deque[_MessageDelivery] | None = None
    gate: OrderedMessageDeliveryGate | None = None
    user_outbox: list[QueuedUserMessage] | None = None
    custom_outbox: list[QueuedCustomMessage] | None = None

    def finalize_retirement(self) -> tuple[object, ...]:
        pending, gate = self.pending, self.gate
        user_outbox, custom_outbox = self.user_outbox, self.custom_outbox
        self.pending = self.gate = self.user_outbox = self.custom_outbox = None
        if user_outbox is None:
            return ()
        retained = (
            (() if pending is None else tuple(pending))
            + (() if gate is None else (gate,))
            + tuple(user_outbox)
            + (() if custom_outbox is None else tuple(custom_outbox))
        )
        user_outbox.clear()
        if custom_outbox is not None:
            custom_outbox.clear()
        return retained


class GenerationMessageRouting:
    def __init__(
        self,
        user_outbox: list[QueuedUserMessage],
        custom_outbox: list[QueuedCustomMessage],
        *,
        mutex: threading.RLock | None = None,
        boundary_observer: Callable[[str], None] | None = None,
    ) -> None:
        if type(user_outbox) is not list or type(custom_outbox) is not list:
            raise TypeError("generation message outboxes must be exact lists")
        if mutex is not None and not isinstance(mutex, _RLOCK_TYPE):
            raise TypeError("generation message routing mutex must be an RLock")
        self._user_outbox = user_outbox
        self._custom_outbox = custom_outbox
        self._attached_user_outbox: list[QueuedUserMessage] | None = user_outbox
        self._attached_custom_outbox: list[QueuedCustomMessage] | None = custom_outbox
        self._mutex = mutex
        self._boundary_observer = boundary_observer
        self._state: Literal[
            "uninstalled", "candidate", "releasing", "live", "retired"
        ] = "uninstalled"
        self._gate: OrderedMessageDeliveryGate | None = None
        self._pending: deque[_MessageDelivery] | None = None

    @property
    def user_outbox(self) -> list[QueuedUserMessage]:
        return self._user_outbox

    @property
    def custom_outbox(self) -> list[QueuedCustomMessage]:
        return self._custom_outbox

    @property
    def mutex(self) -> threading.RLock | None:
        return self._mutex

    def _bind_session_mutex(self, mutex: threading.RLock) -> None:
        if not isinstance(mutex, _RLOCK_TYPE):
            raise TypeError("generation message routing mutex must be an RLock")
        if self._mutex is None:
            if self._state != "uninstalled":
                raise RuntimeError("only an uninstalled route can receive its mutex")
            self._mutex = mutex
        elif self._mutex is not mutex:
            raise ValueError("message routing must retain one exact session mutex")

    def _observe(self, event: str) -> None:
        if self._boundary_observer is not None:
            self._boundary_observer(event)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        mutex = self._mutex
        if mutex is None:
            raise RuntimeError("unbound routing has no session guard")
        self._observe("routing_guard_enter")
        try:
            with mutex:
                yield
        finally:
            self._observe("routing_guard_exit")

    def _pending_fifo(self) -> deque[_MessageDelivery]:
        pending = self._pending
        if pending is None:
            raise RuntimeError("candidate message route has no pending FIFO")
        return pending

    def accept(self, reservation: GenerationMessageReservation) -> None:
        if reservation.owner is not self:
            return
        if self._mutex is None:
            if reservation.allow_uninstalled_fallback:
                self._observe("direct_fallback")
                reservation.delivery()
            return
        direct = False
        claim: tuple[OrderedMessageDeliveryGate, _MessageDelivery] | None = None
        with self._locked():
            if self._state == "uninstalled":
                direct = reservation.allow_uninstalled_fallback
            elif self._state in ("candidate", "releasing"):
                self._pending_fifo().append(reservation.forwarding)
            elif self._state == "live":
                if self._gate is None:
                    raise RuntimeError("live message route has no delivery gate")
                claim = (self._gate, reservation.live_forwarding)
        if direct:
            self._observe("direct_fallback")
            reservation.delivery()
        elif claim is not None:
            claim[0].submit(claim[1])

    def _install_candidate_route(self, gate: OrderedMessageDeliveryGate) -> None:
        if self._mutex is None:
            raise ValueError("unbound direct routing cannot be installed")
        pending: deque[_MessageDelivery] = deque()
        with self._locked():
            if self._state != "uninstalled":
                raise RuntimeError("candidate message route is unavailable")
            self._gate = gate
            self._pending = pending
            self._state = "candidate"

    def release_pending(self) -> int:
        if self._mutex is None:
            return 0
        with self._locked():
            if self._state != "candidate":
                return 0
            gate = self._gate
            if gate is None:
                raise RuntimeError("candidate message route has no delivery gate")
            prefix = self._pending_fifo()
            self._pending = deque()
            self._state = "releasing"
        prefix_count = len(prefix)
        try:
            if prefix:
                self._observe("detached_batch_submission")
                gate.append_reserved(prefix)
        except BaseException:
            dropped: deque[_MessageDelivery] | None = None
            with self._locked():
                if self._state == "releasing":
                    self._state = "retired"
                    dropped, self._pending = self._pending, None
                    self._gate = None
            del dropped
            raise
        with self._locked():
            if self._state != "releasing":
                return prefix_count
            tail = self._pending_fifo()
            self._pending = None
            try:
                gate.append_reserved(tail)
            except BaseException:
                self._state = "retired"
                self._gate = None
                raise
            self._state = "live"
            return prefix_count + len(tail)

    def _append_live_user(self, message: QueuedUserMessage) -> None:
        with self._locked():
            if self._state == "live" and self._attached_user_outbox is not None:
                self._attached_user_outbox.append(message)

    def _append_live_custom(self, message: QueuedCustomMessage) -> None:
        with self._locked():
            if self._state == "live" and self._attached_custom_outbox is not None:
                self._attached_custom_outbox.append(message)

    def _deliver_live_drain(self, delivery: _DrainedMessageDelivery) -> None:
        with self._locked():
            user_outbox = self._attached_user_outbox
            custom_outbox = self._attached_custom_outbox
            if self._state != "live" or user_outbox is None or custom_outbox is None:
                return
            user_messages = tuple(user_outbox)
            custom_messages = tuple(custom_outbox)
            user_outbox.clear()
            custom_outbox.clear()
        self._observe("ordered_forwarding")
        delivery(user_messages, custom_messages)

    def mark_retired_locked(self, retirement: GenerationMessageRetirement) -> None:
        """Mark and detach by assignments only; the caller owns the session mutex."""

        if self._state == "uninstalled" or self._attached_user_outbox is None:
            return
        retirement.pending = self._pending
        retirement.gate = self._gate
        retirement.user_outbox = self._attached_user_outbox
        retirement.custom_outbox = self._attached_custom_outbox
        self._state = "retired"
        self._pending = self._gate = None
        self._attached_user_outbox = self._attached_custom_outbox = None

    def retire(self) -> tuple[object, ...]:
        if self._mutex is None:
            return ()
        retirement = GenerationMessageRetirement()
        with self._locked():
            self.mark_retired_locked(retirement)
        return retirement.finalize_retirement()

    def route_drain(self, delivery: _DrainedMessageDelivery) -> bool:
        if self._mutex is None:
            return False

        def forwarding() -> None:
            self._deliver_live_drain(delivery)

        claim: tuple[OrderedMessageDeliveryGate, _MessageDelivery] | None = None
        with self._locked():
            handled = self._state != "uninstalled"
            if self._state in ("candidate", "releasing"):
                self._pending_fifo().append(forwarding)
            elif self._state == "live":
                if self._gate is None:
                    raise RuntimeError("live message route has no delivery gate")
                claim = (self._gate, forwarding)
        if claim is not None:
            claim[0].submit(claim[1])
        return handled


def _reserved_message_delivery(
    owner: GenerationMessageRouting,
    target: list[_DeliveryValue],
    message: _DeliveryValue,
    append_live: Callable[[_DeliveryValue], None],
) -> tuple[_MessageDelivery, _MessageDelivery, _MessageDelivery]:
    def deliver() -> None:
        target.append(message)

    def forward() -> None:
        owner._observe("ordered_forwarding")
        deliver()

    def forward_live() -> None:
        owner._observe("ordered_forwarding")
        append_live(message)

    return deliver, forward, forward_live


def _routing_for_activation_batch(
    threaded: Iterable[GenerationMessageRouting],
    outbox: list[QueuedUserMessage],
    custom_outbox: list[QueuedCustomMessage],
    *,
    supplied: GenerationMessageRouting | None,
    required: GenerationMessageRouting | None = None,
) -> GenerationMessageRouting:
    routing = supplied
    for threaded_routing in threaded:
        if routing is None:
            routing = threaded_routing
        elif threaded_routing is not routing:
            raise ValueError("activation batch owner must match every host routing")
    routing = routing or GenerationMessageRouting(outbox, custom_outbox)
    if required is not None and routing is not required:
        raise ValueError("activation batch must retain its message routing")
    if routing.user_outbox is not outbox or routing.custom_outbox is not custom_outbox:
        raise ValueError("activation batch routing must own its exact outboxes")
    return routing

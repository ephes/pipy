"""Terminal-independent ownership for live extension chrome state.

The owner stores extension chrome values plus generation-owned regions and
hooks. Status rows and sticky working message/visibility survive a generation
clear, matching the product contract; header/footer/widgets, title/indicator,
terminal-input registrations, and footer rebuild state belong to the retired
generation and are detached. Candidate sidecars use one local guard for their
closed-check/write/close lifecycle. This owner performs no extension factory or
component calls, terminal I/O, filesystem inspection, or painting; those effects
remain on ``ToolLoopTerminalUi`` after the sidecar guard is released.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias


@dataclass(slots=True)
class ChromeRegion:
    """A stored chrome source + its last rendered snapshot.

    ``source`` is a zero-arg factory (already bound to the theme / footer_data)
    or a pre-coerced lines source. ``component`` is the built component for a
    factory source (created once), used to call ``dispose()``. ``snapshot`` is
    the rendered lines; ``width`` is the width they were rendered at.
    ``is_factory`` distinguishes reactive factory/component regions, which are
    re-rendered each frame and invalidated on resize, from static snapshots.
    """

    source: object
    component: object | None
    snapshot: tuple[str, ...]
    width: int
    is_factory: bool


@dataclass(frozen=True, slots=True)
class ExtensionChromeSnapshot:
    """Detached desired retained chrome for one candidate sidecar."""

    widgets: tuple[tuple[str, object, str], ...] = ()
    header: object | None = None
    footer: object | None = None
    title: str | None = None
    indicator_frames: object = None
    indicator_interval_ms: object = None
    terminal_input_listeners: tuple[tuple[int, Callable[[str], object]], ...] = ()
    autocomplete_providers: tuple[object, ...] = ()
    editor_component: object | None = None
    hidden_thinking_label: str | None = None


@dataclass(frozen=True, slots=True)
class ExtensionChromeEvent:
    """One accepted sidecar write delivered to the concrete TUI adapter."""

    kind: str
    values: tuple[object, ...]


ExtensionChromeDelivery: TypeAlias = Callable[[ExtensionChromeEvent], object]


@dataclass(frozen=True, slots=True)
class _PendingChromeDelivery:
    event: ExtensionChromeEvent
    listener_id: int | None = None


def _inert_disposer() -> None:
    """Preserve the listener registration return shape after sink close."""


class ExtensionChromeAttachPhase(Enum):
    """Observable point at which a sink attach completed or was refused."""

    ATTACHED = "attached"
    REFUSED_BEFORE_RECONCILE = "refused_before_reconcile"
    REFUSED_AFTER_RECONCILE = "refused_after_reconcile"


@dataclass(frozen=True, slots=True)
class ExtensionChromeAttachResult:
    """Structured attach outcome used to decide whether rollback must repaint."""

    phase: ExtensionChromeAttachPhase
    candidate_closed: bool = False

    @property
    def attached(self) -> bool:
        return self.phase is ExtensionChromeAttachPhase.ATTACHED

    @property
    def reconciled(self) -> bool:
        return self.phase is not ExtensionChromeAttachPhase.REFUSED_BEFORE_RECONCILE

    def __bool__(self) -> bool:
        return self.attached


@dataclass(slots=True)
class ExtensionChromeRetirement:
    """Detached cleanup from one sink-local close critical section."""

    sink: "ExtensionChromeSink"
    snapshot: ExtensionChromeSnapshot
    disposers: tuple[Callable[[], None], ...]
    retained: tuple[object, ...]
    finalized: bool = False

    def finalize(self) -> None:
        """Run disposal and release detached values with no guard held."""

        if self.finalized:
            return
        self.finalized = True
        for disposer in self.disposers:
            try:
                disposer()
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - one bad disposer must not strand cleanup
                continue
        with self.sink._guard:  # noqa: SLF001 - exact sink-local idle owner
            while self.sink._inflight:  # noqa: SLF001
                self.sink._idle.wait()  # noqa: SLF001
        self.disposers = ()
        self.retained = ()

    def finalize_nonraising(self) -> BaseException | None:
        try:
            self.finalize()
        except BaseException as error:  # noqa: BLE001 - non-raising: caller inspects the error
            return error
        return None


class ExtensionChromeSink:
    """Guarded retained chrome/listener requests owned by one candidate.

    Candidate sinks have no delivery callback, so writes only update detached
    desired state. Acceptance attaches the concrete TUI adapter after semantic
    commit. Every closed-check and mutation shares ``_guard`` with ``close``;
    adapter calls and listener disposal happen only after that guard is released.
    """

    def __init__(self, delivery: ExtensionChromeDelivery | None = None) -> None:
        self._guard = threading.RLock()
        self._idle = threading.Condition(self._guard)
        self._closed = False
        self._delivery = delivery
        self._attaching = False
        self._pending_deliveries: list[_PendingChromeDelivery] = []
        self._inflight = 0
        self._widgets: dict[str, tuple[object, str]] = {}
        self._header: object | None = None
        self._footer: object | None = None
        self._title: str | None = None
        self._indicator_frames: object = None
        self._indicator_interval_ms: object = None
        self._terminal_input_listeners: dict[int, Callable[[str], object]] = {}
        self._terminal_input_disposers: dict[int, Callable[[], None]] = {}
        self._terminal_input_next_id = 0
        self._autocomplete_providers: list[object] = []
        self._editor_component: object | None = None
        self._hidden_thinking_label: str | None = None

    @classmethod
    def from_snapshot(cls, snapshot: ExtensionChromeSnapshot) -> "ExtensionChromeSink":
        """Create a detached owner for a rollback repaint without reopening its sink."""

        sink = cls()
        with sink._guard:
            sink._widgets = {
                key: (content, placement)
                for key, content, placement in snapshot.widgets
            }
            sink._header = snapshot.header
            sink._footer = snapshot.footer
            sink._title = snapshot.title
            sink._indicator_frames = snapshot.indicator_frames
            sink._indicator_interval_ms = snapshot.indicator_interval_ms
            sink._terminal_input_listeners = dict(snapshot.terminal_input_listeners)
            sink._terminal_input_next_id = (
                max(sink._terminal_input_listeners, default=-1) + 1
            )
            sink._autocomplete_providers = list(snapshot.autocomplete_providers)
            sink._editor_component = snapshot.editor_component
            sink._hidden_thinking_label = snapshot.hidden_thinking_label
        return sink

    def snapshot(self) -> ExtensionChromeSnapshot:
        with self._guard:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> ExtensionChromeSnapshot:
        return ExtensionChromeSnapshot(
            widgets=tuple(
                (key, content, placement)
                for key, (content, placement) in self._widgets.items()
            ),
            header=self._header,
            footer=self._footer,
            title=self._title,
            indicator_frames=self._indicator_frames,
            indicator_interval_ms=self._indicator_interval_ms,
            terminal_input_listeners=tuple(self._terminal_input_listeners.items()),
            autocomplete_providers=tuple(self._autocomplete_providers),
            editor_component=self._editor_component,
            hidden_thinking_label=self._hidden_thinking_label,
        )

    def attach(self, delivery: ExtensionChromeDelivery) -> ExtensionChromeAttachResult:
        """Reconcile, drain writes that raced it, then expose live delivery.

        ``_delivery`` remains unpublished while the initial snapshot is being
        reconciled. Writes in that interval mutate desired state and queue one
        ordered event each. The attaching thread drains those events outside
        the guard before atomically exposing direct delivery, so a write can
        neither be erased by the snapshot nor delivered twice.
        """

        with self._guard:
            if self._closed or self._delivery is not None or self._attaching:
                return ExtensionChromeAttachResult(
                    ExtensionChromeAttachPhase.REFUSED_BEFORE_RECONCILE,
                    candidate_closed=self._closed,
                )
            self._attaching = True
            snapshot = self._snapshot_locked()
            self._inflight += 1
        try:
            result: object = None
            try:
                result = delivery(ExtensionChromeEvent("reconcile", (snapshot,)))
            finally:
                self._finish_delivery(result, snapshot=snapshot)
            while True:
                with self._guard:
                    if self._closed:
                        self._attaching = False
                        self._pending_deliveries.clear()
                        self._idle.notify_all()
                        return ExtensionChromeAttachResult(
                            ExtensionChromeAttachPhase.REFUSED_AFTER_RECONCILE,
                            candidate_closed=True,
                        )
                    if not self._pending_deliveries:
                        self._delivery = delivery
                        self._attaching = False
                        self._idle.notify_all()
                        return ExtensionChromeAttachResult(
                            ExtensionChromeAttachPhase.ATTACHED
                        )
                    pending = self._pending_deliveries.pop(0)
                    self._inflight += 1
                queued_result: object = None
                try:
                    queued_result = delivery(pending.event)
                finally:
                    self._finish_delivery(
                        queued_result, listener_id=pending.listener_id
                    )
        except BaseException:
            with self._guard:
                self._attaching = False
                # Every queued write remains represented in desired state. A
                # retry starts from a fresh complete snapshot, so replaying the
                # abandoned event list would duplicate it.
                self._pending_deliveries.clear()
                self._idle.notify_all()
            raise

    def reconcile_attached(self, delivery: ExtensionChromeDelivery) -> None:
        """Repaint this live sink's complete snapshot for acceptance rollback."""

        with self._guard:
            if self._closed:
                raise RuntimeError("extension chrome sink is closed")
            snapshot = self._snapshot_locked()
            self._inflight += 1
        result: object = None
        try:
            result = delivery(ExtensionChromeEvent("reconcile", (snapshot,)))
        finally:
            self._finish_delivery(result, snapshot=snapshot)

    def discard_reconciled_disposers(self) -> None:
        """Forget listener disposers after another snapshot replaced this one."""

        with self._guard:
            self._terminal_input_disposers.clear()

    def _write(self, event: ExtensionChromeEvent, mutate: Callable[[], object]) -> None:
        retired: object = None
        with self._guard:
            if self._closed:
                return
            retired = mutate()
            if self._attaching:
                self._pending_deliveries.append(_PendingChromeDelivery(event))
                delivery = None
            else:
                delivery = self._delivery
                if delivery is not None:
                    self._inflight += 1
        try:
            if delivery is not None:
                result: object = None
                try:
                    result = delivery(event)
                finally:
                    self._finish_delivery(result)
        finally:
            # Keep a displaced callback/component/factory reachable until after
            # the guard and any delivery have both completed.
            _ = retired

    def _finish_delivery(
        self,
        result: object,
        *,
        listener_id: int | None = None,
        snapshot: ExtensionChromeSnapshot | None = None,
    ) -> None:
        stale_disposers: list[Callable[[], None]] = []
        with self._guard:
            if snapshot is not None and isinstance(result, Mapping):
                for registered_id, disposer in result.items():
                    if not isinstance(registered_id, int) or not callable(disposer):
                        continue
                    if (
                        not self._closed
                        and registered_id in self._terminal_input_listeners
                    ):
                        self._terminal_input_disposers[registered_id] = disposer
                    else:
                        stale_disposers.append(disposer)
            elif listener_id is not None and callable(result):
                if not self._closed and listener_id in self._terminal_input_listeners:
                    self._terminal_input_disposers[listener_id] = result
                else:
                    stale_disposers.append(result)
        try:
            self._dispose_stale(stale_disposers)
        finally:
            with self._guard:
                if self._inflight:
                    self._inflight -= 1
                    self._idle.notify_all()

    @staticmethod
    def _dispose_stale(disposers: list[Callable[[], None]]) -> None:
        for disposer in disposers:
            try:
                disposer()
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - one stale listener must not leak later cleanup
                # One stale listener must not leak later cleanup or replace the
                # result of the delivery that made it stale.
                continue

    def set_widget(self, key: str, content: object, placement: str) -> None:
        def mutate() -> object:
            if content is None:
                return self._widgets.pop(key, None)
            retired = self._widgets.get(key)
            self._widgets[key] = (content, placement)
            return retired

        self._write(ExtensionChromeEvent("widget", (key, content, placement)), mutate)

    def set_header(self, factory: object | None) -> None:
        def mutate() -> object:
            retired = self._header
            self._header = factory
            return retired

        self._write(ExtensionChromeEvent("header", (factory,)), mutate)

    def set_footer(self, factory: object | None) -> None:
        def mutate() -> object:
            retired = self._footer
            self._footer = factory
            return retired

        self._write(ExtensionChromeEvent("footer", (factory,)), mutate)

    def set_title(self, title: str) -> None:
        def mutate() -> object:
            retired = self._title
            self._title = title
            return retired

        self._write(ExtensionChromeEvent("title", (title,)), mutate)

    def set_working_indicator(self, frames: object, interval_ms: object) -> None:
        def mutate() -> object:
            retired = (self._indicator_frames, self._indicator_interval_ms)
            self._indicator_frames = frames
            self._indicator_interval_ms = interval_ms
            return retired

        self._write(ExtensionChromeEvent("indicator", (frames, interval_ms)), mutate)

    def set_hidden_thinking_label(self, label: str | None) -> None:
        def mutate() -> object:
            retired = self._hidden_thinking_label
            self._hidden_thinking_label = label
            return retired

        self._write(ExtensionChromeEvent("hidden-thinking-label", (label,)), mutate)

    def add_autocomplete_provider(self, factory: object) -> None:
        def mutate() -> object:
            self._autocomplete_providers.append(factory)
            return None

        self._write(ExtensionChromeEvent("autocomplete", (factory,)), mutate)

    def set_editor_component(self, factory: object | None) -> None:
        def mutate() -> object:
            retired = self._editor_component
            self._editor_component = factory
            return retired

        self._write(ExtensionChromeEvent("editor-component", (factory,)), mutate)

    def add_terminal_input_listener(
        self, handler: Callable[[str], object]
    ) -> Callable[[], None]:
        with self._guard:
            if self._closed:
                return _inert_disposer
            listener_id = self._terminal_input_next_id
            self._terminal_input_next_id += 1
            self._terminal_input_listeners[listener_id] = handler
            event = ExtensionChromeEvent("listener", (listener_id, handler))
            if self._attaching:
                self._pending_deliveries.append(
                    _PendingChromeDelivery(event, listener_id=listener_id)
                )
                delivery = None
            else:
                delivery = self._delivery
                if delivery is not None:
                    self._inflight += 1
        result: object = None
        if delivery is not None:
            try:
                result = delivery(event)
            finally:
                self._finish_delivery(result, listener_id=listener_id)

        def dispose() -> None:
            with self._guard:
                retired_handler = self._terminal_input_listeners.pop(listener_id, None)
                live_disposer = self._terminal_input_disposers.pop(listener_id, None)
            if live_disposer is not None:
                live_disposer()
            _ = retired_handler

        return dispose

    def mark_closed(self) -> ExtensionChromeRetirement | None:
        """Atomically close and detach cleanup under only the sink-local guard."""

        with self._guard:
            if self._closed:
                return None
            self._closed = True
            snapshot = self._snapshot_locked()
            disposers = tuple(self._terminal_input_disposers.values())
            retained = (
                self._delivery,
                self._pending_deliveries,
                self._widgets,
                self._header,
                self._footer,
                self._title,
                self._indicator_frames,
                self._indicator_interval_ms,
                self._terminal_input_listeners,
                self._terminal_input_disposers,
                self._autocomplete_providers,
                self._editor_component,
                self._hidden_thinking_label,
            )
            self._delivery = None
            self._pending_deliveries = []
            self._widgets = {}
            self._header = None
            self._footer = None
            self._title = None
            self._indicator_frames = None
            self._indicator_interval_ms = None
            self._terminal_input_listeners = {}
            self._terminal_input_disposers = {}
            self._autocomplete_providers = []
            self._editor_component = None
            self._hidden_thinking_label = None
        return ExtensionChromeRetirement(self, snapshot, disposers, retained)

    def close(self) -> None:
        """Close once, then dispose and release after the sink guard."""

        retirement = self.mark_closed()
        if retirement is not None:
            retirement.finalize()


@dataclass(slots=True)
class ExtensionChromeState:
    """Single mutable owner for extension chrome and listener bookkeeping."""

    generation: int = 0
    working_message: str | None = None
    working_visible: bool = True
    statuses: dict[str, str] = field(default_factory=dict)
    widgets_above: dict[str, ChromeRegion] = field(default_factory=dict)
    widgets_below: dict[str, ChromeRegion] = field(default_factory=dict)
    header: ChromeRegion | None = None
    footer: ChromeRegion | None = None
    footer_factory: object | None = None
    footer_branch: str | None = None
    footer_branch_callbacks: dict[int, Callable[[], object]] = field(
        default_factory=dict
    )
    footer_branch_callback_next_id: int = 0
    footer_branch_slots: tuple[int, ...] = ()
    footer_branch_rebuild_slots: tuple[int, ...] | None = None
    footer_branch_rebuild_index: int = 0
    footer_branch_rebuild_active_ids: frozenset[int] = frozenset()
    footer_branch_rebuild_new_slots: list[int] = field(default_factory=list)
    footer_branch_rebuild_fire_ids: list[int] = field(default_factory=list)
    footer_branch_last_check: float = 0.0
    footer_branch_check_interval: float = 0.25
    title: str | None = None
    indicator_frames: tuple[str, ...] | None = None
    indicator_interval_ms: float | None = None
    terminal_input_listeners: dict[int, Callable[[str], object]] = field(
        default_factory=dict
    )
    terminal_input_next_id: int = 0
    terminal_input_last_replaced: bool = False

    def set_status(self, key: str, text: str | None) -> None:
        if text is None:
            self.statuses.pop(key, None)
        else:
            self.statuses[key] = text

    def widget_maps(
        self, placement: str
    ) -> tuple[dict[str, ChromeRegion], dict[str, ChromeRegion]]:
        if placement == "below_editor":
            return self.widgets_below, self.widgets_above
        return self.widgets_above, self.widgets_below

    def set_working_message(self, message: str | None) -> None:
        self.working_message = message

    def set_working_visible(self, visible: bool) -> None:
        self.working_visible = visible

    def set_indicator(
        self,
        *,
        frames: tuple[str, ...] | None,
        interval_ms: float | None,
        replace_frames: bool,
    ) -> None:
        if replace_frames:
            self.indicator_frames = frames
        self.indicator_interval_ms = interval_ms

    def register_terminal_input_listener(
        self, handler: Callable[[str], object]
    ) -> tuple[int, int]:
        listener_id = self.terminal_input_next_id
        self.terminal_input_next_id += 1
        self.terminal_input_listeners[listener_id] = handler
        return self.generation, listener_id

    def remove_terminal_input_listener(self, generation: int, listener_id: int) -> None:
        if generation == self.generation:
            self.terminal_input_listeners.pop(listener_id, None)

    def register_footer_branch_callback(
        self, callback: Callable[[], object]
    ) -> tuple[int, int]:
        if self.footer_branch_rebuild_slots is not None:
            if self.footer_branch_rebuild_index < len(self.footer_branch_rebuild_slots):
                callback_id = self.footer_branch_rebuild_slots[
                    self.footer_branch_rebuild_index
                ]
            else:
                callback_id = self.footer_branch_callback_next_id
                self.footer_branch_callback_next_id += 1
            self.footer_branch_rebuild_index += 1
            self.footer_branch_rebuild_new_slots.append(callback_id)
            if callback_id in self.footer_branch_rebuild_active_ids:
                self.footer_branch_rebuild_fire_ids.append(callback_id)
        else:
            callback_id = self.footer_branch_callback_next_id
            self.footer_branch_callback_next_id += 1
            self.footer_branch_slots = (*self.footer_branch_slots, callback_id)
        self.footer_branch_callbacks[callback_id] = callback
        self.footer_branch_last_check = 0.0
        return self.generation, callback_id

    def remove_footer_branch_callback(self, generation: int, callback_id: int) -> None:
        if generation == self.generation:
            self.footer_branch_callbacks.pop(callback_id, None)

    def clear_footer_branch_callbacks(self) -> None:
        self.footer_branch_callbacks.clear()
        self.footer_branch_slots = ()
        self.footer_branch_last_check = 0.0

    def begin_footer_rebuild(self, branch: str | None) -> None:
        slots_before = self.footer_branch_slots
        active_before = frozenset(self.footer_branch_callbacks)
        self.footer_branch = branch
        self.footer_branch_callbacks.clear()
        self.footer_branch_rebuild_slots = slots_before
        self.footer_branch_rebuild_index = 0
        self.footer_branch_rebuild_active_ids = active_before
        self.footer_branch_rebuild_new_slots = []
        self.footer_branch_rebuild_fire_ids = []

    def _reset_footer_rebuild(self) -> None:
        self.footer_branch_rebuild_slots = None
        self.footer_branch_rebuild_index = 0
        self.footer_branch_rebuild_active_ids = frozenset()
        self.footer_branch_rebuild_new_slots = []
        self.footer_branch_rebuild_fire_ids = []

    def finish_footer_rebuild(self) -> tuple[Callable[[], object], ...]:
        self.footer_branch_slots = tuple(self.footer_branch_rebuild_new_slots)
        callbacks = tuple(
            self.footer_branch_callbacks[callback_id]
            for callback_id in self.footer_branch_rebuild_fire_ids
            if callback_id in self.footer_branch_callbacks
        )
        self._reset_footer_rebuild()
        return callbacks

    def abort_footer_rebuild(self) -> None:
        self._reset_footer_rebuild()

    def regions_for_clear(self) -> tuple[ChromeRegion, ...]:
        """Snapshot regions for effectful disposal before state retirement."""

        return tuple(
            region
            for region in (
                *self.widgets_above.values(),
                *self.widgets_below.values(),
                self.header,
                self.footer,
            )
            if region is not None
        )

    def detach_generation_for_disposal(self) -> tuple[ChromeRegion, ...]:
        """Detach old regions before unlocked disposal, without advancing identity.

        Disposal-time registrations still receive the retiring generation id.
        A subsequent :meth:`retire_generation` clears those reentrant writes and
        advances the id, so their disposers cannot target fresh registrations.
        """

        regions = self.regions_for_clear()
        self._clear_generation_state()
        return regions

    def retire_generation(self) -> None:
        """Clear disposal-time writes and advance past the retired generation."""

        self.generation += 1
        self._clear_generation_state()

    def _clear_generation_state(self) -> None:
        self.widgets_above.clear()
        self.widgets_below.clear()
        self.header = None
        self.footer = None
        self.footer_factory = None
        self.footer_branch = None
        self.clear_footer_branch_callbacks()
        self.title = None
        self.indicator_frames = None
        self.indicator_interval_ms = None
        self.terminal_input_listeners.clear()
        self.abort_footer_rebuild()

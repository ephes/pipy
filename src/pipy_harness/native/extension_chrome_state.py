"""Terminal-independent ownership for live extension chrome state.

The owner stores extension chrome values plus generation-owned regions and
hooks. Status rows and sticky working message/visibility survive a generation
clear, matching the product contract; header/footer/widgets, title/indicator,
terminal-input registrations, and footer rebuild state belong to the retired
generation and are detached. It performs no extension factory or component
calls, terminal I/O, filesystem inspection, painting, or locking; those effects
remain on ``ToolLoopTerminalUi``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


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

    def retire_generation(self) -> None:
        """Clear generation state after disposal while retaining sticky values."""

        self.generation += 1
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

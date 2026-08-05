"""Ordered ownership for retiring and reconciling extension UI generations.

A generation crosses four UI participants in a fixed order: chrome regions and
listeners, autocomplete/custom-editor state, folded-thinking state, and the
terminal title.  Retirement is deliberately three-phase.  Each participant
prepares a retirement without mutating shared state, all detach transitions run
atomically under the shared :class:`PaintLock`, trusted disposal runs with no
paint/owner/sink guard held, and all final retirement transitions run atomically
under the same lock.  Reconciliation follows the same participant order after
fully retiring the predecessor.

This module is below the terminal facade boundary.  It therefore takes only the
shared chrome record, the paint lock, repaint, and narrow injected callables for
sibling-owner and terminal effects; it never names or imports the facade.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Protocol

from pipy_harness.native.extension_chrome_state import (
    ChromeRegion,
    ExtensionChromeSnapshot,
    ExtensionChromeState,
)
from pipy_harness.native.ui.paint_lock import PaintLock


class _Retirement(Protocol):
    def detach(self) -> None: ...

    def dispose(self) -> None: ...

    def finalize(self) -> None: ...


class _GenerationParticipant(Protocol):
    """The deliberately narrow lifecycle shared by generation participants."""

    def retire_generation(self) -> _Retirement: ...

    def reconcile_generation(
        self, snapshot: ExtensionChromeSnapshot
    ) -> Mapping[int, Callable[[], None]] | None: ...


@dataclass(slots=True)
class _ChromeRetirement:
    record: ExtensionChromeState
    dispose_region: Callable[[ChromeRegion], None]
    regions: tuple[ChromeRegion, ...] = ()

    def detach(self) -> None:
        self.regions = self.record.detach_generation_for_disposal()

    def dispose(self) -> None:
        for region in self.regions:
            self.dispose_region(region)

    def finalize(self) -> None:
        self.record.retire_generation()


class _ChromeParticipant:
    def __init__(
        self,
        record: ExtensionChromeState,
        *,
        dispose_region: Callable[[ChromeRegion], None],
        set_widget: Callable[[str, object, str], None],
        set_header: Callable[[object | None], None],
        set_footer: Callable[[object | None], None],
        set_title: Callable[[str], None],
        set_indicator: Callable[[object, object], None],
        add_listener: Callable[[Callable[[str], object]], Callable[[], None]],
    ) -> None:
        self._record = record
        self._dispose_region = dispose_region
        self._set_widget = set_widget
        self._set_header = set_header
        self._set_footer = set_footer
        self._set_title = set_title
        self._set_indicator = set_indicator
        self._add_listener = add_listener

    def retire_generation(self) -> _Retirement:
        return _ChromeRetirement(self._record, self._dispose_region)

    def reconcile_generation(
        self, snapshot: ExtensionChromeSnapshot
    ) -> Mapping[int, Callable[[], None]]:
        for key, content, placement in snapshot.widgets:
            self._set_widget(key, content, placement)
        self._set_header(snapshot.header)
        self._set_footer(snapshot.footer)
        if snapshot.title is not None:
            self._set_title(snapshot.title)
        self._set_indicator(
            snapshot.indicator_frames,
            snapshot.indicator_interval_ms,
        )
        return {
            listener_id: self._add_listener(handler)
            for listener_id, handler in snapshot.terminal_input_listeners
        }


@dataclass(slots=True)
class _EditorRetirement:
    had_custom_editor: bool
    current_text: str | None
    current_component: Callable[[], object | None]
    clear_autocomplete: Callable[[], None]
    clear_custom_editor: Callable[[], None]
    restore_editor_text: Callable[[str], None]
    custom_editor: object | None = None

    def detach(self) -> None:
        self.custom_editor = (
            self.current_component() if self.had_custom_editor else None
        )
        self.clear_autocomplete()
        self.clear_custom_editor()

    def dispose(self) -> None:
        if self.custom_editor is None:
            return
        dispose = getattr(self.custom_editor, "dispose", None)
        if callable(dispose):
            try:
                dispose()
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - trusted extension cleanup is fail-soft
                pass

    def finalize(self) -> None:
        self.clear_autocomplete()
        self.clear_custom_editor()
        if self.had_custom_editor:
            assert self.current_text is not None
            self.restore_editor_text(self.current_text)


class _EditorParticipant:
    def __init__(
        self,
        *,
        custom_editor_active: Callable[[], bool],
        read_input_text: Callable[[], str],
        current_custom_editor_component: Callable[[], object | None],
        clear_autocomplete: Callable[[], None],
        clear_custom_editor: Callable[[], None],
        restore_editor_text: Callable[[str], None],
        add_autocomplete_provider: Callable[[object], None],
        set_editor_component: Callable[[object | None], None],
    ) -> None:
        self._custom_editor_active = custom_editor_active
        self._read_input_text = read_input_text
        self._current_custom_editor_component = current_custom_editor_component
        self._clear_autocomplete = clear_autocomplete
        self._clear_custom_editor = clear_custom_editor
        self._restore_editor_text = restore_editor_text
        self._add_autocomplete_provider = add_autocomplete_provider
        self._set_editor_component = set_editor_component

    def retire_generation(self) -> _Retirement:
        # Reading custom text can call trusted extension code.  It precedes all
        # state detachment and intentionally runs without the paint lock.
        had_custom_editor = self._custom_editor_active()
        current_text = self._read_input_text() if had_custom_editor else None
        return _EditorRetirement(
            had_custom_editor,
            current_text,
            self._current_custom_editor_component,
            self._clear_autocomplete,
            self._clear_custom_editor,
            self._restore_editor_text,
        )

    def reconcile_generation(
        self, snapshot: ExtensionChromeSnapshot
    ) -> Mapping[int, Callable[[], None]] | None:
        for factory in snapshot.autocomplete_providers:
            self._add_autocomplete_provider(factory)
        self._set_editor_component(snapshot.editor_component)
        return None


@dataclass(frozen=True, slots=True)
class _CallbackRetirement:
    callback: Callable[[], None]

    def detach(self) -> None:
        return None

    def dispose(self) -> None:
        return None

    def finalize(self) -> None:
        self.callback()


class _TranscriptParticipant:
    def __init__(
        self,
        *,
        reset_hidden_thinking_label: Callable[[], None],
        set_hidden_thinking_label: Callable[[str | None], None],
    ) -> None:
        self._reset_hidden_thinking_label = reset_hidden_thinking_label
        self._set_hidden_thinking_label = set_hidden_thinking_label

    def retire_generation(self) -> _Retirement:
        return _CallbackRetirement(self._reset_hidden_thinking_label)

    def reconcile_generation(
        self, snapshot: ExtensionChromeSnapshot
    ) -> Mapping[int, Callable[[], None]] | None:
        self._set_hidden_thinking_label(snapshot.hidden_thinking_label)
        return None


class _TitleParticipant:
    def __init__(self, restore_title: Callable[[], None]) -> None:
        self._restore_title = restore_title

    def retire_generation(self) -> _Retirement:
        return _CallbackRetirement(self._restore_title)

    def reconcile_generation(
        self, snapshot: ExtensionChromeSnapshot
    ) -> Mapping[int, Callable[[], None]] | None:
        del snapshot
        return None


# This is an ordering contract, not documentation.  Its members are the
# participant implementations themselves, each with the common lifecycle.
EXTENSION_GENERATION_PARTICIPANTS = (
    _ChromeParticipant,
    _EditorParticipant,
    _TranscriptParticipant,
    _TitleParticipant,
)


class ExtensionGenerationOwner:
    """Named owner of the ordered extension-generation lifecycle."""

    def __init__(
        self,
        record: ExtensionChromeState,
        paint_lock: PaintLock,
        repaint: Callable[[], None],
        *,
        dispose_region: Callable[[ChromeRegion], None],
        custom_editor_active: Callable[[], bool],
        read_input_text: Callable[[], str],
        current_custom_editor_component: Callable[[], object | None],
        clear_autocomplete: Callable[[], None],
        clear_custom_editor: Callable[[], None],
        restore_editor_text: Callable[[str], None],
        restore_title: Callable[[], None],
        reset_hidden_thinking_label: Callable[[], None],
        set_widget: Callable[[str, object, str], None],
        set_header: Callable[[object | None], None],
        set_footer: Callable[[object | None], None],
        set_title: Callable[[str], None],
        set_indicator: Callable[[object, object], None],
        add_listener: Callable[[Callable[[str], object]], Callable[[], None]],
        add_autocomplete_provider: Callable[[object], None],
        set_editor_component: Callable[[object | None], None],
        set_hidden_thinking_label: Callable[[str | None], None],
    ) -> None:
        self._record = record
        self._paint_lock = paint_lock
        self._repaint = repaint
        participants: dict[type[object], _GenerationParticipant] = {
            _ChromeParticipant: _ChromeParticipant(
                record,
                dispose_region=dispose_region,
                set_widget=set_widget,
                set_header=set_header,
                set_footer=set_footer,
                set_title=set_title,
                set_indicator=set_indicator,
                add_listener=add_listener,
            ),
            _EditorParticipant: _EditorParticipant(
                custom_editor_active=custom_editor_active,
                read_input_text=read_input_text,
                current_custom_editor_component=current_custom_editor_component,
                clear_autocomplete=clear_autocomplete,
                clear_custom_editor=clear_custom_editor,
                restore_editor_text=restore_editor_text,
                add_autocomplete_provider=add_autocomplete_provider,
                set_editor_component=set_editor_component,
            ),
            _TranscriptParticipant: _TranscriptParticipant(
                reset_hidden_thinking_label=reset_hidden_thinking_label,
                set_hidden_thinking_label=set_hidden_thinking_label,
            ),
            _TitleParticipant: _TitleParticipant(restore_title),
        }
        self._participants = tuple(
            participants[participant_type]
            for participant_type in EXTENSION_GENERATION_PARTICIPANTS
        )

    @property
    def generation(self) -> int:
        return self._record.generation

    def retire_generation(
        self,
        *,
        retirement_scope: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> None:
        """Detach, dispose unlocked, then atomically finalize every participant."""

        retirements = tuple(
            participant.retire_generation() for participant in self._participants
        )
        with self._paint_lock:
            for retirement in retirements:
                retirement.detach()

        dispose_scope = retirement_scope() if retirement_scope else nullcontext()
        try:
            with dispose_scope:
                for retirement in retirements:
                    retirement.dispose()
        finally:
            with self._paint_lock:
                for retirement in retirements:
                    retirement.finalize()
        self._repaint()

    def reconcile_generation(
        self,
        snapshot: ExtensionChromeSnapshot,
        *,
        retirement_scope: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> dict[int, Callable[[], None]]:
        """Retire the predecessor, then install accepted state in fixed order."""

        self.retire_generation(retirement_scope=retirement_scope)
        listener_disposers: dict[int, Callable[[], None]] = {}
        for participant in self._participants:
            result = participant.reconcile_generation(snapshot)
            if result is not None:
                listener_disposers.update(result)
        return listener_disposers

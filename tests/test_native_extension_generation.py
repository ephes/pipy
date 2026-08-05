"""Focused ownership and ordering contracts for extension generations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypeVar

from pipy_harness.native.extension_chrome_state import (
    ChromeRegion,
    ExtensionChromeSnapshot,
    ExtensionChromeState,
)
from pipy_harness.native.ui.extension_generation import (
    EXTENSION_GENERATION_PARTICIPANTS,
    ExtensionGenerationOwner,
)
from pipy_harness.native.ui.paint_lock import PaintLock

_T = TypeVar("_T")


def _record(events: list[str], event: str, value: _T) -> _T:
    events.append(event)
    return value


def _owner(
    record: ExtensionChromeState,
    events: list[str],
    *,
    custom_editor_active: bool = False,
    current_component: object | None = None,
) -> ExtensionGenerationOwner:
    return ExtensionGenerationOwner(
        record,
        PaintLock(),
        lambda: events.append("repaint"),
        dispose_region=lambda region: events.append(
            f"dispose-region:{region.snapshot[0]}"
        ),
        custom_editor_active=lambda: _record(
            events, "custom-editor-active", custom_editor_active
        ),
        read_input_text=lambda: _record(events, "read-input-text", "custom draft"),
        current_custom_editor_component=lambda: _record(
            events, "detach-custom-editor", current_component
        ),
        clear_autocomplete=lambda: events.append("clear-autocomplete"),
        clear_custom_editor=lambda: events.append("clear-custom-editor"),
        restore_editor_text=lambda text: events.append(f"restore-editor:{text}"),
        restore_title=lambda: events.append("restore-title"),
        reset_hidden_thinking_label=lambda: events.append("reset-thinking"),
        set_widget=lambda key, _content, placement: events.append(
            f"widget:{key}:{placement}"
        ),
        set_header=lambda _header: events.append("header"),
        set_footer=lambda _footer: events.append("footer"),
        set_title=lambda title: events.append(f"title:{title}"),
        set_indicator=lambda _frames, _interval: events.append("indicator"),
        add_listener=lambda handler: _record(
            events,
            f"listener:{handler('probe')}",
            lambda: events.append("dispose-listener"),
        ),
        add_autocomplete_provider=lambda _factory: events.append("autocomplete"),
        set_editor_component=lambda _factory: events.append("editor"),
        set_hidden_thinking_label=lambda label: events.append(f"thinking:{label}"),
    )


def test_generation_participant_order_is_explicit_and_stable() -> None:
    assert tuple(
        participant.__name__ for participant in EXTENSION_GENERATION_PARTICIPANTS
    ) == (
        "_ChromeParticipant",
        "_EditorParticipant",
        "_TranscriptParticipant",
        "_TitleParticipant",
    )
    assert all(
        callable(participant.retire_generation)
        and callable(participant.reconcile_generation)
        for participant in EXTENSION_GENERATION_PARTICIPANTS
    )


def test_retirement_preserves_detach_dispose_finalize_order() -> None:
    events: list[str] = []
    record = ExtensionChromeState()
    record.widgets_above["old"] = ChromeRegion(
        source=["old"],
        component=object(),
        snapshot=("old",),
        width=80,
        is_factory=False,
    )

    class CustomEditor:
        def dispose(self) -> None:
            events.append("dispose-custom-editor")

    @contextmanager
    def retirement_scope() -> Iterator[None]:
        events.append("scope-enter")
        try:
            yield
        finally:
            events.append("scope-exit")

    owner = _owner(
        record,
        events,
        custom_editor_active=True,
        current_component=CustomEditor(),
    )
    owner.retire_generation(retirement_scope=retirement_scope)

    assert events == [
        "custom-editor-active",
        "read-input-text",
        "detach-custom-editor",
        "clear-autocomplete",
        "clear-custom-editor",
        "scope-enter",
        "dispose-region:old",
        "dispose-custom-editor",
        "scope-exit",
        "clear-autocomplete",
        "clear-custom-editor",
        "restore-editor:custom draft",
        "reset-thinking",
        "restore-title",
        "repaint",
    ]
    assert record.generation == 1
    assert record.widgets_above == {}


def test_reconcile_retires_first_then_installs_snapshot_in_order() -> None:
    events: list[str] = []
    record = ExtensionChromeState()

    def listener(key: str) -> str:
        return f"handled-{key}"

    autocomplete_factory = object()
    editor_factory = object()
    snapshot = ExtensionChromeSnapshot(
        widgets=(("widget", object(), "below_editor"),),
        header=object(),
        footer=object(),
        title="accepted",
        indicator_frames=(".", "o"),
        indicator_interval_ms=50,
        terminal_input_listeners=((7, listener),),
        autocomplete_providers=(autocomplete_factory,),
        editor_component=editor_factory,
        hidden_thinking_label="Folded",
    )

    disposers = _owner(record, events).reconcile_generation(snapshot)

    assert events == [
        "custom-editor-active",
        "clear-autocomplete",
        "clear-custom-editor",
        "clear-autocomplete",
        "clear-custom-editor",
        "reset-thinking",
        "restore-title",
        "repaint",
        "widget:widget:below_editor",
        "header",
        "footer",
        "title:accepted",
        "indicator",
        "listener:handled-probe",
        "autocomplete",
        "editor",
        "thinking:Folded",
    ]
    assert tuple(disposers) == (7,)
    assert callable(disposers[7])

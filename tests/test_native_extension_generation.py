"""Focused ownership and ordering contracts for extension generations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from pipy_harness.native.editor_state import EditorState
from pipy_harness.native.extension_chrome_state import (
    ChromeRegion,
    ExtensionChromeSnapshot,
    ExtensionChromeState,
)
from pipy_harness.native.ui.autocomplete import AutocompleteComponent
from pipy_harness.native.ui.components.custom_editor import (
    CustomEditorEffects,
    CustomEditorOwner,
    CustomEditorState,
)
from pipy_harness.native.ui.components.input_editor import InputEditor
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
    editor_state = EditorState(text="custom draft")
    paint_lock = PaintLock()

    def noop() -> None:
        return None

    custom_editor = CustomEditorOwner(
        CustomEditorState(
            component=current_component if custom_editor_active else None,
            active=custom_editor_active,
        ),
        editor_state,
        paint_lock,
        noop,
        host=object(),
        theme=lambda: object(),
        keybindings_manager=lambda: None,
        effects=CustomEditorEffects(
            restore_input_text=lambda _text: None,
            clear_initial_text=noop,
            enqueue_follow_up=lambda _text: None,
            restore_pending=noop,
            paste_clipboard_image=noop,
            external_editor=lambda _text: None,
            autocomplete_provider=lambda: None,
        ),
    )
    autocomplete = AutocompleteComponent(
        editor_state,
        cwd=Path("."),
        repaint=noop,
        custom_editor=custom_editor,
    )
    input_editor = InputEditor(
        editor_state,
        paint_lock,
        noop,
        command_names=lambda: (),
        refresh_autocomplete=noop,
        custom_editor=custom_editor,
        insert_paste=lambda _text: None,
    )
    autocomplete.clear_generation_state = lambda: events.append("clear-autocomplete")  # type: ignore[method-assign]
    autocomplete.add_extension_provider = lambda _factory: events.append("autocomplete")  # type: ignore[assignment,method-assign]
    custom_editor.clear_generation_state = lambda: events.append("clear-custom-editor")  # type: ignore[method-assign]
    custom_editor.set_editor_component = lambda _factory: events.append("editor")  # type: ignore[assignment,method-assign]

    def restore_editor_text(text: str) -> None:
        events.append(f"restore-editor:{text}")

    input_editor.restore_generation_text = restore_editor_text  # type: ignore[method-assign]
    return ExtensionGenerationOwner(
        record,
        paint_lock,
        lambda: events.append("repaint"),
        dispose_region=lambda region: events.append(
            f"dispose-region:{region.snapshot[0]}"
        ),
        editor=input_editor,
        autocomplete=autocomplete,
        custom_editor=custom_editor,
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

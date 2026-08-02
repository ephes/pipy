"""Adaptation contracts from the TUI facade to terminal-free editor state."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pipy_harness.native.editor_state import CompletionItem
from pipy_harness.native.tui import ToolLoopTerminalUi

_EDITOR_WRITABLE_PROJECTIONS = (
    ("input_text", "text", "owner text", "assigned text"),
    ("input_cursor", "cursor", 3, 2),
    ("input_history", "input_history", ["owner history"], ["assigned history"]),
    ("_history_nav_index", "history_nav_index", 4, 1),
    ("_history_draft", "history_draft", "owner draft", "assigned draft"),
    ("_pending_paste", "pending_paste", "owner paste", "assigned paste"),
    (
        "_pending_initial_text",
        "pending_initial_text",
        "owner initial",
        "assigned initial",
    ),
    ("slash_menu_open", "slash_menu_open", True, False),
    ("slash_menu_selection", "slash_menu_selection", 3, 2),
    ("autocomplete_open", "autocomplete_open", True, False),
    (
        "autocomplete_items",
        "autocomplete_items",
        (CompletionItem("owner", "Owner"),),
        (CompletionItem("assigned", "Assigned"),),
    ),
    ("autocomplete_selection", "autocomplete_selection", 3, 1),
    ("autocomplete_mode", "autocomplete_mode", "path", "at"),
    ("autocomplete_token_start", "autocomplete_token_start", 7, 5),
    ("autocomplete_prefix", "autocomplete_prefix", "owner", "assigned"),
)
_EDITOR_READ_ONLY_PROJECTIONS = (
    "_undo_stack",
    "_redo_stack",
    "_autocomplete_active_provider",
    "_autocomplete_provider_factories",
)
_RETIRED_UNPROJECTED_NAMES = (
    "_pending_steering",
    "_pending_follow_up",
    "_pending_drain",
    "_pending_drain_kinds",
    "_last_drain_kind",
    "_pending_command",
)


def _ui(tmp_path: Path) -> ToolLoopTerminalUi:
    return ToolLoopTerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
    )


@pytest.mark.parametrize(
    ("facade_name", "owner_name", "owner_value", "assigned_value"),
    _EDITOR_WRITABLE_PROJECTIONS,
)
def test_writable_facade_projection_is_bidirectional(
    tmp_path: Path,
    facade_name: str,
    owner_name: str,
    owner_value: object,
    assigned_value: object,
) -> None:
    ui = _ui(tmp_path)

    setattr(ui._editor, owner_name, owner_value)
    assert getattr(ui, facade_name) is owner_value

    setattr(ui, facade_name, assigned_value)
    assert getattr(ui._editor, owner_name) is assigned_value


@pytest.mark.parametrize("facade_name", _EDITOR_READ_ONLY_PROJECTIONS)
def test_read_only_facade_projection_observes_owner_and_rejects_assignment(
    tmp_path: Path, facade_name: str
) -> None:
    ui = _ui(tmp_path)
    marker = object()

    if facade_name == "_undo_stack":
        ui._editor.undo_stack.append(("owner", 5))
        expected: object = ui._editor.undo_stack
    elif facade_name == "_redo_stack":
        ui._editor.redo_stack.append(("owner", 5))
        expected = ui._editor.redo_stack
    elif facade_name == "_autocomplete_active_provider":
        ui._editor.autocomplete_active_provider = marker
        expected = marker
    else:
        ui._editor.autocomplete_provider_factories.append(marker)
        expected = ui._editor.autocomplete_provider_factories

    assert getattr(ui, facade_name) is expected
    with pytest.raises(AttributeError):
        setattr(ui, facade_name, object())


@pytest.mark.parametrize("retired_name", _RETIRED_UNPROJECTED_NAMES)
def test_slotted_facade_rejects_silent_dead_writes(
    tmp_path: Path, retired_name: str
) -> None:
    ui = _ui(tmp_path)

    with pytest.raises(AttributeError):
        setattr(ui, retired_name, object())
    assert not hasattr(ui, retired_name)


def test_declared_editor_projection_inventory_is_backed_by_facade_properties() -> None:
    facade_properties = {
        name
        for name, value in ToolLoopTerminalUi.__dict__.items()
        if isinstance(value, property)
    }
    editor_projection_inventory = {
        *(row[0] for row in _EDITOR_WRITABLE_PROJECTIONS),
        *_EDITOR_READ_ONLY_PROJECTIONS,
    }

    # Overlay/chrome owners may add unrelated facade properties in later slices.
    assert editor_projection_inventory <= facade_properties

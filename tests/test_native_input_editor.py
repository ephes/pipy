"""Focused contracts for the built-in input-editor effect owner."""

from __future__ import annotations

import inspect
import io
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from textwrap import dedent
from typing import Literal, cast

import pytest

from pipy_harness.native.editor_state import EditorState
from pipy_harness.native.frame_renderer import InputSnapshot, input_lines
from pipy_harness.native.tui import ToolLoopTerminalUi
from pipy_harness.native.ui.components.custom_editor import (
    CustomEditorEffects,
    CustomEditorOwner,
    CustomEditorState,
)
from pipy_harness.native.ui.components.input_editor import (
    EditingKeyContext,
    InputEditor,
    apply_editing_key,
)
from pipy_harness.native.ui.paint_lock import PaintLock

_COMMANDS = ("/hotkeys", "/model", "/settings")


def _custom_owner(state: EditorState, lock: PaintLock) -> CustomEditorOwner:
    def noop() -> None:
        return None

    return CustomEditorOwner(
        CustomEditorState(),
        state,
        lock,
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


def _owner(
    state: EditorState | None = None,
    *,
    paints: list[str] | None = None,
    refreshes: list[str] | None = None,
) -> InputEditor:
    paint_events = paints if paints is not None else []
    refresh_events = refreshes if refreshes is not None else []
    editor = state if state is not None else EditorState()
    lock = PaintLock()
    return InputEditor(
        editor,
        lock,
        lambda: paint_events.append("paint"),
        command_names=lambda: _COMMANDS,
        refresh_autocomplete=lambda: refresh_events.append("refresh"),
        custom_editor=_custom_owner(editor, lock),
        insert_paste=lambda _text: None,
    )


def _context(
    *,
    owner: InputEditor,
    history: bool,
    path: bool,
    tab_repaint: str = "path",
    listener_replaced: bool = False,
    popup: str | None = None,
    events: list[str] | None = None,
) -> EditingKeyContext:
    calls = events if events is not None else []

    def event(name: str, result: bool = False) -> Callable[..., bool | None]:
        def record(*_args: object) -> bool | None:
            calls.append(name)
            if name in {"accept-slash", "accept-auto", "nav-slash", "nav-auto"}:
                owner.request_repaint()
            return result if name == "path" else None

        return record

    return EditingKeyContext(
        slash_menu_open=lambda: popup == "slash",
        slash_menu_has_matches=lambda: True,
        autocomplete_open=lambda: popup == "autocomplete",
        navigate_slash_menu=cast(Callable[[str], None], event("nav-slash")),
        navigate_autocomplete=cast(Callable[[str], None], event("nav-auto")),
        accept_slash_menu=cast(Callable[[], None], event("accept-slash")),
        accept_autocomplete=cast(Callable[[], None], event("accept-auto")),
        attempt_path_completion=cast(Callable[[], bool], event("path", True)),
        allow_history=history,
        allow_path_completion=path,
        allow_listener_replacement=history,
        listener_replaced=lambda: listener_replaced,
        tab_repaint=cast(Literal["path", "always"], tab_repaint),
    )


@pytest.mark.parametrize("drive_mode", ("read-line", "active-turn"))
@pytest.mark.parametrize(
    ("key", "state", "expected"),
    (
        ("backspace", EditorState(text="ab", cursor=2), ("a", 1)),
        ("left", EditorState(text="ab", cursor=2), ("ab", 1)),
        ("home", EditorState(text="ab", cursor=2), ("ab", 0)),
        ("ctrl-u", EditorState(text="ab", cursor=2), ("", 0)),
        (
            "ctrl-z",
            EditorState(text="a", cursor=1, undo_stack=[("", 0)]),
            ("", 0),
        ),
        (
            "ctrl-y",
            EditorState(text="", cursor=0, redo_stack=[("a", 1)]),
            ("a", 1),
        ),
        ("x", EditorState(text="a", cursor=1), ("ax", 2)),
    ),
)
def test_editing_key_effect_matrix_is_shared_by_both_drive_modes(
    drive_mode: str,
    key: str,
    state: EditorState,
    expected: tuple[str, int],
) -> None:
    state = deepcopy(state)
    paints: list[str] = []
    owner = _owner(state, paints=paints)
    context = _context(
        owner=owner,
        history=drive_mode == "read-line",
        path=drive_mode == "read-line",
        tab_repaint="path" if drive_mode == "read-line" else "always",
    )

    assert apply_editing_key(owner, key, context)
    assert (state.text, state.effective_cursor()) == expected
    assert paints == ["paint"]


def test_drive_mode_differences_preserve_history_listener_and_tab_repaints() -> None:
    state = EditorState(text="draft", input_history=["saved"])
    paints: list[str] = []
    owner = _owner(state, paints=paints)

    read_context = _context(owner=owner, history=True, path=True)
    assert apply_editing_key(owner, "up", read_context)
    assert state.text == "saved"
    assert paints == ["paint"]

    state.set_buffer("draft")
    paints.clear()
    active_context = _context(
        owner=owner, history=False, path=False, tab_repaint="always"
    )
    assert apply_editing_key(owner, "up", active_context)
    assert state.text == "draft"
    assert paints == []

    read_replacement = _context(
        owner=owner, history=True, path=True, listener_replaced=True
    )
    assert apply_editing_key(owner, "replacement", read_replacement)
    assert state.text.endswith("replacement")
    assert not apply_editing_key(owner, "replacement", active_context)

    paints.clear()
    active_popup = _context(
        owner=owner,
        history=False,
        path=False,
        tab_repaint="always",
        popup="autocomplete",
    )
    assert apply_editing_key(owner, "tab", active_popup)
    assert paints == ["paint", "paint"]

    paints.clear()
    read_popup = _context(owner=owner, history=True, path=True, popup="autocomplete")
    assert apply_editing_key(owner, "tab", read_popup)
    assert paints == ["paint"]


class _TrackingLock:
    depth = 0

    def __enter__(self) -> bool:
        self.depth += 1
        return True

    def __exit__(self, *_args: object) -> None:
        self.depth -= 1


def test_record_transition_is_atomic_and_callbacks_run_outside_lock() -> None:
    state = EditorState(text="a", cursor=1)
    lock = _TrackingLock()
    callbacks: list[tuple[str, str]] = []

    def outside(name: str) -> None:
        assert lock.depth == 0
        callbacks.append((name, state.text))

    def command_names() -> tuple[str, ...]:
        outside("names")
        return _COMMANDS

    owner = InputEditor(
        state,
        cast(PaintLock, lock),
        lambda: outside("paint"),
        command_names=command_names,
        refresh_autocomplete=lambda: outside("refresh"),
        custom_editor=_custom_owner(state, cast(PaintLock, lock)),
        insert_paste=lambda _text: outside("paste"),
    )

    owner.insert_text("b")

    assert lock.depth == 0
    assert state.text == "ab"
    assert callbacks == [
        ("names", "a"),
        ("refresh", "ab"),
        ("paint", "ab"),
    ]


def test_input_frame_matches_pure_projection_for_control_text_wrap_and_cursor() -> None:
    state = EditorState(text="a\nb\tcdef", cursor=5)
    owner = _owner(state)

    actual = owner.input_frame_lines(5, max_rows=2)
    expected = list(
        input_lines(
            InputSnapshot(text=state.text, cursor=state.effective_cursor()),
            5,
            max_rows=2,
        )
    )

    assert actual == expected
    assert owner.display_input_text("a\nb\tc") == "a⏎b c"
    assert any(row.meta is not None for row in actual)


def test_history_undo_redo_and_draft_recall_live_on_owner() -> None:
    state = EditorState()
    owner = _owner(state)
    owner.record_history("first")
    owner.record_history("second")
    owner.set_buffer("draft")

    owner.navigate_history("up")
    assert owner.text == "second"
    owner.navigate_history("down")
    assert owner.text == "draft"

    owner.reset_line_editor_state()
    owner.insert_text("!")
    owner.undo()
    assert owner.text == "draft"
    owner.redo()
    assert owner.text == "draft!"


def test_shell_shares_one_record_and_lock_and_has_no_dead_editor_facade(
    tmp_path: Path,
) -> None:
    ui = ToolLoopTerminalUi(
        input_stream=io.StringIO(), terminal_stream=io.StringIO(), cwd=tmp_path
    )

    state = ui.input_editor.editor_state
    assert ui.pending_messages._editor is state  # noqa: SLF001
    assert ui.clipboard_images._editor is state  # noqa: SLF001
    assert ui.input_editor._paint_lock is ui._paint_lock  # noqa: SLF001
    assert ui.pending_messages._paint_lock is ui._paint_lock  # noqa: SLF001
    assert ui.clipboard_images._paint_lock is ui._paint_lock  # noqa: SLF001

    dead_surface = {
        "_editor",
        "input_text",
        "input_cursor",
        "input_history",
        "_history_nav_index",
        "_history_draft",
        "_undo_stack",
        "_redo_stack",
        "_pending_paste",
        "_pending_initial_text",
        "get_input_text",
        "set_input_text",
        "paste_input_text",
        "_insert_input_text",
        "_delete_before_cursor",
        "_kill_to_line_start",
        "_undo_edit",
        "_redo_edit",
        "_navigate_history",
        "_load_history_entry",
        "_display_input_text",
        "_input_frame_lines",
    }
    assert dead_surface.isdisjoint(ToolLoopTerminalUi.__dict__)


def test_both_raw_mode_loops_call_the_shared_editing_key_function() -> None:
    for method in (
        ToolLoopTerminalUi.read_line,
        ToolLoopTerminalUi.wait_for_active_turn_interrupt,
    ):
        source = dedent(inspect.getsource(method))
        assert source.count("apply_editing_key(") == 1
        assert 'if key == "backspace"' not in source

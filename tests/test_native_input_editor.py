"""Focused contracts for the built-in input-editor effect owner."""

from __future__ import annotations

import inspect
import io
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from textwrap import dedent
from typing import Literal, cast

import pytest

from pipy_harness.native.editor_state import EditorState
from pipy_harness.native.frame_renderer import InputSnapshot, input_lines
from pipy_harness.native.tui import TerminalUi
from pipy_harness.native.ui.components.custom_editor import (
    HOTKEY_EXTENSION_SHORTCUT_PREFIX,
    HOTKEY_THINKING_CYCLE,
    CustomEditorEffects,
    CustomEditorOwner,
    CustomEditorState,
)
from pipy_harness.native.ui.components.input_editor import (
    EditingAction,
    EditingKeyContext,
    EditingMode,
    InputEditor,
    LineEditingEffects,
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
    lock = PaintLock(threading.RLock())
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
    mode: EditingMode | None = None,
) -> EditingKeyContext:
    calls = events if events is not None else []

    def event(name: str, result: bool = False) -> Callable[..., bool | None]:
        def record(*_args: object) -> bool | None:
            calls.append(name)
            if name in {"accept-slash", "accept-auto", "nav-slash", "nav-auto"}:
                owner.request_repaint()
            return result if name == "path" else None

        return record

    resolved_mode = mode or (EditingMode.LINE if history else EditingMode.ACTIVE_QUEUE)
    line_effects = None
    if resolved_mode is EditingMode.LINE:
        line_effects = LineEditingEffects(
            matches_external_editor=lambda _key: False,
            run_external_editor=lambda _text: None,
            paste_clipboard_image=cast(Callable[[], None], event("clipboard")),
            shortcut_keys=lambda: (),
            custom_editor_active=lambda: False,
            handle_custom_editor=lambda _key: None,
            consume_custom_exit=lambda: False,
        )
    return EditingKeyContext(
        mode=resolved_mode,
        slash_menu_open=lambda: popup == "slash",
        slash_menu_has_matches=lambda: True,
        slash_menu_exact_match=lambda _text: False,
        autocomplete_open=lambda: popup == "autocomplete",
        navigate_slash_menu=cast(Callable[[str], None], event("nav-slash")),
        navigate_autocomplete=cast(Callable[[str], None], event("nav-auto")),
        accept_slash_menu=cast(Callable[[], None], event("accept-slash")),
        accept_autocomplete=cast(Callable[[], None], event("accept-auto")),
        dismiss_slash_menu=cast(Callable[[], None], event("dismiss-slash")),
        close_autocomplete=cast(Callable[[], None], event("close-auto")),
        attempt_path_completion=cast(Callable[[], bool], event("path", True)),
        consume_paste=lambda: "pasted",
        insert_paste=lambda _text: None,
        repaint=owner.request_repaint,
        is_local_command=lambda text: text.lstrip().startswith(("/", "!")),
        allow_history=history,
        allow_path_completion=path,
        line_effects=line_effects,
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

    assert apply_editing_key(owner, key, context).action is EditingAction.CONTINUE
    assert (state.text, state.effective_cursor()) == expected
    assert paints == ["paint"]


def test_drive_mode_differences_preserve_history_listener_and_tab_repaints() -> None:
    state = EditorState(text="draft", input_history=["saved"])
    paints: list[str] = []
    owner = _owner(state, paints=paints)

    read_context = _context(owner=owner, history=True, path=True)
    assert apply_editing_key(owner, "up", read_context).action is EditingAction.CONTINUE
    assert state.text == "saved"
    assert paints == ["paint"]

    state.set_buffer("draft")
    paints.clear()
    active_context = _context(
        owner=owner, history=False, path=False, tab_repaint="always"
    )
    assert (
        apply_editing_key(owner, "up", active_context).action is EditingAction.CONTINUE
    )
    assert state.text == "draft"
    assert paints == []

    read_replacement = _context(
        owner=owner, history=True, path=True, listener_replaced=True
    )
    assert (
        apply_editing_key(owner, "replacement", read_replacement).action
        is EditingAction.CONTINUE
    )
    assert state.text.endswith("replacement")
    before = state.text
    assert (
        apply_editing_key(owner, "replacement", active_context).action
        is EditingAction.CONTINUE
    )
    assert state.text == before

    paints.clear()
    active_popup = _context(
        owner=owner,
        history=False,
        path=False,
        tab_repaint="always",
        popup="autocomplete",
    )
    assert (
        apply_editing_key(owner, "tab", active_popup).action is EditingAction.CONTINUE
    )
    assert paints == ["paint", "paint"]

    paints.clear()
    read_popup = _context(owner=owner, history=True, path=True, popup="autocomplete")
    assert apply_editing_key(owner, "tab", read_popup).action is EditingAction.CONTINUE
    assert paints == ["paint"]


@pytest.mark.parametrize(
    ("key", "text", "expected_action", "expected_text"),
    (
        ("ctrl-c", "", EditingAction.INTERRUPT, None),
        ("ctrl-d", "", EditingAction.EOF, None),
        ("enter", "submit me", EditingAction.SUBMIT, "submit me"),
        ("shift-tab", "draft", EditingAction.APP_COMMAND, HOTKEY_THINKING_CYCLE),
    ),
)
def test_line_mode_outcomes_are_explicit(
    key: str,
    text: str,
    expected_action: EditingAction,
    expected_text: str | None,
) -> None:
    owner = _owner(EditorState(text=text))
    context = _context(owner=owner, history=True, path=True)

    outcome = apply_editing_key(owner, key, context)

    assert (outcome.action, outcome.text) == (expected_action, expected_text)


@pytest.mark.parametrize(
    ("mode", "key", "text", "expected_action", "expected_text"),
    (
        (EditingMode.ACTIVE_WATCH, "esc", "", EditingAction.ABORT, None),
        (
            EditingMode.ACTIVE_WATCH,
            "ctrl-c",
            "",
            EditingAction.INTERRUPT,
            None,
        ),
        (
            EditingMode.ACTIVE_QUEUE,
            "enter",
            "/settings",
            EditingAction.LOCAL_COMMAND,
            "/settings",
        ),
        (
            EditingMode.ACTIVE_QUEUE,
            "enter",
            "steer now",
            EditingAction.STEER,
            "steer now",
        ),
        (
            EditingMode.ACTIVE_QUEUE,
            "alt-enter",
            "later",
            EditingAction.FOLLOW_UP,
            "later",
        ),
        (
            EditingMode.ACTIVE_QUEUE,
            "alt-up",
            "",
            EditingAction.RESTORE_PENDING,
            None,
        ),
        (
            EditingMode.ACTIVE_COMMAND,
            "x",
            "",
            EditingAction.CONTINUE,
            None,
        ),
    ),
)
def test_active_mode_outcomes_are_explicit(
    mode: EditingMode,
    key: str,
    text: str,
    expected_action: EditingAction,
    expected_text: str | None,
) -> None:
    owner = _owner(EditorState(text=text))
    context = _context(owner=owner, history=False, path=False, mode=mode)

    outcome = apply_editing_key(owner, key, context)

    assert (outcome.action, outcome.text) == (expected_action, expected_text)


def test_line_mode_effect_ports_preserve_external_clipboard_shortcut_and_custom() -> (
    None
):
    events: list[object] = []
    owner = _owner(EditorState(text="draft"))
    context = _context(owner=owner, history=True, path=True)
    assert context.line_effects is not None
    effects = replace(
        context.line_effects,
        matches_external_editor=lambda key: key == "ctrl-g",
        run_external_editor=lambda text: f"{text}-edited",
        paste_clipboard_image=lambda: events.append("clipboard"),
        shortcut_keys=lambda: ("ctrl-k",),
    )
    context = replace(
        context,
        line_effects=effects,
        consume_paste=lambda: " pasted",
        insert_paste=lambda text: events.append(("paste", text)),
    )

    assert apply_editing_key(owner, "ctrl-g", context).action is EditingAction.CONTINUE
    assert owner.text == "draft-edited"
    assert apply_editing_key(owner, "paste", context).action is EditingAction.CONTINUE
    assert apply_editing_key(owner, "ctrl-v", context).action is EditingAction.CONTINUE
    shortcut = apply_editing_key(owner, "ctrl-k", context)

    assert (shortcut.action, shortcut.text) == (
        EditingAction.APP_COMMAND,
        f"{HOTKEY_EXTENSION_SHORTCUT_PREFIX}ctrl-k",
    )
    assert events == [("paste", " pasted"), "clipboard"]

    submitted = ["custom text"]
    custom_effects = replace(
        effects,
        custom_editor_active=lambda: True,
        handle_custom_editor=lambda _key: submitted.pop() if submitted else None,
        consume_custom_exit=lambda: False,
    )
    custom = apply_editing_key(
        owner, "enter", replace(context, line_effects=custom_effects)
    )
    assert (custom.action, custom.text) == (
        EditingAction.SUBMIT,
        "custom text",
    )

    exit_effects = replace(
        custom_effects,
        handle_custom_editor=lambda _key: "ignored",
        consume_custom_exit=lambda: True,
    )
    exited = apply_editing_key(
        owner, "ctrl-d", replace(context, line_effects=exit_effects)
    )
    assert exited.action is EditingAction.EOF


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
    ui = TerminalUi(
        input_stream=io.StringIO(), terminal_stream=io.StringIO(), cwd=tmp_path
    )

    state = ui.components.input_editor.editor_state
    assert ui.components.pending_messages._editor is state  # noqa: SLF001
    assert ui.components.clipboard_images._editor is state  # noqa: SLF001
    assert ui.components.input_editor._paint_lock is ui.components.screen.paint_lock  # noqa: SLF001
    assert ui.components.pending_messages._paint_lock is ui.components.screen.paint_lock  # noqa: SLF001
    assert ui.components.clipboard_images._paint_lock is ui.components.screen.paint_lock  # noqa: SLF001

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
    assert dead_surface.isdisjoint(TerminalUi.__dict__)


def test_both_raw_mode_loops_call_the_shared_editing_key_function() -> None:
    for method in (
        TerminalUi.read_line,
        TerminalUi.wait_for_active_turn_interrupt,
    ):
        source = dedent(inspect.getsource(method))
        assert source.count("apply_editing_key(") == 1
        assert 'if key == "backspace"' not in source

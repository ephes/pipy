"""Terminal-independent contracts for the product editor-state owner."""

from __future__ import annotations

from pipy_harness.native.editor_state import (
    CompletionItem,
    EditorState,
    QueuedInput,
)


_COMMANDS = ("/hotkeys", "/model", "/settings")


def test_buffer_cursor_menu_and_undo_redo_are_pure_transitions() -> None:
    state = EditorState()

    state.insert("/m", _COMMANDS)
    assert state.text == "/m"
    assert state.cursor == 2
    assert state.slash_menu_open
    assert state.filtered_commands(_COMMANDS) == ("/model",)

    state.move_cursor("left")
    state.insert("X", _COMMANDS)
    assert (state.text, state.cursor) == ("/Xm", 2)
    assert not state.slash_menu_open

    assert state.undo(_COMMANDS)
    assert (state.text, state.cursor) == ("/m", 1)
    assert state.redo(_COMMANDS)
    assert (state.text, state.cursor) == ("/Xm", 2)


def test_history_navigation_restores_the_exact_draft() -> None:
    state = EditorState()
    state.record_history("first\nline")
    state.record_history("second")
    state.set_buffer("draft", cursor=2)

    assert state.navigate_history("up")
    assert (state.text, state.cursor) == ("second", len("second"))
    assert state.navigate_history("up")
    assert state.text == "first\nline"
    assert state.navigate_history("down")
    assert state.text == "second"
    assert state.navigate_history("down")
    assert (state.text, state.cursor) == ("draft", len("draft"))
    assert state.history_nav_index is None


def test_line_rehydration_submission_and_paste_have_one_owner() -> None:
    state = EditorState()
    state.stage_initial_text("rehydrated")
    state.stage_paste("a\nb")

    assert state.begin_line() == "rehydrated"
    assert state.consume_paste() == "a\nb"
    assert state.consume_paste() == ""
    state.insert("!", _COMMANDS)
    submitted = state.submit_line()

    assert submitted == "rehydrated!"
    assert state.input_history == ["rehydrated!"]
    assert (state.text, state.cursor) == ("", 0)
    assert state.pending_initial_text is None
    assert not state.undo_stack


def test_completion_popup_enforces_priority_anchor_and_selection() -> None:
    state = EditorState()
    state.set_buffer("@co")
    items = (
        CompletionItem("@config.py", "config.py"),
        CompletionItem("@src/config.py", "config.py"),
    )
    provider = object()
    state.open_autocomplete(
        items=items,
        mode="at",
        token_start=0,
        prefix="co",
        active_provider=provider,
    )

    selection = state.completion_selection()
    assert selection is not None and selection.span_is_valid()
    assert state.navigate_autocomplete("up")
    assert state.autocomplete_selection == 1
    assert state.autocomplete_active_provider is provider
    state.apply_completion_result("@src/config.py", 14)
    assert (state.text, state.cursor) == ("@src/config.py", 14)
    assert not state.autocomplete_open

    state.set_buffer("/")
    state.refresh_slash_menu(_COMMANDS)
    state.open_autocomplete(
        items=items,
        mode="at",
        token_start=0,
        prefix="",
    )
    assert state.slash_menu_open
    assert not state.autocomplete_open


def test_queue_preserves_lane_order_kind_and_abort_restoration() -> None:
    state = EditorState()
    state.enqueue_follow_up("F1")
    state.enqueue_steering("S1")
    state.enqueue_follow_up("F2")
    state.promote_pending_to_drain()
    custom_text_calls = 0

    def custom_text() -> str:
        nonlocal custom_text_calls
        custom_text_calls += 1
        return "draft"

    assert state.take_next_drain() == "S1"
    assert state.take_last_drain_kind() == "steering"
    assert state.restore_pending_to_editor(custom_text_supplier=custom_text)
    assert custom_text_calls == 1
    assert state.text == "F1\n\nF2\n\ndraft"
    assert state.pending_initial_text == state.text
    assert state.take_next_drain() is None

    # With no queue, even an injected extension adapter stays lazy.
    assert not state.restore_pending_to_editor(custom_text_supplier=custom_text)
    assert custom_text_calls == 1


def test_queue_entries_make_content_kind_pairing_structural() -> None:
    state = EditorState(
        pending_drain=[
            QueuedInput("follow_up", "F1"),
            QueuedInput("steering", "S2"),
        ]
    )

    assert state.take_next_drain() == "F1"
    assert state.take_last_drain_kind() == "follow_up"
    assert state.take_next_drain() == "S2"
    assert state.take_last_drain_kind() == "steering"


def test_pending_entries_project_typed_kinds_without_rendering_labels() -> None:
    state = EditorState()
    state.enqueue_follow_up("F1")
    state.enqueue_steering("S1")

    assert state.pending_messages() == (
        QueuedInput("steering", "S1"),
        QueuedInput("follow_up", "F1"),
    )
    assert all(
        entry.kind not in {"Steering", "Follow-up"}
        for entry in state.pending_messages()
    )


def test_navigation_reports_only_real_selection_changes() -> None:
    state = EditorState(text="/", slash_menu_open=True)

    assert not state.navigate_slash_menu("down", ("/only",))
    assert state.navigate_slash_menu("down", ("/first", "/second"))
    assert state.slash_menu_selection == 1
    state.close_slash_menu()

    state.open_autocomplete(
        items=(CompletionItem("one", "One"),),
        mode="at",
        token_start=0,
        prefix="",
    )
    assert not state.navigate_autocomplete("down")
    state.open_autocomplete(
        items=(CompletionItem("one", "One"), CompletionItem("two", "Two")),
        mode="at",
        token_start=0,
        prefix="",
    )
    assert state.navigate_autocomplete("down")
    assert state.autocomplete_selection == 1


def test_empty_insert_preserves_historical_edit_boundary_and_refresh() -> None:
    state = EditorState(
        text="draft",
        cursor=2,
        history_nav_index=3,
        history_draft="old draft",
        redo_stack=[("redo", 4)],
        autocomplete_open=True,
        autocomplete_items=(CompletionItem("value", "label"),),
    )

    state.insert("", _COMMANDS)
    assert (state.text, state.cursor) == ("draft", 2)
    assert state.undo_stack == [("draft", 2)]
    assert state.redo_stack == []
    assert state.history_nav_index is None
    assert state.history_draft == ""
    # Provider lookup/closure is an effectful facade refresh; the owner has
    # still recorded the empty edit and refreshed slash-menu priority.
    assert state.autocomplete_open

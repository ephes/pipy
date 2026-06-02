"""Pure helpers backing the native session-tree product commands.

These cover the Pi selection semantics for ``/tree``, entry-reference
resolution, tree rendering, and ``/session`` status formatting independent of
the REPL loop or any TTY.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree
from pipy_harness.native.session_tree_commands import (
    apply_tree_selection,
    format_session_status,
    render_tree_lines,
    resolve_entry_ref,
    resolve_startup_session,
    visible_tree_entries,
)
from pipy_harness.native.tools.messages import AssistantMessage, UserMessage


def _user_entry(tree: NativeSessionTree, content: str) -> MessageEntry:
    for entry in tree.get_entries():
        if (
            isinstance(entry, MessageEntry)
            and isinstance(entry.message, UserMessage)
            and entry.message.content == content
        ):
            return entry
    raise AssertionError(f"no user message {content!r}")


def _seed(tmp_path: Path) -> NativeSessionTree:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "s")
    tree.append_message(UserMessage(content="ROOT"))
    tree.append_message(AssistantMessage(content="SEEN:ROOT"))
    tree.append_message(UserMessage(content="MAIN"))
    tree.append_message(AssistantMessage(content="SEEN:ROOT,MAIN"))
    return tree


def test_select_user_message_rehydrates_editor_and_branches_to_parent(
    tmp_path: Path,
) -> None:
    tree = _seed(tmp_path)
    main_user = _user_entry(tree, "MAIN")
    parent_id = main_user.parent_id

    result = apply_tree_selection(tree, main_user.id)

    assert result.editor_text == "MAIN"
    assert result.is_user_selection is True
    assert tree.get_leaf_id() == parent_id

    # Submitting the (edited) text creates a sibling branch.
    tree.append_message(UserMessage(content="ALT"))
    child_contents = [
        e.message.content
        for e in tree.get_children(parent_id)
        if isinstance(e, MessageEntry) and isinstance(e.message, UserMessage)
    ]
    assert "ALT" in child_contents


def test_select_root_user_message_sets_leaf_to_none(tmp_path: Path) -> None:
    tree = _seed(tmp_path)
    root_user = _user_entry(tree, "ROOT")
    result = apply_tree_selection(tree, root_user.id)
    assert result.editor_text == "ROOT"
    assert tree.get_leaf_id() is None


def test_select_non_user_entry_sets_leaf_and_empty_editor(tmp_path: Path) -> None:
    tree = _seed(tmp_path)
    assistant = next(
        e
        for e in tree.get_entries()
        if isinstance(e, MessageEntry)
        and isinstance(e.message, AssistantMessage)
        and e.message.content == "SEEN:ROOT,MAIN"
    )
    result = apply_tree_selection(tree, assistant.id)
    assert result.editor_text is None
    assert result.is_user_selection is False
    assert tree.get_leaf_id() == assistant.id


def test_resolve_entry_ref_by_prefix_and_index(tmp_path: Path) -> None:
    tree = _seed(tmp_path)
    entries = visible_tree_entries(tree)
    first = entries[0]
    # by 1-based index
    assert resolve_entry_ref(tree, "1") is first
    # by id prefix
    assert resolve_entry_ref(tree, first.id[:6]) is first
    # unknown
    assert resolve_entry_ref(tree, "zzzzzzzz") is None


def test_render_tree_lines_marks_active_path(tmp_path: Path) -> None:
    tree = _seed(tmp_path)
    lines = render_tree_lines(tree)
    text = "\n".join(lines)
    assert "ROOT" in text
    assert "MAIN" in text
    # The active leaf path is marked.
    assert any("*" in line for line in lines)


def test_format_session_status_reports_safe_fields(tmp_path: Path) -> None:
    tree = _seed(tmp_path)
    tree.append_session_info("conformance-tree")
    status = format_session_status(tree)
    assert "conformance-tree" in status
    assert tree.session_id[:8] in status


def test_resolve_startup_session_modes(tmp_path: Path) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    state_root = tmp_path / "state"

    # no-session / disabled -> ephemeral (None)
    assert (
        resolve_startup_session(
            cwd, mode="none", state_root=state_root
        )
        is None
    )

    # new -> fresh persistent session
    fresh = resolve_startup_session(cwd, mode="new", state_root=state_root)
    assert fresh is not None and fresh.path is not None
    fresh.append_message(UserMessage(content="HELLO"))
    first_id = fresh.session_id

    # continue -> reopens the most recent session
    cont = resolve_startup_session(cwd, mode="continue", state_root=state_root)
    assert cont is not None
    assert cont.session_id == first_id

    # session -> open by id prefix
    opened = resolve_startup_session(
        cwd, mode="session", target=first_id[:6], state_root=state_root
    )
    assert opened is not None
    assert opened.session_id == first_id

    # fork -> new file referencing the parent
    forked = resolve_startup_session(
        cwd, mode="fork", target=first_id[:6], state_root=state_root
    )
    assert forked is not None
    assert forked.session_id != first_id
    assert forked.path is not None
    assert "parentSession" in forked.path.read_text(encoding="utf-8")


def test_user_only_filter_hides_assistant_entries(tmp_path: Path) -> None:
    tree = _seed(tmp_path)
    visible = visible_tree_entries(tree, filter_mode="user-only")
    contents = [
        e.message.content
        for e in visible
        if isinstance(e, MessageEntry) and isinstance(e.message, UserMessage)
    ]
    assert "ROOT" in contents
    assert "MAIN" in contents
    assert not any(
        isinstance(e, MessageEntry) and isinstance(e.message, AssistantMessage)
        for e in visible
    )

"""Pure helpers backing the native session-tree product commands.

These cover the Pi selection semantics for ``/tree``, entry-reference
resolution, tree rendering, and ``/session`` status formatting independent of
the REPL loop or any TTY.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree
from pipy_harness.native.session_tree_commands import (
    TreeCommandOutcome,
    apply_tree_selection,
    format_session_status,
    handle_tree_command,
    render_tree_lines,
    resolve_entry_ref,
    resolve_startup_session,
    visible_tree_entries,
)


def _user_entry(tree: NativeSessionTree, content: str) -> MessageEntry:
    for entry in tree.get_entries():
        if (
            isinstance(entry, MessageEntry)
            and isinstance(entry.message, AgentUserMessage)
            and entry.message.content.value == content
        ):
            return entry
    raise AssertionError(f"no user message {content!r}")


def _seed(tmp_path: Path) -> NativeSessionTree:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "s")
    tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    tree.append_message(AgentAssistantMessage(content=ProductContent("SEEN:ROOT")))
    tree.append_message(AgentUserMessage(content=ProductContent("MAIN")))
    tree.append_message(AgentAssistantMessage(content=ProductContent("SEEN:ROOT,MAIN")))
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
    tree.append_message(AgentUserMessage(content=ProductContent("ALT")))
    child_contents = [
        e.message.content.value
        for e in tree.get_children(parent_id)
        if isinstance(e, MessageEntry) and isinstance(e.message, AgentUserMessage)
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
        and isinstance(e.message, AgentAssistantMessage)
        and e.message.content.value == "SEEN:ROOT,MAIN"
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
    assert resolve_startup_session(cwd, mode="none", state_root=state_root) is None

    # new -> fresh persistent session
    fresh = resolve_startup_session(cwd, mode="new", state_root=state_root)
    assert fresh is not None and fresh.path is not None
    fresh.append_message(AgentUserMessage(content=ProductContent("HELLO")))
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


def test_tree_command_adapter_renders_lines_then_help(tmp_path: Path) -> None:
    tree = _seed(tmp_path)
    diagnostics: list[str] = []

    outcome = handle_tree_command(
        "",
        session_tree=tree,
        filter_mode="default",
        rebuild_messages=lambda: None,
        diagnostic=diagnostics.append,
    )

    assert outcome == TreeCommandOutcome()
    assert diagnostics == [
        *render_tree_lines(tree),
        "pipy: use '/tree select <n|id>' to move, "
        "'/tree label <n|id> [text]' to (un)label, "
        "'/tree filter <mode>' to filter.",
    ]


def test_tree_command_adapter_prefers_interactive_callback_for_bare_command(
    tmp_path: Path,
) -> None:
    tree = _seed(tmp_path)
    diagnostics: list[str] = []
    expected = TreeCommandOutcome(prefill="RESTORED", filter_mode="all")

    outcome = handle_tree_command(
        "",
        session_tree=tree,
        filter_mode="default",
        rebuild_messages=lambda: None,
        diagnostic=diagnostics.append,
        interactive_selector=lambda: expected,
    )

    assert outcome is expected
    assert diagnostics == []


def test_tree_command_adapter_label_filter_and_unknown_dispatch(
    tmp_path: Path,
) -> None:
    tree = _seed(tmp_path)
    first = visible_tree_entries(tree)[0]
    diagnostics: list[str] = []

    label_outcome = handle_tree_command(
        "LABEL 1 marked",
        session_tree=tree,
        filter_mode="default",
        rebuild_messages=lambda: None,
        diagnostic=diagnostics.append,
    )
    filter_outcome = handle_tree_command(
        "filter ALL",
        session_tree=tree,
        filter_mode="default",
        rebuild_messages=lambda: None,
        diagnostic=diagnostics.append,
    )
    unknown_outcome = handle_tree_command(
        "mystery alpha",
        session_tree=tree,
        filter_mode="default",
        rebuild_messages=lambda: None,
        diagnostic=diagnostics.append,
    )

    assert label_outcome == TreeCommandOutcome()
    assert tree.get_label(first.id) == "marked"
    assert filter_outcome == TreeCommandOutcome(filter_mode="all")
    assert unknown_outcome == TreeCommandOutcome()
    assert diagnostics == [
        f"pipy: labeled {first.id[:8]} 'marked'.",
        "pipy: /tree filter set to all.",
        "pipy: unknown /tree subcommand 'mystery'; use select, label, or filter.",
    ]


def test_tree_command_adapter_summary_focus_and_prefill(tmp_path: Path) -> None:
    tree = _seed(tmp_path)
    root = _user_entry(tree, "ROOT")
    summary_calls: list[tuple[list[str], str | None]] = []
    rebuilds: list[None] = []
    diagnostics: list[str] = []

    def summarize(messages: list[AgentMessage], focus: str | None) -> str:
        summary_calls.append(([message.content.value for message in messages], focus))
        return "abandoned summary"

    outcome = handle_tree_command(
        f"select {root.id} summarize:first summarize:last",
        session_tree=tree,
        filter_mode="default",
        rebuild_messages=lambda: rebuilds.append(None),
        diagnostic=diagnostics.append,
        summarizer=summarize,
    )

    assert outcome == TreeCommandOutcome(prefill="ROOT")
    assert summary_calls and summary_calls[0][1] == "last"
    assert rebuilds == [None]
    assert diagnostics == ["pipy: recorded branch summary and switched branches."]


def test_tree_command_adapter_summary_cancellation_leaves_tree_unchanged(
    tmp_path: Path,
) -> None:
    tree = _seed(tmp_path)
    root = _user_entry(tree, "ROOT")
    old_leaf = tree.get_leaf_id()
    old_count = len(tree.get_entries())
    diagnostics: list[str] = []

    def unexpected_rebuild() -> None:
        raise AssertionError("must not rebuild")

    outcome = handle_tree_command(
        f"select {root.id} summarize",
        session_tree=tree,
        filter_mode="default",
        rebuild_messages=unexpected_rebuild,
        diagnostic=diagnostics.append,
        summarizer=lambda _messages, _focus: None,
    )

    assert outcome == TreeCommandOutcome()
    assert tree.get_leaf_id() == old_leaf
    assert len(tree.get_entries()) == old_count
    assert diagnostics == ["pipy: branch summary cancelled; tree and leaf unchanged."]


def test_user_only_filter_hides_assistant_entries(tmp_path: Path) -> None:
    tree = _seed(tmp_path)
    visible = visible_tree_entries(tree, filter_mode="user-only")
    contents = [
        e.message.content.value
        for e in visible
        if isinstance(e, MessageEntry) and isinstance(e.message, AgentUserMessage)
    ]
    assert "ROOT" in contents
    assert "MAIN" in contents
    assert not any(
        isinstance(e, MessageEntry) and isinstance(e.message, AgentAssistantMessage)
        for e in visible
    )

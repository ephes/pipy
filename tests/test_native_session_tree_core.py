"""Core tests for the Pi-style native product session tree.

These pin the durable conversation-tree semantics defined in
``docs/session-tree.md``: append-only JSONL with a header, parent/leaf
pointer bookkeeping, branch traversal, active-branch context reconstruction,
labels, compaction/branch-summary replay, and reload from file. The store is
the product session source for pipy-native; ``pipy-session`` is not.
"""

from __future__ import annotations

import json
from pathlib import Path

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.session_tree import (
    MessageEntry,
    NativeSessionTree,
    default_native_session_dir,
    encode_cwd_dir_name,
)


def _new_tree(tmp_path: Path) -> NativeSessionTree:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    session_dir = tmp_path / "native-sessions"
    return NativeSessionTree.create(cwd, session_dir=session_dir)


# --------------------------------------------------------------------------
# Storage path encoding
# --------------------------------------------------------------------------


def test_encode_cwd_dir_name_matches_pi_shape() -> None:
    assert (
        encode_cwd_dir_name(Path("/Users/jochen/projects/pipy"))
        == "--Users-jochen-projects-pipy--"
    )


def test_default_native_session_dir_under_local_state(tmp_path: Path) -> None:
    root = tmp_path / "state"
    directory = default_native_session_dir(Path("/home/u/proj"), state_root=root)
    assert directory == root / "native-sessions" / "--home-u-proj--"


# --------------------------------------------------------------------------
# Append / leaf bookkeeping
# --------------------------------------------------------------------------


def test_create_writes_header_and_file(tmp_path: Path) -> None:
    tree = _new_tree(tmp_path)
    assert tree.path is not None
    assert tree.path.exists()
    header = tree.get_header()
    assert header is not None
    assert header.type == "session"
    assert header.cwd == str((tmp_path / "workspace").resolve())
    assert tree.get_leaf_id() is None


def test_append_message_advances_leaf_and_sets_parent(tmp_path: Path) -> None:
    tree = _new_tree(tmp_path)
    root = tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    assert root.parent_id is None
    assert tree.get_leaf_id() == root.id
    reply = tree.append_message(
        AgentAssistantMessage(content=ProductContent("SEEN:ROOT"))
    )
    assert reply.parent_id == root.id
    assert tree.get_leaf_id() == reply.id


def test_append_persists_each_entry_as_jsonl_line(tmp_path: Path) -> None:
    tree = _new_tree(tmp_path)
    tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    tree.append_message(AgentAssistantMessage(content=ProductContent("SEEN:ROOT")))
    assert tree.path is not None
    lines = [
        line
        for line in tree.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # header + 2 entries
    assert len(lines) == 3


# --------------------------------------------------------------------------
# Branch traversal + context reconstruction
# --------------------------------------------------------------------------


def test_branch_creates_sibling_without_rewriting_entries(tmp_path: Path) -> None:
    tree = _new_tree(tmp_path)
    root = tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    a_reply = tree.append_message(
        AgentAssistantMessage(content=ProductContent("SEEN:ROOT"))
    )
    main = tree.append_message(AgentUserMessage(content=ProductContent("MAIN")))
    tree.append_message(AgentAssistantMessage(content=ProductContent("SEEN:ROOT,MAIN")))

    # Re-edit MAIN: move leaf to MAIN's parent and submit an alternative.
    tree.branch(a_reply.id)
    alt = tree.append_message(AgentUserMessage(content=ProductContent("ALT")))
    tree.append_message(AgentAssistantMessage(content=ProductContent("SEEN:ROOT,ALT")))

    assert alt.parent_id == a_reply.id
    assert main.parent_id == a_reply.id
    # Both MAIN and ALT remain; nothing was rewritten.
    children_texts = {
        e.message.content.value
        for e in tree.get_children(a_reply.id)
        if isinstance(e, MessageEntry) and isinstance(e.message, AgentUserMessage)
    }
    assert children_texts == {"MAIN", "ALT"}
    assert root.parent_id is None


def test_build_context_follows_only_active_branch(tmp_path: Path) -> None:
    tree = _new_tree(tmp_path)
    tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    a_reply = tree.append_message(
        AgentAssistantMessage(content=ProductContent("SEEN:ROOT"))
    )
    tree.append_message(AgentUserMessage(content=ProductContent("MAIN")))
    tree.append_message(AgentAssistantMessage(content=ProductContent("SEEN:ROOT,MAIN")))

    tree.branch(a_reply.id)
    tree.append_message(AgentUserMessage(content=ProductContent("ALT")))
    tree.append_message(AgentAssistantMessage(content=ProductContent("SEEN:ROOT,ALT")))

    texts = [m.content.value for m in tree.build_context().messages]
    assert "ROOT" in texts
    assert "ALT" in texts
    assert "MAIN" not in texts

    # Navigate back to the MAIN branch leaf.
    main_leaf = next(
        e
        for e in tree.get_entries()
        if hasattr(e, "message")
        and isinstance(e.message, AgentAssistantMessage)
        and e.message.content.value == "SEEN:ROOT,MAIN"
    )
    tree.branch(main_leaf.id)
    texts_main = [m.content.value for m in tree.build_context().messages]
    assert "MAIN" in texts_main
    assert "ALT" not in texts_main


def test_get_branch_returns_root_to_leaf_order(tmp_path: Path) -> None:
    tree = _new_tree(tmp_path)
    root = tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    reply = tree.append_message(
        AgentAssistantMessage(content=ProductContent("SEEN:ROOT"))
    )
    branch = tree.get_branch()
    assert [e.id for e in branch] == [root.id, reply.id]


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------


def test_label_set_and_clear(tmp_path: Path) -> None:
    tree = _new_tree(tmp_path)
    root = tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    tree.append_label_change(root.id, "milestone")
    assert tree.get_label(root.id) == "milestone"
    tree.append_label_change(root.id, None)
    assert tree.get_label(root.id) is None


# --------------------------------------------------------------------------
# Reload / resume from file
# --------------------------------------------------------------------------


def test_open_rebuilds_tree_labels_leaf_and_name(tmp_path: Path) -> None:
    tree = _new_tree(tmp_path)
    root = tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    reply = tree.append_message(
        AgentAssistantMessage(content=ProductContent("SEEN:ROOT"))
    )
    tree.append_label_change(root.id, "start")
    info = tree.append_session_info("conformance-tree")
    assert tree.path is not None
    path = tree.path

    reopened = NativeSessionTree.open(path)
    # Pi semantics: the leaf defaults to the latest entry on load.
    assert reopened.get_leaf_id() == info.id
    # The reply remains on the active branch (info -> label -> reply -> root).
    assert reply.id in {e.id for e in reopened.get_branch()}
    assert reopened.get_label(root.id) == "start"
    assert reopened.name == "conformance-tree"
    texts = [m.content.value for m in reopened.build_context().messages]
    assert texts == ["ROOT", "SEEN:ROOT"]


def test_open_skips_malformed_lines(tmp_path: Path) -> None:
    tree = _new_tree(tmp_path)
    tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    assert tree.path is not None
    with tree.path.open("a", encoding="utf-8") as handle:
        handle.write("this is not json\n")
    reopened = NativeSessionTree.open(tree.path)
    texts = [m.content.value for m in reopened.build_context().messages]
    assert texts == ["ROOT"]


# --------------------------------------------------------------------------
# Tool result round-trip
# --------------------------------------------------------------------------


def test_tool_result_message_round_trips(tmp_path: Path) -> None:
    tree = _new_tree(tmp_path)
    tree.append_message(AgentUserMessage(content=ProductContent("run it")))
    tree.append_message(
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(
                AgentToolCall("provider-call-1", "test_tool", ProductContent("{}")),
            ),
        )
    )
    tree.append_message(
        AgentToolResultMessage(
            tool_request_id="pipy-tool-1",
            tool_name="test_tool",
            content=ProductContent("done"),
            provider_correlation_id="provider-call-1",
            added_tool_names=("late_one", "late_two"),
        )
    )
    assert tree.path is not None
    reopened = NativeSessionTree.open(tree.path)
    messages = reopened.build_context().messages
    tool_results = [m for m in messages if isinstance(m, AgentToolResultMessage)]
    assert len(tool_results) == 1
    assert tool_results[0].content.value == "done"
    assert tool_results[0].added_tool_names == ("late_one", "late_two")


def test_tool_result_name_inference_is_branch_local(tmp_path: Path) -> None:
    tree = _new_tree(tmp_path)
    root = tree.append_message(AgentUserMessage(content=ProductContent("run")))
    first_call = tree.append_message(
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(
                AgentToolCall("shared-correlation", "first_tool", ProductContent("{}")),
            ),
        )
    )
    first_result = tree.append_message(
        AgentToolResultMessage(
            tool_request_id="pipy-tool-first",
            tool_name="first_tool",
            content=ProductContent("first"),
            provider_correlation_id="shared-correlation",
        )
    )
    tree.set_leaf(root.id)
    second_call = tree.append_message(
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(
                AgentToolCall(
                    "shared-correlation", "second_tool", ProductContent("{}")
                ),
            ),
        )
    )
    second_result = tree.append_message(
        AgentToolResultMessage(
            tool_request_id="pipy-tool-second",
            tool_name="second_tool",
            content=ProductContent("second"),
            provider_correlation_id="shared-correlation",
        )
    )

    assert tree.path is not None
    reopened = NativeSessionTree.open(tree.path)
    first_entry = reopened.by_id[first_result.id]
    second_entry = reopened.by_id[second_result.id]
    assert isinstance(first_entry, MessageEntry)
    assert isinstance(first_entry.message, AgentToolResultMessage)
    assert first_entry.message.tool_name == "first_tool"
    assert first_entry.parent_id == first_call.id
    assert isinstance(second_entry, MessageEntry)
    assert isinstance(second_entry.message, AgentToolResultMessage)
    assert second_entry.message.tool_name == "second_tool"
    assert second_entry.parent_id == second_call.id


def test_unresolved_tool_result_preserves_storage_and_ancestry(tmp_path: Path) -> None:
    tree = _new_tree(tmp_path)
    root = tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    assert tree.path is not None
    unresolved_body = {
        "type": "message",
        "id": "unresolved-tool-result",
        "parentId": root.id,
        "timestamp": "2026-07-18T00:00:00+00:00",
        "message": {
            "role": "tool",
            "tool_request_id": "legacy-tool-id",
            "output_text": "legacy output",
            "is_error": False,
            "provider_correlation_id": None,
        },
    }
    child_body = {
        "type": "message",
        "id": "child-after-unresolved",
        "parentId": unresolved_body["id"],
        "timestamp": "2026-07-18T00:00:01+00:00",
        "message": {"role": "user", "content": "CHILD"},
    }
    with tree.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(unresolved_body) + "\n")
        handle.write(json.dumps(child_body) + "\n")

    reopened = NativeSessionTree.open(tree.path)
    assert [entry.id for entry in reopened.get_branch()] == [
        root.id,
        unresolved_body["id"],
        child_body["id"],
    ]
    assert [message.content.value for message in reopened.build_context().messages] == [
        "ROOT",
        "CHILD",
    ]

    forked = NativeSessionTree.fork_from(
        tree.path,
        tmp_path / "workspace",
        leaf_id=str(child_body["id"]),
        session_dir=tmp_path / "forked-sessions",
    )
    assert forked.path is not None
    forked_bodies = [json.loads(line) for line in forked.path.read_text().splitlines()]
    forked_messages = [body for body in forked_bodies if body.get("type") == "message"]
    assert forked_messages[1]["message"] == unresolved_body["message"]
    assert forked_messages[2]["parentId"] == forked_messages[1]["id"]


# --------------------------------------------------------------------------
# Standalone build_context for compaction / branch summary
# --------------------------------------------------------------------------


def test_fork_of_compacted_branch_preserves_kept_messages(tmp_path: Path) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    session_dir = tmp_path / "s"
    tree = NativeSessionTree.create(cwd, session_dir=session_dir)
    tree.append_message(AgentUserMessage(content=ProductContent("OLD")))
    tree.append_message(AgentAssistantMessage(content=ProductContent("OLD-R")))
    keep = tree.append_message(AgentUserMessage(content=ProductContent("KEEP")))
    tree.append_compaction(
        summary="summary-of-old", first_kept_entry_id=keep.id, tokens_before=10
    )
    tree.append_message(AgentAssistantMessage(content=ProductContent("KEEP-R")))

    source_texts = [m.content.value for m in tree.build_context().messages]
    assert "KEEP" in source_texts
    assert "OLD" not in source_texts

    assert tree.path is not None
    forked = NativeSessionTree.fork_from(
        tree.path, cwd, leaf_id=tree.get_leaf_id(), session_dir=session_dir
    )
    fork_texts = [m.content.value for m in forked.build_context().messages]
    # The retained boundary must survive the fork: KEEP/KEEP-R kept, OLD dropped.
    assert "KEEP" in fork_texts
    assert "KEEP-R" in fork_texts
    assert "OLD" not in fork_texts


def test_compaction_keeps_summary_then_kept_messages(tmp_path: Path) -> None:
    tree = _new_tree(tmp_path)
    tree.append_message(AgentUserMessage(content=ProductContent("OLD-1")))
    tree.append_message(AgentAssistantMessage(content=ProductContent("REPLY-1")))
    keep_user = tree.append_message(AgentUserMessage(content=ProductContent("KEEP")))
    tree.append_compaction(
        summary="earlier turns summarized",
        first_kept_entry_id=keep_user.id,
        tokens_before=1234,
    )
    tree.append_message(AgentAssistantMessage(content=ProductContent("REPLY-KEEP")))

    texts = [m.content.value for m in tree.build_context().messages]
    assert "earlier turns summarized" in texts[0]
    assert "OLD-1" not in texts
    assert "KEEP" in texts
    assert "REPLY-KEEP" in texts


# --------------------------------------------------------------------------
# Tree builder: child ordering, label derivation, deep-history safety
# --------------------------------------------------------------------------


def test_build_tree_nodes_sorts_children_by_timestamp(tmp_path: Path) -> None:
    from pipy_harness.native.session_tree import build_tree_nodes

    # Three siblings appended (in the entries list) out of timestamp order; Pi
    # sorts each node's children by timestamp ascending regardless of file order.
    root = MessageEntry(
        id="r",
        parent_id=None,
        timestamp="2026-07-14T00:00:00Z",
        message=AgentUserMessage(content=ProductContent("ROOT")),
    )
    late = MessageEntry(
        id="c_late",
        parent_id="r",
        timestamp="2026-07-14T00:00:03Z",
        message=AgentAssistantMessage(content=ProductContent("LATE")),
    )
    early = MessageEntry(
        id="c_early",
        parent_id="r",
        timestamp="2026-07-14T00:00:01Z",
        message=AgentAssistantMessage(content=ProductContent("EARLY")),
    )
    mid = MessageEntry(
        id="c_mid",
        parent_id="r",
        timestamp="2026-07-14T00:00:02Z",
        message=AgentAssistantMessage(content=ProductContent("MID")),
    )
    roots = build_tree_nodes([root, late, early, mid])
    assert len(roots) == 1
    assert [c.entry.id for c in roots[0].children] == ["c_early", "c_mid", "c_late"]


def test_build_tree_nodes_derives_labels_from_entries(tmp_path: Path) -> None:
    from pipy_harness.native.session_tree import build_tree_nodes

    tree = _new_tree(tmp_path)
    root = tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    reply = tree.append_message(AgentAssistantMessage(content=ProductContent("REPLY")))
    tree.append_label_change(root.id, "pinned")

    roots = build_tree_nodes(tree.entries)
    by_id = {}

    def walk(node) -> None:  # noqa: ANN001 - SessionTreeNode
        by_id[node.entry.id] = node
        for child in node.children:
            walk(child)

    for r in roots:
        walk(r)

    assert by_id[root.id].label == "pinned"
    assert by_id[root.id].label_timestamp is not None
    # Unlabelled node carries no label/timestamp (RPC/JSON omits the keys).
    assert by_id[reply.id].label is None
    assert by_id[reply.id].label_timestamp is None
    # A cleared label (empty) removes the resolved label.
    tree.append_label_change(root.id, "")
    roots2 = build_tree_nodes(tree.entries)
    assert roots2[0].label is None


def test_snapshot_entries_and_leaf_coherent_under_concurrent_appends(
    tmp_path: Path,
) -> None:
    import threading

    from pipy_harness.native.session_tree import build_tree_nodes

    tree = _new_tree(tmp_path)
    tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))

    stop = threading.Event()

    def hammer() -> None:
        i = 0
        while not stop.is_set():
            tree.append_message(AgentAssistantMessage(content=ProductContent(f"M{i}")))
            if i % 5 == 0:
                leaf = tree.leaf_id
                if leaf is not None:
                    tree.append_label_change(leaf, f"L{i}")
            i += 1

    worker = threading.Thread(target=hammer, daemon=True)
    worker.start()
    try:
        for _ in range(400):
            entries, leaf = tree.snapshot_entries_and_leaf()
            ids = {e.id for e in entries}
            # leaf is always present in its own entries snapshot (never ahead).
            assert leaf is None or leaf in ids
            # Building the tree from the snapshot never KeyErrors and labels
            # resolve consistently with the captured entries.
            roots = build_tree_nodes(entries)
            assert roots
    finally:
        stop.set()
        worker.join(timeout=5.0)

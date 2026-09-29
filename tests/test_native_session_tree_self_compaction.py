"""A compaction that keeps no earlier entry (Pi ``firstKeptEntryId ?? id``)."""

from __future__ import annotations

from pathlib import Path

from test_native_session_tree_anchored_compaction import _cycle, _texts, _tree

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.session_tree import CompactionEntry, NativeSessionTree


def _exchange(tree: NativeSessionTree, text: str) -> None:
    tree.append_message(AgentUserMessage(ProductContent(text)))
    tree.append_message(AgentAssistantMessage(ProductContent(f"{text} reply")))


def _assert_reopen_and_fork(tmp_path: Path, tree: NativeSessionTree) -> None:
    assert tree.path is not None
    reopened = NativeSessionTree.open(tree.path)
    assert reopened.build_context() == tree.build_context()
    assert reopened.build_coding_context() == tree.build_coding_context()
    strict = NativeSessionTree.open(tree.path, strict=True)
    assert strict.build_coding_context() == tree.build_coding_context()
    forked = NativeSessionTree.fork_from(
        tree.path, tmp_path / "workspace", session_dir=tmp_path / "forks"
    )
    assert forked.build_context() == tree.build_context()
    source = [e for e in tree.get_entries() if isinstance(e, CompactionEntry)]
    copies = [e for e in forked.get_entries() if isinstance(e, CompactionEntry)]
    for original, copy in zip(source, copies, strict=True):
        if original.first_kept_entry_id == original.id:
            assert copy.first_kept_entry_id == copy.id != original.id


def test_none_first_kept_stores_own_id_and_keeps_only_later_entries(
    tmp_path: Path,
) -> None:
    tree = _tree(tmp_path)
    _exchange(tree, "old")
    _cycle(tree, "old-cycle")
    entry = tree.append_compaction(
        summary="summary", first_kept_entry_id=None, tokens_before=10
    )
    assert entry.first_kept_entry_id == entry.id
    assert _texts(tree) == []
    assert tree.build_coding_context().prior_summary == "summary"
    _exchange(tree, "new")
    assert _texts(tree) == ["new", "new reply"]
    visible = [message.content.value for message in tree.build_context().messages]
    assert "summary" in visible[0] and visible[1:] == ["new", "new reply"]
    _assert_reopen_and_fork(tmp_path, tree)


def test_self_compaction_after_an_anchored_compaction(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    anchor = tree.append_message(AgentUserMessage(ProductContent("task")))
    _cycle(tree, "old-cycle")
    suffix, _ = _cycle(tree, "kept-cycle")
    tree.append_compaction(
        summary="anchored",
        retained_user_entry_id=anchor.id,
        first_kept_entry_id=suffix.id,
        tokens_before=100,
    )
    tree.append_compaction(summary="self", first_kept_entry_id=None, tokens_before=50)
    _exchange(tree, "next")
    assert _texts(tree) == ["next", "next reply"]
    assert tree.build_coding_context().prior_summary == "self"
    _assert_reopen_and_fork(tmp_path, tree)


def test_later_compactions_after_a_self_compaction(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    _exchange(tree, "old")
    tree.append_compaction(summary="self", first_kept_entry_id=None, tokens_before=50)
    anchor = tree.append_message(AgentUserMessage(ProductContent("task")))
    _cycle(tree, "old-cycle")
    suffix, _ = _cycle(tree, "kept-cycle")
    tree.append_compaction(
        summary="anchored",
        retained_user_entry_id=anchor.id,
        first_kept_entry_id=suffix.id,
        tokens_before=100,
    )
    assert _texts(tree) == ["task", "inspect kept-cycle", "result kept-cycle"]
    _assert_reopen_and_fork(tmp_path, tree)
    _exchange(tree, "later")
    later = tree.get_branch()[-2]
    tree.append_compaction(
        summary="ordinary", first_kept_entry_id=later.id, tokens_before=40
    )
    assert _texts(tree) == ["later", "later reply"]
    _assert_reopen_and_fork(tmp_path, tree)

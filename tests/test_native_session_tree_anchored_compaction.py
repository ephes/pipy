"""Durable retained-user compaction reconstruction contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.session_tree import CompactionEntry, NativeSessionTree


def _tree(tmp_path: Path) -> NativeSessionTree:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    return NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")


def _cycle(tree: NativeSessionTree, name: str):
    call = AgentToolCall(
        provider_correlation_id=f"call-{name}",
        tool_name="read",
        arguments_json=ProductContent(f'{{"path":"{name}"}}'),
    )
    assistant = tree.append_message(
        AgentAssistantMessage(ProductContent(f"inspect {name}"), tool_calls=(call,))
    )
    result = tree.append_message(
        AgentToolResultMessage(
            tool_request_id=f"pipy-tool-{name}",
            provider_correlation_id=f"call-{name}",
            tool_name="read",
            content=ProductContent(f"result {name}"),
        )
    )
    return assistant, result


def _texts(tree: NativeSessionTree) -> list[str]:
    return [message.content.value for message in tree.build_coding_context().messages]


def test_anchored_then_ordinary_cut_uses_effective_chronology(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    tree.append_message(AgentUserMessage(ProductContent("old group")))
    tree.append_message(AgentAssistantMessage(ProductContent("old reply")))
    anchor = tree.append_message(AgentUserMessage(ProductContent("task")))
    _cycle(tree, "old-cycle")
    suffix, _ = _cycle(tree, "kept-cycle")
    tree.append_compaction(
        summary="first summary",
        retained_user_entry_id=anchor.id,
        first_kept_entry_id=suffix.id,
        tokens_before=100,
    )
    newest, _ = _cycle(tree, "newest-cycle")
    tree.append_compaction(
        summary="newer summary",
        retained_user_entry_id=anchor.id,
        first_kept_entry_id=newest.id,
        tokens_before=90,
    )
    tree.append_message(AgentUserMessage(ProductContent("later")))
    tree.append_message(AgentAssistantMessage(ProductContent("later reply")))
    tree.append_compaction(
        summary="second summary",
        first_kept_entry_id=anchor.id,
        tokens_before=80,
    )

    assert _texts(tree) == [
        "task",
        "inspect newest-cycle",
        "result newest-cycle",
        "later",
        "later reply",
    ]
    assert tree.build_coding_context().prior_summary == "second summary"
    visible = [message.content.value for message in tree.build_context().messages]
    assert "second summary" in visible[0]
    assert visible[1:] == _texts(tree)
    assert "inspect old-cycle" not in visible
    assert "inspect kept-cycle" not in visible
    assert tree.path is not None
    reopened = NativeSessionTree.open(tree.path)
    assert reopened.build_context() == tree.build_context()
    forked = NativeSessionTree.fork_from(
        tree.path,
        tmp_path / "workspace",
        session_dir=tmp_path / "repeated-fork",
    )
    assert forked.build_context() == tree.build_context()


def test_reopen_and_fork_preserve_anchor_and_raw_settings(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    tree.append_model_change("before", "removed-model")
    tree.append_message(AgentUserMessage(ProductContent("older")))
    anchor = tree.append_message(AgentUserMessage(ProductContent("task")))
    _cycle(tree, "old-cycle")
    tree.append_model_change("kept-setting-owner", "final-model")
    tree.append_thinking_level_change("high")
    suffix, _ = _cycle(tree, "kept-cycle")
    tree.append_compaction(
        summary="summary",
        retained_user_entry_id=anchor.id,
        first_kept_entry_id=suffix.id,
        tokens_before=100,
    )
    expected = tree.build_context()
    assert expected.model == ("kept-setting-owner", "final-model")
    assert expected.thinking_level == "high"
    assert tree.path is not None
    reopened = NativeSessionTree.open(tree.path)
    assert reopened.build_context() == expected
    forked = NativeSessionTree.fork_from(
        tree.path,
        tmp_path / "workspace",
        session_dir=tmp_path / "forks",
    )
    assert forked.build_context() == expected
    assert forked.path is not None
    body = [json.loads(line) for line in forked.path.read_text().splitlines()]
    compact = next(item for item in body if item.get("type") == "compaction")
    assert compact["retainedUserEntryId"] != anchor.id
    assert compact["firstKeptEntryId"] != suffix.id


@pytest.mark.parametrize(
    "anchor,boundary",
    [(None, "suffix"), ("", "suffix"), ("anchor", None), ("anchor", "")],
)
def test_anchored_json_fields_are_strict(
    tmp_path: Path, anchor: object, boundary: object
) -> None:
    tree = _tree(tmp_path)
    assert tree.path is not None
    with tree.path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "compaction",
                    "id": "strict",
                    "parentId": None,
                    "timestamp": "now",
                    "summary": "safe",
                    "retainedUserEntryId": anchor,
                    "firstKeptEntryId": boundary,
                    "tokensBefore": 1,
                }
            )
            + "\n"
        )
    with pytest.raises(ValueError, match="malformed anchored"):
        NativeSessionTree.open(tree.path)


@pytest.mark.parametrize(
    "field,value",
    [("id", None), ("id", ""), ("parentId", 7), ("tokensBefore", "invalid")],
)
def test_other_malformed_anchored_fields_raise_value_error(
    tmp_path: Path, field: str, value: object
) -> None:
    tree = _tree(tmp_path)
    assert tree.path is not None
    body: dict[str, object] = {
        "type": "compaction",
        "id": "strict",
        "parentId": None,
        "timestamp": "now",
        "summary": "safe",
        "retainedUserEntryId": "anchor",
        "firstKeptEntryId": "suffix",
        "tokensBefore": 1,
    }
    body[field] = value
    with tree.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(body) + "\n")
    with pytest.raises(ValueError):
        NativeSessionTree.open(tree.path)


def test_anchor_must_be_actual_effective_user(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    synthetic = tree.append_custom_message("note", "task")
    suffix, _ = _cycle(tree, "one")
    with pytest.raises(ValueError, match="actual user"):
        tree.append_compaction(
            summary="safe",
            retained_user_entry_id=synthetic.id,
            first_kept_entry_id=suffix.id,
            tokens_before=1,
        )


def test_anchored_load_rejects_missing_effective_reference(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    anchor = tree.append_message(AgentUserMessage(ProductContent("task")))
    _cycle(tree, "old-cycle")
    suffix, _ = _cycle(tree, "kept-cycle")
    tree.append_compaction(
        summary="first",
        retained_user_entry_id=anchor.id,
        first_kept_entry_id=suffix.id,
        tokens_before=10,
    )
    tree.append_compaction(
        summary="ordinary",
        first_kept_entry_id=suffix.id,
        tokens_before=8,
    )
    assert tree.path is not None
    with tree.path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "compaction",
                    "id": "invalid-later",
                    "parentId": tree.get_leaf_id(),
                    "timestamp": "now",
                    "summary": "safe",
                    "retainedUserEntryId": anchor.id,
                    "firstKeptEntryId": suffix.id,
                    "tokensBefore": 1,
                }
            )
            + "\n"
        )
    with pytest.raises(ValueError, match="unavailable effective ancestor"):
        NativeSessionTree.open(tree.path)


@pytest.mark.parametrize("missing", ["map", "anchor", "suffix"])
def test_fork_clone_requires_both_anchor_mappings(tmp_path: Path, missing: str) -> None:
    tree = _tree(tmp_path)
    entry = CompactionEntry(
        id="cut",
        parent_id=None,
        timestamp="now",
        summary="safe",
        retained_user_entry_id="anchor",
        first_kept_entry_id="suffix",
        tokens_before=1,
    )
    mapping = (
        None
        if missing == "map"
        else {
            "anchor": "new-anchor",
            "suffix": "new-suffix",
        }
    )
    if mapping is not None:
        mapping.pop(missing)
    with pytest.raises(ValueError, match="reference (map is required|was not cloned)"):
        tree._clone_entry_onto_leaf(entry, id_map=mapping)


def test_anchored_append_keeps_state_first_disk_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _tree(tmp_path)
    anchor = tree.append_message(AgentUserMessage(ProductContent("task")))
    _cycle(tree, "old-cycle")
    suffix, _ = _cycle(tree, "kept-cycle")
    assert tree.path is not None
    persisted_before = NativeSessionTree.open(tree.path).get_entries()

    def fail_write(_entry: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(tree, "_write_entry", fail_write)
    with pytest.raises(OSError, match="disk full"):
        tree.append_compaction(
            summary="safe",
            retained_user_entry_id=anchor.id,
            first_kept_entry_id=suffix.id,
            tokens_before=10,
        )
    assert isinstance(tree.get_entries()[-1], CompactionEntry)
    assert NativeSessionTree.open(tree.path).get_entries() == persisted_before


@pytest.mark.parametrize("boundary_kind", ["removed", "nonexistent"])
def test_later_ordinary_boundary_must_exist_in_anchored_effective_history(
    tmp_path: Path, boundary_kind: str
) -> None:
    tree = _tree(tmp_path)
    anchor = tree.append_message(AgentUserMessage(ProductContent("task")))
    removed, _ = _cycle(tree, "old-cycle")
    suffix, _ = _cycle(tree, "kept-cycle")
    tree.append_compaction(
        summary="anchored",
        retained_user_entry_id=anchor.id,
        first_kept_entry_id=suffix.id,
        tokens_before=10,
    )
    invalid_boundary = removed.id if boundary_kind == "removed" else "does-not-exist"
    assert tree.path is not None
    before = (
        tree.mutation_epoch,
        tree.get_entries(),
        tree.get_leaf_id(),
        tree.path.read_bytes(),
    )
    with pytest.raises(ValueError, match="boundary unavailable"):
        tree.append_compaction(
            summary="invalid ordinary",
            first_kept_entry_id=invalid_boundary,
            tokens_before=8,
        )
    assert (
        tree.mutation_epoch,
        tree.get_entries(),
        tree.get_leaf_id(),
        tree.path.read_bytes(),
    ) == before

    with tree.path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "compaction",
                    "id": f"invalid-{boundary_kind}",
                    "parentId": tree.get_leaf_id(),
                    "timestamp": "now",
                    "summary": "invalid ordinary",
                    "firstKeptEntryId": invalid_boundary,
                    "tokensBefore": 8,
                }
            )
            + "\n"
        )

    reopened = NativeSessionTree.open(tree.path)
    for build in (reopened.build_context, reopened.build_coding_context):
        with pytest.raises(ValueError, match="boundary unavailable"):
            build()


def test_legacy_only_missing_latest_boundary_keeps_permissive_behavior(
    tmp_path: Path,
) -> None:
    tree = _tree(tmp_path)
    tree.append_message(AgentUserMessage(ProductContent("older")))
    tree.append_compaction(
        summary="legacy summary",
        first_kept_entry_id="missing-legacy-boundary",
        tokens_before=3,
    )
    later = tree.append_message(AgentUserMessage(ProductContent("later")))

    coding = tree.build_coding_context()
    assert coding.messages == (later.message,)
    assert coding.entry_ids == (later.id,)
    assert coding.prior_summary == "legacy summary"
    assert tree.path is not None
    assert NativeSessionTree.open(tree.path).build_coding_context() == coding

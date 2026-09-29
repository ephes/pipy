"""System messages in the native session tree (Pi `9e05370b2`).

A stored system message is transcript state: `build_context` (Pi
`buildSessionContext`) returns it, `build_coding_context` (provider history)
does not, and a compaction stores the replayed state as its checkpoint.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentSystemMessage,
    AgentToolDeclaration,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.system_messages import current_system_message
from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree
from pipy_harness.native.session_tree_commands import _message_entry_preview

_READ = AgentToolDeclaration("read", "Read a file.", '{"type":"object"}')
_GREP = AgentToolDeclaration("grep", "Search.", '{"type":"object"}')


def _new_tree(tmp_path: Path) -> NativeSessionTree:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    return NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")


def _session_file(tmp_path: Path) -> Path:
    return next((tmp_path / "sessions").glob("*.jsonl"))


def test_system_messages_round_trip_and_stay_out_of_provider_history(
    tmp_path: Path,
) -> None:
    tree = _new_tree(tmp_path)
    leading = AgentSystemMessage(
        sections=(("preamble", "prompt"),), tools_added=(_READ,)
    )
    tree.append_message(leading)
    tree.append_message(AgentUserMessage(ProductContent("hi")))
    tree.append_message(AgentAssistantMessage(ProductContent("hello")))

    records = [
        json.loads(line) for line in _session_file(tmp_path).read_text().splitlines()
    ]
    stored = records[1]["message"]
    assert stored == {
        "role": "system",
        "content": "",
        "sections": {"preamble": "prompt"},
        "toolsAdded": [
            {
                "name": "read",
                "description": "Read a file.",
                "parameters": {"type": "object"},
            }
        ],
    }

    for strict in (False, True):
        reopened = NativeSessionTree.open(_session_file(tmp_path), strict=strict)
        messages = reopened.build_context().messages
        assert messages[0] == leading
        assert [type(m).__name__ for m in messages] == [
            "AgentSystemMessage",
            "AgentUserMessage",
            "AgentAssistantMessage",
        ]
        coding = reopened.build_coding_context()
        assert not any(isinstance(m, AgentSystemMessage) for m in coding.messages)


def test_compaction_stores_a_checkpoint_that_replaces_earlier_system_entries(
    tmp_path: Path,
) -> None:
    tree = _new_tree(tmp_path)
    tree.append_message(
        AgentSystemMessage(sections=(("preamble", "one"),), tools_added=(_READ,))
    )
    first = tree.append_message(AgentUserMessage(ProductContent("first")))
    tree.append_message(AgentAssistantMessage(ProductContent("a1")))
    tree.append_message(
        AgentSystemMessage(sections=(("preamble", "two"),), tools_added=(_GREP,))
    )
    tree.append_message(AgentUserMessage(ProductContent("second")))

    compaction = tree.append_compaction(
        summary="summary", first_kept_entry_id=first.id, tokens_before=10
    )

    checkpoint = AgentSystemMessage(
        sections=(("preamble", "two"),), tools_added=(_READ, _GREP)
    )
    assert compaction.system_message == checkpoint
    messages = tree.build_context().messages
    assert messages[0] == checkpoint
    # The retained range keeps its user/assistant messages but not its
    # system messages; the checkpoint already carries their replay.
    assert sum(isinstance(m, AgentSystemMessage) for m in messages) == 1
    assert current_system_message(messages) == checkpoint
    entries = tree.build_context_entries()
    assert not any(
        isinstance(e, MessageEntry) and isinstance(e.message, AgentSystemMessage)
        for e in entries
    )

    later = AgentSystemMessage(sections=(("preamble", "three"),))
    tree.append_message(later)
    assert tree.build_context().messages[-1] == later

    record = json.loads(_session_file(tmp_path).read_text().splitlines()[6])
    assert record["type"] == "compaction"
    assert record["systemMessage"]["sections"] == {"preamble": "two"}
    for strict in (False, True):
        reopened = NativeSessionTree.open(_session_file(tmp_path), strict=strict)
        assert reopened.build_context().messages[0] == checkpoint


def test_a_compaction_without_system_state_stores_no_checkpoint(
    tmp_path: Path,
) -> None:
    tree = _new_tree(tmp_path)
    first = tree.append_message(AgentUserMessage(ProductContent("first")))
    compaction = tree.append_compaction(
        summary="summary", first_kept_entry_id=first.id, tokens_before=1
    )

    assert compaction.system_message is None
    record = json.loads(_session_file(tmp_path).read_text().splitlines()[-1])
    assert "systemMessage" not in record


_MALFORMED_SYSTEM_MESSAGES = [
    {"role": "system", "toolsAdded": ["read"]},
    {"role": "system", "toolsAdded": [{"name": "read"}]},
    {
        "role": "system",
        "toolsAdded": [{"name": "read", "description": "d", "parameters": 42}],
    },
    {"role": "system", "content": [{"type": "image", "data": "x"}]},
    {"role": "system", "content": 7},
    {"role": "system", "sections": {"preamble": 1}},
    {"role": "system", "sections": ["preamble"]},
    {"role": "system", "toolsRemoved": ["read"]},
]


@pytest.mark.parametrize("body", _MALFORMED_SYSTEM_MESSAGES)
@pytest.mark.parametrize("where", ["message", "checkpoint"])
def test_strict_open_refuses_a_malformed_system_message(
    tmp_path: Path, body: dict[str, object], where: str
) -> None:
    tree = _new_tree(tmp_path)
    first = tree.append_message(AgentUserMessage(ProductContent("hi")))
    path = _session_file(tmp_path)
    record: dict[str, object] = {
        "id": "bad00001",
        "parentId": tree.get_leaf_id(),
        "timestamp": "2026-09-30T00:00:00.000Z",
    }
    if where == "message":
        record.update(type="message", message=body)
    else:
        record.update(
            type="compaction",
            summary="s",
            firstKeptEntryId=first.id,
            tokensBefore=1,
            systemMessage=body,
        )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")

    with pytest.raises(ValueError):
        NativeSessionTree.open(path, strict=True)
    # The permissive loader keeps loading the rest of the file.
    NativeSessionTree.open(path)


def test_tree_preview_shows_system_entries_as_role_only(tmp_path: Path) -> None:
    tree = _new_tree(tmp_path)
    entry = tree.append_message(AgentSystemMessage(sections=(("preamble", "p"),)))

    assert _message_entry_preview(entry) == "[system]"

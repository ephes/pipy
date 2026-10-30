"""Unsupported/fenced storage facts refuse without crossing capabilities."""

import asyncio
from dataclasses import replace

import pytest
from generation_support import admit, definition, seed
from test_tool_boundaries import run_idle
from test_tool_execution import KeyedTool, RoundWire

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentUserMessage
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.completed_projection import (
    ProjectionClosed,
    observe_completed,
)
from pipy_harness.native.durable.documents import define_doc, resolve
from pipy_harness.native.durable.generation import message_entry
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.records import EntryRecord, TaskRecord
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.storage import Page
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.native.durable.tool_history import ORDER_DOC


def refuse_view(session, cid):
    async def run():
        with pytest.raises(ProjectionClosed) as caught:
            await observe_completed(session, cid)
        assert caught.value.reason == "capture_error"
        assert "synthetic-private" not in str(caught.value)

    asyncio.run(run())


@pytest.mark.parametrize(
    "wrong",
    [
        "entry_conversation",
        "entry_owner",
        "document_owner",
        "document_newer",
        "fork",
        "head",
    ],
)
def test_guarded_reader_refuses_foreign_newer_or_unsupported_context(
    monkeypatch, wrong
):
    backend = MemoryStorage()
    session = Session(backend)
    cid, other = [
        session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
        for _ in range(2)
    ]
    entry = session.commit(
        lambda tx: tx.append_entry(
            cid, message_entry(AgentUserMessage(ProductContent("synthetic-private")))
        )
    )
    if wrong.startswith("entry_"):
        row = entry.to_tree()
        row.update(
            {"conversationId": other}
            if wrong == "entry_conversation"
            else {"byTaskId": 999}
        )
        monkeypatch.setattr(
            backend, "scan_entries", lambda *args: Page((EntryRecord(row),))
        )
    elif wrong == "document_owner":
        session.commit(
            lambda tx: tx.doc(ORDER_DOC, conversation_id=other).update({"rounds": {}})
        )
        _, address = resolve(ORDER_DOC, conversation_id=other)
        foreign = backend.find_document(address)
        original = backend.find_document
        monkeypatch.setattr(
            backend,
            "find_document",
            lambda address, *args: (
                foreign
                if address.to_tree()["kind"] == ORDER_DOC.kind
                else original(address, *args)
            ),
        )
        monkeypatch.setattr(
            backend,
            "document",
            lambda *args: pytest.fail("followed a foreign document ID"),
        )
    elif wrong == "document_newer":
        newer = define_doc(
            ORDER_DOC.kind,
            2,
            "conversation",
            lambda: {"rounds": {}},
            "latest",
            "initial",
            checkpoint_when=lambda _value, _ops, _info: False,
        )
        session.commit(
            lambda tx: tx.doc(newer, conversation_id=cid).update({"rounds": {}})
        )
    elif wrong == "fork":
        from pipy_harness.native.durable.records import ConversationRecord

        monkeypatch.setattr(
            backend,
            "conversation",
            lambda id: ConversationRecord(
                {"id": cid, "parent": {"conversationId": other, "at": entry.id}}
            ),
        )
    else:
        row = entry.to_tree()
        row["head"] = entry.id
        monkeypatch.setattr(
            backend, "scan_entries", lambda *args: Page((EntryRecord(row),))
        )
    try:
        refuse_view(session, cid)
        assert session.failure is None
        assert (
            not session._commit_listeners.copy() and not session._close_listeners.copy()
        )
    finally:
        session.close(timeout=5)


@pytest.mark.parametrize(
    "wrong", ["closed_foreign_result", "child_owner", "child_input"]
)
def test_actual_completed_round_rejects_corrupt_links_before_following_foreign_id(
    tmp_path, monkeypatch, wrong
):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    backend = MemoryStorage()
    session = Session(backend)
    port = KeyedTool("one", [])
    wire = RoundWire(("one",))
    generation = definition(tmp_path, wire)
    tool = define_tool_task(
        {"one": ToolRegistration(port, "fixture.one", "1")}, workspace_root=tmp_path
    )
    cid, _, _ = admit(
        session,
        generation,
        snapshot_provider_request(
            replace(seed(tmp_path).request, available_tools=(port.definition,))
        ),
    )
    run_idle(session, [generation, tool])
    other = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    foreign = session.commit(
        lambda tx: tx.append_entry(
            other, message_entry(AgentUserMessage(ProductContent("synthetic-private")))
        )
    )
    if wrong == "closed_foreign_result":
        key = next(
            iter(
                session.snapshot(ORDER_DOC, conversation_id=cid).value.thaw()["rounds"]
            )
        )
        session.commit(
            lambda tx: tx.doc(ORDER_DOC, conversation_id=cid).update(
                {"rounds": {key: [foreign.id]}}
            )
        )
        original = backend.entry

        def fenced(id, **kwargs):
            assert id != foreign.id, "followed foreign embedded entry ID"
            return original(id, **kwargs)

        monkeypatch.setattr(backend, "entry", fenced)
    else:
        original = backend.scan_tasks

        def corrupt(*args):
            page = original(*args)
            rows = []
            for record in page.items:
                row = record.to_tree()
                if row["kind"] == "pi.tool":
                    if wrong == "child_owner":
                        row["owner"] = foreign.id
                    else:
                        row["input"]["assistant"] = foreign.id
                rows.append(TaskRecord(row))
            return Page(tuple(rows), page.next)

        monkeypatch.setattr(backend, "scan_tasks", corrupt)
    try:
        refuse_view(session, cid)
        assert len(wire.calls) == 2 and len(port.calls) == 1 and session.failure is None
    finally:
        session.close(timeout=5)


def test_absent_baseline_reads_never_mint_commit_or_initialize(monkeypatch):
    backend = MemoryStorage()
    session = Session(backend)
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id

    def forbid(*args, **kwargs):
        pytest.fail("observer attempted a storage mutation")

    monkeypatch.setattr(backend, "mint_id", forbid)
    monkeypatch.setattr(backend, "commit", forbid)

    async def run():
        watch = await observe_completed(session, cid)
        assert not watch.snapshot.messages and watch.snapshot.usage is None
        watch.stop()
        await watch.wait_closed()

    try:
        asyncio.run(run())
    finally:
        session.close(timeout=5)

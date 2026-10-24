"""Actual SQLite ack/storage/adoption faults at the final child/parent boundary."""

import json
import sqlite3
import threading

import pytest
from generation_support import admit
from test_tool_sqlite import resume
from tool_support import counts, definitions

from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable import tx as tx_module
from pipy_harness.native.durable.generation import USAGE_DOC
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import EntryQuery


def final_target(connection, parent):
    rows = [
        sqlite._decode(row[0]) for row in connection.execute("SELECT record FROM tasks")
    ]
    last = next(
        (
            r
            for r in rows
            if r["kind"] == "pi.tool" and r["input"]["callId"] == "call:1"
        ),
        None,
    )
    owner = next(r for r in rows if r["id"] == parent)
    return (
        last is not None
        and last["state"]["status"] == "terminal"
        and owner["state"]["status"] == "pending"
    )


def install_fault(monkeypatch, parent, fault, reached):
    def fail_before(connection):
        if final_target(connection, parent):
            reached.set()
            raise sqlite3.OperationalError("injected tool commit I/O failure")

    def fail_after(connection):
        if final_target(connection, parent):
            reached.set()
            if fault == "ack":
                raise RuntimeError("injected tool acknowledgement loss")

    original_adopt = tx_module._TxCore.adopt

    def fail_adopt(self, seq):
        if reached.is_set():
            raise RuntimeError("injected tool adoption failure")
        return original_adopt(self, seq)

    if fault == "before":
        monkeypatch.setattr(sqlite, "_before_commit", fail_before)
    else:
        monkeypatch.setattr(sqlite, "_after_commit", fail_after)
    if fault == "adoption":
        monkeypatch.setattr(tx_module._TxCore, "adopt", fail_adopt)


@pytest.mark.parametrize("fault", ["before", "ack", "adoption"])
def test_real_final_tool_commit_fault_seals_owner_and_reopen_reuses_receipt(
    tmp_path, monkeypatch, fault
):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    generation, tool, snapshot = definitions(tmp_path)
    session = Session(
        open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="create")
    )
    conversation, _, parent = admit(session, generation, snapshot)
    scheduler = TaskScheduler(session, [generation, tool])
    scheduler.open()
    reached = threading.Event()
    publications = []

    def observe(publication):
        publications.extend(
            change.to_tree()
            for change in publication.changes
            if hasattr(change, "to_tree") and change.to_tree()["type"] == "entry"
        )

    session.subscribe_commits(observe)

    install_fault(monkeypatch, parent, fault, reached)
    scheduler.resume()
    end = session.wait_closed(timeout=10)
    assert end.reason == "failed" and reached.is_set()
    assert not scheduler._thread.is_alive()
    before = counts(tmp_path)
    assert before == {"calls": 2, "applications": 2, "prepares": 2, "providers": 1}
    # No publication of the unacknowledged final tool result; committed backend
    # truth still wins when COMMIT preceded ack/adoption loss.
    assert (
        len([row for row in publications if row["value"]["kind"] == "pi.tool-result"])
        == 1
    )
    scheduler.resume()
    assert counts(tmp_path) == before
    monkeypatch.undo()
    with sqlite3.connect(tmp_path / "session.sqlite") as reader:
        rows = [
            sqlite._decode(row[0]) for row in reader.execute("SELECT record FROM tasks")
        ]
        assert reader.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    last = next(
        r for r in rows if r["kind"] == "pi.tool" and r["input"]["callId"] == "call:1"
    )
    owner = next(r for r in rows if r["id"] == parent)
    assert (last["state"]["status"], owner["state"]["status"]) == (
        ("running", "waiting") if fault == "before" else ("terminal", "pending")
    )
    (tmp_path / "fault-backend.json").write_text(
        json.dumps(
            {
                "fault": fault,
                "tasks": rows,
                "counts": before,
                "close_reason": end.reason,
            },
            indent=2,
        )
    )
    _, final, completed = resume(tmp_path, parent, missing=True)
    assert completed and all(row["state"]["status"] == "terminal" for row in final)
    assert counts(tmp_path) == {**before, "providers": 2}
    inspector = Session(
        open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="open")
    )
    try:
        entries = inspector.commit(
            lambda tx: tx.scan_entries(
                EntryQuery({"conversationId": conversation}), 100
            )
        ).items
        assert (
            sum(entry.to_tree()["kind"] == "pi.tool-result" for entry in entries) == 2
        )
        ledger = inspector.snapshot(
            USAGE_DOC, conversation_id=conversation
        ).value.thaw()
        assert len(ledger["settlements"]) == 2
        assert ledger["models"]["openai/gpt-test"]["input"] == 32
        assert ledger["models"]["openai/gpt-test"]["reasoning"] == 6
        assert ledger["tools"] == {}
    finally:
        inspector.close(timeout=10)

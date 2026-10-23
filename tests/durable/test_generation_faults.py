"""Actual SQLite acknowledgement/adoption uncertainty seals the generation owner."""

import threading

import pytest
from generation_support import Wire, admit, completion, definition, seed

from pipy_harness.native.durable import tx as tx_module
from pipy_harness.native.durable.generation import USAGE_DOC
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import EntryQuery


def selected(record, stage):
    state = record["state"]
    if stage == "settlement":
        return state["status"] in ("waiting", "terminal")
    return (
        state["status"] == "running"
        and state["checkpoint"]["phase"]
        == {"request": "request", "receipt": "settle"}[stage]
    )


@pytest.mark.parametrize("stage", ["request", "receipt", "settlement"])
@pytest.mark.parametrize("failure", ["before", "ack", "adoption"])
def test_generation_storage_failure_seals_and_recovery_uses_actual_commit(
    tmp_path, monkeypatch, stage, failure
):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    path = tmp_path / "session.sqlite"
    inner = open_sqlite_storage(str(path), mode="create")
    tripped = threading.Event()

    class Storage:
        armed = False

        def __getattr__(self, name):
            return getattr(inner, name)

        def commit(self, writes):
            target = self.armed and any(
                value.get("type") == "task" and selected(value["value"], stage)
                for value in (write.to_tree() for write in writes)
            )
            if target and failure != "adoption":
                self.armed = False
                if failure == "ack":
                    inner.commit(writes)
                tripped.set()
                raise OSError("Synthetic generation commit acknowledgement failure")
            return inner.commit(writes)

    storage = Storage()
    session = Session(storage)
    wire = Wire(("read", "read"))
    task = definition(tmp_path, wire)
    conversation, _, task_id = admit(session, task, seed(tmp_path, ("read",)))
    done, publications = completion(session, task_id)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    original = tx_module._TxCore.adopt

    def adoption(self, seq):
        if storage.armed and any(
            selected(row.to_tree(), stage) for row in self.staged_tasks()
        ):
            storage.armed = False
            tripped.set()
            raise RuntimeError("Synthetic generation adoption failure")
        return original(self, seq)

    if failure == "adoption":
        monkeypatch.setattr(tx_module._TxCore, "adopt", adoption)
    storage.armed = True
    scheduler.resume()
    try:
        assert session.wait_closed(timeout=10).reason == "failed"
        assert tripped.is_set()
        assert scheduler._host_done and not scheduler._thread.is_alive()
        assert not done.is_set() and publications == []
        assert len(wire.calls) == (0 if stage == "request" else 1)
        scheduler.resume()
    finally:
        session.close(timeout=10)
    monkeypatch.setattr(tx_module._TxCore, "adopt", original)
    reopened = Session(open_sqlite_storage(str(path), mode="open"))
    try:
        replay = TaskScheduler(reopened, [task])
        replay.open()
        recovered = next(row.to_tree() for row in replay.inspect() if row.id == task_id)
        committed = failure != "before"
        expected = {
            "request": ("request" if committed else "prepare"),
            "receipt": ("settle" if committed else "request"),
            "settlement": ("tools" if committed else "settle"),
        }[stage]
        assert recovered["state"]["checkpoint"]["phase"] == expected
        assert (recovered["state"]["status"] == "waiting") == (
            stage == "settlement" and committed
        )
        finished, _ = completion(reopened, task_id)
        replay.resume()
        if recovered["state"]["status"] != "waiting":
            assert finished.wait(10)
        rows = [row.to_tree() for row in replay.inspect()]
        assert len([row for row in rows if row["kind"] == "pi.tool"]) == 2
        entries = reopened.commit(
            lambda tx: tx.scan_entries(
                EntryQuery({"conversationId": conversation}), 100
            )
        ).items
        answers = [
            entry for entry in entries if entry.to_tree()["kind"] == "pi.assistant"
        ]
        assert len(answers) == 1
        ledger = reopened.snapshot(USAGE_DOC, conversation_id=conversation).value.thaw()
        assert list(ledger["settlements"].values()) == [answers[0].id]
        assert len(wire.calls) == (
            2 if stage == "receipt" and failure == "before" else 1
        )
    finally:
        reopened.close(timeout=10)

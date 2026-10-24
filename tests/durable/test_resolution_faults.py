"""Nonce winner, ACK/storage faults and reopen through real operator authority."""

import json
import sqlite3
import threading

import pytest
from test_resolution import make_definitions, setup
from tool_support import counts

from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable import tx as tx_module
from pipy_harness.native.durable.inspection import inspect_task
from pipy_harness.native.durable.resolution import AUDIT
from pipy_harness.native.durable.resolution_values import ResolutionError
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))


def audit_present(connection):
    return any(
        sqlite._decode(row[0])["kind"] == AUDIT.kind
        for row in connection.execute("SELECT record FROM documents")
    )


def install_fault(patch, fault, reached):
    adopt = tx_module._TxCore.adopt

    def fail_before(connection):
        if audit_present(connection):
            reached.set()
            raise sqlite3.OperationalError("fixture resolution precommit fault")

    def fail_after(connection):
        if audit_present(connection):
            reached.set()
            if fault == "ack":
                raise RuntimeError("fixture resolution ACK loss")

    def fail_adopt(self, seq):
        if reached.is_set():
            raise RuntimeError("fixture resolution adoption loss")
        return adopt(self, seq)

    if fault == "before":
        patch.setattr(sqlite, "_before_commit", fail_before)
    else:
        patch.setattr(sqlite, "_after_commit", fail_after)
    if fault == "adoption":
        patch.setattr(tx_module._TxCore, "adopt", fail_adopt)


@pytest.mark.parametrize("fault", ["before", "ack", "adoption"])
def test_real_resolution_fault_reopen_nonce_first_winner(tmp_path, monkeypatch, fault):
    session, scheduler, cid, parent, old, _ = setup(tmp_path, "sqlite")
    target = scheduler.resolution_target(old["id"])
    nonce = "f" * 32
    before = counts(tmp_path)
    reached = threading.Event()
    with monkeypatch.context() as patch:
        install_fault(patch, fault, reached)
        with pytest.raises(ResolutionError):
            scheduler.resolve(target, nonce=nonce)
        assert reached.wait(10)
        end = session.wait_closed(timeout=10)
        assert end.reason == "failed" and not scheduler._thread.is_alive()
    assert counts(tmp_path) == before
    with sqlite3.connect(tmp_path / "session.sqlite") as reader:
        rows = [
            sqlite._decode(r[0]) for r in reader.execute("SELECT record FROM tasks")
        ]
        persisted = audit_present(reader)
        assert reader.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    old_stored = next(r for r in rows if r["id"] == old["id"])
    assert persisted == (fault != "before")
    assert old_stored["state"]["status"] == (
        "pending" if fault == "before" else "terminal"
    )
    (tmp_path / "resolution-cut.json").write_text(
        json.dumps(
            {
                "fault": fault,
                "tasks": rows,
                "counts": before,
                "audit_present": persisted,
            },
            indent=2,
        )
    )
    generation, tool, _ = make_definitions(tmp_path)
    reopened = Session(
        open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="open")
    )
    owner = TaskScheduler(reopened, [generation, tool])
    try:
        owner.open()
        again = owner.resolution_target(old["id"])
        result = owner.resolve(again, nonce=nonce)
        assert owner.resolve(again, nonce=nonce) == result
        assert (
            counts(tmp_path) == before
        )  # resolution itself does no tool/provider effects
        winner = reopened.snapshot(AUDIT, conversation_id=cid, key=nonce).value.thaw()
        assert winner["source"] == old and winner["new_task_id"] == result.new_task_id
        done = threading.Event()

        def completed(pub):
            if any(
                c.to_tree()["type"] == "task"
                and c.to_tree()["value"]["id"] == parent
                and c.to_tree()["value"]["state"]["status"] == "terminal"
                for c in pub.changes
                if hasattr(c, "to_tree")
            ):
                done.set()

        reopened.subscribe_commits(completed)
        owner.resume()
        assert done.wait(10), reopened.failure
        assert counts(tmp_path) == {
            "calls": 3,
            "applications": 3,
            "prepares": 3,
            "providers": 2,
        }
        assert inspect_task(reopened, old["id"]).outcome == "aborted"
        assert (
            len([r for r in owner.inspect() if r.to_tree()["kind"] == "pi.tool"]) == 3
        )
    finally:
        reopened.close(timeout=10)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize("same_nonce", [True, False])
def test_concurrent_resolution_one_atomic_attempt(tmp_path, backend, same_nonce):
    session, scheduler, cid, parent, old, _ = setup(tmp_path, backend)
    target = scheduler.resolution_target(old["id"])
    start = threading.Barrier(3)
    results, failures = [], []

    def run(nonce):
        start.wait(10)
        try:
            results.append(scheduler.resolve(target, nonce=nonce))
        except ResolutionError as error:
            failures.append(error.code)

    threads = [
        threading.Thread(target=run, args=("1" * 32,)),
        threading.Thread(target=run, args=(("1" if same_nonce else "2") * 32,)),
    ]
    try:
        for thread in threads:
            thread.start()
        start.wait(10)
        for thread in threads:
            thread.join(10)
            assert not thread.is_alive()
        assert len({r.new_task_id for r in results}) == 1
        assert len(results) == (2 if same_nonce else 1)
        assert failures == ([] if same_nonce else ["stale"])
        # Both callers resolved one committed replacement, never two approvals.
        rows = [r.to_tree() for r in scheduler.inspect()]
        assert len([r for r in rows if r["kind"] == "pi.tool"]) == 3
    finally:
        session.close(timeout=10)

"""Independent process death around the single atomic operator transaction."""

import json
import signal
import sqlite3
import threading
from pathlib import Path

import pytest
from sqlite_child_support import Child
from test_resolution import make_definitions
from test_resolution_faults import audit_present
from tool_support import counts

from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.resolution import AUDIT
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage

CHILD = r"""
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from test_resolution import setup
from test_resolution_faults import audit_present
from pipy_harness.native.durable import sqlite
root,cut=Path(sys.argv[2]),sys.argv[3]
session,scheduler,cid,parent,old,_=setup(root,'sqlite')
target=scheduler.resolution_target(old['id'])
def barrier(connection):
    if audit_present(connection):
        say('cut',stage=cut)
        command()
if cut=='before':sqlite._before_commit=barrier
else:sqlite._after_commit=barrier
say('ready',cid=cid,parent=parent,old=old)
assert command()=='go'
scheduler.resolve(target,nonce='3'*32)
say('unexpected-return')
"""


@pytest.mark.parametrize("cut", ["before", "after"])
def test_resolution_sigkill_before_after_commit_reopen_one_attempt(
    tmp_path, monkeypatch, cut
):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    with Child(
        CHILD, str(Path(__file__).parent), str(tmp_path), cut, home=tmp_path
    ) as child:
        ready = child.expect("ready")
        child.send("go")
        assert child.expect("cut")["stage"] == cut
        child.kill_group()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        (tmp_path / "operator-process.json").write_text(
            json.dumps(child.evidence(), indent=2)
        )
    before = counts(tmp_path)
    assert before == {"calls": 1, "applications": 1, "prepares": 1, "providers": 1}
    with sqlite3.connect(tmp_path / "session.sqlite") as reader:
        rows = [
            sqlite._decode(r[0]) for r in reader.execute("SELECT record FROM tasks")
        ]
        assert audit_present(reader) == (cut == "after")
        assert reader.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    old = next(r for r in rows if r["id"] == ready["old"]["id"])
    assert old["state"]["status"] == ("pending" if cut == "before" else "terminal")
    (tmp_path / "operator-persisted.json").write_text(
        json.dumps({"tasks": rows, "counts": before}, indent=2)
    )
    generation, tool, _ = make_definitions(tmp_path)
    session = Session(
        open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="open")
    )
    owner = TaskScheduler(session, [generation, tool])
    try:
        owner.open()
        target = owner.resolution_target(old["id"])
        result = owner.resolve(target, nonce="3" * 32)
        assert owner.resolve(target, nonce="3" * 32) == result
        assert counts(tmp_path) == before
        assert (
            session.snapshot(
                AUDIT, conversation_id=ready["cid"], key="3" * 32
            ).value.thaw()["source"]
            == ready["old"]
        )
        done = threading.Event()

        def complete(pub):
            if any(
                c.to_tree()["type"] == "task"
                and c.to_tree()["value"]["id"] == ready["parent"]
                and c.to_tree()["value"]["state"]["status"] == "terminal"
                for c in pub.changes
                if hasattr(c, "to_tree")
            ):
                done.set()

        session.subscribe_commits(complete)
        owner.resume()
        assert done.wait(10), session.failure
        assert counts(tmp_path) == {
            "calls": 3,
            "applications": 3,
            "prepares": 3,
            "providers": 2,
        }
        assert (
            len([r for r in owner.inspect() if r.to_tree()["kind"] == "pi.tool"]) == 3
        )
    finally:
        session.close(timeout=10)

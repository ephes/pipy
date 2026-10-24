"""Real DE08 SQLite process death across owned tool intent/effect/receipt cuts."""

import json
import signal
import sqlite3
import threading
from pathlib import Path

import pytest
from sqlite_child_support import Child
from tool_support import counts, definitions

from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import TaskQuery

CHILD = r"""
import threading
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from generation_support import admit
from tool_support import definitions
from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
root,cut,replay = Path(sys.argv[2]),sys.argv[3],sys.argv[4]
def barrier(label):
    say('cut',label=label)
    if not threading.Event().wait(60):raise RuntimeError('missing kill')
generation,tool,snapshot=definitions(root,replay=replay,barrier=barrier,cut=cut,hints=sys.argv[5]=='hints')
session=Session(open_sqlite_storage(str(root/'session.sqlite'),mode='create'))
conversation,_,parent=admit(session,generation,snapshot)
scheduler=TaskScheduler(session,[generation,tool]);scheduler.open()
def at(connection,point):
    rows=[sqlite._decode(row[0]) for row in connection.execute('SELECT record FROM tasks')]
    wanted='call:1' if cut.startswith('final-') else 'call:0'
    child=next((r for r in rows if r['kind']=='pi.tool' and r['input']['callId']==wanted),None)
    if child is None:return
    state=child['state']; phase=state.get('checkpoint',{}).get('phase')
    stage='intent' if phase=='execute' else 'receipt' if phase=='settle' else 'result' if state['status']=='terminal' else 'pending'
    if cut.startswith('final-'):
        owner=next(r for r in rows if r['id']==parent)
        if stage=='result' and owner['state']['status']=='pending' and cut=='final-result-'+point:barrier(cut)
    elif cut==stage+'-'+point:barrier(cut)
sqlite._before_commit=lambda c:at(c,'before')
sqlite._after_commit=lambda c:at(c,'after')
say('ready',parent=parent,conversation=conversation)
assert command()=='go'
scheduler.resume()
assert command()=='close'
session.close()
"""


def cut_store(tmp_path, cut, replay, *, hints=False):
    with Child(
        CHILD,
        str(Path(__file__).parent),
        str(tmp_path),
        cut,
        replay,
        "hints" if hints else "none",
        home=tmp_path,
    ) as child:
        ready = child.expect("ready")
        child.send("go")
        assert child.expect("cut")["label"] == cut
        child.kill_group()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        (tmp_path / "process.json").write_text(json.dumps(child.evidence(), indent=2))
    from pipy_harness.native.durable import sqlite as sqlite_backend

    with sqlite3.connect(tmp_path / "session.sqlite") as reader:
        raw = [
            sqlite_backend._decode(row[0])
            for row in reader.execute("SELECT record FROM tasks")
        ]
        integrity = reader.execute("PRAGMA integrity_check").fetchone()[0]
    (tmp_path / "cut-persisted.json").write_text(
        json.dumps(
            {"tasks": raw, "integrity": integrity, "counts": counts(tmp_path)}, indent=2
        )
    )
    assert integrity == "ok"
    return ready


def resume(root, parent, *, replay="unsafe", **kwargs):
    generation, tool, _ = definitions(root, replay=replay, **kwargs)
    session = Session(open_sqlite_storage(str(root / "session.sqlite"), mode="open"))
    scheduler = TaskScheduler(session, [generation, tool])
    done = threading.Event()
    idle = threading.Event()
    real = scheduler._reserve

    async def drained():
        result = await real()
        if result is None:
            idle.set()
        return result

    scheduler._reserve = drained

    def observe(publication):
        for change in publication.changes:
            if not hasattr(change, "to_tree"):
                continue
            row = change.to_tree()
            if (
                row["type"] == "task"
                and row["value"]["id"] == parent
                and row["value"]["state"]["status"] == "terminal"
            ):
                done.set()

    session.subscribe_commits(observe)
    scheduler.open()
    before = counts(root)
    scheduler.resume()
    assert idle.wait(10), (
        [row.to_tree() for row in scheduler.inspect()],
        session.failure,
    )
    rows = [row.to_tree() for row in scheduler.inspect()]
    session.close(timeout=10)
    return before, rows, done.is_set()


@pytest.mark.parametrize("stored", ["safe", "unsafe"])
@pytest.mark.parametrize("current", ["safe", "unsafe"])
def test_actual_stored_current_intersection_keyed_applications_and_parent(
    tmp_path, monkeypatch, stored, current
):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    ready = cut_store(tmp_path, "effect", stored)
    assert counts(tmp_path) == {
        "calls": 1,
        "applications": 1,
        "prepares": 1,
        "providers": 1,
    }
    _, rows, completed = resume(tmp_path, ready["parent"], replay=current)
    first = next(
        r for r in rows if r["kind"] == "pi.tool" and r["input"]["callId"] == "call:0"
    )
    expected = stored == current == "safe"
    assert completed is expected
    if expected:
        assert counts(tmp_path) == {
            "calls": 3,
            "applications": 2,
            "prepares": 2,
            "providers": 2,
        }
        with sqlite3.connect(tmp_path / "effects.sqlite") as db:
            calls = db.execute("SELECT key,args FROM calls ORDER BY rowid").fetchall()
        assert calls[0] == calls[1] and calls[1][0] != calls[2][0]
    else:
        assert (
            first["state"]["status"] == "pending"
            and first["state"]["checkpoint"]["phase"] == "blocked"
        )
        assert counts(tmp_path) == {
            "calls": 1,
            "applications": 1,
            "prepares": 1,
            "providers": 1,
        }
    snapshot = counts(tmp_path)
    for _ in range(2):
        resume(tmp_path, ready["parent"], replay="safe")
        assert counts(tmp_path) == snapshot
    with sqlite3.connect(tmp_path / "session.sqlite") as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)


@pytest.mark.parametrize(
    "cut",
    [
        "pending-before",
        "pending-after",
        "prepare",
        "intent-before",
        "intent-after",
        "invoke-before-effect",
        "effect",
        "receipt-before",
        "receipt-after",
        "result-before",
        "result-after",
    ],
)
def test_actual_unsafe_cuts_and_receipt_reuse_without_tool_resource(
    tmp_path, monkeypatch, cut
):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    ready = cut_store(tmp_path, cut, "unsafe")
    original = counts(tmp_path)
    has_receipt = cut in ("receipt-after", "result-before", "result-after")
    _, rows, completed = resume(tmp_path, ready["parent"], missing=has_receipt)
    first = next(
        r for r in rows if r["kind"] == "pi.tool" and r["input"]["callId"] == "call:0"
    )
    no_intent = cut in ("pending-before", "pending-after", "prepare", "intent-before")
    if no_intent:
        assert completed
        assert counts(tmp_path)["applications"] == 2
    elif has_receipt:
        # First receipt is reused despite registry absence; the later child is
        # blocked as unavailable, so parent cannot continue to another provider.
        assert first["state"]["status"] == "terminal"
        assert counts(tmp_path)["calls"] == original["calls"] == 1
        assert counts(tmp_path)["prepares"] == original["prepares"]
        assert counts(tmp_path)["providers"] == 1 and not completed
    else:
        assert first["state"]["checkpoint"]["phase"] == "blocked" and not completed
        assert counts(tmp_path) == original
        assert counts(tmp_path)["providers"] == 1
    after = counts(tmp_path)
    resume(tmp_path, ready["parent"], replay="safe")
    assert counts(tmp_path) == after


@pytest.mark.parametrize("drift", ["revision", "schema", "missing", "workspace"])
def test_compatible_safe_flag_never_overrides_definition_or_resource_drift(
    tmp_path, monkeypatch, drift
):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    ready = cut_store(tmp_path, "intent-after", "safe")
    options = (
        {"revision": "2"}
        if drift == "revision"
        else {"schema": {"type": "object"}}
        if drift == "schema"
        else {"missing": True}
        if drift == "missing"
        else {"workspace_root": tmp_path / "different"}
    )
    before = counts(tmp_path)
    _, rows, completed = resume(tmp_path, ready["parent"], replay="safe", **options)
    assert not completed and counts(tmp_path) == before
    first = next(
        r for r in rows if r["kind"] == "pi.tool" and r["input"]["callId"] == "call:0"
    )
    assert first["state"]["checkpoint"]["phase"] == "blocked"
    resume(tmp_path, ready["parent"], replay="safe")
    assert (
        counts(tmp_path) == before
    )  # restoring resources does not authorize old blocked work


@pytest.mark.parametrize("corruption", ["missing", "newer", "wrong_type", "wrong_key"])
def test_bad_receipt_blocks_without_reresolving_or_reexecuting(
    tmp_path, monkeypatch, corruption
):
    from pipy_harness.native.durable.tool_execution import RECEIPT_DOC

    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    ready = cut_store(tmp_path, "receipt-after", "safe")
    session = Session(
        open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="open")
    )

    def damage(tx):
        first = next(
            r
            for r in tx.scan_tasks(TaskQuery({}), 100).items
            if r.to_tree()["kind"] == "pi.tool"
            and r.to_tree()["input"]["callId"] == "call:0"
        )
        doc = tx.doc(RECEIPT_DOC, task_id=first.id)
        if corruption == "missing":
            doc.clear()
        elif corruption == "newer":
            doc["version"] = 2
        elif corruption == "wrong_type":
            doc["nativeToolOutput"]["outcome"] = {
                "codec_version": 1,
                "nodes": [{"tag": "scalar", "value": None}],
            }
        else:
            doc["key"] = "another-key"

    session.commit(damage)
    session.close(timeout=10)
    before = counts(tmp_path)
    _, rows, completed = resume(tmp_path, ready["parent"], replay="safe", missing=True)
    assert not completed and counts(tmp_path) == before
    assert (
        next(
            r
            for r in rows
            if r["kind"] == "pi.tool" and r["input"]["callId"] == "call:0"
        )["state"]["checkpoint"]["phase"]
        == "blocked"
    )


@pytest.mark.parametrize("point", ["before", "after"])
def test_final_owned_child_result_and_parent_wake_are_one_real_commit(
    tmp_path, monkeypatch, point
):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    ready = cut_store(tmp_path, "final-result-" + point, "unsafe")
    persisted = json.loads((tmp_path / "cut-persisted.json").read_text())
    parent = next(row for row in persisted["tasks"] if row["id"] == ready["parent"])
    last = next(
        row
        for row in persisted["tasks"]
        if row["kind"] == "pi.tool" and row["input"]["callId"] == "call:1"
    )
    assert (last["state"]["status"], parent["state"]["status"]) == (
        ("running", "waiting") if point == "before" else ("terminal", "pending")
    )
    before = counts(tmp_path)
    assert before == {"calls": 2, "applications": 2, "prepares": 2, "providers": 1}
    _, rows, completed = resume(tmp_path, ready["parent"], missing=True)
    assert completed
    assert counts(tmp_path) == {**before, "providers": 2}
    assert all(row["state"]["status"] == "terminal" for row in rows)
    resume(tmp_path, ready["parent"], missing=True)
    assert counts(tmp_path) == {**before, "providers": 2}


def test_unverified_readonly_idempotent_hints_do_not_authorize_replay(tmp_path):
    ready = cut_store(tmp_path, "effect", "unsafe", hints=True)
    before = counts(tmp_path)
    _, rows, completed = resume(tmp_path, ready["parent"], hints=True)
    assert not completed and counts(tmp_path) == before
    child = next(
        row
        for row in rows
        if row["kind"] == "pi.tool" and row["input"]["callId"] == "call:0"
    )
    assert child["state"]["checkpoint"]["phase"] == "blocked"
    assert child["state"]["checkpoint"]["intent"]["definition"]["replay"] == "unsafe"

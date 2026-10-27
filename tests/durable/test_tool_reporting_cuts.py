"""Real process death before/after progress ACK, receipt2 and terminal commit."""

import hashlib
import json
import signal
import sqlite3
import threading

import pytest
from reporting_cut_support import definitions
from sqlite_child_support import Child
from tool_support import counts

from pipy_harness.native.durable.generation import USAGE_DOC
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import _decode, _encode, open_sqlite_storage

CHILD = r"""
import hashlib,threading
from pathlib import Path
from generation_support import admit
from reporting_cut_support import definitions
from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
root,cut,failed=Path(sys.argv[1]),sys.argv[2],sys.argv[3]=='failed'
generation,tool,snapshot=definitions(root,failed=failed)
session=Session(sqlite.open_sqlite_storage(str(root/'session.sqlite'),mode='create'))
cid,_,parent=admit(session,generation,snapshot)
scheduler=TaskScheduler(session,[generation,tool]);scheduler.open()
receipt_sha=None

def at(connection,point):
    global receipt_sha
    records=[sqlite._decode(row[0]) for row in connection.execute('SELECT record FROM tasks')]
    task=next((row for row in records if row['kind']=='pi.tool'),None)
    if task is None:return
    state=task['state'];phase=state.get('checkpoint',{}).get('phase')
    rows=connection.execute('SELECT r.content FROM document_revisions r JOIN documents d ON d.id=r.document_id WHERE d.kind=?',(json.dumps('pi.tool.receipt'),)).fetchall()
    if rows:
        receipt=sqlite._decode(rows[0][0])
        if receipt and receipt.get('version')==2:receipt_sha=hashlib.sha256(sqlite._encode(receipt)).hexdigest()
    stage='receipt' if phase=='settle' else 'result' if state['status']=='terminal' else None
    if cut.startswith('progress-') and phase=='execute':
        rows=connection.execute('SELECT r.content FROM document_revisions r JOIN documents d ON d.id=r.document_id WHERE d.kind=?',(json.dumps('pi.tool.progress'),)).fetchall()
        if rows and sqlite._decode(rows[0][0])['details']=={'present':True,'value':{'acknowledged':[1]}}:stage='progress'
    if cut==str(stage)+'-'+point:
        say('cut',label=cut,receipt_sha=receipt_sha)
        if not threading.Event().wait(30):raise RuntimeError('missing process-death controller')
sqlite._before_commit=lambda c:at(c,'before')
sqlite._after_commit=lambda c:at(c,'after')
say('ready',parent=parent,cid=cid)
assert command()=='go'
scheduler.resume()
assert command()=='close'
session.close()
"""


def _raw(path):
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        tasks = [_decode(row[0]) for row in db.execute("SELECT record FROM tasks")]
        entries = [_decode(row[0]) for row in db.execute("SELECT record FROM entries")]
        progress = db.execute(
            "SELECT r.content FROM document_revisions r JOIN documents d ON d.id=r.document_id WHERE d.kind=?",
            (json.dumps("pi.tool.progress"),),
        ).fetchall()
    return tasks, entries, [_decode(row[0]) for row in progress]


def _resume(root):
    generation, tool, _ = definitions(root, missing=True)
    session = Session(open_sqlite_storage(str(root / "session.sqlite"), mode="open"))
    scheduler = TaskScheduler(session, [generation, tool])
    idle = threading.Event()
    reserve = scheduler._reserve

    async def observe():
        result = await reserve()
        if result is None:
            idle.set()
        return result

    scheduler._reserve = observe
    try:
        scheduler.open()
        scheduler.resume()
        assert idle.wait(10), session.failure
        return [row.to_tree() for row in scheduler.inspect()]
    finally:
        session.close(timeout=10)
        assert not scheduler._thread.is_alive()


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize(
    "cut",
    [
        "progress-before",
        "progress-after",
        "receipt-before",
        "receipt-after",
        "result-before",
        "result-after",
    ],
)
def test_actual_process_cut_keeps_ack_and_receipt_authorities_distinct(
    tmp_path, failed, cut
):
    with Child(
        CHILD, str(tmp_path), cut, "failed" if failed else "completed", home=tmp_path
    ) as child:
        ready = child.expect("ready")
        child.send("go")
        witness = child.expect("cut")
        assert witness["label"] == cut
        child.kill_group()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        (tmp_path / "actual-process.json").write_text(
            json.dumps(child.evidence(), indent=2)
        )
    path = tmp_path / "session.sqlite"
    tasks, entries, progress = _raw(path)
    assert counts(tmp_path) == {
        "calls": 1,
        "applications": 1,
        "prepares": 1,
        "providers": 1,
    }
    if cut.startswith("progress-"):
        assert len(progress) == 1
        assert progress[0]["details"] == (
            {"present": False}
            if cut.endswith("before")
            else {"present": True, "value": {"acknowledged": [1]}}
        )
    recoverable = cut in ("receipt-after", "result-before", "result-after")
    result = _resume(tmp_path)
    assert counts(tmp_path) == {
        "calls": 1,
        "applications": 1,
        "prepares": 1,
        "providers": 2 if recoverable else 1,
    }
    parent = next(row for row in result if row["id"] == ready["parent"])
    child_task = next(row for row in result if row["kind"] == "pi.tool")
    if recoverable:
        assert parent["state"]["outcome"]["status"] == "completed"
        assert child_task["state"]["outcome"]["status"] == (
            "failed" if failed else "completed"
        )
        _, entries, _ = _raw(path)
        results = [row for row in entries if row["kind"] == "pi.tool-result"]
        assert len(results) == 1
        assert set(results[0]["data"]) == {"nativeToolReceipt"}
        carrier = results[0]["data"]["nativeToolReceipt"]
        assert hashlib.sha256(_encode(carrier)).hexdigest() == witness["receipt_sha"]
        assert carrier["version"] == carrier["nativeToolOutput"]["version"] == 2
        session = Session(open_sqlite_storage(str(path), mode="open"))
        try:
            ledger = session.snapshot(
                USAGE_DOC, conversation_id=ready["cid"]
            ).value.thaw()
            assert len(ledger["settlements"]) == 2
            assert ledger["models"]["openai/gpt-test"]["input"] == 32
        finally:
            session.close()
    else:
        assert parent["state"]["status"] != "terminal"
        assert child_task["state"]["status"] != "terminal"
        assert not any(row["kind"] == "pi.tool-result" for row in _raw(path)[1])
    before = counts(tmp_path)
    _resume(tmp_path)
    assert counts(tmp_path) == before

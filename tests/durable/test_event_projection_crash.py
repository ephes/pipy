"""SIGKILL around committed facts: reopen emits each event once, in order.

The child projects a real SQLite run into a caller-owned event ledger that
records each event and, after each fact, its key. The three cuts of the loop
integration design (section 6): after the generation's settle commit and
before its events, after the first tool result's commit and before its
events, and after the run settled. A fresh process reopens with the ledger's
keys, finishes the run, and the two processes' events together are the loop's
events for the run, each once.
"""

import json
import signal
from pathlib import Path

import pytest
from sqlite_child_support import Child
from test_event_projection import TOOL_RUN

SUPPORT = str(Path(__file__).parent)

LEDGER = r"""
import os
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from tool_support import definitions, counts
from pipy_harness.native.durable.completed_projection import observe_completed
from pipy_harness.native.durable.event_projection import CommittedEventProjection
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
root=Path(sys.argv[1])
def append(line):
    with open(root/'ledger.jsonl','a') as f:
        f.write(json.dumps(line)+'\n'); f.flush(); os.fsync(f.fileno())
class Sink:
    def emit(self, event):
        append({'event': type(event).__name__, 'pid': os.getpid()})
def delivered():
    path=root/'ledger.jsonl'
    lines=[json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
    return [l['key'] for l in lines if 'key' in l]
"""

PROGRAM = (
    LEDGER
    + r"""
from generation_support import admit
cut=sys.argv[3]
session=Session(open_sqlite_storage(str(root/'session.sqlite'),mode='create'))
generation,tool,snapshot=definitions(root,replay='safe')
cid,_,parent=admit(session,generation,snapshot)
(root/'ids.json').write_text(json.dumps({'cid':cid,'parent':parent}))
scheduler=TaskScheduler(session,[generation,tool]); scheduler.open()
kinds={'settle':'pi.assistant','tool':'pi.tool-result'}
def barrier(publication):
    for change in publication.changes:
        if hasattr(change,'to_tree'):
            tree=change.to_tree()
            if tree['type']=='entry' and tree['value']['kind']==kinds.get(cut):
                say('committed',counts=counts(root),seq=publication.seq)
                assert command()=='release'
# Registered before the watch, so the cut holds the publication before the
# projection hears of it.
session.subscribe_commits(barrier)
def on_delivered(key):
    append({'key': key})
    if cut=='settled' and key.startswith('outcome:'):
        say('committed',counts=counts(root))
        assert command()=='release'
async def main():
    watch=await observe_completed(session,cid)
    CommittedEventProjection(cid,Sink(),on_delivered=on_delivered).attach(watch)
    scheduler.resume()
    await asyncio.Event().wait()
import asyncio
asyncio.run(main())
"""
)

REOPEN = (
    LEDGER
    + r"""
import asyncio
ids=json.loads((root/'ids.json').read_text()); cid=ids['cid']
session=Session(open_sqlite_storage(str(root/'session.sqlite'),mode='open'))
generation,tool,_=definitions(root,replay='safe')
scheduler=TaskScheduler(session,[generation,tool]); scheduler.open()
before=delivered()
async def main():
    watch=await observe_completed(session,cid)
    projection=CommittedEventProjection(
        cid,Sink(),delivered=before,on_delivered=lambda key: append({'key':key}))
    projection.attach(watch)
    scheduler.resume()
    for _ in range(200):
        view=await watch.drain()
        if any(o.task_id==ids['parent'] for o in view.outcomes):
            break
        await asyncio.sleep(0.05)
    # A redelivered snapshot emits nothing.
    assert projection.apply(await watch.drain())==()
    watch.stop(); await watch.wait_closed()
asyncio.run(main()); session.close(timeout=10)
say('reopened',counts=counts(root),before=before)
"""
)

EFFECTS = {"calls": 2, "applications": 2, "prepares": 2, "providers": 2}


@pytest.mark.parametrize("cut", ["settle", "tool", "settled"])
def test_kill_after_commit_reopen_emits_each_event_once(tmp_path, cut):
    with Child(PROGRAM, str(tmp_path), SUPPORT, cut, home=tmp_path) as child:
        child.expect("committed")
        child.kill_child_only()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
    lines = [
        json.loads(line)
        for line in (tmp_path / "ledger.jsonl").read_text().splitlines()
    ]
    killed = [line["event"] for line in lines if "event" in line]
    if cut == "settled":
        assert killed == TOOL_RUN
    else:
        # The baseline input is projected before the run resumes; the cut
        # fact's events are not emitted before the kill.
        assert 4 <= len(killed) <= (4 if cut == "settle" else 6)
        assert killed == TOOL_RUN[: len(killed)]
    with Child(REOPEN, str(tmp_path), SUPPORT, home=tmp_path) as child:
        reopened = child.expect("reopened")
        assert child.wait() == 0
        child.wait_stdout_eof()
    assert reopened["counts"] == EFFECTS
    lines = [
        json.loads(line)
        for line in (tmp_path / "ledger.jsonl").read_text().splitlines()
    ]
    events = [line["event"] for line in lines if "event" in line]
    assert events == TOOL_RUN
    keys = [line["key"] for line in lines if "key" in line]
    assert len(keys) == len(set(keys)) == 7
    if cut == "settled":
        assert len(lines) == len(TOOL_RUN) + 7  # the reopen emitted nothing

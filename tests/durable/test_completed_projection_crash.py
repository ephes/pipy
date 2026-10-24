"""SIGKILL committed work before callback and after ambiguous consumer ack."""

import signal
from pathlib import Path

import pytest
from sqlite_child_support import Child

SUPPORT = str(Path(__file__).parent)
PROGRAM = r"""
import asyncio
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from tool_support import definitions, counts
from generation_support import admit
from pipy_harness.native.durable.completed_projection import observe_completed
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
root=Path(sys.argv[1]); cut=sys.argv[3]
session=Session(open_sqlite_storage(str(root/'session.sqlite'),mode='create'))
generation,tool,snapshot=definitions(root,replay='safe')
cid,_,parent=admit(session,generation,snapshot)
(root/'ids.json').write_text(json.dumps({'cid':cid,'parent':parent}))
scheduler=TaskScheduler(session,[generation,tool]); scheduler.open()
def barrier(publication):
    for change in publication.changes:
        if hasattr(change,'to_tree'):
            tree=change.to_tree()
            if (tree['type']=='task' and tree['value']['id']==parent
                and tree['value']['state']['status']=='terminal'):
                say('commit-before-callback',counts=counts(root),seq=publication.seq)
                assert command()=='release'
if cut=='before-callback':
    session.subscribe_commits(barrier)
async def main():
    watch=await observe_completed(session,cid)
    async def consume(view):
        terminal=next((o for o in view.outcomes if o.task_id==parent),None)
        if terminal is None:
            return
        assert terminal.outcome.thaw()['status']=='completed'
        # This is the caller's own keyed consumer ledger, not projection state.
        ledger=root/'consumer.json'
        keys=json.loads(ledger.read_text()) if ledger.exists() else []
        for message in view.messages:
            key=f'{cid}:{message.entry_id}'
            if key not in keys: keys.append(key)
        ledger.write_text(json.dumps(keys))
        say('callback-before-ack',counts=counts(root),keys=keys)
        await asyncio.Event().wait()
    watch.start(consume); scheduler.resume()
    await asyncio.Event().wait()
asyncio.run(main())
"""

REOPEN = r"""
import asyncio
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from tool_support import counts
from pipy_harness.native.durable.completed_projection import observe_completed
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
root=Path(sys.argv[1]); ids=json.loads((root/'ids.json').read_text())
session=Session(open_sqlite_storage(str(root/'session.sqlite'),mode='open'))
before=counts(root)
async def main():
    watch=await observe_completed(session,ids['cid'])
    initial=watch.snapshot
    assert len(initial.messages)==5 and len(initial.rounds)==1
    assert len(initial.outcomes)==3
    assert all(o.outcome.thaw()['status']=='completed' for o in initial.outcomes)
    assert len(initial.usage.thaw()['settlements'])==2
    ledger=root/'consumer.json'
    keys=json.loads(ledger.read_text()) if ledger.exists() else []
    added=[]
    async def consume(view):
        assert view.messages==initial.messages and view.outcomes==initial.outcomes
        for message in view.messages:
            key=f'{ids["cid"]}:{message.entry_id}'
            if key not in keys:
                keys.append(key); added.append(key)
        ledger.write_text(json.dumps(keys))
    watch.start(consume)
    await watch.drain(); await watch.drain()
    assert len(keys)==5 and counts(root)==before
    watch.stop(); await watch.wait_closed()
    return keys,added
keys,added=asyncio.run(main()); session.close(timeout=5)
say('reopened',counts=counts(root),keys=keys,added=added)
"""


@pytest.mark.parametrize("cut", ["before-callback", "ambiguous-ack"])
def test_kill_reopen_two_drains_no_effect_reissue_and_consumer_owned_dedupe(
    tmp_path, cut
):
    with Child(PROGRAM, str(tmp_path), SUPPORT, cut, home=tmp_path) as child:
        observed = child.expect(
            "commit-before-callback"
            if cut == "before-callback"
            else "callback-before-ack"
        )
        assert observed["counts"] == {
            "calls": 2,
            "applications": 2,
            "prepares": 2,
            "providers": 2,
        }
        child.kill_child_only()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
    with Child(REOPEN, str(tmp_path), SUPPORT, home=tmp_path) as child:
        reopened = child.expect("reopened")
        assert reopened["counts"] == observed["counts"]
        assert len(reopened["keys"]) == 5
        assert len(reopened["added"]) == (5 if cut == "before-callback" else 0)
        assert child.wait() == 0
        child.wait_stdout_eof()

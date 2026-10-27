"""Public async factory hard-kill/reopen with independent fake effect ledger."""

import json
import signal
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlite_child_support import Child

from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.tool_output_values import INTERRUPTED_TEXT
from pipy_harness.native.durable.tool_values import receipt

SUPPORT = str(Path(__file__).parent)
PROGRAM = r"""
import asyncio
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from generation_support import SPEC
from tool_support import CountedWire,Keyed,counts,database
from pipy_harness.sdk import DurableProviderProfile,create_durable_product_session
from pipy_harness.native.durable.tool_execution import ToolRegistration
from pipy_harness.native.durable import sqlite,generation
from pipy_harness.native.durable.generation_transport import owned_call
root=Path(sys.argv[1]);cut=sys.argv[3];database(root)
def barrier(stage):
    say(stage,counts=counts(root))
    assert command()=='release'
class Wire(CountedWire):
    def post_json(self,*a,**kw):
        if cut=='provider-inflight':
            # Count a real accepted fake transport invocation independently.
            import sqlite3
            with sqlite3.connect(root/'effects.sqlite') as db: db.execute('INSERT INTO providers VALUES (?)',('inflight',))
            barrier(cut)
        return super().post_json(*a,**kw)
ports=[Keyed(root,name,barrier=barrier,cut='effect' if cut=='effect' and name=='keyed' else '') for name in ('keyed','later')]
registry={p.definition.name:ToolRegistration(p,'fixture.'+p.definition.name,'1') for p in ports}
wire=Wire(root)
profile=DurableProviderProfile(SPEC,auth_path=root/'auth.json',env={},runtime_api_key='fixture-key',http_client=wire)
original=generation.execute_provider
async def response(*a,**kw):
    result=await original(*a,**kw)
    if cut=='response-before-receipt': await owned_call(lambda:barrier(cut))
    return result
generation.execute_provider=response
armed=False
def committed(db):
    if not armed:return
    rows=[sqlite._decode(r[0]) for r in db.execute('SELECT record FROM tasks')]
    for row in rows:
        state=row['state'];phase=state.get('checkpoint',{}).get('phase')
        chosen=(
            cut=='admission' and row['kind']=='pi.generation' and state['status']=='pending' and phase=='prepare'
            or cut=='provider-receipt' and row['kind']=='pi.generation' and state['status']=='running' and phase=='settle'
            or cut=='assistant' and row['kind']=='pi.generation' and state['status']=='waiting'
            or cut=='tool-intent' and row['kind']=='pi.tool' and state['status']=='running' and phase=='execute'
            or cut=='tool-receipt' and row['kind']=='pi.tool' and state['status']=='running' and phase=='settle'
            or cut=='final' and row['kind']=='pi.generation' and state['status']=='terminal')
        if chosen:barrier(cut)
sqlite._after_commit=committed
async def main():
    global armed
    product=await create_durable_product_session(root/'product.sqlite',workspace=root,profile=profile,tools=registry)
    armed=True
    await product.submit('input')
    await product.resume()
    raise AssertionError('required kill barrier absent')
asyncio.run(main())
"""

REOPEN = r"""
import asyncio
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from generation_support import SPEC
from tool_support import CountedWire,Keyed,counts
from pipy_harness.sdk import DurableProviderProfile,DurableProductError,open_durable_product_session
from pipy_harness.native.durable.tool_execution import ToolRegistration
root=Path(sys.argv[1]);cut=sys.argv[3]
ports=[Keyed(root,name) for name in ('keyed','later')]
registry={p.definition.name:ToolRegistration(p,'fixture.'+p.definition.name,'1') for p in ports}
wire=CountedWire(root)
profile=DurableProviderProfile(SPEC,auth_path=root/'auth.json',env={},runtime_api_key='fixture-key',http_client=wire)
async def main():
    before=counts(root)
    product=await open_durable_product_session(root/'product.sqlite',workspace=root,profile=profile,tools=registry)
    assert product._scheduler is None and counts(root)==before
    try:
        try:await product.submit('must never be accepted again')
        except DurableProductError as e:assert e.code=='already_admitted'
        else:raise AssertionError('repeated input accepted')
        result=await product.resume()
        assert result.status=='terminal' and result.inspection.outcome=='completed'
        assert len(result.completed.messages)==5 and len(result.completed.rounds)==1
        assert len(result.completed.usage.thaw()['settlements'])==2
        finished=counts(root)
        again=await product.resume()
        assert again.completed.messages==result.completed.messages and counts(root)==finished
        return before,finished,result.task_id
    finally:await product.close()
before,finished,task=asyncio.run(main())
assert not list(root.rglob('*.jsonl'))
say('reopened',before=before,counts=finished,task=task)
"""


def interrupted_authority(root, cut):
    """Capture the actual killed execute and its independent effect prefix."""
    if cut not in ("tool-intent", "effect"):
        return None
    with closing(sqlite3.connect(root / "product.sqlite")) as db:
        tasks = [
            sqlite._decode(row[0]) for row in db.execute("SELECT record FROM tasks")
        ]
    execute = [
        task
        for task in tasks
        if task["kind"] == "pi.tool"
        and task["state"].get("checkpoint", {}).get("phase") == "execute"
    ]
    assert len(execute) == 1
    child = execute[0]
    intent = child["state"]["checkpoint"]["intent"]
    return {
        "task_id": child["id"],
        "intent": intent,
        "effects": interrupted_effects(root, intent["key"]),
    }


def interrupted_effects(root, key):
    with closing(sqlite3.connect(root / "effects.sqlite")) as db:
        return {
            "calls": db.execute(
                "SELECT name,key,args FROM calls WHERE key=?", (key,)
            ).fetchall(),
            "applications": db.execute(
                "SELECT key,args FROM applications WHERE key=?", (key,)
            ).fetchall(),
            "prepares": db.execute(
                "SELECT name FROM prepares WHERE name='keyed'"
            ).fetchall(),
        }


def assert_interrupted_recovery(root, authority):
    if authority is None:
        return None
    with closing(sqlite3.connect(root / "product.sqlite")) as db:
        tasks = [
            sqlite._decode(row[0]) for row in db.execute("SELECT record FROM tasks")
        ]
        entries = [
            sqlite._decode(row[0]) for row in db.execute("SELECT record FROM entries")
        ]
    child = next(task for task in tasks if task["id"] == authority["task_id"])
    outcome = child["state"]["outcome"]
    assert child["state"]["status"] == "terminal"
    assert outcome["status"] == "failed"
    assert outcome["error"] == {"message": INTERRUPTED_TEXT}
    entry = next(row for row in entries if row["id"] == outcome["result"]["entryId"])
    assert set(entry["data"]) == {"nativeToolReceipt"}
    saved = entry["data"]["nativeToolReceipt"]
    receipt(saved)  # The actual committed carrier, never a reconstructed receipt.
    assert saved["version"] == 2 and saved["intent"] == authority["intent"]
    full = saved["nativeToolOutput"]
    assert full["version"] == 2 and full["ending"] == "interrupted"
    assert full["recovery"]["reason"] == "interrupted"
    decoded = decode_canonical(full["outcome"])
    assert decoded.result.is_error and not decoded.result.terminate
    assert decoded.interruption.value == "settled"
    assert not decoded.cancelled and not decoded.malformed_arguments
    assert decoded.execution_seconds is None
    assert INTERRUPTED_TEXT in decode_canonical(entry["model"][0]).content.value
    assert interrupted_effects(root, authority["intent"]["key"]) == authority["effects"]
    assert len(authority["effects"]["prepares"]) == 1
    return {"authority": authority, "actual_receipt": saved, "outcome": outcome}


@pytest.mark.parametrize(
    "cut",
    [
        "admission",
        "provider-inflight",
        "response-before-receipt",
        "provider-receipt",
        "assistant",
        "tool-intent",
        "effect",
        "tool-receipt",
        "final",
    ],
)
def test_public_hard_kill_exact_reopen_finishes_without_forged_effects(tmp_path, cut):
    with Child(PROGRAM, str(tmp_path), SUPPORT, cut, home=tmp_path) as child:
        before = child.expect(cut)
        child.kill_child_only()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        killed = child.evidence()
    authority = interrupted_authority(tmp_path, cut)
    with Child(REOPEN, str(tmp_path), SUPPORT, cut, home=tmp_path) as child:
        result = child.expect("reopened")
        assert result["before"] == before["counts"]
        assert result["counts"] == {
            "calls": 1 if cut == "tool-intent" else 2,
            "applications": 1 if cut == "tool-intent" else 2,
            "prepares": 2,
            "providers": 3
            if cut in ("provider-inflight", "response-before-receipt")
            else 2,
        }
        assert child.wait() == 0
        child.wait_stdout_eof()
        reopened = child.evidence()
    interrupted = assert_interrupted_recovery(tmp_path, authority)
    (tmp_path / "public-kill-proof.json").write_text(
        json.dumps(
            {
                "cut": cut,
                "before": before,
                "result": result,
                "killed": killed,
                "reopened": reopened,
                "interrupted_recovery": interrupted,
            },
            indent=2,
        )
    )

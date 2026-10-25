"""Public async factory hard-kill/reopen with independent fake effect ledger."""

import json
import signal
from pathlib import Path

import pytest
from sqlite_child_support import Child

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
        if cut in ('tool-intent','effect'):
            assert result.status=='held' and counts(root)==before
            blocked=next(t for t in (await product.inspect()).tasks if t.family=='model_tool' and t.reason=='uncertain')
            await product.authorize_retry(blocked.task_id,nonce='d'*32)
            assert counts(root)==before
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
    with Child(REOPEN, str(tmp_path), SUPPORT, cut, home=tmp_path) as child:
        result = child.expect("reopened")
        assert result["before"] == before["counts"]
        assert result["counts"] == {
            "calls": 3 if cut == "effect" else 2,
            "applications": 3 if cut == "effect" else 2,
            "prepares": 3 if cut in ("effect", "tool-intent") else 2,
            "providers": 3
            if cut in ("provider-inflight", "response-before-receipt")
            else 2,
        }
        assert child.wait() == 0
        child.wait_stdout_eof()
        reopened = child.evidence()
    (tmp_path / "public-kill-proof.json").write_text(
        json.dumps(
            {
                "cut": cut,
                "before": before,
                "result": result,
                "killed": killed,
                "reopened": reopened,
            },
            indent=2,
        )
    )

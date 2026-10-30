"""Real Product/SQLite fixtures; fault injections are labelled at their seam."""

import asyncio
import threading

from generation_support import Wire

from pipy_harness.native.durable.session_core import Session


async def entered(event):
    assert await asyncio.to_thread(event.wait, 5)


async def caller_turn():
    """Yield to already scheduled public callers, not evidence of physical exit."""
    event = asyncio.Event()
    asyncio.get_running_loop().call_soon(event.set)
    await event.wait()


class FirstCommitGate:
    """Hold the real target Session ticket; all other Sessions are forwarded."""

    def __init__(self, patch, session):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.exited = threading.Event()
        self.calls = 0
        actual = Session.commit

        def commit(owner, *args, **kwargs):
            if owner is not session:
                return actual(owner, *args, **kwargs)
            self.calls += 1
            if self.calls != 1:
                return actual(owner, *args, **kwargs)
            self.entered.set()
            try:
                assert self.release.wait(15)
                return actual(owner, *args, **kwargs)
            finally:
                self.exited.set()

        patch.setattr(Session, "commit", commit)


class FinalizerWire(Wire):
    """An actual provider worker and separately held real Python finalizer."""

    def __init__(self):
        super().__init__()
        self.release.clear()
        self.finalizing = threading.Event()
        self.final_release = threading.Event()
        self.exited = threading.Event()

    def post_json(self, *args, **kwargs):
        try:
            return super().post_json(*args, **kwargs)
        finally:
            self.finalizing.set()
            assert self.final_release.wait(15)
            self.exited.set()

    def unblock(self):
        self.release.set()
        self.final_release.set()


# This is the existing HELD_PROVIDER/Child protocol adapted to an already
# submitted Product's retry-task command. The post-result fault is explicitly
# synthetic; provider execution, finalizer, CLI, SQLite lease and joins are real.
RETRY_CLI = r"""
from pathlib import Path
from contextlib import redirect_stdout
from pipy_harness.cli import main
from pipy_harness import durable_cli, durable_product_api
from pipy_harness.native.http import JsonResponse
from pipy_harness.sdk import DurableProductError
from test_product_session import profile
from adjudication_sdk_support import native_origins
event_stream=sys.stdout
def emit(event,**fields):
    event_stream.write(json.dumps({'event':event,'pid':os.getpid(),**fields})+'\n')
    event_stream.flush()
root=Path(sys.argv[1]);target=sys.argv[2];mode=sys.argv[3]
class Held:
    def post_json(self,*args,**kwargs):
        emit('invoke')
        try:
            assert command()=='release-invoke'
            return JsonResponse(200,{'choices':[{'message':{'content':'done'},'finish_reason':'stop'}]})
        finally:
            emit('finalizer')
            assert command()=='release-finalizer'
            emit('worker-exiting')
durable_cli.build_profile=lambda _:profile(root,Held())
actual=durable_product_api.DurableProductSession._wait_result
async def result(self,*args,**kwargs):
    value=await actual(self,*args,**kwargs)
    if mode=='post-result-fault':
        emit('synthetic-post-result-fault')
        raise DurableProductError('incompatible')
    return value
if mode=='post-result-fault':
    durable_product_api.DurableProductSession._wait_result=result
with (root/'retry-output.json').open('w') as output,redirect_stdout(output):
    code=main(['durable','retry-task','--store',str(root/'session.sqlite'),
        '--workspace',str(root),'--provider','openai','--model','gpt-test',
        '--no-tools','--target-task',target,'--format','text'])
emit('finished',code=code,native_origins=native_origins())
raise SystemExit(code)
"""

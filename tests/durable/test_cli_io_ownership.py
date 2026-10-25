"""Advisory readiness races leave real blocked I/O/finalizers physically owned."""

import json
import os
import signal

import pytest
from test_cli_shared_io import Process
from test_tool_lifecycle import independent_opener

RACE = r"""
import sys,os,json,threading,asyncio,select,signal,fcntl
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'src'))
from pipy_harness import cli,durable_cli
from pipy_harness.native.automation.durable_lifetime import SignalOwner
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.http import JsonResponse
from pipy_harness.sdk import DurableProviderProfile
root=Path(sys.argv[2]);direction=sys.argv[3];mode=sys.argv[4]
events=sys.stdout;control=sys.stdin
def say(event,**fields):
 events.write(json.dumps({'event':event,'pid':os.getpid(),**fields})+'\n');events.flush()
entered=threading.Event();returned=threading.Event();finalizing=threading.Event()
release_finalizer=threading.Event();release_flush=threading.Event()
readfd,writefd=os.pipe();read,write,ready=os.read,os.write,select.select
target=readfd if direction=='input' else writefd
if direction=='input':write(writefd,b'seed')
else:
 # Establish/discard kernel write-history before the private race fixture.
 write(writefd,b'warm');assert read(readfd,4)==b'warm'
class Wire:
 def post_json(self,*a,**kw):
  return JsonResponse(200,{'choices':[{'message':{'content':'x'*200000},'finish_reason':'stop'}]})
durable_cli.build_profile=lambda _:DurableProviderProfile(
 NativeModelSpec('openai','test','Test','openai-completions'),auth_path=root/'auth.json',
 env={},runtime_api_key='fixture-key',http_client=Wire())
original_run=durable_cli._run;run_task=[]
async def running(*a,**kw):
 run_task[:]=[asyncio.current_task()]
 return await original_run(*a,**kw)
durable_cli._run=running
original_notify=SignalOwner.notify
def notify(self,number,frame):
 original_notify(self,number,frame)
 say('signal-recorded',number=number,thread=threading.get_ident(),syscall_returned=returned.is_set())
SignalOwner.notify=notify
async def monitor(task):
 while not entered.is_set():await asyncio.sleep(0.001)
 say('loop-responsive',thread=threading.get_ident(),main_thread=threading.main_thread().ident,
     entered=entered.is_set(),returned=returned.is_set())
 if mode=='cancelled':
  task.cancel();say('waiter-cancelled',cancelling=task.cancelling())
  while not finalizing.is_set():await asyncio.sleep(0.001)
  run_task[0].cancel();say('cleanup-waiter-cancelled',cancelling=run_task[0].cancelling())
if direction=='input':
 original=durable_cli.read_input
 async def reading(*a,**kw):
  asyncio.create_task(monitor(asyncio.current_task()))
  return await original(*a,**kw)
 durable_cli.read_input=reading
else:
 original=durable_cli.write_output
 async def writing(*a,**kw):
  asyncio.create_task(monitor(asyncio.current_task()))
  return await original(*a,**kw)
 durable_cli.write_output=writing
raced=False
def readiness(reads,writes,errors,timeout):
 global raced
 observed=ready(reads,writes,errors,timeout)
 selected=observed[0] if direction=='input' else observed[1]
 if direction!='flush' and target in selected and not raced:
  raced=True
  if direction=='input':
   removed=read(readfd,65536);assert removed==b'seed'
   assert not ready([readfd],[],[],0)[0]
   say('readiness-raced',removed_bytes=len(removed))
  else:
   # A false readiness result alone does not prove a small write will block.
   # This private peer fills through actual EAGAIN, restoring its exact flags
   # before returning the stale readiness result to product code.
   flags=fcntl.fcntl(writefd,fcntl.F_GETFL);filled=0
   os.set_blocking(writefd,False)
   try:
    while True:filled+=write(writefd,b'f'*select.PIPE_BUF)
   except BlockingIOError:pass
   finally:fcntl.fcntl(writefd,fcntl.F_SETFL,flags)
   assert fcntl.fcntl(writefd,fcntl.F_GETFL)==flags
   assert not ready([],[writefd],[],0)[1]
   say('readiness-raced',filled_bytes=filled)
  # Actual readiness was true, but a peer has invalidated it before the I/O.
 return observed
select.select=readiness
def actual(fd,value):
 if fd!=target:return (read if direction=='input' else write)(fd,value)
 say('io-entered',thread=threading.get_ident(),main_thread=threading.main_thread().ident);entered.set()
 result=(read if direction=='input' else write)(fd,value)
 returned.set();say('syscall-returned',bytes=len(result) if isinstance(result,bytes) else result)
 try:return result
 finally:
  say('io-finalizer');finalizing.set();release_finalizer.wait()
if direction=='input':
 os.read=actual;sys.stdin=os.fdopen(readfd,'r',closefd=False)
elif direction=='output':
 os.write=actual;sys.stdout=os.fdopen(writefd,'w',closefd=False)
else:
 class Flush:
  def __init__(self):self.stream=(root/'output.json').open('w')
  def fileno(self):return self.stream.fileno()
  def flush(self):
   say('io-entered',thread=threading.get_ident(),main_thread=threading.main_thread().ident);entered.set()
   release_flush.wait();self.stream.flush();returned.set();say('syscall-returned',operation='flush_callback')
   try:return None
   finally:say('io-finalizer');finalizing.set();release_finalizer.wait()
 sys.stdout=Flush()
def commands():
 assert control.readline().strip()=='release-syscall'
 if direction=='input':write(writefd,b'input')
 elif direction=='output':
  drained=read(readfd,65536);assert len(drained)>=select.PIPE_BUF
 else:release_flush.set()
 assert control.readline().strip()=='release-finalizer'
 release_finalizer.set()
coordinator=threading.Thread(target=commands);coordinator.start()
args=['durable','start','--store',str(root/'run.sqlite'),'--workspace',str(root),
 '--provider','openai','--model','test','--no-tools','--format','json']
args+=['--stdin'] if direction=='input' else ['--prompt','input']
code=cli.main(args);sys.stdout=events;sys.stdin=control;coordinator.join()
say('finished',code=code,returned=returned.is_set())
raise SystemExit(code)
"""


@pytest.mark.parametrize("direction", ["input", "output", "flush"])
@pytest.mark.parametrize("mode", ["signal", "cancelled"])
def test_real_blocking_io_and_finalizer_join_before_lease_or_loop_release(
    tmp_path, direction, mode
):
    child = Process(RACE, tmp_path, direction, mode)
    try:
        if direction != "flush":
            child.expect("readiness-raced")
        entered = child.expect("io-entered")
        assert entered["thread"] != entered["main_thread"]
        loop = child.expect("loop-responsive")
        assert (
            loop["thread"] == loop["main_thread"]
            and loop["entered"]
            and not loop["returned"]
        )
        if direction == "flush":
            assert (tmp_path / "output.json").stat().st_size == 0, (
                "Payload preceded the held preflush"
            )
        if mode == "signal":
            os.kill(child.process.pid, signal.SIGTERM)
            assert not child.expect("signal-recorded")["syscall_returned"]
        else:
            assert child.expect("waiter-cancelled")["cancelling"] > 0
        if direction != "input":
            independent_opener(
                tmp_path / "run.sqlite",
                tmp_path,
                "blocked-io",
                "refused",
                invoke_held=True,
            )
        else:
            assert not (tmp_path / "run.sqlite").exists()
        assert child.process.poll() is None
        child.send("release-syscall")
        child.expect("syscall-returned")
        child.expect("io-finalizer")
        if mode == "cancelled":
            assert child.expect("cleanup-waiter-cancelled")["cancelling"] > 0
        if direction != "input":
            independent_opener(
                tmp_path / "run.sqlite", tmp_path, "io-finalizer", "refused"
            )
        else:
            assert not (tmp_path / "run.sqlite").exists()
        assert child.process.poll() is None
        child.send("release-finalizer")
        final = child.expect("finished")
        assert final["returned"] and final["code"] == (143 if mode == "signal" else 1)
        assert child.process.wait(timeout=10) == final["code"]
        child.finish(tmp_path, "io-process")
    finally:
        if child.process.poll() is None:
            child.finish(tmp_path, "failure-cleanup", kill=True)
    if direction != "input":
        independent_opener(tmp_path / "run.sqlite", tmp_path, "io-closed", "opened")
    else:
        assert not (tmp_path / "run.sqlite").exists()
    (tmp_path / "observed.json").write_text(
        json.dumps({"direction": direction, "mode": mode, "events": child.events})
    )

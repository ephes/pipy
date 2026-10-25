"""Actual CLI process signals retain real acquisition/invoke/finalizer owners."""

import json
import os
import signal
import subprocess
import sys

import pytest
from sqlite_child_support import Child
from test_tool_lifecycle import independent_opener

HELD_PROVIDER = r"""
import signal
from pathlib import Path
from contextlib import redirect_stdout
from pipy_harness.cli import main
from pipy_harness import durable_cli
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.http import JsonResponse
from pipy_harness.sdk import DurableProviderProfile
event_stream=sys.stdout
def say(event, **fields):
    event_stream.write(json.dumps({'event':event,'pid':os.getpid(),**fields})+'\n')
    event_stream.flush()
root=Path(sys.argv[1]); path=root/'held.sqlite'
class Wire:
    def post_json(self,*args,**kwargs):
        say('invoke')
        try:
            assert command()=='release-invoke'
            return JsonResponse(200,{'choices':[{'message':{'content':'done'},'finish_reason':'stop'}]})
        finally:
            say('finalizer')
            assert command()=='release-finalizer'
            say('worker-exiting')
def profile(_):
    return DurableProviderProfile(NativeModelSpec('openai','test','Test','openai-completions'),
       auth_path=root/'auth.json',env={},runtime_api_key='fixture-key',http_client=Wire())
durable_cli.build_profile=profile
signals=(signal.SIGTERM,signal.SIGHUP,signal.SIGINT)
prior={s:signal.getsignal(s) for s in signals}
with (root/'output.txt').open('w') as output, redirect_stdout(output):
    code=main(['durable','start','--store',str(path),'--workspace',str(root),
       '--provider','openai','--model','test','--no-tools','--prompt','input','--format','json'])
say('finished',code=code,restored=all(signal.getsignal(s)==v for s,v in prior.items()))
raise SystemExit(code)
"""


WAITING_INPUT = r"""
import signal
from pathlib import Path
from pipy_harness.cli import main
from pipy_harness import durable_cli
root=Path(sys.argv[1]); original=durable_cli.read_input
async def reading(*args,**kwargs):
    print(json.dumps({'event':'waiting-input','pid':os.getpid()}),flush=True)
    return await original(*args,**kwargs)
durable_cli.read_input=reading
code=main(['durable','start','--store',str(root/'absent'/'run.sqlite'),'--workspace',str(root),
 '--provider','fake','--model','fake-native-bootstrap','--stdin'])
print(json.dumps({'event':'finished','code':code}),flush=True)
raise SystemExit(code)
"""


def test_signal_while_pipe_has_not_reached_eof_never_opens_or_admits(tmp_path):
    with Child(WAITING_INPUT, str(tmp_path), home=tmp_path) as child:
        child.expect("waiting-input")
        os.kill(child.pid, signal.SIGTERM)
        assert child.expect("finished")["code"] == 143
        assert child.wait() == 143
        assert not (tmp_path / "absent").exists()
        (tmp_path / "stdin-signal-process.json").write_text(
            json.dumps(child.evidence(), indent=2)
        )


HELD_ACQUISITION = r"""
import signal
from pathlib import Path
from contextlib import redirect_stdout
from pipy_harness.cli import main
from pipy_harness import durable_product_api
event_stream=sys.stdout
def say(event,**fields):
    event_stream.write(json.dumps({'event':event,'pid':os.getpid(),**fields})+'\n');event_stream.flush()
root=Path(sys.argv[1]);original=durable_product_api.open_sqlite_storage
def open_store(*a,**kw):
    backend=original(*a,**kw)
    say('acquisition')
    assert command()=='release-acquisition'
    return backend
durable_product_api.open_sqlite_storage=open_store
with (root/'acquisition-output.txt').open('w') as output,redirect_stdout(output):
    code=main(['durable','start','--store',str(root/'acquisition.sqlite'),'--workspace',str(root),
       '--provider','fake','--model','fake-native-bootstrap','--no-tools','--prompt','input'])
say('finished',code=code)
raise SystemExit(code)
"""

HELD_TOOL = r"""
import signal
from pathlib import Path
from contextlib import redirect_stdout
from pipy_harness.cli import main
from pipy_harness import durable_product_api
from pipy_harness import durable_cli
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.http import JsonResponse
from pipy_harness.native.durable.tool_execution import ToolRegistration
from pipy_harness.native.tools.base import ToolDefinition,ToolExecutionResult
from pipy_harness.sdk import DurableProviderProfile
event_stream=sys.stdout
def say(event,**fields):
    event_stream.write(json.dumps({'event':event,'pid':os.getpid(),**fields})+'\n');event_stream.flush()
root=Path(sys.argv[1]);path=root/'held-tool.sqlite'
class Held:
    definition=ToolDefinition('held','held',{'type':'object'})
    def invoke(self,request,context):
        say('invoke')
        try:
            assert command()=='release-invoke'
            return ToolExecutionResult(request.tool_request_id,'actual',provider_correlation_id=request.provider_correlation_id)
        finally:
            say('finalizer')
            assert command()=='release-finalizer'
            say('worker-exiting')
port=Held()
durable_product_api.builtin_registrations=lambda:{'held':ToolRegistration(port,'fixture.held','1')}
class Wire:
    def post_json(self,*a,**kw):
        return JsonResponse(200,{'choices':[{'message':{'content':'','tool_calls':[
            {'id':'call0','type':'function','function':{'name':'held','arguments':'{}'}}]},'finish_reason':'tool_calls'}]})
def profile(_):
    return DurableProviderProfile(NativeModelSpec('openai','test','Test','openai-completions'),
       auth_path=root/'auth.json',env={},runtime_api_key='fixture-key',http_client=Wire())
durable_cli.build_profile=profile
signals=(signal.SIGTERM,signal.SIGHUP,signal.SIGINT);prior={s:signal.getsignal(s) for s in signals}
with (root/'tool-output.txt').open('w') as output,redirect_stdout(output):
    code=main(['durable','start','--store',str(path),'--workspace',str(root),
       '--provider','openai','--model','test','--prompt','input','--format','json'])
say('finished',code=code,restored=all(signal.getsignal(s)==v for s,v in prior.items()))
raise SystemExit(code)
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


@pytest.mark.parametrize("first_signal", [signal.SIGTERM, signal.SIGHUP, signal.SIGINT])
def test_cli_signal_and_repeated_signal_join_actual_provider_finalizer_and_os_lease(
    tmp_path, first_signal
):
    path = tmp_path / "held.sqlite"
    with Child(HELD_PROVIDER, str(tmp_path), home=tmp_path) as child:
        child.expect("invoke")
        os.kill(child.pid, first_signal)
        independent_opener(path, tmp_path, "signal-invoke", "refused", invoke_held=True)
        assert child.process.poll() is None
        child.send("release-invoke")
        child.expect("finalizer")
        os.kill(child.pid, signal.SIGTERM)
        independent_opener(path, tmp_path, "signal-finalizer", "refused")
        assert child.process.poll() is None
        child.send("release-finalizer")
        child.expect("worker-exiting")
        outcome = child.expect("finished")
        assert outcome["code"] == 128 + first_signal and outcome["restored"]
        assert child.wait() == 128 + first_signal
        child.wait_stdout_eof()
        receipt = child.evidence()
    independent_opener(path, tmp_path, "signal-closed", "opened")
    assert (tmp_path / "output.txt").read_text() == ""
    (tmp_path / "signal-process.json").write_text(json.dumps(receipt, indent=2))


LOOP_CLEANUP = r"""
import asyncio,signal
from pathlib import Path
from contextlib import redirect_stdout
from pipy_harness.cli import main
event_stream=sys.stdout
def say(event,**fields):
    event_stream.write(json.dumps({'event':event,'pid':os.getpid(),**fields})+'\n');event_stream.flush()
root=Path(sys.argv[1]);original=asyncio.Runner.close
def close(runner):
    say('loop-cleanup')
    assert command()=='release-close'
    original(runner)
    say('loop-closed')
asyncio.Runner.close=close
with (root/'completed.json').open('w') as output,redirect_stdout(output):
    code=main(['durable','start','--store',str(root/'done.sqlite'),'--workspace',str(root),
       '--provider','fake','--model','fake-native-bootstrap','--no-tools','--prompt','input','--format','json'])
say('finished',code=code)
raise SystemExit(code)
"""


def test_notification_during_real_loop_cleanup_changes_exit_after_committed_output(
    tmp_path,
):
    with Child(LOOP_CLEANUP, str(tmp_path), home=tmp_path) as child:
        child.expect("loop-cleanup")
        assert (
            json.loads((tmp_path / "completed.json").read_text())["inspection"][
                "outcome"
            ]
            == "completed"
        )
        os.kill(child.pid, signal.SIGHUP)
        child.send("release-close")
        child.expect("loop-closed")
        assert child.expect("finished")["code"] == 128 + signal.SIGHUP
        assert child.wait() == 128 + signal.SIGHUP
        child.wait_stdout_eof()
        (tmp_path / "loop-process.json").write_text(
            json.dumps(child.evidence(), indent=2)
        )


def test_signal_during_real_acquisition_waits_late_product_and_closes_lease(tmp_path):
    path = tmp_path / "acquisition.sqlite"
    with Child(HELD_ACQUISITION, str(tmp_path), home=tmp_path) as child:
        child.expect("acquisition")
        os.kill(child.pid, signal.SIGTERM)
        independent_opener(path, tmp_path, "acquisition-held", "refused")
        assert child.process.poll() is None
        child.send("release-acquisition")
        assert child.expect("finished")["code"] == 128 + signal.SIGTERM
        assert child.wait() == 128 + signal.SIGTERM
        child.wait_stdout_eof()
        (tmp_path / "acquisition-process.json").write_text(
            json.dumps(child.evidence(), indent=2)
        )
    independent_opener(path, tmp_path, "acquisition-closed", "opened")
    assert (tmp_path / "acquisition-output.txt").read_text() == ""
    from pipy_harness.native.durable.inspection import inspect_store

    assert inspect_store(str(path)).tasks == ()


def test_cli_signal_join_actual_noncooperative_tool_executor_and_finalizer(tmp_path):
    path = tmp_path / "held-tool.sqlite"
    with Child(HELD_TOOL, str(tmp_path), home=tmp_path) as child:
        child.expect("invoke")
        os.kill(child.pid, signal.SIGINT)
        independent_opener(path, tmp_path, "tool-invoke", "refused", invoke_held=True)
        assert child.process.poll() is None
        child.send("release-invoke")
        child.expect("finalizer")
        os.kill(child.pid, signal.SIGHUP)
        independent_opener(path, tmp_path, "tool-finalizer", "refused")
        assert child.process.poll() is None
        child.send("release-finalizer")
        child.expect("worker-exiting")
        result = child.expect("finished")
        assert result["code"] == 130 and result["restored"]
        assert child.wait() == 130
        child.wait_stdout_eof()
        (tmp_path / "tool-process.json").write_text(
            json.dumps(child.evidence(), indent=2)
        )
    independent_opener(path, tmp_path, "tool-closed", "opened")
    assert (tmp_path / "tool-output.txt").read_text() == ""


def _start(tmp_path, *source):
    return [
        sys.executable,
        "-m",
        "pipy_harness.cli",
        "durable",
        "start",
        "--store",
        str(tmp_path / "stdin.sqlite"),
        "--workspace",
        str(tmp_path),
        "--provider",
        "fake",
        "--model",
        "fake-native-bootstrap",
        "--no-tools",
        "--format",
        "json",
        *source,
    ]


def test_stdin_pipe_and_regular_file_preserve_utf8_crlf_and_final_newline(tmp_path):
    payload = "Unicode Ä\r\ntrailing\n".encode()
    pipe = subprocess.run(
        _start(tmp_path, "--stdin"), input=payload, capture_output=True, timeout=20
    )
    assert pipe.returncode == 0, pipe.stderr
    assert (
        json.loads(pipe.stdout)["completed"]["messages"][0]["message"]["content"]
        == payload.decode()
    )
    other = tmp_path / "regular"
    other.mkdir()
    source = tmp_path / "input.txt"
    source.write_bytes(payload)
    with source.open("rb") as stream:
        regular = subprocess.run(
            _start(other, "--stdin"), stdin=stream, capture_output=True, timeout=20
        )
    assert regular.returncode == 0, regular.stderr
    assert (
        json.loads(regular.stdout)["completed"]["messages"][0]["message"]["content"]
        == payload.decode()
    )


def test_actual_pty_stdin_refuses_before_creation_prompt_remains_supported(tmp_path):
    master, slave = os.openpty()
    try:
        result = subprocess.run(
            _start(tmp_path, "--stdin"), stdin=slave, capture_output=True, timeout=20
        )
        assert result.returncode == 2 and result.stdout == b""
        assert result.stderr == b"Durable command unavailable: unsupported\n"
        assert not (tmp_path / "stdin.sqlite").exists()
        prompt = subprocess.run(
            _start(tmp_path, "--prompt", "input"),
            stdin=slave,
            capture_output=True,
            timeout=20,
        )
        assert prompt.returncode == 0, prompt.stderr
    finally:
        os.close(master)
        os.close(slave)

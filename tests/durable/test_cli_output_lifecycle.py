"""Backpressure, broken pipes and cancellation join the same real CLI owner."""

import json
import os
import signal

import pytest
from sqlite_child_support import Child
from test_tool_lifecycle import independent_opener

OUTPUT_WORKER = r"""
import asyncio,io,signal,select
from pathlib import Path
from contextlib import redirect_stdout
from pipy_harness.cli import main
from pipy_harness import durable_cli
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.http import JsonResponse
from pipy_harness.sdk import DurableProviderProfile
event_stream=sys.stdout
def say(event,**fields):
    event_stream.write(json.dumps({'event':event,'pid':os.getpid(),**fields})+'\n');event_stream.flush()
root=Path(sys.argv[1]);mode=sys.argv[2];path=root/'output.sqlite'
class Wire:
    def post_json(self,*a,**kw):
        return JsonResponse(200,{'choices':[{'message':{'content':'x'*200000},'finish_reason':'stop'}]})
durable_cli.build_profile=lambda _:DurableProviderProfile(
    NativeModelSpec('openai','test','Test','openai-completions'),auth_path=root/'auth.json',
    env={},runtime_api_key='fixture-key',http_client=Wire())
readfd,writefd=os.pipe();output=os.fdopen(writefd,'w')
if mode=='broken':
    os.close(readfd)
elif mode=='cancelled':
    original=durable_cli.write_output
    async def writing(text,owner):
        task=asyncio.current_task()
        asyncio.get_running_loop().call_soon(task.cancel)
        await original(text,owner)
    durable_cli.write_output=writing
else:
    original=select.select;announced=False
    def readiness(reads,writes,errors,timeout):
        global announced
        result=original(reads,writes,errors,timeout)
        if writefd in writes and writefd not in result[1] and not announced:
            announced=True;say('output-blocked')
        return result
    select.select=readiness
with redirect_stdout(output):
    code=main(['durable','start','--store',str(path),'--workspace',str(root),
        '--provider','openai','--model','test','--no-tools','--prompt','input','--format','json'])
output.close()
say('finished',code=code)
raise SystemExit(code)
"""

BUFFERED_FILE = r"""
from pathlib import Path
from contextlib import redirect_stdout
from pipy_harness.cli import main
root=Path(sys.argv[1]);format=sys.argv[2]
with (root/'buffered-output.txt').open('w') as stream,redirect_stdout(stream):
    stream.write('CALLER-PREFIX\n')
    # Deliberately no tell/seek/flush before the real public CLI call.
    code=main(['durable','start','--store',str(root/'buffered.sqlite'),'--workspace',str(root),
        '--provider','fake','--model','fake-native-bootstrap','--no-tools','--prompt','input','--format',format])
say('finished',code=code)
raise SystemExit(code)
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


@pytest.mark.parametrize(
    "mode,expected",
    [("blocked", 128 + signal.SIGTERM), ("broken", 141), ("cancelled", 1)],
)
def test_real_output_lifetime_and_failure_disposition(tmp_path, mode, expected):
    with Child(OUTPUT_WORKER, str(tmp_path), mode, home=tmp_path) as child:
        if mode == "blocked":
            child.expect("output-blocked")
            independent_opener(
                tmp_path / "output.sqlite", tmp_path, "blocked-output", "refused"
            )
            os.kill(child.pid, signal.SIGTERM)
        outcome = child.expect("finished")
        assert outcome["code"] == expected
        assert child.wait() == expected
        child.wait_stdout_eof()
        (tmp_path / "output-process.json").write_text(
            json.dumps(child.evidence(), indent=2)
        )
    independent_opener(tmp_path / "output.sqlite", tmp_path, "output-closed", "opened")


@pytest.mark.parametrize("format", ["json", "text"])
def test_buffered_file_prefix_precedes_actual_public_cli_payload(tmp_path, format):
    with Child(BUFFERED_FILE, str(tmp_path), format, home=tmp_path) as child:
        assert child.expect("finished")["code"] == 0
        assert child.wait() == 0
        child.wait_stdout_eof()
        (tmp_path / "buffered-process.json").write_text(
            json.dumps(child.evidence(), indent=2)
        )
    value = (tmp_path / "buffered-output.txt").read_text()
    assert value.startswith("CALLER-PREFIX\n"), (
        "Actual durable payload overtook caller's buffered prefix"
    )
    payload = value.removeprefix("CALLER-PREFIX\n")
    if format == "json":
        result = json.loads(payload)
        assert result["inspection"]["outcome"] == "completed"
        assert result["completed"]["messages"][-1]["message"]["role"] == "assistant"
    else:
        assert payload.strip() and "CALLER-PREFIX" not in payload

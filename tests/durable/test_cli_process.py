"""Real CLI dispatch, SIGKILL and exact reopen with independent effect counts."""

import json
import signal

import pytest
from sqlite_child_support import Child
from test_product_process import (
    PROGRAM,
    REOPEN,
    SUPPORT,
    assert_interrupted_recovery,
    interrupted_authority,
)

# Reuse the actual native fake-wire/tools/SQLite cut fixtures, replacing only
# their former SDK entrypoint. Production still executes exclusively in Python.
CLI_PROGRAM = (
    PROGRAM.split("async def main():", 1)[0]
    + r"""
from contextlib import redirect_stdout
from pipy_harness.cli import main as cli_main
from pipy_harness import durable_cli
from pipy_harness import durable_product_api
event_stream=sys.stdout
def say(event,**fields):
    event_stream.write(json.dumps({'event':event,'pid':os.getpid(),**fields})+'\n');event_stream.flush()
durable_cli.build_profile=lambda _:profile
durable_product_api.builtin_registrations=lambda:registry
armed=True
with (root/'killed-cli-output.txt').open('w') as output,redirect_stdout(output):
    code=cli_main(['durable','start','--store',str(root/'product.sqlite'),'--workspace',str(root),
       '--provider','openai','--model','gpt-test','--prompt','input','--format','json'])
raise AssertionError('required kill barrier absent')
"""
)

CLI_REOPEN = (
    REOPEN.split("async def main():", 1)[0]
    + r"""
from contextlib import redirect_stdout
import io
from pipy_harness.cli import main as cli_main
from pipy_harness import durable_cli
from pipy_harness import durable_product_api
durable_cli.build_profile=lambda _:profile
durable_product_api.builtin_registrations=lambda:registry
base=['durable'];common=['--store',str(root/'product.sqlite'),'--workspace',str(root)]
family=['--provider','openai','--model','gpt-test']
def call(action,extra=(),profiled=True):
    output=io.StringIO()
    with redirect_stdout(output):
        code=cli_main([*base,action,*common,*(family if profiled else []),'--format','json',*extra])
    return code,json.loads(output.getvalue())
before=counts(root)
code,first=call('resume')
assert code==0 and first['inspection']['outcome']=='completed'
assert len(first['completed']['messages'])==5 and len(first['completed']['rounds'])==1
assert len(first['completed']['usage']['settlements'])==2
finished=counts(root)
code,again=call('resume',profiled=False)
assert code==0 and again['completed']['messages']==first['completed']['messages'] and counts(root)==finished
assert not list(root.rglob('*.jsonl'))
say('reopened',before=before,counts=finished,task=first['task_id'],output=first,again=again)
"""
)


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
def test_actual_cli_hard_kill_reopen_recovery_and_reuse(tmp_path, cut):
    with Child(CLI_PROGRAM, str(tmp_path), SUPPORT, cut, home=tmp_path) as child:
        before = child.expect(cut)
        child.kill_child_only()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        killed = child.evidence()
    assert (tmp_path / "killed-cli-output.txt").read_text() == ""
    authority = interrupted_authority(tmp_path, cut)
    with Child(CLI_REOPEN, str(tmp_path), SUPPORT, cut, home=tmp_path) as child:
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
    (tmp_path / "cli-kill-proof.json").write_text(
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

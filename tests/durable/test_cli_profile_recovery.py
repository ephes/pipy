"""CLI current first-prepare authority versus saved exact request/price identity."""

import json
import signal
import sqlite3
from pathlib import Path

import pytest
from cli_process_support import run_cli
from sqlite_child_support import Child

from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.durable.codec import OwnedJson

SUPPORT = str(Path(__file__).parent)
PREFACE = r"""
from pathlib import Path
from contextlib import redirect_stdout
sys.path.insert(0,sys.argv[2])
from generation_support import Wire as BaseWire
from pipy_harness.cli import main as cli_main
from pipy_harness import durable_cli
from pipy_harness.native.durable import sqlite
import sqlite3
root=Path(sys.argv[1]);cut=sys.argv[3]
os.environ['PIPY_CONFIG_HOME']=str(root/'config')
os.environ['PIPY_AUTH_DIR']=str(root/'auth')
if cut=='prepared_cache' and sys.argv[4]=='reopen':os.environ['PIPY_CACHE_RETENTION']='long'
for name in ('config','auth'):(root/name).mkdir(exist_ok=True)
class Wire(BaseWire):
    def post_json(self,url,**kwargs):
        with sqlite3.connect(root/'wire.sqlite') as db:
            db.execute('INSERT INTO calls VALUES (?,?,?)',(url,json.dumps(dict(kwargs['headers'])),json.dumps(kwargs['body'])))
        return super().post_json(url,**kwargs)
wire=Wire();original=durable_cli.DurableProviderProfile
durable_cli.DurableProviderProfile=lambda *a,**kw:original(*a,**kw,http_client=wire)
event_stream=sys.stdout
def say(event,**fields):
    event_stream.write(json.dumps({'event':event,'pid':os.getpid(),**fields})+'\n');event_stream.flush()
common=['--store',str(root/'profile.sqlite'),'--workspace',str(root),'--format','json']
family=['--provider','openai','--model','gpt-test','--no-tools','--thinking','off']
"""

INITIAL = (
    PREFACE
    + r"""
def after(db):
    for (raw,) in db.execute('SELECT record FROM tasks'):
        state=sqlite._decode(raw)['state'];phase=state.get('checkpoint',{}).get('phase')
        if (cut=='unprepared_target' and state['status']=='pending' and phase=='prepare'
            or cut!='unprepared_target' and state['status']=='running' and phase=='request'):
            say('cut',phase=phase)
            assert command()=='release'
sqlite._after_commit=after
with (root/'before-output.txt').open('w') as output,redirect_stdout(output):
    cli_main(['durable','start',*common,*family,'--prompt','input'])
raise AssertionError('required request cut absent')
"""
)

REOPEN = (
    PREFACE
    + r"""
with (root/'resumed-output.json').open('w') as output,redirect_stdout(output):
    code=cli_main(['durable','resume',*common,*family])
say('finished',code=code,calls=len(wire.calls))
raise SystemExit(code)
"""
)


@pytest.mark.parametrize(
    "cut",
    [
        "unprepared_target",
        "prepared_target",
        "prepared_rotation",
        "prepared_header",
        "prepared_cache",
        "prepared_price",
    ],
)
def test_actual_cli_profile_reopen_drift_rotation_and_saved_price(tmp_path, cut):
    (tmp_path / "config").mkdir()
    (tmp_path / "auth").mkdir()
    api = "openai-responses" if cut == "prepared_cache" else "openai-completions"
    config = {
        "providers": {
            "openai": {
                "api": api,
                "baseUrl": "https://wire.invalid/original-private-target",
                "headers": {"X-Private": "original-private-header"},
                "models": [
                    {
                        "id": "gpt-test",
                        "name": "Test",
                        "cost": {
                            "input": 1,
                            "output": 2,
                            "cacheRead": 3,
                            "cacheWrite": 4,
                        },
                    }
                ],
            }
        }
    }
    file = tmp_path / "config" / "models.json"
    file.write_text(json.dumps(config))
    auth = AuthStore(tmp_path / "auth" / "auth.json")
    auth.set("openai", {"type": "api_key", "key": "old-key"})
    with sqlite3.connect(tmp_path / "wire.sqlite") as db:
        db.execute("CREATE TABLE calls(url TEXT,headers TEXT,body TEXT)")
    with Child(INITIAL, str(tmp_path), SUPPORT, cut, "initial", home=tmp_path) as child:
        observed = child.expect("cut")
        assert observed["phase"] == (
            "prepare" if cut == "unprepared_target" else "request"
        )
        child.kill_child_only()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        killed = child.evidence()
    assert (tmp_path / "before-output.txt").read_text() == ""
    with sqlite3.connect(tmp_path / "wire.sqlite") as db:
        assert db.execute("SELECT count(*) FROM calls").fetchone()[0] == 0
    provider = config["providers"]["openai"]
    if cut in ("unprepared_target", "prepared_target"):
        provider["baseUrl"] = "https://wire.invalid/current-private-target"
    elif cut == "prepared_rotation":
        auth.set("openai", {"type": "api_key", "key": "new-key"})
    elif cut == "prepared_header":
        provider["headers"] = {"X-Private": "changed-private-header"}
    elif cut == "prepared_price":
        provider["models"][0]["cost"] = {
            "input": 99,
            "output": 99,
            "cacheRead": 99,
            "cacheWrite": 99,
        }
    file.write_text(json.dumps(config))
    blocked = cut in ("prepared_target", "prepared_header", "prepared_cache")
    with Child(REOPEN, str(tmp_path), SUPPORT, cut, "reopen", home=tmp_path) as child:
        outcome = child.expect("finished")
        assert outcome["code"] == (1 if blocked else 0)
        assert outcome["calls"] == (0 if blocked else 1)
        assert child.wait() == outcome["code"]
        child.wait_stdout_eof()
        reopened = child.evidence()
    raw = (tmp_path / "resumed-output.json").read_text()
    output = json.loads(raw)
    assert output["inspection"]["outcome"] == ("faulted" if blocked else "completed")
    with sqlite3.connect(tmp_path / "wire.sqlite") as db:
        calls = db.execute("SELECT url,headers,body FROM calls").fetchall()
    if not blocked:
        assert len(calls) == 1
        assert (
            "current-private-target"
            if cut == "unprepared_target"
            else "original-private-target"
        ) in calls[0][0]
        key = "new-key" if cut == "prepared_rotation" else "old-key"
        assert json.loads(calls[0][1])["Authorization"] == "Bearer " + key
        assert json.loads(calls[0][1])["X-Private"] == "original-private-header"
        if cut == "prepared_price":
            cost = output["completed"]["messages"][-1]["message"]["usage"]["cost"]
            assert cost == pytest.approx(
                {
                    "input": 16 / 1e6,
                    "output": 20 / 1e6,
                    "cacheRead": 12 / 1e6,
                    "cacheWrite": 0.0,
                    "total": 48 / 1e6,
                },
                rel=1e-12,
            )
    for sentinel in (
        "original-private-target",
        "current-private-target",
        "original-private-header",
        "changed-private-header",
        "old-key",
        "new-key",
    ):
        assert sentinel not in raw and sentinel.encode().hex() not in raw
    passive = run_cli(tmp_path, "passive-terminal", ["resume", *common_args(tmp_path)])
    assert passive.returncode == (1 if blocked else 0)
    assert OwnedJson(json.loads(passive.stdout)["completed"]["messages"]) == OwnedJson(
        output["completed"]["messages"]
    )
    with sqlite3.connect(tmp_path / "wire.sqlite") as db:
        assert db.execute("SELECT count(*) FROM calls").fetchone()[0] == len(calls)
    (tmp_path / "profile-recovery-proof.json").write_text(
        json.dumps(
            {
                "cut": cut,
                "initial": observed,
                "killed": killed,
                "reopened": reopened,
                "outcome": outcome,
            },
            indent=2,
        )
    )


def common_args(root):
    return [
        "--store",
        str(root / "profile.sqlite"),
        "--workspace",
        str(root),
        "--format",
        "json",
    ]

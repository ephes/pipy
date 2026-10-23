"""Actual process cuts across private capture and canonical phase commits."""

import json
import signal
import threading
import uuid

import pytest
from sqlite_child_support import Child
from test_canonical_codec import PROVIDER, SNAPSHOT
from test_checkpoint_codec import IDENTITY, SPEC, abort, frame

from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.durable.canonical_codec import encode_canonical
from pipy_harness.native.durable.checkpoint_codec import (
    commit_checkpoint,
    decode_checkpoint,
    define_canonical_task,
)
from pipy_harness.native.durable.provider_checkpoint import prepare_provider_settings
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage

CHILD = r"""
import threading, uuid
from pathlib import Path
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.checkpoint_codec import encode_checkpoint, commit_checkpoint, define_canonical_task
from pipy_harness.native.durable.provider_checkpoint import prepare_provider_settings
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
path, cut, seed_path, auth_path, effects = sys.argv[1:]
seed = json.loads(Path(seed_path).read_text())
snapshot = decode_canonical(seed["snapshot"])
outcome = ProviderTurnOutcome(result=decode_canonical(seed["result"]))
spec = NativeModelSpec("provider", "model", "Model", "openai-completions")
identity = seed["identity"]

def barrier(label):
    say("cut", label=label)
    if not threading.Event().wait(60): raise RuntimeError("missing parent kill")

def packet(phase, role, payload, settings=None):
    return encode_checkpoint(name="canonical", version=-1.5, phase=phase, role=role, identity=identity, payload=payload, settings=settings)

async def abort(*args): raise AssertionError("abort not dispatched")

async def prepare(value, runtime, context):
    material = await runtime.memo("material", uuid.uuid4().hex)
    settings = prepare_provider_settings(snapshot, spec, auth_path=Path(auth_path), env={}, runtime_api_key="fake-key", material_id=material)
    if cut == "material-before": barrier(cut)
    await commit_checkpoint(runtime, packet("request", "request", snapshot, settings))

async def request(value, runtime, context):
    with open(effects, "a") as handle:
        handle.write("request\n"); handle.flush(); os.fsync(handle.fileno())
    if cut == "response-before": barrier(cut)
    await commit_checkpoint(runtime, packet("result", "provider_outcome", outcome, value.settings))

async def result(value, runtime, context):
    await commit_checkpoint(runtime, packet("result", "provider_outcome", value.payload, value.settings), terminal=True)
    say("done")

definition = define_canonical_task("canonical", -1.5, initial_phase="prepare", phases={"prepare":("prepare",prepare),"request":("request",request),"result":("provider_outcome",result)}, abort=abort)
session = Session(open_sqlite_storage(path, mode="create"))
conversation = session.commit(lambda tx: tx.create_conversation({"kind":"ownerless"})).id
id = session.commit(lambda tx: tx.create_task(definition, packet("prepare","prepare",None).to_tree(), ownership={"kind":"conversation"}, conversation_id=conversation))
scheduler = TaskScheduler(session,[definition]); scheduler.open()

def at(connection, point):
    row = sqlite._decode(connection.execute("SELECT record FROM tasks WHERE id=?",(id,)).fetchone()[0])
    state = row["state"]
    if state["status"] == "terminal": stage = "terminal"
    elif state["checkpoint"]["role"] == "request": stage = "request"
    elif state["checkpoint"]["role"] == "provider_outcome": stage = "outcome"
    else: return
    if cut == stage + "-" + point: barrier(cut)
sqlite._before_commit = lambda connection: at(connection,"before")
sqlite._after_commit = lambda connection: at(connection,"after")
say("ready", id=id)
assert command() == "go"
scheduler.resume()
assert command() == "close"
session.close()
"""


@pytest.mark.parametrize(
    "cut",
    [
        "material-before",
        "request-before",
        "request-after",
        "response-before",
        "outcome-before",
        "outcome-after",
        "terminal-before",
        "terminal-after",
    ],
)
def test_real_kill_adoption_checkpoint_outcome_ack_recovery(tmp_path, cut):
    path, auth, effects = (
        tmp_path / "session.sqlite",
        tmp_path / "auth.json",
        tmp_path / "effects.txt",
    )
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            {
                "snapshot": encode_canonical(SNAPSHOT),
                "result": encode_canonical(PROVIDER),
                "identity": IDENTITY,
            }
        )
    )
    with Child(
        CHILD, str(path), cut, str(seed), str(auth), str(effects), home=tmp_path
    ) as child:
        child.expect("ready")
        child.send("go")
        assert child.expect("cut")["label"] == cut
        child.kill_group()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        (tmp_path / "process.json").write_text(json.dumps(child.evidence(), indent=2))
    before = effects.read_text().splitlines() if effects.exists() else []
    assert before == (
        []
        if cut in ("material-before", "request-before", "request-after")
        else ["request"]
    )
    assert (tmp_path / "auth.json.request-material").exists()
    finished = threading.Event()
    seen = []

    async def prepare(value, runtime, context):
        material = await runtime.memo("material", uuid.uuid4().hex)
        saved = prepare_provider_settings(
            SNAPSHOT,
            SPEC,
            auth_path=auth,
            env={},
            runtime_api_key="fake-key",
            material_id=material,
        )
        await commit_checkpoint(runtime, frame("request", "request", SNAPSHOT, saved))

    async def request(value, runtime, context):
        assert encode_canonical(value.payload) == encode_canonical(SNAPSHOT)
        seen.append("request")
        await commit_checkpoint(
            runtime,
            frame(
                "result",
                "provider_outcome",
                ProviderTurnOutcome(result=PROVIDER),
                value.settings,
            ),
        )

    async def result(value, runtime, context):
        assert encode_canonical(value.payload) == encode_canonical(
            ProviderTurnOutcome(result=PROVIDER)
        )
        await commit_checkpoint(
            runtime,
            frame("result", "provider_outcome", value.payload, value.settings),
            terminal=True,
        )
        finished.set()

    definition = define_canonical_task(
        "canonical",
        -1.5,
        initial_phase="prepare",
        phases={
            "prepare": ("prepare", prepare),
            "request": ("request", request),
            "result": ("provider_outcome", result),
        },
        abort=abort,
    )
    session = Session(open_sqlite_storage(str(path), mode="open"))
    try:
        scheduler = TaskScheduler(session, [definition])
        scheduler.open()
        assert seen == []
        terminal = scheduler.inspect()[0].to_tree()["state"]["status"] == "terminal"
        scheduler.resume()
        if not terminal:
            assert finished.wait(5)
        receipt = scheduler.inspect()[0].to_tree()["state"]["outcome"]["result"]
        decoded = decode_checkpoint(
            receipt,
            name="canonical",
            version=-1.5,
            phase="result",
            role="provider_outcome",
        )
        assert encode_canonical(decoded.payload) == encode_canonical(
            ProviderTurnOutcome(result=PROVIDER)
        )
        assert seen == (
            []
            if cut in ("outcome-after", "terminal-before", "terminal-after")
            else ["request"]
        )
    finally:
        session.close(timeout=5)
    public = path.read_bytes()
    for secret in (b"fake-key", b"auth.json.request-material"):
        assert secret not in public

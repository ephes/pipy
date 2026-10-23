"""Real process death across actual generation request, receipt and settlement."""

import json
import signal
import sqlite3
from pathlib import Path

import pytest
from generation_support import Wire, completion, definition
from sqlite_child_support import Child

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentUserMessage
from pipy_harness.native.agent.usage import AgentTokenPricing
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.generation import USAGE_DOC, message_entry
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import EntryQuery

CHILD = r"""
import threading
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from generation_support import Wire, admit, definition, seed
from pipy_harness.native.agent.usage import AgentTokenPricing
from pipy_harness.native.durable import generation, sqlite
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
root, cut, tools = Path(sys.argv[2]), sys.argv[3], sys.argv[4] == "yes"
effects = root / "calls.jsonl"

def barrier(label):
    say("cut", label=label)
    if not threading.Event().wait(60): raise RuntimeError("missing parent kill")

class Counted(Wire):
    def post_json(self, url, **kwargs):
        with effects.open("a") as stream:
            stream.write(json.dumps(kwargs["body"]) + "\n"); stream.flush(); os.fsync(stream.fileno())
        if cut == "provider-inflight": barrier(cut)
        return super().post_json(url, **kwargs)

wire = Counted(("read", "read", "unavailable") if tools else ())
task = definition(root, wire, pricing=AgentTokenPricing(1,2,3,4))
session = Session(open_sqlite_storage(str(root / "session.sqlite"), mode="create"))
conversation, cutoff, task_id = admit(session, task, seed(root, ("read",)))
scheduler = TaskScheduler(session, [task]); scheduler.open()

def at(connection, point):
    row = sqlite._decode(connection.execute("SELECT record FROM tasks WHERE id=?", (task_id,)).fetchone()[0])
    state = row["state"]
    if state["status"] in ("terminal", "waiting"): stage = "settlement"
    else: stage = {"request":"request", "settle":"receipt"}.get(state["checkpoint"]["phase"])
    if stage and cut == stage + "-" + point: barrier(cut)

sqlite._before_commit = lambda connection: at(connection,"before")
sqlite._after_commit = lambda connection: at(connection,"after")
assemble = generation._assistant
def after_response(*args, **kwargs):
    if cut == "response-before": barrier(cut)
    return assemble(*args, **kwargs)
generation._assistant = after_response
say("ready", conversation=conversation, cutoff=cutoff, task_id=task_id)
assert command() == "go"
scheduler.resume()
assert command() == "close"
session.close()
"""

CUTS = (
    "request-before",
    "request-after",
    "provider-inflight",
    "response-before",
    "receipt-before",
    "receipt-after",
    "settlement-before",
    "settlement-after",
)


@pytest.mark.parametrize("tools", [False, True])
@pytest.mark.parametrize("cut", CUTS)
def test_real_sigkill_generation_reuses_receipt_and_atomic_settlement(
    tmp_path, cut, tools, monkeypatch
):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    path = tmp_path / "session.sqlite"
    effects = tmp_path / "calls.jsonl"
    with Child(
        CHILD,
        str(Path(__file__).parent),
        str(tmp_path),
        cut,
        "yes" if tools else "no",
        home=tmp_path,
    ) as child:
        ready = child.expect("ready")
        child.send("go")
        assert child.expect("cut")["label"] == cut
        child.kill_group()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        (tmp_path / "process.json").write_text(json.dumps(child.evidence(), indent=2))
    original = effects.read_text().splitlines() if effects.exists() else []
    assert len(original) == (0 if cut.startswith("request-") else 1)

    class Counted(Wire):
        def post_json(self, url, **kwargs):
            import os

            with effects.open("a") as stream:
                stream.write(json.dumps(kwargs["body"]) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            return super().post_json(url, **kwargs)

    wire = Counted(("read", "read", "unavailable") if tools else ())
    task = definition(
        tmp_path,
        wire,
        pricing=AgentTokenPricing(1, 2, 3, 4)
        if cut == "request-before"
        else AgentTokenPricing(100, 200, 300, 400),
    )
    session = Session(open_sqlite_storage(str(path), mode="open"))
    try:
        scheduler = TaskScheduler(session, [task])
        scheduler.open()
        assert len(effects.read_text().splitlines() if effects.exists() else []) == len(
            original
        )
        # This new committed input and the new prices cannot enter a recovered
        # request or receipt. Before request COMMIT, prepare may use new history;
        # keep its already-protected material binding compatible in that case.
        latest = session.commit(
            lambda tx: tx.append_entry(
                ready["conversation"],
                message_entry(AgentUserMessage(ProductContent("later"))),
            )
        )
        parent = next(
            r.to_tree() for r in scheduler.inspect() if r.id == ready["task_id"]
        )
        done, _ = completion(session, ready["task_id"])
        scheduler.resume()
        if parent["state"]["status"] not in ("terminal", "waiting"):
            assert done.wait(10)
        final = [r.to_tree() for r in scheduler.inspect()]
        entries = session.commit(
            lambda tx: tx.scan_entries(
                EntryQuery({"conversationId": ready["conversation"]}), 100
            )
        ).items
        assistants = [
            e.to_tree() for e in entries if e.to_tree()["kind"] == "pi.assistant"
        ]
        assert len(assistants) == 1
        entry = assistants[0]
        saved = decode_canonical(entry["data"]["request"])
        receipt = entry["data"]["receipt"]
        assert receipt["identity"]["cutoff"] == (
            latest.id if cut == "request-before" else ready["cutoff"]
        )
        assert [m.content.value for m in saved.request.messages] == (
            ["input", "later"] if cut == "request-before" else ["input"]
        )
        ledger = session.snapshot(
            USAGE_DOC, conversation_id=ready["conversation"]
        ).value.thaw()
        assert list(ledger["settlements"].values()) == [entry["id"]]
        assistant = decode_canonical(entry["model"][0])
        assert assistant.usage.cost.total == pytest.approx((16 + 20 + 12) / 1e6)
        assert len([r for r in final if r["kind"] == "pi.tool"]) == (2 if tools else 0)
    finally:
        session.close(timeout=10)
    attempts = effects.read_text().splitlines()
    assert len(attempts) == (
        2 if cut in ("provider-inflight", "response-before", "receipt-before") else 1
    )
    if len(attempts) == 2:
        assert json.loads(attempts[0]) == json.loads(attempts[1])
    for _ in range(2):
        reopened = Session(open_sqlite_storage(str(path), mode="open"))
        try:
            repeated = TaskScheduler(reopened, [task])
            repeated.open()
            repeated.resume()
            assert [r.to_tree() for r in repeated.inspect()] == final
            assert (
                reopened.snapshot(
                    USAGE_DOC, conversation_id=ready["conversation"]
                ).value.thaw()
                == ledger
            )
        finally:
            reopened.close(timeout=10)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert effects.read_text().splitlines() == attempts

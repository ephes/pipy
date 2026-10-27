"""Real operator retries: new authority, preserved uncertainty and atomic slots."""

import json
import threading
from dataclasses import replace

import pytest
from generation_support import Wire, admit, definition, seed
from tool_support import (
    CountedWire,
    InfrastructureFailureFirst,
    Keyed,
    counts,
    database,
    reporting_faults,
)

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.completed_values import capture as capture_completed
from pipy_harness.native.durable.generation import ROUND_DOC, USAGE_DOC
from pipy_harness.native.durable.inspection import inspect_task
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.resolution import AUDIT
from pipy_harness.native.durable.resolution_values import ResolutionError
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import EntryQuery
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.native.tools.base import ToolDefinition


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))


def make_definitions(root, *, port_type=InfrastructureFailureFirst):
    database(root)
    ports = [port_type(root, name) for name in ("keyed", "later")]
    tool = define_tool_task(
        {
            p.definition.name: ToolRegistration(p, "fixture." + p.definition.name, "1")
            for p in ports
        },
        workspace_root=root,
    )
    generation = definition(root, CountedWire(root))
    snapshot = snapshot_provider_request(
        replace(
            seed(root).request,
            available_tools=tuple(
                ToolDefinition(
                    p.definition.name,
                    p.definition.description,
                    p.definition.input_schema,
                )
                for p in ports
            ),
        )
    )
    return generation, tool, snapshot


def setup(root, backend, *, port_type=InfrastructureFailureFirst):
    generation, tool, snapshot = make_definitions(root, port_type=port_type)
    session = Session(
        MemoryStorage()
        if backend == "memory"
        else open_sqlite_storage(str(root / "session.sqlite"), mode="create")
    )
    try:
        cid, _, parent = admit(session, generation, snapshot)
        scheduler = TaskScheduler(session, [generation, tool])
        idle = threading.Event()
        reserve = scheduler._reserve

        async def observed():
            result = await reserve()
            if result is None:
                idle.set()
            return result

        scheduler._reserve = observed
        scheduler.open()
        scheduler.resume()
        assert idle.wait(10), session.failure
        rows = [r.to_tree() for r in scheduler.inspect()]
        child = next(
            r
            for r in rows
            if r["kind"] == "pi.tool" and r["input"]["callId"] == "call:0"
        )
        assert (
            child["state"]["status"] == "pending"
            and child["state"]["checkpoint"]["phase"] == "blocked"
        )
        if port_type is InfrastructureFailureFirst:
            assert reporting_faults(root) == [
                ("keyed", child["state"]["checkpoint"]["intent"]["key"], 1, 1)
            ]
            assert counts(root) == {
                "calls": 1,
                "applications": 1,
                "prepares": 1,
                "providers": 1,
            }
            entries = session.read_on_line(
                lambda: (
                    session._owner.storage.scan_entries(
                        EntryQuery({"conversationId": cid}), 100
                    ).items
                )
            )
            assert not any(
                entry.to_tree()["kind"] == "pi.tool-result" for entry in entries
            )
            from pipy_harness.native.durable.inspection_read import document_exists
            from pipy_harness.native.durable.tool_execution import RECEIPT_DOC

            assert not session.read_on_line(
                lambda: document_exists(
                    session._owner.storage, RECEIPT_DOC, cid, child["id"]
                )
            )
        return session, scheduler, cid, parent, child, idle
    except BaseException:
        session.close(timeout=10)
        raise


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_new_authorized_owned_child_atomic_round_and_private_old_outcome(
    tmp_path, backend
):
    session, scheduler, cid, parent, old, idle = setup(tmp_path, backend)
    try:
        assert counts(tmp_path) == {
            "calls": 1,
            "applications": 1,
            "prepares": 1,
            "providers": 1,
        }
        initial_round = session.snapshot(ROUND_DOC, task_id=parent).value.thaw()
        initial_usage = session.snapshot(USAGE_DOC, conversation_id=cid).value.thaw()
        target = scheduler.resolution_target(old["id"])
        publications = []
        done = threading.Event()

        def observe(pub):
            changes = [c.to_tree() for c in pub.changes if hasattr(c, "to_tree")]
            if any(
                c["type"] == "task" and c["value"]["id"] == old["id"] for c in changes
            ):
                publications.append(changes)
            if any(
                c["type"] == "task"
                and c["value"]["id"] == parent
                and c["value"]["state"]["status"] == "terminal"
                for c in changes
            ):
                done.set()

        session.subscribe_commits(observe)
        nonce = "a" * 32
        result = scheduler.resolve(target, nonce=nonce)
        assert result.source_task_id == old["id"] and result.new_task_id != old["id"]
        assert scheduler.resolve(target, nonce=nonce) == result
        assert done.wait(10), (
            session.failure,
            [r.to_tree() for r in scheduler.inspect()],
        )
        assert len(publications) == 1
        changed = {
            c["value"]["id"]: c["value"] for c in publications[0] if c["type"] == "task"
        }
        assert changed[old["id"]]["state"] == {
            "status": "terminal",
            "outcome": {"status": "aborted", "reason": "operator_retry"},
        }
        assert changed[result.new_task_id]["owner"] == parent
        assert changed[parent]["state"]["on"] == [
            result.new_task_id,
            initial_round["tools"][1],
        ]
        audit = session.snapshot(AUDIT, conversation_id=cid, key=nonce).value.thaw()
        assert audit["source"] == old
        assert audit["new_task_id"] == result.new_task_id
        assert counts(tmp_path) == {
            "calls": 3,
            "applications": 3,
            "prepares": 3,
            "providers": 2,
        }
        with __import__("sqlite3").connect(tmp_path / "effects.sqlite") as db:
            keys = [r[0] for r in db.execute("SELECT key FROM calls")]
        assert (
            len(set(keys)) == 3
            and old["state"]["checkpoint"]["intent"]["key"] == keys[0]
        )
        ledger = session.snapshot(USAGE_DOC, conversation_id=cid).value.thaw()
        assert (
            len(initial_usage["settlements"]) == 1 and len(ledger["settlements"]) == 2
        )
        assert ledger["models"]["openai/gpt-test"]["input"] == 32
        assert ledger["models"]["openai/gpt-test"]["reasoning"] == 6
        entries = session.commit(
            lambda tx: tx.scan_entries(EntryQuery({"conversationId": cid}), 128)
        ).items
        assert sum(e.to_tree()["kind"] == "pi.tool-result" for e in entries) == 2
        completed = session.read_on_line(
            lambda: capture_completed(session._owner.storage, cid)
        )
        public_old = next(
            outcome for outcome in completed.outcomes if outcome.task_id == old["id"]
        )
        assert public_old.outcome.thaw() == {
            "status": "aborted",
            "reason": "operator_retry",
        }
        saved_frame = next(
            e.to_tree()["data"]["receipt"]
            for e in entries
            if e.to_tree()["kind"] == "pi.assistant"
        )
        for private in (
            old["state"]["checkpoint"],
            saved_frame,
            saved_frame["settings"]["material"],
        ):
            poisoned = replace(
                public_old,
                outcome=OwnedJson(
                    {"status": "aborted", "reason": "operator_retry", "result": private}
                ),
            )
            with pytest.raises(AssertionError):
                assert poisoned.outcome.thaw() == {
                    "status": "aborted",
                    "reason": "operator_retry",
                }
        dto = inspect_task(session, old["id"]).to_tree()
        assert dto["outcome"] == "aborted" and dto["invocation_ownership"] == "unknown"
        assert "checkpoint" not in dto and "result" not in dto
        assert old["state"]["checkpoint"]["intent"]["key"] not in json.dumps(dto)
    finally:
        session.close(timeout=10)


def test_generation_new_seed_terminal_only_and_nonce_conflict(tmp_path):
    wire = Wire()
    generation = definition(tmp_path, wire)
    session = Session(MemoryStorage())
    cid, _, old = admit(session, generation, seed(tmp_path))
    scheduler = TaskScheduler(session, [generation])
    try:
        scheduler.open()
        target = scheduler.resolution_target(old)
        with pytest.raises(ResolutionError) as error:
            scheduler.resolve(target, nonce="b" * 32, new_request=seed(tmp_path))
        assert error.value.code == "not_supported" and wire.calls == []
        record = session.commit(lambda tx: tx.task(old)).to_tree()
        original = {
            **record,
            "state": {
                "status": "terminal",
                "outcome": {
                    "status": "faulted",
                    "error": {"message": "private frame sentinel"},
                },
            },
        }
        session.commit(lambda tx: tx.set_task(original))
        original = session.commit(lambda tx: tx.task(old)).to_tree()
        target = scheduler.resolution_target(old)
        result = scheduler.resolve(target, nonce="c" * 32, new_request=seed(tmp_path))
        assert (
            scheduler.resolve(target, nonce="c" * 32, new_request=seed(tmp_path))
            == result
        )
        assert session.commit(lambda tx: tx.task(old)).to_tree() == original
        new = session.commit(lambda tx: tx.task(result.new_task_id)).to_tree()
        assert (
            new["id"] != old
            and new["state"]["checkpoint"]["identity"]
            != record["state"]["checkpoint"]["identity"]
        )
        assert OwnedJson(new["input"]) == OwnedJson(record["input"])
        other = snapshot_provider_request(
            replace(
                seed(tmp_path).request, user_prompt="new explicit input", messages=()
            )
        )
        with pytest.raises(ResolutionError) as conflict:
            scheduler.resolve(target, nonce="c" * 32, new_request=other)
        assert conflict.value.code == "conflict" and wire.calls == []
    finally:
        session.close(timeout=10)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize("frame_version", [1, 999])
@pytest.mark.parametrize("abort_requested", [False, True])
def test_actual_failed_settle_receipt_is_held_never_retried(
    tmp_path, monkeypatch, backend, frame_version, abort_requested
):
    from pipy_harness.native.durable import tool_execution

    actual_settle = tool_execution._Tool.settle

    async def incompatible_settle(self, record, runtime, context):
        async def damage(tx, current):
            doc = await tx.doc(tool_execution.RECEIPT_DOC)
            doc["key"] = "fixture-mismatched-key"

        await runtime.commit(damage)
        await actual_settle(self, record, runtime, context)

    monkeypatch.setattr(tool_execution._Tool, "settle", incompatible_settle)
    session, scheduler, cid, parent, old, idle = setup(
        tmp_path, backend, port_type=Keyed
    )
    try:
        if abort_requested:
            session.commit(lambda tx: tx.set_task({**old, "abortRequested": True}))
            old = session.commit(lambda tx: tx.task(old["id"])).to_tree()
        if frame_version != 1:
            newer = {
                **old,
                "state": {
                    **old["state"],
                    "checkpoint": {
                        **old["state"]["checkpoint"],
                        "version": frame_version,
                    },
                },
            }
            session.commit(lambda tx: tx.set_task(newer))
            old = session.commit(lambda tx: tx.task(old["id"])).to_tree()
        assert old["state"]["checkpoint"]["intent"] is None
        before = counts(tmp_path)
        assert before == {"calls": 1, "applications": 1, "prepares": 1, "providers": 1}
        receipt = session.snapshot(tool_execution.RECEIPT_DOC, task_id=old["id"]).value
        target = scheduler.resolution_target(old["id"])
        report = inspect_task(session, old["id"]).to_tree()
        assert report["reason"] == "receipt_held_needs_repair"
        with pytest.raises(ResolutionError) as refusal:
            scheduler.resolve(target, nonce="d" * 32)
        assert refusal.value.code == "receipt_held_needs_repair"
        assert session.commit(lambda tx: tx.task(old["id"])).to_tree() == old
        assert (
            session.snapshot(tool_execution.RECEIPT_DOC, task_id=old["id"]).value
            == receipt
        )
        assert counts(tmp_path) == before
        # Receipt changes invalidate captured authority as well. No receipt is
        # parsed as proof of safety, dropped, or redirected into settlement.
        session.commit(
            lambda tx: tx.doc(
                tool_execution.RECEIPT_DOC, task_id=old["id"]
            ).__setitem__("private", "retained receipt sentinel")
        )
        with pytest.raises(ResolutionError) as stale:
            scheduler.resolve(target, nonce="e" * 32)
        assert stale.value.code == "stale" and counts(tmp_path) == before
        assert inspect_task(session, old["id"]).reason == "receipt_held_needs_repair"
    finally:
        session.close(timeout=10)

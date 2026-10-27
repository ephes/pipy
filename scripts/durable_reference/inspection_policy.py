# ruff: noqa: E402
"""Native-only operator evidence. Never a Pi trace or a product execution path."""

from __future__ import annotations

import copy
import sqlite3
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/durable"))
from dataclasses import replace

from generation_support import Wire, admit, definition, seed
from test_resolution import (  # noqa: E402
    InfrastructureFailureFirst,
    make_definitions,
    setup,
)
from test_tool_boundaries import RichTool, run_idle
from test_tool_execution import KeyedTool
from tool_support import counts  # noqa: E402

from pipy_harness.native.agent.messages import AgentAssistantMessage
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.completed_values import capture
from pipy_harness.native.durable.generation import ROUND_DOC, USAGE_DOC
from pipy_harness.native.durable.inspection import inspect_task
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.records import TaskRecord
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
from pipy_harness.native.durable.tool_values import read_native_tool_output

FIELDS = {
    "hold",
    "source",
    "parent",
    "cid",
    "round_before",
    "round_replaced",
    "parent_before",
    "parent_replaced",
    "audit",
    "resolved",
    "duplicate",
    "reopened",
    "old_row",
    "new_row",
    "before",
    "after_resolution",
    "after_reopen",
    "after",
    "final",
    "ledger_before",
    "ledger_after",
    "entries",
    "outcomes",
    "calls",
    "invocations",
    "acks",
    "drained",
    "stale_code",
    "report",
}
REPORT_FIELDS = {
    "task_id",
    "conversation_id",
    "owner_id",
    "family",
    "version",
    "status",
    "phase",
    "outcome",
    "reason",
    "compatibility",
    "dependencies",
    "dependency_count",
    "invocation_ownership",
    "execution_material",
}
MUTANTS = (
    "payload",
    "availability",
    "stale",
    "duplicate",
    "oldkey",
    "preack",
    "success",
    "slot",
    "usage",
    "worker",
    "latewrite",
    "doubleeffect",
    "doubleoutput",
    "earlywake",
    "cutoff",
    "hold",
)


def produce(root, d):
    root.mkdir(parents=True, mode=0o700)
    session, scheduler, cid, parent, old, _ = setup(root, "sqlite")
    before = counts(root)
    round_before = session.snapshot(ROUND_DOC, task_id=parent).value.thaw()
    ledger_before = session.snapshot(USAGE_DOC, conversation_id=cid).value.thaw()
    parent_before = session.commit(lambda tx: tx.task(parent)).to_tree()
    session.close(timeout=10)
    acks, invocations, guard = [], [], threading.Lock()

    class ProofPort(InfrastructureFailureFirst):
        def invoke(self, request, context):
            event = {
                "key": context.call_key,
                "name": request.tool_name,
                "arguments": copy.deepcopy(request.arguments),
                "started": time.monotonic_ns(),
                "ended": None,
            }
            with guard:
                invocations.append(event)
            try:
                return super().invoke(request, context)
            finally:
                with guard:
                    event["ended"] = time.monotonic_ns()

    def observe(pub):
        for change in pub.changes:
            if hasattr(change, "to_tree"):
                value = change.to_tree()
                if value["type"] == "task":
                    row = value["value"]
                    if row["state"].get("checkpoint", {}).get("phase") == "execute":
                        with guard:
                            acks.append({"record": row, "at": time.monotonic_ns()})

    session, owner = reopen(root, ProofPort)
    try:
        target = owner.resolution_target(old["id"])
        resolved = owner.resolve(target, nonce="7" * 32)
        duplicate = owner.resolve(target, nonce="7" * 32)
        stale_code = _stale(owner, target)
        replaced = session.snapshot(ROUND_DOC, task_id=parent).value.thaw()
        old_row = session.commit(lambda tx: tx.task(old["id"])).to_tree()
        parent_replaced = session.commit(lambda tx: tx.task(parent)).to_tree()
        new_row = session.commit(lambda tx: tx.task(resolved.new_task_id)).to_tree()
        audit = session.snapshot(AUDIT, conversation_id=cid, key="7" * 32).value.thaw()
        after_resolution = counts(root)
    finally:
        session.close(timeout=10)
    session, owner = reopen(root, ProofPort)
    try:
        replayed = owner.resolve(owner.resolution_target(old["id"]), nonce="7" * 32)
        after_reopen = counts(root)
        session.subscribe_commits(observe)
        idle = threading.Event()
        reserve = owner._reserve

        async def drained():
            result = await reserve()
            if result is None:
                idle.set()
            return result

        owner._reserve = drained
        owner.resume()
        d.require(idle.wait(15), "Operator fixture did not drain")
        with owner._guard:
            owner_drained = owner._invocation is None
        final = [r.to_tree() for r in owner.inspect()]
        ledger_after = session.snapshot(USAGE_DOC, conversation_id=cid).value.thaw()
        entries = [
            e.to_tree()
            for e in session.commit(
                lambda tx: tx.scan_entries(EntryQuery({"conversationId": cid}), 128)
            ).items
        ]
        completed = session.read_on_line(lambda: capture(session._owner.storage, cid))
        outcomes = [
            {"task_id": outcome.task_id, "outcome": outcome.outcome.thaw()}
            for outcome in completed.outcomes
        ]
        report = inspect_task(session, old["id"]).to_tree()
        after = counts(root)
    finally:
        session.close(timeout=10)
    with sqlite3.connect(root / "effects.sqlite") as db:
        calls = [
            {"name": name, "key": key, "arguments": __import__("json").loads(args)}
            for name, key, args in db.execute(
                "SELECT name,key,args FROM calls ORDER BY rowid"
            )
        ]
    return {
        "hold": _hold(root / "hold"),
        "source": old,
        "parent": parent,
        "cid": cid,
        "round_before": round_before,
        "round_replaced": replaced,
        "parent_before": parent_before,
        "parent_replaced": parent_replaced,
        "audit": audit,
        "resolved": resolved.to_tree(),
        "duplicate": duplicate.to_tree(),
        "reopened": replayed.to_tree(),
        "old_row": old_row,
        "new_row": new_row,
        "before": before,
        "after_resolution": after_resolution,
        "after_reopen": after_reopen,
        "after": after,
        "final": final,
        "ledger_before": ledger_before,
        "ledger_after": ledger_after,
        "entries": entries,
        "outcomes": outcomes,
        "calls": calls,
        "invocations": invocations,
        "acks": acks,
        "drained": owner_drained,
        "stale_code": stale_code,
        "report": report,
    }


def _stale(owner, target):
    try:
        owner.resolve(target, nonce="8" * 32)
    except ResolutionError as error:
        return error.code
    return "accepted"


def reopen(root, port_type):
    generation, tool, _ = make_definitions(root, port_type=port_type)
    session = Session(open_sqlite_storage(str(root / "session.sqlite"), mode="open"))
    scheduler = TaskScheduler(session, [generation, tool])
    scheduler.open()
    return session, scheduler


def protocol(raw, d):
    d.shape(raw, FIELDS, "Native operator proof")
    for key in ("source", "old_row", "new_row", "parent_before", "parent_replaced"):
        TaskRecord(raw[key])
    for row in raw["final"]:
        TaskRecord(row)
    for ack in raw["acks"]:
        d.shape(ack, {"record", "at"}, "Native acknowledged intent")
        TaskRecord(ack["record"])
    return raw


def valid(raw, tools, d):
    protocol(raw, d)
    old, replaced = raw["source"], raw["old_row"]
    result, new = raw["resolved"], raw["new_row"]
    wanted_outcome = {"status": "aborted", "reason": "operator_retry"}
    expected_round = copy.deepcopy(raw["round_before"])
    expected_round["tools"] = [
        new["id"] if id == old["id"] else id for id in expected_round["tools"]
    ]
    expected_round["slots"] = [
        {**slot, "taskId": new["id"]} if slot.get("taskId") == old["id"] else slot
        for slot in expected_round["slots"]
    ]
    expected_parent = {
        **raw["parent_before"],
        "state": {**raw["parent_before"]["state"], "on": expected_round["tools"]},
    }
    old_outcome = next(
        (row["outcome"] for row in raw["outcomes"] if row["task_id"] == old["id"]), None
    )
    return all(
        (
            old["state"]["status"] == "pending"
            and old["state"]["checkpoint"].get("phase") == "blocked",
            result == raw["duplicate"] == raw["reopened"],
            result["source_task_id"] == old["id"],
            result["new_task_id"] == new["id"] != old["id"],
            new.get("owner") == old.get("owner") == raw["parent"],
            new["input"] == old["input"],
            replaced["state"] == {"status": "terminal", "outcome": wanted_outcome},
            old_outcome == wanted_outcome,
            raw["round_replaced"] == expected_round,
            raw["parent_replaced"] == expected_parent,
            raw["audit"]["source"] == old,
            raw["audit"]["new_task_id"] == new["id"],
            raw["before"] == raw["after_resolution"] == raw["after_reopen"],
            raw["stale_code"] == "stale",
            raw["report"].keys() == REPORT_FIELDS,
            raw["report"].get("execution_material") == "not_checked",
            raw["report"].get("invocation_ownership") == "unknown",
            _effects(raw),
            _acknowledged(raw),
            _usage(raw, tools, d),
            len([e for e in raw["entries"] if e["kind"] == "pi.tool-result"])
            == len(raw["round_before"]["tools"]),
            len([r for r in raw["final"] if r["kind"] == old["kind"]])
            == len(raw["round_before"]["tools"]) + 1,
            all(r["state"]["status"] == "terminal" for r in raw["final"]),
            raw["drained"] is True,
            _hold_valid(raw["hold"]),
        )
    )


def _hold(root):
    root.mkdir(mode=0o700)
    port, later = RichTool(None, terminate=True), KeyedTool("later", [])
    wire = Wire(("rich", "later"))
    generation = definition(root, wire)
    tool = define_tool_task(
        {
            p.definition.name: ToolRegistration(p, "fixture." + p.definition.name, "1")
            for p in (port, later)
        },
        workspace_root=root,
    )
    session = Session(MemoryStorage())
    cid, _, parent = admit(
        session,
        generation,
        snapshot_provider_request(
            replace(
                seed(root).request, available_tools=(port.definition, later.definition)
            )
        ),
    )
    try:
        scheduler = run_idle(session, [generation, tool])
        child = next(
            r.id
            for r in scheduler.inspect()
            if r.to_tree()["kind"] == "pi.tool"
            and r.to_tree()["input"]["callId"] == "call:1"
        )
        entries = session.commit(
            lambda tx: tx.scan_entries(EntryQuery({"conversationId": cid}), 128)
        ).items
        output = next(
            e.to_tree()["data"]["nativeToolOutput"]
            for e in entries
            if e.to_tree()["kind"] == "pi.tool-result"
        )
        return {
            "parent": inspect_task(session, parent).to_tree(),
            "later": inspect_task(session, child).to_tree(),
            "native_output": output,
            "calls": len(port.calls),
            "later_calls": len(later.calls),
            "provider_calls": len(wire.calls),
        }
    finally:
        session.close(timeout=10)


def _hold_valid(value):
    return (
        value["parent"]["reason"] == value["later"]["reason"] == "retained_terminate"
        and value["parent"]["status"] == "waiting"
        and value["later"]["status"] == "pending"
        and read_native_tool_output(value["native_output"])[1].result.terminate
        and value["calls"] == value["provider_calls"] == 1
        and value["later_calls"] == 0
    )


def _effects(raw):
    calls = raw["calls"]
    count = len(raw["round_before"]["tools"])
    before, after = raw["before"], raw["after"]
    return all(
        (
            len(calls) == after["calls"] == before["calls"] + count,
            after["applications"] == before["applications"] + count,
            after["prepares"] == before["prepares"] + count,
            after["providers"] == before["providers"] + 1,
            len({call["key"] for call in calls}) == len(calls),
            calls[0]["key"] == raw["source"]["state"]["checkpoint"]["intent"]["key"],
            len(raw["invocations"]) == count,
        )
    )


def _acknowledged(raw):
    by_key = {
        ack["record"]["state"]["checkpoint"].get("intent", {}).get("key"): ack
        for ack in raw["acks"]
    }
    for invoke in raw["invocations"]:
        ack = by_key.get(invoke["key"])
        if ack is None:
            return False
        checkpoint = ack["record"]["state"]["checkpoint"]
        if (
            checkpoint.get("phase") != "execute"
            or checkpoint["intent"]["arguments"] != invoke["arguments"]
            or not ack["at"] <= invoke["started"] <= invoke["ended"]
        ):
            return False
    return True


def _usage(raw, tools, d):
    assistants = [
        decode_canonical(e["model"][0])
        for e in raw["entries"]
        if e["kind"] == "pi.assistant"
    ]
    if not all(type(message) is AgentAssistantMessage for message in assistants):
        return False
    values = [tools.canonical.project_usage(message.usage) for message in assistants]
    ledger = tools.canonical.project_ledger_usage(
        raw["ledger_after"]["models"]["openai/gpt-test"]
    )
    return (
        tools.usage_matches(values, ledger)
        and len(raw["ledger_after"]["settlements"]) == len(values)
        and len(raw["ledger_before"]["settlements"]) + 1 == len(values)
    )


def mutate(raw, name):
    value = copy.deepcopy(raw)
    simple = {
        "availability": (value["report"], "execution_material", "executable"),
        "stale": (value, "stale_code", "accepted"),
        "duplicate": (
            value["duplicate"],
            "new_task_id",
            value["resolved"]["new_task_id"] + 1,
        ),
        "worker": (value, "drained", False),
        "oldkey": (value["calls"][1], "key", value["calls"][0]["key"]),
        "preack": (value["acks"][0], "at", value["invocations"][0]["started"] + 1),
        "success": (
            value["old_row"],
            "state",
            {"status": "terminal", "outcome": {"status": "completed", "result": None}},
        ),
        "slot": (
            value["round_replaced"],
            "tools",
            list(reversed(value["round_replaced"]["tools"])),
        ),
        "usage": (
            value["ledger_after"]["models"]["openai/gpt-test"],
            "input",
            value["ledger_after"]["models"]["openai/gpt-test"]["input"] + 1,
        ),
        "latewrite": (value["old_row"], "state", value["source"]["state"]),
        "doubleeffect": (
            value["after"],
            "applications",
            value["after"]["applications"] + 1,
        ),
        "earlywake": (
            value["parent_replaced"],
            "state",
            {
                "status": "pending",
                "checkpoint": value["parent_replaced"]["state"]["checkpoint"],
            },
        ),
        "cutoff": (
            value["parent_replaced"]["state"]["checkpoint"]["identity"],
            "cutoff",
            value["parent_replaced"]["state"]["checkpoint"]["identity"]["cutoff"] + 1,
        ),
        "hold": (value["hold"]["parent"], "reason", None),
    }
    if name in simple:
        target, key, replacement = simple[name]
        target[key] = replacement
    elif name == "payload":
        value["report"]["checkpoint"] = value["source"]["state"]["checkpoint"]
    elif name == "doubleoutput":
        value["entries"].append(
            copy.deepcopy(
                next(e for e in value["entries"] if e["kind"] == "pi.tool-result")
            )
        )
    else:
        raise ValueError("Unknown native operator mutant")
    return value

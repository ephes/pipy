"""DE02b3 paired pinned Node Session vs native Session (sync and async). run/compare:0 equal,1 mismatch,2 invalid.

Closed recipes drive the actual pinned Pi ``SessionImpl`` (through a bounded
driver subclass) and the private native ``session_core.Session`` over their
real Memory backends, in both native modes: ``commit()`` on owned threads and
``acommit()`` on one event loop. Commit/Tx/default-delta document, publication,
failure and close paths only; no product, typed-document, fork or full Session
parity claim. ``SCOPE`` lists everything not compared; the recipe's closed cell
map ties every original DE02/DE02b cell to paired scenarios or native tests.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import copy
import importlib.util
import json
import os
import shutil
import sys
import threading
import time
import types
import uuid
from collections import deque
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "b3_guard_parent", Path(__file__).with_name("durable_session_guard_comparison.py")
)
assert spec and spec.loader
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
m = g.m
v = g.v
o = g.o
from pipy_harness.native.durable import session as real_session  # noqa: E402
from pipy_harness.native.durable import session_core as real_core  # noqa: E402
from pipy_harness.native.durable import tx as real_tx  # noqa: E402
from pipy_harness.native.durable.codec import OwnedJson  # noqa: E402
from pipy_harness.native.durable.delta_ops import own_operations  # noqa: E402
from pipy_harness.native.durable.memory import MemoryStorage  # noqa: E402
from pipy_harness.native.durable.records import Record, safe_integer  # noqa: E402
from pipy_harness.native.durable.storage import (  # noqa: E402
    ConversationQuery,
    EntryQuery,
    Page,
    StorageRequestError,
    StorageWrite,
    TaskQuery,
)

PHASE = "session-commit-b3-v1"
INPUT = ROOT / "tests/fixtures/durable/session-commit-v1.input.json"
MANIFEST = ROOT / "tests/fixtures/durable/session-commit-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/session-commit.mts"
DESTINATION = "session-commit.mts"
MODULES = [
    "packages/durable/src/session/session.ts",
    "packages/durable/src/session/transaction.ts",
    "packages/durable/src/documents.ts",
    "packages/durable/src/errors.ts",
    "packages/durable/src/storage/memory.ts",
    "packages/chord/src/context/index.ts",
    "packages/chord/src/delta/index.ts",
]
SOURCES = set(MODULES) | {
    "packages/durable/src/storage/scan.ts",
    "packages/durable/src/types.ts",
    "packages/durable/src/ids.ts",
    "packages/durable/src/timers.ts",
    "packages/chord/src/delta/tracker.ts",
}
UPSTREAM_TESTS = {
    "packages/durable/test/" + name
    for name in """harness-storage-failure.test.ts session-documents.test.ts
    session-tables.test.ts session-support.ts""".split()
}
CLOSURE = g.CLOSURE | {
    "src/pipy_harness/native/durable/tx.py",
    "src/pipy_harness/native/durable/draft_tracker.py",
    "src/pipy_harness/native/durable/delta_ops.py",
    "src/pipy_harness/native/durable/session_core.py",
    "tests/durable/test_session_tx_core.py",
    "tests/durable/test_session_commit.py",
    "scripts/durable_session_commit_comparison.py",
    "scripts/durable_reference/cases/session-commit.mts",
    "tests/fixtures/durable/session-commit-v1.input.json",
    "tests/durable/test_session_commit_comparison.py",
}
SCENARIOS = tuple(
    """
    tb132-minted-ids atomic-before-ack after-ack-applied h291-request-error-commit
    ids-not-reused ingress-no-alias ingress-pending-nested-copy
    tb82-121-read-after-write tb188-213-final-owner d78-120-137-create-change-identity
    d158-copy-per-placement d174-empty-batch d207-escaped-returned-draft
    d228-callback-failure-aborts d284-pending-acquisition d311-no-late-init
    d337-callback-error-wins d352-captured-tx d250-memoized-duplicate
    d459-old-revision-held-commit d405-assembly-rollback d479-retire-recreate
    d591-cold-load queued-view-cancel view-cancel-in-callback-ignored
    view-cancel-during-held-commit cancelled-tx-read-exempt h276-caught-read-then-write
    h276-caught-read-then-return late-read-discard failure-rejects-queued
    commit-before-adopt-publish h328-internal-listener-poisons
    h466-host-close-listeners-contained d605-publication-synchronous
    h431-rejected-promise-listener d622-close-listeners-unsubscribe
    d632-admitted-before-close busy-line-guard-window idle-close-quiesce
    fail-under-held-line
    """.split()
)
CELLS = tuple(
    """
    DE02:cross-record-atomicity DE02:before-after-ack-failure DE02:immutable-ids
    DE02:no-alias-ingress DE02b:read-after-write DE02b:final-owner-checks
    DE02b:no-late-initializer DE02b:no-draft-escape DE02b:queued-cancel-cut
    DE02b:running-callback-and-admitted-commit DE02b:first-failure-poisoning
    DE02b:late-read-discard DE02b:commit-before-adopt-publication
    DE02b:publication-after-storage-only DE02b:adopt-failure DE02b:listeners
    DE02b:reentry DE02b:close-drain-barriers DE02b2:no-op-suppression
    DE02b:committed-snapshots DE02b:pending-operations DE02:kernel-storage-spec
    """.split()
)
NATIVE_FILES = tuple(
    """test_session_storage_guard.py test_session_guard_comparison.py
    test_session_tx_core.py test_session_commit.py""".split()
)
MODES = ("sync", "async")
WAIT = 5.0
"""Bound for every native wait/join; negative-control tests may lower it."""
NATIVE_TIMEOUT = 240
"""Deadline for the native child process; its process group is killed after it."""
EXECUTOR_WORKERS = 8
SYNC_OPS = {"settle_submission", "place_submission", "set_task"}
QUERIES = {
    "scan_conversations": ConversationQuery,
    "scan_entries": EntryQuery,
    "scan_tasks": TaskQuery,
}
UNDEFINED = ["undefined"]
require = v.require
load = v.load
dump = v.dump
REAL = types.SimpleNamespace(session=real_session, tx=real_tx, core=real_core)


def _rules(text: str) -> dict[str, tuple[set[str], set[str]]]:
    """``name: a b ?c`` lines: required ``{a, b}``, optional ``{c}``."""
    table = {}
    for line in text.strip().splitlines():
        name, _, words = line.partition(":")
        items = words.split()
        required = {w for w in items if w[0] != "?"}
        table[name.strip()] = (required, {w[1:] for w in items if w[0] == "?"})
    return table


TX_FIELDS = _rules(
    """
    conversation: id
    entry: id
    task: id
    submission: id
    submission_by_request: conversation request
    latest_head_marker: conversation
    scan_conversations: query limit
    scan_entries: query limit
    scan_tasks: query limit
    create_conversation: ownership
    append_entry: conversation draft
    create_task: kind version input checkpoint ownership ?conversation ?background
    create_submission: create
    settle_submission: id settlement
    place_submission: id entry
    set_task: record
    doc: doc ?conversation
    retire_doc: doc ?conversation
    """
)
PROGRAM_OPS = _rules(
    """
    await: ?bind
    draft_set: path value
    draft_push: path values
    draft_pop: path
    draft_get: path ?bind
    bind_tx:
    local: value
    mutate_local: path value
    catch: mark
    mark:
    hold:
    throw:
    return:
    """
)
STEPS = _rules(
    """
    start: job program ?context
    call: job program ?context
    settle: ?job ?handle
    await_mark: mark
    await_queued: ?job ?handle
    release: ?hold ?gate
    cancel: job
    arm: target gate hold outcome ?job
    await_entered: gate
    arm_close: hold
    await_close_entered:
    release_close:
    fail: error
    close_start: handle
    quiesce:
    observe: blocked_by
    snapshot: doc ?conversation ?bind ?handle
    guard_read: call
    raw_read: call
    raw_commit: writes
    identity: a b
    inspect: bind
    use: action ?tx
    subscribe: listener
    unsubscribe: listener
    await_end:
    """
)
REFS = _rules(
    """
    scripted: id
    session-failed: message cause ?cause_split
    session-closed: message
    request: message
    commit-cancelled:
    tx-settled:
    pending-ops: message
    read-after-write: method
    draft-revoked:
    error: message
    unclassified: side name message
    """
)
EFFECT_SHAPES = _rules(
    """
    call: e op gate args
    return: e op gate status ?value ?error
    close-enter: e
    close-exit: e status ?error
    report: e error type failure_visible
    publish: e listener seq changes handle_settled
    close-listener: e listener
    init: e doc
    mark: e mark
    caught: e mark error
    """
)
SHAPES = _rules(
    """
    listener: id kind action ?error ?bind ?hit ?deferred
    hit: doc bind ?conversation
    scenario: id pi_anchor modes steps ?listeners
    cell: paired native why ?docs
    """
)
WRITES = "create_conversation append_entry create_task create_submission doc retire_doc"
READ_METHODS = set(TX_FIELDS) - SYNC_OPS - set(WRITES.split())
STORAGE_OPS = {
    "mint_id": "mint",
    "find_latest_head_marker": "head",
    "submission_by_request": "request",
} | {
    name: name
    for name in """commit conversation task submission entry find_document document
    scan_conversations scan_entries scan_tasks scan_submissions scan_documents""".split()
}
BACKEND_OPS = set(STORAGE_OPS.values())
RAW_READS = set(
    "conversation task submission entry scan_entries find_document document".split()
)
OUTCOME_STEPS = set(
    "call settle guard_read raw_read raw_commit fail identity inspect use".split()
)
BARRIERS = {"await_mark", "await_queued", "await_entered", "await_close_entered"}
EFFECTFUL = set(
    """start call snapshot guard_read raw_read fail close_start raw_commit release
    cancel release_close use subscribe unsubscribe""".split()
)
IDLE_WINDOW = set(
    "guard_read fail raw_commit start subscribe unsubscribe close_start release".split()
)


def shape(value: Any, fields: set[str], label: str) -> Any:
    return o.contract.shape(value, fields, label)


def fields(value: Any, rule: tuple[set[str], set[str]], label: str) -> Any:
    required, optional = rule
    ok = type(value) is dict and required <= value.keys() <= required | optional
    require(ok, "Invalid " + label + " shape")
    return value


def _name(value: Any, label: str) -> str:
    require(type(value) is str and bool(value), "Malformed " + label)
    return value


def _names(value: Any, label: str) -> list[str]:
    items = [_name(item, label) for item in o.contract.array(value, label)]
    require(len(set(items)) == len(items), "Duplicate " + label)
    return items


def _one(step: Any, keys: set[str], label: str) -> str:
    present = step.keys() & keys
    require(len(present) == 1, label + " needs exactly one target")
    return str(step[present.pop()])


# -- the closed recipe -------------------------------------------------------------


def _literal(value: Any, refs: bool = False) -> None:
    """A JSON value whose only non-literal nodes are ``$local``/``$bind`` refs."""

    def strip(item: Any) -> Any:
        if type(item) is dict and len(item) == 1 and next(iter(item)) in REF_KEYS:
            require(refs, "Unexpected value reference")
            _name(next(iter(item.values())), "value reference")
            return None
        if type(item) is dict:
            return {key: strip(x) for key, x in item.items()}
        return [strip(x) for x in item] if type(item) is list else item

    OwnedJson(strip(value))


REF_KEYS = {"$local", "$bind"}


def _check_tx(value: Any, op: Any, facts: dict[str, Any]) -> None:
    name = op.get("tx", op.get("start_tx"))
    require(name in TX_FIELDS, "Unknown Tx operation")
    required, optional = TX_FIELDS[name]
    if "start_tx" in op:
        fields(op, ({"start_tx", "handle"} | required, optional), "Tx operation")
        require(name not in SYNC_OPS, "A synchronous Tx op cannot start unawaited")
        facts["async"] = True
        facts["handles"].append(_name(op["handle"], "Tx handle"))
    else:
        fields(op, ({"tx"} | required, optional | {"bind"}), "Tx operation")
        facts["binds"] += [op["bind"]] if "bind" in op else []
    for key in ("id", "conversation", "entry"):
        safe_integer(op.get(key, 1))
    doc = value["docs"].get(op.get("doc"), {})
    owned = ("conversation" in op) == (doc.get("scope") == "conversation")
    require("doc" not in op or (bool(doc) and owned), "Document owner drift")
    if name in QUERIES:
        require(type(op["limit"]) is int and op["limit"] > 0, "Malformed scan limit")
        QUERIES[name](op["query"])
    if name == "create_task":
        typed = type(op["kind"]) is str and type(op["version"]) is int
        require(typed, "Malformed task kind")
        _literal(op["checkpoint"])
    for key in ("draft", "ownership", "input", "create", "settlement", "record"):
        _literal(op.get(key), refs=True)


def _check_catch(value: Any, op: Any, facts: dict[str, Any]) -> None:
    inner = o.contract.array(op["catch"], "catch ops")
    require(
        all(type(x) is dict and "tx" in x for x in inner), "catch holds Tx ops only"
    )
    _program(value, inner, facts, top=False)
    facts["caught"].append(_name(op["mark"], "catch mark"))


def _check_return(value: Any, op: Any, facts: dict[str, Any]) -> None:
    result = op["return"]
    keys = set(result) if type(result) is dict else set()
    require(
        keys in ({"value"}, {"bind"}, {"bind", "key"}, {"binds"}), "Malformed return"
    )
    _literal(result.get("value"))


def _check_named(value: Any, op: Any, facts: dict[str, Any]) -> None:
    head = next(k for k in ("mark", "hold", "throw", "await") if k in op)
    if head == "throw":
        require(op["throw"] in facts["errors"], "Unknown thrown error")
    elif head == "await":
        require(op["await"] in facts["handles"], "Await of an unknown Tx handle")
        facts["async"] = True
        facts["awaited"].append(op["await"])
        facts["binds"] += [op["bind"]] if "bind" in op else []
    else:
        facts[head + "s"].append(_name(op[head], head))


def _check_draft(value: Any, op: Any, facts: dict[str, Any]) -> None:
    head = next(k for k in op if k in PROGRAM_OPS)
    if head in {"bind_tx", "local"}:
        facts["binds"].append(_name(op[head], head))
        _literal(op.get("value"))
        return
    _name(op[head], "draft name")
    path = o.contract.array(op["path"], "draft path")
    require(all(type(x) in {str, int} for x in path), "Malformed draft path")
    _literal(op.get("value", op.get("values")), refs=True)
    facts["binds"] += [op["bind"]] if "bind" in op else []


PROGRAM_CHECKS: dict[str, Callable[[Any, Any, dict[str, Any]], None]] = {
    "tx": _check_tx,
    "catch": _check_catch,
    "return": _check_return,
} | dict.fromkeys(("mark", "hold", "throw", "await"), _check_named)


def _head(op: Any) -> str:
    require(type(op) is dict, "Malformed program op")
    if "tx" in op or "start_tx" in op:
        return "tx"
    heads = ["catch"] if "catch" in op else [key for key in op if key in PROGRAM_OPS]
    require(len(heads) == 1, "Unknown program op")
    return heads[0]


def _program(value: Any, ops: Any, facts: dict[str, Any], top: bool = True) -> None:
    """Validate one closed callback program and collect its facts."""
    items = o.contract.array(ops, "program ops")
    require(bool(items), "Empty program")
    for index, op in enumerate(items):
        head = _head(op)
        last = top and index == len(items) - 1
        require(head != "return" or last, "return must end its program")
        if head != "tx":
            required, optional = PROGRAM_OPS[head]
            fields(op, ({head} | required, optional), "program op " + head)
        PROGRAM_CHECKS.get(head, _check_draft)(value, op, facts)


def program_facts(value: Any, name: str, ops: Any = None) -> dict[str, Any]:
    keys = ("marks", "holds", "binds", "handles", "caught", "awaited")
    facts: dict[str, Any] = {key: [] for key in keys}
    facts |= {"async": False, "errors": {e["id"] for e in value["errors"]}}
    require(ops is not None or name in value["programs"], "Unknown program")
    _program(value, value["programs"][name] if ops is None else ops, facts)
    return facts


class _Planner:
    """Closed recipe rules for one scenario, including the timing discipline."""

    def __init__(self, value: Any, scenario: Any) -> None:
        self.value = value
        self.async_only = scenario["modes"] == ["async"]
        self.errors = {e["id"] for e in value["errors"]}
        self.jobs: dict[str, Any] = {}
        self.handles: dict[str, str] = {}
        self.settled: set[str] = set()
        self.unjoined: set[str] = set()
        self.marks: dict[str, str] = {}
        self.holds: set[str] = set()
        self.released: set[str] = set()
        self.awaited: set[str] = set()
        self.cancelled: set[str] = set()
        self.gates: dict[str, dict[str, Any]] = {}
        self.close: dict[str, bool] = {}
        self.window: str | None = None
        self.ended = False
        self.sites: dict[str, list[str]] = {}
        self.listeners: dict[str, Any] = {}
        self.subscribed: set[str] = set()
        for item in o.contract.array(scenario.get("listeners", []), "listeners"):
            self.listener(item)

    def listener(self, item: Any) -> None:
        fields(item, SHAPES["listener"], "listener")
        key, action = _name(item["id"], "listener ID"), item["action"]
        require(
            key not in self.listeners
            and item["kind"] in {"commit", "internal", "close"}
            and action in {"record", "throw", "reject"}
            and ("error" in item) == (action != "record")
            and item.get("error", next(iter(self.errors))) in self.errors
            and item.get("deferred", False) in {True, False}
            and (action != "reject" or self.async_only)
            and (item["kind"] != "close" or not {"bind", "hit"} & item.keys()),
            "Malformed listener",
        )
        if "hit" in item:
            fields(item["hit"], SHAPES["hit"], "listener hit")
        for name in (item.get("bind"), item.get("hit", {}).get("bind")):
            if name is not None:
                self.site(name, "listener")
        self.listeners[key] = item
        if not item.get("deferred", False):
            self.subscribed.add(key)

    def site(self, name: Any, kind: str) -> None:
        self.sites.setdefault(_name(name, "bind name"), []).append(kind)

    def bound(self, name: Any) -> None:
        """Identity operands have exactly one binding site (or only listener ones)."""
        sites = self.sites.get(name, [])
        only = set(sites) == {"listener"} if "listener" in sites else len(sites) == 1
        require(bool(sites) and only, "Ambiguous or unbound name")

    def flying(self) -> set[str]:
        kinds = {"job", "snapshot"}
        return {h for h, k in self.handles.items() if k in kinds} - self.settled

    def check(self, op: str) -> None:
        require(not self.ended, "Step after await_end")
        joined = op not in EFFECTFUL or not self.unjoined
        require(joined, "Unjoined concurrent work before an effect")
        idle = self.window != "idle" or op not in IDLE_WINDOW
        require(idle, "Effect inside the idle close window")

    def handle(self, name: Any, kind: str) -> str:
        key = _name(name, "handle")
        require(key not in self.handles, "Handle reused")
        self.handles[key] = kind
        return key

    def start(self, step: Any) -> None:
        job = self.handle(step["job"], "job")
        require(step.get("context", "cancellable") == "cancellable", "Unknown context")
        facts = program_facts(self.value, step["program"])
        require(
            not facts["async"] or self.async_only, "Async-only op in a sync scenario"
        )
        fresh = (
            not set(facts["marks"]) & self.marks.keys()
            | set(facts["holds"]) & self.holds
        )
        require(fresh, "Mark or hold produced twice")
        self.marks |= dict.fromkeys(facts["marks"], job)
        self.holds |= set(facts["holds"])
        for name in facts["binds"]:
            self.site(name, "program")
        for name in facts["handles"]:
            self.handle(name, "op")
        self.awaited |= set(facts["awaited"])
        self.jobs[job] = {"context": step.get("context"), "holds": set(facts["holds"])}
        self.unjoined.add(job)
        if step["op"] == "call":
            self.settle({"job": job})

    call = start

    def settle(self, step: Any) -> None:
        key = _one(step, {"job", "handle"}, "settle")
        kind = self.handles.get(key)
        known = kind is not None and (kind == "job") == ("job" in step)
        require(known and key not in self.settled, "Settle of unknown handle")
        held = self.jobs.get(key, {"holds": set()})["holds"]
        require(held <= self.released, "Settle of a held job")
        self.settled.add(key)
        self.unjoined.discard(key)
        if self.window == "busy" and not self.flying():
            self.window = "idle"

    def await_mark(self, step: Any) -> None:
        require(step["mark"] in self.marks, "Await of an unknown mark")
        self.unjoined.discard(self.marks.pop(step["mark"]))

    def await_queued(self, step: Any) -> None:
        key = _one(step, {"job", "handle"}, "await_queued")
        require(key in self.flying(), "Await of an unknown queued handle")
        self.unjoined.discard(key)

    def release(self, step: Any) -> None:
        key = _one(step, {"hold", "gate"}, "release")
        if "hold" in step:
            require(key in self.holds - self.released, "Release of an unknown hold")
            self.released.add(key)
            return
        gate = self.gates.get(key, {})
        require(
            gate.get("entered") is True and not gate["released"], "Release before entry"
        )
        gate["released"] = True

    def cancel(self, step: Any) -> None:
        context = self.jobs.get(step["job"], {}).get("context")
        once = step["job"] not in self.cancelled
        require(context == "cancellable" and once, "Cancel of a non-cancellable job")
        self.cancelled.add(step["job"])

    def arm(self, step: Any) -> None:
        outcome = step["outcome"]
        one = type(outcome) is dict and len(outcome) == 1
        kind = next(iter(outcome)) if one else ("pass" if outcome == "pass" else None)
        scripted = kind in {"throw_before", "throw_after", "abort_then_throw"}
        require(
            step["target"] in BACKEND_OPS
            and _name(step["gate"], "gate") not in self.gates
            and type(step["hold"]) is bool
            and (kind == "pass" or (scripted and outcome[kind] in self.errors))
            and ("job" in step) == (kind == "abort_then_throw"),
            "Malformed gate",
        )
        abort = step.get("job")
        gate = {
            "hold": step["hold"],
            "entered": False,
            "released": False,
            "abort": abort,
        }
        self.gates[step["gate"]] = gate

    def await_entered(self, step: Any) -> None:
        gate = self.gates.get(step["gate"], {})
        holds = gate.get("hold") is True and not gate["entered"]
        require(holds, "Await on a gate that cannot hold")
        gate["entered"] = True
        self.unjoined.clear()

    def arm_close(self, step: Any) -> None:
        once = not self.close and type(step["hold"]) is bool
        require(once, "Backend close armed twice")
        self.close = {"hold": step["hold"], "entered": False, "released": False}

    def await_close_entered(self, step: Any) -> None:
        holds = self.close.get("hold") is True and not self.close["entered"]
        require(holds, "Await on a close that cannot hold")
        self.close["entered"] = True
        self.unjoined.clear()

    def release_close(self, step: Any) -> None:
        entered = self.close.get("entered") is True and not self.close["released"]
        require(entered, "Release before close entry")
        self.close["released"] = True

    def fail(self, step: Any) -> None:
        require(step["error"] in self.errors, "Unknown failure error")

    def close_start(self, step: Any) -> None:
        first = "closer" not in self.handles.values() and not self.close.get("hold")
        require(first, "Second or held recipe close")
        self.handle(step["handle"], "closer")
        self.window = "busy" if self.flying() else "idle"

    def quiesce(self, step: Any) -> None:
        require(self.window == "idle", "quiesce outside an idle close window")
        self.window = None

    def observe(self, step: Any) -> None:
        blocked = set(_names(step["blocked_by"], "blocked handles"))
        require(blocked <= self.handles.keys(), "Unknown blocked handle")
        self.unjoined.clear()

    def snapshot(self, step: Any) -> None:
        scope = self.value["docs"].get(step["doc"], {}).get("scope")
        owned = ("conversation" in step) == (scope == "conversation")
        require(scope is not None and owned, "Snapshot owner drift")
        safe_integer(step.get("conversation", 1))
        if "bind" in step:
            self.site(step["bind"], "snapshot")
        if "handle" in step:
            self.unjoined.add(self.handle(step["handle"], "snapshot"))

    def guard_read(self, step: Any) -> None:
        self.raw_read(step, RAW_READS | {"mint"})

    def raw_read(self, step: Any, ops: set[str] = RAW_READS) -> None:
        call = shape(step["call"], {"op", "args"}, "storage call")
        known = call["op"] in ops and "cursor_ref" not in call["args"]
        require(known, "Unknown storage call")
        m._recipe_args(call["op"], call["args"])

    def raw_commit(self, step: Any) -> None:
        for write in o.contract.array(step["writes"], "raw writes"):
            StorageWrite(write)

    def identity(self, step: Any) -> None:
        self.bound(step["a"])
        self.bound(step["b"])

    def inspect(self, step: Any) -> None:
        self.bound(step["bind"])

    def use(self, step: Any) -> None:
        action = step["action"]
        plain = type(action) is dict and not {"return", "start_tx"} & action.keys()
        require(plain, "Malformed use action")
        facts = program_facts(self.value, "#", [action])
        quiet = not (facts["binds"] or facts["marks"] or facts["holds"])
        require(quiet, "A use action binds nothing")
        if "tx" in step:
            require("tx" in action, "A captured Tx runs Tx operations only")
            self.bound(step["tx"])

    def subscribe(self, step: Any) -> None:
        deferred = self.listeners.get(step["listener"], {}).get("deferred", False)
        fresh = deferred is True and step["listener"] not in self.subscribed
        require(fresh, "Subscribe of an unknown listener")
        self.subscribed.add(step["listener"])

    def unsubscribe(self, step: Any) -> None:
        require(
            step["listener"] in self.subscribed, "Unsubscribe of an unknown listener"
        )
        self.subscribed.discard(step["listener"])

    def await_end(self, step: Any) -> None:
        require(
            self.window is None and not self.unjoined, "await_end inside a close window"
        )
        self.ended = True

    def finish(self) -> None:
        settled = set(self.handles) - self.awaited <= self.settled
        require(self.ended and settled, "Unsettled handle")
        require(self.holds <= self.released, "Held program never released")
        gates = all(not g["hold"] or g["released"] for g in self.gates.values())
        require(gates, "Held gate never released")
        close = not self.close.get("hold") or self.close["released"]
        require(close, "Held backend close never released")
        aborts = {g["abort"] for g in self.gates.values()} - {None}
        cancellable = all(self.jobs.get(j, {}).get("context") for j in aborts)
        require(cancellable, "Abort of a non-cancellable job")


def _plan(value: Any, scenario: Any) -> _Planner:
    fields(scenario, SHAPES["scenario"], "scenario")
    _name(scenario["pi_anchor"], "Pi anchor")
    modes = scenario["modes"] in (["sync", "async"], ["async"])
    require(modes, "Unknown scenario modes")
    planner = _Planner(value, scenario)
    steps = o.contract.array(scenario["steps"], "steps")
    last = steps[-1] if steps else None
    require(type(last) is dict and last.get("op") == "await_end", "Missing await_end")
    for step in steps:
        require(type(step) is dict and step.get("op") in STEPS, "Unknown recipe step")
        required, optional = STEPS[step["op"]]
        fields(step, ({"id", "op"} | required, optional), "step " + step["op"])
        _name(step["id"], "step ID")
        planner.check(step["op"])
        getattr(planner, step["op"])(step)
    require(len({s["id"] for s in steps}) == len(steps), "Duplicate step IDs")
    planner.finish()
    return planner


def _docs(value: Any) -> None:
    for name, doc in value.items():
        _name(name, "document name")
        owned = type(doc) is dict and doc.get("scope") == "conversation"
        keys = {"kind", "version", "scope", "initial"}
        shape(doc, keys | ({"history", "fork"} if owned else set()), "document spec")
        history, fork = doc.get("history"), doc.get("fork")
        REAL.tx.Doc(doc["kind"], doc["version"], doc["scope"], dict, history, fork)
        require(type(doc["initial"]) is dict, "Document initial must be an object")
        _literal(doc["initial"])


def _cells(value: Any) -> None:
    require(type(value) is dict and tuple(value) == CELLS, "Cell map identity drift")
    paired: set[str] = set()
    for cell in value.values():
        fields(cell, SHAPES["cell"], "cell")
        names = _names(cell["paired"], "paired scenarios")
        native = _names(cell["native"], "native evidence")
        docs = _names(cell.get("docs", []), "documentation links")
        require(set(names) <= set(SCENARIOS), "Unknown paired scenario")
        refs = [ref.partition("::") for ref in native]
        require(
            all(f in NATIVE_FILES and n for f, _, n in refs),
            "Malformed native evidence",
        )
        why = type(cell["why"]) is str and bool(cell["why"])
        require(why and bool(names or native or docs), "Empty cell")
        paired |= set(names)
    require(paired == set(SCENARIOS), "Scenario outside the cell map")


def _registered(node: ast.AST) -> str | None:
    """The literal name passed to a ``scenario(...)`` registration call."""
    if not isinstance(node, ast.Call) or not node.args:
        return None
    func, first = node.func, node.args[0]
    named = isinstance(func, ast.Name) and func.id == "scenario"
    if named and isinstance(first, ast.Constant) and type(first.value) is str:
        return first.value
    return None


def native_names() -> dict[str, set[str]]:
    """Scenario registrations and test functions of the native suites, by ``ast``."""
    result: dict[str, set[str]] = {}
    for name in NATIVE_FILES:
        tree = ast.parse((ROOT / "tests/durable" / name).read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        tests = {f.name for f in functions if f.name.startswith("test_")}
        found = {_registered(node) for node in ast.walk(tree)} - {None}
        result[name] = tests | {str(x) for x in found}
    return result


def cell_evidence(value: Any) -> None:
    """Every native reference resolves and every documentation link exists."""
    names = native_names()
    for cell in value["cells"].values():
        for ref in cell["native"]:
            file, _, name = ref.partition("::")
            require(name in names[file], "Dangling native evidence: " + ref)
        for link in cell.get("docs", []):
            o.acceptance.local_link(link, ROOT, o.contract)


def recipe(value: Any) -> Any:
    keys = {"schema_version", "phase", "clock", "errors", "docs", "cells", "programs"}
    shape(value, keys | {"scenarios"}, "session recipe")
    version = value["schema_version"]
    ok = type(version) is int and version == 1 and value["phase"] == PHASE
    require(ok, "Unsupported session recipe")
    safe_integer(value["clock"])
    errors = o.contract.array(value["errors"], "scripted errors")
    for item in errors:
        shape(item, {"id", "kind", "message"}, "scripted error")
        typed = item["kind"] in {"error", "request"} and type(item["message"]) is str
        require(typed, "Malformed scripted error")
    _names([e["id"] for e in errors], "scripted error IDs")
    _docs(value["docs"])
    programs = value["programs"]
    require(type(programs) is dict and bool(programs), "Missing programs")
    for name in programs:
        program_facts(value, name)
    _cells(value["cells"])
    scenarios = o.contract.array(value["scenarios"], "scenarios")
    ids = [s.get("id") if type(s) is dict else None for s in scenarios]
    require(ids == list(SCENARIOS), "Session scenario identity drift")
    for scenario in scenarios:
        _plan(value, scenario)
    return value


# -- traces, projection and comparison --------------------------------------------------


def _value(item: Any) -> None:
    if item in (UNDEFINED, ["draft"]):
        return
    if type(item) is list and len(item) == 2 and item[0] == "array":
        for child in o.contract.array(item[1], "array value"):
            _value(child)
        return
    OwnedJson(v.decode(item))


def _ref(value: Any, errors: set[str], depth: int = 0) -> None:
    require(type(value) is dict and value.get("kind") in REFS, "Malformed REF")
    kind = value["kind"]
    required, optional = REFS[kind]
    fields(value, ({"kind"} | required, optional), kind + " REF")
    typed = all(type(value[k]) is str for k in required - {"cause"})
    require(typed, "Malformed REF field")
    known = (
        (kind != "scripted" or value["id"] in errors)
        and (kind != "read-after-write" or value["method"] in READ_METHODS)
        and (kind != "unclassified" or value["side"] in {"node", "python"})
    )
    require(known, "Unknown REF value")
    if kind == "session-failed":
        split = value.get("cause_split", True) is True
        require(depth < 4 and split, "Malformed session-failed REF")
        _ref(value["cause"], errors, depth + 1)


def _outcome(value: Any, errors: set[str], python: bool) -> None:
    status = value.get("status") if type(value) is dict else None
    payload = {"fulfilled": {"value"}, "rejected": {"error"}}.get(status, set())
    shape(value, {"status"} | payload, "outcome")
    if status == "fulfilled":
        _value(value["value"])
    elif status == "rejected":
        _ref(value["error"], errors)
    else:
        require(python and status == "unsettled", "Unknown outcome status")


def _change(value: Any) -> None:
    kind = value.get("type") if type(value) is dict else None
    if kind != "document":
        shape(value, {"type", "value"}, "table change")
        tables = {"conversation", "entry", "task", "submission"}
        ok = kind in tables and type(v.decode(value["value"])) is dict
        require(ok, "Malformed table change")
        return
    rest = {"retired"} if "retired" in value else {"version", "value"}
    shape(value, {"type", "record", "conversation", "ops"} | rest, "document change")
    _value(value["record"])
    _value(value["conversation"])
    _value(value["ops"])
    try:
        own_operations(v.decode(value["ops"]))
    except (TypeError, ValueError):
        require(False, "Malformed document operations")
    if "retired" in value:
        require(v.decode(value["ops"]) == [], "Retirement has operations")
        require(value["retired"] is True, "Malformed retirement")
        return
    safe_integer(v.decode(value["version"]))
    require(type(v.decode(value["value"])) is dict, "Malformed document value")


def _effect(effect: Any, ctx: dict[str, Any]) -> None:
    """Closed effect shapes; payloads and names are checked against the recipe."""
    kind = effect.get("e") if type(effect) is dict else None
    require(kind in EFFECT_SHAPES, "Unknown effect")
    fields(effect, EFFECT_SHAPES[kind], kind + " effect")
    if "status" in effect:
        rejected = effect["status"] == "rejected"
        status = effect["status"] in {"fulfilled", "rejected"}
        require(status and ("error" in effect) == rejected, "Malformed effect status")
        require(kind != "return" or ("value" in effect) != rejected, "Malformed return")
    if "op" in effect:
        gates = ctx["gates"] | {None}
        require(effect["op"] in BACKEND_OPS and effect["gate"] in gates, "Unknown call")
    if "error" in effect:
        _ref(effect["error"], ctx["errors"])
    for key in ("value", "args"):
        if key in effect:
            _value(effect[key])
    names = {
        "listener": ctx["commit"] if kind == "publish" else ctx["close"],
        "doc": ctx["docs"],
        "mark": ctx["marks"] if kind == "mark" else ctx["caught"],
    }
    known = all(effect[key] in names[key] for key in names if key in effect)
    require(known, "Unknown effect name")
    if kind == "report":
        typed = type(effect["failure_visible"]) is bool
        require(typed and effect["type"] in REPORTS, "Malformed report")
    if kind == "publish":
        require(type(effect["handle_settled"]) is bool, "Malformed publish")
        safe_integer(v.decode(effect["seq"]))
        for change in o.contract.array(effect["changes"], "changes"):
            _change(change)


REPORTS = {"session_failed", "listener_error"}


def _step(step: Any, spec: Any, ctx: dict[str, Any], python: bool) -> None:
    op = spec["op"]
    same = type(step) is dict and step.get("id") == spec["id"] and step.get("op") == op
    require(same, "Trace step identity drift")
    if op in OUTCOME_STEPS or (op == "snapshot" and "handle" not in spec):
        shape(step, {"id", "op", "outcome"}, "trace step " + op)
        _outcome(step["outcome"], ctx["errors"], python)
    elif op in BARRIERS:
        shape(step, {"id", "op", "reached"}, "trace barrier")
        reached = type(step["reached"]) is bool and (python or step["reached"])
        require(reached, "Unreached reference barrier")
    elif op == "observe":
        shape(step, {"id", "op", "state"}, "trace observation")
        state = shape(
            step["state"], {"pending", "failure", "reports"}, "observed state"
        )
        pending = _names(state["pending"], "pending handles")
        inside = python or set(pending) <= set(spec["blocked_by"])
        require(pending == sorted(pending) and inside, "Malformed pending handles")
        count = type(state["reports"]) is int and state["reports"] >= 0
        require(count, "Malformed report count")
        if state["failure"] is not None:
            _ref(state["failure"], ctx["errors"])
    else:
        shape(step, {"id", "op"}, "trace step " + op)


def _context(script: Any, scenario: Any) -> dict[str, Any]:
    starts = [s for s in scenario["steps"] if s["op"] in {"start", "call"}]
    facts = [program_facts(script, s["program"]) for s in starts]
    listeners = {item["id"]: item["kind"] for item in scenario.get("listeners", [])}
    return {
        "errors": {e["id"] for e in script["errors"]},
        "gates": {s["gate"] for s in scenario["steps"] if s["op"] == "arm"},
        "commit": {key for key, kind in listeners.items() if kind != "close"},
        "close": {key for key, kind in listeners.items() if kind == "close"},
        "docs": set(script["docs"]),
        "marks": {mark for f in facts for mark in f["marks"]},
        "caught": {mark for f in facts for mark in f["caught"]},
    }


def _row(row: Any, scenario: Any, script: Any, python: bool) -> None:
    shape(row, {"id", "steps", "effects", "end"}, "trace scenario")
    ctx = _context(script, scenario)
    steps = o.contract.array(row["steps"], "trace steps")
    require(len(steps) == len(scenario["steps"]), "Incomplete trace steps")
    for step, spec in zip(steps, scenario["steps"], strict=True):
        _step(step, spec, ctx, python)
    effects = o.contract.array(row["effects"], "effects")
    for effect in effects:
        _effect(effect, ctx)
    used = sorted(e["gate"] for e in effects if e["e"] == "call" and e["gate"])
    require(
        python or used == sorted(ctx["gates"]), "Armed gate not consumed exactly once"
    )
    end = row["end"]
    failed = type(end) is dict and end.get("reason") == "failed"
    shape(end, {"reason", "error"} if failed else {"reason"}, "end")
    if failed:
        _ref(end["error"], ctx["errors"])
    closed = failed or end["reason"] == "closed"
    require(closed or (python and end["reason"] == "unsettled"), "Unknown end reason")


def trace(value: Any) -> Any:
    python = type(value) is dict and value.get("backend") == "python"
    keys = {"schema_version", "phase", "backend", "scenarios"}
    shape(value, keys | ({"mode"} if python else set()), "session trace")
    version = value["schema_version"]
    supported = (
        type(version) is int
        and version == 1
        and value["phase"] == PHASE
        and value["backend"] in {"node", "python"}
        and (not python or value["mode"] in MODES)
    )
    require(supported, "Unsupported session trace")
    script = recipe(load(INPUT))
    mode = value.get("mode")
    expected = [s for s in script["scenarios"] if not python or mode in s["modes"]]
    rows = o.contract.array(value["scenarios"], "trace scenarios")
    ids = [r.get("id") if type(r) is dict else None for r in rows]
    require(ids == [s["id"] for s in expected], "Trace scenario identity drift")
    for row, scenario in zip(rows, expected, strict=True):
        _row(row, scenario, script, python)
    return value


def projection(value: Any, mode: str) -> Any:
    """The scenarios of one native mode; backend and mode labels dropped."""
    require(mode in MODES, "Unknown native mode")
    result = json.loads(json.dumps(trace(value)))
    modes = {s["id"]: s["modes"] for s in load(INPUT)["scenarios"]}
    result["scenarios"] = [s for s in result["scenarios"] if mode in modes[s["id"]]]
    result["backend"] = "session"
    result.pop("mode", None)
    return result


def comparison(expected: Any, actual: Any) -> int:
    """Node or native traces; two native traces must share one mode."""
    trace(expected)
    trace(actual)
    named = {t["mode"] for t in (expected, actual) if t["backend"] == "python"}
    require(len(named) <= 1, "Native mode drift")
    modes = sorted(named) or list(MODES)
    same = all(projection(expected, x) == projection(actual, x) for x in modes)
    return 0 if same else 1


# -- native driver ------------------------------------------------------------------


class ScriptedError(Exception):
    """Driver-local scripted failure; identity is the comparison key."""


def _wire(value: Any) -> Any:
    return UNDEFINED if value is None else v.encode(value)


def _walk(node: Any, path: list[Any]) -> Any:
    for key in path:
        node = node[key]
    return node


def _put(node: Any, path: list[Any], value: Any) -> None:
    _walk(node, path[:-1])[path[-1]] = value


def _recorded(method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """The argument record the Node driver writes for the same Storage call."""
    if method == "commit":
        return {"writes": [write.to_tree() for write in args[0]]}
    if method.startswith("scan_"):
        return g._scan_args(*args)
    if method in {"find_document", "document"}:
        if method == "find_document":
            return {"address": args[0].to_tree(), "at": args[1]}
        return {"id": args[0], "at": args[1]}
    if method in {"find_latest_head_marker", "submission_by_request"}:
        key = "bound" if method == "find_latest_head_marker" else "request_id"
        extra = {} if args[1] is None else {key: args[1]}
        return {"conversation_id": args[0]} | extra
    if method == "entry":
        scoped = kwargs.get("conversation_id")
        return {"id": args[0]} | ({} if scoped is None else {"conversation_id": scoped})
    return {} if method == "mint_id" else {"id": args[0]}


class _Directive:
    __slots__ = ("entered", "error", "gate", "hold", "job", "kind", "release")

    def __init__(self, gate: Any, hold: bool, kind: str, error: Any, job: Any) -> None:
        self.gate, self.hold, self.kind, self.error, self.job = (
            gate,
            hold,
            kind,
            error,
            job,
        )
        self.entered = threading.Event()
        self.release = threading.Event()


class ControlledBackend:
    """Storage over a fresh real native MemoryStorage: per-operation FIFO directives.

    Every Storage method is the same wrapper (``STORAGE_OPS``); the logged
    arguments mirror the Node driver's record of the same call.
    """

    def __init__(self, run: _Run) -> None:
        self.inner = MemoryStorage()
        self._run = run
        self._lock = threading.Lock()
        self.effects: list[Any] = []
        self.queues: dict[str, deque[_Directive]] = {}
        self.close_directive: _Directive | None = None

    def __getattr__(self, method: str) -> Any:
        if method not in STORAGE_OPS:
            raise AttributeError(method)
        return partial(self._dispatch, method)

    def log(self, item: Any) -> None:
        with self._lock:
            self.effects.append(item)

    def snapshot(self) -> list[Any]:
        with self._lock:
            return json.loads(json.dumps(self.effects))

    def blocked(self) -> bool:
        """A held backend call or close is entered and not yet released."""
        held = [d for d in [*self._run.gates.values(), self.close_directive] if d]
        return any(
            d.hold and d.entered.is_set() and not d.release.is_set() for d in held
        )

    def _hold(self, directive: _Directive) -> None:
        directive.entered.set()
        if not directive.release.wait(self._run.wait * 4):
            self._run.fault("held backend call was never released")

    def _dispatch(self, method: str, *args: Any, **kwargs: Any) -> Any:
        op = STORAGE_OPS[method]
        record = v.encode(_recorded(method, args, kwargs))
        with self._lock:
            queue = self.queues.get(op)
            directive = queue.popleft() if queue else None
            gate = None if directive is None else directive.gate
            self.effects.append({"e": "call", "op": op, "gate": gate, "args": record})
        returned = {"e": "return", "op": op, "gate": gate}
        try:
            self._scripted(directive, before=True)
            result = getattr(self.inner, method)(*args, **kwargs)
            self._scripted(directive, before=False)
        except BaseException as error:  # noqa: BLE001 - logged and re-raised unchanged
            rejected = {"status": "rejected", "error": self._run.ref(error)}
            self.log(returned | rejected)
            raise
        self.log(returned | {"status": "fulfilled", "value": _wire(m._dto(result))})
        return result

    def _scripted(self, directive: _Directive | None, *, before: bool) -> None:
        if directive is None:
            return
        if before and directive.hold:
            self._hold(directive)
        if before and directive.kind == "abort_then_throw":
            self._run.latches[directive.job].cancel()
        throwing = {"throw_before", "abort_then_throw"} if before else {"throw_after"}
        if directive.kind in throwing:
            raise directive.error

    def close(self) -> None:
        directive = self.close_directive
        self.log({"e": "close-enter"})
        try:
            if directive is not None and directive.hold:
                self._hold(directive)
            self.inner.close()
        except BaseException as error:  # noqa: BLE001 - logged and re-raised unchanged
            rejected = {"status": "rejected", "error": self._run.ref(error)}
            self.log({"e": "close-exit"} | rejected)
            raise
        self.log({"e": "close-exit", "status": "fulfilled"})


class _Handle:
    """One awaited thing: a job, snapshot, closer, off-line call or Tx operation."""

    __slots__ = ("aw", "bind", "result", "task", "thread")

    def __init__(self, bind: str | None = None) -> None:
        self.bind = bind
        self.result: tuple[str, Any] | None = None
        self.thread: threading.Thread | None = None
        self.task: asyncio.Task[None] | None = None
        self.aw: Any = None

    @property
    def settled(self) -> bool:
        return self.result is not None


def drive(body: Coroutine[Any, Any, Any]) -> Any:
    """Run a sync-mode coroutine that never suspends (its waits block)."""
    try:
        body.send(None)
    except StopIteration as stop:
        return stop.value
    body.close()
    raise o.EvidenceError("Sync-mode driver step suspended")


def _scan(name: str) -> Callable[[Any, Any, Any], Any]:
    def call(r: Any, tx: Any, op: Any) -> Any:
        return getattr(tx, name)(QUERIES[name](r.value(op["query"])), op["limit"])

    return call


def _create_task(r: Any, tx: Any, op: Any) -> Any:
    names = (("conversation_id", "conversation"), ("background", "background"))
    options = {key: op[field] for key, field in names if field in op}
    options["ownership"] = r.value(op["ownership"])
    checkpoint = copy.deepcopy(op["checkpoint"])
    return tx.create_task(
        op["kind"], op["version"], r.value(op["input"]), checkpoint, **options
    )


def _document(name: str) -> Callable[[Any, Any, Any], Any]:
    def call(r: Any, tx: Any, op: Any) -> Any:
        return getattr(tx, name)(
            r.docs[op["doc"]], conversation_id=op.get("conversation")
        )

    return call


TX_CALLS: dict[str, Callable[[Any, Any, Any], Any]] = (
    {
        "conversation": lambda r, tx, op: tx.conversation(op["id"]),
        "entry": lambda r, tx, op: tx.entry(op["id"]),
        "task": lambda r, tx, op: tx.task(op["id"]),
        "submission": lambda r, tx, op: tx.submission(op["id"]),
        "submission_by_request": lambda r, tx, op: tx.submission_by_request(
            op["conversation"], op["request"]
        ),
        "latest_head_marker": lambda r, tx, op: tx.latest_head_marker(
            op["conversation"]
        ),
        "create_conversation": lambda r, tx, op: tx.create_conversation(
            r.value(op["ownership"])
        ),
        "append_entry": lambda r, tx, op: tx.append_entry(
            op["conversation"], r.value(op["draft"])
        ),
        "create_task": _create_task,
        "create_submission": lambda r, tx, op: tx.create_submission(
            r.value(op["create"])
        ),
        "settle_submission": lambda r, tx, op: tx.settle_submission(
            op["id"], r.value(op["settlement"])
        ),
        "place_submission": lambda r, tx, op: tx.place_submission(
            op["id"], op["entry"]
        ),
        "set_task": lambda r, tx, op: tx.set_task(r.value(op["record"])),
    }
    | {name: _scan(name) for name in QUERIES}
    | {name: _document(name) for name in ("doc", "retire_doc")}
)
SIMPLE_OPS: dict[str, Callable[[Any, Any, Any], Any]] = {
    "draft_set": lambda r, op, tx: _put(
        r.binds[op["draft_set"]], op["path"], r.value(op["value"])
    ),
    "draft_push": lambda r, op, tx: _walk(r.binds[op["draft_push"]], op["path"]).extend(
        [r.value(x) for x in op["values"]]
    ),
    "draft_pop": lambda r, op, tx: _walk(r.binds[op["draft_pop"]], op["path"]).pop(),
    "draft_get": lambda r, op, tx: r.bind(
        op, _walk(r.binds[op["draft_get"]], op["path"])
    ),
    "bind_tx": lambda r, op, tx: r.binds.update({op["bind_tx"]: tx}),
    "local": lambda r, op, tx: r.binds.update(
        {op["local"]: copy.deepcopy(op["value"])}
    ),
    "mutate_local": lambda r, op, tx: _put(
        r.binds[op["mutate_local"]], op["path"], copy.deepcopy(op["value"])
    ),
    "mark": lambda r, op, tx: r.mark(op["mark"]),
}


def _fulfilled(value: Any) -> dict[str, Any]:
    return {"outcome": {"status": "fulfilled", "value": value}}


def _release(r: Any, s: Any) -> None:
    event = r.event("hold", s["hold"]) if "hold" in s else r.gates[s["gate"]].release
    event.set()


SYNC_STEPS: dict[str, Callable[[Any, Any], Any]] = {
    "start": lambda r, s: r.job(s),
    "cancel": lambda r, s: r.latches[s["job"]].cancel(),
    "release": _release,
    "arm": lambda r, s: r.arm(s),
    "arm_close": lambda r, s: r.arm_close(s),
    "release_close": lambda r, s: r.backend.close_directive.release.set(),
    "close_start": lambda r, s: r.close_start(s),
    "raw_commit": lambda r, s: _fulfilled(
        v.encode(r.backend.inner.commit(tuple(StorageWrite(w) for w in s["writes"])))
    ),
    "identity": lambda r, s: _fulfilled(
        ["bool", r.binds.get(s["a"]) is r.binds.get(s["b"])]
    ),
    "inspect": lambda r, s: _fulfilled(r.out(r.binds.get(s["bind"]))),
    "subscribe": lambda r, s: r.subscribe(r.listener(s["listener"])),
    "unsubscribe": lambda r, s: r.listeners[s["listener"]](),
}


class _Run:
    """One scenario in one native mode over a fresh backend and Session."""

    def __init__(
        self, M: Any, value: Any, scenario: Any, mode: str, wait: float
    ) -> None:
        self.M, self.mode, self.sync, self.wait = M, mode, mode == "sync", wait
        self.name, self.scenario = scenario["id"], scenario
        self.programs = value["programs"]
        self.errors: dict[str, BaseException] = {
            item["id"]: (
                StorageRequestError if item["kind"] == "request" else ScriptedError
            )(item["message"])
            for item in value["errors"]
        }
        self.kinds = {
            M.core.CommitCancelled: "commit-cancelled",
            M.tx.DraftRevoked: "draft-revoked",
            M.tx.TransactionSettled: "tx-settled",
            M.tx.ReadAfterWrite: "read-after-write",
            StorageRequestError: "request",
            M.session.SessionClosedError: "session-closed",
            M.tx.PendingTxOperations: "pending-ops",
            RuntimeError: "error",
        }
        self.faults: list[str] = []
        self.loop_reports: list[Any] = []
        self.backend = ControlledBackend(self)
        self.handles: dict[str, _Handle] = {}
        self.binds: dict[str, Any] = {}
        self.events: dict[tuple[str, str], Any] = {}
        self.gates: dict[str, _Directive] = {}
        self.latches: dict[str, Any] = {}
        self.listeners: dict[str, Callable[[], None]] = {}
        steps = scenario["steps"]
        self.closers = {s["handle"] for s in steps if s["op"] == "close_start"}
        self.current: str | None = None
        self.threads: list[threading.Thread] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.end: Any = None
        self.effects: Any = None
        clock = value["clock"]
        self.docs = {
            name: self._doc(name, item) for name, item in value["docs"].items()
        }
        self.session = M.core.Session(
            self.backend, reporter=self.report, now=lambda: clock
        )

    # -- primitives ------------------------------------------------------------------

    def fault(self, message: str) -> None:
        self.faults.append(message)

    def _doc(self, name: str, item: Any) -> Any:
        def factory() -> Any:
            self.backend.log({"e": "init", "doc": name})
            return copy.deepcopy(item["initial"])

        semantics = {k: item[k] for k in ("history", "fork") if k in item}
        kind, version, scope = item["kind"], item["version"], item["scope"]
        return self.M.tx.Doc(kind, version, scope, factory, **semantics)

    def event(self, table: str, name: str) -> Any:
        if (table, name) not in self.events:
            event = threading.Event() if self.sync else asyncio.Event()
            self.events[table, name] = event
        return self.events[table, name]

    async def until(self, predicate: Callable[[], bool]) -> bool:
        """Bounded wait for an owned-state witness; the poll is not evidence."""
        deadline = time.monotonic() + self.wait
        while not predicate():
            if time.monotonic() > deadline:
                return False
            if self.sync:
                time.sleep(0.001)
            else:
                await asyncio.sleep(0.001)
        return True

    async def wait_event(self, event: Any) -> bool:
        if self.sync:
            return bool(event.wait(self.wait))
        try:
            await asyncio.wait_for(event.wait(), self.wait)
        except TimeoutError:
            return False
        return True

    def ref(self, error: object, depth: int = 0) -> Any:
        for key, item in self.errors.items():
            if error is item:
                return {"kind": "scripted", "id": key}
        kind = type(error)
        if depth < 4 and kind is self.M.session.SessionFailed:
            cause = getattr(error, "cause", None)
            split = cause is not getattr(error, "__cause__", None)
            caused = {"cause": self.ref(cause, depth + 1)}
            failed = {"kind": "session-failed", "message": str(error)} | caused
            return failed | ({"cause_split": True} if split else {})
        label = self.kinds.get(kind)
        if label is None:
            unknown = {"side": "python", "name": kind.__name__, "message": str(error)}
            return {"kind": "unclassified"} | unknown
        if label == "read-after-write":
            return {"kind": label, "method": getattr(error, "method", "")}
        carries = "message" in REFS[label][0]
        return {"kind": label} | ({"message": str(error)} if carries else {})

    def report(self, error: BaseException, kind: str) -> None:
        visible = self.session.failure is not None
        report = {"e": "report", "error": self.ref(error), "type": kind}
        self.backend.log(report | {"failure_visible": visible})

    def out(self, value: Any) -> Any:
        """Transport of an outcome value; ``None`` is Pi's ``undefined``."""
        tx = self.M.tx
        if value is None:
            return UNDEFINED
        if type(value) in {tx.Draft, tx.DraftList}:
            return ["draft"]
        if type(value) is list:
            return ["array", [self.out(item) for item in value]]
        if type(value) in {tx.CommittedDocument, OwnedJson}:
            return v.encode(self.bound(value).thaw())
        dto = isinstance(value, Record) or type(value) is Page
        return v.encode(m._dto(value) if dto else value)

    def bound(self, value: Any) -> Any:
        return value.value if type(value) is self.M.tx.CommittedDocument else value

    def change(self, item: Any) -> Any:
        if type(item) is StorageWrite:
            tree = item.to_tree()
            return {"type": tree["type"], "value": v.encode(tree["value"])}
        known = type(item) is self.M.tx.RawDocumentChange
        require(known, "Unknown publication change")
        record = v.encode(item.record.to_tree())
        base = {"type": "document", "record": record}
        base["conversation"] = _wire(item.conversation_id)
        base["ops"] = v.encode(item.ops.thaw())
        if item.value is None:
            require(item.version is None, "Retired change with a version")
            return base | {"retired": True}
        value = v.encode(item.value.thaw())
        return base | {"version": v.encode(item.version), "value": value}

    def listener(self, key: str) -> Any:
        return next(x for x in self.scenario["listeners"] if x["id"] == key)

    # -- handles --------------------------------------------------------------------

    def start(
        self, name: str, call: Callable[[], Any], bind: str | None = None
    ) -> None:
        """Start a blocking call (sync: owned thread) or awaitable (async: task)."""
        handle = self.handles[name] = _Handle(bind)
        if not self.sync:
            assert self.loop is not None
            handle.aw = call()
            handle.task = self.loop.create_task(self._capture(handle))
            return

        def body() -> None:
            try:
                handle.result = ("fulfilled", call())
            except BaseException as error:  # noqa: BLE001 - recorded outcome
                handle.result = ("rejected", error)

        label = f"de02b3-{self.name}-{name}"
        handle.thread = threading.Thread(target=body, name=label, daemon=False)
        self.threads.append(handle.thread)
        handle.thread.start()

    async def _capture(self, handle: _Handle) -> None:
        try:
            handle.result = ("fulfilled", await handle.aw)
        except BaseException as error:  # noqa: BLE001 - recorded outcome
            handle.result = ("rejected", error)

    async def join(self, handle: _Handle) -> None:
        if handle.thread is not None:
            handle.thread.join(self.wait)
        elif handle.task is not None:
            await asyncio.wait({handle.task}, timeout=self.wait)

    async def settle(self, name: str) -> Any:
        handle = self.handles[name]
        await self.join(handle)
        if handle.result is None:
            return {"status": "unsettled"}
        status, value = handle.result
        if status == "rejected":
            return {"status": "rejected", "error": self.ref(value)}
        if handle.bind is not None:
            self.binds[handle.bind] = self.bound(value)
        return {"status": "fulfilled", "value": self.out(value)}

    def owns(self, ticket: Any, handle: _Handle) -> bool:
        if handle.thread is not None:
            return bool(ticket.thread == handle.thread.ident)
        return bool(ticket.tasks) and ticket.tasks[0] is handle.task

    # -- the callback-program interpreter --------------------------------------------

    def value(self, item: Any) -> Any:
        """Fresh literal containers; ``$local``/``$bind`` pass the bound object itself."""
        if type(item) is list:
            return [self.value(x) for x in item]
        if type(item) is not dict:
            return item
        if len(item) == 1 and next(iter(item)) in REF_KEYS:
            return self.binds.get(next(iter(item.values())))
        return {key: self.value(x) for key, x in item.items()}

    def bind(self, op: Any, result: Any) -> None:
        if "bind" in op:
            self.binds[op["bind"]] = result

    def mark(self, name: str) -> None:
        self.backend.log({"e": "mark", "mark": name})
        self.event("mark", name).set()

    async def exec(self, op: Any, tx: Any) -> None:
        head = _head(op)
        if head in SIMPLE_OPS:
            SIMPLE_OPS[head](self, op, tx)
        else:
            await getattr(self, "do_" + head)(op, tx)

    async def do_tx(self, op: Any, tx: Any) -> None:
        if "start_tx" in op:
            call = TX_CALLS[op["start_tx"]]
            self.start(op["handle"], lambda: call(self, tx, op))
            return
        result = TX_CALLS[op["tx"]](self, tx, op)
        if not self.sync and op["tx"] not in SYNC_OPS:
            result = await result
        self.bind(op, result)

    async def do_await(self, op: Any, tx: Any) -> None:
        self.bind(op, await self.handles[op["await"]].aw)

    async def do_catch(self, op: Any, tx: Any) -> None:
        try:
            for inner in op["catch"]:
                await self.exec(inner, tx)
        except Exception as error:  # noqa: BLE001 - the program's own catch
            caught = {"e": "caught", "mark": op["mark"], "error": self.ref(error)}
            self.backend.log(caught)

    async def do_hold(self, op: Any, tx: Any) -> None:
        if not await self.wait_event(self.event("hold", op["hold"])):
            self.fault("program hold was never released")

    async def do_throw(self, op: Any, tx: Any) -> None:
        raise self.errors[op["throw"]]

    async def program(self, name: str, job: str, tx: Any) -> Any:
        self.current = job
        for op in self.programs[name]:
            if "return" not in op:
                await self.exec(op, tx)
                continue
            spec = op["return"]
            if "value" in spec:
                return copy.deepcopy(spec["value"])
            if "binds" in spec:
                return [self.binds.get(key) for key in spec["binds"]]
            bound = self.binds.get(spec["bind"])
            return getattr(bound, spec["key"]) if "key" in spec else bound
        return None

    # -- listeners ------------------------------------------------------------------

    def _act(self, spec: Any) -> Any:
        if spec["action"] == "throw":
            raise self.errors[spec["error"]]
        if spec["action"] != "reject":
            return None
        assert self.loop is not None
        future = self.loop.create_future()
        future.set_exception(self.errors[spec["error"]])
        return future

    def _publication(self, spec: Any, publication: Any) -> Any:
        changes = [self.change(item) for item in publication.changes]
        current = self.handles.get(self.current or "")
        settled = current is not None and current.settled
        publish = {
            "e": "publish",
            "listener": spec["id"],
            "seq": v.encode(publication.seq),
        }
        self.backend.log(publish | {"changes": changes, "handle_settled": settled})
        documents = (c for c in publication.changes if type(c) is not StorageWrite)
        values = [c.value for c in documents if c.value is not None]
        if "bind" in spec:
            self.binds[spec["bind"]] = values[0] if values else None
        if "hit" in spec:
            hit = spec["hit"]
            raw, conversation = self.docs[hit["doc"]], hit.get("conversation")
            loaded = self.session.snapshot(raw, conversation_id=conversation)
            self.binds[hit["bind"]] = self.bound(loaded)
        return self._act(spec)

    def _closing(self, spec: Any) -> Any:
        self.backend.log({"e": "close-listener", "listener": spec["id"]})
        return self._act(spec)

    def subscribe(self, spec: Any) -> None:
        session = self.session
        if spec["kind"] == "close":
            dispose = session.subscribe_close(lambda: self._closing(spec))
        else:
            internal = spec["kind"] == "internal"
            add = session.observe_commits if internal else session.subscribe_commits
            dispose = add(lambda publication: self._publication(spec, publication))
        self.listeners[spec["id"]] = dispose

    # -- recipe steps ---------------------------------------------------------------

    def job(self, step: Any) -> None:
        name, program, session = step["job"], step["program"], self.session
        view = None
        if step.get("context") == "cancellable":
            latch = self.latches[name] = self.M.session.CancelLatch()
            view = latch.view()

        def change(tx: Any) -> Any:
            body = self.program(program, name, tx)
            return drive(body) if self.sync else body

        commit = session.commit if self.sync else session.acommit
        self.start(name, lambda: commit(change, cancel=view))

    def arm(self, step: Any) -> None:
        outcome = step["outcome"]
        kind = "pass" if outcome == "pass" else next(iter(outcome))
        error = None if kind == "pass" else self.errors[outcome[kind]]
        args = (step["gate"], step["hold"], kind, error, step.get("job"))
        directive = self.gates[step["gate"]] = _Directive(*args)
        self.backend.queues.setdefault(step["target"], deque()).append(directive)

    def arm_close(self, step: Any) -> None:
        self.backend.close_directive = _Directive(
            None, step["hold"], "pass", None, None
        )

    def close_start(self, step: Any) -> None:
        # Pi's close() runs the close listeners synchronously in the call.
        session = self.session
        session.request_close()
        if self.sync:
            self.start(step["handle"], lambda: session.close(timeout=self.wait * 2))
        else:
            self.start(step["handle"], session.aclose)

    async def step(self, step: Any) -> Any:
        record: dict[str, Any] = {"id": step["id"], "op": step["op"]}
        handler = SYNC_STEPS.get(step["op"])
        if handler is None:
            await getattr(self, "op_" + step["op"])(step, record)
            return record
        added = handler(self, step)
        return record | (added if type(added) is dict else {})

    async def op_call(self, step: Any, record: Any) -> None:
        self.job(step)
        record["outcome"] = await self.settle(step["job"])

    async def op_settle(self, step: Any, record: Any) -> None:
        record["outcome"] = await self.settle(step.get("job", step.get("handle")))

    async def op_await_mark(self, step: Any, record: Any) -> None:
        record["reached"] = await self.wait_event(self.event("mark", step["mark"]))

    async def op_await_queued(self, step: Any, record: Any) -> None:
        handle = self.handles[step.get("job", step.get("handle"))]
        owner = self.session._owner

        def queued() -> bool:
            # Driver-only private read of owned tickets under the owner lock:
            # identity and ``int`` comparisons only, no caller hook.
            with owner._cond:
                return any(self.owns(ticket, handle) for ticket in owner._queue)

        record["reached"] = await self.until(queued)

    async def op_await_entered(self, step: Any, record: Any) -> None:
        record["reached"] = await self.until(self.gates[step["gate"]].entered.is_set)

    async def op_await_close_entered(self, step: Any, record: Any) -> None:
        directive = self.backend.close_directive
        assert directive is not None
        record["reached"] = await self.until(directive.entered.is_set)

    async def op_fail(self, step: Any, record: Any) -> None:
        error, name = self.errors[step["error"]], "#" + step["id"]

        async def on_loop() -> None:
            self.session.fail(error)

        # Sync: an owned thread (a drained close may run inline); async: on the loop.
        self.start(name, (lambda: self.session.fail(error)) if self.sync else on_loop)
        record["outcome"] = await self.settle(name)

    async def op_quiesce(self, step: Any, record: Any) -> None:
        for name in self.closers & self.handles.keys():
            handle = self.handles[name]
            await self.until(lambda: handle.settled or self.backend.blocked())

    async def op_observe(self, step: Any, record: Any) -> None:
        names = [name for name in self.handles if not name.startswith("#")]
        for name in set(names) - set(step["blocked_by"]):
            handle = self.handles[name]
            await self.until(lambda: handle.settled)
        failure = self.session.failure
        reports = sum(e["e"] == "report" for e in self.backend.snapshot())
        record["state"] = {
            "pending": sorted(n for n in names if not self.handles[n].settled),
            "failure": None if failure is None else self.ref(failure),
            "reports": reports,
        }

    async def op_snapshot(self, step: Any, record: Any) -> None:
        raw, conversation = self.docs[step["doc"]], step.get("conversation")
        method = self.session.snapshot if self.sync else self.session.asnapshot
        name = step.get("handle", "#" + step["id"])
        self.start(
            name, lambda: method(raw, conversation_id=conversation), step.get("bind")
        )
        if "handle" not in step:
            record["outcome"] = await self.settle(name)

    async def op_guard_read(self, step: Any, record: Any) -> None:
        storage, call, name = (
            self.session._owner.storage,
            step["call"],
            "#" + step["id"],
        )

        def read() -> Any:
            return m.invoke(storage, call["op"], call["args"])

        loop = self.loop
        self.start(
            name, read if loop is None else lambda: loop.run_in_executor(None, read)
        )
        record["outcome"] = await self.settle(name)

    async def op_raw_read(self, step: Any, record: Any) -> None:
        await self.op_use({"raw": step["call"]}, record)

    async def op_use(self, step: Any, record: Any) -> None:
        try:
            if "raw" in step:
                call = step["raw"]
                result = m.invoke(self.backend.inner, call["op"], call["args"])
                value = _wire(m._dto(result))
            else:
                await self.exec(step["action"], self.binds.get(step.get("tx", "")))
                value = UNDEFINED
            record["outcome"] = {"status": "fulfilled", "value": value}
        except Exception as error:  # noqa: BLE001 - recorded outcome
            record["outcome"] = {"status": "rejected", "error": self.ref(error)}

    async def op_await_end(self, step: Any, record: Any) -> None:
        try:
            if self.sync:
                end = self.session.wait_closed(timeout=self.wait)
            else:
                end = await asyncio.wait_for(self.session.await_closed(), self.wait)
            failed = {"error": self.ref(end.error)} if end.reason == "failed" else {}
            self.end = {"reason": end.reason} | failed
        except TimeoutError:
            self.end = {"reason": "unsettled"}
        self.effects = self.backend.snapshot()

    # -- lifecycle ------------------------------------------------------------------

    async def main(self) -> list[Any]:
        if not self.sync:
            loop = self.loop = asyncio.get_running_loop()
            executor = ThreadPoolExecutor(
                EXECUTOR_WORKERS, thread_name_prefix="de02b3-exec"
            )
            loop.set_default_executor(executor)
            loop.set_exception_handler(lambda _loop, ctx: self.loop_reports.append(ctx))
        steps = []
        try:
            for spec in self.scenario.get("listeners", []):
                if not spec.get("deferred", False):
                    self.subscribe(spec)
            for item in self.scenario["steps"]:
                steps.append(await self.step(item))
        finally:
            await self.cleanup()
        return steps

    async def cleanup(self) -> None:
        for directive in [*self.gates.values(), self.backend.close_directive]:
            if directive is not None:
                directive.release.set()
        for event in self.events.values():
            event.set()
        try:
            self.session.request_close()
            if self.sync:
                self.session.wait_closed(timeout=self.wait)
            else:
                await asyncio.wait_for(self.session.await_closed(), self.wait)
        except BaseException:  # noqa: BLE001 - cleanup only; outcomes already recorded
            pass
        for handle in self.handles.values():
            await self.join(handle)
        tasks = [h.task for h in self.handles.values() if h.task is not None]
        pending = [task for task in tasks if not task.done()]
        if pending:
            self.fault("Unreaped native session task")
            for task in pending:
                task.cancel()
            await asyncio.wait(pending, timeout=self.wait)

    def execute(self) -> Any:
        steps = drive(self.main()) if self.sync else asyncio.run(self.main())
        for thread in self.threads:
            thread.join(self.wait * 3)
        alive = [thread for thread in self.threads if thread.is_alive()]
        require(not alive, "Unreaped native session thread")
        require(not self.faults, "Native driver fault: " + "; ".join(self.faults))
        complete = not self.loop_reports and self.effects is not None
        require(complete, "Native loop report or missing end")
        return {
            "id": self.name,
            "steps": steps,
            "effects": self.effects,
            "end": self.end,
        }


def native(
    value: Any,
    mods: Any = None,
    wait: float | None = None,
    modes: tuple[str, ...] = MODES,
) -> tuple[dict[str, Any], Any]:
    """Drive the native Session (or a negative-control build) in each mode."""
    recipe(value)
    M = REAL if mods is None else mods
    traces: dict[str, Any] = {}
    threads = 0
    for mode in modes:
        rows = []
        for scenario in (s for s in value["scenarios"] if mode in s["modes"]):
            run = _Run(M, value, scenario, mode, WAIT if wait is None else wait)
            rows.append(run.execute())
            threads += len(run.threads)
        head = {"schema_version": 1, "phase": PHASE, "backend": "python", "mode": mode}
        traces[mode] = head | {"scenarios": rows}
    witness = {"schema_version": 1, "phase": PHASE, "session_module": M.core.__name__}
    witness |= {
        "modes": list(modes),
        "threads_started": threads,
        "threads_reaped": threads,
    }
    return traces, witness | {"executor_workers": EXECUTOR_WORKERS}


# -- trace mutants -------------------------------------------------------------------

PENDING = "Session commit callback settled before its pending Tx operations"
FULFILLED = {"status": "fulfilled", "value": UNDEFINED}


def _rejected(kind: str, **extra: Any) -> Any:
    return {"status": "rejected", "error": {"kind": kind, **extra}}


def _fulfilled_with(value: Any) -> Any:
    return {"status": "fulfilled", "value": v.encode(value)}


def _trace_step(row: Any, op: str, **match: Any) -> Any:
    spec = next(s for s in load(INPUT)["scenarios"] if s["id"] == row["id"])
    steps = [s for s in spec["steps"] if s["op"] == op]
    ids = [s["id"] for s in steps if all(s.get(k) == x for k, x in match.items())]
    require(bool(ids), "Missing mutation step")
    return next(s for s in row["steps"] if s["id"] == ids[0])


def _at(row: Any, last: bool = False, **match: Any) -> int:
    effects = row["effects"]
    found = [
        i for i, e in enumerate(effects) if all(e.get(k) == x for k, x in match.items())
    ]
    require(bool(found), "Missing mutation effect")
    return found[-1 if last else 0]


def _set(op: str, outcome: Any, **match: Any) -> Callable[[Any], None]:
    def mutate(row: Any) -> None:
        _trace_step(row, op, **match)["outcome"] = outcome

    return mutate


def _drop(**match: Any) -> Callable[[Any], None]:
    def mutate(row: Any) -> None:
        del row["effects"][_at(row, **match)]

    return mutate


def _insert(effect: Any, after: bool, **match: Any) -> Callable[[Any], None]:
    def mutate(row: Any) -> None:
        row["effects"].insert(_at(row, **match) + after, effect)

    return mutate


def _swap(first: Any, second: Any) -> Callable[[Any], None]:
    def mutate(row: Any) -> None:
        a, b = _at(row, **first), _at(row, **second)
        row["effects"][a], row["effects"][b] = row["effects"][b], row["effects"][a]

    return mutate


def _both(*mutations: Callable[[Any], None]) -> Callable[[Any], None]:
    def mutate(row: Any) -> None:
        for mutation in mutations:
            mutation(row)

    return mutate


def _move_close(row: Any) -> None:
    moved = [row["effects"].pop(_at(row, e=e)) for e in ("close-enter", "close-exit")]
    index = _at(row, e="return", op="commit", gate="g1") + 1
    row["effects"][index:index] = moved


def _publish_early(row: Any) -> None:
    publish = row["effects"].pop(_at(row, e="publish", listener="L1"))
    row["effects"].insert(_at(row, last=True, e="return", op="commit"), publish)


def _reported_only(row: Any) -> None:
    report = row["effects"][_at(row, e="report")]
    report.update(type="listener_error", failure_visible=False)
    row["end"] = {"reason": "closed"}


def _publication_ops(row: Any) -> None:
    changes = [
        c
        for e in row["effects"]
        if e["e"] == "publish"
        for c in e["changes"]
        if c["type"] == "document" and "retired" not in c
    ]
    changed = next(c for c in changes if v.decode(c["ops"]))
    changed["ops"] = v.encode([])


def _base_instead_of_delta(row: Any) -> None:
    effect = next(
        e
        for e in row["effects"]
        if e["e"] == "call"
        and e["op"] == "commit"
        and any(w["type"] == "document.change" for w in v.decode(e["args"])["writes"])
    )
    args = v.decode(effect["args"])
    write = next(w for w in args["writes"] if w["type"] == "document.change")
    write["content"] = {
        "kind": "base",
        "version": write["content"]["version"],
        "value": {},
    }
    effect["args"] = v.encode(args)


PUBLISH = {"e": "publish", "listener": "L1", "seq": v.encode(2), "changes": []}
PUBLISH |= {"handle_settled": False}
CANCELLED = _rejected("commit-cancelled")
B_IN = {"e": "mark", "mark": "b-in"}
MUTATIONS: dict[str, tuple[str, Callable[[Any], None]]] = {
    "publication-ops-lost": ("d78-120-137-create-change-identity", _publication_ops),
    "loaded-delta-replaced-by-base": (
        "d78-120-137-create-change-identity",
        _base_instead_of_delta,
    ),
    "queued-cancel-runs-callback": (
        "queued-view-cancel",
        _both(
            _set("settle", FULFILLED, job="j2"),
            _insert(B_IN, False, e="mark", mark="c-in"),
        ),
    ),
    "running-cancel-rolls-back": (
        "view-cancel-in-callback-ignored",
        _set("settle", CANCELLED, job="j1"),
    ),
    "admitted-cancel-cuts-commit": (
        "view-cancel-during-held-commit",
        _both(_set("settle", CANCELLED, job="j1"), _drop(e="publish", seq=v.encode(2))),
    ),
    "fifo-reorder": (
        "queued-view-cancel",
        _swap({"e": "mark", "mark": "a-in"}, {"e": "mark", "mark": "c-in"}),
    ),
    "read-after-write-allowed": (
        "tb82-121-read-after-write",
        _drop(e="caught", mark="conversation"),
    ),
    "final-owner-unchecked": (
        "tb188-213-final-owner",
        _set("call", FULFILLED, job="j1"),
    ),
    "ids-reused": (
        "ids-not-reused",
        _set("call", _fulfilled_with({"id": 2}), job="j2"),
    ),
    "late-initializer": (
        "d311-no-late-init",
        _insert({"e": "init", "doc": "Late"}, True, e="return", op="find_document"),
    ),
    "escaped-draft-live": ("d207-escaped-returned-draft", _set("use", FULFILLED)),
    "pending-ops-committed": (
        "d284-pending-acquisition",
        _set("settle", FULFILLED, job="j1"),
    ),
    "callback-error-loses": (
        "d337-callback-error-wins",
        _set("settle", _rejected("pending-ops", message=PENDING), job="j1"),
    ),
    "caught-read-commits": (
        "h276-caught-read-then-return",
        _set("call", _fulfilled_with("ok"), job="j1"),
    ),
    "late-read-leak": ("late-read-discard", _set("settle", FULFILLED, job="j1")),
    "before-ack-applied": (
        "atomic-before-ack",
        _set("raw_read", _fulfilled_with({"id": 2})),
    ),
    "after-ack-adopted": ("after-ack-applied", _set("settle", FULFILLED, job="j1")),
    "publish-before-commit-return": ("commit-before-adopt-publish", _publish_early),
    "publish-on-failure": (
        "after-ack-applied",
        _insert(PUBLISH, True, e="return", op="commit", gate="g1"),
    ),
    "internal-listener-reported-only": (
        "h328-internal-listener-poisons",
        _reported_only,
    ),
    "internal-listener-rejects-commit": (
        "h328-internal-listener-poisons",
        _set("call", _rejected("scripted", id="B1"), job="j1"),
    ),
    "host-listener-skips-rest": (
        "h466-host-close-listeners-contained",
        _drop(e="publish", listener="L2"),
    ),
    "close-before-admitted": ("d632-admitted-before-close", _move_close),
    "admission-after-close": (
        "d632-admitted-before-close",
        _set("call", FULFILLED, job="j4"),
    ),
    "close-listener-after-report": (
        "fail-under-held-line",
        _swap({"e": "close-listener"}, {"e": "report"}),
    ),
    "guard-sealed-early": (
        "busy-line-guard-window",
        _set("guard_read", _rejected("session-closed", message="Session is closed")),
    ),
}
MUTANTS = tuple(MUTATIONS)


def _mutation(raw: Any, name: str) -> Any:
    """One schema-valid semantic mutant of an actual reference trace."""
    value = json.loads(json.dumps(raw))
    scenario, mutate = MUTATIONS[name]
    rows = [s for s in value["scenarios"] if s["id"] == scenario]
    require(len(rows) == 1, "Missing mutation scenario")
    mutate(rows[0])
    return trace(value)


def mutant_modes(name: str) -> list[str]:
    scenario = MUTATIONS[name][0]
    return list(
        next(s["modes"] for s in load(INPUT)["scenarios"] if s["id"] == scenario)
    )


def execute_mutants(output: Path, raw: Any, env: dict[str, str]) -> Any:
    """Each mutant as in a1/a2b/b1b: observed against itself (positive 0) and
    against the mutant (negative 1), independent of the native outcome, so a
    valid Node/native mismatch still exits 1 with its reports. A Node pair
    compares both mode projections; the permanent tests also compare every
    mutant with each native mode it runs in."""
    results = []
    child = {"PATH": env["PATH"], "HOME": env["HOME"]}
    observed = output / "observed.json"
    script = str(Path(__file__).resolve())
    for name in MUTANTS:
        path = output / (name + "-mutant.json")
        dump(path, _mutation(raw, name))
        for label, actual, wanted in (("positive", observed, 0), ("negative", path, 1)):
            identity = f"{name}-{label}"
            command = [sys.executable, script, "compare", str(observed), str(actual)]
            try:
                o.run(command, ROOT, output, identity, child, 60)
            except o.EvidenceError:
                require(wanted == 1, "Positive session comparison failed")
            code = o.load(output / (identity + ".json"))["exit_code"]
            require(
                code == wanted, "Session mutant must be valid mismatch1, never invalid2"
            )
        result = {"id": name, "scenario": MUTATIONS[name][0], "schema_valid": True}
        exits = {"positive_exit": 0, "negative_exit": 1}
        results.append(result | exits | {"sha256": o.sha(path)})
    return results


# -- manifest and run ------------------------------------------------------------------

SCOPE = (
    "Bounded paired Session comparison only: pinned SessionImpl through a driver subclass "
    "(guarded getter, recorded report) versus the private native session_core.Session in "
    "sync commit() and async acommit() modes, each over its real Memory backend; 41 closed "
    "scenarios and a closed DE02/DE02b cell map. Definitions omit checkpointWhen, so loaded "
    "nonempty changes persist actual deltas. Oracle adaptation: createTask "
    "receives a structural task-definition stub. Native-only, never paired: adoption failure, "
    "reentry, every Task.cancel row, Tx/draft affinity, coroutine listeners, bulk queued-"
    "rejection timing, background-close reporter routing, a sync commit settling while its "
    "own inline backend close is held, process-control exceptions and sync callbacks "
    "returning awaitables. Not exercised: a caller mutating an entry draft's top-level keys "
    "while the append is pending (Pi reads them after its awaits, native at the call). Not "
    "compared at all: typed documents, migrations, families, task-scope documents and "
    "terminal-task document retirement, checkpoint callbacks, forks/copies, "
    "documentState/watchDoc, retention/eviction, unloadDocuments, telemetry and listener "
    "context, Harness hooks, SQLite. No product, factory, full Session parity or P-row closure."
)
CONTEXT_DRIVER = {
    "base": "SessionImpl",
    "own_members": ["constructor", "guarded", "report"],
    "close_route": "SessionImpl.close",
    "checkpoint_when": "absent",
    "task_definition": "structural-stub",
    "sweeps": 0,
}
PARENT_KEYS = tuple(
    """baseline_manifest_sha256 a1_manifest_sha256 a2b_manifest_sha256
    b1b_manifest_sha256""".split()
)
MANIFEST_KEYS = {"schema_version", "phase", "reference_commit", "closure", "scope"}
MANIFEST_KEYS |= {"reference_sources", "upstream_tests", *PARENT_KEYS}


def parents() -> dict[str, str]:
    paths = (o.FIXTURES / "manifest.json", v.MANIFEST, m.MANIFEST, g.MANIFEST)
    return {key: o.sha(path) for key, path in zip(PARENT_KEYS, paths, strict=True)}


def configuration() -> Any:
    config = shape(load(MANIFEST), MANIFEST_KEYS, "session manifest")
    identity = [config["phase"], config["reference_commit"], config["scope"]]
    version = config["schema_version"]
    require(type(version) is int and version == 1, "Unsupported session manifest")
    require(identity == [PHASE, o.PIN, SCOPE], "Unsupported session manifest")
    o.contract.shape(config["closure"], CLOSURE, "session closed artifact inventory")
    for name, digest in config["closure"].items():
        o.contract.digest(digest)
        require(o.sha(ROOT / name) == digest, "Session artifact drift")
    for key, names in (
        ("reference_sources", SOURCES),
        ("upstream_tests", UPSTREAM_TESTS),
    ):
        o.contract.shape(config[key], names, "session pinned " + key)
        for digest in config[key].values():
            o.contract.digest(digest)
    current = all(config[key] == digest for key, digest in parents().items())
    require(current, "Parent manifest drift")
    g.configuration()
    cell_evidence(recipe(load(INPUT)))
    return config


def document_changes(raw: Any) -> int:
    """Admitted document.change writes in the reference trace; each must be a default delta."""
    commits = [
        e for row in raw["scenarios"] for e in row["effects"] if e["e"] == "call"
    ]
    writes = [
        write
        for effect in commits
        if effect["op"] == "commit"
        for write in v.decode(effect["args"])["writes"]
        if write["type"] == "document.change"
    ]
    deltas = all(write["content"]["kind"] == "delta" for write in writes)
    require(deltas, "Reference non-delta change write")
    return len(writes)


def _context_file(output: Path, raw: Any) -> None:
    keys = {"schema_version", "phase", "scenarios", "driver"}
    context = shape(load(output / "reference-context.json"), keys, "session context")
    facts = dict(context["driver"]) if type(context["driver"]) is dict else {}
    changes = facts.pop("delta_document_changes", None)
    head = [context["schema_version"], context["phase"], context["scenarios"]]
    same = head == [1, PHASE, len(SCENARIOS)] and facts == CONTEXT_DRIVER
    require(
        same and changes == document_changes(raw), "Session reference context drift"
    )
    diagnostics = load(output / "session-raw.json")
    shape(diagnostics, {"schema_version", "errors"}, "session raw diagnostics")
    require(diagnostics["schema_version"] == 1, "Session raw version drift")
    keys = {"scenario", "where", "name", "message", "stack", "constructor"}
    for item in o.contract.array(diagnostics["errors"], "raw session errors"):
        shape(item, keys, "raw session error")
        texts = all(type(item[k]) is str for k in keys - {"stack"})
        stack = item["stack"] is None or type(item["stack"]) is str
        require(
            texts and stack and item["scenario"] in SCENARIOS,
            "Malformed raw session error",
        )


def _reference(parent: Path, output: Path, config: Any, base: Any) -> tuple[Any, ...]:
    """Attest the parent, run the pinned driver and validate its evidence."""
    v.artifacts.attest(parent, o.FIXTURES)
    run = o.acceptance.attest_reference(o, parent / "reference", base, o.FIXTURES)
    acceptance = o.load(parent / "acceptance.json")
    verified = acceptance.get("status") == "acceptance-verified"
    require(
        verified and acceptance.get("complete") is True, "Incomplete parent acceptance"
    )
    parent_sha = o.sha(parent / "acceptance.json")
    snapshot = parent / "reference/snapshot"
    o.verify_sources(snapshot, config["reference_sources"])
    o.verify_sources(snapshot, config["upstream_tests"])
    require(not output.is_relative_to(parent), "Session output must be outside parent")
    output.mkdir(mode=0o700, exist_ok=False)
    output.chmod(0o700)
    driver = snapshot / DESTINATION
    fresh = not driver.exists() and not driver.is_symlink()
    require(fresh, "Session caller destination exists")
    shutil.copyfile(DRIVER, driver)
    driver.chmod(0o600)
    require(o.sha(driver) == o.sha(DRIVER), "Session caller copy drift")
    shutil.copyfile(INPUT, output / "input.json")
    env = o.child_env(output)
    for runtime in ("node", "npm"):
        version = o.run(
            [runtime, "--version"], snapshot, output, runtime + "-version", env, 15
        )
        require(
            version.decode().strip() == base["runtime"][runtime],
            "Session runtime drift",
        )
    resolver = str(snapshot / o.RESOLVER)
    command = [
        "node",
        "--import",
        resolver,
        str(driver),
        str(output / "input.json"),
        str(output),
    ]
    o.run(command, snapshot, output, "pi-session-commit", env, 120)
    sources = o.load(parent / "reference/source-sha256.json")
    resolutions = o.load(output / "resolutions.json")
    adapters = {DESTINATION: o.sha(DRIVER)}
    modules = o.verify_resolutions(snapshot, resolutions, sources, adapters, MODULES)
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    deps = o.load(parent / "reference/dependency-sha256.json")
    for path, digest in modules.items():
        pinned = "node_modules" not in Path(path).parts or deps.get(path) == digest
        require(pinned, "Session dependency byte drift")
    raw = trace(load(output / "observed.json"))
    require(raw["backend"] == "node", "Reference trace backend drift")
    _context_file(output, raw)
    return raw, acceptance, parent_sha, modules, env


def _native(output: Path, env: dict[str, str]) -> dict[str, Any]:
    # A stuck non-daemon native worker must not block exit: native runs in a
    # bounded child process whose whole group is killed and reaped at the deadline.
    child = {"PATH": env["PATH"], "HOME": env["HOME"]}
    o.run(
        _native_command(output), ROOT, output, "native-session", child, NATIVE_TIMEOUT
    )
    products = {mode: load(output / f"native-{mode}.json") for mode in MODES}
    for mode, product in products.items():
        native_ok = trace(product)["backend"] == "python" and product["mode"] == mode
        require(native_ok, "Native trace drift")
    keys = {"schema_version", "phase", "session_module", "modes", "executor_workers"}
    witness = load(output / "native-context.json")
    shape(witness, keys | {"threads_started", "threads_reaped"}, "native witness")
    reaped = witness["threads_started"] == witness["threads_reaped"]
    require(reaped and witness["modes"] == list(MODES), "Native witness drift")
    return products


ARTIFACTS = """input.json observed.json native-sync.json native-async.json
native-context.json observed-projected-sync.json native-projected-sync.json
observed-projected-async.json native-projected-async.json reference-context.json
session-raw.json resolutions.json pi-session-commit.json native-session.json""".split()
DIGESTS = (
    ("expected_sha256", "observed.json"),
    ("actual_sha256", "native-{}.json"),
    ("expected_projection_sha256", "observed-projected-{}.json"),
    ("actual_projection_sha256", "native-projected-{}.json"),
)


def _mismatch(output: Path, codes: dict[str, int]) -> list[str]:
    if not max(codes.values()):
        return []
    modes = {}
    for mode, code in codes.items():
        report = {
            "code": code,
            "expected": "observed.json",
            "actual": f"native-{mode}.json",
        }
        digests = {key: o.sha(output / name.format(mode)) for key, name in DIGESTS}
        modes[mode] = report | digests
    report = {"schema_version": 1, "phase": PHASE, "status": "mismatch", "modes": modes}
    dump(output / "mismatch-report.json", report)
    return ["mismatch-report.json"]


def execute(parent: Path, output: Path) -> int:
    manifest_sha = o.sha(MANIFEST)
    config = configuration()
    base = o.manifest()
    parent, output = parent.resolve(strict=True), output.resolve()
    raw, acceptance, parent_sha, modules, env = _reference(parent, output, config, base)
    products = _native(output, env)
    codes = {}
    for mode, product in products.items():
        dump(output / f"observed-projected-{mode}.json", projection(raw, mode))
        dump(output / f"native-projected-{mode}.json", projection(product, mode))
        codes[mode] = comparison(raw, product)
    mutants = execute_mutants(output, raw, env)
    o.acceptance.attest_reference(o, parent / "reference", base, o.FIXTURES)
    v.artifacts.attest(parent, o.FIXTURES)
    configuration()
    stable = o.sha(MANIFEST) == manifest_sha
    stable = stable and o.sha(parent / "acceptance.json") == parent_sha
    require(stable, "Session manifest/parent identity drift")
    names = ARTIFACTS + _mismatch(output, codes)
    code = max(codes.values())
    result = {
        "schema_version": 1,
        "phase": PHASE,
        "status": "PASS" if code == 0 else "mismatch",
        "execution_id": str(uuid.uuid4()),
        "fresh_reference_bootstrap": False,
        "parent_execution_id": acceptance["execution_id"],
        "parent_acceptance_sha256": parent_sha,
        "manifest_sha256": manifest_sha,
        "reference_run_sha256": o.sha(parent / "reference/run.json"),
        "module_sha256": modules,
        "artifacts_sha256": {name: o.sha(output / name) for name in names},
        "comparisons": codes,
        "mutants": mutants,
        "scenarios": len(SCENARIOS),
        "modes": {
            mode: len(product["scenarios"]) for mode, product in products.items()
        },
        "session_line": True,
        "tx": True,
        "default_delta_documents": True,
    }
    flags = (
        "typed_documents",
        "product_integration",
        "full_session_parity",
        "p_rows_closed",
    )
    dump(output / "result.json", result | dict.fromkeys(flags, False))
    return code


def _native_command(output: Path) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), "native", str(output)]


def _native_child(output: Path) -> int:
    """Child side of ``run``: write both native traces, never wait on stuck workers."""
    try:
        traces, witness = native(load(INPUT))
        for mode, product in traces.items():
            dump(output / f"native-{mode}.json", trace(product))
        dump(output / "native-context.json", witness)
    except Exception as error:  # noqa: BLE001 - every child failure is invalid evidence
        print(
            f"Invalid session evidence: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("expected", type=Path)
    compare.add_argument("actual", type=Path)
    child = commands.add_parser("native", help="internal: bounded native child")
    child.add_argument("output", type=Path)
    run = commands.add_parser("run")
    run.add_argument("--acceptance", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "native":
        code = _native_child(args.output)
        sys.stdout.flush()
        sys.stderr.flush()
        # Exit now: an unreaped non-daemon worker must not hang interpreter shutdown.
        os._exit(code)
    try:
        if args.command == "compare":
            return comparison(load(args.expected), load(args.actual))
        return execute(args.acceptance, args.output)
    except (
        o.EvidenceError,
        ValueError,
        OSError,
        KeyError,
        TypeError,
        RecursionError,
    ) as error:
        print(
            f"Invalid session evidence: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

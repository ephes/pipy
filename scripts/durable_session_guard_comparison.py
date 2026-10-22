"""DE02b1b actual pinned Node guard oracle vs native b1a guard. run/compare:0 equal,1 mismatch,2 invalid.

Paired recipes drive the protected guarded Storage of pinned Pi ``SessionImpl``
(through a bounded driver subclass) and the private native ``SessionLifecycle``,
each over its real Memory backend. Guard/lifecycle only: no Session line, Tx,
product or full Session parity claim. Not compared: Pi's microtask-later guard
seal while the Session line is busy (DE02b2), report versus outcome order for a
healthy close failure, Python-only ``BaseException`` handling, non-Error JS
throw values, Harness ``safeReport`` and close listeners. Pi's
``GuardedStorage.close`` is not close-once; the Node side closes only through
``SessionImpl.close``, so the native facade's close-once ``close()`` is the
owner's role, not a Pi guard claim.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import threading
import types
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "b1b_storage_transport", Path(__file__).with_name("durable_memory_comparison.py")
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
v = m.v
o = m.o
from pipy_harness.native.durable import session as real_session  # noqa: E402
from pipy_harness.native.durable.memory import MemoryStorage  # noqa: E402
from pipy_harness.native.durable.storage import (  # noqa: E402
    Cursor,
    StorageRequestError,
)

PHASE = "session-guard-b1b-v1"
INPUT = ROOT / "tests/fixtures/durable/session-guard-v1.input.json"
MANIFEST = ROOT / "tests/fixtures/durable/session-guard-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/session-guard.mts"
DESTINATION = "session-guard.mts"
MODULES = [
    "packages/durable/src/session/session.ts",
    "packages/durable/src/errors.ts",
    "packages/durable/src/storage/memory.ts",
    "packages/chord/src/context/index.ts",
]
SOURCES = set(MODULES) | {
    "packages/durable/src/storage/scan.ts",
    "packages/durable/src/types.ts",
}
UPSTREAM_TESTS = {
    "packages/durable/test/harness-storage-failure.test.ts",
    "packages/durable/test/session-support.ts",
}
CLOSURE = m.CLOSURE | {
    "src/pipy_harness/native/durable/documents.py",
    "src/pipy_harness/native/durable/session.py",
    "tests/durable/test_session_storage_guard.py",
    "scripts/durable_session_guard_comparison.py",
    "scripts/durable_reference/cases/session-guard.mts",
    "tests/fixtures/durable/session-guard-v1.input.json",
    "tests/durable/test_session_guard_comparison.py",
}
SCENARIOS = (
    "healthy-delegates",
    "roles-request-error",
    "roles-cancelled-error",
    "roles-cancelled-success",
    "pi301-real-request-error",
    "commit-request-error-fatal",
    "mint-request-error-fatal",
    "commit-cancelled-fatal",
    "mint-cancelled-fatal",
    "read-error-fatal-scan_entries",
    "read-error-fatal-task",
    "read-error-fatal-find_document",
    "mint-error-fatal",
    "pi132-late-read-success",
    "pi132-late-read-error",
    "pi156-paged-close-drain",
    "pi192-healthy-close-failure",
    "pi192-failed-then-close-failure",
    "two-failures-first-wins",
    "fail-direct",
    "admission-after-fail-vs-closed",
    "closed-then-probe",
    "concurrent-closers-cancel-one",
    "cancelled-closer-keeps-drain",
    "reentrant-report",
    "sync-throw",
    "refused-commit-not-applied",
)
MUTANTS = (
    "role-exemption-flip",
    "commit-exempt",
    "cancel-exemption-lost",
    "late-read-success-leak",
    "late-error-replaces",
    "first-cause-replacement",
    "report-twice",
    "report-missing",
    "close-before-drain",
    "double-close",
    "admission-after-seal",
    "healthy-close-not-failed",
    "close-error-swallowed",
    "waiter-cancel-undoes-close",
    "seal-before-health",
    "sync-throw-unguarded",
    "reentrant-report-admitted",
    "cause-split",
)
READS = {
    "conversation",
    "task",
    "submission",
    "request",
    "entry",
    "head",
    "scan_conversations",
    "scan_entries",
    "scan_tasks",
    "scan_submissions",
    "document",
    "find_document",
    "scan_documents",
}
GUARDED = READS | {"commit", "mint"}
STEPS = {
    "arm": {"id", "op", "handle", "hold", "outcome"},
    "arm_close": {"id", "op", "hold", "outcome"},
    "start": {"id", "op", "handle", "call", "facade"},
    "call": {"id", "op", "handle", "call", "facade"},
    "probe": {"id", "op", "handle", "call", "facade"},
    "await_entered": {"id", "op", "handle"},
    "release": {"id", "op", "handle"},
    "settle": {"id", "op", "handle"},
    "cancel": {"id", "op", "handle"},
    "fail": {"id", "op", "error"},
    "close_start": {"id", "op", "handle", "cancellable"},
    "quiesce": {"id", "op"},
    "await_close_entered": {"id", "op"},
    "release_close": {"id", "op"},
    "observe": {"id", "op", "blocked_by"},
    "raw_read": {"id", "op", "call"},
    "await_end": {"id", "op"},
}
OUTCOME_STEPS = {"call", "probe", "settle", "fail", "raw_read"}
FACADES = {"default", "cancellable", "cancelled"}
REPORT_TYPE = real_session.SESSION_FAILED_REPORT
UNDEFINED = ["undefined"]
WAIT = 5.0
"""Bound for every native wait/join; negative-control tests may lower it."""
NATIVE_TIMEOUT = 120
"""Deadline for the native child process; its process group is killed after it."""
require = v.require
load = v.load
dump = v.dump
_ambient = threading.local()


class ScriptedStorageError(Exception):
    """Driver-local scripted backend failure; identity is the comparison key."""


def shape(value: Any, fields: set[str], label: str) -> Any:
    return o.contract.shape(value, fields, label)


def _name(value: Any, label: str) -> str:
    require(type(value) is str and bool(value), "Malformed " + label)
    return value


def _errors(value: Any) -> dict[str, str]:
    kinds: dict[str, str] = {}
    for item in o.contract.array(value, "scripted errors"):
        shape(item, {"id", "kind", "message"}, "scripted error")
        key = _name(item["id"], "scripted error ID")
        require(
            key not in kinds
            and type(item["kind"]) is str
            and item["kind"] in {"error", "request", "close"}
            and type(item["message"]) is str,
            "Malformed scripted error",
        )
        kinds[key] = item["kind"]
    require(bool(kinds), "Missing scripted errors")
    return kinds


def _directive(step: Any, kinds: dict[str, str], *, close: bool) -> str:
    require(type(step["hold"]) is bool, "Malformed hold flag")
    outcome = step["outcome"]
    if outcome == "pass":
        return "pass"
    allowed = {"throw"} if close else {"throw", "abort_then_throw", "sync_throw"}
    require(
        type(outcome) is dict
        and len(outcome) == 1
        and next(iter(outcome)) in allowed
        and outcome[next(iter(outcome))] in kinds,
        "Malformed scripted outcome",
    )
    kind = next(iter(outcome))
    require((kinds[outcome[kind]] == "close") == close, "Scripted error role mismatch")
    require(not (kind == "sync_throw" and step["hold"]), "A sync throw cannot hold")
    return kind


def _call(value: Any, ops: set[str], settled: set[str], calls: dict[str, Any]) -> str:
    shape(value, {"op", "args"}, "guarded call")
    op = value["op"]
    require(type(op) is str and op in ops, "Unknown guarded operation")
    a = v.decode(value["args"])
    m._recipe_args(op, a)
    require("python_pair_fields" not in a, "Unsupported spelling boundary")
    if "cursor_ref" in a:
        require(
            a["cursor_ref"] in settled and calls[a["cursor_ref"]].startswith("scan_"),
            "Cursor reference to an unsettled scan",
        )
    return op


class _Planner:
    """Closed recipe rules for one scenario; builds its handle table."""

    def __init__(self, scenario: Any, kinds: dict[str, str]) -> None:
        self.kinds = kinds
        self.handles: dict[str, dict[str, Any]] = {}
        self.calls: dict[str, str] = {}
        self.arms: dict[str, dict[str, Any]] = {}
        self.started: set[str] = set()
        self.settled: set[str] = set()
        self.entered: set[str] = set()
        self.released: set[str] = set()
        self.cancelled: set[str] = set()
        self.close_arm: dict[str, Any] | None = None
        self.close_entered = self.close_released = self.window = False
        self.report_probe: str | None = scenario.get("report_probe")
        if self.report_probe is not None:
            _name(self.report_probe, "report probe")
            self.handles[self.report_probe] = {"kind": "report", "op": "task"}
            self.calls[self.report_probe] = "task"

    def arm(self, step: Any, handle: str) -> None:
        require(
            handle not in self.arms and handle not in self.handles,
            "Handle armed twice",
        )
        kind = _directive(step, self.kinds, close=False)
        self.arms[handle] = {"hold": step["hold"], "kind": kind}

    def arm_close(self, step: Any, handle: None) -> None:
        require(self.close_arm is None, "Backend close armed twice")
        kind = _directive(step, self.kinds, close=True)
        self.close_arm = {"hold": step["hold"], "kind": kind}

    def start(self, step: Any, handle: str) -> None:
        require(not self.window, "Guarded call inside the close/seal window")
        require(handle not in self.handles, "Handle started twice")
        require(step["facade"] in FACADES, "Unknown facade")
        arm = self.arms.get(handle)
        require(
            arm is None or step["op"] == "start" or not arm["hold"],
            "An inline call cannot hold",
        )
        require(
            arm is None
            or arm["kind"] != "abort_then_throw"
            or step["facade"] == "cancellable",
            "Abort requires a cancellable facade",
        )
        self.calls[handle] = _call(step["call"], GUARDED, self.settled, self.calls)
        self.handles[handle] = {
            "kind": "call",
            "op": self.calls[handle],
            "facade": step["facade"],
            "arm": arm,
        }
        self.started.add(handle)
        if step["op"] != "start":
            self.settled.add(handle)

    call = probe = start

    def await_entered(self, step: Any, handle: str) -> None:
        arm = self.arms.get(handle)
        require(
            handle in self.started
            and arm is not None
            and arm["hold"]
            and handle not in self.entered,
            "Await on a call that cannot hold",
        )
        self.entered.add(handle)

    def release(self, step: Any, handle: str) -> None:
        require(
            handle in self.entered and handle not in self.released,
            "Release before entry",
        )
        self.released.add(handle)

    def settle(self, step: Any, handle: str) -> None:
        item = self.handles.get(handle)
        require(
            item is not None and handle not in self.settled,
            "Settle of unknown handle",
        )
        arm = (item or {}).get("arm")
        require(
            arm is None or not arm["hold"] or handle in self.released,
            "Settle of a held call",
        )
        self.settled.add(handle)

    def cancel(self, step: Any, handle: str) -> None:
        item = self.handles.get(handle)
        require(
            item is not None
            and handle not in self.cancelled
            and (item.get("facade") == "cancellable" or item.get("cancellable")),
            "Cancel of a non-cancellable handle",
        )
        self.cancelled.add(handle)

    def fail(self, step: Any, handle: None) -> None:
        require(step["error"] in self.kinds, "Unknown failure error")

    def close_start(self, step: Any, handle: str) -> None:
        require(handle not in self.handles, "Closer handle reused")
        require(type(step["cancellable"]) is bool, "Malformed cancellable flag")
        self.handles[handle] = {"kind": "closer", "cancellable": step["cancellable"]}
        self.started.add(handle)
        self.window = True

    def quiesce(self, step: Any, handle: None) -> None:
        self.window = False

    def await_close_entered(self, step: Any, handle: None) -> None:
        arm = self.close_arm
        require(
            arm is not None and arm["hold"] and not self.close_entered,
            "Await on a backend close that cannot hold",
        )
        self.close_entered = True
        self.window = False

    def release_close(self, step: Any, handle: None) -> None:
        require(
            self.close_entered and not self.close_released,
            "Release before close entry",
        )
        self.close_released = True

    def observe(self, step: Any, handle: None) -> None:
        blocked = o.contract.array(step["blocked_by"], "blocked handles")
        require(
            len(set(blocked)) == len(blocked)
            and all(type(h) is str and h in self.started for h in blocked),
            "Unknown blocked handle",
        )

    def raw_read(self, step: Any, handle: None) -> None:
        _call(step["call"], READS, set(), self.calls)
        require(
            "cursor_ref" not in v.decode(step["call"]["args"]),
            "Raw reads take literal arguments",
        )

    def await_end(self, step: Any, handle: None) -> None:
        return None

    def finish(self) -> dict[str, dict[str, Any]]:
        require(set(self.arms) <= self.started, "Armed handle never started")
        require(
            all(not a["hold"] or h in self.released for h, a in self.arms.items()),
            "Held call never released",
        )
        arm = self.close_arm
        require(
            arm is None or not arm["hold"] or self.close_released,
            "Held backend close never released",
        )
        probe = {self.report_probe} if self.report_probe is not None else set()
        require(self.started | probe == self.settled, "Unsettled handle")
        return self.handles


def _plan(scenario: Any, kinds: dict[str, str]) -> dict[str, Any]:
    """Validate one recipe scenario; return its handle table."""
    fields = {"id", "pi_anchor", "steps"}
    extra = (
        {"report_probe"}
        if type(scenario) is dict and "report_probe" in scenario
        else set()
    )
    shape(scenario, fields | extra, "guard scenario")
    _name(scenario["pi_anchor"], "Pi anchor")
    planner = _Planner(scenario, kinds)
    steps = o.contract.array(scenario["steps"], "guard steps")
    require(bool(steps) and type(steps[-1]) is dict, "Missing guard steps")
    require(steps[-1].get("op") == "await_end", "Missing await_end")
    ids = []
    for index, step in enumerate(steps):
        require(type(step) is dict and step.get("op") in STEPS, "Unknown guard step")
        op = step["op"]
        shape(step, STEPS[op], "guard step " + op)
        ids.append(_name(step["id"], "step ID"))
        handle = step.get("handle")
        if handle is not None:
            _name(handle, "handle")
        require(op != "await_end" or index == len(steps) - 1, "await_end must be last")
        getattr(planner, op)(step, handle)
    require(len(ids) == len(set(ids)), "Duplicate step IDs")
    return planner.finish()


def recipe(value: Any) -> Any:
    shape(value, {"schema_version", "phase", "errors", "scenarios"}, "guard recipe")
    require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Unsupported guard recipe",
    )
    kinds = _errors(value["errors"])
    scenarios = o.contract.array(value["scenarios"], "guard scenarios")
    require(
        [s.get("id") if type(s) is dict else None for s in scenarios]
        == list(SCENARIOS),
        "Guard scenario identity drift",
    )
    for scenario in scenarios:
        _plan(scenario, kinds)
    return value


def _ref(value: Any, kinds: dict[str, str], depth: int = 0) -> None:
    require(type(value) is dict and type(value.get("kind")) is str, "Malformed REF")
    kind = value["kind"]
    if kind == "scripted":
        shape(value, {"kind", "id"}, "scripted REF")
        require(value["id"] in kinds, "Unknown scripted REF")
    elif kind == "session-failed":
        require(depth < 4, "REF nesting too deep")
        fields = {"kind", "message", "cause"}
        shape(
            value,
            fields | ({"cause_split"} if "cause_split" in value else set()),
            "session-failed REF",
        )
        require(
            type(value["message"]) is str and value.get("cause_split", True) is True,
            "Malformed session-failed REF",
        )
        _ref(value["cause"], kinds, depth + 1)
    elif kind == "session-closed":
        shape(value, {"kind", "message"}, "session-closed REF")
        require(type(value["message"]) is str, "Malformed session-closed REF")
    elif kind == "wait-cancelled":
        shape(value, {"kind"}, "wait-cancelled REF")
    elif kind == "request":
        shape(value, {"kind", "message"}, "request REF")
        require(type(value["message"]) is str, "Malformed request REF")
    else:
        require(kind == "unclassified", "Unknown REF kind")
        shape(value, {"kind", "name", "message"}, "unclassified REF")
        require(
            type(value["name"]) is str and type(value["message"]) is str,
            "Malformed unclassified REF",
        )


def _end(value: Any, kinds: dict[str, str], python: bool) -> None:
    require(type(value) is dict, "Malformed end")
    if value.get("reason") == "failed":
        shape(value, {"reason", "error"}, "failed end")
        _ref(value["error"], kinds)
    else:
        shape(value, {"reason"}, "end")
        require(
            value["reason"] == "closed" or (python and value["reason"] == "unsettled"),
            "Unknown end reason",
        )


def _outcome(value: Any, op: str | None, kinds: dict[str, str], python: bool) -> None:
    require(type(value) is dict, "Malformed outcome")
    status = value.get("status")
    if status == "fulfilled":
        shape(value, {"status", "value"}, "fulfilled outcome")
        if op is None:
            require(value["value"] == ["undefined"], "Unexpected void result")
        else:
            m._result(op, value["value"])
    elif status == "rejected":
        shape(value, {"status", "error"}, "rejected outcome")
        _ref(value["error"], kinds)
    else:
        shape(value, {"status"}, "unsettled outcome")
        require(python and status == "unsettled", "Unknown outcome status")


def _effect(value: Any, handles: dict[str, Any], kinds: dict[str, str]) -> None:
    require(type(value) is dict and type(value.get("e")) is str, "Malformed effect")
    e = value["e"]
    if e in {"call", "return"}:
        require(
            value.get("h") is None or value.get("h") in handles, "Unknown effect handle"
        )
    if e == "call":
        shape(value, {"e", "h", "op", "args"}, "call effect")
        require(type(value["op"]) is str and value["op"] in GUARDED, "Unknown call")
        m._recipe_args(value["op"], v.decode(value["args"]))
    elif e in {"return", "close-exit"}:
        fields = {"e", "status"} | ({"h"} if e == "return" else set())
        if value.get("status") == "rejected":
            shape(value, fields | {"error"}, e + " effect")
            _ref(value["error"], kinds)
        else:
            shape(value, fields, e + " effect")
            require(value["status"] == "fulfilled", "Unknown effect status")
    elif e == "report":
        shape(value, {"e", "error", "type", "failure_visible"}, "report effect")
        _ref(value["error"], kinds)
        require(
            type(value["type"]) is str and type(value["failure_visible"]) is bool,
            "Malformed report effect",
        )
    else:
        require(e == "close-enter", "Unknown effect")
        shape(value, {"e"}, "close-enter effect")


def trace(value: Any) -> Any:
    shape(value, {"schema_version", "phase", "backend", "scenarios"}, "guard trace")
    require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE
        and type(value["backend"]) is str
        and value["backend"] in {"node", "python"},
        "Unsupported guard trace",
    )
    python = value["backend"] == "python"
    script = recipe(load(INPUT))
    kinds = _errors(script["errors"])
    scenarios = o.contract.array(value["scenarios"], "trace scenarios")
    require(len(scenarios) == len(SCENARIOS), "Incomplete trace scenarios")
    for scenario, expected in zip(scenarios, script["scenarios"], strict=True):
        shape(scenario, {"id", "steps", "effects", "end"}, "trace scenario")
        require(scenario["id"] == expected["id"], "Trace scenario identity drift")
        handles = _plan(expected, kinds)
        steps = o.contract.array(scenario["steps"], "trace steps")
        require(len(steps) == len(expected["steps"]), "Incomplete trace steps")
        for step, recipe_step in zip(steps, expected["steps"], strict=True):
            op = recipe_step["op"]
            require(
                type(step) is dict
                and step.get("id") == recipe_step["id"]
                and step.get("op") == op,
                "Trace step identity drift",
            )
            _step(step, recipe_step, handles, kinds, python)
        for effect in o.contract.array(scenario["effects"], "effects"):
            _effect(effect, handles, kinds)
        _end(scenario["end"], kinds, python)
    return value


def _step(
    step: Any,
    recipe_step: Any,
    handles: dict[str, Any],
    kinds: dict[str, str],
    python: bool,
) -> None:
    op = recipe_step["op"]
    if op in OUTCOME_STEPS:
        shape(step, {"id", "op", "outcome"}, "trace step " + op)
        if op == "raw_read":
            target: str | None = recipe_step["call"]["op"]
        elif op == "fail":
            target = None
        else:
            item = handles[recipe_step["handle"]]
            target = item.get("op")
        _outcome(step["outcome"], target, kinds, python)
    elif op in {"await_entered", "await_close_entered"}:
        shape(step, {"id", "op", "reached"}, "trace barrier")
        require(
            type(step["reached"]) is bool and (python or step["reached"]),
            "Unreached reference barrier",
        )
    elif op == "observe":
        shape(step, {"id", "op", "state"}, "trace observation")
        state = shape(
            step["state"],
            {"failure", "close_entered", "close_exited", "reports", "end", "pending"},
            "observed state",
        )
        if state["failure"] is not None:
            _ref(state["failure"], kinds)
        require(
            all(
                type(state[k]) is int and state[k] >= 0
                for k in ("close_entered", "close_exited", "reports")
            ),
            "Malformed observed counters",
        )
        if state["end"] is not None:
            _end(state["end"], kinds, False)
        pending = o.contract.array(state["pending"], "pending handles")
        require(
            pending == sorted(set(pending))
            and all(h in handles and handles[h]["kind"] != "report" for h in pending),
            "Malformed pending handles",
        )
        require(
            python or set(pending) <= set(recipe_step["blocked_by"]),
            "Reference pending outside its held blockers",
        )
    else:
        shape(step, {"id", "op"}, "trace step " + op)


def projection(value: Any) -> Any:
    result = json.loads(json.dumps(trace(value)))
    result["backend"] = "guard"
    return result


def comparison(expected: Any, actual: Any) -> int:
    return 0 if projection(expected) == projection(actual) else 1


# -- native driver ----------------------------------------------------------------


class _Directive:
    __slots__ = ("entered", "error", "hold", "kind", "release")

    def __init__(self, hold: bool, kind: str, error: BaseException | None) -> None:
        self.hold = hold
        self.kind = kind
        self.error = error
        self.entered = threading.Event()
        self.release = threading.Event()


def _scan_args(query: Any, limit: int, cursor: Cursor | None) -> dict[str, Any]:
    result: dict[str, Any] = {"query": query.to_tree(), "limit": limit}
    if cursor is not None:
        result["cursor"] = {
            "after": cursor.after,
            **({"order": cursor.order} if cursor.order is not None else {}),
        }
    return result


class ControlledBackend:
    """Storage over a fresh real native MemoryStorage with scripted holds/outcomes."""

    def __init__(self, run: _Run) -> None:
        self.inner = MemoryStorage()
        self._run = run
        self._lock = threading.Lock()
        self.effects: list[Any] = []
        self.directives: dict[str, _Directive] = {}
        self.close_directive: _Directive | None = None

    def log(self, item: Any) -> None:
        with self._lock:
            self.effects.append(item)

    def snapshot(self) -> list[Any]:
        with self._lock:
            return json.loads(json.dumps(self.effects))

    def count(self, kind: str) -> int:
        with self._lock:
            return sum(item["e"] == kind for item in self.effects)

    def blocked(self) -> bool:
        """A held backend call or close is entered and not yet released."""
        held = list(self.directives.values())
        if self.close_directive is not None:
            held.append(self.close_directive)
        return any(
            d.hold and d.entered.is_set() and not d.release.is_set() for d in held
        )

    def _hold(self, directive: _Directive) -> None:
        directive.entered.set()
        if not directive.release.wait(self._run.wait * 4):
            raise RuntimeError("held backend call was never released")

    def _call(self, op: str, args: dict[str, Any], run: Any) -> Any:
        handle: str | None = getattr(_ambient, "handle", None)
        directive = None if handle is None else self.directives.get(handle)
        self.log({"e": "call", "h": handle, "op": op, "args": v.encode(args)})
        try:
            if directive is not None:
                if directive.hold:
                    self._hold(directive)
                if directive.kind == "abort_then_throw":
                    assert handle is not None
                    self._run.latches[handle].cancel()
                if directive.kind != "pass":
                    assert directive.error is not None
                    raise directive.error
            result = run()
        except BaseException as error:  # noqa: BLE001 - logged and re-raised unchanged
            self.log(
                {
                    "e": "return",
                    "h": handle,
                    "status": "rejected",
                    "error": self._run.ref(error),
                }
            )
            raise
        self.log({"e": "return", "h": handle, "status": "fulfilled"})
        return result

    def commit(self, writes: Any) -> Any:
        args = {"writes": [w.to_tree() for w in writes]}
        return self._call("commit", args, lambda: self.inner.commit(writes))

    def mint_id(self) -> Any:
        return self._call("mint", {}, self.inner.mint_id)

    def conversation(self, id: Any) -> Any:
        return self._call(
            "conversation", {"id": id}, lambda: self.inner.conversation(id)
        )

    def scan_conversations(self, query: Any, limit: Any, cursor: Any = None) -> Any:
        return self._call(
            "scan_conversations",
            _scan_args(query, limit, cursor),
            lambda: self.inner.scan_conversations(query, limit, cursor),
        )

    def entry(self, id: Any, *, conversation_id: Any = None) -> Any:
        args = {"id": id}
        if conversation_id is not None:
            args["conversation_id"] = conversation_id
        return self._call(
            "entry", args, lambda: self.inner.entry(id, conversation_id=conversation_id)
        )

    def find_latest_head_marker(
        self, conversation_id: Any, at_or_before_entry_id: Any = None
    ) -> Any:
        args = {"conversation_id": conversation_id}
        if at_or_before_entry_id is not None:
            args["bound"] = at_or_before_entry_id
        return self._call(
            "head",
            args,
            lambda: self.inner.find_latest_head_marker(
                conversation_id, at_or_before_entry_id
            ),
        )

    def scan_entries(self, query: Any, limit: Any, cursor: Any = None) -> Any:
        return self._call(
            "scan_entries",
            _scan_args(query, limit, cursor),
            lambda: self.inner.scan_entries(query, limit, cursor),
        )

    def task(self, id: Any) -> Any:
        return self._call("task", {"id": id}, lambda: self.inner.task(id))

    def scan_tasks(self, query: Any, limit: Any, cursor: Any = None) -> Any:
        return self._call(
            "scan_tasks",
            _scan_args(query, limit, cursor),
            lambda: self.inner.scan_tasks(query, limit, cursor),
        )

    def submission(self, id: Any) -> Any:
        return self._call("submission", {"id": id}, lambda: self.inner.submission(id))

    def scan_submissions(self, query: Any, limit: Any, cursor: Any = None) -> Any:
        return self._call(
            "scan_submissions",
            _scan_args(query, limit, cursor),
            lambda: self.inner.scan_submissions(query, limit, cursor),
        )

    def submission_by_request(self, conversation_id: Any, request_id: Any) -> Any:
        return self._call(
            "request",
            {"conversation_id": conversation_id, "request_id": request_id},
            lambda: self.inner.submission_by_request(conversation_id, request_id),
        )

    def find_document(self, address: Any, at: Any = "current") -> Any:
        return self._call(
            "find_document",
            {"address": address.to_tree(), "at": at},
            lambda: self.inner.find_document(address, at),
        )

    def document(self, id: Any, at: Any = "current") -> Any:
        return self._call(
            "document", {"id": id, "at": at}, lambda: self.inner.document(id, at)
        )

    def scan_documents(self, query: Any, limit: Any, cursor: Any = None) -> Any:
        return self._call(
            "scan_documents",
            _scan_args(query, limit, cursor),
            lambda: self.inner.scan_documents(query, limit, cursor),
        )

    def close(self) -> None:
        directive = self.close_directive
        self.log({"e": "close-enter"})
        try:
            if directive is not None and directive.hold:
                self._hold(directive)
            self.inner.close()
            if directive is not None and directive.kind == "throw":
                assert directive.error is not None
                raise directive.error
        except BaseException as error:  # noqa: BLE001 - logged and re-raised unchanged
            self.log(
                {"e": "close-exit", "status": "rejected", "error": self._run.ref(error)}
            )
            raise
        self.log({"e": "close-exit", "status": "fulfilled"})


class _Run:
    """One scenario: fresh backend and owner, named non-daemon reaped threads."""

    def __init__(
        self, S: types.ModuleType, value: Any, scenario: Any, wait: float
    ) -> None:
        self.S = S
        self.wait = wait
        self.name = scenario["id"]
        self.errors: dict[str, BaseException] = {
            item["id"]: (
                StorageRequestError(item["message"])
                if item["kind"] == "request"
                else ScriptedStorageError(item["message"])
            )
            for item in value["errors"]
        }
        self.backend = ControlledBackend(self)
        self.probe = scenario.get("report_probe")
        self.results: dict[str, tuple[str, Any]] = {}
        self.threads: dict[str, threading.Thread] = {}
        self.closers: set[str] = set()
        self.latches: dict[str, Any] = {}
        self.cache: dict[str, Any] = {}
        self.owner = S.SessionLifecycle(self.backend, reporter=self.report)

    def ref(self, error: object, depth: int = 0) -> Any:
        for key, item in self.errors.items():
            if error is item:
                return {"kind": "scripted", "id": key}
        S = self.S
        if depth < 4 and type(error) is S.SessionFailed:
            cause = getattr(error, "cause", None)
            result = {
                "kind": "session-failed",
                "message": str(error),
                "cause": self.ref(cause, depth + 1),
            }
            if cause is not getattr(error, "__cause__", None):
                result["cause_split"] = True
            return result
        if type(error) is S.SessionClosedError:
            return {"kind": "session-closed", "message": str(error)}
        if type(error) is S.WaitCancelled:
            return {"kind": "wait-cancelled"}
        if type(error) is StorageRequestError:
            return {"kind": "request", "message": str(error)}
        return {
            "kind": "unclassified",
            "name": type(error).__name__,
            "message": str(error),
        }

    def report(self, error: BaseException, kind: str) -> None:
        visible = self.owner.failure is not None
        self.backend.log(
            {
                "e": "report",
                "error": self.ref(error),
                "type": kind,
                "failure_visible": visible,
            }
        )
        probe = self.probe
        if probe is not None and probe not in self.results:
            previous = getattr(_ambient, "handle", None)
            _ambient.handle = probe
            try:
                self.results[probe] = ("fulfilled", self.owner.storage.task(1))
            except Exception as caught:  # noqa: BLE001 - recorded outcome
                self.results[probe] = ("rejected", caught)
            finally:
                _ambient.handle = previous

    def start(self, handle: str, run: Any) -> None:
        def body() -> None:
            _ambient.handle = handle
            try:
                self.results[handle] = ("fulfilled", run())
            except BaseException as caught:  # noqa: BLE001 - recorded outcome
                self.results[handle] = ("rejected", caught)
            finally:
                _ambient.handle = None

        thread = threading.Thread(
            target=body, name=f"de02b1b-{self.name}-{handle}", daemon=False
        )
        self.threads[handle] = thread
        thread.start()

    def outcome(self, handle: str, *, op: str | None) -> Any:
        thread = self.threads.get(handle)
        if thread is not None:
            thread.join(self.wait)
        if handle not in self.results:
            return {"status": "unsettled"}
        status, value = self.results[handle]
        if status == "rejected":
            return {"status": "rejected", "error": self.ref(value)}
        tree = m._dto(value)
        if op is not None and op.startswith("scan_"):
            self.cache[handle] = tree
        return {
            "status": "fulfilled",
            "value": ["undefined"] if tree is None else v.encode(tree),
        }

    def facade(self, handle: str, kind: str) -> Any:
        if kind == "default":
            return self.owner.storage
        latch = self.S.CancelLatch()
        self.latches[handle] = latch
        if kind == "cancelled":
            latch.cancel()
        return self.owner.storage.reading(latch.view())

    def closer_stable(self, handle: str) -> None:
        """Wait until a closer finished or is blocked behind a held backend call."""
        thread = self.threads[handle]
        deadline = self.wait / 0.005
        while thread.is_alive() and deadline > 0:
            if self.owner.sealed and self.backend.blocked():
                return
            thread.join(0.005)
            deadline -= 1

    def stabilize(self, blocked: list[str]) -> None:
        for handle, thread in list(self.threads.items()):
            if handle not in blocked:
                thread.join(self.wait)
            elif handle in self.closers:
                self.closer_stable(handle)
            else:
                directive = self.backend.directives.get(handle)
                if directive is not None and directive.hold:
                    directive.entered.wait(self.wait)

    def end(self, value: Any) -> Any:
        if value.reason == "closed":
            return {"reason": "closed"}
        return {"reason": "failed", "error": self.ref(value.error)}

    def step(self, step: Any, handles: dict[str, Any]) -> Any:
        record: dict[str, Any] = {"id": step["id"], "op": step["op"]}
        getattr(self, "op_" + step["op"])(step, record, handles)
        return record

    def _directive(self, step: Any) -> _Directive:
        outcome = step["outcome"]
        kind = "pass" if outcome == "pass" else next(iter(outcome))
        error = None if kind == "pass" else self.errors[outcome[kind]]
        return _Directive(step["hold"], kind, error)

    def op_arm(self, step: Any, record: Any, handles: Any) -> None:
        self.backend.directives[step["handle"]] = self._directive(step)

    def op_arm_close(self, step: Any, record: Any, handles: Any) -> None:
        self.backend.close_directive = self._directive(step)

    def op_start(self, step: Any, record: Any, handles: Any) -> None:
        handle = step["handle"]
        facade = self.facade(handle, step["facade"])
        ref = v.decode(step["call"]["args"]).get("cursor_ref")
        op = step["call"]["op"]
        if ref is None or "next" in self.cache.get(ref, {}):
            args = {"id": step["id"], "args": step["call"]["args"]}
            a = m.arguments(args, self.cache, [])
            self.start(handle, lambda: m.invoke(facade, op, a))
        # Otherwise the native referenced page never fulfilled: the call cannot
        # run and its handle stays unsettled (a Python-only semantic mismatch).
        if step["op"] != "start":
            record["outcome"] = self.outcome(handle, op=op)

    op_call = op_probe = op_start

    def op_await_entered(self, step: Any, record: Any, handles: Any) -> None:
        directive = self.backend.directives[step["handle"]]
        record["reached"] = directive.entered.wait(self.wait)

    def op_release(self, step: Any, record: Any, handles: Any) -> None:
        self.backend.directives[step["handle"]].release.set()

    def op_settle(self, step: Any, record: Any, handles: Any) -> None:
        handle = step["handle"]
        record["outcome"] = self.outcome(handle, op=handles[handle].get("op"))

    def op_cancel(self, step: Any, record: Any, handles: Any) -> None:
        self.latches[step["handle"]].cancel()

    def op_fail(self, step: Any, record: Any, handles: Any) -> None:
        error = self.errors[step["error"]]
        key = "fail:" + step["id"]
        self.start(key, lambda: self.owner.fail(error))
        record["outcome"] = self.outcome(key, op=None)

    def op_close_start(self, step: Any, record: Any, handles: Any) -> None:
        handle = step["handle"]
        view = None
        if step["cancellable"]:
            latch = self.S.CancelLatch()
            self.latches[handle] = latch
            view = latch.view()
        self.closers.add(handle)
        wait = self.wait * 2
        self.start(handle, lambda: self.owner.close(cancel=view, timeout=wait))

    def op_quiesce(self, step: Any, record: Any, handles: Any) -> None:
        for closer in self.closers:
            self.closer_stable(closer)

    def op_await_close_entered(self, step: Any, record: Any, handles: Any) -> None:
        directive = self.backend.close_directive
        assert directive is not None
        record["reached"] = directive.entered.wait(self.wait)

    def op_release_close(self, step: Any, record: Any, handles: Any) -> None:
        directive = self.backend.close_directive
        assert directive is not None
        directive.release.set()

    def op_observe(self, step: Any, record: Any, handles: Any) -> None:
        self.stabilize(step["blocked_by"])
        failure = self.owner.failure
        end = self.owner.end
        record["state"] = {
            "failure": None if failure is None else self.ref(failure),
            "close_entered": self.backend.count("close-enter"),
            "close_exited": self.backend.count("close-exit"),
            "reports": self.backend.count("report"),
            "end": None if end is None else self.end(end),
            "pending": sorted(
                h
                for h in self.threads
                if h not in self.results and not h.startswith("fail:")
            ),
        }

    def op_raw_read(self, step: Any, record: Any, handles: Any) -> None:
        a = v.decode(step["call"]["args"])
        try:
            tree = m._dto(m.invoke(self.backend.inner, step["call"]["op"], a))
            record["outcome"] = {
                "status": "fulfilled",
                "value": UNDEFINED if tree is None else v.encode(tree),
            }
        except Exception as error:  # noqa: BLE001 - recorded outcome
            record["outcome"] = {"status": "rejected", "error": self.ref(error)}

    def cleanup(self) -> None:
        for directive in self.backend.directives.values():
            directive.release.set()
        if self.backend.close_directive is not None:
            self.backend.close_directive.release.set()
        try:
            self.owner.close(timeout=self.wait)
        except BaseException:  # noqa: BLE001 - cleanup only; outcomes already recorded
            pass
        for thread in self.threads.values():
            thread.join(self.wait * 3)
        alive = sorted(t.name for t in self.threads.values() if t.is_alive())
        require(not alive, "Unreaped native guard thread")


def native(
    value: Any, session: types.ModuleType = real_session, wait: float | None = None
) -> tuple[Any, Any]:
    """Drive the native b1a owner (or a negative-control variant) with the recipe."""
    recipe(value)
    kinds = _errors(value["errors"])
    scenarios = []
    started = 0
    for scenario in value["scenarios"]:
        handles = _plan(scenario, kinds)
        run = _Run(session, value, scenario, WAIT if wait is None else wait)
        steps = []
        effects: list[Any] | None = None
        end: Any = None
        try:
            for step in scenario["steps"]:
                if step["op"] == "await_end":
                    try:
                        end = run.end(run.owner.wait_closed(timeout=run.wait))
                    except TimeoutError:
                        end = {"reason": "unsettled"}
                    effects = run.backend.snapshot()
                    steps.append({"id": step["id"], "op": step["op"]})
                    continue
                steps.append(run.step(step, handles))
        finally:
            run.cleanup()
            started += len(run.threads)
        scenarios.append(
            {"id": scenario["id"], "steps": steps, "effects": effects, "end": end}
        )
    return {
        "schema_version": 1,
        "phase": PHASE,
        "backend": "python",
        "scenarios": scenarios,
    }, {
        "schema_version": 1,
        "phase": PHASE,
        "session_module": session.__name__,
        "threads_started": started,
        "threads_reaped": started,
    }


# -- mutants ------------------------------------------------------------------------

SCRIPTED = {key: {"kind": "scripted", "id": key} for key in ("E1", "E2", "R1", "C1")}
EMPTY_PAGE = v.encode({"items": []})


def _rows(value: Any, scenario: str) -> Any:
    rows = [s for s in value["scenarios"] if s["id"] == scenario]
    require(len(rows) == 1, "Missing mutation scenario")
    return rows[0]


def _step_of(row: Any, op: str, handle: str | None = None) -> Any:
    script = _rows(recipe(load(INPUT)), row["id"])["steps"]
    ids = [
        s["id"]
        for s in script
        if s["op"] == op and (handle is None or s.get("handle") == handle)
    ]
    require(bool(ids), "Missing mutation step")
    return next(s for s in row["steps"] if s["id"] == ids[0])


def _effect_index(row: Any, **match: Any) -> int:
    for index, effect in enumerate(row["effects"]):
        if all(effect.get(k) == want for k, want in match.items()):
            return index
    raise o.EvidenceError("Missing mutation effect")


def _report(error: str) -> Any:
    return {
        "e": "report",
        "error": SCRIPTED[error],
        "type": REPORT_TYPE,
        "failure_visible": True,
    }


def _add_report(scenario: str, handle: str, error: str) -> Any:
    def mutate(value: Any) -> None:
        row = _rows(value, scenario)
        index = _effect_index(row, e="return", h=handle)
        row["effects"].insert(index + 1, _report(error))

    return mutate


def _unreported(scenario: str) -> Any:
    def mutate(value: Any) -> None:
        row = _rows(value, scenario)
        del row["effects"][_effect_index(row, e="report")]
        row["end"] = {"reason": "closed"}
        state = _step_of(row, "observe")["state"]
        state.update(failure=None, reports=0, end={"reason": "closed"})

    return mutate


def _outcome_of(scenario: str, op: str, handle: str, outcome: Any) -> Any:
    def mutate(value: Any) -> None:
        _step_of(_rows(value, scenario), op, handle)["outcome"] = outcome

    return mutate


def _cause_split(value: Any) -> None:
    row = _rows(value, "pi132-late-read-success")
    _step_of(row, "settle", "h1")["outcome"]["error"]["cause_split"] = True


def _close_before_drain(value: Any) -> None:
    row = _rows(value, "pi132-late-read-success")
    effect = row["effects"].pop(_effect_index(row, e="close-enter"))
    row["effects"].insert(_effect_index(row, e="return", h="h1"), effect)


def _first_cause_replacement(value: Any) -> None:
    row = _rows(value, "two-failures-first-wins")
    row["end"]["error"] = SCRIPTED["E2"]
    _step_of(row, "settle", "h2")["outcome"]["error"]["cause"] = SCRIPTED["E2"]


def _report_twice(value: Any) -> None:
    row = _rows(value, "mint-error-fatal")
    index = _effect_index(row, e="report")
    row["effects"].insert(index, json.loads(json.dumps(row["effects"][index])))


def _report_missing(value: Any) -> None:
    row = _rows(value, "fail-direct")
    del row["effects"][_effect_index(row, e="report")]


def _double_close(value: Any) -> None:
    row = _rows(value, "closed-then-probe")
    row["effects"] += [{"e": "close-enter"}, {"e": "close-exit", "status": "fulfilled"}]


def _admission_after_seal(value: Any) -> None:
    row = _rows(value, "pi156-paged-close-drain")
    _step_of(row, "probe", "page2")["outcome"] = {
        "status": "fulfilled",
        "value": EMPTY_PAGE,
    }
    call = row["effects"][_effect_index(row, e="call", h="page1")]
    index = _effect_index(row, e="return", h="page1")
    row["effects"][index + 1 : index + 1] = [
        dict(call, h="page2"),
        {"e": "return", "h": "page2", "status": "fulfilled"},
    ]


def _healthy_close_not_failed(value: Any) -> None:
    _rows(value, "pi192-healthy-close-failure")["end"] = {"reason": "closed"}


def _waiter_cancel_undoes_close(value: Any) -> None:
    row = _rows(value, "cancelled-closer-keeps-drain")
    row["effects"] = [
        e for e in row["effects"] if e["e"] not in {"close-enter", "close-exit"}
    ]
    _step_of(row, "probe", "later")["outcome"] = {
        "status": "fulfilled",
        "value": UNDEFINED,
    }


def _reentrant_report_admitted(value: Any) -> None:
    row = _rows(value, "reentrant-report")
    _step_of(row, "settle", "reporter")["outcome"] = {
        "status": "fulfilled",
        "value": UNDEFINED,
    }
    index = _effect_index(row, e="report")
    row["effects"][index + 1 : index + 1] = [
        {"e": "call", "h": "reporter", "op": "task", "args": v.encode({"id": 1})},
        {"e": "return", "h": "reporter", "status": "fulfilled"},
    ]


MUTATIONS = {
    "role-exemption-flip": _add_report("roles-request-error", "r-scan_entries", "R1"),
    "commit-exempt": _unreported("commit-request-error-fatal"),
    "cancel-exemption-lost": _add_report("roles-cancelled-error", "c-task", "E1"),
    "late-read-success-leak": _outcome_of(
        "pi132-late-read-success",
        "settle",
        "h1",
        {"status": "fulfilled", "value": EMPTY_PAGE},
    ),
    "late-error-replaces": _outcome_of(
        "pi132-late-read-error",
        "settle",
        "h1",
        {"status": "rejected", "error": SCRIPTED["E2"]},
    ),
    "first-cause-replacement": _first_cause_replacement,
    "report-twice": _report_twice,
    "report-missing": _report_missing,
    "close-before-drain": _close_before_drain,
    "double-close": _double_close,
    "admission-after-seal": _admission_after_seal,
    "healthy-close-not-failed": _healthy_close_not_failed,
    "close-error-swallowed": _outcome_of(
        "pi192-healthy-close-failure",
        "settle",
        "closer",
        {"status": "fulfilled", "value": UNDEFINED},
    ),
    "waiter-cancel-undoes-close": _waiter_cancel_undoes_close,
    "seal-before-health": _outcome_of(
        "admission-after-fail-vs-closed",
        "probe",
        "later",
        {
            "status": "rejected",
            "error": {"kind": "session-closed", "message": "Session is closed"},
        },
    ),
    "sync-throw-unguarded": _unreported("sync-throw"),
    "reentrant-report-admitted": _reentrant_report_admitted,
    "cause-split": _cause_split,
}
assert tuple(MUTATIONS) == MUTANTS


def _mutation(raw: Any, name: str) -> Any:
    """One schema-valid semantic mutant of an actual trace."""
    value = json.loads(json.dumps(raw))
    MUTATIONS[name](value)
    trace(value)
    return value


def execute_mutants(output: Path, raw: Any, env: dict[str, str]) -> Any:
    results = []
    for name in MUTANTS:
        path = output / (name + "-mutant.json")
        dump(path, _mutation(raw, name))
        for label, actual, wanted in [
            ("positive", output / "observed.json", 0),
            ("negative", path, 1),
        ]:
            identity = name + "-" + label
            try:
                o.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "compare",
                        str(output / "observed.json"),
                        str(actual),
                    ],
                    ROOT,
                    output,
                    identity,
                    {"PATH": env["PATH"], "HOME": env["HOME"]},
                    30,
                )
            except o.EvidenceError:
                require(wanted == 1, "Positive guard comparison failed")
            require(
                o.load(output / (identity + ".json"))["exit_code"] == wanted,
                "Guard mutant must be valid mismatch1, never invalid2",
            )
        results.append(
            {
                "id": name,
                "schema_valid": True,
                "positive_exit": 0,
                "negative_exit": 1,
                "sha256": o.sha(path),
            }
        )
    return results


# -- manifest and run ------------------------------------------------------------------

SCOPE = (
    "Bounded guard/lifecycle comparison only: pinned SessionImpl GuardedStorage via a "
    "driver subclass (guarded getter, recorded report) versus private native "
    "SessionLifecycle/GuardedStorage, each over its real Memory backend. Not compared: "
    "the Pi window between session.close() and its later line-job guard seal (DE02b2 "
    "Session line), report versus outcome publication order for a healthy close "
    "failure, Python-only BaseException handling, non-Error JS throw values, Harness "
    "safeReport and close listeners. Node closes only through SessionImpl.close; Pi "
    "GuardedStorage.close is not close-once, so native facade close-once is the "
    "owner's role, not a Pi guard claim. No Session line, Tx, product or P closure."
)
CONTEXT_DRIVER = {
    "base": "SessionImpl",
    "own_members": ["constructor", "guarded", "report"],
    "guarded_is_backend": False,
    "guarded_constructor": "GuardedStorage",
    "close_route": "SessionImpl.close",
}


def configuration() -> Any:
    config = load(MANIFEST)
    shape(
        config,
        {
            "schema_version",
            "phase",
            "reference_commit",
            "baseline_manifest_sha256",
            "a1_manifest_sha256",
            "a2b_manifest_sha256",
            "closure",
            "reference_sources",
            "upstream_tests",
            "scope",
        },
        "guard manifest",
    )
    require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN
        and config["scope"] == SCOPE,
        "Unsupported guard manifest",
    )
    o.contract.shape(config["closure"], CLOSURE, "guard closed artifact inventory")
    for name, digest in config["closure"].items():
        o.contract.digest(digest)
        require(o.sha(ROOT / name) == digest, "Guard artifact drift")
    for field, names in (
        ("reference_sources", SOURCES),
        ("upstream_tests", UPSTREAM_TESTS),
    ):
        o.contract.shape(config[field], names, "guard pinned " + field)
        for digest in config[field].values():
            o.contract.digest(digest)
    require(
        config["baseline_manifest_sha256"] == o.sha(o.FIXTURES / "manifest.json")
        and config["a1_manifest_sha256"] == o.sha(v.MANIFEST)
        and config["a2b_manifest_sha256"] == o.sha(m.MANIFEST),
        "Parent manifest drift",
    )
    m.configuration()
    recipe(load(INPUT))
    return config


def _raw(value: Any) -> None:
    shape(value, {"schema_version", "errors"}, "guard raw diagnostics")
    require(
        type(value["schema_version"]) is int and value["schema_version"] == 1,
        "Guard raw version drift",
    )
    for item in o.contract.array(value["errors"], "raw guard errors"):
        fields = {"scenario", "where", "name", "message", "stack", "constructor"}
        shape(
            item,
            fields | ({"cause"} if type(item) is dict and "cause" in item else set()),
            "raw guard error",
        )
        require(
            all(type(item[k]) is str for k in fields - {"stack"})
            and (item["stack"] is None or type(item["stack"]) is str)
            and item["scenario"] in SCENARIOS,
            "Malformed raw guard error",
        )
        if "cause" in item:
            shape(item["cause"], {"name", "message", "constructor"}, "raw cause")


def execute(parent: Path, output: Path) -> int:
    manifest_sha = o.sha(MANIFEST)
    config = configuration()
    base = o.manifest()
    parent = parent.resolve(strict=True)
    v.artifacts.attest(parent, o.FIXTURES)
    run = o.acceptance.attest_reference(o, parent / "reference", base, o.FIXTURES)
    acceptance = o.load(parent / "acceptance.json")
    require(
        acceptance.get("status") == "acceptance-verified"
        and acceptance.get("complete") is True,
        "Incomplete parent acceptance",
    )
    parent_sha = o.sha(parent / "acceptance.json")
    snapshot = parent / "reference/snapshot"
    o.verify_sources(snapshot, config["reference_sources"])
    o.verify_sources(snapshot, config["upstream_tests"])
    output = output.resolve()
    require(not output.is_relative_to(parent), "Guard output must be outside parent")
    output.mkdir(mode=0o700, exist_ok=False)
    output.chmod(0o700)
    driver = snapshot / DESTINATION
    require(
        not driver.exists() and not driver.is_symlink(),
        "Guard caller destination exists",
    )
    shutil.copyfile(DRIVER, driver)
    driver.chmod(0o600)
    require(o.sha(driver) == o.sha(DRIVER), "Guard caller copy drift")
    shutil.copyfile(INPUT, output / "input.json")
    env = o.child_env(output)
    for runtime in ("node", "npm"):
        require(
            o.run(
                [runtime, "--version"], snapshot, output, runtime + "-version", env, 15
            )
            .decode()
            .strip()
            == base["runtime"][runtime],
            "Guard runtime drift",
        )
    o.run(
        [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(driver),
            str(output / "input.json"),
            str(output),
        ],
        snapshot,
        output,
        "pi-session-guard",
        env,
        60,
    )
    modules = o.verify_resolutions(
        snapshot,
        o.load(output / "resolutions.json"),
        o.load(parent / "reference/source-sha256.json"),
        {DESTINATION: o.sha(DRIVER)},
        MODULES,
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    deps = o.load(parent / "reference/dependency-sha256.json")
    for path, digest in modules.items():
        if "node_modules" in Path(path).parts:
            require(deps.get(path) == digest, "Guard dependency byte drift")
    context = load(output / "reference-context.json")
    shape(context, {"schema_version", "phase", "scenarios", "driver"}, "guard context")
    require(
        type(context["schema_version"]) is int
        and context["schema_version"] == 1
        and context["phase"] == PHASE
        and context["scenarios"] == len(SCENARIOS)
        and context["driver"] == CONTEXT_DRIVER,
        "Guard reference context drift",
    )
    raw = trace(load(output / "observed.json"))
    require(raw["backend"] == "node", "Reference trace backend drift")
    _raw(load(output / "guard-raw.json"))
    # A stuck non-daemon native worker must not block exit: native runs in a
    # bounded child process whose whole group is killed and reaped at the deadline.
    o.run(
        _native_command(output),
        ROOT,
        output,
        "native-guard",
        {"PATH": env["PATH"], "HOME": env["HOME"]},
        NATIVE_TIMEOUT,
    )
    product = load(output / "native.json")
    witness = load(output / "native-context.json")
    require(trace(product)["backend"] == "python", "Native trace backend drift")
    dump(output / "native.json", product)
    dump(output / "native-context.json", witness)
    dump(output / "observed-projected.json", projection(raw))
    dump(output / "native-projected.json", projection(product))
    code = comparison(raw, product)
    mutants = execute_mutants(output, raw, env)
    o.acceptance.attest_reference(o, parent / "reference", base, o.FIXTURES)
    v.artifacts.attest(parent, o.FIXTURES)
    configuration()
    require(
        o.sha(MANIFEST) == manifest_sha
        and o.sha(parent / "acceptance.json") == parent_sha,
        "Guard manifest/parent identity drift",
    )
    names = [
        "input.json",
        "observed.json",
        "native.json",
        "native-context.json",
        "observed-projected.json",
        "native-projected.json",
        "reference-context.json",
        "guard-raw.json",
        "resolutions.json",
        "pi-session-guard.json",
        "native-guard.json",
    ]
    if code:
        dump(
            output / "mismatch-report.json",
            {
                "schema_version": 1,
                "phase": PHASE,
                "status": "mismatch",
                "expected": "observed.json",
                "actual": "native.json",
                "expected_sha256": o.sha(output / "observed.json"),
                "actual_sha256": o.sha(output / "native.json"),
                "expected_projection": "observed-projected.json",
                "actual_projection": "native-projected.json",
                "expected_projection_sha256": o.sha(output / "observed-projected.json"),
                "actual_projection_sha256": o.sha(output / "native-projected.json"),
            },
        )
        names.append("mismatch-report.json")
    dump(
        output / "result.json",
        {
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
            "mutants": mutants,
            "scenarios": len(SCENARIOS),
            "product_integration": False,
            "session_line": False,
            "tx": False,
            "full_session_parity": False,
        },
    )
    return code


def _native_command(output: Path) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), "native", str(output)]


def _native_child(output: Path) -> int:
    """Child side of ``run``: write the native trace, never wait on stuck workers."""
    try:
        product, witness = native(load(INPUT))
        trace(product)
        dump(output / "native.json", product)
        dump(output / "native-context.json", witness)
    except Exception as error:  # noqa: BLE001 - every child failure is invalid evidence
        print(
            f"Invalid guard evidence: {type(error).__name__}: {error}", file=sys.stderr
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
            f"Invalid guard evidence: {type(error).__name__}: {error}", file=sys.stderr
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

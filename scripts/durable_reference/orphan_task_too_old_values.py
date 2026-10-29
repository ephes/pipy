"""Finite task-too-old observations; preserve complete raw backend evidence."""

from __future__ import annotations

import copy
import math

PHASE = "task-orphan-task-too-old-v1"
CASES = [
    {
        "id": "memory-warm",
        "backend": "memory",
        "cold": False,
        "parked": False,
        "install": False,
    },
    {
        "id": "sqlite-warm",
        "backend": "sqlite",
        "cold": False,
        "parked": False,
        "install": False,
    },
    {
        "id": "sqlite-cold-unmarked",
        "backend": "sqlite",
        "cold": True,
        "parked": False,
        "install": False,
    },
    {
        "id": "sqlite-parked-control",
        "backend": "sqlite",
        "cold": False,
        "parked": True,
        "install": False,
    },
    {
        "id": "sqlite-terminal-new-definition",
        "backend": "sqlite",
        "cold": False,
        "parked": False,
        "install": True,
    },
]
IDS = tuple(row["id"] for row in CASES)
VERSIONS = {"stored": 2, "available": 1}
RECIPE = {
    "schema_version": 1,
    "phase": PHASE,
    "definition_versions": VERSIONS,
    "cases": CASES,
}
TASK_NAME = "fixture.orphan.too-old.generic"
DOC_KINDS = ("fixture.orphan.too-old.note", "fixture.orphan.too-old.cached")
MUTANTS = (
    "outcome",
    "reason",
    "abort_flag",
    "live_metadata",
    "atomic_retirement",
    "document_visibility",
    "first_ack",
    "repeat_ack",
    "callback_delta",
    "cold_preservation",
    "repeat_no_write",
    "unrelated_preservation",
    "available_version",
    "version_relation",
    "task_preservation",
)
FIELDS = {
    "labels",
    "before",
    "after",
    "first_ack",
    "repeat_acks",
    "document_visibility",
    "callbacks",
    "atomic_retirement",
    "live_metadata_cleared",
    "repeat_no_write",
    "unrelated_preserved",
    "cold_preserved",
    "closed",
    "definition",
    "task_preserved",
}
SCOPE = {
    "common": "actual-generic-pending-present-older-definition-root-orphan-taskdoc-retirement",
    "controls": "real Task/Tx creation; parked row is an explicit restart/memo metadata control, not a crash; no target runtime resume",
    "normalization": [
        "proved immediate Pi marked ACK to orphaned only",
        "explicit target/conversation IDs only; fixture clock retained",
    ],
    "native_only": [
        "strict all-graph ownership/incoming-waiter/pi.-prefix refusal",
        "lifecycle/cancellation/error types",
        "strict missing/equal/newer definition refusal and native numeric domains",
        "actual native action _fit/_fitting_definition forwarding call observations",
        "Memory has no numeric historical read",
    ],
    "reference_only": [
        "actual full Harness with installed settleSchedulerOutcome generic guard",
        "Harness initial live/inbox/usage/provider/agent baseline",
        "broader Pi abort and ordinary ownership",
    ],
    "raw_differences": [
        "complete TaskRecords/ACKs",
        "all current DocVisibility",
        "Storage commit batches/minted IDs",
        "callback SQLite ledger",
        "SQLite full tables/document_revisions",
        "inspection/policy observations",
        "actual origins/IDs/timestamps",
    ],
    "deferred": [
        "public Product/SDK/CLI abort",
        "active abort/cascade/completing ownership",
        "reserved presentation cleanup",
        "other migration/version/phase orphan reasons",
        "physical crash",
        "DE15/16/23",
    ],
}


def checked_recipe(value):
    if type(value) is not dict or set(value) != {
        "schema_version",
        "phase",
        "definition_versions",
        "cases",
    }:
        raise ValueError("Closed orphan recipe")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or type(value["phase"]) is not str
        or value["phase"] != PHASE
    ):
        raise ValueError("Orphan recipe identity")
    versions = value["definition_versions"]
    if type(versions) is not dict or set(versions) != set(VERSIONS):
        raise ValueError("Closed orphan definition versions")
    if any(
        type(versions[k]) is not int or versions[k] != n for k, n in VERSIONS.items()
    ):
        raise ValueError("Orphan exact definition version recipe")
    if type(value["cases"]) is not list or len(value["cases"]) != len(CASES):
        raise ValueError("Orphan finite recipe cases")
    for row, expected in zip(value["cases"], CASES, strict=True):
        if type(row) is not dict or set(row) != set(expected):
            raise ValueError("Closed orphan recipe case")
        for key, wanted in expected.items():
            if type(row[key]) is not type(wanted) or row[key] != wanted:
                raise ValueError("Orphan recipe typed value: " + key)
    return value


def labels(case):
    result = ["created"]
    if case["parked"]:
        result.append("seeded-restarts")
    if case["cold"]:
        result.append("unmarked-reopen")
    result += ["before-action", "orphan"]
    if case["install"]:
        result.append("install-definition")
    result.append("repeat")
    if case["backend"] == "sqlite":
        result += ["terminal-reopen", "repeat-after-reopen"]
    return result


def _task(row, created):
    value = copy.deepcopy(row)
    if (
        value["id"] != created["id"]
        or value["conversationId"] != created["conversationId"]
    ):
        raise ValueError("Actual target identity")
    value["id"] = "@target"
    value["conversationId"] = "@conversation"
    return value


def _same_boundary(left, right):
    return all(
        left[k] == right[k]
        for k in (
            "record",
            "documents",
            "backend",
            "batch_count",
            "mint_count",
            "counts",
            "callback_rows",
        )
    )


def project(raw):
    case = next(c for c in CASES if c["id"] == raw["id"])
    steps = raw["steps"]
    if [s["label"] for s in steps] != labels(case):
        raise ValueError("Actual orphan action order")
    for step in steps:
        ledger = step["callback_rows"]
        if [r["seq"] for r in ledger] != list(range(1, len(ledger) + 1)):
            raise ValueError("Actual callback SQLite sequence")
        if {k: sum(r["kind"] == k for r in ledger) for k in step["counts"]} != step[
            "counts"
        ] or any(r["kind"] not in step["counts"] for r in ledger):
            raise ValueError("Actual callback SQLite/count join")
    if [r["kind"] for r in raw["callback_rows"]] != raw["callback_log"]:
        raise ValueError("Actual final callback log join")
    before = next(s for s in steps if s["label"] == "before-action")
    after = next(s for s in steps if s["label"] == "orphan")
    immediate = after["record"]["state"] == {
        "status": "terminal",
        "outcome": {"status": "orphaned", "reason": "task_too_old"},
    }
    ack = after["ACK"]
    if raw["role"] == "pi" and ack == "marked" and immediate:
        ack = "orphaned"
    repeats = [s for s in steps if s["label"].startswith("repeat")]
    repeat_stable = all(
        _same_boundary(steps[i - 1], step)
        for i, step in enumerate(steps)
        if step["label"].startswith("repeat")
    )
    target_ids = {d["record"]["id"] for d in before["documents"]["target"]}
    new_batches = raw["commit_batches"][before["batch_count"] : after["batch_count"]]
    writes = [] if len(new_batches) != 1 else new_batches[0]["writes"]
    task_writes = [w for w in writes if w["type"] == "task"]
    retired = {w["id"] for w in writes if w["type"] == "document.retire"}
    atomic = (
        len(new_batches) == 1
        and len(task_writes) == 1
        and task_writes[0]["value"]["id"] == raw["created"]["id"]
        and task_writes[0]["value"]["state"] == after["record"]["state"]
        and retired == target_ids
        and len(writes) == 1 + len(target_ids)
    )
    docs = [
        {
            "kind": kind,
            "before": any(
                d["record"]["kind"] == kind for d in before["documents"]["target"]
            ),
            "after": any(
                d["record"]["kind"] == kind for d in after["documents"]["target"]
            ),
        }
        for kind in DOC_KINDS
    ]
    cold = None
    if case["cold"]:
        cold_step = next(s for s in steps if s["label"] == "unmarked-reopen")
        cold = _same_boundary(steps[0], cold_step)
    return {
        "labels": [s["label"] for s in steps],
        "before": _task(before["record"], raw["created"]),
        "after": _task(after["record"], raw["created"]),
        "first_ack": ack,
        "repeat_acks": [s["ACK"] for s in repeats],
        "document_visibility": docs,
        "callbacks": {"before": before["counts"], "after": after["counts"]},
        "atomic_retirement": atomic,
        "live_metadata_cleared": "memos" not in after["record"]
        and "restarts" not in after["record"],
        "repeat_no_write": repeat_stable,
        "unrelated_preserved": all(
            s["documents"]["conversation"] == before["documents"]["conversation"]
            for s in steps[steps.index(before) :]
        ),
        "cold_preserved": cold,
        "closed": raw["closed"],
        "definition": copy.deepcopy(raw["definition_observation"]),
        "task_preserved": {
            k: value
            for k, value in before["record"].items()
            if k not in ("state", "memos", "restarts", "endedAt")
        }
        == {
            k: value
            for k, value in after["record"].items()
            if k not in ("state", "memos", "restarts", "endedAt")
        },
    }


def _numeric_version(value):
    return type(value) is int or type(value) is float and math.isfinite(value)


def _record_shape(record, d):
    required = {
        "id",
        "conversationId",
        "kind",
        "version",
        "input",
        "background",
        "abortRequested",
        "state",
    }
    optional = {
        "trace",
        "links",
        "owner",
        "abortReason",
        "abandonOnRestart",
        "startedAt",
        "endedAt",
        "restarts",
        "memos",
    }
    d.require(
        type(record) is dict and required <= set(record) <= required | optional,
        "Closed complete normalized TaskRecord",
    )
    d.require(
        record["id"] == "@target"
        and record["conversationId"] == "@conversation"
        and type(record["kind"]) is str
        and _numeric_version(record["version"])
        and type(record["background"]) is bool
        and type(record["abortRequested"]) is bool,
        "Explicit target IDs and retained scalar fields",
    )
    state = record["state"]
    d.require(type(state) is dict, "Actual Task state")
    status = state.get("status")
    shapes = {
        "pending": {"status", "checkpoint"},
        "running": {"status", "checkpoint"},
        "sleeping": {"status", "checkpoint", "until"},
        "waiting": {"status", "checkpoint", "on", "policy"},
        "completing": {"status", "outcome"},
        "terminal": {"status", "outcome"},
    }
    d.require(type(status) is str and status in shapes, "Task state domain")
    d.shape(state, shapes[status], "Closed Task state")
    if status in ("terminal", "completing"):
        outcome = state["outcome"]
        d.require(type(outcome) is dict, "Actual terminal outcome")
        kinds = {
            "completed": ({"status", "result"}, set()),
            "failed": ({"status", "error"}, {"result"}),
            "aborted": ({"status"}, {"result", "reason"}),
            "faulted": ({"status", "error"}, set()),
            "orphaned": ({"status", "reason"}, set()),
        }
        k = outcome.get("status")
        d.require(type(k) is str and k in kinds, "Outcome domain")
        need, extra = kinds[k]
        d.require(need <= set(outcome) <= need | extra, "Closed outcome fields")


def validate(value, d, v):
    d.shape(value, {"schema_version", "phase", "cases"}, "Orphan trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Orphan trace identity",
    )
    d.require(
        type(value["cases"]) is list and [r["id"] for r in value["cases"]] == list(IDS),
        "Orphan trace cases",
    )
    for row, case in zip(value["cases"], CASES, strict=True):
        d.shape(row, {"id", "observed"}, "Orphan case")
        x = v.decode(row["observed"])
        d.shape(x, FIELDS, "Closed orphan observations")
        definition = x["definition"]
        d.shape(
            definition,
            {"available_version", "stored_version", "strictly_older"},
            "Actual available/stored version observation",
        )
        d.require(
            all(
                _numeric_version(definition[k])
                for k in ("available_version", "stored_version")
            )
            and type(definition["strictly_older"]) is bool,
            "Version comparison scalar domains",
        )
        d.require(x["labels"] == labels(case), "Orphan action labels")
        for key in ("before", "after"):
            _record_shape(x[key], d)
        d.require(
            x["first_ack"] in ("orphaned", "terminal"), "Orphan immediate ACK domain"
        )
        d.require(
            type(x["repeat_acks"]) is list
            and len(x["repeat_acks"]) == (2 if case["backend"] == "sqlite" else 1)
            and all(a in ("orphaned", "terminal") for a in x["repeat_acks"]),
            "Orphan repeat ACK domain",
        )
        d.require(
            type(x["document_visibility"]) is list
            and [q["kind"] for q in x["document_visibility"]] == list(DOC_KINDS),
            "Target document identities",
        )
        for doc in x["document_visibility"]:
            d.shape(doc, {"kind", "before", "after"}, "Document visibility")
            d.require(
                type(doc["before"]) is bool and type(doc["after"]) is bool,
                "Document visibility flags",
            )
        d.shape(x["callbacks"], {"before", "after"}, "Callback boundary")
        for c in x["callbacks"].values():
            d.shape(c, {"initial", "migrate", "handler", "abort"}, "Callback fields")
            d.require(
                all(type(n) is int and n >= 0 for n in c.values()),
                "Callback counter domain",
            )
        for key in (
            "atomic_retirement",
            "live_metadata_cleared",
            "repeat_no_write",
            "unrelated_preserved",
            "task_preserved",
        ):
            d.require(type(x[key]) is bool, "Orphan predicate type")
        d.require(
            x["cold_preserved"] is None or type(x["cold_preserved"]) is bool,
            "Cold observation type",
        )
        d.require(
            type(x["closed"]) is list and all(type(q) is str for q in x["closed"]),
            "Actual owner close outcomes",
        )


def successful(value, d, v):
    validate(value, d, v)
    for row, case in zip(value["cases"], CASES, strict=True):
        x = v.decode(row["observed"])
        d.require(
            x["definition"]
            == {
                "available_version": VERSIONS["available"],
                "stored_version": VERSIONS["stored"],
                "strictly_older": True,
            }
            and x["definition"]["available_version"] < x["definition"]["stored_version"]
            and x["before"]["version"] == x["definition"]["stored_version"]
            and x["task_preserved"] is True,
            "Actual present older definition and preserved stored task",
        )
        d.require(
            x["before"]["kind"] == TASK_NAME
            and x["before"]["state"]["status"] == "pending"
            and x["before"]["abortRequested"] is False,
            "Actual pending unmarked generic target",
        )
        d.require(
            x["after"]["state"]
            == {
                "status": "terminal",
                "outcome": {"status": "orphaned", "reason": "task_too_old"},
            }
            and x["after"]["abortRequested"] is False,
            "Actual immediate orphan outcome without mark",
        )
        d.require(
            x["first_ack"] == "orphaned"
            and all(a == "terminal" for a in x["repeat_acks"]),
            "Actual first/repeated ACK",
        )
        d.require(
            x["callbacks"]["before"]
            == x["callbacks"]["after"]
            == {"initial": 1, "migrate": 0, "handler": 0, "abort": 0},
            "Actual creation versus action callback boundary",
        )
        d.require(
            all(q["before"] and not q["after"] for q in x["document_visibility"]),
            "Actual current task-document retirement",
        )
        d.require(
            all(
                x[k]
                for k in (
                    "atomic_retirement",
                    "live_metadata_cleared",
                    "repeat_no_write",
                    "unrelated_preserved",
                )
            ),
            "Actual atomic/no-write/preservation witnesses",
        )
        d.require(
            x["cold_preserved"] is True
            if case["cold"]
            else x["cold_preserved"] is None,
            "Unmarked cold open does not settle",
        )
        d.require(
            "memos" not in x["after"] and "restarts" not in x["after"],
            "Terminal live metadata absent",
        )
        if case["parked"]:
            d.require(
                x["before"].get("restarts") == 5
                and x["before"].get("memos") == {"fixture.memo": {"kept": True}},
                "Explicit parked restart/memo control",
            )
        d.require(
            x["closed"] and all(r == "closed" for r in x["closed"]),
            "Every real owner closed",
        )


def mutate(value, name, v):
    result = copy.deepcopy(value)
    index = 2 if name == "cold_preservation" else 0
    x = v.decode(result["cases"][index]["observed"])
    changes = {
        "outcome": (
            ("after", "state", "outcome"),
            {"status": "faulted", "error": {"message": "mutant"}},
        ),
        "reason": (("after", "state", "outcome", "reason"), "mutant"),
        "abort_flag": (("after", "abortRequested"), True),
        "live_metadata": (("live_metadata_cleared",), False),
        "atomic_retirement": (("atomic_retirement",), False),
        "document_visibility": (("document_visibility", 0, "after"), True),
        "first_ack": (("first_ack",), "terminal"),
        "repeat_ack": (("repeat_acks", 0), "orphaned"),
        "callback_delta": (
            ("callbacks", "after", "abort"),
            x["callbacks"]["after"]["abort"] + 1,
        ),
        "cold_preservation": (("cold_preserved",), False),
        "repeat_no_write": (("repeat_no_write",), False),
        "unrelated_preservation": (("unrelated_preserved",), False),
        "available_version": (("definition", "available_version"), VERSIONS["stored"]),
        "version_relation": (("definition", "strictly_older"), False),
        "task_preservation": (("task_preserved",), False),
    }
    if name not in changes:
        raise ValueError("Unknown orphan mutant")
    path, replacement = changes[name]
    target = x
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = replacement
    result["cases"][index]["observed"] = v.encode(x)
    return result

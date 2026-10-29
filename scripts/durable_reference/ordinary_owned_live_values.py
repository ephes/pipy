"""Finite held-completion projection; complete raw states remain lane-specific."""

from __future__ import annotations

import copy

PHASE = "task-ordinary-owned-live-v1"
TASK_NAME = "fixture.ordinary-owned-live.hold"
CASES = [
    {"id": "memory-background-first", "backend": "memory", "cold": False, "first": "Z"},
    {"id": "sqlite-foreground-first", "backend": "sqlite", "cold": False, "first": "D"},
    {"id": "sqlite-cold-ended-bridge", "backend": "sqlite", "cold": True, "first": "Z"},
]
IDS = tuple(c["id"] for c in CASES)
RECIPE = {"schema_version": 1, "phase": PHASE, "cases": CASES}
MUTANTS = ("held_false", "after_first_inverted", "released_true")
SCOPE = {
    "common": "ordinary-owned-live-held-completion-common-v1",
    "controls": "Real Pi public FullHarness completion frames and waits; native real Tx graph creation/terminalization with execution disabled; actual SQLite cold reopen, not crash",
    "normalization": [
        "Exact P/B/A/C/D/Z role IDs for topology joins only",
        "Native ownership boolean versus actual Pi completing/terminal-completed boundary only",
    ],
    "native_only": [
        "Direct real Tx terminalization, no native owned execution/completing",
        "Query-only storage/revision/callback silence",
        "Global graph/candidate/lifecycle/assembly controls remain Core/Broad",
    ],
    "reference_only": [
        "Actual registered task callbacks, public graph watch/ACK/getTask/read transaction/waitForTask",
        "FullHarness unchanged outcome hook and conversation documents",
        "No private ownedLive instrumentation",
    ],
    "raw_differences": [
        "Full TaskRecords/conversations/documents",
        "Callbacks/ACKs/watch frames and lifecycle",
        "Timestamps/outcomes/restarts",
        "Full SQLite tables/revisions and commit/mint tapes",
    ],
    "deferred": [
        "Native completing/dispatch/cascade",
        "Physical deletion/done sets",
        "Public SDK/CLI/abort/handover",
        "DE14/P07 and full DE15/P09",
    ],
}


def checked_recipe(value):
    if type(value) is not dict or set(value) != set(RECIPE):
        raise ValueError("Closed ownership recipe")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["phase"] != PHASE
    ):
        raise ValueError("Ownership recipe identity")
    if type(value["cases"]) is not list or len(value["cases"]) != len(CASES):
        raise ValueError("Ownership finite cases")
    for row, expected in zip(value["cases"], CASES, strict=True):
        if (
            type(row) is not dict
            or set(row) != set(expected)
            or any(
                type(row[k]) is not type(v) or row[k] != v for k, v in expected.items()
            )
        ):
            raise ValueError("Ownership typed recipe case")
    return value


def labels(case):
    return ["held", "after-first", "released"]


def _done(record):
    return record["state"] == {
        "status": "terminal",
        "outcome": {"status": "completed", "result": None},
    }


def _topology(raw, step):
    ids = raw["topology"]
    if set(ids) != {"root", "P", "B", "A", "C", "Z", "D"} or len(
        set(ids.values())
    ) != len(ids):
        raise ValueError("Actual ownership role identities")
    rows = {r["id"]: r for r in step["snapshot"]["tasks"]}
    records = {k: rows[ids[k]] for k in ("P", "B", "A", "Z", "D")}
    for name, record in records.items():
        if (
            record["kind"] != TASK_NAME
            or record["input"] != {"name": name}
            or record["version"] != 1
            or record["abortRequested"] is not False
        ):
            raise ValueError("Actual created ownership task")
        if record["background"] is not (name == "Z"):
            raise ValueError("Actual foreground/background boundary")
        cid = ids["C"] if name in ("Z", "D") else ids["root"]
        owner = {"B": ids["P"], "A": ids["B"]}.get(name)
        if record["conversationId"] != cid or record.get("owner") != owner:
            raise ValueError("Actual task owner edge")
    conversations = {r["id"]: r for r in step["snapshot"]["conversations"]}
    if conversations[ids["C"]].get("owner") != {
        "taskId": ids["A"],
        "conversationId": ids["root"],
    }:
        raise ValueError("Actual conversation owner edge")
    if not _done(records["A"]) or not _done(records["B"]) or ids["Z"] >= ids["D"]:
        raise ValueError("Actual ended bridge and later foreground ordering")
    return records


def _answer(raw, step, case):
    records = _topology(raw, step)
    ended = (
        set()
        if step["label"] == "held"
        else ({case["first"]} if step["label"] == "after-first" else {"D", "Z"})
    )
    if any(_done(records[name]) is not (name in ended) for name in ("D", "Z")):
        raise ValueError("Actual live/released D/Z boundary")
    if raw["role"] == "native":
        query = step["query"]
        if query["before"] != query["after"] or query["silent"] is not True:
            raise ValueError("Ownership query actual no-write witness")
        if step["snapshot"] != query["after"]["snapshot"]:
            raise ValueError("Actual query/snapshot join")
        if records["P"]["state"]["status"] != "pending":
            raise ValueError("Native disabled execution asymmetry")
        return query["answer"]
    state = records["P"]["state"]
    if state == {
        "status": "completing",
        "outcome": {"status": "completed", "result": None},
    }:
        return True
    if _done(records["P"]):
        return False
    raise ValueError("Actual Pi held/completed outcome")


def _lifecycle(raw, case):
    lifecycle = (
        raw["native_only"]["lifecycle"]
        if raw["role"] == "native"
        else raw["reference_only"]["lifecycle"]
    )
    if not case["cold"]:
        if lifecycle:
            raise ValueError("No fabricated warm reopen")
        return
    if len(lifecycle) != 2 or [x["action"] for x in lifecycle] != ["close", "reopen"]:
        raise ValueError("Actual cold close/reopen witness")
    for snapshot in (lifecycle[0]["before"], lifecycle[1]["after"]):
        by_id = {r["id"]: r for r in snapshot["tasks"]}
        if any(not _done(by_id[raw["topology"][k]]) for k in ("A", "B")):
            raise ValueError("Actual persisted ended bridge")


def _reference(raw):
    side = raw["reference_only"]
    held = side["held_frame"]["tasks"][str(raw["topology"]["P"])]
    if (
        held["state"] != {"status": "completing", "outcome": "completed"}
        or "P" not in side["commit_acks"]
    ):
        raise ValueError("Actual public completing frame and commit ACK")
    if "ack:P" not in raw["callback_log"] or side["reports"] != []:
        raise ValueError("Actual public completion ACK/report ledger")
    if (
        side["release_outcome"] != {"status": "completed", "result": None}
        or side["private_owned_live"] != "NOT_OBSERVED"
    ):
        raise ValueError("Actual public terminal release")


def project(raw):
    case = next(c for c in CASES if c["id"] == raw["id"])
    if [s["label"] for s in raw["steps"]] != labels(case):
        raise ValueError("Ownership actual boundary order")
    result = [
        {"label": step["label"], "owned_live": _answer(raw, step, case)}
        for step in raw["steps"]
    ]
    _lifecycle(raw, case)
    if raw["role"] == "pi":
        _reference(raw)
    return {"milestones": result}


def validate(value, d, v):
    d.shape(value, {"schema_version", "phase", "cases"}, "Ownership trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Ownership trace identity",
    )
    d.require(
        type(value["cases"]) is list and [r["id"] for r in value["cases"]] == list(IDS),
        "Ownership cases",
    )
    for row, case in zip(value["cases"], CASES, strict=True):
        d.shape(row, {"id", "observed"}, "Ownership case")
        item = v.decode(row["observed"])
        d.shape(item, {"milestones"}, "Ownership projection")
        d.require(
            type(item["milestones"]) is list
            and [s["label"] for s in item["milestones"]] == labels(case),
            "Ownership milestones",
        )
        for step in item["milestones"]:
            d.shape(step, {"label", "owned_live"}, "Ownership boolean")
            d.require(type(step["owned_live"]) is bool, "Ownership exact boolean")


def successful(value, d, v):
    validate(value, d, v)
    for row, case in zip(value["cases"], CASES, strict=True):
        observed = v.decode(row["observed"])["milestones"]
        d.require(
            [s["owned_live"] for s in observed] == [True, case["first"] == "Z", False],
            "Actual held/released ownership result",
        )


def mutate(value, name, v):
    if name not in MUTANTS:
        raise ValueError("Unknown ownership mutant")
    result = copy.deepcopy(value)
    item = v.decode(result["cases"][0]["observed"])
    index = MUTANTS.index(name)
    item["milestones"][index]["owned_live"] = not item["milestones"][index][
        "owned_live"
    ]
    result["cases"][0]["observed"] = v.encode(item)
    return result

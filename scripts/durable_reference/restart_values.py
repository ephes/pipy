"""Declared generic restart observations; raw controls are retained separately."""

from __future__ import annotations

import copy

PHASE = "restart-parking-v1"
CASES = [
    {
        "id": "default-five",
        "limit": "default",
        "count": 4,
        "running": True,
        "action": "retry",
    },
    {"id": "explicit-two", "limit": 2, "count": 1, "running": True, "action": "retry"},
    {
        "id": "checkpoint-progress",
        "limit": 5,
        "count": 1,
        "running": True,
        "action": "progress",
    },
    {
        "id": "equal-memo-document-chatter",
        "limit": 5,
        "count": 1,
        "running": True,
        "action": "chatter",
    },
    {"id": "healthy-close", "limit": 5, "count": 1, "running": True, "action": "hold"},
    {
        "id": "successful-noop-enables",
        "limit": 5,
        "count": None,
        "running": False,
        "action": "noop",
    },
    {
        "id": "fresh-higher-unlimited",
        "limit": 5,
        "count": 5,
        "running": False,
        "action": "fresh",
    },
]
IDS = tuple(row["id"] for row in CASES)
RECIPE = {"schema_version": 1, "phase": PHASE, "cases": CASES}
SCOPE = {
    "common": "actual-registered-generic-task-recovery-parking-progress-close-private-retry-fresh-owner-policy",
    "controls": "actual-created-task-with-explicit-Session-commit-running-counter-seed; NOT physical-crash or tool-receipt authority",
    "public_difference": "native-product-reset-ACK-plus-explicit-resume-is-not-Pi-Harness.retryTask-global-enable",
    "raw_differences": [
        "complete-records",
        "IDs",
        "timestamps",
        "private-retry-return",
        "inspection-shape",
        "source-observations",
    ],
    "deferred": [
        "DE14b-live-policy-migration-orphan",
        "DE15/16-graph-abort",
        "DE17-attempt-backoff",
        "raw-SDK-int-domain-public-controls",
    ],
}
FIELDS = {
    "calls_at_open",
    "open",
    "parked",
    "after_action",
    "after_close",
    "after_reopen",
    "phases",
    "same_task",
    "same_input",
    "closed",
    "chatter_evidence",
}
MUTANTS = tuple(sorted(FIELDS | {"phase_count"}))


def count(row):
    return {"present": "restarts" in row, "value": row.get("restarts")}


def state(row):
    return {"status": row["state"]["status"], "count": count(row)}


def project(row):
    snapshots = [
        row[k]
        for k in (
            "before_open",
            "after_open",
            "after_action",
            "after_close",
            "after_reopen",
        )
    ]
    return {
        "calls_at_open": row["calls_at_open"],
        "open": state(row["after_open"]),
        "parked": row["parked"],
        "after_action": state(row["after_action"]),
        "after_close": state(row["after_close"]),
        "after_reopen": state(row["after_reopen"]),
        "phases": row["phases"],
        "same_task": len({r["id"] for r in snapshots}) == 1,
        "same_input": all(r["input"] == snapshots[0]["input"] for r in snapshots),
        "closed": row["closed"],
        "chatter_evidence": row["chatter_evidence"],
    }


def validate(value, d, v):
    d.shape(value, {"schema_version", "phase", "cases"}, "Restart trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Restart identity",
    )
    d.require(
        type(value["cases"]) is list and [r["id"] for r in value["cases"]] == list(IDS),
        "Restart cases",
    )
    for row in value["cases"]:
        d.shape(row, {"id", "observed"}, "Restart case")
        observed = v.decode(row["observed"])
        d.shape(observed, FIELDS, "Restart observation")
        d.require(
            type(observed["calls_at_open"]) is int and observed["calls_at_open"] >= 0,
            "Restart call count",
        )
        for key in ("same_task", "same_input", "parked"):
            d.require(type(observed[key]) is bool, "Restart predicate")
        for key in ("open", "after_action", "after_close", "after_reopen"):
            d.shape(observed[key], {"status", "count"}, "Restart state")
            d.require(
                observed[key]["status"] in {"pending", "running", "terminal"},
                "Restart status",
            )
            c = observed[key]["count"]
            d.shape(c, {"present", "value"}, "Restart count")
            d.require(
                type(c["present"]) is bool
                and (
                    type(c["value"]) in (int, float)
                    if c["present"]
                    else c["value"] is None
                ),
                "Restart presence",
            )
        d.require(
            type(observed["phases"]) is list and type(observed["closed"]) is list,
            "Restart lists",
        )
        for phase in observed["phases"]:
            d.shape(phase, {"phase", "count"}, "Restart phase")
            d.require(phase["phase"] in {"run", "next"}, "Restart phase name")
            d.shape(phase["count"], {"present", "value"}, "Restart phase count")
            c = phase["count"]
            d.require(
                type(c["present"]) is bool
                and (
                    type(c["value"]) in (int, float)
                    if c["present"]
                    else c["value"] is None
                ),
                "Restart phase count presence",
            )
        evidence = observed["chatter_evidence"]
        if evidence is not None:
            d.shape(evidence, {"document", "memo"}, "Restart chatter proof")
            d.require(
                type(evidence["document"]) is int and evidence["document"] >= 0,
                "Restart document write count",
            )


def successful(value, d, v):
    validate(value, d, v)
    for row, recipe in zip(value["cases"], CASES, strict=True):
        x = v.decode(row["observed"])
        action = recipe["action"]
        initial = recipe["count"]
        after = None if initial is None else initial + int(recipe["running"])
        d.require(
            x["calls_at_open"] == 0 and x["same_task"] and x["same_input"],
            "Restart actual identity/open",
        )
        d.require(
            x["open"]
            == {
                "status": "pending",
                "count": {"present": after is not None, "value": after},
            },
            "Restart recovery count",
        )
        d.require(
            x["parked"] == (action in {"retry", "fresh"}), "Restart actual parked"
        )
        terminal = action in {"retry", "noop", "fresh"}
        expected_count = None if terminal or action == "progress" else after
        d.require(
            x["after_action"]["status"] == ("terminal" if terminal else "running"),
            "Restart action status",
        )
        d.require(
            x["after_action"]["count"]
            == {"present": expected_count is not None, "value": expected_count},
            "Restart action count",
        )
        for key in ("after_close", "after_reopen"):
            d.require(
                x[key]
                == {
                    "status": "terminal" if terminal else "pending",
                    "count": {
                        "present": expected_count is not None,
                        "value": expected_count,
                    },
                },
                "Restart healthy close/reopen",
            )
        phases = [p["phase"] for p in x["phases"]]
        d.require(
            phases
            == (
                ["run", "next"]
                if action == "progress"
                else ["run", "run"]
                if action == "fresh"
                else ["run"]
            ),
            "Restart actual phases",
        )
        expected_phase_counts = (
            [after, None]
            if action == "progress"
            else [5, 1e100]
            if action == "fresh"
            else [None]
            if action in {"retry", "noop"}
            else [after]
        )
        d.require(
            [p["count"] for p in x["phases"]]
            == [{"present": c is not None, "value": c} for c in expected_phase_counts],
            "Restart count at actual phase entry",
        )
        d.require(
            x["chatter_evidence"]
            == (
                {"document": 1, "memo": {"kept": True}} if action == "chatter" else None
            ),
            "Restart actual memo/document chatter",
        )
        d.require(
            x["closed"] and all(reason == "closed" for reason in x["closed"]),
            "Restart actual close reason",
        )


def mutate(value, name, v):
    result = copy.deepcopy(value)
    x = v.decode(result["cases"][0]["observed"])
    if name == "calls_at_open":
        x[name] += 1
    elif name in {"parked", "same_task", "same_input"}:
        x[name] = not x[name]
    elif name in {"open", "after_action", "after_close", "after_reopen"}:
        x[name]["count"] = {"present": True, "value": 17}
    elif name == "phases":
        x[name] = []
    elif name == "closed":
        x[name] = ["failed"]
    elif name == "phase_count":
        x["phases"][0]["count"] = {"present": True, "value": 17}
    elif name == "chatter_evidence":
        x[name] = {"document": 0, "memo": None}
    else:
        raise ValueError("Unknown restart mutant")
    result["cases"][0]["observed"] = v.encode(x)
    return result

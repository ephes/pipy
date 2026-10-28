"""Finite actual generic live-policy observations; full raw records stay retained."""

from __future__ import annotations

import copy

PHASE = "restart-policy-live-v1"
CASES = [
    {
        "id": "raise-finite",
        "initial": 5,
        "count": 5,
        "update": 6,
        "fresh": 5,
        "async_setter": False,
    },
    {
        "id": "raise-unlimited",
        "initial": 5,
        "count": 1e100,
        "update": "Infinity",
        "fresh": 5,
        "async_setter": True,
    },
    {
        "id": "lower-before-admission",
        "initial": 6,
        "count": 5,
        "update": 5,
        "fresh": 6,
        "async_setter": False,
    },
    {
        "id": "same-finite-remains-parked",
        "initial": 5,
        "count": 5,
        "update": 5,
        "fresh": 6,
        "async_setter": True,
    },
    {
        "id": "same-unlimited",
        "initial": "Infinity",
        "count": 1e100,
        "update": "Infinity",
        "fresh": 5,
        "async_setter": False,
    },
]
IDS = tuple(row["id"] for row in CASES)
RECIPE = {"schema_version": 1, "phase": PHASE, "cases": CASES}
SCOPE = {
    "common": "generic-current-owner-live-budget-read-on-later-explicit-resume-no-task-counter-reset",
    "controls": "actual-created-pending-task-explicit-counter-seed; native reservation observer; Pi parked first-resume FIFO Session-line observer, not graph idle; no physical SIGKILL",
    "reference_update": "actual mutable HarnessOptions.settings host object, then explicit Harness.resume; no Pi setter API",
    "native_only": [
        "set/aset boolean ACK",
        "no setter notify/enable",
        "owner effective versus standalone supplied policy",
        "raw SDK numeric input-domain",
    ],
    "raw_differences": [
        "complete TaskRecords",
        "owner/standalone inspection",
        "native ACK",
        "native reserve observer",
        "Pi parked first-resume Session-line observer",
        "IDs/timestamps",
        "actual source observations",
    ],
    "deferred": [
        "public Product live settings",
        "settings getters/listeners",
        "registry/handover",
        "DE15/16/23",
        "physical crash/resource proof",
    ],
}
FIELDS = {
    "calls_at_open",
    "calls_at_update",
    "unchanged_record",
    "unchanged_counter",
    "parked_before",
    "parked_after",
    "calls_after_resume",
    "after_resume",
    "calls_before_final_resume",
    "after_final_resume",
    "phase_counts",
    "same_task_input",
    "closed",
}
MUTANTS = tuple(sorted(FIELDS))


def count(row):
    return {"present": "restarts" in row, "value": row.get("restarts")}


def state(row):
    return {"status": row["state"]["status"], "count": count(row)}


def project(row):
    records = [
        row[k]
        for k in (
            "after_open",
            "before_update",
            "after_update",
            "after_resume",
            "before_final_resume",
            "after_final_resume",
            "after_close",
        )
    ]
    return {
        "calls_at_open": row["calls_at_open"],
        "calls_at_update": row["calls_at_update"],
        "unchanged_record": row["before_update"] == row["after_update"],
        "unchanged_counter": count(row["before_update"]) == count(row["after_update"]),
        "parked_before": row["parked_before"],
        "parked_after": row["parked_after"],
        "calls_after_resume": row["calls_after_resume"],
        "after_resume": state(row["after_resume"]),
        "calls_before_final_resume": row["calls_before_final_resume"],
        "after_final_resume": state(row["after_final_resume"]),
        "phase_counts": row["phase_counts"],
        "same_task_input": all(
            x["id"] == records[0]["id"] and x["input"] == records[0]["input"]
            for x in records
        ),
        "closed": row["closed"],
    }


def validate(value, d, v):
    d.shape(value, {"schema_version", "phase", "cases"}, "Live-policy trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Live-policy identity",
    )
    d.require(
        type(value["cases"]) is list and [x["id"] for x in value["cases"]] == list(IDS),
        "Live-policy cases",
    )
    for row in value["cases"]:
        d.shape(row, {"id", "observed"}, "Live-policy case")
        x = v.decode(row["observed"])
        d.shape(x, FIELDS, "Live-policy observation")
        for name in (
            "calls_at_open",
            "calls_at_update",
            "calls_after_resume",
            "calls_before_final_resume",
        ):
            d.require(
                type(x[name]) is int and x[name] >= 0, "Live-policy actual call count"
            )
        for name in (
            "unchanged_record",
            "unchanged_counter",
            "parked_before",
            "parked_after",
            "same_task_input",
        ):
            d.require(type(x[name]) is bool, "Live-policy predicate")
        for name in ("after_resume", "after_final_resume"):
            d.shape(x[name], {"status", "count"}, "Live-policy state")
            d.require(
                x[name]["status"] in {"pending", "running", "terminal"},
                "Live-policy task state",
            )
            _count(x[name]["count"], d)
        d.require(
            type(x["phase_counts"]) is list
            and type(x["closed"]) is list
            and all(type(z) is str for z in x["closed"]),
            "Live-policy observed lists",
        )
        for c in x["phase_counts"]:
            _count(c, d)


def _count(value, d):
    d.shape(value, {"present", "value"}, "Live-policy counter")
    d.require(
        type(value["present"]) is bool
        and (
            type(value["value"]) in (int, float)
            if value["present"]
            else value["value"] is None
        ),
        "Live-policy counter presence",
    )


def successful(value, d, v):
    validate(value, d, v)
    for row, recipe in zip(value["cases"], CASES, strict=True):
        x = v.decode(row["observed"])
        blocked = recipe["update"] != "Infinity" and recipe["count"] >= recipe["update"]
        prior = recipe["initial"] != "Infinity" and recipe["count"] >= recipe["initial"]
        d.require(
            x["calls_at_open"] == x["calls_at_update"] == 0
            and x["unchanged_record"]
            and x["unchanged_counter"]
            and x["same_task_input"],
            "Live-policy unchanged authority/noninvocation",
        )
        d.require(
            x["parked_before"] == prior and x["parked_after"] == blocked,
            "Live-policy actual budget eligibility",
        )
        d.require(
            x["calls_after_resume"]
            == x["calls_before_final_resume"]
            == (0 if blocked else 1),
            "Live-policy actual first admission",
        )
        wanted = {
            "status": "pending" if blocked else "terminal",
            "count": {
                "present": blocked,
                "value": recipe["count"] if blocked else None,
            },
        }
        d.require(x["after_resume"] == wanted, "Live-policy first resume state/counter")
        d.require(
            x["after_final_resume"]
            == {"status": "terminal", "count": {"present": False, "value": None}},
            "Live-policy actual final terminal",
        )
        d.require(
            x["phase_counts"] == [{"present": True, "value": recipe["count"]}],
            "Live-policy same counter reaches sole actual invocation",
        )
        d.require(
            x["closed"] == ["closed", "closed"], "Live-policy actual joined closes"
        )


def mutate(value, name, v):
    changed = copy.deepcopy(value)
    index = 2 if name in {"calls_after_resume", "after_resume"} else 0
    x = v.decode(changed["cases"][index]["observed"])
    if name in {
        "calls_at_open",
        "calls_at_update",
        "calls_after_resume",
        "calls_before_final_resume",
    }:
        x[name] += 1
    elif name in {
        "unchanged_record",
        "unchanged_counter",
        "parked_before",
        "parked_after",
        "same_task_input",
    }:
        x[name] = not x[name]
    elif name in {"after_resume", "after_final_resume"}:
        x[name]["count"] = {"present": True, "value": 0}
    elif name == "phase_counts":
        x[name] = [{"present": True, "value": 0}]
    elif name == "closed":
        x[name] = ["faulted", "closed"]
    else:
        raise ValueError("Unknown live-policy mutant")
    changed["cases"][index]["observed"] = v.encode(x)
    return changed

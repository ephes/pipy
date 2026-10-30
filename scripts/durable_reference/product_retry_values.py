"""Closed retry-admission facts; full lane records and histories stay raw."""

from __future__ import annotations

import copy

PHASE = "task-product-retry-enable-v1"
CASES = [
    {"id": "genuine-parked-sqlite", "parked": True, "restart_limit": 1},
    {"id": "live-noop-sqlite", "parked": False, "restart_limit": 1},
]
IDS = tuple(case["id"] for case in CASES)
RECIPE = {"schema_version": 1, "phase": PHASE, "cases": CASES}
FIELDS = (
    "parked_before",
    "counter_absent_after_reset",
    "same_target",
    "returned_before_release",
    "completion_pending_at_gate",
    "completed_after_release",
)
MUTANTS = FIELDS
SCOPE = {
    "common": "bounded-retry-admission-owned-progress-common-v1",
    "controls": "Genuine SQLite running cut/reopen at ceiling1; live no-op; real held callback and public result release",
    "normalization": ["Role IDs only; no raw record/error/history normalization"],
    "native_only": [
        "Public selected Product authority and DurableTaskRetry ACK",
        "Existing receipt first-winner/recovery and C1/C2/catalog fences",
        "Actual supported callback kind may differ from Pi generic phase",
    ],
    "reference_only": [
        "Public FullHarness retryTask void result and generic Task authority",
        "waitForTask starts only after retry return plus held callback evidence",
        "Unchanged installed settleSchedulerOutcome hook; no private enabled/cache probe",
    ],
    "raw_differences": [
        "Complete TaskRecords/inspection/reset commits/outcomes/callbacks",
        "SQLite all tables/revisions, cut process records and owner closure",
        "Void versus Native ACK, selected root and receipt trajectories",
    ],
    "deferred": [
        "Generic owned executor parity",
        "Arbitrary Product tasks",
        "Parent closure",
    ],
}


def checked_recipe(value):
    if type(value) is not dict or set(value) != set(RECIPE):
        raise ValueError("Closed product retry recipe")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Product retry recipe version")
    if value["phase"] != PHASE or type(value["cases"]) is not list:
        raise ValueError("Product retry recipe identity")
    if len(value["cases"]) != len(CASES):
        raise ValueError("Product retry finite cases")
    for row, expected in zip(value["cases"], CASES, strict=True):
        if type(row) is not dict or set(row) != set(expected):
            raise ValueError("Closed product retry case")
        if any(type(row[k]) is not type(v) or row[k] != v for k, v in expected.items()):
            raise ValueError("Exact typed product retry case")
    return value


def _lane(row, case):
    if row["role"] == "pi":
        side = row["reference_only"]
        state = row["inspection_before"]["state"]
        parked = (
            state.get("kind") == "blocked" and state.get("reason") == "restart_limit"
        )
        if side["retry_return"] != {"kind": "void"}:
            raise ValueError("Actual Pi void return")
        if side["wait_started_before_gate"] is not False:
            raise ValueError("Pi wait must not enable the retry witness")
        if side["private_enabled_or_cache"] != "NOT_OBSERVED":
            raise ValueError("No private Pi scheduling observation")
        completed = (
            row["records"]["after_release"]["state"]["outcome"]["status"] == "completed"
        )
    else:
        parked = row["inspection_before"]["restart_parked"]
        ack = row["native_only"]["retry_ack"]
        if (
            type(ack.get("task_id")) is not int
            or type(ack.get("reset")) is not bool
            or ack != {"task_id": row["task_id"], "reset": case["parked"]}
        ):
            raise ValueError("Actual selected Product retry ACK")
        completed = (
            row["native_only"]["run_result"]["inspection"]["outcome"] == "completed"
        )
    if type(parked) is not bool:
        raise ValueError("Exact parking observation")
    return parked, completed


def _origin(row, case, before):
    cut = row["cut"]
    if row["control"]["typed_counter_seed"] is not False:
        raise ValueError("No typed counter seed in either case")
    if case["parked"]:
        if (
            type(cut) is not dict
            or cut["exit_signal"] != "SIGKILL"
            or cut["reaped"] is not True
            or row["control"]["typed_counter_seed"] is not False
            or type(before.get("restarts")) is not int
            or before["restarts"] != 1
            or row["reset"]["before"] != before
        ):
            raise ValueError("Genuine one-cut recovered parking/reset window")
    elif cut is not None or "restarts" in before:
        raise ValueError("Actual no-op without artificial count")
    if row["control"]["arbitrary_terminal_mutation"] is not False:
        raise ValueError("Real callback execution only")


def _lifecycle(row):
    if row["closed"] != ["closed"]:
        raise ValueError("Actual post-release owner closure")
    if [event["seq"] for event in row["events"]] != list(
        range(1, len(row["events"]) + 1)
    ):
        raise ValueError("Actual event ledger sequence")
    if any(type(event["seq"]) is not int for event in row["events"]):
        raise ValueError("Exact event sequence integer")
    names = [event["event"] for event in row["events"]]
    positions = [
        names.index(name)
        for name in ("retry_return", "gate_read", "release", "complete", "closed")
    ]
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        raise ValueError("Public retry/gate/release actual ordering")


def project(row):
    case = next(case for case in CASES if case["id"] == row["id"])
    if row["role"] not in ("pi", "native") or row["backend_kind"] != "sqlite":
        raise ValueError("Actual retry lane/backend")
    records, gate = row["records"], row["gate"]
    if set(gate) != {
        "retry_returned",
        "callback_entered",
        "released",
        "result_settled",
    } or not all(type(x) is bool for x in gate.values()):
        raise ValueError("Exact raw gate booleans")
    before, reset = records["before_retry"], row["reset"]["after"]
    parked, completed = _lane(row, case)
    _origin(row, case, before)
    _lifecycle(row)
    return {
        "parked_before": parked,
        "counter_absent_after_reset": "restarts" not in reset,
        "same_target": all(
            record["id"] == row["task_id"] for record in records.values()
        ),
        "returned_before_release": gate["retry_returned"] and not gate["released"],
        "completion_pending_at_gate": gate["callback_entered"]
        and not gate["result_settled"],
        "completed_after_release": completed,
    }


def validate(value, d, v):
    d.shape(value, {"schema_version", "phase", "cases"}, "Product retry trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Product retry identity",
    )
    d.require(
        type(value["cases"]) is list and [r["id"] for r in value["cases"]] == list(IDS),
        "Product retry case order",
    )
    for row in value["cases"]:
        d.shape(row, {"id", "observed"}, "Product retry case")
        observed = v.decode(row["observed"])
        d.shape(observed, set(FIELDS), "Closed product retry facts")
        d.require(
            all(type(observed[k]) is bool for k in FIELDS),
            "Exact product retry booleans",
        )


def successful(value, d, v):
    validate(value, d, v)
    for row, case in zip(value["cases"], CASES, strict=True):
        observed = v.decode(row["observed"])
        d.require(
            observed
            == {
                name: case["parked"] if name == "parked_before" else True
                for name in FIELDS
            },
            "Actual retry admission/progress",
        )


def mutate(value, name, v):
    if name not in MUTANTS:
        raise ValueError("Unknown product retry mutant")
    result = copy.deepcopy(value)
    item = v.decode(result["cases"][0]["observed"])
    item[name] = not item[name]
    result["cases"][0]["observed"] = v.encode(item)
    return result

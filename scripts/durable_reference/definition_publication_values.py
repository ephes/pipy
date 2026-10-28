"""Finite created-task publication observations; all raw rows remain retained."""

from __future__ import annotations

import copy

PHASE = "task-definition-publication-idle-v1"
CASES = [
    {"id": name}
    for name in (
        "missing-install",
        "enabled-failure-cache",
        "failed-remove-readd-and-overwrite",
        "order-and-snapshot",
        "absent-remove",
    )
]
IDS = tuple(row["id"] for row in CASES)
RECIPE = {"schema_version": 1, "phase": PHASE, "cases": CASES}
NAMES = (
    "fixture.definition.main",
    "fixture.definition.left",
    "fixture.definition.right",
    "fixture.definition.unrelated",
    "fixture.definition.absent",
)
LABELS = {
    IDS[0]: ["open", "install", "resume"],
    IDS[1]: ["open", "first-resume", "identical", "unrelated", "replacement-success"],
    IDS[2]: [
        "open",
        "first-resume",
        "remove",
        "readd-same",
        "replacement-failed",
        "restore-A",
        "replacement-success",
    ],
    IDS[3]: ["open", "replace-middle", "remove-middle", "reappend-middle", "resume"],
    IDS[4]: ["open", "absent-remove", "identical", "resume"],
}
FIELDS = {
    "labels",
    "states",
    "migration_counts",
    "handler_counts",
    "initializer_counts",
    "record_preservation",
    "order_steps",
    "lookup_steps",
    "snapshot_stable",
    "storage_preservation",
    "task_identity",
    "terminal",
    "closed",
}
MUTANTS = tuple(sorted(FIELDS - {"labels"}))
SCOPE = {
    "common": "generic-created-task-definition-publication-and-source-identity-migration-memo",
    "controls": "native private generation read and forwarded real quiescent/notify observers; Pi FIFO reservation-line read, not drain or graph idle; temporary SQLite only",
    "native_only": [
        "bool ACK",
        "idle/busy/lifecycle gate",
        "captured ownership/reflection",
        "cancellation joining",
        "no registry listeners",
    ],
    "reference_only": [
        "full extension registry",
        "synchronous registry listener notification",
        "source Task retained directly",
    ],
    "raw_differences": [
        "complete records",
        "SQLite all-table/revision snapshots",
        "actual callback logs",
        "ordered snapshots/lookup",
        "ACK/notifications/retirement",
        "actual origins/IDs/timestamps",
    ],
    "deferred": [
        "Product/public registry",
        "listeners parity",
        "active handover",
        "opaque/model/tool migration",
        "physical crash",
        "DE15/16/23",
    ],
}


def project(raw):
    steps = raw["steps"]
    for step in steps:
        ledger = step["callback_rows"]
        if [r["seq"] for r in ledger] != list(range(1, len(ledger) + 1)) or any(
            r["kind"] not in step["counts"] for r in ledger
        ):
            raise ValueError("Actual callback SQLite sequence")
        if {
            name: sum(r["kind"] == name for r in ledger) for name in step["counts"]
        } != step["counts"]:
            raise ValueError("Actual callback SQLite/count join")
    if [r["kind"] for r in raw["callback_rows"]] != raw["callback_log"]:
        raise ValueError("Final callback SQLite/log join")

    def state(record):
        return {
            "status": record["state"]["status"],
            "version": record["version"],
            "restarts": {
                "present": "restarts" in record,
                "value": record.get("restarts"),
            },
        }

    return {
        "labels": [s["label"] for s in steps],
        "states": [state(s["record"]) for s in steps],
        "migration_counts": [s["counts"]["migrate"] for s in steps],
        "handler_counts": [s["counts"]["handler"] for s in steps],
        "initializer_counts": [s["counts"]["initial"] for s in steps],
        "record_preservation": [
            None if s["before_record"] is None else s["before_record"] == s["record"]
            for s in steps
        ],
        "order_steps": [s["order"] for s in steps],
        "lookup_steps": [s["lookup"] for s in steps],
        "snapshot_stable": [s["old_snapshot"] == raw["old_snapshot"] for s in steps],
        "storage_preservation": [
            None if s["sql"] is None else s["sql"]["before"] == s["sql"]["after"]
            for s in steps
        ],
        "task_identity": all(
            s["record"]["id"] == raw["created"]["id"]
            and s["record"]["input"] == raw["created"]["input"]
            for s in steps
        ),
        "terminal": {
            "status": raw["final_record"]["state"]["outcome"]["status"],
            "version": raw["final_record"]["version"],
            "result": raw["final_record"]["state"]["outcome"].get("result"),
        },
        "closed": raw["closed"],
    }


def validate(value, d, v):
    d.shape(value, {"schema_version", "phase", "cases"}, "Publication trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Publication identity",
    )
    d.require(
        type(value["cases"]) is list and [x["id"] for x in value["cases"]] == list(IDS),
        "Publication cases",
    )
    for row in value["cases"]:
        d.shape(row, {"id", "observed"}, "Publication case")
        x = v.decode(row["observed"])
        d.shape(x, FIELDS, "Publication observations")
        size = len(LABELS[row["id"]])
        d.require(x["labels"] == LABELS[row["id"]], "Actual action order")
        for key in FIELDS - {"task_identity", "terminal", "closed"}:
            d.require(
                type(x[key]) is list and len(x[key]) == size,
                "Publication observed vector",
            )
        for key in ("migration_counts", "handler_counts", "initializer_counts"):
            d.require(
                all(type(n) is int and n >= 0 for n in x[key]), "Actual callback counts"
            )
        for key in ("record_preservation", "storage_preservation"):
            d.require(
                all(n is None or type(n) is bool for n in x[key]),
                "Optional actual boundary",
            )
        d.require(
            type(x["task_identity"]) is bool
            and all(type(n) is bool for n in x["snapshot_stable"]),
            "Publication predicates",
        )
        for state in x["states"]:
            d.shape(state, {"status", "version", "restarts"}, "Task state")
            d.require(
                state["status"] in {"pending", "running", "terminal"}
                and type(state["version"]) in (int, float),
                "Task version/state",
            )
            d.shape(state["restarts"], {"present", "value"}, "Task counter")
            c = state["restarts"]
            d.require(
                type(c["present"]) is bool
                and (
                    type(c["value"]) in (int, float)
                    if c["present"]
                    else c["value"] is None
                ),
                "Task counter presence",
            )
        for order in x["order_steps"]:
            d.require(
                type(order) is list
                and len(set(order)) == len(order)
                and all(n in NAMES for n in order),
                "Actual generic name order",
            )
        for lookup in x["lookup_steps"]:
            d.shape(lookup, set(NAMES), "Actual lookup")
            for item in lookup.values():
                if item is not None:
                    d.shape(item, {"version", "migrate"}, "Lookup metadata")
                    d.require(
                        type(item["version"]) in (int, float)
                        and type(item["migrate"]) is bool,
                        "Callback-free lookup",
                    )
        d.shape(x["terminal"], {"status", "version", "result"}, "Terminal observation")
        d.require(
            x["terminal"]["status"] in {"completed", "faulted"}
            and type(x["terminal"]["version"]) in (int, float),
            "Terminal metadata",
        )
        d.require(
            type(x["closed"]) is list and all(type(s) is str for s in x["closed"]),
            "Closed owners",
        )


def successful(value, d, v):
    validate(value, d, v)
    for row in value["cases"]:
        x = v.decode(row["observed"])
        name = row["id"]
        size = len(LABELS[name])
        failing = name in IDS[1:3]
        wanted = {
            IDS[0]: [0, 0, 0],
            IDS[1]: [0, 1, 1, 1, 2],
            IDS[2]: [0, 1, 1, 1, 2, 3, 4],
            IDS[3]: [0] * 5,
            IDS[4]: [0] * 4,
        }[name]
        d.require(
            x["migration_counts"] == wanted
            and x["handler_counts"] == [0] * (size - 1) + [1]
            and x["initializer_counts"] == [1] * size,
            "Actual migration identity/one final invocation",
        )
        d.require(
            all(z is not False for z in x["record_preservation"])
            and all(z is not False for z in x["storage_preservation"])
            and all(x["snapshot_stable"])
            and x["task_identity"],
            "Owned record/storage/snapshot authority",
        )
        d.require(
            all(
                z["status"] == "pending"
                and z["version"] == 1
                and z["restarts"] == {"present": False, "value": None}
                for z in x["states"][:-1]
            ),
            "Failed migration leaves actual pending record/counter",
        )
        d.require(
            x["terminal"]
            == {
                "status": "completed",
                "version": 2 if failing else 1,
                "result": {"case": name},
            }
            and x["states"][-1]["status"] == "terminal",
            "Actual terminal successor",
        )
        d.require(
            x["closed"] == (["closed", "closed"] if failing else ["closed"]),
            "Actual joined owner closes",
        )
        if name == IDS[3]:
            main, left, right = NAMES[:3]
            d.require(
                x["order_steps"]
                == [
                    [main, left, right],
                    [main, left, right],
                    [main, right],
                    [main, right, left],
                    [main, right, left],
                ],
                "Replacement position/reappend",
            )
        if name == IDS[2]:
            d.require(
                x["order_steps"][2] == [] and x["lookup_steps"][2][NAMES[0]] is None,
                "Actual removal",
            )
        if name == IDS[4]:
            d.require(x["order_steps"][:3] == [[NAMES[0]]] * 3, "No-change publication")


def mutate(value, name, v):
    changed = copy.deepcopy(value)
    row = changed["cases"][0]
    x = v.decode(row["observed"])
    if name in {"migration_counts", "handler_counts", "initializer_counts"}:
        x[name][0] += 1
    elif name == "states":
        x[name][0]["status"] = "running"
    elif name in {"record_preservation", "storage_preservation"}:
        x[name][1] = False
    elif name == "order_steps":
        x[name][0] = [NAMES[3]]
    elif name == "lookup_steps":
        x[name][0][NAMES[4]] = {"version": 1, "migrate": False}
    elif name == "snapshot_stable":
        x[name][0] = False
    elif name == "task_identity":
        x[name] = False
    elif name == "terminal":
        x[name]["status"] = "faulted"
    elif name == "closed":
        x[name][0] = "failed"
    else:
        raise ValueError("Unknown publication mutant")
    row["observed"] = v.encode(x)
    return changed

"""Cold generic migration common observations; full platform records remain raw."""

from __future__ import annotations

import copy
import math

PHASE = "task-migration-cold-v1"
CASES = [
    {
        "id": "equal-signed-zero",
        "stored": -0.0,
        "current": 0,
        "mode": "same",
        "blocked": None,
        "parked": False,
    },
    {
        "id": "fractional-content",
        "stored": -0.5,
        "current": 0.25,
        "mode": "change",
        "blocked": None,
        "parked": False,
    },
    {
        "id": "version-only",
        "stored": 1,
        "current": 2,
        "mode": "same",
        "blocked": None,
        "parked": False,
    },
    {
        "id": "missing-definition",
        "stored": 1,
        "current": None,
        "mode": "none",
        "blocked": "definition_missing",
        "parked": False,
    },
    {
        "id": "stored-newer",
        "stored": 2,
        "current": 1,
        "mode": "change",
        "blocked": "stored_version_newer",
        "parked": False,
    },
    {
        "id": "missing-migrator",
        "stored": 1,
        "current": 2,
        "mode": "none",
        "blocked": "migration_failed",
        "parked": False,
    },
    {
        "id": "throw-once",
        "stored": 1,
        "current": 2,
        "mode": "throw",
        "blocked": "migration_failed",
        "parked": False,
    },
    {
        "id": "invalid-once",
        "stored": 1,
        "current": 2,
        "mode": "invalid",
        "blocked": "migration_failed",
        "parked": False,
    },
    {
        "id": "parked-migration",
        "stored": 1,
        "current": 2,
        "mode": "change",
        "blocked": None,
        "parked": True,
    },
    {
        "id": "blocked-zero-current",
        "stored": -2.0,
        "current": 0,
        "mode": "none",
        "blocked": "migration_failed",
        "parked": False,
    },
]
IDS = tuple(c["id"] for c in CASES)
RECIPE = {"schema_version": 1, "phase": PHASE, "cases": CASES}
SCOPE = {
    "common": "actual-created-generic-task-cold-owner-version-fit-migration-failure-cache-second-candidate-fresh-owner",
    "controls": "explicit-Session-task-row-checkpoint-memo-restart-seeds-in-private-SQLite; NOT physical-crash or tool-receipt authority",
    "public_difference": "native-private-fixed-catalog-retry-is-not-Pi-live-registry-or-public-global-enable; no Product migration API parity",
    "raw_differences": [
        "complete-records",
        "IDs",
        "timestamps",
        "inspection-shape-and-error",
        "native-detached-callback-arguments-vs-Pi-stored-arguments",
        "reserve-pass-cardinality",
        "private-retry-return",
        "source-and-lifetime-observations",
    ],
    "version_equivalence": "Only exact finite numeric Task version zero is projected to common0; all raw handler/Task records retained; no payload/checkpoint normalization",
    "deferred": [
        "DE14b-live-registration-replacement-orphan",
        "DE15/16-graph-abort",
        "public-Product-upgrades",
        "native-only-invalid-JSON-awaitables-reentry-physical-cuts",
    ],
}
FIELDS = {
    "calls_at_open",
    "initializers",
    "parked_at_open",
    "migration_calls",
    "before_restore_calls",
    "repeated_calls",
    "first_handler",
    "second_handlers",
    "blocked_reason",
    "blocked_unchanged",
    "identity_preserved",
    "closed",
    "cold_equal_calls",
}
MUTANTS = tuple(sorted(FIELDS | {"version"}))


def count(record):
    return {"present": "restarts" in record, "value": record.get("restarts")}


def handler(record):
    version = record["version"]
    if type(version) not in (int, float) or not math.isfinite(version):
        raise ValueError("Expected finite numeric Task version")
    return {
        "version": 0 if version == 0 else version,
        "input": record["input"],
        "checkpoint": record["state"]["checkpoint"],
        "memos": record.get("memos"),
        "restarts": count(record),
    }


def project(row):
    return {key: row[key] for key in FIELDS}


def validate(value, d, v):
    d.shape(value, {"schema_version", "phase", "cases"}, "Migration trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Migration trace identity",
    )
    d.require(
        type(value["cases"]) is list and [r["id"] for r in value["cases"]] == list(IDS),
        "Migration case order",
    )
    for row in value["cases"]:
        d.shape(row, {"id", "observed"}, "Migration case")
        x = v.decode(row["observed"])
        d.shape(x, FIELDS, "Migration common fields")
        for key in (
            "calls_at_open",
            "initializers",
            "before_restore_calls",
            "repeated_calls",
            "second_handlers",
            "cold_equal_calls",
        ):
            d.require(type(x[key]) is int and x[key] >= 0, "Migration count")
        for key in ("parked_at_open", "blocked_unchanged", "identity_preserved"):
            d.require(type(x[key]) is bool, "Migration boolean")
        d.require(
            x["blocked_reason"]
            in {None, "definition_missing", "stored_version_newer", "migration_failed"},
            "Migration reason",
        )
        d.require(
            type(x["migration_calls"]) is list
            and type(x["closed"]) is list
            and all(type(y) is str for y in x["closed"]),
            "Migration lists",
        )
        for call in x["migration_calls"]:
            d.shape(
                call, {"input", "checkpoint", "from_version", "owner"}, "Migration call"
            )
            d.require(
                type(call["from_version"]) in (int, float)
                and call["owner"] in {"first", "restore"},
                "Migration actual callback",
            )
        first = x["first_handler"]
        if first is not None:
            d.shape(
                first,
                {"version", "input", "checkpoint", "memos", "restarts"},
                "Migration admitted handler",
            )
            d.require(
                type(first["version"]) in (int, float)
                and math.isfinite(first["version"]),
                "Migration finite Task version",
            )
            d.shape(
                first["restarts"], {"present", "value"}, "Migration restart presence"
            )
            d.require(
                type(first["restarts"]["present"]) is bool,
                "Migration restart presence flag",
            )


def successful(value, d, v):
    validate(value, d, v)
    for row, case in zip(value["cases"], CASES, strict=True):
        x = v.decode(row["observed"])
        attempted = case["mode"] in {"throw", "invalid"} or (
            case["blocked"] is None and case["current"] != case["stored"]
        )
        first_count = int(attempted)
        blocked = case["blocked"] is not None
        expected_calls = []
        original = {"fixture": case["id"], "n": 1}
        checkpoint = {"phase": "run", "n": 2}
        if attempted:
            expected_calls.append(
                {
                    "input": original,
                    "checkpoint": checkpoint,
                    "from_version": case["stored"],
                    "owner": "first",
                }
            )
        if blocked:
            expected_calls.append(
                {
                    "input": original,
                    "checkpoint": checkpoint,
                    "from_version": case["stored"],
                    "owner": "restore",
                }
            )
        d.require(
            x["calls_at_open"] == 0
            and x["initializers"] == 1
            and x["parked_at_open"] == case["parked"],
            "Migration cold admission",
        )
        d.require(
            d.same(x["migration_calls"], expected_calls)
            and x["before_restore_calls"] == first_count
            and x["repeated_calls"] == first_count
            and x["cold_equal_calls"] == len(expected_calls),
            "Migration actual calls/cache/equal cold owner",
        )
        d.require(
            x["blocked_reason"] == case["blocked"]
            and x["blocked_unchanged"]
            and x["identity_preserved"],
            "Migration no-write/identity",
        )
        d.require(
            x["second_handlers"] == int(case["mode"] in {"throw", "invalid"}),
            "Migration following eligible candidate",
        )
        current = case["current"] if case["current"] is not None else case["stored"]
        target = max(case["stored"], current) + 1 if blocked else case["current"]
        changed = blocked or case["mode"] == "change"
        d.require(
            d.same(
                x["first_handler"],
                {
                    "version": target,
                    "input": {**original, "migrated": True} if changed else original,
                    "checkpoint": {"phase": "run", "n": 3} if changed else checkpoint,
                    "memos": {"fixture.memo": {"kept": True}},
                    "restarts": {
                        "present": not case["parked"],
                        "value": None if case["parked"] else 2,
                    },
                },
            ),
            "Migration first handler reads committed values/memos/count",
        )
        d.require(
            x["closed"] and all(y == "closed" for y in x["closed"]),
            "Migration actual healthy owner close",
        )


def mutate(value, name, v):
    result = copy.deepcopy(value)
    # Failure-cache and second-candidate mutants target their actual discriminator case.
    index = (
        6
        if name
        in {
            "before_restore_calls",
            "repeated_calls",
            "second_handlers",
            "blocked_reason",
            "blocked_unchanged",
        }
        else 1
    )
    x = v.decode(result["cases"][index]["observed"])
    if name in {
        "calls_at_open",
        "initializers",
        "before_restore_calls",
        "repeated_calls",
        "second_handlers",
        "cold_equal_calls",
    }:
        x[name] += 1
    elif name in {"parked_at_open", "blocked_unchanged", "identity_preserved"}:
        x[name] = not x[name]
    elif name == "migration_calls":
        x[name][0]["from_version"] += 7
    elif name == "version":
        x["first_handler"]["version"] += 1
    elif name == "first_handler":
        x[name]["checkpoint"]["n"] += 1
    elif name == "blocked_reason":
        x[name] = "definition_missing"
    elif name == "closed":
        x[name] = ["failed"]
    else:
        raise ValueError("Unknown migration mutant")
    result["cases"][index]["observed"] = v.encode(x)
    return result

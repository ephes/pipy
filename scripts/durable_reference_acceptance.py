"""Verification-only fresh acceptance, workload evidence and exhaustive coverage index."""

from __future__ import annotations

import copy
import json
import math
import platform
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

WORKLOAD = "storage-baseline-v1"
READS = [
    "exact entry lookup",
    "entry page scan (100)",
    "filtered task scan (50)",
    "exact document address among many",
    *[f"document replay tail ({n})" for n in (0, 16, 128, 1024)],
    "ancient historical read before newer base",
    "recent historical read after newer base",
    "fork-depth history scan (100)",
    "fork-depth head lookup",
]
WRITES = [
    "commit one entry",
    "commit 100 entries",
    "commit mixed entry/task/submission/document",
]
ORACLES = {
    "lost-field": "harness-round-v1",
    "stale-head": "context-heads-v1",
    "duplicate-effect": "tool-recovery-v1",
    "post-abort-write": "durable-abort-v1",
}
VERIFICATION = {
    "tests/durable/test_pi_comparison.py",
    "tests/durable/test_pi_comparison_cases.py",
    "tests/durable/test_pi_comparison_acceptance.py",
    "tests/durable/test_pi_comparison_coverage.py",
    "tests/durable/test_reference_codec.mts",
    "scripts/durable_reference_artifacts.py",
    "tests/durable/test_reference_artifacts.py",
    ".github/workflows/ci.yml",
}
PLAN = "docs/specs/2026-10-19-durable-execution-plan.md"
CHILDREN = {
    "DE13c1",
    "DE13b",
    "DE13a",
    "DE05a",
    "DE05b",
    "DE04b1b1",
    "DE04b1b2",
    "DE04b1b3",
    "DE04b1b3a",
    "DE04b1b3b",
    "DE04b1a",
    "DE04b1b",
    "DE32a",
    "DE32b",
    "DE01a",
    "DE01b1",
    "DE01b2",
    "DE01b3",
    "DE01b3a",
    "DE01b3b",
    "DE02a",
    "DE02a1",
    "DE02a2",
    "DE02a2a",
    "DE02a2b",
    "DE02b",
    "DE02b1a",
    "DE02b1b",
    "DE02b2",
    "DE02b2a",
    "DE02b2b",
    "DE02b3",
    "DE03a",
    "DE03b",
    "DE03c",
    "DE03c1",
    "DE03c2",
    "DE03d",
    "DE06a",
    "DE06b",
    "DE03d1",
    "DE03d2",
    "DE04a",
    "DE04b",
}
ALL_IDS = (
    {f"DE{i:02}" for i in range(46)} | {f"P{i:02}" for i in range(1, 44)} | CHILDREN
)
INPUT = {
    "schema_version": 1,
    "backend": "memory",
    "scale": "timing",
    "read_rounds": 10,
    "write_samples": 3,
}
MODULES = [
    "packages/durable/src/testing/storage-benchmark.ts",
    "packages/durable/src/storage/memory.ts",
]


def require(ok: bool, label: str, c: Any) -> None:
    if not ok:
        raise c.EvidenceError(label)


def version(value: Any, expected: int, c: Any) -> None:
    require(
        type(value) is int and value == expected, "Unsupported acceptance schema", c
    )


def hashed(item: Any, root: Path, c: Any, load: Any, sha: Any) -> Any:
    c.shape(item, {"path", "sha256"}, "hashed acceptance artifact")
    path = root / c.safe_path(item["path"])
    c.digest(item["sha256"])
    require(
        path.resolve() == path.absolute()
        and not path.is_symlink()
        and sha(path) == item["sha256"],
        "Acceptance artifact drift",
        c,
    )
    return load(path)


def manifest(
    config: Any, root: Path, fixtures: Path, c: Any, load: Any, sha: Any
) -> None:
    workload = c.shape(
        config["workload"],
        {"id", "input", "adapter", "required_modules", "read_names", "write_names"},
        "workload registry",
    )
    require(
        workload["id"] == WORKLOAD
        and workload["adapter"] == "durable_pi_reference.mts"
        and workload["required_modules"] == MODULES,
        "Workload identity drift",
        c,
    )
    require(
        workload["read_names"] == READS and workload["write_names"] == WRITES,
        "Exported workload registry drift",
        c,
    )
    require(
        workload["input"]["path"] == WORKLOAD + ".input.json",
        "Workload input path drift",
        c,
    )
    value = hashed(workload["input"], fixtures, c, load, sha)
    require(
        type(value) is dict
        and set(value) == set(INPUT)
        and all(type(value[k]) is type(v) and value[k] == v for k, v in INPUT.items()),
        "Workload input drift",
        c,
    )
    require(
        config["oracles"]
        == [{"id": k, "case": v, "mutation_version": 1} for k, v in ORACLES.items()]
        and all(type(x["mutation_version"]) is int for x in config["oracles"]),
        "Oracle registry drift",
        c,
    )
    require(config["coverage"]["path"] == "coverage.json", "Coverage path drift", c)
    coverage(hashed(config["coverage"], fixtures, c, load, sha), root, c)
    c.shape(config["verification"], VERIFICATION, "verification closure")
    for name, digest in config["verification"].items():
        c.digest(digest)
        path = root / name
        require(
            path.resolve() == path.absolute() and sha(path) == digest,
            "Verification tool drift",
            c,
        )
    require(
        set(MODULES) <= config["reference"]["source_sha256"].keys(),
        "Missing workload source provenance",
        c,
    )


LEDGER = "docs/specs/2026-10-19-durable-execution-evidence.md"


def plan_rows(root: Path, c: Any) -> tuple[dict[str, list[str]], dict[str, int]]:
    rows: dict[str, list[str]] = {}
    locations: dict[str, int] = {}
    for number, line in enumerate((root / PLAN).read_text().splitlines(), 1):
        if not re.match(
            r"\| (?:DE04b1b(?:[12]|3[ab]?)|DE\d\d(?:a(?:1|2[ab]?)?|b(?:[123](?:[ab])?)?|c[12]?|d[12]?)?|P\d\d) ",
            line,
        ):
            continue
        cells = [v.strip() for v in line.strip().strip("|").split("|")]
        key = cells[0].split(" / ")[0]
        require(key in ALL_IDS and key not in rows, "Unknown/duplicate plan row", c)
        require(
            len(cells) == (4 if key.startswith("P") else 3) and all(cells),
            "Malformed plan row",
            c,
        )
        rows[key] = cells
        locations[key] = number
    require(
        set(rows) == ALL_IDS - {"DE01", "DE01b3"}, "Incomplete canonical plan rows", c
    )
    return rows, locations


def ledger_status(root: Path, c: Any) -> dict[str, str]:
    text = (root / LEDGER).read_text()
    require(
        "## Slice status" in text and "## Next delivery gate" in text,
        "Missing ledger status tables",
        c,
    )
    text = text.split("## Slice status", 1)[1].split("## Next delivery gate", 1)[0]
    statuses: dict[str, str] = {}
    for line in text.splitlines():
        if not re.match(
            r"\| (?:DE04b1b(?:[12]|3[ab]?)|DE\d\d(?:a(?:1|2[ab]?)?|b(?:[123](?:[ab])?)?|c[12]?|d[12]?)?|P\d\d) \|",
            line,
        ):
            continue
        cells = [v.strip() for v in line.strip().strip("|").split("|")]
        key = cells[0]
        require(
            len(cells) == (5 if key.startswith("P") else 4), "Malformed ledger row", c
        )
        status = cells[3 if key.startswith("P") else 2]
        require(
            key in ALL_IDS
            and key not in statuses
            and status in {"OPEN", "IN_PROGRESS", "LANDED"},
            "Invalid ledger status row",
            c,
        )
        statuses[key] = status
    require(
        set(statuses) == ALL_IDS - {"DE01", "DE01b3"}, "Incomplete ledger statuses", c
    )
    for parent, children in [
        ("DE01b3", ["DE01b3a", "DE01b3b"]),
        ("DE02a2", ["DE02a2a", "DE02a2b"]),
        ("DE02b2", ["DE02b2a", "DE02b2b"]),
        ("DE02b", ["DE02b1a", "DE02b1b", "DE02b2", "DE02b3"]),
        ("DE02a", ["DE02a1", "DE02a2"]),
        ("DE02", ["DE02a", "DE02b"]),
        ("DE03c", ["DE03c1", "DE03c2"]),
        ("DE06", ["DE06a", "DE06b"]),
        ("DE32", ["DE32a", "DE32b"]),
        ("DE03d", ["DE03d1", "DE03d2"]),
        ("DE03", ["DE03a", "DE03b", "DE03c", "DE03d"]),
        ("DE04b1b3", ["DE04b1b3a", "DE04b1b3b"]),
        ("DE04b1b", ["DE04b1b1", "DE04b1b2", "DE04b1b3"]),
        ("DE04b", ["DE04b1a", "DE04b1b"]),
        ("DE04", ["DE04a", "DE04b"]),
        ("DE05", ["DE05a", "DE05b"]),
        ("DE01", ["DE01a", "DE01b1", "DE01b2", "DE01b3"]),
    ]:
        states = {statuses[key] for key in children}
        expected = (
            "LANDED"
            if states == {"LANDED"}
            else ("OPEN" if states == {"OPEN"} else "IN_PROGRESS")
        )
        if parent in statuses:
            require(statuses[parent] == expected, "Cumulative parent status drift", c)
        statuses[parent] = expected
    return statuses


def de_source_anchor(key: str, locations: dict[str, int]) -> str:
    if key in locations:
        return f"{PLAN}:{locations[key]} ({key} contract)"
    first = "DE01a" if key == "DE01" else "DE01b3a"
    return (
        f"{PLAN}:{locations[first]}-{locations['DE01b3b']} ({key} cumulative contract)"
    )


BOUNDED_NATIVE = {
    "DE13c1",
    "DE13b",
    "DE13a",
    "DE12",
    "DE11",
    "P41",
    "P42",
    "DE10",
    "DE09",
    "P28",
    "DE08",
    "DE07",
    "DE06b",
    "DE05b",
    "DE05a",
    "DE04b1b3b",
    "DE04b1b3a",
    "DE04b1b1",
    "DE04b1b2",
    "DE04b1a",
    "DE32a",
    "DE02a1",
    "DE02a2a",
    "DE02a2b",
    "DE02b1b",
    "DE02b3",
    "DE03d2",
    "DE04a",
}
DE_EVIDENCE = {
    "DE13c1": "de13c1-implementation-boundary-2026-10-27",
    "DE13b": "de13b-retained-reporting-acceptance",
    "DE13a": "de13a-normal-details-abi-acceptance",
    "DE12": "de12-opt-in-headless-cli-acceptance",
    "DE11": "de11-opt-in-product-session-acceptance",
    "DE10": "de10-inspection-and-operator-resolution-acceptance",
    "DE09": "de09-completed-projection-acceptance",
    "DE08": "de08-tool-execution-acceptance",
    "DE07": "de07-generation-acceptance",
    "DE06": "de06b-canonical-checkpoint-acceptance",
    "DE05": "de05b-runtime-reservation-recovery-acceptance",
    "DE05a": "de05a-typed-creation-acceptance",
    "DE05b": "de05b-runtime-reservation-recovery-acceptance",
    "DE04": "de04b1b3b-cumulative-acceptance",
    "DE04b1b1": "de04b1b1-verified-delivery-and-owner-selected-j-admission",
    "DE04b1b2": "de04b1b2-verified-delivery-and-t-integration-successor",
    "DE04b1b3": "de04b1b3b-cumulative-acceptance",
    "DE04b1b3a": "de04b1b3a-verified-delivery-and-de04b1b3b-admission",
    "DE04b1b3b": "de04b1b3b-cumulative-acceptance",
    "DE04b1a": "de04b1a-verified-delivery-and-de04b1b-pre-code-admission",
    "DE04b1b": "de04b1b3b-cumulative-acceptance",
    "DE32a": "de32a-delivery-and-de04a-repaired-main-integration",
    "DE32b": "de32-pre-code-scanner-split",
    "DE00": "de00-acceptance-evidence",
    "DE01": "de01-split-acceptance",
    "DE01a": "de01a-local-reference-evidence",
    "DE01b1": "de01b1-delivered-de01b2-implementation-admission",
    "DE01b2": "de01b2-delivery-reconciliation-and-de01b3a-admission",
    "DE01b3": "de01b3a-local-acceptance-evidence",
    "DE01b3a": "de01b3a-local-acceptance-evidence",
    "DE01b3b": "de01b3b-local-ci-and-artifact-implementation",
    "DE02": "de02-split-before-product-implementation",
    "DE02a": "de02-split-before-product-implementation",
    "DE02a1": "de02a1-local-value-and-record-evidence",
    "DE02a2": "de02-split-before-product-implementation",
    "DE02a2a": "de02a1-delivery-and-de02a2-pre-code-split",
    "DE02a2b": "de02a2a-delivery-and-de02a2b-admission",
    "DE02b": "de02-split-before-product-implementation",
    "DE02b1a": "de02a2b-delivery-and-de02b-split",
    "DE02b1b": "de02b1a-delivery-and-de02b1b-admission",
    "DE02b2": "de02a2b-delivery-and-de02b-split",
    "DE02b2a": "de02b1b-delivery-and-de02b2a-admission",
    "DE02b2b": "de02b2a-delivery-and-de02b2b-admission",
    "DE02b3": "de02b2b-delivery-and-de02b3-admission",
    "DE03": "de02b3-delivery-and-de03-split",
    "DE03a": "de02b3-delivery-and-de03-split",
    "DE03b": "de02b3-delivery-and-de03-split",
    "DE03c": "de03b-delivery-and-de03c-split",
    "DE03c1": "de03b-delivery-and-de03c-split",
    "DE03c2": "de03b-delivery-and-de03c-split",
    "DE03d": "de02b3-delivery-and-de03-split",
    "DE06a": "de06-pre-runtime-codec-split",
    "DE06b": "de06b-canonical-checkpoint-acceptance",
    "DE03d1": "de03d1-delivery-and-de03d2-implementation-admission-2026-10-22",
    "DE03d2": "de03d2-paired-implementation-and-approved-lifetime-boundary",
    "DE04a": "de04a-verified-delivery-and-de04b1a-integration-successor",
    "DE04b": "de04b1b3b-cumulative-acceptance",
}


def de_current(row: Any, c: Any) -> None:
    key = row["id"]
    cases = (
        ["values-records-a1"]
        if key == "DE02a1"
        else (
            ["kernel-memory-v1"]
            if key == "DE01a"
            else (
                list(c.CASES)[:3]
                if key == "DE01b1"
                else (
                    list(c.CASES)
                    if key in {"DE01", "DE01b2", "DE01b3", "DE01b3a"}
                    else []
                )
            )
        )
    )
    cases = {
        "DE02a2a": ["memory-tables-a2a-v1"],
        "DE02a2b": ["memory-current-base-a2b-v1"],
        "DE02b1b": ["session-guard-b1b-v1"],
        "DE02b3": ["session-commit-b3-v1"],
        "DE03d2": ["storage-sqlite-d2-v1"],
        "DE04a": ["session-documents-a-v1"],
        "DE04b1b1": ["draft-tracker-v1"],
        "DE04b1b2": ["storage-current-deltas-v1"],
        "DE04b1b3a": ["session-commit-b3-v1", "session-documents-a-v1"],
        "DE04b1b3b": ["session-checkpoints-j-v1"],
        "DE05a": ["task-creation-a-v1"],
        "DE05b": ["task-runtime-b-v1"],
        "DE06b": ["canonical-checkpoint-b-v1"],
        "DE07": ["generation-b-v1"],
        "DE08": ["tool-execution-b-v1"],
        "DE09": ["completed-projection-b-v1"],
        "DE10": ["inspection-v1"],
        "DE11": ["product-session-v1"],
        "DE12": ["headless-cli-v1"],
        "DE13a": ["normal-details-v1"],
        "DE13b": ["retained-tool-reporting-v1"],
        "DE13c1": ["interrupted-tool-continuation-v1"],
    }.get(key, cases)
    expected = {
        "cases": cases,
        "workloads": [WORKLOAD] if key in {"DE01", "DE01b3", "DE01b3a"} else [],
        "oracles": [name for name, case in ORACLES.items() if case in cases],
    }
    current = row["current"]
    require(
        all(current[field] == ids for field, ids in expected.items()),
        "DE current evidence ownership drift",
        c,
    )
    require(
        current["kind"]
        == (
            "bounded-native"
            if key in BOUNDED_NATIVE
            else "reference-only"
            if cases
            else ("delivery" if key == "DE00" else "source-only")
        ),
        "DE current evidence kind drift",
        c,
    )
    links = [PLAN + "#5-delivery-order-and-reviewable-slices"]
    if key in DE_EVIDENCE:
        links.insert(0, LEDGER + "#" + DE_EVIDENCE[key])
    if key == "DE01b3b":
        links.append(
            "docs/durable-reference.md#exhaustive-current-and-future-evidence-index"
        )
    topics = {
        "DE03d2": "docs/durable-reference.md#closed-sqlite-oracle-and-lifetime-witness-de03d2",
        "DE04b1b1": "docs/durable-draft-tracker.md",
        "DE04b1b2": "docs/durable-current-deltas.md",
        "DE04b1b3a": "docs/durable-session-deltas.md",
        "DE04b1b3b": "docs/durable-session-checkpoints.md",
        "DE05a": "docs/durable-task-creation.md",
        "DE05b": "docs/durable-task-runtime.md",
        "DE06b": "docs/durable-canonical-codec.md",
        "DE07": "docs/durable-generation.md",
        "DE08": "docs/durable-tools.md",
        "DE09": "docs/durable-completed-projection.md",
        "DE10": "docs/durable-inspection.md",
        "DE11": "docs/durable-product-session.md",
        "DE12": "docs/durable-headless.md",
        "DE13a": "docs/tool-details.md",
        "DE13b": "docs/durable-tools.md",
        "DE13c1": "docs/durable-tools.md",
    }
    if key in topics:
        links.append(topics[key])
    require(row["evidence"] == links, "DE bounded evidence link drift", c)


def local_link(value: Any, root: Path, c: Any) -> None:
    require(type(value) is str, "Invalid coverage link", c)
    path, _, anchor = value.partition("#")
    name = root / c.safe_path(path)
    require(
        name.resolve() == name.absolute() and name.is_file(),
        "Dangling coverage link",
        c,
    )
    if anchor:
        headings = [
            re.sub(r"[^\w\- ]", "", line.lstrip("#").strip().lower()).replace(" ", "-")
            for line in name.read_text().splitlines()
            if line.startswith("#")
        ]
        require(anchor in headings, "Dangling coverage anchor", c)


def coverage(value: Any, root: Path, c: Any) -> None:
    c.shape(value, {"schema_version", "future_registry", "rows"}, "coverage index")
    version(value["schema_version"], 2, c)
    require(value["future_registry"] == {}, "Unknown future case registry", c)
    canonical, locations = plan_rows(root, c)
    statuses = ledger_status(root, c)
    seen = set()
    for row in c.array(value["rows"], "coverage rows"):
        c.shape(
            row,
            {
                "id",
                "owners",
                "contract",
                "source_anchor",
                "plan_cells",
                "current",
                "inherited_reference",
                "future",
                "status",
                "evidence",
            },
            "coverage row",
        )
        key = row["id"]
        require(
            type(key) is str and key in ALL_IDS and key not in seen,
            "Unknown/duplicate coverage ID",
            c,
        )
        seen.add(key)
        require(
            row["status"] == "OPEN"
            if key.startswith("P")
            else row["status"] in {"LANDED", "OPEN", "IN_PROGRESS"},
            "Unsupported coverage status",
            c,
        )
        require(row["status"] == statuses[key], "Coverage/ledger status drift", c)
        owners = c.array(row["owners"], "owners")
        require(
            bool(owners)
            and len(set(owners)) == len(owners)
            and all(
                type(x) is str and x.startswith("DE") and x in ALL_IDS for x in owners
            ),
            "Unknown coverage owner",
            c,
        )
        require(
            type(row["contract"]) is str
            and bool(row["contract"])
            and type(row["source_anchor"]) is str
            and bool(row["source_anchor"]),
            "Missing coverage contract/source",
            c,
        )
        require(
            owners == coverage_owners(canonical.get(key, []), key, c),
            "Coverage ownership drift",
            c,
        )
        if key in canonical:
            require(
                row["plan_cells"] == canonical[key]
                and row["contract"] == canonical[key][1],
                "Canonical coverage contract drift",
                c,
            )
            require(
                row["future"] == canonical[key][-1]
                if key.startswith("DE")
                else row["future"] == canonical[key][1],
                "Omitted future acceptance",
                c,
            )
        else:
            require(
                key in {"DE01", "DE01b3"}
                and row["plan_cells"] == []
                and bool(row["future"]),
                "Missing canonical coverage row",
                c,
            )
        require(
            row["source_anchor"]
            == (
                canonical[key][2]
                if key.startswith("P")
                else de_source_anchor(key, locations)
            ),
            "Coverage source anchor drift",
            c,
        )
        inherited = c.shape(
            row["inherited_reference"],
            {"owners", "cases", "workloads", "oracles", "observables"},
            "inherited evidence",
        )
        require(
            type(inherited["observables"]) is str and bool(inherited["observables"]),
            "Missing inherited evidence boundary",
            c,
        )
        expected_inherited = (
            {
                "owners": ["DE01b1", "DE01b2", "DE01b3a"],
                "cases": list(c.CASES),
                "workloads": [WORKLOAD],
                "oracles": list(ORACLES),
            }
            if key == "DE01b3b"
            else {"owners": [], "cases": [], "workloads": [], "oracles": []}
        )
        require(
            all(inherited[field] == ids for field, ids in expected_inherited.items()),
            "Inherited evidence ownership drift",
            c,
        )
        current = c.shape(
            row["current"],
            {"kind", "cases", "workloads", "oracles", "observables"},
            "current evidence",
        )
        require(
            current["kind"] in {"source-only", "reference-only", "delivery"}
            or (key in BOUNDED_NATIVE and current["kind"] == "bounded-native"),
            "Native parity claim in reference index",
            c,
        )
        for name, allowed in [
            (
                "cases",
                set(c.CASES)
                | ({"values-records-a1"} if key == "DE02a1" else set())
                | ({"memory-tables-a2a-v1"} if key == "DE02a2a" else set())
                | ({"memory-current-base-a2b-v1"} if key == "DE02a2b" else set())
                | ({"session-guard-b1b-v1"} if key == "DE02b1b" else set())
                | ({"session-commit-b3-v1"} if key == "DE02b3" else set())
                | ({"storage-sqlite-d2-v1"} if key == "DE03d2" else set())
                | ({"session-documents-a-v1"} if key == "DE04a" else set())
                | ({"storage-current-deltas-v1"} if key == "DE04b1b2" else set())
                | ({"draft-tracker-v1"} if key == "DE04b1b1" else set())
                | ({"session-checkpoints-j-v1"} if key == "DE04b1b3b" else set())
                | ({"task-creation-a-v1"} if key == "DE05a" else set())
                | ({"task-runtime-b-v1"} if key == "DE05b" else set())
                | ({"canonical-checkpoint-b-v1"} if key == "DE06b" else set())
                | ({"generation-b-v1"} if key == "DE07" else set())
                | ({"tool-execution-b-v1"} if key == "DE08" else set())
                | ({"completed-projection-b-v1"} if key in {"DE09", "P28"} else set())
                | ({"inspection-v1"} if key == "DE10" else set())
                | ({"product-session-v1"} if key in {"DE11", "P41", "P42"} else set())
                | ({"headless-cli-v1"} if key == "DE12" else set())
                | ({"normal-details-v1"} if key == "DE13a" else set())
                | ({"retained-tool-reporting-v1"} if key == "DE13b" else set())
                | ({"interrupted-tool-continuation-v1"} if key == "DE13c1" else set())
                | (
                    {"session-commit-b3-v1", "session-documents-a-v1"}
                    if key == "DE04b1b3a"
                    else set()
                ),
            ),
            ("workloads", {WORKLOAD}),
            ("oracles", set(ORACLES)),
        ]:
            ids = c.array(current[name], name)
            require(
                all(type(x) is str and x in allowed for x in ids)
                and len(set(ids)) == len(ids),
                "Unknown current evidence ID",
                c,
            )
        require(
            type(current["observables"]) is str and bool(current["observables"]),
            "Missing bounded current observables",
            c,
        )
        if key.startswith("DE"):
            de_current(row, c)
        if key == "DE01b3b":
            require(
                current["kind"] == "source-only"
                and all(
                    current[field] == [] for field in ("cases", "workloads", "oracles")
                ),
                "Unimplemented b3b claims own execution",
                c,
            )
        links = c.array(row["evidence"], "evidence links")
        require(bool(links), "Missing current evidence link", c)
        for link in links:
            local_link(link, root, c)
    require(seen == ALL_IDS, "Incomplete DE/P coverage index", c)


def coverage_owners(cells: list[str], key: str, c: Any) -> list[str]:
    if not key.startswith("P"):
        return [key]
    require(
        len(cells) == 4 and all(type(cell) is str and bool(cell) for cell in cells),
        "Malformed P ownership row",
        c,
    )
    owners: list[str] = []
    for match in re.finditer(r"DE(\d\d)(?:[–-](\d\d))?", cells[-1]):
        owners.extend(
            f"DE{i:02}" for i in range(int(match[1]), int(match[2] or match[1]) + 1)
        )
    if "every slice" in cells[-1]:
        owners.extend(f"DE{i:02}" for i in range(46))
    return sorted(set(owners))


def number(value: Any, c: Any, integer: bool = False) -> None:
    require(
        type(value) in ((int,) if integer else (int, float))
        and math.isfinite(value)
        and value >= 0,
        "Invalid workload number",
        c,
    )


def span(value: Any, c: Any) -> None:
    c.shape(value, {"start", "end"}, "span")
    for item in value.values():
        number(item, c)
    require(value["end"] >= value["start"], "Reversed measurement span", c)


def same(left: Any, right: Any, c: Any) -> bool:
    """Compare complete workload DTOs without Python's bool/int equality collision."""
    return json.dumps(c.encode(left), sort_keys=True, allow_nan=False) == json.dumps(
        c.encode(right), sort_keys=True, allow_nan=False
    )


def write_sample(row: Any, name: str, expected: int, c: Any) -> None:
    c.shape(
        row,
        {
            "id",
            "name",
            "expected",
            "actual",
            "seed",
            "run",
            "checked",
            "before",
            "after",
            "commit",
            "readbacks",
            "closed",
        },
        "write sample",
    )
    require(
        row["name"] == name
        and type(row["expected"]) is int
        and type(row["actual"]) is int
        and row["actual"] == row["expected"] == expected
        and row["closed"] is True,
        "Write result drift",
        c,
    )
    number(row["id"], c, True)
    span(row["seed"], c)
    span(row["run"], c)
    number(row["checked"], c)
    require(
        row["seed"]["end"]
        <= row["run"]["start"]
        <= row["run"]["end"]
        <= row["checked"],
        "Write span order drift",
        c,
    )
    require(
        type(row["before"]) is int
        and row["before"] == 100
        and type(row["after"]) is int
        and row["after"] == 100 + (100 if expected == 100 else 1),
        "Persisted entry count drift",
        c,
    )
    commit = c.shape(row["commit"], {"seq", "writes"}, "observed commit")
    number(commit["seq"], c, True)
    batch = c.array(commit["writes"], "committed writes")
    back = c.array(row["readbacks"], "readbacks")
    types = (
        ["entry", "task", "submission", "document.create"]
        if expected == 4
        else ["entry"] * expected
    )
    require(len(batch) == len(back) == len(types), "Incomplete persisted readbacks", c)
    for write, record, kind in zip(batch, back, types, strict=True):
        require(
            type(write) is dict
            and write.get("type") == kind
            and type(record) is dict
            and record.get("type") == kind,
            "Write/readback type drift",
            c,
        )
        if kind == "entry":
            c.shape(record, {"type", "value"}, "entry readback")
            stored = c.shape(record["value"], {"entry", "commitSeq"}, "stored entry")
            require(
                same(stored["entry"], write.get("value"), c)
                and stored["commitSeq"] == commit["seq"],
                "Persisted entry diverged",
                c,
            )
        elif kind == "task":
            c.shape(record, {"type", "value"}, "task readback")
            require(
                same(record["value"], write.get("value"), c),
                "Persisted task diverged",
                c,
            )
        elif kind == "submission":
            c.shape(record, {"type", "value", "byRequest"}, "submission readback")
            require(
                same(record["value"], write.get("value"), c)
                and same(record["byRequest"], write.get("value"), c),
                "Persisted submission diverged",
                c,
            )
        else:
            c.shape(record, {"type", "value", "byAddress"}, "document readback")
            stored = c.shape(
                record["value"],
                {"record", "version", "value", "deltasSinceBase"},
                "stored document",
            )
            require(
                stored["record"]
                == record["byAddress"]
                == {**write["record"], "createdAt": commit["seq"]}
                and stored["version"] == 1
                and stored["deltasSinceBase"] == 0
                and same(stored["value"], write["content"]["value"], c),
                "Persisted document diverged",
                c,
            )
    if expected == 4:
        entry, task, submission, doc = batch
        require(
            submission["value"]["entry"] == entry["value"]["id"]
            and submission["value"]["requestId"]
            == f"benchmark-{submission['value']['id']}"
            and doc["record"]["key"] == str(doc["record"]["id"])
            and doc["content"]["value"]
            == {"entryId": entry["value"]["id"], "taskId": task["value"]["id"]},
            "Mixed write correlation drift",
            c,
        )


def workload(value: Any, c: Any) -> Any:
    c.shape(value, {"schema_version", "id", "codec", "value"}, "workload envelope")
    version(value["schema_version"], 1, c)
    require(
        value["id"] == WORKLOAD and value["codec"] == "tagged-js-v1",
        "Unknown workload report",
        c,
    )
    data = c.decode(value["value"])
    c.shape(
        data,
        {
            "schema_version",
            "backend",
            "scale",
            "primary_records",
            "dataset",
            "hardware",
            "limits",
            "spans",
            "reads",
            "writes",
            "closed",
        },
        "workload report",
    )
    version(data["schema_version"], 1, c)
    require(
        data["backend"] == "memory"
        and data["scale"]
        == {
            "name": "timing",
            "entryCount": 1000,
            "taskCount": 300,
            "documentCount": 300,
        }
        and type(data["primary_records"]) is int
        and data["primary_records"] == 1870
        and data["closed"] is True,
        "Workload scale/backend drift",
        c,
    )
    hardware = c.shape(
        data["hardware"],
        {
            "platform",
            "release",
            "arch",
            "cpu_model",
            "cpu_count",
            "total_ram_bytes",
            "node",
        },
        "hardware",
    )
    for key in ("platform", "release", "arch", "cpu_model"):
        require(
            type(hardware[key]) is str and bool(hardware[key]),
            "Missing hardware identity",
            c,
        )
    require(hardware["node"] == "v26.12.0", "Workload runtime drift", c)
    for key in ("cpu_count", "total_ram_bytes"):
        number(hardware[key], c, True)
        require(hardware[key] > 0, "Missing hardware count", c)
    limits = c.shape(
        data["limits"],
        {"memory_retained", "protocol", "warmups", "write_span", "performance_parity"},
        "limits",
    )
    require(
        limits["memory_retained"] == "uncollected"
        and limits["protocol"] == "bounded-local-v1"
        and type(limits["warmups"]) is int
        and limits["warmups"] == 0
        and limits["performance_parity"] is False
        and type(limits["write_span"]) is str,
        "Invalid baseline claim",
        c,
    )
    spans = c.shape(
        data["spans"],
        {"started", "seed", "correctness_end", "finished"},
        "workload spans",
    )
    span(spans["seed"], c)
    for key in ("started", "correctness_end", "finished"):
        number(spans[key], c)
    require(
        spans["started"]
        <= spans["seed"]["start"]
        <= spans["seed"]["end"]
        <= spans["correctness_end"]
        <= spans["finished"],
        "Workload phase order drift",
        c,
    )
    r = c.shape(data["reads"], {"correctness", "samples"}, "read results")
    require(
        len(c.array(r["correctness"], "read correctness")) == 12
        and len(c.array(r["samples"], "read samples")) == 120,
        "Incomplete read workload",
        c,
    )
    dataset = c.shape(
        data["dataset"],
        {
            "firstEntryId",
            "filteredTaskCount",
            "exactDocumentId",
            "exactDocumentKey",
            "replayDocumentIds",
            "historicalDocumentId",
            "ancientAt",
            "recentAt",
            "deepestConversationId",
            "ancestorHeadEntryId",
        },
        "workload dataset",
    )
    for key in set(dataset) - {"exactDocumentKey", "replayDocumentIds"}:
        number(dataset[key], c, True)
    require(
        dataset["filteredTaskCount"] == 5
        and dataset["exactDocumentKey"] == "key-299"
        and dataset["ancientAt"] < dataset["recentAt"],
        "Seed dataset drift",
        c,
    )
    tails = c.shape(
        dataset["replayDocumentIds"], {"0", "16", "128", "1024"}, "replay documents"
    )
    for ident in tails.values():
        number(ident, c, True)
    source_expected = [
        dataset["firstEntryId"],
        100,
        dataset["filteredTaskCount"],
        dataset["exactDocumentId"],
        0,
        16,
        128,
        1024,
        128,
        256,
        100,
        dataset["ancestorHeadEntryId"],
    ]
    expected = {}
    for name, row, source_result in zip(
        READS, r["correctness"], source_expected, strict=True
    ):
        c.shape(row, {"name", "actual", "expected"}, "read correctness")
        require(
            row["name"] == name
            and type(row["actual"]) is int
            and type(row["expected"]) is int
            and row["actual"] == row["expected"] == source_result,
            "Read correctness failed",
            c,
        )
        expected[name] = row["actual"]
    for i, row in enumerate(r["samples"]):
        c.shape(row, {"name", "round", "start", "end", "actual"}, "read sample")
        span({k: row[k] for k in ("start", "end")}, c)
        require(
            type(row["round"]) is int
            and row["round"] == i // 12
            and row["name"] == READS[i % 12]
            and type(row["actual"]) is int
            and row["actual"] == expected[row["name"]]
            and row["start"] >= spans["correctness_end"]
            and row["end"] <= spans["finished"],
            "Measured read result/order drift",
            c,
        )
    w = c.shape(data["writes"], {"correctness", "samples"}, "write results")
    checks = c.array(w["correctness"], "write checks")
    samples = c.array(w["samples"], "write samples")
    require(len(checks) == 3 and len(samples) == 9, "Incomplete write workload", c)
    ids = []
    for i, row in enumerate(checks + samples):
        index = i if i < 3 else (i - 3) // 3
        write_sample(row, WRITES[index], (1, 100, 4)[index], c)
        ids.append(row["id"])
        require(
            row["checked"] <= spans["correctness_end"]
            if i < 3
            else row["seed"]["start"] >= spans["correctness_end"],
            "Timing before correctness/fresh seed",
            c,
        )
    require(ids == list(range(1, 13)), "Reused write storage sample", c)
    return data


def mutate(name: str, raw: Any, c: Any) -> Any:
    require(name in ORACLES, "Unknown oracle", c)
    c.validate_trace(raw)
    require(raw["case_id"] == ORACLES[name], "Oracle source case drift", c)
    value = copy.deepcopy(raw)
    observed = c.decode(value["steps"][0]["value"])
    if name == "lost-field":
        results = [e for e in observed["history"] if e["kind"] == "pi.tool-result"]
        require(len(results) == 1, "Missing/ambiguous tool result for mutation", c)
        models = c.array(results[0].get("model"), "tool result model")
        require(
            bool(models) and type(models[0]) is dict,
            "Missing tool result model for mutation",
            c,
        )
        result = models[0]
        require(
            "details" in result and result["details"] is None,
            "Lost-field mutation precondition",
            c,
        )
        del result["details"]
    elif name == "stale-head":
        require(
            observed["final"]["head"]["id"] != observed["first"]["head"]["id"],
            "Stale-head mutation precondition",
            c,
        )
        observed["final"] = observed["first"]
    elif name == "duplicate-effect":
        variants = c.array(observed["variants"], "mutation variants")
        require(bool(variants), "Missing recovery variant for mutation", c)
        variant = variants[0]
        require(
            variant["runs"] == 1 and len(variant["invocations"]) == 1,
            "Duplicate-effect mutation precondition",
            c,
        )
        c.array(variant["invocations"], "mutation invocations")
        extra = copy.deepcopy(variant["invocations"][0])
        extra.update(ordinal=2, phase="after-reopen")
        variant["invocations"].append(extra)
        variant["runs"] = 2
    elif name == "post-abort-write":
        commits = [x for x in observed["late"] if x["op"] == "commit"]
        require(len(commits) == 1, "Missing/ambiguous late commit for mutation", c)
        commit = commits[0]
        require(
            commit["status"] == "rejected" and observed["callbackEntered"] is False,
            "Post-abort mutation precondition",
            c,
        )
        commit.clear()
        commit.update(op="commit", status="fulfilled")
        observed["callbackEntered"] = True

        # All positive integer values are conservatively reserved, including
        # IDs outside history. This cannot collide with any observed ID domain.
        def integers(v: Any) -> list[int]:
            if type(v) is int:
                return [v]
            if type(v) is list:
                return [n for child in v for n in integers(child)]
            if type(v) is dict:
                return [n for child in v.values() for n in integers(child)]
            return []

        ident = max([0, *integers(observed)]) + 1
        require(ident < 2**53, "No safe fresh mutation ID", c)
        observed["history"].append(
            {
                "id": ident,
                "conversationId": 1,
                "byTaskId": observed["id"],
                "kind": "fixture.forbidden",
                "data": {"late": True},
            }
        )
    else:
        raise c.EvidenceError("Unknown oracle")
    value["steps"][0]["value"] = c.encode(observed)
    c.validate_trace(value)
    c.project(value)
    return value


def attest_reference(
    o: Any, folder: Path, config: Any, fixtures: Path | None = None
) -> dict[str, Any]:
    fixtures = o.FIXTURES if fixtures is None else fixtures
    run = o.load(folder / "run.json")
    require(
        run["status"] == "reference-verified"
        and run["complete"] is True
        and run["required_cases"] == list(o.contract.CASES)
        and [x["id"] for x in run["cases"]] == list(o.contract.CASES),
        "Incomplete fresh reference",
        o.contract,
    )
    require(
        run["manifest_sha256"] == o.sha(fixtures / "manifest.json")
        and run["comparison_sha256"] == config["comparison"]
        and run["adapters"] == config["adapters"]
        and run["reference"] == config["reference"]
        and run["runtime"] == config["runtime"],
        "Parent reference identity drift",
        o.contract,
    )
    for name, key in [
        ("reference.tar", "archive_sha256"),
        ("source-sha256.json", "source_inventory_sha256"),
        ("dependency-sha256.json", "dependency_inventory_sha256"),
    ]:
        require(
            o.sha(folder / name) == run[key],
            "Reference archive/inventory identity drift",
            o.contract,
        )
    snapshot = folder / "snapshot"
    originals = o.load(folder / "source-sha256.json")
    adapters = {x["destination"]: x["sha256"] for x in config["adapters"].values()}
    deps = o.load(folder / "dependency-sha256.json")
    o.verify_sources(snapshot, originals)
    o.verify_sources(snapshot, adapters)
    require(
        o.installed_dependencies(snapshot) == run["installed_dependencies"],
        "Installed dependency evidence drift",
        o.contract,
    )
    for case in run["cases"]:
        path = folder / case["id"]
        require(
            o.load(path / "result.json") == case and case["equal"] is True,
            "Case result drift",
            o.contract,
        )
        for key, name in [
            ("input_sha256", "input.json"),
            ("raw_sha256", "trace.json"),
            ("projected_sha256", "projected.json"),
            ("witness_sha256", "projection-witness.json"),
        ]:
            require(
                o.sha(path / name) == case[key], "Case artifact hash drift", o.contract
            )
        raw = o.trace(o.load(path / "trace.json"))
        projected, witness = o.contract.project(raw)
        require(
            projected == o.load(path / "projected.json")
            and witness == o.load(path / "projection-witness.json"),
            "Projection evidence drift",
            o.contract,
        )
        registered = next(x for x in config["cases"] if x["id"] == case["id"])
        require(
            case["input_sha256"] == registered["input"]["sha256"]
            and case["expected_sha256"] == registered["expected"]["sha256"]
            and o.compare(o.load(fixtures / registered["expected"]["path"]), raw),
            "Case fixture comparison drift",
            o.contract,
        )
        modules = o.verify_resolutions(
            snapshot,
            o.load(path / "resolutions.json"),
            originals,
            adapters,
            registered["required_modules"],
        )
        require(
            modules == case["module_sha256"], "Case module evidence drift", o.contract
        )
        o.module_dependencies(snapshot, modules, run["installed_dependencies"])
        for name, digest in modules.items():
            if "node_modules" in Path(name).parts:
                require(
                    deps.get(name) == digest, "Physical dependency drift", o.contract
                )
        for name, digest in case["auxiliary_sha256"].items():
            require(
                o.sha(path / name) == digest, "Auxiliary evidence drift", o.contract
            )
        if case["id"] == "tool-recovery-v1":
            require(
                o.database_evidence(path, o.load(path / "database-evidence.json"))
                == case["database_sha256"],
                "Database attestation drift",
                o.contract,
            )
        if case["id"] == "env-conformance-v1":
            o.chunk_evidence(o.load(path / "raw-chunks.json"), raw)
        command = o.load(folder / (case["id"] + ".json"))
        require(
            type(command["exit_code"]) is int
            and command["exit_code"] == 0
            and command["timed_out"] is False
            and command["cwd"] == str(snapshot)
            and command["command"]
            == [
                "node",
                "--import",
                str(snapshot / o.RESOLVER),
                str(snapshot / registered["adapter"]),
                case["id"],
                str(path / "input.json"),
                str(path),
            ],
            "Case execution command/path drift",
            o.contract,
        )
    return run


def execute(o: Any, source: Path, output: Path, fixtures: Path) -> int:
    started = time.time()
    identity = str(uuid.uuid4())
    manifest_start = o.sha(fixtures / "manifest.json")
    config = o.manifest(fixtures)
    output = output.resolve()
    require(
        not output.is_relative_to(source.resolve()),
        "Acceptance output inside upstream",
        o.contract,
    )
    output.mkdir(mode=0o700, exist_ok=False)
    output.chmod(0o700)
    invocation = output / "invocation.json"
    o.dump(
        invocation,
        {
            "schema_version": 1,
            "execution_id": identity,
            "reference_execution_id": identity + ":reference",
            "output_root": str(output),
            "manifest_sha256": manifest_start,
            "reference_commit": o.PIN,
            "started_unix": started,
        },
    )
    invocation_sha = o.sha(invocation)
    # Only this fresh invocation can create the exclusive reference child.
    code = o.reference(source, output / "reference", fixtures)
    if code:
        return code
    run = attest_reference(o, output / "reference", config, fixtures)
    run_sha = o.sha(output / "reference/run.json")
    snapshot = output / "reference/snapshot"
    env = o.child_env(output)
    work = output / WORKLOAD
    work.mkdir(mode=0o700)
    spec = config["workload"]
    o.shutil.copyfile(fixtures / spec["input"]["path"], work / "input.json")
    child_started = time.perf_counter()
    o.run(
        [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(snapshot / spec["adapter"]),
            WORKLOAD,
            str(work / "input.json"),
            str(work),
        ],
        snapshot,
        output,
        WORKLOAD,
        env,
        120,
    )
    child_seconds = time.perf_counter() - child_started
    data = workload(o.load(work / "workload.json"), o.contract)
    originals = o.load(output / "reference/source-sha256.json")
    adapters = {x["destination"]: x["sha256"] for x in config["adapters"].values()}
    modules = o.verify_resolutions(
        snapshot, o.load(work / "resolutions.json"), originals, adapters, MODULES
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    deps = o.load(output / "reference/dependency-sha256.json")
    for name, digest in modules.items():
        if "node_modules" in Path(name).parts:
            require(deps.get(name) == digest, "Workload dependency drift", o.contract)
    frozen = {
        work / name: o.sha(work / name)
        for name in ("input.json", "workload.json", "resolutions.json")
    }
    frozen[output / (WORKLOAD + ".json")] = o.sha(output / (WORKLOAD + ".json"))
    frozen[invocation] = invocation_sha
    mutants = []
    for name, case in ORACLES.items():
        folder = output / name
        folder.mkdir(mode=0o700)
        raw = o.load(output / "reference" / case / "trace.json")
        mutant = mutate(name, raw, o.contract)
        o.dump(folder / "original.json", raw)
        o.dump(folder / "mutant.json", mutant)
        expected = output / "reference" / case / "trace.json"
        # Compare uses the same public CLI and child lifecycle/deadline as callers.
        for label, actual, wanted in [
            ("positive", folder / "original.json", 0),
            ("negative", folder / "mutant.json", 1),
        ]:
            try:
                o.run(
                    [
                        sys.executable,
                        str(o.ROOT / "scripts/durable_pi_comparison.py"),
                        "compare",
                        str(expected),
                        str(actual),
                    ],
                    o.ROOT,
                    folder,
                    label,
                    {"PATH": env["PATH"], "HOME": env["HOME"]},
                    30,
                )
            except o.EvidenceError:
                if wanted == 0:
                    raise
            observed = o.load(folder / (label + ".json"))["exit_code"]
            require(
                type(observed) is int and observed == wanted,
                "Oracle compare failed (invalid2 is not mismatch1)",
                o.contract,
            )
        for artifact in (
            "original.json",
            "mutant.json",
            "positive.json",
            "negative.json",
            "positive.stdout",
            "negative.stdout",
            "positive.stderr",
            "negative.stderr",
        ):
            frozen[folder / artifact] = o.sha(folder / artifact)
        mutants.append(
            {
                "id": name,
                "case": case,
                "mutation_version": 1,
                "original_sha256": o.sha(folder / "original.json"),
                "mutant_sha256": o.sha(folder / "mutant.json"),
                "schema_valid": True,
                "positive_exit": 0,
                "negative_exit": 1,
            }
        )
    # Recheck ALL original source files, adapters, cases, sidecars and modules;
    # final success is bound to the original parent and manifest byte identities.
    attest_reference(o, output / "reference", config, fixtures)
    o.manifest(fixtures)
    require(
        o.sha(fixtures / "manifest.json") == manifest_start
        and o.sha(output / "reference/run.json") == run_sha,
        "Acceptance parent/manifest identity changed",
        o.contract,
    )
    require(
        o.sha(work / "input.json") == spec["input"]["sha256"],
        "Workload input changed",
        o.contract,
    )
    require(
        all(
            not path.is_symlink() and o.sha(path) == digest
            for path, digest in frozen.items()
        ),
        "Acceptance execution artifact identity changed",
        o.contract,
    )
    require(
        o.verify_resolutions(
            snapshot, o.load(work / "resolutions.json"), originals, adapters, MODULES
        )
        == modules,
        "Workload module identity changed",
        o.contract,
    )
    # Require the actual raw submatrix, not only declared summary counts.
    h2 = o.contract.decode(
        o.load(output / "reference/tool-recovery-v1/trace.json")["steps"][0]["value"]
    )
    e1 = o.contract.decode(
        o.load(output / "reference/env-conformance-v1/trace.json")["steps"][0]["value"]
    )
    require(
        len(h2["variants"]) == 4 and len(e1["scenarios"]) == 2,
        "Acceptance submatrix incomplete",
        o.contract,
    )
    summary = {
        "schema_version": 1,
        "status": "acceptance-verified",
        "complete": True,
        "execution_id": identity,
        "output_root": str(output),
        "started_unix": started,
        "finished_unix": time.time(),
        "reference_run_sha256": run_sha,
        "reference_execution_id": identity + ":reference",
        "execution_artifacts_sha256": {
            str(p.relative_to(output)): digest for p, digest in frozen.items()
        },
        "manifest_sha256": manifest_start,
        "reference": config["reference"],
        "runtime": {**config["runtime"], "python": platform.python_version()},
        "comparison": config["comparison"],
        "verification": config["verification"],
        "coverage": config["coverage"],
        "cases": list(o.contract.CASES),
        "submatrix": {"h2_variants": 4, "e1_scenarios": 2},
        "workload": {
            "id": WORKLOAD,
            "input_sha256": o.sha(work / "input.json"),
            "raw_sha256": o.sha(work / "workload.json"),
            "modules": modules,
            "child_process_seconds": child_seconds,
            "read_correctness": 12,
            "measured_reads": 120,
            "write_correctness": 3,
            "measured_writes": 9,
            "hardware": data["hardware"],
        },
        "oracles": mutants,
        "python_adapter": "OPEN",
        "parity_rows": "OPEN",
        "reference_ci": "OPEN until b3b",
    }
    o.dump(output / "acceptance.json", summary)
    return 0

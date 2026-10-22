"""DE03d2 closed Pi SQLite/Memory comparison: 0 equal, 1 mismatch, 2 invalid.

Current raw-base only. Raw reference witnesses are retained; closed projections
handle language spelling, and exact divergence pointers never excuse native loss.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "sqlite_memory_parent", ROOT / "scripts/durable_memory_comparison.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
spec = importlib.util.spec_from_file_location(
    "sqlite_session_parent", ROOT / "scripts/durable_session_commit_comparison.py"
)
assert spec and spec.loader
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
v, o = m.v, m.o
from pipy_harness.native.durable.sqlite import open_sqlite_storage  # noqa: E402

PHASE = "storage-sqlite-d2-v1"
INPUT = ROOT / "tests/fixtures/durable/storage-sqlite-v1.input.json"
MANIFEST = ROOT / "tests/fixtures/durable/storage-sqlite-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/storage-sqlite.mts"
DESTINATION = "storage-sqlite-d2.mts"
REFERENCE = ("memory", "sqlite", "sqlite-reopen")
NATIVE = ("native-sqlite", "native-reopen")
D2 = (
    "a-request",
    "b-zero",
    *("c-max-" + k for k in ("conversation", "entry", "task", "submission")),
    *(
        "d-limits-" + k
        for k in ("conversation", "entry", "task", "submission", "document")
    ),
    *(
        "e-" + action + "-" + version
        for version in ("zero", "negative", "fraction", "negative-zero", "huge")
        for action in ("create", "change")
    ),
    "f-dangling-False",
    "f-dangling-True",
)
SOURCES = (
    {
        "packages/durable/src/" + p
        for p in (
            "errors.ts",
            "ids.ts",
            "types.ts",
            "storage/memory.ts",
            "storage/scan.ts",
            "storage/sqlite/index.ts",
            "storage/sqlite/node.ts",
            "storage/sqlite/storage.ts",
            "storage/sqlite/schema.ts",
            "storage/sqlite/migrations.ts",
            "testing/storage-conformance.ts",
            "testing/storage-benchmark.ts",
            "testing/types.ts",
        )
    }
    | {
        "packages/chord/src/" + p
        for p in (
            "context/index.ts",
            "json.ts",
            "delta/index.ts",
            "delta/tracker.ts",
            "delta/diff.ts",
            "delta/apply-immutable-trusted.ts",
            "sqlite/index.ts",
            "sqlite/node.ts",
            "sqlite/schema.ts",
            "sqlite/migrations.ts",
            "sqlite/database.ts",
        )
    }
    | {
        "packages/telemetry/src/" + p + ".ts"
        for p in ("index", "memory", "metrics", "noop")
    }
)
MODULES = sorted(
    SOURCES
    - {
        "packages/durable/src/storage/sqlite/index.ts",
        "packages/durable/src/testing/storage-benchmark.ts",
        "packages/durable/src/testing/types.ts",
        "packages/chord/src/sqlite/database.ts",
    }
)

UPSTREAM_TESTS = {
    "packages/durable/test/" + p + ".test.ts"
    for p in ("sqlite-storage", "sqlite-facade", "sqlite-migrations")
}
PARENTS = {
    "baseline": "manifest.json",
    "a1": "storage-records-v1.manifest.json",
    "a2b": "storage-memory-v1.manifest.json",
    "b1b": "session-guard-v1.manifest.json",
    "b3": "session-commit-v1.manifest.json",
}
CLOSURE = (
    b.CLOSURE
    | {
        "src/pipy_harness/native/durable/" + p + ".py"
        for p in ("sqlite", "sqlite_connection", "sqlite_schema", "ownership")
    }
    | {
        "tests/durable/" + p + ".py"
        for p in (
            "sqlite_child_support",
            "storage_contract_cases",
            "test_sqlite_ownership",
            "test_sqlite_schema",
            "test_sqlite_storage",
            "test_sqlite_documents",
            "test_session_sqlite",
            "test_sqlite_workload",
            "test_sqlite_comparison",
        )
    }
    | {
        "scripts/durable_sqlite_comparison.py",
        "scripts/durable_sqlite_workload.py",
        "scripts/durable_reference/cases/storage-sqlite.mts",
        "tests/fixtures/durable/storage-sqlite-v1.input.json",
        "tests/fixtures/durable/storage-sqlite-v1.expected.json",
        "tests/fixtures/durable/storage-sqlite-workload-v1.input.json",
    }
)
CLOSURE |= {"tests/fixtures/durable/storage-sqlite-v1.expected.json"}
require, load, dump, shape = m.require, m.load, m.dump, m.shape


LIFETIME: dict[str, Any] = {
    "scenario": "document-atomic",
    "operation": "highwater-retained",
    "path": ["value"],
    "values": {
        backend: [
            "number",
            "4018000000000000"
            if backend in ("sqlite-reopen", "native-reopen")
            else "401c000000000000",
        ]
        for backend in (*REFERENCE, *NATIVE)
    },
}


def same(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, allow_nan=False
    )


def recipe() -> Any:
    value = load(INPUT)
    shape(
        value,
        {
            "schema_version",
            "phase",
            "scenarios",
            "projections",
            "divergences",
            "reopen_lifetime",
        },
        "SQLite recipe",
    )
    require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "SQLite recipe identity",
    )
    require(
        [s.get("id") for s in value["scenarios"]] == list(D2),
        "Closed D2 scenario inventory",
    )
    require(
        same(value["reopen_lifetime"], LIFETIME),
        "Missing/stale/overbroad reopen lifetime witness",
    )
    inherited = m.recipe(load(m.INPUT))
    all_scenarios = inherited["scenarios"] + value["scenarios"]
    for scenario in all_scenarios:
        shape(scenario, {"id", "operations"}, "SQLite scenario")
        ids = []
        for step in scenario["operations"]:
            shape(step, {"id", "op", "args"}, "SQLite step")
            require(
                type(step["id"]) is str and step["id"] and step["op"] in m.OPERATIONS,
                "SQLite operation identity",
            )
            ids.append(step["id"])
            arguments = v.decode(step["args"])
            m._recipe_args(step["op"], ingress(step["op"], arguments))
        require(len(ids) == len(set(ids)), "Duplicate SQLite step")
    for name, fields in (
        ("projections", {"backend", "scenario", "operation", "raw", "canonical"}),
        (
            "divergences",
            {"backend", "scenario", "operation", "path", "memory", "sqlite"},
        ),
    ):
        keys = []
        for declaration in value[name]:
            shape(declaration, fields, name)
            key: tuple[Any, ...] = (
                declaration["backend"],
                declaration["scenario"],
                declaration["operation"],
            )
            require(
                key[1] in {s["id"] for s in all_scenarios},
                "Unknown declaration scenario",
            )
            steps = next(s["operations"] for s in all_scenarios if s["id"] == key[1])
            require(key[2] in {s["id"] for s in steps}, "Unknown declaration operation")
            require(
                key[0]
                in (REFERENCE + NATIVE if name == "projections" else REFERENCE[1:]),
                "Unknown declaration backend",
            )
            if name == "divergences":
                path = declaration["path"]
                require(
                    type(path) is list and all(type(p) in (str, int) for p in path),
                    "Invalid divergence pointer",
                )
                require(
                    not same(declaration["memory"], declaration["sqlite"]),
                    "Stale equal divergence",
                )
                key += (json.dumps(path),)
            else:
                error(declaration["raw"])
                error(declaration["canonical"])
                require(
                    not same(declaration["raw"], declaration["canonical"]),
                    "Unused equal projection",
                )
            keys.append(key)
        require(len(keys) == len(set(keys)), "Duplicate declaration")
    return {
        **value,
        "scenarios": all_scenarios,
        "exported_cases": inherited["exported_cases"],
    }


def ingress(op: str, args: Any) -> Any:
    """Only the page-size role converts JS integral Number into Python int."""
    result = copy.deepcopy(args)
    if op.startswith("scan_"):
        limit = result["limit"]
        require(
            type(limit) in (int, float)
            and math.isfinite(limit)
            and limit == int(limit)
            and abs(limit) <= 2**53 - 1,
            "Nonintegral/unsafe page size",
        )
        result["limit"] = int(limit)
    return result


def error(value: Any) -> None:
    require(
        type(value) is dict
        and set(value)
        in ({"name", "message", "code"}, {"name", "message", "code", "cause"}),
        "Invalid raw error",
    )
    require(
        type(value["name"]) is str
        and type(value["message"]) is str
        and (value["code"] is None or type(value["code"]) is str),
        "Invalid raw error scalar",
    )
    if "cause" in value:
        m._raw_tag(value["cause"])


def missing_cursor(step: Any, cache: dict[str, Any]) -> str | None:
    """Identify an uncallable continuation from actual prior results, without guessing."""
    args = v.decode(step["args"])
    ref = args.get("cursor_ref")
    if ref is not None and (
        ref not in cache or type(cache[ref]) is not dict or "next" not in cache[ref]
    ):
        return ref
    return None


def trace(value: Any, recipes: Any | None = None) -> Any:
    recipes = recipe() if recipes is None else recipes
    shape(value, {"schema_version", "phase", "backends"}, "SQLite trace")
    require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "SQLite trace identity",
    )
    backends = value["backends"]
    require(
        type(backends) is list
        and tuple(x.get("backend") for x in backends) in (REFERENCE, NATIVE),
        "Closed backend inventory",
    )
    for backend in backends:
        shape(backend, {"backend", "scenarios"}, "SQLite backend")
        scenarios = backend["scenarios"]
        require(
            type(scenarios) is list and len(scenarios) == len(recipes["scenarios"]),
            "Incomplete SQLite scenarios",
        )
        for row, script in zip(scenarios, recipes["scenarios"], strict=True):
            shape(row, {"id", "operations"}, "SQLite trace scenario")
            require(
                row["id"] == script["id"]
                and type(row["operations"]) is list
                and len(row["operations"]) == len(script["operations"]),
                "Incomplete SQLite steps",
            )
            cache: dict[str, Any] = {}
            for step, expected in zip(
                row["operations"], script["operations"], strict=True
            ):
                shape(step, {"id", "op", "args", "outcome"}, "SQLite trace step")
                require(
                    step["id"] == expected["id"] and step["op"] == expected["op"],
                    "SQLite step identity",
                )
                # An absent actual continuation blocks the call; never invent a cursor.
                absent = missing_cursor(expected, cache)
                if absent is not None:
                    require(backend["backend"] in NATIVE, "Missing Pi cursor evidence")
                    require(
                        same(step["args"], expected["args"]),
                        "Blocked call argument drift",
                    )
                    require(
                        same(
                            step["outcome"], {"status": "blocked", "cursor_ref": absent}
                        ),
                        "Invalid blocked continuation",
                    )
                    continue
                # Cursors are actual preceding results, not fixture guesses.
                args = m.arguments(expected, cache, [])
                if "cursor_ref" in v.decode(expected["args"]):
                    # A changed response cursor is a valid behavioral mutant;
                    # actual subsequent cursor arguments remain typed evidence.
                    observed_args = v.decode(step["args"])
                    m._recipe_args(step["op"], ingress(step["op"], observed_args))
                    require(
                        same(
                            {
                                k: val
                                for k, val in observed_args.items()
                                if k != "cursor"
                            },
                            {k: val for k, val in args.items() if k != "cursor"},
                        ),
                        "SQLite cursor call drift",
                    )
                else:
                    require(
                        same(step["args"], v.encode(args)), "SQLite call argument drift"
                    )
                outcome = step["outcome"]
                require(type(outcome) is dict, "Invalid SQLite outcome")
                if outcome.get("status") == "fulfilled":
                    shape(outcome, {"status", "value"}, "SQLite fulfilled")
                    m._raw_tag(outcome["value"])
                    cache[step["id"]] = (
                        None
                        if outcome["value"] == ["undefined"]
                        else v.decode(outcome["value"])
                    )
                else:
                    shape(outcome, {"status", "error"}, "SQLite rejected")
                    require(outcome["status"] == "rejected", "Invalid SQLite status")
                    error(outcome["error"])
    return value


class ReopeningStorage:
    """Private oracle leg: every commit, including helper calls, reopens."""

    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self.current = open_sqlite_storage(self.path, mode="create", durability="full")
        self.closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.current, name)

    def commit(self, writes: Any) -> Any:
        if self.closed:
            return self.current.commit(writes)
        try:
            return self.current.commit(writes)
        finally:
            self.current.close()
            self.current = open_sqlite_storage(
                self.path, mode="open", durability="full"
            )

    def close(self) -> None:
        self.closed = True
        self.current.close()


def native(folder: Path, recipes: Any | None = None) -> Any:
    recipes = recipe() if recipes is None else recipes
    folder.mkdir(mode=0o700, exist_ok=False)
    backends = []
    for backend in NATIVE:
        scenarios = []
        for index, scenario in enumerate(recipes["scenarios"]):
            path = folder / (backend + str(index) + ".sqlite")
            store: Any = (
                ReopeningStorage(path)
                if backend == "native-reopen"
                else open_sqlite_storage(str(path), mode="create", durability="full")
            )
            rows: list[Any] = []
            cache: dict[str, Any] = {}
            try:
                for step in scenario["operations"]:
                    absent = missing_cursor(step, cache)
                    if absent is not None:
                        rows.append(
                            {
                                "id": step["id"],
                                "op": step["op"],
                                "args": copy.deepcopy(step["args"]),
                                "outcome": {"status": "blocked", "cursor_ref": absent},
                            }
                        )
                        continue
                    args = m.arguments(step, cache, [])
                    record = {
                        "id": step["id"],
                        "op": step["op"],
                        "args": v.encode(args),
                    }
                    try:
                        result = m._dto(
                            m.invoke(store, step["op"], ingress(step["op"], args))
                        )
                        cache[step["id"]] = result
                        record["outcome"] = {
                            "status": "fulfilled",
                            "value": ["undefined"]
                            if result is None
                            else v.encode(result),
                        }
                    except (
                        RuntimeError,
                        m.StorageRequestError,
                        IndexError,
                        KeyError,
                    ) as exc:
                        record["outcome"] = {
                            "status": "rejected",
                            "error": {
                                "name": type(exc).__name__,
                                "message": str(exc),
                                "code": None,
                            },
                        }
                    rows.append(record)
            finally:
                store.close()
            scenarios.append({"id": scenario["id"], "operations": rows})
        backends.append({"backend": backend, "scenarios": scenarios})
    return trace({"schema_version": 1, "phase": PHASE, "backends": backends}, recipes)


def projected(value: Any, recipes: Any) -> Any:
    result = copy.deepcopy(trace(value, recipes))
    declarations = {
        (d["backend"], d["scenario"], d["operation"]): d for d in recipes["projections"]
    }
    for backend in result["backends"]:
        for row in backend["scenarios"]:
            for step in row["operations"]:
                d = declarations.get((backend["backend"], row["id"], step["id"]))
                if (
                    d
                    and step["outcome"].get("status") == "rejected"
                    and same(step["outcome"]["error"], d["raw"])
                ):
                    step["outcome"]["error"] = copy.deepcopy(d["canonical"])
    return result


def differences(memory: Any, sqlite: Any, path: list[Any] | None = None) -> list[Any]:
    path = [] if path is None else path
    if same(memory, sqlite):
        return []
    if type(memory) is dict and type(sqlite) is dict and set(memory) == set(sqlite):
        return [
            d
            for key in sorted(memory)
            for d in differences(memory[key], sqlite[key], [*path, key])
        ]
    if type(memory) is list and type(sqlite) is list and len(memory) == len(sqlite):
        return [
            d
            for i, (left, right) in enumerate(zip(memory, sqlite, strict=True))
            for d in differences(left, right, [*path, i])
        ]
    return [{"path": path, "memory": memory, "sqlite": sqlite}]


def observed_divergences(value: Any, recipes: Any) -> list[Any]:
    projected_trace = projected(value, recipes)
    require(
        tuple(b["backend"] for b in value["backends"]) == REFERENCE,
        "Expected actual Pi backends",
    )
    memory = projected_trace["backends"][0]["scenarios"]
    result: list[Any] = []
    for backend in projected_trace["backends"][1:]:
        for row, expected in zip(backend["scenarios"], memory, strict=True):
            for step, target in zip(
                row["operations"], expected["operations"], strict=True
            ):
                if (
                    row["id"] == LIFETIME["scenario"]
                    and step["id"] == LIFETIME["operation"]
                ):
                    continue  # validated separately as an approved lifetime boundary
                result.extend(
                    {
                        "backend": backend["backend"],
                        "scenario": row["id"],
                        "operation": step["id"],
                        **d,
                    }
                    for d in differences(target["outcome"], step["outcome"])
                )
    return result


def comparison(expected: Any, actual: Any, recipes: Any | None = None) -> int:
    recipes = recipe() if recipes is None else recipes
    trace(expected, recipes)
    trace(actual, recipes)
    require(
        tuple(b["backend"] for b in actual["backends"]) == NATIVE,
        "Expected native backends",
    )
    for declaration in recipes["projections"]:
        canonical = selected(
            expected["backends"][0], declaration["scenario"], declaration["operation"]
        )["outcome"]
        require(
            canonical["status"] == "rejected"
            and same(canonical["error"], declaration["canonical"]),
            "Stale/overbroad error projection",
        )
        if declaration["backend"] in REFERENCE:
            backend = next(
                b
                for b in expected["backends"]
                if b["backend"] == declaration["backend"]
            )
            raw_error = selected(
                backend, declaration["scenario"], declaration["operation"]
            )["outcome"]
            require(
                raw_error["status"] == "rejected"
                and same(raw_error["error"], declaration["raw"]),
                "Unused error projection",
            )
    # Pi declarations must be used exactly by raw observations. Native spelling
    # entries are conditional: an unexpected native error remains a mismatch.
    # Semantic divergence declarations must be exhaustive and used by Pi.
    require(
        same(observed_divergences(expected, recipes), recipes["divergences"]),
        "Missing/stale/overbroad divergence inventory",
    )
    require(
        same(recipes["reopen_lifetime"], LIFETIME),
        "Invalid reopen lifetime declaration",
    )
    for backend in expected["backends"]:
        step = selected(backend, LIFETIME["scenario"], LIFETIME["operation"])
        require(
            step["outcome"]["status"] == "fulfilled"
            and same(step["outcome"]["value"], LIFETIME["values"][backend["backend"]]),
            "Stale reopen lifetime observation",
        )
    memory = projected(expected, recipes)["backends"][0]["scenarios"]
    for backend in projected(actual, recipes)["backends"]:
        target = {"scenarios": copy.deepcopy(memory)}
        selected(target, LIFETIME["scenario"], LIFETIME["operation"])["outcome"][
            "value"
        ] = LIFETIME["values"][backend["backend"]]
        if not same(backend["scenarios"], target["scenarios"]):
            return 1
    return 0


def selected(backend: Any, scenario: str, operation: str) -> Any:
    row = next(r for r in backend["scenarios"] if r["id"] == scenario)
    return next(step for step in row["operations"] if step["id"] == operation)


def parents() -> Any:
    return {key: o.sha(o.FIXTURES / filename) for key, filename in PARENTS.items()}


# Immutable delivered D1 snapshot identities; current sources are bound by CLOSURE.
D1_SOURCES = {
    "scripts/durable_sqlite_workload.py": "2407f5f41ef73886417951a82b5d26930b5edf9fefe5103e594dc26bd75c009b",
    "tests/durable/test_session_sqlite.py": "b4baaf2577580d1c5edc3bd24ef67512956ada44d18cb51e2e6ffaf7b8eab5b6",
    "tests/durable/test_sqlite_workload.py": "a085fc69ec73952ab63adc9bbd34170d90eb08b7571e7e1a61dfc21b84adb1f7",
    "tests/fixtures/durable/storage-sqlite-workload-v1.input.json": "79d43012e682e94f15ad6ff6adef292a5bf63835d6522db7391c19842ace6111",
}


def configuration() -> Any:
    config = load(MANIFEST)
    shape(
        config,
        {
            "schema_version",
            "phase",
            "reference_commit",
            "parents",
            "closure",
            "reference_sources",
            "upstream_tests",
            "d1_evidence",
        },
        "SQLite manifest",
    )
    require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "SQLite manifest identity",
    )
    for key, inventory in (
        ("closure", CLOSURE),
        ("reference_sources", SOURCES),
        ("upstream_tests", UPSTREAM_TESTS),
    ):
        shape(config[key], inventory, key)
        for name, digest in config[key].items():
            o.contract.digest(digest)
            if key == "closure":
                require(o.sha(ROOT / name) == digest, "SQLite closure drift")
    require(same(config["parents"], parents()), "SQLite parent drift")
    d1 = config["d1_evidence"]
    shape(
        d1,
        {
            "delivery",
            "reviewed_tree",
            "gate_proof_sha256",
            "bindings_sha256",
            "sources",
            "workloads",
        },
        "D1 evidence",
    )
    require(
        d1["delivery"] == "8ab38d2010ea8e7f8660124085b04b6f0e821d94"
        and d1["reviewed_tree"] == "5113e7c423cb0b35a89b99e12bef783dba954ec3"
        and d1["gate_proof_sha256"]
        == "75790fcc350d8e36bfc0876d15a35d096bf3eb5d06d90840276337e55ed0a581",
        "D1 delivery evidence drift",
    )
    o.contract.digest(d1["bindings_sha256"])
    files = {
        "scripts/durable_sqlite_workload.py",
        "tests/durable/test_session_sqlite.py",
        "tests/durable/test_sqlite_workload.py",
        "tests/fixtures/durable/storage-sqlite-workload-v1.input.json",
    }
    shape(d1["sources"], files, "D1 source evidence")
    for name, digest in d1["sources"].items():
        o.contract.digest(digest)
        require(D1_SOURCES.get(name) == digest, "Historical D1 source evidence drift")
    shape(d1["workloads"], {"macos", "linux"}, "D1 workload evidence")
    for workload in d1["workloads"].values():
        shape(
            workload,
            {
                "manifest.json",
                "clean/result.json",
                "clean/process.json",
                "unclean/result.json",
                "unclean/process.json",
                "unclean/recovery-process.json",
            },
            "D1 workload artifacts",
        )
        for digest in workload.values():
            o.contract.digest(digest)
    b.configuration()
    recipe()
    return config


def process(
    argv: list[str], cwd: Path, output: Path, label: str, env: Any, timeout: int = 120
) -> Any:
    """No-descendant driver target; signal only while leader remains unreaped."""
    started = time.monotonic()
    child = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stdout, stderr, killed = b"", b"", False
    try:
        stdout, stderr = child.communicate(timeout=timeout)
    finally:
        if child.returncode is None:
            killed = True
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            stdout, stderr = child.communicate(timeout=10)
        (output / (label + ".stdout")).write_bytes(stdout)
        (output / (label + ".stderr")).write_bytes(stderr)
        record = {
            "argv": argv,
            "cwd": str(cwd),
            "returncode": child.returncode,
            "leader_reaped": child.returncode is not None,
            "signal": "SIGKILL" if killed else None,
            "stdout_sha256": o.sha(output / (label + ".stdout")),
            "stderr_sha256": o.sha(output / (label + ".stderr")),
            "seconds": time.monotonic() - started,
            "timeout": timeout,
        }
        dump(output / (label + ".json"), record)
    process_record(record, output, label)
    return stdout, stderr, child.returncode


def process_record(record: Any, output: Path, label: str) -> None:
    shape(
        record,
        {
            "argv",
            "cwd",
            "returncode",
            "leader_reaped",
            "signal",
            "stdout_sha256",
            "stderr_sha256",
            "seconds",
            "timeout",
        },
        "SQLite process record",
    )
    require(
        type(record["argv"]) is list
        and bool(record["argv"])
        and all(type(a) is str for a in record["argv"]),
        "Invalid process argv",
    )
    require(
        type(record["cwd"]) is str
        and Path(record["cwd"]).is_absolute()
        and type(record["returncode"]) is int
        and record["leader_reaped"] is True
        and record["signal"] is None,
        "Unreaped/wrong process metadata",
    )
    require(
        type(record["timeout"]) is int
        and record["timeout"] > 0
        and type(record["seconds"]) in (int, float)
        and math.isfinite(record["seconds"])
        and record["seconds"] >= 0,
        "Invalid process timing",
    )
    for stream in ("stdout", "stderr"):
        o.contract.digest(record[stream + "_sha256"])
        require(
            record[stream + "_sha256"] == o.sha(output / (label + "." + stream)),
            "Process output identity drift",
        )


def conformance(context: Any, raw: Any, reference: Any, recipes: Any) -> None:
    shape(
        context, {"schema_version", "phase", "backends"}, "SQLite conformance context"
    )
    require(
        type(context["schema_version"]) is int
        and context["schema_version"] == 1
        and context["phase"] == PHASE,
        "SQLite conformance identity",
    )
    shape(raw, {"schema_version", "calls", "errors"}, "SQLite conformance raw")
    require(
        type(raw["schema_version"]) is int and raw["schema_version"] == 1,
        "Conformance raw identity",
    )
    require(
        tuple(b["backend"] for b in context["backends"]) == REFERENCE,
        "Conformance backend inventory",
    )
    for backend in context["backends"]:
        shape(backend, {"backend", "exported_cases"}, "SQLite exported backend")
        require(
            [c["name"] for c in backend["exported_cases"]] == recipes["exported_cases"],
            "Conformance case inventory",
        )
        for case in backend["exported_cases"]:
            shape(
                case,
                {"name", "callback_calls", "closed", "operations"},
                "SQLite conformance case",
            )
            calls = [
                c
                for c in raw["calls"]
                if c["backend"] == backend["backend"] and c["case"] == case["name"]
            ]
            require(
                type(case["callback_calls"]) is int
                and case["callback_calls"] == 1
                and case["closed"] is True
                and type(case["operations"]) is int
                and case["operations"] == len(calls) > 0,
                "Incomplete conformance execution",
            )
            for call in calls:
                shape(
                    call,
                    {"backend", "case", "op", "args", "outcome"},
                    "SQLite conformance call",
                )
                require(type(call["op"]) is str, "Invalid conformance operation")
                m._raw_tag(call["args"])
                outcome = call["outcome"]
                if outcome.get("status") == "fulfilled":
                    shape(outcome, {"status", "value"}, "Conformance result")
                    m._raw_tag(outcome["value"])
                else:
                    shape(outcome, {"status", "error"}, "Conformance rejection")
                    require(
                        outcome["status"] == "rejected", "Invalid conformance status"
                    )
                    error(outcome["error"])
    for item in raw["errors"]:
        require(
            type(item) is dict
            and set(item)
            in (
                {
                    "backend",
                    "scenario",
                    "operation",
                    "name",
                    "message",
                    "code",
                    "stack",
                },
                {
                    "backend",
                    "scenario",
                    "operation",
                    "name",
                    "message",
                    "code",
                    "stack",
                    "cause",
                },
            ),
            "Invalid raw error witness",
        )
        require(
            item["backend"] in REFERENCE
            and type(item["scenario"]) is str
            and type(item["operation"]) is str
            and (item["stack"] is None or type(item["stack"]) is str),
            "Invalid raw diagnostic identity",
        )
        error(
            {
                key: value
                for key, value in item.items()
                if key in ("name", "message", "code", "cause")
            }
        )
    known_calls = sum(
        c["operations"] for b in context["backends"] for c in b["exported_cases"]
    )
    require(len(raw["calls"]) == known_calls, "Unknown conformance calls")
    rejected = sum(c["outcome"]["status"] == "rejected" for c in raw["calls"]) + sum(
        s["outcome"]["status"] == "rejected"
        for b in reference["backends"]
        for row in b["scenarios"]
        for s in row["operations"]
    )
    require(len(raw["errors"]) == rejected, "Missing raw error witnesses")


def comparison_report(output: Path, raw: Any, actual: Any, recipes: Any) -> int:
    result = comparison(raw, actual, recipes)
    dump(output / "observed-projected.json", projected(raw, recipes))
    dump(output / "native-projected.json", projected(actual, recipes))
    if result:
        dump(
            output / "mismatch-report.json",
            {
                "expected_sha256": o.sha(output / "observed.json"),
                "actual_sha256": o.sha(output / "native.json"),
                "expected_projected_sha256": o.sha(output / "observed-projected.json"),
                "actual_projected_sha256": o.sha(output / "native-projected.json"),
            },
        )
    return result


def execute(parent: Path, output: Path) -> int:
    output = output.absolute()
    require(
        not output.exists() and not output.is_symlink(), "SQLite output entry exists"
    )
    config = configuration()
    identity = o.sha(MANIFEST)
    parent = parent.resolve(strict=True)
    output = output.absolute()
    require(not output.resolve().is_relative_to(parent), "SQLite output inside parent")
    base = o.manifest()
    v.artifacts.attest(parent, o.FIXTURES)
    run = o.acceptance.attest_reference(o, parent / "reference", base, o.FIXTURES)
    acceptance = load(parent / "acceptance.json")
    require(
        acceptance["status"] == "acceptance-verified"
        and acceptance["complete"] is True,
        "Incomplete SQLite parent",
    )
    parent_sha = o.sha(parent / "acceptance.json")
    snapshot = parent / "reference/snapshot"
    o.verify_sources(snapshot, config["reference_sources"])
    o.verify_sources(snapshot, config["upstream_tests"])
    output.mkdir(mode=0o700, exist_ok=False)
    driver = snapshot / DESTINATION
    require(
        not driver.exists() and not driver.is_symlink(),
        "SQLite caller destination exists",
    )
    shutil.copyfile(DRIVER, driver)
    require(o.sha(driver) == o.sha(DRIVER), "SQLite caller copy drift")
    recipes = recipe()
    dump(output / "input.json", recipes)
    env = o.child_env(output)
    for runtime in ("node", "npm"):
        stdout, stderr, code = process(
            [runtime, "--version"], snapshot, output, runtime + "-version", env, 15
        )
        require(
            code == 0
            and not stderr
            and stdout.decode().strip() == base["runtime"][runtime],
            "SQLite runtime drift",
        )
    stdout, stderr, code = process(
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
        "pi-sqlite",
        env,
    )
    require(code == 0 and not stdout and not stderr, "SQLite driver failed")
    modules = o.verify_resolutions(
        snapshot,
        load(output / "resolutions.json"),
        load(parent / "reference/source-sha256.json"),
        {DESTINATION: o.sha(DRIVER)},
        MODULES,
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    dependencies = load(parent / "reference/dependency-sha256.json")
    for name, digest in modules.items():
        require(
            name in config["reference_sources"]
            or name == DESTINATION
            or (
                "node_modules" in Path(name).parts and dependencies.get(name) == digest
            ),
            "Unbound SQLite import",
        )
    raw = trace(load(output / "observed.json"), recipes)
    conformance(
        load(output / "reference-context.json"),
        load(output / "conformance-raw.json"),
        raw,
        recipes,
    )
    native_dir = output / "native"
    native_dir.mkdir(mode=0o700)
    # Bound separate interpreter process: unexpected native faults never hang the
    # coordinator or become a valid mismatch by escaping with Python exit 1.
    stdout, stderr, code = process(
        [
            sys.executable,
            "-I",
            str(Path(__file__).resolve()),
            "_native",
            str(output / "native.json"),
            str(native_dir / "stores"),
        ],
        ROOT,
        output,
        "native-sqlite",
        {"PATH": env["PATH"], "HOME": env["HOME"]},
    )
    require(code == 0 and not stdout and not stderr, "Native SQLite driver failed")
    actual = trace(load(output / "native.json"), recipes)
    result = comparison_report(output, raw, actual, recipes)
    controls = [] if result else mutants(output, raw, actual, recipes, env)
    o.acceptance.attest_reference(o, parent / "reference", base, o.FIXTURES)
    v.artifacts.attest(parent, o.FIXTURES)
    configuration()
    require(o.sha(driver) == o.sha(DRIVER), "SQLite caller changed during execution")
    require(
        identity == o.sha(MANIFEST) and parent_sha == o.sha(parent / "acceptance.json"),
        "SQLite parent/manifest changed",
    )
    artifacts = {
        str(p.relative_to(output)): o.sha(p)
        for p in output.rglob("*")
        if p.is_file() and p.name != "result.json"
    }
    dump(
        output / "result.json",
        {
            "schema_version": 1,
            "phase": PHASE,
            "status": "PASS" if not result else "mismatch",
            "execution_id": str(uuid.uuid4()),
            "fresh_reference_bootstrap": False,
            "parent_execution_id": acceptance["execution_id"],
            "parent_acceptance_sha256": parent_sha,
            "reference_run_sha256": o.sha(parent / "reference/run.json"),
            "manifest_sha256": identity,
            "parents": parents(),
            "module_sha256": modules,
            "artifacts_sha256": artifacts,
            "mutants": controls,
            "scripted_scenarios_per_backend": len(recipes["scenarios"]),
            "scripted_operations_per_backend": sum(
                len(s["operations"]) for s in recipes["scenarios"]
            ),
            "exported_cases_per_reference_backend": len(m.EXPORTED),
            "reference_backends": list(REFERENCE),
            "native_backends": list(NATIVE),
            "product_integration": False,
            "full_storage_parity": False,
            "d1_evidence": config["d1_evidence"],
        },
    )
    return result


def inherited_mutation(backend: Any, name: str) -> None:
    inherited = {
        "schema_version": 1,
        "phase": m.PHASE,
        "backend": "python",
        "scenarios": backend["scenarios"][: len(m.SCENARIOS)],
    }
    # m.trace has the older error shape; adapt only its private mutation
    # dispatch on a copy while preserving the full SQLite witness outside.
    for row in inherited["scenarios"]:
        for step in row["operations"]:
            if step["outcome"]["status"] == "rejected":
                step["outcome"]["error"].pop("code")
    changed = m._mutation(inherited, name)
    for row in changed["scenarios"]:
        for step in row["operations"]:
            if step["outcome"]["status"] == "rejected":
                step["outcome"]["error"]["code"] = None
    backend["scenarios"][: len(m.SCENARIOS)] = changed["scenarios"]


def mutate(actual: Any, name: str) -> Any:
    result = copy.deepcopy(actual)
    if name in m.MUTANTS:
        inherited_mutation(result["backends"][0], name)
    else:
        backend = result["backends"][1 if name in ("lost-reopen", "reopen-mint") else 0]
        sid, operation = {
            "lost-reopen": ("identity", "exact"),
            "next-id": ("identity", "mint21"),
            "next-seq": ("a-request", "s0"),
            "signed-zero": ("b-zero", "s1"),
            "raw-version": ("e-create-negative-zero", "s3"),
            "semantic-error": ("f-dangling-True", "s1"),
            "reopen-mint": ("document-atomic", "highwater-retained"),
        }[name]
        row = next(r for r in backend["scenarios"] if r["id"] == sid)
        step = next(s for s in row["operations"] if s["id"] == operation)
        if name == "semantic-error":
            step["outcome"]["error"]["message"] = "wrong missing ancestor"
        elif name == "lost-reopen":
            step["outcome"]["value"] = ["undefined"]
        else:
            tree = v.decode(step["outcome"]["value"])
            if name == "signed-zero":
                tree["entry"]["data"]["zero"] = 0.0
            elif name == "raw-version":
                tree["version"] = 0.0
            else:
                tree += 1
            step["outcome"]["value"] = v.encode(tree)
    return result


MUTANTS = (
    *m.MUTANTS,
    "lost-reopen",
    "next-id",
    "next-seq",
    "signed-zero",
    "raw-version",
    "semantic-error",
    "reopen-mint",
)


def mutants(output: Path, expected: Any, actual: Any, recipes: Any, env: Any) -> Any:
    results = []
    for name in MUTANTS:
        path = output / (name + "-mutant.json")
        changed = trace(mutate(actual, name), recipes)
        dump(path, changed)
        for label, source, wanted in (
            ("positive", output / "native.json", 0),
            ("negative", path, 1),
        ):
            _, _, code = process(
                [
                    sys.executable,
                    "-I",
                    str(Path(__file__).resolve()),
                    "compare",
                    str(output / "observed.json"),
                    str(source),
                ],
                ROOT,
                output,
                name + "-" + label,
                {"PATH": env["PATH"], "HOME": env["HOME"]},
                30,
            )
            require(
                code == wanted, "SQLite mutant requires valid mismatch1, never invalid2"
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("expected", type=Path)
    compare.add_argument("actual", type=Path)
    run = commands.add_parser("run")
    run.add_argument("--acceptance", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    internal = commands.add_parser("_native")
    internal.add_argument("output", type=Path)
    internal.add_argument("stores", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "compare":
            return comparison(load(args.expected), load(args.actual))
        if args.command == "_native":
            dump(args.output, native(args.stores))
            return 0
        return execute(args.acceptance, args.output)
    except Exception as exc:  # noqa: BLE001 -- ordinary failures are invalid, never mismatch1
        print(f"Invalid SQLite evidence: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

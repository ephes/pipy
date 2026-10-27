"""Pinned supplied current-delta storage pairing: 0 equal, 1 mismatch, 2 invalid."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "current_delta_profile", ROOT / "scripts/durable_delta_ops_comparison.py"
)
assert spec and spec.loader
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
o = d.o
from pipy_harness.native.durable.memory import MemoryStorage  # noqa: E402
from pipy_harness.native.durable.sqlite import open_sqlite_storage  # noqa: E402
from pipy_harness.native.durable.storage import StorageWrite  # noqa: E402

INPUT = ROOT / "tests/fixtures/durable/storage-current-deltas-v1.input.json"
MANIFEST = ROOT / "tests/fixtures/durable/storage-current-deltas-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/storage-current-deltas.mts"
MODULES = (
    {
        "packages/durable/src/" + name + ".ts"
        for name in (
            "errors",
            "ids",
            "storage/memory",
            "storage/scan",
            "storage/sqlite/node",
            "storage/sqlite/storage",
            "storage/sqlite/schema",
            "storage/sqlite/migrations",
        )
    }
    | {
        "packages/chord/src/" + name + ".ts"
        for name in (
            "context/index",
            "json",
            "delta/index",
            "delta/tracker",
            "delta/diff",
            "delta/apply-immutable-trusted",
            "sqlite/index",
            "sqlite/node",
            "sqlite/schema",
            "sqlite/migrations",
        )
    }
    | {
        "packages/telemetry/src/" + name + ".ts"
        for name in ("index", "memory", "metrics", "noop")
    }
)
SOURCES = MODULES | {"package.json", "packages/durable/test/sqlite-storage.test.ts"}
CLOSURE = {
    "src/pipy_harness/native/durable/" + name + ".py"
    for name in (
        "__init__",
        "codec",
        "records",
        "storage",
        "memory",
        "delta_ops",
        "sqlite",
        "sqlite_schema",
        "sqlite_connection",
        "ownership",
    )
} | {
    "src/pipy_harness/native/agent/json_values.py",
    "scripts/durable_current_deltas_comparison.py",
    "scripts/durable_delta_ops_comparison.py",
    "scripts/durable_pi_comparison.py",
    "scripts/durable_reference_contract.py",
    "scripts/durable_reference_acceptance.py",
    "scripts/durable_reference/cases/storage-current-deltas.mts",
    "tests/fixtures/durable/storage-current-deltas-v1.input.json",
    "tests/fixtures/durable/storage-current-deltas-v1.old-source.json",
    "tests/durable/test_current_deltas.py",
    "tests/durable/test_memory_storage.py",
    "tests/durable/test_sqlite_storage.py",
    "tests/durable/test_sqlite_documents.py",
    "tests/durable/test_sqlite_ownership.py",
    "tests/durable/test_sqlite_schema.py",
    "tests/durable/storage_contract_cases.py",
    "docs/durable-current-deltas.md",
    "tests/durable/sqlite_child_support.py",
}


def recipes() -> list[dict[str, Any]]:
    value = d.load(INPUT)
    o.contract.shape(value, {"schema_version", "cases"}, "current-delta recipes")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise o.EvidenceError("Recipe version drift")
    ids = []
    for case in o.contract.array(value["cases"], "cases"):
        o.contract.shape(case, {"id", "writes"}, "current-delta case")
        if type(case["id"]) is not str or not case["id"]:
            raise o.EvidenceError("Invalid case identity")
        ids.append(case["id"])
        for writes in o.contract.array(case["writes"], "write batches"):
            for write in o.contract.array(writes, "writes"):
                StorageWrite(write)
    if len(ids) != len(set(ids)) or not ids:
        raise o.EvidenceError("Case identity cardinality")
    return value["cases"]


def native(inputs: list[dict[str, Any]], output: Path) -> list[dict[str, Any]]:
    rows = []
    for backend in ("memory", "sqlite", "reopen"):
        for index, case in enumerate(inputs):
            path = str(output / f"native-{backend}-{index}.sqlite")
            store = (
                MemoryStorage()
                if backend == "memory"
                else open_sqlite_storage(path, mode="create")
            )
            steps = []
            try:
                for writes in case["writes"]:
                    seq = store.commit(tuple(StorageWrite(write) for write in writes))
                    if backend == "reopen":
                        store.close()
                        store = open_sqlite_storage(path, mode="open")
                    document = store.document(2)
                    assert document is not None
                    steps.append(
                        {"seq": seq, "document": d.profile(document.to_tree())}
                    )
            finally:
                store.close()
            rows.append({"backend": backend, "id": case["id"], "steps": steps})
    return rows


def observations(value: Any) -> list[dict[str, Any]]:  # noqa: C901 - closed trace grammar
    inputs = recipes()
    if type(value) is not list or len(value) != 3 * len(inputs):
        raise o.EvidenceError("Observation cardinality")
    identities = [
        (backend, case["id"])
        for backend in ("memory", "sqlite", "reopen")
        for case in inputs
    ]
    for row, (backend, id) in zip(value, identities, strict=True):
        o.contract.shape(row, {"backend", "id", "steps"}, "observation")
        if row["backend"] != backend or row["id"] != id:
            raise o.EvidenceError("Observation identity drift")
        case = next(case for case in inputs if case["id"] == id)
        steps = o.contract.array(row["steps"], "steps")
        if not steps or len(steps) > len(case["writes"]):
            raise o.EvidenceError("Step cardinality")
        for index, step in enumerate(steps):
            if type(step) is not dict:
                raise o.EvidenceError("Step shape")
            if set(step) == {"seq", "document"}:
                if type(step["seq"]) is not int or step["seq"] != index + 1:
                    raise o.EvidenceError("Sequence drift")
                d.valid_profile(step["document"])
            elif set(step) == {"error"} and index == len(steps) - 1:
                o.contract.shape(step["error"], {"name", "code"}, "error")
                if type(step["error"]["name"]) is not str or type(
                    step["error"]["code"]
                ) not in (str, type(None)):
                    raise o.EvidenceError("Invalid error")
            else:
                raise o.EvidenceError("Step shape")
        if "error" not in steps[-1] and len(steps) != len(case["writes"]):
            raise o.EvidenceError("Incomplete observation")
    return value


def normalized_profile(profile: Any) -> Any:
    """Pi SQLite JSON.stringify loses negative zero; native persists its bits."""
    if profile[0] == "number" and profile[1] == "8000000000000000":
        return ["number", "0000000000000000"]
    if profile[0] == "array":
        return ["array", [normalized_profile(v) for v in profile[1]]]
    if profile[0] == "object":
        return ["object", [[k, normalized_profile(v)] for k, v in profile[1]]]
    return profile


def expected_reference(actual: Any) -> Any:
    result = json.loads(json.dumps(observations(actual)))
    for row in result:
        if row["backend"] == "memory":
            continue
        if row["id"] in {"version-fraction", "version-huge"}:
            row["steps"] = [{"error": {"name": "Error", "code": "ERR_SQLITE_ERROR"}}]
            continue
        for step in row["steps"]:
            step["document"] = normalized_profile(step["document"])
    return result


def compare(expected: Any, actual: Any) -> int:
    native_rows = observations(actual)
    memory = {
        row["id"]: row["steps"] for row in native_rows if row["backend"] == "memory"
    }
    agreement = all(row["steps"] == memory[row["id"]] for row in native_rows)
    return int(not agreement or observations(expected) != expected_reference(actual))


def manifest() -> dict[str, Any]:
    config = d.load(MANIFEST)
    o.contract.shape(
        config,
        {"schema_version", "reference_commit", "sources", "closure"},
        "current-delta manifest",
    )
    if (
        type(config["schema_version"]) is not int
        or config["schema_version"] != 1
        or config["reference_commit"] != o.PIN
    ):
        raise o.EvidenceError("Manifest identity drift")
    o.contract.shape(config["sources"], SOURCES, "upstream closure")
    o.contract.shape(config["closure"], CLOSURE, "native closure")
    for name, digest in config["closure"].items():
        o.contract.digest(digest)
        if o.sha(ROOT / name) != digest:
            raise o.EvidenceError("Native closure drift")
    for digest in config["sources"].values():
        o.contract.digest(digest)
    return config


def run(source: Path, output: Path) -> int:  # noqa: C901 - bounded snapshot lifecycle
    if not source.is_absolute() or not (source / ".git").exists():
        raise o.EvidenceError("Reference must be an absolute checkout root")
    source = source.resolve()
    config = manifest()
    manifest_hash = o.sha(MANIFEST)
    if output.resolve().is_relative_to(source):
        raise o.EvidenceError("Output must be outside upstream")
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise o.EvidenceError("Output entry already exists")
    output.mkdir(mode=0o700)
    snapshot = output / "snapshot"
    snapshot.mkdir(mode=0o700)
    env = o.child_env(output)

    def execute(command: list[str], label: str) -> bytes:
        return o.run(command, snapshot, output, label, env, timeout=120)

    for binary, version in (("node", "v26.12.0"), ("npm", "11.20.0")):
        if (
            execute([binary, "--version"], binary + "-version").decode().strip()
            != version
        ):
            raise o.EvidenceError("Runtime pin drift")
    for index, name in enumerate(sorted(SOURCES)):
        data = execute(
            ["git", "-C", str(source), "show", o.PIN + ":" + name], f"source-{index}"
        )
        destination = snapshot / name
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination.write_bytes(data)
    o.verify_sources(snapshot, config["sources"])
    shutil.copyfile(DRIVER, snapshot / "storage-current-deltas.mts")
    inputs = recipes()
    d.dump(output / "recipes.json", {"cases": inputs, "modules": sorted(MODULES)})
    raw = execute(
        [
            "node",
            str(snapshot / "storage-current-deltas.mts"),
            str(output / "recipes.json"),
            str(output),
        ],
        "node-current-deltas",
    )
    response = json.loads(raw, object_pairs_hook=o._pairs, parse_constant=o._constant)
    o.contract.shape(response, {"observed", "loaded"}, "reference response")
    loaded = {(snapshot / name).resolve().as_uri() for name in MODULES}
    if set(response["loaded"]) != loaded or len(response["loaded"]) != len(loaded):
        raise o.EvidenceError("Loaded module closure drift")
    observed = observations(response["observed"])
    actual = native(inputs, output)
    d.dump(output / "observed.json", observed)
    d.dump(output / "native.json", actual)
    expected = expected_reference(actual)
    positive = compare(expected, actual)
    mutant = json.loads(json.dumps(actual))
    mutant[0]["steps"][0]["document"] = d.profile({"mutant": True})
    negative = compare(expected, mutant)
    if positive != 0 or negative != 1:
        raise o.EvidenceError("Comparator controls failed")
    equal = compare(observed, actual) == 0
    o.verify_sources(snapshot, config["sources"])
    if (
        manifest() != config
        or o.sha(MANIFEST) != manifest_hash
        or o.sha(snapshot / "storage-current-deltas.mts") != o.sha(DRIVER)
    ):
        raise o.EvidenceError("Evidence identity changed during run")
    d.dump(
        output / "result.json",
        {
            "equal": equal,
            "cases": len(inputs),
            "controls": {"positive": positive, "negative": negative},
            "reference_commit": o.PIN,
            "sources": config["sources"],
            "closure": config["closure"],
            "manifest_sha256": manifest_hash,
            "loaded": response["loaded"],
            "native_imports": {
                name: str(sys.modules[name].__file__)
                for name in (
                    MemoryStorage.__module__,
                    open_sqlite_storage.__module__,
                    StorageWrite.__module__,
                )
            },
            "artifacts": {
                name: o.sha(output / name)
                for name in (
                    "recipes.json",
                    "observed.json",
                    "native.json",
                    "node-current-deltas.stdout",
                )
            },
            "fresh_reference_bootstrap": False,
            "supplied_current_deltas_only": True,
        },
    )
    return 0 if equal else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run")
    runner.add_argument("--pi-source", type=Path, required=True)
    runner.add_argument("--output", type=Path, required=True)
    comparison = sub.add_parser("compare")
    comparison.add_argument("expected", type=Path)
    comparison.add_argument("actual", type=Path)
    args = parser.parse_args()
    try:
        return (
            run(args.pi_source, args.output)
            if args.command == "run"
            else compare(d.load(args.expected), d.load(args.actual))
        )
    except (o.EvidenceError, OSError, ValueError, TypeError, RecursionError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

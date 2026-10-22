"""Pure pinned decoded-operation pairing: run/compare exit 0 equal, 1 mismatch, 2 invalid."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "delta_reference_parent", ROOT / "scripts/durable_pi_comparison.py"
)
assert spec and spec.loader
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)
from pipy_harness.native.durable.codec import OwnedJson  # noqa: E402
from pipy_harness.native.durable.delta_ops import (  # noqa: E402
    DeltaPathError,
    DeltaUnsafePath,
    apply_operations,
)

INPUT = ROOT / "tests/fixtures/durable/delta-ops-v1.input.json"
MANIFEST = ROOT / "tests/fixtures/durable/delta-ops-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/delta-ops.mts"
MODULES = {
    "packages/chord/src/json.ts",
    "packages/chord/src/delta/index.ts",
    "packages/chord/src/delta/diff.ts",
    "packages/chord/src/delta/tracker.ts",
    "packages/chord/src/delta/apply-immutable-trusted.ts",
    "packages/chord/src/delta/revision-validator.ts",
}
SOURCES = MODULES | {
    "package.json",
    "packages/chord/test/delta.test.ts",
    "packages/chord/test/delta-apply-immutable.test.ts",
}
CLOSURE = {
    "src/pipy_harness/native/durable/__init__.py",
    "src/pipy_harness/native/durable/codec.py",
    "src/pipy_harness/native/durable/delta_ops.py",
    "tests/durable/test_delta_ops.py",
    "tests/durable/test_delta_ops_comparison.py",
    "scripts/durable_delta_ops_comparison.py",
    "scripts/durable_reference/cases/delta-ops.mts",
    "tests/fixtures/durable/delta-ops-v1.input.json",
    "scripts/durable_pi_comparison.py",
    "scripts/durable_reference_contract.py",
    "scripts/durable_reference_acceptance.py",
}


def load(path: Path) -> Any:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=o._pairs,
        parse_constant=o._constant,
    )
    OwnedJson(value)
    return value


def dump(path: Path, value: Any) -> None:
    OwnedJson(value)
    path.write_text(
        json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
    )


def profile(value: Any) -> Any:
    if value is None:
        return ["null"]
    if type(value) is bool:
        return ["bool", value]
    if type(value) in (int, float):
        return ["number", struct.pack(">d", value).hex()]
    if type(value) is str:
        return ["string", value.encode("utf-16-be", "surrogatepass").hex()]
    if type(value) is list:
        return ["array", [profile(v) for v in value]]
    return [
        "object",
        sorted(
            [
                [k.encode("utf-16-be", "surrogatepass").hex(), profile(v)]
                for k, v in value.items()
            ]
        ),
    ]


def valid_profile(value: Any) -> None:  # noqa: C901 - closed recursive evidence grammar
    if type(value) is not list or not value:
        raise o.EvidenceError("Invalid value profile")
    kind = value[0]
    if kind == "null" and len(value) == 1:
        return
    if len(value) != 2:
        raise o.EvidenceError("Invalid profile arity")
    payload = value[1]
    if kind == "bool" and type(payload) is bool:
        return
    if kind == "number" and type(payload) is str:
        try:
            raw = bytes.fromhex(payload)
            if (
                len(raw) == 8
                and raw.hex() == payload
                and math.isfinite(struct.unpack(">d", raw)[0])
            ):
                return
        except ValueError:
            pass
    if kind == "string" and type(payload) is str:
        try:
            raw = bytes.fromhex(payload)
            if len(raw) % 2 == 0 and raw.hex() == payload:
                return
        except ValueError:
            pass
    if kind == "array" and type(payload) is list:
        for child in payload:
            valid_profile(child)
        return
    if kind == "object" and type(payload) is list:
        keys = []
        for pair in payload:
            if type(pair) is not list or len(pair) != 2:
                raise o.EvidenceError("Invalid object profile")
            valid_profile(["string", pair[0]])
            valid_profile(pair[1])
            keys.append(pair[0])
        if keys == sorted(set(keys)):
            return
    raise o.EvidenceError("Invalid value profile")


def recipes() -> list[dict[str, Any]]:
    value = load(INPUT)
    o.contract.shape(value, {"schema_version", "cases"}, "delta recipes")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise o.EvidenceError("Recipe version")
    for case in o.contract.array(value["cases"], "cases"):
        o.contract.shape(case, {"value", "ops"}, "delta recipe")
        OwnedJson(case)
    return value["cases"]


def native(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for case in inputs:
        try:
            result = apply_operations(OwnedJson(case["value"]), OwnedJson(case["ops"]))
            outcome = {"ops": profile(case["ops"]), "result": profile(result.thaw())}
        except (TypeError, DeltaPathError, DeltaUnsafePath) as error:
            outcome = {
                "ops": profile(case["ops"]),
                "error": "TypeError"
                if type(error) is TypeError
                else "PathError"
                if type(error) is DeltaPathError
                else "UnsafePathError",
            }
        output.append(outcome)
    return output


def observations(value: Any, inputs: list[dict[str, Any]]) -> Any:
    if type(value) is not list or len(value) != len(inputs):
        raise o.EvidenceError("Observation cardinality")
    for row, case in zip(value, inputs, strict=True):
        if type(row) is not dict or set(row) not in (
            {"ops", "result"},
            {"ops", "error"},
        ):
            raise o.EvidenceError("Observation shape")
        valid_profile(row["ops"])
        if row["ops"] != profile(case["ops"]):
            raise o.EvidenceError("Operation identity drift")
        if "result" in row:
            valid_profile(row["result"])
        elif row["error"] not in {"TypeError", "PathError", "UnsafePathError"}:
            raise o.EvidenceError("Unclassified error")
    return value


def compare(expected: Any, actual: Any) -> int:
    inputs = recipes()
    observations(expected, inputs)
    observations(actual, inputs)
    return int(expected != actual)


def manifest() -> dict[str, Any]:
    config = load(MANIFEST)
    o.contract.shape(
        config,
        {"schema_version", "reference_commit", "sources", "closure"},
        "delta manifest",
    )
    if (
        type(config["schema_version"]) is not int
        or config["schema_version"] != 1
        or config["reference_commit"] != o.PIN
    ):
        raise o.EvidenceError("Delta manifest identity drift")
    o.contract.shape(config["sources"], SOURCES, "delta source closure")
    o.contract.shape(config["closure"], CLOSURE, "delta native closure")
    for name, digest in config["closure"].items():
        o.contract.digest(digest)
        if o.sha(ROOT / name) != digest:
            raise o.EvidenceError("Delta closure drift")
    for digest in config["sources"].values():
        o.contract.digest(digest)
    return config


def run(source: Path, output: Path) -> int:  # noqa: C901 - bounded snapshot lifecycle
    if not source.is_absolute():
        raise o.EvidenceError("Reference source must be absolute")
    source = source.resolve()
    if not (source / ".git").exists():
        raise o.EvidenceError("Reference source must be a checkout root")
    config = manifest()
    manifest_hash = o.sha(MANIFEST)
    if output.resolve().is_relative_to(source):
        raise o.EvidenceError("Output must be outside upstream")
    # Preserve the requested directory entry: resolving it would follow a
    # dangling symlink and create its previously absent target.
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise o.EvidenceError("Output entry already exists")
    output.mkdir(mode=0o700, exist_ok=False)
    snapshot = output / "snapshot"
    snapshot.mkdir(mode=0o700)
    env = o.child_env(output)

    def execute(command: list[str], label: str) -> bytes:
        return o.run(command, snapshot, output, label, env, timeout=120)

    for binary, version in [("node", "v26.12.0"), ("npm", "11.20.0")]:
        if (
            execute([binary, "--version"], binary + "-version").decode().strip()
            != version
        ):
            raise o.EvidenceError("Delta runtime pin drift")
    for index, name in enumerate(sorted(SOURCES)):
        data = execute(
            ["git", "-C", str(source), "show", o.PIN + ":" + name], f"source-{index}"
        )
        path = snapshot / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(data)
    o.verify_sources(snapshot, config["sources"])
    shutil.copyfile(DRIVER, snapshot / "delta-ops.mts")
    inputs = recipes()
    dump(output / "recipes.json", inputs)
    raw = execute(
        [
            "node",
            str(snapshot / "delta-ops.mts"),
            str(output.resolve() / "recipes.json"),
        ],
        "node-delta",
    )
    response = json.loads(raw, object_pairs_hook=o._pairs, parse_constant=o._constant)
    o.contract.shape(response, {"observed", "loaded"}, "delta response")
    expected_loaded = {(snapshot / name).resolve().as_uri() for name in MODULES}
    if set(response["loaded"]) != expected_loaded or len(response["loaded"]) != len(
        expected_loaded
    ):
        raise o.EvidenceError("Delta loaded module drift")
    observed = observations(response["observed"], inputs)
    dump(output / "observed.json", observed)
    actual = native(inputs)
    positive = compare(observed, observed)
    mutant = json.loads(json.dumps(observed))
    mutant[0]["result"] = profile("permanent mismatch control")
    negative = compare(observed, mutant)
    if positive != 0 or negative != 1:
        raise o.EvidenceError("Delta comparator controls failed")
    dump(output / "native.json", actual)
    # Compare closed operation/result profiles without scalar type coercion.
    equal = json.dumps(observed, sort_keys=True) == json.dumps(actual, sort_keys=True)
    o.verify_sources(snapshot, config["sources"])
    if (
        o.sha(snapshot / "delta-ops.mts") != o.sha(DRIVER)
        or manifest() != config
        or o.sha(MANIFEST) != manifest_hash
    ):
        raise o.EvidenceError("Delta identity changed during execution")
    dump(
        output / "result.json",
        {
            "equal": equal,
            "recipes": len(inputs),
            "controls": {"positive": positive, "negative": negative},
            "reference_commit": o.PIN,
            "sources": config["sources"],
            "closure": config["closure"],
            "manifest_sha256": manifest_hash,
            "native_import": str(sys.modules[apply_operations.__module__].__file__),
            "artifacts": {
                name: o.sha(output / name)
                for name in [
                    "recipes.json",
                    "observed.json",
                    "native.json",
                    "node-delta.stdout",
                ]
            },
            "loaded": response["loaded"],
            "dependencies": {},
            "fresh_reference_bootstrap": False,
            "pure_delta_only": True,
        },
    )
    return 0 if equal else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--pi-source", type=Path, required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    cmp = sub.add_parser("compare")
    cmp.add_argument("expected", type=Path)
    cmp.add_argument("actual", type=Path)
    args = parser.parse_args()
    try:
        return (
            run(args.pi_source, args.output)
            if args.command == "run"
            else compare(load(args.expected), load(args.actual))
        )
    except (o.EvidenceError, OSError, ValueError, TypeError, RecursionError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

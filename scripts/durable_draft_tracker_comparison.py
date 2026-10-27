"""Pinned tracker pairing: 0 equivalent under declared key projection, 1 mismatch, 2 invalid."""

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
    "tracker_reference_parent", ROOT / "scripts/durable_pi_comparison.py"
)
assert spec and spec.loader
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)
from pipy_harness.native.durable.codec import OwnedJson  # noqa: E402
from pipy_harness.native.durable.draft_tracker import (  # noqa: E402
    Tracker,
    TrackerRevoked,
)

INPUT = ROOT / "tests/fixtures/durable/draft-tracker-v1.input.json"
MANIFEST = ROOT / "tests/fixtures/durable/draft-tracker-v1.manifest.json"
COMPARISON_PROFILE = "js-array-index-own-key-order-v1"
DRIVER = ROOT / "scripts/durable_reference/cases/draft-tracker.mts"
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
    "packages/chord/test/delta-tracker/tracker.test.ts",
    "packages/chord/test/delta-apply-immutable.test.ts",
}
CLOSURE = {
    "src/pipy_harness/native/agent/json_values.py",
    "docs/durable-draft-tracker.md",
    "src/pipy_harness/native/durable/__init__.py",
    "src/pipy_harness/native/durable/codec.py",
    "src/pipy_harness/native/durable/delta_ops.py",
    "src/pipy_harness/native/durable/draft_tracker.py",
    "tests/durable/test_draft_tracker.py",
    "tests/durable/test_draft_tracker_comparison.py",
    "scripts/durable_draft_tracker_comparison.py",
    "scripts/durable_reference/cases/draft-tracker.mts",
    "tests/fixtures/durable/draft-tracker-v1.input.json",
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
        [
            [k.encode("utf-16-be", "surrogatepass").hex(), profile(v)]
            for k, v in value.items()
        ],
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
        if len(keys) == len(set(keys)):
            return
    raise o.EvidenceError("Invalid value profile")


def index_key(units: str) -> int | None:
    """JS array-index spelling, inspecting at most ten UTF-16 units."""
    if not 4 <= len(units) <= 40:
        return None
    key = bytes.fromhex(units).decode("utf-16-be", "surrogatepass")
    if key == "0":
        return 0
    if not "1" <= key[0] <= "9" or any(not "0" <= c <= "9" for c in key):
        return None
    number = int(key)  # Already bounded to ten ASCII digits.
    return number if number < 4_294_967_295 else None


def comparison_profile(value: Any) -> Any:
    """Project validated profiles only; retain raw profiles and product ordering."""
    if value[0] == "array":
        return ["array", [comparison_profile(child) for child in value[1]]]
    if value[0] != "object":
        return value
    indices, strings = [], []
    for key, child in value[1]:
        pair = [key, comparison_profile(child)]
        index = index_key(key)
        if index is None:
            strings.append(pair)
        else:
            indices.append((index, pair))
    return ["object", [pair for _, pair in sorted(indices)] + strings]


def profile_equivalent(left: Any, right: Any) -> bool:
    # JSON comparison of typed profiles never equates bool and numeric payloads.
    return json.dumps(comparison_profile(left)) == json.dumps(comparison_profile(right))


def projection_controls() -> list[dict[str, Any]]:
    """Permanent equivalence and rejection witnesses, also recorded in each run."""
    cases = [
        ("index-reordering", {"2": 2, "1": 1}, {"1": 1, "2": 2}, True),
        ("last-index", {"01": 0, "4294967294": 4}, {"4294967294": 4, "01": 0}, True),
        ("zero-index", {"a": 1, "0": 0}, {"0": 0, "a": 1}, True),
        ("leading-zero", {"01": 0, "a": 1}, {"a": 1, "01": 0}, False),
        (
            "non-index-boundary",
            {"4294967295": 5, "01": 0},
            {"01": 0, "4294967295": 5},
            False,
        ),
        ("ordinary-order", {"a": 1, "b": 2}, {"b": 2, "a": 1}, False),
        ("nested-order", {"x": {"a": 1, "b": 2}}, {"x": {"b": 2, "a": 1}}, False),
        ("double-zero", {"00": 0, "a": 1}, {"a": 1, "00": 0}, False),
        ("unicode-digit", {"１": 1, "a": 2}, {"a": 2, "１": 1}, False),
        (
            "long-numeric-string",
            {"9" * 5000: 1, "a": 2},
            {"a": 2, "9" * 5000: 1},
            False,
        ),
        (
            "op-order",
            [["s", ["2"], 2], ["s", ["1"], 1]],
            [["s", ["1"], 1], ["s", ["2"], 2]],
            False,
        ),
        ("missing-key", {"1": 1, "2": 2}, {"1": 1}, False),
        ("changed-key", {"1": 1}, {"01": 1}, False),
        ("changed-value", {"1": 1}, {"1": 2}, False),
        ("bool-number", {"1": True}, {"1": 1}, False),
        ("false-zero", {"1": False}, {"1": 0}, False),
        ("signed-zero", {"1": -0.0}, {"1": 0.0}, False),
        ("utf16", {"1": "\ud800"}, {"1": "\ud801"}, False),
    ]
    results = []
    for name, left, right, expected in cases:
        actual = profile_equivalent(profile(left), profile(right))
        if actual is not expected:
            raise o.EvidenceError(f"Tracker projection control failed: {name}")
        results.append({"name": name, "equivalent": actual, "expected": expected})
    return results


def recipes() -> list[dict[str, Any]]:
    value = load(INPUT)
    o.contract.shape(value, {"schema_version", "cases"}, "tracker recipes")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise o.EvidenceError("Recipe version")
    names = set()
    for case in o.contract.array(value["cases"], "cases"):
        o.contract.shape(case, {"name", "base", "actions"}, "tracker recipe")
        if type(case["name"]) is not str or case["name"] in names:
            raise o.EvidenceError("Recipe identity")
        names.add(case["name"])
        OwnedJson(case)
        if type(case["base"]) not in (dict, list) or type(case["actions"]) is not list:
            raise o.EvidenceError("Recipe containers")
        held: set[str] = set()
        for action in case["actions"]:
            _action(action, held)
    return value["cases"]


def _path(value: Any, *, nonempty: bool = False) -> None:
    if type(value) is not list or (nonempty and not value):
        raise o.EvidenceError("Recipe path")
    if any(not (type(key) is str or type(key) is int and key >= 0) for key in value):
        raise o.EvidenceError("Recipe path segment")


def _splice_args(values: list[Any]) -> None:
    if (
        len(values) != 3
        or type(values[0]) is not int
        or type(values[1]) is not int
        or values[1] < 0
        or type(values[2]) is not list
    ):
        raise o.EvidenceError("Recipe splice")


def _action(action: Any, held: set[str]) -> None:  # noqa: C901 - closed operation schema
    arities = {
        "set": 3,
        "del": 2,
        "splice": 5,
        "reverse": 2,
        "hold": 3,
        "hset": 4,
        "hsplice": 6,
        "place": 3,
        "pop_reinsert": 3,
        "clear": 2,
    }
    if (
        type(action) is not list
        or not action
        or type(action[0]) is not str
        or len(action) != arities.get(action[0])
    ):
        raise o.EvidenceError("Recipe action")
    tag, path, *args = action
    if tag in {"hold", "hset", "hsplice"}:
        if type(path) is not str:
            raise o.EvidenceError("Recipe held identity")
        if tag == "hold":
            held.add(path)
        elif path not in held:
            raise o.EvidenceError("Recipe missing held identity")
        _path(args[0], nonempty=tag == "hset")
        if tag == "hsplice":
            _splice_args(args[1:])
    else:
        _path(path, nonempty=tag in {"set", "del", "place"})
        if tag == "splice":
            _splice_args(args)
        elif tag == "place":
            _path(args[0])
        elif tag == "pop_reinsert" and (type(args[0]) is not int or args[0] < 0):
            raise o.EvidenceError("Recipe pop index")


def mutate(tracker: Tracker, actions: list[Any]) -> None:  # noqa: C901 - closed recipe dispatch
    root = tracker.root
    held = {}

    def get(path: list[Any], start: Any = None) -> Any:
        value = root if start is None else start
        for key in path:
            value = value[key]
        return value

    def set_value(path: list[Any], value: Any, start: Any = None) -> None:
        get(path[:-1], start)[path[-1]] = value

    def splice(target: Any, start: int, remove: int, items: list[Any]) -> None:
        length = len(target)
        start = max(0, length + start) if start < 0 else min(start, length)
        remove = max(0, min(remove, length - start))
        target[start : start + remove] = items

    for action, path, *args in actions:
        if action == "set":
            set_value(path, args[0])
        elif action == "del":
            del get(path[:-1])[path[-1]]
        elif action == "splice":
            splice(get(path), *args)
        elif action == "reverse":
            get(path).reverse()
        elif action == "hold":
            held[path] = get(args[0])
        elif action == "hset":
            set_value(args[0], args[1], held[path])
        elif action == "hsplice":
            splice(get(args[0], held[path]), *args[1:])
        elif action == "place":
            set_value(path, get(args[0]))
        elif action == "pop_reinsert":
            target = get(path)
            value = target.pop(args[0])
            target.insert(args[0], value)
        elif action == "clear":
            get(path).clear()
        else:
            raise o.EvidenceError("Unrecognized action")


def native(
    inputs: list[dict[str, Any]], raw: list[Any] | None = None
) -> list[dict[str, Any]]:
    output = []
    for case in inputs:
        tracker = Tracker(OwnedJson(case["base"]))
        root = tracker.root
        mutate(tracker, case["actions"])
        prepared = tracker.prepare()
        revoked = False
        try:
            len(root)
        except TrackerRevoked:
            revoked = True
        if raw is not None:
            raw.append(
                {
                    "name": case["name"],
                    "ops": prepared.ops.thaw(),
                    "replay": prepared.replayed_value.thaw(),
                    "revoked": revoked,
                }
            )
        output.append(
            {
                "name": case["name"],
                "ops": profile(prepared.ops.thaw()),
                "replay": profile(prepared.replayed_value.thaw()),
                "revoked": revoked,
            }
        )
    return output


def observations(
    value: Any, inputs: list[dict[str, Any]], *, require_prepared: bool = False
) -> Any:
    if type(value) is not list or len(value) != len(inputs):
        raise o.EvidenceError("Observation cardinality")
    for row, case in zip(value, inputs, strict=True):
        if type(row) is not dict or set(row) not in (
            {"name", "ops", "replay", "revoked"},
            {"name", "ops", "replay", "revoked", "prepared"},
        ):
            raise o.EvidenceError("Observation shape")
        if require_prepared and "prepared" not in row:
            raise o.EvidenceError("Node observation requires prepared profile")
        if row["name"] != case["name"] or type(row["revoked"]) is not bool:
            raise o.EvidenceError("Observation identity")
        for field in ("ops", "replay", "prepared"):
            if field in row:
                valid_profile(row[field])
    return value


def compare(expected: Any, actual: Any) -> int:
    inputs = recipes()
    observations(expected, inputs)
    observations(actual, inputs)

    return int(
        any(
            left["revoked"] != right["revoked"]
            or any(
                not profile_equivalent(left[key], right[key])
                for key in ("ops", "replay")
            )
            for left, right in zip(expected, actual, strict=True)
        )
    )


def manifest() -> dict[str, Any]:
    config = load(MANIFEST)
    o.contract.shape(
        config,
        {"schema_version", "reference_commit", "sources", "closure"},
        "tracker manifest",
    )
    if (
        type(config["schema_version"]) is not int
        or config["schema_version"] != 1
        or config["reference_commit"] != o.PIN
    ):
        raise o.EvidenceError("Tracker manifest identity drift")
    o.contract.shape(config["sources"], SOURCES, "tracker source closure")
    o.contract.shape(config["closure"], CLOSURE, "tracker native closure")
    for name, digest in config["closure"].items():
        o.contract.digest(digest)
        if o.sha(ROOT / name) != digest:
            raise o.EvidenceError("Tracker closure drift")
    for digest in config["sources"].values():
        o.contract.digest(digest)
    return config


def native_imports() -> dict[str, str]:
    result = {}
    for name in ("codec", "delta_ops", "draft_tracker"):
        module = sys.modules["pipy_harness.native.durable." + name]
        expected = (ROOT / "src/pipy_harness/native/durable" / (name + ".py")).resolve()
        if module.__file__ is None:
            raise o.EvidenceError("Native tracker import missing")
        actual = Path(module.__file__).resolve()
        if actual != expected:
            raise o.EvidenceError("Native tracker import drift")
        result[name] = str(actual)
    return result


def run(source: Path, output: Path) -> int:  # noqa: C901 - bounded snapshot lifecycle
    if not source.is_absolute():
        raise o.EvidenceError("Reference source must be absolute")
    source = source.resolve()
    if not (source / ".git").exists():
        raise o.EvidenceError("Reference source must be a checkout root")
    config = manifest()
    imports = native_imports()
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
            raise o.EvidenceError("Tracker runtime pin drift")
    for index, name in enumerate(sorted(SOURCES)):
        data = execute(
            ["git", "-C", str(source), "show", o.PIN + ":" + name], f"source-{index}"
        )
        path = snapshot / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(data)
    o.verify_sources(snapshot, config["sources"])
    shutil.copyfile(DRIVER, snapshot / "draft-tracker.mts")
    inputs = recipes()
    dump(output / "recipes.json", inputs)
    raw = execute(
        [
            "node",
            str(snapshot / "draft-tracker.mts"),
            str(output.resolve() / "recipes.json"),
        ],
        "node-delta",
    )
    response = json.loads(raw, object_pairs_hook=o._pairs, parse_constant=o._constant)
    o.contract.shape(response, {"observed", "raw", "loaded"}, "tracker response")
    expected_loaded = {(snapshot / name).resolve().as_uri() for name in MODULES}
    if set(response["loaded"]) != expected_loaded or len(response["loaded"]) != len(
        expected_loaded
    ):
        raise o.EvidenceError("Tracker loaded module drift")
    observed = observations(response["observed"], inputs, require_prepared=True)
    dump(output / "observed.json", observed)
    dump(output / "raw-node.json", response["raw"])
    native_raw: list[Any] = []
    actual = native(inputs, native_raw)
    dump(output / "raw-native.json", native_raw)
    positive = compare(observed, observed)
    mutant = json.loads(json.dumps(observed))
    mutant[0]["replay"] = profile("permanent mismatch control")
    negative = compare(observed, mutant)
    if positive != 0 or negative != 1:
        raise o.EvidenceError("Tracker comparator controls failed")
    controls = projection_controls()
    dump(output / "projection-controls.json", controls)
    dump(output / "native.json", actual)
    equivalent = compare(observed, actual) == 0
    raw_differences = []
    for left, right in zip(observed, actual, strict=True):
        fields = [
            key
            for key in ("ops", "replay")
            if json.dumps(left[key]) != json.dumps(right[key])
        ]
        if fields:
            raw_differences.append({"name": left["name"], "fields": fields})
    witnesses = [
        row["name"]
        for row in observed
        if json.dumps(row["prepared"]) != json.dumps(row["replay"])
    ]
    expected_witnesses = {
        "precision/signed-zero-restore-with-other-write",
        "precision/signed-zero-restore-inverse-other-write",
    }
    if set(witnesses) != expected_witnesses:
        raise o.EvidenceError("Prepared/replay witness drift")
    o.verify_sources(snapshot, config["sources"])
    if (
        o.sha(snapshot / "draft-tracker.mts") != o.sha(DRIVER)
        or manifest() != config
        or o.sha(MANIFEST) != manifest_hash
    ):
        raise o.EvidenceError("Tracker identity changed during execution")
    dump(
        output / "result.json",
        {
            "equivalent": equivalent,
            "comparison_profile": COMPARISON_PROFILE,
            "raw_ordered_profiles_equal": not raw_differences,
            "raw_ordered_profile_differences": raw_differences,
            "recipes": len(inputs),
            "controls": {"positive": positive, "negative": negative},
            "projection_controls": len(controls),
            "reference_commit": o.PIN,
            "sources": config["sources"],
            "closure": config["closure"],
            "manifest_sha256": manifest_hash,
            "native_import": str(sys.modules[Tracker.__module__].__file__),
            "native_imports": imports,
            "prepared_replay_witnesses": witnesses,
            "artifacts": {
                name: o.sha(output / name)
                for name in [
                    "recipes.json",
                    "observed.json",
                    "native.json",
                    "node-delta.stdout",
                    "raw-node.json",
                    "raw-native.json",
                    "projection-controls.json",
                ]
            },
            "loaded": response["loaded"],
            "dependencies": {},
            "fresh_reference_bootstrap": False,
            "pure_tracker_only": True,
        },
    )
    return 0 if equivalent else 1


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
    except Exception:  # noqa: BLE001 - CLI evidence boundary must fail closed.
        # Unexpected producer/native failures are invalid evidence, never a
        # semantic mismatch. Do not expose exception payloads or tracebacks.
        print("Unexpected tracker comparison failure", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

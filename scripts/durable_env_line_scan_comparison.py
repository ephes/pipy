"""Bounded pinned-source pure scanner comparison: 0 equal, 1 mismatch, 2 invalid.

Uses read-only git show and a private minimal source snapshot, the pinned
source resolver and existing deadline/process-group runner. This dependency-free
source closure needs no npm installation and makes no full env/parity claim.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "scanner_reference_parent", ROOT / "scripts/durable_pi_comparison.py"
)
assert spec and spec.loader
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)
from pipy_harness.native.durable.env.line_scan import LineScanner  # noqa: E402

INPUT = ROOT / "tests/fixtures/durable/env-line-scan-v1.input.json"
MANIFEST = ROOT / "tests/fixtures/durable/env-line-scan-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/env-line-scan.mts"
SOURCES = {
    "packages/durable/src/env/line-scan.ts",
    "packages/durable/src/env/decode.ts",
    "packages/durable/src/env/index.ts",
    "packages/durable/test/env-line-scan.test.ts",
    o.RESOLVER,
    "package.json",
    "tsconfig.json",
}
CLOSURE = {
    "src/pipy_harness/native/durable/env/__init__.py",
    "src/pipy_harness/native/durable/env/line_scan.py",
    "tests/durable/test_env_line_scan.py",
    "tests/durable/test_env_line_scan_comparison.py",
    "scripts/durable_env_line_scan_comparison.py",
    "scripts/durable_reference/cases/env-line-scan.mts",
    "tests/fixtures/durable/env-line-scan-v1.input.json",
    "scripts/durable_pi_comparison.py",
    "scripts/durable_reference_contract.py",
    "scripts/durable_reference_acceptance.py",
}


def random_values(seed: int) -> Any:
    """Exact upstream Mulberry32 uint32 arithmetic, including JS imul wrap."""
    state = seed
    mask = 0xFFFFFFFF
    while True:
        state = (state + 0x6D2B79F5) & mask
        t = ((state ^ (state >> 15)) * (state | 1)) & mask
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & mask)) & mask
        yield ((t ^ (t >> 14)) & mask) / 4294967296


def recipes() -> list[dict[str, Any]]:
    config = o.load(INPUT)
    expected = {
        "schema_version": 1,
        "generator": "mulberry32-upstream-5000-v1",
        "seeds": 5000,
        "chunk_sizes": [1, 2, 3, 7, 65536],
        "pieces": [
            "0a",
            "0a",
            "61",
            "6263",
            "efbbbf",
            "c3a9",
            "e282ac",
            "f09f9880",
            "e282",
            "ff",
            "80",
            "f09f",
            "0d0a",
        ],
    }
    if type(config) is not dict or json.dumps(config, sort_keys=True) != json.dumps(
        expected, sort_keys=True
    ):
        raise o.EvidenceError("Closed scanner recipe drift")
    pieces = [bytes.fromhex(s) for s in config["pieces"]]
    result = []
    for seed in range(1, 5001):
        values = random_values(seed)
        data = b"".join(
            pieces[int(next(values) * len(pieces))]
            for _ in range(int(next(values) * 60))
        )
        lines = data.count(b"\n") + 1
        start = int(next(values) * (lines + 2))
        end = (
            None if next(values) < 0.3 else start + 1 + int(next(values) * (lines + 1))
        )
        sizes = []
        offset = 0
        while offset < len(data):
            size = min(1 + int(next(values) * 7), len(data) - offset)
            sizes.append(size)
            offset += size
        for fixed in [None, *config["chunk_sizes"]]:
            chunks = (
                sizes
                if fixed is None
                else [min(fixed, len(data) - i) for i in range(0, len(data), fixed)]
            )
            result.append(
                {
                    "hex": data.hex(),
                    "start": start,
                    "end": end,
                    "chunks": [0, *chunks, 0],
                }
            )
    return result


def native(inputs: list[dict[str, Any]]) -> list[dict[str, int]]:
    output = []
    for recipe in inputs:
        data = bytes.fromhex(recipe["hex"])
        scanner = LineScanner(recipe["start"], recipe["end"])
        offset = 0
        for size in recipe["chunks"]:
            scanner.push(data[offset : offset + size])
            offset += size
        output.append(asdict(scanner.finish()))
    return output


def observations(value: Any, count: int) -> list[dict[str, int]]:
    fields = {
        "newlines",
        "start",
        "end",
        "first_line_end",
        "last_line_start",
        "selected_bytes",
        "first_line_bytes",
    }
    if type(value) is not list or len(value) != count:
        raise o.EvidenceError("Scanner observation cardinality")
    for row in value:
        o.contract.shape(row, fields, "scanner observation")
        if any(type(v) is not int or not 0 <= v <= 2**53 - 1 for v in row.values()):
            raise o.EvidenceError("Scanner observation integer")
    return value


def manifest() -> dict[str, Any]:
    config = o.load(MANIFEST)
    o.contract.shape(
        config,
        {"schema_version", "reference_commit", "sources", "closure"},
        "scanner manifest",
    )
    if (
        type(config["schema_version"]) is not int
        or config["schema_version"] != 1
        or config["reference_commit"] != o.PIN
    ):
        raise o.EvidenceError("Scanner manifest identity drift")
    o.contract.shape(config["sources"], SOURCES, "scanner source closure")
    o.contract.shape(config["closure"], CLOSURE, "scanner native closure")
    for name, digest in config["closure"].items():
        o.contract.digest(digest)
        if o.sha(ROOT / name) != digest:
            raise o.EvidenceError("Scanner closure drift")
    for digest in config["sources"].values():
        o.contract.digest(digest)
    return config


def run(source: Path, output: Path) -> int:
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
            raise o.EvidenceError("Scanner runtime pin drift")
    for index, name in enumerate(sorted(SOURCES)):
        data = execute(
            ["git", "-C", str(source), "show", o.PIN + ":" + name], f"source-{index}"
        )
        path = snapshot / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(data)
    o.verify_sources(snapshot, config["sources"])
    shutil.copyfile(DRIVER, snapshot / "env-line-scan.mts")
    inputs = recipes()
    o.dump(output / "recipes.json", inputs)
    raw = execute(
        [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(snapshot / "env-line-scan.mts"),
            str(output.resolve() / "recipes.json"),
        ],
        "node-scanner",
    )
    response = json.loads(raw, object_pairs_hook=o._pairs, parse_constant=o._constant)
    o.contract.shape(response, {"observed", "loaded"}, "scanner response")
    expected_loaded = [
        (snapshot / name).resolve().as_uri()
        for name in [
            "packages/durable/src/env/line-scan.ts",
            "packages/durable/src/env/decode.ts",
        ]
    ]
    if response["loaded"] != expected_loaded:
        raise o.EvidenceError("Scanner loaded module drift")
    observed = observations(response["observed"], len(inputs))
    actual = native(inputs)
    o.dump(output / "native.json", actual)
    # Compare exact field types as well as all seven values, never bool/int coercion.
    equal = json.dumps(observed, sort_keys=True) == json.dumps(actual, sort_keys=True)
    o.verify_sources(snapshot, config["sources"])
    if (
        o.sha(snapshot / "env-line-scan.mts") != o.sha(DRIVER)
        or manifest() != config
        or o.sha(MANIFEST) != manifest_hash
    ):
        raise o.EvidenceError("Scanner identity changed during execution")
    o.dump(
        output / "result.json",
        {
            "equal": equal,
            "recipes": len(inputs),
            "seeds": 5000,
            "reference_commit": o.PIN,
            "sources": config["sources"],
            "closure": config["closure"],
            "manifest_sha256": manifest_hash,
            "native_import": str(sys.modules[LineScanner.__module__].__file__),
            "artifacts": {
                name: o.sha(output / name)
                for name in ["recipes.json", "native.json", "node-scanner.stdout"]
            },
            "loaded": response["loaded"],
            "dependencies": {},
            "fresh_reference_bootstrap": False,
            "pure_scanner_only": True,
        },
    )
    return 0 if equal else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pi-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        return run(args.pi_source, args.output)
    except (o.EvidenceError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""CI-only whole-module partition and stdlib coverage gate; no test execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SCHEMA = 1
PROPERTY = "pipy_ci_nodeid"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assignment(nodeid: str, count: int) -> int:
    if count < 1:
        raise ValueError("Shard count must be positive")
    module = nodeid.split("::", 1)[0]
    return int(hashlib.sha256(module.encode("utf-8")).hexdigest(), 16) % count


def _git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "--no-optional-locks", *args], cwd=root)


def source_identity(root: Path, expected_sha: str) -> dict[str, Any]:
    """Read actual checkout identity and bytes, never write Git objects or refs."""
    sha = _git(root, "rev-parse", "HEAD").decode().strip()
    if sha != expected_sha:
        raise ValueError("Checkout SHA differs from the requested run")
    _git(root, "diff", "--exit-code", "--no-ext-diff", "--no-textconv", "HEAD", "--")
    entries = _git(root, "ls-files", "--stage", "-z").split(b"\0")
    files = []
    for entry in filter(None, entries):
        header, raw = entry.split(b"\t", 1)
        mode, _oid, stage = header.decode().split()
        if stage != "0" or mode not in {"100644", "100755", "120000"}:
            raise ValueError("Unsupported or unmerged source entry")
        path = root / raw.decode("utf-8")
        content = (
            path.readlink().as_posix().encode()
            if path.is_symlink()
            else path.read_bytes()
        )
        files.append([raw.decode("utf-8"), mode, hashlib.sha256(content).hexdigest()])
    encoded = json.dumps(files, ensure_ascii=True, separators=(",", ":")).encode()
    return {
        "sha": sha,
        "tree": _git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
        "fingerprint": hashlib.sha256(encoded).hexdigest(),
        "tracked_files": len(files),
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _unique(value: Any, name: str) -> list[str]:
    _require(isinstance(value, list) and all(isinstance(x, str) for x in value), name)
    _require(len(value) == len(set(value)), f"Duplicate {name}")
    return value


def _phases(reports: Any, selected: list[str]) -> dict[str, list[dict[str, Any]]]:
    _require(isinstance(reports, list), "Missing phase reports")
    grouped: dict[str, list[dict[str, Any]]] = {node: [] for node in selected}
    for report in reports:
        _require(isinstance(report, dict), "Invalid phase report")
        _require(report.get("nodeid") in grouped, "Foreign phase report")
        _require(report.get("outcome") in {"passed", "skipped"}, "Failed phase")
        _require(
            "wasxfail" in report and "reason" in report, "Missing outcome qualification"
        )
        if report["outcome"] == "skipped":
            _require(isinstance(report["reason"], str), "Missing skip/xfail reason")
        grouped[report["nodeid"]].append(report)
    for phases in grouped.values():
        names = [phase.get("when") for phase in phases]
        _require(
            names in [["setup", "call", "teardown"], ["setup", "teardown"]],
            "Incomplete phases",
        )
        _require(
            (phases[0]["outcome"] == "passed") == ("call" in names),
            "Missing call phase",
        )
    return grouped


def _xml_cases(path: Path) -> dict[str, ET.Element]:
    cases = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        ids = [
            p.get("value")
            for p in case.findall("./properties/property")
            if p.get("name") == PROPERTY
        ]
        _require(
            len(ids) == 1 and ids[0] is not None, "JUnit lacks unique node identity"
        )
        node = ids[0]
        _require(node not in cases, "Duplicate JUnit node identity")
        cases[node] = case
    return cases


def _junit(path: Path, phases: dict[str, list[dict[str, Any]]]) -> None:
    cases = _xml_cases(path)
    _require(set(cases) == set(phases), "JUnit coverage differs from selected tests")
    for node, case in cases.items():
        _require(
            case.find("failure") is None and case.find("error") is None, "JUnit failure"
        )
        skipped = any(p["outcome"] == "skipped" for p in phases[node])
        _require(
            (case.find("skipped") is not None) == skipped, "JUnit skip/report mismatch"
        )


def _load(directory: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    inventory_path, receipt_path = (
        directory / "inventory.json",
        directory / "receipt.json",
    )
    inventory = json.loads(inventory_path.read_text())
    receipt = json.loads(receipt_path.read_text())
    _require(
        inventory.get("schema") == SCHEMA and receipt.get("schema") == SCHEMA,
        "Unknown receipt schema",
    )
    _require(
        receipt.get("inventory_sha256") == digest(inventory_path), "Changed inventory"
    )
    _require(
        receipt.get("complete") is True and receipt.get("exitstatus") == 0,
        "Incomplete/nonzero shard",
    )
    _require(
        receipt.get("junit_sha256") == digest(directory / "junit.xml"), "Changed JUnit"
    )
    return inventory, receipt


def _coverage(inventory: dict[str, Any], receipt: dict[str, Any]) -> list[str]:
    full = _unique(inventory.get("inventory"), "inventory")
    chosen = _unique(inventory.get("selected"), "selection")
    _require(bool(full) and bool(chosen), "Empty shard is a configuration error")
    expected = [
        n for n in full if assignment(n, inventory["count"]) == inventory["index"]
    ]
    _require(chosen == expected, "Wrong deterministic selection")
    _require(
        _unique(receipt.get("started"), "starts") == chosen, "Unstarted/reordered test"
    )
    _require(receipt.get("problems") == [], "Shard source/origin/report problem")
    return chosen


def _pytest_config(value: Any) -> dict[str, str]:
    _require(
        isinstance(value, dict)
        and set(value) == {"rootpath", "inipath", "sha256"}
        and all(isinstance(v, str) and v for v in value.values()),
        "Missing/malformed pytest configuration",
    )
    _require(
        Path(value["rootpath"]).is_absolute()
        and Path(value["inipath"]).is_absolute()
        and Path(value["inipath"]).parent == Path(value["rootpath"])
        and len(value["sha256"]) == 64
        and set(value["sha256"]) <= set("0123456789abcdef"),
        "Malformed pytest configuration",
    )
    return value


def validate(
    root: Path,
    *,
    count: int,
    python: str,
    run_id: str,
    run_attempt: str,
    source: dict[str, Any],
) -> dict[str, Any]:
    _require(count > 0, "Invalid shard count")
    directories = sorted(p.parent for p in root.glob("*/receipt.json"))
    _require(len(directories) == count, "Missing or duplicate shard artifacts")
    inventories, configurations, union, seen = [], [], [], set()
    for directory in directories:
        inventory, receipt = _load(directory)
        identity = (
            inventory.get("python"),
            inventory.get("run_id"),
            inventory.get("run_attempt"),
            inventory.get("count"),
        )
        _require(
            identity == (python, run_id, run_attempt, count),
            "Wrong version/run/attempt/count",
        )
        index = inventory.get("index")
        _require(
            type(index) is int and 0 <= index < count and index not in seen,
            "Invalid/duplicate shard index",
        )
        seen.add(index)
        _require(
            inventory.get("source") == source == receipt.get("source_after"),
            "Source drift",
        )
        configurations.append(_pytest_config(inventory.get("pytest_config")))
        chosen = _coverage(inventory, receipt)
        phases = _phases(receipt.get("reports"), chosen)
        _junit(directory / "junit.xml", phases)
        inventories.append(inventory["inventory"])
        union.extend(chosen)
    _require(
        all(config == configurations[0] for config in configurations),
        "Pytest configuration drift",
    )
    _require(
        all(ids == inventories[0] for ids in inventories), "Full inventory/order drift"
    )
    _require(
        len(union) == len(set(union)) and set(union) == set(inventories[0]),
        "Incomplete/duplicate union",
    )
    return {
        "python": python,
        "shards": count,
        "tests": len(union),
        "source": source,
        "complete": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    try:
        result = validate(
            args.artifacts,
            count=args.count,
            python=args.python_version,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            source=source_identity(Path.cwd(), args.source_sha),
        )
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        ET.ParseError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"CI coverage rejected: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

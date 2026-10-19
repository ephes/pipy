"""Verification-only pinned Pi bootstrap and strict trace oracle (DE01a).

Exit 0 verifies a reference fixture, never Python parity. Exit 1 is a valid
trace mismatch; exit 2 is missing/invalid evidence or a failed prerequisite.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import shutil
import signal
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests/fixtures/durable"
DRIVER = ROOT / "scripts/durable_pi_reference.mts"
PIN = "2ec55d808d455b1a64a2bf7888198207a096120a"
TREE = "b51be844ffaf57e96d06a58d3f45ee90c6ba7401"
RESOLVER = "packages/coding-agent/src/experimental/source-resolver.ts"
SAFE_INTEGER = 2**53 - 1
# npm 11.20 copies these four already-extraneous pinned lock records into
# its hidden lock without installing them. They are provenance, never deps.
ABSENT_EXTRANEOUS = frozenset(
    {
        "packages/agent/node_modules/@earendil-works/pi-ai/node_modules/typebox",
        "packages/coding-agent/node_modules/@earendil-works/pi-agent-core/node_modules/typebox",
        "packages/coding-agent/node_modules/@earendil-works/pi-ai/node_modules/@anthropic-ai/sdk",
        "packages/coding-agent/node_modules/@earendil-works/pi-ai/node_modules/typebox",
    }
)


# Explicit sibling loading also works for tests that load this CLI by file path.
_contract_spec = importlib.util.spec_from_file_location(
    "durable_reference_contract",
    Path(__file__).with_name("durable_reference_contract.py"),
)
assert _contract_spec is not None and _contract_spec.loader is not None
contract = importlib.util.module_from_spec(_contract_spec)
_contract_spec.loader.exec_module(contract)
EvidenceError = contract.EvidenceError


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(_: str) -> Any:
    raise EvidenceError("Non-finite JSON constant")


def finite(value: Any) -> None:
    """Reject values without an unambiguous, finite cross-language JSON form."""
    if value is None or type(value) is bool:
        return
    if type(value) is str:
        value.encode("utf-8", errors="strict")
        return
    if type(value) is int:
        if abs(value) > SAFE_INTEGER:
            raise EvidenceError("JSON integer exceeds interoperable safe range")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise EvidenceError("Non-finite JSON number")
        if value.is_integer() and abs(value) > SAFE_INTEGER:
            raise EvidenceError("JSON integer exceeds interoperable safe range")
        return
    _finite_container(value)


def _finite_container(value: Any) -> None:
    if type(value) is list:
        for child in value:
            finite(child)
    elif type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise EvidenceError("JSON object key is not a string")
            finite(key)
            finite(child)
    else:
        raise EvidenceError("Not a JSON value")


def load(path: Path) -> Any:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=_constant,
    )
    finite(value)
    return value


def dump(path: Path, value: Any) -> None:
    finite(value)
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
    )


def trace(value: Any) -> dict[str, Any]:
    finite(value)
    contract.validate_trace(value)
    if value["schema_version"] == 2:
        return value
    if type(value) is not dict or set(value) != {
        "schema_version",
        "case_id",
        "normalization",
        "steps",
    }:
        raise EvidenceError("Unknown trace envelope")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise EvidenceError("Unsupported trace schema version")
    if (
        value["case_id"] != "kernel-memory-v1"
        or value["normalization"] != "finite-json-v1"
    ):
        raise EvidenceError("Unsupported case or normalization")
    if type(value["steps"]) is not list or not value["steps"]:
        raise EvidenceError("Missing trace steps")
    for step in value["steps"]:
        if type(step) is not dict or set(step) != {"op", "value"}:
            raise EvidenceError("Unknown trace step")
        if type(step["op"]) is not str or not step["op"]:
            raise EvidenceError("Invalid trace operation")
    return value


def _canonical(value: Any) -> str:
    # Object-key order is immaterial; array order, absence/null, booleans and
    # every payload field remain significant. JSON numbers share JS semantics.
    if type(value) is float and value.is_integer():
        value = int(value)
    if type(value) is dict:
        return (
            "{"
            + ",".join(
                json.dumps(k) + ":" + _canonical(v) for k, v in sorted(value.items())
            )
            + "}"
        )
    if type(value) is list:
        return "[" + ",".join(_canonical(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def compare(expected: Any, actual: Any) -> bool:
    return _canonical(contract.project(trace(expected))[0]) == _canonical(
        contract.project(trace(actual))[0]
    )


def manifest(fixtures: Path = FIXTURES, driver: Path = DRIVER) -> dict[str, Any]:
    value = load(fixtures / "manifest.json")
    contract.registry(value, ROOT, fixtures, load, sha, driver)
    ref = contract.shape(
        value["reference"],
        {"commit", "tree", "package", "version", "source_sha256"},
        "reference",
    )
    if (ref["commit"], ref["tree"], ref["package"], ref["version"]) != (
        PIN,
        TREE,
        "@earendil-works/pi-durable",
        "1.3.0",
    ):
        raise EvidenceError("Reference identity drift")
    sources = ref["source_sha256"]
    if (
        type(sources) is not dict
        or not {"package-lock.json", "LICENSE", RESOLVER} <= sources.keys()
    ):
        raise EvidenceError("Missing pinned source hashes")
    for path, digest in sources.items():
        contract.safe_path(path)
        contract.digest(digest)
    runtime = contract.shape(value["runtime"], {"node", "npm"}, "runtime")
    if runtime != {"node": "v26.12.0", "npm": "11.20.0"}:
        raise EvidenceError("Runtime identity drift")
    for case in value["cases"]:
        for test in case["upstream_tests"]:
            if sources.get(test["path"]) != test["sha256"]:
                raise EvidenceError("Case source provenance drift")
        if not set(case["required_modules"]) <= sources.keys():
            raise EvidenceError("Case module provenance missing")
    return value


def child_env(output: Path) -> dict[str, str]:
    # Never propagate provider credentials, Node injection, user npm config or
    # Python startup hooks into the synthetic reference process.
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(output / "home"),
        "TMPDIR": str(output / "tmp"),
        "LANG": "C.UTF-8",
        "npm_config_cache": str(output / "npm-cache"),
        "npm_config_userconfig": str(output / "empty-npmrc"),
        "npm_config_globalconfig": str(output / "empty-global-npmrc"),
    }
    for name in ("home", "tmp", "npm-cache"):
        (output / name).mkdir(mode=0o700)
    for name in ("empty-npmrc", "empty-global-npmrc"):
        (output / name).touch(mode=0o600)
    return env


def run(
    command: list[str],
    cwd: Path,
    output: Path,
    label: str,
    env: dict[str, str],
    timeout: float = 120,
) -> bytes:
    """Retain full output and exit metadata; kill/reap the entire child group."""
    metadata: dict[str, Any] = {
        "command": command,
        "cwd": str(cwd),
        "timeout_s": timeout,
    }
    with (
        (output / f"{label}.stdout").open("wb") as stdout,
        (output / f"{label}.stderr").open("wb") as stderr,
    ):
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            metadata.update(exit_code=process.returncode, timed_out=True)
            dump(output / f"{label}.json", metadata)
            raise EvidenceError(f"Child deadline/interruption: {label}") from None
    metadata.update(exit_code=code, timed_out=False)
    dump(output / f"{label}.json", metadata)
    if code:
        raise EvidenceError(f"Child failed: {label} (exit {code}); see retained logs")
    return (output / f"{label}.stdout").read_bytes()


def verify_sources(snapshot: Path, expected: dict[str, str]) -> None:
    if any(sha(snapshot / path) != digest for path, digest in expected.items()):
        raise EvidenceError("Pinned source or lockfile drift")


def _locked_dependency(snapshot: Path, path: str, record: Any, original: Any) -> bool:
    if original is None:
        raise EvidenceError("Installed dependency outside lockfile")
    if type(record) is not dict or type(original) is not dict:
        raise EvidenceError("Invalid dependency lock record")
    for field in ("version", "integrity", "resolved", "link"):
        if record.get(field) != original.get(field):
            raise EvidenceError("Installed dependency lock drift")
    if record.get("link"):
        link = snapshot / path
        target = (snapshot / original["resolved"]).resolve()
        if (
            not link.is_symlink()
            or link.resolve() != target
            or not target.is_relative_to(snapshot)
        ):
            raise EvidenceError("Workspace dependency escaped snapshot or drifted")
        return True
    return False


def _lock_packages(path: Path) -> dict[str, Any]:
    lock = load(path)
    if type(lock) is not dict or type(lock.get("packages")) is not dict:
        raise EvidenceError("Invalid dependency lock packages object")
    return lock["packages"]


def installed_dependencies(snapshot: Path) -> dict[str, Any]:
    snapshot = snapshot.resolve()
    locked = _lock_packages(snapshot / "package-lock.json")
    installed = _lock_packages(snapshot / "node_modules/.package-lock.json")
    if not installed:
        raise EvidenceError("Installed dependency evidence missing")
    present = {}
    absent = {}
    for path, record in installed.items():
        if _locked_dependency(snapshot, path, record, locked.get(path)):
            present[path] = record
            continue
        original = locked[path]
        package_path = snapshot / path / "package.json"
        if not package_path.is_file() and path in ABSENT_EXTRANEOUS:
            if (
                original.get("extraneous") is not True
                or record.get("extraneous") is not True
            ):
                raise EvidenceError("Unpinned absent extraneous record")
            absent[path] = record
            continue
        package = load(package_path)
        if package["version"] != record["version"]:
            raise EvidenceError("Installed package version drift")
        present[path] = record
    return {"present": present, "absent_extraneous_lock_records": absent}


def module_dependencies(
    snapshot: Path, modules: dict[str, str], dependencies: Any
) -> None:
    for relative in modules:
        if "node_modules" not in Path(relative).parts:
            continue
        package_dir = (snapshot / relative).parent
        while package_dir != snapshot and not (package_dir / "package.json").is_file():
            package_dir = package_dir.parent
        key = str(package_dir.relative_to(snapshot))
        if key not in dependencies["present"]:
            raise EvidenceError("Resolved dependency has no installed lock evidence")


def _resolution_module(
    snapshot: Path, item: Any, originals: dict[str, str], adapters: dict[str, str]
) -> tuple[str, str]:
    if type(item) is not dict or set(item) != {"path", "specifier"}:
        raise EvidenceError("Invalid module-resolution record")
    relative, specifier = item["path"], item["specifier"]
    if (
        type(relative) is not str
        or type(specifier) is not str
        or not relative
        or not specifier
    ):
        raise EvidenceError("Invalid module-resolution path or specifier")
    parts = Path(relative).parts
    path = snapshot / relative
    if (
        Path(relative).is_absolute()
        or ".." in parts
        or not path.resolve().is_relative_to(snapshot)
        or not path.is_file()
    ):
        raise EvidenceError("Module-resolution escaped snapshot or missing")
    registry = "node_modules" in parts
    if specifier.startswith("@earendil-works/") and (
        registry or parts[0] != "packages"
    ):
        raise EvidenceError("Workspace module did not resolve to pinned source")
    digest = sha(path)
    if (
        not registry
        and originals.get(relative) != digest
        and adapters.get(relative) != digest
    ):
        raise EvidenceError("Resolved source is absent from pinned archive or changed")
    return relative, digest


def verify_resolutions(
    snapshot: Path,
    resolutions: Any,
    originals: dict[str, str],
    adapters: dict[str, str] | None = None,
    required: list[str] | None = None,
) -> dict[str, str]:
    snapshot = snapshot.resolve()
    if type(resolutions) is not list or not resolutions:
        raise EvidenceError("Missing module-resolution evidence")
    result = dict(
        _resolution_module(snapshot, item, originals, adapters or {})
        for item in resolutions
    )
    for anchor in (
        required
        if required is not None
        else (
            "packages/durable/src/session/session.ts",
            "packages/durable/src/storage/memory.ts",
            "packages/durable/src/documents.ts",
        )
    ):
        if anchor not in result:
            raise EvidenceError("Required kernel module not executed")
    return result


def reference(source: Path, output: Path, fixtures: Path = FIXTURES) -> int:
    manifest_sha256 = sha(fixtures / "manifest.json")
    config = manifest(fixtures)
    if not (source / ".git").exists():
        raise EvidenceError("Reference source must be a checkout root")
    output = output.resolve()
    if output.is_relative_to(source.resolve()):
        raise EvidenceError("Output must be outside upstream")
    # Exclusive creation prevents reuse of stale node_modules or evidence.
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    output.chmod(0o700)
    env = child_env(output)
    snapshot = output / "snapshot"
    snapshot.mkdir(mode=0o700)

    def execute(cmd: list[str], label: str, timeout: float = 120) -> bytes:
        return run(cmd, snapshot, output, label, env, timeout)

    resolved = (
        execute(["git", "-C", str(source), "rev-parse", PIN + "^{commit}"], "ref")
        .decode()
        .strip()
    )
    tree = (
        execute(["git", "-C", str(source), "rev-parse", PIN + "^{tree}"], "tree")
        .decode()
        .strip()
    )
    if resolved != PIN or tree != TREE:
        raise EvidenceError("Git reference identity drift")
    for binary in ("node", "npm"):
        version = execute([binary, "--version"], binary + "-version").decode().strip()
        if version != config["runtime"][binary]:
            raise EvidenceError(f"Runtime version drift: {binary}")
    originals, dependencies, dependency_hashes = install_reference(
        source, snapshot, output, config, execute
    )
    adapters = copy_adapters(config, snapshot, originals)
    results = []
    for case in config["cases"]:
        result = reference_case(
            case,
            snapshot,
            output,
            fixtures,
            execute,
            originals,
            adapters,
            dependencies,
            dependency_hashes,
        )
        results.append(result)
    # Revalidate caller artifacts and fixtures after all children, not just at startup.
    manifest(fixtures)
    if sha(fixtures / "manifest.json") != manifest_sha256:
        raise EvidenceError("Manifest identity changed during reference execution")
    complete = [item["id"] for item in results] == [
        case["id"] for case in config["cases"]
    ]
    if not complete:
        raise EvidenceError("Incomplete required case execution")
    equal = all(item["equal"] for item in results)
    dump(
        output / "run.json",
        {
            "schema_version": 2,
            "status": "reference-verified" if equal else "mismatch",
            "complete": complete,
            "required_cases": [c["id"] for c in config["cases"]],
            "cases": results,
            "reference": config["reference"],
            "runtime": config["runtime"],
            "manifest_sha256": manifest_sha256,
            "comparison_sha256": config["comparison"],
            "adapters": config["adapters"],
            "archive_sha256": sha(output / "reference.tar"),
            "source_inventory_sha256": sha(output / "source-sha256.json"),
            "dependency_inventory_sha256": sha(output / "dependency-sha256.json"),
            "source_verification": "unchanged-after-install-and-each-case",
            "installed_dependencies": dependencies,
            "python_adapter": "OPEN",
            "parity_rows": "OPEN",
        },
    )
    return 0 if equal else 1


def copy_adapters(
    config: Any, snapshot: Path, originals: dict[str, str]
) -> dict[str, str]:
    adapters = {}
    for source_path, artifact in config["adapters"].items():
        destination = snapshot / artifact["destination"]
        if destination.exists() or artifact["destination"] in originals:
            raise EvidenceError("Adapter/archive path collision")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source_path, destination)
        adapters[artifact["destination"]] = artifact["sha256"]
    verify_sources(snapshot, adapters)
    return adapters


def install_reference(
    source: Path, snapshot: Path, output: Path, config: Any, execute: Any
) -> tuple[dict[str, str], Any, dict[str, str]]:
    archive = execute(
        ["git", "-C", str(source), "archive", "--format=tar", PIN], "archive"
    )
    (output / "reference.tar").write_bytes(archive)
    execute(["tar", "-xf", str(output / "reference.tar")], "extract")
    # Fingerprint every extracted file so installation cannot rewrite sources.
    originals = {
        str(p.relative_to(snapshot)): sha(p) for p in snapshot.rglob("*") if p.is_file()
    }
    dump(output / "source-sha256.json", originals)
    verify_sources(snapshot, config["reference"]["source_sha256"])
    package = load(snapshot / "packages/durable/package.json")
    if (package["name"], package["version"]) != ("@earendil-works/pi-durable", "1.3.0"):
        raise EvidenceError("Pinned package metadata drift")
    execute(
        ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
        "npm-ci",
        timeout=900,
    )
    verify_sources(snapshot, originals)
    dependencies = installed_dependencies(snapshot)
    dependency_hashes = {
        str(p.relative_to(snapshot)): sha(p)
        for p in snapshot.rglob("*")
        if "node_modules" in p.relative_to(snapshot).parts and p.is_file()
    }
    dump(output / "dependency-sha256.json", dependency_hashes)
    return originals, dependencies, dependency_hashes


def database_evidence(case_output: Path, value: Any) -> dict[str, str]:
    """Check the four closed synthetic databases against the bytes on disk."""
    rows = contract.array(value, "database evidence")
    expected_ids = {f"{a}-{b}" for a, b in contract.POLICIES}
    by_id = {}
    for row in rows:
        contract.shape(row, {"id", "path", "bytes", "sha256", "closed"}, "database")
        name = row["id"]
        if type(name) is not str or name not in expected_ids or name in by_id:
            raise EvidenceError("Unknown or duplicate database variant")
        by_id[name] = row
    if set(by_id) != expected_ids:
        raise EvidenceError("Incomplete database evidence")
    return {
        f"{name}/session.sqlite": database_file(case_output, name, row)
        for name, row in by_id.items()
    }


def database_file(case_output: Path, name: str, row: Any) -> str:
    root = case_output.resolve()
    expected = root / name / "session.sqlite"
    if row["closed"] is not True or type(row["bytes"]) is not int or row["bytes"] < 0:
        raise EvidenceError("Invalid closed database size/state")
    contract.digest(row["sha256"])
    if type(row["path"]) is not str or row["path"] != str(expected):
        raise EvidenceError("Database evidence path drift")
    if expected.is_symlink() or expected.resolve() != expected:
        raise EvidenceError("Database evidence symlink/escape")
    # Do not follow a replaced leaf or block on a non-regular file.
    descriptor = os.open(expected, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise EvidenceError("Database evidence is not a regular file")
        content = file.read()
    digest = hashlib.sha256(content).hexdigest()
    if len(content) != row["bytes"] or digest != row["sha256"]:
        raise EvidenceError("Database evidence bytes/hash drift")
    return digest


def chunk_evidence(value: Any, trace_value: Any) -> None:
    sidecar = contract.shape(value, {"schema_version", "scenarios"}, "raw chunks")
    if type(sidecar["schema_version"]) is not int or sidecar["schema_version"] != 1:
        raise EvidenceError("Unsupported raw chunk schema")
    scenarios = contract.array(sidecar["scenarios"], "chunk scenarios")
    observed = contract.decode(trace_value["steps"][0]["value"])["scenarios"]
    expected = {row["id"]: row for row in observed}
    seen = set()
    for scenario in scenarios:
        contract.shape(scenario, {"id", "chunks"}, "chunk scenario")
        name = scenario["id"]
        if type(name) is not str or name not in expected or name in seen:
            raise EvidenceError("Unknown or duplicate chunk scenario")
        seen.add(name)
        streams = chunk_streams(scenario["chunks"])
        executions = [op for op in expected[name]["operations"] if op["op"] == "exec"]
        if len(executions) > 1:
            raise EvidenceError("Ambiguous selected exec observation")
        settled = (
            executions[0]["streams"] if executions else {"stdout": "", "stderr": ""}
        )
        if (
            any(type(text) is not str for text in settled.values())
            or streams != settled
        ):
            raise EvidenceError("Raw chunks disagree with settled streams")
    if seen != set(expected):
        raise EvidenceError("Incomplete raw chunk scenarios")


def chunk_streams(chunks: Any) -> dict[str, str]:
    streams = {"stdout": "", "stderr": ""}
    for chunk in contract.array(chunks, "raw chunks"):
        contract.shape(chunk, {"text", "info"}, "raw chunk")
        contract.shape(chunk["info"], {"stream"}, "chunk info")
        stream = chunk["info"]["stream"]
        if (
            type(stream) is not str
            or stream not in streams
            or type(chunk["text"]) is not str
        ):
            raise EvidenceError("Invalid raw chunk stream/text")
        streams[stream] += chunk["text"]
    return streams


def reference_case(
    case: dict[str, Any],
    snapshot: Path,
    output: Path,
    fixtures: Path,
    execute: Any,
    originals: dict[str, str],
    adapters: dict[str, str],
    dependencies: Any,
    dependency_hashes: dict[str, str],
) -> dict[str, Any]:
    case_output = output / case["id"]
    case_output.mkdir(mode=0o700)
    # Copy the hashed input: children never reopen a mutable caller fixture mid-run.
    input_path = case_output / "input.json"
    shutil.copyfile(fixtures / case["input"]["path"], input_path)
    if sha(input_path) != case["input"]["sha256"]:
        raise EvidenceError("Input changed during copy")
    execute(
        [
            "node",
            "--import",
            str(snapshot / RESOLVER),
            str(snapshot / case["adapter"]),
            case["id"],
            str(input_path),
            str(case_output),
        ],
        case["id"],
        timeout=60,
    )
    verify_sources(snapshot, originals)
    verify_sources(snapshot, adapters)
    resolutions = load(case_output / "resolutions.json")
    modules = verify_resolutions(
        snapshot, resolutions, originals, adapters, case["required_modules"]
    )
    module_dependencies(snapshot, modules, dependencies)
    for relative, digest in modules.items():
        if (
            "node_modules" in Path(relative).parts
            and dependency_hashes.get(relative) != digest
        ):
            raise EvidenceError("Resolved dependency changed after installation")
    actual = trace(load(case_output / "trace.json"))
    if actual["case_id"] != case["id"]:
        raise EvidenceError("Executed case identity drift")
    projected, witness = contract.project(actual)
    dump(case_output / "projected.json", projected)
    dump(case_output / "projection-witness.json", witness)
    expected = load(fixtures / case["expected"]["path"])
    expected_projection, expected_witness = contract.project(trace(expected))
    dump(case_output / "expected-projected.json", expected_projection)
    dump(case_output / "expected-projection-witness.json", expected_witness)
    equal = compare(expected, actual)
    auxiliary_files = {
        "tool-recovery-v1": ["database-evidence.json"],
        "env-conformance-v1": ["raw-chunks.json"],
    }.get(case["id"], [])
    auxiliary = {}
    databases = {}
    for name in auxiliary_files:
        artifact = case_output / name
        if artifact.is_symlink() or not artifact.is_file():
            raise EvidenceError("Missing raw auxiliary evidence")
        sidecar = load(artifact)
        if name == "database-evidence.json":
            databases = database_evidence(case_output, sidecar)
        else:
            chunk_evidence(sidecar, actual)
        auxiliary[name] = sha(artifact)
    result = {
        "id": case["id"],
        "equal": equal,
        "input_sha256": sha(input_path),
        "raw_sha256": sha(case_output / "trace.json"),
        "projected_sha256": sha(case_output / "projected.json"),
        "witness_sha256": sha(case_output / "projection-witness.json"),
        "expected_sha256": case["expected"]["sha256"],
        "auxiliary_sha256": auxiliary,
        "database_sha256": databases,
        "module_sha256": modules,
        "module_provenance": {
            path: "pinned-archive"
            if path in originals
            else "caller-adapter"
            if path in adapters
            else "locked-dependency"
            for path in modules
        },
    }
    dump(case_output / "result.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    probe = sub.add_parser("reference")
    probe.add_argument("--pi-source", type=Path, required=True)
    probe.add_argument("--output", type=Path, required=True)
    probe.add_argument("--fixtures", type=Path, default=FIXTURES)
    check = sub.add_parser("compare")
    check.add_argument("expected", type=Path)
    check.add_argument("actual", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "compare":
            equal = compare(load(args.expected), load(args.actual))
            print("TRACE EQUAL (no parity claim)" if equal else "TRACE MISMATCH")
            return 0 if equal else 1
        code = reference(args.pi_source.resolve(), args.output, args.fixtures)
        print(
            "REFERENCE VERIFIED (Python adapter OPEN)"
            if code == 0
            else "REFERENCE TRACE MISMATCH"
        )
        return code
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        # Only field/class diagnostics; payloads and private state never print.
        detail = (
            str(error) if isinstance(error, EvidenceError) else type(error).__name__
        )
        print(f"REFERENCE EVIDENCE INVALID: {detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

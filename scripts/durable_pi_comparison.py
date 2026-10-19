"""Verification-only pinned Pi bootstrap and strict trace oracle (DE01a).

Exit 0 verifies a reference fixture, never Python parity. Exit 1 is a valid
trace mismatch; exit 2 is missing/invalid evidence or a failed prerequisite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import signal
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


class EvidenceError(ValueError):
    """The run cannot supply valid reference evidence."""


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
    return _canonical(trace(expected)) == _canonical(trace(actual))


def _manifest_contract(value: Any) -> None:
    if (
        (
            value["case_id"],
            value["normalization"],
            value["python_adapter"],
            value["parity_rows"],
        )
        != ("kernel-memory-v1", "finite-json-v1", "OPEN", "OPEN")
        or type(value["trace_schema"]) is not int
        or value["trace_schema"] != 1
    ):
        raise EvidenceError("Unsupported manifest contract")
    ref = value["reference"]
    if type(ref) is not dict or set(ref) != {
        "commit",
        "tree",
        "package",
        "version",
        "source_sha256",
    }:
        raise EvidenceError("Unknown reference manifest schema")
    if ref["package"] != "@earendil-works/pi-durable":
        raise EvidenceError("Reference package drift")
    sources = ref["source_sha256"]
    if (
        type(sources) is not dict
        or "package-lock.json" not in sources
        or RESOLVER not in sources
    ):
        raise EvidenceError("Missing pinned source hashes")
    for path, digest in sources.items():
        if (
            Path(path).is_absolute()
            or ".." in Path(path).parts
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise EvidenceError("Invalid pinned source hash")
    if type(value["runtime"]) is not dict or set(value["runtime"]) != {"node", "npm"}:
        raise EvidenceError("Unknown runtime manifest schema")


def manifest(fixtures: Path = FIXTURES, driver: Path = DRIVER) -> dict[str, Any]:
    value = load(fixtures / "manifest.json")
    required = {
        "schema_version",
        "reference",
        "runtime",
        "case_id",
        "trace_schema",
        "normalization",
        "python_adapter",
        "parity_rows",
        "driver_sha256",
        "expected_trace_sha256",
    }
    if type(value) is not dict or set(value) != required:
        raise EvidenceError("Unknown manifest schema")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise EvidenceError("Unsupported manifest version")
    _manifest_contract(value)
    ref = value["reference"]
    if ref["commit"] != PIN or ref["tree"] != TREE or ref["version"] != "1.3.0":
        raise EvidenceError("Reference identity drift")
    if value["driver_sha256"] != sha(driver):
        raise EvidenceError("Driver hash drift")
    if value["expected_trace_sha256"] != sha(fixtures / "kernel-memory-v1.json"):
        raise EvidenceError("Fixture hash drift")
    trace(load(fixtures / "kernel-memory-v1.json"))
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
    snapshot: Path, item: Any, originals: dict[str, str]
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
    if not registry and originals.get(relative) != digest:
        raise EvidenceError("Resolved source is absent from pinned archive or changed")
    return relative, digest


def verify_resolutions(
    snapshot: Path, resolutions: Any, originals: dict[str, str]
) -> dict[str, str]:
    snapshot = snapshot.resolve()
    if type(resolutions) is not list or not resolutions:
        raise EvidenceError("Missing module-resolution evidence")
    result = dict(_resolution_module(snapshot, item, originals) for item in resolutions)
    for required in (
        "packages/durable/src/session/session.ts",
        "packages/durable/src/storage/memory.ts",
        "packages/durable/src/documents.ts",
    ):
        if required not in result:
            raise EvidenceError("Required kernel module not executed")
    return result


def reference(source: Path, output: Path, fixtures: Path = FIXTURES) -> int:
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
    shutil.copyfile(DRIVER, snapshot / "durable_pi_reference.mts")
    execute(
        [
            "node",
            "--import",
            str(snapshot / RESOLVER),
            str(snapshot / "durable_pi_reference.mts"),
        ],
        "kernel-memory",
        timeout=60,
    )
    verify_sources(snapshot, originals)
    resolutions = load(snapshot / "resolutions.json")
    modules = verify_resolutions(snapshot, resolutions, originals)
    module_dependencies(snapshot, modules, dependencies)
    actual = trace(load(snapshot / "trace.json"))
    dump(output / "trace.json", actual)
    dump(output / "resolutions.json", resolutions)
    equal = compare(load(fixtures / "kernel-memory-v1.json"), actual)
    dump(
        output / "run.json",
        {
            "schema_version": 1,
            "status": "reference-verified" if equal else "mismatch",
            "reference": config["reference"],
            "runtime": config["runtime"],
            "manifest_sha256": sha(fixtures / "manifest.json"),
            "driver_sha256": sha(DRIVER),
            "oracle_sha256": sha(Path(__file__)),
            "fixture_sha256": config["expected_trace_sha256"],
            "archive_sha256": sha(output / "reference.tar"),
            "source_inventory_sha256": sha(output / "source-sha256.json"),
            "source_verification": "unchanged-after-install-and-execution",
            "trace_sha256": sha(output / "trace.json"),
            "installed_dependencies": dependencies,
            "module_sha256": modules,
            "python_adapter": "OPEN",
            "parity_rows": "OPEN",
        },
    )
    return 0 if equal else 1


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

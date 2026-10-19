"""Closed textual evidence staging; never a reference runner or hosted pass claim."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import re
import stat
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location(
    "durable_artifact_oracle", Path(__file__).with_name("durable_pi_comparison.py")
)
assert _spec and _spec.loader
o = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(o)
EvidenceError = o.EvidenceError
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
WRAPPER_COMMANDS = (
    "source-init",
    "source-fetch",
    "source-ref",
    "source-tree",
    "node-download",
    "node-shasums",
    "node-extract",
    "node-version",
    "npm-version",
    "codec",
    "acceptance",
)


def require(ok: bool, message: str) -> None:
    if not ok:
        raise EvidenceError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def open_leaf(path: Path) -> int:
    """Pin each directory descriptor; an exchanged intermediate cannot redirect IO."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(path.anchor, directory_flags)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, directory_flags, dir_fd=parent)
            os.close(parent)
            parent = child
        return os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
    finally:
        os.close(parent)


def read(root: Path, name: str, *, text: bool = True) -> bytes:
    """Read only a bounded regular leaf, with no lexical/resolved aliases."""
    o.contract.safe_path(name)
    root = root.absolute()
    path = root / name
    require(root.resolve() == root and path.resolve() == path, "Unsafe artifact path")
    fd = open_leaf(path)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode), "Artifact is not a regular file")
        require(before.st_nlink == 1, "Artifact hardlink alias")
        require(before.st_size <= MAX_FILE, "Artifact exceeds byte limit")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(MAX_FILE + 1)
        after = os.fstat(fd)
        require(
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            and len(data) == before.st_size,
            "Artifact changed while reading",
        )
    finally:
        os.close(fd)
    if text:
        require(b"\0" not in data, "Binary artifact is not uploadable text")
        data.decode("utf-8", errors="strict")
    return data


def registered() -> dict[str, tuple[str, str]]:
    """Destination -> (controlled root, exact relative source), never a glob."""
    rows = {
        "provenance/manifest.json": ("fixtures", "manifest.json"),
        "provenance/coverage.json": ("fixtures", "coverage.json"),
        "acceptance/invocation.json": ("acceptance", "invocation.json"),
        "acceptance/acceptance.json": ("acceptance", "acceptance.json"),
    }
    reference = ["run.json", "source-sha256.json", "dependency-sha256.json"]
    for case in o.contract.CASES:
        reference.extend(
            f"{case}/{name}"
            for name in (
                "input.json",
                "trace.json",
                "projected.json",
                "projection-witness.json",
                "expected-projected.json",
                "expected-projection-witness.json",
                "resolutions.json",
                "result.json",
            )
        )
    reference += [
        "tool-recovery-v1/database-evidence.json",
        "env-conformance-v1/raw-chunks.json",
    ]
    for label in (
        "ref",
        "tree",
        "node-version",
        "npm-version",
        "npm-ci",
        "extract",
        *o.contract.CASES,
    ):
        reference.extend(label + suffix for suffix in (".json", ".stdout", ".stderr"))
    # archive.stdout is the binary archive, never a textual command log.
    reference += ["archive.json", "archive.stderr"]
    for name in reference:
        rows["acceptance/reference/" + name] = ("acceptance", "reference/" + name)
    for name in ("input.json", "workload.json", "resolutions.json"):
        path = o.acceptance.WORKLOAD + "/" + name
        rows["acceptance/" + path] = ("acceptance", path)
    for suffix in (".json", ".stdout", ".stderr"):
        path = o.acceptance.WORKLOAD + suffix
        rows["acceptance/" + path] = ("acceptance", path)
    for oracle in o.acceptance.ORACLES:
        for name in (
            "original.json",
            "mutant.json",
            "positive.json",
            "negative.json",
            "positive.stdout",
            "negative.stdout",
            "positive.stderr",
            "negative.stderr",
        ):
            path = oracle + "/" + name
            rows["acceptance/" + path] = ("acceptance", path)
    rows["job/job.json"] = ("job", "job.json")
    for label in WRAPPER_COMMANDS:
        for suffix in (".json", ".stdout", ".stderr"):
            name = label + suffix
            rows["job/" + name] = ("job", name)
    rows["job/node-shasums.txt"] = ("job", "node-shasums.txt")
    return rows


def identities(args: Any) -> dict[str, Any]:
    require(
        bool(re.fullmatch(r"[0-9a-f]{40}", args.pipy_commit)), "Invalid pipy commit"
    )
    require(
        bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository)),
        "Invalid repository",
    )
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]+", args.job_key)), "Invalid job key")
    require(
        args.run_id.isdecimal()
        and args.run_attempt.isdecimal()
        and int(args.run_attempt) > 0,
        "Invalid workflow identity",
    )
    return {
        "pipy_commit": args.pipy_commit,
        "repository": args.repository,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
        "job_key": args.job_key,
    }


def wrapper(root: Path, identity: dict[str, Any]) -> dict[str, Any]:
    job = o.load(root / "job.json")
    o.contract.shape(
        job,
        {
            "schema_version",
            "origin",
            "complete",
            "identity",
            "commands",
            "node_archive",
        },
        "job evidence",
    )
    require(
        type(job["schema_version"]) is int and job["schema_version"] == 1,
        "Unknown job evidence schema",
    )
    require(
        job["identity"] == identity and job["complete"] is True,
        "Incomplete/mismatched job evidence",
    )
    require(job["origin"] in ("local", "github-actions"), "Unknown execution origin")
    expected = (
        list(WRAPPER_COMMANDS)
        if job["origin"] == "github-actions"
        else ["codec", "acceptance"]
    )
    require(job["commands"] == expected, "Incomplete wrapper command sequence")
    for label in expected:
        command = o.load(root / (label + ".json"))
        require(
            type(command) is dict
            and type(command.get("exit_code")) is int
            and command["exit_code"] == 0
            and command.get("timed_out") is False,
            "Failed wrapper command",
        )
    archive = job["node_archive"]
    if job["origin"] == "github-actions":
        o.contract.shape(archive, {"bytes", "sha256"}, "Node archive")
        require(
            type(archive["bytes"]) is int
            and archive["bytes"] > 0
            and archive["sha256"]
            == "fd47865ecd6391ebb431fc815e01b9135bf83ded5cf373247e9331e196b7aded",
            "Node archive identity drift",
        )
    else:
        require(archive is None, "Local run cannot attest a hosted Node download")
    return job


def attest(output: Path, fixtures: Path) -> dict[str, Any]:
    """Validate ORIGINAL complete local evidence, including excluded DB/archive."""
    config = o.manifest(fixtures)
    summary = o.load(output / "acceptance.json")
    invocation = o.load(output / "invocation.json")
    require(
        type(summary) is dict and type(invocation) is dict,
        "Malformed acceptance evidence",
    )
    require(
        summary.get("schema_version") == 1
        and type(summary.get("schema_version")) is int
        and summary.get("status") == "acceptance-verified"
        and summary.get("complete") is True,
        "Incomplete acceptance",
    )
    require(
        summary.get("output_root") == str(output)
        and invocation.get("output_root") == str(output)
        and summary.get("execution_id") == invocation.get("execution_id")
        and summary.get("reference_execution_id")
        == invocation.get("reference_execution_id")
        and summary.get("reference_execution_id")
        == summary.get("execution_id", "") + ":reference",
        "Acceptance invocation identity drift",
    )
    require(
        summary.get("manifest_sha256")
        == o.sha(fixtures / "manifest.json")
        == invocation.get("manifest_sha256")
        and summary.get("comparison") == config["comparison"]
        and summary.get("verification") == config["verification"]
        and summary.get("coverage") == config["coverage"],
        "Acceptance tooling identity drift",
    )
    run = o.acceptance.attest_reference(o, output / "reference", config, fixtures)
    require(
        summary.get("reference_run_sha256") == o.sha(output / "reference/run.json")
        and summary.get("reference") == config["reference"]
        and summary.get("cases") == list(o.contract.CASES)
        and summary.get("submatrix") == {"h2_variants": 4, "e1_scenarios": 2},
        "Acceptance parent/matrix drift",
    )
    for case in config["cases"]:
        projected, witness = o.contract.project(
            o.load(fixtures / case["expected"]["path"])
        )
        folder = output / "reference" / case["id"]
        require(
            o.load(folder / "expected-projected.json") == projected
            and o.load(folder / "expected-projection-witness.json") == witness,
            "Expected projection evidence drift",
        )
    artifacts = summary.get("execution_artifacts_sha256")
    require(type(artifacts) is dict, "Malformed execution artifacts")
    expected = {"invocation.json", o.acceptance.WORKLOAD + ".json"}
    expected.update(
        o.acceptance.WORKLOAD + "/" + name
        for name in ("input.json", "workload.json", "resolutions.json")
    )
    for name in o.acceptance.ORACLES:
        expected.update(
            name + "/" + suffix
            for suffix in (
                "original.json",
                "mutant.json",
                "positive.json",
                "negative.json",
                "positive.stdout",
                "positive.stderr",
                "negative.stdout",
                "negative.stderr",
            )
        )
    require(set(artifacts) == expected, "Incomplete execution artifact inventory")
    for name, sha in artifacts.items():
        o.contract.digest(sha)
        require(digest(read(output, name)) == sha, "Execution artifact drift")
    attest_workload_modules(output, summary, config, run)
    validate_workload_oracles(output, summary)
    require(
        summary.get("python_adapter") == "OPEN"
        and summary.get("parity_rows") == "OPEN"
        and summary.get("reference_ci") == "OPEN until b3b",
        "Local acceptance cannot claim parity or hosted delivery",
    )
    return {
        "execution_id": summary["execution_id"],
        "manifest_sha256": summary["manifest_sha256"],
        "reference_run_sha256": summary["reference_run_sha256"],
        "reference_execution_id": summary["reference_execution_id"],
        "acceptance_sha256": o.sha(output / "acceptance.json"),
        "database_sha256": next(
            case["database_sha256"]
            for case in run["cases"]
            if case["id"] == "tool-recovery-v1"
        ),
    }


def attest_workload_modules(
    output: Path, summary: dict[str, Any], config: dict[str, Any], run: dict[str, Any]
) -> None:
    snapshot = output / "reference/snapshot"
    modules = o.verify_resolutions(
        snapshot,
        o.load(output / o.acceptance.WORKLOAD / "resolutions.json"),
        o.load(output / "reference/source-sha256.json"),
        {x["destination"]: x["sha256"] for x in config["adapters"].values()},
        o.acceptance.MODULES,
    )
    observed = summary.get("workload")
    require(
        type(observed) is dict and observed.get("modules") == modules,
        "Workload module evidence drift",
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    dependencies = o.load(output / "reference/dependency-sha256.json")
    for name, sha in modules.items():
        if "node_modules" in Path(name).parts:
            require(dependencies.get(name) == sha, "Workload dependency drift")


def validate_workload_oracles(output: Path, summary: dict[str, Any]) -> None:
    work = o.acceptance.WORKLOAD
    data = o.acceptance.workload(o.load(output / work / "workload.json"), o.contract)
    observed = summary.get("workload")
    require(
        type(observed) is dict
        and observed.get("hardware") == data["hardware"]
        and observed.get("id") == work
        and observed.get("raw_sha256") == o.sha(output / work / "workload.json")
        and observed.get("input_sha256") == o.sha(output / work / "input.json")
        and [
            observed.get(key)
            for key in (
                "read_correctness",
                "measured_reads",
                "write_correctness",
                "measured_writes",
            )
        ]
        == [12, 120, 3, 9],
        "Workload attestation drift",
    )
    oracles: Any = summary.get("oracles")
    require(
        type(oracles) is list
        and len(oracles) == 4
        and all(type(x) is dict for x in oracles)
        and [x.get("id") for x in oracles] == list(o.acceptance.ORACLES),
        "Incomplete oracle evidence",
    )
    for row in oracles:
        folder = output / row["id"]
        raw = o.load(folder / "original.json")
        mutant = o.load(folder / "mutant.json")
        require(
            row.get("schema_valid") is True
            and type(row.get("positive_exit")) is int
            and row["positive_exit"] == 0
            and type(row.get("negative_exit")) is int
            and row["negative_exit"] == 1,
            "Invalid oracle classification",
        )
        require(
            row.get("original_sha256") == o.sha(folder / "original.json")
            and row.get("mutant_sha256") == o.sha(folder / "mutant.json")
            and o.compare(raw, raw)
            and not o.compare(raw, mutant)
            and raw
            == o.load(
                output / "reference" / o.acceptance.ORACLES[row["id"]] / "trace.json"
            )
            and mutant == o.acceptance.mutate(row["id"], raw, o.contract),
            "Oracle byte/semantic drift",
        )
        for label, wanted in (("positive", 0), ("negative", 1)):
            meta = o.load(folder / (label + ".json"))
            require(type(meta) is dict, "Malformed oracle command metadata")
            require(
                type(meta.get("exit_code")) is int
                and meta["exit_code"] == wanted
                and meta.get("timed_out") is False,
                "Oracle command classification drift",
            )


def exclusions(output: Path, job: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    case = output / "reference/tool-recovery-v1"
    sidecar = o.load(case / "database-evidence.json")
    require(
        o.database_evidence(case, sidecar)
        == o.load(case / "result.json")["database_sha256"],
        "Excluded database attestation drift",
    )
    for record in sidecar:
        rows.append(
            {
                "path": "acceptance/reference/tool-recovery-v1/"
                + record["id"]
                + "/session.sqlite",
                "bytes": record["bytes"],
                "sha256": record["sha256"],
                "uploaded": False,
                "reason": "SQLite binary; validated by case result.database_sha256",
            }
        )
    for name in ("reference.tar", "archive.stdout"):
        data = read(output, "reference/" + name, text=False)
        rows.append(
            {
                "path": "acceptance/reference/" + name,
                "bytes": len(data),
                "sha256": digest(data),
                "uploaded": False,
                "reason": "binary git archive",
            }
        )
    if job["node_archive"] is not None:
        rows.append(
            {
                "path": "runtime/node-v26.12.0-linux-x64.tar.xz",
                **job["node_archive"],
                "uploaded": False,
                "reason": "runtime archive",
            }
        )
    return rows


def gather(
    roots: dict[str, Path], names: dict[str, tuple[str, str]], required: set[str]
) -> tuple[dict[str, bytes], list[str], dict[str, str]]:
    captured = {}
    missing = []
    invalid = {}
    total = 0
    for target, (role, name) in names.items():
        try:
            data = read(roots[role], name)
        except FileNotFoundError:
            if target in required:
                missing.append(target)
        except (OSError, ValueError) as error:
            invalid[target] = type(error).__name__
        else:
            if total + len(data) > MAX_TOTAL:
                invalid[target] = "AggregateByteLimit"
            else:
                captured[target] = data
                total += len(data)
    return captured, missing, invalid


def stage(args: Any) -> tuple[dict[str, Any], int]:
    identity = identities(args)
    output = args.output.absolute()
    require(
        output.resolve() == output and not output.exists(),
        "Staging output must be new and unaliased",
    )
    roots = {
        "acceptance": args.acceptance.absolute(),
        "job": args.job_evidence.absolute(),
        "fixtures": o.FIXTURES,
    }
    require(
        not any(
            output.is_relative_to(root) or root.is_relative_to(output)
            for root in roots.values()
        ),
        "Staging overlaps source evidence",
    )
    output.mkdir(mode=0o700, parents=False)
    index: dict[str, Any] = {
        "schema_version": 1,
        "status": "failed-partial",
        "complete": False,
        "identity": identity,
        "attestation": None,
        "files": [],
        "excluded": [],
        "missing": [],
        "invalid": {},
        "error": None,
    }
    try:
        names = registered()
        captured, missing, invalid = gather(roots, names, set(names))
        index.update(missing=missing, invalid=invalid)
        require(not invalid, "Invalid artifact evidence roles")
        job = wrapper(roots["job"], identity)
        optional = {
            "job/" + label + suffix
            for label in WRAPPER_COMMANDS
            if label not in job["commands"]
            for suffix in (".json", ".stdout", ".stderr")
        }
        if job["origin"] == "local":
            optional.add("job/node-shasums.txt")
        require(not set(missing) - optional, "Missing required artifact evidence")
        index["attestation"] = attest(roots["acceptance"], roots["fixtures"])
        index["excluded"] = exclusions(roots["acceptance"], job)
        index["origin"] = job["origin"]
        verify_capture(roots, names, captured)
        index.update(status="staged-verified", complete=True, missing=[])
        code = 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RecursionError,
        AttributeError,
    ) as error:
        # Nested helper maps can raise AttributeError on malformed JSON shapes.
        # Proven by actual CLI auxiliary-list/source-inventory-null regressions.
        # Fail closed. Only independently safe wrapper logs may survive invalid input.
        index.update(
            status="failed-partial",
            complete=False,
            attestation=None,
            excluded=[],
            files=[],
            error=type(error).__name__,
        )
        failure = {k: v for k, v in registered().items() if v[0] == "job"}
        safe, _, invalid = gather(roots, failure, set())
        index["invalid"].update(invalid)
        names, captured = failure, safe
        code = 2
    try:
        write_captured(output, roots, names, captured, index)
    except (OSError, ValueError, KeyError, TypeError) as error:
        # The output is exclusive/private; remove only our exact attempted leaves.
        for target in captured:
            (output / target).unlink(missing_ok=True)
        index.update(
            status="failed-partial",
            complete=False,
            attestation=None,
            excluded=[],
            files=[],
            error=type(error).__name__,
        )
        code = 2
    o.dump(output / "index.json", index)
    (output / "index.json").chmod(0o600)
    return index, code


def verify_capture(
    roots: dict[str, Path],
    names: dict[str, tuple[str, str]],
    captured: dict[str, bytes],
) -> None:
    allowed = registered()
    require(
        set(captured) <= names.keys() <= allowed.keys(), "Unknown artifact selection"
    )
    for target, data in captured.items():
        require(names[target] == allowed[target], "Artifact role/alias drift")
        role, name = names[target]
        require(read(roots[role], name) == data, "Artifact changed before staging")


def write_captured(
    output: Path,
    roots: dict[str, Path],
    names: dict[str, tuple[str, str]],
    captured: dict[str, bytes],
    index: dict[str, Any],
) -> None:
    verify_capture(roots, names, captured)
    for target, data in captured.items():
        role, name = names[target]
        path = output / target
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
        path.chmod(0o600)
        require(path.read_bytes() == data, "Staged artifact byte drift")
        index["files"].append(
            {
                "path": target,
                "role": role,
                "source": name,
                "source_bytes": len(data),
                "source_sha256": digest(data),
                "staged_bytes": len(data),
                "staged_sha256": digest(data),
                "uploaded": True,
            }
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("acceptance", "job-evidence", "output"):
        parser.add_argument("--" + flag, required=True, type=Path)
    for flag in ("pipy-commit", "repository", "run-id", "run-attempt", "job-key"):
        parser.add_argument("--" + flag, required=True)
    args = parser.parse_args(argv)
    try:
        _, code = stage(args)
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        print("ARTIFACT EVIDENCE INVALID: " + type(error).__name__)
        return 2
    print("ARTIFACT STAGING " + ("VERIFIED" if code == 0 else "FAILED/PARTIAL"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

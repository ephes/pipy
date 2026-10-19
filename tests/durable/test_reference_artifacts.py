"""Host-independent collector boundaries; mocked attestation is not a runtime pass."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/durable_reference_artifacts.py"
spec = importlib.util.spec_from_file_location("artifact_tests", SCRIPT)
assert spec and spec.loader
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


def args(tmp_path):
    return SimpleNamespace(
        acceptance=tmp_path / "acceptance",
        job_evidence=tmp_path / "job",
        output=tmp_path / "staged",
        pipy_commit="a" * 40,
        repository="ephes/pipy",
        run_id="123",
        run_attempt="1",
        job_key="durable-reference",
    )


def synthetic(tmp_path, monkeypatch):
    """Exercise real selection/read/copy/index, isolate only deep runtime attestation."""
    value = args(tmp_path)
    fixtures = tmp_path / "fixtures"
    roots = {
        "acceptance": value.acceptance,
        "job": value.job_evidence,
        "fixtures": fixtures,
    }
    for _, (role, name) in s.registered().items():
        path = roots[role] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"synthetic":true}\n' if name.endswith(".json") else "synthetic\n"
        )
    identity = s.identities(value)
    s.o.dump(
        value.job_evidence / "job.json",
        {
            "schema_version": 1,
            "origin": "local",
            "complete": True,
            "identity": identity,
            "commands": ["codec", "acceptance"],
            "node_archive": None,
        },
    )
    for label in ("codec", "acceptance"):
        s.o.dump(
            value.job_evidence / (label + ".json"), {"exit_code": 0, "timed_out": False}
        )
    monkeypatch.setattr(s.o, "FIXTURES", fixtures)
    monkeypatch.setattr(s, "attest", lambda *a: {"execution_id": "synthetic-only"})
    monkeypatch.setattr(
        s, "exclusions", lambda *a: [{"path": "synthetic.sqlite", "uploaded": False}]
    )
    return value, roots


def test_closed_success_preserves_every_selected_byte_and_excludes_unknowns(
    tmp_path, monkeypatch
):
    value, roots = synthetic(tmp_path, monkeypatch)
    (value.acceptance / "unknown.bin").write_bytes(b"\0private")
    index, code = s.stage(value)
    assert code == 0 and index["complete"] and index["status"] == "staged-verified"
    assert len(index["files"]) == len(s.registered())
    assert index["identity"] == s.identities(value)
    assert not (value.output / "unknown.bin").exists()
    assert {row["path"] for row in index["files"]} == set(s.registered())
    for row in index["files"]:
        content = (roots[row["role"]] / row["source"]).read_bytes()
        assert (value.output / row["path"]).read_bytes() == content
        assert row["source_bytes"] == row["staged_bytes"] == len(content)
        assert row["source_sha256"] == row["staged_sha256"] == s.digest(content)
        assert row["uploaded"] is True
    assert value.output.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "kind",
    ["symlink", "parent-symlink", "directory", "fifo", "binary", "missing", "drift"],
)
def test_invalid_evidence_returns2_and_only_safe_job_logs_survive(
    tmp_path, monkeypatch, kind
):
    value, roots = synthetic(tmp_path, monkeypatch)
    path = value.acceptance / "invocation.json"
    path.unlink()
    if kind == "symlink":
        outside = tmp_path / "outside"
        outside.write_text("private")
        path.symlink_to(outside)
    elif kind == "parent-symlink":
        moved = tmp_path / "moved"
        value.acceptance.rename(moved)
        value.acceptance.symlink_to(moved, target_is_directory=True)
    elif kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "binary":
        path.write_bytes(b"\0private")
    elif kind == "drift":
        path.write_text("before")

        def drift(*unused):
            path.write_text("after")
            return {}

        monkeypatch.setattr(s, "attest", drift)
    index, code = s.stage(value)
    assert code == 2 and index["status"] == "failed-partial" and not index["complete"]
    assert index["attestation"] is None
    assert all(row["role"] == "job" for row in index["files"])
    assert not (value.output / "acceptance/invocation.json").exists()


@pytest.mark.parametrize(
    "name", ["../secret", "/absolute", "a/../secret", "./a", "a//b"]
)
def test_read_rejects_aliases_and_traversal(tmp_path, name):
    with pytest.raises(s.EvidenceError):
        s.read(tmp_path, name)


def test_failure_before_any_output_does_not_require_manifest(tmp_path, monkeypatch):
    value = args(tmp_path)
    value.job_evidence.mkdir()
    (value.job_evidence / "codec.stderr").write_text("controlled failure\n")
    # Missing command.json is honest partial evidence (e.g. Popen failure).
    monkeypatch.setattr(
        s.o, "manifest", lambda *a: pytest.fail("must not load manifest")
    )
    index, code = s.stage(value)
    assert code == 2 and not index["complete"] and index["attestation"] is None
    assert index["missing"] and "acceptance/acceptance.json" in index["missing"]
    assert [row["path"] for row in index["files"]] == ["job/codec.stderr"]
    assert (value.output / "index.json").is_file()


@pytest.mark.parametrize(
    "alter",
    [
        lambda v: v.update(complete=False),
        lambda v: v.update(origin="other"),
        lambda v: v["commands"].append("unexpected"),
        lambda v: v["identity"].update(run_id="999"),
    ],
)
def test_wrapper_invalid_cannot_turn_green(tmp_path, monkeypatch, alter):
    value, roots = synthetic(tmp_path, monkeypatch)
    job = s.o.load(value.job_evidence / "job.json")
    alter(job)
    s.o.dump(value.job_evidence / "job.json", job)
    index, code = s.stage(value)
    assert code == 2 and not index["complete"]


def test_original_failed_exit_is_preserved(tmp_path, monkeypatch):
    value, roots = synthetic(tmp_path, monkeypatch)
    path = value.job_evidence / "acceptance.json"
    s.o.dump(path, {"exit_code": 2, "timed_out": False})
    index, code = s.stage(value)
    assert code == 2
    assert s.o.load(value.output / "job/acceptance.json")["exit_code"] == 2


def test_unknown_requested_role_and_duplicate_alias_are_rejected(tmp_path):
    with pytest.raises(s.EvidenceError, match="Unknown artifact selection"):
        s.verify_capture(
            {}, {"unknown.bin": ("job", "codec.stdout")}, {"unknown.bin": b""}
        )
    with pytest.raises(s.EvidenceError, match="role/alias"):
        s.verify_capture(
            {},
            {"job/codec.stdout": ("job", "acceptance.stdout")},
            {"job/codec.stdout": b""},
        )


def test_artifact_size_and_utf8_are_bounded(tmp_path, monkeypatch):
    path = tmp_path / "log"
    path.write_bytes(b"ab")
    monkeypatch.setattr(s, "MAX_FILE", 1)
    with pytest.raises(s.EvidenceError, match="byte limit"):
        s.read(tmp_path, "log")
    path.write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        s.read(tmp_path, "log")


def test_staging_must_be_exclusive_and_outside_inputs(tmp_path):
    value = args(tmp_path)
    value.output = value.acceptance / "staged"
    with pytest.raises(s.EvidenceError, match="overlaps"):
        s.stage(value)
    value.output = tmp_path
    with pytest.raises(s.EvidenceError, match="new"):
        s.stage(value)


def complete_bundle(tmp_path, monkeypatch):
    """Real manifest/schema/projection/oracle checks; only archive runtime is mocked."""
    helper_spec = importlib.util.spec_from_file_location(
        "workload_report",
        SCRIPT.parents[1] / "tests/durable/test_pi_comparison_acceptance.py",
    )
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    value = args(tmp_path)
    value.acceptance.mkdir()
    output = value.acceptance
    config = s.o.manifest()
    invocation = {
        "output_root": str(output),
        "execution_id": "synthetic",
        "reference_execution_id": "synthetic:reference",
        "manifest_sha256": s.o.sha(s.o.FIXTURES / "manifest.json"),
    }
    s.o.dump(output / "invocation.json", invocation)
    for case in config["cases"]:
        folder = output / "reference" / case["id"]
        folder.mkdir(parents=True)
        raw = s.o.load(s.o.FIXTURES / case["expected"]["path"])
        projected, witness = s.o.contract.project(raw)
        for name, content in [
            ("trace.json", raw),
            ("expected-projected.json", projected),
            ("expected-projection-witness.json", witness),
        ]:
            s.o.dump(folder / name, content)
    s.o.dump(output / "reference/run.json", {"synthetic": True})
    work = output / s.o.acceptance.WORKLOAD
    work.mkdir()
    s.o.dump(work / "input.json", s.o.acceptance.INPUT)
    s.o.dump(work / "workload.json", helper.report())
    s.o.dump(work / "resolutions.json", [])
    s.o.dump(output / (s.o.acceptance.WORKLOAD + ".json"), {})
    artifacts = {
        str(path.relative_to(output)): s.o.sha(path)
        for path in (
            output / "invocation.json",
            output / (s.o.acceptance.WORKLOAD + ".json"),
            work / "input.json",
            work / "workload.json",
            work / "resolutions.json",
        )
    }
    oracles = []
    for name, case in s.o.acceptance.ORACLES.items():
        folder = output / name
        folder.mkdir()
        raw = s.o.load(output / "reference" / case / "trace.json")
        s.o.dump(folder / "original.json", raw)
        s.o.dump(folder / "mutant.json", s.o.acceptance.mutate(name, raw, s.o.contract))
        for label, code in (("positive", 0), ("negative", 1)):
            s.o.dump(
                folder / (label + ".json"), {"exit_code": code, "timed_out": False}
            )
            (folder / (label + ".stdout")).write_text("synthetic\n")
            (folder / (label + ".stderr")).write_text("")
        artifacts.update(
            {str(p.relative_to(output)): s.o.sha(p) for p in folder.iterdir()}
        )
        oracles.append(
            {
                "id": name,
                "schema_valid": True,
                "positive_exit": 0,
                "negative_exit": 1,
                "original_sha256": s.o.sha(folder / "original.json"),
                "mutant_sha256": s.o.sha(folder / "mutant.json"),
            }
        )
    raw = s.o.contract.decode(s.o.load(work / "workload.json")["value"])
    summary = {
        "schema_version": 1,
        "status": "acceptance-verified",
        "complete": True,
        **invocation,
        "reference_execution_id": "synthetic:reference",
        "reference_run_sha256": s.o.sha(output / "reference/run.json"),
        "reference": config["reference"],
        "comparison": config["comparison"],
        "verification": config["verification"],
        "coverage": config["coverage"],
        "cases": list(s.o.contract.CASES),
        "submatrix": {"h2_variants": 4, "e1_scenarios": 2},
        "execution_artifacts_sha256": artifacts,
        "workload": {
            "id": work.name,
            "hardware": raw["hardware"],
            "raw_sha256": s.o.sha(work / "workload.json"),
            "input_sha256": s.o.sha(work / "input.json"),
            "read_correctness": 12,
            "measured_reads": 120,
            "write_correctness": 3,
            "measured_writes": 9,
        },
        "oracles": oracles,
        "python_adapter": "OPEN",
        "parity_rows": "OPEN",
        "reference_ci": "OPEN until b3b",
    }
    s.o.dump(output / "acceptance.json", summary)
    monkeypatch.setattr(
        s.o.acceptance,
        "attest_reference",
        lambda *a: {"cases": [{"id": "tool-recovery-v1", "database_sha256": {}}]},
    )
    monkeypatch.setattr(s, "attest_workload_modules", lambda *a: None)
    return value, summary


@pytest.mark.parametrize(
    "name", ["expected-projected.json", "expected-projection-witness.json"]
)
def test_expected_projection_pair_is_actually_checked(tmp_path, monkeypatch, name):
    value, summary = complete_bundle(tmp_path, monkeypatch)
    assert s.attest(value.acceptance, s.o.FIXTURES)["execution_id"] == "synthetic"
    path = value.acceptance / "reference/kernel-memory-v1" / name
    s.o.dump(path, {"wrong": True})
    with pytest.raises(s.EvidenceError, match="Expected projection evidence drift"):
        s.attest(value.acceptance, s.o.FIXTURES)


@pytest.mark.parametrize(
    "failure",
    [
        "cases",
        "matrix",
        "artifacts",
        "oracle-invalid2",
        "oracle-missing",
        "workload-count",
        "manifest",
    ],
)
def test_deep_acceptance_guard_is_not_just_a_declared_success(
    tmp_path, monkeypatch, failure
):
    value, summary = complete_bundle(tmp_path, monkeypatch)
    assert s.attest(value.acceptance, s.o.FIXTURES)
    if failure == "cases":
        summary["cases"].pop()
    elif failure == "matrix":
        summary["submatrix"]["h2_variants"] = 3
    elif failure == "artifacts":
        summary["execution_artifacts_sha256"].pop("invocation.json")
    elif failure == "oracle-invalid2":
        summary["oracles"][0]["negative_exit"] = 2
    elif failure == "oracle-missing":
        summary["oracles"].pop()
    elif failure == "workload-count":
        summary["workload"]["measured_reads"] = 119
    else:
        summary["manifest_sha256"] = "0" * 64
    s.o.dump(value.acceptance / "acceptance.json", summary)
    with pytest.raises(s.EvidenceError):
        s.attest(value.acceptance, s.o.FIXTURES)


@pytest.mark.parametrize(
    "failure", ["absent", "partial-popen", "malformed-json", "unsafe-log"]
)
def test_actual_collector_cli2_early_failure_retains_safe_index(tmp_path, failure):
    import subprocess
    import sys

    value = args(tmp_path)
    value.job_evidence.mkdir()
    if failure == "partial-popen":
        (value.job_evidence / "codec.stdout").write_text("")
        (value.job_evidence / "codec.stderr").write_text("controlled Popen failure")
    elif failure == "malformed-json":
        (value.job_evidence / "job.json").write_text("{bad")
    elif failure == "unsafe-log":
        (value.job_evidence / "codec.stderr").write_bytes(b"\0not text")
    command = [
        sys.executable,
        str(SCRIPT),
        "--acceptance",
        str(value.acceptance),
        "--job-evidence",
        str(value.job_evidence),
        "--output",
        str(value.output),
        "--pipy-commit",
        value.pipy_commit,
        "--repository",
        value.repository,
        "--run-id",
        value.run_id,
        "--run-attempt",
        value.run_attempt,
        "--job-key",
        value.job_key,
    ]
    process = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert process.returncode == 2 and "Traceback" not in process.stderr
    index = s.o.load(value.output / "index.json")
    assert index["complete"] is False and index["status"] == "failed-partial"
    if failure == "partial-popen":
        assert {row["path"] for row in index["files"]} == {
            "job/codec.stdout",
            "job/codec.stderr",
        }
    if failure == "unsafe-log":
        assert not index["files"]


def workflow_data():
    # PyYAML is an explicit dev dependency, never a host Node/network requirement.
    yaml = importlib.import_module("yaml")
    return yaml.load(
        (SCRIPT.parents[1] / ".github/workflows/ci.yml").read_text(),
        Loader=yaml.BaseLoader,
    )


def test_workflow_is_mandatory_and_keeps_one_fresh_bootstrap_and_failure_upload():
    workflow = workflow_data()
    assert set(workflow["on"]) == {"push", "pull_request", "workflow_dispatch"}
    job = workflow["jobs"]["durable-reference"]
    assert (
        job["runs-on"] == "ubuntu-24.04"
        and "if" not in job
        and "continue-on-error" not in job
    )
    steps = job["steps"]
    execute = next(step for step in steps if step["name"].startswith("Acquire exact"))[
        "run"
    ]
    assert execute.count('run("acceptance",') == 1
    assert '"acceptance", "--pi-source"' in execute
    assert 'run("codec", ["node"' in execute
    assert "GIT_CONFIG_NOSYSTEM" in execute and '"credential.helper="' in execute
    assert (
        "SHASUMS256.txt" in execute
        and "fd47865ecd6391ebb431fc815e01b9135bf83ded5cf373247e9331e196b7aded"
        in execute
    )
    assert "o.child_env(root)" in execute and "sys.executable" in execute
    upload = next(
        step
        for step in steps
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["if"] == "always()" and upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["include-hidden-files"] == "false"
    assert upload["with"]["path"].endswith("/staged/")
    collector = next(
        step for step in steps if step["name"].startswith("Collect closed")
    )
    assert collector["if"] == "always()" and "env -i" in collector["run"]


def test_excluded_databases_are_validated_and_only_size_hash_is_staged(tmp_path):
    case = tmp_path / "reference/tool-recovery-v1"
    case.mkdir(parents=True)
    sidecar = []
    expected = {}
    for old, new in s.o.contract.POLICIES:
        name = old + "-" + new
        db = case / name / "session.sqlite"
        db.parent.mkdir()
        db.write_bytes(b"synthetic database bytes\0")
        sha = s.digest(db.read_bytes())
        sidecar.append(
            {
                "id": name,
                "path": str(db),
                "bytes": db.stat().st_size,
                "sha256": sha,
                "closed": True,
            }
        )
        expected[name + "/session.sqlite"] = sha
    s.o.dump(case / "database-evidence.json", sidecar)
    s.o.dump(case / "result.json", {"database_sha256": expected})
    for name in ("reference.tar", "archive.stdout"):
        (tmp_path / "reference" / name).write_bytes(b"synthetic archive\0")
    rows = s.exclusions(tmp_path, {"node_archive": None})
    assert len(rows) == 6 and all(row["uploaded"] is False for row in rows)
    assert {
        row["path"].split("tool-recovery-v1/", 1)[1]: row["sha256"] for row in rows[:4]
    } == expected
    db.write_bytes(b"changed")
    with pytest.raises(s.EvidenceError, match="Database evidence bytes/hash drift"):
        s.exclusions(tmp_path, {"node_archive": None})


def test_changed_at_copy_boundary_leaves_only_failed_index(tmp_path, monkeypatch):
    value, roots = synthetic(tmp_path, monkeypatch)
    real = s.write_captured

    def changed(output, roots, names, captured, index):
        (value.acceptance / "invocation.json").write_text(
            "changed after prior validation"
        )
        return real(output, roots, names, captured, index)

    monkeypatch.setattr(s, "write_captured", changed)
    index, code = s.stage(value)
    assert code == 2 and not index["complete"] and not index["files"]
    assert list(value.output.iterdir()) == [value.output / "index.json"]


def test_intermediate_swap_between_check_and_open_cannot_redirect_read(
    tmp_path, monkeypatch
):
    root = tmp_path / "input"
    folder = root / "case"
    folder.mkdir(parents=True)
    (folder / "log").write_text("controlled original")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "log").write_text("must never read private bytes")
    real_open = os.open
    swapped = []

    def swap(name, flags, *a, **kw):
        # Trigger at either the old vulnerable full-path open or secure parent open.
        if not swapped and (str(name) == str(folder / "log") or str(name) == "case"):
            swapped.append(True)
            folder.rename(root / "held")
            folder.symlink_to(outside, target_is_directory=True)
        return real_open(name, flags, *a, **kw)

    monkeypatch.setattr(s.os, "open", swap)
    with pytest.raises(OSError):
        s.read(root, "case/log")
    assert swapped == [True]


def test_hardlink_alias_cannot_import_unrelated_text(tmp_path):
    outside = tmp_path / "private"
    outside.write_text("unrelated private text")
    root = tmp_path / "input"
    root.mkdir()
    (root / "codec.stderr").hardlink_to(outside)
    with pytest.raises(s.EvidenceError, match="hardlink alias"):
        s.read(root, "codec.stderr")


def assert_reference_contexts(workflow):
    """Narrow permanent contract; actual complete-workflow actionlint runs separately."""
    job = workflow["jobs"]["durable-reference"]
    # runner is unavailable at jobs.<job_id>.env, but available in steps.env/with.
    assert "runner." not in str(job.get("env", {})), "runner context at job env"
    root = "${{ runner.temp }}/durable-reference-${{ github.run_id }}-${{ github.run_attempt }}"
    acquire = next(
        step for step in job["steps"] if step["name"].startswith("Acquire exact")
    )
    collect = next(
        step for step in job["steps"] if step["name"].startswith("Collect closed")
    )
    upload = next(
        step for step in job["steps"] if step["name"].startswith("Upload selective")
    )
    assert acquire["env"]["DURABLE_CI_ROOT"] == root
    assert collect["env"]["DURABLE_CI_ROOT"] == root and collect["if"] == "always()"
    assert upload["with"]["path"] == root + "/staged/" and upload["if"] == "always()"


def assert_reference_budget(workflow):
    import ast

    job = workflow["jobs"]["durable-reference"]
    acquire = next(
        step for step in job["steps"] if step["name"].startswith("Acquire exact")
    )
    python = acquire["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    tree = ast.parse(python)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run"
    )
    default = ast.literal_eval(function.args.defaults[0])
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run"
    ]
    budgets = {
        ast.literal_eval(node.args[0]): ast.literal_eval(node.args[2])
        if len(node.args) > 2
        else default
        for node in calls
    }
    assert len(calls) == len(budgets) == len(s.WRAPPER_COMMANDS)
    assert set(budgets) == set(s.WRAPPER_COMMANDS)
    assert sum(budgets.values()) == 3180
    assert sum(budgets.values()) + 120 + 300 + 300 < int(job["timeout-minutes"]) * 60, (
        "job budget cuts controlled cleanup/upload"
    )
    assert sum(budgets.values()) + 120 <= int(acquire["timeout-minutes"]) * 60, (
        "acquisition step budget"
    )
    step_ceiling = sum(int(step["timeout-minutes"]) * 60 for step in job["steps"])
    assert step_ceiling + 540 <= int(job["timeout-minutes"]) * 60, (
        "job budget cuts controlled cleanup/upload"
    )


def test_workflow_context_is_valid_at_each_reference_consumer():
    assert_reference_contexts(workflow_data())


def test_workflow_budget_includes_every_child_step_and_failure_upload():
    assert_reference_budget(workflow_data())


def test_context_and_budget_contracts_reject_known_workflow_mutants():
    import copy

    workflow = workflow_data()
    assert_reference_contexts(workflow)
    assert_reference_budget(workflow)
    invalid_context = copy.deepcopy(workflow)
    invalid_context["jobs"]["durable-reference"]["env"] = {
        "DURABLE_CI_ROOT": "${{ runner.temp }}/wrong"
    }
    with pytest.raises(AssertionError, match="runner context at job env"):
        assert_reference_contexts(invalid_context)
    invalid_budget = copy.deepcopy(workflow)
    invalid_budget["jobs"]["durable-reference"]["timeout-minutes"] = "40"
    with pytest.raises(AssertionError, match="job budget"):
        assert_reference_budget(invalid_budget)


def test_gather_failure_retains_truthful_missing_and_invalid_roles(
    tmp_path, monkeypatch
):
    value, roots = synthetic(tmp_path, monkeypatch)
    first = value.acceptance / "invocation.json"
    first.unlink()
    first.symlink_to(tmp_path / "unrelated")
    missing = value.acceptance / "lost-field/negative.json"
    missing.unlink()
    index, code = s.stage(value)
    assert code == 2 and not index["complete"]
    assert "acceptance/lost-field/negative.json" in index["missing"]
    assert "acceptance/invocation.json" in index["invalid"]
    assert "acceptance/invocation.json" not in index["missing"]
    assert all(row["role"] == "job" for row in index["files"])
    assert not (value.output / "acceptance").exists()


def malformed_inventory_bundle(tmp_path, bad):
    """Enough actual evidence to reach real verify_sources.items, no mocked exception."""
    value = args(tmp_path)
    roots = {"acceptance": value.acceptance, "job": value.job_evidence}
    for _, (role, name) in s.registered().items():
        if role not in roots:
            continue
        path = roots[role] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}" if name.endswith(".json") else "")
    config = s.o.manifest()
    invocation = {
        "output_root": str(value.acceptance),
        "execution_id": "malformed-fixture",
        "reference_execution_id": "malformed-fixture:reference",
        "manifest_sha256": s.o.sha(s.o.FIXTURES / "manifest.json"),
    }
    s.o.dump(value.acceptance / "invocation.json", invocation)
    summary = {
        "schema_version": 1,
        "status": "acceptance-verified",
        "complete": True,
        **invocation,
        "comparison": config["comparison"],
        "verification": config["verification"],
        "coverage": config["coverage"],
    }
    s.o.dump(value.acceptance / "acceptance.json", summary)
    reference = value.acceptance / "reference"
    (reference / "snapshot").mkdir()
    (reference / "reference.tar").write_bytes(b"scripted not-real archive")
    s.o.dump(reference / "source-sha256.json", bad)
    s.o.dump(reference / "dependency-sha256.json", {})
    run = {
        "status": "reference-verified",
        "complete": True,
        "required_cases": list(s.o.contract.CASES),
        "cases": [{"id": name} for name in s.o.contract.CASES],
        "manifest_sha256": invocation["manifest_sha256"],
        "comparison_sha256": config["comparison"],
        "adapters": config["adapters"],
        "reference": config["reference"],
        "runtime": config["runtime"],
        "archive_sha256": s.o.sha(reference / "reference.tar"),
        "source_inventory_sha256": s.o.sha(reference / "source-sha256.json"),
        "dependency_inventory_sha256": s.o.sha(reference / "dependency-sha256.json"),
    }
    s.o.dump(reference / "run.json", run)
    s.o.dump(
        value.job_evidence / "job.json",
        {
            "schema_version": 1,
            "origin": "local",
            "complete": True,
            "identity": s.identities(value),
            "commands": ["codec", "acceptance"],
            "node_archive": None,
        },
    )
    for label in ("codec", "acceptance"):
        s.o.dump(
            value.job_evidence / (label + ".json"), {"exit_code": 0, "timed_out": False}
        )
    return value


@pytest.mark.parametrize("bad", [None, [], "not-an-inventory", 1])
def test_actual_cli_malformed_inventory_shape_yields2_and_failed_index(tmp_path, bad):
    import subprocess
    import sys

    value = malformed_inventory_bundle(tmp_path, bad)
    command = [sys.executable, str(SCRIPT)]
    for name, argument in vars(value).items():
        command += ["--" + name.replace("_", "-"), str(argument)]
    process = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert process.returncode == 2 and "Traceback" not in process.stderr
    index = s.o.load(value.output / "index.json")
    # Real helper AttributeError boundary, not an earlier accidental schema rejection.
    assert index["error"] == "AttributeError" and index["status"] == "failed-partial"
    assert index["complete"] is False and index["attestation"] is None
    assert all(row["role"] == "job" for row in index["files"])


def test_yaml_parser_is_direct_dev_dependency_in_project_and_lock():
    import tomllib

    root = SCRIPT.parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert "pyyaml>=6,<7" in project["dependency-groups"]["dev"]
    lock = tomllib.loads((root / "uv.lock").read_text())
    package = next(item for item in lock["package"] if item["name"] == "pipy")
    assert {"name": "pyyaml"} in package["dev-dependencies"]["dev"]
    assert {"name": "pyyaml", "specifier": ">=6,<7"} in package["metadata"][
        "requires-dev"
    ]["dev"]

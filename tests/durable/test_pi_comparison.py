"""DE01a infrastructure tests. Actual pinned Pi execution is an explicit gate."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/durable_pi_comparison.py"
spec = importlib.util.spec_from_file_location("durable_pi_comparison", SCRIPT)
assert spec is not None and spec.loader is not None
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)


def example():
    return {
        "schema_version": 1,
        "case_id": "kernel-memory-v1",
        "normalization": "finite-json-v1",
        "steps": [
            {
                "op": "commit",
                "value": {
                    "count": 1,
                    "error": None,
                    "history": ["a", "b"],
                    "usage": {"input": 2},
                },
            }
        ],
    }


@pytest.mark.parametrize(
    "text",
    [
        '{"x":1,"x":2}',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":-Infinity}',
        '{"x":1e999}',
        '{"x":9007199254740992}',
        '{"x":9007199254740992.0}',
        '{"x":9007199254740993.0}',
        '{"x":-9007199254740992.0}',
        '{"x":-9007199254740993.0}',
        '{"x":9.007199254740992e15}',
        '{"x":-9.007199254740993e15}',
        '{"x":"\\ud800"}',
    ],
)
def test_strict_json_rejects_ambiguous_values(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text)
    with pytest.raises((ValueError, UnicodeError)):
        oracle.load(path)


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), (1,), {1: "key"}, {"nested": [object()]}]
)
def test_finite_rejects_non_json_python_values(value):
    with pytest.raises(ValueError):
        oracle.finite(value)


@pytest.mark.parametrize("version", [True, False, 1.0, 0, 2, "1"])
def test_unknown_or_noninteger_schema_rejected(version):
    value = example()
    value["schema_version"] = version
    with pytest.raises(ValueError):
        oracle.trace(value)


def test_known_mutation_fails_and_preserves_all_observables():
    expected = example()
    assert oracle.compare(expected, copy.deepcopy(expected))
    mutations = [True, None, "1", 2]
    for value in mutations:
        actual = copy.deepcopy(expected)
        actual["steps"][0]["value"]["count"] = value
        assert not oracle.compare(expected, actual)
    actual = copy.deepcopy(expected)
    del actual["steps"][0]["value"]["usage"]
    assert not oracle.compare(expected, actual)
    actual = copy.deepcopy(expected)
    actual["steps"][0]["value"]["history"].reverse()
    assert not oracle.compare(expected, actual)
    actual = copy.deepcopy(expected)
    actual["steps"].append(copy.deepcopy(actual["steps"][0]))
    assert not oracle.compare(expected, actual)


def test_object_order_and_json_number_representation_only():
    expected = example()
    actual = copy.deepcopy(expected)
    actual["steps"][0]["value"] = dict(
        reversed(list(actual["steps"][0]["value"].items()))
    )
    actual["steps"][0]["value"]["count"] = 1.0
    assert oracle.compare(expected, actual)
    actual["steps"][0]["value"]["extra"] = None
    assert not oracle.compare(expected, actual)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: x.update(extra=True),
        lambda x: x.update(steps=[]),
        lambda x: x["steps"][0].update(extra=True),
        lambda x: x["steps"][0].update(op=True),
    ],
)
def test_trace_envelope_rejects_unsupported_shapes(mutation):
    value = example()
    mutation(value)
    with pytest.raises(ValueError):
        oracle.trace(value)


def test_manifest_and_fixture_drift_fail_closed(tmp_path):
    import shutil

    fixtures = tmp_path / "fixtures"
    shutil.copytree(oracle.FIXTURES, fixtures)
    oracle.manifest(fixtures)
    fixture = fixtures / "kernel-memory-v1.json"
    fixture.write_text(fixture.read_text() + " ")
    with pytest.raises(ValueError, match="Fixture hash drift"):
        oracle.manifest(fixtures)
    fixture.unlink()
    with pytest.raises(FileNotFoundError):
        oracle.manifest(fixtures)


def test_driver_and_reference_drift_fail_closed(tmp_path):
    import shutil

    fixtures = tmp_path / "fixtures"
    shutil.copytree(oracle.FIXTURES, fixtures)
    driver = tmp_path / "driver.mts"
    driver.write_text("// altered")
    with pytest.raises(ValueError, match="Driver hash drift"):
        oracle.manifest(fixtures, driver)
    config = json.loads((fixtures / "manifest.json").read_text())
    config["reference"]["commit"] = "0" * 40
    (fixtures / "manifest.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="Reference identity drift"):
        oracle.manifest(fixtures)


def test_compare_cli_distinguishes_mismatch_and_missing(tmp_path, capsys):
    expected = tmp_path / "expected.json"
    actual = tmp_path / "actual.json"
    oracle.dump(expected, example())
    other = example()
    other["steps"][0]["value"]["count"] = 2
    oracle.dump(actual, other)
    assert oracle.main(["compare", str(expected), str(actual)]) == 1
    assert "MISMATCH" in capsys.readouterr().out
    assert oracle.main(["compare", str(expected), str(tmp_path / "absent")]) == 2


def test_missing_ref_is_error_not_pass(tmp_path, capsys):
    (tmp_path / "absent/.git").mkdir(parents=True)
    assert (
        oracle.main(
            [
                "reference",
                "--pi-source",
                str(tmp_path / "absent"),
                "--output",
                str(tmp_path / "run"),
            ]
        )
        == 2
    )
    metadata = json.loads((tmp_path / "run/ref.json").read_text())
    assert metadata["exit_code"] != 0
    assert "INVALID" in capsys.readouterr().err


@pytest.mark.parametrize("binary", ["node", "npm"])
def test_missing_runtime_is_error_not_pass(tmp_path, monkeypatch, binary):
    (tmp_path / "source/.git").mkdir(parents=True)
    runtime = oracle.manifest()["runtime"]
    attempted = []

    def missing(command, cwd, output, label, env, timeout=120):
        if command[0] == binary:
            attempted.append(binary)
            raise FileNotFoundError(binary)
        if command[0] == "git":
            return (oracle.PIN if label == "ref" else oracle.TREE).encode()
        if command == ["node", "--version"]:
            return runtime["node"].encode()
        raise AssertionError("Unexpected prerequisite command in isolated runtime test")

    monkeypatch.setattr(oracle, "run", missing)
    assert (
        oracle.main(
            [
                "reference",
                "--pi-source",
                str(tmp_path / "source"),
                "--output",
                str(tmp_path / "run"),
            ]
        )
        == 2
    )

    assert attempted == [binary]


def test_missing_dependencies_and_version_drift_are_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        oracle.installed_dependencies(tmp_path)
    (tmp_path / "node_modules/pkg").mkdir(parents=True)
    records = {
        "node_modules/pkg": {
            "version": "1.0.0",
            "integrity": "sha512-test",
            "resolved": "https://example.invalid/pkg",
        }
    }
    oracle.dump(tmp_path / "package-lock.json", {"packages": records})
    oracle.dump(tmp_path / "node_modules/.package-lock.json", {"packages": records})
    oracle.dump(tmp_path / "node_modules/pkg/package.json", {"version": "2.0.0"})
    with pytest.raises(ValueError, match="version drift"):
        oracle.installed_dependencies(tmp_path)


def test_module_resolution_requires_snapshot_and_kernel(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    outside = tmp_path / "outside.ts"
    outside.touch()
    with pytest.raises(ValueError, match="escaped"):
        oracle.verify_resolutions(
            snapshot, [{"specifier": "fixture", "path": "../outside.ts"}], {}
        )
    with pytest.raises(ValueError, match="Missing"):
        oracle.verify_resolutions(snapshot, [], {})
    (snapshot / "standin.ts").touch()
    with pytest.raises(ValueError, match="Required kernel"):
        oracle.verify_resolutions(
            snapshot,
            [{"specifier": "fixture", "path": "standin.ts"}],
            {"standin.ts": oracle.sha(snapshot / "standin.ts")},
        )


def test_child_environment_has_no_credentials_or_user_config(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-value")
    monkeypatch.setenv("NODE_OPTIONS", "--invalid")
    env = oracle.child_env(tmp_path)
    assert "OPENAI_API_KEY" not in env and "NODE_OPTIONS" not in env
    assert env["HOME"] == str(tmp_path / "home")
    assert (tmp_path / "home").stat().st_mode & 0o777 == 0o700


def test_child_deadline_kills_and_reaps_process_group(tmp_path):
    pidfile = tmp_path / "pid"
    program = (
        "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
        "open(sys.argv[1],'w').write(str(p.pid)); time.sleep(120)"
    )
    with pytest.raises(ValueError, match="deadline"):
        oracle.run(
            [sys.executable, "-c", program, str(pidfile)],
            tmp_path,
            tmp_path,
            "deadline",
            dict(os.environ),
            timeout=1,
        )
    record = json.loads((tmp_path / "deadline.json").read_text())
    assert record["timed_out"] is True and record["exit_code"] == -signal.SIGKILL
    child = int(pidfile.read_text())
    # A reparented zombie can remain until init reaps it; a live child cannot.
    process = __import__("subprocess").run(
        ["ps", "-o", "stat=", "-p", str(child)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode != 0 or process.stdout.strip().startswith("Z")


def test_existing_output_cannot_reuse_stale_evidence(tmp_path):
    (tmp_path / "source/.git").mkdir(parents=True)
    assert (
        oracle.main(
            [
                "reference",
                "--pi-source",
                str(tmp_path / "source"),
                "--output",
                str(tmp_path),
            ]
        )
        == 2
    )


def test_missing_actual_resolved_dependency_is_not_phantom_exception(tmp_path):
    module = tmp_path / "node_modules/pkg/index.js"
    module.parent.mkdir(parents=True)
    module.write_text("export const value=1;")
    oracle.dump(module.parent / "package.json", {"version": "1.0.0"})
    modules = {"node_modules/pkg/index.js": oracle.sha(module)}
    with pytest.raises(ValueError, match="no installed lock evidence"):
        oracle.module_dependencies(
            tmp_path,
            modules,
            {"present": {}, "absent_extraneous_lock_records": {"node_modules/pkg": {}}},
        )
    module.unlink()
    with pytest.raises(ValueError, match="missing"):
        oracle.verify_resolutions(
            tmp_path, [{"specifier": "pkg", "path": "node_modules/pkg/index.js"}], {}
        )


def test_source_hash_drift_and_missing_fail_closed(tmp_path):
    path = tmp_path / "package-lock.json"
    path.write_text("original")
    expected = {path.name: oracle.sha(path)}
    path.write_text("drift")
    with pytest.raises(ValueError, match="source or lockfile drift"):
        oracle.verify_sources(tmp_path, expected)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        oracle.verify_sources(tmp_path, expected)


def test_output_inside_upstream_is_refused(tmp_path):
    (tmp_path / ".git").mkdir()
    assert (
        oracle.main(
            [
                "reference",
                "--pi-source",
                str(tmp_path),
                "--output",
                str(tmp_path / "evidence"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize(
    "case_index,field,value",
    [
        (0, "trace_schema", True),
        (0, "trace_schema", 1.0),
        (1, "trace_schema", True),
        (1, "trace_schema", 2.0),
        (1, "id", "other"),
        (1, "id", True),
        (None, "python_adapter", "PASS"),
        (None, "schema_version", 4),
    ],
)
def test_manifest_contract_cannot_claim_other_schema_or_parity(
    tmp_path, case_index, field, value
):
    import shutil

    fixtures = tmp_path / "fixtures"
    shutil.copytree(oracle.FIXTURES, fixtures)
    config = json.loads((fixtures / "manifest.json").read_text())
    target = config if case_index is None else config["cases"][case_index]
    target[field] = value
    oracle.dump(fixtures / "manifest.json", config)
    with pytest.raises(ValueError):
        oracle.manifest(fixtures)


def test_failed_child_is_invalid_outer_reference_evidence(tmp_path, monkeypatch):
    def failed_reference(source, output, fixtures):
        oracle.run(
            [sys.executable, "-c", "raise SystemExit(1)"],
            tmp_path,
            tmp_path,
            "failed-driver",
            dict(os.environ),
        )

    monkeypatch.setattr(oracle, "reference", failed_reference)
    assert (
        oracle.main(
            [
                "reference",
                "--pi-source",
                str(tmp_path / "source"),
                "--output",
                str(tmp_path / "run"),
            ]
        )
        == 2
    )
    record = json.loads((tmp_path / "failed-driver.json").read_text())
    assert record["exit_code"] == 1 and record["timed_out"] is False


def test_subdirectory_source_is_refused_before_any_output(tmp_path):
    source = tmp_path / "repo/packages/durable"
    source.mkdir(parents=True)
    (tmp_path / "repo/.git").mkdir()
    assert (
        oracle.main(
            [
                "reference",
                "--pi-source",
                str(source),
                "--output",
                str(tmp_path / "repo/evidence"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "repo/evidence").exists()


@pytest.mark.parametrize("value", [float(2**53), -float(2**53), 1e20, -1e20])
def test_integral_float_safe_range_applies_to_in_memory_traces(value):
    actual = example()
    actual["steps"][0]["value"]["count"] = value
    with pytest.raises(ValueError, match="safe range"):
        oracle.finite(value)
    with pytest.raises(ValueError, match="safe range"):
        oracle.compare(actual, actual)


@pytest.mark.parametrize("value", [2**53 - 1, -(2**53 - 1)])
def test_safe_boundary_integer_and_float_remain_equal(value):
    expected = example()
    expected["steps"][0]["value"]["count"] = value
    actual = copy.deepcopy(expected)
    actual["steps"][0]["value"]["count"] = float(value)
    assert oracle.compare(expected, actual)


@pytest.mark.parametrize(
    "boolean,number", [(True, 1), (True, 1.0), (False, 0), (False, 0.0)]
)
def test_json_boolean_and_numeric_types_remain_distinct(boolean, number):
    expected = example()
    expected["steps"][0]["value"]["count"] = boolean
    actual = copy.deepcopy(expected)
    actual["steps"][0]["value"]["count"] = number
    assert not oracle.compare(expected, actual)


@pytest.mark.parametrize(
    "first,second",
    [
        ("9007199254740992.0", "9007199254740993.0"),
        ("9.007199254740992e15", "9.007199254740993e15"),
        ("-9007199254740992.0", "-9007199254740993.0"),
    ],
)
def test_actual_compare_cli_rejects_collapsed_unsafe_float_literals(
    tmp_path, first, second
):
    template = json.dumps(
        {
            "schema_version": 1,
            "case_id": "kernel-memory-v1",
            "normalization": "finite-json-v1",
            "steps": [{"op": "commit", "value": "NUMBER"}],
        }
    )
    expected, actual = tmp_path / "expected.json", tmp_path / "actual.json"
    expected.write_text(template.replace('"NUMBER"', first))
    actual.write_text(template.replace('"NUMBER"', second))
    process = subprocess.run(
        [sys.executable, str(SCRIPT), "compare", str(expected), str(actual)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 2
    assert "safe range" in process.stderr
    assert "TRACE EQUAL" not in process.stdout


def resolution_fixture(snapshot):
    required = [
        "packages/durable/src/session/session.ts",
        "packages/durable/src/storage/memory.ts",
        "packages/durable/src/documents.ts",
    ]
    for relative in required:
        path = snapshot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("pinned fixture")
    originals = {relative: oracle.sha(snapshot / relative) for relative in required}
    resolutions = [{"specifier": "fixture", "path": relative} for relative in required]
    return originals, resolutions


def test_workspace_resolution_rejects_nested_registry_package(tmp_path):
    originals, resolutions = resolution_fixture(tmp_path)
    relative = "packages/coding-agent/node_modules/@earendil-works/pi-ai/dist/index.js"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text("registry standin")
    resolutions.append({"specifier": "@earendil-works/pi-ai", "path": relative})
    with pytest.raises(ValueError, match="Workspace module"):
        oracle.verify_resolutions(tmp_path, resolutions, originals)


@pytest.mark.parametrize(
    "relative",
    ["packages/durable/src/not-in-pinned-archive.ts", "not-in-pinned-archive.ts"],
)
def test_resolved_source_requires_archive_membership(tmp_path, relative):
    originals, resolutions = resolution_fixture(tmp_path)
    (tmp_path / relative).write_text("unpinned standin")
    resolutions.append({"specifier": "fixture", "path": relative})
    with pytest.raises(ValueError, match="absent from pinned archive"):
        oracle.verify_resolutions(tmp_path, resolutions, originals)


def test_resolved_source_requires_exact_archived_hash(tmp_path):
    originals, resolutions = resolution_fixture(tmp_path)
    assert oracle.verify_resolutions(tmp_path, resolutions, originals) == originals
    (tmp_path / resolutions[0]["path"]).write_text("changed standin")
    with pytest.raises(ValueError, match="or changed"):
        oracle.verify_resolutions(tmp_path, resolutions, originals)


@pytest.mark.parametrize(
    "value", [[1], {"packages": [1]}, {"packages": {"node_modules/pkg": [1]}}]
)
@pytest.mark.parametrize(
    "which", ["package-lock.json", "node_modules/.package-lock.json"]
)
def test_malformed_lock_objects_raise_evidence_error(tmp_path, value, which):
    (tmp_path / "node_modules").mkdir()
    valid = {"packages": {"node_modules/pkg": {"version": "1.0.0"}}}
    oracle.dump(tmp_path / "package-lock.json", valid)
    oracle.dump(tmp_path / "node_modules/.package-lock.json", valid)
    oracle.dump(tmp_path / which, value)
    with pytest.raises(oracle.EvidenceError, match="lock"):
        oracle.installed_dependencies(tmp_path)


@pytest.mark.parametrize(
    "record",
    [
        1,
        {"specifier": 1, "path": "source.ts"},
        {"specifier": "fixture", "path": [1]},
        {"specifier": "fixture"},
    ],
)
def test_malformed_resolution_record_raises_evidence_error(tmp_path, record):
    with pytest.raises(oracle.EvidenceError, match="module-resolution"):
        oracle.verify_resolutions(tmp_path, [record], {})


@pytest.mark.parametrize("kind", ["packages", "record", "specifier"])
def test_invalid_evidence_main_subprocess_exits_two(tmp_path, kind):
    # Inject only the reference execution boundary: the real evidence parser
    # and CLI argument/error handling run in another Python process.
    (tmp_path / "node_modules").mkdir()
    records = {"packages": {"node_modules/pkg": {"version": "1.0.0"}}}
    oracle.dump(tmp_path / "package-lock.json", records)
    if kind == "packages":
        records = {"packages": [1]}
    elif kind == "record":
        records = {"packages": {"node_modules/pkg": [1]}}
    oracle.dump(tmp_path / "node_modules/.package-lock.json", records)
    program = """
import importlib.util,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('oracle',sys.argv[1])
oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
def invalid_reference(source,output,fixtures):
    if sys.argv[3]=='specifier':
        return oracle.verify_resolutions(Path(sys.argv[2]),[{'specifier':1,'path':'source.ts'}],{})
    return oracle.installed_dependencies(Path(sys.argv[2]))
oracle.reference=invalid_reference
raise SystemExit(oracle.main(['reference','--pi-source',sys.argv[2],'--output',sys.argv[2]]))
"""
    process = subprocess.run(
        [sys.executable, "-c", program, str(SCRIPT), str(tmp_path), kind],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 2
    assert "REFERENCE EVIDENCE INVALID" in process.stderr
    assert "Traceback" not in process.stderr


def test_actual_compare_cli_valid_mismatch_stays_exit_one(tmp_path):
    expected, actual = tmp_path / "expected.json", tmp_path / "actual.json"
    oracle.dump(expected, example())
    other = example()
    other["steps"][0]["value"]["count"] = 2
    oracle.dump(actual, other)
    process = subprocess.run(
        [sys.executable, str(SCRIPT), "compare", str(expected), str(actual)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 1 and "TRACE MISMATCH" in process.stdout

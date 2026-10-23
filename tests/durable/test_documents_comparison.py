"""Closed typed-document oracle: actual native legs vs lossless captured Pi unit data.

The default-delta fixture was captured from the actual pinned driver in a verified
reused source/dependency closure; it never substitutes
for the coordinator's fresh complete chain gates.
"""

import asyncio
import base64
import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/durable_documents_comparison.py"
spec = importlib.util.spec_from_file_location("documents_comparator_tests", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
RAW_BYTES = 2_428_093
RAW_SHA = "e592dcf85c2e5a1329436e0f1f62936eb8cfce60cca242eac89b552c315608fd"


def captured():
    wrapper = m.d.load(m.o.FIXTURES / "session-documents-deltas-v1.expected.json")
    m.d.shape(
        wrapper,
        {"schema_version", "encoding", "raw_bytes", "raw_sha256", "data"},
        "captured document wrapper",
    )
    assert type(wrapper["schema_version"]) is int and wrapper["schema_version"] == 1
    assert type(wrapper["raw_bytes"]) is int and wrapper["raw_bytes"] == RAW_BYTES
    assert wrapper["raw_sha256"] == RAW_SHA and wrapper["encoding"] == "zlib+base64"
    assert type(wrapper["data"]) is str and len(wrapper["data"]) < 40_000
    decoder = zlib.decompressobj()
    raw = decoder.decompress(
        base64.b64decode(wrapper["data"], validate=True), RAW_BYTES + 1
    )
    assert (
        len(raw) == RAW_BYTES
        and decoder.eof
        and not decoder.unused_data
        and not decoder.unconsumed_tail
    )
    assert hashlib.sha256(raw).hexdigest() == RAW_SHA
    return json.loads(raw)


@pytest.fixture(scope="module")
def paired(tmp_path_factory):
    reference = captured()
    actual, raw = asyncio.run(
        m.native(tmp_path_factory.mktemp("typed-documents") / "stores")
    )
    assert m.comparison(reference, actual) == 0
    assert len(raw) == 3
    return reference, actual


def cli(tmp_path, expected, actual):
    m.d.dump(tmp_path / "expected.json", expected)
    m.d.dump(tmp_path / "actual.json", actual)
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "compare", "expected.json", "actual.json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_actual_native_72_cases_match_captured_pi_24_with_closed_spellings(paired):
    expected, actual = paired
    assert len(expected["cases"]) == 24
    assert [len(b["cases"]) for b in actual["backends"]] == [24, 24, 24]
    assert len(m.errors(m.recipe())) == 11
    for lane in actual["backends"]:
        rows = {r["id"]: m.v.decode(r["profile"]) for r in lane["cases"]}
        assert rows["T7"]["counts"].get("scan_documents", 0) == 0
        assert len(rows["P1"]["scans"]) == len(rows["P2"]["scans"]) == 2
        assert len(rows["P1"]["publications"]) == len(rows["P2"]["publications"]) == 257
        assert rows["T8"]["facts"]["acquire"]["status"] == "rejected"
        assert rows["T9"]["facts"]["acquire"]["status"] == "fulfilled"
        assert all(rows[x]["facts"]["same_pending"] for x in ("A3", "A4", "A5"))


@pytest.mark.parametrize("name", m.MUTANTS)
def test_actual_cli_positive_zero_behavior_control_one(paired, tmp_path, name):
    expected, actual = paired
    assert cli(tmp_path, expected, actual).returncode == 0
    result = cli(tmp_path, expected, m.mutate(actual, name))
    assert (
        result.returncode == 1
        and "INVALID" not in result.stderr
        and "Traceback" not in result.stderr
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "schema-bool",
        "schema-float",
        "counter-bool",
        "missing-case",
        "extra-backend",
        "invalid-tag",
        "missing-ops",
        "unknown-publication-field",
        "invalid-ops",
    ],
)
def test_actual_cli_malformed_metadata_is_two(paired, tmp_path, mutation):
    expected, actual = copy.deepcopy(paired)
    if mutation == "schema-bool":
        actual["schema_version"] = True
    elif mutation == "schema-float":
        actual["schema_version"] = 1.0
    elif mutation == "missing-case":
        actual["backends"][0]["cases"].pop()
    elif mutation == "extra-backend":
        actual["backends"].append(copy.deepcopy(actual["backends"][0]))
    elif mutation == "invalid-tag":
        actual["backends"][0]["cases"][0]["profile"] = ["unknown"]
    else:
        row = actual["backends"][0]["cases"][0]
        value = m.v.decode(row["profile"])
        if mutation == "missing-ops":
            value["publications"][0].pop("ops")
        elif mutation == "unknown-publication-field":
            value["publications"][0]["extra"] = None
        elif mutation == "invalid-ops":
            value["publications"][0]["ops"] = [["unknown"]]
        else:
            value["counts"]["mint_id"] = True
        row["profile"] = m.v.encode(value)
    result = cli(tmp_path, expected, actual)
    assert result.returncode == 2 and "Traceback" not in result.stderr


def test_unexpected_native_error_remains_behavior_mismatch_one(paired):
    expected, actual = copy.deepcopy(paired)
    row = next(r for r in actual["backends"][0]["cases"] if r["id"] == "T8")
    value = m.v.decode(row["profile"])
    value["facts"]["acquire"]["error"]["message"] = "unexpected rejection"
    row["profile"] = m.v.encode(value)
    assert m.comparison(expected, actual) == 1
    assert m.comparison(expected, m.projected_native(actual, m.recipe())) == 1


def test_pi_error_usage_is_exact_and_native_projection_is_idempotent(paired):
    expected, actual = copy.deepcopy(paired)
    assert m.comparison(expected, m.projected_native(actual, m.recipe())) == 0
    row = next(r for r in expected["cases"] if r["id"] == "A6")
    value = m.v.decode(row["profile"])
    value["facts"]["invalid"]["error"] = m.recipe()["error_spellings"][0]["native"]
    row["profile"] = m.v.encode(value)
    with pytest.raises(m.o.EvidenceError, match="Stale Pi"):
        m.comparison(expected, actual)


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "unknown", "equal", "bool-count"]
)
def test_recipe_cannot_open_error_or_case_inventory(monkeypatch, mutation):
    value = m.recipe()
    if mutation == "missing":
        value["error_spellings"].pop()
    elif mutation == "duplicate":
        value["error_spellings"][1] = value["error_spellings"][0]
    elif mutation == "unknown":
        value["error_spellings"][0]["case"] = "any"
    elif mutation == "equal":
        value["error_spellings"][0]["native"] = value["error_spellings"][0]["pi"]
    else:
        value["retirement_count"] = True
    original = m.d.load
    monkeypatch.setattr(m.d, "load", lambda p: value if p == m.INPUT else original(p))
    with pytest.raises(m.o.EvidenceError):
        m.recipe()


@pytest.mark.parametrize("entry", ["directory", "dangling-symlink"])
def test_existing_output_is_refused_before_parent_lookup(tmp_path, entry):
    output, target = tmp_path / "output", tmp_path / "not-created"
    if entry == "directory":
        output.mkdir()
    else:
        output.symlink_to(target, target_is_directory=True)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            "run",
            "--acceptance",
            "missing",
            "--sqlite",
            "missing",
            "--output",
            "output",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2 and "output entry exists" in result.stderr
    assert not target.exists()


@pytest.mark.parametrize("exception", [AttributeError, RecursionError])
def test_unexpected_ordinary_driver_exception_is_invalid_two(
    monkeypatch, capsys, exception
):
    def fail(*args):
        raise exception("driver failure")

    monkeypatch.setattr(m, "execute", fail)
    assert m.main(["run", "--acceptance", "x", "--sqlite", "y", "--output", "z"]) == 2
    assert "INVALID" in capsys.readouterr().err


def test_current_import_closure_pins_new_document_helper_in_all_session_phases():
    helper = "src/pipy_harness/native/durable/documents.py"
    for inventory in (m.b.g.CLOSURE, m.b.CLOSURE, m.d.CLOSURE, m.CLOSURE):
        assert helper in inventory
    for helper in ("draft_tracker.py", "delta_ops.py"):
        assert "src/pipy_harness/native/durable/" + helper in m.b.CLOSURE
        assert "src/pipy_harness/native/durable/" + helper in m.CLOSURE
    assert "tests/durable/test_session_deltas.py" in m.CLOSURE
    assert (
        "tests/fixtures/durable/session-documents-deltas-v1.expected.json" in m.CLOSURE
    )
    assert m.configuration()["parents"]["sqlite"] == m.o.sha(m.d.MANIFEST)


def _synthetic_sqlite_predecessor(folder, parent, modules, *, misplaced=False):
    """Closed validator fixture using the delivered D2 generator's flat layout."""
    m.d.dump(folder / "observed.json", {"fixture": "reference"})
    m.d.dump(folder / "native.json", {"fixture": "native"})
    m.d.dump(folder / "resolutions.json", [])
    controls = []
    for name in m.d.MUTANTS:
        path = folder / (name + "-mutant.json")
        m.d.dump(path, {"fixture_control": name})
        controls.append(
            {
                "id": name,
                "schema_valid": True,
                "positive_exit": 0,
                "negative_exit": 1,
                "sha256": m.o.sha(path),
            }
        )
    result = {
        "schema_version": 1,
        "phase": m.d.PHASE,
        "status": "PASS",
        "execution_id": "445b6c54-bab1-4398-b0a7-c6a39a62c71c",
        "fresh_reference_bootstrap": False,
        "parent_execution_id": m.d.load(parent / "acceptance.json")["execution_id"],
        "parent_acceptance_sha256": m.o.sha(parent / "acceptance.json"),
        "reference_run_sha256": m.o.sha(parent / "reference/run.json"),
        "manifest_sha256": m.o.sha(m.d.MANIFEST),
        "parents": m.d.parents(),
        "module_sha256": modules,
        "artifacts_sha256": {
            str(p.relative_to(folder)): m.o.sha(p)
            for p in folder.rglob("*")
            if p.is_file()
        },
        "mutants": controls,
        "scripted_scenarios_per_backend": 34,
        "scripted_operations_per_backend": 258,
        "exported_cases_per_reference_backend": 21,
        "reference_backends": list(m.d.REFERENCE),
        "native_backends": list(m.d.NATIVE),
        "product_integration": False,
        "full_storage_parity": False,
        "d1_evidence": m.d.configuration()["d1_evidence"],
    }
    m.d.dump(folder / "result.json", result)
    assert not (folder / "mutants").exists()
    if misplaced:
        target = folder / "mutants"
        target.mkdir()
        for name in m.d.MUTANTS:
            (folder / (name + "-mutant.json")).rename(target / (name + ".json"))
        result["artifacts_sha256"] = {
            str(p.relative_to(folder)): m.o.sha(p)
            for p in folder.rglob("*")
            if p.is_file() and p.name != "result.json"
        }
        m.d.dump(folder / "result.json", result)
    return result


def _private_execute_fixture(folder, variant):
    """CLI wiring only: synthetic predecessor attestations and Node observations.

    Native compare/control subprocesses remain actual CLI calls. This is never
    a fresh Pi reference gate; no historical proof root is opened or modified.
    """
    from unittest.mock import patch

    folder = Path(folder)
    parent, sqlite, output = folder / "parent", folder / "sqlite", folder / "output"
    snapshot = parent / "reference/snapshot"
    snapshot.mkdir(parents=True)
    sqlite.mkdir()
    (snapshot / m.DESTINATION).parent.mkdir(parents=True, exist_ok=True)
    m.d.dump(parent / "acceptance.json", {"execution_id": "fixture-parent"})
    m.d.dump(parent / "reference/run.json", {"fixture": True})
    dependency = "node_modules/probe/index.js"
    digest = hashlib.sha256(b"fixture module").hexdigest()
    m.d.dump(parent / "reference/dependency-sha256.json", {dependency: digest})
    m.d.dump(parent / "reference/source-sha256.json", {})
    modules = {next(iter(m.SOURCES)): "0" * 64, dependency: digest}
    if variant == "dependency-drift":
        modules[dependency] = "1" * 64
    elif variant == "unbound-source":
        modules["packages/unbound.ts"] = "2" * 64
    _synthetic_sqlite_predecessor(
        sqlite, parent, modules, misplaced=variant == "misplaced-control"
    )
    before = {
        str(p.relative_to(parent)): m.o.sha(p) for p in parent.rglob("*") if p.is_file()
    }
    original_process = m.d.process
    calls = []

    def process(command, cwd, destination, label, env, timeout=120):
        calls.append(label)
        assert Path(env["HOME"]) == output / "home"
        if label in ("node-version", "npm-version", "pi-documents"):
            assert env["LANG"] == "C.UTF-8"
            assert Path(env["TMPDIR"]) == output / "tmp"
            assert Path(env["npm_config_cache"]) == output / "npm-cache"
            for key in ("npm_config_userconfig", "npm_config_globalconfig"):
                assert Path(env[key]).read_bytes() == b""
            # Model config/temp/cache writes by the synthetic reference child.
            for key in ("HOME", "TMPDIR", "npm_config_cache"):
                (Path(env[key]) / label).write_text("private fixture")
            if label.endswith("-version"):
                return (b"v26.12.0\n" if command[0] == "node" else b"11.20.0\n"), b"", 0
            m.d.dump(output / "observed.json", m.d.load(folder / "reference.json"))
            m.d.dump(output / "raw.json", {"fixture": True})
            m.d.dump(output / "resolutions.json", [])
            return b"", b"", 0
        if label == "native-documents":
            m.d.dump(output / "native.json", m.d.load(folder / "actual.json"))
            m.d.dump(output / "native-raw.json", {"fixture": True})
            return b"", b"", 0
        return original_process(command, cwd, destination, label, env, timeout)

    with (
        patch.object(m.v.artifacts, "attest"),
        patch.object(
            m.o.acceptance,
            "attest_reference",
            return_value={"installed_dependencies": {}},
        ),
        patch.object(m.d, "comparison", return_value=0),
        patch.object(m.o, "verify_sources"),
        patch.object(m.o, "verify_resolutions", return_value=modules),
        patch.object(m.o, "module_dependencies"),
        patch.object(m.d, "process", side_effect=process),
    ):
        result = m.main(
            [
                "run",
                "--acceptance",
                str(parent),
                "--sqlite",
                str(sqlite),
                "--output",
                str(output),
            ]
        )
    assert all(m.o.sha(parent / name) == digest for name, digest in before.items())
    after = {str(p.relative_to(parent)) for p in parent.rglob("*") if p.is_file()}
    expected_additions = (
        set()
        if variant == "misplaced-control"
        else {"reference/snapshot/" + m.DESTINATION}
    )
    assert after - set(before) == expected_additions
    assert (
        "native-documents" in calls
        if variant == "clean"
        else "native-documents" not in calls
    )
    return result


@pytest.mark.parametrize(
    "variant", ["clean", "dependency-drift", "unbound-source", "misplaced-control"]
)
def test_actual_cli_private_predecessor_import_and_environment_guards(
    paired, tmp_path, variant
):
    expected, actual = paired
    m.d.dump(tmp_path / "reference.json", expected)
    m.d.dump(tmp_path / "actual.json", actual)
    bootstrap = (
        "import importlib.util,sys;"
        f"s=importlib.util.spec_from_file_location('private_fixture',{str(Path(__file__).resolve())!r});"
        "t=importlib.util.module_from_spec(s);s.loader.exec_module(t);"
        "sys.exit(t._private_execute_fixture(sys.argv[1],sys.argv[2]))"
    )
    _, stderr, code = m.d.process(
        [sys.executable, "-I", "-c", bootstrap, str(tmp_path), variant],
        tmp_path,
        tmp_path,
        "private-predecessor",
        m.o.child_env(tmp_path),
        90,
    )
    assert code == (0 if variant == "clean" else 2), stderr
    assert b"Traceback" not in stderr
    if variant == "misplaced-control":
        assert b"FileNotFoundError" in stderr and not (tmp_path / "output").exists()
    elif variant != "clean":
        assert b"Unbound document import" in stderr

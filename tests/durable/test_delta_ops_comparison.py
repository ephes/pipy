"""Evidence validation and permanent semantic controls for decoded operations."""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/durable_delta_ops_comparison.py"
spec = importlib.util.spec_from_file_location("delta_pair_tests", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_closed_manifest_and_all_recipe_shapes():
    assert set(m.manifest()["closure"]) == m.CLOSURE
    inputs = m.recipes()
    assert len(inputs) == 66
    actual = m.native(inputs)
    assert m.compare(actual, actual) == 0
    assert {
        case["ops"][0][0]
        for case in inputs
        if case["ops"] and type(case["ops"][0]) is list and case["ops"][0]
    } >= set("rsdatpm")


@pytest.mark.parametrize(
    "index", [0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 13, 14, 15, 16, 18, 55, 56, 57]
)
def test_schema_valid_semantic_mutants_mismatch(index):
    actual = m.native(m.recipes())
    mutant = copy.deepcopy(actual)
    if "result" in mutant[index]:
        mutant[index]["result"] = m.profile("wrong result")
    else:
        mutant[index]["error"] = (
            "TypeError" if mutant[index]["error"] != "TypeError" else "PathError"
        )
    assert m.compare(actual, actual) == 0
    assert m.compare(actual, mutant) == 1


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        ["number", "7ff0000000000000"],
        ["bool", 1],
        ["string", "0"],
        ["object", [["0061", ["null"]], ["0061", ["null"]]]],
    ],
)
def test_invalid_profiles_refuse(bad):
    with pytest.raises(m.o.EvidenceError):
        m.valid_profile(bad)


def test_strict_json_duplicate_and_nonfinite_refusal(tmp_path):
    for index, text in enumerate(('{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}')):
        path = tmp_path / str(index)
        path.write_text(text)
        with pytest.raises((m.o.EvidenceError, ValueError)):
            m.load(path)


@pytest.mark.parametrize(
    "boundary",
    ["relative", "noncheckout", "subdirectory", "inside", "symlink", "existing"],
)
def test_cli_refusals_use_private_fake_checkout(tmp_path, boundary):
    source = tmp_path / "private-source"
    source.mkdir()
    (source / ".git").mkdir()
    output = tmp_path / "output"
    if boundary == "relative":
        source = Path("private-source")
    elif boundary == "noncheckout":
        (source / ".git").rmdir()
    elif boundary == "subdirectory":
        source = source / "subdirectory"
        source.mkdir()
    elif boundary == "inside":
        output = source / "output"
    elif boundary == "symlink":
        output.symlink_to(tmp_path / "absent", target_is_directory=True)
    elif boundary == "existing":
        output.mkdir()
    process = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "run",
            "--pi-source",
            str(source),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(m.ROOT / "src")},
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 2
    expected = (
        b"Output must be outside upstream"
        if boundary == "inside"
        else b"Reference source must be absolute"
        if boundary == "relative"
        else b"Reference source must be a checkout root"
        if boundary in {"noncheckout", "subdirectory"}
        else b"Output entry already exists"
    )
    assert expected in process.stderr
    if boundary not in {"existing", "symlink"}:
        assert not output.exists()
    assert not (output / "result.json").exists()
    assert not (tmp_path / "absent").exists()


def test_compare_cli_zero_one_two(tmp_path):
    expected = m.native(m.recipes())
    paths = [tmp_path / name for name in ("expected.json", "actual.json")]
    paths[0].write_text(json.dumps(expected))
    for code in (0, 1, 2):
        actual = copy.deepcopy(expected)
        if code == 1:
            actual[0]["result"] = m.profile("wrong")
        if code == 2:
            actual[0]["extra"] = True
        paths[1].write_text(json.dumps(actual))
        process = subprocess.run(
            [sys.executable, str(SCRIPT), "compare", *map(str, paths)],
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(m.ROOT / "src")},
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert process.returncode == code, process.stderr


@pytest.mark.parametrize(
    ("before", "after", "selector"),
    [
        (
            '_RESERVED = frozenset({"__proto__", "constructor", "prototype"})',
            "_RESERVED = frozenset()",
            "test_invalid_decoded_grammar",
        ),
        (
            "min(int(operation[2]), len(units) // 2) * 2",
            "min(int(operation[2]), len(units) // 2) * 4",
            "test_exact_decoded_application",
        ),
        (
            "target[:] = [target[int(index)] for index in operation[2]]",
            "target[:] = target",
            "test_exact_decoded_application",
        ),
        (
            "index = min(int(operation[2]), len(target))",
            "index = 0",
            "test_exact_decoded_application",
        ),
        (
            "if type(parent) is list and key > len(parent):",
            "if False:",
            "test_unresolved_and_array_boundaries",
        ),
        (
            "parent.append(replacement)",
            "parent.append(None)",
            "test_exact_decoded_application",
        ),
        (
            'replacement = units.decode("utf-16-be", "surrogatepass")',
            'replacement = current + operation[2] if tag == "a" else units.decode("utf-16-be", "surrogatepass")',
            "test_touched_string_results_use_consistent_python_spelling",
        ),
        (
            "root: Any = value.thaw()",
            "root: Any = {}",
            "test_owned_atomicity_late_invalid_and_detached_outputs",
        ),
    ],
)
def test_private_source_mutants_defeated_by_designated_tests(
    tmp_path, before, after, selector
):
    # A private minimal package avoids importing or modifying any live checkout.
    package = tmp_path / "src/pipy_harness/native/durable"
    package.mkdir(parents=True)
    for parent in (package, package.parent, package.parent.parent):
        (parent / "__init__.py").write_text("")
    agent = package.parent / "agent"
    agent.mkdir()
    (agent / "__init__.py").write_text("")
    (agent / "json_values.py").write_bytes(
        (m.ROOT / "src/pipy_harness/native/agent/json_values.py").read_bytes()
    )
    source = (m.ROOT / "src/pipy_harness/native/durable/delta_ops.py").read_text()
    assert source.count(before) == 1
    (package / "delta_ops.py").write_text(source)
    (package / "codec.py").write_bytes(
        (m.ROOT / "src/pipy_harness/native/durable/codec.py").read_bytes()
    )
    target = tmp_path / "test_delta_ops.py"
    target.write_bytes((m.ROOT / "tests/durable/test_delta_ops.py").read_bytes())
    command = [
        sys.executable,
        "-X",
        "dev",
        "-W",
        "error",
        "-m",
        "pytest",
        "-q",
        "-c",
        "/dev/null",
        "-p",
        "no:cacheprovider",
        "--rootdir",
        str(tmp_path),
        str(target) + "::" + selector,
        "--basetemp",
        str(tmp_path / "pytest"),
    ]
    environment = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(tmp_path / "src"),
        "TMPDIR": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for key in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        path = tmp_path / "environment" / key
        path.mkdir(parents=True)
        environment[key] = str(path)
    baseline = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert baseline.returncode == 0, baseline.stdout.decode() + baseline.stderr.decode()
    (package / "delta_ops.py").write_text(source.replace(before, after))
    process = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 1, process.stdout.decode() + process.stderr.decode()
    assert b"failed" in process.stdout


@pytest.mark.parametrize(
    "field",
    [
        "sources",
        "closure",
        "reference_commit",
        "schema_version",
        "extra",
        "digest",
        "bytes",
    ],
)
def test_closed_manifest_drift_refuses(monkeypatch, tmp_path, field):
    value = m.load(m.MANIFEST)
    if field in {"sources", "closure"}:
        value[field]["unexpected.py"] = "0" * 64
    elif field == "extra":
        value[field] = True
    elif field == "digest":
        value["sources"][next(iter(value["sources"]))] = "bad"
    elif field == "bytes":
        value["closure"][next(iter(value["closure"]))] = "0" * 64
    else:
        value[field] = False
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(value))
    monkeypatch.setattr(m, "MANIFEST", manifest)
    with pytest.raises(m.o.EvidenceError):
        m.manifest()


def test_pinned_source_byte_drift_refuses_private_snapshot(monkeypatch, tmp_path):
    source = tmp_path / "private-source"
    source.mkdir()
    (source / ".git").mkdir()
    output = tmp_path / "output"

    def execute(command, cwd, output, label, env, timeout):
        if label == "node-version":
            return b"v26.12.0"
        if label == "npm-version":
            return b"11.20.0"
        assert label.startswith("source-")
        return b"mutated pinned source"

    monkeypatch.setattr(m.o, "run", execute)
    with pytest.raises(m.o.EvidenceError):
        m.run(source, output)
    assert not (output / "result.json").exists()

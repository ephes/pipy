"""Closed paired corpus generation and native whole-file cross-check."""

import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_env_line_scan import expected

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts/durable_env_line_scan_comparison.py"
)
spec = importlib.util.spec_from_file_location("env_scanner_tests", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_closed_corpus_native_all_seven_fields():
    inputs = m.recipes()
    assert len(inputs) == 30000
    assert inputs == m.recipes()
    actual = m.native(inputs)
    for recipe, result in zip(inputs, actual, strict=True):
        assert result == m.asdict(
            expected(bytes.fromhex(recipe["hex"]), recipe["start"], recipe["end"])
        )


def test_manifest_is_closed_and_current():
    assert set(m.manifest()["closure"]) == m.CLOSURE


@pytest.mark.parametrize(
    "field", ["seeds", "generator", "pieces", "chunk_sizes", "extra"]
)
def test_recipe_drift_refuses(monkeypatch, field):
    original = m.o.load

    def changed(path):
        value = original(path)
        value[field] = None
        return value

    monkeypatch.setattr(m.o, "load", changed)
    with pytest.raises(m.o.EvidenceError, match="Closed scanner recipe drift"):
        m.recipes()


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        [True],
        [{"start": 0}],
        [{k: True for k in m.asdict(expected(b"", 0, None))}],
    ],
)
def test_invalid_observations_refuse(bad):
    with pytest.raises(m.o.EvidenceError):
        m.observations(bad, 1)


def test_cli_refuses_dangling_output_entry_without_creating_target(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / ".git").mkdir()
    target = tmp_path / "absent-target"
    output = tmp_path / "output-link"
    output.symlink_to(target, target_is_directory=True)
    env = dict(os.environ, PYTHONPATH=str(m.ROOT / "src"))
    process = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--pi-source",
            str(source),
            "--output",
            str(output),
        ],
        env=env,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 2
    assert output.is_symlink()
    assert not target.exists()
    assert b"Output entry already exists" in process.stderr


def test_relative_output_keeps_requested_entry(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    config = {
        "sources": {"pinned.ts": hashlib.sha256(b"closed-source").hexdigest()},
        "closure": {},
    }
    monkeypatch.setattr(m, "manifest", lambda: config)
    monkeypatch.setattr(m, "SOURCES", {"pinned.ts"})
    monkeypatch.setattr(m, "recipes", lambda: [])
    monkeypatch.setattr(m, "native", lambda _: [])

    def execute(command, cwd, output, label, env, timeout):
        assert cwd.is_absolute() and output == tmp_path / "relative-output"
        if label == "node-version":
            return b"v26.12.0"
        if label == "npm-version":
            return b"11.20.0"
        if label == "source-0":
            assert command == ["git", "-C", str(source), "show", m.o.PIN + ":pinned.ts"]
            return b"closed-source"
        assert label == "node-scanner"
        loaded = [
            (cwd / name).as_uri()
            for name in [
                "packages/durable/src/env/line-scan.ts",
                "packages/durable/src/env/decode.ts",
            ]
        ]
        data = m.json.dumps({"observed": [], "loaded": loaded}).encode()
        (output / "node-scanner.stdout").write_bytes(data)
        return data

    monkeypatch.setattr(m.o, "run", execute)
    assert m.run(Path("source"), Path("relative-output")) == 0
    assert (tmp_path / "relative-output/result.json").is_file()


@pytest.mark.parametrize("boundary", ["subdirectory", "noncheckout", "inside"])
def test_cli_source_boundary_refuses_before_output_creation(tmp_path, boundary):
    # Never exercise this control against actual upstream: old code creates output.
    checkout = tmp_path / "private-checkout"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    subdirectory = checkout / "subdirectory"
    subdirectory.mkdir()
    source = subdirectory if boundary == "subdirectory" else checkout
    output = checkout / "must-remain-absent"
    if boundary == "noncheckout":
        source = tmp_path / "noncheckout"
        source.mkdir()
        output = tmp_path / "outside-output"
    process = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--pi-source",
            str(source),
            "--output",
            str(output),
        ],
        env=dict(os.environ, PYTHONPATH=str(m.ROOT / "src")),
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 2
    assert not output.exists()
    expected = (
        b"Output must be outside upstream"
        if boundary == "inside"
        else b"Reference source must be a checkout root"
    )
    assert expected in process.stderr


@pytest.mark.parametrize("duplicate", ["observed", "loaded", "start", "selected_bytes"])
def test_duplicate_response_keys_are_invalid2(monkeypatch, tmp_path, duplicate):
    source = tmp_path / "private-checkout"
    source.mkdir()
    (source / ".git").mkdir()
    output = tmp_path / "output"
    config = {"sources": {}, "closure": {}}
    monkeypatch.setattr(m, "manifest", lambda: config)
    monkeypatch.setattr(m, "SOURCES", set())
    recipe = {"hex": "", "start": 0, "end": None, "chunks": [0]}
    monkeypatch.setattr(m, "recipes", lambda: [recipe])

    def execute(command, cwd, output, label, env, timeout):
        if label == "node-version":
            return b"v26.12.0"
        if label == "npm-version":
            return b"11.20.0"
        assert label == "node-scanner"
        loaded = [
            (cwd / name).as_uri()
            for name in [
                "packages/durable/src/env/line-scan.ts",
                "packages/durable/src/env/decode.ts",
            ]
        ]
        row = m.json.dumps(m.native([recipe])[0])
        if duplicate in {"start", "selected_bytes"}:
            row = row.replace(
                '"' + duplicate + '": 0',
                '"' + duplicate + '": 99, "' + duplicate + '": 0',
            )
        observed = "[" + row + "]"
        loaded_text = m.json.dumps(loaded)
        observed_field = '"observed":' + observed
        loaded_field = '"loaded":' + loaded_text
        if duplicate == "observed":
            observed_field = '"observed":[], ' + observed_field
        if duplicate == "loaded":
            loaded_field = '"loaded":[], ' + loaded_field
        data = ("{" + observed_field + "," + loaded_field + "}").encode()
        (output / "node-scanner.stdout").write_bytes(data)
        return data

    monkeypatch.setattr(m.o, "run", execute)
    monkeypatch.setattr(
        sys, "argv", [str(SCRIPT), "--pi-source", str(source), "--output", str(output)]
    )
    assert m.main() == 2
    assert not (output / "result.json").exists()

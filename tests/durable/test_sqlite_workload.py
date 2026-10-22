"""Real workload plus independent evidence refusal/mismatch controls (DE03d1)."""

from __future__ import annotations

import copy
import importlib.util
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/durable_sqlite_workload.py"
spec = importlib.util.spec_from_file_location("sqlite_workload", SCRIPT)
assert spec and spec.loader
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


@pytest.fixture(scope="module")
def evidence(tmp_path_factory):
    folder = tmp_path_factory.mktemp("sqlite-workload") / "evidence"
    assert w.run(folder) == 0
    return folder


def artifacts(evidence, mode="clean"):
    return tuple(
        w.load(evidence / mode / (name + ".json"))
        for name in ("expected", "result", "process")
    )


def test_real_workload_scale_all_write_shapes_and_recovered_wal(evidence):
    assert w.verify(evidence) == 0
    expected, result, process = artifacts(evidence)
    assert expected["seed_counts"] == {"entry": 1000, "task": 300, "document": 300}
    assert len(expected["write_samples"]) == 9
    assert [sample["submitted_count"] for sample in expected["write_samples"]] == [
        1
    ] * 3 + [100] * 3 + [4] * 3
    assert len(result["reads"][0]["filtered_tasks"]) == 5
    assert process["leader_reaped"]
    expected, result, process = artifacts(evidence, "unclean")
    assert result["wal_bytes"] > 0 and process["signal"] == "SIGKILL"
    assert process["returncode"] == -9 and process["leader_reaped"]
    assert result["integrity"] == [["ok"]]


@pytest.mark.parametrize(
    "mutation",
    [
        "persisted-field",
        "persisted-count",
        "return-count",
        "counter",
        "seq",
        "read-page",
        "request-index",
        "sample-count-delta",
        "timing-budget",
        "size-budget",
    ],
)
def test_schema_valid_behavior_mutants_are_mismatch_not_invalid(evidence, mutation):
    expected, result, process = copy.deepcopy(artifacts(evidence))
    if mutation == "persisted-field":
        result["persisted"]["entry"][0]["data"]["text"] = "wrong"
    elif mutation == "persisted-count":
        result["persisted"]["documents"].pop()
    elif mutation == "return-count":
        expected["write_samples"][0]["submitted_count"] = 9
    elif mutation == "counter":
        result["next_id"] += 1
    elif mutation == "seq":
        result["persisted"]["entry_seqs"][str(expected["entry"][0]["id"])] += 1
    elif mutation == "read-page":
        result["reads"][0]["next_page"].reverse()
    elif mutation == "request-index":
        result["persisted"]["request_index"].clear()
    elif mutation == "sample-count-delta":
        expected["write_samples"][0]["after_counts"]["entry"] += 1
    elif mutation == "timing-budget":
        result["seed_seconds"] = 31
    else:
        result["database_bytes"] = 16777217
    with pytest.raises(w.Mismatch):
        w.validate(expected, result, process, False)


@pytest.mark.parametrize("mutation", ["persisted-bool", "persisted-float"])
def test_json_representation_mutants_are_mismatches(evidence, mutation):
    expected, result, process = copy.deepcopy(artifacts(evidence))
    result["persisted"]["entry"][0]["data"]["index"] = (
        False if mutation == "persisted-bool" else 0.0
    )
    with pytest.raises(w.Mismatch):
        w.validate(expected, result, process, False)


@pytest.mark.parametrize(
    "mutation",
    [
        "seed-scale",
        "backend",
        "durability",
        "configuration",
        "missing-readback",
        "missing-cut",
        "leaked-child",
        "nan",
        "infinity",
        "negative",
        "wrong-read-rounds",
        "wrong-sample-inventory",
    ],
)
def test_invalid_evidence_cannot_become_performance_success(evidence, mutation):
    expected, result, process = copy.deepcopy(artifacts(evidence))
    if mutation == "seed-scale":
        expected["seed_counts"]["task"] = 299
    elif mutation == "backend":
        result["hardware"]["backend"] = "memory"
    elif mutation == "durability":
        result["hardware"]["durability"] = "normal"
    elif mutation == "configuration":
        result["hardware"]["pragmas"]["synchronous"] = 1
    elif mutation == "missing-readback":
        del result["persisted"]
    elif mutation == "missing-cut":
        process["event"] = {"event": "pretended"}
    elif mutation == "leaked-child":
        process["leader_reaped"] = False
    elif mutation in ("nan", "infinity", "negative"):
        result["read_seconds"] = {
            "nan": math.nan,
            "infinity": math.inf,
            "negative": -1,
        }[mutation]
    elif mutation == "wrong-read-rounds":
        result["reads"].pop()
    else:
        expected["write_samples"].pop()
    with pytest.raises(w.Invalid):
        w.validate(expected, result, process, False)


def test_cli_existing_output_is_invalid_and_never_removed(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "caller-owned"
    sentinel.write_text("retained")
    run = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "run", "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert run.returncode == 2 and sentinel.read_text() == "retained"


@pytest.mark.parametrize(
    "drift", ["artifact", "database", "input", "script", "runtime", "inventory"]
)
def test_actual_verify_cli_rejects_identity_drift(evidence, tmp_path, drift):
    import shutil

    target = tmp_path / "copy"
    target.mkdir()
    for mode in ("clean", "unclean"):
        (target / mode).mkdir()
        for path in list((evidence / mode).glob("*.json")) + list(
            (evidence / mode).glob("*.sqlite")
        ):
            shutil.copyfile(path, target / mode / path.name)
    shutil.copyfile(evidence / "manifest.json", target / "manifest.json")
    manifest = w.load(target / "manifest.json")
    if drift == "artifact":
        path = target / "clean/result.json"
        value = w.load(path)
        value["next_id"] += 1
        w.dump(path, value)
    elif drift == "database":
        with (target / "clean/store.sqlite").open("ab") as stream:
            stream.write(b"drift")
    elif drift == "input":
        manifest["input_sha256"] = "0" * 64
    elif drift == "script":
        manifest["script_sha256"] = "0" * 64
    elif drift == "runtime":
        manifest["runtime_sha256"][next(iter(manifest["runtime_sha256"]))] = "0" * 64
    else:
        manifest["artifacts"].pop(next(iter(manifest["artifacts"])))
    w.dump(target / "manifest.json", manifest)
    run = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "verify", str(target)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert run.returncode == 2 and "Traceback" not in run.stderr


def test_missing_ready_event_reaps_actual_child_on_startup_failure(
    tmp_path, monkeypatch
):
    # A malformed internal command makes the real child exit before readiness;
    # the parent must record/reap even this early failure.
    monkeypatch.setitem(w.SPEC["limits"], "child_seconds", 1)
    monkeypatch.setattr(w, "configuration", lambda: w.SPEC)
    folder = tmp_path / "failed-child"
    # Inject only the executable command's
    # mode to an invalid argparse choice using a delegating Popen factory.
    original = subprocess.Popen

    def invalid_command(command, **kwargs):
        command = list(command)
        command[4] = "invalid-mode"
        return original(command, **kwargs)

    monkeypatch.setattr(w.subprocess, "Popen", invalid_command)
    with pytest.raises(w.Invalid, match="before readiness"):
        w.launch("clean", folder)
    report = w.load(folder / "process.json")
    assert report["leader_reaped"] and report["returncode"] != 0
    with pytest.raises(ProcessLookupError):
        os.killpg(report["group"], 0)


@pytest.mark.parametrize(
    "key,value",
    [("entry_count", 999), ("schema_version", True), ("schema_version", 1.0)],
)
def test_actual_closed_input_drift_is_refused(tmp_path, monkeypatch, key, value):
    changed = copy.deepcopy(w.SPEC)
    changed[key] = value
    path = tmp_path / "input.json"
    w.dump(path, changed)
    monkeypatch.setattr(w, "INPUT", path)
    with pytest.raises(w.Invalid, match="input drift"):
        w.configuration()


@pytest.mark.parametrize(
    "mutation", ["field", "bool-field", "float-field", "count", "return"]
)
def test_actual_cli_preserves_valid_behavior_mismatch_exit_one(
    evidence, tmp_path, mutation
):
    import shutil

    target = tmp_path / "mismatch"
    target.mkdir()
    for mode in ("clean", "unclean"):
        (target / mode).mkdir()
        for path in list((evidence / mode).glob("*.json")) + list(
            (evidence / mode).glob("*.sqlite")
        ):
            shutil.copyfile(path, target / mode / path.name)
    manifest = w.load(evidence / "manifest.json")
    name = (
        "clean/expected.json" if mutation.startswith("return") else "clean/result.json"
    )
    value = w.load(target / name)
    if mutation == "field":
        value["persisted"]["task"][0]["input"]["index"] = -1
    elif mutation == "bool-field":
        value["persisted"]["entry"][0]["data"]["index"] = False
    elif mutation == "float-field":
        value["persisted"]["entry"][0]["data"]["index"] = 0.0
    elif mutation == "count":
        value["persisted"]["entry"].pop()
    else:
        value["write_samples"][0]["submitted_count"] = (
            True if mutation == "return-bool" else 4
        )
    w.dump(target / name, value)
    manifest["artifacts"][name] = w.sha(target / name)
    w.dump(target / "manifest.json", manifest)
    run = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "verify", str(target)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert run.returncode == 1 and "Traceback" not in run.stderr


def test_input_drift_during_real_child_execution_is_not_bound_as_success(
    tmp_path, monkeypatch
):
    input_path = tmp_path / "input.json"
    w.dump(input_path, w.SPEC)
    monkeypatch.setattr(w, "INPUT", input_path)
    original = w.launch

    def change_after_child(mode, folder):
        result = original(mode, folder)
        changed = copy.deepcopy(w.SPEC)
        changed["read_rounds"] = 99
        w.dump(input_path, changed)
        return result

    monkeypatch.setattr(w, "launch", change_after_child)
    output = tmp_path / "evidence"
    with pytest.raises(w.Invalid, match="changed during workload"):
        w.run(output)
    assert not (output / "manifest.json").exists()
    process = w.load(output / "clean/process.json")
    assert process["returncode"] == 0 and process["leader_reaped"]


def copied_evidence(evidence, target):
    import shutil

    shutil.copytree(evidence, target)
    return w.load(target / "manifest.json")


def cli_verify(target):
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "verify", str(target)],
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize("mutation", ["artifact-list", "deep-json"])
def test_actual_cli_malformed_manifest_is_invalid(evidence, tmp_path, mutation):
    target = tmp_path / "malformed"
    manifest = copied_evidence(evidence, target)
    if mutation == "artifact-list":
        manifest["artifacts"] = list(manifest["artifacts"])
        w.dump(target / "manifest.json", manifest)
    else:
        (target / "manifest.json").write_text("[" * 2000 + "0" + "]" * 2000)
    result = cli_verify(target)
    assert result.returncode == 2 and "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "name,path,value",
    [
        ("clean/result.json", ("next_seq",), "float-current"),
        ("clean/result.json", ("next_id",), "float-current"),
        ("clean/result.json", ("hardware", "pragmas", "synchronous"), 2.0),
        ("clean/expected.json", ("next_seq",), True),
        ("clean/expected.json", ("write_samples", 0, "sample"), False),
        ("clean/expected.json", ("write_samples", 0, "submitted_count"), True),
        ("clean/expected.json", ("write_samples", 0, "seq"), 18.0),
        ("clean/expected.json", ("write_samples", 0, "after_counts", "task"), 300.0),
        ("clean/process.json", ("returncode",), False),
        ("unclean/process.json", ("returncode",), -9.0),
        ("unclean/recovery-process.json", ("returncode",), False),
        ("unclean/recovery-process.json", ("stdout_tail",), "unexpected"),
        ("unclean/recovery-process.json", ("signal",), "SIGKILL"),
    ],
)
def test_actual_cli_numeric_and_process_provenance_is_exact(
    evidence, tmp_path, name, path, value
):
    target = tmp_path / "provenance"
    manifest = copied_evidence(evidence, target)
    record = w.load(target / name)
    member = record
    for key in path[:-1]:
        member = member[key]
    member[path[-1]] = float(member[path[-1]]) if value == "float-current" else value
    w.dump(target / name, record)
    manifest["artifacts"][name] = w.sha(target / name)
    w.dump(target / "manifest.json", manifest)
    result = cli_verify(target)
    assert result.returncode == 2 and "Traceback" not in result.stderr


def test_private_small_config_drives_real_storage_workload(tmp_path):
    config = copy.deepcopy(w.SPEC)
    config.update(
        entry_count=12,
        task_count=7,
        document_count=5,
        batch_size=4,
        read_rounds=2,
        write_samples=1,
    )
    store = w.open_sqlite_storage(str(tmp_path / "small.sqlite"), mode="create")
    try:
        data = w.seed(store, config)
        assert [len(data[k]) for k in ("entry", "task", "documents")] == [12, 7, 5]
        reads = [
            w.read_shapes(store, data, config) for _ in range(config["read_rounds"])
        ]
        assert len(reads) == 2 and len(reads[0]["page"]) == 4
        assert len(reads[0]["next_page"]) == 4
        calls = []

        class ObservedStorage:
            def __getattr__(self, name):
                method = getattr(store, name)
                if not name.startswith("scan_"):
                    return method

                def scan(*args, **kwargs):
                    calls.append(name)
                    return method(*args, **kwargs)

                return scan

        w.write_samples(ObservedStorage(), data, config)
        assert set(calls) == {
            "scan_entries",
            "scan_tasks",
            "scan_submissions",
            "scan_documents",
        }
        assert len(calls) >= 24
        samples = data["write_samples"]
        assert len(samples) == 3
        assert samples[0]["before_counts"] == dict(
            entry=12, task=7, submission=0, documents=5
        )
        assert samples[-1]["after_counts"] == dict(
            entry=114, task=8, submission=1, documents=6
        )
        assert [s["seq"] for s in samples] == list(
            range(samples[0]["seq"], samples[0]["seq"] + 3)
        )
        assert w.same(w.readback(store, data)["entry"], data["entry"])
    finally:
        store.close()


@pytest.mark.parametrize("scenario", ["normal", "timeout", "startup"])
def test_owned_child_signals_only_before_reaping(tmp_path, monkeypatch, scenario):
    original_popen, original_killpg = w.subprocess.Popen, w.os.killpg
    owned = []
    signals = []

    def create(command, **kwargs):
        if scenario == "timeout":
            command = [
                sys.executable,
                "-c",
                "import os,json,signal; print(json.dumps({'event':'complete','pid':os.getpid()}),flush=True); signal.pause()",
            ]
        elif scenario == "startup":
            command = [sys.executable, "-c", "raise SystemExit(3)"]
        child = original_popen(command, **kwargs)
        owned.append(child)
        return child

    def guarded_killpg(group, sig):
        assert owned[0].returncode is None, "PGID accessed after reaping"
        signals.append(sig)
        return original_killpg(group, sig)

    monkeypatch.setattr(w.subprocess, "Popen", create)
    monkeypatch.setattr(w.os, "killpg", guarded_killpg)
    if scenario == "timeout":
        monkeypatch.setitem(w.SPEC["limits"], "child_seconds", 0.2)
        monkeypatch.setattr(w, "configuration", lambda: w.SPEC)
    folder = tmp_path / scenario
    if scenario == "normal":
        report = w.launch("clean", folder)
        assert report["returncode"] == 0 and not signals
    else:
        with pytest.raises((w.Invalid, subprocess.TimeoutExpired)):
            w.launch("clean", folder)
        assert signals == [w.signal.SIGKILL]
    assert owned[0].returncode is not None
    assert w.load(folder / "process.json")["leader_reaped"]


@pytest.mark.parametrize("invalid_seq", [True, 1.0])
def test_actual_commit_sequence_type_is_not_coerced(tmp_path, invalid_seq):
    store = w.open_sqlite_storage(str(tmp_path / "sequence.sqlite"), mode="create")

    class WrongReturn:
        def commit(self, writes):
            assert store.commit(writes) == 1
            return invalid_seq

    try:
        with pytest.raises(w.Mismatch, match="sequence"):
            w.apply(
                WrongReturn(),
                [{"type": "conversation", "value": {"id": 1}}],
                {"next_seq": 1},
            )
        assert store.conversation(1) is not None
    finally:
        store.close()


def test_actual_cli_relative_output_and_relative_verify(tmp_path):
    name = "relative-evidence"
    run = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "run", "--output", name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stderr
    output = tmp_path / name
    for mode in ("clean", "unclean"):
        assert (output / mode / "store.sqlite").is_file()
        assert not (output / mode / name).exists()
    verify = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "verify", name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert verify.returncode == 0, verify.stderr
    again = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "run", "--output", name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert again.returncode == 2
    assert w.verify(output) == 0


def test_actual_cli_dangling_output_symlink_is_refused(tmp_path):
    target = tmp_path / "absent-target"
    output = tmp_path / "existing-link"
    output.symlink_to(target, target_is_directory=True)
    run = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "run", "--output", output.name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert run.returncode == 2, run.stderr
    assert output.is_symlink() and output.readlink() == target
    assert not target.exists()

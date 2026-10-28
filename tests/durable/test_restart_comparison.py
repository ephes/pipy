"""Actual native generic restart fixture and retained comparison corruption controls."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "restart_comparison", ROOT / "scripts/durable_restart_comparison.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("restart-source")
    (root / "processes").mkdir()
    m.d.dump(root / "input.json", m.recipe())
    stdout, stderr, code = m.proc.run(
        m.d,
        [sys.executable, "-I", str(m.NATIVE), str(root / "input.json"), str(root)],
        ROOT,
        root / "processes",
        "native-restart",
        m.o.child_env(root),
        120,
    )
    assert code == 0 and not stdout and not stderr, stderr
    raw = m.d.load(root / "native-raw.json")
    actual = m.projection(raw)
    m.values.successful(actual, m.d, m.v)
    return actual, raw, root


def test_actual_generic_scheduler_restart_controls(native_run):
    _, rows, _ = native_run
    assert all(
        r["control"]["kind"] == "explicit-temporary-task-row-seed"
        and r["control"]["physical_SIGKILL"] is False
        for r in rows
    )
    assert rows[0]["private_retry_return"] is True
    assert (
        next(r for r in rows if r["id"] == "successful-noop-enables")[
            "private_retry_return"
        ]
        is False
    )
    assert (
        next(r for r in rows if r["id"] == "fresh-higher-unlimited")[
            "unlimited_control"
        ]["after"]["restarts"]
        == 1e100
    )


@pytest.mark.parametrize("name", m.values.MUTANTS)
def test_schema_valid_restart_semantic_mutants(native_run, name):
    actual, _, _ = native_run
    changed = m.values.mutate(actual, name, m.v)
    m.trace(changed)
    assert m.compare(actual, changed) == 1


def test_raw_control_and_source_corruption_refuse(native_run, tmp_path):
    actual, raw, root = native_run
    changed = copy.deepcopy(raw)
    changed[0]["before_open"]["restarts"] = 0
    with pytest.raises(m.o.EvidenceError):
        m.projection(changed)
    invalid = copy.deepcopy(actual)
    invalid["unknown"] = True
    with pytest.raises(m.o.EvidenceError):
        m.trace(invalid)
    m.d.dump(tmp_path / "native-modules.json", {"src/unlisted.py": "0" * 64})
    m.d.dump(tmp_path / "native-raw.json", raw)
    with pytest.raises(m.o.EvidenceError):
        m.native_modules(tmp_path)
    process = m.d.load(root / "processes/native-restart.json")
    process["leader_reaped"] = False
    with pytest.raises(m.o.EvidenceError):
        m.proc.attest(m.d, process, root / "processes", "native-restart")


def test_exact_root_artifact_exclusion_retains_nested_results_and_stores(tmp_path):
    root = tmp_path / "ancestor/stores/phase"
    (root / "nested").mkdir(parents=True)
    (root / "stores").mkdir()
    for p in (root / "result.json", root / "nested/result.json", root / "stores/db"):
        p.write_text("{}")
    assert set(m.artifacts(root)) == {"nested/result.json", "stores/db"}


def test_discriminator_failure_joins_owned_native_sessions(monkeypatch, tmp_path):
    import restart_reference_support as support

    original = support.TaskScheduler
    schedulers = []

    def capture(*args, **kwargs):
        scheduler = original(*args, **kwargs)
        schedulers.append(scheduler)
        return scheduler

    def refuse(*args, **kwargs):
        raise RuntimeError("fixture inspection discriminator")

    monkeypatch.setattr(support, "TaskScheduler", capture)
    monkeypatch.setattr(support, "inspect_task", refuse)
    folder = tmp_path / "failed-owner"
    with pytest.raises(RuntimeError, match="fixture inspection discriminator"):
        support.exercise(m.values.CASES[0], folder)
    assert schedulers and all(not s._thread.is_alive() for s in schedulers)
    from pipy_harness.native.durable.sqlite import open_sqlite_storage

    store = open_sqlite_storage(str(folder / "session.sqlite"), mode="open")
    store.close()

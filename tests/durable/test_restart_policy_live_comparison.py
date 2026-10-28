"""Actual generic setter/host-object comparison and schema-valid discriminator controls."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "restart_policy_live_comparison",
    ROOT / "scripts/durable_restart_policy_live_comparison.py",
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("restart-policy-live-source")
    (root / "processes").mkdir()
    m.d.dump(root / "input.json", m.recipe())
    stdout, stderr, code = m.proc.run(
        m.d,
        [sys.executable, "-I", str(m.NATIVE), str(root / "input.json"), str(root)],
        ROOT,
        root / "processes",
        "native-restart-policy-live",
        m.o.child_env(root),
        120,
    )
    assert code == 0 and not stdout and not stderr, stderr
    raw = m.d.load(root / "native-raw.json")
    actual = m.projection(raw)
    m.values.successful(actual, m.d, m.v)
    return actual, raw, root


def test_actual_generic_live_policy_and_side_specific_ack(native_run):
    _, rows, _ = native_run
    assert len(rows) == len(m.values.CASES)
    for row, recipe in zip(rows, m.values.CASES, strict=True):
        assert row["control"]["physical_SIGKILL"] is False
        assert row["native_only"]["setter_return"] is (
            recipe["initial"] != recipe["update"]
        )
        assert row["native_only"]["setter_notify_calls"] == 0
        assert row["before_update"] == row["after_update"]
        expected_limit = None if recipe["update"] == "Infinity" else recipe["update"]
        assert row["native_only"]["owner_after"]["restart_limit"] == expected_limit
        assert row["native_only"]["standalone_default_after"]["restart_limit"] == 5
    lower = next(row for row in rows if row["id"] == "lower-before-admission")
    assert lower["calls_after_resume"] == 0 and lower["after_resume"]["restarts"] == 5
    noop = next(row for row in rows if row["id"] == "same-finite-remains-parked")
    assert noop["calls_after_resume"] == 0 and noop["after_resume"]["restarts"] == 5


@pytest.mark.parametrize("name", m.values.MUTANTS)
def test_schema_valid_live_policy_semantic_mutants(native_run, name):
    actual, _, _ = native_run
    changed = m.values.mutate(actual, name, m.v)
    m.trace(changed)
    assert m.compare(actual, changed) == 1


def test_raw_seed_and_closed_source_guards_refuse(native_run, tmp_path):
    actual, raw, root = native_run
    changed = copy.deepcopy(raw)
    changed[0]["after_open"]["restarts"] = 0
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
    process = m.d.load(root / "processes/native-restart-policy-live.json")
    process["leader_reaped"] = False
    with pytest.raises(m.o.EvidenceError):
        m.proc.attest(m.d, process, root / "processes", "native-restart-policy-live")


def test_artifact_root_exclusion_retains_nested_result_and_stores(tmp_path):
    root = tmp_path / "ancestor/stores/phase"
    (root / "nested").mkdir(parents=True)
    (root / "stores").mkdir()
    for file in (root / "result.json", root / "nested/result.json", root / "stores/db"):
        file.write_text("{}")
    assert set(m.artifacts(root)) == {"nested/result.json", "stores/db"}


def test_observer_discriminator_failure_joins_owned_sessions(monkeypatch, tmp_path):
    import restart_policy_live_reference_support as support

    schedulers = []
    original = support.TaskScheduler

    def capture(*args, **kwargs):
        value = original(*args, **kwargs)
        schedulers.append(value)
        return value

    def refuse(*args, **kwargs):
        raise RuntimeError("live-policy fixture inspection discriminator")

    monkeypatch.setattr(support, "TaskScheduler", capture)
    monkeypatch.setattr(support, "inspect_task", refuse)
    folder = tmp_path / "failed-owner"
    with pytest.raises(
        RuntimeError, match="live-policy fixture inspection discriminator"
    ):
        support.exercise(m.values.CASES[0], folder)
    assert schedulers and all(
        not scheduler._thread.is_alive() for scheduler in schedulers
    )
    from pipy_harness.native.durable.sqlite import open_sqlite_storage

    store = open_sqlite_storage(str(folder / "session.sqlite"), mode="open")
    store.close()

"""Actual generic cold fixtures and retained CLI/schema/provenance discriminators."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "migration_comparison", ROOT / "scripts/durable_migration_comparison.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("migration-source")
    (root / "processes").mkdir()
    m.d.dump(root / "input.json", m.recipe())
    stdout, stderr, code = m.proc.run(
        m.d,
        [sys.executable, "-I", str(m.NATIVE), str(root / "input.json"), str(root)],
        ROOT,
        root / "processes",
        "native-migration",
        m.o.child_env(root),
        120,
    )
    assert code == 0 and not stdout and not stderr, stderr
    raw = m.d.load(root / "native-raw.json")
    actual = m.projection(raw)
    m.values.successful(actual, m.d, m.v)
    return actual, raw, root


def test_actual_cold_migration_and_failure_cache(native_run):
    _, rows, _ = native_run
    assert all(row["control"]["physical_SIGKILL"] is False for row in rows)
    assert all(
        row["native_lifetimes"]
        and all(
            not lifetime["host_thread_alive_after_close"]
            for lifetime in row["native_lifetimes"]
        )
        for row in rows
    )
    failures = [row for row in rows if row["id"] in {"throw-once", "invalid-once"}]
    assert len(failures) == 2
    assert all(
        row["second_handlers"] == 1
        and row["before_restore_calls"] == row["repeated_calls"] == 1
        for row in failures
    )
    assert (
        next(row for row in rows if row["id"] == "parked-migration")[
            "private_retry_return"
        ]
        is True
    )


def test_blocked_zero_current_restores_above_actual_current_definition(native_run):
    actual, rows, _ = native_run
    row = next(row for row in rows if row["id"] == "blocked-zero-current")
    assert row["before_open"]["version"] == -2
    assert row["blocked_reason"] == "migration_failed" and row["blocked_unchanged"]
    assert row["first_handler"]["version"] == row["final"]["version"] == 1
    assert row["migration_calls"] == [
        {
            "input": {"fixture": "blocked-zero-current", "n": 1},
            "checkpoint": {"phase": "run", "n": 2},
            "from_version": -2,
            "owner": "restore",
        }
    ]
    wrong = copy.deepcopy(actual)
    case = next(case for case in wrong["cases"] if case["id"] == row["id"])
    observed = m.v.decode(case["observed"])
    observed["first_handler"]["version"] = -1
    case["observed"] = m.v.encode(observed)
    m.trace(wrong)
    with pytest.raises(m.o.EvidenceError, match="Migration first handler"):
        m.values.successful(wrong, m.d, m.v)


@pytest.mark.parametrize("name", m.values.MUTANTS)
def test_schema_valid_migration_semantic_mutants(native_run, name):
    actual, _, _ = native_run
    changed = m.values.mutate(actual, name, m.v)
    m.trace(changed)
    assert m.compare(actual, changed) == 1


def test_raw_handler_control_and_source_corruption_refuse(native_run, tmp_path):
    actual, raw, root = native_run
    changed = copy.deepcopy(raw)
    changed[0]["before_open"]["input"]["n"] = 99
    with pytest.raises(m.o.EvidenceError):
        m.projection(changed)
    changed = copy.deepcopy(raw)
    changed[1]["handler_records"][0]["input"]["n"] = 99
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
    process = m.d.load(root / "processes/native-migration.json")
    process["leader_reaped"] = False
    with pytest.raises(m.o.EvidenceError):
        m.proc.attest(m.d, process, root / "processes", "native-migration")


def test_exact_root_artifact_exclusion_keeps_nested_results_and_stores(tmp_path):
    root = tmp_path / "ancestor/stores/phase"
    (root / "nested").mkdir(parents=True)
    (root / "stores").mkdir()
    for path in (root / "result.json", root / "nested/result.json", root / "stores/db"):
        path.write_text("{}")
    assert set(m.artifacts(root)) == {"nested/result.json", "stores/db"}


def test_failed_discriminator_joins_owned_sessions(monkeypatch, tmp_path):
    import migration_reference_support as support

    original = support.TaskScheduler
    schedulers = []

    def capture(*args, **kwargs):
        scheduler = original(*args, **kwargs)
        schedulers.append(scheduler)
        return scheduler

    def refuse(*args, **kwargs):
        raise RuntimeError("fixture inspection discriminator")

    monkeypatch.setattr(support, "TaskScheduler", capture)
    monkeypatch.setattr(original, "inspect_task", refuse)
    folder = tmp_path / "failed-owner"
    with pytest.raises(RuntimeError, match="fixture inspection discriminator"):
        support.exercise(m.values.CASES[0], folder)
    assert schedulers and all(
        not scheduler._thread.is_alive() for scheduler in schedulers
    )
    from pipy_harness.native.durable.sqlite import open_sqlite_storage

    store = open_sqlite_storage(str(folder / "session.sqlite"), mode="open")
    store.close()


def test_task_version_zero_equivalence_keeps_nonzero_differences(native_run):
    actual, raw, _ = native_run
    zero = copy.deepcopy(raw)
    zero[0]["handler_records"][0]["version"] = 0.0
    zero[0]["first_handler"] = m.values.handler(zero[0]["handler_records"][0])
    assert m.compare(actual, m.projection(zero)) == 0
    nonzero = copy.deepcopy(actual)
    observed = m.v.decode(nonzero["cases"][0]["observed"])
    observed["first_handler"]["version"] = 1
    nonzero["cases"][0]["observed"] = m.v.encode(observed)
    m.trace(nonzero)
    assert m.compare(actual, nonzero) == 1
    for bad in (True, None, "0", float("inf")):
        with pytest.raises(ValueError, match="finite numeric Task version"):
            m.values.handler({"version": bad})

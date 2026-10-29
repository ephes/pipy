"""Closed-schema controls are not producer evidence; native fixture runs are explicit."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "orphan_missing_definition_comparison",
    ROOT / "scripts/durable_orphan_missing_definition_comparison.py",
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def _schema_trace_not_producer():
    """Typed comparison-only values; never used as Task/Tx or accepted run artifacts."""
    rows = []
    for c in m.values.CASES:
        before = {
            "id": "@target",
            "conversationId": "@conversation",
            "kind": m.values.TASK_NAME,
            "version": 1,
            "input": {"fixture": c["id"]},
            "background": False,
            "abortRequested": False,
            "state": {"status": "pending", "checkpoint": {"phase": "run"}},
        }
        if c["parked"]:
            before.update(restarts=5, memos={"fixture.memo": {"kept": True}})
        after = {
            k: copy.deepcopy(v)
            for k, v in before.items()
            if k not in ("memos", "restarts")
        }
        after.update(
            state={
                "status": "terminal",
                "outcome": {"status": "orphaned", "reason": "missing_task"},
            },
            endedAt=1000,
        )
        counts = {"initial": 1, "migrate": 0, "handler": 0, "abort": 0}
        x = {
            "labels": m.values.labels(c),
            "before": before,
            "after": after,
            "first_ack": "orphaned",
            "repeat_acks": ["terminal"] * (2 if c["backend"] == "sqlite" else 1),
            "document_visibility": [
                {"kind": k, "before": True, "after": False} for k in m.values.DOC_KINDS
            ],
            "callbacks": {"before": dict(counts), "after": dict(counts)},
            "atomic_retirement": True,
            "live_metadata_cleared": True,
            "repeat_no_write": True,
            "unrelated_preserved": True,
            "cold_preserved": True if c["cold"] else None,
            "closed": ["closed"],
        }
        rows.append({"id": c["id"], "observed": m.v.encode(x)})
    return {"schema_version": 1, "phase": m.PHASE, "cases": rows}


def test_orphan_recipe_typed_metadata_prevalidation():
    invalid = copy.deepcopy(m.values.RECIPE)
    invalid["cases"][0]["cold"] = 0
    with pytest.raises(ValueError, match="typed value"):
        m.values.checked_recipe(invalid)
    invalid = copy.deepcopy(m.values.RECIPE)
    invalid["unknown"] = True
    with pytest.raises(ValueError, match="Closed orphan recipe"):
        m.values.checked_recipe(invalid)


def test_orphan_schema_positive_not_runtime_evidence():
    trace = _schema_trace_not_producer()
    m.values.successful(trace, m.d, m.v)
    assert m.compare(trace, trace) == 0


@pytest.mark.parametrize("name", m.values.MUTANTS)
def test_orphan_schema_semantic_mutants(name):
    expected = _schema_trace_not_producer()
    actual = m.values.mutate(expected, name, m.v)
    m.trace(actual)
    assert m.compare(expected, actual) == 1


def test_orphan_schema_cli_codes_without_producers(tmp_path):
    expected = _schema_trace_not_producer()
    paths = {
        "expected": tmp_path / "schema-only-expected.json",
        "different": tmp_path / "schema-only-mutant.json",
        "invalid": tmp_path / "schema-only-invalid.json",
    }
    m.d.dump(paths["expected"], expected)
    m.d.dump(paths["different"], m.values.mutate(expected, "reason", m.v))
    invalid = copy.deepcopy(expected)
    invalid["unknown"] = True
    m.d.dump(paths["invalid"], invalid)
    records = tmp_path / "processes"
    records.mkdir()
    environment = m.o.child_env(tmp_path)
    for label, key, wanted in [
        ("positive", "expected", 0),
        ("negative", "different", 1),
        ("malformed", "invalid", 2),
    ]:
        stdout, stderr, code = m.proc.run(
            m.d,
            [
                sys.executable,
                str(Path(m.__file__)),
                "compare",
                str(paths["expected"]),
                str(paths[key]),
            ],
            ROOT,
            records,
            label,
            environment,
            60,
        )
        assert code == wanted and not stdout
        assert bool(stderr) is (wanted == 2)


def test_orphan_schema_closed_shapes():
    trace = _schema_trace_not_producer()
    trace["unknown"] = True
    with pytest.raises(m.o.EvidenceError):
        m.trace(trace)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    """Real native producer; excluded from the initial schema-only cheap selectors."""
    output = tmp_path_factory.mktemp("orphan-native-reference")
    (output / "processes").mkdir()
    m.d.dump(output / "input.json", m.recipe())
    stdout, stderr, code = m.proc.run(
        m.d,
        [sys.executable, "-I", str(m.NATIVE), str(output / "input.json"), str(output)],
        ROOT,
        output / "processes",
        "native-orphan-missing-definition",
        m.o.child_env(output),
        120,
    )
    assert code == 0 and not stdout and not stderr, stderr
    rows = m.d.load(output / "native-raw.json")
    trace = m.projection(rows)
    m.values.successful(trace, m.d, m.v)
    return trace, rows, output


def test_actual_native_orphan_rows_and_backend_evidence(native_run):
    _, rows, output = native_run
    assert [r["id"] for r in rows] == list(m.values.IDS)
    for row in rows:
        folder = output / "stores/native" / row["id"]
        assert (
            m.sqlite_snapshot(folder / "callback-counter.sqlite")
            == row["callback_sqlite"]
        )
        if row["backend_kind"] == "sqlite":
            assert m.sqlite_snapshot(folder / "session.sqlite") == row["sqlite_final"]
        else:
            assert row["sqlite_final"] is None
        assert row["control"]["fabricated_task_authority"] is False
        assert row["native_only"]["paused_owner"] is True
        after = next(s for s in row["steps"] if s["label"] == "orphan")
        assert after["ACK"] == "orphaned" and after["documents"]["target"] == []
        assert after["record"]["abortRequested"] is False


@pytest.mark.parametrize("name", m.values.MUTANTS)
def test_actual_native_orphan_semantic_mutants(native_run, name):
    expected, _, _ = native_run
    actual = m.values.mutate(expected, name, m.v)
    m.trace(actual)
    assert m.compare(expected, actual) == 1


def test_actual_native_raw_and_process_refusal(native_run):
    _, rows, output = native_run
    broken = copy.deepcopy(rows)
    broken[0]["steps"][0]["callback_rows"][0]["kind"] = "abort"
    with pytest.raises(ValueError, match="SQLite/count"):
        m.projection(broken)
    broken = copy.deepcopy(rows)
    broken[0]["unknown"] = True
    with pytest.raises(m.o.EvidenceError):
        m.projection(broken)
    p = m.d.load(output / "processes/native-orphan-missing-definition.json")
    p["leader_reaped"] = False
    with pytest.raises(m.o.EvidenceError):
        m.proc.attest(m.d, p, output / "processes", "native-orphan-missing-definition")


def test_orphan_artifact_inventory_retains_nested_result_and_stores(tmp_path):
    output = tmp_path / "phase"
    (output / "nested").mkdir(parents=True)
    (output / "stores").mkdir()
    for path in (
        output / "result.json",
        output / "nested/result.json",
        output / "stores/db",
    ):
        path.write_text("{}")
    assert set(m.artifacts(output)) == {"nested/result.json", "stores/db"}


def test_actual_orphan_fixture_failure_joins_owner(monkeypatch, tmp_path):
    from importlib import import_module

    support = import_module("orphan_missing_definition_reference_support")
    owned = []
    original = support.TaskScheduler

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        owned.append(result)
        return result

    def refuse(*args):
        raise RuntimeError("orphan fixture policy discriminator")

    monkeypatch.setattr(support, "TaskScheduler", capture)
    monkeypatch.setattr(support, "RestartPolicy", refuse)
    with pytest.raises(RuntimeError, match="fixture policy"):
        support.exercise(m.values.CASES[1], tmp_path / "failed-owner")
    assert owned and all(not owner._thread.is_alive() for owner in owned)
    from pipy_harness.native.durable.sqlite import open_sqlite_storage

    storage = open_sqlite_storage(
        str(tmp_path / "failed-owner/session.sqlite"), mode="open"
    )
    storage.close()

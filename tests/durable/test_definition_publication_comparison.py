"""Created-task publication, callback-ledger joins and schema-valid controls."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "definition_publication_comparison",
    ROOT / "scripts/durable_definition_publication_comparison.py",
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    output = tmp_path_factory.mktemp("definition-publication-reference")
    (output / "processes").mkdir()
    m.d.dump(output / "input.json", m.recipe())
    stdout, stderr, code = m.proc.run(
        m.d,
        [sys.executable, "-I", str(m.NATIVE), str(output / "input.json"), str(output)],
        ROOT,
        output / "processes",
        "native-definition-publication",
        m.o.child_env(output),
        120,
    )
    assert code == 0 and not stdout and not stderr, stderr
    rows = m.d.load(output / "native-raw.json")
    actual = m.projection(rows)
    m.values.successful(actual, m.d, m.v)
    return actual, rows, output


def test_actual_definition_identity_and_callback_sqlite(native_run):
    _, rows, output = native_run
    assert [r["id"] for r in rows] == list(m.values.IDS)
    for row in rows:
        folder = output / "stores/native" / row["id"]
        assert (
            m.sqlite_snapshot(folder / "callback-counter.sqlite")
            == row["callback_sqlite"]
        )
        assert m.sqlite_snapshot(folder / "session.sqlite") == row["sqlite_final"]
        assert [r["kind"] for r in row["callback_rows"]] == row["callback_log"]
        assert row["control"]["fabricated_task_or_failure_memo"] is False
        for step in row["steps"]:
            side = step["native_only"]
            if side and "ACK" in side:
                assert type(side["ACK"]) is bool and side["no_busy_retry"] is True
                if side["actual_retirement"] is not None:
                    assert side["actual_retirement"] == {
                        "draining": False,
                        "invocation_none": True,
                    }
    cached = rows[1]
    for label in ("identical", "unrelated"):
        step = next(s for s in cached["steps"] if s["label"] == label)
        assert (
            step["counts"]["migrate"] == 1 and step["native_only"]["notify_delta"] == 1
        )
    assert (
        next(s for s in cached["steps"] if s["label"] == "identical")["native_only"][
            "ACK"
        ]
        is False
    )
    absent = next(s for s in rows[4]["steps"] if s["label"] == "absent-remove")
    assert (
        absent["native_only"]["ACK"] is False
        and absent["native_only"]["notify_delta"] == 0
    )


@pytest.mark.parametrize("name", m.values.MUTANTS)
def test_schema_valid_definition_publication_mutants(native_run, name):
    actual, _, _ = native_run
    changed = m.values.mutate(actual, name, m.v)
    m.trace(changed)
    assert m.compare(actual, changed) == 1


def test_raw_ledger_and_closed_shape_refuse(native_run, tmp_path):
    actual, rows, output = native_run
    changed = copy.deepcopy(rows)
    changed[0]["steps"][0]["callback_rows"][0]["kind"] = "handler"
    with pytest.raises(ValueError, match="SQLite/count"):
        m.projection(changed)
    changed = copy.deepcopy(rows)
    changed[0]["unknown"] = True
    with pytest.raises(m.o.EvidenceError):
        m.projection(changed)
    malformed = copy.deepcopy(actual)
    malformed["unknown"] = True
    with pytest.raises(m.o.EvidenceError):
        m.trace(malformed)
    m.d.dump(tmp_path / "native-modules.json", {"src/unlisted.py": "0" * 64})
    m.d.dump(tmp_path / "native-raw.json", rows)
    with pytest.raises(m.o.EvidenceError):
        m.native_modules(tmp_path)
    process = m.d.load(output / "processes/native-definition-publication.json")
    process["leader_reaped"] = False
    with pytest.raises(m.o.EvidenceError):
        m.proc.attest(
            m.d, process, output / "processes", "native-definition-publication"
        )


def test_artifact_inventory_retains_stores_and_nested_result(tmp_path):
    output = tmp_path / "ancestor/stores/phase"
    (output / "nested").mkdir(parents=True)
    (output / "stores").mkdir()
    for p in (
        output / "result.json",
        output / "nested/result.json",
        output / "stores/db",
    ):
        p.write_text("{}")
    assert set(m.artifacts(output)) == {"nested/result.json", "stores/db"}


def test_observer_failure_joins_real_owners(monkeypatch, tmp_path):
    import definition_publication_reference_support as support

    observed = []
    original = support.TaskScheduler

    def capture(*args, **kwargs):
        scheduler = original(*args, **kwargs)
        observed.append(scheduler)
        return scheduler

    def refuse(*args):
        raise RuntimeError("publication fixture generation discriminator")

    monkeypatch.setattr(support, "TaskScheduler", capture)
    monkeypatch.setattr(support, "generation_data", refuse)
    with pytest.raises(RuntimeError, match="generation discriminator"):
        support.exercise(m.values.CASES[0], tmp_path / "failed-owner")
    assert observed and all(not s._thread.is_alive() for s in observed)
    from pipy_harness.native.durable.sqlite import open_sqlite_storage

    store = open_sqlite_storage(
        str(tmp_path / "failed-owner/session.sqlite"), mode="open"
    )
    store.close()

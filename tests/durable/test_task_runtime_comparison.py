"""Closed runtime trace schema and semantic negatives; real producer is a gate."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "runtime_comparison", ROOT / "scripts/durable_task_runtime_comparison.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_manifest_closes_actual_runtime_and_implementation():
    m.configuration()
    assert "src/pipy_harness/native/durable/scheduler.py" in m.CLOSURE
    assert "packages/durable/src/harness/scheduler.ts" in m.MODULES


@pytest.mark.parametrize("name", m.MUTANTS)
def test_each_schema_valid_runtime_mutant_changes_observation(name, tmp_path):
    import asyncio

    actual = asyncio.run(m.native(tmp_path / "stores"))
    assert m.compare(actual, actual) == 0
    assert m.compare(actual, m.mutate(actual, name)) == 1


def test_invalid_trace_does_not_become_a_mismatch():
    with pytest.raises(m.o.EvidenceError):
        m.trace({})


def test_nested_result_mutant_is_part_of_artifact_closure(tmp_path):
    (tmp_path / "mutants").mkdir()
    (tmp_path / "stores").mkdir()
    (tmp_path / "result.json").write_text("root receipt")
    (tmp_path / "mutants/result.json").write_text("retained mutant")
    (tmp_path / "stores/native.sqlite").write_text("excluded store")
    assert m.artifact_hashes(tmp_path) == {
        "mutants/result.json": m.o.sha(tmp_path / "mutants/result.json")
    }


def test_run_under_stores_ancestor_keeps_its_artifacts(tmp_path):
    run = tmp_path / "stores/run"
    run.mkdir(parents=True)
    (run / "input.json").write_text("actual input")
    assert m.artifact_hashes(run) == {"input.json": m.o.sha(run / "input.json")}


def test_actual_transitive_product_imports_are_bound_and_unlisted_import_refuses(
    monkeypatch,
):
    modules = m.native_modules()
    assert "src/pipy_harness/native/durable/tool_graph.py" in modules
    assert "src/pipy_harness/native/durable/generation.py" in modules
    assert "src/pipy_harness/native/provider_construction.py" in modules
    assert all(
        path in m.CLOSURE and digest == m.o.sha(ROOT / path)
        for path, digest in modules.items()
    )
    monkeypatch.setattr(
        m, "CLOSURE", m.CLOSURE - {"src/pipy_harness/native/provider_construction.py"}
    )
    with pytest.raises(m.o.EvidenceError, match="outside closure"):
        m.native_modules()

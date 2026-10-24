"""Actual completed producer and schema-valid semantic rejection controls."""

import asyncio
import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "completed_comparison", ROOT / "scripts/durable_completed_comparison.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("completed-common")
    return asyncio.run(m.native(root / "stores")), root


def test_actual_completed_watch_facts_and_saved_order(native_run):
    trace, _ = native_run
    observed = m.v.decode(trace["cases"][0]["observed"])
    assert observed["result_names"] == ["keyed", "unavailable", "later"]
    assert observed["roles"] == [
        "user",
        "assistant",
        "toolResult",
        "toolResult",
        "toolResult",
        "assistant",
    ]
    assert observed["provider_calls"] == observed["tool_calls"] == 2
    assert observed["usage"]["totalTokens"] == 60
    assert all(
        observed[key]
        for key in (
            "completed",
            "automatic",
            "independent",
            "isolation",
            "reconnect",
            "stop",
            "effects_unchanged",
            "usage_once",
        )
    )


@pytest.mark.parametrize("name", m.MUTANTS)
def test_semantic_mutants_are_valid_and_mismatch(native_run, name):
    actual, _ = native_run
    changed = m.mutate(actual, name)
    m.trace(changed)
    assert m.compare(actual, changed) == 1


def test_actual_faulted_task_record_cannot_project_as_completed(native_run):
    actual, root = native_run
    raw = m.d.load(root / "native-records/sqlite/completed.json")
    owner = next(row for row in raw["tasks"] if row["kind"] == "pi.generation")
    owner["state"] = {
        "status": "terminal",
        "outcome": {"status": "faulted", "error": {"message": "synthetic fault"}},
    }
    from pipy_harness.native.durable.records import TaskRecord

    TaskRecord(owner)  # A genuine schema-valid raw task, not a projected bool edit.
    projected = m.project(m.recipe()["cases"][0], raw)
    assert not projected["completed"]
    changed = copy.deepcopy(actual)
    changed["cases"][0]["observed"] = m.v.encode(projected)
    assert m.compare(actual, changed) == 1


def test_actual_aggregate_doubling_without_settlement_change_fails(native_run):
    actual, root = native_run
    raw = m.d.load(root / "native-records/sqlite/completed.json")
    settlements = copy.deepcopy(raw["retained"]["usage"]["settlements"])
    raw["retained"]["usage"]["models"]["openai/gpt-test"]["total_tokens"] *= 2
    assert raw["retained"]["usage"]["settlements"] == settlements
    projected = m.project(m.recipe()["cases"][0], raw)
    assert not projected["usage_once"]
    changed = copy.deepcopy(actual)
    changed["cases"][0]["observed"] = m.v.encode(projected)
    assert m.compare(actual, changed) == 1


def test_root_relative_artifact_exclusions_retain_nested_results(tmp_path):
    for name in ("mutants", "stores"):
        (tmp_path / name).mkdir()
    for name in (
        "result.json",
        "input.json",
        "mutants/result.json",
        "stores/session.sqlite",
    ):
        (tmp_path / name).write_text(name)
    assert set(m.artifact_hashes(tmp_path)) == {"input.json", "mutants/result.json"}
    nested = tmp_path / "stores/phase"
    nested.mkdir()
    (nested / "input.json").write_text("retained despite ancestor stores")
    assert set(m.artifact_hashes(nested)) == {"input.json"}


def test_malformed_body_is_invalid():
    with pytest.raises(m.o.EvidenceError):
        m.trace({})

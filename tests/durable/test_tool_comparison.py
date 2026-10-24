"""Actual native tool projections and schema-valid discriminating controls."""

import asyncio
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "tool_comparison", ROOT / "scripts/durable_tool_comparison.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("tool-common")
    return asyncio.run(m.native(root / "stores")), root


@pytest.fixture(scope="module")
def actual(native_run):
    return native_run[0]


def test_actual_replay_intersection_attempts_differ_from_applications(actual):
    rows = [m.v.decode(row["observed"]) for row in actual["cases"]]
    assert [row["calls"] for row in rows] == [2, 2, 1, 1, 1]
    assert [row["applications"] for row in rows] == [2, 1, 1, 1, 1]
    assert [row["prepares"] for row in rows] == [2, 1, 1, 1, 1]
    assert all(
        row["same_key"] and row["distinct_keys"] and row["intent_before_effect"]
        for row in rows
    )
    assert rows[0]["closed"]["result_names"] == ["keyed", "unavailable", "later"]
    assert rows[0]["closed"]["usage"]["reasoning"] == 6
    assert rows[1]["closed"]["usage"]["reasoning"] == 0
    assert all(
        row["worker_joined"] and row["reuse_calls"] == row["post_close_calls"] == 0
        for row in rows
    )


@pytest.mark.parametrize("name", m.MUTANTS)
def test_semantic_mutants_remain_valid_and_mismatch(actual, name):
    changed = m.mutate(actual, name)
    m.trace(changed)
    assert m.compare(actual, changed) == 1


@pytest.mark.parametrize("reasoning,cache", [(None, None), (0, 0), (3, 4)])
def test_shared_ledger_predicate_preserves_optional_presence(reasoning, cache):
    first = {"input": 2, "cost": {"total": 0.2}}
    for key, value in (("reasoning", reasoning), ("cacheWrite1h", cache)):
        if value is not None:
            first[key] = value
    total = m.usage_total([first, first])
    assert m.usage_matches([first, first], total)
    assert ("reasoning" in total) is (reasoning is not None)
    assert ("cacheWrite1h" in total) is (cache is not None)
    changed = dict(total)
    if "cacheWrite1h" in changed:
        changed.pop("cacheWrite1h")
    else:
        changed["cacheWrite1h"] = 0
    assert not m.usage_matches([first, first], changed)


def test_only_root_receipt_and_run_relative_stores_are_excluded(tmp_path):
    for name in ("mutants", "stores"):
        (tmp_path / name).mkdir()
    for name in (
        "input.json",
        "mutants/input.json",
        "mutants/result.json",
        "result.json",
        "stores/session.sqlite",
    ):
        (tmp_path / name).write_text(name)
    assert set(m.artifact_hashes(tmp_path)) == {
        "input.json",
        "mutants/input.json",
        "mutants/result.json",
    }
    nested = tmp_path / "stores/run"
    nested.mkdir()
    (nested / "result.json").write_text("root receipt")
    (nested / "input.json").write_text("input")
    assert set(m.artifact_hashes(nested)) == {"input.json"}


def test_malformed_trace_is_invalid():
    with pytest.raises(m.o.EvidenceError):
        m.trace({})


def test_faulted_raw_terminal_parent_cannot_match_completed_round(native_run):
    import copy

    from pipy_harness.native.durable.records import TaskRecord

    actual, root = native_run
    raw = m.d.load(root / "native-records/sqlite/loop.json")
    before = m.project(m.recipe()["cases"][0], raw)
    parent = next(row for row in raw["tasks"] if row["kind"] == "pi.generation")
    parent["state"]["outcome"] = {
        "status": "faulted",
        "error": {"message": "fixture fault"},
    }
    assert TaskRecord(parent).to_tree() == parent
    m.d.dump(root / "faulted-parent-negative.json", raw)
    projected = m.project(m.recipe()["cases"][0], raw)
    assert before["closed"]["terminal"]
    assert projected["closed"]["terminal"] is False
    only_status = copy.deepcopy(before)
    only_status["closed"]["terminal"] = False
    assert projected == only_status
    changed = copy.deepcopy(actual)
    changed["cases"][0]["observed"] = m.v.encode(projected)
    m.trace(changed)
    assert m.compare(actual, changed) == 1

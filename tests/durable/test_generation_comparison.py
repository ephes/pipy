"""Actual generation outputs discriminate the declared common admission view."""

import asyncio
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "generation_comparison", ROOT / "scripts/durable_generation_comparison.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("generation-common")
    return asyncio.run(m.native(root / "stores")), root


@pytest.fixture(scope="module")
def actual(native_run):
    return native_run[0]


def test_actual_default_round_admits_every_real_owned_child(actual):
    answer, tools = [m.v.decode(row["observed"]) for row in actual["cases"]]
    assert answer["assistant"]["text"] == "done"
    assert answer["admission"]["children"] == []
    assert answer["usage"]["reasoning"] == 3
    assert answer["ledger_usage"] == answer["usage"]
    assert answer["resumed_after_terminal"] and answer["retained_terminal"]
    assert answer["retained_calls"] == 1
    assert not tools["resumed_after_terminal"] and tools["retained_terminal"] is None
    assert answer["provider"]["cutoff"] and answer["provider"]["session"]
    assert tools["request_calls"] == tools["retained_calls"] == 1
    assert len(tools["admission"]["children"]) == 2
    assert all(
        child["owner"] and child["assistant"] and child["conversation"]
        for child in tools["admission"]["children"]
    )
    assert tools["admission"]["unavailable"] == [{"callId": "call:2", "isError": True}]
    assert (
        tools["publication"]
        and tools["usage_once"]
        and tools["close_reason"] == "closed"
    )


@pytest.mark.parametrize("name", m.MUTANTS)
def test_domain_mutants_are_valid_mismatches(actual, name):
    changed = m.mutate(actual, name)
    m.trace(changed)
    assert m.compare(actual, changed) == 1


def test_double_actual_model_aggregate_fails_with_settlement_map_unchanged(native_run):
    import copy

    actual, root = native_run
    raw = m.d.load(root / "native-records/sqlite/answer.json")
    answer = next(entry for entry in raw["entries"] if entry["kind"] == "pi.assistant")
    assistant = m.decode_canonical(answer["model"][0])
    ledger = copy.deepcopy(raw["ledger"])
    totals = ledger["models"]["openai/gpt-test"]
    assert m.usage_matches(assistant.usage, totals)
    totals["input"] *= 2
    assert ledger["settlements"] == raw["ledger"]["settlements"]
    assert not m.usage_matches(assistant.usage, totals)
    changed = copy.deepcopy(actual)
    value = m.v.decode(changed["cases"][0]["observed"])
    value["ledger_usage"] = m.project_ledger_usage(totals)
    value["usage_once"] = m.usage_matches(assistant.usage, totals)
    changed["cases"][0]["observed"] = m.v.encode(value)
    m.trace(changed)
    assert m.compare(actual, changed) == 1


@pytest.mark.parametrize("reasoning,cache1h", [(None, None), (0, 0), (3, 4)])
def test_actual_usage_optional_members_match_ledger_presence(
    native_run, reasoning, cache1h
):
    import copy
    from dataclasses import replace

    _, root = native_run
    raw = m.d.load(root / "native-records/sqlite/answer.json")
    entry = next(entry for entry in raw["entries"] if entry["kind"] == "pi.assistant")
    assistant = m.decode_canonical(entry["model"][0])
    usage = replace(assistant.usage, reasoning=reasoning, cache_write_1h=cache1h)
    totals = copy.deepcopy(raw["ledger"]["models"]["openai/gpt-test"])
    for key, value in (("reasoning", reasoning), ("cache_write_1h", cache1h)):
        if value is None:
            totals.pop(key, None)
        else:
            totals[key] = value
    projected = m.project_usage(usage)
    assert ("reasoning" in projected) is (reasoning is not None)
    assert ("cacheWrite1h" in projected) is (cache1h is not None)
    assert m.usage_matches(usage, totals)


def test_manifest_binds_actual_generation_and_pinned_producers():
    m.configuration()
    assert "src/pipy_harness/native/durable/generation_transport.py" in m.CLOSURE
    assert "packages/durable/src/harness/generation.ts" in m.SOURCES
    assert "packages/durable/src/harness/tool.ts" in m.SOURCES


def test_malformed_outer_protocol_is_invalid():
    with pytest.raises(m.o.EvidenceError):
        m.trace({})


def test_only_root_receipt_and_relative_store_subtree_are_excluded(tmp_path):
    (tmp_path / "mutants").mkdir()
    (tmp_path / "stores").mkdir()
    for name in (
        "input.json",
        "mutants/input.json",
        "mutants/result.json",
        "result.json",
        "stores/private.sqlite",
    ):
        (tmp_path / name).write_text(name)
    assert set(m.artifact_hashes(tmp_path)) == {
        "input.json",
        "mutants/input.json",
        "mutants/result.json",
    }
    nested = tmp_path / "stores/run"
    nested.mkdir()
    (nested / "input.json").write_text("retained")
    assert set(m.artifact_hashes(nested)) == {"input.json"}

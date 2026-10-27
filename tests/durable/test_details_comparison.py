"""Actual normal CLI records, rich next history and fail-closed comparison."""

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "details_comparison", ROOT / "scripts/durable_details_comparison.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("details-source")
    (root / "processes").mkdir()
    m.d.dump(root / "input.json", m.recipe())
    stdout, stderr, code = m.proc.run(
        m.d,
        [sys.executable, "-I", str(m.NATIVE), str(root / "input.json"), str(root)],
        m.ROOT,
        root / "processes",
        "native-details",
        m.o.child_env(root),
        120,
    )
    assert code == 0 and not stdout and not stderr, stderr
    raw = m.d.load(root / "native-raw.json")
    return m.projection(raw, True), raw, root


def test_actual_cli_next_history_reopen_and_absent_null(native_run):
    actual, _, _ = native_run
    for row in actual["cases"]:
        item = m.v.decode(row["observed"])
        assert item["completed"] and item["repeat_unchanged"]
        assert item["result"] == item["next_result"] == item["reopened_result"]
        assert item["roles"] == ["user", "assistant", "toolResult", "assistant"]
        assert (item["provider_calls"], item["tool_calls"], item["prepares"]) == (
            2,
            1,
            1,
        )
        assert item["next_wire_tools"] == [
            {
                "fields": ["content", "role", "tool_call_id"],
                "role": "tool",
                "content": "actual:value",
                "correlated": True,
            }
        ]
    absent, null = [
        m.v.decode(row["observed"])["result"] for row in actual["cases"][:2]
    ]
    assert absent == {"state": "absent"}
    assert null == {"state": "value", "value": None}


@pytest.mark.parametrize("name", m.values.MUTANTS)
def test_schema_valid_semantic_controls(native_run, name):
    actual, _, _ = native_run
    changed = m.values.mutate(actual, name, m.v)
    m.trace(changed)
    assert m.compare(actual, changed) == 1


def test_actual_faulted_task_and_saved_next_history_discriminate(native_run):
    actual, raw, _ = native_run
    changed = copy.deepcopy(raw)
    changed[1]["raw"]["tasks"][0]["state"]["outcome"] = {
        "status": "faulted",
        "error": "synthetic",
    }
    projected = m.projection(changed, True)
    assert not m.v.decode(projected["cases"][1]["observed"])["completed"]
    assert m.compare(actual, projected) == 1
    changed = copy.deepcopy(raw)
    changed[1]["raw"]["next_request"] = changed[0]["raw"]["next_request"]
    assert m.compare(actual, m.projection(changed, True)) == 1
    changed = copy.deepcopy(raw)
    body = changed[1]["raw"]["wire"][-1]
    message = next(row for row in body["messages"] if row["role"] == "tool")
    message["tool_call_id"] = "wrong-transport-call"
    assert m.compare(actual, m.projection(changed, True)) == 1


def test_invalid_protocol_and_root_relative_artifact_exclusions(native_run, tmp_path):
    actual, _, _ = native_run
    bad = copy.deepcopy(actual)
    bad["unknown"] = True
    with pytest.raises(m.o.EvidenceError):
        m.compare(actual, bad)
    root = tmp_path / "ancestor/stores/output"
    (root / "nested").mkdir(parents=True)
    (root / "stores").mkdir()
    for path in (root / "result.json", root / "nested/result.json", root / "stores/db"):
        path.write_text("{}")
    assert set(m.artifacts(root)) == {"nested/result.json"}


def test_unlisted_loaded_module_rejected(tmp_path):
    m.d.dump(tmp_path / "native-modules.json", {"src/unlisted.py": "0" * 64})
    with pytest.raises(m.o.EvidenceError, match="unlisted"):
        m.native_modules(tmp_path)

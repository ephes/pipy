"""Real native public producer and pure/schema-valid declared-subset controls."""

import asyncio
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "product_comparison", ROOT / "scripts/durable_product_comparison.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("product-common")
    (root / "processes").mkdir()
    return asyncio.run(m.native(root / "stores")), root


def test_actual_public_sqlite_empty_create_atomic_input_tools_completion_reopen(
    native_run,
):
    actual, root = native_run
    for case in actual["cases"]:
        value = m.v.decode(case["observed"])
        assert (
            value["empty_reopen"]
            and value["atomic_admission"]
            and value["commit_readable"]
        )
        assert (
            value["completed"]
            and value["reconnect"]
            and value["repeat_unchanged"]
            and value["usage_once"]
        )
        assert value["user_text"] == "input"
        assert value["provider_calls"] == (1 if case["id"] == "plain" else 2)
        assert value["tool_calls"] == (0 if case["id"] == "plain" else 2)
        raw = m.d.load(root / "native-records" / "sqlite" / (case["id"] + ".json"))
        assert not raw["host_alive_after_close"]
        m.reader_attest(root, raw, case["id"])


@pytest.mark.parametrize("name", m.MUTANTS)
def test_public_subset_schema_valid_semantic_controls(native_run, name):
    actual, _ = native_run
    changed = m.mutate(actual, name)
    m.trace(changed)
    assert m.compare(actual, changed) == 1


def test_raw_faulted_record_regression_is_not_completed(native_run):
    _, root = native_run
    raw = m.d.load(root / "native-records" / "sqlite" / "tools.json")
    raw["tasks"][0]["state"]["outcome"] = {
        "status": "faulted",
        "error": {"name": "Error", "message": "fixture"},
    }
    assert m.project(m.recipe()["cases"][1], raw)["completed"] is False


def test_common_malformed_body_is_separate():
    with pytest.raises(m.o.EvidenceError):
        m.trace({})


@pytest.fixture(scope="module")
def native_policy(tmp_path_factory):
    root = tmp_path_factory.mktemp("product-policy")
    (root / "processes").mkdir()
    return m.policy.produce(
        root / "stores", m.d, m.proc.run, root / "processes", m.o.child_env(root)
    )


def test_actual_native_only_public_retry_quiescence_and_held_physical_lease(
    native_policy,
):
    assert m.policy.valid(native_policy, m.d)


@pytest.mark.parametrize("name", m.policy.MUTANTS)
def test_native_only_raw_semantic_controls(native_policy, name):
    changed = m.policy.mutate(native_policy, name)
    m.policy.protocol(changed, m.d)
    assert not m.policy.valid(changed, m.d)


def test_artifact_exclusions_are_only_relative_to_actual_root(tmp_path):
    root = tmp_path / "ancestor" / "stores" / "output"
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "result.json").write_text("{}")
    (root / "result.json").write_text("{}")
    (root / "stores").mkdir()
    (root / "stores" / "own.sqlite").write_bytes(b"own declared mutable store")
    (root / "archive" / "stores").mkdir(parents=True)
    (root / "archive" / "stores" / "result.json").write_text("{}")
    assert set(m.artifact_hashes(root)) == {
        "nested/result.json",
        "archive/stores/result.json",
    }


def test_raw_missing_generation_is_not_vacuously_completed(native_run):
    _, root = native_run
    raw = m.d.load(root / "native-records" / "sqlite" / "tools.json")
    raw["tasks"] = [row for row in raw["tasks"] if row["kind"] != "pi.generation"]
    assert m.project(m.recipe()["cases"][1], raw)["completed"] is False


def test_wrong_counter_type_is_malformed_not_semantic(native_run):
    import copy

    actual, _ = native_run
    value = copy.deepcopy(actual)
    observed = m.v.decode(value["cases"][0]["observed"])
    observed["provider_calls"] = "not a count"
    value["cases"][0]["observed"] = m.v.encode(observed)
    with pytest.raises(m.o.EvidenceError):
        m.trace(value)

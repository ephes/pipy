"""Actual native inspection and schema-valid common semantic controls."""

import asyncio
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "inspection_comparison", ROOT / "scripts/durable_inspection_comparison.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("inspection-common")
    return asyncio.run(m.native(root / "stores")), root


def test_actual_inspection_versions_and_zero_code(native_run):
    actual, root = native_run
    value = m.v.decode(actual["cases"][0]["observed"])
    assert value["live"] == ["ready", "missing", "newer", "older"]
    assert [row["fit"] for row in value["tasks"]] == [True, False, False, False, None]
    assert [row["reason"] for row in value["tasks"]] == [
        None,
        "definition_missing",
        "stored_version_newer",
        "definition_version_mismatch",
        None,
    ]
    assert value["code_runs_delta"] == value["extra_live"] == 0
    assert value["read_count"] == 5 and value["repeat_equal"]
    assert all(row["record_unchanged"] for row in value["tasks"])
    for backend in ("memory", "sqlite", "reopen"):
        assert (root / "native-records" / backend / "metadata.json").is_file()


@pytest.mark.parametrize("name", m.MUTANTS)
def test_schema_valid_semantic_controls(native_run, name):
    actual, _ = native_run
    mutant = m.mutate(actual, name)
    m.trace(mutant)
    assert m.compare(actual, mutant) == 1


def test_malformed_is_separate():
    with pytest.raises(m.o.EvidenceError):
        m.trace({})


@pytest.fixture(scope="module")
def native_policy(tmp_path_factory):
    root = tmp_path_factory.mktemp("operator-proof") / "stores"
    return m.policy.produce(root, m.d)


def test_actual_native_only_operator_relations(native_policy):
    assert m.policy.valid(native_policy, m.canonical.canonical, m.d)


@pytest.mark.parametrize("name", m.policy.MUTANTS)
def test_native_only_schema_valid_operator_controls(native_policy, name):
    mutant = m.policy.mutate(native_policy, name)
    m.policy.protocol(mutant, m.d)
    assert not m.policy.valid(mutant, m.canonical.canonical, m.d)

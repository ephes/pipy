"""Closed schema controls are distinct from real Product callback/cut evidence."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "product_retry_comparison", ROOT / "scripts/durable_product_retry_comparison.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def _schema_only():
    return {
        "schema_version": 1,
        "phase": m.PHASE,
        "cases": [
            {
                "id": case["id"],
                "observed": m.v.encode(
                    {
                        name: case["parked"] if name == "parked_before" else True
                        for name in m.values.FIELDS
                    }
                ),
            }
            for case in m.values.CASES
        ],
    }


def test_recipe_closed_types_and_finite_cases():
    assert m.values.checked_recipe(copy.deepcopy(m.values.RECIPE)) == m.values.RECIPE
    value = copy.deepcopy(m.values.RECIPE)
    value["cases"][0]["restart_limit"] = True
    with pytest.raises(ValueError, match="typed product retry"):
        m.values.checked_recipe(value)
    value = copy.deepcopy(m.values.RECIPE)
    value["unknown"] = True
    with pytest.raises(ValueError, match="Closed product retry"):
        m.values.checked_recipe(value)


def test_schema_positive_is_not_producer_evidence():
    value = _schema_only()
    m.values.successful(value, m.d, m.v)
    assert m.compare(value, value) == 0


@pytest.mark.parametrize("name", m.values.MUTANTS)
def test_closed_semantic_boolean_mutants(name):
    expected = _schema_only()
    actual = m.values.mutate(expected, name, m.v)
    m.trace(actual)
    assert m.compare(expected, actual) == 1
    with pytest.raises(m.o.EvidenceError, match="retry admission/progress"):
        m.values.successful(actual, m.d, m.v)


@pytest.mark.parametrize("replacement", [0, 1, None, "true"])
def test_exact_boolean_shape_refuses_other_domains(replacement):
    value = _schema_only()
    observed = m.v.decode(value["cases"][0]["observed"])
    observed["counter_absent_after_reset"] = replacement
    value["cases"][0]["observed"] = m.v.encode(observed)
    with pytest.raises(m.o.EvidenceError, match="Exact product retry booleans"):
        m.trace(value)


def test_cli_schema_0_1_2_without_producers(tmp_path):
    expected = _schema_only()
    records = tmp_path / "processes"
    records.mkdir()
    m.d.dump(tmp_path / "expected.json", expected)
    different = m.values.mutate(expected, "returned_before_release", m.v)
    invalid = copy.deepcopy(expected)
    invalid["unknown"] = True
    env = m.o.child_env(tmp_path)
    for name, body, wanted in (
        ("equal", expected, 0),
        ("different", different, 1),
        ("malformed", invalid, 2),
    ):
        path = tmp_path / (name + ".json")
        m.d.dump(path, body)
        stdout, stderr, code = m.proc.run(
            m.d,
            [
                sys.executable,
                "-B",
                "-I",
                str(Path(m.__file__)),
                "compare",
                str(tmp_path / "expected.json"),
                str(path),
            ],
            ROOT,
            records,
            name,
            env,
            60,
        )
        assert code == wanted and not stdout and bool(stderr) is (wanted == 2)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    output = tmp_path_factory.mktemp("public-product-retry-native")
    (output / "processes").mkdir()
    m.d.dump(output / "input.json", m.values.RECIPE)
    argv = [
        sys.executable,
        "-B",
        "-I",
        str(m.NATIVE),
        str(output / "input.json"),
        str(output),
    ]
    stdout, stderr, code = m.proc.run(
        m.d,
        argv,
        ROOT,
        output / "processes",
        "native-product-retry",
        m.o.child_env(output),
        120,
    )
    assert code == 0 and not stdout and not stderr, stderr
    rows = m.d.load(output / "native-raw.json")
    actual = m.projection(rows)
    m.values.successful(actual, m.d, m.v)
    return actual, rows, output


def test_actual_native_public_retry_progress(native_run):
    actual, rows, _ = native_run
    assert m.compare(actual, actual) == 0
    assert [row["id"] for row in rows] == list(m.values.IDS)
    assert all(
        row["native_only"]["private_operation_token_used"] is False for row in rows
    )
    assert rows[0]["cut"]["actual_process"]["returncode"] == -9
    before = rows[0]["native_only"]["effects_before"]
    after = rows[0]["native_only"]["effects_after"]
    assert after["calls"] == before["calls"]
    assert after["applications"] == before["applications"]


def test_actual_native_cleanup_and_readonly_storage_join(native_run):
    _, rows, output = native_run
    m.sqlite_joins(output, [], rows)
    for row in rows:
        assert row["closed"] == ["closed"]
        assert row["records"]["after_close"] == row["records"]["after_release"]
        assert row["gate"]["callback_entered"] is True
        assert row["gate"]["released"] is False
        assert row["gate"]["result_settled"] is False
        assert row["callback_rows"]
        assert row["native_origins"]

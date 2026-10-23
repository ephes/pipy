"""Closed typed-creation evidence, schema-valid mutants and actual CLI mapping."""

import asyncio
import copy
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts/durable_task_creation_comparison.py"
)
spec = importlib.util.spec_from_file_location("task_creation_oracle_tests", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_closed_configuration_and_real_backend_observations(tmp_path):
    assert set(m.configuration()["closure"]) == m.CLOSURE
    actual = asyncio.run(m.native(tmp_path / "native"))
    assert m.compare(actual, actual) == 0
    cases = {case["id"]: case for case in actual["cases"]}
    assert len(cases) == 12
    assert m.v.decode(cases["initial-mutation"]["stored"])["input"]["items"] == [1, 2]
    assert cases["initializer-throw"]["calls"] == ["conversation", "initial"]
    assert cases["missing-owner"]["error"] == "Task owner 999 does not exist"
    for name in m.MUTANTS:
        mutant = m.mutate(actual, name)
        m.trace(mutant)
        assert m.compare(actual, mutant) == 1


def test_actual_compare_cli_0_1_2_retains_inputs_and_binds_receipts(tmp_path):
    actual = asyncio.run(m.native(tmp_path / "native"))
    expected = tmp_path / "observed.json"
    m.d.dump(expected, actual)
    m.d.dump(tmp_path / "input.json", m.recipe())
    recipe_bytes = (tmp_path / "input.json").read_bytes()
    processes, mutants = tmp_path / "processes", tmp_path / "mutants"
    processes.mkdir()
    mutants.mkdir()
    for code, label, name in (
        (0, "input-positive", "input"),
        (1, "input-negative", "input"),
        (1, "kind-negative", "kind"),
        (2, "invalid-cli", "invalid"),
    ):
        value = m.mutate(actual, name) if code == 1 else copy.deepcopy(actual)
        if code == 2:
            value["unknown"] = True
        path = tmp_path / "native.json" if code == 0 else mutants / (name + ".json")
        m.d.dump(path, value)
        input_bytes = path.read_bytes()
        stdout, stderr, returned = m.d.process(
            [sys.executable, str(SCRIPT), "compare", str(expected), str(path)],
            m.ROOT,
            processes,
            label,
            {"PATH": "/usr/bin:/bin"},
            30,
        )
        assert returned == code and b"Traceback" not in stderr and not stdout
        assert path.read_bytes() == input_bytes
        assert (tmp_path / "input.json").read_bytes() == recipe_bytes
        receipt = m.d.load(processes / (label + ".json"))
        m.process_identity(receipt, label, tmp_path, tmp_path / "reference")
        receipt["argv"][-1] = str(tmp_path / "unrelated.json")
        with pytest.raises(m.o.EvidenceError, match="identity drift"):
            m.process_identity(receipt, label, tmp_path, tmp_path / "reference")


@pytest.mark.parametrize(
    "change",
    [
        "missing-case",
        "duplicate-case",
        "unknown-field",
        "bool-version",
        "bad-profile",
        "bad-call",
    ],
)
def test_producer_schema_refuses_invalid(change, tmp_path):
    value = asyncio.run(m.native(tmp_path / "native"))
    if change == "missing-case":
        value["cases"].pop()
    elif change == "duplicate-case":
        value["cases"][1] = value["cases"][0]
    elif change == "unknown-field":
        value["cases"][0]["unknown"] = True
    elif change == "bool-version":
        value["schema_version"] = True
    elif change == "bad-call":
        value["cases"][0]["calls"].append("dispatch")
    else:
        value["cases"][0]["stored"] = ["number", "7ff0000000000000"]
    with pytest.raises((m.o.EvidenceError, ValueError)):
        m.trace(value)

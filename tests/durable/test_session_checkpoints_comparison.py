"""Closed checkpoint producers and actual native three-backend execution.

The compressed expected fixture contains actual pinned Pi bytes. It is never
made from, or relabelled from, a native trace.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def oracle():
    spec = importlib.util.spec_from_file_location(
        "checkpoint_comparison_tests",
        ROOT / "scripts/durable_session_checkpoints_comparison.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def actual(oracle, tmp_path_factory):
    return asyncio.run(
        oracle.native(tmp_path_factory.mktemp("checkpoint-native") / "stores")
    )


@pytest.fixture(scope="module")
def reference(oracle):
    return oracle.fixture()


def test_actual_native_three_backends_match_pinned_session_with_closed_differences(
    oracle, reference, actual
):
    assert oracle.comparison(reference, actual) == 0
    assert all(
        oracle.d.same(actual["backends"][0]["cases"], lane["cases"])
        for lane in actual["backends"][1:]
    )
    assert reference["producer"] == "pi" and actual["producer"] == "native"
    assert all(
        lane["runtime"] == oracle.runtime_contract(True)
        for lane in reference["backends"]
    )
    assert all(
        lane["runtime"] == oracle.runtime_contract(False) for lane in actual["backends"]
    )


@pytest.mark.parametrize(
    "name",
    [
        "kind",
        "count",
        "ops",
        "value",
        "cold",
        "rollback",
        "native-sign",
        "native-order",
    ],
)
def test_valid_semantic_mutations_reach_the_comparison(oracle, reference, actual, name):
    assert oracle.comparison(reference, actual) == 0
    changed = oracle.mutate(actual, name)
    oracle.trace(changed, False)
    if not name.startswith("native-"):
        assert all(
            oracle.d.same(changed["backends"][0]["cases"], lane["cases"])
            for lane in changed["backends"][1:]
        )
    assert oracle.comparison(reference, changed) == 1


@pytest.mark.parametrize(
    "field", ["unknown", "missing", "producer", "profile", "number", "ops", "runtime"]
)
def test_invalid_producer_fields_and_nested_profiles_fail_closed(
    oracle, reference, actual, field
):
    changed = copy.deepcopy(actual)
    if field == "unknown":
        changed["unknown"] = True
    elif field == "missing":
        del changed["backends"][0]["cases"][0]["steps"][0]["cold"]
    elif field == "producer":
        changed["producer"] = "pi"
    elif field == "profile":
        changed["profile"] = "json-lossy"
    elif field == "number":
        changed["backends"][0]["cases"][0]["steps"][0]["value"] = [
            "number",
            "7ff0000000000000",
        ]
    elif field == "ops":
        changed["backends"][0]["cases"][0]["steps"][0]["ops"] = oracle.t.profile(
            [["unknown", []]]
        )
    else:
        changed["backends"][0]["runtime"]["promise"]["extra"] = 1
    with pytest.raises((ValueError, TypeError)):
        oracle.comparison(reference, changed)


def test_reference_projection_cannot_hide_arbitrary_values_or_unobserved_fields(
    oracle, reference, actual
):
    for field in (
        "value",
        "stored",
        "cold",
        "owner_replay",
        "frozen",
        "final",
        "fresh",
    ):
        changed = copy.deepcopy(reference)
        row = next(
            row
            for row in changed["backends"][0]["cases"]
            if row["id"] == "zero-true-positive-simple-dirty"
        )
        step = row["steps"][0]
        if field == "owner_replay":
            step[field]["ops"] = oracle.t.profile([])
        elif field == "frozen":
            step["callbacks"][0][field] = [True, True, True]
        elif field in ("final", "fresh"):
            row[field] = oracle.t.profile({"n": 99})
        else:
            tree = oracle.v.decode(step[field])
            tree["n"] = 99
            step[field] = oracle.t.profile(tree)
        oracle.trace(changed, True)
        assert oracle.comparison(changed, actual) == 1


def test_native_order_consistency_precedes_the_javascript_comparison_projection(
    oracle, reference, actual
):
    changed = copy.deepcopy(actual)
    row = next(
        row
        for row in changed["backends"][1]["cases"]
        if row["id"] == "zero-false-negative-untouched-dirty"
    )
    value = oracle.v.decode(row["steps"][0]["value"])
    # This change is invisible to the JS own-key projection, but changes the
    # native member order. Native consistency must reject it first.
    nested = value["nested"]
    value["nested"] = {
        "zero": nested["zero"],
        "1": nested["1"],
        "2": nested["2"],
        "\ud800": nested["\ud800"],
        "max": nested["max"],
    }
    altered = oracle.t.profile(value)
    assert altered != row["steps"][0]["value"]
    assert oracle.t.profile_equivalent(altered, row["steps"][0]["value"])
    row["steps"][0]["value"] = altered
    assert oracle.comparison(reference, changed) == 1


def test_comparator_real_cli_returns_0_1_2_and_reaps_children(
    oracle, reference, actual, tmp_path
):
    expected = tmp_path / "reference.json"
    oracle.d.dump(expected, reference)
    env = {"PATH": os.defpath, "HOME": str(tmp_path)}
    for label, data, wanted in (
        ("equal", actual, 0),
        ("mismatch", oracle.mutate(actual, "count"), 1),
        ("invalid", {**actual, "unknown": True}, 2),
    ):
        path = tmp_path / (label + ".json")
        oracle.d.dump(path, data)
        _stdout, _stderr, code = oracle.d.process(
            [
                sys.executable,
                "-I",
                str(Path(oracle.__file__)),
                "compare",
                str(expected),
                str(path),
            ],
            ROOT,
            tmp_path,
            label + "-child",
            env,
            60,
        )
        assert code == wanted
        record = oracle.d.load(tmp_path / (label + "-child.json"))
        assert record["leader_reaped"] is True and record["signal"] is None


def test_current_checkpoint_closure_configuration(oracle):
    # Root writes/cascades this manifest after the sole-writer source freeze.
    # An absent/stale manifest remains a real gate failure, never a skip.
    config = oracle.configuration()
    assert config["phase"] == "session-checkpoints-j-v1"
    assert set(config["closure"]) == oracle.CLOSURE


def test_compressed_actual_fixture_rejects_unknown_fields_and_trailing_payload(
    oracle, tmp_path
):
    original = oracle.d.load(oracle.EXPECTED)
    for value in (
        {**original, "unknown": True},
        {**original, "raw_bytes": original["raw_bytes"] - 1},
        {**original, "payload": original["payload"] + "AAAA"},
    ):
        path = tmp_path / "fixture.json"
        oracle.d.dump(path, value)
        with pytest.raises((ValueError, TypeError)):
            oracle.fixture(path)

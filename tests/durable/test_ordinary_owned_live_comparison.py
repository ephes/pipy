"""Schema controls are separate from actual native Task/Tx producer evidence."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "ordinary_owned_live_comparison",
    ROOT / "scripts/durable_ordinary_owned_live_comparison.py",
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def _schema_only():
    rows = []
    for case in m.values.CASES:
        x = {
            "milestones": [
                {"label": label, "owned_live": answer}
                for label, answer in zip(
                    m.values.labels(case),
                    [True, case["first"] == "Z", False],
                    strict=True,
                )
            ]
        }
        rows.append({"id": case["id"], "observed": m.v.encode(x)})
    return {"schema_version": 1, "phase": m.PHASE, "cases": rows}


def test_recipe_closed_types_and_finite_schedule():
    assert m.values.checked_recipe(copy.deepcopy(m.values.RECIPE)) == m.values.RECIPE
    value = copy.deepcopy(m.values.RECIPE)
    value["cases"][0]["cold"] = 0
    with pytest.raises(ValueError, match="typed recipe"):
        m.values.checked_recipe(value)
    value = copy.deepcopy(m.values.RECIPE)
    value["unknown"] = True
    with pytest.raises(ValueError, match="Closed ownership"):
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
    with pytest.raises(m.o.EvidenceError, match="held/released"):
        m.values.successful(actual, m.d, m.v)


@pytest.mark.parametrize("replacement", [0, 1, None, "true"])
def test_exact_boolean_shape_refuses_other_domains(replacement):
    value = _schema_only()
    x = m.v.decode(value["cases"][0]["observed"])
    x["milestones"][0]["owned_live"] = replacement
    value["cases"][0]["observed"] = m.v.encode(x)
    with pytest.raises(m.o.EvidenceError, match="exact boolean"):
        m.trace(value)


def test_cli_schema_0_1_2_without_producers(tmp_path):
    expected = _schema_only()
    records = tmp_path / "processes"
    records.mkdir()
    m.d.dump(tmp_path / "expected.json", expected)
    different = m.values.mutate(expected, "held_false", m.v)
    invalid = copy.deepcopy(expected)
    invalid["unknown"] = True
    env = m.o.child_env(tmp_path)
    for name, body, wanted in [
        ("equal", expected, 0),
        ("different", different, 1),
        ("malformed", invalid, 2),
    ]:
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
    output = tmp_path_factory.mktemp("ordinary-ownership-native")
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
        "native-ordinary-owned-live",
        m.o.child_env(output),
        120,
    )
    assert code == 0 and not stdout and not stderr, stderr
    rows = m.d.load(output / "native-raw.json")
    trace = m.projection(rows)
    m.values.successful(trace, m.d, m.v)
    return trace, rows, output


def test_actual_native_query_and_storage_silence(native_run):
    _, rows, output = native_run
    for row, case in zip(rows, m.values.CASES, strict=True):
        assert row["native_only"]["execution"] == "DISABLED_NO_SCHEDULER"
        assert [r["kind"] for r in row["callback_rows"]] == [
            "initial:" + name for name in ("P", "B", "A", "Z", "D")
        ]
        assert (
            m.sqlite_snapshot(
                output / "stores/native" / row["id"] / "callback-counter.sqlite"
            )
            == row["callback_sqlite"]
        )
        for step in row["steps"]:
            assert (
                step["query"]["before"] == step["query"]["after"]
                and step["query"]["silent"] is True
            )
        if case["backend"] == "sqlite":
            assert (
                m.sqlite_snapshot(
                    output / "stores/native" / row["id"] / "session.sqlite"
                )
                == row["sqlite_final"]
            )
        else:
            assert row["sqlite_final"] is None


@pytest.mark.parametrize("name", m.values.MUTANTS)
def test_actual_native_boolean_mutants(native_run, name):
    expected, _, _ = native_run
    actual = m.values.mutate(expected, name, m.v)
    m.trace(actual)
    assert m.compare(expected, actual) == 1


@pytest.mark.parametrize(
    "name",
    [
        "background",
        "owner",
        "ended_bridge",
        "live_leaf",
        "query_silence",
        "callback",
        "cold_lifecycle",
        "extra",
    ],
)
def test_actual_native_raw_witness_refusal(native_run, name):
    _, original, _ = native_run
    rows = copy.deepcopy(original)
    row = rows[0]
    records = {r["id"]: r for r in row["steps"][0]["snapshot"]["tasks"]}
    if name == "background":
        records[row["topology"]["Z"]]["background"] = False
    elif name == "owner":
        records[row["topology"]["A"]]["owner"] = row["topology"]["P"]
    elif name == "ended_bridge":
        records[row["topology"]["B"]]["state"] = {
            "status": "pending",
            "checkpoint": {"phase": "hold"},
        }
    elif name == "live_leaf":
        records[row["topology"]["D"]]["state"] = {
            "status": "terminal",
            "outcome": {"status": "completed", "result": None},
        }
    elif name == "query_silence":
        row["steps"][0]["query"]["silent"] = False
    elif name == "callback":
        row["callback_rows"][0]["kind"] = "run:unexpected"
    elif name == "cold_lifecycle":
        rows[2]["native_only"]["lifecycle"] = []
    else:
        row["unknown"] = True
    with pytest.raises((ValueError, m.o.EvidenceError)):
        m.projection(rows)


def test_actual_fixture_failure_still_closes_session(monkeypatch, tmp_path):
    from importlib import import_module

    support = import_module("ordinary_owned_live_reference_support")
    owners = []
    original = support.Session

    def capture(*args, **kwargs):
        owner = original(*args, **kwargs)
        owners.append(owner)
        return owner

    def refuse(*args):
        raise RuntimeError("ownership fixture discriminator")

    monkeypatch.setattr(support, "Session", capture)
    monkeypatch.setattr(support.OwnedLiveQuery, "capture", refuse)
    with pytest.raises(RuntimeError, match="fixture discriminator"):
        support.exercise(m.values.CASES[1], tmp_path / "failed")
    assert owners and all(
        owner.end is not None and owner.end.reason == "closed" for owner in owners
    )
    from pipy_harness.native.durable.sqlite import open_sqlite_storage

    storage = open_sqlite_storage(str(tmp_path / "failed/session.sqlite"), mode="open")
    storage.close()

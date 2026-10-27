"""Actual native projections and exhaustive fixture-field discrimination."""

import asyncio
import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "canonical_comparison", ROOT / "scripts/durable_checkpoint_comparison.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    folder = tmp_path_factory.mktemp("canonical-oracle") / "stores"
    return asyncio.run(m.native(folder)), folder


@pytest.fixture(scope="module")
def actual(native_run):
    return native_run[0]


def test_actual_decoded_projection_matches_frozen_source_recipe(actual):
    for row, case in zip(actual["cases"], m.recipe()["cases"], strict=True):
        expected = {key: case[key] for key in ("payload", "identity", "settings")}
        observed = m.v.decode(row["observed"])
        assert observed["before_calls"] == 0
        assert m.v.encode(observed["result"]) == m.v.encode(
            m._persisted_numbers(expected)
        )
        assert all(
            m.v.encode(value) == m.v.encode(expected) for value in observed["phases"]
        )
        assert m.v.encode(observed["reopened"]) == m.v.encode(
            m._persisted_numbers(expected)
        )


@pytest.mark.parametrize("name", m.MUTANTS)
def test_each_domain_mutant_is_schema_valid_and_detected(actual, name):
    changed = m.mutate(actual, name)
    m.trace(changed)
    assert m.compare(actual, changed) == 1


def fields(value, path=()):
    if type(value) is dict:
        if "$type" in value:
            for key in value["fields"]:
                yield value["$type"], key, (*path, "fields", key)
        for key, item in value.items():
            yield from fields(item, (*path, key))
    elif type(value) is list:
        for index, item in enumerate(value):
            yield from fields(item, (*path, index))


def test_every_registered_field_has_executed_mapping_and_valid_negative(actual):
    from pipy_harness.native.durable.canonical_codec import SCHEMAS

    seen = set()
    for index, row in enumerate(actual["cases"]):
        value = m.v.decode(row["observed"])
        for kind, key, path in fields(value["result"]["payload"]):
            seen.add((kind, key))
            changed = copy.deepcopy(actual)
            changed_value = m.v.decode(changed["cases"][index]["observed"])
            parent = changed_value["result"]["payload"]
            for part in path[:-1]:
                parent = parent[part]
            # Finite common observations permit loss; such a trace is a valid
            # mismatch, not the malformed outer protocol's exit2.
            parent.pop(path[-1])
            changed["cases"][index]["observed"] = m.v.encode(changed_value)
            m.trace(changed)
            assert m.compare(actual, changed) == 1, (kind, key)
    expected = {
        (cls.__name__, key) for cls, schema in SCHEMAS.items() for key in schema
    }
    assert expected <= seen


def test_manifest_covers_actual_native_semantics_and_pinned_producers():
    m.configuration()
    assert "src/pipy_harness/native/durable/checkpoint_codec.py" in m.CLOSURE
    assert "src/pipy_harness/native/auth_material.py" in m.CLOSURE
    assert "packages/durable/src/storage/sqlite/node.ts" in m.MODULES


def test_invalid_outer_trace_is_not_a_mismatch():
    with pytest.raises(m.o.EvidenceError):
        m.trace({})


def test_namespace_closure_keeps_input_mutant_and_nested_receipts(tmp_path):
    (tmp_path / "mutants").mkdir()
    (tmp_path / "processes").mkdir()
    (tmp_path / "input.json").write_text("immutable input")
    (tmp_path / "mutants/input.json").write_text("input mutant")
    (tmp_path / "mutants/result.json").write_text("result mutant")
    (tmp_path / "processes/input-negative.json").write_text("process receipt")
    (tmp_path / "result.json").write_text("root receipt")
    assert set(m.artifact_hashes(tmp_path)) == {
        "input.json",
        "mutants/input.json",
        "mutants/result.json",
        "processes/input-negative.json",
    }
    run = tmp_path / "stores/run"
    run.mkdir(parents=True)
    (run / "input.json").write_text("input under stores ancestor")
    assert set(m.artifact_hashes(run)) == {"input.json"}


def test_native_types_and_presence_from_actual_persisted_receipts(native_run):
    import struct
    from datetime import datetime

    from pipy_harness.native.agent.content import NO_STRUCTURED_CONTENT
    from pipy_harness.native.durable.checkpoint_codec import decode_checkpoint

    _, root = native_run

    def receipt(name, role):
        return decode_checkpoint(
            m.d.load(root / "sqlite" / name / "decoded-receipt.json"),
            name="canonical.pair",
            version=-1.5,
            phase="settle",
            role=role,
        ).payload

    provider = receipt("provider", "provider_outcome").result
    assert type(provider.started_at) is datetime
    assert provider.started_at.fold == 1
    assert provider.started_at.tzname() == "named offset"
    assert type(provider.usage["input_tokens"]) is int
    assert type(provider.usage["cost"]["total"]) is int
    assert type(provider.metadata["control"]["continue"]) is bool
    tool = receipt("tool-missing", "tool_outcome")
    assert tool.result.structured_content is NO_STRUCTURED_CONTENT
    assert receipt("tool-null", "tool_outcome").result.structured_content is None
    assert type(tool.execution_seconds) is float and tool.execution_seconds == 0.125
    assert struct.pack("!d", tool.result.details["deep"][0]["zero"]) == bytes.fromhex(
        "8000000000000000"
    )


def test_number_projection_retains_raw_live_sign_presence_and_substantive_numbers():
    value = {
        "zero": -0.0,
        "positive": 1.25,
        "flag": False,
        "null": None,
        "sentinel": {"$enum": "NoStructuredContent", "value": "missing"},
    }
    exact = {
        "schema_version": 1,
        "phase": m.PHASE,
        "cases": [
            {
                "id": "probe",
                "observed": m.v.encode(
                    {"phases": [value], "result": value, "reopened": value}
                ),
            }
        ],
    }
    profiled = m.common_profile(exact)
    raw = m.v.decode(exact["cases"][0]["observed"])
    projected = m.v.decode(profiled["cases"][0]["observed"])
    import math

    assert math.copysign(1, raw["result"]["zero"]) == -1
    assert math.copysign(1, projected["phases"][0]["zero"]) == -1
    assert math.copysign(1, projected["result"]["zero"]) == 1
    assert projected["result"]["positive"] == 1.25
    assert projected["result"]["flag"] is False
    assert projected["result"]["null"] is None
    assert projected["result"]["sentinel"] == value["sentinel"]
    assert "absent" not in projected["result"]


def _raw_native_projection(native_run):
    _, root = native_run
    return m.d.load(root.parent / "native-exact.json")


def test_common_legacy_dto_profile_preserves_full_raw_native_fields(native_run):
    raw = _raw_native_projection(native_run)
    before = copy.deepcopy(raw)
    projected = m.common_profile(raw)
    evidence_fields = []
    for row in raw["cases"]:
        value = m.v.decode(row["observed"])
        evidence_fields.extend(
            (kind, key)
            for kind, key, _path in fields(value)
            if key == "nested_call_evidence"
        )
    assert len(evidence_fields) == 12
    assert {kind for kind, _key in evidence_fields} == {"AgentToolResultMessage"}
    assert raw == before
    assert projected == native_run[0]


def test_common_legacy_profile_refuses_populated_native_only_evidence(native_run):
    raw = _raw_native_projection(native_run)
    row = next(row for row in raw["cases"] if row["id"] == "tool-null")
    value = m.v.decode(row["observed"])
    value["result"]["payload"]["fields"]["result"]["fields"]["nested_call_evidence"] = {
        "calls": []
    }
    row["observed"] = m.v.encode(value)
    with pytest.raises(m.o.EvidenceError, match="outside the legacy"):
        m.common_profile(raw)


def test_common_profile_never_scans_generic_user_details_for_dto_lookalikes(
    native_run,
):
    raw = _raw_native_projection(native_run)
    row = next(row for row in raw["cases"] if row["id"] == "tool-null")
    value = m.v.decode(row["observed"])
    lookalike = {
        "$type": "AgentToolResultMessage",
        "fields": {"nested_call_evidence": None, "details": [None, False, 0]},
    }
    for frame in [*value["phases"], value["result"], value["reopened"]]:
        frame["payload"]["fields"]["result"]["fields"]["details"] = lookalike
    row["observed"] = m.v.encode(value)
    projected = m.common_profile(raw)
    changed = next(row for row in projected["cases"] if row["id"] == "tool-null")
    actual = m.v.decode(changed["observed"])
    assert all(
        frame["payload"]["fields"]["result"]["fields"]["details"] == lookalike
        for frame in [*actual["phases"], actual["result"], actual["reopened"]]
    )

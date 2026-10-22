"""Closed SQLite comparator tests; captured Pi fixture is not a fresh bootstrap."""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/durable_sqlite_comparison.py"
spec = importlib.util.spec_from_file_location("sqlite_comparator_tests", SCRIPT)
assert spec and spec.loader
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


RAW_BYTES = 3_204_104
RAW_SHA256 = "fcd4caa2b7f8fd4ee032103994c6f0b1d1771167fea8774d41ea8d3e258f3642"


def captured_reference(wrapper):
    """Decode only the bounded, lossless captured unit fixture, never CLI input."""
    if (
        type(wrapper) is not dict
        or set(wrapper)
        != {"schema_version", "encoding", "raw_bytes", "raw_sha256", "data"}
        or type(wrapper["schema_version"]) is not int
        or wrapper["schema_version"] != 1
        or wrapper["encoding"] != "zlib+base64"
        or type(wrapper["raw_bytes"]) is not int
        or wrapper["raw_bytes"] != RAW_BYTES
        or wrapper["raw_sha256"] != RAW_SHA256
        or type(wrapper["data"]) is not str
        or len(wrapper["data"]) > 100_000
    ):
        raise ValueError("invalid captured fixture wrapper")
    compressed = base64.b64decode(wrapper["data"], validate=True)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(compressed, RAW_BYTES + 1)
    if (
        len(raw) != RAW_BYTES
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
        or hashlib.sha256(raw).hexdigest() != RAW_SHA256
    ):
        raise ValueError("invalid captured fixture bytes")
    return json.loads(raw)


def test_captured_reference_preserves_complete_original_raw_bytes():
    wrapper = d.load(d.o.FIXTURES / "storage-sqlite-v1.expected.json")
    raw = zlib.decompress(base64.b64decode(wrapper["data"], validate=True))
    assert len(raw) == RAW_BYTES
    assert hashlib.sha256(raw).hexdigest() == RAW_SHA256
    assert captured_reference(wrapper) == json.loads(raw)


@pytest.mark.parametrize(
    "mutation",
    ["schema-bool", "encoding", "base64", "trailing", "overrun", "digest", "length"],
)
def test_captured_reference_refuses_invalid_or_unbounded_bytes(mutation):
    wrapper = d.load(d.o.FIXTURES / "storage-sqlite-v1.expected.json")
    if mutation == "schema-bool":
        wrapper["schema_version"] = True
    elif mutation == "encoding":
        wrapper["encoding"] = "raw"
    elif mutation == "base64":
        wrapper["data"] = "!"
    elif mutation == "trailing":
        compressed = base64.b64decode(wrapper["data"], validate=True) + b"trailing"
        wrapper["data"] = base64.b64encode(compressed).decode("ascii")
    elif mutation == "overrun":
        wrapper["data"] = base64.b64encode(
            zlib.compress(b"x" * (RAW_BYTES + 1))
        ).decode("ascii")
    elif mutation == "digest":
        wrapper["data"] = base64.b64encode(zlib.compress(b"x" * RAW_BYTES)).decode(
            "ascii"
        )
    else:
        wrapper["raw_bytes"] = RAW_BYTES + 1
    with pytest.raises(ValueError):
        captured_reference(wrapper)


@pytest.fixture(scope="module")
def paired(tmp_path_factory):
    recipes = d.recipe()
    reference = captured_reference(
        d.load(d.o.FIXTURES / "storage-sqlite-v1.expected.json")
    )
    actual = d.native(tmp_path_factory.mktemp("sqlite-comparison") / "stores", recipes)
    assert d.comparison(reference, actual, recipes) == 0
    return reference, actual, recipes


def test_actual_native_sqlite_reopen_and_closed_observed_inventory(paired):
    reference, actual, recipes = paired
    assert len(recipes["scenarios"]) == 34
    assert sum(len(s["operations"]) for s in recipes["scenarios"]) == 258
    assert len(recipes["exported_cases"]) == 21
    assert tuple(b["backend"] for b in reference["backends"]) == d.REFERENCE
    assert tuple(b["backend"] for b in actual["backends"]) == d.NATIVE
    assert len(recipes["divergences"]) == 126
    assert len(recipes["projections"]) == 64
    assert d.same(d.observed_divergences(reference, recipes), recipes["divergences"])
    for backend in (*reference["backends"], *actual["backends"]):
        step = d.selected(backend, "document-atomic", "highwater-retained")
        assert step["outcome"]["value"] == d.LIFETIME["values"][backend["backend"]]


@pytest.mark.parametrize("name", d.MUTANTS)
def test_all_behavior_controls_are_schema_valid_mismatch_one(paired, name):
    reference, actual, recipes = paired
    changed = d.mutate(actual, name)
    d.trace(changed, recipes)
    assert d.comparison(reference, actual, recipes) == 0
    assert d.comparison(reference, changed, recipes) == 1


@pytest.mark.parametrize(
    "mutation",
    ["missing", "stale", "overbroad", "duplicate", "wrong-backend", "wrong-memory"],
)
def test_divergence_declarations_cannot_excuse_arbitrary_results(paired, mutation):
    reference, actual, recipes = copy.deepcopy(paired)
    entries = recipes["divergences"]
    if mutation == "missing":
        entries.pop()
    elif mutation == "stale":
        entries[0]["sqlite"] = entries[0]["memory"]
    elif mutation == "overbroad":
        entries[0]["path"] = []
    elif mutation == "duplicate":
        entries.append(copy.deepcopy(entries[0]))
    elif mutation == "wrong-backend":
        entries[0]["backend"] = "native-reopen"
    else:
        entries[0]["memory"] = ["undefined"]
    with pytest.raises(d.o.EvidenceError, match="divergence"):
        d.comparison(reference, actual, recipes)


@pytest.mark.parametrize("mutation", ["missing", "stale", "broad", "memory-native"])
def test_reopen_lifetime_is_one_exact_contract_not_memory_normalization(
    paired, mutation
):
    reference, actual, recipes = copy.deepcopy(paired)
    witness = recipes["reopen_lifetime"]
    if mutation == "missing":
        recipes["reopen_lifetime"] = None
    elif mutation == "stale":
        witness["values"]["memory"] = ["number", "4018000000000000"]
    elif mutation == "broad":
        witness["operation"] = "*"
    else:
        witness["values"]["native-reopen"] = witness["values"]["memory"]
    with pytest.raises(d.o.EvidenceError, match="lifetime"):
        d.comparison(reference, actual, recipes)


@pytest.mark.parametrize("limit", [-2, -1, -0.0, 0, 1])
def test_only_integral_limit_role_converts_preserving_payload_and_raw_version(limit):
    args = {"query": {"scope": {"kind": "session"}, "at": "current"}, "limit": limit}
    args = d.v.decode(d.v.encode(args))
    if limit < 0:
        assert type(args["limit"]) is float
    converted = d.ingress("scan_documents", args)
    assert type(converted["limit"]) is int and converted["limit"] == int(limit)
    data = {
        "writes": [
            {
                "type": "document.create",
                "record": {"id": 2, "kind": "zero", "scope": {"kind": "session"}},
                "content": {"kind": "base", "version": -0.0, "value": {"zero": -0.0}},
            }
        ]
    }
    assert d.v.encode(d.ingress("commit", data)) == d.v.encode(data)
    assert "8000000000000000" in json.dumps(d.v.encode(data))


@pytest.mark.parametrize("limit", [True, False, 1.5, float("inf"), float("nan"), 2**53])
def test_limit_ingress_rejects_nonintegral_or_unsafe_values(limit):
    with pytest.raises(d.o.EvidenceError):
        d.ingress("scan_tasks", {"query": {}, "limit": limit})


def cli(tmp_path, reference, actual):
    d.dump(tmp_path / "expected.json", reference)
    d.dump(tmp_path / "actual.json", actual)
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "compare", "expected.json", "actual.json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "name",
    ["field", "cursor", "signed-zero", "raw-version", "reopen-mint", "semantic-error"],
)
def test_actual_compare_cli_distinguishes_valid_mismatch(paired, tmp_path, name):
    reference, actual, _ = paired
    assert cli(tmp_path, reference, actual).returncode == 0
    result = cli(tmp_path, reference, d.mutate(actual, name))
    assert result.returncode == 1 and "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "mutation",
    [
        "version-bool",
        "backend",
        "missing-row",
        "extra-key",
        "invalid-tag",
        "missing-error-code",
    ],
)
def test_actual_compare_cli_invalid_evidence_is_two(paired, tmp_path, mutation):
    reference, actual, _ = copy.deepcopy(paired)
    if mutation == "version-bool":
        actual["schema_version"] = True
    elif mutation == "backend":
        actual["backends"][0]["backend"] = "memory"
    elif mutation == "missing-row":
        actual["backends"][1]["scenarios"].pop()
    elif mutation == "extra-key":
        actual["unexpected"] = 1
    elif mutation == "invalid-tag":
        d.selected(actual["backends"][0], "identity", "exact")["outcome"]["value"] = [
            "number",
            "7ff0000000000000",
        ]
    else:
        d.selected(actual["backends"][0], "identity", "closed")["outcome"]["error"].pop(
            "code"
        )
    result = cli(tmp_path, reference, actual)
    assert result.returncode == 2 and "Traceback" not in result.stderr


def test_actual_compare_cli_deep_json_is_invalid(tmp_path):
    (tmp_path / "expected.json").write_text("[" * 2000 + "0" + "]" * 2000)
    (tmp_path / "actual.json").write_text("{}")
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "compare", "expected.json", "actual.json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2 and "Traceback" not in result.stderr


@pytest.mark.parametrize("scenario", ["normal", "timeout", "startup"])
def test_bounded_driver_signals_only_owned_unreaped_leader(
    tmp_path, monkeypatch, scenario
):
    real_popen, real_kill = d.subprocess.Popen, d.os.killpg
    owned, signals = [], []

    def create(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        owned.append(child)
        return child

    def kill(group, sig):
        assert owned[0].returncode is None
        signals.append(sig)
        return real_kill(group, sig)

    monkeypatch.setattr(d.subprocess, "Popen", create)
    monkeypatch.setattr(d.os, "killpg", kill)
    code = (
        "import signal;signal.pause()"
        if scenario == "timeout"
        else "raise SystemExit(3)"
        if scenario == "startup"
        else "print('ready')"
    )
    if scenario == "timeout":
        with pytest.raises(subprocess.TimeoutExpired):
            d.process(
                [sys.executable, "-c", code],
                tmp_path,
                tmp_path,
                "child",
                {"PATH": os.defpath},
                1,
            )
        assert signals == [signal.SIGKILL]
    else:
        stdout, stderr, result = d.process(
            [sys.executable, "-c", code],
            tmp_path,
            tmp_path,
            "child",
            {"PATH": os.defpath},
            10,
        )
        assert result == (3 if scenario == "startup" else 0)
        assert stdout == (b"" if scenario == "startup" else b"ready\n") and not stderr
        assert not signals
    assert owned[0].returncode is not None
    record = d.load(tmp_path / "child.json")
    assert record["leader_reaped"] is True


@pytest.mark.parametrize(
    "key,value",
    [
        ("returncode", False),
        ("timeout", 1.0),
        ("seconds", float("nan")),
        ("leader_reaped", 1),
        ("signal", "SIGKILL"),
    ],
)
def test_process_metadata_admission_is_exact(tmp_path, key, value):
    d.process(
        [sys.executable, "-c", "pass"],
        tmp_path,
        tmp_path,
        "child",
        {"PATH": os.defpath},
    )
    record = d.load(tmp_path / "child.json")
    record[key] = value
    with pytest.raises(d.o.EvidenceError):
        d.process_record(record, tmp_path, "child")


def test_projection_never_hides_unexpected_native_error(paired):
    reference, actual, recipes = copy.deepcopy(paired)
    step = d.selected(actual["backends"][0], "f-dangling-True", "s1")
    assert step["outcome"]["error"] == {
        "name": "KeyError",
        "message": "999",
        "code": None,
    }
    step["outcome"]["error"]["message"] = "other missing value"
    assert d.comparison(reference, actual, recipes) == 1


@pytest.mark.parametrize("entry", ["directory", "dangling-symlink"])
def test_actual_run_cli_existing_output_refused_before_parent_use(tmp_path, entry):
    output = tmp_path / "output"
    target = tmp_path / "uncreated-target"
    if entry == "directory":
        output.mkdir()
        (output / "caller-owned").write_text("retained")
    else:
        output.symlink_to(target, target_is_directory=True)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            "run",
            "--acceptance",
            "absent-parent",
            "--output",
            "output",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2 and "SQLite output entry exists" in result.stderr
    assert not target.exists()
    if entry == "directory":
        assert (output / "caller-owned").read_text() == "retained"
    else:
        assert output.is_symlink() and output.readlink() == target


def test_invalid_declaration_uses_cli_main_exit_two(paired, tmp_path, monkeypatch):
    reference, actual, recipes = copy.deepcopy(paired)
    d.dump(tmp_path / "reference.json", reference)
    d.dump(tmp_path / "actual.json", actual)
    recipes["divergences"].pop()
    monkeypatch.setattr(d, "recipe", lambda: recipes)
    assert (
        d.main(
            ["compare", str(tmp_path / "reference.json"), str(tmp_path / "actual.json")]
        )
        == 2
    )


def test_unexpected_ordinary_failure_cannot_be_mismatch_one(tmp_path, monkeypatch):
    def unexpected(_path):
        raise AttributeError("malformed internal evidence")

    monkeypatch.setattr(d, "load", unexpected)
    assert d.main(["compare", str(tmp_path / "left"), str(tmp_path / "right")]) == 2


@pytest.mark.parametrize(
    "mutation",
    [
        "schema-bool",
        "parent",
        "closure",
        "reference",
        "d1-proof",
        "d1-source",
        "d1-workload",
    ],
)
def test_manifest_admission_rejects_identity_and_provenance_drift(
    monkeypatch, mutation
):
    config = copy.deepcopy(d.load(d.MANIFEST))
    if mutation == "schema-bool":
        config["schema_version"] = True
    elif mutation == "parent":
        config["parents"]["b3"] = "0" * 64
    elif mutation == "closure":
        config["closure"][next(iter(config["closure"]))] = "0" * 64
    elif mutation == "reference":
        config["reference_sources"][next(iter(config["reference_sources"]))] = False
    elif mutation == "d1-proof":
        config["d1_evidence"]["gate_proof_sha256"] = "0" * 64
    elif mutation == "d1-source":
        config["d1_evidence"]["sources"][
            next(iter(config["d1_evidence"]["sources"]))
        ] = "0" * 64
    else:
        config["d1_evidence"]["workloads"]["linux"]["manifest.json"] = True
    original = d.load
    monkeypatch.setattr(
        d, "load", lambda path: config if Path(path) == d.MANIFEST else original(path)
    )
    with pytest.raises(d.o.EvidenceError):
        d.configuration()


def test_manifest_five_parent_validators_and_native_d1_binding():
    config = d.configuration()
    assert config["parents"] == d.parents()
    assert len(config["parents"]) == 5
    assert (
        config["d1_evidence"]["delivery"] == "8ab38d2010ea8e7f8660124085b04b6f0e821d94"
    )
    assert set(config["d1_evidence"]["workloads"]) == {"macos", "linux"}


@pytest.mark.parametrize(
    "mutation",
    ["duplicate-projection", "unknown-projection", "bad-pointer", "wrong-lifetime"],
)
def test_closed_recipe_declaration_admission(tmp_path, monkeypatch, mutation):
    value = copy.deepcopy(d.load(d.INPUT))
    if mutation == "duplicate-projection":
        value["projections"].append(copy.deepcopy(value["projections"][0]))
    elif mutation == "unknown-projection":
        value["projections"][0]["backend"] = "any"
    elif mutation == "bad-pointer":
        value["divergences"][0]["path"] = [True]
    else:
        value["reopen_lifetime"]["path"] = []
    path = tmp_path / "recipe.json"
    d.dump(path, value)
    monkeypatch.setattr(d, "INPUT", path)
    with pytest.raises(d.o.EvidenceError):
        d.recipe()


def test_reopen_leg_covers_helper_commits_failures_and_final_close(
    tmp_path, monkeypatch
):
    calls = []
    original = d.open_sqlite_storage

    def opened(path, **kwargs):
        calls.append(kwargs["mode"])
        return original(path, **kwargs)

    monkeypatch.setattr(d, "open_sqlite_storage", opened)
    store = d.ReopeningStorage(tmp_path / "helper.sqlite")
    try:
        store.commit((d.m.StorageWrite({"type": "conversation", "value": {"id": 1}}),))
        assert d.m._ownership(store, {"id": 2})["source_detached"]
        assert d.m._document_ownership(store, {"id": 3})["read_detached"]
        assert calls == ["create", "open", "open", "open"]
        with pytest.raises(RuntimeError):
            store.commit(
                (
                    d.m.StorageWrite(
                        {
                            "type": "entry",
                            "value": {
                                "id": 1,
                                "conversationId": 1,
                                "kind": "collision",
                            },
                        }
                    ),
                )
            )
        assert calls == ["create", "open", "open", "open", "open"]
        assert store.entry(2) is not None
        store.close()
        with pytest.raises(RuntimeError):
            store.commit(())
        assert len(calls) == 5
    finally:
        store.close()


def test_native_adapter_open_count_includes_helper_commits(tmp_path, monkeypatch):
    calls = []
    original = d.open_sqlite_storage

    def opened(path, **kwargs):
        calls.append((Path(path).name, kwargs["mode"]))
        return original(path, **kwargs)

    monkeypatch.setattr(d, "open_sqlite_storage", opened)
    recipes = d.recipe()
    d.native(tmp_path / "actual-stores", recipes)
    actual = [mode for name, mode in calls if name.startswith("native-reopen")]
    assert actual.count("create") == len(recipes["scenarios"])
    expected = sum(
        step["op"] in {"commit", "ownership", "document_ownership"}
        for scenario in recipes["scenarios"]
        for step in scenario["operations"]
    )
    assert actual.count("open") == expected
    assert any(
        step["op"] == "ownership"
        for scenario in recipes["scenarios"]
        for step in scenario["operations"]
    )
    assert any(
        step["op"] == "document_ownership"
        for scenario in recipes["scenarios"]
        for step in scenario["operations"]
    )


@pytest.mark.parametrize("representation", ["canonical-error", "projected"])
def test_raw_pi_projection_usage_rejects_canonical_drift(
    paired, tmp_path, representation
):
    reference, actual, recipes = copy.deepcopy(paired)
    if representation == "projected":
        reference = d.projected(reference, recipes)
    else:
        declaration = next(
            p for p in recipes["projections"] if p["backend"] in d.REFERENCE
        )
        backend = next(
            b for b in reference["backends"] if b["backend"] == declaration["backend"]
        )
        d.selected(backend, declaration["scenario"], declaration["operation"])[
            "outcome"
        ]["error"] = declaration["canonical"]
    result = cli(tmp_path, reference, actual)
    assert result.returncode == 2 and "Unused error projection" in result.stderr


def test_real_native_cursor_loss_is_causal_mismatch_with_bound_report(
    paired, tmp_path, monkeypatch
):
    reference, _, recipes = paired
    invoke = d.m.invoke
    calls = []

    def drop_cursor(store, op, args):
        calls.append(op)
        result = d.m._dto(invoke(store, op, args))
        if op.startswith("scan_") and type(result) is dict:
            result.pop("next", None)
        return result

    monkeypatch.setattr(d.m, "invoke", drop_cursor)
    actual = d.native(tmp_path / "real-stores", recipes)
    blocked = [
        step
        for backend in actual["backends"]
        for row in backend["scenarios"]
        for step in row["operations"]
        if step["outcome"]["status"] == "blocked"
    ]
    assert blocked
    assert len(calls) == 2 * 258 - len(blocked)
    for step in blocked:
        assert d.v.decode(step["args"])["cursor_ref"] == step["outcome"]["cursor_ref"]
        assert "cursor" not in d.v.decode(step["args"])
    assert cli(tmp_path, reference, actual).returncode == 1
    d.dump(tmp_path / "observed.json", reference)
    d.dump(tmp_path / "native.json", actual)
    assert d.comparison_report(tmp_path, reference, actual, recipes) == 1
    report = d.load(tmp_path / "mismatch-report.json")
    for field, name in [("expected", "observed"), ("actual", "native")]:
        assert report[field + "_sha256"] == d.o.sha(tmp_path / (name + ".json"))
        assert report[field + "_projected_sha256"] == d.o.sha(
            tmp_path / (name + "-projected.json")
        )


def test_blocked_continuation_requires_actual_missing_cursor(paired):
    reference, actual, recipes = copy.deepcopy(paired)
    step = next(
        step
        for row in actual["backends"][0]["scenarios"]
        for step in row["operations"]
        if "cursor" in d.v.decode(step["args"])
    )
    step["outcome"] = {"status": "blocked", "cursor_ref": "invented"}
    with pytest.raises(d.o.EvidenceError):
        d.comparison(reference, actual, recipes)

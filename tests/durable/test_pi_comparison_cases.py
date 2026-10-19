"""DE01b1: real recorded cases, strict closure, and semantic oracle negatives."""

from __future__ import annotations

import copy
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/durable_pi_comparison.py"
spec = importlib.util.spec_from_file_location("durable_cases_oracle", SCRIPT)
assert spec is not None and spec.loader is not None
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)
contract = oracle.contract


def fixture(case="harness-round-v1"):
    return oracle.load(oracle.FIXTURES / (case + ".json"))


def observed(trace):
    return contract.decode(trace["steps"][0]["value"])


def changed(trace, value):
    trace["steps"][0]["value"] = contract.encode(value)
    return trace


def cli(tmp_path, expected, actual):
    oracle.dump(tmp_path / "expected.json", expected)
    oracle.dump(tmp_path / "actual.json", actual)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "compare",
            str(tmp_path / "expected.json"),
            str(tmp_path / "actual.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("case", list(contract.CASES))
def test_recorded_cases_validate_and_compare(case):
    value = fixture(case)
    oracle.trace(value)
    assert oracle.compare(value, copy.deepcopy(value))


@pytest.mark.parametrize("case", ["harness-round-v1", "context-heads-v1"])
def test_real_semantic_mutants_are_valid_but_cli_mismatch(tmp_path, case):
    expected = fixture(case)
    actual = copy.deepcopy(expected)
    value = observed(actual)
    if case == "harness-round-v1":
        result = next(e for e in value["history"] if e["kind"] == "pi.tool-result")[
            "model"
        ][0]
        assert "details" in result and result["details"] is None
        del result["details"]
    else:
        assert value["final"]["head"]["id"] != value["first"]["head"]["id"]
        original_history, cutoff = (
            copy.deepcopy(value["history"]),
            value["requested_cutoff"],
        )
        value["final"] = value["first"]
        assert (
            value["history"] == original_history and value["requested_cutoff"] == cutoff
        )
    changed(actual, value)
    oracle.trace(actual)
    result = cli(tmp_path, expected, actual)
    assert result.returncode == 1 and "TRACE MISMATCH" in result.stdout
    assert "INVALID" not in result.stderr


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"x": contract.UNDEFINED},
        {"x": None},
        {"x": {"type": "undefined"}},
        {"x": {"$undefined": True}},
        [contract.UNDEFINED],
        [None],
    ],
)
def test_tagged_values_distinguish_presence_and_literal_markers(value):
    encoded = contract.encode(value)
    assert contract.encode(contract.decode(encoded)) == encoded
    variants = [
        {},
        {"x": contract.UNDEFINED},
        {"x": None},
        {"x": {"type": "undefined"}},
        {"x": {"$undefined": True}},
        [contract.UNDEFINED],
        [None],
    ]
    assert len({oracle._canonical(contract.encode(v)) for v in variants}) == len(
        variants
    )


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"type": "other"},
        {"type": "undefined", "extra": 1},
        {"type": "object", "entries": [["x", 1], ["x", 2]]},
        {"type": "object", "entries": [1]},
        {"type": "array", "items": {}},
        {"type": "object", "entries": [[True, 1]]},
    ],
)
def test_malformed_tagged_values_fail(value):
    with pytest.raises(oracle.EvidenceError):
        contract.decode(value)


def at(value, pointer):
    parts = pointer.strip("/").split("/")
    for part in parts[:-1]:
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if type(value) is list else value[part]
    return value, parts[-1].replace("~1", "/").replace("~0", "~")


@pytest.mark.parametrize("case", ["harness-round-v1", "context-heads-v1"])
def test_selected_id_renaming_is_bijective_and_witnessed(case):
    expected = fixture(case)
    actual = copy.deepcopy(expected)
    _, witness = contract.project(expected)
    value = observed(actual)
    for item in witness:
        if item["rule"] != "durable-id-bijection-v1":
            continue
        parent, key = at(value, item["path"])
        parent[key] += 100
    changed(actual, value)
    assert oracle.compare(expected, actual)
    _, actual_witness = contract.project(actual)
    assert actual_witness and all(
        "before" in w and "after" in w for w in actual_witness
    )
    assert value["history"][0]["conversationId"] == 1


@pytest.mark.parametrize(
    "mutation", ["root", "cross-kind-collision", "unknown-reference"]
)
def test_identity_constraints_fail_closed(mutation):
    actual = fixture()
    value = observed(actual)
    if mutation == "root":
        value["history"][0]["conversationId"] = 200
    elif mutation == "cross-kind-collision":
        value["submission"]["id"] = value["tasks"][0]["id"]
    else:
        value["history"][0]["byTaskId"] = 10000
    with pytest.raises(oracle.EvidenceError):
        contract.project(changed(actual, value))


def test_provider_id_bijection_preserves_request_correlation():
    expected = fixture()
    actual = copy.deepcopy(expected)
    value = observed(actual)
    value["provider"]["sessionId"] = "synthetic-renamed-session"
    for request in value["requests"]:
        request["sessionId"] = "synthetic-renamed-session"
    assert oracle.compare(expected, changed(actual, value))
    value["requests"][1]["sessionId"] = "different-session"
    assert not oracle.compare(expected, changed(actual, value))


def test_elapsed_projection_preserves_raw_and_duplicate_origin_correlation():
    expected = fixture()
    actual = copy.deepcopy(expected)
    value = observed(actual)
    tool = next(e for e in value["history"] if e["kind"] == "pi.tool-result")["model"][
        0
    ]
    original = tool["durationMs"]
    tool["durationMs"] += 10
    request_tool = next(
        m for m in value["requests"][1]["messages"] if m["role"] == "toolResult"
    )
    request_tool["durationMs"] += 10
    assert oracle.compare(expected, changed(actual, value))
    raw_before = copy.deepcopy(actual)
    _, witness = contract.project(actual)
    assert actual == raw_before
    durations = [v for v in witness if v["rule"] == "correlated-tool-elapsed-ms-v1"]
    assert len(durations) == 2 and all(v["before"] == original + 10 for v in durations)
    request_tool["durationMs"] += 1
    assert not oracle.compare(expected, changed(actual, value))


@pytest.mark.parametrize("bad", [True, -1, "0", 1.5])
def test_tool_elapsed_type_and_unit_are_validated(bad):
    actual = fixture()
    value = observed(actual)
    value["history"][3]["model"][0]["durationMs"] = bad
    with pytest.raises(oracle.EvidenceError):
        contract.project(changed(actual, value))


@pytest.mark.parametrize("field", ["id", "timestamp", "durationMs"])
def test_arbitrary_payload_fields_are_never_projected(field):
    expected = fixture()
    value = observed(expected)
    value["history"][3]["data"][field] = 42
    changed(expected, value)
    actual = copy.deepcopy(expected)
    value = observed(actual)
    value["history"][3]["data"][field] = 43
    assert not oracle.compare(expected, changed(actual, value))


@pytest.mark.parametrize(
    "mutation", ["usage", "error", "history-order", "call-id", "api", "timestamp"]
)
def test_semantic_observables_remain_significant(mutation):
    expected = fixture()
    actual = copy.deepcopy(expected)
    value = observed(actual)
    if mutation == "usage":
        value["usage"]["models"]["faux/faux-1"]["cacheRead"] += 1
    elif mutation == "error":
        value["reports"].append({"name": "Error", "message": "preserve error"})
    elif mutation == "history-order":
        value["history"].reverse()
    elif mutation == "call-id":
        value["history"][3]["model"][0]["toolCallId"] = "different"
    elif mutation == "api":
        value["history"][2]["model"][0]["api"] = "different"
    else:
        value["history"][0]["model"][0]["timestamp"] += 1
    assert not oracle.compare(expected, changed(actual, value))


def rewrite_manifest(tmp_path, mutation):
    fixtures = tmp_path / "fixtures"
    shutil.copytree(oracle.FIXTURES, fixtures)
    value = oracle.load(fixtures / "manifest.json")
    mutation(value)
    oracle.dump(fixtures / "manifest.json", value)
    return fixtures


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v["cases"].pop(),
        lambda v: v["cases"].append(copy.deepcopy(v["cases"][0])),
        lambda v: v["cases"][0].update(id="unknown"),
        lambda v: v["cases"][1].update(normalization="unknown"),
        lambda v: v["cases"][1].update(required_modules=[]),
        lambda v: v["cases"][1]["upstream_tests"][0].update(name="different"),
        lambda v: v["cases"][1]["input"].update(path="../input.json"),
        lambda v: v["cases"][1]["input"].update(path="kernel-memory-v1.input.json"),
        lambda v: v["adapters"].pop("scripts/durable_reference/contract.mts"),
        lambda v: v["adapters"].update(
            {"extra.mts": {"destination": "extra.mts", "sha256": "0" * 64}}
        ),
        lambda v: v["adapters"]["scripts/durable_reference/contract.mts"].update(
            destination="packages/new.ts"
        ),
        lambda v: v["adapters"]["scripts/durable_reference/contract.mts"].update(
            sha256="0" * 64
        ),
        lambda v: v["comparison"].pop("scripts/durable_reference_contract.py"),
        lambda v: v["comparison"].update(
            {"scripts/durable_reference_contract.py": "0" * 64}
        ),
    ],
)
def test_registry_is_closed_and_complete(tmp_path, mutation):
    fixtures = rewrite_manifest(tmp_path, mutation)
    with pytest.raises(oracle.EvidenceError):
        oracle.manifest(fixtures)


def test_only_exact_hashed_adapter_file_can_resolve(tmp_path):
    path = tmp_path / "durable_reference/contract.mts"
    path.parent.mkdir()
    path.write_text("export const x=1;")
    module = {
        "specifier": "./durable_reference/contract.mts",
        "path": "durable_reference/contract.mts",
    }
    closure = {module["path"]: oracle.sha(path)}
    assert oracle.verify_resolutions(tmp_path, [module], {}, closure, []) == closure
    with pytest.raises(oracle.EvidenceError):
        oracle.verify_resolutions(tmp_path, [module], {}, {}, [])
    path.write_text("export const x=2;")
    with pytest.raises(oracle.EvidenceError):
        oracle.verify_resolutions(tmp_path, [module], {}, closure, [])
    undeclared = path.with_name("extra.mts")
    undeclared.write_text("export {};")
    with pytest.raises(oracle.EvidenceError):
        oracle.verify_resolutions(
            tmp_path,
            [{"specifier": "./extra.mts", "path": "durable_reference/extra.mts"}],
            {},
            closure,
            [],
        )


def test_copy_adapter_rejects_archive_collision(tmp_path):
    config = oracle.manifest()
    (tmp_path / "durable_pi_reference.mts").write_text("original archived file")
    with pytest.raises(oracle.EvidenceError, match="collision"):
        oracle.copy_adapters(config, tmp_path, {"durable_pi_reference.mts": "archived"})


@pytest.mark.parametrize("case", ["harness-round-v1", "context-heads-v1"])
def test_v2_malformed_trace_is_cli_invalid(tmp_path, case):
    expected = fixture(case)
    actual = copy.deepcopy(expected)
    actual["steps"][0]["value"] = {"type": "object", "entries": [["x", 1], ["x", 2]]}
    result = cli(tmp_path, expected, actual)
    assert result.returncode == 2 and "EVIDENCE INVALID" in result.stderr


@pytest.mark.parametrize("path", [".", "", "../x", "/x", "a//b", "a/./b", "a\\b"])
def test_noncanonical_paths_refused(path):
    with pytest.raises(oracle.EvidenceError):
        contract.safe_path(path)


def test_tagged_object_key_order_only_is_immaterial():
    expected = fixture()
    actual = copy.deepcopy(expected)
    actual["steps"][0]["value"]["entries"].reverse()
    assert oracle.compare(expected, actual)


@pytest.mark.parametrize("value", [None, True, [], 1, "bad"])
def test_malformed_v2_observation_record_is_evidence_error(value):
    actual = fixture()
    decoded = observed(actual)
    decoded["tasks"][0]["state"] = value
    with pytest.raises(oracle.EvidenceError):
        contract.project(changed(actual, decoded))


@pytest.mark.parametrize(
    "case,collection",
    [
        ("context-heads-v1", "history"),
        ("harness-round-v1", "history"),
        ("harness-round-v1", "tasks"),
    ],
)
def test_id_ordered_scan_relation_cannot_be_hidden_by_bijective_renaming(
    tmp_path, case, collection
):
    expected = fixture(case)
    actual = copy.deepcopy(expected)
    decoded = observed(actual)
    first, second = (row["id"] for row in decoded[collection][:2])
    swap = {first: second, second: first}
    _, witness = contract.project(expected)
    for item in witness:
        if item["rule"] != "durable-id-bijection-v1":
            continue
        parent, key = at(decoded, item["path"])
        parent[key] = swap.get(parent[key], parent[key])
    changed(actual, decoded)
    oracle.trace(actual)
    result = cli(tmp_path, expected, actual)
    assert result.returncode == 1 and "TRACE MISMATCH" in result.stdout
    projected, _ = contract.project(actual)
    assert projected["scan_order_relations"]["/" + collection][0] is False


def test_reference_refuses_coordinated_manifest_golden_drift(tmp_path, monkeypatch):
    """Isolate manifest identity from bootstrap; this supplies no reference evidence."""
    fixtures = tmp_path / "fixtures"
    shutil.copytree(oracle.FIXTURES, fixtures)
    source = tmp_path / "source"
    (source / ".git").mkdir(parents=True)

    def prerequisites(command, cwd, output, label, env, timeout=120):
        values = {
            "ref": oracle.PIN,
            "tree": oracle.TREE,
            "node-version": "v26.12.0",
            "npm-version": "11.20.0",
        }
        assert label in values
        return values[label].encode()

    def bootstrap(source, snapshot, output, config, execute):
        (output / "reference.tar").write_bytes(
            b"unit bootstrap standin, not reference evidence"
        )
        oracle.dump(output / "source-sha256.json", {})
        oracle.dump(output / "dependency-sha256.json", {})
        return {}, {}, {}

    def case_result(case, *args):
        if case["id"] == "kernel-memory-v1":
            golden = fixtures / "kernel-memory-v1.json"
            value = oracle.load(golden)
            value["steps"][0]["value"]["present"] = True
            oracle.dump(golden, value)
            manifest = oracle.load(fixtures / "manifest.json")
            manifest["cases"][0]["expected"]["sha256"] = oracle.sha(golden)
            oracle.dump(fixtures / "manifest.json", manifest)
        return {"id": case["id"], "equal": True}

    monkeypatch.setattr(oracle, "run", prerequisites)
    monkeypatch.setattr(oracle, "install_reference", bootstrap)
    monkeypatch.setattr(oracle, "copy_adapters", lambda *args: {})
    monkeypatch.setattr(oracle, "reference_case", case_result)
    output = tmp_path / "result"
    assert (
        oracle.main(
            [
                "reference",
                "--pi-source",
                str(source),
                "--output",
                str(output),
                "--fixtures",
                str(fixtures),
            ]
        )
        == 2
    )
    assert not (output / "run.json").exists()

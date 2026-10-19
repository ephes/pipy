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


@pytest.mark.parametrize("case", ["tool-recovery-v1", "durable-abort-v1"])
def test_additional_semantic_mutants_are_valid_but_cli_mismatch(tmp_path, case):
    expected = fixture(case)
    actual = copy.deepcopy(expected)
    value = observed(actual)
    if case == "tool-recovery-v1":
        variant = value["variants"][0]
        assert variant["runs"] == 1
        extra = copy.deepcopy(variant["invocations"][0])
        extra.update(ordinal=2, phase="after-reopen")
        variant["invocations"].append(extra)
        variant["runs"] = 2
    else:
        commit = next(item for item in value["late"] if item["op"] == "commit")
        commit.clear()
        commit.update(op="commit", status="fulfilled")
        value["callbackEntered"] = True
        value["history"].append(
            {
                "id": 1000,
                "conversationId": 1,
                "byTaskId": value["id"],
                "kind": "fixture.forbidden",
                "data": {"late": True},
            }
        )
    changed(actual, value)
    oracle.trace(actual)
    contract.project(actual)
    result = cli(tmp_path, expected, actual)
    assert result.returncode == 1 and "TRACE MISMATCH" in result.stdout
    assert "INVALID" not in result.stderr


@pytest.mark.parametrize("case", ["tool-recovery-v1", "env-conformance-v1"])
@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown"])
def test_required_recovery_matrix_and_env_selection_are_closed(case, mutation):
    actual = fixture(case)
    value = observed(actual)
    rows = value["variants" if case == "tool-recovery-v1" else "scenarios"]
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    else:
        rows[0]["id"] = "unknown"
    with pytest.raises(oracle.EvidenceError):
        contract.project(changed(actual, value))


def test_actual_recovery_fixture_retains_policy_intersection_and_persisted_facts():
    value = observed(fixture("tool-recovery-v1"))
    assert len(value["variants"]) == 4
    for item in value["variants"]:
        reruns = item["stored"] == item["current"] == "safe"
        assert item["runs"] == len(item["invocations"]) == (2 if reruns else 1)
        assert item["invocations"][0]["phase"] == "before-close"
        assert item["beforeClose"]["task"]["state"]["status"] == "running"
        assert item["reopened"]["task"]["state"]["status"] == "pending"
        checkpoint = item["reopened"]["task"]["state"]["checkpoint"]
        assert checkpoint == {
            "phase": "execute",
            "arguments": {},
            "replay": item["stored"],
        }
        assert item["beforeClose"]["task"]["id"] == item["reopened"]["task"]["id"]
        assert (
            item["beforeClose"]["provider"]
            == item["reopened"]["provider"]
            == item["provider"]
        )
        assert item["reopened"]["runs"] == item["reopened"]["callCount"] == 1
        result = next(e for e in item["history"] if e["kind"] == "pi.tool-result")[
            "model"
        ][0]
        assert result["isError"] is not reruns
        assert result["details"] == {"run": 2 if reruns else 1}
        assert item["faux"]["callCount"] == 2 and len(item["requests"]) == 2
        assert item["live"] == {} and item["close"]["reason"] == "closed"
        assert item["database"]["header"] == "SQLite format 3\0"
        assert any(fact["op"] == "advance" for fact in item["clock"])
        assert item["usage"]["models"]["faux/faux-1"]["cacheRead"] > 0


@pytest.mark.parametrize("case", ["tool-recovery-v1", "durable-abort-v1"])
def test_new_typed_id_paths_rename_consistently_without_changing_raw(case):
    expected = fixture(case)
    actual = copy.deepcopy(expected)
    value = observed(actual)
    _, witness = contract.project(expected)
    for item in witness:
        if item["rule"] != "durable-id-bijection-v1":
            continue
        parent, key = at(value, item["path"])
        key = int(key) if isinstance(parent, list) else key
        parent[key] += 100
    if case == "durable-abort-v1":
        for operation in value["late"]:
            operation["error"]["message"] = (
                f"Task {value['id']} has a durable abort mark"
            )
    changed(actual, value)
    assert oracle.compare(expected, actual)
    raw_before = copy.deepcopy(actual)
    contract.project(actual)
    assert actual == raw_before
    if case == "tool-recovery-v1":
        pointers = {item["path"] for item in witness}
        assert "/variants/0/reopened/live/run/inputs/0" in pointers
        assert any("/state/on/" in path for path in pointers)
        assert any("/state/checkpoint/tools/" in path for path in pointers)


@pytest.mark.parametrize(
    "mutation",
    ["checkpoint", "effect-order", "clock", "provider-copy", "duration-copy"],
)
def test_recovery_observables_remain_significant(mutation):
    expected = fixture("tool-recovery-v1")
    actual = copy.deepcopy(expected)
    value = observed(actual)
    item = value["variants"][1]
    if mutation == "checkpoint":
        item["reopened"]["task"]["state"]["checkpoint"]["replay"] = "unsafe"
    elif mutation == "effect-order":
        item["invocations"].reverse()
    elif mutation == "clock":
        next(f for f in item["clock"] if f["op"] == "advance")["at"] += 1
    elif mutation == "provider-copy":
        item["reopened"]["provider"]["sessionId"] = "different-persisted-provider"
    else:
        message = next(
            m for m in item["requests"][1]["messages"] if m["role"] == "toolResult"
        )
        message["durationMs"] += 1
    assert not oracle.compare(expected, changed(actual, value))


def test_actual_abort_fixture_preserves_durable_order_and_fresh_invocation():
    value = observed(fixture("durable-abort-v1"))
    assert value["memoRead"] == 1 and value["callbackEntered"] is False
    assert value["abortInvocations"] == 1 and value["freshRuntime"] is True
    assert value["abortCurrent"]["memos"] == {"kept": 1}
    assert value["final"]["state"]["outcome"] == {"status": "aborted", "reason": "mark"}
    assert value["publications"][0]["changes"][0]["value"]["abortRequested"] is True
    assert value["events"].index("host.durable-mark-publication") < value[
        "events"
    ].index("run.cancellation-continuation")
    assert [item["status"] for item in value["late"]] == ["rejected", "rejected"]
    assert all(
        item["error"]["message"] == f"Task {value['id']} has a durable abort mark"
        for item in value["late"]
    )
    assert value["history"] == [] and value["abortResult"] == "marked"


@pytest.mark.parametrize("mutation", ["error", "memo", "events", "seq", "abort-count"])
def test_abort_semantics_are_not_projected_away(mutation):
    expected = fixture("durable-abort-v1")
    actual = copy.deepcopy(expected)
    value = observed(actual)
    if mutation == "error":
        value["late"][0]["error"]["message"] += " changed"
    elif mutation == "memo":
        value["abortCurrent"]["memos"]["kept"] = 2
    elif mutation == "events":
        value["events"].reverse()
    elif mutation == "seq":
        value["publications"][0]["seq"] += 1
    else:
        value["abortInvocations"] += 1
    assert not oracle.compare(expected, changed(actual, value))


def test_actual_environment_fixture_preserves_void_bytes_errors_and_literal_argv():
    reader, argv = observed(fixture("env-conformance-v1"))["scenarios"]
    assert reader["calls"] == argv["calls"] == 1
    operations = reader["operations"]
    assert operations[0]["result"] == {"ok": True, "value": contract.UNDEFINED}
    reads = [op for op in operations if op["op"] == "reader.read"]
    assert reads[0]["result"]["value"] == list(b"hello")
    assert reads[1]["result"]["value"] == list(b"world")
    assert [op["args"] for op in reads[5:8]] == [[-1, 1], [0, 1.5], [0, 1]]
    assert [op["result"]["error"]["code"] for op in reads[5:8]] == [
        "invalid",
        "invalid",
        "aborted",
    ]
    exec_op, exists_op = argv["operations"]
    assert isinstance(exec_op["argv"], list) and "$(touch pwned)" in exec_op["argv"][4]
    assert exec_op["streams"] == {"stdout": exec_op["argv"][4] + "|a b", "stderr": ""}
    assert exec_op["result"] == {"ok": True, "value": {"exitCode": 0}}
    assert exists_op["result"] == {"ok": True, "value": False}


def test_environment_projection_is_bounded_and_witnessed():
    expected = fixture("env-conformance-v1")
    actual = copy.deepcopy(expected)
    value = observed(actual)
    _, witness = contract.project(expected)
    for item in witness:
        parent, key = at(value, item["path"])
        if item["rule"] in ("controlled-env-root-v1", "controlled-env-path-v1"):
            parent[key] = parent[key].replace("/tmp/", "/tmp/synthetic-root/", 1)
        elif item["rule"] == "file-info-mtime-ms-v1":
            parent[key] += 123.25
    assert oracle.compare(expected, changed(actual, value))
    assert {w["rule"] for w in witness} == {
        "controlled-env-root-v1",
        "controlled-env-path-v1",
        "file-info-mtime-ms-v1",
    }
    info = value["scenarios"][0]["operations"][2]["result"]["value"]
    info["name"] = "different.txt"
    assert not oracle.compare(expected, changed(actual, value))


@pytest.mark.parametrize("mutation", ["escape", "bytes", "message", "literal"])
def test_environment_payloads_and_scope_are_preserved(mutation):
    expected = fixture("env-conformance-v1")
    actual = copy.deepcopy(expected)
    value = observed(actual)
    reader, argv = value["scenarios"]
    if mutation == "escape":
        reader["operations"][2]["result"]["value"]["path"] = (
            reader["cwd"] + "/../other/data.txt"
        )
        with pytest.raises(oracle.EvidenceError):
            contract.project(changed(actual, value))
        return
    if mutation == "bytes":
        reader["operations"][3]["result"]["value"][0] += 1
    elif mutation == "message":
        reader["operations"][8]["result"]["error"]["message"] += " changed"
    else:
        argv["operations"][0]["argv"][4] += " changed"
    assert not oracle.compare(expected, changed(actual, value))


@pytest.mark.parametrize("outcome", [None, [], [None], True, "completed", 1])
def test_malformed_abort_task_outcome_is_cli_invalid(tmp_path, outcome):
    expected = fixture("durable-abort-v1")
    actual = copy.deepcopy(expected)
    value = observed(actual)
    value["final"]["state"]["outcome"] = outcome
    result = cli(tmp_path, expected, changed(actual, value))
    assert result.returncode == 2 and "EVIDENCE INVALID" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "outcome,exit_code",
    [
        ({"status": "completed", "result": None}, 1),
        ({"status": "aborted", "reason": "mark"}, 0),
        ({"status": "failed", "error": {"message": "fixture failure"}}, 1),
    ],
)
def test_valid_task_outcomes_and_absent_nonterminal_outcome_remain_comparable(
    tmp_path, outcome, exit_code
):
    expected = fixture("durable-abort-v1")
    actual = copy.deepcopy(expected)
    value = observed(actual)
    assert "outcome" not in value["admitted"]["state"]
    assert "outcome" not in value["running"]["state"]
    value["final"]["state"]["outcome"] = outcome
    result = cli(tmp_path, expected, changed(actual, value))
    assert result.returncode == exit_code
    assert "INVALID" not in result.stderr and "Traceback" not in result.stderr


def synthetic_database_sidecar(root):
    """Regular-file validator fixtures, not SQLite execution evidence."""
    rows = []
    for stored, current in contract.POLICIES:
        name = f"{stored}-{current}"
        path = root / name / "session.sqlite"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"SQLite format 3\0synthetic validator bytes")
        rows.append(
            {
                "id": name,
                "path": str(path.resolve()),
                "bytes": path.stat().st_size,
                "sha256": oracle.sha(path),
                "closed": True,
            }
        )
    return rows


def test_database_sidecar_attests_actual_regular_file_bytes(tmp_path):
    rows = synthetic_database_sidecar(tmp_path)
    assert oracle.database_evidence(tmp_path, rows) == {
        f"{row['id']}/session.sqlite": oracle.sha(Path(row["path"])) for row in rows
    }


@pytest.mark.parametrize(
    "fault",
    [
        "empty",
        "missing-variant",
        "duplicate",
        "unknown",
        "not-closed",
        "closed-number",
        "size-bool",
        "wrong-size",
        "wrong-hash",
        "changed-file",
        "missing-file",
        "escape",
        "leaf-symlink",
        "parent-symlink",
        "directory",
    ],
)
def test_database_sidecar_faults_fail_closed(tmp_path, fault):
    root = tmp_path / "case"
    rows = synthetic_database_sidecar(root)
    file = Path(rows[0]["path"])
    if fault == "empty":
        rows.clear()
    elif fault == "missing-variant":
        rows.pop()
    elif fault == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif fault == "unknown":
        rows[0]["id"] = "unknown"
    elif fault == "not-closed":
        rows[0]["closed"] = False
    elif fault == "closed-number":
        rows[0]["closed"] = 1
    elif fault == "size-bool":
        rows[0]["bytes"] = True
    elif fault == "wrong-size":
        rows[0]["bytes"] += 1
    elif fault == "wrong-hash":
        rows[0]["sha256"] = "0" * 64
    else:
        corrupt_database_file(tmp_path, root, file, rows, fault)
    with pytest.raises((oracle.EvidenceError, OSError)):
        oracle.database_evidence(root, rows)


def corrupt_database_file(tmp_path, root, file, rows, fault):
    if fault == "changed-file":
        file.write_bytes(b"different bytes")
    elif fault == "missing-file":
        file.unlink()
    elif fault == "escape":
        rows[0]["path"] = str(root / ".." / "outside.sqlite")
    elif fault == "leaf-symlink":
        other = tmp_path / "other.sqlite"
        file.rename(other)
        file.symlink_to(other)
    elif fault == "parent-symlink":
        other = tmp_path / "other-directory"
        file.parent.rename(other)
        file.parent.symlink_to(other, target_is_directory=True)
    else:
        file.unlink()
        file.mkdir()


def synthetic_chunk_sidecar(trace):
    """Use retained real settled text to isolate chunk-validator behavior."""
    rows = []
    for scenario in observed(trace)["scenarios"]:
        chunks = []
        for operation in scenario["operations"]:
            if operation["op"] == "exec":
                for stream, text in operation["streams"].items():
                    if text:
                        middle = len(text) // 2
                        chunks.extend(
                            {"text": part, "info": {"stream": stream}}
                            for part in (text[:middle], text[middle:])
                        )
        rows.append({"id": scenario["id"], "chunks": chunks})
    return {"schema_version": 1, "scenarios": rows}


def test_chunk_sidecar_preserves_both_scenarios_without_fixing_chunk_partition():
    trace = fixture("env-conformance-v1")
    sidecar = synthetic_chunk_sidecar(trace)
    assert sidecar["scenarios"][0]["chunks"] == []
    oracle.chunk_evidence(sidecar, trace)
    chunks = sidecar["scenarios"][1]["chunks"]
    text = "".join(row["text"] for row in chunks)
    sidecar["scenarios"][1]["chunks"] = [{"text": text, "info": {"stream": "stdout"}}]
    oracle.chunk_evidence(sidecar, trace)


@pytest.mark.parametrize(
    "fault",
    [
        "empty",
        "missing-scenario",
        "duplicate",
        "unknown",
        "version-bool",
        "missing-chunks",
        "text-type",
        "stream",
        "info-shape",
        "different-text",
        "same-stream-order",
    ],
)
def test_chunk_sidecar_faults_fail_closed(fault):
    trace = fixture("env-conformance-v1")
    sidecar = synthetic_chunk_sidecar(trace)
    chunks = sidecar["scenarios"][1]["chunks"]
    if fault == "empty":
        sidecar = []
    elif fault == "missing-scenario":
        sidecar["scenarios"].pop()
    elif fault == "duplicate":
        sidecar["scenarios"].append(copy.deepcopy(sidecar["scenarios"][0]))
    elif fault == "unknown":
        sidecar["scenarios"][0]["id"] = "unknown"
    elif fault == "version-bool":
        sidecar["schema_version"] = True
    elif fault == "missing-chunks":
        chunks.clear()
    elif fault == "text-type":
        chunks[0]["text"] = True
    elif fault == "stream":
        chunks[0]["info"]["stream"] = "unknown"
    else:
        corrupt_chunk_payload(chunks, fault)
    with pytest.raises(oracle.EvidenceError):
        oracle.chunk_evidence(sidecar, trace)


def corrupt_chunk_payload(chunks, fault):
    if fault == "info-shape":
        chunks[0]["info"] = None
    elif fault == "different-text":
        chunks[0]["text"] += " changed"
    else:
        chunks.reverse()


@pytest.mark.parametrize("case", ["tool-recovery-v1", "env-conformance-v1"])
def test_empty_sidecar_is_outer_invalid_not_verified(tmp_path, monkeypatch, case):
    """Isolate reference_case wiring; no Node/bootstrap/runtime pass is asserted."""
    config = oracle.load(oracle.FIXTURES / "manifest.json")
    selected = next(row for row in config["cases"] if row["id"] == case)
    output = tmp_path / "output"
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    def execute(command, label, timeout):
        target = output / case
        oracle.dump(target / "trace.json", fixture(case))
        oracle.dump(target / "resolutions.json", [])
        name = (
            "database-evidence.json"
            if case == "tool-recovery-v1"
            else "raw-chunks.json"
        )
        oracle.dump(target / name, [])

    def replay(source, destination, fixtures):
        destination.mkdir()
        oracle.reference_case(
            selected, snapshot, destination, fixtures, execute, {}, {}, {}, {}
        )
        raise AssertionError("Empty sidecar unexpectedly verified")

    monkeypatch.setattr(oracle, "verify_sources", lambda *args: None)
    monkeypatch.setattr(oracle, "verify_resolutions", lambda *args: {})
    monkeypatch.setattr(oracle, "module_dependencies", lambda *args: None)
    monkeypatch.setattr(oracle, "reference", replay)
    assert (
        oracle.main(
            ["reference", "--pi-source", str(tmp_path), "--output", str(output)]
        )
        == 2
    )
    assert not (output / case / "result.json").exists()

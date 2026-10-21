"""Host-independent real native recipe/schema/CLI tests, not Node evidence."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/durable_memory_comparison.py"
    spec = importlib.util.spec_from_file_location("table_tests", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_actual_native_recipe_and_separate_python_spelling_witness():
    m = module()
    value, witness = m.native(m.load(m.INPUT))
    m.trace(value)
    assert [s["id"] for s in value["scenarios"]] == list(m.SCENARIOS)
    assert sum(len(s["operations"]) for s in value["scenarios"]) == 128
    assert m.comparison(value, value) == 0
    assert [x["python_codepoints"] for x in witness["python_spellings"]] == [
        [0xD83D, 0xDE00]
    ] * 5
    rows = {s["id"]: {x["id"]: x for x in s["operations"]} for s in value["scenarios"]}
    assert m.v.decode(rows["identity"]["exact"]["outcome"]["value"])["commitSeq"] == 2
    assert rows["identity"]["closed"]["outcome"]["status"] == "rejected"
    assert (
        rows["ancestry"]["unknown"]["outcome"]["error"]["name"] == "StorageRequestError"
    )
    assert rows["replacement"]["no-fallback"]["outcome"]["value"] == ["undefined"]
    for name in ("legacy-entry-order", "legacy-task-order"):
        outcome = rows["cursor"][name]["outcome"]
        assert outcome["status"] == "rejected"
        assert outcome["error"]["name"] == "StorageRequestError"
        assert "cursor continues" in outcome["error"]["message"]
    local = rows["ancestry"]
    page = m.v.decode(local["raw-local-page"]["outcome"]["value"])
    assert [entry["id"] for entry in page["items"]] == [44]
    assert page["next"] == {"after": 44, "order": "descending"}
    point = m.v.decode(local["raw-local-point"]["outcome"]["value"])
    assert point["entry"]["id"] == 43 and point["commitSeq"] == 3
    marker = m.v.decode(local["raw-local-marker"]["outcome"]["value"])
    assert marker["id"] == marker["head"] == 43
    for name in ("raw-mincap-desc", "raw-mincap-asc"):
        assert m.v.decode(local[name]["outcome"]["value"])["items"] == []
    assert local["raw-point-above-cap"]["outcome"]["value"] == ["undefined"]
    capped = m.v.decode(local["raw-asc-capped-local"]["outcome"]["value"])
    assert [entry["id"] for entry in capped["items"]] == [43, 44]
    facts = m.v.decode(rows["ownership"]["owned"]["outcome"]["value"])
    assert all(
        facts[k] is True
        for k in ("source_detached", "placements_independent", "read_detached")
    )


def cli(m, tmp_path, expected, actual, wanted):
    left = tmp_path / "expected.json"
    right = tmp_path / "actual.json"
    m.dump(left, expected)
    m.dump(right, actual)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    p = subprocess.run(
        [sys.executable, m.__file__, "compare", str(left), str(right)],
        env={"PATH": os.defpath, "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert p.returncode == wanted, p.stderr
    assert "Traceback" not in p.stderr
    return p


@pytest.mark.parametrize("name", module().MUTANTS)
def test_real_native_valid_mutants_are_cli1_never_invalid2(tmp_path, name):
    m = module()
    raw, _ = m.native(m.load(m.INPUT))
    mutant = m._mutation(raw, name)
    assert mutant != raw
    m.trace(mutant)
    cli(m, tmp_path, raw, raw, 0)
    cli(m, tmp_path, raw, mutant, 1)


@pytest.mark.parametrize(
    "corrupt",
    [
        "version",
        "phase",
        "empty",
        "scenario-null",
        "outcome-null",
        "bad-record-id",
        "bad-cursor",
        "unknown-error",
    ],
)
def test_invalid_trace_cli2_is_not_a_valid_mismatch(tmp_path, corrupt):
    m = module()
    raw, _ = m.native(m.load(m.INPUT))
    bad = json.loads(json.dumps(raw))
    if corrupt == "version":
        bad["schema_version"] = True
    elif corrupt == "phase":
        bad["phase"] = "memory-tables-a2a-v1"
    elif corrupt == "empty":
        bad["scenarios"] = []
    elif corrupt == "scenario-null":
        bad["scenarios"][0] = None
    elif corrupt == "outcome-null":
        bad["scenarios"][0]["operations"][0]["outcome"] = None
    elif corrupt == "bad-record-id":
        row = bad["scenarios"][0]["operations"][5]
        tree = m.v.decode(row["outcome"]["value"])
        tree["entry"]["id"] = True
        row["outcome"]["value"] = m.v.encode(tree)
    elif corrupt == "bad-cursor":
        row = bad["scenarios"][3]["operations"][2]
        tree = m.v.decode(row["outcome"]["value"])
        tree["next"]["after"] = True
        row["outcome"]["value"] = m.v.encode(tree)
    else:
        bad["scenarios"][0]["operations"][-1]["outcome"]["error"]["name"] = (
            "AttributeError"
        )
    cli(m, tmp_path, raw, raw, 0)
    p = cli(m, tmp_path, raw, bad, 2)
    assert "Invalid table evidence" in p.stderr


def test_explicit_error_projection_preserves_raw_fields_and_rejections():
    m = module()
    raw, _ = m.native(m.load(m.INPUT))
    other = json.loads(json.dumps(raw))
    other["backend"] = "node"
    for scenario in other["scenarios"]:
        for step in scenario["operations"]:
            if step["outcome"]["status"] == "rejected":
                e = step["outcome"]["error"]
                e["name"] = {
                    "RuntimeError": "Error",
                    "StorageClosedError": "Error",
                }.get(e["name"], e["name"])
    assert m.comparison(raw, other) == 0
    assert (
        raw["scenarios"][0]["operations"][-1]["outcome"]["error"]["name"]
        == "StorageClosedError"
    )
    other["scenarios"][0]["operations"][-1]["outcome"]["error"]["message"] = "different"
    assert m.comparison(raw, other) == 1


@pytest.mark.parametrize("after", [-(2**53 - 1), -1, 0, 2**53 - 1])
def test_number_transport_converts_only_signed_cursor_integer_role(after):
    m = module()
    raw = m.v.decode(m.v.encode({"after": after, "order": "ascending"}))
    assert m._cursor(raw)["after"] == after and type(m._cursor(raw)["after"]) is int
    assert m.v.decode(m.v.encode(-0.0)) == 0.0
    assert m.v.encode(m.v.decode(m.v.encode(-0.0))) == ["number", "8000000000000000"]


@pytest.mark.parametrize("change", ["phase", "ref", "closure", "hash", "parent"])
def test_closed_table_manifest_fails_before_runtime_or_output(
    tmp_path, monkeypatch, change, capsys
):
    m = module()
    value = m.load(m.MANIFEST)
    if change == "phase":
        value["phase"] = "memory-tables-a2a-v1"
    elif change == "ref":
        value["reference_commit"] = "0" * 40
    elif change == "closure":
        value["closure"].pop("src/pipy_harness/native/durable/memory.py")
    elif change == "hash":
        value["closure"]["src/pipy_harness/native/durable/memory.py"] = "0" * 64
    else:
        value["baseline_manifest_sha256"] = "0" * 64
    path = tmp_path / "manifest.json"
    m.dump(path, value)
    monkeypatch.setattr(m, "MANIFEST", path)
    assert (
        m.main(
            [
                "run",
                "--acceptance",
                str(tmp_path / "absent"),
                "--output",
                str(tmp_path / "output"),
            ]
        )
        == 2
    )
    assert "Invalid table evidence" in capsys.readouterr().err
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "mutation",
    ["none", *module().MUTANTS, "invalid", "empty-conformance", "missing-case"],
)
def test_run_controller_classifies_and_attests_0_1_2_without_host_node(
    tmp_path, monkeypatch, capsys, mutation
):
    # Unit-only bootstrap/child replacements, NOT an actual pinned execution.
    m = module()
    parent = tmp_path / "parent"
    snapshot = parent / "reference/snapshot"
    snapshot.mkdir(parents=True)
    m.dump(
        parent / "acceptance.json",
        {
            "status": "acceptance-verified",
            "complete": True,
            "execution_id": "unit-parent",
        },
    )
    for name in ("run.json", "source-sha256.json", "dependency-sha256.json"):
        m.dump(snapshot.parent / name, {})
    monkeypatch.setattr(m, "configuration", lambda: {"reference_sources": {}})
    monkeypatch.setattr(
        m.o, "manifest", lambda: {"runtime": {"node": "unit-node", "npm": "unit-npm"}}
    )
    attestations = []
    monkeypatch.setattr(
        m.v.artifacts, "attest", lambda *_: attestations.append("bundle")
    )
    monkeypatch.setattr(
        m.o.acceptance,
        "attest_reference",
        lambda *_: attestations.append("reference") or {"installed_dependencies": []},
    )
    monkeypatch.setattr(m.o, "verify_sources", lambda *_: None)
    monkeypatch.setattr(m.o, "verify_resolutions", lambda *_: {})
    monkeypatch.setattr(m.o, "module_dependencies", lambda *_: None)
    monkeypatch.setattr(m, "execute_mutants", lambda *_: [])
    original = m.native
    observed, _ = original(m.load(m.INPUT))
    calls = []

    def native(value):
        raw, witness = original(value)
        if mutation == "invalid":
            raw["scenarios"][0]["operations"][0]["outcome"] = None
        elif mutation in m.MUTANTS:
            raw = m._mutation(raw, mutation)
        return raw, witness

    monkeypatch.setattr(m.o, "run", _unit_child(m, observed, calls, mutation))
    monkeypatch.setattr(m, "native", native)
    output = tmp_path / "output"
    wanted = 0 if mutation == "none" else 1 if mutation in m.MUTANTS else 2
    assert (
        m.main(["run", "--acceptance", str(parent), "--output", str(output)]) == wanted
    )
    assert calls == ["node-version", "npm-version", "pi-memory"]
    if wanted == 2:
        diagnostic = capsys.readouterr().err
        assert "Invalid table evidence" in diagnostic
        expected = {
            "invalid": "Malformed outcome",
            "empty-conformance": "Conformance raw call counts drift",
            "missing-case": "Missing exported conformance case",
        }[mutation]
        assert expected in diagnostic
        assert not (output / "result.json").exists()
        assert not (output / "mismatch-report.json").exists()
        return
    assert attestations == ["bundle", "reference", "reference", "bundle"]
    result = m.load(output / "result.json")
    assert result["status"] == ("PASS" if wanted == 0 else "mismatch")
    for name, digest in result["artifacts_sha256"].items():
        assert m.o.sha(output / name) == digest
    if wanted:
        report = m.load(output / "mismatch-report.json")
        assert report["status"] == "mismatch"
        assert m.o.sha(output / report["expected"]) == report["expected_sha256"]
        assert m.o.sha(output / report["actual"]) == report["actual_sha256"]
        assert (
            m.comparison(
                m.load(output / report["expected"]), m.load(output / report["actual"])
            )
            == 1
        )
    else:
        assert not (output / "mismatch-report.json").exists()


def test_mutant_driver_rejects_invalid2_even_after_positive0(tmp_path, monkeypatch):
    m = module()
    raw, _ = m.native(m.load(m.INPUT))
    m.dump(tmp_path / "observed.json", raw)
    calls = []

    def child(command, cwd, folder, identity, env, timeout):
        code = 0 if identity.endswith("positive") else 2
        calls.append((identity, code))
        m.dump(folder / (identity + ".json"), {"exit_code": code})
        if code:
            raise m.o.EvidenceError("unit invalid child")
        return b""

    monkeypatch.setattr(m.o, "run", child)
    with pytest.raises(m.o.EvidenceError, match="valid mismatch1, never invalid2"):
        m.execute_mutants(tmp_path, raw, {"HOME": str(tmp_path), "PATH": os.defpath})
    assert calls == [("field-positive", 0), ("field-negative", 2)]


def _unit_child(m, observed, calls, mutation):
    def child(command, cwd, folder, identity, env, timeout):
        calls.append(identity)
        if identity.endswith("-version"):
            return ("unit-" + identity.removesuffix("-version")).encode()
        assert identity == "pi-memory"
        m.dump(folder / "observed.json", observed)
        m.dump(folder / "resolutions.json", [])
        m.dump(folder / "pi-memory.json", {"exit_code": 0})
        m.dump(
            folder / "reference-context.json",
            {
                "schema_version": 1,
                "phase": m.PHASE,
                "scenarios": len(m.SCENARIOS),
                "exported_cases": [
                    {"name": name, "callback_calls": 1, "closed": True, "operations": 1}
                    for name in m.EXPORTED
                ],
            },
        )
        errors = [
            dict(step["outcome"]["error"], stack="unit diagnostic")
            for scenario in observed["scenarios"]
            for step in scenario["operations"]
            if step["outcome"]["status"] == "rejected"
        ]
        m.dump(
            folder / "conformance-raw.json",
            {
                "schema_version": 1,
                "errors": errors,
                "calls": [
                    {
                        "case": name,
                        "op": "close",
                        "args": ["array", []],
                        "outcome": {"status": "fulfilled", "value": ["undefined"]},
                    }
                    for name in m.EXPORTED
                ],
            },
        )
        if mutation == "empty-conformance":
            m.dump(
                folder / "conformance-raw.json",
                {"schema_version": 1, "errors": errors, "calls": []},
            )
        elif mutation == "missing-case":
            context = m.load(folder / "reference-context.json")
            context["exported_cases"].pop()
            m.dump(folder / "reference-context.json", context)
        return b""

    return child


def _valid_operation_arguments():
    values = {
        op: {"id": 2}
        for op in (
            "conversation",
            "entry",
            "task",
            "submission",
            "ownership",
            "document",
            "document_ownership",
        )
    }
    values.update(
        {
            "mint": {},
            "close": {},
            "commit": {"writes": []},
            "request": {"conversation_id": 1, "request_id": "synthetic"},
            "head": {"conversation_id": 1, "bound": 2},
            "find_document": {
                "address": {"kind": "cache", "scope": {"kind": "session"}}
            },
        }
    )
    for op in ("scan_conversations", "scan_entries", "scan_tasks", "scan_submissions"):
        values[op] = {
            "query": {"conversationId": 1} if op == "scan_entries" else {},
            "limit": 2,
            "cursor": {"after": 0, "order": "ascending"},
        }
    values["scan_documents"] = {
        "query": {"scope": {"kind": "session"}, "at": "current"},
        "limit": 2,
        "cursor": {"after": 0, "order": "descending"},
    }
    return values


@pytest.mark.parametrize("op", sorted(module().OPERATIONS))
def test_existing_argument_guard_covers_all_18_registered_roles(op):
    m = module()
    arguments = _valid_operation_arguments()
    assert set(arguments) == m.OPERATIONS
    m._recipe_args(op, arguments[op])
    with pytest.raises(m.o.EvidenceError, match="^Malformed operation arguments$"):
        m._recipe_args(op, None)


def _recorded_step(raw, op, *, scenario=None, identity=None):
    matches = [
        step
        for row in raw["scenarios"]
        for step in row["operations"]
        if step["op"] == op
        and (scenario is None or row["id"] == scenario)
        and (identity is None or step["id"] == identity)
    ]
    assert matches, (op, scenario, identity)
    return matches[0]


@pytest.mark.parametrize("op", sorted(module().OPERATIONS - {"submission"}))
def test_recorded_17_roles_null_argument_pack_is_public_cli_invalid2(tmp_path, op):
    m = module()
    raw, _ = m.native(m.load(m.INPUT))
    assert (
        {step["op"] for row in raw["scenarios"] for step in row["operations"]}
        == (m.OPERATIONS - {"submission"})
    )  # Exact submission is not a recorded recipe operation; direct helper guard remains.
    bad = json.loads(json.dumps(raw))
    _recorded_step(bad, op)["args"] = ["null"]
    cli(m, tmp_path, raw, raw, 0)
    p = cli(m, tmp_path, raw, bad, 2)
    assert (
        p.stderr.strip()
        == "Invalid table evidence: EvidenceError: Malformed operation arguments"
    )


@pytest.mark.parametrize(
    "op,args,diagnostic",
    [
        ("commit", {"writes": None}, "Invalid scripted writes array"),
        (
            "commit",
            {"writes": [{"type": "entry", "value": {"id": 2}}]},
            "Missing or unknown record fields",
        ),
        (
            "entry",
            {"id": True},
            "Expected exact safe integer in the selected role domain",
        ),
        ("scan_entries", {"query": None, "limit": 1}, "Expected plain record object"),
        (
            "scan_entries",
            {"query": {"conversationId": 1}, "limit": 1, "cursor": {"after": True}},
            "Nonintegral cursor position",
        ),
        ("scan_tasks", {"query": {}, "limit": True}, "Malformed scan size"),
        (
            "request",
            {"conversation_id": 1, "request_id": None},
            "Malformed request key",
        ),
        ("mint", {"id": 1}, "Malformed operation arguments"),
        ("close", {"id": 1}, "Malformed operation arguments"),
    ],
)
def test_recorded_typed_argument_fields_fail_at_their_actual_guard_cli2(
    tmp_path, op, args, diagnostic
):
    m = module()
    raw, _ = m.native(m.load(m.INPUT))
    bad = json.loads(json.dumps(raw))
    _recorded_step(bad, op)["args"] = m.v.encode(args)
    cli(m, tmp_path, raw, raw, 0)
    p = cli(m, tmp_path, raw, bad, 2)
    assert p.stderr.strip().endswith(": " + diagnostic)


@pytest.mark.parametrize("change", ["id", "optional-field", "query", "cursor"])
def test_recorded_valid_wrong_arguments_remain_semantic_cli1(tmp_path, change):
    m = module()
    raw, _ = m.native(m.load(m.INPUT))
    bad = json.loads(json.dumps(raw))
    op = {
        "id": "entry",
        "optional-field": "commit",
        "query": "scan_tasks",
        "cursor": "scan_entries",
    }[change]
    step = (
        _recorded_step(bad, op, scenario="identity", identity="seed")
        if change == "optional-field"
        else _recorded_step(bad, op)
    )
    args = m.v.decode(step["args"])
    if change == "id":
        args["id"] = 999
    elif change == "optional-field":
        del args["writes"][0]["value"]["data"]
    elif change == "query":
        args["query"]["status"] = "terminal"
    else:
        args["cursor"] = {"after": -1, "order": "ascending"}
    m._recipe_args(op, args)  # Wrong values are still valid call shapes.
    step["args"] = m.v.encode(args)
    assert bad != raw
    m.trace(bad)
    cli(m, tmp_path, raw, raw, 0)
    cli(m, tmp_path, raw, bad, 1)


@pytest.mark.parametrize(
    "op,args,diagnostic",
    [
        (
            "document",
            {"id": True},
            "Expected exact safe integer in the selected role domain",
        ),
        (
            "document",
            {"id": 2, "at": None},
            "Only current document reads are supported",
        ),
        ("find_document", {"address": None}, "Expected plain record object"),
        (
            "scan_documents",
            {"query": {"scope": {"kind": "session"}, "at": "current"}, "limit": True},
            "Malformed scan size",
        ),
    ],
)
def test_document_argument_forms_are_cli_invalid2_not_semantic_mismatch(
    tmp_path, op, args, diagnostic
):
    m = module()
    raw, _ = m.native(m.load(m.INPUT))
    bad = json.loads(json.dumps(raw))
    _recorded_step(bad, op)["args"] = m.v.encode(args)
    cli(m, tmp_path, raw, raw, 0)
    result = cli(m, tmp_path, raw, bad, 2)
    assert diagnostic in result.stderr


@pytest.mark.parametrize(
    "op,args",
    [
        ("document", {"id": 999}),
        (
            "find_document",
            {"address": {"scope": {"kind": "session"}, "kind": "absent"}},
        ),
        (
            "scan_documents",
            {
                "query": {"scope": {"kind": "task", "taskId": 999}, "at": "current"},
                "limit": 2,
            },
        ),
    ],
)
def test_valid_wrong_document_arguments_stay_public_mismatch1(tmp_path, op, args):
    m = module()
    raw, _ = m.native(m.load(m.INPUT))
    bad = json.loads(json.dumps(raw))
    _recorded_step(bad, op)["args"] = m.v.encode(args)
    m.trace(bad)
    cli(m, tmp_path, raw, raw, 0)
    cli(m, tmp_path, raw, bad, 1)

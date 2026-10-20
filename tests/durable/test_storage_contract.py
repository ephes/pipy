"""Port DTOs are validated owned values; this is not Memory/Session conformance."""

import pytest

from pipy_harness.native.durable.records import ConversationRecord, RecordError
from pipy_harness.native.durable.storage import (
    ConversationQuery,
    Cursor,
    DocumentAddress,
    DocumentQuery,
    EntryLookup,
    EntryQuery,
    Page,
    Query,
    StorageRequestError,
    StorageWrite,
    StoredDocument,
    SubmissionQuery,
    TaskQuery,
)


@pytest.mark.parametrize(
    "cls,value",
    [
        (
            ConversationQuery,
            {"ownerConversationId": 1, "ownerTaskId": 2, "order": "descending"},
        ),
        (
            EntryQuery,
            {
                "conversationId": 1,
                "minEntryId": 2,
                "maxEntryId": 4,
                "order": "ascending",
            },
        ),
        (
            TaskQuery,
            {
                "conversationId": 1,
                "kind": "\ud800",
                "status": "sleeping",
                "abortRequested": False,
                "background": True,
            },
        ),
        (SubmissionQuery, {"conversationId": 1, "status": "unanswered"}),
        (
            DocumentQuery,
            {
                "scope": {"kind": "conversation", "conversationId": 1},
                "at": 3,
                "kind": "\ud801",
            },
        ),
        (
            DocumentAddress,
            {
                "scope": {"kind": "task", "taskId": 3},
                "kind": "__proto__",
                "key": "\ud800",
            },
        ),
    ],
)
def test_all_query_shapes_are_owned(cls, value):
    query = cls(value)
    value.clear()
    first = query.to_tree()
    assert first
    first.clear()
    assert query.to_tree()


@pytest.mark.parametrize(
    "cls,value",
    [
        (EntryQuery, {}),
        (EntryQuery, {"conversationId": True}),
        (TaskQuery, {"background": 1}),
        (TaskQuery, {"status": "done"}),
        (SubmissionQuery, {"status": "terminal"}),
        (ConversationQuery, {"order": "random"}),
        (DocumentAddress, {"scope": {"kind": "session", "taskId": 1}, "kind": "x"}),
        (DocumentQuery, {"scope": {"kind": "session"}, "at": False}),
        (Query, {}),
        (TaskQuery, {"unrecognized": None}),
    ],
)
def test_queries_fail_closed(cls, value):
    with pytest.raises(RecordError):
        cls(value)


@pytest.mark.parametrize("value", [-(2**53 - 1), -1, 0, 2**53 - 1])
def test_cursor_positions_are_signed_safe_numbers(value):
    assert Cursor(value, "ascending").after == value


@pytest.mark.parametrize("value", [True, 1.0, -(2**53), 2**53])
def test_invalid_cursor_values(value):
    with pytest.raises(RecordError):
        Cursor(value)


def test_pages_require_concrete_immutable_records_and_cursor():
    record = ConversationRecord({"id": 1})
    assert Page((record,), Cursor(0)).items == (record,)
    with pytest.raises(StorageRequestError):
        Page([record])
    with pytest.raises(StorageRequestError):
        Page(({"id": 1},))
    with pytest.raises(StorageRequestError):
        Page((record,), {})
    with pytest.raises(RecordError):
        EntryLookup(record, 1)


def test_all_document_write_roles_are_retained_without_claiming_backend_support():
    create = {"id": 3, "kind": "\ud800", "key": "\ud801", "scope": {"kind": "session"}}
    writes = [
        {"type": "conversation", "value": {"id": 1}},
        {
            "type": "document.create",
            "record": create,
            "content": {"kind": "base", "version": 1, "value": {"x": [1]}},
        },
        {
            "type": "document.change",
            "id": 3,
            "content": {"kind": "delta", "version": 1, "ops": []},
        },
        {
            "type": "document.copy",
            "record": {**create, "id": 4},
            "source": {"id": 3, "at": 2},
        },
        {"type": "document.retire", "id": 3},
    ]
    owned = [StorageWrite(write) for write in writes]
    writes[1]["content"]["value"]["x"].clear()
    assert owned[1].to_tree()["content"]["value"] == {"x": [1]}
    output = owned[1].to_tree()
    output["content"]["value"]["x"].clear()
    assert owned[1].to_tree()["content"]["value"] == {"x": [1]}


@pytest.mark.parametrize(
    "value",
    [
        {"type": "unknown"},
        {"type": "entry", "value": {"id": 2}},
        {"type": "document.retire", "id": True},
        {
            "type": "document.create",
            "record": {"id": 3, "kind": "", "scope": {"kind": "session"}},
            "content": {"kind": "delta", "version": 1, "ops": []},
        },
        {
            "type": "document.change",
            "id": 3,
            "content": {"kind": "base", "version": 1, "value": []},
        },
    ],
)
def test_malformed_write_dtos_are_invalid(value):
    with pytest.raises(RecordError):
        StorageWrite(value)


def test_materialized_document_owns_every_slot():
    value = {
        "record": {"id": 3, "kind": "d", "scope": {"kind": "session"}, "createdAt": 1},
        "version": 1,
        "value": {"nested": [None]},
        "deltasSinceBase": 0,
    }
    document = StoredDocument(value)
    value["value"]["nested"].clear()
    assert document.to_tree()["value"] == {"nested": [None]}


def comparison_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts/durable_storage_comparison.py"
    spec = importlib.util.spec_from_file_location("a1_value_record_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scripted_product_probe_is_real_codec_roundtrip_and_exact_bits():
    module = comparison_module()
    source = module.load(module.INPUT)
    result = module.native(source)
    assert module.comparison(result, result) == 0
    assert len(result["records"]) == 31
    assert len(result["values"]) == 16
    assert result["values"][4] == ["number", "8000000000000000"]
    assert result["facts"] == {
        "source_detached": True,
        "placements_independent": True,
        "read_detached": True,
        "omission_distinct": True,
        "nonfinite_rejected": [True, True, True],
        "utf16_key_identity": True,
    }


@pytest.mark.parametrize("mutation", ["field", "id", "number", "alias", "invalid"])
def test_actual_public_comparison_cli_valid_mutants1_and_invalid2(tmp_path, mutation):
    import json
    import os
    import subprocess
    import sys

    module = comparison_module()
    original = module.native(module.load(module.INPUT))
    mutant = json.loads(json.dumps(original))
    if mutation in {"field", "id"}:
        entry = next(row for row in mutant["records"] if row["kind"] == "entry")
        tree = module.decode(entry["value"])
        if mutation == "field":
            del tree["data"]
        else:
            tree["id"] = 100
        entry["value"] = module.encode(tree)
    elif mutation == "number":
        mutant["values"][4] = ["number", "0000000000000000"]
    elif mutation == "alias":
        mutant["facts"]["placements_independent"] = False
    else:
        mutant["records"][0]["value"] = ["object", []]
    expected = tmp_path / "expected.json"
    actual = tmp_path / "actual.json"
    module.dump(expected, original)
    module.dump(actual, mutant)
    (tmp_path / "home").mkdir()
    env = {"PATH": os.defpath, "HOME": str(tmp_path / "home")}
    for path, wanted in [(expected, 0), (actual, 2 if mutation == "invalid" else 1)]:
        run = subprocess.run(
            [sys.executable, module.__file__, "compare", str(expected), str(path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert run.returncode == wanted, run.stderr
        assert "Traceback" not in run.stderr


@pytest.mark.parametrize(
    "value",
    [
        ["number", "7ff0000000000000"],
        ["number", "00"],
        ["number", "zz"],
        ["string", "d8"],
        ["bool", 1],
        ["null", None],
        ["unknown", None],
        ["object", [["0061", ["null"]], ["0061", ["null"]]]],
    ],
)
def test_comparison_transport_fails_closed_without_narrowing_product_json(value):
    module = comparison_module()
    with pytest.raises((ValueError, UnicodeError)):
        module.decode(value)


@pytest.mark.parametrize(
    "corruption,diagnostic",
    [
        ("version", "Unsupported native comparison manifest"),
        ("ref", "Unsupported native comparison manifest"),
        ("closure", "a1 closed artifact inventory"),
        ("hash", "Native comparison artifact drift"),
    ],
)
def test_own_closed_manifest_guards_are_actual_cli_invalid2(
    tmp_path, corruption, diagnostic
):
    import json
    import os
    import subprocess
    import sys

    module = comparison_module()
    value = module.load(module.MANIFEST)
    if corruption == "version":
        value["schema_version"] = True
    elif corruption == "ref":
        value["reference_commit"] = "0" * 40
    elif corruption == "closure":
        value["closure"].pop(next(iter(value["closure"])))
    else:
        value["closure"]["scripts/durable_storage_comparison.py"] = "0" * 64
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value))
    program = """import importlib.util,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('a1',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.MANIFEST=Path(sys.argv[2]);sys.exit(m.main(['run','--acceptance',sys.argv[3],'--output',sys.argv[4]]))
"""
    env = {"PATH": os.defpath, "HOME": str(tmp_path)}
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            module.__file__,
            str(path),
            str(tmp_path / "absent-parent"),
            str(tmp_path / "output"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert run.returncode == 2
    assert diagnostic in run.stderr
    assert "Traceback" not in run.stderr
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("boundary", ["query", "write", "document"])
def test_storage_dtos_reject_nonplain_keys_without_hooks(boundary):
    from pipy_harness.native.durable.codec import JsonValueError

    calls = []

    class Key(str):
        def __hash__(self):
            calls.append("hash")
            return super().__hash__()

        def __eq__(self, other):
            calls.append("eq")
            return super().__eq__(other)

    if boundary == "query":
        value = {Key("kind"): "x"}
        constructor = TaskQuery
    elif boundary == "write":
        value = {"type": "conversation", "value": {Key("id"): 1}}
        constructor = StorageWrite
    else:
        value = {
            "record": {
                "id": 3,
                "kind": "d",
                "scope": {"kind": "session"},
                "createdAt": 1,
            },
            "version": 1,
            Key("value"): None,
            "deltasSinceBase": 0,
        }
        constructor = StoredDocument
    calls.clear()
    with pytest.raises(JsonValueError, match="exact string keys"):
        constructor(value)
    assert calls == []


@pytest.mark.parametrize("boundary", ["write", "document"])
@pytest.mark.parametrize("left,right", [(-0.0, 0.0), (1, 1.0)])
def test_storage_dto_equality_and_hash_retain_value_representation(
    boundary, left, right
):
    def make(value):
        if boundary == "write":
            return StorageWrite(
                {
                    "type": "entry",
                    "value": {"id": 2, "conversationId": 1, "kind": "", "data": value},
                }
            )
        return StoredDocument(
            {
                "record": {
                    "id": 3,
                    "kind": "",
                    "scope": {"kind": "session"},
                    "createdAt": 1,
                },
                "version": 1,
                "value": {"data": value},
                "deltasSinceBase": 0,
            }
        )

    a, same, b = make(left), make(left), make(right)
    assert a == same and hash(a) == hash(same)
    assert a != b and len({a, same, b}) == 2


def test_query_equality_is_owned_representation_not_utf16_index_identity():
    a, same = TaskQuery({"kind": "\U0001f600"}), TaskQuery({"kind": "\U0001f600"})
    b = TaskQuery({"kind": "\ud83d\ude00"})
    assert a == same and hash(a) == hash(same)
    assert a != b and len({a, same, b}) == 2


def test_probe_values_are_distinct_js_values_not_python_spelling_claims():
    module = comparison_module()
    values = module.load(module.INPUT)["values"]
    assert len(values) == 16
    assert len({module.json.dumps(value, sort_keys=True) for value in values}) == 16
    assert module.decode(values[-1]) == {"\U0001f600": 1}
    assert next(iter(module.decode(values[-1]))) != "\ud83d\ude00"


@pytest.mark.parametrize(
    "mutation,wanted", [("none", 0), ("native", 1), ("roundtrip", 1), ("invalid", 2)]
)
def test_execute_run_classifies_valid_mismatch_and_records_attested_report(
    tmp_path, monkeypatch, capsys, mutation, wanted
):
    # Unit-only bootstrap replacement: real Node execution is a separate gate.
    module = comparison_module()
    parent = tmp_path / "parent"
    snapshot = parent / "reference/snapshot"
    snapshot.mkdir(parents=True)
    output = tmp_path / "output"
    module.dump(
        parent / "acceptance.json",
        {
            "status": "acceptance-verified",
            "complete": True,
            "execution_id": "unit-parent",
        },
    )
    for name in ("run.json", "source-sha256.json", "dependency-sha256.json"):
        module.dump(snapshot.parent / name, {})
    monkeypatch.setattr(
        module,
        "configuration",
        lambda: {
            "closure": {
                str(module.DRIVER.relative_to(module.ROOT)): module.o.sha(module.DRIVER)
            },
            "reference_sources": {},
        },
    )
    monkeypatch.setattr(
        module.o,
        "manifest",
        lambda: {"runtime": {"node": "unit-node", "npm": "unit-npm"}},
    )
    attestations = []
    monkeypatch.setattr(
        module.artifacts, "attest", lambda *_: attestations.append("bundle")
    )
    monkeypatch.setattr(
        module.o.acceptance,
        "attest_reference",
        lambda *_: attestations.append("reference") or {"installed_dependencies": []},
    )
    monkeypatch.setattr(module.o, "verify_sources", lambda *_: None)
    monkeypatch.setattr(module.o, "verify_resolutions", lambda *_: {})
    monkeypatch.setattr(module.o, "module_dependencies", lambda *_: None)
    monkeypatch.setattr(module, "execute_mutants", lambda *_: [])
    original_native = module.native
    observed = original_native(module.load(module.INPUT))
    calls = []

    monkeypatch.setattr(module.o, "run", _unit_child(module, observed, calls))
    monkeypatch.setattr(
        module, "native", _mutated_native(module, mutation, monkeypatch)
    )
    assert (
        module.main(["run", "--acceptance", str(parent), "--output", str(output)])
        == wanted
    )
    assert calls == ["node-version", "npm-version", "pi-value-records"]
    if wanted == 2:
        assert "Invalid a1 evidence" in capsys.readouterr().err
        assert not (output / "result.json").exists()
        assert not (output / "mismatch-report.json").exists()
        return
    assert attestations == ["bundle", "reference", "reference", "bundle"]
    result = module.load(output / "result.json")
    assert result["status"] == ("mismatch" if wanted == 1 else "PASS")
    for name, digest in result["artifacts_sha256"].items():
        assert module.o.sha(output / name) == digest
    if wanted == 1:
        report = module.load(output / "mismatch-report.json")
        assert report["status"] == "mismatch"
        assert [row["equal"] for row in report["comparisons"]] == (
            [False, True] if mutation == "native" else [True, False]
        )
        for row in report["comparisons"]:
            assert module.o.sha(output / row["expected"]) == row["expected_sha256"]
            assert module.o.sha(output / row["actual"]) == row["actual_sha256"]
            assert (
                module.comparison(
                    module.load(output / row["expected"]),
                    module.load(output / row["actual"]),
                )
                == 0
            ) is row["equal"]
    else:
        assert not (output / "mismatch-report.json").exists()


def _unit_child(module, observed, calls):
    def child(command, cwd, folder, identity, env, timeout):
        calls.append(identity)
        if identity.endswith("-version"):
            return ("unit-" + identity.removesuffix("-version")).encode()
        assert identity == "pi-value-records"
        module.dump(folder / "observed.json", observed)
        for name in (
            "resolutions.json",
            "reference-context.json",
            "pi-value-records.json",
        ):
            module.dump(folder / name, {})
        return b""

    return child


def _mutated_native(module, mutation, monkeypatch):
    original_native = module.native

    def native(value):
        result = original_native(value)
        if mutation == "native":
            result["facts"]["placements_independent"] = False
        elif mutation == "invalid":
            result["values"][0] = ["unknown"]
        elif mutation == "roundtrip":
            original_decode = module.decode_record

            def altered(tree):
                record = original_decode(tree)
                if record.kind == "entry":
                    value = record.to_tree()
                    value["data"] = None
                    return module.RECORD_TYPES["entry"](value)
                return record

            monkeypatch.setattr(module, "decode_record", altered)
        return result

    return native

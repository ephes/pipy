"""Live execute cuts with schema-valid opaque receipt/progress document controls."""

import copy

import pytest
from interrupted_cut_support import counts, logical, raw
from test_tool_interrupted_sdk import cold, start_cut

from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.records import DocumentRecord, RecordError
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import DocumentQuery, StorageWrite
from pipy_harness.native.durable.tool_execution import PROGRESS_DOC, RECEIPT_DOC


def install(root, kind, damage):
    before = raw(root)
    child = next(row for row in before["tasks"] if row["kind"] == "pi.tool")
    assert child["state"]["checkpoint"]["phase"] == "execute"
    original = before["documents"]["pi.tool.progress"][-1]
    value = copy.deepcopy(original)
    if damage == "empty":
        value = {}
    elif damage == "opaque":
        value = {"opaque_future_payload": True}
    elif damage == "newer":
        value["version"] = 99
    elif damage == "wrong_call":
        value["call"]["callId"] = "another-call"
    elif damage == "wrong_key":
        value["key"] = "another-key"
    elif damage == "malformed":
        value.pop("output")
    else:
        raise AssertionError(damage)
    OwnedJson(value)
    session = Session(open_sqlite_storage(str(root / "session.sqlite"), mode="open"))
    try:

        def replace(tx):
            document = tx.doc(
                RECEIPT_DOC if kind == "receipt" else PROGRESS_DOC, task_id=child["id"]
            )
            document.clear()
            document.update(value)

        session.commit(replace)

        def read_saved():
            store = session._owner.storage
            records = store.scan_documents(
                DocumentQuery(
                    {"scope": {"kind": "task", "taskId": child["id"]}, "at": "current"}
                ),
                100,
            ).items
            selected = next(
                record
                for record in records
                if record.to_tree()["kind"] == "pi.tool." + kind
            )
            assert DocumentRecord(selected.to_tree()).to_tree() == selected.to_tree()
            material = store.document(selected.id)
            assert material is not None
            return material.to_tree()

        material = session.read_on_line(read_saved)
        # Actual Storage material and its JSON payload pass record acquisition;
        # only the recovery receipt/progress predicate must refuse them.
        assert material["value"] == value and material["version"] == 1
    finally:
        session.close()
    return child, value


@pytest.mark.parametrize(
    "kind,damage",
    [
        ("receipt", "opaque"),
        ("receipt", "newer"),
        ("progress", "opaque"),
        ("progress", "newer"),
        ("progress", "wrong_call"),
        ("progress", "wrong_key"),
        ("progress", "malformed"),
    ],
)
def test_live_execute_invalid_document_fences_interrupted_synthesis(
    tmp_path, kind, damage
):
    start_cut(tmp_path, "progress-after")
    child, value = install(tmp_path, kind, damage)
    before = counts(tmp_path)
    result, _ = cold(tmp_path)
    assert result["event"] == "result" and result["status"] == "held", result
    state = raw(tmp_path)
    retained = next(row for row in state["tasks"] if row["id"] == child["id"])
    assert retained["state"]["status"] == "pending"
    assert retained["state"]["checkpoint"] == {
        "phase": "blocked",
        "version": 1,
        "intent": child["state"]["checkpoint"]["intent"],
        "reason": "incompatible",
    }
    assert state["documents"]["pi.tool." + kind][-1] == value
    assert not any(e["kind"] == "pi.tool-result" for e in state["entries"])
    assert counts(tmp_path) == before and before["providers"] == 1
    if kind == "progress":
        assert not any(state["documents"].get("pi.tool.receipt", []))
    result, _ = cold(tmp_path)
    assert result["status"] == "held" and counts(tmp_path) == before


@pytest.mark.parametrize("kind", ["receipt", "progress"])
def test_empty_default_document_is_not_an_opaque_payload(tmp_path, kind):
    start_cut(tmp_path, "progress-after")
    install(tmp_path, kind, "empty")
    before = counts(tmp_path)
    result, _ = cold(tmp_path)
    assert result["event"] == "result" and result["status"] == "terminal", result
    after = counts(tmp_path)
    assert after == {**before, "providers": 2}
    entries = [e for e in raw(tmp_path)["entries"] if e["kind"] == "pi.tool-result"]
    assert len(entries) == 1
    full = entries[0]["data"]["nativeToolReceipt"]["nativeToolOutput"]
    assert full["ending"] == "interrupted"
    assert full["recovery"]["attempts"] == (None if kind == "progress" else 1)


@pytest.mark.parametrize("value", [None, []], ids=["null", "list"])
def test_nonobject_document_root_is_not_a_schema_valid_live_mutant(tmp_path, value):
    start_cut(tmp_path, "progress-after")
    before, graph = counts(tmp_path), logical(tmp_path)
    with pytest.raises(RecordError):
        StorageWrite(
            {
                "type": "document.change",
                "id": 1,
                "content": {"kind": "base", "version": 1, "value": value},
            }
        )
    assert counts(tmp_path) == before and logical(tmp_path) == graph
    result, _ = cold(tmp_path)
    assert result["status"] == "terminal"
    assert counts(tmp_path) == {**before, "providers": 2}

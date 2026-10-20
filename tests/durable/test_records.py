"""Complete source-derived variant shapes, presence, ownership and closed decoding."""

import copy
import dataclasses

import pytest

from pipy_harness.native.durable.codec import JsonValueError
from pipy_harness.native.durable.records import (
    OMITTED,
    ConversationRecord,
    DocumentCreate,
    DocumentRecord,
    EntryRecord,
    RecordError,
    SubmissionRecord,
    TaskRecord,
    decode_record,
    encode_record,
    safe_integer,
)


def task(state, **extra):
    return {
        "id": 2,
        "conversationId": 1,
        "kind": "\ud800",
        "version": 1,
        "input": {"payload": [None]},
        "background": False,
        "abortRequested": False,
        "state": state,
        **extra,
    }


OUTCOMES = [
    {"status": "completed", "result": None},
    {"status": "failed", "error": {"message": "", "detail": None}, "result": 1},
    {"status": "aborted", "reason": "", "result": None},
    {"status": "orphaned", "reason": "unavailable"},
    {"status": "faulted", "error": {"message": "contract"}},
]


@pytest.mark.parametrize("outcome", OUTCOMES)
@pytest.mark.parametrize("status", ["completing", "terminal"])
def test_all_terminal_outcomes_preserve_fields(status, outcome):
    value = task(
        {"status": status, "outcome": outcome},
        startedAt=1.25,
        endedAt=2.5,
        restarts=0,
        trace={"traceparent": "x"},
        links=[{}],
        owner=3,
        abandonOnRestart=True,
        abortReason="restart",
    )
    assert decode_record(encode_record(TaskRecord(value))).to_tree() == value


@pytest.mark.parametrize(
    "state",
    [
        {"status": "pending", "checkpoint": None},
        {"status": "running", "checkpoint": {"count": 1}},
        {"status": "waiting", "checkpoint": 1, "on": [3], "policy": "allSettled"},
        {"status": "waiting", "checkpoint": 1, "on": [], "policy": "failFast"},
        {"status": "sleeping", "checkpoint": {}, "until": 1.25},
    ],
)
def test_live_states_and_memos(state):
    value = task(state, memos={"__proto__": None})
    assert decode_record(encode_record(TaskRecord(value))).to_tree() == value


@pytest.mark.parametrize(
    "kind,status,extra",
    [
        ("input", "queued", {}),
        ("write", "queued", {}),
        ("input", "placed", {"entry": 3}),
        ("input", "done", {"entry": 3, "answer": 4}),
        ("write", "done", {"entry": 3}),
        ("input", "unanswered", {"entry": 3, "reason": "", "detail": None}),
        ("write", "unanswered", {"reason": "x", "detail": {"x": [1]}}),
    ],
)
def test_all_submission_variants(kind, status, extra):
    value = {
        "id": 4,
        "conversationId": 1,
        "type": kind,
        "status": status,
        "requestId": "\ud800",
        "trace": {},
        **extra,
    }
    assert decode_record(encode_record(SubmissionRecord(value))).to_tree() == value


@pytest.mark.parametrize(
    "scope,extra",
    [
        ({"kind": "session"}, {}),
        ({"kind": "task", "taskId": 2}, {}),
        (
            {"kind": "conversation", "conversationId": 1},
            {"history": "latest", "fork": "current"},
        ),
        (
            {"kind": "conversation", "conversationId": 1},
            {"history": "rewindable", "fork": "asOf"},
        ),
    ],
)
def test_document_identity_creation_and_persisted_slots(scope, extra):
    value = {"id": 7, "kind": "\ud800", "key": "\ud801", "scope": scope, **extra}
    assert DocumentCreate(value).to_tree() == value
    record = DocumentRecord({**value, "createdAt": 3, "retiredAt": 4})
    assert decode_record(encode_record(record)).to_tree() == record.to_tree()


def test_entry_and_conversation_full_fields_detach_and_omit_only_selected_fields():
    value = {
        "id": 2,
        "conversationId": 1,
        "kind": "",
        "data": None,
        "head": 2,
        "model": [{"role": "assistant", "uninterpreted": {"usage": [1]}}],
        "edits": [
            {"target": 3, "action": "omit"},
            {"target": 4, "action": "replace", "messages": []},
        ],
        "byTaskId": 5,
    }
    record = EntryRecord(value)
    value["model"][0]["uninterpreted"]["usage"].clear()
    snapshot = record.to_tree()
    snapshot["model"].clear()
    assert record.to_tree()["model"][0]["uninterpreted"]["usage"] == [1]
    assert EntryRecord(
        {"id": 2, "conversationId": 1, "kind": "", "data": OMITTED}
    ).to_tree() == {"id": 2, "conversationId": 1, "kind": ""}
    assert "data" in record.to_tree() and record.to_tree()["data"] is None
    conversation = {
        "id": 1,
        "parent": {"conversationId": 6, "at": 9},
        "owner": {"conversationId": 3, "taskId": 5},
    }
    assert ConversationRecord(conversation).to_tree() == conversation
    assert "uninterpreted" not in repr(record)
    with pytest.raises(dataclasses.FrozenInstanceError):
        record._value = None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v.update(id=True),
        lambda v: v.update(id=2**53),
        lambda v: v.update(background=1),
        lambda v: v.update(owner=None),
        lambda v: v.update(trace={"x": 1}),
        lambda v: v.update(links=[None]),
        lambda v: v.update(abandonOnRestart=False),
        lambda v: v.update(restarts=True),
        lambda v: v["state"].update(outcome=None),
        lambda v: v["state"].update(status="unknown"),
        lambda v: v.update(extra=OMITTED),
        lambda v: v.update(input=[OMITTED]),
        lambda v: v.update(id=OMITTED),
    ],
)
def test_malformed_task_fields_reject_without_mutating_input(mutate):
    value = task({"status": "pending", "checkpoint": {}})
    mutate(value)
    before = copy.deepcopy(value)
    with pytest.raises(JsonValueError):
        TaskRecord(value)
    # Sentinel identity is deliberately not inferred from deepcopy equality.
    assert value.keys() == before.keys()


@pytest.mark.parametrize(
    "state",
    [
        {"status": "terminal", "outcome": None},
        {"status": "terminal", "outcome": {"status": "completed"}},
        {
            "status": "terminal",
            "outcome": {"status": "orphaned", "reason": "x", "result": None},
        },
        {"status": "sleeping", "checkpoint": {}, "until": "later"},
        {"status": "waiting", "checkpoint": {}, "on": [True], "policy": "failFast"},
    ],
)
def test_invalid_discriminated_shapes_fail(state):
    with pytest.raises(RecordError):
        TaskRecord(task(state))


@pytest.mark.parametrize("version", [True, 1.0, 2, None])
def test_record_version_is_exact_and_newer_refused(version):
    with pytest.raises(RecordError):
        decode_record(
            {"schema_version": version, "kind": "conversation", "value": {"id": 1}}
        )


@pytest.mark.parametrize("value", [True, 0, -1, 1.0, 2**53])
def test_positive_safe_id_domain_is_separate_from_payload_numbers(value):
    with pytest.raises(RecordError):
        safe_integer(value)


@pytest.mark.parametrize(
    "field,value", [("data", OMITTED), ("data", None), ("extra", None)]
)
def test_record_rejects_nonplain_keys_before_omission_or_copy(field, value):
    calls = []

    class Key(str):
        def __hash__(self):
            calls.append("hash")
            return super().__hash__()

        def __eq__(self, other):
            calls.append("eq")
            return super().__eq__(other)

        def __repr__(self):
            calls.append("repr")
            return super().__repr__()

    tree = {"id": 2, "conversationId": 1, "kind": "", Key(field): value}
    calls.clear()  # Caller-side dict construction is outside the public boundary.
    with pytest.raises(JsonValueError, match="exact string keys"):
        EntryRecord(tree)
    assert calls == []


def test_record_decode_rejects_nonplain_envelope_keys_before_shape_membership():
    calls = []

    class Key(str):
        def __hash__(self):
            calls.append("hash")
            return super().__hash__()

        def __eq__(self, other):
            calls.append("eq")
            return super().__eq__(other)

    tree = {"schema_version": 1, "kind": "conversation", Key("value"): {"id": 1}}
    calls.clear()
    with pytest.raises(RecordError, match="exact string keys"):
        decode_record(tree)
    assert calls == []


def test_versioned_record_decoder_rejects_constructor_only_omission():
    value = {"id": 2, "conversationId": 1, "kind": "", "data": OMITTED}
    assert EntryRecord(value).to_tree() == {"id": 2, "conversationId": 1, "kind": ""}
    with pytest.raises(JsonValueError, match="plain containers and scalar"):
        decode_record({"schema_version": 1, "kind": "entry", "value": value})


@pytest.mark.parametrize("left,right", [(-0.0, 0.0), (1, 1.0)])
def test_record_equality_and_hash_retain_payload_representation(left, right):
    base = {"id": 2, "conversationId": 1, "kind": ""}
    a = EntryRecord({**base, "data": left})
    same = decode_record(encode_record(a))
    b = EntryRecord({**base, "data": right})
    assert a == same and hash(a) == hash(same)
    assert a != b and len({a, same, b}) == 2

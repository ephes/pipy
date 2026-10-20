"""Complete owned record trees, with explicit presence and version boundaries.

Source-derived shapes: Pi durable 1.3.0 types.ts:394–753 (MIT, copyright
2025 Mario Zechner; full notice in tests/fixtures/durable/UPSTREAM-LICENSE.txt).
Model-message payload interpretation belongs to DE06; every JSON field is kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, NewType, cast

from .codec import JsonValueError, OwnedJson

ConversationId = NewType("ConversationId", int)
EntryId = NewType("EntryId", int)
TaskId = NewType("TaskId", int)
SubmissionId = NewType("SubmissionId", int)
DocumentId = NewType("DocumentId", int)
Seq = NewType("Seq", int)
SAFE_INTEGER = 2**53 - 1


class RecordError(JsonValueError):
    """Malformed native record tree (diagnostics contain no payload)."""


class _Omitted:
    __slots__ = ()

    def __repr__(self) -> str:
        return "OMITTED"


OMITTED = _Omitted()


def safe_integer(value: object, *, positive: bool = True) -> int:
    minimum = 1 if positive else -SAFE_INTEGER
    if type(value) is not int or not minimum <= value <= SAFE_INTEGER:
        raise RecordError("Expected exact safe integer in the selected role domain")
    return value


def shape(
    value: object, required: set[str], optional: set[str] | None = None
) -> dict[str, object]:
    if type(value) is not dict:
        raise RecordError("Expected plain record object")
    if any(type(key) is not str for key in value):
        raise RecordError("Record object requires exact string keys")
    result = cast(dict[str, object], value)
    if not required <= result.keys() or not result.keys() <= required | (
        optional or set()
    ):
        raise RecordError("Missing or unknown record fields")
    return result


def text(value: object) -> None:
    if type(value) is not str:
        raise RecordError("Expected string field")


def flag(value: object) -> None:
    if type(value) is not bool:
        raise RecordError("Expected boolean field")


def choice(value: object, allowed: set[str]) -> str:
    if type(value) is not str or value not in allowed:
        raise RecordError("Unknown discriminant")
    return value


def array(value: object) -> list[object]:
    if type(value) is not list:
        raise RecordError("Expected plain array field")
    return cast(list[object], value)


def carrier(value: object) -> None:
    if type(value) is not dict:
        raise RecordError("Expected telemetry carrier")
    for key, item in value.items():
        text(key)
        text(item)


def optional_checks(
    value: dict[str, object], fields: set[str], validator: object
) -> None:
    # Internal validator table only; never a user-provided callback.
    from collections.abc import Callable

    check = cast(Callable[[object], object], validator)
    for key in fields & value.keys():
        check(value[key])


def conversation(value: dict[str, object]) -> None:
    shape(value, {"id"}, {"parent", "owner"})
    safe_integer(value["id"])
    for key, fields in (
        ("parent", {"conversationId", "at"}),
        ("owner", {"conversationId", "taskId"}),
    ):
        if key in value:
            item = shape(value[key], fields)
            for number in item.values():
                safe_integer(number)


def edit(value: object) -> None:
    item = shape(value, {"target", "action"}, {"messages"})
    safe_integer(item["target"])
    action = choice(item["action"], {"omit", "replace"})
    shape(item, {"target", "action"} | ({"messages"} if action == "replace" else set()))
    if action == "replace":
        messages(item["messages"])


def messages(value: object) -> None:
    for message in array(value):
        if type(message) is not dict:
            raise RecordError("Expected JSON model-message object")


def entry(value: dict[str, object]) -> None:
    shape(
        value,
        {"id", "conversationId", "kind"},
        {"model", "data", "head", "edits", "byTaskId"},
    )
    optional_checks(value, {"id", "conversationId", "head", "byTaskId"}, safe_integer)
    text(value["kind"])
    if "model" in value:
        messages(value["model"])
    for item in array(value.get("edits", [])):
        edit(item)


def outcome(value: object) -> None:
    item = shape(value, {"status"}, {"result", "error", "reason"})
    status = choice(
        item["status"], {"completed", "failed", "aborted", "orphaned", "faulted"}
    )
    required, optional = {
        "completed": ({"result"}, set()),
        "failed": ({"error"}, {"result"}),
        "aborted": (set(), {"result", "reason"}),
        "orphaned": ({"reason"}, set()),
        "faulted": ({"error"}, set()),
    }[status]
    shape(item, {"status"} | required, optional)
    if "reason" in item:
        text(item["reason"])
    if "error" in item:
        error = shape(item["error"], {"message"}, {"detail"})
        text(error["message"])


def task_state(value: object) -> str:
    item = shape(value, {"status"}, {"checkpoint", "outcome", "on", "policy", "until"})
    status = choice(
        item["status"],
        {"pending", "running", "waiting", "sleeping", "completing", "terminal"},
    )
    extras = {"waiting": {"on", "policy"}, "sleeping": {"until"}}.get(status, set())
    shape(
        item,
        {"status", "outcome" if status in {"completing", "terminal"} else "checkpoint"}
        | extras,
    )
    if status in {"completing", "terminal"}:
        outcome(item["outcome"])
    numeric_fields(item)
    if status == "waiting":
        for number in array(item["on"]):
            safe_integer(number)
        choice(item["policy"], {"failFast", "allSettled"})
    return status


def task(value: dict[str, object]) -> None:
    required = {
        "id",
        "conversationId",
        "kind",
        "version",
        "input",
        "background",
        "abortRequested",
        "state",
    }
    optional = {
        "trace",
        "links",
        "owner",
        "abortReason",
        "abandonOnRestart",
        "startedAt",
        "endedAt",
        "restarts",
        "memos",
    }
    shape(value, required, optional)
    optional_checks(value, {"id", "conversationId", "owner"}, safe_integer)
    optional_checks(value, {"kind"}, text)
    optional_checks(value, {"background", "abortRequested"}, flag)
    numeric_fields(value)
    task_metadata(value)
    status = task_state(value["state"])
    if "memos" in value:
        if status in {"completing", "terminal"} or type(value["memos"]) is not dict:
            raise RecordError("Memos require a live task and plain object")


def numeric_fields(value: dict[str, object]) -> None:
    for key in {"version", "startedAt", "endedAt", "restarts", "until"} & value.keys():
        if type(value[key]) not in {int, float}:
            raise RecordError("Expected finite numeric lifecycle field")


def task_metadata(value: dict[str, object]) -> None:
    if "trace" in value:
        carrier(value["trace"])
    for link in array(value.get("links", [])):
        carrier(link)
    if "abortReason" in value:
        choice(value["abortReason"], {"restart"})
    if "abandonOnRestart" in value and value["abandonOnRestart"] is not True:
        raise RecordError("abandonOnRestart, when present, must be true")


def submission(value: dict[str, object]) -> None:
    common = {"id", "conversationId", "type", "status"}
    shape(value, common, {"trace", "requestId", "entry", "answer", "reason", "detail"})
    kind = choice(value["type"], {"input", "write"})
    status = choice(value["status"], {"queued", "placed", "done", "unanswered"})
    variants = {
        ("input", "queued"): (set(), set()),
        ("write", "queued"): (set(), set()),
        ("input", "placed"): ({"entry"}, set()),
        ("input", "done"): ({"entry", "answer"}, set()),
        ("write", "done"): ({"entry"}, set()),
        ("input", "unanswered"): ({"reason"}, {"entry", "detail"}),
        ("write", "unanswered"): ({"reason"}, {"detail"}),
    }
    if (kind, status) not in variants:
        raise RecordError("Invalid submission lifecycle variant")
    required, optional = variants[(kind, status)]
    shape(value, common | required, optional | {"trace", "requestId"})
    optional_checks(value, {"id", "conversationId", "entry", "answer"}, safe_integer)
    optional_checks(value, {"reason", "requestId"}, text)
    if "trace" in value:
        carrier(value["trace"])


def scope(value: object) -> None:
    item = shape(value, {"kind"}, {"conversationId", "taskId"})
    kind = choice(item["kind"], {"session", "conversation", "task"})
    fields = {"session": set(), "conversation": {"conversationId"}, "task": {"taskId"}}[
        kind
    ]
    shape(item, {"kind"} | fields)
    optional_checks(item, fields, safe_integer)


def document(value: dict[str, object], *, creating: bool = False) -> None:
    required = {"id", "kind", "scope"} | (set() if creating else {"createdAt"})
    optional = {"key", "history", "fork"} | (set() if creating else {"retiredAt"})
    shape(value, required, optional)
    optional_checks(value, {"id", "createdAt", "retiredAt"}, safe_integer)
    optional_checks(value, {"kind", "key"}, text)
    scope(value["scope"])
    point = cast(dict[str, object], value["scope"])
    if point["kind"] == "conversation":
        history = choice(value.get("history"), {"latest", "rewindable"})
        choice(
            value.get("fork"),
            {"current", "initial"} | ({"asOf"} if history == "rewindable" else set()),
        )
    elif "history" in value or "fork" in value:
        raise RecordError("Only conversation documents have history/fork policies")


@dataclass(frozen=True, slots=True, init=False, repr=False)
class Record:
    """Validated variant; access/encoding returns detached values, never aliases."""

    kind: ClassVar[str] = "abstract"
    _value: OwnedJson = field(repr=False)

    def __init__(self, value: object) -> None:
        if type(value) is not dict:
            raise RecordError("Expected plain record tree")
        if any(type(key) is not str for key in value):
            raise RecordError("Record tree requires exact string keys")
        # Omission is selected only for explicitly optional record-object fields.
        optional = {
            "conversation": {"parent", "owner"},
            "entry": {"model", "data", "head", "edits", "byTaskId"},
            "task": {
                "trace",
                "links",
                "owner",
                "abortReason",
                "abandonOnRestart",
                "startedAt",
                "endedAt",
                "restarts",
                "memos",
            },
            "submission": {"trace", "requestId", "entry", "answer", "reason", "detail"},
            "document": {"key", "history", "fork", "retiredAt"},
            "document.create": {"key", "history", "fork"},
        }.get(self.kind, set())
        cleaned = {
            key: item
            for key, item in value.items()
            if not (item is OMITTED and key in optional)
        }
        owned = OwnedJson(cleaned)
        tree = cast(dict[str, object], owned.thaw())
        _validate_record(self.kind, tree)
        object.__setattr__(self, "_value", owned)

    @property
    def id(self) -> int:
        return cast(int, self.to_tree()["id"])

    def to_tree(self) -> dict[str, object]:
        return cast(dict[str, object], self._value.thaw())

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<private record>)"


class ConversationRecord(Record):
    __slots__ = ()
    kind = "conversation"


class EntryRecord(Record):
    __slots__ = ()
    kind = "entry"


class TaskRecord(Record):
    __slots__ = ()
    kind = "task"


class SubmissionRecord(Record):
    __slots__ = ()
    kind = "submission"


class DocumentRecord(Record):
    __slots__ = ()
    kind = "document"


class DocumentCreate(Record):
    __slots__ = ()
    kind = "document.create"


RECORD_TYPES: dict[str, type[Record]] = {
    cls.kind: cls
    for cls in (
        ConversationRecord,
        EntryRecord,
        TaskRecord,
        SubmissionRecord,
        DocumentRecord,
        DocumentCreate,
    )
}


def _validate_record(kind: str, value: dict[str, object]) -> None:
    validators = {
        "conversation": conversation,
        "entry": entry,
        "task": task,
        "submission": submission,
    }
    if kind in validators:
        validators[kind](value)
    elif kind in {"document", "document.create"}:
        document(value, creating=kind == "document.create")
    else:
        raise RecordError("Unknown record kind")


def encode_record(record: Record) -> dict[str, object]:
    if type(record) not in RECORD_TYPES.values():
        raise RecordError("Expected a concrete owned record")
    return {"schema_version": 1, "kind": record.kind, "value": record.to_tree()}


def decode_record(tree: object) -> Record:
    envelope = shape(tree, {"schema_version", "kind", "value"})
    if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
        raise RecordError("Unsupported record schema version")
    kind = choice(envelope["kind"], set(RECORD_TYPES))
    # Decoded trees must be JSON, before any constructor-only omission handling.
    owned = OwnedJson(envelope["value"])
    _validate_record(kind, cast(dict[str, object], owned.thaw()))
    record = object.__new__(RECORD_TYPES[kind])
    object.__setattr__(record, "_value", owned)
    return record

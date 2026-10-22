"""Synchronous Storage port and owned DTOs for detached backend ingress.

Memory and SQLite implement supplied current base/delta batches. The port
retains copy/history roles for later implementations.
Accepting a DTO is not a capability claim: bounded backends must refuse entire
unsupported batches. Session admission/cancellation belongs to DE02b.
Source: Pi types.ts:753–910,1179–1306 (MIT, copyright2025 Mario Zechner).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Generic, Literal, Protocol, TypeAlias, TypeVar, cast

from .codec import OwnedJson
from .records import (
    RECORD_TYPES,
    ConversationId,
    ConversationRecord,
    DocumentCreate,
    DocumentId,
    DocumentRecord,
    EntryId,
    EntryRecord,
    Record,
    RecordError,
    Seq,
    SubmissionId,
    SubmissionRecord,
    TaskId,
    TaskRecord,
    array,
    choice,
    flag,
    numeric_fields,
    optional_checks,
    safe_integer,
    scope,
    shape,
    text,
)

ScanOrder: TypeAlias = Literal["ascending", "descending"]
DocumentPoint: TypeAlias = Seq | Literal["current"]


class StorageRequestError(ValueError):
    """Invalid caller request; distinct from a fatal backend failure."""


class StorageClosedError(RuntimeError):
    """The backend has sealed resource ownership."""


class UnsupportedStorageOperation(StorageRequestError):
    """A declared later capability has not been implemented by this backend."""


def point(value: object) -> None:
    if value != "current":
        safe_integer(value)


@dataclass(frozen=True, slots=True)
class Cursor:
    after: int
    order: ScanOrder | None = None

    def __post_init__(self) -> None:
        safe_integer(self.after, positive=False)
        if self.order is not None:
            choice(self.order, {"ascending", "descending"})


T = TypeVar("T", bound=Record, covariant=True)


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next: Cursor | None = None

    def __post_init__(self) -> None:
        if type(self.items) is not tuple:
            raise StorageRequestError("Page items must be an immutable tuple")
        if any(type(item) not in RECORD_TYPES.values() for item in self.items):
            raise StorageRequestError("Page requires concrete owned records")
        if self.next is not None and type(self.next) is not Cursor:
            raise StorageRequestError("Expected owned continuation cursor")


@dataclass(frozen=True, slots=True, init=False, repr=False)
class Query:
    kind: ClassVar[str] = "abstract"
    _value: OwnedJson = field(repr=False)

    def __init__(self, value: object) -> None:
        owned = OwnedJson(value)
        query = cast(dict[str, object], owned.thaw())
        _query(self.kind, query)
        object.__setattr__(self, "_value", owned)

    def to_tree(self) -> dict[str, object]:
        return cast(dict[str, object], self._value.thaw())

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<private query>)"


class ConversationQuery(Query):
    __slots__ = ()
    kind = "conversation"


class EntryQuery(Query):
    __slots__ = ()
    kind = "entry"


class TaskQuery(Query):
    __slots__ = ()
    kind = "task"


class SubmissionQuery(Query):
    __slots__ = ()
    kind = "submission"


class DocumentQuery(Query):
    __slots__ = ()
    kind = "document"


class DocumentAddress(Query):
    __slots__ = ()
    kind = "address"


def _query(kind: str, value: dict[str, object]) -> None:
    required, optional = {
        "conversation": (set(), {"ownerConversationId", "ownerTaskId", "order"}),
        "entry": ({"conversationId"}, {"minEntryId", "maxEntryId", "order"}),
        "task": (
            set(),
            {
                "conversationId",
                "kind",
                "status",
                "abortRequested",
                "background",
                "order",
            },
        ),
        "submission": (set(), {"conversationId", "status", "order"}),
        "document": ({"scope", "at"}, {"kind"}),
        "address": ({"scope", "kind"}, {"key"}),
    }.get(kind, ({"invalid"}, set()))
    shape(value, required, optional)
    optional_checks(
        value,
        {
            "conversationId",
            "ownerConversationId",
            "ownerTaskId",
            "minEntryId",
            "maxEntryId",
        },
        safe_integer,
    )
    optional_checks(value, {"kind", "key"}, text)
    optional_checks(value, {"background", "abortRequested"}, flag)
    _query_discriminants(kind, value)


def _query_discriminants(kind: str, value: dict[str, object]) -> None:
    if "order" in value:
        choice(value["order"], {"ascending", "descending"})
    if "status" in value:
        allowed = (
            {"queued", "placed", "done", "unanswered"}
            if kind == "submission"
            else {"pending", "running", "waiting", "sleeping", "completing", "terminal"}
        )
        choice(value["status"], allowed)
    if "scope" in value:
        scope(value["scope"])
    if "at" in value:
        point(value["at"])


def _content(value: object, *, base_only: bool = False) -> None:
    item = shape(value, {"kind", "version"}, {"value", "ops"})
    kind = choice(item["kind"], {"base"} if base_only else {"base", "delta"})
    shape(item, {"kind", "version", "value" if kind == "base" else "ops"})
    numeric_fields(item)
    if kind == "base" and type(item["value"]) is not dict:
        raise RecordError("Document base requires an object")
    if kind == "delta":
        array(item["ops"])


@dataclass(frozen=True, slots=True, init=False, repr=False)
class StorageWrite:
    _value: OwnedJson = field(repr=False)

    def __init__(self, value: object) -> None:
        owned = OwnedJson(value)
        _write(cast(dict[str, object], owned.thaw()))
        object.__setattr__(self, "_value", owned)

    def to_tree(self) -> dict[str, object]:
        return cast(dict[str, object], self._value.thaw())

    def __repr__(self) -> str:
        return "StorageWrite(<private write>)"


def _write(value: dict[str, object]) -> None:
    item = shape(value, {"type"}, {"value", "record", "content", "source", "id"})
    kind = choice(
        item["type"],
        {
            "conversation",
            "entry",
            "task",
            "submission",
            "document.create",
            "document.change",
            "document.retire",
            "document.copy",
        },
    )
    if kind in {"conversation", "entry", "task", "submission"}:
        shape(item, {"type", "value"})
        RECORD_TYPES[kind](item["value"])
    else:
        _document_write(kind, item)


def _document_write(kind: str, item: dict[str, object]) -> None:
    fields = {
        "document.create": {"record", "content"},
        "document.copy": {"record", "source"},
        "document.change": {"id", "content"},
        "document.retire": {"id"},
    }[kind]
    shape(item, {"type"} | fields)
    if "record" in fields:
        DocumentCreate(item["record"])
    if "id" in fields:
        safe_integer(item["id"])
    if "content" in fields:
        _content(item["content"], base_only=kind == "document.create")
    if "source" in fields:
        source = shape(item["source"], {"id", "at"})
        safe_integer(source["id"])
        point(source["at"])


@dataclass(frozen=True, slots=True)
class EntryLookup:
    entry: EntryRecord
    commit_seq: Seq

    def __post_init__(self) -> None:
        if type(self.entry) is not EntryRecord:
            raise RecordError("Expected owned entry")
        safe_integer(self.commit_seq)


@dataclass(frozen=True, slots=True, init=False, repr=False)
class StoredDocument:
    _value: OwnedJson = field(repr=False)

    def __init__(self, value: object) -> None:
        owned = OwnedJson(value)
        item = shape(owned.thaw(), {"record", "version", "value", "deltasSinceBase"})
        DocumentRecord(item["record"])
        numeric_fields(item)
        count = item["deltasSinceBase"]
        if type(count) is not int or count < 0 or type(item["value"]) is not dict:
            raise RecordError("Invalid materialized document shape")
        object.__setattr__(self, "_value", owned)

    def to_tree(self) -> dict[str, object]:
        return cast(dict[str, object], self._value.thaw())


class Storage(Protocol):
    """Shared synchronous port; each backend documents its supported operations."""

    def commit(self, writes: tuple[StorageWrite, ...]) -> Seq: ...
    def mint_id(self) -> int: ...
    def conversation(self, id: ConversationId) -> ConversationRecord | None: ...
    def scan_conversations(
        self, query: ConversationQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[ConversationRecord]: ...
    def entry(
        self, id: EntryId, *, conversation_id: ConversationId | None = None
    ) -> EntryLookup | None: ...
    def find_latest_head_marker(
        self,
        conversation_id: ConversationId,
        at_or_before_entry_id: EntryId | None = None,
    ) -> EntryRecord | None: ...
    def scan_entries(
        self, query: EntryQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[EntryRecord]: ...
    def task(self, id: TaskId) -> TaskRecord | None: ...
    def scan_tasks(
        self, query: TaskQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[TaskRecord]: ...
    def submission(self, id: SubmissionId) -> SubmissionRecord | None: ...
    def scan_submissions(
        self, query: SubmissionQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[SubmissionRecord]: ...
    def submission_by_request(
        self, conversation_id: ConversationId, request_id: str
    ) -> SubmissionRecord | None: ...
    def find_document(
        self, address: DocumentAddress, at: DocumentPoint = "current"
    ) -> DocumentRecord | None: ...
    def document(
        self, id: DocumentId, at: DocumentPoint = "current"
    ) -> StoredDocument | None: ...
    def scan_documents(
        self, query: DocumentQuery, limit: int, cursor: Cursor | None = None
    ) -> Page[DocumentRecord]: ...
    def close(self) -> None: ...

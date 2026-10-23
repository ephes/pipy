"""Private typed current-document Session transaction core; no commit entrypoint.

One sans-IO core stages one commit callback's work over the DE02b1a guard: table
reads before the first table write, eager conversation/entry staging, task and
submission candidates resolved at assembly, final-owner validation, and
typed current documents behind mutable, nested-revocable drafts, including
family identity and coupled terminal-task retirement. It
prepares, assembles, discards or adopts; it never commits. The first exposed
Session (line, ``commit``/``acommit``, async driver, publication, close) is
DE02b2b, which drives this same core.

Semantics adapted from Pi durable 1.3.0 ``packages/durable/src/session/transaction.ts``
(``Transaction``, ``planDocument``, ``publishes``, ``applySubmissionChange``),
``src/documents.ts`` (``checkRecordScope``, ``checkRecordVersion``,
``documentCreate``), ``src/session/session.ts`` (``#loadDocument``, host
``cached``/``install``/``evict``) and ``src/errors.ts`` (``ReadAfterWrite``) at
2ec55d808d455b1a64a2bf7888198207a096120a, MIT Copyright (c) 2025 Mario Zechner;
notice: tests/fixtures/durable/UPSTREAM-LICENSE.txt.

Python spelling, documented decisions:

* Every Tx operation is a synchronous prologue (open check, phase rule,
  argument ingress, memo lookup) that raises at call time, followed by one ``async``
  body coroutine. A body suspends only on ``_Io``: one guarded backend call,
  run at most once (a memoized committed task read shares one ``_Io``). A
  driver runs bodies; ``_SyncDriver`` runs them inline on the thread that
  created it. Handles a driver returns are what Pi's ``#track`` records, so a
  duplicate ``doc()`` returns the same handle and settlement can see pending
  work. The core never waits on another operation: retiring after a pending
  acquisition asks the driver for a follower handle, and draining pending
  operations is the driver's job (``settle_failure`` returns them,
  ``PendingTxOperations`` carries them).
* Every body re-checks that the transaction is open after each ``_Io`` before
  it runs an initializer, mints or stages, so a late acquisition never
  initializes, mints or stages (Pi X612–637).
* Every prologue follows Pi's order: open check (then thread affinity), the
  phase rule, then argument ingress. A write sets the table-write phase before
  its arguments are validated, so an invalid write still ends the read phase;
  a read applies ``ReadAfterWrite`` before its arguments. Ingress admits exact
  primitives only and raises ``TypeError`` for an ill-typed ID before any
  guarded backend call (a raw Memory ``RecordError`` would fail the owner).
* Immutable ``Doc``/``DocFamily`` tokens resolve session/conversation/task
  addresses, including absent versus empty family keys. First seeds are copied
  before suspension, after same-Tx memo cutoff. A staged terminal task rejects
  before memo lookup; committed owner checks apply only to absent documents.
  Terminal task candidates retire staged and unknown persisted members in pages
  of 256 before one coupled Storage admission. Loaded versions still reject
  migrations. Creation writes its actual final base. Loaded nonempty normalized
  operations adopt their exact persisted replay; checkpoint predicates choose
  base/reset or delta/increment only after every validation read. Empty
  operations retain the prior committed incarnation. Full histories, fork and
  migrations remain DE27.
* ``Draft``/``DraftList`` are guarded adapters over one delivered ``Tracker``
  owned by one ``_DraftChange``. Every method checks first that the change is open
  (``DraftRevoked`` otherwise), then the driver's thread affinity
  (``TxAffinityError``). Every placement validates and deep-copies its value.
  Prepare revokes every view, nested or escaped, before assembly; abort and
  adoption keep them revoked.
* The document cache holds immutable committed value/count pairs. Adoption
  swaps entries under the cache lock only after acknowledgement, so a reader
  outside the line sees the old or the new revision and never a candidate.
  Publications share the same owned value and prepared operations; retirement
  overrides them with no value/version and empty operations.
"""

from __future__ import annotations

import inspect
import threading
import time
from collections.abc import (
    Callable,
    Coroutine,
    Generator,
    Iterable,
    Iterator,
    MutableMapping,
    MutableSequence,
)
from dataclasses import dataclass
from functools import partial
from types import CoroutineType
from typing import (
    Any,
    Final,
    Generic,
    Literal,
    Protocol,
    TypeAlias,
    TypeVar,
    cast,
    final,
)

from .codec import JsonValueError, OwnedJson
from .documents import (
    CheckpointInfo,
    CheckpointWhen,
    Doc,
    DocFamily,
    Token,
    address_key,
)
from .documents import Key as _Key
from .documents import check_scope as _check_scope
from .documents import resolve as _resolve
from .draft_tracker import Tracker, TrackerRevoked
from .draft_tracker import _List as _TrackedList
from .draft_tracker import _Map as _TrackedMap
from .records import (
    OMITTED,
    SAFE_INTEGER,
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
)
from .session import GuardedStorage
from .storage import (
    ConversationQuery,
    Cursor,
    DocumentAddress,
    DocumentQuery,
    EntryQuery,
    Page,
    StorageWrite,
    TaskQuery,
)
from .tasks import Task
from .tasks import capture as capture_task
from .tasks import initialize as initialize_task

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)

Tree: TypeAlias = dict[str, Any]


# -- errors -----------------------------------------------------------------


class ReadAfterWrite(RuntimeError):
    """A table read after the first table write (Pi ``ReadAfterWrite``)."""

    def __init__(self, method: str) -> None:
        super().__init__(
            f"Tx.{method}() cannot read tables after the first table write"
        )
        self.method = method


class TransactionSettled(RuntimeError):
    """Tx use after its callback settled (Pi ``Error("Transaction has settled")``)."""

    def __init__(self) -> None:
        super().__init__("Transaction has settled")


class PendingTxOperations(RuntimeError):
    """The callback settled first; the driver drains ``pending`` before raising."""

    def __init__(self, pending: tuple[_Handle[object], ...]) -> None:
        super().__init__(
            "Session commit callback settled before its pending Tx operations"
        )
        self.pending = pending


class DraftRevoked(TypeError):
    """A draft was used after its change was prepared or aborted (Pi revoked Proxy)."""

    def __init__(self) -> None:
        super().__init__("Draft is revoked: its transaction has settled")


class TxAffinityError(RuntimeError):
    """A transaction or draft was used outside its owning thread (Python-only)."""

    def __init__(self) -> None:
        super().__init__("Transaction used outside its owning thread")


# -- suspension, handles and the sync driver --------------------------------

_NEW: Final = "new"
_RUNNING: Final = "running"
_SETTLED: Final = "settled"


@final
class _Io(Generic[T]):
    """The core's only suspension: one guarded backend call, run at most once."""

    __slots__ = ("_error", "_fn", "_state", "_value")

    def __init__(self, fn: Callable[[], T]) -> None:
        self._fn = fn
        self._state: str = _NEW
        self._value: T | None = None
        self._error: BaseException | None = None

    def run(self) -> T:
        """Run the call once; later runs return or raise the same outcome."""
        if self._state is _NEW:
            self._state = _RUNNING
            try:
                self._value = self._fn()
            except BaseException as error:  # noqa: BLE001 - memoized outcome
                self._error = error
            self._state = _SETTLED
        if self._state is _RUNNING:
            raise RuntimeError("Tx backend call re-entered while running")
        if self._error is not None:
            raise self._error
        return cast(T, self._value)

    def __await__(self) -> Generator[_Io[T], object, T]:
        value = yield self
        return cast(T, value)


class _Handle(Protocol[T_co]):
    """What a driver returns for one operation (Pi's tracked promise)."""

    def done(self) -> bool: ...
    def result(self) -> T_co: ...
    def add_done_callback(self, fn: Callable[[], object]) -> None: ...


class _Driver(Protocol):
    def check(self) -> None: ...
    def spawn(self, body: Coroutine[Any, Any, T]) -> _Handle[T]: ...
    def follow(self, handle: _Handle[object]) -> _Handle[None]: ...
    def ready(self, value: T) -> _Handle[T]: ...


@final
class _Done(Generic[T]):
    """A settled sync handle; ``result()`` returns or raises its outcome."""

    __slots__ = ("_error", "_value")

    def __init__(self, value: T | None, error: Exception | None = None) -> None:
        self._value = value
        self._error = error

    def done(self) -> bool:
        return True

    def result(self) -> T:
        if self._error is not None:
            raise self._error
        return cast(T, self._value)

    def add_done_callback(self, fn: Callable[[], object]) -> None:
        fn()


def _drive(body: Coroutine[Any, Any, T]) -> T:
    """Run one body inline, answering each ``_Io`` request on this thread."""
    send: object = None
    error: BaseException | None = None
    while True:
        try:
            request = body.throw(error) if error is not None else body.send(send)
        except StopIteration as stop:
            return cast(T, stop.value)
        send, error = None, None
        if type(request) is not _Io:
            body.close()
            raise RuntimeError("Tx core awaited something other than a backend call")
        try:
            send = request.run()
        except BaseException as caught:  # noqa: BLE001 - delivered into the body
            error = caught


@final
class _SyncDriver:
    """Inline driver bound to the thread that created it (sync ``commit``)."""

    __slots__ = ("_thread",)

    def __init__(self) -> None:
        self._thread = threading.get_ident()

    def check(self) -> None:
        if threading.get_ident() != self._thread:
            raise TxAffinityError()

    def spawn(self, body: Coroutine[Any, Any, T]) -> _Done[T]:
        # Exceptions settle the handle; process-control exceptions propagate.
        try:
            return _Done(_drive(body))
        except Exception as error:  # noqa: BLE001 - the operation's outcome
            return _Done(None, error)

    def follow(self, handle: _Handle[object]) -> _Done[None]:
        assert handle.done(), "sync handles are settled when returned"
        try:
            handle.result()
        except Exception as error:  # noqa: BLE001 - the followed outcome
            return _Done(None, error)
        return _Done(None)

    def ready(self, value: T) -> _Done[T]:
        return _Done(value)

    def run(self, body: Coroutine[Any, Any, T]) -> T:
        """Run a settlement coroutine inline (``settle_success``)."""
        self.check()
        return _drive(body)


# -- raw-base documents -------------------------------------------------------


def _id(value: object, what: str) -> int:
    if type(value) is not int or not 1 <= value <= SAFE_INTEGER:
        raise TypeError(f"Expected a {what} ID")
    return value


def _number(value: object) -> str:
    return (
        str(int(value)) if type(value) is float and value.is_integer() else str(value)
    )


@dataclass(frozen=True, slots=True)
class CommittedDocument:
    """One committed incarnation in the Session cache; shared and identity-stable."""

    record: DocumentRecord
    version: int | float
    value: OwnedJson
    deltas_since_base: int


@dataclass(frozen=True, slots=True)
class RawDocumentChange:
    """Publication of one adopted incarnation; ``value is None`` when retired."""

    record: DocumentRecord
    conversation_id: int | None
    version: int | float | None
    value: OwnedJson | None
    ops: OwnedJson


def _check_version(raw: Token, record: DocumentRecord, version: int | float) -> None:
    tree = _tree(record)
    name = f"Document {tree['id']} ({tree['kind']})"
    if version > raw.version:
        raise RuntimeError(
            f"{name} has newer version {_number(version)} than {raw.version}"
        )
    if version < raw.version:
        raise RuntimeError(f"{name} requires migration from version {_number(version)}")


@final
class _DocumentCache:
    """Session cache of committed incarnations by address; never a candidate."""

    __slots__ = ("_documents", "_lock")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._documents: dict[_Key, CommittedDocument] = {}

    def get(self, key: _Key) -> CommittedDocument | None:
        with self._lock:
            return self._documents.get(key)

    def peek(
        self,
        raw: Token,
        conversation_id: int | None = None,
        *,
        task_id: int | None = None,
        key: object = OMITTED,
    ) -> CommittedDocument | None:
        """The cached incarnation at ``raw``'s address, without loading."""
        return self.get(_resolve(raw, conversation_id, task_id, key)[0])

    def install(self, key: _Key, document: CommittedDocument) -> None:
        with self._lock:
            self._documents[key] = document

    def swap(self, key: _Key, old: CommittedDocument, new: CommittedDocument) -> None:
        """Pointer swap: replace ``old`` only while it is still the occupant."""
        with self._lock:
            if self._documents.get(key) is old:
                self._documents[key] = new

    def evict(self, key: _Key, record_id: int) -> None:
        with self._lock:
            cached = self._documents.get(key)
            if cached is not None and cached.record.id == record_id:
                del self._documents[key]

    def drop(self, key: _Key) -> None:
        with self._lock:
            self._documents.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._documents.clear()


async def _load_document(
    storage: GuardedStorage,
    cache: _DocumentCache,
    raw: Token,
    key: _Key,
    address: DocumentAddress,
) -> CommittedDocument | None:
    """Pi ``#loadDocument``: a cache hit of this version, else a cold load.

    Shared by Tx acquisition and the Session's on-line snapshot miss; both
    run while the line is held, so every cache write is a line job.
    """
    cached = cache.get(key)
    if cached is not None and cached.version == raw.version:
        return cached
    if cached is not None:
        cache.drop(key)
    record = await _Io(partial(storage.find_document, address, "current"))
    if record is None:
        return None
    stored = await _Io(partial(storage.document, DocumentId(record.id), "current"))
    if stored is None:
        tree = _tree(record)
        raise RuntimeError(
            f"Current document {tree['id']} ({tree['kind']}) cannot be read"
        )
    value = cast(Tree, stored.to_tree())
    loaded = CommittedDocument(
        DocumentRecord(value["record"]),
        value["version"],
        OwnedJson(value["value"]),
        value["deltasSinceBase"],
    )
    _check_scope(raw, loaded.record)
    _check_version(raw, loaded.record, loaded.version)
    cache.install(key, loaded)
    return loaded


# -- drafts ---------------------------------------------------------------------

_OPEN: Final = "open"
_PREPARED: Final = "prepared"
_ABORTED: Final = "aborted"
_ADOPTED: Final = "adopted"


@dataclass(frozen=True, slots=True)
class _Prepared:
    value: OwnedJson
    ops: OwnedJson


_EMPTY_OPS: Final = OwnedJson([])
_TrackedView: TypeAlias = _TrackedMap | _TrackedList


@final
class _DraftChange:
    """Guarded adapters over one Tracker; no second mutable document tree."""

    __slots__ = ("created", "check", "root", "state", "tracker", "views")

    def __init__(
        self, value: OwnedJson, created: bool, check: Callable[[], None]
    ) -> None:
        self.state: str = _OPEN
        self.created = created
        self.check = check
        self.views: dict[int, Draft | DraftList] = {}
        self.tracker = Tracker(value, self.guard)
        self.root = cast(Draft, self.view(self.tracker.root))

    def guard(self) -> None:
        if self.state is not _OPEN:
            raise DraftRevoked()
        self.check()

    def view(self, view: Any) -> Any:
        if type(view) not in (_TrackedMap, _TrackedList):
            return view
        cached = self.views.get(id(view))
        if cached is None:
            cached = (
                Draft(self, view)
                if type(view) is _TrackedMap
                else DraftList(self, view)
            )
            self.views[id(view)] = cached
        return cached

    def place(self, value: object) -> object:
        """Unwrap only our guarded views; Tracker owns every placed subtree."""
        self.guard()
        if _is_view(value):
            return cast(Draft | DraftList, value).snapshot().thaw()
        return value

    def placements(self, values: Iterable[object]) -> Iterator[object]:
        for value in values:
            yield self.place(value)

    def prepare(self) -> _Prepared:
        self.guard()
        try:
            # Creation keeps its actual final object, including scalar signs and
            # member order. Loaded changes use the exact persisted replay.
            created = self.root.snapshot() if self.created else None
            prepared = self.tracker.prepare()
            return _Prepared(
                created if created is not None else prepared.replayed_value,
                _EMPTY_OPS if created is not None else prepared.ops,
            )
        finally:
            self.state = _PREPARED
            self.views.clear()

    def discard(self) -> None:
        if self.state is _OPEN or self.state is _PREPARED:
            self.state = _ABORTED
            self.tracker.discard()
            self.views.clear()

    def settle(self, adopted: bool) -> None:
        self.state = _ADOPTED if adopted else _ABORTED


def _is_view(value: object) -> bool:
    # Exact types: a foreign ``__class__`` or subclass is never trusted as a view.
    return type(value) is Draft or type(value) is DraftList


def _key(key: object, *, write: bool = False) -> str:
    """Validate before unwrapping a placed view; no caller coercion runs."""
    if type(key) is not str:
        if write:
            raise TypeError("Draft object keys must be exact strings")
        raise KeyError("Draft object keys are exact strings")
    return key


def _index(index: object) -> int | slice:
    if type(index) is slice and all(
        part is None or type(part) is int
        for part in (index.start, index.stop, index.step)
    ):
        return index
    if type(index) is not int:
        raise TypeError("Draft list indices must be exact integers or slices")
    return index


class _View:
    __slots__ = ("_change", "_view")
    _change: _DraftChange
    _view: _TrackedView  # Delivered facade, never its mutable node.

    def __init__(self, change: _DraftChange, view: _TrackedView) -> None:
        if hasattr(self, "_change"):
            raise TypeError("Draft view is already initialized")
        object.__setattr__(self, "_change", change)
        object.__setattr__(self, "_view", view)

    def _use(self, fn: Callable[..., T], *args: Any) -> T:
        # The outer check keeps DraftRevoked before TxAffinityError and never
        # leaks Tracker's generic wrong-thread exception from normal access.
        self._change.guard()
        try:
            return fn(*args)
        except TrackerRevoked:
            raise DraftRevoked() from None

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Drafts expose items, not attributes")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("Drafts expose items, not attributes")

    def __copy__(self) -> object:
        raise TypeError("Drafts cannot be copied; use snapshot()")

    def __deepcopy__(self, memo: object) -> object:
        raise TypeError("Drafts cannot be copied; use snapshot()")

    def __reduce_ex__(self, protocol: object) -> Any:
        raise TypeError("Drafts cannot be pickled; use snapshot()")

    def __reduce__(self) -> Any:
        raise TypeError("Drafts cannot be pickled; use snapshot()")

    def __getstate__(self) -> Any:
        raise TypeError("Drafts expose no state; use snapshot()")

    def __repr__(self) -> str:
        state = "open" if self._change.state is _OPEN else "revoked"
        return f"{type(self).__name__}(<{state}>)"

    def snapshot(self) -> OwnedJson:
        """A detached owned copy of the current value."""
        return self._use(self._view.snapshot)

    def __eq__(self, other: object) -> bool:
        mine = self.snapshot().thaw()
        theirs = cast(_View, other).snapshot().thaw() if _is_view(other) else other
        return bool(mine == theirs)


def _guarded(view: _View, items: Iterator[Any]) -> Iterator[Any]:
    while True:
        try:
            item = view._use(next, items)
        except StopIteration:
            return
        yield view._change.view(item)


class Draft(_View, MutableMapping[str, Any]):
    """Mutable object adapter over the same Tracker as every nested view."""

    __slots__ = ()
    __hash__ = None  # type: ignore[assignment]
    _view: _TrackedMap

    def __getitem__(self, key: str) -> Any:
        return self._change.view(self._use(self._view.__getitem__, key))

    def __setitem__(self, key: str, value: object) -> None:
        self._change.guard()
        key = _key(key, write=True)
        self._use(self._view.__setitem__, key, self._change.place(value))

    def __delitem__(self, key: str) -> None:
        self._use(self._view.__delitem__, key)

    def __iter__(self) -> Iterator[str]:
        return _guarded(self, self._use(iter, self._view))

    def __len__(self) -> int:
        return self._use(len, self._view)

    def __contains__(self, key: object) -> bool:
        return self._use(self._view.__contains__, key)

    def pop(self, key: str, *default: object) -> Any:
        return self._use(self._view.pop, key, *default)

    def popitem(self) -> tuple[str, Any]:
        return self._use(self._view.popitem)

    def clear(self) -> None:
        self._use(self._view.clear)

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._change.guard()
        super().update(*args, **kwargs)
        self._change.guard()

    def setdefault(self, key: str, default: object = None) -> Any:
        self._change.guard()
        if _key(key) not in self:
            self[key] = default
        return self[key]


class DraftList(_View, MutableSequence[Any]):
    """Mutable array adapter; ABC methods dispatch through these same routes."""

    __slots__ = ()
    __hash__ = None  # type: ignore[assignment]
    _view: _TrackedList

    def __getitem__(self, index: Any) -> Any:
        return self._change.view(self._use(self._view.__getitem__, index))

    def __setitem__(self, index: Any, value: Any) -> None:
        self._change.guard()
        position = _index(index)
        placed = (
            self._change.placements(value)
            if type(position) is slice
            else self._change.place(value)
        )
        self._use(self._view.__setitem__, position, placed)

    def __delitem__(self, index: Any) -> None:
        self._use(self._view.__delitem__, index)

    def __len__(self) -> int:
        return self._use(len, self._view)

    def __contains__(self, value: object) -> bool:
        return self._use(self._view.__contains__, value)

    def __iter__(self) -> Iterator[Any]:
        return _guarded(self, self._use(iter, self._view))

    def index(self, value: object, start: int = 0, stop: int | None = None) -> int:
        return self._use(self._view.index, value, start, stop)

    def count(self, value: object) -> int:
        return self._use(self._view.count, value)

    def insert(self, index: int, value: object) -> None:
        self._change.guard()
        if type(index) is not int:
            raise TypeError("Draft list indices must be exact integers")
        self._use(self._view.insert, index, self._change.place(value))

    def extend(self, values: Iterable[Any]) -> None:
        self._change.guard()
        super().extend(values)
        self._change.guard()

    def pop(self, index: int = -1) -> Any:
        return self._use(self._view.pop, index)

    def reverse(self) -> None:
        self._use(self._view.reverse)

    def clear(self) -> None:
        self._use(self._view.clear)


# -- the transaction core -------------------------------------------------------


@dataclass(slots=True)
class _TaskEntry:
    committed: _Io[TaskRecord | None] | None = None
    write: tuple[Literal["create", "replace"], TaskRecord] | None = None


@dataclass(slots=True)
class _Created:
    record: DocumentCreate
    version: int


@dataclass(slots=True)
class _DocEntry:
    key: _Key
    address: DocumentAddress
    raw: Token
    retiring: bool
    seed: object = OMITTED
    handle: _Handle[Draft] | None = None
    target: CommittedDocument | _Created | DocumentRecord | None = None
    change: _DraftChange | None = None
    prepared: _Prepared | None = None
    checkpoint_when: CheckpointWhen | None = None


@dataclass(slots=True)
class _Plan:
    key: _Key
    record: DocumentCreate | DocumentRecord
    retiring: bool
    content: StorageWrite | None = None
    prepared: _Prepared | None = None
    change: _DraftChange | None = None
    version: int = 0
    loaded: CommittedDocument | None = None
    conversation_id: int | None = None
    checkpoint_when: CheckpointWhen | None = None
    checkpoint: bool = False


def _plan(document: _DocEntry) -> _Plan | None:
    """Creation bases and ordinary default-policy deltas; retirement follows."""
    target = document.target
    if target is None:
        return None
    if type(target) is DocumentRecord:
        return _Plan(document.key, target, document.retiring)
    prepared, change = document.prepared, document.change
    assert prepared is not None and change is not None
    version = document.raw.version
    if type(target) is _Created:
        content = {"version": version, "kind": "base", "value": prepared.value.thaw()}
        write = {"type": "document.create", "record": target.record.to_tree()}
        return _Plan(
            document.key,
            target.record,
            document.retiring,
            StorageWrite(write | {"content": content}),
            prepared,
            change,
            version,
        )
    loaded = cast(CommittedDocument, target)
    # Structural operations may restore equal values and must still persist.
    ops = prepared.ops.thaw()
    changed = (
        StorageWrite(
            {
                "type": "document.change",
                "id": loaded.record.id,
                "content": {"version": version, "kind": "delta", "ops": ops},
            }
        )
        if ops
        else None
    )
    return _Plan(
        document.key,
        loaded.record,
        document.retiring,
        changed,
        prepared,
        change,
        version,
        loaded,
        checkpoint_when=document.checkpoint_when,
    )


def _publishes(plan: _Plan) -> bool:
    return (
        plan.retiring
        or (plan.change is not None and plan.loaded is None)
        or plan.content is not None
    )


def _apply_submission(current: SubmissionRecord, change: Tree) -> SubmissionRecord:
    """Pi ``applySubmissionChange``: the same record when it is already settled."""
    tree = _tree(current)
    status = tree["status"]
    if status in {"done", "unanswered"}:
        return current
    if change["status"] == "placed":
        if status != "queued":
            raise RuntimeError(f"Submission {current.id} is not queued")
        placed = "placed" if tree["type"] == "input" else "done"
        return SubmissionRecord({**tree, "status": placed, "entry": change["entry"]})
    if change["status"] == "done" and status != "placed":
        raise RuntimeError(f"Submission {current.id} is not a placed input")
    return SubmissionRecord({**tree, **change})


def _ownership(value: object, kinds: set[str]) -> int | None:
    # Exact keys and kind before any hashing, so no caller hook runs.
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise TypeError("Expected an ownership object")
    kind = value.get("kind")
    if type(kind) is not str or kind not in kinds:
        raise TypeError("Expected an ownership object")
    if kind != "task":
        if set(value) != {"kind"}:
            raise TypeError("Unexpected ownership fields")
        return None
    if set(value) != {"kind", "taskId"}:
        raise TypeError("Task ownership requires exactly taskId")
    return _id(value["taskId"], "task")


def _tree(record: Record) -> Tree:
    return cast(Tree, record.to_tree())


def _plain_tree(value: object) -> Tree:
    """Detach a caller's record tree; ``OMITTED`` values are omitted as by ``Record``."""
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise RecordError("Expected plain record tree")
    kept = {key: item for key, item in value.items() if item is not OMITTED}
    return cast(Tree, OwnedJson(kept).thaw())


def _status(record: TaskRecord) -> str:
    return cast(str, _tree(record)["state"]["status"])


def _clock() -> float:
    return time.time() * 1000


@final
class _TxCore:
    """Private transaction core for one commit callback; never commits."""

    def __init__(
        self,
        storage: GuardedStorage,
        cache: _DocumentCache,
        driver: _Driver,
        *,
        now: Callable[[], float] = _clock,
        conversation_id: int | None = None,
        task_id: int | None = None,
        healthy: Callable[[], None] | None = None,
    ) -> None:
        if type(storage) is not GuardedStorage:
            raise TypeError("The Tx core reads and writes only through the guard")
        if type(cache) is not _DocumentCache:
            raise TypeError("Expected the Session document cache")
        self._storage = storage
        self._cache = cache
        self._driver = driver
        self._now = now
        self._healthy = healthy
        self._scope_conversation = (
            None if conversation_id is None else _id(conversation_id, "conversation")
        )
        self._scope_task = None if task_id is None else _id(task_id, "task")
        self._sealed = False
        self._assembled = False
        self._adopted = False
        self._aborted = False
        self._has_table_write = False
        self._pending: set[_Handle[object]] = set()
        # Conversations and entries stage eagerly, in staging order.
        self._writes: list[tuple[str, ConversationRecord | EntryRecord]] = []
        self._created_conversations: set[int] = set()
        self._tasks: dict[int, _TaskEntry] = {}
        self._submissions: dict[int, SubmissionRecord] = {}
        self._submission_changes: list[tuple[int, Tree]] = []
        self._documents: list[_DocEntry] = []
        self._latest: dict[_Key, _DocEntry] = {}
        self._plans: list[_Plan] = []
        self._table_count = 0

    # -- phase and lifecycle helpers ---------------------------------------

    def _enter(self) -> None:
        """Prologue: open first (Pi ``#assertOpen``), then thread affinity."""
        if self._sealed:
            raise TransactionSettled()
        self._driver.check()

    def _recheck(self) -> None:
        """After each ``_Io``: never initialize, mint or stage once settled."""
        if self._sealed:
            raise TransactionSettled()

    def _track(self, handle: _Handle[T]) -> _Handle[T]:
        if not handle.done():
            pending = self._pending
            pending.add(handle)
            handle.add_done_callback(lambda: pending.discard(handle))
        return handle

    def _reading(self, method: str) -> None:
        """Pi ``#read``: open, then the phase rule, before any argument handling."""
        self._enter()
        if self._has_table_write:
            raise ReadAfterWrite(method)

    def _writing(self) -> None:
        """Pi ``#write`` and the sync writers: open, then the write phase.

        The phase is set before any argument handling, so a write whose
        arguments fail validation has already ended the table read phase.
        """
        self._enter()
        self._has_table_write = True

    def _run(self, body: Coroutine[Any, Any, T]) -> _Handle[T]:
        return self._track(self._driver.spawn(body))

    async def _call(self, fn: Callable[[], T]) -> T:
        return await _Io(fn)

    # -- table reads (Pi X232–282) -----------------------------------------

    def conversation(self, id: int) -> _Handle[ConversationRecord | None]:
        self._reading("conversation")
        key = ConversationId(_id(id, "conversation"))
        return self._run(self._call(partial(self._storage.conversation, key)))

    def entry(self, id: int) -> _Handle[EntryRecord | None]:
        self._reading("entry")
        return self._run(self._entry(_id(id, "entry")))

    async def _entry(self, id: int) -> EntryRecord | None:
        found = await _Io(partial(self._storage.entry, EntryId(id)))
        return None if found is None else found.entry

    def task(self, id: int) -> _Handle[TaskRecord | None]:
        self._reading("task")
        return self._run(self._committed(_id(id, "task")))

    def submission(self, id: int) -> _Handle[SubmissionRecord | None]:
        self._reading("submission")
        key = SubmissionId(_id(id, "submission"))
        return self._run(self._call(partial(self._storage.submission, key)))

    def submission_by_request(
        self, conversation_id: int, request_id: str
    ) -> _Handle[SubmissionRecord | None]:
        self._reading("submission_by_request")
        key = ConversationId(_id(conversation_id, "conversation"))
        if type(request_id) is not str:
            raise TypeError("Expected an exact request ID string")
        lookup = partial(self._storage.submission_by_request, key, request_id)
        return self._run(self._call(lookup))

    def latest_head_marker(self, conversation_id: int) -> _Handle[EntryRecord | None]:
        self._reading("latest_head_marker")
        key = ConversationId(_id(conversation_id, "conversation"))
        lookup = partial(self._storage.find_latest_head_marker, key, None)
        return self._run(self._call(lookup))

    def scan_conversations(
        self, query: ConversationQuery, limit: int, cursor: Cursor | None = None
    ) -> _Handle[Page[ConversationRecord]]:
        self._reading("scan_conversations")
        scan = partial(self._storage.scan_conversations, query, limit, cursor)
        return self._run(self._call(scan))

    def scan_entries(
        self, query: EntryQuery, limit: int, cursor: Cursor | None = None
    ) -> _Handle[Page[EntryRecord]]:
        self._reading("scan_entries")
        scan = partial(self._storage.scan_entries, query, limit, cursor)
        return self._run(self._call(scan))

    def scan_tasks(
        self, query: TaskQuery, limit: int, cursor: Cursor | None = None
    ) -> _Handle[Page[TaskRecord]]:
        self._reading("scan_tasks")
        scan = partial(self._storage.scan_tasks, query, limit, cursor)
        return self._run(self._call(scan))

    # -- table writes (Pi X284–499) ----------------------------------------

    def create_conversation(self, ownership: object) -> _Handle[ConversationRecord]:
        self._writing()
        owner = _ownership(ownership, {"ownerless", "task"})
        return self._run(self._stage_conversation(owner))

    async def _stage_conversation(self, owner_task: int | None) -> ConversationRecord:
        id = await _Io(self._storage.mint_id)
        self._recheck()
        tree: Tree = {"id": id}
        if owner_task is not None:
            task = await self._current_task(owner_task)
            self._recheck()
            if task is None:
                raise RuntimeError(
                    f"Conversation owner task {owner_task} does not exist"
                )
            tree["owner"] = {
                "conversationId": _tree(task)["conversationId"],
                "taskId": owner_task,
            }
        record = ConversationRecord(tree)
        self._created_conversations.add(id)
        self._writes.append(("conversation", record))
        return record

    def append_entry(self, conversation_id: int, draft: object) -> _Handle[EntryRecord]:
        self._writing()
        key = _id(conversation_id, "conversation")
        if type(draft) is not dict or any(type(k) is not str for k in draft):
            raise TypeError("Expected an entry draft object")
        return self._run(self._append_entry(key, dict(draft)))

    async def _append_entry(self, conversation_id: int, draft: Tree) -> EntryRecord:
        await self._require_conversation(conversation_id)
        self._recheck()
        id = await _Io(self._storage.mint_id)
        self._recheck()
        head = draft.pop("head", OMITTED)
        tree = {
            **draft,
            "id": id,
            "conversationId": conversation_id,
            "head": id if type(head) is str and head == "self" else head,
            "byTaskId": OMITTED if self._scope_task is None else self._scope_task,
        }
        record = EntryRecord(tree)
        self._writes.append(("entry", record))
        return record

    def create_task(
        self,
        task: Task[Any, Any, Any],
        input: object,
        *,
        ownership: object,
        conversation_id: int | None = None,
        background: bool = False,
        abandon_on_restart: bool = False,
        trace: object = None,
    ) -> _Handle[int]:
        self._writing()
        owner = _ownership(ownership, {"conversation", "task"})
        conversation = (
            None if conversation_id is None else _id(conversation_id, "conversation")
        )
        if type(background) is not bool or type(abandon_on_restart) is not bool:
            raise TypeError("Task flags must be booleans")
        definition = capture_task(task)
        fields: Tree = {"kind": definition.name, "version": definition.version}
        if trace is not None:
            fields["trace"] = trace
        fields = _plain_tree(fields)
        owned_input = OwnedJson(input)
        body = self._create_task(
            definition,
            owned_input,
            fields,
            owner,
            conversation,
            background,
            abandon_on_restart,
        )
        return self._run(body)

    async def _create_task(
        self,
        definition: Task[Any, Any, Any],
        owned_input: OwnedJson,
        fields: Tree,
        owner_id: int | None,
        conversation_id: int | None,
        background: bool,
        abandon: bool,
    ) -> int:
        owner_conversation = None
        if owner_id is not None:
            # Validated again against the owner's final candidate at assembly.
            owner = await self._current_task(owner_id)
            self._recheck()
            if owner is None:
                raise RuntimeError(f"Task owner {owner_id} does not exist")
            if background:
                raise TypeError("A child task cannot be background")
            owner_conversation = cast(int, _tree(owner)["conversationId"])
            if conversation_id is not None and conversation_id != owner_conversation:
                raise RuntimeError(
                    f"A child task lives in its owner's conversation {owner_conversation}"
                )
        resolved = owner_conversation or conversation_id or self._scope_conversation
        if resolved is None:
            raise TypeError("Tx.create_task() requires conversation_id")
        await self._require_conversation(resolved)
        self._recheck()
        # The initializer may mutate its detached input, as in Pi. Validate both
        # final values before minting; no caller container survives suspension.
        initial = cast(Tree, initialize_task(definition, owned_input).thaw())
        self._recheck()
        if self._healthy is not None:
            self._healthy()
        id = await _Io(self._storage.mint_id)
        self._recheck()
        tree: Tree = {
            "id": id,
            "conversationId": resolved,
            **fields,
            "input": initial["input"],
        }
        if owner_id is not None:
            tree["owner"] = owner_id
        tree |= {"background": background, "abortRequested": False}
        if abandon:
            tree["abandonOnRestart"] = True
        tree["state"] = {"status": "pending", "checkpoint": initial["checkpoint"]}
        self._tasks[id] = _TaskEntry(write=("create", TaskRecord(tree)))
        return id

    def create_submission(self, create: object) -> _Handle[SubmissionRecord]:
        self._writing()
        if type(create) is not dict or any(type(k) is not str for k in create):
            raise TypeError("Expected a submission create object")
        conversation = _id(create.get("conversationId"), "conversation")
        return self._run(self._create_submission(conversation, dict(create)))

    async def _create_submission(
        self, conversation_id: int, create: Tree
    ) -> SubmissionRecord:
        await self._require_conversation(conversation_id)
        self._recheck()
        id = await _Io(self._storage.mint_id)
        self._recheck()
        record = SubmissionRecord({**create, "id": id})
        self._submissions[id] = record
        return record

    def settle_submission(self, id: int, settlement: object) -> None:
        """Synchronous; resolved at assembly, so it works after table writes."""
        self._writing()
        key = _id(id, "submission")
        if type(settlement) is not dict:
            raise TypeError("Expected a done or unanswered settlement")
        value = cast(Tree, OwnedJson(settlement).thaw())
        if value.get("status") not in {"done", "unanswered"}:
            raise TypeError("Expected a done or unanswered settlement")
        self._submission_changes.append((key, value))

    def place_submission(self, id: int, entry: int) -> None:
        """Synchronous; place a queued submission at ``entry`` during assembly."""
        self._writing()
        key = _id(id, "submission")
        at = _id(entry, "entry")
        self._submission_changes.append((key, {"status": "placed", "entry": at}))

    def set_task(self, record: object) -> None:
        """Synchronous complete replacement of one task record (Pi X484–499).

        Pi order: phase, task entry, candidate checks, time stamps, then the
        owned copy. A plain tree is detached first (exact JSON only, so no
        caller hook runs later) and its record shape is validated last.
        """
        self._writing()
        given = record if type(record) is TaskRecord else None
        tree = _tree(given) if given is not None else _plain_tree(record)
        id = _id(tree.get("id"), "task")
        task = self._task_entry(id)
        candidate = None if task.write is None else task.write[1]
        if candidate is not None:
            if _status(candidate) == "terminal":
                raise RuntimeError(f"Task {id} already has a terminal candidate")
            if _tree(candidate)["conversationId"] != tree.get("conversationId"):
                raise RuntimeError(f"Task {id} cannot change conversations")
        kind: Literal["create", "replace"] = (
            "create"
            if task.write is not None and task.write[0] == "create"
            else "replace"
        )
        stamped = self._stamp_times(tree, candidate)
        value = given if given is not None and stamped is tree else None
        task.write = (kind, value if value is not None else TaskRecord(stamped))

    def _stamp_times(self, value: Tree, candidate: TaskRecord | None) -> Tree:
        """``startedAt`` on the first ``running``, ``endedAt`` on ``terminal``."""
        state = value.get("state")
        status = state.get("status") if type(state) is dict else None
        prior = {} if candidate is None else _tree(candidate)
        started = prior.get("startedAt", value.get("startedAt"))
        if started is None and status == "running":
            started = self._now()
        ended = prior.get("endedAt", value.get("endedAt"))
        if ended is None and status == "terminal":
            ended = self._now()
        if started == value.get("startedAt") and ended == value.get("endedAt"):
            return value
        stamped = dict(value)
        if started is not None:
            stamped["startedAt"] = started
        if ended is not None:
            stamped["endedAt"] = ended
        return stamped

    def staged_tasks(self) -> tuple[TaskRecord, ...]:
        return tuple(t.write[1] for t in self._tasks.values() if t.write is not None)

    def staged_entries(self) -> tuple[EntryRecord, ...]:
        return tuple(
            cast(EntryRecord, record)
            for kind, record in self._writes
            if kind == "entry"
        )

    def staged_conversations(self) -> tuple[ConversationRecord, ...]:
        return tuple(
            cast(ConversationRecord, r)
            for kind, r in self._writes
            if kind == "conversation"
        )

    # -- documents (Pi X536–684) -------------------------------------------

    def doc(
        self,
        raw: Token,
        *,
        conversation_id: int | None = None,
        task_id: int | None = None,
        key: object = OMITTED,
        seed: object = OMITTED,
    ) -> _Handle[Draft]:
        """Acquire the draft at ``raw``'s address, memoized per address."""
        self._enter()
        identity, address = _resolve(raw, conversation_id, task_id, key)
        if raw.scope == "task":
            candidate = self._tasks.get(cast(int, task_id))
            if (
                candidate is not None
                and candidate.write is not None
                and _status(candidate.write[1]) == "terminal"
            ):
                raise RuntimeError(f"Task {task_id} is terminal")
        if type(raw) is Doc and seed is not OMITTED:
            raise TypeError("Singleton document does not accept a family seed")
        latest = self._latest.get(identity)
        if latest is not None and not latest.retiring and latest.handle is not None:
            return latest.handle
        copied_seed: object = OMITTED
        if type(raw) is DocFamily:
            if seed is OMITTED:
                raise TypeError("Document family acquisition requires a seed")
            copied_seed = OwnedJson(seed).thaw()
        entry = _DocEntry(
            identity,
            address,
            raw,
            retiring=False,
            seed=copied_seed,
            checkpoint_when=raw.checkpoint_when,
        )
        self._documents.append(entry)
        self._latest[identity] = entry
        # Capture retirement first, so an old acquisition and its replacement stay distinct.
        skip_load = latest is not None and latest.retiring
        entry.handle = self._track(self._driver.spawn(self._acquire(entry, skip_load)))
        return entry.handle

    def retire_doc(
        self,
        raw: Token,
        *,
        conversation_id: int | None = None,
        task_id: int | None = None,
        key: object = OMITTED,
    ) -> _Handle[None]:
        """Retire the current incarnation at ``raw``'s address at commit."""
        self._enter()
        identity, address = _resolve(raw, conversation_id, task_id, key)
        latest = self._latest.get(identity)
        if latest is not None and latest.retiring:
            return self._driver.ready(None)
        if latest is not None and latest.handle is not None:
            # Retiring an acquired draft persists its final content first.
            latest.retiring = True
            return self._track(self._driver.follow(latest.handle))
        entry = _DocEntry(identity, address, raw, retiring=True)
        self._documents.append(entry)
        self._latest[identity] = entry
        return self._track(self._driver.spawn(self._find_retirement(entry)))

    async def _acquire(self, entry: _DocEntry, skip_load: bool) -> Draft:
        raw = entry.raw
        loaded = None if skip_load else await self._load(raw, entry.key, entry.address)
        self._recheck()
        if loaded is not None:
            _check_scope(raw, loaded.record)
            _check_version(raw, loaded.record, loaded.version)
            entry.target = loaded
            entry.change = _DraftChange(loaded.value, False, self._driver.check)
            return entry.change.root
        scope = cast(Tree, entry.address.to_tree())["scope"]
        if scope["kind"] == "conversation":
            await self._require_conversation(scope["conversationId"])
        if scope["kind"] == "task":
            task = await self._current_task(scope["taskId"])
            if task is None:
                raise RuntimeError(f"Task {scope['taskId']} does not exist")
            if _status(task) == "terminal":
                raise RuntimeError(f"Task {scope['taskId']} is terminal")
        self._recheck()
        value = (
            raw.initial(entry.seed)
            if type(raw) is DocFamily
            else cast(Doc[Any], raw).initial()
        )
        initial = cast(Tree, OwnedJson(value).thaw())
        if type(initial) is not dict:
            raise JsonValueError("Document initial value must be a JSON object")
        id = await _Io(self._storage.mint_id)
        self._recheck()
        create: Tree = {
            "id": id,
            "kind": raw.kind,
            "scope": entry.address.to_tree()["scope"],
        }
        if "key" in entry.address.to_tree():
            create["key"] = entry.address.to_tree()["key"]
        if raw.scope == "conversation":
            create |= {"history": raw.history, "fork": raw.fork}
        entry.target = _Created(DocumentCreate(create), raw.version)
        entry.change = _DraftChange(OwnedJson(initial), True, self._driver.check)
        return entry.change.root

    async def _load(
        self, raw: Token, key: _Key, address: DocumentAddress
    ) -> CommittedDocument | None:
        return await _load_document(self._storage, self._cache, raw, key, address)

    async def _find_retirement(self, entry: _DocEntry) -> None:
        cached = self._cache.get(entry.key)
        record = (
            cached.record
            if cached is not None
            else await _Io(
                partial(self._storage.find_document, entry.address, "current")
            )
        )
        self._recheck()
        if record is None:
            return
        _check_scope(entry.raw, record)
        entry.target = record

    # -- settlement (Pi X689–782) ------------------------------------------

    def settle_failure(self) -> tuple[_Handle[object], ...]:
        """Seal after callback failure and abort every change.

        Returns the pending operations; the driver drains them before it
        re-raises the callback error.
        """
        self._sealed = True
        self._abort()
        return tuple(self._pending)

    async def settle_success(self) -> tuple[StorageWrite, ...]:
        """Seal, prepare every change (revoking every draft) and assemble.

        Pending operations abort every change and raise
        ``PendingTxOperations`` for the driver to drain. Any later failure
        aborts every change before Storage admission.
        """
        self._sealed = True
        if self._pending:
            self._abort()
            raise PendingTxOperations(tuple(self._pending))
        try:
            for document in self._documents:
                if document.change is not None:
                    document.prepared = document.change.prepare()
            writes = await self._assemble()
        except BaseException:
            self._abort()
            raise
        self._assembled = True
        return writes

    def discard(self) -> None:
        """Abort every prepared change: an empty batch or a Storage failure."""
        self._abort()

    def adopt(self, seq: Seq) -> tuple[RawDocumentChange, ...]:
        """After Storage success: swap cache entries and describe publication."""
        if not self._assembled or self._adopted:
            raise RuntimeError("Adoption requires one assembled, unadopted transaction")
        self._adopted = True
        commit = _id(seq, "commit sequence")
        return tuple(
            change for plan in self._plans if (change := self._adopt(plan, commit))
        )

    def _adopt(self, plan: _Plan, seq: int) -> RawDocumentChange | None:
        committed = type(plan.record) is DocumentRecord
        record = plan.record
        if not committed or plan.retiring:
            tree = _tree(record)
            if not committed:
                tree["createdAt"] = seq
            if plan.retiring:
                tree["retiredAt"] = seq
            record = DocumentRecord(tree)
        document = cast(DocumentRecord, record)
        prepared = plan.prepared
        if plan.change is not None and prepared is not None:
            loaded = plan.loaded
            # A new incarnation is adopted unless it retires in this commit; a
            # loaded one only when it changed.
            adopted = not plan.retiring and plan.content is not None
            plan.change.settle(adopted)
            if loaded is not None and adopted:
                new = CommittedDocument(
                    loaded.record,
                    plan.version,
                    prepared.value,
                    0 if plan.checkpoint else loaded.deltas_since_base + 1,
                )
                self._cache.swap(plan.key, loaded, new)
            elif loaded is None and adopted:
                new = CommittedDocument(document, plan.version, prepared.value, 0)
                self._cache.install(plan.key, new)
        if plan.retiring:
            if committed:
                self._cache.evict(plan.key, document.id)
            return RawDocumentChange(
                document, plan.conversation_id, None, None, _EMPTY_OPS
            )
        if prepared is not None and _publishes(plan):
            return RawDocumentChange(
                document,
                plan.conversation_id,
                plan.version,
                prepared.value,
                prepared.ops,
            )
        return None

    async def _assemble(self) -> tuple[StorageWrite, ...]:
        plans = [plan for d in self._documents if (plan := _plan(d)) is not None]
        self._plans = plans
        await self._validate_owners()
        await self._validate_replacements()
        await self._retire_terminal_documents(plans)
        # Resolve task publication ownership before Storage admission/adoption.
        for plan in plans:
            if not _publishes(plan):
                continue
            scope = _tree(plan.record)["scope"]
            if scope["kind"] == "conversation":
                plan.conversation_id = scope["conversationId"]
            elif scope["kind"] == "task":
                task = await self._current_task(scope["taskId"])
                if task is not None:
                    plan.conversation_id = _tree(task)["conversationId"]
        await self._resolve_submissions()
        if self._aborted:
            raise TransactionSettled()
        self._checkpoints(plans)
        return self._batch(plans)

    def _checkpoints(self, plans: list[_Plan]) -> None:
        """All preparation and validation finished; no lock surrounds policy."""
        for plan in plans:
            policy, loaded, prepared = plan.checkpoint_when, plan.loaded, plan.prepared
            if policy is None or loaded is None or plan.content is None:
                continue
            assert prepared is not None
            selected = policy(
                prepared.value, prepared.ops, CheckpointInfo(loaded.deltas_since_base)
            )
            if type(selected) is not bool:
                # Only a known native, never-started coroutine can be closed.
                # No foreign close/await/bool/equality/__class__ hook is probed.
                if (
                    type(selected) is CoroutineType
                    and inspect.getcoroutinestate(selected) == inspect.CORO_CREATED
                ):
                    selected.close()
                raise TypeError("checkpoint_when must return an exact bool")
            if self._healthy is not None:
                self._healthy()
            plan.checkpoint = selected
            if selected:
                plan.content = StorageWrite(
                    {
                        "type": "document.change",
                        "id": loaded.record.id,
                        "content": {
                            "version": plan.version,
                            "kind": "base",
                            "value": prepared.value.thaw(),
                        },
                    }
                )

    async def _retire_terminal_documents(self, plans: list[_Plan]) -> None:
        terminals = [
            id
            for id, task in self._tasks.items()
            if task.write is not None and _status(task.write[1]) == "terminal"
        ]
        retiring: set[int] = set()
        for plan in plans:
            scope = _tree(plan.record)["scope"]
            if scope["kind"] == "task" and scope["taskId"] in terminals:
                plan.retiring = True
                retiring.add(plan.record.id)
        for task_id in terminals:
            task = self._tasks[task_id]
            if task.write is not None and task.write[0] == "create":
                continue
            query = DocumentQuery(
                {"scope": {"kind": "task", "taskId": task_id}, "at": "current"}
            )
            cursor = None
            while True:
                page = await _Io(
                    partial(self._storage.scan_documents, query, 256, cursor)
                )
                for record in page.items:
                    if record.id not in retiring:
                        plans.append(
                            _Plan(
                                address_key(cast(Tree, record.to_tree())), record, True
                            )
                        )
                        retiring.add(record.id)
                cursor = page.next
                if cursor is None:
                    break

    async def _validate_replacements(self) -> None:
        for id, task in list(self._tasks.items()):
            if task.write is None or task.write[0] != "replace":
                continue
            committed = await self._committed(id)
            if committed is None:
                raise RuntimeError(f"Task {id} does not exist")
            if _status(committed) == "terminal":
                raise RuntimeError(f"Task {id} is already terminal")
            if (
                _tree(committed)["conversationId"]
                != _tree(task.write[1])["conversationId"]
            ):
                raise RuntimeError(f"Task {id} cannot change conversations")

    async def _resolve_submissions(self) -> None:
        """Against the latest candidate, falling back to committed state."""
        for sid, change in self._submission_changes:
            current = self._submissions.get(sid)
            if current is None:
                lookup = partial(self._storage.submission, SubmissionId(sid))
                current = await _Io(lookup)
            if current is None:
                raise RuntimeError(f"Submission {sid} does not exist")
            following = _apply_submission(current, change)
            if following is not current:
                self._submissions[sid] = following

    def table_changes(
        self, writes: tuple[StorageWrite, ...]
    ) -> tuple[StorageWrite, ...]:
        """The committed table writes of the assembled batch, in batch order.

        Pi ``#publish`` keeps entry, submission, conversation and task writes;
        the batch places them before every document write.
        """
        if not self._assembled:
            raise RuntimeError("Table changes require an assembled transaction")
        return writes[: self._table_count]

    def _batch(self, plans: list[_Plan]) -> tuple[StorageWrite, ...]:
        writes = [
            StorageWrite({"type": kind, "value": record.to_tree()})
            for kind, record in self._writes
        ]
        writes += [
            StorageWrite({"type": "submission", "value": record.to_tree()})
            for record in self._submissions.values()
        ]
        writes += [
            StorageWrite({"type": "task", "value": task.write[1].to_tree()})
            for task in self._tasks.values()
            if task.write is not None
        ]
        self._table_count = len(writes)
        for plan in plans:
            if plan.content is not None:
                writes.append(plan.content)
            if plan.retiring:
                writes.append(
                    StorageWrite({"type": "document.retire", "id": plan.record.id})
                )
        return tuple(writes)

    async def _validate_owners(self) -> None:
        """New owned work needs a live owner on its final candidate (Pi X884–901)."""
        owners: list[tuple[str, int]] = []
        for kind, record in self._writes:
            owner = _tree(record).get("owner") if kind == "conversation" else None
            if owner is not None:
                owners.append(("Conversation owner task", owner["taskId"]))
        for task in self._tasks.values():
            if task.write is not None and task.write[0] == "create":
                owner = _tree(task.write[1]).get("owner")
                if owner is not None:
                    owners.append(("Task owner", owner))
        for what, task_id in owners:
            current = await self._current_task(task_id)
            if current is None:
                raise RuntimeError(f"{what} {task_id} does not exist")
            status = _status(current)
            if status in {"terminal", "completing"}:
                raise RuntimeError(f"{what} {task_id} is {status}")
            if _tree(current)["abortRequested"]:
                raise RuntimeError(f"{what} {task_id} is abort-marked")

    def _abort(self) -> None:
        self._aborted = True
        for document in self._documents:
            if document.change is not None:
                document.change.discard()

    # -- internal task and conversation resolution (Pi X970–1011) ----------

    async def _require_conversation(self, id: int) -> None:
        if id in self._created_conversations:
            return
        if await _Io(partial(self._storage.conversation, ConversationId(id))) is None:
            raise RuntimeError(f"Conversation {id} does not exist")

    def _task_entry(self, id: int) -> _TaskEntry:
        task = self._tasks.get(id)
        if task is None:
            task = self._tasks[id] = _TaskEntry()
        return task

    async def _current_task(self, id: int) -> TaskRecord | None:
        """Latest candidate, else committed; not a caller table read."""
        task = self._tasks.get(id)
        if task is not None and task.write is not None:
            return task.write[1]
        return await self._committed(id)

    async def _committed(self, id: int) -> TaskRecord | None:
        task = self._task_entry(id)
        if task.committed is None:
            task.committed = _Io(partial(self._storage.task, TaskId(id)))
        return await task.committed

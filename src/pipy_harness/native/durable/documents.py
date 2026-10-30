"""Private typed current-document tokens and owned logical addresses.

Adapted from Pi durable documents.ts at 4a080252f06caf63a525d7873c5d94d3c23887e0,
MIT Copyright (c) 2025 Mario Zechner; see UPSTREAM-LICENSE.txt. Python uses
explicit owner/key keywords and exact built-ins rather than TS overloads.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeAlias, TypeVar

from .codec import OwnedJson
from .records import OMITTED, SAFE_INTEGER, DocumentCreate, DocumentRecord
from .storage import DocumentAddress

V = TypeVar("V")
S = TypeVar("S")
Scope: TypeAlias = Literal["session", "conversation", "task"]
Key: TypeAlias = tuple[bytes, str, int | None, bytes | None]


@dataclass(frozen=True, slots=True)
class CheckpointInfo:
    """Prior committed delta count supplied to a document's checkpoint policy."""

    deltas_since_base: int


CheckpointWhen: TypeAlias = Callable[[OwnedJson, OwnedJson, CheckpointInfo], bool]


def _choice(value: object, choices: tuple[str, ...], name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"Document {name} must be an exact string")
    for choice in choices:
        if value == choice:
            return choice
    raise TypeError(f"Document {name} is not supported")


def _fields(
    kind: object,
    version: object,
    scope: object,
    initial: object,
    history: object,
    fork: object,
    checkpoint_when: object,
) -> tuple[str, Any, Any]:
    if type(kind) is not str:
        raise TypeError("Document kind must be a string")
    if type(version) is not int or not 1 <= version <= SAFE_INTEGER:
        raise TypeError(f"Document {kind} version must be a positive integer")
    scoped = _choice(scope, ("session", "conversation", "task"), "scope")
    if not callable(initial):
        raise TypeError(f"Document {kind} requires an initial factory")
    if not callable(checkpoint_when):
        raise TypeError("Document checkpoint_when must be callable")
    if scoped != "conversation":
        if history is not None or fork is not None:
            raise TypeError("Only conversation documents have history/fork policies")
        return scoped, None, None
    try:
        retained = _choice(history, ("latest", "rewindable"), "history")
        forks = ("current", "initial") + (("asOf",) if retained == "rewindable" else ())
        return scoped, retained, _choice(fork, forks, "fork")
    except TypeError:
        raise TypeError(f"Document {kind} has invalid history/fork semantics") from None


@dataclass(frozen=True, slots=True)
class Doc(Generic[V]):
    """Immutable singleton definition; its initializer runs only for absent state."""

    kind: str
    version: int
    scope: Scope
    initial: Callable[[], V]
    history: Literal["latest", "rewindable"] | None = None
    fork: Literal["current", "initial", "asOf"] | None = None
    checkpoint_when: CheckpointWhen = field(kw_only=True)

    def __post_init__(self) -> None:
        scope, history, fork = _fields(
            self.kind,
            self.version,
            self.scope,
            self.initial,
            self.history,
            self.fork,
            self.checkpoint_when,
        )
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "history", history)
        object.__setattr__(self, "fork", fork)


@dataclass(frozen=True, slots=True)
class DocFamily(Generic[S, V]):
    """Immutable keyed definition; the first acquisition owns its copied seed."""

    kind: str
    version: int
    scope: Scope
    initial: Callable[[S], V]
    history: Literal["latest", "rewindable"] | None = None
    fork: Literal["current", "initial", "asOf"] | None = None
    checkpoint_when: CheckpointWhen = field(kw_only=True)

    def __post_init__(self) -> None:
        scope, history, fork = _fields(
            self.kind,
            self.version,
            self.scope,
            self.initial,
            self.history,
            self.fork,
            self.checkpoint_when,
        )
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "history", history)
        object.__setattr__(self, "fork", fork)


Token: TypeAlias = Doc[Any] | DocFamily[Any, Any]


def define_doc(
    kind: str,
    version: int,
    scope: Scope,
    initial: Callable[[], V],
    history: Literal["latest", "rewindable"] | None = None,
    fork: Literal["current", "initial", "asOf"] | None = None,
    *,
    checkpoint_when: CheckpointWhen,
) -> Doc[V]:
    return Doc(
        kind, version, scope, initial, history, fork, checkpoint_when=checkpoint_when
    )


def define_doc_family(
    kind: str,
    version: int,
    scope: Scope,
    initial: Callable[[S], V],
    history: Literal["latest", "rewindable"] | None = None,
    fork: Literal["current", "initial", "asOf"] | None = None,
    *,
    checkpoint_when: CheckpointWhen,
) -> DocFamily[S, V]:
    return DocFamily(
        kind, version, scope, initial, history, fork, checkpoint_when=checkpoint_when
    )


def address_key(value: dict[str, Any]) -> Key:
    """Owned exact storage DTO -> UTF-16 address identity, including absent vs ''."""
    scope = value["scope"]
    owner = scope.get("conversationId", scope.get("taskId"))
    return (
        value["kind"].encode("utf-16-be", "surrogatepass"),
        scope["kind"],
        owner,
        value["key"].encode("utf-16-be", "surrogatepass") if "key" in value else None,
    )


def resolve(
    token: Token,
    conversation_id: object = None,
    task_id: object = None,
    key: object = OMITTED,
) -> tuple[Key, DocumentAddress]:
    if type(token) not in (Doc, DocFamily):
        raise TypeError("Expected a document token")
    # Recheck exact components before cache lookup, even after hostile object.__setattr__.
    scope, _, _ = _fields(
        token.kind,
        token.version,
        token.scope,
        token.initial,
        token.history,
        token.fork,
        token.checkpoint_when,
    )
    scoped: dict[str, Any]
    if scope == "session":
        if conversation_id is not None or task_id is not None:
            raise TypeError(f"Document {token.kind} is session-scoped")
        scoped = {"kind": "session"}
    else:
        owner = conversation_id if scope == "conversation" else task_id
        other = task_id if scope == "conversation" else conversation_id
        if (
            other is not None
            or type(owner) is not int
            or not 1 <= owner <= SAFE_INTEGER
        ):
            raise TypeError(f"Document {token.kind} requires a {scope} ID")
        scoped = {
            "kind": scope,
            "conversationId" if scope == "conversation" else "taskId": owner,
        }
    tree: dict[str, Any] = {"kind": token.kind, "scope": scoped}
    if type(token) is DocFamily:
        if type(key) is not str:
            raise TypeError("Document family requires an exact string key")
        tree["key"] = key
    elif key is not OMITTED:
        raise TypeError("Singleton document does not accept a family key")
    return address_key(tree), DocumentAddress(tree)


def check_scope(token: Token, record: DocumentCreate | DocumentRecord) -> None:
    tree: Any = record.to_tree()
    if tree["scope"]["kind"] != token.scope or (
        token.scope == "conversation"
        and (tree.get("history") != token.history or tree.get("fork") != token.fork)
    ):
        raise TypeError(
            f"Document {tree['id']} ({tree['kind']}) does not match the supplied definition semantics"
        )

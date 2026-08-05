"""Read-only session and conversation views for extension contexts.

These are the immutable, JSON-safe projections an extension command,
shortcut, or hook handler sees instead of live product state: the
`ConversationView` over a dispatch-time message snapshot, the Pi-shaped
`SessionManagerView` over the native session tree, and the frozen
header/entry/tree-node value objects they return. Everything here is
detached from the live session — handlers can never mutate product state
through a view, and view data is round-tripped through the stdlib JSON
codec so a handler also cannot smuggle live object references out of the
session tree.

`_CommandContext` (`pipy_harness.native.extensions.command_context`)
builds these views per invocation; the public names are re-exported from
`pipy_harness.extensions`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - imported for type checkers only
    from pipy_harness.native.session_tree import (
        NativeSessionTree,
        SessionEntry,
        SessionHeader,
        SessionTreeNode,
    )


@dataclass(frozen=True, slots=True)
class AssistantMessageView:
    """Read-only view of an assistant message handed to a command handler.

    `text` is the assistant message content. `complete` is True when it was a
    finished text answer — i.e. it carries text and left no tool calls pending
    (the pipy analog of Pi's `stopReason === "stop"`). A handler that wants the
    last *complete* answer (e.g. to extract questions from it) checks `complete`
    before using `text`.
    """

    text: str
    complete: bool


@runtime_checkable
class ConversationView(Protocol):
    """Read-only view of the live conversation handed to a command handler."""

    def last_assistant_message(self) -> "AssistantMessageView | None": ...


@dataclass(frozen=True, slots=True)
class SessionHeaderView:
    """Read-only extension view of the active native session header."""

    id: str | None = None
    timestamp: str | None = None
    cwd: str | None = None
    version: int | None = None
    parent_session: str | None = None

    def to_dict(self) -> dict[str, object | None]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "cwd": self.cwd,
            "version": self.version,
            "parentSession": self.parent_session,
        }


@dataclass(frozen=True, slots=True)
class SessionEntryView:
    """Immutable, JSON-like extension view of one native session entry."""

    id: str
    parent_id: str | None
    timestamp: str
    type: str
    data: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        body: dict[str, object] = {
            "id": self.id,
            "parentId": self.parent_id,
            "timestamp": self.timestamp,
            "type": self.type,
        }
        body.update(dict(self.data))
        return body


@dataclass(frozen=True, slots=True)
class SessionTreeNodeView:
    """Read-only extension view of a session tree node."""

    entry: SessionEntryView
    children: tuple["SessionTreeNodeView", ...] = ()
    label: str | None = None
    label_timestamp: str | None = None

    def to_dict(self) -> dict[str, object | None]:
        return {
            "entry": self.entry.to_dict(),
            "children": [child.to_dict() for child in self.children],
            "label": self.label,
            "labelTimestamp": self.label_timestamp,
        }


@runtime_checkable
class SessionManagerView(Protocol):
    """Read-only Pi-shaped session-manager view for extension contexts."""

    def get_cwd(self) -> str | None: ...
    def get_session_dir(self) -> str | None: ...
    def get_session_id(self) -> str | None: ...
    def get_session_file(self) -> str | None: ...
    def get_leaf_id(self) -> str | None: ...
    def get_leaf_entry(self) -> SessionEntryView | None: ...
    def get_entry(self, entry_id: str) -> SessionEntryView | None: ...
    def get_label(self, entry_id: str) -> str | None: ...
    def get_branch(
        self, from_id: str | None = None
    ) -> tuple[SessionEntryView, ...]: ...
    def get_header(self) -> SessionHeaderView: ...
    def get_entries(self) -> tuple[SessionEntryView, ...]: ...
    def get_tree(self) -> tuple[SessionTreeNodeView, ...]: ...
    def get_session_name(self) -> str | None: ...


class _ConversationView:
    """Concrete `ConversationView` over a snapshot of the message history.

    The handler receives a snapshot taken at dispatch time; it never mutates
    the live conversation. Messages without an assistant turn yield `None`.
    """

    def __init__(self, messages: "Sequence[object]" = ()) -> None:
        self._messages = tuple(messages)

    def last_assistant_message(self) -> "AssistantMessageView | None":
        # Import here to avoid a heavy import at module load.
        from pipy_harness.native.agent import AgentAssistantMessage

        for message in reversed(self._messages):
            if isinstance(message, AgentAssistantMessage):
                text = message.content.value
                complete = bool(text.strip()) and not message.tool_calls
                return AssistantMessageView(text=text, complete=complete)
        return None


def _validated_json_value(value: object) -> object:
    """Validate and detach one value produced by the stdlib JSON decoder."""

    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        detached_items: list[object] = []
        for item in value:
            detached_items.append(_validated_json_value(item))
        return detached_items
    if isinstance(value, dict):
        detached_mapping: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object key is not a string")
            detached_mapping[key] = _validated_json_value(item)
        return detached_mapping
    raise ValueError("JSON decoder produced an unsupported value")


def _json_round_trip(value: object) -> tuple[str, object]:
    """Encode, decode, and executably narrow a JSON-compatible value."""

    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    return encoded, _validated_json_value(json.loads(encoded))


def _copy_session_data(value: object) -> object:
    try:
        _encoded, decoded = _json_round_trip(value)
        return decoded
    except (TypeError, ValueError):
        return str(value)


def _session_header_view(header: "SessionHeader") -> SessionHeaderView:
    return SessionHeaderView(
        id=header.id,
        timestamp=header.timestamp,
        cwd=header.cwd,
        version=header.version,
        parent_session=header.parent_session,
    )


def _session_entry_view(entry: "SessionEntry") -> SessionEntryView:
    from pipy_harness.native.session_tree import _entry_to_json

    body = _entry_to_json(entry)
    data = {
        key: _copy_session_data(value)
        for key, value in body.items()
        if key not in {"id", "parentId", "timestamp", "type"}
    }
    parent_id = body.get("parentId")
    return SessionEntryView(
        id=str(body["id"]),
        parent_id=parent_id if isinstance(parent_id, str) else None,
        timestamp=str(body.get("timestamp", "")),
        type=str(body.get("type", "")),
        data=MappingProxyType(data),
    )


def _session_tree_node_view(node: "SessionTreeNode") -> SessionTreeNodeView:
    return SessionTreeNodeView(
        entry=_session_entry_view(node.entry),
        children=tuple(_session_tree_node_view(child) for child in node.children),
        label=node.label,
        label_timestamp=node.label_timestamp,
    )


class _ReadOnlySessionManagerView:
    """Read-only adapter from `NativeSessionTree` to extension context."""

    def __init__(self, tree: "NativeSessionTree | None" = None) -> None:
        self._tree = tree

    def get_cwd(self) -> str | None:
        return self._tree.header.cwd if self._tree is not None else None

    def get_session_dir(self) -> str | None:
        if self._tree is None or self._tree.path is None:
            return None
        return str(self._tree.path.parent)

    def get_session_id(self) -> str | None:
        return self._tree.session_id if self._tree is not None else None

    def get_session_file(self) -> str | None:
        if self._tree is None or self._tree.path is None:
            return None
        return str(self._tree.path)

    def get_leaf_id(self) -> str | None:
        return self._tree.get_leaf_id() if self._tree is not None else None

    def get_leaf_entry(self) -> SessionEntryView | None:
        if self._tree is None:
            return None
        entry = self._tree.get_leaf_entry()
        return _session_entry_view(entry) if entry is not None else None

    def get_entry(self, entry_id: str) -> SessionEntryView | None:
        if self._tree is None:
            return None
        entry = self._tree.get_entry(str(entry_id))
        return _session_entry_view(entry) if entry is not None else None

    def get_label(self, entry_id: str) -> str | None:
        return self._tree.get_label(str(entry_id)) if self._tree is not None else None

    def get_branch(self, from_id: str | None = None) -> tuple[SessionEntryView, ...]:
        if self._tree is None:
            return ()
        return tuple(
            _session_entry_view(entry) for entry in self._tree.get_branch(from_id)
        )

    def get_header(self) -> SessionHeaderView:
        if self._tree is None:
            return SessionHeaderView()
        return _session_header_view(self._tree.get_header())

    def get_entries(self) -> tuple[SessionEntryView, ...]:
        if self._tree is None:
            return ()
        return tuple(_session_entry_view(entry) for entry in self._tree.get_entries())

    def get_tree(self) -> tuple[SessionTreeNodeView, ...]:
        if self._tree is None:
            return ()
        return tuple(_session_tree_node_view(node) for node in self._tree.get_tree())

    def get_session_name(self) -> str | None:
        return self._tree.name if self._tree is not None else None

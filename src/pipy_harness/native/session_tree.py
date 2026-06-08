"""Pi-style native product session tree for pipy-native.

This module implements the durable, append-only conversation tree described in
``docs/session-tree.md``. It is the product session source of truth for
pipy-native: full-history resume, ``/tree`` navigation, ``/fork``/``/clone``,
``/resume``, ``/new``, branch summaries, and durable compaction all read and
write this store. The existing ``pipy-session`` metadata archive is a separate
summary-safe catalog surface and is *not* the product session state.

Storage layout (analogous to Pi's ``~/.pi/agent/sessions/...``)::

    ~/.local/state/pipy/native-sessions/--<encoded-cwd>--/<timestamp>_<uuid>.jsonl

Each file is append-only JSONL: a ``session`` header line followed by tree
entries. Every non-header entry carries ``type``, ``id`` (unique within the
file), ``parentId`` (parent entry id or ``null``), and ``timestamp``. The
in-memory manager keeps all entries in append order, an id->entry map, resolved
labels, and a current ``leafId`` that defaults to the latest entry on load.

The file intentionally contains raw conversation content (user prompts,
assistant text, tool results, compaction/branch summaries, labels, names)
because ``/tree`` and product resume require them. It lives outside git, uses
owner-only permissions where practical, and is never synced by the metadata
archive recipes.
"""

from __future__ import annotations

import json
import os
import stat
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    LoopMessage,
    ToolResultMessage,
    UserMessage,
)

CURRENT_SESSION_VERSION = 1


# ---------------------------------------------------------------------------
# Storage path helpers
# ---------------------------------------------------------------------------


def encode_cwd_dir_name(cwd: Path) -> str:
    """Encode ``cwd`` into a safe directory name, matching Pi's shape.

    Pi uses ``--<cwd with separators replaced by '-'>--``. We resolve the path
    and replace path separators and colons with ``-`` so the encoded name is a
    single filesystem-safe component.
    """

    resolved = str(cwd)
    # Drop a single leading separator, then replace separators / colons.
    trimmed = resolved.lstrip("/\\")
    safe = (
        trimmed.replace("/", "-").replace("\\", "-").replace(":", "-")
    )
    return f"--{safe}--"


def default_state_root() -> Path:
    configured = os.environ.get("PIPY_NATIVE_SESSIONS_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "state" / "pipy"


def default_native_session_dir(
    cwd: Path, *, state_root: Path | None = None
) -> Path:
    root = state_root or default_state_root()
    return root / "native-sessions" / encode_cwd_dir_name(cwd)


# ---------------------------------------------------------------------------
# Header + entry value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionHeader:
    """First line of a native session file."""

    id: str
    timestamp: str
    cwd: str
    version: int = CURRENT_SESSION_VERSION
    parent_session: str | None = None
    type: str = "session"

    def to_json_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": "session",
            "version": self.version,
            "id": self.id,
            "timestamp": self.timestamp,
            "cwd": self.cwd,
        }
        if self.parent_session:
            body["parentSession"] = self.parent_session
        return body


@dataclass(frozen=True, slots=True)
class MessageEntry:
    """A provider-visible message in the tree."""

    id: str
    parent_id: str | None
    timestamp: str
    message: LoopMessage
    type: str = "message"


@dataclass(frozen=True, slots=True)
class ModelChangeEntry:
    id: str
    parent_id: str | None
    timestamp: str
    provider: str
    model_id: str
    type: str = "model_change"


@dataclass(frozen=True, slots=True)
class ThinkingLevelChangeEntry:
    id: str
    parent_id: str | None
    timestamp: str
    thinking_level: str
    type: str = "thinking_level_change"


@dataclass(frozen=True, slots=True)
class CompactionEntry:
    id: str
    parent_id: str | None
    timestamp: str
    summary: str
    first_kept_entry_id: str
    tokens_before: int
    type: str = "compaction"


@dataclass(frozen=True, slots=True)
class BranchSummaryEntry:
    id: str
    parent_id: str | None
    timestamp: str
    from_id: str
    summary: str
    type: str = "branch_summary"


@dataclass(frozen=True, slots=True)
class LabelEntry:
    id: str
    parent_id: str | None
    timestamp: str
    target_id: str
    label: str | None
    type: str = "label"


@dataclass(frozen=True, slots=True)
class SessionInfoEntry:
    id: str
    parent_id: str | None
    timestamp: str
    name: str | None
    type: str = "session_info"


@dataclass(frozen=True, slots=True)
class CustomEntry:
    id: str
    parent_id: str | None
    timestamp: str
    custom_type: str
    data: Any = None
    type: str = "custom"


@dataclass(frozen=True, slots=True)
class CustomMessageEntry:
    id: str
    parent_id: str | None
    timestamp: str
    custom_type: str
    content: str
    display: bool = True
    type: str = "custom_message"


SessionEntry = (
    MessageEntry
    | ModelChangeEntry
    | ThinkingLevelChangeEntry
    | CompactionEntry
    | BranchSummaryEntry
    | LabelEntry
    | SessionInfoEntry
    | CustomEntry
    | CustomMessageEntry
)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _message_to_json(message: LoopMessage) -> dict[str, Any]:
    if isinstance(message, UserMessage):
        return {"role": "user", "content": message.content}
    if isinstance(message, AssistantMessage):
        return {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "provider_correlation_id": call.provider_correlation_id,
                    "tool_name": call.tool_name,
                    "arguments_json": call.arguments_json,
                }
                for call in message.tool_calls
            ],
        }
    if isinstance(message, ToolResultMessage):
        return {
            "role": "tool",
            "tool_request_id": message.tool_request_id,
            "output_text": message.output_text,
            "is_error": message.is_error,
            "provider_correlation_id": message.provider_correlation_id,
        }
    raise TypeError(f"unsupported message type: {type(message)!r}")


def _message_from_json(body: dict[str, Any]) -> LoopMessage:
    role = body.get("role")
    if role == "user":
        return UserMessage(content=str(body.get("content", "")))
    if role == "assistant":
        raw_calls = body.get("tool_calls") or []
        tool_calls = tuple(
            ProviderToolCall(
                provider_correlation_id=str(call["provider_correlation_id"]),
                tool_name=str(call["tool_name"]),
                arguments_json=str(call["arguments_json"]),
            )
            for call in raw_calls
        )
        return AssistantMessage(
            content=str(body.get("content", "")),
            tool_calls=tool_calls,
        )
    if role == "tool":
        return ToolResultMessage(
            tool_request_id=str(body["tool_request_id"]),
            output_text=str(body.get("output_text", "")),
            is_error=bool(body.get("is_error", False)),
            provider_correlation_id=body.get("provider_correlation_id"),
        )
    raise ValueError(f"unsupported message role: {role!r}")


def _entry_to_json(entry: SessionEntry) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": entry.type,
        "id": entry.id,
        "parentId": entry.parent_id,
        "timestamp": entry.timestamp,
    }
    if isinstance(entry, MessageEntry):
        base["message"] = _message_to_json(entry.message)
    elif isinstance(entry, ModelChangeEntry):
        base["provider"] = entry.provider
        base["modelId"] = entry.model_id
    elif isinstance(entry, ThinkingLevelChangeEntry):
        base["thinkingLevel"] = entry.thinking_level
    elif isinstance(entry, CompactionEntry):
        base["summary"] = entry.summary
        base["firstKeptEntryId"] = entry.first_kept_entry_id
        base["tokensBefore"] = entry.tokens_before
    elif isinstance(entry, BranchSummaryEntry):
        base["fromId"] = entry.from_id
        base["summary"] = entry.summary
    elif isinstance(entry, LabelEntry):
        base["targetId"] = entry.target_id
        base["label"] = entry.label
    elif isinstance(entry, SessionInfoEntry):
        base["name"] = entry.name
    elif isinstance(entry, CustomEntry):
        base["customType"] = entry.custom_type
        base["data"] = entry.data
    elif isinstance(entry, CustomMessageEntry):
        base["customType"] = entry.custom_type
        base["content"] = entry.content
        base["display"] = entry.display
    return base


def _entry_from_json(body: dict[str, Any]) -> SessionEntry | None:
    entry_type = body.get("type")
    entry_id = body.get("id")
    if not isinstance(entry_id, str) or not entry_id:
        return None
    parent_id = body.get("parentId")
    if parent_id is not None and not isinstance(parent_id, str):
        return None
    timestamp = str(body.get("timestamp", ""))
    try:
        if entry_type == "message":
            return MessageEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=timestamp,
                message=_message_from_json(dict(body.get("message", {}))),
            )
        if entry_type == "model_change":
            return ModelChangeEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=timestamp,
                provider=str(body["provider"]),
                model_id=str(body["modelId"]),
            )
        if entry_type == "thinking_level_change":
            return ThinkingLevelChangeEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=timestamp,
                thinking_level=str(body["thinkingLevel"]),
            )
        if entry_type == "compaction":
            return CompactionEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=timestamp,
                summary=str(body.get("summary", "")),
                first_kept_entry_id=str(body["firstKeptEntryId"]),
                tokens_before=int(body.get("tokensBefore", 0)),
            )
        if entry_type == "branch_summary":
            return BranchSummaryEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=timestamp,
                from_id=str(body.get("fromId", "root")),
                summary=str(body.get("summary", "")),
            )
        if entry_type == "label":
            label = body.get("label")
            return LabelEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=timestamp,
                target_id=str(body["targetId"]),
                label=None if label is None else str(label),
            )
        if entry_type == "session_info":
            name = body.get("name")
            return SessionInfoEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=timestamp,
                name=None if name is None else str(name),
            )
        if entry_type == "custom":
            return CustomEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=timestamp,
                custom_type=str(body.get("customType", "")),
                data=body.get("data"),
            )
        if entry_type == "custom_message":
            return CustomMessageEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=timestamp,
                custom_type=str(body.get("customType", "")),
                content=str(body.get("content", "")),
                display=bool(body.get("display", True)),
            )
    except (KeyError, ValueError, TypeError):
        return None
    return None


# ---------------------------------------------------------------------------
# Context reconstruction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Provider-visible context rebuilt from the active branch."""

    messages: tuple[LoopMessage, ...]
    thinking_level: str
    model: tuple[str, str] | None  # (provider, model_id)


def _compaction_summary_message(summary: str) -> UserMessage:
    return UserMessage(
        content=(
            "[Earlier context was compacted to save space. Summary of the "
            f"removed turns:]\n{summary}"
        )
    )


def _branch_summary_message(summary: str) -> UserMessage:
    return UserMessage(
        content=(
            "[Summary of an abandoned conversation branch:]\n" + summary
        )
    )


def build_context(
    entries: list[SessionEntry],
    leaf_id: str | None,
    by_id: dict[str, SessionEntry] | None = None,
) -> SessionContext:
    """Rebuild provider-visible context by walking leaf->root then reversing.

    Only entries on the active branch contribute. Compaction emits its summary
    first then keeps messages from ``first_kept_entry_id`` through the
    compaction boundary plus all later active-branch messages. Branch summaries
    and custom messages contribute messages; model/thinking changes only update
    runtime settings.
    """

    if by_id is None:
        by_id = {entry.id: entry for entry in entries}

    if leaf_id is None and entries:
        # Explicit root navigation (leaf before first entry) is signalled by
        # callers via a dedicated marker; an unset leaf with entries present
        # means "no leaf chosen yet" only at construction time. The manager
        # always passes its own leaf, so leaf_id is authoritative here: None
        # means an empty/root context.
        return SessionContext(messages=(), thinking_level="off", model=None)

    leaf: SessionEntry | None = by_id.get(leaf_id) if leaf_id else None
    if leaf is None:
        return SessionContext(messages=(), thinking_level="off", model=None)

    # Walk leaf -> root.
    path: list[SessionEntry] = []
    current: SessionEntry | None = leaf
    while current is not None:
        path.insert(0, current)
        current = by_id.get(current.parent_id) if current.parent_id else None

    thinking_level = "off"
    model: tuple[str, str] | None = None
    compaction: CompactionEntry | None = None
    for entry in path:
        if isinstance(entry, ThinkingLevelChangeEntry):
            thinking_level = entry.thinking_level
        elif isinstance(entry, ModelChangeEntry):
            model = (entry.provider, entry.model_id)
        elif isinstance(entry, CompactionEntry):
            compaction = entry

    messages: list[LoopMessage] = []

    def append_message(entry: SessionEntry) -> None:
        if isinstance(entry, MessageEntry):
            messages.append(entry.message)
        elif isinstance(entry, CustomMessageEntry):
            messages.append(UserMessage(content=entry.content))
        elif isinstance(entry, BranchSummaryEntry) and entry.summary:
            messages.append(_branch_summary_message(entry.summary))

    if compaction is not None:
        messages.append(_compaction_summary_message(compaction.summary))
        compaction_idx = next(
            (
                i
                for i, entry in enumerate(path)
                if isinstance(entry, CompactionEntry)
                and entry.id == compaction.id
            ),
            -1,
        )
        found_first_kept = False
        for i in range(compaction_idx):
            entry = path[i]
            if entry.id == compaction.first_kept_entry_id:
                found_first_kept = True
            if found_first_kept:
                append_message(entry)
        for i in range(compaction_idx + 1, len(path)):
            append_message(path[i])
    else:
        for entry in path:
            append_message(entry)

    return SessionContext(
        messages=tuple(messages),
        thinking_level=thinking_level,
        model=model,
    )


# ---------------------------------------------------------------------------
# Tree node for /tree
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SessionTreeNode:
    entry: SessionEntry
    children: list[SessionTreeNode] = field(default_factory=list)
    label: str | None = None
    label_timestamp: str | None = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


def _new_entry_id(existing: dict[str, SessionEntry]) -> str:
    for _ in range(100):
        candidate = uuid.uuid4().hex[:8]
        if candidate not in existing:
            return candidate
    return uuid.uuid4().hex


def _new_session_id() -> str:
    return uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_file_entries(path: Path) -> tuple[SessionHeader | None, list[SessionEntry]]:
    try:
        content = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None, []
    header: SessionHeader | None = None
    entries: list[SessionEntry] = []
    for index, line in enumerate(content.splitlines()):
        if not line.strip():
            continue
        try:
            body = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(body, dict):
            continue
        if index == 0 or (header is None and body.get("type") == "session"):
            if body.get("type") == "session" and isinstance(body.get("id"), str):
                header = SessionHeader(
                    id=str(body["id"]),
                    timestamp=str(body.get("timestamp", "")),
                    cwd=str(body.get("cwd", "")),
                    version=int(body.get("version", CURRENT_SESSION_VERSION)),
                    parent_session=body.get("parentSession"),
                )
                continue
        if body.get("type") == "session":
            continue
        entry = _entry_from_json(body)
        if entry is not None:
            entries.append(entry)
    return header, entries


@dataclass
class NativeSessionTree:
    """In-memory manager for one native product session file."""

    header: SessionHeader
    path: Path | None
    persist: bool = True
    entries: list[SessionEntry] = field(default_factory=list)
    by_id: dict[str, SessionEntry] = field(default_factory=dict)
    labels_by_id: dict[str, str] = field(default_factory=dict)
    label_timestamps_by_id: dict[str, str] = field(default_factory=dict)
    leaf_id: str | None = None
    _name: str | None = None

    MAX_NAME_LENGTH: ClassVar[int] = 200

    # -- construction -------------------------------------------------------

    @classmethod
    def create(
        cls,
        cwd: Path,
        *,
        session_dir: Path | None = None,
        state_root: Path | None = None,
        persist: bool = True,
        session_id: str | None = None,
        parent_session: str | None = None,
        timestamp: str | None = None,
    ) -> NativeSessionTree:
        resolved_cwd = cwd.expanduser().resolve()
        sid = session_id or _new_session_id()
        ts = timestamp or _now_iso()
        header = SessionHeader(
            id=sid,
            timestamp=ts,
            cwd=str(resolved_cwd),
            parent_session=parent_session,
        )
        path: Path | None = None
        if persist:
            directory = session_dir or default_native_session_dir(
                resolved_cwd, state_root=state_root
            )
            directory.mkdir(parents=True, exist_ok=True)
            try:
                directory.chmod(0o700)
            except OSError:
                pass
            file_ts = ts.replace(":", "-").replace(".", "-")
            path = directory / f"{file_ts}_{sid}.jsonl"
        tree = cls(header=header, path=path, persist=persist)
        tree._write_header()
        return tree

    @classmethod
    def open(cls, path: Path, *, persist: bool = True) -> NativeSessionTree:
        resolved = Path(path).expanduser()
        header, entries = _load_file_entries(resolved)
        if header is None:
            raise ValueError(f"not a valid native session file: {resolved}")
        tree = cls(header=header, path=resolved if persist else None, persist=persist)
        tree._load_entries(entries)
        return tree

    @classmethod
    def continue_recent(
        cls,
        cwd: Path,
        *,
        session_dir: Path | None = None,
        state_root: Path | None = None,
        persist: bool = True,
    ) -> NativeSessionTree | None:
        directory = session_dir or default_native_session_dir(
            cwd.expanduser().resolve(), state_root=state_root
        )
        recent = most_recent_session_file(directory)
        if recent is None:
            return None
        return cls.open(recent, persist=persist)

    @classmethod
    def fork_from(
        cls,
        source_path: Path,
        target_cwd: Path,
        *,
        leaf_id: str | None = None,
        session_dir: Path | None = None,
        state_root: Path | None = None,
        persist: bool = True,
    ) -> NativeSessionTree:
        """Create a new session file containing the active branch of ``source``.

        When ``leaf_id`` is given, only the root->leaf path is copied;
        otherwise the source's current leaf path is used. The new file records
        ``parentSession`` pointing at the source.
        """

        source = cls.open(source_path, persist=False)
        path_entries = source.get_branch(leaf_id)
        new_tree = cls.create(
            target_cwd,
            session_dir=session_dir,
            state_root=state_root,
            persist=persist,
            parent_session=str(Path(source_path).expanduser()),
        )
        # Re-create entries fresh so ids are unique in the new file. Labels are
        # reattached from the source's resolved label map.
        id_map: dict[str, str] = {}
        for entry in path_entries:
            if isinstance(entry, LabelEntry):
                continue
            new_entry = new_tree._clone_entry_onto_leaf(entry, id_map=id_map)
            id_map[entry.id] = new_entry.id
        for old_id, new_id in id_map.items():
            label = source.labels_by_id.get(old_id)
            if label:
                new_tree.append_label_change(new_id, label)
        if source._name:
            new_tree.append_session_info(source._name)
        return new_tree

    # -- internal load ------------------------------------------------------

    def _load_entries(self, entries: Iterable[SessionEntry]) -> None:
        for entry in entries:
            self.entries.append(entry)
            self.by_id[entry.id] = entry
            if isinstance(entry, LabelEntry):
                if entry.label:
                    self.labels_by_id[entry.target_id] = entry.label
                    self.label_timestamps_by_id[entry.target_id] = entry.timestamp
                else:
                    self.labels_by_id.pop(entry.target_id, None)
                    self.label_timestamps_by_id.pop(entry.target_id, None)
            elif isinstance(entry, SessionInfoEntry):
                self._name = entry.name
        self.leaf_id = self.entries[-1].id if self.entries else None

    # -- file IO ------------------------------------------------------------

    def _write_header(self) -> None:
        if not self.persist or self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.header.to_json_dict()) + "\n")
        try:
            self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def _write_entry(self, entry: SessionEntry) -> None:
        if not self.persist or self.path is None:
            return
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_entry_to_json(entry)) + "\n")

    # -- append -------------------------------------------------------------

    def _append_entry(self, entry: SessionEntry) -> SessionEntry:
        self.entries.append(entry)
        self.by_id[entry.id] = entry
        self.leaf_id = entry.id
        self._write_entry(entry)
        return entry

    def _next_id(self) -> str:
        return _new_entry_id(self.by_id)

    def append_message(self, message: LoopMessage) -> MessageEntry:
        entry = MessageEntry(
            id=self._next_id(),
            parent_id=self.leaf_id,
            timestamp=_now_iso(),
            message=message,
        )
        return self._append_entry(entry)  # type: ignore[return-value]

    def append_model_change(
        self, provider: str, model_id: str
    ) -> ModelChangeEntry:
        entry = ModelChangeEntry(
            id=self._next_id(),
            parent_id=self.leaf_id,
            timestamp=_now_iso(),
            provider=provider,
            model_id=model_id,
        )
        return self._append_entry(entry)  # type: ignore[return-value]

    def append_thinking_level_change(
        self, thinking_level: str
    ) -> ThinkingLevelChangeEntry:
        """Record a ``thinking_level_change`` entry on the active branch.

        Called by the product TUI's Shift+Tab thinking-level cycle and the
        ``/settings`` "cycle thinking level" action (see
        ``NativeToolReplSession._cycle_thinking_level``), so the chosen reasoning
        level is durable across resume; runs no provider turn.
        """

        entry = ThinkingLevelChangeEntry(
            id=self._next_id(),
            parent_id=self.leaf_id,
            timestamp=_now_iso(),
            thinking_level=thinking_level,
        )
        return self._append_entry(entry)  # type: ignore[return-value]

    def append_compaction(
        self, *, summary: str, first_kept_entry_id: str, tokens_before: int
    ) -> CompactionEntry:
        entry = CompactionEntry(
            id=self._next_id(),
            parent_id=self.leaf_id,
            timestamp=_now_iso(),
            summary=summary,
            first_kept_entry_id=first_kept_entry_id,
            tokens_before=tokens_before,
        )
        return self._append_entry(entry)  # type: ignore[return-value]

    def append_custom(self, custom_type: str, data: Any = None) -> CustomEntry:
        entry = CustomEntry(
            id=self._next_id(),
            parent_id=self.leaf_id,
            timestamp=_now_iso(),
            custom_type=custom_type,
            data=data,
        )
        return self._append_entry(entry)  # type: ignore[return-value]

    def append_custom_message(
        self, custom_type: str, content: str, *, display: bool = True
    ) -> CustomMessageEntry:
        entry = CustomMessageEntry(
            id=self._next_id(),
            parent_id=self.leaf_id,
            timestamp=_now_iso(),
            custom_type=custom_type,
            content=content,
            display=display,
        )
        return self._append_entry(entry)  # type: ignore[return-value]

    def append_session_info(self, name: str | None) -> SessionInfoEntry:
        cleaned = None if name is None else name.strip()[: self.MAX_NAME_LENGTH]
        entry = SessionInfoEntry(
            id=self._next_id(),
            parent_id=self.leaf_id,
            timestamp=_now_iso(),
            name=cleaned or None,
        )
        appended = self._append_entry(entry)
        self._name = cleaned or None
        return appended  # type: ignore[return-value]

    def append_label_change(
        self, target_id: str, label: str | None
    ) -> LabelEntry:
        if target_id not in self.by_id:
            raise KeyError(f"entry {target_id} not found")
        entry = LabelEntry(
            id=self._next_id(),
            parent_id=self.leaf_id,
            timestamp=_now_iso(),
            target_id=target_id,
            label=label or None,
        )
        appended = self._append_entry(entry)
        if label:
            self.labels_by_id[target_id] = label
            self.label_timestamps_by_id[target_id] = entry.timestamp
        else:
            self.labels_by_id.pop(target_id, None)
            self.label_timestamps_by_id.pop(target_id, None)
        return appended  # type: ignore[return-value]

    def _clone_entry_onto_leaf(
        self, entry: SessionEntry, *, id_map: dict[str, str] | None = None
    ) -> SessionEntry:
        """Append a fresh copy of ``entry`` as a child of the current leaf.

        ``id_map`` maps already-cloned source entry ids to their new ids; it is
        used to remap a ``CompactionEntry.first_kept_entry_id`` so the retained
        boundary still resolves in the new file (otherwise ``build_context``
        would drop the messages the compaction meant to keep).
        """

        if isinstance(entry, MessageEntry):
            return self.append_message(entry.message)
        if isinstance(entry, ModelChangeEntry):
            return self.append_model_change(entry.provider, entry.model_id)
        if isinstance(entry, ThinkingLevelChangeEntry):
            return self.append_thinking_level_change(entry.thinking_level)
        if isinstance(entry, CompactionEntry):
            first_kept = entry.first_kept_entry_id
            if id_map is not None:
                first_kept = id_map.get(first_kept, first_kept)
            return self.append_compaction(
                summary=entry.summary,
                first_kept_entry_id=first_kept,
                tokens_before=entry.tokens_before,
            )
        if isinstance(entry, BranchSummaryEntry):
            new_entry = BranchSummaryEntry(
                id=self._next_id(),
                parent_id=self.leaf_id,
                timestamp=_now_iso(),
                from_id=entry.from_id,
                summary=entry.summary,
            )
            return self._append_entry(new_entry)
        if isinstance(entry, SessionInfoEntry):
            return self.append_session_info(entry.name)
        if isinstance(entry, CustomEntry):
            return self.append_custom(entry.custom_type, entry.data)
        if isinstance(entry, CustomMessageEntry):
            return self.append_custom_message(
                entry.custom_type, entry.content, display=entry.display
            )
        raise TypeError(f"cannot clone entry: {type(entry)!r}")

    # -- navigation ---------------------------------------------------------

    def branch(self, branch_from_id: str) -> None:
        if branch_from_id not in self.by_id:
            raise KeyError(f"entry {branch_from_id} not found")
        self.leaf_id = branch_from_id

    def reset_leaf(self) -> None:
        self.leaf_id = None

    def set_leaf(self, leaf_id: str | None) -> None:
        if leaf_id is not None and leaf_id not in self.by_id:
            raise KeyError(f"entry {leaf_id} not found")
        self.leaf_id = leaf_id

    def branch_with_summary(
        self, branch_from_id: str | None, summary: str
    ) -> BranchSummaryEntry:
        if branch_from_id is not None and branch_from_id not in self.by_id:
            raise KeyError(f"entry {branch_from_id} not found")
        self.leaf_id = branch_from_id
        entry = BranchSummaryEntry(
            id=self._next_id(),
            parent_id=branch_from_id,
            timestamp=_now_iso(),
            from_id=branch_from_id or "root",
            summary=summary,
        )
        return self._append_entry(entry)  # type: ignore[return-value]

    # -- queries ------------------------------------------------------------

    def get_leaf_id(self) -> str | None:
        return self.leaf_id

    def get_leaf_entry(self) -> SessionEntry | None:
        return self.by_id.get(self.leaf_id) if self.leaf_id else None

    def get_entry(self, entry_id: str) -> SessionEntry | None:
        return self.by_id.get(entry_id)

    def get_entries(self) -> list[SessionEntry]:
        return list(self.entries)

    def get_header(self) -> SessionHeader:
        return self.header

    def get_children(self, parent_id: str | None) -> list[SessionEntry]:
        return [e for e in self.entries if e.parent_id == parent_id]

    def get_label(self, entry_id: str) -> str | None:
        return self.labels_by_id.get(entry_id)

    def get_label_timestamp(self, entry_id: str) -> str | None:
        return self.label_timestamps_by_id.get(entry_id)

    @property
    def name(self) -> str | None:
        return self._name

    @property
    def session_id(self) -> str:
        return self.header.id

    def get_branch(self, from_id: str | None = None) -> list[SessionEntry]:
        path: list[SessionEntry] = []
        start_id = from_id if from_id is not None else self.leaf_id
        current = self.by_id.get(start_id) if start_id else None
        while current is not None:
            path.insert(0, current)
            current = (
                self.by_id.get(current.parent_id) if current.parent_id else None
            )
        return path

    def build_context(self) -> SessionContext:
        return build_context(self.entries, self.leaf_id, self.by_id)

    def get_tree(self) -> list[SessionTreeNode]:
        node_map: dict[str, SessionTreeNode] = {}
        roots: list[SessionTreeNode] = []
        for entry in self.entries:
            node_map[entry.id] = SessionTreeNode(
                entry=entry,
                label=self.labels_by_id.get(entry.id),
                label_timestamp=self.label_timestamps_by_id.get(entry.id),
            )
        for entry in self.entries:
            node = node_map[entry.id]
            if entry.parent_id is None or entry.parent_id == entry.id:
                roots.append(node)
            else:
                parent = node_map.get(entry.parent_id)
                if parent is not None:
                    parent.children.append(node)
                else:
                    roots.append(node)
        # Children are already in append (timestamp) order because entries are
        # appended in order; no re-sort needed.
        return roots


def most_recent_session_file(session_dir: Path) -> Path | None:
    directory = Path(session_dir).expanduser()
    if not directory.is_dir():
        return None
    candidates = sorted(
        (p for p in directory.glob("*.jsonl") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def list_session_files(session_dir: Path) -> list[Path]:
    directory = Path(session_dir).expanduser()
    if not directory.is_dir():
        return []
    return sorted(
        (p for p in directory.glob("*.jsonl") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

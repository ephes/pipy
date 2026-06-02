"""Pure helpers for the native session-tree product commands.

These back ``/tree``, ``/session``, ``/resume``, ``/fork``, and ``/clone`` with
loop- and TTY-independent logic: Pi ``/tree`` selection semantics, tree
rendering, filter modes, entry-reference resolution, and safe status
formatting. The REPL loop and the interactive TUI selector both call into here
so captured-stream and live-TTY paths share identical behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.session_tree import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomMessageEntry,
    MessageEntry,
    ModelChangeEntry,
    NativeSessionTree,
    SessionEntry,
    SessionInfoEntry,
    ThinkingLevelChangeEntry,
    default_native_session_dir,
    list_session_files,
)
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)

FILTER_MODES = ("default", "no-tools", "user-only", "labeled-only", "all")


# ---------------------------------------------------------------------------
# Selection semantics
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TreeSelectionResult:
    """Outcome of selecting an entry in ``/tree``.

    ``editor_text`` is the text to put back into the editor (user/custom-message
    selection) or ``None`` (non-user selection leaves the editor empty).
    ``is_user_selection`` distinguishes the two branches. ``is_noop`` is true
    when the selected entry is already the current leaf.
    """

    editor_text: str | None
    is_user_selection: bool
    is_noop: bool


def _is_user_message_entry(entry: SessionEntry) -> bool:
    return isinstance(entry, MessageEntry) and isinstance(
        entry.message, UserMessage
    )


def apply_tree_selection(
    tree: NativeSessionTree, target_id: str
) -> TreeSelectionResult:
    """Apply Pi ``/tree`` selection semantics to ``tree`` in place.

    Selecting the current leaf is a no-op. Selecting a user message (or a
    custom message with text) sets the leaf to that entry's parent and returns
    the text for the editor, so submitting creates an alternative branch.
    Selecting any other entry sets the leaf to that entry with an empty editor,
    so the next prompt continues from that point.
    """

    entry = tree.get_entry(target_id)
    if entry is None:
        raise KeyError(f"entry {target_id} not found")

    if entry.id == tree.get_leaf_id():
        return TreeSelectionResult(
            editor_text=None, is_user_selection=False, is_noop=True
        )

    if _is_user_message_entry(entry):
        assert isinstance(entry, MessageEntry)
        assert isinstance(entry.message, UserMessage)
        tree.set_leaf(entry.parent_id)
        return TreeSelectionResult(
            editor_text=entry.message.content,
            is_user_selection=True,
            is_noop=False,
        )

    if isinstance(entry, CustomMessageEntry) and entry.content:
        tree.set_leaf(entry.parent_id)
        return TreeSelectionResult(
            editor_text=entry.content, is_user_selection=True, is_noop=False
        )

    tree.set_leaf(entry.id)
    return TreeSelectionResult(
        editor_text=None, is_user_selection=False, is_noop=False
    )


def branch_summary_attach_parent(
    tree: NativeSessionTree, target_id: str
) -> str | None:
    """Where a branch summary attaches when selecting ``target_id``.

    For a user/custom-message selection the summary attaches to that entry's
    parent (the selected text goes back into the editor). For any other entry
    it attaches to the selected entry. For a root user message it attaches at
    the root (``None``).
    """

    entry = tree.get_entry(target_id)
    if entry is None:
        raise KeyError(f"entry {target_id} not found")
    if _is_user_message_entry(entry) or (
        isinstance(entry, CustomMessageEntry) and bool(entry.content)
    ):
        return entry.parent_id
    return entry.id


def abandoned_branch_messages(
    tree: NativeSessionTree, old_leaf_id: str | None, attach_parent_id: str | None
):  # noqa: ANN201 - returns list[LoopMessage]
    """Messages on the old branch that are not retained on the new branch.

    Collects the abandoned path from the old leaf back to the common ancestor
    with the target attachment point, returning their provider-visible
    messages (oldest first).
    """

    if old_leaf_id is None:
        return []
    # attach_parent_id is None means the summary attaches at the root, so the
    # retained branch is empty. (get_branch(None) would otherwise fall back to
    # the current leaf.)
    keep_ids = (
        set()
        if attach_parent_id is None
        else {e.id for e in tree.get_branch(attach_parent_id)}
    )
    abandoned_entries = [
        e for e in tree.get_branch(old_leaf_id) if e.id not in keep_ids
    ]
    messages = []
    for entry in abandoned_entries:
        if isinstance(entry, MessageEntry):
            messages.append(entry.message)
        elif isinstance(entry, CustomMessageEntry) and entry.content:
            from pipy_harness.native.tools.messages import UserMessage as _U

            messages.append(_U(content=entry.content))
    return messages


# ---------------------------------------------------------------------------
# Filtering + traversal
# ---------------------------------------------------------------------------


def _dfs_entries(tree: NativeSessionTree) -> list[tuple[SessionEntry, int]]:
    """Return (entry, depth) pairs in tree pre-order."""

    result: list[tuple[SessionEntry, int]] = []

    def walk(node, depth: int) -> None:  # noqa: ANN001 - SessionTreeNode
        result.append((node.entry, depth))
        for child in node.children:
            walk(child, depth + 1)

    for root in tree.get_tree():
        walk(root, 0)
    return result


def _entry_passes_filter(
    tree: NativeSessionTree, entry: SessionEntry, filter_mode: str
) -> bool:
    if filter_mode == "all":
        return True
    if filter_mode == "labeled-only":
        return tree.get_label(entry.id) is not None
    if filter_mode == "user-only":
        return _is_user_message_entry(entry) or (
            isinstance(entry, CustomMessageEntry) and bool(entry.content)
        )
    # default + no-tools: show conversation-relevant entries, hide bookkeeping.
    if isinstance(
        entry,
        (ModelChangeEntry, ThinkingLevelChangeEntry, SessionInfoEntry),
    ):
        return False
    if isinstance(entry, MessageEntry) and isinstance(
        entry.message, ToolResultMessage
    ):
        return filter_mode != "no-tools"
    return True


def visible_tree_entries(
    tree: NativeSessionTree, *, filter_mode: str = "default"
) -> list[SessionEntry]:
    return [
        entry
        for entry, _depth in _dfs_entries(tree)
        if _entry_passes_filter(tree, entry, filter_mode)
    ]


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------


def resolve_entry_ref(
    tree: NativeSessionTree, ref: str, *, filter_mode: str = "default"
) -> SessionEntry | None:
    """Resolve a 1-based visible index or an entry-id (prefix) to an entry."""

    ref = ref.strip()
    if not ref:
        return None
    # A small in-range number is a 1-based visible index; larger all-digit
    # strings (or ones that fall outside the range) are treated as id prefixes,
    # since entry ids are uuid slices that can be all digits.
    if ref.isdigit():
        visible = visible_tree_entries(tree, filter_mode=filter_mode)
        value = int(ref)
        if 1 <= value <= len(visible):
            return visible[value - 1]
    entry = tree.get_entry(ref)
    if entry is not None:
        return entry
    matches = [e for e in tree.get_entries() if e.id.startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def entry_preview(tree: NativeSessionTree, entry: SessionEntry) -> str:
    """Public alias for a one-line display preview of a tree entry."""

    return _entry_preview(tree, entry)


def _entry_preview(tree: NativeSessionTree, entry: SessionEntry) -> str:
    label = tree.get_label(entry.id)
    label_prefix = f"[{label}] " if label else ""
    if isinstance(entry, MessageEntry):
        message = entry.message
        if isinstance(message, UserMessage):
            return f"{label_prefix}user: {_truncate(message.content)}"
        if isinstance(message, AssistantMessage):
            text = message.content or "(tool call)"
            return f"{label_prefix}assistant: {_truncate(text)}"
        if isinstance(message, ToolResultMessage):
            return f"{label_prefix}tool: {_truncate(message.output_text)}"
    if isinstance(entry, BranchSummaryEntry):
        return f"{label_prefix}branch-summary: {_truncate(entry.summary)}"
    if isinstance(entry, CompactionEntry):
        return f"{label_prefix}compaction: {_truncate(entry.summary)}"
    if isinstance(entry, CustomMessageEntry):
        return f"{label_prefix}custom: {_truncate(entry.content)}"
    if isinstance(entry, ModelChangeEntry):
        return f"{label_prefix}model: {entry.provider}/{entry.model_id}"
    if isinstance(entry, SessionInfoEntry):
        return f"{label_prefix}name: {entry.name}"
    return f"{label_prefix}{entry.type}"


def _truncate(text: str, limit: int = 60) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def render_tree_lines(
    tree: NativeSessionTree,
    *,
    filter_mode: str = "default",
    selected_id: str | None = None,
) -> list[str]:
    """Render the session tree as ASCII lines.

    Entries on the active leaf path are marked with ``*``; the selected entry
    (when given) is marked with ``>``. Index numbers match
    :func:`visible_tree_entries` for the same filter mode.
    """

    active_path = {e.id for e in tree.get_branch()}
    visible_ids = {
        e.id for e in visible_tree_entries(tree, filter_mode=filter_mode)
    }
    lines: list[str] = []
    index = 0
    for entry, depth in _dfs_entries(tree):
        if entry.id not in visible_ids:
            continue
        index += 1
        active_marker = "*" if entry.id in active_path else " "
        select_marker = ">" if entry.id == selected_id else " "
        indent = "  " * depth
        lines.append(
            f"{select_marker}{active_marker} {index:>3}. {entry.id[:8]} "
            f"{indent}{_entry_preview(tree, entry)}"
        )
    if not lines:
        return ["(empty session tree)"]
    return lines


# ---------------------------------------------------------------------------
# Status formatting
# ---------------------------------------------------------------------------


def format_session_status(tree: NativeSessionTree) -> str:
    header = tree.get_header()
    leaf = tree.get_leaf_id() or "(root)"
    message_count = sum(
        1 for e in tree.get_entries() if isinstance(e, MessageEntry)
    )
    branch_count = _branch_count(tree)
    path_label = str(tree.path) if tree.path is not None else "(ephemeral)"
    name = tree.name or "(unnamed)"
    return (
        "pipy native session: "
        f"name={name} id={header.id[:8]} leaf={leaf[:8]} "
        f"messages={message_count} branches={branch_count} file={path_label}"
    )


def _branch_count(tree: NativeSessionTree) -> int:
    """Number of leaf nodes (entries with no children) in the tree."""

    parent_ids = {
        e.parent_id for e in tree.get_entries() if e.parent_id is not None
    }
    leaves = [e for e in tree.get_entries() if e.id not in parent_ids]
    return max(1, len(leaves))


# ---------------------------------------------------------------------------
# Resume listing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionListEntry:
    path: Path
    session_id: str
    name: str | None
    message_count: int


def resolve_session_target(session_dir: Path, ref: str) -> Path | None:
    """Resolve a path, id-prefix, or 1-based index to a native session file."""

    ref = ref.strip()
    if not ref:
        return None
    candidate = Path(ref).expanduser()
    if candidate.is_file():
        return candidate
    sessions = list_native_sessions(session_dir)
    # A small in-range number is a 1-based index; otherwise treat the digits as
    # an id prefix (session ids are uuids that can be all digits).
    if ref.isdigit():
        value = int(ref)
        if 1 <= value <= len(sessions):
            return sessions[value - 1].path
    matches = [
        entry
        for entry in sessions
        if entry.session_id.startswith(ref) or entry.path.stem.startswith(ref)
    ]
    if len(matches) == 1:
        return matches[0].path
    return None


def resolve_startup_session(
    cwd: Path,
    *,
    mode: str = "new",
    target: str | None = None,
    state_root: Path | None = None,
) -> NativeSessionTree | None:
    """Resolve Pi-style startup session flags to a native session tree.

    Modes:

    - ``none``: ephemeral — no native session (returns ``None``).
    - ``new``: create a fresh native product session.
    - ``continue``/``resume``: reopen the most recent session (Pi ``-c``/``-r``),
      creating a fresh one when none exists.
    - ``session``: open a specific session file or partial id (Pi ``--session``).
    - ``fork``: fork a session file or partial id into a new session
      (Pi ``--fork``).
    """

    if mode == "none":
        return None
    if mode in ("continue", "resume"):
        existing = NativeSessionTree.continue_recent(cwd, state_root=state_root)
        if existing is not None:
            return existing
        return NativeSessionTree.create(cwd, state_root=state_root)
    session_dir = default_native_session_dir(cwd.expanduser().resolve(), state_root=state_root)
    if mode == "session":
        if not target:
            raise ValueError("--session requires a path or id")
        path = resolve_session_target(session_dir, target)
        if path is None:
            raise ValueError(f"no native session matched {target!r}")
        return NativeSessionTree.open(path)
    if mode == "fork":
        if not target:
            raise ValueError("--fork requires a path or id")
        path = resolve_session_target(session_dir, target)
        if path is None:
            raise ValueError(f"no native session matched {target!r}")
        return NativeSessionTree.fork_from(path, cwd, state_root=state_root)
    return NativeSessionTree.create(cwd, state_root=state_root)


def delete_native_session(path: Path) -> tuple[bool, str]:
    """Delete a native session file, preferring the ``trash`` CLI when present.

    Matches Pi's safety posture: use ``trash`` if available, otherwise remove
    the file directly (callers gate this behind explicit confirmation). Only the
    native session file is affected; ``pipy-session`` archive records are never
    touched.
    """

    import shutil
    import subprocess

    path = Path(path).expanduser()
    if not path.is_file():
        return False, f"no native session file at {path}"
    trash = shutil.which("trash")
    if trash is not None:
        try:
            subprocess.run([trash, str(path)], check=True, capture_output=True)
            return True, f"moved native session {path.name} to trash"
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        path.unlink()
    except OSError as exc:
        return False, f"could not delete {path.name}: {exc}"
    return True, f"deleted native session {path.name}"


def list_native_sessions(session_dir: Path) -> list[SessionListEntry]:
    entries: list[SessionListEntry] = []
    for path in list_session_files(session_dir):
        try:
            tree = NativeSessionTree.open(path, persist=False)
        except ValueError:
            continue
        message_count = sum(
            1 for e in tree.get_entries() if isinstance(e, MessageEntry)
        )
        entries.append(
            SessionListEntry(
                path=path,
                session_id=tree.session_id,
                name=tree.name,
                message_count=message_count,
            )
        )
    return entries

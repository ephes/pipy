"""Pure helpers for the native session-tree product commands.

These back ``/tree``, ``/session``, ``/resume``, ``/fork``, and ``/clone`` with
loop- and TTY-independent logic: Pi ``/tree`` selection semantics, tree
rendering, filter modes, entry-reference resolution, and safe status
formatting. The REPL loop and the interactive TUI selector both call into here
so captured-stream and live-TTY paths share identical behavior.
"""

from __future__ import annotations

from collections.abc import Callable
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
    list_session_dirs,
    list_session_files,
    native_sessions_root,
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
    # name and the workspace-derived path are user-controlled; sanitize them so
    # the status line cannot inject terminal escape sequences.
    path_label = (
        sanitize_label_text(str(tree.path))
        if tree.path is not None
        else "(ephemeral)"
    )
    name = sanitize_label_text(tree.name) if tree.name else "(unnamed)"
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
    cwd: str = ""
    mtime: float = 0.0


@dataclass(frozen=True, slots=True)
class SessionRefResult:
    """Outcome of resolving a ``--session``/``--fork`` reference (Pi shape).

    ``kind`` is one of ``path`` (an explicit file path), ``local`` (matched a
    session in the current project), ``global`` (matched a session in a
    *different* project — the cross-project fork-prompt case), or ``not_found``.
    ``path`` is the resolved native session file (``None`` only for
    ``not_found``). ``cwd`` is the matched session's workspace (set for
    ``global``). ``arg`` echoes the original reference for diagnostics.
    """

    kind: str
    arg: str
    path: Path | None = None
    cwd: str | None = None


def _looks_like_path(ref: str) -> bool:
    return "/" in ref or "\\" in ref or ref.endswith(".jsonl")


def _match_by_id(
    entries: list[SessionListEntry], ref: str
) -> SessionListEntry | None:
    """Exact id match first, then a *unique* id-prefix match.

    An exact id always wins. Otherwise a prefix is accepted only when it
    resolves to exactly one session; an ambiguous prefix returns ``None`` (so
    the caller reports "no session matched" rather than silently opening or
    forking the wrong session).
    """

    for entry in entries:
        if entry.session_id == ref:
            return entry
    prefix_matches = [e for e in entries if e.session_id.startswith(ref)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    return None


def resolve_session_ref(
    cwd: Path,
    ref: str,
    *,
    state_root: Path | None = None,
    sessions_root: Path | None = None,
) -> SessionRefResult:
    """Resolve a session reference local-first, then global (cross-project).

    Mirrors Pi's ``resolveSessionPath``: an explicit path short-circuits;
    otherwise the current project is searched (exact id then id-prefix), then
    every project under the sessions root. A match outside the current project
    is returned as ``global`` so the caller can prompt to fork.
    """

    ref = ref.strip()
    if not ref:
        return SessionRefResult(kind="not_found", arg=ref)
    resolved_cwd = cwd.expanduser().resolve()
    if _looks_like_path(ref):
        candidate = Path(ref).expanduser()
        # A relative path is resolved against the CLI workspace ``cwd`` (Pi's
        # ``resolvePath(sessionArg, cwd)``), not the process cwd. Only an
        # existing file resolves to a path; a missing file becomes
        # ``not_found`` so the CLI reports a clean error instead of raising an
        # uncaught file error when it is later opened/forked.
        if not candidate.is_absolute():
            candidate = resolved_cwd / candidate
        if candidate.is_file():
            return SessionRefResult(kind="path", arg=ref, path=candidate)
        return SessionRefResult(kind="not_found", arg=ref)
    root = native_sessions_root(state_root=state_root, session_dir=sessions_root)
    local_dir = root / _encode_cwd(resolved_cwd)
    local = _match_by_id(list_native_sessions(local_dir), ref)
    if local is not None:
        return SessionRefResult(kind="local", arg=ref, path=local.path)
    global_match = _match_by_id(list_all_native_sessions(root), ref)
    if global_match is not None:
        return SessionRefResult(
            kind="global",
            arg=ref,
            path=global_match.path,
            cwd=global_match.cwd or None,
        )
    return SessionRefResult(kind="not_found", arg=ref)


def _encode_cwd(cwd: Path) -> str:
    from pipy_harness.native.session_tree import encode_cwd_dir_name

    return encode_cwd_dir_name(cwd)


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


class StartupSessionAborted(Exception):
    """Raised when the user declines a cross-project ``--session`` fork prompt."""

    def __init__(self, other_cwd: str = "") -> None:
        super().__init__(other_cwd)
        self.other_cwd = other_cwd


def resolve_startup_session(
    cwd: Path,
    *,
    mode: str = "new",
    target: str | None = None,
    name: str | None = None,
    session_id: str | None = None,
    state_root: Path | None = None,
    sessions_root: Path | None = None,
    confirm_fork: Callable[[SessionRefResult], bool] | None = None,
) -> NativeSessionTree | None:
    """Resolve Pi-style startup session flags to a native session tree.

    Modes:

    - ``none``: ephemeral — no native session (returns ``None``).
    - ``new``: create a fresh native product session.
    - ``continue``/``resume``: reopen the most recent session (Pi ``-c``/``-r``),
      creating a fresh one when none exists.
    - ``session``: open a specific session file or partial id (Pi ``--session``).
      A partial id that resolves only in a *different* project returns the
      cross-project case: ``confirm_fork(result)`` decides whether to fork it
      into ``cwd`` (Pi prompts) and a falsey decision raises
      :class:`StartupSessionAborted`.
    - ``session-id``: open the local session with this exact id, or create a
      fresh session carrying it (Pi ``--session-id``).
    - ``fork``: fork a session file or partial id into a new session
      (Pi ``--fork``); ``session_id`` names the new file when given.

    ``name`` (Pi ``--name``/``-n``) is appended as a session name after the
    session is created/opened/forked, for every non-ephemeral mode.
    """

    resolved_cwd = cwd.expanduser().resolve()
    proj_dir = default_native_session_dir(
        resolved_cwd, state_root=state_root, sessions_root=sessions_root
    )
    tree = _resolve_startup_tree(
        resolved_cwd,
        proj_dir,
        mode=mode,
        target=target,
        session_id=session_id,
        state_root=state_root,
        sessions_root=sessions_root,
        confirm_fork=confirm_fork,
    )
    if tree is not None and name:
        cleaned = name.strip()
        if cleaned:
            tree.append_session_info(cleaned)
    return tree


def _resolve_startup_tree(
    resolved_cwd: Path,
    proj_dir: Path,
    *,
    mode: str,
    target: str | None,
    session_id: str | None,
    state_root: Path | None,
    sessions_root: Path | None,
    confirm_fork: Callable[[SessionRefResult], bool] | None,
) -> NativeSessionTree | None:
    if mode == "none":
        return None
    if mode in ("continue", "resume"):
        existing = NativeSessionTree.continue_recent(
            resolved_cwd, session_dir=proj_dir
        )
        if existing is not None:
            return existing
        return NativeSessionTree.create(resolved_cwd, session_dir=proj_dir)
    if mode == "session-id":
        if not target:
            raise ValueError("--session-id requires an id")
        existing_entry = next(
            (
                entry
                for entry in list_native_sessions(proj_dir)
                if entry.session_id == target
            ),
            None,
        )
        if existing_entry is not None:
            return NativeSessionTree.open(existing_entry.path)
        return NativeSessionTree.create(
            resolved_cwd, session_dir=proj_dir, session_id=target
        )
    if mode == "session":
        if not target:
            raise ValueError("--session requires a path or id")
        ref = resolve_session_ref(
            resolved_cwd, target, state_root=state_root, sessions_root=sessions_root
        )
        if ref.kind == "not_found" or ref.path is None:
            raise ValueError(f"no native session matched {target!r}")
        if ref.kind == "global":
            if confirm_fork is not None and not confirm_fork(ref):
                raise StartupSessionAborted(ref.cwd or "")
            return NativeSessionTree.fork_from(
                ref.path, resolved_cwd, session_dir=proj_dir, session_id=session_id
            )
        return NativeSessionTree.open(ref.path)
    if mode == "fork":
        if not target:
            raise ValueError("--fork requires a path or id")
        # --fork --session-id names the new file; reject an id that already
        # exists locally rather than writing a duplicate-id session (Pi
        # main.ts findLocalSessionByExactId guard). (--session is mutually
        # exclusive with --session-id, so only fork mode can carry one here.)
        if session_id is not None and any(
            e.session_id == session_id for e in list_native_sessions(proj_dir)
        ):
            raise ValueError(
                f"a native session already exists with id {session_id!r}"
            )
        ref = resolve_session_ref(
            resolved_cwd, target, state_root=state_root, sessions_root=sessions_root
        )
        if ref.kind == "not_found" or ref.path is None:
            raise ValueError(f"no native session matched {target!r}")
        return NativeSessionTree.fork_from(
            ref.path, resolved_cwd, session_dir=proj_dir, session_id=session_id
        )
    return NativeSessionTree.create(resolved_cwd, session_dir=proj_dir)


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
        entry = _read_session_list_entry(path)
        if entry is not None:
            entries.append(entry)
    return entries


def list_all_native_sessions(sessions_root: Path) -> list[SessionListEntry]:
    """List native sessions across every project under a sessions root.

    Used by the cross-project ``--session``/``--fork`` lookup and the
    all-sessions scope of the interactive picker (Pi ``SessionManager.listAll``).
    Entries are sorted newest-first by file mtime.
    """

    entries: list[SessionListEntry] = []
    for project_dir in list_session_dirs(sessions_root):
        for path in list_session_files(project_dir):
            entry = _read_session_list_entry(path)
            if entry is not None:
                entries.append(entry)
    entries.sort(key=lambda e: e.mtime, reverse=True)
    return entries


def _read_session_list_entry(path: Path) -> SessionListEntry | None:
    try:
        tree = NativeSessionTree.open(path, persist=False)
    except ValueError:
        return None
    message_count = sum(
        1 for e in tree.get_entries() if isinstance(e, MessageEntry)
    )
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return SessionListEntry(
        path=path,
        session_id=tree.session_id,
        name=tree.name,
        message_count=message_count,
        cwd=tree.get_header().cwd,
        mtime=mtime,
    )


# ---------------------------------------------------------------------------
# Interactive session picker (the /resume + -r overlay, Pi session-selector)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionPickerRow:
    """One visible row in the interactive session picker.

    ``is_current`` marks the session currently open in this REPL; ``is_selected``
    is reserved for the highlighted row (set by the TUI, not this builder).
    """

    session_id: str
    path: Path
    name: str | None
    cwd: str
    message_count: int
    mtime: float
    is_current: bool = False


def sanitize_label_text(text: str) -> str:
    """Strip control characters from user-controlled label text.

    Session names and workspace paths are user-controlled and reach the
    terminal as picker labels. Removing C0/C1 control bytes (including ESC)
    prevents terminal escape-sequence injection while keeping ordinary text.
    """

    cleaned = []
    for ch in text:
        code = ord(ch)
        if code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F:
            cleaned.append(" ")
        else:
            cleaned.append(ch)
    return "".join(cleaned)


def format_relative_age(mtime: float, now: float) -> str:
    """Format a compact relative age (Pi ``formatSessionDate`` shape)."""

    delta = max(0.0, now - mtime)
    minute = 60.0
    hour = 60 * minute
    day = 24 * hour
    week = 7 * day
    month = 30 * day
    year = 365 * day
    if delta < minute:
        return "now"
    if delta < hour:
        return f"{int(delta // minute)}m"
    if delta < day:
        return f"{int(delta // hour)}h"
    if delta < week:
        return f"{int(delta // day)}d"
    if delta < month:
        return f"{int(delta // week)}w"
    if delta < year:
        return f"{int(delta // month)}mo"
    return f"{int(delta // year)}y"


def build_session_picker_rows(
    project_sessions: list[SessionListEntry],
    all_sessions: list[SessionListEntry],
    *,
    scope: str = "current",
    query: str = "",
    sort: str = "recent",
    named_only: bool = False,
    current_path: Path | None = None,
) -> list[SessionPickerRow]:
    """Build the filtered/sorted picker rows (pure; shared by all picker paths).

    ``scope`` chooses the current-project list or the all-projects list (Pi
    ``Tab``). ``named_only`` keeps only named sessions (Pi ``Ctrl+N``). ``query``
    is a case-insensitive substring match over name, id, and workspace path.
    ``sort`` is ``recent`` (mtime, newest first) or ``name`` (alphabetical,
    unnamed sessions last) (Pi ``Ctrl+S``). The session at ``current_path`` is
    marked ``is_current``.
    """

    source = all_sessions if scope == "all" else project_sessions
    needle = query.strip().lower()
    resolved_current = (
        current_path.expanduser().resolve() if current_path is not None else None
    )

    rows: list[SessionPickerRow] = []
    for entry in source:
        if named_only and not entry.name:
            continue
        if needle:
            haystack = " ".join(
                [entry.name or "", entry.session_id, entry.cwd]
            ).lower()
            if needle not in haystack:
                continue
        is_current = (
            resolved_current is not None
            and entry.path.expanduser().resolve() == resolved_current
        )
        rows.append(
            SessionPickerRow(
                session_id=entry.session_id,
                path=entry.path,
                name=entry.name,
                cwd=entry.cwd,
                message_count=entry.message_count,
                mtime=entry.mtime,
                is_current=is_current,
            )
        )

    if sort == "name":
        rows.sort(
            key=lambda r: (r.name is None, (r.name or "").lower(), -r.mtime)
        )
    else:
        rows.sort(key=lambda r: r.mtime, reverse=True)
    return rows


def format_session_picker_label(
    row: SessionPickerRow,
    *,
    show_path: bool = False,
    show_cwd: bool = False,
    now: float = 0.0,
) -> str:
    """Render a single picker row to a safe, single-line label.

    Includes the (sanitized) name or ``(unnamed)``, a short id, message count,
    and relative age; optionally the session file path (Pi ``Ctrl+P``) and the
    workspace path (shown in all-projects scope). All user-controlled text is
    sanitized against terminal escape injection.
    """

    name = sanitize_label_text(row.name) if row.name else "(unnamed)"
    marker = "● " if row.is_current else ""
    parts = [
        f"{marker}{name}",
        # Session ids are user-controlled via --session-id, so sanitize them too.
        sanitize_label_text(row.session_id[:8]),
        f"msgs={row.message_count}",
    ]
    if now:
        parts.append(format_relative_age(row.mtime, now))
    if show_cwd and row.cwd:
        parts.append(sanitize_label_text(row.cwd))
    if show_path:
        parts.append(sanitize_label_text(str(row.path)))
    return "  ".join(parts)

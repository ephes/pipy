"""Pipy-owned editor completion core (Pi parity, stdlib only).

This module is the pure-logic foundation behind the product TUI's ``@`` file
picker and Tab path completion. It mirrors the user-visible behavior of Pi's
``CombinedAutocompleteProvider``
(``pi-mono/packages/tui/src/autocomplete.ts``) through pipy-owned Python — it
is **not** a port of that TypeScript and does not require ``fd``.

Two surfaces are provided:

- The ``@`` file picker: :func:`extract_at_token` finds an ``@``-prefixed token
  at the cursor, and :func:`at_candidates` walks the workspace (bounded, stdlib
  ``os.walk``) and ranks entries with :func:`score_entry` — an exact / prefix /
  substring scorer, **not** a fuzzy subsequence matcher (so ``@srctuiconfig``
  does not match ``src/tui/config.py``). The walk is workspace-scoped, applies
  the existing ``.git``/ignored default-deny and symlink-containment policy,
  and reads no file contents.
- Tab path completion: :func:`extract_path_prefix` reproduces Pi's path-like
  natural trigger (token contains ``/`` or starts with ``.`` / ``~/``) plus the
  forced-Tab fallback, and :func:`path_candidates` lists a single directory's
  entries with a case-insensitive ``startswith`` filter, directories-first then
  alphabetical ordering, ``~/`` expansion, space-quoting, and trailing-slash
  rules.

Both surfaces return :class:`CompletionItem` values: ``value`` is the text the
editor inserts (``@``-prefixed and/or quoted as Pi would) and ``label`` is the
short display name shown in the popup row.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pipy_harness.native.read_only_tool import (
    _is_ignored_or_generated,
    _is_relative_to,
)

# Bound the workspace walk so a huge tree cannot stall the editor or grow
# memory: stop scanning directories past a depth and after a total entry cap.
_WALK_MAX_DEPTH = 8
_WALK_MAX_ENTRIES = 5000
# Default number of ranked candidates returned to the popup (Pi: top 20).
_AT_RESULT_LIMIT = 20
# Default number of path-completion entries listed for a directory.
_PATH_RESULT_LIMIT = 50
# Directory names never walked/listed regardless of ignore policy.
_HARD_DENY_DIR_NAMES = frozenset({".git"})


@dataclass(frozen=True, slots=True)
class CompletionItem:
    """One completion row.

    ``value`` is the literal text inserted into the editor when the row is
    accepted (already ``@``-prefixed and/or double-quoted as needed, with a
    trailing ``/`` for directories). ``label`` is the short display name for
    the popup row (basename, with a trailing ``/`` for directories).
    """

    value: str
    label: str


def score_entry(file_path: str, query: str, *, is_directory: bool) -> int:
    """Score ``file_path`` against ``query`` (higher is a better match).

    Mirrors Pi's ``scoreEntry``: a case-insensitive exact filename match scores
    100, a filename ``startswith`` the query 80, a substring within the
    filename 50, a substring within the full path 30, and a matched directory
    earns a +10 bonus. A query that does not appear as an ordered substring of
    the filename or path scores 0 (exact/prefix/substring, never fuzzy).
    """

    file_name = PurePosixPath(file_path).name
    lower_name = file_name.lower()
    lower_query = query.lower()

    score = 0
    if lower_name == lower_query:
        score = 100
    elif lower_name.startswith(lower_query):
        score = 80
    elif lower_query in lower_name:
        score = 50
    elif lower_query in file_path.lower():
        score = 30

    if is_directory and score > 0:
        score += 10
    return score


def extract_at_token(text_before_cursor: str) -> tuple[int, str] | None:
    """Return ``(token_start_index, query)`` for an ``@`` token at the cursor.

    The token is the current whitespace-delimited word ending at the cursor.
    When it begins with ``@`` (optionally ``@"`` to allow embedded spaces), the
    leading ``@``/``@"`` is stripped and the remaining text returned as the
    query, along with the index of the ``@`` in ``text_before_cursor``. Returns
    ``None`` when there is no ``@`` token at the cursor (e.g. ordinary prose, a
    completed token followed by a space, or a ``/`` slash command).
    """

    # Quoted form: the last unmatched @" begins a token that may contain spaces.
    quoted_index = text_before_cursor.rfind('@"')
    if quoted_index != -1 and '"' not in text_before_cursor[quoted_index + 2 :]:
        return quoted_index, text_before_cursor[quoted_index + 2 :]

    boundary = max(text_before_cursor.rfind(" "), text_before_cursor.rfind("\n"))
    token = text_before_cursor[boundary + 1 :]
    if not token.startswith("@"):
        return None
    return boundary + 1, token[1:]


def at_candidates(
    workspace: Path, query: str, *, limit: int = _AT_RESULT_LIMIT
) -> list[CompletionItem]:
    """Rank workspace files/dirs for an ``@`` query (Pi scoreEntry semantics).

    ``query`` may itself contain a ``/`` to scope the search to a subdirectory
    (e.g. ``src/co`` ranks entries under ``src`` by the leaf ``co``), mirroring
    Pi's scoped fuzzy query. The walk is workspace-bounded, applies the
    ``.git``/ignored default-deny and symlink-containment policy, reads no file
    contents, and returns the top ``limit`` candidates by descending score
    (ties broken by path). Each ``value`` is ``@``-prefixed (quoted when it
    contains a space); directories keep a trailing ``/``.
    """

    try:
        workspace_root = workspace.expanduser().resolve()
    except OSError:
        return []
    if not workspace_root.is_dir():
        return []

    base_rel, leaf_query = _split_scoped_query(query)
    base_dir = workspace_root
    if base_rel:
        candidate = (workspace_root / base_rel).resolve()
        if not _is_relative_to(candidate, workspace_root) or not candidate.is_dir():
            return []
        base_dir = candidate

    scored: list[tuple[int, str, CompletionItem]] = []
    for relative_label, is_dir in _walk_workspace(base_dir, workspace_root):
        score = score_entry(relative_label, leaf_query, is_directory=is_dir)
        if score <= 0 and leaf_query != "":
            continue
        if leaf_query == "":
            score = 1 + (10 if is_dir else 0)
        item = _build_at_item(relative_label, is_dir)
        scored.append((score, relative_label, item))

    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [item for _score, _label, item in scored[: max(0, limit)]]


def extract_path_prefix(
    text_before_cursor: str, *, force: bool
) -> tuple[int, str] | None:
    """Return ``(prefix_start_index, prefix)`` for editor path completion.

    Mirrors Pi's ``extractPathPrefix``: a token opened by an unmatched double
    quote (a path with spaces, e.g. ``"./my dir/``) is taken whole from the
    quote so progressive completion survives the embedded space; otherwise the
    prefix is the text after the last whitespace boundary. With ``force=True``
    (a Tab keypress) any token is returned. With ``force=False`` (a natural
    trigger) the token is returned only when it looks path-like (contains ``/``
    or starts with ``.`` / ``~/``). Ordinary prose — including an empty token
    after a trailing space, e.g. ``hello <Tab>`` — returns ``None`` so a Tab
    there is a no-op rather than listing the working directory.
    """

    # A quoted path (unmatched opening double quote) may contain spaces; take it
    # whole from the quote so the embedded space does not split the token. The
    # leading quote is preserved so path completion re-quotes the result.
    quote_index = _last_unmatched_quote(text_before_cursor)
    if quote_index is not None:
        return quote_index, text_before_cursor[quote_index:]

    boundary = max(text_before_cursor.rfind(" "), text_before_cursor.rfind("\n"))
    start = boundary + 1
    prefix = text_before_cursor[start:]

    if force:
        return start, prefix
    if "/" in prefix or prefix.startswith(".") or prefix.startswith("~/"):
        return start, prefix
    return None


def path_candidates(
    workspace: Path, prefix: str, *, limit: int = _PATH_RESULT_LIMIT
) -> list[CompletionItem]:
    """List a single directory's entries for Tab path completion (Pi parity).

    Resolves ``prefix`` into a search directory plus a filename fragment, lists
    that directory's entries with a case-insensitive ``startswith`` match on the
    fragment, and orders directories first then alphabetically (no fuzzy
    ranking). ``~/`` is expanded to the home directory, ``.``/``./`` and
    absolute prefixes are preserved in the returned ``value``, directories keep
    a trailing ``/``, and a ``value`` containing a space is double-quoted. Reads
    no file contents; ``.git`` entries are never listed, and a ``.git`` (or
    other hard-denied / ignored) directory is never listed *into* either.
    """

    if not _safe_completion_prefix(prefix):
        return []
    quoted = prefix.startswith('"')
    raw = prefix[1:] if quoted else prefix

    search_dir, search_prefix = _resolve_search_dir(workspace, raw)
    if search_dir is None:
        return []
    if not _listable_search_dir(search_dir, workspace):
        return []

    try:
        entries = list(os.scandir(search_dir))
    except OSError:
        return []

    items: list[tuple[bool, str, CompletionItem]] = []
    for entry in entries:
        name = entry.name
        if name in _HARD_DENY_DIR_NAMES:
            continue
        if not name.lower().startswith(search_prefix.lower()):
            continue
        try:
            is_dir = entry.is_dir()
        except OSError:
            is_dir = False
        relative_path = _compose_relative_path(raw, name)
        path_value = f"{relative_path}/" if is_dir else relative_path
        value = _quote_if_needed(path_value, quoted=quoted, at_prefix=False)
        label = name + ("/" if is_dir else "")
        items.append((is_dir, label.lower(), CompletionItem(value, label)))

    items.sort(key=lambda entry: (not entry[0], entry[1]))
    return [item for _is_dir, _key, item in items[: max(0, limit)]]


# --- internal helpers -------------------------------------------------------


def _last_unmatched_quote(text: str) -> int | None:
    """Return the index of an unmatched opening double quote, or ``None``.

    A quoted path token (``"./my dir/``) is open while the number of double
    quotes before the cursor is odd; the last quote then begins a token that may
    contain spaces.
    """

    if text.count('"') % 2 == 0:
        return None
    return text.rfind('"')


def _split_scoped_query(query: str) -> tuple[str, str]:
    """Split an ``@`` query into a workspace-relative base dir and leaf query."""

    normalized = query.replace("\\", "/")
    if "/" not in normalized:
        return "", normalized
    base, leaf = normalized.rsplit("/", 1)
    return base, leaf


def _walk_workspace(base_dir: Path, workspace_root: Path):
    """Yield ``(workspace_relative_label, is_dir)`` for a bounded walk.

    Bounded by depth and total entry count; applies the ``.git``/ignored
    default-deny and skips symlinked directories that escape the workspace.
    """

    seen = 0
    base_depth = len(base_dir.relative_to(workspace_root).parts)
    for current_root, dir_names, file_names in os.walk(base_dir, followlinks=False):
        current = Path(current_root)
        depth = len(current.relative_to(workspace_root).parts)
        if depth - base_depth >= _WALK_MAX_DEPTH:
            dir_names[:] = []
        # Prune denied/ignored directories in place so os.walk does not descend.
        kept_dirs: list[str] = []
        for name in sorted(dir_names):
            if name in _HARD_DENY_DIR_NAMES:
                continue
            rel = (current / name).relative_to(workspace_root).as_posix()
            if _is_ignored_or_generated(rel, workspace_root):
                continue
            # Symlink containment: a symlinked directory whose target escapes
            # the workspace is neither descended into nor offered as a candidate.
            if not _contained_in_workspace(current / name, workspace_root):
                continue
            kept_dirs.append(name)
            if seen >= _WALK_MAX_ENTRIES:
                break
            yield rel, True
            seen += 1
        dir_names[:] = kept_dirs
        for name in sorted(file_names):
            if seen >= _WALK_MAX_ENTRIES:
                return
            rel = (current / name).relative_to(workspace_root).as_posix()
            if _is_ignored_or_generated(rel, workspace_root):
                continue
            # Symlink containment: a symlinked file resolving outside the
            # workspace is not offered (it would fail the resolver's policy too).
            if not _contained_in_workspace(current / name, workspace_root):
                continue
            yield rel, False
            seen += 1
        if seen >= _WALK_MAX_ENTRIES:
            return


def _contained_in_workspace(path: Path, workspace_root: Path) -> bool:
    """True when ``path`` resolves to a location inside the workspace.

    A symlink whose target escapes the workspace resolves elsewhere and is
    excluded, matching the ReadTool / file_references symlink-containment policy.
    """

    try:
        return _is_relative_to(path.resolve(), workspace_root)
    except OSError:
        return False


def _build_at_item(relative_label: str, is_dir: bool) -> CompletionItem:
    path_value = f"{relative_label}/" if is_dir else relative_label
    value = _quote_if_needed(path_value, quoted=False, at_prefix=True)
    label = PurePosixPath(relative_label).name + ("/" if is_dir else "")
    return CompletionItem(value, label)


def _quote_if_needed(path: str, *, quoted: bool, at_prefix: bool) -> str:
    needs_quotes = quoted or " " in path
    prefix = "@" if at_prefix else ""
    if not needs_quotes:
        return f"{prefix}{path}"
    # A completed directory (trailing ``/``) keeps the quote *open* so a
    # subsequent Tab still sees an unmatched quote and continues inside the
    # directory; a file closes the quote so the reference resolves as one token.
    if path.endswith("/"):
        return f'{prefix}"{path}'
    return f'{prefix}"{path}"'


def _safe_completion_prefix(prefix: str) -> bool:
    if "\x00" in prefix:
        return False
    return not any(ord(char) < 32 for char in prefix)


def _listable_search_dir(search_dir: Path, workspace: Path) -> bool:
    """Whether a Tab-completion search directory may be listed at all.

    Never lists into a hard-denied directory (``.git`` as any path component),
    and for workspace-relative directories applies the same ignored/generated
    deny the ``@`` picker walk uses — so ``.git/<Tab>`` (and other ignored
    roots) cannot bypass the completion boundary by being the search dir itself.
    """

    if any(part in _HARD_DENY_DIR_NAMES for part in search_dir.parts):
        return False
    try:
        workspace_root = workspace.expanduser().resolve()
        resolved = search_dir.resolve()
    except OSError:
        return False
    if _is_relative_to(resolved, workspace_root):
        rel = resolved.relative_to(workspace_root).as_posix()
        if rel not in {"", "."} and _is_ignored_or_generated(rel, workspace_root):
            return False
    return True


def _resolve_search_dir(
    workspace: Path, raw: str
) -> tuple[Path | None, str]:
    """Return ``(search_dir, search_prefix)`` for path completion.

    ``search_prefix`` is the filename fragment; the caller keeps ``raw`` for the
    display prefix (so ``./`` and ``~/`` survive on the rendered value).
    """

    try:
        workspace_root = workspace.expanduser().resolve()
    except OSError:
        return None, ""
    home = Path.home()

    def expand(path_text: str) -> Path | None:
        if path_text.startswith("~/") or path_text == "~":
            return home / path_text[2:] if path_text.startswith("~/") else home
        if path_text.startswith("/"):
            return Path(path_text)
        return workspace_root / path_text

    is_root_prefix = raw in {"", "./", "../", "~", "~/", "/"}
    if is_root_prefix or raw.endswith("/"):
        target = expand(raw)
        search_prefix = ""
    else:
        directory = PurePosixPath(raw).parent
        directory_text = "" if str(directory) == "." else str(directory)
        target = expand(directory_text + ("/" if directory_text else ""))
        if target is not None and directory_text == "":
            target = expand(raw[: -len(PurePosixPath(raw).name)] or "")
        search_prefix = PurePosixPath(raw).name

    if target is None:
        return None, ""
    try:
        resolved = target.resolve()
    except OSError:
        return None, ""
    if not resolved.is_dir():
        return None, ""
    # Workspace-relative prefixes must stay inside the workspace; ``~/`` and
    # absolute prefixes are explicit user navigation (Pi behavior) and allowed.
    if not raw.startswith(("~", "/")) and not _is_relative_to(resolved, workspace_root):
        return None, ""
    return resolved, search_prefix


def _compose_relative_path(raw: str, name: str) -> str:
    """Compose the inserted path value for ``name`` preserving the typed style.

    Mirrors Pi's ``getFileSuggestions`` display-prefix handling: a prefix
    ending in ``/`` appends the name; ``~``/``~…`` keeps the ``~/`` form; a
    prefix with an embedded ``/`` keeps everything up to the last separator;
    and a bare token completes to just the name.
    """

    if raw.endswith("/"):
        return f"{raw}{name}"
    if raw == "/":
        return f"/{name}"
    if "/" in raw:
        head = raw[: raw.rfind("/") + 1]
        return f"{head}{name}"
    if raw.startswith("~"):
        return f"~/{name}"
    return name

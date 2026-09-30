"""The ignore rules ``rg`` and ``fd`` apply while walking, for Python walks.

Pi's ``grep`` runs ``rg --hidden`` and its ``find`` runs ``fd --hidden``
(adding ``--no-require-git`` outside a git repository). pipy's ``find`` and
its ``grep`` fallback walk in Python, so this module reproduces the rules
those tools apply, measured on rg 15.2.0 and fd 10.5.0:

- Hidden entries and ``.git`` itself are walked; nothing is refused by name.
- ``.ignore`` files apply everywhere, in every ancestor of the search path
  and in every visited directory. The tool's own file does too:
  ``.rgignore`` for rg, ``.fdignore`` for fd.
- ``.gitignore`` files apply inside a git repository, from the repository
  root down; rules above the repository root do not. Outside a repository
  rg ignores them and fd (``--no-require-git``) applies them, ancestors
  included. A repository's ``info/exclude`` applies inside it; for a linked
  worktree that is the main repository's (the ``commondir``).
- A directory holding ``.git`` starts a repository, so ``.gitignore`` rules
  from above it stop there (fd's nested-repository boundary, and rg's),
  except in fd's ``--no-require-git`` walk, which keeps them.
- Precedence (the ``ignore`` crate): the tool file beats ``.ignore``, which
  beats the git rules (``info/exclude`` lowest). Within one class, the last
  matching line of the deepest directory wins. A leading ``!`` re-includes.

Each line compiles as the ``ignore`` crate compiles it (``gitignore.rs``):
``#`` comments and blank lines are skipped, trailing spaces are trimmed
unless escaped, ``!`` negates (``\\!``/``\\#`` escape), a leading ``/``
anchors the line to its file's directory, a trailing ``/`` restricts it to
directories, a line with no other ``/`` gets ``**/`` (any depth), a trailing
``/**`` becomes ``/**/*``, and the result is a globset glob with
``literal_separator`` and backslash escapes, so ``{a,b}`` alternation and
``[...]`` classes work (:mod:`pipy_harness.native.tools.glob_match`).

Deviation: the global git excludes file (``core.excludesFile``) is not read.
A caller's ``rg --glob`` override is checked before these rules (see
:mod:`pipy_harness.native.tools.grep_fallback`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from pipy_harness.native.tools.glob_match import GlobError, compile_glob

RG_IGNORE_FILE = ".rgignore"
FD_IGNORE_FILE = ".fdignore"


@dataclass(frozen=True, slots=True)
class GitIgnoreRule:
    """One compiled ignore-file line."""

    negated: bool
    dir_only: bool
    regex: re.Pattern[str]

    def matches(self, relative_path: str, *, is_dir: bool) -> bool:
        """``relative_path`` is POSIX and relative to the rule's directory."""

        if self.dir_only and not is_dir:
            return False
        return self.regex.match(relative_path) is not None


def compile_gitignore_line(line: str) -> GitIgnoreRule | None:
    """Compile one ignore-file line like the ``ignore`` crate; None to skip it."""

    if line.startswith("#"):
        return None
    if not line.endswith("\\ "):
        line = line.rstrip()
    negated = line.startswith("!")
    if negated or line.startswith(("\\!", "\\#")):
        line = line[1:]
    anchored = line.startswith("/")
    line = line.removeprefix("/")
    dir_only = line.endswith("/")
    line = line.removesuffix("/")
    if not line:
        return None
    glob = line
    if not anchored and "/" not in line and not line.startswith("**/"):
        glob = f"**/{line}"
    if glob.endswith("/**"):
        glob = f"{glob}/*"
    try:
        regex = compile_glob(
            _escape_unclosed_brackets(glob), literal_separator=True, ignore_case=False
        )
    except GlobError:
        # Measured: rg and fd skip a line they cannot parse (an unclosed `{`).
        return None
    return GitIgnoreRule(negated, dir_only, regex)


def _escape_unclosed_brackets(glob: str) -> str:
    """Escape each `[` that opens no class: rg and fd match it literally.

    Measured: a `[foo` or `*.[ch` line excludes the files `[foo` and `x.[ch`.
    """

    out: list[str] = []
    index = 0
    while index < len(glob):
        char = glob[index]
        if char == "\\":
            out.append(glob[index : index + 2])
            index += 2
            continue
        if char == "[":
            end = _class_end(glob, index)
            if end is None:
                out.append("\\[")
            else:
                # A closed class is copied as is, `[` members included.
                out.append(glob[index : end + 1])
                index = end + 1
                continue
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _class_end(glob: str, start: int) -> int | None:
    """Index of the `]` closing the class opened at ``start``, or None."""

    position = start + 1
    if position < len(glob) and glob[position] in "!^":
        position += 1
    # A `]` right after `[` (or `[!`) is a member, not the end.
    if position < len(glob) and glob[position] == "]":
        position += 1
    end = glob.find("]", position)
    return None if end == -1 else end


@dataclass(frozen=True, slots=True)
class _ScopedRule:
    base: str  # the rule file's directory, absolute, ending with "/"
    rule: GitIgnoreRule


@dataclass(frozen=True, slots=True)
class WalkIgnore:
    """The rules that apply to the entries of one directory (immutable)."""

    tool_file: str
    require_git: bool
    in_repo: bool = False
    tool_rules: tuple[_ScopedRule, ...] = ()
    plain_rules: tuple[_ScopedRule, ...] = ()
    git_rules: tuple[_ScopedRule, ...] = ()

    @classmethod
    def for_search(
        cls, search_path: Path, *, tool_file: str, no_require_git_outside_repo: bool
    ) -> WalkIgnore:
        """Rules for the entries of ``search_path`` (an absolute directory).

        ``no_require_git_outside_repo`` is Pi's fd rule: when no ancestor of
        the search path (itself included) holds ``.git``, the walk runs as
        ``fd --no-require-git``: ``.gitignore`` files apply everywhere and a
        nested repository does not reset them. Otherwise (and always for
        rg) the walk is git-aware.
        """

        chain = (*reversed(search_path.parents), search_path)
        outside_repo = not any(_exists(directory / ".git") for directory in chain)
        state = cls(
            tool_file=tool_file,
            require_git=not (no_require_git_outside_repo and outside_repo),
        )
        for directory in chain:
            state = state.descend(directory)
        return state

    def descend(self, directory: Path) -> WalkIgnore:
        """Rules for the entries of ``directory``, a child of this directory."""

        base = directory.as_posix().rstrip("/") + "/"
        repo_root = _exists(directory / ".git")
        in_repo = self.in_repo or repo_root
        git_rules = self.git_rules
        if repo_root:
            exclude = _load(
                _common_git_dir(directory / ".git") / "info" / "exclude", base
            )
            # A git-aware walk starts over at a repository root; rules from
            # above it stop there. --no-require-git keeps them.
            git_rules = exclude + (git_rules if not self.require_git else ())
        gitignore = (
            _load(directory / ".gitignore", base)
            if in_repo or not self.require_git
            else ()
        )
        plain = _load(directory / ".ignore", base)
        tool = _load(directory / self.tool_file, base)
        if not (repo_root or gitignore or plain or tool):
            return self
        return replace(
            self,
            in_repo=in_repo,
            tool_rules=self.tool_rules + tool,
            plain_rules=self.plain_rules + plain,
            git_rules=git_rules + gitignore,
        )

    def ignored(self, path: Path, *, is_dir: bool) -> bool:
        """Return True when the entry at absolute ``path`` is ignored."""

        text = path.as_posix()
        for rules in (self.tool_rules, self.plain_rules, self.git_rules):
            decision = _decide(rules, text, is_dir)
            if decision is not None:
                return decision
        return False


def _decide(rules: tuple[_ScopedRule, ...], path: str, is_dir: bool) -> bool | None:
    for scoped in reversed(rules):
        if not path.startswith(scoped.base):
            continue
        if scoped.rule.matches(path[len(scoped.base) :], is_dir=is_dir):
            return not scoped.rule.negated
    return None


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _common_git_dir(dot_git: Path) -> Path:
    """The directory holding ``info/exclude`` for a ``.git`` entry.

    A ``.git`` file (linked worktree, submodule) names its git directory
    with ``gitdir: <path>``; a linked worktree's git directory has a
    ``commondir`` file naming the shared one, relative to it.
    """

    if not dot_git.is_file():
        return dot_git
    try:
        text = dot_git.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return dot_git
    if not text.startswith("gitdir:"):
        return dot_git
    git_dir = dot_git.parent / text.removeprefix("gitdir:").strip()
    try:
        common = (git_dir / "commondir").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return git_dir
    return git_dir / common if common else git_dir


def _load(path: Path, base: str) -> tuple[_ScopedRule, ...]:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    rules: list[_ScopedRule] = []
    # Measured: rg and fd drop a leading UTF-8 byte order mark.
    for line in re.split(r"\r?\n", content.removeprefix("﻿")):
        rule = compile_gitignore_line(line)
        if rule is not None:
            rules.append(_ScopedRule(base, rule))
    return tuple(rules)


__all__ = [
    "FD_IGNORE_FILE",
    "RG_IGNORE_FILE",
    "GitIgnoreRule",
    "WalkIgnore",
    "compile_gitignore_line",
]

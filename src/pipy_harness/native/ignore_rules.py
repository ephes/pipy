"""Gitignore-style ignore rules for skill discovery.

A small stdlib port of the ignore handling Pi applies while collecting
skills (`packages/coding-agent/src/core/package-manager.ts`
`addIgnoreRules`/`prefixIgnorePattern`, and `core/skills.ts`), matched with
the node `ignore` package's gitignore semantics:

- At each visited directory, `.gitignore`, `.ignore` and `.fdignore` lines
  are added, prefixed with the directory's root-relative POSIX path. Blank
  lines and `#` comments are skipped; a leading `!` negates; a leading `/`
  is stripped before prefixing; `\\#` and `\\!` are escapes.
- The last matching rule wins. A trailing `/` restricts a rule to
  directories. A rule containing `/` is anchored at the matcher root;
  otherwise it matches at any depth. `*`, `?`, `**` and `[...]` behave as in
  gitignore. A path below an ignored directory is ignored.

Paths passed to `IgnoreMatcher.ignores` are root-relative POSIX paths; a
directory is tested with a trailing `/`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

IGNORE_FILE_NAMES: tuple[str, ...] = (".gitignore", ".ignore", ".fdignore")


@dataclass(frozen=True, slots=True)
class _Rule:
    negated: bool
    regex: re.Pattern[str]


class IgnoreMatcher:
    """An ordered set of gitignore rules (node `ignore` semantics)."""

    def __init__(self) -> None:
        self._rules: list[_Rule] = []

    def add(self, patterns: Iterable[str]) -> None:
        for pattern in patterns:
            rule = _compile_rule(pattern)
            if rule is not None:
                self._rules.append(rule)

    def ignores(self, path: str) -> bool:
        """Return True when root-relative `path` is ignored.

        A trailing `/` marks `path` as a directory. Parent directories are
        checked first: a path under an ignored directory is ignored.
        """

        if not self._rules:
            return False
        is_dir = path.endswith("/")
        parts = [part for part in path.strip("/").split("/") if part]
        for index in range(1, len(parts)):
            if self._test("/".join(parts[:index]) + "/"):
                return True
        if not parts:
            return False
        return self._test("/".join(parts) + ("/" if is_dir else ""))

    def _test(self, path: str) -> bool:
        ignored = False
        for rule in self._rules:
            if rule.negated != ignored:
                continue
            if rule.regex.search(path):
                ignored = not rule.negated
        return ignored


def prefix_ignore_pattern(line: str, prefix: str) -> str | None:
    """Port of Pi `prefixIgnorePattern`."""

    trimmed = line.strip()
    if not trimmed:
        return None
    if trimmed.startswith("#") and not trimmed.startswith("\\#"):
        return None
    pattern = line
    negated = False
    if pattern.startswith("!"):
        negated = True
        pattern = pattern[1:]
    elif pattern.startswith("\\!"):
        pattern = pattern[1:]
    if pattern.startswith("/"):
        pattern = pattern[1:]
    prefixed = f"{prefix}{pattern}" if prefix else pattern
    return f"!{prefixed}" if negated else prefixed


def add_ignore_rules(matcher: IgnoreMatcher, directory: Path, root: Path) -> None:
    """Port of Pi `addIgnoreRules`: add `directory`'s ignore files to `matcher`."""

    try:
        relative_dir = directory.relative_to(root).as_posix()
    except ValueError:
        return
    prefix = "" if relative_dir in ("", ".") else f"{relative_dir}/"
    for filename in IGNORE_FILE_NAMES:
        ignore_path = directory / filename
        try:
            if not ignore_path.exists():
                continue
            content = ignore_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        patterns = [
            prefixed
            for line in re.split(r"\r?\n", content)
            if (prefixed := prefix_ignore_pattern(line, prefix))
        ]
        if patterns:
            matcher.add(patterns)


def _compile_rule(pattern: str) -> _Rule | None:
    text = pattern
    if not text.strip() or (text.startswith("#")):
        return None
    negated = False
    if text.startswith("!"):
        negated = True
        text = text[1:]
    elif text.startswith("\\!") or text.startswith("\\#"):
        text = text[1:]
    text = _strip_trailing_spaces(text)
    if not text:
        return None
    dir_only = text.endswith("/")
    text = text.rstrip("/")
    if text.startswith("/"):
        text = text[1:]
    if not text:
        return None
    anchored = "/" in text
    body = _translate(text)
    head = "^" if anchored else "^(?:.*/)?"
    tail = "/$" if dir_only else "/?$"
    return _Rule(negated=negated, regex=re.compile(head + body + tail))


def _strip_trailing_spaces(text: str) -> str:
    stripped = text.rstrip(" ")
    if stripped.endswith("\\") and len(stripped) < len(text):
        return stripped[:-1] + " "
    return stripped


def _translate(pattern: str) -> str:
    """Translate one gitignore glob (no leading/trailing `/`) to a regex body."""

    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            regex, index = _translate_star(pattern, index)
        elif char == "[":
            regex, index = _translate_bracket(pattern, index)
        elif char == "?":
            regex, index = "[^/]", index + 1
        elif char == "\\" and index + 1 < len(pattern):
            regex, index = re.escape(pattern[index + 1]), index + 2
        else:
            regex, index = re.escape(char), index + 1
        out.append(regex)
    return "".join(out)


def _translate_star(pattern: str, index: int) -> tuple[str, int]:
    """Translate `*` or `**` starting at `index`; return (regex, next index)."""

    if not pattern.startswith("**", index):
        return "[^/]*", index + 1
    after = index + 2
    at_segment_start = index == 0 or pattern[index - 1] == "/"
    if not at_segment_start:
        return "[^/]*", after
    if after == len(pattern):
        return ".+", after  # trailing `/**`: everything inside
    if pattern[after] == "/":
        return "(?:.*/)?", after + 1  # `**/`: zero or more directories
    return "[^/]*", after


def _translate_bracket(pattern: str, index: int) -> tuple[str, int]:
    """Translate a `[...]` class at `index`; a lone `[` is literal."""

    end = pattern.find("]", index + 2)
    if end == -1:
        return re.escape("["), index + 1
    inner = pattern[index + 1 : end]
    if inner.startswith("!"):
        inner = "^" + inner[1:]
    return "[" + inner.replace("\\", "\\\\") + "]", end + 1

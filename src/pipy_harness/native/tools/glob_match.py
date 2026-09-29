"""Glob patterns with the globset rules that Pi's `fd` and `rg --glob` use.

Pi's `find` runs `fd --glob` and its `grep` passes `glob` to `rg --glob`; both
tools compile globs with Rust's `globset`. pipy implements `find` and the
`grep` fallback in Python, so this module translates a glob to a regular
expression with the same rules:

- ``*`` matches any run of characters and ``?`` one character; with
  ``literal_separator`` neither crosses ``/``.
- ``**`` as a whole path component matches any number of components: a
  leading ``**/`` matches zero or more leading directories, a trailing
  ``/**`` everything below, ``/**/`` zero or more directories between. A
  ``**`` inside a component is a plain ``*``.
- ``[abc]``, ``[a-z]`` and the negations ``[!a]``/``[^a]`` are character
  classes; ``{a,b}`` is an alternation of globs (not nested); a backslash
  escapes the next character.

An unclosed ``[`` or ``{``, or a nested ``{``, raises :class:`GlobError`.
Standard library only.
"""

from __future__ import annotations

import re


class GlobError(ValueError):
    """The glob pattern is not valid."""


def compile_glob(
    pattern: str, *, literal_separator: bool, ignore_case: bool
) -> re.Pattern[str]:
    """Compile ``pattern`` into a regex that must match a whole path."""

    body = _translate(pattern, literal_separator=literal_separator)
    flags = re.DOTALL | (re.IGNORECASE if ignore_case else 0)
    try:
        return re.compile(f"(?:{body})\\Z", flags)
    except re.error as exc:  # pragma: no cover - the translator emits valid regex
        raise GlobError(f"error parsing glob '{pattern}': {exc}") from None


def _translate(
    pattern: str, *, literal_separator: bool, in_braces: bool = False
) -> str:
    star = "[^/]*" if literal_separator else ".*"
    question = "[^/]" if literal_separator else "."
    out: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "\\":
            index += 1
            if index < length:
                out.append(re.escape(pattern[index]))
                index += 1
            else:
                out.append(re.escape("\\"))
            continue
        if char == "*":
            if pattern.startswith("**", index):
                piece, index = _double_star(pattern, index, star)
                out.append(piece)
                continue
            out.append(star)
            index += 1
            continue
        if char == "?":
            out.append(question)
            index += 1
            continue
        if char == "[":
            piece, index = _char_class(pattern, index)
            out.append(piece)
            continue
        if char == "{":
            if in_braces:
                raise GlobError(f"error parsing glob '{pattern}': nested alternates")
            close = _closing_brace(pattern, index)
            alternatives = _split_alternatives(pattern[index + 1 : close])
            out.append(
                "(?:"
                + "|".join(
                    _translate(
                        alternative,
                        literal_separator=literal_separator,
                        in_braces=True,
                    )
                    for alternative in alternatives
                )
                + ")"
            )
            index = close + 1
            continue
        out.append(re.escape(char))
        index += 1
    return "".join(out)


def _double_star(pattern: str, index: int, star: str) -> tuple[str, int]:
    end = index + 2
    at_start = index == 0 or pattern[index - 1] == "/"
    at_end = end == len(pattern) or pattern[end] == "/"
    if not (at_start and at_end):
        # `**` inside a component is an ordinary `*`.
        return star, end
    if end == len(pattern):
        if index == 0:
            return ".*", end
        # `a/**` matches everything below `a/`.
        return ".*", end
    # `**/` (leading or between components): zero or more directories.
    return "(?:.*/)?", end + 1


def _char_class(pattern: str, index: int) -> tuple[str, int]:
    position = index + 1
    negate = False
    if position < len(pattern) and pattern[position] in "!^":
        negate = True
        position += 1
    members: list[str] = []
    first = True
    while position < len(pattern):
        char = pattern[position]
        if char == "]" and not first:
            prefix = "^" if negate else ""
            return f"[{prefix}{''.join(members)}]", position + 1
        if (
            char == "-"
            and members
            and position + 1 < len(pattern)
            and pattern[position + 1] != "]"
        ):
            members.append("-")
        elif char in "\\^[]-":
            members.append("\\" + char)
        else:
            members.append(char)
        first = False
        position += 1
    raise GlobError(
        f"error parsing glob '{pattern}': unclosed character class; missing ']'"
    )


def _closing_brace(pattern: str, index: int) -> int:
    position = index + 1
    while position < len(pattern):
        char = pattern[position]
        if char == "\\":
            position += 2
            continue
        if char == "{":
            raise GlobError(f"error parsing glob '{pattern}': nested alternates")
        if char == "}":
            return position
        position += 1
    raise GlobError(f"error parsing glob '{pattern}': unclosed alternate group")


def _split_alternatives(body: str) -> list[str]:
    alternatives: list[str] = []
    current: list[str] = []
    position = 0
    while position < len(body):
        char = body[position]
        if char == "\\" and position + 1 < len(body):
            current.append(body[position : position + 2])
            position += 2
            continue
        if char == ",":
            alternatives.append("".join(current))
            current = []
        else:
            current.append(char)
        position += 1
    alternatives.append("".join(current))
    return alternatives


__all__ = ["GlobError", "compile_glob"]

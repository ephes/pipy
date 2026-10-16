"""Glob matching as Pi's minimatch does it with its default options.

Pi matches the ``!`` entries of a resource setting with minimatch 10.2.6
(``matchesAnyPattern`` in ``package-manager.ts``): a leading ``!`` run
negates the pattern, a ``#`` pattern matches nothing, braces are expanded
first (``{a,b}``, ``{1..3}``), and each path segment is matched as an
extended glob (``+(a|b)``, ``@(...)``, ``!(...)``, character classes), in
which a ``*`` never crosses a ``/`` and no wildcard matches a leading dot.
A ``**`` segment matches zero or more segments that do not start with a
dot.

Braces are expanded by :mod:`pipy_harness.native.brace_expansion` and a
segment is compiled by :mod:`pipy_harness.native.glob_segment`, ports of
the code minimatch uses. The walk over the ``**`` segments follows
minimatch's ``#matchGlobstar`` (its head and tail, the last start it allows
each section and its 200-section limit), but as one pass per section over
the path's segments, so the work grows with the number of sections times
the path's length and nothing recurses.

Not as in minimatch: braces nested so deeply that Python's recursion limit
is reached match nothing, and so does a segment Python cannot compile,
where minimatch raises for most such patterns.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Final

from pipy_harness.native.brace_expansion import expand as brace_expand
from pipy_harness.native.glob_segment import segment_matcher

# minimatch's maxGlobstarRecursion: with more sections between the first
# and the last `**`, minimatch finds no match.
_MAX_GLOBSTAR_SECTIONS: Final = 200
# minimatch's test for a pattern worth expanding (JavaScript's `.`).
_HAS_BRACES: Final = re.compile("\\{(?:(?!\\{)[^\n\r\u2028\u2029])*\\}")
_SLASHES: Final = re.compile(r"/+")

# A pattern segment; `None` stands for a `**` segment.
_Part = str | None


@lru_cache(maxsize=1024)
def minimatch(path: str, pattern: str) -> bool:
    """Whether ``path`` matches ``pattern`` as ``minimatch(path, pattern)``."""

    if pattern.startswith("#"):
        return False
    if not pattern:
        return path == ""
    body = pattern.lstrip("!")
    negate = (len(pattern) - len(body)) % 2 == 1
    segments = _SLASHES.split(path)
    hit = any(_match_parts(segments, parts) for parts in _compile(body))
    return hit != negate


@lru_cache(maxsize=256)
def _compile(pattern: str) -> tuple[tuple[_Part, ...], ...]:
    """The pattern's brace expansions, each split into segments."""

    if _HAS_BRACES.search(pattern):
        try:
            expanded = brace_expand(pattern)
        except RecursionError:
            return ()
    else:
        expanded = [pattern]
    return tuple(_optimize(_SLASHES.split(item)) for item in dict.fromkeys(expanded))


def _optimize(segments: Sequence[str]) -> tuple[_Part, ...]:
    """minimatch's ``levelOneOptimize``: adjacent ``**`` become one, and a
    ``..`` drops the segment before it unless that is ``.``, ``..`` or ``**``."""

    parts: list[_Part] = []
    for segment in segments:
        previous = parts[-1] if parts else ""
        if segment == "**" and parts and previous is None:
            continue
        if segment == ".." and previous not in ("", "..", ".", None):
            parts.pop()
            continue
        parts.append(None if segment == "**" else segment)
    return tuple(parts) if parts else ("",)


def _hidden(segment: str) -> bool:
    """A segment a ``**`` does not pass over (no ``dot`` option)."""

    return segment.startswith(".")


def _segment_matches(segment: str, part: str) -> bool:
    if not part:
        return segment == ""
    try:
        return segment_matcher(part)(segment)
    except RecursionError:
        return False


def _match_run(segments: Sequence[str], start: int, parts: Sequence[_Part]) -> bool:
    """minimatch's ``#matchOne``: ``parts`` match ``segments[start:]``; an
    empty last segment (a trailing ``/``) may be left over."""

    index = start
    for part in parts:
        if index == len(segments) or part is None:
            return False
        if not _segment_matches(segments[index], part):
            return False
        index += 1
    return index == len(segments) or (
        index == len(segments) - 1 and segments[index] == ""
    )


def _match_parts(segments: Sequence[str], parts: tuple[_Part, ...]) -> bool:
    if None not in parts:
        return _match_run(segments, 0, parts)
    first = parts.index(None)
    last = len(parts) - 1 - parts[::-1].index(None)
    head = parts[:first]
    start = len(head)
    if head and not _match_run(segments[:start], 0, head):
        return False
    tail_size = _match_tail(segments, start, parts[last + 1 :])
    if tail_size is None:
        return False
    end = len(segments) - tail_size
    sections = _sections(parts[first + 1 : last])
    if not sections:
        skipped = segments[start:end]
        return bool(tail_size or skipped) and not any(map(_hidden, skipped))
    if len(sections) > _MAX_GLOBSTAR_SECTIONS:
        return False
    return _match_sections(segments, start, end, sections, saw_tail=tail_size > 0)


def _match_tail(
    segments: Sequence[str], start: int, tail: Sequence[_Part]
) -> int | None:
    """How many segments at the end the parts after the last ``**`` take,
    or ``None`` when they do not match there."""

    if not tail:
        return 0
    if start + len(tail) > len(segments):
        return None
    at = len(segments) - len(tail)
    if _match_run(segments, at, tail):
        return len(tail)
    # minimatch: `a/**/*` matches `a/b/`, the tail before the empty segment.
    if segments[-1] != "" or start + len(tail) == len(segments):
        return None
    if _match_run(segments, at - 1, tail):
        return len(tail) + 1
    return None


def _sections(body: Sequence[_Part]) -> list[list[str]]:
    """The runs of segments between the ``**`` of ``body``."""

    if not body:
        return []
    sections: list[list[str]] = [[]]
    for part in body:
        if part is None:
            sections.append([])
        else:
            sections[-1].append(part)
    return sections


def _latest_starts(sections: Sequence[Sequence[str]], end: int) -> list[int]:
    """The last index at which minimatch tries each section.

    For section ``k`` of ``n`` it is ``end`` less the section's length and
    the number of segments in the sections before section ``n - 1 - k``,
    which is the room the later sections need only when the sections are
    of one length; minimatch's bound is kept as it is.
    """

    before = [0]
    for section in sections[:-1]:
        before.append(before[-1] + len(section))
    count = len(sections)
    return [
        end - (before[count - 1 - index] + len(section))
        for index, section in enumerate(sections)
    ]


def _match_sections(
    segments: Sequence[str],
    start: int,
    end: int,
    sections: Sequence[Sequence[str]],
    *,
    saw_tail: bool,
) -> bool:
    """minimatch's ``#matchGlobStarBodySections`` as one pass per section.

    ``starts`` holds the indices a ``**`` may start at. It passes over
    segments that do not start with a dot, so each section may begin at a
    start or at a later index reached that way, up to its latest start; the
    ends of its matches are the next section's starts. The segments after
    the last section, the tail's included, must not start with a dot.
    """

    starts = {start}
    for section, latest in zip(sections, _latest_starts(sections, end), strict=True):
        found: set[int] = set()
        reached = False
        for at in range(min(starts), latest + 1):
            reached = at in starts or (reached and not _hidden(segments[at - 1]))
            if reached and _section_at(segments, at, section):
                found.add(at + len(section))
        if not found:
            return False
        starts = found
    return any(
        (saw_tail or at < len(segments)) and not any(map(_hidden, segments[at:]))
        for at in starts
    )


def _section_at(segments: Sequence[str], at: int, section: Sequence[str]) -> bool:
    if at + len(section) > len(segments):
        return False
    return all(
        _segment_matches(segments[at + offset], part)
        for offset, part in enumerate(section)
    )

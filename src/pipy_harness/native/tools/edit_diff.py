"""Edit matching and the display diff, ported from Pi's ``edit-diff.ts``.

Mirrors ``packages/coding-agent/src/core/tools/edit-diff.ts`` (pi-mono
``1b347794e``) line for line: line-ending detection and normalization, the
fuzzy normalization (NFKC, trailing whitespace, smart quotes, Unicode dashes
and spaces), ``applyEditsToNormalizedContent`` with its error texts, and
``generateDiffString``, the ``+NN``/``-NN`` display diff Pi's edit row shows.

``generateDiffString`` runs on jsdiff's ``diffLines``; :func:`diff_lines` is a
port of jsdiff 8.0.4 (``libesm/diff/base.js`` and ``line.js``, no options):
the Myers walk with jsdiff's diagonal choice, the line tokenizer that keeps
line endings, and component merging, so ambiguous diffs pick the same lines
as Pi's.

Offsets count Python code points where Pi counts UTF-16 units; both are used
only inside one run, so the results are the same.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass


# JavaScript's `trimEnd` set: WhiteSpace (tab, VT, FF, space, NBSP, BOM, Zs)
# plus LineTerminator. Python's `rstrip()` differs (no BOM, adds \x1c-\x1f).
def _chars(*codepoints: int) -> str:
    return "".join(chr(codepoint) for codepoint in codepoints)


BOM = _chars(0xFEFF)
_JS_WHITESPACE = "\t\n\x0b\x0c\r \xa0" + _chars(
    0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF
)
_SMART_SINGLE = re.compile(f"[{_chars(0x2018, 0x2019, 0x201A, 0x201B)}]")
_SMART_DOUBLE = re.compile(f"[{_chars(0x201C, 0x201D, 0x201E, 0x201F)}]")
# U+2010 hyphen, U+2011 non-breaking hyphen, U+2012 figure dash, U+2013 en
# dash, U+2014 em dash, U+2015 horizontal bar, U+2212 minus.
_DASHES = re.compile(f"[{_chars(*range(0x2010, 0x2016), 0x2212)}]")
# U+00A0 NBSP, U+2002-U+200A spaces, U+202F narrow NBSP, U+205F medium
# mathematical space, U+3000 ideographic space.
_SPECIAL_SPACES = re.compile(
    f"[\xa0{_chars(*range(0x2002, 0x200B), 0x202F, 0x205F, 0x3000)}]"
)
_LINES_WITH_ENDINGS = re.compile(r"[^\n]*\n|[^\n]+")


class EditError(Exception):
    """An edit failure whose message is Pi's error text."""


@dataclass(frozen=True, slots=True)
class Edit:
    old_text: str
    new_text: str


def split_bom(content: str) -> tuple[str, str]:
    """Pi ``splitBom``: ``(bom, text)``."""

    if content.startswith(BOM):
        return BOM, content[1:]
    return "", content


def detect_line_ending(content: str) -> str:
    crlf_index = content.find("\r\n")
    lf_index = content.find("\n")
    if lf_index == -1 or crlf_index == -1:
        return "\n"
    return "\r\n" if crlf_index < lf_index else "\n"


def normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    return text.replace("\n", "\r\n") if ending == "\r\n" else text


def normalize_for_fuzzy_match(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = "\n".join(
        line.rstrip(_JS_WHITESPACE) for line in normalized.split("\n")
    )
    normalized = _SMART_SINGLE.sub("'", normalized)
    normalized = _SMART_DOUBLE.sub('"', normalized)
    normalized = _DASHES.sub("-", normalized)
    return _SPECIAL_SPACES.sub(" ", normalized)


@dataclass(frozen=True, slots=True)
class _FuzzyMatch:
    found: bool
    index: int
    match_length: int
    used_fuzzy_match: bool


def fuzzy_find_text(content: str, old_text: str) -> _FuzzyMatch:
    exact = content.find(old_text)
    if exact != -1:
        return _FuzzyMatch(True, exact, len(old_text), False)
    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old = normalize_for_fuzzy_match(old_text)
    index = fuzzy_content.find(fuzzy_old)
    if index == -1:
        return _FuzzyMatch(False, -1, 0, False)
    return _FuzzyMatch(True, index, len(fuzzy_old), True)


def _count_occurrences(content: str, old_text: str) -> int:
    """Pi: ``fuzzyContent.split(fuzzyOldText).length - 1``.

    For a non-empty needle that is ``str.count``. An ``oldText`` of only
    trailing whitespace normalizes to ``""``, and ``split("")`` cuts the text
    into UTF-16 code units (``""`` gives no parts at all).
    """

    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old = normalize_for_fuzzy_match(old_text)
    if fuzzy_old:
        return fuzzy_content.count(fuzzy_old)
    return len(fuzzy_content.encode("utf-16-le")) // 2 - 1


@dataclass(frozen=True, slots=True)
class _Replacement:
    edit_index: int
    match_index: int
    match_length: int
    new_text: str


@dataclass(slots=True)
class _Group:
    start_line: int
    end_line: int
    replacements: list[_Replacement]


def _split_lines_with_endings(content: str) -> list[str]:
    return _LINES_WITH_ENDINGS.findall(content)


def _line_spans(content: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    offset = 0
    for line in _split_lines_with_endings(content):
        spans.append((offset, offset + len(line)))
        offset += len(line)
    return spans


def _replacement_line_range(
    lines: list[tuple[int, int]], replacement: _Replacement
) -> tuple[int, int]:
    start = replacement.match_index
    end = replacement.match_index + replacement.match_length
    start_line = -1
    for index, (line_start, line_end) in enumerate(lines):
        if line_start <= start < line_end:
            start_line = index
            break
    if start_line == -1:
        raise EditError("Replacement range is outside the base content.")
    end_line = start_line
    while end_line < len(lines) and lines[end_line][1] < end:
        end_line += 1
    if end_line >= len(lines):
        raise EditError("Replacement range is outside the base content.")
    return start_line, end_line + 1


def _apply_replacements(
    content: str, replacements: list[_Replacement], offset: int = 0
) -> str:
    result = content
    for replacement in reversed(replacements):
        index = replacement.match_index - offset
        result = (
            result[:index]
            + replacement.new_text
            + result[index + replacement.match_length :]
        )
    return result


def _apply_preserving_unchanged_lines(
    original: str, base: str, replacements: list[_Replacement]
) -> str:
    original_lines = _split_lines_with_endings(original)
    base_lines = _line_spans(base)
    if len(original_lines) != len(base_lines):
        raise EditError(
            "Cannot preserve unchanged lines because the base content has a "
            "different line count."
        )
    groups: list[_Group] = []
    for replacement in sorted(replacements, key=lambda item: item.match_index):
        start_line, end_line = _replacement_line_range(base_lines, replacement)
        if groups and start_line < groups[-1].end_line:
            groups[-1].end_line = max(groups[-1].end_line, end_line)
            groups[-1].replacements.append(replacement)
            continue
        groups.append(_Group(start_line, end_line, [replacement]))

    original_index = 0
    result: list[str] = []
    for group in groups:
        result.append("".join(original_lines[original_index : group.start_line]))
        group_start = base_lines[group.start_line][0]
        group_end = base_lines[group.end_line - 1][1]
        result.append(
            _apply_replacements(
                base[group_start:group_end], group.replacements, group_start
            )
        )
        original_index = group.end_line
    result.append("".join(original_lines[original_index:]))
    return "".join(result)


def _not_found_error(path: str, index: int, total: int) -> EditError:
    if total == 1:
        return EditError(
            f"Could not find the exact text in {path}. The old text must match "
            "exactly including all whitespace and newlines."
        )
    return EditError(
        f"Could not find edits[{index}] in {path}. The oldText must match "
        "exactly including all whitespace and newlines."
    )


def _duplicate_error(path: str, index: int, total: int, occurrences: int) -> EditError:
    if total == 1:
        return EditError(
            f"Found {occurrences} occurrences of the text in {path}. The text "
            "must be unique. Please provide more context to make it unique."
        )
    return EditError(
        f"Found {occurrences} occurrences of edits[{index}] in {path}. Each "
        "oldText must be unique. Please provide more context to make it unique."
    )


def _empty_old_text_error(path: str, index: int, total: int) -> EditError:
    if total == 1:
        return EditError(f"oldText must not be empty in {path}.")
    return EditError(f"edits[{index}].oldText must not be empty in {path}.")


def _no_change_error(path: str, total: int) -> EditError:
    if total == 1:
        return EditError(
            f"No changes made to {path}. The replacement produced identical "
            "content. This might indicate an issue with special characters or "
            "the text not existing as expected."
        )
    return EditError(
        f"No changes made to {path}. The replacements produced identical content."
    )


def apply_edits_to_normalized_content(
    normalized_content: str, edits: list[Edit], path: str
) -> tuple[str, str]:
    """Pi ``applyEditsToNormalizedContent``: ``(base_content, new_content)``."""

    normalized_edits = [
        Edit(normalize_to_lf(edit.old_text), normalize_to_lf(edit.new_text))
        for edit in edits
    ]
    total = len(normalized_edits)
    for index, edit in enumerate(normalized_edits):
        if not edit.old_text:
            raise _empty_old_text_error(path, index, total)

    used_fuzzy = any(
        fuzzy_find_text(normalized_content, edit.old_text).used_fuzzy_match
        for edit in normalized_edits
    )
    replacement_base = (
        normalize_for_fuzzy_match(normalized_content)
        if used_fuzzy
        else normalized_content
    )

    matched: list[_Replacement] = []
    for index, edit in enumerate(normalized_edits):
        match = fuzzy_find_text(replacement_base, edit.old_text)
        if not match.found:
            raise _not_found_error(path, index, total)
        occurrences = _count_occurrences(replacement_base, edit.old_text)
        if occurrences > 1:
            raise _duplicate_error(path, index, total, occurrences)
        matched.append(
            _Replacement(index, match.index, match.match_length, edit.new_text)
        )

    matched.sort(key=lambda item: item.match_index)
    for previous, current in zip(matched, matched[1:], strict=False):
        if previous.match_index + previous.match_length > current.match_index:
            raise EditError(
                f"edits[{previous.edit_index}] and edits[{current.edit_index}] "
                f"overlap in {path}. Merge them into one edit or target disjoint "
                "regions."
            )

    base_content = normalized_content
    new_content = (
        _apply_preserving_unchanged_lines(normalized_content, replacement_base, matched)
        if used_fuzzy
        else _apply_replacements(replacement_base, matched)
    )
    if base_content == new_content:
        raise _no_change_error(path, total)
    return base_content, new_content


# -- jsdiff 8.0.4 diffLines ---------------------------------------------------


@dataclass(slots=True)
class DiffPart:
    """One jsdiff change object."""

    count: int
    added: bool
    removed: bool
    previous: DiffPart | None = None
    value: str = ""


@dataclass(slots=True)
class _Path:
    old_pos: int
    last: DiffPart | None


def _tokenize_lines(value: str) -> list[str]:
    parts = re.split(r"(\n|\r\n)", value)
    if not parts[-1]:
        parts.pop()
    tokens: list[str] = []
    for index, part in enumerate(parts):
        if index % 2:
            tokens[-1] += part
        else:
            tokens.append(part)
    return [token for token in tokens if token]


def _add_to_path(path: _Path, *, added: bool, removed: bool, old_inc: int) -> _Path:
    last = path.last
    if last is not None and last.added == added and last.removed == removed:
        return _Path(
            path.old_pos + old_inc,
            DiffPart(last.count + 1, added, removed, last.previous),
        )
    return _Path(path.old_pos + old_inc, DiffPart(1, added, removed, last))


def _same(left: str, right: str) -> bool:
    return left == right


def _extract_common(
    path: _Path,
    new_tokens: list[str],
    old_tokens: list[str],
    diagonal: int,
    equals: Callable[[str, str], bool] = _same,
) -> int:
    old_pos = path.old_pos
    new_pos = old_pos - diagonal
    common = 0
    while (
        new_pos + 1 < len(new_tokens)
        and old_pos + 1 < len(old_tokens)
        and equals(old_tokens[old_pos + 1], new_tokens[new_pos + 1])
    ):
        new_pos += 1
        old_pos += 1
        common += 1
    if common:
        path.last = DiffPart(common, False, False, path.last)
    path.old_pos = old_pos
    return new_pos


def _build_values(
    last: DiffPart | None,
    new_tokens: list[str],
    old_tokens: list[str],
    join: Callable[[list[str]], str] = "".join,
) -> list[DiffPart]:
    components: list[DiffPart] = []
    while last is not None:
        components.append(last)
        last, components[-1].previous = last.previous, None
    components.reverse()
    new_pos = old_pos = 0
    for component in components:
        if not component.removed:
            component.value = join(new_tokens[new_pos : new_pos + component.count])
            new_pos += component.count
            if not component.added:
                old_pos += component.count
        else:
            component.value = join(old_tokens[old_pos : old_pos + component.count])
            old_pos += component.count
    return components


class _Walk:
    """The state of jsdiff's ``diffWithOptionsObj`` walk."""

    def __init__(
        self,
        old_tokens: list[str],
        new_tokens: list[str],
        equals: Callable[[str, str], bool] = _same,
    ) -> None:
        self.old_tokens = old_tokens
        self.new_tokens = new_tokens
        self.equals = equals
        self.best: dict[int, _Path | None] = {}
        self.min_diagonal = -(len(old_tokens) + len(new_tokens) + 1)
        self.max_diagonal = len(old_tokens) + len(new_tokens) + 1

    def done(self, path: _Path, new_pos: int) -> bool:
        return path.old_pos + 1 >= len(self.old_tokens) and new_pos + 1 >= len(
            self.new_tokens
        )

    def step(self, diagonal: int) -> _Path | None:
        """Extend the best path onto ``diagonal``; return it when it ends."""

        remove_path = self.best.get(diagonal - 1)
        add_path = self.best.get(diagonal + 1)
        if remove_path is not None:
            self.best[diagonal - 1] = None
        can_add = add_path is not None and (
            0 <= add_path.old_pos - diagonal < len(self.new_tokens)
        )
        can_remove = remove_path is not None and remove_path.old_pos + 1 < len(
            self.old_tokens
        )
        if not can_add and not can_remove:
            self.best[diagonal] = None
            return None
        # Branch from the path farthest along the old text that stays in bounds.
        if add_path is not None and (
            remove_path is None
            or not can_remove
            or (can_add and remove_path.old_pos < add_path.old_pos)
        ):
            base = _add_to_path(add_path, added=True, removed=False, old_inc=0)
        else:
            assert remove_path is not None
            base = _add_to_path(remove_path, added=False, removed=True, old_inc=1)
        new_pos = _extract_common(
            base, self.new_tokens, self.old_tokens, diagonal, self.equals
        )
        if self.done(base, new_pos):
            return base
        self.best[diagonal] = base
        if base.old_pos + 1 >= len(self.old_tokens):
            self.max_diagonal = min(self.max_diagonal, diagonal - 1)
        if new_pos + 1 >= len(self.new_tokens):
            self.min_diagonal = max(self.min_diagonal, diagonal + 1)
        return None


def diff_lines(old: str, new: str) -> list[DiffPart]:
    """jsdiff ``diffLines(old, new)`` with no options."""

    return diff_tokens(_tokenize_lines(old), _tokenize_lines(new))


def diff_tokens(
    old_tokens: list[str],
    new_tokens: list[str],
    *,
    equals: Callable[[str, str], bool] = _same,
    join: Callable[[list[str]], str] = "".join,
) -> list[DiffPart]:
    """jsdiff ``Diff.diffWithOptionsObj`` over non-empty tokens.

    ``equals`` and ``join`` are the subclass's ``equals``/``join`` (the line
    diff uses plain equality and concatenation, the word diff its own).
    """

    walk = _Walk(old_tokens, new_tokens, equals)
    root = _Path(-1, None)
    walk.best[0] = root
    new_pos = _extract_common(root, new_tokens, old_tokens, 0, equals)
    if walk.done(root, new_pos):
        return _build_values(root.last, new_tokens, old_tokens, join)
    for edit_length in range(1, len(old_tokens) + len(new_tokens) + 1):
        diagonal = max(walk.min_diagonal, -edit_length)
        while diagonal <= min(walk.max_diagonal, edit_length):
            finished = walk.step(diagonal)
            if finished is not None:
                return _build_values(finished.last, new_tokens, old_tokens, join)
            diagonal += 2
    raise AssertionError("diff_tokens did not converge")  # pragma: no cover


class _DiffWriter:
    """The output state of Pi's ``generateDiffString``."""

    def __init__(self, width: int, context_lines: int) -> None:
        self.width = width
        self.context_lines = context_lines
        self.output: list[str] = []
        self.old_line = 1
        self.new_line = 1
        self.first_changed: int | None = None

    def change(self, lines: list[str], *, added: bool) -> None:
        if self.first_changed is None:
            self.first_changed = self.new_line
        for line in lines:
            if added:
                self.output.append(f"+{str(self.new_line).rjust(self.width)} {line}")
                self.new_line += 1
            else:
                self.output.append(f"-{str(self.old_line).rjust(self.width)} {line}")
                self.old_line += 1

    def context(self, lines: list[str], *, leading: bool, trailing: bool) -> None:
        """Unchanged lines; ``leading``/``trailing`` = a change before/after."""

        n = self.context_lines
        if leading and trailing:
            if len(lines) <= n * 2:
                self._show(lines)
            else:
                self._show(lines[:n])
                self._skip(len(lines) - 2 * n)
                self._show(lines[len(lines) - n :])
        elif leading:
            self._show(lines[:n])
            if len(lines) > n:
                self._skip(len(lines) - n)
        elif trailing:
            skipped = max(0, len(lines) - n)
            if skipped > 0:
                self._skip(skipped)
            self._show(lines[skipped:])
        else:
            self.old_line += len(lines)
            self.new_line += len(lines)

    def _show(self, lines: list[str]) -> None:
        for line in lines:
            self.output.append(f" {str(self.old_line).rjust(self.width)} {line}")
            self.old_line += 1
            self.new_line += 1

    def _skip(self, count: int) -> None:
        self.output.append(f" {' ' * self.width} ...")
        self.old_line += count
        self.new_line += count


def generate_diff_string(
    old_content: str, new_content: str, context_lines: int = 4
) -> tuple[str, int | None]:
    """Pi ``generateDiffString``: ``(diff, first_changed_line)``."""

    parts = diff_lines(old_content, new_content)
    max_line_num = max(len(old_content.split("\n")), len(new_content.split("\n")))
    writer = _DiffWriter(len(str(max_line_num)), context_lines)
    last_was_change = False
    for index, part in enumerate(parts):
        raw = part.value.split("\n")
        if raw[-1] == "":
            raw.pop()
        if part.added or part.removed:
            writer.change(raw, added=part.added)
            last_was_change = True
            continue
        next_is_change = index < len(parts) - 1 and (
            parts[index + 1].added or parts[index + 1].removed
        )
        writer.context(raw, leading=last_was_change, trailing=next_is_change)
        last_was_change = False
    return "\n".join(writer.output), writer.first_changed


__all__ = [
    "DiffPart",
    "Edit",
    "EditError",
    "apply_edits_to_normalized_content",
    "detect_line_ending",
    "diff_lines",
    "diff_tokens",
    "fuzzy_find_text",
    "generate_diff_string",
    "normalize_for_fuzzy_match",
    "normalize_to_lf",
    "restore_line_endings",
    "split_bom",
]

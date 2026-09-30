"""jsdiff ``diffWords``, as Pi's edit diff renderer calls it.

Port of jsdiff 8.0.4 ``libesm/diff/word.js`` (no options) over the Myers walk
of :func:`pipy_harness.native.tools.edit_diff.diff_tokens`: the tokenizer that
glues surrounding whitespace onto words and punctuation, ``equals`` on trimmed
tokens, the ``join`` that drops each later token's leading whitespace, and the
``postProcess`` whitespace dedupe between keep/insert/delete runs
(``libesm/util/string.js``).

JavaScript's ``\\s`` and ``trim()`` use the ECMAScript WhiteSpace and
LineTerminator sets, which differ from Python's (BOM is whitespace in
JavaScript; ``\\x1c``-``\\x1f`` are not), so both are spelled out.
Pi's ``modes/interactive/components/diff.ts`` only diffs one removed line
against one added line; the strings are small.
"""

from __future__ import annotations

import re

from pipy_harness.native.ansi_wrap import JS_WHITESPACE
from pipy_harness.native.tools.edit_diff import DiffPart, diff_tokens


def _chars(*codepoints: int) -> str:
    return "".join(chr(codepoint) for codepoint in codepoints)


_WS_CLASS = "".join(f"\\u{ord(char):04x}" for char in JS_WHITESPACE)
# jsdiff `extendedWordChars`.
_WORD_CLASS = (
    "a-zA-Z0-9_"
    + _chars(0xAD)
    + _chars(0xC0)
    + "-"
    + _chars(0xD6)
    + _chars(0xD8)
    + "-"
    + _chars(0xF6)
    + _chars(0xF8)
    + "-"
    + _chars(0x2C6)
    + _chars(0x2C8)
    + "-"
    + _chars(0x2D7)
    + _chars(0x2DE)
    + "-"
    + _chars(0x2FF)
    + _chars(0x1E00)
    + "-"
    + _chars(0x1EFF)
)
_TOKEN = re.compile(f"[{_WORD_CLASS}]+|[{_WS_CLASS}]+|[^{_WORD_CLASS}]")
_HAS_WS = re.compile(f"[{_WS_CLASS}]")
_LEADING_WS = re.compile(f"^[{_WS_CLASS}]+")


def _js_trim(text: str) -> str:
    return text.strip(JS_WHITESPACE)


def _leading_ws(text: str) -> str:
    match = _LEADING_WS.match(text)
    return match.group(0) if match else ""


def _trailing_ws(text: str) -> str:
    return text[len(text.rstrip(JS_WHITESPACE)) :]


def _tokenize(value: str) -> list[str]:
    tokens: list[str] = []
    prev_part: str | None = None
    for part in _TOKEN.findall(value):
        if _HAS_WS.search(part):
            if prev_part is None:
                tokens.append(part)
            else:
                tokens.append(tokens.pop() + part)
        elif prev_part is not None and _HAS_WS.search(prev_part):
            if tokens[-1] == prev_part:
                tokens.append(tokens.pop() + part)
            else:
                tokens.append(prev_part + part)
        else:
            tokens.append(part)
        prev_part = part
    return tokens


def _join(tokens: list[str]) -> str:
    return "".join(
        token if index == 0 else _LEADING_WS.sub("", token, count=1)
        for index, token in enumerate(tokens)
    )


def _equals(left: str, right: str) -> bool:
    return _js_trim(left) == _js_trim(right)


def _common_prefix(a: str, b: str) -> str:
    index = 0
    while index < len(a) and index < len(b) and a[index] == b[index]:
        index += 1
    return a[:index]


def _common_suffix(a: str, b: str) -> str:
    if not a or not b or a[-1] != b[-1]:
        return ""
    index = 0
    while index < len(a) and index < len(b):
        if a[len(a) - (index + 1)] != b[len(b) - (index + 1)]:
            return a[-index:] if index else ""
        index += 1
    return a[-index:] if index else ""


def _replace_prefix(text: str, old: str, new: str) -> str:
    if not text.startswith(old):  # pragma: no cover - jsdiff: "this is a bug"
        raise ValueError(f"{text!r} doesn't start with prefix {old!r}")
    return new + text[len(old) :]


def _replace_suffix(text: str, old: str, new: str) -> str:
    if not old:
        return text + new
    if not text.endswith(old):  # pragma: no cover - jsdiff: "this is a bug"
        raise ValueError(f"{text!r} doesn't end with suffix {old!r}")
    return text[: -len(old)] + new


def _maximum_overlap(a: str, b: str) -> str:
    """jsdiff ``maximumOverlap``: the longest suffix of ``a`` that prefixes ``b``."""

    for size in range(min(len(a), len(b)), 0, -1):
        if a[-size:] == b[:size]:
            return b[:size]
    return ""


def _dedupe(
    start: DiffPart | None,
    deletion: DiffPart | None,
    insertion: DiffPart | None,
    end: DiffPart | None,
) -> None:
    if deletion is not None and insertion is not None:
        old_prefix, old_suffix = (
            _leading_ws(deletion.value),
            _trailing_ws(deletion.value),
        )
        new_prefix, new_suffix = (
            _leading_ws(insertion.value),
            _trailing_ws(insertion.value),
        )
        if start is not None:
            common = _common_prefix(old_prefix, new_prefix)
            start.value = _replace_suffix(start.value, new_prefix, common)
            deletion.value = _replace_prefix(deletion.value, common, "")
            insertion.value = _replace_prefix(insertion.value, common, "")
        if end is not None:
            common = _common_suffix(old_suffix, new_suffix)
            end.value = _replace_prefix(end.value, new_suffix, common)
            deletion.value = _replace_suffix(deletion.value, common, "")
            insertion.value = _replace_suffix(insertion.value, common, "")
    elif insertion is not None:
        if start is not None:
            insertion.value = insertion.value[len(_leading_ws(insertion.value)) :]
        if end is not None:
            end.value = end.value[len(_leading_ws(end.value)) :]
    elif deletion is not None and start is not None and end is not None:
        new_ws_full = _leading_ws(end.value)
        del_start, del_end = _leading_ws(deletion.value), _trailing_ws(deletion.value)
        new_ws_start = _common_prefix(new_ws_full, del_start)
        deletion.value = _replace_prefix(deletion.value, new_ws_start, "")
        new_ws_end = _common_suffix(
            _replace_prefix(new_ws_full, new_ws_start, ""), del_end
        )
        deletion.value = _replace_suffix(deletion.value, new_ws_end, "")
        end.value = _replace_prefix(end.value, new_ws_full, new_ws_end)
        start.value = _replace_suffix(
            start.value,
            new_ws_full,
            new_ws_full[: len(new_ws_full) - len(new_ws_end)],
        )
    elif deletion is not None and end is not None:
        overlap = _maximum_overlap(_trailing_ws(deletion.value), _leading_ws(end.value))
        deletion.value = _replace_suffix(deletion.value, overlap, "")
    elif deletion is not None and start is not None:
        overlap = _maximum_overlap(
            _trailing_ws(start.value), _leading_ws(deletion.value)
        )
        deletion.value = _replace_prefix(deletion.value, overlap, "")


def _post_process(changes: list[DiffPart]) -> list[DiffPart]:
    last_keep: DiffPart | None = None
    insertion: DiffPart | None = None
    deletion: DiffPart | None = None
    for change in changes:
        if change.added:
            insertion = change
        elif change.removed:
            deletion = change
        else:
            if insertion is not None or deletion is not None:
                _dedupe(last_keep, deletion, insertion, change)
            last_keep = change
            insertion = None
            deletion = None
    if insertion is not None or deletion is not None:
        _dedupe(last_keep, deletion, insertion, None)
    return changes


def diff_words(old: str, new: str) -> list[DiffPart]:
    """jsdiff ``diffWords(old, new)`` with no options."""

    old_tokens = [token for token in _tokenize(old) if token]
    new_tokens = [token for token in _tokenize(new) if token]
    return _post_process(
        diff_tokens(old_tokens, new_tokens, equals=_equals, join=_join)
    )


__all__ = ["JS_WHITESPACE", "diff_words"]

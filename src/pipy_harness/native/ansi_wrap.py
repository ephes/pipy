"""ANSI-aware word wrapping, ported from Pi's ``wrapTextWithAnsi``.

Mirrors ``packages/tui/src/utils.ts`` (pi-mono ``1b347794e``):
``AnsiCodeTracker`` (SGR state carried onto the next line),
``splitIntoTokensWithAnsi`` (runs of spaces and of non-spaces, SGR codes glued
to the next visible text), ``wrapSingleLine`` (words move to the next line,
a word longer than the width is broken, trailing whitespace trimmed) and
``breakLongWord``.

Simplifications, as the rest of pipy's frame renderer measures text: every
code point is one column (no East Asian width, no grapheme clusters, no CJK
break opportunities) and only SGR sequences and OSC 8 hyperlinks (the
``ESC ]8;;<url> ESC \\`` form ``hyperlink`` writes) are recognized; the
frame renderer drops every other escape. As in Pi, a link open at the end
of a wrapped line is closed there and opened again on the next one.
"""

from __future__ import annotations

import re

# ECMAScript WhiteSpace + LineTerminator (`String.prototype.trim`).
JS_WHITESPACE = "\t\n\x0b\x0c\r \xa0" + "".join(
    chr(codepoint)
    for codepoint in (
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
        0xFEFF,
    )
)
# An SGR sequence (group 1: its parameters) or an OSC 8 hyperlink (group 2:
# the URL, empty to close the link), terminated by ST.
_ESCAPE = re.compile(r"\x1b\[([0-9;]*)m|\x1b\]8;;([!-~]{0,4096})\x1b\\")
OSC8_CLOSE = "\x1b]8;;\x1b\\"


def hyperlink(text: str, url: str) -> str:
    """Pi ``hyperlink``: ``text`` as an OSC 8 link to ``url``.

    ``url`` must be printable ASCII without spaces, or the frame renderer
    will not keep the link."""

    return f"\x1b]8;;{url}\x1b\\{text}{OSC8_CLOSE}"


class SgrTracker:
    """Pi ``AnsiCodeTracker`` for SGR codes."""

    __slots__ = ("_flags", "bg", "fg", "link")

    _FLAG_ON = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 7: "7", 8: "8", 9: "9"}
    _FLAG_OFF: dict[int, tuple[str, ...]] = {
        21: ("1",),
        22: ("1", "2"),
        23: ("3",),
        24: ("4",),
        25: ("5",),
        27: ("7",),
        28: ("8",),
        29: ("9",),
    }

    def __init__(self) -> None:
        self._flags: set[str] = set()
        self.fg: str | None = None
        self.bg: str | None = None
        # The URL of the OSC 8 link that is open; an SGR reset keeps it.
        self.link: str | None = None

    def reset(self) -> None:
        self._flags.clear()
        self.fg = None
        self.bg = None

    def process(self, params: str) -> None:
        """Apply one SGR parameter string (the text between ``\\x1b[`` and ``m``)."""

        if params in {"", "0"}:
            self.reset()
            return
        parts = params.split(";")
        index = 0
        while index < len(parts):
            try:
                code = int(parts[index])
            except ValueError:
                code = -1
            if code in {38, 48}:
                color = self._extended_color(parts, index)
                if color is not None:
                    value, consumed = color
                    if code == 38:
                        self.fg = value
                    else:
                        self.bg = value
                    index += consumed
                    continue
            self._apply(code)
            index += 1

    @staticmethod
    def _extended_color(parts: list[str], index: int) -> tuple[str, int] | None:
        if index + 2 < len(parts) and parts[index + 1] == "5":
            return ";".join(parts[index : index + 3]), 3
        if index + 4 < len(parts) and parts[index + 1] == "2":
            return ";".join(parts[index : index + 5]), 5
        return None

    def _apply(self, code: int) -> None:
        if code == 0:
            self.reset()
        elif code in self._FLAG_ON:
            self._flags.add(self._FLAG_ON[code])
        elif code in self._FLAG_OFF:
            self._flags.difference_update(self._FLAG_OFF[code])
        elif code == 39:
            self.fg = None
        elif code == 49:
            self.bg = None
        elif 30 <= code <= 37 or 90 <= code <= 97:
            self.fg = str(code)
        elif 40 <= code <= 47 or 100 <= code <= 107:
            self.bg = str(code)

    def apply(self, match: re.Match[str]) -> None:
        """Apply one match of the escape pattern: SGR or OSC 8."""

        if match.group(1) is not None:
            self.process(match.group(1))
        else:
            self.link = match.group(2) or None

    def update(self, text: str) -> None:
        for match in _ESCAPE.finditer(text):
            self.apply(match)

    def active_codes(self) -> str:
        codes = [flag for flag in "12345789" if flag in self._flags]
        if self.fg:
            codes.append(self.fg)
        if self.bg:
            codes.append(self.bg)
        sgr = f"\x1b[{';'.join(codes)}m" if codes else ""
        return sgr + (f"\x1b]8;;{self.link}\x1b\\" if self.link else "")

    def line_end_reset(self) -> str:
        underline = "\x1b[24m" if "4" in self._flags else ""
        return underline + (OSC8_CLOSE if self.link else "")


def visible_width(text: str) -> int:
    return len(_ESCAPE.sub("", text))


def _js_trim_end(text: str) -> str:
    """``String.prototype.trimEnd``: an SGR code at the end stops it."""

    return text.rstrip(JS_WHITESPACE)


def split_lines_with_carry(text: str) -> list[str]:
    """Split on newlines, prefixing each later line with the carried SGR codes.

    The per-line half of Pi ``wrapTextWithAnsi``: a style opened on one line
    (``theme.fg("muted", "\\n... (3 more lines,")``) still colours the next.
    """

    lines: list[str] = []
    tracker = SgrTracker()
    for index, line in enumerate(re.split(r"\r\n|\r|\n", text)):
        prefix = tracker.active_codes() if index > 0 else ""
        lines.append(prefix + line)
        tracker.update(line)
    return lines


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    current = ""
    kind: str | None = None
    pending = ""
    index = 0
    while index < len(text):
        match = _ESCAPE.match(text, index)
        if match is not None:
            pending += match.group(0)
            index = match.end()
            continue
        char = text[index]
        char_kind = "space" if char == " " else "word"
        if current and kind != char_kind:
            tokens.append(current)
            current = ""
        if pending:
            current += pending
            pending = ""
        kind = char_kind
        current += char
        index += 1
    if pending:
        if current:
            current += pending
        elif tokens:
            tokens[-1] += pending
        else:
            current = pending
    if current:
        tokens.append(current)
    return tokens


def _break_long_word(word: str, width: int, tracker: SgrTracker) -> list[str]:
    lines: list[str] = []
    current = tracker.active_codes()
    current_width = 0
    index = 0
    while index < len(word):
        match = _ESCAPE.match(word, index)
        if match is not None:
            current += match.group(0)
            tracker.apply(match)
            index = match.end()
            continue
        if current_width + 1 > width:
            current += tracker.line_end_reset()
            lines.append(current)
            current = tracker.active_codes()
            current_width = 0
        current += word[index]
        current_width += 1
        index += 1
    if current:
        lines.append(current)
    return lines or [""]


def _wrap_single_line(line: str, width: int) -> list[str]:
    if not line:
        return [""]
    if visible_width(line) <= width:
        return [line]
    wrapped: list[str] = []
    tracker = SgrTracker()
    current = ""
    current_width = 0
    for token in _tokens(line):
        token_width = visible_width(token)
        is_space = token.strip(JS_WHITESPACE) == ""
        if token_width > width and not is_space:
            if current:
                current += tracker.line_end_reset()
                wrapped.append(current)
                current = ""
                current_width = 0
            broken = _break_long_word(token, width, tracker)
            wrapped.extend(broken[:-1])
            current = broken[-1]
            current_width = visible_width(current)
            continue
        if current_width + token_width > width and current_width > 0:
            wrapped.append(_js_trim_end(current) + tracker.line_end_reset())
            if is_space:
                current = tracker.active_codes()
                current_width = 0
            else:
                current = tracker.active_codes() + token
                current_width = token_width
        else:
            current += token
            current_width += token_width
        tracker.update(token)
    if current:
        wrapped.append(current)
    return [_js_trim_end(line) for line in wrapped] if wrapped else [""]


def wrap_text_with_ansi(text: str, width: int) -> list[str]:
    """Pi ``wrapTextWithAnsi``: lines of at most ``width`` visible columns."""

    if not text:
        return [""]
    result: list[str] = []
    for line in split_lines_with_carry(text):
        result.extend(_wrap_single_line(line, max(1, width)))
    return result or [""]


__all__ = [
    "OSC8_CLOSE",
    "SgrTracker",
    "hyperlink",
    "split_lines_with_carry",
    "visible_width",
    "wrap_text_with_ansi",
]

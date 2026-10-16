"""One path segment of a glob, compiled as Pi's minimatch compiles it.

A port of minimatch 10.2.6's ``AST`` (``ast.js``), ``parseClass``
(``brace-expressions.js``), ``unescape`` and the fast tests of
``Minimatch.parse``, with its default options (no ``dot``, ``nocase`` or
``noext``). :func:`segment_matcher` turns a segment such as ``+(a|b)*.md``
into a test of one path segment, so extended globs, character classes and
their quirks behave as in Pi: ``!(...)`` looks at the rest of the pattern,
nested extended globs are merged or, beyond two levels, read as text, and
an empty ``!()`` matches any non-empty text.

minimatch builds a JavaScript regular expression. The port builds the same
expression for Python's ``re`` with three changes: ``$`` becomes ``\\Z``;
a POSIX class that minimatch writes as a Unicode property (``\\p{L}``,
which ``re`` lacks) lists its characters, from the tables Node's engine
gives (:mod:`pipy_harness.native.unicode_properties`); and
since JavaScript reads strings in UTF-16 code units unless a POSIX class
sets the ``u`` flag, the pattern and the segment are compared in code units
then, so ``?`` does not match an emoji. A pattern that Python cannot
compile (minimatch would raise for most of them) matches nothing.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache
from typing import Final

from pipy_harness.native.unicode_properties import CATEGORY_RANGES

_EXTGLOB_TYPES: Final = frozenset("!?+*@")
# Which extended globs can take in the alternatives of a nested one.
_ADOPTION: Final[dict[str, str]] = {
    "!": "@",
    "?": "?@",
    "@": "@",
    "*": "*+?@",
    "+": "+@",
}
# ... with a blank alternative added.
_ADOPTION_WITH_SPACE: Final[dict[str, str]] = {"!": "?", "@": "?", "+": "?*"}
_ADOPTION_ANY: Final[dict[str, str]] = {
    "!": "?@",
    "?": "?@",
    "@": "?@",
    "*": "*+?@",
    "+": "+@?*",
}
# An only child that takes over its parent: parent -> child -> new type.
_USURP: Final[dict[str, dict[str, str]]] = {
    "!": {"!": "@"},
    "?": {"*": "*", "+": "*"},
    "@": {"!": "!", "?": "?", "@": "@", "*": "*", "+": "+"},
    "+": {"?": "*", "*": "*"},
}
_MAX_EXTGLOB_RECURSION: Final = 2
_START_NO_TRAVERSAL: Final = r"(?!(?:^|/)\.\.?(?:$|/))"
_START_NO_DOT: Final = r"(?!\.)"
_ADD_PATTERN_START: Final = frozenset("[.")
_RE_SPECIALS: Final = frozenset("().*{}+?[]^$\\!")
_QMARK: Final = "[^/]"
_STAR: Final = _QMARK + "*?"
_STAR_NO_EMPTY: Final = _QMARK + "+?"
# JavaScript's `\s`.
_JS_SPACE: Final = (
    "\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_REGEXP_SPECIAL: Final = re.compile(f"[-[\\]{{}}()*+?.,\\\\^$|#{_JS_SPACE}]")
# JavaScript's `.`.
_JS_ANY: Final = "[^\n\r\u2028\u2029]"
_UNESCAPE_SET: Final = re.compile(f"((?!\\\\){_JS_ANY}|^)\\[([^/\\\\])\\]")
_UNESCAPE_CHAR: Final = re.compile(r"\\([^/])")
_SINGLE_ESCAPED: Final = re.compile(f"\\\\?{_JS_ANY}")
_ASTRAL: Final = re.compile("[\U00010000-\U0010ffff]")

# minimatch's posixClasses: the Unicode general categories and characters
# of each class, whether it needs the `u` flag, and whether minimatch puts
# them in a negated set.
_POSIX_CLASSES: Final[dict[str, tuple[tuple[str, ...], str, bool, bool]]] = {
    "[:alnum:]": (("L", "Nl", "Nd"), "", True, False),
    "[:alpha:]": (("L", "Nl"), "", True, False),
    "[:ascii:]": ((), "", False, False),
    "[:blank:]": (("Zs",), "\t", True, False),
    "[:cntrl:]": (("Cc",), "", True, False),
    "[:digit:]": (("Nd",), "", True, False),
    "[:graph:]": (("Z", "C"), "", True, True),
    "[:lower:]": (("Ll",), "", True, False),
    "[:print:]": (("C",), "", True, False),
    "[:punct:]": (("P",), "", True, False),
    "[:space:]": (("Z",), "\t\r\n\v\f", True, False),
    "[:upper:]": (("Lu",), "", True, False),
    "[:word:]": (("L", "Nl", "Nd", "Pc"), "", True, False),
    "[:xdigit:]": ((), "", False, False),
}
_ASCII_CLASS_SOURCE: Final = {"[:ascii:]": r"\x00-\x7f", "[:xdigit:]": "A-Fa-f0-9"}

# minimatch's fast tests for the commonest segments.
_STAR_ONLY: Final = re.compile(r"\*+")
_STAR_DOT_EXT: Final = re.compile(r"\*+([^+@!?*\[(]*)")
_QMARKS: Final = re.compile(r"\?+([^+@!?*\[(]*)?")
_STAR_DOT_STAR: Final = re.compile(r"\*+\.\*+")
_DOT_STAR: Final = re.compile(r"\.\*+")


def segment_matcher(pattern: str) -> Callable[[str], bool]:
    """A test of one path segment (no ``/``) against ``pattern``, a pattern
    segment other than ``**`` and ``""``, as minimatch's ``parse``.

    Extended globs nested deeper than Python's recursion limit raise
    ``RecursionError``.
    """

    return _segment_matcher(_utf16(pattern))


@lru_cache(maxsize=1024)
def _segment_matcher(pattern: str) -> Callable[[str], bool]:
    fast = _fast_test(pattern)
    compiled = _Ast.from_glob(pattern).to_mm_pattern()
    if isinstance(compiled, str):
        return lambda segment: _utf16(segment) == compiled
    expression, unicode = compiled
    if fast is not None:
        return lambda segment: fast(_utf16(segment))
    try:
        regex = re.compile(expression)
    except (re.error, RecursionError, OverflowError):
        return lambda segment: False
    # As JavaScript's `test`, which finds a match anywhere: the expression
    # is anchored at both ends, unless an escaped `|`, which minimatch
    # leaves as it is, splits it.
    if unicode:
        return lambda segment: regex.search(segment) is not None
    return lambda segment: regex.search(_utf16(segment)) is not None


def _fast_test(pattern: str) -> Callable[[str], bool] | None:
    if _STAR_ONLY.fullmatch(pattern):
        return lambda f: len(f) != 0 and not f.startswith(".")
    if match := _STAR_DOT_EXT.fullmatch(pattern):
        ext = match[1]
        return lambda f: not f.startswith(".") and f.endswith(ext)
    if match := _QMARKS.fullmatch(pattern):
        size = len(match[0])
        qext = match[1] or ""
        return lambda f: len(f) == size and not f.startswith(".") and f.endswith(qext)
    if _STAR_DOT_STAR.fullmatch(pattern):
        return lambda f: not f.startswith(".") and "." in f
    if _DOT_STAR.fullmatch(pattern):
        return lambda f: f not in (".", "..") and f.startswith(".")
    return None


def _utf16(text: str) -> str:
    """``text`` with each character above U+FFFF as its two surrogates."""

    if _ASTRAL.search(text) is None:
        return text
    return _ASTRAL.sub(_surrogates, text)


def _surrogates(match: re.Match[str]) -> str:
    offset = ord(match[0]) - 0x10000
    return chr(0xD800 + (offset >> 10)) + chr(0xDC00 + (offset & 0x3FF))


def _code_points(text: str) -> str:
    """``text`` with each surrogate pair as the character it stands for."""

    return text.encode("utf-16-le", "surrogatepass").decode(
        "utf-16-le", "surrogatepass"
    )


def _regexp_escape(text: str) -> str:
    return _REGEXP_SPECIAL.sub(lambda match: "\\" + match[0], text)


def _unescape(text: str) -> str:
    """minimatch's ``unescape``: ``[x]`` and ``\\x`` become ``x``."""

    return _UNESCAPE_CHAR.sub(r"\1", _UNESCAPE_SET.sub(r"\1\2", text))


def _python_regex(source: str) -> str:
    """A JavaScript expression as Python reads it: ``$`` outside a set is
    the end of the text (``\\Z``)."""

    out: list[str] = []
    index = 0
    in_set = False
    while index < len(source):
        char = source[index]
        if char == "\\":
            out.append(source[index : index + 2])
            index += 2
            continue
        if in_set:
            in_set = char != "]"
        elif char == "[":
            in_set = True
            if source.startswith("^", index + 1):
                out.append("[^")
                index += 2
                continue
        elif char == "$":
            char = r"\Z"
        out.append(char)
        index += 1
    return "".join(out)


class _Ast:
    """minimatch's ``AST``: a run of text and extended globs, or one
    extended glob whose parts are its alternatives."""

    def __init__(self, kind: str | None, parent: _Ast | None) -> None:
        self.type = kind
        self.has_magic: bool | None = True if kind else None
        self.uflag = False
        self.parts: list[str | _Ast] = []
        self.parent = parent
        self.root: _Ast = parent.root if parent is not None else self
        self.negs: list[_Ast] = self.root.negs if parent is not None else []
        self.filled_negs = False
        self.empty_ext = False
        if kind == "!" and not self.root.filled_negs:
            self.negs.append(self)
        self.parent_index = len(parent.parts) if parent is not None else 0

    def __str__(self) -> str:
        if self.type is None:
            return "".join(map(str, self.parts))
        return self.type + "(" + "|".join(map(str, self.parts)) + ")"

    def push(self, *parts: str | _Ast) -> None:
        self.parts.extend(part for part in parts if part != "")

    def is_start(self) -> bool:
        if self.root is self:
            return True
        parent = self.parent
        assert parent is not None
        if not parent.is_start():
            return False
        if self.parent_index == 0:
            return True
        # Still the start if only negations come before it.
        return all(
            isinstance(before, _Ast) and before.type == "!"
            for before in parent.parts[: self.parent_index]
        )

    def is_end(self) -> bool:
        if self.root is self:
            return True
        parent = self.parent
        assert parent is not None
        if parent.type == "!":
            return True
        if not parent.is_end():
            return False
        if self.type is None:
            return True
        return self.parent_index == len(parent.parts) - 1

    def copy_in(self, part: str | _Ast) -> None:
        self.push(part if isinstance(part, str) else part.clone(self))

    def clone(self, parent: _Ast) -> _Ast:
        copy = _Ast(self.type, parent)
        for part in self.parts:
            copy.copy_in(part)
        return copy

    @classmethod
    def from_glob(cls, pattern: str) -> _Ast:
        ast = cls(None, None)
        _parse(pattern, ast, 0, 0)
        return ast

    # -- flattening -------------------------------------------------------

    def _only_extglob_child(self, child: str | _Ast) -> _Ast | None:
        if (
            not isinstance(child, _Ast)
            or child.type is not None
            or len(child.parts) != 1
            or self.type is None
        ):
            return None
        grandchild = child.parts[0]
        if not isinstance(grandchild, _Ast) or grandchild.type is None:
            return None
        return grandchild

    def _can_adopt(self, child: str | _Ast, table: dict[str, str]) -> bool:
        grandchild = self._only_extglob_child(child)
        return (
            grandchild is not None
            and self.type is not None
            and grandchild.type is not None
            and grandchild.type in table.get(self.type, "")
        )

    def _adopt(self, child: _Ast, index: int) -> None:
        grandchild = child.parts[0]
        assert isinstance(grandchild, _Ast)
        self.parts[index : index + 1] = grandchild.parts
        for part in grandchild.parts:
            if isinstance(part, _Ast):
                part.parent = self

    def _adopt_with_space(self, child: _Ast, index: int) -> None:
        grandchild = child.parts[0]
        assert isinstance(grandchild, _Ast)
        blank = _Ast(None, grandchild)
        blank.parts.append("")
        grandchild.push(blank)
        self._adopt(child, index)

    def _can_usurp(self, child: str | _Ast) -> bool:
        if len(self.parts) != 1:
            return False
        grandchild = self._only_extglob_child(child)
        return (
            grandchild is not None
            and self.type is not None
            and grandchild.type in _USURP.get(self.type, {})
        )

    def _usurp(self, child: _Ast) -> None:
        grandchild = child.parts[0]
        assert isinstance(grandchild, _Ast) and self.type is not None
        assert grandchild.type is not None
        self.parts = grandchild.parts
        for part in self.parts:
            if isinstance(part, _Ast):
                part.parent = self
        self.type = _USURP[self.type][grandchild.type]
        self.empty_ext = False

    def _flatten(self) -> None:
        if self.type not in _EXTGLOB_TYPES:
            for part in self.parts:
                if isinstance(part, _Ast):
                    part._flatten()
            return
        # Up to ten passes, as minimatch.
        for _ in range(10):
            done = True
            index = 0
            while index < len(self.parts):
                if self._flatten_child(index):
                    done = False
                index += 1
            if done:
                return

    def _flatten_child(self, index: int) -> bool:
        """Flatten the part at ``index`` and merge it in where minimatch
        does; whether this changed the parts."""

        child = self.parts[index]
        if not isinstance(child, _Ast):
            return False
        child._flatten()
        if self._can_adopt(child, _ADOPTION):
            self._adopt(child, index)
        elif self._can_adopt(child, _ADOPTION_WITH_SPACE):
            self._adopt_with_space(child, index)
        elif self._can_usurp(child):
            self._usurp(child)
        else:
            return False
        return True

    def _fill_negs(self) -> None:
        str(self)
        self.filled_negs = True
        while self.negs:
            negation = self.negs.pop()
            if negation.type != "!":
                continue
            # Append everything that comes after it to each alternative.
            node = negation
            parent = node.parent
            while parent is not None:
                index = node.parent_index + 1
                while parent.type is None and index < len(parent.parts):
                    for part in negation.parts:
                        assert isinstance(part, _Ast)
                        part.copy_in(parent.parts[index])
                    index += 1
                node = parent
                parent = node.parent

    # -- the regular expression -------------------------------------------

    def to_mm_pattern(self) -> str | tuple[str, bool]:
        """The unescaped text when there is no magic, else the expression
        (for Python) and whether minimatch would set the ``u`` flag."""

        source, body, has_magic, uflag = self.to_regexp_source(None)
        if not (has_magic or self.has_magic):
            return body
        expression = _python_regex("^" + source + "$")
        return (_code_points(expression) if uflag else expression), uflag

    def to_regexp_source(self, allow_dot: bool | None) -> tuple[str, str, bool, bool]:
        dot = bool(allow_dot)
        if self.root is self:
            self._flatten()
            self._fill_negs()
        if self.type not in _EXTGLOB_TYPES:
            return self._text_source(allow_dot, dot)
        return self._extglob_source(allow_dot, dot)

    def _text_source(
        self, allow_dot: bool | None, dot: bool
    ) -> tuple[str, str, bool, bool]:
        no_empty = (
            self.is_start()
            and self.is_end()
            and all(isinstance(part, str) for part in self.parts)
        )
        sources: list[str] = []
        for part in self.parts:
            if isinstance(part, str):
                source, _body, magic, uflag = _parse_glob(
                    part, self.has_magic, no_empty
                )
            else:
                source, _body, magic, uflag = part.to_regexp_source(allow_dot)
            self.has_magic = bool(self.has_magic or magic)
            self.uflag = self.uflag or uflag
            sources.append(source)
        source = "".join(sources)
        start = ""
        if self.is_start() and self.parts and isinstance(self.parts[0], str):
            dots_only = len(self.parts) == 1 and self.parts[0] in (".", "..")
            if not dots_only:
                no_traversal = (
                    (dot and source[:1] in _ADD_PATTERN_START)
                    or (source.startswith("\\.") and source[2:3] in _ADD_PATTERN_START)
                    or (
                        source.startswith("\\.\\.")
                        and source[4:5] in _ADD_PATTERN_START
                    )
                )
                no_dot = not dot and not allow_dot and source[:1] in _ADD_PATTERN_START
                start = (
                    _START_NO_TRAVERSAL
                    if no_traversal
                    else _START_NO_DOT
                    if no_dot
                    else ""
                )
        end = ""
        if (
            self.is_end()
            and self.root.filled_negs
            and self.parent is not None
            and self.parent.type == "!"
        ):
            end = "(?:$|\\/)"
        self.has_magic = bool(self.has_magic)
        return start + source + end, _unescape(source), self.has_magic, self.uflag

    def _parts_source(self, dot: bool) -> str:
        sources: list[str] = []
        whole = self.is_start() and self.is_end()
        for part in self.parts:
            assert isinstance(part, _Ast)
            source, _body, _magic, uflag = part.to_regexp_source(dot)
            self.uflag = self.uflag or uflag
            if source or not whole:
                sources.append(source)
        return "|".join(sources)

    def _extglob_source(
        self, allow_dot: bool | None, dot: bool
    ) -> tuple[str, str, bool, bool]:
        repeated = self.type in ("*", "+")
        start = "(?:(?!(?:" if self.type == "!" else "(?:"
        body = self._parts_source(dot)
        if self.is_start() and self.is_end() and not body and self.type != "!":
            # An empty extended glob that is the whole segment is text.
            text = str(self)
            self.parts = [text]
            self.type = None
            self.has_magic = None
            return text, _unescape(str(self)), False, False
        body_dot_allowed = (
            "" if not repeated or allow_dot or dot else self._parts_source(True)
        )
        if body_dot_allowed == body:
            body_dot_allowed = ""
        if body_dot_allowed:
            body = f"(?:{body})(?:{body_dot_allowed})*?"
        if self.type == "!" and self.empty_ext:
            final = (_START_NO_DOT if self.is_start() and not dot else "") + (
                _STAR_NO_EMPTY
            )
        else:
            if self.type == "!":
                no_dot = self.is_start() and not dot and not allow_dot
                close = "))" + (_START_NO_DOT if no_dot else "") + _STAR + ")"
            elif self.type == "@":
                close = ")"
            elif self.type == "?":
                close = ")?"
            elif (self.type == "+" and body_dot_allowed) or (
                self.type == "*" and body_dot_allowed
            ):
                close = ")" if self.type == "+" else ")?"
            else:
                close = ")" + str(self.type)
            final = start + body + close
        self.has_magic = bool(self.has_magic)
        return final, _unescape(body), self.has_magic, self.uflag


def _parse(text: str, ast: _Ast, position: int, depth: int) -> int:
    """minimatch's ``#parseAST``: the text from ``position`` into ``ast``;
    returns the index after what it read."""

    if ast.type is None:
        return _parse_text(text, ast, position, depth)
    return _parse_extglob(text, ast, position, depth)


class _Scan:
    """The escape and ``[...]`` state of ``#parseAST``."""

    def __init__(self) -> None:
        self.escaping = False
        self.in_set = False
        self.set_start = -1
        self.set_negated = False

    def literal(self, char: str, index: int) -> bool:
        """Whether ``char`` (read before ``index``) is plain text here."""

        if self.escaping or char == "\\":
            self.escaping = not self.escaping
            return True
        if self.in_set:
            if index == self.set_start + 1:
                if char in "^!":
                    self.set_negated = True
            elif char == "]" and not (index == self.set_start + 2 and self.set_negated):
                self.in_set = False
            return True
        if char == "[":
            self.in_set = True
            self.set_start = index
            self.set_negated = False
            return True
        return False


def _parse_text(text: str, ast: _Ast, position: int, depth: int) -> int:
    scan = _Scan()
    index = position
    acc = ""
    while index < len(text):
        char = text[index]
        index += 1
        if scan.literal(char, index):
            acc += char
            continue
        if (
            char in _EXTGLOB_TYPES
            and text[index : index + 1] == "("
            and depth <= _MAX_EXTGLOB_RECURSION
        ):
            ast.push(acc)
            acc = ""
            extglob = _Ast(char, ast)
            index = _parse(text, extglob, index, depth + 1)
            ast.push(extglob)
            continue
        acc += char
    ast.push(acc)
    return index


def _parse_extglob(text: str, ast: _Ast, position: int, depth: int) -> int:
    assert ast.type is not None
    scan = _Scan()
    index = position + 1
    part = _Ast(None, ast)
    parts: list[_Ast] = []
    acc = ""
    while index < len(text):
        char = text[index]
        index += 1
        if scan.literal(char, index):
            acc += char
            continue
        adopts = char in _ADOPTION_ANY.get(ast.type, "")
        if (
            char in _EXTGLOB_TYPES
            and text[index : index + 1] == "("
            and (depth <= _MAX_EXTGLOB_RECURSION or adopts)
        ):
            part.push(acc)
            acc = ""
            extglob = _Ast(char, part)
            part.push(extglob)
            index = _parse(text, extglob, index, depth + (0 if adopts else 1))
            continue
        if char == "|":
            part.push(acc)
            acc = ""
            parts.append(part)
            part = _Ast(None, ast)
            continue
        if char == ")":
            if acc == "" and not ast.parts:
                ast.empty_ext = True
            part.push(acc)
            ast.push(*parts, part)
            return index
        acc += char
    # An extended glob that is not closed is text.
    ast.type = None
    ast.has_magic = None
    ast.parts = [text[position - 1 :]]
    return index


def _parse_glob(
    glob: str, has_magic: bool | None, no_empty: bool
) -> tuple[str, str, bool, bool]:
    """minimatch's ``#parseGlob``: a run of text without extended globs."""

    escaping = False
    source = ""
    uflag = False
    magic = bool(has_magic)
    in_star = False
    index = 0
    while index < len(glob):
        char = glob[index]
        if escaping:
            escaping = False
            source += ("\\" if char in _RE_SPECIALS else "") + char
        elif char == "*":
            if not in_star:
                in_star = True
                only_stars = no_empty and _STAR_ONLY.fullmatch(glob) is not None
                source += _STAR_NO_EMPTY if only_stars else _STAR
                magic = True
            index += 1
            continue
        elif char == "\\":
            if index == len(glob) - 1:
                source += "\\\\"
            else:
                escaping = True
        elif char == "[" and (found := _parse_class(glob, index))[2]:
            class_source, class_uflag, consumed, class_magic = found
            source += class_source
            uflag = uflag or class_uflag
            index += consumed
            magic = magic or class_magic
            in_star = False
            continue
        elif char == "?":
            source += _QMARK
            magic = True
        else:
            source += _regexp_escape(char)
        in_star = False
        index += 1
    return source, _unescape(glob), magic, uflag


def _set_escape(text: str) -> str:
    """minimatch's ``braceEscape``, also escaping what Python reads as set
    operators."""

    return re.sub(r"[\[\]\\\-&~|]", lambda match: "\\" + match[0], text)


def _parse_class(glob: str, position: int) -> tuple[str, bool, int, bool]:  # noqa: C901 - minimatch's parseClass
    """minimatch's ``parseClass``: the ``[...]`` set at ``position`` as
    ``(source, uflag, consumed, magic)``; ``consumed`` is 0 when it is not
    a closed set."""

    ranges: list[str] = []
    negs: list[str] = []
    index = position + 1
    saw_start = False
    uflag = False
    escaping = False
    negate = False
    end = position
    range_start = ""
    while index < len(glob):
        char = glob[index]
        if char in "!^" and index == position + 1:
            negate = True
            index += 1
            continue
        if char == "]" and saw_start and not escaping:
            end = index + 1
            break
        saw_start = True
        if char == "\\" and not escaping:
            escaping = True
            index += 1
            continue
        if char == "[" and not escaping:
            name = next(
                (cls for cls in _POSIX_CLASSES if glob.startswith(cls, index)), None
            )
            if name is not None:
                if range_start:
                    return "$.", False, len(glob) - position, True
                index += len(name)
                _categories, _extra, needs_u, negated = _POSIX_CLASSES[name]
                (negs if negated else ranges).append(_class_source(name))
                uflag = uflag or needs_u
                continue
        escaping = False
        if range_start:
            if char > range_start:
                ranges.append(_set_escape(range_start) + "-" + _set_escape(char))
            elif char == range_start:
                ranges.append(_set_escape(char))
            range_start = ""
            index += 1
            continue
        if glob.startswith("-]", index + 1):
            ranges.append(_set_escape(char + "-"))
            index += 2
            continue
        if glob.startswith("-", index + 1):
            range_start = char
            index += 2
            continue
        ranges.append(_set_escape(char))
        index += 1
    if end < index:
        return "", False, 0, False
    if not ranges and not negs:
        return "$.", False, len(glob) - position, True
    if (
        not negs
        and len(ranges) == 1
        and _SINGLE_ESCAPED.fullmatch(ranges[0])
        and not negate
    ):
        literal = ranges[0][-1] if len(ranges[0]) == 2 else ranges[0]
        return _regexp_escape(literal), False, end - position, False
    positive = "[" + ("^" if negate else "") + "".join(ranges) + "]"
    negative = "[" + ("" if negate else "^") + "".join(negs) + "]"
    if ranges and negs:
        source = "(" + positive + "|" + negative + ")"
    else:
        source = positive if ranges else negative
    return source, uflag, end - position, True


def _class_source(name: str) -> str:
    if name in _ASCII_CLASS_SOURCE:
        return _ASCII_CLASS_SOURCE[name]
    return _unicode_class(name)


@lru_cache(maxsize=None)
def _unicode_class(name: str) -> str:
    """The characters of a Unicode property class as ranges for a Python
    ``[...]`` set, from Node's tables (:mod:`unicode_properties`). For
    ``[:graph:]`` these are the characters of ``\\p{Z}\\p{C}``, which
    minimatch puts in a negated set itself."""

    categories, extra, _needs_u, _negated = _POSIX_CLASSES[name]
    spans = [(ord(char), ord(char)) for char in extra]
    for category in categories:
        for span in CATEGORY_RANGES[category].split():
            low, _, high = span.partition("-")
            spans.append((int(low, 16), int(high, 16)))
    merged: list[list[int]] = []
    for first, last in sorted(spans):
        if merged and first <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], last)
        else:
            merged.append([first, last])
    return "".join(
        _set_escape(chr(first)) + ("-" + _set_escape(chr(last)) if last > first else "")
        for first, last in merged
    )

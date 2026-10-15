"""Bash-style brace expansion as Pi's minimatch does it.

A port of ``brace-expansion`` 5.0.9 and the ``balanced-match`` 4.0.4 it
uses, the packages minimatch 10.2.6 expands braces with: ``a{b,c}d`` gives
``abd`` and ``acd``, ``{1..3}`` and ``{a..e..2}`` are sequences, ``${...}``
and ``{a}`` stay as written, and the backslash of ``\\{``, ``\\}``,
``\\,``, ``\\.`` and ``\\\\`` is dropped. The output is cut at 100 000
results and 4 000 000 characters, as in the package.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Sequence
from decimal import Decimal
from typing import Final

EXPANSION_MAX: Final = 100_000
EXPANSION_MAX_LENGTH: Final = 4_000_000


def _js_string(number: float) -> str:
    """JavaScript's ``String(number)`` (Number::toString): the shortest
    digits that read back as the number, in plain form from 1e-6 to below
    1e21 and in exponent form outside."""

    if math.isinf(number):
        return "Infinity" if number > 0 else "-Infinity"
    if number == 0:
        return "0"
    sign = "-" if number < 0 else ""
    # Python's repr has the same shortest round-trip digits.
    digits, exponent = _shortest_digits(abs(number))
    count = len(digits)
    point = count + exponent  # the value is 0.<digits> * 10**point
    if count <= point <= 21:
        return sign + digits + "0" * (point - count)
    if 0 < point <= 21:
        return sign + digits[:point] + "." + digits[point:]
    if -6 < point <= 0:
        return sign + "0." + "0" * -point + digits
    mantissa = digits[0] + ("." + digits[1:] if count > 1 else "")
    power = point - 1
    return f"{sign}{mantissa}e{'+' if power >= 0 else '-'}{abs(power)}"


def _shortest_digits(number: float) -> tuple[str, int]:
    """The shortest decimal digits of ``number`` without trailing zeros, and
    the power of ten of their last digit."""

    value = Decimal(repr(number)).normalize()
    sign, digit_tuple, exponent = value.as_tuple()
    assert isinstance(exponent, int)
    return "".join(map(str, digit_tuple)), exponent


def _marker(name: str) -> str:
    """brace-expansion's ``'\\0' + name + Math.random() + '\\0'``. The limits
    count the markers' characters, so the number is written as JavaScript
    writes it."""

    return "\0" + name + _js_string(random.random()) + "\0"


_ESC_SLASH: Final = _marker("SLASH")
_ESC_OPEN: Final = _marker("OPEN")
_ESC_CLOSE: Final = _marker("CLOSE")
_ESC_COMMA: Final = _marker("COMMA")
_ESC_PERIOD: Final = _marker("PERIOD")
_ESCAPES: Final = (
    ("\\\\", _ESC_SLASH, "\\"),
    ("\\{", _ESC_OPEN, "{"),
    ("\\}", _ESC_CLOSE, "}"),
    ("\\,", _ESC_COMMA, ","),
    ("\\.", _ESC_PERIOD, "."),
)
# JavaScript's `.` does not match these.
_ANY: Final = "[^\n\r  ]"
_NUMERIC_SEQUENCE: Final = re.compile(r"-?[0-9]+\.\.-?[0-9]+(?:\.\.-?[0-9]+)?")
_ALPHA_SEQUENCE: Final = re.compile(r"[a-zA-Z]\.\.[a-zA-Z](?:\.\.-?[0-9]+)?")
_PADDED: Final = re.compile(r"-?0[0-9]")
_COMMA_BEFORE_CLOSE: Final = re.compile(f",(?!,){_ANY}*\\}}")


def expand(text: str) -> list[str]:
    """``expand(text)`` of brace-expansion with its default limits."""

    if not text:
        return []
    if text.startswith("{}"):
        # Bash keeps a leading `{}`, at the top level only.
        text = "\\{\\}" + text[2:]
    for raw, token, _plain in _ESCAPES:
        text = text.replace(raw, token)
    result = _expand(text, is_top=True)
    for _raw, token, plain in _ESCAPES:
        result = [item.replace(token, plain) for item in result]
    return result


def _balanced(text: str) -> tuple[str, str, str] | None:
    """balanced-match's ``balanced('{', '}', text)``: ``(pre, body, post)``
    around the first balanced pair, or ``None``."""

    found = _range(text)
    if found is None:
        return None
    start, end = found
    return text[:start], text[start + 1 : end], text[end + 1 :]


def _range(text: str) -> tuple[int, int] | None:
    """balanced-match's ``range('{', '}', text)``."""

    open_at = text.find("{")
    close_at = text.find("}", open_at + 1)
    if open_at < 0 or close_at <= 0:
        return None
    opens: list[int] = []
    left = len(text)
    right: int | None = None
    at = open_at
    while at >= 0:
        if at == open_at:
            opens.append(at)
            open_at = text.find("{", at + 1)
        elif len(opens) == 1:
            return opens.pop(), close_at
        else:
            begin = opens.pop() if opens else None
            if begin is not None and begin < left:
                left = begin
                right = close_at
            close_at = text.find("}", at + 1)
        at = open_at if 0 <= open_at < close_at else close_at
    if opens and right is not None:
        return left, right
    return None


def _comma_parts(text: str) -> list[str]:
    """``text.split(",")``, keeping nested brace groups whole."""

    if not text:
        return [""]
    found = _balanced(text)
    if found is None:
        return text.split(",")
    pre, body, post = found
    parts = pre.split(",")
    parts[-1] += "{" + body + "}"
    post_parts = _comma_parts(post)
    if post:
        parts[-1] += post_parts.pop(0)
        parts.extend(post_parts)
    return parts


def _combine(
    acc: Sequence[str], pre: str, values: Sequence[str], *, drop_empties: bool
) -> list[str]:
    """Every ``acc[a] + pre + values[v]``, at most ``EXPANSION_MAX`` of them
    and ``EXPANSION_MAX_LENGTH`` characters in all."""

    out: list[str] = []
    length = 0
    for head in acc:
        for value in values:
            if len(out) >= EXPANSION_MAX:
                return out
            expansion = head + pre + value
            if drop_empties and not expansion:
                continue
            size = _js_length(expansion)
            if length + size > EXPANSION_MAX_LENGTH:
                return out
            out.append(expansion)
            length += size
    return out


def _js_length(text: str) -> int:
    """JavaScript's ``length``: UTF-16 code units."""

    if text.isascii():
        return len(text)
    return len(text) + sum(1 for char in text if ord(char) > 0xFFFF)


def _numeric(text: str) -> float:
    """``numeric`` of brace-expansion: ``parseInt`` of a number (a
    JavaScript number, so a long one is rounded or infinite), else the code
    of the first character."""

    return float(text) if re.fullmatch(r"-?[0-9]+", text) else float(ord(text[0]))


def _sequence(body: str, *, alpha: bool) -> list[str]:
    """The values of ``1..5`` or ``a..e..2``."""

    bounds = body.split("..")
    first, last = _numeric(bounds[0]), _numeric(bounds[1])
    width = max(len(bounds[0]), len(bounds[1]))
    step = max(abs(_numeric(bounds[2])), 1.0) if len(bounds) == 3 else 1.0
    reverse = last < first
    if reverse:
        step = -step
    pad = any(_PADDED.match(bound) for bound in bounds)
    values: list[str] = []
    length = 0
    current = first
    while (current >= last if reverse else current <= last) and len(
        values
    ) < EXPANSION_MAX:
        value = _sequence_value(current, width, alpha=alpha, pad=pad)
        if length + len(value) > EXPANSION_MAX_LENGTH:
            break
        values.append(value)
        length += len(value)
        current += step
    return values


def _sequence_value(number: float, width: int, *, alpha: bool, pad: bool) -> str:
    if alpha:
        value = chr(int(number))
        return "" if value == "\\" else value
    value = _js_string(number)
    need = width - len(value)
    if pad and need > 0:
        zeros = "0" * need
        return "-" + zeros + value[1:] if number < 0 else zeros + value
    return value


def _option_values(body: str, *, drops_empties: bool) -> list[str] | str:
    """The values of a comma group, or the one text ``x{{a}}y`` keeps."""

    parts = _comma_parts(body)
    if len(parts) == 1:
        parts = ["{" + item + "}" for item in _expand(parts[0], is_top=False)]
        if len(parts) == 1:
            return parts[0]
    values: list[str] = []
    length = 0
    for part in parts:
        for value in _expand(part, is_top=False):
            if drops_empties and not value:
                continue
            size = _js_length(value)
            if len(values) >= EXPANSION_MAX or length + size > EXPANSION_MAX_LENGTH:
                return values
            values.append(value)
            length += size
    return values


def _expand(text: str, *, is_top: bool) -> list[str]:
    """brace-expansion's ``expand_``: the top-level groups left to right."""

    acc = [""]
    drop_empties = False
    first_group = True
    while True:
        found = _balanced(text)
        if found is None:
            return _combine(acc, text, [""], drop_empties=drop_empties)
        pre, body, post = found
        last = not post
        if pre.endswith("$"):
            acc = _combine(
                acc, pre + "{" + body + "}", [""], drop_empties=drop_empties and last
            )
            first_group = False
            if last:
                return acc
            text = post
            continue
        alpha = _ALPHA_SEQUENCE.fullmatch(body) is not None
        is_sequence = alpha or _NUMERIC_SEQUENCE.fullmatch(body) is not None
        if not is_sequence and "," not in body:
            if _COMMA_BEFORE_CLOSE.search(post):
                # {a},b}
                text = pre + "{" + body + _ESC_CLOSE + post
                is_top = True
                continue
            return _combine(
                acc, pre + "{" + body + "}" + post, [""], drop_empties=drop_empties
            )
        if first_group:
            drop_empties = is_top and not is_sequence
            first_group = False
        values = (
            _sequence(body, alpha=alpha)
            if is_sequence
            else _option_values(
                body, drops_empties=drop_empties and last and not pre and not any(acc)
            )
        )
        if isinstance(values, str):
            acc = _combine(acc, pre + values, [""], drop_empties=drop_empties and last)
        else:
            acc = _combine(acc, pre, values, drop_empties=drop_empties and last)
        if last:
            return acc
        text = post

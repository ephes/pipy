"""The codemode source format: Python, optionally after one options line.

Pi ``packages/codemode/src/source.ts``::

    // @options: {"max_output_tokens": 2000, "timeout_ms": 30000}
    text(tools.read({"path": "pyproject.toml"})[:200])

The line keeps Pi's ``//`` form, which is not Python: it is replaced by an
empty line before the script runs, so line numbers do not change.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

CODEMODE_OPTIONS_PREFIX = "// @options:"

_SUPPORTED_FIELDS = ("max_output_tokens", "timeout_ms")
_SUPPORTED_FIELDS_TEXT = "`max_output_tokens` and `timeout_ms`"
# Pi: the largest delay ``setTimeout`` supports, which bounds ``timeout_ms``.
MAX_TIMEOUT_MS = 2_147_483_647
# JavaScript's ``Number.MAX_SAFE_INTEGER``.
_MAX_SAFE_INTEGER = 2**53 - 1


class CodemodeSourceError(ValueError):
    """Pi ``CodemodeSourceError``: empty input or an invalid options line."""


@dataclass(frozen=True, slots=True)
class ParsedCodemodeSource:
    """The script, its options line replaced by an empty line, and the
    options: a token budget for the output and a deadline in milliseconds
    for the whole script (``None`` when not given)."""

    code: str
    max_output_tokens: int | None = None
    timeout_ms: int | None = None


def parse_codemode_source(source: str) -> ParsedCodemodeSource:
    """Split an optional first-line ``// @options: {...}`` from the script.

    Raises :class:`CodemodeSourceError` for empty input and invalid options,
    with Pi's messages (naming Python where Pi names JavaScript).
    """

    if source.strip() == "":
        raise CodemodeSourceError(
            "Expected Python source text (non-empty). Provide Python only, "
            'optionally with a first line `// @options: {"max_output_tokens": 1000}`.'
        )
    newline = source.find("\n")
    first_line = source if newline == -1 else source[:newline]
    first_line = first_line.removesuffix("\r")
    trimmed = first_line.lstrip()
    if not trimmed.startswith(CODEMODE_OPTIONS_PREFIX):
        return ParsedCodemodeSource(source)
    code = "" if newline == -1 else source[newline:]
    if code.strip() == "":
        raise CodemodeSourceError(
            "The @options line must be followed by Python source on subsequent lines"
        )
    max_output_tokens, timeout_ms = _parse_options(
        trimmed[len(CODEMODE_OPTIONS_PREFIX) :].strip()
    )
    return ParsedCodemodeSource(code, max_output_tokens, timeout_ms)


def _parse_options(directive: str) -> tuple[int | None, int | None]:
    shape = (
        f"@options must be a JSON object with supported fields {_SUPPORTED_FIELDS_TEXT}"
    )
    if directive == "":
        raise CodemodeSourceError(shape)
    try:
        value = json.loads(directive, parse_constant=_reject_constant)
    except ValueError as error:
        raise CodemodeSourceError(
            f"@options must be valid JSON with supported fields "
            f"{_SUPPORTED_FIELDS_TEXT}: {error}"
        ) from None
    if not isinstance(value, dict):
        raise CodemodeSourceError(shape)
    for key in value:
        if key not in _SUPPORTED_FIELDS:
            raise CodemodeSourceError(
                f"@options only supports {_SUPPORTED_FIELDS_TEXT}; got `{key}`"
            )
    max_output_tokens = _safe_integer(value.get("max_output_tokens"))
    if "max_output_tokens" in value and max_output_tokens is None:
        raise CodemodeSourceError(
            "@options field `max_output_tokens` must be a non-negative safe integer"
        )
    timeout_ms = _safe_integer(value.get("timeout_ms"))
    if "timeout_ms" in value and (
        timeout_ms is None or timeout_ms == 0 or timeout_ms > MAX_TIMEOUT_MS
    ):
        raise CodemodeSourceError(
            f"@options field `timeout_ms` must be a positive integer up to {MAX_TIMEOUT_MS}"
        )
    return max_output_tokens, timeout_ms


def _reject_constant(name: str) -> object:
    # ``NaN`` and ``Infinity`` are not JSON (JavaScript ``JSON.parse``).
    raise ValueError(f"{name} is not valid JSON")


def _safe_integer(value: object) -> int | None:
    """Pi ``isSafeInteger``: a non-negative integral number up to 2^53 - 1;
    ``1.0`` counts, as JSON numbers do not tell them apart."""

    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        value = int(value)
    if not isinstance(value, int) or not 0 <= value <= _MAX_SAFE_INTEGER:
        return None
    return value

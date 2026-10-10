"""Pi-style best-effort arguments for newly completed stopped calls only."""

from __future__ import annotations

import json
from typing import Any

from partial_json_parser import loads as load_partial


def _reject_constant(value: str) -> None:
    raise ValueError(value)


def _strict_loads(text: str) -> Any:
    return json.loads(text, parse_constant=_reject_constant)


def normalize_partial_arguments(text: str) -> str:
    """Return finite JSON, preserving recoverable values; invalid input is {}.

    Complete null stays null; partial null becomes {}, matching Pi's strict
    parse versus partial-parser fallback. Ordinary execution never calls here.
    """
    repaired = _repair_string_literals(text)
    for partial, candidate in (
        (False, text),
        (False, repaired),
        (True, text),
        (True, repaired),
    ):
        try:
            value = (
                load_partial(candidate, parser=_strict_loads)
                if partial
                else _strict_loads(candidate)
            )
            if partial and value is None:
                value = {}
            return json.dumps(value, separators=(",", ":"), allow_nan=False)
        except (ValueError, RecursionError):
            continue
    return "{}"


def _repair_string_literals(text: str) -> str:
    """Escape raw controls and invalid backslashes inside JSON strings only."""
    pieces: list[str] = []
    in_string = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            in_string = not in_string
        if in_string and char == "\\":
            next_char = text[index + 1 : index + 2]
            if next_char and next_char in '\\"/bfnrtu':
                pieces.append(char + next_char)
                index += 2
            else:
                pieces.append("\\\\")
                index += 1
            continue
        if in_string and ord(char) < 32:
            char = json.dumps(char)[1:-1]
        pieces.append(char)
        index += 1
    return "".join(pieces)

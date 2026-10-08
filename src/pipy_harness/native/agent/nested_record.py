"""Bounded data-only nested evidence, safe to consume from imported details."""

from __future__ import annotations

import json
from math import isfinite
from typing import Any

MAX_CALLS = 256
MAX_ARGUMENT_BYTES = 8192
MAX_TOTAL_ARGUMENT_BYTES = 32768
MAX_ERROR_CHARS = 500


def bounded_arguments(text: str) -> dict[str, Any] | None:
    """Snapshot JSON objects only; never parse an unbounded payload."""
    if len(text) > MAX_ARGUMENT_BYTES:
        return None
    if len(text.encode("utf-8", errors="surrogatepass")) > MAX_ARGUMENT_BYTES:
        return None
    try:
        value = json.loads(text, parse_constant=_reject_constant)
        return (
            value if type(value) is dict and argument_json(value) is not None else None
        )
    except (ValueError, RecursionError):
        return None


def _reject_constant(value: str) -> None:
    raise ValueError(value)


def argument_json(value: object) -> str | None:  # noqa: C901 - bounded JSON type walker
    """Encode bounded plain JSON iteratively, rejecting cycles and custom objects.

    Check containers before encoding so imported metadata cannot allocate an
    unbounded intermediate string or recurse through hostile mappings.
    """
    if type(value) is not dict:
        return None
    stack: list[tuple[Any, int]] = [(value, 0)]
    text_budget = 0
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 8192 or depth > 64:
            return None
        if type(item) in (dict, list):
            if nodes + len(stack) + len(item) > 8192:
                return None
            if type(item) is dict:
                for key, child in item.items():
                    if type(key) is not str or len(key) > 8192:
                        return None
                    text_budget += len(key)
                    stack.append((child, depth + 1))
            else:
                stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            text_budget += len(item)
            if len(item) > 8192:
                return None
        elif type(item) is float:
            if not isfinite(item):
                return None
        elif item is not None and type(item) not in (bool, int):
            return None
        elif type(item) is int:
            if item.bit_length() > 4096:
                return None
            text_budget += item.bit_length() // 3 + 1
        if text_budget > 8192:
            return None
    try:
        text = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    except (ValueError, RecursionError, UnicodeError):
        return None
    if len(text.encode("utf-8", errors="surrogatepass")) > MAX_ARGUMENT_BYTES:
        return None
    return text


def finite_duration(value: object) -> bool:
    """Reject malformed numbers, including integers that overflow float conversion."""
    if not isinstance(value, (int, float)) or type(value) not in (int, float):
        return False
    try:
        return isfinite(value) and value >= 0
    except OverflowError:
        return False


def parse_nested_record(details: object) -> tuple[dict[str, Any], ...]:
    """Ignore malformed fields; cap imported count, text and argument storage."""
    if (
        type(details) is not dict
        or type(record := details.get("nestedCalls")) is not dict
    ):
        return ()
    calls = record.get("calls")
    if type(calls) is not list:
        return ()
    result: list[dict[str, Any]] = []
    total = 0
    for call in calls[:MAX_CALLS]:
        if type(call) is not dict:
            continue
        identity, name, status = call.get("id"), call.get("name"), call.get("status")
        duration: Any = call.get("durationMs")
        if (
            type(identity) is not str
            or not identity
            or len(identity) > 1024
            or type(name) is not str
            or not name
            or len(name) > 80
            or type(status) is not str
            or status not in {"ok", "error", "cancelled", "unfinished"}
            or not finite_duration(duration)
        ):
            continue
        row = {"id": identity, "name": name, "status": status, "durationMs": duration}
        text = argument_json(call.get("arguments"))
        if (
            text is not None
            and total + (size := len(text.encode("utf-8", errors="surrogatepass")))
            <= MAX_TOTAL_ARGUMENT_BYTES
        ):
            row["arguments"] = json.loads(text)
            total += size
        else:
            omitted: Any = call.get("argumentsBytes")
            if type(omitted) is int and 0 <= omitted <= 2**63 - 1:
                row["argumentsBytes"] = omitted
        error = call.get("error")
        if type(error) is str:
            row["error"] = error[:MAX_ERROR_CHARS]
        result.append(row)
    return tuple(result)

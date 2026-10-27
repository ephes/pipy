"""Bounded data-only nested evidence, safe to consume from imported details."""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import islice
from math import isfinite
from typing import Any

from pipy_harness.native.agent.tool_details import has_tool_details

MAX_CALLS = 256
MAX_ARGUMENT_BYTES = 8192
MAX_TOTAL_ARGUMENT_BYTES = 32768
MAX_ERROR_CHARS = 500
# Pi codemode ``ARGS_PREVIEW_CHARS``: the arguments a call's row shows.
ARGUMENT_PREVIEW_CHARS = 200


@dataclass(frozen=True, slots=True)
class NestedCallRecord:
    """One existing service-record row, not a nested invocation authority."""

    id: str
    name: str
    status: str
    duration_ms: int | float
    arguments_json: str | None = None
    arguments_bytes: int | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.id) is not str
            or not 0 < len(self.id) <= 1024
            or type(self.name) is not str
            or not 0 < len(self.name) <= 80
            or type(self.status) is not str
            or self.status not in {"ok", "error", "cancelled", "unfinished"}
            or not finite_duration(self.duration_ms)
            or (self.arguments_json is None) == (self.arguments_bytes is None)
        ):
            raise ValueError("Invalid nested call evidence")
        if self.arguments_json is not None:
            if (
                type(self.arguments_json) is not str
                or bounded_arguments(self.arguments_json) is None
            ):
                raise ValueError("Invalid nested call evidence")
        elif (
            type(self.arguments_bytes) is not int
            or not 0 <= self.arguments_bytes <= 2**63 - 1
        ):
            raise ValueError("Invalid nested call evidence")
        if self.error is not None and (
            type(self.error) is not str or len(self.error) > MAX_ERROR_CHARS
        ):
            raise ValueError("Invalid nested call evidence")

    def to_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "durationMs": self.duration_ms,
        }
        if self.arguments_json is not None:
            row["arguments"] = json.loads(self.arguments_json)
        else:
            row["argumentsBytes"] = self.arguments_bytes
        if self.error is not None:
            row["error"] = self.error
        return row


@dataclass(frozen=True, slots=True)
class NestedCallEvidence:
    """Finite actual call record for a result whose details are not a mapping."""

    calls: tuple[NestedCallRecord, ...]
    complete: bool
    argument_previews: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.calls) is not tuple
            or len(self.calls) > MAX_CALLS
            or type(self.complete) is not bool
        ):
            raise ValueError("Invalid nested call evidence")
        if any(type(call) is not NestedCallRecord for call in self.calls):
            raise ValueError("Invalid nested call evidence")
        if len({call.id for call in self.calls}) != len(self.calls):
            raise ValueError("Invalid nested call evidence")
        if self.complete and any(call.status == "unfinished" for call in self.calls):
            raise ValueError("Invalid nested call evidence")
        if (
            sum(
                len((call.arguments_json or "").encode("utf-8", "surrogatepass"))
                for call in self.calls
            )
            > MAX_TOTAL_ARGUMENT_BYTES
        ):
            raise ValueError("Invalid nested call evidence")
        if (
            type(self.argument_previews) is not tuple
            or len(self.argument_previews) > MAX_CALLS
        ):
            raise ValueError("Invalid nested call evidence")
        ids: set[str] = set()
        for pair in self.argument_previews:
            if (
                type(pair) is not tuple
                or len(pair) != 2
                or type(pair[0]) is not str
                or pair[0] in ids
                or type(pair[1]) is not str
                or len(pair[1]) > ARGUMENT_PREVIEW_CHARS
                or not any(
                    call.id == pair[0] and call.arguments_bytes is not None
                    for call in self.calls
                )
            ):
                raise ValueError("Invalid nested call evidence")
            ids.add(pair[0])

    @classmethod
    def from_record(cls, record: object, previews: object = None) -> NestedCallEvidence:
        if (
            type(record) is not dict
            or set(record) != {"calls", "complete"}
            or type(record["calls"]) is not list
        ):
            raise ValueError("Invalid nested call evidence")
        calls = []
        for row in record["calls"]:
            if (
                type(row) is not dict
                or not {"id", "name", "status", "durationMs"} <= set(row)
                or set(row)
                - {
                    "id",
                    "name",
                    "status",
                    "durationMs",
                    "arguments",
                    "argumentsBytes",
                    "error",
                }
            ):
                raise ValueError("Invalid nested call evidence")
            if ("arguments" in row) == ("argumentsBytes" in row):
                raise ValueError("Invalid nested call evidence")
            arguments = argument_json(row["arguments"]) if "arguments" in row else None
            if "arguments" in row and arguments is None:
                raise ValueError("Invalid nested call evidence")
            calls.append(
                NestedCallRecord(
                    row["id"],
                    row["name"],
                    row["status"],
                    row["durationMs"],
                    arguments,
                    row.get("argumentsBytes"),
                    row.get("error"),
                )
            )
        if previews is None:
            previews = {}
        if type(previews) is not dict:
            raise ValueError("Invalid nested call evidence")
        return cls(tuple(calls), record["complete"], tuple(previews.items()))

    def to_json(self) -> dict[str, Any]:
        return {
            "calls": [call.to_row() for call in self.calls],
            "complete": self.complete,
            **(
                {"argumentPreviews": dict(self.argument_previews)}
                if self.argument_previews
                else {}
            ),
        }

    @classmethod
    def from_json(cls, value: object) -> NestedCallEvidence:
        if (
            type(value) is not dict
            or set(value) - {"calls", "complete", "argumentPreviews"}
            or not {"calls", "complete"} <= set(value)
            or (
                "argumentPreviews" in value
                and type(value["argumentPreviews"]) is not dict
            )
        ):
            raise ValueError("Invalid nested call evidence")
        return cls.from_record(
            {"calls": value["calls"], "complete": value["complete"]},
            value.get("argumentPreviews"),
        )


def validate_nested_evidence(
    details: object, evidence: NestedCallEvidence | None
) -> None:
    if evidence is not None and (
        type(evidence) is not NestedCallEvidence
        or type(details) is dict
        or not has_tool_details(details)
    ):
        raise ValueError("Conflicting nested call evidence")


def selected_nested_details(details: Any, evidence: NestedCallEvidence | None) -> Any:
    """A transient renderer view, never a second persisted result authority."""
    if evidence is None:
        return details
    value = evidence.to_json()
    return {
        "nestedCalls": {"calls": value["calls"], "complete": value["complete"]},
        **(
            {"argumentPreviews": value["argumentPreviews"]}
            if "argumentPreviews" in value
            else {}
        ),
    }


def attach_nested_record(
    details: Any, record: object, previews: dict[str, str] | None = None
) -> tuple[Any, NestedCallEvidence | None]:
    """Keep old mapping/absent bytes; never overwrite a rich details value."""
    if type(details) is dict or not has_tool_details(details):
        return {
            **(details if type(details) is dict else {}),
            **({"argumentPreviews": dict(previews)} if previews else {}),
            "nestedCalls": record,
        }, None
    return details, NestedCallEvidence.from_record(record, previews)


def retarget_nested_evidence(
    details: Any, evidence: NestedCallEvidence | None
) -> tuple[Any, NestedCallEvidence | None]:
    """A hook changes details; preserve the actual record in exactly one channel."""
    if evidence is None:
        return details, None
    value = evidence.to_json()
    return attach_nested_record(
        details,
        {"calls": value["calls"], "complete": value["complete"]},
        value.get("argumentPreviews"),
    )


def argument_preview(arguments_json: str) -> str:
    """The start of a call's arguments for its row (Pi codemode
    ``previewArgs``): their JSON, cut to 200 characters with ``...``."""
    if len(arguments_json) <= ARGUMENT_PREVIEW_CHARS:
        return arguments_json
    return arguments_json[: ARGUMENT_PREVIEW_CHARS - 3] + "..."


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


def parse_argument_previews(details: object) -> dict[str, str]:
    """The ``argumentPreviews`` of a codemode result, by call id: the start
    of the arguments of each call whose arguments the ``nestedCalls``
    record left out. Malformed entries are ignored; count and text are
    capped as the record's are."""
    if (
        type(details) is not dict
        or type(previews := details.get("argumentPreviews")) is not dict
    ):
        return {}
    return {
        identity: preview[:ARGUMENT_PREVIEW_CHARS]
        for identity, preview in islice(previews.items(), MAX_CALLS)
        if type(identity) is str and 0 < len(identity) <= 1024 and type(preview) is str
    }

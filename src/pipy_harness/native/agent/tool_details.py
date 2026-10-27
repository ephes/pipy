"""Tool-local JSON presence, separate from structured content and hook control."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pipy_harness.native.agent.json_values import copy_normal_json


class ToolDetailsState(Enum):
    """Missing data, historical unknown presence, and a transform-only default."""

    ABSENT = "absent"
    LEGACY_UNSPECIFIED = "legacy_unspecified"
    UNCHANGED = "unchanged"


NO_TOOL_DETAILS = ToolDetailsState.ABSENT
LEGACY_TOOL_DETAILS_NONE = ToolDetailsState.LEGACY_UNSPECIFIED
UNCHANGED_TOOL_DETAILS = ToolDetailsState.UNCHANGED


def has_tool_details(value: object) -> bool:
    return value is not NO_TOOL_DETAILS and value is not LEGACY_TOOL_DETAILS_NONE


def copy_tool_details(value: Any) -> Any:
    """Own finite JSON without inventing presence or truthiness fallback.

    The legacy marker is retained for copies of decoded historical facts.
    New producers/replacement transforms must not emit it; this value copier
    does not authenticate a marker's origin.
    """
    if value is NO_TOOL_DETAILS or value is LEGACY_TOOL_DETAILS_NONE:
        return value
    if value is UNCHANGED_TOOL_DETAILS:
        raise ValueError("Unchanged tool details is only a transform default")
    return copy_normal_json(value)


def admit_tool_details(value: Any) -> Any:
    """Own new producer details within the existing stdlib JSON boundary.

    This is a separate admission policy, not a limit on owned values or
    historical canonical messages. Encoding is only a predicate: keep the
    snapshot's exact strings, keys and numbers without a text roundtrip.
    """
    snapshot = copy_tool_details(value)
    if has_tool_details(snapshot):
        try:
            json.dumps(snapshot, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            raise ValueError(
                "Tool details exceed native JSON encoding support"
            ) from None
    return snapshot


def copy_forwarded_tool_details(value: Any) -> Any:
    """Own new extension details using the established JSON text semantics.

    Forwarding normalizes strings and keys as a JSON roundtrip does. Historical
    copies and direct ToolPort admission retain their separate value policies.
    """
    snapshot = copy_tool_details(value)
    if not has_tool_details(snapshot):
        return snapshot
    try:
        return json.loads(json.dumps(snapshot, allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        raise ValueError("Tool details exceed native JSON forwarding support") from None


def tool_details_fields(value: Any) -> dict[str, Any]:
    """JSON key presence for ordinary serializers; null is a present value."""
    return {"details": copy_tool_details(value)} if has_tool_details(value) else {}

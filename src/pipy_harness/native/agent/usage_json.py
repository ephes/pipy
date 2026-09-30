"""Pi's JSON shape for the usage stored on an assistant message.

Pi ``Usage`` (``packages/ai/src/types.ts``): ``{input, output, cacheRead,
cacheWrite, cacheWrite1h?, reasoning?, totalTokens, cost: {input, output,
cacheRead, cacheWrite, total}}``. The session tree stores it in this shape and
the JSON/RPC projections emit it unchanged.
"""

from __future__ import annotations

from math import isfinite
from typing import Any

from pipy_harness.native.agent.messages import AgentMessageUsage, AgentUsageCost


def usage_to_json(usage: AgentMessageUsage) -> dict[str, Any]:
    body: dict[str, Any] = {
        "input": usage.input,
        "output": usage.output,
        "cacheRead": usage.cache_read,
        "cacheWrite": usage.cache_write,
    }
    if usage.cache_write_1h is not None:
        body["cacheWrite1h"] = usage.cache_write_1h
    if usage.reasoning is not None:
        body["reasoning"] = usage.reasoning
    body["totalTokens"] = usage.total_tokens
    body["cost"] = {
        "input": usage.cost.input,
        "output": usage.cost.output,
        "cacheRead": usage.cost.cache_read,
        "cacheWrite": usage.cost.cache_write,
        "total": usage.cost.total,
    }
    return body


def usage_from_json(body: object) -> AgentMessageUsage:
    """Read a stored usage object; a malformed one raises ``ValueError``."""

    if not isinstance(body, dict):
        raise ValueError("assistant usage must be an object")
    cost = body.get("cost")
    if not isinstance(cost, dict):
        raise ValueError("assistant usage.cost must be an object")
    return AgentMessageUsage(
        input=_count(body, "input"),
        output=_count(body, "output"),
        cache_read=_count(body, "cacheRead"),
        cache_write=_count(body, "cacheWrite"),
        total_tokens=_count(body, "totalTokens"),
        cost=AgentUsageCost(
            input=_dollars(cost, "input"),
            output=_dollars(cost, "output"),
            cache_read=_dollars(cost, "cacheRead"),
            cache_write=_dollars(cost, "cacheWrite"),
            total=_dollars(cost, "total"),
        ),
        reasoning=_optional_count(body, "reasoning"),
        cache_write_1h=_optional_count(body, "cacheWrite1h"),
    )


def _count(body: dict[str, Any], key: str) -> int:
    value = body.get(key)
    if type(value) is not int or value < 0:
        raise ValueError(f"assistant usage.{key} must be a nonnegative integer")
    return value


def _optional_count(body: dict[str, Any], key: str) -> int | None:
    return None if body.get(key) is None else _count(body, key)


def _dollars(cost: dict[str, Any], key: str) -> float:
    value = cost.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError(f"assistant usage.cost.{key} must be a nonnegative number")
    return float(value)

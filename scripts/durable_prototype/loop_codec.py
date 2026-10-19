"""Finite JSON checkpoint codec for the canonical-loop experiment only."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum

from pipy_harness.native.agent.content import (
    ImageContent,
    NoStructuredContent,
    ProductContent,
    TextContent,
    ThinkingContent,
)
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentMessageUsage,
    AgentStopReason,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUsageCost,
    AgentUserMessage,
)
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
)
from pipy_harness.native.models import ProviderResult, ProviderToolCall
from pipy_harness.status import HarnessStatus

CLASSES = {
    cls.__name__: cls
    for cls in (
        ProductContent,
        TextContent,
        ThinkingContent,
        ImageContent,
        AgentUserMessage,
        AgentAssistantMessage,
        AgentToolCall,
        AgentToolResultMessage,
        AgentMessageUsage,
        AgentUsageCost,
        ProviderResult,
        ProviderToolCall,
        ProviderTurnOutcome,
        ToolExecutionOutcome,
    )
}
ENUMS = {
    cls.__name__: cls
    for cls in (
        HarnessStatus,
        AgentStopReason,
        ToolExecutionInterruption,
        NoStructuredContent,
    )
}


def registered_name(value, registry):
    name = type(value).__name__
    if registry.get(name) is not type(value):
        raise TypeError("unsupported checkpoint type")
    return name


def encode(value):
    if isinstance(value, Enum):
        return {"tag": registered_name(value, ENUMS), "value": value.value}
    if isinstance(value, datetime):
        return {"tag": "datetime", "value": value.isoformat()}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "tag": registered_name(value, CLASSES),
            "fields": {
                field.name: encode(getattr(value, field.name))
                for field in fields(value)
            },
        }
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("checkpoint maps require string keys")
        return {
            "tag": "dict",
            "fields": {key: encode(item) for key, item in value.items()},
        }
    if isinstance(value, tuple):
        return {"tag": "tuple", "items": [encode(item) for item in value]}
    if isinstance(value, list):
        return [encode(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise TypeError(f"unsupported checkpoint value {type(value).__name__}")


def decode(value):
    if isinstance(value, list):
        return [decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    tag = value["tag"]
    if tag == "dict":
        return {key: decode(item) for key, item in value["fields"].items()}
    if tag == "tuple":
        return tuple(decode(item) for item in value["items"])
    if tag == "datetime":
        return datetime.fromisoformat(value["value"])
    if tag in ENUMS:
        return ENUMS[tag](value["value"])
    if tag in CLASSES:
        return CLASSES[tag](
            **{key: decode(item) for key, item in value["fields"].items()}
        )
    raise ValueError("unsupported checkpoint tag")


def dumps(value):
    return json.dumps(
        encode(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def loads(text):
    return decode(json.loads(text))

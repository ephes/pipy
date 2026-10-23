"""Common rich-value fixture projection for the DE06b actual task oracle.

The Node producer independently persists these plain value recipes with pinned
Harness tasks. Native observations reflect decoded dataclass fields, including
compare=False; no observation is copied from the recipe.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pipy_harness.native.agent.request import (
    freeze_provider_request,
)
from pipy_harness.native.durable import canonical_codec as codec
from pipy_harness.native.models import (
    ProviderRequest,
)


def project(value):
    """Independent field projection of actual decoded native values."""
    if type(value) is datetime:
        return {
            "$datetime": value.isoformat(),
            "fold": value.fold,
            "zone": value.tzname(),
        }
    if type(value) is type(Path()):
        return {"$path": str(value)}
    if type(value) is tuple:
        return {"$tuple": [project(item) for item in value]}
    if isinstance(value, Enum):
        return {"$enum": type(value).__name__, "value": value.value}
    if isinstance(value, Mapping):
        return {key: project(item) for key, item in value.items()}
    if is_dataclass(value):
        return {
            "$type": type(value).__name__,
            "fields": {
                field.name: project(getattr(value, field.name))
                for field in fields(value)
            },
        }
    if type(value) is list:
        return [project(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise ValueError("Unprojectable fixture value")


def construct(value):
    """Build source fixture DTOs through normal constructors, not codec decode."""
    if type(value) is list:
        return [construct(item) for item in value]
    if type(value) is not dict:
        return value
    if "$tuple" in value:
        return tuple(construct(item) for item in value["$tuple"])
    if "$path" in value:
        return Path(value["$path"])
    if "$datetime" in value:
        parsed = datetime.fromisoformat(value["$datetime"])
        zone = (
            None
            if parsed.tzinfo is None
            else timezone(parsed.utcoffset(), value["zone"])
        )
        return parsed.replace(fold=value["fold"], tzinfo=zone)
    if "$enum" in value:
        return codec.TAGS[value["$enum"]](value["value"])
    if "$type" in value:
        cls = codec.TAGS[value["$type"]]
        dto = cls(**{key: construct(item) for key, item in value["fields"].items()})
        return freeze_provider_request(dto) if cls is ProviderRequest else dto
    return {key: construct(item) for key, item in value.items()}

"""Value objects for the native pipy runtime bootstrap."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus


@dataclass(frozen=True, slots=True)
class NativeRunInput:
    """One native pipy turn request owned by the native runtime boundary."""

    goal: str
    cwd: Path
    provider_name: str
    model_id: str
    system_prompt_id: str
    system_prompt_version: str


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """Request sent across the native provider port."""

    system_prompt: str
    user_prompt: str
    provider_name: str
    model_id: str
    cwd: Path


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """Result returned by a native provider."""

    status: HarnessStatus
    provider_name: str
    model_id: str
    started_at: datetime
    ended_at: datetime
    final_text: str | None = None
    usage: dict[str, int | float] | None = None
    metadata: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class NativeRunOutput:
    """Native session result before adaptation into the harness result shape."""

    status: HarnessStatus
    exit_code: int
    started_at: datetime
    ended_at: datetime
    final_text: str | None = None
    provider_name: str | None = None
    model_id: str | None = None

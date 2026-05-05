"""Deterministic fakes for native-runtime tests and smoke runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import (
    NativeToolRequest,
    NativeToolResult,
    NativeToolStatus,
    PROVIDER_TOOL_INTENT_METADATA_KEY,
    ProviderRequest,
    ProviderResult,
)


@dataclass(frozen=True, slots=True)
class FakeNativeProvider:
    """A deterministic non-AI provider used to exercise the native boundary."""

    model_id: str = "fake-native-bootstrap"
    final_text: str = "pipy native fake provider completed."
    status: HarnessStatus = HarnessStatus.SUCCEEDED
    metadata: dict[str, Any] | None = None
    tool_intent: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        return "fake"

    def complete(self, request: ProviderRequest) -> ProviderResult:
        started_at = datetime.now(UTC)
        ended_at = datetime.now(UTC)
        final_text = self.final_text if self.status == HarnessStatus.SUCCEEDED else None
        metadata = dict(self.metadata or {})
        if self.tool_intent is not None:
            metadata[PROVIDER_TOOL_INTENT_METADATA_KEY] = dict(self.tool_intent)
        return ProviderResult(
            status=self.status,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=started_at,
            ended_at=ended_at,
            final_text=final_text,
            usage={
                "input_characters": len(request.system_prompt) + len(request.user_prompt),
                "output_characters": len(final_text or ""),
            },
            metadata=metadata or None,
        )


@dataclass(frozen=True, slots=True)
class FakeNoOpNativeTool:
    """A deterministic tool that proves the boundary without side effects."""

    tool_name: str = "noop"
    status: NativeToolStatus = NativeToolStatus.SUCCEEDED
    metadata: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None

    @property
    def name(self) -> str:
        return self.tool_name

    def invoke(self, request: NativeToolRequest) -> NativeToolResult:
        started_at = datetime.now(UTC)
        ended_at = datetime.now(UTC)
        metadata = self.metadata or {
            "workspace_mutated": False,
            "workspace_inspected": False,
            "stdout_stored": False,
            "stderr_stored": False,
            "tool_payloads_stored": False,
        }
        return NativeToolResult(
            request_id=request.request_id,
            tool_name=self.name,
            status=self.status,
            started_at=started_at,
            ended_at=ended_at,
            metadata=metadata,
            error_type=self.error_type if self.status == NativeToolStatus.FAILED else None,
            error_message=self.error_message if self.status == NativeToolStatus.FAILED else None,
        )

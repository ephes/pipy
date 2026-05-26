"""Deterministic fakes for native-runtime tests and smoke runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import (
    NativeToolRequest,
    NativeToolResult,
    NativeToolStatus,
    PROVIDER_PATCH_PROPOSAL_METADATA_KEY,
    PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY,
    PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY,
    PROVIDER_TOOL_INTENT_METADATA_KEY,
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
)
from pipy_harness.native.provider import StreamChunkSink


@dataclass(frozen=True, slots=True)
class FakeNativeProvider:
    """A deterministic non-AI provider used to exercise the native boundary.

    For Tool-Loop Parity Track tests, `supports_tool_calls` can be flipped to
    `True` and `programmable_tool_calls` can be primed with a tuple of
    `(ProviderToolCall, ...)` tuples, one per provider call. Each call to
    `complete` consumes the next tuple in order; when the script is empty the
    provider falls back to an empty `tool_calls` tuple and emits only
    `final_text`. The fake never inspects pipy-owned `tool_request_id`
    values; it only round-trips `provider_correlation_id`.
    """

    model_id: str = "fake-native-bootstrap"
    final_text: str = "pipy native fake provider completed."
    status: HarnessStatus = HarnessStatus.SUCCEEDED
    metadata: dict[str, Any] | None = None
    tool_intent: dict[str, Any] | None = None
    tool_observation_fixture: dict[str, Any] | None = None
    read_only_tool_fixture: dict[str, Any] | None = None
    patch_proposal: dict[str, Any] | None = None
    supports_tool_calls: bool = False
    programmable_tool_calls: tuple[tuple[ProviderToolCall, ...], ...] = ()
    programmable_text_chunks: tuple[str, ...] = ()
    _call_counter: list[int] = field(default_factory=lambda: [0])

    @property
    def name(self) -> str:
        return "fake"

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
    ) -> ProviderResult:
        started_at = datetime.now(UTC)
        if (
            stream_sink is not None
            and self.programmable_text_chunks
            and self.status == HarnessStatus.SUCCEEDED
        ):
            streamed = True
            for chunk in self.programmable_text_chunks:
                stream_sink(chunk)
        else:
            streamed = False
        ended_at = datetime.now(UTC)
        if self.status != HarnessStatus.SUCCEEDED:
            final_text = None
        elif streamed:
            final_text = "".join(self.programmable_text_chunks)
        else:
            final_text = self.final_text
        metadata = dict(self.metadata or {})
        if self.tool_intent is not None:
            metadata[PROVIDER_TOOL_INTENT_METADATA_KEY] = dict(self.tool_intent)
        if self.tool_observation_fixture is not None:
            metadata[PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY] = dict(self.tool_observation_fixture)
        if self.read_only_tool_fixture is not None:
            metadata[PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY] = dict(self.read_only_tool_fixture)
        if self.patch_proposal is not None:
            metadata[PROVIDER_PATCH_PROPOSAL_METADATA_KEY] = dict(self.patch_proposal)
        tool_calls: tuple[ProviderToolCall, ...] = ()
        if self.supports_tool_calls and self.programmable_tool_calls:
            call_index = self._call_counter[0]
            if call_index < len(self.programmable_tool_calls):
                tool_calls = self.programmable_tool_calls[call_index]
        self._call_counter[0] += 1
        return ProviderResult(
            status=self.status,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=started_at,
            ended_at=ended_at,
            final_text=final_text,
            usage={},
            metadata=metadata or None,
            tool_calls=tool_calls,
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

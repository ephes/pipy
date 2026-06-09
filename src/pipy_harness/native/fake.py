"""Deterministic fakes for native-runtime tests and smoke runs."""

from __future__ import annotations

# ``field`` is used by AutomationFakeProvider (and FakeNativeProvider) below for
# default_factory state.
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
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
    programmable_reasoning_chunks: tuple[str, ...] = ()
    # The first ``cancellable_turns`` provider calls block on the active-turn
    # cancel token instead of returning immediately, so tests and PTY runs can
    # exercise a genuinely in-flight turn that observes Escape / Ctrl-C
    # cancellation at the provider boundary (rather than only filtering output
    # after the fact). Later calls complete normally, so a follow-up prompt
    # after an aborted turn still produces an answer.
    cancellable_turns: int = 0
    block_timeout_seconds: float = 30.0
    _call_counter: list[int] = field(default_factory=lambda: [0])
    _entered_counter: list[int] = field(default_factory=lambda: [0])
    _cancel_observed: list[bool] = field(default_factory=lambda: [False])

    @property
    def name(self) -> str:
        return "fake"

    @property
    def cancel_observed(self) -> bool:
        """Whether a ``complete`` call observed cancellation at the boundary."""

        return self._cancel_observed[0]

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        started_at = datetime.now(UTC)
        self._entered_counter[0] += 1
        if self._entered_counter[0] <= self.cancellable_turns:
            self._await_cancellation(cancel_token)
        if (
            reasoning_sink is not None
            and self.programmable_reasoning_chunks
            and self.status == HarnessStatus.SUCCEEDED
        ):
            for chunk in self.programmable_reasoning_chunks:
                reasoning_sink(chunk)
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

    def _await_cancellation(self, cancel_token: CancelToken | None) -> None:
        """Block until the active-turn cancel token fires, then abort.

        Models a slow provider turn that is interrupted at the boundary: when
        the tool loop cancels the turn the fake observes it and raises
        :class:`ProviderCancelledError` instead of producing output, proving
        cancellation reaches the provider rather than merely hiding late text.
        Without a token (e.g. captured-stream mode) there is nothing to wait
        on, so the call falls through to a normal completion.
        """

        if cancel_token is None:
            return
        if cancel_token.event.wait(timeout=self.block_timeout_seconds):
            self._cancel_observed[0] = True
            raise ProviderCancelledError("fake provider turn cancelled")


AUTOMATION_FAKE_MODEL_ID = "fake-tools"
AUTOMATION_FAKE_BLOCK_SENTINEL = "BLOCK"


@dataclass(frozen=True, slots=True)
class AutomationFakeProvider:
    """Deterministic, tool-capable, streaming provider for automation modes.

    Selectable via ``--native-provider fake --native-model fake-tools`` so the
    headless ``--mode json``/``--mode rpc``/``--print`` paths and the
    conformance gate can drive the real tool loop offline. It derives its reply
    from the latest user message (``"SEEN:<first-token>"``) and streams it as
    two deterministic text chunks, so ``text_delta`` events concatenate to the
    ``message_end`` content.

    When the latest user message begins with ``BLOCK`` the turn waits on the
    active-turn cancel token instead of replying, so the RPC mid-turn
    ``steer``/``abort`` checks can interrupt a genuinely in-flight turn at the
    provider boundary. No auth/credential material is ever produced.
    """

    model_id: str = AUTOMATION_FAKE_MODEL_ID
    supports_tool_calls: bool = True
    block_timeout_seconds: float = 30.0
    _cancel_observed: list[bool] = field(default_factory=lambda: [False])

    @property
    def name(self) -> str:
        return "fake"

    @property
    def cancel_observed(self) -> bool:
        return self._cancel_observed[0]

    @staticmethod
    def _latest_user_text(request: ProviderRequest) -> str:
        from pipy_harness.native.tools.messages import UserMessage

        for message in reversed(request.messages):
            if isinstance(message, UserMessage):
                return message.content
        return request.user_prompt or ""

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        started_at = datetime.now(UTC)
        user_text = self._latest_user_text(request).strip()
        if user_text.startswith(AUTOMATION_FAKE_BLOCK_SENTINEL) and cancel_token is not None:
            if cancel_token.event.wait(timeout=self.block_timeout_seconds):
                self._cancel_observed[0] = True
                raise ProviderCancelledError("automation fake turn cancelled")
        token = (user_text.split() or ["EMPTY"])[0]
        chunks = ("SEEN:", token)
        if stream_sink is not None:
            for chunk in chunks:
                stream_sink(chunk)
        ended_at = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=started_at,
            ended_at=ended_at,
            final_text="".join(chunks),
            usage={},
            tool_calls=(),
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

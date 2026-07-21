"""Contracts for coding-session accepted-turn input preparation.

The preparer owns the resource-vs-literal branch, the transformed-vs-original
prompt split, the hook ordering (input hook, then ``@file`` resolution, then
image attachments, then ``before_agent_start`` suffix), the diagnostic text, and
the safe-counter recording. Every effect flows through an injected product port,
so these contracts drive the preparer with recording fakes and real resolution
data contracts and assert the exact prepared :class:`CodingAcceptedTurn` plus the
metadata-only archive boundary (transformed provider text, ``@file`` excerpts,
image bytes, and injected system-prompt context ride the returned turn only).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.loop_policy import AgentToolPolicyState
from pipy_harness.native.agent.messages import AgentUserMessage
from pipy_harness.native.agent.usage import AgentUsageAccumulator
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.coding.accepted_input import (
    CodingAcceptedInputPreparer,
    CodingAcceptedTurn,
    CodingSessionAcceptedInputRecorder,
)
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.file_references import (
    FileReferenceResolution,
    ResolvedFileReference,
)
from pipy_harness.native.image_attachment import (
    ImageAttachmentResolution,
    ProviderImageAttachment,
    ResolvedImageAttachment,
)
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import ProviderPort, StreamChunkSink


class _FakeProvider:
    @property
    def name(self) -> str:
        return "port-name"

    @property
    def model_id(self) -> str:
        return "port-model"

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del request, stream_sink, reasoning_sink, cancel_token
        raise AssertionError("accepted-input contracts never invoke the provider")


def _provider() -> ProviderPort:
    return _FakeProvider()


def _state() -> CodingSessionState:
    return CodingSessionState(
        provider=_provider(),
        provider_name="explicit-provider",
        model_id="explicit-model",
        usage_accumulator=AgentUsageAccumulator(),
        messages=(),
    )


@dataclass
class _RecordingRecorder:
    """Records the accepted-input recorder port calls for assertion."""

    tool_state: AgentToolPolicyState
    file_calls: list[tuple[int, int, int]] = field(default_factory=list)
    image_calls: list[tuple[int, int, int]] = field(default_factory=list)

    def record_file_references(
        self, *, reference_count: int, loaded_count: int, failed_count: int
    ) -> None:
        self.file_calls.append((reference_count, loaded_count, failed_count))

    def record_image_attachments(
        self, *, attachment_count: int, loaded_count: int, failed_count: int
    ) -> None:
        self.image_calls.append((attachment_count, loaded_count, failed_count))

    def snapshot_tool_state(self) -> AgentToolPolicyState:
        return self.tool_state


def _empty_files(_: str) -> FileReferenceResolution:
    return FileReferenceResolution()


def _empty_images(_: str) -> ImageAttachmentResolution:
    return ImageAttachmentResolution()


def _no_suffix(_: str) -> str | None:
    return None


def _no_context() -> tuple[ProductContent, ...]:
    return ()


def _make_preparer(
    *,
    transform_input=lambda prompt: prompt,
    resolve_file_references=_empty_files,
    resolve_image_attachments=_empty_images,
    system_prompt_suffix=_no_suffix,
    next_turn_context=_no_context,
    emit_diagnostic=lambda message: None,
    state_recorder=None,
) -> CodingAcceptedInputPreparer:
    return CodingAcceptedInputPreparer(
        transform_input=transform_input,
        resolve_file_references=resolve_file_references,
        resolve_image_attachments=resolve_image_attachments,
        system_prompt_suffix=system_prompt_suffix,
        next_turn_context=next_turn_context,
        emit_diagnostic=emit_diagnostic,
        state_recorder=state_recorder
        or _RecordingRecorder(AgentToolPolicyState(tool_budget=25)),
    )


def test_resource_turn_uses_provider_text_verbatim_and_skips_input_resolution() -> None:
    calls: list[str] = []

    def _transform(prompt: str) -> str:
        calls.append(prompt)
        return prompt + "-transformed"

    def _files(prompt: str) -> FileReferenceResolution:
        calls.append("files")
        return FileReferenceResolution()

    def _images(prompt: str) -> ImageAttachmentResolution:
        calls.append("images")
        return ImageAttachmentResolution()

    recorder = _RecordingRecorder(AgentToolPolicyState(tool_budget=7))
    preparer = _make_preparer(
        transform_input=_transform,
        resolve_file_references=_files,
        resolve_image_attachments=_images,
        state_recorder=recorder,
    )

    turn = preparer.prepare(
        user_input="ignored-literal",
        resource_provider_text="expanded skill text",
        selected_provider_content=None,
        base_system_prompt="base",
    )

    assert turn.provider_user_input == "expanded skill text"
    assert turn.turn_user_message.content == ProductContent("expanded skill text")
    assert turn.turn_attachments == ()
    # No input hook, @file, or image resolution runs on a resource turn.
    assert calls == []
    assert recorder.file_calls == []
    assert recorder.image_calls == []


def test_literal_turn_transforms_provider_text_only() -> None:
    def _transform(prompt: str) -> str:
        return prompt + "-transformed"

    turn = _make_preparer(transform_input=_transform).prepare(
        user_input="hello",
        resource_provider_text=None,
        selected_provider_content=None,
        base_system_prompt="base",
    )

    # Only the provider-visible text is transformed; the accepted user message
    # (rendered panel / prompt history / product session tree) keeps the literal.
    assert turn.provider_user_input == "hello-transformed"
    assert turn.turn_user_message.content == ProductContent("hello")


def test_selected_provider_content_becomes_accepted_message() -> None:
    def _transform(prompt: str) -> str:
        return prompt + "-transformed"

    selected = ProductContent("selected literal")
    turn = _make_preparer(transform_input=_transform).prepare(
        user_input="typed",
        resource_provider_text=None,
        selected_provider_content=selected,
        base_system_prompt="base",
    )

    assert turn.turn_user_message.content == selected
    # The transform still applies to the typed user_input for provider text.
    assert turn.provider_user_input == "typed-transformed"


def test_file_reference_counters_recorded_and_prompt_augmented() -> None:
    diagnostics: list[str] = []
    resolution = FileReferenceResolution(
        references=(
            ResolvedFileReference(
                raw="a.py",
                loaded=True,
                reason="loaded",
                text="print(1)",
                byte_count=8,
                line_count=1,
            ),
            ResolvedFileReference(
                raw="missing.py", loaded=False, reason="invalid_reference"
            ),
        ),
    )
    recorder = _RecordingRecorder(AgentToolPolicyState(tool_budget=25))

    turn = _make_preparer(
        transform_input=lambda prompt: prompt,
        resolve_file_references=lambda _: resolution,
        emit_diagnostic=diagnostics.append,
        state_recorder=recorder,
    ).prepare(
        user_input="see @a.py and @missing.py",
        resource_provider_text=None,
        selected_provider_content=None,
        base_system_prompt="base",
    )

    assert recorder.file_calls == [(2, 1, 1)]
    assert diagnostics == list(resolution.diagnostics())
    assert turn.provider_user_input == resolution.augmented_prompt(
        "see @a.py and @missing.py"
    )
    # The literal user message is untouched by augmentation.
    assert turn.turn_user_message.content == ProductContent("see @a.py and @missing.py")


def test_file_references_without_load_do_not_augment() -> None:
    resolution = FileReferenceResolution(
        references=(
            ResolvedFileReference(
                raw="missing.py", loaded=False, reason="invalid_reference"
            ),
        ),
    )
    recorder = _RecordingRecorder(AgentToolPolicyState(tool_budget=25))

    turn = _make_preparer(
        transform_input=lambda prompt: prompt + "-t",
        resolve_file_references=lambda _: resolution,
        state_recorder=recorder,
    ).prepare(
        user_input="see @missing.py",
        resource_provider_text=None,
        selected_provider_content=None,
        base_system_prompt="base",
    )

    assert recorder.file_calls == [(1, 0, 1)]
    # Nothing loaded: the provider text is the transformed prompt unchanged.
    assert turn.provider_user_input == "see @missing.py-t"


def test_image_attachment_counters_recorded_and_attachments_set() -> None:
    diagnostics: list[str] = []
    attachment = ProviderImageAttachment(
        media_type="image/png",
        data_base64="Zm9v",
        byte_count=3,
        sha256="abc",
        source_label="img.png",
    )
    resolution = ImageAttachmentResolution(
        references=(
            ResolvedImageAttachment(
                raw="img.png", loaded=True, reason="loaded", attachment=attachment
            ),
            ResolvedImageAttachment(
                raw="broken.png", loaded=False, reason="oversized_image"
            ),
        ),
    )
    recorder = _RecordingRecorder(AgentToolPolicyState(tool_budget=25))

    turn = _make_preparer(
        resolve_image_attachments=lambda _: resolution,
        emit_diagnostic=diagnostics.append,
        state_recorder=recorder,
    ).prepare(
        user_input="@image:img.png",
        resource_provider_text=None,
        selected_provider_content=None,
        base_system_prompt="base",
    )

    assert recorder.image_calls == [(2, 1, 1)]
    assert diagnostics == list(resolution.diagnostics())
    assert turn.turn_attachments == (attachment,)


def test_before_agent_start_suffix_appended_once() -> None:
    suffix_inputs: list[str] = []

    def _suffix(base_prompt: str) -> str | None:
        suffix_inputs.append(base_prompt)
        return "injected context"

    turn = _make_preparer(system_prompt_suffix=_suffix).prepare(
        user_input="hi",
        resource_provider_text=None,
        selected_provider_content=None,
        base_system_prompt="BASE",
    )

    assert suffix_inputs == ["BASE"]
    assert turn.agent_system_prompt == "BASE\ninjected context"


def test_empty_before_agent_start_suffix_leaves_base_prompt() -> None:
    turn = _make_preparer(system_prompt_suffix=lambda _: "").prepare(
        user_input="hi",
        resource_provider_text=None,
        selected_provider_content=None,
        base_system_prompt="BASE",
    )

    assert turn.agent_system_prompt == "BASE"


def test_next_turn_context_becomes_request_overlay() -> None:
    context = (ProductContent("ctx-one"), ProductContent("ctx-two"))

    turn = _make_preparer(next_turn_context=lambda: context).prepare(
        user_input="hi",
        resource_provider_text=None,
        selected_provider_content=None,
        base_system_prompt="base",
    )

    assert turn.active_input.accepted_message is turn.turn_user_message
    assert turn.active_input.request_overlay == (
        AgentUserMessage(content=ProductContent("ctx-one")),
        AgentUserMessage(content=ProductContent("ctx-two")),
    )


def test_initial_tool_state_is_recorder_snapshot() -> None:
    snapshot = AgentToolPolicyState(
        tool_budget=13,
        tool_invocation_count=4,
        malformed_argument_count=2,
        consecutive_malformed_streak=1,
        budget_exhausted_count=3,
    )
    recorder = _RecordingRecorder(snapshot)

    turn = _make_preparer(state_recorder=recorder).prepare(
        user_input="hi",
        resource_provider_text=None,
        selected_provider_content=None,
        base_system_prompt="base",
    )

    assert turn.initial_tool_state is snapshot


def test_session_recorder_snapshots_budget_and_live_counters() -> None:
    state = _state()
    state.sync_tool_policy(
        AgentToolPolicyState(
            tool_budget=99,
            tool_invocation_count=6,
            malformed_argument_count=2,
            consecutive_malformed_streak=1,
            budget_exhausted_count=4,
        )
    )
    recorder = CodingSessionAcceptedInputRecorder(state, tool_budget=42)

    recorder.record_file_references(reference_count=3, loaded_count=2, failed_count=1)
    recorder.record_image_attachments(
        attachment_count=2, loaded_count=1, failed_count=1
    )
    snapshot = recorder.snapshot_tool_state()

    # The snapshot pairs the controller-owned budget with the live counters.
    assert snapshot == AgentToolPolicyState(
        tool_budget=42,
        tool_invocation_count=6,
        malformed_argument_count=2,
        consecutive_malformed_streak=1,
        budget_exhausted_count=4,
    )
    assert state.file_reference_count == 3
    assert state.file_reference_loaded_count == 2
    assert state.image_attachment_count == 2


def test_session_recorder_rejects_non_exact_state() -> None:
    class _StateSubclass(CodingSessionState):
        pass

    substate = _StateSubclass(
        provider=_provider(),
        provider_name="p",
        model_id="m",
        usage_accumulator=AgentUsageAccumulator(),
        messages=(),
    )
    with pytest.raises(TypeError, match="exact CodingSessionState"):
        CodingSessionAcceptedInputRecorder(substate, tool_budget=1)


def test_accepted_turn_rejects_non_exact_fields() -> None:
    good = AgentUserMessage(content=ProductContent("hi"))
    good_active = AgentActiveInput(good)
    good_tool_state = AgentToolPolicyState(tool_budget=1)

    with pytest.raises(TypeError, match="turn_user_message"):
        CodingAcceptedTurn(
            turn_user_message="not-a-message",  # type: ignore[arg-type]
            active_input=good_active,
            initial_tool_state=good_tool_state,
            provider_user_input="p",
            turn_attachments=(),
            agent_system_prompt="s",
        )
    with pytest.raises(TypeError, match="provider_user_input"):
        CodingAcceptedTurn(
            turn_user_message=good,
            active_input=good_active,
            initial_tool_state=good_tool_state,
            provider_user_input=None,  # type: ignore[arg-type]
            turn_attachments=(),
            agent_system_prompt="s",
        )
    with pytest.raises(TypeError, match="turn_attachments"):
        CodingAcceptedTurn(
            turn_user_message=good,
            active_input=good_active,
            initial_tool_state=good_tool_state,
            provider_user_input="p",
            turn_attachments=("not-an-attachment",),  # type: ignore[arg-type]
            agent_system_prompt="s",
        )

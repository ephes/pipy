"""Accepted-turn input preparation for the coding-session controller.

Once a fresh or retained prompt clears the local-command boundary and is
accepted for a provider run, the controller must turn it into the exact inputs
the reusable agent loop consumes: the accepted user message, the transient
next-turn request overlay, the initial tool-policy counters, the
provider-visible prompt text, image attachments, and the augmented agent system
prompt. This module owns that translation behind injected product ports so the
controller keeps only thin adapters over its effectful helpers.

The metadata-only workflow archive boundary is preserved by construction: the
transformed provider text, ``@file`` excerpts, image bytes, and injected
system-prompt context stay on the provider-visible ``CodingAcceptedTurn`` fields
and never cross into the ports' persistence/archive paths. This module reads no
persistence, extension, UI, provider, or archive implementation directly; it
depends only on canonical ``native.agent`` contracts, the injected
``CodingSessionState`` recorder, and the ``file_references``/``image_attachment``
resolution data contracts carried by the resolver ports.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.loop_policy import AgentToolPolicyState
from pipy_harness.native.agent.messages import AgentUserMessage
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.file_references import FileReferenceResolution
from pipy_harness.native.image_attachment import (
    ImageAttachmentResolution,
    ProviderImageAttachment,
)

# Injected product ports. Each is a thin seam the controller binds around its
# existing effectful helper so this module carries no composition, UI,
# extension, provider, persistence, or archive dependency of its own.
AcceptedInputHookTransform = Callable[[str], str]
AcceptedFileReferenceResolver = Callable[[str], FileReferenceResolution]
AcceptedImageAttachmentResolver = Callable[[str], ImageAttachmentResolution]
# Returns the bounded ``before_agent_start`` suffix for this run's system
# prompt, or ``None`` when no hook injected context. The preparer owns the
# single-newline concatenation onto the base prompt.
AcceptedSystemPromptSuffixSource = Callable[[str], str | None]
AcceptedNextTurnContextSource = Callable[[], tuple[ProductContent, ...]]
AcceptedInputDiagnosticSink = Callable[[str], None]


class AcceptedInputStateRecorder(Protocol):
    """Record accepted-turn resolution counters and snapshot tool counters.

    The recorder accumulates the safe ``@file`` / image-attachment counters into
    the session state and produces the run's initial tool-policy counters. It
    exposes counters only; no provider-visible excerpt, image byte, or prompt
    text passes through it.
    """

    def record_file_references(
        self,
        *,
        reference_count: int,
        loaded_count: int,
        failed_count: int,
    ) -> None: ...

    def record_image_attachments(
        self,
        *,
        attachment_count: int,
        loaded_count: int,
        failed_count: int,
    ) -> None: ...

    def snapshot_tool_state(self) -> AgentToolPolicyState: ...


class CodingSessionAcceptedInputRecorder:
    """Bind the accepted-input recorder port to the live session state.

    Counter accumulation forwards to :class:`CodingSessionState`; the tool-state
    snapshot pairs the controller-owned ``tool_budget`` with the session's live
    tool-policy counters exactly as the inline preparation did.
    """

    def __init__(
        self,
        coding_state: CodingSessionState,
        *,
        tool_budget: int,
    ) -> None:
        if type(coding_state) is not CodingSessionState:
            raise TypeError("coding_state must be an exact CodingSessionState")
        self._coding_state = coding_state
        self._tool_budget = tool_budget

    def record_file_references(
        self,
        *,
        reference_count: int,
        loaded_count: int,
        failed_count: int,
    ) -> None:
        self._coding_state.record_file_references(
            reference_count=reference_count,
            loaded_count=loaded_count,
            failed_count=failed_count,
        )

    def record_image_attachments(
        self,
        *,
        attachment_count: int,
        loaded_count: int,
        failed_count: int,
    ) -> None:
        self._coding_state.record_image_attachments(
            attachment_count=attachment_count,
            loaded_count=loaded_count,
            failed_count=failed_count,
        )

    def snapshot_tool_state(self) -> AgentToolPolicyState:
        return AgentToolPolicyState(
            tool_budget=self._tool_budget,
            tool_invocation_count=self._coding_state.tool_invocation_count,
            malformed_argument_count=self._coding_state.malformed_argument_count,
            consecutive_malformed_streak=(
                self._coding_state.consecutive_malformed_streak
            ),
            budget_exhausted_count=self._coding_state.budget_exhausted_count,
        )


@dataclass(frozen=True, slots=True)
class CodingAcceptedTurn:
    """The exact prepared inputs one accepted prompt hands to the agent loop.

    All fields except the counter snapshot carry provider-visible content: the
    accepted user message, the transient request overlay bound to it, the
    provider prompt text (already ``@file``-augmented and input-hook
    transformed), image attachments, and the ``before_agent_start``-augmented
    agent system prompt. That content is provider-visible only and never enters
    the metadata-only workflow archive.
    """

    turn_user_message: AgentUserMessage
    active_input: AgentActiveInput
    initial_tool_state: AgentToolPolicyState
    provider_user_input: str
    turn_attachments: tuple[ProviderImageAttachment, ...]
    agent_system_prompt: str

    def __post_init__(self) -> None:
        if type(self.turn_user_message) is not AgentUserMessage:
            raise TypeError(
                "CodingAcceptedTurn.turn_user_message must be AgentUserMessage"
            )
        if type(self.active_input) is not AgentActiveInput:
            raise TypeError("CodingAcceptedTurn.active_input must be AgentActiveInput")
        if type(self.initial_tool_state) is not AgentToolPolicyState:
            raise TypeError(
                "CodingAcceptedTurn.initial_tool_state must be AgentToolPolicyState"
            )
        if not isinstance(self.provider_user_input, str):
            raise TypeError("CodingAcceptedTurn.provider_user_input must be str")
        if not isinstance(self.turn_attachments, tuple) or any(
            type(attachment) is not ProviderImageAttachment
            for attachment in self.turn_attachments
        ):
            raise TypeError(
                "CodingAcceptedTurn.turn_attachments must be a tuple of "
                "ProviderImageAttachment values"
            )
        if not isinstance(self.agent_system_prompt, str):
            raise TypeError("CodingAcceptedTurn.agent_system_prompt must be str")


class CodingAcceptedInputPreparer:
    """Translate one accepted prompt into a :class:`CodingAcceptedTurn`.

    The preparer owns the resource-vs-literal branch, the transformed-vs-original
    prompt split, the hook ordering (input hook, then ``@file`` resolution, then
    image attachments, then ``before_agent_start`` augmentation), the diagnostic
    text, and the safe-counter recording. It performs no effect itself: every
    transform, resolution, augmentation, next-turn-context read, diagnostic
    emission, and counter record flows through an injected product port.
    """

    def __init__(
        self,
        *,
        transform_input: AcceptedInputHookTransform,
        resolve_file_references: AcceptedFileReferenceResolver,
        resolve_image_attachments: AcceptedImageAttachmentResolver,
        system_prompt_suffix: AcceptedSystemPromptSuffixSource,
        next_turn_context: AcceptedNextTurnContextSource,
        emit_diagnostic: AcceptedInputDiagnosticSink,
        state_recorder: AcceptedInputStateRecorder,
    ) -> None:
        self._transform_input = transform_input
        self._resolve_file_references = resolve_file_references
        self._resolve_image_attachments = resolve_image_attachments
        self._system_prompt_suffix = system_prompt_suffix
        self._next_turn_context = next_turn_context
        self._emit_diagnostic = emit_diagnostic
        self._state_recorder = state_recorder

    def prepare(
        self,
        *,
        user_input: str,
        resource_provider_text: str | None,
        selected_provider_content: ProductContent | None,
        base_system_prompt: str,
    ) -> CodingAcceptedTurn:
        turn_attachments: tuple[ProviderImageAttachment, ...] = ()
        if resource_provider_text is not None:
            # Resource turn: the bounded instruction/expansion is the provider
            # message verbatim. `input` hook transform, @file augmentation, and
            # image attachments are all skipped so the literal resource text
            # never leaks past the provider or into the metadata-only archive.
            provider_user_input = resource_provider_text
            accepted_user_content = ProductContent(provider_user_input)
        else:
            # The original prompt still drives the rendered panel, prompt
            # history, and native product session tree; only the
            # provider-visible text and @file/image resolution use the
            # transformed value.
            accepted_user_content = (
                selected_provider_content
                if selected_provider_content is not None
                else ProductContent(user_input)
            )
            transformed_input = self._transform_input(user_input)
            provider_user_input = transformed_input
            file_references = self._resolve_file_references(transformed_input)
            if file_references.reference_count:
                self._state_recorder.record_file_references(
                    reference_count=file_references.reference_count,
                    loaded_count=file_references.loaded_count,
                    failed_count=file_references.failed_count,
                )
                for diagnostic in file_references.diagnostics():
                    self._emit_diagnostic(diagnostic)
                if file_references.used:
                    provider_user_input = file_references.augmented_prompt(
                        transformed_input
                    )
            image_attachments = self._resolve_image_attachments(transformed_input)
            if image_attachments.reference_count:
                self._state_recorder.record_image_attachments(
                    attachment_count=image_attachments.reference_count,
                    loaded_count=image_attachments.loaded_count,
                    failed_count=image_attachments.failed_count,
                )
                for diagnostic in image_attachments.diagnostics():
                    self._emit_diagnostic(diagnostic)
                turn_attachments = image_attachments.attachments()
        turn_user_message = AgentUserMessage(content=accepted_user_content)
        # `before_agent_start` hooks may inject bounded context into this run's
        # system prompt. Computed before the next-turn-context read to preserve
        # the exact hook ordering; the injected suffix is appended exactly once
        # with a single newline. The injected text is provider-visible but not
        # added to the metadata archive.
        agent_system_prompt = base_system_prompt
        system_prompt_suffix = self._system_prompt_suffix(base_system_prompt)
        if system_prompt_suffix:
            agent_system_prompt = base_system_prompt + "\n" + system_prompt_suffix
        active_input = AgentActiveInput(
            turn_user_message,
            tuple(
                AgentUserMessage(content=content)
                for content in self._next_turn_context()
            ),
        )
        initial_tool_state = self._state_recorder.snapshot_tool_state()
        return CodingAcceptedTurn(
            turn_user_message=turn_user_message,
            active_input=active_input,
            initial_tool_state=initial_tool_state,
            provider_user_input=provider_user_input,
            turn_attachments=turn_attachments,
            agent_system_prompt=agent_system_prompt,
        )

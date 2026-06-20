"""Native pipy agent session bootstrap."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import isfinite
from typing import Callable, Mapping

from pipy_harness.adapters.base import EventSink
from pipy_harness.capture import sanitize_metadata, sanitize_text
from pipy_harness.native._provider_helpers import utc_now
from pipy_harness.models import HarnessStatus
from pipy_harness.native.conversation import (
    NativeConversationState,
    NativeNoToolReplConversationContext,
    NativeTurnMetadata,
)
from pipy_harness.native.fake import FakeNoOpNativeTool
from pipy_harness.native.image_attachment import (
    ProviderImageAttachment,
)
from pipy_harness.native.models import (
    NATIVE_PATCH_APPLY_RECORDED_EVENT,
    NATIVE_PATCH_PROPOSAL_RECORDED_EVENT,
    NATIVE_TOOL_OBSERVATION_PAYLOAD_KEYS,
    NATIVE_TOOL_OBSERVATION_RECORDED_EVENT,
    NATIVE_VERIFICATION_RECORDED_EVENT,
    PROVIDER_PATCH_PROPOSAL_METADATA_KEY,
    PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY,
    NativePatchApplyRequest,
    NativePatchProposal,
    NativePatchProposalOperation,
    NativePatchProposalReason,
    NativePatchProposalStatus,
    NativeRunInput,
    NativeReadOnlyToolRequest,
    NativeReadOnlyToolRequestKind,
    NativeRunOutput,
    NativeToolApprovalMode,
    NativeToolApprovalPolicy,
    NativeToolIntent,
    NativeToolObservation,
    NativeToolObservationReason,
    NativeToolObservationStatus,
    NativeToolRequest,
    NativeToolRequestIdentity,
    NativeToolResult,
    NativeToolSandboxPolicy,
    NativeToolSandboxMode,
    NativeToolStatus,
    NativeVerificationRequest,
    PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY,
    PROVIDER_TOOL_INTENT_METADATA_KEY,
    ProviderRequest,
    ProviderResult,
)
from pipy_harness.native.patch_apply import (
    NativePatchApplyApprovalDecision,
    NativePatchApplyGateDecision,
    NativePatchApplyReason,
    NativePatchApplyResult,
    NativePatchApplyTool,
)
from pipy_harness.native.provider import ProviderPort, StreamChunkSink
from pipy_harness.native.read_only_tool import (
    NativeExplicitFileExcerptResult,
    NativeExplicitFileExcerptTarget,
    NativeExplicitFileExcerptTool,
    NativeReadOnlyApprovalDecision,
    NativeReadOnlyGateDecision,
)
from pipy_harness.native.tool import ToolPort
from pipy_harness.native.usage import normalize_provider_usage
from pipy_harness.native.workspace_context import (
    WorkspaceInstructionLoader,
    compose_system_prompt,
    empty_workspace_instruction_loader,
    workspace_instruction_safe_metadata,
)
from pipy_harness.native.verification import (
    NativeVerificationApprovalDecision,
    NativeVerificationGateDecision,
    NativeVerificationReason,
    NativeVerificationResult,
    NativeVerificationTool,
    safe_verification_command_label,
)

SYSTEM_PROMPT_ID = "pipy-native-bootstrap"
SYSTEM_PROMPT_VERSION = "1"
NOOP_TOOL_NAME = "noop"
NOOP_TOOL_KIND = "internal_noop"
READ_ONLY_TOOL_NAME = "read_only_repo_inspection"
READ_ONLY_TOOL_KIND = "read_only_workspace"
_SUPPORTED_READ_ONLY_FIXTURE_SOURCE = "pipy_owned_explicit_file_excerpt"
_SUPPORTED_PATCH_PROPOSAL_SOURCE = "pipy_owned_patch_proposal"
TOOL_INTENT_UNSUPPORTED_NAME = "unsupported"
TOOL_INTENT_UNSUPPORTED_KIND = "unsupported_intent"
TOOL_INTENT_UNSAFE_NAME = "unsafe"
TOOL_INTENT_UNSAFE_KIND = "unsafe_intent"
_SUPPORTED_INTENT_SOURCES = {"fake_provider", "provider_metadata"}
_SUPPORTED_OBSERVATION_FIXTURE_SOURCE = "synthetic_safe_noop"
_SUPPORTED_OBSERVATION_STATUSES = {
    (NativeToolObservationStatus.SUCCEEDED.value, NativeToolObservationReason.TOOL_RESULT_SUCCEEDED.value)
}
_SUPPORTED_PATCH_PROPOSAL_STATUSES = {
    (NativePatchProposalStatus.PROPOSED.value, NativePatchProposalReason.STRUCTURED_PROPOSAL_ACCEPTED.value)
}
_SAFE_INTENT_METADATA_KEYS = {
    "fixture",
    "internal_noop",
    "provider_visible_context",
    "request_kind",
    "safe_count",
    "scope_label",
    "tool_payloads_stored",
    "workspace_inspected",
    "workspace_mutated",
}
_ALLOWED_INTENT_KEYS = {
    "request_id",
    "tool_name",
    "tool_kind",
    "turn_index",
    "intent_source",
    "approval_policy",
    "approval_required",
    "sandbox_policy",
    "filesystem_mutation_allowed",
    "shell_execution_allowed",
    "network_access_allowed",
    "workspace_read_allowed",
    "tool_payloads_stored",
    "stdout_stored",
    "stderr_stored",
    "diffs_stored",
    "file_contents_stored",
    "metadata",
}
_ALLOWED_OBSERVATION_FIXTURE_KEYS = set(NATIVE_TOOL_OBSERVATION_PAYLOAD_KEYS) | {"fixture_source"}
_ALLOWED_READ_ONLY_FIXTURE_KEYS = {
    "fixture_source",
    "tool_request_id",
    "turn_index",
    "request_kind",
    "approval_decision",
    "decision_authority",
    "decision_reason_label",
    "workspace_relative_path",
    "target_authority",
    "scope_label",
}
_ALLOWED_PATCH_PROPOSAL_KEYS = {
    "proposal_source",
    "tool_request_id",
    "turn_index",
    "status",
    "reason_label",
    "file_count",
    "operation_count",
    "operation_labels",
    "patch_text_stored",
    "diffs_stored",
    "file_contents_stored",
    "prompt_stored",
    "model_output_stored",
    "provider_responses_stored",
    "raw_transcript_imported",
    "workspace_mutated",
}
_SAFE_PROVIDER_METADATA_UNSUPPORTED_LABEL = "<unsupported>"
_SAFE_PROVIDER_METADATA_HTTP_STATUS_MIN = 100
_SAFE_PROVIDER_METADATA_HTTP_STATUS_MAX = 599
_GENERATED_PROVIDER_METADATA_PRESENT_KEYS = {
    "tool_intent_metadata_present",
    "tool_observation_fixture_metadata_present",
    "read_only_tool_fixture_metadata_present",
    "patch_proposal_metadata_present",
}

# Closed enum sets for the three string-shaped allowlisted provider-metadata
# fields. Real adapters constrain their own outputs through
# `_safe_response_label` (see `openrouter_provider.py` /
# `openai_codex_provider.py`) plus an explicit pipy-internal
# `"unknown"` / `"failed"` default in error paths, so these enums are
# wide enough to cover every value the production adapters actually
# emit. Any other string value — including a short literal an
# adversarial or future provider might use to smuggle instruction text
# (for example "DO_NOT_ARCHIVE" or a snippet of an AGENTS.md line) — is
# replaced with `<unsupported>` so the archive carries only known-safe
# labels.
_PROVIDER_RESPONSE_STATUS_ALLOWED: frozenset[str] = frozenset(
    {
        "cancelled",
        "completed",
        "failed",
        "in_progress",
        "incomplete",
        "queued",
        "unknown",
    }
)
_PROVIDER_RESPONSE_OBJECT_ALLOWED: frozenset[str] = frozenset(
    {
        "chat.completion",
        "chat.completion.chunk",
        "unknown",
    }
)
_PROVIDER_FINISH_REASON_ALLOWED: frozenset[str] = frozenset(
    {
        "content_filter",
        "function_call",
        "length",
        "stop",
        "tool_calls",
        "unknown",
    }
)


def _project_safe_bool_metadata(value: object) -> object:
    if value is True or value is False:
        return value
    return _SAFE_PROVIDER_METADATA_UNSUPPORTED_LABEL


def _project_safe_http_status_metadata(value: object) -> object:
    if isinstance(value, bool):
        return _SAFE_PROVIDER_METADATA_UNSUPPORTED_LABEL
    if not isinstance(value, int):
        return _SAFE_PROVIDER_METADATA_UNSUPPORTED_LABEL
    if not (
        _SAFE_PROVIDER_METADATA_HTTP_STATUS_MIN
        <= value
        <= _SAFE_PROVIDER_METADATA_HTTP_STATUS_MAX
    ):
        return _SAFE_PROVIDER_METADATA_UNSUPPORTED_LABEL
    return value


def _make_enum_projector(allowed: frozenset[str]) -> Callable[[object], object]:
    def project(value: object) -> object:
        if not isinstance(value, str):
            return _SAFE_PROVIDER_METADATA_UNSUPPORTED_LABEL
        if value not in allowed:
            return _SAFE_PROVIDER_METADATA_UNSUPPORTED_LABEL
        return value

    return project


# Allowlist of provider-emitted metadata keys, each paired with a strict
# per-key projector. Keys not in this mapping are dropped entirely. Values
# whose type, shape, or enum membership does not match are replaced with
# the deterministic `<unsupported>` sentinel so an adversarial or future
# provider cannot route the composed system prompt back into the archive
# by stuffing it into an allowlisted field — including with a short
# string that fits inside any previous length cap.
_SAFE_PROVIDER_METADATA_PROJECTORS: dict[str, Callable[[object], object]] = {
    "provider_response_store_requested": _project_safe_bool_metadata,
    "http_status": _project_safe_http_status_metadata,
    "response_status": _make_enum_projector(_PROVIDER_RESPONSE_STATUS_ALLOWED),
    "response_object": _make_enum_projector(_PROVIDER_RESPONSE_OBJECT_ALLOWED),
    "finish_reason": _make_enum_projector(_PROVIDER_FINISH_REASON_ALLOWED),
}
INITIAL_PROVIDER_TURN_LABEL = "initial"
POST_TOOL_OBSERVATION_PROVIDER_TURN_LABEL = "post_tool_observation"


@dataclass(frozen=True, slots=True)
class _ParsedToolIntent:
    intent: NativeToolIntent | None = None
    skipped_request: NativeToolRequest | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _ParsedToolObservationFixture:
    observation: NativeToolObservation | None = None
    skipped_observation: NativeToolObservation | None = None


@dataclass(frozen=True, slots=True)
class _ParsedReadOnlyToolFixture:
    request: NativeReadOnlyToolRequest | None = None
    gate_decision: NativeReadOnlyGateDecision | None = None
    target: NativeExplicitFileExcerptTarget | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _ParsedPatchProposal:
    proposal: NativePatchProposal | None = None


@dataclass(slots=True)
class NativeAgentSession:
    """Owns one minimal native pipy turn."""

    provider: ProviderPort
    tool: ToolPort = field(default_factory=FakeNoOpNativeTool)
    patch_apply_request: NativePatchApplyRequest | None = None
    patch_apply_gate: NativePatchApplyGateDecision | None = None
    verification_request: NativeVerificationRequest | None = None
    verification_gate: NativeVerificationGateDecision | None = None
    instruction_loader: WorkspaceInstructionLoader = field(
        default=empty_workspace_instruction_loader
    )
    stream_sink: StreamChunkSink | None = None

    def run(self, run_input: NativeRunInput, event_sink: EventSink) -> NativeRunOutput:
        started_at = utc_now()
        conversation_state = NativeConversationState.for_native_run(max_turns=2)
        discovery = self.instruction_loader(run_input.cwd)
        composed_system_prompt = compose_system_prompt(
            NATIVE_BOOTSTRAP_SYSTEM_PROMPT, discovery
        )
        safe_context = {
            **_safe_context(run_input),
            **workspace_instruction_safe_metadata(discovery),
        }
        event_sink.emit(
            "native.session.started",
            summary=(
                "Native pipy session started: "
                f"provider={sanitize_text(run_input.provider_name)}, model={sanitize_text(run_input.model_id)}."
            ),
            payload={
                **safe_context,
                "status": HarnessStatus.RUNNING.value,
            },
        )

        conversation_state, provider_turn = _append_provider_turn(
            conversation_state,
            provider_turn_label=INITIAL_PROVIDER_TURN_LABEL,
        )
        provider_result, provider_usage = _call_provider_turn(
            self.provider,
            run_input,
            event_sink,
            safe_context,
            user_prompt=run_input.goal,
            provider_turn=provider_turn,
            tool_observation=None,
            system_prompt=composed_system_prompt,
            stream_sink=self.stream_sink,
        )

        tool_result: NativeToolResult | None = None
        read_only_result: NativeExplicitFileExcerptResult | None = None
        observation_failure_reason: NativeToolObservationReason | None = None
        follow_up_provider_result: ProviderResult | None = None
        follow_up_provider_usage: dict[str, int | float] = {}
        patch_apply_result: NativePatchApplyResult | None = None
        verification_result: NativeVerificationResult | None = None
        if provider_result.status == HarnessStatus.SUCCEEDED:
            parsed_intent = _parse_tool_intent(provider_result)
            if parsed_intent.intent is not None:
                _emit_tool_intent_detected(event_sink, safe_context, parsed_intent.intent)
                if _is_read_only_intent(parsed_intent.intent):
                    tool_result, read_only_result = self._invoke_read_only_tool(
                        run_input,
                        event_sink,
                        safe_context,
                        provider_result,
                        parsed_intent.intent,
                    )
                else:
                    tool_result = self._invoke_noop_tool(event_sink, safe_context, parsed_intent.intent)
                if tool_result.status == NativeToolStatus.SUCCEEDED:
                    if read_only_result is not None:
                        observation = _read_only_observation(read_only_result)
                        _emit_tool_observation_recorded(event_sink, safe_context, observation)
                        conversation_state, follow_up_provider_turn = _append_provider_turn(
                            conversation_state,
                            provider_turn_label=POST_TOOL_OBSERVATION_PROVIDER_TURN_LABEL,
                        )
                        # Streaming is scoped to the initial provider turn per the
                        # Streaming Output Parity Track in docs/backlog.md; the
                        # post-tool follow-up turn intentionally stays buffered.
                        follow_up_provider_result, follow_up_provider_usage = _call_provider_turn(
                            self.provider,
                            run_input,
                            event_sink,
                            safe_context,
                            user_prompt=_build_post_tool_user_prompt(observation, read_only_result),
                            provider_turn=follow_up_provider_turn,
                            tool_observation=observation,
                            system_prompt=composed_system_prompt,
                        )
                        if follow_up_provider_result.status == HarnessStatus.SUCCEEDED:
                            parsed_proposal = _parse_patch_proposal(follow_up_provider_result)
                            if parsed_proposal.proposal is not None:
                                _emit_patch_proposal_recorded(
                                    event_sink,
                                    safe_context,
                                    parsed_proposal.proposal,
                                )
                                if (
                                    parsed_proposal.proposal.status == NativePatchProposalStatus.PROPOSED
                                    and self.patch_apply_request is not None
                                ):
                                    patch_apply_result = self._invoke_patch_apply(
                                        run_input,
                                        event_sink,
                                        safe_context,
                                    )
                                    if (
                                        patch_apply_result.status == NativeToolStatus.SUCCEEDED
                                        and self.verification_request is not None
                                    ):
                                        verification_result = self._invoke_verification(
                                            run_input,
                                            event_sink,
                                            safe_context,
                                        )
                    else:
                        parsed_observation = _parse_tool_observation_fixture(provider_result, tool_result)
                        if parsed_observation.observation is not None:
                            _emit_tool_observation_recorded(
                                event_sink,
                                safe_context,
                                parsed_observation.observation,
                            )
                            conversation_state, follow_up_provider_turn = _append_provider_turn(
                                conversation_state,
                                provider_turn_label=POST_TOOL_OBSERVATION_PROVIDER_TURN_LABEL,
                            )
                            follow_up_provider_result, follow_up_provider_usage = _call_provider_turn(
                                self.provider,
                                run_input,
                                event_sink,
                                safe_context,
                                user_prompt=_build_post_tool_user_prompt(parsed_observation.observation),
                                provider_turn=follow_up_provider_turn,
                                tool_observation=parsed_observation.observation,
                                system_prompt=composed_system_prompt,
                            )
                        elif parsed_observation.skipped_observation is not None:
                            observation_failure_reason = parsed_observation.skipped_observation.reason_label
                            _emit_tool_observation_recorded(
                                event_sink,
                                safe_context,
                                parsed_observation.skipped_observation,
                            )
            elif parsed_intent.skipped_request is not None:
                tool_result = _skipped_tool_result(
                    parsed_intent.skipped_request,
                    error_type="NativeToolIntentSkipped",
                    error_message=parsed_intent.reason or "tool_intent_skipped",
                )
                _emit_tool_result_event(
                    event_sink,
                    safe_context,
                    parsed_intent.skipped_request,
                    tool_result,
                    reason=parsed_intent.reason,
                )
        else:
            tool_request = _noop_tool_request()
            tool_result = _skipped_tool_result(
                tool_request,
                error_type="NativeToolSkipped",
                error_message="provider_not_succeeded",
            )
            _emit_tool_result_event(
                event_sink,
                safe_context,
                tool_request,
                tool_result,
                reason="provider_not_succeeded",
            )

        final_provider_result = follow_up_provider_result or provider_result
        final_usage = _merge_provider_usage(provider_usage, follow_up_provider_usage)
        ended_at = utc_now()
        final_status, error_type, error_message = _final_outcome(
            provider_result,
            tool_result,
            observation_failure_reason=observation_failure_reason,
            follow_up_provider_result=follow_up_provider_result,
            patch_apply_result=patch_apply_result,
            verification_result=verification_result,
        )
        exit_code = 0 if final_status == HarnessStatus.SUCCEEDED else 1
        event_sink.emit(
            "native.session.completed",
            summary=f"Native pipy session completed: status={final_status.value}.",
            payload={
                **safe_context,
                "status": final_status.value,
                "exit_code": exit_code,
                "duration_seconds": _duration_seconds(started_at, ended_at),
            },
        )
        return NativeRunOutput(
            status=final_status,
            exit_code=exit_code,
            started_at=started_at,
            ended_at=ended_at,
            final_text=final_provider_result.final_text if final_status == HarnessStatus.SUCCEEDED else None,
            provider_name=final_provider_result.provider_name,
            model_id=final_provider_result.model_id,
            usage=final_usage,
            error_type=error_type,
            error_message=error_message,
        )

    def _invoke_noop_tool(
        self,
        event_sink: EventSink,
        safe_context: Mapping[str, object],
        intent: NativeToolIntent,
    ) -> NativeToolResult:
        tool_request = _tool_request_from_intent(intent)
        event_sink.emit(
            "native.tool.started",
            summary=(
                "Native tool invocation started: "
                f"tool={sanitize_text(tool_request.tool_name)}, kind={sanitize_text(tool_request.tool_kind)}."
            ),
            payload={
                **safe_context,
                **_safe_tool_context(tool_request),
                "status": NativeToolStatus.RUNNING.value,
            },
        )
        tool_started_at = utc_now()
        try:
            tool_result = self.tool.invoke(tool_request)
        except Exception as exc:
            tool_result = _failed_tool_result(tool_request, exc, started_at=tool_started_at)
        _emit_tool_result_event(event_sink, safe_context, tool_request, tool_result)
        return tool_result

    def _invoke_read_only_tool(
        self,
        run_input: NativeRunInput,
        event_sink: EventSink,
        safe_context: Mapping[str, object],
        provider_result: ProviderResult,
        intent: NativeToolIntent,
    ) -> tuple[NativeToolResult, NativeExplicitFileExcerptResult | None]:
        tool_request = _tool_request_from_intent(intent)
        parsed_fixture = _parse_read_only_tool_fixture(provider_result)
        if (
            parsed_fixture.request is None
            or parsed_fixture.gate_decision is None
            or parsed_fixture.target is None
        ):
            tool_result = _skipped_tool_result(
                tool_request,
                error_type="NativeReadOnlyToolSkipped",
                error_message=parsed_fixture.reason or "unsafe_read_only_context",
            )
            _emit_tool_result_event(
                event_sink,
                safe_context,
                tool_request,
                tool_result,
                reason=parsed_fixture.reason or "unsafe_read_only_context",
            )
            return tool_result, None

        event_sink.emit(
            "native.tool.started",
            summary=(
                "Native tool invocation started: "
                f"tool={sanitize_text(tool_request.tool_name)}, kind={sanitize_text(tool_request.tool_kind)}."
            ),
            payload={
                **safe_context,
                **_safe_tool_context(tool_request),
                "status": NativeToolStatus.RUNNING.value,
            },
        )
        try:
            read_only_result = NativeExplicitFileExcerptTool(run_input.cwd).invoke(
                parsed_fixture.request,
                parsed_fixture.gate_decision,
                parsed_fixture.target,
            )
        except Exception as exc:
            tool_result = _failed_tool_result(tool_request, exc, started_at=utc_now())
            _emit_tool_result_event(event_sink, safe_context, tool_request, tool_result)
            return tool_result, None

        tool_result = _tool_result_from_read_only_result(read_only_result)
        _emit_tool_result_event(event_sink, safe_context, tool_request, tool_result)
        if read_only_result.status != NativeToolStatus.SUCCEEDED or read_only_result.excerpt is None:
            return tool_result, None
        return tool_result, read_only_result

    def _invoke_patch_apply(
        self,
        run_input: NativeRunInput,
        event_sink: EventSink,
        safe_context: Mapping[str, object],
    ) -> NativePatchApplyResult:
        if self.patch_apply_request is None:
            raise RuntimeError("patch apply request is required")
        gate = self.patch_apply_gate
        if gate is None:
            gate = NativePatchApplyGateDecision(
                approval_decision=NativePatchApplyApprovalDecision.SKIPPED
            )
        try:
            result = NativePatchApplyTool(run_input.cwd).invoke(self.patch_apply_request, gate)
        except Exception as exc:
            result = _failed_patch_apply_result(self.patch_apply_request, gate, exc)
        _emit_patch_apply_recorded(event_sink, safe_context, result)
        return result

    def _invoke_verification(
        self,
        run_input: NativeRunInput,
        event_sink: EventSink,
        safe_context: Mapping[str, object],
    ) -> NativeVerificationResult:
        if self.verification_request is None:
            raise RuntimeError("verification request is required")
        gate = self.verification_gate
        if gate is None:
            gate = NativeVerificationGateDecision(
                approval_decision=NativeVerificationApprovalDecision.SKIPPED
            )
        try:
            result = NativeVerificationTool(run_input.cwd).invoke(self.verification_request, gate)
        except Exception:
            result = _failed_verification_result(self.verification_request, gate)
        _emit_verification_recorded(event_sink, safe_context, result)
        return result


NATIVE_BOOTSTRAP_SYSTEM_PROMPT: str = (
    "You are the native pipy runtime bootstrap. Complete exactly one minimal "
    "provider turn and do not execute tools."
)


NATIVE_TOOL_LOOP_SYSTEM_PROMPT: str = (
    "You are pipy-native, a local coding-agent harness running in the user's "
    "terminal. You help the user by reading and editing files, exploring "
    "directories, running commands, and answering questions about the current "
    "workspace and any configured reference roots.\n"
    "\n"
    "Available tools:\n"
    "- read: read a bounded UTF-8 file excerpt\n"
    "- ls: list directory entries\n"
    "- grep: literal-string search across files\n"
    "- find: glob-pattern path search\n"
    "- write/edit/edit_diff: workspace mutations (workspace-only)\n"
    "- truncate: shrink a prior tool result\n"
    "- bash: run a shell command in the workspace. This is a real shell — "
    "pipes, redirection, command substitution, and any executable on PATH are "
    "allowed. Use it to run tests, builds, git, and other commands; combined "
    "stdout/stderr streams back as it is produced. Optionally pass a timeout "
    "in seconds.\n"
    "\n"
    "Use these tools directly to carry out what the user asks. When asked to "
    "run the tests, build, or run any command, call the bash tool (for example "
    "`just test`, `uv run pytest`, or `git status`) — do not refuse or claim a "
    "shell is unavailable. Reference roots (when present) are read-only sibling "
    "projects you may pass as absolute paths to read/ls/grep/find (for example "
    "'/Users/me/src/other-repo/README.md' or '/Users/me/src/other-repo').\n"
    "\n"
    "The read/ls/grep/find readers and the write/edit mutation tools refuse "
    "paths under .git and matching .gitignore and filter binary, "
    "control-character, and secret-looking content; the bash tool is a real "
    "shell without those path restrictions, and mutation tools always stay "
    "inside the workspace. Be concise in your responses and show file paths "
    "clearly when working with files. When the user asks a 'where are we' or "
    "'feature parity' question, prefer to inspect the relevant docs and "
    "source files rather than guessing."
)


def _build_system_prompt() -> str:
    return NATIVE_BOOTSTRAP_SYSTEM_PROMPT


def _call_provider_turn(
    provider: ProviderPort,
    run_input: NativeRunInput,
    event_sink: EventSink,
    safe_context: Mapping[str, object],
    *,
    user_prompt: str,
    provider_turn: NativeTurnMetadata,
    tool_observation: NativeToolObservation | None,
    archive_provider_metadata: bool = True,
    no_tool_repl_context: NativeNoToolReplConversationContext | None = None,
    system_prompt: str | None = None,
    stream_sink: StreamChunkSink | None = None,
    attachments: tuple[ProviderImageAttachment, ...] = (),
) -> tuple[ProviderResult, dict[str, int | float]]:
    effective_system_prompt = (
        system_prompt if system_prompt is not None else _build_system_prompt()
    )
    provider_turn_label = _required_provider_turn_label(provider_turn)
    provider_turn_context = {
        "provider_turn_index": provider_turn.turn_index,
        "provider_turn_label": provider_turn_label,
    }
    no_tool_context_payload = (
        no_tool_repl_context.safe_metadata() if no_tool_repl_context is not None else {}
    )
    event_sink.emit(
        "native.provider.started",
        summary=(
            "Native provider call started: "
            f"provider={sanitize_text(run_input.provider_name)}, model={sanitize_text(run_input.model_id)}, "
            f"turn={sanitize_text(provider_turn_label)}."
        ),
        payload={
            **safe_context,
            **provider_turn_context,
            **no_tool_context_payload,
            "status": HarnessStatus.RUNNING.value,
        },
    )

    provider_started_at = utc_now()
    try:
        provider_result = provider.complete(
            ProviderRequest(
                system_prompt=effective_system_prompt,
                user_prompt=user_prompt,
                provider_name=run_input.provider_name,
                model_id=run_input.model_id,
                cwd=run_input.cwd,
                provider_turn_index=provider_turn.turn_index,
                provider_turn_label=provider_turn_label,
                tool_observation=tool_observation,
                no_tool_repl_context=no_tool_repl_context,
                attachments=attachments,
            ),
            stream_sink=stream_sink,
        )
    except Exception as exc:
        provider_result = _failed_provider_result(run_input, exc, started_at=provider_started_at)

    provider_event = (
        "native.provider.completed"
        if provider_result.status == HarnessStatus.SUCCEEDED
        else "native.provider.failed"
    )
    provider_usage = normalize_provider_usage(provider_result.usage or {})
    event_sink.emit(
        provider_event,
        summary=(
            "Native provider call finished: "
            f"status={provider_result.status.value}, provider={sanitize_text(provider_result.provider_name)}, "
            f"model={sanitize_text(provider_result.model_id)}, turn={sanitize_text(provider_turn_label)}."
        ),
        payload={
            **safe_context,
            **provider_turn_context,
            **no_tool_context_payload,
            "status": provider_result.status.value,
            "duration_seconds": _duration_seconds(provider_result.started_at, provider_result.ended_at),
            "usage": provider_usage,
            "provider_metadata": (
                _safe_provider_metadata(
                    provider_result.metadata or {},
                    patch_proposal_supported=_supports_patch_proposal_metadata(tool_observation),
                )
                if archive_provider_metadata
                else {}
            ),
            "error_type": _safe_optional_text(provider_result.error_type),
            "error_message": _safe_optional_text(provider_result.error_message),
        },
    )
    return provider_result, provider_usage


def _append_provider_turn(
    conversation_state: NativeConversationState,
    *,
    provider_turn_label: str,
) -> tuple[NativeConversationState, NativeTurnMetadata]:
    next_state = conversation_state.append_provider_turn(
        provider_turn_label=provider_turn_label,
    )
    return next_state, next_state.turns[-1].metadata


def _required_provider_turn_label(provider_turn: NativeTurnMetadata) -> str:
    if provider_turn.provider_turn_label is None:
        raise ValueError("provider turn metadata requires a provider turn label")
    return provider_turn.provider_turn_label


def _safe_context(run_input: NativeRunInput) -> dict[str, object]:
    return {
        "adapter": "pipy-native",
        "provider": run_input.provider_name,
        "model_id": run_input.model_id,
        "system_prompt_id": run_input.system_prompt_id,
        "system_prompt_version": run_input.system_prompt_version,
        "prompt_stored": False,
        "model_output_stored": False,
        "tool_payloads_stored": False,
        "raw_transcript_imported": False,
    }


def _supports_patch_proposal_metadata(tool_observation: NativeToolObservation | None) -> bool:
    return (
        tool_observation is not None
        and tool_observation.tool_name == READ_ONLY_TOOL_NAME
        and tool_observation.tool_kind == READ_ONLY_TOOL_KIND
        and tool_observation.status == NativeToolObservationStatus.SUCCEEDED
    )


def _safe_provider_metadata(
    metadata: Mapping[str, object],
    *,
    patch_proposal_supported: bool,
) -> dict[str, object]:
    """Project provider-returned metadata into a fixed allowlist of safe keys.

    The allowlist is the only way provider metadata reaches the session
    archive: scalar status/finish-reason markers documented in
    `_SAFE_PROVIDER_METADATA_KEYS` plus the four pipy-owned sentinel
    `*_metadata_present` booleans synthesized below. Any other key — top
    level or nested — never lands in the JSONL or Markdown. This closes
    the family of leaks where a provider (or a hostile / future
    `ProviderPort`) echoes the composed system prompt back through fields
    like `system_prompt`, `instructions`, `input`, `messages`, or nested
    wrappers such as `request.system_prompt`.
    """

    safe_metadata: dict[str, object] = {}
    if PROVIDER_TOOL_INTENT_METADATA_KEY in metadata:
        safe_metadata["tool_intent_metadata_present"] = True
    if PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY in metadata:
        safe_metadata["tool_observation_fixture_metadata_present"] = True
    if PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY in metadata:
        safe_metadata["read_only_tool_fixture_metadata_present"] = True
    if (
        PROVIDER_PATCH_PROPOSAL_METADATA_KEY in metadata
        and patch_proposal_supported
    ):
        safe_metadata["patch_proposal_metadata_present"] = True
    for safe_key, projector in _SAFE_PROVIDER_METADATA_PROJECTORS.items():
        if safe_key in metadata:
            safe_metadata[safe_key] = projector(metadata[safe_key])
    return sanitize_metadata(safe_metadata)


def _parse_tool_intent(provider_result: ProviderResult) -> _ParsedToolIntent:
    metadata = provider_result.metadata or {}
    if PROVIDER_TOOL_INTENT_METADATA_KEY not in metadata:
        return _ParsedToolIntent()

    identity = NativeToolRequestIdentity.current_noop()
    raw_intent = metadata[PROVIDER_TOOL_INTENT_METADATA_KEY]
    if not isinstance(raw_intent, Mapping):
        return _ParsedToolIntent(
            skipped_request=_skipped_intent_tool_request(identity, "unsafe_tool_intent_shape"),
            reason="unsafe_tool_intent_shape",
        )

    reason = _unsafe_intent_reason(raw_intent, identity)
    if reason is not None:
        return _ParsedToolIntent(skipped_request=_skipped_intent_tool_request(identity, reason), reason=reason)

    tool_name = raw_intent.get("tool_name")
    tool_kind = raw_intent.get("tool_kind")
    if (tool_name, tool_kind) not in {
        (NOOP_TOOL_NAME, NOOP_TOOL_KIND),
        (READ_ONLY_TOOL_NAME, READ_ONLY_TOOL_KIND),
    }:
        return _ParsedToolIntent(
            skipped_request=_skipped_intent_tool_request(identity, "unsupported_tool_intent"),
            reason="unsupported_tool_intent",
        )

    metadata_result = _safe_intent_metadata(raw_intent.get("metadata"))
    if metadata_result is None:
        return _ParsedToolIntent(
            skipped_request=_skipped_intent_tool_request(identity, "unsafe_tool_intent_metadata"),
            reason="unsafe_tool_intent_metadata",
        )

    return _ParsedToolIntent(
        intent=NativeToolIntent(
            request_id=identity.request_id,
            tool_name=str(tool_name),
            tool_kind=str(tool_kind),
            turn_index=identity.turn_index,
            intent_source=str(raw_intent.get("intent_source", "provider_metadata")),
            approval_policy=_intent_approval_policy(str(tool_name), str(tool_kind)),
            sandbox_policy=_intent_sandbox_policy(str(tool_name), str(tool_kind)),
            metadata=metadata_result,
        )
    )


def _unsafe_intent_reason(
    raw_intent: Mapping[object, object],
    identity: NativeToolRequestIdentity,
) -> str | None:
    if any(not isinstance(key, str) for key in raw_intent):
        return "unsafe_tool_intent_keys"
    if set(raw_intent) - _ALLOWED_INTENT_KEYS:
        return "unsafe_tool_intent_keys"
    if "request_id" in raw_intent:
        return "unsafe_tool_intent_request_id"
    if raw_intent.get("turn_index", identity.turn_index) != identity.turn_index:
        return "unsafe_tool_intent_turn_index"
    intent_source = raw_intent.get("intent_source", "provider_metadata")
    if intent_source not in _SUPPORTED_INTENT_SOURCES:
        return "unsafe_tool_intent_source"
    if raw_intent.get("tool_name") == READ_ONLY_TOOL_NAME and raw_intent.get("tool_kind") == READ_ONLY_TOOL_KIND:
        if raw_intent.get("approval_policy", NativeToolApprovalMode.REQUIRED.value) != NativeToolApprovalMode.REQUIRED.value:
            return "unsafe_tool_intent_policy"
        if raw_intent.get("approval_required", True) is not True:
            return "unsafe_tool_intent_policy"
        if raw_intent.get("sandbox_policy", NativeToolSandboxMode.READ_ONLY_WORKSPACE.value) != NativeToolSandboxMode.READ_ONLY_WORKSPACE.value:
            return "unsafe_tool_intent_policy"
        if raw_intent.get("workspace_read_allowed", True) is not True:
            return "unsafe_tool_intent_policy"
    else:
        if raw_intent.get("approval_policy", NativeToolApprovalPolicy().label) != NativeToolApprovalPolicy().label:
            return "unsafe_tool_intent_policy"
        if raw_intent.get("approval_required", False) is not False:
            return "unsafe_tool_intent_policy"
        if raw_intent.get("sandbox_policy", NativeToolSandboxPolicy().label) != NativeToolSandboxPolicy().label:
            return "unsafe_tool_intent_policy"
        if raw_intent.get("workspace_read_allowed", False) is not False:
            return "unsafe_tool_intent_policy"
    for key in (
        "filesystem_mutation_allowed",
        "shell_execution_allowed",
        "network_access_allowed",
        "tool_payloads_stored",
        "stdout_stored",
        "stderr_stored",
        "diffs_stored",
        "file_contents_stored",
    ):
        if raw_intent.get(key, False) is not False:
            return "unsafe_tool_intent_policy"
    return None


def _safe_intent_metadata(value: object) -> dict[str, object] | None:
    if value is None:
        return {
            "internal_noop": True,
            "tool_payloads_stored": False,
        }
    if not isinstance(value, Mapping):
        return None

    safe_metadata: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or key not in _SAFE_INTENT_METADATA_KEYS:
            return None
        if not isinstance(item, bool | int | float | str):
            return None
        sanitized_item = sanitize_text(item) if isinstance(item, str) else item
        if sanitized_item == "[REDACTED]":
            return None
        safe_metadata[key] = sanitized_item
    safe_metadata.setdefault("internal_noop", True)
    safe_metadata.setdefault("tool_payloads_stored", False)
    return safe_metadata


def _intent_approval_policy(tool_name: str, tool_kind: str) -> NativeToolApprovalPolicy:
    if tool_name == READ_ONLY_TOOL_NAME and tool_kind == READ_ONLY_TOOL_KIND:
        return NativeToolApprovalPolicy(mode=NativeToolApprovalMode.REQUIRED)
    return NativeToolApprovalPolicy()


def _intent_sandbox_policy(tool_name: str, tool_kind: str) -> NativeToolSandboxPolicy:
    if tool_name == READ_ONLY_TOOL_NAME and tool_kind == READ_ONLY_TOOL_KIND:
        return NativeToolSandboxPolicy(
            mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
            workspace_read_allowed=True,
        )
    return NativeToolSandboxPolicy()


def _is_read_only_intent(intent: NativeToolIntent) -> bool:
    return intent.tool_name == READ_ONLY_TOOL_NAME and intent.tool_kind == READ_ONLY_TOOL_KIND


def _parse_tool_observation_fixture(
    provider_result: ProviderResult,
    tool_result: NativeToolResult,
) -> _ParsedToolObservationFixture:
    metadata = provider_result.metadata or {}
    if PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY not in metadata:
        return _ParsedToolObservationFixture()

    identity = NativeToolRequestIdentity.current_noop()
    raw_fixture = metadata[PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY]
    if not isinstance(raw_fixture, Mapping):
        return _ParsedToolObservationFixture(
            skipped_observation=_skipped_observation(identity, NativeToolObservationReason.UNSAFE_OBSERVATION)
        )

    if _unsafe_observation_fixture(raw_fixture, identity):
        return _ParsedToolObservationFixture(
            skipped_observation=_skipped_observation(identity, NativeToolObservationReason.UNSAFE_OBSERVATION)
        )
    if _unsupported_observation_fixture(raw_fixture):
        return _ParsedToolObservationFixture(
            skipped_observation=_skipped_observation(identity, NativeToolObservationReason.UNSUPPORTED_OBSERVATION)
        )

    return _ParsedToolObservationFixture(
        observation=NativeToolObservation(
            tool_request_id=identity.request_id,
            turn_index=identity.turn_index,
            tool_name=NOOP_TOOL_NAME,
            tool_kind=NOOP_TOOL_KIND,
            status=NativeToolObservationStatus.SUCCEEDED,
            reason_label=NativeToolObservationReason.TOOL_RESULT_SUCCEEDED,
            duration_seconds=_safe_duration_value(raw_fixture.get("duration_seconds"), tool_result),
            tool_payloads_stored=False,
            stdout_stored=False,
            stderr_stored=False,
            diffs_stored=False,
            file_contents_stored=False,
            prompt_stored=False,
            model_output_stored=False,
            provider_responses_stored=False,
            raw_transcript_imported=False,
        )
    )


def _parse_read_only_tool_fixture(provider_result: ProviderResult) -> _ParsedReadOnlyToolFixture:
    metadata = provider_result.metadata or {}
    identity = NativeToolRequestIdentity.current_noop()
    raw_fixture = metadata.get(PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY)
    if raw_fixture is None:
        return _ParsedReadOnlyToolFixture(reason="missing_read_only_context")
    if not isinstance(raw_fixture, Mapping):
        return _ParsedReadOnlyToolFixture(reason="unsafe_read_only_context")
    if any(not isinstance(key, str) for key in raw_fixture):
        return _ParsedReadOnlyToolFixture(reason="unsafe_read_only_context")
    if set(raw_fixture) - _ALLOWED_READ_ONLY_FIXTURE_KEYS:
        return _ParsedReadOnlyToolFixture(reason="unsafe_read_only_context")
    if raw_fixture.get("fixture_source") != _SUPPORTED_READ_ONLY_FIXTURE_SOURCE:
        return _ParsedReadOnlyToolFixture(reason="unsupported_read_only_context")
    if raw_fixture.get("tool_request_id") != identity.request_id:
        return _ParsedReadOnlyToolFixture(reason="unsafe_read_only_context")
    if raw_fixture.get("turn_index") != identity.turn_index:
        return _ParsedReadOnlyToolFixture(reason="unsafe_read_only_context")
    if raw_fixture.get("request_kind") != NativeReadOnlyToolRequestKind.EXPLICIT_FILE_EXCERPT.value:
        return _ParsedReadOnlyToolFixture(reason="unsupported_read_only_context")

    try:
        approval_decision = NativeReadOnlyApprovalDecision(str(raw_fixture.get("approval_decision")))
        gate_decision = NativeReadOnlyGateDecision(
            approval_decision=approval_decision,
            decision_authority=str(raw_fixture.get("decision_authority", "pipy-owned")),
            reason_label=_read_only_fixture_optional_text(raw_fixture.get("decision_reason_label")),
        )
        target_path = raw_fixture.get("workspace_relative_path")
        if not isinstance(target_path, str):
            return _ParsedReadOnlyToolFixture(reason="unsafe_read_only_context")
        target = NativeExplicitFileExcerptTarget(
            workspace_relative_path=target_path,
            target_authority=str(raw_fixture.get("target_authority", "pipy-owned")),
        )
        request = NativeReadOnlyToolRequest(
            tool_request_id=identity.request_id,
            turn_index=identity.turn_index,
            request_kind=NativeReadOnlyToolRequestKind.EXPLICIT_FILE_EXCERPT,
            scope_label=_read_only_fixture_optional_text(raw_fixture.get("scope_label")),
        )
    except ValueError:
        return _ParsedReadOnlyToolFixture(reason="unsafe_read_only_context")

    return _ParsedReadOnlyToolFixture(
        request=request,
        gate_decision=gate_decision,
        target=target,
    )


def _read_only_fixture_optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional read-only fixture text must be a string")
    return value


def _parse_patch_proposal(provider_result: ProviderResult) -> _ParsedPatchProposal:
    metadata = provider_result.metadata or {}
    if PROVIDER_PATCH_PROPOSAL_METADATA_KEY not in metadata:
        return _ParsedPatchProposal()

    identity = NativeToolRequestIdentity.current_noop()
    raw_proposal = metadata[PROVIDER_PATCH_PROPOSAL_METADATA_KEY]
    if not isinstance(raw_proposal, Mapping):
        return _ParsedPatchProposal(
            proposal=_skipped_patch_proposal(identity, NativePatchProposalReason.UNSAFE_PROPOSAL)
        )

    unsafe_reason = _unsafe_patch_proposal_reason(raw_proposal, identity)
    if unsafe_reason is not None:
        return _ParsedPatchProposal(proposal=_skipped_patch_proposal(identity, unsafe_reason))
    unsupported_or_unsafe_reason = _unsupported_or_unsafe_patch_proposal_reason(raw_proposal)
    if unsupported_or_unsafe_reason is not None:
        return _ParsedPatchProposal(proposal=_skipped_patch_proposal(identity, unsupported_or_unsafe_reason))

    try:
        operation_labels = tuple(
            NativePatchProposalOperation(str(label))
            for label in raw_proposal.get("operation_labels", ())
        )
        proposal = NativePatchProposal(
            tool_request_id=identity.request_id,
            turn_index=identity.turn_index,
            status=NativePatchProposalStatus.PROPOSED,
            reason_label=NativePatchProposalReason.STRUCTURED_PROPOSAL_ACCEPTED,
            file_count=int(raw_proposal.get("file_count", 0)),
            operation_count=int(raw_proposal.get("operation_count", 0)),
            operation_labels=operation_labels,
        )
    except (TypeError, ValueError):
        return _ParsedPatchProposal(
            proposal=_skipped_patch_proposal(identity, NativePatchProposalReason.UNSAFE_PROPOSAL)
        )
    return _ParsedPatchProposal(proposal=proposal)


def _unsafe_patch_proposal_reason(
    raw_proposal: Mapping[object, object],
    identity: NativeToolRequestIdentity,
) -> NativePatchProposalReason | None:
    if any(not isinstance(key, str) for key in raw_proposal):
        return NativePatchProposalReason.UNSAFE_PROPOSAL
    if set(raw_proposal) - _ALLOWED_PATCH_PROPOSAL_KEYS:
        return NativePatchProposalReason.UNSAFE_PROPOSAL
    if raw_proposal.get("tool_request_id") != identity.request_id:
        return NativePatchProposalReason.UNSAFE_PROPOSAL
    if raw_proposal.get("turn_index") != identity.turn_index:
        return NativePatchProposalReason.UNSAFE_PROPOSAL
    for key in (
        "patch_text_stored",
        "diffs_stored",
        "file_contents_stored",
        "prompt_stored",
        "model_output_stored",
        "provider_responses_stored",
        "raw_transcript_imported",
        "workspace_mutated",
    ):
        if raw_proposal.get(key, False) is not False:
            return NativePatchProposalReason.UNSAFE_PROPOSAL
    for key in ("file_count", "operation_count"):
        value = raw_proposal.get(key, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return NativePatchProposalReason.UNSAFE_PROPOSAL
    operation_labels = raw_proposal.get("operation_labels", ())
    if not isinstance(operation_labels, list | tuple):
        return NativePatchProposalReason.UNSAFE_PROPOSAL
    if any(not isinstance(label, str) for label in operation_labels):
        return NativePatchProposalReason.UNSAFE_PROPOSAL
    return None


def _unsupported_or_unsafe_patch_proposal_reason(
    raw_proposal: Mapping[object, object],
) -> NativePatchProposalReason | None:
    if raw_proposal.get("proposal_source") != _SUPPORTED_PATCH_PROPOSAL_SOURCE:
        return NativePatchProposalReason.UNSUPPORTED_PROPOSAL
    status = raw_proposal.get("status")
    reason_label = raw_proposal.get("reason_label")
    if (status, reason_label) not in _SUPPORTED_PATCH_PROPOSAL_STATUSES:
        return NativePatchProposalReason.UNSUPPORTED_PROPOSAL
    raw_operation_labels = raw_proposal.get("operation_labels", ())
    if not isinstance(raw_operation_labels, list | tuple):
        return NativePatchProposalReason.UNSAFE_PROPOSAL
    try:
        operation_labels = [
            NativePatchProposalOperation(str(label))
            for label in raw_operation_labels
        ]
    except ValueError:
        return NativePatchProposalReason.UNSUPPORTED_PROPOSAL
    if len(operation_labels) > NativePatchProposal.MAX_OPERATION_LABELS:
        return NativePatchProposalReason.UNSAFE_PROPOSAL
    file_count = raw_proposal.get("file_count", 0)
    operation_count = raw_proposal.get("operation_count", 0)
    if not isinstance(file_count, int) or isinstance(file_count, bool):
        return NativePatchProposalReason.UNSAFE_PROPOSAL
    if not isinstance(operation_count, int) or isinstance(operation_count, bool):
        return NativePatchProposalReason.UNSAFE_PROPOSAL
    if file_count > NativePatchProposal.MAX_FILE_COUNT:
        return NativePatchProposalReason.UNSAFE_PROPOSAL
    if operation_count > NativePatchProposal.MAX_OPERATION_COUNT:
        return NativePatchProposalReason.UNSAFE_PROPOSAL
    return None


def _skipped_patch_proposal(
    identity: NativeToolRequestIdentity,
    reason_label: NativePatchProposalReason,
) -> NativePatchProposal:
    return NativePatchProposal(
        tool_request_id=identity.request_id,
        turn_index=identity.turn_index,
        status=NativePatchProposalStatus.SKIPPED,
        reason_label=reason_label,
        file_count=0,
        operation_count=0,
        operation_labels=(),
    )


def _unsafe_observation_fixture(
    raw_fixture: Mapping[object, object],
    identity: NativeToolRequestIdentity,
) -> bool:
    if any(not isinstance(key, str) for key in raw_fixture):
        return True
    if set(raw_fixture) - _ALLOWED_OBSERVATION_FIXTURE_KEYS:
        return True
    if raw_fixture.get("tool_request_id") != identity.request_id:
        return True
    if raw_fixture.get("turn_index") != identity.turn_index:
        return True
    for key in (
        "tool_payloads_stored",
        "stdout_stored",
        "stderr_stored",
        "diffs_stored",
        "file_contents_stored",
        "prompt_stored",
        "model_output_stored",
        "provider_responses_stored",
        "raw_transcript_imported",
    ):
        if raw_fixture.get(key, False) is not False:
            return True
    return False


def _unsupported_observation_fixture(raw_fixture: Mapping[object, object]) -> bool:
    if raw_fixture.get("fixture_source") != _SUPPORTED_OBSERVATION_FIXTURE_SOURCE:
        return True
    if raw_fixture.get("tool_name") != NOOP_TOOL_NAME or raw_fixture.get("tool_kind") != NOOP_TOOL_KIND:
        return True
    status = raw_fixture.get("status")
    reason_label = raw_fixture.get("reason_label")
    if (status, reason_label) not in _SUPPORTED_OBSERVATION_STATUSES:
        return True
    duration = raw_fixture.get("duration_seconds")
    if duration is not None and (
        not isinstance(duration, int | float) or isinstance(duration, bool) or duration < 0 or not isfinite(duration)
    ):
        return True
    return False


def _skipped_observation(
    identity: NativeToolRequestIdentity,
    reason_label: NativeToolObservationReason,
) -> NativeToolObservation:
    if reason_label == NativeToolObservationReason.UNSAFE_OBSERVATION:
        tool_name = "unsafe"
        tool_kind = "unsafe_observation"
    else:
        tool_name = "unsupported"
        tool_kind = "unsupported_observation"
    return NativeToolObservation(
        tool_request_id=identity.request_id,
        turn_index=identity.turn_index,
        tool_name=tool_name,
        tool_kind=tool_kind,
        status=NativeToolObservationStatus.SKIPPED,
        reason_label=reason_label,
        duration_seconds=0.0,
    )


def _safe_duration_value(raw_duration: object, tool_result: NativeToolResult) -> float:
    if (
        isinstance(raw_duration, int | float)
        and not isinstance(raw_duration, bool)
        and raw_duration >= 0
        and isfinite(raw_duration)
    ):
        return float(raw_duration)
    return _duration_seconds(tool_result.started_at, tool_result.ended_at)


def _tool_result_from_read_only_result(read_only_result: NativeExplicitFileExcerptResult) -> NativeToolResult:
    error_type: str | None = None
    if read_only_result.status == NativeToolStatus.SKIPPED:
        error_type = "NativeReadOnlyToolSkipped"
    elif read_only_result.status == NativeToolStatus.FAILED:
        error_type = "NativeReadOnlyToolFailed"
    return NativeToolResult(
        request_id=read_only_result.tool_request_id,
        tool_name=READ_ONLY_TOOL_NAME,
        status=read_only_result.status,
        started_at=read_only_result.started_at,
        ended_at=read_only_result.ended_at,
        metadata=read_only_result.archive_metadata(),
        error_type=None
        if read_only_result.status == NativeToolStatus.SUCCEEDED
        else error_type or "NativeReadOnlyToolError",
        error_message=None
        if read_only_result.status == NativeToolStatus.SUCCEEDED
        else read_only_result.reason_label.value,
    )


def _read_only_observation(read_only_result: NativeExplicitFileExcerptResult) -> NativeToolObservation:
    return NativeToolObservation(
        tool_request_id=read_only_result.tool_request_id,
        turn_index=read_only_result.turn_index,
        tool_name=READ_ONLY_TOOL_NAME,
        tool_kind=READ_ONLY_TOOL_KIND,
        status=NativeToolObservationStatus.SUCCEEDED,
        reason_label=NativeToolObservationReason.TOOL_RESULT_SUCCEEDED,
        duration_seconds=_duration_seconds(read_only_result.started_at, read_only_result.ended_at),
        tool_payloads_stored=False,
        stdout_stored=False,
        stderr_stored=False,
        diffs_stored=False,
        file_contents_stored=False,
        prompt_stored=False,
        model_output_stored=False,
        provider_responses_stored=False,
        raw_transcript_imported=False,
    )


def _noop_tool_request() -> NativeToolRequest:
    identity = NativeToolRequestIdentity.current_noop()
    return NativeToolRequest(
        request_id=identity.request_id,
        tool_name=NOOP_TOOL_NAME,
        tool_kind=NOOP_TOOL_KIND,
        approval_policy=NativeToolApprovalPolicy(),
        sandbox_policy=NativeToolSandboxPolicy(),
        metadata={
            "internal_noop": True,
            "tool_payloads_stored": False,
        },
    )


def _tool_request_from_intent(intent: NativeToolIntent) -> NativeToolRequest:
    return NativeToolRequest(
        request_id=intent.request_id,
        tool_name=intent.tool_name,
        tool_kind=intent.tool_kind,
        approval_policy=intent.approval_policy,
        sandbox_policy=intent.sandbox_policy,
        metadata=intent.metadata,
    )


def _skipped_intent_tool_request(
    identity: NativeToolRequestIdentity,
    reason: str,
) -> NativeToolRequest:
    unsafe = reason.startswith("unsafe")
    return NativeToolRequest(
        request_id=identity.request_id,
        tool_name=TOOL_INTENT_UNSAFE_NAME if unsafe else TOOL_INTENT_UNSUPPORTED_NAME,
        tool_kind=TOOL_INTENT_UNSAFE_KIND if unsafe else TOOL_INTENT_UNSUPPORTED_KIND,
        approval_policy=NativeToolApprovalPolicy(),
        sandbox_policy=NativeToolSandboxPolicy(),
    )


def _safe_intent_context(intent: NativeToolIntent) -> dict[str, object]:
    return {
        "tool_request_id": intent.request_id,
        "tool_name": intent.tool_name,
        "tool_kind": intent.tool_kind,
        "turn_index": intent.turn_index,
        "intent_source": intent.intent_source,
        "approval_policy": intent.approval_policy.label,
        "approval_required": intent.approval_policy.mode == NativeToolApprovalMode.REQUIRED,
        "sandbox_policy": intent.sandbox_policy.label,
        "workspace_read_allowed": intent.sandbox_policy.workspace_read_allowed,
        "filesystem_mutation_allowed": intent.sandbox_policy.filesystem_mutation_allowed,
        "shell_execution_allowed": intent.sandbox_policy.shell_execution_allowed,
        "network_access_allowed": intent.sandbox_policy.network_access_allowed,
        "tool_payloads_stored": False,
        "stdout_stored": False,
        "stderr_stored": False,
        "diffs_stored": False,
        "file_contents_stored": False,
        "intent_metadata": sanitize_metadata(intent.metadata or {}),
    }


def _safe_tool_context(tool_request: NativeToolRequest) -> dict[str, object]:
    return {
        "tool_request_id": tool_request.request_id,
        "tool_name": tool_request.tool_name,
        "tool_kind": tool_request.tool_kind,
        "approval_policy": tool_request.approval_policy.label,
        "approval_required": tool_request.approval_policy.mode == NativeToolApprovalMode.REQUIRED,
        "sandbox_policy": tool_request.sandbox_policy.label,
        "workspace_read_allowed": tool_request.sandbox_policy.workspace_read_allowed,
        "filesystem_mutation_allowed": tool_request.sandbox_policy.filesystem_mutation_allowed,
        "shell_execution_allowed": tool_request.sandbox_policy.shell_execution_allowed,
        "network_access_allowed": tool_request.sandbox_policy.network_access_allowed,
        "tool_payloads_stored": False,
        "stdout_stored": False,
        "stderr_stored": False,
        "diffs_stored": False,
        "file_contents_stored": False,
    }


def _emit_tool_intent_detected(
    event_sink: EventSink,
    safe_context: Mapping[str, object],
    intent: NativeToolIntent,
) -> None:
    event_sink.emit(
        "native.tool.intent.detected",
        summary=(
            "Native tool intent detected: "
            f"tool={sanitize_text(intent.tool_name)}, kind={sanitize_text(intent.tool_kind)}."
        ),
        payload={
            **safe_context,
            **_safe_intent_context(intent),
            "status": NativeToolStatus.PENDING.value,
        },
    )


def _emit_tool_result_event(
    event_sink: EventSink,
    safe_context: Mapping[str, object],
    tool_request: NativeToolRequest,
    tool_result: NativeToolResult,
    *,
    reason: str | None = None,
) -> None:
    event_type = _tool_event_type(tool_result.status)
    payload = {
        **safe_context,
        **_safe_tool_context(tool_request),
        "status": tool_result.status.value,
        "duration_seconds": _duration_seconds(tool_result.started_at, tool_result.ended_at),
        "tool_metadata": sanitize_metadata(tool_result.metadata or {}),
        "error_type": _safe_optional_text(tool_result.error_type),
        "error_message": _safe_optional_text(tool_result.error_message),
    }
    if reason is not None:
        payload["reason"] = sanitize_text(reason)
    event_sink.emit(
        event_type,
        summary=(
            "Native tool invocation finished: "
            f"status={tool_result.status.value}, tool={sanitize_text(tool_result.tool_name)}."
        ),
        payload=payload,
    )


def _emit_tool_observation_recorded(
    event_sink: EventSink,
    safe_context: Mapping[str, object],
    observation: NativeToolObservation,
) -> None:
    payload = {
        **safe_context,
        **_safe_observation_context(observation),
    }
    event_sink.emit(
        NATIVE_TOOL_OBSERVATION_RECORDED_EVENT,
        summary=(
            "Native tool observation recorded: "
            f"status={observation.status.value}, tool={sanitize_text(observation.tool_name)}."
        ),
        payload=payload,
    )


def _emit_patch_proposal_recorded(
    event_sink: EventSink,
    safe_context: Mapping[str, object],
    proposal: NativePatchProposal,
) -> None:
    event_sink.emit(
        NATIVE_PATCH_PROPOSAL_RECORDED_EVENT,
        summary=(
            "Native patch proposal recorded: "
            f"status={proposal.status.value}, file_count={proposal.file_count}."
        ),
        payload={
            **safe_context,
            **_safe_patch_proposal_context(proposal),
        },
    )


def _emit_patch_apply_recorded(
    event_sink: EventSink,
    safe_context: Mapping[str, object],
    result: NativePatchApplyResult,
) -> None:
    event_sink.emit(
        NATIVE_PATCH_APPLY_RECORDED_EVENT,
        summary=(
            "Native patch apply recorded: "
            f"status={result.status.value}, file_count={result.file_count}."
        ),
        payload={
            **safe_context,
            **result.archive_metadata(),
        },
    )


def _emit_verification_recorded(
    event_sink: EventSink,
    safe_context: Mapping[str, object],
    result: NativeVerificationResult,
) -> None:
    event_sink.emit(
        NATIVE_VERIFICATION_RECORDED_EVENT,
        summary=(
            "Native verification recorded: "
            f"status={result.status.value}, command={sanitize_text(result.command_label)}."
        ),
        payload={
            **safe_context,
            **result.archive_metadata(),
        },
    )


def _safe_observation_context(observation: NativeToolObservation) -> dict[str, object]:
    payload: dict[str, object] = {
        "tool_request_id": observation.tool_request_id,
        "turn_index": observation.turn_index,
        "tool_name": observation.tool_name,
        "tool_kind": observation.tool_kind,
        "status": observation.status.value,
        "reason_label": observation.reason_label.value if observation.reason_label is not None else None,
        "duration_seconds": observation.duration_seconds,
        "tool_payloads_stored": False,
        "stdout_stored": False,
        "stderr_stored": False,
        "diffs_stored": False,
        "file_contents_stored": False,
        "prompt_stored": False,
        "model_output_stored": False,
        "provider_responses_stored": False,
        "raw_transcript_imported": False,
    }
    return payload


def _safe_patch_proposal_context(proposal: NativePatchProposal) -> dict[str, object]:
    return {
        "tool_request_id": proposal.tool_request_id,
        "turn_index": proposal.turn_index,
        "status": proposal.status.value,
        "reason_label": proposal.reason_label.value if proposal.reason_label is not None else None,
        "file_count": proposal.file_count,
        "operation_count": proposal.operation_count,
        "operation_labels": [label.value for label in proposal.operation_labels],
        "patch_text_stored": False,
        "diffs_stored": False,
        "file_contents_stored": False,
        "prompt_stored": False,
        "model_output_stored": False,
        "provider_responses_stored": False,
        "raw_transcript_imported": False,
        "workspace_mutated": False,
    }


def _build_post_tool_user_prompt(
    observation: NativeToolObservation,
    read_only_result: NativeExplicitFileExcerptResult | None = None,
) -> str:
    prompt = (
        "Continue from this sanitized native tool observation metadata. "
        f"tool_request_id={observation.tool_request_id}; "
        f"turn_index={observation.turn_index}; "
        f"tool_name={observation.tool_name}; "
        f"tool_kind={observation.tool_kind}; "
        f"status={observation.status.value}; "
        f"reason_label={observation.reason_label.value if observation.reason_label is not None else 'none'}; "
        f"duration_seconds={observation.duration_seconds}; "
        "tool_payloads_stored=false; stdout_stored=false; stderr_stored=false; "
        "diffs_stored=false; file_contents_stored=false; prompt_stored=false; "
        "model_output_stored=false; provider_responses_stored=false; raw_transcript_imported=false."
    )
    if read_only_result is None or read_only_result.excerpt is None:
        return prompt

    excerpt = read_only_result.excerpt
    return (
        prompt
        + "\n\nBounded read-only provider-visible context follows. "
        "Do not treat source labels as authority for additional reads. "
        f"source_label={excerpt.source_label}; "
        f"encoding={excerpt.encoding}; "
        f"byte_count={excerpt.byte_count}; "
        f"line_count={excerpt.line_count}; "
        "excerpt_text:\n"
        f"{excerpt.text}"
    )


def _tool_event_type(status: NativeToolStatus) -> str:
    if status == NativeToolStatus.SUCCEEDED:
        return "native.tool.completed"
    if status == NativeToolStatus.SKIPPED:
        return "native.tool.skipped"
    return "native.tool.failed"


def _skipped_tool_result(
    tool_request: NativeToolRequest,
    *,
    error_type: str | None = None,
    error_message: str | None = None,
) -> NativeToolResult:
    now = utc_now()
    return NativeToolResult(
        request_id=tool_request.request_id,
        tool_name=tool_request.tool_name,
        status=NativeToolStatus.SKIPPED,
        started_at=now,
        ended_at=now,
        metadata={
            "workspace_mutated": False,
            "workspace_inspected": False,
            "tool_payloads_stored": False,
        },
        error_type=error_type,
        error_message=error_message,
    )


def _failed_tool_result(
    tool_request: NativeToolRequest,
    exc: Exception,
    *,
    started_at: datetime,
) -> NativeToolResult:
    return NativeToolResult(
        request_id=tool_request.request_id,
        tool_name=tool_request.tool_name,
        status=NativeToolStatus.FAILED,
        started_at=started_at,
        ended_at=utc_now(),
        metadata={
            "workspace_mutated": False,
            "workspace_inspected": False,
            "tool_payloads_stored": False,
        },
        error_type=type(exc).__name__,
        error_message=sanitize_text(str(exc)) or type(exc).__name__,
    )


def _failed_patch_apply_result(
    request: NativePatchApplyRequest,
    gate: NativePatchApplyGateDecision,
    exc: Exception,
) -> NativePatchApplyResult:
    _ = exc
    now = utc_now()
    return NativePatchApplyResult(
        status=NativeToolStatus.FAILED,
        reason_label=NativePatchApplyReason.WRITE_FAILED,
        tool_request_id=request.tool_request_id,
        turn_index=request.turn_index,
        started_at=now,
        ended_at=now,
        file_count=_patch_apply_file_count(request),
        operation_count=len(request.operations),
        operation_labels=tuple(operation.operation for operation in request.operations),
        approval_policy=request.approval_policy.mode,
        approval_decision=gate.approval_decision,
        sandbox_policy=request.sandbox_policy.mode,
        workspace_read_allowed=request.sandbox_policy.workspace_read_allowed,
        filesystem_mutation_allowed=request.sandbox_policy.filesystem_mutation_allowed,
        shell_execution_allowed=request.sandbox_policy.shell_execution_allowed,
        network_access_allowed=request.sandbox_policy.network_access_allowed,
        workspace_mutated=False,
        scope_label=request.scope_label,
    )


def _patch_apply_file_count(request: NativePatchApplyRequest) -> int:
    paths: set[str] = set()
    for operation in request.operations:
        paths.add(operation.workspace_relative_path)
        if operation.target_workspace_relative_path is not None:
            paths.add(operation.target_workspace_relative_path)
    return len(paths)


def _failed_verification_result(
    request: NativeVerificationRequest,
    gate: NativeVerificationGateDecision,
) -> NativeVerificationResult:
    now = utc_now()
    return NativeVerificationResult(
        status=NativeToolStatus.FAILED,
        reason_label=NativeVerificationReason.EXECUTION_FAILED,
        tool_request_id=request.tool_request_id,
        turn_index=request.turn_index,
        command_label=safe_verification_command_label(request.command_label),
        started_at=now,
        ended_at=now,
        exit_code=None,
        approval_policy=request.approval_policy.mode,
        approval_decision=gate.approval_decision,
        sandbox_policy=request.sandbox_policy.mode,
        workspace_read_allowed=request.sandbox_policy.workspace_read_allowed,
        filesystem_mutation_allowed=request.sandbox_policy.filesystem_mutation_allowed,
        shell_execution_allowed=request.sandbox_policy.shell_execution_allowed,
        network_access_allowed=request.sandbox_policy.network_access_allowed,
        scope_label=request.scope_label,
        error_label=NativeVerificationReason.EXECUTION_FAILED.value,
    )


def _merge_provider_usage(
    first: Mapping[str, int | float],
    second: Mapping[str, int | float],
) -> dict[str, int | float]:
    merged: dict[str, int | float] = dict(first)
    for key, value in second.items():
        if key in merged:
            merged[key] += value
        else:
            merged[key] = value
    return merged


def _final_outcome(
    provider_result: ProviderResult,
    tool_result: NativeToolResult | None,
    *,
    observation_failure_reason: NativeToolObservationReason | None,
    follow_up_provider_result: ProviderResult | None,
    patch_apply_result: NativePatchApplyResult | None,
    verification_result: NativeVerificationResult | None,
) -> tuple[HarnessStatus, str | None, str | None]:
    """Walk the result tree once and return ``(status, error_type, error_message)``.

    The three values share the same dispatch ladder; computing them
    together avoids re-walking the tree (and avoids three near-identical
    helper functions diverging).
    """

    if provider_result.status != HarnessStatus.SUCCEEDED:
        return (
            provider_result.status,
            _safe_optional_text(provider_result.error_type),
            _safe_optional_text(provider_result.error_message),
        )
    if tool_result is not None and tool_result.status != NativeToolStatus.SUCCEEDED:
        return (
            HarnessStatus.FAILED,
            _safe_optional_text(tool_result.error_type) or "NativeToolError",
            _safe_optional_text(tool_result.error_message),
        )
    if observation_failure_reason is not None:
        return (
            HarnessStatus.FAILED,
            "NativeToolObservationSkipped",
            observation_failure_reason.value,
        )
    if follow_up_provider_result is not None:
        if follow_up_provider_result.status != HarnessStatus.SUCCEEDED:
            return (
                follow_up_provider_result.status,
                _safe_optional_text(follow_up_provider_result.error_type),
                _safe_optional_text(follow_up_provider_result.error_message),
            )
        if patch_apply_result is not None and patch_apply_result.status != NativeToolStatus.SUCCEEDED:
            error_type = (
                "NativePatchApplySkipped"
                if patch_apply_result.status == NativeToolStatus.SKIPPED
                else "NativePatchApplyFailed"
            )
            return (HarnessStatus.FAILED, error_type, patch_apply_result.reason_label.value)
        if verification_result is not None and verification_result.status != NativeToolStatus.SUCCEEDED:
            error_type = (
                "NativeVerificationSkipped"
                if verification_result.status == NativeToolStatus.SKIPPED
                else "NativeVerificationFailed"
            )
            return (HarnessStatus.FAILED, error_type, verification_result.reason_label.value)
        return (follow_up_provider_result.status, None, None)
    return (HarnessStatus.SUCCEEDED, None, None)


def _safe_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return sanitize_text(value)


def _failed_provider_result(
    run_input: NativeRunInput,
    exc: Exception,
    *,
    started_at: datetime,
) -> ProviderResult:
    return ProviderResult(
        status=HarnessStatus.FAILED,
        provider_name=run_input.provider_name,
        model_id=run_input.model_id,
        started_at=started_at,
        ended_at=utc_now(),
        error_type=type(exc).__name__,
        error_message=type(exc).__name__,
    )


def _duration_seconds(started_at: datetime, ended_at: datetime) -> float:
    return max(0.0, (_ensure_utc(ended_at) - _ensure_utc(started_at)).total_seconds())


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

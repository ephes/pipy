"""Native pipy agent session bootstrap."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import isfinite
from typing import Mapping

from pipy_harness.adapters.base import EventSink
from pipy_harness.capture import sanitize_metadata, sanitize_text
from pipy_harness.models import HarnessStatus
from pipy_harness.native.fake import FakeNoOpNativeTool
from pipy_harness.native.models import (
    NATIVE_TOOL_OBSERVATION_PAYLOAD_KEYS,
    NATIVE_TOOL_OBSERVATION_RECORDED_EVENT,
    NativeRunInput,
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
    NativeToolStatus,
    PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY,
    PROVIDER_TOOL_INTENT_METADATA_KEY,
    ProviderRequest,
    ProviderResult,
)
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.tool import ToolPort
from pipy_harness.native.usage import normalize_provider_usage

SYSTEM_PROMPT_ID = "pipy-native-bootstrap"
SYSTEM_PROMPT_VERSION = "1"
NOOP_TOOL_NAME = "noop"
NOOP_TOOL_KIND = "internal_noop"
TOOL_INTENT_UNSUPPORTED_NAME = "unsupported"
TOOL_INTENT_UNSUPPORTED_KIND = "unsupported_intent"
TOOL_INTENT_UNSAFE_NAME = "unsafe"
TOOL_INTENT_UNSAFE_KIND = "unsafe_intent"
_SUPPORTED_INTENT_SOURCES = {"fake_provider", "provider_metadata"}
_SUPPORTED_OBSERVATION_FIXTURE_SOURCE = "synthetic_safe_noop"
_SUPPORTED_OBSERVATION_STATUSES = {
    (NativeToolObservationStatus.SUCCEEDED.value, NativeToolObservationReason.TOOL_RESULT_SUCCEEDED.value)
}
_SAFE_INTENT_METADATA_KEYS = {
    "fixture",
    "internal_noop",
    "safe_count",
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
    "tool_payloads_stored",
    "stdout_stored",
    "stderr_stored",
    "diffs_stored",
    "file_contents_stored",
    "metadata",
}
_ALLOWED_OBSERVATION_FIXTURE_KEYS = set(NATIVE_TOOL_OBSERVATION_PAYLOAD_KEYS) | {"fixture_source"}


@dataclass(frozen=True, slots=True)
class _ParsedToolIntent:
    intent: NativeToolIntent | None = None
    skipped_request: NativeToolRequest | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _ParsedToolObservationFixture:
    observation: NativeToolObservation | None = None
    skipped_observation: NativeToolObservation | None = None


@dataclass(slots=True)
class NativeAgentSession:
    """Owns one minimal native pipy turn."""

    provider: ProviderPort
    tool: ToolPort = field(default_factory=FakeNoOpNativeTool)

    def run(self, run_input: NativeRunInput, event_sink: EventSink) -> NativeRunOutput:
        started_at = _utc_now()
        safe_context = _safe_context(run_input)
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

        provider_result, provider_usage = self._call_provider_turn(
            run_input,
            event_sink,
            safe_context,
            user_prompt=run_input.goal,
            provider_turn_index=0,
            provider_turn_label="initial",
            tool_observation=None,
        )

        tool_result: NativeToolResult | None = None
        observation_failure_reason: NativeToolObservationReason | None = None
        follow_up_provider_result: ProviderResult | None = None
        follow_up_provider_usage: dict[str, int | float] = {}
        if provider_result.status == HarnessStatus.SUCCEEDED:
            parsed_intent = _parse_tool_intent(provider_result)
            if parsed_intent.intent is not None:
                _emit_tool_intent_detected(event_sink, safe_context, parsed_intent.intent)
                tool_result = self._invoke_noop_tool(event_sink, safe_context, parsed_intent.intent)
                if tool_result.status == NativeToolStatus.SUCCEEDED:
                    parsed_observation = _parse_tool_observation_fixture(provider_result, tool_result)
                    if parsed_observation.observation is not None:
                        _emit_tool_observation_recorded(
                            event_sink,
                            safe_context,
                            parsed_observation.observation,
                        )
                        follow_up_provider_result, follow_up_provider_usage = self._call_provider_turn(
                            run_input,
                            event_sink,
                            safe_context,
                            user_prompt=_build_post_tool_user_prompt(parsed_observation.observation),
                            provider_turn_index=1,
                            provider_turn_label="post_tool_observation",
                            tool_observation=parsed_observation.observation,
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
        ended_at = _utc_now()
        final_status = _final_status(
            provider_result,
            tool_result,
            observation_failure_reason=observation_failure_reason,
            follow_up_provider_result=follow_up_provider_result,
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
            error_type=_native_error_type(
                provider_result,
                tool_result,
                observation_failure_reason=observation_failure_reason,
                follow_up_provider_result=follow_up_provider_result,
            ),
            error_message=_native_error_message(
                provider_result,
                tool_result,
                observation_failure_reason=observation_failure_reason,
                follow_up_provider_result=follow_up_provider_result,
            ),
        )

    def _call_provider_turn(
        self,
        run_input: NativeRunInput,
        event_sink: EventSink,
        safe_context: Mapping[str, object],
        *,
        user_prompt: str,
        provider_turn_index: int,
        provider_turn_label: str,
        tool_observation: NativeToolObservation | None,
    ) -> tuple[ProviderResult, dict[str, int | float]]:
        provider_turn_context = {
            "provider_turn_index": provider_turn_index,
            "provider_turn_label": provider_turn_label,
        }
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
                "status": HarnessStatus.RUNNING.value,
            },
        )

        provider_started_at = _utc_now()
        try:
            provider_result = self.provider.complete(
                ProviderRequest(
                    system_prompt=_build_system_prompt(),
                    user_prompt=user_prompt,
                    provider_name=run_input.provider_name,
                    model_id=run_input.model_id,
                    cwd=run_input.cwd,
                    provider_turn_index=provider_turn_index,
                    provider_turn_label=provider_turn_label,
                    tool_observation=tool_observation,
                )
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
                "status": provider_result.status.value,
                "duration_seconds": _duration_seconds(provider_result.started_at, provider_result.ended_at),
                "usage": provider_usage,
                "provider_metadata": _safe_provider_metadata(provider_result.metadata or {}),
                "error_type": _safe_optional_text(provider_result.error_type),
                "error_message": _safe_optional_text(provider_result.error_message),
            },
        )
        return provider_result, provider_usage

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
        tool_started_at = _utc_now()
        try:
            tool_result = self.tool.invoke(tool_request)
        except Exception as exc:
            tool_result = _failed_tool_result(tool_request, exc, started_at=tool_started_at)
        _emit_tool_result_event(event_sink, safe_context, tool_request, tool_result)
        return tool_result


def _build_system_prompt() -> str:
    return (
        "You are the native pipy runtime bootstrap. Complete exactly one minimal "
        "provider turn and do not execute tools."
    )


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


def _safe_provider_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe_metadata = dict(metadata)
    if PROVIDER_TOOL_INTENT_METADATA_KEY in safe_metadata:
        safe_metadata.pop(PROVIDER_TOOL_INTENT_METADATA_KEY)
        safe_metadata["tool_intent_metadata_present"] = True
    if PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY in safe_metadata:
        safe_metadata.pop(PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY)
        safe_metadata["tool_observation_fixture_metadata_present"] = True
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

    if raw_intent.get("tool_name") != NOOP_TOOL_NAME or raw_intent.get("tool_kind") != NOOP_TOOL_KIND:
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
            tool_name=NOOP_TOOL_NAME,
            tool_kind=NOOP_TOOL_KIND,
            turn_index=identity.turn_index,
            intent_source=str(raw_intent.get("intent_source", "provider_metadata")),
            approval_policy=NativeToolApprovalPolicy(),
            sandbox_policy=NativeToolSandboxPolicy(),
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
    if raw_intent.get("approval_policy", NativeToolApprovalPolicy().label) != NativeToolApprovalPolicy().label:
        return "unsafe_tool_intent_policy"
    if raw_intent.get("approval_required", False) is not False:
        return "unsafe_tool_intent_policy"
    if raw_intent.get("sandbox_policy", NativeToolSandboxPolicy().label) != NativeToolSandboxPolicy().label:
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


def _build_post_tool_user_prompt(observation: NativeToolObservation) -> str:
    return (
        "Continue from this sanitized native tool observation only. "
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
    now = _utc_now()
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
        ended_at=_utc_now(),
        metadata={
            "workspace_mutated": False,
            "workspace_inspected": False,
            "tool_payloads_stored": False,
        },
        error_type=type(exc).__name__,
        error_message=sanitize_text(str(exc)) or type(exc).__name__,
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


def _final_status(
    provider_result: ProviderResult,
    tool_result: NativeToolResult | None,
    *,
    observation_failure_reason: NativeToolObservationReason | None,
    follow_up_provider_result: ProviderResult | None,
) -> HarnessStatus:
    if provider_result.status != HarnessStatus.SUCCEEDED:
        return provider_result.status
    if tool_result is not None and tool_result.status != NativeToolStatus.SUCCEEDED:
        return HarnessStatus.FAILED
    if observation_failure_reason is not None:
        return HarnessStatus.FAILED
    if follow_up_provider_result is not None:
        return follow_up_provider_result.status
    return HarnessStatus.SUCCEEDED


def _native_error_type(
    provider_result: ProviderResult,
    tool_result: NativeToolResult | None,
    *,
    observation_failure_reason: NativeToolObservationReason | None,
    follow_up_provider_result: ProviderResult | None,
) -> str | None:
    if provider_result.status != HarnessStatus.SUCCEEDED:
        return _safe_optional_text(provider_result.error_type)
    if tool_result is not None and tool_result.status != NativeToolStatus.SUCCEEDED:
        return _safe_optional_text(tool_result.error_type) or "NativeToolError"
    if observation_failure_reason is not None:
        return "NativeToolObservationSkipped"
    if follow_up_provider_result is not None and follow_up_provider_result.status != HarnessStatus.SUCCEEDED:
        return _safe_optional_text(follow_up_provider_result.error_type)
    return None


def _native_error_message(
    provider_result: ProviderResult,
    tool_result: NativeToolResult | None,
    *,
    observation_failure_reason: NativeToolObservationReason | None,
    follow_up_provider_result: ProviderResult | None,
) -> str | None:
    if provider_result.status != HarnessStatus.SUCCEEDED:
        return _safe_optional_text(provider_result.error_message)
    if tool_result is not None and tool_result.status != NativeToolStatus.SUCCEEDED:
        return _safe_optional_text(tool_result.error_message)
    if observation_failure_reason is not None:
        return observation_failure_reason.value
    if follow_up_provider_result is not None and follow_up_provider_result.status != HarnessStatus.SUCCEEDED:
        return _safe_optional_text(follow_up_provider_result.error_message)
    return None


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
        ended_at=_utc_now(),
        error_type=type(exc).__name__,
        error_message=sanitize_text(str(exc)) or type(exc).__name__,
    )


def _duration_seconds(started_at: datetime, ended_at: datetime) -> float:
    return max(0.0, (_ensure_utc(ended_at) - _ensure_utc(started_at)).total_seconds())


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

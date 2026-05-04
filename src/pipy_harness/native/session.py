"""Native pipy agent session bootstrap."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping

from pipy_harness.adapters.base import EventSink
from pipy_harness.capture import sanitize_text
from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import NativeRunInput, NativeRunOutput, ProviderRequest, ProviderResult
from pipy_harness.native.provider import ProviderPort

SYSTEM_PROMPT_ID = "pipy-native-bootstrap"
SYSTEM_PROMPT_VERSION = "1"


@dataclass(slots=True)
class NativeAgentSession:
    """Owns one minimal native pipy turn."""

    provider: ProviderPort

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
        event_sink.emit(
            "native.provider.started",
            summary=(
                "Native provider call started: "
                f"provider={sanitize_text(run_input.provider_name)}, model={sanitize_text(run_input.model_id)}."
            ),
            payload={
                **safe_context,
                "status": HarnessStatus.RUNNING.value,
            },
        )

        provider_started_at = _utc_now()
        try:
            provider_result = self.provider.complete(
                ProviderRequest(
                    system_prompt=_build_system_prompt(),
                    user_prompt=run_input.goal,
                    provider_name=run_input.provider_name,
                    model_id=run_input.model_id,
                    cwd=run_input.cwd,
                )
            )
        except Exception as exc:
            provider_result = _failed_provider_result(run_input, exc, started_at=provider_started_at)

        provider_event = (
            "native.provider.completed"
            if provider_result.status == HarnessStatus.SUCCEEDED
            else "native.provider.failed"
        )
        event_sink.emit(
            provider_event,
            summary=(
                "Native provider call finished: "
                f"status={provider_result.status.value}, provider={sanitize_text(provider_result.provider_name)}, "
                f"model={sanitize_text(provider_result.model_id)}."
            ),
            payload={
                **safe_context,
                "status": provider_result.status.value,
                "duration_seconds": _duration_seconds(provider_result.started_at, provider_result.ended_at),
                "usage": provider_result.usage or {},
                "provider_metadata": provider_result.metadata or {},
                "error_type": provider_result.error_type,
                "error_message": provider_result.error_message,
            },
        )

        ended_at = _utc_now()
        exit_code = 0 if provider_result.status == HarnessStatus.SUCCEEDED else 1
        event_sink.emit(
            "native.session.completed",
            summary=f"Native pipy session completed: status={provider_result.status.value}.",
            payload={
                **safe_context,
                "status": provider_result.status.value,
                "exit_code": exit_code,
                "duration_seconds": _duration_seconds(started_at, ended_at),
            },
        )
        return NativeRunOutput(
            status=provider_result.status,
            exit_code=exit_code,
            started_at=started_at,
            ended_at=ended_at,
            final_text=provider_result.final_text,
            provider_name=provider_result.provider_name,
            model_id=provider_result.model_id,
            error_type=provider_result.error_type,
            error_message=provider_result.error_message,
        )


def _build_system_prompt() -> str:
    return (
        "You are the native pipy runtime bootstrap. Complete exactly one minimal "
        "provider turn and do not execute tools."
    )


def _safe_context(run_input: NativeRunInput) -> Mapping[str, object]:
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

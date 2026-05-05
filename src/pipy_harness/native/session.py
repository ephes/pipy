"""Native pipy agent session bootstrap."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Mapping

from pipy_harness.adapters.base import EventSink
from pipy_harness.capture import sanitize_metadata, sanitize_text
from pipy_harness.models import HarnessStatus
from pipy_harness.native.fake import FakeNoOpNativeTool
from pipy_harness.native.models import (
    NativeRunInput,
    NativeRunOutput,
    NativeToolApprovalMode,
    NativeToolApprovalPolicy,
    NativeToolRequest,
    NativeToolResult,
    NativeToolSandboxPolicy,
    NativeToolStatus,
    ProviderRequest,
    ProviderResult,
)
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.tool import ToolPort

SYSTEM_PROMPT_ID = "pipy-native-bootstrap"
SYSTEM_PROMPT_VERSION = "1"
NOOP_TOOL_REQUEST_ID = "native-tool-0001"
NOOP_TOOL_NAME = "noop"
NOOP_TOOL_KIND = "internal_noop"


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
                "usage": sanitize_metadata(provider_result.usage or {}),
                "provider_metadata": sanitize_metadata(provider_result.metadata or {}),
                "error_type": _safe_optional_text(provider_result.error_type),
                "error_message": _safe_optional_text(provider_result.error_message),
            },
        )

        tool_result: NativeToolResult | None = None
        if provider_result.status == HarnessStatus.SUCCEEDED:
            tool_result = self._invoke_noop_tool(event_sink, safe_context)
        else:
            tool_request = _noop_tool_request()
            tool_result = _skipped_tool_result(tool_request)
            _emit_tool_result_event(
                event_sink,
                safe_context,
                tool_request,
                tool_result,
                reason="provider_not_succeeded",
            )

        ended_at = _utc_now()
        final_status = _final_status(provider_result, tool_result)
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
            final_text=provider_result.final_text if final_status == HarnessStatus.SUCCEEDED else None,
            provider_name=provider_result.provider_name,
            model_id=provider_result.model_id,
            error_type=_native_error_type(provider_result, tool_result),
            error_message=_native_error_message(provider_result, tool_result),
        )

    def _invoke_noop_tool(
        self,
        event_sink: EventSink,
        safe_context: Mapping[str, object],
    ) -> NativeToolResult:
        tool_request = _noop_tool_request()
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


def _noop_tool_request() -> NativeToolRequest:
    return NativeToolRequest(
        request_id=NOOP_TOOL_REQUEST_ID,
        tool_name=NOOP_TOOL_NAME,
        tool_kind=NOOP_TOOL_KIND,
        approval_policy=NativeToolApprovalPolicy(),
        sandbox_policy=NativeToolSandboxPolicy(),
        metadata={
            "internal_noop": True,
            "tool_payloads_stored": False,
        },
    )


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


def _tool_event_type(status: NativeToolStatus) -> str:
    if status == NativeToolStatus.SUCCEEDED:
        return "native.tool.completed"
    if status == NativeToolStatus.SKIPPED:
        return "native.tool.skipped"
    return "native.tool.failed"


def _skipped_tool_result(tool_request: NativeToolRequest) -> NativeToolResult:
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


def _final_status(provider_result: ProviderResult, tool_result: NativeToolResult | None) -> HarnessStatus:
    if provider_result.status != HarnessStatus.SUCCEEDED:
        return provider_result.status
    if tool_result is not None and tool_result.status == NativeToolStatus.FAILED:
        return HarnessStatus.FAILED
    return HarnessStatus.SUCCEEDED


def _native_error_type(
    provider_result: ProviderResult,
    tool_result: NativeToolResult | None,
) -> str | None:
    if provider_result.status != HarnessStatus.SUCCEEDED:
        return _safe_optional_text(provider_result.error_type)
    if tool_result is not None and tool_result.status == NativeToolStatus.FAILED:
        return _safe_optional_text(tool_result.error_type) or "NativeToolError"
    return None


def _native_error_message(
    provider_result: ProviderResult,
    tool_result: NativeToolResult | None,
) -> str | None:
    if provider_result.status != HarnessStatus.SUCCEEDED:
        return _safe_optional_text(provider_result.error_message)
    if tool_result is not None and tool_result.status == NativeToolStatus.FAILED:
        return _safe_optional_text(tool_result.error_message)
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

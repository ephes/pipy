"""Native pipy runtime adapter."""

from __future__ import annotations

import sys

from pipy_harness.adapters.base import EventSink
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import AdapterResult, PreparedRun, RunRequest
from pipy_harness.native.models import NativeRunInput
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.session import NativeAgentSession, SYSTEM_PROMPT_ID, SYSTEM_PROMPT_VERSION


class PipyNativeAdapter:
    """Run one minimal native pipy turn through an injected provider."""

    name = "pipy-native"

    def __init__(self, provider: ProviderPort) -> None:
        self.provider = provider

    def prepare(self, request: RunRequest) -> PreparedRun:
        cwd = request.cwd.expanduser().resolve()
        if not cwd.exists():
            raise ValueError(f"cwd does not exist: {cwd}")
        if not cwd.is_dir():
            raise ValueError(f"cwd is not a directory: {cwd}")
        if request.command:
            raise ValueError("pipy-native runs do not accept a command after --")
        if not request.goal:
            raise ValueError("pipy-native runs require --goal")

        return PreparedRun(
            command=(),
            cwd=cwd,
            adapter=self.name,
            command_executable=self.name,
            goal=request.goal,
            native_provider=request.native_provider or self.provider.name,
            native_model=request.native_model or self.provider.model_id,
        )

    def run(
        self,
        prepared: PreparedRun,
        *,
        event_sink: EventSink,
        capture_policy: CapturePolicy,
    ) -> AdapterResult:
        run_output = NativeAgentSession(provider=self.provider).run(
            NativeRunInput(
                goal=prepared.goal or "",
                cwd=prepared.cwd,
                provider_name=prepared.native_provider or self.provider.name,
                model_id=prepared.native_model or self.provider.model_id,
                system_prompt_id=SYSTEM_PROMPT_ID,
                system_prompt_version=SYSTEM_PROMPT_VERSION,
            ),
            event_sink,
        )
        if run_output.final_text:
            print(run_output.final_text, file=sys.stdout)

        return AdapterResult(
            status=run_output.status,
            exit_code=run_output.exit_code,
            started_at=run_output.started_at,
            ended_at=run_output.ended_at,
            metadata={
                "error_type": run_output.error_type,
                "error_message": run_output.error_message,
            },
        )

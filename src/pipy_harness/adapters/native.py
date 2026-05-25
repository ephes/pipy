"""Native pipy runtime adapter."""

from __future__ import annotations

import sys
from typing import TextIO

from pipy_harness.adapters.base import EventSink
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import AdapterResult, PreparedRun, RunRequest
from pipy_harness.native.fake import FakeNoOpNativeTool
from pipy_harness.native.models import NativeRunInput
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.repl_state import NativeModelSelection, NativeReplProviderState
from pipy_harness.native.repl_input import REPL_INPUT_RUNTIME_AUTO
from pipy_harness.native.session import (
    NATIVE_BOOTSTRAP_SYSTEM_PROMPT,
    NativeAgentSession,
    NativeNoToolReplSession,
    SYSTEM_PROMPT_ID,
    SYSTEM_PROMPT_VERSION,
)
from pipy_harness.native.tool import ToolPort
from pipy_harness.native.tool_loop_session import (
    NativeToolReplSession,
    production_tool_registry,
)
from pipy_harness.native.tools import ToolPort as ModelDrivenToolPort
from pipy_harness.native.transcripts import TranscriptSink
from pipy_harness.native.workspace_context import (
    WorkspaceInstructionLoader,
    compose_system_prompt,
    empty_workspace_instruction_loader,
    workspace_instruction_safe_metadata,
)


class PipyNativeAdapter:
    """Run one minimal native pipy turn through an injected provider."""

    name = "pipy-native"

    def __init__(
        self,
        provider: ProviderPort,
        tool: ToolPort | None = None,
        *,
        instruction_loader: WorkspaceInstructionLoader = empty_workspace_instruction_loader,
    ) -> None:
        self.provider = provider
        self.tool = tool or FakeNoOpNativeTool()
        self.instruction_loader = instruction_loader

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
            native_output=request.native_output,
        )

    def run(
        self,
        prepared: PreparedRun,
        *,
        event_sink: EventSink,
        capture_policy: CapturePolicy,
    ) -> AdapterResult:
        run_output = NativeAgentSession(
            provider=self.provider,
            tool=self.tool,
            instruction_loader=self.instruction_loader,
        ).run(
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
        if prepared.native_output != "json" and run_output.final_text:
            print(run_output.final_text, file=sys.stdout)

        return AdapterResult(
            status=run_output.status,
            exit_code=run_output.exit_code,
            started_at=run_output.started_at,
            ended_at=run_output.ended_at,
            metadata={
                "adapter": self.name,
                "provider": run_output.provider_name,
                "model_id": run_output.model_id,
                "usage": run_output.usage or {},
                "error_type": run_output.error_type,
                "error_message": run_output.error_message,
            },
        )


class PipyNativeReplAdapter:
    """Run a bounded native pipy REPL through an injected provider."""

    name = "pipy-native"

    def __init__(
        self,
        provider: ProviderPort | None = None,
        *,
        provider_state: NativeReplProviderState | None = None,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
        error_stream: TextIO | None = None,
        input_runtime: str = REPL_INPUT_RUNTIME_AUTO,
        instruction_loader: WorkspaceInstructionLoader = empty_workspace_instruction_loader,
    ) -> None:
        if provider is None and provider_state is None:
            raise ValueError("PipyNativeReplAdapter requires provider or provider_state")
        self.provider = provider
        self.provider_state = provider_state
        self.input_stream = input_stream or sys.stdin
        self.output_stream = output_stream or sys.stdout
        self.error_stream = error_stream or sys.stderr
        self.input_runtime = input_runtime
        self.instruction_loader = instruction_loader

    def prepare(self, request: RunRequest) -> PreparedRun:
        cwd = request.cwd.expanduser().resolve()
        if not cwd.exists():
            raise ValueError(f"cwd does not exist: {cwd}")
        if not cwd.is_dir():
            raise ValueError(f"cwd is not a directory: {cwd}")
        if request.command:
            raise ValueError("pipy-native repl does not accept a command after --")

        selection = self._current_selection()
        return PreparedRun(
            command=(),
            cwd=cwd,
            adapter=self.name,
            command_executable=self.name,
            goal=request.goal or "Native REPL",
            native_provider=request.native_provider or selection.provider_name,
            native_model=request.native_model or selection.model_id,
        )

    def run(
        self,
        prepared: PreparedRun,
        *,
        event_sink: EventSink,
        capture_policy: CapturePolicy,
    ) -> AdapterResult:
        selection = self._current_selection()
        run_output = NativeNoToolReplSession(
            provider=self.provider,
            provider_state=self.provider_state,
            input_runtime=self.input_runtime,
            instruction_loader=self.instruction_loader,
        ).run(
            NativeRunInput(
                goal=prepared.goal or "Native REPL",
                cwd=prepared.cwd,
                provider_name=prepared.native_provider or selection.provider_name,
                model_id=prepared.native_model or selection.model_id,
                system_prompt_id=SYSTEM_PROMPT_ID,
                system_prompt_version=SYSTEM_PROMPT_VERSION,
            ),
            event_sink,
            input_stream=self.input_stream,
            output_stream=self.output_stream,
            error_stream=self.error_stream,
        )

        return AdapterResult(
            status=run_output.status,
            exit_code=run_output.exit_code,
            started_at=run_output.started_at,
            ended_at=run_output.ended_at,
            metadata={
                "adapter": self.name,
                "provider": run_output.provider_name,
                "model_id": run_output.model_id,
                "usage": run_output.usage or {},
                "error_type": run_output.error_type,
                "error_message": run_output.error_message,
            },
        )

    def _current_selection(self) -> NativeModelSelection:
        if self.provider_state is not None:
            return self.provider_state.current_selection()
        if self.provider is None:
            raise ValueError("PipyNativeReplAdapter requires provider or provider_state")
        return NativeModelSelection(self.provider.name, self.provider.model_id)


class PipyNativeToolReplAdapter:
    """Run a bounded native pipy tool-loop REPL through an injected provider.

    Slice 5 of the Tool-Loop Parity Track wires this adapter behind
    `pipy repl --agent pipy-native --repl-mode tool-loop`. It constructs a
    `NativeToolReplSession` with the current production tool registry (which
    holds only `read` at this slice) and the configured tool budget, and
    runs the loop against an injected provider.

    The adapter does not change the metadata-first archive contracts: the
    `AdapterResult.metadata` mapping carries only safe counters and labels,
    and never raw prompts, model text, tool payloads, file contents, or
    diffs.
    """

    name = "pipy-native"

    def __init__(
        self,
        provider: ProviderPort | None = None,
        *,
        provider_state: NativeReplProviderState | None = None,
        tool_registry: dict[str, ModelDrivenToolPort] | None = None,
        tool_budget: int = NativeToolReplSession.DEFAULT_TOOL_BUDGET,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
        error_stream: TextIO | None = None,
        transcript_sink: TranscriptSink | None = None,
        instruction_loader: WorkspaceInstructionLoader = empty_workspace_instruction_loader,
    ) -> None:
        if provider is None and provider_state is None:
            raise ValueError(
                "PipyNativeToolReplAdapter requires provider or provider_state"
            )
        self.provider = provider
        self.provider_state = provider_state
        self.tool_registry = (
            dict(tool_registry) if tool_registry is not None else production_tool_registry()
        )
        self.tool_budget = tool_budget
        self.input_stream = input_stream or sys.stdin
        self.output_stream = output_stream or sys.stdout
        self.error_stream = error_stream or sys.stderr
        self.transcript_sink = transcript_sink
        self.instruction_loader = instruction_loader

    def prepare(self, request: RunRequest) -> PreparedRun:
        cwd = request.cwd.expanduser().resolve()
        if not cwd.exists():
            raise ValueError(f"cwd does not exist: {cwd}")
        if not cwd.is_dir():
            raise ValueError(f"cwd is not a directory: {cwd}")
        if request.command:
            raise ValueError(
                "pipy-native repl does not accept a command after --"
            )

        selection = self._current_selection()
        return PreparedRun(
            command=(),
            cwd=cwd,
            adapter=self.name,
            command_executable=self.name,
            goal=request.goal or "Native tool-loop REPL",
            native_provider=request.native_provider or selection.provider_name,
            native_model=request.native_model or selection.model_id,
        )

    def run(
        self,
        prepared: PreparedRun,
        *,
        event_sink: EventSink,
        capture_policy: CapturePolicy,
    ) -> AdapterResult:
        provider = self._current_provider()
        if not provider.supports_tool_calls:
            raise ValueError(
                f"provider {provider.name!r} does not advertise "
                "supports_tool_calls=True; --repl-mode tool-loop requires a "
                "tool-capable provider"
            )
        discovery = self.instruction_loader(prepared.cwd)
        composed_system_prompt = compose_system_prompt(
            NATIVE_BOOTSTRAP_SYSTEM_PROMPT, discovery
        )
        instruction_metadata = workspace_instruction_safe_metadata(discovery)
        event_sink.emit(
            "native.workspace_context.loaded",
            summary=(
                "Native workspace context resolved: "
                f"files={len(discovery.instructions)}, "
                f"total_byte_cap_reached={discovery.total_byte_cap_reached}."
            ),
            payload={
                "adapter": self.name,
                "repl_mode": "tool-loop",
                **instruction_metadata,
            },
        )
        session = NativeToolReplSession(
            provider=provider,
            tool_registry=self.tool_registry,
            tool_budget=self.tool_budget,
            transcript_sink=self.transcript_sink,
        )
        try:
            run_output = session.run(
                workspace_root=prepared.cwd,
                input_stream=self.input_stream,
                output_stream=self.output_stream,
                error_stream=self.error_stream,
                system_prompt=composed_system_prompt,
                provider_name=prepared.native_provider or provider.name,
                model_id=prepared.native_model or provider.model_id,
            )
        finally:
            if self.transcript_sink is not None:
                self.transcript_sink.close()
        return AdapterResult(
            status=run_output.status,
            exit_code=run_output.exit_code,
            started_at=run_output.started_at,
            ended_at=run_output.ended_at,
            metadata={
                "adapter": self.name,
                "provider": run_output.provider_name,
                "model_id": run_output.model_id,
                "repl_mode": "tool-loop",
                "tool_budget": self.tool_budget,
                "user_turn_count": run_output.user_turn_count,
                "tool_invocation_count": run_output.tool_invocation_count,
                "malformed_argument_count": run_output.malformed_argument_count,
                "budget_exhausted_count": run_output.budget_exhausted_count,
                "error_type": run_output.error_type,
                "error_message": run_output.error_message,
                **instruction_metadata,
            },
        )

    def _current_selection(self) -> NativeModelSelection:
        if self.provider_state is not None:
            return self.provider_state.current_selection()
        if self.provider is None:
            raise ValueError(
                "PipyNativeToolReplAdapter requires provider or provider_state"
            )
        return NativeModelSelection(self.provider.name, self.provider.model_id)

    def _current_provider(self) -> ProviderPort:
        if self.provider is not None:
            return self.provider
        if self.provider_state is None:
            raise ValueError(
                "PipyNativeToolReplAdapter requires provider or provider_state"
            )
        return self.provider_state.current_provider()

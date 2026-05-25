"""Bounded model-driven REPL session skeleton.

Slice 4 of the Tool-Loop Parity Track introduces a small `NativeToolReplSession`
class that wires the slice 2 contracts (`ToolDefinition`, `ToolRequest`,
`ToolExecutionResult`, `ToolPort`, `ToolContext`, `validate_arguments`) and the
slice 3 provider extension (`ProviderPort.supports_tool_calls`,
`ProviderToolCall`, `ProviderResult.tool_calls`) into a real turn loop.

The session deliberately ships with an empty production tool registry. Real
tools (`read`, `ls`, `grep`, `find`, `write`, `edit`) are added in later
slices; tests inject a `_FixtureTool` through the registry argument to verify
loop behavior. No CLI mode flip happens in this slice; the existing no-tool
REPL stays the default surface.

Invariants pinned by the focused tests:

- The session refuses providers that do not advertise
  `supports_tool_calls=True`.
- `--tool-budget` is bounded to `[1, 25]`; the constructor validates the
  value.
- Each user turn allows at most `tool_budget` tool invocations; subsequent
  model-emitted calls receive a deterministic "tool budget exhausted"
  observation.
- Malformed tool calls (unknown tool name, JSON decode error, schema
  violation) are returned to the model as `ToolResultMessage(is_error=True)`
  observations and increment a streak counter; three consecutive malformed
  turns end the loop with a deterministic stderr diagnostic.
- One successful invocation resets the malformed streak.
- The session does not write prompts, model text, tool payloads, file
  contents, or diffs to the archive; only safe counters and labels.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, TextIO

from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderToolCall,
)
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.transcripts import TranscriptSink
from pipy_harness.native.tools import (
    AssistantMessage,
    LoopMessage,
    ToolArgumentError,
    ToolContext,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
    ToolResultMessage,
    UserMessage,
    make_tool_request_id,
    validate_arguments,
)


def production_tool_registry() -> dict[str, ToolPort]:
    """Return the current production tool registry.

    Slices 5 through 10 add `read`, `ls`, `grep`, `find`, `write`, and
    `edit` respectively. The registry holds all six tools at slice 10.
    """

    from pipy_harness.native.tools.edit import EditTool
    from pipy_harness.native.tools.find import FindTool
    from pipy_harness.native.tools.grep import GrepTool
    from pipy_harness.native.tools.ls import LsTool
    from pipy_harness.native.tools.read import ReadTool
    from pipy_harness.native.tools.write import WriteTool

    return {
        "read": ReadTool(),
        "ls": LsTool(),
        "grep": GrepTool(),
        "find": FindTool(),
        "write": WriteTool(),
        "edit": EditTool(),
    }


@dataclass(frozen=True, slots=True)
class NativeToolReplResult:
    """Bounded result returned by `NativeToolReplSession.run`.

    The fields are deliberately small and metadata-only. No prompts, model
    text, tool payloads, file contents, or diffs cross this boundary.
    """

    status: HarnessStatus
    exit_code: int
    started_at: datetime
    ended_at: datetime
    provider_name: str
    model_id: str
    user_turn_count: int = 0
    tool_invocation_count: int = 0
    malformed_argument_count: int = 0
    consecutive_malformed_streak: int = 0
    budget_exhausted_count: int = 0
    error_type: str | None = None
    error_message: str | None = None


@dataclass
class NativeToolReplSession:
    """Bounded model-driven tool loop, slice 4 skeleton.

    `tool_registry` defaults to the empty production registry; tests pass a
    mapping populated with a `_FixtureTool` (or later real tools) to exercise
    the loop. `tool_budget` is per-user-turn and capped at
    `MAX_TOOL_BUDGET`. The session reads one user turn per `readline()`
    call from `input_stream` and stops when the stream returns an empty
    string (EOF) or the malformed-tool-call streak reaches
    `MAX_MALFORMED_STREAK`.

    `transcript_sink` is an opt-in `TranscriptSink`; when supplied (via
    `--archive-transcript`), the loop writes raw turns to the sidecar
    JSONL outside the pipy session archive. The metadata archive remains
    untouched.
    """

    provider: ProviderPort
    tool_registry: dict[str, ToolPort] = field(default_factory=production_tool_registry)
    tool_budget: int = 10
    workspace_root: Path | None = None
    transcript_sink: TranscriptSink | None = None

    DEFAULT_TOOL_BUDGET: ClassVar[int] = 10
    MAX_TOOL_BUDGET: ClassVar[int] = 25
    MAX_MALFORMED_STREAK: ClassVar[int] = 3

    def __post_init__(self) -> None:
        if not self.provider.supports_tool_calls:
            raise ValueError(
                f"provider {self.provider.name!r} does not advertise "
                "supports_tool_calls=True; --repl-mode tool-loop requires a "
                "tool-capable provider"
            )
        if isinstance(self.tool_budget, bool) or not isinstance(
            self.tool_budget, int
        ):
            raise TypeError("tool_budget must be an int")
        if self.tool_budget < 1 or self.tool_budget > self.MAX_TOOL_BUDGET:
            raise ValueError(
                "tool_budget must be in "
                f"[1, {self.MAX_TOOL_BUDGET}]; got {self.tool_budget}"
            )

    def run(
        self,
        *,
        workspace_root: Path | None = None,
        input_stream: TextIO,
        output_stream: TextIO,
        error_stream: TextIO,
        system_prompt: str = "",
        provider_name: str | None = None,
        model_id: str | None = None,
    ) -> NativeToolReplResult:
        cwd = workspace_root or self.workspace_root
        if cwd is None:
            raise ValueError("NativeToolReplSession.run requires a workspace_root")
        cwd = cwd.expanduser().resolve()
        if not cwd.is_dir():
            raise ValueError(f"workspace_root is not a directory: {cwd}")

        def _stderr_sink(text: str) -> None:
            error_stream.write(text)
            if self.transcript_sink is not None:
                self.transcript_sink.append("diff", {"text": text})

        context = ToolContext(workspace_root=cwd, stderr_sink=_stderr_sink)
        effective_provider_name = provider_name or self.provider.name
        effective_model_id = model_id or self.provider.model_id
        if self.transcript_sink is not None:
            self.transcript_sink.append(
                "session",
                {
                    "provider_name": effective_provider_name,
                    "model_id": effective_model_id,
                    "tool_budget": self.tool_budget,
                },
            )

        started_at = datetime.now(UTC)
        messages: list[LoopMessage] = []
        user_turn_count = 0
        tool_invocation_count = 0
        malformed_argument_count = 0
        consecutive_malformed_streak = 0
        budget_exhausted_count = 0

        while True:
            try:
                line = input_stream.readline()
            except (KeyboardInterrupt, EOFError):
                break
            if not line:
                break
            user_input = line.rstrip("\n")
            if not user_input.strip():
                continue
            messages.append(UserMessage(content=user_input))
            user_turn_count += 1
            if self.transcript_sink is not None:
                self.transcript_sink.append("user", {"content": user_input})

            invocations_this_turn = 0
            inner_iteration_cap = self.tool_budget + 2
            inner_iterations = 0

            while inner_iterations < inner_iteration_cap:
                inner_iterations += 1
                available_tools = tuple(
                    tool.definition for tool in self.tool_registry.values()
                )
                provider_request = ProviderRequest(
                    system_prompt=system_prompt,
                    user_prompt=user_input,
                    provider_name=effective_provider_name,
                    model_id=effective_model_id,
                    cwd=cwd,
                    messages=tuple(messages),
                    available_tools=available_tools,
                )
                provider_result = self.provider.complete(provider_request)
                tool_calls = tuple(provider_result.tool_calls)
                messages.append(
                    AssistantMessage(
                        content=provider_result.final_text or "",
                        tool_calls=tool_calls,
                    )
                )
                if self.transcript_sink is not None:
                    self.transcript_sink.append(
                        "assistant",
                        {
                            "content": provider_result.final_text or "",
                            "tool_calls": [
                                {
                                    "provider_correlation_id": call.provider_correlation_id,
                                    "tool_name": call.tool_name,
                                    "arguments_json": call.arguments_json,
                                }
                                for call in tool_calls
                            ],
                        },
                    )

                if not tool_calls:
                    if provider_result.final_text:
                        print(provider_result.final_text, file=output_stream)
                    break

                fatal = False
                for call in tool_calls:
                    if invocations_this_turn >= self.tool_budget:
                        budget_exhausted_count += 1
                        messages.append(
                            self._error_observation(
                                call=call,
                                output_text=(
                                    f"tool budget exhausted "
                                    f"(limit {self.tool_budget})"
                                ),
                            )
                        )
                        continue

                    observation = self._invoke(call=call, context=context)
                    if observation.is_error:
                        malformed_argument_count += 1
                        consecutive_malformed_streak += 1
                        messages.append(observation)
                        if consecutive_malformed_streak >= self.MAX_MALFORMED_STREAK:
                            print(
                                "pipy: tool-loop ended after "
                                f"{self.MAX_MALFORMED_STREAK} consecutive malformed "
                                "tool calls",
                                file=error_stream,
                            )
                            fatal = True
                            break
                        continue

                    invocations_this_turn += 1
                    tool_invocation_count += 1
                    consecutive_malformed_streak = 0
                    messages.append(observation)
                    if self.transcript_sink is not None:
                        self.transcript_sink.append(
                            "tool_result",
                            {
                                "tool_request_id": observation.tool_request_id,
                                "output_text": observation.output_text,
                                "is_error": observation.is_error,
                                "provider_correlation_id": observation.provider_correlation_id,
                            },
                        )

                if fatal:
                    ended_at = datetime.now(UTC)
                    return NativeToolReplResult(
                        status=HarnessStatus.FAILED,
                        exit_code=1,
                        started_at=started_at,
                        ended_at=ended_at,
                        provider_name=effective_provider_name,
                        model_id=effective_model_id,
                        user_turn_count=user_turn_count,
                        tool_invocation_count=tool_invocation_count,
                        malformed_argument_count=malformed_argument_count,
                        consecutive_malformed_streak=consecutive_malformed_streak,
                        budget_exhausted_count=budget_exhausted_count,
                        error_type="NativeToolLoopMalformedFatal",
                        error_message=(
                            f"{self.MAX_MALFORMED_STREAK} consecutive malformed "
                            "tool calls"
                        ),
                    )

        ended_at = datetime.now(UTC)
        return NativeToolReplResult(
            status=HarnessStatus.SUCCEEDED,
            exit_code=0,
            started_at=started_at,
            ended_at=ended_at,
            provider_name=effective_provider_name,
            model_id=effective_model_id,
            user_turn_count=user_turn_count,
            tool_invocation_count=tool_invocation_count,
            malformed_argument_count=malformed_argument_count,
            consecutive_malformed_streak=consecutive_malformed_streak,
            budget_exhausted_count=budget_exhausted_count,
        )

    def _invoke(
        self,
        *,
        call: ProviderToolCall,
        context: ToolContext,
    ) -> ToolResultMessage:
        tool = self.tool_registry.get(call.tool_name)
        if tool is None:
            return self._error_observation(
                call=call,
                output_text=f"unknown tool: {call.tool_name}",
            )
        try:
            raw_args = json.loads(call.arguments_json)
        except json.JSONDecodeError as exc:
            return self._error_observation(
                call=call,
                output_text=f"invalid arguments JSON: {exc.msg}",
            )
        try:
            validated = validate_arguments(
                tool_name=call.tool_name,
                schema=tool.definition.input_schema,
                arguments=raw_args,
            )
        except ToolArgumentError as exc:
            return self._error_observation(call=call, output_text=str(exc))

        request_id = make_tool_request_id()
        tool_request = ToolRequest(
            tool_request_id=request_id,
            tool_name=call.tool_name,
            arguments=validated,
            provider_correlation_id=call.provider_correlation_id,
        )
        try:
            execution_result = tool.invoke(tool_request, context)
        except ToolArgumentError as exc:
            return self._error_observation(call=call, output_text=str(exc))

        if not isinstance(execution_result, ToolExecutionResult):
            raise TypeError(
                f"tool {call.tool_name!r} returned non-ToolExecutionResult value"
            )
        return ToolResultMessage(
            tool_request_id=execution_result.tool_request_id,
            output_text=execution_result.output_text,
            is_error=execution_result.is_error,
            provider_correlation_id=execution_result.provider_correlation_id,
        )

    @staticmethod
    def _error_observation(
        *,
        call: ProviderToolCall,
        output_text: str,
    ) -> ToolResultMessage:
        return ToolResultMessage(
            tool_request_id=make_tool_request_id(),
            output_text=output_text,
            is_error=True,
            provider_correlation_id=call.provider_correlation_id,
        )


__all__ = [
    "NativeToolReplResult",
    "NativeToolReplSession",
    "production_tool_registry",
]

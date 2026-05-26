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
import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Mapping
from typing import Any, ClassVar, TextIO

from pipy_harness.models import HarnessStatus
from pipy_harness.native.chrome import (
    BottomStatusFields,
    chrome_width,
    format_bottom_status_line,
    print_bottom_status_block,
    print_input_separator,
    print_startup_chrome,
)
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderToolCall,
)
from pipy_harness.native.provider import ProviderPort, StreamChunkSink
from pipy_harness.native.repl_input import (
    REPL_INPUT_RUNTIME_AUTO,
    NativeReplInput,
    native_repl_input_for,
)
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


@dataclass(frozen=True, slots=True)
class _PricingEntry:
    """Per-million-token pricing for one (provider, model) pair.

    Pricing is illustrative and conservative. The bottom status only
    shows the running total to the nearest mil-cent, so an exact match
    against the provider's billing portal is not required.
    """

    input_per_million: float
    output_per_million: float
    reasoning_per_million: float


_PRICING_TABLE: dict[tuple[str, str], _PricingEntry] = {
    # OpenAI Codex subscription (GPT-5.x family) — approximate.
    ("openai-codex", "gpt-5"): _PricingEntry(
        input_per_million=1.25, output_per_million=10.00, reasoning_per_million=10.00
    ),
}


def _pricing_for(provider_name: str, model_id: str) -> _PricingEntry | None:
    """Return per-million-token pricing for (provider, model), or None.

    Falls back to a model-family prefix lookup so e.g. ``gpt-5.5`` reuses
    the ``gpt-5`` entry. ``None`` disables cost rendering for that
    selection; the bottom status keeps showing ``$0.000``.
    """

    direct = _PRICING_TABLE.get((provider_name, model_id))
    if direct is not None:
        return direct
    for (entry_provider, entry_model), price in _PRICING_TABLE.items():
        if entry_provider != provider_name:
            continue
        if model_id.startswith(entry_model):
            return price
    return None


class _UsageAccumulator:
    """Running counters fed from each provider turn's usage payload.

    Captures input, output, and reasoning tokens plus an approximate USD
    cost. The last-turn total-token snapshot drives the context-window
    meter so the bottom status reflects real provider numbers when the
    adapter reports them and falls back to the deterministic estimate
    otherwise.
    """

    __slots__ = (
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "last_total_tokens",
        "cost_usd",
        "_pricing",
        "_provider_name",
        "_model_id",
    )

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.reasoning_tokens = 0
        self.last_total_tokens = 0
        self.cost_usd = 0.0
        self._pricing: _PricingEntry | None = None
        self._provider_name = ""
        self._model_id = ""

    def bind(self, provider_name: str, model_id: str) -> None:
        self._provider_name = provider_name
        self._model_id = model_id
        self._pricing = _pricing_for(provider_name, model_id)

    def absorb(self, usage: Mapping[str, Any] | None) -> None:
        if not usage:
            return
        input_tokens = _coerce_int(usage.get("input_tokens"))
        output_tokens = _coerce_int(usage.get("output_tokens"))
        reasoning_tokens = _coerce_int(usage.get("reasoning_tokens"))
        total_tokens = _coerce_int(usage.get("total_tokens"))
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.reasoning_tokens += reasoning_tokens
        if total_tokens > 0:
            self.last_total_tokens = total_tokens
        else:
            self.last_total_tokens = (
                input_tokens + output_tokens + reasoning_tokens
            )
        if self._pricing is not None:
            self.cost_usd += (
                input_tokens * self._pricing.input_per_million
                + output_tokens * self._pricing.output_per_million
                + reasoning_tokens * self._pricing.reasoning_per_million
            ) / 1_000_000.0


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


@dataclass(frozen=True, slots=True)
class _ContextBudget:
    """Approximate provider/model context-window budget for the meter.

    ``token_budget`` is the absolute denominator; ``budget_label`` is the
    short label rendered into the bottom status (e.g. ``272k`` for the
    272 000-token GPT-5.5 context).
    """

    token_budget: int
    budget_label: str


_CODEX_GPT_5_5_BUDGET = _ContextBudget(token_budget=272_000, budget_label="272k")
_DEFAULT_CONTEXT_BUDGET = _ContextBudget(token_budget=128_000, budget_label="128k")


def _context_budget_for(provider_name: str, model_id: str) -> _ContextBudget:
    """Return the rough context-window budget label for the bottom status.

    The mapping deliberately covers the providers/models that pipy is
    tested against today. Unknown selections fall back to the safe
    128k default so the meter still renders. Switching to authoritative
    provider usage telemetry is a separate follow-up.
    """

    if provider_name == "openai-codex":
        if model_id.startswith("gpt-5"):
            return _CODEX_GPT_5_5_BUDGET
    if provider_name in {"anthropic"} and "sonnet" in model_id.lower():
        return _ContextBudget(token_budget=200_000, budget_label="200k")
    return _DEFAULT_CONTEXT_BUDGET


def _effort_label_for(provider_name: str, model_id: str) -> str:
    """Return the reasoning-effort label the bottom status surfaces.

    Pi shows ``high`` for the codex GPT-5.x family because those models
    default to high reasoning effort. Other providers / unknown
    configurations keep the safe ``default`` label.
    """

    if provider_name == "openai-codex" and model_id.startswith("gpt-5"):
        return "high"
    return "default"


def _friendly_cwd_label(cwd: Path) -> str:
    """Render ``cwd`` as ``~/<rel> (branch)`` when inside the user's home.

    Falls back to the absolute path when ``cwd`` is outside ``~`` or
    when the home directory cannot be resolved. The ``(branch)`` suffix
    is appended when ``cwd`` (or any parent up to the home directory)
    contains a ``.git`` directory whose ``HEAD`` can be read.
    """

    label = str(cwd)
    try:
        home = Path.home()
    except RuntimeError:
        home = None
    if home is not None:
        try:
            relative = cwd.resolve().relative_to(home.resolve())
            relative_str = relative.as_posix()
            label = "~" if relative_str in {"", "."} else f"~/{relative_str}"
        except ValueError:
            pass
    branch = _detect_git_branch(cwd)
    if branch:
        label = f"{label} ({branch})"
    return label


def _detect_git_branch(cwd: Path) -> str | None:
    """Walk up from ``cwd`` looking for ``.git/HEAD`` and return the branch."""

    candidate: Path | None = cwd
    while candidate is not None and candidate != candidate.parent:
        head = candidate / ".git" / "HEAD"
        try:
            text = head.read_text(encoding="utf-8")
        except OSError:
            candidate = candidate.parent
            continue
        text = text.strip()
        if text.startswith("ref: refs/heads/"):
            return text.split("refs/heads/", 1)[1]
        if text:
            return text[:7]
        return None
    return None


def production_tool_registry() -> dict[str, ToolPort]:
    """Return the current production tool registry.

    `bash` is intentionally not registered here. A shell tool needs a real
    process/filesystem sandbox before it can satisfy pipy's default-deny
    `.git` and secret-isolation invariants in the model-visible loop.
    """

    from pipy_harness.native.tools.edit import EditTool
    from pipy_harness.native.tools.edit_diff import EditDiffTool
    from pipy_harness.native.tools.find import FindTool
    from pipy_harness.native.tools.grep import GrepTool
    from pipy_harness.native.tools.ls import LsTool
    from pipy_harness.native.tools.read import ReadTool
    from pipy_harness.native.tools.truncate import TruncateTool
    from pipy_harness.native.tools.write import WriteTool

    return {
        "read": ReadTool(),
        "ls": LsTool(),
        "grep": GrepTool(),
        "find": FindTool(),
        "write": WriteTool(),
        "edit": EditTool(),
        "edit_diff": EditDiffTool(),
        "truncate": TruncateTool(),
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
    tool_budget: int = 50
    workspace_root: Path | None = None
    transcript_sink: TranscriptSink | None = None
    input_runtime: str = REPL_INPUT_RUNTIME_AUTO
    reference_roots: tuple[Path, ...] = field(default_factory=tuple)

    DEFAULT_TOOL_BUDGET: ClassVar[int] = 50
    MAX_TOOL_BUDGET: ClassVar[int] = 200
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

        context = ToolContext(
            workspace_root=cwd,
            stderr_sink=_stderr_sink,
            reference_roots=self.reference_roots,
        )
        renderer = _ToolLoopRenderer(
            output_stream=output_stream, error_stream=error_stream
        )
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
        usage_accumulator = _UsageAccumulator()
        usage_accumulator.bind(effective_provider_name, effective_model_id)

        repl_input = self._build_repl_input(
            input_stream=input_stream,
            error_stream=error_stream,
            workspace=cwd,
        )
        print_startup_chrome(error_stream, cwd=cwd)
        # Pi-parity: the slash-menu input adapter draws the bottom status
        # block (cwd + status line) live below the input area, so we only
        # emit a pre-loop frame for non-slash-menu runtimes. This avoids a
        # duplicate cwd/status row above the prompt area in TTY sessions,
        # while keeping the captured-stream/plain case visible on immediate
        # EOF. `_print_footer` re-emits it after each submission.
        if repl_input.runtime_label != "slash-menu":
            self._print_footer(
                error_stream,
                cwd=cwd,
                provider_name=effective_provider_name,
                model_id=effective_model_id,
                user_turn_count=user_turn_count,
                tool_invocation_count=tool_invocation_count,
                usage_accumulator=usage_accumulator,
            )

        while True:
            print_input_separator(error_stream)
            footer_text = self._footer_text(
                cwd=cwd,
                provider_name=effective_provider_name,
                model_id=effective_model_id,
                user_turn_count=user_turn_count,
                tool_invocation_count=tool_invocation_count,
                error_stream=error_stream,
                usage_accumulator=usage_accumulator,
            )
            try:
                # Pi's input cursor has no leading `> ` glyph; the
                # separator pair above and below the input area is the
                # visual frame instead. Pass an empty prompt so the
                # readline / slash-menu adapter renders just the cursor.
                line = repl_input.read_line("", footer=footer_text)
            except KeyboardInterrupt:
                print(file=error_stream)
                break
            if not line:
                break
            user_input = line.rstrip("\n")
            stripped = user_input.strip()
            # Pi paints the submitted user message back on a muted
            # `userMessageBg` panel — distinct from the green tool
            # panel — so the prompt reads as a chat bubble. Overwrite
            # the readline echo line with the styled panel row when
            # the renderer can drive ANSI cursor controls.
            if stripped:
                renderer.render_user_message(user_input)
            if not stripped:
                if repl_input.runtime_label != "slash-menu":
                    self._print_footer(
                        error_stream,
                        cwd=cwd,
                        provider_name=effective_provider_name,
                        model_id=effective_model_id,
                        user_turn_count=user_turn_count,
                        tool_invocation_count=tool_invocation_count,
                    )
                continue
            if stripped in {"/exit", "/quit"}:
                break
            if stripped == "/help":
                self._print_help(error_stream)
                if repl_input.runtime_label != "slash-menu":
                    self._print_footer(
                        error_stream,
                        cwd=cwd,
                        provider_name=effective_provider_name,
                        model_id=effective_model_id,
                        user_turn_count=user_turn_count,
                        tool_invocation_count=tool_invocation_count,
                    )
                continue
            if stripped.startswith("/"):
                print(
                    f"pipy: {stripped!r} is not handled in tool-loop mode; "
                    "supported local commands are /help, /exit, /quit. "
                    "Other prompts are sent to the model.",
                    file=error_stream,
                )
                if repl_input.runtime_label != "slash-menu":
                    self._print_footer(
                        error_stream,
                        cwd=cwd,
                        provider_name=effective_provider_name,
                        model_id=effective_model_id,
                        user_turn_count=user_turn_count,
                        tool_invocation_count=tool_invocation_count,
                    )
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
                renderer.begin_provider_turn()
                renderer.show_working()
                provider_result = self.provider.complete(
                    provider_request,
                    stream_sink=renderer.stream_sink,
                    reasoning_sink=renderer.reasoning_sink,
                )
                usage_accumulator.absorb(provider_result.usage)
                renderer.end_provider_turn(
                    final_text=provider_result.final_text or "",
                    has_tool_calls=bool(provider_result.tool_calls),
                )
                if provider_result.status != HarnessStatus.SUCCEEDED:
                    error_type = provider_result.error_type or "ProviderFailed"
                    error_message = (
                        provider_result.error_message
                        or f"provider {effective_provider_name!r} returned status "
                        f"{provider_result.status.value!r} without a final response"
                    )
                    # Surface the failure on the error stream but keep the
                    # REPL alive: a transient HTTP error from a single
                    # provider turn (e.g. a 503 the retry helper exhausted
                    # against, or a brief network hiccup) should not tear
                    # the whole session down. The user can ask again at
                    # the next prompt.
                    print(
                        f"pipy: provider failure during turn: "
                        f"{error_type}: {error_message}",
                        file=error_stream,
                    )
                    if repl_input.runtime_label != "slash-menu":
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                            usage_accumulator=usage_accumulator,
                        )
                    break
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
                    if provider_result.final_text and not renderer.streamed_any:
                        print(provider_result.final_text, file=output_stream)
                    if repl_input.runtime_label != "slash-menu":
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                            usage_accumulator=usage_accumulator,
                        )
                    break

                fatal = False
                for call in tool_calls:
                    if invocations_this_turn >= self.tool_budget:
                        budget_exhausted_count += 1
                        renderer.render_tool_call(call)
                        renderer.render_tool_result(
                            output_text=(
                                f"tool budget exhausted "
                                f"(limit {self.tool_budget})"
                            ),
                            is_error=True,
                        )
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

                    renderer.render_tool_call(call)
                    tool_started_at = datetime.now(UTC)
                    observation = self._invoke(call=call, context=context)
                    tool_ended_at = datetime.now(UTC)
                    tool_duration = (
                        tool_ended_at - tool_started_at
                    ).total_seconds()
                    renderer.render_tool_result(
                        output_text=observation.output_text,
                        is_error=observation.is_error,
                        duration_seconds=tool_duration,
                    )
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
                    try:
                        repl_input.close()
                    except Exception:
                        pass
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

        try:
            repl_input.close()
        except Exception:
            pass
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

    def _build_repl_input(
        self,
        *,
        input_stream: TextIO,
        error_stream: TextIO,
        workspace: Path,
    ) -> NativeReplInput:
        return native_repl_input_for(
            input_stream=input_stream,
            error_stream=error_stream,
            input_runtime=self.input_runtime,
            workspace=workspace,
        )

    def _footer_text(
        self,
        *,
        cwd: Path,
        provider_name: str,
        model_id: str,
        user_turn_count: int,
        tool_invocation_count: int,
        error_stream: TextIO | None = None,
        usage_accumulator: "_UsageAccumulator | None" = None,
    ) -> str:
        plan_label = "sub" if provider_name == "openai-codex" else "api"
        budget = _context_budget_for(provider_name, model_id)
        used_pct = 0.0
        if budget.token_budget > 0:
            if usage_accumulator is not None and usage_accumulator.last_total_tokens > 0:
                used_pct = (
                    100.0
                    * usage_accumulator.last_total_tokens
                    / float(budget.token_budget)
                )
            else:
                estimated_tokens = self._estimated_context_tokens(
                    tool_invocation_count=tool_invocation_count,
                    user_turn_count=user_turn_count,
                )
                used_pct = 100.0 * estimated_tokens / float(budget.token_budget)
            used_pct = min(used_pct, 999.9)
        cost_label = (
            f"${usage_accumulator.cost_usd:.3f}"
            if usage_accumulator is not None
            else "$0.000"
        )
        fields = BottomStatusFields(
            cwd_label="",
            cost_label=cost_label,
            plan_label=plan_label,
            context_used_pct=used_pct,
            context_budget_label=budget.budget_label,
            context_budget_suffix="auto",
            provider_name=provider_name,
            model_id=model_id,
            effort_label=_effort_label_for(provider_name, model_id),
            tokens_in=(
                usage_accumulator.input_tokens if usage_accumulator else 0
            ),
            tokens_out=(
                usage_accumulator.output_tokens if usage_accumulator else 0
            ),
            tokens_reasoning=(
                usage_accumulator.reasoning_tokens if usage_accumulator else 0
            ),
        )
        status_line = format_bottom_status_line(chrome_width(error_stream), fields)
        cwd_label = _friendly_cwd_label(cwd)
        return f"{cwd_label}\n{status_line}"

    def _estimated_context_tokens(
        self, *, tool_invocation_count: int, user_turn_count: int
    ) -> float:
        """Cheap upper-bound estimate for the prompt's context-window draw.

        We do not parse provider usage telemetry yet; until that lands the
        bottom-status meter shows a deterministic rough estimate that
        grows with tool invocations and user turns. This matches Pi's
        ``used%/budget`` shape without inventing fake exact counts.
        """

        per_turn_tokens = 2_000.0
        per_tool_tokens = 1_500.0
        return (
            user_turn_count * per_turn_tokens
            + tool_invocation_count * per_tool_tokens
        )

    def _print_footer(
        self,
        error_stream: TextIO,
        *,
        cwd: Path,
        provider_name: str,
        model_id: str,
        user_turn_count: int,
        tool_invocation_count: int,
        usage_accumulator: "_UsageAccumulator | None" = None,
    ) -> None:
        print_input_separator(error_stream)
        footer = self._footer_text(
            cwd=cwd,
            provider_name=provider_name,
            model_id=model_id,
            user_turn_count=user_turn_count,
            tool_invocation_count=tool_invocation_count,
            error_stream=error_stream,
            usage_accumulator=usage_accumulator,
        )
        cwd_label, _, status_line = footer.partition("\n")
        print_bottom_status_block(
            error_stream, cwd_label=cwd_label, status_line=status_line
        )

    def _print_help(self, error_stream: TextIO) -> None:
        print(
            "pipy: tool-loop mode supports `/help`, `/exit`, `/quit` locally. "
            "Other input is sent to the model. The model can call bounded "
            "`read`, `ls`, `grep`, `find`, `write`, `edit`, `edit_diff`, and "
            "`truncate` tools (budget per user turn).",
            file=error_stream,
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


class _ToolLoopRenderer:
    """Pi-parity live rendering for the bounded tool loop.

    Streams provider text deltas to ``error_stream`` as they arrive, then
    paints a styled header/body block around each tool invocation. Falls
    back to plain text on non-TTY streams or when ``NO_COLOR`` is set,
    so captured logs stay deterministic and tests can pin behavior.

    Style intent:
    - Streamed assistant text: dim cyan italic prefix `assistant >`, then
      raw deltas printed verbatim (the provider already shapes the text).
    - Tool call header: italic green prefix `→ <tool>(<arg-preview>)`.
    - Tool result body: dim/quiet block prefixed with `↳`, indented two
      spaces per line, with a leading `[error]` tag on failures.

    The renderer exposes ``streamed_any`` so the loop can avoid double-
    printing the final buffered text when streaming already covered it.
    """

    _ANSI_BOLD = "\x1b[1m"
    _ANSI_DIM = "\x1b[2m"
    _ANSI_ITALIC = "\x1b[3m"
    _ANSI_GREEN = "\x1b[32m"
    _ANSI_RED = "\x1b[31m"
    _ANSI_CYAN = "\x1b[36m"
    _ANSI_YELLOW = "\x1b[33m"
    _ANSI_RESET = "\x1b[0m"
    # Pi's `toolPendingBg` theme uses a *very* muted dark-olive panel
    # behind each tool block — almost a gray with a hint of green, not
    # a saturated forest green. We pin the same intent with a truecolor
    # RGB triplet (`\x1b[48;2;28;42;30m`) on terminals that advertise
    # 24-bit color, falling back to 256-color index 235 (a near-black
    # gray) when truecolor is unavailable. `\x1b[K` fills the rest of
    # the row with the same background so each panel row reads as a
    # contiguous strip.
    _ANSI_BG_TOOL_PANEL_TRUECOLOR = "\x1b[48;2;28;42;30m"
    _ANSI_BG_TOOL_PANEL_256 = "\x1b[48;5;235m"
    # Pi's `userMessageBg` theme paints a muted slate-gray panel
    # spanning the full row behind the user's typed message so it
    # reads as a chat bubble distinct from the green tool panel. The
    # bubble is three rows tall: one blank padding row above the text,
    # the text row itself, and one blank padding row below — mirror
    # pi by emitting all three with the same background and
    # `\x1b[K` clear-to-EOL.
    _ANSI_BG_USER_MESSAGE_TRUECOLOR = "\x1b[48;2;52;53;65m"
    _ANSI_BG_USER_MESSAGE_256 = "\x1b[48;5;237m"
    _ANSI_CLEAR_EOL = "\x1b[K"
    _ANSI_CURSOR_UP_ONE = "\x1b[1A"
    _ANSI_CLEAR_LINE = "\x1b[2K"

    _RESULT_LINE_PREVIEW_MAX_LENGTH = 12
    _ARGUMENT_VALUE_PREVIEW_LIMIT = 80

    def __init__(
        self,
        *,
        output_stream: TextIO,
        error_stream: TextIO,
    ) -> None:
        self._output_stream = output_stream
        self._error_stream = error_stream
        self._enabled = self._compute_enabled(error_stream)
        self._tool_panel_bg = (
            self._ANSI_BG_TOOL_PANEL_TRUECOLOR
            if self._supports_truecolor()
            else self._ANSI_BG_TOOL_PANEL_256
        )
        self._user_message_bg = (
            self._ANSI_BG_USER_MESSAGE_TRUECOLOR
            if self._supports_truecolor()
            else self._ANSI_BG_USER_MESSAGE_256
        )
        self._stream_active = False
        self._stream_emitted_any = False
        self._streamed_any = False
        self._working_shown = False
        self._stop_working_event: threading.Event | None = None
        self._working_thread: threading.Thread | None = None
        self._reasoning_active = False
        self._reasoning_emitted_any = False

    @staticmethod
    def _compute_enabled(stream: TextIO) -> bool:
        if "NO_COLOR" in os.environ:
            return False
        term = os.environ.get("TERM", "").lower()
        if term == "dumb":
            return False
        return bool(getattr(stream, "isatty", lambda: False)())

    @staticmethod
    def _supports_truecolor() -> bool:
        """Return True when the active terminal advertises 24-bit color.

        Truecolor lets us pin Pi's exact muted-olive panel RGB. Falls
        back to a 256-color near-black on TERM strings that only carry
        eight or sixteen color slots. We treat `xterm-256color` and any
        explicit `COLORTERM=truecolor`/`24bit` as truecolor-capable.
        """

        colorterm = os.environ.get("COLORTERM", "").lower()
        if colorterm in {"truecolor", "24bit"}:
            return True
        term = os.environ.get("TERM", "").lower()
        if "256color" in term or "direct" in term:
            return True
        return False

    @property
    def streamed_any(self) -> bool:
        return self._streamed_any

    @property
    def stream_sink(self) -> StreamChunkSink:
        return self._handle_stream_chunk

    def begin_provider_turn(self) -> None:
        self._close_reasoning()
        self._stream_active = False
        self._stream_emitted_any = False
        self._working_shown = False
        self._reasoning_emitted_any = False

    @property
    def reasoning_sink(self) -> StreamChunkSink:
        return self.handle_reasoning_chunk

    _SPINNER_FRAMES: ClassVar[tuple[str, ...]] = (
        "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏",
    )
    _SPINNER_INTERVAL_SECONDS: ClassVar[float] = 0.08

    def show_working(self) -> None:
        """Animate a Pi-shape `⠋ Working...` line on the error stream.

        A background thread cycles through ``_SPINNER_FRAMES`` every
        80 ms and rewrites the line in place (`\\r\\x1b[K`). The thread
        is daemonized so it never blocks process exit, and stopped via
        ``_stop_working_event`` before the next visible block (stream
        text, tool block, or footer redraw) lands. On non-TTY streams
        the line and animation are suppressed entirely so captured
        logs stay deterministic.
        """

        if not self._enabled:
            self._working_shown = False
            return
        self._stop_working_event = threading.Event()
        self._working_shown = True

        def _animate(stop_event: threading.Event) -> None:
            frame_index = 0
            while not stop_event.is_set():
                glyph = self._SPINNER_FRAMES[
                    frame_index % len(self._SPINNER_FRAMES)
                ]
                marker = self._style(
                    f"{glyph} Working...",
                    self._ANSI_DIM,
                )
                try:
                    self._error_stream.write(f"\r\x1b[K {marker}")
                    self._error_stream.flush()
                except (ValueError, OSError):
                    return
                frame_index += 1
                stop_event.wait(self._SPINNER_INTERVAL_SECONDS)

        thread = threading.Thread(
            target=_animate,
            args=(self._stop_working_event,),
            name="pipy-tool-loop-spinner",
            daemon=True,
        )
        self._working_thread = thread
        thread.start()

    def _clear_working(self) -> None:
        if not self._working_shown:
            return
        if self._stop_working_event is not None:
            self._stop_working_event.set()
        if self._working_thread is not None:
            self._working_thread.join(timeout=0.2)
        self._stop_working_event = None
        self._working_thread = None
        if self._enabled:
            try:
                self._error_stream.write("\r\x1b[K")
                self._error_stream.flush()
            except (ValueError, OSError):
                pass
        self._working_shown = False

    def end_provider_turn(
        self, *, final_text: str, has_tool_calls: bool
    ) -> None:
        del has_tool_calls
        if self._stream_active:
            # Flush a trailing newline so the next render block starts
            # on its own line, even when the provider did not emit one,
            # then a second one so a blank row sits between the last
            # response line and the next input-frame separator, matching
            # pi's spacing below the assistant message.
            if not self._stream_emitted_any or not final_text.endswith("\n"):
                self._output_stream.write("\n\n")
            else:
                self._output_stream.write("\n")
            self._output_stream.flush()
        self._stream_active = False

    def _handle_stream_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        self._clear_working()
        if not self._stream_active:
            self._stream_active = True
            # Pi prints the final assistant answer with a one-space
            # left indent and a single blank row above. The bottom
            # padding row of the user-message bubble already provides
            # one of the two visual rows between the bubble text and
            # the answer; emit one more `\n` plus the leading indent
            # here. Subsequent lines within the same stream get their
            # indent from the newline rewrite below.
            self._output_stream.write("\n ")
        self._output_stream.write(chunk.replace("\n", "\n "))
        self._output_stream.flush()
        self._stream_emitted_any = True
        self._streamed_any = True

    def handle_reasoning_chunk(self, chunk: str) -> None:
        """Render an italic dim reasoning-summary delta inline.

        Pi paints the model's reasoning summary between tool calls with
        an italicized prose voice and renders section titles in bold.
        Pipy mirrors that by routing the codex
        `response.reasoning_summary_text.delta` events through this
        method so the user sees the same "thinking" cues. ``**...**``
        spans inside the chunk are rendered as ANSI bold+italic so
        section titles like `**Investigating pi-mono and pipy**`
        appear as bold prose instead of literal asterisks.
        """

        if not chunk:
            return
        self._clear_working()
        if not self._reasoning_active:
            self._reasoning_active = True
            indent = self._style(" ", self._ANSI_DIM)
            self._error_stream.write("\n" + indent)
        for segment, is_bold in self._split_reasoning_segments(chunk):
            if not segment:
                continue
            if is_bold:
                styled = self._style(
                    segment, self._ANSI_BOLD + self._ANSI_ITALIC + self._ANSI_DIM
                )
            else:
                styled = self._style(
                    segment, self._ANSI_ITALIC + self._ANSI_DIM
                )
            self._error_stream.write(styled)
        self._error_stream.flush()
        self._reasoning_emitted_any = True

    @staticmethod
    def _split_reasoning_segments(text: str) -> list[tuple[str, bool]]:
        """Split a reasoning chunk into (segment, is_bold) pairs.

        ``**…**`` spans become bold segments; the literal asterisks are
        removed from the rendered output. Unmatched trailing ``**`` is
        emitted verbatim so partial deltas across chunk boundaries do
        not silently drop the open marker.
        """

        segments: list[tuple[str, bool]] = []
        cursor = 0
        while True:
            open_index = text.find("**", cursor)
            if open_index == -1:
                segments.append((text[cursor:], False))
                break
            if open_index > cursor:
                segments.append((text[cursor:open_index], False))
            close_index = text.find("**", open_index + 2)
            if close_index == -1:
                segments.append((text[open_index + 2 :], True))
                break
            segments.append((text[open_index + 2 : close_index], True))
            cursor = close_index + 2
        return segments

    def _close_reasoning(self) -> None:
        if not self._reasoning_active:
            return
        self._error_stream.write("\n")
        self._error_stream.flush()
        self._reasoning_active = False

    def render_user_message(self, text: str) -> None:
        """Paint the submitted user message on the user-message panel.

        Pi's user-message bubble is three rows tall and fills the row
        width: a blank padding row above the text, the text row, and a
        blank padding row below — all painted on the same
        ``userMessageBg`` background. The readline / slash-menu adapter
        has already echoed the typed text to the error stream; we
        overwrite that previous line plus the `print_input_separator`
        row above with `\\x1b[1A\\x1b[2K\\r` and re-render the bubble in
        place. Non-TTY streams skip the rewrite and just leave the
        readline echo in place.
        """

        if not text:
            return
        lines = text.splitlines() or [""]
        if self._enabled:
            # Step back over the readline echo plus the separator row
            # that `print_input_separator` drew above the input area.
            # The readline echo of a single logical line can wrap to
            # multiple visual rows on narrow panes (`ceil(len /
            # width)`), so count visual rows — not logical lines —
            # before clearing, otherwise stale echo fragments stay
            # above the rendered bubble.
            width = max(1, chrome_width(self._error_stream))
            visual_rows = 0
            for line in lines:
                # `len(line) + 1` accounts for the leading prompt-area
                # column pi-parity already reserves; `// width` plus
                # the always-present row itself gives the wrapped
                # count, with empty lines counting as one row.
                effective = max(1, len(line) + 1)
                visual_rows += (effective + width - 1) // width
            self._error_stream.write("\r")
            for _ in range(visual_rows + 1):
                self._error_stream.write(self._ANSI_CURSOR_UP_ONE + self._ANSI_CLEAR_LINE)
            self._error_stream.write("\r")
            # Top padding row of the bubble (full-width bg).
            self._error_stream.write(self._user_message_panel_blank_line())
        for line in lines:
            self._error_stream.write(self._user_message_panel_line(line))
        if self._enabled:
            # Bottom padding row of the bubble (full-width bg).
            self._error_stream.write(self._user_message_panel_blank_line())
        self._error_stream.flush()

    def _user_message_panel_line(self, text: str) -> str:
        """Render the text row of the user-message bubble."""

        if not self._enabled:
            return f" {text}\n"
        # Full-width bg behind the text row. We pad with spaces out to
        # the rendered chrome width instead of relying solely on
        # `\x1b[K` because `tmux capture-pane -e` drops cells that
        # carry attributes but no character — without explicit space
        # characters the bg disappears in screenshots and replay.
        width = chrome_width(self._error_stream)
        padding = " " * max(0, width - len(text) - 1)
        return (
            f"{self._user_message_bg} {text}{padding}{self._ANSI_CLEAR_EOL}"
            f"{self._ANSI_RESET}\n"
        )

    def _user_message_panel_blank_line(self) -> str:
        """Render an empty padding row in the user-message bubble.

        Filled with spaces (not just `\\x1b[K`) so tmux/screenshot
        replays still see the bg on every cell of the row — empty bg
        cells get dropped by `tmux capture-pane`.
        """

        width = chrome_width(self._error_stream)
        padding = " " * width
        return (
            f"{self._user_message_bg}{padding}{self._ANSI_CLEAR_EOL}"
            f"{self._ANSI_RESET}\n"
        )

    def render_tool_call(self, call: ProviderToolCall) -> None:
        self._clear_working()
        self._close_reasoning()
        self._error_stream.write(self._tool_panel_blank_line())
        rendered = self._format_pi_call_header_rich(
            call.tool_name, call.arguments_json
        )
        self._error_stream.write(self._tool_panel_rich_line(rendered))
        self._error_stream.write(self._tool_panel_blank_line())
        self._error_stream.flush()

    def render_tool_result(
        self,
        *,
        output_text: str,
        is_error: bool,
        duration_seconds: float | None = None,
    ) -> None:
        lines = output_text.splitlines() or [""]
        preview_lines = lines[: self._RESULT_LINE_PREVIEW_MAX_LENGTH]
        earlier = len(lines) - len(preview_lines)
        if earlier > 0:
            self._error_stream.write(
                self._tool_panel_line(
                    f"... ({earlier} earlier lines, ctrl+o to expand)",
                    style=self._ANSI_DIM,
                )
            )
            tail_preview = lines[-self._RESULT_LINE_PREVIEW_MAX_LENGTH :]
        else:
            tail_preview = preview_lines
        for line in tail_preview:
            self._error_stream.write(
                self._tool_panel_line(line, style=self._ANSI_DIM)
            )
        if is_error:
            self._error_stream.write(
                self._tool_panel_line(
                    "[error] tool reported a failure",
                    style=self._ANSI_RED + self._ANSI_DIM,
                )
            )
        # Pi keeps the `Took {n}s` caption inside the panel so the
        # block reads as one contiguous strip. Emit a blank panel row
        # for breathing room, then the duration, then a final blank
        # panel row before the next block starts.
        if duration_seconds is not None:
            self._error_stream.write(self._tool_panel_blank_line())
            self._error_stream.write(
                self._tool_panel_line(
                    f"Took {duration_seconds:.1f}s",
                    style=self._ANSI_DIM,
                )
            )
        self._error_stream.write(self._tool_panel_blank_line())
        self._error_stream.flush()

    def _tool_panel_line(
        self,
        text: str,
        *,
        style: str = "",
        bold: bool = False,
    ) -> str:
        """Render one row of a tool block inside the dark-green panel.

        Pads with a leading space (matches Pi's column gutter), applies
        the supplied style on top of the panel background, then writes
        `\\x1b[K` to fill the remainder of the row with the same
        background before resetting. On non-TTY streams the helper
        falls back to plain text with the leading space so captured
        logs stay readable.
        """

        if not self._enabled:
            return f" {text}\n"
        prefix = self._tool_panel_bg
        weight = self._ANSI_BOLD if bold else ""
        return f"{prefix}{weight}{style} {text}{self._ANSI_CLEAR_EOL}{self._ANSI_RESET}\n"

    def _tool_panel_blank_line(self) -> str:
        """Emit an empty row of the dark-green panel (spacing inside the block)."""

        if not self._enabled:
            return "\n"
        return f"{self._tool_panel_bg}{self._ANSI_CLEAR_EOL}{self._ANSI_RESET}\n"

    def _tool_panel_rich_line(
        self, segments: list[tuple[str, str]]
    ) -> str:
        """Render a multi-style row inside the dark-green panel.

        ``segments`` is an ordered sequence of ``(text, ansi_style)``
        pairs. Each segment is wrapped with its own ANSI weight/color
        on top of the panel background. The trailing `\\x1b[K` fills
        the rest of the row so the panel reads as a contiguous strip.
        On non-TTY streams the helper concatenates the text segments
        plain (no escapes) so captured logs stay readable.
        """

        if not self._enabled:
            return " " + "".join(text for text, _ in segments) + "\n"
        parts = [self._tool_panel_bg, " "]
        for text, style in segments:
            if style:
                parts.append(style)
                parts.append(text)
                parts.append(self._ANSI_RESET)
                parts.append(self._tool_panel_bg)
            else:
                parts.append(text)
        parts.append(self._ANSI_CLEAR_EOL)
        parts.append(self._ANSI_RESET)
        parts.append("\n")
        return "".join(parts)

    @staticmethod
    def _read_range_label(data: Mapping[str, Any]) -> str:
        """Format the ``:start-end`` line range for a ``read`` header.

        Pi's read tool natively exposes ``offset`` and ``limit`` style
        arguments. Pipy's bounded `read` tool uses a fixed line cap, but
        the codex provider may still emit the optional ``offset`` and
        ``limit`` properties that other read tools advertise. When
        present they shape the header label so the user sees the
        actual requested range; otherwise the default ``:1-200``
        matches the tool's hard-coded ``line_limit``.
        """

        start = data.get("offset")
        limit = data.get("limit")
        if isinstance(start, int) and start >= 0:
            start_line = start + 1
        else:
            start_line = 1
        if isinstance(limit, int) and limit > 0:
            end_line = start_line + limit - 1
        else:
            end_line = start_line + 199
        return f":{start_line}-{end_line}"

    def _format_pi_call_header_rich(
        self, tool_name: str, arguments_json: str
    ) -> list[tuple[str, str]]:
        """Return a list of (text, style) segments for a tool-call header.

        Pi styles the header per-segment: the verb (e.g. `read`,
        `ls`, `grep`) is bold white, the operand (path/pattern) is
        plain dim white, and the line range (`:1-200`) is yellow.
        We reproduce that by emitting separate text+style pairs,
        which `_tool_panel_rich_line` joins back into one panel row
        with each segment carrying its own ANSI weight/color while
        sharing the panel background.
        """

        try:
            data = json.loads(arguments_json)
        except (json.JSONDecodeError, ValueError):
            data = None
        if not isinstance(data, dict):
            data = {}
        bold = self._ANSI_BOLD
        plain = ""
        yellow = self._ANSI_YELLOW
        if tool_name == "read":
            path = str(data.get("path", ""))
            verb = "read resource" if path.startswith("/") else "read"
            range_label = self._read_range_label(data)
            return [
                (verb, bold),
                (" ", plain),
                (path, plain),
                (range_label, yellow),
            ]
        if tool_name == "ls":
            return [
                ("ls", bold),
                (" ", plain),
                (str(data.get("path", ".")), plain),
            ]
        if tool_name == "grep":
            return [
                ("grep", bold),
                (" ", plain),
                (f'"{data.get("pattern", "")}"', plain),
                (" ", plain),
                (str(data.get("path", ".")), plain),
            ]
        if tool_name == "find":
            return [
                ("find", bold),
                (" ", plain),
                (f'"{data.get("pattern", "")}"', plain),
                (" ", plain),
                (str(data.get("path", ".")), plain),
            ]
        if tool_name in {"write", "edit", "edit_diff"}:
            return [
                (tool_name, bold),
                (" ", plain),
                (str(data.get("path", "")), plain),
            ]
        if tool_name == "truncate":
            return [("truncate", bold)]
        preview = self._argument_preview(arguments_json)
        return [(f"{tool_name}({preview})", bold)]

    def _format_pi_call_header(self, tool_name: str, arguments_json: str) -> str:
        """Render a Pi-shape one-line tool header.

        Built-in read/ls/grep/find/write/edit tools render as Pi-style
        compact lines: ``read path:1-line_limit``, ``ls path``,
        ``grep "pattern" path``, ``find "pattern" path``. Unknown tools
        fall back to a ``name(args)`` form so the user can still see the
        invocation.
        """

        try:
            data = json.loads(arguments_json)
        except (json.JSONDecodeError, ValueError):
            data = None
        if not isinstance(data, dict):
            data = {}
        if tool_name == "read":
            path = data.get("path", "")
            prefix = "read resource" if str(path).startswith("/") else "read"
            range_label = self._read_range_label(data)
            return f"{prefix} {path}{range_label} (ctrl+o to expand)"
        if tool_name == "ls":
            path = data.get("path", ".")
            return f"ls {path}"
        if tool_name == "grep":
            pattern = data.get("pattern", "")
            path = data.get("path", ".")
            return f'grep "{pattern}" {path}'
        if tool_name == "find":
            pattern = data.get("pattern", "")
            path = data.get("path", ".")
            return f'find "{pattern}" {path}'
        if tool_name == "write":
            path = data.get("path", "")
            return f"write {path}"
        if tool_name == "edit":
            path = data.get("path", "")
            return f"edit {path}"
        if tool_name == "edit_diff":
            path = data.get("path", "")
            return f"edit_diff {path}"
        if tool_name == "truncate":
            return "truncate"
        preview = self._argument_preview(arguments_json)
        return f"{tool_name}({preview})"

    def _argument_preview(self, arguments_json: str) -> str:
        try:
            data = json.loads(arguments_json)
        except (json.JSONDecodeError, ValueError):
            preview = arguments_json.strip()
            if len(preview) > self._ARGUMENT_VALUE_PREVIEW_LIMIT:
                preview = preview[: self._ARGUMENT_VALUE_PREVIEW_LIMIT] + "…"
            return preview
        if not isinstance(data, dict):
            return ""
        pieces: list[str] = []
        for key, value in data.items():
            if isinstance(value, str):
                value_repr = value
                if len(value_repr) > self._ARGUMENT_VALUE_PREVIEW_LIMIT:
                    value_repr = value_repr[: self._ARGUMENT_VALUE_PREVIEW_LIMIT] + "…"
                pieces.append(f'{key}="{value_repr}"')
            elif isinstance(value, (int, float, bool)) or value is None:
                pieces.append(f"{key}={value}")
            else:
                pieces.append(f"{key}=…")
        return ", ".join(pieces)

    def _style(self, text: str, code: str) -> str:
        if not self._enabled:
            return text
        return f"{code}{text}{self._ANSI_RESET}"


__all__ = [
    "NativeToolReplResult",
    "NativeToolReplSession",
    "production_tool_registry",
]

"""Slice 4 tests: `NativeToolReplSession` skeleton.

These tests pin the loop's behavior using a test-only `_FixtureTool` that
echoes its `text` argument back. The production tool registry stays empty;
the loop is exercised by injecting the fixture registry directly. Real
providers all advertise `supports_tool_calls=False` at this point, so the
session is also exercised against `FakeNativeProvider` with a programmable
script.
"""

from __future__ import annotations

import ast
import builtins
import io
import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO, cast

import pytest

from pipy_harness.extensions import ExtensionCapabilityError
from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AgentEvent,
    AgentRunCompleted,
    AgentRunOutcome,
    AgentTurnOutcome,
    AgentUsage,
    MessageCompleted,
    ProviderFailed,
    ToolCallCompleted,
    TurnCompleted,
    UsageUpdated,
)
from pipy_harness.native.agent.history import (
    AgentHistoryCompaction,
    _agent_history_summary,
)
from pipy_harness.native.agent.provider_turn import ProviderTurnInterruption
from pipy_harness.native.agent.loop_policy import MAX_AGENT_TOOL_BUDGET
from pipy_harness.native.agent.usage import AgentTokenPricing, AgentUsageAccumulator
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.coding.state import CodingSessionUsageSnapshot
from pipy_harness.native import (
    FakeNativeProvider,
    NativeToolReplResult,
    NativeToolReplSession,
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
    production_tool_registry,
)
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.extension_provider_catalog import (
    extension_reserved_command_names,
    extension_reserved_tool_names,
    load_extension_provider_contributions,
)
from pipy_harness.native.tool_loop_session import _wait_for_provider_interrupt
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.repl_state import (
    ModelRuntime,
    NativeModelSelection,
    NativeReplProviderState,
)
from pipy_harness.native.session_resume import ResumeContext
from pipy_harness.native.session_tree import ModelChangeEntry, NativeSessionTree
from pipy_harness.native.tool_capabilities import ToolFilterOptions
from pipy_harness.native.tui import (
    TURN_ABORTED,
    TURN_LOCAL_COMMAND,
    TURN_SETTLED,
    TURN_STEERED,
    ToolLoopTerminalUi,
)
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
)


@dataclass(frozen=True, slots=True)
class _FixtureEchoTool:
    """Test-only echo tool used to exercise the loop end-to-end."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="echo",
            description="Return the provided text verbatim.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "maxLength": 1024},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        text = str(request.arguments["text"])
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=text,
            provider_correlation_id=request.provider_correlation_id,
        )


@dataclass(frozen=True, slots=True)
class _FixtureErrorTool:
    """Test-only tool that returns a valid execution error observation."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="fail",
            description="Return a tool execution error.",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        del context
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text="fail error: expected fixture failure",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )


def _make_call(
    tool_name: str,
    arguments_json: str,
    *,
    correlation_id: str = "call_test_1",
) -> ProviderToolCall:
    return ProviderToolCall(
        provider_correlation_id=correlation_id,
        tool_name=tool_name,
        arguments_json=arguments_json,
    )


@dataclass(slots=True)
class _UsageScriptProvider:
    """Tool-capable provider with deterministic per-call usage payloads."""

    script: tuple[tuple[Mapping[str, Any] | None, tuple[ProviderToolCall, ...]], ...]
    supports_tool_calls: bool = True
    name: str = "usage-script"
    model_id: str = "usage-script-model"
    statuses: tuple[HarnessStatus, ...] = ()
    call_index: int = 0
    requests: list[ProviderRequest] = field(default_factory=list)

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        self.requests.append(request)
        del stream_sink, reasoning_sink, cancel_token
        usage, tool_calls = self.script[self.call_index]
        status = (
            self.statuses[self.call_index]
            if self.call_index < len(self.statuses)
            else HarnessStatus.SUCCEEDED
        )
        self.call_index += 1
        now = datetime.now(UTC)
        return ProviderResult(
            status=status,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=(
                f"provider turn {self.call_index}"
                if status is HarnessStatus.SUCCEEDED
                else None
            ),
            usage=usage,
            tool_calls=tool_calls,
            error_type=(
                "UsageScriptProviderFailed"
                if status is not HarnessStatus.SUCCEEDED
                else None
            ),
            error_message=(
                "deterministic provider failure"
                if status is not HarnessStatus.SUCCEEDED
                else None
            ),
        )


@dataclass(slots=True)
class _CollectingAgentEventSink:
    events: list[AgentEvent] = field(default_factory=list)
    trace: list[AgentEvent | tuple[str, AgentUsage, int]] | None = None

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)
        if self.trace is not None:
            self.trace.append(event)


def _capture_usage_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    list[AgentUsageAccumulator],
    list[tuple[str, str, AgentTokenPricing | None]],
]:
    """Spy on the product composition sites without replacing accumulator logic."""

    import pipy_harness.native.agent.loop as agent_loop
    import pipy_harness.native.tool_loop_session as tool_loop_session

    constructed: list[AgentUsageAccumulator] = []
    pricing_lookups: list[tuple[str, str, AgentTokenPricing | None]] = []
    original_pricing_for = tool_loop_session._pricing_for

    class _RecordingUsageAccumulator(AgentUsageAccumulator):
        def __init__(self, pricing: AgentTokenPricing | None = None) -> None:
            super().__init__(pricing)
            constructed.append(self)

    def record_pricing(provider_name: str, model_id: str) -> AgentTokenPricing | None:
        pricing = original_pricing_for(provider_name, model_id)
        pricing_lookups.append((provider_name, model_id, pricing))
        return pricing

    monkeypatch.setattr(
        tool_loop_session, "AgentUsageAccumulator", _RecordingUsageAccumulator
    )
    monkeypatch.setattr(agent_loop, "AgentUsageAccumulator", _RecordingUsageAccumulator)
    monkeypatch.setattr(tool_loop_session, "_pricing_for", record_pricing)
    return constructed, pricing_lookups


def _record_footer_in_trace(
    monkeypatch: pytest.MonkeyPatch,
    trace: list[AgentEvent | tuple[str, AgentUsage, int]],
) -> None:
    def record_footer(
        self: NativeToolReplSession,
        error_stream: TextIO,
        *,
        cwd: Path,
        provider_name: str,
        model_id: str,
        user_turn_count: int,
        tool_invocation_count: int,
        usage_snapshot: CodingSessionUsageSnapshot | None = None,
    ) -> None:
        del self, error_stream, cwd, provider_name, model_id
        del user_turn_count, tool_invocation_count
        assert usage_snapshot is not None
        trace.append(
            (
                "footer",
                usage_snapshot.usage,
                usage_snapshot.last_total_tokens,
            )
        )

    monkeypatch.setattr(NativeToolReplSession, "_print_footer", record_footer)


def _assert_usage_trace_order(
    trace: list[AgentEvent | tuple[str, AgentUsage, int]],
    *,
    first_run_usage: AgentUsage,
    session_usage: AgentUsage,
) -> None:
    ordered = [
        item
        for item in trace
        if isinstance(
            item,
            (
                UsageUpdated,
                ProviderFailed,
                MessageCompleted,
                TurnCompleted,
                AgentRunCompleted,
            ),
        )
        or isinstance(item, tuple)
    ]
    assert [
        type(item).__name__ if not isinstance(item, tuple) else item[0]
        for item in ordered
    ] == [
        "footer",
        "MessageCompleted",  # first run's user message
        "UsageUpdated",
        "MessageCompleted",  # tool-requesting assistant message
        "TurnCompleted",
        "UsageUpdated",  # missing usage preserves cumulative usage, last total zero
        "MessageCompleted",  # settling assistant message
        "footer",
        "TurnCompleted",
        "AgentRunCompleted",
        "MessageCompleted",  # second run's user message
        "UsageUpdated",
        "ProviderFailed",
        "footer",
        "MessageCompleted",  # failed run's empty assistant message
        "TurnCompleted",
        "AgentRunCompleted",
    ]
    assert [item for item in ordered if isinstance(item, tuple)] == [
        ("footer", AgentUsage(), 0),
        ("footer", first_run_usage, 0),
        ("footer", session_usage, 12),
    ]


def _run_session(
    *,
    tool_calls_script: tuple[tuple[ProviderToolCall, ...], ...],
    tool_registry: Mapping[str, ToolPort] | None,
    user_inputs: tuple[str, ...],
    tmp_path: Path,
    tool_budget: int = 10,
) -> tuple[NativeToolReplResult, str, str]:
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=tool_calls_script,
    )
    session = NativeToolReplSession(
        provider=provider,
        tool_registry=dict(tool_registry or {}),
        tool_budget=tool_budget,
    )
    input_stream = io.StringIO("\n".join(user_inputs) + "\n")
    output_stream = io.StringIO()
    error_stream = io.StringIO()
    result = session.run(
        workspace_root=tmp_path,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )
    return result, output_stream.getvalue(), error_stream.getvalue()


def test_footer_paths_read_constant_time_state_scalars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.chrome as chrome
    import pipy_harness.native.tool_loop_session as tool_loop_session

    module_path = tool_loop_session.__file__
    chrome_path = chrome.__file__
    assert module_path is not None
    assert chrome_path is not None
    syntax = ast.parse(Path(module_path).read_text())
    chrome_syntax = ast.parse(Path(chrome_path).read_text())
    session_class = next(
        node
        for node in syntax.body
        if isinstance(node, ast.ClassDef) and node.name == "NativeToolReplSession"
    )
    run_method = next(
        node
        for node in session_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "run"
    )
    # The footer-text and legacy-footer refresh bodies are owned by chrome's
    # `_ChromeFooterEffects`; `run()` keeps only the inline pre-loop legacy-footer
    # paint. The four injected/inline footer calls still read the same
    # constant-time state scalars.
    assert not any(
        isinstance(node, ast.ClassDef) and node.name == "_FooterEffects"
        for node in syntax.body
    )
    footer_effects_class = next(
        node
        for node in chrome_syntax.body
        if isinstance(node, ast.ClassDef) and node.name == "_ChromeFooterEffects"
    )
    footer_calls = [
        node
        for scope in (run_method, footer_effects_class)
        for node in ast.walk(scope)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"footer_text", "print_footer", "_print_footer"}
    ]
    assert len(footer_calls) == 4
    for call in footer_calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        for keyword_name in (
            "provider_name",
            "model_id",
            "user_turn_count",
            "tool_invocation_count",
        ):
            value = keywords[keyword_name]
            assert isinstance(value, ast.Attribute)
            assert value.attr == keyword_name
            assert isinstance(value.value, ast.Name)
            assert value.value.id == "coding_state"

    # The result projection (the terminate `FAILED` branch and the post-loop
    # `SUCCEEDED` finalize) relocated with the per-iteration loop step and its
    # bookends into the module-level `_ReplLoopStep` handler; the two
    # `result_snapshot` calls read the same constant-time state scalars there.
    repl_loop_step_class = next(
        node
        for node in syntax.body
        if isinstance(node, ast.ClassDef) and node.name == "_ReplLoopStep"
    )
    result_snapshot_calls = [
        node
        for node in ast.walk(repl_loop_step_class)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "result_snapshot"
    ]
    assert len(result_snapshot_calls) == 2

    session = NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True)
    )
    state = session._coding_state
    monkeypatch.setattr(tool_loop_session, "chrome_width", lambda _stream: 120)
    footer = session._footer_text(
        cwd=tmp_path,
        provider_name=state.provider_name,
        model_id=state.model_id,
        user_turn_count=state.user_turn_count,
        tool_invocation_count=state.tool_invocation_count,
    )

    assert footer.startswith(f"{tmp_path}\n$0.000 (api)")
    assert "(fake) fake-native-bootstrap • default" in footer


def test_session_command_family_has_one_narrow_composition_root_executor() -> None:
    import pipy_harness.native.tool_loop_session as tool_loop_session

    module_path = tool_loop_session.__file__
    assert module_path is not None
    syntax = ast.parse(Path(module_path).read_text(encoding="utf-8"))
    effects_class = next(
        node
        for node in syntax.body
        if isinstance(node, ast.ClassDef) and node.name == "_SessionCommandEffects"
    )
    dataclass_decorator = next(
        decorator
        for decorator in effects_class.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "dataclass"
    )
    assert {
        keyword.arg: ast.literal_eval(keyword.value)
        for keyword in dataclass_decorator.keywords
    } == {"frozen": True, "slots": True, "kw_only": True}
    assert {
        node.target.id
        for node in effects_class.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    } == {
        "session",
        "ctl",
        "cwd",
        "terminal_ui",
        "error_stream",
        "repl_input",
        "diag",
        "apply_compaction",
        "extension_session_allows",
        "rebuild_messages_from_tree",
        "redraw_custom_entries_for_active_branch",
        "current_session_dir",
        "resolve_session_file",
        "summarize_branch",
    }

    interpreter_class = next(
        node
        for node in syntax.body
        if isinstance(node, ast.ClassDef) and node.name == "_BuiltinCommandInterpreter"
    )
    interpret_method = next(
        node
        for node in interpreter_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "interpret"
    )
    session_actions = {
        "SHOW_SESSION_STATUS",
        "COMPACT",
        "SESSION_NAME",
        "NEW_SESSION",
        "SESSION_TREE",
        "SESSION_RESUME",
        "SESSION_FORK",
        "SESSION_CLONE",
    }
    assert (
        not {
            node.attr
            for node in ast.walk(interpret_method)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "CodingCommandAction"
        }
        & session_actions
    )
    delegation_calls = [
        node
        for node in ast.walk(interpret_method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == "session_effects"
        and node.func.attr == "execute"
    ]
    assert len(delegation_calls) == 1


def test_provider_configuration_family_has_one_typed_effect_owner() -> None:
    import pipy_harness.native.tool_loop_session as tool_loop_session

    module_path = tool_loop_session.__file__
    assert module_path is not None
    syntax = ast.parse(Path(module_path).read_text(encoding="utf-8"))
    effects_class = next(
        node
        for node in syntax.body
        if isinstance(node, ast.ClassDef)
        and node.name == "_ProviderConfigurationCommandEffects"
    )
    dataclass_decorator = next(
        decorator
        for decorator in effects_class.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "dataclass"
    )
    assert {
        keyword.arg: ast.literal_eval(keyword.value)
        for keyword in dataclass_decorator.keywords
    } == {"frozen": True, "slots": True, "kw_only": True}
    assert {
        node.target.id
        for node in effects_class.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    } == {
        "session",
        "ctl",
        "coding_state",
        "terminal_ui",
        "error_stream",
        "keybindings",
        "settings",
        "cwd",
        "prompt_history_store",
        "provider_mutation",
    }

    interpreter_class = next(
        node
        for node in syntax.body
        if isinstance(node, ast.ClassDef) and node.name == "_BuiltinCommandInterpreter"
    )
    interpret_method = next(
        node
        for node in interpreter_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "interpret"
    )
    provider_configuration_actions = {
        "SHOW_HOTKEYS",
        "SHOW_CHANGELOG",
        "COPY_LAST_ANSWER",
        "SETTINGS",
        "TRUST_PROJECT",
        "MODEL",
        "SCOPED_MODELS",
        "LOGIN",
        "LOGOUT",
    }
    assert (
        not {
            node.attr
            for node in ast.walk(interpret_method)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "CodingCommandAction"
        }
        & provider_configuration_actions
    )
    delegation_calls = [
        node
        for node in ast.walk(interpret_method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == "provider_configuration_effects"
        and node.func.attr == "execute"
    ]
    assert len(delegation_calls) == 1
    root_parameters = {
        argument.arg
        for argument in (
            *interpret_method.args.posonlyargs,
            *interpret_method.args.args,
            *interpret_method.args.kwonlyargs,
        )
    }
    assert {
        "keybindings",
        "settings",
        "prompt_history_store",
        "apply_model_selection",
        "apply_auth_change",
    }.isdisjoint(root_parameters)


def test_transfer_and_reload_families_have_closed_phased_effect_owners() -> None:
    import pipy_harness.native.tool_loop_session as tool_loop_session

    module_path = tool_loop_session.__file__
    assert module_path is not None
    syntax = ast.parse(Path(module_path).read_text(encoding="utf-8"))
    classes = {
        node.name: node for node in syntax.body if isinstance(node, ast.ClassDef)
    }

    expected_fields = {
        "_TransferCommandEffects": {
            "session",
            "ctl",
            "cwd",
            "system_prompt",
            "input_stream",
            "error_stream",
            "terminal_ui",
            "diag",
            "current_session_dir",
            "session_switch_allows",
            "rebuild_messages_from_tree",
        },
        "_ReloadCommandEffects": {
            "session",
            "ctl",
            "settings",
            "keybindings",
            "terminal_ui",
            "renderer",
            "error_stream",
            "emitter",
            "provider_mutation",
            "cwd",
            "resource_options",
            "tool_capabilities",
            "diag",
            "redraw_custom_entries_for_active_branch",
            "extension_send_message",
            "extension_render_details",
        },
        "_BuiltinCommandInterpreter": {
            "session_effects",
            "provider_configuration_effects",
            "transfer_effects",
            "reload_effects",
            "refresh_legacy_footer",
            "refresh_legacy_footer_with_usage",
        },
    }
    for class_name, fields in expected_fields.items():
        owner = classes[class_name]
        dataclass_decorator = next(
            decorator
            for decorator in owner.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "dataclass"
        )
        assert {
            keyword.arg: ast.literal_eval(keyword.value)
            for keyword in dataclass_decorator.keywords
        } == {"frozen": True, "slots": True, "kw_only": True}
        assert {
            node.target.id
            for node in owner.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        } == fields

    assert "_ReloadConfigurationDependencies" not in classes
    interpreter = classes["_BuiltinCommandInterpreter"]
    interpret = next(
        node
        for node in interpreter.body
        if isinstance(node, ast.FunctionDef) and node.name == "interpret"
    )
    parameters = (
        *interpret.args.posonlyargs,
        *interpret.args.args,
        *interpret.args.kwonlyargs,
    )
    assert len(parameters) < 10
    transfer_reload_actions = {
        "SESSION_EXPORT",
        "SESSION_IMPORT",
        "SESSION_SHARE",
        "RELOAD",
    }
    assert transfer_reload_actions.isdisjoint(
        {
            node.attr
            for node in ast.walk(interpret)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "CodingCommandAction"
        }
    )
    delegated_owners = [
        node.func.value.attr
        for node in ast.walk(interpret)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
    ]
    assert sorted(delegated_owners) == [
        "provider_configuration_effects",
        "reload_effects",
        "session_effects",
        "transfer_effects",
    ]
    root_names = {node.id for node in ast.walk(interpret) if isinstance(node, ast.Name)}
    assert {
        "ctl",
        "session",
        "coding_state",
        "terminal_ui",
        "renderer",
        "settings",
        "keybindings",
        "resource_options",
        "tool_capabilities",
    }.isdisjoint(root_names)

    reload_owner = classes["_ReloadCommandEffects"]
    reload_execute = next(
        node
        for node in reload_owner.body
        if isinstance(node, ast.FunctionDef) and node.name == "execute"
    )
    publication_lifetime = next(
        node
        for node in reload_execute.body
        if isinstance(node, ast.Try)
        and any(isinstance(statement, ast.With) for statement in node.body)
    )
    publishing = next(
        statement
        for statement in publication_lifetime.body
        if isinstance(statement, ast.With)
    )
    assert len(publishing.items) == 1
    publishing_context = publishing.items[0].context_expr
    assert isinstance(publishing_context, ast.Call)
    assert not publishing_context.args
    assert not publishing_context.keywords
    publishing_function = publishing_context.func
    assert isinstance(publishing_function, ast.Attribute)
    assert publishing_function.attr == "publishing"
    generation_ref = publishing_function.value
    assert isinstance(generation_ref, ast.Attribute)
    assert generation_ref.attr == "generation_ref"
    ctl = generation_ref.value
    assert isinstance(ctl, ast.Attribute)
    assert ctl.attr == "ctl"
    assert isinstance(ctl.value, ast.Name)
    assert ctl.value.id == "self"
    phase_calls = [
        node
        for statement in publishing.body
        for node in ast.walk(statement)
        if isinstance(node, ast.Call)
    ]
    assert len(phase_calls) == 5
    assert all(isinstance(call.func, ast.Attribute) for call in phase_calls)
    phase_names = [
        call.func.attr for call in phase_calls if isinstance(call.func, ast.Attribute)
    ]
    assert phase_names == [
        "_reload_configuration_and_resources",
        "_reload_extension_generation",
        "refresh_provider_after_reload",
        "_publish_tool_and_lifecycle_projections",
        "_refresh_presentation_and_persistence",
    ]
    disposal_call = next(
        node
        for statement in publication_lifetime.finalbody
        for node in ast.walk(statement)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "candidate"
        and node.func.attr == "dispose"
    )
    lifecycle_call = next(
        node
        for node in reload_execute.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "fire_lifecycle"
    )
    lifecycle_expression = lifecycle_call.value
    assert isinstance(lifecycle_expression, ast.Call)
    assert len(lifecycle_expression.args) == 1
    lifecycle_event = lifecycle_expression.args[0]
    assert isinstance(lifecycle_event, ast.Name)
    assert lifecycle_event.id == "EVENT_SESSION_START"
    assert len(lifecycle_expression.keywords) == 1
    lifecycle_reason = lifecycle_expression.keywords[0]
    assert lifecycle_reason.arg == "reason"
    assert isinstance(lifecycle_reason.value, ast.Constant)
    assert lifecycle_reason.value.value == "reload"
    final_diagnostic = next(
        node
        for node in reload_execute.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "diag"
    )
    publishing_end = publishing.end_lineno
    assert publishing_end is not None
    assert publishing_end < disposal_call.lineno
    assert disposal_call.lineno < lifecycle_call.lineno
    assert lifecycle_call.lineno < final_diagnostic.lineno
    reload_methods = {
        node.name: node
        for node in reload_owner.body
        if isinstance(node, ast.FunctionDef)
    }
    reload_method_line_budgets = {
        "execute": 36,
        "_reload_configuration_and_resources": 32,
        "_reload_extension_generation": 54,
        "_commit_extension_generation": 26,
        "_publish_tool_and_lifecycle_projections": 30,
        "_diagnose_unknown_tool_filters": 12,
        "_refresh_presentation_and_persistence": 40,
    }
    assert reload_methods.keys() == reload_method_line_budgets.keys()
    for method_name, method in reload_methods.items():
        assert method.end_lineno is not None
        method_length = method.end_lineno - method.lineno + 1
        budget = reload_method_line_budgets[method_name]
        assert method_length <= budget, (
            f"{method_name} grew to {method_length} lines; phased-review budget is {budget}"
        )

    reload_delegation = next(
        node
        for node in ast.walk(interpret)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "reload_effects"
    )
    assert reload_delegation.end_lineno is not None
    footer_calls = [
        node
        for node in ast.walk(interpret)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr
        in {"refresh_legacy_footer", "refresh_legacy_footer_with_usage"}
    ]
    assert len(footer_calls) == 2
    assert all(
        reload_delegation.end_lineno < footer_call.lineno
        for footer_call in footer_calls
    )


def test_changed_agent_history_compaction_has_nonempty_product_summary() -> None:
    result = AgentHistoryCompaction(
        messages=(),
        changed=True,
        dropped_group_count=1,
        dropped_message_count=3,
        dropped_user_count=1,
        dropped_assistant_count=1,
        dropped_tool_call_count=1,
        dropped_tool_result_count=1,
        retained_group_count=2,
        retained_message_count=4,
        bytes_before=100,
        bytes_after=40,
    )

    summary = _agent_history_summary(result)

    assert summary
    assert "1 earlier exchange(s)" in summary
    assert "1 assistant turn(s), 1 tool call(s)" in summary


def test_composition_keeps_callback_counters_and_rebinds_final_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipy_harness.native.coding.agent_run as agent_run
    from pipy_harness.native.agent import (
        AgentAssistantMessage,
        AgentRunResult,
        ProductContent,
    )
    from pipy_harness.native.agent.loop import (
        AgentLoopOutcome,
        AgentLoopRunInput,
        AgentLoopStatusPolicy,
    )
    from pipy_harness.native.agent.loop_policy import AgentToolPolicyState
    from pipy_harness.native.agent.ports import AgentEventSink
    from pipy_harness.native.clipboard import ClipboardResult

    callback_state = AgentToolPolicyState(
        tool_budget=10,
        invocations_this_turn=2,
        tool_invocation_count=7,
        malformed_argument_count=4,
        consecutive_malformed_streak=2,
        budget_exhausted_count=3,
    )
    distinct_outcome_state = AgentToolPolicyState(
        tool_budget=10,
        invocations_this_turn=1,
        tool_invocation_count=71,
        malformed_argument_count=41,
        consecutive_malformed_streak=21,
        budget_exhausted_count=31,
    )

    class _ComposedAgentLoop:
        def __init__(self, **ports: object) -> None:
            self._status = cast(AgentLoopStatusPolicy, ports["status_policy"])
            self._events = cast(AgentEventSink, ports["event_sink"])

        def run(self, run_input: AgentLoopRunInput) -> AgentLoopOutcome:
            self._status.run_entered()
            accepted = run_input.active_input.accepted_message
            assistant = AgentAssistantMessage(ProductContent("rebound answer"))
            final_history = (*run_input.history, accepted, assistant)
            self._status.input_accepted()
            self._status.tool_policy_state_changed(callback_state)
            result = AgentRunResult(
                AgentRunOutcome.SUCCEEDED,
                final_history,
                AgentUsage(),
            )
            self._events.emit(AgentRunCompleted(result))
            return AgentLoopOutcome(
                result,
                final_history,
                distinct_outcome_state,
            )

    copied: list[str] = []

    def copy_answer(text: str, *, terminal_stream: TextIO) -> ClipboardResult:
        del terminal_stream
        copied.append(text)
        return ClipboardResult(True, "test", len(text.encode()), "test clipboard")

    monkeypatch.setattr(agent_run, "AgentLoop", _ComposedAgentLoop)
    result = NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True),
        clipboard_copy=copy_answer,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n/copy\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert result.user_turn_count == 1
    assert result.tool_invocation_count == callback_state.tool_invocation_count
    assert result.malformed_argument_count == callback_state.malformed_argument_count
    assert (
        result.consecutive_malformed_streak
        == callback_state.consecutive_malformed_streak
    )
    assert result.budget_exhausted_count == callback_state.budget_exhausted_count
    assert result.tool_invocation_count != distinct_outcome_state.tool_invocation_count
    assert copied == ["rebound answer"]


def test_canonical_usage_order_and_scope_cover_success_and_provider_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_usage = {
        "input_tokens": 10,
        "output_tokens": 2,
        "reasoning_tokens": 1,
        "cached_tokens": 3,
        "cache_write_tokens": 4,
        "total_tokens": 20,
    }
    second_usage = {"input_tokens": 7, "output_tokens": 5, "total_tokens": 12}
    first_run_usage = AgentUsage(
        input_tokens=10,
        output_tokens=2,
        reasoning_tokens=1,
        cache_read_tokens=3,
        cache_write_tokens=4,
    )
    second_run_usage = AgentUsage(input_tokens=7, output_tokens=5)
    session_usage = AgentUsage(
        input_tokens=17,
        output_tokens=7,
        reasoning_tokens=1,
        cache_read_tokens=3,
        cache_write_tokens=4,
    )
    provider = _UsageScriptProvider(
        (
            (first_usage, (_make_call("echo", '{"text":"ok"}'),)),
            (None, ()),
            (second_usage, ()),
        ),
        statuses=(
            HarnessStatus.SUCCEEDED,
            HarnessStatus.SUCCEEDED,
            HarnessStatus.FAILED,
        ),
    )
    trace: list[AgentEvent | tuple[str, AgentUsage, int]] = []
    canonical = _CollectingAgentEventSink(trace=trace)
    _record_footer_in_trace(monkeypatch, trace)
    result = NativeToolReplSession(
        provider=provider,
        tool_registry={"echo": _FixtureEchoTool()},
        agent_event_sink=canonical,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("first\nsecond\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 1
    updates = [event for event in canonical.events if isinstance(event, UsageUpdated)]
    assert [
        (event.cumulative_usage, event.last_turn_total_tokens) for event in updates
    ] == [(first_run_usage, 20), (first_run_usage, 0), (second_run_usage, 12)]
    completed = [
        event for event in canonical.events if isinstance(event, AgentRunCompleted)
    ]
    assert [event.result.usage for event in completed] == [
        first_run_usage,
        second_run_usage,
    ]
    assert [event.result.outcome for event in completed] == [
        AgentRunOutcome.SUCCEEDED,
        AgentRunOutcome.FAILED,
    ]

    _assert_usage_trace_order(
        trace, first_run_usage=first_run_usage, session_usage=session_usage
    )


@pytest.mark.parametrize("model_id", ["gpt-5", "gpt-5.6-sol"])
def test_product_pricing_lookup_is_injected_into_session_and_run_usage(
    model_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    constructed, pricing_lookups = _capture_usage_construction(monkeypatch)
    usage = {
        "input_tokens": 10,
        "output_tokens": 2,
        "reasoning_tokens": 1,
        "total_tokens": 13,
    }
    provider = _UsageScriptProvider(
        ((usage, ()),), name="openai-codex", model_id=model_id
    )

    result = NativeToolReplSession(provider=provider, tool_registry={}).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("priced\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert [
        (provider_name, selected_model)
        for provider_name, selected_model, _ in pricing_lookups
    ] == [
        ("openai-codex", model_id),
        ("openai-codex", model_id),
    ]
    assert len(constructed) == 2
    assert constructed[0] is not constructed[1]
    assert all(pricing is not None for _, _, pricing in pricing_lookups)
    assert [accumulator.agent_usage().cost_usd for accumulator in constructed] == [
        pytest.approx(0.0000425),
        pytest.approx(0.0000425),
    ]


# --------------------- production registry holds model tools ----------------


def test_production_tool_registry_registers_real_bash():
    registry = production_tool_registry()

    expected = {
        "read",
        "ls",
        "grep",
        "find",
        "write",
        "edit",
        "edit_diff",
        "truncate",
        "bash",
    }
    assert set(registry.keys()) == expected
    assert "bash" in registry
    for name in registry:
        assert registry[name].definition.name == name


# ------------------------- provider capability gate ------------------------


def test_session_rejects_provider_without_tool_call_capability():
    provider = FakeNativeProvider(supports_tool_calls=False)

    with pytest.raises(ValueError, match="supports_tool_calls"):
        NativeToolReplSession(provider=provider)


def test_session_rejects_fake_provider_when_capability_not_flipped():
    provider = FakeNativeProvider()

    with pytest.raises(ValueError, match="supports_tool_calls"):
        NativeToolReplSession(provider=provider)


# --------------------------- tool budget validation -------------------------


def test_session_rejects_tool_budget_outside_supported_range():
    provider = FakeNativeProvider(supports_tool_calls=True)

    with pytest.raises(ValueError, match=r"\[1, 200\]"):
        NativeToolReplSession(provider=provider, tool_budget=0)
    with pytest.raises(ValueError, match=r"\[1, 200\]"):
        NativeToolReplSession(provider=provider, tool_budget=201)


def test_session_tool_budget_cap_is_the_canonical_agent_maximum() -> None:
    assert NativeToolReplSession.MAX_TOOL_BUDGET is MAX_AGENT_TOOL_BUDGET
    assert MAX_AGENT_TOOL_BUDGET == 200


def test_session_rejects_non_int_tool_budget():
    provider = FakeNativeProvider(supports_tool_calls=True)

    with pytest.raises(TypeError, match="tool_budget"):
        NativeToolReplSession(provider=provider, tool_budget=True)


@dataclass(slots=True)
class _ProviderInterruptUi:
    outcome: str
    raise_keyboard_interrupt: bool = False
    accept_queue: bool | None = None
    accept_commands: bool | None = None
    seen_done_event: threading.Event | None = None
    seen_cancel_event: threading.Event | None = None

    def wait_for_active_turn_interrupt(
        self,
        done_event: threading.Event,
        cancel_event: threading.Event,
        *,
        poll_seconds: float = 0.05,
        accept_queue: bool = False,
        accept_commands: bool = False,
    ) -> str:
        del poll_seconds
        self.accept_queue = accept_queue
        self.accept_commands = accept_commands
        self.seen_done_event = done_event
        self.seen_cancel_event = cancel_event
        if self.raise_keyboard_interrupt:
            raise KeyboardInterrupt
        if self.outcome != TURN_SETTLED:
            cancel_event.set()
        return self.outcome


@pytest.mark.parametrize(
    ("terminal_outcome", "expected_interruption", "expects_cancel"),
    [
        (TURN_SETTLED, ProviderTurnInterruption.SETTLED, False),
        (TURN_ABORTED, ProviderTurnInterruption.OPERATOR_ABORT, True),
        (TURN_STEERED, ProviderTurnInterruption.STEERING, True),
        (TURN_LOCAL_COMMAND, ProviderTurnInterruption.LOCAL_COMMAND, True),
    ],
)
def test_provider_interrupt_waiter_maps_terminal_outcomes(
    terminal_outcome: str,
    expected_interruption: ProviderTurnInterruption,
    expects_cancel: bool,
) -> None:
    ui = _ProviderInterruptUi(terminal_outcome)
    done_event = threading.Event()
    cancel_event = threading.Event()

    interruption = _wait_for_provider_interrupt(
        cast(ToolLoopTerminalUi, ui), done_event, cancel_event
    )

    assert interruption is expected_interruption
    assert cancel_event.is_set() is expects_cancel
    assert ui.accept_queue is True
    assert ui.accept_commands is False
    assert ui.seen_done_event is done_event
    assert ui.seen_cancel_event is cancel_event


def test_provider_interrupt_waiter_maps_keyboard_interrupt_and_signals_cancel() -> None:
    ui = _ProviderInterruptUi(TURN_SETTLED, raise_keyboard_interrupt=True)
    done_event = threading.Event()
    cancel_event = threading.Event()

    interruption = _wait_for_provider_interrupt(
        cast(ToolLoopTerminalUi, ui), done_event, cancel_event
    )

    assert interruption is ProviderTurnInterruption.OPERATOR_ABORT
    assert cancel_event.is_set()
    assert ui.accept_queue is True
    assert ui.accept_commands is False


# --------------------------- successful invocation --------------------------


def test_session_invokes_fixture_tool_and_reports_metadata(tmp_path: Path):
    tool = _FixtureEchoTool()
    script = (
        (_make_call("echo", '{"text": "hello"}'),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": tool},
        user_inputs=("please echo hello",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.exit_code == 0
    assert result.user_turn_count == 1
    assert result.tool_invocation_count == 1
    assert result.malformed_argument_count == 0
    assert result.consecutive_malformed_streak == 0
    assert result.budget_exhausted_count == 0
    assert result.error_type is None
    assert "pipy v" in stderr  # chrome present


def test_tool_execution_errors_do_not_count_as_malformed(tmp_path: Path):
    tool = _FixtureErrorTool()
    script = (
        (_make_call("fail", "{}", correlation_id="a"),),
        (_make_call("fail", "{}", correlation_id="b"),),
        (_make_call("fail", "{}", correlation_id="c"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"fail": tool},
        user_inputs=("call failing tool",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.exit_code == 0
    assert result.tool_invocation_count == 3
    assert result.malformed_argument_count == 0
    assert result.consecutive_malformed_streak == 0
    assert result.error_type is None
    assert "3 consecutive malformed tool calls" not in stderr


# ----------------------------- unknown tool name ----------------------------


def test_unadvertised_unknown_tool_is_a_policy_error_not_malformed(tmp_path: Path):
    script = (
        (_make_call("missing_tool", "{}"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={},
        user_inputs=("call missing",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 0
    assert result.malformed_argument_count == 0
    assert result.consecutive_malformed_streak == 0
    assert "pipy v" in stderr  # chrome present


# ------------------------------ malformed JSON ------------------------------


def test_invalid_arguments_json_is_returned_as_error_observation(tmp_path: Path):
    tool = _FixtureEchoTool()
    script = (
        (_make_call("echo", "{not json"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": tool},
        user_inputs=("call echo",),
        tmp_path=tmp_path,
    )

    assert result.malformed_argument_count == 1
    assert result.tool_invocation_count == 0
    assert "pipy v" in stderr  # chrome present


# --------------------------- schema validation fail -------------------------


def test_schema_violation_is_returned_as_error_observation(tmp_path: Path):
    tool = _FixtureEchoTool()
    script = (
        (_make_call("echo", "{}"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": tool},
        user_inputs=("call echo",),
        tmp_path=tmp_path,
    )

    assert result.malformed_argument_count == 1
    assert result.consecutive_malformed_streak == 1
    assert result.tool_invocation_count == 0
    assert "pipy v" in stderr  # chrome present


# --------------------- three consecutive malformed = fatal ------------------


def test_three_consecutive_malformed_turns_are_fatal(tmp_path: Path):
    script = (
        (_make_call("echo", "{}"),),
        (_make_call("echo", "{}"),),
        (_make_call("echo", "{}"),),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": _FixtureEchoTool()},
        user_inputs=("call missing",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.FAILED
    assert result.exit_code == 1
    assert result.error_type == "NativeToolLoopMalformedFatal"
    assert result.malformed_argument_count == 3
    assert result.consecutive_malformed_streak == 3
    assert "3 consecutive malformed tool calls" in stderr


def test_three_malformed_in_one_response_are_fatal(tmp_path: Path):
    script = (
        (
            _make_call("echo", "{}", correlation_id="a"),
            _make_call("echo", "{}", correlation_id="b"),
            _make_call("echo", "{}", correlation_id="c"),
        ),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": _FixtureEchoTool()},
        user_inputs=("call three missing",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.FAILED
    assert result.malformed_argument_count == 3
    assert "3 consecutive malformed tool calls" in stderr


def test_malformed_fatal_result_keeps_legacy_zero_image_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    (tmp_path / "shot.png").write_bytes(image_bytes)
    provider = _UsageScriptProvider(
        (
            (
                None,
                tuple(
                    _make_call("echo", "{}", correlation_id=f"malformed-{index}")
                    for index in range(1, 4)
                ),
            ),
        )
    )

    result = NativeToolReplSession(
        provider=provider,
        tool_registry={"echo": _FixtureEchoTool()},
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("describe @image:shot.png\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert len(provider.requests) == 1
    assert len(provider.requests[0].attachments) == 1
    assert provider.requests[0].attachments[0].byte_count == len(image_bytes)
    assert result.status is HarnessStatus.FAILED
    assert result.malformed_argument_count == 3
    assert result.image_attachment_count == 0
    assert result.image_attachment_loaded_count == 0
    assert result.image_attachment_failed_count == 0


def test_malformed_streak_persists_across_accepted_runs_until_fatal(
    tmp_path: Path,
) -> None:
    malformed_calls = tuple(
        _make_call("echo", "{}", correlation_id=f"malformed-{index}")
        for index in range(1, 4)
    )
    provider = _UsageScriptProvider(
        (
            (None, (malformed_calls[0],)),
            (None, ()),
            (None, (malformed_calls[1],)),
            (None, ()),
            (None, (malformed_calls[2],)),
        )
    )
    canonical = _CollectingAgentEventSink()

    result = NativeToolReplSession(
        provider=provider,
        tool_registry={"echo": _FixtureEchoTool()},
        agent_event_sink=canonical,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("first\nsecond\nthird\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert provider.call_index == 5
    assert result.status is HarnessStatus.FAILED
    assert result.exit_code == 1
    assert result.user_turn_count == 3
    assert result.tool_invocation_count == 0
    assert result.malformed_argument_count == 3
    assert result.consecutive_malformed_streak == 3
    assert result.error_type == "NativeToolLoopMalformedFatal"
    assert result.error_message == "3 consecutive malformed tool calls"
    assert [
        event.result.outcome
        for event in canonical.events
        if isinstance(event, AgentRunCompleted)
    ] == [
        AgentRunOutcome.SUCCEEDED,
        AgentRunOutcome.SUCCEEDED,
        AgentRunOutcome.FAILED,
    ]


# ---------------------- one success resets the streak -----------------------


def test_one_success_resets_malformed_streak(tmp_path: Path):
    tool = _FixtureEchoTool()
    script = (
        (_make_call("echo", "{}", correlation_id="a"),),
        (_make_call("echo", "{}", correlation_id="b"),),
        (_make_call("echo", '{"text": "hi"}', correlation_id="c"),),
        (_make_call("echo", "{}", correlation_id="d"),),
        (_make_call("echo", "{}", correlation_id="e"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": tool},
        user_inputs=("go",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 1
    assert result.malformed_argument_count == 4
    assert result.consecutive_malformed_streak == 2


def test_tool_execution_error_resets_malformed_streak(tmp_path: Path):
    tool = _FixtureErrorTool()
    script = (
        (_make_call("echo", "{}", correlation_id="a"),),
        (_make_call("echo", "{}", correlation_id="b"),),
        (_make_call("fail", "{}", correlation_id="c"),),
        (_make_call("echo", "{}", correlation_id="d"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": _FixtureEchoTool(), "fail": tool},
        user_inputs=("go",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 1
    assert result.malformed_argument_count == 3
    assert result.consecutive_malformed_streak == 1
    assert "3 consecutive malformed tool calls" not in stderr


# --------------------------- per-turn budget enforcement --------------------


def test_budget_exhausted_emits_observation_without_invoking(tmp_path: Path):
    tool = _FixtureEchoTool()
    script = (
        (_make_call("echo", '{"text": "1"}', correlation_id="a"),),
        (_make_call("echo", '{"text": "2"}', correlation_id="b"),),
        (_make_call("echo", '{"text": "3"}', correlation_id="c"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": tool},
        user_inputs=("go",),
        tmp_path=tmp_path,
        tool_budget=2,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 2
    assert result.budget_exhausted_count == 1
    assert "pipy v" in stderr  # chrome present


def test_inner_iteration_cap_is_tool_budget_plus_two_and_exhaustion_is_nonterminal(
    tmp_path: Path,
) -> None:
    provider = _UsageScriptProvider(
        tuple(
            (
                None,
                (
                    _make_call(
                        "echo",
                        f'{{"text":"{index}"}}',
                        correlation_id=f"budget-{index}",
                    ),
                ),
            )
            for index in range(1, 4)
        )
    )
    canonical = _CollectingAgentEventSink()

    result = NativeToolReplSession(
        provider=provider,
        tool_registry={"echo": _FixtureEchoTool()},
        tool_budget=1,
        agent_event_sink=canonical,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert provider.call_index == 3
    assert result.status is HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 1
    assert result.budget_exhausted_count == 2
    assert [
        event.result.content.value
        for event in canonical.events
        if isinstance(event, ToolCallCompleted)
    ] == [
        "1",
        "tool budget exhausted (limit 1)",
        "tool budget exhausted (limit 1)",
    ]
    assert [
        event.outcome for event in canonical.events if isinstance(event, TurnCompleted)
    ] == [
        AgentTurnOutcome.SUCCEEDED,
        AgentTurnOutcome.SUCCEEDED,
        AgentTurnOutcome.SUCCEEDED,
    ]
    assert [
        event.result.outcome
        for event in canonical.events
        if isinstance(event, AgentRunCompleted)
    ] == [AgentRunOutcome.SUCCEEDED]


# ---------------------- final text printed on stdout -----------------------


def test_final_text_is_printed_when_no_tool_calls(tmp_path: Path):
    script = ((),)
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=script,
        final_text="hello world",
    )
    session = NativeToolReplSession(provider=provider)
    input_stream = io.StringIO("hi\n")
    output_stream = io.StringIO()
    error_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.user_turn_count == 1
    assert "hello world" in output_stream.getvalue()
    stderr = error_stream.getvalue()
    assert "pipy v" in stderr  # startup chrome rendered
    assert "escape interrupt" in stderr


# ---------------- session ends on EOF and stays archive-safe ---------------


def test_session_ends_at_eof_with_zero_turns(tmp_path: Path):
    provider = FakeNativeProvider(supports_tool_calls=True)
    session = NativeToolReplSession(provider=provider)
    input_stream = io.StringIO("")
    output_stream = io.StringIO()
    error_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.user_turn_count == 0
    assert result.tool_invocation_count == 0


def test_native_tool_repl_result_has_only_metadata_fields():
    from dataclasses import fields

    field_names = {field.name for field in fields(NativeToolReplResult)}

    forbidden = {
        "arguments",
        "diff",
        "diffs",
        "file_content",
        "file_contents",
        "model_output",
        "patch",
        "payload",
        "prompt",
        "provider_response",
        "stderr",
        "stdout",
        "tool_payload",
    }
    assert forbidden.isdisjoint(field_names)


def test_compaction_enabled_false_disables_auto_compaction(tmp_path, monkeypatch):
    import pipy_harness.native.tool_loop_session as tls
    from pipy_harness.native.settings import SettingsManager

    # Force the threshold so auto-compaction would fire if enabled.
    monkeypatch.setattr(
        tls,
        "should_compact_agent_history",
        lambda messages, **_kwargs: True,
    )

    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "settings.json").write_text(
        '{"compaction": {"enabled": false}}', encoding="utf-8"
    )
    manager = SettingsManager(
        global_path=tmp_path / "cfg" / "settings.json",
        project_path=tmp_path / ".pipy" / "settings.json",
    )
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        final_text="answer",
    )
    session = NativeToolReplSession(provider=provider, settings_manager=manager)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("one\ntwo\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    # compaction.enabled=false short-circuits the auto-compaction gate, so the
    # "compacted conversation context (auto; ...)" notice never appears.
    assert "compacted conversation context (auto" not in error_stream.getvalue()


def test_compaction_enabled_true_allows_auto_compaction(tmp_path, monkeypatch):
    import pipy_harness.native.tool_loop_session as tls
    from pipy_harness.native.settings import SettingsManager

    monkeypatch.setattr(
        tls,
        "should_compact_agent_history",
        lambda messages, **_kwargs: True,
    )
    manager = SettingsManager(
        global_path=tmp_path / "cfg" / "settings.json",  # missing -> defaults (enabled)
        project_path=tmp_path / ".pipy" / "settings.json",
    )
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="answer")
    session = NativeToolReplSession(provider=provider, settings_manager=manager)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("one\ntwo\nthree\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    # Default compaction.enabled=true: the gate allows auto-compaction to run.
    assert "compacted conversation context (auto" in error_stream.getvalue()


def _scoped_models_state(tmp_path, seen):
    from pipy_harness.native import NativeModelSelection, NativeReplProviderState
    from pipy_harness.native.auth_store import AuthStore
    from pipy_harness.native.catalog_state import ProviderCatalogState
    from pipy_harness.native.repl_state import ModelRuntime

    class _Rec:
        def __init__(self, provider_name, model_id, supports_tool_calls=True):
            self.name = provider_name
            self.model_id = model_id
            self.supports_tool_calls = supports_tool_calls

        def complete(self, request, **_kwargs):
            seen.append((request.provider_name, request.model_id))
            from datetime import UTC, datetime

            now = datetime.now(UTC)
            from pipy_harness.models import HarnessStatus
            from pipy_harness.native.provider import ProviderResult

            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="ok",
            )

    class _RecReplState(NativeReplProviderState):
        """State whose provider build is the recording ``_Rec``.

        Provider construction is otherwise runtime-owned; this double lets the
        scoped-models tests probe/rebind over the catalog availability gate while
        recording any provider turn into ``seen``.
        """

        def provider_for(self, selection):
            return _Rec(selection.provider_name, selection.model_id)

    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "absent.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={"OPENAI_API_KEY": "x", "ANTHROPIC_API_KEY": "x"},
        openai_codex_auth_path=tmp_path / "missing.json",
    )
    return _RecReplState(
        selection=NativeModelSelection("openai", "gpt-5.5"),
        model_runtime=ModelRuntime(catalog=catalog),
        persist_defaults=False,
    )


def test_scoped_models_show_set_clear_and_cycle(tmp_path, monkeypatch):
    from pipy_harness.native.settings import SettingsManager

    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "nd.json"))
    (tmp_path / "cfg").mkdir()
    settings_path = tmp_path / "cfg" / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    manager = SettingsManager(
        global_path=settings_path, project_path=tmp_path / ".pipy" / "settings.json"
    )
    seen: list = []
    state = _scoped_models_state(tmp_path, seen)
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(
        provider=provider, provider_state=state, settings_manager=manager
    )
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(
            "/scoped-models\n"
            "/scoped-models openai/*\n"
            "/scoped-models\n"
            "/scoped-models clear\n"
            "/exit\n"
        ),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    out = error_stream.getvalue()
    assert "scoped models:" in out
    assert "scoped models set: openai/*" in out
    assert "scoped models cleared" in out
    # Persisted to the settings file (set then cleared -> empty list on disk).
    on_disk = json.loads(settings_path.read_text(encoding="utf-8"))
    assert on_disk.get("enabledModels") == []
    # /scoped-models view/set/clear ran no provider turn.
    assert seen == []


def test_scoped_models_next_cycles_and_rebinds_without_provider_turn(
    tmp_path, monkeypatch
):
    from pipy_harness.native.settings import SettingsManager

    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "nd.json"))
    manager = SettingsManager(
        global_path=tmp_path / "cfg" / "settings.json",
        project_path=tmp_path / ".pipy" / "settings.json",
    )
    seen: list = []
    state = _scoped_models_state(tmp_path, seen)
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(
        provider=provider, provider_state=state, settings_manager=manager
    )
    before = state.current_selection().reference
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/scoped-models next\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    after = state.current_selection().reference
    assert after != before  # cycled to a different available model
    assert "selected model" in error_stream.getvalue()
    assert seen == []  # cycling ran no provider turn


def test_scoped_models_write_failure_preserves_settings_and_usage_footer_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipy_harness.native.settings import SettingsManager

    settings_path = tmp_path / "cfg" / "settings.json"
    settings_path.parent.mkdir()
    settings_path.write_text(
        json.dumps(
            {
                "enabledModels": ["openai/*"],
                "lastChangelogVersion": "0.1.0",
            }
        ),
        encoding="utf-8",
    )
    manager = SettingsManager(
        global_path=settings_path,
        project_path=tmp_path / ".pipy" / "settings.json",
    )
    trace: list[str] = []
    footer_usage_flags: list[bool] = []
    diagnostics: list[str] = []

    def fail_write(models: list[str], *, scope: str = "global") -> None:
        del scope
        assert models == ["anthropic/*"]
        trace.append("settings-write")
        raise RuntimeError("disk is read-only")

    def record_diagnostic(
        terminal_ui: ToolLoopTerminalUi | None,
        error_stream: TextIO,
        message: str,
    ) -> None:
        del terminal_ui, error_stream
        trace.append("diagnostic")
        diagnostics.append(message)

    def record_footer(
        self: NativeToolReplSession,
        error_stream: TextIO,
        **kwargs: object,
    ) -> None:
        del self, error_stream
        trace.append("footer")
        footer_usage_flags.append(kwargs.get("usage_snapshot") is not None)

    monkeypatch.setattr(manager, "set_enabled_models", fail_write)
    monkeypatch.setattr(
        NativeToolReplSession, "_emit_diagnostic", staticmethod(record_diagnostic)
    )
    monkeypatch.setattr(NativeToolReplSession, "_print_footer", record_footer)
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="unused")

    result = NativeToolReplSession(
        provider=provider, settings_manager=manager, tool_registry={}
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/scoped-models anthropic/*\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert trace == ["footer", "settings-write", "diagnostic", "footer"]
    assert footer_usage_flags == [True, True]
    assert diagnostics == ["pipy: could not update scoped models: disk is read-only"]
    assert manager.get_enabled_models() == ["openai/*"]
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {
        "enabledModels": ["openai/*"],
        "lastChangelogVersion": "0.1.0",
    }
    assert provider._call_counter[0] == 0
    assert result.user_turn_count == 0
    assert result.tool_invocation_count == 0


def test_model_change_constructs_a_distinct_usage_accumulator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    constructed, pricing_lookups = _capture_usage_construction(monkeypatch)
    seen: list[tuple[str, str]] = []
    state = _scoped_models_state(tmp_path, seen)
    session = NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True), provider_state=state
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/model anthropic/custom-sonnet\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert [
        (provider_name, model_id) for provider_name, model_id, _ in pricing_lookups
    ] == [
        ("fake", "fake-native-bootstrap"),
        ("anthropic", "custom-sonnet"),
    ]
    assert len(constructed) == 2
    assert constructed[0] is not constructed[1]
    assert seen == []


def test_model_command_does_not_append_deferred_model_change_entry(
    tmp_path: Path,
) -> None:
    seen: list[tuple[str, str]] = []
    state = _scoped_models_state(tmp_path, seen)
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "sessions")
    session = NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True),
        provider_state=state,
        native_session=tree,
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/model anthropic/custom-sonnet\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert state.current_selection().reference == "anthropic/custom-sonnet"
    assert not any(isinstance(entry, ModelChangeEntry) for entry in tree.get_branch())


def test_model_command_does_not_dispatch_deferred_extension_model_select(
    tmp_path: Path,
) -> None:
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    proof = tmp_path / "model-select-events.txt"
    (extension_dir / "model_select_observer.py").write_text(
        "from pathlib import Path\n"
        f"PROOF = Path({str(proof)!r})\n"
        "def activate(api):\n"
        "    @api.on('session_start')\n"
        "    def started(event, ctx):\n"
        "        del event, ctx\n"
        "        PROOF.write_text('session-start\\n', encoding='utf-8')\n"
        "    @api.on('model_select')\n"
        "    def selected(event, ctx):\n"
        "        del event, ctx\n"
        "        with PROOF.open('a', encoding='utf-8') as fh:\n"
        "            fh.write('model-select\\n')\n",
        encoding="utf-8",
    )
    seen: list[tuple[str, str]] = []
    state = _scoped_models_state(tmp_path, seen)
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="unused")

    result = NativeToolReplSession(
        provider=provider,
        provider_state=state,
        tool_registry={},
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/model anthropic/custom-sonnet\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    # session_start proves the extension activated and its hooks were live.
    # The successful selection deliberately has no model_select dispatch yet.
    assert proof.read_text(encoding="utf-8").splitlines() == ["session-start"]
    assert state.current_selection().reference == "anthropic/custom-sonnet"
    assert seen == []
    assert provider._call_counter[0] == 0


def test_state_owned_provider_survives_setup_failure_for_the_next_run(
    tmp_path: Path,
) -> None:
    seen: list[tuple[str, str]] = []
    state = _scoped_models_state(tmp_path, seen)
    session = NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True),
        provider_state=state,
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/model anthropic/custom-sonnet\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    assert session.provider_port.name == "anthropic"
    assert "provider" not in vars(session)

    session.tool_filter_options = ToolFilterOptions(exclude=("missing",))
    with pytest.raises(ValueError, match="unknown tool name"):
        session.run(
            workspace_root=tmp_path,
            input_stream=io.StringIO("ignored\n"),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )

    session.tool_filter_options = ToolFilterOptions.empty()
    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("after failure\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert seen == [("anthropic", "custom-sonnet")]


def test_static_settings_projection_uses_the_state_owned_provider(
    tmp_path: Path,
) -> None:
    provider = FakeNativeProvider(supports_tool_calls=True)
    session = NativeToolReplSession(provider=provider)

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/settings\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert session.provider_port is provider
    assert "provider" not in vars(session)


def test_auth_change_constructs_a_distinct_usage_accumulator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    constructed, pricing_lookups = _capture_usage_construction(monkeypatch)
    seen: list[tuple[str, str]] = []
    state = _scoped_models_state(tmp_path, seen)
    session = NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True), provider_state=state
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/logout openai\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert [
        (provider_name, model_id) for provider_name, model_id, _ in pricing_lookups
    ] == [
        ("fake", "fake-native-bootstrap"),
        ("openai", "gpt-5.5"),
    ]
    assert len(constructed) == 2
    assert constructed[0] is not constructed[1]
    assert seen == []


def test_reload_rereads_edited_settings_without_provider_turn(tmp_path, monkeypatch):
    from pipy_harness.native.settings import SettingsManager

    monkeypatch.delenv("PIPY_THEME", raising=False)
    (tmp_path / "cfg").mkdir()
    settings_path = tmp_path / "cfg" / "settings.json"
    settings_path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    manager = SettingsManager(
        global_path=settings_path, project_path=tmp_path / ".pipy" / "settings.json"
    )
    # Edit the file after the manager loaded the original value.
    settings_path.write_text(json.dumps({"theme": "ocean"}), encoding="utf-8")

    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(provider=provider, settings_manager=manager)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/settings\n/reload\n/settings\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    out = error_stream.getvalue()
    # First /settings shows the originally-loaded theme; after /reload the second
    # /settings reflects the edited file.
    assert "theme: dark" in out.split("reloaded settings")[0]
    assert "theme: ocean" in out.split("reloaded settings")[1]
    assert "reloaded settings, keybindings, and resources." in out
    # /reload and /settings ran no provider turn.
    assert provider._call_counter[0] == 0


def test_reload_malformed_settings_keeps_prior_and_warns(tmp_path):
    from pipy_harness.native.settings import SettingsManager

    (tmp_path / "cfg").mkdir()
    settings_path = tmp_path / "cfg" / "settings.json"
    settings_path.write_text(json.dumps({"theme": "ocean"}), encoding="utf-8")
    manager = SettingsManager(
        global_path=settings_path, project_path=tmp_path / ".pipy" / "settings.json"
    )
    settings_path.write_text("{broken", encoding="utf-8")
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(provider=provider, settings_manager=manager)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/reload\n/settings\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    out = error_stream.getvalue()
    assert "kept prior global settings" in out
    # Prior good theme survives the malformed reload.
    assert "theme: ocean" in out


def test_reload_refreshes_extension_entry_renderers(
    tmp_path: Path, monkeypatch
) -> None:
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    marker = extension_dir / "renderer_prefix.txt"
    extension_file = extension_dir / "renderer_reload.py"
    extension_file.write_text(
        "from pathlib import Path\n"
        "def activate(api):\n"
        "    marker = Path(__file__).with_name('renderer_prefix.txt')\n"
        "    prefix = marker.read_text(encoding='utf-8') if marker.exists() else 'old'\n"
        "    from pipy_harness.extensions import lines_component\n"
        "    api.register_entry_renderer('card', lambda entry, ctx, prefix=prefix: lines_component([prefix + ':' + entry['data']['title']]))\n"
        "    def card(ctx, args):\n"
        "        ctx.append_entry('card', {'title': args})\n"
        "    def flip(ctx, args):\n"
        "        marker.write_text('new', encoding='utf-8')\n"
        "    api.register_command('card', 'card', card)\n"
        "    api.register_command('flip-renderer', 'flip renderer', flip)\n",
        encoding="utf-8",
    )
    provider = FakeNativeProvider(supports_tool_calls=True)
    session = NativeToolReplSession(provider=provider, tool_registry={})
    terminal_stream = _TtyBuffer()
    terminal_ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, io.StringIO()),
        terminal_stream=cast(TextIO, terminal_stream),
        cwd=tmp_path,
    )
    queued = ["/card one", "/flip-renderer", "/reload", "/card two", "/exit"]

    def _read_line(self, prompt_label, *, footer=None):
        del self, prompt_label, footer
        return queued.pop(0) if queued else ""

    monkeypatch.setattr(ToolLoopTerminalUi, "read_line", _read_line)
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kw: (
            terminal_ui
        ),
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=cast(TextIO, io.StringIO()),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    rendered_blocks = "\n".join(
        line for _kind, lines in terminal_ui.custom_entry_blocks() for line in lines
    )
    assert marker.read_text(encoding="utf-8") == "new"
    assert "new:one" in rendered_blocks
    assert "new:two" in rendered_blocks
    assert "old:one" not in rendered_blocks
    assert "old:two" not in rendered_blocks


def test_reload_fires_session_start_reload_for_new_extension_generation(
    tmp_path: Path,
) -> None:
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    proof = tmp_path / "session_start_reasons.txt"
    extension_file = extension_dir / "reload_lifecycle.py"
    extension_file.write_text(
        "from pathlib import Path\n"
        "def activate(api):\n"
        f"    proof = Path({str(proof)!r})\n"
        "    marker = Path(__file__).with_name('reload_marker.txt')\n"
        "    generation = marker.read_text(encoding='utf-8') if marker.exists() else 'initial'\n"
        "    @api.on('session_start')\n"
        "    def started(event, ctx):\n"
        "        with proof.open('a', encoding='utf-8') as fh:\n"
        "            fh.write((event.reason or '') + ':' + generation + '\\n')\n"
        "        if event.reason == 'reload':\n"
        "            ctx.ui.notify('reload-session-start:' + generation)\n"
        "    def flip(ctx, args):\n"
        "        marker.write_text('reloaded', encoding='utf-8')\n"
        "    api.register_command('flip-lifecycle', 'flip lifecycle marker', flip)\n",
        encoding="utf-8",
    )
    provider = FakeNativeProvider(supports_tool_calls=True)
    session = NativeToolReplSession(provider=provider, tool_registry={})
    error_stream = io.StringIO()

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/flip-lifecycle\n/reload\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    assert proof.read_text(encoding="utf-8").splitlines() == [
        "startup:initial",
        "reload:reloaded",
    ]
    assert "reload-session-start:reloaded" in error_stream.getvalue()
    assert provider._call_counter[0] == 0


class _TtyBuffer:
    """Minimal TTY-like stream so a real ``terminal_ui`` is built for a run."""

    def __init__(self) -> None:
        self._buffer = io.StringIO()

    def write(self, text: str) -> int:
        return self._buffer.write(text)

    def flush(self) -> None:
        self._buffer.flush()

    def isatty(self) -> bool:
        return True

    def getvalue(self) -> str:
        return self._buffer.getvalue()


def test_reopened_session_replays_extension_custom_entries_live_only(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    monkeypatch.setenv("COLUMNS", "101")
    monkeypatch.setenv("LINES", "31")
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "cards.py").write_text(
        "from pipy_harness.extensions import lines_component\n"
        "def activate(api):\n"
        "    api.register_entry_renderer('plain-card', lambda entry, ctx: lines_component(['PLAIN:' + entry['data']['title']]))\n"
        "    def render_rich(entry, ctx):\n"
        "        body = f\"RICH:{entry['data']['title']}:expanded={ctx.expanded}:width={ctx.width}:theme={ctx.theme is not None}\"\n"
        "        text = ctx.theme.fg('accent', body) if ctx.theme else body\n"
        "        return lines_component([text])\n"
        "    api.register_entry_renderer('rich-card', render_rich)\n",
        encoding="utf-8",
    )
    session_dir = tmp_path / "sessions"
    tree = NativeSessionTree.create(tmp_path, session_dir=session_dir)
    plain = tree.append_custom("plain-card", {"title": "ROOT"})
    tree.append_custom("rich-card", {"title": "OFF_BRANCH"})
    tree.branch(plain.id)
    tree.append_custom("rich-card", {"title": "ACTIVE"})
    tree.append_custom("unknown-card", {"title": "FALLBACK"})
    tree.append_custom_message("legacy-card", "LEGACY_SHOW", display=True)
    tree.append_custom_message("legacy-card", "LEGACY_HIDE", display=False)
    assert tree.path is not None
    before = tree.path.read_text(encoding="utf-8")

    reopened = NativeSessionTree.open(tree.path)
    terminal_stream = _TtyBuffer()
    terminal_ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, io.StringIO()),
        terminal_stream=cast(TextIO, terminal_stream),
        cwd=tmp_path,
    )
    queued = [""]

    def _read_line(self, prompt_label, *, footer=None):
        del self, prompt_label, footer
        return queued.pop(0) if queued else ""

    monkeypatch.setattr(ToolLoopTerminalUi, "read_line", _read_line)
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kw: (
            terminal_ui
        ),
    )
    provider = FakeNativeProvider(supports_tool_calls=True)
    session = NativeToolReplSession(
        provider=provider,
        tool_registry={},
        native_session=reopened,
        resume_context=ResumeContext(
            prior_session_id="parent-session",
            prior_provider_name="fake",
            prior_model_id="fake-native-bootstrap",
            prior_turn_count=1,
            prior_workspace_hash="HASH",
            prior_started_at="2026-06-22T00:00:00+00:00",
            prior_ended_at="2026-06-22T00:01:00+00:00",
            prior_summary=None,
        ),
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=cast(TextIO, io.StringIO()),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    committed_frame = "\n".join(
        terminal_ui.render_lines(width=72, height=24, pad=False)
    )
    history = terminal_ui._history_blocks

    notice_index = next(i for i, (kind, _) in enumerate(history) if kind == "notice")
    first_custom_index = next(
        i for i, (kind, _) in enumerate(history) if kind.startswith("custom")
    )
    assert notice_index < first_custom_index
    assert any(kind == "custom_message_custom" for kind, _ in history)
    assert "PLAIN:ROOT" in committed_frame
    assert "RICH:ACTIVE" in committed_frame
    assert "expanded=False:width=101:theme=True" in committed_frame
    assert "FALLBACK" not in committed_frame
    assert "LEGACY_SHOW" in committed_frame
    assert "OFF_BRANCH" not in committed_frame
    assert "LEGACY_HIDE" not in committed_frame
    assert tree.path.read_text(encoding="utf-8") == before

    terminal_ui.tools_expanded = True
    terminal_ui.rerender_custom_messages()
    rerendered_frame = "\n".join(
        terminal_ui.render_lines(width=101, height=24, pad=False)
    )
    assert "RICH:ACTIVE:expanded=True:width=101:theme=True" in rerendered_frame


def test_rich_message_renderer_styles_scrollback_and_does_not_leak(
    tmp_path: Path, monkeypatch
) -> None:
    # Product path: a 2-arg component message renderer routes through the live
    # terminal UI via the SGR-preserving ``custom_message_custom`` block (NOT
    # the sanitizing/[label] ``custom`` block), the body shows in the committed
    # frame, and the body never leaks into the archive-safe output stream.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "card.py").write_text(
        "from pipy_harness.extensions import lines_component\n"
        "def activate(api):\n"
        "    def render(entry, ctx):\n"
        "        text = ctx.theme.fg('accent', entry['data']['title']) if ctx.theme else entry['data']['title']\n"
        "        return lines_component([text])\n"
        "    api.register_entry_renderer('card', render)\n"
        "    def cmd(ctx, args):\n"
        "        ctx.append_entry('card', {'title': 'SECRET_TITLE'})\n"
        "    api.register_command('mkcard', 'make a card', cmd)\n",
        encoding="utf-8",
    )
    terminal_stream = _TtyBuffer()
    terminal_ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, io.StringIO()),
        terminal_stream=cast(TextIO, terminal_stream),
        cwd=tmp_path,
    )
    # Feed commands without driving raw-mode reads (StringIO has no usable fd):
    # ``read_line`` returns the queued line, then "" to end the loop at EOF.
    queued = ["/mkcard\n", ""]

    def _read_line(self, prompt_label, *, footer=None):
        del self, prompt_label, footer
        return queued.pop(0) if queued else ""

    monkeypatch.setattr(ToolLoopTerminalUi, "read_line", _read_line)
    provider = FakeNativeProvider(supports_tool_calls=True)
    session = NativeToolReplSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kw: (
            terminal_ui
        ),
    )
    output_stream = io.StringIO()

    session.run(
        workspace_root=tmp_path,
        input_stream=cast(TextIO, io.StringIO()),
        output_stream=output_stream,
        error_stream=io.StringIO(),
    )

    committed_frame = "\n".join(
        terminal_ui.render_lines(width=72, height=20, pad=False)
    )
    archive_text = output_stream.getvalue()

    # Styled route => SGR-safe ``custom_message_custom`` block, not plain custom.
    assert any(k == "custom_message_custom" for k, _ in terminal_ui._history_blocks)
    assert not any(k == "custom" for k, _ in terminal_ui._history_blocks)
    # Body rendered live in the committed scrollback.
    assert "SECRET_TITLE" in committed_frame
    # No forced ``[card]`` label injected by the component path (judgment 2).
    assert "[card]" not in committed_frame
    # The body never reaches the archive-safe (metadata-only) output stream.
    assert "SECRET_TITLE" not in archive_text


def test_reload_rebinds_active_extension_provider_factory(tmp_path):
    marker = tmp_path / "marker.txt"
    schema_marker = tmp_path / "schemas.json"
    marker.write_text("before", encoding="utf-8")
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "reload_provider.py").write_text(
        "import json\n"
        "from datetime import datetime, timezone\n"
        "from pathlib import Path\n"
        "from pipy_harness.extensions import ExtensionProvider\n"
        "from pipy_harness.models import HarnessStatus\n"
        "from pipy_harness.native.models import ProviderResult\n"
        f"MARKER = Path({str(marker)!r})\n"
        f"SCHEMA_MARKER = Path({str(schema_marker)!r})\n"
        "class _Port:\n"
        "    name = 'reloadext'\n"
        "    supports_tool_calls = True\n"
        "    def __init__(self, ctx):\n"
        "        self.model_id = ctx.model_id\n"
        "        self.final_text = MARKER.read_text(encoding='utf-8')\n"
        "    def complete(self, request, **kwargs):\n"
        "        SCHEMA_MARKER.write_text(json.dumps([\n"
        "            tool.input_schema for tool in request.available_tools\n"
        "        ]), encoding='utf-8')\n"
        "        now = datetime(2026, 6, 18, tzinfo=timezone.utc)\n"
        "        return ProviderResult(status=HarnessStatus.SUCCEEDED,\n"
        "            provider_name=self.name, model_id=self.model_id,\n"
        "            started_at=now, ended_at=now,\n"
        "            final_text=self.final_text, tool_calls=())\n"
        "def _flip(ctx, args):\n"
        "    MARKER.write_text('after', encoding='utf-8')\n"
        "def activate(api):\n"
        "    api.register_command('flip-provider', 'flip provider marker', _flip)\n"
        "    api.register_provider(ExtensionProvider(name='reloadext',\n"
        "        default_model='m', models=('m',), factory=lambda ctx: _Port(ctx)))\n",
        encoding="utf-8",
    )
    providers, unregistered = load_extension_provider_contributions(
        tmp_path,
        include_workspace_defaults=True,
        reserved_command_names=extension_reserved_command_names(),
        reserved_tool_names=extension_reserved_tool_names(),
        diagnostic=lambda message: pytest.fail(message),
    )
    catalog_state = ProviderCatalogState(models_json_path=tmp_path / "absent.json")
    catalog_state.set_extension_provider_contributions(providers, unregistered)
    state = NativeReplProviderState(
        selection=NativeModelSelection("reloadext", "m"),
        model_runtime=ModelRuntime(catalog=catalog_state),
        persist_defaults=False,
    )
    provider = state.current_provider()
    session = NativeToolReplSession(
        provider=provider,
        provider_state=state,
        tool_registry={"echo": _FixtureEchoTool()},
    )
    output_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/flip-provider\n/reload\nhi\n/exit\n"),
        output_stream=output_stream,
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert "after" in output_stream.getvalue()
    assert "before" not in output_stream.getvalue()
    schemas = json.loads(schema_marker.read_text(encoding="utf-8"))
    assert schemas == [
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "maxLength": 1024},
            },
            "required": ["text"],
            "additionalProperties": False,
        }
    ]


def test_reload_tool_capability_fallback_constructs_a_distinct_usage_accumulator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    constructed, pricing_lookups = _capture_usage_construction(monkeypatch)
    marker = tmp_path / "tool-capability.txt"
    marker.write_text("enabled", encoding="utf-8")
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "capability_provider.py").write_text(
        "from datetime import datetime, timezone\n"
        "from pathlib import Path\n"
        "from pipy_harness.extensions import ExtensionProvider\n"
        "from pipy_harness.models import HarnessStatus\n"
        "from pipy_harness.native.models import ProviderResult\n"
        f"MARKER = Path({str(marker)!r})\n"
        "class _Port:\n"
        "    name = 'capabilityext'\n"
        "    model_id = 'active'\n"
        "    def __init__(self):\n"
        "        self.supports_tool_calls = MARKER.read_text(encoding='utf-8') == 'enabled'\n"
        "    def complete(self, request, **kwargs):\n"
        "        now = datetime(2026, 7, 19, tzinfo=timezone.utc)\n"
        "        return ProviderResult(status=HarnessStatus.SUCCEEDED,\n"
        "            provider_name=self.name, model_id=self.model_id,\n"
        "            started_at=now, ended_at=now, final_text='active', tool_calls=())\n"
        "class _FallbackPort(_Port):\n"
        "    name = 'fallbackext'\n"
        "    model_id = 'fallback'\n"
        "    supports_tool_calls = True\n"
        "    def __init__(self):\n"
        "        pass\n"
        "def _disable(ctx, args):\n"
        "    MARKER.write_text('disabled', encoding='utf-8')\n"
        "def activate(api):\n"
        "    api.register_command('disable-provider-tools', 'disable tools', _disable)\n"
        "    api.register_provider(ExtensionProvider(name='capabilityext',\n"
        "        default_model='active', models=('active',), factory=lambda ctx: _Port()))\n"
        "    api.register_provider(ExtensionProvider(name='fallbackext',\n"
        "        default_model='fallback', models=('fallback',), factory=lambda ctx: _FallbackPort()))\n",
        encoding="utf-8",
    )
    providers, unregistered = load_extension_provider_contributions(
        tmp_path,
        include_workspace_defaults=True,
        reserved_command_names=extension_reserved_command_names(),
        reserved_tool_names=extension_reserved_tool_names(),
        diagnostic=lambda message: pytest.fail(message),
    )
    catalog_state = ProviderCatalogState(models_json_path=tmp_path / "absent.json")
    catalog_state.set_extension_provider_contributions(providers, unregistered)
    state = NativeReplProviderState(
        selection=NativeModelSelection("capabilityext", "active"),
        model_runtime=ModelRuntime(catalog=catalog_state),
        persist_defaults=False,
    )
    session = NativeToolReplSession(
        provider=state.current_provider(), provider_state=state
    )
    error_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/disable-provider-tools\n/reload\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert state.current_selection().reference != "capabilityext/active"
    assert "no longer supports tool calls after reload" in error_stream.getvalue()
    assert [
        (provider_name, model_id) for provider_name, model_id, _ in pricing_lookups
    ] == [
        ("capabilityext", "active"),
        (
            state.current_selection().provider_name,
            state.current_selection().model_id,
        ),
    ]
    assert len(constructed) == 2
    assert constructed[0] is not constructed[1]


def test_reload_falls_back_when_shadowing_extension_provider_is_removed(
    tmp_path,
    monkeypatch,
):
    constructed, pricing_lookups = _capture_usage_construction(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    extension_file = extension_dir / "shadow_openai.py"
    extension_file.write_text(
        "from datetime import datetime, timezone\n"
        "from pathlib import Path\n"
        "from pipy_harness.extensions import ExtensionProvider\n"
        "from pipy_harness.models import HarnessStatus\n"
        "from pipy_harness.native.models import ProviderResult\n"
        f"EXTENSION_FILE = Path({str(extension_file)!r})\n"
        "class _Port:\n"
        "    name = 'openai'\n"
        "    model_id = 'ext'\n"
        "    supports_tool_calls = True\n"
        "    def complete(self, request, **kwargs):\n"
        "        now = datetime(2026, 6, 18, tzinfo=timezone.utc)\n"
        "        return ProviderResult(status=HarnessStatus.SUCCEEDED,\n"
        "            provider_name=self.name, model_id=self.model_id,\n"
        "            started_at=now, ended_at=now,\n"
        "            final_text='removed extension provider was used', tool_calls=())\n"
        "def _remove(ctx, args):\n"
        "    EXTENSION_FILE.unlink()\n"
        "def activate(api):\n"
        "    api.register_command('remove-shadow', 'remove shadow provider', _remove)\n"
        "    api.register_provider(ExtensionProvider(name='openai',\n"
        "        default_model='ext', models=('ext',), factory=lambda ctx: _Port()))\n",
        encoding="utf-8",
    )
    providers, unregistered = load_extension_provider_contributions(
        tmp_path,
        include_workspace_defaults=True,
        reserved_command_names=extension_reserved_command_names(),
        reserved_tool_names=extension_reserved_tool_names(),
        diagnostic=lambda message: pytest.fail(message),
    )
    catalog_state = ProviderCatalogState(models_json_path=tmp_path / "absent.json")
    catalog_state.set_extension_provider_contributions(providers, unregistered)
    state = NativeReplProviderState(
        selection=NativeModelSelection("openai", "ext"),
        model_runtime=ModelRuntime(catalog=catalog_state),
        persist_defaults=False,
    )
    session = NativeToolReplSession(
        provider=state.current_provider(),
        provider_state=state,
    )
    error_stream = io.StringIO()
    output_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/remove-shadow\n/reload\n/exit\n"),
        output_stream=output_stream,
        error_stream=error_stream,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert state.current_selection().reference != "openai/ext"
    assert session.provider_port.name == state.current_selection().provider_name
    assert "active model disappeared on reload" in error_stream.getvalue()
    assert "removed extension provider was used" not in output_stream.getvalue()
    assert [
        (provider_name, model_id) for provider_name, model_id, _ in pricing_lookups
    ] == [
        ("openai", "ext"),
        (
            state.current_selection().provider_name,
            state.current_selection().model_id,
        ),
    ]
    assert len(constructed) == 2
    assert constructed[0] is not constructed[1]


def test_reload_fail_closes_removed_extension_provider_when_no_fallback(
    tmp_path,
):
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    extension_file = extension_dir / "unique_provider.py"
    extension_file.write_text(
        "from datetime import datetime, timezone\n"
        "from pathlib import Path\n"
        "from pipy_harness.extensions import ExtensionProvider\n"
        "from pipy_harness.models import HarnessStatus\n"
        "from pipy_harness.native.models import ProviderResult\n"
        f"EXTENSION_FILE = Path({str(extension_file)!r})\n"
        "class _Port:\n"
        "    name = 'uniqueext'\n"
        "    model_id = 'm'\n"
        "    supports_tool_calls = True\n"
        "    def complete(self, request, **kwargs):\n"
        "        now = datetime(2026, 6, 18, tzinfo=timezone.utc)\n"
        "        return ProviderResult(status=HarnessStatus.SUCCEEDED,\n"
        "            provider_name=self.name, model_id=self.model_id,\n"
        "            started_at=now, ended_at=now,\n"
        "            final_text='removed unique extension provider was used',\n"
        "            tool_calls=())\n"
        "def _remove(ctx, args):\n"
        "    EXTENSION_FILE.unlink()\n"
        "def activate(api):\n"
        "    api.register_command('remove-unique-provider', 'remove provider', _remove)\n"
        "    api.register_provider(ExtensionProvider(name='uniqueext',\n"
        "        default_model='m', models=('m',), factory=lambda ctx: _Port()))\n",
        encoding="utf-8",
    )
    providers, unregistered = load_extension_provider_contributions(
        tmp_path,
        include_workspace_defaults=True,
        reserved_command_names=extension_reserved_command_names(),
        reserved_tool_names=extension_reserved_tool_names(),
        diagnostic=lambda message: pytest.fail(message),
    )
    catalog_state = ProviderCatalogState(
        models_json_path=tmp_path / "absent.json",
        env={},
        openai_codex_auth_path=tmp_path / "missing-codex.json",
    )
    catalog_state.set_extension_provider_contributions(providers, unregistered)
    state = NativeReplProviderState(
        selection=NativeModelSelection("uniqueext", "m"),
        model_runtime=ModelRuntime(catalog=catalog_state),
        persist_defaults=False,
    )
    session = NativeToolReplSession(
        provider=state.current_provider(),
        provider_state=state,
    )
    error_stream = io.StringIO()
    output_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/remove-unique-provider\n/reload\nhi\n/exit\n"),
        output_stream=output_stream,
        error_stream=error_stream,
    )

    stderr = error_stream.getvalue()
    assert result.status == HarnessStatus.SUCCEEDED
    assert state.current_selection().reference == "uniqueext/m"
    assert "no available tool-capable fallback was found" in stderr
    assert "ProviderUnavailableAfterReload" in stderr
    assert "removed unique extension provider was used" not in output_stream.getvalue()


def test_changelog_command_renders_without_provider_turn(tmp_path):
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(provider=provider)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/changelog\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    assert "What's New" in error_stream.getvalue()
    assert provider._call_counter[0] == 0


def test_headless_command_kernel_classifies_supported_local_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.coding.session_controller as controller_module
    from pipy_harness.native.agent import ProductContent
    from pipy_harness.native.coding.commands import CodingCommandOutcome

    # Built-in classification now lives in the headless controller, so intercept
    # it there rather than at the (superseded) monolith import site.
    original_classifier = controller_module.classify_coding_command
    classified: list[ProductContent] = []

    def record_classification(content: ProductContent) -> CodingCommandOutcome:
        classified.append(content)
        return original_classifier(content)

    monkeypatch.setattr(
        controller_module, "classify_coding_command", record_classification
    )
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="unused")
    error_stream = io.StringIO()

    NativeToolReplSession(provider=provider).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(
            "\n/hotkeys\n/changelog\n/copy\n/session\n"
            "/compact\n/name\n/name classified value\n"
            "/model openai/gpt-5.5\n/scoped-models clear\n"
            "/login openai-codex\n/logout openai-codex\n/new\n"
            "/tree\n/tree select 1\n/resume\n/resume named\n"
            "/fork\n/fork 1\n/clone\n/trust   \n"
            "/export\n/export artifacts/session.html\n"
            "/import artifacts/session.jsonl --yes\n/exit\n"
        ),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    assert [content.value for content in classified] == [
        "",
        "/hotkeys",
        "/changelog",
        "/copy",
        "/session",
        "/compact",
        "/name",
        "/name classified value",
        "/model openai/gpt-5.5",
        "/scoped-models clear",
        "/login openai-codex",
        "/logout openai-codex",
        "/new",
        "/tree",
        "/tree select 1",
        "/resume",
        "/resume named",
        "/fork",
        "/fork 1",
        "/clone",
        "/trust",
        "/export",
        "/export artifacts/session.html",
        "/import artifacts/session.jsonl --yes",
        "/exit",
    ]
    assert "What's New" in error_stream.getvalue()
    assert provider._call_counter[0] == 0


def test_provider_control_commands_use_only_typed_interpreter_dispatch() -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    module_path = loop_module.__file__
    assert module_path is not None
    source = Path(module_path).read_text(encoding="utf-8")
    for command in ("/model", "/scoped-models", "/login", "/logout"):
        assert f'if command_text == "{command}"' not in source


def test_new_command_uses_only_typed_interpreter_dispatch() -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    module_path = loop_module.__file__
    assert module_path is not None
    source = Path(module_path).read_text(encoding="utf-8")
    assert 'if command_text == "/new"' not in source


def test_session_tree_command_uses_only_typed_interpreter_dispatch() -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    module_path = loop_module.__file__
    assert module_path is not None
    source = Path(module_path).read_text(encoding="utf-8")
    assert 'if command_text == "/tree"' not in source
    assert 'command_text.startswith("/tree ")' not in source


def test_session_resume_command_uses_only_typed_interpreter_dispatch() -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    module_path = loop_module.__file__
    assert module_path is not None
    source = Path(module_path).read_text(encoding="utf-8")
    assert 'if command_text == "/resume"' not in source
    assert 'command_text.startswith("/resume ")' not in source


def test_session_fork_and_clone_commands_use_only_typed_interpreter_dispatch() -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    module_path = loop_module.__file__
    assert module_path is not None
    source = Path(module_path).read_text(encoding="utf-8")
    assert 'if command_text == "/fork"' not in source
    assert 'command_text.startswith("/fork ")' not in source
    assert 'if command_text == "/clone"' not in source


def test_trust_command_uses_only_typed_interpreter_dispatch() -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    module_path = loop_module.__file__
    assert module_path is not None
    source = Path(module_path).read_text(encoding="utf-8")
    assert 'if command_text == "/trust"' not in source


def test_export_command_uses_only_typed_interpreter_dispatch() -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    module_path = loop_module.__file__
    assert module_path is not None
    source = Path(module_path).read_text(encoding="utf-8")
    assert 'if command_text == "/export"' not in source
    assert 'command_text.startswith("/export ")' not in source


def test_import_command_uses_only_typed_interpreter_dispatch() -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    module_path = loop_module.__file__
    assert module_path is not None
    source = Path(module_path).read_text(encoding="utf-8")
    assert 'if command_text == "/import"' not in source
    assert 'command_text.startswith("/import ")' not in source


def test_new_command_applies_standard_footer_without_provider_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    footer_kwargs: list[dict[str, object]] = []

    def record_footer(*_args: object, **kwargs: object) -> None:
        footer_kwargs.append(kwargs)

    monkeypatch.setattr(NativeToolReplSession, "_print_footer", record_footer)
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="unused")

    NativeToolReplSession(provider=provider).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/new\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert len(footer_kwargs) == 2
    assert footer_kwargs[0].get("usage_snapshot") is not None
    assert footer_kwargs[1].get("usage_snapshot") is None
    assert provider._call_counter[0] == 0


def test_tree_commands_apply_standard_footer_without_provider_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    footer_kwargs: list[dict[str, object]] = []

    def record_footer(*_args: object, **kwargs: object) -> None:
        footer_kwargs.append(kwargs)

    monkeypatch.setattr(NativeToolReplSession, "_print_footer", record_footer)
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="unused")

    NativeToolReplSession(provider=provider).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/tree\n/tree filter user-only\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert len(footer_kwargs) == 3
    assert footer_kwargs[0].get("usage_snapshot") is not None
    assert all(kwargs.get("usage_snapshot") is None for kwargs in footer_kwargs[1:])
    assert provider._call_counter[0] == 0


def test_resume_commands_apply_standard_footer_without_provider_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    footer_kwargs: list[dict[str, object]] = []

    def record_footer(*_args: object, **kwargs: object) -> None:
        footer_kwargs.append(kwargs)

    monkeypatch.setattr(NativeToolReplSession, "_print_footer", record_footer)
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="unused")

    NativeToolReplSession(provider=provider).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/resume\n/resume named\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert len(footer_kwargs) == 3
    assert footer_kwargs[0].get("usage_snapshot") is not None
    assert all(kwargs.get("usage_snapshot") is None for kwargs in footer_kwargs[1:])
    assert provider._call_counter[0] == 0


def test_fork_and_clone_commands_apply_standard_footer_without_provider_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    footer_kwargs: list[dict[str, object]] = []

    def record_footer(*_args: object, **kwargs: object) -> None:
        footer_kwargs.append(kwargs)

    monkeypatch.setattr(NativeToolReplSession, "_print_footer", record_footer)
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="unused")

    NativeToolReplSession(provider=provider).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/fork\n/fork 1\n/clone\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert len(footer_kwargs) == 4
    assert footer_kwargs[0].get("usage_snapshot") is not None
    assert all(kwargs.get("usage_snapshot") is None for kwargs in footer_kwargs[1:])
    assert provider._call_counter[0] == 0


def test_trust_command_preserves_outer_trim_bubble_and_standard_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    footer_kwargs: list[dict[str, object]] = []
    rendered_user_messages: list[str] = []

    def record_footer(*_args: object, **kwargs: object) -> None:
        footer_kwargs.append(kwargs)

    def record_user_message(_renderer: object, text: str) -> None:
        rendered_user_messages.append(text)

    monkeypatch.setattr(NativeToolReplSession, "_print_footer", record_footer)
    monkeypatch.setattr(
        loop_module._ToolLoopRenderer,
        "render_user_message",
        record_user_message,
    )
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="unused")
    error_stream = io.StringIO()

    NativeToolReplSession(provider=provider).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/trust   \n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    assert rendered_user_messages == ["/trust   ", "/exit"]
    assert "/trust requires the interactive product TUI" in error_stream.getvalue()
    assert len(footer_kwargs) == 2
    assert footer_kwargs[0].get("usage_snapshot") is not None
    assert footer_kwargs[1].get("usage_snapshot") is None
    assert provider._call_counter[0] == 0


def test_provider_control_commands_apply_usage_aware_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    footer_kwargs: list[dict[str, object]] = []

    def record_footer(*_args: object, **kwargs: object) -> None:
        footer_kwargs.append(kwargs)

    monkeypatch.setattr(NativeToolReplSession, "_print_footer", record_footer)
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="unused")

    NativeToolReplSession(provider=provider).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/model\n/scoped-models\n/login\n/logout\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    # The initial frame and all four command refreshes carry the usage snapshot.
    assert len(footer_kwargs) == 5
    assert all(kwargs.get("usage_snapshot") is not None for kwargs in footer_kwargs)
    assert provider._call_counter[0] == 0


def test_headless_command_kernel_exit_emits_no_command_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    footer_calls: list[None] = []

    def record_footer(*_args: object, **_kwargs: object) -> None:
        footer_calls.append(None)

    monkeypatch.setattr(NativeToolReplSession, "_print_footer", record_footer)
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="unused")

    NativeToolReplSession(provider=provider).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    # The sole call is the pre-loop frame; EXIT does not apply STANDARD footer.
    assert footer_calls == [None]
    assert provider._call_counter[0] == 0


def test_startup_changelog_shows_new_entries_on_version_bump(tmp_path):
    from pipy_harness.native.settings import SettingsManager

    (tmp_path / "cfg").mkdir()
    settings_path = tmp_path / "cfg" / "settings.json"
    # A stale lastChangelogVersion forces a bump against the shipped version.
    settings_path.write_text(
        json.dumps({"lastChangelogVersion": "0.0.0"}), encoding="utf-8"
    )
    manager = SettingsManager(
        global_path=settings_path, project_path=tmp_path / ".pipy" / "settings.json"
    )
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(provider=provider, settings_manager=manager)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    out = error_stream.getvalue()
    assert "What's New" in out  # new entries shown at startup
    # The shipped version was recorded so the next run does not re-show.
    on_disk = json.loads(settings_path.read_text(encoding="utf-8"))
    assert on_disk["lastChangelogVersion"] != "0.0.0"


def test_startup_changelog_first_run_records_version_shows_nothing(tmp_path):
    from pipy_harness.native.settings import SettingsManager

    (tmp_path / "cfg").mkdir()
    settings_path = tmp_path / "cfg" / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    manager = SettingsManager(
        global_path=settings_path, project_path=tmp_path / ".pipy" / "settings.json"
    )
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(provider=provider, settings_manager=manager)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    assert "What's New" not in error_stream.getvalue()
    on_disk = json.loads(settings_path.read_text(encoding="utf-8"))
    assert on_disk.get("lastChangelogVersion")  # recorded on first run


# --------- Pi-faithful slash-command set (no deprecation shims) --------------


def _run_local_commands(tmp_path: Path, script: str) -> str:
    """Drive the tool-loop session over a local-command script, return stderr."""

    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(provider=provider)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(script),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    assert provider._call_counter[0] == 0  # local commands run no provider turn
    return error_stream.getvalue()


def test_pipy_only_commands_removed(tmp_path: Path):
    # Pi has no /clear, /status, or /help built-ins; the equivalents are /new,
    # /session, and /hotkeys, which remain canonical and unchanged. The
    # pipy-only aliases are removed outright (no deprecation shims), so each
    # dispatches as an unknown command: no handler runs and no provider turn
    # fires.
    for gone in ("/clear", "/status", "/help"):
        out = _run_local_commands(tmp_path, f"{gone}\n/exit\n")
        assert f"'{gone}' is not handled in tool-loop mode" in out
        # No trace of the old deprecation notices or alias behavior.
        assert "is deprecated" not in out
        assert "supported local commands are /help" not in out


def test_theme_command_removed(tmp_path: Path):
    # Pi has no /theme command: theme selection now lives in the /settings
    # dialog (covered by the settings-dialog theme-row test). Dispatching
    # /theme is therefore an unknown command — no handler runs, the theme is
    # not switched, and no provider turn fires.
    out = _run_local_commands(tmp_path, "/theme\n/exit\n")
    assert "'/theme' is not handled in tool-loop mode" in out
    # It is not advertised as a supported local command, and nothing about the
    # old list/apply behavior remains.
    assert "available:" not in out


def test_trust_command_is_local_and_never_reads_captured_stdin(tmp_path: Path):
    out = _run_local_commands(tmp_path, "/trust\n/exit\n")
    assert "/trust requires the interactive product TUI" in out


def test_tool_filter_options_filter_provider_visible_tools(tmp_path: Path):
    seen: list[tuple[str, ...]] = []

    @dataclass(frozen=True, slots=True)
    class RecordingProvider:
        supports_tool_calls: bool = True
        name: str = "recording"
        model_id: str = "recording-model"

        def complete(
            self,
            request: ProviderRequest,
            *,
            stream_sink: StreamChunkSink | None = None,
            reasoning_sink: StreamChunkSink | None = None,
            cancel_token: CancelToken | None = None,
        ) -> ProviderResult:
            del stream_sink, reasoning_sink, cancel_token
            seen.append(tuple(tool.name for tool in request.available_tools))
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="ok",
                usage={},
                tool_calls=(),
            )

    session = NativeToolReplSession(
        provider=RecordingProvider(),
        tool_registry={"echo": _FixtureEchoTool()},
        tool_filter_options=ToolFilterOptions(allow=("echo",)),
    )
    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert seen == [("echo",)]


def test_unfiltered_tool_visibility_includes_extension_tools_added_by_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # No CLI filter is different from an explicit active-name snapshot: after a
    # reload, newly discovered extension tools must become provider-visible.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    dynamic_tool_file = extension_dir / "dynamic_tool.py"
    (extension_dir / "installer.py").write_text(
        "from pathlib import Path\n"
        f"DYNAMIC_TOOL = Path({str(dynamic_tool_file)!r})\n"
        "def install(ctx, args):\n"
        "    DYNAMIC_TOOL.write_text(\n"
        '        "from pipy_harness.extensions import ExtensionTool, ToolResult\\n"\n'
        '        "def activate(api):\\n"\n'
        '        "    api.register_tool(ExtensionTool(\\n"\n'
        "        \"        name='dynamic_tool', description='added on reload',\\n\"\n"
        "        \"        input_schema={'type': 'object'},\\n\"\n"
        "        \"        handler=lambda ctx, params: ToolResult(content='ok'),\\n\"\n"
        '        "    ))\\n"\n'
        "    )\n"
        "def activate(api):\n"
        "    api.register_command('install-tool', 'install a tool', install)\n",
        encoding="utf-8",
    )
    seen: list[tuple[str, ...]] = []

    @dataclass(frozen=True, slots=True)
    class RecordingProvider:
        supports_tool_calls: bool = True
        name: str = "recording"
        model_id: str = "recording-model"

        def complete(
            self, request: ProviderRequest, **_kwargs: object
        ) -> ProviderResult:
            seen.append(tuple(tool.name for tool in request.available_tools))
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="ok",
                usage={},
                tool_calls=(),
            )

    session = NativeToolReplSession(
        provider=RecordingProvider(), tool_registry={"echo": _FixtureEchoTool()}
    )
    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/install-tool\n/reload\ngo\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert seen == [("echo", "dynamic_tool")]


def test_reload_reports_configured_extension_tool_that_disappeared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    extension_file = extension_dir / "disappearing_tool.py"
    extension_file.write_text(
        "from pathlib import Path\n"
        "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
        f"EXTENSION_FILE = Path({str(extension_file)!r})\n"
        "def remove(ctx, args):\n"
        "    EXTENSION_FILE.unlink()\n"
        "def activate(api):\n"
        "    api.register_command(\n"
        "        'remove-filtered-tool', 'remove filtered tool', remove)\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='disappearing', description='temporary tool',\n"
        "        input_schema={'type': 'object'},\n"
        "        handler=lambda ctx, params: ToolResult(content='ok')))\n",
        encoding="utf-8",
    )
    seen: list[tuple[str, ...]] = []

    @dataclass(frozen=True, slots=True)
    class RecordingProvider:
        supports_tool_calls: bool = True
        name: str = "recording"
        model_id: str = "recording-model"

        def complete(
            self, request: ProviderRequest, **_kwargs: object
        ) -> ProviderResult:
            seen.append(tuple(tool.name for tool in request.available_tools))
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="continued",
                usage={},
                tool_calls=(),
            )

    error_stream = io.StringIO()
    output_stream = io.StringIO()
    session = NativeToolReplSession(
        provider=RecordingProvider(),
        tool_registry={"echo": _FixtureEchoTool()},
        tool_filter_options=ToolFilterOptions(allow=("disappearing",)),
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/remove-filtered-tool\n/reload\nhello\n/exit\n"),
        output_stream=output_stream,
        error_stream=error_stream,
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert seen == [()]
    assert "continued" in output_stream.getvalue()
    assert (
        "pipy: unknown tool name(s): disappearing. Known tools: echo"
        in error_stream.getvalue().splitlines()
    )


def test_tool_filter_options_unknown_name_fails_early(tmp_path: Path):
    session = NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True),
        tool_registry={"echo": _FixtureEchoTool()},
        tool_filter_options=ToolFilterOptions(exclude=("missing",)),
    )

    with pytest.raises(ValueError, match="unknown tool name"):
        session.run(
            workspace_root=tmp_path,
            input_stream=io.StringIO("go\n"),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )


def test_extension_command_persists_session_name_and_label(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "session_meta.py").write_text(
        "def activate(api):\n"
        "    def meta(ctx, args):\n"
        "        entry = ctx.append_entry('note', {'body': 'hello'})\n"
        "        ctx.setSessionName('extension named session')\n"
        "        ctx.setLabel(entry, 'extension-label')\n"
        "    api.register_command('session-meta', 'session metadata', meta)\n",
        encoding="utf-8",
    )
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "sessions")
    provider = FakeNativeProvider(supports_tool_calls=True)
    session = NativeToolReplSession(
        provider=provider, tool_registry={}, native_session=tree
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/session-meta\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    custom = next(entry for entry in tree.get_entries() if entry.type == "custom")
    assert tree.name == "extension named session"
    assert tree.get_label(custom.id) == "extension-label"
    assert [entry.type for entry in tree.get_entries()] == [
        "custom",
        "session_info",
        "label",
    ]


@dataclass
class _RecordingToolProvider:
    """Tool-capable provider that records each request's provider-visible text.

    Used by the Phase 3.1d dispatch-precedence characterization to pin exactly
    which submitted lines reach a provider turn (and which are intercepted by
    the built-in kernel, resource dispatch, extension dispatch, or the
    unknown-``/`` fallback before ever reaching the provider).
    """

    requests: list[ProviderRequest] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "recording-tool-fake"

    @property
    def model_id(self) -> str:
        return "recording-tool-model"

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="OK",
            usage=None,
            metadata=None,
            tool_calls=(),
        )


def test_command_dispatch_precedence_kernel_resource_extension_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Characterize the closed Phase 3.1d dispatch precedence end to end.

    Driving ``run()`` through the real dispatch boundary pins the exact
    post-migration ordering now that ``/reload`` was the final built-in to
    leave the raw late-branch path:

    1. **Kernel is the sole built-in classifier.** ``/reload`` is claimed by
       the outcome kernel (it reloads settings/resources) even though a custom
       command *also* named ``reload`` exists on disk; no raw ``command_text ==
       "/reload"`` branch survives to change that. Runtime precedence is
       resolved by kernel-before-resource ordering; independently, as of Phase
       3.2 ``reload`` is also a member of the widened
       ``RESERVED_COMMAND_NAMES`` (derived from the full declarative-registry
       built-in set), so the colliding custom ``reload`` command is now also
       kept out of slash discovery and can never be claimed by the resource
       layer even if it were consulted.
    2. **UNHANDLED is the single delegation boundary**, in the fixed order
       ``dispatch_resource_command`` -> ``dispatch_extension_command`` -> the
       unknown-``/`` fallback diagnostic -> the provider turn.
    3. **Built-in over custom.** The colliding custom ``/reload`` command is
       never claimed by resource dispatch.
    4. **Custom over extension, extension over fallback.** ``/greet`` resolves
       to the workspace prompt template (a resource run) and the same-named
       extension command never fires, because a resource claim guards out
       extension dispatch; ``/extonly`` reaches the extension command (which
       runs before the unknown-``/`` fallback); ``/bogus`` reaches the
       unknown-``/`` fallback. (A same-named custom *command* would additionally
       be reserved out of the extension at registration time; a prompt template
       is not reserved, so both coexist and the fixed dispatch order — resource
       before extension — is what decides the winner.)
    """

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))

    # A custom command whose name collides with the built-in kernel command
    # ``/reload``. It is discovered and would be claimed by resource dispatch if
    # that layer were ever consulted for ``/reload``; the kernel intercepts
    # first, so it never is.
    commands_dir = tmp_path / ".pipy" / "commands"
    commands_dir.mkdir(parents=True)
    (commands_dir / "reload.md").write_text(
        "---\nname: reload\ndescription: custom reload\n---\n\n"
        "CUSTOM_RELOAD_BODY $ARGUMENTS\n",
        encoding="utf-8",
    )

    # A prompt template whose name collides with an extension command
    # (``greet``). A template is not reserved out of extensions, so both the
    # template and the extension command coexist; the fixed dispatch order
    # (resource before extension) must let the template win.
    templates_dir = tmp_path / ".pipy" / "templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "greet.md").write_text(
        "---\nname: greet\ndescription: greet template\n---\n\n"
        "TEMPLATE_GREET_BODY $ARGUMENTS\n",
        encoding="utf-8",
    )

    # Extension commands: ``greet`` collides with the prompt template above and
    # must never win; ``extonly`` has no resource collision and must run before
    # the unknown-``/`` fallback. Each writes a marker file when its handler
    # actually executes.
    greet_marker = tmp_path / "greet_ext_ran"
    extonly_marker = tmp_path / "extonly_ext_ran"
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "precedence_ext.py").write_text(
        "from pathlib import Path\n"
        "def activate(api):\n"
        f"    greet_marker = Path({str(greet_marker)!r})\n"
        f"    extonly_marker = Path({str(extonly_marker)!r})\n"
        "    def greet(ctx, args):\n"
        "        greet_marker.write_text('ran', encoding='utf-8')\n"
        "    def extonly(ctx, args):\n"
        "        extonly_marker.write_text('ran', encoding='utf-8')\n"
        "    api.register_command('greet', 'greet command', greet)\n"
        "    api.register_command('extonly', 'extension only', extonly)\n",
        encoding="utf-8",
    )

    provider = _RecordingToolProvider()
    session = NativeToolReplSession(provider=provider, tool_registry={})
    error_stream = io.StringIO()
    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/reload\n/greet hi\n/extonly\n/bogus\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    err = error_stream.getvalue()

    # (1)+(2): exactly one submitted line reached a provider turn — the
    # ``/greet`` prompt-template resource run. ``/reload`` (built-in kernel),
    # ``/extonly`` (extension command), ``/bogus`` (unknown-``/`` fallback),
    # and ``/exit`` (built-in kernel) all short-circuit before the provider.
    prompts = [request.user_prompt or "" for request in provider.requests]
    assert len(prompts) == 1
    assert prompts[0].strip() == "TEMPLATE_GREET_BODY hi"
    assert not any("CUSTOM_RELOAD_BODY" in prompt for prompt in prompts)
    assert result.resource_invocation_count == 1

    # (1)+(3): the outcome kernel classified ``/reload`` as the RELOAD built-in
    # (settings/resources reloaded) rather than the colliding custom command.
    assert "reloaded settings, keybindings, and resources." in err

    # Runtime precedence is enforced by kernel-before-resource ordering: the
    # kernel classifies ``/reload`` and continues first, so the resource layer
    # is never consulted for it. As of Phase 3.2 the advertising gap is also
    # closed: ``reload`` is a member of the widened ``RESERVED_COMMAND_NAMES``,
    # so the colliding custom ``reload`` command is dropped from slash discovery
    # and the resource layer returns ``None`` for ``/reload`` even if consulted
    # directly — it is reserved, not claimable.
    from pipy_harness.native.resources import (
        RESERVED_COMMAND_NAMES,
        WorkspaceResources,
        dispatch_resource_command,
    )

    assert "reload" in RESERVED_COMMAND_NAMES
    resources = WorkspaceResources.discover(
        tmp_path,
        config_home_env={},
        home_dir=tmp_path,
        include_workspace_defaults=True,
    )
    # The colliding custom ``reload`` command is no longer advertised.
    assert "/reload" not in resources.custom_command_slash_names()
    # And resource dispatch never claims it: ``reload`` is reserved, so the line
    # falls through to ``None`` (the caller's fail-closed unknown-command path).
    assert dispatch_resource_command("/reload", resources) is None

    # (4a): the ``/greet`` prompt-template resource run wins over the
    # same-named extension command, which therefore never fires (a resource
    # claim guards out extension dispatch). This is only a real dispatch-order
    # check if the ``greet`` extension command genuinely coexists rather than
    # being silently disabled at registration: the extension reserved set is a
    # union of the widened built-in set with *custom-command* slash names only
    # (not prompt templates), so the ``greet`` template does not reserve the
    # ``greet`` extension command. Pin that here so ``not greet_marker.exists()``
    # cannot pass vacuously if a future change ever folded template names into
    # the reserved set.
    reserved = extension_reserved_command_names(resources.custom_command_slash_names())
    assert "greet" not in reserved
    assert "extonly" not in reserved
    # The extension reserved set is widened to the full built-in vocabulary as
    # of Phase 3.2: a built-in like ``reload`` an extension could formerly have
    # registered (it was absent from the completion-menu subset) is now reserved.
    assert "reload" in reserved
    assert "session" in reserved
    assert not greet_marker.exists()

    # (4b): ``/extonly`` reaches the extension command (before the fallback).
    assert extonly_marker.exists()
    assert "'/extonly' is not handled in tool-loop mode" not in err

    # (2): ``/bogus`` — no built-in, no resource, no extension command — lands
    # on the single unknown-``/`` fallback diagnostic.
    assert "'/bogus' is not handled in tool-loop mode" in err


def test_lifecycle_hook_contexts_expose_no_model_runtime_controls(
    tmp_path: Path,
) -> None:
    """Lifecycle hooks cannot reach the gated mutation ports.

    The publication gate refuses `setModel` / `setThinkingLevel` /
    `setActiveTools` while a reload republishes its projections, and the reload
    closes the gate before firing `session_start` so a hook is never refused by
    it. That ordering is defensive today: a lifecycle-hook context does not
    carry the model-runtime controls at all, so there is nothing for the gate
    to refuse. This pins that fact — if the controls are ever wired into
    lifecycle contexts, the gate ordering must already be correct, and this
    test is the reminder to re-check it.
    """

    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    marker = extension_dir / "hook_capabilities.txt"
    (extension_dir / "reload_hook.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "MARKER = Path(__file__).with_name('hook_capabilities.txt')\n"
        "\n"
        "def activate(api):\n"
        "    @api.on('session_start')\n"
        "    def _on_start(ctx, event):\n"
        "        names = [\n"
        "            name\n"
        "            for name in (\n"
        "                'set_model', 'setModel',\n"
        "                'set_thinking_level', 'setThinkingLevel',\n"
        "                'set_active_tools', 'setActiveTools',\n"
        "            )\n"
        "            if hasattr(ctx, name)\n"
        "        ]\n"
        "        with MARKER.open('a', encoding='utf-8') as handle:\n"
        "            handle.write(repr(names) + '\\n')\n",
        encoding="utf-8",
    )

    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(provider=provider)
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/reload\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    records = marker.read_text(encoding="utf-8").splitlines()
    # Startup fires `session_start` once and `/reload` fires it again, so a
    # second record proves the reloaded generation's hook ran too rather than
    # the assertion passing on the startup fire alone.
    assert len(records) >= 2, records
    assert set(records) == {"[]"}, records


def test_a_malformed_candidate_flag_retains_the_complete_prior_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejecting a candidate must leave the old generation whole.

    Before the transactional rebuild the reloaded runtime went live *before*
    its flags were parsed, so a malformed flag left the candidate's commands
    registered against the previous generation's flag values. Now the
    candidate is rejected as a whole: the prior generation's command still
    dispatches and the candidate's command never becomes live.
    """

    from pipy_harness.native.resource_loading import RuntimeResourceOptions

    retained_apis: list[Any] = []
    monkeypatch.setattr(builtins, "_pipy_r1_apis", retained_apis, raising=False)
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "flagged.py").write_text(
        "import builtins\n"
        "from pathlib import Path\n"
        "\n"
        "FLIPPED = Path(__file__).with_name('flipped.txt')\n"
        "RAN = Path(__file__).with_name('ran.txt')\n"
        "\n"
        "def _mark(name):\n"
        "    with RAN.open('a', encoding='utf-8') as handle:\n"
        "        handle.write(name + '\\n')\n"
        "\n"
        "def activate(api):\n"
        "    builtins._pipy_r1_apis.append(api)\n"
        "    def flip(ctx, args):\n"
        "        FLIPPED.write_text('yes', encoding='utf-8')\n"
        "    api.register_command('flip', 'flip', flip)\n"
        "    if FLIPPED.exists():\n"
        "        # Candidate shape: the declared flag is gone, so the run's\n"
        "        # --needs-value token no longer parses.\n"
        "        from pipy_harness.extensions import ExtensionFlag\n"
        "        api.register_flag(\n"
        "            ExtensionFlag('candidate-state', 'string', default='candidate')\n"
        "        )\n"
        "        api.register_command('new-only', 'new', lambda c, a: _mark('NEW'))\n"
        "    else:\n"
        "        from pipy_harness.extensions import ExtensionFlag\n"
        "        api.register_flag(\n"
        "            ExtensionFlag('needs-value', 'string', 'needs a value')\n"
        "        )\n"
        "        api.register_command('old-only', 'old', lambda c, a: _mark('OLD'))\n",
        encoding="utf-8",
    )

    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(
        provider=provider,
        resource_options=RuntimeResourceOptions(
            extension_flag_tokens=("--needs-value=x",),
        ),
    )
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/flip\n/reload\n/old-only\n/new-only\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    out = error_stream.getvalue()
    assert "unknown extension flag" in out
    assert "keeping the previous extensions" in out

    dispatched = (extension_dir / "ran.txt").read_text(encoding="utf-8").split()
    # The retained generation still serves its command; the rejected
    # candidate's command never became live.
    assert dispatched == ["OLD"], dispatched

    assert len(retained_apis) == 2
    live_api, rejected_api = retained_apis
    assert live_api.get_flag("needs-value") == "x"
    assert rejected_api.get_flag("candidate-state") is None

    live_outbox_size = len(live_api._outbox)
    live_api.send_user_message("live-after-rejection")
    assert len(live_api._outbox) == live_outbox_size + 1
    assert live_api._outbox[-1].content == "live-after-rejection"

    rejected_outbox_size = len(rejected_api._outbox)
    rejected_api.send_user_message("must-drop")
    assert len(rejected_api._outbox) == rejected_outbox_size
    with pytest.raises(ExtensionCapabilityError):
        rejected_api.register_command("too-late", "late", lambda _ctx, _args: None)


def test_a_malformed_startup_flag_disposes_the_unpublished_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipy_harness.native.resource_loading import RuntimeResourceOptions

    retained_apis: list[Any] = []
    monkeypatch.setattr(builtins, "_pipy_r1_startup_apis", retained_apis, raising=False)
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "startup_flag.py").write_text(
        "import builtins\n"
        "from pipy_harness.extensions import ExtensionFlag\n"
        "def activate(api):\n"
        "    builtins._pipy_r1_startup_apis.append(api)\n"
        "    api.register_flag(ExtensionFlag('needs-value', 'string'))\n"
        "    api.register_command('candidate-only', 'candidate', lambda c, a: None)\n",
        encoding="utf-8",
    )
    session = NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True, final_text="ok"),
        resource_options=RuntimeResourceOptions(
            extension_flag_tokens=("--needs-value",),
        ),
    )
    error_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    assert result.status is HarnessStatus.FAILED
    assert result.exit_code == 2
    assert result.error_type == "ExtensionFlagError"
    assert "missing value for --needs-value" in error_stream.getvalue()
    assert len(retained_apis) == 1
    api = retained_apis[0]
    assert api.get_flag("needs-value") is None
    outbox_size = len(api._outbox)
    api.send_user_message("must-drop")
    assert len(api._outbox) == outbox_size
    with pytest.raises(ExtensionCapabilityError):
        api.register_command("late", "late", lambda _ctx, _args: None)

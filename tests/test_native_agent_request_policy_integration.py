"""Product integration contracts for provider-request tool authorization."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AGENT_TOOL_REQUEST_ID_PREFIX,
    AgentEvent,
    AgentRunCompleted,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
    ToolCallCompleted,
    ToolCallStarted,
    TurnCompleted,
)
from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent_request import (
    NativeProviderRequestHookContext,
    NativeProviderRequestInput,
    prepare_provider_request,
)
from pipy_harness.native.extension_runtime import ProviderRequestTransform
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree
from pipy_harness.native.tool_capabilities import ToolFilterOptions
from pipy_harness.native.tool_loop_session import NativeToolReplSession
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)


@dataclass(slots=True)
class _ScriptProvider:
    script: tuple[tuple[ProviderToolCall, ...], ...]
    supports_tool_calls: bool = True
    name: str = "request-policy"
    model_id: str = "request-policy-model"
    index: int = 0
    requests: list[ProviderRequest] = field(default_factory=list)

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        calls = self.script[self.index]
        self.index += 1
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=f"provider turn {self.index}",
            tool_calls=calls,
        )


@dataclass(slots=True)
class _EventSink:
    events: list[AgentEvent] = field(default_factory=list)

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


@dataclass(slots=True)
class _NamedTool:
    name: str
    invoked: list[str]
    live_output: str | None = None

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=f"Fixture {self.name} tool.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        self.invoked.append(self.name)
        if self.live_output is not None and context.output_sink is not None:
            context.output_sink(self.live_output)
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=str(request.arguments["text"]),
            provider_correlation_id=request.provider_correlation_id,
        )


def _call(name: str, arguments: str, correlation_id: str) -> ProviderToolCall:
    return ProviderToolCall(
        provider_correlation_id=correlation_id,
        tool_name=name,
        arguments_json=arguments,
    )


def _completed_results(sink: _EventSink) -> list[AgentToolResultMessage]:
    return [
        event.result for event in sink.events if isinstance(event, ToolCallCompleted)
    ]


def _request_input(tmp_path: Path) -> NativeProviderRequestInput:
    active_user_message = AgentUserMessage(ProductContent("user"))
    return NativeProviderRequestInput(
        system_prompt="system",
        user_prompt="user",
        provider_name="fake",
        model_id="fake-model",
        cwd=tmp_path,
        messages=(active_user_message,),
        active_input=AgentActiveInput(active_user_message),
        available_tools=tuple(
            ToolDefinition(
                name=name,
                description=f"Fixture {name} tool.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            )
            for name in ("first", "second", "third")
        ),
        attachments=(),
        provider_header_callback=None,
    )


def _hook_context(tmp_path: Path) -> NativeProviderRequestHookContext:
    return NativeProviderRequestHookContext(
        cwd=str(tmp_path),
        has_ui=False,
        notify_sink=None,
        ui_driver=None,
        set_active_tools_fn=None,
        set_model_fn=None,
        set_thinking_level_fn=None,
        flags={},
        project_trusted=False,
    )


def test_serial_hooks_share_context_and_cumulative_fields_but_original_messages(
    tmp_path: Path,
) -> None:
    request_input = _request_input(tmp_path)
    seen_context_ids: list[int] = []

    def first(event, context):
        seen_context_ids.append(id(context))
        assert event.system_prompt == "system"
        assert event.user_prompt == "user"
        assert event.available_tools == ("first", "second", "third")
        assert event.messages is request_input.messages
        return ProviderRequestTransform(
            system_prompt="system::first",
            user_prompt="user::first",
            available_tools=("second",),
        )

    def second(event, context):
        seen_context_ids.append(id(context))
        assert event.system_prompt == "system::first::prompt-only"
        assert event.user_prompt == "user::first::prompt-only"
        assert event.available_tools == ("second",)
        assert event.messages is request_input.messages
        assert event.messages[0].content.value == "user"
        return ProviderRequestTransform(
            system_prompt="system::first::prompt-only::second",
            user_prompt="user::first::prompt-only::second",
            available_tools=("first", "second"),
        )

    def no_transform(event, context):
        seen_context_ids.append(id(context))
        assert event.system_prompt == "system::first"
        assert event.user_prompt == "user::first"
        assert event.available_tools == ("second",)
        return None

    def prompt_only(event, context):
        seen_context_ids.append(id(context))
        assert event.system_prompt == "system::first"
        assert event.user_prompt == "user::first"
        assert event.available_tools == ("second",)
        return ProviderRequestTransform(
            system_prompt="system::first::prompt-only",
            user_prompt="user::first::prompt-only",
        )

    snapshot = prepare_provider_request(
        request_input,
        (first, no_transform, prompt_only, second),
        _hook_context(tmp_path),
    )

    assert len(set(seen_context_ids)) == 1
    assert snapshot.request.system_prompt == "system::first::prompt-only::second"
    assert snapshot.request.user_prompt == "user::first::prompt-only::second"
    assert snapshot.advertised_tool_names == ("second",)
    assert (
        snapshot.request.messages[0].content.value == "user::first::prompt-only::second"
    )


def test_async_hooks_preserve_monotonic_narrowing(tmp_path: Path) -> None:
    request_input = _request_input(tmp_path)

    async def first(event, _context):
        assert event.available_tools == ("first", "second", "third")
        return ProviderRequestTransform(
            user_prompt="async::first",
            available_tools=("third", "first"),
        )

    async def second(event, _context):
        assert event.user_prompt == "async::first"
        assert event.available_tools == ("first", "third")
        assert event.messages is request_input.messages
        return ProviderRequestTransform(
            user_prompt="async::second",
            available_tools=("second", "third"),
        )

    snapshot = prepare_provider_request(
        request_input,
        (first, second),
        _hook_context(tmp_path),
    )

    assert snapshot.request.user_prompt == "async::second"
    assert snapshot.advertised_tool_names == ("third",)


def test_hidden_registered_call_is_rejected_before_hooks_or_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    call_marker = tmp_path / "hidden-call-hook.txt"
    result_marker = tmp_path / "hidden-result-hook.txt"
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "hidden_guard.py").write_text(
        "from pathlib import Path\n"
        f"CALL = Path({str(call_marker)!r})\n"
        f"RESULT = Path({str(result_marker)!r})\n"
        "def activate(api):\n"
        "    @api.on('tool_call')\n"
        "    def call(event, ctx):\n"
        "        CALL.write_text(event.tool_name, encoding='utf-8')\n"
        "    @api.on('tool_result')\n"
        "    def result(event, ctx):\n"
        "        RESULT.write_text(event.tool_name, encoding='utf-8')\n",
        encoding="utf-8",
    )
    invoked: list[str] = []
    provider = _ScriptProvider(
        ((_call("echo", '{"text":"hidden"}', "provider-hidden"),), ())
    )
    sink = _EventSink()
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "sessions")
    output = io.StringIO()

    result = NativeToolReplSession(
        provider=provider,
        tool_registry={"echo": _NamedTool("echo", invoked, "forbidden-live-output")},
        tool_filter_options=ToolFilterOptions(no_tools=True),
        agent_event_sink=sink,
        native_session=tree,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=output,
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 0
    assert result.malformed_argument_count == 0
    assert result.budget_exhausted_count == 0
    assert invoked == []
    assert not call_marker.exists()
    assert not result_marker.exists()
    assert "forbidden-live-output" not in output.getvalue()

    starts = [event for event in sink.events if isinstance(event, ToolCallStarted)]
    completions = [
        event for event in sink.events if isinstance(event, ToolCallCompleted)
    ]
    assert len(starts) == len(completions) == 1
    assert starts[0].call.tool_name == "echo"
    assert starts[0].call.provider_correlation_id == "provider-hidden"
    rejected = completions[0].result
    assert rejected.tool_name == "echo"
    assert rejected.provider_correlation_id == "provider-hidden"
    assert rejected.tool_request_id.startswith(AGENT_TOOL_REQUEST_ID_PREFIX)
    assert rejected.tool_request_id != rejected.provider_correlation_id
    assert rejected.is_error
    assert rejected.content.value == "unknown tool: echo"

    turns = [event for event in sink.events if isinstance(event, TurnCompleted)]
    assert [turn.tool_results for turn in turns] == [(rejected,), ()]
    assert [
        message
        for message in provider.requests[1].messages
        if isinstance(message, AgentToolResultMessage)
    ] == [rejected]
    runs = [event for event in sink.events if isinstance(event, AgentRunCompleted)]
    assert len(runs) == 1
    assert [
        message
        for message in runs[0].result.messages
        if isinstance(message, AgentToolResultMessage)
    ] == [rejected]
    assert [
        entry.message
        for entry in tree.get_entries()
        if isinstance(entry, MessageEntry)
        and isinstance(entry.message, AgentToolResultMessage)
    ] == [rejected]


def test_hidden_extension_tool_skips_custom_renderers_for_malformed_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    handler_marker = tmp_path / "hidden-handler.txt"
    call_render_marker = tmp_path / "hidden-render-call.txt"
    result_render_marker = tmp_path / "hidden-render-result.txt"
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "hidden_renderer.py").write_text(
        "from pathlib import Path\n"
        "from pipy_harness.extensions import "
        "ExtensionTool, ToolResult, lines_component\n"
        f"HANDLER = Path({str(handler_marker)!r})\n"
        f"CALL_RENDER = Path({str(call_render_marker)!r})\n"
        f"RESULT_RENDER = Path({str(result_render_marker)!r})\n"
        "def handler(ctx, params):\n"
        "    HANDLER.write_text('called', encoding='utf-8')\n"
        "    return ToolResult(content='forbidden')\n"
        "def render_call(ctx):\n"
        "    CALL_RENDER.write_text('called', encoding='utf-8')\n"
        "    return lines_component(['custom call'])\n"
        "def render_result(ctx):\n"
        "    RESULT_RENDER.write_text('called', encoding='utf-8')\n"
        "    return lines_component(['custom result'])\n"
        "def activate(api):\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='hidden', description='hidden fixture',\n"
        "        input_schema={'type': 'object'}, handler=handler,\n"
        "        render_call=render_call, render_result=render_result))\n",
        encoding="utf-8",
    )
    provider = _ScriptProvider(((_call("hidden", "{", "hidden-render"),), ()))
    sink = _EventSink()
    error_stream = io.StringIO()

    result = NativeToolReplSession(
        provider=provider,
        tool_registry={},
        tool_filter_options=ToolFilterOptions(no_tools=True),
        agent_event_sink=sink,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 0
    assert result.malformed_argument_count == 0
    assert not handler_marker.exists()
    assert not call_render_marker.exists()
    assert not result_render_marker.exists()
    assert "custom call" not in error_stream.getvalue()
    assert "custom result" not in error_stream.getvalue()
    assert "hidden({)" in error_stream.getvalue()
    assert "unknown tool: hidden" in error_stream.getvalue()

    starts = [event for event in sink.events if isinstance(event, ToolCallStarted)]
    completions = [
        event for event in sink.events if isinstance(event, ToolCallCompleted)
    ]
    assert len(starts) == len(completions) == 1
    assert starts[0].call.provider_correlation_id == "hidden-render"
    rejected = completions[0].result
    assert rejected.provider_correlation_id == "hidden-render"
    assert rejected.is_error
    assert rejected.content.value == "unknown tool: hidden"


def test_hidden_first_consumes_budget_before_following_valid_call(
    tmp_path: Path,
) -> None:
    invoked: list[str] = []
    provider = _ScriptProvider(
        (
            (
                _call("hidden", '{"text":"hidden"}', "hidden"),
                _call("echo", '{"text":"valid"}', "valid"),
            ),
            (),
        )
    )
    sink = _EventSink()

    result = NativeToolReplSession(
        provider=provider,
        tool_registry={
            "echo": _NamedTool("echo", invoked),
            "hidden": _NamedTool("hidden", invoked),
        },
        tool_filter_options=ToolFilterOptions(allow=("echo",)),
        tool_budget=1,
        agent_event_sink=sink,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.tool_invocation_count == 0
    assert result.malformed_argument_count == 0
    assert result.budget_exhausted_count == 1
    assert invoked == []
    observations = _completed_results(sink)
    assert [item.provider_correlation_id for item in observations] == [
        "hidden",
        "valid",
    ]
    assert [item.content.value for item in observations] == [
        "unknown tool: hidden",
        "tool budget exhausted (limit 1)",
    ]


def test_mixed_valid_then_hidden_calls_execute_and_reject_in_response_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    call_marker = tmp_path / "mixed-call-hook.txt"
    result_marker = tmp_path / "mixed-result-hook.txt"
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "mixed_hooks.py").write_text(
        "from pathlib import Path\n"
        f"CALL = Path({str(call_marker)!r})\n"
        f"RESULT = Path({str(result_marker)!r})\n"
        "def activate(api):\n"
        "    @api.on('tool_call')\n"
        "    def call(event, ctx):\n"
        "        CALL.write_text(event.tool_name, encoding='utf-8')\n"
        "    @api.on('tool_result')\n"
        "    def result(event, ctx):\n"
        "        RESULT.write_text(event.tool_name, encoding='utf-8')\n",
        encoding="utf-8",
    )
    invoked: list[str] = []
    provider = _ScriptProvider(
        (
            (
                _call("echo", '{"text":"valid"}', "valid"),
                _call("hidden", '{"text":"hidden"}', "hidden"),
            ),
            (),
        )
    )
    sink = _EventSink()

    result = NativeToolReplSession(
        provider=provider,
        tool_registry={
            "echo": _NamedTool("echo", invoked),
            "hidden": _NamedTool("hidden", invoked),
        },
        tool_filter_options=ToolFilterOptions(allow=("echo",)),
        tool_budget=2,
        agent_event_sink=sink,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.tool_invocation_count == 1
    assert result.malformed_argument_count == 0
    assert result.budget_exhausted_count == 0
    assert invoked == ["echo"]
    assert call_marker.read_text(encoding="utf-8") == "echo"
    assert result_marker.read_text(encoding="utf-8") == "echo"
    observations = _completed_results(sink)
    assert [item.provider_correlation_id for item in observations] == [
        "valid",
        "hidden",
    ]
    assert [item.content.value for item in observations] == [
        "valid",
        "unknown tool: hidden",
    ]


def test_budget_exhaustion_precedes_request_authorization(tmp_path: Path) -> None:
    invoked: list[str] = []
    provider = _ScriptProvider(
        (
            (
                _call("echo", '{"text":"first"}', "first"),
                _call("hidden", '{"text":"hidden"}', "hidden"),
            ),
            (),
        )
    )
    sink = _EventSink()

    result = NativeToolReplSession(
        provider=provider,
        tool_registry={
            "echo": _NamedTool("echo", invoked),
            "hidden": _NamedTool("hidden", invoked),
        },
        tool_filter_options=ToolFilterOptions(allow=("echo",)),
        tool_budget=1,
        agent_event_sink=sink,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.tool_invocation_count == 1
    assert result.malformed_argument_count == 0
    assert result.budget_exhausted_count == 1
    assert invoked == ["echo"]
    assert [item.content.value for item in _completed_results(sink)] == [
        "first",
        "tool budget exhausted (limit 1)",
    ]


def test_unauthorized_call_does_not_reset_consecutive_malformed_streak(
    tmp_path: Path,
) -> None:
    invoked: list[str] = []
    provider = _ScriptProvider(
        (
            (
                _call("echo", "{", "malformed-a"),
                _call("hidden", '{"text":"hidden"}', "unauthorized"),
                _call("echo", "{", "malformed-b"),
                _call("echo", "{", "malformed-c"),
            ),
        )
    )
    sink = _EventSink()
    error_stream = io.StringIO()

    result = NativeToolReplSession(
        provider=provider,
        tool_registry={
            "echo": _NamedTool("echo", invoked),
            "hidden": _NamedTool("hidden", invoked),
        },
        tool_filter_options=ToolFilterOptions(allow=("echo",)),
        agent_event_sink=sink,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    assert result.status is HarnessStatus.FAILED
    assert result.error_type == "NativeToolLoopMalformedFatal"
    assert result.tool_invocation_count == 0
    assert result.malformed_argument_count == 3
    assert result.consecutive_malformed_streak == 3
    assert result.budget_exhausted_count == 0
    assert invoked == []
    assert "3 consecutive malformed tool calls" in error_stream.getvalue()

    starts = [event for event in sink.events if isinstance(event, ToolCallStarted)]
    completions = [
        event for event in sink.events if isinstance(event, ToolCallCompleted)
    ]
    expected_ids = [
        "malformed-a",
        "unauthorized",
        "malformed-b",
        "malformed-c",
    ]
    assert [event.call.provider_correlation_id for event in starts] == expected_ids
    assert [
        event.result.provider_correlation_id for event in completions
    ] == expected_ids
    assert completions[1].result.content.value == "unknown tool: hidden"


def test_tool_added_by_first_call_is_not_authorized_later_in_same_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    late_marker = tmp_path / "late-tool-invoked.txt"
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "dynamic_request_tools.py").write_text(
        "from pathlib import Path\n"
        "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
        f"LATE_MARKER = Path({str(late_marker)!r})\n"
        "def activate(api):\n"
        "    def prepare(ctx, args):\n"
        "        assert ctx.set_active_tools(['loader'])\n"
        "    api.register_command('prepare', 'prepare loader', prepare)\n"
        "    def loader(ctx, params):\n"
        "        assert ctx.set_active_tools(['loader', 'late_tool'])\n"
        "        return ToolResult(content='loaded')\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='loader', description='load late tool',\n"
        "        input_schema={'type': 'object'}, handler=loader))\n"
        "    def late(ctx, params):\n"
        "        LATE_MARKER.write_text('invoked', encoding='utf-8')\n"
        "        return ToolResult(content='late')\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='late_tool', description='late tool',\n"
        "        input_schema={'type': 'object'}, handler=late))\n",
        encoding="utf-8",
    )
    provider = _ScriptProvider(
        (
            (
                _call("loader", "{}", "loader"),
                _call("late_tool", "{}", "late"),
            ),
            (),
        )
    )
    sink = _EventSink()

    result = NativeToolReplSession(
        provider=provider,
        tool_registry={},
        agent_event_sink=sink,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/prepare\nload\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.tool_invocation_count == 1
    assert result.malformed_argument_count == 0
    assert not late_marker.exists()
    assert [
        tuple(tool.name for tool in request.available_tools)
        for request in provider.requests
    ] == [("loader",), ("loader", "late_tool")]
    observations = _completed_results(sink)
    assert [item.provider_correlation_id for item in observations] == [
        "loader",
        "late",
    ]
    assert [item.content.value for item in observations] == [
        "loaded",
        "unknown tool: late_tool",
    ]
    assert observations[0].added_tool_names == ("late_tool",)
    assert observations[1].added_tool_names == ()


def test_tool_activated_by_call_runs_with_custom_renderers_on_next_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    invoked_marker = tmp_path / "late-tool-invoked.txt"
    call_render_marker = tmp_path / "late-render-call.txt"
    result_render_marker = tmp_path / "late-render-result.txt"
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "dynamic_rendered_tool.py").write_text(
        "from pathlib import Path\n"
        "from pipy_harness.extensions import "
        "ExtensionTool, ToolResult, lines_component\n"
        f"INVOKED = Path({str(invoked_marker)!r})\n"
        f"CALL_RENDER = Path({str(call_render_marker)!r})\n"
        f"RESULT_RENDER = Path({str(result_render_marker)!r})\n"
        "def activate(api):\n"
        "    def prepare(ctx, args):\n"
        "        assert ctx.set_active_tools(['loader'])\n"
        "    api.register_command('prepare', 'prepare loader', prepare)\n"
        "    def loader(ctx, params):\n"
        "        assert ctx.set_active_tools(['loader', 'late_tool'])\n"
        "        return ToolResult(content='loaded')\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='loader', description='load late tool',\n"
        "        input_schema={'type': 'object'}, handler=loader))\n"
        "    def late(ctx, params):\n"
        "        INVOKED.write_text('invoked', encoding='utf-8')\n"
        "        return ToolResult(content='late')\n"
        "    def render_call(ctx):\n"
        "        CALL_RENDER.write_text('rendered', encoding='utf-8')\n"
        "        return lines_component(['late custom call'])\n"
        "    def render_result(ctx):\n"
        "        RESULT_RENDER.write_text('rendered', encoding='utf-8')\n"
        "        return lines_component(['late custom result'])\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='late_tool', description='late tool',\n"
        "        input_schema={'type': 'object'}, handler=late,\n"
        "        render_call=render_call, render_result=render_result))\n",
        encoding="utf-8",
    )
    provider = _ScriptProvider(
        (
            (_call("loader", "{}", "loader"),),
            (_call("late_tool", "{}", "late"),),
            (),
        )
    )
    sink = _EventSink()
    error_stream = io.StringIO()

    result = NativeToolReplSession(
        provider=provider,
        tool_registry={},
        agent_event_sink=sink,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/prepare\nload\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 2
    assert result.malformed_argument_count == 0
    assert invoked_marker.read_text(encoding="utf-8") == "invoked"
    assert call_render_marker.read_text(encoding="utf-8") == "rendered"
    assert result_render_marker.read_text(encoding="utf-8") == "rendered"
    assert "late custom call" in error_stream.getvalue()
    assert "late custom result" in error_stream.getvalue()
    assert [
        tuple(tool.name for tool in request.available_tools)
        for request in provider.requests
    ] == [
        ("loader",),
        ("loader", "late_tool"),
        ("loader", "late_tool"),
    ]
    observations = _completed_results(sink)
    assert [item.provider_correlation_id for item in observations] == [
        "loader",
        "late",
    ]
    assert [item.content.value for item in observations] == ["loaded", "late"]

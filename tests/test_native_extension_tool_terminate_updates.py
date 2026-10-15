"""Extension tool results: `terminate` and partial updates (MCP parity T2).

Pi's `AgentToolResult.terminate` ends a tool batch without the automatic
follow-up turn when every result of the batch asks for it
(`shouldTerminateToolBatch`); a `tool_call` hook's block can ask too. A
running extension tool publishes partial results through `ctx.on_update`
(Pi's `onUpdate` of `execute`).
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from test_native_agent_loop import _tool_result
from test_native_extension_tool_metadata import (
    _result,
    _StubProvider,
    _tool_results,
)

from pipy_harness.extensions import ExtensionTool, ToolResult
from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AgentEvent,
    AgentToolCall,
    NestedToolCallUpdated,
    ProductContent,
    ToolCallCompleted,
    ToolCallUpdated,
)
from pipy_harness.native.agent.content import ToolPartialResult
from pipy_harness.native.automation.serialize import tool_result_json
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.extension_types import RegisteredTool
from pipy_harness.native.extensions.command_context import (
    ExtensionToolContext,
    make_extension_context,
)
from pipy_harness.native.extensions.tool_port import _ExtensionToolPort
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.tools import (
    ToolContext,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.registry import production_tool_registry

_EXTENSION = """import threading
from pipy_harness.extensions import ExtensionTool, ToolBlock, ToolResult

_NONE = {'type': 'object', 'properties': {}, 'additionalProperties': False}


def activate(api):
    def finish(ctx, params):
        return ToolResult(content='finished', terminate=True)

    def plain(ctx, params):
        return ToolResult(content='plain')

    def progress(ctx, params):
        ctx.on_update(ToolResult(content='half', details={'step': 1}))
        worker = threading.Thread(
            target=ctx.onUpdate, args=(ToolResult(content='most'),))
        worker.start()
        worker.join()
        return ToolResult(content='whole')

    def relay(ctx, params):
        seen = []
        outcome = ctx.execute_tool('progress', on_update=seen.append)
        ended = ctx.execute_tool('finish')
        return ToolResult(content=' | '.join([
            outcome.result.content,
            '/'.join(update.content for update in seen),
            str(ended.result.terminate)]))

    for name, handler in (('finish', finish), ('plain', plain),
                          ('progress', progress), ('relay', relay)):
        api.register_tool(ExtensionTool(
            name=name, description=name, input_schema=_NONE, handler=handler))

    @api.on('tool_call')
    def gate(event, ctx):
        if event.tool_name == 'plain':
            return ToolBlock(reason='not now', terminate=True)
        return None
"""


@dataclass
class _ThreadSink:
    """Collects the events with the thread each was emitted on."""

    events: list[tuple[AgentEvent, threading.Thread]] = field(default_factory=list)

    def emit(self, event: AgentEvent) -> None:
        self.events.append((event, threading.current_thread()))

    def of(self, kind: type) -> list:
        return [event for event, _ in self.events if isinstance(event, kind)]


def _session(workspace: Path, monkeypatch, provider, *, interruptible: bool):
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(workspace / "empty-global"))
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "t2.py").write_text(_EXTENSION, encoding="utf-8")
    sink = _ThreadSink()
    session = CodingSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=20,
        agent_event_sink=sink,
        abort_event=threading.Event() if interruptible else None,
    )
    result = session.run(
        workspace_root=workspace,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    return result, sink


def _calls(*names: str) -> tuple[ProviderToolCall, ...]:
    return tuple(
        ProviderToolCall(f"c{index}", name, "{}") for index, name in enumerate(names, 1)
    )


# -- terminate ---------------------------------------------------------------


def test_a_batch_that_all_asks_to_terminate_runs_no_follow_up_turn(
    tmp_path, monkeypatch
) -> None:
    # Pi: the batch is final when every result sets `terminate: true`; a
    # blocked call joins through its `tool_call` hook's `terminate`. The
    # stub provider has no second response, so a follow-up turn would fail.
    provider = _StubProvider([_result(tool_calls=_calls("finish", "plain"))])

    result, sink = _session(tmp_path, monkeypatch, provider, interruptible=False)

    assert result.status is HarnessStatus.SUCCEEDED
    assert len(provider.requests) == 1
    finished, blocked = (event.result for event in sink.of(ToolCallCompleted))
    assert (finished.content.value, finished.terminate) == ("finished", True)
    assert blocked.is_error is True
    assert blocked.content.value == "blocked by extension: not now"
    assert blocked.terminate is True


def test_one_result_that_does_not_terminate_keeps_the_follow_up_turn(
    tmp_path, monkeypatch
) -> None:
    provider = _StubProvider(
        [_result(tool_calls=_calls("finish", "progress")), _result(final_text="ok")]
    )

    result, _sink = _session(tmp_path, monkeypatch, provider, interruptible=False)

    assert result.status is HarnessStatus.SUCCEEDED
    assert len(provider.requests) == 2
    assert [m.content.value for m in _tool_results(provider.requests[1])] == [
        "finished",
        "whole",
    ]


def test_a_nested_result_that_terminates_ends_nothing(tmp_path, monkeypatch) -> None:
    # Pi applies the rule to the model's batch: the calling tool sees the
    # hint on its outcome, and its own result decides.
    provider = _StubProvider(
        [_result(tool_calls=_calls("relay")), _result(final_text="ok")]
    )

    result, _sink = _session(tmp_path, monkeypatch, provider, interruptible=True)

    assert result.status is HarnessStatus.SUCCEEDED
    assert len(provider.requests) == 2
    (relayed,) = _tool_results(provider.requests[1])
    assert relayed.content.value == "whole | half/most | True"
    assert relayed.terminate is False


def test_the_json_result_carries_terminate_only_when_set(tmp_path) -> None:
    port = _port(lambda ctx, params: ToolResult(content="x", terminate=True))
    assert _invoke(port, tmp_path).terminate is True
    # Pi `terminate === true`: anything but True does not ask.
    port = _port(lambda ctx, params: ToolResult(content="x", terminate=1))  # type: ignore[arg-type]
    assert _invoke(port, tmp_path).terminate is False

    message = _tool_result(AgentToolCall("c1", "finish", ProductContent("{}")))
    assert "terminate" not in tool_result_json(message)
    assert tool_result_json(replace(message, terminate=True))["terminate"] is True


# -- partial updates ---------------------------------------------------------


@pytest.mark.parametrize("interruptible", [False, True])
def test_a_tool_publishes_partial_results_on_the_session_thread(
    tmp_path, monkeypatch, interruptible
) -> None:
    # Pi `onUpdate`: each partial result becomes a `tool_execution_update`
    # that replaces the one before; pipy publishes it on the session thread,
    # also when the handler's own thread sends it.
    provider = _StubProvider(
        [_result(tool_calls=_calls("progress")), _result(final_text="ok")]
    )

    result, sink = _session(
        tmp_path, monkeypatch, provider, interruptible=interruptible
    )

    assert result.status is HarnessStatus.SUCCEEDED
    updates = [
        (event, thread)
        for event, thread in sink.events
        if isinstance(event, ToolCallUpdated)
    ]
    assert [event.update for event, _ in updates] == [
        ToolPartialResult("half", {"step": 1}),
        ToolPartialResult("most"),
    ]
    assert {thread for _, thread in updates} == {threading.main_thread()}
    kinds = [type(event) for event, _ in sink.events]
    assert kinds.index(ToolCallUpdated) < kinds.index(ToolCallCompleted)


def test_a_nested_tools_partial_results_reach_its_caller_and_the_events(
    tmp_path, monkeypatch
) -> None:
    provider = _StubProvider(
        [_result(tool_calls=_calls("relay")), _result(final_text="ok")]
    )

    _result_, sink = _session(tmp_path, monkeypatch, provider, interruptible=False)

    nested = sink.of(NestedToolCallUpdated)
    assert [event.update.content for event in nested] == ["half", "most"]
    assert {event.parent_correlation_id for event in nested} == {"c1"}
    assert sink.of(ToolCallUpdated) == []


def _port(handler) -> _ExtensionToolPort:
    tool = ExtensionTool(
        name="probe",
        description="probe",
        input_schema={"type": "object"},
        handler=handler,
    )
    return _ExtensionToolPort(RegisteredTool(tool=tool, extension="t"), has_ui=False)


def _invoke(port: _ExtensionToolPort, tmp_path: Path, sink=None):
    return port.invoke(
        ToolRequest(
            tool_request_id=make_tool_request_id(), tool_name="probe", arguments={}
        ),
        ToolContext(workspace_root=tmp_path, output_sink=sink),
    )


def test_a_partial_result_is_bounded_and_must_be_a_tool_result(tmp_path) -> None:
    published: list[object] = []

    def handler(ctx, params):
        ctx.on_update(ToolResult(content="x" * 40_000, details={"n": 1}))
        ctx.on_update("not a result")
        return ToolResult(content="unreached")

    result = _invoke(_port(handler), tmp_path, published.append)

    (partial,) = published
    assert isinstance(partial, ToolPartialResult)
    assert len(partial.content) <= 32 * 1024
    assert partial.content.endswith("[pipy: extension tool output truncated]")
    assert partial.details == {"n": 1}
    assert partial.appended is None
    assert result.is_error is True
    assert result.output_text == "extension tool error: TypeError"


def test_on_update_does_nothing_where_nothing_receives_it(tmp_path) -> None:
    result = _invoke(
        _port(lambda ctx, params: (ctx.on_update(ToolResult(content="x")), "done")[1]),
        tmp_path,
    )
    assert result.output_text == "done"
    command_context = make_extension_context(str(tmp_path), False)
    assert isinstance(command_context, ExtensionToolContext)
    command_context.on_update(ToolResult(content="x"))

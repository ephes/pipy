"""Live stopped tool-call rows are display-only and match reopened history."""

from pathlib import Path

import pytest
from test_native_agent_loop import _make_loop, _provider_result, _run_input
from test_native_session_history_render import _Terminal
from test_native_ui_coding_session_renderer import _Harness

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentCancellationReason,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.content import TextContent
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.models import (
    ProviderContentBlock,
    ProviderPartial,
    ProviderToolCall,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.ui.rendering import RenderingAgentEventAdapter


@pytest.mark.parametrize("tool", ["read", "edit", "bash"])
@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("call_count", [1, 2])
@pytest.mark.parametrize("arguments", ['{"path":"a"}', '{"path":"a'])
def test_stopped_partial_call_live_rows_match_durable_reopen(
    tmp_path: Path,
    failure: bool,
    arguments: str,
    call_count: int,
    tool: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    preview_attempts: list[object] = []
    monkeypatch.setattr(
        "pipy_harness.native.ui.components.tool_loop_renderer.compute_edit_preview",
        lambda *args: preview_attempts.append(args),
    )
    calls = tuple(ProviderToolCall(f"c{i}", tool, arguments) for i in range(call_count))
    blocks: tuple[ProviderContentBlock, ...] = (TextContent("partial"), *calls)
    turn = (
        ProviderTurnOutcome(
            result=replace(
                _provider_result(status=HarnessStatus.FAILED), content_blocks=blocks
            )
        )
        if failure
        else ProviderTurnOutcome(
            cancellation_reason=AgentCancellationReason.OPERATOR_ABORT,
            partial=ProviderPartial(content_blocks=blocks),
        )
    )
    loop, _, events, _ = _make_loop([], [turn])
    outcome = loop.run(_run_input())
    message = outcome.final_history[-1]
    assert isinstance(message, AgentAssistantMessage)
    assert len(message.tool_calls) == call_count
    h = _Harness()
    adapter = RenderingAgentEventAdapter(h.renderer)
    for event in events.events:
        adapter.emit(event)
    live = h.transcript.history_blocks
    assert [kind for kind, _ in live] == ["assistant", *(["tool_box"] * call_count)]
    assert h.transcript.pending_tool_lines == ()
    assert preview_attempts == []
    assert h.ticks == []
    assert h.cancelled == 0
    assert not any(type(e).__name__.startswith("ToolCall") for e in events.events)

    tree = NativeSessionTree.create(tmp_path / "saved")
    tree.append_message(AgentUserMessage(ProductContent("q")))
    tree.append_message(message)
    assert tree.path is not None
    restored = _Terminal(tmp_path / "restored")
    restored.tree = NativeSessionTree.open(tree.path, strict=True)
    restored.history.render_active_branch()
    assert restored.rows()[1:] == live

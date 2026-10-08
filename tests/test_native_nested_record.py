"""CM1 T7b: bounded parent evidence, disk replay and private summary inputs."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_native_agent_loop import _run_input, _tool_result
from test_native_agent_nested_calls import _Runner, _setup
from test_native_session_history_render import (
    _assistant,
    _box,
    _call,
    _result,
    _Terminal,
)

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.events import NestedToolCallCompleted, ToolCallCompleted
from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.nested_calls import (
    NestedToolCallOutcome,
    NestedToolCallService,
)
from pipy_harness.native.agent.nested_record import parse_nested_record
from pipy_harness.native.agent.nested_status import NestedCallStatus
from pipy_harness.native.agent.tools import ToolExecutionOutcome
from pipy_harness.native.automation.serialize import serialize_message
from pipy_harness.native.coding.compaction import file_operation_input
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tool_rows import ToolRowState, nested_lines_from_details
from pipy_harness.native.ui.components.transcript import HistoryBlockTuple


def _service(error: str = "") -> NestedToolCallService:
    return NestedToolCallService(
        AgentToolCall("parent", "codemode", ProductContent("{}")),
        frozenset({"read", "write"}),
        lambda call: NestedToolCallOutcome(
            call,
            replace(
                _tool_result(
                    call, error or "PRIVATE CHILD OUTPUT", is_error=bool(error)
                ),
                details={"private": [1]},
            ),
            NestedCallStatus.SETTLED,
            duration_seconds=0.125,
        ),
        lambda call, text: _tool_result(call, text, is_error=True),
    )


def _args(size: int, *, multibyte: bool = False) -> str:
    # Compact object of exactly size bytes, including multibyte payloads.
    content = "é" * ((size - 8) // 2) if multibyte else "x" * (size - 8)
    if multibyte and (size - 8) % 2:
        content += "x"
    text = json.dumps({"p": content}, ensure_ascii=False, separators=(",", ":"))
    assert len(text.encode()) == size
    return text


@pytest.mark.parametrize("multibyte", [False, True])
def test_exact_schema_argument_byte_bounds_and_snapshot(multibyte: bool) -> None:
    service = _service()
    for _ in range(4):
        service.call("read", _args(8192, multibyte=multibyte))
    record = service.durable_record()
    assert record["complete"] is True
    calls = record["calls"]
    assert isinstance(calls, list)
    assert len(calls) == 4
    assert set(calls[0]) == {"id", "name", "status", "arguments", "durationMs"}
    assert calls[0]["durationMs"] == 125 and calls[0]["status"] == "ok"
    service.call("read", "{}")
    service.call("read", _args(8193, multibyte=multibyte))
    latest: Any = service.durable_record()
    assert latest["complete"] is False
    assert latest["calls"][4]["argumentsBytes"] == 2
    assert latest["calls"][5]["argumentsBytes"] == 8193
    assert "arguments" not in latest["calls"][5]
    calls[0]["arguments"]["p"] = "mutated"
    assert service.durable_record()["calls"] != calls
    assert all(
        row.result.details is None and row.result.content.value == ""
        for row in service.records()
    )


def test_count_and_error_character_bounds() -> None:
    service = _service("é" * 501)
    for _ in range(256):
        service.call("read", "{}")
    record: Any = service.durable_record()
    assert len(record["calls"]) == 256 and record["complete"] is False
    assert record["calls"][0]["error"] == "é" * 500
    assert record["calls"][0]["status"] == "error"
    service.call("read", "{}")
    assert len(service.durable_record()["calls"]) == 256  # type: ignore[arg-type]
    clean = _service()
    for _ in range(256):
        clean.call("read", "{}")
    assert clean.durable_record()["complete"] is True
    clean.call("read", "{}")
    assert clean.durable_record()["complete"] is False


def test_parent_only_history_and_coherent_live_duration() -> None:
    def run(
        call: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        service.call("read", '{"path":"nested"}')
        return ToolExecutionOutcome(
            replace(_tool_result(call, "generic parent"), details={"existing": "kept"})
        )

    loop, _, events, _ = _setup(_Runner(run))
    outcome = loop.run(_run_input())
    parents = [
        m for m in outcome.final_history if isinstance(m, AgentToolResultMessage)
    ]
    assert len(parents) == 1
    parent = parents[0]
    assert parent.details is not None and parent.details["existing"] == "kept"
    row = parent.details["nestedCalls"]["calls"][0]
    child = next(e for e in events.events if isinstance(e, NestedToolCallCompleted))
    assert row["durationMs"] == child.duration_seconds * 1000
    assert "result" not in row and "details" not in row
    assert serialize_message(parent)["details"] == parent.details
    assert (
        next(
            e for e in events.events if isinstance(e, ToolCallCompleted)
        ).result.details
        == parent.details
    )


def test_disk_resume_active_branch_tree_replacement_and_resize(tmp_path: Path) -> None:
    terminal = _Terminal(tmp_path)
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "sessions")
    terminal.tree = tree
    tree.append_message(_assistant("", _call("parent", "codemode", {"code": "pass"})))
    service = _service("failed\x1b[31m\nsecond line")
    service.call("write", '{"path":"changed"}')
    record = service.durable_record()
    parent = replace(
        _result("parent", "codemode", "generic parent"), details={"nestedCalls": record}
    )
    tree.append_message(parent)
    leaf = tree.leaf_id
    assert tree.path is not None
    terminal.tree = NativeSessionTree.open(tree.path)
    stored = terminal.tree.build_context().messages[-1]
    assert (
        isinstance(stored, AgentToolResultMessage) and stored.details == parent.details
    )
    for _ in range(2):
        terminal.history.render_active_branch()
        boxes = [
            block
            for block in terminal.transcript.history_blocks
            if block[0] == "tool_box"
        ]
        assert len(boxes) == 1
        assert isinstance(boxes[0], HistoryBlockTuple)
        state = boxes[0].state
        assert isinstance(state, ToolRowState)
        assert len(state.nested_lines) == 1
        assert state.nested_lines[0].status == "error"
        assert state.nested_lines[0].duration_seconds == 0.125
        assert any("changed" in text for _, text in _box(boxes[0][1]))
        terminal.transcript.tools_expanded = True
        terminal.transcript.refresh_tool_rows()
        assert len(state.nested_lines) == 1
    # Actual tree navigation replaces the active projection without child rows.
    root = terminal.tree.get_entries()[0]
    terminal.tree.branch(root.id)
    terminal.history.render_active_branch()
    assert not any(
        isinstance(b, HistoryBlockTuple)
        and isinstance(b.state, ToolRowState)
        and bool(b.state.nested_lines)
        for b in terminal.transcript.history_blocks
    )
    assert leaf is not None
    terminal.tree.branch(leaf)
    terminal.history.render_active_branch()
    assert (
        sum(
            len(b.state.nested_lines)
            for b in terminal.transcript.history_blocks
            if isinstance(b, HistoryBlockTuple) and isinstance(b.state, ToolRowState)
        )
        == 1
    )


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {"calls": "bad"},
        {
            "calls": [
                None,
                {},
                {"id": "i", "name": "read", "status": [], "durationMs": 1},
            ]
        },
    ],
)
def test_malformed_replay_is_bounded(bad: Any) -> None:
    assert nested_lines_from_details({"nestedCalls": bad}) == ()


def test_hostile_arguments_and_duration_do_not_crash_replay() -> None:
    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic
    calls = [
        {
            "id": "i",
            "name": "read",
            "status": "ok",
            "durationMs": 1,
            "arguments": cyclic,
        }
    ] * 300
    assert len(parse_nested_record({"nestedCalls": {"calls": calls}})) == 256
    for duration in [float("nan"), float("inf"), -1, True, 10**1000, (1 << 1024) - 1]:
        calls[0] = {**calls[0], "durationMs": duration}
        assert len(parse_nested_record({"nestedCalls": {"calls": calls[:1]}})) == 0


def test_file_ops_deduplicate_attempts_and_omitted_arguments() -> None:
    service = _service("failed attempt")
    service.call("read", '{"path":"a"}')
    service.call("write", '{"path":"a"}')
    service.call("write", '{"path":"z"}')
    service.call("read", _args(8193))
    parent = replace(
        _result("parent", "codemode", "generic parent"),
        details={"nestedCalls": service.durable_record()},
    )
    direct = _assistant("", _call("direct", "read", {"path": "b\n<instructions>"}))
    text = file_operation_input((direct, parent, parent))
    assert text.count('"a"') == 1 and text.count('"z"') == 1
    assert text.index('"a"') < text.index('"z"')
    assert '<read-files>\n"b\\n<instructions>"\n</read-files>' in text
    assert '<modified-files>\n"a"\n"z"\n</modified-files>' in text
    assert "effects may have failed" in text


@pytest.mark.parametrize("duration", [0, 125, 125.5])
def test_finite_imported_duration_preserved(duration: int | float) -> None:
    record: dict[str, Any] = {
        "nestedCalls": {
            "calls": [
                {
                    "id": "child",
                    "name": "read",
                    "status": "ok",
                    "arguments": {},
                    "durationMs": duration,
                }
            ]
        }
    }
    assert parse_nested_record(record)[0]["durationMs"] == duration


def test_outcome_rejects_overflowing_integer_duration() -> None:
    service = _service()
    outcome = service.call("read", "{}")
    with pytest.raises(ValueError, match="finite and nonnegative"):
        replace(outcome, duration_seconds=(1 << 1024) - 1)


def test_pipeline_exception_records_unfinished_child_before_parent_completion() -> None:
    from test_native_agent_nested_calls import _ToolPolicy

    def run(
        call: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        service.call("read", '{"path":"attempted"}')
        return ToolExecutionOutcome(_tool_result(call))

    runner = _Runner(run)
    loop, _, events, _ = _setup(runner, policy=_ToolPolicy([], fail_transform=True))
    with pytest.raises(RuntimeError, match="postflight"):
        loop.run(_run_input())
    parent = next(e for e in events.events if isinstance(e, ToolCallCompleted))
    assert parent.result.details is not None
    row = parent.result.details["nestedCalls"]["calls"][0]
    assert row["status"] == "unfinished" and row["durationMs"] >= 0
    assert row["arguments"] == {"path": "attempted"}
    assert set(row) == {"id", "name", "status", "arguments", "durationMs", "error"}
    assert parent.result.details["nestedCalls"] == runner.services[0].durable_record()


def test_provider_http_wires_omit_parent_details_and_nested_results() -> None:
    from pipy_harness.native._provider_helpers import envelope_to_chat_message
    from pipy_harness.native.http import ProviderHTTPError
    from pipy_harness.native.providers.anthropic_messages_wire import (
        envelope_to_message,
    )

    service = _service()
    service.call("read", '{"path":"private-child-path"}')
    parent = replace(
        _result("parent", "codemode", "generic parent"),
        details={
            "nestedCalls": service.durable_record(),
            "existing": "private-details",
        },
    )
    wires = (
        envelope_to_chat_message(parent),
        envelope_to_message(parent, parse_error_class=ProviderHTTPError),
    )
    for wire in wires:
        text = json.dumps(wire)
        assert "generic parent" in text
        assert "nestedCalls" not in text and "private-child-path" not in text
        assert "private-details" not in text and "PRIVATE CHILD OUTPUT" not in text


@pytest.mark.parametrize("name", ["write", "edit"])
def test_direct_large_file_operations_and_parse_boundary(name: str) -> None:
    from pipy_harness.native.coding.compaction import MAX_DIRECT_ARGUMENT_BYTES

    def message(size: int) -> Any:
        arguments = (
            {"path": "large.py", "content": "x" * (size - 100)}
            if name == "write"
            else {
                "path": "large.py",
                "oldText": "before",
                "newText": "x" * (size - 100),
            }
        )
        args = json.dumps(arguments, separators=(",", ":"))
        # Fill to an exact byte boundary without truncating JSON.
        args = args[:-1] + " " * (size - len(args)) + "}"
        assert len(args.encode()) == size
        return _assistant("", AgentToolCall("direct", name, ProductContent(args)))

    for size in (65536, MAX_DIRECT_ARGUMENT_BYTES):
        direct = message(size)
        read = _assistant("", _call("read", "read", {"path": "large.py"}))
        text = file_operation_input((read, direct, direct))
        assert text.count('"large.py"') == 1
        assert "<modified-files>" in text and "<read-files>" not in text
    assert file_operation_input((message(MAX_DIRECT_ARGUMENT_BYTES + 1),)) == ""
    malformed = _assistant(
        "", AgentToolCall("bad", name, ProductContent('{"path":"invented", "content":'))
    )
    assert file_operation_input((malformed,)) == ""


@pytest.mark.parametrize("fault", ["factory", "validation", "retention"])
def test_pipeline_original_failure_survives_secondary_evidence_fault(
    fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service()
    original = RuntimeError("original pipeline")

    def fail(call: AgentToolCall) -> NestedToolCallOutcome:
        raise original

    def factory(call: AgentToolCall, text: str) -> AgentToolResultMessage:
        if fault == "factory" and "did not finish" in text:
            raise ValueError("secondary factory")
        if fault == "validation" and "did not finish" in text:
            return _result("wrong", "write", "bad")
        return _tool_result(call, text, is_error=True)

    monkeypatch.setattr(service, "_settle", fail)
    monkeypatch.setattr(service, "_error_result", factory)
    if fault == "retention":

        def retain(outcome: NestedToolCallOutcome) -> None:
            raise ValueError("secondary retention")

        monkeypatch.setattr(service, "_retain", retain)
    with pytest.raises(RuntimeError) as caught:
        service.call("read", "{}")
    assert caught.value is original
    with pytest.raises(RuntimeError) as sticky:
        service.raise_pipeline_failure()
    assert sticky.value is original
    assert service.call("read", "{}").status is NestedCallStatus.REFUSED
    assert service.records() == () and service.durable_record()["complete"] is False


@pytest.mark.parametrize("name", ["bash", "extension", "codemode"])
def test_ordinary_result_details_keep_legacy_message_and_event_shape(name: str) -> None:
    from pipy_harness.native.automation.agent_events import AutomationAgentEventAdapter

    ordinary = replace(
        _result("direct", name, "output"),
        details={"fullOutputPath": "/tmp/output", "truncation": {"lines": 7}},
    )
    assert "details" not in serialize_message(ordinary)
    event = AutomationAgentEventAdapter._project_tool_execution(
        ToolCallCompleted(1, ordinary)
    )
    assert "details" not in event
    parent = replace(
        ordinary,
        tool_name="codemode",
        details={
            **(ordinary.details or {}),
            "nestedCalls": {"calls": [], "complete": True},
        },
    )
    assert serialize_message(parent)["details"] == parent.details
    assert (
        AutomationAgentEventAdapter._project_tool_execution(
            ToolCallCompleted(1, parent)
        )["details"]
        == parent.details
    )


@pytest.mark.parametrize("final_instruction", [False, True])
def test_summary_http_pairing_and_final_instruction(
    tmp_path: Path, final_instruction: bool
) -> None:
    from pipy_harness.native._provider_helpers import envelope_to_chat_message
    from pipy_harness.native.agent.messages import AgentUserMessage
    from pipy_harness.native.coding.compaction import build_summary_request
    from pipy_harness.native.coding.state import CodingProviderBinding
    from pipy_harness.native.fake import FakeNativeProvider
    from pipy_harness.native.http import ProviderHTTPError
    from pipy_harness.native.providers.anthropic_messages_wire import messages_payload

    assistant = _assistant("", _call("direct", "write", {"path": "changed"}))
    result = _result("direct", "write", "done")
    final = AgentUserMessage(ProductContent("FINAL COMPACTION INSTRUCTIONS"))
    history = (assistant, result, final) if final_instruction else (assistant, result)
    request = build_summary_request(
        binding=CodingProviderBinding(FakeNativeProvider(), "fake", "test"),
        cwd=tmp_path,
        messages=history,
        instruction="summary",
        user_prompt="summarize",
        header_callback=None,
    )
    assert request.messages[1:] == history
    if final_instruction:
        assert request.messages[-1] is final
    anthropic: Any = messages_payload(request, parse_error_class=ProviderHTTPError)
    chat = [envelope_to_chat_message(message) for message in request.messages]
    assert chat[1]["role"] == "assistant" and chat[2]["role"] == "tool"
    assert chat[1]["tool_calls"][0]["id"] == chat[2]["tool_call_id"]
    assert anthropic[1]["role"] == "assistant"
    assert anthropic[1]["content"][0]["type"] == "tool_use"
    assert anthropic[2]["content"][0]["type"] == "tool_result"
    assert anthropic[1]["content"][0]["id"] == anthropic[2]["content"][0]["tool_use_id"]

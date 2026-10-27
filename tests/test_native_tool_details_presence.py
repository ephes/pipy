"""Actual normal result boundaries distinguish missing details from JSON null."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pipy_harness.native.agent.content import ProductContent, ToolPartialResult
from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.tools import ToolExecutor
from pipy_harness.native.automation.serialize import (
    partial_result_json,
    serialize_message,
    tool_result_json,
)
from pipy_harness.native.durable.canonical_codec import (
    decode_canonical,
    encode_canonical,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
)


class DetailsTool:
    definition = ToolDefinition("details", "Return local JSON", {"type": "object"})

    def __init__(self, details: Any) -> None:
        self.details = details

    def invoke(self, request, context):
        return ToolExecutionResult(
            request.tool_request_id,
            "result",
            provider_correlation_id=request.provider_correlation_id,
            details=self.details,
        )


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    for key in ("PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(key, str(tmp_path / key))


@pytest.mark.parametrize("value", [None, False, 0, "scalar", [None, 0, False]])
def test_actual_executor_retains_generic_details_and_explicit_null(tmp_path, value):
    tool = DetailsTool(value)
    result = (
        ToolExecutor({"details": tool})
        .execute(
            AgentToolCall("call", "details", ProductContent("{}")),
            ToolContext(tmp_path),
        )
        .result
    )
    assert not result.is_error
    wire = tool_result_json(result)
    assert "details" in wire
    assert wire["details"] == value
    assert serialize_message(result)["details"] == value


def test_explicit_null_partial_is_not_absent():
    assert partial_result_json(ToolPartialResult("partial", None))["details"] is None


def test_new_null_message_needs_lossless_codec_version_two():
    result = AgentToolResultMessage(
        "pipy-tool-null", "details", ProductContent("x"), "c", details=None
    )
    arena = encode_canonical(result)
    assert arena["codec_version"] == 2
    assert decode_canonical(arena).details is None


def test_session_jsonl_keeps_explicit_null_key(tmp_path):
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "session")
    tree.append_message(
        AgentToolResultMessage(
            "pipy-tool-null", "details", ProductContent("x"), "c", details=None
        )
    )
    rows = [json.loads(line) for line in tree.path.read_text().splitlines()]
    assert rows[-1]["message"]["details"] is None


def test_absent_final_does_not_fall_back_to_partial_in_shared_executor(tmp_path):
    from pipy_harness.native.tools.bash import BashTool

    updates = []
    result = (
        ToolExecutor({"bash": BashTool()})
        .execute(
            AgentToolCall(
                "c", "bash", ProductContent(json.dumps({"command": "printf result"}))
            ),
            ToolContext(tmp_path, output_sink=updates.append),
        )
        .result
    )
    assert updates and isinstance(updates[-1], ToolPartialResult)
    assert updates[-1].details == {}
    assert "details" not in tool_result_json(result)


def test_result_owns_details_after_caller_mutates_input(tmp_path):
    source = {"items": [1]}
    result = (
        ToolExecutor({"details": DetailsTool(source)})
        .execute(
            AgentToolCall("c", "details", ProductContent("{}")), ToolContext(tmp_path)
        )
        .result
    )
    source["items"].append(2)
    assert result.details == {"items": [1]}

"""Offline production-tool edit/check continuity across a durable session reopen."""

from __future__ import annotations

import io
import json
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.session_tree import NativeSessionTree


class _DailyUseProvider:
    """Record canonical requests and emit only the supplied test-local script."""

    name = "fake"
    model_id = "fake-daily-use"
    supports_tool_calls = True

    def __init__(self, calls: tuple[ProviderToolCall, ...], final_text: str) -> None:
        self.calls = calls
        self.final_text = final_text
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        index = len(self.requests)
        self.requests.append(request)
        assert index <= len(self.calls), "unexpected additional provider request"
        calls = (self.calls[index],) if index < len(self.calls) else ()
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            tool_calls=calls,
            final_text="" if calls else self.final_text,
        )


def test_production_edit_check_and_durable_reopen_preserve_exact_context(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "answer.py"
    original = "def answer():\n    return 41\n"
    corrected = "def answer():\n    return 42\n"
    target.write_text(original, encoding="utf-8")
    (workspace / "check.py").write_text(
        "from pathlib import Path\n"
        "from answer import answer\n"
        "with Path('check-runs.txt').open('a') as runs:\n"
        "    runs.write('run\\n')\n"
        "assert answer() == 42\n"
        "print('DAILY_CHECK_PASSED_7f3')\n",
        encoding="utf-8",
    )
    prompt = "DAILY_TASK_7f3: fix answer.py to return 42 and run check.py."
    final_text = "DAILY_DONE_7f3: corrected answer.py and verified the check."
    continuation = (
        "DAILY_CONTINUE_7f3: report the verified result without rerunning tools."
    )
    calls = (
        ProviderToolCall("daily-read", "read", json.dumps({"path": "answer.py"})),
        ProviderToolCall(
            "daily-edit",
            "edit",
            json.dumps(
                {
                    "path": "answer.py",
                    "old_string": "return 41",
                    "new_string": "return 42",
                }
            ),
        ),
        ProviderToolCall(
            "daily-check",
            "bash",
            json.dumps(
                {
                    "command": f"{shlex.quote(sys.executable)} -E -s check.py",
                    "timeout": 10,
                }
            ),
        ),
    )
    tree = NativeSessionTree.create(workspace, session_dir=tmp_path / "sessions")
    provider = _DailyUseProvider(calls, final_text)
    result = CodingSession(provider=provider, native_session=tree).run(
        workspace_root=workspace,
        input_stream=io.StringIO(f"{prompt}\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 3
    assert len(provider.requests) == 4
    assert target.read_text(encoding="utf-8") == corrected
    check_runs = workspace / "check-runs.txt"
    assert check_runs.read_text(encoding="utf-8") == "run\n"

    # Every tool observation reaches the very next request, preserving the
    # complete prefix and the exact provider call/result correlation.
    expected: list[AgentMessage] = [AgentUserMessage(ProductContent(prompt))]
    assert provider.requests[0].messages == tuple(expected)
    observations: list[AgentToolResultMessage] = []
    for index, call in enumerate(calls):
        next_messages = provider.requests[index + 1].messages
        observation = next_messages[-1]
        assert isinstance(observation, AgentToolResultMessage)
        assert observation.tool_name == call.tool_name
        assert observation.provider_correlation_id == call.provider_correlation_id
        assert observation.is_error is False
        observations.append(observation)
        expected.extend(
            (
                AgentAssistantMessage(
                    ProductContent(""),
                    (
                        AgentToolCall(
                            call.provider_correlation_id,
                            call.tool_name,
                            ProductContent(call.arguments_json),
                        ),
                    ),
                ),
                observation,
            )
        )
        assert next_messages == tuple(expected)
    assert len({observation.tool_request_id for observation in observations}) == 3
    assert "return 41" in observations[0].content.value
    assert observations[1].content.value == "edited answer.py (1 replacement(s))"
    # BashTool.is_error=False includes nonzero command exits; inspect the
    # actual exit status and output instead of trusting the scripted answer.
    assert observations[2].content.value == (
        "exit code: 0\n[output]\nDAILY_CHECK_PASSED_7f3\n"
    )
    expected.append(AgentAssistantMessage(ProductContent(final_text)))

    assert tree.path is not None
    durable_body = tree.path.read_text(encoding="utf-8")
    assert prompt in durable_body
    assert "DAILY_CHECK_PASSED_7f3" in durable_body
    assert final_text in durable_body
    target_mtime = target.stat().st_mtime_ns
    reopened = NativeSessionTree.open(tree.path)
    resumed_provider = _DailyUseProvider((), "DAILY_RESUMED_7f3")
    resumed_result = CodingSession(
        provider=resumed_provider, native_session=reopened
    ).run(
        workspace_root=workspace,
        input_stream=io.StringIO(f"{continuation}\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert resumed_result.status is HarnessStatus.SUCCEEDED
    assert resumed_result.tool_invocation_count == 0
    assert len(resumed_provider.requests) == 1
    expected.append(AgentUserMessage(ProductContent(continuation)))
    assert resumed_provider.requests[0].messages == tuple(expected)
    assert target.read_text(encoding="utf-8") == corrected
    assert target.stat().st_mtime_ns == target_mtime
    assert check_runs.read_text(encoding="utf-8") == "run\n"
    for metadata_result in (result, resumed_result):
        assert "DAILY_" not in str(metadata_result)
        assert "return 41" not in str(metadata_result)
        assert "return 42" not in str(metadata_result)

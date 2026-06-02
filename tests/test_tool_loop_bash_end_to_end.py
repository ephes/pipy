"""Product-path tool-loop tests for the registered ``bash`` tool.

These drive ``NativeToolReplSession.run`` with a scripted tool-capable
provider and the real ``production_tool_registry`` so the test exercises the
exact dispatch the product uses: provider emits a ``bash`` tool call, the loop
runs it through the sandbox, feeds the observation back, and the provider
returns final text. A second case proves a sandbox refusal is surfaced as a
tool error the loop counts as malformed.
"""

from __future__ import annotations

import dataclasses
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
)
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.tool_loop_session import (
    NativeToolReplResult,
    NativeToolReplSession,
    production_tool_registry,
)


class _StubToolProvider:
    def __init__(self, results: list[ProviderResult]) -> None:
        self._results = list(results)
        self.name = "stub-tool"
        self.model_id = "stub-model"
        self.requests: list[ProviderRequest] = []

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
    ) -> ProviderResult:
        del stream_sink, reasoning_sink
        self.requests.append(request)
        if not self._results:
            raise AssertionError("stub provider exhausted")
        return self._results.pop(0)


def _provider_result(
    *,
    tool_calls: tuple[ProviderToolCall, ...] = (),
    final_text: str | None = None,
) -> ProviderResult:
    now = datetime.now(UTC)
    return ProviderResult(
        status=HarnessStatus.SUCCEEDED,
        provider_name="stub-tool",
        model_id="stub-model",
        started_at=now,
        ended_at=now,
        final_text=final_text,
        tool_calls=tool_calls,
    )


def _run(
    *, results: list[ProviderResult], workspace: Path
) -> tuple[NativeToolReplResult, str, _StubToolProvider]:
    provider = _StubToolProvider(results)
    session = NativeToolReplSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=10,
    )
    output_stream = io.StringIO()
    error_stream = io.StringIO()
    result = session.run(
        workspace_root=workspace,
        input_stream=io.StringIO("inspect the repo\n"),
        output_stream=output_stream,
        error_stream=error_stream,
    )
    return result, output_stream.getvalue(), provider


def test_bash_tool_call_runs_and_feeds_observation(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello from notes\n", encoding="utf-8")
    call = ProviderToolCall(
        provider_correlation_id="call-1",
        tool_name="bash",
        arguments_json=json.dumps({"command": "cat notes.txt"}),
    )
    results = [
        _provider_result(tool_calls=(call,)),
        _provider_result(final_text="the notes say hello"),
    ]
    result, output, provider = _run(results=results, workspace=tmp_path)

    assert result.status is HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 1
    assert result.malformed_argument_count == 0
    assert "the notes say hello" in output

    # The observation fed back to the provider contains the command output.
    second_request = provider.requests[1]
    assert "hello from notes" in _all_text(second_request)


def test_bash_tool_runs_a_real_shell_pipeline(tmp_path: Path) -> None:
    # The bash tool is a real shell (Pi parity): a pipeline that the old
    # read-only inspection tool would have refused now runs and its output is
    # fed back to the provider as a normal (non-malformed) observation.
    call = ProviderToolCall(
        provider_correlation_id="call-1",
        tool_name="bash",
        arguments_json=json.dumps({"command": "echo hello | tr a-z A-Z"}),
    )
    results = [
        _provider_result(tool_calls=(call,)),
        _provider_result(final_text="understood"),
    ]
    result, output, provider = _run(results=results, workspace=tmp_path)

    assert result.status is HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 1
    assert result.malformed_argument_count == 0
    assert "HELLO" in _all_text(provider.requests[1])


def _all_text(obj: Any) -> str:
    acc: list[str] = []
    _collect_text(obj, acc)
    return "\n".join(acc)


def _collect_text(obj: Any, acc: list[str]) -> None:
    if isinstance(obj, str):
        acc.append(obj)
    elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for f in dataclasses.fields(obj):
            _collect_text(getattr(obj, f.name), acc)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _collect_text(item, acc)
    elif isinstance(obj, dict):
        for value in obj.values():
            _collect_text(value, acc)

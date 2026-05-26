"""Tests for the Pi-parity streaming, tool-block rendering, and reference-root
inspection slice on top of `NativeToolReplSession`.

These tests cover behavior the previous tool-loop suite did not pin:

- The renderer streams provider text deltas to ``output_stream`` and the
  bounded tool-loop suppresses the buffered ``final_text`` echo when
  streaming covered it (so users never see the answer twice).
- Tool calls render a styled `→ <tool>(<arg-preview>)` header and a
  styled `↳` result block on ``error_stream`` (visible alongside the
  bottom-status footer).
- ANSI escapes are disabled on captured (non-TTY) streams and when
  ``NO_COLOR`` is set, so test logs and pipes stay readable.
- The tool-loop deflects "I cannot inspect" answers by actually invoking
  tools and letting their results steer the final answer.
- Reference roots let read-only tools resolve absolute paths under a
  trusted sibling project while keeping the workspace `.git` / secrets
  defenses intact.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO, cast

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
    FakeNativeProvider,
    NativeToolReplSession,
    ProviderToolCall,
)
from pipy_harness.native.tool_loop_session import _ToolLoopRenderer
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
)


class _StreamingStub:
    """Stub stream that records writes and optionally reports as a TTY."""

    def __init__(self, *, isatty: bool = False) -> None:
        self._buffer = io.StringIO()
        self._isatty = isatty

    def write(self, text: str) -> int:
        return self._buffer.write(text)

    def flush(self) -> None:
        self._buffer.flush()

    def isatty(self) -> bool:
        return self._isatty

    def getvalue(self) -> str:
        return self._buffer.getvalue()


@pytest.fixture(autouse=True)
def _clear_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure NO_COLOR does not silently disable ANSI in TTY tests."""

    monkeypatch.delenv("NO_COLOR", raising=False)


def _make_call(tool_name: str, arguments_json: str) -> ProviderToolCall:
    return ProviderToolCall(
        provider_correlation_id=f"call_{tool_name}",
        tool_name=tool_name,
        arguments_json=arguments_json,
    )


def _run_loop(
    *,
    tool_calls_script: tuple[tuple[ProviderToolCall, ...], ...],
    text_chunks: tuple[str, ...],
    tool_registry: Mapping[str, ToolPort],
    user_inputs: tuple[str, ...],
    tmp_path: Path,
    reference_roots: tuple[Path, ...] = (),
) -> tuple[str, str]:
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=tool_calls_script,
        programmable_text_chunks=text_chunks,
    )
    session = NativeToolReplSession(
        provider=provider,
        tool_registry=dict(tool_registry),
        tool_budget=5,
        reference_roots=reference_roots,
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
    assert result.status == HarnessStatus.SUCCEEDED
    return output_stream.getvalue(), error_stream.getvalue()


# ------------------------------ renderer unit ------------------------------


def test_renderer_streams_chunks_to_output_stream_with_assistant_prefix():
    out = _StreamingStub(isatty=False)
    err = _StreamingStub(isatty=False)
    renderer = _ToolLoopRenderer(output_stream=cast(TextIO, out), error_stream=cast(TextIO, err))

    renderer.begin_provider_turn()
    renderer.stream_sink("hello ")
    renderer.stream_sink("world")
    renderer.end_provider_turn(final_text="hello world", has_tool_calls=False)

    assert renderer.streamed_any is True
    assert "assistant > " in out.getvalue()
    assert "hello world" in out.getvalue()
    assert err.getvalue() == ""


def test_renderer_renders_tool_call_header_and_result_on_error_stream():
    out = _StreamingStub(isatty=False)
    err = _StreamingStub(isatty=False)
    renderer = _ToolLoopRenderer(output_stream=cast(TextIO, out), error_stream=cast(TextIO, err))

    renderer.render_tool_call(
        ProviderToolCall(
            provider_correlation_id="cc",
            tool_name="read",
            arguments_json='{"path": "docs/backlog.md"}',
        )
    )
    renderer.render_tool_result(
        output_text="line one\nline two\nline three", is_error=False
    )

    rendered = err.getvalue()
    assert "→ read(" in rendered
    assert 'path="docs/backlog.md"' in rendered
    assert "↳" in rendered
    assert "line one" in rendered
    assert "line two" in rendered


def test_renderer_renders_tool_result_error_with_error_tag():
    out = _StreamingStub(isatty=False)
    err = _StreamingStub(isatty=False)
    renderer = _ToolLoopRenderer(output_stream=cast(TextIO, out), error_stream=cast(TextIO, err))

    renderer.render_tool_result(
        output_text="read error: path is ignored", is_error=True
    )

    rendered = err.getvalue()
    assert "↳ [error]" in rendered
    assert "path is ignored" in rendered


def test_renderer_truncates_long_result_with_more_line_count():
    out = _StreamingStub(isatty=False)
    err = _StreamingStub(isatty=False)
    renderer = _ToolLoopRenderer(output_stream=cast(TextIO, out), error_stream=cast(TextIO, err))

    long_body = "\n".join(f"row {index}" for index in range(20))
    renderer.render_tool_result(output_text=long_body, is_error=False)

    rendered = err.getvalue()
    assert "row 0" in rendered
    assert "row 11" in rendered
    assert "+8 more line(s)" in rendered


def test_renderer_disables_ansi_on_non_tty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    out = _StreamingStub(isatty=False)
    err = _StreamingStub(isatty=False)
    renderer = _ToolLoopRenderer(output_stream=cast(TextIO, out), error_stream=cast(TextIO, err))

    renderer.render_tool_call(_make_call("ls", '{"path": "."}'))

    assert "\x1b[" not in err.getvalue()


def test_renderer_disables_ansi_under_no_color(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NO_COLOR", "1")
    out = _StreamingStub(isatty=True)
    err = _StreamingStub(isatty=True)
    renderer = _ToolLoopRenderer(output_stream=cast(TextIO, out), error_stream=cast(TextIO, err))

    renderer.render_tool_call(_make_call("ls", '{"path": "."}'))
    renderer.render_tool_result(output_text="ok", is_error=False)

    assert "\x1b[" not in err.getvalue()


def test_renderer_enables_ansi_on_tty_with_color(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    out = _StreamingStub(isatty=True)
    err = _StreamingStub(isatty=True)
    renderer = _ToolLoopRenderer(output_stream=cast(TextIO, out), error_stream=cast(TextIO, err))

    renderer.render_tool_call(_make_call("ls", '{"path": "."}'))
    renderer.render_tool_result(output_text="ok", is_error=False)

    assert "\x1b[" in err.getvalue()


def test_renderer_argument_preview_handles_invalid_json():
    out = _StreamingStub(isatty=False)
    err = _StreamingStub(isatty=False)
    renderer = _ToolLoopRenderer(output_stream=cast(TextIO, out), error_stream=cast(TextIO, err))

    renderer.render_tool_call(
        ProviderToolCall(
            provider_correlation_id="cc",
            tool_name="read",
            arguments_json="this is not json",
        )
    )

    assert "→ read(this is not json)" in err.getvalue()


# ---------------------- streaming integration with tool loop ---------------


class _NoOpTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="noop",
            description="No-op tool used to drive the loop.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )

    def invoke(
        self, request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        del context
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text="ok",
            provider_correlation_id=request.provider_correlation_id,
        )


def test_tool_loop_streams_provider_text_chunks_into_output_stream(
    tmp_path: Path,
):
    text_chunks = ("the ", "answer ", "is ", "42")
    output, _error = _run_loop(
        tool_calls_script=((),),  # one call, no tools -> final answer
        text_chunks=text_chunks,
        tool_registry={},
        user_inputs=("explain the meaning of life",),
        tmp_path=tmp_path,
    )

    assert "assistant > " in output
    # All chunks appear in order on the output stream.
    expected_text = "".join(text_chunks)
    assert expected_text in output
    # The buffered final_text is not duplicated after the streamed text.
    assert output.count(expected_text) == 1


def test_tool_loop_renders_tool_block_on_error_stream(tmp_path: Path):
    tool_calls_script: tuple[tuple[ProviderToolCall, ...], ...] = (
        (_make_call("noop", "{}"),),
        (),
    )
    _output, error = _run_loop(
        tool_calls_script=tool_calls_script,
        text_chunks=("done",),
        tool_registry={"noop": _NoOpTool()},
        user_inputs=("run noop please",),
        tmp_path=tmp_path,
    )

    assert "→ noop(" in error
    assert "↳" in error


def test_tool_loop_does_not_answer_i_cannot_inspect_when_inspection_available(
    tmp_path: Path,
):
    # The fake provider streams a real answer that uses the tool result.
    tool_calls_script: tuple[tuple[ProviderToolCall, ...], ...] = (
        (_make_call("noop", "{}"),),
        (),
    )
    chunks = ("Workspace inspection succeeded: parity is 49/50.",)
    output, _error = _run_loop(
        tool_calls_script=tool_calls_script,
        text_chunks=chunks,
        tool_registry={"noop": _NoOpTool()},
        user_inputs=("where are we?",),
        tmp_path=tmp_path,
    )

    refusal_markers = (
        "cannot inspect",
        "constrained not to inspect",
        "I'm constrained not to inspect",
        "do not execute tools",
    )
    for marker in refusal_markers:
        assert marker not in output


# ------------------------- reference root acceptance -----------------------


def test_reference_root_lets_read_tool_open_absolute_path(tmp_path: Path):
    from pipy_harness.native.tools.read import ReadTool

    ref_root = tmp_path / "sibling"
    ref_root.mkdir()
    (ref_root / "notes.md").write_text("# sibling docs\nbody\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    tool = ReadTool()
    context = ToolContext(
        workspace_root=workspace,
        reference_roots=(ref_root,),
    )
    request = ToolRequest(
        tool_request_id="pipy-tool-test-1",
        tool_name="read",
        arguments={"path": str(ref_root / "notes.md")},
    )
    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "# sibling docs" in result.output_text


def test_read_tool_refuses_absolute_path_outside_any_root(tmp_path: Path):
    from pipy_harness.native.tools.read import ReadTool

    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "secrets.txt").write_text("nothing here", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    tool = ReadTool()
    context = ToolContext(workspace_root=workspace)
    request = ToolRequest(
        tool_request_id="pipy-tool-test-2",
        tool_name="read",
        arguments={"path": str(other / "secrets.txt")},
    )

    from pipy_harness.native.tools.base import ToolArgumentError

    with pytest.raises(ToolArgumentError, match="outside the workspace"):
        tool.invoke(request, context)


def test_reference_root_preserves_git_default_deny(tmp_path: Path):
    from pipy_harness.native.tools.read import ReadTool

    ref_root = tmp_path / "sibling"
    git_dir = ref_root / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "config").write_text("[core]\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    tool = ReadTool()
    context = ToolContext(
        workspace_root=workspace,
        reference_roots=(ref_root,),
    )
    request = ToolRequest(
        tool_request_id="pipy-tool-test-3",
        tool_name="read",
        arguments={"path": str(git_dir / "config")},
    )
    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "ignored or under .git" in result.output_text


def test_reference_root_preserves_secret_content_check(tmp_path: Path):
    from pipy_harness.native.tools.read import ReadTool

    ref_root = tmp_path / "sibling"
    ref_root.mkdir()
    (ref_root / "leaky.txt").write_text(
        "api_key=AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8"
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    tool = ReadTool()
    context = ToolContext(
        workspace_root=workspace,
        reference_roots=(ref_root,),
    )
    request = ToolRequest(
        tool_request_id="pipy-tool-test-4",
        tool_name="read",
        arguments={"path": str(ref_root / "leaky.txt")},
    )
    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "secret-looking" in result.output_text

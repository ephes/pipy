"""Tests for the `bash` tool: bounded shell command execution."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pipy_harness.native.tools import (
    ToolArgumentError,
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.bash import TRUNCATION_MARKER, BashTool


def _make_request(arguments: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="bash",
        arguments=arguments,
    )


def test_bash_tool_satisfies_tool_port_protocol() -> None:
    tool = BashTool()

    assert isinstance(tool, ToolPort)


def test_bash_tool_definition_requires_command_only() -> None:
    tool = BashTool()

    schema = tool.definition.input_schema

    assert schema["type"] == "object"
    assert schema["required"] == ["command"]
    assert "timeout_seconds" in schema["properties"]
    assert schema["additionalProperties"] is False


def test_bash_tool_runs_command_in_workspace_root(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello world\n", encoding="utf-8")
    tool = BashTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"command": "cat hello.txt"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "exit_code=0" in result.output_text
    assert "hello world" in result.output_text


def test_bash_tool_captures_non_zero_exit_with_stderr(tmp_path: Path) -> None:
    tool = BashTool()
    context = ToolContext(workspace_root=tmp_path)
    # `false` returns exit code 1; combine with a stderr write to cover both
    request = _make_request({"command": "echo boom >&2; false"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "exit_code=1" in result.output_text
    assert "boom" in result.output_text


def test_bash_tool_refuses_command_with_dot_git_path(tmp_path: Path) -> None:
    tool = BashTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"command": "cat .git/config"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "command refused" in result.output_text


def test_bash_tool_refuses_command_with_git_dir_flag(tmp_path: Path) -> None:
    tool = BashTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"command": "git --git-dir=/tmp/x status"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "command refused" in result.output_text


def test_bash_tool_truncates_oversized_stdout(tmp_path: Path) -> None:
    tool = BashTool(max_stdout_bytes=64)
    context = ToolContext(workspace_root=tmp_path)
    # produce 200 bytes of output
    request = _make_request({"command": "printf '%.0sA' {1..200}"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert TRUNCATION_MARKER in result.output_text


def test_bash_tool_truncates_oversized_stderr(tmp_path: Path) -> None:
    tool = BashTool(max_stderr_bytes=64)
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request(
        {"command": "printf '%.0sE' {1..200} >&2"}
    )

    result = tool.invoke(request, context)

    # exit code 0; stderr truncated marker should appear
    assert TRUNCATION_MARKER in result.output_text


def test_bash_tool_enforces_timeout(tmp_path: Path) -> None:
    tool = BashTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"command": "sleep 5", "timeout_seconds": 1})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "timed out" in result.output_text


def test_bash_tool_caps_timeout_at_max(tmp_path: Path) -> None:
    tool = BashTool(default_timeout_seconds=1.0, max_timeout_seconds=2.0)
    context = ToolContext(workspace_root=tmp_path)
    # request 10s but should be capped to 2s; sleep 5 should still time out
    request = _make_request({"command": "sleep 5", "timeout_seconds": 10})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "timed out" in result.output_text
    assert "after 2s" in result.output_text


def test_bash_tool_rejects_empty_command(tmp_path: Path) -> None:
    tool = BashTool()
    context = ToolContext(workspace_root=tmp_path)

    with pytest.raises(ToolArgumentError) as exc_info:
        tool.invoke(_make_request({"command": "   "}), context)

    assert "non-empty" in str(exc_info.value)


def test_bash_tool_rejects_non_string_command(tmp_path: Path) -> None:
    tool = BashTool()
    context = ToolContext(workspace_root=tmp_path)

    with pytest.raises(ToolArgumentError):
        tool.invoke(_make_request({"command": 42}), context)


def test_bash_tool_rejects_non_positive_timeout(tmp_path: Path) -> None:
    tool = BashTool()
    context = ToolContext(workspace_root=tmp_path)

    with pytest.raises(ToolArgumentError):
        tool.invoke(
            _make_request({"command": "echo ok", "timeout_seconds": 0}),
            context,
        )


def test_bash_tool_construction_validates_bounds() -> None:
    with pytest.raises(ValueError):
        BashTool(default_timeout_seconds=0)
    with pytest.raises(ValueError):
        BashTool(max_timeout_seconds=0.5, default_timeout_seconds=1.0)
    with pytest.raises(ValueError):
        BashTool(max_stdout_bytes=0)
    with pytest.raises(ValueError):
        BashTool(max_stderr_bytes=0)
    with pytest.raises(ValueError):
        BashTool(default_timeout_seconds=10_000)


def test_bash_tool_handles_command_that_writes_both_streams(
    tmp_path: Path,
) -> None:
    tool = BashTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request(
        {"command": "echo out; echo err >&2"}
    )

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "out" in result.output_text
    assert "err" in result.output_text


def test_bash_tool_runs_python_interpreter(tmp_path: Path) -> None:
    tool = BashTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request(
        {"command": f"{sys.executable} -c 'print(2 + 2)'"}
    )

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "4" in result.output_text

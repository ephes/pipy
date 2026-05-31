"""Focused tests for the model-visible ``bash`` tool.

The tool is a thin provider-facing adapter over the shared command substrate
(:mod:`pipy_harness.native.command_sandbox`); these tests pin the observation
shaping and the budget/error contract the tool loop relies on. The deeper
containment guarantees live in ``test_command_sandbox.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pipy_harness.native.tools.base import (
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.bash import BashTool


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext(workspace_root=workspace.resolve())


def _request(arguments: dict[str, Any]) -> ToolRequest:
    return ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="bash",
        arguments=arguments,
        provider_correlation_id="prov-1",
    )


def test_bash_tool_is_a_toolport() -> None:
    tool = BashTool()
    assert isinstance(tool, ToolPort)
    definition = tool.definition
    assert definition.name == "bash"
    assert definition.input_schema["properties"]["command"]["type"] == "string"


def test_runs_allowed_command(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello world\n", encoding="utf-8")
    result = BashTool().invoke(_request({"command": "cat a.txt"}), _ctx(tmp_path))
    assert result.is_error is False
    assert "hello world" in result.output_text
    assert result.provider_correlation_id == "prov-1"


def test_nonzero_exit_is_not_a_tool_error(tmp_path: Path) -> None:
    # A command that exits non-zero is a normal observation the model reacts to,
    # not a malformed tool call; it must not trip the malformed streak.
    result = BashTool().invoke(
        _request({"command": "cat does-not-exist.txt"}), _ctx(tmp_path)
    )
    assert result.is_error is False
    assert "exit code" in result.output_text.lower()


def test_blocks_git_access(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    result = BashTool().invoke(_request({"command": "cat .git/config"}), _ctx(tmp_path))
    assert result.is_error is True
    assert "unsafe_path_argument" in result.output_text


def test_blocks_command_substitution(tmp_path: Path) -> None:
    result = BashTool().invoke(
        _request({"command": "echo $(cat a.txt)"}), _ctx(tmp_path)
    )
    assert result.is_error is True
    assert "shell_metacharacters" in result.output_text


def test_blocks_disallowed_executable(tmp_path: Path) -> None:
    result = BashTool().invoke(_request({"command": "python3 -c pass"}), _ctx(tmp_path))
    assert result.is_error is True
    assert "disallowed_executable" in result.output_text


def test_times_out(tmp_path: Path) -> None:
    tool = BashTool(timeout_seconds=0.5, allowed_executables=frozenset({"sleep"}))
    result = tool.invoke(_request({"command": "sleep 5"}), _ctx(tmp_path))
    assert result.is_error is True
    assert "timed out" in result.output_text.lower()


def test_bounds_output(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("x" * 5000 + "\n", encoding="utf-8")
    tool = BashTool(max_output_bytes=200)
    result = tool.invoke(_request({"command": "cat big.txt"}), _ctx(tmp_path))
    assert result.is_error is False
    assert "truncated" in result.output_text.lower()


def test_redacts_secret_shaped_output(tmp_path: Path) -> None:
    (tmp_path / "creds.txt").write_text(
        "aws_key = AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8"
    )
    result = BashTool().invoke(_request({"command": "cat creds.txt"}), _ctx(tmp_path))
    assert result.is_error is False
    assert "AKIAIOSFODNN7EXAMPLE" not in result.output_text
    assert "redacted" in result.output_text.lower()

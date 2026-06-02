"""Focused tests for the model-visible ``bash`` tool.

The tool is a real shell, matching Pi: it runs an arbitrary bash command in the
workspace and returns combined stdout/stderr bounded to the model. These tests
pin real execution (pipes, substitution, any executable, non-zero exit,
timeout) and the budget/error contract the tool loop relies on. Every case here
would have been *refused* by the previous read-only inspection tool, so they
also pin the move to genuine shell parity.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pipy_harness.native.tools.base import (
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.bash import BashTool


def _ctx(
    workspace: Path, *, output_sink: Callable[[str], None] | None = None
) -> ToolContext:
    return ToolContext(workspace_root=workspace.resolve(), output_sink=output_sink)


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
    assert definition.input_schema["properties"]["timeout"]["type"] == "integer"


def test_runs_a_command(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello world\n", encoding="utf-8")
    result = BashTool().invoke(_request({"command": "cat a.txt"}), _ctx(tmp_path))
    assert result.is_error is False
    assert "hello world" in result.output_text
    assert "exit code: 0" in result.output_text
    assert result.provider_correlation_id == "prov-1"


def test_runs_a_pipeline(tmp_path: Path) -> None:
    # Pipes were refused by the old read-only tool; a real shell runs them.
    result = BashTool().invoke(
        _request({"command": "echo hello | tr a-z A-Z"}), _ctx(tmp_path)
    )
    assert result.is_error is False
    assert "HELLO" in result.output_text


def test_runs_command_substitution_and_chaining(tmp_path: Path) -> None:
    result = BashTool().invoke(
        _request({"command": "echo $(echo nested) && echo done"}), _ctx(tmp_path)
    )
    assert result.is_error is False
    assert "nested" in result.output_text
    assert "done" in result.output_text


def test_can_read_git_directory(tmp_path: Path) -> None:
    # The old tool default-denied .git; a real shell (like Pi) can read it.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    result = BashTool().invoke(
        _request({"command": "cat .git/config"}), _ctx(tmp_path)
    )
    assert result.is_error is False
    assert "[core]" in result.output_text


def test_combines_stdout_and_stderr(tmp_path: Path) -> None:
    result = BashTool().invoke(
        _request({"command": "echo out; echo err 1>&2"}), _ctx(tmp_path)
    )
    assert result.is_error is False
    assert "out" in result.output_text
    assert "err" in result.output_text


def test_nonzero_exit_is_not_a_tool_error(tmp_path: Path) -> None:
    # A non-zero exit is a normal observation the model reacts to, not a
    # malformed tool call; it must not trip the malformed streak.
    result = BashTool().invoke(
        _request({"command": "echo boom; exit 3"}), _ctx(tmp_path)
    )
    assert result.is_error is False
    assert "exit code: 3" in result.output_text
    assert "boom" in result.output_text


def test_runs_in_the_workspace_root(tmp_path: Path) -> None:
    result = BashTool().invoke(_request({"command": "pwd"}), _ctx(tmp_path))
    assert result.is_error is False
    assert str(tmp_path.resolve()) in result.output_text


def test_bounds_large_output(tmp_path: Path) -> None:
    tool = BashTool(max_output_bytes=256)
    result = tool.invoke(
        _request({"command": "for i in $(seq 1 5000); do echo line $i; done"}),
        _ctx(tmp_path),
    )
    assert result.is_error is False
    assert "(output truncated)" in result.output_text
    assert len(result.output_text.encode("utf-8")) < 2000


def test_times_out(tmp_path: Path) -> None:
    result = BashTool().invoke(
        _request({"command": "sleep 30", "timeout": 1}), _ctx(tmp_path)
    )
    assert result.is_error is True
    assert "timed out" in result.output_text.lower()


def test_streams_output_incrementally_before_process_exits(tmp_path: Path) -> None:
    # Pi-style live streaming: the first chunk must reach the sink while the
    # command is still running, not all at once at the end. The command prints
    # AAA, sleeps, then prints BBB — so a streaming reader sees AAA ~0.6s before
    # the process finishes, while a buffer-until-exit reader would not.
    received: list[tuple[float, str]] = []

    def sink(chunk: str) -> None:
        received.append((time.monotonic(), chunk))

    ctx = _ctx(tmp_path, output_sink=sink)
    result = BashTool().invoke(
        _request({"command": "printf AAA; sleep 0.6; printf BBB"}), ctx
    )
    finished_at = time.monotonic()

    assert result.is_error is False
    streamed = "".join(chunk for _, chunk in received)
    assert "AAA" in streamed
    assert "BBB" in streamed
    # The first emission (AAA) landed well before the command finished.
    first_emit_at = received[0][0]
    assert finished_at - first_emit_at >= 0.4


def test_timeout_enforced_when_stdout_closed_early(tmp_path: Path) -> None:
    # A command can close its stdout/stderr and keep running. The timeout must
    # still fire (the process group is killed) rather than blocking invoke()
    # for the full runtime after the pipe hits EOF.
    start = time.monotonic()
    result = BashTool().invoke(
        _request({"command": "exec 1>&- 2>&-; sleep 30", "timeout": 1}),
        _ctx(tmp_path),
    )
    elapsed = time.monotonic() - start
    assert result.is_error is True
    assert "timed out" in result.output_text.lower()
    assert elapsed < 5  # must not wait for the full 30s sleep


def test_high_volume_output_is_bounded_not_accumulated(tmp_path: Path) -> None:
    # A noisy producer must not be retained in full; the returned result keeps
    # only a bounded tail (memory stays bounded during capture).
    tool = BashTool(max_output_bytes=1024)
    result = tool.invoke(
        _request({"command": "for i in $(seq 1 200000); do echo line $i; done"}),
        _ctx(tmp_path),
    )
    assert result.is_error is False
    assert "(output truncated)" in result.output_text
    assert len(result.output_text.encode("utf-8")) < 4096
    # The tail is retained, so the last lines are present.
    assert "line 200000" in result.output_text


def test_no_sink_still_returns_full_output(tmp_path: Path) -> None:
    # Streaming is optional: with no output_sink the tool still returns the
    # complete bounded result.
    result = BashTool().invoke(
        _request({"command": "printf AAA; sleep 0.2; printf BBB"}), _ctx(tmp_path)
    )
    assert result.is_error is False
    assert "AAABBB" in result.output_text

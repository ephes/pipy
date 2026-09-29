"""Focused tests for the model-visible ``bash`` tool.

The tool is a real shell, matching Pi: it runs an arbitrary bash command in the
workspace and returns combined stdout/stderr bounded to the model. These tests
pin real execution (pipes, substitution, any executable, non-zero exit,
timeout) and the budget/error contract the tool loop relies on. Every case here
would have been *refused* by the previous read-only inspection tool, so they
also pin the move to genuine shell parity.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import threading
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
from pipy_harness.native.tools.bash import (
    BashTool,
    _ByteTail,
    _stream_output,
    run_local_command,
)
from pipy_harness.native.tools.output_accumulator import OutputAccumulator


def _ctx(
    workspace: Path,
    *,
    output_sink: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> ToolContext:
    return ToolContext(
        workspace_root=workspace.resolve(),
        output_sink=output_sink,
        cancel_event=cancel_event,
    )


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
    result = BashTool().invoke(_request({"command": "cat .git/config"}), _ctx(tmp_path))
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


def _notice_path(output_text: str) -> Path:
    match = re.search(r"Full output: (\S+)\]$", output_text)
    assert match is not None, output_text[-300:]
    return Path(match.group(1))


def test_bounds_large_output(tmp_path: Path) -> None:
    # Pi truncateTail: the last 2000 lines, and the full output in a temp file
    # that the notice names.
    result = BashTool().invoke(
        _request({"command": "seq 1 5000"}),
        _ctx(tmp_path),
    )
    assert result.is_error is False
    body = result.output_text.split("[output]\n", 1)[1]
    kept, notice = body.rsplit("\n\n", 1)
    assert kept.splitlines() == [str(n) for n in range(3001, 5001)]
    full = _notice_path(result.output_text)
    assert notice == f"[Showing lines 3001-5000 of 5000. Full output: {full}]"
    assert full.name.startswith("pipy-bash-") and full.suffix == ".log"
    assert full.read_text() == "".join(f"{n}\n" for n in range(1, 5001))
    assert full.stat().st_mode & 0o777 == 0o600
    full.unlink()


def test_byte_limit_notice(tmp_path: Path) -> None:
    # 1500 lines of 100 bytes: under the line limit, over 50 KB.
    result = BashTool().invoke(
        _request({"command": "for i in $(seq 1 1500); do printf '%099d\\n' $i; done"}),
        _ctx(tmp_path),
    )
    body = result.output_text.split("[output]\n", 1)[1]
    kept, notice = body.rsplit("\n\n", 1)
    # 512 lines of 100 bytes minus the final newline fit in 51200 bytes.
    assert len(kept.splitlines()) == 512
    full = _notice_path(result.output_text)
    assert notice == (
        f"[Showing lines 989-1500 of 1500 (50.0KB limit). Full output: {full}]"
    )
    full.unlink()


def test_partial_last_line_notice(tmp_path: Path) -> None:
    result = BashTool().invoke(
        _request({"command": "head -c 60000 /dev/zero | tr '\\0' x"}),
        _ctx(tmp_path),
    )
    body = result.output_text.split("[output]\n", 1)[1]
    kept, notice = body.rsplit("\n\n", 1)
    assert kept == "x" * 51200
    full = _notice_path(result.output_text)
    assert notice == (
        f"[Showing last 50.0KB of line 1 (line is 58.6KB). Full output: {full}]"
    )
    full.unlink()


def test_small_output_writes_no_temp_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    result = BashTool().invoke(_request({"command": "seq 1 2000"}), _ctx(tmp_path))
    assert "Full output" not in result.output_text
    assert list((tmp_path / "tmp").iterdir()) == []


def test_schema_and_description_match_pi() -> None:
    definition = BashTool().definition
    assert definition.description == (
        "Execute a bash command in the current working directory. Returns stdout "
        "and stderr. Output is truncated to last 2000 lines or 50KB (whichever is "
        "hit first). If truncated, full output is saved to a temp file. "
        "Optionally provide a timeout in seconds."
    )
    properties = definition.input_schema["properties"]
    assert properties["command"] == {
        "type": "string",
        "description": "Shell command to execute",
    }
    assert properties["timeout"]["description"] == (
        "Timeout in seconds (optional, no default timeout)"
    )
    assert definition.input_schema["required"] == ["command"]


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


def test_cancellation_observed_when_stdout_closed_early(tmp_path: Path) -> None:
    cancel_event = threading.Event()
    holder: list[Any] = []

    def invoke() -> None:
        holder.append(
            BashTool().invoke(
                _request(
                    {
                        "command": (
                            'printf "started\\n"; sleep 0.1; exec 1>&- 2>&-; sleep 30'
                        ),
                    }
                ),
                _ctx(tmp_path, cancel_event=cancel_event),
            )
        )

    start = time.monotonic()
    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    time.sleep(0.5)
    cancel_event.set()
    worker.join(timeout=5)
    elapsed = time.monotonic() - start

    assert not worker.is_alive()
    result = holder[0]
    assert not isinstance(result, BaseException)
    assert result.is_error is True
    assert "cancelled" in result.output_text.lower()
    assert elapsed < 5


def test_high_volume_output_is_bounded_not_accumulated(tmp_path: Path) -> None:
    # A noisy producer must not be retained in full; the returned result keeps
    # only a bounded tail (memory stays bounded during capture).
    result = BashTool().invoke(
        _request({"command": "seq 1 200000 | sed 's/^/line /'"}),
        _ctx(tmp_path),
    )
    assert result.is_error is False
    assert "[Showing lines 198001-200000 of 200000. Full output:" in result.output_text
    assert len(result.output_text.encode("utf-8")) < 60 * 1024
    # The tail is retained, so the last lines are present.
    assert "line 200000" in result.output_text
    _notice_path(result.output_text).unlink()


def test_accumulator_rolling_tail_counts_the_whole_stream() -> None:
    accumulator = OutputAccumulator(max_lines=3, max_bytes=64)
    for n in range(1, 1001):
        accumulator.append(f"row {n}\n".encode())
    accumulator.finish()
    snapshot = accumulator.snapshot(persist_if_truncated=True)
    accumulator.close_temp_file()
    assert snapshot.content == "row 998\nrow 999\nrow 1000"
    assert snapshot.truncation.total_lines == 1000
    assert snapshot.truncation.truncated_by == "lines"
    assert snapshot.full_output_path is not None
    full = Path(snapshot.full_output_path)
    assert full.read_bytes() == b"".join(f"row {n}\n".encode() for n in range(1, 1001))
    full.unlink()


def test_accumulator_decodes_split_utf8_and_counts_open_line() -> None:
    accumulator = OutputAccumulator()
    for chunk in (b"a\n\xc3", b"\xa9b"):
        accumulator.append(chunk)
    accumulator.finish()
    snapshot = accumulator.snapshot()
    assert snapshot.content == "a\néb"
    assert snapshot.truncation.total_lines == 2
    assert snapshot.truncation.truncated is False
    assert snapshot.full_output_path is None
    assert accumulator.last_line_bytes == 3


def test_no_sink_still_returns_full_output(tmp_path: Path) -> None:
    # Streaming is optional: with no output_sink the tool still returns the
    # complete bounded result.
    result = BashTool().invoke(
        _request({"command": "printf AAA; sleep 0.2; printf BBB"}), _ctx(tmp_path)
    )
    assert result.is_error is False
    assert "AAABBB" in result.output_text


def test_stream_flushes_incomplete_utf8_and_closes_stdout() -> None:
    proc = subprocess.Popen(  # noqa: S603 - fixed interpreter and test payload
        [sys.executable, "-c", "import os; os.write(1, b'\\xe2\\x82')"],
        stdout=subprocess.PIPE,
    )
    streamed: list[str] = []
    store = _ByteTail(16)

    outcome = _stream_output(
        proc,
        sink=streamed.append,
        timeout=None,
        store=store,
    )

    assert outcome == (False, False)
    assert store.output() == "\ufffd"
    assert streamed == ["\ufffd"]
    assert proc.stdout is not None and proc.stdout.closed
    assert proc.returncode == 0


def test_local_shell_shortcut_keeps_its_16kb_tail(tmp_path: Path) -> None:
    # The `!` shortcut is out of TOOLS1's scope (TOOLS2): it keeps 16 KB.
    result = run_local_command("seq 1 20000", workspace_root=tmp_path)
    assert result.truncated is True
    assert len(result.output.encode()) == 16 * 1024
    assert result.output.endswith("20000\n")


def test_accumulator_drops_a_temp_file_it_could_not_write() -> None:
    accumulator = OutputAccumulator(max_lines=2, max_bytes=1024)
    accumulator.append(b"1\n2\n3\n")
    path = accumulator.snapshot(persist_if_truncated=True).full_output_path
    assert path is not None and Path(path).exists()

    class _Full:
        def write(self, _data: bytes) -> None:
            raise OSError("disk full")

        def close(self) -> None:
            pass

    accumulator._temp_file = _Full()  # type: ignore[assignment]
    accumulator.append(b"4\n")
    accumulator.finish()
    snapshot = accumulator.snapshot(persist_if_truncated=True)
    # A partial file is never named as the full output.
    assert snapshot.full_output_path is None
    assert not Path(path).exists()
    assert snapshot.content == "3\n4"

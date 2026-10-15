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
from pathlib import Path
from typing import Any

from pipy_harness.native.agent.content import (
    NO_STRUCTURED_CONTENT,
    ToolOutput,
    ToolPartialResult,
)
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolOutputSink,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.bash import (
    BashTool,
    _stream_output,
    run_local_command,
    shell_exit_code,
)
from pipy_harness.native.tools.output_accumulator import OutputAccumulator


def _ctx(
    workspace: Path,
    *,
    output_sink: ToolOutputSink | None = None,
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
    assert result.output_text == "hello world\n"
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


def test_nonzero_exit_is_an_error_with_pis_status(tmp_path: Path) -> None:
    # Pi appends `Command exited with code N` and marks the result an error.
    result = BashTool().invoke(
        _request({"command": "echo boom; exit 3"}), _ctx(tmp_path)
    )
    assert result.is_error is True
    assert result.output_text == "boom\n\n\nCommand exited with code 3"


def test_nonzero_exit_without_output_keeps_the_no_output_text(tmp_path: Path) -> None:
    result = BashTool().invoke(_request({"command": "exit 1"}), _ctx(tmp_path))
    assert result.is_error is True
    assert result.output_text == "(no output)\n\nCommand exited with code 1"


def test_success_without_output_is_no_output(tmp_path: Path) -> None:
    result = BashTool().invoke(_request({"command": "true"}), _ctx(tmp_path))
    assert result.is_error is False
    assert result.output_text == "(no output)"


def test_a_signal_killed_shell_reports_128_plus_the_signal(tmp_path: Path) -> None:
    result = BashTool().invoke(_request({"command": "kill -9 $$"}), _ctx(tmp_path))
    assert result.is_error is True
    assert result.output_text == "(no output)\n\nCommand exited with code 137"
    assert shell_exit_code(-15) == 143
    assert shell_exit_code(None) == 1
    assert shell_exit_code(2) == 2


def test_a_missing_working_directory_is_pis_error(tmp_path: Path) -> None:
    gone = tmp_path / "gone"
    result = BashTool().invoke(_request({"command": "true"}), _ctx(gone))
    assert result.is_error is True
    assert result.output_text == (
        f"Working directory does not exist: {gone.resolve()}\n"
        "Cannot execute bash commands."
    )


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
    body = result.output_text
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
    body = result.output_text
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
    body = result.output_text
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
    assert result.output_text == "Command timed out after 1 seconds"


def test_times_out_after_output(tmp_path: Path) -> None:
    result = BashTool().invoke(
        _request({"command": "echo partial; sleep 30", "timeout": 1}), _ctx(tmp_path)
    )
    assert result.is_error is True
    assert result.output_text == "partial\n\n\nCommand timed out after 1 seconds"


def test_streams_output_incrementally_before_process_exits(tmp_path: Path) -> None:
    # Pi-style live streaming: the first chunk must reach the sink while the
    # command is still running, not all at once at the end. The command prints
    # AAA, sleeps, then prints BBB — so a streaming reader sees AAA ~0.6s before
    # the process finishes, while a buffer-until-exit reader would not.
    received: list[tuple[float, ToolPartialResult]] = []

    def sink(update: ToolOutput) -> None:
        # Pi ``emitOutputUpdate``: each update is the output so far.
        assert isinstance(update, ToolPartialResult)
        received.append((time.monotonic(), update))

    ctx = _ctx(tmp_path, output_sink=sink)
    result = BashTool().invoke(
        _request({"command": "printf AAA; sleep 0.6; printf BBB"}), ctx
    )
    finished_at = time.monotonic()

    assert result.is_error is False
    updates = [update for _, update in received]
    assert updates[0].content == "AAA"
    assert updates[-1].content == "AAABBB"
    assert all(update.details == {} for update in updates)
    # A live row appends what each update added.
    assert "".join(update.appended or "" for update in updates) == "AAABBB"
    # The first emission (AAA) landed well before the command finished.
    first_emit_at = received[0][0]
    assert finished_at - first_emit_at >= 0.4


def test_streamed_updates_are_the_tail_with_truncation_details(
    tmp_path: Path,
) -> None:
    # Pi: past 2000 lines an update holds the last 2000, ``details`` the
    # truncation and the temp file with the full output.
    updates: list[ToolPartialResult] = []

    def sink(update: ToolOutput) -> None:
        assert isinstance(update, ToolPartialResult)
        updates.append(update)

    result = BashTool().invoke(
        _request({"command": "seq 1 2500"}), _ctx(tmp_path, output_sink=sink)
    )

    assert result.is_error is False
    last = updates[-1]
    lines = last.content.splitlines()
    assert len(lines) == 2000
    assert lines[0] == "501" and lines[-1] == "2500"
    assert last.details is not None
    truncation = last.details["truncation"]
    assert truncation["truncated"] is True
    assert truncation["truncatedBy"] == "lines"
    assert truncation["totalLines"] == 2500
    path = Path(last.details["fullOutputPath"])
    assert path.read_text().splitlines()[0] == "1"
    assert "".join(update.appended or "" for update in updates).splitlines() == [
        str(n) for n in range(1, 2501)
    ]


def test_the_last_update_holds_the_whole_decoded_output(tmp_path: Path) -> None:
    # An incomplete UTF-8 sequence at the end is decoded before the last
    # update, as in the result.
    updates: list[ToolPartialResult] = []

    def sink(update: ToolOutput) -> None:
        assert isinstance(update, ToolPartialResult)
        updates.append(update)

    result = BashTool().invoke(
        _request({"command": "printf 'ok\\342'"}), _ctx(tmp_path, output_sink=sink)
    )

    assert result.output_text == "ok\ufffd"
    assert updates[-1].content == "ok\ufffd"


def test_the_file_an_update_names_holds_the_output_so_far(tmp_path: Path) -> None:
    # A consumer may read the full output while the command still runs.
    seen: list[str] = []

    def sink(update: ToolOutput) -> None:
        assert isinstance(update, ToolPartialResult)
        details = update.details or {}
        if "fullOutputPath" in details and not seen:
            seen.append(Path(details["fullOutputPath"]).read_text())

    BashTool().invoke(
        _request({"command": "seq 1 2001; sleep 0.5; echo done"}),
        _ctx(tmp_path, output_sink=sink),
    )

    assert seen and seen[0].splitlines()[:1] == ["1"]
    assert "2001" in seen[0].splitlines()
    assert "done" not in seen[0]


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
    assert result.output_text == "Command timed out after 1 seconds"
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
    assert result.output_text == "started\n\n\nCommand aborted"
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
    stored: list[bytes] = []

    class _Store:
        def append(self, data: bytes) -> None:
            stored.append(data)

    outcome = _stream_output(
        proc,
        sink=streamed.append,
        timeout=None,
        store=_Store(),
    )

    assert outcome == (False, False)
    assert b"".join(stored) == b"\xe2\x82"
    assert streamed == ["\ufffd"]
    assert proc.stdout is not None and proc.stdout.closed
    assert proc.returncode == 0


def test_local_shell_shortcut_keeps_pis_tail_and_a_temp_file(tmp_path: Path) -> None:
    # Pi's bash-executor: truncateTail (2000 lines / 50 KB) plus a temp file.
    result = run_local_command("seq 1 20000", workspace_root=tmp_path)
    assert result.truncated is True
    assert result.output.splitlines() == [str(n) for n in range(18001, 20001)]
    assert result.full_output_path is not None
    full = Path(result.full_output_path)
    assert full.name.startswith("pipy-bash-")
    assert full.read_text() == "".join(f"{n}\n" for n in range(1, 20001))
    full.unlink()


def test_local_shell_shortcut_small_output_has_no_temp_file(tmp_path: Path) -> None:
    result = run_local_command("seq 1 3; exit 4", workspace_root=tmp_path)
    assert (result.output, result.truncated, result.full_output_path) == (
        "1\n2\n3\n",
        False,
        None,
    )
    assert result.exit_code == 4


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
    assert accumulator.read_full_output(1024) == ("3\n4", True)


# Goal-check finding M15: Pi's structured bash result for programmatic
# callers (bash.ts ``bashOutputSchema`` and ``structuredContent``).


def test_the_output_schema_is_pis() -> None:
    assert BashTool().definition.output_schema == {
        "type": "object",
        "properties": {
            "output": {
                "type": "string",
                "description": "Combined stdout and stderr, possibly truncated",
            },
            "truncated": {"type": "boolean"},
            "full_output_path": {
                "type": "string",
                "description": "Full output, when truncated",
            },
            "exit_code": {"type": "number"},
            "wall_time_seconds": {"type": "number"},
        },
        "required": ["output", "truncated", "exit_code", "wall_time_seconds"],
    }


def test_a_command_carries_a_structured_result(tmp_path: Path) -> None:
    result = BashTool().invoke(_request({"command": "echo hi"}), _ctx(tmp_path))

    structured = result.structured_content
    assert isinstance(structured, dict)
    assert list(structured) == ["output", "truncated", "exit_code", "wall_time_seconds"]
    assert structured["output"] == "hi\n"
    assert structured["truncated"] is False
    assert structured["exit_code"] == 0
    wall = structured["wall_time_seconds"]
    assert isinstance(wall, float) and 0 <= wall < 30 and wall == round(wall, 1)


def test_a_nonzero_exit_is_an_error_that_keeps_its_structured_result(
    tmp_path: Path,
) -> None:
    result = BashTool().invoke(
        _request({"command": "echo bad; exit 3"}), _ctx(tmp_path)
    )

    assert result.is_error is True
    assert result.output_text == "bad\n\n\nCommand exited with code 3"
    assert result.structured_content["output"] == "bad\n"
    assert result.structured_content["exit_code"] == 3
    # No output stays empty here; ``(no output)`` is the model's text.
    empty = BashTool().invoke(_request({"command": "exit 1"}), _ctx(tmp_path))
    assert empty.structured_content["output"] == ""


def test_structured_output_is_not_cut_like_the_models_text(tmp_path: Path) -> None:
    # 3000 lines: the model sees the last 2000, the script all of them, and
    # under 1 MiB nothing is cut, so no file is named.
    result = BashTool().invoke(_request({"command": "seq 1 3000"}), _ctx(tmp_path))

    structured = result.structured_content
    assert structured["output"] == "".join(f"{n}\n" for n in range(1, 3001))
    assert structured["truncated"] is False
    assert "full_output_path" not in structured
    assert result.details is not None and "fullOutputPath" in result.details


def test_structured_output_over_one_mebibyte_keeps_head_and_tail(
    tmp_path: Path,
) -> None:
    # 1.5 MiB of one line, starting with "<" and ending with ">".
    command = f"{sys.executable} -c \"import sys; sys.stdout.write('<' + 'x' * (3 * 512 * 1024 - 2) + '>')\""
    result = BashTool().invoke(_request({"command": command}), _ctx(tmp_path))

    structured = result.structured_content
    assert structured["truncated"] is True
    output = structured["output"]
    marker = f"\n\n[... {3 * 512 * 1024 - 1024 * 1024} bytes omitted ...]\n\n"
    head, _, tail = output.partition(marker)
    assert head == "<" + "x" * (512 * 1024 - 1)
    assert tail == "x" * (512 * 1024 - 1) + ">"
    path = structured["full_output_path"]
    assert result.details is not None
    assert path == result.details["fullOutputPath"]
    assert Path(path).stat().st_size == 3 * 512 * 1024
    assert list(structured) == [
        "output",
        "truncated",
        "full_output_path",
        "exit_code",
        "wall_time_seconds",
    ]


def test_an_interrupted_command_has_no_structured_result(tmp_path: Path) -> None:
    # Pi throws for a timeout or an abort, so no structured content exists.
    result = BashTool().invoke(
        _request({"command": "sleep 30", "timeout": 1}), _ctx(tmp_path)
    )

    assert result.is_error is True
    assert result.structured_content is NO_STRUCTURED_CONTENT


def test_full_output_cuts_at_character_boundaries(tmp_path: Path) -> None:
    accumulator = OutputAccumulator(max_lines=10, max_bytes=8)
    # Twelve bytes of three-byte characters; the cut at 5 + 5 bytes falls
    # inside characters on both sides.
    accumulator.append("€€€€".encode())
    accumulator.finish()
    accumulator.snapshot(persist_if_truncated=True)
    accumulator.close_temp_file()

    output, truncated = accumulator.read_full_output(10)

    assert truncated is True
    assert output == "€\n\n[... 2 bytes omitted ...]\n\n€"
    small = OutputAccumulator()
    small.append(b"ok\xff")
    small.finish()
    assert small.read_full_output(10) == ("ok�", False)

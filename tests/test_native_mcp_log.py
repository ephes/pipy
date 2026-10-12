"""Tests for the MCP server log file."""

from __future__ import annotations

import re
import stat
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipy_harness.native.mcp import log as log_module
from pipy_harness.native.mcp.log import (
    MAX_LOG_BYTES,
    McpServerLog,
    format_mcp_log_message,
    mcp_log_path,
)

_NOW = datetime(2026, 10, 12, 8, 30, 5, 120_000, tzinfo=timezone.utc)
_STAMP = "2026-10-12T08:30:05.120Z"


def _line(params: object) -> str:
    return format_mcp_log_message("fake", params, _NOW)


def test_a_line_has_time_server_level_logger_and_message() -> None:
    params = {"level": "warning", "logger": "db", "data": "slow query"}
    assert _line(params) == f"{_STAMP} [fake] warning db: slow query\n"


def test_the_time_is_utc_with_milliseconds() -> None:
    local = _NOW.astimezone(timezone(timedelta(hours=2)))
    assert format_mcp_log_message("s", {"data": "x"}, local).startswith(_STAMP + " ")
    assert re.match(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z \[s\] info x\n$",
        format_mcp_log_message("s", {"data": "x"}),
    )


@pytest.mark.parametrize(
    ("params", "rest"),
    [
        # The level defaults to info; the logger part is left out when absent.
        ({"data": "hello"}, "info hello"),
        ({"level": 3, "logger": "", "data": "hello"}, "info hello"),
        ({"level": "debug", "logger": 7, "data": "hello"}, "debug hello"),
        # Data that is not a string is JSON without spaces.
        (
            {"level": "error", "data": {"code": 7, "why": ["a", "b"]}},
            'error {"code":7,"why":["a","b"]}',
        ),
        ({"data": None}, "info null"),
        ({"data": 12}, "info 12"),
        ({"data": "ünïcode"}, "info ünïcode"),
        ({"data": {"text": "ü"}}, 'info {"text":"ü"}'),
        # Continuation lines are indented four spaces.
        ({"data": "a\nb\r\nc"}, "info a\n    b\n    c"),
        # Params that are not an object are the data.
        ("plain", "info plain"),
        (["a", 1], 'info ["a",1]'),
        # No data at all, as JavaScript prints it.
        ({"level": "notice"}, "notice undefined"),
        (None, "info undefined"),
    ],
)
def test_message_forms(params: object, rest: str) -> None:
    assert _line(params) == f"{_STAMP} [fake] {rest}\n"


def test_the_log_is_in_the_config_home(tmp_path: Path) -> None:
    assert mcp_log_path(tmp_path) == tmp_path / "mcp.log"
    assert mcp_log_path(str(tmp_path)) == tmp_path / "mcp.log"


def test_write_appends_lines_and_creates_the_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "home" / "mcp.log"
    log = McpServerLog(path)
    log.write("one", {"level": "warning", "logger": "db", "data": "slow\nquery"})
    log.write("two", {"level": "error", "data": {"code": 7}})
    lines = re.sub(r"(?m)^\S+ ", "", path.read_text(encoding="utf-8"))
    assert lines == '[one] warning db: slow\n    query\n[two] error {"code":7}\n'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_keeps_what_another_writer_appended(tmp_path: Path) -> None:
    path = tmp_path / "mcp.log"
    path.write_text("earlier\n", encoding="utf-8")
    first, second = McpServerLog(path), McpServerLog(path)
    first.write("a", {"data": "1"})
    second.write("b", {"data": "2"})
    first.write("a", {"data": "3"})
    text = re.sub(r"(?m)^\S+ (?=\[)", "", path.read_text(encoding="utf-8"))
    assert text == "earlier\n[a] info 1\n[b] info 2\n[a] info 3\n"


def test_a_file_over_the_limit_is_renamed_before_the_next_line(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mcp.log"
    with path.open("wb") as file:
        file.truncate(MAX_LOG_BYTES + 1)
    rotated = tmp_path / "mcp.log.1"
    rotated.write_text("older still\n", encoding="utf-8")
    McpServerLog(path).write("fake", {"data": "after"})
    assert rotated.stat().st_size == MAX_LOG_BYTES + 1
    assert path.read_text(encoding="utf-8").endswith(" [fake] info after\n")
    assert path.read_text(encoding="utf-8").count("\n") == 1


def test_a_file_at_the_limit_is_not_renamed(tmp_path: Path) -> None:
    path = tmp_path / "mcp.log"
    with path.open("wb") as file:
        file.truncate(MAX_LOG_BYTES)
    McpServerLog(path).write("fake", {"data": "x"})
    assert not (tmp_path / "mcp.log.1").exists()
    assert path.stat().st_size > MAX_LOG_BYTES


def test_the_log_rotates_when_its_own_lines_pass_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(log_module, "MAX_LOG_BYTES", 100)
    path = tmp_path / "mcp.log"
    log = McpServerLog(path)
    for number in range(4):
        log.write("fake", {"data": f"message {number} " + "x" * 30})
    # Lines 0 and 1 passed the limit, so line 2 started a new file.
    assert (tmp_path / "mcp.log.1").read_text(encoding="utf-8").count("\n") == 2
    text = path.read_text(encoding="utf-8")
    assert "message 2" in text and "message 3" in text


def test_a_file_another_process_rotated_is_not_renamed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(log_module, "MAX_LOG_BYTES", 100)
    path = tmp_path / "mcp.log"
    rotated = tmp_path / "mcp.log.1"
    log = McpServerLog(path)
    for number in range(2):
        log.write("fake", {"data": f"message {number} " + "x" * 30})
    # Another process renames the file and starts a new one.
    path.rename(rotated)
    path.write_text("theirs\n", encoding="utf-8")
    log.write("fake", {"data": "mine"})
    assert rotated.read_text(encoding="utf-8").count("\n") == 2
    assert path.read_text(encoding="utf-8").startswith("theirs\n")
    assert path.read_text(encoding="utf-8").endswith(" [fake] info mine\n")


def test_write_failures_are_ignored(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    log = McpServerLog(blocker / "mcp.log")
    log.write("fake", {"data": "x"})
    log.write("fake", {"data": "y"})
    assert blocker.read_text(encoding="utf-8") == ""


def test_a_message_that_cannot_be_encoded_is_still_written(tmp_path: Path) -> None:
    path = tmp_path / "mcp.log"
    McpServerLog(path).write("fake", {"data": "bad \ud800 end"})
    assert path.read_text(encoding="utf-8").endswith(" [fake] info bad \ufffd end\n")


def test_lines_written_from_several_threads_stay_whole(tmp_path: Path) -> None:
    path = tmp_path / "mcp.log"
    log = McpServerLog(path)

    def write(worker: int) -> None:
        for number in range(50):
            log.write(f"s{worker}", {"data": f"{worker}-{number} " + "y" * 200})

    threads = [threading.Thread(target=write, args=(worker,)) for worker in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 200
    assert all(re.search(r" \[s\d\] info \d-\d+ y{200}$", line) for line in lines)

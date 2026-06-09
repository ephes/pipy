"""Tests for the stdlib LF-only JSONL transport used by automation modes.

The automation JSON/RPC surfaces (`--mode json`, `--mode rpc`) frame every
record as one JSON object per LF-delimited line, mirroring Pi's
`serializeJsonLine` / `attachJsonlLineReader` (`packages/coding-agent/src/
modes/rpc/jsonl.ts`). These tests pin the framing rules pipy must honor:
split only on ``\n``, tolerate a trailing ``\r``, preserve U+2028/U+2029
inside string payloads, buffer multi-record and partial trailing lines, and
serialize all writes through a single writer so records never interleave.
"""

from __future__ import annotations

import io
import json
import threading

import math

import pytest

from pipy_harness.native.automation.jsonl import (
    JsonlLineBuffer,
    JsonlWriter,
    loads_strict,
    serialize_json_line,
)

LS = " "  # LINE SEPARATOR
PS = " "  # PARAGRAPH SEPARATOR


def test_serialize_json_line_is_compact_lf_terminated() -> None:
    line = serialize_json_line({"type": "agent_start", "n": 1})
    assert line == '{"type":"agent_start","n":1}\n'
    assert line.endswith("\n")
    assert "\r" not in line


def test_serialize_json_line_preserves_unicode_separators_literally() -> None:
    # JSON.stringify does not escape U+2028/U+2029; pipy keeps them literal so
    # the bytes match Pi and the reader proves it splits on \n only.
    line = serialize_json_line({"text": f"a{LS}b{PS}c"})
    assert LS in line
    assert PS in line
    assert line.count("\n") == 1  # only the trailing record separator


def test_buffer_emits_complete_lines() -> None:
    buf = JsonlLineBuffer()
    assert buf.feed("a\nb\n") == ["a", "b"]


def test_buffer_holds_partial_trailing_line_until_next_feed() -> None:
    buf = JsonlLineBuffer()
    assert buf.feed("a\nb") == ["a"]
    assert buf.feed("c\n") == ["bc"]


def test_buffer_strips_trailing_carriage_return() -> None:
    buf = JsonlLineBuffer()
    assert buf.feed("a\r\nb\r\n") == ["a", "b"]


def test_buffer_splits_only_on_newline_preserving_separators() -> None:
    buf = JsonlLineBuffer()
    # U+2028 / U+2029 must NOT be treated as line breaks.
    assert buf.feed(f"x{LS}y{PS}z\n") == [f"x{LS}y{PS}z"]


def test_buffer_flush_returns_unterminated_remainder_once() -> None:
    buf = JsonlLineBuffer()
    assert buf.feed("abc") == []
    assert buf.flush() == ["abc"]
    assert buf.flush() == []


def test_buffer_flush_strips_trailing_carriage_return() -> None:
    buf = JsonlLineBuffer()
    assert buf.feed("abc\r") == []
    assert buf.flush() == ["abc"]


def test_serialize_json_line_rejects_non_finite_floats() -> None:
    # Strict JSON output: never emit bare NaN/Infinity.
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            serialize_json_line({"x": value})


def test_loads_strict_rejects_nan_and_infinity() -> None:
    assert loads_strict('{"a": 1}') == {"a": 1}
    for blob in ('{"x": NaN}', '{"x": Infinity}', '{"x": -Infinity}'):
        with pytest.raises(ValueError):
            loads_strict(blob)


def test_writer_emits_lf_only_records() -> None:
    raw = io.BytesIO()
    writer = JsonlWriter(raw)
    writer.write_line({"type": "session", "id": "x"})
    writer.write_line({"type": "agent_end"})
    data = raw.getvalue().decode("utf-8")
    assert "\r" not in data
    lines = data.split("\n")
    assert lines[-1] == ""  # trailing newline
    records = [json.loads(line) for line in lines[:-1]]
    assert records == [{"type": "session", "id": "x"}, {"type": "agent_end"}]


def test_writer_does_not_interleave_under_concurrent_writers() -> None:
    raw = io.BytesIO()
    writer = JsonlWriter(raw)

    def emit(tag: str) -> None:
        for i in range(200):
            writer.write_line({"tag": tag, "i": i})

    threads = [threading.Thread(target=emit, args=(t,)) for t in ("a", "b", "c")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    data = raw.getvalue().decode("utf-8")
    lines = data.split("\n")
    assert lines[-1] == ""
    # Every line is a complete, parseable record (no torn/interleaved writes).
    parsed = [json.loads(line) for line in lines[:-1]]
    assert len(parsed) == 600
    assert {record["tag"] for record in parsed} == {"a", "b", "c"}

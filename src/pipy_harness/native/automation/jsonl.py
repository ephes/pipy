"""Strict LF-only JSONL framing for the automation transports.

Mirrors Pi's ``serializeJsonLine`` / ``attachJsonlLineReader``
(`packages/coding-agent/src/modes/rpc/jsonl.ts`) with stdlib only:

- one JSON object per line, ``"\n"`` separator, never ``"\r\n"``;
- the reader splits on ``"\n"`` only and tolerates a single trailing ``"\r"``
  on each line, so other Unicode separators (U+2028/U+2029) inside string
  payloads are preserved;
- all writes go through one lock so concurrently produced records (async
  session events vs. command responses) never interleave mid-line.
"""

from __future__ import annotations

import json
import threading
from typing import Any, BinaryIO


def _reject_non_finite_constant(name: str) -> Any:
    """``parse_constant`` hook: reject the non-standard ``NaN``/``Infinity``.

    Strict JSON (and ``JSON.parse``) has no ``NaN``/``Infinity`` literals; Python's
    default ``json.loads`` accepts them. Rejecting them keeps the JSONL protocol
    strict so a malformed command surfaces the documented parse-error response.
    """

    raise ValueError(f"non-finite JSON constant not allowed: {name}")


def loads_strict(text: str) -> Any:
    """``json.loads`` that rejects the non-standard ``NaN``/``Infinity`` literals."""

    return json.loads(text, parse_constant=_reject_non_finite_constant)


def serialize_json_line(value: Any) -> str:
    """Serialize ``value`` as one compact JSON object followed by a single LF.

    Uses ``ensure_ascii=False`` so U+2028/U+2029 stay literal (matching
    ``JSON.stringify``); the reader proves it still splits on ``\n`` only.
    ``allow_nan=False`` keeps the output strict JSON — a non-finite float raises
    rather than emitting bare ``NaN``/``Infinity`` that standards-compliant
    clients would reject (pipy's own payloads never contain non-finite floats,
    and tool arguments reject them at parse time).
    """

    return (
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    )


class JsonlLineBuffer:
    """Incremental LF-delimited line splitter.

    ``feed`` appends a decoded chunk and returns every complete line it can
    now yield (trailing ``\r`` stripped), buffering any partial trailing line
    until the next ``feed``. ``flush`` returns a final unterminated remainder
    once (e.g. at EOF) and then nothing.
    """

    def __init__(self) -> None:
        self._buffer = ""

    @staticmethod
    def _strip_cr(line: str) -> str:
        return line[:-1] if line.endswith("\r") else line

    def feed(self, chunk: str) -> list[str]:
        self._buffer += chunk
        lines: list[str] = []
        while True:
            newline_index = self._buffer.find("\n")
            if newline_index == -1:
                return lines
            lines.append(self._strip_cr(self._buffer[:newline_index]))
            self._buffer = self._buffer[newline_index + 1 :]

    def flush(self) -> list[str]:
        if not self._buffer:
            return []
        remainder = self._strip_cr(self._buffer)
        self._buffer = ""
        return [remainder]


class JsonlWriter:
    """Single serialized writer over a binary stream.

    Every ``write_line`` serializes the value, writes the UTF-8 bytes, and
    flushes while holding one lock. The blocking flushed write is pipy's
    stdlib analogue of Pi's selective stdout backpressure: a slow consumer
    blocks the writer rather than letting records interleave.
    """

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def write_line(self, value: Any) -> None:
        data = serialize_json_line(value).encode("utf-8")
        with self._lock:
            self._stream.write(data)
            self._stream.flush()

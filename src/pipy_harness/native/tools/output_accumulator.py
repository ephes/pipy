"""Bounded streaming output with a full-output temp file, ported from Pi.

Mirrors ``packages/coding-agent/src/core/tools/output-accumulator.ts``
(pi-mono ``4df157433``). Chunks are decoded as UTF-8 with replacement
characters; only a rolling decoded tail is kept for the snapshot, while line
and byte counters cover the whole stream. Raw bytes stay in memory until the
output passes the line or byte limit; then a temp file is opened, the buffered
bytes are written to it, and every later chunk is appended, so the full output
survives for the model to read.

Pi names the file ``pi-bash-<16 hex>.log`` in ``os.tmpdir()``; pipy uses
``pipy-bash-<16 hex>.log`` in :func:`tempfile.gettempdir`, created owner-only.
Like Pi, the file is not deleted.
"""

from __future__ import annotations

import codecs
import os
import secrets
import tempfile
from dataclasses import dataclass, replace
from typing import BinaryIO, Literal

from pipy_harness.native.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationResult,
    truncate_tail,
)


@dataclass(frozen=True, slots=True)
class OutputSnapshot:
    """The truncated tail, its truncation facts and the temp-file path."""

    content: str
    truncation: TruncationResult
    full_output_path: str | None


def _byte_length(text: str) -> int:
    return len(text.encode("utf-8"))


class OutputAccumulator:
    """Track streaming output with bounded memory (Pi ``OutputAccumulator``)."""

    def __init__(
        self,
        *,
        max_lines: int = DEFAULT_MAX_LINES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        temp_file_prefix: str = "pipy-output",
    ) -> None:
        self._max_lines = max_lines
        self._max_bytes = max_bytes
        self._max_rolling_bytes = max(max_bytes * 2, 1)
        self._temp_file_prefix = temp_file_prefix
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._raw_chunks: list[bytes] = []
        self._tail_text = ""
        self._tail_bytes = 0
        self._tail_starts_at_line_boundary = True
        self._total_raw_bytes = 0
        self._total_decoded_bytes = 0
        self._completed_lines = 0
        self._total_lines = 0
        self._current_line_bytes = 0
        self._has_open_line = False
        self._finished = False
        self._temp_file_path: str | None = None
        self._temp_file: BinaryIO | None = None
        self._temp_file_failed = False

    def append(self, data: bytes) -> None:
        if self._finished:
            raise RuntimeError("Cannot append to a finished output accumulator")
        self._total_raw_bytes += len(data)
        self._append_decoded_text(self._decoder.decode(data))
        if self._temp_file is not None or self._should_use_temp_file():
            self._ensure_temp_file()
            self._write_temp(data)
        elif data:
            self._raw_chunks.append(data)

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._append_decoded_text(self._decoder.decode(b"", final=True))
        if self._should_use_temp_file():
            self._ensure_temp_file()

    def snapshot(self, *, persist_if_truncated: bool = False) -> OutputSnapshot:
        tail = truncate_tail(
            self._snapshot_text(),
            max_lines=self._max_lines,
            max_bytes=self._max_bytes,
        )
        truncated = (
            self._total_lines > self._max_lines
            or self._total_decoded_bytes > self._max_bytes
        )
        truncated_by: Literal["lines", "bytes"] | None = None
        if truncated:
            truncated_by = tail.truncated_by or (
                "bytes" if self._total_decoded_bytes > self._max_bytes else "lines"
            )
        truncation = replace(
            tail,
            truncated=truncated,
            truncated_by=truncated_by,
            total_lines=self._total_lines,
            total_bytes=self._total_decoded_bytes,
            max_lines=self._max_lines,
            max_bytes=self._max_bytes,
        )
        if persist_if_truncated and truncation.truncated:
            self._ensure_temp_file()
        return OutputSnapshot(
            content=truncation.content,
            truncation=truncation,
            full_output_path=self._temp_file_path,
        )

    def close_temp_file(self) -> None:
        if self._temp_file is None:
            return
        handle = self._temp_file
        try:
            handle.close()
        except OSError:
            # The final flush failed: the file is incomplete.
            self._abandon_temp_file()
            return
        self._temp_file = None

    @property
    def last_line_bytes(self) -> int:
        return self._current_line_bytes

    def _append_decoded_text(self, text: str) -> None:
        if not text:
            return
        size = _byte_length(text)
        self._total_decoded_bytes += size
        self._tail_text += text
        self._tail_bytes += size
        if self._tail_bytes > self._max_rolling_bytes * 2:
            self._trim_tail()

        newlines = text.count("\n")
        if newlines == 0:
            self._current_line_bytes += size
            self._has_open_line = True
        else:
            self._completed_lines += newlines
            rest = text[text.rindex("\n") + 1 :]
            self._current_line_bytes = _byte_length(rest)
            self._has_open_line = bool(rest)
        self._total_lines = self._completed_lines + (1 if self._has_open_line else 0)

    def _trim_tail(self) -> None:
        encoded = self._tail_text.encode("utf-8")
        if len(encoded) <= self._max_rolling_bytes:
            self._tail_bytes = len(encoded)
            return
        start = len(encoded) - self._max_rolling_bytes
        while start < len(encoded) and (encoded[start] & 0xC0) == 0x80:
            start += 1
        if start != 0:
            self._tail_starts_at_line_boundary = encoded[start - 1] == 0x0A
        self._tail_text = encoded[start:].decode("utf-8")
        self._tail_bytes = _byte_length(self._tail_text)

    def _snapshot_text(self) -> str:
        if self._tail_starts_at_line_boundary:
            return self._tail_text
        first_newline = self._tail_text.find("\n")
        if first_newline == -1:
            return self._tail_text
        return self._tail_text[first_newline + 1 :]

    def _should_use_temp_file(self) -> bool:
        return (
            self._total_raw_bytes > self._max_bytes
            or self._total_decoded_bytes > self._max_bytes
            or self._total_lines > self._max_lines
        )

    def _ensure_temp_file(self) -> None:
        if self._temp_file_path is not None or self._temp_file_failed:
            return
        name = f"{self._temp_file_prefix}-{secrets.token_hex(8)}.log"
        path = os.path.join(tempfile.gettempdir(), name)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            # pipy keeps the tail and names no file rather than failing the
            # command; the caller's notice then has no path to offer.
            self._temp_file_failed = True
            self._raw_chunks = []
            return
        self._temp_file_path = path
        self._temp_file = os.fdopen(fd, "wb")
        chunks, self._raw_chunks = self._raw_chunks, []
        for chunk in chunks:
            self._write_temp(chunk)

    def _write_temp(self, data: bytes) -> None:
        if self._temp_file is None:
            return
        try:
            self._temp_file.write(data)
        except OSError:
            self._abandon_temp_file()

    def _abandon_temp_file(self) -> None:
        """Drop a temp file that could not be written in full.

        A partial file must not be named as the full output, so it is removed
        and the snapshot names no file; the tail is still returned.
        """

        handle, path = self._temp_file, self._temp_file_path
        self._temp_file = None
        self._temp_file_path = None
        self._temp_file_failed = True
        self._raw_chunks = []
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        if path is not None:
            try:
                os.unlink(path)
            except OSError:
                pass


__all__ = ["OutputAccumulator", "OutputSnapshot"]

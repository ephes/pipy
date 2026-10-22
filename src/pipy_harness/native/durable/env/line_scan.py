"""Bounded, thread-confined scanner for zero-based half-open line ranges.

Offsets count raw bytes; sizes count replacement-decoded UTF-8 bytes. Only a
file-leading BOM is omitted from sizes. No text or input chunk is retained.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass

_SAFE = 2**53 - 1


@dataclass(frozen=True, slots=True)
class LineScan:
    """Immutable offsets and decoded sizes of a selected file line range."""

    newlines: int
    start: int
    end: int
    first_line_end: int
    last_line_start: int
    selected_bytes: int
    first_line_bytes: int


class LineScanner:
    """Feed exact owned bytes in order, then finish once; later calls refuse.

    Empty chunks are allowed. The caller owns serialization: instances are
    thread-confined. Decoder output is counted in fixed-size blocks, including
    for a single arbitrarily large push. There are no filesystem effects.
    """

    def __init__(self, start_line: int, end_line: int | None = None) -> None:
        if type(start_line) is not int or not 0 <= start_line <= _SAFE:
            raise ValueError("Invalid line range")
        if end_line is not None and (
            type(end_line) is not int or not start_line < end_line <= _SAFE
        ):
            raise ValueError("Invalid line range")
        self._start_line = start_line
        self._end_line = end_line
        self._position = 0
        self._newlines = 0
        self._line_start = 0
        self._start: int | None = None
        self._end: int | None = None
        self._first_end: int | None = None
        self._last_start: int | None = None
        self._selected_bytes = 0
        self._first_bytes = 0
        self._selection: codecs.IncrementalDecoder | None = None
        self._first: codecs.IncrementalDecoder | None = None
        self._head: bytes | None = b""
        self._bom = False
        self._finished = False
        if start_line == 0:
            self._begin(0)

    def _begin(self, position: int) -> None:
        self._start = position
        if self._end_line == self._start_line + 1:
            self._last_start = position
        self._selection = codecs.getincrementaldecoder("utf-8")("replace")
        self._first = codecs.getincrementaldecoder("utf-8")("replace")

    def push(self, chunk: bytes) -> None:
        """Consume a chunk without retaining it; reject foreign binary values."""
        if type(chunk) is not bytes:
            raise TypeError("chunk must be exact bytes")
        if self._finished:
            raise RuntimeError("Scanner is finished")
        offset = 0
        if self._head is not None:
            offset = min(3 - len(self._head), len(chunk))
            self._head += chunk[:offset]
            if len(self._head) < 3:
                return
            self._release_head()
        self._process(chunk, offset)

    def _release_head(self) -> None:
        head = self._head
        assert head is not None
        self._head = None
        self._bom = head == b"\xef\xbb\xbf"
        self._process(head, 0)

    def _feed(self, chunk: bytes, base: int, start: int, end: int) -> None:
        if self._bom and base + start < 3:
            start = min(end, 3 - base)
        # Bound temporary decoded strings even when a caller supplies a giant chunk.
        while start < end and (self._selection is not None or self._first is not None):
            stop = min(end, start + 4096)
            block = memoryview(chunk)[start:stop]
            if self._selection is not None:
                self._selected_bytes += len(
                    self._selection.decode(block).encode("utf-8")
                )
            if self._first is not None:
                self._first_bytes += len(self._first.decode(block).encode("utf-8"))
            start = stop

    def _end_first(self, position: int) -> None:
        self._first_end = position
        if self._first is not None:
            self._first_bytes += len(
                self._first.decode(b"", final=True).encode("utf-8")
            )
        self._first = None

    def _end_selection(self, position: int) -> None:
        self._end = position
        if self._selection is not None:
            self._selected_bytes += len(
                self._selection.decode(b"", final=True).encode("utf-8")
            )
        self._selection = None

    def _process(self, chunk: bytes, offset: int) -> None:
        base = self._position - offset
        start = offset
        index = chunk.find(b"\n", start)
        while index != -1:
            self._feed(chunk, base, start, index)
            line = self._newlines
            position = base + index
            if line == self._start_line:
                self._end_first(position)
            if self._end_line is not None and line == self._end_line - 1:
                self._end_selection(position)
            self._feed(chunk, base, index, index + 1)
            start = index + 1
            self._newlines += 1
            self._line_start = position + 1
            if self._newlines == self._start_line:
                self._begin(self._line_start)
            if self._end_line is not None and self._newlines == self._end_line - 1:
                self._last_start = self._line_start
            index = chunk.find(b"\n", start)
        self._feed(chunk, base, start, len(chunk))
        self._position += len(chunk) - offset

    def finish(self) -> LineScan:
        """Finalize decoder tails and return the immutable result exactly once."""
        if self._finished:
            raise RuntimeError("Scanner is finished")
        self._finished = True
        if self._head is not None:
            self._release_head()
        size = self._position
        if self._start is None:
            return LineScan(self._newlines, size, size, size, size, 0, 0)
        if self._first_end is None:
            self._end_first(size)
        if self._end is None:
            self._end_selection(size)
        return LineScan(
            self._newlines,
            self._start,
            self._end if self._end is not None else size,
            self._first_end if self._first_end is not None else size,
            self._last_start if self._last_start is not None else self._line_start,
            self._selected_bytes,
            self._first_bytes,
        )

"""Output handling of the ``!``/``!!`` shell shortcut, from Pi's ``bash-executor.ts``.

Pi runs the editor's ``!`` commands through ``executeBashWithOperations``
(``packages/coding-agent/src/core/bash-executor.ts``, pi-mono ``1b347794e``),
not through the ``bash`` tool's accumulator. :class:`ExecutorOutput` is that
function's output side:

- each chunk is decoded (streaming UTF-8, never flushed at the end, as Pi's
  ``TextDecoder`` is not), ANSI escapes are removed (Pi's ``stripAnsi``, the
  ``ansi-regex`` pattern), control characters are dropped
  (``sanitizeBinaryOutput``) and ``\\r`` is removed;
- a rolling list of chunks keeps at most ``2 × 50 KB`` of text, counted in
  UTF-16 code units like JavaScript's ``string.length``; the oldest chunk is
  dropped while more than one remains;
- once the command has written more than 50 KB (raw bytes), every chunk also
  goes to a temp file ``pipy-bash-<16 hex>.log`` (owner-only, in the system
  temp directory), starting with the chunks still held;
- at the end the kept text is cut with ``truncateTail`` (2000 lines / 50 KB)
  and, when it was cut, the temp file is guaranteed to exist.
"""

from __future__ import annotations

import codecs
import os
import re
import secrets
import tempfile
from collections import deque
from dataclasses import dataclass
from typing import BinaryIO

from pipy_harness.native.tools.truncate import DEFAULT_MAX_BYTES, truncate_tail

# Pi `utils/ansi.ts` (from ansi-regex): OSC sequences up to the first string
# terminator, then CSI and related sequences.
_ST = r"(?:\x07|\x1b\\|\x9c)"
_ANSI = re.compile(
    rf"(?:\x1b\][\s\S]*?{_ST})"
    # JavaScript's `\d` is ASCII-only; Python's would match any digit.
    r"|[\x1b\x9b][\[\]()#;?]*(?:[0-9]{1,4}(?:[;:][0-9]{0,4})*)?"
    r"[0-9A-PR-TZcf-nq-uy=><~]"
)
# Pi `sanitizeBinaryOutput`: C0 controls except tab, LF and CR, and the
# interlinear annotation characters U+FFF9-U+FFFB.
_BINARY = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f" + "".join(map(chr, range(0xFFF9, 0xFFFC))) + "]"
)

_MAX_ROLLING_UNITS = DEFAULT_MAX_BYTES * 2
_TEMP_FILE_PREFIX = "pipy-bash"


def strip_ansi(value: str) -> str:
    """Pi ``stripAnsi``."""

    if "\u001b" not in value and "\u009b" not in value:
        return value
    return _ANSI.sub("", value)


def sanitize_binary_output(value: str) -> str:
    """Pi ``sanitizeBinaryOutput``."""

    return _BINARY.sub("", value)


def utf16_length(text: str) -> int:
    """JavaScript ``string.length``: UTF-16 code units."""

    return len(text) + sum(1 for char in text if ord(char) > 0xFFFF)


@dataclass(frozen=True, slots=True)
class ExecutorResult:
    output: str
    truncated: bool
    full_output_path: str | None


class ExecutorOutput:
    """The output side of Pi's ``executeBashWithOperations``."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._chunks: deque[str] = deque()
        self._units = 0
        self._total_bytes = 0
        self._temp_path: str | None = None
        self._temp_file: BinaryIO | None = None
        self._temp_failed = False

    def sanitize(self, text: str) -> str:
        return sanitize_binary_output(strip_ansi(text)).replace("\r", "")

    def append(self, data: bytes) -> None:
        self._total_bytes += len(data)
        text = self.sanitize(self._decoder.decode(data))
        if self._total_bytes > DEFAULT_MAX_BYTES:
            self._ensure_temp_file()
        self._write_temp(text)
        self._chunks.append(text)
        self._units += utf16_length(text)
        while self._units > _MAX_ROLLING_UNITS and len(self._chunks) > 1:
            self._units -= utf16_length(self._chunks.popleft())

    def finish(self) -> ExecutorResult:
        full = "".join(self._chunks)
        truncation = truncate_tail(full)
        if truncation.truncated:
            self._ensure_temp_file()
        self._close_temp_file()
        return ExecutorResult(
            output=truncation.content if truncation.truncated else full,
            truncated=truncation.truncated,
            full_output_path=self._temp_path,
        )

    def _ensure_temp_file(self) -> None:
        if self._temp_path is not None or self._temp_failed:
            return
        path = os.path.join(
            tempfile.gettempdir(), f"{_TEMP_FILE_PREFIX}-{secrets.token_hex(8)}.log"
        )
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            # Keep the tail and name no file rather than failing the command.
            self._temp_failed = True
            return
        self._temp_path = path
        self._temp_file = os.fdopen(fd, "wb")
        for chunk in self._chunks:
            self._write_temp(chunk)

    def _write_temp(self, text: str) -> None:
        if self._temp_file is None:
            return
        try:
            self._temp_file.write(text.encode("utf-8"))
        except OSError:
            self._abandon_temp_file()

    def _close_temp_file(self) -> None:
        handle, self._temp_file = self._temp_file, None
        if handle is None:
            return
        try:
            handle.close()
        except OSError:
            self._abandon_temp_file()

    def _abandon_temp_file(self) -> None:
        """Drop a temp file that could not be written in full."""

        handle, path = self._temp_file, self._temp_path
        self._temp_file = None
        self._temp_path = None
        self._temp_failed = True
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


__all__ = [
    "ExecutorOutput",
    "ExecutorResult",
    "sanitize_binary_output",
    "strip_ansi",
    "utf16_length",
]

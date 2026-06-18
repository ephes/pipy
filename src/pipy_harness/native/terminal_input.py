"""Low-level terminal input decoding helpers."""

from __future__ import annotations

import os
import select


def read_terminal_utf8_char(
    fd: int,
    *,
    pending_bytes: bytearray,
    continuation_timeout: float = 0.05,
) -> str:
    """Read one UTF-8 character from a terminal file descriptor.

    The raw-mode editors read from ``os.read`` so they can handle control keys
    and escape sequences directly. Reading and decoding one byte at a time
    corrupts non-ASCII input because UTF-8 characters often span multiple bytes.
    This helper reads the full scalar for valid UTF-8 leading bytes while keeping
    ASCII/control bytes as single-byte reads.
    """

    data = _read_one_byte(fd, pending_bytes)
    if not data:
        return ""
    expected_length = _utf8_sequence_length(data[0])
    while len(data) < expected_length:
        readable, _, _ = select.select([fd], [], [], continuation_timeout)
        if fd not in readable:
            break
        chunk = _read_one_byte(fd, pending_bytes)
        if not chunk:
            break
        if not _is_utf8_continuation_byte(chunk[0]):
            pending_bytes.insert(0, chunk[0])
            break
        data += chunk
    decoded = data.decode("utf-8", errors="replace")
    # Key decoders require exactly one scalar per read. The continuation-byte
    # validation above keeps malformed trailing ASCII for the next read; this
    # clamp is the last defense for other malformed byte combinations.
    return decoded[:1]


def _utf8_sequence_length(first_byte: int) -> int:
    if first_byte < 0x80:
        return 1
    if 0xC2 <= first_byte <= 0xDF:
        return 2
    if 0xE0 <= first_byte <= 0xEF:
        return 3
    if 0xF0 <= first_byte <= 0xF4:
        return 4
    return 1


def _is_utf8_continuation_byte(value: int) -> bool:
    return 0x80 <= value <= 0xBF


def _read_one_byte(fd: int, pending_bytes: bytearray) -> bytes:
    if pending_bytes:
        value = pending_bytes.pop(0)
        return bytes((value,))
    try:
        return os.read(fd, 1)
    except (OSError, InterruptedError):
        return b""

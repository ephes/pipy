from __future__ import annotations

import os

from pipy_harness.native.terminal_input import read_terminal_utf8_char


def _read_from_pipe(data: bytes) -> str:
    pending = bytearray()
    read_fd, write_fd = os.pipe()
    os.write(write_fd, data)
    os.close(write_fd)
    try:
        return read_terminal_utf8_char(read_fd, pending_bytes=pending)
    finally:
        os.close(read_fd)


def test_read_terminal_utf8_char_reads_two_byte_scalar() -> None:
    assert _read_from_pipe("ö".encode("utf-8")) == "ö"


def test_read_terminal_utf8_char_reads_four_byte_scalar() -> None:
    assert _read_from_pipe("🙂".encode("utf-8")) == "🙂"


def test_read_terminal_utf8_char_returns_one_replacement_for_malformed_sequence() -> None:
    assert _read_from_pipe(b"\xc3") == "�"


def test_read_terminal_utf8_char_preserves_non_continuation_byte_for_next_read() -> None:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"\xc3(")
    os.close(write_fd)
    pending = bytearray()
    try:
        assert read_terminal_utf8_char(read_fd, pending_bytes=pending) == "�"
        assert read_terminal_utf8_char(read_fd, pending_bytes=pending) == "("
    finally:
        os.close(read_fd)


def test_read_terminal_utf8_char_returns_one_replacement_for_invalid_byte() -> None:
    assert _read_from_pipe(b"\xff") == "�"

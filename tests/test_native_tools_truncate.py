"""Tests for the Pi truncation helpers used by the `read` tool (READ1)."""

from __future__ import annotations

import pytest

from pipy_harness.native.tools.image_mime import detect_supported_image_mime_type
from pipy_harness.native.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
    truncate_head,
)


def test_defaults_match_pi():
    assert DEFAULT_MAX_LINES == 2000
    assert DEFAULT_MAX_BYTES == 50 * 1024


@pytest.mark.parametrize(
    ("size", "label"),
    [
        (0, "0B"),
        (1023, "1023B"),
        (1024, "1.0KB"),
        (1280, "1.3KB"),  # JS toFixed rounds the exact tie 1.25 up
        (1331, "1.3KB"),
        (51200, "50.0KB"),
        (60000, "58.6KB"),
        (1024 * 1024 - 1, "1024.0KB"),
        (1024 * 1024, "1.0MB"),
        (int(1.25 * 1024 * 1024), "1.3MB"),
    ],
)
def test_format_size_matches_pi(size: int, label: str):
    assert format_size(size) == label


def test_truncate_head_no_truncation_keeps_content_and_counts_like_pi():
    result = truncate_head("a\nb\n")

    assert result.truncated is False
    assert result.truncated_by is None
    assert result.content == "a\nb\n"
    # The trailing newline does not count as a line.
    assert result.total_lines == 2
    assert result.total_bytes == 4


def test_truncate_head_empty_content():
    result = truncate_head("")

    assert result.truncated is False
    assert result.total_lines == 0


def test_truncate_head_by_lines():
    result = truncate_head("1\n2\n3\n4\n", max_lines=2)

    assert result.truncated is True
    assert result.truncated_by == "lines"
    assert result.content == "1\n2"
    assert result.output_lines == 2
    assert result.total_lines == 4


def test_truncate_head_by_bytes_never_splits_a_line():
    # "aaa" (3) + "\nbbb" (4) = 7 bytes fits in 8; "\nccc" would make 11.
    result = truncate_head("aaa\nbbb\nccc", max_bytes=8)

    assert result.truncated_by == "bytes"
    assert result.content == "aaa\nbbb"
    assert result.output_bytes == 7
    assert result.last_line_partial is False


def test_truncate_head_counts_utf8_bytes():
    result = truncate_head("éé\nx", max_bytes=4)

    assert result.content == "éé"
    assert result.truncated_by == "bytes"


def test_truncate_head_first_line_over_limit():
    result = truncate_head("abcdef\nx", max_bytes=5)

    assert result.first_line_exceeds_limit is True
    assert result.content == ""
    assert result.output_lines == 0
    assert result.truncated_by == "bytes"


def test_truncate_head_line_limit_wins_when_both_would_apply():
    # Two lines of 3 bytes: the line limit stops first even though a later
    # line would also break the byte limit.
    result = truncate_head("aaa\nbbb\ncccccccccc", max_lines=2, max_bytes=8)

    assert result.truncated_by == "lines"
    assert result.content == "aaa\nbbb"


def test_image_detection_rejects_short_and_unknown_buffers():
    assert detect_supported_image_mime_type(b"") is None
    assert detect_supported_image_mime_type(b"plain text") is None
    assert detect_supported_image_mime_type(b"BM" + b"\x00" * 30) is None
    assert detect_supported_image_mime_type(b"\x89PNG\r\n\x1a\n") is None

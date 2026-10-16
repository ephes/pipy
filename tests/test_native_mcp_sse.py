"""Tests for the server-sent event parser of the MCP HTTP transport."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

import pytest

from pipy_harness.native.mcp.protocol import McpProtocolError
from pipy_harness.native.mcp.sse import SseEvent, consume_sse_stream


def _events(
    chunks: Iterable[bytes], max_event_bytes: int | None = None
) -> list[SseEvent]:
    events: list[SseEvent] = []
    if max_event_bytes is None:
        consume_sse_stream(chunks, on_event=events.append)
    else:
        consume_sse_stream(
            chunks, on_event=events.append, max_event_bytes=max_event_bytes
        )
    return events


def test_parses_chunked_crlf_events_comments_ids_and_multiline_data() -> None:
    # Pi: "parses chunked CRLF events, comments, IDs, and multiline data"
    chunks = [b': keepalive\r\nid: 7\r\ndata: {"one":\r\n', b"data: 1}\r\n\r\n"]

    assert _events(chunks) == [SseEvent(data='{"one":\n1}', id="7")]


def test_rejects_data_lines_over_the_limit_without_a_blank_line() -> None:
    # Pi: "rejects events whose data lines exceed the limit without a blank line"
    sent = 0

    def endless() -> Iterator[bytes]:
        nonlocal sent
        while sent <= 1000:
            sent += 1
            # Never sends a blank line, so the event is never dispatched.
            yield b"data: xxxxxxxxxxxxxxxx\n"

    with pytest.raises(McpProtocolError, match="^MCP SSE event exceeds 256 bytes$"):
        _events(endless(), max_event_bytes=256)
    assert sent < 100


def test_data_limit_counts_the_joining_newlines() -> None:
    # Two lines of four bytes and the newline between them are nine bytes.
    stream = [b"data: aaaa\ndata: bbbb\n\n"]

    assert _events(stream, max_event_bytes=9) == [SseEvent(data="aaaa\nbbbb")]
    with pytest.raises(McpProtocolError, match="exceeds 8 bytes"):
        _events(stream, max_event_bytes=8)


def test_data_limit_starts_again_with_each_event() -> None:
    stream = [b"data: aaaa\n\ndata: bbbb\n\n"]

    assert _events(stream, max_event_bytes=4) == [SseEvent("aaaa"), SseEvent("bbbb")]


def test_rejects_a_line_over_the_limit_that_never_ends() -> None:
    read = 0

    def endless() -> Iterator[bytes]:
        nonlocal read
        while read <= 1000:
            read += 1
            yield b"event: " + b"x" * 16

    with pytest.raises(McpProtocolError, match="^MCP SSE event exceeds 256 bytes$"):
        _events(endless(), max_event_bytes=256)
    assert read < 100


def test_line_limit_counts_bytes_not_characters() -> None:
    # Three characters, nine bytes.
    partial = "€€€".encode()

    assert _events([b"data: ok\n\n", partial[:9]], max_event_bytes=9) == [
        SseEvent("ok")
    ]
    with pytest.raises(McpProtocolError, match="exceeds 8 bytes"):
        _events([b"data: ok\n\n", partial], max_event_bytes=8)


def test_a_complete_long_line_that_is_not_data_is_not_limited() -> None:
    comment = b":" + b"x" * 100 + b"\n"

    assert _events([comment + b"data: ok\n\n"], max_event_bytes=8) == [SseEvent("ok")]


def test_lf_line_ends_and_event_names() -> None:
    stream = [b"event: message\ndata: one\n\nevent: ping\ndata: two\n\n"]

    assert _events(stream) == [
        SseEvent(data="one", event="message"),
        SseEvent(data="two", event="ping"),
    ]


def test_empty_event_name_and_id_are_absent() -> None:
    assert _events([b"event:\nid:\ndata: x\n\n"]) == [SseEvent(data="x")]


def test_an_event_without_data_is_not_reported_and_resets_name_and_id() -> None:
    stream = [b"event: ping\nid: 1\n\ndata: x\n\n"]

    assert _events(stream) == [SseEvent(data="x")]


def test_an_empty_data_line_is_data() -> None:
    assert _events([b"data\n\ndata:\n\ndata: \ndata\n\n"]) == [
        SseEvent(""),
        SseEvent(""),
        SseEvent("\n"),
    ]


def test_only_one_leading_space_is_dropped_from_a_value() -> None:
    assert _events([b"data:  two\ndata:none\n\n"]) == [SseEvent(" two\nnone")]


def test_unknown_fields_and_lines_without_a_colon_are_ignored() -> None:
    assert _events([b"other: 1\nnoise\ndata: x\n\n"]) == [SseEvent("x")]


def test_an_event_open_at_the_end_of_the_stream_is_reported() -> None:
    assert _events([b"id: 3\ndata: tail"]) == [SseEvent(data="tail", id="3")]
    assert _events([b"data: tail\r"]) == [SseEvent(data="tail")]
    assert _events([]) == []
    assert _events([b"", b"\n\n"]) == []


def test_on_id_gets_every_id_also_without_data() -> None:
    # A server primes resumption with an event that has an id and no data.
    ids: list[str] = []
    events: list[SseEvent] = []

    consume_sse_stream(
        [b"id: 1\n\nid: bad\x00id\ndata: a\n\nid:\ndata: b\n\nid: 4\ndata: c\n\n"],
        on_event=events.append,
        on_id=ids.append,
    )

    assert ids == ["1", "", "4"]
    # An id that contains NUL is ignored.
    assert events == [SseEvent("a"), SseEvent("b"), SseEvent("c", id="4")]


def test_on_retry_gets_decimal_values_only() -> None:
    delays: list[int] = []

    consume_sse_stream(
        [
            b"retry: 5\nretry:1500\nretry: 1.5\nretry: -1\nretry:\nretry: 1e3\n"
            + b"retry: "
            + b"9" * 5000
            + b"\n"
            + "retry: ٣\n".encode()
        ],
        on_event=lambda event: None,
        on_retry=delays.append,
    )

    assert delays == [5, 1500]


def test_text_split_inside_a_character_and_a_line_end() -> None:
    raw = "data: grüße €\r\n\r\n".encode()

    for cut in range(1, len(raw)):
        assert _events([raw[:cut], raw[cut:]]) == [SseEvent("grüße €")], cut
    assert _events([bytes([byte]) for byte in raw]) == [SseEvent("grüße €")]


def test_a_byte_order_mark_is_dropped_and_invalid_bytes_are_replaced() -> None:
    assert _events([b"\xef\xbb\xbfdata: a\xffb\n\n"]) == [SseEvent("a�b")]
    assert _events([b"data: cut \xe2\x82"]) == [SseEvent("cut �")]


def test_a_failing_callback_ends_the_stream_with_its_error() -> None:
    read: list[int] = []

    def chunks() -> Iterator[bytes]:
        for number in range(3):
            read.append(number)
            yield b"data: x\n\n"

    def fail(event: SseEvent) -> None:
        raise ValueError("stop")

    with pytest.raises(ValueError, match="stop"):
        consume_sse_stream(chunks(), on_event=fail)
    assert read == [0]

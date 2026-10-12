"""A parser for server-sent event streams, as MCP servers send them.

``consume_sse_stream`` reads a stream of byte chunks to its end and reports
every event. It is the reader of both streamable HTTP response streams and the
server-to-client stream.
"""

from __future__ import annotations

import codecs
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from pipy_harness.native.mcp.protocol import McpProtocolError
from pipy_harness.native.mcp.transport import DEFAULT_MAX_MESSAGE_BYTES


@dataclass(frozen=True, slots=True)
class SseEvent:
    """One event. ``event`` and ``id`` are ``None`` when absent or empty."""

    data: str
    event: str | None = None
    id: str | None = None


_MAX_RETRY_DIGITS = 18


def _utf8_length(text: str) -> int:
    return len(text.encode("utf-8"))


class _SseParser:
    """The pending event of one stream."""

    def __init__(
        self,
        max_event_bytes: int,
        on_event: Callable[[SseEvent], None],
        on_id: Callable[[str], None] | None,
        on_retry: Callable[[int], None] | None,
    ) -> None:
        self._max_event_bytes = max_event_bytes
        self._on_event = on_event
        self._on_id = on_id
        self._on_retry = on_retry
        self._event_name: str | None = None
        self._event_id: str | None = None
        self._data_lines: list[str] = []
        # Bytes of the pending event's data, including the "\n" joins, so an
        # event sent as many short `data:` lines without a closing blank line
        # cannot grow without bound.
        self._data_bytes = 0

    def too_large(self) -> McpProtocolError:
        return McpProtocolError(f"MCP SSE event exceeds {self._max_event_bytes} bytes")

    def dispatch(self) -> None:
        """End the pending event; report it when it has data."""

        data_lines = self._data_lines
        event_name = self._event_name
        event_id = self._event_id
        self._data_lines = []
        self._data_bytes = 0
        self._event_name = None
        self._event_id = None
        if data_lines:
            self._on_event(
                SseEvent("\n".join(data_lines), event_name or None, event_id or None)
            )

    def process_line(self, raw_line: str) -> None:
        line = raw_line.removesuffix("\r")
        if not line:
            self.dispatch()
            return
        if line.startswith(":"):
            return
        field, _, value = line.partition(":")
        self._set_field(field, value.removeprefix(" "))

    def _set_field(self, field: str, value: str) -> None:
        if field == "data":
            self._data_bytes += _utf8_length(value) + (1 if self._data_lines else 0)
            if self._data_bytes > self._max_event_bytes:
                raise self.too_large()
            self._data_lines.append(value)
        elif field == "event":
            self._event_name = value
        elif field == "id" and "\0" not in value:
            self._event_id = value
            if self._on_id is not None:
                self._on_id(value)
        elif field == "retry" and value.isascii() and value.isdigit():
            # More digits than a delay can have are also more than `int` takes.
            if self._on_retry is not None and len(value) <= _MAX_RETRY_DIGITS:
                self._on_retry(int(value))


def consume_sse_stream(
    chunks: Iterable[bytes],
    *,
    on_event: Callable[[SseEvent], None],
    on_id: Callable[[str], None] | None = None,
    on_retry: Callable[[int], None] | None = None,
    max_event_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
) -> None:
    """Read ``chunks`` to the end, calling ``on_event`` for each event with data.

    Lines end with LF or CRLF. A line starting with ``:`` is a comment, several
    ``data`` lines are joined with a newline, and an event without data is not
    reported. ``on_id`` is called for every ``id`` field, also on an event
    without data (a server primes stream resumption that way); an id that
    contains NUL is ignored. ``on_retry`` gets every ``retry`` field that is a
    decimal number, in milliseconds. An event still open when the stream ends
    is reported. The text is UTF-8; invalid bytes are replaced.

    Raises ``McpProtocolError`` when a line or an event's data exceeds
    ``max_event_bytes``. Whatever the callbacks and the iteration of ``chunks``
    raise passes through.
    """

    parser = _SseParser(max_event_bytes, on_event, on_id, on_retry)
    # "utf-8-sig" drops a byte order mark at the start of the stream.
    decoder = codecs.getincrementaldecoder("utf-8-sig")(errors="replace")
    # The line that has no newline yet, in the pieces it arrived in.
    pending: list[str] = []
    pending_bytes = 0

    def feed(text: str) -> None:
        nonlocal pending_bytes
        head, newline, rest = text.partition("\n")
        if not newline:
            if text:
                pending.append(text)
                pending_bytes += _utf8_length(text)
            return
        *lines, tail = rest.split("\n")
        first = "".join(pending) + head
        pending[:] = [tail] if tail else []
        pending_bytes = _utf8_length(tail)
        parser.process_line(first)
        for line in lines:
            parser.process_line(line)

    for chunk in chunks:
        feed(decoder.decode(chunk))
        if pending_bytes > max_event_bytes:
            raise parser.too_large()
    feed(decoder.decode(b"", final=True))
    if pending:
        parser.process_line("".join(pending))
    parser.dispatch()

"""Focused tests for the shared ``native.http`` streaming/retry primitives.

Slice 5.1 (Codex sub-slice) centralizes the SSE framing and the
transport-exception retry classifier in ``native.http`` so the Codex adapter and
the later protocol-family migrations share one owner. These tests assert the
shared primitives directly; the end-to-end Codex behavior stays covered by the
provider/retry suites.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import urllib.error

from pipy_harness.native.http import (
    iter_sse_event_payloads,
    transport_exception_retryable,
)


def test_iter_sse_event_payloads_frames_data_lines_and_records_body() -> None:
    lines = [
        b": keepalive\n",
        b"data: {\"type\": \"a\"}\n",
        b"\n",
        b"data: {\"type\": \"b\", ",
        b"data: \"partial\": 1}\n",
        b"\n",
        b"data: [DONE]\n",
        b"\n",
    ]
    collected: list[str] = []
    payloads = list(iter_sse_event_payloads(iter(lines), collected))
    assert payloads == ['{"type": "a"}', '{"type": "b",\n"partial": 1}']
    # Every raw line (comments, data, blanks, and the [DONE] sentinel) is
    # retained for the after-the-fact transcript.
    assert len(collected) == len(lines)


def test_iter_sse_event_payloads_flushes_trailing_event_without_blank_line() -> None:
    collected: list[str] = []
    payloads = list(iter_sse_event_payloads(iter(["data: {\"x\": 1}"]), collected))
    assert payloads == ['{"x": 1}']


def test_transport_exception_retryable_classifies_transient_and_permanent() -> None:
    assert transport_exception_retryable(TimeoutError()) is True
    assert transport_exception_retryable(socket.gaierror()) is True
    assert transport_exception_retryable(ConnectionResetError()) is True
    assert transport_exception_retryable(ssl.SSLError()) is True
    assert (
        transport_exception_retryable(http.client.RemoteDisconnected("closed"))
        is True
    )
    assert transport_exception_retryable(ssl.SSLCertVerificationError()) is False
    assert (
        transport_exception_retryable(http.client.BadStatusLine("")) is False
    )


def test_transport_exception_retryable_unwraps_urlerror_reason() -> None:
    wrapped = urllib.error.URLError(TimeoutError())
    assert transport_exception_retryable(wrapped) is True
    assert transport_exception_retryable(urllib.error.URLError("plain")) is None


def test_transport_exception_retryable_returns_none_for_unrelated() -> None:
    assert transport_exception_retryable(ValueError("nope")) is None

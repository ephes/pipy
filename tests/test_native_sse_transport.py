"""Ownership and real-wire framing proofs for the shared SSE payload stream."""

from __future__ import annotations

import threading
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.http import SsePayloadStream, open_url_cancellable
from pipy_harness.native.openai_codex_provider import (
    OpenAICodexResponseParseError,
    OpenAICodexStreamInterruptedError,
    UrllibSseHTTPClient,
    _parse_sse_response,
)


class _Response:
    def __init__(self, lines=(), error=None):
        self.lines = iter(lines)
        self.error = error
        self.reads = 0
        self.closes = 0

    def getcode(self):
        return 200

    def __iter__(self):
        return self

    def __next__(self):
        self.reads += 1
        if self.error is not None:
            raise self.error
        return next(self.lines)

    def close(self):
        self.closes += 1


def test_payload_stream_closes_before_first_read_and_close_is_idempotent():
    response = _Response([b'data: {"ignored":true}\n\n'])
    stream = SsePayloadStream(response)
    stream.close()
    stream.close()
    assert list(stream) == []
    assert response.reads == 0
    assert response.closes == 1


def test_payload_stream_closes_at_eof_and_returns_raw_non_json_payloads():
    response = _Response([b"data: raw-payload\n", b"\n"])
    stream = SsePayloadStream(response)
    assert list(stream) == ["raw-payload"]
    stream.close()
    assert response.closes == 1


@pytest.mark.parametrize(
    "error", [OSError("read failed"), ValueError("bad read"), RuntimeError("bug")]
)
def test_payload_stream_closes_and_preserves_uncancelled_read_failures(error):
    response = _Response(error=error)
    with pytest.raises(type(error)) as caught:
        next(SsePayloadStream(response))
    assert caught.value is error
    assert response.closes == 1


def test_payload_stream_cancellation_at_clean_eof_wins_over_success():
    token = CancelToken()

    class CancelAtEof(_Response):
        def __next__(self):
            token.cancel()
            raise StopIteration

    response = CancelAtEof()
    with pytest.raises(ProviderCancelledError):
        next(SsePayloadStream(response, cancel_token=token))
    assert response.closes == 1


@pytest.mark.parametrize("pre_cancelled", [True, False])
def test_payload_stream_never_yields_after_cancellation(pre_cancelled):
    token = CancelToken()

    class CancelBeforeYield(_Response):
        def __next__(self):
            raw = super().__next__()
            if raw == b"\n":
                token.cancel()
            return raw

    response = CancelBeforeYield([b"data: must-not-escape\n", b"\n"])
    if pre_cancelled:
        token.cancel()
    with pytest.raises(ProviderCancelledError):
        next(SsePayloadStream(response, cancel_token=token))
    assert response.closes == 1
    if pre_cancelled:
        assert response.reads == 0


def test_codex_event_iterator_close_releases_response_before_first_iteration(
    monkeypatch,
):
    raw = _Response()
    monkeypatch.setattr(
        "pipy_harness.native.openai_codex_provider.open_url_cancellable",
        lambda *args, **kwargs: raw,
    )
    response = UrllibSseHTTPClient().post_sse(
        "https://example.invalid/responses", headers={}, body={}, timeout_seconds=1
    )
    assert response.event_stream is not None
    response.event_stream.close()
    assert raw.reads == 0
    assert raw.closes == 1
    response.close()
    assert raw.closes == 1


@pytest.mark.parametrize(
    "ending", ["completed", "error", "eof", "cancel", "sink-error"]
)
def test_codex_consumer_closes_transport_on_every_exit(monkeypatch, ending):
    token = CancelToken()
    lines = [b'data: {"type":"response.output_text.delta","delta":"answer"}\n', b"\n"]
    if ending == "completed":
        lines += [
            b'data: {"type":"response.completed","response":{"status":"completed"}}\n',
            b"\n",
        ]
    elif ending == "error":
        lines += [b'data: {"type":"error","message":"fixture failure"}\n', b"\n"]
    # Any read after terminal or error is a defect. EOF deliberately has no tail.
    if ending in {"completed", "error"}:
        lines += [b"data: MUST_NOT_BE_READ\n", b"\n"]
    raw = _Response(lines)
    monkeypatch.setattr(
        "pipy_harness.native.openai_codex_provider.open_url_cancellable",
        lambda *args, **kwargs: raw,
    )
    response = UrllibSseHTTPClient().post_sse(
        "https://example.invalid/responses",
        headers={},
        body={},
        timeout_seconds=1,
        cancel_token=token,
    )
    chunks = []

    def sink(chunk):
        chunks.append(chunk)
        if ending == "sink-error":
            raise RuntimeError("sink failed")
        if ending == "cancel":
            token.cancel()

    expected = {
        "error": OpenAICodexResponseParseError,
        "eof": OpenAICodexStreamInterruptedError,
        "cancel": ProviderCancelledError,
        "sink-error": RuntimeError,
    }
    if ending == "completed":
        parsed = _parse_sse_response(
            "", event_stream=response.event_stream, stream_sink=sink, cancel_token=token
        )
        assert parsed.final_text == "answer"
    else:
        with pytest.raises(expected[ending]):
            _parse_sse_response(
                "",
                event_stream=response.event_stream,
                stream_sink=sink,
                cancel_token=token,
            )
    assert chunks == ["answer"]
    assert raw.closes == 1
    if ending in {"completed", "error"}:
        assert raw.reads == 4


def test_codex_status_read_failure_closes_opened_response(monkeypatch):
    class BrokenStatus(_Response):
        def getcode(self):
            raise RuntimeError("status read failed")

    raw = BrokenStatus()
    monkeypatch.setattr(
        "pipy_harness.native.openai_codex_provider.open_url_cancellable",
        lambda *args, **kwargs: raw,
    )
    with pytest.raises(RuntimeError, match="status read failed"):
        UrllibSseHTTPClient().post_sse(
            "https://example.invalid/responses", headers={}, body={}, timeout_seconds=1
        )
    assert raw.closes == 1


@contextmanager
def _wire_response(
    initial: list[bytes],
    final: list[bytes],
    *,
    before_headers: bool = False,
    close_delimited: bool = False,
    declared_length: int | None = None,
) -> Iterator[tuple[str, threading.Event, threading.Event]]:
    paused, release = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            try:
                if before_headers:
                    paused.set()
                    assert release.wait(10)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                if not close_delimited:
                    length = (
                        declared_length
                        if declared_length is not None
                        else sum(map(len, initial + final))
                    )
                    self.send_header("Content-Length", str(length))
                self.end_headers()
                for fragment in initial:
                    self.wfile.write(fragment)
                    self.wfile.flush()
                if not before_headers:
                    paused.set()
                    assert release.wait(10)
                for fragment in final:
                    self.wfile.write(fragment)
                    self.wfile.flush()
            except OSError:
                # Cancellation/early close deliberately retires the peer.
                pass

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/responses", paused, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


def _open(url, token):
    return open_url_cancellable(
        urllib.request.Request(url, data=b"{}", method="POST"),
        timeout_seconds=None,
        cancel_token=token,
    )


def test_real_http_frames_fragmented_utf8_crlf_comments_multiline_and_trailing_event():
    token = CancelToken()
    # The barrier splits a Unicode character before its line terminator. Further
    # writes split CRLF and SSE field boundaries; urllib supplies complete lines.
    initial = [b": keepalive\r\n", b"data: caf\xc3"]
    final = [
        b"\xa9\r",
        b"\n\r",
        b"\ndata: first\r\n",
        b"da",
        b"ta: second\r\n\r\n",
        b"data: trailing",
    ]
    with _wire_response(initial, final) as (url, paused, release):
        with ThreadPoolExecutor(1) as pool:
            result = pool.submit(
                lambda: list(SsePayloadStream(_open(url, token), cancel_token=token))
            )
            try:
                assert paused.wait(5)
                assert not result.done()
                release.set()
                assert result.result(timeout=5) == ["café", "first\nsecond", "trailing"]
            finally:
                token.cancel()


@pytest.mark.parametrize("phase", ["headers", "body", "close-delimited-body"])
def test_real_sse_cancellation_interrupts_headers_and_blocking_body(phase):
    token = CancelToken()
    opened = []
    initial = [b"data: unfinished"]
    with _wire_response(
        initial,
        [],
        before_headers=phase == "headers",
        close_delimited=phase == "close-delimited-body",
        declared_length=4096,
    ) as (url, paused, release):

        def consume():
            response = _open(url, token)
            opened.append(response)
            return list(SsePayloadStream(response, cancel_token=token))

        with ThreadPoolExecutor(1) as pool:
            result = pool.submit(consume)
            try:
                assert paused.wait(5)
                assert not result.done()
                token.cancel()
                with pytest.raises(ProviderCancelledError):
                    result.result(timeout=5)
                assert not release.is_set(), "cancellation needed server-side EOF"
                if opened:
                    assert opened[0].closed
            finally:
                token.cancel()
                release.set()


def test_real_sse_close_before_first_read_releases_opened_response():
    token = CancelToken()
    with _wire_response([b"data: unread\n\n"], [b"data: withheld\n\n"]) as (
        url,
        paused,
        release,
    ):
        response = _open(url, token)
        try:
            assert paused.wait(5)
            stream = SsePayloadStream(response, cancel_token=token)
            stream.close()
            assert response.closed
            assert list(stream) == []
            assert not release.is_set()
        finally:
            token.cancel()


@pytest.mark.parametrize("ending", ["completed", "error"])
def test_real_codex_terminal_or_error_closes_without_waiting_for_eof(
    monkeypatch, ending
):
    token = CancelToken()
    opened = []
    import pipy_harness.native.openai_codex_provider as codex

    original = codex.open_url_cancellable

    def tracked_open(*args, **kwargs):
        response = original(*args, **kwargs)
        opened.append(response)
        return response

    monkeypatch.setattr(codex, "open_url_cancellable", tracked_open)
    first = b'data: {"type":"response.output_text.delta","delta":"answer"}\n\n'
    terminal = (
        b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
        if ending == "completed"
        else b'data: {"type":"error","message":"fixture failure"}\n\n'
    )
    with _wire_response([first, terminal], [b"data: late\n\n"]) as (
        url,
        paused,
        release,
    ):
        chunks = []

        def consume():
            response = UrllibSseHTTPClient().post_sse(
                url, headers={}, body={}, timeout_seconds=None, cancel_token=token
            )
            return _parse_sse_response(
                "",
                event_stream=response.event_stream,
                stream_sink=chunks.append,
                cancel_token=token,
            )

        with ThreadPoolExecutor(1) as pool:
            result = pool.submit(consume)
            try:
                assert paused.wait(5)
                if ending == "completed":
                    assert result.result(timeout=5).final_text == "answer"
                else:
                    with pytest.raises(OpenAICodexResponseParseError):
                        result.result(timeout=5)
                assert not release.is_set(), "consumer waited for server-side EOF"
                assert chunks == ["answer"]
                assert opened[0].closed
            finally:
                token.cancel()
                release.set()

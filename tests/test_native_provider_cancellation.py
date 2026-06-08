"""Provider-boundary cancellation tests.

These cover the two proofs the active-turn cancellation goal calls for:

* a real-urllib proof that ``cancel()`` closes a live HTTP connection and
  unblocks a worker stuck in ``response.read()`` (``urlopen_read_cancellable``);
* a deterministic fake-HTTP proof that a real provider adapter threads the
  cancel token down to its ``post_json`` boundary and observes cancellation.
"""

from __future__ import annotations

import socket
import threading
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native._provider_helpers import (
    JsonResponse,
    urlopen_read_cancellable,
)
from pipy_harness.native.anthropic_provider import AnthropicProvider
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.models import ProviderRequest


class _HangingHTTPServer:
    """A minimal HTTP server that stalls a request, optionally after headers.

    With ``send_headers=True`` it sends headers plus one body byte, then stalls
    the rest of the body — ``urlopen`` returns and ``response.read()`` blocks.
    With ``send_headers=False`` it accepts the connection but sends *nothing*,
    so ``urlopen`` itself blocks inside ``getresponse()`` waiting for headers —
    the non-streaming-JSON state that the cancel token must also interrupt.
    """

    def __init__(
        self, *, send_headers: bool = True, connection_close: bool = False
    ) -> None:
        self._send_headers = send_headers
        self._connection_close = connection_close
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self.request_received = threading.Event()
        self.headers_sent = threading.Event()
        self._stop = threading.Event()
        self._conns: list[socket.socket] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _addr = self._sock.accept()
        except OSError:
            return
        self._conns.append(conn)
        try:
            conn.recv(4096)  # consume the request line + headers
            self.request_received.set()
            if self._send_headers and self._connection_close:
                # Close-delimited body (Connection: close, no Content-Length):
                # http.client marks the response will_close and detaches
                # conn.sock, so the cancel path must shut down the captured
                # socket, not conn.sock. Send headers + one byte, then stall.
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Connection: close\r\n\r\n"
                    b"{"
                )
                self.headers_sent.set()
            elif self._send_headers:
                # Promise a large body, send only headers + one byte, then stall.
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: 4096\r\n\r\n"
                    b"{"
                )
                self.headers_sent.set()
            # Hold the connection open without sending (the rest of) the body.
            self._stop.wait(timeout=5)
        except OSError:
            pass

    def close(self) -> None:
        self._stop.set()
        for conn in self._conns:
            try:
                conn.close()
            except OSError:
                pass
        try:
            self._sock.close()
        except OSError:
            pass


def test_urlopen_read_cancellable_interrupts_blocking_read() -> None:
    server = _HangingHTTPServer()
    server.start()
    token = CancelToken()
    outcome: list[str] = []
    finished = threading.Event()

    def _worker() -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.port}/v1/messages",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen_read_cancellable(
                request, timeout_seconds=5.0, cancel_token=token
            )
            outcome.append("completed")
        except ProviderCancelledError:
            outcome.append("cancelled")
        except Exception as exc:  # noqa: BLE001 - surface unexpected failures
            outcome.append(f"error:{type(exc).__name__}")
        finally:
            finished.set()

    worker = threading.Thread(target=_worker)
    worker.start()
    try:
        assert server.headers_sent.wait(timeout=5), "server never sent headers"
        # The worker is now blocked inside response.read(); cancel must close
        # the socket and unblock it promptly.
        token.cancel()
        assert finished.wait(timeout=5), "cancel did not unblock the read"
    finally:
        server.close()
        worker.join(timeout=5)

    assert outcome == ["cancelled"]
    assert not worker.is_alive()


def test_urlopen_read_cancellable_interrupts_header_wait() -> None:
    """Cancel interrupts a non-streaming request blocked waiting for headers.

    This is the load-bearing case: a non-streaming JSON API does not send
    response headers until the model has finished generating, so the worker is
    blocked *inside* ``urlopen()``/``getresponse()`` — before any response
    object exists. Registering the underlying connection must let ``cancel()``
    close the socket during that wait, not only during the body read.
    """

    server = _HangingHTTPServer(send_headers=False)
    server.start()
    token = CancelToken()
    outcome: list[str] = []
    finished = threading.Event()

    def _worker() -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.port}/v1/messages",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen_read_cancellable(
                request, timeout_seconds=5.0, cancel_token=token
            )
            outcome.append("completed")
        except ProviderCancelledError:
            outcome.append("cancelled")
        except Exception as exc:  # noqa: BLE001 - surface unexpected failures
            outcome.append(f"error:{type(exc).__name__}")
        finally:
            finished.set()

    worker = threading.Thread(target=_worker)
    worker.start()
    try:
        # The server received the request but is sending no headers; the worker
        # is now blocked inside urlopen() waiting for the response line.
        assert server.request_received.wait(timeout=5), "server never got request"
        assert not server.headers_sent.is_set()
        token.cancel()
        assert finished.wait(timeout=5), "cancel did not unblock the header wait"
    finally:
        server.close()
        worker.join(timeout=5)

    assert outcome == ["cancelled"]
    assert not worker.is_alive()


def test_urlopen_read_cancellable_interrupts_connection_close_body() -> None:
    """Cancel interrupts a blocked read on a ``Connection: close`` response.

    For a close-delimited response ``http.client.getresponse()`` hands the
    socket to the ``HTTPResponse`` and sets ``conn.sock = None`` while the body
    read still blocks. The closer must shut down the socket it captured at
    connect time, not look it up on the connection at cancel time.
    """

    server = _HangingHTTPServer(send_headers=True, connection_close=True)
    server.start()
    token = CancelToken()
    outcome: list[str] = []
    finished = threading.Event()

    def _worker() -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.port}/v1/messages",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen_read_cancellable(
                request, timeout_seconds=5.0, cancel_token=token
            )
            outcome.append("completed")
        except ProviderCancelledError:
            outcome.append("cancelled")
        except Exception as exc:  # noqa: BLE001 - surface unexpected failures
            outcome.append(f"error:{type(exc).__name__}")
        finally:
            finished.set()

    worker = threading.Thread(target=_worker)
    worker.start()
    try:
        assert server.headers_sent.wait(timeout=5), "server never sent headers"
        token.cancel()
        assert finished.wait(timeout=5), "cancel did not unblock the close-body read"
    finally:
        server.close()
        worker.join(timeout=5)

    assert outcome == ["cancelled"]
    assert not worker.is_alive()


class _AttributeErrorOnReadResponse:
    """Fake urllib response whose body read raises ``AttributeError``.

    Reproduces CPython's ``http.client`` shutdown race deterministically: when
    :meth:`CancelToken.cancel` shuts the socket down mid-read,
    ``HTTPResponse._close_conn`` can call ``fp.close()`` after a concurrent path
    already set ``self.fp = None``, surfacing ``AttributeError: 'NoneType'
    object has no attribute 'close'`` instead of an ``OSError``.
    """

    def getcode(self) -> int:
        return 200

    def read(self) -> bytes:
        raise AttributeError("'NoneType' object has no attribute 'close'")

    def close(self) -> None:
        pass


def test_urlopen_read_cancellable_maps_attributeerror_when_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read ``AttributeError`` after cancel is a cancellation, not a failure.

    Shutting the socket down mid-read can race ``http.client`` into raising
    ``AttributeError`` rather than ``OSError``. With the token cancelled the
    helper must still surface :class:`ProviderCancelledError` so the abort path
    is taken instead of leaking a spurious provider error.
    """

    import pipy_harness.native._provider_helpers as helpers

    monkeypatch.setattr(
        helpers,
        "open_url_cancellable",
        lambda *a, **k: _AttributeErrorOnReadResponse(),
    )
    token = CancelToken()
    token.cancel()
    request = urllib.request.Request("http://127.0.0.1:1/", method="POST")

    with pytest.raises(ProviderCancelledError):
        urlopen_read_cancellable(request, timeout_seconds=5.0, cancel_token=token)


def test_urlopen_read_cancellable_attributeerror_propagates_without_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ``AttributeError`` that is *not* a cancellation must still surface.

    The cancel mapping is scoped to ``cancel_token.cancelled``: a genuine bug
    that raises ``AttributeError`` while the token is live must propagate as
    itself, never be silently swallowed as a cancellation.
    """

    import pipy_harness.native._provider_helpers as helpers

    monkeypatch.setattr(
        helpers,
        "open_url_cancellable",
        lambda *a, **k: _AttributeErrorOnReadResponse(),
    )
    token = CancelToken()  # never cancelled
    request = urllib.request.Request("http://127.0.0.1:1/", method="POST")

    with pytest.raises(AttributeError):
        urlopen_read_cancellable(request, timeout_seconds=5.0, cancel_token=token)


def test_urlopen_read_cancellable_passthrough_without_token() -> None:
    """With no token the helper behaves like a plain urlopen+read."""

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    body = b'{"ok": true}'

    def _serve() -> None:
        conn, _ = server.accept()
        with conn:
            conn.recv(4096)
            header = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
            ).encode()
            conn.sendall(header + body)

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/", data=b"{}", method="POST"
        )
        status, payload = urlopen_read_cancellable(request, timeout_seconds=5.0)
    finally:
        server.close()
        thread.join(timeout=5)

    assert status == 200
    assert payload == body


class _BlockingCancelHTTPClient:
    """Fake JSON client that blocks until the cancel token closes it.

    Proves a provider adapter forwards the cancel token to its HTTP boundary:
    ``post_json`` registers a closeable on the token and waits; ``cancel()``
    closes it and the client raises ``ProviderCancelledError`` from inside the
    provider, exactly as the real urllib client would.
    """

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.received_token: CancelToken | None = None

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
        cancel_token: CancelToken | None = None,
    ) -> JsonResponse:
        del url, headers, body, timeout_seconds
        self.received_token = cancel_token
        release = threading.Event()

        class _Closeable:
            def close(self) -> None:
                release.set()

        self.entered.set()
        if cancel_token is None:
            raise AssertionError("provider did not forward a cancel token")
        cancel_token.register(_Closeable())
        try:
            if not release.wait(timeout=5):
                raise AssertionError("cancel() never closed the connection")
            raise ProviderCancelledError("native provider turn cancelled")
        finally:
            cancel_token.unregister(_Closeable())


def test_codex_sse_parse_raises_on_cancel_after_clean_stream_eof() -> None:
    """A cancel that ends the SSE stream at EOF must raise, not return partial.

    Shutting the socket down makes the stream iterator stop cleanly (EOF) rather
    than raise, so ``_parse_sse_response`` must re-check the token after the loop
    instead of building a partial successful result from the events seen so far.
    """

    from pipy_harness.native.openai_codex_provider import _parse_sse_response

    token = CancelToken()

    def _stream() -> Any:
        # One delta arrives while the turn is live, then the stream ends at the
        # same moment the user cancels (the socket-shutdown EOF equivalent).
        yield {"type": "response.output_text.delta", "delta": "partial answer"}
        token.cancel()

    with pytest.raises(ProviderCancelledError):
        _parse_sse_response("", event_stream=_stream(), cancel_token=token)


def test_anthropic_provider_forwards_cancel_token_to_http_boundary() -> None:
    client = _BlockingCancelHTTPClient()
    provider = AnthropicProvider(
        model_id="claude-test",
        api_key="test-key",
        http_client=client,
    )
    token = CancelToken()
    outcome: list[str] = []
    finished = threading.Event()

    def _worker() -> None:
        try:
            provider.complete(
                ProviderRequest(
                    system_prompt="",
                    user_prompt="hello",
                    provider_name="anthropic",
                    model_id="claude-test",
                    cwd=Path("."),
                ),
                cancel_token=token,
            )
            outcome.append("completed")
        except ProviderCancelledError:
            outcome.append("cancelled")
        finally:
            finished.set()

    worker = threading.Thread(target=_worker)
    worker.start()
    assert client.entered.wait(timeout=5)
    # Provider is blocked at the HTTP boundary holding the cancel token.
    assert client.received_token is token
    token.cancel()
    assert finished.wait(timeout=5)
    worker.join(timeout=5)

    assert outcome == ["cancelled"]


def test_already_cancelled_token_short_circuits_provider() -> None:
    client = _BlockingCancelHTTPClient()
    provider = AnthropicProvider(
        model_id="claude-test",
        api_key="test-key",
        http_client=client,
    )
    token = CancelToken()
    token.cancel()

    with pytest.raises(ProviderCancelledError):
        provider.complete(
            ProviderRequest(
                system_prompt="",
                user_prompt="hello",
                provider_name="anthropic",
                model_id="claude-test",
                cwd=Path("."),
            ),
            cancel_token=token,
        )

    # The provider must bail out before ever reaching the HTTP boundary.
    assert client.entered.is_set() is False

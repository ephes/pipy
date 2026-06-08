"""Tests for the native active-turn cancellation token."""

from __future__ import annotations

import threading

import pytest

from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError


class _RecordingCloseable:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def test_fresh_token_is_not_cancelled() -> None:
    token = CancelToken()
    assert token.cancelled is False
    # raise_if_cancelled is a no-op while the token is live.
    token.raise_if_cancelled()


def test_cancel_sets_event_and_closes_registered_closeables() -> None:
    token = CancelToken()
    closeable = _RecordingCloseable()
    token.register(closeable)

    token.cancel()

    assert token.cancelled is True
    assert closeable.closed == 1
    with pytest.raises(ProviderCancelledError):
        token.raise_if_cancelled()


def test_register_after_cancel_closes_immediately_and_raises() -> None:
    token = CancelToken()
    token.cancel()
    closeable = _RecordingCloseable()

    with pytest.raises(ProviderCancelledError):
        token.register(closeable)

    # The worker never gets to read from a connection it could not register.
    assert closeable.closed == 1


def test_unregister_prevents_double_close() -> None:
    token = CancelToken()
    closeable = _RecordingCloseable()
    token.register(closeable)
    token.unregister(closeable)

    token.cancel()

    assert closeable.closed == 0


def test_cancel_is_idempotent() -> None:
    token = CancelToken()
    closeable = _RecordingCloseable()
    token.register(closeable)

    token.cancel()
    token.cancel()

    # The closeable is closed exactly once even across repeated cancel() calls.
    assert closeable.closed == 1


def test_close_failures_do_not_propagate() -> None:
    class _Boom:
        def close(self) -> None:
            raise OSError("bad fd")

    token = CancelToken()
    token.register(_Boom())
    # cancel() is best-effort: a closeable that raises must not break the
    # cancellation of the remaining closeables or the caller.
    token.cancel()
    assert token.cancelled is True


def test_cancel_closes_a_closeable_blocking_a_worker_thread() -> None:
    """A worker blocked on read() is released when the token cancels its closeable."""

    token = CancelToken()
    read_started = threading.Event()
    observed: list[str] = []

    class _BlockingCloseable:
        def __init__(self) -> None:
            self._released = threading.Event()

        def read(self) -> None:
            read_started.set()
            # Block until cancel() closes us.
            self._released.wait(timeout=2)
            if token.cancelled:
                raise ProviderCancelledError("cancelled mid-read")

        def close(self) -> None:
            self._released.set()

    closeable = _BlockingCloseable()

    def _worker() -> None:
        token.register(closeable)
        try:
            closeable.read()
        except ProviderCancelledError:
            observed.append("cancelled")
        finally:
            token.unregister(closeable)

    worker = threading.Thread(target=_worker)
    worker.start()
    assert read_started.wait(timeout=2)
    token.cancel()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert observed == ["cancelled"]

"""Active-turn cancellation token for the native provider boundary.

The native tool loop runs each provider turn on a daemon worker thread while
the main thread watches stdin for Escape / Ctrl-C. Before this token the abort
path was UI-only: late stream chunks were dropped, but the worker's blocking
HTTP ``read()`` ran to completion. ``CancelToken`` closes the cancellation gap
end to end: the worker registers its in-flight HTTP response on the token, and
the main thread's :meth:`CancelToken.cancel` closes that response so the
blocking read raises :class:`ProviderCancelledError` promptly instead of
finishing the request. The worker is then joined/reaped and can no longer
mutate provider, tool, or context state.
"""

from __future__ import annotations

import threading
from typing import Protocol, runtime_checkable


class ProviderCancelledError(Exception):
    """Raised at the provider / HTTP boundary when the active turn is cancelled.

    The native tool loop discards any partial result for a cancelled turn and
    never appends a misleading successful assistant/tool observation, so this
    exception is consumed (not surfaced as a provider failure) once the abort
    state has been rendered.
    """


@runtime_checkable
class Closeable(Protocol):
    """Anything with a ``close()`` the token can use to interrupt a blocked read."""

    def close(self) -> None:  # pragma: no cover - structural protocol
        ...


class CancelToken:
    """Thread-safe cancellation token threaded into ``provider.complete()``.

    The token wraps a :class:`threading.Event` (so existing event-based stream
    suppression keeps working) plus a registry of in-flight closeables. The
    worker thread registers its live HTTP response/socket; :meth:`cancel`
    closes every registered closeable so a concurrent blocking read raises.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._closeables: list[Closeable] = []

    @property
    def event(self) -> threading.Event:
        """The underlying event, for callers that suppress output on abort."""

        return self._event

    @property
    def cancelled(self) -> bool:
        """Whether :meth:`cancel` has been called."""

        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise :class:`ProviderCancelledError` if the token is cancelled."""

        if self._event.is_set():
            raise ProviderCancelledError("native provider turn cancelled")

    def register(self, closeable: Closeable) -> None:
        """Register an in-flight closeable so :meth:`cancel` can close it.

        If the token is already cancelled the closeable is closed immediately
        and :class:`ProviderCancelledError` is raised, so a worker that loses
        the race against :meth:`cancel` still aborts instead of reading from a
        connection the main thread already gave up on.
        """

        with self._lock:
            if not self._event.is_set():
                self._closeables.append(closeable)
                return
        _safe_close(closeable)
        raise ProviderCancelledError("native provider turn cancelled")

    def unregister(self, closeable: Closeable) -> None:
        """Drop a closeable that finished cleanly before cancellation."""

        with self._lock:
            try:
                self._closeables.remove(closeable)
            except ValueError:
                pass

    def cancel(self) -> None:
        """Cancel the turn: set the event and close all registered closeables.

        Idempotent and best-effort: a closeable whose ``close()`` raises does
        not prevent the remaining closeables from being closed.
        """

        with self._lock:
            self._event.set()
            pending = self._closeables
            self._closeables = []
        for closeable in pending:
            _safe_close(closeable)


def _safe_close(closeable: Closeable) -> None:
    try:
        closeable.close()
    except Exception:  # noqa: BLE001 - best-effort interrupt of a blocked read
        pass

"""A bounded store-local wake, never a task or completed-value projection."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

from .session_core import CommitPublication, Session


class ProductWake:
    """One queued loop callback regardless of publisher volume.

    Construct under the product's owned control operation while dispatch is
    disabled, before enabling or inspecting for a result. A harmless store-local
    wake avoids thawing rich private publications merely to filter a CID.
    """

    def __init__(self, session: Session, event: asyncio.Event) -> None:
        self._loop = asyncio.get_running_loop()
        self._event = event
        self._guard = threading.Lock()
        self._pending = False
        self._closed = False
        self._unsubscribe: Callable[[], None] | None = None
        self._unsubscribe = session.subscribe_commits(self._notify)

    def _notify(self, _: CommitPublication) -> None:
        if self._loop.is_closed():
            self.stop()
            return
        with self._guard:
            schedule = not self._closed and not self._pending
            if schedule:
                self._pending = True
        if schedule:
            try:
                self._loop.call_soon_threadsafe(self._deliver)
            except RuntimeError:
                self.stop()

    def _deliver(self) -> None:
        with self._guard:
            self._pending = False
            deliver = not self._closed
        if deliver:
            self._event.set()

    def stop(self) -> None:
        with self._guard:
            self._closed = True
            unsubscribe = self._unsubscribe
            self._unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()

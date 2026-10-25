"""One acquisition handoff, retaining late resources after waiter abandonment."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable
from typing import TypeVar

from .generation_transport import owned_call
from .session_core import Session
from .storage import Storage

T = TypeVar("T")
# Loop-side acquisition work is strongly retained. On loop teardown the real
# worker still owns its holder/raw resource independently of cancelled wrappers.
_ACQUISITIONS: dict[int, asyncio.Task[Session]] = {}
_ACQUISITION_GUARD = threading.Lock()


class _Acquisition:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.loop = asyncio.get_running_loop()
        self.abandoned = False
        self.session: Session | None = None

    def abandon(self) -> None:
        with self.lock:
            self.abandoned = True
            session = self.session
        if session is not None:
            session.request_close()

    def run(
        self, opener: Callable[[], Storage], setup: Callable[[Session], None]
    ) -> Session:
        raw = opener()
        session: Session | None = None
        try:
            with self.lock:
                abandoned = self.abandoned
            if abandoned or self.loop.is_closed():
                raise asyncio.CancelledError
            session = Session(raw)
            with self.lock:
                self.session = session
                abandoned = self.abandoned
            if abandoned or self.loop.is_closed():
                raise asyncio.CancelledError
            setup(session)
            with self.lock:
                abandoned = self.abandoned
            if abandoned or self.loop.is_closed():
                raise asyncio.CancelledError
            return session
        except BaseException:
            if session is None:
                raw.close()
            else:
                session.close()
            raise


async def acquire(
    opener: Callable[[], Storage], setup: Callable[[Session], None]
) -> Session:
    holder = _Acquisition()
    body = owned_call(lambda: holder.run(opener, setup))
    try:
        work = asyncio.create_task(body)
    except BaseException:
        holder.abandon()
        if inspect.getcoroutinestate(body) == inspect.CORO_CREATED:
            body.close()
        raise
    with _ACQUISITION_GUARD:
        _ACQUISITIONS[id(work)] = work

    def finished(done: asyncio.Task[Session]) -> None:
        with _ACQUISITION_GUARD:
            _ACQUISITIONS.pop(id(done), None)
        if not done.cancelled():
            done.exception()

    work.add_done_callback(finished)
    try:
        session = await asyncio.shield(work)
    except BaseException:
        holder.abandon()
        raise
    # No await between the successful handoff and the caller's facade return.
    with holder.lock:
        holder.session = None
    return session

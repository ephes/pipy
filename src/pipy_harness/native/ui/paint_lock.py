"""The single mutex that serializes painting the terminal frame.

One lock, deliberately reentrant. Painting walks
`paint() -> _paint_locked() -> _frame_snapshot() -> _standard_frame_inputs() ->
_extension_*_lines()`, and that last step runs trusted extension factories which
may themselves request a repaint. A non-reentrant lock deadlocks there, so the
reentrancy is a requirement of the design rather than an accident of it.

It is a named type rather than a bare `threading.RLock` because it is shared by
every terminal owner that renders part of a frame -- chrome, footer, transcript,
the custom editor. As those owners move into their own modules they must each
receive *this* lock; two locks would serialize each owner against itself while
letting two owners paint at once, which is the failure this type exists to make
unrepresentable. A plain `Lock` cannot be passed where a `PaintLock` is wanted,
and there is nothing to construct by accident at a call site.
"""

from __future__ import annotations

from _thread import RLock
from types import TracebackType


class PaintLock:
    """A reentrant lock, nameable in a signature and not confusable with any other."""

    __slots__ = ("_lock",)

    def __init__(self, lock: RLock) -> None:
        if not isinstance(lock, RLock):
            raise TypeError("PaintLock requires an RLock")
        self._lock = lock

    def __enter__(self) -> bool:
        return self._lock.__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._lock.__exit__(exc_type, exc, traceback)

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        return self._lock.acquire(blocking, timeout)

    def release(self) -> None:
        self._lock.release()

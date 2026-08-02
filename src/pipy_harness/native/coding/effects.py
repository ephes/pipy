"""Run-scoped coordination for extension coding-session effects."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

_RLOCK_TYPE = type(threading.RLock())


class CodingEffectCoordinator:
    """Serialize retained coding effects while leaving their work unlocked.

    One exclusive owner lease spans each accepted effect. The coordinator's
    reentrant lock is released while provider, rendering, callback, tree, and
    input work runs; tree/input owners reacquire that same lock only for their
    shared-state phases. Terminal teardown closes admission and waits on the
    condition, releasing the lock while the accepted owner drains.
    """

    __slots__ = ("_condition", "_depth", "_lock", "_owner", "_terminal")

    def __init__(self, lock: threading.RLock | None = None) -> None:
        self._lock = lock if lock is not None else threading.RLock()
        if not isinstance(self._lock, _RLOCK_TYPE):
            raise TypeError("coding-effect lock must be an RLock")
        self._condition = threading.Condition(self._lock)
        self._owner: int | None = None
        self._depth = 0
        self._terminal = False

    @property
    def lock(self) -> threading.RLock:
        """The exact reentrant lock shared by the active tree and input queue."""

        return self._lock

    @property
    def terminal(self) -> bool:
        with self._lock:
            return self._terminal

    @contextmanager
    def effect(self) -> Iterator[bool]:
        """Yield whether this thread claimed the exclusive reentrant lease."""

        owner = threading.get_ident()
        admitted = False
        with self._condition:
            while True:
                if self._terminal and self._owner != owner:
                    break
                if self._owner is None:
                    self._owner = owner
                    self._depth = 1
                    admitted = True
                    break
                if self._owner == owner:
                    self._depth += 1
                    admitted = True
                    break
                self._condition.wait()
        if not admitted:
            yield False
            return
        try:
            yield True
        finally:
            with self._condition:
                if self._owner != owner or self._depth <= 0:
                    raise RuntimeError("coding-effect owner lease is unbalanced")
                self._depth -= 1
                if self._depth == 0:
                    self._owner = None
                    self._condition.notify_all()

    @contextmanager
    def terminal_section(self) -> Iterator[bool]:
        """Close admission, wait for the accepted owner, and retain the lock.

        The yielded boolean is true only for the caller that performs terminal
        detach work. Later calls still wait for quiescence but do not repeat it.
        """

        closer = threading.get_ident()
        with self._condition:
            first = not self._terminal
            self._terminal = True
            self._condition.notify_all()
            if self._owner == closer:
                raise RuntimeError("a coding-effect owner cannot close itself")
            while self._owner is not None:
                self._condition.wait()
            yield first

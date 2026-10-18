"""Stable, non-reentrant locks for cooperating local archive writers."""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Serialize threads/processes using a fresh descriptor and persistent inode."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield

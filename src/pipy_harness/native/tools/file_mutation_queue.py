"""Serialize mutations of one file, following Pi's ``file-mutation-queue.ts``.

Pi's ``write`` and ``edit`` run inside ``withFileMutationQueue(path, fn)``:
operations on the same file run one after another, operations on different
files in parallel. pipy's executor runs one tool call at a time, but a call
cancelled by the operator can leave its worker inside a filesystem operation
after the bounded join, so the next ``write``/``edit`` of that file waits for
it here.

The key is the ``realpath`` of the path, so two spellings (or a symlink) of
one file share a lock; a path that does not exist yet (``ENOENT``/``ENOTDIR``)
keys on itself. Any other ``realpath`` error propagates, as in Pi.
"""

from __future__ import annotations

import errno
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class _Entry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    holders: int = 0


_registry_lock = threading.Lock()
_entries: dict[str, _Entry] = {}


def mutation_queue_key(path: Path) -> str:
    """Pi ``getMutationQueueKey``: realpath, or the path when it is missing."""

    resolved = os.path.abspath(path)
    try:
        return os.path.realpath(resolved, strict=True)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return resolved
        raise


@contextmanager
def file_mutation_queue(key: str) -> Iterator[None]:
    """Hold the mutation lock for ``key`` (:func:`mutation_queue_key`)."""

    with _registry_lock:
        entry = _entries.setdefault(key, _Entry())
        entry.holders += 1
    try:
        with entry.lock:
            yield
    finally:
        with _registry_lock:
            entry.holders -= 1
            if entry.holders == 0 and _entries.get(key) is entry:
                del _entries[key]


__all__ = ["file_mutation_queue", "mutation_queue_key"]

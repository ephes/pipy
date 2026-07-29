"""Observable synchronization helpers for real-PTY tests.

The native terminal driver enters raw mode with ``TCSAFLUSH`` and only then
emits the bracketed-paste enable sequence.  A painted prompt, overlay, notice,
or answer can therefore precede input readiness.  PTY tests use the byte as the
product-observable acknowledgement that input queued before the transition can
no longer be discarded.
"""

from __future__ import annotations

import os
import select
import time
from collections.abc import Sequence
from dataclasses import dataclass

BRACKETED_PASTE_READY = b"\x1b[?2004h"
_POLL_INTERVAL = 0.02


@dataclass(frozen=True, slots=True)
class FdOutputObservation:
    """Aggregate fd output plus the exact end offset of a requested match."""

    output: bytes
    match_end: int | None


@dataclass(frozen=True, slots=True)
class FdInputReadyObservation:
    """One fd aggregate with ordered presentation and readiness offsets."""

    output: bytes
    observed_end: int | None
    ready_end: int | None


def output_bytes(chunks: Sequence[bytes]) -> bytes:
    """Return the current immutable view of an append-only output capture."""

    return b"".join(chunks)


def _wait_for_output_until(
    chunks: Sequence[bytes],
    needle: str | bytes,
    *,
    after: int,
    deadline: float,
) -> int | None:
    encoded = needle.encode("utf-8") if isinstance(needle, str) else needle
    while True:
        found = output_bytes(chunks).find(encoded, after)
        if found >= 0:
            return found + len(encoded)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(_POLL_INTERVAL, remaining))


def wait_for_output(
    chunks: Sequence[bytes],
    needle: str | bytes,
    *,
    after: int = 0,
    timeout: float = 8.0,
) -> int | None:
    """Wait for ``needle`` at or after ``after`` and return its end offset."""

    return _wait_for_output_until(
        chunks,
        needle,
        after=after,
        deadline=time.monotonic() + max(timeout, 0.0),
    )


def wait_for_output_after(
    chunks: Sequence[bytes],
    observed: str | bytes,
    expected: str | bytes,
    *,
    after: int = 0,
    timeout: float = 8.0,
) -> int | None:
    """Observe two byte sequences in order within one timeout budget."""

    deadline = time.monotonic() + max(timeout, 0.0)
    observed_end = _wait_for_output_until(
        chunks,
        observed,
        after=after,
        deadline=deadline,
    )
    if observed_end is None:
        return None
    return _wait_for_output_until(
        chunks,
        expected,
        after=observed_end,
        deadline=deadline,
    )


def _wait_for_fd_output_until(
    fd: int,
    output: bytearray,
    needle: bytes,
    *,
    after: int,
    deadline: float,
) -> int | None:
    while True:
        found = output.find(needle, after)
        if found >= 0:
            return found + len(needle)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        readable, _, _ = select.select([fd], [], [], min(0.1, remaining))
        if not readable:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            return None
        if not chunk:
            return None
        output.extend(chunk)


def wait_for_fd_output(
    fd: int,
    needle: bytes,
    *,
    initial: bytes = b"",
    after: int = 0,
    timeout: float = 8.0,
) -> FdOutputObservation:
    """Search an fd aggregate at ``after``, including already captured bytes."""

    output = bytearray(initial)
    match_end = _wait_for_fd_output_until(
        fd,
        output,
        needle,
        after=after,
        deadline=time.monotonic() + max(timeout, 0.0),
    )
    return FdOutputObservation(bytes(output), match_end)


def wait_for_fd_input_ready_after(
    fd: int,
    observed: bytes,
    *,
    initial: bytes = b"",
    after: int = 0,
    timeout: float = 8.0,
) -> FdInputReadyObservation:
    """Observe fd output then post-``TCSAFLUSH`` readiness under one deadline.

    Both phases search one preserved aggregate, and the readiness search starts
    at the exact end of ``observed`` so a stale acknowledgement cannot match.
    """

    output = bytearray(initial)
    deadline = time.monotonic() + max(timeout, 0.0)
    observed_end = _wait_for_fd_output_until(
        fd,
        output,
        observed,
        after=after,
        deadline=deadline,
    )
    if observed_end is None:
        return FdInputReadyObservation(bytes(output), None, None)
    ready_end = _wait_for_fd_output_until(
        fd,
        output,
        BRACKETED_PASTE_READY,
        after=observed_end,
        deadline=deadline,
    )
    return FdInputReadyObservation(bytes(output), observed_end, ready_end)


def input_ready_count(chunks: Sequence[bytes]) -> int:
    """Count completed raw-input acknowledgements in the captured stream."""

    return output_bytes(chunks).count(BRACKETED_PASTE_READY)


def wait_for_input_ready_count(
    chunks: Sequence[bytes], expected: int, *, timeout: float = 8.0
) -> bool:
    """Wait until at least ``expected`` raw-input transitions are observable."""

    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        if input_ready_count(chunks) >= expected:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(_POLL_INTERVAL, remaining))


def wait_for_input_ready(
    chunks: Sequence[bytes], *, after: int = 0, timeout: float = 8.0
) -> int | None:
    """Wait for a post-``TCSAFLUSH`` raw-input acknowledgement byte sequence."""

    return wait_for_output(
        chunks,
        BRACKETED_PASTE_READY,
        after=after,
        timeout=timeout,
    )


def wait_for_input_ready_after(
    chunks: Sequence[bytes],
    observed: str | bytes,
    *,
    after: int = 0,
    timeout: float = 8.0,
) -> int | None:
    """Observe output and later input readiness within one timeout budget."""

    return wait_for_output_after(
        chunks,
        observed,
        BRACKETED_PASTE_READY,
        after=after,
        timeout=timeout,
    )

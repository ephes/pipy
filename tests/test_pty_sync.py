from __future__ import annotations

import os

import pytest

import pty_sync
from pty_sync import (
    BRACKETED_PASTE_READY,
    input_ready_count,
    wait_for_input_ready,
    wait_for_fd_input_ready_after,
    wait_for_fd_output,
    wait_for_input_ready_after,
    wait_for_input_ready_count,
    wait_for_output_after,
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.on_sleep = lambda: None

    def monotonic(self) -> float:
        return self.now

    def sleep(self, duration: float) -> None:
        self.now += duration
        self.on_sleep()


def test_input_readiness_ignores_stale_marker_before_observed_output() -> None:
    chunks = [BRACKETED_PASTE_READY, b"overlay painted"]

    assert wait_for_input_ready_after(chunks, b"overlay painted", timeout=0.02) is None


def test_input_readiness_accepts_marker_split_across_capture_chunks() -> None:
    chunks = [b"notice painted", b"\x1b[?20", b"04h"]

    notice_end = len(b"notice painted")
    assert wait_for_input_ready(chunks, after=notice_end, timeout=0.02) is not None
    assert wait_for_input_ready_after(chunks, b"notice painted", timeout=0.02)
    assert input_ready_count(chunks) == 1
    assert wait_for_input_ready_count(chunks, 1, timeout=0.02)


def test_output_and_readiness_share_one_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    chunks: list[bytes] = []

    def publish_notice() -> None:
        if clock.now >= 0.04 and not chunks:
            chunks.append(b"notice painted")

    clock.on_sleep = publish_notice
    monkeypatch.setattr(pty_sync.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(pty_sync.time, "sleep", clock.sleep)

    assert wait_for_input_ready_after(chunks, b"notice painted", timeout=0.05) is None
    assert clock.now == pytest.approx(0.05)


def test_zero_budget_checks_capture_once_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    monkeypatch.setattr(pty_sync.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(pty_sync.time, "sleep", clock.sleep)

    assert (
        wait_for_input_ready_after([b"notice painted"], b"notice painted", timeout=0)
        is None
    )
    assert clock.now == 0.0


def test_count_wait_non_positive_budget_checks_capture_without_sleeping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    monkeypatch.setattr(pty_sync.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(pty_sync.time, "sleep", clock.sleep)

    assert wait_for_input_ready_count([BRACKETED_PASTE_READY], 1, timeout=0)
    assert wait_for_input_ready_count([BRACKETED_PASTE_READY], 1, timeout=-1)
    assert not wait_for_input_ready_count([], 1, timeout=0)
    assert not wait_for_input_ready_count([], 1, timeout=-1)
    assert clock.now == 0.0


def test_count_wait_sleeps_only_for_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    monkeypatch.setattr(pty_sync.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(pty_sync.time, "sleep", clock.sleep)

    assert not wait_for_input_ready_count([], 1, timeout=0.025)
    assert clock.now == pytest.approx(0.025)


def test_shared_deadline_accepts_later_split_chunk_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    chunks: list[bytes] = []

    def publish_phases() -> None:
        if clock.now >= 0.02 and not chunks:
            chunks.append(b"notice painted")
        if clock.now >= 0.04 and len(chunks) == 1:
            chunks.extend([b"\x1b[?20", b"04h"])

    clock.on_sleep = publish_phases
    monkeypatch.setattr(pty_sync.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(pty_sync.time, "sleep", clock.sleep)

    assert wait_for_input_ready_after(chunks, b"notice painted", timeout=0.05)
    assert clock.now == pytest.approx(0.04)


@pytest.mark.parametrize("timeout", [0.0, -1.0])
def test_fd_output_and_readiness_accept_coalesced_bytes_at_nonpositive_budget(
    timeout: float,
) -> None:
    read_fd, write_fd = os.pipe()
    try:
        title = b"Trust project folder?"
        aggregate = title + BRACKETED_PASTE_READY
        observed = wait_for_fd_input_ready_after(
            read_fd,
            title,
            initial=aggregate,
            timeout=timeout,
        )
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert observed.output == aggregate
    assert observed.observed_end == len(title)
    assert observed.ready_end == len(aggregate)


def test_fd_output_and_readiness_share_one_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    fd = 41
    reads = [b"notice painted"]

    def fake_select(
        readable: list[int],
        _writable: list[int],
        _exceptional: list[int],
        timeout: float,
    ) -> tuple[list[int], list[int], list[int]]:
        if reads:
            clock.now += min(0.04, timeout)
            return readable, [], []
        clock.now += timeout
        return [], [], []

    def fake_read(_fd: int, _size: int) -> bytes:
        return reads.pop(0)

    monkeypatch.setattr(pty_sync.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(pty_sync.select, "select", fake_select)
    monkeypatch.setattr(pty_sync.os, "read", fake_read)

    observed = wait_for_fd_input_ready_after(
        fd,
        b"notice painted",
        timeout=0.05,
    )

    assert observed.output == b"notice painted"
    assert observed.observed_end == len(b"notice painted")
    assert observed.ready_end is None
    assert clock.now == pytest.approx(0.05)


def test_fd_output_and_readiness_preserve_split_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fd = 42
    reads = [b"Trust project ", b"folder?\x1b[?20", b"04h"]

    def fake_select(
        readable: list[int],
        _writable: list[int],
        _exceptional: list[int],
        _timeout: float,
    ) -> tuple[list[int], list[int], list[int]]:
        return readable, [], []

    def fake_read(_fd: int, _size: int) -> bytes:
        return reads.pop(0)

    monkeypatch.setattr(pty_sync.select, "select", fake_select)
    monkeypatch.setattr(pty_sync.os, "read", fake_read)

    title = b"Trust project folder?"
    aggregate = title + BRACKETED_PASTE_READY
    observed = wait_for_fd_input_ready_after(fd, title, timeout=0.05)

    assert observed.output == aggregate
    assert observed.observed_end == len(title)
    assert observed.ready_end == len(aggregate)


def test_fd_output_and_readiness_reject_stale_pre_title_marker() -> None:
    read_fd, write_fd = os.pipe()
    try:
        initial = BRACKETED_PASTE_READY + b"warning painted"
        observed = wait_for_fd_input_ready_after(
            read_fd,
            b"warning painted",
            initial=initial,
            after=len(BRACKETED_PASTE_READY),
            timeout=0,
        )
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert observed.output == initial
    assert observed.observed_end == len(initial)
    assert observed.ready_end is None


def test_ordered_output_accepts_split_fresh_sentinel_and_rejects_stale_one() -> None:
    sentinel = b"RESIZE_FRAME_COMPLETE"
    stale = sentinel + b"old frame"
    chunks = [stale, b"\x1b[", b"2Jnew frame RESIZE_", b"FRAME_COMPLETE"]

    assert wait_for_output_after(
        chunks,
        b"\x1b[2J",
        sentinel,
        after=len(stale),
        timeout=0,
    ) == len(b"".join(chunks))


def test_fd_wait_rejects_stale_clear_before_requested_offset() -> None:
    read_fd, write_fd = os.pipe()
    try:
        stale = b"\x1b[2Jold frame"
        observed = wait_for_fd_output(
            read_fd,
            b"\x1b[2J",
            initial=stale,
            after=len(stale),
            timeout=0,
        )
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert observed.output == stale
    assert observed.match_end is None

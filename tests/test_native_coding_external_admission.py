"""Guarded, initially unadopted external admission queue contract."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace

import pytest

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.runtime_ports import AgentQueuedInputKind
from pipy_harness.native.cancellation import _AcceptedAbortSignal
from pipy_harness.native.coding.input_queue import (
    CodingInputQueue,
    CodingInputSource,
    _ExternalAdmissionSnapshot,
    _ExternalReservation,
    _ExternalReservationToken,
)


def _reservation(
    queue: CodingInputQueue,
    value: str,
    kind: AgentQueuedInputKind | None = None,
    *,
    steer_active: bool = False,
) -> _ExternalReservation:
    snapshot = queue._admit_external(
        ProductContent(value), kind, steer_active=steer_active
    )
    assert snapshot.reservation is not None
    return snapshot.reservation


def _join(thread: threading.Thread) -> None:
    thread.join(2)
    assert not thread.is_alive(), f"{thread.name} did not finish"


def test_simultaneous_admission_reserves_one_and_queues_one() -> None:
    queue = CodingInputQueue(mutation_lock=threading.RLock())
    barrier = threading.Barrier(3)
    values = (ProductContent("first"), ProductContent("second"))
    failures: list[BaseException] = []

    def admit(content: ProductContent) -> None:
        try:
            barrier.wait(2)
            queue._admit_external(content)
        except Exception as exc:  # noqa: BLE001 - asserted after bounded joins
            failures.append(exc)

    threads = [threading.Thread(target=admit, args=(value,)) for value in values]
    for thread in threads:
        thread.start()
    try:
        barrier.wait(2)
    finally:
        for thread in threads:
            _join(thread)
    assert failures == []

    snapshot = queue._external_admission_snapshot()
    assert snapshot.reservation is not None
    assert snapshot.reservation.content in values
    assert snapshot.follow_ups == tuple(
        value for value in values if value is not snapshot.reservation.content
    )
    assert snapshot.steering == ()
    assert snapshot.pending_count == 1


def test_claim_and_settle_require_exact_current_token_once() -> None:
    first = CodingInputQueue()
    second = CodingInputQueue()
    reservation = _reservation(first, "active")
    foreign = _reservation(second, "foreign").token
    stale_shape = _ExternalReservationToken()
    copied = replace(reservation.token)
    before = first._external_admission_snapshot()

    assert first._settle_external(reservation.token) is None
    assert first._claim_external(foreign) is None
    assert first._claim_external(stale_shape) is None
    assert first._claim_external(copied) is None
    assert first._external_admission_snapshot() == before

    claim = first._claim_external(reservation.token)
    assert claim is not None
    assert claim.content is reservation.content
    assert claim.kind is None
    assert first._claim_external(reservation.token) is None
    settled = first._settle_external(reservation.token)
    assert settled is not None and settled.reservation is None
    assert first._settle_external(reservation.token) is None
    assert first._claim_external(reservation.token) is None


def test_atomic_begin_refuses_busy_slot_or_pending_lanes_without_mutation() -> None:
    queue = CodingInputQueue()
    active = queue._begin_external_operation(ProductContent("active"))
    assert active is not None
    before_active = queue._external_admission_snapshot()

    assert queue._begin_external_operation(ProductContent("refused")) is None
    assert queue._external_admission_snapshot() == before_active

    assert queue._settle_external(active.token) is not None
    queue._admit_external(ProductContent("reserved"))
    queue._admit_external(ProductContent("pending"))
    reserved = queue._external_admission_snapshot().reservation
    assert reserved is not None
    assert queue._claim_external(reserved.token) is not None
    assert queue._settle_external(reserved.token) is not None
    before_pending = queue._external_admission_snapshot()
    assert before_pending.reservation is not None

    assert queue._begin_external_operation(ProductContent("also refused")) is None
    assert queue._external_admission_snapshot() == before_pending


def test_simultaneous_claim_of_same_token_succeeds_exactly_once() -> None:
    queue = CodingInputQueue()
    reservation = _reservation(queue, "active")
    barrier = threading.Barrier(3)
    claims: list[object] = []
    failures: list[BaseException] = []

    def claim() -> None:
        try:
            barrier.wait(2)
            claims.append(queue._claim_external(reservation.token))
        except Exception as exc:  # noqa: BLE001 - asserted after bounded joins
            failures.append(exc)

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    try:
        barrier.wait(2)
    finally:
        for thread in threads:
            _join(thread)

    assert failures == []
    assert sum(item is not None for item in claims) == 1
    current = queue._external_admission_snapshot().reservation
    assert current is not None and current.claimed


def test_settlement_promotes_steering_first_and_fifo_with_truthful_pending() -> None:
    queue = CodingInputQueue()
    active = _reservation(queue, "ordinary")
    follow_one = ProductContent("follow one")
    steer_one = ProductContent("steer one")
    follow_two = ProductContent("follow two")
    steer_two = ProductContent("steer two")
    queue._admit_external(follow_one)
    queue._admit_external(steer_one, steer_active=True)
    queue._admit_external(follow_two, AgentQueuedInputKind.FOLLOW_UP)
    queued = queue._admit_external(steer_two, AgentQueuedInputKind.STEERING)

    assert queued.pending_count == 4
    assert queued.steering == (steer_one, steer_two)
    assert queued.follow_ups == (follow_one, follow_two)
    assert queue._claim_external(active.token) is not None

    expected = [
        (steer_one, AgentQueuedInputKind.STEERING, 3),
        (steer_two, AgentQueuedInputKind.STEERING, 2),
        (follow_one, AgentQueuedInputKind.FOLLOW_UP, 1),
        (follow_two, AgentQueuedInputKind.FOLLOW_UP, 0),
    ]
    token = active.token
    for content, kind, pending in expected:
        snapshot = queue._settle_external(token)
        assert snapshot is not None and snapshot.reservation is not None
        assert snapshot.reservation.content is content
        assert snapshot.reservation.kind is kind
        assert snapshot.pending_count == pending
        token = snapshot.reservation.token
        assert queue._claim_external(token) is not None
    final = queue._settle_external(token)
    assert final is not None and final.reservation is None


@pytest.mark.parametrize("settle_first", [False, True])
def test_admission_racing_settlement_has_no_loss_or_false_idle(
    settle_first: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = CodingInputQueue()
    active = _reservation(queue, "active")
    assert queue._claim_external(active.token) is not None
    paused = threading.Event()
    release = threading.Event()
    original = CodingInputQueue._external_snapshot
    first_operation = "settle" if settle_first else "admit"
    next_content = ProductContent("next")

    def snapshot(self: CodingInputQueue):
        if threading.current_thread().name == first_operation:
            paused.set()
            assert release.wait(2)
        return original(self)

    monkeypatch.setattr(CodingInputQueue, "_external_snapshot", snapshot)
    results: dict[str, object] = {}
    failures: list[BaseException] = []

    def run(name: str, action: Callable[[], object]) -> None:
        try:
            results[name] = action()
        except Exception as exc:  # noqa: BLE001 - asserted after bounded joins
            failures.append(exc)

    admission = threading.Thread(
        name="admit",
        target=run,
        args=("admit", lambda: queue._admit_external(next_content)),
    )
    settlement = threading.Thread(
        name="settle",
        target=run,
        args=("settle", lambda: queue._settle_external(active.token)),
    )
    first, second = (settlement, admission) if settle_first else (admission, settlement)
    first.start()
    second_started = False
    try:
        assert paused.wait(2)
        second.start()
        second_started = True
    finally:
        release.set()
        _join(first)
        if second_started:
            _join(second)

    assert failures == []
    assert set(results) == {"admit", "settle"}
    admission_result = results["admit"]
    settlement_result = results["settle"]
    assert isinstance(admission_result, _ExternalAdmissionSnapshot)
    assert isinstance(settlement_result, _ExternalAdmissionSnapshot)
    if settle_first:
        assert settlement_result.reservation is None
        assert settlement_result.pending_count == 0
        assert admission_result.reservation is not None
        next_token = admission_result.reservation.token
        assert next_token is not active.token
        assert admission_result.reservation.content is next_content
        assert admission_result.reservation.kind is None
        assert not admission_result.reservation.claimed
        assert admission_result.pending_count == 0
    else:
        assert admission_result.reservation is not None
        assert admission_result.reservation.token is active.token
        assert admission_result.reservation.content is active.content
        assert admission_result.reservation.claimed
        assert admission_result.follow_ups == (next_content,)
        assert admission_result.pending_count == 1
        assert settlement_result.reservation is not None
        next_token = settlement_result.reservation.token
        assert next_token is not active.token
        assert settlement_result.reservation.content is next_content
        assert settlement_result.reservation.kind is AgentQueuedInputKind.FOLLOW_UP
        assert not settlement_result.reservation.claimed
        assert settlement_result.pending_count == 0
    final = queue._external_admission_snapshot()
    assert final.reservation is not None
    assert final.reservation.token is next_token
    assert final.reservation.content is next_content
    assert not final.reservation.claimed
    assert final.pending_count == 0


def test_abort_captures_old_latch_before_promotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = CodingInputQueue()
    old = _reservation(queue, "old")
    old_claim = queue._claim_external(old.token)
    assert old_claim is not None
    queue._admit_external(ProductContent("next"))
    captured = threading.Event()
    release = threading.Event()
    original_set = _AcceptedAbortSignal.set

    def delayed_set(signal: object) -> None:
        captured.set()
        assert release.wait(2)
        original_set(signal)  # type: ignore[arg-type]

    monkeypatch.setattr(_AcceptedAbortSignal, "set", delayed_set)
    failures: list[BaseException] = []

    def abort() -> None:
        try:
            queue._abort_external()
        except Exception as exc:  # noqa: BLE001 - asserted after bounded join
            failures.append(exc)

    aborter = threading.Thread(target=abort, name="abort")
    aborter.start()
    next_claim = None
    try:
        assert captured.wait(2)
        promoted = queue._settle_external(old.token)
        assert promoted is not None and promoted.reservation is not None
        next_claim = queue._claim_external(promoted.reservation.token)
        assert next_claim is not None
    finally:
        release.set()
        _join(aborter)

    assert failures == []
    assert old_claim.abort_signal.is_set()
    assert next_claim is not None
    assert not next_claim.abort_signal.is_set()


def test_abort_before_claim_is_replayed_and_reservation_remains_accepted() -> None:
    queue = CodingInputQueue()
    reserved = _reservation(queue, "accepted")
    snapshot = queue._abort_external()

    assert snapshot.reservation == reserved
    claim = queue._claim_external(reserved.token)
    assert claim is not None and claim.abort_signal.is_set()
    assert queue._settle_external(reserved.token) is not None


def test_abort_callback_can_read_queue_from_second_thread_without_deadlock() -> None:
    queue = CodingInputQueue(mutation_lock=threading.RLock())
    reserved = _reservation(queue, "active")
    claim = queue._claim_external(reserved.token)
    assert claim is not None
    callback_finished = threading.Event()

    def callback() -> None:
        def read_queue() -> None:
            queue._external_admission_snapshot()
            callback_finished.set()

        reader = threading.Thread(target=read_queue)
        reader.start()
        _join(reader)

    unregister = claim.abort_signal.register_cancel_callback(callback)
    try:
        queue._abort_external()
    finally:
        unregister()
    assert callback_finished.is_set()


def test_idle_abort_does_not_poison_fresh_next_reservation() -> None:
    queue = CodingInputQueue()
    idle = queue._abort_external()
    assert idle.reservation is None

    reserved = _reservation(queue, "next")
    claim = queue._claim_external(reserved.token)
    assert claim is not None
    assert not claim.abort_signal.is_set()


def test_abort_discards_pending_steering_but_preserves_follow_up() -> None:
    queue = CodingInputQueue()
    active = _reservation(queue, "active")
    queue._admit_external(ProductContent("steer"), steer_active=True)
    follow = ProductContent("follow")
    queue._admit_external(follow)

    snapshot = queue._abort_external()
    assert snapshot.steering == ()
    assert snapshot.follow_ups == (follow,)
    assert snapshot.pending_count == 1
    assert queue._claim_external(active.token) is not None
    promoted = queue._settle_external(active.token)
    assert promoted is not None and promoted.reservation is not None
    assert promoted.reservation.content is follow


def test_existing_selectors_and_extension_clear_do_not_consume_managed_work() -> None:
    queue = CodingInputQueue(seeds=(ProductContent("seed"),))
    reserved = _reservation(queue, "managed")
    queue._admit_external(ProductContent("pending"), steer_active=True)
    queue.enqueue_extension_prompt(ProductContent("extension"))

    queue.clear_extension_inputs()
    selected = queue.take_next()

    assert selected is not None
    assert selected.source is CodingInputSource.POSITIONAL_SEED
    assert queue.agent_loop_port.take_next() is None
    snapshot = queue._external_admission_snapshot()
    assert snapshot.reservation == reserved
    assert [item.value for item in snapshot.steering] == ["pending"]


@pytest.mark.parametrize(
    ("value", "kind", "steer_active"),
    [
        ("/tree select 1\n", None, False),
        ("!printf hello\n", None, True),
        ("  exact whitespace  ", AgentQueuedInputKind.STEERING, False),
        ("line one\nline two\n\n", AgentQueuedInputKind.FOLLOW_UP, False),
    ],
)
def test_content_and_ordinary_or_classified_kind_are_preserved_exactly(
    value: str,
    kind: AgentQueuedInputKind | None,
    steer_active: bool,
) -> None:
    queue = CodingInputQueue()
    content = ProductContent(value)
    snapshot = queue._admit_external(content, kind, steer_active=steer_active)

    assert snapshot.reservation is not None
    assert snapshot.reservation.content is content
    assert snapshot.reservation.content.value == value
    assert snapshot.reservation.kind is kind


def test_invalid_admission_is_rejected_before_mutation() -> None:
    queue = CodingInputQueue()
    before = queue._external_admission_snapshot()

    with pytest.raises(TypeError, match="exact ProductContent"):
        queue._admit_external("text")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact AgentQueuedInputKind"):
        queue._admit_external(ProductContent("text"), "steering")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="ordinary"):
        queue._admit_external(
            ProductContent("text"), AgentQueuedInputKind.STEERING, steer_active=True
        )
    assert queue._external_admission_snapshot() == before

"""Focused contracts for the headless coding-session loop controller.

These tests pin the two outer transitions owned by
:class:`~pipy_harness.native.coding.session_controller.CodingSessionController`:
input selection (exact product priority, drain-before-poll, external-wake
overlay, EOF/Ctrl-C classification) and the once-only true-idle
``agent_settled`` boundary with its re-poll. They drive the real
:class:`~pipy_harness.native.coding.input_queue.CodingInputQueue` behind fake
injected ports so the controller's contract is exercised without the monolith.
"""

from __future__ import annotations

import pytest

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentQueuedInputKind,
)
from pipy_harness.native.agent.usage import AgentUsageAccumulator
from pipy_harness.native.coding.input_queue import CodingInputQueue
from pipy_harness.native.coding.session_controller import (
    CodingLoopStep,
    CodingLoopStepKind,
    CodingSessionController,
)
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.cancellation import CancelToken


class _FakeProvider:
    @property
    def name(self) -> str:
        return "fake"

    @property
    def model_id(self) -> str:
        return "fake-model"

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:  # pragma: no cover - never invoked by the controller
        del request, stream_sink, reasoning_sink, cancel_token
        raise AssertionError("the provider port is never invoked by the controller")


class _RecordingEmitter:
    def __init__(self) -> None:
        self.settled_calls = 0
        self.on_settled: list[object] = []

    def agent_settled(self) -> None:
        self.settled_calls += 1
        for callback in self.on_settled:
            callback()  # type: ignore[operator]


class _StaticPort:
    """A registered external input-stream source returning a fixed value once."""

    def __init__(self, value: AgentQueuedInput | None) -> None:
        self._value = value
        self.calls = 0

    def take_next(self) -> AgentQueuedInput | None:
        self.calls += 1
        value = self._value
        self._value = None
        return value


def _coding_state() -> CodingSessionState:
    return CodingSessionState(
        provider=_FakeProvider(),
        provider_name="fake",
        model_id="fake-model",
        usage_accumulator=AgentUsageAccumulator(),
        messages=(),
    )


def _controller(
    queue: CodingInputQueue,
    emitter: _RecordingEmitter | None = None,
) -> tuple[CodingSessionController, _RecordingEmitter]:
    resolved = emitter or _RecordingEmitter()
    controller = CodingSessionController(
        input_queue=queue,
        coding_state=_coding_state(),
        emitter=resolved,
    )
    return controller, resolved


def _no_read() -> str:  # pragma: no cover - asserted not to be called
    raise AssertionError("read_fresh_line must not be called")


def _noop_drain() -> None:
    return None


# --- step selection per source ------------------------------------------------


def test_local_command_selection_yields_local_command_step() -> None:
    queue = CodingInputQueue()
    queue.defer_local_command(ProductContent("/exit"))
    controller, emitter = _controller(queue)

    step = controller.select_next_step(
        settle_pending=False,
        drain_outbox=_noop_drain,
        read_fresh_line=_no_read,
        input_queued_input_port=None,
    )

    assert step.kind is CodingLoopStepKind.LOCAL_COMMAND
    assert step.line == "/exit\n"
    assert step.settle_pending is False
    assert step.selected_provider_content is None
    assert step.queued_input is None
    assert emitter.settled_calls == 0


def test_positional_seed_yields_provider_content_without_queued_input() -> None:
    queue = CodingInputQueue(seeds=(ProductContent("seeded prompt"),))
    controller, _ = _controller(queue)

    step = controller.select_next_step(
        settle_pending=False,
        drain_outbox=_noop_drain,
        read_fresh_line=_no_read,
        input_queued_input_port=None,
    )

    assert step.kind is CodingLoopStepKind.PROVIDER_CONTENT
    assert step.line == "seeded prompt\n"
    assert step.selected_provider_content == ProductContent("seeded prompt")
    assert step.queued_input is None


def test_extension_steering_yields_provider_content_with_queued_input() -> None:
    queue = CodingInputQueue()
    queue.enqueue_extension_steering(ProductContent("steer me"))
    controller, _ = _controller(queue)

    step = controller.select_next_step(
        settle_pending=False,
        drain_outbox=_noop_drain,
        read_fresh_line=_no_read,
        input_queued_input_port=None,
    )

    assert step.kind is CodingLoopStepKind.PROVIDER_CONTENT
    assert step.line == "steer me\n"
    assert step.selected_provider_content == ProductContent("steer me")
    assert step.queued_input is not None
    assert step.queued_input.kind is AgentQueuedInputKind.STEERING
    assert step.queued_input.content == ProductContent("steer me")


def test_retained_fresh_line_yields_retained_fresh_step_without_reframing() -> None:
    # Drive the queue into its retained-fresh state through the documented
    # classify_external_wake path: a local command accepted during a blocking
    # read keeps priority while the just-read fresh line is retained verbatim.
    port = _StaticPort(
        AgentQueuedInput(ProductContent("queued"), AgentQueuedInputKind.STEERING)
    )
    pending_commands = iter([ProductContent("/status")])

    queue = CodingInputQueue(
        external_inputs=(port,),
        pending_local_command_source=lambda: next(pending_commands, None),
    )
    first = queue.classify_external_wake(port, "raw typed line\n")
    assert first is not None and first.content == ProductContent("/status")

    controller, _ = _controller(queue)
    step = controller.select_next_step(
        settle_pending=False,
        drain_outbox=_noop_drain,
        read_fresh_line=_no_read,
        input_queued_input_port=None,
    )

    assert step.kind is CodingLoopStepKind.RETAINED_FRESH
    assert step.line == "raw typed line\n"
    assert step.selected_provider_content is None
    assert step.queued_input is None


def test_fresh_line_read_when_queue_empty() -> None:
    queue = CodingInputQueue()
    controller, emitter = _controller(queue)

    step = controller.select_next_step(
        settle_pending=False,
        drain_outbox=_noop_drain,
        read_fresh_line=lambda: "hello there\n",
        input_queued_input_port=None,
    )

    assert step.kind is CodingLoopStepKind.FRESH_LINE
    assert step.line == "hello there\n"
    assert step.selected_provider_content is None
    assert emitter.settled_calls == 0


# --- external-wake overlay ----------------------------------------------------


def test_external_wake_matching_line_yields_provider_content() -> None:
    port = _StaticPort(
        AgentQueuedInput(ProductContent("queued prompt"), AgentQueuedInputKind.FOLLOW_UP)
    )
    queue = CodingInputQueue(external_inputs=(port,))
    controller, _ = _controller(queue)

    step = controller.select_next_step(
        settle_pending=False,
        drain_outbox=_noop_drain,
        read_fresh_line=lambda: "queued prompt\n",
        input_queued_input_port=port,
    )

    assert step.kind is CodingLoopStepKind.PROVIDER_CONTENT
    assert step.line == "queued prompt\n"
    assert step.selected_provider_content == ProductContent("queued prompt")
    assert step.queued_input is not None
    assert step.queued_input.kind is AgentQueuedInputKind.FOLLOW_UP


def test_external_wake_ordinary_line_falls_through_to_fresh_line() -> None:
    port = _StaticPort(None)
    queue = CodingInputQueue(external_inputs=(port,))
    controller, _ = _controller(queue)

    step = controller.select_next_step(
        settle_pending=False,
        drain_outbox=_noop_drain,
        read_fresh_line=lambda: "ordinary typed\n",
        input_queued_input_port=port,
    )

    assert step.kind is CodingLoopStepKind.FRESH_LINE
    assert step.line == "ordinary typed\n"
    # The port is polled once by the top-of-loop take_next (external priority)
    # and once by the classify_external_wake overlay after the fresh read.
    assert port.calls == 2


def test_external_wake_selects_queued_input_on_eof_read() -> None:
    port = _StaticPort(
        AgentQueuedInput(ProductContent("late queued"), AgentQueuedInputKind.STEERING)
    )
    queue = CodingInputQueue(external_inputs=(port,))
    controller, _ = _controller(queue)

    step = controller.select_next_step(
        settle_pending=False,
        drain_outbox=_noop_drain,
        read_fresh_line=lambda: "",
        input_queued_input_port=port,
    )

    assert step.kind is CodingLoopStepKind.PROVIDER_CONTENT
    assert step.line == "late queued\n"
    assert step.selected_provider_content == ProductContent("late queued")


# --- EOF / Ctrl-C sentinels ---------------------------------------------------


def test_empty_read_yields_plain_eof_sentinel() -> None:
    queue = CodingInputQueue()
    controller, _ = _controller(queue)

    step = controller.select_next_step(
        settle_pending=False,
        drain_outbox=_noop_drain,
        read_fresh_line=lambda: "",
        input_queued_input_port=None,
    )

    assert step.kind is CodingLoopStepKind.EOF
    assert step.line == ""
    assert step.keyboard_interrupt is False


def test_keyboard_interrupt_yields_interrupt_eof_sentinel() -> None:
    queue = CodingInputQueue()
    controller, _ = _controller(queue)

    def interrupt() -> str:
        raise KeyboardInterrupt

    step = controller.select_next_step(
        settle_pending=True,
        drain_outbox=_noop_drain,
        read_fresh_line=interrupt,
        input_queued_input_port=None,
    )

    assert step.kind is CodingLoopStepKind.EOF
    assert step.keyboard_interrupt is True
    # The settled boundary fired before the blocking read, so the sentinel
    # carries the reset flag rather than re-arming shutdown-time settlement.
    assert step.settle_pending is False


def test_keyboard_interrupt_during_external_wake_yields_interrupt_eof() -> None:
    # A Ctrl-C landing while the external-wake overlay runs (after the fresh read
    # returned a line) shares the same guard as the read itself, so it converts to
    # the clean interrupt-EOF sentinel rather than propagating out of the step.
    class _RaisingPort:
        def take_next(self) -> AgentQueuedInput | None:
            return None

    port = _RaisingPort()

    class _InterruptingQueue(CodingInputQueue):
        def classify_external_wake(self, source, line):  # type: ignore[no-untyped-def]
            raise KeyboardInterrupt

    queue = _InterruptingQueue(external_inputs=(port,))
    controller, _ = _controller(queue)

    step = controller.select_next_step(
        settle_pending=False,
        drain_outbox=_noop_drain,
        read_fresh_line=lambda: "typed a line\n",
        input_queued_input_port=port,
    )

    assert step.kind is CodingLoopStepKind.EOF
    assert step.keyboard_interrupt is True


# --- once-only settled fire + re-poll -----------------------------------------


def test_settled_fires_once_and_re_polls_before_reading() -> None:
    queue = CodingInputQueue()
    emitter = _RecordingEmitter()
    # A settled observer schedules a new prompt; the re-poll must pick it up
    # instead of blocking on a fresh read.
    emitter.on_settled.append(
        lambda: queue.enqueue_extension_prompt(ProductContent("scheduled by observer"))
    )
    controller, _ = _controller(queue, emitter)

    step = controller.select_next_step(
        settle_pending=True,
        drain_outbox=_noop_drain,
        read_fresh_line=_no_read,
        input_queued_input_port=None,
    )

    assert emitter.settled_calls == 1
    assert step.kind is CodingLoopStepKind.PROVIDER_CONTENT
    assert step.line == "scheduled by observer\n"
    assert step.settle_pending is False


def test_settled_fires_once_then_reads_fresh_when_nothing_scheduled() -> None:
    queue = CodingInputQueue()
    controller, emitter = _controller(queue)

    step = controller.select_next_step(
        settle_pending=True,
        drain_outbox=_noop_drain,
        read_fresh_line=lambda: "typed after settle\n",
        input_queued_input_port=None,
    )

    assert emitter.settled_calls == 1
    assert step.kind is CodingLoopStepKind.FRESH_LINE
    assert step.line == "typed after settle\n"
    assert step.settle_pending is False


def test_settled_not_fired_when_input_is_pending() -> None:
    queue = CodingInputQueue()
    queue.defer_local_command(ProductContent("/help"))
    controller, emitter = _controller(queue)

    step = controller.select_next_step(
        settle_pending=True,
        drain_outbox=_noop_drain,
        read_fresh_line=_no_read,
        input_queued_input_port=None,
    )

    assert emitter.settled_calls == 0
    assert step.kind is CodingLoopStepKind.LOCAL_COMMAND
    # The unfired flag rides through so the shutdown-time settle can still fire.
    assert step.settle_pending is True


def test_settled_not_fired_when_flag_is_clear() -> None:
    queue = CodingInputQueue()
    controller, emitter = _controller(queue)

    step = controller.select_next_step(
        settle_pending=False,
        drain_outbox=_noop_drain,
        read_fresh_line=lambda: "",
        input_queued_input_port=None,
    )

    assert emitter.settled_calls == 0
    assert step.kind is CodingLoopStepKind.EOF
    assert step.settle_pending is False


# --- drain-before-poll ordering -----------------------------------------------


def test_drain_runs_before_each_poll_and_again_before_re_poll() -> None:
    order: list[str] = []

    class _LoggingQueue(CodingInputQueue):
        def take_next(self):  # type: ignore[no-untyped-def]
            order.append("poll")
            return super().take_next()

    queue = _LoggingQueue()

    def drain() -> None:
        order.append("drain")

    controller, _ = _controller(queue)
    controller.select_next_step(
        settle_pending=True,
        drain_outbox=drain,
        read_fresh_line=lambda: "",
        input_queued_input_port=None,
    )

    assert order == ["drain", "poll", "drain", "poll"]


# --- port rejection -----------------------------------------------------------


def test_rejects_non_exact_coding_state() -> None:
    class _StateSubclass(CodingSessionState):
        pass

    with pytest.raises(TypeError, match="coding_state must be an exact"):
        CodingSessionController(
            input_queue=CodingInputQueue(),
            coding_state=_StateSubclass(
                provider=_FakeProvider(),
                provider_name="fake",
                model_id="fake-model",
                usage_accumulator=AgentUsageAccumulator(),
                messages=(),
            ),
            emitter=_RecordingEmitter(),
        )
    with pytest.raises(TypeError, match="coding_state must be an exact"):
        CodingSessionController(
            input_queue=CodingInputQueue(),
            coding_state=object(),  # type: ignore[arg-type]
            emitter=_RecordingEmitter(),
        )


def test_rejects_non_queue_input() -> None:
    with pytest.raises(TypeError, match="input_queue must be a CodingInputQueue"):
        CodingSessionController(
            input_queue=object(),  # type: ignore[arg-type]
            coding_state=_coding_state(),
            emitter=_RecordingEmitter(),
        )


def test_rejects_non_callable_reader_and_drain() -> None:
    controller, _ = _controller(CodingInputQueue())
    with pytest.raises(TypeError, match="read_fresh_line must be callable"):
        controller.select_next_step(
            settle_pending=False,
            drain_outbox=_noop_drain,
            read_fresh_line=object(),  # type: ignore[arg-type]
            input_queued_input_port=None,
        )
    with pytest.raises(TypeError, match="drain_outbox must be callable"):
        controller.select_next_step(
            settle_pending=False,
            drain_outbox=object(),  # type: ignore[arg-type]
            read_fresh_line=_no_read,
            input_queued_input_port=None,
        )


# --- step invariants ----------------------------------------------------------


def test_step_rejects_inconsistent_construction() -> None:
    with pytest.raises(ValueError, match="an EOF step has no line"):
        CodingLoopStep(CodingLoopStepKind.EOF, "x", True)
    with pytest.raises(ValueError, match="a non-EOF step requires a non-empty line"):
        CodingLoopStep(CodingLoopStepKind.FRESH_LINE, "", True)
    with pytest.raises(TypeError, match="provider-content steps require"):
        CodingLoopStep(CodingLoopStepKind.PROVIDER_CONTENT, "x\n", True)
    with pytest.raises(ValueError, match="only provider-content steps carry"):
        CodingLoopStep(
            CodingLoopStepKind.FRESH_LINE,
            "x\n",
            True,
            selected_provider_content=ProductContent("x"),
        )
    with pytest.raises(ValueError, match="only an EOF step may record"):
        CodingLoopStep(
            CodingLoopStepKind.FRESH_LINE, "x\n", True, keyboard_interrupt=True
        )

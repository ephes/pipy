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

from datetime import UTC, datetime

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentQueuedInputKind,
)
from pipy_harness.native.agent.usage import AgentUsageAccumulator
from pipy_harness.native.coding.input_queue import CodingInputQueue
from pipy_harness.native.coding.commands import (
    CommandDispatchResolution,
    CommandDispatchResolutionKind,
    ExtensionDispatchResolution,
    ResourceDispatchKind,
    ResourceDispatchResolution,
)
from pipy_harness.native.coding.result import NativeToolReplResult
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


# --- command dispatch precedence ---------------------------------------------


class _FakeCommandEffects:
    """Recording :class:`CodingCommandEffects` with configurable dispatch results."""

    def __init__(
        self,
        *,
        resource: ResourceDispatchResolution | None = None,
        extension: ExtensionDispatchResolution | None = None,
    ) -> None:
        self._resource = resource
        self._extension = extension
        self.diagnostics: list[str] = []
        self.footer_calls = 0
        self.resource_invocations = 0
        self.resource_dispatches: list[str] = []
        self.extension_dispatches: list[str] = []

    def emit_diagnostic(self, message: str) -> None:
        self.diagnostics.append(message)

    def refresh_footer(self) -> None:
        self.footer_calls += 1

    def record_resource_invocation(self) -> None:
        self.resource_invocations += 1

    def dispatch_resource(
        self, command_text: str
    ) -> ResourceDispatchResolution | None:
        self.resource_dispatches.append(command_text)
        return self._resource

    def dispatch_extension(
        self, command_text: str
    ) -> ExtensionDispatchResolution | None:
        self.extension_dispatches.append(command_text)
        return self._extension


def _dispatch(
    *,
    command_text: str,
    user_input: str,
    selected_provider_content: ProductContent | None = None,
    effects: _FakeCommandEffects,
) -> CommandDispatchResolution:
    controller, _ = _controller(CodingInputQueue())
    return controller.dispatch_command(
        command_text=command_text,
        user_input=user_input,
        selected_provider_content=selected_provider_content,
        effects=effects,
    )


def test_plain_prompt_proceeds_to_run_without_effects() -> None:
    effects = _FakeCommandEffects()

    resolution = _dispatch(
        command_text="hello there",
        user_input="hello there",
        effects=effects,
    )

    assert resolution.kind is CommandDispatchResolutionKind.PROCEED_TO_RUN
    assert resolution.user_input == "hello there"
    assert resolution.resource_provider_text is None
    assert resolution.selected_provider_content is None
    assert effects.resource_dispatches == ["hello there"]
    assert effects.extension_dispatches == ["hello there"]
    assert effects.diagnostics == []
    assert effects.footer_calls == 0
    assert effects.resource_invocations == 0


def test_provider_content_falls_straight_through_to_run() -> None:
    effects = _FakeCommandEffects()
    content = ProductContent("queued turn")

    resolution = _dispatch(
        command_text="",
        user_input="queued turn",
        selected_provider_content=content,
        effects=effects,
    )

    assert resolution.kind is CommandDispatchResolutionKind.PROCEED_TO_RUN
    assert resolution.selected_provider_content == content
    assert resolution.resource_provider_text is None
    assert effects.resource_dispatches == [""]
    assert effects.extension_dispatches == [""]


def test_resource_list_is_consumed_locally() -> None:
    effects = _FakeCommandEffects(
        resource=ResourceDispatchResolution(ResourceDispatchKind.LIST, "the list")
    )

    resolution = _dispatch(command_text="/skills", user_input="/skills", effects=effects)

    assert resolution.kind is CommandDispatchResolutionKind.CONTINUE_LOOP
    assert effects.diagnostics == ["the list"]
    assert effects.footer_calls == 1
    assert effects.extension_dispatches == []
    assert effects.resource_invocations == 0


def test_resource_reject_is_consumed_locally() -> None:
    effects = _FakeCommandEffects(
        resource=ResourceDispatchResolution(ResourceDispatchKind.REJECT, "nope")
    )

    resolution = _dispatch(command_text="/bad", user_input="/bad", effects=effects)

    assert resolution.kind is CommandDispatchResolutionKind.CONTINUE_LOOP
    assert effects.diagnostics == ["nope"]
    assert effects.footer_calls == 1
    assert effects.extension_dispatches == []


def test_resource_run_proceeds_with_provider_text_and_records_invocation() -> None:
    effects = _FakeCommandEffects(
        resource=ResourceDispatchResolution(
            ResourceDispatchKind.RUN, "ran /skill", "expanded prompt"
        )
    )

    resolution = _dispatch(command_text="/skill", user_input="/skill", effects=effects)

    assert resolution.kind is CommandDispatchResolutionKind.PROCEED_TO_RUN
    assert resolution.resource_provider_text == "expanded prompt"
    assert resolution.user_input == "/skill"
    assert effects.resource_invocations == 1
    assert effects.diagnostics == ["ran /skill"]
    # A resource run never dispatches an extension and paints no footer here.
    assert effects.extension_dispatches == []
    assert effects.footer_calls == 0


def test_resource_run_with_no_provider_text_carries_empty_string() -> None:
    effects = _FakeCommandEffects(
        resource=ResourceDispatchResolution(ResourceDispatchKind.RUN, "ran", None)
    )

    resolution = _dispatch(command_text="/skill", user_input="/skill", effects=effects)

    assert resolution.kind is CommandDispatchResolutionKind.PROCEED_TO_RUN
    assert resolution.resource_provider_text == ""


def test_extension_command_consumed_without_error() -> None:
    effects = _FakeCommandEffects(
        extension=ExtensionDispatchResolution(name="hi", ran=True, error=None)
    )

    resolution = _dispatch(command_text="/hi", user_input="/hi", effects=effects)

    assert resolution.kind is CommandDispatchResolutionKind.CONTINUE_LOOP
    assert effects.diagnostics == []
    assert effects.footer_calls == 1


def test_extension_command_failure_surfaces_diagnostic() -> None:
    effects = _FakeCommandEffects(
        extension=ExtensionDispatchResolution(name="hi", ran=False, error="ValueError")
    )

    resolution = _dispatch(command_text="/hi", user_input="/hi", effects=effects)

    assert resolution.kind is CommandDispatchResolutionKind.CONTINUE_LOOP
    assert effects.diagnostics == [
        "pipy: extension command /hi failed (ValueError)"
    ]
    assert effects.footer_calls == 1


def test_unhandled_slash_command_reports_supported_commands() -> None:
    effects = _FakeCommandEffects()

    resolution = _dispatch(command_text="/bogus", user_input="/bogus", effects=effects)

    assert resolution.kind is CommandDispatchResolutionKind.CONTINUE_LOOP
    assert len(effects.diagnostics) == 1
    assert effects.diagnostics[0].startswith(
        "pipy: '/bogus' is not handled in tool-loop mode; "
        "supported local commands are /hotkeys, /reload,"
    )
    assert effects.diagnostics[0].endswith("Other prompts are sent to the model.")
    assert effects.footer_calls == 1


def test_precedence_resource_run_beats_extension() -> None:
    effects = _FakeCommandEffects(
        resource=ResourceDispatchResolution(
            ResourceDispatchKind.RUN, "ran", "text"
        ),
        extension=ExtensionDispatchResolution(name="x", ran=True, error=None),
    )

    resolution = _dispatch(command_text="/x", user_input="/x", effects=effects)

    assert resolution.kind is CommandDispatchResolutionKind.PROCEED_TO_RUN
    # The extension port is never consulted once a resource run wins.
    assert effects.extension_dispatches == []


def test_dispatch_command_rejects_non_effects_port() -> None:
    controller, _ = _controller(CodingInputQueue())

    with pytest.raises(TypeError, match="effects must implement CodingCommandEffects"):
        controller.dispatch_command(
            command_text="/x",
            user_input="/x",
            selected_provider_content=None,
            effects=object(),  # type: ignore[arg-type]
        )


def test_dispatch_command_rejects_wrong_resource_result_type() -> None:
    class _BadResource(_FakeCommandEffects):
        def dispatch_resource(self, command_text: str) -> ResourceDispatchResolution:
            return object()  # type: ignore[return-value]

    controller, _ = _controller(CodingInputQueue())
    with pytest.raises(TypeError, match="ResourceDispatchResolution"):
        controller.dispatch_command(
            command_text="/x",
            user_input="/x",
            selected_provider_content=None,
            effects=_BadResource(),
        )


def test_dispatch_command_rejects_wrong_extension_result_type() -> None:
    class _BadExtension(_FakeCommandEffects):
        def dispatch_extension(
            self, command_text: str
        ) -> ExtensionDispatchResolution:
            return object()  # type: ignore[return-value]

    controller, _ = _controller(CodingInputQueue())
    with pytest.raises(TypeError, match="ExtensionDispatchResolution"):
        controller.dispatch_command(
            command_text="/x",
            user_input="/x",
            selected_provider_content=None,
            effects=_BadExtension(),
        )


def test_command_dispatch_resolution_rejects_inconsistent_construction() -> None:
    with pytest.raises(ValueError, match="CONTINUE_LOOP resolution carries no"):
        CommandDispatchResolution(
            CommandDispatchResolutionKind.CONTINUE_LOOP, user_input="x"
        )
    with pytest.raises(TypeError, match="user_input must be an exact str"):
        CommandDispatchResolution(
            CommandDispatchResolutionKind.PROCEED_TO_RUN,
            user_input=None,  # type: ignore[arg-type]
        )


# --- run_loop: loop driver + start/shutdown lifecycle ------------------------


class _LogEmitter:
    """A settled emitter that appends its fire to a shared ordering log."""

    def __init__(self, log: list[str]) -> None:
        self._log = log

    def agent_settled(self) -> None:
        self._log.append("settled")


def _repl_result(status: HarnessStatus = HarnessStatus.SUCCEEDED) -> NativeToolReplResult:
    now = datetime.now(UTC)
    return NativeToolReplResult(
        status=status,
        exit_code=0 if status is HarnessStatus.SUCCEEDED else 1,
        started_at=now,
        ended_at=now,
        provider_name="fake",
        model_id="fake-model",
    )


def _run_loop_controller(log: list[str]) -> CodingSessionController:
    return CodingSessionController(
        input_queue=CodingInputQueue(),
        coding_state=_coding_state(),
        emitter=_LogEmitter(log),
    )


def test_run_loop_fires_lifecycle_in_order_and_returns_drive_result() -> None:
    log: list[str] = []
    controller = _run_loop_controller(log)
    result = _repl_result()

    def drive() -> NativeToolReplResult:
        log.append("drive")
        return result

    returned = controller.run_loop(
        drive=drive,
        fire_session_start=lambda: log.append("start"),
        fire_session_shutdown=lambda: log.append("shutdown"),
        consume_settle_pending=lambda: True,
        clear_extension_chrome=lambda: log.append("clear"),
    )

    # session_start fires before the loop; the once-only true-idle settle, the
    # session_shutdown fire, and the extension-chrome clear run after it, in that
    # exact order; and run_loop returns whatever result the driver selected.
    assert returned is result
    assert log == ["start", "drive", "settled", "shutdown", "clear"]


def test_run_loop_passes_through_a_terminate_failed_result() -> None:
    log: list[str] = []
    controller = _run_loop_controller(log)
    failed = _repl_result(HarnessStatus.FAILED)

    returned = controller.run_loop(
        drive=lambda: failed,
        fire_session_start=lambda: log.append("start"),
        fire_session_shutdown=lambda: log.append("shutdown"),
        consume_settle_pending=lambda: True,
        clear_extension_chrome=lambda: log.append("clear"),
    )

    assert returned is failed
    assert log == ["start", "settled", "shutdown", "clear"]


def test_run_loop_skips_settle_when_not_pending() -> None:
    log: list[str] = []
    controller = _run_loop_controller(log)

    controller.run_loop(
        drive=lambda: _repl_result(),
        fire_session_start=lambda: log.append("start"),
        fire_session_shutdown=lambda: log.append("shutdown"),
        consume_settle_pending=lambda: False,
        clear_extension_chrome=lambda: log.append("clear"),
    )

    # A cleared true-idle flag means agent_settled must not fire, but shutdown
    # and chrome-clear still run.
    assert "settled" not in log
    assert log == ["start", "shutdown", "clear"]


def test_run_loop_runs_finally_when_drive_raises() -> None:
    log: list[str] = []
    controller = _run_loop_controller(log)

    def drive() -> NativeToolReplResult:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        controller.run_loop(
            drive=drive,
            fire_session_start=lambda: log.append("start"),
            fire_session_shutdown=lambda: log.append("shutdown"),
            consume_settle_pending=lambda: True,
            clear_extension_chrome=lambda: log.append("clear"),
        )

    # The finally-always guarantee holds on the exception exit path too.
    assert log == ["start", "settled", "shutdown", "clear"]


def test_run_loop_does_not_run_finally_when_session_start_raises() -> None:
    log: list[str] = []
    controller = _run_loop_controller(log)

    def start() -> None:
        raise RuntimeError("start-failed")

    def drive() -> NativeToolReplResult:  # pragma: no cover - never reached
        raise AssertionError("drive must not run when session_start fails")

    with pytest.raises(RuntimeError, match="start-failed"):
        controller.run_loop(
            drive=drive,
            fire_session_start=start,
            fire_session_shutdown=lambda: log.append("shutdown"),
            consume_settle_pending=lambda: False,
            clear_extension_chrome=lambda: log.append("clear"),
        )

    # session_start fires outside the try, so a start failure does not run the
    # shutdown bookend for a session that never started.
    assert log == []


def test_run_loop_rejects_non_callable_ports() -> None:
    controller = _run_loop_controller([])

    def ok_drive() -> NativeToolReplResult:
        return _repl_result()

    def noop() -> None:
        return None

    def not_pending() -> bool:
        return False

    with pytest.raises(TypeError, match="drive must be callable"):
        controller.run_loop(
            drive=None,  # type: ignore[arg-type]
            fire_session_start=noop,
            fire_session_shutdown=noop,
            consume_settle_pending=not_pending,
            clear_extension_chrome=noop,
        )
    with pytest.raises(TypeError, match="fire_session_start must be callable"):
        controller.run_loop(
            drive=ok_drive,
            fire_session_start=None,  # type: ignore[arg-type]
            fire_session_shutdown=noop,
            consume_settle_pending=not_pending,
            clear_extension_chrome=noop,
        )
    with pytest.raises(TypeError, match="fire_session_shutdown must be callable"):
        controller.run_loop(
            drive=ok_drive,
            fire_session_start=noop,
            fire_session_shutdown=None,  # type: ignore[arg-type]
            consume_settle_pending=not_pending,
            clear_extension_chrome=noop,
        )
    with pytest.raises(TypeError, match="consume_settle_pending must be callable"):
        controller.run_loop(
            drive=ok_drive,
            fire_session_start=noop,
            fire_session_shutdown=noop,
            consume_settle_pending=None,  # type: ignore[arg-type]
            clear_extension_chrome=noop,
        )
    with pytest.raises(TypeError, match="clear_extension_chrome must be callable"):
        controller.run_loop(
            drive=ok_drive,
            fire_session_start=noop,
            fire_session_shutdown=noop,
            consume_settle_pending=not_pending,
            clear_extension_chrome=None,  # type: ignore[arg-type]
        )

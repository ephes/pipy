"""Headless controller for a product coding session's outer transitions.

This module owns the two most tightly-coupled outer transitions of
``NativeToolReplSession.run``: input selection and the true-idle
(``agent_settled``) boundary. A single :meth:`CodingSessionController.select_next_step`
call reproduces the exact top-of-loop policy that previously lived inline in the
monolith:

* drain the extension outboxes, then take one queued input using the product
  priority owned by :class:`~pipy_harness.native.coding.input_queue.CodingInputQueue`;
* if nothing local/retained/provider-visible is pending and a prior run armed the
  true-idle boundary, fire ``agent_settled`` exactly once, re-drain, and re-poll
  so a settled observer's freshly enqueued prompt becomes the next run instead of
  blocking on input;
* otherwise read one fresh line through an injected reader and apply the
  ``classify_external_wake`` overlay for a registered input-stream source under a
  single ``KeyboardInterrupt`` guard spanning both, matching the deleted inline
  block; and
* return a typed, frozen :class:`CodingLoopStep` describing the selected input or
  an EOF/Ctrl-C sentinel.

The controller is headless: it drives only the injected ports (the input queue,
an outbox-drain callable, a fresh-line reader callable, and the settled-event
emitter) plus its exact session-state anchor. It never touches the terminal,
renderer, ``repl_input``, extensions, providers, tools, persistence, automation,
the SDK, capture, or the workflow archive; the read_line call, footer text,
prefill rehydration, and separator printing stay in the composition root.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentQueuedInputPort,
)
from pipy_harness.native.coding.input_queue import (
    CodingInputQueue,
    CodingInputSelection,
    CodingInputSource,
)
from pipy_harness.native.coding.state import CodingSessionState


@runtime_checkable
class SettledEventEmitter(Protocol):
    """Narrow emitter port for the once-only true-idle notification."""

    def agent_settled(self) -> None: ...


class CodingLoopStepKind(Enum):
    """Closed classification of one outer-loop step selected by the controller."""

    LOCAL_COMMAND = "local_command"
    RETAINED_FRESH = "retained_fresh"
    PROVIDER_CONTENT = "provider_content"
    FRESH_LINE = "fresh_line"
    EOF = "eof"


@dataclass(frozen=True, slots=True)
class CodingLoopStep:
    """One outer-loop step: the selected input, or an EOF/Ctrl-C sentinel.

    ``line`` is the exact line the composition loop consumes next. It is empty
    only for :attr:`CodingLoopStepKind.EOF`. ``selected_provider_content`` and
    ``queued_input`` are carried only for provider-visible content. ``settle_pending``
    is the post-boundary value of the run's true-idle flag; the composition loop
    assigns it straight back so the inline ``try/finally`` observes the exact
    once-only ``agent_settled`` timing. ``keyboard_interrupt`` is set only for an
    EOF sentinel produced by a ``KeyboardInterrupt`` during the fresh read.
    """

    kind: CodingLoopStepKind
    line: str
    settle_pending: bool
    selected_provider_content: ProductContent | None = None
    queued_input: AgentQueuedInput | None = None
    keyboard_interrupt: bool = False

    def __post_init__(self) -> None:
        if type(self.kind) is not CodingLoopStepKind:
            raise TypeError("kind must be an exact CodingLoopStepKind")
        if type(self.line) is not str:
            raise TypeError("line must be an exact str")
        if type(self.settle_pending) is not bool:
            raise TypeError("settle_pending must be an exact bool")
        if type(self.keyboard_interrupt) is not bool:
            raise TypeError("keyboard_interrupt must be an exact bool")
        if self.kind is CodingLoopStepKind.PROVIDER_CONTENT:
            if type(self.selected_provider_content) is not ProductContent:
                raise TypeError(
                    "provider-content steps require an exact ProductContent"
                )
            if self.queued_input is not None and (
                type(self.queued_input) is not AgentQueuedInput
            ):
                raise TypeError("queued_input must be an exact AgentQueuedInput")
        else:
            if self.selected_provider_content is not None:
                raise ValueError(
                    "only provider-content steps carry selected_provider_content"
                )
            if self.queued_input is not None:
                raise ValueError("only provider-content steps carry queued_input")
        if self.kind is CodingLoopStepKind.EOF:
            if self.line != "":
                raise ValueError("an EOF step has no line")
        else:
            if self.line == "":
                raise ValueError("a non-EOF step requires a non-empty line")
            if self.keyboard_interrupt:
                raise ValueError("only an EOF step may record a keyboard interrupt")

    @classmethod
    def local_command(cls, line: str, settle_pending: bool) -> CodingLoopStep:
        return cls(CodingLoopStepKind.LOCAL_COMMAND, line, settle_pending)

    @classmethod
    def retained_fresh(cls, line: str, settle_pending: bool) -> CodingLoopStep:
        return cls(CodingLoopStepKind.RETAINED_FRESH, line, settle_pending)

    @classmethod
    def provider_content(
        cls,
        line: str,
        content: ProductContent,
        queued_input: AgentQueuedInput | None,
        settle_pending: bool,
    ) -> CodingLoopStep:
        return cls(
            CodingLoopStepKind.PROVIDER_CONTENT,
            line,
            settle_pending,
            selected_provider_content=content,
            queued_input=queued_input,
        )

    @classmethod
    def fresh_line(cls, line: str, settle_pending: bool) -> CodingLoopStep:
        return cls(CodingLoopStepKind.FRESH_LINE, line, settle_pending)

    @classmethod
    def eof(cls, *, settle_pending: bool, keyboard_interrupt: bool) -> CodingLoopStep:
        return cls(
            CodingLoopStepKind.EOF,
            "",
            settle_pending,
            keyboard_interrupt=keyboard_interrupt,
        )


class CodingSessionController:
    """Own the input-selection and true-idle transitions for one coding session.

    The controller is constructed once per run from the session's already-owned
    input queue, its exact :class:`CodingSessionState` anchor, and the
    settled-event emitter. The composition loop calls :meth:`select_next_step`
    each iteration and consumes the returned :class:`CodingLoopStep`; the loop
    skeleton, lifecycle firing, command dispatch, run transition, and result
    building remain in the composition root.
    """

    __slots__ = ("_coding_state", "_emitter", "_input_queue")

    def __init__(
        self,
        *,
        input_queue: CodingInputQueue,
        coding_state: CodingSessionState,
        emitter: SettledEventEmitter,
    ) -> None:
        if not isinstance(input_queue, CodingInputQueue):
            raise TypeError("input_queue must be a CodingInputQueue")
        if type(coding_state) is not CodingSessionState:
            raise TypeError("coding_state must be an exact CodingSessionState")
        self._input_queue = input_queue
        self._coding_state = coding_state
        self._emitter = emitter

    def select_next_step(
        self,
        *,
        settle_pending: bool,
        drain_outbox: Callable[[], None],
        read_fresh_line: Callable[[], str],
        input_queued_input_port: AgentQueuedInputPort | None,
    ) -> CodingLoopStep:
        """Select the next outer-loop step using the exact product priority.

        ``settle_pending`` is the run's current true-idle flag; the returned step
        carries its post-boundary value. ``drain_outbox`` drains extension-enqueued
        messages, ``read_fresh_line`` reads one fresh line (returning ``""`` at EOF
        and raising ``KeyboardInterrupt`` on Ctrl-C), and ``input_queued_input_port``
        is the optional registered input-stream source used for the
        ``classify_external_wake`` overlay.
        """

        if not callable(drain_outbox):
            raise TypeError("drain_outbox must be callable")
        if not callable(read_fresh_line):
            raise TypeError("read_fresh_line must be callable")

        drain_outbox()
        step = self._step_from_selection(
            self._input_queue.take_next(), settle_pending
        )
        if step is not None:
            return step

        # No local/retained/provider-visible input is pending. If a prior run
        # armed the true-idle boundary, fire ``agent_settled`` exactly once, then
        # re-drain and re-poll so a settled observer's newly scheduled prompt
        # becomes the next run instead of blocking on fresh input.
        if settle_pending:
            settle_pending = False
            self._emitter.agent_settled()
            drain_outbox()
            step = self._step_from_selection(
                self._input_queue.take_next(), settle_pending
            )
            if step is not None:
                return step

        # The fresh read AND the external-wake overlay share one
        # ``KeyboardInterrupt`` guard, exactly as the superseded inline block did:
        # a Ctrl-C landing during ``classify_external_wake``'s non-blocking poll /
        # line comparison converts to the same clean EOF-break-with-newline path as
        # one landing during the blocking read, rather than propagating out through
        # the run's ``finally`` as an observably different exit.
        try:
            line = read_fresh_line()
            wake = (
                self._input_queue.classify_external_wake(input_queued_input_port, line)
                if input_queued_input_port is not None
                else None
            )
        except KeyboardInterrupt:
            return CodingLoopStep.eof(
                settle_pending=settle_pending, keyboard_interrupt=True
            )
        if wake is not None:
            return self._step_from_wake(wake, settle_pending)
        if not line:
            return CodingLoopStep.eof(
                settle_pending=settle_pending, keyboard_interrupt=False
            )
        return CodingLoopStep.fresh_line(line, settle_pending)

    def _step_from_selection(
        self,
        selection: CodingInputSelection | None,
        settle_pending: bool,
    ) -> CodingLoopStep | None:
        if selection is None:
            return None
        if selection.source is CodingInputSource.LOCAL_COMMAND:
            return CodingLoopStep.local_command(
                f"{selection.content.value}\n", settle_pending
            )
        if selection.source is CodingInputSource.RETAINED_FRESH_INPUT:
            return CodingLoopStep.retained_fresh(selection.content.value, settle_pending)
        return CodingLoopStep.provider_content(
            f"{selection.content.value}\n",
            selection.content,
            selection.queued_input,
            settle_pending,
        )

    def _step_from_wake(
        self,
        wake: CodingInputSelection,
        settle_pending: bool,
    ) -> CodingLoopStep:
        if wake.source is CodingInputSource.LOCAL_COMMAND:
            return CodingLoopStep.local_command(
                f"{wake.content.value}\n", settle_pending
            )
        return CodingLoopStep.provider_content(
            f"{wake.content.value}\n",
            wake.content,
            wake.queued_input,
            settle_pending,
        )


__all__ = [
    "CodingLoopStep",
    "CodingLoopStepKind",
    "CodingSessionController",
    "SettledEventEmitter",
]

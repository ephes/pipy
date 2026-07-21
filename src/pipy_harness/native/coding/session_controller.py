"""Headless controller for a product coding session's outer transitions.

This module owns the outer transitions of ``NativeToolReplSession.run``. The
loop driver and start/shutdown lifecycle live in
:meth:`CodingSessionController.run_loop`: the controller fires ``session_start``,
drives the injected ``while True`` step closure, and guarantees the once-only
true-idle settle, the ``session_shutdown`` fire, and the extension-chrome clear
on every exit path (normal, fatal, or exception). Within each step it owns input
selection and the true-idle (``agent_settled``) boundary. A single
:meth:`CodingSessionController.select_next_step`
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
from pipy_harness.native.coding.commands import (
    CodingCommandOutcomeKind,
    CommandDispatchResolution,
    ExtensionDispatchResolution,
    ResourceDispatchKind,
    ResourceDispatchResolution,
    classify_coding_command,
)
from pipy_harness.native.coding.input_queue import (
    CodingInputQueue,
    CodingInputSelection,
    CodingInputSource,
)
from pipy_harness.native.coding.result import NativeToolReplResult
from pipy_harness.native.coding.state import CodingSessionState

_NOT_HANDLED_COMMAND_NOTICE = (
    "supported local commands are /hotkeys, /reload, "
    "/changelog, /model, /scoped-models, /settings, /trust, "
    "/login, /logout, /copy, /compact, /export, /import, "
    "/share, /session, /name, "
    "/new, /tree, /resume, /fork, /clone, /skill, "
    "/exit, /quit "
    "(plus any workspace prompt templates and custom "
    "commands as /<name>, and activated extension "
    "commands). Other prompts are sent to the model."
)


@runtime_checkable
class SettledEventEmitter(Protocol):
    """Narrow emitter port for the once-only true-idle notification."""

    def agent_settled(self) -> None: ...


@runtime_checkable
class CodingCommandEffects(Protocol):
    """Composition-root effect port for the command-dispatch precedence.

    Every method performs a composition-root effect the headless controller may
    not reach directly (terminal/renderer diagnostics, footer painting, resource/
    extension dispatch, and the resource-invocation counter). The controller owns
    only the ordering/precedence and the resulting
    :class:`~pipy_harness.native.coding.commands.CommandDispatchResolution`; the
    concrete port implementation stays in the composition root.
    """

    def emit_diagnostic(self, message: str) -> None: ...

    def refresh_footer(self) -> None: ...

    def record_resource_invocation(self) -> None: ...

    def dispatch_resource(
        self, command_text: str
    ) -> ResourceDispatchResolution | None: ...

    def dispatch_extension(
        self, command_text: str
    ) -> ExtensionDispatchResolution | None: ...


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

    def run_loop(
        self,
        *,
        drive: Callable[[], NativeToolReplResult],
        fire_session_start: Callable[[], None],
        fire_session_shutdown: Callable[[], None],
        consume_settle_pending: Callable[[], bool],
        clear_extension_chrome: Callable[[], None],
    ) -> NativeToolReplResult:
        """Drive the outer session loop and own the start/shutdown lifecycle.

        The controller owns the lifecycle bookends of one product coding
        session: it fires ``session_start`` once the session is set up, drives
        the injected ``drive`` closure (the ``while True`` step loop, whose exit
        paths return the bounded :class:`NativeToolReplResult` — the terminate
        ``FAILED`` projection or the post-loop ``SUCCEEDED`` projection), and
        guarantees, on every exit path (normal return, fatal return, or a
        propagated exception), the once-only true-idle settle, the
        ``session_shutdown`` fire, and the extension-chrome clear — in that
        order.

        Every effect is performed through an injected port so the controller
        never touches the terminal, renderer, ``repl_input``, extensions,
        providers, tools, persistence, automation, the SDK, capture, or the
        workflow archive: ``fire_session_start``/``fire_session_shutdown`` fire
        the composition root's lifecycle emitter, ``consume_settle_pending``
        reads-and-resets the run's armed true-idle flag (the controller fires the
        once-only ``agent_settled`` through its own settled emitter when it
        returns ``True``), and ``clear_extension_chrome`` clears any live TUI
        chrome. ``drive`` returns the projected result the loop selected.
        """

        if not callable(drive):
            raise TypeError("drive must be callable")
        if not callable(fire_session_start):
            raise TypeError("fire_session_start must be callable")
        if not callable(fire_session_shutdown):
            raise TypeError("fire_session_shutdown must be callable")
        if not callable(consume_settle_pending):
            raise TypeError("consume_settle_pending must be callable")
        if not callable(clear_extension_chrome):
            raise TypeError("clear_extension_chrome must be callable")

        # ``session_start`` fires once the session is set up (reason "startup"),
        # outside the try so a setup-fire failure does not run the shutdown
        # bookend for a session that never started; ``session_shutdown`` fires
        # from the finally below so it — like the true-idle settle and the
        # chrome clear — runs on EVERY exit path.
        fire_session_start()
        try:
            return drive()
        finally:
            if consume_settle_pending():
                self._emitter.agent_settled()
            fire_session_shutdown()
            clear_extension_chrome()

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
        step = self._step_from_selection(self._input_queue.take_next(), settle_pending)
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
            return CodingLoopStep.retained_fresh(
                selection.content.value, settle_pending
            )
        return CodingLoopStep.provider_content(
            f"{selection.content.value}\n",
            selection.content,
            selection.queued_input,
            settle_pending,
        )

    def dispatch_command(
        self,
        *,
        command_text: str,
        stripped: str,
        user_input: str,
        selected_provider_content: ProductContent | None,
        effects: CodingCommandEffects,
    ) -> CommandDispatchResolution:
        """Resolve the built-in>resource>extension command precedence.

        The controller owns the full ordering/precedence and outcome routing.
        Built-in commands are classified FIRST so a resource or extension can
        never shadow them: ``/exit``/``/quit`` resolve to ``EXIT_LOOP`` (the
        composition loop breaks), and every other continuing built-in resolves
        to ``INTERPRET_BUILTIN`` carrying the ``CodingCommandOutcome`` the
        composition loop still interprets inline (the imperative per-action
        effect chain has not yet been relocated behind ports). The classification
        runs only when the composition loop would have run it inline — for typed
        input, or an empty submission — never for non-empty provider/queued
        content, whose blank ``command_text`` must reach the provider verbatim
        and therefore falls straight through to ``PROCEED_TO_RUN``.

        A non-built-in ``/…`` (or plain prompt) then runs resource dispatch
        (a list/reject is consumed locally; a run records the invocation counter
        and carries the bounded provider text), then extension dispatch (so a
        custom command can never shadow a built-in or a resource), then the
        unhandled ``/…`` fallback; every effect is performed through the injected
        :class:`CodingCommandEffects` port.
        """

        if not isinstance(effects, CodingCommandEffects):
            raise TypeError("effects must implement CodingCommandEffects")

        # Built-in commands classify first, gated by the exact inline condition
        # (``selected_provider_content is None or not stripped``) so non-empty
        # provider/queued content — which carries a blank ``command_text`` — is
        # never intercepted as an empty built-in.
        if selected_provider_content is None or not stripped:
            outcome = classify_coding_command(ProductContent(command_text))
            if outcome.kind is CodingCommandOutcomeKind.EXIT:
                return CommandDispatchResolution.exit_loop()
            if outcome.kind is CodingCommandOutcomeKind.CONTINUE:
                return CommandDispatchResolution.interpret_builtin(outcome)

        # Resource dispatch (skills, prompt templates, custom commands) runs
        # through the same local-command boundary as the built-ins, after them so
        # a custom command can never shadow a built-in.
        resource = effects.dispatch_resource(command_text)
        resource_provider_text: str | None = None
        if resource is not None:
            if type(resource) is not ResourceDispatchResolution:
                raise TypeError(
                    "dispatch_resource must return a ResourceDispatchResolution"
                )
            if resource.kind is ResourceDispatchKind.LIST:
                effects.emit_diagnostic(resource.message)
                effects.refresh_footer()
                return CommandDispatchResolution.continue_loop()
            if resource.kind is ResourceDispatchKind.REJECT:
                # Fail closed: diagnostic only, no provider turn, native
                # product-session write, prompt-history entry, or metadata-only
                # workflow archive event.
                effects.emit_diagnostic(resource.message)
                effects.refresh_footer()
                return CommandDispatchResolution.continue_loop()
            # RUN: the expanded/instruction text becomes the bounded
            # provider-visible message; it never reaches prompt history, the
            # native product session tree, or the metadata-only workflow archive.
            # Only the invocation counter is surfaced.
            effects.record_resource_invocation()
            resource_provider_text = resource.provider_text or ""
            effects.emit_diagnostic(resource.message)
        # Extension commands dispatch AFTER built-ins and resource commands (so
        # they can never shadow them) and BEFORE the not-handled fallback. The
        # handler runs locally with no provider turn; its notifications are live
        # UI output only.
        if resource_provider_text is None:
            extension = effects.dispatch_extension(command_text)
            if extension is not None:
                if type(extension) is not ExtensionDispatchResolution:
                    raise TypeError(
                        "dispatch_extension must return an ExtensionDispatchResolution"
                    )
                if not extension.ran and extension.error:
                    effects.emit_diagnostic(
                        f"pipy: extension command /{extension.name} "
                        f"failed ({extension.error})"
                    )
                effects.refresh_footer()
                return CommandDispatchResolution.continue_loop()
        if command_text.startswith("/") and resource_provider_text is None:
            effects.emit_diagnostic(
                f"pipy: {command_text!r} is not handled in tool-loop mode; "
                + _NOT_HANDLED_COMMAND_NOTICE
            )
            effects.refresh_footer()
            return CommandDispatchResolution.continue_loop()
        return CommandDispatchResolution.proceed_to_run(
            user_input=user_input,
            resource_provider_text=resource_provider_text,
            selected_provider_content=selected_provider_content,
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
    "CodingCommandEffects",
    "CodingLoopStep",
    "CodingLoopStepKind",
    "CodingSessionController",
    "SettledEventEmitter",
]

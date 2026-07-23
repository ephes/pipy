"""Headless controller for a product coding session's outer transitions.

This module owns the outer transitions of ``NativeToolReplSession.run``. The
loop skeleton and start/shutdown lifecycle live in
:meth:`CodingSessionController.run_loop`: the controller fires ``session_start``,
runs the ``while True`` skeleton itself — calling the injected per-iteration
``step_once`` port and routing its returned :class:`LoopStepSignal`
(``CONTINUE`` re-enters the loop, ``BREAK`` finalizes the post-loop
``SUCCEEDED`` projection through the injected ``finalize`` port, ``RETURN_RESULT``
returns the terminate ``FAILED`` projection the step selected) — and guarantees
the once-only true-idle settle, the ``session_shutdown`` fire, and the
extension-chrome clear on every exit path (normal, fatal, or exception). Within
each step it owns input selection and the true-idle (``agent_settled``)
boundary. A single
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
from pipy_harness.native.coding.command_registry import classify_coding_command
from pipy_harness.native.coding.commands import (
    CodingCommandOutcome,
    CodingCommandOutcomeKind,
    CommandDispatchResolution,
    ExtensionDispatchResolution,
    ResourceDispatchKind,
    ResourceDispatchResolution,
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
    """Injected effect port for the command-dispatch precedence.

    Every method performs a product effect the headless controller may not reach
    directly (terminal/renderer diagnostics, footer painting, resource/extension
    dispatch, the resource-invocation counter, and the continuing built-in's
    imperative per-action effect chain). The controller owns only the ordering/
    precedence and the resulting
    :class:`~pipy_harness.native.coding.commands.CommandDispatchResolution`.
    The concrete callable adapter is owned beside this port, while the product
    effects it invokes remain injected from the composition root.
    """

    def emit_diagnostic(self, message: str) -> None: ...

    def refresh_footer(self) -> None: ...

    def interpret_builtin(self, outcome: CodingCommandOutcome) -> None: ...

    def record_resource_invocation(self) -> None: ...

    def dispatch_resource(
        self, command_text: str
    ) -> ResourceDispatchResolution | None: ...

    def dispatch_extension(
        self, command_text: str
    ) -> ExtensionDispatchResolution | None: ...


class _CallableCodingCommandEffects:
    """Composition-root :class:`CodingCommandEffects` port over run() closures.

    The controller owns the built-in>resource>extension precedence; this adapter
    performs each effect (diagnostics, footer painting, the resource-invocation
    counter, and resource/extension dispatch) through callables that close over
    the live run-loop state, so a ``/reload`` that rebinds the workspace
    resources or extension registry is reflected on the next dispatch.
    """

    __slots__ = (
        "_emit",
        "_footer",
        "_interpret",
        "_record_resource",
        "_resolve_extension",
        "_resolve_resource",
    )

    def __init__(
        self,
        *,
        emit: Callable[[str], None],
        footer: Callable[[], None],
        interpret: Callable[[CodingCommandOutcome], None],
        record_resource: Callable[[], None],
        resolve_resource: Callable[[str], ResourceDispatchResolution | None],
        resolve_extension: Callable[[str], ExtensionDispatchResolution | None],
    ) -> None:
        self._emit = emit
        self._footer = footer
        self._interpret = interpret
        self._record_resource = record_resource
        self._resolve_resource = resolve_resource
        self._resolve_extension = resolve_extension

    def emit_diagnostic(self, message: str) -> None:
        self._emit(message)

    def refresh_footer(self) -> None:
        self._footer()

    def interpret_builtin(self, outcome: CodingCommandOutcome) -> None:
        self._interpret(outcome)

    def record_resource_invocation(self) -> None:
        self._record_resource()

    def dispatch_resource(self, command_text: str) -> ResourceDispatchResolution | None:
        return self._resolve_resource(command_text)

    def dispatch_extension(
        self, command_text: str
    ) -> ExtensionDispatchResolution | None:
        return self._resolve_extension(command_text)


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
        _require_exact_coding_loop_step_fields(self)
        _require_coding_loop_step_payload(self)

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


class LoopStepSignalKind(Enum):
    """Closed classification of one per-iteration outcome the step port returns."""

    CONTINUE = "continue"
    BREAK = "break"
    RETURN_RESULT = "return_result"


@dataclass(frozen=True, slots=True)
class LoopStepSignal:
    """The routing signal one ``step_once`` call returns to :meth:`run_loop`.

    ``CONTINUE`` re-enters the loop (the composition step handled the iteration
    itself), ``BREAK`` ends the loop through the ``finalize`` port (the post-loop
    ``SUCCEEDED`` projection), and ``RETURN_RESULT`` ends the loop returning the
    exact bounded :class:`NativeToolReplResult` the step already built (the
    terminate ``FAILED`` projection). ``result`` is carried only for
    ``RETURN_RESULT``.
    """

    kind: LoopStepSignalKind
    result: NativeToolReplResult | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not LoopStepSignalKind:
            raise TypeError("kind must be an exact LoopStepSignalKind")
        if self.kind is LoopStepSignalKind.RETURN_RESULT:
            if type(self.result) is not NativeToolReplResult:
                raise TypeError("RETURN_RESULT requires an exact NativeToolReplResult")
        elif self.result is not None:
            raise ValueError("only RETURN_RESULT carries a result")

    @classmethod
    def continue_loop(cls) -> LoopStepSignal:
        return cls(LoopStepSignalKind.CONTINUE)

    @classmethod
    def break_loop(cls) -> LoopStepSignal:
        return cls(LoopStepSignalKind.BREAK)

    @classmethod
    def return_result(cls, result: NativeToolReplResult) -> LoopStepSignal:
        return cls(LoopStepSignalKind.RETURN_RESULT, result)


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
        step_once: Callable[[], LoopStepSignal],
        finalize: Callable[[], NativeToolReplResult],
        fire_session_start: Callable[[], None],
        fire_session_shutdown: Callable[[], None],
        consume_settle_pending: Callable[[], bool],
        clear_extension_chrome: Callable[[], None],
    ) -> NativeToolReplResult:
        """Own the ``while True`` skeleton and the start/shutdown lifecycle.

        The controller owns the loop skeleton and the lifecycle bookends of one
        product coding session: it fires ``session_start`` once the session is
        set up, runs the ``while True`` itself, and on each iteration calls the
        injected ``step_once`` port and routes its returned
        :class:`LoopStepSignal` — ``CONTINUE`` re-enters the loop, ``BREAK`` ends
        it through the injected ``finalize`` port (the post-loop ``SUCCEEDED``
        projection), and ``RETURN_RESULT`` ends it returning the exact bounded
        :class:`NativeToolReplResult` the step already built (the terminate
        ``FAILED`` projection). On every exit path (normal return, fatal return,
        or a propagated exception) it guarantees the once-only true-idle settle,
        the ``session_shutdown`` fire, and the extension-chrome clear — in that
        order.

        Every effect is performed through an injected port so the controller
        never touches the terminal, renderer, ``repl_input``, extensions,
        providers, tools, persistence, automation, the SDK, capture, or the
        workflow archive: ``step_once`` performs one iteration's composition-root
        work and returns only the routing signal, ``finalize`` builds the
        post-loop ``SUCCEEDED`` projection, ``fire_session_start``/
        ``fire_session_shutdown`` fire the composition root's lifecycle emitter,
        ``consume_settle_pending`` reads-and-resets the run's armed true-idle flag
        (the controller fires the once-only ``agent_settled`` through its own
        settled emitter when it returns ``True``), and ``clear_extension_chrome``
        clears any live TUI chrome.
        """

        _require_run_loop_ports(
            step_once=step_once,
            finalize=finalize,
            fire_session_start=fire_session_start,
            fire_session_shutdown=fire_session_shutdown,
            consume_settle_pending=consume_settle_pending,
            clear_extension_chrome=clear_extension_chrome,
        )

        # ``session_start`` fires once the session is set up (reason "startup"),
        # outside the try so a setup-fire failure does not run the shutdown
        # bookend for a session that never started; ``session_shutdown`` fires
        # from the finally below so it — like the true-idle settle and the
        # chrome clear — runs on EVERY exit path.
        fire_session_start()
        try:
            while True:
                signal = step_once()
                if type(signal) is not LoopStepSignal:
                    raise TypeError("step_once must return a LoopStepSignal")
                if signal.kind is LoopStepSignalKind.RETURN_RESULT:
                    assert signal.result is not None
                    return signal.result
                if signal.kind is LoopStepSignalKind.BREAK:
                    return finalize()
                # CONTINUE: the step handled the iteration; re-enter the loop.
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
        composition loop breaks), and every other continuing built-in is
        interpreted through the injected :meth:`CodingCommandEffects.interpret_builtin`
        port — the composition-root per-action effect chain that reassigns the
        run's control state — after which the controller returns ``CONTINUE_LOOP``,
        so built-in interpretation runs through the same port as resource and
        extension dispatch and the outcome never crosses back to the composition
        loop as data. The classification runs only when the composition loop
        would have run it inline — for typed input, or an empty submission —
        never for non-empty provider/queued content, whose blank ``command_text``
        must reach the provider verbatim and therefore falls straight through to
        ``PROCEED_TO_RUN``.

        A non-built-in ``/…`` (or plain prompt) then runs resource dispatch
        (a list/reject is consumed locally; a run records the invocation counter
        and carries the bounded provider text), then extension dispatch (so a
        custom command can never shadow a built-in or a resource), then the
        unhandled ``/…`` fallback; every effect is performed through the injected
        :class:`CodingCommandEffects` port.
        """

        if not isinstance(effects, CodingCommandEffects):
            raise TypeError("effects must implement CodingCommandEffects")

        builtin = _dispatch_builtin_command(
            command_text=command_text,
            stripped=stripped,
            selected_provider_content=selected_provider_content,
            effects=effects,
        )
        if builtin is not None:
            return builtin

        resource, resource_provider_text = _dispatch_resource_command(
            command_text, effects
        )
        if resource is not None:
            return resource

        if resource_provider_text is None:
            extension = _dispatch_extension_command(command_text, effects)
            if extension is not None:
                return extension
        if command_text.startswith("/") and resource_provider_text is None:
            return _dispatch_unhandled_command(command_text, effects)
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


def _require_exact_coding_loop_step_fields(step: CodingLoopStep) -> None:
    """Validate the step's exact closed field types in declaration order."""

    if type(step.kind) is not CodingLoopStepKind:
        raise TypeError("kind must be an exact CodingLoopStepKind")
    if type(step.line) is not str:
        raise TypeError("line must be an exact str")
    if type(step.settle_pending) is not bool:
        raise TypeError("settle_pending must be an exact bool")
    if type(step.keyboard_interrupt) is not bool:
        raise TypeError("keyboard_interrupt must be an exact bool")


def _require_coding_loop_step_payload(step: CodingLoopStep) -> None:
    """Validate content ownership and EOF invariants after exact field types."""

    if step.kind is CodingLoopStepKind.PROVIDER_CONTENT:
        if type(step.selected_provider_content) is not ProductContent:
            raise TypeError("provider-content steps require an exact ProductContent")
        if (
            step.queued_input is not None
            and type(step.queued_input) is not AgentQueuedInput
        ):
            raise TypeError("queued_input must be an exact AgentQueuedInput")
    else:
        if step.selected_provider_content is not None:
            raise ValueError(
                "only provider-content steps carry selected_provider_content"
            )
        if step.queued_input is not None:
            raise ValueError("only provider-content steps carry queued_input")
    if step.kind is CodingLoopStepKind.EOF:
        if step.line != "":
            raise ValueError("an EOF step has no line")
        return
    if step.line == "":
        raise ValueError("a non-EOF step requires a non-empty line")
    if step.keyboard_interrupt:
        raise ValueError("only an EOF step may record a keyboard interrupt")


def _require_run_loop_ports(
    *,
    step_once: Callable[[], LoopStepSignal],
    finalize: Callable[[], NativeToolReplResult],
    fire_session_start: Callable[[], None],
    fire_session_shutdown: Callable[[], None],
    consume_settle_pending: Callable[[], bool],
    clear_extension_chrome: Callable[[], None],
) -> None:
    """Validate lifecycle ports in the original fail-fast order."""

    if not callable(step_once):
        raise TypeError("step_once must be callable")
    if not callable(finalize):
        raise TypeError("finalize must be callable")
    if not callable(fire_session_start):
        raise TypeError("fire_session_start must be callable")
    if not callable(fire_session_shutdown):
        raise TypeError("fire_session_shutdown must be callable")
    if not callable(consume_settle_pending):
        raise TypeError("consume_settle_pending must be callable")
    if not callable(clear_extension_chrome):
        raise TypeError("clear_extension_chrome must be callable")


def _dispatch_builtin_command(
    *,
    command_text: str,
    stripped: str,
    selected_provider_content: ProductContent | None,
    effects: CodingCommandEffects,
) -> CommandDispatchResolution | None:
    """Intercept a built-in under the exact typed/provider-content gate."""

    if selected_provider_content is not None and stripped:
        return None
    outcome = classify_coding_command(ProductContent(command_text))
    if outcome.kind is CodingCommandOutcomeKind.EXIT:
        return CommandDispatchResolution.exit_loop()
    if outcome.kind is CodingCommandOutcomeKind.CONTINUE:
        effects.interpret_builtin(outcome)
        return CommandDispatchResolution.continue_loop()
    return None


def _dispatch_resource_command(
    command_text: str,
    effects: CodingCommandEffects,
) -> tuple[CommandDispatchResolution | None, str | None]:
    """Route LIST/REJECT locally and return RUN's provider-visible text."""

    resource = effects.dispatch_resource(command_text)
    if resource is None:
        return None, None
    if type(resource) is not ResourceDispatchResolution:
        raise TypeError("dispatch_resource must return a ResourceDispatchResolution")
    if resource.kind is ResourceDispatchKind.LIST:
        effects.emit_diagnostic(resource.message)
        effects.refresh_footer()
        return CommandDispatchResolution.continue_loop(), None
    if resource.kind is ResourceDispatchKind.REJECT:
        effects.emit_diagnostic(resource.message)
        effects.refresh_footer()
        return CommandDispatchResolution.continue_loop(), None
    effects.record_resource_invocation()
    provider_text = resource.provider_text or ""
    effects.emit_diagnostic(resource.message)
    return None, provider_text


def _dispatch_extension_command(
    command_text: str,
    effects: CodingCommandEffects,
) -> CommandDispatchResolution | None:
    """Consume a handled or failed extension command after resources."""

    extension = effects.dispatch_extension(command_text)
    if extension is None:
        return None
    if type(extension) is not ExtensionDispatchResolution:
        raise TypeError("dispatch_extension must return an ExtensionDispatchResolution")
    if not extension.ran and extension.error:
        effects.emit_diagnostic(
            f"pipy: extension command /{extension.name} failed ({extension.error})"
        )
    effects.refresh_footer()
    return CommandDispatchResolution.continue_loop()


def _dispatch_unhandled_command(
    command_text: str,
    effects: CodingCommandEffects,
) -> CommandDispatchResolution:
    """Consume an unhandled slash command with the product diagnostic."""

    effects.emit_diagnostic(
        f"pipy: {command_text!r} is not handled in tool-loop mode; "
        + _NOT_HANDLED_COMMAND_NOTICE
    )
    effects.refresh_footer()
    return CommandDispatchResolution.continue_loop()


__all__ = [
    "CodingCommandEffects",
    "CodingLoopStep",
    "CodingLoopStepKind",
    "CodingSessionController",
    "LoopStepSignal",
    "LoopStepSignalKind",
    "SettledEventEmitter",
    "classify_coding_command",
]

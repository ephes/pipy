"""Headless queued-input policy for a product coding session."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentQueuedInputKind,
    AgentQueuedInputPort,
)


class CodingInputSource(StrEnum):
    """Closed origins for one controller-selected input."""

    LOCAL_COMMAND = "local_command"
    RETAINED_FRESH_INPUT = "retained_fresh_input"
    RETAINED_AGENT_INPUT = "retained_agent_input"
    EXTERNAL_QUEUE = "external_queue"
    POSITIONAL_SEED = "positional_seed"
    EXTENSION_STEERING = "extension_steering"
    EXTENSION_FOLLOW_UP = "extension_follow_up"
    EXTENSION_PROMPT = "extension_prompt"


@dataclass(frozen=True, slots=True)
class CodingInputSelection:
    """One local-dispatch input or provider-visible prompt chosen by policy."""

    content: ProductContent
    source: CodingInputSource
    queued_input: AgentQueuedInput | None = None

    def __post_init__(self) -> None:
        _require_content(self.content, "content")
        if type(self.source) is not CodingInputSource:
            raise TypeError("source must be an exact CodingInputSource")
        if self.queued_input is not None:
            _require_queued_input(self.queued_input, "queued_input")
            if self.queued_input.content is not self.content:
                raise ValueError("queued_input content must be the selected content")
        if self.source in _LOCAL_DISPATCH_SOURCES:
            if self.queued_input is not None:
                raise ValueError("local-dispatch inputs cannot carry queued state")
        elif self.source in _TYPED_QUEUE_SOURCES:
            if self.queued_input is None:
                raise ValueError("typed queue sources require queued_input")
        elif self.queued_input is not None:
            raise ValueError("only typed queue sources may carry queued_input")

    @property
    def bypass_local_command_dispatch(self) -> bool:
        """Whether the selection must be sent to the provider verbatim."""

        return self.source not in _LOCAL_DISPATCH_SOURCES


_LOCAL_DISPATCH_SOURCES = frozenset(
    {
        CodingInputSource.LOCAL_COMMAND,
        CodingInputSource.RETAINED_FRESH_INPUT,
    }
)


_TYPED_QUEUE_SOURCES = frozenset(
    {
        CodingInputSource.RETAINED_AGENT_INPUT,
        CodingInputSource.EXTERNAL_QUEUE,
        CodingInputSource.EXTENSION_STEERING,
        CodingInputSource.EXTENSION_FOLLOW_UP,
    }
)


class _AgentLoopQueuedInputPort:
    """Narrow port offered to an active reusable agent loop."""

    __slots__ = ("_queue",)

    def __init__(self, queue: CodingInputQueue) -> None:
        self._queue = queue

    def take_next(self) -> AgentQueuedInput | None:
        return self._queue.take_next_for_agent_loop()


class CodingInputQueue:
    """Mutable, synchronous product policy for queued coding-session inputs."""

    __slots__ = (
        "_agent_loop_port",
        "_extension_follow_ups",
        "_extension_prompts",
        "_extension_steering",
        "_external_inputs",
        "_next_turn_context",
        "_pending_local_command",
        "_pending_local_command_source",
        "_retained_agent_inputs",
        "_retained_external_input",
        "_retained_fresh_input",
        "_seeds",
    )

    def __init__(
        self,
        *,
        external_inputs: Iterable[AgentQueuedInputPort] = (),
        pending_local_command_source: Callable[[], ProductContent | None] | None = None,
        seeds: Iterable[ProductContent] = (),
    ) -> None:
        self._external_inputs = tuple(external_inputs)
        if not all(
            isinstance(source, AgentQueuedInputPort) for source in self._external_inputs
        ):
            raise TypeError("external inputs must implement AgentQueuedInputPort")
        if pending_local_command_source is not None and not callable(
            pending_local_command_source
        ):
            raise TypeError("pending_local_command_source must be callable")
        self._pending_local_command_source = pending_local_command_source
        self._seeds: deque[ProductContent] = deque()
        self._extension_steering: deque[AgentQueuedInput] = deque()
        self._extension_follow_ups: deque[AgentQueuedInput] = deque()
        self._extension_prompts: deque[ProductContent] = deque()
        self._next_turn_context: deque[ProductContent] = deque()
        self._pending_local_command: ProductContent | None = None
        self._retained_agent_inputs: deque[AgentQueuedInput] = deque()
        self._retained_external_input: (
            tuple[AgentQueuedInputPort, AgentQueuedInput] | None
        ) = None
        self._retained_fresh_input: ProductContent | None = None
        self._agent_loop_port = _AgentLoopQueuedInputPort(self)
        for seed in seeds:
            self.enqueue_seed(seed)

    @property
    def agent_loop_port(self) -> AgentQueuedInputPort:
        """Return the stable queue port used by the active agent loop."""

        return self._agent_loop_port

    @property
    def has_pending_local_command(self) -> bool:
        return self._pending_local_command is not None

    def defer_local_command(self, content: ProductContent) -> None:
        """Give a local command precedence without consuming queued prompts."""

        _require_content(content, "content")
        if self._pending_local_command is not None:
            raise RuntimeError("a local command is already pending")
        self._pending_local_command = content

    def retain_agent_input(self, queued_input: AgentQueuedInput | None) -> None:
        """Append one whole agent-loop handoff for later separate runs, FIFO."""

        if queued_input is None:
            return
        _require_queued_input(queued_input, "queued_input")
        self._retained_agent_inputs.append(queued_input)

    def enqueue_seed(self, content: ProductContent) -> None:
        _require_content(content, "content")
        self._seeds.append(content)

    def enqueue_extension_steering(self, content: ProductContent) -> None:
        self._extension_steering.append(
            _new_queued_input(content, AgentQueuedInputKind.STEERING)
        )

    def enqueue_extension_follow_up(self, content: ProductContent) -> None:
        self._extension_follow_ups.append(
            _new_queued_input(content, AgentQueuedInputKind.FOLLOW_UP)
        )

    def enqueue_extension_prompt(self, content: ProductContent) -> None:
        _require_content(content, "content")
        self._extension_prompts.append(content)

    def enqueue_next_turn_context(self, content: ProductContent) -> None:
        """Attach extension context once to the next provider-visible prompt."""

        _require_content(content, "content")
        self._next_turn_context.append(content)

    def take_next(self) -> CodingInputSelection | None:
        """Take one input using exact product priority and one-shot clearing.

        Local commands precede retained fresh lines, retained agent handoffs,
        external queues, positional seeds, and extension deliveries, in order.
        """

        self._poll_pending_local_command()
        if self._pending_local_command is not None:
            content = self._pending_local_command
            self._pending_local_command = None
            return CodingInputSelection(content, CodingInputSource.LOCAL_COMMAND)
        if self._retained_fresh_input is not None:
            content = self._retained_fresh_input
            self._retained_fresh_input = None
            return CodingInputSelection(
                content,
                CodingInputSource.RETAINED_FRESH_INPUT,
            )
        if self._retained_agent_inputs:
            retained = self._retained_agent_inputs.popleft()
            return self._queued_selection(
                retained, CodingInputSource.RETAINED_AGENT_INPUT
            )
        external = self._take_external_once()
        if external is not None:
            return self._queued_selection(external, CodingInputSource.EXTERNAL_QUEUE)
        if self._seeds:
            return self._prompt_selection(
                self._seeds.popleft(), CodingInputSource.POSITIONAL_SEED
            )
        if self._extension_steering:
            return self._queued_selection(
                self._extension_steering.popleft(),
                CodingInputSource.EXTENSION_STEERING,
            )
        if self._extension_follow_ups:
            return self._queued_selection(
                self._extension_follow_ups.popleft(),
                CodingInputSource.EXTENSION_FOLLOW_UP,
            )
        if self._extension_prompts:
            return self._prompt_selection(
                self._extension_prompts.popleft(),
                CodingInputSource.EXTENSION_PROMPT,
            )
        return None

    def take_next_for_agent_loop(self) -> AgentQueuedInput | None:
        """Take one active-run continuation without overtaking local policy."""

        self._poll_pending_local_command()
        if self._pending_local_command is not None:
            return None
        if self._retained_fresh_input is not None:
            return None
        external = self._take_external_once()
        if external is not None:
            return external
        if self._seeds:
            return None
        if self._extension_steering:
            return self._extension_steering.popleft()
        if self._extension_follow_ups:
            return self._extension_follow_ups.popleft()
        return None

    def classify_external_wake(
        self,
        source: AgentQueuedInputPort,
        line: str,
    ) -> CodingInputSelection | None:
        """Classify one line returned after a registered external source woke input.

        A source returning ``None`` identifies an ordinary freshly typed line.
        A queued DTO whose content does not describe the exact line is retained,
        leaving the just-read line fresh rather than losing either input.
        EOF has no fresh input to preserve, so a valid DTO is selected directly.
        A local command accepted during the blocking read keeps product priority;
        the already classified queued input is retained for the next selection.
        """

        if not any(registered is source for registered in self._external_inputs):
            raise ValueError("external wake source is not registered")
        if type(line) is not str:
            raise TypeError("line must be an exact str")
        self._require_no_retained_wake_input()
        queued_input = source.take_next()
        if queued_input is not None:
            _require_queued_input(queued_input, "external wake queued input")
        matches_line = queued_input is not None and line == _line_framing(
            queued_input.content.value
        )
        self._poll_pending_local_command()
        if self._pending_local_command is None:
            if queued_input is None:
                return None
            if not line or matches_line:
                return self._queued_selection(
                    queued_input,
                    CodingInputSource.EXTERNAL_QUEUE,
                )
            self._retain_external_input(source, queued_input)
            return None
        if not matches_line and line:
            self._retain_fresh_line(line)
        if queued_input is not None:
            self._retain_external_input(source, queued_input)
        content = self._pending_local_command
        self._pending_local_command = None
        return CodingInputSelection(content, CodingInputSource.LOCAL_COMMAND)

    def clear_extension_inputs(self) -> None:
        """Clear session-bound extension delivery without touching other owners."""

        self._extension_steering.clear()
        self._extension_follow_ups.clear()
        self._extension_prompts.clear()
        self._next_turn_context.clear()

    def take_next_turn_context(self) -> tuple[ProductContent, ...]:
        """Take request-only extension context for the next accepted run."""

        context = tuple(self._next_turn_context)
        self._next_turn_context.clear()
        return context

    def _take_external_once(self) -> AgentQueuedInput | None:
        retained = self._retained_external_input
        if retained is not None:
            self._retained_external_input = None
            source, queued_input = retained
            if not any(registered is source for registered in self._external_inputs):
                raise RuntimeError("retained external wake source is not registered")
            return queued_input
        for source in self._external_inputs:
            polled_input = source.take_next()
            if polled_input is None:
                continue
            _require_queued_input(polled_input, "external queued input")
            return polled_input
        return None

    def _poll_pending_local_command(self) -> None:
        if (
            self._pending_local_command is not None
            or self._pending_local_command_source is None
        ):
            return
        content = self._pending_local_command_source()
        if content is not None:
            _require_content(content, "pending local command")
            self._pending_local_command = content

    def _retain_external_input(
        self,
        source: AgentQueuedInputPort,
        queued_input: AgentQueuedInput,
    ) -> None:
        if self._retained_external_input is not None:
            raise RuntimeError("an external wake input is already retained")
        self._retained_external_input = (source, queued_input)

    def _retain_fresh_line(self, line: str) -> None:
        if self._retained_fresh_input is not None:
            raise RuntimeError("a fresh wake input is already retained")
        self._retained_fresh_input = ProductContent(line)

    def _require_no_retained_wake_input(self) -> None:
        if self._retained_external_input is not None:
            raise RuntimeError("an external wake input is already retained")
        if self._retained_fresh_input is not None:
            raise RuntimeError("a fresh wake input is already retained")

    def _queued_selection(
        self,
        queued_input: AgentQueuedInput,
        source: CodingInputSource,
    ) -> CodingInputSelection:
        return CodingInputSelection(
            queued_input.content,
            source,
            queued_input,
        )

    def _prompt_selection(
        self,
        content: ProductContent,
        source: CodingInputSource,
    ) -> CodingInputSelection:
        return CodingInputSelection(content, source)


def _new_queued_input(
    content: ProductContent,
    kind: AgentQueuedInputKind,
) -> AgentQueuedInput:
    _require_content(content, "content")
    if type(kind) is not AgentQueuedInputKind:
        raise TypeError("kind must be an exact AgentQueuedInputKind")
    return AgentQueuedInput(content, kind)


def _line_framing(content: str) -> str:
    return content if content.endswith("\n") else f"{content}\n"


def _require_content(content: object, field_name: str) -> None:
    if type(content) is not ProductContent:
        raise TypeError(f"{field_name} must be an exact ProductContent")


def _require_queued_input(queued_input: object, field_name: str) -> None:
    if type(queued_input) is not AgentQueuedInput:
        raise TypeError(f"{field_name} must be an exact AgentQueuedInput")
    _require_content(queued_input.content, f"{field_name}.content")
    if type(queued_input.kind) is not AgentQueuedInputKind:
        raise TypeError(f"{field_name}.kind must be an exact AgentQueuedInputKind")

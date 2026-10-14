"""Transient, session-thread-owned calls under one admitted composite parent."""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from time import monotonic
from typing import Final, Protocol, runtime_checkable

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.nested_record import bounded_arguments, finite_duration
from pipy_harness.native.agent.nested_status import NestedCallStatus as NestedCallStatus
from pipy_harness.native.agent.request import (
    validate_agent_tool_call,
    validate_agent_tool_result_message,
)
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
    ToolInterruptWaiter,
    cancelled_tool_outcome,
)
from pipy_harness.native.tools.base import (
    NESTED_CALLS_UNAVAILABLE,
    NestedToolCalls,
    NestedToolResult,
    ToolDefinition,
    ToolInfo,
    tool_info,
)


def tool_error_text(tool_name: str, text: str) -> str:
    """The error a nested caller sees for a failed call (Pi ``toScriptValue``):
    the tool's error text, or ``Tool "<name>" failed`` when it has none."""

    return text or f'Tool "{tool_name}" failed'


@dataclass(frozen=True, slots=True)
class NestedToolCallOutcome:
    nested_call: AgentToolCall
    result: AgentToolResultMessage
    status: NestedCallStatus
    interruption: ToolExecutionInterruption | None = None
    duration_seconds: float = 0.0
    arguments_bytes: int | None = None

    def __post_init__(self) -> None:
        if not finite_duration(self.duration_seconds):
            raise ValueError("nested duration must be finite and nonnegative")
        validate_agent_tool_call(self.nested_call)
        validate_agent_tool_result_message(self.result)
        AgentToolResultMessage.__post_init__(self.result)
        if (
            self.result.provider_correlation_id
            != self.nested_call.provider_correlation_id
            or self.result.tool_name != self.nested_call.tool_name
        ):
            raise ValueError("nested result must match its call")
        if type(self.status) is not NestedCallStatus:
            raise TypeError("status must be NestedCallStatus")
        if (
            self.interruption is not None
            and type(self.interruption) is not ToolExecutionInterruption
        ):
            raise TypeError("interruption must be ToolExecutionInterruption or None")
        if self.status is NestedCallStatus.INTERRUPTED:
            if self.interruption not in {
                ToolExecutionInterruption.OPERATOR_ABORT,
                ToolExecutionInterruption.LOCAL_COMMAND,
            }:
                raise ValueError("interrupted outcome requires an interruption")
        elif self.interruption is not None:
            raise ValueError("only interrupted outcomes carry interruption")


class NestedCallableTools(Protocol):
    """One parent's callable snapshot: names bound to the executor that runs them.

    The names, the registry behind them and the executor belong to one
    capability value and never change for the life of the parent.
    """

    def callable_names(self) -> frozenset[str]: ...

    def callable_tools(self) -> tuple[ToolDefinition, ...]:
        """The definitions of the callable names, in registration order."""
        ...

    def execute(
        self,
        call: AgentToolCall,
        *,
        output_sink: Callable[[str], None] | None = None,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
        nested_tools: NestedToolCalls | None = None,
    ) -> ToolExecutionOutcome: ...


class NestedToolEligibility(Protocol):
    """Take a callable snapshot from the live capability value."""

    def callable_snapshot(self) -> NestedCallableTools: ...


@runtime_checkable
class CompositeToolEligibility(Protocol):
    """Optional frozen product identity guard; separate from tool capabilities."""

    def composite_enabled(self) -> bool: ...


class AgentCompositeToolRunner(Protocol):
    """Optional internal port; invoked only after parent admission and hooks.

    ``records_always`` says whether the parent's result carries the record
    of its nested calls also when it made none. A runner without the
    attribute counts as ``True``. ``reaches_itself`` says whether the
    parent's own tool is among the tools its nested calls reach; without the
    attribute it is not.
    """

    def execute(
        self,
        call: AgentToolCall,
        service: NestedToolCallService,
        tool_waiter: ToolInterruptWaiter | None,
        /,
    ) -> ToolExecutionOutcome: ...


@dataclass(frozen=True, slots=True, eq=False)
class PendingNestedCall:
    """A nested call that passed its admission, hooks and readiness wait.

    ``execute(wait_for_interrupt=, nested_tools=, output_sink=)`` runs the
    tool and may be
    called on any thread; ``finish(execution)`` settles what it returned on
    the session thread, with the ``tool_result`` hooks, and gives the
    outcome. The pipeline gives one of these instead of an outcome for a
    call that is to run.
    """

    call: AgentToolCall
    execute: Callable[..., ToolExecutionOutcome]
    finish: Callable[[ToolExecutionOutcome], NestedToolCallOutcome]
    # Session thread: publish one chunk of the tool's live output.
    update: Callable[[str], None] | None = None


NestedSettle = Callable[[AgentToolCall], "NestedToolCallOutcome | PendingNestedCall"]

# How often a waiting thread looks whether it was stopped.
_POLL_SECONDS: Final = 0.05


class _Request:
    """A call asked for on a thread that is not the session thread."""

    __slots__ = (
        "arguments_json",
        "caller_id",
        "on_update",
        "ready",
        "result",
        "signal",
        "tool_name",
    )

    def __init__(
        self,
        caller_id: str,
        tool_name: str,
        arguments_json: str,
        signal: threading.Event | None = None,
        on_update: Callable[[str], None] | None = None,
    ) -> None:
        self.caller_id = caller_id
        self.tool_name = tool_name
        self.arguments_json = arguments_json
        self.signal = signal
        self.on_update = on_update
        self.ready = threading.Event()
        self.result = unavailable_result(caller_id)

    def answer(self, result: NestedToolResult) -> None:
        self.result = result
        self.ready.set()


class _Running:
    """A nested call running on a thread of its own."""

    __slots__ = ("ended", "pending", "reason", "request", "started", "stop")

    def __init__(
        self, pending: PendingNestedCall, started: float, request: _Request
    ) -> None:
        self.pending = pending
        self.started = started
        self.request = request
        self.stop = threading.Event()
        self.reason = ToolExecutionInterruption.CALLER_STOP
        # Set by the call's thread when its tool returned, before the
        # session thread settles it.
        self.ended = threading.Event()

    def end(self, reason: ToolExecutionInterruption) -> None:
        self.reason = reason
        self.stop.set()

    def wait(
        self, done_event: threading.Event, cancel_event: threading.Event, /
    ) -> ToolExecutionInterruption:
        """The waiter of the call's executor, on the call's thread."""
        signal = self.request.signal
        while not done_event.wait(_POLL_SECONDS):
            if self.stop.is_set():
                return self.reason
            if signal is not None and signal.is_set():
                return ToolExecutionInterruption.CALLER_STOP
        return ToolExecutionInterruption.SETTLED


class _Update:
    """One chunk of a running call's live output, for the session thread."""

    __slots__ = ("call_id", "chunk")

    def __init__(self, call_id: str, chunk: str) -> None:
        self.call_id = call_id
        self.chunk = chunk


class _Finished:
    """The end of a call's thread: what its execution returned or raised."""

    __slots__ = ("call_id", "execution")

    def __init__(
        self, call_id: str, execution: ToolExecutionOutcome | BaseException
    ) -> None:
        self.call_id = call_id
        self.execution = execution


def _until_ended(
    waiter: ToolInterruptWaiter, ends: tuple[threading.Event, ...]
) -> ToolInterruptWaiter:
    """``waiter``, ended also by any of ``ends``: the wait then gives
    ``CALLER_STOP``."""

    def wait(
        done_event: threading.Event, cancel_event: threading.Event, /
    ) -> ToolExecutionInterruption:
        wake = threading.Event()
        stop = threading.Event()

        def watch() -> None:
            while not (done_event.is_set() or any(end.is_set() for end in ends)):
                if stop.wait(_POLL_SECONDS):
                    return
            wake.set()

        threading.Thread(target=watch, name="pipy-nested-ready", daemon=True).start()
        try:
            interruption = waiter(wake, cancel_event)
        finally:
            stop.set()
        if interruption is not ToolExecutionInterruption.SETTLED:
            return interruption
        if done_event.is_set():
            return ToolExecutionInterruption.SETTLED
        return ToolExecutionInterruption.CALLER_STOP

    return wait


def _until_done(
    done_event: threading.Event, cancel_event: threading.Event, /
) -> ToolExecutionInterruption:
    """Wait for the tool alone."""
    done_event.wait()
    return ToolExecutionInterruption.SETTLED


def unavailable_result(caller_id: str = "") -> NestedToolResult:
    """What a call gets where no nested call can run (Pi's text and its id
    ``<calling id>/0``)."""

    return NestedToolResult(
        NESTED_CALLS_UNAVAILABLE,
        is_error=True,
        tool_call_id=f"{caller_id}/0" if caller_id else "",
    )


def nested_tool_result(outcome: NestedToolCallOutcome) -> NestedToolResult:
    """What a calling tool receives for one nested outcome."""

    result = outcome.result
    call = outcome.nested_call
    try:
        arguments = json.loads(call.arguments_json.value)
    except ValueError:
        arguments = {}
    return NestedToolResult(
        result.content.value,
        is_error=result.is_error,
        details=result.details,
        structured_content=result.structured_content,
        blocks=result.blocks,
        tool_call_id=call.provider_correlation_id,
        arguments=arguments if isinstance(arguments, dict) else {},
        duration_ms=None
        if outcome.status is NestedCallStatus.REFUSED
        else outcome.duration_seconds * 1000,
    )


class _CallerTools:
    """``NestedToolCalls`` of one calling tool call (Pi ``ctx.executeTool``).

    Its calls get the ids ``<caller id>/<n>``. May be used from any thread.
    """

    def __init__(self, service: NestedToolCallService, caller_id: str) -> None:
        self._service = service
        self._caller_id = caller_id

    def tools(self) -> tuple[ToolInfo, ...]:
        return self._service._tool_infos

    def execute(
        self,
        tool_name: str,
        arguments: object,
        /,
        *,
        signal: threading.Event | None = None,
        on_update: Callable[[str], None] | None = None,
    ) -> NestedToolResult:
        name = str(tool_name)
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            return self._refused(f"Tool {name} arguments must be an object")
        try:
            arguments_json = json.dumps(dict(arguments))
        except (TypeError, ValueError) as error:
            return self._refused(f"Tool {name} arguments must be JSON: {error}")
        return self._service._request(
            self._caller_id, name, arguments_json, signal, on_update
        )

    def _refused(self, text: str) -> NestedToolResult:
        """A call refused before it got an id of its own (Pi ``<id>/0``)."""
        return replace(unavailable_result(self._caller_id), content=text)


class NestedToolCallService:
    """The nested calls under one admitted parent, at every depth.

    The records, the ids and the pipeline belong to the session thread: only
    it begins and settles calls. A tool running on another thread asks for a
    call through ``nested_tools(caller_id)``; the request waits in a queue
    until the session thread, waiting in ``serve`` for whatever it waits for,
    takes it, begins the call and runs the tool on a thread of its own. Calls
    from several threads therefore run at the same time, as Pi's do. The lock
    guards the queue, the wake event and the callers that may still call.

    Records are transient observations, capped at 256 calls, 8 KiB arguments
    per call and 32 KiB total. Oversized arguments are replaced by their byte
    count; excess calls are dropped. Wrong-thread refusal reads no mutable
    fields and retains no evidence.
    """

    def __init__(
        self,
        parent: AgentToolCall,
        eligible_names: frozenset[str],
        settle: NestedSettle,
        error_result: Callable[[AgentToolCall, str], AgentToolResultMessage],
        settle_with_waiter: Callable[
            [AgentToolCall, ToolInterruptWaiter],
            NestedToolCallOutcome | PendingNestedCall,
        ]
        | None = None,
        *,
        callable_tools: tuple[ToolDefinition, ...] = (),
        session_waiter: ToolInterruptWaiter | None = None,
    ) -> None:
        self._parent_id = parent.provider_correlation_id
        self._thread = threading.current_thread()
        # The loop leaves the parent's own tool out unless its runner
        # ``reaches_itself``.
        self._eligible = eligible_names
        self._tools = tuple(
            tool for tool in callable_tools if tool.name in self._eligible
        )
        self._tool_infos = tuple(tool_info(tool) for tool in self._tools)
        self._output_schema_names = frozenset(
            tool.name for tool in self._tools if tool.output_schema is not None
        )
        self._settle = settle
        self._settle_with_waiter = settle_with_waiter
        self._error_result = error_result
        self._session_waiter = session_waiter
        self._closed = False
        # Set while the pipeline runs on the session thread (hooks, waits):
        # a call it makes from there is refused, not run inside it.
        self._in_pipeline = False
        self._next: dict[str, int] = {}
        self._records: list[NestedToolCallOutcome] = []
        self._argument_bytes = 0
        self._complete = True
        self._failure: BaseException | None = None
        self._interruption: ToolExecutionInterruption | None = None
        # Session thread: the calls running on threads of their own.
        self._running: dict[str, _Running] = {}
        # Session thread: the calls whose tool runs, with when each started;
        # their ``update`` publishes the tool's live output.
        self._live: dict[str, tuple[PendingNestedCall, float]] = {}
        # Session thread: the calls ``_fail`` kept as unfinished.
        self._failed_ids: set[str] = set()
        # Guarded by the lock: requests, finished calls and live output for
        # the session thread, and the callers whose requests are taken: a
        # caller's tool always runs under ``serve``.
        self._lock = threading.Lock()
        self._jobs: deque[_Request | _Finished | _Update] = deque()
        self._wake = threading.Event()
        self._callers: set[str] = set()

    def _require_thread(self) -> None:
        if threading.current_thread() is not self._thread:
            raise RuntimeError("nested service belongs to the session thread")

    def close(self) -> None:
        """Refuse every later call, and answer the requests nobody took."""
        self._require_thread()
        self._closed = True
        with self._lock:
            self._callers.clear()
            waiting = [job for job in self._jobs if isinstance(job, _Request)]
            self._jobs = deque(job for job in self._jobs if isinstance(job, _Finished))
        for request in waiting:
            request.answer(unavailable_result(request.caller_id))

    def records(self) -> tuple[NestedToolCallOutcome, ...]:
        self._require_thread()
        return tuple(self._records)

    @property
    def interruption(self) -> ToolExecutionInterruption | None:
        """First child interruption, independent of bounded evidence retention."""
        self._require_thread()
        return self._interruption

    def raise_pipeline_failure(self) -> None:
        """Preserve canonical callback/executor failures across the runner boundary."""
        self._require_thread()
        if self._failure is not None:
            raise self._failure

    def eligible_names(self) -> frozenset[str]:
        self._require_thread()
        return self._eligible

    def callable_tools(self) -> tuple[ToolDefinition, ...]:
        """The definitions of the tools this parent may call, in registration
        order: what a caller lists, searches and describes (Pi ``ctx.tools``)."""
        self._require_thread()
        return self._tools

    def declares_output_schema(self, tool_name: str) -> bool:
        """Whether a caller receives this tool's structured content (Pi
        ``toScriptValue``: ``tool.outputSchema`` in the parent's snapshot)."""
        self._require_thread()
        return tool_name in self._output_schema_names

    def nested_tools(self, caller_id: str, /) -> NestedToolCalls:
        """What the tool call ``caller_id`` runs other tools with. Its tool
        must run under ``serve``, which takes the calls asked for on other
        threads; ``end_caller`` ends what the returned object can do."""
        self._require_thread()
        with self._lock:
            if not self._closed:
                self._callers.add(caller_id)
        return _CallerTools(self, caller_id)

    def end_caller(self, caller_id: str, /) -> None:
        """The call ``caller_id`` ended: refuse its waiting and later calls."""
        self._require_thread()
        with self._lock:
            self._callers.discard(caller_id)
            waiting = [
                job
                for job in self._jobs
                if isinstance(job, _Request) and job.caller_id == caller_id
            ]
            for request in waiting:
                self._jobs.remove(request)
        for request in waiting:
            request.answer(unavailable_result(caller_id))

    def _request(
        self,
        caller_id: str,
        tool_name: str,
        arguments_json: str,
        signal: threading.Event | None = None,
        on_update: Callable[[str], None] | None = None,
    ) -> NestedToolResult:
        """Any thread: run one call for ``caller_id`` and wait for it."""
        if threading.current_thread() is self._thread:
            with self._lock:
                open_caller = caller_id in self._callers
            if not open_caller:
                return unavailable_result(caller_id)
            return nested_tool_result(
                self.call(
                    tool_name,
                    arguments_json,
                    caller_id=caller_id,
                    unknown_tool_error=f"Tool {tool_name} not found",
                    signal=signal,
                    on_update=on_update,
                )
            )
        request = _Request(caller_id, tool_name, arguments_json, signal, on_update)
        with self._lock:
            if caller_id not in self._callers:
                return request.result
            self._jobs.append(request)
            self._wake.set()
        request.ready.wait()
        return request.result

    def call(
        self,
        tool_name: str,
        arguments_json: str,
        /,
        *,
        waiter: ToolInterruptWaiter | None = None,
        unknown_tool_error: str | None = None,
        caller_id: str | None = None,
        signal: threading.Event | None = None,
        on_update: Callable[[str], None] | None = None,
    ) -> NestedToolCallOutcome:
        """Run one call on the session thread and wait for it.

        ``caller_id`` is the call that asks, by default the parent. A caller
        that lets its user name any tool, as ``ctx.execute_tool`` does,
        passes ``unknown_tool_error``: a name the parent may not call is then
        refused with that text and kept in the record (Pi records every call
        ``executeTool`` was asked for). Without it such a refusal leaves no
        record. While the tool runs, the calls it asks for on other threads
        are served. ``signal`` cancels this call alone; ``on_update``
        receives the chunks of its live output.
        """
        if threading.current_thread() is not self._thread:
            call = AgentToolCall(
                f"{self._parent_id}/0", tool_name, ProductContent(arguments_json)
            )
            # Build locally: the product error factory may itself access session state.
            result = AgentToolResultMessage(
                tool_request_id="pipy-tool-thread-refused",
                tool_name=tool_name,
                content=ProductContent("nested call refused: wrong thread"),
                is_error=True,
                provider_correlation_id=call.provider_correlation_id,
            )
            return NestedToolCallOutcome(call, result, NestedCallStatus.REFUSED)
        # The tool always runs under ``serve``, so the calls and the live
        # output of other threads reach the session thread. A ``signal``
        # ends the readiness wait and the tool's wait.
        base = waiter or self._session_waiter or _until_done
        if signal is not None:
            base = _until_ended(base, (signal,))
        began = self._begin(
            caller_id or self._parent_id,
            tool_name,
            arguments_json,
            unknown_tool_error,
            base
            if waiter is not None
            or (signal is not None and self._settle_with_waiter is not None)
            else None,
        )
        if isinstance(began, NestedToolCallOutcome):
            return began
        pending, started = began
        if signal is not None and signal.is_set():
            # Cancelled while it was admitted: it does not run.
            return self._finish(
                pending,
                started,
                cancelled_tool_outcome(
                    self._error_result,
                    pending.call,
                    ToolExecutionInterruption.CALLER_STOP,
                ),
            )
        child_id = pending.call.provider_correlation_id
        tools = self.nested_tools(child_id)
        sink = self._output_sink(child_id, pending, on_update)
        self._live[child_id] = (pending, started)
        serve_base = base

        def served(
            done_event: threading.Event, cancel_event: threading.Event, /
        ) -> ToolExecutionInterruption:
            return self.serve(serve_base, done_event, cancel_event)

        try:
            try:
                execution = pending.execute(
                    wait_for_interrupt=served, nested_tools=tools, output_sink=sink
                )
            except BaseException as exc:
                self._fail(pending.call, started, exc)
                raise
            finally:
                self._live.pop(child_id, None)
            return self._finish(pending, started, execution)
        finally:
            self.end_caller(child_id)

    def _output_sink(
        self,
        call_id: str,
        pending: PendingNestedCall,
        on_update: Callable[[str], None] | None,
    ) -> Callable[[str], None]:
        """Where the tool of ``call_id`` writes its live output: the
        caller's ``on_update`` on the writing thread, and the pipeline's
        update on the session thread while the tool runs."""
        publish = pending.update

        def sink(chunk: str) -> None:
            if on_update is not None:
                try:
                    on_update(chunk)
                except Exception:  # noqa: BLE001 - the caller's callback does not end the call
                    pass
            if publish is None:
                return
            if threading.current_thread() is self._thread:
                self._publish(call_id, chunk)
                return
            with self._lock:
                self._jobs.append(_Update(call_id, chunk))
                self._wake.set()

        return sink

    def _publish(self, call_id: str, chunk: str) -> None:
        """Session thread: publish a chunk of a live call's output; dropped
        once the call ended. A failure to publish is the pipeline's."""
        live = self._live.get(call_id)
        if live is None:
            return
        pending, started = live
        if pending.update is None:
            return
        try:
            pending.update(chunk)
        except BaseException as exc:
            self._fail(pending.call, started, exc)
            raise

    def _begin(
        self,
        caller_id: str,
        tool_name: str,
        arguments_json: str,
        unknown_tool_error: str | None,
        waiter: ToolInterruptWaiter | None,
    ) -> NestedToolCallOutcome | tuple[PendingNestedCall, float]:
        """Session thread: give the call its id and run the pipeline up to
        the tool. A call that does not run is settled and kept here."""
        number = self._next.get(caller_id, 0) + 1
        self._next[caller_id] = number
        call = AgentToolCall(
            f"{caller_id}/{number}", tool_name, ProductContent(arguments_json)
        )
        if self._closed or self._in_pipeline or tool_name not in self._eligible:
            return self._refuse(
                call,
                None if self._closed or self._in_pipeline else unknown_tool_error,
            )
        started = monotonic()
        self._in_pipeline = True
        try:
            if waiter is not None:
                if self._settle_with_waiter is None:
                    raise RuntimeError("nested waiter override is unavailable")
                began = self._settle_with_waiter(call, waiter)
            else:
                began = self._settle(call)
        except BaseException as exc:
            self._fail(call, started, exc)
            raise
        finally:
            self._in_pipeline = False
        if isinstance(began, PendingNestedCall):
            return began, started
        return self._keep(began)

    def _finish(
        self,
        pending: PendingNestedCall,
        started: float,
        execution: ToolExecutionOutcome,
    ) -> NestedToolCallOutcome:
        """Session thread: settle what the tool returned and keep it."""
        self._in_pipeline = True
        try:
            outcome = pending.finish(execution)
        except BaseException as exc:
            self._fail(pending.call, started, exc)
            raise
        finally:
            self._in_pipeline = False
        return self._keep(outcome)

    def _fail(
        self, call: AgentToolCall, started: float, failure: BaseException
    ) -> None:
        """The pipeline raised: close, and keep the call as unfinished."""
        if self._failure is None:
            self._failure = failure
        self._closed = True
        call_id = call.provider_correlation_id
        if call_id in self._failed_ids:
            return  # kept as unfinished already, when it failed to publish
        self._failed_ids.add(call_id)
        try:
            self._retain(
                NestedToolCallOutcome(
                    call,
                    self._error_result(call, "nested pipeline did not finish"),
                    NestedCallStatus.UNFINISHED,
                    duration_seconds=monotonic() - started,
                )
            )
        except BaseException:  # noqa: BLE001 - preserve canonical failure even during evidence capture
            # Secondary evidence failures must not replace the original.
            self._complete = False

    def _keep(self, outcome: NestedToolCallOutcome) -> NestedToolCallOutcome:
        if outcome.status is NestedCallStatus.INTERRUPTED:
            if self._interruption is None:
                self._interruption = outcome.interruption
            self.close()
        self._retain(outcome)
        return outcome

    def serve(
        self,
        waiter: ToolInterruptWaiter,
        done_event: threading.Event,
        cancel_event: threading.Event,
        /,
    ) -> ToolExecutionInterruption:
        """Session thread: wait for a running tool as ``waiter`` would, and
        meanwhile run the calls asked for on other threads.

        Returns what ended the wait. When it returns, the calls it started
        have ended: an interruption of the session or of one of them ends the
        others with it, and the end of the tool it waits for cancels them.
        """
        self._require_thread()
        started: set[str] = set()
        watching = threading.Event()
        threading.Thread(
            target=self._watch,
            args=(done_event, watching),
            name="pipy-nested-wake",
            daemon=True,
        ).start()
        try:
            while True:
                interruption = waiter(self._wake, cancel_event)
                if interruption is not ToolExecutionInterruption.SETTLED:
                    self._end_running(started, interruption)
                    return interruption
                ending = done_event.is_set()
                interrupted = self._take_jobs(
                    started, accept=not ending, waiter=waiter, ended=done_event
                )
                if interrupted is not None:
                    self._end_running(started, interrupted)
                    return interrupted
                if ending:
                    self._end_running(started, ToolExecutionInterruption.CALLER_STOP)
                    return ToolExecutionInterruption.SETTLED
                if done_event.is_set():
                    # It ended while the jobs ran, after the watcher's wake
                    # was taken with them.
                    self._wake.set()
        except BaseException:
            self._abandon(started)
            raise
        finally:
            watching.set()

    def _watch(self, done_event: threading.Event, watching: threading.Event) -> None:
        """Wake the session thread when the tool it waits for ended."""
        while not done_event.wait(_POLL_SECONDS):
            if watching.is_set():
                return
        self._wake.set()

    def _take_jobs(
        self,
        started: set[str],
        *,
        accept: bool,
        waiter: ToolInterruptWaiter | None = None,
        ended: threading.Event | None = None,
    ) -> ToolExecutionInterruption | None:
        """Session thread: begin the requested calls (or refuse them, unless
        ``accept``) and settle the finished ones. Returns the interruption of
        a call, if one was interrupted. ``waiter`` and ``ended`` are what
        ``serve`` waits with and for: a call's readiness wait ends with them."""
        with self._lock:
            jobs = list(self._jobs)
            self._jobs.clear()
            self._wake.clear()
        interrupted: ToolExecutionInterruption | None = None
        for index, job in enumerate(jobs):
            try:
                if isinstance(job, _Update):
                    self._publish(job.call_id, job.chunk)
                    outcome = None
                elif isinstance(job, _Finished):
                    outcome = self._settle_finished(job)
                elif (
                    accept
                    and not self._closed
                    and waiter is not None
                    and ended is not None
                ):
                    outcome = self._start(job, started, waiter, ended)
                else:
                    job.answer(unavailable_result(job.caller_id))
                    outcome = None
            except BaseException:
                # The pipeline failed: nobody takes the remaining requests.
                for rest in jobs[index:]:
                    if isinstance(rest, _Request):
                        rest.answer(unavailable_result(rest.caller_id))
                raise
            if (
                outcome is not None
                and outcome.interruption is not None
                and interrupted is None
            ):
                interrupted = outcome.interruption
        return interrupted

    def _start(
        self,
        request: _Request,
        started: set[str],
        waiter: ToolInterruptWaiter,
        ended: threading.Event,
    ) -> NestedToolCallOutcome | None:
        """Begin a requested call; run its tool on a thread of its own.

        The readiness wait of the call ends like ``serve``'s own wait: with
        an interruption, or a stop of the script it serves, and with the end
        of the tool ``serve`` waits for (``ended``) or of the call's own
        caller, which then cancel the call as the end of its caller does."""
        caller = self._running.get(request.caller_id)
        caller_ends = (ended,) if caller is None else (ended, caller.ended)
        # The call's own signal ends it too, once it has an id.
        ends = caller_ends if request.signal is None else (*caller_ends, request.signal)
        with self._lock:
            open_caller = request.caller_id in self._callers
        if not open_caller or any(end.is_set() for end in caller_ends):
            request.answer(unavailable_result(request.caller_id))
            return None
        try:
            began = self._begin(
                request.caller_id,
                request.tool_name,
                request.arguments_json,
                f"Tool {request.tool_name} not found",
                _until_ended(waiter, ends)
                if self._settle_with_waiter is not None
                else None,
            )
            if not isinstance(began, NestedToolCallOutcome) and any(
                end.is_set() for end in ends
            ):
                # Its caller ended while it was admitted: it does not run.
                pending, at = began
                began = self._finish(
                    pending,
                    at,
                    cancelled_tool_outcome(
                        self._error_result,
                        pending.call,
                        ToolExecutionInterruption.CALLER_STOP,
                    ),
                )
        except BaseException:
            request.answer(unavailable_result(request.caller_id))
            raise
        if isinstance(began, NestedToolCallOutcome):
            request.answer(nested_tool_result(began))
            return began
        pending, at = began
        call_id = pending.call.provider_correlation_id
        running = _Running(pending, at, request)
        self._running[call_id] = running
        started.add(call_id)
        tools = self.nested_tools(call_id)
        sink = self._output_sink(call_id, pending, request.on_update)
        self._live[call_id] = (pending, at)

        def run() -> None:
            execution: ToolExecutionOutcome | BaseException
            try:
                execution = pending.execute(
                    wait_for_interrupt=running.wait,
                    nested_tools=tools,
                    output_sink=sink,
                )
            except BaseException as exc:  # noqa: BLE001 - handed to the session thread
                execution = exc
            running.ended.set()
            with self._lock:
                self._jobs.append(_Finished(call_id, execution))
                self._wake.set()

        threading.Thread(target=run, name="pipy-nested-call", daemon=True).start()
        return None

    def _settle_finished(self, finished: _Finished) -> NestedToolCallOutcome | None:
        """Settle a call whose thread ended and answer its caller."""
        running = self._running.pop(finished.call_id, None)
        self._live.pop(finished.call_id, None)
        self.end_caller(finished.call_id)
        if running is None:  # abandoned when the pipeline failed
            return None
        try:
            if isinstance(finished.execution, BaseException):
                self._fail(running.pending.call, running.started, finished.execution)
                raise finished.execution
            outcome = self._finish(running.pending, running.started, finished.execution)
        except BaseException:
            running.request.answer(unavailable_result(running.request.caller_id))
            raise
        running.request.answer(nested_tool_result(outcome))
        return outcome

    def _end_running(
        self, started: set[str], reason: ToolExecutionInterruption
    ) -> None:
        """End the calls ``started`` that still run, and settle them."""
        for call_id in started:
            running = self._running.get(call_id)
            if running is not None:
                running.end(reason)
        # Each thread ends within its executor's bound once stopped.
        while any(call_id in self._running for call_id in started):
            self._wake.wait()
            self._take_jobs(started, accept=False)

    def _abandon(self, started: set[str]) -> None:
        """The pipeline failed: stop the calls ``started`` without settling
        them, and answer everyone waiting."""
        for call_id in started:
            running = self._running.pop(call_id, None)
            self._live.pop(call_id, None)
            if running is not None:
                running.end(ToolExecutionInterruption.CALLER_STOP)
                running.request.answer(unavailable_result(running.request.caller_id))
        self.close()

    def _refuse(
        self, call: AgentToolCall, recorded_error: str | None
    ) -> NestedToolCallOutcome:
        refused = NestedToolCallOutcome(
            call,
            self._error_result(call, recorded_error or "nested call refused"),
            NestedCallStatus.REFUSED,
        )
        if recorded_error is not None:
            self._retain(refused)
        return refused

    def _retain(self, outcome: NestedToolCallOutcome) -> None:
        if len(self._records) >= 256:
            self._complete = False
            return
        raw = outcome.nested_call.arguments_json.value
        size = len(raw.encode("utf-8", errors="surrogatepass"))
        arguments = bounded_arguments(raw)
        if arguments is None or self._argument_bytes + size > 32768:
            self._complete = False
            call = replace(outcome.nested_call, arguments_json=ProductContent("{}"))
            omitted = size
        else:
            self._argument_bytes += size
            call = outcome.nested_call
            omitted = None
        error = (
            tool_error_text(outcome.nested_call.tool_name, outcome.result.content.value)
            if outcome.result.is_error
            else ""
        )
        if len(error) > 500:
            self._complete = False
        # Retain no nested result output, details or structured content, only
        # the bounded error field.
        self._records.append(
            replace(
                outcome,
                nested_call=call,
                arguments_bytes=omitted,
                result=replace(
                    outcome.result,
                    content=ProductContent(error[:500]),
                    details=None,
                    structured_content=None,
                    blocks=(),
                ),
            )
        )

    def durable_record(self) -> dict[str, object]:
        """Fresh independent snapshot; canonical state never leaves this thread."""
        self._require_thread()
        calls: list[dict[str, object]] = []
        for outcome in self._records:
            status = (
                "unfinished"
                if outcome.status is NestedCallStatus.UNFINISHED
                else "cancelled"
                if outcome.status
                in {NestedCallStatus.CANCELLED, NestedCallStatus.INTERRUPTED}
                else "error"
                if outcome.result.is_error
                else "ok"
            )
            row: dict[str, object] = {
                "id": outcome.nested_call.provider_correlation_id,
                "name": outcome.nested_call.tool_name,
                "status": status,
                "durationMs": outcome.duration_seconds * 1000,
            }
            if outcome.arguments_bytes is None:
                row["arguments"] = json.loads(outcome.nested_call.arguments_json.value)
            else:
                row["argumentsBytes"] = outcome.arguments_bytes
            if outcome.result.is_error:
                row["error"] = outcome.result.content.value
            calls.append(row)
        return {"calls": calls, "complete": self._complete}

    def has_record(self) -> bool:
        """Whether a call was kept or dropped: Pi leaves ``nestedCalls`` off
        a result whose tool called nothing."""
        self._require_thread()
        return bool(self._records) or not self._complete

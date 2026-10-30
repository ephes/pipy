"""Fixed native tool execution with acknowledged intent and committed receipts."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from types import MappingProxyType
from typing import Any, Literal, cast
from uuid import uuid4

from pipy_harness.native.agent.tool_details import NO_TOOL_DETAILS
from pipy_harness.native.agent.tools import ToolExecutionOutcome, ToolExecutor
from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolPort,
)

from .canonical_codec import _schema_lists, decode_canonical, encode_canonical
from .codec import OwnedJson
from .documents import Doc, define_doc
from .execution_support import ExecutionSupport
from .generation import message_entry
from .generation_tasks import TOOL_NAME, TOOL_VERSION, _initial
from .generation_transport import owned_call
from .recovery_catalog import HEADER, RecoveryCatalog
from .scheduler import TaskContext, TaskRuntime, TaskTx
from .tasks import Task, define_task
from .tool_output_values import ending_text, project_tool_output
from .tool_progress import ProgressEnded, RetainedToolProgress
from .tool_progress_values import (
    FailureCategory,
    detail_value,
    initial_progress,
    progress,
)
from .tool_values import (
    _model_declaration,
    checkpoint,
    declaration,
    intent,
    match_result,
    native_tool_output,
    receipt,
    refuse,
)

Tree = dict[str, Any]
RECEIPT_DOC: Doc[Tree] = define_doc(
    "pi.tool.receipt",
    1,
    "task",
    dict,
    checkpoint_when=lambda _value, _ops, _info: False,
)
PROGRESS_DOC: Doc[Tree] = define_doc(
    "pi.tool.progress",
    1,
    "task",
    dict,
    checkpoint_when=lambda value, operations, info: True,
)


class _InvocationFailure(RuntimeError):
    def __init__(self, seconds: float) -> None:
        super().__init__("Durable tool invocation failed")
        self.seconds = seconds


class _ToolDomainFailure(RuntimeError):
    def __init__(
        self, category: FailureCategory, retained: OwnedJson, seconds: float | None
    ) -> None:
        super().__init__("Durable tool failed: " + category)
        self.category, self.retained, self.seconds = category, retained, seconds


@dataclass(frozen=True, slots=True)
class DurableToolContext(ToolContext):
    """Stable idempotency identity for tools with an explicitly admitted protocol."""

    call_key: str = field(kw_only=True)
    _reporter: RetainedToolProgress | None = field(
        default=None, kw_only=True, repr=False
    )

    def report_details(self, value: object) -> None:
        """Wait for committed-prefix details, or raise a fixed refusal/end."""
        if self._reporter is None:
            raise ProgressEnded()
        self._reporter.report_details(value)

    def report_diagnostic(
        self,
        *,
        severity: Literal["info", "warning", "error"],
        message: str,
        code: str | None = None,
    ) -> None:
        """Report an ordered diagnostic; a normal return is not an ACK."""
        if self._reporter is not None:
            self._reporter.report_diagnostic(
                severity=severity, message=message, code=code
            )


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class ToolRegistration:
    port: ToolPort
    implementation: str
    revision: str
    replay: str = "unsafe"


def builtin_registrations() -> Mapping[str, ToolRegistration]:
    from pipy_harness.native.tools.registry import production_tool_registry

    return MappingProxyType(
        {
            name: ToolRegistration(port, "pipy.native." + name, "1")
            for name, port in production_tool_registry().items()
        }
    )


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class _PinnedPort:
    definition: ToolDefinition
    invoke_function: Any
    prepare_function: Any

    def invoke(self, request: Any, context: ToolContext) -> Any:
        began = monotonic()
        try:
            return self.invoke_function(request, context)
        except ToolArgumentError:
            # The shared executor preserves this intentional argument refusal.
            raise
        except Exception:  # noqa: BLE001 - fixed durable cause, no exception-text guessing
            raise _InvocationFailure(monotonic() - began) from None

    def prepare_arguments(self, raw: object) -> object:
        return raw if self.prepare_function is None else self.prepare_function(raw)


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class _Registration:
    port: _PinnedPort
    descriptor: OwnedJson
    supported: bool


def _capture(name: str, raw: ToolRegistration) -> _Registration:
    if type(raw) is not ToolRegistration:
        refuse()
    source = raw.port.definition
    if type(source) is not ToolDefinition or source.name != name:
        refuse()
    schema = cast(
        Tree,
        OwnedJson(
            _schema_lists(decode_canonical(encode_canonical(source.input_schema)))
        ).thaw(),
    )
    # Model-only declaration projection; executable registration stays private.
    definition = ToolDefinition(
        name, source.description, schema, schema_mode=source.schema_mode
    )
    descriptor = declaration(
        {
            "implementation": raw.implementation,
            "revision": raw.revision,
            "schema": schema,
            "schema_mode": source.schema_mode,
            "output_schema": None
            if source.output_schema is None
            else OwnedJson(
                _schema_lists(decode_canonical(encode_canonical(source.output_schema)))
            ).thaw(),
            "execution_mode": source.execution_mode,
            "replay": raw.replay,
        }
    )
    prepare = getattr(raw.port, "prepare_arguments", None)
    if prepare is not None and not callable(prepare):
        refuse()
    return _Registration(
        _PinnedPort(definition, raw.port.invoke, prepare),
        OwnedJson(descriptor),
        source.exposure not in ("hidden", "codemode")
        and source.reaches_tools is None
        and source.prepare_loadout is None
        and source.execution_mode != "sequential",
    )


def _same(current: Tree, previous: Tree) -> None:
    if OwnedJson(current["state"]["checkpoint"]) != OwnedJson(previous):
        refuse()


async def _join_reporter(
    reporter: RetainedToolProgress,
    flusher: asyncio.Task[None],
    watcher: asyncio.Task[None],
) -> None:
    reporter.finish()
    try:
        await asyncio.shield(flusher)
    finally:
        while not flusher.done():
            try:
                await asyncio.shield(flusher)
            except asyncio.CancelledError:
                continue
        if not flusher.cancelled():
            flusher.exception()
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


async def _transport(
    executor: ToolExecutor,
    call: Any,
    arguments: Tree,
    workspace: Path,
    key: str,
    context: TaskContext,
    runtime: TaskRuntime,
    previous: Tree,
    initial: OwnedJson,
) -> tuple[ToolExecutionOutcome, OwnedJson]:
    cancelled = threading.Event()

    async def stop() -> None:
        await context.signal.wait()
        cancelled.set()

    watcher = asyncio.create_task(stop())

    async def commit(snapshot: OwnedJson) -> None:
        async def save(tx: TaskTx, current: Tree) -> None:
            nonlocal snapshot
            _same(current, previous)
            document = await tx.doc(PROGRESS_DOC)
            new = cast(Tree, snapshot.thaw())
            if any(
                document[k] != new[k] for k in ("call", "key", "attempts", "restarts")
            ):
                refuse()
            document.update(new)
            # The guarded document update owns the detached candidate now.
            # The commit Task still owns the transaction through backend
            # completion/adoption; this handoff is not a persistence ACK.
            del snapshot

        await runtime.commit(save)

    reporter = RetainedToolProgress(initial, commit, runtime._reporting_preflight)
    flusher = asyncio.create_task(reporter.flush())
    failure: FailureCategory | None = None
    seconds: float | None = None
    outcome: ToolExecutionOutcome | None = None
    try:
        tool_context = DurableToolContext(
            workspace, cancel_event=cancelled, call_key=key, _reporter=reporter
        )
        try:
            outcome = await owned_call(
                lambda: executor.execute(
                    call,
                    tool_context,
                    wait_for_interrupt=None,
                    arguments=arguments,
                    retained_output=reporter,
                )
            )
        except _InvocationFailure as error:
            failure, seconds = "invocation_exception", error.seconds
        except Exception:  # noqa: BLE001 - returned/normalized result failed after real invoke
            failure = "invalid_result"
    finally:
        await _join_reporter(reporter, flusher, watcher)
    retained, domain_failure = reporter.result()
    if context.signal.cancelled:
        raise ProgressEnded()
    category = domain_failure or failure
    if category is not None:
        raise _ToolDomainFailure(
            category,
            retained,
            seconds if outcome is None else outcome.execution_seconds,
        )
    assert outcome is not None
    if outcome.result.details is NO_TOOL_DETAILS:
        details = detail_value(cast(Tree, retained.thaw())["details"])
        outcome = replace(outcome, result=replace(outcome.result, details=details))
    return outcome, retained


@dataclass(frozen=True, slots=True, repr=False)
class _Tool:
    registrations: Mapping[str, _Registration]
    workspace: Path
    catalog: RecoveryCatalog | None = None

    def support(self) -> ExecutionSupport:
        return ExecutionSupport(
            {
                "family": "model_tool",
                "version": 1,
                "workspace": str(self.workspace),
                "tools": {
                    name: {
                        "descriptor": value.descriptor.thaw(),
                        "advertised": encode_canonical(value.port.definition),
                        "supported": value.supported,
                    }
                    for name, value in self.registrations.items()
                },
            }
        )

    async def call(
        self, record: Tree, runtime: TaskRuntime, context: TaskContext
    ) -> None:
        previous = checkpoint(record["state"]["checkpoint"])
        captured = []

        async def read(tx: TaskTx, current: Tree) -> None:
            _same(current, previous)
            captured.append(await tx.model_tool_call())

        await runtime.commit(read)
        model = captured[0]
        registration = self.registrations.get(model.call.tool_name)
        if (
            registration is None
            or not registration.supported
            or model.snapshot.request.cwd != self.workspace
        ):
            await self.block(runtime, previous, None, "incompatible", record["input"])
            return
        offered = next(
            (
                tool
                for tool in model.snapshot.request.available_tools
                if tool.name == model.call.tool_name
            ),
            None,
        )
        if (
            offered is None
            or _model_declaration(offered)
            != _model_declaration(registration.port.definition)
            or offered.schema_mode != registration.port.definition.schema_mode
        ):
            await self.block(runtime, previous, None, "incompatible", record["input"])
            return
        if cast(Tree, registration.descriptor.thaw())["execution_mode"] == "sequential":
            await self.block(runtime, previous, None, "incompatible", record["input"])
            return
        executor = ToolExecutor(
            MappingProxyType({model.call.tool_name: registration.port})
        )
        prepared = await owned_call(lambda: executor.validated_arguments(model.call))
        key = await runtime.memo("pi.tool.call-key", "pipy-tool-" + uuid4().hex)
        if type(key) is not str:
            refuse()
        if type(prepared) is ToolExecutionOutcome:
            await self.receive(runtime, previous, None, record["input"], key, prepared)
            return
        final = intent(
            {
                "call": record["input"],
                "key": key,
                "definition": registration.descriptor.thaw(),
                "arguments": prepared,
                "workspace": str(self.workspace),
            }
        )
        execute = {"phase": "execute", "version": 1, "intent": final}

        async def admit(tx: TaskTx, current: Tree) -> Tree:
            _same(current, previous)
            saved = initial_progress(
                record["input"], key, attempts=1, restarts=current.get("restarts", 0)
            )
            (await tx.doc(PROGRESS_DOC)).update(cast(Tree, saved.thaw()))
            return {"status": "running", "checkpoint": execute}

        await runtime.commit(admit)
        # This fresh acknowledged continuation may execute unsafe exactly once.
        await self.run(
            record, runtime, context, execute, model.call, registration, fresh=True
        )

    async def execute(
        self, record: Tree, runtime: TaskRuntime, context: TaskContext
    ) -> None:
        from .tool_recovery import classify, counter

        previous = checkpoint(record["state"]["checkpoint"])
        replay = []

        async def recover(tx: TaskTx, current: Tree) -> Tree | None:
            _same(current, previous)
            model = await tx.model_tool_call()
            if self.catalog is not None:
                actual = (await tx.doc(HEADER)).snapshot().thaw()
                self.catalog.next_snapshot(
                    actual, current["conversationId"], model.snapshot
                )
                self.catalog.validate_intent(previous["intent"], model.call.tool_name)
            saved_doc = await tx.doc(RECEIPT_DOC)
            raw_saved = saved_doc.snapshot().thaw()
            retained = (
                (await tx.doc(PROGRESS_DOC)).snapshot().thaw()
                if type(raw_saved) is dict and not raw_saved
                else {}
            )
            if type(raw_saved) is dict and not raw_saved and "restarts" in current:
                counter(current["restarts"])
            try:
                decision = classify(
                    current,
                    model.call,
                    model.snapshot,
                    self.support().to_tree(),
                    raw_saved,
                    retained,
                )
            except (ValueError, TypeError, KeyError, IndexError):
                # Invalid receipt/progress is a hold, never permission to
                # synthesize a replacement or to retry an effect.
                return {
                    "status": "pending",
                    "checkpoint": {
                        "phase": "blocked",
                        "version": 1,
                        "intent": previous["intent"],
                        "reason": "incompatible",
                    },
                }
            if decision.disposition == "replay":
                if self.catalog is not None and not self.catalog.unchanged:
                    refuse()
                replay.append(model.call)
                return None
            final = previous["intent"]
            if model.has_descendants and (
                decision.disposition == "interrupted"
                or decision.saved is not None
                and cast(Tree, decision.saved.thaw())["nativeToolOutput"].get("ending")
                == "interrupted"
            ):
                return {
                    "status": "pending",
                    "checkpoint": {
                        "phase": "blocked",
                        "version": 1,
                        "intent": final,
                        "reason": "incompatible",
                    },
                }
            if decision.disposition == "interrupted":
                assert decision.retained is not None
                retained = cast(Tree, decision.retained.thaw())
                result = ToolExecutor({}).error_result(model.call, retained["output"])
                outcome = ToolExecutionOutcome(
                    replace(result, details=detail_value(retained["details"])),
                    execution_seconds=None,
                )
                output = native_tool_output(
                    outcome,
                    datetime.now(timezone.utc),
                    diagnostics=retained["diagnostics"],
                    interrupted={
                        "reason": "interrupted",
                        "attempts": retained["attempts"],
                        "restarts": decision.restarts,
                    },
                )
                saved = {
                    "version": 2,
                    "intent": final,
                    "call": current["input"],
                    "key": final["key"],
                    "nativeToolOutput": output,
                }
                receipt(saved)
                saved_doc.update(saved)
            # A valid existing receipt is the first winner. No payload rewrite.
            return {
                "status": "running",
                "checkpoint": {"phase": "settle", "version": 1, "key": final["key"]},
            }

        await runtime.commit(recover)
        if replay:
            call = replay[0]
            await self.run(
                record,
                runtime,
                context,
                previous,
                call,
                self.registrations[call.tool_name],
            )

    async def run(
        self,
        record: Tree,
        runtime: TaskRuntime,
        context: TaskContext,
        previous: Tree,
        call: Any,
        registration: _Registration,
        *,
        fresh: bool = False,
    ) -> None:
        final = previous["intent"]
        admitted: list[OwnedJson] = []

        async def attempt(tx: TaskTx, current: Tree) -> None:
            _same(current, previous)
            document = await tx.doc(PROGRESS_DOC)
            raw = document.snapshot().thaw()
            old = cast(Tree, progress(raw).thaw()) if raw else None
            if fresh:
                if (
                    old is None
                    or old["call"] != record["input"]
                    or old["key"] != final["key"]
                ):
                    refuse()
                captured = progress(old)
            else:
                count = None if old is None else old["attempts"]
                captured = initial_progress(
                    record["input"],
                    final["key"],
                    attempts=None if count is None else count + 1,
                    restarts=current.get("restarts"),
                )
                document.update(cast(Tree, captured.thaw()))
            admitted.append(captured)

        await runtime.commit(attempt)
        if context.signal.cancelled:
            return
        try:
            executor = ToolExecutor(
                MappingProxyType({call.tool_name: registration.port})
            )
            outcome, retained = await _transport(
                executor,
                call,
                final["arguments"],
                self.workspace,
                final["key"],
                context,
                runtime,
                previous,
                admitted[0],
            )
            if context.signal.cancelled:
                return
            try:
                match_result(outcome.result, call)
                output = native_tool_output(
                    outcome,
                    datetime.now(timezone.utc),
                    diagnostics=cast(Tree, retained.thaw())["diagnostics"],
                )
            except (ValueError, TypeError, KeyError, IndexError):
                raise _ToolDomainFailure(
                    "invalid_result", retained, outcome.execution_seconds
                ) from None
        except _ToolDomainFailure as error:
            retained = error.retained
            tree = cast(Tree, retained.thaw())
            result = executor.error_result(call, tree["output"])
            outcome = ToolExecutionOutcome(
                replace(result, details=detail_value(tree["details"])),
                execution_seconds=error.seconds,
            )
            output = native_tool_output(
                outcome,
                datetime.now(timezone.utc),
                diagnostics=tree["diagnostics"],
                failure=error.category,
            )
        except ProgressEnded:
            if context.signal.cancelled:
                return
            # No handled failure permission follows from lost reporting/commit authority.
            await self.block(
                runtime, previous, final, "invalid_result", record["input"]
            )
            return
        await self.receive(
            runtime,
            previous,
            final,
            record["input"],
            final["key"],
            outcome,
            output=output,
        )

    async def receive(
        self,
        runtime: TaskRuntime,
        previous: Tree,
        final: Tree | None,
        call_input: Tree,
        key: str,
        outcome: ToolExecutionOutcome,
        *,
        output: Tree | None = None,
    ) -> None:
        saved = {
            "version": 1 if output is None else output["version"],
            "intent": final,
            "call": call_input,
            "key": key,
            "nativeToolOutput": output
            if output is not None
            else native_tool_output(outcome, datetime.now(timezone.utc)),
        }
        receipt(saved)

        async def received(tx: TaskTx, current: Tree) -> Tree:
            _same(current, previous)
            output = await tx.doc(RECEIPT_DOC)
            if output.snapshot().thaw():
                refuse()  # A committed receipt is the first winner, never overwritten.
            output.update(saved)
            return {
                "status": "running",
                "checkpoint": {"phase": "settle", "version": 1, "key": key},
            }

        await runtime.commit(received)

    async def settle(
        self, record: Tree, runtime: TaskRuntime, context: TaskContext
    ) -> None:
        previous = checkpoint(record["state"]["checkpoint"])

        async def settled(tx: TaskTx, current: Tree) -> Tree:
            _same(current, previous)
            model = await tx.model_tool_call()
            raw = (await tx.doc(RECEIPT_DOC)).snapshot().thaw()
            try:
                saved, outcome = receipt(raw)
                if saved["call"] != record["input"] or saved["key"] != previous["key"]:
                    refuse()
                match_result(outcome.result, model.call)
            except (ValueError, TypeError, KeyError, IndexError):
                return {
                    "status": "pending",
                    "checkpoint": {
                        "phase": "blocked",
                        "version": 1,
                        "intent": None,
                        "reason": "incompatible",
                        "call": record["input"],
                    },
                }
            full = saved["nativeToolOutput"]
            draft = message_entry(project_tool_output(full, outcome))
            draft["data"] = (
                {"nativeToolReceipt": saved}
                if saved["version"] == 2
                else {
                    "nativeToolOutput": saved["nativeToolOutput"],
                    "call": saved["call"],
                    "key": saved["key"],
                }
            )
            entry = await tx.append_entry(draft)
            terminal: Tree = {
                "status": "terminal",
                "outcome": {
                    "status": "completed",
                    "result": {"kind": "model", "entryId": entry.id},
                },
            }
            if full["version"] == 2 and full["ending"] in ("failed", "interrupted"):
                terminal["outcome"].update(
                    status="failed",
                    error={"message": ending_text(full)},
                )
            return terminal

        await runtime.commit(settled)

    async def block(
        self,
        runtime: TaskRuntime,
        previous: Tree,
        final: Tree | None,
        reason: str,
        call_input: Tree,
    ) -> None:
        blocked = {"phase": "blocked", "version": 1, "intent": final, "reason": reason}
        if final is None:
            blocked["call"] = call_input
        checkpoint(blocked)

        def blocked_state(tx: TaskTx, current: Tree) -> Tree:
            _same(current, previous)
            return {"status": "pending", "checkpoint": blocked}

        await runtime.commit(blocked_state)


def _safe(handler: Any) -> Any:
    async def invoke(record: Tree, runtime: TaskRuntime, context: TaskContext) -> None:
        try:
            await handler(record, runtime, context)
        except Exception:  # noqa: BLE001 - faults are persisted and must be payload-free
            refuse()

    return invoke


async def _abort(*args: Any) -> None:
    refuse()


def _capture_tool(
    registrations: Mapping[str, ToolRegistration], *, workspace_root: Path
) -> _Tool:
    """Fixed current model-callable registry; live reload belongs to DE21/23."""
    if (
        type(registrations) not in (dict, MappingProxyType)
        or type(workspace_root) is not type(Path())
        or not workspace_root.is_absolute()
    ):
        refuse()
    try:
        owned = MappingProxyType(
            {name: _capture(name, raw) for name, raw in registrations.items()}
        )
        return _Tool(owned, workspace_root)
    except (TypeError, ValueError):
        refuse()


def _tool_definition(driver: _Tool) -> Task[Any, Any, Any]:
    return define_task(
        TOOL_NAME,
        TOOL_VERSION,
        _initial,
        {
            "call": _safe(driver.call),
            "execute": _safe(driver.execute),
            "settle": _safe(driver.settle),
        },
        _abort,
        support=driver.support(),
    )


def define_tool_task(
    registrations: Mapping[str, ToolRegistration], *, workspace_root: Path
) -> Task[Any, Any, Any]:
    """Fixed current model-callable registry without a product catalog binding."""
    return _tool_definition(_capture_tool(registrations, workspace_root=workspace_root))

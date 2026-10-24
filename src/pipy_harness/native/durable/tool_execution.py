"""Fixed native tool execution with acknowledged intent and committed receipts."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast
from uuid import uuid4

from pipy_harness.native.agent.tools import ToolExecutionOutcome, ToolExecutor
from pipy_harness.native.tools.base import ToolContext, ToolDefinition, ToolPort

from .canonical_codec import _schema_lists, decode_canonical, encode_canonical
from .codec import OwnedJson
from .documents import Doc, define_doc
from .generation import message_entry
from .generation_tasks import TOOL_NAME, TOOL_VERSION, _initial
from .generation_transport import owned_call
from .scheduler import TaskContext, TaskRuntime, TaskTx
from .tasks import Task, define_task
from .tool_values import (
    checkpoint,
    declaration,
    intent,
    match_result,
    native_tool_output,
    receipt,
    refuse,
)

Tree = dict[str, Any]
RECEIPT_DOC: Doc[Tree] = define_doc("pi.tool.receipt", 1, "task", dict)


@dataclass(frozen=True, slots=True)
class DurableToolContext(ToolContext):
    """Stable idempotency identity for tools with an explicitly admitted protocol."""

    call_key: str = field(kw_only=True)


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
        return self.invoke_function(request, context)

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


async def _transport(
    executor: ToolExecutor,
    call: Any,
    arguments: Tree,
    workspace: Path,
    key: str,
    context: TaskContext,
) -> ToolExecutionOutcome:
    cancelled = threading.Event()

    async def stop() -> None:
        await context.signal.wait()
        cancelled.set()

    watcher = asyncio.create_task(stop())
    try:
        tool_context = DurableToolContext(
            workspace, cancel_event=cancelled, call_key=key
        )
        return await owned_call(
            lambda: executor.execute(
                call, tool_context, wait_for_interrupt=None, arguments=arguments
            )
        )
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


@dataclass(frozen=True, slots=True, repr=False)
class _Tool:
    registrations: Mapping[str, _Registration]
    workspace: Path

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
            or OwnedJson(encode_canonical(offered))
            != OwnedJson(encode_canonical(registration.port.definition))
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

        def admit(tx: TaskTx, current: Tree) -> Tree:
            _same(current, previous)
            return {"status": "running", "checkpoint": execute}

        await runtime.commit(admit)
        # This fresh acknowledged continuation may execute unsafe exactly once.
        await self.run(record, runtime, context, execute, model.call, registration)

    async def execute(
        self, record: Tree, runtime: TaskRuntime, context: TaskContext
    ) -> None:
        previous = checkpoint(record["state"]["checkpoint"])
        captured = []

        async def read(tx: TaskTx, current: Tree) -> None:
            _same(current, previous)
            captured.append(await tx.model_tool_call())

        await runtime.commit(read)
        model = captured[0]
        saved = previous["intent"]
        registration = self.registrations.get(model.call.tool_name)
        compatible = (
            registration is not None
            and registration.supported
            and OwnedJson(saved["definition"]) == registration.descriptor
            and saved["workspace"]
            == str(self.workspace)
            == str(model.snapshot.request.cwd)
            and saved["call"] == record["input"]
        )
        if not compatible or saved["definition"]["replay"] != "safe":
            await self.block(
                runtime,
                previous,
                saved,
                "uncertain" if compatible else "incompatible",
                record["input"],
            )
            return
        assert registration is not None
        await self.run(record, runtime, context, previous, model.call, registration)

    async def run(
        self,
        record: Tree,
        runtime: TaskRuntime,
        context: TaskContext,
        previous: Tree,
        call: Any,
        registration: _Registration,
    ) -> None:
        final = previous["intent"]
        await runtime.commit(lambda tx, current: _same(current, previous))
        if context.signal.cancelled:
            return
        try:
            executor = ToolExecutor(
                MappingProxyType({call.tool_name: registration.port})
            )
            outcome = await _transport(
                executor,
                call,
                final["arguments"],
                self.workspace,
                final["key"],
                context,
            )
            if context.signal.cancelled:
                return
            match_result(outcome.result, call)
            output = native_tool_output(outcome, datetime.now(timezone.utc))
        except Exception:  # noqa: BLE001 - invocation errors preserve possible effects, not private diagnostics
            # An invoke may already have effected the world; no fabricated result.
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
            "version": 1,
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
            draft = message_entry(outcome.result)
            draft["data"] = {
                "nativeToolOutput": saved["nativeToolOutput"],
                "call": saved["call"],
                "key": saved["key"],
            }
            entry = await tx.append_entry(draft)
            return {
                "status": "terminal",
                "outcome": {
                    "status": "completed",
                    "result": {"kind": "model", "entryId": entry.id},
                },
            }

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


def define_tool_task(
    registrations: Mapping[str, ToolRegistration], *, workspace_root: Path
) -> Task[Any, Any, Any]:
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
        driver = _Tool(owned, workspace_root)
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
        )
    except (TypeError, ValueError):
        refuse()

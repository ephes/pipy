"""Private completed-only native generation on the existing Session line.

Adapted from Pi durable harness/{generation,provider,usage}.ts at
2ec55d808d455b1a64a2bf7888198207a096120a, MIT Copyright (c) 2025
Mario Zechner; see UPSTREAM-LICENSE.txt. Tool execution is DE08, public product
projection DE09–12, retries DE17 and committed partial streaming DE26.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, NoReturn, cast
from uuid import uuid4

from pipy_harness.native.agent.assistant_blocks import (
    agent_blocks,
    assistant_message,
    failed_provider_blocks,
    stopped_assistant,
)
from pipy_harness.native.agent.content import (
    ProductContent,
    TextContent,
    ThinkingContent,
)
from pipy_harness.native.agent.identity import AGENT_TOOL_REQUEST_ID_PREFIX
from pipy_harness.native.agent.loop_policy import (
    AgentProviderStatusAction,
    normalize_provider_status,
)
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentMessage,
    AgentMessageUsage,
    AgentStopReason,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.provider_result_validation import (
    validate_provider_result,
)
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.agent.request import (
    AgentProviderRequestSnapshot,
    snapshot_provider_request,
)
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentTokenPricing,
    message_usage,
)
from pipy_harness.native.auth_store import ProviderAuthRequestConfig
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.http import JsonHTTPClient
from pipy_harness.native.provider_construction import ConstructionOptions
from pipy_harness.status import HarnessStatus

from .canonical_codec import decode_canonical, encode_canonical
from .checkpoint_codec import CanonicalCheckpoint, decode_checkpoint, encode_checkpoint
from .codec import OwnedJson
from .documents import Doc, define_doc
from .generation_tasks import GENERATION_NAME, GENERATION_VERSION, TOOL_TASK
from .generation_transport import execute_provider, owned_call
from .provider_checkpoint import (
    PreparedProviderSettings,
    prepare_provider_settings,
    reconstruct_provider,
)
from .provider_inputs import own_construction_inputs
from .provider_settings import (
    decode_options,
    decode_pricing,
    encode_options,
    encode_pricing,
)
from .scheduler import TaskContext, TaskRuntime, TaskTx
from .tasks import Task, define_task

Tree = dict[str, Any]
PROVIDER_DOC: Doc[Tree] = define_doc(
    "pi.provider", 1, "conversation", lambda: {"sessionId": None}, "latest", "initial"
)
RECEIPT_DOC: Doc[Tree] = define_doc("pi.generation.receipt", 1, "task", dict)
ROUND_DOC: Doc[Tree] = define_doc("pi.generation.round", 1, "task", dict)
USAGE_DOC: Doc[Tree] = define_doc(
    "pi.usage",
    1,
    "conversation",
    lambda: {"models": {}, "tools": {}, "settlements": {}},
    "latest",
    "initial",
)


class GenerationError(ValueError):
    """Payload-free failure safe for the scheduler's persisted fault."""


def _fail() -> NoReturn:
    raise GenerationError("Durable generation is incompatible") from None


def message_entry(message: AgentMessage) -> Tree:
    """Minimal canonical model-entry representation, before full DE20 context."""
    kinds = {
        AgentUserMessage: "pi.user",
        AgentAssistantMessage: "pi.assistant",
        AgentToolResultMessage: "pi.tool-result",
    }
    if type(message) not in kinds:
        _fail()
    return {"kind": kinds[type(message)], "model": [encode_canonical(message)]}


def _frame(
    phase: str,
    role: str,
    identity: object,
    payload: object,
    settings: PreparedProviderSettings | None = None,
) -> CanonicalCheckpoint:
    return encode_checkpoint(
        name=GENERATION_NAME,
        version=GENERATION_VERSION,
        phase=phase,
        role=role,
        identity=identity,
        payload=payload,
        settings=settings,
    )


def _read(record: Tree, phase: str, role: str) -> CanonicalCheckpoint:
    return decode_checkpoint(
        record["state"]["checkpoint"],
        name=GENERATION_NAME,
        version=GENERATION_VERSION,
        phase=phase,
        role=role,
    )


def _seed(value: object) -> AgentProviderRequestSnapshot:
    result = decode_canonical(value)
    if type(result) is not AgentProviderRequestSnapshot:
        _fail()
    return result


def _current(current: Tree, frame: CanonicalCheckpoint) -> None:
    if OwnedJson(current["state"]["checkpoint"]) != OwnedJson(frame.to_tree()):
        _fail()


def _initial(value: object) -> Tree:
    _seed(value)
    return _frame(
        "prepare",
        "prepare",
        {"attempt": uuid4().hex, "request": uuid4().hex, "cutoff": 0},
        None,
    ).to_tree()


def _history(entries: tuple[Any, ...]) -> tuple[tuple[AgentMessage, ...], int]:
    history: list[AgentMessage] = []
    cutoff = 0
    for entry in sorted((e.to_tree() for e in entries), key=lambda e: e["id"]):
        if "edits" in entry or "head" in entry:
            _fail()
        cutoff = entry["id"]
        for arena in entry.get("model", []):
            message = decode_canonical(arena)
            if type(message) not in (
                AgentUserMessage,
                AgentAssistantMessage,
                AgentToolResultMessage,
            ):
                _fail()
            history.append(cast(AgentMessage, message))
    if not history or not cutoff:
        _fail()
    return tuple(history), cutoff


def _assistant(
    outcome: ProviderTurnOutcome,
    snapshot: AgentProviderRequestSnapshot,
    pricing: AgentTokenPricing | None,
    streamed: tuple[TextContent | ThinkingContent, ...],
) -> AgentAssistantMessage:
    result = outcome.result
    if result is None:
        return stopped_assistant(
            agent_blocks(outcome.partial.content_blocks)
            if outcome.partial
            else streamed,
            AgentStopReason.ABORTED,
            usage=AgentMessageUsage(),
            request=snapshot.request,
            provider_thinking_level=(
                outcome.partial.provider_thinking_level if outcome.partial else None
            ),
        )
    validate_provider_result(result, snapshot)
    usage = message_usage(AgentProviderUsageSample.from_mapping(result.usage), pricing)
    status = normalize_provider_status(
        result, provider_name=snapshot.request.provider_name
    )
    if status.action is AgentProviderStatusAction.SUCCEEDED:
        return assistant_message(result, usage, snapshot.request, streamed)
    assert status.failure is not None
    return stopped_assistant(
        failed_provider_blocks(result, streamed),
        AgentStopReason.ERROR,
        status.failure.message.value,
        usage=usage,
        request=snapshot.request,
        provider_thinking_level=result.provider_thinking_level,
    )


def _usage_tree(usage: AgentMessageUsage) -> Tree:
    value: Tree = {
        key: getattr(usage, key)
        for key in ("input", "output", "cache_read", "cache_write", "total_tokens")
    }
    value["cost"] = {
        key: getattr(usage.cost, key)
        for key in ("input", "output", "cache_read", "cache_write", "total")
    }
    for key in ("reasoning", "cache_write_1h"):
        if getattr(usage, key) is not None:
            value[key] = getattr(usage, key)
    return value


def _validate_receipt(
    frame: CanonicalCheckpoint,
    outcome: ProviderTurnOutcome,
    snapshot: AgentProviderRequestSnapshot,
    assistant: AgentAssistantMessage,
) -> None:
    saved = frame.settings
    assert saved is not None
    settings = saved.to_tree()
    _outcome_api(outcome, saved)
    if (snapshot.request.provider_name, snapshot.request.model_id) != (
        settings["provider"],
        settings["model"],
    ):
        _fail()
    buffered = tuple(
        block
        for block in assistant.ordered_content()
        if type(block) in (TextContent, ThinkingContent)
    )
    expected = _assistant(
        outcome,
        snapshot,
        saved.pricing,
        cast(tuple[TextContent | ThinkingContent, ...], buffered),
    )
    if OwnedJson(encode_canonical(expected)) != OwnedJson(encode_canonical(assistant)):
        _fail()


def _outcome_api(
    outcome: ProviderTurnOutcome, settings: PreparedProviderSettings
) -> None:
    if outcome.result is not None and outcome.result.api not in (
        None,
        settings.to_tree()["api"],
    ):
        _fail()


def _record_usage(
    ledger: Any, request_id: str, entry_id: int, assistant: AgentAssistantMessage
) -> None:
    if request_id in ledger["settlements"]:
        _fail()
    key = f"{assistant.provider}/{assistant.model}"
    if assistant.usage is None:
        _fail()
    usage = _usage_tree(assistant.usage)
    if key not in ledger["models"]:
        ledger["models"][key] = usage
    else:
        total = ledger["models"][key]
        for field, value in usage.items():
            if field == "cost":
                for component, amount in value.items():
                    total["cost"][component] += amount
            else:
                total[field] = total.get(field, 0) + value
    ledger["settlements"][request_id] = entry_id


@dataclass(frozen=True, slots=True, repr=False)
class _Generation:
    spec: NativeModelSpec
    auth_path: Path
    env: dict[str, str]
    runtime_api_key: str | None
    config: ProviderAuthRequestConfig | None
    thinking: str | None
    options: ConstructionOptions
    pricing: AgentTokenPricing | None
    http_client: JsonHTTPClient | None

    async def prepare(
        self, record: Tree, runtime: TaskRuntime, context: TaskContext
    ) -> None:
        frame = _read(record, "prepare", "prepare")
        seed = _seed(record["input"])
        chosen: list[Any] = []

        async def capture(tx: TaskTx, current: Tree) -> None:
            _current(current, frame)
            history, cutoff = _history(await tx.entries())
            provider = await tx.doc(PROVIDER_DOC)
            if provider["sessionId"] is None:
                provider["sessionId"] = uuid4().hex
            session_id = provider["sessionId"]
            if type(session_id) is not str or not session_id:
                _fail()
            if seed.request.session_id not in (None, session_id):
                _fail()
            chosen.extend((history, cutoff, session_id))

        await runtime.commit(capture)
        history, cutoff, session_id = chosen
        snapshot = snapshot_provider_request(
            replace(seed.request, session_id=session_id), messages=history
        )
        material = await runtime.memo("pi.generation.material", uuid4().hex)
        if type(material) is not str:
            _fail()
        saved = await owned_call(
            partial(
                prepare_provider_settings,
                snapshot,
                self.spec,
                auth_path=self.auth_path,
                env=self.env,
                runtime_api_key=self.runtime_api_key,
                config=self.config,
                thinking=self.thinking,
                options=self.options,
                pricing=self.pricing,
                json_client=self.http_client is not None,
                material_id=material,
            )
        )
        request = _frame(
            "request", "request", {**frame.identity, "cutoff": cutoff}, snapshot, saved
        )

        def prepared(tx: TaskTx, current: Tree) -> Tree:
            _current(current, frame)
            return {"status": "running", "checkpoint": request.to_tree()}

        await runtime.commit(prepared)

    async def request(
        self, record: Tree, runtime: TaskRuntime, context: TaskContext
    ) -> None:
        frame = _read(record, "request", "request")
        snapshot = cast(AgentProviderRequestSnapshot, frame.payload)
        saved = frame.settings
        assert saved is not None
        provider = await owned_call(
            partial(
                reconstruct_provider,
                saved,
                self.spec,
                auth_path=self.auth_path,
                env=self.env,
                runtime_api_key=self.runtime_api_key,
                config=self.config,
                http_client=self.http_client,
            )
        )
        await runtime.commit(lambda tx, current: _current(current, frame))
        if context.signal.cancelled:
            return
        outcome, buffered = await execute_provider(provider, snapshot.request, context)
        if context.signal.cancelled:
            return
        _outcome_api(outcome, saved)
        # Own the exact provider result AND the completed fallback assistant
        # before either crosses the Session queue/suspension boundary.
        assistant = _assistant(outcome, snapshot, saved.pricing, buffered)
        receipt = OwnedJson(
            {
                "identity": frame.identity,
                "request": encode_canonical(snapshot),
                "assistant": encode_canonical(assistant),
            }
        )
        settled = _frame("settle", "provider_outcome", frame.identity, outcome, saved)

        async def received(tx: TaskTx, current: Tree) -> Tree:
            _current(current, frame)
            document = await tx.doc(RECEIPT_DOC)
            document.update(receipt.thaw())
            return {"status": "running", "checkpoint": settled.to_tree()}

        await runtime.commit(received)

    async def settle(
        self, record: Tree, runtime: TaskRuntime, context: TaskContext
    ) -> None:
        frame = _read(record, "settle", "provider_outcome")
        outcome = cast(ProviderTurnOutcome, frame.payload)

        async def settlement(tx: TaskTx, current: Tree) -> Tree:
            _current(current, frame)
            receipt = (await tx.doc(RECEIPT_DOC)).snapshot().thaw()
            if type(receipt) is not dict or set(receipt) != {
                "identity",
                "request",
                "assistant",
            }:
                _fail()
            if OwnedJson(receipt["identity"]) != OwnedJson(frame.identity):
                _fail()
            snapshot = _seed(receipt["request"])
            assistant = decode_canonical(receipt["assistant"])
            if type(assistant) is not AgentAssistantMessage:
                _fail()
            assert isinstance(assistant, AgentAssistantMessage)
            if (assistant.provider, assistant.model) != (
                snapshot.request.provider_name,
                snapshot.request.model_id,
            ):
                _fail()
            _validate_receipt(frame, outcome, snapshot, assistant)
            ledger = await tx.doc(USAGE_DOC)
            draft = message_entry(assistant)
            draft["data"] = {"receipt": frame.to_tree(), "request": receipt["request"]}
            entry = await tx.append_entry(draft)
            _record_usage(ledger, frame.identity["request"], entry.id, assistant)
            if (
                outcome.result is not None
                and outcome.result.status is HarnessStatus.SUCCEEDED
                and assistant.tool_calls
            ):
                return await _tool_round(tx, entry.id, assistant, snapshot, frame)
            status = (
                "completed"
                if outcome.result is not None
                and outcome.result.status is HarnessStatus.SUCCEEDED
                else "failed"
            )
            terminal: Tree = {"status": status, "result": {"entryId": entry.id}}
            if status == "failed":
                terminal["error"] = {
                    "message": "Durable provider response did not complete"
                }
            return {"status": "terminal", "outcome": terminal}

        await runtime.commit(settlement)


async def _tool_round(
    tx: TaskTx,
    assistant_id: int,
    assistant: AgentAssistantMessage,
    snapshot: AgentProviderRequestSnapshot,
    frame: CanonicalCheckpoint,
) -> Tree:
    children: list[int] = []
    slots: list[Tree] = []
    for call in assistant.tool_calls:
        if snapshot.authorizes(call.tool_name):
            child = await tx.create_task(
                TOOL_TASK,
                {
                    "kind": "model",
                    "assistant": assistant_id,
                    "callId": call.provider_correlation_id,
                },
            )
            children.append(child)
            slots.append({"callId": call.provider_correlation_id, "taskId": child})
        else:
            unavailable = AgentToolResultMessage(
                AGENT_TOOL_REQUEST_ID_PREFIX + uuid4().hex,
                call.tool_name,
                ProductContent("Tool is not available"),
                call.provider_correlation_id,
                True,
                details={"code": "tool_unavailable"},
            )
            result = await tx.append_entry(message_entry(unavailable))
            slots.append({"callId": call.provider_correlation_id, "entryId": result.id})
    round_state = await tx.doc(ROUND_DOC)
    round_state.update({"assistant": assistant_id, "tools": children, "slots": slots})
    waiting = frame.to_tree()
    waiting["phase"] = "tools"
    return {
        "status": "waiting",
        "checkpoint": waiting,
        "on": children,
        "policy": "allSettled",
    }


def _safe(
    handler: Callable[[Tree, TaskRuntime, TaskContext], Awaitable[None]],
) -> Callable[[Tree, TaskRuntime, TaskContext], Awaitable[None]]:
    async def invoke(record: Tree, runtime: TaskRuntime, context: TaskContext) -> None:
        try:
            await handler(record, runtime, context)
        except Exception:  # noqa: BLE001 - transport/private construction diagnostics cannot enter task faults
            _fail()

    return invoke


async def _abort(record: Tree, runtime: TaskRuntime, context: TaskContext) -> None:
    _fail()


def define_generation(
    spec: NativeModelSpec,
    *,
    auth_path: Path,
    env: Mapping[str, str],
    runtime_api_key: str | None = None,
    config: ProviderAuthRequestConfig | None = None,
    thinking: str | None = None,
    options: ConstructionOptions | None = None,
    pricing: AgentTokenPricing | None = None,
    http_client: JsonHTTPClient | None = None,
) -> Task[Any, Any, Any]:
    """Fixed native construction; input is an encoded request snapshot seed.

    Preparation replaces only its history with supported committed model entries
    and establishes provider session identity. Explicit hooks, sequential-round
    controls and extension factories have no admitted input surface here.
    """
    spec, config, environment = own_construction_inputs(spec, config, env)
    driver = _Generation(
        spec,
        auth_path,
        environment,
        runtime_api_key,
        config,
        thinking,
        decode_options(encode_options(options or ConstructionOptions())),
        decode_pricing(encode_pricing(pricing)),
        http_client,
    )
    return define_task(
        GENERATION_NAME,
        GENERATION_VERSION,
        _initial,
        {
            "prepare": _safe(driver.prepare),
            "request": _safe(driver.request),
            "settle": _safe(driver.settle),
        },
        _abort,
    )

"""TEST-only actual SDK producer and read-only captured-world adapters.

Healthy host close is the cut here, not SIGKILL. No receipt/fact is fabricated.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import enum
import json
import threading
from pathlib import Path

import test_tool_fact2_reader_matrix as baseline
from generation_support import seed
from test_product_session import profile
from test_tool_boundaries import MultiRound

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable import tool_execution
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.tools.base import ToolDefinition, ToolExecutionResult
from pipy_harness.sdk import (
    create_durable_product_session,
    open_durable_product_session,
)

TEXT = "Tool execution interrupted; result is uncertain."
PREPARED_MARKER = "C1_PRIVATE_PREPARED_ARGUMENT_VALUE"
RETAINED_DETAILS: dict[str, list[object]] = {"retained": [None, False, 0, [], {}]}


def public_tree(value):
    """Inspect actual DTO payloads, never redacted repr as a privacy oracle."""
    if isinstance(value, OwnedJson):
        return value.thaw()
    if dataclasses.is_dataclass(value):
        return {
            f.name: public_tree(getattr(value, f.name))
            for f in dataclasses.fields(value)
        }
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: public_tree(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [public_tree(v) for v in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise AssertionError("Unexpected nonpublic DTO leaf: " + type(value).__name__)


def counts(base):
    return (tuple(base["prepared"]), tuple(base["invoked"]), len(base["wire"].calls))


async def cut_actual_sdk(root, monkeypatch, *, with_prefix=False):
    """Actual public admission -> real execute commit -> healthy close/join.

    Before-invoke variant replaces only the phase body's final invocation with
    an owned signal wait; the real call/prepare/intent transaction runs first.
    Prefix variant runs a real tool/report_details ACK and waits for close.
    """
    entered = threading.Event()
    prepared, invoked = [], []
    wire = MultiRound((("first", "second"), ()))

    class Port:
        def __init__(self, name):
            self.name = name
            self.definition = ToolDefinition(
                name,
                name,
                {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "privatePrepared": {"type": "string"},
                    },
                },
            )

        def prepare_arguments(self, arguments):
            prepared.append(self.name)
            return {**arguments, "privatePrepared": PREPARED_MARKER}

        def invoke(self, request, context):
            invoked.append(self.name)
            if self.name == "first":
                assert with_prefix, "Before-invoke phase hold must not invoke the tool"
                context.output_sink("retained interruption prefix")
                context.report_diagnostic(
                    severity="info", message="retained note", code="c1-note"
                )
                context.report_details(RETAINED_DETAILS)  # Actual committed-prefix ACK.
                entered.set()
                assert context.cancel_event is not None and context.cancel_event.wait(
                    10
                )
            return ToolExecutionResult(
                request.tool_request_id, "normal tool result", details={"normal": True}
            )

    ports = [Port("first"), Port("second")]
    registry = {
        p.name: tool_execution.ToolRegistration(
            p, "fixture.c1." + p.name, "1", "unsafe"
        )
        for p in ports
    }
    template = snapshot_provider_request(
        dataclasses.replace(
            seed(root).request,
            user_prompt="",
            messages=(),
            available_tools=tuple(p.definition for p in ports),
        )
    )
    original_run = tool_execution._Tool.run

    async def held_run(
        self, record, runtime, context, previous, call, registration, *, fresh=False
    ):
        if call.tool_name == "first" and not with_prefix:
            entered.set()
            await context.signal.wait()
            return
        return await original_run(
            self, record, runtime, context, previous, call, registration, fresh=fresh
        )

    monkeypatch.setattr(tool_execution._Tool, "run", held_run)
    product = None
    work = None
    try:
        product = await create_durable_product_session(
            root / "store.sqlite",
            workspace=root,
            profile=profile(root, wire),
            tools=registry,
            request_template=template,
        )
        await product.submit("input")
        work = asyncio.create_task(product.resume())
        assert await asyncio.to_thread(entered.wait, 8), (
            "Actual execute frontier was not reached"
        )
        before_close = await product._session.aread_on_line(
            lambda: baseline.world(product._session._owner.storage)
        )
        child = next(
            r
            for r in before_close["rows"]
            if r["kind"] == "pi.tool" and r["input"]["callId"] == "call:0"
        )
        assert child["state"]["checkpoint"]["phase"] == "execute"
    finally:
        if product is not None:
            await product.close()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        monkeypatch.setattr(tool_execution._Tool, "run", original_run)
    assert prepared == ["first"] and invoked == (["first"] if with_prefix else [])
    assert len(wire.calls) == 1
    return {
        "root": root,
        "path": root / "store.sqlite",
        "ports": ports,
        "registry": registry,
        "wire": wire,
        "prepared": prepared,
        "invoked": invoked,
        "before_close": before_close,
        "child_id": child["id"],
        "with_prefix": with_prefix,
    }


async def recover_actual_sdk(base, monkeypatch):
    """Cold public resume with transparent source/settlement observation."""
    holder, captures, source_records = {}, [], []
    actual_settle = tool_execution._Tool.settle
    actual_execute = tool_execution._Tool.execute

    async def observed_execute(self, record, runtime, context):
        source_records.append(copy.deepcopy(record))
        return await actual_execute(self, record, runtime, context)

    async def observed_settle(self, record, runtime, context):
        await actual_settle(self, record, runtime, context)
        captures.append(
            await holder["product"]._session.aread_on_line(
                lambda: baseline.world(holder["product"]._session._owner.storage)
            )
        )

    monkeypatch.setattr(tool_execution._Tool, "execute", observed_execute)
    monkeypatch.setattr(tool_execution._Tool, "settle", observed_settle)
    product = await open_durable_product_session(
        base["path"],
        workspace=base["root"],
        profile=profile(base["root"], base["wire"]),
        tools=base["registry"],
    )
    holder["product"] = product
    try:
        before_resume = await product._session.aread_on_line(
            lambda: baseline.world(product._session._owner.storage)
        )
        result = await product.resume()
        closed_world = await product._session.aread_on_line(
            lambda: baseline.world(product._session._owner.storage)
        )
        metadata = await product.inspect()
    finally:
        await product.close()
        monkeypatch.setattr(tool_execution._Tool, "execute", actual_execute)
        monkeypatch.setattr(tool_execution._Tool, "settle", actual_settle)
    base.update(
        before_resume=before_resume,
        result=result,
        closed_world=closed_world,
        metadata=metadata,
        captures=captures,
        source_records=source_records,
    )
    return base


def interrupted_base(base):
    """Require a production-generated C1 carrier, never synthesize one."""
    assert base["result"].status == "terminal", (
        "C1 recovery frontier remains held on this source"
    )
    assert len(base["captures"]) == 2 and len(base["wire"].calls) == 2
    final = base["captures"][-1]
    child = next(r for r in final["rows"] if r["id"] == base["child_id"])
    assert child["state"]["outcome"]["status"] == "failed"
    entry_id = child["state"]["outcome"]["result"]["entryId"]
    tree = final["entries"][entry_id].to_tree()
    assert set(tree["data"]) == {"nativeToolReceipt"}
    saved = tree["data"]["nativeToolReceipt"]
    assert saved["version"] == saved["nativeToolOutput"]["version"] == 2
    assert saved["nativeToolOutput"]["ending"] == "interrupted"
    assistant = decode_canonical(
        final["entries"][final["value"]["assistant"]].to_tree()["model"][0]
    )
    return {
        **base,
        **final,
        "child": child,
        "entry_id": entry_id,
        "assistant": assistant,
        "open_world": base["captures"][0],
        "closed_world": base["closed_world"],
        "settled_world": final,
        "receipt": saved,
        "baseline_counts": counts(base),
    }


# These are storage-schema-valid mutants of an ACTUAL producer carrier. They
# never stand in for the producer frontier above and never change product code.
COMMON_MUTATIONS = (
    "extra_model_arena",
    "unknown_extra_arena",
    "duplicate_carrier",
    "missing_carrier",
    "receipt_version",
    "fact_version",
    "receipt_fact_mismatch",
    "wrong_call",
    "wrong_key",
    "foreign_assistant",
    "foreign_cid",
    "wrong_by_task",
    "wrong_parent",
    "wrong_owner",
    "wrong_input_call",
    "wrong_kind_version",
    "model_details",
    "model_control",
    "raw_details",
    "raw_terminate",
    "raw_cancelled",
    "abort_requested",
    "background",
    "abandon_on_restart",
    "extra_terminal_error",
    "extra_result_key",
    "completed_task_failed_fact",
)
SESSION_READERS = (
    "async_graph_result",
    "tasktx_round",
    "tasktx_call",
    "reconcile",
    "completed_capture",
)
ALL_READERS = tuple(baseline.READERS) + SESSION_READERS
COUNTER_VALUES = {"boolean": True, "float": 1.0, "negative": -1, "unsafe": 2**53}
C1_MUTATIONS = (
    "missing_reason",
    "missing_attempts",
    "missing_restarts",
    "unknown_reason",
    "reason_null",
    "reason_array",
    "reason_object",
    "recovery_null",
    "extra_recovery_category",
    "extra_recovery_private",
    "extra_fact_private",
    "extra_receipt_private",
    "receipt_intent_null",
    "receipt_intent_array",
    "receipt_call_array",
    "receipt_key_array",
    "ending_completed",
    "ending_failed",
    "ending_unknown",
    "terminal_text",
    "raw_is_error",
    "raw_malformed",
    "raw_duration",
    "raw_not_settled",
    "raw_output",
    "raw_diagnostics",
) + tuple(
    f"{key}_{kind}" for key in ("attempts", "restarts") for kind in COUNTER_VALUES
)


def _counter_mutant(mutation, recovery, child):
    if mutation.startswith("missing_"):
        del recovery[mutation.removeprefix("missing_")]
    elif mutation.startswith("task_restart_"):
        kind = mutation.removeprefix("task_restart_")
        if kind == "absent":
            child.pop("restarts", None)
        else:
            child["restarts"] = COUNTER_VALUES[kind]
    elif mutation in ("attempts_null", "restarts_null"):
        recovery[mutation.removesuffix("_null")] = None
    elif mutation in ("attempts_safe_max", "restarts_safe_max"):
        recovery[mutation.removesuffix("_safe_max")] = 2**53 - 1
    elif mutation in {
        f"{key}_{kind}" for key in ("attempts", "restarts") for kind in COUNTER_VALUES
    }:
        key, kind = mutation.split("_", 1)
        recovery[key] = COUNTER_VALUES[kind]
    else:
        return False
    return True


def _cause_mutant(mutation, saved, fact):
    recovery = fact["recovery"]
    if mutation == "unknown_reason":
        recovery["reason"] = "future_interruption"
    elif mutation.startswith("reason_"):
        recovery["reason"] = {
            "reason_null": None,
            "reason_array": [],
            "reason_object": {},
        }[mutation]
    elif mutation == "recovery_null":
        fact["recovery"] = None
    elif mutation == "extra_recovery_category":
        recovery["category"] = "tool_error"
    elif mutation == "extra_recovery_private":
        recovery["private"] = PREPARED_MARKER
    elif mutation == "receipt_intent_null":
        saved["intent"] = None
    elif mutation == "extra_fact_private":
        fact["private"] = PREPARED_MARKER
    elif mutation == "extra_receipt_private":
        saved["private"] = PREPARED_MARKER
    elif mutation in (
        "receipt_intent_null",
        "receipt_intent_array",
        "receipt_call_array",
        "receipt_key_array",
    ):
        saved[mutation.split("_")[1]] = []
    else:
        return False
    return True


def _raw_mutant(mutation, fact):
    from pipy_harness.native.agent.content import ProductContent
    from pipy_harness.native.agent.tools import ToolExecutionInterruption
    from pipy_harness.native.durable.canonical_codec import encode_canonical

    if mutation == "raw_diagnostics":
        fact["diagnostics"] = []
        return True
    if not mutation.startswith("raw_"):
        return False
    outcome = decode_canonical(fact["outcome"])
    if mutation == "raw_is_error":
        outcome = dataclasses.replace(
            outcome, result=dataclasses.replace(outcome.result, is_error=False)
        )
    elif mutation == "raw_malformed":
        outcome = dataclasses.replace(outcome, malformed_arguments=True)
    elif mutation == "raw_duration":
        outcome = dataclasses.replace(outcome, execution_seconds=0.0)
    elif mutation == "raw_not_settled":
        alternative = next(
            x
            for x in ToolExecutionInterruption
            if x is not ToolExecutionInterruption.SETTLED
        )
        outcome = dataclasses.replace(outcome, interruption=alternative)
    elif mutation == "raw_output":
        outcome = dataclasses.replace(
            outcome,
            result=dataclasses.replace(
                outcome.result,
                content=ProductContent("changed retained raw text"),
                blocks=(),
            ),
        )
    else:
        raise AssertionError(mutation)
    fact["outcome"] = encode_canonical(
        outcome, codec_version=fact["outcome"]["codec_version"]
    )
    return True


def _slot_mutant(source, mutation, rows):
    value = copy.deepcopy(source["value"])
    value["slots"][0]["taskId"] = (
        value["slots"][1]["taskId"]
        if mutation == "slot_task_alias"
        else max(r["id"] for r in rows) + 1000
    )
    changed = {
        **source,
        "value": value,
        "documents": copy.deepcopy(source["documents"]),
    }
    for doc in changed["documents"]:
        if doc["value"] == source["value"]:
            doc["value"] = value
    return changed


def mutated_world(base, world_name, mutation="none"):
    """Qualify EntryRecord and EVERY TaskRecord before any consumer is called."""
    from pipy_harness.native.durable.records import EntryRecord, TaskRecord

    source = base[world_name] if world_name is not None else base
    current = {**base, **source}
    parent, rows, entries = baseline.views(
        current, mutation if mutation in COMMON_MUTATIONS else "none"
    )
    child = next(r for r in rows if r["id"] == base["child"]["id"])
    tree = entries[base["entry_id"]].to_tree()
    if mutation not in COMMON_MUTATIONS:
        saved = tree["data"]["nativeToolReceipt"]
        fact = saved["nativeToolOutput"]
        handled = (
            _counter_mutant(mutation, fact["recovery"], child)
            or _cause_mutant(mutation, saved, fact)
            or _raw_mutant(mutation, fact)
        )
        if handled:
            pass
        elif mutation.startswith("ending_"):
            fact["ending"] = mutation.removeprefix("ending_")
        elif mutation == "terminal_text":
            child["state"]["outcome"]["error"]["message"] = "Tool failed (tool_error)."
        elif mutation in ("slot_task_alias", "slot_task_foreign"):
            source = _slot_mutant(source, mutation, rows)
        elif mutation == "completing":
            child["state"]["status"] = "completing"
        elif mutation != "none":
            raise AssertionError(mutation)
        entries[base["entry_id"]] = EntryRecord(tree)
    for row in rows:
        TaskRecord(row)
    return {**source, "parent": parent, "rows": rows, "entries": entries}


def assert_fixed_refusal(error):
    """Do not accept accidental TypeError/KeyError or payload-bearing errors."""
    from pipy_harness.native.durable.scheduler import UnsupportedTaskExecution

    assert isinstance(error, (ValueError, UnsupportedTaskExecution)), type(
        error
    ).__name__
    assert str(error) in {
        "Durable tool execution is incompatible",
        "Committed projection is incompatible",
        "Model tool authority is incompatible",
        "Tool round no longer exists",
    }, str(error)


def read_captured(base, reader, mutation="none", *, accepted=True):
    """Pure predicates or real AsyncTx/TaskTx/Session against captured Memory rows.

    Session readers own an independent clone of actual captured producer data;
    no scheduler/provider/tool/preparer is installed or invoked by these reads.
    """
    if reader in baseline.READERS:
        changed = {**base, **mutated_world(base, None, mutation)}
        try:
            result = read_predicate(changed, reader)
        except Exception as error:
            if accepted:
                raise
            assert_fixed_refusal(error)
            return None
        assert accepted, f"{reader} accepted {mutation}"
        return result

    from pipy_harness.native.durable import completed_values
    from pipy_harness.native.durable.session_core import Session

    assert reader in SESSION_READERS
    world_name = (
        "open_world"
        if reader == "tasktx_call"
        else "closed_world"
        if reader == "completed_capture"
        else "settled_world"
    )
    changed = mutated_world(base, world_name, mutation)
    wrapper = {**base, "reader_world": changed}
    store, parent, rows, entries = memory_world(
        wrapper, "reader_world", waiting=reader == "reconcile"
    )
    session = Session(store)
    value, assistant = changed["value"], base["assistant"]
    before_counts = counts(base)

    async def run():
        async def consume(tx):
            return await session_consume(
                reader, tx, store, parent, value, assistant, rows, entries
            )

        if reader == "completed_capture":
            call = session.aread_on_line(
                lambda: completed_values.capture(
                    session._owner.storage, parent["conversationId"]
                )
            )
        else:
            call = session.acommit(consume)
        try:
            output = await call
        except Exception as error:
            if accepted:
                raise
            assert_fixed_refusal(error)
            return None
        if reader == "reconcile":
            state = next(r for r in output if r["id"] == parent["id"])["state"]
            assert (state["status"] == "pending") is accepted
        else:
            assert accepted, f"{reader} accepted {mutation}"
            assert output is not None
        return output

    try:
        return asyncio.run(run())
    finally:
        session.close()
        assert counts(base) == before_counts


def assert_public_no_private(value, base):
    """Inspect actual payload fields and private values, not redacted repr."""
    payload = json.dumps(public_tree(value), ensure_ascii=True, sort_keys=True)
    intent = base["receipt"]["intent"]
    for private in (
        PREPARED_MARKER,
        str(base["root"]),
        intent["workspace"],
        intent["key"],
    ):
        assert private not in payload
    # Public full facts are supported, private carrier names never are.
    assert '"nativeToolReceipt"' not in payload
    # The schema property name is public; its prepared private VALUE is not.
    return payload


def read_predicate(base, reader):
    """Enter product readers directly, without re-reading the test carrier."""
    from pipy_harness.native.durable import (
        completed_values,
        inspection_read,
        resolution_policy,
        tool_graph,
    )

    parent, rows, entries = base["parent"], base["rows"], base["entries"]
    value, assistant = base["value"], base["assistant"]
    if reader == "child_result_id":
        return tool_graph.child_result_id(
            parent,
            value["assistant"],
            assistant.tool_calls[0],
            value["slots"][0],
            rows,
            entries,
        )
    if reader == "completed_prefix":
        return completed_values._prefix(
            parent, value["assistant"], value["slots"], rows, entries
        )
    if reader == "completed_closed_links":
        index = {
            "rounds": {
                str(value["assistant"]): [
                    next(r for r in rows if r["id"] == slot["taskId"])["state"][
                        "outcome"
                    ]["result"]["entryId"]
                    for slot in value["slots"]
                ]
            }
        }
        return completed_values._closed_links(entries, rows, index)
    if reader == "inspection_terminate":
        return inspection_read.terminate_index(parent, value, assistant, rows, entries)
    if reader == "resolution_prefix":
        return resolution_policy._prefix(
            parent, value, assistant, rows, entries, value["slots"][1]["taskId"]
        )
    raise AssertionError(reader)


def memory_world(base, world_name, *, waiting=False):
    """Storage-schema-qualified captured graph; no test carrier pre-parser."""
    from pipy_harness.native.durable.memory import MemoryStorage
    from pipy_harness.native.durable.storage import StorageWrite

    source = base[world_name]
    parent, rows, entries = source["parent"], source["rows"], source["entries"]
    if waiting:
        parent["state"] = copy.deepcopy(base["open_world"]["parent"]["state"])
    writes = [
        StorageWrite({"type": "conversation", "value": row})
        for row in source["conversations"]
    ]
    writes.extend(StorageWrite({"type": "task", "value": row}) for row in rows)
    writes.extend(
        StorageWrite({"type": "entry", "value": row.to_tree()})
        for row in entries.values()
    )
    for doc in source["documents"]:
        record = dict(doc["record"])
        record.pop("createdAt", None)
        record.pop("retiredAt", None)
        writes.append(
            StorageWrite(
                {
                    "type": "document.create",
                    "record": record,
                    "content": {
                        "kind": "base",
                        "version": doc["version"],
                        "value": doc["value"],
                    },
                }
            )
        )
    store = MemoryStorage()
    store.commit(tuple(writes))
    return store, parent, rows, entries


async def session_consume(reader, tx, store, parent, value, assistant, rows, entries):
    from pipy_harness.native.durable import tool_graph
    from pipy_harness.native.durable.scheduler import TaskTx
    from pipy_harness.native.durable.storage import ConversationQuery

    if reader == "async_graph_result":
        return await tool_graph.result(
            tx,
            parent,
            value["assistant"],
            assistant.tool_calls[0],
            value["slots"][0],
            rows,
        )
    if reader == "tasktx_round":
        return await TaskTx(tx, parent["conversationId"], parent["id"]).tool_round()
    if reader == "tasktx_call":
        return await TaskTx(
            tx, parent["conversationId"], value["slots"][1]["taskId"]
        ).model_tool_call()
    conversations = [
        r.to_tree() for r in store.scan_conversations(ConversationQuery({}), 100).items
    ]
    return await tool_graph.reconcile(tx, rows, entries, conversations)


async def produce_normal_malformed_sdk(root, monkeypatch):
    """Real argument normalization saves a normal receipt1 with intent=None."""
    from pipy_harness.native.durable import completed_values

    class MalformedWire(MultiRound):
        def post_json(self, url, **kwargs):
            response = super().post_json(url, **kwargs)
            if len(self.calls) == 1:
                response.body["choices"][0]["message"]["tool_calls"][0]["function"][
                    "arguments"
                ] = "{"
            return response

    prepared, invoked, receipts, captures = [], [], [], []
    wire = MalformedWire((("malformed", "second"), ()))

    class Port:
        def __init__(self, name):
            self.name = name
            self.definition = ToolDefinition(
                name, name, {"type": "object", "properties": {"x": {"type": "integer"}}}
            )

        def prepare_arguments(self, arguments):
            prepared.append(self.name)
            return arguments

        def invoke(self, request, context):
            invoked.append(self.name)
            return ToolExecutionResult(request.tool_request_id, "normal result")

    ports = [Port("malformed"), Port("second")]
    template = snapshot_provider_request(
        dataclasses.replace(
            seed(root).request,
            user_prompt="",
            messages=(),
            available_tools=tuple(p.definition for p in ports),
        )
    )
    holder = {}
    actual_settle = tool_execution._Tool.settle

    async def observe_settle(self, record, runtime, context):
        product = holder["product"]
        saved = await product._session.aread_on_line(
            lambda: completed_values.document(
                product._session._owner.storage,
                tool_execution.RECEIPT_DOC,
                record["conversationId"],
                record["id"],
            )
        )
        receipts.append((record["input"]["callId"], saved))
        await actual_settle(self, record, runtime, context)
        captures.append(
            await product._session.aread_on_line(
                lambda: baseline.world(product._session._owner.storage)
            )
        )

    monkeypatch.setattr(tool_execution._Tool, "settle", observe_settle)
    product = await create_durable_product_session(
        root / "malformed.sqlite",
        workspace=root,
        profile=profile(root, wire),
        tools={
            p.name: tool_execution.ToolRegistration(
                p, "fixture.malformed." + p.name, "1"
            )
            for p in ports
        },
        request_template=template,
    )
    holder["product"] = product
    try:
        await product.submit("input")
        result = await product.resume()
        assert result.status == "terminal" and result.inspection.outcome == "completed"
        closed = await product._session.aread_on_line(
            lambda: baseline.world(product._session._owner.storage)
        )
    finally:
        try:
            await product.close()
        finally:
            monkeypatch.setattr(tool_execution._Tool, "settle", actual_settle)
    assert len(captures) == len(receipts) == 2 and len(wire.calls) == 2
    assert prepared == invoked == ["second"]
    final = captures[-1]
    child = next(
        r
        for r in final["rows"]
        if r["kind"] == "pi.tool" and r["input"]["callId"] == "call:0"
    )
    entry_id = child["state"]["outcome"]["result"]["entryId"]
    assistant = decode_canonical(
        final["entries"][final["value"]["assistant"]].to_tree()["model"][0]
    )
    saved = next(saved for call, saved in receipts if call == "call:0")
    return {
        **final,
        "child": child,
        "entry_id": entry_id,
        "assistant": assistant,
        "saved": saved,
        "result": result,
        "wire": wire,
        "prepared": prepared,
        "invoked": invoked,
        "closed_world": closed,
    }

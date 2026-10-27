"""Actual public producer base and pure result-reader semantic mutants."""

import asyncio
import copy
import json
import logging
import threading
from dataclasses import replace

import pytest
from generation_support import seed
from test_product_session import profile
from test_tool_boundaries import MultiRound, isolated

from pipy_harness.native.agent.messages import AgentToolResultMessage
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable import (
    completed_values,
    inspection_read,
    resolution_policy,
    tool_execution,
    tool_graph,
)
from pipy_harness.native.durable.canonical_codec import (
    decode_canonical,
    encode_canonical,
)
from pipy_harness.native.durable.generation import ROUND_DOC
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.records import EntryRecord, TaskRecord
from pipy_harness.native.durable.scheduler import (
    TaskScheduler,
    TaskTx,
    UnsupportedTaskExecution,
)
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.storage import (
    ConversationQuery,
    DocumentQuery,
    StorageWrite,
)
from pipy_harness.native.durable.tool_execution import ToolRegistration
from pipy_harness.native.durable.tool_values import entry_tool_output
from pipy_harness.native.tools.base import ToolDefinition, ToolExecutionResult
from pipy_harness.sdk import create_durable_product_session


def world(store):
    rows = inspection_read.tasks(store)
    parent = next(r for r in rows if r["kind"] == "pi.generation")
    docs = []
    scopes = [
        {"kind": "session"},
        {"kind": "conversation", "conversationId": parent["conversationId"]},
    ] + [{"kind": "task", "taskId": row["id"]} for row in rows]
    for scope in scopes:
        for record in store.scan_documents(
            DocumentQuery({"scope": scope, "at": "current"}), 100
        ).items:
            material = store.document(record.id)
            if material is not None:
                docs.append(material.to_tree())
    return {
        "rows": rows,
        "parent": parent,
        "entries": inspection_read.entries(store, parent["conversationId"]),
        "value": completed_values.document(
            store, ROUND_DOC, parent["conversationId"], parent["id"]
        ),
        "documents": docs,
        "conversations": [
            r.to_tree()
            for r in store.scan_conversations(ConversationQuery({}), 100).items
        ],
    }


@pytest.fixture(scope="module")
def actual_fact2(tmp_path_factory):
    root = tmp_path_factory.mktemp("actual-fact2")
    monkeypatch = pytest.MonkeyPatch()
    isolated(monkeypatch, root)
    invoked, prepared, captures = [], [], []
    wire = MultiRound((("first", "second"), ()))
    path = root / "session.sqlite"
    original_settle = tool_execution._Tool.settle
    holder = {}

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
            if self.name == "first":
                context.output_sink("nonempty retained text")
                context.report_details({"strict": [None, False, 0, -0.0]})
                context.report_diagnostic(
                    severity="warning", message="retained diagnostic", code="fixture"
                )
                raise ValueError("synthetic private invocation failure")
            return ToolExecutionResult(
                request.tool_request_id, "second normal result", details={"normal": [1]}
            )

    async def settle(self, record, runtime, context):
        await original_settle(self, record, runtime, context)

        def capture():
            product = holder["product"]
            store = product._session._owner.storage
            captures.append({**world(store), "tool_id": runtime.task_id})

        await holder["product"]._session.aread_on_line(capture)

    monkeypatch.setattr(tool_execution._Tool, "settle", settle)
    ports = [Port("first"), Port("second")]
    template = snapshot_provider_request(
        replace(
            seed(root).request,
            user_prompt="",
            messages=(),
            available_tools=tuple(p.definition for p in ports),
        )
    )

    async def produce():
        product = await create_durable_product_session(
            path,
            workspace=root,
            profile=profile(root, wire),
            tools={
                p.name: ToolRegistration(p, "fixture." + p.name, "1") for p in ports
            },
            request_template=template,
        )
        holder["product"] = product
        try:
            await product.submit("input")
            result = await product.resume()
            assert (
                result.status == "terminal" and result.inspection.outcome == "completed"
            )
            assert len(result.completed.rounds) == 1
            holder["final_world"] = await product._session.aread_on_line(
                lambda: world(product._session._owner.storage)
            )
        finally:
            await product.close()

    try:
        asyncio.run(asyncio.wait_for(produce(), 20))
    finally:
        monkeypatch.undo()
    assert invoked == prepared == ["first", "second"]
    assert len(wire.calls) == 2 and len(captures) == 2
    final = captures[-1]
    assert len(final["value"]["slots"]) == 2
    assistant_id = final["value"]["assistant"]
    assistant = decode_canonical(final["entries"][assistant_id].to_tree()["model"][0])
    child = next(
        r
        for r in final["rows"]
        if r["kind"] == "pi.tool"
        and r["input"]["callId"] == assistant.tool_calls[0].provider_correlation_id
    )
    assert child["state"]["outcome"]["status"] == "failed"
    id = child["state"]["outcome"]["result"]["entryId"]
    found = final["entries"][id]
    assert set(found.to_tree()["data"]) == {"nativeToolReceipt"}
    assert found.to_tree()["data"]["nativeToolReceipt"]["version"] == 2
    assert isinstance(
        decode_canonical(found.to_tree()["model"][0]), AgentToolResultMessage
    )
    baseline_counts = (len(invoked), len(prepared), len(wire.calls))
    (root / "fixture-witness.json").write_text(
        json.dumps(
            {
                "invoked": invoked,
                "prepared": prepared,
                "provider_calls": len(wire.calls),
                "parent_id": final["parent"]["id"],
                "first_child_id": child["id"],
                "first_entry_id": id,
                "model_arena_count": len(found.to_tree()["model"]),
                "receipt_version": found.to_tree()["data"]["nativeToolReceipt"][
                    "version"
                ],
            },
            indent=2,
        )
    )
    result = {
        **final,
        "root": root,
        "path": path,
        "assistant": assistant,
        "child": child,
        "entry_id": id,
        "invoked": invoked,
        "prepared": prepared,
        "wire": wire,
        "baseline_counts": baseline_counts,
        "open_world": captures[0],
        "closed_world": holder["final_world"],
        "ports": ports,
    }
    yield result
    assert (len(invoked), len(prepared), len(wire.calls)) == baseline_counts


def _mutate_0(mutation, tree, saved, fact, parent, child):
    if mutation in ("extra_model_arena", "unknown_extra_arena"):
        extra = copy.deepcopy(tree["model"][0])
        if mutation == "unknown_extra_arena":
            extra["codec_version"] = 999
        tree["model"].append(extra)
        return True
    if mutation == "duplicate_carrier":
        tree["data"]["nativeToolOutput"] = copy.deepcopy(fact)
        return True
    if mutation == "missing_carrier":
        tree["data"] = {}
        return True
    if mutation == "receipt_version":
        saved["version"] = 3
        return True
    if mutation == "fact_version":
        fact["version"] = 3
        return True
    if mutation == "receipt_fact_mismatch":
        saved["version"] = 1
        return True
    return False


def _mutate_1(mutation, tree, saved, fact, parent, child):
    if mutation == "wrong_call":
        saved["call"]["callId"] = "foreign-call"
        saved["intent"]["call"]["callId"] = "foreign-call"
        return True
    if mutation == "wrong_key":
        saved["key"] += "-foreign"
        return True
    if mutation == "foreign_assistant":
        saved["call"]["assistant"] += 1000
        saved["intent"]["call"]["assistant"] += 1000
        return True
    if mutation == "foreign_cid":
        tree["conversationId"] += 1000
        return True
    if mutation == "wrong_by_task":
        tree["byTaskId"] += 1000
        return True
    if mutation == "wrong_parent":
        parent["id"] += 1000
        return True
    return False


def _mutate_2(mutation, tree, saved, fact, parent, child):
    if mutation == "wrong_owner":
        child["owner"] += 1000
        return True
    if mutation == "wrong_input_call":
        child["input"]["callId"] = "foreign-call"
        return True
    if mutation == "wrong_kind_version":
        child["version"] += 10
        return True
    if mutation in ("model_details", "model_control", "recorded_v2_positive"):
        message = decode_canonical(tree["model"][0])
        version = tree["model"][0]["codec_version"]
        if mutation == "model_details":
            message = replace(message, details={"tampered": [0]})
        elif mutation == "model_control":
            message = replace(message, terminate=True)
        else:
            version = 2
        tree["model"][0] = encode_canonical(message, codec_version=version)
        return True
    if mutation == "ending":
        fact["ending"], fact["recovery"] = ("completed", None)
        return True
    if mutation == "interrupted_cause":
        fact["recovery"]["reason"] = "interrupted"
        return True
    return False


def _mutate_3(mutation, tree, saved, fact, parent, child):
    if mutation == "unknown_cause":
        fact["recovery"]["category"] = "future_unknown"
        return True
    if mutation in ("raw_details", "raw_terminate", "raw_cancelled"):
        outcome = decode_canonical(fact["outcome"])
        if mutation == "raw_cancelled":
            outcome = replace(outcome, cancelled=True)
        else:
            message = replace(
                outcome.result,
                **{"terminate": True}
                if mutation == "raw_terminate"
                else {"details": {"tampered": [0]}},
            )
            outcome = replace(outcome, result=message)
        fact["outcome"] = encode_canonical(
            outcome, codec_version=fact["outcome"]["codec_version"]
        )
        return True
    if mutation == "abort_requested":
        child["abortRequested"] = True
        return True
    if mutation == "background":
        child["background"] = True
        return True
    if mutation == "abandon_on_restart":
        child["abandonOnRestart"] = True
        return True
    if mutation == "extra_terminal_error":
        child["state"]["outcome"]["error"]["detail"] = {"not": "allowed"}
        return True
    return False


RESULT_PAYLOADS = {
    "entry_id_array": {"kind": "model", "entryId": []},
    "entry_id_object": {"kind": "model", "entryId": {}},
    "entry_id_null": {"kind": "model", "entryId": None},
    "entry_id_boolean": {"kind": "model", "entryId": True},
    "entry_id_float": {"kind": "model", "entryId": 1.0},
    "entry_id_string": {"kind": "model", "entryId": "1"},
    "entry_id_missing": {"kind": "model"},
    "entry_id_negative": {"kind": "model", "entryId": -1},
    "entry_id_unsafe_integer": {"kind": "model", "entryId": 9007199254740992},
    "result_null": None,
    "result_string": "schema-valid primitive result",
    "result_array": ["schema-valid result"],
    "result_boolean": True,
    "result_number": 7,
}


def _mutate_4(mutation, tree, saved, fact, parent, child):
    if mutation in RESULT_PAYLOADS:
        child["state"]["outcome"]["result"] = copy.deepcopy(RESULT_PAYLOADS[mutation])
        return True
    if mutation == "extra_result_key":
        child["state"]["outcome"]["result"]["extra"] = True
        return True
    if mutation == "completed_task_failed_fact":
        child["state"]["outcome"] = {
            "status": "completed",
            "result": child["state"]["outcome"]["result"],
        }
        return True
    if mutation in ("failed_without_result", "aborted", "faulted", "orphaned"):
        terminal = (
            {"status": "failed", "error": {"message": "generic failed"}}
            if mutation == "failed_without_result"
            else {"status": "aborted"}
            if mutation == "aborted"
            else {"status": "faulted", "error": {"message": "generic faulted"}}
            if mutation == "faulted"
            else {"status": "orphaned", "reason": "generic orphaned"}
        )
        child["state"] = {"status": "terminal", "outcome": terminal}
        return True
    if mutation != "none":
        raise AssertionError(mutation)
        return True
    return False


def views(base, mutation):
    rows = copy.deepcopy(base["rows"])
    parent = next(r for r in rows if r["id"] == base["parent"]["id"])
    child = next(r for r in rows if r["id"] == base["child"]["id"])
    entries = dict(base["entries"])
    tree = entries[base["entry_id"]].to_tree()
    saved = tree["data"]["nativeToolReceipt"]
    fact = saved["nativeToolOutput"]
    for mutate in (_mutate_0, _mutate_1, _mutate_2, _mutate_3, _mutate_4):
        if mutate(mutation, tree, saved, fact, parent, child):
            break
    entries[base["entry_id"]] = EntryRecord(tree)
    for row in rows:
        TaskRecord(row)
    return parent, rows, entries


def read(base, reader, mutation="none"):
    parent, rows, entries = views(base, mutation)
    value, assistant = base["value"], base["assistant"]
    call, slot = assistant.tool_calls[0], value["slots"][0]
    if reader == "child_result_id":
        return tool_graph.child_result_id(
            parent, value["assistant"], call, slot, rows, entries
        )
    if reader == "completed_prefix":
        return completed_values._prefix(
            parent, value["assistant"], value["slots"], rows, entries
        )
    if reader == "completed_closed_links":
        index = {
            "rounds": {
                str(value["assistant"]): [
                    next(row for row in rows if row["id"] == s["taskId"])["state"][
                        "outcome"
                    ]["result"]["entryId"]
                    for s in value["slots"]
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


READERS = [
    "child_result_id",
    "completed_prefix",
    "completed_closed_links",
    "inspection_terminate",
    "resolution_prefix",
]


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("version", ["none", "recorded_v2_positive"])
def test_actual_fact2_positive_all_selected_readers(actual_fact2, reader, version):
    read(actual_fact2, reader, version)


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize(
    "mutation",
    [
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
        "ending",
        "interrupted_cause",
        "unknown_cause",
        "raw_details",
        "raw_terminate",
        "raw_cancelled",
        "abort_requested",
        "background",
        "abandon_on_restart",
        "extra_terminal_error",
        "extra_result_key",
        "completed_task_failed_fact",
    ],
)
def test_schema_valid_carrier_or_model_mutant_refuses(actual_fact2, reader, mutation):
    views(actual_fact2, mutation)
    with pytest.raises(ValueError):
        read(actual_fact2, reader, mutation)


def clone_memory(base, world_name, mutation="none", *, waiting=False):
    source = base[world_name]
    changed = {
        **base,
        "rows": source["rows"],
        "parent": source["parent"],
        "entries": source["entries"],
    }
    parent, rows, entries = views(changed, mutation)
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


@pytest.mark.parametrize(
    "reader",
    [
        "async_graph_result",
        "tasktx_round",
        "tasktx_call",
        "reconcile",
        "completed_capture",
    ],
)
@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "extra_model_arena",
        "unknown_extra_arena",
        "duplicate_carrier",
        "model_details",
        "wrong_key",
        "interrupted_cause",
    ],
)
def test_actual_session_reader_migration_on_captured_memory_world(
    actual_fact2, reader, mutation
):
    world_name = (
        "open_world"
        if reader == "tasktx_call"
        else "closed_world"
        if reader == "completed_capture"
        else None
    )
    if world_name is None:
        # Last settlement world has both genuine outcomes and a still-live round doc.
        actual_fact2["settled_world"] = {
            k: actual_fact2[k]
            for k in (
                "rows",
                "parent",
                "entries",
                "value",
                "documents",
                "conversations",
            )
        }
        world_name = "settled_world"
    store, parent, rows, entries = clone_memory(
        actual_fact2, world_name, mutation, waiting=reader == "reconcile"
    )
    session = Session(store)
    value, assistant = actual_fact2["value"], actual_fact2["assistant"]
    accepted = mutation == "none"

    async def run():
        async def consume(tx):
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
                return await TaskTx(
                    tx, parent["conversationId"], parent["id"]
                ).tool_round()
            if reader == "tasktx_call":
                return await TaskTx(
                    tx, parent["conversationId"], value["slots"][1]["taskId"]
                ).model_tool_call()
            conversations = [
                r.to_tree()
                for r in store.scan_conversations(ConversationQuery({}), 100).items
            ]
            return await tool_graph.reconcile(tx, rows, entries, conversations)

        if reader == "completed_capture":
            call = session.aread_on_line(
                lambda: completed_values.capture(
                    session._owner.storage, parent["conversationId"]
                )
            )
        else:
            call = session.acommit(consume)
        if reader == "reconcile":
            output = await call
            state = next(row for row in output if row["id"] == parent["id"])["state"]
            assert (state["status"] == "pending") is accepted
        elif accepted:
            assert await call is not None
        else:
            with pytest.raises((ValueError, RuntimeError)):
                await call

    try:
        asyncio.run(run())
    finally:
        session.close()


@pytest.mark.parametrize(
    "mutation", ["failed_without_result", "aborted", "faulted", "orphaned"]
)
def test_generic_terminal_child_has_no_model_result_authority(actual_fact2, mutation):
    parent, rows, entries = views(actual_fact2, mutation)
    value, assistant = actual_fact2["value"], actual_fact2["assistant"]
    assert (
        tool_graph.child_result_id(
            parent,
            value["assistant"],
            assistant.tool_calls[0],
            value["slots"][0],
            rows,
            entries,
        )
        is None
    )
    visible, _ = completed_values._prefix(
        parent, value["assistant"], value["slots"], rows, entries
    )
    assert actual_fact2["entry_id"] not in visible  # held prefix, not a thrown error
    assert (
        inspection_read.terminate_index(parent, value, assistant, rows, entries) is None
    )
    with pytest.raises(ValueError):
        resolution_policy._prefix(
            parent, value, assistant, rows, entries, value["slots"][1]["taskId"]
        )
    for reader in ("tasktx_call", "reconcile", "completed_capture"):
        test_actual_session_reader_migration_on_captured_memory_world(
            actual_fact2, reader, mutation
        )


def test_actual_normal_fact1_and_recorded_v1_are_not_given_receipt2_duty(actual_fact2):
    base = actual_fact2
    slot, call = base["value"]["slots"][1], base["assistant"].tool_calls[1]
    child = next(row for row in base["rows"] if row["id"] == slot["taskId"])
    id = child["state"]["outcome"]["result"]["entryId"]
    tree = base["entries"][id].to_tree()
    assert set(tree["data"]) == {"nativeToolOutput", "call", "key"}
    full, outcome, _, _ = entry_tool_output(tree["data"])
    assert full["version"] == 1 and full["outcome"]["codec_version"] == 1
    assert encode_canonical(outcome, codec_version=1) == full["outcome"]
    assert (
        encode_canonical(decode_canonical(tree["model"][0]), codec_version=1)
        == tree["model"][0]
    )
    assert (
        tool_graph.child_result_id(
            base["parent"],
            base["value"]["assistant"],
            call,
            slot,
            base["rows"],
            base["entries"],
        )
        == id
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "extra_model_arena",
        "unknown_extra_arena",
        "wrong_key",
        "interrupted_cause",
    ],
)
def test_real_scheduler_runtime_and_tasktx_admission_without_invoking_ports(
    actual_fact2, mutation
):
    base = actual_fact2
    store, parent, rows, entries = clone_memory(base, "open_world", mutation)
    session = Session(store)
    decision = threading.Event()
    observed, errors = [], []

    async def probe(record, runtime, context):
        try:

            async def checked(tx, current):
                model = await tx.model_tool_call()
                observed.append((current["id"], model.call.tool_name))

            await runtime.commit(checked)
        except Exception as error:
            logging.exception("Unexpected reader probe failure")
            errors.append(type(error).__name__)
        finally:
            decision.set()
        await context.signal.wait()

    # The real scheduler reserves a real model-tool invocation and constructs
    # TaskRuntime/TaskContext. Only its phase handler is this effect-free probe.
    definition = tool_execution.define_tool_task(
        {
            port.name: ToolRegistration(port, "fixture." + port.name, "1")
            for port in base["ports"]
        },
        workspace_root=base["root"],
    )
    definition = replace(
        definition, phases={phase: probe for phase in definition.phases}
    )
    scheduler = TaskScheduler(session, [definition])
    reserve = scheduler._reserve

    async def idle():
        invocation = await reserve()
        if invocation is None:
            decision.set()
        return invocation

    scheduler._reserve = idle
    try:
        scheduler.open()
        scheduler.resume()
        assert decision.wait(8), session.failure
        assert not errors
        assert bool(observed) is (mutation == "none")
        if observed:
            assert observed == [(base["value"]["slots"][1]["taskId"], "second")]
    finally:
        session.close(timeout=8)
    assert not scheduler._thread.is_alive()
    assert (
        len(base["invoked"]),
        len(base["prepared"]),
        len(base["wire"].calls),
    ) == base["baseline_counts"]


@pytest.mark.parametrize("mutation", RESULT_PAYLOADS)
@pytest.mark.parametrize("status", ["terminal", "completing"])
@pytest.mark.parametrize("reader", READERS)
def test_schema_valid_primitive_result_pure_consumer_refusal(
    actual_fact2, mutation, status, reader
):
    parent, rows, entries = views(actual_fact2, mutation)
    child = next(row for row in rows if row["id"] == actual_fact2["child"]["id"])
    child["state"]["status"] = status
    TaskRecord(child)  # The malformed model reference is a valid storage record.
    value, assistant = actual_fact2["value"], actual_fact2["assistant"]
    call, slot = assistant.tool_calls[0], value["slots"][0]

    def consume():
        if reader == "child_result_id":
            return tool_graph.child_result_id(
                parent, value["assistant"], call, slot, rows, entries
            )
        if reader == "completed_prefix":
            return completed_values._prefix(
                parent, value["assistant"], value["slots"], rows, entries
            )
        if reader == "completed_closed_links":
            ids = [
                next(row for row in actual_fact2["rows"] if row["id"] == s["taskId"])[
                    "state"
                ]["outcome"]["result"]["entryId"]
                for s in value["slots"]
            ]
            return completed_values._closed_links(
                entries, rows, {"rounds": {str(value["assistant"]): ids}}
            )
        if reader == "inspection_terminate":
            return inspection_read.terminate_index(
                parent, value, assistant, rows, entries
            )
        return resolution_policy._prefix(
            parent, value, assistant, rows, entries, value["slots"][1]["taskId"]
        )

    if status == "completing" and reader in ("child_result_id", "inspection_terminate"):
        assert consume() is None
    elif status == "completing" and reader == "completed_prefix":
        visible, _ = consume()
        assert visible == []
    else:
        with pytest.raises(
            ValueError,
            match=(
                "^Expected exact safe integer in the selected role domain$"
                if mutation.startswith("entry_id_") and status == "terminal"
                else "incompatible"
            ),
        ):
            consume()


@pytest.mark.parametrize("mutation", RESULT_PAYLOADS)
@pytest.mark.parametrize("status", ["terminal", "completing"])
@pytest.mark.parametrize(
    "reader",
    [
        "async_graph_result",
        "tasktx_round",
        "tasktx_call",
        "reconcile",
        "completed_capture",
    ],
)
def test_schema_valid_primitive_result_actual_session_consumers(
    actual_fact2, mutation, status, reader
):
    base = actual_fact2
    base["settled_world"] = {
        k: base[k]
        for k in ("rows", "parent", "entries", "value", "documents", "conversations")
    }
    world_name = (
        "open_world"
        if reader == "tasktx_call"
        else "closed_world"
        if reader == "completed_capture"
        else "settled_world"
    )
    store, parent, rows, entries = clone_memory(
        base, world_name, mutation, waiting=reader == "reconcile"
    )
    child = next(row for row in rows if row["id"] == base["child"]["id"])
    child["state"]["status"] = status
    TaskRecord(child)
    store.commit((StorageWrite({"type": "task", "value": child}),))
    session = Session(store)
    value, assistant = base["value"], base["assistant"]

    async def run():
        async def consume(tx):
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
                return await TaskTx(
                    tx, parent["conversationId"], parent["id"]
                ).tool_round()
            if reader == "tasktx_call":
                return await TaskTx(
                    tx, parent["conversationId"], value["slots"][1]["taskId"]
                ).model_tool_call()
            conversations = [
                row.to_tree()
                for row in store.scan_conversations(ConversationQuery({}), 100).items
            ]
            return await tool_graph.reconcile(tx, rows, entries, conversations)

        if reader == "reconcile":
            result = await session.acommit(consume)
            assert (
                next(row for row in result if row["id"] == parent["id"])["state"][
                    "status"
                ]
                == "waiting"
            )
        elif reader == "tasktx_call":
            with pytest.raises(
                UnsupportedTaskExecution, match="Model tool authority is incompatible"
            ):
                await session.acommit(consume)
        else:
            with pytest.raises(
                ValueError,
                match=(
                    "^Expected exact safe integer in the selected role domain$"
                    if mutation.startswith("entry_id_")
                    and reader in ("async_graph_result", "tasktx_round")
                    else "incompatible"
                ),
            ):
                if reader == "completed_capture":
                    await session.aread_on_line(
                        lambda: completed_values.capture(
                            session._owner.storage, parent["conversationId"]
                        )
                    )
                else:
                    await session.acommit(consume)
        assert session.failure is None
        assert (
            await session.aread_on_line(lambda: "healthy after fixed refusal")
            == "healthy after fixed refusal"
        )

    try:
        asyncio.run(run())
    finally:
        session.close()

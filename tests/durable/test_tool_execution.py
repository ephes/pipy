"""DE08 actual generation-owned tool execution, receipts and ordered continuation."""

import threading
from dataclasses import replace

import pytest
from generation_support import Wire, admit, definition, seed

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.agent.usage import AgentTokenPricing
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.generation import USAGE_DOC
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import EntryQuery
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.native.tools.base import ToolDefinition, ToolExecutionResult


class KeyedTool:
    def __init__(self, name, calls):
        self.definition = ToolDefinition(
            name,
            name,
            {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "prepared": {"type": "boolean"},
                },
            },
        )
        self.calls = calls
        self.prepared = 0
        self.applications = {}

    def prepare_arguments(self, raw):
        self.prepared += 1
        return {**raw, "prepared": True}

    def invoke(self, request, context):
        self.calls.append((request.tool_name, request.arguments, context.call_key))
        assert request.arguments == {"x": 1, "prepared": True}
        self.applications.setdefault(context.call_key, len(self.applications) + 1)
        return ToolExecutionResult(
            request.tool_request_id,
            "actual:" + request.tool_name,
            provider_correlation_id=request.provider_correlation_id,
            details={"application": self.applications[context.call_key]},
            structured_content=None,
        )


class RoundWire(Wire):
    def post_json(self, url, **kwargs):
        if self.calls:
            self.tool_calls = ()
        return super().post_json(url, **kwargs)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_actual_parent_owned_children_parent_order_and_new_request(
    tmp_path, monkeypatch, backend
):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    store = (
        MemoryStorage()
        if backend == "memory"
        else open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="create")
    )
    session = Session(store)
    calls = []
    one, two = KeyedTool("one", calls), KeyedTool("two", calls)
    tool = define_tool_task(
        {
            p.definition.name: ToolRegistration(
                p, "fixture." + p.definition.name, "1", "safe"
            )
            for p in (one, two)
        },
        workspace_root=tmp_path,
    )
    wire = RoundWire(("two", "unavailable", "one"))
    generation = definition(tmp_path, wire, pricing=AgentTokenPricing(1, 2, 3, 4))
    snapshot = snapshot_provider_request(
        replace(
            seed(tmp_path).request, available_tools=(one.definition, two.definition)
        )
    )
    conversation, _, parent = admit(session, generation, snapshot)
    done = threading.Event()
    admissions = []

    def observe(publication):
        rows = [
            change.to_tree()["value"]
            for change in publication.changes
            if hasattr(change, "to_tree") and change.to_tree()["type"] == "task"
        ]
        for row in rows:
            if row["id"] == parent and row["state"]["status"] == "waiting":
                admissions.append(
                    (
                        row,
                        [child for child in rows if child["kind"] == "pi.tool"],
                        len(calls),
                    )
                )
            if row["id"] == parent and row["state"]["status"] == "terminal":
                done.set()

    session.subscribe_commits(observe)
    scheduler = TaskScheduler(session, [generation, tool])
    try:
        scheduler.open()
        scheduler.resume()
        assert done.wait(10), (
            [
                (
                    r.id,
                    r.to_tree()["kind"],
                    r.to_tree()["state"]["status"],
                    r.to_tree()["state"].get("checkpoint", {}).get("phase"),
                    r.to_tree()["state"].get("outcome"),
                )
                for r in scheduler.inspect()
            ],
            calls,
            session.failure,
        )
        rows = [row.to_tree() for row in scheduler.inspect()]
        final = next(row for row in rows if row["id"] == parent)
        assert final["state"]["outcome"]["status"] == "completed", final
        assert (
            len(admissions) == 1
            and len(admissions[0][1]) == 2
            and admissions[0][2] == 0
        )
        assert [name for name, _, _ in calls] == ["two", "one"]
        assert len({key for _, _, key in calls}) == 2
        assert one.prepared == two.prepared == 1
        assert len(one.applications) == len(two.applications) == 1
        assert len(wire.calls) == 2
        messages = wire.calls[1][1]["body"]["messages"]
        offered_ids = [
            call["id"]
            for message in messages
            if message["role"] == "assistant"
            for call in message.get("tool_calls", ())
        ]
        assert [
            m["tool_call_id"] for m in messages if m["role"] == "tool"
        ] == offered_ids
        assert [
            call["function"]["name"]
            for message in messages
            if message["role"] == "assistant"
            for call in message.get("tool_calls", ())
        ] == ["two", "unavailable", "one"]
        entries = session.commit(
            lambda tx: tx.scan_entries(
                EntryQuery({"conversationId": conversation}), 100
            )
        ).items
        results = [
            entry.to_tree()
            for entry in sorted(entries, key=lambda item: item.id)
            if entry.to_tree()["kind"] == "pi.tool-result"
        ]
        assert (
            decode_canonical(results[0]["model"][0]).provider_correlation_id == "call:1"
        )  # physical order differs
        assert all(
            row["owner"] == parent and row["state"]["status"] == "terminal"
            for row in rows
            if row["kind"] == "pi.tool"
        )
        ledger = session.snapshot(USAGE_DOC, conversation_id=conversation).value.thaw()
        assert len(ledger["settlements"]) == 2
        usage = ledger["models"]["openai/gpt-test"]
        assert {
            key: usage[key]
            for key in (
                "input",
                "output",
                "cache_read",
                "cache_write",
                "total_tokens",
                "reasoning",
            )
        } == {
            "input": 32,
            "output": 20,
            "cache_read": 8,
            "cache_write": 0,
            "total_tokens": 60,
            "reasoning": 6,
        }
        assert usage["cost"]["total"] == pytest.approx(96 / 1e6)
        assert (
            ledger["tools"] == {}
        )  # Native ToolExecutionResult reports no token usage.
        assistants = [
            entry.to_tree()
            for entry in sorted(entries, key=lambda item: item.id)
            if entry.to_tree()["kind"] == "pi.assistant"
        ]
        identities = [a["data"]["receipt"]["identity"]["request"] for a in assistants]
        assert len(set(identities)) == 2
        assert (
            decode_canonical(
                assistants[1]["data"]["request"]
            ).request.provider_turn_index
            == 1
        )
    finally:
        session.close(timeout=10)

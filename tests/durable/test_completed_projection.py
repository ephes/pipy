"""Real committed facts, ordered tool rounds and isolated async observers."""

import asyncio
import json
import threading
from dataclasses import fields, replace

import pytest
from generation_support import admit, definition, seed
from test_tool_execution import KeyedTool, RoundWire

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentToolResultMessage, AgentUserMessage
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.completed_projection import (
    ProjectionClosed,
    observe_completed,
)
from pipy_harness.native.durable.generation import message_entry
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import EntryQuery
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)


def store_at(tmp_path, backend):
    return (
        MemoryStorage()
        if backend == "memory"
        else open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="create")
    )


def isolate(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))


def assert_public_completion(snapshot, stored):
    # Inspect actual delivered values, not privacy-safe repr implementations.
    assert {field.name for field in fields(snapshot)} == {
        "conversation_id",
        "revision",
        "publication_seq",
        "messages",
        "rounds",
        "outcomes",
        "usage",
    }
    for message in snapshot.messages:
        assert {field.name for field in fields(message)} == {
            "conversation_id",
            "entry_id",
            "commit_seq",
            "by_task_id",
            "canonical",
            "provider_outcome",
            "tool_output",
        }
        row = stored[message.entry_id]
        assert message.canonical == OwnedJson(row["model"][0])
        data = row.get("data", {})
        expected = OwnedJson(data["receipt"]["payload"]) if "receipt" in data else None
        assert message.provider_outcome == expected
        expected_tool = (
            OwnedJson(data["nativeToolOutput"]) if "nativeToolOutput" in data else None
        )
        assert message.tool_output == expected_tool
    for outcome in snapshot.outcomes:
        assert {field.name for field in fields(outcome)} == {
            "task_id",
            "kind",
            "version",
            "outcome",
        }
    assert_private_material_absent(snapshot, stored)


def assert_private_material_absent(snapshot, stored):
    bodies = [outcome.outcome.thaw() for outcome in snapshot.outcomes]
    bodies.append(None if snapshot.usage is None else snapshot.usage.thaw())
    for message in snapshot.messages:
        for owned in (message.canonical, message.provider_outcome, message.tool_output):
            if owned is not None:
                bodies.append(owned.thaw())
    public = json.dumps(bodies)
    for row in stored.values():
        data = row.get("data", {})
        if "receipt" in data:
            material = data["receipt"]["settings"]["material"]
            assert (
                material["version"] == 1
                and material["store_id"]
                and material["material_id"]
            )
            assert material["store_id"] not in public
            assert material["material_id"] not in public


def assert_private_mutants_rejected(snapshot, stored):
    assistant = next(
        message for message in snapshot.messages if message.provider_outcome is not None
    )
    raw = stored[assistant.entry_id]
    assert raw["data"]["receipt"]["settings"] is not None
    # Both are genuine synthetic saved values. A leaked full frame carries
    # settings/private material references; the other is the saved request.
    for forbidden in (raw["data"]["receipt"], raw["data"]["request"]):
        poisoned = replace(
            snapshot,
            messages=tuple(
                replace(message, provider_outcome=OwnedJson(forbidden))
                if message is assistant
                else message
                for message in snapshot.messages
            ),
        )
        with pytest.raises(AssertionError):
            assert_public_completion(poisoned, stored)


def stored_entries(session, cid):
    return {
        entry.id: entry.to_tree()
        for entry in session._owner.storage.scan_entries(
            EntryQuery({"conversationId": cid}), 100
        ).items
    }


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_actual_two_owned_tools_next_answer_and_reconnect(
    tmp_path, monkeypatch, backend
):
    isolate(tmp_path, monkeypatch)
    store = store_at(tmp_path, backend)
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
    wire = RoundWire(("two", "missing", "one"))
    generation = definition(tmp_path, wire)
    snapshot = snapshot_provider_request(
        replace(
            seed(tmp_path).request, available_tools=(one.definition, two.definition)
        )
    )
    cid, _, parent = admit(session, generation, snapshot)
    scheduler = TaskScheduler(session, [generation, tool])
    scheduler.open()
    committed = threading.Event()

    def terminal(publication):
        for change in publication.changes:
            if hasattr(change, "to_tree"):
                tree = change.to_tree()
                if (
                    tree["type"] == "task"
                    and tree["value"]["id"] == parent
                    and tree["value"]["state"]["status"] == "terminal"
                ):
                    committed.set()

    session.subscribe_commits(terminal)

    async def run():
        watch = await observe_completed(session, cid)
        assert len(watch.snapshot.messages) == 1
        assert watch.snapshot.publication_seq is None
        delivered = []
        automatic_terminal = asyncio.Event()

        async def consume(snapshot):
            # A separate line read proves all exposed entries independently
            # exist before the observer acknowledges delivery.
            def check():
                from pipy_harness.native.durable.records import ConversationId, EntryId

                for message in snapshot.messages:
                    lookup = session._owner.storage.entry(
                        EntryId(message.entry_id), conversation_id=ConversationId(cid)
                    )
                    assert lookup.commit_seq == message.commit_seq
                return True

            assert await asyncio.to_thread(lambda: session.read_on_line(check))
            delivered.append(snapshot)
            if any(outcome.task_id == parent for outcome in snapshot.outcomes):
                automatic_terminal.set()

        watch.start(consume)
        scheduler.resume()
        assert await asyncio.to_thread(committed.wait, 10), (
            [r.to_tree() for r in scheduler.inspect()],
            session.failure,
        )
        await asyncio.wait_for(automatic_terminal.wait(), 5)
        final = await asyncio.wait_for(watch.drain(), 10)
        assert delivered[-1] is final
        assert [
            m.decode().provider_correlation_id
            for m in final.messages
            if type(m.decode()) is AgentToolResultMessage
        ] == ["call:0", "call:1", "call:2"]
        assert len(final.messages) == 6
        assert len(final.rounds) == 1
        assert len(final.outcomes) == 3
        assert all(o.outcome.thaw()["status"] == "completed" for o in final.outcomes)
        assert len(final.usage.thaw()["settlements"]) == 2
        assert final.usage.thaw()["models"]["openai/gpt-test"]["total_tokens"] == 60
        assert len(wire.calls) == 2 and len(calls) == 2
        assert sum(m.tool_output is not None for m in final.messages) == 2
        assert sum(m.provider_outcome is not None for m in final.messages) == 2

        stored = await asyncio.to_thread(
            lambda: session.read_on_line(lambda: stored_entries(session, cid))
        )
        assert_public_completion(final, stored)
        assert_private_mutants_rejected(final, stored)
        watch.stop()
        assert await watch.wait_closed() == "stopped"
        fresh = await observe_completed(session, cid)
        assert fresh.snapshot.messages == final.messages
        assert fresh.snapshot.rounds == final.rounds
        assert len(wire.calls) == 2 and len(calls) == 2
        fresh.stop()
        await fresh.wait_closed()

    try:
        asyncio.run(run())
    finally:
        session.close(timeout=10)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_slow_throwing_consumers_coalesce_and_do_not_hold_mutation_line(
    tmp_path, backend
):
    session = Session(store_at(tmp_path, backend))
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id

    async def append(text):
        return await session.acommit(
            lambda tx: tx.append_entry(
                cid, message_entry(AgentUserMessage(ProductContent(text)))
            )
        )

    async def run():
        slow = await observe_completed(session, cid)
        throwing = await observe_completed(session, cid)
        entered, release = asyncio.Event(), asyncio.Event()
        deliveries = []

        async def blocked(snapshot):
            deliveries.append(snapshot)
            entered.set()
            await release.wait()

        async def throws(snapshot):
            raise RuntimeError("private payload must stay contained")

        slow.start(blocked)
        throwing.start(throws)
        await append("first")
        await asyncio.wait_for(entered.wait(), 5)
        assert await throwing.wait_closed() == "listener_error"
        for i in range(12):
            await append(str(i))
        assert len(deliveries) == 1 and session.failure is None
        drain = asyncio.create_task(slow.drain())
        await asyncio.sleep(0.03)
        assert not drain.done()
        release.set()
        final = await asyncio.wait_for(drain, 5)
        assert len(final.messages) == 13
        assert len(deliveries) <= 3
        assert slow._pending is None
        slow.stop()
        await slow.wait_closed()
        await append("after stop")

    try:
        asyncio.run(run())
    finally:
        session.close(timeout=5)


def test_drain_self_reentry_cancel_and_stop_do_not_join_consumer(tmp_path):
    session = Session(MemoryStorage())
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id

    async def run():
        watch = await observe_completed(session, cid)
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []

        async def listener(snapshot):
            with pytest.raises(RuntimeError, match="cannot drain itself"):
                await watch.drain()
            entered.set()
            await release.wait()
            seen.append(snapshot)

        watch.start(listener)
        drain = asyncio.create_task(watch.drain())
        await entered.wait()
        drain.cancel()
        with pytest.raises(asyncio.CancelledError):
            await drain
        watch.stop()
        assert await asyncio.wait_for(watch.wait_closed(), 1) == "stopped"
        assert not seen
        await asyncio.wait_for(session.aclose(), 2)
        release.set()
        await asyncio.sleep(0)
        with pytest.raises(ProjectionClosed):
            await watch.drain()

    try:
        asyncio.run(run())
    finally:
        session.close(timeout=5)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_actual_early_unavailable_entry_is_hidden_behind_held_owned_slot(
    tmp_path, monkeypatch, backend
):
    isolate(tmp_path, monkeypatch)
    session = Session(store_at(tmp_path, backend))
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Held(KeyedTool):
        def invoke(self, request, context):
            entered.set()
            assert release.wait(10)
            return super().invoke(request, context)

    first, last = Held("first", calls), KeyedTool("last", calls)
    tool = define_tool_task(
        {
            p.definition.name: ToolRegistration(
                p, "fixture." + p.definition.name, "1", "safe"
            )
            for p in (first, last)
        },
        workspace_root=tmp_path,
    )
    wire = RoundWire(("first", "missing", "last"))
    generation = definition(tmp_path, wire)
    cid, _, parent = admit(
        session,
        generation,
        snapshot_provider_request(
            replace(
                seed(tmp_path).request,
                available_tools=(first.definition, last.definition),
            )
        ),
    )
    scheduler = TaskScheduler(session, [generation, tool])
    scheduler.open()

    async def run():
        watch = await observe_completed(session, cid)
        assistant_visible, terminal = asyncio.Event(), asyncio.Event()
        snapshots = []

        async def consume(snapshot):
            snapshots.append(snapshot)
            if len(snapshot.messages) == 2:
                assistant_visible.set()
            if any(outcome.task_id == parent for outcome in snapshot.outcomes):
                terminal.set()

        watch.start(consume)
        scheduler.resume()
        assert await asyncio.to_thread(entered.wait, 5)
        await asyncio.wait_for(assistant_visible.wait(), 5)
        held = await watch.drain()
        assert len(held.messages) == 2 and not held.rounds
        # The unavailable result has physically committed, before the real
        # first tool result, but cannot jump its unresolved saved slot.
        from pipy_harness.native.durable.storage import EntryQuery

        physical = await session.acommit(
            lambda tx: tx.scan_entries(EntryQuery({"conversationId": cid}), 100)
        )
        assert sum(e.to_tree()["kind"] == "pi.tool-result" for e in physical.items) == 1
        release.set()
        await asyncio.wait_for(terminal.wait(), 5)
        final = await watch.drain()
        assert [
            m.decode().tool_name
            for m in final.messages
            if type(m.decode()) is AgentToolResultMessage
        ] == ["first", "missing", "last"]
        assert len(final.rounds) == 1
        watch.stop()
        await watch.wait_closed()

    try:
        asyncio.run(run())
    finally:
        release.set()
        session.close(timeout=10)


def test_eager_listener_delivers_two_real_sqlite_drains(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    session = Session(store_at(tmp_path, "sqlite"))
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id

    async def run():
        loop = asyncio.get_running_loop()
        previous = loop.get_task_factory()
        seen = []
        watch = await observe_completed(session, cid)

        async def listener(snapshot):
            seen.append(snapshot.revision)

        try:
            # Python 3.11 retains the ordinary-factory regression; 3.12+ uses
            # the real global eager factory through both capture and delivery.
            loop.set_task_factory(getattr(asyncio, "eager_task_factory", previous))
            watch.start(listener)
            first = await asyncio.wait_for(watch.drain(), 5)
            second = await asyncio.wait_for(watch.drain(), 5)
            assert seen == [first.revision, second.revision]
            assert second.revision > first.revision
        finally:
            loop.set_task_factory(previous)
            watch.stop()
            await watch.wait_closed()
            await session.aclose()

    try:
        asyncio.run(run())
    finally:
        session.close(timeout=5)


def test_lazy_watch_body_is_not_created_before_cancel_or_factory_refusal(tmp_path):
    session = Session(store_at(tmp_path, "sqlite"))
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id

    async def run():
        loop = asyncio.get_running_loop()
        previous = loop.get_task_factory()
        watch = await observe_completed(session, cid)
        created = []

        async def body():
            pytest.fail("Unadmitted body executed")

        def factory():
            created.append(True)
            return body()

        def refused(loop, coroutine, **kwargs):
            raise RuntimeError("labelled factory refusal")

        try:
            loop.set_task_factory(None)
            work = watch._spawn(factory)
            assert work is not None
            work.cancel()  # No wrapper first step, therefore no inner coroutine.
            with pytest.raises(asyncio.CancelledError):
                await work
            assert not created
            loop.set_task_factory(refused)
            assert watch._spawn(factory) is None
            assert not created
        finally:
            loop.set_task_factory(previous)
            watch.stop()
            await watch.wait_closed()
            await session.aclose()

    try:
        asyncio.run(run())
    finally:
        session.close(timeout=5)

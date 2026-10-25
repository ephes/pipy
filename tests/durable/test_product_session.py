"""Actual public opt-in SQLite lifetime; synthetic native transport only."""

import asyncio

import pytest
from generation_support import SPEC, Wire

from pipy_harness.sdk import (
    DurableProductError,
    DurableProviderProfile,
    UnsupportedDurableCapability,
    create_durable_product_session,
    open_durable_product_session,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


def profile(tmp_path, wire):
    return DurableProviderProfile(
        SPEC,
        auth_path=tmp_path / "auth.json",
        env={},
        runtime_api_key="fixture-key",
        http_client=wire,
    )


def test_empty_created_session_reopens_exactly_without_dispatch(tmp_path):
    wire = Wire()
    path = tmp_path / "product.sqlite"

    async def scenario():
        product = await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools={},
        )
        assert (await product.inspect()).tasks == ()
        await product.close()
        product = await open_durable_product_session(path, workspace=tmp_path, tools={})
        try:
            assert (await product.inspect()).tasks == ()
            with pytest.raises(DurableProductError, match="no_input"):
                await product.resume()
        finally:
            await product.close()
        assert wire.calls == []

    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_atomic_single_input_explicit_resume_terminal_no_auth_reopen(tmp_path):
    wire = Wire()
    path = tmp_path / "product.sqlite"

    async def scenario():
        product = await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools={},
        )
        try:
            admission = await product.submit("input")
            assert wire.calls == []
            assert (
                await product.inspect(admission.generation_task_id)
            ).status == "pending"
            with pytest.raises(DurableProductError, match="already_admitted"):
                await product.submit("input again")
            result = await product.resume()
            assert result.status == "terminal", result.inspection
            assert result.inspection.outcome == "completed", result.inspection
            assert [
                message.decode().content.value for message in result.completed.messages
            ] == ["input", "done"]
            assert len(wire.calls) == 1
        finally:
            await product.close()
        product = await open_durable_product_session(path, workspace=tmp_path, tools={})
        try:
            result2 = await product.resume()
            assert result2.status == "terminal"
            assert result2.inspection.outcome == "completed"
            assert len(wire.calls) == 1
        finally:
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 15))


@pytest.mark.parametrize(
    "capability", ["compact", "fork", "reset", "extensions", "provider"]
)
def test_unsupported_factory_capabilities_refuse_before_store(tmp_path, capability):
    path = tmp_path / "missing" / "product.sqlite"

    async def scenario():
        with pytest.raises(UnsupportedDurableCapability):
            await create_durable_product_session(
                path,
                workspace=tmp_path,
                profile=profile(tmp_path, Wire()),
                **{capability: object()},
            )
        assert not path.parent.exists()

    asyncio.run(scenario())


def tools(root, *, port_type=None, terminate=False):
    from tool_support import Keyed, database

    from pipy_harness.native.durable.tool_execution import ToolRegistration

    database(root)
    ports = [
        (port_type or Keyed)(root, name, terminate=terminate and name == "keyed")
        for name in ("keyed", "later")
    ]
    return {
        p.definition.name: ToolRegistration(p, "fixture." + p.definition.name, "1")
        for p in ports
    }


def test_two_actual_owned_tools_then_next_answer(tmp_path):
    from tool_support import CountedWire, counts

    wire = CountedWire(tmp_path)
    registry = tools(tmp_path)

    async def scenario():
        async with await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools=registry,
        ) as product:
            await product.submit("input")
            result = await product.resume()
            assert result.status == "terminal"
            assert result.inspection.outcome == "completed"
            assert len(result.completed.rounds) == 1
            assert len(result.completed.rounds[0].result_ids) == 2
            assert counts(tmp_path) == {
                "calls": 2,
                "applications": 2,
                "prepares": 2,
                "providers": 2,
            }
            assert [m.decode().content.value for m in result.completed.messages] == [
                "input",
                "done",
                "actual fixture output",
                "actual fixture output",
                "done",
            ]

    asyncio.run(asyncio.wait_for(scenario(), 15))


def test_retained_terminate_is_held_not_completed(tmp_path):
    from tool_support import CountedWire, counts

    registry = tools(tmp_path, terminate=True)
    wire = CountedWire(tmp_path)

    async def scenario():
        async with await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools=registry,
        ) as product:
            await product.submit("input")
            result = await product.resume()
            assert result.status == "held"
            assert result.inspection.status == "waiting"
            assert result.inspection.reason == "retained_terminate"
            assert not result.completed.rounds
            assert counts(tmp_path) == {
                "calls": 1,
                "applications": 1,
                "prepares": 1,
                "providers": 1,
            }
            await product.resume()
            assert counts(tmp_path)["providers"] == 1

    asyncio.run(asyncio.wait_for(scenario(), 15))


def test_generation_retry_derived_seed_explicit_selector_no_sticky_dispatch(tmp_path):
    from pipy_harness.sdk import DurableRunSelection

    wire = Wire(failure=True)
    path = tmp_path / "product.sqlite"

    async def scenario():
        product = await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools={},
        )
        try:
            admission = await product.submit("input")
            failed = await product.resume()
            assert (
                failed.status == "terminal" and failed.inspection.outcome == "faulted"
            )
            attempts = len(wire.calls)
            retry = await product.authorize_retry(
                admission.generation_task_id, nonce="a" * 32
            )
            assert retry == await product.authorize_retry(
                admission.generation_task_id, nonce="a" * 32
            )
            assert len(wire.calls) == attempts
            with pytest.raises(DurableProductError, match="successor_exists"):
                await product.authorize_retry(
                    admission.generation_task_id, nonce="b" * 32
                )
            with pytest.raises(DurableProductError, match="selection_required"):
                await product.resume()
            wire.failure = False
            selected = DurableRunSelection(retry.new_task_id, "a" * 32)
            result = await product.resume(selected)
            assert result.task_id == retry.new_task_id
            assert result.inspection.outcome == "completed"
            assert len(wire.calls) == attempts + 1
        finally:
            await product.close()
        product = await open_durable_product_session(
            path, workspace=tmp_path, selection=selected, tools={}
        )
        try:
            result = await product.resume()
            assert (
                result.task_id == retry.new_task_id
                and result.inspection.outcome == "completed"
            )
        finally:
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_tool_authorized_retry_stays_paused_until_resume(tmp_path):
    from dataclasses import replace

    from tool_support import CountedWire, Keyed, counts

    class InvalidFirst(Keyed):
        def invoke(self, request, context):
            result = super().invoke(request, context)
            if self.definition.name == "keyed" and counts(self.root)["calls"] == 1:
                return replace(result, details={"private": object()})
            return result

    registry = tools(tmp_path, port_type=InvalidFirst)

    async def scenario():
        async with await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, CountedWire(tmp_path)),
            tools=registry,
        ) as product:
            admission = await product.submit("input")
            held = await product.resume()
            assert held.status == "held"
            page = await product.inspect()
            child = next(
                t
                for t in page.tasks
                if t.family == "model_tool" and t.reason == "invalid_result"
            )
            before = counts(tmp_path)
            retry = await product.authorize_retry(child.task_id, nonce="c" * 32)
            assert retry == await product.authorize_retry(child.task_id, nonce="c" * 32)
            assert counts(tmp_path) == before
            finished = await product.resume()
            assert finished.task_id == admission.generation_task_id
            assert finished.inspection.outcome == "completed"
            assert counts(tmp_path)["applications"] == 3

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_derived_generation_retry_preserves_closed_tool_history_and_one_user_input(
    tmp_path,
):
    from tool_support import CountedWire, counts

    from pipy_harness.native.durable.codec import OwnedJson
    from pipy_harness.native.durable.records import TaskId
    from pipy_harness.sdk import DurableRunSelection

    class RoundThenFault(CountedWire):
        allow = False

        def post_json(self, url, **kwargs):
            self.failure = not self.allow and any(
                m["role"] == "tool" for m in kwargs["body"]["messages"]
            )
            return super().post_json(url, **kwargs)

    registry = tools(tmp_path)
    wire = RoundThenFault(tmp_path)

    async def scenario():
        async with await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools=registry,
        ) as product:
            admission = await product.submit("input")
            failed = await product.resume()
            assert (
                failed.inspection.outcome == "faulted"
                and len(failed.completed.rounds) == 1
            )
            before = counts(tmp_path)
            assert before == {
                "calls": 2,
                "applications": 2,
                "prepares": 2,
                "providers": 2,
            }
            source = await product._read(
                lambda: product._session._owner.storage.task(
                    TaskId(admission.generation_task_id)
                ).to_tree()
            )
            retry = await product.authorize_retry(
                admission.generation_task_id, nonce="e" * 32
            )
            newer = await product._read(
                lambda: product._session._owner.storage.task(
                    TaskId(retry.new_task_id)
                ).to_tree()
            )
            assert OwnedJson(source["input"]) == OwnedJson(newer["input"])
            assert counts(tmp_path) == before
            wire.allow = True
            finished = await product.resume(
                DurableRunSelection(retry.new_task_id, "e" * 32)
            )
            assert finished.inspection.outcome == "completed"
            assert counts(tmp_path) == {**before, "providers": 3}
            roles = [
                m["role"]
                for m in wire.calls[-1][1]["body"]["messages"]
                if m["role"] != "system"
            ]
            assert roles == ["user", "assistant", "tool", "tool"]
            assert (
                sum(
                    m.decode().content.value == "input"
                    for m in finished.completed.messages
                )
                == 1
            )
            assert len(finished.completed.usage.thaw()["settlements"]) == 2
            assert any(
                o.task_id == admission.generation_task_id
                and o.outcome.thaw()["status"] == "faulted"
                for o in finished.completed.outcomes
            )
            assert (
                await product.authorize_retry(
                    admission.generation_task_id, nonce="e" * 32
                )
                == retry
            )

    asyncio.run(asyncio.wait_for(scenario(), 15))


def test_held_child_beyond_terminal_page_returns_and_public_inspect_pages(tmp_path):
    from dataclasses import replace

    from tool_support import CountedWire, Keyed, counts

    from pipy_harness.native.durable.generation_transport import owned_call
    from pipy_harness.native.durable.storage import StorageWrite
    from pipy_harness.native.durable.tasks import define_task

    class InvalidFirst(Keyed):
        def invoke(self, request, context):
            return replace(
                super().invoke(request, context), details={"private": object()}
            )

    async def never(*args):
        raise AssertionError("Terminal history must never execute")

    old = define_task(
        "fixture.done", 1, lambda value: {"phase": "done"}, {"done": never}, never
    )
    registry = tools(tmp_path, port_type=InvalidFirst)
    wire = CountedWire(tmp_path)

    async def scenario():
        async with await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools=registry,
        ) as product:
            cid = (await product.submit("input")).conversation_id

            def history(tx):
                # Real typed task rows, deliberately synthetic terminal history;
                # no claim that these fixture rows executed provider/tool work.
                for _ in range(128):
                    id = tx.create_task(
                        old,
                        None,
                        ownership={"kind": "conversation"},
                        conversation_id=cid,
                    )
                    row = next(r.to_tree() for r in tx.staged_tasks() if r.id == id)
                    tx.set_task(
                        {
                            **row,
                            "state": {
                                "status": "terminal",
                                "outcome": {"status": "completed", "result": None},
                            },
                        }
                    )

            await owned_call(lambda: product._session.commit(history))
            blocked = asyncio.Event()
            loop = asyncio.get_running_loop()

            def publication(value):
                for change in value.changes:
                    if type(change) is StorageWrite:
                        row = change.to_tree()
                        if (
                            row["type"] == "task"
                            and row["value"]["state"]
                            .get("checkpoint", {})
                            .get("reason")
                            == "invalid_result"
                        ):
                            loop.call_soon_threadsafe(blocked.set)

            unsubscribe = product._session.subscribe_commits(publication)
            try:
                running = asyncio.create_task(product.resume())
                try:
                    await asyncio.wait_for(blocked.wait(), 10)
                    assert counts(tmp_path) == {
                        "calls": 1,
                        "applications": 1,
                        "prepares": 1,
                        "providers": 1,
                    }
                    held = await asyncio.wait_for(running, 2)
                finally:
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)
                assert held.status == "held" and held.inspection.status == "waiting"
                assert held.inspection.reason is None
                first = await product.inspect()
                assert first.next is not None
                assert all(t.reason is None for t in first.tasks)
                second = await product.inspect(cursor=first.next)
                child = next(t for t in second.tasks if t.reason == "invalid_result")
                assert child.family == "model_tool" and second.next is None
                assert not held.completed.rounds
            finally:
                unsubscribe()

    asyncio.run(asyncio.wait_for(scenario(), 20))

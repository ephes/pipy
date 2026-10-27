"""Source-backed rejection boundaries before dispatch or extra accepted input."""

import asyncio
from dataclasses import replace

import pytest
from generation_support import Wire
from test_product_session import profile

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.generation_tasks import TOOL_TASK
from pipy_harness.native.durable.recovery_catalog import HEADER
from pipy_harness.native.durable.resolution import GENERATION_ADMISSION
from pipy_harness.native.durable.resolution_values import ResolutionError
from pipy_harness.sdk import (
    DurableProductError,
    UnsupportedDurableCapability,
    create_durable_product_session,
    open_durable_product_session,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


@pytest.mark.parametrize(
    "foreign", ["sibling", "conversation", "unbound_child", "waiting_graph"]
)
def test_all_store_preflight_refuses_unselected_or_malformed_live_graph_before_effect(
    tmp_path, foreign
):
    wire = Wire()

    async def scenario():
        async with await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools={},
        ) as product:
            admission = await product.submit("input")

            def damage(tx):
                row = tx.task(admission.generation_task_id).to_tree()
                cid = row["conversationId"]
                if foreign == "waiting_graph":
                    tx.set_task(
                        {
                            **row,
                            "state": {
                                "status": "waiting",
                                "checkpoint": row["state"]["checkpoint"],
                                "policy": "allSettled",
                                "on": [999],
                            },
                        }
                    )
                elif foreign == "unbound_child":
                    tx.create_task(
                        TOOL_TASK,
                        {
                            "kind": "model",
                            "assistant": admission.entry_id,
                            "callId": "foreign",
                        },
                        ownership={"kind": "task", "taskId": row["id"]},
                        conversation_id=cid,
                    )
                else:
                    if foreign == "conversation":
                        cid = tx.create_conversation({"kind": "ownerless"}).id
                    tx.create_task(
                        GENERATION_ADMISSION,
                        row["input"],
                        ownership={"kind": "conversation"},
                        conversation_id=cid,
                    )

            await product_api_commit(product, damage)
            with pytest.raises(DurableProductError):
                await product.resume()
            assert wire.calls == []

    asyncio.run(asyncio.wait_for(scenario(), 10))


async def product_api_commit(product, change):
    from pipy_harness.native.durable.generation_transport import owned_call

    return await owned_call(lambda: product._session.commit(change))


@pytest.mark.parametrize(
    "method",
    [
        "compact",
        "tree",
        "fork",
        "clone",
        "reset",
        "handoff",
        "steer",
        "follow_up",
        "inject",
        "abort",
        "configure",
    ],
)
def test_unsupported_product_controls_refuse_without_admission(tmp_path, method):
    wire = Wire()

    async def scenario():
        async with await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools={},
        ) as product:
            with pytest.raises(UnsupportedDurableCapability):
                await getattr(product, method)("private-unsupported-payload")
            assert (await product.inspect()).tasks == ()
            assert wire.calls == []
            assert not list(tmp_path.rglob("*.jsonl"))

    asyncio.run(scenario())


def test_request_callback_refuses_before_store_without_callback_invocation(tmp_path):
    from generation_support import seed

    called = []
    template = snapshot_provider_request(
        replace(
            seed(tmp_path).request,
            user_prompt="",
            messages=(),
            provider_header_callback=lambda *args: called.append(args),
        )
    )

    async def scenario():
        with pytest.raises(UnsupportedDurableCapability):
            await create_durable_product_session(
                tmp_path / "absent" / "product.sqlite",
                workspace=tmp_path,
                profile=profile(tmp_path, Wire()),
                request_template=template,
                tools={},
            )
        assert called == [] and not (tmp_path / "absent").exists()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change", ["template", "selection", "tools", "workspace", "pointers", "version"]
)
def test_header_changed_binding_exact_open_refuses_without_initializer_or_auth(
    tmp_path, change
):
    path = tmp_path / "product.sqlite"
    wire = Wire()

    async def scenario():
        product = await create_durable_product_session(
            path, workspace=tmp_path, profile=profile(tmp_path, wire), tools={}
        )
        await product.submit("input")

        def damage(tx):
            draft = tx.doc(HEADER)
            if change == "template":
                value = draft.snapshot().thaw()["template"]
                value["nodes"][2]["value"] = "756e626f756e64"
                draft["template"] = value
            elif change == "selection":
                draft["selection"] = ["other", "gpt-test", "openai-completions"]
            elif change == "tools":
                value = draft.snapshot().thaw()["tools"]
                value["workspace"] = "/foreign"
                draft["tools"] = value
            elif change == "workspace":
                draft["workspace"] = "/foreign"
            elif change == "pointers":
                draft["initial_entry"] = None
            else:
                draft["version"] = 999

        await product_api_commit(product, damage)
        await product.close()
        with pytest.raises(DurableProductError):
            await open_durable_product_session(path, workspace=tmp_path, tools={})
        assert wire.calls == []

    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_generation_retry_chain_binds_all_edges_after_progress_and_refuses_seed_override(
    tmp_path,
):
    from pipy_harness.native.durable.resolution import AUDIT
    from pipy_harness.sdk import DurableRunSelection

    wire = Wire(failure=True)

    async def scenario():
        async with await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools={},
        ) as product:
            admitted = await product.submit("input")
            assert (await product.resume()).inspection.outcome == "faulted"
            first = await product.authorize_retry(
                admitted.generation_task_id, nonce="a" * 32
            )
            assert (
                await product.resume(DurableRunSelection(first.new_task_id, "a" * 32))
            ).inspection.outcome == "faulted"
            second = await product.authorize_retry(first.new_task_id, nonce="b" * 32)
            with pytest.raises(UnsupportedDurableCapability):
                await product.authorize_retry(
                    second.new_task_id, nonce="c" * 32, new_request=object()
                )
            wire.failure = False
            assert (
                await product.resume(DurableRunSelection(second.new_task_id, "b" * 32))
            ).inspection.outcome == "completed"
            before = len(wire.calls)
            assert (
                await product.authorize_retry(
                    admitted.generation_task_id, nonce="a" * 32
                )
                == first
            )
            value = await product._read(product._header)

            def corrupt(tx):
                draft = tx.doc(
                    AUDIT,
                    conversation_id=value["conversation"],
                    key="a" * 32,
                    seed=None,
                )
                audit = draft.snapshot().thaw()
                changed = {**audit["proposal"], "new_request": {"unbound": True}}
                draft["proposal"] = changed

            await product_api_commit(product, corrupt)
            with pytest.raises((DurableProductError, ResolutionError)):
                await product.resume(DurableRunSelection(second.new_task_id, "b" * 32))
            assert len(wire.calls) == before
            assert OwnedJson(value["template"]) != OwnedJson({"unbound": True})

    asyncio.run(asyncio.wait_for(scenario(), 15))


def test_caller_session_identity_refuses_before_store_preserving_durable_first_winner(
    tmp_path,
):
    from generation_support import seed

    template = snapshot_provider_request(
        replace(
            seed(tmp_path).request,
            user_prompt="",
            messages=(),
            session_id="unsupported-caller-identity",
        )
    )

    async def scenario():
        with pytest.raises(UnsupportedDurableCapability):
            await create_durable_product_session(
                tmp_path / "missing" / "product.sqlite",
                workspace=tmp_path,
                profile=profile(tmp_path, Wire()),
                request_template=template,
                tools={},
            )
        assert not (tmp_path / "missing").exists()

    asyncio.run(scenario())


def test_terminal_fast_path_retains_live_child_refusal_without_current_credentials(
    tmp_path,
):
    from pipy_harness.native.durable.generation_tasks import TOOL_NAME, TOOL_VERSION
    from pipy_harness.native.durable.sqlite import open_sqlite_storage
    from pipy_harness.native.durable.storage import StorageWrite

    path = tmp_path / "product.sqlite"

    async def prepare():
        async with await create_durable_product_session(
            path, workspace=tmp_path, profile=profile(tmp_path, Wire()), tools={}
        ) as product:
            admission = await product.submit("input")
            result = await product.resume()
            return admission, result.completed.messages[-1].entry_id

    admission, assistant = asyncio.run(prepare())
    # Deliberately bypass semantic Tx to test a malformed persisted graph;
    # this row is never claimed as an admitted product/tool operation.
    backend = open_sqlite_storage(str(path), mode="open")
    try:
        backend.commit(
            (
                StorageWrite(
                    {
                        "type": "task",
                        "value": {
                            "id": backend.mint_id(),
                            "conversationId": admission.conversation_id,
                            "owner": admission.generation_task_id,
                            "kind": TOOL_NAME,
                            "version": TOOL_VERSION,
                            "input": {
                                "kind": "model",
                                "assistant": assistant,
                                "callId": "unbound",
                            },
                            "background": False,
                            "abortRequested": False,
                            "state": {
                                "status": "pending",
                                "checkpoint": {"phase": "call"},
                            },
                        },
                    }
                ),
            )
        )
    finally:
        backend.close()
    for material in tmp_path.glob("auth.json*"):
        material.unlink()

    async def refuse():
        async with await open_durable_product_session(
            path, workspace=tmp_path, tools={}
        ) as product:
            assert (
                await product.inspect(admission.generation_task_id)
            ).status == "terminal"
            with pytest.raises(DurableProductError, match="incompatible"):
                await product.resume()
            assert product._scheduler is None

    asyncio.run(asyncio.wait_for(refuse(), 5))


@pytest.mark.parametrize("active", [False, True])
def test_database_filename_symlink_cannot_bypass_existing_path_owner_refusal(
    tmp_path, active
):
    path = tmp_path / "product.sqlite"
    alias = tmp_path / "alias.sqlite"
    wire = Wire()

    async def scenario():
        product = await create_durable_product_session(
            path, workspace=tmp_path, profile=profile(tmp_path, wire), tools={}
        )
        alias.symlink_to(path)
        try:
            if not active:
                await product.close()
            with pytest.raises(DurableProductError):
                await open_durable_product_session(alias, workspace=tmp_path, tools={})
            assert wire.calls == []
            if active:
                assert (await product.inspect()).tasks == ()
        finally:
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 5))


@pytest.mark.parametrize(
    "invalid", [True, 0, -1, 2**53, 1.5, "private-selector", [], {}, None]
)
def test_invalid_public_task_ids_refuse_fixed_before_read_or_control(
    tmp_path, monkeypatch, invalid
):
    from unittest.mock import AsyncMock

    from generation_support import Wire
    from test_product_session import profile

    from pipy_harness.sdk import DurableRunSelection

    async def scenario():
        async with await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, Wire()),
            tools={},
        ) as product:
            read = AsyncMock(side_effect=AssertionError("Invalid IDs must not read"))
            monkeypatch.setattr(product, "_read", read)
            if invalid is not None:  # None intentionally means live inspection.
                with pytest.raises(DurableProductError, match="invalid_request"):
                    await product.inspect(invalid)
            with pytest.raises(DurableProductError, match="invalid_request"):
                await product.authorize_retry(invalid, nonce="c" * 32)
            with pytest.raises(DurableProductError, match="invalid_request"):
                DurableRunSelection(invalid, "c" * 32)
            read.assert_not_called()
            assert product._scheduler is None and not product._operations

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 129},
        {"limit": True},
        {"cursor": object()},
        {"task_id": 1, "limit": 1},
    ],
)
def test_invalid_product_inspection_paging_refuses_before_read(
    tmp_path, monkeypatch, kwargs
):
    from unittest.mock import AsyncMock

    from generation_support import Wire
    from test_product_session import profile

    async def scenario():
        async with await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, Wire()),
            tools={},
        ) as product:
            read = AsyncMock(side_effect=AssertionError("Invalid paging must not read"))
            monkeypatch.setattr(product, "_read", read)
            with pytest.raises(DurableProductError, match="invalid_request"):
                await product.inspect(**kwargs)
            read.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize("sibling", [False, True])
def test_profile_required_precedes_live_graph_preflight(tmp_path, sibling):
    wire = Wire()

    async def scenario():
        path = tmp_path / "product.sqlite"
        async with await create_durable_product_session(
            path, workspace=tmp_path, profile=profile(tmp_path, wire), tools={}
        ) as product:
            admission = await product.submit("input")
            if sibling:

                def add(tx):
                    row = tx.task(admission.generation_task_id).to_tree()
                    tx.create_task(
                        GENERATION_ADMISSION,
                        row["input"],
                        ownership={"kind": "conversation"},
                        conversation_id=row["conversationId"],
                    )

                await product_api_commit(product, add)
        async with await open_durable_product_session(
            path, workspace=tmp_path, tools={}
        ) as product:
            with pytest.raises(DurableProductError) as caught:
                await product.resume()
            assert caught.value.code == "profile_required"
            assert product._scheduler is None and wire.calls == []
        async with await open_durable_product_session(
            path, workspace=tmp_path, profile=profile(tmp_path, wire), tools={}
        ) as product:
            if sibling:
                with pytest.raises(DurableProductError) as caught:
                    await product.resume()
                assert caught.value.code == "foreign_live_task"
                assert product._scheduler is None and wire.calls == []
            else:
                result = await product.resume()
                assert (
                    result.status == "terminal"
                    and result.inspection.outcome == "completed"
                )
                assert len(wire.calls) == 1

    asyncio.run(asyncio.wait_for(scenario(), 15))

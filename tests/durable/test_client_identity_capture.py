"""Real native capture, cold defaults and C2 replay; fake transport only."""

import asyncio
import json
from dataclasses import replace

import pytest
from generation_support import SPEC, Wire, admit, completion, seed
from test_tool_boundaries import MultiRound, RichTool, isolated, run_idle

from pipy_harness.native.agent.content import NO_STRUCTURED_CONTENT
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.client_identity import ClientAttribution, ClientIdentity
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.generation import define_generation
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.resolution import AUDIT
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import EntryQuery
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.sdk import (
    DurableProductError,
    DurableProviderProfile,
    DurableRunSelection,
    create_durable_product_session,
    open_durable_product_session,
)

SPEC_WITH_TARGET = replace(SPEC, base_url="https://openrouter.ai/api/v1")
OVERRIDE = ClientIdentity(attribution=None, version="REQUEST-VERSION")


def host(mode="short", version="HOST-VERSION"):
    return ClientIdentity(
        mode, ClientAttribution("HOST-ONLY", "https://host.invalid", ("host",)), version
    )


def profile(root, wire, identity, spec=SPEC_WITH_TARGET):
    return DurableProviderProfile(
        spec,
        auth_path=root / "auth.json",
        env={},
        runtime_api_key="fixture-key",
        http_client=wire,
        identity=identity,
    )


def template(root, override=OVERRIDE):
    return snapshot_provider_request(
        replace(seed(root).request, user_prompt="", messages=(), identity=override)
    )


def assistant_rows(session, cid):
    return session.commit(
        lambda tx: tx.scan_entries(EntryQuery({"conversationId": cid}), 100)
    ).items


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_actual_capture_persists_only_override_and_default_identity_stays_host_owned(
    tmp_path, monkeypatch, backend
):
    isolated(monkeypatch, tmp_path)
    wire = Wire()
    task = define_generation(
        SPEC_WITH_TARGET,
        auth_path=tmp_path / "auth.json",
        env={},
        runtime_api_key="fixture-key",
        http_client=wire,
        identity=host(),
    )
    session = Session(
        MemoryStorage()
        if backend == "memory"
        else open_sqlite_storage(str(tmp_path / "generation.sqlite"), mode="create")
    )
    snapshot = snapshot_provider_request(
        replace(seed(tmp_path).request, identity=OVERRIDE)
    )
    try:
        cid, _, task_id = admit(session, task, snapshot)
        done, _ = completion(session, task_id)
        from pipy_harness.native.durable.scheduler import TaskScheduler

        scheduler = TaskScheduler(session, [task])
        scheduler.open()
        scheduler.resume()
        assert done.wait(10)
        assert len(wire.calls) == 1
        headers = wire.calls[0][1]["headers"]
        assert headers["User-Agent"] == "pipy/REQUEST-VERSION"
        assert "HTTP-Referer" not in headers and "X-OpenRouter-Title" not in headers
        row = next(
            e.to_tree()
            for e in assistant_rows(session, cid)
            if e.to_tree()["kind"] == "pi.assistant"
        )
        saved = decode_canonical(row["data"]["request"])
        assert saved.request.identity == OVERRIDE
        assert row["data"]["request"]["codec_version"] == 3
        raw = json.dumps(row["data"]["request"])
        for value in ("HOST-ONLY", "HOST-VERSION", "https://host.invalid"):
            assert value not in raw and value.encode().hex() not in raw
        scheduler.resume()
        assert len(wire.calls) == 1
    finally:
        session.close(timeout=10)


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("target_drift", [False, True])
def test_cold_override_survives_and_current_host_defaults_apply_only_outbound(
    tmp_path, monkeypatch, prepared, target_drift
):
    isolated(monkeypatch, tmp_path)
    from pipy_harness.native.durable import sqlite

    old_wire, fresh_wire = Wire(), Wire()
    path = tmp_path / "product.sqlite"

    async def scenario():
        product = await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, old_wire, host()),
            tools={},
            request_template=template(tmp_path),
        )
        await product.submit("input")
        try:
            if prepared:

                def lose_ack(connection):
                    rows = [
                        sqlite._decode(r[0])
                        for r in connection.execute("SELECT record FROM tasks")
                    ]
                    if any(
                        r["state"]["status"] == "running"
                        and r["state"]["checkpoint"]["phase"] == "request"
                        for r in rows
                    ):
                        raise RuntimeError("test committed request ACK lost")

                with monkeypatch.context() as patch:
                    patch.setattr(sqlite, "_after_commit", lose_ack)
                    with pytest.raises(DurableProductError):
                        await product.resume()
            assert old_wire.calls == []
        finally:
            await product.close()
        async with await open_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(
                tmp_path,
                fresh_wire,
                host("detailed", "NEW-HOST-VERSION"),
                replace(SPEC_WITH_TARGET, base_url="https://changed.invalid/v1")
                if target_drift
                else SPEC_WITH_TARGET,
            ),
            tools={},
        ) as reopened:
            result = await reopened.resume()
            if prepared and target_drift:
                assert result.inspection.outcome == "faulted"
                assert fresh_wire.calls == []
                return
            assert result.inspection.outcome == "completed"
            assert len(fresh_wire.calls) == 1
            headers = fresh_wire.calls[0][1]["headers"]
            assert headers["User-Agent"].startswith("pipy/REQUEST-VERSION (")
            assert "HTTP-Referer" not in headers
            saved = await reopened._read(reopened._header)
            assert decode_canonical(saved["template"]).request.identity == OVERRIDE
        # Settled winner replay needs no profile/auth and performs no request.
        async with await open_durable_product_session(
            path, workspace=tmp_path, tools={}
        ) as reopened:
            assert (await reopened.resume()).inspection.outcome == "completed"
        assert len(fresh_wire.calls) == 1

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_real_C2_retry_proposal_winner_and_cold_nonce_replay_keep_override(
    tmp_path, monkeypatch
):
    isolated(monkeypatch, tmp_path)
    failed_wire, fresh_wire = Wire(failure=True), Wire()
    path = tmp_path / "retry.sqlite"

    async def scenario():
        async with await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, failed_wire, host()),
            tools={},
            request_template=template(tmp_path),
        ) as product:
            admitted = await product.submit("input")
            first = await product.resume()
            assert first.inspection.outcome == "faulted" and len(failed_wire.calls) == 1
            winner = await product.authorize_retry(
                admitted.generation_task_id, nonce="a" * 32
            )
            assert winner == await product.authorize_retry(
                admitted.generation_task_id, nonce="a" * 32
            )
            assert len(failed_wire.calls) == 1
            header = await product._read(product._header)
            audit = product._session.snapshot(
                AUDIT, conversation_id=header["conversation"], key="a" * 32
            ).value.thaw()
            proposal = audit["proposal"]["new_request"]
            assert proposal["codec_version"] == 3
            assert decode_canonical(proposal).request.identity == OVERRIDE
        selected = DurableRunSelection(winner.new_task_id, "a" * 32)
        async with await open_durable_product_session(
            path,
            workspace=tmp_path,
            selection=selected,
            profile=profile(tmp_path, fresh_wire, host("detailed", "CHANGED")),
            tools={},
        ) as reopened:
            assert (
                await reopened.authorize_retry(
                    admitted.generation_task_id, nonce="a" * 32
                )
                == winner
            )
            result = await reopened.resume()
            assert (
                result.task_id == winner.new_task_id
                and result.inspection.outcome == "completed"
            )
            assert len(fresh_wire.calls) == 1
            assert fresh_wire.calls[0][1]["headers"]["User-Agent"].startswith(
                "pipy/REQUEST-VERSION ("
            )
            assert "HTTP-Referer" not in fresh_wire.calls[0][1]["headers"]
        replay_wire = Wire(failure=True)
        async with await open_durable_product_session(
            path,
            workspace=tmp_path,
            selection=selected,
            tools={},
            profile=profile(tmp_path, replay_wire, host("short", "REPLAY-DEFAULT")),
        ) as reopened:
            assert (
                await reopened.authorize_retry(
                    admitted.generation_task_id, nonce="a" * 32
                )
                == winner
            )
            assert (await reopened.resume()).inspection.outcome == "completed"
        assert replay_wire.calls == []
        # Settled resume itself retains the existing auth-free path.
        async with await open_durable_product_session(
            path, workspace=tmp_path, selection=selected, tools={}
        ) as reopened:
            assert (await reopened.resume()).inspection.outcome == "completed"
        assert len(fresh_wire.calls) == 1

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_actual_next_tool_round_preserves_override(tmp_path, monkeypatch):
    isolated(monkeypatch, tmp_path)
    wire = MultiRound((("rich",), ()))
    port = RichTool(NO_STRUCTURED_CONTENT)
    tools = define_tool_task(
        {"rich": ToolRegistration(port, "fixture.rich", "1")}, workspace_root=tmp_path
    )
    generation = define_generation(
        SPEC_WITH_TARGET,
        auth_path=tmp_path / "auth.json",
        env={},
        runtime_api_key="fixture-key",
        http_client=wire,
        identity=host(),
    )
    snapshot = snapshot_provider_request(
        replace(
            seed(tmp_path).request,
            identity=OVERRIDE,
            available_tools=(port.definition,),
        )
    )
    session = Session(MemoryStorage())
    try:
        cid, _, task_id = admit(session, generation, snapshot)
        scheduler = run_idle(session, [generation, tools])
        root = next(t.to_tree() for t in scheduler.inspect() if t.id == task_id)
        assert root["state"]["outcome"]["status"] == "completed"
        assert len(wire.calls) == 2 and len(port.calls) == 1
        for _, call in wire.calls:
            assert call["headers"]["User-Agent"] == "pipy/REQUEST-VERSION"
        for row in assistant_rows(session, cid):
            value = row.to_tree()
            if value["kind"] == "pi.assistant":
                assert (
                    decode_canonical(value["data"]["request"]).request.identity
                    == OVERRIDE
                )
    finally:
        session.close(timeout=10)


def test_genuine_generation_uses_pi_attribution_without_persisting_defaults(
    tmp_path, monkeypatch
):
    isolated(monkeypatch, tmp_path)
    wire = Wire()
    task = define_generation(
        SPEC_WITH_TARGET,
        auth_path=tmp_path / "auth.json",
        env={},
        runtime_api_key="fixture",
        http_client=wire,
    )
    session = Session(MemoryStorage())
    try:
        cid, _, task_id = admit(session, task, seed(tmp_path))
        run_idle(session, [task])
        assert len(wire.calls) == 1
        headers = wire.calls[0][1]["headers"]
        assert headers["HTTP-Referer"] == "https://pi.dev"
        assert headers["X-OpenRouter-Title"] == "pi"
        assert headers["X-OpenRouter-Categories"] == "cli-agent"
        saved = next(
            e.to_tree()
            for e in assistant_rows(session, cid)
            if e.to_tree()["kind"] == "pi.assistant"
        )["data"]["request"]
        assert saved["codec_version"] == 1
        assert decode_canonical(saved).request.identity is None
    finally:
        session.close(timeout=10)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_invalid_V3_task_ingress_rolls_back_entry_and_admission(
    tmp_path, monkeypatch, backend
):
    isolated(monkeypatch, tmp_path)
    from pipy_harness.native.agent.content import ProductContent
    from pipy_harness.native.agent.messages import AgentUserMessage
    from pipy_harness.native.durable.canonical_codec import (
        CanonicalCodecError,
        encode_canonical,
    )
    from pipy_harness.native.durable.generation import message_entry
    from pipy_harness.native.durable.storage import TaskQuery

    wire = Wire()
    task = define_generation(
        SPEC,
        auth_path=tmp_path / "auth.json",
        env={},
        runtime_api_key="fixture",
        http_client=wire,
    )
    session = Session(
        MemoryStorage()
        if backend == "memory"
        else open_sqlite_storage(str(tmp_path / "rollback.sqlite"), mode="create")
    )
    try:
        cid = session.commit(
            lambda tx: tx.create_conversation({"kind": "ownerless"})
        ).id
        malformed = encode_canonical(template(tmp_path))
        malformed["codec_version"] = 2  # Must reject, never silently discard identity.

        def failed(tx):
            tx.append_entry(
                cid, message_entry(AgentUserMessage(ProductContent("must roll back")))
            )
            tx.create_task(
                task, malformed, ownership={"kind": "conversation"}, conversation_id=cid
            )

        with pytest.raises(CanonicalCodecError):
            session.commit(failed)
        assert (
            session.commit(
                lambda tx: tx.scan_entries(EntryQuery({"conversationId": cid}), 10)
            ).items
            == ()
        )
        assert (
            session.commit(
                lambda tx: tx.scan_tasks(TaskQuery({"conversationId": cid}), 10)
            ).items
            == ()
        )
        assert wire.calls == []
    finally:
        session.close(timeout=10)


def test_cancelled_waiter_and_close_join_actual_identity_transport(
    tmp_path, monkeypatch
):
    isolated(monkeypatch, tmp_path)
    wire = Wire()
    wire.release.clear()

    async def scenario():
        product = await create_durable_product_session(
            tmp_path / "owned.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, wire, host()),
            tools={},
            request_template=template(tmp_path),
        )
        await product.submit("input")
        waiter = asyncio.create_task(product.resume())
        closing = None
        try:
            assert await asyncio.to_thread(wire.entered.wait, 10)
            assert wire.calls[0][1]["headers"]["User-Agent"] == "pipy/REQUEST-VERSION"
            waiter.cancel()
            closing = asyncio.create_task(product.close())
            await asyncio.sleep(0)
            assert not closing.done()  # Actual Wire is still held; close must join it.
        finally:
            wire.release.set()
            await asyncio.gather(waiter, return_exceptions=True)
            if closing is not None:
                await asyncio.wait_for(closing, 10)
            else:
                await product.close()
        assert len(wire.calls) == 1

    asyncio.run(asyncio.wait_for(scenario(), 20))

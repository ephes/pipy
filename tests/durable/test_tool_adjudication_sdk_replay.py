"""Public decision ACKs, exact CLI refusals and genuine post-ACK phase faults."""

import asyncio
import json
import threading
from dataclasses import replace

import pytest
from adjudication_sdk_support import cli, disk_logical, native_origins, save
from generation_support import SPEC
from test_product_session import tools
from test_tool_adjudication import held_product
from tool_support import CountedWire, InfrastructureFailureFirst, counts

from pipy_harness.native.durable import adjudication, tool_execution
from pipy_harness.native.durable.inspection_read import peek_document
from pipy_harness.native.durable.records import TaskId
from pipy_harness.native.durable.storage import DocumentQuery, EntryQuery
from pipy_harness.native.durable.tool_execution import RECEIPT_DOC
from pipy_harness.sdk import (
    DurableProductError,
    DurableProviderProfile,
    open_durable_product_session,
)

NONCE = "c" * 32


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in (
        "HOME",
        "PIPY_AUTH_DIR",
        "PIPY_CONFIG_HOME",
        "XDG_STATE_HOME",
        "TMPDIR",
    ):
        path = tmp_path / name.lower()
        path.mkdir()
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "native-theme.json"))
    # The committed test/helper must work without worker-only source variables.
    monkeypatch.delenv("C2_SUT_SOURCE", raising=False)


async def read_row(product, task_id):
    return await product._read(
        lambda: product._session._owner.storage.task(TaskId(task_id)).to_tree()
    )


async def material(product, admission, task_id):
    def read():
        store = product._session._owner.storage
        return {
            "receipt": peek_document(
                store, RECEIPT_DOC, admission.conversation_id, task_id
            ),
            "audit": peek_document(
                store, adjudication.AUDIT, admission.conversation_id, key=NONCE
            ),
            "task_documents": [
                r.to_tree()
                for r in store.scan_documents(
                    DocumentQuery(
                        {"scope": {"kind": "task", "taskId": task_id}, "at": "current"}
                    ),
                    100,
                ).items
            ],
            "result_entries": [
                r.to_tree()
                for r in store.scan_entries(
                    EntryQuery({"conversationId": admission.conversation_id}), 100
                ).items
                if r.to_tree()["kind"] == "pi.tool-result"
            ],
        }

    return await product._read(read)


def exact_ack(value, task_id):
    assert value.to_tree() == {"task_id": task_id, "decision": "interrupted"}


async def cold_ack(root, task_id, ack):
    before = disk_logical(root / "product.sqlite")
    effects = counts(root)
    attempted = []

    def forbidden_profile(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("auth-free SDK replay constructed a profile")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(DurableProviderProfile, "__init__", forbidden_profile)
        async with await open_durable_product_session(
            root / "product.sqlite", workspace=root, tools={}
        ) as product:
            assert await product.adjudicate_interrupted(task_id, nonce=NONCE) == ack
    assert attempted == []
    assert disk_logical(root / "product.sqlite") == before and counts(root) == effects
    code, out, err, origin = await asyncio.to_thread(
        cli, root, "cold-ack", task_id, NONCE
    )
    assert code == 0 and err == ""
    assert json.loads(out) == {
        "format": "pipy-durable-output",
        "version": 1,
        "operation": "adjudicate-interrupted",
        "task_id": task_id,
        "decision": "interrupted",
    }
    assert origin["profile_constructions"] == []
    assert disk_logical(root / "product.sqlite") == before and counts(root) == effects


def test_actual_cli_fresh_decision_explicit_resume_and_authfree_ack(tmp_path):
    async def scenario():
        async with held_product(tmp_path) as (product, admission, task_id):
            before = counts(tmp_path)
            assert before == {
                "calls": 1,
                "applications": 1,
                "prepares": 1,
                "providers": 1,
            }
        code, out, err, origins = await asyncio.to_thread(
            cli, tmp_path, "fresh-decision", task_id, NONCE, kind="same"
        )
        assert code == 0 and err == ""
        assert json.loads(out) == {
            "format": "pipy-durable-output",
            "version": 1,
            "operation": "adjudicate-interrupted",
            "task_id": task_id,
            "decision": "interrupted",
        }
        assert len(origins["profile_constructions"]) == 1 and counts(tmp_path) == before
        async with await open_durable_product_session(
            tmp_path / "product.sqlite", workspace=tmp_path, tools={}
        ) as product:
            ack = await product.adjudicate_interrupted(task_id, nonce=NONCE)
            exact_ack(ack, task_id)
            found = await material(product, admission, task_id)
            assert (
                found["audit"] is not None
                and found["receipt"] is not None
                and found["result_entries"] == []
            )
            assert (await read_row(product, task_id))["state"]["checkpoint"][
                "phase"
            ] == "settle"
        code, out, err, _ = await asyncio.to_thread(
            cli,
            tmp_path,
            "explicit-resume",
            task_id,
            NONCE,
            kind="same",
            action="resume",
        )
        assert code == 0 and err == ""
        result = json.loads(out)
        assert result["status"] == "terminal"
        assert counts(tmp_path) == {
            "calls": 2,
            "applications": 2,
            "prepares": 2,
            "providers": 2,
        }
        await cold_ack(tmp_path, task_id, ack)
        save(
            tmp_path,
            "public-witness.json",
            {
                "task_id": task_id,
                "before": before,
                "after": counts(tmp_path),
                "ack": ack.to_tree(),
                "parent_origins": native_origins(),
            },
        )

    asyncio.run(asyncio.wait_for(scenario(), 35))


@pytest.mark.parametrize("stage", ["fresh", "replay"])
def test_wrong_profile_fixed_refusal_sdk_and_cli(tmp_path, stage):
    async def scenario():
        async with held_product(tmp_path) as (product, admission, task_id):
            if stage == "replay":
                await product.adjudicate_interrupted(task_id, nonce=NONCE)
        before = disk_logical(tmp_path / "product.sqlite")
        effects = counts(tmp_path)
        wrong = DurableProviderProfile(
            replace(SPEC, model_id="other-model"),
            auth_path=tmp_path / "NO_AUTH.json",
            env={},
            runtime_api_key="synthetic-only",
            http_client=CountedWire(tmp_path),
        )
        with pytest.raises(DurableProductError) as error:
            async with await open_durable_product_session(
                tmp_path / "product.sqlite",
                workspace=tmp_path,
                profile=wrong,
                tools=tools(tmp_path, port_type=InfrastructureFailureFirst),
            ) as product:
                await product.adjudicate_interrupted(task_id, nonce=NONCE)
        assert error.value.code == "incompatible"
        assert (
            disk_logical(tmp_path / "product.sqlite") == before
            and counts(tmp_path) == effects
        )
        code, out, err, origin = await asyncio.to_thread(
            cli, tmp_path, "wrong-profile", task_id, NONCE, kind="wrong"
        )
        assert (code, out, err) == (
            1,
            "",
            "Durable command unavailable: incompatible\n",
        )
        assert len(origin["profile_constructions"]) == 1
        assert (
            disk_logical(tmp_path / "product.sqlite") == before
            and counts(tmp_path) == effects
        )

    asyncio.run(asyncio.wait_for(scenario(), 25))


def test_missing_winner_without_profile_and_cli_syntax_fixed_codes(tmp_path):
    async def scenario():
        async with held_product(tmp_path) as (product, admission, task_id):
            pass
        before = disk_logical(tmp_path / "product.sqlite")
        effects = counts(tmp_path)
        async with await open_durable_product_session(
            tmp_path / "product.sqlite", workspace=tmp_path, tools={}
        ) as product:
            with pytest.raises(DurableProductError) as error:
                await product.adjudicate_interrupted(task_id, nonce=NONCE)
            assert error.value.code == "profile_required"
        for label, nonce, extra, expected in [
            (
                "missing-winner",
                NONCE,
                (),
                (1, "", "Durable command unavailable: profile_required\n"),
            ),
            (
                "bad-nonce",
                "not-a-nonce",
                (),
                (2, "", "Durable command unavailable: invalid_request\n"),
            ),
            (
                "partial-profile",
                NONCE,
                ("--provider", "openai"),
                (2, "", "Durable command unavailable: invalid_request\n"),
            ),
        ]:
            code, out, err, origin = await asyncio.to_thread(
                cli, tmp_path, label, task_id, nonce, extra=extra
            )
            assert (code, out, err) == expected
            assert origin["profile_constructions"] == []
            assert (
                disk_logical(tmp_path / "product.sqlite") == before
                and counts(tmp_path) == effects
            )

    asyncio.run(asyncio.wait_for(scenario(), 30))


def _phase_hook(monkeypatch, mode):
    original_settle = tool_execution._Tool.settle
    original_match = tool_execution.match_result
    lock = threading.Lock()
    state = {
        "active": False,
        "target": None,
        "inside": False,
        "entered": 0,
        "validation_faults": 0,
        "normal_return": False,
    }

    def snapshot():
        with lock:
            return dict(state)

    def match(result, call):
        with lock:
            fail = state["active"] and state["inside"] and mode == "reblock"
            if fail:
                state["validation_faults"] += 1
        if fail:
            raise ValueError("synthetic one-shot settle validation fault")
        return original_match(result, call)

    async def settle(self, record, runtime, context):
        with lock:
            selected = state["active"] and record["id"] == state["target"]
            if selected:
                state["entered"] += 1
                state["inside"] = True
        if not selected:
            return await original_settle(self, record, runtime, context)
        try:
            if mode == "raise":
                raise RuntimeError("synthetic settle phase failure")
            if mode == "no_progress":
                with lock:
                    state["normal_return"] = True
                return
            await original_settle(self, record, runtime, context)
        finally:
            with lock:
                state["inside"] = False
                state["active"] = False

    # _tool_definition captures bound callbacks, so install before public factory.
    monkeypatch.setattr(tool_execution._Tool, "settle", settle)
    monkeypatch.setattr(tool_execution, "match_result", match)

    return lock, state, snapshot


@pytest.mark.parametrize("mode", ["raise", "no_progress", "reblock"])
def test_actual_post_ack_phase_state_and_cold_decision_only_ack(
    tmp_path, monkeypatch, mode
):
    lock, state, snapshot = _phase_hook(monkeypatch, mode)

    async def scenario():
        async with held_product(tmp_path) as (product, admission, task_id):
            before = counts(tmp_path)
            assert snapshot()["entered"] == 0
            first = await material(product, admission, task_id)
            assert first["receipt"] is None and first["audit"] is None
            ack = await product.adjudicate_interrupted(task_id, nonce=NONCE)
            exact_ack(ack, task_id)
            after_ack = await material(product, admission, task_id)
            assert (
                after_ack["receipt"] is not None
                and after_ack["audit"] is not None
                and after_ack["result_entries"] == []
            )
            assert (
                snapshot()["entered"] == snapshot()["validation_faults"] == 0
                and counts(tmp_path) == before
            )
            assert (await read_row(product, task_id))["state"]["checkpoint"][
                "phase"
            ] == "settle"
            committed = threading.Event()
            observed = []

            def published(publication):
                for change in publication.changes:
                    tree = change.to_tree() if hasattr(change, "to_tree") else {}
                    value = tree.get("value", {})
                    if tree.get("type") == "task" and value.get("id") == task_id:
                        outcome = value["state"].get("outcome", {})
                        if (
                            outcome.get("status") == "faulted"
                            or value["state"].get("checkpoint", {}).get("phase")
                            == "blocked"
                        ):
                            observed.append(value)
                            committed.set()

            unsubscribe = product._session.subscribe_commits(published)
            with lock:
                state["target"] = task_id
                state["active"] = True
            running = asyncio.create_task(product.resume())
            try:
                assert await asyncio.to_thread(committed.wait, 10)
                row = await read_row(product, task_id)
                actual = await material(product, admission, task_id)
                assert snapshot()["entered"] == 1 and len(observed) == 1
                assert (
                    actual["audit"] == after_ack["audit"]
                    and actual["result_entries"] == []
                    and counts(tmp_path) == before
                )
                if mode == "reblock":
                    assert snapshot()["validation_faults"] == 1
                    assert row["state"] == {
                        "status": "pending",
                        "checkpoint": {
                            "phase": "blocked",
                            "version": 1,
                            "intent": None,
                            "reason": "incompatible",
                            "call": row["input"],
                        },
                    }
                    assert actual["receipt"] == after_ack["receipt"]
                    assert (await running).status == "held"
                else:
                    assert (
                        row["state"]["status"] == "terminal"
                        and row["state"]["outcome"]["status"] == "faulted"
                    )
                    assert actual["task_documents"] == [] and actual["receipt"] is None
                    assert snapshot()["normal_return"] == (mode == "no_progress")
                save(
                    tmp_path,
                    "post-ack-state-witness.json",
                    {
                        "mode": mode,
                        "hook": snapshot(),
                        "actual_task": row,
                        "committed_observations": observed,
                        "ack": ack.to_tree(),
                        "counts": counts(tmp_path),
                        "parent_origins": native_origins(),
                    },
                )
            finally:
                unsubscribe()
                await product.close()
                cleanup = {"explicit_close": True, "resume_exit": "returned"}
                try:
                    await running
                except DurableProductError as error:
                    # Public resume retains its fixed error boundary during close.
                    assert error.code == "closed"
                    cleanup.update(resume_exit=type(error).__name__, code=error.code)
                save(tmp_path, "resume-close-observation.json", cleanup)
        await cold_ack(tmp_path, task_id, ack)
        save(
            tmp_path,
            "after-cold-witness.json",
            {"counts": counts(tmp_path), "hook": snapshot(), "ack": ack.to_tree()},
        )

    asyncio.run(asyncio.wait_for(scenario(), 30))

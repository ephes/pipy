"""Actual Session-line C2 transitions and deliberately injected authority races."""

import asyncio
import threading

import pytest
from adjudication_sdk_support import logical
from test_tool_adjudication import held_product
from test_tool_lifecycle import independent_opener
from tool_support import counts

from pipy_harness.native.durable import adjudication, tool_execution
from pipy_harness.native.durable.generation_transport import owned_call
from pipy_harness.native.durable.inspection_read import peek_document
from pipy_harness.native.durable.records import TaskId
from pipy_harness.native.durable.resolution_values import ResolutionError
from pipy_harness.native.durable.tool_execution import RECEIPT_DOC
from pipy_harness.native.durable.tx import TransactionSettled, TxAffinityError
from pipy_harness.sdk import DurableProductError, open_durable_product_session


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


@pytest.mark.parametrize("rollback", [False, True])
def test_actual_new_receipt_identity_atomic_audit_and_rollback(
    tmp_path, monkeypatch, rollback
):
    original = adjudication._acquired_receipt_identity
    identities, handles = [], []

    def observed(tx, task_id):
        value = original(tx, task_id)
        identities.append(value)
        handles.append(tx)
        if rollback:
            raise ResolutionError("incompatible")
        return value

    monkeypatch.setattr(adjudication, "_acquired_receipt_identity", observed)

    async def scenario():
        async with held_product(tmp_path) as (product, admission, task_id):
            before = await owned_call(lambda: logical(product._session))
            effects = counts(tmp_path)
            if rollback:
                with pytest.raises(DurableProductError) as refused:
                    await product.adjudicate_interrupted(task_id, nonce="a" * 32)
                assert refused.value.code == "incompatible"
                assert await owned_call(lambda: logical(product._session)) == before
            else:
                await product.adjudicate_interrupted(task_id, nonce="a" * 32)
            assert len(identities) == 1
            saved, audit = await product._read(
                lambda: (
                    peek_document(
                        product._session._owner.storage,
                        RECEIPT_DOC,
                        admission.conversation_id,
                        task_id,
                    ),
                    peek_document(
                        product._session._owner.storage,
                        adjudication.AUDIT,
                        admission.conversation_id,
                        key="a" * 32,
                    ),
                )
            )
            if rollback:
                assert saved is None and audit is None
            else:
                assert adjudication.document_identity(saved) == identities[0]
                assert audit["value"]["receipt"]["identity"] == identities[0]
            # A borrowed Tx handle cannot expose metadata after its transaction.
            with pytest.raises(TransactionSettled):
                original(handles[0], task_id)
            assert counts(tmp_path) == effects

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_private_identity_seam_uses_actual_transaction_affinity(tmp_path, monkeypatch):
    original = adjudication._acquired_receipt_identity
    refused = []

    def observed(tx, task_id):
        def foreign():
            try:
                original(tx, task_id)
            except TxAffinityError as error:
                refused.append(type(error).__name__)

        thread = threading.Thread(target=foreign)
        thread.start()
        thread.join(5)
        assert not thread.is_alive()
        assert refused == ["TxAffinityError"]
        return original(tx, task_id)

    monkeypatch.setattr(adjudication, "_acquired_receipt_identity", observed)

    async def scenario():
        async with held_product(tmp_path) as (product, _, task_id):
            await product.adjudicate_interrupted(task_id, nonce="a" * 32)
        assert refused == ["TxAffinityError"]

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_identical_payload_replacement_cannot_replay_old_document_identity(tmp_path):
    async def scenario():
        async with held_product(tmp_path) as (product, admission, task_id):
            await product.adjudicate_interrupted(task_id, nonce="a" * 32)
            saved = await product._read(
                lambda: peek_document(
                    product._session._owner.storage,
                    RECEIPT_DOC,
                    admission.conversation_id,
                    task_id,
                )
            )

            def replace(tx):
                tx.retire_doc(RECEIPT_DOC, task_id=task_id)
                tx.doc(RECEIPT_DOC, task_id=task_id).update(saved["value"])

            await owned_call(lambda: product._session.commit(replace))
            newer = await product._read(
                lambda: peek_document(
                    product._session._owner.storage,
                    RECEIPT_DOC,
                    admission.conversation_id,
                    task_id,
                )
            )
            assert newer["value"] == saved["value"]
            assert newer["record"]["id"] != saved["record"]["id"]
            before = await owned_call(lambda: logical(product._session))
            with pytest.raises(DurableProductError) as refused:
                await product.adjudicate_interrupted(task_id, nonce="a" * 32)
            assert refused.value.code == "incompatible"
            assert await owned_call(lambda: logical(product._session)) == before

    asyncio.run(asyncio.wait_for(scenario(), 20))


@pytest.mark.parametrize("mutation", ["abort", "parent_abort", "late_receipt"])
def test_same_line_revalidation_refuses_actual_intervening_commit(
    tmp_path, monkeypatch, mutation
):
    original = adjudication.commit
    reached = []

    def race(session, path, workspace, current, expected, nonce, scheduler):
        facts = expected.facts.thaw()
        task_id = facts["task"]["id"]

        def change(tx):
            if mutation == "late_receipt":
                # Explicit late committed-receipt control, not a fictitious late ToolPort return.
                tx.doc(RECEIPT_DOC, task_id=task_id).update(expected.saved.thaw())
            else:
                target = (
                    facts["task"]["owner"] if mutation == "parent_abort" else task_id
                )
                row = tx.task(target).to_tree()
                tx.set_task({**row, "abortRequested": True})

        session.commit(change)
        reached.append(logical(session))
        return original(session, path, workspace, current, expected, nonce, scheduler)

    monkeypatch.setattr(adjudication, "commit", race)

    async def scenario():
        async with held_product(tmp_path) as (product, _, task_id):
            effects = counts(tmp_path)
            with pytest.raises(DurableProductError) as refused:
                await product.adjudicate_interrupted(task_id, nonce="a" * 32)
            assert refused.value.code == "stale"
            assert len(reached) == 1
            assert await owned_call(lambda: logical(product._session)) == reached[0]
            assert counts(tmp_path) == effects

    asyncio.run(asyncio.wait_for(scenario(), 20))


@pytest.mark.parametrize("nonce", ["a" * 32, "b" * 32])
@pytest.mark.parametrize(
    "mutation", ["empty", "proposal", "version", "identity", "digest", "authority"]
)
def test_corrupt_committed_audit_refuses_replay_and_second_decision(
    tmp_path, nonce, mutation
):
    async def scenario():
        async with held_product(tmp_path) as (product, admission, task_id):
            await product.adjudicate_interrupted(task_id, nonce="a" * 32)

            def corrupt(tx):
                doc = tx.doc(
                    adjudication.AUDIT,
                    conversation_id=admission.conversation_id,
                    key="a" * 32,
                    seed=None,
                )
                value = doc.snapshot().thaw()
                if mutation == "empty":
                    value = {}
                elif mutation == "proposal":
                    value["proposal"] = {"task_id": []}
                elif mutation == "version":
                    value["version"] = 2
                elif mutation == "identity":
                    value["receipt"]["identity"]["record"]["id"] += 1
                elif mutation == "digest":
                    value["receipt"]["digest"] = "0" * 64
                else:
                    value["authority"]["key"] = "private-corrupt-authority"
                doc.clear()
                doc.update(value)

            await owned_call(lambda: product._session.commit(corrupt))
            before = await owned_call(lambda: logical(product._session))
            effects = counts(tmp_path)
            with pytest.raises(DurableProductError) as refused:
                await product.adjudicate_interrupted(task_id, nonce=nonce)
            assert refused.value.code == "incompatible"
            assert "private-corrupt-authority" not in str(refused.value)
            assert await owned_call(lambda: logical(product._session)) == before
            assert counts(tmp_path) == effects

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_cancelled_decision_and_close_retain_actual_commit_and_lease(
    tmp_path, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    original = adjudication._acquired_receipt_identity

    def held(tx, task_id):
        identity = original(tx, task_id)
        entered.set()
        assert release.wait(15)
        return identity

    monkeypatch.setattr(adjudication, "_acquired_receipt_identity", held)

    async def scenario():
        async with held_product(tmp_path) as (product, _, task_id):
            effects = counts(tmp_path)
            offered = asyncio.create_task(
                product.adjudicate_interrupted(task_id, nonce="a" * 32)
            )
            assert await asyncio.to_thread(entered.wait, 5)
            offered.cancel()
            with pytest.raises(asyncio.CancelledError):
                await offered
            assert any(not operation.done() for operation in product._operations)
            closing = asyncio.create_task(product.close())
            await asyncio.sleep(0)
            assert not closing.done()
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            await asyncio.to_thread(
                independent_opener,
                tmp_path / "product.sqlite",
                tmp_path,
                "adjudication-commit-held",
                "refused",
            )
            assert counts(tmp_path) == effects
            release.set()
            await asyncio.wait_for(product.close(), 5)
            assert not product._scheduler._thread.is_alive()
        await asyncio.to_thread(
            independent_opener,
            tmp_path / "product.sqlite",
            tmp_path,
            "adjudication-commit-closed",
            "opened",
        )
        async with await open_durable_product_session(
            tmp_path / "product.sqlite", workspace=tmp_path, tools={}
        ) as reopened:
            decision = await reopened.adjudicate_interrupted(task_id, nonce="a" * 32)
            assert decision.decision == "interrupted"
            assert counts(tmp_path) == effects

    try:
        asyncio.run(asyncio.wait_for(scenario(), 20))
    finally:
        release.set()


def test_commit_success_ack_failure_replays_actual_winner(tmp_path, monkeypatch):
    original, committed = adjudication.commit, []

    def lost_ack(*args, **kwargs):
        decision = original(*args, **kwargs)
        committed.append(decision)
        raise RuntimeError("private-ack-failure")

    monkeypatch.setattr(adjudication, "commit", lost_ack)

    async def scenario():
        async with held_product(tmp_path) as (product, _, task_id):
            effects = counts(tmp_path)
            with pytest.raises(DurableProductError) as refused:
                await product.adjudicate_interrupted(task_id, nonce="a" * 32)
            assert refused.value.code == "unavailable"
            assert "private-ack-failure" not in str(refused.value)
            assert len(committed) == 1
        async with await open_durable_product_session(
            tmp_path / "product.sqlite", workspace=tmp_path, tools={}
        ) as reopened:
            assert (
                await reopened.adjudicate_interrupted(task_id, nonce="a" * 32)
                == committed[0]
            )
            assert len(committed) == 1
            assert counts(tmp_path) == effects

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_kernel_abort_after_decision_replays_ack_without_retired_receipt(
    tmp_path, monkeypatch
):
    """Abort is an actual scheduler control here, not a claimed product API."""
    original = tool_execution._Tool.settle
    lock, selected, injections = threading.Lock(), [], []

    async def kernel_abort(self, record, runtime, context):
        with lock:
            active = selected == [record["id"]]
        if not active:
            return await original(self, record, runtime, context)

        def aborted(tx, current):
            injections.append(current["id"])
            return {
                "status": "terminal",
                "outcome": {"status": "aborted", "reason": "synthetic_kernel_abort"},
            }

        await runtime.commit(aborted)

    monkeypatch.setattr(tool_execution._Tool, "settle", kernel_abort)

    async def scenario():
        async with held_product(tmp_path) as (product, admission, task_id):
            effects = counts(tmp_path)
            ack = await product.adjudicate_interrupted(task_id, nonce="a" * 32)
            observed, terminal = [], threading.Event()

            def published(publication):
                for change in publication.changes:
                    value = change.to_tree()
                    row = value.get("value", {})
                    if (
                        value.get("type") == "task"
                        and row.get("id") == task_id
                        and row["state"]["status"] == "terminal"
                    ):
                        observed.append(row)
                        terminal.set()

            unsubscribe = product._session.subscribe_commits(published)
            try:
                assert injections == []
                with lock:
                    selected.append(task_id)
                product._scheduler.resume()
                assert await asyncio.to_thread(terminal.wait, 5)
                await product._scheduler.apause()
            finally:
                unsubscribe()
            assert len(observed) == 1 and injections == [task_id]
            assert observed[0]["state"]["outcome"]["status"] == "aborted"
            row, saved = await product._read(
                lambda: (
                    product._session._owner.storage.task(TaskId(task_id)).to_tree(),
                    peek_document(
                        product._session._owner.storage,
                        RECEIPT_DOC,
                        admission.conversation_id,
                        task_id,
                    ),
                )
            )
            assert row == observed[0] and saved is None
            before = await owned_call(lambda: logical(product._session))
            assert await product.adjudicate_interrupted(task_id, nonce="a" * 32) == ack
            assert await owned_call(lambda: logical(product._session)) == before
            assert counts(tmp_path) == effects
        async with await open_durable_product_session(
            tmp_path / "product.sqlite", workspace=tmp_path, tools={}
        ) as reopened:
            assert await reopened.adjudicate_interrupted(task_id, nonce="a" * 32) == ack
            assert counts(tmp_path) == effects

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_no_write_oracle_detects_revisions_with_identical_restored_payload(tmp_path):
    async def scenario():
        async with held_product(tmp_path) as (product, admission, task_id):
            await product.adjudicate_interrupted(task_id, nonce="a" * 32)
            before = await owned_call(lambda: logical(product._session))

            def edit(tx, restore):
                doc = tx.doc(
                    adjudication.AUDIT,
                    conversation_id=admission.conversation_id,
                    key="a" * 32,
                    seed=None,
                )
                if restore:
                    del doc["temporary_oracle_control"]
                else:
                    doc["temporary_oracle_control"] = True

            await owned_call(
                lambda: product._session.commit(lambda tx: edit(tx, False))
            )
            await owned_call(lambda: product._session.commit(lambda tx: edit(tx, True)))
            after = await owned_call(lambda: logical(product._session))
            for table in ("tasks", "entries", "conversations", "documents"):
                assert after[table] == before[table]
            assert after["document_revisions"] != before["document_revisions"]
            assert after != before
            assert (
                await product.adjudicate_interrupted(task_id, nonce="a" * 32)
            ).decision == "interrupted"
            assert await owned_call(lambda: logical(product._session)) == after

    asyncio.run(asyncio.wait_for(scenario(), 20))

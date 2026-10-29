"""Finite ordinary-ownership semantics on actual synchronous Session transactions."""

import asyncio
import dataclasses
import threading

import pytest
from task_migration_support import Rig, definition

from pipy_harness.native.durable.ordinary_ownership import OwnedLiveQuery
from pipy_harness.native.durable.records import RecordError, TaskRecord
from pipy_harness.native.durable.session_core import SyncTx
from pipy_harness.native.durable.storage import Cursor, Page
from pipy_harness.native.durable.tx import (
    ReadAfterWrite,
    TransactionSettled,
    TxAffinityError,
)

INVALID = "^Invalid ordinary ownership graph$"
DONE = {"status": "terminal", "outcome": {"status": "completed", "result": None}}


@pytest.fixture
def rig(tmp_path):
    value = Rig("memory", tmp_path)
    try:
        yield value
    finally:
        value.close()


def child(tx, owner):
    return tx.create_task(
        definition(), None, ownership={"kind": "task", "taskId": owner}
    )


def replace(tx, row, **fields):
    tx.set_task({**row, **fields})


def query(rig, target):
    before = rig.silence()
    answer = rig.session.commit(lambda tx: OwnedLiveQuery.capture(tx).has(target))
    rig.assert_silent(before)
    return answer


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_real_staged_graph_query_is_silent_after_intentional_mint(backend, tmp_path):
    rig = Rig(backend, tmp_path)
    try:
        root = rig.create(definition())

        def apply(tx):
            view = OwnedLiveQuery.capture(tx)
            assert view.has(root) is False
            created = child(tx, root)
            staged = tx.staged_tasks()
            commits, mints = len(rig.storage.commits), len(rig.storage.mints)
            assert view.has(root) is True and view.has(created) is False
            assert tx.staged_tasks() == staged
            assert (len(rig.storage.commits), len(rig.storage.mints)) == (
                commits,
                mints,
            )
            if backend == "memory":
                published = rig.storage.inner._state
                assert view.has(root) is True
                assert rig.storage.inner._state is published
            return created

        created = rig.session.commit(apply)
        assert query(rig, root) is True
        assert query(rig, created) is False
    finally:
        rig.close()


@pytest.mark.parametrize(
    "status", ["pending", "running", "waiting", "sleeping", "completing", "terminal"]
)
def test_live_partition_and_root_self_exclusion(rig, status):
    root = rig.create(definition())
    target = rig.session.commit(lambda tx: child(tx, root))
    state = {"status": status, "checkpoint": {"phase": "start"}}
    if status in {"terminal", "completing"}:
        state = {"status": status, "outcome": {"status": "completed", "result": None}}
    elif status == "waiting":
        state |= {"on": [root], "policy": "allSettled"}
    elif status == "sleeping":
        state["until"] = 123
    rig.change(target, lambda row: row.update(state=state))
    assert query(rig, root) is (status != "terminal")
    assert query(rig, target) is False


def test_terminal_and_conversation_edges_and_background_root(rig):
    root = rig.create(definition())
    middle = rig.session.commit(lambda tx: child(tx, root))

    def create(tx):
        conversation = tx.create_conversation({"kind": "task", "taskId": middle})
        return tx.create_task(
            definition(),
            None,
            ownership={"kind": "conversation"},
            conversation_id=conversation.id,
        )

    leaf = rig.session.commit(create)
    rig.change(middle, lambda row: row.update(state=DONE))
    rig.change(root, lambda row: row.update(background=True))
    assert query(rig, root) is True
    rig.change(leaf, lambda row: row.update(state=DONE))
    assert query(rig, root) is False


def test_committed_owned_background_is_a_real_boundary(rig):
    root = rig.create(definition())
    background = rig.session.commit(lambda tx: child(tx, root))
    leaf = rig.session.commit(lambda tx: child(tx, background))
    rig.change(background, lambda row: row.update(background=True))
    assert query(rig, root) is False
    assert query(rig, background) is True
    assert query(rig, leaf) is False


@pytest.mark.parametrize(
    "mode", ["phantom", "terminal_repeat", "revive", "owner", "background"]
)
def test_real_replace_authority_refusals_are_no_write(rig, mode):
    root = rig.create(definition())
    other = rig.create(definition())
    if mode in {"terminal_repeat", "revive"}:
        rig.change(root, lambda row: row.update(state=DONE))
    before = rig.silence()

    class Rollback(Exception):
        pass

    def apply(tx):
        row = tx.task(root).to_tree()
        view = OwnedLiveQuery.capture(tx)
        if mode == "phantom":
            row["id"] = 900000
        elif mode == "revive":
            row["state"] = {"status": "pending", "checkpoint": {}}
        elif mode == "owner":
            row["owner"] = other
        elif mode == "background":
            row["background"] = True
        tx.set_task(row)
        staged = tx.staged_tasks()
        with pytest.raises(ValueError, match=INVALID):
            view.has(other)
        assert tx.staged_tasks() == staged
        raise Rollback

    with pytest.raises(Rollback):
        rig.session.commit(apply)
    rig.assert_silent(before)


@pytest.mark.parametrize("field", ["owner", "background"])
def test_genuine_create_then_set_task_uses_final_graph(rig, field):
    root = rig.create(definition())
    other = rig.create(definition())

    def apply(tx):
        view = OwnedLiveQuery.capture(tx)
        created = child(tx, root)
        row = next(row.to_tree() for row in tx.staged_tasks() if row.id == created)
        assert view.has(root) is True
        row[field] = other if field == "owner" else True
        tx.set_task(row)
        assert view.has(root) is False
        assert view.has(other) is (field == "owner")
        return created

    created = rig.session.commit(apply)
    assert rig.record(created)[field] == (other if field == "owner" else True)
    assert query(rig, root) is False


@pytest.mark.parametrize("owner_state", ["terminal", "completing", "abort"])
@pytest.mark.parametrize("new_work", ["task", "conversation"])
def test_graph_answer_does_not_predict_create_side_assembly(rig, owner_state, new_work):
    root = rig.create(definition())
    before = rig.storage.inner._state
    observed = []

    def apply(tx):
        row = tx.task(root).to_tree()
        view = OwnedLiveQuery.capture(tx)
        if new_work == "task":
            child(tx, root)
        else:
            conv = tx.create_conversation({"kind": "task", "taskId": root})
            tx.create_task(
                definition(),
                None,
                ownership={"kind": "conversation"},
                conversation_id=conv.id,
            )
        if owner_state == "abort":
            row["abortRequested"] = True
        else:
            row["state"] = {**DONE, "status": owner_state}
        tx.set_task(row)
        published = rig.storage.inner._state
        staged = tx.staged_tasks()
        observed.append(view.has(root))
        assert rig.storage.inner._state is published
        assert tx.staged_tasks() == staged

    with pytest.raises(RuntimeError, match="is (terminal|completing|abort-marked)"):
        rig.session.commit(apply)
    assert observed == [True]
    after = rig.storage.inner._state
    # Real ID allocation is not rolled back. No other published state changes.
    assert dataclasses.replace(after, next_id=before.next_id) == before
    assert after.next_id > before.next_id


@pytest.mark.parametrize(
    "corruption", ["dangling", "self", "cycle", "cross_conversation"]
)
def test_genuinely_committed_bad_owner_refuses_unrelated_root(rig, corruption):
    root = rig.create(definition())
    a = rig.create(definition())
    b = rig.create(definition())
    if corruption == "cross_conversation":
        conversation = rig.session.commit(
            lambda tx: tx.create_conversation({"kind": "ownerless"})
        )
        b = rig.session.commit(
            lambda tx: tx.create_task(
                definition(),
                None,
                ownership={"kind": "conversation"},
                conversation_id=conversation.id,
            )
        )
    rig.change(
        a,
        lambda row: row.update(
            owner=900000
            if corruption == "dangling"
            else a
            if corruption == "self"
            else b
        ),
    )
    if corruption == "cycle":
        rig.change(b, lambda row: row.update(owner=a))
    before = rig.silence()

    def apply(tx):
        with pytest.raises(ValueError, match=INVALID):
            OwnedLiveQuery.capture(tx).has(root)

    rig.session.commit(apply)
    rig.assert_silent(before)


@pytest.mark.parametrize("value", [True, 0, -1, 1.0, "1", None, 2**53])
def test_id_and_unknown_errors_do_not_mutate(rig, value):
    root = rig.create(definition())
    before = rig.silence()

    def apply(tx):
        view = OwnedLiveQuery.capture(tx)
        with pytest.raises(TypeError, match="Expected a task ID"):
            view.has(value)
        with pytest.raises(ValueError, match="^Unknown task$"):
            view.has(900000)
        assert view.has(root) is False

    rig.session.commit(apply)
    rig.assert_silent(before)


def test_lifecycle_precedes_argument_and_candidates(rig):
    root = rig.create(definition())
    escaped = []
    errors = []

    def apply(tx):
        view = OwnedLiveQuery.capture(tx)
        escaped.append(view)

        def wrong_thread():
            try:
                view.has("invalid")
            except TxAffinityError as exc:
                errors.append(exc)

        worker = threading.Thread(target=wrong_thread)
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
        assert isinstance(errors[0], TxAffinityError)
        assert view.has(root) is False

    rig.session.commit(apply)
    with pytest.raises(TransactionSettled):
        escaped[0].has("invalid")


def test_capture_refuses_after_write_and_async_facade(rig):
    def apply(tx):
        with pytest.raises(RecordError, match="Expected plain record tree"):
            tx.set_task(None)
        with pytest.raises(ReadAfterWrite):
            OwnedLiveQuery.capture(tx)

    rig.session.commit(apply)

    async def body(tx):
        with pytest.raises(TypeError, match="Expected a SyncTx"):
            OwnedLiveQuery.capture(tx)

    asyncio.run(rig.session.acommit(body))
    with pytest.raises(TypeError, match="capture"):
        OwnedLiveQuery()


def test_projection_stores_no_record_payloads(rig):
    root = rig.create(definition(), {"large": ["irrelevant"] * 1000})

    def apply(tx):
        view = OwnedLiveQuery.capture(tx)
        projected = view._tasks[root]
        assert {field.name for field in dataclasses.fields(projected)} == {
            "conversation",
            "owner",
            "background",
            "terminal",
        }
        assert all(
            value is None or type(value) in (int, bool)
            for value in dataclasses.astuple(projected)
        )
        with pytest.raises(TypeError):
            view._tasks[root] = projected
        assert view.has(root) is False

    rig.session.commit(apply)


@pytest.mark.parametrize(
    "fault", ["duplicate", "bad_cursor", "empty_cursor", "wrong_record"]
)
def test_labelled_kernel_scan_protocol_controls(rig, monkeypatch, fault):
    root = rig.create(definition())
    original = SyncTx.scan_tasks

    def scan(tx, query, limit, cursor=None):
        page = original(tx, query, limit, cursor)
        assert limit == 128
        if fault == "duplicate":
            return Page((page.items[0], page.items[0]))
        if fault == "bad_cursor":
            return Page(page.items, Cursor(root + 1))
        if fault == "empty_cursor":
            return Page((), Cursor(root))
        return Page((tx.conversation(rig.conversation),))

    monkeypatch.setattr(SyncTx, "scan_tasks", scan)

    def apply(tx):
        with pytest.raises(ValueError, match=INVALID):
            OwnedLiveQuery.capture(tx)

    rig.session.commit(apply)


def test_labelled_kernel_staged_identity_mismatch(rig):
    root = rig.create(definition())

    def apply(tx):
        view = OwnedLiveQuery.capture(tx)
        created = child(tx, root)
        kind, record = tx._core._tasks[created].write
        original = SyncTx.staged_tasks

        # Impossible facade protocol: equal bytes, different authority object.
        def wrong_staged(current):
            return (
                (TaskRecord(record.to_tree()),) if current is tx else original(current)
            )

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(SyncTx, "staged_tasks", wrong_staged)
            with pytest.raises(ValueError, match=INVALID):
                view.has(root)
        assert kind == "create" and view.has(root) is True

    rig.session.commit(apply)

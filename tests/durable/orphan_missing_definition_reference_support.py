"""Actual Task/Tx roots and one explicit control; no public abort or crash claim."""

from __future__ import annotations

import asyncio
import copy
import sqlite3
import threading
from contextlib import closing
from importlib import import_module

from pipy_harness.native.durable.documents import define_doc
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.restart_policy import RestartPolicy
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import DocumentQuery
from pipy_harness.native.durable.tasks import define_task

values = import_module("durable_reference.orphan_missing_definition_values")
previous = import_module("definition_publication_reference_support")
origins = previous.origins
sqlite_snapshot = previous.sqlite_snapshot
NOTE = define_doc(
    values.DOC_KINDS[0],
    1,
    "task",
    lambda: {"n": 0},
    checkpoint_when=lambda _value, _ops, _info: False,
)
CACHED = define_doc(
    values.DOC_KINDS[1],
    1,
    "task",
    lambda: {"n": 0},
    checkpoint_when=lambda _value, _ops, _info: False,
)
OTHER = define_doc(
    "fixture.orphan.unrelated",
    1,
    "conversation",
    lambda: {"n": 0},
    history="rewindable",
    fork="current",
    checkpoint_when=lambda _value, _ops, _info: False,
)


class Recording:
    """Forward one owned backend; retain successful real commits and allocations."""

    def __init__(self, inner, batches, minted, lock):
        self.inner, self.batches, self.minted, self.lock = inner, batches, minted, lock

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def commit(self, writes):
        body = [w.to_tree() for w in writes]
        seq = self.inner.commit(writes)
        with self.lock:
            self.batches.append({"seq": seq, "writes": body})
        return seq

    def mint_id(self):
        result = self.inner.mint_id()
        with self.lock:
            self.minted.append(result)
        return result


def _documents(store, scope):
    cursor, result = None, []
    while True:
        page = store.scan_documents(
            DocumentQuery({"scope": scope, "at": "current"}), 128, cursor
        )
        for row in page.items:
            value = store.document(row.id)
            result.append(
                {
                    "record": row.to_tree(),
                    "materialized": None if value is None else value.to_tree(),
                }
            )
        if page.next is None:
            return result
        cursor = page.next


def _callbacks(counter_path, counts, callback_log, lock):
    def ledger():
        with closing(
            sqlite3.connect(counter_path.as_uri() + "?mode=ro", uri=True)
        ) as db:
            db.execute("PRAGMA query_only=ON")
            return [
                {"seq": n, "kind": k}
                for n, k in db.execute("SELECT seq,kind FROM calls ORDER BY seq")
            ]

    def called(name):
        with lock:
            with closing(sqlite3.connect(counter_path)) as db, db:
                db.execute("INSERT INTO calls(kind) VALUES (?)", (name,))
            counts[name] += 1
            callback_log.append(name)

    def initial(value):
        called("initial")
        return {"phase": "run"}

    async def phase(*args):
        called("handler")
        raise AssertionError("Target phase outside missing-definition profile")

    async def abort(*args):
        called("abort")
        raise AssertionError("Target abort handler outside missing-definition profile")

    def migrate(value, checkpoint, version):
        called("migrate")
        raise AssertionError("Target migration outside missing-definition profile")

    task = define_task(
        values.TASK_NAME, 1, initial, {"run": phase}, abort, migrate=migrate
    )

    return task, ledger


def _snapshots(
    owner, case, path, tid, cid, counts, batches, minted, lock, ledger, steps
):
    def snapshot(label, ack=None):
        session, store = owner()

        def read():
            found = store.task(tid)
            assert found is not None
            record = found.to_tree()
            documents = {
                "target": _documents(store, {"kind": "task", "taskId": tid}),
                "conversation": _documents(
                    store, {"kind": "conversation", "conversationId": cid}
                ),
            }
            with lock:
                rows, counter = ledger(), dict(counts)
                n_batches, n_minted = len(batches), len(minted)
                tape = {
                    "kind": "memory-current-only",
                    "commit_batches": copy.deepcopy(batches),
                    "minted": list(minted),
                }
            backend = sqlite_snapshot(path) if case["backend"] == "sqlite" else tape
            return {
                "label": label,
                "record": record,
                "documents": documents,
                "counts": counter,
                "callback_rows": rows,
                "backend": backend,
                "batch_count": n_batches,
                "mint_count": n_minted,
                "ACK": ack,
            }

        step = session.read_on_line(read)
        steps.append(step)
        return step

    return snapshot


def exercise(case, folder):
    owners = []
    failure = None
    try:
        return _exercise(case, folder.resolve(), owners)
    except BaseException as error:
        failure = error
        raise
    finally:
        errors = []
        for owner in reversed(owners):
            try:
                owner.close(timeout=10)
            except BaseException as error:  # noqa: BLE001 - join every real owner
                errors.append(error)
        if errors:
            raise BaseExceptionGroup(
                "Orphan fixture cleanup", ([failure] if failure else []) + errors
            )


def _orphan_action(scheduler, case, task, tid, before, snapshot):
    info = scheduler.inspect_task(tid)
    assert info is not None
    policy = RestartPolicy(5)
    policy_observation = {
        "configured_limit": policy.limit,
        "derived_parked": policy.parked(before["record"]),
        "inspection": info.to_tree(),
    }
    if case["parked"]:
        assert policy_observation["derived_parked"] is True
    with scheduler._guard:
        assert scheduler._enabled is False
    ack = scheduler.orphan_missing_definition(tid)
    snapshot("orphan", ack)
    if case["install"]:
        assert scheduler.install_definition(task) is True
        snapshot("install-definition")
    snapshot("repeat", asyncio.run(scheduler.aorphan_missing_definition(tid)))
    return policy_observation


def _exercise(case, folder, owners):
    folder.mkdir(parents=True)
    path = folder / "session.sqlite"
    counter_path = folder / "callback-counter.sqlite"
    with closing(sqlite3.connect(counter_path)) as db, db:
        db.execute(
            "CREATE TABLE calls(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL)"
        )
    lock = threading.RLock()
    counts = {"initial": 0, "migrate": 0, "handler": 0, "abort": 0}
    callback_log, batches, minted, closed = [], [], [], []

    task, ledger = _callbacks(counter_path, counts, callback_log, lock)

    def owner(mode):
        inner = (
            MemoryStorage()
            if case["backend"] == "memory"
            else open_sqlite_storage(str(path), mode=mode)
        )
        store = Recording(inner, batches, minted, lock)
        session = Session(store, now=lambda: 1000)
        owners.append(session)
        return session, store

    session, store = owner("create")

    def create(tx):
        cid = tx.create_conversation({"kind": "ownerless"}).id
        tid = tx.create_task(
            task,
            {"fixture": case["id"]},
            ownership={"kind": "conversation"},
            conversation_id=cid,
        )
        tx.doc(NOTE, task_id=tid)["n"] = 1
        tx.doc(CACHED, task_id=tid)["n"] = 2
        tx.doc(OTHER, conversation_id=cid)["n"] = 1
        return cid, tid

    cid, tid = session.commit(create)

    # Real reversible unrelated edit gives revision equality an actual discriminator.
    def unrelated(value):
        def change(tx):
            tx.doc(OTHER, conversation_id=cid)["n"] = value

        return change

    session.commit(unrelated(2))
    session.commit(unrelated(1))
    scheduler = None
    steps = []

    snapshot = _snapshots(
        lambda: (session, store),
        case,
        path,
        tid,
        cid,
        counts,
        batches,
        minted,
        lock,
        ledger,
        steps,
    )

    created = snapshot("created")["record"]
    if case["parked"]:

        def seed(tx):
            row = tx.task(tid).to_tree()
            row["restarts"] = 5
            row["memos"] = {"fixture.memo": {"kept": True}}
            tx.set_task(row)

        session.commit(seed)
        snapshot("seeded-restarts")
    if case["cold"]:
        session.close(timeout=10)
        closed.append(session.end.reason)
        session, store = owner("open")
    scheduler = TaskScheduler(session, [], restart_limit=5)
    scheduler.open()
    if case["cold"]:
        snapshot("unmarked-reopen")
    before = snapshot("before-action")
    policy_observation = _orphan_action(scheduler, case, task, tid, before, snapshot)
    if case["backend"] == "sqlite":
        session.close(timeout=10)
        closed.append(session.end.reason)
        session, store = owner("open")
        scheduler = TaskScheduler(session, [task] if case["install"] else [])
        scheduler.open()
        snapshot("terminal-reopen")
        snapshot("repeat-after-reopen", scheduler.orphan_missing_definition(tid))
    final_backend = steps[-1]["backend"]
    session.close(timeout=10)
    closed.append(session.end.reason)
    with lock:
        final_batches, final_minted = copy.deepcopy(batches), list(minted)
    return {
        "id": case["id"],
        "role": "native",
        "backend_kind": case["backend"],
        "control": {
            "actual_Task_Tx_creation": True,
            "typed_restart_memo_seed": case["parked"],
            "physical_SIGKILL": False,
            "fabricated_task_authority": False,
        },
        "created": created,
        "steps": steps,
        "commit_batches": final_batches,
        "minted": final_minted,
        "callback_log": callback_log,
        "callback_rows": ledger(),
        "callback_sqlite": sqlite_snapshot(counter_path),
        "closed": closed,
        "sqlite_final": sqlite_snapshot(path) if case["backend"] == "sqlite" else None,
        "final_backend": final_backend,
        "native_only": {
            "policy_observation": policy_observation,
            "paused_owner": True,
            "no_explicit_enable_or_notify_in_fixture": True,
            "Memory_history": "current materialization/commit tape, no numeric historical read",
            "strict_refusal_lifecycle_cancel": "separate native tests, not common Pi parity",
        },
        "mutations": "Actual Task/doc creation and reversible conversation edit; optional typed counter/memo control; one actual explicit orphan and stable repetition. Raw backend tables/history retained independently, never global cross-backend equality.",
    }

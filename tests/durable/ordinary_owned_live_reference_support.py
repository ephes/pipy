"""Real Session/Tx query fixtures; native execution is deliberately absent."""

from __future__ import annotations

import copy
import sqlite3
import threading
from contextlib import closing
from importlib import import_module

from definition_publication_reference_support import origins as origins
from definition_publication_reference_support import sqlite_snapshot
from orphan_missing_migration_reference_support import Recording

from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.ordinary_ownership import OwnedLiveQuery
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import (
    ConversationQuery,
    DocumentQuery,
    TaskQuery,
)
from pipy_harness.native.durable.tasks import define_task

values = import_module("durable_reference.ordinary_owned_live_values")
DONE = {"status": "terminal", "outcome": {"status": "completed", "result": None}}


def _scan(method, query):
    rows, cursor = [], None
    while True:
        page = method(query, 128, cursor)
        rows.extend(r.to_tree() for r in page.items)
        if page.next is None:
            return rows
        cursor = page.next


def _snapshot(storage, root, owned):
    documents = []
    for cid in (root, owned):
        documents.extend(
            _scan(
                storage.scan_documents,
                DocumentQuery(
                    {
                        "scope": {"kind": "conversation", "conversationId": cid},
                        "at": "current",
                    }
                ),
            )
        )
    return {
        "tasks": _scan(storage.scan_tasks, TaskQuery({"order": "ascending"})),
        "conversations": _scan(
            storage.scan_conversations, ConversationQuery({"order": "ascending"})
        ),
        "documents": documents,
    }


class Fixture:
    def __init__(self, case, folder):
        self.case, self.folder = case, folder
        folder.mkdir(parents=True, mode=0o700)
        self.path = folder / "session.sqlite"
        self.counter = folder / "callback-counter.sqlite"
        with closing(sqlite3.connect(self.counter)) as db, db:
            db.execute(
                "CREATE TABLE calls(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL)"
            )
        self.batches, self.minted, self.closed, self.owners = [], [], [], []
        self.lock = threading.RLock()
        self.open()

    def open(self):
        inner = (
            MemoryStorage()
            if self.case["backend"] == "memory"
            else open_sqlite_storage(
                str(self.path), mode="open" if self.path.exists() else "create"
            )
        )
        self.storage = Recording(inner, self.batches, self.minted, self.lock)
        self.session = Session(self.storage)
        self.owners.append(self.session)

    def close(self):
        self.session.close(timeout=10)
        assert self.session.end is not None
        self.closed.append(self.session.end.reason)
        self.owners.remove(self.session)

    def called(self, kind):
        with closing(sqlite3.connect(self.counter)) as db, db:
            db.execute("INSERT INTO calls(kind) VALUES (?)", (kind,))

    def ledger(self):
        with closing(
            sqlite3.connect(self.counter.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            db.execute("PRAGMA query_only=ON")
            return [
                {"seq": n, "kind": k}
                for n, k in db.execute("SELECT seq,kind FROM calls ORDER BY seq")
            ]

    def definition(self):
        def initial(value):
            self.called("initial:" + value["name"])
            return {"phase": "hold"}

        async def phase(*args):
            self.called("run:unexpected")
            raise AssertionError("Native ownership fixture has no execution")

        return define_task(values.TASK_NAME, 1, initial, {"hold": phase}, phase)

    def create(self):
        task = self.definition()

        def apply(tx):
            root = tx.create_conversation({"kind": "ownerless"}).id
            p = tx.create_task(
                task,
                {"name": "P"},
                ownership={"kind": "conversation"},
                conversation_id=root,
            )
            b = tx.create_task(
                task, {"name": "B"}, ownership={"kind": "task", "taskId": p}
            )
            a = tx.create_task(
                task, {"name": "A"}, ownership={"kind": "task", "taskId": b}
            )
            c = tx.create_conversation({"kind": "task", "taskId": a}).id
            z = tx.create_task(
                task,
                {"name": "Z"},
                ownership={"kind": "conversation"},
                conversation_id=c,
                background=True,
            )
            return {"root": root, "P": p, "B": b, "A": a, "C": c, "Z": z}

        self.ids = self.session.commit(apply)
        self.end("A")
        self.end("B")
        before = self.session.read_on_line(
            lambda: _snapshot(self.storage, self.ids["root"], self.ids["C"])
        )
        lifecycle = []
        if self.case["cold"]:
            self.close()
            lifecycle.append(
                {"action": "close", "reason": self.closed[-1], "before": before}
            )
            self.open()
            after = self.session.read_on_line(
                lambda: _snapshot(self.storage, self.ids["root"], self.ids["C"])
            )
            assert before == after
            lifecycle.append({"action": "reopen", "after": after, "preserved": True})
        self.ids["D"] = self.session.commit(
            lambda tx: tx.create_task(
                task,
                {"name": "D"},
                ownership={"kind": "conversation"},
                conversation_id=self.ids["C"],
            )
        )
        return lifecycle

    def end(self, name):
        def apply(tx):
            row = tx.task(self.ids[name])
            assert row is not None
            tx.set_task({**row.to_tree(), "state": copy.deepcopy(DONE)})

        self.session.commit(apply)

    def boundary(self):
        return self.session.read_on_line(
            lambda: {
                "snapshot": _snapshot(self.storage, self.ids["root"], self.ids["C"]),
                "batches": copy.deepcopy(self.batches),
                "minted": list(self.minted),
                "callbacks": self.ledger(),
                "backend": sqlite_snapshot(self.path)
                if self.case["backend"] == "sqlite"
                else None,
            }
        )

    def step(self, label):
        before = self.boundary()
        answer = self.session.commit(
            lambda tx: OwnedLiveQuery.capture(tx).has(self.ids["P"])
        )
        after = self.boundary()
        assert before == after, (
            "Actual query changed storage/callback/revision boundary"
        )
        return {
            "label": label,
            "snapshot": after["snapshot"],
            "callbacks": self.ledger(),
            "batch_count": len(self.batches),
            "mint_count": len(self.minted),
            "backend": after["backend"],
            "query": {
                "answer": answer,
                "before": before,
                "after": after,
                "silent": before == after,
            },
        }

    def result(self, lifecycle, steps):
        final_backend = self.boundary()
        self.close()
        return {
            "id": self.case["id"],
            "role": "native",
            "backend_kind": self.case["backend"],
            "control": {
                "actual_Task_Tx_creation": True,
                "native_direct_Tx_terminalization": True,
                "physical_SIGKILL": False,
            },
            "topology": self.ids,
            "steps": steps,
            "commit_batches": self.batches,
            "minted": self.minted,
            "callback_log": [r["kind"] for r in self.ledger()],
            "callback_rows": self.ledger(),
            "callback_sqlite": sqlite_snapshot(self.counter),
            "closed": self.closed,
            "sqlite_final": sqlite_snapshot(self.path)
            if self.case["backend"] == "sqlite"
            else None,
            "final_backend": final_backend,
            "native_only": {
                "execution": "DISABLED_NO_SCHEDULER",
                "terminalization": "ACTUAL_DIRECT_TX_NOT_OWNED_EXECUTION",
                "lifecycle": lifecycle,
            },
            "mutations": "Actual creation/direct terminalization writes; each separate query has full no-write witness",
        }


def exercise(case, folder):
    fixture = Fixture(case, folder.resolve())
    failure = None
    try:
        lifecycle = fixture.create()
        steps = [fixture.step("held")]
        fixture.end(case["first"])
        steps.append(fixture.step("after-first"))
        fixture.end("D" if case["first"] == "Z" else "Z")
        steps.append(fixture.step("released"))
        return fixture.result(lifecycle, steps)
    except BaseException as error:
        failure = error
        raise
    finally:
        errors = []
        for owner in reversed(fixture.owners):
            try:
                owner.close(timeout=10)
            except BaseException as error:  # noqa: BLE001 - join every actual owned Session
                errors.append(error)
        if errors:
            raise BaseExceptionGroup(
                "Ownership fixture cleanup", ([failure] if failure else []) + errors
            )

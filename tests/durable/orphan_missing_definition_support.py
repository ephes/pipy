"""Real Session/Tx controls for explicit missing-definition orphan tests."""

import threading
from contextlib import closing
from sqlite3 import connect

from pipy_harness.native.durable.documents import define_doc, define_doc_family
from pipy_harness.native.durable.storage import DocumentQuery


def current_documents(rig, task_id):
    def read():
        result, cursor = [], None
        while True:
            page = rig.storage.scan_documents(
                DocumentQuery(
                    {"scope": {"kind": "task", "taskId": task_id}, "at": "current"}
                ),
                256,
                cursor,
            )
            result.extend(page.items)
            cursor = page.next
            if cursor is None:
                return tuple(result)

    return rig.session.read_on_line(read)


def task_documents(rig, task_id, count, calls):
    def initial(seed):
        calls.append(("initial", seed))
        return {"saved": seed}

    family = define_doc_family(
        "orphan.target.documents",
        1,
        "task",
        initial,
        checkpoint_when=lambda _value, _ops, _info: False,
    )

    def create(tx):
        for number in range(count):
            tx.doc(family, task_id=task_id, key=str(number), seed=number)

    rig.session.commit(create)
    return family, current_documents(rig, task_id)


def unrelated_history(rig):
    token = define_doc(
        "orphan.unrelated.history",
        1,
        "conversation",
        lambda: {"saved": 1},
        history="rewindable",
        fork="initial",
        checkpoint_when=lambda _value, _ops, _info: False,
    )
    rig.session.commit(lambda tx: tx.doc(token, conversation_id=rig.conversation))

    def change(tx):
        tx.doc(token, conversation_id=rig.conversation)["saved"] = 2

    rig.session.commit(change)
    record = rig.session.snapshot(token, conversation_id=rig.conversation).record
    return token, record.id


def sqlite_document_rows(path, ids):
    """Backend-specific retirement oracle; never mutate history manually."""
    with closing(connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only = ON")
        db.execute("BEGIN")
        documents = tuple(
            row
            for row in db.execute(
                "SELECT id, retired_at, record FROM documents ORDER BY id"
            )
            if row[0] in ids
        )
        revisions = tuple(
            row
            for row in db.execute(
                "SELECT * FROM document_revisions ORDER BY document_id, seq"
            )
            if row[0] in ids
        )
        return documents, revisions


def populate_large_graph(rig, source, count=129):
    """Every row is created by the actual Tx, not denormalized SQL."""
    conversations, tasks = [], []

    def create(tx):
        for _ in range(count):
            conversation = tx.create_conversation({"kind": "ownerless"}).id
            conversations.append(conversation)
            tasks.append(
                tx.create_task(
                    source,
                    None,
                    ownership={"kind": "conversation"},
                    conversation_id=conversation,
                )
            )

    rig.session.commit(create)
    return conversations, tasks


def terminal_fields(row):
    return {
        key: value
        for key, value in row.items()
        if key not in {"state", "memos", "restarts", "endedAt"}
    }


def assert_orphan(rig, task_id, before):
    after = rig.record(task_id)
    assert after["state"] == {
        "status": "terminal",
        "outcome": {"status": "orphaned", "reason": "missing_task"},
    }
    assert terminal_fields(after) == terminal_fields(before)
    assert after["abortRequested"] is False
    assert "memos" not in after and "restarts" not in after
    assert type(after["endedAt"]) in {int, float}
    return after


class CommitHold:
    """Hold one real forwarded storage commit before or after durable adoption."""

    def __init__(self, storage, stage):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.retired = threading.Event()
        self.calls = []

        def observe(writes):
            self.calls.append(writes)
            self.entered.set()
            try:
                assert self.release.wait(10)
            finally:
                self.retired.set()

        assert stage in {"before", "after"}
        setattr(storage, stage + "_commit", observe)


class GraphScans:
    """Forward exact-instance class methods and retain actual graph page reads."""

    def __init__(self, rig, monkeypatch):
        self.pages = []
        inner = rig.storage.inner

        def instrument(name):
            original = getattr(type(inner), name)

            def observed(actual, query, limit, cursor=None):
                page = original(actual, query, limit, cursor)
                if actual is inner:
                    self.pages.append(
                        (name, cursor, page.next, tuple(row.id for row in page.items))
                    )
                return page

            monkeypatch.setattr(type(inner), name, observed)

        instrument("scan_tasks")
        instrument("scan_conversations")

    def assert_complete_multiple_pages(self):
        for name in ("scan_tasks", "scan_conversations"):
            pages = [page for page in self.pages if page[0] == name]
            assert len(pages) >= 2
            assert pages[0][1] is None
            assert pages[-1][2] is None
            assert any(page[1] is not None for page in pages)
            assert sum(len(page[3]) for page in pages) > 128

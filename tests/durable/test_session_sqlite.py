"""DE03d1: real SQLite durability through sync and async Session ownership."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlite_child_support import Child
from task_support import task_definition
from test_session_commit import ASYNC, SYNC

from pipy_harness.native.durable import sqlite, sqlite_connection
from pipy_harness.native.durable.documents import Doc, define_doc
from pipy_harness.native.durable.session import SessionFailed
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import (
    DocumentAddress,
    EntryQuery,
    StorageWrite,
)
from pipy_harness.native.durable.tx import RawDocumentChange

WAIT = 10
MODES = pytest.mark.parametrize("mode", [SYNC, ASYNC], ids=["sync", "async"])


class Recording:
    def __init__(self, inner):
        self.inner = inner
        self.calls = 0
        self.returned = []

    def commit(self, writes):
        self.calls += 1
        seq = self.inner.commit(writes)
        self.returned.append(seq)
        return seq

    def __getattr__(self, name):
        return getattr(self.inner, name)


@contextlib.contextmanager
def rig(tmp_path):
    path = str(tmp_path / "session.sqlite")
    storage = open_sqlite_storage(path, mode="create")
    allowed_close = []

    def close_storage():
        try:
            storage.close()
        except RuntimeError as error:
            assert error in allowed_close

    with contextlib.ExitStack() as stack:
        stack.callback(close_storage)
        storage.commit((StorageWrite({"type": "conversation", "value": {"id": 1}}),))
        recording = Recording(storage)
        reports = []
        session = Session(
            recording,
            reporter=lambda error, info: reports.append((error, info)),
            now=lambda: 1000.0,
        )

        def finish():
            try:
                session.close(timeout=WAIT)
            except RuntimeError as error:
                assert error in allowed_close
                assert session.wait_closed(timeout=WAIT).error is error

        stack.callback(finish)
        yield SimpleNamespace(
            session=session,
            storage=storage,
            recording=recording,
            path=path,
            reports=reports,
            allowed_close=allowed_close,
        )


def raw():
    return Doc(
        "sqlite.session",
        1,
        "session",
        lambda: {"items": []},
        checkpoint_when=lambda _value, _ops, _info: False,
    )


async def mixed(tx, spec, value):
    (await tx.doc(spec))["items"].append(value)
    conversation = (await tx.create_conversation({"kind": "ownerless"})).id
    entry = await tx.append_entry(conversation, {"kind": "sqlite.entry", "data": value})
    task = await tx.create_task(
        task_definition("sqlite.task", 1, {"phase": "new"}),
        value,
        ownership={"kind": "conversation"},
        conversation_id=conversation,
        background=True,
    )
    submission = await tx.create_submission(
        {
            "conversationId": conversation,
            "type": "input",
            "status": "queued",
            "requestId": "sqlite.request",
        }
    )
    return {
        "conversation": conversation,
        "entry": entry.id,
        "task": task,
        "submission": submission.id,
    }


def assert_persisted(storage, ids, value):
    assert storage.conversation(ids["conversation"]).id == ids["conversation"]
    lookup = storage.entry(ids["entry"])
    assert lookup.commit_seq == 2 and lookup.entry.to_tree()["data"] == value
    assert storage.task(ids["task"]).to_tree()["input"] == value
    assert storage.submission(ids["submission"]).to_tree() == {
        "id": ids["submission"],
        "conversationId": ids["conversation"],
        "type": "input",
        "status": "queued",
        "requestId": "sqlite.request",
    }
    assert (
        storage.submission_by_request(ids["conversation"], "sqlite.request").id
        == ids["submission"]
    )
    found = storage.find_document(
        DocumentAddress({"scope": {"kind": "session"}, "kind": "sqlite.session"})
    )
    assert storage.document(found.id).to_tree()["value"] == {"items": [value]}


@MODES
def test_mixed_commit_publication_identity_and_fresh_session_reopen(tmp_path, mode):
    with rig(tmp_path) as r:
        spec = raw()
        publications = []

        def publish(item):
            assert r.recording.returned[-1] == item.seq
            publications.append(item)

        r.session.subscribe_commits(publish)
        value = {"nested": [None, -0.0], "text": "detached"}

        async def body():
            ids = await mode.commit(r, lambda tx: mixed(tx, spec, value))
            snap = await mode.snapshot(r, spec)
            changes = [
                change
                for change in publications[0].changes
                if type(change) is RawDocumentChange
            ]
            assert len(changes) == 1 and changes[0].value is snap.value
            assert (await mode.snapshot(r, spec)) is snap
            assert snap.value.thaw() == {"items": [value]}
            return ids

        ids = mode.run(body)
        expected = json.loads(json.dumps(value))
        value["nested"].clear()
        assert_persisted(r.storage, ids, expected)

        async def close():
            await mode.close(r)

        mode.run(close)
        reopened = open_sqlite_storage(r.path, mode="open")
        with contextlib.ExitStack() as cleanup:
            cleanup.callback(reopened.close)
            fresh = SimpleNamespace(session=Session(reopened))
            cleanup.callback(lambda: fresh.session.close(timeout=WAIT))
            assert_persisted(reopened, ids, expected)

            async def read_and_continue():
                assert (await mode.snapshot(fresh, raw())).value.thaw() == {
                    "items": [expected]
                }

                async def append(tx):
                    return (
                        await tx.append_entry(
                            ids["conversation"], {"kind": "continued"}
                        )
                    ).id

                return await mode.commit(fresh, append)

            new_id = mode.run(read_and_continue)
            assert new_id == max(ids.values()) + 1
            assert reopened.entry(new_id).commit_seq == 3


@MODES
def test_real_commit_ack_loss_fails_session_without_adoption_publication_or_retry(
    tmp_path, mode, monkeypatch
):
    with rig(tmp_path) as r:
        spec = raw()
        publications = []
        r.session.subscribe_commits(publications.append)
        error = RuntimeError("after durable COMMIT")

        def lose_ack(connection):
            assert not connection.in_transaction
            raise error

        monkeypatch.setattr(sqlite, "_after_commit", lose_ack)

        async def body():
            with pytest.raises(RuntimeError) as caught:
                await mode.commit(r, lambda tx: mixed(tx, spec, {"ack": "lost"}))
            assert caught.value is error and r.session.failure is error
            assert (
                publications == []
                and r.recording.calls == 1
                and r.recording.returned == []
            )
            with pytest.raises(SessionFailed):
                await mode.snapshot(r, spec)
            with pytest.raises(SessionFailed):
                await mode.commit(r, lambda tx: mixed(tx, spec, {}))
            assert (await mode.closed(r)).error is error

        mode.run(body)
        monkeypatch.undo()
        reopened = open_sqlite_storage(r.path, mode="open")
        try:
            entries = reopened.scan_entries(EntryQuery({"conversationId": 3}), 10).items
            assert len(entries) == 1
            assert_persisted(
                reopened,
                {"conversation": 3, "entry": 4, "task": 5, "submission": 6},
                {"ack": "lost"},
            )
            assert reopened.mint_id() == 7 and reopened.commit(()) == 3
        finally:
            reopened.close()


@MODES
@pytest.mark.parametrize("failure", ["precommit", "ack", "adoption"])
def test_checkpoint_base_count_reset_failure_and_fresh_session_reopen(
    tmp_path, mode, monkeypatch, failure
):
    from pipy_harness.native.durable.tx import _DocumentCache

    with rig(tmp_path) as r:
        chosen, policy_counts, publications = [False], [], []

        def policy(_value, _ops, info):
            policy_counts.append(info.deltas_since_base)
            return chosen[0]

        spec = define_doc(
            "sqlite.checkpoint", 1, "session", lambda: {"n": 0}, checkpoint_when=policy
        )
        r.session.observe_commits(publications.append)
        error = ValueError("checkpoint " + failure)

        async def edit(tx):
            (await tx.doc(spec))["n"] += 1

        async def body():
            await mode.commit(r, edit)
            await mode.commit(r, edit)
            await mode.commit(r, edit)
            old = await mode.snapshot(r, spec)
            assert old.value.thaw() == {"n": 3} and old.deltas_since_base == 2
            chosen[0] = True

            def fail(*_args):
                raise error

            monkeypatch.setattr(
                _DocumentCache if failure == "adoption" else sqlite,
                "swap"
                if failure == "adoption"
                else "_after_commit"
                if failure == "ack"
                else "_before_commit",
                fail,
            )
            with pytest.raises(ValueError) as caught:
                await mode.commit(r, edit)
            assert caught.value is error and r.session.failure is error
            assert policy_counts == [0, 1, 2]
            assert len(publications) == 3
            assert old.value.thaw() == {"n": 3} and old.deltas_since_base == 2
            assert (await mode.closed(r)).error is error
            return old

        old = mode.run(body)
        monkeypatch.undo()
        reopened = open_sqlite_storage(r.path, mode="open")
        fresh = SimpleNamespace(session=Session(reopened))
        try:

            async def read():
                return await mode.snapshot(fresh, spec)

            loaded = mode.run(read)
            assert loaded.record.id == old.record.id and loaded.version == 1
            assert loaded.value.thaw() == {"n": 3 if failure == "precommit" else 4}
            assert loaded.deltas_since_base == (2 if failure == "precommit" else 0)
        finally:
            fresh.session.close(timeout=WAIT)


@MODES
@pytest.mark.parametrize("refusal", ["readonly", "full", "busy"])
def test_real_sqlite_error_poisoning_and_no_partial_state(tmp_path, mode, refusal):
    with rig(tmp_path) as r, contextlib.ExitStack() as cleanup:
        connection = r.storage._database.connection()
        if refusal == "readonly":
            connection.execute("PRAGMA query_only = ON")
        elif refusal == "full":
            pages = connection.execute("PRAGMA page_count").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count = {pages}")
        else:
            # Deliberate fault injector bypasses pipy's normal single-owner API.
            writer = cleanup.enter_context(
                Child(
                    """
import sqlite3
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("BEGIN IMMEDIATE")
say("locked")
assert command() == "release"
connection.execute("ROLLBACK")
connection.close()
say("released")
""",
                    r.path,
                    home=tmp_path,
                )
            )
            writer.expect("locked")
            connection.execute("PRAGMA busy_timeout = 20")

        async def body():
            with pytest.raises(sqlite3.OperationalError) as failure:
                await mode.commit(
                    r, lambda tx: mixed(tx, raw(), {"text": "x" * 300_000})
                )
            assert (
                failure.value.sqlite_errorcode
                == {
                    "readonly": sqlite3.SQLITE_READONLY,
                    "full": sqlite3.SQLITE_FULL,
                    "busy": sqlite3.SQLITE_BUSY,
                }[refusal]
            )
            assert r.session.failure is failure.value
            with pytest.raises(SessionFailed):
                await mode.commit(r, lambda tx: mixed(tx, raw(), {}))
            assert (await mode.closed(r)).error is failure.value

        started = time.monotonic()
        mode.run(body)
        assert time.monotonic() - started < WAIT
        if refusal == "busy":
            writer.send("release")
            writer.expect("released")
            assert writer.wait() == 0
        # Auto-close has completed; never touch its closed connection again.
        reopened = open_sqlite_storage(r.path, mode="open")
        try:
            assert (
                reopened.scan_entries(EntryQuery({"conversationId": 1}), 10).items == ()
            )
            assert reopened.conversation(3) is None and reopened.document(2) is None
            assert reopened.mint_id() == 2 and reopened.commit(()) == 2
            assert reopened._database.connection().execute(
                "PRAGMA integrity_check"
            ).fetchall() == [("ok",)]
        finally:
            reopened.close()


@MODES
@pytest.mark.parametrize("failing", [False, True])
def test_close_and_checkpoint_failure_release_owner_to_fresh_child(
    tmp_path, mode, failing, monkeypatch
):
    with rig(tmp_path) as r:
        failure = RuntimeError("checkpoint refusal")
        if failing:
            r.allowed_close.append(failure)

            def refuse(connection):
                raise failure

            monkeypatch.setattr(sqlite_connection, "_truncate_checkpoint", refuse)

        async def body():
            if failing:
                with pytest.raises(RuntimeError) as caught:
                    await mode.close(r)
                assert caught.value is failure
            else:
                await mode.close(r)
            end = await mode.closed(r)
            assert end.reason == ("failed" if failing else "closed")

        mode.run(body)
        monkeypatch.undo()
        with Child(
            """
from pipy_harness.native.durable.sqlite import open_sqlite_storage
store = open_sqlite_storage(sys.argv[1], mode="open")
say("opened", seq=store.commit(()))
store.close()
say("closed")
""",
            r.path,
            home=tmp_path,
        ) as child:
            assert child.expect("opened")["seq"] == 2
            child.expect("closed")
            assert child.wait() == 0


def _held_close(tmp_path, mode, monkeypatch):
    with rig(tmp_path) as r:
        entered, release = threading.Event(), threading.Event()

        def barrier(connection):
            entered.set()
            assert release.wait(WAIT)

        monkeypatch.setattr(sqlite, "_before_commit", barrier)
        if mode.sync:
            with ThreadPoolExecutor(2, thread_name_prefix="de03d1-held") as pool:
                future = pool.submit(
                    r.session.commit, lambda tx: tx.append_entry(1, {"kind": "held"})
                )
                try:
                    assert entered.wait(WAIT)
                    r.session.request_close()
                    with r.session._owner._cond:
                        assert r.session._owner._close_state == "idle"
                    assert not future.done() and r.session._owner.end is None
                finally:
                    release.set()
                assert future.result(WAIT).id == 2
                r.session.close(timeout=WAIT)
        else:

            async def body():
                async def change(tx):
                    return await tx.append_entry(1, {"kind": "held"})

                future = asyncio.create_task(r.session.acommit(change))
                try:
                    assert await asyncio.to_thread(entered.wait, WAIT)
                    r.session.request_close()
                    with r.session._owner._cond:
                        assert r.session._owner._close_state == "idle"
                    assert not future.done() and r.session._owner.end is None
                finally:
                    release.set()
                assert (await asyncio.wait_for(future, WAIT)).id == 2
                await asyncio.wait_for(r.session.aclose(), WAIT)

            mode.run(body)
        reopened = open_sqlite_storage(r.path, mode="open")
        try:
            assert reopened.entry(2).entry.to_tree()["kind"] == "held"
        finally:
            reopened.close()


@MODES
def test_close_drains_held_real_commit_without_inline_deadlock(tmp_path, mode):
    from pathlib import Path

    with Child(
        r"""
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import pytest
import test_session_sqlite as cases
with pytest.MonkeyPatch.context() as patch:
    cases._held_close(Path(sys.argv[1]), cases.SYNC if sys.argv[3] == "sync" else cases.ASYNC, patch)
say("drained")
""",
        str(tmp_path),
        str(Path(__file__).resolve().parent),
        mode.name,
        home=tmp_path,
    ) as child:
        child.expect("drained", timeout=3 * WAIT)
        assert child.wait(timeout=WAIT) == 0
        assert child.process.stderr.read() == b""


@MODES
def test_loaded_delta_counts_survive_fresh_sessions_and_independent_documents(
    tmp_path, mode
):
    specs = (
        raw(),
        Doc(
            "other",
            1,
            "session",
            lambda: {"items": []},
            checkpoint_when=lambda _value, _ops, _info: False,
        ),
    )
    old_values = []
    with rig(tmp_path) as r:

        async def run():
            async def initial(tx):
                await tx.doc(specs[0])
                await tx.doc(specs[1])

            await mode.commit(r, initial)
            for n in range(3):

                async def edit(tx):
                    (await tx.doc(specs[0]))["items"].append(n)
                    if n == 0:
                        (await tx.doc(specs[1]))["items"].append(n)

                await mode.commit(r, edit)
            for spec, count in zip(specs, (3, 1), strict=True):
                current = await mode.snapshot(r, spec)
                assert current.deltas_since_base == count
                assert (
                    r.storage.document(current.record.id).to_tree()["deltasSinceBase"]
                    == count
                )
                old_values.append(current.value)

        mode.run(run)
    storage = open_sqlite_storage(r.path, mode="open")
    fresh = SimpleNamespace(session=Session(storage), storage=storage)
    try:

        async def run():
            for spec, count, old in zip(specs, (3, 1), old_values, strict=True):
                current = await mode.snapshot(fresh, spec)
                assert current.value == old and current.deltas_since_base == count

            async def edit(tx):
                (await tx.doc(specs[0]))["items"].append(3)

            await mode.commit(fresh, edit)
            assert (await mode.snapshot(fresh, specs[0])).deltas_since_base == 4
            assert (await mode.snapshot(fresh, specs[1])).deltas_since_base == 1

        mode.run(run)
    finally:
        fresh.session.close(timeout=WAIT)


@MODES
@pytest.mark.parametrize("failure", ["ack-loss", "readonly"])
def test_loaded_delta_real_failure_keeps_old_cache_and_publication_until_reopen(
    tmp_path, mode, monkeypatch, failure
):
    with rig(tmp_path) as r:
        spec = raw()
        publications = []
        r.session.subscribe_commits(publications.append)

        async def initial():
            async def edit(tx):
                (await tx.doc(spec))["items"].append("base")

            await mode.commit(r, edit)

        mode.run(initial)
        previous = r.session.snapshot(spec)
        error = RuntimeError("delta after durable COMMIT")
        if failure == "ack-loss":

            def lose_ack(connection):
                assert not connection.in_transaction
                raise error

            monkeypatch.setattr(sqlite, "_after_commit", lose_ack)
        else:
            r.storage._database.connection().execute("PRAGMA query_only = ON")
        escaped = []
        adopted = []
        from pipy_harness.native.durable.tx import _DocumentCache

        original_swap, original_install = _DocumentCache.swap, _DocumentCache.install

        def swap(cache, *args):
            adopted.append("swap")
            original_swap(cache, *args)

        def install(cache, *args):
            adopted.append("install")
            original_install(cache, *args)

        monkeypatch.setattr(_DocumentCache, "swap", swap)
        monkeypatch.setattr(_DocumentCache, "install", install)

        async def run():
            async def edit(tx):
                d = await tx.doc(spec)
                escaped.append(d["items"])
                d["items"].append("delta")
                await tx.append_entry(1, {"kind": "delta-coupled"})

            with pytest.raises(
                RuntimeError if failure == "ack-loss" else sqlite3.OperationalError
            ) as caught:
                await mode.commit(r, edit)
            assert failure != "ack-loss" or caught.value is error
            assert r.session.failure is caught.value
            assert adopted == []
            assert r.session._cache.peek(spec) in (previous, None)
            assert previous.deltas_since_base == 0 and previous.value.thaw() == {
                "items": ["base"]
            }
            assert (
                len(publications) == 1
                and r.recording.calls == 2
                and len(r.recording.returned) == 1
            )
            from pipy_harness.native.durable.tx import DraftRevoked

            with pytest.raises(DraftRevoked):
                len(escaped[0])
            assert (await mode.closed(r)).error is caught.value

        mode.run(run)
        monkeypatch.undo()
        storage = open_sqlite_storage(r.path, mode="open")
        fresh = SimpleNamespace(session=Session(storage))
        try:

            async def verify():
                current = await mode.snapshot(fresh, spec)
                assert current.deltas_since_base == (1 if failure == "ack-loss" else 0)
                assert current.value.thaw() == {
                    "items": ["base", "delta"] if failure == "ack-loss" else ["base"]
                }
                entries = storage.scan_entries(
                    EntryQuery({"conversationId": 1}), 10
                ).items
                assert len(entries) == (1 if failure == "ack-loss" else 0)

            mode.run(verify)
        finally:
            fresh.session.close(timeout=WAIT)

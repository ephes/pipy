"""Actual read tickets and independent-process SQLite lock ownership."""

import asyncio
import threading

import pytest
from sqlite_child_support import Child
from test_tool_lifecycle import OPENER

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentUserMessage
from pipy_harness.native.durable.completed_projection import (
    CompletedWatch,
    ProjectionClosed,
    observe_completed,
)
from pipy_harness.native.durable.generation import message_entry
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage


def opener(path, root, expected):
    with Child(OPENER, str(path), home=root) as child:
        child.expect(expected)
        assert child.wait() == 0
        child.wait_stdout_eof()


@pytest.mark.parametrize("cut", ["acquire", "drain"])
def test_real_read_worker_retains_ticket_path_through_cancel_close(
    tmp_path, monkeypatch, cut
):
    path = tmp_path / "session.sqlite"
    backend = open_sqlite_storage(str(path), mode="create")
    session = Session(backend)
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    original = backend.scan_entries

    def held(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(10)
            return original(*args, **kwargs)
        finally:
            exited.set()

    async def run():
        watch = None
        if cut == "drain":
            watch = await observe_completed(session, cid)

            async def consume(snapshot):
                pass

            watch.start(consume)
        monkeypatch.setattr(backend, "scan_entries", held)
        work = asyncio.create_task(
            observe_completed(session, cid) if watch is None else watch.drain()
        )
        assert await asyncio.to_thread(entered.wait, 5)
        work.cancel()
        if cut == "acquire":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(work, 1)
        # Cancellation of the capture wrapper is not actual read exit.
        assert not exited.is_set()
        session.request_close()
        close_wait = asyncio.create_task(session.aclose())
        await asyncio.sleep(0)
        close_wait.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close_wait
        await asyncio.to_thread(opener, path, tmp_path, "refused")
        assert not exited.is_set()
        if watch is not None:
            assert await watch.wait_closed() == "session_closed"
        release.set()
        with pytest.raises((asyncio.CancelledError, ProjectionClosed)):
            await work
        await asyncio.wait_for(session.aclose(), 5)
        assert exited.is_set()
        await asyncio.to_thread(opener, path, tmp_path, "opened")

    try:
        asyncio.run(run())
    finally:
        release.set()
        session.close(timeout=5)


@pytest.mark.parametrize("half", ["before_registration", "after_registration"])
def test_attach_baseline_registration_has_no_commit_gap(monkeypatch, half):
    session = Session(MemoryStorage())
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    entered, release = threading.Event(), threading.Event()
    original = CompletedWatch._register

    def held(self):
        if half == "after_registration":
            snapshot = original(self)
        entered.set()
        assert release.wait(5)
        return original(self) if half == "before_registration" else snapshot

    monkeypatch.setattr(CompletedWatch, "_register", held)

    async def run():
        acquire = asyncio.create_task(observe_completed(session, cid))
        assert await asyncio.to_thread(entered.wait, 5)
        mutation = asyncio.create_task(
            session.acommit(
                lambda tx: tx.append_entry(
                    cid, message_entry(AgentUserMessage(ProductContent("gapless")))
                )
            )
        )
        # The queued mutation cannot pass either half of the held line job.
        await asyncio.sleep(0)
        assert not mutation.done()
        release.set()
        watch = await acquire
        seen = []

        async def consume(snapshot):
            seen.append(snapshot)

        watch.start(consume)
        entry = await mutation
        snapshot = await asyncio.wait_for(watch.drain(), 5)
        assert [m.entry_id for m in snapshot.messages] == [entry.id]
        assert seen[-1] is snapshot
        assert snapshot.publication_seq == snapshot.messages[0].commit_seq
        watch.stop()
        await watch.wait_closed()

    try:
        asyncio.run(run())
    finally:
        release.set()
        session.close(timeout=5)


def test_internal_listener_failure_preserves_current_publication_and_reopen(tmp_path):
    path = tmp_path / "session.sqlite"
    session = Session(open_sqlite_storage(str(path), mode="create"))
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    published = []

    def fail(publication):
        raise RuntimeError("synthetic internal observer failure")

    async def run():
        watch = await observe_completed(session, cid)

        async def consume(snapshot):
            raise AssertionError("Session failure must end async delivery")

        watch.start(consume)
        session.observe_commits(fail)
        session.subscribe_commits(lambda publication: published.append(publication))
        entry = await session.acommit(
            lambda tx: tx.append_entry(
                cid, message_entry(AgentUserMessage(ProductContent("committed")))
            )
        )
        assert published and published[-1].seq > 0
        assert session.failure is not None
        assert await watch.wait_closed() == "session_closed"
        return entry.id

    try:
        id = asyncio.run(run())
    finally:
        session.close(timeout=5)
    reopened = Session(open_sqlite_storage(str(path), mode="open"))

    async def inspect():
        watch = await observe_completed(reopened, cid)
        assert [m.entry_id for m in watch.snapshot.messages] == [id]
        watch.stop()
        await watch.wait_closed()

    try:
        asyncio.run(inspect())
    finally:
        reopened.close(timeout=5)


@pytest.mark.parametrize("fault", ["before", "ack", "adoption"])
def test_actual_last_child_fault_never_delivers_unacknowledged_round(
    tmp_path, monkeypatch, fault
):
    from generation_support import admit
    from test_tool_faults import install_fault
    from test_tool_sqlite import resume
    from tool_support import counts, definitions

    from pipy_harness.native.durable.scheduler import TaskScheduler

    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    generation, tool, snapshot = definitions(tmp_path, replay="safe")
    path = tmp_path / "session.sqlite"
    session = Session(open_sqlite_storage(str(path), mode="create"))
    cid, _, parent = admit(session, generation, snapshot)
    scheduler = TaskScheduler(session, [generation, tool])
    scheduler.open()
    reached = threading.Event()

    async def run():
        watch = await observe_completed(session, cid)
        seen = []

        async def consume(view):
            seen.append(view)

        watch.start(consume)
        with monkeypatch.context() as patch:
            install_fault(patch, parent, fault, reached)
            scheduler.resume()
            assert await asyncio.wait_for(watch.wait_closed(), 10) == "session_closed"
            end = await asyncio.to_thread(session.wait_closed, timeout=10)
            assert end.reason == "failed" and reached.is_set()
        assert all(
            not view.rounds and all(o.task_id != parent for o in view.outcomes)
            for view in seen
        )
        assert counts(tmp_path) == {
            "calls": 2,
            "applications": 2,
            "prepares": 2,
            "providers": 1,
        }

    try:
        asyncio.run(run())
    finally:
        session.close(timeout=10)
    _, rows, completed = resume(tmp_path, parent, replay="safe", missing=True)
    assert completed and all(r["state"]["status"] == "terminal" for r in rows)
    assert counts(tmp_path) == {
        "calls": 2,
        "applications": 2,
        "prepares": 2,
        "providers": 2,
    }
    # The second provider is the legitimate following answer. Old tool effects
    # and the first generation receipt were reused, not reissued.
    reopened = Session(open_sqlite_storage(str(path), mode="open"))

    async def inspect():
        watch = await observe_completed(reopened, cid)
        assert len(watch.snapshot.rounds) == 1 and len(watch.snapshot.outcomes) == 3
        assert len(watch.snapshot.usage.thaw()["settlements"]) == 2
        watch.stop()
        await watch.wait_closed()

    try:
        asyncio.run(inspect())
    finally:
        reopened.close(timeout=5)


@pytest.mark.parametrize("line", ["read", "commit"])
def test_observe_and_drain_refuse_actual_same_session_async_line_reentry(line):
    from pipy_harness.native.durable.session_core import ReentrantSessionCall

    session = Session(MemoryStorage())
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id

    async def run():
        watch = await observe_completed(session, cid)
        seen = []

        async def consume(snapshot):
            seen.append(snapshot)

        watch.start(consume)

        async def held(*args):
            with pytest.raises(ReentrantSessionCall):
                await observe_completed(session, cid)
            with pytest.raises(ReentrantSessionCall):
                await watch.drain()

        operation = (
            session.aread_on_line(held) if line == "read" else session.acommit(held)
        )
        await asyncio.wait_for(operation, 2)
        assert watch._capture_task is None and not seen
        assert await asyncio.wait_for(watch.drain(), 2) is seen[-1]
        watch.stop()
        await watch.wait_closed()

    try:
        asyncio.run(run())
    finally:
        session.close(timeout=5)


def test_cancelled_drain_cannot_let_older_inflight_capture_ack_new_barrier(monkeypatch):
    backend = MemoryStorage()
    session = Session(backend)
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    enters = [threading.Event(), threading.Event()]
    releases = [threading.Event(), threading.Event()]
    original = backend.scan_entries
    calls = []

    def held(*args):
        index = len(calls)
        calls.append(threading.get_ident())
        enters[index].set()
        assert releases[index].wait(5)
        return original(*args)

    async def run():
        watch = await observe_completed(session, cid)
        monkeypatch.setattr(backend, "scan_entries", held)
        first_delivered = asyncio.Event()
        seen = []

        async def consume(snapshot):
            seen.append(snapshot.revision)
            first_delivered.set()

        watch.start(consume)
        old = asyncio.create_task(watch.drain())
        assert await asyncio.to_thread(enters[0].wait, 5)
        old.cancel()
        with pytest.raises(asyncio.CancelledError):
            await old
        new = asyncio.create_task(watch.drain())
        await asyncio.sleep(0)
        releases[0].set()
        await first_delivered.wait()
        assert await asyncio.to_thread(enters[1].wait, 5)
        assert not new.done() and seen == [1]
        releases[1].set()
        final = await asyncio.wait_for(new, 5)
        assert final.revision == 2 and seen == [1, 2] and len(calls) == 2
        watch.stop()
        await watch.wait_closed()

    try:
        asyncio.run(run())
    finally:
        for release in releases:
            release.set()
        session.close(timeout=5)


def test_partial_registration_and_failed_task_handoff_cleanup(monkeypatch):
    session = Session(MemoryStorage())
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id

    async def run():
        with monkeypatch.context() as patch:

            def refuse(*args):
                raise RuntimeError("synthetic private handoff")

            patch.setattr(Session, "subscribe_close", refuse)
            with pytest.raises(ProjectionClosed):
                await observe_completed(session, cid)
            assert not session._commit_listeners.copy()
        watch = await observe_completed(session, cid)

        async def consume(value):
            pytest.fail("failed handoff must not invoke consumer")

        watch.start(consume)
        loop = asyncio.get_running_loop()
        real = loop.create_task
        refusals = []

        def refused(body, *args, **kwargs):
            if body.cr_code.co_qualname == "CompletedWatch._spawn.<locals>.registered":
                refusals.append(body.cr_code.co_qualname)
                raise RuntimeError("synthetic private task factory")
            return real(body, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(loop, "create_task", refused)
            with pytest.raises(ProjectionClosed) as caught:
                await asyncio.wait_for(watch.drain(), 5)
            assert caught.value.reason == "loop_closed"
            assert refusals == ["CompletedWatch._spawn.<locals>.registered"]
        assert (
            not session._commit_listeners.copy() and not session._close_listeners.copy()
        )

    try:
        asyncio.run(run())
    finally:
        session.close(timeout=5)


@pytest.mark.parametrize("conversation", ["chosen", "other"])
def test_closed_consumer_loop_detaches_on_late_publication_without_failing_session(
    conversation,
):
    session = Session(MemoryStorage())
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    other = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    loop = asyncio.new_event_loop()
    watch = loop.run_until_complete(observe_completed(session, cid))
    loop.close()
    try:
        session.commit(
            lambda tx: tx.append_entry(
                cid if conversation == "chosen" else other,
                message_entry(AgentUserMessage(ProductContent("late"))),
            )
        )
        with watch._lock:
            assert watch._reason == "loop_closed"
        assert (
            not session._commit_listeners.copy() and not session._close_listeners.copy()
        )
        assert session.failure is None
    finally:
        session.close(timeout=5)


@pytest.mark.parametrize("end", ["cancel", "loop_teardown"])
def test_actual_registered_read_ticket_outlives_done_wrapper_and_host_loop(
    tmp_path, monkeypatch, end
):
    path = tmp_path / "session.sqlite"
    session = Session(open_sqlite_storage(str(path), mode="create"))
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    original = CompletedWatch._register

    def held(watch):
        snapshot = original(watch)
        entered.set()
        try:
            assert release.wait(10)
            return snapshot
        finally:
            exited.set()

    monkeypatch.setattr(CompletedWatch, "_register", held)

    async def cancel():
        acquire = asyncio.create_task(observe_completed(session, cid))
        assert await asyncio.to_thread(entered.wait, 5)
        acquire.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(acquire, 1)
        assert not exited.is_set()
        assert (
            not session._commit_listeners.copy() and not session._close_listeners.copy()
        )
        session.request_close()
        await asyncio.to_thread(opener, path, tmp_path, "refused")
        release.set()
        await asyncio.wait_for(session.aclose(), 5)
        await asyncio.to_thread(opener, path, tmp_path, "opened")

    def teardown():
        ready, ended = threading.Event(), threading.Event()
        state = {}
        failures = []

        def host():
            loop = asyncio.new_event_loop()
            state["loop"] = loop
            loop.create_task(observe_completed(session, cid))
            ready.set()
            try:
                loop.run_forever()
            except BaseException as error:  # noqa: BLE001 - transfer actual host-thread failure to the test
                failures.append(error)
            finally:
                loop.close()
                ended.set()

        thread = threading.Thread(target=host)
        thread.start()
        assert ready.wait(5) and entered.wait(5)
        loop = state["loop"]

        def cancel_all():
            # Only the owning host loop inspects its own Tasks.
            tasks = asyncio.all_tasks(loop)
            for task in tasks:
                task.cancel()

            async def reap_wrappers():
                await asyncio.gather(*tasks, return_exceptions=True)
                loop.stop()

            loop.create_task(reap_wrappers())

        loop.call_soon_threadsafe(cancel_all)
        assert ended.wait(5)
        thread.join(5)
        assert not thread.is_alive() and not failures and not exited.is_set()
        assert (
            not session._commit_listeners.copy() and not session._close_listeners.copy()
        )
        session.request_close()
        opener(path, tmp_path, "refused")
        assert not exited.is_set()
        release.set()
        session.close(timeout=5)
        opener(path, tmp_path, "opened")

    try:
        asyncio.run(cancel()) if end == "cancel" else teardown()
        assert exited.is_set()
    finally:
        release.set()
        session.close(timeout=5)

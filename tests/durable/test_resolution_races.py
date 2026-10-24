"""Real phase retirement, late writes, abort and close share the Session line."""

import asyncio
import threading

import pytest
from generation_support import Wire, admit, definition, seed
from test_resolution import setup
from tool_support import counts

from pipy_harness.native.durable import resolution, tool_execution
from pipy_harness.native.durable.generation_transport import owned_call
from pipy_harness.native.durable.inspection import inspect_task
from pipy_harness.native.durable.resolution_values import ResolutionError
from pipy_harness.native.durable.scheduler import InvocationEnded


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))


def test_pending_blocked_wrapper_still_physically_owned_and_late_runtime_cannot_write(
    tmp_path, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    runtimes = []
    actual = tool_execution._Tool.block

    async def held(self, runtime, previous, final, reason, call_input):
        await actual(self, runtime, previous, final, reason, call_input)
        runtimes.append(runtime)
        entered.set()
        await owned_call(lambda: release.wait(20))

    monkeypatch.setattr(tool_execution._Tool, "block", held)
    # setup's idle wait would correctly wait for actual retirement; start its
    # real loop on a worker so this test observes the intermediate committed row.
    box, errors = [], []

    def run():
        try:
            box.append(setup(tmp_path, "sqlite"))
        except BaseException as error:  # noqa: BLE001 - retain worker failure for the joining test
            errors.append(error)

    creator = threading.Thread(target=run)
    creator.start()
    try:
        assert entered.wait(10)
        runtime = runtimes[0]
        scheduler = runtime._scheduler
        session = scheduler._session
        child = runtime.task_id
        assert inspect_task(session, child).status == "pending"
        assert inspect_task(session, child).phase == "blocked"
        target = scheduler.resolution_target(child)
        with pytest.raises(ResolutionError) as error:
            scheduler.resolve(target, nonce="4" * 32)
        assert error.value.code == "busy" and counts(tmp_path)["calls"] == 1
        release.set()
        creator.join(10)
        assert not creator.is_alive() and not errors and len(box) == 1
        before = session.commit(lambda tx: tx.task(child)).to_tree()

        async def late():
            with pytest.raises(InvocationEnded):
                await runtime.commit(lambda *a: pytest.fail("late phase wrote"))

        asyncio.run(late())
        assert session.commit(lambda tx: tx.task(child)).to_tree() == before
        result = scheduler.resolve(target, nonce="4" * 32)
        assert result.new_task_id != child
    finally:
        release.set()
        creator.join(10)
        if box:
            box[0][0].close(timeout=10)
        elif runtimes:
            runtimes[0]._scheduler._session.close(timeout=10)


def test_abort_before_resolution_stales_then_current_abort_refuses(tmp_path):
    session, scheduler, cid, parent, old, _ = setup(tmp_path, "memory")
    try:
        target = scheduler.resolution_target(old["id"])
        session.commit(lambda tx: tx.set_task({**old, "abortRequested": True}))
        assert inspect_task(session, old["id"]).reason == "abort_requested"
        before = counts(tmp_path)
        with pytest.raises(ResolutionError) as stale:
            scheduler.resolve(target, nonce="5" * 32)
        assert stale.value.code == "stale"
        with pytest.raises(ResolutionError) as aborted:
            scheduler.resolve(scheduler.resolution_target(old["id"]), nonce="5" * 32)
        assert aborted.value.code == "incompatible" and counts(tmp_path) == before
    finally:
        session.close(timeout=10)


def test_close_while_resolution_read_holds_line_refuses_before_staging(
    tmp_path, monkeypatch
):
    session, scheduler, cid, parent, old, _ = setup(tmp_path, "sqlite")
    target = scheduler.resolution_target(old["id"])
    entered, release = threading.Event(), threading.Event()
    actual = resolution.capture_facts
    results = []

    def held(*args):
        value = actual(*args)
        entered.set()
        assert release.wait(20)
        return value

    monkeypatch.setattr(resolution, "capture_facts", held)

    def run():
        try:
            scheduler.resolve(target, nonce="6" * 32)
        except ResolutionError as error:
            results.append(error.code)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(10)
        before = counts(tmp_path)
        session.request_close()
        assert session.end is None
        release.set()
        worker.join(10)
        assert not worker.is_alive() and results == ["closed"]
        assert session.wait_closed(timeout=10).reason == "closed"
        assert counts(tmp_path) == before
        from pipy_harness.native.durable.sqlite import open_sqlite_storage

        store = open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="open")
        try:
            assert store.task(old["id"]).to_tree() == old
        finally:
            store.close()
    finally:
        release.set()
        worker.join(10)
        session.close(timeout=10)


def test_unrelated_conversation_commit_does_not_stale_resolution(tmp_path):
    session, scheduler, cid, parent, old, _ = setup(tmp_path, "memory")
    try:
        target = scheduler.resolution_target(old["id"])
        wire = Wire()
        generation = definition(tmp_path, wire)
        other_cid, _, _ = admit(session, generation, seed(tmp_path))
        assert other_cid != cid
        result = scheduler.resolve(target, nonce="7" * 32)
        assert result.new_task_id != old["id"]
    finally:
        session.close(timeout=10)


def test_cancelled_resolution_waiter_retains_accepted_atomic_commit(
    tmp_path, monkeypatch
):
    session, scheduler, cid, parent, old, _ = setup(tmp_path, "sqlite")
    target = scheduler.resolution_target(old["id"])
    entered, release, published = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    actual = resolution.capture_facts

    def held(*args):
        value = actual(*args)
        entered.set()
        assert release.wait(20)
        return value

    monkeypatch.setattr(resolution, "capture_facts", held)

    def observe(publication):
        if any(
            c.to_tree()["type"] == "task"
            and c.to_tree()["value"]["id"] == old["id"]
            and c.to_tree()["value"]["state"]["status"] == "terminal"
            for c in publication.changes
            if hasattr(c, "to_tree")
        ):
            published.set()

    session.subscribe_commits(observe)

    async def run():
        waiter = asyncio.create_task(scheduler.aresolve(target, nonce="8" * 32))
        assert await asyncio.to_thread(entered.wait, 10)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not published.is_set()
        release.set()
        assert await asyncio.to_thread(published.wait, 10)
        result = await scheduler.aresolve(target, nonce="8" * 32)
        assert result.source_task_id == old["id"] and result.new_task_id != old["id"]

    try:
        asyncio.run(run())
        assert (
            len([r for r in scheduler.inspect() if r.to_tree()["kind"] == "pi.tool"])
            == 3
        )
    finally:
        release.set()
        session.close(timeout=10)

"""Actual reads/invocations/finalizers retain their existing Session and OS lease."""

import asyncio
import json
import threading
from dataclasses import replace

import pytest
from generation_support import Wire, admit, definition, seed
from sqlite_child_support import Child

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.inspection import ainspect_task, inspect_task
from pipy_harness.native.durable.resolution_values import ResolutionError
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.native.tools.base import ToolDefinition, ToolExecutionResult

OPENER = r"""
import hashlib
from pathlib import Path
from pipy_harness.native.durable import inspection
from pipy_harness.native.durable.inspection import inspect_store
from pipy_harness.native.durable.inspection_values import InspectionError
source=Path(inspection.__file__)
try:
    result=inspect_store(sys.argv[1])
except InspectionError as error:
    say(error.code,source=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest())
else:
    say('opened',count=len(result.tasks),source=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest())
"""


def probe(path, root, stage, expected):
    with Child(OPENER, str(path), home=root) as child:
        event = child.expect(expected)
        assert child.wait() == 0
        child.wait_stdout_eof()
        (root / ("inspection-opener-" + stage + ".json")).write_text(
            json.dumps({"event": event, "process": child.evidence()}, indent=2)
        )


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))


def test_inspect_and_resolve_during_real_noncooperative_tool_and_finalizer(tmp_path):
    entered, release, finalizer, final_release, exited = (
        threading.Event() for _ in range(5)
    )
    path = tmp_path / "session.sqlite"
    contexts = []

    class Held:
        definition = ToolDefinition(
            "held", "held", {"type": "object", "properties": {"x": {"type": "integer"}}}
        )

        def invoke(self, request, context):
            contexts.append(context)
            entered.set()
            try:
                assert release.wait(20)
                return ToolExecutionResult(
                    request.tool_request_id,
                    "fixture result",
                    provider_correlation_id=request.provider_correlation_id,
                )
            finally:
                finalizer.set()
                assert final_release.wait(20)
                exited.set()

    session = Session(open_sqlite_storage(str(path), mode="create"))
    port = Held()
    tool = define_tool_task(
        {"held": ToolRegistration(port, "fixture.held", "1")}, workspace_root=tmp_path
    )
    generation = definition(tmp_path, Wire(("held",)))
    _, _, parent = admit(
        session,
        generation,
        snapshot_provider_request(
            replace(seed(tmp_path).request, available_tools=(port.definition,))
        ),
    )
    scheduler = TaskScheduler(session, [generation, tool])
    try:
        scheduler.open()
        scheduler.resume()
        assert entered.wait(10)
        child = next(
            r.id for r in scheduler.inspect() if r.to_tree()["kind"] == "pi.tool"
        )
        target = scheduler.resolution_target(child)
        assert inspect_task(session, child).invocation_ownership == "unknown"
        assert inspect_task(session, child).status == "running"
        with pytest.raises(ResolutionError) as busy:
            scheduler.resolve(target, nonce="a" * 32)
        assert busy.value.code == "busy"
        probe(path, tmp_path, "invoke", "busy")
        release.set()
        assert finalizer.wait(10) and not exited.is_set()
        with pytest.raises(ResolutionError) as held:
            scheduler.resolve(target, nonce="b" * 32)
        assert held.value.code == "busy"
        probe(path, tmp_path, "finalizer", "busy")
        session.request_close()
        assert contexts[0].cancel_event.wait(10)

        async def cancel_waiter():
            waiter = asyncio.create_task(session.aclose())
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter

        asyncio.run(cancel_waiter())
        with pytest.raises(ResolutionError) as closing:
            scheduler.resolve(target, nonce="c" * 32)
        assert closing.value.code == "closed" and not exited.is_set()
        probe(path, tmp_path, "cancelled-close", "busy")
        final_release.set()
        assert session.wait_closed(timeout=10).reason == "closed"
        assert exited.is_set() and not scheduler._thread.is_alive()
        probe(path, tmp_path, "closed", "opened")
    finally:
        release.set()
        final_release.set()
        session.close(timeout=10)


def test_cancelled_inspection_waiter_cannot_detach_actual_read_before_close(
    tmp_path, monkeypatch
):
    path = tmp_path / "session.sqlite"
    store = open_sqlite_storage(str(path), mode="create")
    session = Session(store)
    generation = definition(tmp_path, Wire())
    _, _, tid = admit(session, generation, seed(tmp_path))
    entered, release = threading.Event(), threading.Event()
    actual = store.task

    def held(task_id):
        entered.set()
        assert release.wait(20)
        return actual(task_id)

    monkeypatch.setattr(store, "task", held)

    async def run():
        waiter = asyncio.create_task(ainspect_task(session, tid))
        assert await asyncio.to_thread(entered.wait, 10)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        session.request_close()
        assert session.end is None
        await asyncio.to_thread(probe, path, tmp_path, "read-held", "busy")
        release.set()
        assert (await session.await_closed()).reason == "closed"

    try:
        asyncio.run(run())
        probe(path, tmp_path, "read-closed", "opened")
    finally:
        release.set()
        session.close(timeout=10)

"""Actual invoke/finalizer retention through close and cancelled API waiters."""

import asyncio
import json
import threading
from dataclasses import replace

import pytest
from generation_support import Wire, admit, definition, seed
from sqlite_child_support import Child

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.ownership import StorageOwnershipError
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
from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.ownership import StorageOwnershipError
source=Path(sqlite.__file__)
try:
    store=open_sqlite_storage(sys.argv[1],mode='open')
except StorageOwnershipError:
    say('refused',source=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest())
else:
    store.close()
    say('opened',source=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest())
"""


def independent_opener(path, root, stage, expected, *, invoke_held=False):
    with Child(OPENER, str(path), home=root) as child:
        observed = child.expect(expected)
        assert child.wait() == 0
        child.wait_stdout_eof()
        value = {
            "stage": stage,
            "invoke_held_at_start": invoke_held,
            "observed": observed,
            "process": child.evidence(),
        }
    (root / ("opener-" + stage + ".json")).write_text(json.dumps(value, indent=2))


@pytest.mark.parametrize("cooperative", [False, True])
def test_actual_tool_and_finalizer_hold_path_until_close_finishes(
    tmp_path, monkeypatch, cooperative
):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    entered, release = threading.Event(), threading.Event()
    finalizer, final_release, finalized = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    contexts = []

    class Held:
        definition = ToolDefinition(
            "held", "held", {"type": "object", "properties": {"x": {"type": "integer"}}}
        )

        def invoke(self, request, context):
            contexts.append(context)
            entered.set()
            try:
                assert (context.cancel_event if cooperative else release).wait(10)
                return ToolExecutionResult(
                    request.tool_request_id,
                    "actual",
                    provider_correlation_id=request.provider_correlation_id,
                )
            finally:
                finalizer.set()
                assert final_release.wait(10)
                finalized.set()

    path = tmp_path / "session.sqlite"
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
        session.request_close()
        assert contexts[0].cancel_event.wait(10)

        async def cancel_waiter():
            waiter = asyncio.create_task(session.aclose())
            await asyncio.sleep(
                0
            )  # schedule only; actual close admission was observed above
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter

        asyncio.run(cancel_waiter())
        assert not finalized.is_set()
        independent_opener(
            path, tmp_path, "invoke", "refused", invoke_held=not finalizer.is_set()
        )
        with pytest.raises(StorageOwnershipError):
            open_sqlite_storage(str(path), mode="open")
        release.set()
        assert finalizer.wait(10)
        assert not finalized.is_set()
        independent_opener(path, tmp_path, "finalizer", "refused")
        with pytest.raises(StorageOwnershipError):
            open_sqlite_storage(str(path), mode="open")
        final_release.set()
        end = session.wait_closed(timeout=10)
        assert end.reason == "closed" and finalized.is_set()
        assert not scheduler._thread.is_alive()
        independent_opener(path, tmp_path, "closed", "opened")
        reopened = open_sqlite_storage(str(path), mode="open")
        try:
            assert reopened.task(parent).to_tree()["state"]["status"] == "waiting"
        finally:
            reopened.close()
    finally:
        release.set()
        final_release.set()
        session.close(timeout=10)

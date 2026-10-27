"""Real repeated ACKs retain one current base, including close/reopen."""

import json
import sqlite3
import threading
from dataclasses import replace

import pytest
from generation_support import Wire, admit, definition, seed

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import _decode, open_sqlite_storage
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.native.tools.base import ToolDefinition, ToolExecutionResult


def nodes(value):
    count, work = 0, [value]
    while work:
        item = work.pop()
        count += 1
        if type(item) is dict:
            count += len(item)
            work.extend(item.values())
        elif type(item) is list:
            work.extend(item)
    return count


def maximum(context, index, capacity):
    reporter = context._reporter
    with reporter._condition:
        current = reporter._working
    template = current.thaw()
    empty = "" if capacity == "bytes" else {"": 0} if capacity == "keys" else []
    value = {"capture_census_payload": empty, "sequence": index}
    template["details"] = {"present": True, "value": value}
    if capacity == "bytes":
        overhead = len(json.dumps(template, ensure_ascii=True, separators=(",", ":")))
        value["capture_census_payload"] = "CENSUS_PAYLOAD_" + "x" * (
            512 * 1024 - overhead - len("CENSUS_PAYLOAD_")
        )
    elif capacity == "keys":
        overhead = len(json.dumps(template, ensure_ascii=True, separators=(",", ":")))
        key = "CENSUS_KEY_" + "x" * (512 * 1024 - overhead - len("CENSUS_KEY_"))
        value["capture_census_payload"] = {key: 0}
    else:
        value["capture_census_payload"] = [0] * (32768 - nodes(template))
    return value


@pytest.mark.parametrize("capacity", ["bytes", "nodes", "keys"])
def test_many_acknowledged_progress_flushes_have_one_base_and_reopen(
    tmp_path, capacity
):
    path = tmp_path / "progress.sqlite"
    acknowledged, errors, last = [], [], []
    held = threading.Event()

    class Port:
        definition = ToolDefinition(
            "reporter",
            "reporter",
            {"type": "object", "properties": {"x": {"type": "integer"}}},
        )

        def invoke(self, request, context):
            try:
                for index in range(20):
                    value = maximum(context, index, capacity)
                    last[:] = [value]
                    context.report_details(value)
                    with sqlite3.connect(path) as db:
                        rows = db.execute(
                            "SELECT r.document_id,r.kind,r.content FROM document_revisions r "
                            "JOIN documents d ON d.id=r.document_id WHERE d.kind=?",
                            (json.dumps("pi.tool.progress"),),
                        ).fetchall()
                    assert len(rows) == 1 and rows[0][1] == "base"
                    actual = _decode(rows[0][2])
                    if capacity != "nodes":
                        assert (
                            len(
                                json.dumps(
                                    actual, ensure_ascii=True, separators=(",", ":")
                                )
                            )
                            == 512 * 1024
                        )
                    else:
                        assert nodes(actual) == 32768
                    assert OwnedJson(actual["details"]["value"]) == OwnedJson(value)
                    acknowledged.append((rows[0][0], index, len(rows[0][2])))
                held.set()
                assert context.cancel_event.wait(30)
                return ToolExecutionResult(request.tool_request_id, "closing")
            except BaseException as error:
                errors.append(error)
                held.set()
                raise

    store = open_sqlite_storage(str(path), mode="create")
    session = Session(store)
    port = Port()
    tool = define_tool_task(
        {"reporter": ToolRegistration(port, "fixture.reporter", "1")},
        workspace_root=tmp_path,
    )
    generation = definition(tmp_path, Wire(("reporter",)))
    admit(
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
        assert held.wait(30), session.failure
        assert not errors, errors
        assert len(acknowledged) == 20
        assert len({row[0] for row in acknowledged}) == 1
        session.request_close()
        session.wait_closed(timeout=30)
        assert not scheduler._thread.is_alive()
    finally:
        session.close(timeout=30)
    reopened = open_sqlite_storage(str(path), mode="open")
    try:
        saved = reopened.document(acknowledged[-1][0])
        assert saved is not None
        tree = saved.to_tree()
        assert tree["deltasSinceBase"] == 0
        assert OwnedJson(tree["value"]["details"]["value"]) == OwnedJson(last[0])
    finally:
        reopened.close()
    with sqlite3.connect(path) as db:
        count, bases = db.execute(
            "SELECT count(*),sum(kind='base') FROM document_revisions WHERE document_id=?",
            (acknowledged[-1][0],),
        ).fetchone()
    assert count == bases == 1

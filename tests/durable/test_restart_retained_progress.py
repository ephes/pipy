"""Real retained-reporting ACK is document progress, not task-state progress."""

import threading
from dataclasses import replace

from generation_support import Wire, admit, definition, seed

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.inspection_read import peek_document
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import TaskQuery
from pipy_harness.native.durable.tool_execution import (
    PROGRESS_DOC,
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.native.tools.base import ToolDefinition, ToolExecutionResult


def test_actual_retained_details_ack_does_not_clear_current_restart_streak(tmp_path):
    entered, release, acknowledged, finish = (threading.Event() for _ in range(4))
    calls = []

    class Port:
        definition = ToolDefinition(
            "report",
            "report",
            {"type": "object", "properties": {"x": {"type": "integer"}}},
        )

        def invoke(self, request, context):
            calls.append(request)
            entered.set()
            assert release.wait(5)
            context.report_details({"committed": True})
            acknowledged.set()
            assert finish.wait(5)
            return ToolExecutionResult(request.tool_request_id, "done")

    port = Port()
    tool = define_tool_task(
        {"report": ToolRegistration(port, "fixture.report", "1")},
        workspace_root=tmp_path,
    )
    generation = definition(tmp_path, Wire(("report",)))
    store = open_sqlite_storage(str(tmp_path / "report.sqlite"), mode="create")
    session = Session(store)
    cid, _, _ = admit(
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
        assert entered.wait(5)

        def seed_counter(tx):
            rows = tx.scan_tasks(TaskQuery({"conversationId": cid}), 20).items
            row = next(
                record.to_tree()
                for record in rows
                if record.to_tree()["kind"] == tool.name
            )
            assert row["state"]["status"] == "running"
            assert row["state"]["checkpoint"]["phase"] == "execute"
            tx.set_task({**row, "restarts": 3})
            return row["id"]

        task_id = session.commit(seed_counter)
        release.set()
        assert acknowledged.wait(5)

        def read():
            row = store.task(task_id).to_tree()
            document = peek_document(store, PROGRESS_DOC, cid, task_id)
            return row, document

        row, document = session.read_on_line(read)
        assert row["restarts"] == 3
        assert document["value"]["details"] == {
            "present": True,
            "value": {"committed": True},
        }
        assert row["state"]["status"] == "running"
        assert len(calls) == 1
    finally:
        release.set()
        finish.set()
        session.close(timeout=5)
    assert not scheduler._thread.is_alive()

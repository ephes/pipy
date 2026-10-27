"""Actual Session/phase reentry is bounded by an owned outer process."""

import asyncio
import json
import threading
from dataclasses import replace

import pytest
from generation_support import admit, definition, seed
from sqlite_child_support import Child
from test_tool_boundaries import MultiRound

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.native.durable.tool_progress import ProgressEnded
from pipy_harness.native.tools.base import ToolDefinition, ToolExecutionResult


def _reentry_body(root):
    observed, done = [], threading.Event()

    class Port:
        definition = ToolDefinition(
            "reporter",
            "reporter",
            {"type": "object", "properties": {"x": {"type": "integer"}}},
        )

        def invoke(self, request, context):
            reporter = context._reporter

            def capture():
                with pytest.raises(RuntimeError, match="admitted callback"):
                    reporter.deactivate()
                observed.append("self-drain-refused")
                return "kept"

            reporter.dispatch(capture)
            with reporter._condition:
                epoch = reporter._epoch
                assert epoch == 1 and reporter._domain_failure is None

            async def host():
                with pytest.raises(ProgressEnded):
                    context.report_details({"host": True})
                observed.append("host-refused")

            asyncio.run_coroutine_threadsafe(host(), reporter._loop).result(timeout=5)
            with pytest.raises(ProgressEnded):
                session.read_on_line(
                    lambda: context.report_details({"held-line": True})
                )
            observed.append("held-line-refused")
            with reporter._condition:
                assert reporter._epoch == epoch
                assert reporter._domain_failure is None
                assert not reporter._infrastructure_failed
            return ToolExecutionResult(request.tool_request_id, "done")

    session = Session(open_sqlite_storage(str(root / "session.sqlite"), mode="create"))
    port = Port()
    tool = define_tool_task(
        {"reporter": ToolRegistration(port, "fixture.reporter", "1")},
        workspace_root=root,
    )
    wire = MultiRound((("reporter",), ()))
    generation = definition(root, wire)
    _, _, parent = admit(
        session,
        generation,
        snapshot_provider_request(
            replace(seed(root).request, available_tools=(port.definition,))
        ),
    )
    scheduler = TaskScheduler(session, [generation, tool])
    reserve = scheduler._reserve

    async def idle():
        result = await reserve()
        if result is None and any(
            r.id == parent and r.to_tree()["state"]["status"] == "terminal"
            for r in scheduler.inspect()
        ):
            done.set()
        return result

    scheduler._reserve = idle
    try:
        scheduler.open()
        scheduler.resume()
        assert done.wait(12), session.failure
        assert observed == ["self-drain-refused", "host-refused", "held-line-refused"]
        assert len(wire.calls) == 2
    finally:
        session.close(timeout=5)
    assert not scheduler._thread.is_alive()
    (root / "reentry-observed.json").write_text(json.dumps(observed))


def test_actual_phase_self_host_and_held_line_reentry_refuse_without_mutation(tmp_path):
    code = "from pathlib import Path\nfrom test_tool_reporting_reentry import _reentry_body\n_reentry_body(Path(sys.argv[1]))\nsay('passed')\n"
    with Child(code, str(tmp_path), home=tmp_path) as child:
        child.expect("passed", timeout=20)
        assert child.wait() == 0
        child.wait_stdout_eof()
        evidence = child.evidence()
    (tmp_path / "reentry-process.json").write_text(json.dumps(evidence, indent=2))

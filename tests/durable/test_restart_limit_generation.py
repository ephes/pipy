"""A native generation cut off the restart limit times in a row ends its run.

Release 1.5 ends such a run with `restart_limit` (bridge R15-2b). Each case
stops a real phase with a healthy close, which counts no restart, and then
seeds the count as a typed control on the stopped pending record, as a host
that died there repeatedly would leave it.
"""

import math
import threading
from dataclasses import replace

import pytest
from generation_support import Wire, admit, definition, seed

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable import generation as generation_module
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.native.tools.base import ToolDefinition, ToolExecutionResult


def open_store(path, *, create=False):
    store = open_sqlite_storage(str(path), mode="create" if create else "open")
    return store, Session(store)


def row(session, store, task_id):
    return session.read_on_line(lambda: store.task(task_id).to_tree())


def seed_count(path, task_id, count, phase):
    store, session = open_store(path)
    try:

        def change(tx):
            current = tx.task(task_id).to_tree()
            assert current["state"]["status"] == "pending"
            assert current["state"]["checkpoint"]["phase"] == phase
            assert "restarts" not in current  # A healthy close counts nothing.
            tx.set_task({**current, "restarts": count})

        session.commit(change)
    finally:
        session.close(timeout=5)


def terminal(session, task_id):
    done = threading.Event()

    def observe(publication):
        for change in publication.changes:
            if hasattr(change, "to_tree"):
                value = change.to_tree()
                if (
                    value["type"] == "task"
                    and value["value"]["id"] == task_id
                    and value["value"]["state"]["status"] == "terminal"
                ):
                    done.set()

    session.subscribe_commits(observe)
    return done


class OnceWire(Wire):
    """Calls tools in its first answer only, then answers."""

    def post_json(self, url, **kwargs):
        if self.calls:
            self.tool_calls = ()
        return super().post_json(url, **kwargs)


def run_to_end(path, definitions, task_id, *, restart_limit=5, update=None):
    store, session = open_store(path)
    try:
        done = terminal(session, task_id)
        scheduler = TaskScheduler(session, definitions, restart_limit=restart_limit)
        scheduler.open()
        if update is not None:
            assert scheduler.set_restart_limit(update)
        scheduler.resume()
        assert done.wait(5)
        return row(session, store, task_id)
    finally:
        session.close(timeout=5)


def limit_outcome(text):
    return {
        "status": "terminal",
        "outcome": {
            "status": "failed",
            "error": {"message": text, "detail": {"reason": "restart_limit"}},
        },
    }


@pytest.mark.parametrize(
    ("count", "said"), [(5, "5 times in a row"), (7.5, "7.5 times in a row")]
)
def test_prepare_at_the_limit_ends_the_run_without_a_request(tmp_path, count, said):
    path, wire = tmp_path / "g.sqlite", Wire()
    task = definition(tmp_path, wire)
    store, session = open_store(path, create=True)
    _, _, task_id = admit(session, task, seed(tmp_path))
    session.close(timeout=5)
    seed_count(path, task_id, count, "prepare")
    ended = run_to_end(path, [task], task_id)
    assert ended["state"] == limit_outcome(
        f"The generation was interrupted {said} in its prepare phase"
    )
    assert "restarts" not in ended and not wire.calls


@pytest.mark.parametrize(
    ("limit", "update"), [(6, None), (math.inf, None), (5, 6)], ids=str
)
def test_below_or_raised_limit_prepares_and_answers(tmp_path, limit, update):
    path, wire = tmp_path / "g.sqlite", Wire()
    task = definition(tmp_path, wire)
    store, session = open_store(path, create=True)
    _, _, task_id = admit(session, task, seed(tmp_path))
    session.close(timeout=5)
    seed_count(path, task_id, 5, "prepare")
    # The phase reads the owner's current policy when it starts.
    ended = run_to_end(path, [task], task_id, restart_limit=limit, update=update)
    assert ended["state"]["outcome"]["status"] == "completed"
    assert len(wire.calls) == 1


def stop_in_request(path, tmp_path, wire, task):
    wire.release.clear()
    store, session = open_store(path, create=True)
    _, _, task_id = admit(session, task, seed(tmp_path))
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    scheduler.resume()
    assert wire.entered.wait(5)
    # The sealed line refuses the receipt commit, so the stopped request stays
    # in its phase however the transport and the stop signal race.
    session.request_close()
    wire.release.set()
    session.wait_closed(timeout=5)
    assert not scheduler._thread.is_alive()
    return task_id


def test_request_at_the_limit_is_not_resent(tmp_path):
    path, wire = tmp_path / "g.sqlite", Wire()
    task = definition(tmp_path, wire)
    task_id = stop_in_request(path, tmp_path, wire, task)
    assert len(wire.calls) == 1
    seed_count(path, task_id, 5, "request")
    ended = run_to_end(path, [task], task_id)
    assert ended["state"] == limit_outcome(
        "The generation was interrupted 5 times in a row in its request phase"
    )
    assert len(wire.calls) == 1


def test_request_below_the_limit_is_resent(tmp_path):
    path, wire = tmp_path / "g.sqlite", Wire()
    task = definition(tmp_path, wire)
    task_id = stop_in_request(path, tmp_path, wire, task)
    seed_count(path, task_id, 4, "request")
    ended = run_to_end(path, [task], task_id)
    # Until DE17 charges an interrupted request, recovery resends it.
    assert ended["state"]["outcome"]["status"] == "completed"
    assert len(wire.calls) == 2


def held_phase(monkeypatch, name):
    """Stop the named phase at entry until close signals its invocation."""
    entered = threading.Event()

    async def held(self, record, runtime, context):
        assert record["state"]["checkpoint"]["phase"] == name
        entered.set()
        await context.signal.wait()

    monkeypatch.setattr(generation_module._Generation, name, held)
    return entered


class Port:
    definition = ToolDefinition(
        "report", "report", {"type": "object", "properties": {"x": {"type": "integer"}}}
    )

    def __init__(self):
        self.calls = []

    def invoke(self, request, context):
        self.calls.append(request)
        return ToolExecutionResult(request.tool_request_id, "done")


def tool_round(tmp_path, wire):
    port = Port()
    tool = define_tool_task(
        {"report": ToolRegistration(port, "fixture.report", "1")},
        workspace_root=tmp_path,
    )
    snapshot = snapshot_provider_request(
        replace(seed(tmp_path).request, available_tools=(port.definition,))
    )
    return port, tool, snapshot


@pytest.mark.parametrize("phase", ["tools", "settle"])
def test_phase_after_the_receipt_at_the_limit(tmp_path, monkeypatch, phase):
    path, wire = tmp_path / "g.sqlite", OnceWire(("report",))
    port, tool, snapshot = tool_round(tmp_path, wire)
    entered = held_phase(monkeypatch, phase)
    held = definition(tmp_path, wire)
    store, session = open_store(path, create=True)
    _, _, task_id = admit(session, held, snapshot)
    scheduler = TaskScheduler(session, [held, tool])
    scheduler.open()
    scheduler.resume()
    assert entered.wait(5)
    session.close(timeout=5)
    assert not scheduler._thread.is_alive()
    monkeypatch.undo()
    seed_count(path, task_id, 5, phase)
    task = definition(tmp_path, wire)
    ended = run_to_end(path, [task, tool], task_id)
    if phase == "tools":
        # The round is closed: the run ends instead of preparing again.
        assert ended["state"] == limit_outcome(
            "The generation was interrupted 5 times in a row in its tools phase"
        )
        assert len(wire.calls) == 1 and len(port.calls) == 1
    else:
        # Settlement only commits the outcome its receipt holds: no new
        # request, and the run continues with its tool round.
        assert ended["state"]["outcome"]["status"] == "completed"
        assert len(port.calls) == 1 and len(wire.calls) == 2

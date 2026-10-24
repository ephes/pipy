"""Actual committed operator metadata, never a task-code availability probe."""

import asyncio
import json
from dataclasses import fields

import pytest

from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.execution_support import ExecutionSupport
from pipy_harness.native.durable.generation_tasks import (
    GENERATION_NAME,
    GENERATION_VERSION,
)
from pipy_harness.native.durable.inspection import (
    ainspect_live,
    ainspect_task,
    inspect_live,
    inspect_task,
)
from pipy_harness.native.durable.inspection_values import (
    InspectionError,
    InspectionSupport,
)
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.resolution_values import ResolutionError
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import ReentrantSessionCall, Session
from pipy_harness.native.durable.tasks import capture, define_task

PRIVATE = "synthetic-private-payload"


@pytest.mark.parametrize("invalid_id", [0, -1, True, "private-id", 1.5, 2**53])
@pytest.mark.parametrize(
    "api",
    [
        "inspect_task",
        "inspect_live",
        "resolution_target",
        "ainspect_task",
        "ainspect_live",
        "aresolution_target",
    ],
)
def test_invalid_operator_ids_have_closed_errors_before_read(
    monkeypatch, invalid_id, api
):
    session = Session(MemoryStorage())
    scheduler = TaskScheduler(session, [])
    monkeypatch.setattr(
        Session, "read_on_line", lambda *a: pytest.fail("invalid id read storage")
    )
    calls = {
        "inspect_task": lambda: inspect_task(session, invalid_id),
        "inspect_live": lambda: inspect_live(session, conversation_id=invalid_id),
        "resolution_target": lambda: scheduler.resolution_target(invalid_id),
        "ainspect_task": lambda: ainspect_task(session, invalid_id),
        "ainspect_live": lambda: ainspect_live(session, conversation_id=invalid_id),
        "aresolution_target": lambda: scheduler.aresolution_target(invalid_id),
    }
    error_type = ResolutionError if "resolution" in api else InspectionError
    try:
        with pytest.raises(error_type) as error:
            result = calls[api]()
            if api.startswith("a"):
                asyncio.run(result)
        assert error.value.code == (
            "incompatible" if error_type is ResolutionError else "invalid_request"
        )
    finally:
        session.close()


async def poison(*args):
    pytest.fail("inspection ran a task callback")


def admitted(session, *, name=PRIVATE, version=-1.5, checkpoint=None):
    task = define_task(
        name,
        version,
        lambda _: checkpoint or {"phase": "execute", "payload": PRIVATE},
        {"execute": poison},
        poison,
    )
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    tid = session.commit(
        lambda tx: tx.create_task(
            task,
            {"private": PRIVATE},
            ownership={"kind": "conversation"},
            conversation_id=cid,
        )
    )
    return task, cid, tid


def test_actual_metadata_allowlist_no_writes_or_task_code(monkeypatch):
    backend = MemoryStorage()
    session = Session(backend)
    task, cid, tid = admitted(session)
    support = InspectionSupport((task,))
    monkeypatch.setattr(backend, "mint_id", lambda: pytest.fail("inspection minted"))
    monkeypatch.setattr(backend, "commit", lambda _: pytest.fail("inspection wrote"))
    try:
        report = inspect_task(session, tid, support=support)
        assert report is not None
        body = report.to_tree()
        assert set(body) == {field.name for field in fields(report)}
        assert body == {
            "task_id": tid,
            "conversation_id": cid,
            "owner_id": None,
            "family": "custom",
            "version": -1.5,
            "status": "pending",
            "phase": "execute",
            "outcome": None,
            "reason": None,
            "compatibility": "supported",
            "dependencies": [],
            "dependency_count": 0,
            "invocation_ownership": "unknown",
            "execution_material": "not_checked",
        }
        assert PRIVATE not in json.dumps(body)
        assert inspect_live(session, support=support).tasks == (report,)
        assert inspect_task(session, tid + 100) is None
    finally:
        session.close()


def test_terminal_metadata_needs_no_definition_or_payload_decoder(monkeypatch):
    session = Session(MemoryStorage())
    _, _, tid = admitted(session, name=GENERATION_NAME, version=41.5)

    def terminal(tx):
        row = tx.task(tid).to_tree()
        tx.set_task(
            {
                **row,
                "state": {
                    "status": "terminal",
                    "outcome": {
                        "status": "faulted",
                        "error": {"message": PRIVATE, "detail": {"material": PRIVATE}},
                    },
                },
            }
        )

    session.commit(terminal)
    monkeypatch.setattr(
        "pipy_harness.native.durable.inspection.decode_checkpoint",
        lambda *args, **kwargs: pytest.fail("decoded terminal payload"),
    )
    try:
        report = inspect_task(session, tid)
        assert (
            report.outcome == "faulted" and report.compatibility == "terminal_readonly"
        )
        assert report.phase is None and PRIVATE not in json.dumps(report.to_tree())
        assert not inspect_live(session).tasks
    finally:
        session.close()


def test_newer_envelope_reports_outer_metadata_without_decoder(monkeypatch):
    session = Session(MemoryStorage())
    _, _, tid = admitted(
        session,
        name=GENERATION_NAME,
        version=GENERATION_VERSION,
        checkpoint={"phase": "request", "envelope_version": 999, "payload": PRIVATE},
    )
    monkeypatch.setattr(
        "pipy_harness.native.durable.inspection.decode_checkpoint",
        lambda *args, **kwargs: pytest.fail("decoded newer format"),
    )
    try:
        report = inspect_task(session, tid)
        assert report.reason == "unsupported_format" and report.phase == "request"
        assert PRIVATE not in json.dumps(report.to_tree())
    finally:
        session.close()


def test_support_copies_finite_metadata_and_preserves_numeric_version():
    raw = {
        "family": "generation",
        "version": 1,
        "provider": "fixture",
        "model": "m",
        "api": "fake",
        "thinking": None,
        "options": {},
        "pricing": None,
    }
    token = ExecutionSupport(raw)
    task = define_task("name", -0.0, lambda _: {}, {}, poison, support=token)
    raw["provider"] = PRIVATE
    copied = capture(task)
    assert copied.support.to_tree()["provider"] == "fixture"
    assert OwnedJson(copied.version).thaw() == -0.0
    view = InspectionSupport((copied,))
    assert view.definition("name")["support"]["provider"] == "fixture"


def test_async_read_and_session_reentry_refusal():
    session = Session(MemoryStorage())
    _, _, tid = admitted(session)

    async def run():
        assert (await ainspect_task(session, tid)).task_id == tid

        async def held(tx):
            with pytest.raises(ReentrantSessionCall):
                await ainspect_task(session, tid)

        await session.acommit(held)

    try:
        asyncio.run(run())
    finally:
        session.close()

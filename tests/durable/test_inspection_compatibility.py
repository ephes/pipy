"""Pure descriptor fit and actual pre-effect drift, with no material probes."""

import json
import signal
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from generation_support import SPEC, Wire, admit, definition, seed
from sqlite_child_support import Child
from test_generation_sqlite import CHILD as GENERATION_CHILD
from test_resolution import setup
from test_tool_boundaries import RichTool, run_idle
from test_tool_execution import KeyedTool
from tool_support import counts

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.generation import define_generation
from pipy_harness.native.durable.inspection import inspect_task
from pipy_harness.native.durable.inspection_values import InspectionSupport
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.resolution_values import ResolutionError
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))


@pytest.mark.parametrize("names", [("rich", "later"), ("rich",), ("later", "rich")])
def test_inspect_derives_terminate_hold_never_resolves_it(tmp_path, names):
    session = Session(MemoryStorage())
    port, later = RichTool(None, terminate=True), KeyedTool("later", [])
    wire = Wire(names)
    generation = definition(tmp_path, wire)
    tool = define_tool_task(
        {
            p.definition.name: ToolRegistration(p, "fixture." + p.definition.name, "1")
            for p in (port, later)
        },
        workspace_root=tmp_path,
    )
    snapshot = snapshot_provider_request(
        replace(
            seed(tmp_path).request, available_tools=(port.definition, later.definition)
        )
    )
    cid, _, parent = admit(session, generation, snapshot)
    try:
        scheduler = run_idle(session, [generation, tool])
        support = InspectionSupport((generation, tool))
        report = inspect_task(session, parent, support=support)
        assert report.reason == "retained_terminate"
        assert report.status == ("pending" if names[-1] == "rich" else "waiting")
        if names[-1] != "rich":
            later_row = next(
                r
                for r in scheduler.inspect()
                if r.to_tree()["kind"] == "pi.tool"
                and r.to_tree()["input"]["callId"] == "call:1"
            )
            assert (
                inspect_task(session, later_row.id, support=support).reason
                == "retained_terminate"
            )
        with pytest.raises(ResolutionError) as refusal:
            scheduler.resolve(
                scheduler.resolution_target(parent),
                nonce="a" * 32,
                new_request=snapshot,
            )
        assert refusal.value.code == "not_supported"
        assert len(port.calls) == 1 and len(wire.calls) == 1
        assert len(later.calls) == int(names[0] == "later")
    finally:
        session.close(timeout=10)


@pytest.mark.parametrize("drift", ["model", "api", "environment", "codec"])
def test_saved_request_actual_execution_refuses_drift_before_transport(
    tmp_path, monkeypatch, drift
):
    script = GENERATION_CHILD
    if drift == "environment":
        script = script.replace(
            "wire = Counted(",
            'from dataclasses import replace\nimport generation_support\ngeneration_support.SPEC=replace(generation_support.SPEC,api="openai-responses")\nwire = Counted(',
        )
    with Child(
        script,
        str(Path(__file__).parent),
        str(tmp_path),
        "request-after",
        "no",
        home=tmp_path,
    ) as child:
        ready = child.expect("ready")
        child.send("go")
        assert child.expect("cut")["label"] == "request-after"
        child.kill_group()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        (tmp_path / "compatibility-cut.json").write_text(
            json.dumps(child.evidence(), indent=2)
        )
    wire = Wire()
    spec = (
        replace(SPEC, model_id="different")
        if drift == "model"
        else replace(SPEC, api="openai-responses")
        if drift == "api"
        else replace(SPEC, api="openai-responses")
        if drift == "environment"
        else SPEC
    )
    generation = define_generation(
        spec,
        auth_path=tmp_path / "auth.json",
        env={"PIPY_CACHE_RETENTION": "long"} if drift == "environment" else {},
        runtime_api_key="fixture-key",
        http_client=wire,
    )
    session = Session(
        open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="open")
    )
    if drift == "codec":

        def newer(tx):
            row = tx.task(ready["task_id"]).to_tree()
            frame = {**row["state"]["checkpoint"], "envelope_version": 99}
            tx.set_task({**row, "state": {**row["state"], "checkpoint": frame}})

        session.commit(newer)
    try:
        # Inspection itself may not dereference private material/auth, even when
        # execution later needs that authority to decide environment fit.
        with monkeypatch.context() as context:
            context.setattr(
                "pipy_harness.native.durable.provider_checkpoint.reconstruct_provider",
                lambda *a, **k: pytest.fail("inspection reconstructed"),
            )
            report = inspect_task(
                session, ready["task_id"], support=InspectionSupport((generation,))
            )
        if drift in ("model", "api", "codec"):
            assert report.compatibility == "unsupported"
        assert report.execution_material == "not_checked"
        scheduler = TaskScheduler(session, [generation])
        scheduler.open()
        idle = threading.Event()
        reserve = scheduler._reserve

        async def drained():
            result = await reserve()
            if result is None:
                idle.set()
            return result

        scheduler._reserve = drained
        scheduler.resume()
        assert idle.wait(10), session.failure
        assert wire.calls == []
        row = next(r.to_tree() for r in scheduler.inspect() if r.id == ready["task_id"])
        assert (
            row["state"]["status"] == "terminal"
            and row["state"]["outcome"]["status"] == "faulted"
        )
        assert "different" not in json.dumps(
            inspect_task(session, ready["task_id"]).to_tree()
        )
    finally:
        session.close(timeout=10)


@pytest.mark.parametrize(
    "drift", ["revision", "schema", "workspace", "missing", "policy"]
)
def test_blocked_tool_current_drift_refuses_new_authorization_without_effect(
    tmp_path, drift
):
    session, scheduler, cid, parent, old, _ = setup(tmp_path, "sqlite")
    session.close(timeout=10)
    from tool_support import definitions

    kwargs = (
        {"revision": "2"}
        if drift == "revision"
        else {"schema": {"type": "object"}}
        if drift == "schema"
        else {"workspace_root": tmp_path / "other"}
        if drift == "workspace"
        else {"missing": True}
        if drift == "missing"
        else {"replay": "safe"}
    )
    generation, tool, _ = definitions(tmp_path, **kwargs)
    reopened = Session(
        open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="open")
    )
    owner = TaskScheduler(reopened, [generation, tool])
    try:
        owner.open()
        before = counts(tmp_path)
        support = InspectionSupport((generation, tool))
        assert (
            inspect_task(reopened, old["id"], support=support).reason == "incompatible"
        )
        with pytest.raises(ResolutionError) as refusal:
            owner.resolve(owner.resolution_target(old["id"]), nonce="b" * 32)
        assert refusal.value.code == "incompatible" and counts(tmp_path) == before
    finally:
        reopened.close(timeout=10)

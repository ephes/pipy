"""Live Bash domain and authority cuts retain actual cleanup ownership."""

import asyncio
import json
import os
import shlex
import sqlite3
import sys
import threading
import traceback
from dataclasses import replace
from functools import partial

import pytest
from generation_support import admit, definition, seed
from test_tool_boundaries import MultiRound, isolated
from test_tool_lifecycle import independent_opener

from pipy_harness.native.agent.content import ToolPartialResult
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import (
    SqliteStorage,
    _decode,
    open_sqlite_storage,
)
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.native.durable.tool_progress import RetainedToolProgress
from pipy_harness.native.tools import bash
from pipy_harness.native.tools.base import ToolDefinition, ToolRequest


@pytest.mark.parametrize(
    "fault",
    [
        "bytes",
        "nodes",
        "backend_failed",
        "backend_ambiguous",
        "closed",
        "ended",
        "busy",
        "permits",
        "busy_cancel",
        "timeout",
        "barrier_cancel",
        "wake",
    ],
)
def test_live_bash_domain_or_authority_fault_retains_actual_cleanup(
    tmp_path, monkeypatch, fault
):
    isolated(monkeypatch, tmp_path)
    reached, returned, finalizer, release, finalized, completed = (
        threading.Event() for _ in range(6)
    )
    processes, reporters, contexts, errors, observations = ([], [], [], [], [])
    original_popen = bash.subprocess.Popen
    original_capture = RetainedToolProgress._capture_output
    original_dispatch = RetainedToolProgress.dispatch
    original_schedule = RetainedToolProgress._schedule
    original_commit = SqliteStorage.commit
    original_snapshot = bash.OutputAccumulator.snapshot
    capture_release, backend_release, backend_held, sealed = (
        threading.Event() for _ in range(4)
    )
    barriers, barrier_errors, sealers, snapshot_calls = ([], [], [], [])
    armed = threading.Event()
    cut_once = threading.Event()
    script = "import os,time; from pathlib import Path; Path('effect').write_text('one\\n'); Path('pid').write_text(str(os.getpid())); print('first',flush=True)\nwhile not Path('again').exists(): time.sleep(0.01)\nprint('second',flush=True)\nwhile not Path('finish').exists(): time.sleep(0.01)\n"
    command = "exec " + shlex.quote(sys.executable) + " -c " + shlex.quote(script)

    class Shell:
        definition = ToolDefinition(
            "shell",
            "shell",
            {"type": "object", "properties": {"x": {"type": "integer"}}},
        )

        def invoke(self, request, context):
            contexts.append(context)
            reporters.append(context._reporter)
            context.report_details({"accepted": [1]})
            try:
                return bash.BashTool(shell_path="/bin/bash").invoke(
                    ToolRequest(
                        request.tool_request_id,
                        "bash",
                        {
                            "command": command,
                            **({"timeout": 2} if fault == "timeout" else {}),
                        },
                    ),
                    context,
                )
            except BaseException as error:
                errors.append(error)
                raise
            finally:
                finalizer.set()
                assert release.wait(15)
                finalized.set()

    path = tmp_path / "session.sqlite"
    store = open_sqlite_storage(str(path), mode="create")
    session = Session(store)
    port = Shell()
    tool = define_tool_task(
        {"shell": ToolRegistration(port, "fixture.shell", "1")}, workspace_root=tmp_path
    )
    wire = MultiRound((("shell",), ()))
    generation = definition(tmp_path, wire)
    cid, _, parent = admit(
        session,
        generation,
        snapshot_provider_request(
            replace(seed(tmp_path).request, available_tools=(port.definition,))
        ),
    )
    scheduler = TaskScheduler(session, [generation, tool])
    reserve = scheduler._reserve
    popen = partial(_matrix_popen, original_popen=original_popen, processes=processes)
    capture = partial(
        _matrix_capture,
        fault=fault,
        armed=armed,
        processes=processes,
        tmp_path=tmp_path,
        observations=observations,
        session=session,
        scheduler=scheduler,
        original_capture=original_capture,
        reached=reached,
    )
    schedule = partial(
        _matrix_schedule, fault=fault, armed=armed, original_schedule=original_schedule
    )
    dispatch = partial(
        _matrix_dispatch,
        original_dispatch=original_dispatch,
        armed=armed,
        returned=returned,
    )
    snapshot = partial(
        _matrix_snapshot,
        fault=fault,
        snapshot_calls=snapshot_calls,
        armed=armed,
        processes=processes,
        tmp_path=tmp_path,
        reached=reached,
        capture_release=capture_release,
        original_snapshot=original_snapshot,
    )
    commit = partial(
        _matrix_commit,
        store=store,
        armed=armed,
        fault=fault,
        cut_once=cut_once,
        processes=processes,
        backend_held=backend_held,
        backend_release=backend_release,
        original_commit=original_commit,
        observations=observations,
        reached=reached,
    )
    monkeypatch.setattr(bash.subprocess, "Popen", popen)
    monkeypatch.setattr(RetainedToolProgress, "_capture_output", _bound_method(capture))
    monkeypatch.setattr(RetainedToolProgress, "dispatch", _bound_method(dispatch))
    monkeypatch.setattr(RetainedToolProgress, "_schedule", _bound_method(schedule))
    monkeypatch.setattr(SqliteStorage, "commit", _bound_method(commit))
    monkeypatch.setattr(bash.OutputAccumulator, "snapshot", _bound_method(snapshot))
    observe_idle = partial(
        _matrix_observe_idle,
        reserve=reserve,
        scheduler=scheduler,
        completed=completed,
        parent=parent,
    )
    scheduler._reserve = observe_idle
    try:
        scheduler.open()
        scheduler.resume()
        assert reached.wait(12), (fault, session.failure)
        busy_capture = partial(
            _matrix_busy_capture,
            reporters=reporters,
            sealers=sealers,
            sealed=sealed,
            finalizer=finalizer,
            path=path,
            tmp_path=tmp_path,
            fault=fault,
            processes=processes,
            capture_release=capture_release,
            session=session,
        )
        if fault.startswith("busy"):
            busy_capture()
        assert returned.wait(5)
        cancel_barrier = partial(
            _matrix_cancel_barrier,
            backend_held=backend_held,
            reporters=reporters,
            path=path,
            tmp_path=tmp_path,
            session=session,
            backend_release=backend_release,
            contexts=contexts,
        )
        if fault == "barrier_cancel":
            cancel_barrier()
        held_permits = partial(
            _matrix_held_permits,
            backend_held=backend_held,
            reporters=reporters,
            barriers=barriers,
            path=path,
            tmp_path=tmp_path,
            backend_release=backend_release,
            barrier_errors=barrier_errors,
            contexts=contexts,
        )
        if fault == "permits":
            held_permits()
        assert int((tmp_path / "pid").read_text()) == processes[0].pid
        reporter = reporters[0]
        with reporter._condition:
            before = reporter._epoch
            ack = reporter._acknowledged
            if fault in ("bytes", "nodes", "busy", "permits"):
                assert reporter._domain_failure == "progress_capacity"
                assert before == ack == (18 if fault == "permits" else 1)
        independent_opener(
            path, tmp_path, fault + "-owned", "refused", invoke_held=True
        )
        _matrix_wake_retained(fault, reporter, processes, observations)
        if fault in ("bytes", "nodes", "busy", "permits"):
            assert processes[0].poll() is None
            (tmp_path / "again").touch()
            (tmp_path / "finish").touch()
        elif fault != "timeout":
            contexts[0].output_sink("after infrastructure cut")
            session.request_close()
        assert finalizer.wait(12), (fault, session.failure)
        assert processes[0].returncode is not None
        assert not finalized.is_set()
        assert errors == []
        independent_opener(path, tmp_path, fault + "-finalizer", "refused")
        verify_and_release = partial(
            _matrix_verify_and_release,
            path=path,
            fault=fault,
            observations=observations,
            cut_once=cut_once,
            reporter=reporter,
            snapshot_calls=snapshot_calls,
            release=release,
            completed=completed,
            session=session,
            finalized=finalized,
            scheduler=scheduler,
            tmp_path=tmp_path,
            processes=processes,
            wire=wire,
            before=before,
            ack=ack,
        )
        verify_and_release()
    finally:
        wake_red_witness = partial(
            _matrix_wake_red_witness,
            finalizer=finalizer,
            finalized=finalized,
            tmp_path=tmp_path,
            processes=processes,
            path=path,
            reporters=reporters,
            wire=wire,
            observations=observations,
            returned=returned,
            errors=errors,
        )
        if fault == "wake" and processes and (processes[0].poll() is None):
            wake_red_witness()
        _matrix_cleanup(
            tmp_path,
            fault,
            returned,
            errors,
            finalizer,
            finalized,
            processes,
            capture_release,
            backend_release,
            release,
            session,
            barriers,
            sealers,
        )


def _matrix_popen(argv, *args, original_popen, processes, **kwargs):
    process = original_popen(argv, *args, **kwargs)
    if argv[0] == "/bin/bash":
        processes.append(process)
    return process


def _matrix_wake_retained(fault, reporter, processes, observations):
    if fault != "wake":
        return
    assert observations == [{"before_install_epoch": 1, "before_install_ack": 1}]
    assert processes[0].poll() is None
    with reporter._condition:
        assert reporter._epoch == 2 and reporter._acknowledged == 1
        assert reporter._infrastructure_failed
    captured = []
    reporter.dispatch(lambda: captured.append(True) or "later refused output")
    assert captured == []
    with reporter._condition:
        assert reporter._epoch == 2 and reporter._acknowledged == 1


def _matrix_capture(
    self,
    output,
    *,
    fault,
    armed,
    processes,
    tmp_path,
    observations,
    session,
    scheduler,
    original_capture,
    reached,
):
    if not fault.startswith("busy") and (not armed.is_set()):
        assert len(processes) == 1 and processes[0].poll() is None
        assert (tmp_path / "effect").read_text() == "one\n"
        with self._condition:
            assert self._acknowledged == self._epoch == 1
            if fault == "wake":
                observations.append(
                    {
                        "before_install_epoch": self._epoch,
                        "before_install_ack": self._acknowledged,
                    }
                )
        armed.set()
        if fault == "bytes":
            output = ToolPartialResult("live", {"huge": "x" * (512 * 1024)})
        elif fault == "nodes":
            output = ToolPartialResult("live", {"wide": [0] * 32768})
        elif fault == "closed":
            session.request_close()
        elif fault == "ended":
            with scheduler._guard:
                invocation = scheduler._invocation
            assert invocation is not None
            scheduler._end(invocation)
        try:
            return original_capture(self, output)
        finally:
            if fault in (
                "bytes",
                "nodes",
                "closed",
                "ended",
                "permits",
                "timeout",
                "barrier_cancel",
                "wake",
            ):
                reached.set()
    return original_capture(self, output)


def _matrix_schedule(self, *, fault, armed, original_schedule):
    if fault == "wake" and armed.is_set():
        raise RuntimeError("injected unexpected retained wake failure")
    return original_schedule(self)


def _matrix_dispatch(self, capture, *, original_dispatch, armed, returned):
    value = original_dispatch(self, _bound_method(capture))
    if armed.is_set():
        returned.set()
    return value


def _matrix_snapshot(
    self,
    *args,
    fault,
    snapshot_calls,
    armed,
    processes,
    tmp_path,
    reached,
    capture_release,
    original_snapshot,
    **kwargs,
):
    if fault.startswith("busy") and kwargs.get("persist_if_truncated"):
        # The bound test adapter adds exactly one frame. The next frame must
        # still be the actual Bash implementation, never another test helper.
        adapter = sys._getframe(1)
        assert adapter.f_code.co_name == "invoke"
        assert adapter.f_code.co_filename == __file__
        native = adapter.f_back
        assert native is not None and native.f_code.co_filename == bash.__file__
        caller = native.f_code.co_name
        snapshot_calls.append(caller)
        if caller == "capture" and (not armed.is_set()):
            assert processes[0].poll() is None
            assert (tmp_path / "effect").read_text() == "one\n"
            armed.set()
            reached.set()
            assert capture_release.wait(15)
    return original_snapshot(self, *args, **kwargs)


def _matrix_commit(
    self,
    writes,
    *,
    store,
    armed,
    fault,
    cut_once,
    processes,
    backend_held,
    backend_release,
    original_commit,
    observations,
    reached,
):
    if (
        self is store
        and armed.is_set()
        and (fault in ("permits", "barrier_cancel"))
        and (not cut_once.is_set())
    ):
        cut_once.set()
        assert processes[0].poll() is None
        backend_held.set()
        assert backend_release.wait(15)
    if (
        self is store
        and armed.is_set()
        and fault.startswith("backend_")
        and (not cut_once.is_set())
    ):
        cut_once.set()
        assert processes[0].poll() is None
        if fault == "backend_ambiguous":
            seq = original_commit(self, writes)
            observations.append({"actual_committed_seq": seq})
        reached.set()
        raise OSError("synthetic private backend acknowledgement failure")
    return original_commit(self, writes)


async def _matrix_observe_idle(*, reserve, scheduler, completed, parent):
    result = await reserve()
    if result is None and any(
        (
            row.id == parent and row.to_tree()["state"]["status"] == "terminal"
            for row in scheduler.inspect()
        )
    ):
        completed.set()
    return result


async def _matrix_cancel_close_waiter(*, session):
    waiter = asyncio.create_task(session.aclose())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter


def _matrix_busy_capture(
    *,
    reporters,
    sealers,
    sealed,
    finalizer,
    path,
    tmp_path,
    fault,
    processes,
    capture_release,
    session,
):
    reporter = reporters[0]
    forbidden = []
    with reporter._condition:
        assert reporter._active_callbacks == 1 and reporter._preparing
        assert reporter._epoch == reporter._acknowledged == 1
    reporter.dispatch(lambda: forbidden.append(True) or "not captured")
    assert forbidden == []
    closer = threading.Thread(target=lambda: (reporter.deactivate(), sealed.set()))
    sealers.append(closer)
    closer.start()
    with reporter._condition:
        assert reporter._condition.wait_for(lambda: not reporter._active, 5)
        assert reporter._active_callbacks == 1
    assert not sealed.is_set() and (not finalizer.is_set())
    independent_opener(path, tmp_path, "pre-bash-snapshot", "refused", invoke_held=True)
    if fault == "busy_cancel":
        cancel_close_waiter = partial(_matrix_cancel_close_waiter, session=session)
        asyncio.run(cancel_close_waiter())
        assert not sealed.is_set() and (not finalizer.is_set())
        assert processes[0].poll() is None
        independent_opener(path, tmp_path, "closed-caller-loop-held-capture", "refused")
    capture_release.set()
    closer.join(5)
    assert not closer.is_alive() and sealed.is_set()


def _matrix_barrier_call(*, contexts, barrier_results, exited):
    try:
        contexts[0].report_details({"cancelled-wrapper": True})
        barrier_results.append("ack")
    except BaseException as error:  # noqa: BLE001 - retain actual owned barrier outcome
        barrier_results.append(type(error).__name__)
    finally:
        exited.set()


def _matrix_admitted(*, reporter):
    with reporter._condition:
        return reporter._condition.wait_for(
            lambda: reporter._epoch == 3 and (not reporter._preparing), 5
        )


async def _matrix_abandon_wrapper(*, barrier_call, admitted):
    task = asyncio.create_task(asyncio.to_thread(barrier_call))
    assert await asyncio.to_thread(admitted)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def _matrix_cancel_barrier(
    *, backend_held, reporters, path, tmp_path, session, backend_release, contexts
):
    assert backend_held.wait(5)
    reporter = reporters[0]
    exited = threading.Event()
    barrier_results = []
    barrier_call = partial(
        _matrix_barrier_call,
        contexts=contexts,
        barrier_results=barrier_results,
        exited=exited,
    )
    admitted = partial(_matrix_admitted, reporter=reporter)
    loop = asyncio.new_event_loop()
    abandon_wrapper = partial(
        _matrix_abandon_wrapper, barrier_call=barrier_call, admitted=admitted
    )
    try:
        loop.run_until_complete(abandon_wrapper())
    finally:
        loop.close()
    assert loop.is_closed() and (not exited.is_set())
    with reporter._condition:
        assert reporter._active_callbacks == 1 and reporter._acknowledged == 1
    independent_opener(path, tmp_path, "cancelled-wrapper-physical-barrier", "refused")
    session.request_close()
    backend_release.set()
    assert exited.wait(5)
    assert barrier_results == ["ProgressEnded"]


def _matrix_barrier(index, *, contexts, barrier_errors):
    try:
        contexts[0].report_details({"barrier": index})
    except BaseException as error:  # noqa: BLE001 - retain actual owned barrier outcome
        barrier_errors.append(error)


def _matrix_held_permits(
    *,
    backend_held,
    reporters,
    barriers,
    path,
    tmp_path,
    backend_release,
    barrier_errors,
    contexts,
):
    assert backend_held.wait(5)
    reporter = reporters[0]
    barrier = partial(_matrix_barrier, contexts=contexts, barrier_errors=barrier_errors)
    for index in range(16):
        worker = threading.Thread(target=barrier, args=(index,))
        barriers.append(worker)
        worker.start()
        with reporter._condition:
            assert reporter._condition.wait_for(
                lambda: reporter._epoch == index + 3 and (not reporter._preparing), 5
            )
            assert reporter._active_callbacks == index + 1
    assert all((worker.is_alive() for worker in barriers))
    with reporter._condition:
        admitted_epoch = reporter._epoch
        assert admitted_epoch == 18 and reporter._acknowledged == 1
    forbidden = []
    reporter.dispatch(lambda: forbidden.append(True) or "not captured")
    assert forbidden == []
    with reporter._condition:
        assert reporter._epoch == admitted_epoch
        assert reporter._active_callbacks == 16
        assert reporter._domain_failure == "progress_capacity"
    independent_opener(
        path, tmp_path, "sixteen-barriers-backend", "refused", invoke_held=True
    )
    backend_release.set()
    for worker in barriers:
        worker.join(5)
    assert all((not worker.is_alive() for worker in barriers)) and (not barrier_errors)


def _matrix_verify_and_release(
    *,
    path,
    fault,
    observations,
    cut_once,
    reporter,
    snapshot_calls,
    release,
    completed,
    session,
    finalized,
    scheduler,
    tmp_path,
    processes,
    wire,
    before,
    ack,
):
    with sqlite3.connect(path) as db:
        progress_rows = db.execute(
            "SELECT r.kind,r.content FROM document_revisions r JOIN documents d ON d.id=r.document_id WHERE d.kind=?",
            (json.dumps("pi.tool.progress"),),
        ).fetchall()
        assert len(progress_rows) == 1 and progress_rows[0][0] == "base"
        raw_progress = _decode(progress_rows[0][1])
        records = [_decode(row[0]) for row in db.execute("SELECT record FROM tasks")]
        assert not any(
            (
                row["kind"] == "pi.tool" and row["state"]["status"] == "terminal"
                for row in records
            )
        )
    if fault == "backend_failed":
        assert raw_progress["output"] == ""
    elif fault == "backend_ambiguous":
        assert raw_progress["output"] == "first\n"
        assert observations and cut_once.is_set()
        with reporter._condition:
            assert reporter._acknowledged == 1
    if fault in ("bytes", "nodes", "busy", "permits"):
        assert raw_progress["details"] == {
            "present": True,
            "value": {"barrier": 15} if fault == "permits" else {"accepted": [1]},
        }
        if fault.startswith("busy"):
            assert snapshot_calls.count("capture") == 1
    release.set()
    if fault in ("bytes", "nodes", "busy", "permits", "timeout"):
        assert completed.wait(12), session.failure
    session.close(timeout=12)
    assert finalized.is_set() and (not scheduler._thread.is_alive())
    independent_opener(path, tmp_path, fault + "-released", "opened")
    with pytest.raises(ProcessLookupError):
        os.killpg(processes[0].pid, 0)
    assert (tmp_path / "effect").read_text() == "one\n"
    with sqlite3.connect(path) as db:
        records = [_decode(row[0]) for row in db.execute("SELECT record FROM tasks")]
        entries = [_decode(row[0]) for row in db.execute("SELECT record FROM entries")]
    child = next((row for row in records if row["kind"] == "pi.tool"))
    if fault in ("bytes", "nodes", "busy", "permits", "timeout"):
        assert child["state"]["outcome"]["status"] == (
            "completed" if fault == "timeout" else "failed"
        )
        assert len(wire.calls) == 2
    else:
        assert child["state"]["status"] != "terminal"
        assert not any((row["kind"] == "pi.tool-result" for row in entries))
        assert len(wire.calls) == 1
    (tmp_path / "fault-witness.json").write_text(
        json.dumps(
            {
                "fault": fault,
                "pid": processes[0].pid,
                "effect_count": 1,
                "epoch_at_cut": before,
                "ack_at_cut": ack,
                "backend_observations": observations,
                "actual_returncode": processes[0].returncode,
                "finalized": finalized.is_set(),
            }
        )
    )


def _matrix_wake_red_witness(
    *,
    finalizer,
    finalized,
    tmp_path,
    processes,
    path,
    reporters,
    wire,
    observations,
    returned,
    errors,
):
    assert finalizer.is_set() and (not finalized.is_set())
    assert int((tmp_path / "pid").read_text()) == processes[0].pid
    assert (tmp_path / "effect").read_text() == "one\n"
    independent_opener(
        path, tmp_path, "escaped-wake-before-test-finish", "refused", invoke_held=True
    )
    with reporters[0]._condition:
        gate_after = {
            "after_install_epoch": reporters[0]._epoch,
            "acknowledged": reporters[0]._acknowledged,
            "infrastructure_failed": reporters[0]._infrastructure_failed,
            "active_callbacks": reporters[0]._active_callbacks,
        }
    with sqlite3.connect(path) as db:
        revisions = db.execute(
            "SELECT r.content FROM document_revisions r JOIN documents d ON d.id=r.document_id WHERE d.kind=?",
            (json.dumps("pi.tool.progress"),),
        ).fetchall()
        saved = _decode(revisions[0][0])
        docs = [_decode(row[0]) for row in db.execute("SELECT record FROM documents")]
        entries = [_decode(row[0]) for row in db.execute("SELECT record FROM entries")]
    store_after = {
        "progress_revision_count": len(revisions),
        "saved_details": saved["details"],
        "saved_output": saved["output"],
        "receipt_document_count": sum(
            (row["kind"] == "pi.tool.receipt" for row in docs)
        ),
        "tool_result_entry_count": sum(
            (row["kind"] == "pi.tool-result" for row in entries)
        ),
        "provider_calls": len(wire.calls),
    }
    (tmp_path / "wake-precleanup-witness.json").write_text(
        json.dumps(
            {
                "before_install": observations,
                "gate_after_install": gate_after,
                "independent_store_after": store_after,
                "void_returned_normally": returned.is_set(),
                "actual_escaped_exception_types": [
                    type(error).__name__ for error in errors
                ],
                "actual_exception_frames": [
                    [
                        {
                            "file": frame.filename,
                            "line": frame.lineno,
                            "function": frame.name,
                        }
                        for frame in traceback.extract_tb(error.__traceback__)
                    ]
                    for error in errors
                ],
                "popen_pid": processes[0].pid,
                "popen_poll": processes[0].poll(),
                "effect": "one\n",
                "finalizer_entered": finalizer.is_set(),
                "finalizer_finished": finalized.is_set(),
                "test_finish_file_exists": (tmp_path / "finish").exists(),
                "lease_opener": "refused before any test finish/release",
            },
            indent=2,
        )
    )


def _matrix_cleanup(
    tmp_path,
    fault,
    returned,
    errors,
    finalizer,
    finalized,
    processes,
    capture_release,
    backend_release,
    release,
    session,
    barriers,
    sealers,
):
    (tmp_path / "pre-test-cleanup.json").write_text(
        json.dumps(
            {
                "fault": fault,
                "void_returned": returned.is_set(),
                "invoke_error_types": [type(error).__name__ for error in errors],
                "finalizer_entered": finalizer.is_set(),
                "finalized": finalized.is_set(),
                "child_states_before_test_cleanup": [
                    {"pid": process.pid, "poll": process.poll()}
                    for process in processes
                ],
            }
        )
    )
    capture_release.set()
    backend_release.set()
    (tmp_path / "again").touch()
    (tmp_path / "finish").touch()
    release.set()
    session.close(timeout=15)
    for process in processes:
        if process.poll() is None:
            (tmp_path / "test-forced-child-cleanup.json").write_text(
                json.dumps(
                    {
                        "pid": process.pid,
                        "alive_after_session_close": True,
                        "void_returned": returned.is_set(),
                        "invoke_errors": [type(error).__name__ for error in errors],
                    }
                )
            )
            os.killpg(process.pid, 9)
            process.wait(timeout=5)
    for worker in barriers + sealers:
        worker.join(5)
        assert not worker.is_alive()


def _bound_method(callback):
    def invoke(*args, **kwargs):
        return callback(*args, **kwargs)

    return invoke

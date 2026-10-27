"""Host writer ownership and prefix ACKs, separate from physical-child proof."""

import asyncio

import pytest

from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.tool_progress import (
    ProgressEnded,
    RetainedToolProgress,
)


def initial():
    return OwnedJson(
        {
            "version": 1,
            "call": {"kind": "model", "assistant": 1, "callId": "call:1"},
            "key": "key",
            "attempts": 1,
            "restarts": 0,
            "output": "",
            "details": {"present": False},
            "diagnostics": [],
            "droppedBytes": 0,
            "droppedLines": 0,
        }
    )


async def wait_for_preparation_release(reporter):
    """Backend entry precedes worker-finally; observe the actual workspace."""

    def released():
        with reporter._condition:
            assert reporter._condition.wait_for(lambda: not reporter._preparing, 5)
            # The explicit reporter still owns its physical barrier permit;
            # the backend remains held, so no prefix ACK can have happened.
            assert reporter._active_callbacks == 1
            assert reporter._epoch == 1 and reporter._acknowledged == 0

    await asyncio.to_thread(released)


def test_details_waits_for_commit_and_next_output_coalesces_without_lost_update():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        committed = []

        async def commit(value):
            entered.set()
            await release.wait()
            committed.append(value)

        reporter = RetainedToolProgress(initial(), commit, lambda: None)
        flusher = asyncio.create_task(reporter.flush())
        details = {"array": [1]}
        barrier = asyncio.create_task(
            asyncio.to_thread(reporter.report_details, details)
        )
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await wait_for_preparation_release(reporter)
            assert not barrier.done()
            reporter("after")
            release.set()
            await asyncio.wait_for(barrier, 5)
            details["array"].append(2)
            await asyncio.to_thread(reporter.deactivate)
            reporter.finish()
            await asyncio.wait_for(flusher, 5)
            value, failure = reporter.result()
            tree = value.thaw()
            assert failure is None
            assert tree["details"] == {"present": True, "value": {"array": [1]}}
            assert tree["output"] == "after"
            assert len(committed) == 2
        finally:
            release.set()
            await asyncio.gather(barrier, return_exceptions=True)
            reporter.finish()
            await flusher

    asyncio.run(run())


def test_failed_commit_never_acks_and_later_void_callback_adds_no_epoch():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def commit(value):
            entered.set()
            await release.wait()
            raise RuntimeError("private injected commit failure")

        reporter = RetainedToolProgress(initial(), commit, lambda: None)
        flusher = asyncio.create_task(reporter.flush())
        barrier = asyncio.create_task(asyncio.to_thread(reporter.report_details, [1]))
        await asyncio.wait_for(entered.wait(), 5)
        release.set()
        with pytest.raises(ProgressEnded):
            await asyncio.wait_for(barrier, 5)
        await flusher
        with reporter._condition:
            epoch = reporter._epoch
            assert reporter._acknowledged == 0 and epoch == 1
        reporter("refused after failure")
        with reporter._condition:
            assert reporter._epoch == epoch
            assert reporter._active_callbacks == 0
        await asyncio.to_thread(reporter.deactivate)
        reporter.finish()
        with pytest.raises(ProgressEnded):
            reporter.result()

    asyncio.run(run())


def test_single_preparation_refuses_before_capture_and_preserves_admitted_prefix():
    import threading

    async def run():
        committing, commit_release = asyncio.Event(), asyncio.Event()
        capturing, capture_release = threading.Event(), threading.Event()
        forbidden = []

        async def commit(value):
            committing.set()
            await commit_release.wait()

        reporter = RetainedToolProgress(initial(), commit, lambda: None)
        flusher = asyncio.create_task(reporter.flush())
        barrier = asyncio.create_task(asyncio.to_thread(reporter.report_details, [1]))
        worker = None
        try:
            await asyncio.wait_for(committing.wait(), 5)
            await wait_for_preparation_release(reporter)
            with reporter._condition:
                assert reporter._epoch == 1 and not reporter._preparing
                assert reporter._active_callbacks == 1
            assert not barrier.done()

            def held_capture():
                capturing.set()
                assert capture_release.wait(5)
                return "not admitted after capacity failure"

            worker = asyncio.create_task(
                asyncio.to_thread(reporter.dispatch, held_capture)
            )
            assert await asyncio.to_thread(capturing.wait, 5)
            reporter.dispatch(lambda: forbidden.append(True) or "forbidden")
            assert forbidden == []
            with reporter._condition:
                assert reporter._epoch == 1 and reporter._preparing
                assert reporter._domain_failure == "progress_capacity"
            capture_release.set()
            await asyncio.wait_for(worker, 5)
            commit_release.set()
            await asyncio.wait_for(barrier, 5)
            await asyncio.to_thread(reporter.deactivate)
            reporter.finish()
            await asyncio.wait_for(flusher, 5)
            value, failure = reporter.result()
            assert failure == "progress_capacity"
            assert value.thaw()["details"] == {"present": True, "value": [1]}
            with reporter._condition:
                assert reporter._epoch == reporter._acknowledged == 1
                assert reporter._active_callbacks == 0 and not reporter._preparing
        finally:
            capture_release.set()
            commit_release.set()
            await asyncio.gather(
                *(task for task in (worker, barrier) if task is not None),
                return_exceptions=True,
            )
            reporter.finish()
            await flusher

    asyncio.run(run())


def test_wrapper_partial_is_gone_before_strict_candidate_install(monkeypatch):
    import gc

    from pipy_harness.native.agent.content import ToolPartialResult
    from pipy_harness.native.extension_types import ToolResult
    from pipy_harness.native.extensions import tool_port

    created, reached = [], []
    actual_partial = tool_port.ToolPartialResult
    actual_install = RetainedToolProgress._install

    def partial(*args, **kwargs):
        value = actual_partial(*args, **kwargs)
        created.append(id(value))
        return value

    def install(self, candidate, **kwargs):
        assert created
        assert not any(
            type(obj) is ToolPartialResult and id(obj) in created
            for obj in gc.get_objects()
        )
        with self._condition:
            assert self._active_callbacks == 1 and self._preparing
            assert self._epoch == 0
        reached.append(True)
        return actual_install(self, candidate, **kwargs)

    monkeypatch.setattr(tool_port, "ToolPartialResult", partial)
    monkeypatch.setattr(RetainedToolProgress, "_install", install)

    async def run():
        committed = []

        async def commit(value):
            committed.append(value)

        reporter = RetainedToolProgress(initial(), commit, lambda: None)
        flusher = asyncio.create_task(reporter.flush())
        mutable = {"array": [1], "text": "\ud83d\ude00"}
        tool_port._partial_update(reporter)(
            ToolResult(content="partial", details=mutable)
        )
        assert reached == [True]
        mutable["array"].append(2)
        await asyncio.to_thread(reporter.deactivate)
        reporter.finish()
        await flusher
        value, failure = reporter.result()
        assert failure is None
        assert value.thaw()["details"] == {
            "present": True,
            "value": {"array": [1], "text": "😀"},
        }
        assert committed == [value]

    asyncio.run(run())


@pytest.mark.parametrize("field", ["message", "code"])
@pytest.mark.parametrize("text", ["\ud800", "\ud83d\ude00"])
def test_unrepresentable_diagnostic_refuses_before_epoch_or_ack(field, text):
    async def run():
        commits = []

        async def commit(value):
            commits.append(value)

        reporter = RetainedToolProgress(initial(), commit, lambda: None)
        flusher = asyncio.create_task(reporter.flush())
        values = {"severity": "warning", "message": "visible", "code": "code"}
        values[field] = text
        reporter.report_diagnostic(**values)
        with reporter._condition:
            assert reporter._epoch == reporter._acknowledged == 0
            assert reporter._domain_failure == "invalid_progress"
            assert not reporter._infrastructure_failed
        await asyncio.to_thread(reporter.deactivate)
        reporter.finish()
        await flusher
        assert commits == []
        value, failure = reporter.result()
        assert failure == "invalid_progress"
        assert value.thaw()["diagnostics"] == []

    asyncio.run(run())

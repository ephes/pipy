"""Queued public C1/C2 authority waits for the active resume's control lease."""

import asyncio

import pytest
from adjudication_sdk_support import disk_logical
from test_tool_adjudication import held_product
from tool_support import counts


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


async def caller_turn():
    ready = asyncio.Event()
    asyncio.get_running_loop().call_soon(ready.set)
    await ready.wait()


class ControlObserver:
    """Observe real lock admission/release; no replacement lock or fake Future."""

    def __init__(self, patch, product):
        self.product = product
        self.lock = product._control
        self.owner = None
        self.waiting = asyncio.Event()
        self.actual_waiting_tasks = []
        self.timeline = []
        self.body_calls = []
        actual_acquire = type(self.lock).acquire
        actual_release = type(self.lock).release
        actual_owned = product._owned

        async def acquire(lock):
            if lock is self.lock and lock.locked():
                self.actual_waiting_tasks.append(asyncio.current_task())
                self.waiting.set()
            return await actual_acquire(lock)

        def release(lock):
            if lock is self.lock and asyncio.current_task() is self.owner:
                try:
                    assert lock.locked() and not self.owner.done()
                    self.timeline.append("owner-release")
                finally:
                    # A failed observation must still forward real release;
                    # never strand the queue or turn a defect into a timeout.
                    actual_release(lock)
                return None
            return actual_release(lock)

        async def owned(body):
            async def entered_body():
                self.body_calls.append(asyncio.current_task())
                self.timeline.append("mutation-body")
                return await body()

            return await actual_owned(entered_body)

        patch.setattr(type(self.lock), "acquire", acquire)
        patch.setattr(type(self.lock), "release", release)
        patch.setattr(product, "_owned", owned)

    async def actually_queued(self):
        await asyncio.wait_for(self.waiting.wait(), 5)
        # Event.set is immediately followed by forwarding acquire. Since the
        # lock is held, the original implementation registers its real waiter
        # before this observer task resumes. Read only; never inject a waiter.
        assert self.lock.locked()
        assert self.lock._waiters is not None
        assert any(not waiter.done() for waiter in self.lock._waiters)
        assert self.actual_waiting_tasks
        assert all(
            task in self.product._operations for task in self.actual_waiting_tasks
        )
        assert all(not task.done() for task in self.actual_waiting_tasks)


def control(product, task_id, kind, nonce):
    if kind == "C1":
        return product.adjudicate_interrupted(task_id, nonce=nonce)
    assert kind == "C2"
    return product.authorize_retry(task_id, nonce=nonce)


@pytest.mark.parametrize("kind", ["C1", "C2"])
def test_active_resume_joins_owner_then_ordered_winner_changes_fresh_run(
    tmp_path, monkeypatch, kind
):
    """The same actual authority commits only after the owner's lease ends."""

    async def run():
        async with held_product(tmp_path) as (product, _, target):
            ready, release = asyncio.Event(), asyncio.Event()
            original_result = product._wait_result
            first_result = []
            held_once = False
            offered = []
            queued = None

            async def held_result(*args, **kwargs):
                nonlocal held_once
                value = await original_result(*args, **kwargs)
                if not held_once:
                    held_once = True
                    assert value.status == "held"
                    first_result.append(value)
                    ready.set()
                    await release.wait()
                return value

            try:
                with monkeypatch.context() as patch:
                    patch.setattr(product, "_wait_result", held_result)
                    observed = ControlObserver(patch, product)
                    first = asyncio.create_task(product.resume())
                    offered.append(first)
                    await asyncio.wait_for(ready.wait(), 5)
                    operation = product._current
                    observed.owner = operation.task
                    before = disk_logical(tmp_path / "product.sqlite")
                    effects = counts(tmp_path)
                    queued = asyncio.create_task(
                        control(product, target, kind, "b" * 32)
                    )
                    await observed.actually_queued()
                    join = asyncio.create_task(product.resume())
                    offered.append(join)
                    await caller_turn()
                    assert product._current is operation and not join.done()
                    assert not queued.done() and not observed.body_calls
                    assert disk_logical(tmp_path / "product.sqlite") == before
                    release.set()
                    one, two = await asyncio.gather(first, join)
                    assert one is two is first_result[0]
                    assert one.status == "held"
                    winner = await queued
                    assert len(observed.body_calls) == 1
                    assert observed.timeline == ["owner-release", "mutation-body"]
                    after_mutation = disk_logical(tmp_path / "product.sqlite")
                    assert after_mutation != before
                    assert counts(tmp_path) == effects
                    if kind == "C1":
                        assert winner.to_tree() == {
                            "task_id": target,
                            "decision": "interrupted",
                        }
                    else:
                        assert (
                            winner.source_task_id == target
                            and winner.new_task_id != target
                        )
                    assert await control(product, target, kind, "b" * 32) == winner
                    assert disk_logical(tmp_path / "product.sqlite") == after_mutation
                    fresh = await product.resume()
                    assert product._current is not operation
                    assert (
                        fresh.status == "terminal"
                        and fresh.inspection.outcome == "completed"
                    )
                    assert counts(tmp_path) == {
                        "calls": 2 if kind == "C1" else 3,
                        "applications": 2 if kind == "C1" else 3,
                        "prepares": 2 if kind == "C1" else 3,
                        "providers": 2,
                    }
            finally:
                release.set()
                await asyncio.gather(
                    *offered,
                    *([] if queued is None else [queued]),
                    return_exceptions=True,
                )

    asyncio.run(asyncio.wait_for(run(), 30))

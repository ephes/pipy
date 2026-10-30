"""Narrow forwarded observers over real Product owners and asyncio lock waiters."""

import asyncio
import json


async def caller_turn():
    ready = asyncio.Event()
    asyncio.get_running_loop().call_soon(ready.set)
    await ready.wait()


class DeferredOwnerRetirement(set):
    """Delay only one real _operations.discard observer, never Task wakeups."""

    def __init__(self, members):
        super().__init__(members)
        self.owner = None
        self.called = asyncio.Event()
        self.deferred = []
        self.released = False

    def discard(self, work):
        if work is self.owner and not self.released:
            self.deferred.append(work)
            self.called.set()
            return
        super().discard(work)

    def release(self):
        self.released = True
        for work in self.deferred:
            super().discard(work)


class ControlObserver:
    """Observe real lock admission/release; no replacement lock or fake Future."""

    def __init__(self, patch, product, *, failure):
        self.product = product
        self.lock = product._control
        self.owner = None
        self.failure = failure
        self.waiting = asyncio.Event()
        self.actual_waiting_tasks = []
        self.release_facts = []
        self.timeline = []
        actual_acquire = type(self.lock).acquire
        actual_release = type(self.lock).release
        actual_owned = product._owned
        self.body_calls = []

        async def acquire(lock):
            if lock is self.lock and lock.locked():
                self.actual_waiting_tasks.append(asyncio.current_task())
                self.waiting.set()
            return await actual_acquire(lock)

        def release(lock):
            if lock is self.lock and asyncio.current_task() is self.owner:
                operation = product._current
                expected = "continuation_failed" if self.failure else "success"
                try:
                    assert operation.task is self.owner
                    assert lock.locked() and not self.owner.done()
                    assert operation.outcome is not None
                    assert operation.outcome.kind == expected
                    self.release_facts.append(
                        {"outcome": expected, "locked": True, "owner_task_done": False}
                    )
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


def save_facts(root, name, value):
    (root / name).write_text(json.dumps(value, indent=2) + "\n")

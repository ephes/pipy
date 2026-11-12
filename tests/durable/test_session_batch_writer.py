"""R15-3: the Session writer over the release 1.5 batch ``Storage.commit``.

The Session sends one batch per call (bridge deviation D1) and judges the
call's outcome as Pi 1.5's writer does (bridge U07, U08): a batch stored before
its call raised is published and then fails the Session; a refusal or a result
without the batch's receipt fails it; a backend's optional ``publishing`` scope
runs the publication.
"""

from __future__ import annotations

from typing import Any

import pytest

from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.session import SessionFailed
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.storage import (
    CommitResult,
    StorageWrite,
    commit_writes,
)

WAIT = 10


class Scripted:
    """A MemoryStorage whose commit calls follow one scripted behavior each."""

    def __init__(self) -> None:
        self.inner = MemoryStorage()
        commit_writes(
            self.inner, (StorageWrite({"type": "conversation", "value": {"id": 1}}),)
        )
        self.script: list[str] = []
        self.batches: list[int] = []
        self.events: list[str] = []
        self.during: Any = None  # runs after the inner commit, inside the call

    def commit(self, batches: Any, receive: Any) -> CommitResult:
        self.batches.append(len(batches))
        action = self.script.pop(0) if self.script else "pass"
        if action == "raise-before":
            raise RuntimeError("connection lost")
        if action == "no-receipt":
            return self.inner.commit(batches, lambda index, seq: None)
        if action == "refuse":
            return CommitResult(0, RuntimeError("refused by Durable's checks"))
        result = self.inner.commit(batches, receive)
        if self.during is not None:
            self.during()
        if action == "raise-after":
            raise RuntimeError("acknowledgement lost")
        if action == "interrupt-after":
            raise KeyboardInterrupt
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class Scoped(Scripted):
    """A backend with a publication scope (Pi 1.5 ``Storage.publishing``)."""

    def __init__(self, mode: str = "run") -> None:
        super().__init__()
        self.mode = mode

    def publishing(self, publish: Any) -> None:
        self.events.append("scope-enter")
        if self.mode == "skip":
            return
        if self.mode == "raise":
            raise RuntimeError("scope broke")
        publish()
        self.events.append("scope-exit")


def session(backend: Any) -> tuple[Session, list[Any], list[Any]]:
    reports: list[Any] = []
    published: list[Any] = []
    owned = Session(
        backend,
        reporter=lambda error, info: reports.append((error, info)),
        now=lambda: 1000.0,
    )
    owned.subscribe_commits(lambda publication: published.append(publication.seq))
    return owned, reports, published


def note(tx: Any) -> Any:
    return tx.append_entry(1, {"kind": "note"})


def test_one_batch_per_call_and_the_receipt_seq_is_published():
    backend = Scripted()
    owned, reports, published = session(backend)
    owned.commit(note)
    owned.commit(note)
    assert backend.batches == [1, 1]
    assert published == [2, 3]
    owned.close(timeout=WAIT)
    assert reports == []


def test_a_batch_stored_before_its_call_raised_is_published_then_fails():
    backend = Scripted()
    backend.script = ["raise-after"]
    owned, reports, published = session(backend)
    with pytest.raises(RuntimeError, match="acknowledgement lost") as raised:
        owned.commit(note)
    assert published == [2]  # durable, so published before the failure
    end = owned.wait_closed(timeout=WAIT)
    assert end.reason == "failed" and end.error is raised.value
    assert [error for error, _ in reports] == [raised.value]
    with pytest.raises(SessionFailed):
        owned.commit(note)


@pytest.mark.parametrize(
    ("action", "message"),
    [
        ("raise-before", "connection lost"),
        ("refuse", "refused by Durable's checks"),
        ("no-receipt", "Storage committed without adopting the batch"),
    ],
)
def test_an_unreceived_batch_fails_the_session_and_publishes_nothing(action, message):
    backend = Scripted()
    backend.script = [action]
    owned, reports, published = session(backend)
    with pytest.raises(RuntimeError, match=message) as raised:
        owned.commit(note)
    assert published == []
    end = owned.wait_closed(timeout=WAIT)
    assert end.reason == "failed" and end.error is raised.value
    assert [error for error, _ in reports] == [raised.value]


def test_the_async_writer_judges_a_raise_after_the_receipt_the_same_way():
    import asyncio

    backend = Scripted()
    backend.script = ["raise-after"]
    owned, reports, published = session(backend)

    async def body() -> BaseException:
        with pytest.raises(RuntimeError) as raised:
            await owned.acommit(note)
        return raised.value

    error = asyncio.run(body())
    assert published == [2]
    assert owned.wait_closed(timeout=WAIT).error is error
    assert [e for e, _ in reports] == [error]


def test_a_publishing_scope_runs_the_publication_inside_it():
    backend = Scoped()
    owned, reports, published = session(backend)
    owned.subscribe_commits(lambda publication: backend.events.append("observer"))
    owned.commit(note)
    assert backend.events == ["scope-enter", "observer", "scope-exit"]
    assert published == [2]
    owned.close(timeout=WAIT)
    assert reports == []


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("skip", "Storage.publishing returned without the publication"),
        ("raise", "scope broke"),
    ],
)
def test_a_publishing_scope_that_skips_or_raises_fails_the_session(mode, message):
    backend = Scoped(mode)
    owned, reports, published = session(backend)
    with pytest.raises(RuntimeError, match=message) as raised:
        owned.commit(note)
    assert owned.wait_closed(timeout=WAIT).error is raised.value
    assert [error for error, _ in reports] == [raised.value]
    assert published == []


@pytest.mark.parametrize("action", ["pass", "raise-after", "interrupt-after"])
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_a_session_failed_while_the_call_ran_publishes_nothing(action, asynchronous):
    """Pi's writer: whatever the call returned, the first failure wins."""
    import asyncio

    backend = Scripted()
    backend.script = [action]
    owned, reports, published = session(backend)
    first = RuntimeError("failed meanwhile")
    backend.during = lambda: owned._owner.fail(first)

    async def acommit() -> None:
        await owned.acommit(note)

    def run() -> None:
        if asynchronous:
            asyncio.run(acommit())
        else:
            owned.commit(note)

    expected = KeyboardInterrupt if action == "interrupt-after" else SessionFailed
    with pytest.raises(expected) as raised:
        run()
    if expected is SessionFailed:
        assert raised.value.cause is first
    assert published == []  # stored, but never published
    assert owned.wait_closed(timeout=WAIT).error is first
    assert [error for error, _ in reports] == [first]


def test_a_process_control_raise_after_the_receipt_publishes_then_fails():
    backend = Scripted()
    backend.script = ["interrupt-after"]
    owned, reports, published = session(backend)
    with pytest.raises(KeyboardInterrupt) as raised:
        owned.commit(note)
    assert published == [2]
    assert owned.wait_closed(timeout=WAIT).error is raised.value


def test_an_adoption_failure_after_a_raised_call_fails_with_the_call_error(
    monkeypatch,
):
    """Pi's writer fails the Session with the storage error and rejects the
    commit with its own adoption error."""
    from pipy_harness.native.durable import tx

    backend = Scripted()
    backend.script = ["raise-after"]
    owned, reports, published = session(backend)
    bug = RuntimeError("adopt bug")

    def failing(self: Any, seq: int) -> Any:
        raise bug

    monkeypatch.setattr(tx._TxCore, "adopt", failing)
    with pytest.raises(RuntimeError) as raised:
        owned.commit(note)
    assert raised.value is bug
    end = owned.wait_closed(timeout=WAIT)
    assert str(end.error) == "acknowledgement lost"
    assert [str(error) for error, _ in reports] == ["acknowledgement lost"]
    assert published == []

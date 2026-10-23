"""Driving-task authority and exceptional owned handoff on the actual line."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest
from test_session_commit import Store, entered
from test_session_documents import seed, task

from pipy_harness.native.durable.documents import define_doc
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.session import SessionFailed, _LineTicket
from pipy_harness.native.durable.session_core import (
    ReentrantSessionCall,
    Session,
    _AsyncDriver,
    _driving,
)
from pipy_harness.native.durable.tx import _Io

WAIT = 5


def probe(body, expected=None):
    """Execute an async prologue in THIS Task; detach even a broken admission."""
    expected = ReentrantSessionCall if expected is None else expected
    try:
        with pytest.raises(expected):
            body.send(None)
    finally:
        try:
            body.throw(asyncio.CancelledError())
        except (asyncio.CancelledError, StopIteration, RuntimeError):
            pass
        body.close()


def test_cold_initializer_recognizes_driving_task_and_independent_task_queues():
    async def run():
        session = Session(MemoryStorage())
        events, independent = [], []

        def initial():
            holder = session._owner._line_holder()
            assert holder.actor is asyncio.current_task()
            events.append("initializer")
            with pytest.raises(ReentrantSessionCall):
                session.commit(lambda _tx: None)
            probe(session.acommit(lambda _tx: None))
            probe(session.aread_on_line(lambda: None))
            probe(session.asnapshot(other))
            probe(session.aclose())
            assert not session._owner._line_sealed
            independent.append(
                asyncio.create_task(
                    session.aread_on_line(lambda: events.append("queued"))
                )
            )
            return {"n": 0}

        spec = define_doc("authority.initial", 1, "session", initial)
        other = define_doc("authority.missing", 1, "session", dict)
        try:
            async with asyncio.timeout(WAIT):
                await session.acommit(lambda tx: tx.doc(spec))
                await independent[0]
            assert events == ["initializer", "queued"]
            assert session._owner._line_holder() is None
        finally:
            await session.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("error", [ValueError("ordinary"), BaseException("control")])
def test_driver_scope_restores_on_resumed_send_throw_and_nested_advance(error):
    async def run():
        loop = asyncio.get_running_loop()
        ticket = _LineTicket(lambda: None)
        previous = object()
        ticket.actor = previous
        driver = _AsyncDriver(loop, lambda: _driving(ticket))
        seen = []

        async def immediate():
            assert ticket.actor is asyncio.current_task()
            return 7

        def failed_io():
            raise error

        async def body():
            assert ticket.actor is asyncio.current_task()
            assert driver.spawn(immediate()).result() == 7
            assert ticket.actor is asyncio.current_task()
            await _Io(lambda: 1)
            seen.append("send")
            assert ticket.actor is asyncio.current_task()
            try:
                await _Io(failed_io)
            except BaseException as caught:
                assert caught is error
                seen.append("throw")
                assert ticket.actor is asyncio.current_task()
                raise

        handle = driver.spawn(body())
        assert ticket.actor is previous
        with pytest.raises(type(error)) as caught:
            async with asyncio.timeout(WAIT):
                await handle
        assert caught.value is error
        assert ticket.actor is previous
        assert seen == ["send", "throw"]

    asyncio.run(run())


@pytest.mark.parametrize("control", [False, True])
def test_task_factory_failure_after_backend_submission_drains_before_leaving(control):
    async def run():
        store = Store(MemoryStorage())
        session = Session(store)
        gate = store.hold("find_document")
        error = BaseException("factory control") if control else ValueError("factory")
        spec = define_doc("factory.failure", 1, "session", dict)
        loop = asyncio.get_running_loop()

        def factory(_loop, body, **_kwargs):
            raise error

        async def commit():
            loop.set_task_factory(factory)
            try:
                await session.acommit(lambda tx: tx.doc(spec))
            finally:
                loop.set_task_factory(None)

        owner = asyncio.create_task(commit())
        try:
            await entered(gate)
            assert session._owner._line_holder() is not None
            assert not owner.done()
            assert session._owner._line_holder().actor is None
            gate.release.set()
            with pytest.raises(type(error)) as caught:
                async with asyncio.timeout(WAIT):
                    await owner
            assert caught.value is error
            assert session._owner._line_holder() is None
            assert store.count("commit") == 0
            assert session.failure is None
            await session.acommit(lambda tx: tx.doc(spec))
        finally:
            loop.set_task_factory(None)
            gate.release.set()
            if not owner.done():
                await owner
            await session.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("eager", [False, True])
def test_settlement_identity_precedes_publication_even_with_eager_factory(eager):
    if eager and not hasattr(asyncio, "eager_task_factory"):
        pytest.skip("Python 3.11 has no eager task factory")

    async def run():
        loop = asyncio.get_running_loop()
        session = Session(MemoryStorage())
        spec = define_doc("factory.eager", 1, "session", lambda: {"n": 0})
        seen = []

        def listener(_publication):
            holder = session._owner._line_holder()
            assert any(task is asyncio.current_task() for task in holder.tasks)
            probe(session.aread_on_line(lambda: None))
            seen.append("publication")

        session.observe_commits(listener)
        try:
            if eager:
                loop.set_task_factory(asyncio.eager_task_factory)
            async with asyncio.timeout(WAIT):
                await session.acommit(lambda tx: tx.doc(spec))
            assert seen == ["publication"]
            assert session.failure is None
        finally:
            loop.set_task_factory(None)
            await session.aclose()

    asyncio.run(run())


def test_settlement_factory_failure_aborts_prepared_views_and_recovers():
    async def run():
        loop = asyncio.get_running_loop()
        store = Store(MemoryStorage())
        session = Session(store)
        spec = define_doc("factory.prepared", 1, "session", lambda: {"n": 0})
        escaped = []
        error = ValueError("settlement factory")

        def factory(loop, body, **kwargs):
            if body.cr_code.co_name == "_settle_async":
                raise error
            return asyncio.Task(body, loop=loop, **kwargs)

        async def edit(tx):
            draft = await tx.doc(spec)
            draft["n"] = 1
            escaped.append(draft)

        try:
            await session.acommit(lambda tx: tx.doc(spec))
            old = await session.asnapshot(spec)
            loop.set_task_factory(factory)
            with pytest.raises(ValueError) as caught:
                async with asyncio.timeout(WAIT):
                    await session.acommit(edit)
            assert caught.value is error
            assert session._owner._line_holder() is None
            assert session.failure is None
            from pipy_harness.native.durable.tx import DraftRevoked

            with pytest.raises(DraftRevoked):
                escaped[0]["n"] = 3
            assert await session.asnapshot(spec) is old
            assert store.count("commit") == 1
            loop.set_task_factory(None)
            await session.acommit(edit)
        finally:
            loop.set_task_factory(None)
            await session.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("eager", [False, True])
def test_actual_policy_reentry_authority_has_no_inherited_child_authority(
    resumed, eager, witness=None
):
    if eager and not hasattr(asyncio, "eager_task_factory"):
        pytest.skip("Python 3.11 has no eager task factory")

    async def run():
        loop = asyncio.get_running_loop()
        store = Store(MemoryStorage())
        seed(
            store.inner,
            {"type": "conversation", "value": {"id": 7}},
            {"type": "task", "value": task()},
        )
        session = Session(store)
        events, children, actors = [], [], []
        old = None
        other = define_doc("policy.missing", 1, "session", dict)

        def policy(_value, _ops, _info):
            if witness is not None:
                witness.append("policy")
            holder = session._owner._line_holder()
            actors.append((holder, asyncio.current_task()))
            assert holder.actor is asyncio.current_task()
            assert session.snapshot(spec) is old
            for fn in (
                lambda: session.commit(lambda _tx: None),
                lambda: session.read_on_line(lambda: None),
                lambda: session.snapshot(other),
                session.unload_documents,
                session.close,
                session.wait_closed,
            ):
                with pytest.raises(ReentrantSessionCall):
                    fn()
            for body in (
                session.acommit(lambda _tx: None),
                session.aread_on_line(lambda: None),
                session.asnapshot(other),
                session.aunload_documents(),
                session.aclose(),
                session.await_closed(),
            ):
                probe(body)
            assert not session._owner._line_sealed
            children.append(
                asyncio.create_task(
                    session.aread_on_line(lambda: events.append("child"))
                )
            )
            events.append("policy")
            return True

        spec = define_doc(
            "policy.authority", 1, "session", lambda: {"n": 0}, checkpoint_when=policy
        )

        async def edit(tx):
            (await tx.doc(spec))["n"] = 1
            if resumed:
                tx.set_task(task(terminal=True))

        try:
            await session.acommit(lambda tx: tx.doc(spec))
            old = await session.asnapshot(spec)
            session.observe_commits(lambda _: events.append("publication"))
            if eager:
                loop.set_task_factory(asyncio.eager_task_factory)
            async with asyncio.timeout(WAIT):
                await session.acommit(edit)
                await children[0]
            assert events == ["policy", "publication", "child"]
            assert len(actors) == 1 and actors[0][0].actor is None
            assert session.failure is None
        finally:
            loop.set_task_factory(None)
            await session.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("action", ["close", "fail", "caught-read"])
def test_post_policy_health_blocks_failed_owner_but_request_close_finishes(  # noqa: C901 - three callback health outcomes
    resumed, action, witness=None
):
    async def run():  # noqa: C901 - health, admitted-close and failed-read paths
        store = Store(MemoryStorage())
        seed(
            store.inner,
            {"type": "conversation", "value": {"id": 7}},
            {"type": "task", "value": task()},
        )
        session = Session(store)
        error = ValueError("policy health")
        seen = []

        def first(*_args):
            if witness is not None:
                witness.append("policy")
            seen.append("first")
            if action == "close":
                session.request_close()
            elif action == "fail":
                session.fail(error)
            else:
                store.fault("conversation", error)
                with pytest.raises(ValueError) as caught:
                    session._owner.storage.conversation(7)
                assert caught.value is error
            return True

        def second(*_args):
            seen.append("second")
            return False

        specs = [
            define_doc(f"health.{i}", 1, "session", lambda: {"n": 0}, checkpoint_when=p)
            for i, p in enumerate([first, second])
        ]

        async def create(tx):
            for spec in specs:
                await tx.doc(spec)

        async def edit(tx):
            for spec in specs:
                (await tx.doc(spec))["n"] = 1
            if resumed:
                tx.set_task(task(terminal=True))

        try:
            await session.acommit(create)
            before = store.count("commit")
            if action == "close":
                async with asyncio.timeout(WAIT):
                    await session.acommit(edit)
                assert store.count("commit") == before + 1
                assert seen == ["first", "second"]
            else:
                with pytest.raises(SessionFailed) as caught:
                    async with asyncio.timeout(WAIT):
                        await session.acommit(edit)
                assert caught.value.__cause__ is error
                assert session.failure is error
                assert store.count("commit") == before
                assert seen == ["first"]
            async with asyncio.timeout(WAIT):
                await session.await_closed()
        finally:
            await session.aclose()

    asyncio.run(run())


def test_resumed_preparation_handoff_failure_drains_and_never_calls_policy():
    async def run():
        loop = asyncio.get_running_loop()
        store = Store(MemoryStorage())
        seed(
            store.inner,
            {"type": "conversation", "value": {"id": 7}},
            {"type": "task", "value": task()},
        )
        session = Session(store)
        calls, escaped = [], []
        spec = define_doc(
            "handoff.resume",
            1,
            "session",
            lambda: {"n": 0},
            checkpoint_when=lambda *_: calls.append("policy") or True,
        )
        error = ValueError("settlement creation fails persistently")

        def factory(loop, body, **kwargs):
            if body.cr_code.co_name == "_settle_async":
                raise error
            return asyncio.Task(body, loop=loop, **kwargs)

        async def edit(tx):
            draft = await tx.doc(spec)
            escaped.append(draft)
            draft["n"] = 1
            tx.set_task(task(terminal=True))

        await session.acommit(lambda tx: tx.doc(spec))
        old = await session.asnapshot(spec)
        gate = store.hold("scan_documents")
        loop.set_task_factory(factory)
        owner = asyncio.create_task(session.acommit(edit))
        try:
            await entered(gate)
            assert calls == [] and not owner.done()
            assert session._owner._line_holder().actor is None
            gate.release.set()
            with pytest.raises(ValueError) as caught:
                async with asyncio.timeout(WAIT):
                    await owner
            assert caught.value is error and calls == []
            assert session._owner._line_holder() is None
            assert await session.asnapshot(spec) is old
            assert store.count("commit") == 1
            from pipy_harness.native.durable.tx import DraftRevoked

            with pytest.raises(DraftRevoked):
                escaped[0]["n"] = 3
        finally:
            loop.set_task_factory(None)
            gate.release.set()
            if not owner.done():
                await owner
            await session.aclose()

    asyncio.run(run())


def test_caught_factory_error_cannot_admit_while_submitted_io_is_pending():
    async def run():
        loop = asyncio.get_running_loop()
        store = Store(MemoryStorage())
        session = Session(store)
        gate = store.hold("find_document")
        spec = define_doc("caught.factory", 1, "session", dict)
        error = ValueError("helper factory")
        caught = []

        def factory(loop, body, **kwargs):
            if body.cr_code.co_name == "_resume":
                raise error
            return asyncio.Task(body, loop=loop, **kwargs)

        async def edit(tx):
            loop.set_task_factory(factory)
            try:
                await tx.doc(spec)
            except ValueError as failure:
                caught.append(failure)

        owner = asyncio.create_task(session.acommit(edit))
        try:
            await entered(gate)
            assert caught == [] and not owner.done()
            assert session._owner._line_holder() is not None
            gate.release.set()
            async with asyncio.timeout(WAIT):
                await owner
            assert caught == [error]
            assert store.count("mint_id") == 0 and store.count("commit") == 0
            assert session._owner._line_holder() is None
            loop.set_task_factory(None)
            await session.acommit(lambda tx: tx.doc(spec))
        finally:
            loop.set_task_factory(None)
            gate.release.set()
            await session.aclose()

    asyncio.run(run())


def test_executor_submission_failure_closes_body_and_restores_run_scope(monkeypatch):
    async def run():
        loop = asyncio.get_running_loop()
        ticket = _LineTicket(lambda: None)
        previous = object()
        ticket.actor = previous
        driver = _AsyncDriver(loop, lambda: _driving(ticket))
        closed, ran = [], []
        error = ValueError("executor rejected")

        async def body():
            try:
                assert ticket.actor is asyncio.current_task()
                await _Io(lambda: ran.append("backend"))
            finally:
                closed.append("body")

        def refused(*_args):
            raise error

        with monkeypatch.context() as patch:
            patch.setattr(loop, "run_in_executor", refused)
            for handle in (driver.spawn(body()), driver.run(body())):
                with pytest.raises(ValueError) as caught:
                    await handle
                assert caught.value is error
                assert ticket.actor is previous
        assert closed == ["body", "body"] and ran == [] and driver.pending() == ()

    asyncio.run(run())


@pytest.mark.parametrize("control", [False, True])
def test_eager_completed_settlement_then_factory_error_leaves_exactly_once(control):
    async def run():
        loop = asyncio.get_running_loop()
        session = Session(MemoryStorage())
        error = (
            BaseException("eager factory control")
            if control
            else ValueError("eager factory")
        )
        reached = []

        def factory(loop, body, **kwargs):
            if body.cr_code.co_name != "_settle_async":
                return asyncio.Task(body, loop=loop, **kwargs)
            try:
                body.send(None)
            except StopIteration:
                reached.append("settled")
            else:
                raise AssertionError("empty settlement suspended")
            raise error

        try:
            loop.set_task_factory(factory)
            with pytest.raises(type(error)) as caught:
                async with asyncio.timeout(WAIT):
                    await session.acommit(lambda _tx: None)
            assert caught.value is error and reached == ["settled"]
            assert session._owner._line_holder() is None and session.failure is None
            loop.set_task_factory(None)
            await session.acommit(lambda _tx: None)
        finally:
            loop.set_task_factory(None)
            await session.aclose()

    asyncio.run(run())


def core_mutant(name, old, new, *, tx=False):
    """Only owned source bytes; no checkout imports or historical native code."""
    from pipy_harness.native.durable import session_core as real
    from pipy_harness.native.durable import tx as real_tx

    package = real.__package__
    target = real_tx if tx else real
    source = Path(target.__file__).read_text()
    assert source.count(old) == 1, (name, old)
    source = source.replace(old, new)
    own_name = package + "._checkpoint_mutant_" + name
    own = types.ModuleType(own_name)
    own.__package__ = package
    own.__file__ = target.__file__
    sys.modules[own_name] = own
    try:
        exec(compile(source, "<checkpoint mutant " + name + ">", "exec"), own.__dict__)
        if not tx:
            return own
        core_name = own_name + "_core"
        core = types.ModuleType(core_name)
        core.__package__ = package
        core.__file__ = real.__file__
        sys.modules[core_name] = core
        sys.modules[real_tx.__name__] = own
        try:
            exec(
                compile(
                    Path(real.__file__).read_text(), "<checkpoint mutant core>", "exec"
                ),
                core.__dict__,
            )
        finally:
            sys.modules[real_tx.__name__] = real_tx
            del sys.modules[core_name]
        return core
    finally:
        del sys.modules[own_name]


@pytest.mark.parametrize("name", ["resume-scope", "restore", "inherit", "health"])
def test_authority_production_mutants_reach_actual_policy_and_fail(name, monkeypatch):
    if name == "inherit" and not hasattr(asyncio, "eager_task_factory"):
        pytest.skip("Inherited child authority needs an eager task factory witness")
    mutations = {
        "resume-scope": (
            "                    with self._scope():",
            "                    with nullcontext():",
        ),
        "restore": (
            "        ticket.actor = previous",
            "        ticket.actor = _current_task()",
        ),
        "inherit": (
            "            current is holder.actor or any(task is current for task in holder.tasks)",
            "            holder.actor is not None or any(task is current for task in holder.tasks)",
        ),
        "health": (
            "            if self._healthy is not None:\n                self._healthy()",
            "            if self._healthy is not None:\n                pass",
        ),
    }
    witness = []
    if name == "health":
        test_post_policy_health_blocks_failed_owner_but_request_close_finishes(
            True, "fail", witness
        )
    else:
        test_actual_policy_reentry_authority_has_no_inherited_child_authority(
            True, name == "inherit", witness
        )
    assert witness == ["policy"]
    changed = core_mutant(name.replace("-", "_"), *mutations[name], tx=name == "health")
    witness.clear()
    with monkeypatch.context() as patch:
        patch.setattr(sys.modules[__name__], "Session", changed.Session)
        patch.setattr(
            sys.modules[__name__], "ReentrantSessionCall", changed.ReentrantSessionCall
        )
        with pytest.raises((AssertionError, RuntimeError, pytest.fail.Exception)):
            if name == "health":
                test_post_policy_health_blocks_failed_owner_but_request_close_finishes(
                    True, "fail", witness
                )
            else:
                test_actual_policy_reentry_authority_has_no_inherited_child_authority(
                    True, name == "inherit", witness
                )
    assert witness == ["policy"]

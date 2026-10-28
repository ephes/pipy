"""Private generation coherence and retained built-in authority boundaries."""

import asyncio

import pytest
from test_task_runtime import abort
from test_task_runtime import rig as rig

from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session import SessionClosedError
from pipy_harness.native.durable.task_values import identity
from pipy_harness.native.durable.tasks import define_task


async def never(*args):
    raise AssertionError("Publication must not invoke task code")


def declaration(name, version=1):
    return define_task(
        name, version, lambda _: {"phase": "work"}, {"work": never}, abort
    )


def generation(scheduler):
    with scheduler._guard:
        return scheduler._definition_generation


def test_atomic_snapshot_order_identity_and_no_durable_writes(rig):
    session, store = rig
    a, b = declaration("a"), declaration("b")
    scheduler = TaskScheduler(session, [a, b])
    scheduler.open()
    old = generation(scheduler)
    before = len(store.commits)
    assert scheduler.install_definition(a) is False
    assert generation(scheduler) is old
    newer = declaration("a", 2)
    assert scheduler.install_definition(newer) is True
    current = generation(scheduler)
    assert tuple(current.bindings) == (identity("a"), identity("b"))
    assert current.bindings[identity("b")] is old.bindings[identity("b")]
    assert old.support.definition("a")["version"] == 1
    assert current.support.definition("a")["version"] == 2
    assert (
        current.definitions[identity("a")] is current.bindings[identity("a")].captured
    )
    assert scheduler.remove_definition("missing") is False
    assert generation(scheduler) is current
    assert scheduler.remove_definition("a") is True
    assert scheduler.install_definition(newer) is True
    assert tuple(generation(scheduler).bindings) == (identity("b"), identity("a"))
    assert len(store.commits) == before
    with scheduler._guard:
        assert not scheduler._enabled
    with pytest.raises(TypeError):
        current.bindings[identity("c")] = old.bindings[identity("a")]


@pytest.mark.parametrize("name", ["pi.generation", "pi.tool", "pi.compaction"])
def test_reserved_publication_refuses_without_changing_constructor_authority(rig, name):
    session, store = rig
    task = declaration(name)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    old = generation(scheduler)
    before = len(store.commits)
    for call in (
        lambda: scheduler.install_definition(task),
        lambda: scheduler.remove_definition(name),
    ):
        with pytest.raises(ValueError, match="Built-in"):
            call()
    assert generation(scheduler) is old
    assert len(store.commits) == before


def test_known_source_reflection_cannot_replace_owned_callback(rig):
    session, store = rig
    task = declaration("known")
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    old = generation(scheduler)
    object.__setattr__(task, "version", 2)
    with pytest.raises(ValueError, match="Known task declaration"):
        scheduler.install_definition(task)
    assert generation(scheduler) is old
    assert old.definitions[identity("known")].version == 1
    assert scheduler.install_definition(declaration("known", 2)) is True


def test_async_controls_keep_private_snapshot_and_paused_state(rig):
    session, store = rig
    scheduler = TaskScheduler(session, [])
    scheduler.open()
    task = declaration("async")

    async def use():
        assert await scheduler.ainstall_definition(task) is True
        assert await scheduler.ainstall_definition(task) is False
        assert await scheduler.aremove_definition("absent") is False
        assert await scheduler.aremove_definition("async") is True

    before = len(store.commits)
    asyncio.run(use())
    assert not generation(scheduler).bindings
    assert len(store.commits) == before


def test_known_callback_comparison_never_uses_arbitrary_equality(rig):
    session, store = rig

    class Callback:
        def __eq__(self, other):
            raise AssertionError("Callback equality is not declaration authority")

        def __call__(self, *args):
            raise AssertionError("Publication must not invoke callbacks")

    callback = Callback()
    task = define_task(
        "callbacks", 1, callback, {"one": callback}, callback, migrate=callback
    )
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    old = generation(scheduler)
    assert scheduler.install_definition(task) is False
    assert generation(scheduler) is old
    object.__setattr__(task, "abort", Callback())
    with pytest.raises(ValueError, match="Known task declaration"):
        scheduler.install_definition(task)
    assert generation(scheduler) is old


def test_closed_health_precedes_valid_reflected_known_source_but_not_invalid_capture(
    rig,
):
    session, store = rig
    task = declaration("closed")
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    old = generation(scheduler)
    object.__setattr__(task, "version", 2)
    session.close(timeout=5)
    with pytest.raises(SessionClosedError):
        scheduler.install_definition(task)
    with pytest.raises(TypeError):
        scheduler.install_definition(object())
    with pytest.raises(ValueError, match="Built-in"):
        scheduler.remove_definition("pi.tool")
    assert generation(scheduler) is old
    assert not scheduler._thread.is_alive()


def test_sync_off_loop_refusal_precedes_invalid_input(rig):
    session, store = rig
    scheduler = TaskScheduler(session, [])
    scheduler.open()

    async def misuse():
        with pytest.raises(RuntimeError, match="ainstall_definition"):
            scheduler.install_definition(object())
        with pytest.raises(RuntimeError, match="aremove_definition"):
            scheduler.remove_definition(None)

    asyncio.run(misuse())
    assert not generation(scheduler).bindings

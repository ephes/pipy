"""DE05a definitions and real typed creation; execution remains DE05b."""

from __future__ import annotations

import asyncio
import inspect
import math
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest
from task_support import unexecuted
from test_session_commit import ASYNC, SYNC

from pipy_harness.native.durable.codec import JsonValueError, OwnedJson
from pipy_harness.native.durable.documents import define_doc
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.session import SessionFailed
from pipy_harness.native.durable.session_core import ReentrantSessionCall, Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import DocumentAddress
from pipy_harness.native.durable.tasks import define_task


class Recording:
    def __init__(self, inner):
        self.inner = inner
        self.calls = []

    def mint_id(self):
        self.calls.append("mint")
        return self.inner.mint_id()

    def conversation(self, id):
        self.calls.append("conversation")
        return self.inner.conversation(id)

    def commit(self, writes):
        self.calls.append("commit")
        return self.inner.commit(writes)

    def __getattr__(self, name):
        return getattr(self.inner, name)


@pytest.fixture(params=["memory", "sqlite", "reopen"])
def rig(request, tmp_path):
    path = tmp_path / "typed.sqlite"
    backend = request.param
    store = Recording(
        MemoryStorage()
        if backend == "memory"
        else open_sqlite_storage(str(path), mode="create")
    )
    session = Session(store)
    publications = []
    session.subscribe_commits(publications.append)
    scope = SimpleNamespace(
        session=session, store=store, publications=publications, expected=[]
    )
    yield scope
    session.close(timeout=10)
    if backend == "reopen":
        reopened = open_sqlite_storage(str(path), mode="open")
        try:
            for row in scope.expected:
                assert OwnedJson(reopened.task(row["id"]).to_tree()) == OwnedJson(row)
        finally:
            reopened.close()


@pytest.fixture(params=[SYNC, ASYNC], ids=["sync", "async"])
def mode(request):
    return request.param


def token(initial=lambda value: {"phase": "start", "value": value}, **options):
    return define_task(
        options.get("name", "work"),
        options.get("version", 1),
        initial,
        {"start": unexecuted},
        unexecuted,
    )


def commit(mode, rig, change):
    async def run():
        return await mode.commit(rig, change)

    return mode.run(run)


def test_definition_freezes_mapping_and_callbacks():
    phases = {"start": unexecuted}

    def initial(value):
        return value

    definition = define_task("work", -1.5, initial, phases, unexecuted)
    phases["start"] = lambda: None
    phases["other"] = unexecuted
    assert dict(definition.phases) == {"start": unexecuted}
    assert definition.initial is initial and definition.abort is unexecuted
    with pytest.raises(TypeError):
        definition.phases["start"] = unexecuted
    with pytest.raises(FrozenInstanceError):
        definition.name = "changed"


@pytest.mark.parametrize("version", [0, -1, 1.5, -0.0, 1.7976931348623157e308, 2**53])
def test_finite_numeric_definition_versions(version):
    definition = token(version=version)
    assert OwnedJson(definition.version) == OwnedJson(version)


@pytest.mark.parametrize(
    "version", [True, "1", None, float("inf"), float("nan"), 2**53 + 1]
)
def test_invalid_versions_reject(version):
    with pytest.raises((TypeError, JsonValueError)):
        token(version=version)


@pytest.mark.parametrize(
    "change",
    [
        "name",
        "initial",
        "abort",
        "phases",
        "phase-key",
        "phase-handler",
        "duplicate-utf16",
    ],
)
def test_definition_exact_fields(change):
    values = dict(
        name="work",
        version=1,
        initial=lambda value: value,
        phases={"start": unexecuted},
        abort=unexecuted,
    )
    if change in {"name", "initial", "abort", "phases"}:
        values[change] = object()
    elif change == "phase-key":
        values["phases"] = {1: unexecuted}
    elif change == "phase-handler":
        values["phases"] = {"start": None}
    else:
        values["phases"] = {"\U00010000": unexecuted, "\ud800\udc00": unexecuted}
    with pytest.raises((TypeError, JsonValueError)):
        define_task(**values)


@pytest.mark.parametrize("version", [0, -1, 1.5, -0.0, 1.7976931348623157e308])
def test_actual_creation_owns_mutated_input_and_checkpoint(mode, rig, version):
    caller = {"items": [1], "zero": -0.0}
    escaped = []

    def initial(value):
        rig.store.calls.append("initial")
        assert value is not caller and value["items"] is not caller["items"]
        value["items"].append(2)
        checkpoint = {"phase": "unregistered", "value": value}
        escaped.append((value, checkpoint))
        return checkpoint

    async def change(tx):
        conversation = (await tx.create_conversation({"kind": "ownerless"})).id
        return await tx.create_task(
            token(initial, version=version),
            caller,
            ownership={"kind": "conversation"},
            conversation_id=conversation,
        )

    id = commit(mode, rig, change)
    record = rig.store.task(id).to_tree()
    assert record["state"]["status"] == "pending"
    assert record["input"]["items"] == [1, 2]
    assert record["state"]["checkpoint"]["phase"] == "unregistered"
    assert OwnedJson(record["version"]) == OwnedJson(version)
    assert math.copysign(1, record["input"]["zero"]) == -1
    assert caller["items"] == [1]
    assert rig.store.calls[-3:] == ["initial", "mint", "commit"]
    escaped[0][0]["items"].append(3)
    escaped[0][1]["phase"] = "changed"
    caller["items"].append(4)
    assert rig.store.task(id).to_tree() == record
    assert len(rig.publications) == 1
    rig.expected = [record]


@pytest.mark.parametrize(
    "bad_owner",
    ["missing-task", "background-child", "wrong-conversation", "missing-conversation"],
)
def test_owner_and_conversation_checks_precede_initializer_and_mint(
    mode, rig, bad_owner
):
    async def seed(tx):
        conversation = (await tx.create_conversation({"kind": "ownerless"})).id
        task = await tx.create_task(
            token(),
            None,
            ownership={"kind": "conversation"},
            conversation_id=conversation,
        )
        return conversation, task

    conversation, parent = commit(mode, rig, seed)
    calls = []
    definition = token(lambda value: calls.append(value))
    ownership = (
        {"kind": "conversation"}
        if bad_owner == "missing-conversation"
        else {"kind": "task", "taskId": 999 if bad_owner == "missing-task" else parent}
    )
    options = (
        {"conversation_id": 999}
        if bad_owner in {"wrong-conversation", "missing-conversation"}
        else {}
    )
    if bad_owner == "background-child":
        options["background"] = True
    before = rig.store.calls.count("mint")

    async def change(tx):
        return await tx.create_task(definition, None, ownership=ownership, **options)

    with pytest.raises((RuntimeError, TypeError)):
        commit(mode, rig, change)
    assert not calls and rig.store.calls.count("mint") == before
    assert rig.session.failure is None
    rig.expected = [rig.store.task(parent).to_tree()]


@pytest.mark.parametrize(
    "result", ["throw", "invalid", "input-invalid", "coroutine", "foreign-awaitable"]
)
def test_initializer_failures_abort_documents_without_mint_or_foreign_cleanup(
    mode, rig, result
):
    touched = []
    foreign = type(
        "Foreign",
        (),
        {
            "__await__": lambda self: (_ for _ in ()),
            "close": lambda self: touched.append("close"),
            "__bool__": lambda self: touched.append("bool"),
        },
    )()
    saved = []

    async def coroutine():
        touched.append("ran")

    def initial(value):
        if result == "throw":
            raise ValueError("initializer")
        if result == "input-invalid":
            value["bad"] = object()
            return None
        if result == "coroutine":
            saved.append(coroutine())
            return saved[-1]
        return foreign if result == "foreign-awaitable" else object()

    async def seed(tx):
        return await tx.create_conversation({"kind": "ownerless"})

    conversation = commit(mode, rig, seed)
    spec = define_doc(
        "rollback",
        1,
        "session",
        lambda: {"n": 0},
        checkpoint_when=lambda _value, _ops, _info: False,
    )
    before = rig.store.calls.count("mint")

    async def change(tx):
        (await tx.doc(spec))["n"] = 1
        # Creation of the document has minted; count the task boundary separately.
        at = rig.store.calls.count("mint")
        try:
            await tx.create_task(
                token(initial),
                {},
                ownership={"kind": "conversation"},
                conversation_id=conversation.id,
            )
        finally:
            assert rig.store.calls.count("mint") == at

    with pytest.raises((ValueError, TypeError)):
        commit(mode, rig, change)
    assert rig.store.calls.count("mint") == before + 1
    assert (
        rig.store.find_document(
            DocumentAddress({"kind": "rollback", "scope": {"kind": "session"}})
        )
        is None
    )
    assert not touched and rig.session.failure is None
    if saved:
        assert inspect.getcoroutinestate(saved[0]) == inspect.CORO_CLOSED
    rig.expected = []


def test_async_creation_captures_before_cold_read_and_refuses_reentry(tmp_path):
    async def run():
        store = Recording(MemoryStorage())
        session = Session(store)
        try:
            conversation = await session.acommit(
                lambda tx: tx.create_conversation({"kind": "ownerless"})
            )
            caller = {"value": [1]}
            calls = []

            def initial(value):
                calls.append("captured")
                with pytest.raises(ReentrantSessionCall):
                    session.read_on_line(lambda: None)
                return {"phase": "start", "value": value}

            definition = token(initial)

            async def change(tx):
                operation = tx.create_task(
                    definition,
                    caller,
                    ownership={"kind": "conversation"},
                    conversation_id=conversation.id,
                )
                caller["value"].append(2)
                object.__setattr__(
                    definition, "initial", lambda _: calls.append("replacement")
                )
                object.__setattr__(definition, "name", "replacement")
                return await operation

            id = await session.acommit(change)
            record = store.task(id).to_tree()
            assert calls == ["captured"] and record["kind"] == "work"
            assert record["input"] == {"value": [1]}
        finally:
            await session.aclose()

    asyncio.run(run())


def test_foreign_initializer_result_never_runs_attribute_or_cleanup_hooks():
    touched = []

    class Foreign:
        def __getattribute__(self, name):
            touched.append(name)
            raise AssertionError("foreign attribute hook")

        def __await__(self):
            raise AssertionError("foreign await hook")

        def close(self):
            raise AssertionError("foreign cleanup hook")

    store = Recording(MemoryStorage())
    session = Session(store)
    try:
        conversation = session.commit(
            lambda tx: tx.create_conversation({"kind": "ownerless"})
        ).id
        before = store.calls.count("mint")
        with pytest.raises(JsonValueError):
            session.commit(
                lambda tx: tx.create_task(
                    token(lambda _: Foreign()),
                    None,
                    ownership={"kind": "conversation"},
                    conversation_id=conversation,
                )
            )
        assert touched == [] and store.calls.count("mint") == before
    finally:
        session.close()


@pytest.mark.parametrize("action", ["close", "fail"])
def test_initializer_health_and_accepted_close(action):
    store = Recording(MemoryStorage())
    session = Session(store, reporter=lambda *_: None)
    failure = RuntimeError("initializer failed owner")
    conversation = session.commit(
        lambda tx: tx.create_conversation({"kind": "ownerless"})
    ).id
    before = store.calls.count("mint")

    def initial(value):
        # Callback and reference disposal must never occur under this lock.
        assert session._owner._cond.acquire(blocking=False)
        session._owner._cond.release()
        if action == "close":
            session.request_close()
        else:
            session.fail(failure)
        return {"phase": "start"}

    try:
        if action == "fail":
            with pytest.raises(SessionFailed) as caught:
                session.commit(
                    lambda tx: tx.create_task(
                        token(initial),
                        None,
                        ownership={"kind": "conversation"},
                        conversation_id=conversation,
                    )
                )
            assert caught.value.cause is failure and store.calls.count("mint") == before
        else:
            id = session.commit(
                lambda tx: tx.create_task(
                    token(initial),
                    None,
                    ownership={"kind": "conversation"},
                    conversation_id=conversation,
                )
            )
            assert id and store.calls.count("mint") == before + 1
    finally:
        session.close()


def test_definition_identity_never_compares_or_hashes_foreign_callbacks():
    class Handler:
        def __call__(self, *args):
            return None

        def __eq__(self, other):
            raise AssertionError("foreign callback equality")

        def __hash__(self):
            raise AssertionError("foreign callback hash")

    handler = Handler()
    first = define_task("work", 1, handler, {"start": handler}, handler)
    second = define_task("work", 1, handler, {"start": handler}, handler)
    other_handler = Handler()
    third = define_task(
        "work", 1, other_handler, {"start": other_handler}, other_handler
    )
    assert first != third
    assert first is not second and first != second
    assert first == first and len({first, second}) == 2
    assert {first: "first", second: "second"}[first] == "first"

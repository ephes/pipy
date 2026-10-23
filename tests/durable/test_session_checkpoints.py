"""Real checkpoint policies: selection, ownership, ordering and atomic failure."""

from __future__ import annotations

import asyncio
import dataclasses
import gc
import inspect
import threading

import pytest
from storage_contract_cases import backend as backend
from test_session_callback_authority import core_mutant
from test_session_documents import address, base, commit, docs, seed, snapshot, task
from test_session_documents import mode as mode
from test_session_documents import rig as rig
from test_session_tx_core import Worker

from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.documents import (
    CheckpointInfo,
    define_doc,
    define_doc_family,
)
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.tx import DraftRevoked, TransactionSettled

pytestmark = pytest.mark.parametrize(
    "backend", ["memory", "sqlite", "reopen"], indirect=True
)


async def unload(r):
    if r.mode.sync:
        r.session.unload_documents()
    else:
        await r.session.aunload_documents()


@pytest.mark.parametrize("scope", ["session", "conversation", "task"])
@pytest.mark.parametrize("family", [False, True])
def test_policy_prior_counts_base_reset_actual_unload_and_exact_publication(
    rig, scope, family
):
    r = rig
    seen = []

    class Policy:
        def choose(self, value, ops, info):
            assert type(value) is OwnedJson and type(ops) is OwnedJson
            assert type(info) is CheckpointInfo
            with pytest.raises(dataclasses.FrozenInstanceError):
                info.deltas_since_base = 100
            seen.append((value, ops, info))
            return info.deltas_since_base == 2

    policy = Policy().choose
    factory = define_doc_family if family else define_doc
    spec = factory(
        "checkpoint.counts",
        1,
        scope,
        (lambda _seed: {"n": 0}) if family else (lambda: {"n": 0}),
        "latest" if scope == "conversation" else None,
        "initial" if scope == "conversation" else None,
        policy,
    )
    options = address(scope, **({"key": ""} if family else {}))

    async def edit(tx):
        draft = await tx.doc(spec, **options, **({"seed": None} if family else {}))
        draft["n"] += 1

    async def run():
        await commit(r, edit)
        old = await snapshot(r, spec, **options)
        assert old.value == OwnedJson({"n": 1}) and seen == []
        for index, count in enumerate([1, 2, 0, 1, 2, 0, 1, 2], 2):
            await commit(r, edit)
            current = await snapshot(r, spec, **options)
            pub = docs(r.publications[-1])[0]
            assert pub.value is current.value is seen[-1][0]
            assert pub.ops is seen[-1][1]
            assert pub.ops == OwnedJson([["s", ["n"], index]])
            assert current.deltas_since_base == count
            content = r.store.commits[-1][0].to_tree()["content"]
            assert content["kind"] == ("base" if count == 0 else "delta")
            before = len(r.store.calls), len(r.publications)
            await unload(r)
            assert before == (len(r.store.calls), len(r.publications))
            cold = await snapshot(r, spec, **options)
            assert cold is not current
            assert cold.value == current.value and cold.version == current.version
            assert cold.deltas_since_base == current.deltas_since_base
            assert old.value == OwnedJson({"n": 1}) and old.deltas_since_base == 0
        assert [item[2].deltas_since_base for item in seen] == [0, 1, 2, 0, 1, 2, 0, 1]

    r.mode.run(run)


def test_all_drafts_detached_views_and_iterators_revoked_before_first_policy(rig):  # noqa: C901 - every revocation surface before policy
    r = rig
    escaped, mutators, seen = [], [], []

    def policy(value, ops, info):
        if not seen:
            for view in escaped:
                with pytest.raises(DraftRevoked):
                    len(view)
        for change in mutators:
            with pytest.raises(TransactionSettled):
                change()
        seen.append((value, ops))
        return True

    specs = [
        define_doc(
            f"revoke.{n}",
            1,
            "session",
            lambda: {"a": [1, 2], "b": {"n": 0}},
            checkpoint_when=policy,
        )
        for n in range(2)
    ]

    async def create(tx):
        for spec in specs:
            await tx.doc(spec)

    async def edit(tx):
        for spec in specs:
            draft = await tx.doc(spec)
            nested = draft["a"]
            iterator = iter(nested)
            assert next(iterator) == 1
            detached = draft["b"]
            del draft["b"]
            nested.append(3)
            escaped.extend([draft, nested, detached])
            mutators.append(lambda iterator=iterator: next(iterator))
        # Iterator guards are DraftRevoked; transaction mutation guards differ.
        iterators = mutators[:]
        mutators.clear()
        for iterator in iterators:
            escaped.append(IteratorView(iterator))
        mutators.append(lambda: tx.set_task(task()))

    class IteratorView:
        def __init__(self, advance):
            self.advance = advance

        def __len__(self):
            return self.advance()

    async def run():
        await commit(r, create)
        await commit(r, edit)
        assert len(seen) == 2
        assert all(
            w.to_tree()["content"]["kind"] == "base" for w in r.store.commits[-1]
        )

    r.mode.run(run)


@pytest.mark.parametrize("control", [False, True])
def test_second_policy_rolls_back_every_document_table_and_retirement(rig, control):
    r = rig
    seen, fail = [], [True]
    error = (
        BaseException("second predicate") if control else ValueError("second predicate")
    )

    def first(value, ops, info):
        seen.append("first")
        return True

    def second(value, ops, info):
        seen.append("second")
        if fail[0]:
            raise error
        return False

    specs = [
        define_doc(
            f"atomic.{n}", 1, "session", lambda: {"n": 0}, checkpoint_when=policy
        )
        for n, policy in enumerate([first, second])
    ]

    async def initial(tx):
        for spec in specs:
            await tx.doc(spec)

    async def edit(tx):
        await tx.append_entry(7, {"kind": "checkpoint.entry", "data": None})
        await tx.create_submission(
            {
                "conversationId": 7,
                "type": "input",
                "status": "queued",
                "requestId": "checkpoint.atomic",
            }
        )
        tx.set_task(task(terminal=True))
        for spec in specs:
            (await tx.doc(spec))["n"] = 1
        await tx.retire_doc(specs[0])

    async def run():
        await commit(r, initial)
        old = [await snapshot(r, spec) for spec in specs]
        writes, publications = len(r.store.commits), len(r.publications)
        with pytest.raises(type(error)) as caught:
            await commit(r, edit)
        assert caught.value is error and seen == ["first", "second"]
        assert len(r.store.commits) == writes and len(r.publications) == publications
        assert [await snapshot(r, spec) for spec in specs] == old
        for spec, before in zip(specs, old, strict=True):
            assert await snapshot(r, spec) is before
        assert r.inner.task(20).to_tree()["state"]["status"] == "pending"
        assert r.session.failure is None
        fail[0] = False
        await commit(r, edit)
        assert seen == ["first", "second", "first", "second"]
        assert await snapshot(r, specs[0]) is None
        assert (await snapshot(r, specs[1])).deltas_since_base == 1

    r.mode.run(run)


def test_all_257_terminal_members_scan_before_any_policy(rig):
    r = rig
    seen = []
    seed(r.inner, *(base(1000 + n, f"unknown.{n}") for n in range(257)))

    def policy(_value, _ops, _info):
        seen.append(sum(name == "scan_documents" for name, _ in r.store.calls))
        return False

    spec = define_doc(
        "pages.policy", 1, "session", lambda: {"n": 0}, checkpoint_when=policy
    )

    async def create(tx):
        await tx.doc(spec)

    async def edit(tx):
        (await tx.doc(spec))["n"] = 1
        tx.set_task(task(terminal=True))

    r.mode.run(lambda: commit(r, create))
    gate = r.store.hold("scan_documents")
    worker = Worker("checkpoint-pages", lambda: r.mode.run(lambda: commit(r, edit)))
    worker.thread.start()
    try:
        assert gate.entered.wait(5)
        assert seen == []
    finally:
        gate.release.set()
        worker.join()
    assert worker.error is None
    assert seen == [2]
    assert (
        sum(w.to_tree()["type"] == "document.retire" for w in r.store.commits[-1])
        == 257
    )


@pytest.mark.parametrize("invalid", [None, 0, 1, "yes", object()])
def test_policy_requires_exact_bool_and_session_recovers(rig, invalid):
    r = rig
    answers = [invalid]
    spec = define_doc(
        "invalid.policy",
        1,
        "session",
        lambda: {"n": 0},
        checkpoint_when=lambda *_: answers[0],
    )

    async def edit(tx):
        (await tx.doc(spec))["n"] += 1

    async def run():
        await commit(r, edit)
        old = await snapshot(r, spec)
        with pytest.raises(TypeError, match="exact bool"):
            await commit(r, edit)
        assert await snapshot(r, spec) is old
        assert r.session.failure is None
        answers[0] = False
        await commit(r, edit)
        assert (await snapshot(r, spec)).deltas_since_base == 1

    r.mode.run(run)


def test_refused_coroutine_closed_without_running_or_foreign_result_hooks(rig):
    r = rig
    seen, returns = [], []

    async def body():
        seen.append("ran")

    class Foreign:
        def __getattribute__(self, name):
            seen.append(name)
            raise AssertionError(name)

        def __bool__(self):
            seen.append("bool")
            raise AssertionError

        def __await__(self):
            seen.append("await")
            yield

    spec = define_doc(
        "result.policy",
        1,
        "session",
        lambda: {"n": 0},
        checkpoint_when=lambda *_: returns[0],
    )

    async def edit(tx):
        (await tx.doc(spec))["n"] += 1

    async def run():
        await commit(r, edit)
        values = [body(), Foreign()]
        future = None if r.mode.sync else asyncio.get_running_loop().create_future()
        if future is not None:
            values.append(future)
        for value in values:
            returns[:] = [value]
            with pytest.raises(TypeError, match="exact bool"):
                await commit(r, edit)
        assert inspect.getcoroutinestate(values[0]) == inspect.CORO_CLOSED
        assert future is None or not future.done()
        assert seen == []

    r.mode.run(run)


def test_policy_result_finalizer_runs_outside_owner_cache_and_backend_locks(rig):
    r = rig
    finalized = []

    class Result:
        def __del__(self):
            acquired = []

            def probe():
                with r.session._owner._cond, r.session._cache._lock, r.store.lock:
                    acquired.append(True)

            worker = threading.Thread(target=probe, daemon=True)
            worker.start()
            worker.join(5)
            finalized.append(acquired == [True] and not worker.is_alive())

    spec = define_doc(
        "policy.finalizer",
        1,
        "session",
        lambda: {"n": 0},
        checkpoint_when=lambda *_: Result(),
    )

    async def edit(tx):
        (await tx.doc(spec))["n"] += 1

    async def run():
        await commit(r, edit)
        try:
            await commit(r, edit)
        except TypeError:
            pass
        else:
            raise AssertionError("foreign result accepted")

    r.mode.run(run)
    gc.collect()
    assert finalized == [True]


@pytest.mark.parametrize("dirty", [False, True])
def test_creation_empty_and_clean_retirement_skip_policy_dirty_retirement_calls_once(
    rig, dirty
):
    r = rig
    calls = []
    spec = define_doc(
        "policy.skips",
        1,
        "session",
        lambda: {"n": 0},
        checkpoint_when=lambda *_: calls.append("policy") or True,
    )

    async def create(tx):
        (await tx.doc(spec))["n"] = 2

    async def empty(tx):
        draft = await tx.doc(spec)
        draft["n"] = 3
        draft["n"] = 2

    async def retire(tx):
        if dirty:
            (await tx.doc(spec))["n"] = 4
        await tx.retire_doc(spec)

    async def run():
        await commit(r, create)
        before = await snapshot(r, spec)
        await commit(r, empty)
        assert await snapshot(r, spec) is before and calls == []
        await commit(r, retire)
        assert calls == (["policy"] if dirty else [])
        assert await snapshot(r, spec) is None
        pub = docs(r.publications[-1])[0]
        assert pub.value is None and pub.version is None and pub.ops == OwnedJson([])
        await commit(r, retire)
        assert calls == (["policy"] if dirty else [])

    r.mode.run(run)


async def checkpoint_witness(session_type, inner, reached):
    session = session_type(inner)
    publications = []
    session.observe_commits(publications.append)

    def choose(_value, _ops, info):
        reached.append(info.deltas_since_base)
        return info.deltas_since_base == 2

    spec = define_doc(
        "mutant.checkpoint", 1, "session", lambda: {"n": 0}, checkpoint_when=choose
    )

    async def edit(tx):
        (await tx.doc(spec))["n"] += 1

    try:
        await session.acommit(edit)
        for n, count in enumerate([1, 2, 0, 1], 2):
            await session.acommit(edit)
            snapshot = await session.asnapshot(spec)
            assert snapshot.deltas_since_base == count
            assert len(publications[-1].changes) == 1
            assert publications[-1].changes[0].ops == OwnedJson([["s", ["n"], n]])
        assert reached == [0, 1, 2, 0]
    finally:
        await session.aclose()


@pytest.mark.parametrize(
    "mutation", ["force-base", "force-delta", "count-reset", "publication-ops"]
)
def test_checkpoint_production_mutants_reach_policy_and_fail(backend, mutation):
    changes = {
        "force-base": (
            "            plan.checkpoint = selected",
            "            selected = True\n            plan.checkpoint = selected",
        ),
        "force-delta": (
            "            plan.checkpoint = selected",
            "            selected = False\n            plan.checkpoint = selected",
        ),
        "count-reset": (
            "0 if plan.checkpoint else loaded.deltas_since_base + 1",
            "loaded.deltas_since_base + 1",
        ),
        "publication-ops": (
            "                prepared.value,\n                prepared.ops,",
            "                prepared.value,\n                _EMPTY_OPS,",
        ),
    }
    reached = []
    asyncio.run(checkpoint_witness(Session, backend.new(), reached))
    assert reached == [0, 1, 2, 0]
    changed = core_mutant(mutation.replace("-", "_"), *changes[mutation], tx=True)
    reached.clear()
    with pytest.raises(AssertionError):
        asyncio.run(checkpoint_witness(changed.Session, backend.new(), reached))
    assert reached

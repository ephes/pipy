"""DE04a: typed current documents on actual Memory/SQLite/reopen Session lines.

Reference call-order recipes A1–A9/T1–T10/P1–P3/C1–C2 are paired separately.
These Python-only exact ingress and real-backend matrix tests use explicit
entered/release barriers; no sleep establishes ordering.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from storage_contract_cases import backend as backend
from task_support import task_definition
from test_session_commit import ASYNC, SYNC
from test_session_tx_core import Store, Worker

from pipy_harness.native.durable.codec import JsonValueError
from pipy_harness.native.durable.documents import (
    DocFamily,
    define_doc,
    define_doc_family,
)
from pipy_harness.native.durable.records import OMITTED
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.storage import (
    DocumentQuery,
    StorageWrite,
)
from pipy_harness.native.durable.tx import DraftRevoked, RawDocumentChange

WAIT = 10


def task(id=20, *, terminal=False, conversation=7):
    return {
        "id": id,
        "conversationId": conversation,
        "kind": "probe.task",
        "version": 1,
        "input": None,
        "state": {
            "status": "terminal",
            "outcome": {"status": "completed", "result": {"ok": True}},
        }
        if terminal
        else {"status": "pending", "checkpoint": None},
        "background": False,
        "abortRequested": False,
    }


def base(id, kind="probe.single", *, owner=20, key=OMITTED, value=None):
    record = {"id": id, "kind": kind, "scope": {"kind": "task", "taskId": owner}}
    if key is not OMITTED:
        record["key"] = key
    return {
        "type": "document.create",
        "record": record,
        "content": {
            "kind": "base",
            "version": 1,
            "value": {"lines": ["raw"]} if value is None else value,
        },
    }


def seed(inner, *writes):
    return inner.commit(tuple(StorageWrite(w) for w in writes))


@pytest.fixture(params=[SYNC, ASYNC], ids=["sync", "async"])
def mode(request):
    return request.param


@pytest.fixture(params=["memory", "sqlite", "reopen"])
def backend_kind(request):
    return request.param


@pytest.fixture
def rig(backend, mode):
    inner = backend.new()
    seed(
        inner,
        {"type": "conversation", "value": {"id": 1}},
        {"type": "conversation", "value": {"id": 7}},
        {"type": "task", "value": task()},
    )
    store = Store(inner)
    reports, publications = [], []
    session = Session(
        store,
        reporter=lambda error, info: reports.append((error, info)),
        now=lambda: 1000,
    )
    session.observe_commits(publications.append)
    value = SimpleNamespace(
        session=session,
        inner=inner,
        store=store,
        mode=mode,
        reports=reports,
        publications=publications,
    )
    try:
        yield value
    finally:
        store.release_all()
        try:
            session.close(timeout=WAIT)
        except RuntimeError:
            assert reports


# Reuse the real backend factory and its post-close integrity/mode audit.
pytestmark = pytest.mark.parametrize(
    "backend", ["memory", "sqlite", "reopen"], indirect=True
)


def token(
    scope="session", *, family=False, kind="probe.single", initial=None, version=1
):
    options = (
        {"history": "latest", "fork": "initial"} if scope == "conversation" else {}
    )
    return (
        define_doc_family(
            kind,
            version,
            scope,
            initial or (lambda seed: {"seed": seed, "hits": 0}),
            **options,
            checkpoint_when=lambda _value, _ops, _info: False,
        )
        if family
        else define_doc(
            kind,
            version,
            scope,
            initial or (lambda: {"lines": []}),
            **options,
            checkpoint_when=lambda _value, _ops, _info: False,
        )
    )


def address(scope, *, key=OMITTED):
    result = (
        {}
        if scope == "session"
        else {
            "conversation_id" if scope == "conversation" else "task_id": 7
            if scope == "conversation"
            else 20
        }
    )
    if key is not OMITTED:
        result["key"] = key
    return result


async def snapshot(r, spec, **options):
    return (
        r.session.snapshot(spec, **options)
        if r.mode.sync
        else await r.session.asnapshot(spec, **options)
    )


async def commit(r, cb):
    return await r.mode.commit(r, cb)


def docs(publication):
    return [v for v in publication.changes if type(v) is RawDocumentChange]


def test_all_scopes_families_atomic_and_publication_snapshot_identity(rig):
    r = rig

    async def run():
        specs = [
            (
                token(scope, family=family),
                address(scope, key="k") if family else address(scope),
                family,
            )
            for scope in ("session", "conversation", "task")
            for family in (False, True)
        ]
        escaped = []

        async def write(tx):
            await tx.append_entry(7, {"kind": "probe", "data": None})
            for i, (spec, options, family) in enumerate(specs):
                draft = await tx.doc(
                    spec, **options, **({"seed": {"n": i}} if family else {})
                )
                escaped.append(draft)
                if family:
                    draft["hits"] = 1
                else:
                    draft["lines"].append(i)

        await commit(r, write)
        changes = docs(r.publications[-1])
        assert len(changes) == 6 and len({c.record.id for c in changes}) == 6
        for (spec, options, _), change in zip(specs, changes, strict=True):
            current = await snapshot(r, spec, **options)
            assert current.value is change.value
            assert current.deltas_since_base == 0 and change.ops.thaw() == []
            assert change.conversation_id == (None if spec.scope == "session" else 7)
            record = current.record.to_tree()
            assert ("key" in record) is (type(spec) is DocFamily)
        assert len(r.store.commits) == 1
        for draft in escaped:
            with pytest.raises(DraftRevoked):
                list(draft)

    r.mode.run(run)


def test_family_memo_seed_validation_cutoff_and_existing_member(rig):
    r = rig
    initial = []
    spec = token(
        family=True,
        initial=lambda seed: initial.append(seed) or {"seed": seed, "hits": 0},
    )

    async def run():
        async def first(tx):
            one = await tx.doc(spec, key="", seed={"n": 1})
            two = await tx.doc(spec, key="", seed=object())
            other_version = token(family=True, version=2)
            assert await tx.doc(other_version, key="", seed=object()) is one is two
            one["hits"] = 1

        await commit(r, first)
        assert initial == [{"n": 1}]
        before = r.store.count("find_document")

        async def invalid(tx):
            await tx.doc(spec, key="", seed=object())

        with pytest.raises(JsonValueError):
            await commit(r, invalid)
        assert r.store.count("find_document") == before

        async def existing(tx):
            draft = await tx.doc(spec, key="", seed="unused")
            draft["hits"] = 2

        await commit(r, existing)
        assert initial == [{"n": 1}]
        assert (await snapshot(r, spec, key="")).value.thaw() == {
            "seed": {"n": 1},
            "hits": 2,
        }

        async def wrong_version(tx):
            await tx.doc(token(family=True, version=2), key="", seed=None)

        with pytest.raises(RuntimeError, match="requires migration"):
            await commit(r, wrong_version)

    r.mode.run(run)


def test_address_isolation_utf16_empty_key_retire_recreate(rig):
    r = rig
    single, family = token(), token(family=True)

    async def run():
        async def write(tx):
            await tx.doc(single)
            for key in ("", "k", "\ud800", "\U0001f600"):
                await tx.doc(family, key=key, seed=key)
            assert await tx.doc(
                family, key="\ud83d\ude00", seed=object()
            ) is await tx.doc(family, key="\U0001f600", seed=object())

        await commit(r, write)
        old = await snapshot(r, family, key="")

        async def replace(tx):
            await tx.retire_doc(family, key="")
            await tx.doc(family, key="", seed="new")

        await commit(r, replace)
        new = await snapshot(r, family, key="")
        assert new.record.id != old.record.id
        assert new.value.thaw()["seed"] == "new" and old.value.thaw()["seed"] == ""
        assert await snapshot(r, single) is not None
        assert (await snapshot(r, family, key="k")).value.thaw()["seed"] == "k"

        async def remove(tx):
            await tx.retire_doc(family, key="")

        await commit(r, remove)
        assert await snapshot(r, family, key="") is None

    r.mode.run(run)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("owner", ["missing", "terminal"])
def test_task_absent_only_owner_guard_keeps_loaded_call_order(rig, existing, owner):
    r = rig
    owner_id = 4242 if owner == "missing" else 20
    writes = []
    if owner == "terminal":
        writes.append({"type": "task", "value": task(terminal=True)})
    if existing:
        writes.append(base(100, owner=owner_id))
    if writes:
        seed(r.inner, *writes)
    spec = token("task")

    async def run():
        async def use(tx):
            draft = await tx.doc(spec, task_id=owner_id)
            draft["lines"].append("loaded")

        if existing:
            await commit(r, use)
            assert docs(r.publications[-1])[0].conversation_id == (
                None if owner == "missing" else 7
            )
        else:
            with pytest.raises(
                RuntimeError,
                match="does not exist" if owner == "missing" else "is terminal",
            ):
                await commit(r, use)
            assert not r.store.commits

    r.mode.run(run)


def test_staged_terminal_precedes_memo_and_invalid_seed(rig):
    r = rig
    single, family = token("task"), token("task", family=True)
    seed(r.inner, base(100))

    async def run():
        async def finish(tx):
            draft = await tx.doc(single, task_id=20)
            tx.set_task(task(terminal=True))
            with pytest.raises(RuntimeError, match="Task 20 is terminal"):
                await tx.doc(single, task_id=20)
            with pytest.raises(RuntimeError, match="Task 20 is terminal"):
                await tx.doc(family, task_id=20, key="late", seed=object())
            draft["lines"].append("final")

        await commit(r, finish)
        change = docs(r.publications[-1])[0]
        assert change.value is None and change.version is None
        assert await snapshot(r, single, task_id=20) is None
        writes = [w.to_tree() for w in r.store.commits[-1]]
        assert [w["type"] for w in writes] == [
            "task",
            "document.change",
            "document.retire",
        ]

    r.mode.run(run)


def test_new_task_document_terminal_skips_scan_and_never_caches(rig):
    r = rig
    single, family = token("task"), token("task", family=True)

    async def run():
        async def finish(tx):
            id = await tx.create_task(
                task_definition("probe.task", 1, None),
                None,
                ownership={"kind": "conversation"},
                conversation_id=7,
            )
            draft = await tx.doc(single, task_id=id)
            draft["lines"].append("final")
            await tx.doc(family, task_id=id, key="one", seed=None)
            tx.set_task(task(id, terminal=True))
            return id

        result = await commit(r, finish)
        # Commit result is callback's return; its exact wrapper is tested by existing Session cases.
        assert result is not None
        assert r.store.count("scan_documents") == 0
        changes = docs(r.publications[-1])
        assert len(changes) == 2
        for change in changes:
            tree = change.record.to_tree()
            assert tree["createdAt"] == tree["retiredAt"]
            assert change.value is None and change.conversation_id == 7
        assert not r.session._cache._documents

    r.mode.run(run)


@pytest.mark.parametrize("dedupe", [False, True])
def test_terminal_retires_257_unknown_kinds_all_pages_and_dedupes(rig, dedupe):
    r = rig
    seed(
        r.inner,
        *(
            base(
                1000 + i,
                "unknown." + str(i),
                key=OMITTED if i % 3 == 0 else "" if i % 3 == 1 else "k",
            )
            for i in range(257)
        ),
    )

    async def run():
        async def finish(tx):
            if dedupe:
                await tx.retire_doc(token("task", kind="unknown.0"), task_id=20)
                draft = await tx.doc(
                    token("task", family=True, kind="unknown.1"),
                    task_id=20,
                    key="",
                    seed=None,
                )
                draft["lines"].append("final")
            tx.set_task(task(terminal=True))

        await commit(r, finish)
        scans = [args for name, args in r.store.calls if name == "scan_documents"]
        assert len(scans) == 2 and [args[1] for args in scans] == [256, 256]
        assert scans[0][2] is None and scans[1][2].after == 1255
        writes = [w.to_tree() for w in r.store.commits[-1]]
        ids = [w["id"] for w in writes if w["type"] == "document.retire"]
        assert sorted(ids) == list(range(1000, 1257))
        changes = docs(r.publications[-1])
        assert len(changes) == 257 and all(
            c.value is None
            and c.version is None
            and c.ops.thaw() == []
            and c.conversation_id == 7
            for c in changes
        )
        if dedupe:
            content = next(w for w in writes if w["type"] == "document.change")
            assert content["content"] == {
                "kind": "delta",
                "version": 1,
                "ops": [["p", ["lines"], 1, 0, ["final"]]],
            }
            assert writes.index(content) < writes.index(
                {"type": "document.retire", "id": content["id"]}
            )
        assert not r.inner.scan_documents(
            DocumentQuery({"scope": {"kind": "task", "taskId": 20}, "at": "current"}),
            300,
        ).items

    r.mode.run(run)


def test_retirement_without_acquisition_ignores_initializer_and_terminal_gate(rig):
    r = rig
    seed(r.inner, {"type": "task", "value": task(terminal=True)}, base(100))

    def forbidden():
        raise AssertionError("initializer must not run")

    spec = token("task", initial=forbidden, version=2)

    async def run():
        async def retire(tx):
            await tx.retire_doc(spec, task_id=20)

        await commit(r, retire)
        assert docs(r.publications[-1])[0].conversation_id == 7
        assert await snapshot(r, spec, task_id=20) is None

    r.mode.run(run)


@pytest.mark.parametrize("failure", ["initial", "json", "owner", "assembly"])
def test_late_failure_rolls_back_coupled_tables_docs_and_revokes_nested(rig, failure):
    r = rig
    one, two = token(), token(kind="other")
    escaped = []

    def initial():
        if failure == "initial":
            raise ValueError("late initializer")
        return {"bad": object()}

    bad = token(kind="bad", initial=initial)

    async def run():
        async def before(tx):
            await tx.doc(one)
            await tx.doc(two)

        await commit(r, before)
        old = await snapshot(r, one)
        old_two = await snapshot(r, two)
        publications = len(r.publications)
        commits = len(r.store.commits)

        async def abort(tx):
            draft = await tx.doc(one)
            escaped.append(draft["lines"])
            draft["lines"].append("candidate")
            (await tx.doc(two))["lines"].append("candidate")
            await tx.append_entry(7, {"kind": "probe", "data": None})
            await tx.create_task(
                task_definition("probe.task", 1, None),
                None,
                ownership={"kind": "conversation"},
                conversation_id=7,
            )
            if failure == "assembly":
                tx.set_task(task(terminal=True))
                await tx.create_task(
                    task_definition("owned", 1, None),
                    None,
                    ownership={"kind": "task", "taskId": 20},
                    conversation_id=7,
                )
            elif failure == "owner":
                await tx.doc(token("task"), task_id=4242)
            else:
                await tx.doc(bad)

        with pytest.raises((ValueError, RuntimeError)):
            await commit(r, abort)
        assert len(r.store.commits) == commits and len(r.publications) == publications
        assert await snapshot(r, one) is old and await snapshot(r, two) is old_two
        assert old.deltas_since_base == old_two.deltas_since_base == 0
        assert all(
            r.inner.document(d.record.id).to_tree()["deltasSinceBase"] == 0
            for d in (old, old_two)
        )
        with pytest.raises(DraftRevoked):
            list(escaped[0])

    r.mode.run(run)


def test_seed_copy_and_pending_duplicate_cutoff_async(rig):
    r = rig
    if r.mode.sync:
        # Concurrent pending acquisitions exist only on the asynchronous facade.
        pytest.skip("Pending acquisition interleaving requires the async facade")
    initial = []
    spec = token(
        family=True, initial=lambda seed: initial.append(seed) or {"seed": seed}
    )

    async def run():
        gate = r.store.hold("find_document")
        caller = {"nested": {"n": 1}}

        async def write(tx):
            first = tx.doc(spec, key="k", seed=caller)
            assert await asyncio.to_thread(gate.entered.wait, WAIT)
            caller["nested"]["n"] = 99
            second = tx.doc(spec, key="k", seed=object())
            assert first is second
            gate.release.set()
            assert await first is await second

        try:
            await commit(r, write)
        finally:
            gate.release.set()
        assert initial == [{"nested": {"n": 1}}]

    r.mode.run(run)


@pytest.mark.parametrize("existing", [False, True])
def test_held_task_acquisition_rechecks_only_absent_latest_candidate(rig, existing):
    r = rig
    if r.mode.sync:
        pytest.skip(
            "Same-Tx mutation during a held acquisition requires the async facade"
        )
    if existing:
        seed(r.inner, base(100))
    initialized = []
    spec = token("task", initial=lambda: initialized.append(True) or {"lines": []})

    async def run():
        gate = r.store.hold("find_document")

        async def write(tx):
            pending = tx.doc(spec, task_id=20)
            assert await asyncio.to_thread(gate.entered.wait, WAIT)
            tx.set_task(task(terminal=True))
            gate.release.set()
            if existing:
                (await pending)["lines"].append("last")
            else:
                with pytest.raises(RuntimeError, match="Task 20 is terminal"):
                    await pending

        try:
            await commit(r, write)
        finally:
            gate.release.set()
        assert initialized == []
        assert await snapshot(r, spec, task_id=20) is None

    r.mode.run(run)


def test_held_commit_exposes_only_committed_family_revision_and_listener_identity(rig):
    r = rig
    spec = token(family=True)

    async def first():
        async def write(tx):
            await tx.doc(spec, key="k", seed="first")

        await commit(r, write)

    r.mode.run(first)
    previous = r.session.snapshot(spec, key="k")
    observed = []
    r.session.observe_commits(
        lambda publication: observed.append(
            r.session.snapshot(spec, key="k").value is docs(publication)[0].value
        )
    )
    gate = r.store.hold("commit")

    async def second():
        async def write(tx):
            (await tx.doc(spec, key="k", seed=None))["hits"] = 9

        await commit(r, write)

    worker = Worker("typed-held-commit", lambda: r.mode.run(second))
    worker.thread.start()
    try:
        assert gate.entered.wait(WAIT)
        assert r.session.snapshot(spec, key="k") is previous
        assert previous.value.thaw()["hits"] == 0 and previous.deltas_since_base == 0
        assert len(r.publications) == 1
    finally:
        gate.release.set()
        worker.value()
    current = r.session.snapshot(spec, key="k")
    assert current.value.thaw()["hits"] == 9 and observed == [True]
    assert current.deltas_since_base == 1 and previous.deltas_since_base == 0
    assert docs(r.publications[-1])[0].ops.thaw() == [["s", ["hits"], 9]]
    assert previous.value.thaw()["hits"] == 0


def test_family_retirement_evicts_each_cached_key_and_publication_owner_is_latest_task(
    rig,
):
    r = rig
    spec = token("task", family=True)

    async def run():
        async def write(tx):
            for key in ("", "a", "b"):
                await tx.doc(spec, task_id=20, key=key, seed=key)

        await commit(r, write)
        old = [await snapshot(r, spec, task_id=20, key=key) for key in ("", "a", "b")]

        async def finish(tx):
            await tx.create_conversation(ownership={"kind": "ownerless"})
            tx.set_task(task(terminal=True))

        await commit(r, finish)
        assert all(
            c.conversation_id == 7 and c.value is None for c in docs(r.publications[-1])
        )
        assert not r.session._cache._documents
        for key, previous in zip(("", "a", "b"), old, strict=True):
            assert await snapshot(r, spec, task_id=20, key=key) is None
            assert previous.value.thaw()["seed"] == key

    r.mode.run(run)


def test_absent_snapshots_and_retirement_never_initialize_or_mint(rig):
    r = rig

    def forbidden(*args):
        raise AssertionError("absent read/retire must not initialize")

    async def run():
        for scope in ("session", "conversation", "task"):
            for family in (False, True):
                spec = token(scope, family=family, initial=forbidden)
                options = address(scope, key="k") if family else address(scope)
                assert await snapshot(r, spec, **options) is None

                async def retire(tx):
                    await tx.retire_doc(spec, **options)

                await commit(r, retire)
        assert not r.store.commits and r.store.count("mint_id") == 0

    r.mode.run(run)


def test_mixed_entry_task_and_two_existing_document_updates_share_one_commit(rig):
    r = rig
    one, two = token(), token("task", family=True)

    async def run():
        async def initial(tx):
            await tx.doc(one)
            await tx.doc(two, task_id=20, key="k", seed={"n": 1})

        await commit(r, initial)
        old_one, old_two = (
            await snapshot(r, one),
            await snapshot(r, two, task_id=20, key="k"),
        )
        count = len(r.store.commits)

        async def mixed(tx):
            (await tx.doc(one))["lines"].append("new")
            (await tx.doc(two, task_id=20, key="k", seed=None))["hits"] = 2
            await tx.append_entry(7, {"kind": "probe", "data": {"coupled": True}})
            await tx.create_submission(
                {
                    "conversationId": 7,
                    "type": "input",
                    "status": "queued",
                    "requestId": "delta-coupled",
                }
            )
            candidate = task()
            candidate["input"] = {"coupled": True}
            tx.set_task(candidate)

        await commit(r, mixed)
        assert len(r.store.commits) == count + 1
        assert [w.to_tree()["type"] for w in r.store.commits[-1]] == [
            "entry",
            "submission",
            "task",
            "document.change",
            "document.change",
        ]
        assert (
            old_one.value.thaw() == {"lines": []} and old_two.value.thaw()["hits"] == 0
        )
        assert (await snapshot(r, one)).value.thaw() == {"lines": ["new"]}
        assert (await snapshot(r, two, task_id=20, key="k")).value.thaw()["hits"] == 2
        changes = docs(r.publications[-1])
        assert [c.ops.thaw() for c in changes] == [
            [["p", ["lines"], 0, 0, ["new"]]],
            [["s", ["hits"], 2]],
        ]
        for spec, options in ((one, {}), (two, {"task_id": 20, "key": "k"})):
            current = await snapshot(r, spec, **options)
            assert current.deltas_since_base == 1
            assert r.inner.document(current.record.id).to_tree()["deltasSinceBase"] == 1

    r.mode.run(run)


@pytest.mark.parametrize("settlement", ["success", "failure", "close"])
def test_late_task_family_acquisition_never_initializes_or_mints(rig, settlement):
    r = rig
    if r.mode.sync:
        pytest.skip("Unawaited pending acquisition requires the async facade")
    initial = []
    spec = token(
        "task", family=True, initial=lambda seed: initial.append(seed) or {"seed": seed}
    )

    async def run():
        gate = r.store.hold("find_document")
        settled = asyncio.Event()
        original = ValueError("callback failure")

        async def callback(tx):
            tx.doc(spec, task_id=20, key="late", seed={"n": 1})
            assert await asyncio.to_thread(gate.entered.wait, WAIT)
            settled.set()
            if settlement == "failure":
                raise original

        job = asyncio.create_task(r.session.acommit(callback))
        try:
            await asyncio.wait_for(settled.wait(), WAIT)
            if settlement == "close":
                r.session.request_close()
            gate.release.set()
            with pytest.raises((ValueError, RuntimeError)) as failed:
                await asyncio.wait_for(job, WAIT)
            if settlement == "failure":
                assert failed.value is original
        finally:
            gate.release.set()
            if not job.done():
                await asyncio.wait_for(
                    asyncio.gather(job, return_exceptions=True), WAIT
                )
        assert initial == [] and r.store.count("mint_id") == 0 and not r.store.commits

    r.mode.run(run)

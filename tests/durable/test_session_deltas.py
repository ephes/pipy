"""Actual default-policy Draft/Tx delta joins on Memory, SQLite and reopen.

These tests exercise the existing Python methods, not translated primitives.
The persisted replay is authoritative; strict OwnedJson equality retains native
binary64 signs and ordered UTF-16 keys without a reference projection.
"""

from __future__ import annotations

import copy
import gc
import pickle
from concurrent.futures import ThreadPoolExecutor

import pytest
from storage_contract_cases import backend as backend
from test_session_documents import (
    commit,
    docs,
    snapshot,
    token,
)
from test_session_documents import (
    mode as mode,
)
from test_session_documents import (
    rig as rig,
)

from pipy_harness.native.durable import tx as tx_module
from pipy_harness.native.durable.codec import JsonValueError, OwnedJson
from pipy_harness.native.durable.tx import (
    Draft,
    DraftList,
    DraftRevoked,
    TxAffinityError,
)

pytestmark = pytest.mark.parametrize(
    "backend", ["memory", "sqlite", "reopen"], indirect=True
)


async def create(r, spec):
    async def initial(tx):
        await tx.doc(spec)

    await commit(r, initial)
    current = await snapshot(r, spec)
    assert current.deltas_since_base == 0
    assert docs(r.publications[-1])[0].ops.thaw() == []
    return current


async def changed(r, spec, mutate, expected, value=None):
    old = await snapshot(r, spec)

    async def edit(tx):
        mutate(await tx.doc(spec))

    await commit(r, edit)
    current = await snapshot(r, spec)
    publication = docs(r.publications[-1])[0]
    assert current is not old and current.deltas_since_base == old.deltas_since_base + 1
    assert publication.value is current.value
    assert publication.ops.thaw() == expected
    content = r.store.commits[-1][0].to_tree()["content"]
    assert content == {"kind": "delta", "version": 1, "ops": expected}
    assert (
        r.inner.document(current.record.id).to_tree()["deltasSinceBase"]
        == current.deltas_since_base
    )
    assert (
        OwnedJson(r.inner.document(current.record.id).to_tree()["value"])
        == current.value
    )
    if value is not None:
        assert current.value == OwnedJson(value)
    return current


LIST_METHODS = [
    (
        "positive-extended-set",
        lambda a: a.__setitem__(slice(None, None, 2), [7, 8]),
        [["s", ["a", 0], 7], ["s", ["a", 2], 8]],
        [7, 1, 8, 3],
    ),
    (
        "positive-extended-delete",
        lambda a: a.__delitem__(slice(None, None, 2)),
        [["p", ["a"], 2, 1, []], ["p", ["a"], 0, 1, []]],
        [1, 3],
    ),
    ("integer-set", lambda a: a.__setitem__(-1, 9), [["s", ["a", 3], 9]], [0, 1, 2, 9]),
    (
        "integer-delete",
        lambda a: a.__delitem__(-2),
        [["p", ["a"], 2, 1, []]],
        [0, 1, 3],
    ),
    (
        "slice-set",
        lambda a: a.__setitem__(slice(1, 3), [7, 8]),
        [["p", ["a"], 1, 2, []], ["p", ["a"], 1, 0, [7, 8]]],
        [0, 7, 8, 3],
    ),
    (
        "slice-delete",
        lambda a: a.__delitem__(slice(1, 3)),
        [["p", ["a"], 1, 2, []]],
        [0, 3],
    ),
    (
        "extended-set",
        lambda a: a.__setitem__(slice(None, None, -2), [7, 8]),
        [["s", ["a", 3], 7], ["s", ["a", 1], 8]],
        [0, 8, 2, 7],
    ),
    (
        "extended-delete",
        lambda a: a.__delitem__(slice(None, None, -2)),
        [["p", ["a"], 3, 1, []], ["p", ["a"], 1, 1, []]],
        [0, 2],
    ),
    ("append", lambda a: a.append(4), [["p", ["a"], 4, 0, [4]]], [0, 1, 2, 3, 4]),
    (
        "extend",
        lambda a: a.extend([4, 5]),
        [["p", ["a"], 4, 0, [4, 5]]],
        [0, 1, 2, 3, 4, 5],
    ),
    (
        "self-extend",
        lambda a: a.extend(a),
        [["p", ["a"], 4, 0, [0, 1, 2, 3]]],
        [0, 1, 2, 3, 0, 1, 2, 3],
    ),
    ("iadd", lambda a: a.__iadd__([4]), [["p", ["a"], 4, 0, [4]]], [0, 1, 2, 3, 4]),
    ("remove", lambda a: a.remove(1), [["p", ["a"], 1, 1, []]], [0, 2, 3]),
    ("insert", lambda a: a.insert(-1, 9), [["p", ["a"], 3, 0, [9]]], [0, 1, 2, 9, 3]),
    ("pop", lambda a: a.pop(-2), [["p", ["a"], 2, 1, []]], [0, 1, 3]),
    ("reverse", lambda a: a.reverse(), [["m", ["a"], [3, 2, 1, 0]]], [3, 2, 1, 0]),
    ("clear", lambda a: a.clear(), [["p", ["a"], 0, 4, []]], []),
]


@pytest.mark.parametrize(
    "name,mutate,ops,expected", LIST_METHODS, ids=[r[0] for r in LIST_METHODS]
)
def test_actual_list_methods_persist_exact_operations(rig, name, mutate, ops, expected):
    r = rig
    spec = token(initial=lambda: {"a": [0, 1, 2, 3]})

    async def run():
        await create(r, spec)
        await changed(r, spec, lambda d: mutate(d["a"]), ops, {"a": expected})

    r.mode.run(run)


def test_mapping_methods_detached_placement_and_stable_view_identity(rig):
    r = rig
    spec = token(initial=lambda: {"a": {"n": 0}})
    escaped = []

    async def run():
        await create(r, spec)

        def edit(d):
            assert type(d) is Draft and d["a"] is d["a"]
            held = d["a"]
            removed = d.pop("a")
            held["n"] = 9
            assert removed == {"n": 0}
            assert d.pop("missing", 8) == 8
            d.update([("b", 2), ("c", 3)])
            assert d.setdefault("b", 9) == 2
            assert d.popitem() == ("c", 3)
            assert d.setdefault("placed", held) == {"n": 9}
            held["n"] = 10
            escaped.extend((d, held, iter(d)))

        await changed(
            r,
            spec,
            edit,
            [["s", ["b"], 2], ["s", ["placed"], {"n": 9}], ["d", ["a"]]],
            {"b": 2, "placed": {"n": 9}},
        )
        await changed(
            r, spec, lambda d: d.clear(), [["d", ["b"]], ["d", ["placed"]]], {}
        )
        for view in escaped:
            with pytest.raises(DraftRevoked):
                next(view) if hasattr(view, "__next__") else len(view)

    r.mode.run(run)


def test_empty_operations_keep_incarnation_but_equal_structural_changes_write(rig):
    r = rig
    spec = token(initial=lambda: {"a": [0, 1], "n": 0, "m": {"x": 1}})

    async def run():
        old = await create(r, spec)
        commits, publications = len(r.store.commits), len(r.publications)

        async def untouched(tx):
            return None

        await commit(r, untouched)
        for mutate in (
            lambda d: None,
            lambda d: d["m"]["x"],
            lambda d: d.__setitem__("n", 0),
            lambda d: (d["a"].append(9), d["a"].pop()),
            lambda d: (d["m"].__setitem__("x", 2), d["m"].__setitem__("x", 1)),
        ):

            async def no_op(tx):
                mutate(await tx.doc(spec))

            await commit(r, no_op)
            assert await snapshot(r, spec) is old

        async def equal_container(tx):
            d = await tx.doc(spec)
            held = d["m"]
            d["m"] = {"x": 1}
            held["x"] = 99
            assert d["m"] is not held

        await commit(r, equal_container)
        assert await snapshot(r, spec) is old
        assert (len(r.store.commits), len(r.publications)) == (commits, publications)
        await changed(
            r,
            spec,
            lambda d: (d["a"].pop(0), d["a"].insert(0, 0)),
            [["p", ["a"], 0, 1, []], ["p", ["a"], 0, 0, [0]]],
            old.value.thaw(),
        )

    r.mode.run(run)


@pytest.mark.parametrize("zero", [0.0, -0.0], ids=["positive", "negative"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("dirty", [False, True])
def test_persisted_signed_zero_is_authoritative_without_global_projection(
    rig, zero, nested, dirty
):
    r = rig
    branch = {
        "zero": zero,
        "\ud800": 1.7976931348623157e308,
        "2": 2,
        "1": 1,
        "01": 1,
        "tiny": 5e-324,
    }
    base = {"box": branch, "n": 0} if nested else dict(branch, n=0)
    spec = token(initial=lambda: base)

    async def run():
        old = await create(r, spec)
        before = len(r.store.commits)

        async def edit(tx):
            d = await tx.doc(spec)
            target = d["box"] if nested else d
            target["zero"] = 1
            target["zero"] = -zero
            assert target.snapshot() != OwnedJson(branch)
            if dirty:
                d["n"] = 1

        await commit(r, edit)
        current = await snapshot(r, spec)
        expected = dict(base, n=1) if dirty else base
        assert current.value == OwnedJson(expected)
        assert (
            OwnedJson(r.inner.document(current.record.id).to_tree()["value"])
            == current.value
        )
        if dirty:
            change = docs(r.publications[-1])[0]
            assert change.value is current.value and change.ops.thaw() == [
                ["s", ["n"], 1]
            ]
            assert current.deltas_since_base == 1
        else:
            assert current is old and len(r.store.commits) == before
        # Test-only residency eviction pins this join's cold path. Actual unload
        # and its line lifecycle remain a later production slice.
        r.session._cache.clear()
        cold = await snapshot(r, spec)
        assert (
            cold.value == current.value
            and cold.deltas_since_base == current.deltas_since_base
        )

    r.mode.run(run)


def test_creation_keeps_actual_final_value_and_prepared_ops_link_to_publication(
    rig, monkeypatch
):
    r = rig
    spec = token(initial=lambda: {"zero": 0.0, "a": 1, "b": 2})
    prepared = []
    original = tx_module.Tracker.prepare

    def observe(tracker):
        result = original(tracker)
        prepared.append(result)
        return result

    monkeypatch.setattr(tx_module.Tracker, "prepare", observe)

    async def run():
        async def initial(tx):
            d = await tx.doc(spec)
            d["zero"] = 1
            d["zero"] = -0.0
            del d["a"]
            d["a"] = 1

        await commit(r, initial)
        current = await snapshot(r, spec)
        assert current.value == OwnedJson({"zero": -0.0, "b": 2, "a": 1})
        publication = docs(r.publications[-1])[0]
        assert publication.value is current.value and publication.ops.thaw() == []
        assert r.store.commits[-1][0].to_tree()["content"]["kind"] == "base"
        current = await changed(
            r, spec, lambda d: d.__setitem__("b", 3), [["s", ["b"], 3]]
        )
        publication = docs(r.publications[-1])[0]
        assert publication.ops is prepared[-1].ops
        assert publication.value is current.value is prepared[-1].replayed_value
        publication.ops.thaw().clear()
        publication.value.thaw().clear()
        assert publication.ops.thaw() == [["s", ["b"], 3]]

    r.mode.run(run)


@pytest.mark.parametrize("count", [4096, 4097])
def test_operation_limit_root_replacement_remains_delta(rig, count):
    r = rig
    spec = token(initial=dict)

    async def run():
        await create(r, spec)
        expected = {str(i): i for i in range(count)}
        ops = (
            [["s", [str(i)], i] for i in range(count)]
            if count == 4096
            else [["r", expected]]
        )
        await changed(r, spec, lambda d: d.update(expected), ops, expected)

    r.mode.run(run)


def test_reserved_root_and_dense_restored_descendants_remain_deltas(rig):
    r = rig
    spec = token(
        initial=lambda: {"__proto__": {"n": 0}, "a": [{"n": 0} for _ in range(256)]}
    )

    async def run():
        await create(r, spec)
        expected = {"__proto__": {"n": 1}, "a": [{"n": 0} for _ in range(256)]}
        await changed(
            r,
            spec,
            lambda d: d["__proto__"].__setitem__("n", 1),
            [["r", expected]],
            expected,
        )

        def restore(d):
            for child in d["a"]:
                child["n"] = 1
                child["n"] = 0

        await changed(r, spec, restore, [["p", ["a"], 0, 256, expected["a"]]], expected)

    r.mode.run(run)


def test_preparation_failure_revokes_all_documents_without_adoption_and_recovers(
    rig, monkeypatch
):
    r = rig
    one, two = token(), token(kind="second")
    escaped = []
    error = ValueError("prepare sentinel")
    original = tx_module.Tracker.prepare

    async def run():
        old_one, old_two = await create(r, one), await create(r, two)
        count = len(r.store.commits), len(r.publications)

        def fail(tracker):
            original(tracker)
            raise error

        monkeypatch.setattr(tx_module.Tracker, "prepare", fail)

        async def edit(tx):
            for spec in (one, two):
                d = await tx.doc(spec)
                d["lines"].append(1)
                escaped.extend((d, d["lines"], iter(d["lines"])))

        with pytest.raises(ValueError) as caught:
            await commit(r, edit)
        assert caught.value is error and r.session.failure is None
        assert (len(r.store.commits), len(r.publications)) == count
        assert await snapshot(r, one) is old_one and await snapshot(r, two) is old_two
        for view in escaped:
            with pytest.raises(DraftRevoked):
                next(view) if hasattr(view, "__next__") else len(view)
        monkeypatch.setattr(tx_module.Tracker, "prepare", original)
        await changed(
            r, one, lambda d: d["lines"].append(1), [["p", ["lines"], 0, 0, [1]]]
        )

    r.mode.run(run)


def test_foreign_iterators_recheck_coordinates_and_guards_without_lock_hooks(rig):
    r = rig
    spec = token(initial=lambda: {"a": [0, 1, 2]})
    seen = []

    async def run():
        await create(r, spec)

        def edit(d):
            a = d["a"]
            assert type(a) is DraftList

            def incoming():
                assert r.session._cache._lock.acquire(blocking=False)
                r.session._cache._lock.release()
                a.insert(0, 9)
                yield 4

            a[-1:] = incoming()
            assert a[:] == [9, 0, 1, 4]
            for invalid in ([3, object()], [3]):
                with pytest.raises((JsonValueError, ValueError)):
                    a[::2] = invalid
                assert a[:] == [9, 0, 1, 4]

            class Items:
                def __iter__(self):
                    class Iterator:
                        def __iter__(self):
                            return self

                        def __next__(self):
                            raise StopIteration

                        def __del__(self):
                            seen.append(a.snapshot().thaw())
                            a.insert(0, 8)

                    return Iterator()

            a[-1:] = Items()
            assert seen == [[9, 0, 1, 4]] and a[:] == [8, 9, 0, 1]
            gc.collect()

        await changed(
            r,
            spec,
            edit,
            [["p", ["a"], 2, 1, []], ["p", ["a"], 0, 0, [8, 9]]],
            {"a": [8, 9, 0, 1]},
        )

    r.mode.run(run)


def test_adapter_private_surface_and_revocation_before_thread_affinity(rig):
    r = rig
    spec = token(initial=lambda: {"a": [0]})
    kept = []

    async def run():
        async def edit(tx):
            d = await tx.doc(spec)
            kept.extend((d, d["a"]))
            for view in kept:
                assert not hasattr(view, "__dict__") and not hasattr(view, "_node")
                for action in (
                    lambda: copy.copy(view),
                    lambda: copy.deepcopy(view),
                    lambda: pickle.dumps(view),
                    lambda: view.__init__(None, None),
                ):
                    with pytest.raises(TypeError):
                        action()
                with pytest.raises(AttributeError):
                    del view._view
                with ThreadPoolExecutor(max_workers=1) as pool:
                    with pytest.raises(TxAffinityError):
                        pool.submit(len, view).result(timeout=5)

        await commit(r, edit)
        with ThreadPoolExecutor(max_workers=1) as pool:
            for view in kept:
                with pytest.raises(DraftRevoked):
                    pool.submit(len, view).result(timeout=5)

    r.mode.run(run)


@pytest.mark.parametrize("method", ["slice", "extend", "update"])
def test_empty_foreign_iterator_settlement_is_rechecked(rig, method):
    r = rig
    spec = token(initial=lambda: {"a": [0]})

    async def run():
        await create(r, spec)
        old = await snapshot(r, spec)

        async def edit(tx):
            d = await tx.doc(spec)
            a = d["a"]

            def settling():
                # Test-only lifecycle cut, as in the primitive Tracker tests.
                # Even an empty foreign iterator must not bypass the boundary.
                d._change.discard()
                return
                yield  # pragma: no cover

            if method == "slice":
                a[:] = settling()
            elif method == "extend":
                a.extend(settling())
            else:
                d.update(settling())

        with pytest.raises(DraftRevoked):
            await commit(r, edit)
        assert await snapshot(r, spec) is old
        assert r.session.failure is None

    r.mode.run(run)


def test_moved_and_removed_list_handles_keep_only_their_current_paths(rig):
    r = rig
    spec = token(initial=lambda: {"a": [{"n": 0}, {"n": 1}], "delete": 1})
    kept = []

    async def run():
        await create(r, spec)

        def edit(d):
            a = d["a"]
            first, second = a[0], a[1]
            iterator = iter(a)
            assert next(iterator) is first
            a.reverse()
            second["n"] = 2
            removed = a.pop(1)
            first["n"] = 9
            d["copy"] = first
            first["n"] = 10
            del d["delete"]
            assert removed == {"n": 0} and a[0] is second
            kept.extend((first, second, iterator))

        await changed(
            r,
            spec,
            edit,
            [
                ["s", ["copy"], {"n": 9}],
                ["d", ["delete"]],
                ["p", ["a"], 0, 1, []],
                ["s", ["a", 0, "n"], 2],
            ],
            {"a": [{"n": 2}], "copy": {"n": 9}},
        )
        for view in kept:
            with pytest.raises(DraftRevoked):
                next(view) if hasattr(view, "__next__") else view.snapshot()

    r.mode.run(run)


@pytest.mark.parametrize("accessed", [False, True], ids=["untouched", "accessed"])
def test_untouched_nested_signed_zero_and_order_survive_other_edits(rig, accessed):
    r = rig
    branch = {"negative": -0.0, "positive": 0.0, "2": 2, "1": 1, "\ud800": 5e-324}
    spec = token(initial=lambda: {"nested": branch, "n": 0})

    async def run():
        await create(r, spec)

        def edit(d):
            if accessed:
                assert d["nested"].snapshot() == OwnedJson(branch)
            d["n"] = 1

        await changed(r, spec, edit, [["s", ["n"], 1]], {"nested": branch, "n": 1})

    r.mode.run(run)

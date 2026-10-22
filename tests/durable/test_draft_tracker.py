"""Pure tracking semantics, owned ingress, and callback-local view lifetime."""

import copy
import gc
import json
import pickle
import struct
from concurrent.futures import ThreadPoolExecutor

import pytest

from pipy_harness.native.durable import draft_tracker as m
from pipy_harness.native.durable.codec import (
    JsonValueError,
    OwnedJson,
    decode_value,
    encode_value,
)


def change(base):
    tracker = m.Tracker(OwnedJson(base))
    return tracker, tracker.root


def test_numeric_string_keys_keep_native_draft_operations_and_replay_order():
    keys = ["2", "1", "01", "4294967294", "4294967295"]
    expected = dict(zip(keys, range(5), strict=True))
    tracker, draft = change({})
    for key, value in expected.items():
        draft[key] = value
    assert list(draft) == keys
    prepared = tracker.prepare()
    assert prepared.ops == OwnedJson(
        [["s", [key], value] for key, value in expected.items()]
    )
    assert prepared.replayed_value == OwnedJson(expected)
    assert list(prepared.replayed_value.thaw()) == keys
    roundtrip = decode_value(
        json.loads(json.dumps(encode_value(prepared.replayed_value)))
    )
    assert roundtrip == prepared.replayed_value


def test_numeric_delete_readd_placement_keeps_native_order_without_alias():
    base = {"1": 10, "2": 11, "4294967294": 12, "01": 13, "4294967295": 14}
    tracker, draft = change({"source": base, "target": None})
    del draft["source"]["1"]
    draft["source"]["1"] = 10
    keys = ["2", "4294967294", "01", "4294967295", "1"]
    assert list(draft["source"]) == keys
    draft["target"] = draft["source"]
    draft["target"]["1"] = 99
    assert draft["source"]["1"] == 10
    assert list(draft["target"]) == keys
    prepared = tracker.prepare()
    target = {key: 99 if key == "1" else base[key] for key in keys}
    assert prepared.ops == OwnedJson(
        [["s", ["target"], target], ["d", ["source", "1"]], ["s", ["source", "1"], 10]]
    )
    replay = prepared.replayed_value.thaw()
    assert list(replay["source"]) == list(replay["target"]) == keys
    assert list(prepared.ops.thaw()[0][2]) == keys
    replay["target"]["1"] = -1
    assert prepared.replayed_value.thaw()["target"]["1"] == 99


@pytest.mark.parametrize("array", [False, True])
def test_supplied_numeric_payload_keeps_native_order_and_is_owned(array):
    keys = ["2", "1", "01", "4294967294", "4294967295"]
    payload = dict(zip(keys, range(5), strict=True))
    tracker, draft = change({"target": [] if array else None})
    if array:
        draft["target"].append(payload)
    else:
        draft["target"] = payload
    payload.clear()
    prepared = tracker.prepare()
    raw = prepared.ops.thaw()
    placed = raw[0][4][0] if array else raw[0][2]
    assert list(placed) == keys
    replay = prepared.replayed_value.thaw()["target"]
    assert list(replay[0] if array else replay) == keys
    assert OwnedJson(placed) == OwnedJson(dict(zip(keys, range(5), strict=True)))


def test_restore_cancels_but_structural_identity_survives():
    tracker, draft = change({"n": 0, "a": [1, 2]})
    draft["n"] = 1
    draft["n"] = 0
    draft["a"].append(3)
    draft["a"].pop()
    assert tracker.prepare().ops.thaw() == []
    tracker, draft = change({"a": [1, 2]})
    value = draft["a"].pop(0)
    draft["a"].insert(0, value)
    prepared = tracker.prepare()
    assert prepared.ops.thaw() == [["p", ["a"], 0, 1, []], ["p", ["a"], 0, 0, [1]]]
    assert prepared.replayed_value.thaw() == {"a": [1, 2]}


def test_permutation_removal_insertion_and_override_order():
    tracker, draft = change({"a": [0, 1, 2, 3]})
    values = draft["a"]
    values[1] = 10
    del values[2]
    values.reverse()
    values.insert(1, 9)
    assert tracker.prepare().ops.thaw() == [
        ["p", ["a"], 2, 1, []],
        ["m", ["a"], [2, 1, 0]],
        ["p", ["a"], 1, 0, [9]],
        ["s", ["a", 2], 10],
    ]


def test_equal_reverse_and_canceled_reverse():
    tracker, draft = change({"a": [1, 1]})
    draft["a"].reverse()
    assert tracker.prepare().ops.thaw() == [["m", ["a"], [1, 0]]]
    tracker, draft = change({"a": [1, 1]})
    draft["a"].reverse()
    draft["a"].reverse()
    assert tracker.prepare().ops.thaw() == []


def test_held_reindexed_detached_and_placed_descendants():
    tracker, draft = change({"a": [{"n": 0}, {"n": 1}], "b": {}})
    first, second = draft["a"]
    draft["a"].reverse()
    first["n"] = 7
    draft["a"].pop(0)
    second["n"] = 99
    draft["b"] = second
    second["n"] = 100
    assert tracker.prepare().ops.thaw() == [
        ["s", ["b"], {"n": 99}],
        ["p", ["a"], 1, 1, []],
        ["s", ["a", 0, "n"], 7],
    ]


def test_equal_placement_detaches_old_handle_and_retains_baseline_order():
    tracker, draft = change({"v": {"a": 1, "b": 2}, "x": 0})
    old = draft["v"]
    draft["v"] = {"b": 2, "a": 1}
    old["a"] = 99
    draft["x"] = 1
    prepared = tracker.prepare()
    assert prepared.ops.thaw() == [["s", ["x"], 1]]
    assert list(prepared.replayed_value.thaw()["v"]) == ["a", "b"]
    tracker, draft = change({"a": [{"n": 0}]})
    held = draft["a"][0]
    draft["a"][0] = {"n": 0}
    held["n"] = 9
    assert tracker.prepare().ops.thaw() == []


def test_delete_readd_and_peer_dirty_order():
    tracker, draft = change({"first": {"n": 0}, "second": {"n": 0}, "x": 1, "y": 2})
    draft["second"]["n"] = 1
    draft["first"]["n"] = 2
    del draft["x"]
    draft["x"] = 1
    assert tracker.prepare().ops.thaw() == [
        ["d", ["x"]],
        ["s", ["x"], 1],
        ["s", ["second", "n"], 1],
        ["s", ["first", "n"], 2],
    ]


@pytest.mark.parametrize("key", ["__proto__", "constructor", "prototype"])
def test_reserved_restoring_mutation_still_folds(key):
    tracker, draft = change({"v": {key: 0, "ordinary": 1}})
    draft["v"][key] = 1
    draft["v"][key] = 0
    assert tracker.prepare().ops.thaw() == [["s", ["v"], {key: 0, "ordinary": 1}]]
    tracker, draft = change({key: {"n": 0}})
    draft[key]["n"] = 1
    assert tracker.prepare().ops.thaw() == [["r", {key: {"n": 1}}]]
    tracker, draft = change({key: 0, "n": 0})
    draft["n"] = 1
    assert tracker.prepare().ops.thaw() == [["s", ["n"], 1]]


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("abc", "abcdef", [["a", ["s"], "def"]]),
        ("abcde", "cdef", [["t", ["s"], 2], ["a", ["s"], "f"]]),
        ("\U0001f600x", "\ude00xy", [["t", ["s"], 1], ["a", ["s"], "y"]]),
        ("same", "other", [["s", ["s"], "other"]]),
    ],
)
def test_utf16_strings(before, after, expected):
    tracker, draft = change({"s": before})
    draft["s"] = after
    assert tracker.prepare().ops.thaw() == expected


@pytest.mark.parametrize("count", [255, 256, 257])
def test_dense_restored_descendants_vs_direct_scalars(count):
    tracker, draft = change({"a": [{"n": 0} for _ in range(count)]})
    for child in draft["a"]:
        child["n"] = 1
        child["n"] = 0
    expected = (
        []
        if count < 256
        else [["p", ["a"], 0, count, [{"n": 0} for _ in range(count)]]]
    )
    assert tracker.prepare().ops.thaw() == expected
    tracker, draft = change({"a": [0] * count})
    for i in range(count):
        draft["a"][i] = 1
        draft["a"][i] = 0
    assert tracker.prepare().ops.thaw() == []


def test_structural_history_disables_dense_even_when_canceled():
    tracker, draft = change({"a": [{"n": 0} for _ in range(256)]})
    for child in draft["a"]:
        child["n"] = 1
        child["n"] = 0
    draft["a"].reverse()
    draft["a"].reverse()
    assert tracker.prepare().ops.thaw() == []


@pytest.mark.parametrize("count", [4096, 4097])
def test_operation_limit(count):
    tracker, draft = change({})
    for i in range(count):
        draft[str(i)] = i
    ops = tracker.prepare().ops.thaw()
    assert (len(ops), ops[0][0]) == ((count, "s") if count == 4096 else (1, "r"))


def test_mapping_methods_and_detached_returns():
    tracker, draft = change({"a": {"n": 0}})
    held = draft["a"]
    removed = draft.pop("a")
    held["n"] = 9
    assert removed == {"n": 0}
    assert draft.pop("missing", 8) == 8
    draft.update({"b": 2, "c": 3})
    assert draft.setdefault("b", 9) == 2
    assert draft.popitem() == ("c", 3)
    assert tracker.prepare().ops.thaw() == [["s", ["b"], 2], ["d", ["a"]]]
    tracker, draft = change({"a": 1, "b": 2})
    draft.clear()
    assert tracker.prepare().ops.thaw() == [["d", ["a"]], ["d", ["b"]]]


def test_list_methods_slices_and_per_placement_copy():
    tracker, draft = change({"a": [0, 1, 2, 3, 4]})
    values = draft["a"]
    values[::-2] = [8, 7, 6]
    assert values[:] == [6, 1, 7, 3, 8]
    del values[::2]
    values.extend(values)
    values.remove(3)
    values.insert(-1, {"n": 0})
    popped = values.pop(-2)
    assert popped == {"n": 0}
    assert values[:] == [1, 1, 3]
    prepared = tracker.prepare()
    assert prepared.ops.thaw() == [
        ["p", ["a"], 2, 3, []],
        ["p", ["a"], 0, 1, []],
        ["p", ["a"], 1, 0, [1, 3]],
    ]
    assert prepared.replayed_value.thaw() == {"a": [1, 1, 3]}
    tracker, draft = change({"a": [{"n": 0}]})
    values = draft["a"]
    values.extend(values)
    values[0]["n"] = 1
    assert values[1]["n"] == 0
    snapshot = values[:]
    snapshot[0]["n"] = 9
    assert values[0]["n"] == 1


def test_bad_slice_input_is_atomic_and_reentrant_settlement_revokes():
    tracker, draft = change({"a": [0, 1, 2]})
    values = draft["a"]
    for incoming in ([3, object()], [3]):
        with pytest.raises((JsonValueError, ValueError)):
            values[::2] = incoming
        assert values[:] == [0, 1, 2]

    def settle():
        tracker.discard()
        yield 3

    with pytest.raises(m.TrackerRevoked):
        values[:] = settle()
    with pytest.raises(m.TrackerRevoked):
        values.append(9)


def test_empty_iterable_settlement_is_rechecked():
    tracker, draft = change({"a": [0]})
    values = draft["a"]

    def settle():
        tracker.discard()
        return
        yield  # pragma: no cover - make this an empty settling iterator

    with pytest.raises(m.TrackerRevoked):
        values[:] = settle()


def test_iterable_reentry_uses_post_admission_coordinates():
    tracker, draft = change({"a": [0, 1, 2]})
    values = draft["a"]

    def source():
        values.clear()
        yield 4

    values[1:] = source()
    assert tracker.prepare().replayed_value.thaw() == {"a": [4]}


def test_exact_ingress_never_calls_foreign_hooks():
    class Foreign:
        @property  # type: ignore[misc]  # hostile exact-type admission probe
        def __class__(self):
            raise AssertionError("class")

        def __hash__(self):
            raise AssertionError("hash")

        def __eq__(self, other):
            raise AssertionError("equality")

        def __index__(self):
            raise AssertionError("index")

    class String(str):
        pass

    tracker, draft = change({"a": [0]})
    foreign = Foreign()
    for value in (foreign, String("s"), [foreign]):
        with pytest.raises(JsonValueError):
            draft["bad"] = value
    for key in (foreign, String("s")):
        with pytest.raises(TypeError):
            draft[key] = 1
    for index in (foreign, True, slice(foreign), slice(None, None, True)):
        with pytest.raises(TypeError):
            draft["a"][index] = 1
    assert tracker.prepare().ops.thaw() == []


def test_duplicate_utf16_key_admission_and_scalar_number_identity():
    tracker, draft = change({"\U0001f600": 1, "n": 0, "b": False})
    with pytest.raises(JsonValueError):
        draft["\ud83d\ude00"] = 2
    draft["n"] = -0.0
    draft["b"] = 0
    prepared = tracker.prepare()
    assert prepared.ops.thaw() == [["s", ["b"], 0]]
    assert struct.pack(">d", prepared.replayed_value.thaw()["n"]) == bytes(8)


def test_foreign_comparisons_only_receive_detached_operands():
    tracker, draft = change({"a": [{"n": 0}]})

    class Compare:
        def __eq__(self, other):
            if type(other) is dict:
                other["n"] = 99
            elif type(other) is list:
                other.clear()
            return False

    other = Compare()
    assert draft["a"] != other
    assert other not in draft["a"]
    assert draft["a"].count(other) == 0
    with pytest.raises(ValueError):
        draft["a"].index(other)
    assert draft["a"][0]["n"] == 0
    assert tracker.prepare().ops.thaw() == []


def test_preparation_revokes_every_view_even_when_validation_fails(monkeypatch):
    tracker, draft = change({"a": [{"n": 0}]})
    values, child = draft["a"], draft["a"][0]
    iterator = iter(values)

    def fail(_):
        raise ValueError("injected validation failure")

    monkeypatch.setattr(m, "own_operations", fail)
    with pytest.raises(ValueError):
        tracker.prepare()
    for operation in (
        lambda: len(draft),
        lambda: values.clear(),
        lambda: child.snapshot(),
        lambda: next(iterator),
    ):
        with pytest.raises(m.TrackerRevoked):
            operation()


def test_owned_output_and_copied_cross_context_placements():
    seed = {"n": [0]}
    tracker, draft = change(seed)
    seed["n"].append(1)
    other, source = change({"a": {"n": 2}})
    draft["left"] = source["a"]
    draft["right"] = source["a"]
    source["a"]["n"] = 9
    draft["left"]["n"] = 3
    prepared = tracker.prepare()
    assert prepared.replayed_value.thaw() == {
        "n": [0],
        "left": {"n": 3},
        "right": {"n": 2},
    }
    escaped = prepared.ops.thaw()
    escaped.clear()
    assert len(prepared.ops.thaw()) == 2
    other.discard()
    fresh, target = change({})
    with pytest.raises(m.TrackerRevoked):
        target["bad"] = source
    assert fresh.prepare().ops.thaw() == []


def test_sealed_views_and_revoked_before_affinity():
    tracker, draft = change({"a": []})
    for view in (draft, draft["a"]):
        for operation in (
            lambda: copy.copy(view),
            lambda: copy.deepcopy(view),
            lambda: pickle.dumps(view),
            lambda: view.__getstate__(),
        ):
            with pytest.raises(TypeError):
                operation()
        with pytest.raises(AttributeError):
            view._node = None
        with pytest.raises(AttributeError):
            del view._node
        with pytest.raises(TypeError):
            view.__init__(view._node)
        assert not hasattr(view, "__dict__")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="owning thread"):
            pool.submit(len, draft).result(timeout=5)
        tracker.prepare()
        with pytest.raises(m.TrackerRevoked):
            pool.submit(len, draft).result(timeout=5)


def test_foreign_iterator_finalizer_runs_before_application_and_recheck():
    tracker, draft = change({"a": [0]})
    held = draft["a"]
    seen = []

    class Items:
        def __iter__(self):
            class Iterator:
                def __iter__(self):
                    return self

                def __next__(self):
                    raise StopIteration

                def __del__(self):
                    seen.append(held.snapshot().thaw())
                    tracker.discard()

            return Iterator()

    with pytest.raises(m.TrackerRevoked):
        held[:] = Items()
    gc.collect()
    assert seen == [[0]]


def test_long_array_append_keeps_metadata_sparse():
    tracker, draft = change({"a": list(range(100_000))})
    values = draft["a"]
    for n in range(1000):
        values.append(n)
    assert len(values._array.entries) == 0
    assert len(values._array.pieces) == 2
    assert len(tracker._views) == 2
    prepared = tracker.prepare()
    assert prepared.ops.thaw() == [["p", ["a"], 100_000, 0, list(range(1000))]]


def test_deep_values_and_paths_have_no_recursion_cutoff():
    tree = {"leaf": 0}
    for _ in range(1600):
        tree = {"next": tree}
    tracker, draft = change(tree)
    child = draft
    for _ in range(1600):
        child = child["next"]
    child["leaf"] = 1
    prepared = tracker.prepare()
    assert prepared.ops.thaw() == [["s", ["next"] * 1600 + ["leaf"], 1]]
    tracker, draft = change({"v": {}})
    draft["v"] = tree
    assert len(tracker._views) == 1
    assert tracker.prepare().ops.thaw()[0][:2] == ["s", ["v"]]


def test_guard_reentry_settlement_does_not_escape_a_view():
    tracker = None

    def guard():
        tracker.discard()

    tracker = m.Tracker(OwnedJson({}), guard)
    with pytest.raises(m.TrackerRevoked):
        _ = tracker.root


def test_inherited_helpers_and_iterators_keep_existing_surface():
    tracker, draft = change({"a": [0, 1], "m": {"x": 1}})
    values = draft["a"]
    values += [2]
    assert list(reversed(values)) == [2, 1, 0]
    assert values.index(2, -2) == 2
    assert values.count(1) == 1
    draft["m"].update([("y", 2)], z=3)
    assert list(draft["m"].items()) == [("x", 1), ("y", 2), ("z", 3)]
    keys = iter(draft["m"])
    items = iter(values)
    assert tracker.prepare().ops.thaw() == [
        ["p", ["a"], 2, 0, [2]],
        ["s", ["m", "y"], 2],
        ["s", ["m", "z"], 3],
    ]
    for iterator in (keys, items):
        with pytest.raises(m.TrackerRevoked):
            next(iterator)


def test_cross_thread_discard_cannot_revoke_owner():
    tracker, draft = change({"n": 0})
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="owning thread"):
            pool.submit(tracker.discard).result(timeout=5)
    draft["n"] = 1
    assert tracker.prepare().ops.thaw() == [["s", ["n"], 1]]

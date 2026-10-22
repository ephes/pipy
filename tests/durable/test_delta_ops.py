"""Pure decoded-operation contract; no Session or Storage integration."""

import math

import pytest

from pipy_harness.native.durable.codec import JsonValueError, OwnedJson
from pipy_harness.native.durable.delta_ops import (
    DeltaPathError,
    DeltaUnsafePath,
    apply_operations,
    own_operations,
)


def run(value, operations):
    return apply_operations(OwnedJson(value), own_operations(operations)).thaw()


@pytest.mark.parametrize(
    ("value", "operations", "expected"),
    [
        (None, [["r", {"x": 1}]], {"x": 1}),
        ({}, [["s", ["x"], 2]], {"x": 2}),
        ({"x": 1}, [["d", ["x"]], ["d", ["absent"]]], {}),
        ({"x": "a"}, [["a", ["x"], "b"]], {"x": "ab"}),
        ({"x": "😀ab"}, [["t", ["x"], 1]], {"x": "\ude00ab"}),
        ({"x": "😀ab"}, [["t", ["x"], 2]], {"x": "ab"}),
        ([1, 2, 3], [["p", [], 1, 1, [4, 5]]], [1, 4, 5, 3]),
        ([1, 2, 3], [["m", [], [2, 0, 1]]], [3, 1, 2]),
        ({"xs": [1]}, [["s", ["xs", 1.0], 2]], {"xs": [1, 2]}),
        ({"xs": [1, 2]}, [["d", ["xs", 0]]], {"xs": [2]}),
        ({"1": []}, [["p", [1], 0, 0, [2]]], {"1": [2]}),
        (
            {},
            [["r", {"__proto__": {"constructor": 1}}]],
            {"__proto__": {"constructor": 1}},
        ),
        ({"x": "abc"}, [["t", ["x"], 1e300]], {"x": ""}),
        ([1], [["p", [], 1e300, 1e300, [2]]], [1, 2]),
        ({"😀": 1}, [["s", ["\ud83d\ude00"], 2]], {"😀": 2}),
        ({}, [["s", [1e20], 1]], {"100000000000000000000": 1}),
        ({}, [["s", [1e21], 1]], {"1e+21": 1}),
        (0, [], 0),
        ({}, [["r", [1]], ["p", [], 0, 0, [2]], ["r", None]], None),
    ],
)
def test_exact_decoded_application(value, operations, expected):
    assert run(value, operations) == expected


@pytest.mark.parametrize(
    "operation",
    [
        None,
        [],
        ["#", 0, []],
        ["unknown"],
        ["r"],
        ["r", 1, 2],
        ["s", 1],
        ["s", "x", 1],
        ["s", [], 1],
        ["d", []],
        ["a", ["x"], 2],
        ["t", ["x"], -1],
        ["t", ["x"], 0.5],
        ["p", [], -1, 0, []],
        ["p", [], 0, -1, []],
        ["p", [], 0, 0, {}],
        ["m", [], {}],
        ["m", [], [0, 0]],
        ["m", [], [1]],
        ["m", [], [-1]],
        ["s", [True], 0],
        ["s", [-1], 0],
        ["s", [0.5], 0],
        ["s", ["__proto__"], 0],
        ["d", ["constructor"]],
        ["p", ["prototype"], 0, 0, []],
    ],
)
def test_invalid_decoded_grammar(operation):
    with pytest.raises((TypeError, DeltaUnsafePath)):
        own_operations([operation])


@pytest.mark.parametrize(
    ("value", "operation", "error"),
    [
        ({}, ["s", ["missing", "x"], 1], DeltaPathError),
        ([], ["s", [2], 1], DeltaUnsafePath),
        ([], ["s", [1e300], 1], DeltaUnsafePath),
        ([], ["s", ["0"], 1], DeltaUnsafePath),
        ([1], ["d", [1]], DeltaPathError),
        ({}, ["a", ["x"], "a"], DeltaPathError),
        ({"x": 1}, ["t", ["x"], 0], DeltaPathError),
        ({}, ["p", [], 0, 0, []], DeltaPathError),
        ([1], ["m", [], []], DeltaPathError),
        ({"x": 1}, ["s", ["x", "y"], 2], DeltaPathError),
    ],
)
def test_unresolved_and_array_boundaries(value, operation, error):
    with pytest.raises(error):
        run(value, [operation])


def test_owned_atomicity_late_invalid_and_detached_outputs():
    caller = {"xs": [{"n": 1}]}
    payload = {"n": 2}
    raw = [["s", ["xs", 0], payload]]
    value, batch = OwnedJson(caller), own_operations(raw)
    payload["n"] = 9
    caller["xs"].clear()
    first = apply_operations(value, batch)
    assert first.thaw() == {"xs": [{"n": 2}]}
    escaped = first.thaw()
    escaped["xs"][0]["n"] = 7
    assert first.thaw() == {"xs": [{"n": 2}]}
    for invalid in (["m", [], [1]], ["s", ["xs", 8], 2]):
        bad = OwnedJson(raw + [invalid])
        before = bad.thaw()
        with pytest.raises((TypeError, DeltaUnsafePath)):
            apply_operations(value, bad)
        assert bad.thaw() == before
        assert value.thaw() == {"xs": [{"n": 1}]}
        assert first.thaw() == {"xs": [{"n": 2}]}


def test_strict_owned_ingress_no_foreign_hooks_or_nonfinite_values():
    class Foreign(list):
        def __iter__(self):
            pytest.fail("foreign hook")

    for value in (Foreign(), [["r", math.inf]], [["r", 2**53 + 1]]):
        with pytest.raises(JsonValueError):
            own_operations(value)
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(JsonValueError):
        own_operations(cyclic)


def test_application_preserves_first_error_while_grammar_preparation_is_separate():
    malformed = [["s", ["missing", "x"], 1], ["r"]]
    with pytest.raises(TypeError):
        own_operations(malformed)
    with pytest.raises(DeltaPathError):
        apply_operations(OwnedJson({}), OwnedJson(malformed))
    with pytest.raises(TypeError):
        apply_operations(OwnedJson({}), OwnedJson(list(reversed(malformed))))


@pytest.mark.parametrize(
    ("text", "operation", "expected"),
    [
        ("x\ud83d", ["a", ["text"], "\ude00"], "x😀"),
        ("x\ud83d\ude00", ["a", ["text"], ""], "x😀"),
        ("x\ud83d\ude00", ["t", ["text"], 0], "x😀"),
        ("x\ud83d", ["a", ["text"], ""], "x\ud83d"),
        ("x\ude00", ["t", ["text"], 0], "x\ude00"),
        ("\ud83d\ude00", ["t", ["text"], 1], "\ude00"),
    ],
)
def test_touched_string_results_use_consistent_python_spelling(
    text, operation, expected
):
    source = OwnedJson({"text": text, "untouched": "\ud83d\ude00"})
    batch = own_operations([operation])
    result = apply_operations(source, batch)
    assert result == OwnedJson({"text": expected, "untouched": "\ud83d\ude00"})
    assert result.thaw()["text"] == expected
    assert source.thaw() == {"text": text, "untouched": "\ud83d\ude00"}
    assert batch.thaw() == [operation]
    if "😀" in expected:
        assert result.thaw()["text"].encode("utf-8") == expected.encode("utf-8")
    assert result.thaw()["untouched"] != "😀"

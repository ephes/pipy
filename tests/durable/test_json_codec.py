"""Product JSON ownership and full binary64 fidelity; no Node/runtime dependency."""

import math
import struct

import pytest

from pipy_harness.native.durable.codec import (
    JsonValueError,
    OwnedJson,
    decode_value,
    encode_value,
)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "",
        "\ud800",
        "\ud801",
        0,
        2**53,
        2**60,
        1.5,
        -0.0,
        5e-324,
        1.7976931348623157e308,
        float(2**60),
    ],
)
def test_full_finite_domain_roundtrip(value):
    result = decode_value(encode_value(OwnedJson(value))).thaw()
    assert type(result) is type(value)
    if type(value) is float:
        assert struct.pack(">d", result) == struct.pack(">d", value)
    else:
        assert result == value


@pytest.mark.parametrize(
    "value",
    [
        2**53 + 1,
        10**400,
        float("nan"),
        float("inf"),
        -float("inf"),
        {1: "x"},
        (1,),
        {1},
        object(),
    ],
)
def test_invalid_values_fail_closed(value):
    with pytest.raises(JsonValueError):
        OwnedJson(value)


def test_repeated_aliases_are_independent_and_every_decode_owns_its_tree():
    shared = {"items": [1]}
    value = OwnedJson({"left": shared, "right": shared})
    shared["items"].append(2)
    decoded = value.thaw()
    decoded["left"]["items"].append(3)
    assert decoded["right"] == {"items": [1]}
    assert value.thaw() == {"left": {"items": [1]}, "right": {"items": [1]}}
    encoded = encode_value(value)
    other = decode_value(encoded)
    encoded["value"]["left"]["items"].clear()
    assert other.thaw() == value.thaw()
    assert "items" not in repr(value)


def test_cycles_reject_and_acyclic_depth_has_no_arbitrary_256_cutoff():
    cycle = []
    cycle.append(cycle)
    with pytest.raises(JsonValueError, match="cycle"):
        OwnedJson(cycle)
    tree = {"leaf": "\ud800"}
    for _ in range(1500):
        tree = [tree]
    copy = decode_value(encode_value(OwnedJson(tree))).thaw()
    for _ in range(1500):
        copy = copy[0]
    assert copy == {"leaf": "\ud800"}


def test_plain_container_boundary_never_calls_subclass_hooks():
    calls = []

    class Hostile(dict):
        def items(self):
            calls.append("items")
            raise AssertionError("must not run")

    class HostileList(list):
        def __iter__(self):
            calls.append("iter")
            raise AssertionError("must not run")

    for item in (Hostile(), HostileList()):
        with pytest.raises(JsonValueError):
            OwnedJson(item)
    assert calls == []


@pytest.mark.parametrize(
    "envelope",
    [
        {},
        {"schema_version": True, "value": None},
        {"schema_version": 1.0, "value": None},
        {"schema_version": 2, "value": None},
        {"schema_version": 1, "value": math.inf},
        {"schema_version": 1, "value": None, "extra": 1},
    ],
)
def test_versioned_decode_validates_shape_numbers_and_exact_version(envelope):
    with pytest.raises(JsonValueError):
        decode_value(envelope)


def test_bool_is_not_equal_to_numeric_one_and_strings_are_lossless():
    assert OwnedJson(True) != OwnedJson(1)
    value = dict(
        [
            ("__proto__", {"constructor": "\ud800"}),
            ("\ud800", 1),
            ("\ud801", 2),
            ("", None),
        ]
    )
    assert OwnedJson(value).thaw() == value


@pytest.mark.parametrize("boundary", ["value", "versioned-value", "record"])
def test_dual_utf16_equivalent_keys_have_no_lossless_js_object_value(boundary):
    from pipy_harness.native.durable.records import EntryRecord

    value = dict([("\U0001f600", 1), ("\ud83d\ude00", 2)])
    assert len(value) == 2
    with pytest.raises(JsonValueError, match="distinct UTF-16"):
        if boundary == "value":
            OwnedJson(value)
        elif boundary == "versioned-value":
            decode_value({"schema_version": 1, "value": value})
        else:
            EntryRecord({"id": 2, "conversationId": 1, "kind": "", "data": value})


@pytest.mark.parametrize("key", ["\U0001f600", "\ud83d\ude00", "\ud800", "\ud801"])
def test_single_utf16_key_and_string_spellings_remain_lossless(key):
    original = {key: key}
    result = decode_value(encode_value(OwnedJson(original))).thaw()
    assert list(result) == [key]
    assert result[key] == key
    assert next(iter(result)).encode("utf-16-be", "surrogatepass") == key.encode(
        "utf-16-be", "surrogatepass"
    )


def test_value_decode_rejects_nonplain_envelope_keys_without_hash_or_equality():
    calls = []

    class Key(str):
        def __hash__(self):
            calls.append("hash")
            return super().__hash__()

        def __eq__(self, other):
            calls.append("eq")
            return super().__eq__(other)

    tree = {"schema_version": 1, Key("value"): None}
    calls.clear()
    with pytest.raises(JsonValueError, match="exact string keys"):
        decode_value(tree)
    assert calls == []


@pytest.mark.parametrize("left,right", [(-0.0, 0.0), (1, 1.0), (0, 0.0)])
def test_owned_equality_and_hash_preserve_numeric_representation(left, right):
    a = OwnedJson({"nested": [left]})
    same = OwnedJson({"nested": [left]})
    b = OwnedJson({"nested": [right]})
    assert a == same and hash(a) == hash(same)
    assert a != b
    assert len({a, same, b}) == 2


def test_owned_equality_is_order_and_python_string_representation_sensitive():
    assert OwnedJson({"a": 1, "b": 2}) != OwnedJson({"b": 2, "a": 1})
    assert OwnedJson("\U0001f600") != OwnedJson("\ud83d\ude00")
    assert OwnedJson(True) != OwnedJson(1)


def test_durable_and_agent_details_share_the_same_owned_json_implementation():
    from pipy_harness.native.agent import json_values, tool_details
    from pipy_harness.native.durable import codec

    assert codec.OwnedJson is json_values.OwnedJson
    assert codec.JsonValueError is json_values.JsonValueError
    assert tool_details.copy_normal_json is json_values.copy_normal_json
    assert codec.encode_value is json_values.encode_value
    assert codec.decode_value is json_values.decode_value
    value = {"actual": [None, False, -0.0]}
    detached = tool_details.copy_tool_details(value)
    value["actual"].append("late")
    assert codec.OwnedJson(detached) == codec.decode_value(
        codec.encode_value(json_values.OwnedJson(detached))
    )
    assert len(detached["actual"]) == 3

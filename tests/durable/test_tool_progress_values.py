"""Progress capacity is bounded before ownership, without historical limits."""

import json

import pytest

from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.tool_progress_values import (
    DIAGNOSTIC_CHARACTERS,
    Diagnostic,
    ProgressRefused,
    Tail,
    capture_json,
    diagnostics,
)


@pytest.mark.parametrize(
    "value",
    [None, False, 0, 1.5, {}, [], [1, 2], {"a": [1, {"b": "\ud83d\ude00"}]}],
)
def test_capture_accounts_exact_compact_ensure_ascii_bytes(value):
    captured = capture_json(value)
    assert captured.encoded_bytes == len(
        json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    )
    assert captured.value == OwnedJson(value)


def test_capture_preserves_deep_owned_domain_and_detaches():
    value = {"key": "\ud83d\ude00"}
    for _ in range(1500):
        value = [value]
    captured = capture_json(value)
    assert captured.nodes == 1503
    value[0] = "changed"
    output = captured.value.thaw()
    for _ in range(1500):
        output = output[0]
    assert output == {"key": "\ud83d\ude00"}


@pytest.mark.parametrize("value", [(1,), 9007199254740993, float("inf"), {1: 2}])
def test_progress_keeps_strict_domain(value):
    with pytest.raises(ProgressRefused) as caught:
        capture_json(value)
    assert caught.value.category == "invalid_progress"


def test_capacity_before_encoding_or_expanding(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("oversized input must refuse before scalar encoding")

    monkeypatch.setattr(json, "dumps", forbidden)
    for value, limits in [
        ("x" * 1025, {"max_bytes": 1024}),
        ([None] * 100, {"max_nodes": 99}),
        ({str(i): None for i in range(100)}, {"max_nodes": 199}),
    ]:
        with pytest.raises(ProgressRefused) as caught:
            capture_json(value, **limits)
        assert caught.value.category == "progress_capacity"


def test_escaped_string_limit_and_duplicate_utf16_keys():
    with pytest.raises(ProgressRefused) as caught:
        capture_json("\x00" * 100, max_bytes=200)
    assert caught.value.category == "progress_capacity"
    with pytest.raises(ProgressRefused) as caught:
        capture_json({"\ud83d\ude00": 1, "\U0001f600": 2})
    assert caught.value.category == "invalid_progress"


def test_ancestor_cycle_refuses_but_dag_is_detached():
    shared = [1]
    copied = capture_json([shared, shared]).value.thaw()
    assert copied == [[1], [1]] and copied[0] is not copied[1]
    shared.append(shared)
    with pytest.raises(ProgressRefused) as caught:
        capture_json(shared)
    assert caught.value.category == "invalid_progress"


def test_diagnostic_reserves_output_and_fixed_error_before_admission():
    first = Diagnostic("warning", "\U0001f600" * (DIAGNOSTIC_CHARACTERS - 20), "x")
    assert diagnostics([first.tree()]) == (first,)
    with pytest.raises(ProgressRefused) as caught:
        diagnostics([first.tree(), Diagnostic("error", "last" * 20).tree()])
    assert caught.value.category == "progress_capacity"


@pytest.mark.parametrize(
    "value",
    [
        "line\n" * 2500 + "last",
        "\U0001f600" * 20000 + "\nlast\n",
        "x" * 160000,
        "x\n" * 10000,
    ],
)
def test_tail_keeps_stream_continuity_and_exact_observed_drop_counts(value):
    from pipy_harness.native.tools.truncate import truncate_tail

    tail = Tail()
    for start in range(0, len(value), 997):
        tail = tail.append(value[start : start + 997])
    expected = truncate_tail(value)
    assert tail.text == expected.content
    assert tail.dropped_bytes == expected.total_bytes - expected.output_bytes
    assert tail.dropped_lines == expected.total_lines - expected.output_lines


def test_bounded_forwarding_preserves_normal_then_json_text_semantics():
    from pipy_harness.native.agent.tool_details import NO_TOOL_DETAILS
    from pipy_harness.native.durable.tool_progress_values import (
        capture_forwarded_details,
    )
    from pipy_harness.native.tools.base import json_safe_details

    class String(str):
        def __str__(self):
            raise AssertionError("subclass presentation is not JSON semantics")

    class Integer(int):
        def __repr__(self):
            raise AssertionError("subclass presentation is not JSON semantics")

    shared = [String("\ud83d\ude00"), Integer(9007199254740993)]
    value = {String("\ud83d\ude00"): (shared,), "😀": [2]}
    captured = capture_forwarded_details(value)
    assert captured == json_safe_details(value)
    assert captured == {"😀": [2]}
    shared.append(3)
    assert captured == {"😀": [2]}
    assert capture_forwarded_details(object()) is NO_TOOL_DETAILS
    assert capture_forwarded_details({"bad": object()}) is NO_TOOL_DETAILS


def test_bounded_forwarding_refuses_before_wide_iteration_or_encoding(monkeypatch):
    from pipy_harness.native.durable.tool_progress_values import (
        capture_forwarded_details,
    )

    class Wide(list):
        def __iter__(self):
            pytest.fail("wide container was expanded before capacity refusal")

    with pytest.raises(ProgressRefused, match="progress_capacity"):
        capture_forwarded_details(Wide([None] * 32768))

    def forbidden(*args, **kwargs):
        pytest.fail("huge value was encoded before capacity refusal")

    monkeypatch.setattr(json, "dumps", forbidden)
    for value in ("x" * (512 * 1024), 1 << (512 * 1024 * 4 + 4)):
        with pytest.raises(ProgressRefused, match="progress_capacity"):
            capture_forwarded_details(value)

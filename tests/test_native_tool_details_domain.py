"""Normal JSON snapshots preserve legacy domains before strict durable ingress."""

import json
from types import SimpleNamespace

import pytest

from pipy_harness.extensions import ToolResult
from pipy_harness.native.agent.content import ProductContent, ToolPartialResult
from pipy_harness.native.agent.loop import _transform_tool_result
from pipy_harness.native.agent.loop_policy import AgentToolResultTransform
from pipy_harness.native.agent.messages import AgentToolCall
from pipy_harness.native.agent.tools import ToolExecutor
from pipy_harness.native.automation.serialize import (
    partial_result_json,
    serialize_message,
    tool_result_json,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
)


@pytest.mark.parametrize(
    "details",
    [{"items": (1, 2)}, {"n": 9007199254740993}],
    ids=["tuple-under-mapping", "nonexact-binary64-int"],
)
def test_existing_normal_details_json_domain(details, tmp_path):
    expected = json.loads(json.dumps(details, allow_nan=False))

    def wire(value):
        return json.loads(json.dumps(value, allow_nan=False))

    direct = ToolExecutionResult("pipy-tool-direct", "x", details=details)
    assert wire(direct.details) == expected
    assert (
        wire(partial_result_json(ToolPartialResult("partial", details)))["details"]
        == expected
    )

    class Port:
        definition = ToolDefinition("normal", "Normal details", {"type": "object"})

        def invoke(self, request, context):
            return ToolExecutionResult(
                request.tool_request_id,
                "result",
                details=details,
                provider_correlation_id=request.provider_correlation_id,
            )

    call = AgentToolCall("normal-call", "normal", ProductContent("{}"))
    result = (
        ToolExecutor({"normal": Port()}).execute(call, ToolContext(tmp_path)).result
    )
    transform = AgentToolResultTransform(ProductContent("changed"), details=details)
    policy = SimpleNamespace(transform_result=lambda *args: transform)
    changed = _transform_tool_result(policy, call, result, {}, None)
    assert wire(tool_result_json(changed))["details"] == expected
    # Mapping details stay on execution-end and JSONL, not this event message.
    assert "details" not in serialize_message(changed)
    sdk_result = ToolResult(content="extension", details=details)
    assert wire(sdk_result.details) == expected
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "session")
    tree.append_message(changed)
    assert (
        json.loads(tree.path.read_text().splitlines()[-1])["message"]["details"]
        == expected
    )


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    for key in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(key, str(tmp_path / key))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))


@pytest.mark.parametrize(
    "value",
    [None, False, 0, "scalar", [1, None], {"items": (1, 2)}, {"n": 9007199254740993}],
)
def test_normal_snapshot_has_no_durable_numeric_domain(value):
    from pipy_harness.native.agent.tool_details import copy_tool_details

    assert copy_tool_details(value) == json.loads(json.dumps(value, allow_nan=False))


def test_normal_snapshot_detaches_normalized_tuple_descendants_and_numeric_types():
    import math

    from pipy_harness.native.agent.tool_details import copy_tool_details

    source = {"items": ([1, 1.0, -0.0],), "large": 9007199254740993}
    first = copy_tool_details(source)
    source["items"][0].append(2)
    assert first == {"items": [[1, 1.0, -0.0]], "large": 9007199254740993}
    second = copy_tool_details(first)
    first["items"][0].append(3)
    assert second["items"] == [[1, 1.0, -0.0]]
    assert type(second["items"][0][0]) is int
    assert type(second["items"][0][1]) is float
    assert math.copysign(1, second["items"][0][2]) == -1


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        {"a": float("nan")},
        {"a": {1: "not a string key"}},
        {"a": object()},
        b"not JSON",
    ],
)
def test_normal_snapshot_refuses_invalid_json(value):
    from pipy_harness.native.agent.tool_details import copy_tool_details

    with pytest.raises(ValueError, match="Tool details must be finite JSON"):
        copy_tool_details(value)


def test_normal_snapshot_refuses_cycles():
    from pipy_harness.native.agent.tool_details import copy_tool_details

    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="Tool details must be finite JSON"):
        copy_tool_details(cycle)


def test_durable_value_authority_and_canonical_ingress_remain_strict():
    from pipy_harness.native.agent import json_values
    from pipy_harness.native.agent.messages import AgentToolResultMessage
    from pipy_harness.native.durable import codec
    from pipy_harness.native.durable.canonical_codec import (
        CanonicalCodecError,
        encode_canonical,
    )

    assert codec.OwnedJson is json_values.OwnedJson
    assert codec.JsonValueError is json_values.JsonValueError
    for raw in ({"items": (1, 2)}, {"n": 9007199254740993}):
        with pytest.raises(codec.JsonValueError):
            codec.OwnedJson(raw)
    normal = AgentToolResultMessage(
        "pipy-tool-normal",
        "normal",
        ProductContent("x"),
        "c",
        details={"n": 9007199254740993},
    )
    assert tool_result_json(normal)["details"] == {"n": 9007199254740993}
    with pytest.raises(CanonicalCodecError):
        encode_canonical(normal)


@pytest.mark.parametrize("details", [{"items": (1, 2)}, {"n": 9007199254740993}])
def test_actual_nondurable_sdk_retains_normal_json_in_next_request(tmp_path, details):
    from test_native_coding_session_resume_compact import _RecordingToolProvider

    from pipy_harness import sdk
    from pipy_harness.native.agent.messages import AgentToolResultMessage
    from pipy_harness.native.models import ProviderToolCall

    class Port:
        definition = ToolDefinition("normal", "Normal details", {"type": "object"})

        def invoke(self, request, context):
            return ToolExecutionResult(
                request.tool_request_id,
                "result",
                details=details,
                provider_correlation_id=request.provider_correlation_id,
            )

    provider = _RecordingToolProvider(
        call_script=((ProviderToolCall("normal-call", "normal", "{}"),), ())
    )
    expected = json.loads(json.dumps(details, allow_nan=False))
    with sdk.create_product_session(
        workspace=tmp_path, provider=provider, tools={"normal": Port()}
    ) as session:
        snapshot = session.submit("normal details")
        returned = next(
            m for m in snapshot.messages if isinstance(m, AgentToolResultMessage)
        )
        assert returned.details == expected
        assert json.loads(json.dumps(tool_result_json(returned)))["details"] == expected
        assert "details" not in serialize_message(returned)
        following = next(
            m
            for m in provider.requests[1].messages
            if isinstance(m, AgentToolResultMessage)
        )
        assert following.details == expected
        details.clear()
        assert returned.details == expected and following.details == expected


@pytest.mark.parametrize("label", ["lone", "pair", "astral"])
@pytest.mark.parametrize("place", ["key", "value"])
def test_actual_old_v1_string_and_key_arenas_keep_exact_representation(label, place):
    from pathlib import Path

    from pipy_harness.native.durable.canonical_codec import (
        decode_canonical,
        encode_canonical,
    )

    fixture = (
        Path(__file__).parent / "fixtures/durable/tool-details-legacy-strings-v1.json"
    )
    corpus = json.loads(fixture.read_text())
    arena = corpus["cases"][label + "-" + place]
    assert corpus["source_commit"] == "df5c31373a1cca45812200bf3b3e949244f924f1"
    import hashlib

    raw = json.dumps(arena, ensure_ascii=True, separators=(",", ":")) + "\n"
    assert (
        hashlib.sha256(raw.encode()).hexdigest()
        == corpus["case_raw_sha256"][label + "-" + place]
    )
    expected = {"lone": [0xD800], "pair": [0xD83D, 0xDE00], "astral": [0x1F600]}[label]

    def recorded_codepoints(value):
        root = value["nodes"][0]
        details = value["nodes"][root["children"][root["keys"].index("details")]]
        encoded = (
            details["keys"][0]
            if place == "key"
            else value["nodes"][details["children"][0]]["value"]
        )
        return [ord(c) for c in bytes.fromhex(encoded).decode("utf-8", "surrogatepass")]

    assert recorded_codepoints(arena) == expected
    restored = decode_canonical(arena)
    actual = restored.details["s"] if place == "value" else next(iter(restored.details))
    assert [ord(c) for c in actual] == expected
    reencoded = encode_canonical(restored, codec_version=1)
    assert reencoded == arena
    assert recorded_codepoints(reencoded) == expected


def test_actual_old_deep_v1_arena_roundtrips_without_a_constructor_depth_gate():
    from pathlib import Path

    from pipy_harness.native.automation.durable_output import json_output
    from pipy_harness.native.durable.canonical_codec import (
        decode_canonical,
        encode_canonical,
    )

    arena = json.loads(
        (
            Path(__file__).parent / "fixtures/durable/tool-details-legacy-deep-v1.json"
        ).read_text()
    )
    restored = decode_canonical(arena)
    value = restored.details
    depth = 0
    while "child" in value:
        value = value["child"]
        depth += 1
    assert depth == 1500 and value == {"end": None}
    assert encode_canonical(restored, codec_version=1) == arena
    assert json_output({"details": restored.details}).count('"child":') == 1500


def test_normal_snapshot_matches_stable_json_subclasses_underlying_values():
    from pipy_harness.native.agent.tool_details import copy_tool_details

    class Number(int):
        def __int__(self):
            return 0

    class Floating(float):
        def __float__(self):
            return 0.0

    class Text(str):
        def __str__(self):
            return "wrong"

    class Array(list):
        pass

    class Object(dict):
        pass

    class Tuple(tuple):
        pass

    source = Object(
        {
            Text("key"): Array(
                [
                    Number(9007199254740993),
                    Floating(-0.0),
                    Text("value"),
                    Tuple((1, False)),
                ]
            )
        }
    )
    expected = json.loads(json.dumps(source, allow_nan=False))
    actual = copy_tool_details(source)
    assert actual == expected
    assert type(actual) is dict and type(next(iter(actual))) is str
    assert type(actual["key"]) is list and type(actual["key"][0]) is int
    assert type(actual["key"][1]) is float and type(actual["key"][2]) is str
    assert type(actual["key"][3]) is list
    import math

    assert math.copysign(1, actual["key"][1]) == -1


def test_normal_snapshot_duplicates_shared_noncyclic_descendants():
    from pipy_harness.native.agent.tool_details import copy_tool_details

    shared = {"items": [1]}
    actual = copy_tool_details([shared, shared])
    actual[0]["items"].append(2)
    assert shared == {"items": [1]} and actual[1] == {"items": [1]}
    assert actual[0] is not actual[1]


@pytest.mark.parametrize(
    "text", ["\ud800", "\ud83d\ude00", "\U0001f600"], ids=["lone", "pair", "astral"]
)
@pytest.mark.parametrize("place", ["key", "value"])
def test_normal_jsonl_uses_existing_json_utf16_semantics_without_mutating_snapshot(
    tmp_path, text, place
):
    from pipy_harness.native.agent.messages import (
        AgentAssistantMessage,
        AgentToolResultMessage,
    )
    from pipy_harness.native.session_tree import MessageEntry

    details = {"s": text} if place == "value" else {text: "x"}
    message = AgentToolResultMessage(
        "pipy-tool-unicode",
        "normal",
        ProductContent("x"),
        "unicode-call",
        details=details,
    )
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "unicode")
    tree.append_message(
        AgentAssistantMessage(
            ProductContent(""),
            tool_calls=(AgentToolCall("unicode-call", "normal", ProductContent("{}")),),
        )
    )
    tree.append_message(message)
    # JSONL retains its actual old JSON text interpretation. Raw canonical v1
    # representation is a separate hex arena, and remains exact in tests above.
    expected = json.loads(json.dumps(details, allow_nan=False))
    assert (
        json.loads(tree.path.read_text().splitlines()[-1])["message"]["details"]
        == expected
    )
    reopened = NativeSessionTree.open(tree.path, strict=True)
    restored = [
        e.message for e in reopened.get_entries() if isinstance(e, MessageEntry)
    ][-1]
    assert restored.details == expected
    actual = message.details["s"] if place == "value" else next(iter(message.details))
    assert [ord(c) for c in actual] == [ord(c) for c in text]

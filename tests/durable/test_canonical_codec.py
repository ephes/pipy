"""Field-complete DE06a conformance; no runtime recovery or parity closure claim."""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

import pytest

from pipy_harness.native.agent.content import (
    NO_STRUCTURED_CONTENT,
    ImageContent,
    ProductContent,
    TextContent,
    ThinkingContent,
)
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentMessageUsage,
    AgentStopReason,
    AgentSystemMessage,
    AgentToolCall,
    AgentToolDeclaration,
    AgentToolResultMessage,
    AgentUsageCost,
    AgentUserMessage,
)
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.agent.request import (
    freeze_provider_request,
    snapshot_provider_request,
)
from pipy_harness.native.agent.results import AgentCancellationReason
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
)
from pipy_harness.native.durable import canonical_codec as c
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.image_attachment import ProviderImageAttachment
from pipy_harness.native.models import (
    ProviderPartial,
    ProviderRequest,
    ProviderResult,
    ProviderSystemMessage,
    ProviderToolCall,
)
from pipy_harness.native.tools.base import ToolDefinition, ToolNamespace
from pipy_harness.status import HarnessStatus

RAW = ' { "x": [1,  2], "text": "ä" } \n'
CALL = AgentToolCall("provider:id", "read", ProductContent(RAW), "tool-signature")
PCALL = ProviderToolCall("provider:id", "read", RAW, "tool-signature")
USAGE = AgentMessageUsage(
    11,
    22,
    33,
    44,
    110,
    AgentUsageCost(0.1, 0.2, 0.3, 0.4, 1.0),
    reasoning=5,
    cache_write_1h=6,
)
BLOCKS = (
    ThinkingContent("thought", "thought-signature", True),
    TextContent("first", "text-signature"),
    CALL,
    TextContent("second"),
)
ASSISTANT = AgentAssistantMessage.from_blocks(
    BLOCKS,
    usage=USAGE,
    provider="provider",
    model="model",
    provider_thinking_level="high",
    api="api",
    stop_reason=AgentStopReason.ERROR,
    error_message="stopped",
)
RESULT = AgentToolResultMessage(
    "pipy-tool-codec",
    "read",
    ProductContent("text"),
    "provider:id",
    True,
    details={"diagnostics": [{"code": "failed", "line": 4}]},
    structured_content={"result": None, "negative_zero": -0.0},
    blocks=(
        TextContent("text"),
        ImageContent("aGVsbG8=", "image/png"),
    ),
    terminate=True,
)
SYSTEM = AgentSystemMessage(
    ProductContent("system"),
    (("head", "hello"), ("gone", None)),
    (AgentToolDeclaration("read", "Read", '{"type":"object"}'),),
    ("hidden",),
)
WHEN = datetime(
    2026, 10, 22, 12, 34, 56, 789, timezone(timedelta(hours=2), "named offset"), fold=1
)
PROVIDER = ProviderResult(
    HarnessStatus.SUCCEEDED,
    "provider",
    "model",
    WHEN,
    WHEN + timedelta(seconds=2),
    "firstsecond",
    usage={
        "input": 11,
        "output": 22,
        "reasoning": 5,
        "cache_read": 33,
        "cache_write": 44,
        "cache_write_1h": 6,
        "cost": {"total": 1.0},
    },
    metadata={"diagnostics": ["trace"], "control": {"continue": False}},
    error_type="provider_error",
    error_message="partial failure",
    tool_calls=(PCALL,),
    provider_thinking_level="high",
    content_blocks=(BLOCKS[0], BLOCKS[1], PCALL, BLOCKS[3]),
    api="api",
)
SCHEMA = {
    "type": "object",
    "properties": {"x": {"type": "string", "enum": ["a", "b"]}},
    "required": ["x"],
}
TOOL = ToolDefinition("read", "Read a file", SCHEMA)
ATTACHMENT = ProviderImageAttachment("image/png", "aGVsbG8=", 5, "012345", "image.png")
REQUEST = freeze_provider_request(
    ProviderRequest(
        "prompt",
        "input",
        "provider",
        "model",
        Path("/workspace/ä"),
        provider_turn_index=7,
        provider_turn_label="tools",
        messages=(AgentUserMessage(ProductContent("input")), ASSISTANT, RESULT),
        available_tools=(TOOL,),
        attachments=(ATTACHMENT,),
        session_id="session:cache",
        cache_retention="long",
        system_messages=(ProviderSystemMessage(0, SYSTEM),),
        hidden_tool_names=("hidden",),
        undeclared_tool_names=("script_only",),
    )
)
SNAPSHOT = snapshot_provider_request(REQUEST)
OUTCOME = ToolExecutionOutcome(
    RESULT, True, ToolExecutionInterruption.OPERATOR_ABORT, True, 0.125
)
VALUES = (
    ProductContent("full\ud800"),
    *BLOCKS,
    ImageContent("aGVsbG8=", "image/png"),
    AgentUserMessage(ProductContent("input")),
    USAGE.cost,
    USAGE,
    ASSISTANT,
    RESULT,
    SYSTEM.tools_added[0],
    SYSTEM,
    PCALL,
    PROVIDER,
    ProviderPartial((ThinkingContent("partial", "sig", True), PCALL), "medium"),
    ProviderTurnOutcome(result=PROVIDER),
    ProviderTurnOutcome(
        cancellation_reason=AgentCancellationReason.OPERATOR_ABORT,
        partial=ProviderPartial((TextContent("partial"),), "high"),
        retry_wait_cancelled=True,
    ),
    OUTCOME,
    REQUEST.system_messages[0],
    ATTACHMENT,
    TOOL,
    REQUEST,
    SNAPSHOT,
    AgentStopReason.ABORTED,
    NO_STRUCTURED_CONTENT,
    HarnessStatus.FAILED,
    ToolExecutionInterruption.LOCAL_COMMAND,
    AgentCancellationReason.STEERING,
)


def round_trip(value):
    tree = c.encode_canonical(value)
    # The arena is directly JSON-serializable; no private parser/prototype needed.
    transported = json.loads(json.dumps(tree, ensure_ascii=True, allow_nan=False))
    return c.decode_canonical(transported)


def assert_fields(left, right):
    """Independent field oracle: never re-encode expected or restored values."""
    if isinstance(left, Mapping):
        assert isinstance(right, Mapping)
        assert list(left) == list(right)
        for key in left:
            assert_fields(left[key], right[key])
        return
    assert type(right) is type(left)
    if is_dataclass(left):
        for field in fields(left):
            assert_fields(getattr(left, field.name), getattr(right, field.name))
    elif type(left) in (tuple, list):
        assert len(left) == len(right)
        for before, after in zip(left, right, strict=True):
            assert_fields(before, after)
    elif type(left) is datetime:
        assert left.isoformat() == right.isoformat()
        assert left.fold == right.fold
        assert left.tzname() == right.tzname()
    elif type(left) is float:
        assert OwnedJson(left) == OwnedJson(right)
    else:
        assert left == right


@pytest.mark.parametrize("value", VALUES)
def test_each_registered_dto_field_survives_transport(value):
    restored = round_trip(value)
    assert type(restored) is type(value)
    if type(value) in c.SCHEMAS:
        assert_fields(value, restored)
    else:
        assert restored is value


def test_explicit_registry_has_no_unnoticed_new_dto_fields():
    omissions = {f.name for f in fields(ToolDefinition)} - set(
        c.SCHEMAS[ToolDefinition]
    )
    assert omissions == {
        "exposure",
        "namespace",
        "annotations",
        "output_schema",
        "reaches_tools",
        "default_active",
        "prepare_loadout",
        "prompt_snippet",
        "prompt_guidelines",
        "execution_mode",
    }
    for cls, schema in c.SCHEMAS_V3.items():
        if cls is not ToolDefinition:
            assert {f.name for f in fields(cls)} == set(schema)
    assert {type(value) for value in VALUES if type(value) in c.SCHEMAS} == set(
        c.SCHEMAS
    )


def test_compare_false_metadata_rich_order_raw_arguments_and_cost_are_explicit():
    restored = round_trip(ASSISTANT)
    assert restored.blocks == BLOCKS
    assert restored.blocks[0].redacted is True
    assert restored.blocks[0].signature == "thought-signature"
    assert restored.blocks[1].signature == "text-signature"
    assert restored.tool_calls[0].arguments_json.value == RAW
    assert restored.tool_calls[0].provider_correlation_id == "provider:id"
    assert restored.tool_calls[0].thought_signature == "tool-signature"
    assert (
        restored.provider,
        restored.api,
        restored.model,
        restored.provider_thinking_level,
    ) == ("provider", "api", "model", "high")
    assert_fields(USAGE, restored.usage)
    assert_fields(USAGE.cost, restored.usage.cost)
    result = round_trip(OUTCOME)
    assert result.execution_seconds == 0.125
    assert result.result.details == RESULT.details
    assert result.result.structured_content["result"] is None
    assert math.copysign(1, result.result.structured_content["negative_zero"]) == -1
    assert result.result.terminate is True
    assert result.cancelled is True
    assert result.interruption is ToolExecutionInterruption.OPERATOR_ABORT


def test_request_preserves_cutoff_catalog_authorization_session_cache_images():
    restored = round_trip(SNAPSHOT)
    assert restored.advertised_tool_names == ("read",)
    assert restored.authorizes("read") is True
    assert restored.authorizes("hidden") is False
    assert_fields(REQUEST, restored.request)
    assert restored.request.cwd == Path("/workspace/ä")
    assert restored.request.provider_turn_index == 7
    assert restored.request.provider_turn_label == "tools"
    assert restored.request.session_id == "session:cache"
    assert restored.request.cache_retention == "long"
    assert restored.request.attachments[0].data_base64 == "aGVsbG8="
    assert restored.request.system_messages[0].position == 0
    with pytest.raises(TypeError):
        restored.request.available_tools[0].input_schema["type"] = "changed"
    assert restored.request.available_tools[0].input_schema["required"] == ("x",)


def test_timestamp_offset_microseconds_fold_and_timezone_name_survive():
    restored = round_trip(PROVIDER)
    for field in ("started_at", "ended_at"):
        before = getattr(PROVIDER, field)
        after = getattr(restored, field)
        assert before.isoformat() == after.isoformat()
        assert before.fold == after.fold
        assert before.tzname() == after.tzname()
    assert round_trip(datetime(2026, 1, 1)).tzinfo is None


@pytest.mark.parametrize(
    "structured", [NO_STRUCTURED_CONTENT, None, {}, [], False, 0, ""]
)
def test_absent_null_and_json_values_remain_distinct(structured):
    result = AgentToolResultMessage(
        "pipy-tool-codec",
        "read",
        ProductContent(""),
        "id",
        structured_content=structured,
    )
    restored = round_trip(result)
    assert type(restored.structured_content) is type(structured)
    assert restored.structured_content == structured


@pytest.mark.parametrize(
    "value",
    [
        -0.0,
        0.0,
        1,
        1.0,
        2**100,
        float(2**100),
        "\ud800",
        "\udc00",
        "\ud83d\ude00",
        "😀",
        {"z": 0, "a": -0.0},
    ],
)
def test_full_finite_ownedjson_scalar_and_key_identity(value):
    restored = round_trip(value)
    assert OwnedJson(restored) == OwnedJson(value)


def test_plain_payload_nesting_beyond_python_recursion_limit():
    value = {"leaf": -0.0}
    for _ in range(1800):
        value = {"array": [value]}
    restored = round_trip(value)
    assert OwnedJson(restored) == OwnedJson(value)
    result = AgentToolResultMessage(
        "pipy-tool-codec",
        "read",
        ProductContent(""),
        "id",
        details=value,
        structured_content=value,
    )
    restored_result = round_trip(result)
    assert OwnedJson(restored_result.details) == OwnedJson(value)
    assert OwnedJson(restored_result.structured_content) == OwnedJson(value)


def test_encode_and_decode_detach_every_placement_and_every_call():
    payload = {"items": [1]}
    source = [payload, payload]
    tree = c.encode_canonical(source)
    payload["items"].append(2)
    first = c.decode_canonical(tree)
    second = c.decode_canonical(tree)
    assert first == second == [{"items": [1]}, {"items": [1]}]
    first[0]["items"].append(3)
    assert first[1] == second[0] == second[1] == {"items": [1]}
    leaf = next(
        node for node in tree["nodes"] if node["tag"] == "scalar" and node["value"] == 1
    )
    leaf["value"] = 11
    assert first == [{"items": [1, 3]}, {"items": [1]}]
    assert second == [{"items": [1]}, {"items": [1]}]
    assert c.decode_canonical(tree) == [{"items": [11]}, {"items": [1]}]


class Hostile:
    def __getattribute__(self, name):
        raise AssertionError("foreign attribute hook")

    def __iter__(self):
        raise AssertionError("foreign iterator hook")

    def __hash__(self):
        raise AssertionError("foreign hash hook")

    def __eq__(self, other):
        raise AssertionError("foreign equality hook")

    def __index__(self):
        raise AssertionError("foreign index hook")


class ForeignDict(dict):
    def items(self):
        raise AssertionError("foreign mapping hook")


class ForeignInt(int):
    pass


class ForeignProductContent(ProductContent):
    pass


@pytest.mark.parametrize(
    "value",
    [
        Hostile,
        ForeignDict(),
        ForeignInt(1),
        ForeignProductContent("secret"),
        lambda: "token",
        b"bytes",
        set(),
        float("nan"),
        float("inf"),
        2**100 + 1,
        {1: "bad"},
        {"😀": 1, "\ud83d\ude00": 2},
    ],
)
def test_exact_ingress_refuses_foreign_and_nonfinite_values_without_hooks(value):
    if value is Hostile:
        value = Hostile()
    with pytest.raises(c.CanonicalCodecError):
        c.encode_canonical(value)


def test_foreign_dictionary_key_is_refused_before_hashing_again():
    class Key:
        calls = 0

        def __hash__(self):
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("second foreign hash")
            return 1

    key = Key()
    with pytest.raises(c.CanonicalCodecError):
        c.encode_canonical({key: "value"})
    assert key.calls == 1


@pytest.mark.parametrize("container", ["list", "dict", "dto"])
def test_cycles_are_refused(container):
    if container == "list":
        value = []
        value.append(value)
    elif container == "dict":
        value = {}
        value["self"] = value
    else:
        value = ProductContent("safe")
        object.__setattr__(value, "value", value)
    with pytest.raises(c.CanonicalCodecError):
        c.encode_canonical(value)


def test_live_callback_refusal_never_calls_or_prints_callback():
    def callback(*args):
        raise AssertionError("secret callback called")

    value = ProviderRequest(
        "s", "u", "p", "m", Path("/tmp"), provider_header_callback=callback
    )
    with pytest.raises(c.CanonicalCodecError, match="Live provider header") as caught:
        c.encode_canonical(value)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace", ToolNamespace("ns")),
        ("exposure", "model-only"),
        ("output_schema", {"type": "object"}),
        ("default_active", False),
        ("prompt_snippet", "snippet"),
        ("prompt_guidelines", ("guide",)),
        ("execution_mode", "sequential"),
        ("reaches_tools", lambda _: ()),
        ("prepare_loadout", lambda _: None),
    ],
)
def test_non_request_registration_fields_are_explicit_refusals(field, value):
    tool = ToolDefinition("read", "Read", SCHEMA, **{field: value})
    with pytest.raises(c.CanonicalCodecError, match="model-visible"):
        c.encode_canonical(tool)


def test_foreign_registered_metadata_never_invokes_equality():
    tool = ToolDefinition("read", "Read", SCHEMA)
    object.__setattr__(tool, "prompt_guidelines", (Hostile(),))
    with pytest.raises(c.CanonicalCodecError):
        c.encode_canonical(tool)


class ForeignTimezone(tzinfo):
    def utcoffset(self, dt):
        raise AssertionError("foreign timezone hook")


def test_foreign_timezone_refused_before_datetime_conversion():
    with pytest.raises(c.CanonicalCodecError):
        c.encode_canonical(datetime(2026, 1, 1, tzinfo=ForeignTimezone()))


def malformed_trees():
    leaf = {"tag": "string", "value": "PRIVATE_PAYLOAD".encode().hex()}
    return [
        None,
        {},
        {"codec_version": True, "nodes": [leaf]},
        {"codec_version": 4, "nodes": [leaf]},
        {"codec_version": 1, "nodes": []},
        {"codec_version": 1, "nodes": [leaf], "extra": None},
        {"codec_version": 1, "nodes": [{"tag": "unknown", "value": "PRIVATE_PAYLOAD"}]},
        {"codec_version": 1, "nodes": [dict(leaf, extra=1)]},
        {"codec_version": 1, "nodes": [{"tag": "scalar", "value": []}]},
        {"codec_version": 1, "nodes": [leaf, leaf]},
        *[
            {"codec_version": 1, "nodes": [{"tag": "list", "children": [ref]}, leaf]}
            for ref in [0, -1, 2, True, 1.0, "1"]
        ],
        {"codec_version": 1, "nodes": [{"tag": "list", "children": [1, 1]}, leaf]},
        {
            "codec_version": 1,
            "nodes": [
                {"tag": "mapping", "keys": ["a", "a"], "children": [1, 2]},
                leaf,
                leaf,
            ],
        },
        {
            "codec_version": 1,
            "nodes": [
                {
                    "tag": "mapping",
                    "keys": [
                        "😀".encode().hex(),
                        "\ud83d\ude00".encode("utf-8", "surrogatepass").hex(),
                    ],
                    "children": [1, 2],
                },
                leaf,
                leaf,
            ],
        },
        {"codec_version": 1, "nodes": [{"tag": "path", "value": None}]},
        {"codec_version": 1, "nodes": [{"tag": "HarnessStatus", "value": "unknown"}]},
    ]


@pytest.mark.parametrize("tree", malformed_trees())
def test_malformed_unknown_newer_dangling_and_alias_arenas_refused_privately(tree):
    with pytest.raises(c.CanonicalCodecError) as caught:
        c.decode_canonical(tree)
    assert "PRIVATE_PAYLOAD" not in str(caught.value)
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "kind", ["extra", "missing", "wrongtype", "authorization", "request_callback"]
)
def test_dto_field_shapes_and_actual_constructors_are_used(kind):
    tree = c.encode_canonical(SNAPSHOT)
    nodes = tree["nodes"]
    if kind == "authorization":
        snapshot = nodes[0]
        ref = snapshot["children"][snapshot["keys"].index("advertised_tool_names")]
        name_ref = nodes[ref]["children"][0]
        nodes[name_ref]["value"] = "wrong".encode().hex()
    elif kind == "request_callback":
        node = next(n for n in nodes if n["tag"] == "ProviderRequest")
        ref = node["children"][node["keys"].index("provider_header_callback")]
        nodes[ref] = {"tag": "string", "value": "secret-header".encode().hex()}
    else:
        node = nodes[0]
        if kind == "extra":
            node["keys"].append("unknown")
            node["children"].append(len(nodes))
            nodes.append({"tag": "scalar", "value": None})
        elif kind == "missing":
            tree = _without_field(tree, node["keys"][-1])
        else:
            node = next(n for n in nodes if n.get("value") == 7)
            node["value"] = True
    with pytest.raises(c.CanonicalCodecError):
        c.decode_canonical(tree)


def test_corrupted_exact_dto_wrong_field_type_refused_by_closed_schema():
    value = copy.copy(PROVIDER)
    object.__setattr__(value, "started_at", ProductContent("not a timestamp"))
    with pytest.raises(c.CanonicalCodecError):
        c.encode_canonical(value)


def _assert_valid_arena(tree):
    """Independent structure oracle; no production decoder or DTO schema used."""
    nodes = tree["nodes"]
    incoming = []
    for index, node in enumerate(nodes):
        if "children" not in node:
            continue
        if "keys" in node:
            assert len(node["keys"]) == len(node["children"])
            assert len(set(node["keys"])) == len(node["keys"])
        for child in node["children"]:
            assert type(child) is int and index < child < len(nodes)
            incoming.append(child)
    assert sorted(incoming) == list(range(1, len(nodes)))


def _without_field(tree, field):
    """Remove the field edge, its complete subtree, and compact all references."""
    tree = copy.deepcopy(tree)
    nodes = tree["nodes"]
    position = nodes[0]["keys"].index(field)
    nodes[0]["keys"].pop(position)
    nodes[0]["children"].pop(position)
    reachable = set()
    work = [0]
    while work:
        index = work.pop()
        assert index not in reachable
        reachable.add(index)
        work.extend(nodes[index].get("children", ()))
    kept = sorted(reachable)
    compacted = {old: new for new, old in enumerate(kept)}
    result = []
    for index in kept:
        node = nodes[index]
        if "children" in node:
            node["children"] = [compacted[child] for child in node["children"]]
        result.append(node)
    tree["nodes"] = result
    _assert_valid_arena(tree)
    return tree


@pytest.mark.parametrize(
    "value", [value for value in VALUES if type(value) in c.SCHEMAS]
)
def test_every_registered_dto_rejects_unknown_fields(value):
    tree = c.encode_canonical(value)
    root = tree["nodes"][0]
    root["keys"].append("newer_field")
    root["children"].append(len(tree["nodes"]))
    tree["nodes"].append({"tag": "scalar", "value": None})
    _assert_valid_arena(tree)
    with pytest.raises(c.CanonicalCodecError):
        c.decode_canonical(tree)


@pytest.mark.parametrize(
    "value,field",
    [
        (value, field)
        for value in VALUES
        if type(value) in c.SCHEMAS
        for field in c.SCHEMAS[type(value)]
    ],
)
def test_every_registered_dto_rejects_missing_fields_at_schema(
    value, field, monkeypatch
):
    tree = _without_field(c.encode_canonical(value), field)
    original = c._construct
    observed = []

    def construct(cls, values, codec_version):
        if cls is type(value) and field not in values:
            observed.append(set(values))
        return original(cls, values, codec_version)

    monkeypatch.setattr(c, "_construct", construct)
    with pytest.raises(c.CanonicalCodecError):
        c.decode_canonical(tree)
    assert observed == [set(c.SCHEMAS[type(value)]) - {field}]


@pytest.mark.parametrize("text", ["AA", "61 62", "f", "ff", "00zz"])
def test_string_hex_encoding_is_strict_and_canonical(text):
    with pytest.raises(c.CanonicalCodecError):
        c.decode_canonical(
            {"codec_version": 1, "nodes": [{"tag": "string", "value": text}]}
        )


def test_timezone_name_preserves_surrogate_spelling():
    value = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1), "\ud83d\ude00"))
    assert round_trip(value).tzname() == value.tzname()


def test_foreign_metaclass_protocols_are_not_part_of_ingress():
    class Meta(type):
        def __eq__(cls, other):
            raise AssertionError("foreign metaclass equality")

        def __hash__(cls):
            raise AssertionError("foreign metaclass hash")

    class Foreign(metaclass=Meta):
        pass

    with pytest.raises(c.CanonicalCodecError):
        c.encode_canonical(Foreign())


def test_nested_section_metaclass_uses_only_exact_identity_checks():
    class Meta(type):
        def __eq__(cls, other):
            raise AssertionError("foreign nested metaclass equality")

        def __hash__(cls):
            raise AssertionError("foreign nested metaclass hash")

    class Foreign(metaclass=Meta):
        pass

    sections = (("key", Foreign()),)
    # Normal DTO construction already rejects this; adversarial frozen-object
    # mutation also cannot reach a foreign protocol at codec ingress.
    with pytest.raises(TypeError):
        AgentSystemMessage(sections=sections)
    message = AgentSystemMessage()
    object.__setattr__(message, "sections", sections)
    with pytest.raises(c.CanonicalCodecError):
        c.encode_canonical(message)
    # Pin the field predicate too, although normal decode reaches it only after
    # the closed node registry has already excluded arbitrary objects.
    assert c._matches(sections, "sections") is False


@pytest.mark.parametrize(
    "spelling",
    ["2026-01-01", "20260101T000000", "2026-01-01 00:00:00", "2026-01-01T00:00:00Z"],
)
def test_datetime_requires_encoder_canonical_spelling(spelling):
    tree = c.encode_canonical(datetime(2026, 1, 1, tzinfo=timezone.utc))
    tree["nodes"][0]["value"] = spelling
    if not spelling.endswith("Z"):
        tree["nodes"][0]["timezone_name"] = None
    with pytest.raises(c.CanonicalCodecError):
        c.decode_canonical(tree)


@pytest.mark.parametrize("spelling", ["a//b", "./a", "a/./b", ""])
def test_path_requires_encoder_canonical_spelling(spelling):
    tree = c.encode_canonical(Path("a"))
    tree["nodes"][0]["value"] = spelling.encode().hex()
    with pytest.raises(c.CanonicalCodecError):
        c.decode_canonical(tree)


@pytest.mark.parametrize(
    "value",
    [
        Path("a/b"),
        Path("./a"),
        Path(""),
        Path("\ud800"),
        datetime(2026, 1, 1, fold=1),
        WHEN,
    ],
)
def test_encoder_path_datetime_output_is_canonical(value):
    restored = round_trip(value)
    assert_fields(value, restored)

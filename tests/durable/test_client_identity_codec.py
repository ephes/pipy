"""Closed V3 identity arenas with exact retained legacy wire authorities."""

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from generation_support import seed
from test_canonical_codec import RESULT

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.agent.tool_details import NO_TOOL_DETAILS
from pipy_harness.native.client_identity import (
    _UNSET,
    ClientAttribution,
    ClientIdentity,
    CustomUserAgent,
)
from pipy_harness.native.durable.canonical_codec import (
    CanonicalCodecError,
    decode_canonical,
    encode_canonical,
)


@pytest.mark.parametrize(
    "identity",
    [
        ClientIdentity(),
        ClientIdentity(attribution=None),
        ClientIdentity(version=""),
        ClientIdentity(CustomUserAgent(""), ClientAttribution("", "", ()), ""),
    ],
)
def test_request_identity_requires_three_and_survives_exact_fields(tmp_path, identity):
    snapshot = snapshot_provider_request(
        replace(seed(tmp_path).request, identity=identity)
    )
    tree = encode_canonical(snapshot)
    assert tree["codec_version"] == 3
    actual = decode_canonical(json.loads(json.dumps(tree)))
    assert actual.request.identity == identity
    for version in (1, 2):
        with pytest.raises(CanonicalCodecError):
            encode_canonical(snapshot, codec_version=version)
    assert encode_canonical(actual, codec_version=3) == tree


def test_empty_and_absent_are_distinct_but_unset_never_crosses_wire(tmp_path):
    plain = seed(tmp_path)
    old = encode_canonical(plain)
    assert old["codec_version"] == 1
    assert (
        "identity"
        not in next(n for n in old["nodes"] if n["tag"] == "ProviderRequest")["keys"]
    )
    assert decode_canonical(old).request.identity is None
    explicit_three = encode_canonical(plain, codec_version=3)
    assert decode_canonical(explicit_three).request.identity is None
    assert (
        encode_canonical(decode_canonical(explicit_three), codec_version=3)
        == explicit_three
    )
    assert encode_canonical(decode_canonical(explicit_three)) == old
    empty = encode_canonical(ClientIdentity())
    assert empty == {
        "codec_version": 3,
        "nodes": [{"tag": "ClientIdentity", "children": [], "keys": []}],
    }
    assert decode_canonical(empty).version is _UNSET
    null = encode_canonical(ClientIdentity(attribution=None))
    assert null["nodes"][0]["keys"] == ["attribution"]
    assert null["nodes"][1] == {"tag": "scalar", "value": None}


def test_retained_v1_fixture_and_v2_enum_bytes_are_exact():
    path = Path(__file__).parents[1] / "fixtures/durable/tool-details-legacy-v1.json"
    legacy = json.loads(path.read_text())
    assert encode_canonical(decode_canonical(legacy), codec_version=1) == legacy
    v2 = {"codec_version": 2, "nodes": [{"tag": "ToolDetailsState", "value": "absent"}]}
    assert encode_canonical(decode_canonical(v2), codec_version=2) == v2
    assert decode_canonical(v2) is NO_TOOL_DETAILS


def test_v3_keeps_v2_details_semantics_and_nested_fields(tmp_path):
    snapshot = snapshot_provider_request(
        replace(seed(tmp_path).request, identity=ClientIdentity())
    )
    value = (snapshot, replace(RESULT, details=None))
    tree = encode_canonical(value)
    assert tree["codec_version"] == 3
    restored = decode_canonical(tree)
    assert restored[1].details is None
    assert restored[1].structured_content == RESULT.structured_content
    assert restored[1].blocks == RESULT.blocks
    assert encode_canonical(restored, codec_version=3) == tree


@pytest.mark.parametrize(
    "mutant",
    [
        "unknown-key",
        "duplicate-key",
        "illegal-null",
        "bad-mode",
        "foreign-tag",
        "old-version",
        "unknown-version",
    ],
)
def test_identity_malformed_arenas_are_closed(mutant):
    tree = encode_canonical(ClientIdentity("short"))
    bad = copy.deepcopy(tree)
    if mutant == "unknown-key":
        bad["nodes"][0]["keys"] = ["headers"]
    elif mutant == "duplicate-key":
        bad["nodes"][0]["keys"] = ["user_agent", "user_agent"]
        bad["nodes"][0]["children"] = [1, 2]
        bad["nodes"].append(copy.deepcopy(bad["nodes"][1]))
    elif mutant == "illegal-null":
        bad["nodes"][1] = {"tag": "scalar", "value": None}
    elif mutant == "bad-mode":
        bad["nodes"][1]["value"] = "not-a-mode".encode().hex()
    elif mutant == "foreign-tag":
        bad["nodes"][0]["tag"] = "ForeignIdentity"
    elif mutant == "old-version":
        bad["codec_version"] = 2
    else:
        bad["codec_version"] = 4
    with pytest.raises(CanonicalCodecError):
        decode_canonical(bad)


def test_callbacks_remain_unserializable_with_identity(tmp_path):
    req = replace(
        seed(tmp_path).request,
        identity=ClientIdentity(),
        provider_header_callback=lambda _: None,
    )
    with pytest.raises(CanonicalCodecError, match="callbacks"):
        encode_canonical(req)


@pytest.mark.parametrize("version", [1, 2])
def test_identity_absent_request_has_exact_legacy_arena(tmp_path, version):
    # Independent handwritten old field order and leaves; no V3 schema oracle.
    request = seed(tmp_path).request
    fields = [
        ("system_prompt", "string", "system"),
        ("user_prompt", "string", "input"),
        ("provider_name", "string", "openai"),
        ("model_id", "string", "gpt-test"),
        ("cwd", "path", str(tmp_path)),
        ("provider_turn_index", "scalar", 0),
        ("provider_turn_label", "string", "initial"),
        ("tool_observation", "scalar", None),
        ("messages", "tuple", None),
        ("available_tools", "tuple", None),
        ("attachments", "tuple", None),
        ("provider_header_callback", "scalar", None),
        ("session_id", "scalar", None),
        ("cache_retention", "scalar", None),
        ("system_messages", "tuple", None),
        ("hidden_tool_names", "tuple", None),
        ("undeclared_tool_names", "tuple", None),
    ]
    nodes = [
        {
            "tag": "ProviderRequest",
            "children": list(range(1, len(fields) + 1)),
            "keys": [name for name, _, _ in fields],
        }
    ]
    for _, tag, value in fields:
        if tag == "tuple":
            nodes.append({"tag": tag, "children": []})
        else:
            nodes.append(
                {
                    "tag": tag,
                    "value": value.encode("utf-8", "surrogatepass").hex()
                    if tag in ("string", "path")
                    else value,
                }
            )
    arena = {"codec_version": version, "nodes": nodes}
    assert encode_canonical(request, codec_version=version) == arena
    assert decode_canonical(arena).identity is None

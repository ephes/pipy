"""New producer admission stays separate from historical value ownership."""

import io
import json
import sys

import pytest
from test_native_coding_session_resume_compact import _RecordingToolProvider

from pipy_harness import sdk
from pipy_harness.extensions import ToolResult, ToolResultTransform
from pipy_harness.native.agent.content import ProductContent, ToolPartialResult
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
)
from pipy_harness.native.agent.tool_details import (
    NO_TOOL_DETAILS,
    UNCHANGED_TOOL_DETAILS,
)
from pipy_harness.native.agent.tools import ToolExecutor
from pipy_harness.native.automation.jsonl import JsonlWriter
from pipy_harness.native.automation.rpc import _dumps
from pipy_harness.native.automation.serialize import (
    partial_result_json,
    tool_result_json,
)
from pipy_harness.native.extension_hooks import dispatch_tool_result_hooks
from pipy_harness.native.extension_types import ExtensionTool, RegisteredTool
from pipy_harness.native.extensions.tool_port import _ExtensionToolPort
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
)


def oversized_details(kind):
    if kind == "digits5001":
        return {"number": 10**5000}
    value = {"end": None}
    for _ in range(1500):
        value = {"child": value}
    return value


def stdlib_admits(value):
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, RecursionError):
        return False
    return True


def stdlib_forwarding_admits(value):
    try:
        json.loads(json.dumps(value, allow_nan=False))
    except (ValueError, RecursionError):
        return False
    return True


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    for key in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(key, str(tmp_path / key))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))


def wire_roundtrip(value):
    output = io.BytesIO()
    JsonlWriter(output).write_line(value)
    assert output.getvalue().count(b"\n") == 1
    assert output.getvalue() == (_dumps(value) + "\n").encode()
    return json.loads(output.getvalue())


@pytest.mark.parametrize(
    "details",
    [
        NO_TOOL_DETAILS,
        None,
        False,
        0,
        "s",
        [None],
        {},
        {"tuple": (1, 2)},
        {"integer": 9007199254740993},
    ],
)
def test_admitted_values_keep_presence_through_executor_jsonl_and_rpc(
    tmp_path, details
):
    class Port:
        definition = ToolDefinition("normal", "Normal details", {"type": "object"})

        def invoke(self, request, context):
            return ToolExecutionResult(
                request.tool_request_id, "result", details=details
            )

    call = AgentToolCall("call", "normal", ProductContent("{}"))
    message = (
        ToolExecutor({"normal": Port()}).execute(call, ToolContext(tmp_path)).result
    )
    outward = wire_roundtrip(tool_result_json(message))
    assert ("details" in outward) is (details is not NO_TOOL_DETAILS)
    if details is not NO_TOOL_DETAILS:
        assert outward["details"] == json.loads(json.dumps(details))
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "session")
    tree.append_message(AgentAssistantMessage(ProductContent(""), tool_calls=(call,)))
    tree.append_message(message)
    restored = NativeSessionTree.open(tree.path, strict=True)
    saved = [e.message for e in restored.entries if isinstance(e, MessageEntry)][-1]
    assert wire_roundtrip(tool_result_json(saved)) == outward


@pytest.mark.parametrize("kind", ["deep1500", "digits5001"])
def test_actual_sdk_refuses_at_producer_before_memory_and_disk_diverge(tmp_path, kind):
    details = oversized_details(kind)
    admitted = stdlib_admits(details)
    # Actual 3.11 is a required run, not a skipped or shallower fixture.
    if sys.version_info[:2] == (3, 11):
        assert sys.getrecursionlimit() == 1000
        assert sys.get_int_max_str_digits() == 4300
        assert not admitted
    calls = []

    class Port:
        definition = ToolDefinition("normal", "Normal details", {"type": "object"})

        def invoke(self, request, context):
            calls.append(request.tool_request_id)
            return ToolExecutionResult(
                request.tool_request_id,
                "result",
                details=details,
                provider_correlation_id=request.provider_correlation_id,
            )

    provider = _RecordingToolProvider(
        call_script=((ProviderToolCall("normal-call", "normal", "{}"),), ())
    )
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "session")
    with sdk.create_product_session(
        workspace=tmp_path,
        provider=provider,
        tools={"normal": Port()},
        tree=tree,
        load_context_files=False,
    ) as session:
        if admitted:
            session.submit("details")
        else:
            with pytest.raises(ValueError, match="^Tool details exceed native JSON"):
                session.submit("details")
        returned = [
            m
            for m in session.snapshot().messages
            if isinstance(m, AgentToolResultMessage)
        ]
        assert len(returned) == int(admitted)
    assert len(calls) == 1 and len(provider.requests) == (2 if admitted else 1)
    rows = [json.loads(line) for line in tree.path.read_text().splitlines()]
    assert len(tree.entries) == len(rows) - 1
    disk_tools = [r for r in rows if r.get("message", {}).get("role") == "tool"]
    assert len(disk_tools) == int(admitted)
    restored = NativeSessionTree.open(tree.path, strict=True)
    assert [e.id for e in restored.entries] == [e.id for e in tree.entries]
    assert restored.leaf_id == tree.leaf_id
    with sdk.open_product_session(
        workspace=tmp_path,
        session_path=tree.path,
        provider=provider,
        tools={"normal": Port()},
        load_context_files=False,
    ):
        pass
    assert len(calls) == 1 and len(provider.requests) == (2 if admitted else 1)


@pytest.mark.parametrize("kind", ["deep1500", "digits5001"])
def test_extension_final_and_partial_admission_reach_real_jsonl_rpc_writers(
    tmp_path, kind
):
    details = oversized_details(kind)
    admitted = stdlib_forwarding_admits(details)

    def handler(ctx, arguments):
        ctx.on_update(ToolResult("partial", details=details))
        return ToolResult("final", details=details)

    port = _ExtensionToolPort(
        RegisteredTool(
            ExtensionTool("normal", "Normal details", {"type": "object"}, handler),
            "fixture",
        ),
        has_ui=False,
    )
    updates = []
    call = AgentToolCall("call", "normal", ProductContent("{}"))
    result = (
        ToolExecutor({"normal": port})
        .execute(call, ToolContext(tmp_path, output_sink=updates.append))
        .result
    )
    assert not result.is_error and result.content.value == "final"
    assert len(updates) == 1 and isinstance(updates[0], ToolPartialResult)
    for payload in (partial_result_json(updates[0]), tool_result_json(result)):
        encoded = wire_roundtrip(payload)
        assert ("details" in encoded) is admitted
    if not admitted:
        assert result.details is NO_TOOL_DETAILS
        assert updates[0].details is NO_TOOL_DETAILS
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "session")
    tree.append_message(AgentAssistantMessage(ProductContent(""), tool_calls=(call,)))
    tree.append_message(result)
    restored = NativeSessionTree.open(tree.path, strict=True)
    message = [e.message for e in restored.entries if isinstance(e, MessageEntry)][-1]
    assert ("details" in wire_roundtrip(tool_result_json(message))) is admitted


@pytest.mark.parametrize("kind", ["deep1500", "digits5001"])
def test_unencodable_replacement_hook_is_ignored_atomically(tmp_path, kind):
    details = oversized_details(kind)
    admitted = stdlib_forwarding_admits(details)
    seen = []

    def replace_all(event, ctx):
        return ToolResultTransform(
            content="replacement",
            is_error=True,
            structured_content=None,
            details=details,
        )

    def observe(event, ctx):
        seen.append(event)

    transformed = dispatch_tool_result_hooks(
        (replace_all, observe),
        tool_name="normal",
        content="original",
        is_error=False,
        structured_content={"kept": True},
        details={"kept": True},
        cwd=str(tmp_path),
        has_ui=False,
    )
    event = seen[0]
    if admitted:
        assert transformed is not None
        assert event.content == "replacement" and event.is_error
        assert event.structured_content is None
    else:
        assert transformed is None
        assert event.content == "original" and not event.is_error
        assert event.structured_content == {"kept": True}
        assert event.details == {"kept": True}
    result = AgentToolResultMessage(
        "pipy-tool-hook",
        "normal",
        ProductContent(event.content),
        "call",
        is_error=event.is_error,
        details=event.details,
        structured_content=event.structured_content,
    )
    wire_roundtrip(tool_result_json(result))
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "session")
    tree.append_message(result)
    assert len(tree.entries) == len(tree.path.read_text().splitlines()) - 1


def test_unchanged_hook_copies_deep_history_without_producer_admission(tmp_path):
    details = oversized_details("deep1500")
    seen = []

    def change_content(event, ctx):
        seen.append(event.details)
        return ToolResultTransform(content="changed")

    transformed = dispatch_tool_result_hooks(
        (change_content,),
        tool_name="normal",
        content="old",
        is_error=False,
        details=details,
        cwd=str(tmp_path),
        has_ui=False,
    )
    assert transformed.content == "changed"
    assert transformed.details is UNCHANGED_TOOL_DETAILS
    assert seen[0] is not details
    cursor = seen[0]
    for _ in range(1500):
        cursor = cursor["child"]
    assert cursor == {"end": None}


@pytest.mark.parametrize("text", ["\ud800", "\ud83d\ude00", "\U0001f600"])
@pytest.mark.parametrize("place", ["key", "value"])
def test_producer_admission_does_not_normalize_raw_strings(text, place):
    value = {text: "x"} if place == "key" else {"x": text}
    result = ToolExecutionResult("pipy-tool-unicode", "x", details=value)
    actual = next(iter(result.details)) if place == "key" else result.details["x"]
    assert [ord(c) for c in actual] == [ord(c) for c in text]


def forwarded_result(tmp_path, details, flow):
    if flow == "replacement_hook":
        seen = []

        def replace(event, ctx):
            return ToolResultTransform(
                content="replacement",
                is_error=True,
                structured_content=None,
                details=details,
            )

        def observe(event, ctx):
            seen.append(event)

        transformed = dispatch_tool_result_hooks(
            (replace, observe),
            tool_name="normal",
            content="original",
            is_error=False,
            structured_content={"kept": True},
            details={"kept": True},
            cwd=str(tmp_path),
            has_ui=False,
        )
        assert transformed is not None
        event = seen[0]
        assert event.content == "replacement" and event.is_error
        assert event.structured_content is None
        return AgentToolResultMessage(
            "pipy-tool-hook",
            "normal",
            ProductContent(event.content),
            "call",
            is_error=event.is_error,
            details=event.details,
            structured_content=event.structured_content,
        )

    def handler(ctx, arguments):
        ctx.on_update(ToolResult("partial", details=details))
        return ToolResult("final", details=details)

    port = _ExtensionToolPort(
        RegisteredTool(
            ExtensionTool("normal", "Normal details", {"type": "object"}, handler),
            "fixture",
        ),
        has_ui=False,
    )
    updates = []
    call = AgentToolCall("call", "normal", ProductContent("{}"))
    message = (
        ToolExecutor({"normal": port})
        .execute(call, ToolContext(tmp_path, output_sink=updates.append))
        .result
    )
    assert not message.is_error and message.content.value == "final"
    assert len(updates) == 1 and isinstance(updates[0], ToolPartialResult)
    return updates[0] if flow == "extension_partial" else message


def forwarded_payload(result):
    return (
        partial_result_json(result)
        if isinstance(result, ToolPartialResult)
        else tool_result_json(result)
    )


FORWARDING_FLOWS = ("extension_final", "extension_partial", "replacement_hook")
# Actual df5c old-source JsonlWriter bytes for explicit Python surrogate pairs.
# Captured old observations SHA83a438c33907e357e7cdd74d0c6825402c8b11b1b7e54c0a91e9109217feb228.
OLD_FORWARDING_BYTES = {
    ("extension_final", "key"): (
        b'{"content":[{"type":"text","text":"final"}],"details":{"\xf0\x9f\x98\x80":"x"}}\n'
    ),
    ("extension_final", "value"): (
        b'{"content":[{"type":"text","text":"final"}],"details":{"x":"\xf0\x9f\x98\x80"}}\n'
    ),
    ("extension_partial", "key"): (
        b'{"content":[{"type":"text","text":"partial"}],"details":{"\xf0\x9f\x98\x80":"x"}}\n'
    ),
    ("extension_partial", "value"): (
        b'{"content":[{"type":"text","text":"partial"}],"details":{"x":"\xf0\x9f\x98\x80"}}\n'
    ),
    ("replacement_hook", "key"): (
        b'{"content":[{"type":"text","text":"replacement"}],"details":{"\xf0\x9f\x98\x80":"x"},"structuredContent":null}\n'
    ),
    ("replacement_hook", "value"): (
        b'{"content":[{"type":"text","text":"replacement"}],"details":{"x":"\xf0\x9f\x98\x80"},"structuredContent":null}\n'
    ),
}


@pytest.mark.parametrize("flow", FORWARDING_FLOWS)
@pytest.mark.parametrize("place", ["key", "value"])
@pytest.mark.parametrize("text", ["\ud83d\ude00", "\ud800", "\U0001f600"])
def test_forwarding_unicode_matches_old_actual_utf8_writers(
    tmp_path, flow, place, text
):
    details = {text: "x"} if place == "key" else {"x": text}
    result = forwarded_result(tmp_path, details, flow)
    actual = next(iter(result.details)) if place == "key" else result.details["x"]
    expected = text if text == "\ud800" else "\U0001f600"
    assert [ord(c) for c in actual] == [ord(c) for c in expected]
    payload = forwarded_payload(result)
    output = io.BytesIO()
    if text == "\ud800":
        # Old forwarding retained lone surrogates; both UTF8 writers refused.
        with pytest.raises(UnicodeEncodeError):
            JsonlWriter(output).write_line(payload)
        assert output.getvalue() == b""
        with pytest.raises(UnicodeEncodeError):
            (_dumps(payload) + "\n").encode("utf-8")
    else:
        JsonlWriter(output).write_line(payload)
        assert output.getvalue() == OLD_FORWARDING_BYTES[flow, place]
        assert (_dumps(payload) + "\n").encode("utf-8") == output.getvalue()


@pytest.mark.parametrize("flow", FORWARDING_FLOWS)
@pytest.mark.parametrize(
    "details",
    [
        NO_TOOL_DETAILS,
        None,
        False,
        0,
        "s",
        [None],
        {},
        {"tuple": (1, 2)},
        {"integer": 9007199254740993},
    ],
)
def test_forwarding_values_keep_presence_and_normal_json_domain(
    tmp_path, flow, details
):
    result = forwarded_result(tmp_path, details, flow)
    encoded = wire_roundtrip(forwarded_payload(result))
    assert ("details" in encoded) is (details is not NO_TOOL_DETAILS)
    if details is NO_TOOL_DETAILS:
        assert result.details is NO_TOOL_DETAILS
    else:
        assert encoded["details"] == json.loads(json.dumps(details))


@pytest.mark.parametrize("flow", FORWARDING_FLOWS)
def test_forwarding_detaches_mutable_inputs(tmp_path, flow):
    details = {"nested": [{"value": 1}]}
    result = forwarded_result(tmp_path, details, flow)
    details["nested"][0]["value"] = 2
    assert result.details == {"nested": [{"value": 1}]}
    assert wire_roundtrip(forwarded_payload(result))["details"] == result.details


@pytest.mark.parametrize("place", ["key", "value"])
def test_unchanged_hook_keeps_historical_raw_pair(tmp_path, place):
    text = "\ud83d\ude00"
    details = {text: "x"} if place == "key" else {"x": text}
    seen = []

    def change_content(event, ctx):
        seen.append(event.details)
        return ToolResultTransform(content="changed")

    transformed = dispatch_tool_result_hooks(
        (change_content,),
        tool_name="normal",
        content="old",
        is_error=False,
        details=details,
        cwd=str(tmp_path),
        has_ui=False,
    )
    assert transformed.content == "changed"
    assert transformed.details is UNCHANGED_TOOL_DETAILS
    assert seen[0] is not details
    actual = next(iter(seen[0])) if place == "key" else seen[0]["x"]
    assert [ord(c) for c in actual] == [0xD83D, 0xDE00]

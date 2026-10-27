"""Recorded v1 compatibility and actual normal composite/consumer value paths."""

from __future__ import annotations

import copy
import json
import math
from dataclasses import replace
from pathlib import Path

import pytest
from test_native_nested_record import _service
from test_native_ui_transcript import _Harness as TranscriptHarness

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.loop import AgentLoop, _transform_tool_result
from pipy_harness.native.agent.loop_policy import AgentToolResultTransform
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
)
from pipy_harness.native.agent.nested_record import NestedCallEvidence
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.agent.tool_details import (
    LEGACY_TOOL_DETAILS_NONE,
    NO_TOOL_DETAILS,
    UNCHANGED_TOOL_DETAILS,
)
from pipy_harness.native.automation.durable_output import message as completed_json
from pipy_harness.native.automation.serialize import serialize_message, tool_result_json
from pipy_harness.native.coding.compaction import file_operation_input
from pipy_harness.native.durable.canonical_codec import (
    CanonicalCodecError,
    decode_canonical,
    encode_canonical,
)
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree
from pipy_harness.native.tool_rows import (
    ToolRowResult,
    ToolRowState,
    nested_lines_from_details,
)


def result(details=NO_TOOL_DETAILS):
    return AgentToolResultMessage(
        "pipy-tool-details",
        "codemode",
        ProductContent("result"),
        "parent",
        details=details,
    )


@pytest.mark.parametrize("details", [None, False, "scalar", [None]])
def test_completed_transcript_uses_actual_finite_nested_evidence(details, tmp_path):
    service = _service()
    service.call("read", '{"path":"attempted-file"}')
    message = AgentLoop._with_nested_record(result(details), service)
    transcript = TranscriptHarness().component
    state = ToolRowState("codemode", {}, tmp_path)
    transcript.start_tool(state)
    transcript.finish_tool(
        ToolRowResult(
            "result",
            details=message.details,
            is_error=False,
            nested_call_evidence=message.nested_call_evidence,
        )
    )
    assert [line.name for line in state.nested_lines] == ["read"]
    assert "attempted-file" in state.nested_lines[0].arguments
    assert state.result is not None and state.result.details == details


def test_actual_historical_none_arena_preserves_unknown_presence_and_bytes():
    # Captured with the immutable df5c source's actual old encoder, not the new one.
    path = Path(__file__).parent / "fixtures/durable/tool-details-legacy-v1.json"
    arena = json.loads(path.read_text())
    restored = decode_canonical(arena)
    assert restored.details is LEGACY_TOOL_DETAILS_NONE
    assert encode_canonical(restored) == arena
    assert json.dumps(encode_canonical(restored), ensure_ascii=True) == json.dumps(
        arena, ensure_ascii=True
    )
    assert "details" not in tool_result_json(restored)
    assert completed_json(restored)["details_state"] == "legacy_unspecified"


@pytest.mark.parametrize("details", [NO_TOOL_DETAILS, None, False, 0, "s", []])
def test_explicit_v1_refuses_presence_loss(details):
    with pytest.raises(CanonicalCodecError):
        encode_canonical(result(details), codec_version=1)


def test_mapping_values_still_choose_v1_without_new_field():
    arena = encode_canonical(result({"x": [-0.0, 1.0, 1]}))
    assert arena["codec_version"] == 1
    assert "nested_call_evidence" not in arena["nodes"][0]["keys"]
    restored = decode_canonical(arena)
    assert type(restored.details["x"][1]) is float
    assert type(restored.details["x"][2]) is int
    assert math.copysign(1, restored.details["x"][0]) == -1


def test_mixed_rich_next_request_uses_v2_without_rewriting_old_message():
    old = json.loads(
        (
            Path(__file__).parent / "fixtures/durable/tool-details-legacy-v1.json"
        ).read_text()
    )
    legacy = decode_canonical(old)
    request = ProviderRequest(
        "system",
        "input",
        "fake",
        "model",
        Path("/workspace"),
        messages=(legacy, result(None), result([False, -0.0])),
    )
    snapshot = snapshot_provider_request(request)
    arena = encode_canonical(snapshot)
    assert arena["codec_version"] == 2
    restored = decode_canonical(arena).request.messages
    assert restored[0].details is LEGACY_TOOL_DETAILS_NONE
    assert encode_canonical(restored[0]) == old
    assert restored[1].details is None
    assert restored[2].details == [False, -0.0]


@pytest.mark.parametrize(
    "replacement", [UNCHANGED_TOOL_DETAILS, NO_TOOL_DETAILS, None, False, [], "scalar"]
)
def test_actual_policy_transform_distinguishes_unchanged_clear_and_null(replacement):
    source = result({"keep": True})

    class Policy:
        def transform_result(self, call, original, arguments, parent):
            return AgentToolResultTransform(original.content, details=replacement)

    transformed = _transform_tool_result(
        Policy(),
        AgentToolCall("parent", "codemode", ProductContent("{}")),
        source,
        {},
        None,
    )
    expected = source.details if replacement is UNCHANGED_TOOL_DETAILS else replacement
    assert transformed.details == expected
    assert ("details" in tool_result_json(transformed)) is (
        expected is not NO_TOOL_DETAILS
    )


@pytest.mark.parametrize("details", [None, False, 0, "scalar", [None, 1]])
def test_actual_nested_service_record_preserves_nonmapping_details_across_consumers(
    tmp_path, details
):
    service = _service()
    service.call("read", '{"path":"attempted-file"}')
    message = AgentLoop._with_nested_record(result(details), service)
    assert message.details == details
    evidence = message.nested_call_evidence
    assert type(evidence) is NestedCallEvidence
    assert evidence.to_json() == service.durable_record()
    assert nested_lines_from_details(message.details, evidence)[0].name == "read"
    assert "attempted-file" in file_operation_input((message,))
    assert tool_result_json(message)["details"] == details
    assert serialize_message(message)["nestedCallEvidence"] == service.durable_record()
    assert completed_json(message)["nested_call_evidence"] == service.durable_record()
    arena = encode_canonical(message)
    assert arena["codec_version"] == 2
    restored = decode_canonical(arena)
    assert restored.details == details
    assert restored.nested_call_evidence == evidence
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "session")
    tree.append_message(
        AgentAssistantMessage(
            ProductContent(""),
            tool_calls=(AgentToolCall("parent", "codemode", ProductContent("{}")),),
        )
    )
    tree.append_message(message)
    reopened = NativeSessionTree.open(tree.path, strict=True)
    replay = [
        entry.message
        for entry in reopened.get_entries()
        if isinstance(entry, MessageEntry)
    ][-1]
    assert replay.details == details
    assert replay.nested_call_evidence == evidence


def test_legacy_nested_mapping_and_absent_branch_do_not_duplicate_evidence():
    service = _service()
    service.call("read", "{}")
    for details in (NO_TOOL_DETAILS, LEGACY_TOOL_DETAILS_NONE, {"local": True}):
        message = AgentLoop._with_nested_record(result(details), service)
        assert message.nested_call_evidence is None
        assert message.details == {
            **(details if isinstance(details, dict) else {}),
            "nestedCalls": service.durable_record(),
        }
        assert encode_canonical(message)["codec_version"] == 1


def test_duplicate_or_malformed_typed_evidence_refuses():
    service = _service()
    service.call("read", "{}")
    evidence = NestedCallEvidence.from_record(service.durable_record())
    with pytest.raises(ValueError, match="Conflicting"):
        replace(
            result({"nestedCalls": service.durable_record()}),
            nested_call_evidence=evidence,
        )
    with pytest.raises(ValueError):
        NestedCallEvidence.from_json({"calls": [], "complete": True, "arbitrary": {}})


def test_typed_nested_preview_field_rejects_null_instead_of_erasing_presence():
    with pytest.raises(ValueError, match="Invalid nested call evidence"):
        NestedCallEvidence.from_json(
            {"calls": [], "complete": True, "argumentPreviews": None}
        )


def test_transform_only_marker_cannot_enter_a_result_or_canonical_history():
    with pytest.raises(ValueError):
        result(UNCHANGED_TOOL_DETAILS)
    with pytest.raises(CanonicalCodecError):
        encode_canonical(UNCHANGED_TOOL_DETAILS)


@pytest.mark.parametrize("new_details", [NO_TOOL_DETAILS, None, {"after": True}, [0]])
def test_policy_replacement_keeps_one_actual_nested_record_channel(new_details):
    service = _service()
    service.call("read", "{}")
    source = AgentLoop._with_nested_record(result(None), service)

    class Policy:
        def transform_result(self, call, original, arguments, parent):
            return AgentToolResultTransform(original.content, details=new_details)

    changed = _transform_tool_result(
        Policy(),
        AgentToolCall("parent", "codemode", ProductContent("{}")),
        source,
        {},
        None,
    )
    if new_details is NO_TOOL_DETAILS or isinstance(new_details, dict):
        assert changed.nested_call_evidence is None
        assert changed.details["nestedCalls"] == service.durable_record()
    else:
        assert changed.details == new_details
        assert changed.nested_call_evidence.to_json() == service.durable_record()


@pytest.mark.parametrize("details", [None, [False, 0], "scalar"])
def test_real_composite_loop_publishes_rich_value_and_actual_service_record(details):
    from test_native_agent_loop import _run_input, _tool_result
    from test_native_agent_nested_calls import _Runner, _setup

    from pipy_harness.native.agent.events import ToolCallCompleted
    from pipy_harness.native.agent.tools import ToolExecutionOutcome

    def body(call, service):
        service.call("read", '{"path":"attempted"}')
        return ToolExecutionOutcome(replace(_tool_result(call), details=details))

    runner = _Runner(body)
    loop, _, events, _ = _setup(runner)
    loop.run(_run_input())
    parent = next(
        event.result for event in events.events if isinstance(event, ToolCallCompleted)
    )
    assert parent.details == details
    assert parent.nested_call_evidence.to_json() == runner.services[0].durable_record()
    assert serialize_message(parent)["details"] == details


def test_final_details_domain_has_no_new_global_node_or_recursion_cutoff():
    from pipy_harness.native.automation.durable_output import json_output

    value = {"end": None}
    for _ in range(1500):
        value = {"child": value}
    message = decode_canonical(encode_canonical(result(value)))
    cursor = message.details
    for _ in range(1500):
        cursor = cursor["child"]
    assert cursor == {"end": None}
    # Exercise the actual iterative CLI value encoder, not json.dumps's stack.
    encoded = json_output(completed_json(message))
    assert encoded.count('"child"') == 1500


@pytest.mark.parametrize("details", [None, False, "scalar", [None]])
def test_actual_exported_html_script_draws_typed_actual_nested_record(
    details, tmp_path
):
    from test_native_export_distribution import _decode_html_payload
    from test_native_export_nested_calls import _nested, _page
    from test_native_export_tool_rows import _tree

    from pipy_harness.native.export_distribution import export_native_session_to_html

    service = _service()
    service.call("read", '{"path":"attempted-file"}')
    message = AgentLoop._with_nested_record(result(details), service)
    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            ProductContent(""),
            tool_calls=(
                AgentToolCall(
                    message.tool_request_id, "codemode", ProductContent("{}")
                ),
            ),
        ),
        message,
    )
    output = export_native_session_to_html(tree, tmp_path / "result.html")
    _preview, full = _nested(_page(tmp_path, output)["entries"])
    assert "read" in full and "attempted-file" in full
    exported = _decode_html_payload(output)
    row = next(
        row["message"]
        for row in exported["entries"]
        if row.get("message", {}).get("role") == "tool"
    )
    assert row["details"] == details
    assert row["nestedCallEvidence"] == service.durable_record()


@pytest.mark.parametrize(
    "details", [None, False, 0, "scalar", [None], {}, NO_TOOL_DETAILS]
)
def test_reopened_history_keeps_actual_nested_rows_and_original_details(
    details, tmp_path
):
    from test_native_session_history_render import _box, _Terminal

    service = _service()
    service.call("read", '{"path":"attempted-file"}')
    message = AgentLoop._with_nested_record(result(details), service)
    call = AgentToolCall("parent", "codemode", ProductContent("{}"))
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "stored")
    tree.append_message(AgentAssistantMessage(ProductContent(""), tool_calls=(call,)))
    tree.append_message(message)
    reopened = NativeSessionTree.open(tree.path, strict=True)
    restored = [
        entry.message
        for entry in reopened.get_entries()
        if isinstance(entry, MessageEntry)
    ][-1]
    assert restored.details == message.details
    assert restored.nested_call_evidence == message.nested_call_evidence
    terminal = _Terminal(tmp_path / "screen")
    terminal.tree = reopened
    terminal.custom.ctl.session_tree = reopened
    terminal.history.render_active_branch()
    text = "\n".join(
        text
        for kind, lines in terminal.rows()
        if kind == "tool_box"
        for _, text in _box(lines)
    )
    assert "read" in text and "attempted-file" in text
    # Replay is a read: the original stored full fact is still exact.
    same = [
        entry.message
        for entry in NativeSessionTree.open(tree.path, strict=True).get_entries()
        if isinstance(entry, MessageEntry)
    ][-1]
    assert same.details == message.details
    assert same.nested_call_evidence == message.nested_call_evidence


def test_noncanonical_null_nested_evidence_is_not_a_second_v2_encoding():
    arena = copy.deepcopy(encode_canonical(result(None)))
    assert arena["codec_version"] == 2
    root = arena["nodes"][0]
    assert "nested_call_evidence" not in root["keys"]
    root["keys"].append("nested_call_evidence")
    root["children"].append(len(arena["nodes"]))
    arena["nodes"].append({"tag": "scalar", "value": None})
    with pytest.raises(CanonicalCodecError):
        decode_canonical(arena)

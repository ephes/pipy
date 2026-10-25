"""Actual committed rich output, finite field inventory and private boundaries."""

import asyncio
import json
import math
from dataclasses import fields, replace

import pytest
from cli_process_support import run_cli
from test_canonical_codec import SNAPSHOT
from test_product_session import profile
from test_tool_boundaries import MultiRound, RichTool

from pipy_harness.native.agent.content import NO_STRUCTURED_CONTENT
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentToolResultMessage,
)
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.automation.durable_output import (
    json_output,
    run_output,
    terminal,
)
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.completed_values import (
    CompletedMessage,
    CompletedSnapshot,
    TerminalOutcome,
)
from pipy_harness.native.durable.product_values import DurableRunResult
from pipy_harness.native.durable.tool_execution import ToolRegistration
from pipy_harness.sdk import (
    create_durable_product_session,
    open_durable_product_session,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


def test_completed_output_field_inventory_requires_explicit_future_alignment():
    assert {f.name for f in fields(AgentAssistantMessage)} == {
        "content",
        "tool_calls",
        "stop_reason",
        "error_message",
        "usage",
        "provider",
        "model",
        "provider_thinking_level",
        "blocks",
        "api",
    }
    assert {f.name for f in fields(AgentToolResultMessage)} == {
        "tool_request_id",
        "tool_name",
        "content",
        "provider_correlation_id",
        "is_error",
        "details",
        "structured_content",
        "blocks",
        "terminate",
    }
    assert {f.name for f in fields(CompletedMessage)} == {
        "conversation_id",
        "entry_id",
        "commit_seq",
        "by_task_id",
        "canonical",
        "provider_outcome",
        "tool_output",
    }
    assert {f.name for f in fields(CompletedSnapshot)} == {
        "conversation_id",
        "revision",
        "publication_seq",
        "messages",
        "rounds",
        "outcomes",
        "usage",
    }
    assert {f.name for f in fields(DurableRunResult)} == {
        "task_id",
        "status",
        "inspection",
        "completed",
    }
    assert {f.name for f in fields(TerminalOutcome)} == {
        "task_id",
        "kind",
        "version",
        "outcome",
    }


@pytest.mark.parametrize(
    "structured",
    [
        NO_STRUCTURED_CONTENT,
        None,
        {"number": [0, 0.0, -0.0], "nested": [None, False, []]},
    ],
)
def test_actual_rich_fields_saved_usage_and_receipt_payloads_survive_json_and_reopen(
    tmp_path, structured
):
    port = RichTool(structured)
    registry = {"rich": ToolRegistration(port, "fixture.rich", "1")}
    wire = MultiRound((("rich",), ()))
    template = snapshot_provider_request(
        replace(
            SNAPSHOT.request,
            provider_name="openai",
            model_id="gpt-test",
            cwd=tmp_path,
            session_id=None,
            user_prompt="",
            messages=(),
            available_tools=(port.definition,),
        )
    )
    path = tmp_path / "rich.sqlite"

    async def scenario():
        product = await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools=registry,
            request_template=template,
        )
        try:
            await product.submit("input\nUnicode Ä")
            actual = await product.resume()
            output = json.loads(json_output(run_output(actual)))
            rows = output["completed"]["messages"]
            assert [r["message"]["role"] for r in rows] == [
                "user",
                "assistant",
                "tool_result",
                "assistant",
            ]
            for saved, encoded in zip(actual.completed.messages, rows, strict=True):
                assert (
                    encoded["entry_id"] == saved.entry_id
                    and encoded["commit_seq"] == saved.commit_seq
                )
                assert OwnedJson(encoded["provider_outcome"]) == OwnedJson(
                    None
                    if saved.provider_outcome is None
                    else saved.provider_outcome.thaw()
                )
                assert OwnedJson(encoded["tool_output"]) == OwnedJson(
                    None if saved.tool_output is None else saved.tool_output.thaw()
                )
            assistant = rows[1]["message"]
            assert assistant["tool_calls"][0]["arguments_json"] == ' { "x":  1 } '
            assert assistant["usage"]["reasoning"] == 3
            tool = rows[2]["message"]
            assert [b["type"] for b in tool["blocks"]] == ["text", "image", "text"]
            assert tool["details"] == {
                "nested": {"null": None, "empty": [], "false": False}
            }
            assert tool["provider_correlation_id"] == "call:0" and tool["is_error"]
            assert tool["tool_request_id"] == port.calls[0].tool_request_id
            assert rows[2]["tool_execution"]["execution_seconds"] >= 0
            if structured is NO_STRUCTURED_CONTENT:
                assert "structured_content" not in tool
            else:
                assert tool["structured_content"] == structured
                if structured is not None:
                    numbers = tool["structured_content"]["number"]
                    assert [type(n) for n in numbers] == [
                        int,
                        float,
                        float,
                    ] and math.copysign(1, numbers[2]) == -1
            assert output["completed"]["usage"] == actual.completed.usage.thaw()
            assert len(output["completed"]["usage"]["settlements"]) == 2
            assert len(output["completed"]["rounds"]) == 1
            for sentinel in (
                "fixture-key",
                "auth.json",
                "material_ref",
                "settings",
                "workspace",
            ):
                assert sentinel not in json_output(output)
            expected = output["completed"]["messages"]
        finally:
            await product.close()
        actual_cli = run_cli(
            tmp_path,
            "rich-terminal",
            [
                "resume",
                "--store",
                str(path),
                "--workspace",
                str(tmp_path),
                "--format",
                "json",
            ],
        )
        assert actual_cli.returncode == 0, actual_cli.stderr
        serialized = json.loads(actual_cli.stdout)
        assert OwnedJson(serialized["completed"]["messages"]) == OwnedJson(expected)
        product = await open_durable_product_session(path, workspace=tmp_path, tools={})
        try:
            assert (
                run_output(await product.resume())["completed"]["messages"] == expected
            )
            assert len(wire.calls) == 2
        finally:
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 15))


def test_raw_terminal_diagnostics_and_kind_never_escape_closed_output():
    value = TerminalOutcome(
        1,
        "private-kind-sentinel",
        1,
        OwnedJson(
            {
                "status": "faulted",
                "error": {"private": "private-authority-sentinel"},
                "result": {"checkpoint": "private-checkpoint-sentinel"},
            }
        ),
    )
    assert terminal(value) == {
        "task_id": 1,
        "family": "custom",
        "version": 1,
        "status": "faulted",
    }
    assert "private" not in json_output(terminal(value))
    retired = TerminalOutcome(
        2, "pi.tool", 1, OwnedJson({"status": "aborted", "reason": "operator_retry"})
    )
    assert terminal(retired) == {
        "task_id": 2,
        "family": "model_tool",
        "version": 1,
        "status": "aborted",
        "reason": "operator_retry",
    }


def test_actual_serialized_cli_retains_deep_mapping_details_without_codec_cutoff(
    tmp_path,
):
    from pipy_harness.native.agent.identity import AGENT_TOOL_REQUEST_ID_PREFIX
    from pipy_harness.native.durable.sqlite import _decode
    from pipy_harness.native.tools.base import ToolExecutionResult

    nested: object = {"null": None, "number": [0, 0.0, -0.0]}
    # Stay inside the existing DTO's stdlib-JSON domain on Python 3.11.
    # The independent CLI encoder test below still exercises depth1500.
    for _ in range(300):
        nested = {"next": nested}
    ToolExecutionResult(
        AGENT_TOOL_REQUEST_ID_PREFIX + "deep-fixture",
        "valid DTO",
        details={"deep": nested},
    )

    class DeepTool(RichTool):
        def invoke(self, request, context):
            return replace(super().invoke(request, context), details={"deep": nested})

    port = DeepTool(NO_STRUCTURED_CONTENT)
    wire = MultiRound((("rich",), ()))
    path = tmp_path / "deep.sqlite"

    async def populate():
        product = await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools={"rich": ToolRegistration(port, "fixture.rich", "1")},
        )
        try:
            await product.submit("input")
            result = await product.resume()
            assert result.inspection.outcome == "completed"
            return run_output(result)
        finally:
            await product.close()

    expected = asyncio.run(asyncio.wait_for(populate(), 15))
    actual = run_cli(
        tmp_path,
        "deep-terminal",
        [
            "resume",
            "--store",
            str(path),
            "--workspace",
            str(tmp_path),
            "--format",
            "json",
        ],
    )
    assert actual.returncode == 0, actual.stderr
    decoded = _decode(actual.stdout)
    assert OwnedJson(decoded["completed"]["messages"]) == OwnedJson(
        expected["completed"]["messages"]
    )
    details = decoded["completed"]["messages"][2]["message"]["details"]
    assert OwnedJson(details) == OwnedJson({"deep": nested})


def test_json_output_preserves_valid_deep_finite_domain_and_rejects_cycles():
    value = {"field": [False, None, 0, 0.0, -0.0, "\ud800"]}
    assert OwnedJson(json.loads(json_output(value))) == OwnedJson(value)
    nested: object = None
    for _ in range(1500):
        nested = [nested]
    encoded = json_output(nested)
    assert encoded == "[" * 1500 + "null" + "]" * 1500 + "\n"
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError):
        json_output(cycle)


def test_provider_outcome_encoding_is_public_payload_not_request_settings():
    # The public receipt root remains an actual decoded ProviderTurnOutcome;
    # it is never the enclosing private checkpoint frame or request.
    from test_canonical_codec import PROVIDER

    from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
    from pipy_harness.native.durable.canonical_codec import encode_canonical

    encoded = json.loads(
        json_output(encode_canonical(ProviderTurnOutcome(result=PROVIDER)))
    )
    assert type(decode_canonical(encoded)) is ProviderTurnOutcome

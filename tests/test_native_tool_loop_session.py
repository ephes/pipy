"""Slice 4 tests: `NativeToolReplSession` skeleton.

These tests pin the loop's behavior using a test-only `_FixtureTool` that
echoes its `text` argument back. The production tool registry stays empty;
the loop is exercised by injecting the fixture registry directly. Real
providers all advertise `supports_tool_calls=False` at this point, so the
session is also exercised against `FakeNativeProvider` with a programmable
script.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
    FakeNativeProvider,
    NativeToolReplResult,
    NativeToolReplSession,
    OpenAIResponsesProvider,
    ProviderToolCall,
    production_tool_registry,
)
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
)


@dataclass(frozen=True, slots=True)
class _FixtureEchoTool:
    """Test-only echo tool used to exercise the loop end-to-end."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="echo",
            description="Return the provided text verbatim.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "maxLength": 1024},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        )

    def invoke(
        self, request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        text = str(request.arguments["text"])
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=text,
            provider_correlation_id=request.provider_correlation_id,
        )


def _make_call(
    tool_name: str,
    arguments_json: str,
    *,
    correlation_id: str = "call_test_1",
) -> ProviderToolCall:
    return ProviderToolCall(
        provider_correlation_id=correlation_id,
        tool_name=tool_name,
        arguments_json=arguments_json,
    )


def _run_session(
    *,
    tool_calls_script: tuple[tuple[ProviderToolCall, ...], ...],
    tool_registry: Mapping[str, ToolPort] | None,
    user_inputs: tuple[str, ...],
    tmp_path: Path,
    tool_budget: int = 10,
) -> tuple[NativeToolReplResult, str, str]:
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=tool_calls_script,
    )
    session = NativeToolReplSession(
        provider=provider,
        tool_registry=dict(tool_registry or {}),
        tool_budget=tool_budget,
    )
    input_stream = io.StringIO("\n".join(user_inputs) + "\n")
    output_stream = io.StringIO()
    error_stream = io.StringIO()
    result = session.run(
        workspace_root=tmp_path,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )
    return result, output_stream.getvalue(), error_stream.getvalue()


# --------------------- production registry holds read and ls --------------


def test_production_tool_registry_holds_read_and_ls():
    registry = production_tool_registry()

    assert set(registry.keys()) == {"read", "ls"}
    assert registry["read"].definition.name == "read"
    assert registry["ls"].definition.name == "ls"


# ------------------------- provider capability gate ------------------------


def test_session_rejects_provider_without_tool_call_capability():
    provider = OpenAIResponsesProvider(model_id="gpt-test")

    with pytest.raises(ValueError, match="supports_tool_calls"):
        NativeToolReplSession(provider=provider)


def test_session_rejects_fake_provider_when_capability_not_flipped():
    provider = FakeNativeProvider()

    with pytest.raises(ValueError, match="supports_tool_calls"):
        NativeToolReplSession(provider=provider)


# --------------------------- tool budget validation -------------------------


def test_session_rejects_tool_budget_outside_supported_range():
    provider = FakeNativeProvider(supports_tool_calls=True)

    with pytest.raises(ValueError, match=r"\[1, 25\]"):
        NativeToolReplSession(provider=provider, tool_budget=0)
    with pytest.raises(ValueError, match=r"\[1, 25\]"):
        NativeToolReplSession(provider=provider, tool_budget=26)


def test_session_rejects_non_int_tool_budget():
    provider = FakeNativeProvider(supports_tool_calls=True)

    with pytest.raises(TypeError, match="tool_budget"):
        NativeToolReplSession(provider=provider, tool_budget=True)


# --------------------------- successful invocation --------------------------


def test_session_invokes_fixture_tool_and_reports_metadata(tmp_path: Path):
    tool = _FixtureEchoTool()
    script = (
        (_make_call("echo", '{"text": "hello"}'),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": tool},
        user_inputs=("please echo hello",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.exit_code == 0
    assert result.user_turn_count == 1
    assert result.tool_invocation_count == 1
    assert result.malformed_argument_count == 0
    assert result.consecutive_malformed_streak == 0
    assert result.budget_exhausted_count == 0
    assert result.error_type is None
    assert stderr == ""


# ----------------------------- unknown tool name ----------------------------


def test_unknown_tool_is_returned_as_error_observation(tmp_path: Path):
    script = (
        (_make_call("missing_tool", "{}"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={},
        user_inputs=("call missing",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 0
    assert result.malformed_argument_count == 1
    assert result.consecutive_malformed_streak == 1
    assert stderr == ""


# ------------------------------ malformed JSON ------------------------------


def test_invalid_arguments_json_is_returned_as_error_observation(tmp_path: Path):
    tool = _FixtureEchoTool()
    script = (
        (_make_call("echo", "{not json"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": tool},
        user_inputs=("call echo",),
        tmp_path=tmp_path,
    )

    assert result.malformed_argument_count == 1
    assert result.tool_invocation_count == 0
    assert stderr == ""


# --------------------------- schema validation fail -------------------------


def test_schema_violation_is_returned_as_error_observation(tmp_path: Path):
    tool = _FixtureEchoTool()
    script = (
        (_make_call("echo", "{}"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": tool},
        user_inputs=("call echo",),
        tmp_path=tmp_path,
    )

    assert result.malformed_argument_count == 1
    assert result.consecutive_malformed_streak == 1
    assert result.tool_invocation_count == 0
    assert stderr == ""


# --------------------- three consecutive malformed = fatal ------------------


def test_three_consecutive_malformed_turns_are_fatal(tmp_path: Path):
    script = (
        (_make_call("missing_a", "{}"),),
        (_make_call("missing_b", "{}"),),
        (_make_call("missing_c", "{}"),),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={},
        user_inputs=("call missing",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.FAILED
    assert result.exit_code == 1
    assert result.error_type == "NativeToolLoopMalformedFatal"
    assert result.malformed_argument_count == 3
    assert result.consecutive_malformed_streak == 3
    assert "3 consecutive malformed tool calls" in stderr


def test_three_malformed_in_one_response_are_fatal(tmp_path: Path):
    script = (
        (
            _make_call("missing_a", "{}", correlation_id="a"),
            _make_call("missing_b", "{}", correlation_id="b"),
            _make_call("missing_c", "{}", correlation_id="c"),
        ),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={},
        user_inputs=("call three missing",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.FAILED
    assert result.malformed_argument_count == 3
    assert "3 consecutive malformed tool calls" in stderr


# ---------------------- one success resets the streak -----------------------


def test_one_success_resets_malformed_streak(tmp_path: Path):
    tool = _FixtureEchoTool()
    script = (
        (_make_call("missing", "{}", correlation_id="a"),),
        (_make_call("missing", "{}", correlation_id="b"),),
        (_make_call("echo", '{"text": "hi"}', correlation_id="c"),),
        (_make_call("missing", "{}", correlation_id="d"),),
        (_make_call("missing", "{}", correlation_id="e"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": tool},
        user_inputs=("go",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 1
    assert result.malformed_argument_count == 4
    assert result.consecutive_malformed_streak == 2


# --------------------------- per-turn budget enforcement --------------------


def test_budget_exhausted_emits_observation_without_invoking(tmp_path: Path):
    tool = _FixtureEchoTool()
    script = (
        (_make_call("echo", '{"text": "1"}', correlation_id="a"),),
        (_make_call("echo", '{"text": "2"}', correlation_id="b"),),
        (_make_call("echo", '{"text": "3"}', correlation_id="c"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": tool},
        user_inputs=("go",),
        tmp_path=tmp_path,
        tool_budget=2,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 2
    assert result.budget_exhausted_count == 1
    assert stderr == ""


# ---------------------- final text printed on stdout -----------------------


def test_final_text_is_printed_when_no_tool_calls(tmp_path: Path):
    script = ((),)
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=script,
        final_text="hello world",
    )
    session = NativeToolReplSession(provider=provider)
    input_stream = io.StringIO("hi\n")
    output_stream = io.StringIO()
    error_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.user_turn_count == 1
    assert "hello world" in output_stream.getvalue()
    assert error_stream.getvalue() == ""


# ---------------- session ends on EOF and stays archive-safe ---------------


def test_session_ends_at_eof_with_zero_turns(tmp_path: Path):
    provider = FakeNativeProvider(supports_tool_calls=True)
    session = NativeToolReplSession(provider=provider)
    input_stream = io.StringIO("")
    output_stream = io.StringIO()
    error_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.user_turn_count == 0
    assert result.tool_invocation_count == 0


def test_native_tool_repl_result_has_only_metadata_fields():
    from dataclasses import fields

    field_names = {field.name for field in fields(NativeToolReplResult)}

    forbidden = {
        "arguments",
        "diff",
        "diffs",
        "file_content",
        "file_contents",
        "model_output",
        "patch",
        "payload",
        "prompt",
        "provider_response",
        "stderr",
        "stdout",
        "tool_payload",
    }
    assert forbidden.isdisjoint(field_names)

"""The error text of an extension tool that raises (goal check r4, S7).

Pi's agent loop turns what a tool's ``execute`` or ``prepareArguments``
throws into the call's error result with ``error.message``
(``agent-loop.ts`` ``createErrorToolResult``), and Pi's docs tell extension
authors to throw to fail a call. pipy keeps the message too, printable and
bounded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.extensions import ExtensionTool
from pipy_harness.native.extension_types import RegisteredTool
from pipy_harness.native.extensions.tool_port import _ExtensionToolPort
from pipy_harness.native.tools import ToolContext, ToolRequest
from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolExecutionResult,
    make_tool_request_id,
)


def _port(handler=None, prepare=None) -> _ExtensionToolPort:
    tool = ExtensionTool(
        name="probe",
        description="probe",
        input_schema={"type": "object"},
        handler=handler or (lambda ctx, params: "ok"),
        prepare_arguments=prepare,
    )
    return _ExtensionToolPort(RegisteredTool(tool=tool, extension="t"), has_ui=False)


def _invoke(port: _ExtensionToolPort, tmp_path: Path) -> ToolExecutionResult:
    return port.invoke(
        ToolRequest(
            tool_request_id=make_tool_request_id(), tool_name="probe", arguments={}
        ),
        ToolContext(workspace_root=tmp_path),
    )


def _raising(error: BaseException):
    def handler(ctx, params):
        raise error

    return handler


class _UnreadableError(Exception):
    def __str__(self) -> str:
        raise RuntimeError("no message")


@pytest.mark.parametrize(
    ("error", "text"),
    [
        (
            ValueError("Missing project id; supply project_id"),
            "Missing project id; supply project_id",
        ),
        # A message over several lines keeps its line breaks and tabs.
        (RuntimeError("first\n\tsecond"), "first\n\tsecond"),
        # Nothing that would steer a terminal reaches the row or the model.
        (RuntimeError("a\x1b]52;c;eA==\x07b‮c\r"), "a ]52;c;eA== b c "),
        # Without a message the type tells what failed.
        (KeyError(), "KeyError"),
        (RuntimeError("  "), "RuntimeError"),
        (_UnreadableError(), "_UnreadableError"),
    ],
)
def test_a_raising_handler_fails_the_call_with_its_message(
    tmp_path: Path, error: BaseException, text: str
) -> None:
    result = _invoke(_port(_raising(error)), tmp_path)

    assert result.is_error is True
    assert result.output_text == text


def test_a_long_message_is_bounded(tmp_path: Path) -> None:
    result = _invoke(_port(_raising(ValueError("x" * 100_000))), tmp_path)

    cap = ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH
    assert len(result.output_text) <= cap
    assert result.output_text.endswith("[pipy: extension tool output truncated]")


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(3)])
def test_an_interrupt_is_not_a_tool_error(tmp_path: Path, error: BaseException) -> None:
    with pytest.raises(type(error)):
        _invoke(_port(_raising(error)), tmp_path)


def test_a_raising_prepare_arguments_reports_its_message() -> None:
    def prepare(arguments: object) -> object:
        raise ValueError("count must be a number\x1b[2J")

    with pytest.raises(ToolArgumentError) as raised:
        _port(prepare=prepare).prepare_arguments({"count": "x"})

    # Pi `prepareToolCall`: the message alone, without the tool's name.
    assert str(raised.value) == "count must be a number [2J"
    assert raised.value.tool_name == "probe"


def test_a_prepare_arguments_without_a_message_reports_its_type() -> None:
    def prepare(arguments: object) -> object:
        raise RuntimeError

    with pytest.raises(ToolArgumentError) as raised:
        _port(prepare=prepare).prepare_arguments({})

    assert str(raised.value) == "RuntimeError"

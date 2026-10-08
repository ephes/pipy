"""Stable builtin definition; not registered in production (CM1 T8a)."""

from __future__ import annotations

from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)

CODEMODE_DESCRIPTION = "Run a Python script that calls other tools. The script runs in an isolated Python 3.14 interpreter with no file system, network or process access of its own. All effects go through `tools.<name>(args)`, which runs the named tool exactly as a direct call would, with the same JSON arguments. It returns the tool's text output or raises `ToolError`. Calls run one at a time. `text(value)` emits output: strings as-is, anything else as JSON. `print()` output is included as well. Unavailable modules: subprocess, socket networking, ssl, threading, ctypes, zlib/gzip/bz2/lzma, sqlite3. Limits: 120 s wall time, 256 MiB memory, about 10k tokens of output. Tool calls already made are not undone when the script fails. Callable tools: read, ls, grep, find, write, edit, bash."


def codemode_definition() -> ToolDefinition:
    """Return an independent normal schema shared with composite validation."""
    return ToolDefinition(
        name="codemode",
        description=CODEMODE_DESCRIPTION,
        input_schema={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python source run as a script.",
                }
            },
            "required": ["code"],
            "additionalProperties": False,
        },
    )


class CodemodeTool:
    """Definition-only ToolPort; direct execution fails closed."""

    @property
    def definition(self) -> ToolDefinition:
        return codemode_definition()

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text="codemode requires the canonical composite service; direct execution is unavailable.",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
        )

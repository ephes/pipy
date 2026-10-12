"""Stable builtin definition for opt-in, availability-gated production codemode."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolReach,
    ToolRequest,
)

# Script globals that list or look up tools and namespaces, which the script
# may then call by any name (Pi ``DISCOVERY_GLOBALS``, with the Python names).
_DISCOVERY_GLOBALS = re.compile(
    r"\b(search_tools|describe_namespace|describe_tool|ALL_TOOLS)\b", re.ASCII
)
_WORDS = re.compile(r"\w+", re.ASCII)


def script_reach(arguments: Mapping[str, Any]) -> ToolReach:
    """The tools a script may call (Pi ``getScriptReach``).

    Every tool when the script uses a discovery global, else the words of its
    source, so ``tools.mcp__docs__search`` and ``tools["mcp__docs__" + name]``
    both name the ``mcp__docs`` namespace.
    """

    code = arguments.get("code")
    if not isinstance(code, str):
        return ()
    if _DISCOVERY_GLOBALS.search(code):
        return "all"
    return tuple(_WORDS.findall(code))


# This small-file example is executed through the real product pipeline in tests.
_READ_FILTER_EXAMPLE = """matches = []
for path in ("alpha.txt", "beta.txt"):
    content = tools.read({"path": path})
    for line in content.splitlines():
        if line.startswith("KEEP "):
            matches.append({"file": path, "value": line[5:]})
text({"matches": matches, "count": len(matches)})"""

CODEMODE_DESCRIPTION = (
    "Run a Python script that calls other tools. The script runs in an isolated Python 3.14 interpreter with no file system, network or process access of its own. All effects go through `tools.<name>(args)`, which runs the named tool exactly as a direct call would, with the same JSON arguments. It returns the tool's text output or raises `ToolError`. Calls run one at a time. `text(value)` emits output: strings as-is, anything else as JSON. `print()` output is included as well. Unavailable modules: subprocess, socket networking, ssl, threading, ctypes, zlib/gzip/bz2/lzma, sqlite3. Limits: 120 s wall time, 256 MiB memory, about 10k tokens of output. Tool calls already made are not undone when the script fails. Callable tools: read, ls, grep, find, write, edit, bash."
    + "\n\nTool results are strings, not result objects. For a text file, tools.read "
    "returns file text as str (possibly with paging/truncation notices); use "
    "splitlines() to process lines. JSON-decode only content that is actually JSON. "
    "Follow read's offset/limit continuation notices when the file is incomplete. "
    "For read/filter tasks, keep intermediate results in Python, filter there, "
    "and text() only the final compact answer. Do not print types, dump raw reads, "
    "or deliberately raise with their contents just to probe this documented format. "
    "Combine the required reads and processing in one script when practical. "
    "Example for two small text files:\n\n```python\n" + _READ_FILTER_EXAMPLE + "\n```"
)


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
        reaches_tools=script_reach,
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

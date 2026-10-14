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

# Pi ``DESCRIPTION_INTRO`` for Python scripts: where a script runs and what a
# call returns.
_DESCRIPTION_INTRO = (
    "Run a Python script that calls other tools. The script runs in an isolated Python 3.14 interpreter with no file system, network or process access of its own. All effects go through `tools.<name>(args)`, which runs the named tool exactly as a direct call would, with the same JSON arguments.\n"
    "- `tools.<name>(args)` returns a string, or a structured value if the tool's declaration says so, and raises `ToolError` on failure. A tool whose declaration returns a structured value returns it on failure too when the failed result carries one; check its fields. `tools.<name>.submit(args)` starts the call and returns at once; its `.result()` returns or raises as the call would, so calls submitted together run at the same time. Calls still running when the script ends are cancelled.\n"
    '- Optional first line: `// @options: {"max_output_tokens": 10000, "timeout_ms": 60000}`\n'
    "- Unavailable modules: subprocess, socket networking, ssl, threading, ctypes, zlib/gzip/bz2/lzma, sqlite3. Limits: 256 MiB memory, about 10k tokens of output; no wall time limit unless `timeout_ms` sets one. Tool calls already made are not undone when the script fails."
)

# Pi ``describeGlobals``: one line per group of globals.
_DESCRIPTION_GLOBALS = (
    "Globals:\n"
    "- `text(value)` emits output: strings as-is, anything else as JSON. `print()` output is included as well.\n"
    '- `image(value)` adds an image to the output: a base64 `data:` URL, a dict with `image_url`, or an image block `{"type": "image", "data", "mimeType"}` as MCP tools return it. PNG, JPEG, GIF and WebP are accepted; remote URLs are not. The image is also saved to a temp file and the result names its path.\n'
    "- `store(key, value)` and `load(key)` keep JSON values across codemode calls; `store(key, None)` deletes a key.\n"
    "- `ALL_TOOLS`, `search_tools(query, limit=8, namespace=None)`, `describe_tool(name)`, `describe_namespace(name)`: find unlisted tools, such as MCP tools."
)

# pipy's own guidance for read/filter scripts (codemode adherence slice).
_DESCRIPTION_READ_GUIDANCE = (
    "A string result is the tool's text, not a result object. For a text file, tools.read "
    "returns file text as str (possibly with paging/truncation notices); use "
    "splitlines() to process lines. JSON-decode only content that is actually JSON. "
    "Follow read's offset/limit continuation notices when the file is incomplete. "
    "For read/filter tasks, keep intermediate results in Python, filter there, "
    "and text() only the final compact answer. Do not print types, dump raw reads, "
    "or deliberately raise with their contents just to probe this documented format. "
    "Combine the required reads and processing in one script when practical. "
    "Example for two small text files:\n\n```python\n" + _READ_FILTER_EXAMPLE + "\n```"
)

# The description without any tool listed. It depends on no registered tool,
# so it is the same before and after tools register; the tools a request
# lists follow it under ``Nested tools:`` (``coding/codemode_description.py``).
CODEMODE_DESCRIPTION = "\n\n".join(
    (_DESCRIPTION_INTRO, _DESCRIPTION_GLOBALS, _DESCRIPTION_READ_GUIDANCE)
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
        # Declared to the model, never callable from a script (Pi).
        exposure="model-only",
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

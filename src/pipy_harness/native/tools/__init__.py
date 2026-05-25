"""Native pipy model-driven tool contracts.

This subpackage holds the small contracts used by the planned native tool-loop
runtime (`pipy repl --agent pipy-native --repl-mode tool-loop`). The contracts
are kept separate from the existing archive-safe `pipy_harness.native.tool`
boundary used by `/read`, `/propose-file`, `/apply-proposal`, and
`/verify just-check`: archive-safe metadata stays in `NativeToolResult`, and
provider-visible tool payloads stay in `ToolExecutionResult`. The two shapes
are deliberately not conflated.

Only the contracts defined here are exported. No production tool
implementations or REPL wiring live in this slice; both are added in later
slices of the Tool-Loop Parity Track.
"""

from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
    validate_arguments,
)
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    LoopMessage,
    ToolResultMessage,
    UserMessage,
)
from pipy_harness.native.tools.edit import EditTool
from pipy_harness.native.tools.find import FindTool
from pipy_harness.native.tools.grep import GrepTool
from pipy_harness.native.tools.ls import LsTool
from pipy_harness.native.tools.read import ReadTool
from pipy_harness.native.tools.write import WriteTool

__all__ = [
    "AssistantMessage",
    "EditTool",
    "FindTool",
    "GrepTool",
    "LoopMessage",
    "LsTool",
    "ReadTool",
    "WriteTool",
    "ToolArgumentError",
    "ToolContext",
    "ToolDefinition",
    "ToolExecutionResult",
    "ToolPort",
    "ToolRequest",
    "ToolResultMessage",
    "UserMessage",
    "make_tool_request_id",
    "validate_arguments",
]

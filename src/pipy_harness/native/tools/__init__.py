"""Native pipy model-driven tool contracts.

This subpackage holds the small contracts used by the native tool-loop
runtime (`pipy repl --agent pipy-native`). The contracts
are kept separate from the archive-safe `pipy_harness.native.tool`
boundary: archive-safe metadata stays in `NativeToolResult`, and
provider-visible tool payloads stay in `ToolExecutionResult`. The two shapes
are deliberately not conflated.

Only the contracts defined here are exported. No production tool
implementations or REPL wiring live in this slice; both are added in later
slices of the Tool-Loop Parity Track.
"""

from pipy_harness.native.tools.base import (
    ToolAnnotations,
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolInfo,
    ToolNamespace,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
    tool_info,
    validate_arguments,
)
from pipy_harness.native.tools.foreign_schema import validate_foreign_arguments

__all__ = [
    "ToolAnnotations",
    "ToolArgumentError",
    "ToolContext",
    "ToolDefinition",
    "ToolExecutionResult",
    "ToolInfo",
    "ToolNamespace",
    "ToolPort",
    "ToolRequest",
    "make_tool_request_id",
    "tool_info",
    "validate_arguments",
    "validate_foreign_arguments",
]

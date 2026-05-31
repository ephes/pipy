"""The model-visible ``bash`` tool.

``BashTool`` lets the model run a single, bounded inspection command through
the shared safe command-execution substrate
(:mod:`pipy_harness.native.command_sandbox`). The tool itself owns no
process-spawning logic: it builds a :class:`CommandPolicy` from the loop's
:class:`ToolContext` (workspace root plus any read-only reference roots),
delegates execution to :func:`run_command`, and shapes the bounded,
secret-redacted result into a provider-visible observation.

Contract with the tool loop:

- A sandbox rejection (shell metacharacters, ``.git``/traversal/symlink/escape
  path arguments, a disallowed executable), a timeout, or a spawn failure is
  surfaced as ``is_error=True`` with a safe reason label. The loop counts these
  toward its malformed-call streak, matching the other tools.
- A command that runs to completion — even with a non-zero exit code — is
  ``is_error=False``. A failing build or test is a normal observation the model
  should reason about, not a malformed tool call.

No raw command, output body, or argument vector is ever archived; the tool
returns the observation to the model only, and the loop's archive boundary
records counters and labels alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pipy_harness.native.command_sandbox import (
    CommandPolicy,
    CommandResult,
    CommandStatus,
    run_command,
)
from pipy_harness.native.command_sandbox import (
    _DEFAULT_ALLOWED_EXECUTABLES as _SANDBOX_DEFAULT_EXECUTABLES,
)
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)

_COMMAND_MAX_LENGTH = 4 * 1024
_CWD_MAX_LENGTH = 1024


@dataclass(frozen=True, slots=True)
class BashTool:
    """Run one bounded inspection command in the workspace sandbox."""

    timeout_seconds: float = 30.0
    max_output_bytes: int = 16 * 1024
    allowed_executables: frozenset[str] = _SANDBOX_DEFAULT_EXECUTABLES

    HARD_MAX_OUTPUT_BYTES: ClassVar[int] = 64 * 1024

    def __post_init__(self) -> None:
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or self.timeout_seconds <= 0
            or self.timeout_seconds > 600.0
        ):
            raise ValueError("BashTool timeout_seconds must be in (0, 600]")
        if (
            isinstance(self.max_output_bytes, bool)
            or not isinstance(self.max_output_bytes, int)
            or self.max_output_bytes < 1
            or self.max_output_bytes > self.HARD_MAX_OUTPUT_BYTES
        ):
            raise ValueError(
                f"BashTool max_output_bytes must be in [1, {self.HARD_MAX_OUTPUT_BYTES}]"
            )
        if not isinstance(self.allowed_executables, frozenset):
            raise ValueError("BashTool allowed_executables must be a frozenset")

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="bash",
            description=(
                "Run a single safe, read-only inspection command on explicitly "
                "named files in the workspace. The command runs without a shell "
                "(no pipes, redirection, command substitution, globbing, or "
                "chaining) and only a curated set of read-only commands (cat, "
                "head, tail, diff, cut, stat, od, "
                "...) is allowed. Operands must name files, not directories: "
                "directory listing and recursive search are refused — use the "
                "dedicated ls/grep/find tools for those. Path arguments under "
                ".git, outside the workspace, or reached through a symlink "
                "escape are refused, and "
                "output is bounded and secret-redacted. Use the dedicated "
                "edit/write tools to change files and /verify for `just check`."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": _COMMAND_MAX_LENGTH,
                        "description": (
                            "The command to run, e.g. 'cat README.md' or "
                            "'head -n 50 src/app.py'. A single program with "
                            "arguments; no shell operators."
                        ),
                    },
                    "cwd": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": _CWD_MAX_LENGTH,
                        "description": (
                            "Optional workspace-relative directory to run in. "
                            "Defaults to the workspace root."
                        ),
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        command = request.arguments["command"]
        cwd = request.arguments.get("cwd")
        policy = CommandPolicy(
            workspace_root=context.workspace_root.resolve(),
            reference_roots=context.reference_roots,
            timeout_seconds=self.timeout_seconds,
            max_output_bytes=self.max_output_bytes,
            allowed_executables=self.allowed_executables,
        )
        result = run_command(command, policy, cwd_relative=cwd)
        output_text, is_error = _shape_observation(result)
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=output_text,
            is_error=is_error,
            provider_correlation_id=request.provider_correlation_id,
        )


def _shape_observation(result: CommandResult) -> tuple[str, bool]:
    if result.status is CommandStatus.REJECTED:
        reason = result.reason.value if result.reason else "rejected"
        return (f"bash: command refused ({reason})", True)
    if result.status is CommandStatus.TIMED_OUT:
        return ("bash: command timed out", True)
    if result.status is CommandStatus.SPAWN_FAILED:
        return ("bash: failed to start command", True)

    sections = [f"exit code: {result.exit_code}"]
    if result.stdout:
        sections.append("[stdout]\n" + result.stdout)
    if result.stderr:
        sections.append("[stderr]\n" + result.stderr)
    if not result.stdout and not result.stderr:
        sections.append("(no output)")
    if result.truncated:
        sections.append("(output truncated)")
    return ("\n".join(sections), False)


__all__ = ["BashTool"]

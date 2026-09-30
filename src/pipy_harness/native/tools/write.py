"""The model-driven `write` tool, following Pi's `write`.

Mirrors ``packages/coding-agent/src/core/tools/write.ts`` (pi-mono
``1b347794e``): the path resolves like Pi's ``resolveToCwd`` (relative to the
working directory or absolute, ``~`` expanded, no deny list), the parent
directories are created, and the file is created or overwritten with the
content as UTF-8. The result is ``Successfully wrote to {path}``; a failure is
the error text alone, as Pi's thrown errors are.

The TUI's write row shows the content from the call arguments
(:mod:`pipy_harness.native.tool_rows`). Mutations of one file are serialized
like Pi's
``withFileMutationQueue`` (:mod:`pipy_harness.native.tools.file_mutation_queue`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)
from pipy_harness.native.tools.file_mutation_queue import (
    file_mutation_queue,
    mutation_queue_key,
)
from pipy_harness.native.tools.fs_errors import (
    node_encodable_text,
    node_fs_message,
    node_null_byte_message,
)
from pipy_harness.native.tools.path_utils import resolve_to_cwd

WRITE_TOOL_DESCRIPTION = (
    "Write content to a file. Creates the file if it doesn't exist, overwrites "
    "if it does. Automatically creates parent directories."
)

OPERATION_ABORTED = "Operation aborted"


class _WriteFailure(Exception):
    """A failure whose message is the tool result text."""


@dataclass(frozen=True, slots=True)
class WriteTool:
    """Create or overwrite a file, like Pi's `write`."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="write",
            description=WRITE_TOOL_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to write (relative or absolute)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        path_arg = request.arguments["path"]
        content = request.arguments["content"]
        if not isinstance(path_arg, str):
            raise ToolArgumentError(
                "write", "path must be a string", field_path=("path",)
            )
        if not isinstance(content, str):
            raise ToolArgumentError(
                "write", "content must be a string", field_path=("content",)
            )
        content = node_encodable_text(content)
        try:
            _write(path_arg, content, context)
        except _WriteFailure as exc:
            return _result(request, str(exc), is_error=True)
        return _result(request, f"Successfully wrote to {path_arg}", is_error=False)


def _write(path_arg: str, content: str, context: ToolContext) -> None:
    try:
        absolute = resolve_to_cwd(path_arg, context.workspace_root)
    except ValueError as exc:
        raise _WriteFailure(str(exc)) from None
    try:
        key = mutation_queue_key(absolute)
    except OSError as exc:
        raise _WriteFailure(node_fs_message(exc, "realpath", str(absolute))) from None
    except ValueError:  # a NUL byte: Node rejects the path before any syscall
        raise _WriteFailure(node_null_byte_message(str(absolute))) from None
    with file_mutation_queue(key):
        _write_locked(absolute, content, context)


def _write_locked(absolute: Path, content: str, context: ToolContext) -> None:
    # Abort is checked after each step, never mid-operation, so the queue
    # stays held until the filesystem call has settled (as in Pi).
    _throw_if_aborted(context)
    directory = absolute.parent
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise _WriteFailure(node_fs_message(exc, "mkdir", str(directory))) from None
    _throw_if_aborted(context)
    # Encoded before the file is opened, so nothing is truncated by a failure.
    data = content.encode("utf-8")
    try:
        with open(absolute, "wb") as handle:
            handle.write(data)
    except OSError as exc:
        raise _WriteFailure(node_fs_message(exc, "open", str(absolute))) from None
    _throw_if_aborted(context)


def _throw_if_aborted(context: ToolContext) -> None:
    if context.cancel_event is not None and context.cancel_event.is_set():
        raise _WriteFailure(OPERATION_ABORTED)


def _result(request: ToolRequest, text: str, *, is_error: bool) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_request_id=request.tool_request_id,
        output_text=text,
        is_error=is_error,
        provider_correlation_id=request.provider_correlation_id,
    )


__all__ = ["OPERATION_ABORTED", "WRITE_TOOL_DESCRIPTION", "WriteTool"]

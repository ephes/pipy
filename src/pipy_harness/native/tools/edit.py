"""The model-driven `edit` tool, following Pi's `edit`.

Mirrors ``packages/coding-agent/src/core/tools/edit.ts`` (pi-mono
``1b347794e``): ``path`` plus ``edits[]`` of ``{oldText, newText}``, each
matched against the original file (exactly, else after Pi's fuzzy
normalization), unique and non-overlapping, applied together. A UTF-8 BOM and
the file's line ending (CRLF or LF) survive the edit. The path resolves like
Pi's ``resolveToCwd`` with no deny list; the file must exist and be readable
and writable. Results and errors carry Pi's texts
(:mod:`pipy_harness.native.tools.edit_diff`), and mutations of one file are
serialized like Pi's ``withFileMutationQueue``.

:meth:`EditTool.prepare_arguments` is Pi's ``prepareArguments``: the executor
calls it before schema validation, so an ``edits`` JSON string, a single edit
object and the legacy top-level ``oldText``/``newText`` still work.

A success carries Pi's ``details``: the display diff (``generateDiffString``)
and ``firstChangedLine``; the TUI's edit row draws the diff from it
(:mod:`pipy_harness.native.tool_rows`). Pi's ``details.patch`` is not
produced yet (TOOLS3b).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)
from pipy_harness.native.tools.edit_diff import (
    Edit,
    EditError,
    apply_edits_to_normalized_content,
    detect_line_ending,
    generate_diff_string,
    normalize_to_lf,
    restore_line_endings,
    split_bom,
)
from pipy_harness.native.tools.file_mutation_queue import (
    file_mutation_queue,
    mutation_queue_key,
)
from pipy_harness.native.tools.fs_errors import (
    error_code,
    node_encodable_text,
    node_fs_message,
    node_null_byte_message,
)
from pipy_harness.native.tools.path_utils import resolve_to_cwd
from pipy_harness.native.tools.write import OPERATION_ABORTED

EDIT_TOOL_DESCRIPTION = (
    "Edit a single file using exact text replacement. Every edits[].oldText "
    "must match a unique, non-overlapping region of the original file. If two "
    "changes affect the same block or nearby lines, merge them into one edit "
    "instead of emitting overlapping edits. Do not include large unchanged "
    "regions just to connect distant changes."
)


@dataclass(frozen=True, slots=True)
class EditTool:
    """Replace exact text regions in one file, like Pi's `edit`."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="edit",
            description=EDIT_TOOL_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to edit (relative or absolute)",
                    },
                    "edits": {
                        "type": "array",
                        "description": (
                            "One or more targeted replacements. Each edit is "
                            "matched against the original file, not "
                            "incrementally. Do not include overlapping or nested "
                            "edits. If two changes touch the same block or nearby "
                            "lines, merge them into one edit instead."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "oldText": {
                                    "type": "string",
                                    "description": (
                                        "Exact text for one targeted replacement. "
                                        "It must be unique in the original file and "
                                        "must not overlap with any other "
                                        "edits[].oldText in the same call."
                                    ),
                                },
                                "newText": {
                                    "type": "string",
                                    "description": (
                                        "Replacement text for this targeted edit."
                                    ),
                                },
                            },
                            "required": ["oldText", "newText"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["path", "edits"],
                "additionalProperties": False,
            },
        )

    @staticmethod
    def prepare_arguments(arguments: object) -> object:
        """Pi ``prepareEditArguments``, run before schema validation."""

        if not isinstance(arguments, Mapping):
            return arguments
        args = dict(arguments)
        edits = args.get("edits")
        # Some models send edits as a JSON string, others a single object.
        if isinstance(edits, str):
            try:
                parsed = json.loads(edits)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                args["edits"] = parsed
            elif _is_single_edit(parsed):
                args["edits"] = [parsed]
        elif _is_single_edit(edits):
            args["edits"] = [edits]

        old_text, new_text = args.get("oldText"), args.get("newText")
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            return args
        current = args.get("edits")
        merged = list(current) if isinstance(current, list) else []
        merged.append({"oldText": old_text, "newText": new_text})
        del args["oldText"], args["newText"]
        args["edits"] = merged
        return args

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        path_arg = request.arguments["path"]
        raw_edits = request.arguments["edits"]
        if not isinstance(path_arg, str):
            raise ToolArgumentError(
                "edit", "path must be a string", field_path=("path",)
            )
        if not isinstance(raw_edits, list | tuple) or not raw_edits:
            return _result(
                request,
                "Edit tool input is invalid. edits must contain at least one "
                "replacement.",
                is_error=True,
            )
        edits = [Edit(str(item["oldText"]), str(item["newText"])) for item in raw_edits]
        try:
            diff, first_changed_line = _edit(path_arg, edits, context)
        except EditError as exc:
            return _result(request, str(exc), is_error=True)
        details: dict[str, object] = {"diff": diff}
        # Pi's `firstChangedLine` is undefined (dropped by JSON) when nothing
        # changed on a numbered line.
        if first_changed_line is not None:
            details["firstChangedLine"] = first_changed_line
        return _result(
            request,
            f"Successfully replaced {len(edits)} block(s) in {path_arg}.",
            is_error=False,
            details=details,
        )


def _is_single_edit(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("oldText"), str)
        and isinstance(value.get("newText"), str)
    )


def _edit(
    path_arg: str, edits: list[Edit], context: ToolContext
) -> tuple[str, int | None]:
    """Apply ``edits``; return Pi's display diff and first changed line.

    Raises :class:`EditError`.
    """

    try:
        absolute = resolve_to_cwd(path_arg, context.workspace_root)
    except ValueError as exc:
        raise EditError(str(exc)) from None
    try:
        key = mutation_queue_key(absolute)
    except OSError as exc:
        raise EditError(node_fs_message(exc, "realpath", str(absolute))) from None
    except ValueError:  # a NUL byte: Node rejects the path before any syscall
        raise EditError(node_null_byte_message(str(absolute))) from None
    with file_mutation_queue(key):
        return _edit_locked(absolute, path_arg, edits, context)


def _edit_locked(
    absolute: Path, path_arg: str, edits: list[Edit], context: ToolContext
) -> tuple[str, int | None]:
    # Abort is checked after each step, never mid-operation, so the queue
    # stays held until the filesystem call has settled (as in Pi).
    _throw_if_aborted(context)
    _check_access(absolute, path_arg)
    _throw_if_aborted(context)
    raw = _read(absolute)
    _throw_if_aborted(context)

    # The model will not include an invisible BOM in oldText.
    bom, content = split_bom(raw.decode("utf-8", errors="replace"))
    ending = detect_line_ending(content)
    base, new = apply_edits_to_normalized_content(
        normalize_to_lf(content), edits, path_arg
    )
    # Surrogates in newText join or become U+FFFD as Node writes them.
    new = node_encodable_text(new)
    _throw_if_aborted(context)
    # Encoded before the file is opened, so nothing is truncated by a failure.
    data = (bom + restore_line_endings(new, ending)).encode("utf-8")
    try:
        with open(absolute, "wb") as handle:
            handle.write(data)
    except OSError as exc:
        raise EditError(node_fs_message(exc, "open", str(absolute))) from None
    _throw_if_aborted(context)
    return generate_diff_string(base, new)


def _check_access(absolute: Path, path_arg: str) -> None:
    """Pi's ``access(path, R_OK | W_OK)`` check and its error text."""

    try:
        os.stat(absolute)
    except OSError as exc:
        code = error_code(exc)
        detail = f"Error code: {code}" if code else str(exc)
        raise EditError(f"Could not edit file: {path_arg}. {detail}.") from None
    if not os.access(absolute, os.R_OK | os.W_OK):
        raise EditError(f"Could not edit file: {path_arg}. Error code: EACCES.")


def _read(absolute: Path) -> bytes:
    try:
        with open(absolute, "rb") as handle:
            return handle.read()
    except IsADirectoryError as exc:
        # Node opens a directory and fails on the read, naming no path.
        raise EditError(node_fs_message(exc, "read", None)) from None
    except OSError as exc:
        raise EditError(node_fs_message(exc, "open", str(absolute))) from None


def _throw_if_aborted(context: ToolContext) -> None:
    if context.cancel_event is not None and context.cancel_event.is_set():
        raise EditError(OPERATION_ABORTED)


def _result(
    request: ToolRequest,
    text: str,
    *,
    is_error: bool,
    details: dict[str, object] | None = None,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_request_id=request.tool_request_id,
        output_text=text,
        is_error=is_error,
        provider_correlation_id=request.provider_correlation_id,
        details=details,
    )


__all__ = ["EDIT_TOOL_DESCRIPTION", "EditTool"]

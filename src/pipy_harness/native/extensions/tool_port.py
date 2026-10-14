"""Native tool-port adapter for activated extension tools."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from pipy_harness.native.agent.content import (
    NO_STRUCTURED_CONTENT,
    ImageContent,
    TextContent,
    ToolResultBlock,
    tool_result_text,
)
from pipy_harness.native.extension_hooks import TOOL_RESULT_MAX_CHARS
from pipy_harness.native.extension_loader import _drive_awaitable
from pipy_harness.native.extension_types import (
    ControlGetAllToolsFn,
    ExtensionModelRuntimeControl,
    ToolResult,
)
from pipy_harness.native.extensions.command_context import make_extension_context
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
    json_safe_details,
    json_safe_value,
)

if TYPE_CHECKING:
    from pipy_harness.native.extension_types import RegisteredTool

# Bound an extension tool's provider-visible output.
_TOOL_OUTPUT_MAX_CHARS: int = 32 * 1024
_TRUNCATED = "\n[pipy: extension tool output truncated]"


class _ExtensionToolPort:
    """Adapt an extension `RegisteredTool` to the native `ToolPort`.

    The loop validates arguments against `definition.input_schema` before
    `invoke`, so the handler receives already-validated input. A handler
    exception becomes a bounded tool error (never a session crash), and
    the provider-visible output is bounded. `KeyboardInterrupt` /
    `SystemExit` propagate.

    Trust model (see the extension-api spec "Local trust boundary"):
    extension tool handlers are trusted local Python that runs in-process
    with the user's own OS permissions — the same trust level as the
    extension's `activate()` function. There is no in-process sandbox, so
    "read-only / pure" is the *documented convention* for this slice, not
    a runtime guarantee; capability *enforcement* (shell / network / write
    permission gates derived from the manifest `[permissions]` table) is a
    later, explicitly-scoped permission-policy slice. What pipy does
    enforce here is the provider boundary: schema-validated input, bounded
    output, and bounded errors.
    """

    def __init__(
        self,
        registered: RegisteredTool,
        *,
        has_ui: bool,
        notify_sink: Callable[[str, str], None] | None = None,
        set_active_tools_fn: Callable[[int, Sequence[str]], bool] | None = None,
        get_all_tools_fn: ControlGetAllToolsFn | None = None,
        flags: Mapping[str, object] | None = None,
        project_trusted: bool = False,
    ) -> None:
        self._registered = registered
        self._has_ui = has_ui
        self._notify_sink = notify_sink
        self._set_active_tools_fn = set_active_tools_fn
        self._get_all_tools_fn = get_all_tools_fn
        self._flags = dict(flags or {})
        self._project_trusted = bool(project_trusted)
        tool = registered.tool
        self._definition = ToolDefinition(
            name=tool.name,
            description=str(tool.description),
            input_schema=dict(tool.input_schema),
            exposure=tool.exposure,
            namespace=tool.namespace,
            annotations=tool.annotations,
            output_schema=tool.output_schema,
            reaches_tools=tool.reaches_tools,
            default_active=tool.default_active,
            prepare_loadout=tool.prepare_loadout,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        generation_id = context.extension_generation_id
        callback = self._set_active_tools_fn
        set_active_tools = (
            None
            if callback is None or generation_id is None
            else lambda names: callback(generation_id, names)
        )
        ctx = make_extension_context(
            str(context.workspace_root),
            self._has_ui,
            self._notify_sink,
            model_runtime=ExtensionModelRuntimeControl(
                set_active_tools_fn=set_active_tools,
                get_all_tools_fn=self._get_all_tools_fn,
                nested_tools=context.nested_tools,
                tool_signal=context.cancel_event,
            ),
            flags=self._flags,
            project_trusted=self._project_trusted,
        )
        try:
            result = self._registered.tool.handler(ctx, dict(request.arguments))
            # Pi awaits a tool's ``execute``: an ``async def`` handler runs
            # to its end here, on the thread that runs the handler.
            if inspect.isawaitable(result):
                result = _drive_awaitable(result)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as err:  # noqa: BLE001 - bound a bad tool
            return ToolExecutionResult(
                tool_request_id=request.tool_request_id,
                output_text=f"extension tool error: {type(err).__name__}",
                is_error=True,
                provider_correlation_id=request.provider_correlation_id,
            )
        cap = ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH
        blocks: tuple[ToolResultBlock, ...] = ()
        if isinstance(result, ToolResult) and result.blocks:
            bounded = _bounded_blocks(result.blocks, cap)
            if bounded is None:
                return ToolExecutionResult(
                    tool_request_id=request.tool_request_id,
                    output_text="extension tool error: invalid result blocks",
                    is_error=True,
                    provider_correlation_id=request.provider_correlation_id,
                )
            blocks = bounded
            content = tool_result_text(blocks)
        elif isinstance(result, ToolResult) and isinstance(result.content, str):
            content = result.content
        elif isinstance(result, ToolResult):
            content = str(result.content)
        else:
            content = str(result)
        if len(content) > cap:
            content = content[: cap - 64] + _TRUNCATED
        # Pi stores `details` on the tool result and hands it to the result
        # renderer; the JSON copy is what the session file keeps, so live and
        # restored rows render from the same value.
        details = result.details if isinstance(result, ToolResult) else None
        # Pi `isError` and `structuredContent`: a handler reports a failure
        # that still carries data by returning it instead of raising.
        is_error = isinstance(result, ToolResult) and bool(result.is_error)
        structured_content = (
            result.structured_content
            if isinstance(result, ToolResult)
            else NO_STRUCTURED_CONTENT
        )
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=content,
            is_error=is_error,
            provider_correlation_id=request.provider_correlation_id,
            details=json_safe_details(details),
            structured_content=json_safe_value(structured_content),
            blocks=blocks,
        )


def _bounded_blocks(blocks: object, cap: int) -> tuple[ToolResultBlock, ...] | None:
    """A handler's ``blocks`` with their text bounded, or ``None`` when they
    are not a sequence of text and image blocks.

    The bound is ``cap`` or the ``tool_result`` hooks' bound, whichever is
    smaller: text the hooks would cut loses the images with it. Text within
    the bound is kept as it is. Otherwise images are kept whole and in
    order, the text block that reaches the bound is cut and marked, and
    later text blocks are left out.
    """

    if isinstance(blocks, (str, bytes)) or not isinstance(blocks, Sequence):
        return None
    checked: list[ToolResultBlock] = []
    for block in blocks:
        if type(block) is ImageContent:
            checked.append(block)
        elif type(block) is TextContent and block.signature is None:
            checked.append(block)
        else:
            return None
    limit = min(cap, TOOL_RESULT_MAX_CHARS)
    if len(tool_result_text(checked)) <= limit:
        return tuple(checked)
    # Room for the marker and its newline is kept, so the text stays within
    # the bound.
    budget = limit - len(_TRUNCATED) - 1
    kept: list[ToolResultBlock] = []
    used = 0  # length of the kept text blocks joined by newlines
    texts = 0
    cut = False
    for block in checked:
        if not isinstance(block, TextContent):
            kept.append(block)
            continue
        if cut:
            continue
        separator = 1 if texts else 0
        if used + separator + len(block.text) > budget:
            room = max(budget - used - separator, 0)
            block = TextContent(block.text[:room] + _TRUNCATED)
            cut = True
        kept.append(block)
        used += separator + len(block.text)
        texts += 1
    return tuple(kept)

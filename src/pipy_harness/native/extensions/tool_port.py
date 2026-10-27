"""Native tool-port adapter for activated extension tools."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from pipy_harness.native.agent.content import (
    NO_STRUCTURED_CONTENT,
    TextContent,
    ToolPartialResult,
    ToolResultBlock,
    tool_result_text,
)
from pipy_harness.native.agent.tool_details import (
    NO_TOOL_DETAILS,
)
from pipy_harness.native.extension_hooks import bounded_result_blocks
from pipy_harness.native.extension_loader import _drive_awaitable
from pipy_harness.native.extension_types import (
    ControlGetAllToolsFn,
    ExtensionModelRuntimeControl,
    ToolResult,
)
from pipy_harness.native.extensions.command_context import make_extension_context
from pipy_harness.native.tools.base import (
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolOutputSink,
    ToolRequest,
    dispatch_tool_output,
    forward_tool_details,
    json_safe_details,
    json_safe_value,
)

if TYPE_CHECKING:
    from pipy_harness.native.extension_types import RegisteredTool

# Bound an extension tool's provider-visible output.
_TOOL_OUTPUT_MAX_CHARS: int = 32 * 1024
_TRUNCATED = "\n[pipy: extension tool output truncated]"


def _exception_text(err: BaseException, limit: int) -> str:
    """What a raising extension callback reports, as Pi's agent loop does
    (``error.message``): the exception's message, or its type name when the
    message is empty or cannot be read. Characters that would steer a
    terminal become spaces (line breaks and tabs stay), and the text is
    bounded to ``limit`` characters."""

    try:
        message = str(err)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 - an unreadable message
        message = ""
    if not message.strip():
        message = type(err).__name__
    text = "".join(
        char if char.isprintable() or char in "\n\t" else " " for char in message
    )
    if len(text) > limit:
        text = text[: max(0, limit - 64)] + _TRUNCATED
    return text


def _partial_update(sink: ToolOutputSink) -> Callable[[ToolResult], None]:
    """``ctx.on_update`` of a running extension tool (Pi ``onUpdate``):
    each partial ``ToolResult`` becomes a partial result that replaces the
    previous one, its text bounded like the final result's."""

    def capture(partial: ToolResult) -> ToolPartialResult:
        if not isinstance(partial, ToolResult):
            raise TypeError("on_update expects a ToolResult")
        if partial.blocks:
            # Joining all caller-owned blocks before truncation can allocate
            # unbounded text. This prefix has identical truncation semantics.
            pieces: list[str] = []
            length = 0
            for block in partial.blocks:
                if not isinstance(block, TextContent):
                    continue
                prefix = ("\n" if pieces else "") + block.text[
                    : _TOOL_OUTPUT_MAX_CHARS + 1
                ]
                pieces.append(prefix[: _TOOL_OUTPUT_MAX_CHARS + 1 - length])
                length += len(pieces[-1])
                if length > _TOOL_OUTPUT_MAX_CHARS:
                    break
            text = "".join(pieces)
        else:
            text = (
                partial.content
                if isinstance(partial.content, str)
                else str(partial.content)
            )
        if len(text) > _TOOL_OUTPUT_MAX_CHARS:
            text = text[: _TOOL_OUTPUT_MAX_CHARS - 64] + _TRUNCATED
        return ToolPartialResult(text, forward_tool_details(sink, partial.details))

    def update(partial: ToolResult) -> None:
        dispatch_tool_output(sink, lambda: capture(partial))

    return update


class _ExtensionToolPort:
    """Adapt an extension `RegisteredTool` to the native `ToolPort`.

    The loop validates arguments against `definition.input_schema` before
    `invoke`, so the handler receives already-validated input. A handler
    exception becomes a tool error with the exception's message, printable
    and bounded (Pi's agent loop reports ``error.message``; never a session
    crash), and the provider-visible output is bounded. `KeyboardInterrupt` /
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
            prompt_snippet=tool.prompt_snippet,
            prompt_guidelines=tuple(tool.prompt_guidelines),
            execution_mode=tool.execution_mode,
            schema_mode="foreign",
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def prepare_arguments(self, arguments: object) -> object:
        """The tool's ``prepare_arguments`` (Pi ``prepareArguments``), which
        the executor runs on the parsed arguments before validation. A
        callback that raises makes the call an argument error whose text is
        the exception's message (Pi reports what ``prepareToolCall`` catches
        as the call's error result)."""

        prepare = self._registered.tool.prepare_arguments
        if prepare is None:
            return arguments
        try:
            return prepare(arguments)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as err:  # noqa: BLE001 - bound a bad callback
            raise ToolArgumentError(
                self._definition.name,
                _exception_text(err, ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH),
                qualified=False,
            ) from None

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
                tool_update=(
                    None
                    if context.output_sink is None
                    else _partial_update(context.output_sink)
                ),
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
            # Pi: throwing from `execute` fails the call with the message.
            return ToolExecutionResult(
                tool_request_id=request.tool_request_id,
                output_text=_exception_text(
                    err, ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH
                ),
                is_error=True,
                provider_correlation_id=request.provider_correlation_id,
            )
        cap = ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH
        blocks: tuple[ToolResultBlock, ...] = ()
        if isinstance(result, ToolResult) and result.blocks:
            bounded = bounded_result_blocks(result.blocks, cap, _TRUNCATED)
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
        details = result.details if isinstance(result, ToolResult) else NO_TOOL_DETAILS
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
            nested_call_evidence=result.nested_call_evidence
            if isinstance(result, ToolResult)
            else None,
            structured_content=json_safe_value(structured_content),
            blocks=blocks,
            # Pi ``terminate === true``: only an exact True asks.
            terminate=isinstance(result, ToolResult) and result.terminate is True,
        )

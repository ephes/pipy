"""Native tool-port adapter for activated extension tools."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from typing import TYPE_CHECKING, Protocol, TypeAlias

from pipy_harness.native.extension_types import (
    ExtensionModelRuntimeControl,
    ToolResult,
)
from pipy_harness.native.extensions.command_context import make_extension_context
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)

if TYPE_CHECKING:
    from pipy_harness.native.extension_runtime import RegisteredTool

ToolRenderDetails: TypeAlias = Mapping[str, object] | None
ToolRenderDetailsSink: TypeAlias = MutableMapping[str, object | None]


class ToolRenderDetailsWriter(Protocol):
    """Write-only side of the render-details handoff."""

    def __setitem__(
        self, correlation_id: str, details: ToolRenderDetails, /
    ) -> None: ...


# Bound an extension tool's provider-visible output.
_TOOL_OUTPUT_MAX_CHARS: int = 32 * 1024


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
        flags: Mapping[str, object] | None = None,
        render_details_sink: ToolRenderDetailsWriter | None = None,
        project_trusted: bool = False,
    ) -> None:
        self._registered = registered
        self._has_ui = has_ui
        self._notify_sink = notify_sink
        self._set_active_tools_fn = set_active_tools_fn
        self._flags = dict(flags or {})
        self._render_details_sink = render_details_sink
        self._project_trusted = bool(project_trusted)
        tool = registered.tool
        self._definition = ToolDefinition(
            name=tool.name,
            description=str(tool.description),
            input_schema=dict(tool.input_schema),
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
                set_active_tools_fn=set_active_tools
            ),
            flags=self._flags,
            project_trusted=self._project_trusted,
        )
        try:
            result = self._registered.tool.handler(ctx, dict(request.arguments))
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as err:  # noqa: BLE001 - bound a bad tool
            return ToolExecutionResult(
                tool_request_id=request.tool_request_id,
                output_text=f"extension tool error: {type(err).__name__}",
                is_error=True,
                provider_correlation_id=request.provider_correlation_id,
            )
        if isinstance(result, ToolResult) and isinstance(result.content, str):
            content = result.content
        elif isinstance(result, ToolResult):
            content = str(result.content)
        else:
            content = str(result)
        cap = ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH
        if len(content) > cap:
            content = content[: cap - 64] + "\n[pipy: extension tool output truncated]"
        if (
            self._render_details_sink is not None
            and self._registered.tool.render_result is not None
            and request.provider_correlation_id is not None
        ):
            details = result.details if isinstance(result, ToolResult) else None
            self._render_details_sink[request.provider_correlation_id] = (
                dict(details) if isinstance(details, Mapping) else None
            )
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=content,
            is_error=False,
            provider_correlation_id=request.provider_correlation_id,
        )

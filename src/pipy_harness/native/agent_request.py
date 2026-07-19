"""Product adapter for extension transformations of provider requests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pipy_harness.native.agent.messages import AgentMessage
from pipy_harness.native.agent.request import (
    AgentProviderRequestSnapshot,
    snapshot_provider_request,
)
from pipy_harness.native.models import ProviderHeaderCallback, ProviderRequest
from pipy_harness.native.tools.base import ToolDefinition

if TYPE_CHECKING:
    from pipy_harness.native.extension_runtime import (
        ControlSetActiveToolsFn,
        ControlSetModelFn,
        ControlSetThinkingLevelFn,
        ExtensionUiDriver,
        HookHandler,
    )
    from pipy_harness.native.image_attachment import ProviderImageAttachment


@dataclass(slots=True)
class NativeProviderRequestInput:
    """Product-owned inputs used to compose one provider request snapshot."""

    system_prompt: str
    user_prompt: str
    provider_name: str
    model_id: str
    cwd: Path
    messages: tuple[AgentMessage, ...]
    available_tools: tuple[ToolDefinition, ...]
    attachments: tuple[ProviderImageAttachment, ...]
    provider_header_callback: ProviderHeaderCallback | None


@dataclass(slots=True)
class NativeProviderRequestHookContext:
    """Product capabilities exposed while request hooks are dispatched."""

    cwd: str
    has_ui: bool
    notify_sink: Callable[[str, str], None] | None
    ui_driver: ExtensionUiDriver | None
    set_active_tools_fn: ControlSetActiveToolsFn | None
    set_model_fn: ControlSetModelFn | None
    set_thinking_level_fn: ControlSetThinkingLevelFn | None
    flags: Mapping[str, object]
    project_trusted: bool


class _MonotonicToolHookPolicy:
    """Narrow hook tool transforms against one ordered request-local ceiling."""

    def __init__(self, available_tools: tuple[ToolDefinition, ...]) -> None:
        self._ceiling = tuple(dict.fromkeys(tool.name for tool in available_tools))

    def guard(self, hook: HookHandler) -> HookHandler:
        def _guarded(event: object, extension_context: object) -> object:
            result = hook(event, extension_context)
            if inspect.isawaitable(result):
                return self._after_await(cast(Awaitable[object], result))
            return self._narrow(result)

        return _guarded

    async def _after_await(self, result: Awaitable[object]) -> object:
        return self._narrow(await result)

    def _narrow(self, result: object) -> object:
        from pipy_harness.native.extension_runtime import ProviderRequestTransform

        if (
            not isinstance(result, ProviderRequestTransform)
            or result.available_tools is None
        ):
            return result
        requested = {
            name for name in result.available_tools if isinstance(name, str) and name
        }
        self._ceiling = tuple(name for name in self._ceiling if name in requested)
        return replace(result, available_tools=self._ceiling)


def prepare_provider_request(
    request_input: NativeProviderRequestInput,
    hooks: Sequence[HookHandler],
    context: NativeProviderRequestHookContext,
) -> AgentProviderRequestSnapshot:
    """Dispatch hooks serially while permitting only monotonic tool narrowing."""

    from pipy_harness.native.extension_runtime import (
        dispatch_before_provider_request_hooks,
    )

    request = ProviderRequest(
        system_prompt=request_input.system_prompt,
        user_prompt=request_input.user_prompt,
        provider_name=request_input.provider_name,
        model_id=request_input.model_id,
        cwd=request_input.cwd,
        messages=request_input.messages,
        available_tools=request_input.available_tools,
        attachments=request_input.attachments,
        provider_header_callback=request_input.provider_header_callback,
    )
    policy = _MonotonicToolHookPolicy(request.available_tools)
    transform = dispatch_before_provider_request_hooks(
        tuple(policy.guard(hook) for hook in hooks),
        request,
        cwd=context.cwd,
        has_ui=context.has_ui,
        notify_sink=context.notify_sink,
        ui_driver=context.ui_driver,
        set_active_tools_fn=context.set_active_tools_fn,
        set_model_fn=context.set_model_fn,
        set_thinking_level_fn=context.set_thinking_level_fn,
        flags=context.flags,
        project_trusted=context.project_trusted,
    )
    return snapshot_provider_request(
        request,
        system_prompt=transform.system_prompt,
        user_prompt=transform.user_prompt,
        available_tool_names=transform.available_tools,
    )

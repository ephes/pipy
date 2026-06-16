"""Public Pipy extension API surface.

This is the stable import path for Python extensions (the path the
extension examples in `docs/extension-api.md` import from). It re-exports
the activation-time API and the discovery/activation value objects from
the pipy-owned native runtime, so extension authors depend on
`pipy_harness.extensions` rather than internal module layout.

    from pipy_harness.extensions import PipyExtensionAPI

    def activate(api: PipyExtensionAPI) -> None:
        api.register_command("hello", "Print a greeting", _hello)

The surface grows by slice: slice 2 supports `register_command` only.
Tool / hook / provider / UI registration land in later slices.
"""

from __future__ import annotations

from pipy_harness.native.extension_runtime import (
    ActivatedExtension,
    CommandContext,
    ExtensionCommandDispatch,
    ExtensionUi,
    PipyExtensionAPI,
    BeforeAgentStartEvent,
    BeforeAgentStartResult,
    ExtensionTool,
    InputEvent,
    InputTransform,
    LifecycleEvent,
    QueuedUserMessage,
    RegisteredCommand,
    RegisteredTool,
    ToolBlock,
    ToolCallEvent,
    ToolResult,
    ToolResultEvent,
    ToolResultTransform,
    activate_extensions,
    dispatch_before_agent_start_hooks,
    dispatch_extension_command,
    dispatch_input_hooks,
    dispatch_lifecycle_hooks,
    dispatch_tool_call_hooks,
    dispatch_tool_result_hooks,
    drain_user_messages,
    extension_command_map,
    extension_event_hooks,
    extension_tool_call_hooks,
    extension_tools,
    safe_activation_metadata,
)
from pipy_harness.native.extensions import (
    ExtensionDescriptor,
    discover_extensions,
    safe_extension_metadata,
)

__all__ = [
    "PipyExtensionAPI",
    "CommandContext",
    "ExtensionUi",
    "RegisteredCommand",
    "ActivatedExtension",
    "ExtensionCommandDispatch",
    "ToolBlock",
    "ToolCallEvent",
    "LifecycleEvent",
    "InputEvent",
    "InputTransform",
    "BeforeAgentStartEvent",
    "BeforeAgentStartResult",
    "QueuedUserMessage",
    "ExtensionTool",
    "ToolResult",
    "RegisteredTool",
    "ToolResultEvent",
    "ToolResultTransform",
    "dispatch_tool_result_hooks",
    "extension_tools",
    "activate_extensions",
    "dispatch_extension_command",
    "extension_command_map",
    "extension_tool_call_hooks",
    "extension_event_hooks",
    "dispatch_tool_call_hooks",
    "dispatch_lifecycle_hooks",
    "dispatch_input_hooks",
    "dispatch_before_agent_start_hooks",
    "drain_user_messages",
    "safe_activation_metadata",
    "ExtensionDescriptor",
    "discover_extensions",
    "safe_extension_metadata",
]

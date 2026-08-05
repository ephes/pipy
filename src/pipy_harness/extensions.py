"""Public Pipy extension API surface.

This is the stable import path for Python extensions (the path the
extension examples in `docs/extension-api.md` import from). It re-exports
the activation-time API and the discovery/activation value objects from
the pipy-owned native runtime, so extension authors depend on
`pipy_harness.extensions` rather than internal module layout.

    from pipy_harness.extensions import PipyExtensionAPI

    def activate(api: PipyExtensionAPI) -> None:
        api.register_command("hello", "Print a greeting", _hello)

The activation API supports command and keyboard-shortcut registration
(`register_command`, `register_shortcut`), event hooks (`on`), tool and
provider registration (`register_tool`, `register_provider` /
`unregister_provider`), and `send_user_message`. Command/shortcut handlers
receive a mode-aware context exposing the workspace root, `ui` (`notify` and
`custom` interactive overlays, simple dialogs, and a multi-line `editor`), a
read-only `conversation` view
(`last_assistant_message`), and a bounded `complete(system_prompt, user_text)`
one-shot completion.
"""

from __future__ import annotations

from pipy_harness.native.extension_hooks import (
    dispatch_before_agent_start_hooks,
    dispatch_before_provider_headers_hooks,
    dispatch_before_provider_request_hooks,
    dispatch_input_hooks,
    dispatch_lifecycle_hooks,
    dispatch_session_before_hooks,
    dispatch_tool_call_hooks,
    dispatch_tool_result_hooks,
    dispatch_user_bash_hooks,
    extension_event_hooks,
    extension_tool_call_hooks,
)
from pipy_harness.native.extension_runtime import (
    ActivatedExtension,
    ExtensionCommandDispatch,
    PipyExtensionAPI,
    RegisteredCommand,
    RegisteredEntryRenderer,
    RegisteredMessageRenderer,
    RegisteredShortcut,
    activate_extensions,
    dispatch_extension_command,
    dispatch_extension_shortcut,
    drain_custom_messages,
    drain_user_messages,
    extension_command_map,
    extension_entry_renderers,
    extension_flags,
    extension_message_renderers,
    extension_oauth_providers,
    extension_providers,
    extension_shortcuts,
    extension_tools,
    extension_unregistered_providers,
    safe_activation_metadata,
)
from pipy_harness.native.extension_types import (
    BeforeAgentStartEvent,
    BeforeAgentStartResult,
    BeforeProviderHeadersEvent,
    BeforeProviderRequestEvent,
    ChromeComponent,
    CompletionFn,
    CustomComponent,
    CustomComponentFactory,
    EntryRenderContext,
    ExtensionFlag,
    ExtensionOAuthConfig,
    ExtensionProvider,
    ExtensionTool,
    ExtensionUi,
    ExtensionUiDriver,
    FooterData,
    InputEvent,
    InputTransform,
    LifecycleEvent,
    MessageRenderComponent,
    MessageRenderContext,
    ProviderContext,
    ProviderRequestTransform,
    QueuedCustomMessage,
    QueuedUserMessage,
    RegisteredFlag,
    RegisteredProvider,
    RegisteredTool,
    RenderedCustomEntry,
    SessionBeforeEvent,
    SessionDecision,
    ThemeColor,
    ToolBlock,
    ToolCallEvent,
    ToolRenderComponent,
    ToolRenderContext,
    ToolRenderTheme,
    ToolResult,
    ToolResultEvent,
    ToolResultTransform,
    UserBashDecision,
    UserBashDispatch,
    UserBashEvent,
    WidgetPlacement,
    normalize_shortcut_key,
)
from pipy_harness.native.extension_ui import (
    coerce_tool_render_lines,
    lines_component,
)
from pipy_harness.native.extensions.command_context import (
    CommandContext,
    ExtensionCapabilityError,
)
from pipy_harness.native.extensions.custom_payloads import (
    coerce_custom_message,
    render_extension_entry,
    render_extension_message,
    safe_custom_entry_data,
)
from pipy_harness.native.extensions.packages import (
    ExtensionDescriptor,
    discover_extensions,
    safe_extension_metadata,
)
from pipy_harness.native.extensions.session_views import (
    AssistantMessageView,
    ConversationView,
    SessionEntryView,
    SessionHeaderView,
    SessionManagerView,
    SessionTreeNodeView,
)
from pipy_harness.native.provider import apply_provider_headers
from pipy_harness.native.provider_construction import build_extension_provider_port

__all__ = [
    "PipyExtensionAPI",
    "CommandContext",
    "ConversationView",
    "AssistantMessageView",
    "SessionEntryView",
    "SessionHeaderView",
    "SessionManagerView",
    "SessionTreeNodeView",
    "CompletionFn",
    "CustomComponent",
    "CustomComponentFactory",
    "ExtensionCapabilityError",
    "ExtensionUi",
    "ExtensionUiDriver",
    "ChromeComponent",
    "FooterData",
    "WidgetPlacement",
    "MessageRenderComponent",
    "MessageRenderContext",
    "EntryRenderContext",
    "RenderedCustomEntry",
    "RegisteredCommand",
    "RegisteredMessageRenderer",
    "RegisteredEntryRenderer",
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
    "QueuedCustomMessage",
    "ExtensionTool",
    "ExtensionProvider",
    "ExtensionOAuthConfig",
    "ExtensionFlag",
    "ProviderContext",
    "RegisteredProvider",
    "RegisteredFlag",
    "ToolResult",
    "RegisteredTool",
    "ToolResultEvent",
    "ToolResultTransform",
    "BeforeProviderRequestEvent",
    "BeforeProviderHeadersEvent",
    "ProviderRequestTransform",
    "SessionBeforeEvent",
    "SessionDecision",
    "UserBashDecision",
    "UserBashDispatch",
    "UserBashEvent",
    "RegisteredShortcut",
    "dispatch_extension_shortcut",
    "extension_shortcuts",
    "extension_message_renderers",
    "extension_entry_renderers",
    "normalize_shortcut_key",
    "ThemeColor",
    "ToolRenderComponent",
    "ToolRenderContext",
    "ToolRenderTheme",
    "coerce_tool_render_lines",
    "lines_component",
    "dispatch_tool_result_hooks",
    "dispatch_user_bash_hooks",
    "dispatch_before_provider_request_hooks",
    "dispatch_before_provider_headers_hooks",
    "dispatch_session_before_hooks",
    "extension_tools",
    "extension_providers",
    "extension_oauth_providers",
    "extension_flags",
    "extension_unregistered_providers",
    "render_extension_message",
    "render_extension_entry",
    "coerce_custom_message",
    "safe_custom_entry_data",
    "build_extension_provider_port",
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
    "drain_custom_messages",
    "safe_activation_metadata",
    "ExtensionDescriptor",
    "discover_extensions",
    "safe_extension_metadata",
    "apply_provider_headers",
]

"""Ownership characterization for the headless extension UI bridge.

Slice 6.4c relocated `_CollectingUi`, `_safe_ui_key`, and the
`coerce_tool_render_lines` / `_LinesComponent` / `lines_component` chrome
helpers into `pipy_harness.native.extension_ui`. Slice 18 relocated custom
payload coercion and rendering into `native.extensions.custom_payloads`; its
old `extension_runtime` path is absent while the public extension API imports
those objects from their authoritative owner.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pipy_harness.extensions as public_extensions
import pipy_harness.native.extension_hooks as extension_hooks
import pipy_harness.native.extension_runtime as extension_runtime
import pipy_harness.native.extension_types as extension_types
import pipy_harness.native.extension_ui as extension_ui
import pipy_harness.native.extensions.command_context as extension_command_context
import pipy_harness.native.extensions.custom_payloads as extension_custom_payloads
import pipy_harness.native.extensions.packages as extension_discovery
import pipy_harness.native.extensions.session_views as extension_session_views
import pipy_harness.native.provider as provider
import pipy_harness.native.provider_construction as provider_construction

_PUBLIC_EXTENSION_NAMES = (
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
)

_OWNER_GROUPS = (
    (
        extension_runtime,
        (
            "ActivatedExtension",
            "ExtensionCommandDispatch",
            "PipyExtensionAPI",
            "RegisteredCommand",
            "RegisteredEntryRenderer",
            "RegisteredMessageRenderer",
            "RegisteredShortcut",
            "activate_extensions",
            "dispatch_extension_shortcut",
            "dispatch_extension_command",
            "drain_custom_messages",
            "drain_user_messages",
            "extension_command_map",
            "extension_entry_renderers",
            "extension_flags",
            "extension_message_renderers",
            "extension_oauth_providers",
            "extension_providers",
            "extension_shortcuts",
            "extension_tools",
            "extension_unregistered_providers",
            "safe_activation_metadata",
        ),
    ),
    (
        extension_custom_payloads,
        (
            "coerce_custom_message",
            "render_extension_entry",
            "render_extension_message",
            "safe_custom_entry_data",
        ),
    ),
    (
        extension_types,
        (
            "BeforeAgentStartEvent",
            "BeforeAgentStartResult",
            "BeforeProviderHeadersEvent",
            "BeforeProviderRequestEvent",
            "ChromeComponent",
            "CompletionFn",
            "CustomComponent",
            "CustomComponentFactory",
            "EntryRenderContext",
            "ExtensionFlag",
            "ExtensionOAuthConfig",
            "ExtensionProvider",
            "ExtensionTool",
            "ExtensionUi",
            "ExtensionUiDriver",
            "FooterData",
            "InputEvent",
            "InputTransform",
            "LifecycleEvent",
            "MessageRenderComponent",
            "MessageRenderContext",
            "ProviderContext",
            "ProviderRequestTransform",
            "QueuedCustomMessage",
            "QueuedUserMessage",
            "RegisteredFlag",
            "RegisteredProvider",
            "RegisteredTool",
            "RenderedCustomEntry",
            "SessionBeforeEvent",
            "SessionDecision",
            "ThemeColor",
            "ToolBlock",
            "ToolCallEvent",
            "ToolRenderComponent",
            "ToolRenderContext",
            "ToolRenderTheme",
            "ToolResult",
            "ToolResultEvent",
            "ToolResultTransform",
            "UserBashDecision",
            "UserBashDispatch",
            "UserBashEvent",
            "WidgetPlacement",
            "normalize_shortcut_key",
        ),
    ),
    (extension_ui, ("coerce_tool_render_lines", "lines_component")),
    (
        extension_command_context,
        ("CommandContext", "ExtensionCapabilityError"),
    ),
    (
        extension_session_views,
        (
            "AssistantMessageView",
            "ConversationView",
            "SessionEntryView",
            "SessionHeaderView",
            "SessionManagerView",
            "SessionTreeNodeView",
        ),
    ),
    (
        extension_hooks,
        (
            "dispatch_before_agent_start_hooks",
            "dispatch_before_provider_headers_hooks",
            "dispatch_before_provider_request_hooks",
            "dispatch_input_hooks",
            "dispatch_lifecycle_hooks",
            "dispatch_session_before_hooks",
            "dispatch_tool_call_hooks",
            "dispatch_tool_result_hooks",
            "dispatch_user_bash_hooks",
            "extension_event_hooks",
            "extension_tool_call_hooks",
        ),
    ),
    (
        extension_discovery,
        ("ExtensionDescriptor", "discover_extensions", "safe_extension_metadata"),
    ),
    (provider_construction, ("build_extension_provider_port",)),
    (provider, ("apply_provider_headers",)),
)

_MOVED_CUSTOM_PAYLOAD_NAMES = (
    "coerce_custom_message",
    "safe_custom_entry_data",
    "_custom_message_renderer_payload",
    "_custom_entry_renderer_payload",
    "_CustomEntryRedrawRow",
    "_custom_entry_redraw_rows",
    "_renderer_wants_context",
    "_plain_message_render",
    "_invoke_message_renderer",
    "_coerce_message_component",
    "render_extension_message",
    "_close_unsupported_awaitable",
    "_coerce_entry_component",
    "render_extension_entry",
    "_copy_custom_entry_data",
    "_coerce_rendered_lines",
    "_bounded_render_text",
)


def test_custom_payload_cluster_has_one_authoritative_owner() -> None:
    for name in _MOVED_CUSTOM_PAYLOAD_NAMES:
        assert name in vars(extension_custom_payloads)
        assert not hasattr(extension_runtime, name)

    functions = _MOVED_CUSTOM_PAYLOAD_NAMES[:4] + _MOVED_CUSTOM_PAYLOAD_NAMES[5:]
    for name in functions:
        assert getattr(extension_custom_payloads, name).__module__ == (
            extension_custom_payloads.__name__
        )

    owner_path = Path(extension_custom_payloads.__file__ or "")
    tree = ast.parse(owner_path.read_text(encoding="utf-8"))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    definitions.update(
        node.target.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    )
    assert set(_MOVED_CUSTOM_PAYLOAD_NAMES) <= definitions


def test_public_extension_api_inventory_and_owner_identity() -> None:
    assert tuple(public_extensions.__all__) == _PUBLIC_EXTENSION_NAMES
    assert len(_PUBLIC_EXTENSION_NAMES) == 97

    owned_names = tuple(name for _owner, names in _OWNER_GROUPS for name in names)
    assert len(owned_names) == len(set(owned_names)) == 97
    assert set(owned_names) == set(_PUBLIC_EXTENSION_NAMES)
    for owner, names in _OWNER_GROUPS:
        for name in names:
            assert getattr(public_extensions, name) is getattr(owner, name)


def test_public_extension_api_imports_every_name_from_its_owner() -> None:
    expected_owners = {
        name: owner.__name__ for owner, names in _OWNER_GROUPS for name in names
    }
    facade_path = Path(__file__).parents[1] / "src" / "pipy_harness" / "extensions.py"
    tree = ast.parse(facade_path.read_text(encoding="utf-8"))
    direct_imports = [
        (alias.asname or alias.name, node.module)
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
        for alias in node.names
        if (alias.asname or alias.name) in expected_owners
    ]

    assert len(direct_imports) == len(dict(direct_imports)) == 97
    assert dict(direct_imports) == expected_owners


def test_collecting_ui_owned_by_extension_ui() -> None:
    from pipy_harness.native.extension_runtime import _CollectingUi

    assert _CollectingUi is extension_ui._CollectingUi


def test_render_helpers_reexport_same_objects() -> None:
    from pipy_harness.extensions import coerce_tool_render_lines, lines_component
    from pipy_harness.native.extension_runtime import (
        coerce_tool_render_lines as rt_coerce,
    )
    from pipy_harness.native.extension_runtime import lines_component as rt_lines

    assert coerce_tool_render_lines is extension_ui.coerce_tool_render_lines
    assert rt_coerce is extension_ui.coerce_tool_render_lines
    assert lines_component is extension_ui.lines_component
    assert rt_lines is extension_ui.lines_component


def test_runtime_compatibility_type_exports_keep_owner_identity() -> None:
    compatibility_names = (
        "BeforeAgentStartResult",
        "CustomComponent",
        "ExtensionCodingSessionControl",
        "ExtensionModelRuntimeControl",
        "ExtensionTool",
        "ExtensionUi",
        "ExtensionUiDriver",
        "FooterData",
        "InputTransform",
        "LifecycleEvent",
        "ProviderRequestTransform",
        "QueuedCustomMessage",
        "QueuedUserMessage",
        "RegisteredTool",
        "RenderedCustomEntry",
        "SessionDecision",
        "ThemeColor",
        "ToolBlock",
        "ToolRenderContext",
        "ToolRenderTheme",
        "ToolResultTransform",
        "UserBashDecision",
        "is_valid_custom_entry_type",
        "normalize_shortcut_key",
    )

    for name in compatibility_names:
        assert getattr(extension_runtime, name) is getattr(extension_types, name)

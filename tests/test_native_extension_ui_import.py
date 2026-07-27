"""Ownership characterization for the headless extension UI bridge.

Slice 6.4c relocated `_CollectingUi`, `_safe_ui_key`, and the
`coerce_tool_render_lines` / `_LinesComponent` / `lines_component` chrome
helpers into `pipy_harness.native.extension_ui`. `extension_runtime` and the
public `pipy_harness.extensions` surface re-export the same objects, and the
new module never reaches the concrete product session or terminal UI.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pipy_harness.extensions as public_extensions
import pipy_harness.native.extension_hooks as extension_hooks
import pipy_harness.native.extension_runtime as extension_runtime
import pipy_harness.native.extension_types as extension_types
import pipy_harness.native.extension_ui as extension_ui
import pipy_harness.native.extensions as extension_discovery
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
            "AssistantMessageView",
            "CommandContext",
            "ConversationView",
            "ExtensionCapabilityError",
            "ExtensionCommandDispatch",
            "PipyExtensionAPI",
            "RegisteredCommand",
            "RegisteredEntryRenderer",
            "RegisteredMessageRenderer",
            "RegisteredShortcut",
            "SessionEntryView",
            "SessionHeaderView",
            "SessionManagerView",
            "SessionTreeNodeView",
            "activate_extensions",
            "coerce_custom_message",
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
            "render_extension_entry",
            "render_extension_message",
            "safe_activation_metadata",
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

_STRICT_EXTENSION_MODULES = {
    "pipy_harness.extensions",
    "pipy_harness.native.extension_hooks",
    "pipy_harness.native.extension_loader",
    "pipy_harness.native.extension_runtime",
    "pipy_harness.native.extension_types",
    "pipy_harness.native.extension_ui",
    "pipy_harness.native.extensions",
}

_STRICT_SLICE_8A_MODULES = (
    "pipy_harness.native.settings",
    "pipy_harness.native.package_manager",
    "pipy_harness.native.session_tree_commands",
)

_STRICT_SLICE_8B_MODULES = (
    "pipy_harness.native.package_resources",
    "pipy_harness.native.package_runtime",
    "pipy_harness.native.resources",
)

_STRICT_SLICE_8C_MODULES = (
    "pipy_harness.native.repl_input",
    "pipy_harness.native.autocomplete_provider",
    "pipy_harness.native.tool_renderers",
)

_STRICT_SLICE_8D_MODULES = (
    "pipy_harness.native.routing",
    "pipy_harness.native.oauth_providers",
    "pipy_harness.native.models_json",
)

_STRICT_OVERRIDE_MODULES = (
    "pipy_harness.cli",
    "pipy_harness.extensions",
    "pipy_harness.native.ui.*",
    "pipy_harness.native.agent.*",
    "pipy_harness.native.coding.*",
    "pipy_harness.native.automation.*",
    "pipy_harness.native.providers.*",
    "pipy_harness.native.extension_hooks",
    "pipy_harness.native.extension_loader",
    "pipy_harness.native.extension_runtime",
    "pipy_harness.native.extension_types",
    "pipy_harness.native.extension_ui",
    "pipy_harness.native.extensions",
    "pipy_harness.native.http",
    "pipy_harness.native.repl_state",
    "pipy_harness.native.session",
    "pipy_harness.native.tool_loop_session",
    "pipy_harness.native.tui",
    *_STRICT_SLICE_8A_MODULES,
    *_STRICT_SLICE_8B_MODULES,
    *_STRICT_SLICE_8C_MODULES,
    *_STRICT_SLICE_8D_MODULES,
)

_STRICT_OVERRIDE_FLAGS = {
    "check_untyped_defs",
    "disallow_any_generics",
    "disallow_incomplete_defs",
    "disallow_subclassing_any",
    "disallow_untyped_calls",
    "disallow_untyped_decorators",
    "disallow_untyped_defs",
    "extra_checks",
    "no_implicit_reexport",
    "strict_equality",
    "warn_return_any",
    "warn_unused_ignores",
}


def test_public_extension_api_inventory_and_owner_identity() -> None:
    assert tuple(public_extensions.__all__) == _PUBLIC_EXTENSION_NAMES
    assert len(_PUBLIC_EXTENSION_NAMES) == 97

    owned_names = tuple(
        name for _owner, names in _OWNER_GROUPS for name in names
    )
    assert len(owned_names) == len(set(owned_names)) == 97
    assert set(owned_names) == set(_PUBLIC_EXTENSION_NAMES)
    for owner, names in _OWNER_GROUPS:
        for name in names:
            assert getattr(public_extensions, name) is getattr(owner, name)


def test_public_extension_api_imports_every_name_from_its_owner() -> None:
    expected_owners = {
        name: owner.__name__ for owner, names in _OWNER_GROUPS for name in names
    }
    facade_path = (
        Path(__file__).parents[1] / "src" / "pipy_harness" / "extensions.py"
    )
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


def test_strict_frontier_has_exact_extension_and_slice_8_support_surfaces() -> None:
    config_path = Path(__file__).parents[1] / "pyproject.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    mypy_config = config["tool"]["mypy"]
    overrides = mypy_config["overrides"]

    assert len(overrides) == 1
    strict_override = overrides[0]
    extension_modules = {
        module
        for module in strict_override["module"]
        if module == "pipy_harness.extensions"
        or module.startswith("pipy_harness.native.extension")
    }

    assert tuple(strict_override["module"]) == _STRICT_OVERRIDE_MODULES
    assert extension_modules == _STRICT_EXTENSION_MODULES
    assert tuple(
        module
        for module in strict_override["module"]
        if module in _STRICT_SLICE_8A_MODULES
    ) == _STRICT_SLICE_8A_MODULES
    assert tuple(
        module
        for module in strict_override["module"]
        if module in _STRICT_SLICE_8B_MODULES
    ) == _STRICT_SLICE_8B_MODULES
    assert tuple(
        module
        for module in strict_override["module"]
        if module in _STRICT_SLICE_8C_MODULES
    ) == _STRICT_SLICE_8C_MODULES
    assert tuple(
        module
        for module in strict_override["module"]
        if module in _STRICT_SLICE_8D_MODULES
    ) == _STRICT_SLICE_8D_MODULES
    assert len(strict_override["module"]) == 30
    assert "pipy_harness.native.*" not in strict_override["module"]
    assert set(strict_override) == {"module", *_STRICT_OVERRIDE_FLAGS}
    assert all(strict_override[name] is True for name in _STRICT_OVERRIDE_FLAGS)
    assert set(mypy_config) == {
        "warn_unused_configs",
        "warn_redundant_casts",
        "strict_bytes",
        "overrides",
    }
    assert mypy_config["warn_unused_configs"] is True
    assert mypy_config["warn_redundant_casts"] is True
    assert mypy_config["strict_bytes"] is True
    assert "strict" not in mypy_config
    assert "exclude" not in mypy_config


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

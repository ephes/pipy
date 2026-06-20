"""Runtime catalog contributions from extension-registered providers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pipy_harness.native.extension_runtime import (
    RegisteredProvider,
    activate_extensions,
    extension_providers,
    extension_unregistered_providers,
)
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.package_resources import PackageRoot
from pipy_harness.native.resource_enablement import is_resource_enabled


def extension_reserved_command_names(
    custom_command_slash_names: Sequence[str] = (),
) -> tuple[str, ...]:
    """Return slash-command names extensions may not register.

    This is intentionally a union of the REPL built-in command
    vocabularies plus resource/custom commands. Provider-only catalog activation
    uses the same reserved set as full extension activation, so an extension is
    not listable as a provider when its activation would later be disabled by a
    command collision.
    """

    from pipy_harness.native.repl_input import DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS
    from pipy_harness.native.tui import TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS

    names: list[str] = []
    for slash_name in (
        *DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS,
        *TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS,
        "/skill",
        "/template",
        *custom_command_slash_names,
    ):
        normalized = slash_name.lstrip("/")
        if normalized and normalized not in names:
            names.append(normalized)
    return tuple(names)


def extension_reserved_tool_names(
    extra_tool_names: Sequence[str] = (),
) -> tuple[str, ...]:
    """Return model-visible tool names extensions may not register."""

    names = [
        "read",
        "ls",
        "grep",
        "find",
        "write",
        "edit",
        "edit_diff",
        "truncate",
        "bash",
    ]
    for name in extra_tool_names:
        if name and name not in names:
            names.append(name)
    return tuple(names)


def load_extension_provider_contributions(
    cwd: Path,
    *,
    package_roots: Sequence[PackageRoot] = (),
    extension_patterns: Sequence[str] = (),
    explicit_extension_paths: Sequence[Path] = (),
    include_default_extensions: bool = True,
    reserved_command_names: Sequence[str] = (),
    reserved_tool_names: Sequence[str] = (),
) -> tuple[tuple[RegisteredProvider, ...], tuple[str, ...]]:
    """Activate extensions and return only provider catalog contributions.

    This helper intentionally returns safe runtime metadata only: registered
    provider objects and unregister names. It does not persist package/catalog
    state, and callers must not archive extension source paths or factories.
    """

    descriptors = discover_extensions(
        cwd,
        package_roots=tuple(package_roots),
        explicit_paths=tuple(explicit_extension_paths),
        include_defaults=include_default_extensions,
    )
    if extension_patterns:
        descriptors = [
            descriptor
            for descriptor in descriptors
            if descriptor.source_kind == "cli"
            or is_resource_enabled(descriptor.name, list(extension_patterns))
        ]
    activated = activate_extensions(
        descriptors,
        reserved_command_names=reserved_command_names,
        reserved_tool_names=reserved_tool_names,
    )
    return extension_providers(activated), extension_unregistered_providers(activated)

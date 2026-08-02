"""Runtime catalog contributions from extension-registered providers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from pipy_harness.native.extension_runtime import (
    _dispose_activation_results,
    _finalize_provider_catalog_results,
    _report_activation_cleanup,
    _report_provider_catalog_finalization,
    activate_extensions,
    extension_providers,
    extension_unregistered_providers,
)
from pipy_harness.native.extension_types import RegisteredProvider
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.package_resources import PackageRoot
from pipy_harness.native.resource_enablement import is_resource_enabled


def extension_reserved_command_names(
    custom_command_slash_names: Sequence[str] = (),
) -> tuple[str, ...]:
    """Return slash-command names extensions may not register.

    The built-in half is the shared :data:`RESERVED_COMMAND_NAMES` set (every
    declarative-registry built-in name and alias unioned with the ``skill`` and
    ``theme`` resource adjuncts), so an extension can never register a command
    named after any built-in the kernel can classify -- ``reload``, ``tree``,
    ``new``, ``fork``, ``session``, ``compact``, ``export`` and the rest -- not
    only the subset advertised in the completion menus. Discovered custom-command
    slash names are unioned in so an extension cannot shadow a workspace command
    either. Provider-only catalog activation uses the same reserved set as full
    extension activation, so an extension is not listable as a provider when its
    activation would later be disabled by a command collision.
    """

    from pipy_harness.native.resources import RESERVED_COMMAND_NAMES

    names: list[str] = sorted(RESERVED_COMMAND_NAMES)
    seen = set(names)
    for slash_name in custom_command_slash_names:
        normalized = slash_name.lstrip("/")
        if normalized and normalized not in seen:
            seen.add(normalized)
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
    include_workspace_defaults: bool = False,
    reserved_command_names: Sequence[str] = (),
    reserved_tool_names: Sequence[str] = (),
    diagnostic: Callable[[str], None],
) -> tuple[tuple[RegisteredProvider, ...], tuple[str, ...]]:
    """Activate extensions and return only provider catalog contributions.

    This helper intentionally returns safe runtime metadata only: registered
    provider objects and unregister names. It does not persist package/catalog
    state, and callers must not archive extension source paths or factories.
    After those immutable outputs detach, every accepted host enters a terminal
    catalog state that retains only guarded registration-time default flag reads
    for provider factories. This helper does not parse or apply extension CLI
    tokens; ``diagnostic`` receives any bounded finalization anomaly. Workspace
    extension discovery is fail-closed by default; product callers opt in only
    after resolving project trust.
    """

    descriptors = discover_extensions(
        cwd,
        package_roots=tuple(package_roots),
        explicit_paths=tuple(explicit_extension_paths),
        include_defaults=include_default_extensions,
        include_workspace_defaults=include_workspace_defaults,
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
        diagnostic=diagnostic,
    )
    try:
        providers = extension_providers(activated)
        unregistered = extension_unregistered_providers(activated)
    except BaseException:
        _report_activation_cleanup(_dispose_activation_results(activated), diagnostic)
        raise
    # Provider factories commonly close over ``api.get_flag``. Only after both
    # accepted outputs detach, finalize without registries, messages, or sends.
    _report_provider_catalog_finalization(
        _finalize_provider_catalog_results(activated),
        diagnostic,
    )
    return providers, unregistered

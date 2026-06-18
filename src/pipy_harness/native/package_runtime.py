"""Compose installed local-path packages into a session.

A single entry point that turns the configured package sources (recorded
in the settings system by the package-manager CLI) into per-kind resource
roots and installs the package theme registry. Callers thread the
returned roots into `WorkspaceResources.discover` (skills/prompts) and
`discover_extensions` (extension entry points) so package resources flow
through discovery at lowest precedence; the installed theme registry makes
package themes selectable through the ambient `/theme` path.

This boundary executes no package code: resolution only stats
directories and reads manifests/theme files as data.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pipy_harness.native.package_resources import (
    PackageResourceRoots,
    resolve_package_roots,
)
from pipy_harness.native.theme_files import build_theme_registry
from pipy_harness.native.themes import set_active_theme_registry

if TYPE_CHECKING:
    from pipy_harness.native.settings import SettingsManager


def compose_package_runtime(
    settings: "SettingsManager",
    cwd: Path,
    *,
    install_theme_registry: bool = True,
    include_package_themes: bool = True,
    explicit_theme_paths: tuple[Path, ...] = (),
) -> PackageResourceRoots:
    """Resolve configured package roots and install the package theme registry.

    Reads `settings.get_packages()` (project scope first), resolves each
    local-path source into per-kind roots, and — when
    `install_theme_registry` is true — builds and installs the active
    theme registry overlaying package themes (honoring the `themes`
    enablement filters) onto the built-ins. Returns the resolved roots so
    the caller can thread skills/prompts/extensions roots into discovery.
    """

    roots = resolve_package_roots(settings.get_package_entries(), cwd)
    if install_theme_registry:
        registry = build_theme_registry(
            roots.themes if include_package_themes else (),
            filters=settings.get_themes_patterns(),
            explicit_theme_paths=explicit_theme_paths,
        )
        set_active_theme_registry(registry)
    return roots

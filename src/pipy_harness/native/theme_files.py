"""File-based theme loading and the package theme registry.

Built-in chrome themes are code-defined `ChromePalette` objects in
`pipy_harness.native.themes`. Installed local-path packages contribute
*additional* themes as `.toml` files in a package `themes/` root. This
module loads those files and overlays them onto the built-ins through a
`ThemeRegistry` that the ambient theme functions (`resolve_palette`,
`available_theme_names`, `select_theme`, ...) consult.

A theme file declares a `name` plus any subset of the palette's SGR
color fields; unspecified fields inherit the default palette so a theme
author only writes the colors they want to change. Values must be SGR
parameter strings (digits and semicolons only) so a hostile theme file
can never inject a terminal control sequence into the chrome. A built-in
theme always wins a name collision; among packages, the first occurrence
wins. Pi-shaped `+/-pattern` filters can exclude a package theme.

This boundary reads theme files as data; it never imports or executes
package code. Only the palette name reaches any persisted state.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING

from pipy_harness.native.themes import (
    DEFAULT_PALETTE,
    DEFAULT_THEME_NAME,
    ChromePalette,
    builtin_palettes,
)

if TYPE_CHECKING:
    from pipy_harness.native.package_resources import PackageRoot

#: Palette field names that a theme file may set (every field except the
#: identity `name`). Used to validate and merge declared color values.
_COLOR_FIELDS: tuple[str, ...] = tuple(
    f.name for f in fields(ChromePalette) if f.name != "name"
)

#: A valid theme name: lowercase ASCII letters/digits/hyphen, not empty
#: and not starting or ending with a hyphen. Mirrors the resource-name
#: posture used elsewhere so a theme name is safe in UI and persistence.
_THEME_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")

#: An SGR parameter string: digits and semicolons only. No `\x1b`, `m`,
#: or other control bytes can appear, so a value can never move the
#: cursor, clear the screen, or break out of the styling wrapper.
_SGR_VALUE_RE = re.compile(r"^[0-9](?:[0-9;]*[0-9])?$")


def load_theme_file(path: Path) -> ChromePalette | None:
    """Load one `.toml` theme file into a `ChromePalette`.

    Returns the palette, or ``None`` when the file does not parse as a
    TOML table, the resolved name is invalid, or any declared color value
    is not an SGR parameter string. Unknown keys are ignored. Unspecified
    color fields inherit `DEFAULT_PALETTE`.
    """

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return None
    if not isinstance(data, Mapping):
        return None

    raw_name = data.get("name")
    if raw_name is None:
        name = path.stem
    elif isinstance(raw_name, str):
        name = raw_name
    else:
        return None
    if not _THEME_NAME_RE.match(name):
        return None

    overrides: dict[str, str] = {}
    for field in _COLOR_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if not isinstance(value, str) or not _SGR_VALUE_RE.match(value):
            return None
        overrides[field] = value

    merged = {field: getattr(DEFAULT_PALETTE, field) for field in _COLOR_FIELDS}
    merged.update(overrides)
    return ChromePalette(name=name, **merged)


def discover_package_themes(
    theme_roots: "Sequence[PackageRoot]",
    *,
    filters: Sequence[str] = (),
) -> tuple[dict[str, ChromePalette], list[str]]:
    """Load package theme files from `theme_roots` into a name→palette map.

    Roots are searched in order; within a root, `*.toml` files are loaded
    in sorted-name order. A theme whose name collides with a built-in is
    dropped (the built-in wins). Among packages, the first theme with a
    given name wins. A theme excluded by the global `+/-pattern` `filters`
    or by its own package's per-package filter (carried on the
    `PackageRoot`) is dropped. Each dropped theme records a safe
    diagnostic. Unparseable files are skipped silently with a diagnostic.

    Returns ``(palettes, diagnostics)``.
    """

    from pipy_harness.native.resource_enablement import is_resource_enabled

    builtins = builtin_palettes()
    palettes: dict[str, ChromePalette] = {}
    diagnostics: list[str] = []

    for root in theme_roots:
        package_filters = tuple(root.filters)
        for path in _iter_toml_files(root.path):
            palette = load_theme_file(path)
            if palette is None:
                diagnostics.append(f"theme file skipped: {path.name}")
                continue
            name = palette.name
            if name in builtins:
                diagnostics.append(f"theme {name!r} shadows a built-in; ignored")
                continue
            if name in palettes:
                diagnostics.append(f"theme {name!r} already provided; ignored")
                continue
            if package_filters and not is_resource_enabled(name, list(package_filters)):
                diagnostics.append(f"theme {name!r} disabled by package filter")
                continue
            if filters and not is_resource_enabled(name, list(filters)):
                diagnostics.append(f"theme {name!r} disabled by filter")
                continue
            palettes[name] = palette
    return palettes, diagnostics


def discover_theme_paths(
    paths: Sequence[Path],
    *,
    filters: Sequence[str] = (),
) -> tuple[dict[str, ChromePalette], list[str]]:
    """Load themes from explicit CLI file or directory paths.

    These are temporary, per-run sources from ``--theme``. Built-in themes
    still win collisions, and among explicit paths the first theme wins.
    Missing paths fail closed with a safe diagnostic; no absolute path is
    surfaced through the returned palette data.
    """

    from pipy_harness.native.resource_enablement import is_resource_enabled

    builtins = builtin_palettes()
    palettes: dict[str, ChromePalette] = {}
    diagnostics: list[str] = []
    for source in paths:
        path = source.expanduser()
        candidates = [path] if path.suffix == ".toml" else _iter_toml_files(path)
        if not candidates:
            diagnostics.append(f"theme path skipped: {path.name}")
        for candidate in candidates:
            palette = load_theme_file(candidate)
            if palette is None:
                diagnostics.append(f"theme file skipped: {candidate.name}")
                continue
            name = palette.name
            if name in builtins:
                diagnostics.append(f"theme {name!r} shadows a built-in; ignored")
                continue
            if name in palettes:
                diagnostics.append(f"theme {name!r} already provided; ignored")
                continue
            if filters and not is_resource_enabled(name, list(filters)):
                diagnostics.append(f"theme {name!r} disabled by filter")
                continue
            palettes[name] = palette
    return palettes, diagnostics


def _iter_toml_files(directory: Path) -> list[Path]:
    """Return the `*.toml` files directly under `directory`, sorted.

    A symlinked directory is ignored; a file that resolves outside the
    directory is skipped. Missing dirs and OS errors yield an empty list.
    """

    try:
        if directory.is_symlink() or not directory.is_dir():
            return []
        containment = directory.resolve()
    except OSError:
        return []
    try:
        entries = sorted(directory.glob("*.toml"), key=lambda p: p.name)
    except OSError:
        return []
    files: list[Path] = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            entry.resolve().relative_to(containment)
        except (OSError, ValueError):
            continue
        files.append(entry)
    return files


@dataclass(frozen=True, slots=True)
class ThemeRegistry:
    """Built-in plus package-contributed palettes for one session.

    The single source of truth the ambient theme functions consult when a
    package theme registry is active. Resolution fails safe to the default
    palette for an unknown name.
    """

    palettes: Mapping[str, ChromePalette]

    @classmethod
    def builtin(cls) -> "ThemeRegistry":
        return cls(builtin_palettes())

    def names(self) -> tuple[str, ...]:
        """Theme names, default first, then the rest sorted by name."""

        rest = sorted(n for n in self.palettes if n != DEFAULT_THEME_NAME)
        ordered = [DEFAULT_THEME_NAME] if DEFAULT_THEME_NAME in self.palettes else []
        return tuple(ordered + rest)

    def is_known(self, name: str) -> bool:
        return name in self.palettes

    def resolve(self, name: str | None) -> ChromePalette:
        if name is None:
            return DEFAULT_PALETTE
        return self.palettes.get(name, DEFAULT_PALETTE)


def build_theme_registry(
    theme_roots: "Sequence[PackageRoot]",
    *,
    filters: Sequence[str] = (),
    explicit_theme_paths: Sequence[Path] = (),
) -> ThemeRegistry:
    """Overlay package theme files from `theme_roots` onto the built-ins."""

    explicit, _explicit_diagnostics = discover_theme_paths(explicit_theme_paths)
    package, _diagnostics = discover_package_themes(theme_roots, filters=filters)
    merged = builtin_palettes()
    merged.update(package)
    merged.update(explicit)
    return ThemeRegistry(merged)

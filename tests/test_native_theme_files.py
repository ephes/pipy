"""File-based theme loading and the package theme registry.

Built-in themes are code-defined `ChromePalette` objects. Installed
local-path packages contribute *additional* themes as `.toml` files. A
theme file declares a `name` plus any subset of the palette's SGR color
fields; unspecified fields inherit the default palette so authoring a
theme is cheap. `ThemeRegistry` overlays package palettes onto the
built-ins: a built-in always wins a name collision, and among packages
the first occurrence wins. Pi-shaped `+/-pattern` filters can exclude a
package theme.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native.package_resources import PackageRoot
from pipy_harness.native.themes import DEFAULT_PALETTE, DEFAULT_THEME_NAME
from pipy_harness.native.theme_files import (
    ThemeRegistry,
    build_theme_registry,
    discover_package_themes,
    load_theme_file,
)


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _roots(*dirs: Path) -> list[PackageRoot]:
    return [PackageRoot(d) for d in dirs]


def test_load_theme_inherits_unspecified_fields(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "midnight.toml",
        'name = "midnight"\n'
        'title_truecolor = "1;38;2;10;20;30"\n'
        'separator_truecolor = "38;2;40;50;60"\n',
    )

    palette = load_theme_file(path)

    assert palette is not None
    assert palette.name == "midnight"
    assert palette.title_truecolor == "1;38;2;10;20;30"
    assert palette.separator_truecolor == "38;2;40;50;60"
    # Unspecified fields inherit the default palette.
    assert palette.accent_truecolor == DEFAULT_PALETTE.accent_truecolor


def test_load_theme_uses_stem_when_name_absent(tmp_path: Path) -> None:
    path = _write(tmp_path / "dusk.toml", 'accent_truecolor = "38;2;1;2;3"\n')

    palette = load_theme_file(path)

    assert palette is not None
    assert palette.name == "dusk"


def test_load_theme_rejects_bad_sgr_value(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "evil.toml",
        'name = "evil"\ntitle_truecolor = "31m\\u001b[2J"\n',
    )

    assert load_theme_file(path) is None


def test_load_theme_rejects_invalid_name(tmp_path: Path) -> None:
    path = _write(tmp_path / "bad.toml", 'name = "Bad Name!"\n')

    assert load_theme_file(path) is None


def test_load_theme_rejects_non_table(tmp_path: Path) -> None:
    path = _write(tmp_path / "broken.toml", "not valid = = toml [[[")

    assert load_theme_file(path) is None


def test_discover_package_themes_collects_across_roots(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    _write(root_a / "one.toml", 'name = "one"\naccent_truecolor = "38;2;1;1;1"\n')
    _write(root_b / "two.toml", 'name = "two"\naccent_truecolor = "38;2;2;2;2"\n')

    palettes, diagnostics = discover_package_themes(_roots(root_a, root_b))

    assert set(palettes) == {"one", "two"}
    assert diagnostics == []


def test_discover_package_themes_builtin_wins_collision(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _write(root / "pi.toml", f'name = "{DEFAULT_THEME_NAME}"\naccent_truecolor = "38;2;9;9;9"\n')

    palettes, diagnostics = discover_package_themes(_roots(root))

    assert DEFAULT_THEME_NAME not in palettes
    assert diagnostics  # collision recorded as a safe diagnostic


def test_discover_package_themes_first_package_wins(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    _write(root_a / "dup.toml", 'name = "dup"\naccent_truecolor = "38;2;1;1;1"\n')
    _write(root_b / "dup.toml", 'name = "dup"\naccent_truecolor = "38;2;2;2;2"\n')

    palettes, _ = discover_package_themes(_roots(root_a, root_b))

    assert palettes["dup"].accent_truecolor == "38;2;1;1;1"


def test_discover_package_themes_filter_excludes(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _write(root / "hidden.toml", 'name = "hidden"\naccent_truecolor = "38;2;1;1;1"\n')

    palettes, _ = discover_package_themes(_roots(root), filters=["-hidden"])

    assert "hidden" not in palettes


def test_registry_overlays_builtins(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _write(root / "neon.toml", 'name = "neon"\naccent_truecolor = "38;2;7;7;7"\n')

    registry = build_theme_registry(_roots(root))

    assert isinstance(registry, ThemeRegistry)
    assert registry.is_known("neon")
    assert registry.is_known(DEFAULT_THEME_NAME)
    assert registry.resolve("neon").accent_truecolor == "38;2;7;7;7"
    # Default-first ordering preserved.
    assert registry.names()[0] == DEFAULT_THEME_NAME
    assert "neon" in registry.names()


def test_registry_resolve_unknown_falls_back_to_default(tmp_path: Path) -> None:
    registry = ThemeRegistry.builtin()

    assert registry.resolve("does-not-exist").name == DEFAULT_PALETTE.name

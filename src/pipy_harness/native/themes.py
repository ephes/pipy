"""Theme / color-scheme registry for the native REPL chrome.

This module owns the *data* behind the chrome's color styling: a small set
of named palettes plus the resolution and persistence helpers that let a
user pick one. ``chrome.ChromeStyle`` holds a ``ChromePalette`` and renders
through it, so selecting a theme changes the rendered ANSI styling on every
chrome surface (startup chrome, separators, the bottom status block, the
tool-loop TUI frame, and the prompt cursor).

The palette only ever changes *which* ANSI color codes are emitted when color
is enabled. It has no effect on the NO_COLOR / non-TTY fallback: that decision
is made in ``chrome.chrome_style_for`` before a palette is ever consulted, and
``ChromeStyle`` emits plain text whenever ``enabled`` is false regardless of
the active theme.

Persistence is a non-secret JSON file (the chosen theme name only), mirroring
``repl_state.NativeDefaultsStore``. Selection precedence is: a valid
``PIPY_THEME`` environment override, then the persisted store, then the
built-in default.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import MutableMapping, Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ChromePalette:
    """The full set of ANSI color codes one chrome theme emits.

    Truecolor codes are used when the terminal advertises 24-bit support;
    the ``*_fallback`` codes preserve the same intent on 16-color terminals.
    Codes are SGR parameter strings (without the ``\\x1b[`` / ``m`` wrapper).
    """

    name: str
    title_truecolor: str
    title_fallback: str
    accent_truecolor: str
    accent_fallback: str
    section_truecolor: str
    section_fallback: str
    dim_truecolor: str
    dim_fallback: str
    secondary_dim_truecolor: str
    error_truecolor: str
    error_fallback: str
    user_message_bg_truecolor: str
    user_message_text_truecolor: str
    tool_command_bg_truecolor: str
    separator_truecolor: str
    separator_fallback: str


# The default "pi" palette reproduces the reference Pi terminal product: a
# muted sage title, soft-yellow section labels, flat-gray secondary text, and
# a soft-purple input separator. These values are the literals that previously
# lived as module constants in chrome.py.
_PI_PALETTE = ChromePalette(
    name="pi",
    title_truecolor="1;38;2;138;190;183",
    title_fallback="1;36",
    accent_truecolor="38;2;138;190;183",
    accent_fallback="36",
    section_truecolor="38;2;240;198;116",
    section_fallback="1;33",
    dim_truecolor="38;2;102;102;102",
    dim_fallback="2",
    secondary_dim_truecolor="38;2;128;128;128",
    error_truecolor="38;2;204;102;102",
    error_fallback="31",
    user_message_bg_truecolor="48;2;52;53;65",
    user_message_text_truecolor="38;2;212;212;212",
    tool_command_bg_truecolor="48;2;40;50;40",
    separator_truecolor="38;2;178;148;187",
    separator_fallback="35",
)

# A high-contrast scheme for low-vision / bright-terminal users: bold primary
# colors, brighter dim text, and stronger separators.
_HIGH_CONTRAST_PALETTE = ChromePalette(
    name="high-contrast",
    title_truecolor="1;38;2;255;255;255",
    title_fallback="1;97",
    accent_truecolor="1;38;2;0;215;255",
    accent_fallback="1;96",
    section_truecolor="1;38;2;255;215;0",
    section_fallback="1;93",
    dim_truecolor="38;2;200;200;200",
    dim_fallback="37",
    secondary_dim_truecolor="38;2;170;170;170",
    error_truecolor="1;38;2;255;85;85",
    error_fallback="1;91",
    user_message_bg_truecolor="48;2;0;0;0",
    user_message_text_truecolor="38;2;255;255;255",
    tool_command_bg_truecolor="48;2;28;28;28",
    separator_truecolor="1;38;2;0;215;255",
    separator_fallback="1;96",
)

# A cool "ocean" scheme: teal title, cyan accents, blue separators.
_OCEAN_PALETTE = ChromePalette(
    name="ocean",
    title_truecolor="1;38;2;94;196;201",
    title_fallback="1;36",
    accent_truecolor="38;2;94;196;201",
    accent_fallback="36",
    section_truecolor="38;2;126;200;227",
    section_fallback="1;34",
    dim_truecolor="38;2;110;130;140",
    dim_fallback="2",
    secondary_dim_truecolor="38;2;130;150;160",
    error_truecolor="38;2;233;105;134",
    error_fallback="31",
    user_message_bg_truecolor="48;2;30;44;54",
    user_message_text_truecolor="38;2;214;230;236",
    tool_command_bg_truecolor="48;2;26;48;52",
    separator_truecolor="38;2;90;160;200",
    separator_fallback="34",
)

_THEMES: dict[str, ChromePalette] = {
    _PI_PALETTE.name: _PI_PALETTE,
    _HIGH_CONTRAST_PALETTE.name: _HIGH_CONTRAST_PALETTE,
    _OCEAN_PALETTE.name: _OCEAN_PALETTE,
}

DEFAULT_THEME_NAME = _PI_PALETTE.name
THEME_ENV_VAR = "PIPY_THEME"

#: Default palette object, exported for chrome.ChromeStyle's field default.
DEFAULT_PALETTE = _PI_PALETTE


def available_theme_names() -> tuple[str, ...]:
    """Return the registered theme names in a stable, default-first order."""

    ordered = [DEFAULT_THEME_NAME] + sorted(
        n for n in _THEMES if n != DEFAULT_THEME_NAME
    )
    return tuple(ordered)


def is_known_theme(name: str) -> bool:
    return name in _THEMES


def resolve_palette(name: str | None) -> ChromePalette:
    """Map a theme name to its palette, failing safe to the default."""

    if name is None:
        return DEFAULT_PALETTE
    return _THEMES.get(name, DEFAULT_PALETTE)


class NativeThemeStore:
    """Private JSON store for the non-secret chrome theme selection."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_native_theme_path()

    def load(self) -> str | None:
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(body, dict):
            return None
        if body.get("schema") != "pipy.native-theme" or body.get("schema_version") != 1:
            return None
        theme = body.get("theme")
        if not isinstance(theme, str) or not is_known_theme(theme):
            return None
        return theme

    def save(self, theme: str) -> None:
        if not is_known_theme(theme):
            raise ValueError(f"unknown theme: {theme}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        payload = {
            "schema": "pipy.native-theme",
            "schema_version": 1,
            "theme": theme,
        }
        temporary_path = self.path.with_name(f"{self.path.name}.partial")
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        temporary_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        temporary_path.replace(self.path)
        self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def default_native_theme_path() -> Path:
    configured_path = os.environ.get("PIPY_NATIVE_THEME_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    return Path.home() / ".local" / "state" / "pipy" / "native-theme.json"


def resolve_active_theme_name(
    *,
    env: Mapping[str, str] | None = None,
    store: NativeThemeStore | None = None,
) -> str:
    """Resolve the active theme name: env override, then store, then default.

    Only a *known* theme name is honored at each tier; an unknown value falls
    through to the next tier so a stale persisted value or a typo'd env var
    can never blank out the chrome.
    """

    environ = env if env is not None else os.environ
    env_choice = environ.get(THEME_ENV_VAR)
    if env_choice and is_known_theme(env_choice):
        return env_choice
    if store is not None:
        stored = store.load()
        if stored is not None:
            return stored
    return DEFAULT_THEME_NAME


def theme_status_lines(
    *,
    env: Mapping[str, str] | None = None,
    store: NativeThemeStore | None = None,
) -> list[str]:
    """Read-only ``/theme`` status: the active theme plus the registered set.

    Strictly informational — it neither switches the theme, mutates the store,
    nor touches the environment. Used by ``/theme`` with no argument.
    """

    active = resolve_active_theme_name(env=env, store=store)
    lines = ["pipy native REPL theme:", f"  active: {active}", "  available:"]
    for name in available_theme_names():
        marker = " (active)" if name == active else ""
        lines.append(f"    {name}{marker}")
    return lines


def select_theme(
    reference: str,
    *,
    environ: MutableMapping[str, str],
    store: NativeThemeStore | None = None,
) -> tuple[bool, str]:
    """Switch the active chrome theme for the running session.

    Validates ``reference`` against the registry (fail-closed on unknown),
    persists the choice to ``store`` when supplied, and sets ``PIPY_THEME`` in
    ``environ`` so the very next ``chrome_style_for`` render picks up the new
    palette. It performs no provider turn, no tool call, and writes nothing to
    the session archive — only the non-secret theme name reaches the store.
    """

    name = reference.strip()
    if not name:
        return False, "pipy: malformed /theme command. Provide a theme name."
    if not is_known_theme(name):
        catalog = ", ".join(available_theme_names())
        return False, f"pipy: unknown theme {name!r}. Available: {catalog}."
    if store is not None:
        try:
            store.save(name)
        except OSError:
            pass
    environ[THEME_ENV_VAR] = name
    return True, f"pipy: selected theme {name}."

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
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipy_harness.native.theme_files import ThemeRegistry


@dataclass(frozen=True, slots=True)
class ChromePalette:
    """The full set of ANSI color codes one chrome theme emits.

    Truecolor codes are used when the terminal advertises 24-bit support;
    the ``*_fallback`` codes preserve the same intent when only indexed SGR
    colors are available.
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
    secondary_dim_fallback: str
    error_truecolor: str
    error_fallback: str
    user_message_bg_truecolor: str
    user_message_bg_fallback: str
    user_message_text_truecolor: str
    tool_command_bg_truecolor: str
    tool_command_bg_fallback: str
    separator_truecolor: str
    separator_fallback: str
    success_truecolor: str = "38;2;152;195;121"
    success_fallback: str = "32"
    warning_truecolor: str = "38;2;229;192;123"
    warning_fallback: str = "33"
    # Pi's tool rows (dark theme `toolPendingBg`/`toolSuccessBg`/`toolErrorBg`
    # backgrounds and `toolOutput`, which also colours diff context lines).
    tool_pending_bg_truecolor: str = "48;2;52;56;58"
    tool_pending_bg_fallback: str = "48;5;237"
    tool_success_bg_truecolor: str = "48;2;37;65;49"
    tool_success_bg_fallback: str = "48;5;23"
    tool_error_bg_truecolor: str = "48;2;91;40;42"
    tool_error_bg_fallback: str = "48;5;52"
    tool_output_truecolor: str = "38;2;157;165;169"
    tool_output_fallback: str = "38;5;145"
    user_message_text_fallback: str = "37"
    # Pi's `thinkingText` (reasoning, drawn italic); `None` uses
    # `secondary_dim`.
    thinking_text_truecolor: str | None = None
    thinking_text_fallback: str | None = None
    # Pi's `customMessageBg`: the box behind `[compaction]`/`[branch]` rows.
    custom_message_bg_truecolor: str = "48;2;58;48;85"
    custom_message_bg_fallback: str = "48;5;59"
    # Pi's editor border colours (`getThinkingBorderColor` per thinking
    # level, `getBashModeBorderColor`). `None` draws that border in
    # `separator`.
    thinking_off_truecolor: str | None = None
    thinking_off_fallback: str | None = None
    thinking_minimal_truecolor: str | None = None
    thinking_minimal_fallback: str | None = None
    thinking_low_truecolor: str | None = None
    thinking_low_fallback: str | None = None
    thinking_medium_truecolor: str | None = None
    thinking_medium_fallback: str | None = None
    thinking_high_truecolor: str | None = None
    thinking_high_fallback: str | None = None
    thinking_xhigh_truecolor: str | None = None
    thinking_xhigh_fallback: str | None = None
    thinking_max_truecolor: str | None = None
    thinking_max_fallback: str | None = None
    bash_mode_truecolor: str | None = None
    bash_mode_fallback: str | None = None


# The default "pi" palette is Pi's `dark` theme (`theme/dark.json`), resolved
# by Pi's own `theme.ts` in truecolor and 256-colour mode: `accent`,
# `mdHeading` section labels, `dim`, `muted` secondary text, `thinkingText`,
# `error`/`warning`/`success`, the `userMessageBg` bubble with
# `userMessageText`, and the thinking-level editor borders. The title has no
# Pi counterpart (Pi draws a logo); it is bold `accent`.
_PI_PALETTE = ChromePalette(
    name="pi",
    title_truecolor="1;38;2;167;152;215",
    title_fallback="1;38;5;140",
    accent_truecolor="38;2;167;152;215",
    accent_fallback="38;5;140",
    section_truecolor="38;2;205;154;34",
    section_fallback="38;5;172",
    dim_truecolor="38;2;126;136;142",
    dim_fallback="38;5;102",
    secondary_dim_truecolor="38;2;157;165;169",
    secondary_dim_fallback="38;5;145",
    error_truecolor="38;2;234;127;129",
    error_fallback="38;5;174",
    user_message_bg_truecolor="48;2;33;59;73",
    user_message_bg_fallback="48;5;23",
    user_message_text_truecolor="38;2;222;224;225",
    user_message_text_fallback="38;5;254",
    tool_command_bg_truecolor="48;2;40;50;40",
    tool_command_bg_fallback="48;5;235",
    separator_truecolor="38;2;108;118;123",
    separator_fallback="38;5;66",
    success_truecolor="38;2;104;183;141",
    success_fallback="38;5;72",
    warning_truecolor="38;2;205;154;34",
    warning_fallback="38;5;172",
    tool_pending_bg_truecolor="48;2;52;56;58",
    tool_pending_bg_fallback="48;5;237",
    tool_success_bg_truecolor="48;2;37;65;49",
    tool_success_bg_fallback="48;5;23",
    tool_error_bg_truecolor="48;2;91;40;42",
    tool_error_bg_fallback="48;5;52",
    tool_output_truecolor="38;2;157;165;169",
    tool_output_fallback="38;5;145",
    thinking_text_truecolor="38;2;150;160;164",
    thinking_text_fallback="38;5;109",
    custom_message_bg_truecolor="48;2;58;48;85",
    custom_message_bg_fallback="48;5;59",
    thinking_off_truecolor="38;2;108;118;123",
    thinking_off_fallback="38;5;66",
    thinking_minimal_truecolor="38;2;104;128;141",
    thinking_minimal_fallback="38;5;66",
    thinking_low_truecolor="38;2;84;137;164",
    thinking_low_fallback="38;5;67",
    thinking_medium_truecolor="38;2;97;133;204",
    thinking_medium_fallback="38;5;68",
    thinking_high_truecolor="38;2;151;118;229",
    thinking_high_fallback="38;5;104",
    thinking_xhigh_truecolor="38;2;222;84;193",
    thinking_xhigh_fallback="38;5;169",
    thinking_max_truecolor="38;2;254;84;98",
    thinking_max_fallback="38;5;203",
    bash_mode_truecolor="38;2;94;178;134",
    bash_mode_fallback="38;5;72",
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
    secondary_dim_fallback="37",
    error_truecolor="1;38;2;255;85;85",
    error_fallback="1;91",
    user_message_bg_truecolor="48;2;0;0;0",
    user_message_bg_fallback="40",
    user_message_text_truecolor="38;2;255;255;255",
    tool_command_bg_truecolor="48;2;28;28;28",
    tool_command_bg_fallback="48;5;235",
    separator_truecolor="1;38;2;0;215;255",
    separator_fallback="1;96",
    success_truecolor="1;38;2;0;255;0",
    success_fallback="1;92",
    warning_truecolor="1;38;2;255;215;0",
    warning_fallback="1;93",
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
    secondary_dim_fallback="2",
    error_truecolor="38;2;233;105;134",
    error_fallback="31",
    user_message_bg_truecolor="48;2;30;44;54",
    user_message_bg_fallback="48;5;236",
    user_message_text_truecolor="38;2;214;230;236",
    tool_command_bg_truecolor="48;2;26;48;52",
    tool_command_bg_fallback="48;5;235",
    separator_truecolor="38;2;90;160;200",
    separator_fallback="34",
    success_truecolor="38;2;126;200;160",
    success_fallback="32",
    warning_truecolor="38;2;226;192;141",
    warning_fallback="33",
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


# The session-scoped theme registry overlaying package-contributed
# palettes onto the built-ins. The ambient theme functions consult it the
# same way they consult the `PIPY_THEME` env var and the persisted store —
# as process-global session state — so a package theme becomes selectable
# and re-colors the chrome without threading a registry through every
# `chrome_style_for` render. `None` means built-ins only.
_ACTIVE_REGISTRY: "ThemeRegistry | None" = None


def set_active_theme_registry(registry: "ThemeRegistry | None") -> None:
    """Install (or clear, with ``None``) the active package theme registry."""

    global _ACTIVE_REGISTRY
    _ACTIVE_REGISTRY = registry


def active_theme_registry() -> "ThemeRegistry | None":
    """Return the active package theme registry, or ``None`` for built-ins."""

    return _ACTIVE_REGISTRY


def _effective_registry(
    registry: "ThemeRegistry | None",
) -> "ThemeRegistry | None":
    """An explicit registry wins; otherwise fall back to the active one."""

    return registry if registry is not None else _ACTIVE_REGISTRY


def builtin_palettes() -> dict[str, ChromePalette]:
    """Return a copy of the built-in name→palette mapping.

    The seed for a `ThemeRegistry`. Returned as a fresh dict so callers
    can overlay package-contributed palettes without mutating the
    module-global built-in set.
    """

    return dict(_THEMES)


def available_theme_names(
    *, registry: "ThemeRegistry | None" = None
) -> tuple[str, ...]:
    """Return the registered theme names in a stable, default-first order.

    Consults `registry` (or the active package theme registry) so
    package-contributed themes appear alongside the built-ins.
    """

    effective = _effective_registry(registry)
    if effective is not None:
        return effective.names()
    ordered = [DEFAULT_THEME_NAME] + sorted(
        n for n in _THEMES if n != DEFAULT_THEME_NAME
    )
    return tuple(ordered)


def is_known_theme(name: str, *, registry: "ThemeRegistry | None" = None) -> bool:
    effective = _effective_registry(registry)
    if effective is not None:
        return effective.is_known(name)
    return name in _THEMES


def resolve_palette(
    name: str | None, *, registry: "ThemeRegistry | None" = None
) -> ChromePalette:
    """Map a theme name to its palette, failing safe to the default."""

    effective = _effective_registry(registry)
    if effective is not None:
        return effective.resolve(name)
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
        return False, "pipy: no theme selected. Provide a theme name."
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

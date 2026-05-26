"""Theme registry for the native pipy startup chrome and prompt labels.

A `Theme` is pure data: a small bundle of ANSI escape prefixes that
the integrator can wrap around heading, label, value, note, and error
strings. Themes are resolved synchronously without I/O and without any
runtime dependency beyond the standard library; the resolver is a pure
function over the requested name and an explicit `tty` flag.

Three builtin themes ship with the native runtime:

- ``default`` — bright/colorful (cyan headings, green labels, dim
  notes) for human terminals.
- ``quiet`` — minimal (bold only, no color) for users who dislike
  color but still want emphasis.
- ``mono`` — pure plain text with always-empty ANSI codes, suitable
  for captured streams, log files, and test snapshots.

When the integrator knows it is writing to a non-TTY stream
(captured stdout, redirected file, pytest ``capsys``), it should pass
``tty=False`` to :func:`resolve_theme`; the resolver then returns the
``mono`` theme regardless of the requested name so downstream output
stays plain.

This module owns no I/O. It does not read environment variables, files,
terminal capability databases, or any other side-effecting source. The
caller is responsible for detecting TTY status and for selecting a
theme name from configuration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

_ANSI_RESET = "\x1b[0m"


@dataclass(frozen=True, slots=True)
class ThemeColors:
    """ANSI prefix bundle for the five styled regions of native output."""

    heading: str
    label: str
    value: str
    note: str
    error: str
    reset: str


@dataclass(frozen=True, slots=True)
class Theme:
    """A named bundle of ANSI prefixes."""

    name: str
    colors: ThemeColors


_DEFAULT_THEME = Theme(
    name="default",
    colors=ThemeColors(
        heading="\x1b[1;36m",
        label="\x1b[32m",
        value="\x1b[0m",
        note="\x1b[2m",
        error="\x1b[1;31m",
        reset=_ANSI_RESET,
    ),
)


_QUIET_THEME = Theme(
    name="quiet",
    colors=ThemeColors(
        heading="\x1b[1m",
        label="\x1b[1m",
        value="",
        note="",
        error="\x1b[1m",
        reset=_ANSI_RESET,
    ),
)


_MONO_THEME = Theme(
    name="mono",
    colors=ThemeColors(
        heading="",
        label="",
        value="",
        note="",
        error="",
        reset="",
    ),
)


BUILTIN_THEMES: Mapping[str, Theme] = MappingProxyType(
    {
        _DEFAULT_THEME.name: _DEFAULT_THEME,
        _QUIET_THEME.name: _QUIET_THEME,
        _MONO_THEME.name: _MONO_THEME,
    }
)


def list_theme_names() -> list[str]:
    """Return the sorted list of registered theme names."""

    return sorted(BUILTIN_THEMES)


def resolve_theme(name: str | None, *, tty: bool = True) -> Theme:
    """Return the theme to use for the current output stream.

    Resolution rules, in order:

    1. If ``tty`` is ``False``, return the ``mono`` theme so captured
       streams stay plain regardless of the requested name.
    2. If ``name`` is ``None``, return the ``default`` theme.
    3. Otherwise, look the (non-empty, string) name up in
       :data:`BUILTIN_THEMES` and return it.

    Raises:
        ValueError: when ``name`` is the empty string or any unknown
            value. The error message lists the supported names so the
            caller can surface them in a CLI error.
        TypeError: when ``name`` is provided but is not a ``str``.
    """

    if not tty:
        return _MONO_THEME

    if name is not None:
        if not isinstance(name, str):  # pragma: no cover - defensive
            raise TypeError(
                f"theme name must be a string or None, got {type(name).__name__}"
            )
        if name == "":
            raise ValueError(
                "theme name must be a non-empty string; "
                f"supported names: {', '.join(list_theme_names())}"
            )
        if name not in BUILTIN_THEMES:
            raise ValueError(
                f"unknown theme {name!r}; "
                f"supported names: {', '.join(list_theme_names())}"
            )

    if name is None:
        return _DEFAULT_THEME

    return BUILTIN_THEMES[name]


def style(text: str, prefix: str, suffix: str = "") -> str:
    """Wrap ``text`` with a theme prefix and suffix.

    When ``prefix`` is the empty string (as on the ``mono`` theme, or
    on theme entries that intentionally opt out of styling) the text
    is returned unchanged so we never emit a stray reset on plain
    output.
    """

    if prefix == "":
        return text
    return f"{prefix}{text}{suffix}"

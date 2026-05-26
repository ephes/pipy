"""Shared Pi-parity terminal chrome for the native REPL.

Both the bounded no-tool REPL (`NativeNoToolReplSession`) and the
bounded tool-loop REPL (`NativeToolReplSession`) render the same
visual frame: title, dim controls strip, `Type /` affordance,
loaded-only `[Section]` listings, separator-framed prompt area, and a
two-line dim footer with the workspace and provider/model state.

This module owns the helpers so the same rendering ships from both
REPL surfaces. The styles fall back to plain text when the output
stream is not a TTY or when `NO_COLOR` is set; truecolor codes are used
when `COLORTERM` advertises 24-bit support, otherwise the 16-color
fallbacks preserve the same intent.
"""

from __future__ import annotations

import os
import shutil
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Iterable, TextIO

from pipy_harness.capture import sanitize_text


_STARTUP_CHROME_WIDTH_FALLBACK = 88
_STARTUP_CHROME_RESOURCE_SOURCES = {
    "context": ("AGENTS.md", "CLAUDE.md", ".claude"),
    "skills": (".claude/skills", ".codex/skills", ".agents/skills"),
    "prompts": (".claude/commands", "prompts", ".agents/prompts"),
    "extensions": (".claude/extensions", ".agents/plugins", ".codex/plugins"),
}

_PI_TITLE_TRUECOLOR = "1;38;2;138;190;183"
_PI_TITLE_FALLBACK = "1;36"
_PI_SECTION_TRUECOLOR = "1;38;2;240;198;116"
_PI_SECTION_FALLBACK = "1;33"
_PI_DIM_TRUECOLOR = "38;2;128;128;128"
_PI_DIM_FALLBACK = "2"
_PI_SEPARATOR_TRUECOLOR = "38;2;178;148;187"
_PI_SEPARATOR_FALLBACK = "35"


@dataclass(frozen=True, slots=True)
class ChromeStyle:
    """Pi-parity color palette for the native REPL chrome.

    Colors mirror the reference Pi terminal product: a muted sage for
    the title, a soft yellow for ``[Section]`` labels, a flat gray for
    secondary text, and a soft purple for the input separator. Captured
    streams (non-TTY) receive the plain text fall-through so test logs
    stay readable.
    """

    enabled: bool
    truecolor: bool = False

    def title(self, text: str) -> str:
        return self._wrap(text, _PI_TITLE_TRUECOLOR, _PI_TITLE_FALLBACK)

    def section_label(self, text: str) -> str:
        return self._wrap(text, _PI_SECTION_TRUECOLOR, _PI_SECTION_FALLBACK)

    def dim(self, text: str) -> str:
        return self._wrap(text, _PI_DIM_TRUECOLOR, _PI_DIM_FALLBACK)

    def separator(self, text: str) -> str:
        return self._wrap(text, _PI_SEPARATOR_TRUECOLOR, _PI_SEPARATOR_FALLBACK)

    def _wrap(self, text: str, truecolor_code: str, fallback_code: str) -> str:
        if not self.enabled:
            return text
        code = truecolor_code if self.truecolor else fallback_code
        return f"\x1b[{code}m{text}\x1b[0m"


def chrome_style_for(error_stream: TextIO) -> ChromeStyle:
    is_tty = bool(getattr(error_stream, "isatty", lambda: False)())
    term = os.environ.get("TERM", "")
    enabled = is_tty and "NO_COLOR" not in os.environ and term.lower() != "dumb"
    colorterm = os.environ.get("COLORTERM", "").lower()
    truecolor = enabled and (
        colorterm in {"truecolor", "24bit"}
        or "256color" in term.lower()
        or "direct" in term.lower()
    )
    return ChromeStyle(enabled=enabled, truecolor=truecolor)


def chrome_width(error_stream: TextIO) -> int:
    if bool(getattr(error_stream, "isatty", lambda: False)()):
        return max(
            60,
            shutil.get_terminal_size((_STARTUP_CHROME_WIDTH_FALLBACK, 24)).columns,
        )
    return _STARTUP_CHROME_WIDTH_FALLBACK


def pipy_version_label() -> str:
    try:
        return metadata.version("pipy")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def print_startup_chrome(error_stream: TextIO, *, cwd: Path) -> None:
    """Render the Pi-parity compact startup chrome on `error_stream`.

    Layout: title line, dim subtitle, blank line, dim controls strip,
    `Type /` affordance, blank line, then `[Context]`/`[Skills]`/
    `[Prompts]`/`[Extensions]` listings for resources discovered under
    `cwd`. Sections with no candidates are omitted.
    """

    style = chrome_style_for(error_stream)
    width = chrome_width(error_stream)
    resource_labels = _resource_labels(cwd)

    print(
        style.title(f"pipy v{pipy_version_label()}  native shell"),
        file=error_stream,
    )
    _print_wrapped(
        error_stream,
        "  ",
        "Local slash commands and bounded provider turns stay behind pipy-owned boundaries.",
        width=width,
        style=style.dim,
    )
    print(file=error_stream)
    _print_wrapped(
        error_stream,
        "  ",
        " · ".join(
            (
                "Ctrl-C interrupt",
                "/exit quit",
                "/ commands",
                "/help reference",
                "! bash deferred",
            )
        ),
        width=width,
        style=style.dim,
    )
    _print_wrapped(
        error_stream,
        "  ",
        "Type / to open the command menu; /help for the full reference.",
        width=width,
        style=style.dim,
    )
    print(file=error_stream)
    for section_name, label in (
        ("Context", resource_labels.get("context", "")),
        ("Skills", resource_labels.get("skills", "")),
        ("Prompts", resource_labels.get("prompts", "")),
        ("Extensions", resource_labels.get("extensions", "")),
    ):
        if not label or label == "not loaded":
            continue
        print(style.section_label(f"[{section_name}]"), file=error_stream)
        _print_wrapped(
            error_stream,
            "  ",
            label,
            width=width,
            style=_identity_text,
        )
        print(file=error_stream)


def print_input_separator(error_stream: TextIO) -> None:
    style = chrome_style_for(error_stream)
    width = chrome_width(error_stream)
    print(style.separator("─" * width), file=error_stream)


def print_footer_lines(error_stream: TextIO, lines: Iterable[str]) -> None:
    style = chrome_style_for(error_stream)
    for line in lines:
        print(style.dim(line), file=error_stream)


def format_provider_model(provider_name: str, model_id: str) -> str:
    """Format provider/model in the Pi parenthesized style `(provider) model`."""

    return sanitize_text(f"({provider_name}) {model_id}")


def _resource_labels(cwd: Path) -> dict[str, str]:
    return {
        category: _resource_label(cwd, candidates)
        for category, candidates in _STARTUP_CHROME_RESOURCE_SOURCES.items()
    }


def _resource_label(cwd: Path, candidates: Iterable[str]) -> str:
    labels = [
        f"{candidate} labels-only"
        for candidate in candidates
        if _resource_source_exists(cwd, candidate)
    ]
    if not labels:
        return "not loaded"
    return ", ".join(labels)


def _resource_source_exists(cwd: Path, candidate: str) -> bool:
    try:
        return (cwd / candidate).exists()
    except OSError:
        return False


def _identity_text(text: str) -> str:
    return text


def _print_wrapped(
    error_stream: TextIO,
    prefix: str,
    text: str,
    *,
    width: int,
    style: Callable[[str], str],
) -> None:
    wrapper = textwrap.TextWrapper(
        width=max(20, width),
        initial_indent=prefix,
        subsequent_indent=" " * len(prefix),
        break_long_words=False,
        break_on_hyphens=False,
    )
    lines = wrapper.wrap(text) or [prefix.rstrip()]
    for line in lines:
        print(style(line), file=error_stream)

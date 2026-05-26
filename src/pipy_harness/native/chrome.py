"""Shared Pi-parity terminal chrome for the native REPL.

Both the bounded no-tool REPL (`NativeNoToolReplSession`) and the
bounded tool-loop REPL (`NativeToolReplSession`) render the same
visual frame: title with a single-space indent, dim controls strip,
loaded `[Context]`/`[Skills]` listings, separator-framed prompt area,
and a two-row persistent bottom status block (cwd + status line).

This module owns the helpers so the same rendering ships from both
REPL surfaces. The styles fall back to plain text when the output
stream is not a TTY or when `NO_COLOR` is set; truecolor codes are
used when `COLORTERM` advertises 24-bit support, otherwise the
16-color fallbacks preserve the same intent.
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

_STARTUP_CHROME_WIDTH_FALLBACK = 88
# Pipy is a separate product from Claude Code, Codex, and the other agent
# CLIs the workspace may also be set up for. The chrome listing only shows
# files pipy actually loads (workspace `AGENTS.md` + pipy-owned home),
# never `~/.claude/CLAUDE.md` or `~/.codex/...` which would conflate
# pipy's product surface with neighbor tools' configs.
_STARTUP_CHROME_RESOURCE_SOURCES: dict[str, tuple[str, ...]] = {
    "context": ("AGENTS.md", "pipy.md", ".pipy/AGENTS.md"),
    "skills": (".pipy/skills",),
    "prompts": (".pipy/commands",),
    "extensions": (".pipy/plugins",),
}
_STARTUP_CHROME_GLOBAL_RESOURCE_SOURCES: dict[str, tuple[str, ...]] = {
    "context": ("~/.pipy/AGENTS.md", "~/AGENTS.md"),
    "skills": ("~/.pipy/skills",),
    "prompts": ("~/.pipy/commands",),
    "extensions": ("~/.pipy/plugins",),
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


def chrome_width(error_stream: TextIO | None) -> int:
    if error_stream is not None and bool(
        getattr(error_stream, "isatty", lambda: False)()
    ):
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


@dataclass(frozen=True, slots=True)
class BottomStatusFields:
    """Inputs for the persistent bottom status line.

    Mirrors the Pi terminal bottom-row content: cwd above, then a
    single status line with cost (or placeholder), plan/subscription
    tag, context usage meter, provider, model, and reasoning effort.
    All fields are pre-sanitized strings so callers control formatting.

    ``attention`` is an optional short tag (e.g. ``"proposal ready"``)
    appended after the model/effort so user-state signals stay visible
    without breaking the Pi-shape layout.
    """

    cwd_label: str
    cost_label: str
    plan_label: str
    context_used_pct: float
    context_budget_label: str
    context_budget_suffix: str
    provider_name: str
    model_id: str
    effort_label: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_reasoning: int = 0
    attention: str = ""


def format_bottom_status_line(width: int, fields: BottomStatusFields) -> str:
    """Render the Pi-shape bottom status line within `width` columns.

    Layout: `[↑in ↓out ]$cost (plan) used%/budget (suffix)` left-aligned,
    `(provider) model • effort` right-aligned with padding in between.
    """

    tokens_prefix = ""
    if fields.tokens_in or fields.tokens_out or fields.tokens_reasoning:
        parts = [
            f"↑{_short_token_count(fields.tokens_in)}",
            f"↓{_short_token_count(fields.tokens_out)}",
        ]
        if fields.tokens_reasoning:
            parts.append(f"R{_short_token_count(fields.tokens_reasoning)}")
        tokens_prefix = " ".join(parts) + " "
    left = (
        f"{tokens_prefix}{fields.cost_label} ({fields.plan_label}) "
        f"{fields.context_used_pct:.1f}%/{fields.context_budget_label}"
    )
    if fields.context_budget_suffix:
        left = f"{left} ({fields.context_budget_suffix})"
    right = f"({fields.provider_name}) {fields.model_id} • {fields.effort_label}"
    if fields.attention:
        right = f"{right} · {fields.attention}"
    return _justify_status_line(left, right, max(20, width))


def _justify_status_line(left: str, right: str, width: int) -> str:
    combined = f"{left} {right}"
    if len(combined) >= width:
        return combined
    padding = width - len(left) - len(right)
    return f"{left}{' ' * padding}{right}"


def _short_token_count(value: int) -> str:
    if value >= 1_000:
        return f"{value / 1000:.1f}k"
    return str(value)


def print_startup_chrome(error_stream: TextIO, *, cwd: Path) -> None:
    """Render the Pi-parity compact startup chrome on `error_stream`.

    Layout: ` pipy v…` title row (one-space indent), dim controls
    strip, blank line, then `[Context]`/`[Skills]`/`[Prompts]`/
    `[Extensions]` listings populated from project-local sources
    under ``cwd`` and global user-home sources (``~/.claude``,
    ``~/.codex``, ``~/.pipy``). Sections with no candidates are omitted.
    """

    style = chrome_style_for(error_stream)
    width = chrome_width(error_stream)
    resource_labels = _resource_labels(cwd)

    # Pi opens with a blank line before the title so the chrome
    # never butts up against the previous shell line.
    print(file=error_stream)
    print(
        f" {style.title(f'pipy v{pipy_version_label()}')}",
        file=error_stream,
    )
    _print_wrapped(
        error_stream,
        " ",
        " · ".join(
            (
                "escape interrupt",
                "ctrl+c/ctrl+d clear/exit",
                "/ commands",
                "! bash",
                "ctrl+o more",
            )
        ),
        width=width,
        style=style.dim,
    )
    _print_wrapped(
        error_stream,
        " ",
        "Press ctrl+o to show full startup help and loaded resources.",
        width=width,
        style=style.dim,
    )
    print(file=error_stream)
    _print_wrapped(
        error_stream,
        " ",
        "Pipy can explain its own features and look up its docs. Ask it how to use or extend pipy.",
        width=width,
        style=style.dim,
    )
    print(file=error_stream)
    print(file=error_stream)
    rendered_section = False
    for section_name, label in (
        ("Context", resource_labels.get("context", "")),
        ("Skills", resource_labels.get("skills", "")),
        ("Prompts", resource_labels.get("prompts", "")),
        ("Extensions", resource_labels.get("extensions", "")),
    ):
        if not label:
            continue
        print(style.section_label(f"[{section_name}]"), file=error_stream)
        _print_wrapped(
            error_stream,
            "  ",
            label,
            width=width,
            style=style.dim,
        )
        print(file=error_stream)
        rendered_section = True
    if rendered_section:
        # Pi emits a second blank line after the last resource block so the
        # input separator is visually distanced from the listing.
        print(file=error_stream)
    if not rendered_section:
        # Keep one blank line after the chrome controls for spacing parity.
        return


def print_input_separator(error_stream: TextIO) -> None:
    style = chrome_style_for(error_stream)
    width = chrome_width(error_stream)
    print(style.separator("─" * width), file=error_stream)


def print_footer_lines(error_stream: TextIO, lines: Iterable[str]) -> None:
    style = chrome_style_for(error_stream)
    for line in lines:
        print(style.dim(line), file=error_stream)


def print_bottom_status_block(
    error_stream: TextIO,
    *,
    cwd_label: str,
    status_line: str,
) -> None:
    """Print the Pi-parity two-row bottom block: cwd, then status line."""

    print_footer_lines(error_stream, (cwd_label, status_line))


def discover_loaded_resource_names(
    cwd: Path,
    category: str,
    *,
    max_items: int = 32,
) -> tuple[str, ...]:
    """Return the short names of the loaded resources for ``category``.

    Skills/prompts/extensions resolve to the child directory names under
    each candidate root (matching Pi's compact list rendering). Context
    resolves to the source label (e.g. ``AGENTS.md``,
    ``~/.claude/CLAUDE.md``).
    """

    names: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    for candidate in _STARTUP_CHROME_RESOURCE_SOURCES.get(category, ()):
        path = cwd / candidate
        for name in _names_for_candidate(path, candidate, category):
            add(name)
            if len(names) >= max_items:
                return tuple(names)
    home = Path.home()
    for candidate in _STARTUP_CHROME_GLOBAL_RESOURCE_SOURCES.get(category, ()):
        if candidate.startswith("~/"):
            path = home / candidate[2:]
            display = candidate
        else:
            path = Path(candidate)
            display = candidate
        for name in _names_for_candidate(path, display, category):
            add(name)
            if len(names) >= max_items:
                return tuple(names)
    return tuple(names)


def _names_for_candidate(path: Path, display: str, category: str) -> Iterable[str]:
    try:
        if not path.exists():
            return ()
    except OSError:
        return ()
    if category == "context":
        return (display,)
    if path.is_dir():
        try:
            entries = sorted(
                child.name
                for child in path.iterdir()
                if child.is_dir() and not child.name.startswith(".")
            )
        except OSError:
            return ()
        return tuple(entries)
    return ()


def _resource_labels(cwd: Path) -> dict[str, str]:
    return {
        category: ", ".join(discover_loaded_resource_names(cwd, category))
        for category in _STARTUP_CHROME_RESOURCE_SOURCES
    }


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

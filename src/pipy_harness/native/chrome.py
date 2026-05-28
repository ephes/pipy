"""Shared Pi-parity terminal chrome for the native REPL.

Both the bounded no-tool REPL (`NativeNoToolReplSession`) and the
bounded tool-loop REPL (`NativeToolReplSession`) render the same
visual frame: title with a single-space indent, dim controls strip,
loaded `[Context]` listing (workspace + ancestor + global
``AGENTS.md`` discovery), separator-framed prompt area, and a two-row
persistent bottom status block (cwd + status line).

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

from pipy_harness.native.workspace_context import (
    INSTRUCTION_CANDIDATE_FILENAMES,
    resolve_global_instruction_root,
)

_STARTUP_CHROME_WIDTH_FALLBACK = 88
# Pipy is a separate product from Claude Code, Codex, and the other agent
# CLIs the workspace may also be set up for. The chrome listing only shows
# files/directories pipy actually owns (workspace `AGENTS.md` +
# pipy-owned `.pipy/` and `~/.pipy/`), never `~/.claude/`, `~/.codex/`,
# or `~/.pi/` which would conflate pipy's product surface with neighbour
# tools' configs.
_STARTUP_CHROME_RESOURCE_SOURCES: dict[str, tuple[str, ...]] = {
    # Workspace context candidates mirror
    # `workspace_context.INSTRUCTION_CANDIDATE_FILENAMES` exactly so
    # the chrome listing never advertises a file the loader would not
    # actually compose into the system prompt, and never silently
    # drops a file the loader would. Sourced from the loader's tuple
    # directly so the two cannot drift on case-sensitive filesystems
    # where e.g. `AGENTS.MD` or `PIPY.md` matter. `.pipy/AGENTS.md`
    # is intentionally NOT listed here even though it is a pipy-owned
    # path, because `discover_workspace_instructions` only inspects
    # the workspace itself (and its ancestors) for the canonical
    # filenames, not a nested `.pipy/` directory.
    "context": INSTRUCTION_CANDIDATE_FILENAMES,
    "skills": (".pipy/skills",),
}
_STARTUP_CHROME_GLOBAL_RESOURCE_SOURCES: dict[str, tuple[str, ...]] = {
    # Global context candidates are NOT a hardcoded list of paths —
    # the loader picks the first matching candidate from
    # `INSTRUCTION_CANDIDATE_FILENAMES` inside the resolved global
    # root (`resolve_global_instruction_root()`), so e.g.
    # `~/.pipy/pipy.md` is composed when `~/.pipy/AGENTS.md` is
    # absent. Chrome mirrors that at runtime in
    # `discover_loaded_resource_names` rather than via this dict.
    # Only directory-style categories (skills, prompts, extensions)
    # are listed statically here. The `~/AGENTS.md` parent-file case
    # is still surfaced — but through `_ancestor_context_labels`,
    # which already walks `cwd.parent` up to filesystem root.
    "skills": ("~/.pipy/skills",),
}

_PI_TITLE_TRUECOLOR = "1;38;2;138;190;183"
_PI_ACCENT_TRUECOLOR = "38;2;138;190;183"
_PI_TITLE_FALLBACK = "1;36"
_PI_ACCENT_FALLBACK = "36"
_PI_SECTION_TRUECOLOR = "38;2;240;198;116"
_PI_SECTION_FALLBACK = "1;33"
_PI_DIM_TRUECOLOR = "38;2;102;102;102"
_PI_DIM_FALLBACK = "2"
_PI_SECONDARY_DIM_TRUECOLOR = "38;2;128;128;128"
_PI_ERROR_TRUECOLOR = "38;2;204;102;102"
_PI_ERROR_FALLBACK = "31"
_PI_USER_MESSAGE_BG_TRUECOLOR = "48;2;52;53;65"
_PI_USER_MESSAGE_TEXT_TRUECOLOR = "38;2;212;212;212"
_PI_TOOL_COMMAND_BG_TRUECOLOR = "48;2;40;50;40"
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

    def secondary_dim(self, text: str) -> str:
        return self._wrap(text, _PI_SECONDARY_DIM_TRUECOLOR, _PI_DIM_FALLBACK)

    def error(self, text: str) -> str:
        return self._wrap(text, _PI_ERROR_TRUECOLOR, _PI_ERROR_FALLBACK)

    def separator(self, text: str) -> str:
        return self._wrap(text, _PI_SEPARATOR_TRUECOLOR, _PI_SEPARATOR_FALLBACK)

    def user_message(self, text: str, *, width: int) -> str:
        if not self.enabled:
            return text
        padded = text + (" " * max(0, width - len(text)))
        if text == "":
            return f"\x1b[{_PI_USER_MESSAGE_BG_TRUECOLOR}m{padded}\x1b[0m"
        return (
            f"\x1b[{_PI_USER_MESSAGE_BG_TRUECOLOR}m"
            f"\x1b[{_PI_USER_MESSAGE_TEXT_TRUECOLOR}m{padded}\x1b[0m"
        )

    def tool_command(self, text: str, *, width: int) -> str:
        if not self.enabled:
            return text
        text_code = _PI_USER_MESSAGE_TEXT_TRUECOLOR if self.truecolor else "37"
        leading = text[: len(text) - len(text.lstrip(" "))]
        visible = text[len(leading) :]
        padding = " " * max(0, width - len(text))
        return (
            f"\x1b[{_PI_TOOL_COMMAND_BG_TRUECOLOR}m"
            f"{leading}\x1b[1;{text_code}m{visible}\x1b[0m"
            f"\x1b[{_PI_TOOL_COMMAND_BG_TRUECOLOR}m{padding}\x1b[0m"
        )

    def tool_result(self, text: str, *, width: int) -> str:
        if not self.enabled:
            return text
        padding = " " * max(0, width - len(text))
        if text == "":
            return f"\x1b[{_PI_TOOL_COMMAND_BG_TRUECOLOR}m{padding}\x1b[0m"
        text_code = _PI_SECONDARY_DIM_TRUECOLOR if self.truecolor else "2"
        return (
            f"\x1b[{_PI_TOOL_COMMAND_BG_TRUECOLOR}m"
            f"\x1b[{text_code}m{text}\x1b[0m"
            f"\x1b[{_PI_TOOL_COMMAND_BG_TRUECOLOR}m{padding}\x1b[0m"
        )

    def tool_read(self, text: str, *, width: int) -> str:
        if not self.enabled:
            return text
        leading = text[: len(text) - len(text.lstrip(" "))]
        visible = text[len(leading) :]
        verb, separator, rest = visible.partition(" ")
        padding = " " * max(0, width - len(text))
        return (
            f"\x1b[{_PI_TOOL_COMMAND_BG_TRUECOLOR}m"
            f"{leading}\x1b[{_PI_TITLE_TRUECOLOR}m{verb}\x1b[0m"
            f"\x1b[{_PI_TOOL_COMMAND_BG_TRUECOLOR}m"
            f"{separator}{rest}{padding}\x1b[0m"
        )

    def menu_row(self, text: str) -> str:
        if not self.enabled:
            return text
        return self.dim(text)

    def menu_selection(self, text: str) -> str:
        if not self.enabled:
            return text
        return self._wrap(text, _PI_ACCENT_TRUECOLOR, _PI_ACCENT_FALLBACK)

    def cursor_cell(self, before: str, cursor: str = " ", after: str = "") -> str:
        if not self.enabled:
            return f"{before}{cursor}{after}"
        return f"\x1b[39m{before}\x1b[7m{cursor}\x1b[0m{after}"

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
    strip, blank line, then a ``[Context]`` listing populated from
    ``AGENTS.md`` files discovered in the workspace, its ancestors,
    and ``~/.pipy/AGENTS.md``. The section is omitted when no
    candidates are found.
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
    section_order = (("Context", "context"), ("Skills", "skills"))
    rendered_any = False
    for label, key in section_order:
        text = resource_labels.get(key, "")
        if not text:
            continue
        if rendered_any:
            # Single blank line between rendered sections, matching pi.
            print(file=error_stream)
        print(style.section_label(f"[{label}]"), file=error_stream)
        _print_wrapped(
            error_stream,
            "  ",
            text,
            width=width,
            style=style.dim,
        )
        rendered_any = True
    if rendered_any:
        print(file=error_stream)
        # Pi emits a second blank line after the last resource block so
        # the input separator is visually distanced from the listing.
        print(file=error_stream)


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
    category: str = "context",
    *,
    max_items: int = 32,
) -> tuple[str, ...]:
    """Return the source labels for the requested ``category``.

    For ``"context"`` (workspace + ancestor + global AGENTS.md files),
    this mirrors `workspace_context.discover_workspace_instructions`
    order: global root first, then ancestor directories root-most
    first, then the workspace itself last. For ``"skills"``, the
    function lists the immediate subdirectory names under each known
    `.pipy/skills` (workspace) and `~/.pipy/skills` (global) store,
    matching pi's compact `[Skills]` rendering.
    """

    names: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    home = Path.home()
    global_sources = _STARTUP_CHROME_GLOBAL_RESOURCE_SOURCES.get(category, ())
    workspace_sources = _STARTUP_CHROME_RESOURCE_SOURCES.get(category, ())

    if category == "context":
        # The loader takes only the first matching candidate per
        # directory (see `workspace_context.discover_workspace_instructions`
        # / `_load_first_candidate`). Chrome mirrors that — without
        # this, a case-insensitive filesystem would surface both
        # `AGENTS.md` and `AGENTS.MD` even though they resolve to the
        # same file and the loader only composes one of them.
        global_label = _global_context_label(home)
        if global_label is not None:
            add(global_label)
        for display in _ancestor_context_labels(cwd):
            add(display)
            if len(names) >= max_items:
                return tuple(names)
        for candidate_name in workspace_sources:
            candidate = cwd / candidate_name
            if not _candidate_resolves_inside(cwd, candidate):
                continue
            add(candidate_name)
            break
        return tuple(names)

    # Directory-style categories (skills, prompts, extensions): list the
    # immediate child directory names found under each known store.
    for source in global_sources:
        global_dir, _display = _global_candidate_path(source, home)
        for entry_name in _directory_entry_names(global_dir):
            add(entry_name)
            if len(names) >= max_items:
                return tuple(names)
    for source in workspace_sources:
        workspace_dir = cwd / source
        for entry_name in _directory_entry_names(workspace_dir):
            add(entry_name)
            if len(names) >= max_items:
                return tuple(names)
    return tuple(names)


def _directory_entry_names(path: Path) -> Iterable[str]:
    try:
        if not path.exists() or not path.is_dir():
            return ()
    except OSError:
        return ()
    try:
        return tuple(
            sorted(
                child.name
                for child in path.iterdir()
                if not child.name.startswith(".")
            )
        )
    except OSError:
        return ()


def _global_candidate_path(candidate: str, home: Path) -> tuple[Path, str]:
    if candidate.startswith("~/"):
        return home / candidate[2:], candidate
    return Path(candidate), candidate


def _global_context_label(home: Path) -> str | None:
    """Return the display label for the loader's global instruction file.

    Mirrors `workspace_context.discover_workspace_instructions`'s
    `_load_first_candidate(global_root, ...)` step exactly: resolves
    the same global root via `resolve_global_instruction_root()` and
    picks the first matching candidate from
    `INSTRUCTION_CANDIDATE_FILENAMES` whose resolved path stays
    inside that root (i.e. symlinks pointing outside are skipped,
    matching the loader's escape-vector defense). The returned
    label uses ``~/...`` notation for paths under ``$HOME`` so the
    chrome listing reads like pi's, and is ``None`` when no
    candidate exists under the global root.
    """

    try:
        global_root = resolve_global_instruction_root(home_dir=home)
    except OSError:
        return None
    for candidate_name in INSTRUCTION_CANDIDATE_FILENAMES:
        candidate = global_root / candidate_name
        if not _candidate_resolves_inside(global_root, candidate):
            continue
        try:
            relative = candidate.relative_to(home)
            return f"~/{relative.as_posix()}"
        except ValueError:
            return str(candidate)
    return None


def _candidate_resolves_inside(directory: Path, candidate: Path) -> bool:
    """Return True iff ``candidate`` is a file whose resolved path stays
    inside ``directory``.

    Mirrors the symlink-escape defense in
    `workspace_context._load_first_candidate`: a candidate that
    points outside its containing directory (e.g.
    ``AGENTS.md -> /etc/secrets``) is skipped by the loader and
    must also be skipped by chrome so the chrome listing never
    advertises a file the loader would silently drop.
    """

    try:
        if not candidate.is_file():
            return False
    except OSError:
        return False
    try:
        resolved_dir = directory.resolve()
    except OSError:
        return False
    try:
        resolved_candidate = candidate.resolve()
    except OSError:
        return False
    try:
        resolved_candidate.relative_to(resolved_dir)
    except ValueError:
        return False
    return True


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _ancestor_context_labels(cwd: Path) -> list[str]:
    """Return display labels for ancestor instruction files.

    Walks from ``cwd.parent`` up to the filesystem root collecting the
    first-matching candidate filename per directory whose resolved
    path stays inside that directory (matching the loader's
    symlink-escape defense), then returns the list in
    **root-most-first** order to match
    `workspace_context.discover_workspace_instructions`. Uses
    ``~/...`` notation for paths under ``$HOME`` to match Pi's compact
    rendering.
    """

    try:
        workspace = cwd.expanduser().resolve()
    except OSError:
        return []
    home = Path.home()
    labels_near_first: list[str] = []
    current = workspace.parent
    while True:
        if current == workspace:
            break
        for candidate_name in INSTRUCTION_CANDIDATE_FILENAMES:
            candidate = current / candidate_name
            if not _candidate_resolves_inside(current, candidate):
                continue
            try:
                relative = candidate.relative_to(home)
                labels_near_first.append(f"~/{relative.as_posix()}")
            except ValueError:
                labels_near_first.append(str(candidate))
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    labels_near_first.reverse()
    return labels_near_first


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

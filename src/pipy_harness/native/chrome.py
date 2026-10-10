"""Shared Pi-parity terminal chrome for the native REPL.

The bounded tool-loop REPL (`CodingSession`) renders this
visual frame: title with a single-space indent, dim controls strip,
loaded `[Context]` listing (workspace + ancestor + global
``AGENTS.md`` discovery), separator-framed prompt area, and a two-row
persistent bottom status block (cwd + status line).

This module owns the helpers so the same rendering ships from the
REPL surface. The styles fall back to plain text when the output
stream is not a TTY or when `NO_COLOR` is set; truecolor codes are
used when the terminal explicitly advertises 24-bit support, otherwise
fallback SGR codes preserve the same intent.
"""

from __future__ import annotations

import functools
import os
import re as _re
import shutil
import textwrap
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TextIO

from pipy_harness.native.agent.history import estimate_context_tokens
from pipy_harness.native.agent.system_messages import current_system_message
from pipy_harness.native.repl_state import (
    NativeModelSelection,
    NativeReplProviderState,
    StaticNativeReplProviderState,
)

if TYPE_CHECKING:
    from pipy_harness.native.coding.state import (
        CodingSessionState,
        CodingSessionUsageSnapshot,
    )
    from pipy_harness.native.package_resources import PackageRoot
    from pipy_harness.native.session_tree import NativeSessionTree
    from pipy_harness.native.ui.components.footer import FooterComponent

from pipy_harness.native.session_usage import format_tokens, to_fixed, usage_totals
from pipy_harness.native.themes import (
    DEFAULT_PALETTE,
    ChromePalette,
    NativeThemeStore,
    resolve_active_theme_name,
    resolve_palette,
)
from pipy_harness.native.workspace_context import (
    INSTRUCTION_CANDIDATE_FILENAMES,
    discover_workspace_instructions,
)

_CHROME_SGR_RE = _re.compile(r"\x1b\[[0-9;]*m")
# A full reset (`\x1b[m`, `\x1b[0m`) or a default-background code (`49`).
_CHROME_FULL_RESET_RE = _re.compile(r"\x1b\[(?:0?|(?:[0-9;]*;)?(?:0|49)(?:;[0-9;]*)?)m")


def _visible_len_no_sgr(text: str) -> int:
    return len(_CHROME_SGR_RE.sub("", text))


_STARTUP_CHROME_WIDTH_FALLBACK = 88
# Startup-chrome resource categories. `context` is computed by the real
# context-file loader (`workspace_context.discover_workspace_instructions`) and
# `skills` by the real skill loader, so the listing can never advertise a file
# the runtime would not load, or hide one it would.
_STARTUP_CHROME_RESOURCE_SOURCES: dict[str, tuple[str, ...]] = {
    "context": INSTRUCTION_CANDIDATE_FILENAMES,
    "skills": (".pipy/skills",),
}
_STARTUP_CHROME_GLOBAL_RESOURCE_SOURCES: dict[str, tuple[str, ...]] = {
    "skills": ("~/.pipy/skills",),
}


@dataclass(frozen=True, slots=True)
class ChromeStyle:
    """Themed color palette for the native REPL chrome.

    The active ``palette`` decides *which* ANSI color codes are emitted; the
    default ``pi`` palette mirrors the reference Pi terminal product (a muted
    sage title, soft-yellow ``[Section]`` labels, flat-gray secondary text, and
    a soft-purple input separator). A user-selected theme swaps in a different
    palette without changing any layout. Captured streams (non-TTY) and
    ``NO_COLOR`` receive the plain text fall-through — ``enabled`` is decided in
    ``chrome_style_for`` before a palette is consulted, so the theme never
    overrides the no-color contract.
    """

    enabled: bool
    truecolor: bool = False
    palette: ChromePalette = DEFAULT_PALETTE

    def title(self, text: str) -> str:
        return self._wrap(
            text, self.palette.title_truecolor, self.palette.title_fallback
        )

    def section_label(self, text: str) -> str:
        return self._wrap(
            text, self.palette.section_truecolor, self.palette.section_fallback
        )

    def dim(self, text: str) -> str:
        return self._wrap(text, self.palette.dim_truecolor, self.palette.dim_fallback)

    def secondary_dim(self, text: str) -> str:
        return self._wrap(
            text,
            self.palette.secondary_dim_truecolor,
            self.palette.secondary_dim_fallback,
        )

    def dim_italic(self, text: str) -> str:
        # Reasoning: italic in Pi's `thinkingText` (secondary dim for themes
        # that set none).
        if not self.enabled:
            return text
        palette = self.palette
        code = self.palette_code(
            palette.thinking_text_truecolor or palette.secondary_dim_truecolor,
            palette.thinking_text_fallback or palette.secondary_dim_fallback,
        )
        return f"\x1b[3;{code}m{text}\x1b[0m"

    def error(self, text: str) -> str:
        return self._wrap(
            text, self.palette.error_truecolor, self.palette.error_fallback
        )

    def warning(self, text: str) -> str:
        return self._wrap(
            text, self.palette.warning_truecolor, self.palette.warning_fallback
        )

    def separator(self, text: str) -> str:
        return self._wrap(
            text, self.palette.separator_truecolor, self.palette.separator_fallback
        )

    def border(self, text: str) -> str:
        """Pi's ``border`` token; ``separator`` when the theme sets none."""

        palette = self.palette
        if palette.border_truecolor is None or palette.border_fallback is None:
            return self.separator(text)
        return self._wrap(text, palette.border_truecolor, palette.border_fallback)

    def accent(self, text: str) -> str:
        return self._wrap(
            text, self.palette.accent_truecolor, self.palette.accent_fallback
        )

    def muted(self, text: str) -> str:
        """Pi's ``muted`` token (pipy's secondary dim)."""

        return self.secondary_dim(text)

    def editor_border(self, text: str, *, level: str, bash_mode: bool) -> str:
        """Pi's editor border: ``bashMode`` for ``!`` input, else the
        thinking level's colour (``getThinkingBorderColor``); ``separator``
        when the theme sets neither."""

        name = "bash_mode" if bash_mode else f"thinking_{level}"
        mode = "truecolor" if self.truecolor else "fallback"
        code = getattr(self.palette, f"{name}_{mode}", None)
        if code is None:
            return self.separator(text)
        return self._wrap(text, code, code)

    def user_message(self, text: str, *, width: int) -> str:
        if not self.enabled:
            return text
        bg = self.palette_code(
            self.palette.user_message_bg_truecolor,
            self.palette.user_message_bg_fallback,
        )
        padded = text + (" " * max(0, width - len(text)))
        if text == "":
            return f"\x1b[{bg}m{padded}\x1b[0m"
        fg = self.palette_code(
            self.palette.user_message_text_truecolor,
            self.palette.user_message_text_fallback,
        )
        return f"\x1b[{bg}m\x1b[{fg}m{padded}\x1b[0m"

    def tool_command(self, text: str, *, width: int) -> str:
        if not self.enabled:
            return text
        bg = self.palette_code(
            self.palette.tool_command_bg_truecolor,
            self.palette.tool_command_bg_fallback,
        )
        text_code = self.palette.user_message_text_truecolor if self.truecolor else "37"
        leading = text[: len(text) - len(text.lstrip(" "))]
        visible = text[len(leading) :]
        padding = " " * max(0, width - len(text))
        return (
            f"\x1b[{bg}m"
            f"{leading}\x1b[1;{text_code}m{visible}\x1b[0m"
            f"\x1b[{bg}m{padding}\x1b[0m"
        )

    def tool_result(self, text: str, *, width: int) -> str:
        if not self.enabled:
            return text
        bg = self.palette_code(
            self.palette.tool_command_bg_truecolor,
            self.palette.tool_command_bg_fallback,
        )
        padding = " " * max(0, width - len(text))
        if text == "":
            return f"\x1b[{bg}m{padding}\x1b[0m"
        text_code = self.palette.secondary_dim_truecolor if self.truecolor else "2"
        return f"\x1b[{bg}m\x1b[{text_code}m{text}\x1b[0m\x1b[{bg}m{padding}\x1b[0m"

    def palette_code(self, truecolor_code: str, fallback_code: str) -> str:
        """Pick the truecolor vs fallback SGR parameter for this style."""
        return truecolor_code if self.truecolor else fallback_code

    def tool_custom(self, text: str, *, width: int) -> str:
        """Band-only framing for extension-rendered tool rows.

        Applies the tool background band + right padding but imposes NO
        foreground color, so the renderer's own SGR is preserved. When color
        is disabled the renderer already produced plain text, so pass it
        through unchanged."""
        if not self.enabled:
            return text
        bg = self.palette_code(
            self.palette.tool_command_bg_truecolor,
            self.palette.tool_command_bg_fallback,
        )
        visible = _visible_len_no_sgr(text)
        padding = " " * max(0, width - visible)
        if visible == 0:
            return f"\x1b[{bg}m{padding}\x1b[0m"
        return f"\x1b[{bg}m{text}\x1b[0m\x1b[{bg}m{padding}\x1b[0m"

    def tool_box(self, text: str, *, bg: str, width: int) -> str:
        """One row of a Pi tool box: pre-styled ``text`` on its background.

        ``bg`` is ``pending``/``success``/``error`` (Pi ``toolPendingBg`` /
        ``toolSuccessBg`` / ``toolErrorBg``), ``custom`` (``customMessageBg``,
        the summary rows) or ``none`` (edit's result lines below its box).
        The row is padded to ``width``; a full SGR reset inside
        the text re-opens the background, like Pi's ``applyBackgroundToLine``
        keeps the box colour behind every cell.
        """

        if not self.enabled:
            return text
        visible = _visible_len_no_sgr(text)
        padding = " " * max(0, width - visible)
        if bg == "none":
            return f"{text}\x1b[0m{padding}"
        palette = self.palette
        truecolor, fallback = {
            "pending": (
                palette.tool_pending_bg_truecolor,
                palette.tool_pending_bg_fallback,
            ),
            "success": (
                palette.tool_success_bg_truecolor,
                palette.tool_success_bg_fallback,
            ),
            "error": (palette.tool_error_bg_truecolor, palette.tool_error_bg_fallback),
            "custom": (
                palette.custom_message_bg_truecolor,
                palette.custom_message_bg_fallback,
            ),
        }.get(bg, (palette.tool_pending_bg_truecolor, palette.tool_pending_bg_fallback))
        code = f"\x1b[{self.palette_code(truecolor, fallback)}m"
        body = _CHROME_FULL_RESET_RE.sub(lambda match: match.group(0) + code, text)
        return f"{code}{body}{padding}\x1b[0m"

    def menu_row(self, text: str) -> str:
        if not self.enabled:
            return text
        return self.dim(text)

    def menu_selection(self, text: str) -> str:
        if not self.enabled:
            return text
        return self._wrap(
            text, self.palette.accent_truecolor, self.palette.accent_fallback
        )

    def cursor_cell(self, before: str, cursor: str = " ", after: str = "") -> str:
        if not self.enabled:
            return f"{before}{cursor}{after}"
        return f"\x1b[39m{before}\x1b[7m{cursor}\x1b[0m{after}"

    def _wrap(self, text: str, truecolor_code: str, fallback_code: str) -> str:
        if not self.enabled:
            return text
        code = truecolor_code if self.truecolor else fallback_code
        return f"\x1b[{code}m{text}\x1b[0m"


def chrome_style_for(
    error_stream: TextIO, *, theme_name: str | None = None
) -> ChromeStyle:
    """Build a ``ChromeStyle`` for ``error_stream`` honoring the active theme.

    When ``theme_name`` is None the theme is resolved from the ambient
    environment (``PIPY_THEME``) plus the persisted store, exactly like the
    other chrome inputs (``NO_COLOR``/``TERM``) are read per render. The
    NO_COLOR / non-TTY fallback is decided first and always wins: a disabled
    style emits plain text no matter which palette is selected.
    """

    is_tty = bool(getattr(error_stream, "isatty", lambda: False)())
    term = os.environ.get("TERM", "")
    enabled = is_tty and "NO_COLOR" not in os.environ and term.lower() != "dumb"
    colorterm = os.environ.get("COLORTERM", "").lower()
    truecolor = enabled and terminal_supports_truecolor(term, colorterm)
    resolved = (
        theme_name
        if theme_name is not None
        else resolve_active_theme_name(store=NativeThemeStore())
    )
    return ChromeStyle(
        enabled=enabled, truecolor=truecolor, palette=resolve_palette(resolved)
    )


def terminal_supports_truecolor(term: str, colorterm: str) -> bool:
    """Return whether the terminal explicitly advertises RGB SGR support."""

    normalized_term = term.lower()
    normalized_colorterm = colorterm.lower()
    return normalized_colorterm in {"truecolor", "24bit"} or any(
        marker in normalized_term for marker in ("truecolor", "24bit", "direct")
    )


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
class _ContextBudget:
    """Approximate provider/model context-window budget for the meter.

    ``token_budget`` is the absolute denominator; ``budget_label`` is the
    short label rendered into the bottom status (e.g. ``272k`` for the
    272 000-token GPT-5.5 context).
    """

    token_budget: int
    budget_label: str


_DEFAULT_CONTEXT_BUDGET = _ContextBudget(token_budget=128_000, budget_label="128k")


def _format_footer_tokens(count: int) -> str:
    """Pi footer ``formatTokens`` (footer.ts:25-31): 272000 -> 272k, 1e6 -> 1.0M."""

    if count < 1_000:
        return str(count)
    if count < 10_000:
        return f"{count / 1_000:.1f}k"
    if count < 1_000_000:
        return f"{round(count / 1_000)}k"
    if count < 10_000_000:
        return f"{count / 1_000_000:.1f}M"
    return f"{round(count / 1_000_000)}M"


@functools.lru_cache(maxsize=1)
def _builtin_context_windows() -> dict[tuple[str, str], int]:
    from pipy_harness.native.catalog import build_builtin_catalog

    return {
        (row.provider_name, row.model_id): row.context_window
        for row in build_builtin_catalog().get_all()
    }


@functools.lru_cache(maxsize=1)
def _builtin_reasoning_rows() -> frozenset[tuple[str, str]]:
    from pipy_harness.native.catalog import build_builtin_catalog

    return frozenset(
        (row.provider_name, row.model_id)
        for row in build_builtin_catalog().get_all()
        if row.reasoning
    )


def thinking_footer_label(*, reasoning: bool, level: str | None) -> str:
    """Pi footer thinking segment (``footer.ts:233-239``).

    A non-reasoning model shows no segment (``""``); a reasoning model shows
    ``thinking off`` for an off/unset level and the level name otherwise.
    """

    if not reasoning:
        return ""
    if not level or level == "off":
        return "thinking off"
    return level


def _context_budget_for(
    provider_name: str,
    model_id: str,
    *,
    declared_window: int | None = None,
) -> _ContextBudget:
    """Return the context-window budget and label for the bottom status.

    Like Pi's footer (``state.model.contextWindow``), the denominator is the
    active model's declared context window: ``declared_window`` is the resolved
    catalog row the session uses (built-in plus ``models.json`` overrides). A
    caller without a resolved row falls back to the built-in row, so a catalog
    refresh (e.g. Codex GPT-5.6 Sol at 272K, Claude 5.x at 1M) still reaches the
    meter. A selection with neither falls back to the safe 128k default so the
    meter still renders; authoritative provider usage telemetry is a separate
    follow-up.
    """

    window = (
        declared_window
        if declared_window is not None
        else _builtin_context_windows().get((provider_name, model_id))
    )
    if window is None or window <= 0:
        return _DEFAULT_CONTEXT_BUDGET
    return _ContextBudget(
        token_budget=window, budget_label=_format_footer_tokens(window)
    )


def _friendly_cwd_label(cwd: Path) -> str:
    """Render ``cwd`` as ``~/<rel> (branch)`` when inside the user's home.

    Falls back to the absolute path when ``cwd`` is outside ``~`` or
    when the home directory cannot be resolved. The ``(branch)`` suffix
    is appended when ``cwd`` (or any parent up to the home directory)
    contains a ``.git`` directory whose ``HEAD`` can be read.
    """

    label = str(cwd)
    try:
        home = Path.home()
    except RuntimeError:
        home = None
    if home is not None:
        try:
            relative = cwd.resolve().relative_to(home.resolve())
            relative_str = relative.as_posix()
            label = "~" if relative_str in {"", "."} else f"~/{relative_str}"
        except ValueError:
            pass
    branch = _detect_git_branch(cwd)
    if branch:
        label = f"{label} ({branch})"
    return label


def _detect_git_branch(cwd: Path) -> str | None:
    """Walk up from ``cwd`` looking for ``.git/HEAD`` and return the branch."""

    candidate: Path | None = cwd
    while candidate is not None and candidate != candidate.parent:
        head = candidate / ".git" / "HEAD"
        try:
            text = head.read_text(encoding="utf-8")
        except OSError:
            candidate = candidate.parent
            continue
        text = text.strip()
        if text.startswith("ref: refs/heads/"):
            return text.split("refs/heads/", 1)[1]
        if text:
            return text[:7]
        return None
    return None


@dataclass(frozen=True, slots=True)
class BottomStatusFields:
    """Inputs for the persistent bottom status line.

    Mirrors the Pi terminal bottom-row content: cwd above, then a
    single status line with the session cost (Pi ``footer.ts:189-196``: shown
    when non-zero or on a subscription, marked `` (sub)`` then), context usage
    meter, provider, model, and reasoning effort. String fields are
    pre-sanitized so callers control formatting.

    ``attention`` is an optional short tag (e.g. ``"proposal ready"``)
    appended after the model/effort so user-state signals stay visible
    without breaking the Pi-shape layout.
    """

    cwd_label: str
    cost_usd: float
    using_subscription: bool
    context_used_pct: float
    context_budget_label: str
    context_budget_suffix: str
    provider_name: str
    model_id: str
    effort_label: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache_read: int = 0
    tokens_cache_write: int = 0
    cache_hit_percent: float | None = None
    attention: str = ""


class _ReplRuntime(Protocol):
    runtime_label: str


@dataclass(slots=True)
class _EditorBorderLevel:
    """The thinking level the editor border shows, set with the footer text.

    The paint reads it without taking the provider state's lock.
    """

    level: str = "off"


@dataclass(frozen=True, slots=True, kw_only=True)
class _ChromeFooterEffects:
    """Own footer composition and project live coding state into terminal chrome."""

    cwd: Path
    coding_state: CodingSessionState
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None
    error_stream: TextIO
    footer: FooterComponent | None
    repl_runtime: _ReplRuntime
    # The live session tree (it is replaced by /new, /resume and /fork).
    session_tree_section: Callable[[], AbstractContextManager[NativeSessionTree]]
    border: _EditorBorderLevel = field(default_factory=_EditorBorderLevel)

    def border_level(self) -> str:
        """Pi ``updateEditorBorderColor``'s level (``thinkingLevel || "off"``)."""

        return self.border.level

    def _thinking_level(self, provider_name: str, model_id: str) -> str:
        """The live level, ``off`` for a model without reasoning (Pi clamps)."""

        state = self.provider_state
        if not isinstance(state, NativeReplProviderState):
            return "off"
        spec = state.model_runtime.resolve_spec(
            NativeModelSelection(provider_name, model_id)
        )
        if spec is None or not spec.reasoning:
            return "off"
        return state.current_thinking_level() or "off"

    def _declared_context_window(self, provider_name: str, model_id: str) -> int | None:
        """The resolved catalog row's declared window (models.json aware)."""

        state = self.provider_state
        if not isinstance(state, NativeReplProviderState):
            return None
        spec = state.model_runtime.resolve_spec(
            NativeModelSelection(provider_name, model_id)
        )
        return spec.declared_context_window if spec is not None else None

    def _effort_label(self, provider_name: str, model_id: str) -> str:
        """The footer thinking segment from the resolved row and live level.

        Reasoning support comes from the row the session resolves (built-in
        plus ``models.json``), the level from the live provider state, which is
        also what the bound provider was constructed with. A state without a
        catalog (an injected provider) falls back to the built-in row and has
        no live level, so a reasoning row reads ``thinking off``.
        """

        state = self.provider_state
        if isinstance(state, NativeReplProviderState):
            spec = state.model_runtime.resolve_spec(
                NativeModelSelection(provider_name, model_id)
            )
            return thinking_footer_label(
                reasoning=spec is not None and bool(spec.reasoning),
                level=state.current_thinking_level(),
            )
        return thinking_footer_label(
            reasoning=(provider_name, model_id) in _builtin_reasoning_rows(),
            level=None,
        )

    def _using_subscription(self, provider_name: str) -> bool:
        """Pi footer ``usingSubscription``: kimi-coding or a subscription login.

        A state without a catalog (an injected provider) has no stored login.
        """

        if provider_name == "kimi-coding":
            return True
        state = self.provider_state
        if isinstance(state, NativeReplProviderState):
            return state.is_using_subscription(provider_name)
        return False

    def _footer_text(
        self,
        *,
        cwd: Path,
        error_stream: TextIO | None = None,
    ) -> str:
        with self.session_tree_section() as tree:
            with self.coding_state.state_lock:
                context = self.coding_state.result_snapshot()
                system = current_system_message(
                    message for _, message in tree.build_coding_context().system_anchors
                )
                totals = usage_totals(tree.get_entries())
                provider_name, model_id = context.provider_name, context.model_id
                budget = _context_budget_for(
                    provider_name,
                    model_id,
                    declared_window=self._declared_context_window(
                        provider_name, model_id
                    ),
                )
        tokens = estimate_context_tokens(
            context.messages,
            trust_usage=not context.compaction_suffix,
            system=system,
        ) + -(-len(context.compaction_suffix) // 4)
        used_pct = self._context_used_pct(budget=budget, tokens=tokens)
        # Pi footer: totals over every stored assistant message of the
        # session, so they survive resume and a model switch.
        fields = BottomStatusFields(
            cwd_label="",
            cost_usd=totals.cost,
            using_subscription=self._using_subscription(provider_name),
            context_used_pct=used_pct,
            context_budget_label=budget.budget_label,
            context_budget_suffix="auto",
            provider_name=provider_name,
            model_id=model_id,
            effort_label=self._effort_label(provider_name, model_id),
            tokens_in=totals.input,
            tokens_out=totals.output,
            tokens_cache_read=totals.cache_read,
            tokens_cache_write=totals.cache_write,
            cache_hit_percent=totals.latest_cache_hit_rate,
        )
        status_line = format_bottom_status_line(
            max(20, chrome_width(error_stream)), fields
        )
        return f"{_friendly_cwd_label(cwd)}\n{status_line}"

    def _context_used_pct(self, *, budget: _ContextBudget, tokens: int) -> float:
        if budget.token_budget <= 0:
            return 0.0
        return min(100.0 * tokens / float(budget.token_budget), 999.9)

    def _print_footer(
        self,
        error_stream: TextIO,
        *,
        cwd: Path,
        provider_name: str,
        model_id: str,
        user_turn_count: int,
        tool_invocation_count: int,
        usage_snapshot: CodingSessionUsageSnapshot | None = None,
    ) -> None:
        print_input_separator(error_stream)
        footer = self._footer_text(
            cwd=cwd,
            error_stream=error_stream,
        )
        cwd_label, _, status_line = footer.partition("\n")
        print_bottom_status_block(
            error_stream, cwd_label=cwd_label, status_line=status_line
        )

    def coding_footer_text(self) -> str:
        coding_state = self.coding_state
        self.border.level = self._thinking_level(
            coding_state.provider_name, coding_state.model_id
        )
        return self._footer_text(cwd=self.cwd, error_stream=self.error_stream)

    def refresh_footer_text(self) -> None:
        if self.footer is not None:
            self.footer.set_builtin_text(self.coding_footer_text())

    def legacy_footer_enabled(self) -> bool:
        return self.footer is None and self.repl_runtime.runtime_label != "slash-menu"

    def refresh_legacy_footer(self) -> None:
        if self.legacy_footer_enabled():
            coding_state = self.coding_state
            self._print_footer(
                self.error_stream,
                cwd=self.cwd,
                provider_name=coding_state.provider_name,
                model_id=coding_state.model_id,
                user_turn_count=coding_state.user_turn_count,
                tool_invocation_count=coding_state.tool_invocation_count,
            )

    def refresh_legacy_footer_with_usage(self) -> None:
        if self.legacy_footer_enabled():
            coding_state = self.coding_state
            self._print_footer(
                self.error_stream,
                cwd=self.cwd,
                provider_name=coding_state.provider_name,
                model_id=coding_state.model_id,
                user_turn_count=coding_state.user_turn_count,
                tool_invocation_count=coding_state.tool_invocation_count,
                usage_snapshot=coding_state.usage_snapshot(),
            )


def format_bottom_status_line(width: int, fields: BottomStatusFields) -> str:
    """Render the Pi-shape bottom status line within `width` columns.

    Layout: `[↑in ↓out ][$cost[ (sub)] ]used%/budget (suffix)` left-aligned,
    `(provider) model[ • thinking]` right-aligned with padding in between; the
    thinking segment is omitted when ``effort_label`` is empty (a non-reasoning
    model, as in Pi's footer). The cost segment appears, as in Pi, only when the
    cost is non-zero or the provider is used through a subscription.
    """

    # Pi footer.ts: each count only when non-zero, then the latest message's
    # cache hit rate when the session has cache activity.
    parts: list[str] = []
    if fields.tokens_in:
        parts.append(f"↑{format_tokens(fields.tokens_in)}")
    if fields.tokens_out:
        parts.append(f"↓{format_tokens(fields.tokens_out)}")
    if fields.tokens_cache_read:
        parts.append(f"R{format_tokens(fields.tokens_cache_read)}")
    if fields.tokens_cache_write:
        parts.append(f"W{format_tokens(fields.tokens_cache_write)}")
    if (
        fields.tokens_cache_read or fields.tokens_cache_write
    ) and fields.cache_hit_percent is not None:
        parts.append(f"CH{to_fixed(fields.cache_hit_percent, 1)}%")
    tokens_prefix = " ".join(parts) + " " if parts else ""
    cost_prefix = ""
    if fields.cost_usd or fields.using_subscription:
        sub = " (sub)" if fields.using_subscription else ""
        cost_prefix = f"${to_fixed(fields.cost_usd, 3)}{sub} "
    left = (
        f"{tokens_prefix}{cost_prefix}"
        f"{fields.context_used_pct:.1f}%/{fields.context_budget_label}"
    )
    if fields.context_budget_suffix:
        left = f"{left} ({fields.context_budget_suffix})"
    right = f"({fields.provider_name}) {fields.model_id}"
    if fields.effort_label:
        right = f"{right} • {fields.effort_label}"
    if fields.attention:
        right = f"{right} · {fields.attention}"
    return _justify_status_line(left, right, max(20, width))


def _justify_status_line(left: str, right: str, width: int) -> str:
    combined = f"{left} {right}"
    if len(combined) >= width:
        return combined
    padding = width - len(left) - len(right)
    return f"{left}{' ' * padding}{right}"


def print_startup_chrome(
    error_stream: TextIO,
    *,
    cwd: Path,
    quiet: bool | str = False,
    include_workspace_defaults: bool = False,
) -> None:
    """Render the Pi-parity compact startup chrome on `error_stream`.

    Layout: ` pipy v…` title row (one-space indent), dim controls
    strip, blank line, then a ``[Context]`` listing populated from
    ``AGENTS.md`` files discovered in the workspace, its ancestors,
    and ``~/.pipy/AGENTS.md``. The section is omitted when no
    candidates are found.

    Workspace resource sections are fail-closed by default. Product startup
    passes ``include_workspace_defaults=True`` only after resolving project
    trust; global resources and trust-exempt context remain visible otherwise.

    When ``quiet`` is set (the ``quietStartup`` setting), the verbose startup
    banner is suppressed entirely. ``"header"`` keeps version and key hints.
    """

    if quiet is True:
        return
    style = chrome_style_for(error_stream)
    width = chrome_width(error_stream)
    resource_labels = _resource_labels(
        cwd, include_workspace_defaults=include_workspace_defaults
    )

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
    if quiet == "header":
        print(file=error_stream)
        return
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


def _startup_skill_settings(
    cwd: Path,
    *,
    project_trusted: bool,
) -> tuple[tuple["PackageRoot", ...], tuple[str, ...], bool]:
    """Inputs for the honest `[Skills]` listing: package roots + enablement.

    Returns ``(package_skill_roots, skills_patterns, enable_skill_commands)``
    so the listing matches what a session actually registers (the same
    `+/-pattern` filters and `enableSkillCommands` toggle). Self-contained
    and fail-safe: the startup chrome must never crash on a settings/package
    error, so any failure yields ``((), (), True)`` (list everything, the
    prior behavior).
    """

    try:
        from pipy_harness.native.package_resources import resolve_package_roots
        from pipy_harness.native.settings import SettingsManager

        settings = SettingsManager.for_workspace(cwd, project_trusted=project_trusted)
        roots = resolve_package_roots(settings.get_package_entries(), cwd).skills
        return (
            roots,
            tuple(settings.get_skills_patterns()),
            settings.get_enable_skill_commands(),
        )
    except Exception:  # noqa: BLE001 - unreadable settings degrade to defaults
        return (), (), True


def discover_loaded_resource_names(
    cwd: Path,
    category: str = "context",
    *,
    max_items: int = 32,
    include_workspace_defaults: bool = False,
) -> tuple[str, ...]:
    """Return the source labels for the requested ``category``.

    For ``"context"`` (workspace + ancestor + global AGENTS.md files),
    this mirrors `workspace_context.discover_workspace_instructions`
    order: global root first, then ancestor directories root-most
    first, then the workspace itself last. For ``"skills"``, the function uses
    the real skill loader and enablement filters, so it returns only names the
    current session can load. Other resource categories list immediate entries
    from their known stores. Workspace resource stores are excluded unless
    ``include_workspace_defaults`` is true; trust-exempt context discovery is
    unaffected.
    """

    home = Path.home()
    global_sources = _STARTUP_CHROME_GLOBAL_RESOURCE_SOURCES.get(category, ())
    workspace_sources = _STARTUP_CHROME_RESOURCE_SOURCES.get(category, ())
    if category == "context":
        return _discover_context_resource_names(
            cwd,
            home=home,
            max_items=max_items,
        )
    if category == "skills":
        return _discover_skill_resource_names(
            cwd,
            max_items=max_items,
            include_workspace_defaults=include_workspace_defaults,
        )
    return _discover_directory_resource_names(
        cwd,
        home=home,
        global_sources=global_sources,
        workspace_sources=workspace_sources,
        max_items=max_items,
        include_workspace_defaults=include_workspace_defaults,
    )


def _add_resource_name(names: list[str], seen: set[str], name: str) -> None:
    if name and name not in seen:
        seen.add(name)
        names.append(name)


def _discover_context_resource_names(
    cwd: Path,
    *,
    home: Path,
    max_items: int,
) -> tuple[str, ...]:
    """List the context files the runtime loader composes, in its order.

    Runs the real loader (global root, ancestor walk, worktree shadowing,
    dedup and the symlink guard) and formats each file like Pi's
    `formatContextPath`: cwd-relative when inside cwd, else `~`-abbreviated.
    """

    names: list[str] = []
    seen: set[str] = set()
    try:
        discovery = discover_workspace_instructions(cwd, home_dir=home)
    except OSError:
        return ()
    for entry in discovery.instructions:
        if not entry.sha256:
            continue  # the synthetic total-byte-cap marker is not a file
        _add_resource_name(
            names, seen, _format_context_path(entry.absolute_path, cwd, home)
        )
        if len(names) >= max_items:
            break
    return tuple(names)


def _format_context_path(path: str, cwd: Path, home: Path) -> str:
    """Pi `formatContextPath`: cwd-relative inside cwd, else `~` display form."""

    absolute_cwd = os.path.abspath(cwd)
    absolute_path = os.path.abspath(path)
    relative = os.path.relpath(absolute_path, absolute_cwd)
    if relative != os.pardir and not relative.startswith(f"{os.pardir}{os.sep}"):
        return Path(relative).as_posix()
    home_text = str(home)
    if absolute_path.startswith(home_text):
        return f"~{absolute_path[len(home_text) :]}"
    return absolute_path


def _discover_skill_resource_names(
    cwd: Path,
    *,
    max_items: int,
    include_workspace_defaults: bool,
) -> tuple[str, ...]:
    # Use the same loader and enablement filters as `/skill <name>`.
    from pipy_harness.native.resource_enablement import is_resource_enabled
    from pipy_harness.native.skills import discover_workspace_skills

    names: list[str] = []
    seen: set[str] = set()
    package_roots, skills_patterns, enable_skill_commands = _startup_skill_settings(
        cwd, project_trusted=include_workspace_defaults
    )
    if not enable_skill_commands:
        return tuple(names)
    try:
        skills, _ = discover_workspace_skills(
            cwd,
            package_roots=package_roots,
            include_workspace_defaults=include_workspace_defaults,
        )
    except OSError:
        return tuple(names)
    for skill in skills:
        if skills_patterns and not is_resource_enabled(
            skill.name, list(skills_patterns)
        ):
            continue
        _add_resource_name(names, seen, skill.name)
        if len(names) >= max_items:
            break
    return tuple(names)


def _discover_directory_resource_names(
    cwd: Path,
    *,
    home: Path,
    global_sources: tuple[str, ...],
    workspace_sources: tuple[str, ...],
    max_items: int,
    include_workspace_defaults: bool,
) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()

    for source in global_sources:
        global_dir, _display = _global_candidate_path(source, home)
        for entry_name in _directory_entry_names(global_dir):
            _add_resource_name(names, seen, entry_name)
            if len(names) >= max_items:
                return tuple(names)
    if include_workspace_defaults:
        for source in workspace_sources:
            workspace_dir = cwd / source
            for entry_name in _directory_entry_names(workspace_dir):
                _add_resource_name(names, seen, entry_name)
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
                child.name for child in path.iterdir() if not child.name.startswith(".")
            )
        )
    except OSError:
        return ()


def _global_candidate_path(candidate: str, home: Path) -> tuple[Path, str]:
    if candidate.startswith("~/"):
        return home / candidate[2:], candidate
    return Path(candidate), candidate


def _resource_labels(
    cwd: Path, *, include_workspace_defaults: bool = False
) -> dict[str, str]:
    return {
        category: ", ".join(
            discover_loaded_resource_names(
                cwd,
                category,
                include_workspace_defaults=include_workspace_defaults,
            )
        )
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

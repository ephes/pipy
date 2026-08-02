"""Pure frame composition for the native inline-scrollback TUI.

The renderer consumes frozen snapshots and returns immutable frame rows or a
terminal paint plan.  It performs no terminal inspection or writes, acquires no
locks, invokes no extension/component callbacks, and mutates no owner state.
The TUI facade resolves effectful extension/custom-component regions before it
constructs a snapshot; ``TerminalDriver`` remains the only byte sink.
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.session_tree_commands import sanitize_label_text

_DEFAULT_HISTORY_VIEW_LINES = 21
_TOOL_PANEL_HISTORY_VIEW_LINES = 23
_TOOL_STREAM_LIVE_LINES = 12
_OVERFLOW_BOTTOM_GUTTER_LINES = 2
_OVERFLOW_CONTEXT_TARGET_LINES = 13
_OVERFLOW_CONTEXT_MIN_LINES = 4
_MIN_INPUT_ROWS = 1
_INPUT_NEWLINE_GLYPH = "⏎"
_PENDING_TOOL_KINDS = frozenset({"tool", "tool_read", "tool_result"})
_CUSTOM_BLOCK_KINDS = frozenset(
    {"tool_call_custom", "tool_result_custom", "custom_message_custom"}
)
_SAFE_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True, slots=True)
class FrameLine:
    """One immutable logical terminal row plus optional cursor/style metadata."""

    text: str
    kind: str = "normal"
    meta: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        if self.meta is not None:
            object.__setattr__(self, "meta", MappingProxyType(dict(self.meta)))


class ResolvedCustomEditorLine(FrameLine):
    """A custom-editor row already sanitized and clipped by the TUI facade.

    This immutable subtype is the explicit hand-off marker that prevents full-
    frame finishing from applying the ordinary sanitization/clipping pass a
    second time. It adds no state and retains ``FrameLine`` kind/metadata.
    """

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class FrameBlock:
    """Immutable raw history block; wrapping is owned by this renderer."""

    kind: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InputSnapshot:
    """Resolved editor values used for pure wrapping and cursor placement.

    ``custom_rows`` is present only for an effectful extension editor. Its
    component has already rendered, sanitized, clipped, and assigned metadata
    in the facade; this renderer only applies the viewport row window while
    preserving those resolved immutable rows exactly.
    """

    text: str
    cursor: int
    custom_rows: tuple[ResolvedCustomEditorLine, ...] | None = None


@dataclass(frozen=True, slots=True)
class ChromeSnapshot:
    """Resolved immutable chrome rows prepared by the effectful facade."""

    header: tuple[FrameLine, ...] = ()
    above: tuple[FrameLine, ...] = ()
    below: tuple[FrameLine, ...] = ()
    footer: tuple[FrameLine, ...] = ()
    status: tuple[FrameLine, ...] = ()


@dataclass(frozen=True, slots=True)
class FrameSnapshot:
    """All immutable values required to compose one full/live frame."""

    width: int
    height: int
    history: tuple[FrameBlock, ...]
    assistant_text: str
    reasoning_text: str
    tool_output_text: str
    working_text: str
    thinking_hidden: bool
    hidden_thinking_label: str
    tools_expanded: bool
    input: InputSnapshot
    popup: tuple[FrameLine, ...]
    pending: tuple[FrameLine, ...]
    chrome: ChromeSnapshot
    overlay: tuple[FrameLine, ...] | None
    cursor_visible: bool


@dataclass(frozen=True, slots=True)
class PaintState:
    """Physical inline-region bookkeeping captured before one paint."""

    painted_block_count: int
    live_height: int
    live_input_row: int


@dataclass(frozen=True, slots=True)
class PaintRow:
    """One styled row plus whether the terminal must erase its stale tail."""

    text: str
    erase_tail: bool


@dataclass(frozen=True, slots=True)
class PaintPlan:
    """Logical terminal operations and post-attempt bookkeeping for one paint.

    The plan deliberately contains no physical cursor-control bytes. The
    effectful terminal driver serializes these values to ANSI and writes them.
    """

    prior_live_height: int
    prior_live_input_row: int
    committed_rows: tuple[PaintRow, ...]
    live_rows: tuple[PaintRow, ...]
    cursor_lines_up: int
    cursor_col: int
    cursor_visible: bool
    painted_block_count: int
    live_height: int
    live_input_row: int
    painted_size: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _LayoutRegions:
    menu: tuple[FrameLine, ...]
    pending: tuple[FrameLine, ...]
    status: tuple[FrameLine, ...]
    header: tuple[FrameLine, ...]
    above: tuple[FrameLine, ...]
    below: tuple[FrameLine, ...]
    footer: tuple[FrameLine, ...]
    input_rows: tuple[FrameLine, ...]


# ---------------------------------------------------------------------------
# Safe clipping and immutable line/block projection.


def sanitize_custom_text(text: str) -> str:
    """Sanitize extension text while preserving simple SGR styling."""

    raw = str(text)
    cleaned: list[str] = []
    index = 0
    while index < len(raw):
        match = _SAFE_SGR_RE.match(raw, index)
        if match is not None:
            cleaned.append(match.group(0))
            index = match.end()
            continue
        ch = raw[index]
        code = ord(ch)
        cleaned.append(
            " " if code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F else ch
        )
        index += 1
    return "".join(cleaned)


def visible_len(text: str) -> int:
    return len(_SAFE_SGR_RE.sub("", text))


def clip_custom_text(text: str, width: int) -> str:
    """Clip text by visible width while retaining only safe SGR sequences."""

    safe = sanitize_custom_text(text)
    if width <= 0:
        return ""
    if visible_len(safe) <= width:
        return safe
    if width <= 1:
        return "…"
    return _clip_sgr_prefix(safe, width - 1) + "…"


def _clip_sgr_prefix(text: str, target: int) -> str:
    visible = 0
    clipped: list[str] = []
    index = 0
    while index < len(text) and visible < target:
        match = _SAFE_SGR_RE.match(text, index)
        if match is not None:
            clipped.append(match.group(0))
            index = match.end()
        else:
            clipped.append(text[index])
            visible += 1
            index += 1
    return "".join(clipped)


def clip_text(text: str, width: int) -> str:
    safe = sanitize_label_text(str(text))
    if width <= 0:
        return ""
    if len(safe) <= width:
        return safe
    if width == 1:
        return safe[:1]
    return safe[: width - 1] + "…"


def pad_text(text: str, width: int) -> str:
    length = visible_len(text)
    if length >= width:
        return clip_custom_text(text, width)
    return text + (" " * (width - length))


def block_lines(block: FrameBlock, width: int) -> tuple[FrameLine, ...]:
    """Wrap and frame one raw block without observing or mutating UI state."""

    if block.kind in _CUSTOM_BLOCK_KINDS:
        return _custom_block_lines(block, width)
    return _regular_block_lines(block, width)


def _custom_block_lines(block: FrameBlock, width: int) -> tuple[FrameLine, ...]:
    rows = [FrameLine("", "tool_result")]
    rows.extend(
        FrameLine(clip_custom_text(f" {line}", width), block.kind)
        for line in block.lines
    )
    rows.append(FrameLine("", "tool_result"))
    if block.kind in {"tool_result_custom", "custom_message_custom"}:
        rows.append(FrameLine(""))
    return tuple(rows)


def _regular_block_lines(block: FrameBlock, width: int) -> tuple[FrameLine, ...]:
    kind = block.kind
    rendered = _leading_block_rows(kind)
    prefix = _block_prefix(kind)
    line_kind = _line_kind_for_block(kind)
    available = max(10, width - len(prefix))
    for line in block.lines:
        for wrapped in textwrap.wrap(line, width=available) or [""]:
            rendered.append(
                FrameLine(clip_text(f"{prefix}{wrapped}", width), line_kind)
            )
    rendered.extend(_trailing_block_rows(kind))
    return tuple(rendered)


def _block_prefix(kind: str) -> str:
    return {
        "user": " ",
        "assistant": " ",
        "reasoning": " ",
        "working": " ",
        "error": " ",
        "tool": " $ ",
        "tool_read": " ",
        "tool_result": " ",
        "settings": " ",
        "custom": " ",
        "notice": "pipy  ",
    }.get(kind, "")


def _leading_block_rows(kind: str) -> list[FrameLine]:
    if kind == "user":
        return [FrameLine("", "user")]
    if kind in {"tool", "tool_read"}:
        return [FrameLine("", "tool_result")]
    if kind in {"reasoning", "notice", "settings", "custom"}:
        return [FrameLine("")]
    return []


def _trailing_block_rows(kind: str) -> tuple[FrameLine, ...]:
    if kind == "user":
        return FrameLine("", "user"), FrameLine("")
    if kind == "tool":
        return (FrameLine("", "tool_result"),)
    if kind == "tool_read":
        return FrameLine("", "tool_result"), FrameLine("")
    if kind == "tool_result":
        return FrameLine(""), FrameLine("")
    if kind in {"assistant", "notice", "working", "settings", "custom", "error"}:
        return (FrameLine(""),)
    return ()


def _line_kind_for_block(kind: str) -> str:
    return {
        "user": "user",
        "section": "section",
        "title": "title",
        "controls": "controls",
        "dim": "dim",
        "resource": "resource",
        "normal": "normal",
        "working": "working",
        "error": "error",
        "reasoning": "reasoning",
        "tool": "tool",
        "tool_read": "tool_read",
        "tool_result": "tool_result",
        "tool_call_custom": "tool_call_custom",
        "tool_result_custom": "tool_result_custom",
        "custom_message_custom": "custom_message_custom",
        "settings": "settings",
        "custom": "settings",
    }.get(kind, "normal")


# ---------------------------------------------------------------------------
# Input wrapping, region budgeting, row selection, and full/live composition.


def display_input_text(text: str) -> str:
    if not any(ch == "\n" or ord(ch) < 0x20 or ch == "\x7f" for ch in text):
        return text
    return "".join(
        _INPUT_NEWLINE_GLYPH
        if ch == "\n"
        else " "
        if ord(ch) < 0x20 or ch == "\x7f"
        else ch
        for ch in text
    )


def input_lines(
    snapshot: InputSnapshot, width: int, max_rows: int
) -> tuple[FrameLine, ...]:
    """Project at least one input row, with a cursor, inside a bounded window."""

    row_limit = max(1, max_rows)
    if snapshot.custom_rows is not None:
        return _custom_input_lines(snapshot.custom_rows, row_limit)
    display = display_input_text(snapshot.text)
    capacity = max(1, width - 1)
    rows = tuple(
        display[start : start + capacity] for start in range(0, len(display), capacity)
    ) or ("",)
    cursor = min(len(snapshot.text), max(0, snapshot.cursor))
    cursor_row, cursor_col = divmod(cursor, capacity)
    if cursor_row >= len(rows):
        rows = (*rows, "")
    rows, cursor_row = _window_input_rows(rows, cursor_row, row_limit)
    return tuple(
        FrameLine(
            clip_text(row or " ", width),
            "input",
            {"cursor_col": cursor_col} if index == cursor_row else None,
        )
        for index, row in enumerate(rows)
    )


def _custom_input_lines(
    rows: tuple[ResolvedCustomEditorLine, ...], row_limit: int
) -> tuple[FrameLine, ...]:
    if not rows:
        return (FrameLine(" ", "input", {"cursor_col": 0}),)
    return rows[-row_limit:]


def _window_input_rows(
    rows: tuple[str, ...], cursor_row: int, max_rows: int
) -> tuple[tuple[str, ...], int]:
    if len(rows) <= max_rows:
        return rows, cursor_row
    start = min(max(0, cursor_row - max_rows + 1), max(0, len(rows) - max_rows))
    return rows[start : start + max_rows], cursor_row - start


def render_full_frame(
    snapshot: FrameSnapshot, *, pad: bool = True
) -> tuple[FrameLine, ...]:
    """Compose the captured logical full screen from one immutable snapshot."""

    history = list(_history_and_transient_lines(snapshot))
    if snapshot.overlay is not None:
        return _render_overlay_frame(snapshot, history, pad)
    regions = _layout_regions(snapshot)
    maximum = _full_history_budget(snapshot, regions)
    has_tool_panel = any(
        block.kind in _PENDING_TOOL_KINDS for block in snapshot.history
    )
    minimum = min(_DEFAULT_HISTORY_VIEW_LINES, maximum)
    overflowed = len(history) > maximum
    if overflowed:
        capacity = _overflow_history_capacity(snapshot.height, maximum, has_tool_panel)
        history = list(_tail_history_lines(tuple(history), capacity))
    elif len(history) < minimum:
        history.extend(FrameLine("") for _ in range(minimum - len(history)))
    rows = _assemble_regions(tuple(history), regions, snapshot)
    return _finish_frame(rows, snapshot.width, snapshot.height, pad)


def render_live_region(snapshot: FrameSnapshot) -> tuple[FrameLine, ...]:
    """Compose only the mutable region below committed native scrollback."""

    if snapshot.overlay is not None:
        return snapshot.overlay
    regions = _layout_regions(snapshot)
    chrome_height = _regions_height(regions)
    transient_budget = max(0, snapshot.height - chrome_height - 1)
    transient = _transient_lines(snapshot)
    if len(transient) > transient_budget:
        transient = transient[-transient_budget:] if transient_budget else ()
    return _assemble_regions(transient, regions, snapshot)


def _history_lines(snapshot: FrameSnapshot) -> tuple[FrameLine, ...]:
    return tuple(
        row for block in snapshot.history for row in block_lines(block, snapshot.width)
    )


def _history_and_transient_lines(snapshot: FrameSnapshot) -> tuple[FrameLine, ...]:
    return (*_history_lines(snapshot), *_transient_lines(snapshot))


def _transient_lines(snapshot: FrameSnapshot) -> tuple[FrameLine, ...]:
    blocks: list[FrameBlock] = []
    if snapshot.assistant_text:
        blocks.append(
            FrameBlock(
                "assistant", tuple(snapshot.assistant_text.splitlines()) or ("",)
            )
        )
    if snapshot.reasoning_text:
        reasoning = (
            (snapshot.hidden_thinking_label,)
            if snapshot.thinking_hidden
            else tuple(snapshot.reasoning_text.splitlines()) or ("",)
        )
        blocks.append(FrameBlock("reasoning", reasoning))
    if snapshot.tool_output_text:
        raw = tuple(snapshot.tool_output_text.splitlines()) or ("",)
        cap = len(raw) + 1 if snapshot.tools_expanded else _TOOL_STREAM_LIVE_LINES
        blocks.append(FrameBlock("tool_result", raw[-cap:]))
    if snapshot.working_text:
        blocks.append(FrameBlock("working", (snapshot.working_text,)))
    return tuple(row for block in blocks for row in block_lines(block, snapshot.width))


def _render_overlay_frame(
    snapshot: FrameSnapshot, history: list[FrameLine], pad: bool
) -> tuple[FrameLine, ...]:
    overlay = snapshot.overlay or ()
    maximum = max(0, snapshot.height - len(overlay))
    if len(history) > maximum:
        history = history[-maximum:] if maximum else []
    return _finish_frame((*history, *overlay), snapshot.width, snapshot.height, pad)


def _layout_regions(snapshot: FrameSnapshot) -> _LayoutRegions:
    chrome = _clamp_chrome(snapshot)
    extra = (
        len(chrome.header)
        + len(chrome.above)
        + len(chrome.below)
        + max(0, len(chrome.footer) - 2)
    )
    max_input = max(
        1,
        snapshot.height
        - len(snapshot.popup)
        - len(snapshot.pending)
        - len(chrome.status)
        - extra
        - 4,
    )
    return _LayoutRegions(
        menu=snapshot.popup,
        pending=snapshot.pending,
        status=chrome.status,
        header=chrome.header,
        above=chrome.above,
        below=chrome.below,
        footer=chrome.footer,
        input_rows=input_lines(snapshot.input, snapshot.width, max_input),
    )


def _clamp_chrome(snapshot: FrameSnapshot) -> ChromeSnapshot:
    chrome = snapshot.chrome
    footer_reserved = (
        2
        + len(snapshot.popup)
        + len(snapshot.pending)
        + len(chrome.status)
        + _MIN_INPUT_ROWS
    )
    footer = chrome.footer[: max(0, snapshot.height - footer_reserved)]
    reserved = footer_reserved + len(footer) + 1
    header, above, below = _clamp_chrome_rows(
        chrome.header,
        chrome.above,
        chrome.below,
        max(0, snapshot.height - reserved),
        snapshot.width,
    )
    return ChromeSnapshot(header, above, below, footer, chrome.status)


def _clamp_chrome_rows(
    header: tuple[FrameLine, ...],
    above: tuple[FrameLine, ...],
    below: tuple[FrameLine, ...],
    budget: int,
    width: int,
) -> tuple[tuple[FrameLine, ...], tuple[FrameLine, ...], tuple[FrameLine, ...]]:
    if budget <= 0:
        return (), (), ()
    if len(header) + len(above) + len(below) <= budget:
        return header, above, below
    marker = FrameLine(clip_text("  … (chrome clipped)", width), "slash_menu_scroll")
    keep = max(0, budget - 1)
    out_header = header[:keep]
    keep -= len(out_header)
    out_above = above[:keep]
    keep -= len(out_above)
    out_below = below[:keep]
    return _append_marker(out_header, out_above, out_below, marker)


def _append_marker(
    header: tuple[FrameLine, ...],
    above: tuple[FrameLine, ...],
    below: tuple[FrameLine, ...],
    marker: FrameLine,
) -> tuple[tuple[FrameLine, ...], tuple[FrameLine, ...], tuple[FrameLine, ...]]:
    if below:
        return header, above, (*below, marker)
    if above:
        return header, (*above, marker), below
    if header:
        return (*header, marker), above, below
    return (marker,), above, below


def _full_history_budget(snapshot: FrameSnapshot, regions: _LayoutRegions) -> int:
    extra = (
        len(regions.header)
        + len(regions.above)
        + len(regions.below)
        + max(0, len(regions.footer) - 2)
    )
    maximum = max(
        0,
        snapshot.height
        - len(regions.input_rows)
        - 4
        - len(regions.menu)
        - len(regions.pending)
        - len(regions.status)
        - extra,
    )
    if any(block.kind in _PENDING_TOOL_KINDS for block in snapshot.history):
        return min(maximum, _TOOL_PANEL_HISTORY_VIEW_LINES)
    return maximum


def _regions_height(regions: _LayoutRegions) -> int:
    return (
        len(regions.input_rows)
        + 2
        + len(regions.menu)
        + len(regions.pending)
        + len(regions.status)
        + len(regions.header)
        + len(regions.above)
        + len(regions.below)
        + len(regions.footer)
    )


def _assemble_regions(
    leading: tuple[FrameLine, ...], regions: _LayoutRegions, snapshot: FrameSnapshot
) -> tuple[FrameLine, ...]:
    top = _input_separator(snapshot, label=False)
    bottom = _input_separator(snapshot, label=True)
    return (
        *leading,
        *regions.header,
        *regions.pending,
        *regions.above,
        top,
        *regions.input_rows,
        bottom,
        *regions.menu,
        *regions.below,
        *regions.status,
        *regions.footer,
    )


def _input_separator(snapshot: FrameSnapshot, *, label: bool) -> FrameLine:
    if not snapshot.input.text.lstrip().startswith("!"):
        return FrameLine("─" * snapshot.width, "separator")
    text = "─" * snapshot.width
    tag = " ! bash "
    if label and snapshot.width > len(tag) + 2:
        text = "─" + tag + "─" * (snapshot.width - len(tag) - 1)
    return FrameLine(text, "bash_separator")


def _finish_frame(
    rows: Iterable[FrameLine], width: int, height: int, pad: bool
) -> tuple[FrameLine, ...]:
    visible = tuple(rows)[: max(0, height)]
    finished = tuple(_finish_line(row, width, pad) for row in visible)
    if not pad:
        return finished
    return (*finished, *(FrameLine(" " * width) for _ in range(height - len(finished))))


def _finish_line(row: FrameLine, width: int, pad: bool) -> FrameLine:
    if isinstance(row, ResolvedCustomEditorLine):
        text = row.text
        if pad:
            text += " " * max(0, width - visible_len(text))
        return ResolvedCustomEditorLine(text, row.kind, row.meta)
    text = pad_text(row.text, width) if pad else clip_custom_text(row.text, width)
    return FrameLine(text, row.kind, row.meta)


def _tail_history_lines(
    lines: tuple[FrameLine, ...], maximum: int
) -> tuple[FrameLine, ...]:
    if maximum <= 0:
        return ()
    last_user = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if lines[index].kind == "user"
        ),
        None,
    )
    if last_user is None:
        return lines[-maximum:]
    start, end = _user_block_bounds(lines, last_user)
    user = lines[start:end]
    if len(user) >= maximum:
        return user[-maximum:]
    before, after = lines[:start], lines[end:]
    available = maximum - len(user)
    minimum_context = min(len(before), _OVERFLOW_CONTEXT_MIN_LINES, max(0, available))
    after_rows = _history_tail(after, max(0, available - minimum_context))
    context_capacity = maximum - len(user) - len(after_rows)
    target = min(len(before), _OVERFLOW_CONTEXT_TARGET_LINES, max(0, context_capacity))
    context = before[-target:] if target else ()
    remaining = maximum - len(context) - len(user)
    if len(after_rows) > remaining:
        after_rows = after_rows[-remaining:] if remaining > 0 else ()
    return (*context, *user, *after_rows)


def _user_block_bounds(lines: tuple[FrameLine, ...], index: int) -> tuple[int, int]:
    start = index
    while start > 0 and lines[start - 1].kind == "user":
        start -= 1
    end = index + 1
    while end < len(lines) and lines[end].kind == "user":
        end += 1
    return start, end


def _history_tail(lines: tuple[FrameLine, ...], capacity: int) -> tuple[FrameLine, ...]:
    if capacity <= 0:
        return ()
    if len(lines) <= capacity:
        return lines
    compacted = tuple(
        row for row in lines if row.text.strip() or row.kind in {"tool_result", "user"}
    )
    return compacted[-capacity:] if len(compacted) >= capacity else lines[-capacity:]


def _overflow_history_capacity(height: int, maximum: int, has_tool_panel: bool) -> int:
    default = maximum if has_tool_panel else _DEFAULT_HISTORY_VIEW_LINES
    if has_tool_panel:
        return min(maximum, default)
    return min(maximum, default, max(0, height - 5 - _OVERFLOW_BOTTOM_GUTTER_LINES))


def input_index(lines: tuple[FrameLine, ...]) -> int:
    last = len(lines) - 1
    with_cursor = next(
        (
            index
            for index, row in enumerate(lines)
            if row.kind == "input"
            and isinstance((row.meta or {}).get("cursor_col"), int)
        ),
        None,
    )
    if with_cursor is not None:
        return with_cursor
    return next((index for index, row in enumerate(lines) if row.kind == "input"), last)


# ---------------------------------------------------------------------------
# Style mapping and deterministic terminal paint planning.


def style_line(line: FrameLine, style: ChromeStyle, width: int) -> str:
    """Map one immutable row to terminal text without terminal side effects."""

    raw = line.text
    text = raw.rstrip()
    special = {
        "title": _style_title,
        "working": _style_working,
        "slash_menu": _style_menu_row,
        "input": _style_input,
    }.get(line.kind)
    if special is not None:
        return special(line, style, width)
    simple = {
        "dim": style.dim,
        "resource": style.dim,
        "footer": style.dim,
        "controls": style.dim,
        "section": style.section_label,
        "separator": style.separator,
        "bash_separator": style.error,
        "reasoning": style.dim_italic,
        "error": style.error,
        "selector_title": style.section_label,
        "selector_option_selected": style.menu_selection,
        "selector_option_disabled": style.secondary_dim,
        "slash_menu_selected": style.menu_selection,
        "slash_menu_scroll": style.secondary_dim,
    }.get(line.kind)
    if simple is not None:
        return simple(text)
    return _style_width_kind(line.kind, line.text, text, style, width)


def _style_width_kind(
    kind: str, raw: str, text: str, style: ChromeStyle, width: int
) -> str:
    if kind == "tool":
        return style.tool_command(text, width=width)
    if kind == "tool_read":
        return style.tool_read(text, width=width)
    if kind == "tool_result":
        return style.tool_result(text, width=width)
    if kind == "user":
        return style.user_message(text, width=width)
    if kind in _CUSTOM_BLOCK_KINDS or kind == "chrome_custom":
        return style.tool_custom(raw, width=width)
    return text


def _style_title(line: FrameLine, style: ChromeStyle, width: int) -> str:
    del width
    text = line.text.rstrip()
    if not style.enabled:
        return text
    if text.startswith(" pipy v"):
        return f" {style.title('pipy')}{style.dim(text[len(' pipy') :])}"
    return style.title(text)


def _style_working(line: FrameLine, style: ChromeStyle, width: int) -> str:
    del width
    leading, spinner, rest = _split_working_spinner(line.text.rstrip())
    return f"{style.secondary_dim(leading)}{style.menu_selection(spinner)}{style.secondary_dim(rest)}"


def _style_menu_row(line: FrameLine, style: ChromeStyle, width: int) -> str:
    del width
    text = line.text.rstrip()
    start = (line.meta or {}).get("description_start")
    if not isinstance(start, int) or start >= len(text):
        return style.menu_row(text)
    prefix = "\x1b[39m" if style.enabled else ""
    return prefix + text[:start] + style.secondary_dim(text[start:])


def _style_input(line: FrameLine, style: ChromeStyle, width: int) -> str:
    cursor = (line.meta or {}).get("cursor_col")
    if not isinstance(cursor, int):
        return line.text
    col = min(max(0, cursor), max(0, width - 1))
    before = line.text[:col]
    cursor_char = line.text[col] if col < len(line.text) else " "
    after = line.text[col + 1 :] if col < len(line.text) else ""
    return style.cursor_cell(before, cursor_char, after)


def _split_working_spinner(text: str) -> tuple[str, str, str]:
    if not text:
        return "", "", ""
    leading_length = len(text) - len(text.lstrip())
    leading, remainder = text[:leading_length], text[leading_length:]
    if not remainder:
        return leading, "", ""
    if len(remainder) >= 2 and remainder[1].isspace():
        return leading, remainder[0], remainder[1:]
    return leading, remainder[:1], remainder[1:]


def build_paint_plan(
    snapshot: FrameSnapshot, state: PaintState, style: ChromeStyle
) -> PaintPlan:
    """Return logical paint operations and publication values for one attempt."""

    committed = _committed_paint_rows(snapshot, state, style)
    live = render_live_region(snapshot)
    live, current_input = _stabilize_input_row(live, state, len(committed))
    live_rows = tuple(_paint_row(row, style, snapshot.width) for row in live)
    cursor_col = _cursor_col(live, current_input)
    return PaintPlan(
        prior_live_height=state.live_height,
        prior_live_input_row=state.live_input_row,
        committed_rows=committed,
        live_rows=live_rows,
        cursor_lines_up=max(0, len(live) - 1 - current_input),
        cursor_col=min(max(0, snapshot.width - 1), cursor_col),
        cursor_visible=snapshot.cursor_visible,
        painted_block_count=len(snapshot.history),
        live_height=len(live),
        live_input_row=current_input,
        painted_size=(snapshot.width, snapshot.height),
    )


def _cursor_col(live: tuple[FrameLine, ...], input_row: int) -> int:
    if not live or input_row < 0 or input_row >= len(live):
        return 0
    raw_cursor = (live[input_row].meta or {}).get("cursor_col")
    return raw_cursor if isinstance(raw_cursor, int) else 0


def _committed_paint_rows(
    snapshot: FrameSnapshot, state: PaintState, style: ChromeStyle
) -> tuple[PaintRow, ...]:
    return tuple(
        _paint_row(row, style, snapshot.width)
        for block in snapshot.history[state.painted_block_count :]
        for row in block_lines(block, snapshot.width)
    )


def _paint_row(row: FrameLine, style: ChromeStyle, width: int) -> PaintRow:
    styled = style_line(row, style, width)
    return PaintRow(styled, visible_len(styled) < width)


def _stabilize_input_row(
    live: tuple[FrameLine, ...], state: PaintState, committed_rows: int
) -> tuple[tuple[FrameLine, ...], int]:
    current = input_index(live)
    padding = max(0, state.live_input_row - committed_rows - current)
    if padding:
        live = (*(FrameLine("") for _ in range(padding)), *live)
        current += padding
    return live, current

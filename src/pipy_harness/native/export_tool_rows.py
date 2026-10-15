"""Tool rows drawn ahead for the HTML export.

Pi ``core/export-html/tool-renderer.ts`` and ``ansi-to-html.ts``: the calls
to a tool that has renderers are drawn with them, collapsed and expanded, and
the terminal text is turned into HTML that the exported page shows in place
of the raw arguments and result. So a call to an MCP server tool, or to a
tool an extension draws, looks in the export as it does in the transcript.
Calls to the built-in tools and to tools without renderers are left to the
page.
"""

from __future__ import annotations

import html
import io
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar, cast

from pipy_harness.native.agent import AgentAssistantMessage, AgentToolResultMessage
from pipy_harness.native.chrome import ChromeStyle, chrome_style_for
from pipy_harness.native.session_tree import MessageEntry, SessionEntry
from pipy_harness.native.tool_renderer_lookup import (
    BuiltinToolRows,
    McpToolRows,
    ToolRendererLookup,
)
from pipy_harness.native.tool_renderers import build_tool_render_theme
from pipy_harness.native.tool_result_display import tool_result_display_text
from pipy_harness.native.tool_rows import (
    BUILTIN_RENDERED_TOOLS,
    OWN_ROWS_TOOLS,
    RowLine,
    RowRenderers,
    RowRenderInputs,
    ToolRowResult,
    ToolRowState,
    parse_arguments,
    render_tool_row_parts,
    row_theme,
)

_Drawn = TypeVar("_Drawn")

# Pi `createToolHtmlRenderer`: the width the rows are drawn at.
EXPORT_ROW_WIDTH = 100

_SGR = re.compile(r"\x1b\[([0-9;]*)m")
# Escape sequences other than SGR, and the control characters left over.
_OTHER_ESCAPE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|.)?"
)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_BASIC_COLORS = (
    "#000000",
    "#cd3131",
    "#0dbc79",
    "#e5e510",
    "#2472c8",
    "#bc3fbc",
    "#11a8cd",
    "#e5e5e5",
    "#666666",
    "#f14c4c",
    "#23d18b",
    "#f5f543",
    "#3b8eea",
    "#d670d6",
    "#29b8db",
    "#ffffff",
)


def export_chrome_style() -> ChromeStyle:
    """The active theme's colours, always on: a file is not a terminal."""

    return replace(chrome_style_for(io.StringIO()), enabled=True, truecolor=True)


def render_export_tools(
    entries: Sequence[SessionEntry],
    lookup: ToolRendererLookup,
    *,
    cwd: Path,
    redact: Callable[[Any], Any],
    style: ChromeStyle | None = None,
) -> dict[str, dict[str, str]]:
    """The rows of the calls in ``entries`` that renderers draw, as HTML.

    Keyed by the call's id, like Pi's ``renderedTools``: ``callHtml`` is the
    collapsed call; ``resultHtmlExpanded`` is there when the call has a
    result, and ``resultHtmlCollapsed`` when the collapsed result differs
    from it. ``redact`` is applied to the arguments, the result text and the
    details before anything is drawn, so a renderer never sees a credential
    the export withholds.
    """

    style = style if style is not None else export_chrome_style()
    states: dict[str, ToolRowState] = {}
    rendered: dict[str, dict[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, MessageEntry):
            continue
        message = entry.message
        if isinstance(message, AgentAssistantMessage):
            for call in message.tool_calls:
                state = _row_state(
                    lookup,
                    call.tool_name,
                    redact(parse_arguments(call.arguments_json.value)),
                    cwd,
                    call.provider_correlation_id,
                )
                states.pop(call.provider_correlation_id, None)
                rendered.pop(call.provider_correlation_id, None)
                call_html = None if state is None else _drawn(_call_html, state, style)
                if state is not None and call_html is not None:
                    states[call.provider_correlation_id] = state
                    rendered[call.provider_correlation_id] = {"callHtml": call_html}
        elif isinstance(message, AgentToolResultMessage):
            state = states.pop(message.provider_correlation_id, None)
            if state is None:
                continue
            details = message.details
            state.result = ToolRowResult(
                redact(tool_result_display_text(message)),
                None if details is None else redact(details),
                message.is_error,
            )
            result_html = _drawn(_result_html, state, style)
            if result_html is None:
                # The page then shows the stored result as text.
                continue
            collapsed, expanded = result_html
            html_parts = rendered[message.provider_correlation_id]
            html_parts["resultHtmlExpanded"] = expanded
            if collapsed and collapsed != expanded:
                html_parts["resultHtmlCollapsed"] = collapsed
    return rendered


def _drawn(
    draw: Callable[[ToolRowState, ChromeStyle], _Drawn],
    state: ToolRowState,
    style: ChromeStyle,
) -> _Drawn | None:
    """What ``draw`` makes of a row, or None when it cannot be drawn.

    Pi ``createToolHtmlRenderer`` catches every error of a call's rendering
    and leaves that part to the page; one call must not cost the export.
    """

    try:
        return draw(state, style)
    except Exception:  # noqa: BLE001 - this part of the call is left to the page
        return None


def _call_html(state: ToolRowState, style: ChromeStyle) -> str:
    call_rows, _ = _parts(state, style, expanded=False)
    return ansi_lines_to_html(call_rows)


def _result_html(state: ToolRowState, style: ChromeStyle) -> tuple[str, str]:
    # The call was drawn before its result (`_call_html`), as Pi's export
    # draws it; it is not drawn again here.
    _, collapsed_rows = _parts(state, style, expanded=False, with_call=False)
    _, expanded_rows = _parts(state, style, expanded=True, with_call=False)
    return (
        ansi_lines_to_html(_trimmed(collapsed_rows)),
        ansi_lines_to_html(_trimmed(expanded_rows)),
    )


def _row_state(
    lookup: ToolRendererLookup,
    name: str,
    args: dict[str, Any],
    cwd: Path,
    call_id: str,
) -> ToolRowState | None:
    """The row of a call that renderers draw; None for one left to the page."""

    if name in BUILTIN_RENDERED_TOOLS:
        return None
    resolved = lookup.resolve(name, builtin=name in OWN_ROWS_TOOLS)
    if resolved is None or isinstance(resolved, BuiltinToolRows):
        return None
    if isinstance(resolved, McpToolRows):
        return ToolRowState(
            tool_name=name,
            args=args,
            cwd=cwd,
            mcp_label=resolved.label,
            correlation_id=call_id,
        )
    extension = cast(RowRenderers, resolved)
    if extension.render_call is None and extension.render_result is None:
        return None
    return ToolRowState(
        tool_name=name, args=args, cwd=cwd, extension=extension, correlation_id=call_id
    )


def _parts(
    state: ToolRowState, style: ChromeStyle, *, expanded: bool, with_call: bool = True
) -> tuple[list[str], list[str]]:
    call, result = render_tool_row_parts(
        state,
        RowRenderInputs(
            expanded=expanded,
            width=EXPORT_ROW_WIDTH,
            theme=row_theme(style),
            extension_theme=build_tool_render_theme(style),
        ),
        with_call=with_call,
    )
    return _texts(call), _texts(result)


def _texts(rows: Sequence[RowLine]) -> list[str]:
    return [line for row in rows for line in row.text.split("\n")]


def _trimmed(lines: list[str]) -> list[str]:
    """Pi ``trimRenderedResultLines``: without blank lines at either end."""

    start, end = 0, len(lines)
    while start < end and not _SGR.sub("", lines[start]).strip():
        start += 1
    while end > start and not _SGR.sub("", lines[end - 1]).strip():
        end -= 1
    return lines[start:end]


def ansi_lines_to_html(lines: Sequence[str]) -> str:
    """Pi ``ansiLinesToHtml``: one ``<div class="ansi-line">`` per line.

    Foreground colours, bold, italic and underline become inline styles;
    backgrounds and everything else a terminal would act on are dropped, and
    the text is HTML-escaped.
    """

    return "".join(
        f'<div class="ansi-line">{_line_html(line) or "&nbsp;"}</div>' for line in lines
    )


def _line_html(line: str) -> str:
    out: list[str] = []
    style = _Style()
    position = 0
    for match in _SGR.finditer(line):
        out.append(style.span(line[position : match.start()]))
        style.apply(match.group(1))
        position = match.end()
    out.append(style.span(line[position:]))
    return "".join(out)


class _Style:
    """The text attributes in effect at one point of a line."""

    def __init__(self) -> None:
        self.color: str | None = None
        self.bold = False
        self.italic = False
        self.underline = False

    def reset(self) -> None:
        self.color = None
        self.bold = self.italic = self.underline = False

    def span(self, text: str) -> str:
        text = _CONTROL.sub("", _OTHER_ESCAPE.sub("", text))
        if not text:
            return ""
        escaped = html.escape(text)
        css = []
        if self.color is not None:
            css.append(f"color:{self.color}")
        if self.bold:
            css.append("font-weight:bold")
        if self.italic:
            css.append("font-style:italic")
        if self.underline:
            css.append("text-decoration:underline")
        return f'<span style="{";".join(css)}">{escaped}</span>' if css else escaped

    def apply(self, parameters: str) -> None:
        codes = [_parameter(code) for code in parameters.split(";")]
        index = 0
        while index < len(codes):
            code = codes[index]
            index += 1
            if code in (38, 48):
                color, index = _extended_color(codes, index)
                if code == 38 and color is not None:
                    self.color = color
            else:
                self._apply_simple(code)

    def _apply_simple(self, code: int) -> None:
        if code == 0:
            self.reset()
        elif code in _FLAGS:
            name, value = _FLAGS[code]
            setattr(self, name, value)
        elif 30 <= code <= 37:
            self.color = _BASIC_COLORS[code - 30]
        elif 90 <= code <= 97:
            self.color = _BASIC_COLORS[code - 90 + 8]
        elif code == 39:
            self.color = None


def _parameter(digits: str) -> int:
    """One SGR parameter; -1 for a number that is no code.

    An empty parameter is 0 and leading zeros do not count. A number of more
    than three digits is no code, and Python refuses to convert one of
    several thousand digits at all.
    """

    digits = digits.lstrip("0")
    if not digits:
        return 0
    return int(digits) if len(digits) <= 3 else -1


_FLAGS: Mapping[int, tuple[str, bool]] = {
    1: ("bold", True),
    22: ("bold", False),
    3: ("italic", True),
    23: ("italic", False),
    4: ("underline", True),
    24: ("underline", False),
}


def _extended_color(codes: list[int], index: int) -> tuple[str | None, int]:
    """The colour of a ``38``/``48`` sequence and where the codes go on."""

    mode = codes[index] if index < len(codes) else None
    if mode == 5 and index + 1 < len(codes):
        return _palette_color(codes[index + 1]), index + 2
    if mode == 2 and index + 3 < len(codes):
        red, green, blue = (
            min(255, max(0, value)) for value in codes[index + 1 : index + 4]
        )
        return f"#{red:02x}{green:02x}{blue:02x}", index + 4
    return None, len(codes)


def _palette_color(number: int) -> str | None:
    """A colour of the 256-colour palette."""

    if 0 <= number < 16:
        return _BASIC_COLORS[number]
    if 16 <= number < 232:
        number -= 16
        levels = [
            0 if part == 0 else 55 + part * 40
            for part in (number // 36, number // 6 % 6, number % 6)
        ]
        return "#{:02x}{:02x}{:02x}".format(*levels)
    if 232 <= number < 256:
        gray = 8 + (number - 232) * 10
        return f"#{gray:02x}{gray:02x}{gray:02x}"
    return None


__all__ = [
    "EXPORT_ROW_WIDTH",
    "ansi_lines_to_html",
    "export_chrome_style",
    "render_export_tools",
]

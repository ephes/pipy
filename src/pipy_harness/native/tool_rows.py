"""Pi's tool rows: one box per tool call, drawn by each tool's renderers.

Ports ``modes/interactive/components/tool-execution.ts``
(``ToolExecutionComponent``), the built-in renderers in
``core/tools/renderers/{read,grep,find,ls,write,edit,bash}.ts``, the helpers
in ``core/tools/render-utils.ts`` and ``modes/interactive/components/diff.ts``
(``renderDiff``) from pi-mono ``1b347794e``.

A :class:`ToolRowState` keeps everything a row is drawn from (arguments,
extension renderer state, the edit preview, the result and its ``details``),
so the row is drawn again when Ctrl+O flips the expansion flag, live and
restored, like Pi's ``setExpanded``. :func:`render_tool_row` returns
:class:`RowLine` values: styled text plus the background they sit on
(``pending`` while the call runs, then ``success`` or ``error``; ``none`` for
the lines Pi draws outside a box) and whether the frame renderer wraps them
(Pi ``Text``) or clips them (a component that rendered itself at a width).

Deviations, documented in ``docs/tui-workflow.md``: no syntax highlighting
(Pi's ``highlightCode`` uses highlight.js; lines Pi would highlight keep the
terminal's default colour), no OSC 8 file links and no image blocks. A
running ``bash`` row ticks ``Elapsed`` every second (the transcript owns
the timer).
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pipy_harness.native.ansi_wrap import (
    split_lines_with_carry,
    visible_width,
    wrap_text_with_ansi,
)
from pipy_harness.native.frame_renderer import encode_tool_box_line
from pipy_harness.native.tool_headers import INVALID_ARG, shorten_path
from pipy_harness.native.tools.bash_executor import (
    sanitize_binary_output,
    strip_ansi,
)
from pipy_harness.native.tools.edit_diff import (
    Edit,
    EditError,
    apply_edits_to_normalized_content,
    generate_diff_string,
    normalize_to_lf,
    split_bom,
)
from pipy_harness.native.tools.fs_errors import error_code
from pipy_harness.native.tools.path_utils import resolve_to_cwd
from pipy_harness.native.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
)
from pipy_harness.native.tools.word_diff import JS_WHITESPACE, diff_words

if TYPE_CHECKING:
    from pipy_harness.native.chrome import ChromeStyle
    from pipy_harness.native.extension_types import ExtensionTool

RowBackground = Literal["pending", "success", "error", "custom", "none"]

#: Tools whose rows use Pi's built-in renderers (``createAllToolRenderers``).
BUILTIN_RENDERED_TOOLS = frozenset(
    {"read", "grep", "find", "ls", "write", "edit", "bash"}
)
# Pi `ToolExecutionComponent` FALLBACK_PREVIEW_LINES and the renderers' limits.
_FALLBACK_PREVIEW_LINES = 10
_READ_PREVIEW_LINES = 10
_WRITE_PREVIEW_LINES = 10
_GREP_PREVIEW_LINES = 15
_FIND_LS_PREVIEW_LINES = 20
_BASH_PREVIEW_LINES = 5
_COLLAPSED_ARGS_CHARS = 100
_COMPACT_RESOURCE_FILE_NAMES = frozenset(
    {"AGENTS.override.md", "AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD"}
)
# Pi theme `getLanguageFromPath`; a known language means highlighted lines.
# JavaScript's `\s` and `\d` (ASCII digits) and `.` (no line terminators).
_JS_WS_CLASS = "".join(f"\\u{ord(char):04x}" for char in JS_WHITESPACE)
_JS_DOT = "[^\\n\\r\\u2028\\u2029]"
_DIFF_LINE = re.compile(
    f"^([+\\-{_JS_WS_CLASS}])([{_JS_WS_CLASS}]*[0-9]*)[{_JS_WS_CLASS}]({_JS_DOT}*)$"
)
_HIGHLIGHT_EXTENSIONS = frozenset(
    "ts tsx js jsx mjs cjs py rb rs go java kt swift c h cpp cc cxx hpp cs php "
    "sh bash zsh fish ps1 sql html htm css scss sass less json yaml yml toml "
    "xml md markdown dockerfile makefile cmake lua perl r scala clj ex exs erl "
    "hs ml vim graphql proto tf hcl".split()
)


@dataclass(frozen=True, slots=True)
class RowLine:
    """One logical row of a tool box."""

    text: str
    bg: RowBackground = "none"
    wrap: bool = True


@dataclass(frozen=True, slots=True)
class ToolRowResult:
    """A tool result as a row draws it (Pi ``AgentToolResult`` + ``isError``)."""

    content: str
    details: Mapping[str, Any] | None
    is_error: bool


@dataclass(slots=True)
class EditPreview:
    """Pi ``EditDiffResult | EditDiffError``."""

    diff: str | None = None
    first_changed_line: int | None = None
    error: str | None = None


@dataclass(slots=True)
class ToolRowState:
    """The retained inputs of one tool row (Pi ``ToolExecutionComponent``)."""

    tool_name: str
    args: dict[str, Any]
    cwd: Path
    extension: ExtensionTool | None = None
    renderer_state: dict[str, object] = field(default_factory=dict)
    preview: EditPreview | None = None
    settled_error: bool = False
    result: ToolRowResult | None = None
    partial_output: str = ""
    duration_seconds: float | None = None
    # Pi bash renderer `startedAt`/`endedAt` (monotonic seconds): set when a
    # live call starts executing, never for a replayed one. `ended_at`
    # freezes the `Elapsed` of a row committed without a result.
    started_at: float | None = None
    ended_at: float | None = None


# ---------------------------------------------------------------------------
# Theme: Pi's color keys over pipy's palette, with fg-only resets.


@dataclass(frozen=True, slots=True)
class RowTheme:
    """Pi ``Theme`` for tool rows: ``fg``/``bold``/``inverse`` by Pi key."""

    enabled: bool
    codes: Mapping[str, str]

    def fg(self, key: str, text: str) -> str:
        if not self.enabled:
            return text
        code = self.codes[key]
        # Pi's `fg` resets only the foreground (`39`), so the box background
        # survives; a palette code that also sets bold closes it too.
        reset = "\x1b[39;22m" if code.split(";", 1)[0] == "1" else "\x1b[39m"
        return f"\x1b[{code}m{text}{reset}"

    def bold(self, text: str) -> str:
        return f"\x1b[1m{text}\x1b[22m" if self.enabled else text

    def inverse(self, text: str) -> str:
        return f"\x1b[7m{text}\x1b[27m" if self.enabled else text


def row_theme(style: ChromeStyle) -> RowTheme:
    """The row theme for ``style``'s palette (Pi dark keys → pipy colours)."""

    palette = style.palette

    def pick(truecolor: str, fallback: str) -> str:
        return style.palette_code(truecolor, fallback)

    text = pick(palette.user_message_text_truecolor, palette.user_message_text_fallback)
    output = pick(palette.tool_output_truecolor, palette.tool_output_fallback)
    accent = pick(palette.accent_truecolor, palette.accent_fallback)
    error = pick(palette.error_truecolor, palette.error_fallback)
    success = pick(palette.success_truecolor, palette.success_fallback)
    return RowTheme(
        enabled=style.enabled,
        codes={
            "text": text,
            "toolTitle": text,
            "toolOutput": output,
            "muted": output,
            "toolDiffContext": output,
            "customMessageText": output,
            "dim": pick(palette.dim_truecolor, palette.dim_fallback),
            "accent": accent,
            "customMessageLabel": accent,
            "warning": pick(palette.warning_truecolor, palette.warning_fallback),
            "error": error,
            "toolDiffRemoved": error,
            "success": success,
            "toolDiffAdded": success,
        },
    )


# ---------------------------------------------------------------------------
# render-utils.ts


def _js_str(value: object) -> str | None:
    """Pi ``str()``."""

    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return None


def _js_trim(text: str) -> str:
    return text.strip(JS_WHITESPACE)


def _js_number(value: object) -> str:
    """``${value}`` for a JSON scalar (``String(value)``)."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return ",".join("" if item is None else _js_number(item) for item in value)
    if isinstance(value, Mapping):
        return "[object Object]"
    return str(value)


def _json_compact(value: object) -> str:
    return json.dumps(_js_json_value(value), ensure_ascii=False, separators=(",", ":"))


def _json_pretty(value: object) -> str:
    return json.dumps(_js_json_value(value), ensure_ascii=False, indent=2)


def _js_json_value(value: object) -> object:
    """Integral floats as JSON.stringify prints them (``1`` not ``1.0``)."""

    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, Mapping):
        return {str(key): _js_json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_js_json_value(item) for item in value]
    return value


def _replace_tabs(text: str) -> str:
    return text.replace("\t", "   ")


def _text_output(content: str) -> str:
    """Pi ``getTextOutput`` for pipy's one text block."""

    return sanitize_binary_output(strip_ansi(content)).replace("\r", "")


def _key_hint(theme: RowTheme, key: str, description: str) -> str:
    return theme.fg("dim", key) + theme.fg("muted", f" {description}")


def _more_lines(theme: RowTheme, key: str, remaining: int, extra: str = "") -> str:
    return (
        theme.fg("muted", f"\n... ({remaining} more lines,{extra}")
        + " "
        + _key_hint(theme, key, "to expand")
        + theme.fg("muted", ")")
    )


def _invalid_arg(theme: RowTheme) -> str:
    return theme.fg("error", INVALID_ARG)


def _tool_path(
    theme: RowTheme, raw: str | None, *, empty_fallback: str | None = None
) -> str:
    """Pi ``renderToolPath`` (no hyperlink)."""

    if raw is None:
        return _invalid_arg(theme)
    value = raw or empty_fallback
    if not value:
        return theme.fg("toolOutput", "...")
    return theme.fg("accent", shorten_path(value))


def _title(theme: RowTheme, text: str) -> str:
    return theme.fg("toolTitle", theme.bold(text))


def _file_path_arg(args: Mapping[str, Any]) -> object:
    file_path = args.get("file_path")
    return file_path if file_path is not None else args.get("path")


def _trim_trailing_empty(lines: list[str]) -> list[str]:
    end = len(lines)
    while end > 0 and lines[end - 1] == "":
        end -= 1
    return lines[:end]


def _has_language(raw_path: str | None) -> bool:
    """Pi ``getLanguageFromPath`` says whether the file is highlighted."""

    if not raw_path:
        return False
    extension = raw_path.split(".")[-1].lower()
    return bool(extension) and extension in _HIGHLIGHT_EXTENSIONS


def format_tool_call_with_args(
    theme: RowTheme, title: str, args: object, *, expanded: bool
) -> str:
    """Pi ``formatToolCallWithArgs``: the generic call header."""

    header = _title(theme, title)
    if args is None:
        return header
    entries = list(args.items()) if isinstance(args, Mapping) else [("args", args)]
    if not entries:
        return header
    if expanded:
        lines = []
        for key, value in entries:
            text = value if isinstance(value, str) else _json_pretty(value)
            parts = _replace_tabs(text).replace("\r", "").split("\n")
            lines.append(f"  {key}: " + "\n    ".join(parts))
        return f"{header}\n{theme.fg('muted', chr(10).join(lines))}"
    pairs = " ".join(f"{key}={_json_compact(value)}" for key, value in entries)
    if len(pairs) > _COLLAPSED_ARGS_CHARS:
        pairs = pairs[: _COLLAPSED_ARGS_CHARS - 3] + "..."
    return f"{header} {theme.fg('muted', pairs)}"


# ---------------------------------------------------------------------------
# read


def _read_range(theme: RowTheme, args: Mapping[str, Any]) -> str:
    offset = args.get("offset")
    limit = args.get("limit")
    if offset is None and limit is None:
        return ""
    start = offset if offset is not None else 1
    end: object = ""
    if limit is not None:
        if _is_number(start) and _is_number(limit):
            end = start + limit - 1
        else:
            end = ""
    suffix = f"-{_js_number(end)}" if end else ""
    return theme.fg("warning", f":{_js_number(start)}{suffix}")


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _package_root() -> Path | None:
    """The directory Pi's ``getPackageDir`` names: pipy's checkout root.

    Only a root that holds pipy's README counts, so an installed wheel (no
    README next to the package) classifies no read as docs.
    """

    root = Path(__file__).resolve().parents[3]
    return root if (root / "README.md").is_file() else None


def _compact_read_classification(
    args: Mapping[str, Any], cwd: Path
) -> tuple[str, str] | None:
    """Pi ``getCompactReadClassification``: ``(kind, label)`` or None."""

    raw = _js_str(_file_path_arg(args))
    if not raw:
        return None
    try:
        absolute = resolve_to_cwd(raw, cwd)
    except ValueError:
        return None
    name = absolute.name
    if name == "SKILL.md":
        return "skill", absolute.parent.name or name
    root = _package_root()
    if root is not None:
        relative = posixpath.relpath(str(absolute), str(root))
        if relative not in {"", ".", ".."} and not relative.startswith("../"):
            if relative == "README.md" or relative.startswith(("docs/", "examples/")):
                return "docs", relative
    if name in _COMPACT_RESOURCE_FILE_NAMES:
        relative = posixpath.relpath(str(absolute), str(cwd))
        inside = relative == "." or (
            relative != ".." and not relative.startswith("../")
        )
        return "resource", relative if inside else str(absolute)
    return None


def _read_call(
    theme: RowTheme, args: Mapping[str, Any], cwd: Path, key: str, *, expanded: bool
) -> str:
    classification = None if expanded else _compact_read_classification(args, cwd)
    if classification is None:
        path = _tool_path(theme, _js_str(_file_path_arg(args)))
        return f"{_title(theme, 'read')} {path}{_read_range(theme, args)}"
    kind, label = classification
    hint = theme.fg("dim", f" ({key} to expand)")
    if kind == "skill":
        return (
            theme.fg("customMessageLabel", "\x1b[1m[skill]\x1b[22m ")
            + theme.fg("customMessageText", label)
            + _read_range(theme, args)
            + hint
        )
    return (
        _title(theme, f"read {kind}")
        + " "
        + theme.fg("accent", label)
        + _read_range(theme, args)
        + hint
    )


def _read_result(
    theme: RowTheme,
    args: Mapping[str, Any],
    result: ToolRowResult,
    key: str,
    *,
    expanded: bool,
) -> str:
    if not expanded and not result.is_error:
        return ""
    raw = _js_str(_file_path_arg(args))
    output = _text_output(result.content)
    highlighted = not result.is_error and _has_language(raw)
    lines = _trim_trailing_empty(
        (_replace_tabs(output) if highlighted else output).split("\n")
    )
    shown = lines if expanded else lines[:_READ_PREVIEW_LINES]
    remaining = len(lines) - len(shown)
    text = "\n" + "\n".join(
        _replace_tabs(line)
        if highlighted
        else theme.fg("toolOutput", _replace_tabs(line))
        for line in shown
    )
    if remaining > 0:
        text += _more_lines(theme, key, remaining)
    truncation = _mapping(result.details, "truncation")
    if truncation is not None and truncation.get("truncated"):
        max_bytes = format_size(_int_or(truncation.get("maxBytes"), DEFAULT_MAX_BYTES))
        if truncation.get("firstLineExceedsLimit"):
            warning = f"[First line exceeds {max_bytes} limit]"
        elif truncation.get("truncatedBy") == "lines":
            max_lines = _js_number(
                _int_or(truncation.get("maxLines"), DEFAULT_MAX_LINES)
            )
            warning = (
                f"[Truncated: showing {_js_number(truncation.get('outputLines'))} of "
                f"{_js_number(truncation.get('totalLines'))} lines ({max_lines} line limit)]"
            )
        else:
            warning = (
                f"[Truncated: {_js_number(truncation.get('outputLines'))} lines shown "
                f"({max_bytes} limit)]"
            )
        text += f"\n{theme.fg('warning', warning)}"
    return text


def _mapping(details: Mapping[str, Any] | None, key: str) -> Mapping[str, Any] | None:
    if details is None:
        return None
    value = details.get(key)
    return value if isinstance(value, Mapping) else None


def _int_or(value: object, default: int) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value
        else default
    )


# ---------------------------------------------------------------------------
# grep / find / ls


def _search_path(args: Mapping[str, Any]) -> str | None:
    raw = _js_str(args.get("path"))
    return None if raw is None else shorten_path(raw or ".")


def _grep_call(theme: RowTheme, args: Mapping[str, Any]) -> str:
    pattern = _js_str(args.get("pattern"))
    path = _search_path(args)
    glob = _js_str(args.get("glob"))
    text = (
        _title(theme, "grep")
        + " "
        + (
            _invalid_arg(theme)
            if pattern is None
            else theme.fg("accent", f"/{pattern}/")
        )
        + theme.fg("toolOutput", f" in {_invalid_arg(theme) if path is None else path}")
    )
    if glob:
        text += theme.fg("toolOutput", f" ({glob})")
    if "limit" in args:
        text += theme.fg("toolOutput", f" limit {_js_number(args['limit'])}")
    return text


def _find_call(theme: RowTheme, args: Mapping[str, Any]) -> str:
    pattern = _js_str(args.get("pattern"))
    path = _search_path(args)
    text = (
        _title(theme, "find")
        + " "
        + (_invalid_arg(theme) if pattern is None else theme.fg("accent", pattern))
        + theme.fg("toolOutput", f" in {_invalid_arg(theme) if path is None else path}")
    )
    if "limit" in args:
        text += theme.fg("toolOutput", f" (limit {_js_number(args['limit'])})")
    return text


def _ls_call(theme: RowTheme, args: Mapping[str, Any]) -> str:
    path = _tool_path(theme, _js_str(args.get("path")), empty_fallback=".")
    text = f"{_title(theme, 'ls')} {path}"
    if "limit" in args:
        text += theme.fg("toolOutput", f" (limit {_js_number(args['limit'])})")
    return text


def _search_result(
    theme: RowTheme,
    result: ToolRowResult,
    key: str,
    *,
    expanded: bool,
    preview_lines: int,
    warnings: list[str],
) -> str:
    output = _js_trim(_text_output(result.content))
    text = ""
    if output:
        lines = output.split("\n")
        shown = lines if expanded else lines[:preview_lines]
        remaining = len(lines) - len(shown)
        text += "\n" + "\n".join(theme.fg("toolOutput", line) for line in shown)
        if remaining > 0:
            text += _more_lines(theme, key, remaining)
    if warnings:
        text += f"\n{theme.fg('warning', '[Truncated: ' + ', '.join(warnings) + ']')}"
    return text


def _limit_warnings(
    details: Mapping[str, Any] | None, limit_key: str, label: str
) -> list[str]:
    warnings: list[str] = []
    if details is None:
        return warnings
    limit = details.get(limit_key)
    if limit:
        warnings.append(f"{_js_number(limit)} {label} limit")
    truncation = _mapping(details, "truncation")
    if truncation is not None and truncation.get("truncated"):
        max_bytes = _int_or(truncation.get("maxBytes"), DEFAULT_MAX_BYTES)
        warnings.append(f"{format_size(max_bytes)} limit")
    return warnings


# ---------------------------------------------------------------------------
# write


def _write_call(
    theme: RowTheme, args: Mapping[str, Any], key: str, *, expanded: bool
) -> str:
    raw = _js_str(_file_path_arg(args))
    content = _js_str(args.get("content"))
    text = f"{_title(theme, 'write')} {_tool_path(theme, raw)}"
    if content is None:
        return (
            text + f"\n\n{theme.fg('error', '[invalid content arg - expected string]')}"
        )
    if not content:
        return text
    highlighted = _has_language(raw)
    display = content.replace("\r", "")
    lines = _trim_trailing_empty(
        (_replace_tabs(display) if highlighted else display).split("\n")
    )
    total = len(lines)
    shown = lines if expanded else lines[:_WRITE_PREVIEW_LINES]
    remaining = total - len(shown)
    text += "\n\n" + "\n".join(
        line if highlighted else theme.fg("toolOutput", _replace_tabs(line))
        for line in shown
    )
    if remaining > 0:
        text += _more_lines(theme, key, remaining, f" {total} total,")
    return text


def _error_text(result: ToolRowResult) -> str:
    return result.content


# ---------------------------------------------------------------------------
# edit


def _edit_preview_input(args: Mapping[str, Any]) -> tuple[str, list[Edit]] | None:
    """Pi ``getRenderablePreviewInput``."""

    path = args.get("path")
    if not isinstance(path, str):
        path = args.get("file_path")
    if not isinstance(path, str) or not path:
        return None
    edits = args.get("edits")
    if (
        isinstance(edits, list)
        and edits
        and all(
            isinstance(edit, Mapping)
            and isinstance(edit.get("oldText"), str)
            and isinstance(edit.get("newText"), str)
            for edit in edits
        )
    ):
        return path, [Edit(edit["oldText"], edit["newText"]) for edit in edits]
    old_text, new_text = args.get("oldText"), args.get("newText")
    if isinstance(old_text, str) and isinstance(new_text, str):
        return path, [Edit(old_text, new_text)]
    return None


def compute_edit_preview(args: Mapping[str, Any], cwd: Path) -> EditPreview | None:
    """Pi ``computeEditsDiff`` for the call's arguments, or None.

    Runs when a live call's arguments are complete, before the tool runs, so
    the row shows the diff the edit is about to make (or why it will fail).
    """

    preview_input = _edit_preview_input(args)
    if preview_input is None:
        return None
    path, edits = preview_input
    try:
        absolute = resolve_to_cwd(path, cwd)
        try:
            with open(absolute, "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            code = error_code(exc)
            detail = f"Error code: {code}" if code else str(exc)
            return EditPreview(error=f"Could not edit file: {path}. {detail}.")
        _bom, content = split_bom(raw.decode("utf-8", errors="replace"))
        base, new = apply_edits_to_normalized_content(
            normalize_to_lf(content), edits, path
        )
        diff, first_changed = generate_diff_string(base, new)
    except (EditError, ValueError) as exc:
        return EditPreview(error=str(exc))
    return EditPreview(diff=diff, first_changed_line=first_changed)


def _diff_line(line: str) -> tuple[str, str, str] | None:
    """Pi ``parseDiffLine``: ``/^([+-\\s])(\\s*\\d*)\\s(.*)$/``."""

    match = _DIFF_LINE.match(line)
    if match is None:
        return None
    return match.group(1), match.group(2), match.group(3)


def _intra_line(theme: RowTheme, old: str, new: str) -> tuple[str, str]:
    """Pi ``renderIntraLineDiff``: inverse on the changed words."""

    removed = ""
    added = ""
    first_removed = True
    first_added = True
    for part in diff_words(old, new):
        value = part.value
        if part.removed:
            if first_removed:
                leading = value[: len(value) - len(value.lstrip(JS_WHITESPACE))]
                value = value[len(leading) :]
                removed += leading
                first_removed = False
            if value:
                removed += theme.inverse(value)
        elif part.added:
            if first_added:
                leading = value[: len(value) - len(value.lstrip(JS_WHITESPACE))]
                value = value[len(leading) :]
                added += leading
                first_added = False
            if value:
                added += theme.inverse(value)
        else:
            removed += value
            added += value
    return removed, added


def render_diff(theme: RowTheme, diff_text: str) -> str:
    """Pi ``renderDiff``: coloured diff lines, inverse spans on a one-line edit."""

    lines = diff_text.split("\n")
    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        parsed = _diff_line(line)
        if parsed is None:
            result.append(theme.fg("toolDiffContext", line))
            index += 1
            continue
        prefix, number, content = parsed
        if prefix == "-":
            removed: list[tuple[str, str]] = []
            while index < len(lines):
                current = _diff_line(lines[index])
                if current is None or current[0] != "-":
                    break
                removed.append((current[1], current[2]))
                index += 1
            added: list[tuple[str, str]] = []
            while index < len(lines):
                current = _diff_line(lines[index])
                if current is None or current[0] != "+":
                    break
                added.append((current[1], current[2]))
                index += 1
            if len(removed) == 1 and len(added) == 1:
                old_line, new_line = _intra_line(
                    theme, _replace_tabs(removed[0][1]), _replace_tabs(added[0][1])
                )
                result.append(
                    theme.fg("toolDiffRemoved", f"-{removed[0][0]} {old_line}")
                )
                result.append(theme.fg("toolDiffAdded", f"+{added[0][0]} {new_line}"))
            else:
                result.extend(
                    theme.fg("toolDiffRemoved", f"-{num} {_replace_tabs(text)}")
                    for num, text in removed
                )
                result.extend(
                    theme.fg("toolDiffAdded", f"+{num} {_replace_tabs(text)}")
                    for num, text in added
                )
        elif prefix == "+":
            result.append(
                theme.fg("toolDiffAdded", f"+{number} {_replace_tabs(content)}")
            )
            index += 1
        else:
            result.append(
                theme.fg("toolDiffContext", f" {number} {_replace_tabs(content)}")
            )
            index += 1
    return "\n".join(result)


def _edit_header_bg(state: ToolRowState) -> RowBackground:
    """Pi ``getEditHeaderBg``: the preview decides first."""

    if state.preview is not None:
        return "error" if state.preview.error is not None else "success"
    if state.settled_error:
        return "error"
    return "pending"


def apply_edit_result(state: ToolRowState) -> None:
    """Pi edit ``renderResult`` step 1: the result updates the call box."""

    result = state.result
    if result is None:
        return
    diff = (
        None
        if result.is_error or result.details is None
        else result.details.get("diff")
    )
    if isinstance(diff, str):
        first = result.details.get("firstChangedLine") if result.details else None
        state.preview = EditPreview(
            diff=diff,
            first_changed_line=first if isinstance(first, int) else None,
        )
    state.settled_error = result.is_error


def _edit_rows(state: ToolRowState, theme: RowTheme) -> list[RowLine]:
    """The edit box (its own background) and result lines outside it."""

    bg = _edit_header_bg(state)
    raw = _js_str(_file_path_arg(state.args))
    box = [f"{_title(theme, 'edit')} {_tool_path(theme, raw)}"]
    preview = state.preview
    if preview is not None:
        body = (
            theme.fg("error", preview.error)
            if preview.error is not None
            else render_diff(theme, preview.diff or "")
        )
        box.extend(["", body])
    rows = [RowLine("", bg), *_text_rows("\n".join(box), bg), RowLine("", bg)]
    output = _edit_result(state, theme)
    if output:
        rows.append(RowLine("", "none"))
        rows.extend(_text_rows(output, "none"))
    return rows


def _edit_result(state: ToolRowState, theme: RowTheme) -> str | None:
    """Pi ``formatEditResult`` against the (updated) preview."""

    result = state.result
    if result is None:
        return None
    preview = state.preview
    preview_diff = preview.diff if preview is not None else None
    preview_error = preview.error if preview is not None else None
    if result.is_error:
        text = result.content
        if not text or text == preview_error:
            return None
        return theme.fg("error", text)
    diff = result.details.get("diff") if result.details is not None else None
    if isinstance(diff, str) and diff and diff != preview_diff:
        return render_diff(theme, diff)
    return None


# ---------------------------------------------------------------------------
# bash


def _format_duration(seconds: float) -> str:
    """Pi bash ``formatDuration`` (from milliseconds)."""

    if seconds < 60:
        exact = Decimal(seconds).quantize(Decimal("0.1"), ROUND_HALF_UP)
        return f"{exact}s"
    total = int(seconds)
    minutes, remainder = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {remainder}s"
    return f"{minutes // 60}h {minutes % 60}m {remainder}s"


def _bash_call(theme: RowTheme, args: Mapping[str, Any]) -> str:
    command = _js_str(args.get("command"))
    timeout = args.get("timeout")
    suffix = (
        theme.fg("muted", f" (timeout {_js_number(timeout)}s)")
        if timeout not in (None, 0, False, "")
        else ""
    )
    if command is None:
        display = _invalid_arg(theme)
    elif command:
        display = command
    else:
        display = theme.fg("toolOutput", "...")
    return theme.fg("toolTitle", theme.bold(f"$ {display}")) + suffix


def _bash_result_rows(
    state: ToolRowState,
    result: ToolRowResult,
    theme: RowTheme,
    key: str,
    bg: RowBackground,
    *,
    expanded: bool,
    partial: bool,
    width: int,
    now: float,
) -> list[RowLine]:
    output = _js_trim(_text_output(result.content))
    details = result.details
    truncation = _mapping(details, "truncation")
    full_path = details.get("fullOutputPath") if details is not None else None
    full_path = full_path if isinstance(full_path, str) and full_path else None
    if (
        not partial
        and truncation is not None
        and truncation.get("truncated")
        and full_path
        and output.endswith("]")
    ):
        start = output.rfind("\n\n[")
        if start != -1 and full_path in output[start:]:
            output = output[:start].rstrip(JS_WHITESPACE)
    rows: list[RowLine] = []
    if output:
        styled = "\n".join(theme.fg("toolOutput", line) for line in output.split("\n"))
        if expanded:
            rows.extend(_text_rows(f"\n{styled}", bg))
        else:
            rows.extend(_bash_preview_rows(styled, theme, key, bg, width))
    warnings = _bash_warnings(truncation, full_path)
    if warnings:
        rows.extend(
            _text_rows(f"\n{theme.fg('warning', '[' + '. '.join(warnings) + ']')}", bg)
        )
    if state.duration_seconds is not None and not partial:
        took = theme.fg("muted", f"Took {_format_duration(state.duration_seconds)}")
        rows.extend(_text_rows(f"\n{took}", bg))
    elif partial and state.started_at is not None:
        # Pi: `Elapsed` while partial, re-rendered every second.
        end = state.ended_at if state.ended_at is not None else now
        elapsed = max(0.0, end - state.started_at)
        text = theme.fg("muted", f"Elapsed {_format_duration(elapsed)}")
        rows.extend(_text_rows(f"\n{text}", bg))
    return rows


def _bash_preview_rows(
    styled: str, theme: RowTheme, key: str, bg: RowBackground, width: int
) -> list[RowLine]:
    """The collapsed output: the last 5 visual lines at the box's width."""

    content_width = max(1, width - 2)
    visual = _visual_lines(styled, content_width)
    skipped = max(0, len(visual) - _BASH_PREVIEW_LINES)
    rows = [RowLine("", bg)]
    if skipped:
        hint = (
            theme.fg("muted", f"... ({skipped} earlier lines,")
            + " "
            + _key_hint(theme, key, "to expand")
            + theme.fg("muted", ")")
        )
        rows.append(RowLine(_truncate_to_width(hint, content_width), bg, False))
    rows.extend(RowLine(line, bg, False) for line in visual[skipped:])
    return rows


def _bash_warnings(
    truncation: Mapping[str, Any] | None, full_path: str | None
) -> list[str]:
    warnings: list[str] = []
    if full_path:
        warnings.append(f"Full output: {full_path}")
    if truncation is not None and truncation.get("truncated"):
        if truncation.get("truncatedBy") == "lines":
            warnings.append(
                f"Truncated: showing {_js_number(truncation.get('outputLines'))} of "
                f"{_js_number(truncation.get('totalLines'))} lines"
            )
        else:
            max_bytes = _int_or(truncation.get("maxBytes"), DEFAULT_MAX_BYTES)
            warnings.append(
                f"Truncated: {_js_number(truncation.get('outputLines'))} lines shown "
                f"({format_size(max_bytes)} limit)"
            )
    return warnings


def _visual_lines(text: str, width: int) -> list[str]:
    """Pi ``truncateToVisualLines`` input: ``Text(text, 0, 0).render(width)``."""

    if not text or _js_trim(text) == "":
        return []
    return wrap_text_with_ansi(_replace_tabs(text), width)


def _truncate_to_width(text: str, width: int) -> str:
    """Pi ``truncateToWidth(text, width, "...")`` for the hint line."""

    if visible_width(text) <= width:
        return text
    kept = []
    count = 0
    index = 0
    limit = max(0, width - 3)
    while index < len(text) and count < limit:
        if text[index] == "\x1b":
            end = text.find("m", index)
            if end == -1:
                break
            kept.append(text[index : end + 1])
            index = end + 1
            continue
        kept.append(text[index])
        count += 1
        index += 1
    return "".join(kept) + "\x1b[0m" + "..."[: max(0, width - count)]


# ---------------------------------------------------------------------------
# Composition (ToolExecutionComponent)


def _text_rows(text: str, bg: RowBackground) -> list[RowLine]:
    """A Pi ``Text(text, 0, 0)``: nothing when blank, else its lines."""

    if not text or _js_trim(text) == "":
        return []
    return [RowLine(_replace_tabs(line), bg) for line in split_lines_with_carry(text)]


def _result_fallback(
    theme: RowTheme, result: ToolRowResult, key: str, *, expanded: bool
) -> str:
    """Pi ``createResultFallback``."""

    output = _text_output(result.content)
    if not output:
        return ""
    lines = output.split("\n")
    shown = lines if expanded else lines[:_FALLBACK_PREVIEW_LINES]
    remaining = len(lines) - len(shown)
    text = "\n".join(theme.fg("toolOutput", line) for line in shown)
    if remaining > 0:
        text += _more_lines(theme, key, remaining)
    return text


@dataclass(frozen=True, slots=True)
class RowRenderInputs:
    """The live values a row is drawn with."""

    expanded: bool
    width: int
    theme: RowTheme
    expand_key: str = "ctrl+o"
    # The `ToolRenderTheme` handed to extension renderers.
    extension_theme: object = None
    # The monotonic clock reading a running `bash` row's `Elapsed` uses.
    now: float = 0.0


def _render_extension(
    state: ToolRowState,
    inputs: RowRenderInputs,
    *,
    result: ToolRowResult | None,
) -> list[str] | None:
    """Run an extension ``render_call``/``render_result`` fail-soft.

    Called again on every redraw with the retained arguments, per-call state,
    result and details, like Pi's ``updateDisplay``. None falls back to Pi's
    generic header or result preview.
    """

    from pipy_harness.native.extension_types import ToolRenderContext
    from pipy_harness.native.tool_renderers import render_tool_phase

    tool = state.extension
    if tool is None:
        return None
    renderer = tool.render_result if result is not None else tool.render_call
    if renderer is None:
        return None
    ctx = ToolRenderContext(
        tool_name=state.tool_name,
        args=state.args,
        is_result=result is not None,
        is_error=result.is_error if result is not None else False,
        content=result.content if result is not None else None,
        details=result.details if result is not None else None,
        expanded=inputs.expanded,
        # Pi renders a box child at the box's content width.
        width=max(1, inputs.width - 2),
        theme=inputs.extension_theme,
        state=state.renderer_state,
    )
    return render_tool_phase(renderer, ctx)


def render_tool_row(
    state: ToolRowState, inputs: RowRenderInputs
) -> tuple[RowLine, ...]:
    """Draw ``state`` like Pi's ``ToolExecutionComponent`` (box + spacer).

    Pi draws a spacer above every row; pipy's rows end with it instead, as
    every other pipy history block does.
    """

    partial = state.result is None
    result = state.result
    if result is None and (state.partial_output or _bash_started(state)):
        # Pi's bash tool sends an empty update as it starts, so its row shows
        # the result part (`Elapsed`) before any output.
        result = ToolRowResult(state.partial_output, None, False)
    if is_builtin_edit(state):
        return (*_edit_rows(state, inputs.theme), RowLine("", "none"))
    bg: RowBackground = "pending"
    if not partial:
        bg = "error" if result is not None and result.is_error else "success"
    rows: list[RowLine] = [RowLine("", bg), *_call_rows(state, inputs, bg)]
    if result is not None:
        rows.extend(_result_rows(state, inputs, result, bg, partial=partial))
    rows.append(RowLine("", bg))
    rows.append(RowLine("", "none"))
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class SummaryBox:
    """A ``[compaction]``/``[branch]`` row's texts (Pi's summary components).

    Kept instead of styled lines, so the row is drawn again with the active
    theme on Ctrl+O and resize, like a tool row.
    """

    label: str
    # The collapsed text before the `ctrl+o` hint.
    collapsed: str
    header: str
    summary: str


def render_summary_box(
    box: SummaryBox, theme: RowTheme, *, expanded: bool, expand_key: str = "ctrl+o"
) -> tuple[RowLine, ...]:
    """Pi ``Box(1, 1)`` on ``customMessageBg``: the bold label, a spacer, then
    the collapsed hint or the bold header and the summary (Pi renders the
    expanded text as Markdown; pipy shows its lines as they are)."""

    def text(value: str) -> str:
        return theme.fg("customMessageText", value)

    if expanded:
        body = [theme.bold(text(box.header)), "", *map(text, box.summary.splitlines())]
    else:
        body = [text(box.collapsed) + theme.fg("dim", expand_key) + text(" to expand)")]
    return (
        RowLine("", "custom"),
        RowLine(theme.fg("customMessageLabel", theme.bold(box.label)), "custom"),
        RowLine("", "custom"),
        *(RowLine(line, "custom") for line in body),
        RowLine("", "custom"),
        RowLine("", "none"),
    )


def _bash_started(state: ToolRowState) -> bool:
    return (
        state.extension is None
        and state.tool_name == "bash"
        and state.started_at is not None
    )


def is_running_builtin_bash(state: ToolRowState) -> bool:
    """Whether the row is a live built-in ``bash`` call still running."""

    return _bash_started(state) and state.result is None and state.ended_at is None


def is_builtin_edit(state: ToolRowState) -> bool:
    """Whether the row is the built-in ``edit`` (Pi ``renderShell: "self"``)."""

    return state.extension is None and state.tool_name == "edit"


def _call_rows(
    state: ToolRowState, inputs: RowRenderInputs, bg: RowBackground
) -> list[RowLine]:
    theme = inputs.theme
    name = state.tool_name
    if state.extension is not None:
        lines = _render_extension(state, inputs, result=None)
        if lines is not None:
            return [RowLine(line, bg, False) for line in lines]
    if state.extension is None and name in BUILTIN_RENDERED_TOOLS:
        text = _builtin_call(state, inputs)
    else:
        text = format_tool_call_with_args(
            theme, name, state.args, expanded=inputs.expanded
        )
    return _text_rows(text, bg)


def _builtin_call(state: ToolRowState, inputs: RowRenderInputs) -> str:
    theme = inputs.theme
    args = state.args
    name = state.tool_name
    if name == "read":
        return _read_call(
            theme, args, state.cwd, inputs.expand_key, expanded=inputs.expanded
        )
    if name == "grep":
        return _grep_call(theme, args)
    if name == "find":
        return _find_call(theme, args)
    if name == "ls":
        return _ls_call(theme, args)
    if name == "write":
        return _write_call(theme, args, inputs.expand_key, expanded=inputs.expanded)
    return _bash_call(theme, args)


def _result_rows(
    state: ToolRowState,
    inputs: RowRenderInputs,
    result: ToolRowResult,
    bg: RowBackground,
    *,
    partial: bool,
) -> list[RowLine]:
    theme = inputs.theme
    key = inputs.expand_key
    expanded = inputs.expanded
    name = state.tool_name
    if state.extension is not None:
        lines = _render_extension(state, inputs, result=result)
        if lines is not None:
            return [RowLine(line, bg, False) for line in lines]
        return _text_rows(_result_fallback(theme, result, key, expanded=expanded), bg)
    if name not in BUILTIN_RENDERED_TOOLS:
        return _text_rows(_result_fallback(theme, result, key, expanded=expanded), bg)
    if name == "bash":
        return _bash_result_rows(
            state,
            result,
            theme,
            key,
            bg,
            expanded=expanded,
            partial=partial,
            width=inputs.width,
            now=inputs.now,
        )
    if name == "read":
        text = _read_result(theme, state.args, result, key, expanded=expanded)
    elif name == "write":
        text = (
            f"\n{theme.fg('error', _error_text(result))}"
            if result.is_error and _error_text(result)
            else ""
        )
    elif name == "grep":
        warnings = _limit_warnings(result.details, "matchLimitReached", "matches")
        if result.details is not None and result.details.get("linesTruncated"):
            warnings.append("some lines truncated")
        text = _search_result(
            theme,
            result,
            key,
            expanded=expanded,
            preview_lines=_GREP_PREVIEW_LINES,
            warnings=warnings,
        )
    elif name == "find":
        text = _search_result(
            theme,
            result,
            key,
            expanded=expanded,
            preview_lines=_FIND_LS_PREVIEW_LINES,
            warnings=_limit_warnings(result.details, "resultLimitReached", "results"),
        )
    else:
        text = _search_result(
            theme,
            result,
            key,
            expanded=expanded,
            preview_lines=_FIND_LS_PREVIEW_LINES,
            warnings=_limit_warnings(result.details, "entryLimitReached", "entries"),
        )
    return _text_rows(text, bg)


def encode_rows(rows: tuple[RowLine, ...]) -> tuple[str, ...]:
    """Rows as the ``tool_box`` history block's lines."""

    return tuple(encode_tool_box_line(row.text, row.bg, wrap=row.wrap) for row in rows)


def parse_arguments(arguments_json: str) -> dict[str, Any]:
    try:
        parsed = json.loads(arguments_json)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = [
    "BUILTIN_RENDERED_TOOLS",
    "EditPreview",
    "RowLine",
    "RowRenderInputs",
    "RowTheme",
    "SummaryBox",
    "ToolRowResult",
    "ToolRowState",
    "apply_edit_result",
    "compute_edit_preview",
    "encode_rows",
    "format_tool_call_with_args",
    "is_builtin_edit",
    "is_running_builtin_bash",
    "parse_arguments",
    "render_diff",
    "render_summary_box",
    "render_tool_row",
    "row_theme",
]

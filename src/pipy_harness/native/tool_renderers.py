"""Concrete tool-render theme + fail-soft dispatch for extension tool renderers."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any, ClassVar, TextIO

from pipy_harness.native.agent import (
    AgentCancellationReason,
    AgentToolCall,
)
from pipy_harness.native.chrome import (
    ChromeStyle,
    chrome_style_for,
    chrome_width,
    terminal_supports_truecolor,
)
from pipy_harness.native.extension_runtime import (
    ExtensionTool,
    ThemeColor,
    ToolRenderContext,
    ToolRenderTheme,
    coerce_tool_render_lines,
)
from pipy_harness.native.provider import StreamChunkSink


class _PaletteToolRenderTheme:
    """A ToolRenderTheme backed by a ChromeStyle's palette."""

    def __init__(self, style: ChromeStyle) -> None:
        self._style = style

    def _code(self, color: ThemeColor) -> str:
        p = self._style.palette
        table = {
            "text": (p.user_message_text_truecolor, "39"),
            "accent": (p.accent_truecolor, p.accent_fallback),
            "success": (p.success_truecolor, p.success_fallback),
            "warning": (p.warning_truecolor, p.warning_fallback),
            "error": (p.error_truecolor, p.error_fallback),
            "dim": (p.dim_truecolor, p.dim_fallback),
        }
        truecolor_code, fallback_code = table.get(color, table["text"])
        return self._style.palette_code(truecolor_code, fallback_code)

    def fg(self, color: ThemeColor, text: str) -> str:
        if not self._style.enabled:
            return text
        return f"\x1b[{self._code(color)}m{text}\x1b[0m"

    def bold(self, text: str) -> str:
        if not self._style.enabled:
            return text
        return f"\x1b[1m{text}\x1b[0m"

    def dim(self, text: str) -> str:
        return self.fg("dim", text)


def _parse_tool_input(arguments_json: str) -> dict[str, object]:
    """Parse a tool call's argument JSON into a dict for hook inspection.

    A non-object or unparseable payload yields an empty mapping; hooks
    must tolerate missing keys. The parsed input is for live hook
    inspection only and is not archived.
    """

    try:
        parsed = json.loads(arguments_json)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_tool_render_theme(style: ChromeStyle) -> ToolRenderTheme:
    return _PaletteToolRenderTheme(style)


def render_tool_phase(
    renderer: Callable[[ToolRenderContext], object],
    ctx: ToolRenderContext,
) -> list[str] | None:
    """Run one extension tool renderer fail-soft.

    Returns the rendered lines, or None to signal the caller should fall back
    to pipy's default rendering. A renderer that raises, returns a non-
    component, whose render() raises, or returns an uncoercible value all
    yield None. KeyboardInterrupt/SystemExit propagate."""

    try:
        component = renderer(ctx)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 - a bad renderer falls back
        return None
    render = getattr(component, "render", None)
    if not callable(render):
        return None
    try:
        produced = render(ctx.width)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 - a bad render() falls back
        return None
    coerced = coerce_tool_render_lines(produced)
    if coerced is None:
        return None
    return list(coerced)


_CHROME_TRUNCATION_MARKER = "  … (chrome truncated)"


def render_chrome_component(
    source: object,
    *,
    width: int,
    max_lines: int,
) -> list[str] | None:
    """Render a chrome source (lines, str, or zero-arg factory) fail-soft.

    Coercion order mirrors ``coerce_tool_render_lines`` (str special-cased
    before the generic Sequence path). ``source`` may be:
      * a callable factory taking no args and returning a component with
        ``render(width) -> Sequence[str]``;
      * a bare ``str`` (split on newlines) or any other ``Sequence[str]``.
    Returns the bounded lines, or ``None`` to signal the caller to fall back
    (clear the region / use the built-in). KeyboardInterrupt/SystemExit
    propagate."""

    component: object | None = None
    if callable(source):
        try:
            component = source()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - a bad factory falls back
            return None
    elif not isinstance(source, (str, bytes, bytearray)) and callable(
        getattr(source, "render", None)
    ):
        # A direct ChromeComponent object (e.g. lines_component(...)).
        component = source
    if component is not None:
        render = getattr(component, "render", None)
        if not callable(render):
            return None
        try:
            produced = render(width)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - a bad render() falls back
            return None
    else:
        produced = source
    coerced = coerce_tool_render_lines(produced)
    if coerced is None:
        return None
    lines = list(coerced)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines.append(_CHROME_TRUNCATION_MARKER)
    return lines


class _ToolLoopRenderer:
    """Pi-parity live rendering for the bounded tool loop.

    Streams provider text deltas to ``error_stream`` as they arrive, then
    paints a styled header/body block around each tool invocation. Falls
    back to plain text on non-TTY streams or when ``NO_COLOR`` is set,
    so captured logs stay deterministic and tests can pin behavior.

    Style intent:
    - Streamed assistant text: dim cyan italic prefix `assistant >`, then
      raw deltas printed verbatim (the provider already shapes the text).
    - Tool call header: italic green prefix `→ <tool>(<arg-preview>)`.
    - Tool result body: dim/quiet block prefixed with `↳`, indented two
      spaces per line, with a leading `[error]` tag on failures.

    The renderer exposes ``streamed_any`` so the loop can avoid double-
    printing the final buffered text when streaming already covered it.
    """

    _ANSI_BOLD = "\x1b[1m"
    _ANSI_DIM = "\x1b[2m"
    _ANSI_ITALIC = "\x1b[3m"
    _ANSI_GREEN = "\x1b[32m"
    _ANSI_RED = "\x1b[31m"
    _ANSI_CYAN = "\x1b[36m"
    _ANSI_YELLOW = "\x1b[33m"
    _ANSI_RESET = "\x1b[0m"
    # Pi's `toolPendingBg` theme uses a *very* muted dark-olive panel
    # behind each tool block — almost a gray with a hint of green, not
    # a saturated forest green. We pin the same intent with a truecolor
    # RGB triplet (`\x1b[48;2;28;42;30m`) on terminals that advertise
    # 24-bit color, falling back to 256-color index 235 (a near-black
    # gray) when truecolor is unavailable. `\x1b[K` fills the rest of
    # the row with the same background so each panel row reads as a
    # contiguous strip.
    _ANSI_BG_TOOL_PANEL_TRUECOLOR = "\x1b[48;2;28;42;30m"
    _ANSI_BG_TOOL_PANEL_256 = "\x1b[48;5;235m"
    # Pi's `userMessageBg` theme paints a muted slate-gray panel
    # spanning the full row behind the user's typed message so it
    # reads as a chat bubble distinct from the green tool panel. The
    # bubble is three rows tall: one blank padding row above the text,
    # the text row itself, and one blank padding row below — mirror
    # pi by emitting all three with the same background and
    # `\x1b[K` clear-to-EOL.
    _ANSI_BG_USER_MESSAGE_TRUECOLOR = "\x1b[48;2;52;53;65m"
    _ANSI_BG_USER_MESSAGE_256 = "\x1b[48;5;237m"
    _ANSI_CLEAR_EOL = "\x1b[K"
    _ANSI_CURSOR_UP_ONE = "\x1b[1A"
    _ANSI_CLEAR_LINE = "\x1b[2K"

    _RESULT_LINE_PREVIEW_MAX_LENGTH = 12
    _ARGUMENT_VALUE_PREVIEW_LIMIT = 80

    def __init__(
        self,
        *,
        output_stream: TextIO,
        error_stream: TextIO,
        tool_renderers: "Mapping[str, ExtensionTool] | None" = None,
        render_details_sink: "MutableMapping[str, object] | None" = None,
    ) -> None:
        self._output_stream = output_stream
        self._error_stream = error_stream
        self._terminal_lock = threading.Lock()
        self._cursor_control_enabled = self._compute_cursor_control_enabled(
            error_stream
        )
        self._enabled = self._compute_enabled(error_stream)
        self._tool_panel_bg = (
            self._ANSI_BG_TOOL_PANEL_TRUECOLOR
            if self._supports_truecolor()
            else self._ANSI_BG_TOOL_PANEL_256
        )
        self._user_message_bg = (
            self._ANSI_BG_USER_MESSAGE_TRUECOLOR
            if self._supports_truecolor()
            else self._ANSI_BG_USER_MESSAGE_256
        )
        self._stream_active = False
        self._stream_emitted_any = False
        self._stream_ended_with_newline = False
        self._streamed_any = False
        self._working_shown = False
        self._working_mode = ""
        self._stop_working_event: threading.Event | None = None
        self._working_thread: threading.Thread | None = None
        self._reasoning_active = False
        self._reasoning_emitted_any = False
        self._tool_renderers = dict(tool_renderers or {})
        self._render_details_sink = render_details_sink
        self._pending_render: dict[str, object] | None = None
        self._last_tool_name = ""

    def refresh_tool_renderers(
        self, tool_renderers: "Mapping[str, ExtensionTool]"
    ) -> None:
        self._tool_renderers = dict(tool_renderers)

    @staticmethod
    def _compute_enabled(stream: TextIO) -> bool:
        if "NO_COLOR" in os.environ:
            return False
        term = os.environ.get("TERM", "").lower()
        if term == "dumb":
            return False
        return bool(getattr(stream, "isatty", lambda: False)())

    @staticmethod
    def _compute_cursor_control_enabled(stream: TextIO) -> bool:
        term = os.environ.get("TERM", "").lower()
        if term == "dumb":
            return False
        return bool(getattr(stream, "isatty", lambda: False)())

    @staticmethod
    def _supports_truecolor() -> bool:
        """Return True when the active terminal advertises 24-bit color.

        Truecolor lets us pin Pi's exact muted-olive panel RGB. Falls
        back to a 256-color near-black on TERM strings that only carry
        eight, sixteen, or 256 color slots. RGB is used only when
        COLORTERM or TERM explicitly advertises truecolor/direct color.
        """

        return terminal_supports_truecolor(
            os.environ.get("TERM", ""), os.environ.get("COLORTERM", "")
        )

    @property
    def streamed_any(self) -> bool:
        return self._streamed_any

    @property
    def stream_sink(self) -> StreamChunkSink:
        return self._handle_stream_chunk

    def start_assistant_message(self) -> None:
        """Reset and display provider-turn chrome for a canonical message start."""

        self.begin_provider_turn()
        self.show_working()

    def begin_provider_turn(self) -> None:
        self._close_reasoning()
        self._stream_active = False
        self._stream_emitted_any = False
        self._stream_ended_with_newline = False
        self._working_shown = False
        self._working_mode = ""
        self._reasoning_emitted_any = False

    @property
    def reasoning_sink(self) -> StreamChunkSink:
        return self.handle_reasoning_chunk

    _SPINNER_FRAMES: ClassVar[tuple[str, ...]] = (
        "⠋",
        "⠙",
        "⠹",
        "⠸",
        "⠼",
        "⠴",
        "⠦",
        "⠧",
        "⠇",
        "⠏",
    )
    _SPINNER_INTERVAL_SECONDS: ClassVar[float] = 0.08

    def show_working(self) -> None:
        """Animate a Pi-shape `⠋ Working...` line on the error stream.

        A background thread cycles through ``_SPINNER_FRAMES`` every
        80 ms and rewrites the line in place. The visible loader sits one
        row below the post-user-message cursor, matching Pi's active-turn
        spacing, while the terminal cursor returns to the row where streamed
        assistant text should begin. The thread is daemonized so it never
        blocks process exit, and stopped via ``_stop_working_event`` before
        the next visible block (stream text, tool block, or footer redraw)
        lands. On non-TTY streams the line and animation are suppressed
        entirely so captured logs stay deterministic.
        """

        if not self._enabled:
            self._working_shown = False
            return
        self._start_working_animation(mode="reserved")

    def _show_stream_working(self) -> None:
        if not self._enabled:
            self._working_shown = False
            return
        self._start_working_animation(mode="stream")

    def _start_working_animation(self, *, mode: str) -> None:
        self._stop_working_event = threading.Event()
        self._working_shown = True
        self._working_mode = mode

        def _animate(stop_event: threading.Event) -> None:
            frame_index = 0
            while not stop_event.is_set():
                glyph = self._SPINNER_FRAMES[frame_index % len(self._SPINNER_FRAMES)]
                marker = self._style(
                    f"{glyph} Working...",
                    self._ANSI_DIM,
                )
                try:
                    with self._terminal_lock:
                        self._error_stream.write(self._working_frame(marker, mode))
                        self._error_stream.flush()
                except (ValueError, OSError):
                    return
                frame_index += 1
                stop_event.wait(self._SPINNER_INTERVAL_SECONDS)

        thread = threading.Thread(
            target=_animate,
            args=(self._stop_working_event,),
            name="pipy-tool-loop-spinner",
            daemon=True,
        )
        self._working_thread = thread
        thread.start()

    @staticmethod
    def _working_frame(marker: str, mode: str) -> str:
        if mode == "stream":
            return f"\x1b7\x1b[2B\r\x1b[K {marker}\x1b8"
        return f"\x1b7\x1b[1B\r\x1b[K {marker}\x1b8"

    @staticmethod
    def _working_clear(mode: str) -> str:
        if mode == "stream":
            return "\x1b7\x1b[2B\r\x1b[K\x1b8"
        return "\x1b7\x1b[1B\r\x1b[K\x1b8"

    def _clear_working(self) -> None:
        if not self._working_shown:
            return
        mode = self._working_mode
        if self._stop_working_event is not None:
            self._stop_working_event.set()
        if self._working_thread is not None:
            self._working_thread.join(timeout=0.2)
        self._stop_working_event = None
        self._working_thread = None
        if self._enabled:
            try:
                with self._terminal_lock:
                    self._error_stream.write(self._working_clear(mode))
                    self._error_stream.flush()
            except (ValueError, OSError):
                pass
        self._working_shown = False
        self._working_mode = ""

    def complete_assistant_message(self, *, has_tool_calls: bool) -> None:
        del has_tool_calls
        self._finish_provider_turn(
            stream_ended_with_newline=self._stream_ended_with_newline
        )

    def _finish_provider_turn(self, *, stream_ended_with_newline: bool) -> None:
        self._clear_working()
        if self._stream_active:
            # Flush a trailing newline so the next render block starts
            # on its own line, even when the provider did not emit one,
            # then a second one so a blank row sits between the last
            # response line and the next input-frame separator, matching
            # pi's spacing below the assistant message.
            if not self._stream_emitted_any or not stream_ended_with_newline:
                self._output_stream.write("\n\n")
            else:
                self._output_stream.write("\n")
            self._output_stream.flush()
        self._stream_active = False

    def fail_assistant_message(self) -> None:
        # Preserve the historical provider-failure bytes: a partial stream is
        # always terminated with two newlines, even when its last delta ended
        # with one. Successful completion instead follows the canonical delta
        # tail through ``complete_assistant_message`` above.
        self._finish_provider_turn(stream_ended_with_newline=False)

    def cancel_assistant_message(self, reason: AgentCancellationReason) -> None:
        self._clear_working()
        if reason is AgentCancellationReason.OPERATOR_ABORT and self._enabled:
            message = self._style(" Operation aborted", "\x1b[38;2;204;102;102m")
            try:
                with self._terminal_lock:
                    self._error_stream.write(f"\n{message}\n")
                    self._error_stream.flush()
            except (ValueError, OSError):
                pass
        elif reason is AgentCancellationReason.OPERATOR_ABORT:
            print("Operation aborted", file=self._error_stream)
        self._stream_active = False

    def _handle_stream_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        if not self._stream_active:
            self._clear_working()
            self._stream_active = True
            # Pi prints the final assistant answer with a one-space
            # left indent and a single blank row above. The bottom
            # padding row of the user-message bubble already provides
            # one of the two visual rows between the bubble text and
            # the answer; emit one more `\n` plus the leading indent
            # here. Subsequent lines within the same stream get their
            # indent from the newline rewrite below.
            with self._terminal_lock:
                self._output_stream.write("\n ")
                self._output_stream.write(chunk.replace("\n", "\n "))
                self._output_stream.flush()
            self._show_stream_working()
        else:
            with self._terminal_lock:
                self._output_stream.write(chunk.replace("\n", "\n "))
                self._output_stream.flush()
        self._stream_emitted_any = True
        self._stream_ended_with_newline = chunk.endswith("\n")
        self._streamed_any = True

    def handle_reasoning_chunk(self, chunk: str) -> None:
        """Render an italic dim reasoning-summary delta inline.

        Pi paints the model's reasoning summary between tool calls with
        an italicized prose voice and renders section titles in bold.
        Pipy mirrors that by routing the codex
        `response.reasoning_summary_text.delta` events through this
        method so the user sees the same "thinking" cues. ``**...**``
        spans inside the chunk are rendered as ANSI bold+italic so
        section titles like `**Investigating pi-mono and pipy**`
        appear as bold prose instead of literal asterisks.
        """

        if not chunk:
            return
        self._clear_working()
        if not self._reasoning_active:
            self._reasoning_active = True
            indent = self._style(" ", self._ANSI_DIM)
            self._error_stream.write("\n" + indent)
        for segment, is_bold in self._split_reasoning_segments(chunk):
            if not segment:
                continue
            if is_bold:
                styled = self._style(
                    segment, self._ANSI_BOLD + self._ANSI_ITALIC + self._ANSI_DIM
                )
            else:
                styled = self._style(segment, self._ANSI_ITALIC + self._ANSI_DIM)
            self._error_stream.write(styled)
        self._error_stream.flush()
        self._reasoning_emitted_any = True

    @staticmethod
    def _split_reasoning_segments(text: str) -> list[tuple[str, bool]]:
        """Split a reasoning chunk into (segment, is_bold) pairs.

        ``**…**`` spans become bold segments; the literal asterisks are
        removed from the rendered output. Unmatched trailing ``**`` is
        emitted verbatim so partial deltas across chunk boundaries do
        not silently drop the open marker.
        """

        segments: list[tuple[str, bool]] = []
        cursor = 0
        while True:
            open_index = text.find("**", cursor)
            if open_index == -1:
                segments.append((text[cursor:], False))
                break
            if open_index > cursor:
                segments.append((text[cursor:open_index], False))
            close_index = text.find("**", open_index + 2)
            if close_index == -1:
                segments.append((text[open_index + 2 :], True))
                break
            segments.append((text[open_index + 2 : close_index], True))
            cursor = close_index + 2
        return segments

    def _close_reasoning(self) -> None:
        if not self._reasoning_active:
            return
        self._error_stream.write("\n")
        self._error_stream.flush()
        self._reasoning_active = False

    def render_user_message(self, text: str) -> None:
        """Paint the submitted user message on the user-message panel.

        Pi's user-message bubble is three rows tall and fills the row
        width: a blank padding row above the text, the text row, and a
        blank padding row below — all painted on the same
        ``userMessageBg`` background. The readline / slash-menu adapter
        has already echoed the typed text to the error stream; we
        overwrite that previous line plus the `print_input_separator`
        row above with `\\x1b[1A\\x1b[2K\\r` and re-render the bubble in
        place. Non-TTY streams skip the rewrite and just leave the
        readline echo in place.
        """

        if not text:
            return
        lines = text.splitlines() or [""]
        if self._cursor_control_enabled:
            # Step back over the readline echo plus the separator row
            # that `print_input_separator` drew above the input area.
            # The readline echo of a single logical line can wrap to
            # multiple visual rows on narrow panes (`ceil(len /
            # width)`), so count visual rows — not logical lines —
            # before clearing, otherwise stale echo fragments stay
            # above the rendered bubble.
            width = max(1, chrome_width(self._error_stream))
            visual_rows = 0
            for line in lines:
                # `len(line) + 1` accounts for the leading prompt-area
                # column pi-parity already reserves; `// width` plus
                # the always-present row itself gives the wrapped
                # count, with empty lines counting as one row.
                effective = max(1, len(line) + 1)
                visual_rows += (effective + width - 1) // width
            self._error_stream.write("\r")
            for _ in range(visual_rows + 1):
                self._error_stream.write(
                    self._ANSI_CURSOR_UP_ONE + self._ANSI_CLEAR_LINE
                )
            self._error_stream.write("\r")
            # Top padding row of the bubble (full-width bg).
            self._error_stream.write(self._user_message_panel_blank_line())
        for line in lines:
            self._error_stream.write(self._user_message_panel_line(line))
        if self._cursor_control_enabled:
            # Bottom padding row of the bubble (full-width bg).
            self._error_stream.write(self._user_message_panel_blank_line())
        self._error_stream.flush()

    def render_buffered_assistant_text(
        self, text: str, *, has_tool_calls: bool
    ) -> None:
        """Render a non-streamed assistant completion from its canonical event."""

        if not has_tool_calls:
            print(text, file=self._output_stream)

    def _user_message_panel_line(self, text: str) -> str:
        """Render the text row of the user-message bubble."""

        if not self._enabled:
            return f" {text}\n"
        # Full-width bg behind the text row. We pad with spaces out to
        # the rendered chrome width instead of relying solely on
        # `\x1b[K` because `tmux capture-pane -e` drops cells that
        # carry attributes but no character — without explicit space
        # characters the bg disappears in screenshots and replay.
        width = chrome_width(self._error_stream)
        padding = " " * max(0, width - len(text) - 1)
        return (
            f"{self._user_message_bg} {text}{padding}{self._ANSI_CLEAR_EOL}"
            f"{self._ANSI_RESET}\n"
        )

    def _user_message_panel_blank_line(self) -> str:
        """Render an empty padding row in the user-message bubble.

        Filled with spaces (not just `\\x1b[K`) so tmux/screenshot
        replays still see the bg on every cell of the row — empty bg
        cells get dropped by `tmux capture-pane`.
        """

        if not self._enabled:
            return "\n"
        width = chrome_width(self._error_stream)
        padding = " " * width
        return (
            f"{self._user_message_bg}{padding}{self._ANSI_CLEAR_EOL}"
            f"{self._ANSI_RESET}\n"
        )

    def render_tool_call(self, call: AgentToolCall) -> None:
        self._clear_working()
        self._close_reasoning()
        self._last_tool_name = call.tool_name
        self._pending_render = None
        tool = self._tool_renderers.get(call.tool_name)
        if tool is not None:
            args = _parse_tool_input(call.arguments_json.value)
            state: dict[str, object] = {}
            self._pending_render = {
                "corr": call.provider_correlation_id,
                "args": args,
                "state": state,
            }
            if tool.render_call is not None:
                lines = self._dispatch_render(
                    tool.render_call,
                    args,
                    state,
                    is_result=False,
                    content=None,
                    details=None,
                    is_error=False,
                )
                if lines is not None:
                    self._error_stream.write(self._tool_panel_blank_line())
                    for line in lines:
                        self._error_stream.write(self._tool_panel_line(line))
                    self._error_stream.write(self._tool_panel_blank_line())
                    self._error_stream.flush()
                    return
        # --- existing default body ---
        self._error_stream.write(self._tool_panel_blank_line())
        rendered = self._format_pi_call_header_rich(
            call.tool_name, call.arguments_json.value
        )
        self._error_stream.write(self._tool_panel_rich_line(rendered))
        self._error_stream.write(self._tool_panel_blank_line())
        self._error_stream.flush()

    def tool_output_sink(self, chunk: str) -> None:
        # Stream long-running tool output (e.g. pytest dots) live in the
        # captured/plain renderer, mirroring the TUI live region.
        if not chunk:
            return
        try:
            with self._terminal_lock:
                self._error_stream.write(chunk)
                self._error_stream.flush()
        except (ValueError, OSError):
            pass

    def render_tool_result(
        self,
        *,
        output_text: str,
        is_error: bool,
        duration_seconds: float | None = None,
    ) -> None:
        pending = self._pending_render
        self._pending_render = None
        if pending is not None:
            tool = self._tool_renderers.get(self._last_tool_name)
            if tool is not None and tool.render_result is not None:
                details = None
                if self._render_details_sink is not None:
                    details = self._render_details_sink.pop(str(pending["corr"]), None)
                lines = self._dispatch_render(
                    tool.render_result,
                    pending["args"],
                    pending["state"],
                    is_result=True,
                    content=output_text,
                    details=details,
                    is_error=is_error,
                )
                if lines is not None:
                    for line in lines:
                        self._error_stream.write(self._tool_panel_line(line))
                    if duration_seconds is not None:
                        self._error_stream.write(self._tool_panel_blank_line())
                        self._error_stream.write(
                            self._tool_panel_line(
                                f"Took {duration_seconds:.1f}s", style=self._ANSI_DIM
                            )
                        )
                    self._error_stream.write(self._tool_panel_blank_line())
                    self._error_stream.flush()
                    return
        # --- existing default body ---
        lines = output_text.splitlines() or [""]
        preview_lines = lines[: self._RESULT_LINE_PREVIEW_MAX_LENGTH]
        earlier = len(lines) - len(preview_lines)
        if earlier > 0:
            self._error_stream.write(
                self._tool_panel_line(
                    f"... ({earlier} earlier lines, ctrl+o to expand)",
                    style=self._ANSI_DIM,
                )
            )
            tail_preview = lines[-self._RESULT_LINE_PREVIEW_MAX_LENGTH :]
        else:
            tail_preview = preview_lines
        for line in tail_preview:
            self._error_stream.write(self._tool_panel_line(line, style=self._ANSI_DIM))
        if is_error:
            self._error_stream.write(
                self._tool_panel_line(
                    "[error] tool reported a failure",
                    style=self._ANSI_RED + self._ANSI_DIM,
                )
            )
        # Pi keeps the `Took {n}s` caption inside the panel so the
        # block reads as one contiguous strip. Emit a blank panel row
        # for breathing room, then the duration, then a final blank
        # panel row before the next block starts.
        if duration_seconds is not None:
            self._error_stream.write(self._tool_panel_blank_line())
            self._error_stream.write(
                self._tool_panel_line(
                    f"Took {duration_seconds:.1f}s",
                    style=self._ANSI_DIM,
                )
            )
        self._error_stream.write(self._tool_panel_blank_line())
        self._error_stream.flush()

    def _dispatch_render(
        self, renderer, args, state, *, is_result, content, details, is_error
    ):
        style = chrome_style_for(self._error_stream)
        ctx = ToolRenderContext(
            tool_name=self._last_tool_name,
            args=args,
            is_result=is_result,
            is_error=is_error,
            content=content,
            details=details,
            expanded=False,
            width=80,
            theme=build_tool_render_theme(style),
            state=state,
        )
        return render_tool_phase(renderer, ctx)

    def _tool_panel_line(
        self,
        text: str,
        *,
        style: str = "",
        bold: bool = False,
    ) -> str:
        """Render one row of a tool block inside the dark-green panel.

        Pads with a leading space (matches Pi's column gutter), applies
        the supplied style on top of the panel background, then writes
        `\\x1b[K` to fill the remainder of the row with the same
        background before resetting. On non-TTY streams the helper
        falls back to plain text with the leading space so captured
        logs stay readable.
        """

        if not self._enabled:
            return f" {text}\n"
        prefix = self._tool_panel_bg
        weight = self._ANSI_BOLD if bold else ""
        return (
            f"{prefix}{weight}{style} {text}{self._ANSI_CLEAR_EOL}{self._ANSI_RESET}\n"
        )

    def _tool_panel_blank_line(self) -> str:
        """Emit an empty row of the dark-green panel (spacing inside the block)."""

        if not self._enabled:
            return "\n"
        return f"{self._tool_panel_bg}{self._ANSI_CLEAR_EOL}{self._ANSI_RESET}\n"

    def _tool_panel_rich_line(self, segments: list[tuple[str, str]]) -> str:
        """Render a multi-style row inside the dark-green panel.

        ``segments`` is an ordered sequence of ``(text, ansi_style)``
        pairs. Each segment is wrapped with its own ANSI weight/color
        on top of the panel background. The trailing `\\x1b[K` fills
        the rest of the row so the panel reads as a contiguous strip.
        On non-TTY streams the helper concatenates the text segments
        plain (no escapes) so captured logs stay readable.
        """

        if not self._enabled:
            return " " + "".join(text for text, _ in segments) + "\n"
        parts = [self._tool_panel_bg, " "]
        for text, style in segments:
            if style:
                parts.append(style)
                parts.append(text)
                parts.append(self._ANSI_RESET)
                parts.append(self._tool_panel_bg)
            else:
                parts.append(text)
        parts.append(self._ANSI_CLEAR_EOL)
        parts.append(self._ANSI_RESET)
        parts.append("\n")
        return "".join(parts)

    @staticmethod
    def _read_range_label(data: Mapping[str, Any]) -> str:
        """Format the ``:start-end`` line range for a ``read`` header.

        Pi's read tool natively exposes ``offset`` and ``limit`` style
        arguments. Pipy's bounded `read` tool uses a fixed line cap, but
        the codex provider may still emit the optional ``offset`` and
        ``limit`` properties that other read tools advertise. When
        present they shape the header label so the user sees the
        actual requested range; otherwise the default ``:1-200``
        matches the tool's hard-coded ``line_limit``.
        """

        start = data.get("offset")
        limit = data.get("limit")
        if isinstance(start, int) and start >= 0:
            start_line = start + 1
        else:
            start_line = 1
        if isinstance(limit, int) and limit > 0:
            end_line = start_line + limit - 1
        else:
            end_line = start_line + 199
        return f":{start_line}-{end_line}"

    def _format_pi_call_header_rich(
        self, tool_name: str, arguments_json: str
    ) -> list[tuple[str, str]]:
        """Return a list of (text, style) segments for a tool-call header.

        Pi styles the header per-segment: the verb (e.g. `read`,
        `ls`, `grep`) is bold white, the operand (path/pattern) is
        plain dim white, and the line range (`:1-200`) is yellow.
        We reproduce that by emitting separate text+style pairs,
        which `_tool_panel_rich_line` joins back into one panel row
        with each segment carrying its own ANSI weight/color while
        sharing the panel background.
        """

        try:
            data = json.loads(arguments_json)
        except (json.JSONDecodeError, ValueError):
            data = None
        if not isinstance(data, dict):
            data = {}
        bold = self._ANSI_BOLD
        plain = ""
        yellow = self._ANSI_YELLOW
        if tool_name == "read":
            path = str(data.get("path", ""))
            verb = "read resource" if path.startswith("/") else "read"
            range_label = self._read_range_label(data)
            return [
                (verb, bold),
                (" ", plain),
                (path, plain),
                (range_label, yellow),
            ]
        if tool_name == "ls":
            return [
                ("ls", bold),
                (" ", plain),
                (str(data.get("path", ".")), plain),
            ]
        if tool_name == "grep":
            return [
                ("grep", bold),
                (" ", plain),
                (f'"{data.get("pattern", "")}"', plain),
                (" ", plain),
                (str(data.get("path", ".")), plain),
            ]
        if tool_name == "find":
            return [
                ("find", bold),
                (" ", plain),
                (f'"{data.get("pattern", "")}"', plain),
                (" ", plain),
                (str(data.get("path", ".")), plain),
            ]
        if tool_name in {"write", "edit", "edit_diff"}:
            return [
                (tool_name, bold),
                (" ", plain),
                (str(data.get("path", "")), plain),
            ]
        if tool_name == "truncate":
            return [("truncate", bold)]
        preview = self._argument_preview(arguments_json)
        return [(f"{tool_name}({preview})", bold)]

    def _format_pi_call_header(self, tool_name: str, arguments_json: str) -> str:
        """Render a Pi-shape one-line tool header.

        Built-in read/ls/grep/find/write/edit tools render as Pi-style
        compact lines: ``read path:1-line_limit``, ``ls path``,
        ``grep "pattern" path``, ``find "pattern" path``. Unknown tools
        fall back to a ``name(args)`` form so the user can still see the
        invocation.
        """

        try:
            data = json.loads(arguments_json)
        except (json.JSONDecodeError, ValueError):
            data = None
        if not isinstance(data, dict):
            data = {}
        if tool_name == "read":
            path = data.get("path", "")
            prefix = "read resource" if str(path).startswith("/") else "read"
            range_label = self._read_range_label(data)
            return f"{prefix} {path}{range_label} (ctrl+o to expand)"
        if tool_name == "ls":
            path = data.get("path", ".")
            return f"ls {path}"
        if tool_name == "grep":
            pattern = data.get("pattern", "")
            path = data.get("path", ".")
            return f'grep "{pattern}" {path}'
        if tool_name == "find":
            pattern = data.get("pattern", "")
            path = data.get("path", ".")
            return f'find "{pattern}" {path}'
        if tool_name == "write":
            path = data.get("path", "")
            return f"write {path}"
        if tool_name == "edit":
            path = data.get("path", "")
            return f"edit {path}"
        if tool_name == "edit_diff":
            path = data.get("path", "")
            return f"edit_diff {path}"
        if tool_name == "truncate":
            return "truncate"
        preview = self._argument_preview(arguments_json)
        return f"{tool_name}({preview})"

    def _argument_preview(self, arguments_json: str) -> str:
        try:
            data = json.loads(arguments_json)
        except (json.JSONDecodeError, ValueError):
            preview = arguments_json.strip()
            if len(preview) > self._ARGUMENT_VALUE_PREVIEW_LIMIT:
                preview = preview[: self._ARGUMENT_VALUE_PREVIEW_LIMIT] + "…"
            return preview
        if not isinstance(data, dict):
            return ""
        pieces: list[str] = []
        for key, value in data.items():
            if isinstance(value, str):
                value_repr = value
                if len(value_repr) > self._ARGUMENT_VALUE_PREVIEW_LIMIT:
                    value_repr = value_repr[: self._ARGUMENT_VALUE_PREVIEW_LIMIT] + "…"
                pieces.append(f'{key}="{value_repr}"')
            elif isinstance(value, (int, float, bool)) or value is None:
                pieces.append(f"{key}={value}")
            else:
                pieces.append(f"{key}=…")
        return ", ".join(pieces)

    def _style(self, text: str, code: str) -> str:
        if not self._enabled:
            return text
        return f"{code}{text}{self._ANSI_RESET}"

"""DF1-F7 TUI polish: Pi's dark palette, editor border, status lines, startup
and the compaction outcome display (Pi `interactive-mode.ts`, `theme/dark.json`).
"""

from __future__ import annotations

import io
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from pipy_harness.native.agent import AgentUserMessage, ProductContent
from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.coding.compaction import (
    CodingCompactionOutcome,
    CodingCompactionResult,
)
from pipy_harness.native.frame_renderer import (
    ChromeSnapshot,
    FrameBlock,
    FrameLine,
    FrameSnapshot,
    InputSnapshot,
    block_lines,
    render_full_frame,
    style_line,
)
from pipy_harness.native.repl import wiring
from pipy_harness.native.startup_chrome import startup_history_blocks
from pipy_harness.native.theme_files import load_theme_file
from pipy_harness.native.themes import resolve_palette
from pipy_harness.native.tool_rows import SummaryBox, row_theme
from pipy_harness.native.tui import TerminalUi
from pipy_harness.native.ui.components.transcript import TranscriptComponent
from pipy_harness.native.ui.paint_lock import PaintLock
from pipy_harness.native.ui.screen import DriveOwner, DriveResult, ScreenRenderInputs

# Pi's `dark` theme resolved by Pi's own `theme.ts` (pi-mono 1b347794e):
# truecolor and 256-colour SGR codes per token.
_PI_DARK = {
    "accent": ("38;2;167;152;215", "38;5;140"),
    "section": ("38;2;205;154;34", "38;5;172"),  # mdHeading
    "dim": ("38;2;126;136;142", "38;5;102"),
    "secondary_dim": ("38;2;157;165;169", "38;5;145"),  # muted
    "thinking_text": ("38;2;150;160;164", "38;5;109"),
    "error": ("38;2;234;127;129", "38;5;174"),
    "warning": ("38;2;205;154;34", "38;5;172"),
    "success": ("38;2;104;183;141", "38;5;72"),
    "user_message_bg": ("48;2;33;59;73", "48;5;23"),
    "user_message_text": ("38;2;222;224;225", "38;5;254"),
    "tool_pending_bg": ("48;2;52;56;58", "48;5;237"),
    "tool_success_bg": ("48;2;37;65;49", "48;5;23"),
    "tool_error_bg": ("48;2;91;40;42", "48;5;52"),
    "tool_output": ("38;2;157;165;169", "38;5;145"),
    "custom_message_bg": ("48;2;58;48;85", "48;5;59"),
    "separator": ("38;2;108;118;123", "38;5;66"),  # thinkingOff
    "thinking_off": ("38;2;108;118;123", "38;5;66"),
    "thinking_minimal": ("38;2;104;128;141", "38;5;66"),
    "thinking_low": ("38;2;84;137;164", "38;5;67"),
    "thinking_medium": ("38;2;97;133;204", "38;5;68"),
    "thinking_high": ("38;2;151;118;229", "38;5;104"),
    "thinking_xhigh": ("38;2;222;84;193", "38;5;169"),
    "thinking_max": ("38;2;254;84;98", "38;5;203"),
    "bash_mode": ("38;2;94;178;134", "38;5;72"),
}


@pytest.mark.parametrize("field", sorted(_PI_DARK))
def test_pi_palette_is_pi_dark_theme(field: str) -> None:
    palette = resolve_palette("pi")
    truecolor, fallback = _PI_DARK[field]
    assert getattr(palette, f"{field}_truecolor") == truecolor
    assert getattr(palette, f"{field}_fallback") == fallback


@pytest.mark.parametrize("truecolor", [True, False])
@pytest.mark.parametrize(
    "level", ["off", "minimal", "low", "medium", "high", "xhigh", "max"]
)
def test_editor_border_follows_the_thinking_level(truecolor: bool, level: str) -> None:
    style = ChromeStyle(
        enabled=True, truecolor=truecolor, palette=resolve_palette("pi")
    )
    code = _PI_DARK[f"thinking_{level}"][0 if truecolor else 1]
    assert style.editor_border("──", level=level, bash_mode=False) == (
        f"\x1b[{code}m──\x1b[0m"
    )
    bash = _PI_DARK["bash_mode"][0 if truecolor else 1]
    assert style.editor_border("──", level=level, bash_mode=True) == (
        f"\x1b[{bash}m──\x1b[0m"
    )


def test_themes_without_border_colours_keep_their_separator(tmp_path: Path) -> None:
    ocean = ChromeStyle(enabled=True, truecolor=True, palette=resolve_palette("ocean"))
    for bash_mode in (False, True):
        assert ocean.editor_border("──", level="high", bash_mode=bash_mode) == (
            ocean.separator("──")
        )
    # A package theme that sets no border colours does not inherit Pi's.
    path = tmp_path / "midnight.toml"
    path.write_text('name = "midnight"\nseparator_truecolor = "38;2;1;2;3"\n')
    palette = load_theme_file(path)
    assert palette is not None
    assert palette.thinking_high_truecolor is None
    assert palette.bash_mode_truecolor is None
    style = ChromeStyle(enabled=True, truecolor=True, palette=palette)
    assert style.editor_border("──", level="high", bash_mode=True) == (
        "\x1b[38;2;1;2;3m──\x1b[0m"
    )
    # Inherited fields still come from the default palette.
    assert palette.accent_truecolor == _PI_DARK["accent"][0]


def test_a_border_colour_set_for_one_colour_mode_applies_in_that_mode(
    tmp_path: Path,
) -> None:
    path = tmp_path / "partial.toml"
    path.write_text(
        'name = "partial"\nseparator_fallback = "38;5;9"\n'
        'thinking_high_truecolor = "38;2;9;9;9"\n'
    )
    palette = load_theme_file(path)
    assert palette is not None
    truecolor = ChromeStyle(enabled=True, truecolor=True, palette=palette)
    assert truecolor.editor_border("─", level="high", bash_mode=False) == (
        "\x1b[38;2;9;9;9m─\x1b[0m"
    )
    fallback = ChromeStyle(enabled=True, truecolor=False, palette=palette)
    assert fallback.editor_border("─", level="high", bash_mode=False) == (
        "\x1b[38;5;9m─\x1b[0m"
    )


class _TTYStream(io.StringIO):
    def isatty(self) -> bool:  # noqa: D401 - test stub
        return True


def test_summary_boxes_restyle_with_the_active_theme(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A `[compaction]` box keeps its texts, not styled lines, so Ctrl+O and a
    resize draw it with the theme active then (like tool rows)."""

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.setenv("PIPY_THEME", "pi")
    transcript = TranscriptComponent(
        PaintLock(threading.RLock()),
        lambda: None,
        reset_scrollback=lambda: None,
        render_inputs=ScreenRenderInputs(lambda: 80, _TTYStream(), lambda: False),
    )
    transcript.add_summary_box(
        SummaryBox("[compaction]", "Compacted from 5 tokens (", "Compacted", "S")
    )

    def label_line() -> str:
        return transcript.history_blocks[-1][1][1]

    assert _PI_DARK["accent"][0] in label_line()
    ocean_accent = resolve_palette("ocean").accent_truecolor
    monkeypatch.setenv("PIPY_THEME", "ocean")
    transcript.set_tools_expanded(True)
    assert ocean_accent in label_line()
    assert "S" in transcript.history_blocks[-1][1][-3]
    monkeypatch.setenv("PIPY_THEME", "pi")
    transcript.refresh_tool_rows()
    assert _PI_DARK["accent"][0] in label_line()


def test_tool_titles_use_pi_text_in_256_colour_mode() -> None:
    style = ChromeStyle(enabled=True, truecolor=False, palette=resolve_palette("pi"))
    assert row_theme(style).codes["toolTitle"] == _PI_DARK["user_message_text"][1]


def _snapshot(input_text: str, level: str) -> FrameSnapshot:
    return FrameSnapshot(
        width=20,
        height=8,
        history=(),
        assistant_text="",
        reasoning_text="",
        tool_output_text="",
        working_text="",
        thinking_hidden=False,
        hidden_thinking_label="Thinking...",
        tools_expanded=False,
        input=InputSnapshot(input_text, len(input_text)),
        popup=(),
        pending=(),
        chrome=ChromeSnapshot(footer=(FrameLine("cwd", "footer"),)),
        overlay=None,
        cursor_visible=True,
        thinking_level=level,
    )


def test_frame_borders_carry_level_and_bash_mode_without_a_label() -> None:
    style = ChromeStyle(enabled=True, truecolor=True, palette=resolve_palette("pi"))
    rows = render_full_frame(_snapshot("!ls", "high"), pad=False)
    borders = [row for row in rows if row.kind == "separator"]
    assert [row.text for row in borders] == ["─" * 20, "─" * 20]
    bash = _PI_DARK["bash_mode"][0]
    assert [style_line(row, style, 20) for row in borders] == [
        f"\x1b[{bash}m{'─' * 20}\x1b[0m"
    ] * 2
    rows = render_full_frame(_snapshot("hi", "medium"), pad=False)
    medium = _PI_DARK["thinking_medium"][0]
    assert {style_line(row, style, 20) for row in rows if row.kind == "separator"} == {
        f"\x1b[{medium}m{'─' * 20}\x1b[0m"
    }


def test_notice_rows_are_pi_status_lines() -> None:
    """Pi `showStatus`: dim text one column in, no app label."""

    rows = block_lines(FrameBlock("notice", ("Tool output: expanded",)), 40)
    assert rows == (
        FrameLine(" Tool output: expanded", "dim"),
        FrameLine(""),
    )
    style = ChromeStyle(enabled=True, truecolor=True, palette=resolve_palette("pi"))
    assert style_line(rows[0], style, 40) == (
        f"\x1b[{_PI_DARK['dim'][0]}m Tool output: expanded\x1b[0m"
    )


def _ui(tmp_path: Path) -> TerminalUi:
    return TerminalUi(
        input_stream=io.StringIO(), terminal_stream=io.StringIO(), cwd=tmp_path
    )


def test_notice_drops_the_stderr_prefix(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    ui.components.transcript.add_notice("pipy: compaction cancelled.\npipy: again")
    assert ui.components.transcript.history_blocks[-1] == (
        "notice",
        ("compaction cancelled.", "again"),
    )


def test_paints_wait_for_start(tmp_path: Path) -> None:
    """Pi renders once the startup rows are in place: no editor frame is
    written above the header."""

    ui = _ui(tmp_path)
    terminal = cast(io.StringIO, ui.terminal_stream)
    screen = ui.components.screen
    screen.defer_paints()
    ui.components.chrome.footer.set_builtin_text("cwd\nstatus")
    ui.components.transcript.add_notice("loaded early")
    screen.force_full_redraw()
    assert terminal.getvalue() == ""
    ui.start()
    output = terminal.getvalue()
    assert output.count("\x1b[?25l") == 1
    header = output.index("pipy v")
    assert header < output.index("loaded early") < output.index("─")
    # The early notice counts as a startup row, so the first conversation
    # render keeps it.
    ui.components.transcript.replace_conversation([])
    assert any(kind == "notice" for kind, _ in ui.components.transcript.history_blocks)


def test_a_modal_resumes_deferred_paints(tmp_path: Path) -> None:
    ui = _ui(tmp_path)
    screen = ui.components.screen
    screen.defer_paints()
    owner = DriveOwner(open=lambda: DriveResult(7), handle_key=lambda _key: None)
    assert screen.drive(owner) == 7
    assert screen.state.deferred is False


def test_quiet_startup_seeds_only_a_blank_row(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("x")
    assert [tuple(block) for block in startup_history_blocks(tmp_path, True, True)] == [
        ("normal", ("",))
    ]
    loud = startup_history_blocks(tmp_path, True)
    kinds = [block[0] for block in loud]
    assert "title" in kinds and "section" in kinds
    # Pi leaves two blank rows after the last listed section.
    assert tuple(loud[-1]) == ("normal", ("",))
    assert loud[-2][1][-1] == ""


class _History:
    def __init__(self, redrawn: bool) -> None:
        self.redrawn = redrawn
        self.calls: list[object] = []

    def render_after_compaction(self, pending: object = None) -> bool:
        self.calls.append(pending)
        return self.redrawn


def _completed() -> CodingCompactionOutcome:
    return CodingCompactionOutcome(
        "pipy: compacted conversation context (manual; dropped 1).",
        result=CodingCompactionResult("S", "e1", 10, 1, 2),
    )


def _show(
    outcome: CodingCompactionOutcome,
    *,
    history: _History | None,
    tui: bool,
    headless: bool,
) -> tuple[list[str], str]:
    notices: list[str] = []
    terminal_ui = (
        SimpleNamespace(
            components=SimpleNamespace(
                transcript=SimpleNamespace(add_notice=notices.append)
            )
        )
        if tui
        else None
    )
    stderr = io.StringIO()
    pending = AgentUserMessage(ProductContent("go"))
    wiring._show_compaction(  # noqa: SLF001 - the compaction outcome display
        cast(Any, history),
        cast(Any, terminal_ui),
        stderr,
        outcome,
        pending,
        headless=headless,
    )
    return notices, stderr.getvalue()


def test_completed_compaction_redraws_instead_of_a_notice() -> None:
    history = _History(redrawn=True)
    assert _show(_completed(), history=history, tui=True, headless=False) == ([], "")
    assert [getattr(call, "content", None) for call in history.calls] == [
        ProductContent("go")
    ]


def test_completed_compaction_is_silent_headless_and_noted_in_the_plain_repl() -> None:
    assert _show(_completed(), history=None, tui=False, headless=True) == ([], "")
    notices, stderr = _show(_completed(), history=None, tui=False, headless=False)
    assert notices == [] and "compacted conversation context" in stderr


@pytest.mark.parametrize("headless", [True, False])
def test_failed_or_unrecorded_compaction_keeps_its_notice(headless: bool) -> None:
    failed = CodingCompactionOutcome("pipy: compaction failed.")
    assert _show(failed, history=None, tui=False, headless=headless)[1] == (
        "pipy: compaction failed.\n"
    )
    unpersisted = CodingCompactionOutcome(
        "pipy: compacted.",
        result=_completed().result,
        persistence_failed=True,
    )
    history = _History(redrawn=True)
    assert _show(unpersisted, history=history, tui=True, headless=False)[0] == [
        "pipy: compacted."
    ]
    assert history.calls == []
    # The branch has no compaction first: the redraw declines, the notice shows.
    declined = _History(redrawn=False)
    assert _show(_completed(), history=declined, tui=True, headless=False)[0] == [
        _completed().notice
    ]


def test_header_startup_keeps_hints_without_resources(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("x")
    blocks = startup_history_blocks(tmp_path, True, "header")
    assert [block[0] for block in blocks] == ["normal", "title", "controls", "normal"]
    text = "\n".join(line for block in blocks for line in block[1])
    assert "pipy v" in text and "ctrl+p model" in text
    assert "[Context]" not in text and "Pipy can explain" not in text

    from pipy_harness.native.chrome import print_startup_chrome

    stream = io.StringIO()
    print_startup_chrome(
        stream, cwd=tmp_path, quiet="header", include_workspace_defaults=True
    )
    text = stream.getvalue()
    assert "pipy v" in text and "escape interrupt" in text
    assert "[Context]" not in text and "Pipy can explain" not in text

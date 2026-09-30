"""Pi's per-tool rows (TOOLS3): ``tool_rows.render_tool_row`` and its helpers.

Texts come from pi-mono ``1b347794e`` ``core/tools/renderers/*.ts``,
``render-utils.ts``, ``components/tool-execution.ts`` and ``diff.ts``, and
match the rows a real Pi session draws (docs/parity-loop evidence).
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.agent import AgentToolCall, ProductContent
from pipy_harness.native.ansi_wrap import split_lines_with_carry, wrap_text_with_ansi
from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.extension_chrome_state import ExtensionChromeState
from pipy_harness.native.frame_renderer import (
    FrameBlock,
    block_lines,
    decode_tool_box_line,
    encode_tool_box_line,
)
from pipy_harness.native.tool_rows import (
    EditPreview,
    RowRenderInputs,
    RowTheme,
    ToolRowResult,
    ToolRowState,
    apply_edit_result,
    compute_edit_preview,
    render_diff,
    render_tool_row,
    row_theme,
)
from pipy_harness.native.tools.word_diff import diff_words
from pipy_harness.native.ui.components.tool_loop_renderer import TuiToolLoopRenderer
from pipy_harness.native.ui.components.transcript import TranscriptComponent
from pipy_harness.native.ui.paint_lock import PaintLock
from pipy_harness.native.ui.screen import ScreenRenderInputs

_SGR = re.compile(r"\x1b\[[0-9;]*m")
PLAIN = RowTheme(enabled=False, codes={})
# A theme whose codes are easy to read in assertions.
MARKED = RowTheme(
    enabled=True,
    codes={
        key: str(index)
        for index, key in enumerate(
            (
                "text",
                "toolTitle",
                "toolOutput",
                "muted",
                "toolDiffContext",
                "customMessageText",
                "dim",
                "accent",
                "customMessageLabel",
                "warning",
                "error",
                "toolDiffRemoved",
                "success",
                "toolDiffAdded",
            ),
            start=30,
        )
    },
)


def _state(name: str, args: dict[str, Any], tmp_path: Path, **kw: Any) -> ToolRowState:
    return ToolRowState(tool_name=name, args=args, cwd=tmp_path, **kw)


def _rows(
    state: ToolRowState, *, expanded: bool = False, width: int = 100
) -> list[tuple[str, str]]:
    rows = render_tool_row(
        state, RowRenderInputs(expanded=expanded, width=width, theme=PLAIN)
    )
    return [(row.bg, row.text) for row in rows]


def _text(state: ToolRowState, **kw: Any) -> list[str]:
    return [text for _bg, text in _rows(state, **kw) if text]


def _result(content: str, details: Any = None, *, error: bool = False) -> ToolRowResult:
    return ToolRowResult(content, details, error)


# -- box shape ------------------------------------------------------------------


def test_a_row_is_a_padded_box_then_a_spacer(tmp_path: Path) -> None:
    state = _state("ls", {"path": "src"}, tmp_path)
    assert _rows(state) == [
        ("pending", ""),
        ("pending", "ls src"),
        ("pending", ""),
        ("none", ""),
    ]
    state.result = _result("a.py\nb/")
    assert _rows(state) == [
        ("success", ""),
        ("success", "ls src"),
        ("success", ""),
        ("success", "a.py"),
        ("success", "b/"),
        ("success", ""),
        ("none", ""),
    ]
    state.result = _result("boom", error=True)
    assert {bg for bg, _ in _rows(state)[:-1]} == {"error"}


# -- read -----------------------------------------------------------------------


def test_read_header_range_and_collapsed_body(tmp_path: Path) -> None:
    state = _state("read", {"path": "src/a.py", "offset": 5, "limit": 10}, tmp_path)
    state.result = _result("one\ntwo")
    assert _text(state) == ["read src/a.py:5-14"]
    assert _text(state, expanded=True) == ["read src/a.py:5-14", "one", "two"]
    only_offset = _state("read", {"path": "a", "offset": 3}, tmp_path)
    assert _text(only_offset) == ["read a:3"]
    only_limit = _state("read", {"path": "a", "limit": 4, "offset": None}, tmp_path)
    assert _text(only_limit) == ["read a:1-4"]


def test_read_errors_show_even_collapsed(tmp_path: Path) -> None:
    state = _state("read", {"path": "gone.txt"}, tmp_path)
    state.result = _result("read error: file does not exist", error=True)
    assert _text(state) == ["read gone.txt", "read error: file does not exist"]


def test_read_expanded_limits_and_truncation_warnings(tmp_path: Path) -> None:
    body = "\n".join(f"l{n}" for n in range(1, 15)) + "\n\n"
    truncation = {
        "truncated": True,
        "truncatedBy": "lines",
        "outputLines": 14,
        "totalLines": 3000,
        "maxLines": 2000,
        "maxBytes": 51200,
        "firstLineExceedsLimit": False,
    }
    state = _state("read", {"path": "big.txt"}, tmp_path)
    state.result = _result(body, {"truncation": truncation})
    text = _text(state, expanded=True)
    assert text[1:15] == [f"l{n}" for n in range(1, 15)]
    assert text[-1] == "[Truncated: showing 14 of 3000 lines (2000 line limit)]"
    by_bytes = {**truncation, "truncatedBy": "bytes"}
    state.result = _result("x", {"truncation": by_bytes})
    assert (
        _text(state, expanded=True)[-1] == "[Truncated: 14 lines shown (50.0KB limit)]"
    )
    first = {**truncation, "firstLineExceedsLimit": True}
    state.result = _result("[Line 1 is 60KB]", {"truncation": first})
    assert _text(state, expanded=True)[-1] == "[First line exceeds 50.0KB limit]"


def test_read_compact_classification(tmp_path: Path) -> None:
    skill = _state("read", {"path": "skills/demo/SKILL.md"}, tmp_path)
    # Pi writes the label's bold as literal SGR, whatever the theme.
    assert _text(skill) == ["\x1b[1m[skill]\x1b[22m demo (ctrl+o to expand)"]
    assert _text(skill, expanded=True) == ["read skills/demo/SKILL.md"]
    resource = _state("read", {"path": "sub/AGENTS.md", "limit": 3}, tmp_path)
    assert _text(resource) == ["read resource sub/AGENTS.md:1-3 (ctrl+o to expand)"]
    outside = _state("read", {"path": "/elsewhere/CLAUDE.md"}, tmp_path)
    assert _text(outside) == ["read resource /elsewhere/CLAUDE.md (ctrl+o to expand)"]
    plain = _state("read", {"path": "notes.md"}, tmp_path)
    assert _text(plain) == ["read notes.md"]


def test_read_classifies_pipy_docs_under_the_package_root() -> None:
    root = Path(__file__).resolve().parents[1]
    docs = _state("read", {"path": "docs/usage.md"}, root)
    assert _text(docs) == ["read docs docs/usage.md (ctrl+o to expand)"]


def test_read_invalid_path_argument(tmp_path: Path) -> None:
    state = _state("read", {"path": 7}, tmp_path)
    assert _text(state) == ["read [invalid arg]"]
    empty = _state("read", {}, tmp_path)
    assert _text(empty) == ["read ..."]


# -- grep / find / ls -------------------------------------------------------------


def test_grep_header_and_collapsed_preview_with_warnings(tmp_path: Path) -> None:
    state = _state(
        "grep", {"pattern": "def ", "path": "src", "glob": "*.py", "limit": 5}, tmp_path
    )
    lines = "\n".join(f"a.py:{n}: def f{n}()" for n in range(1, 21))
    state.result = _result(
        lines + "\n\n[5 matches limit reached]",
        {
            "matchLimitReached": 5,
            "truncation": {"truncated": True},
            "linesTruncated": True,
        },
    )
    text = _text(state)
    assert text[0] == "grep /def / in src (*.py) limit 5"
    assert text[1:16] == [f"a.py:{n}: def f{n}()" for n in range(1, 16)]
    assert text[16] == "... (7 more lines, ctrl+o to expand)"
    # The warning shows collapsed and expanded alike.
    assert (
        text[17] == "[Truncated: 5 matches limit, 50.0KB limit, some lines truncated]"
    )
    assert _text(state, expanded=True)[-1] == text[17]


def test_find_and_ls_preview_twenty_lines(tmp_path: Path) -> None:
    listing = "\n".join(f"f{n}" for n in range(25))
    find = _state("find", {"pattern": "*.py", "limit": 25}, tmp_path)
    find.result = _result(listing, {"resultLimitReached": 25})
    text = _text(find)
    assert text[0] == "find *.py in . (limit 25)"
    assert len(text) == 1 + 20 + 2
    assert text[-2] == "... (5 more lines, ctrl+o to expand)"
    assert text[-1] == "[Truncated: 25 results limit]"
    ls = _state("ls", {}, tmp_path)
    ls.result = _result(listing, {"entryLimitReached": 500})
    assert _text(ls)[0] == "ls ."
    assert _text(ls)[-1] == "[Truncated: 500 entries limit]"
    assert len(_text(ls, expanded=True)) == 1 + 25 + 1


def test_search_headers_follow_pi_str_rules(tmp_path: Path) -> None:
    home = str(Path.home())
    assert _text(_state("grep", {"pattern": 3, "path": None}, tmp_path)) == [
        "grep [invalid arg] in ."
    ]
    assert _text(_state("find", {"pattern": "x", "path": f"{home}/p"}, tmp_path)) == [
        "find x in ~/p"
    ]
    assert _text(_state("ls", {"path": "", "limit": None}, tmp_path)) == [
        "ls . (limit null)"
    ]


# -- write ------------------------------------------------------------------------


def test_write_previews_ten_lines_and_shows_nothing_on_success(tmp_path: Path) -> None:
    content = "".join(f"line {n}\n" for n in range(1, 16)) + "\n"
    state = _state("write", {"path": "new.txt", "content": content}, tmp_path)
    state.result = _result("Successfully wrote to new.txt")
    text = _text(state)
    assert text[0] == "write new.txt"
    assert text[1:11] == [f"line {n}" for n in range(1, 11)]
    assert text[11] == "... (5 more lines, 15 total, ctrl+o to expand)"
    assert len(text) == 12
    assert _text(state, expanded=True)[1:] == [f"line {n}" for n in range(1, 16)]


def test_write_shows_the_error_and_bad_content(tmp_path: Path) -> None:
    state = _state("write", {"path": "x", "content": 5}, tmp_path)
    state.result = _result("EACCES: permission denied", error=True)
    assert _text(state) == [
        "write x",
        "[invalid content arg - expected string]",
        "EACCES: permission denied",
    ]


# -- edit -------------------------------------------------------------------------


def _edit_args(old: str = "b = 2", new: str = "b = 3") -> dict[str, Any]:
    return {"path": "a.py", "edits": [{"oldText": old, "newText": new}]}


def test_live_edit_preview_draws_the_diff_in_a_success_box(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a = 1\nb = 2\n")
    state = _state("edit", _edit_args(), tmp_path)
    state.preview = compute_edit_preview(state.args, tmp_path)
    assert state.preview == EditPreview(
        diff=" 1 a = 1\n-2 b = 2\n+2 b = 3", first_changed_line=2
    )
    rows = _rows(state)
    assert rows == [
        ("success", ""),
        ("success", "edit a.py"),
        ("success", ""),
        ("success", " 1 a = 1"),
        ("success", "-2 b = 2"),
        ("success", "+2 b = 3"),
        ("success", ""),
        ("none", ""),
    ]
    state.result = _result("ok", {"diff": state.preview.diff, "firstChangedLine": 2})
    apply_edit_result(state)
    assert _rows(state) == rows


def test_restored_edit_takes_the_stored_diff_into_its_box(tmp_path: Path) -> None:
    state = _state("edit", _edit_args(), tmp_path)
    assert _rows(state)[0] == ("pending", "")
    state.result = _result("ok", {"diff": "-1 x\n+1 y", "firstChangedLine": 1})
    apply_edit_result(state)
    assert _rows(state) == [
        ("success", ""),
        ("success", "edit a.py"),
        ("success", ""),
        ("success", "-1 x"),
        ("success", "+1 y"),
        ("success", ""),
        ("none", ""),
    ]


def test_a_result_diff_replaces_a_different_live_preview(tmp_path: Path) -> None:
    state = _state("edit", _edit_args(), tmp_path, preview=EditPreview(diff="-1 old"))
    state.result = _result("ok", {"diff": "-1 new"})
    apply_edit_result(state)
    assert _text(state) == ["edit a.py", "-1 new"]


def test_edit_failure_equal_to_the_preview_error_is_shown_once(tmp_path: Path) -> None:
    state = _state("edit", _edit_args("nope"), tmp_path)
    (tmp_path / "a.py").write_text("x\n")
    state.preview = compute_edit_preview(state.args, tmp_path)
    assert state.preview is not None and state.preview.error is not None
    state.result = _result(state.preview.error, error=True)
    apply_edit_result(state)
    rows = _rows(state)
    assert [text for _bg, text in rows if text] == ["edit a.py", state.preview.error]
    assert {bg for bg, _ in rows[:-1]} == {"error"}


def test_edit_failure_that_differs_is_drawn_below_the_box(tmp_path: Path) -> None:
    state = _state("edit", _edit_args(), tmp_path)
    state.result = _result("Operation aborted", error=True)
    apply_edit_result(state)
    rows = _rows(state)
    # No preview: settledError colours the box; the text sits outside it.
    assert rows == [
        ("error", ""),
        ("error", "edit a.py"),
        ("error", ""),
        ("none", ""),
        ("none", "Operation aborted"),
        ("none", ""),
    ]


def test_a_successful_preview_keeps_its_background_when_the_edit_fails(
    tmp_path: Path,
) -> None:
    state = _state(
        "edit", _edit_args(), tmp_path, preview=EditPreview(diff="-1 a\n+1 b")
    )
    state.result = _result("EACCES", error=True)
    apply_edit_result(state)
    rows = _rows(state)
    assert rows[:6] == [
        ("success", ""),
        ("success", "edit a.py"),
        ("success", ""),
        ("success", "-1 a"),
        ("success", "+1 b"),
        ("success", ""),
    ]
    assert ("none", "EACCES") in rows


def test_edit_preview_errors(tmp_path: Path) -> None:
    preview = compute_edit_preview(_edit_args(), tmp_path)
    assert preview == EditPreview(
        error="Could not edit file: a.py. Error code: ENOENT."
    )
    assert compute_edit_preview({"path": "a.py"}, tmp_path) is None
    single = compute_edit_preview(
        {"path": "a.py", "oldText": "a", "newText": "b"}, tmp_path
    )
    assert single is not None and single.error is not None


def test_render_diff_marks_the_changed_words_of_a_one_line_edit() -> None:
    rendered = render_diff(MARKED, " 1 a\n-2     return a + b\n+2     return a - b\n 3")
    lines = rendered.split("\n")
    assert lines[0] == "\x1b[34m 1 a\x1b[39m"
    assert lines[1] == "\x1b[41m-2     return a \x1b[7m+\x1b[27m b\x1b[39m"
    assert lines[2] == "\x1b[43m+2     return a \x1b[7m-\x1b[27m b\x1b[39m"
    # Several changed lines: no word diff.
    block = render_diff(MARKED, "-1 a\n-2 b\n+1 c").split("\n")
    assert block == [
        "\x1b[41m-1 a\x1b[39m",
        "\x1b[41m-2 b\x1b[39m",
        "\x1b[43m+1 c\x1b[39m",
    ]
    # A line the parser does not recognise is context.
    assert render_diff(MARKED, "     ...") == "\x1b[34m     ...\x1b[39m"


def test_diff_words_matches_jsdiff_examples() -> None:
    def parts(old: str, new: str) -> list[tuple[str, bool, bool]]:
        return [(p.value, p.added, p.removed) for p in diff_words(old, new)]

    # jsdiff's documented cleanup cases (word.js dedupeWhitespaceInChangeObjects).
    assert parts("foo bar baz", "foo baz") == [
        ("foo ", False, False),
        ("bar ", False, True),
        ("baz", False, False),
    ]
    assert parts("foo bar baz", "foo qux baz") == [
        ("foo ", False, False),
        ("bar", False, True),
        ("qux", True, False),
        (" baz", False, False),
    ]


# -- bash ------------------------------------------------------------------------


def test_bash_collapsed_shows_the_last_five_visual_lines(tmp_path: Path) -> None:
    state = _state("bash", {"command": "seq 1 12", "timeout": 30}, tmp_path)
    state.result = _result("\n".join(str(n) for n in range(1, 13)))
    state.duration_seconds = 0.04
    assert _text(state) == [
        "$ seq 1 12 (timeout 30s)",
        "... (7 earlier lines, ctrl+o to expand)",
        "8",
        "9",
        "10",
        "11",
        "12",
        "Took 0.0s",
    ]
    assert len(_text(state, expanded=True)) == 1 + 12 + 1


def test_bash_counts_wrapped_rows_at_the_box_width(tmp_path: Path) -> None:
    state = _state("bash", {"command": "x"}, tmp_path)
    state.result = _result("a" * 25)
    # width 12 -> content width 10: 25 characters are 3 visual rows.
    assert _text(state, width=12) == ["$ x", "aaaaaaaaaa", "aaaaaaaaaa", "aaaaa"]


def test_bash_replaces_the_full_output_notice_with_a_warning(tmp_path: Path) -> None:
    state = _state("bash", {"command": "big"}, tmp_path)
    details = {
        "truncation": {
            "truncated": True,
            "truncatedBy": "lines",
            "outputLines": 2000,
            "totalLines": 5000,
        },
        "fullOutputPath": "/tmp/pipy-bash-1.log",
    }
    state.result = _result(
        "tail\n\n[Showing lines 3001-5000 of 5000. Full output: /tmp/pipy-bash-1.log]",
        details,
    )
    assert _text(state) == [
        "$ big",
        "tail",
        "[Full output: /tmp/pipy-bash-1.log. Truncated: showing 2000 of 5000 lines]",
    ]


@pytest.mark.parametrize(
    ("seconds", "label"),
    [(0.25, "0.3s"), (59.94, "59.9s"), (61, "1m 1s"), (3725, "1h 2m 5s")],
)
def test_bash_duration_formats_like_pi(
    tmp_path: Path, seconds: float, label: str
) -> None:
    state = _state("bash", {"command": "x"}, tmp_path)
    state.result = _result("")
    state.duration_seconds = seconds
    assert _text(state)[-1] == f"Took {label}"


def test_a_running_bash_shows_partial_output_without_a_duration(tmp_path: Path) -> None:
    state = _state("bash", {"command": "make"}, tmp_path, partial_output="step 1\n")
    state.duration_seconds = 3.0
    rows = _rows(state)
    assert {bg for bg, _ in rows[:-1]} == {"pending"}
    assert [text for _bg, text in rows if text] == ["$ make", "step 1"]


# -- extension / generic fallbacks ---------------------------------------------


def test_generic_call_header_and_result_preview(tmp_path: Path) -> None:
    from pipy_harness.extensions import ExtensionTool

    tool = ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=lambda c, i: "",
    )
    state = _state(
        "kv", {"key": "a", "n": 1.0, "list": [1, 2]}, tmp_path, extension=tool
    )
    state.result = _result("\n".join(f"r{n}" for n in range(12)))
    text = _text(state)
    assert text[0] == 'kv key="a" n=1 list=[1,2]'
    assert text[1:11] == [f"r{n}" for n in range(10)]
    assert text[11] == "... (2 more lines, ctrl+o to expand)"
    expanded = _text(state, expanded=True)
    assert expanded[:4] == ["kv", "  key: a", "  n: 1", "  list: ["]
    long_args = _state("kv", {"text": "x" * 200}, tmp_path, extension=tool)
    assert _text(long_args)[0] == "kv " + ('text="' + "x" * 200)[:97] + "..."


# -- theme, wrap and frame ------------------------------------------------------


def test_row_theme_resets_only_the_foreground() -> None:
    style = ChromeStyle(enabled=True, truecolor=True)
    theme = row_theme(style)
    assert theme.fg("toolOutput", "x") == "\x1b[38;2;157;165;169mx\x1b[39m"
    assert theme.bold("x") == "\x1b[1mx\x1b[22m"
    assert row_theme(ChromeStyle(enabled=False)).fg("error", "x") == "x"


def test_styles_carry_across_newlines_like_pi_text() -> None:
    assert split_lines_with_carry("\x1b[31ma\nb\x1b[39m\nc") == [
        "\x1b[31ma",
        "\x1b[31mb\x1b[39m",
        "c",
    ]


def test_wrap_text_with_ansi_matches_pi() -> None:
    # Checked against pi-tui wrapTextWithAnsi with a node differential run.
    assert wrap_text_with_ansi("\x1b[31mhello world again\x1b[39m", 11) == [
        "\x1b[31mhello world",
        "\x1b[31magain\x1b[39m",
    ]
    assert wrap_text_with_ansi("abcdefghij", 4) == ["abcd", "efgh", "ij"]
    assert wrap_text_with_ansi("", 5) == [""]


def test_frame_draws_a_tool_box_with_pi_padding() -> None:
    lines = (
        encode_tool_box_line("", "success", wrap=True),
        encode_tool_box_line("hello world again", "success", wrap=True),
        encode_tool_box_line("clipped-row-that-is-long", "error", wrap=False),
        encode_tool_box_line("", "none", wrap=True),
    )
    rows = block_lines(FrameBlock("tool_box", lines), 13)
    assert [(row.kind, row.text) for row in rows] == [
        ("tool_box_success", " "),
        ("tool_box_success", " hello world"),
        ("tool_box_success", " again"),
        ("tool_box_error", " clipped-row…"),
        ("tool_box_none", " "),
    ]
    assert decode_tool_box_line("untagged") == ("none", True, "untagged")


def test_tool_box_style_keeps_the_background_after_a_reset() -> None:
    style = ChromeStyle(enabled=True, truecolor=True)
    painted = style.tool_box(" a\x1b[0mb", bg="success", width=6)
    bg = "\x1b[48;2;37;65;49m"
    assert painted == f"{bg} a\x1b[0m{bg}b   \x1b[0m"
    assert style.tool_box(" x", bg="none", width=4) == " x\x1b[0m  "
    assert ChromeStyle(enabled=False).tool_box(" x", bg="error", width=4) == " x"


def test_plain_rows_have_no_escape_codes(tmp_path: Path) -> None:
    state = _state("grep", {"pattern": "x"}, tmp_path)
    state.result = _result("a:1: x")
    assert not any(_SGR.search(text) for _bg, text in _rows(state))


# -- live rows: asynchronous preview, resize ------------------------------------


class _Live:
    """A transcript and renderer with a resizable width."""

    def __init__(self, tmp_path: Path) -> None:
        self.width = 40
        inputs = ScreenRenderInputs(
            lambda: self.width, io.StringIO(), lambda: self.transcript.tools_expanded
        )
        self.transcript = TranscriptComponent(
            PaintLock(threading.RLock()),
            lambda: None,
            reset_scrollback=lambda: None,
            render_inputs=inputs,
        )
        self.renderer = TuiToolLoopRenderer(
            transcript=self.transcript,
            chrome=ExtensionChromeState(),
            render_inputs=inputs,
            cwd=tmp_path,
        )

    def call(self, name: str, args: dict[str, Any]) -> None:
        self.renderer.render_tool_call(
            AgentToolCall(f"c-{name}", name, ProductContent(json.dumps(args)))
        )

    def rows(self) -> list[list[str]]:
        return [
            _plain(lines)
            for kind, lines in self.transcript.history_blocks
            if kind == "tool_box"
        ]


def _plain(lines: tuple[str, ...]) -> list[str]:
    texts = (_SGR.sub("", decode_tool_box_line(line)[2]) for line in lines)
    return [text for text in texts if text]


def test_an_edit_preview_never_blocks_the_call_row(tmp_path: Path) -> None:
    """Pi computes the preview asynchronously; a FIFO must not hang the UI."""

    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    live = _Live(tmp_path)
    started = time.monotonic()
    live.call("edit", {"path": "pipe", "edits": [{"oldText": "a", "newText": "b"}]})
    assert time.monotonic() - started < 0.4
    assert live.transcript.pending_tool is not None
    live.renderer.render_tool_result(
        output_text="tool cancelled by escape", is_error=True
    )
    assert live.rows() == [["edit pipe", "tool cancelled by escape"]]
    # Unblock the preview thread so it can finish.
    with open(fifo, "w") as writer:
        writer.write("x")


def test_an_edit_preview_lands_in_the_running_row(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a = 1\n")
    live = _Live(tmp_path)
    live.call(
        "edit", {"path": "a.py", "edits": [{"oldText": "a = 1", "newText": "a = 2"}]}
    )
    thread = live.renderer._preview_thread
    assert thread is not None
    thread.join(2)
    assert _plain(live.transcript.pending_tool_lines) == [
        "edit a.py",
        "-1 a = 1",
        "+1 a = 2",
    ]


def test_a_late_preview_is_dropped_once_the_result_committed(tmp_path: Path) -> None:
    live = _Live(tmp_path)
    live.call("ls", {})
    state = live.transcript.pending_tool
    assert state is not None
    live.renderer.render_tool_result(output_text="a.py", is_error=False)
    live.transcript.apply_tool_preview(state, EditPreview(error="late"))
    assert state.preview is None


def test_a_resize_redraws_tool_rows_at_the_new_width(tmp_path: Path) -> None:
    live = _Live(tmp_path)
    live.call("bash", {"command": "x"})
    live.renderer.render_tool_result(output_text="a" * 30, is_error=False)
    assert live.rows() == [["$ x", "a" * 30]]
    live.width = 12
    live.transcript.refresh_tool_rows()
    # Content width 10: three rows of the wrapped output, as Pi draws them.
    assert live.rows() == [["$ x", "a" * 10, "a" * 10, "a" * 10]]

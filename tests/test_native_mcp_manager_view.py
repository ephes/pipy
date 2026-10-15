"""Tests for the view of the ``/mcp`` manager (MCP parity plan S34, rows
MGR-02, MGR-09 and MGR-10) and for the key loop that drives it.

The two status-screen cases mirror Pi's ``mcp-manager-view.test.ts``.
"""

from __future__ import annotations

import io
import re
import select
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.frame_renderer import visible_len
from pipy_harness.native.keybindings import KeybindingsManager
from pipy_harness.native.mcp.manager_menus import McpMenu, McpMenuItem
from pipy_harness.native.overlay_state import OverlayState
from pipy_harness.native.terminal_driver import TerminalDriver
from pipy_harness.native.tui import TerminalUi
from pipy_harness.native.ui.components.mcp_manager_view import (
    McpManagerView,
    mcp_manager_keys,
)
from pipy_harness.native.ui.components.search_selectors import (
    search_selector_region_lines,
)
from pipy_harness.native.ui.screen import KEY_WAIT_ENDED, Screen

_PLAIN = ChromeStyle(enabled=False)
_LINK = re.compile(r"\x1b\]8;;[!-~]*\x1b\\")
_CLICK = "Cmd+click to open" if sys.platform == "darwin" else "Ctrl+click to open"
_URL = "https://auth.example/authorize?client_id=abc&state=xyz"


def _view(copy: Callable[[str], str | None] = lambda text: None) -> McpManagerView:
    return McpManagerView(mcp_manager_keys(None), copy=copy)


def _unlinked(lines: list[str]) -> list[str]:
    """``lines`` without their OSC 8 hyperlink sequences."""

    return [_LINK.sub("", line) for line in lines]


def _rendered(view: McpManagerView, width: int = 80) -> str:
    return "\n".join(_unlinked(view.render(width, _PLAIN)))


def _row(mark: str, value: str, description: str) -> str:
    """A list row: the label in pi-tui's primary column of 32, then the
    description."""

    return f"{mark} {value:<32}{description}"


def _menu(*values: str, **fields: object) -> McpMenu:
    return McpMenu(
        title="MCP servers",
        items=tuple(McpMenuItem(value, value, f"about {value}") for value in values),
        confirm_label="manage",
        cancel_label="close",
        **fields,  # type: ignore[arg-type]
    )


# -- the status screen (Pi "MCP manager status screen") ----------------------


def test_cancels_the_running_operation_with_the_cancel_key() -> None:
    view = _view()
    cancelled: list[bool] = []
    view.status(
        "Sign in to issues",
        "Contacting the authorization server…",
        lambda: cancelled.append(True),
    )
    assert "cancel" in _rendered(view)
    view.handle_key("esc")
    assert cancelled == [True]


def test_ignores_the_cancel_key_for_operations_that_cannot_be_cancelled() -> None:
    view = _view()
    view.status("Sign in to issues", "Connecting…")
    assert "cancel" not in _rendered(view)
    view.handle_key("esc")
    assert _rendered(view).splitlines()[1:4] == [
        " Sign in to issues",
        "",
        " Connecting…",
    ]


def test_the_view_says_loading_until_it_is_told_what_to_show() -> None:
    lines = _view().render(20, _PLAIN)
    assert lines == ["─" * 20, " MCP servers", "", " Loading…", "─" * 20]


# -- menus ---------------------------------------------------------------------


def test_a_menu_shows_details_errors_items_and_what_the_keys_do() -> None:
    view = _view()
    view.menu(
        lambda: _menu(
            "docs",
            "issues",
            details="one\ntwo",
            error="config: broken",
            selected="issues",
        ),
        lambda choice: None,
    )
    assert view.render(60, _PLAIN) == [
        "─" * 60,
        " MCP servers",
        " one",
        " two",
        " config: broken",
        "",
        _row(" ", "docs", "about docs"),
        _row("→", "issues", "about issues"),
        "",
        " enter manage • escape/ctrl+c close",
        "─" * 60,
    ]


def test_the_keys_move_the_selection_and_confirm_or_cancel() -> None:
    chosen: list[str | None] = []
    view = _view()
    view.menu(lambda: _menu("a", "b", "c"), chosen.append)
    view.handle_key("down")
    view.handle_key("down")
    view.handle_key("up")
    view.handle_key("x")
    assert chosen == []
    view.handle_key("enter")
    assert chosen == ["b"]
    # A menu answers once.
    view.handle_key("enter")
    view.handle_key("esc")
    assert chosen == ["b"]
    view.menu(lambda: _menu("a", "b"), chosen.append)
    view.handle_key("up")  # wraps to the last item
    view.handle_key("ctrl-c")
    assert chosen == ["b", None]


def test_a_menu_without_items_says_so_and_can_only_be_left() -> None:
    chosen: list[str | None] = []
    view = _view()
    view.menu(lambda: _menu(empty="No MCP servers configured."), chosen.append)
    text = _rendered(view)
    assert " No MCP servers configured." in text
    assert "enter" not in text
    assert " escape/ctrl+c close" in text
    view.handle_key("enter")
    view.handle_key("down")
    assert chosen == []
    view.handle_key("esc")
    assert chosen == [None]
    view.menu(lambda: _menu(), chosen.append)
    assert " Nothing to show." in _rendered(view)


def test_a_callback_may_show_the_next_screen() -> None:
    view = _view()
    view.menu(
        lambda: _menu("docs"),
        lambda choice: view.status(f"Server {choice}", "Reconnecting…"),
    )
    view.handle_key("enter")
    assert " Server docs" in _rendered(view)


def test_a_live_menu_follows_the_servers_and_keeps_the_selected_item() -> None:
    menus = [_menu("a", "b", "c")]
    view = _view()
    view.menu(lambda: menus[0], lambda choice: None, live=True)
    view.handle_key("down")
    assert view.take_dirty()
    view.refresh()
    # Nothing changed: nothing to paint.
    assert not view.take_dirty()
    # The servers were sorted anew and one changed its state.
    menus[0] = McpMenu(
        title="MCP servers",
        items=(
            McpMenuItem("c", "c", "failed"),
            McpMenuItem("a", "a", "about a"),
            McpMenuItem("b", "b", "about b"),
        ),
        confirm_label="manage",
        cancel_label="close",
    )
    view.refresh()
    assert view.take_dirty()
    assert _row("→", "b", "about b") in _rendered(view)
    assert _row(" ", "c", "failed") in _rendered(view)
    # The selected item is gone: the first one is selected.
    menus[0] = _menu("x", "y")
    view.refresh()
    assert _row("→", "x", "about x") in _rendered(view)


def test_only_a_live_menu_that_is_still_open_is_built_again() -> None:
    built: list[int] = []

    def build() -> McpMenu:
        built.append(1)
        return _menu("a")

    view = _view()
    view.menu(build, lambda choice: None)
    view.refresh()
    assert len(built) == 1
    view.menu(build, lambda choice: None, live=True)
    view.handle_key("enter")
    view.refresh()
    assert len(built) == 2
    view.status("t", "m")
    view.refresh()
    assert len(built) == 2


def test_a_long_list_shows_twelve_items_around_the_selection() -> None:
    view = _view()
    view.menu(lambda: _menu(*(f"s{n:02}" for n in range(20))), lambda choice: None)
    lines = view.render(60, _PLAIN)
    assert len([line for line in lines if "about s" in line]) == 12
    assert "  (1/20)" in lines


# -- the sign-in screen (MGR-09) ---------------------------------------------


def test_the_sign_in_screen_shows_the_url_an_input_and_the_keys() -> None:
    view = _view()
    view.redirect_url("Sign in to issues", _URL, lambda answer: None)
    assert _unlinked(view.render(80, _PLAIN)) == [
        "─" * 80,
        " Sign in to issues",
        "",
        " Approve access in your browser. If it did not open, visit:",
        f" {_URL}",
        f" {_CLICK} • ctrl+x to copy",
        "",
        " If the browser runs on another machine, paste the URL it was redirected to:",
        "> " + " " * 78,
        "",
        " enter submit • escape/ctrl+c cancel",
        "─" * 80,
    ]


def test_the_url_and_the_click_hint_are_links_to_the_url() -> None:
    view = _view()
    view.redirect_url("Sign in to issues", _URL, lambda answer: None)
    link = f"\x1b]8;;{_URL}\x1b\\"
    close = "\x1b]8;;\x1b\\"
    lines = view.render(30, _PLAIN)
    start = lines.index(" Approve access in your") + 3
    url_lines = lines[start : start + 2]
    for line in url_lines:
        assert line.startswith(f" {link}") and line.endswith(close)
    assert lines[start + 2].startswith(f" {link}{_CLICK}{close} •")
    # The frame keeps the links (and closes each on its line).
    region = [line for line in _region_raw(view, height=40) if link in line]
    assert len(region) == 2


def test_a_url_the_frame_cannot_link_is_plain_text() -> None:
    view = _view()
    url = "https://auth.example/authorize?name=Jürgen"
    view.redirect_url("Sign in", url, lambda answer: None)
    lines = view.render(80, _PLAIN)
    assert f" {url}" in lines
    assert " ctrl+x to copy" in lines
    assert not [line for line in lines if "\x1b" in line]


def test_confirm_submits_a_pasted_url_that_is_not_empty() -> None:
    answers: list[str | None] = []
    view = _view()
    view.redirect_url("Sign in to issues", _URL, answers.append)
    view.handle_key("enter")
    view.handle_key(" ")
    view.handle_key("enter")
    assert answers == []
    view.paste("http://127.0.0.1:1234/callback?code=abc\n")
    for key in ("&", "x", "backspace"):
        view.handle_key(key)
    # The blank typed before is part of the input, not of the answer.
    assert ">  http://127.0.0.1:1234/callback?code=abc&" in _rendered(view)
    view.handle_key("enter")
    assert answers == ["http://127.0.0.1:1234/callback?code=abc&"]
    # The screen answers once.
    view.handle_key("enter")
    view.handle_key("esc")
    assert len(answers) == 1


def test_cancel_ends_the_sign_in_screen_without_an_answer() -> None:
    answers: list[str | None] = []
    view = _view()
    view.redirect_url("Sign in to issues", _URL, answers.append)
    view.handle_key("h")
    view.handle_key("esc")
    assert answers == [None]


def test_the_copy_key_copies_the_url_and_says_how_it_went() -> None:
    copied: list[str] = []
    failures: list[str | None] = [None]

    def copy(text: str) -> str | None:
        copied.append(text)
        return failures[0]

    view = _view(copy)
    view.redirect_url("Sign in to issues", _URL, lambda answer: None)
    view.handle_key("ctrl-x")
    assert copied == [_URL]
    assert " Copied URL to clipboard" in _rendered(view)
    assert "to copy" not in _rendered(view)
    failures[0] = "no clipboard tool found"
    view.handle_key("ctrl-x")
    assert " no clipboard tool found" in _rendered(view)
    # The key is not typed into the input.
    assert "> " + " " * 78 in _rendered(view)


def test_pasted_text_goes_nowhere_but_the_sign_in_input() -> None:
    view = _view()
    view.menu(lambda: _menu("a"), lambda choice: None)
    view.take_dirty()
    view.paste("text")
    assert not view.take_dirty()


@pytest.mark.parametrize("width", [20, 41, 80])
def test_no_line_is_wider_than_the_frame_allows(width: int) -> None:
    view = _view()
    screens: list[Callable[[], None]] = [
        lambda: view.menu(
            lambda: _menu("docs", details="x" * 200, error="y " * 90),
            lambda choice: None,
        ),
        lambda: view.menu(lambda: _menu(empty="e " * 60), lambda choice: None),
        lambda: view.status("t" * 100, "m" * 100, lambda: None),
        lambda: view.redirect_url("Sign in", _URL * 3, lambda answer: None),
    ]
    for show in screens:
        show()
        lines = view.render(width, ChromeStyle(enabled=True))
        assert all(visible_len(line) <= width for line in lines)


def test_key_hints_wrap_in_a_narrow_terminal() -> None:
    # MGR-10: both keys are named however narrow the terminal is.
    view = _view()
    view.redirect_url("Sign in", _URL, lambda answer: None)
    assert view.render(20, _PLAIN)[-5:] == [
        "",
        " enter submit •",
        " escape/ctrl+c",
        " cancel",
        "─" * 20,
    ]
    view.menu(lambda: _menu("docs"), lambda choice: None)
    hints = " ".join(line.strip() for line in view.render(20, _PLAIN)[-4:-1])
    assert hints == "enter manage • escape/ctrl+c close"


# -- a screen that is taller than the terminal ---------------------------------


def _region(view: McpManagerView, *, width: int = 80, height: int) -> list[str]:
    """The view as the frame shows it in ``height`` rows, the two footer rows
    of the session included."""

    overlays = OverlayState()
    overlays.begin_search_selector(view)
    lines = search_selector_region_lines(
        overlays,
        width=width,
        height=height,
        footer_lines=("footer one", "footer two"),
        style=_PLAIN,
    )
    assert len(lines) <= height
    return _unlinked([line.text for line in lines])


def _region_raw(view: McpManagerView, *, height: int) -> list[str]:
    overlays = OverlayState()
    overlays.begin_search_selector(view)
    lines = search_selector_region_lines(
        overlays,
        width=80,
        height=height,
        footer_lines=("footer one", "footer two"),
        style=_PLAIN,
    )
    return [line.text for line in lines]


def test_a_long_error_leaves_the_actions_on_the_screen() -> None:
    stderr = "\n".join(f"stderr line {n}" for n in range(24))
    view = _view()
    view.menu(
        lambda: _menu(
            "reconnect", "disable", details="npx server\nState: failed", error=stderr
        ),
        lambda choice: None,
    )
    # 18 rows for the view: a window over the text, the rest stays.
    assert _region(view, height=20) == [
        "─" * 80,
        " MCP servers",
        " npx server",
        " State: failed",
        *(f" stderr line {n}" for n in range(7)),
        " … lines 1-9 of 26, pageup/pagedown to scroll",
        "",
        _row("→", "reconnect", "about reconnect"),
        _row(" ", "disable", "about disable"),
        "",
        " enter manage • escape/ctrl+c close",
        "─" * 80,
        "footer one",
        "footer two",
    ]
    # With room for everything nothing is cut.
    assert not [line for line in _region(view, height=60) if "to scroll" in line]


def test_a_long_url_leaves_the_input_and_the_keys_on_the_screen() -> None:
    view = _view()
    view.redirect_url("Sign in to issues", _URL * 25, lambda answer: None)
    assert len(view.render(80, _PLAIN)) > 24
    lines = _region(view, height=20)
    assert len(lines) == 20
    assert lines[11:] == [
        " … lines 1-7 of 19, pageup/pagedown to scroll",
        "",
        " If the browser runs on another machine, paste the URL it was redirected to:",
        "> " + " " * 78,
        "",
        " enter submit • escape/ctrl+c cancel",
        "─" * 80,
        "footer one",
        "footer two",
    ]
    # The hints below the URL are scrolled to with it.
    for _ in range(3):
        view.handle_key("pagedown")
    assert f" {_CLICK} • ctrl+x to copy" in _region(view, height=20)


def test_what_a_failed_copy_says_can_be_scrolled_to() -> None:
    failure = "no clipboard command available and the terminal refused OSC 52"
    view = _view(lambda text: failure)
    view.redirect_url("Sign in to issues", _URL, lambda answer: None)
    before = _region(view, width=20, height=24)
    view.handle_key("ctrl-x")
    after = _region(view, width=20, height=24)
    assert len(after) == len(before) == 24
    # Every control stays below the window over the URL and the hint.
    assert after[7:] == [
        " https://auth.examp",
        " le/authorize?clien",
        " … lines 1-2 of 8, …",
        "",
        " If the browser",
        " runs on another",
        " machine, paste the",
        " URL it was",
        " redirected to:",
        "> " + " " * 18,
        "",
        " enter submit •",
        " escape/ctrl+c",
        " cancel",
        "─" * 20,
        "footer one",
        "footer two",
    ]
    for _ in range(3):
        view.handle_key("pagedown")
    assert _region(view, width=20, height=24)[7:10] == [
        " and the terminal",
        " refused OSC 52",
        " … lines 7-8 of 8, …",
    ]


def test_a_long_list_shows_fewer_items_in_a_short_terminal() -> None:
    view = _view()
    view.menu(lambda: _menu(*(f"s{n:02}" for n in range(20))), lambda choice: None)
    for _ in range(9):
        view.handle_key("down")
    lines = _region(view, height=12)
    shown = [line for line in lines if "about s" in line]
    # Ten rows for the view: three items and the position line fit between
    # the title and the key hints.
    assert len(shown) == 3
    assert _row("→", "s09", "about s09") in shown
    assert "  (10/20)" in lines
    assert " enter manage • escape/ctrl+c close" in lines
    # With the rows for them, twelve items again.
    assert len([line for line in _region(view, height=40) if "about s" in line]) == 12


def test_a_status_message_is_cut_short_too() -> None:
    view = _view()
    view.status("Sign in", "\n".join(f"line {n}" for n in range(30)), lambda: None)
    assert _region(view, height=10) == [
        "─" * 80,
        " Sign in",
        "",
        " line 0",
        " … lines 1-1 of 30, pageup/pagedown to scroll",
        "",
        " escape/ctrl+c cancel",
        "─" * 80,
        "footer one",
        "footer two",
    ]


def test_a_long_url_is_wrapped_and_keeps_every_character() -> None:
    view = _view()
    view.redirect_url("Sign in", _URL, lambda answer: None)
    lines = _unlinked(view.render(30, _PLAIN))
    start = lines.index(" Approve access in your") + 3
    end = lines.index(f" {_CLICK} • ctrl+x")
    assert "".join(line[1:] for line in lines[start:end]) == _URL


def test_page_keys_scroll_a_message_that_does_not_fit() -> None:
    view = _view()
    view.status("Sign in", "\n".join(f"line {n}" for n in range(12)), lambda: None)

    def window() -> list[str]:
        return _region(view, height=12)[3:7]

    # Four rows for the text: three lines of it and where they are.
    assert window() == [
        " line 0",
        " line 1",
        " line 2",
        " … lines 1-3 of 12, pageup/pagedown to scroll",
    ]
    view.handle_key("pagedown")
    assert window()[:3] == [" line 3", " line 4", " line 5"]
    # Not past the end, and back up from there.
    for _ in range(10):
        view.handle_key("pagedown")
    assert window() == [
        " line 9",
        " line 10",
        " line 11",
        " … lines 10-12 of 12, pageup/pagedown to scroll",
    ]
    view.handle_key("pageup")
    assert window()[0] == " line 6"
    for _ in range(10):
        view.handle_key("pageup")
    assert window()[0] == " line 0"


def test_a_long_error_scrolls_while_the_menu_keeps_its_keys() -> None:
    chosen: list[str | None] = []
    stderr = "\n".join(f"stderr line {n}" for n in range(24))
    menu = _menu("reconnect", "disable", details="State: failed", error=stderr)
    view = _view()
    view.menu(lambda: menu, chosen.append, live=True)
    _region(view, height=20)
    view.handle_key("pagedown")
    view.refresh()
    lines = _region(view, height=20)
    # A live menu built again keeps the window where it was.
    assert lines[2:4] == [" stderr line 8", " stderr line 9"]
    assert _row("→", "reconnect", "about reconnect") in lines
    view.handle_key("down")
    view.handle_key("enter")
    assert chosen == ["disable"]


def test_a_long_error_above_a_long_list_keeps_rows_to_scroll() -> None:
    stderr = "\n".join(f"stderr line {n}" for n in range(24))
    view = _view()
    view.menu(
        lambda: _menu(*(f"s{n:02}" for n in range(20)), error=stderr),
        lambda choice: None,
    )
    lines = _region(view, height=20)
    assert lines[2:5] == [
        " stderr line 0",
        " stderr line 1",
        " … lines 1-2 of 24, pageup/pagedown to scroll",
    ]
    assert " enter manage • escape/ctrl+c close" in lines
    view.handle_key("pagedown")
    assert _region(view, height=20)[2] == " stderr line 2"


def test_a_copy_key_bound_to_a_page_key_still_copies() -> None:
    copied: list[str] = []

    def copy(text: str) -> None:
        copied.append(text)

    keys = mcp_manager_keys(KeybindingsManager({"app.message.copy": "pageDown"}))
    view = McpManagerView(keys, copy=copy)
    view.redirect_url("Sign in", _URL * 25, lambda answer: None)
    before = _region(view, height=20)
    view.handle_key("pagedown")
    assert copied == [_URL * 25]
    # The window did not move; pageUp still scrolls (nothing above here).
    assert _region(view, height=20)[:10] == before[:10]
    # Elsewhere there is nothing to copy, so the key scrolls.
    view.status("Sign in", "\n".join(f"line {n}" for n in range(30)), lambda: None)
    assert _region(view, height=12)[3] == " line 0"
    view.handle_key("pagedown")
    assert _region(view, height=12)[3] == " line 3"
    stderr = "\n".join(f"stderr line {n}" for n in range(24))
    view.menu(lambda: _menu("reconnect", error=stderr), lambda choice: None)
    assert _region(view, height=20)[2] == " stderr line 0"
    view.handle_key("pagedown")
    assert _region(view, height=20)[2] != " stderr line 0"
    assert copied == [_URL * 25]


def test_page_keys_do_not_reach_the_input_of_the_sign_in_screen() -> None:
    view = _view()
    view.redirect_url("Sign in", _URL * 25, lambda answer: None)
    before = _region(view, height=20)
    view.handle_key("pagedown")
    after = _region(view, height=20)
    assert after != before
    assert "> " + " " * 78 in after


def test_the_copy_key_is_the_users_app_message_copy_binding() -> None:
    copied: list[str] = []

    def copy(text: str) -> None:
        copied.append(text)

    keys = mcp_manager_keys(KeybindingsManager({"app.message.copy": "ctrl+y"}))
    view = McpManagerView(keys, copy=copy)
    view.redirect_url("Sign in to issues", _URL, lambda answer: None)
    assert f" {_CLICK} • ctrl+y to copy" in _unlinked(view.render(80, _PLAIN))
    view.handle_key("ctrl-x")
    assert copied == []
    view.handle_key("ctrl-y")
    assert copied == [_URL]
    # Unbound, nothing copies.
    keys = mcp_manager_keys(KeybindingsManager({"app.message.copy": []}))
    view = McpManagerView(keys, copy=copy)
    view.redirect_url("Sign in to issues", _URL, lambda answer: None)
    view.handle_key("ctrl-x")
    assert copied == [_URL]


# -- the key loop --------------------------------------------------------------


class _Session:
    """Stands for the flow: closes the view after some polls."""

    def __init__(self, view: McpManagerView, close_after: int | None = None) -> None:
        self.view = view
        self.polls = 0
        self.stopped = 0
        self.close_after = close_after
        self.on_poll: Callable[[], None] = lambda: None

    def poll(self) -> None:
        self.polls += 1
        self.on_poll()
        if self.polls == self.close_after:
            self.view.close()

    def stop(self) -> None:
        self.stopped += 1


def _terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keys: list[str | None]
) -> tuple[TerminalUi, list[str]]:
    """A terminal UI whose key loop reads ``keys`` and then finds no input."""

    painted: list[str] = []

    def paint(screen: Screen) -> None:
        lines = screen._live_region_lines(width=72, height=20)  # noqa: SLF001
        painted.append("\n".join(line.text for line in lines))

    monkeypatch.setattr(Screen, "paint", paint)
    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda self: None)
    monkeypatch.setattr(TerminalDriver, "has_pending_input", lambda self: bool(keys))
    monkeypatch.setattr(TerminalDriver, "read_key", lambda self, fd: keys.pop(0))
    monkeypatch.setattr(select, "select", lambda r, w, x, timeout: ([], [], []))
    input_path = tmp_path / "input"
    input_path.write_text("", encoding="utf-8")
    stream = input_path.open(encoding="utf-8")
    ui = TerminalUi(input_stream=stream, terminal_stream=io.StringIO(), cwd=tmp_path)
    return ui, painted


def test_the_manager_stands_in_place_of_the_editor_and_follows_its_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui, painted = _terminal(tmp_path, monkeypatch, ["down", "enter"])
    chosen: list[str | None] = []
    sessions: list[_Session] = []

    def start(view: McpManagerView) -> _Session:
        def choose(choice: str | None) -> None:
            chosen.append(choice)
            view.close()

        view.menu(lambda: _menu("docs", "issues"), choose, live=True)
        sessions.append(_Session(view))
        return sessions[0]

    ui.components.modals.run_mcp_manager(start, copy=lambda text: None)
    assert chosen == ["issues"]
    assert sessions[0].stopped == 1
    assert "→ docs" in painted[0]
    assert any("→ issues" in frame for frame in painted)
    # The view is gone and the editor is back.
    assert ui.components.overlays.search_selector is None
    assert "MCP servers" not in painted[-1]


def test_the_key_loop_polls_the_flow_and_ends_when_the_view_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No key ever arrives: only the flow can end the view.
    ui, painted = _terminal(tmp_path, monkeypatch, [])
    sessions: list[_Session] = []

    def start(view: McpManagerView) -> _Session:
        view.status("Sign in to issues", "Contacting the authorization server…")
        session = _Session(view, close_after=5)

        def on_poll() -> None:
            if session.polls == 3:
                view.status("Sign in to issues", "Connecting…")

        session.on_poll = on_poll
        sessions.append(session)
        return session

    ui.components.modals.run_mcp_manager(start, copy=lambda text: None)
    assert sessions[0].polls == 5
    assert sessions[0].stopped == 1
    connecting = [
        index for index, frame in enumerate(painted) if "Connecting…" in frame
    ]
    # What the flow showed in between was painted, once.
    assert len(connecting) == 1
    assert "Contacting the authorization server…" in painted[connecting[0] - 1]


def test_pasted_text_and_the_end_of_input_reach_the_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui, painted = _terminal(tmp_path, monkeypatch, ["paste", None])
    monkeypatch.setattr(
        TerminalDriver, "consume_paste", lambda self: "http://127.0.0.1/cb?code=1"
    )
    answers: list[str | None] = []
    sessions: list[_Session] = []

    def start(view: McpManagerView) -> _Session:
        view.redirect_url("Sign in to issues", _URL, answers.append)
        sessions.append(_Session(view))
        return sessions[0]

    ui.components.modals.run_mcp_manager(start, copy=lambda text: None)
    assert any("> http://127.0.0.1/cb?code=1" in frame for frame in painted)
    # End of input closes the view; the flow is told to stop what it waits for.
    assert answers == []
    assert sessions[0].stopped == 1


def test_a_view_that_closed_while_it_started_is_never_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui, painted = _terminal(tmp_path, monkeypatch, [])
    sessions: list[_Session] = []

    def start(view: McpManagerView) -> _Session:
        view.close()
        sessions.append(_Session(view))
        return sessions[0]

    ui.components.modals.run_mcp_manager(start, copy=lambda text: None)
    assert sessions[0].polls == 0
    assert sessions[0].stopped == 1
    assert not any("MCP servers" in frame for frame in painted)


def test_a_wait_for_a_key_without_a_poll_is_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui, _ = _terminal(tmp_path, monkeypatch, ["a"])
    screen = ui.components.screen
    assert screen.read_key_polling_resize(0) == "a"
    assert screen.read_key_polling_resize(0, lambda: True) == KEY_WAIT_ENDED

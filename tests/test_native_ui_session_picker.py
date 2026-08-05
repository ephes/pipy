"""Unit tests for the session-picker component and its overlay renderer.

These drive ``SessionPickerComponent`` directly against a plain overlay record
(no terminal shell, no PTY) to prove navigation, search, scope/sort/named
toggles, rename and delete actions, cancel keys, paste handling, and
escape-safe / resize-coherent rendering. The picker must never run a provider
turn; the real-PTY coverage lives in ``test_native_session_picker_pty.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pipy_harness.native.overlay_state import OverlayState
from pipy_harness.native.session_tree_commands import SessionListEntry
from pipy_harness.native.ui.components.session_picker import (
    SessionPickerClose,
    SessionPickerComponent,
    session_picker_region_lines,
)
from pipy_harness.native.ui.paint_lock import PaintLock


def _entry(name, sid, *, cwd="/ws", mtime=0.0) -> SessionListEntry:
    return SessionListEntry(
        path=Path(f"/store/{sid}.jsonl"),
        session_id=sid,
        name=name,
        message_count=1,
        cwd=cwd,
        mtime=mtime,
    )


class _Harness:
    """A component wired to a plain overlay record, counting effects."""

    def __init__(
        self,
        project: Sequence[SessionListEntry],
        all_sessions: Sequence[SessionListEntry] | None = None,
        *,
        current: Path | None = None,
        on_rename=None,
        on_delete=None,
    ) -> None:
        self.overlays = OverlayState()
        self.repaints = 0
        self.pastes = 0
        self.component = SessionPickerComponent(
            self.overlays,
            PaintLock(),
            self._repaint,
            on_rename=on_rename,
            on_delete=on_delete,
            consume_paste=self._consume_paste,
        )
        self.component.open(
            project_sessions=list(project),
            all_sessions=list(all_sessions if all_sessions is not None else project),
            current_path=current,
            now=1000.0,
        )

    def _repaint(self) -> None:
        self.repaints += 1

    def _consume_paste(self) -> None:
        self.pastes += 1

    def feed(self, key: str | None) -> SessionPickerClose | None:
        return self.component.handle_key(key)


def test_open_activates_overlay_and_defaults_now_to_wall_clock() -> None:
    harness = _Harness([_entry("a", "111")])
    assert harness.overlays.is_open("session_picker")
    assert harness.overlays.session_now == 1000.0
    assert harness.repaints == 1

    overlays = OverlayState()
    component = SessionPickerComponent(
        overlays,
        PaintLock(),
        lambda: None,
        on_rename=None,
        on_delete=None,
        consume_paste=lambda: None,
    )
    component.open(project_sessions=[], all_sessions=[])
    assert overlays.session_now > 0.0


def test_navigate_and_enter_returns_path_and_closes() -> None:
    harness = _Harness([_entry("a", "111", mtime=2.0), _entry("b", "222", mtime=1.0)])
    # newest-first: 111 then 222; move down to 222 and select.
    assert harness.feed("down") is None
    closed = harness.feed("enter")
    assert closed == SessionPickerClose(Path("/store/222.jsonl"))
    assert not harness.overlays.is_open("session_picker")
    assert harness.overlays.session_rows == ()


def test_enter_with_empty_rows_keeps_the_overlay_open() -> None:
    harness = _Harness([])
    assert harness.feed("enter") is None
    assert harness.overlays.is_open("session_picker")


def test_cancel_keys_close_without_a_choice() -> None:
    for key in ("esc", "ctrl-c", "ctrl-d", None):
        harness = _Harness([_entry("a", "111")])
        assert harness.feed(key) == SessionPickerClose(None)
        assert not harness.overlays.is_open("session_picker")


def test_paste_is_consumed_in_list_mode_and_ignored_in_rename_mode() -> None:
    harness = _Harness([_entry("a", "111")], on_rename=lambda *_a: None)
    assert harness.feed("paste") is None
    assert harness.pastes == 1
    harness.feed("\x12")  # Ctrl+R -> rename mode
    assert harness.feed("paste") is None
    assert harness.pastes == 1  # the sub-mode leaves the paste buffer alone


def test_search_filters_rows() -> None:
    harness = _Harness([_entry("alpha", "111"), _entry("beta", "222")])
    for ch in "bet":
        harness.feed(ch)
    assert [r.session_id for r in harness.overlays.session_rows] == ["222"]
    assert harness.feed("backspace") is None
    harness.feed("backspace")
    harness.feed("backspace")
    assert {r.session_id for r in harness.overlays.session_rows} == {"111", "222"}


def test_backspace_on_empty_query_does_not_repaint() -> None:
    harness = _Harness([_entry("a", "111")])
    repaints = harness.repaints
    assert harness.feed("backspace") is None
    assert harness.repaints == repaints


def test_tab_toggles_scope() -> None:
    project = [_entry("a", "111")]
    everything = [_entry("a", "111"), _entry("o", "222", cwd="/other")]
    harness = _Harness(project, everything)
    assert [r.session_id for r in harness.overlays.session_rows] == ["111"]
    harness.feed("tab")
    assert {r.session_id for r in harness.overlays.session_rows} == {"111", "222"}


def test_sort_and_named_toggles() -> None:
    harness = _Harness([_entry("b", "111", mtime=2.0), _entry(None, "222", mtime=1.0)])
    harness.feed("\x13")  # Ctrl+S -> name sort (unnamed last)
    assert [r.session_id for r in harness.overlays.session_rows] == ["111", "222"]
    harness.feed("\x0e")  # Ctrl+N -> named only
    assert [r.session_id for r in harness.overlays.session_rows] == ["111"]


def test_rename_flow_invokes_callback() -> None:
    renamed: list[tuple[Path, str]] = []
    harness = _Harness(
        [_entry("old", "111")],
        on_rename=lambda path, name: renamed.append((path, name)),
    )
    harness.feed("\x12")  # Ctrl+R
    assert harness.overlays.session_mode == "rename"
    # Clear seeded name, type a new one.
    for _ in range(len("old")):
        harness.feed("backspace")
    for ch in "new":
        harness.feed(ch)
    harness.feed("enter")
    assert renamed == [(Path("/store/111.jsonl"), "new")]
    assert harness.overlays.session_mode == "list"
    assert "renamed" in harness.overlays.session_status
    assert harness.overlays.session_rows[0].name == "new"


def test_rename_without_callback_is_a_no_op() -> None:
    harness = _Harness([_entry("a", "111")])
    repaints = harness.repaints
    assert harness.feed("\x12") is None
    assert harness.overlays.session_mode == "list"
    assert harness.repaints == repaints


def test_rename_esc_backs_out_to_list() -> None:
    harness = _Harness([_entry("a", "111")], on_rename=lambda *_a: None)
    harness.feed("\x12")
    assert harness.overlays.session_mode == "rename"
    assert harness.feed("esc") is None
    assert harness.overlays.session_mode == "list"
    assert harness.overlays.session_input == ""


def test_delete_flow_confirms_and_removes() -> None:
    deleted: list[Path] = []

    def on_delete(path: Path) -> tuple[bool, str]:
        deleted.append(path)
        return True, "deleted"

    harness = _Harness([_entry("a", "111"), _entry("b", "222")], on_delete=on_delete)
    harness.feed("\x18")  # Ctrl+X
    assert harness.overlays.session_mode == "confirm-delete"
    harness.feed("y")
    assert deleted == [Path("/store/111.jsonl")]
    assert [r.session_id for r in harness.overlays.session_rows] == ["222"]


def test_delete_confirm_enter_is_no() -> None:
    deleted: list[Path] = []

    def on_delete(path: Path) -> tuple[bool, str]:
        deleted.append(path)
        return True, "deleted"

    harness = _Harness([_entry("a", "111"), _entry("b", "222")], on_delete=on_delete)
    harness.feed("\x18")  # Ctrl+X -> confirm-delete
    assert harness.overlays.session_mode == "confirm-delete"
    harness.feed("enter")  # [y/N] default No
    assert deleted == []
    assert harness.overlays.session_mode == "list"
    assert [r.session_id for r in harness.overlays.session_rows] == ["111", "222"]


def test_failed_delete_reports_detail_and_keeps_the_row() -> None:
    harness = _Harness(
        [_entry("a", "111")], on_delete=lambda _path: (False, "delete failed")
    )
    harness.feed("\x18")
    harness.feed("y")
    assert harness.overlays.session_status == "delete failed"
    assert [r.session_id for r in harness.overlays.session_rows] == ["111"]


def test_ctrl_d_cancels_picker_from_sub_modes() -> None:
    # Ctrl-D must cancel the whole picker consistently, including from the
    # rename and delete-confirmation sub-modes (not just list mode).
    for opener in ("\x12", "\x18"):  # Ctrl+R (rename), Ctrl+X (delete)
        harness = _Harness(
            [_entry("a", "111"), _entry("b", "222")],
            on_rename=lambda *_a: None,
            on_delete=lambda _p: (True, "ok"),
        )
        harness.feed(opener)
        assert harness.overlays.session_mode in {"rename", "confirm-delete"}
        assert harness.feed("ctrl-d") == SessionPickerClose(None)
        assert not harness.overlays.is_open("session_picker")


def test_delete_blocked_on_current_session() -> None:
    calls: list[Path] = []

    def on_delete(path: Path) -> tuple[bool, str]:
        calls.append(path)
        return True, "deleted"

    harness = _Harness(
        [_entry("a", "111")],
        current=Path("/store/111.jsonl"),
        on_delete=on_delete,
    )
    harness.feed("\x18")
    assert harness.overlays.session_mode == "list"
    assert "cannot delete" in harness.overlays.session_status
    assert calls == []


def test_status_line_is_escape_safe() -> None:
    harness = _Harness([_entry("a", "111")])
    harness.overlays.session_status = "deleted \x1b[31mx\x07"
    lines = session_picker_region_lines(
        harness.overlays, width=80, height=24, footer_lines=("", "")
    )
    for fl in lines:
        assert "\x1b" not in fl.text
        assert "\x07" not in fl.text


def test_rename_prompt_sanitizes_seeded_name() -> None:
    harness = _Harness([_entry("safe\x1b[31mname", "111")], on_rename=lambda *_a: None)
    harness.feed("\x12")  # Ctrl+R seeds input from the name
    assert harness.overlays.session_mode == "rename"
    lines = session_picker_region_lines(
        harness.overlays, width=80, height=24, footer_lines=("", "")
    )
    assert any("rename:" in fl.text for fl in lines)
    for fl in lines:
        assert "\x1b" not in fl.text


def test_region_lines_render_empty_pool_with_title_and_footer() -> None:
    harness = _Harness([])
    lines = session_picker_region_lines(
        harness.overlays, width=80, height=10, footer_lines=("left", "right")
    )
    assert lines[0].kind == "selector_title"
    assert "Resume session" in lines[0].text
    assert any("(no native sessions)" in fl.text for fl in lines)
    assert [line.kind for line in lines[-2:]] == ["footer", "footer"]
    assert [line.text for line in lines[-2:]] == ["left", "right"]


def test_region_lines_window_overflow_and_report_position() -> None:
    harness = _Harness([_entry(f"s{index}", str(index)) for index in range(20)])
    harness.overlays.session_selection = 10
    lines = session_picker_region_lines(
        harness.overlays, width=80, height=12, footer_lines=("", "")
    )
    option_lines = [line for line in lines if line.kind.startswith("selector_option")]
    assert len(option_lines) == 7  # height 12 - title/prompt/scroll/footer = 7 rows
    assert any(line.kind == "selector_option_selected" for line in option_lines)
    scroll = [line for line in lines if line.kind == "slash_menu_scroll"]
    assert [line.text for line in scroll] == ["  (11/20)"]


def test_region_lines_escape_safe_and_resize_coherent() -> None:
    harness = _Harness(
        [_entry("name\x1b[31m", "111", cwd="/a\x07b")],
        current=Path("/store/111.jsonl"),
    )
    for width, height in ((80, 24), (100, 40), (62, 13)):
        lines = session_picker_region_lines(
            harness.overlays, width=width, height=height, footer_lines=("", "")
        )
        for fl in lines:
            assert "\x1b" not in fl.text
            assert "\x07" not in fl.text
            assert len(fl.text) <= width
        # Current session marker is visible.
        assert any("●" in fl.text for fl in lines)

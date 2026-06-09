"""State-machine tests for the interactive session-picker overlay.

These drive ``ToolLoopTerminalUi``'s session-picker key handler directly (no
real PTY) to prove navigation, search, scope/sort/named toggles, rename and
delete actions, cancel keys, and escape-safe / resize-coherent rendering. The
picker must never run a provider turn.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TextIO, cast

from pipy_harness.native.session_tree_commands import SessionListEntry
from pipy_harness.native.tui import ToolLoopTerminalUi, _PICKER_CONTINUE


class _TtyBuffer:
    def __init__(self) -> None:
        self._buffer = io.StringIO()

    def write(self, text: str) -> int:
        return self._buffer.write(text)

    def flush(self) -> None:
        self._buffer.flush()

    def isatty(self) -> bool:
        return True

    def getvalue(self) -> str:
        return self._buffer.getvalue()


def _ui(tmp_path: Path) -> ToolLoopTerminalUi:
    return ToolLoopTerminalUi(
        input_stream=cast(TextIO, io.StringIO()),
        terminal_stream=cast(TextIO, _TtyBuffer()),
        cwd=tmp_path,
    )


def _entry(name, sid, *, cwd="/ws", mtime=0.0) -> SessionListEntry:
    return SessionListEntry(
        path=Path(f"/store/{sid}.jsonl"),
        session_id=sid,
        name=name,
        message_count=1,
        cwd=cwd,
        mtime=mtime,
    )


def _open(
    ui: ToolLoopTerminalUi,
    project,
    all_sessions=None,
    *,
    current=None,
) -> None:
    ui._session_picker_project = list(project)
    ui._session_picker_all = list(all_sessions if all_sessions is not None else project)
    ui.session_picker_current = current
    ui.session_picker_open = True
    ui.session_picker_mode = "list"
    ui.session_picker_scope = "current"
    ui.session_picker_sort = "recent"
    ui.session_picker_named_only = False
    ui.session_picker_query = ""
    ui.session_picker_selection = 0
    ui._session_picker_now = 1000.0
    ui._rebuild_session_picker_rows()
    ui._session_picker_select_current()


def _feed(ui, key, *, on_rename=None, on_delete=None):
    return ui._handle_session_picker_key(key, on_rename=on_rename, on_delete=on_delete)


def test_navigate_and_enter_returns_path(tmp_path) -> None:
    ui = _ui(tmp_path)
    _open(ui, [_entry("a", "111", mtime=2.0), _entry("b", "222", mtime=1.0)])
    # newest-first: 111 then 222; move down to 222 and select.
    assert _feed(ui, "down") is _PICKER_CONTINUE
    outcome = _feed(ui, "enter")
    assert outcome == Path("/store/222.jsonl")


def test_cancel_keys_return_none(tmp_path) -> None:
    for key in ("esc", "ctrl-c", "ctrl-d", None):
        ui = _ui(tmp_path)
        _open(ui, [_entry("a", "111")])
        assert _feed(ui, key) is None


def test_search_filters_rows(tmp_path) -> None:
    ui = _ui(tmp_path)
    _open(ui, [_entry("alpha", "111"), _entry("beta", "222")])
    for ch in "bet":
        _feed(ui, ch)
    assert [r.session_id for r in ui.session_picker_rows] == ["222"]
    assert _feed(ui, "backspace") is _PICKER_CONTINUE
    _feed(ui, "backspace")
    _feed(ui, "backspace")
    assert {r.session_id for r in ui.session_picker_rows} == {"111", "222"}


def test_tab_toggles_scope(tmp_path) -> None:
    ui = _ui(tmp_path)
    project = [_entry("a", "111")]
    everything = [_entry("a", "111"), _entry("o", "222", cwd="/other")]
    _open(ui, project, everything)
    assert [r.session_id for r in ui.session_picker_rows] == ["111"]
    _feed(ui, "tab")
    assert {r.session_id for r in ui.session_picker_rows} == {"111", "222"}


def test_sort_and_named_toggles(tmp_path) -> None:
    ui = _ui(tmp_path)
    _open(ui, [_entry("b", "111", mtime=2.0), _entry(None, "222", mtime=1.0)])
    _feed(ui, "\x13")  # Ctrl+S -> name sort (unnamed last)
    assert [r.session_id for r in ui.session_picker_rows] == ["111", "222"]
    _feed(ui, "\x0e")  # Ctrl+N -> named only
    assert [r.session_id for r in ui.session_picker_rows] == ["111"]


def test_rename_flow_invokes_callback(tmp_path) -> None:
    ui = _ui(tmp_path)
    _open(ui, [_entry("old", "111")])
    renamed: list[tuple[Path, str]] = []

    def on_rename(path, name):
        renamed.append((path, name))

    _feed(ui, "\x12", on_rename=on_rename)  # Ctrl+R
    assert ui.session_picker_mode == "rename"
    # Clear seeded name, type a new one.
    for _ in range(len("old")):
        _feed(ui, "backspace", on_rename=on_rename)
    for ch in "new":
        _feed(ui, ch, on_rename=on_rename)
    _feed(ui, "enter", on_rename=on_rename)
    assert renamed == [(Path("/store/111.jsonl"), "new")]
    assert ui.session_picker_mode == "list"
    assert "renamed" in ui.session_picker_status
    assert ui.session_picker_rows[0].name == "new"


def test_delete_flow_confirms_and_removes(tmp_path) -> None:
    ui = _ui(tmp_path)
    _open(ui, [_entry("a", "111"), _entry("b", "222")])
    deleted: list[Path] = []

    def on_delete(path):
        deleted.append(path)
        return True, "deleted"

    _feed(ui, "\x18", on_delete=on_delete)  # Ctrl+X
    assert ui.session_picker_mode == "confirm-delete"
    _feed(ui, "y", on_delete=on_delete)
    assert deleted == [Path("/store/111.jsonl")]
    assert [r.session_id for r in ui.session_picker_rows] == ["222"]


def test_delete_confirm_enter_is_no(tmp_path) -> None:
    ui = _ui(tmp_path)
    _open(ui, [_entry("a", "111"), _entry("b", "222")])
    deleted: list[Path] = []

    def on_delete(path):
        deleted.append(path)
        return True, "deleted"

    _feed(ui, "\x18", on_delete=on_delete)  # Ctrl+X -> confirm-delete
    assert ui.session_picker_mode == "confirm-delete"
    _feed(ui, "enter", on_delete=on_delete)  # [y/N] default No
    assert deleted == []
    assert ui.session_picker_mode == "list"
    assert [r.session_id for r in ui.session_picker_rows] == ["111", "222"]


def test_ctrl_d_cancels_picker_from_sub_modes(tmp_path) -> None:
    # Ctrl-D must cancel the whole picker consistently, including from the
    # rename and delete-confirmation sub-modes (not just list mode).
    for opener in ("\x12", "\x18"):  # Ctrl+R (rename), Ctrl+X (delete)
        ui = _ui(tmp_path)
        _open(ui, [_entry("a", "111"), _entry("b", "222")])
        _feed(ui, opener, on_rename=lambda *a: None, on_delete=lambda p: (True, "ok"))
        assert ui.session_picker_mode in {"rename", "confirm-delete"}
        assert _feed(ui, "ctrl-d") is None


def test_status_line_is_escape_safe(tmp_path) -> None:
    ui = _ui(tmp_path)
    _open(ui, [_entry("a", "111")])
    ui.session_picker_status = "deleted \x1b[31mx\x07"
    lines = ui._session_picker_region_lines(width=80, height=24)
    for fl in lines:
        assert "\x1b" not in fl.text
        assert "\x07" not in fl.text


def test_rename_prompt_sanitizes_seeded_name(tmp_path) -> None:
    ui = _ui(tmp_path)
    _open(ui, [_entry("safe\x1b[31mname", "111")])

    def on_rename(path, name):
        pass

    _feed(ui, "\x12", on_rename=on_rename)  # Ctrl+R seeds input from the name
    assert ui.session_picker_mode == "rename"
    lines = ui._session_picker_region_lines(width=80, height=24)
    assert any("rename:" in fl.text for fl in lines)
    for fl in lines:
        assert "\x1b" not in fl.text


def test_delete_blocked_on_current_session(tmp_path) -> None:
    ui = _ui(tmp_path)
    _open(ui, [_entry("a", "111")], current=Path("/store/111.jsonl"))
    calls: list[Path] = []

    def on_delete(path):
        calls.append(path)
        return True, "deleted"

    _feed(ui, "\x18", on_delete=on_delete)
    assert ui.session_picker_mode == "list"
    assert "cannot delete" in ui.session_picker_status
    assert calls == []


def test_region_lines_escape_safe_and_resize_coherent(tmp_path) -> None:
    ui = _ui(tmp_path)
    _open(ui, [_entry("name\x1b[31m", "111", cwd="/a\x07b")], current=Path("/store/111.jsonl"))
    for width, height in ((80, 24), (100, 40), (62, 13)):
        lines = ui._session_picker_region_lines(width=width, height=height)
        for fl in lines:
            assert "\x1b" not in fl.text
            assert "\x07" not in fl.text
            assert len(fl.text) <= width
        # Current session marker is visible.
        assert any("●" in fl.text for fl in lines)

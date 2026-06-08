"""Unit tests for Ctrl+V clipboard-image paste and drag-drop references."""

from __future__ import annotations

import io
import stat
from pathlib import Path

from pipy_harness.native.clipboard import ImageClipboardResult
from pipy_harness.native.tui import ToolLoopTerminalUi

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _ui(tmp_path: Path) -> ToolLoopTerminalUi:
    return ToolLoopTerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
    )


class TestClipboardImagePaste:
    def test_paste_writes_owner_only_temp_and_inserts_reference(
        self, tmp_path: Path
    ) -> None:
        clip_dir = tmp_path / "clip"
        ui = _ui(tmp_path)
        ui.clipboard_temp_dir = clip_dir
        ui.clipboard_image_read = lambda: ImageClipboardResult(
            found=True, data=_PNG, media_type="image/png", detail="ok"
        )
        ui._paste_clipboard_image()
        assert "@image:" in ui.input_text
        written = list(clip_dir.glob("pipy-clipboard-*.png"))
        assert written, "no temp image written"
        mode = stat.S_IMODE(written[0].stat().st_mode)
        assert mode == 0o600, f"temp image not owner-only: {oct(mode)}"
        assert written[0].read_bytes() == _PNG

    def test_no_image_reports_notice_and_inserts_nothing(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        ui.clipboard_temp_dir = tmp_path / "clip"
        ui.clipboard_image_read = lambda: ImageClipboardResult(
            found=False, data=b"", media_type="", detail="no image on the clipboard"
        )
        ui._paste_clipboard_image()
        assert ui.input_text == ""
        notices = [lines for kind, lines in ui._history_blocks if kind == "notice"]
        assert any("no image" in " ".join(lines).lower() for lines in notices)

    def test_paste_unavailable_without_reader(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path)
        ui._paste_clipboard_image()
        assert ui.input_text == ""


class TestDragReference:
    def test_dropped_image_path_becomes_image_reference(self, tmp_path: Path) -> None:
        image = tmp_path / "shot.png"
        image.write_bytes(_PNG)
        ref = _ui(tmp_path)._as_drag_reference(str(image))
        assert ref == f"@image:{image} "

    def test_dropped_other_file_becomes_path_reference(self, tmp_path: Path) -> None:
        doc = tmp_path / "notes.txt"
        doc.write_text("hi\n")
        ref = _ui(tmp_path)._as_drag_reference(str(doc))
        assert ref == f"@{doc} "

    def test_quoted_dropped_path_with_space_is_requoted(self, tmp_path: Path) -> None:
        # A dropped path containing a space must be re-quoted so the reference
        # resolves as one token (it previously reinserted it unquoted, breaking
        # at the space).
        image = tmp_path / "a b.png"
        image.write_bytes(_PNG)
        ref = _ui(tmp_path)._as_drag_reference(f'"{image}"')
        assert ref == f'@image:"{image}" '

    def test_plain_text_paste_is_not_a_reference(self, tmp_path: Path) -> None:
        assert _ui(tmp_path)._as_drag_reference("just some prose") is None

    def test_multiline_paste_is_not_a_reference(self, tmp_path: Path) -> None:
        assert _ui(tmp_path)._as_drag_reference("line1\nline2") is None

    def test_relative_drop_resolves_against_workspace_cwd(self, tmp_path: Path) -> None:
        # A relative dropped path is resolved against the session workspace
        # (ui.cwd), not the process cwd.
        (tmp_path / "dropped.png").write_bytes(_PNG)
        ui = _ui(tmp_path)
        ref = ui._as_drag_reference("dropped.png")
        assert ref == "@image:dropped.png "

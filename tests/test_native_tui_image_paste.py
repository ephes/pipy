"""Focused tests for clipboard-image paste and drag-reference ownership."""

from __future__ import annotations

import io
import stat
from pathlib import Path

import pytest

from pipy_harness.native.clipboard import ImageClipboardResult
from pipy_harness.native.tui import TerminalUi
from pipy_harness.native.ui.clipboard_images import ClipboardConfig, ClipboardImages

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _config(path: Path, *, found: bool = True) -> ClipboardConfig:
    return ClipboardConfig(
        temp_dir=path,
        image_read=lambda: ImageClipboardResult(
            found=found,
            data=_PNG if found else b"",
            media_type="image/png" if found else "",
            detail="ok" if found else "no image on the clipboard",
        ),
    )


def _ui(tmp_path: Path, config: ClipboardConfig | None = None) -> TerminalUi:
    return TerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
        clipboard_config=config,
    )


def _record_failure_callbacks(
    owner: ClipboardImages,
) -> list[tuple[str, str | None]]:
    events: list[tuple[str, str | None]] = []
    owner._add_notice = lambda message: events.append(  # noqa: SLF001
        ("notice", message)
    )
    owner._repaint = lambda: events.append(("repaint", None))  # noqa: SLF001
    return events


class TestClipboardImagePaste:
    def test_wiring_preserves_config_identity_and_shared_owners(
        self, tmp_path: Path
    ) -> None:
        config = _config(tmp_path / "clip")
        ui = _ui(tmp_path, config)
        owner = ui.clipboard_images

        assert isinstance(owner, ClipboardImages)
        assert owner.config is config
        assert owner._editor is ui.input_editor.editor_state  # noqa: SLF001
        assert owner._paint_lock is ui._screen.paint_lock  # noqa: SLF001

    def test_paste_writes_owner_only_temp_and_inserts_reference(
        self, tmp_path: Path
    ) -> None:
        clip_dir = tmp_path / "clip"
        ui = _ui(tmp_path, _config(clip_dir))
        ui.clipboard_images.paste_clipboard_image()
        assert "@image:" in ui.input_editor.text
        written = list(clip_dir.glob("pipy-clipboard-*.png"))
        assert written, "no temp image written"
        assert stat.S_IMODE(clip_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(written[0].stat().st_mode) == 0o600
        assert written[0].read_bytes() == _PNG

    def test_no_image_publishes_notice_then_repaints_once(self, tmp_path: Path) -> None:
        ui = _ui(tmp_path, _config(tmp_path / "clip", found=False))
        events = _record_failure_callbacks(ui.clipboard_images)

        ui.clipboard_images.paste_clipboard_image()

        assert ui.input_editor.text == ""
        assert events == [
            ("notice", "pipy: no image on the clipboard."),
            ("repaint", None),
        ]

    def test_paste_unavailable_publishes_notice_then_repaints_once(
        self, tmp_path: Path
    ) -> None:
        ui = _ui(tmp_path)
        events = _record_failure_callbacks(ui.clipboard_images)

        ui.clipboard_images.paste_clipboard_image()

        assert ui.input_editor.text == ""
        assert events == [
            ("notice", "pipy: clipboard image paste is not available here."),
            ("repaint", None),
        ]

    def test_save_failure_publishes_notice_then_repaints_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ui = _ui(tmp_path, _config(tmp_path / "clip"))
        events = _record_failure_callbacks(ui.clipboard_images)

        def fail_open(*_args: object, **_kwargs: object) -> int:
            raise OSError("write refused")

        monkeypatch.setattr(
            "pipy_harness.native.ui.clipboard_images.os.open", fail_open
        )

        ui.clipboard_images.paste_clipboard_image()

        assert ui.input_editor.text == ""
        assert events == [
            ("notice", "pipy: could not save the pasted clipboard image."),
            ("repaint", None),
        ]


class TestDragReference:
    def test_dropped_image_path_becomes_image_reference(self, tmp_path: Path) -> None:
        image = tmp_path / "shot.png"
        image.write_bytes(_PNG)
        ref = _ui(tmp_path).clipboard_images.as_drag_reference(str(image))
        assert ref == f"@image:{image} "

    def test_dropped_other_file_becomes_path_reference(self, tmp_path: Path) -> None:
        doc = tmp_path / "notes.txt"
        doc.write_text("hi\n")
        ref = _ui(tmp_path).clipboard_images.as_drag_reference(str(doc))
        assert ref == f"@{doc} "

    def test_quoted_dropped_path_with_space_is_requoted(self, tmp_path: Path) -> None:
        image = tmp_path / "a b.png"
        image.write_bytes(_PNG)
        ref = _ui(tmp_path).clipboard_images.as_drag_reference(f'"{image}"')
        assert ref == f'@image:"{image}" '

    def test_plain_or_multiline_text_is_not_a_reference(self, tmp_path: Path) -> None:
        owner = _ui(tmp_path).clipboard_images
        assert owner.as_drag_reference("just some prose") is None
        assert owner.as_drag_reference("line1\nline2") is None

    def test_relative_drop_resolves_against_workspace_cwd(self, tmp_path: Path) -> None:
        (tmp_path / "dropped.png").write_bytes(_PNG)
        ref = _ui(tmp_path).clipboard_images.as_drag_reference("dropped.png")
        assert ref == "@image:dropped.png "

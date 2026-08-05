"""Clipboard-image paste and drag-reference effects for the terminal editor.

``ClipboardConfig`` is the frozen wiring value shared with the session's image
reference-root policy. Clipboard bytes stay local in an owner-only file; only
its ``@image:`` reference enters the in-memory editor.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.clipboard import ImageClipboardResult
from pipy_harness.native.editor_state import EditorState
from pipy_harness.native.ui.paint_lock import PaintLock

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


@dataclass(frozen=True, slots=True)
class ClipboardConfig:
    """One clipboard reader and the session-shared image reference root."""

    temp_dir: Path
    image_read: Callable[[], ImageClipboardResult]


def create_clipboard_config(
    image_read: Callable[[], ImageClipboardResult],
) -> ClipboardConfig:
    """Create the owner-only image root and its immutable wiring record."""

    temp_dir = Path(tempfile.mkdtemp(prefix="pipy-clipboard-"))
    try:
        temp_dir.chmod(0o700)
    except OSError:
        pass
    return ClipboardConfig(temp_dir=temp_dir, image_read=image_read)


class ClipboardImages:
    """Own clipboard file numbering, drag resolution, and editor insertion."""

    def __init__(
        self,
        editor: EditorState,
        paint_lock: PaintLock,
        repaint: Callable[[], None],
        *,
        cwd: Path,
        config: ClipboardConfig | None,
        command_names: Callable[[], tuple[str, ...]],
        refresh_autocomplete: Callable[[], None],
        add_notice: Callable[[str], None],
        custom_editor_text: Callable[[], str | None],
        set_custom_editor_text: Callable[[str], None],
    ) -> None:
        self._editor = editor
        self._paint_lock = paint_lock
        self._repaint = repaint
        self._cwd = cwd
        self._config = config
        self._command_names = command_names
        self._refresh_autocomplete = refresh_autocomplete
        self._add_notice = add_notice
        self._custom_editor_text = custom_editor_text
        self._set_custom_editor_text = set_custom_editor_text
        self._image_count = 0

    @property
    def config(self) -> ClipboardConfig | None:
        """Return the exact frozen record injected by session wiring."""

        return self._config

    def insert_paste(self, text: str) -> None:
        """Insert a literal paste, translating one dropped file to a reference."""

        if not text:
            self._repaint()
            return
        reference = self.as_drag_reference(text)
        self._insert_editor_text(reference if reference is not None else text)

    def as_drag_reference(self, text: str) -> str | None:
        """Return an ``@image:``/``@path`` reference for one existing path."""

        candidate = text.strip()
        if not candidate or "\n" in candidate:
            return None
        if (
            len(candidate) >= 2
            and candidate[0] == candidate[-1]
            and candidate[0] in "\"'"
        ):
            candidate = candidate[1:-1]
        if not candidate or "\x00" in candidate:
            return None
        try:
            resolved = Path(candidate).expanduser()
            if not resolved.is_absolute():
                resolved = self._cwd / resolved
            if not resolved.is_file():
                return None
        except OSError:
            return None
        rendered = f'"{candidate}"' if " " in candidate else candidate
        prefix = "@image:" if Path(candidate).suffix.lower() in _IMAGE_SUFFIXES else "@"
        return f"{prefix}{rendered} "

    def paste_clipboard_image(self) -> None:
        """Persist a clipboard image and insert its private-file reference."""

        config = self._config
        if config is None:
            self._add_notice("pipy: clipboard image paste is not available here.")
            self._repaint()
            return
        result = config.image_read()
        if not result.found:
            self._add_notice(f"pipy: {result.detail}.")
            self._repaint()
            return
        extension = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
        }.get(result.media_type, "png")
        try:
            config.temp_dir.mkdir(parents=True, exist_ok=True)
            try:
                config.temp_dir.chmod(0o700)
            except OSError:
                pass
            with self._paint_lock:
                self._image_count += 1
                count = self._image_count
            path = config.temp_dir / f"pipy-clipboard-{count}.{extension}"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(result.data)
        except OSError:
            self._add_notice("pipy: could not save the pasted clipboard image.")
            self._repaint()
            return
        reference = f'"{path}"' if " " in str(path) else str(path)
        insertion = f"@image:{reference} "
        custom_text = self._custom_editor_text()
        if custom_text is not None:
            self._set_custom_editor_text(f"{custom_text}{insertion}")
            self._repaint()
            return
        self._insert_editor_text(insertion)

    def _insert_editor_text(self, text: str) -> None:
        command_names = self._command_names()
        with self._paint_lock:
            self._editor.insert(text, command_names)
        self._refresh_autocomplete()
        self._repaint()

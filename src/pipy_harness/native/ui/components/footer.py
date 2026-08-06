"""Extension footer region and live git-branch poller effects."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from pipy_harness.native.extension_chrome_state import (
    ChromeRegion,
    ExtensionChromeState,
)
from pipy_harness.native.extension_types import FooterData
from pipy_harness.native.frame_renderer import FrameLine
from pipy_harness.native.ui.extension_chrome import FOOTER_MAX_LINES, clip_custom
from pipy_harness.native.ui.paint_lock import PaintLock


class FooterComponent:
    """Own footer construction, branch callbacks, polling, and frame rows."""

    def __init__(
        self,
        record: ExtensionChromeState,
        paint_lock: PaintLock,
        repaint: Callable[[], None],
        *,
        cwd: Path,
        available_provider_count: Callable[[], int],
        build_region: Callable[[object, object | None, int], ChromeRegion | None],
        dispose_region: Callable[[ChromeRegion | None], None],
        render_region: Callable[..., tuple[str, ...] | None],
        builtin_lines: tuple[str, str],
    ) -> None:
        self._record = record
        self._paint_lock = paint_lock
        self._repaint = repaint
        self._cwd = cwd
        self._available_provider_count = available_provider_count
        self._build_region = build_region
        self._dispose_region = dispose_region
        self._render_region = render_region
        self._builtin_lines = builtin_lines

    def builtin_lines(self) -> tuple[str, str]:
        """Return the complete built-in footer pair under the paint lock."""

        with self._paint_lock:
            return self._builtin_lines

    def set_builtin_text(self, text: str) -> None:
        """Replace both built-in rows atomically, then repaint after unlocking."""

        lines = text.splitlines()
        if len(lines) >= 2:
            replacement = (lines[0], lines[1])
        elif lines:
            replacement = (lines[0], "")
        else:
            replacement = ("", "")
        with self._paint_lock:
            self._builtin_lines = replacement
        self._repaint()

    def _detect_branch(self) -> str | None:
        candidate: Path | None = self._cwd
        while candidate is not None and candidate != candidate.parent:
            head = candidate / ".git" / "HEAD"
            try:
                text = head.read_text(encoding="utf-8")
            except OSError:
                candidate = candidate.parent
                continue
            text = text.strip()
            if text.startswith("ref: refs/heads/"):
                return text.split("refs/heads/", 1)[1]
            if text:
                return "detached"
            return None
        return None

    def register_branch_change_callback(
        self, callback: Callable[[], object]
    ) -> Callable[[], None]:
        with self._paint_lock:
            generation, callback_id = self._record.register_footer_branch_callback(
                callback
            )

        disposed = False

        def dispose() -> None:
            nonlocal disposed
            if disposed:
                return
            disposed = True
            with self._paint_lock:
                self._record.remove_footer_branch_callback(generation, callback_id)

        return dispose

    def _footer_data(self, branch: str | None = None) -> FooterData:
        if branch is None:
            branch = self._detect_branch()
        return FooterData(
            git_branch=branch,
            extension_statuses=dict(self._record.statuses),
            available_provider_count=int(self._available_provider_count() or 0),
            branch_change_registrar=self.register_branch_change_callback,
        )

    def set_footer(
        self, factory: object | None, footer_data: object | None = None
    ) -> None:
        with self._paint_lock:
            self._dispose_region(self._record.footer)
            self._record.clear_footer_branch_callbacks()
            self._record.footer_factory = factory
            if factory is None:
                self._record.footer = None
                self._record.footer_branch = None
            else:
                data = footer_data if footer_data is not None else self._footer_data()
                if isinstance(data, FooterData):
                    self._record.footer_branch = self._detect_branch()
                self._record.footer = self._build_region(
                    factory, data, FOOTER_MAX_LINES
                )
        self._repaint()

    def poll_branch(self, *, force: bool = False) -> None:
        """Rebuild the live footer when its git branch changed."""

        with self._paint_lock:
            factory = self._record.footer_factory
            if factory is None:
                return
            now = time.monotonic()
            if (
                not force
                and now - self._record.footer_branch_last_check
                < self._record.footer_branch_check_interval
            ):
                return
            branch = self._detect_branch()
            if not force and branch == self._record.footer_branch:
                self._record.footer_branch_last_check = now
                return
            self._record.footer_branch_last_check = now
            self._record.begin_footer_rebuild(branch)
            self._dispose_region(self._record.footer)
            try:
                self._record.footer = self._build_region(
                    factory, self._footer_data(branch), FOOTER_MAX_LINES
                )
                callbacks = self._record.finish_footer_rebuild()
            finally:
                self._record.abort_footer_rebuild()
        for callback in callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001 - one extension callback stays bounded
                continue
        self._repaint()

    def lines(self, width: int) -> list[FrameLine] | None:
        """Return custom footer rows, or ``None`` for the built-in footer."""

        with self._paint_lock:
            region = self._record.footer
            if region is None:
                return None
            lines = self._render_region(region, width=width, max_lines=FOOTER_MAX_LINES)
            if lines is None:
                self._dispose_region(region)
                self._record.footer = None
                return None
        return [FrameLine(clip_custom(line, width), "chrome_custom") for line in lines]

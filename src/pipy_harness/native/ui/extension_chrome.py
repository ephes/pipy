"""Extension chrome regions, status, title, and working-indicator effects.

The component owns effects over the dependency-neutral ``ExtensionChromeState``
record.  Every record transition is serialized by the shared ``PaintLock``;
repaints run after the transition, while each terminal-title effect stays in the
same critical section as its title-record transition.  Region factories and
component lifecycle calls retain the existing reentrant rendering contract:
they run while the shared reentrant lock is held so a painter cannot observe a
half-replaced region.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from typing import Any, cast

from pipy_harness.native.extension_chrome_state import (
    ChromeRegion,
    ExtensionChromeState,
)
from pipy_harness.native.frame_renderer import (
    FrameLine,
    clip_custom_text,
    clip_text,
    sanitize_custom_text,
    visible_len,
)
from pipy_harness.native.session_tree_commands import sanitize_label_text
from pipy_harness.native.terminal_driver import _TITLE_MAX_CHARS
from pipy_harness.native.tool_renderers import render_chrome_component
from pipy_harness.native.ui.paint_lock import PaintLock
from pipy_harness.native.ui.screen import ScreenRenderInputs

WIDGET_MAX_LINES = 10
WIDGET_MAX_COUNT = 16
HEADER_MAX_LINES = 8
FOOTER_MAX_LINES = 4
INDICATOR_MAX_FRAMES = 32


def safe_status_key(key: str) -> str | None:
    text = sanitize_label_text(str(key)).strip()
    if not text:
        return None
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in text)
    cleaned = cleaned.strip("-_.")
    return cleaned[:64] or None


def clip_custom(text: str, width: int) -> str:
    cleaned = sanitize_custom_text(text)
    if visible_len(cleaned) <= width:
        return cleaned
    return clip_custom_text(cleaned, width)


class ExtensionChromeComponent:
    """Effect owner for extension chrome except footer polling and listeners."""

    def __init__(
        self,
        record: ExtensionChromeState,
        paint_lock: PaintLock,
        repaint: Callable[[], None],
        *,
        tui_handle: object,
        render_inputs: ScreenRenderInputs,
        push_title: Callable[[], None],
        write_title: Callable[[str], None],
        restore_title: Callable[[], None],
        clear_working_text: Callable[[], None],
    ) -> None:
        self._record = record
        self._paint_lock = paint_lock
        self._repaint = repaint
        self._render_inputs = render_inputs
        self._push_title = push_title
        self._write_title = write_title
        self._restore_title = restore_title
        self._clear_working_text = clear_working_text
        self._tui_handle = tui_handle

    @staticmethod
    def _call_factory(
        source: object, args: tuple[object, ...], legacy_args: tuple[object, ...]
    ) -> object:
        try:
            signature = inspect.signature(cast(Callable[..., object], source))
        except (TypeError, ValueError):
            return cast(Callable[..., object], source)(*args)
        try:
            signature.bind(*args)
        except TypeError:
            signature.bind(*legacy_args)
            return cast(Callable[..., object], source)(*legacy_args)
        return cast(Callable[..., object], source)(*args)

    def build_region(
        self, source: object, footer_data: object | None, max_lines: int
    ) -> ChromeRegion | None:
        """Build a static or reactive region at the terminal's current width."""

        width = self._render_inputs.width()
        component: object | None = None
        is_factory = False
        render_source: object = source
        if callable(source) and not isinstance(source, (str, bytes, bytearray)):
            theme = self._render_inputs.theme()
            try:
                if footer_data is not None:
                    component = self._call_factory(
                        source,
                        (self._tui_handle, theme, footer_data),
                        (theme, footer_data),
                    )
                else:
                    component = self._call_factory(
                        source, (self._tui_handle, theme), (theme,)
                    )
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - a bad factory falls back
                return None
            is_factory = True
            render_source = lambda: component  # noqa: E731
        elif not isinstance(source, (str, bytes, bytearray)) and callable(
            getattr(source, "render", None)
        ):
            component = source
            is_factory = True
            render_source = lambda: component  # noqa: E731
        lines = render_chrome_component(render_source, width=width, max_lines=max_lines)
        if lines is None:
            return None
        return ChromeRegion(
            source=source,
            component=component,
            snapshot=tuple(lines),
            width=width,
            is_factory=is_factory,
        )

    @staticmethod
    def dispose_region(region: ChromeRegion | None) -> None:
        if region is None or region.component is None:
            return
        dispose = getattr(region.component, "dispose", None)
        if callable(dispose):
            try:
                dispose()
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - chrome disposal is fail soft
                pass

    @staticmethod
    def render_region(
        region: ChromeRegion, *, width: int, max_lines: int
    ) -> tuple[str, ...] | None:
        if not region.is_factory or region.component is None:
            return region.snapshot
        if region.width != width:
            invalidate = getattr(region.component, "invalidate", None)
            if callable(invalidate):
                try:
                    invalidate()
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException:  # noqa: BLE001 - invalidation is advisory
                    pass
        lines = render_chrome_component(
            lambda: region.component, width=width, max_lines=max_lines
        )
        if lines is None:
            return None
        region.snapshot = tuple(lines)
        region.width = width
        return region.snapshot

    def set_status(self, key: str, text: str | None) -> None:
        safe_key = safe_status_key(key)
        if safe_key is None:
            return
        with self._paint_lock:
            self._record.set_status(
                safe_key, None if text is None else sanitize_label_text(str(text))
            )
        self._repaint()

    def set_widget(
        self, key: str, content: object, placement: str = "above_editor"
    ) -> None:
        safe_key = safe_status_key(key)
        if safe_key is None:
            return
        with self._paint_lock:
            target, other = self._record.widget_maps(placement)
            if (
                content is not None
                and safe_key not in target
                and len(target) >= WIDGET_MAX_COUNT
            ):
                return
            self.dispose_region(target.get(safe_key))
            self.dispose_region(other.pop(safe_key, None))
            if content is None:
                target.pop(safe_key, None)
            else:
                region = self.build_region(content, None, WIDGET_MAX_LINES)
                if region is None:
                    target.pop(safe_key, None)
                else:
                    target[safe_key] = region
        self._repaint()

    def set_header(self, factory: object | None) -> None:
        with self._paint_lock:
            self.dispose_region(self._record.header)
            self._record.header = (
                None
                if factory is None
                else self.build_region(factory, None, HEADER_MAX_LINES)
            )
        self._repaint()

    def restore_title(self) -> None:
        """Serialize restoration with every title-record/effect transition."""

        with self._paint_lock:
            self._restore_title()

    def set_title(self, title: str | None) -> None:
        with self._paint_lock:
            if title is None:
                self._record.title = None
                self._restore_title()
                return
            cleaned = sanitize_label_text(str(title))[:_TITLE_MAX_CHARS]
            self._push_title()
            self._record.title = cleaned
            self._write_title(cleaned)

    def set_working_indicator(self, frames: object, interval_ms: object) -> None:
        cleaned: tuple[str, ...] | None = None
        replace_frames = frames is None
        if frames is not None:
            try:
                cleaned = tuple(
                    sanitize_label_text(str(frame))
                    for frame in list(cast(Iterable[object], frames))[
                        :INDICATOR_MAX_FRAMES
                    ]
                )
                replace_frames = True
            except (TypeError, ValueError):
                pass
        try:
            interval = (
                None
                if interval_ms is None
                else max(10.0, float(cast(Any, interval_ms)))
            )
        except (TypeError, ValueError):
            interval = None
        with self._paint_lock:
            self._record.set_indicator(
                frames=cleaned,
                interval_ms=interval,
                replace_frames=replace_frames,
            )
        self._repaint()

    def set_working_message(self, message: str | None = None) -> None:
        with self._paint_lock:
            self._record.set_working_message(
                None if message is None else sanitize_label_text(str(message))
            )
        self._repaint()

    def set_working_visible(self, visible: bool) -> None:
        with self._paint_lock:
            self._record.set_working_visible(bool(visible))
            if not self._record.working_visible:
                self._clear_working_text()
        self._repaint()

    def header_lines(self, width: int) -> list[FrameLine]:
        with self._paint_lock:
            region = self._record.header
            if region is None:
                return []
            lines = self.render_region(region, width=width, max_lines=HEADER_MAX_LINES)
            if lines is None:
                self.dispose_region(region)
                self._record.header = None
                return []
        return [FrameLine(clip_custom(line, width), "chrome_custom") for line in lines]

    def widget_lines(self, placement: str, width: int) -> list[FrameLine]:
        with self._paint_lock:
            regions = (
                self._record.widgets_below
                if placement == "below_editor"
                else self._record.widgets_above
            )
            if not regions:
                return []
            out: list[FrameLine] = []
            failed: list[str] = []
            for key, region in regions.items():
                lines = self.render_region(
                    region, width=width, max_lines=WIDGET_MAX_LINES
                )
                if lines is None:
                    failed.append(key)
                    continue
                out.extend(
                    FrameLine(clip_custom(line, width), "chrome_custom")
                    for line in lines
                )
            for key in failed:
                self.dispose_region(regions.pop(key, None))
            return out

    def status_lines(self, width: int) -> list[FrameLine]:
        with self._paint_lock:
            items = tuple(sorted(self._record.statuses.items()))
        rows = [
            FrameLine(
                clip_text(f"  {key}: {sanitize_label_text(value)}", width), "notice"
            )
            for key, value in items[:3]
        ]
        hidden = len(items) - len(rows)
        if hidden > 0:
            rows.append(
                FrameLine(
                    clip_text(f"  ... +{hidden} extension status rows", width),
                    "slash_menu_scroll",
                )
            )
        return rows

"""The Pi-shaped handle an extension receives for its custom overlay.

Hands an extension callback the four verbs Pi exposes -- hide, focus/unfocus,
hidden/focused queries, and request-render -- in both Pi's camelCase spelling and
a snake_case alias.

It takes the shared `OverlayState` record and a repaint callable rather than the
terminal-UI shell. The overlay flags already live on that record; the shell only
projected them through properties, so depending on the record directly removes
the indirection instead of formalizing it, and leaves repainting as the single
thing the shell still owns here.
"""

from __future__ import annotations

from collections.abc import Callable

from pipy_harness.native.overlay_state import OverlayState


class CustomOverlayHandle:
    """Minimal Pi-shaped custom overlay handle exposed to extension callbacks."""

    def __init__(self, overlays: OverlayState, paint: Callable[[], None]) -> None:
        self._overlays = overlays
        self._repaint = paint

    def _paint(self) -> None:
        try:
            self._repaint()
        except (OSError, ValueError):
            pass

    def hide(self) -> None:
        if not self._overlays.custom_done:
            self._overlays.custom_done = True
            self._overlays.custom_result = None
            self._paint()

    def setHidden(self, hidden: bool) -> None:  # noqa: N802 - Pi API
        value = bool(hidden)
        if self._overlays.custom_hidden == value:
            return
        self._overlays.custom_hidden = value
        self._overlays.custom_focused = not value
        self._paint()

    def set_hidden(self, hidden: bool) -> None:
        self.setHidden(hidden)

    def isHidden(self) -> bool:  # noqa: N802 - Pi API
        return bool(self._overlays.custom_hidden)

    def is_hidden(self) -> bool:
        return self.isHidden()

    def focus(self) -> None:
        changed = self._overlays.custom_hidden or not self._overlays.custom_focused
        self._overlays.custom_hidden = False
        self._overlays.custom_focused = True
        if changed:
            self._paint()

    def unfocus(self, options: object = None) -> None:
        # ``options.target`` is accepted for Pi-shaped duck typing. Pipy's
        # bounded custom-component path has no overlay stack/focus graph yet,
        # so there is no alternate target to focus in this slice.
        del options
        if not self._overlays.custom_focused:
            return
        self._overlays.custom_focused = False
        self._paint()

    def isFocused(self) -> bool:  # noqa: N802 - Pi API
        return bool(self._overlays.custom_focused and not self._overlays.custom_hidden)

    def is_focused(self) -> bool:
        return self.isFocused()

    def update(self) -> None:
        self.requestRender()

    def requestRender(self) -> None:
        self._paint()

    def request_render(self) -> None:
        self.requestRender()

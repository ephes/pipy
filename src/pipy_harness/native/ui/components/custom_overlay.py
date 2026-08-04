"""The custom extension overlay: runner transaction, handle, and rendering.

Same ownership contract as the sibling overlay components: the overlay state
already lives on the shared :class:`OverlayState` record (`custom_component`,
`custom_render_width`, the hidden/focused/done flags), so everything here takes
that record and a repaint callable instead of the terminal-UI shell. The shell
keeps only the raw-mode key loop and forwards each decoded key to
:meth:`CustomComponentRunner.handle_key`; :meth:`CustomComponentRunner.dispose`
is the only way a result leaves.

:class:`CustomComponentRunner` is the setup/dispose transaction behind
``run_custom_component``. Construction of the extension's component
(:meth:`create`) is deliberately a separate phase from activating the overlay
(:meth:`begin`): a factory that raises must propagate *without* the teardown in
:meth:`dispose` running, exactly as when the whole transaction lived on the
shell.

:class:`CustomOverlayHandle` is the Pi-shaped handle an extension callback
receives -- hide, focus/unfocus, hidden/focused queries, and request-render --
in both Pi's camelCase spelling and a snake_case alias.

Rendering is a pure function of the overlay record, so the shell's frame
dispatch calls :func:`custom_overlay_region_lines` without holding a runner
instance.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, cast

from pipy_harness.native.frame_renderer import FrameLine, clip_custom_text
from pipy_harness.native.overlay_state import OverlayState

if TYPE_CHECKING:
    from pipy_harness.native.extension_types import (
        CustomComponent,
        CustomComponentFactory,
        CustomComponentOptions,
    )


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


class CustomComponentRunner:
    """Setup/dispose transaction behind ``run_custom_component``.

    The shell drives it in four phases: :meth:`create` builds the extension's
    component (outside the shell's ``try``/``finally``, so a raising factory
    propagates without teardown), :meth:`begin` activates the overlay and
    notifies the Pi-shaped handle callback, :meth:`handle_key` consumes one
    decoded key from the shell's raw-mode loop, and :meth:`dispose` ends the
    overlay, disposes the component, relinquishes the screen, and returns the
    result passed to ``done`` (``None`` if cancelled).
    """

    def __init__(self, overlays: OverlayState, repaint: Callable[[], None]) -> None:
        self._overlays = overlays
        self._repaint = repaint
        self._component: CustomComponent | None = None
        self._options: CustomComponentOptions | None = None
        self._previous_width: int | None = None
        self._render_width: int | None = None
        self._started = False
        self._pending_done = False
        self._pending_result: object = None

    def create(
        self,
        factory: CustomComponentFactory,
        options: CustomComponentOptions | None,
    ) -> None:
        """Build the component; a ``done`` before :meth:`begin` stays pending."""

        self._previous_width = self._overlays.custom_render_width
        self._render_width = _component_render_width(options)
        self._options = options
        self._component = factory(self._finish)

    def _finish(self, result: object = None) -> None:
        if self._started:
            self._overlays.finish_custom(result)
        elif not self._pending_done:
            self._pending_done = True
            self._pending_result = result

    def begin(self) -> None:
        """Activate the overlay, flush a pending ``done``, notify the handle."""

        component = self._component
        if component is None:
            # ``create`` has not run; finish empty so the shell's loop stops.
            self._finish(None)
            return
        self._overlays.begin_custom(component, render_width=self._render_width)
        self._started = True
        if self._pending_done:
            self._overlays.finish_custom(self._pending_result)
        _notify_custom_handle(
            self._options, CustomOverlayHandle(self._overlays, self._repaint)
        )
        self._repaint()

    @property
    def finished(self) -> bool:
        """True once the component (or a cancellation) called ``done``."""

        return bool(self._overlays.custom_done)

    def handle_key(self, key: str | None) -> bool:
        """Route one decoded key to the component; ``True`` stops the loop."""

        component = self._component
        if key is None or component is None:
            # Stream EOF / read error: cancel deterministically.
            self._finish(None)
            return True
        if key == "paste":
            # A bracketed-paste marker carries no decoded text here; ignore it
            # rather than forwarding a sentinel to the component.
            return False
        try:
            if self._overlays.custom_hidden or not self._overlays.custom_focused:
                self._repaint()
                return False
            component.handle_input(key)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - a bad component cancels
            self._finish(None)
            return True
        if not self._overlays.custom_done:
            self._repaint()
        return False

    def dispose(self) -> object:
        """End the overlay, dispose the component, relinquish the screen."""

        result = self._overlays.end_custom(previous_width=self._previous_width)
        dispose = getattr(self._component, "dispose", None)
        if callable(dispose):
            try:
                dispose()
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - must not strand the screen relinquish
                pass
        # Relinquish the screen immediately: repaint the normal frame so the
        # overlay does not linger until some unrelated later paint. Guarded so
        # a repaint failure never masks the in-flight result/exception.
        try:
            self._repaint()
        except (OSError, ValueError):
            pass
        return result


def custom_overlay_region_lines(
    overlays: OverlayState, *, width: int, height: int
) -> list[FrameLine]:
    """Compose the custom extension overlay from the component's lines.

    The component owns its own layout (it is trusted local code, matching the
    extension trust boundary), but the driver still sanitizes and clips
    rendered lines before they reach the terminal frame.
    """

    if overlays.custom_hidden:
        return []
    component = cast("CustomComponent | None", overlays.custom_component)
    if component is None:
        return []
    try:
        render_width = overlays.custom_render_width or width
        raw = component.render(render_width)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 - never let a bad render crash paint
        raw = ["(custom component render error)"]
    lines = [clip_custom_text(str(line), width) for line in (raw or [])][
        : max(1, height)
    ]
    return [FrameLine(line, "normal") for line in lines]


def _component_render_width(options: object) -> int | None:
    overlay_options = _custom_option(options, "overlayOptions", "overlay_options")
    if callable(overlay_options):
        try:
            overlay_options = overlay_options()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - bad options degrade to defaults
            overlay_options = None
    width = _custom_option(overlay_options, "width")
    if isinstance(width, bool):
        return None
    if isinstance(width, int) and width > 0:
        return max(1, min(width, 500))
    if isinstance(width, float) and width > 0:
        return max(1, min(int(width), 500))
    if isinstance(width, str):
        try:
            parsed = int(width)
        except ValueError:
            return None
        return max(1, min(parsed, 500)) if parsed > 0 else None
    return None


def _notify_custom_handle(options: object, handle: CustomOverlayHandle) -> None:
    callback = _custom_option(options, "onHandle", "on_handle")
    if not callable(callback):
        return
    try:
        callback(handle)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 - an overlay callback must not break the session
        pass


def _custom_option(source: object, *names: str) -> object:
    if source is None:
        return None
    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source[name]
        return None
    for name in names:
        if hasattr(source, name):
            return getattr(source, name)
    return None

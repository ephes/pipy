"""Headless extension UI bridge.

The sole owner of the deterministic, headless extension UI implementation
(`_CollectingUi`), the `_safe_ui_key` sanitizer, and the pre-rendered
`lines_component` / `_LinesComponent` / `coerce_tool_render_lines` chrome
helpers. `_CollectingUi` is a mode-aware `ExtensionUi` (structural, not a
subclass) that records notices, statuses, widgets, header/footer/title,
working-indicator state, editor text, autocomplete providers, and theme reads,
delegating every mutating operation to an optional live `ExtensionUiDriver`
only when a UI is available and never blocking otherwise.

This module imports only the `extension_types` contracts and the `native.themes`
registry helpers, so it never reaches the product session (`tool_loop_session`)
or the terminal UI (`tui`). `extension_runtime` imports `_CollectingUi` from
here for `make_extension_context` / `_ActivationApi`; the live
`_LiveExtensionUiDriver` that binds `ToolLoopTerminalUi` stays in
`tool_loop_session` (Phase-4 terminal surface).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

from pipy_harness.native.extension_types import (
    CustomComponentDriver,
    CustomComponentFactory,
    CustomComponentOptions,
    ExtensionUiDriver,
    ToolRenderComponent,
    WidgetPlacement,
)
from pipy_harness.native.themes import (
    ChromePalette,
    NativeThemeStore,
    available_theme_names,
    is_known_theme,
    resolve_active_theme_name,
    resolve_palette,
)

# Bound a tool/entry render's line output. Also re-imported by
# `extension_runtime` for its message/entry render truncation.
_CUSTOM_RENDER_MAX_CHARS: int = 16 * 1024


def coerce_tool_render_lines(value: object) -> tuple[str, ...] | None:
    """Normalize a render() return (or lines_component input) to lines.

    Special-cases `str` (split on newlines) BEFORE the generic Sequence path,
    because `str` is itself a `Sequence[str]` and would otherwise render
    character-per-line. Returns None for unusable types (signals fallback)."""

    if isinstance(value, str):
        text = value
    elif isinstance(value, (bytes, bytearray)):
        return None
    elif isinstance(value, Sequence):
        try:
            text = "\n".join(str(item) for item in value)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - a bad sequence falls back
            return None
    else:
        return None
    if len(text) > _CUSTOM_RENDER_MAX_CHARS:
        text = text[: _CUSTOM_RENDER_MAX_CHARS - 64] + "\n[pipy: tool render truncated]"
    return tuple(text.splitlines() or [""])


@dataclass(frozen=True, slots=True)
class _LinesComponent:
    _lines: tuple[str, ...]

    def render(self, width: int) -> list[str]:
        return list(self._lines)


def lines_component(lines: str | Sequence[str]) -> ToolRenderComponent:
    """Convenience: wrap pre-rendered lines as a ToolRenderComponent."""

    coerced = coerce_tool_render_lines(lines)
    if coerced is None:
        coerced = (str(lines),)
    return _LinesComponent(coerced)


def _safe_ui_key(key: object) -> str | None:
    text = str(key).strip()
    if not text:
        return None
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in text)
    cleaned = cleaned.strip("-_.")
    return cleaned[:64] or None


class _CollectingUi:
    """A mode-aware `ExtensionUi` that records notifications.

    The dispatcher returns the collected messages so the caller (the
    REPL) emits them as live UI output; nothing is archived. This keeps
    dispatch pure and testable and gives deterministic non-interactive
    behavior (notifications are recorded, never blocking).
    """

    def __init__(
        self,
        has_ui: bool,
        notify_sink: "Callable[[str, str], None] | None" = None,
        custom_driver: "CustomComponentDriver | None" = None,
        ui_driver: "ExtensionUiDriver | None" = None,
    ) -> None:
        self.has_ui = has_ui
        self.messages: list[tuple[str, str]] = []
        self.statuses: dict[str, str] = {}
        self.working_message: str | None = None
        self.working_visible: bool = True
        self.widgets: dict[str, tuple[object, str]] = {}
        self.header: object | None = None
        self.footer: object | None = None
        self.title: str | None = None
        self.indicator: tuple[Sequence[str] | None, int | None] | None = None
        self.autocomplete_providers: list[object] = []
        self.editor_component: object | None = None
        self._notify_sink = notify_sink
        self._custom_driver = custom_driver
        self._ui_driver = ui_driver

    def custom(
        self,
        factory: "CustomComponentFactory",
        options: "CustomComponentOptions | None" = None,
    ) -> object:
        """Run a custom interactive component, or no-op deterministically.

        Returns the component's result when a live driver is wired and a UI is
        available; otherwise returns ``None`` without constructing or driving
        the component (deterministic non-interactive behavior).  The optional
        ``options`` mapping accepts Pi-shaped custom-overlay fields; headless
        contexts still ignore it without constructing the component.
        """
        if self._custom_driver is None or not self.has_ui:
            return None
        return self._custom_driver(factory, options)

    def select(self, title: str, options: Sequence[str]) -> str | None:
        choices = tuple(str(option) for option in options if str(option))
        if self._ui_driver is None or not self.has_ui or not choices:
            return None
        try:
            return self._ui_driver.select(str(title), choices)
        except Exception:  # noqa: BLE001 - a UI driver must not break the handler
            return None

    def input(self, title: str, placeholder: str | None = None) -> str | None:
        if self._ui_driver is None or not self.has_ui:
            return None
        try:
            return self._ui_driver.input(str(title), placeholder)
        except Exception:  # noqa: BLE001 - a UI driver must not break the handler
            return None

    def editor(self, title: str, prefill: str | None = None) -> str | None:
        if self._ui_driver is None or not self.has_ui:
            return None
        try:
            return self._ui_driver.editor(str(title), prefill)
        except Exception:  # noqa: BLE001 - a UI driver must not break the handler
            return None

    def confirm(self, title: str, message: str) -> bool:
        if self._ui_driver is None or not self.has_ui:
            return False
        try:
            return bool(self._ui_driver.confirm(str(title), str(message)))
        except Exception:  # noqa: BLE001 - a UI driver must not break the handler
            return False

    def set_status(self, key: str, text: str | None) -> None:
        safe_key = _safe_ui_key(key)
        if safe_key is None:
            return
        if text is None:
            self.statuses.pop(safe_key, None)
        else:
            self.statuses[safe_key] = str(text)
        if self._ui_driver is not None and self.has_ui:
            try:
                self._ui_driver.set_status(
                    safe_key, None if text is None else str(text)
                )
            except Exception:  # noqa: BLE001 - a UI driver must not break the handler
                pass

    def set_working_message(self, message: str | None = None) -> None:
        self.working_message = None if message is None else str(message)
        if self._ui_driver is not None and self.has_ui:
            try:
                self._ui_driver.set_working_message(self.working_message)
            except Exception:  # noqa: BLE001 - a UI driver must not break the handler
                pass

    def set_working_visible(self, visible: bool) -> None:
        self.working_visible = bool(visible)
        if self._ui_driver is not None and self.has_ui:
            try:
                self._ui_driver.set_working_visible(self.working_visible)
            except Exception:  # noqa: BLE001 - a UI driver must not break the handler
                pass

    def set_widget(
        self,
        key: str,
        content: object,
        *,
        placement: WidgetPlacement = "above_editor",
    ) -> None:
        safe_key = _safe_ui_key(key)
        if safe_key is None:
            return
        place = (
            placement
            if placement in ("above_editor", "below_editor")
            else "above_editor"
        )
        if content is None:
            self.widgets.pop(safe_key, None)
        else:
            self.widgets[safe_key] = (content, place)
        if self._ui_driver is not None and self.has_ui:
            try:
                self._ui_driver.set_widget(safe_key, content, place)
            except Exception:  # noqa: BLE001 - a UI driver must not break the handler
                pass

    def set_header(self, factory: object | None) -> None:
        self.header = factory
        if self._ui_driver is not None and self.has_ui:
            try:
                self._ui_driver.set_header(factory)
            except Exception:  # noqa: BLE001 - a UI driver must not break the handler
                pass

    def set_footer(self, factory: object | None) -> None:
        self.footer = factory
        if self._ui_driver is not None and self.has_ui:
            try:
                self._ui_driver.set_footer(factory)
            except Exception:  # noqa: BLE001 - a UI driver must not break the handler
                pass

    def set_title(self, title: str) -> None:
        self.title = str(title)
        if self._ui_driver is not None and self.has_ui:
            try:
                self._ui_driver.set_title(self.title)
            except Exception:  # noqa: BLE001 - a UI driver must not break the handler
                pass

    def set_working_indicator(
        self,
        frames: Sequence[str] | None = None,
        *,
        interval_ms: int | None = None,
    ) -> None:
        if frames is None:
            safe_frames: list[str] | None = None
        else:
            try:
                safe_frames = [str(f) for f in frames]
            except TypeError:
                # A non-iterable frames is ignored rather than raising into the
                # extension handler (fail-soft, like the other ctx.ui setters).
                return
        self.indicator = (safe_frames, interval_ms)
        if self._ui_driver is not None and self.has_ui:
            try:
                self._ui_driver.set_working_indicator(safe_frames, interval_ms)
            except Exception:  # noqa: BLE001 - a UI driver must not break the handler
                pass

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        """Set the live label shown when thinking blocks are folded.

        Headless/no-UI contexts mirror Pi RPC mode: the TUI-only message
        rendering surface is unsupported, so this is a deterministic no-op.
        """
        if self._ui_driver is None or not self.has_ui:
            return
        try:
            set_label = getattr(self._ui_driver, "set_hidden_thinking_label", None)
            if callable(set_label):
                set_label(None if label is None else str(label))
        except Exception:  # noqa: BLE001 - a UI driver must not break the handler
            pass

    def setHiddenThinkingLabel(self, label: str | None = None) -> None:
        self.set_hidden_thinking_label(label)

    def get_editor_text(self) -> str:
        """Return the live core editor text, or ``""`` when headless.

        Mirrors Pi's ``getEditorText`` while preserving pipy's deterministic
        non-interactive UI contract: no driver means no blocking and no state.
        """
        if self._ui_driver is None or not self.has_ui:
            return ""
        try:
            return str(self._ui_driver.get_editor_text())
        except Exception:  # noqa: BLE001 - a UI driver must not break the handler
            return ""

    def getEditorText(self) -> str:
        return self.get_editor_text()

    def set_editor_text(self, text: str) -> None:
        """Replace the live core editor text when a product TUI is available."""
        if self._ui_driver is None or not self.has_ui:
            return
        try:
            self._ui_driver.set_editor_text(str(text))
        except Exception:  # noqa: BLE001 - a UI driver must not break the handler
            pass

    def setEditorText(self, text: str) -> None:
        self.set_editor_text(text)

    def paste_to_editor(self, text: str) -> None:
        """Paste text into the live core editor when a product TUI is available."""
        if self._ui_driver is None or not self.has_ui:
            return
        try:
            self._ui_driver.paste_to_editor(str(text))
        except Exception:  # noqa: BLE001 - a UI driver must not break the handler
            pass

    def pasteToEditor(self, text: str) -> None:
        self.paste_to_editor(text)

    def add_autocomplete_provider(self, factory: object) -> None:
        """Stack an editor autocomplete provider wrapper on the live TUI."""
        if not callable(factory):
            return
        self.autocomplete_providers.append(factory)
        if self._ui_driver is not None and self.has_ui:
            try:
                add_provider = getattr(
                    self._ui_driver, "add_autocomplete_provider", None
                )
                if callable(add_provider):
                    add_provider(factory)
            except Exception:  # noqa: BLE001 - a UI driver must not break the handler
                pass

    def addAutocompleteProvider(self, factory: object) -> None:
        self.add_autocomplete_provider(factory)

    def set_editor_component(self, factory: object | None) -> None:
        """Set or clear the live custom editor factory store.

        This mirrors Pi's ``setEditorComponent`` state surface while keeping
        this slice intentionally render-neutral: pipy stores the opaque object
        in memory only, never calls it, and headless contexts behave like Pi's
        RPC mode (deterministic no-op).
        """
        if self._ui_driver is None or not self.has_ui:
            return
        try:
            set_component = getattr(self._ui_driver, "set_editor_component", None)
            if callable(set_component):
                set_component(factory)
                self.editor_component = factory
        except Exception:  # noqa: BLE001 - a UI driver must not break the handler
            pass

    def setEditorComponent(self, factory: object | None) -> None:
        self.set_editor_component(factory)

    def get_editor_component(self) -> object | None:
        if self._ui_driver is None or not self.has_ui:
            return None
        try:
            get_component = getattr(self._ui_driver, "get_editor_component", None)
            if callable(get_component):
                return get_component()
        except Exception:  # noqa: BLE001 - a UI driver must not break the handler
            return None
        return None

    def getEditorComponent(self) -> object | None:
        return self.get_editor_component()

    def get_tools_expanded(self) -> bool:
        """Return the live product-TUI tool expansion state.

        Headless/no-UI contexts mirror Pi RPC mode: the state is unsupported,
        so callers deterministically see ``False``.
        """
        if self._ui_driver is None or not self.has_ui:
            return False
        try:
            get_expanded = getattr(self._ui_driver, "get_tools_expanded", None)
            if callable(get_expanded):
                return bool(get_expanded())
        except Exception:  # noqa: BLE001 - a UI driver must not break the handler
            return False
        return False

    def getToolsExpanded(self) -> bool:
        return self.get_tools_expanded()

    def set_tools_expanded(self, expanded: bool) -> None:
        if self._ui_driver is None or not self.has_ui:
            return
        try:
            set_expanded = getattr(self._ui_driver, "set_tools_expanded", None)
            if callable(set_expanded):
                set_expanded(bool(expanded))
        except Exception:  # noqa: BLE001 - a UI driver must not break the handler
            pass

    def setToolsExpanded(self, expanded: bool) -> None:
        self.set_tools_expanded(expanded)

    def on_terminal_input(self, handler: Callable[[str], object]) -> Callable[[], None]:
        """Register a live raw terminal-input listener.

        Headless/no-UI contexts mirror Pi RPC mode by returning a safe no-op
        disposer and never invoking the handler.
        """

        if not callable(handler):
            return lambda: None
        if self._ui_driver is None or not self.has_ui:
            return lambda: None
        try:
            add_listener = getattr(self._ui_driver, "add_terminal_input_listener", None)
            if callable(add_listener):
                return cast(Callable[[], None], add_listener(handler))
        except Exception:  # noqa: BLE001 - a UI driver must not break the handler
            return lambda: None
        return lambda: None

    def onTerminalInput(self, handler: Callable[[str], object]) -> Callable[[], None]:
        return self.on_terminal_input(handler)

    def notify(self, message: str, kind: str = "info") -> None:
        safe_kind = kind if kind in ("info", "warning", "error") else "info"
        text = str(message)
        self.messages.append((safe_kind, text))
        if self._notify_sink is not None:
            try:
                self._notify_sink(safe_kind, text)
            except Exception:  # noqa: BLE001 - a UI sink must not break the handler
                pass

    # --- theme controls (rich-UI item E) -----------------------------------
    #
    # Reads are ambient: `available_theme_names`/`resolve_palette`/
    # `resolve_active_theme_name` consult the globally-installed package theme
    # registry plus `PIPY_THEME`/the store, so they are correct and
    # deterministic even headless (more capable than Pi's headless `[]`/
    # `undefined` no-ops, while still side-effect-free). Only `set_theme`
    # mutates the live theme, so it is gated on a live driver + `has_ui` like
    # every other mutating `ctx.ui` method.

    @property
    def theme(self) -> ChromePalette:
        """The current active palette (env override, then store, then default)."""
        name = resolve_active_theme_name(store=NativeThemeStore())
        return resolve_palette(name)

    def get_all_themes(self) -> list[dict[str, str | None]]:
        """Available themes as ``{"name", "path"}`` dicts, default first.

        ``path`` is intentionally always ``None``: the session theme registry
        retains only ``name -> palette`` (theme files state "only the palette
        name reaches any persisted state"), and pipy does not leak package
        theme file paths to extension code. The dict *shape* matches Pi's
        ``getAllThemes`` so Pi extensions translate.
        """
        return [{"name": name, "path": None} for name in available_theme_names()]

    def get_theme(self, name: str) -> ChromePalette | None:
        """Load a palette by name without switching; ``None`` when unknown."""
        text = str(name).strip()
        if not text or not is_known_theme(text):
            return None
        return resolve_palette(text)

    def set_theme(self, theme: "str | ChromePalette") -> dict[str, object]:
        """Switch the live theme by name or palette.

        Returns ``{"success": bool, "error": str | None}``. Headless (no live
        driver) returns ``{"success": False, "error": "UI not available"}``
        without touching process state, matching Pi's headless ``setTheme``.
        """
        name = theme.name if isinstance(theme, ChromePalette) else str(theme)
        if self._ui_driver is None or not self.has_ui:
            return {"success": False, "error": "UI not available"}
        try:
            ok, error = self._ui_driver.apply_theme(name)
        except Exception:  # noqa: BLE001 - a UI driver must not break the handler
            return {"success": False, "error": "theme switch failed"}
        return {
            "success": bool(ok),
            "error": None if ok else (error or "theme switch failed"),
        }

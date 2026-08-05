"""Pipy-owned terminal UI shell for native tool-loop REPL sessions.

The line-oriented renderer prints prompt, loader, assistant text, tool blocks,
and footer as independent lines. This module is the stateful/effectful façade
for a small inline terminal frame; ``native.frame_renderer`` composes immutable
snapshots of its history, transient output, input, overlays, and chrome into
full/live rows and deterministic terminal paint plans.
"""

from __future__ import annotations

import os
import select
import shlex
import subprocess
import sys
import tempfile
import termios
from collections.abc import (
    Callable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import AbstractContextManager, contextmanager
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    TextIO,
    cast,
)

from pipy_harness.native.chrome import (
    ChromeStyle,
    chrome_style_for,
    discover_loaded_resource_names,
    pipy_version_label,
)
from pipy_harness.native.coding.command_registry import project_command_completions
from pipy_harness.native.editor_state import EditorState
from pipy_harness.native.extension_chrome_state import (
    ExtensionChromeCommitToken,
    ExtensionChromeEvent,
    ExtensionChromePrepareInput,
    ExtensionChromeSink,
    ExtensionChromeSnapshot,
    ExtensionChromeState,
)
from pipy_harness.native.extension_runtime import (
    ExtensionTool,
    ToolRenderDetailsSink,
)
from pipy_harness.native.frame_renderer import (
    ChromeSnapshot,
    FrameBlock,
    FrameSnapshot,
    PaintState,
    build_paint_plan,
    render_full_frame,
    render_live_region,
)
from pipy_harness.native.frame_renderer import (
    FrameLine as _FrameLine,
)
from pipy_harness.native.frame_renderer import (
    ResolvedCustomEditorLine as _ResolvedCustomEditorLine,
)
from pipy_harness.native.frame_renderer import (
    block_lines as render_block_lines,
)
from pipy_harness.native.frame_renderer import (
    clip_text as render_clip_text,
)
from pipy_harness.native.frame_renderer import (
    input_index as render_input_index,
)
from pipy_harness.native.frame_renderer import (
    pad_text as render_pad_text,
)
from pipy_harness.native.frame_renderer import (
    sanitize_custom_text as _sanitize_custom_overlay_text,
)
from pipy_harness.native.frame_renderer import (
    style_line as render_styled_line,
)
from pipy_harness.native.keybindings import (
    KeybindingsManager,
)
from pipy_harness.native.overlay_state import (
    ModelSelectorOption as ModelSelectorOption,
)
from pipy_harness.native.overlay_state import (
    OverlayState,
    SettingsOverlayKind,
    TreeSelectorRow,
)
from pipy_harness.native.overlay_state import (
    ScopedModelRow as ScopedModelRow,
)
from pipy_harness.native.overlay_state import (
    SettingsRow as SettingsRow,
)
from pipy_harness.native.project_trust import (
    ProjectTrustEntry,
    ProjectTrustOption,
)
from pipy_harness.native.repl_input import (
    DEFAULT_REPL_COMMAND_DESCRIPTIONS,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.session_tree_commands import (
    SessionListEntry,
    sanitize_label_text,
)
from pipy_harness.native.terminal_driver import (
    _RESIZE_POLL_SECONDS,
    TerminalDriver,
)
from pipy_harness.native.themes import NativeThemeStore, select_theme
from pipy_harness.native.tool_renderers import build_tool_render_theme
from pipy_harness.native.ui.autocomplete import (
    AutocompleteComponent,
    CommandSurface,
)
from pipy_harness.native.ui.chrome_handoff import (
    ChromeAcceptanceResult,
    ChromeHandoffOperation,
    ExtensionChromeRouter,
)
from pipy_harness.native.ui.clipboard_images import ClipboardConfig, ClipboardImages
from pipy_harness.native.ui.components.custom_editor import (
    ExtensionEditorComponent,
)
from pipy_harness.native.ui.components.custom_entry_renderer import (
    CustomEntryTerminalTarget,
)
from pipy_harness.native.ui.components.custom_overlay import (
    CustomComponentRunner,
    custom_overlay_region_lines,
)
from pipy_harness.native.ui.components.extension_prompts import (
    ExtensionConfirmComponent,
    ExtensionInputComponent,
    ExtensionSelectComponent,
)
from pipy_harness.native.ui.components.footer import FooterComponent
from pipy_harness.native.ui.components.input_editor import (
    EditingKeyContext,
    InputEditor,
    apply_editing_key,
)
from pipy_harness.native.ui.components.model_selector import (
    ModelSelectorComponent,
    model_selector_region_lines,
)
from pipy_harness.native.ui.components.scoped_models_selector import (
    ScopedModelsSelectorComponent,
    scoped_models_region_lines,
)
from pipy_harness.native.ui.components.session_picker import (
    SessionPickerComponent,
    session_picker_region_lines,
)
from pipy_harness.native.ui.components.settings_dialog import (
    SettingsDialogComponent,
    settings_dialog_region_lines,
)
from pipy_harness.native.ui.components.tool_loop_renderer import (
    TuiToolLoopRenderer,
)
from pipy_harness.native.ui.components.transcript import (
    HistoryBlock,
    HistoryBlockTuple,
    TranscriptComponent,
)
from pipy_harness.native.ui.components.tree_selector import (
    TreeSelectorClose,
    TreeSelectorComponent,
    tree_selector_region_lines,
)
from pipy_harness.native.ui.extension_chrome import ExtensionChromeComponent
from pipy_harness.native.ui.extension_generation import (
    ExtensionChromeOwners,
    build_extension_chrome_owners,
)
from pipy_harness.native.ui.key_specs import (
    matches_key_specs,
    resolved_key_specs,
)
from pipy_harness.native.ui.paint_lock import PaintLock
from pipy_harness.native.ui.pending_messages import PendingMessages
from pipy_harness.native.ui.terminal_input_listeners import TerminalInputListeners

if TYPE_CHECKING:
    from pipy_harness.native.extension_types import (
        CustomComponentFactory,
        CustomComponentOptions,
    )


TOOL_LOOP_TUI_RUNTIME_LABEL = "tool-loop-tui"
# Curated ordered projection: an explicit advertised-name list validated against
# the declarative command registry. Every name here is a registry built-in (the
# tool-loop menu advertises no resource adjunct); order and membership are
# preserved exactly and are not derived from the full built-in set.
TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS = project_command_completions(
    (
        "/hotkeys",
        "/model",
        "/scoped-models",
        "/settings",
        "/trust",
        "/login",
        "/logout",
        "/copy",
        "/compact",
        "/export",
        "/import",
        "/share",
        "/reload",
        "/changelog",
        "/exit",
        "/quit",
    )
)
# Internal sentinel "commands" returned by ``read_line`` for in-editor hotkeys
# that the session dispatches without rendering a user-message bubble. The
# leading control byte cannot be produced by ordinary typing or paste, so these
# never collide with a real prompt. The session translates the model-cycle
# sentinels into the existing ``/scoped-models next``/``prev`` dispatch.
HOTKEY_THINKING_CYCLE = "\x00pipy-hotkey:thinking-cycle"
HOTKEY_MODEL_CYCLE_NEXT = "\x00pipy-hotkey:model-cycle-next"
HOTKEY_MODEL_CYCLE_PREV = "\x00pipy-hotkey:model-cycle-prev"
HOTKEY_MODEL_SELECT = "\x00pipy-hotkey:model-select"
HOTKEY_TOGGLE_TOOLS = "\x00pipy-hotkey:toggle-tools"
HOTKEY_TOGGLE_THINKING = "\x00pipy-hotkey:toggle-thinking"
# An activated extension's registered keyboard shortcut fired; the normalized
# key follows the prefix so the session can look up and dispatch the handler.
HOTKEY_EXTENSION_SHORTCUT_PREFIX = "\x00pipy-hotkey:ext-shortcut:"

# Outcomes of the active-turn watcher / mid-turn editor.
TURN_SETTLED = "settled"  # the provider turn finished on its own
TURN_ABORTED = "aborted"  # Escape/Ctrl-C cancelled the turn
TURN_STEERED = "steered"  # a steering message interrupted the turn
TURN_LOCAL_COMMAND = "local_command"  # a /… or !… command interrupted the turn


class _LiveExtensionUiDriver:
    """Live `ExtensionUiDriver` backed by the product TUI (one per session)."""

    def __init__(self, terminal_ui: "ToolLoopTerminalUi", cwd: Path) -> None:
        self._terminal_ui = terminal_ui
        self._cwd = cwd
        # Ownership of chrome is a transaction with its own state and no terminal
        # access; it lives in `ui.chrome_handoff` and reaches back only through the
        # delivery callable below. What stays here is the binding half: turning each
        # extension verb into a routed operation, and applying an accepted event to
        # the real terminal.
        self._chrome = ExtensionChromeRouter(self._deliver_chrome_event)

    # -- chrome-transaction delegation -----------------------------------
    # `_LiveExtensionUiDriver` keeps its whole public surface: extensions, the
    # session and the generation proxy all reach the transaction through it.

    def new_candidate_sink(self) -> ExtensionChromeSink:
        return self._chrome.new_candidate_sink()

    def startup_chrome_sink(self) -> ExtensionChromeSink:
        return self._chrome.startup_chrome_sink()

    def prepare_candidate(
        self, prepared: ExtensionChromePrepareInput
    ) -> ExtensionChromeCommitToken | None:
        return self._chrome.prepare_candidate(prepared)

    def accept_candidate(
        self,
        candidate: ExtensionChromeSink,
        *,
        rollback_snapshot: ExtensionChromeSnapshot | None = None,
    ) -> ChromeAcceptanceResult:
        return self._chrome.accept_candidate(
            candidate, rollback_snapshot=rollback_snapshot
        )

    def owns_sink(self, sink: ExtensionChromeSink) -> bool:
        return self._chrome.owns_sink(sink)

    def dispose_retired_sink(self, retired: ExtensionChromeSink) -> str | None:
        return self._chrome.dispose_retired_sink(retired)

    def _route_sink_operation(self, operation: ChromeHandoffOperation) -> object:
        return self._chrome._route_sink_operation(operation)  # noqa: SLF001 - exact transaction owner

    def _route_bound_sink_operation(
        self, sink: ExtensionChromeSink, operation: ChromeHandoffOperation
    ) -> object:
        return self._chrome._route_bound_sink_operation(sink, operation)  # noqa: SLF001 - exact transaction owner

    def _dispose_handoff_listener(self, operation: ChromeHandoffOperation) -> None:
        self._chrome._dispose_handoff_listener(operation)  # noqa: SLF001 - exact transaction owner

    @contextmanager
    def _retiring_disposal_route(self) -> Iterator[None]:
        with self._chrome._retiring_disposal_route():  # noqa: SLF001 - exact transaction owner
            yield

    def candidate_driver(
        self, candidate: ExtensionChromeSink
    ) -> "_GenerationExtensionUiDriver":
        """Bind declarative writes from one candidate callback to its sink."""

        return _GenerationExtensionUiDriver(self, candidate)

    def generation_driver(
        self, sink: ExtensionChromeSink
    ) -> "_GenerationExtensionUiDriver":
        """Bind one ordinary operation and any retained context to its generation."""

        return _GenerationExtensionUiDriver(self, sink)

    def _deliver_chrome_event(self, event: ExtensionChromeEvent) -> object:
        kind = event.kind
        values = event.values
        if kind == "reconcile":
            snapshot = cast(ExtensionChromeSnapshot, values[0])
            return self._terminal_ui.reconcile_extension_chrome(
                snapshot,
                retirement_scope=self._retiring_disposal_route,
            )
        if kind == "widget":
            self._terminal_ui._chrome.component.set_widget(  # noqa: SLF001
                cast(str, values[0]), values[1], placement=cast(str, values[2])
            )
        elif kind == "header":
            self._terminal_ui._chrome.component.set_header(values[0])  # noqa: SLF001
        elif kind == "footer":
            self._terminal_ui._chrome.footer.set_footer(values[0])  # noqa: SLF001
        elif kind == "title":
            self._terminal_ui._chrome.component.set_title(  # noqa: SLF001
                cast(str, values[0])
            )
        elif kind == "indicator":
            self._terminal_ui._chrome.component.set_working_indicator(  # noqa: SLF001
                values[0], values[1]
            )
        elif kind == "hidden-thinking-label":
            self._terminal_ui._transcript.set_hidden_thinking_label(  # noqa: SLF001
                cast("str | None", values[0])
            )
        elif kind == "autocomplete":
            self._terminal_ui.add_extension_autocomplete_provider(values[0])
        elif kind == "editor-component":
            self._terminal_ui.set_editor_component(values[0])
        elif kind == "listener":
            return self._terminal_ui._chrome.listeners.add(  # noqa: SLF001
                cast("Callable[[str], object]", values[1])
            )
        return None

    def select(self, title: str, options: Sequence[str]) -> str | None:
        return self._terminal_ui.run_extension_select(title, options)

    def input(self, title: str, placeholder: str | None = None) -> str | None:
        return self._terminal_ui.run_extension_input(title, placeholder)

    def editor(self, title: str, prefill: str | None = None) -> str | None:
        return self._terminal_ui.run_extension_editor(title, prefill)

    def confirm(self, title: str, message: str) -> bool:
        return self._terminal_ui.run_extension_confirm(title, message)

    def set_status(self, key: str, text: str | None) -> None:
        self._terminal_ui._chrome.component.set_status(key, text)  # noqa: SLF001

    def set_working_message(self, message: str | None = None) -> None:
        self._terminal_ui._chrome.component.set_working_message(  # noqa: SLF001
            message
        )

    def set_working_visible(self, visible: bool) -> None:
        self._terminal_ui._chrome.component.set_working_visible(  # noqa: SLF001
            visible
        )

    def set_widget(self, key: str, content: object, placement: str) -> None:
        self._route_sink_operation(
            ChromeHandoffOperation("widget", (key, content, placement))
        )

    def set_header(self, factory: object | None) -> None:
        self._route_sink_operation(ChromeHandoffOperation("header", (factory,)))

    def set_footer(self, factory: object | None) -> None:
        self._route_sink_operation(ChromeHandoffOperation("footer", (factory,)))

    def set_title(self, title: str) -> None:
        self._route_sink_operation(ChromeHandoffOperation("title", (title,)))

    def set_working_indicator(self, frames: object, interval_ms: object) -> None:
        self._route_sink_operation(
            ChromeHandoffOperation("indicator", (frames, interval_ms))
        )

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        self._route_sink_operation(
            ChromeHandoffOperation("hidden-thinking-label", (label,))
        )

    def get_editor_text(self) -> str:
        return self._terminal_ui.input_editor.get_input_text()

    def set_editor_text(self, text: str) -> None:
        self._terminal_ui.input_editor.set_input_text(text)

    def paste_to_editor(self, text: str) -> None:
        self._terminal_ui.input_editor.paste_input_text(text)

    def add_terminal_input_listener(self, handler: Any) -> Callable[[], None]:
        operation = ChromeHandoffOperation("listener", (handler,))
        result = self._route_sink_operation(operation)
        if callable(result):
            return result
        return lambda: self._dispose_handoff_listener(operation)

    def get_tools_expanded(self) -> bool:
        return bool(self._terminal_ui.tools_expanded)

    def set_tools_expanded(self, expanded: bool) -> None:
        # The terminal UI's verb bundles the retained rich-row rerender with
        # the flag write, so the two writers can never disagree on refresh.
        self._terminal_ui._transcript.set_tools_expanded(  # noqa: SLF001
            bool(expanded)
        )

    def add_autocomplete_provider(self, factory: object) -> None:
        self._route_sink_operation(ChromeHandoffOperation("autocomplete", (factory,)))

    def set_editor_component(self, factory: object | None) -> None:
        self._route_sink_operation(
            ChromeHandoffOperation("editor-component", (factory,))
        )

    def get_editor_component(self) -> object | None:
        # Preserve the pre-R2 API: callers observe the retained live factory,
        # while the terminal UI keeps the instantiated component internally.
        return self._terminal_ui.get_editor_component()

    def apply_theme(self, name: str) -> tuple[bool, str | None]:
        """Switch the live chrome theme (rich-UI item E: ``ctx.ui.set_theme``).

        Reuses ``select_theme`` — the exact mechanism the ``/settings`` theme
        row uses — which validates the name (fail-closed on unknown), persists
        the non-secret name to the chrome store, and sets ``PIPY_THEME`` so the
        next ``chrome_style_for`` render repaints with the new palette. No
        provider turn, tool call, or archive write.
        """
        ok, message = select_theme(name, environ=os.environ, store=NativeThemeStore())
        return ok, None if ok else message


class _GenerationExtensionUiDriver:
    """Route retained declarative reads/writes to one generation's sink.

    Immediate dialogs/editor text/overlays/tools/theme and sticky status/working
    state retain their R0 behavior through ``_live``. Candidate and ordinary
    published operations use the same exact sidecar binding.
    """

    def __init__(self, live: _LiveExtensionUiDriver, sink: ExtensionChromeSink) -> None:
        self._live = live
        self._sink = sink

    def __getattr__(self, name: str) -> Any:
        return getattr(self._live, name)

    def _route(self, operation: ChromeHandoffOperation) -> object:
        return self._live._route_bound_sink_operation(  # noqa: SLF001
            self._sink, operation
        )

    def set_widget(self, key: str, content: object, placement: str) -> None:
        self._route(ChromeHandoffOperation("widget", (key, content, placement)))

    def set_header(self, factory: object | None) -> None:
        self._route(ChromeHandoffOperation("header", (factory,)))

    def set_footer(self, factory: object | None) -> None:
        self._route(ChromeHandoffOperation("footer", (factory,)))

    def set_title(self, title: str) -> None:
        self._route(ChromeHandoffOperation("title", (title,)))

    def set_working_indicator(self, frames: object, interval_ms: object) -> None:
        self._route(ChromeHandoffOperation("indicator", (frames, interval_ms)))

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        self._route(ChromeHandoffOperation("hidden-thinking-label", (label,)))

    def add_terminal_input_listener(self, handler: Any) -> Callable[[], None]:
        result = self._route(ChromeHandoffOperation("listener", (handler,)))
        assert callable(result)
        return result

    def add_autocomplete_provider(self, factory: object) -> None:
        self._route(ChromeHandoffOperation("autocomplete", (factory,)))

    def set_editor_component(self, factory: object | None) -> None:
        self._route(ChromeHandoffOperation("editor-component", (factory,)))

    def get_editor_component(self) -> object | None:
        return self._sink.snapshot().editor_component


class _ExtensionChromeTuiHandle:
    """Small Pi-shaped TUI handle passed to extension chrome factories."""

    def __init__(self, ui: "ToolLoopTerminalUi") -> None:
        self._ui = ui

    def requestRender(self, force: bool = False) -> None:  # noqa: N802 - Pi API
        """Request a live repaint without producing a provider turn."""

        del force
        try:
            self._ui.request_extension_chrome_render()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - a repaint request is fail-soft
            return

    def request_render(self, force: bool = False) -> None:
        """Pythonic alias for extensions that prefer snake_case."""

        self.requestRender(force)


class _CustomEditorKeybindings:
    """Small Pi-shaped keybinding/action adapter for custom editors.

    The literal keys mirror the built-in read-loop branches that return
    ``HOTKEY_*`` sentinels. Canonical bindings stay in Pi's ``ctrl+p`` style
    while aliases accept pipy's decoded ``ctrl-p`` live events.
    """

    _HANDLER_ACTIONS: tuple[str, ...] = (
        "app.interrupt",
        "app.exit",
        "app.thinking.cycle",
        "app.model.cycleForward",
        "app.model.cycleBackward",
        "app.model.select",
        "app.tools.expand",
        "app.thinking.toggle",
        "app.editor.external",
        "app.message.followUp",
        "app.message.dequeue",
    )

    def __init__(
        self,
        ui: "ToolLoopTerminalUi",
        keybindings_manager: KeybindingsManager | None = None,
    ) -> None:
        self._ui = ui
        self._keybindings_manager = keybindings_manager
        self.action_handlers: dict[str, Callable[[], object]] = {}
        for action in self._HANDLER_ACTIONS:
            self.action_handlers[action] = self._handler_for(action)
        self.actionHandlers = self.action_handlers

    def _handler_for(self, action: str) -> Callable[[], object]:
        def handler() -> object:
            self._ui._queue_custom_editor_action(action)
            return None

        return handler

    def keys_for(self, action: str) -> list[str]:
        return resolved_key_specs(action, self._keybindings_manager)

    def matches(self, key: str, action: str) -> bool:
        return matches_key_specs(key, self.keys_for(action))

    def matches_action(self, key: str, action: str) -> bool:
        return self.matches(key, action)

    def matchesAction(self, key: str, action: str) -> bool:
        return self.matches(key, action)


@dataclass(slots=True)
class ToolLoopTerminalUi:
    """Stateful terminal frame for the native tool-loop REPL.

    The UI intentionally uses whole-frame repainting (`cursor home` +
    region composition) instead of relative row rewrites.  Tests can
    inspect :meth:`render_lines` directly, while real TTY sessions use
    :meth:`paint` to draw the current frame.
    """

    input_stream: TextIO
    terminal_stream: TextIO
    cwd: Path
    include_workspace_defaults: bool = False
    runtime_label: str = TOOL_LOOP_TUI_RUNTIME_LABEL
    footer_lines: tuple[str, str] = ("", "")
    # Built-in editor effects and frame projection. Its exact EditorState is
    # also injected into autocomplete, pending messages, and clipboard images.
    input_editor: InputEditor = field(init=False)
    # Single owner for committed history blocks, the live stream buffers, and
    # the Ctrl+O/Ctrl+T view flags (``ui/components/transcript.py``). The
    # facade keeps thin verb delegates and two read-only flag projections.
    _transcript: TranscriptComponent = field(init=False)
    # One composition handle groups the dependency-neutral extension record,
    # its three effect owners, and the ordered generation owner. This replaces
    # the pre-slice record handle without growing facade state.
    _chrome: ExtensionChromeOwners = field(init=False)
    available_provider_count: int = 0
    # Single owner for the slash menu, the @/path completion popup, the
    # published CommandSurface (names/descriptions/extension shortcut keys),
    # the settings-driven row cap, and the extension provider registry effects
    # (``ui/autocomplete.py``). Session startup and ``/reload`` publish through
    # its ``replace_command_surface``/``set_max_visible`` verbs.
    _autocomplete: AutocompleteComponent = field(init=False)
    # Exactly one selector/dialog/custom overlay is active. Terminal I/O,
    # callbacks, extension execution, rendering, and lifecycle effects stay in
    # this facade; the owner holds only synchronous transition state.
    _overlays: OverlayState = field(init=False)
    # Queue and clipboard effects share EditorState and the one paint lock but
    # own their transitions outside this shell. Their public handles let the
    # session wiring consume the real owners without retaining queue facades.
    pending_messages: PendingMessages = field(init=False)
    clipboard_images: ClipboardImages = field(init=False)
    # Low-level terminal I/O owner (write/flush sink, raw-mode lifecycle,
    # bracketed-paste toggling, terminal-title OSC). Built in ``__post_init__``
    # from the input/terminal streams.
    _driver: TerminalDriver = field(init=False)
    _closed: bool = False
    # Inline scrollback rendering state: committed history is printed once into
    # the terminal's normal buffer (so native scrollback in Ghostty/zellij can
    # review it), and only the live region (transient stream + input/footer) is
    # redrawn in place below it.
    _painted_block_count: int = 0
    _live_height: int = 0
    _live_input_row: int = 0
    _paint_lock: PaintLock = field(default_factory=PaintLock)
    _painting: bool = False
    _paint_requested_during_paint: bool = False
    # Live extension custom editor component (Pi ``ctx.ui.setEditorComponent``).
    # The component is trusted extension code and is duck-typed: factories may
    # return objects with render/handle_input/get_text/set_text plus callback
    # attributes. The built-in editor remains the persistence/source-of-truth
    # boundary; switching in either direction preserves the current buffer.
    _custom_editor_factory: object | None = None
    _custom_editor_component: object | None = None
    _custom_editor_active: bool = False
    _custom_editor_submitted: str | None = None
    _custom_editor_changed_text: str | None = None
    _custom_editor_action: str | None = None
    _custom_editor_exit_requested: bool = False
    # Resize handling. The SIGWINCH lifecycle and the pending flag live on the
    # terminal driver; the UI keeps only the last painted geometry it compares
    # the driver's live size against during the layout-coupled resize repaint.
    _last_painted_size: tuple[int, int] = (0, 0)
    keybindings_manager: KeybindingsManager | None = None
    # Constructor-only wiring record: ClipboardImages owns it after startup;
    # unlike the retired reader/path fields it cannot be rewritten piecemeal.
    clipboard_config: InitVar[ClipboardConfig | None] = None

    def __post_init__(self, clipboard_config: ClipboardConfig | None) -> None:
        editor = EditorState()
        self._overlays = OverlayState()
        chrome_record = ExtensionChromeState()
        self._driver = TerminalDriver(self.input_stream, self.terminal_stream)
        self._autocomplete = AutocompleteComponent(
            editor,
            cwd=self.cwd,
            repaint=self.paint,
            custom_editor_component=lambda: self._custom_editor_component,
            surface=CommandSurface(
                names=TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS,
                descriptions=dict(DEFAULT_REPL_COMMAND_DESCRIPTIONS),
            ),
        )
        self._transcript = TranscriptComponent(
            self._paint_lock,
            self.paint,
            reset_scrollback=self._force_full_redraw,
            frame_width=lambda: self._driver.size()[0],
            render_theme=lambda: build_tool_render_theme(
                chrome_style_for(self.terminal_stream)
            ),
        )

        def active_custom_editor_text() -> str | None:
            return self._custom_editor_text() if self._custom_editor_active else None

        self.pending_messages = PendingMessages(
            editor,
            self._paint_lock,
            self.paint,
            custom_editor_active=lambda: self._custom_editor_active,
            custom_editor_text=self._custom_editor_text,
            set_custom_editor_text=self._set_custom_editor_text,
            refresh_slash_menu=self._autocomplete.refresh_slash_menu,
        )
        self.clipboard_images = ClipboardImages(
            editor,
            self._paint_lock,
            self.paint,
            cwd=self.cwd,
            config=clipboard_config,
            command_names=lambda: self._autocomplete.command_names,
            refresh_autocomplete=self._autocomplete.refresh,
            add_notice=self.add_notice,
            custom_editor_text=active_custom_editor_text,
            set_custom_editor_text=self._set_custom_editor_text,
        )
        self.input_editor = InputEditor(
            editor,
            self._paint_lock,
            self.paint,
            command_names=lambda: self._autocomplete.command_names,
            refresh_autocomplete=self._autocomplete.refresh,
            custom_editor_active=lambda: self._custom_editor_active,
            custom_editor_text=self._custom_editor_text,
            set_custom_editor_text=self._set_custom_editor_text,
            insert_paste=self.clipboard_images.insert_paste,
        )
        chrome = ExtensionChromeComponent(
            chrome_record,
            self._paint_lock,
            self.paint,
            tui_handle=_ExtensionChromeTuiHandle(self),
            region_width=lambda: self._driver.size()[0],
            render_theme=lambda: build_tool_render_theme(
                chrome_style_for(self.terminal_stream)
            ),
            push_title=self._driver.push_title,
            write_title=self._driver.write_title,
            restore_title=self._driver.restore_title,
            clear_working_text=self._transcript.discard_working_text,
        )
        footer = FooterComponent(
            chrome_record,
            self._paint_lock,
            self.paint,
            cwd=self.cwd,
            available_provider_count=lambda: self.available_provider_count,
            build_region=chrome.build_region,
            dispose_region=chrome.dispose_region,
            render_region=chrome.render_region,
        )
        listeners = TerminalInputListeners(chrome_record, self._paint_lock, self.paint)

        def clear_autocomplete() -> None:
            editor.autocomplete_provider_factories.clear()
            editor.close_autocomplete()

        def clear_custom_editor() -> None:
            self._custom_editor_factory = None
            self._custom_editor_component = None
            self._custom_editor_active = False

        def restore_editor_text(text: str) -> None:
            self.input_editor.set_buffer(text)
            self.input_editor.pending_initial_text = text

        self._chrome = build_extension_chrome_owners(
            chrome_record,
            self._paint_lock,
            self.paint,
            component=chrome,
            footer=footer,
            listeners=listeners,
            custom_editor_active=lambda: self._custom_editor_active,
            read_input_text=self.input_editor.get_input_text,
            current_custom_editor_component=lambda: self._custom_editor_component,
            clear_autocomplete=clear_autocomplete,
            clear_custom_editor=clear_custom_editor,
            restore_editor_text=restore_editor_text,
            reset_hidden_thinking_label=self._transcript.reset_hidden_thinking_label,
            add_autocomplete_provider=self.add_extension_autocomplete_provider,
            set_editor_component=self.set_editor_component,
            set_hidden_thinking_label=self._transcript.set_hidden_thinking_label,
        )

    @property
    def autocomplete(self) -> AutocompleteComponent:
        """Owner handle for slash-menu/completion state and command surface."""

        return self._autocomplete

    # Overlay/chrome projections are direct views into slotted owners. They
    # preserve characterized facade access without a second stored copy; an
    # ``*_open`` write changes the one active-overlay discriminator, so two
    # overlays cannot become renderable simultaneously.
    @property
    def custom_overlay_open(self) -> bool:
        return self._overlays.is_open("custom")

    @custom_overlay_open.setter
    def custom_overlay_open(self, value: bool) -> None:
        if value:
            self._overlays.supersede("custom")
        else:
            self._overlays.close("custom")

    @classmethod
    def is_supported(cls, input_stream: TextIO, terminal_stream: TextIO) -> bool:
        if input_stream is not sys.stdin or terminal_stream is not sys.stderr:
            return False
        if sys.platform.startswith("win"):
            return False
        if os.environ.get("TERM", "").lower() == "dumb":
            return False
        if not bool(getattr(input_stream, "isatty", lambda: False)()):
            return False
        if not bool(getattr(terminal_stream, "isatty", lambda: False)()):
            return False
        return hasattr(input_stream, "fileno")

    def start(self) -> None:
        """Initialize the shell history and paint the first frame.

        The TUI runs inline (no alternate screen): startup chrome and every
        finalized block are committed into the terminal's normal buffer so the
        host terminal/multiplexer keeps them in native scrollback.
        """

        if not self._transcript.history_blocks:
            self._transcript.seed_history(self._startup_blocks())
        self._driver.install_resize_handler()
        self.paint()

    def read_line(self, prompt_label: str, *, footer: str | None = None) -> str:
        """Read one input line while keeping the input/footer regions live."""

        del prompt_label
        if footer is not None:
            self.set_footer_text(footer)
        self.input_editor.begin_line()
        if self._custom_editor_active:
            self._set_custom_editor_text(self.input_editor.text)
        self.paint()
        fd = self.input_stream.fileno()
        editing_context = EditingKeyContext(
            slash_menu_open=lambda: self._autocomplete.slash_menu_open,
            slash_menu_has_matches=lambda: bool(self._autocomplete.filtered_commands()),
            autocomplete_open=lambda: self._autocomplete.autocomplete_open,
            navigate_slash_menu=self._autocomplete.navigate_slash_menu,
            navigate_autocomplete=self._autocomplete.navigate,
            accept_slash_menu=self._autocomplete.accept_slash_menu_selection,
            accept_autocomplete=self._autocomplete.accept_selection,
            attempt_path_completion=self._autocomplete.attempt_path_completion,
            allow_history=True,
            allow_path_completion=True,
            allow_listener_replacement=True,
            listener_replaced=lambda: self._chrome.listeners.last_replaced,
        )
        with self._driver.raw_mode():
            while True:
                key = self._read_key_polling_resize(fd)
                if key is None:
                    return ""
                key = self._chrome.listeners.apply(key)
                if self._custom_editor_active:
                    submitted = self._handle_custom_editor_key(key)
                    if submitted is not None:
                        if self._custom_editor_exit_requested:
                            self._custom_editor_exit_requested = False
                            self.input_editor.reset_line_editor_state()
                            self.paint()
                            return ""
                        self.input_editor.record_history(submitted)
                        self.input_editor.reset_line_editor_state()
                        self.paint()
                        return f"{submitted}\n"
                    continue
                if key is None:
                    self.paint()
                    continue
                if key == "enter":
                    if self._autocomplete.autocomplete_open:
                        # Enter accepts the highlighted completion (Pi: Enter/Tab
                        # accept) and keeps editing rather than submitting.
                        self._autocomplete.accept_selection()
                        continue
                    if (
                        self._autocomplete.slash_menu_open
                        and self._autocomplete.filtered_commands()
                    ):
                        matches = self._autocomplete.filtered_commands()
                        if self.input_editor.text not in matches:
                            self._autocomplete.accept_slash_menu_selection()
                    submitted = self.input_editor.submit_line()
                    self.paint()
                    return f"{submitted}\n"
                if key == "ctrl-c":
                    raise KeyboardInterrupt
                if key == "ctrl-d":
                    if not self.input_editor.text:
                        return ""
                    continue
                if self._matches_keybinding(key, "app.editor.external"):
                    edited = self._run_configured_external_editor(
                        self.input_editor.text
                    )
                    if edited is None:
                        self.paint()
                    else:
                        self.input_editor.replace_after_external_edit(edited)
                    continue
                if key in {"ctrl-p", "shift-ctrl-p"}:
                    # app.model.cycleForward (ctrl+p) / cycleBackward
                    # (shift+ctrl+p): cycle the active model through the scoped
                    # set. Delegated to the session's /scoped-models dispatch so
                    # the live provider rebinds through the shared select_model
                    # boundary; no provider turn. Any partially-typed input is
                    # preserved and re-injected into the next prompt so the cycle
                    # never drops what the user was typing. (shift+ctrl+p is only
                    # decodable on terminals speaking the kitty keyboard
                    # protocol; legacy terminals send plain ctrl+p and cycle
                    # forward — a documented input-decoding limit.)
                    self.input_editor.preserve_for_next_line()
                    return (
                        f"{HOTKEY_MODEL_CYCLE_PREV}\n"
                        if key == "shift-ctrl-p"
                        else f"{HOTKEY_MODEL_CYCLE_NEXT}\n"
                    )
                if key == "shift-tab":
                    # app.thinking.cycle: cycle the reasoning level. Dispatched
                    # by the session without a provider turn; the partially-typed
                    # buffer is preserved into the next prompt.
                    self.input_editor.preserve_for_next_line()
                    return f"{HOTKEY_THINKING_CYCLE}\n"
                if key in {"ctrl-o", "ctrl-t"}:
                    # app.tools.expand (ctrl+o) / app.thinking.toggle (ctrl+t):
                    # renderer view-flag toggles dispatched by the session (so the
                    # thinking-visibility setting can be persisted and a status
                    # shown). The partially-typed buffer is preserved.
                    self.input_editor.preserve_for_next_line()
                    return (
                        f"{HOTKEY_TOGGLE_TOOLS}\n"
                        if key == "ctrl-o"
                        else f"{HOTKEY_TOGGLE_THINKING}\n"
                    )
                if key == "paste":
                    self.clipboard_images.insert_paste(
                        self.input_editor.consume_paste()
                    )
                    continue
                if key == "ctrl-v":
                    # app.clipboard.pasteImage: read an image from the OS
                    # clipboard, write it to an owner-only temp file, and insert
                    # an @image: reference. No provider turn.
                    self.clipboard_images.paste_clipboard_image()
                    continue
                if key in self._autocomplete.shortcut_keys:
                    # An activated extension bound this key via
                    # api.register_shortcut. Preserve any partially-typed input
                    # into the next prompt (like the app hotkeys) and hand the
                    # session the sentinel so it dispatches the bound handler.
                    self.input_editor.preserve_for_next_line()
                    return f"{HOTKEY_EXTENSION_SHORTCUT_PREFIX}{key}\n"
                if key == "esc":
                    if self._autocomplete.slash_menu_open:
                        self._autocomplete.dismiss_slash_menu()
                    elif self._autocomplete.autocomplete_open:
                        self._autocomplete.close()
                        self.paint()
                    continue
                apply_editing_key(self.input_editor, key, editing_context)

    def wait_for_active_turn_interrupt(
        self,
        done_event: Any,
        abort_event: Any,
        *,
        poll_seconds: float = 0.05,
        accept_queue: bool = False,
        accept_commands: bool = False,
    ) -> str:
        """Watch stdin during an active turn; optionally a mid-turn editor.

        Returns one of :data:`TURN_SETTLED`, :data:`TURN_ABORTED`,
        :data:`TURN_STEERED`, or :data:`TURN_LOCAL_COMMAND`. With both
        ``accept_queue`` and ``accept_commands`` disabled it only watches for
        Escape (sets ``abort_event``, returns ``aborted``) and Ctrl-C (sets
        ``abort_event``, raises). With ``accept_queue=True`` (a provider turn)
        it also accepts editor input mid-turn: a normal Enter enqueues a
        steering message and interrupts the turn (returns ``steered``),
        Alt+Enter enqueues a follow-up without interrupting, Alt+Up restores
        queued messages to the editor, and Escape/Ctrl-C abort (the caller
        restores the queue to the editor). With ``accept_commands=True`` and
        ``accept_queue=False`` (e.g. a ``!`` shell run), only local command
        input that starts with ``/`` or ``!`` is editable/submittable; submitting
        one interrupts the active work and hands the command to the session.
        """

        fd = self.input_stream.fileno()
        command_only = accept_commands and not accept_queue
        editing_context = EditingKeyContext(
            slash_menu_open=lambda: self._autocomplete.slash_menu_open,
            slash_menu_has_matches=lambda: bool(self._autocomplete.filtered_commands()),
            autocomplete_open=lambda: self._autocomplete.autocomplete_open,
            navigate_slash_menu=self._autocomplete.navigate_slash_menu,
            navigate_autocomplete=self._autocomplete.navigate,
            accept_slash_menu=self._autocomplete.accept_slash_menu_selection,
            accept_autocomplete=self._autocomplete.accept_selection,
            attempt_path_completion=self._autocomplete.attempt_path_completion,
            allow_history=False,
            allow_path_completion=not command_only,
            tab_repaint="always",
        )
        with self._driver.raw_mode():
            while not done_event.is_set():
                # Keep the streaming frame coherent if the terminal is resized
                # mid-turn: streamed chunks repaint at the live size, but a
                # stalled stream would not, so poll here too.
                self._poll_resize_repaint()
                key = self._read_driver_key(
                    self._driver.read_key_if_available(fd, poll_seconds)
                )
                if key is None:
                    continue
                if key == "esc":
                    abort_event.set()
                    return TURN_ABORTED
                if key == "ctrl-c":
                    abort_event.set()
                    raise KeyboardInterrupt
                if not accept_queue and not accept_commands:
                    if key == "paste":
                        # A paste mid-turn is not editor input; drop it so its
                        # body never lingers into the next prompt.
                        self.input_editor.consume_paste()
                    continue
                # In command-only mode, preserve the old "ignore random typing"
                # behavior until the user explicitly starts a local command.
                if (
                    command_only
                    and not self.input_editor.text
                    and key not in {"/", "!"}
                ):
                    if key == "paste":
                        self.input_editor.consume_paste()
                    continue
                # accept_queue / accept_commands: a mid-turn editor for
                # steering/follow-up and/or local commands.
                if key == "enter":
                    if self._autocomplete.autocomplete_open:
                        self._autocomplete.accept_selection()
                        continue
                    if (
                        self._autocomplete.slash_menu_open
                        and self._autocomplete.filtered_commands()
                    ):
                        matches = self._autocomplete.filtered_commands()
                        if self.input_editor.text not in matches:
                            self._autocomplete.accept_slash_menu_selection()
                    text = self.input_editor.text
                    self.input_editor.reset_mid_turn_input()
                    if not text.strip():
                        self.paint()
                        continue
                    # A recognized local command (`/…` or `!…`) is never queued
                    # for the provider: like Pi's editor, Enter runs it
                    # immediately rather than steering. It interrupts the turn
                    # and is handed to the session loop to dispatch locally.
                    if self._submitted_text_is_local_command(text):
                        self.input_editor.set_pending_command(text)
                        abort_event.set()
                        self.paint()
                        return TURN_LOCAL_COMMAND
                    if command_only:
                        self.paint()
                        continue
                    self.pending_messages.enqueue_steering(text)
                    abort_event.set()
                    return TURN_STEERED
                if key == "alt-enter":
                    if command_only:
                        continue
                    text = self.input_editor.text
                    self.input_editor.reset_mid_turn_input()
                    self.pending_messages.enqueue_follow_up(text)
                    continue
                if key == "alt-up":
                    if command_only:
                        continue
                    self.pending_messages.restore_pending_to_editor()
                    continue
                if key == "paste":
                    if command_only:
                        self.input_editor.consume_paste()
                        continue
                    self.clipboard_images.insert_paste(
                        self.input_editor.consume_paste()
                    )
                    continue
                apply_editing_key(self.input_editor, key, editing_context)
            return TURN_SETTLED

    def run_model_selector(
        self,
        options: Sequence[ModelSelectorOption],
        *,
        current_index: int = 0,
        title: str | None = None,
    ) -> int | None:
        """Drive the interactive provider/model selector; return a chosen index.

        Renders the supplied rows in the live region and reads raw keys: up/down
        move the highlight (wrapping), ``Enter`` chooses the highlighted row when
        it is selectable, and ``Esc`` / ``Ctrl-C`` / ``Ctrl-D`` / EOF cancel.
        Returns the chosen index, or ``None`` when cancelled or when no row is
        selectable. This method never invokes the provider, tools, or a model
        turn; it is pure local navigation that the caller acts on afterwards.

        ``title`` overrides the overlay heading so the same generic
        label/selectable selector can serve non-model pickers (e.g. the
        ``/settings`` theme row); ``None`` keeps the provider/model wording.
        """

        selector = ModelSelectorComponent(self._overlays, self._paint_lock, self.paint)
        if not selector.open(options, current_index=current_index, title=title):
            return None
        fd = self.input_stream.fileno()
        with self._driver.raw_mode():
            while True:
                key = self._read_key_polling_resize(fd)
                if key == "paste":
                    self.input_editor.consume_paste()
                    continue
                closed = selector.handle_key(key)
                if closed is not None:
                    return closed.index

    def run_scoped_models_selector(
        self,
        rows: Sequence[ScopedModelRow],
        *,
        checked: Iterable[int] = (),
    ) -> frozenset[str] | None:
        """Drive the ``/scoped-models`` multi-select overlay; return the scope.

        Renders one checkbox row per available model. Up/Down move, Space toggles
        membership of the highlighted row, ``a`` enables all, ``c`` clears all,
        Enter saves and returns the chosen ``provider/model`` reference set, and
        Esc/Ctrl-C/Ctrl-D cancel (returning ``None``). Runs no provider turn.
        """

        selector = ScopedModelsSelectorComponent(
            self._overlays, self._paint_lock, self.paint
        )
        if not selector.open(rows, checked):
            return None
        fd = self.input_stream.fileno()
        with self._driver.raw_mode():
            while True:
                key = self._read_key_polling_resize(fd)
                if key == "paste":
                    self.input_editor.consume_paste()
                    continue
                closed = selector.handle_key(key)
                if closed is not None:
                    return closed.references

    def run_settings_dialog(
        self,
        rows: Sequence[SettingsRow],
        *,
        on_local_action: Callable[[str], Sequence[SettingsRow]],
        exit_actions: frozenset[str] = frozenset(),
        current_index: int | None = None,
        title: str = "Settings",
        overlay_kind: SettingsOverlayKind = "settings",
    ) -> str | None:
        """Drive the interactive ``/settings`` dialog as a live overlay.

        Renders the supplied rows in the live region and reads raw keys: up/down
        move the highlight between actionable rows (wrapping, skipping headers
        and read-only status rows), and ``Enter``/``Space`` activate the
        highlighted action row. ``Esc`` / ``Ctrl-C`` / ``Ctrl-D`` / EOF close the
        dialog and return ``None``. ``overlay_kind`` selects only the internal
        stack identity for this settings-family payload: ``project_trust`` uses
        the same rows, selection, and title state but remains distinct so a
        settings -> project_trust -> settings nesting restores exactly.

        Activating an action whose identifier is in ``exit_actions`` closes the
        dialog and returns that identifier so the caller can run a flow that
        needs the terminal itself (the provider/model selector, or interactive
        auth). Any other action is *local*: ``on_local_action`` is invoked with
        the identifier and must return the rebuilt rows, and the dialog stays
        open and re-renders in place. This method never invokes the provider,
        tools, or a model turn; it is pure local navigation/state toggling that
        the caller acts on afterwards.
        """

        dialog = SettingsDialogComponent(
            self._overlays,
            self._paint_lock,
            self.paint,
            on_local_action=on_local_action,
            exit_actions=exit_actions,
        )
        if not dialog.open(
            rows, current_index=current_index, title=title, kind=overlay_kind
        ):
            return None
        fd = self.input_stream.fileno()
        with self._driver.raw_mode():
            while True:
                key = self._read_key_polling_resize(fd)
                if key == "paste":
                    self.input_editor.consume_paste()
                    continue
                closed = dialog.handle_key(key)
                if closed is not None:
                    return closed.action

    def set_editor_component(self, factory: object | None) -> None:
        """Install or clear a live extension custom editor component.

        Pi calls ``factory(tui, theme, keybindings)`` and swaps the returned
        editor into the main editor container. Pipy keeps the same ownership
        boundary with a small duck-typed adapter instead of a Pi TUI port:
        trusted extension components may render rows, consume decoded keys, and
        submit through wired callbacks. Bad factories fail closed to the built-in
        editor, and clearing preserves the component's current text.
        """

        current_text = self.input_editor.get_input_text()
        self._custom_editor_submitted = None
        self._custom_editor_action = None
        self._custom_editor_changed_text = None
        self._custom_editor_exit_requested = False
        if factory is None:
            self._custom_editor_factory = None
            self._custom_editor_component = None
            self._custom_editor_active = False
            self.input_editor.set_input_text(current_text)
            self.paint()
            return
        if not callable(factory):
            return
        self._custom_editor_factory = factory
        try:
            component = factory(
                self,
                chrome_style_for(self.terminal_stream),
                _CustomEditorKeybindings(self, self.keybindings_manager),
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - extension factory fails closed
            self._custom_editor_component = None
            self._custom_editor_active = False
            self.paint()
            return
        self._custom_editor_component = component
        self._custom_editor_active = component is not None
        if component is not None:
            self._wire_custom_editor_component(component)
            self._set_custom_editor_text(current_text)
            self._autocomplete.forward_to_custom_editor(component)
        self.paint()

    def get_editor_component(self) -> object | None:
        return self._custom_editor_factory

    def _wire_custom_editor_component(self, component: object) -> None:
        def submit(value: object | None = None) -> None:
            text = self._custom_editor_text() if value is None else str(value)
            self._custom_editor_submitted = text
            self.input_editor.set_buffer(text)

        def change(value: object | None = None) -> None:
            text = self._custom_editor_text() if value is None else str(value)
            self._custom_editor_changed_text = text
            self.input_editor.set_buffer(text)

        for name in ("on_submit", "onSubmit"):
            self._set_component_attr(component, name, submit)
        for name in ("on_change", "onChange"):
            self._set_component_attr(component, name, change)
        self._set_component_attr_if_absent(
            component,
            "on_extension_shortcut",
            lambda key: self._queue_custom_editor_action(
                f"app.extensionShortcut:{key}"
            ),
        )
        self._set_component_attr_if_absent(
            component,
            "onExtensionShortcut",
            lambda key: self._queue_custom_editor_action(
                f"app.extensionShortcut:{key}"
            ),
        )
        self._set_component_attr_pair_if_absent(
            component,
            ("on_escape", "onEscape"),
            lambda: self._queue_custom_editor_action("app.interrupt"),
        )
        self._set_component_attr_pair_if_absent(
            component,
            ("on_ctrl_d", "onCtrlD"),
            lambda: self._queue_custom_editor_action("app.exit"),
        )
        self._set_component_attr_pair_if_absent(
            component,
            ("on_paste_image", "onPasteImage"),
            lambda: self._queue_custom_editor_action("app.clipboard.pasteImage"),
        )
        handlers = getattr(component, "action_handlers", None)
        if handlers is None:
            handlers = getattr(component, "actionHandlers", None)
        if handlers is not None:
            for action in _CustomEditorKeybindings._HANDLER_ACTIONS:
                try:
                    if action not in handlers:
                        handlers[action] = lambda action=action: (
                            self._queue_custom_editor_action(action)
                        )
                except Exception:  # noqa: BLE001 - duck-typed mapping may be immutable
                    pass

    def _queue_custom_editor_action(self, action: str) -> None:
        self._custom_editor_action = action

    @staticmethod
    def _set_component_attr(component: object, name: str, value: object) -> None:
        try:
            setattr(component, name, value)
        except Exception:  # noqa: BLE001 - duck-typed object may forbid attrs
            pass

    @staticmethod
    def _set_component_attr_if_absent(
        component: object, name: str, value: object
    ) -> None:
        try:
            if getattr(component, name, None) is not None:
                return
        except Exception:  # noqa: BLE001 - still attempt to set below
            pass
        try:
            setattr(component, name, value)
        except Exception:  # noqa: BLE001 - duck-typed object may forbid attrs
            pass

    @classmethod
    def _set_component_attr_pair_if_absent(
        cls, component: object, names: tuple[str, str], value: object
    ) -> None:
        existing = None
        for name in names:
            try:
                candidate = getattr(component, name, None)
            except Exception:  # noqa: BLE001 - still attempt to set below
                candidate = None
            if candidate is not None:
                existing = candidate
                break
        shared = existing if existing is not None else value
        for name in names:
            cls._set_component_attr_if_absent(component, name, shared)

    def _custom_editor_text(self) -> str:
        component = self._custom_editor_component
        if component is None:
            return self.input_editor.text
        for name in ("get_text", "getText"):
            try:
                getter = getattr(component, name, None)
                if callable(getter):
                    return str(getter())
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - trusted editor fails soft
                break
        if self._custom_editor_changed_text is not None:
            return self._custom_editor_changed_text
        return self.input_editor.text

    def _set_custom_editor_text(self, text: str) -> None:
        component = self._custom_editor_component
        self.input_editor.set_buffer(str(text))
        if component is None:
            return
        for name in ("set_text", "setText"):
            setter = getattr(component, name, None)
            if callable(setter):
                try:
                    setter(self.input_editor.text)
                except Exception:  # noqa: BLE001 - keep built-in mirror
                    pass
                return

    def _handle_custom_editor_key(self, key: str | None) -> str | None:
        self._custom_editor_exit_requested = False
        if key is None:
            self.paint()
            return None
        component = self._custom_editor_component
        if component is None:
            return None
        self._custom_editor_submitted = None
        self._custom_editor_action = None
        handler = getattr(component, "handle_input", None) or getattr(
            component, "handleInput", None
        )
        if callable(handler):
            try:
                result = handler(key)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - bad custom editor falls back
                self.set_editor_component(None)
                return None
            if isinstance(result, str):
                self._custom_editor_submitted = result
        elif key == "enter":
            self._custom_editor_submitted = self._custom_editor_text()
        if self._custom_editor_action is not None:
            action = self._custom_editor_action
            self._custom_editor_action = None
            self.input_editor.set_buffer(self._custom_editor_text())
            if action in {
                "app.model.cycleForward",
                "app.model.cycleBackward",
                "app.model.select",
                "app.thinking.cycle",
                "app.tools.expand",
                "app.thinking.toggle",
            }:
                if self.input_editor.text:
                    self.input_editor.pending_initial_text = self.input_editor.text
                self._set_custom_editor_text("")
            if action == "app.model.cycleForward":
                return HOTKEY_MODEL_CYCLE_NEXT
            if action == "app.model.cycleBackward":
                return HOTKEY_MODEL_CYCLE_PREV
            if action == "app.model.select":
                return HOTKEY_MODEL_SELECT
            if action == "app.thinking.cycle":
                return HOTKEY_THINKING_CYCLE
            if action == "app.tools.expand":
                return HOTKEY_TOGGLE_TOOLS
            if action == "app.thinking.toggle":
                return HOTKEY_TOGGLE_THINKING
            if action == "app.editor.external":
                edited = self._run_configured_external_editor(
                    self._custom_editor_text()
                )
                if edited is not None:
                    self._set_custom_editor_text(edited)
                self.paint()
                return None
            if action == "app.message.followUp":
                text = self._custom_editor_text()
                if text.strip():
                    self.pending_messages.enqueue_follow_up(text)
                self.input_editor.clear_initial_text()
                self._set_custom_editor_text("")
                return None
            if action == "app.message.dequeue":
                self.pending_messages.restore_pending_to_editor()
                return None
            if action == "app.clipboard.pasteImage":
                self.clipboard_images.paste_clipboard_image()
                return None
            if action == "app.interrupt":
                self.input_editor.clear_initial_text()
                self._set_custom_editor_text("")
                return None
            if action == "app.exit":
                if not self._custom_editor_text():
                    self._custom_editor_exit_requested = True
                    return ""
                return None
            if action.startswith("app.extensionShortcut:"):
                key_name = action.removeprefix("app.extensionShortcut:")
                return f"{HOTKEY_EXTENSION_SHORTCUT_PREFIX}{key_name}"
            return None
        if self._custom_editor_submitted is not None:
            submitted = self._custom_editor_submitted
            self._custom_editor_submitted = None
            self.input_editor.clear_initial_text()
            self._set_custom_editor_text("")
            return submitted
        self.input_editor.set_buffer(self._custom_editor_text())
        self.paint()
        return None

    def run_tree_selector(
        self,
        *,
        build_rows: Callable[[str], Sequence["TreeSelectorRow"]],
        filter_modes: Sequence[str],
        initial_filter: str,
        on_label_toggle: Callable[[str], None],
    ) -> TreeSelectorClose:
        """Drive the interactive ``/tree`` selector; return its close result.

        ``build_rows(filter_mode)`` returns the visible rows for a filter;
        up/down move the highlight, ``Ctrl-O`` cycles the filter mode, ``L``
        (Shift-L) toggles a label on the highlighted entry via
        ``on_label_toggle``, ``Enter`` selects the highlighted entry, and
        ``Esc``/``Ctrl-C``/``Ctrl-D``/EOF cancel. The close result carries the
        chosen entry id (``None`` on cancel) and the filter mode the overlay
        closed with. Runs no provider turn and no model-visible tool call; the
        caller applies the chosen entry's selection semantics afterward.
        """

        selector = TreeSelectorComponent(
            self._overlays,
            self._paint_lock,
            self.paint,
            build_rows=build_rows,
            filter_modes=filter_modes,
            on_label_toggle=on_label_toggle,
        )
        selector.open(initial_filter)
        fd = self.input_stream.fileno()
        with self._driver.raw_mode():
            while True:
                key = self._read_key_polling_resize(fd)
                if key == "paste":
                    self.input_editor.consume_paste()
                    continue
                closed = selector.handle_key(key)
                if closed is not None:
                    return closed

    # -- custom extension overlay (ctx.ui.custom) ---------------------------

    def run_custom_component(
        self,
        factory: CustomComponentFactory,
        options: CustomComponentOptions | None = None,
    ) -> object:
        """Drive a trusted extension custom component; return its result.

        `factory(done)` builds a component exposing `render(width) -> list[str]`
        and `handle_input(key) -> None`; the component calls `done(result)` to
        finish. The driver paints the component's lines as a full-screen inline
        overlay and routes decoded keys to it until it finishes (or the input
        stream ends / errors, which finishes with ``None``). Runs no provider
        turn. Returns the result passed to `done`, or ``None`` if cancelled.

        ``options`` accepts Pi-shaped custom overlay fields. Pipy's bounded TUI
        currently renders overlay and non-overlay custom components through the
        same inline overlay path, but it honors overlay width hints and handle
        callbacks for API parity.
        """

        runner = CustomComponentRunner(self._overlays, self.paint)
        runner.create(factory, options)
        raw_mode_acquired = False
        try:
            runner.begin()
            fd = self.input_stream.fileno()
            self._driver.enter_raw_mode()
            raw_mode_acquired = True
            while not runner.finished:
                if runner.handle_key(self._read_key_polling_resize(fd)):
                    break
        finally:
            result = runner.dispose()
            if raw_mode_acquired:
                self._driver.restore_terminal_mode()
        return result

    def run_extension_select(self, title: str, options: Sequence[str]) -> str | None:
        """Run a Pi-shaped extension selector over string options."""

        choices = tuple(str(option) for option in options if str(option))
        if not choices:
            return None
        result = self.run_custom_component(
            lambda done: ExtensionSelectComponent(str(title), choices, done)
        )
        return result if isinstance(result, str) else None

    def run_extension_input(
        self, title: str, placeholder: str | None = None
    ) -> str | None:
        """Run a Pi-shaped extension text input overlay."""

        result = self.run_custom_component(
            lambda done: ExtensionInputComponent(str(title), placeholder, done)
        )
        return result if isinstance(result, str) else None

    def run_extension_editor(
        self, title: str, prefill: str | None = None
    ) -> str | None:
        """Run a Pi-shaped extension multi-line editor overlay."""

        external_editor = self._extension_external_editor_callback()
        external_editor_keys = resolved_key_specs(
            "app.editor.external", self.keybindings_manager
        )
        result = self.run_custom_component(
            lambda done: ExtensionEditorComponent(
                str(title), prefill, done, external_editor, external_editor_keys
            )
        )
        return result if isinstance(result, str) else None

    def _extension_external_editor_callback(
        self,
    ) -> Callable[[str], str | None] | None:
        if not self._external_editor_command():
            return None

        def run_external_editor(current_text: str) -> str | None:
            return self._run_configured_external_editor(current_text)

        return run_external_editor

    @staticmethod
    def _external_editor_command() -> str | None:
        return os.environ.get("VISUAL") or os.environ.get("EDITOR")

    def _run_configured_external_editor(self, current_text: str) -> str | None:
        editor_cmd = self._external_editor_command()
        if not editor_cmd:
            return None
        return self._run_extension_external_editor(editor_cmd, current_text)

    def _matches_keybinding(self, key: str, action: str) -> bool:
        return matches_key_specs(
            key,
            resolved_key_specs(action, self.keybindings_manager),
        )

    def _run_extension_external_editor(
        self, editor_cmd: str, current_text: str
    ) -> str | None:
        try:
            argv = shlex.split(editor_cmd)
        except ValueError:
            return None
        if not argv:
            return None

        path = ""
        try:
            fd, path = tempfile.mkstemp(
                prefix="pipy-extension-editor-", suffix=".md", text=True
            )
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(current_text)

            updated: str | None = None
            launched = False
            try:
                with self.external_io_suspension():
                    self._driver.write(
                        f"Launching external editor: {editor_cmd}\n"
                        "Pipy will resume when the editor exits.\n"
                    )
                    launched = True
                    completed = subprocess.run(
                        [*argv, path],
                        stdin=self.input_stream,
                        stdout=self.terminal_stream,
                        stderr=self.terminal_stream,
                        check=False,
                    )
                    if completed.returncode == 0:
                        try:
                            updated = Path(path).read_text(encoding="utf-8")
                        except (OSError, UnicodeError):
                            updated = None
            except (OSError, termios.error, ValueError):
                # A failed cooked-mode handoff occurs before ``launched`` and
                # must not start a foreign terminal consumer. If the editor did
                # run, its successful file is read *inside* the scope so a raw-
                # mode resume failure cannot discard the completed edit. The
                # driver keeps that failure suspended for authoritative close
                # recovery rather than claiming a physically false raw owner.
                if not launched:
                    return None
            return None if updated is None else updated.removesuffix("\n")
        except OSError:
            return None
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def run_extension_confirm(self, title: str, message: str) -> bool:
        """Run a Pi-shaped extension confirmation dialog."""

        result = self.run_custom_component(
            lambda done: ExtensionConfirmComponent(str(title), str(message), done)
        )
        return result == "Yes"

    def clear_extension_chrome(
        self,
        *,
        retirement_scope: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> None:
        """Retire accepted extension UI through its ordered owner."""

        self._chrome.generation.retire_generation(retirement_scope=retirement_scope)

    def reconcile_extension_chrome(
        self,
        snapshot: ExtensionChromeSnapshot,
        *,
        retirement_scope: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> dict[int, Callable[[], None]]:
        """Replace accepted extension UI through its ordered owner."""

        return self._chrome.generation.reconcile_generation(
            snapshot, retirement_scope=retirement_scope
        )

    # -- interactive session picker (/resume + -r overlay) ------------------

    def run_session_picker(
        self,
        *,
        project_sessions: Sequence[SessionListEntry],
        all_sessions: Sequence[SessionListEntry],
        current_path: Path | None = None,
        on_rename: Callable[[Path, str], None] | None = None,
        on_delete: Callable[[Path], tuple[bool, str]] | None = None,
        now: float | None = None,
    ) -> Path | None:
        """Drive the interactive session picker; return a chosen session file.

        Typing searches; ``↑/↓`` move; ``Enter`` opens the highlighted session;
        ``Tab`` toggles current-project / all-projects scope; ``Ctrl+P`` toggles
        the file-path column; ``Ctrl+S`` cycles the sort; ``Ctrl+N`` filters to
        named sessions; ``Ctrl+R`` renames and ``Ctrl+X`` deletes (each with an
        in-overlay confirmation/edit); ``Esc``/``Ctrl+C``/``Ctrl+D``/EOF cancel.
        Runs no provider turn and no model-visible tool call.
        """

        picker = SessionPickerComponent(
            self._overlays,
            self._paint_lock,
            self.paint,
            on_rename=on_rename,
            on_delete=on_delete,
            consume_paste=self.input_editor.consume_paste,
        )
        picker.open(
            project_sessions=project_sessions,
            all_sessions=all_sessions,
            current_path=current_path,
            now=now,
        )
        fd = self.input_stream.fileno()
        with self._driver.raw_mode():
            while True:
                key = self._read_key_polling_resize(fd)
                closed = picker.handle_key(key)
                if closed is not None:
                    return closed.path

    def close(self) -> None:
        # Actual terminal shutdown is the fail-safe boundary: abandon any raw
        # owner a broken earlier path failed to release. Overlay/read scopes
        # continue to use balanced restoration after successful acquisition.
        self._driver.force_restore_terminal_mode()
        self._driver.remove_resize_handler()
        if self._closed:
            return
        self._closed = True
        out: list[str] = []
        # Move below the live region so the next shell prompt does not
        # overwrite the footer, then restore the cursor.
        if self._live_height > 0:
            lines_below = (self._live_height - 1) - self._live_input_row
            if lines_below > 0:
                out.append(f"\x1b[{lines_below}B")
            out.append("\r")
        out.append("\x1b[?25h\n")
        self._driver.write("".join(out))

    def set_footer_text(self, text: str) -> None:
        lines = text.splitlines()
        if len(lines) >= 2:
            self.footer_lines = (lines[0], lines[1])
        elif lines:
            self.footer_lines = (lines[0], "")
        else:
            self.footer_lines = ("", "")
        self.paint()

    # -- transcript facade ---------------------------------------------------
    #
    # Committed history, the live stream buffers, and the Ctrl+O/Ctrl+T view
    # flags live on ``self._transcript`` (ui/components/transcript.py). Both
    # renderer adapters commit straight to the component (built by
    # :meth:`create_tool_loop_renderer` / :meth:`custom_entry_render_target`);
    # the delegates below remain only for the shell's own callers — the local
    # `!`/`!!` shell blocks, the view hotkeys, and the characterization tests
    # that drive frames through the public surface.

    @property
    def tools_expanded(self) -> bool:
        return self._transcript.tools_expanded

    @property
    def thinking_hidden(self) -> bool:
        return self._transcript.thinking_hidden

    def submit_user_message(self, text: str) -> None:
        self._transcript.submit_user_message(text)

    def begin_assistant_turn(self) -> None:
        self._transcript.begin_assistant_turn()

    def set_working(self, text: str) -> None:
        self._transcript.set_working(text)

    def append_assistant(self, chunk: str) -> None:
        self._transcript.append_assistant(chunk)

    def settle_assistant(self, final_text: str = "") -> None:
        self._transcript.settle_assistant(final_text)

    def append_reasoning(self, chunk: str) -> None:
        self._transcript.append_reasoning(chunk)

    def set_thinking_hidden(self, hidden: bool) -> None:
        self._transcript.set_thinking_hidden(hidden)

    def set_tools_expanded(self, expanded: bool) -> None:
        self._transcript.set_tools_expanded(expanded)

    def add_notice(self, text: str) -> None:
        self._transcript.add_notice(text)

    def custom_entry_render_target(self) -> CustomEntryTerminalTarget:
        """Bundle the transcript and live render inputs for custom entries.

        The custom-entry renderer component commits rendered rows straight to
        the transcript; the driver's width and the styling stream stay private
        to this shell and cross as injected values/callables.
        """

        return CustomEntryTerminalTarget(
            transcript=self._transcript,
            terminal_stream=self.terminal_stream,
            frame_width=lambda: self._driver.size()[0],
        )

    def create_tool_loop_renderer(
        self,
        *,
        tool_renderers: Mapping[str, ExtensionTool] | None = None,
        render_details_sink: ToolRenderDetailsSink | None = None,
    ) -> TuiToolLoopRenderer:
        """Build the agent-event renderer bound to this shell's owners.

        The renderer commits straight to the transcript component, reads
        spinner/working chrome off the shared chrome record, and receives the
        driver's width and the styling stream as injected values — it never
        holds the shell itself.
        """

        return TuiToolLoopRenderer(
            transcript=self._transcript,
            chrome=self._chrome.record,
            terminal_stream=self.terminal_stream,
            frame_width=lambda: self._driver.size()[0],
            tool_renderers=tool_renderers,
            render_details_sink=render_details_sink,
        )

    def add_tool_call(self, header: str) -> None:
        self._transcript.add_tool_call(header)

    def append_tool_output(self, chunk: str) -> None:
        self._transcript.append_tool_output(chunk)

    def add_tool_result(
        self,
        *,
        lines: Iterable[str],
        is_error: bool,
        duration_seconds: float | None = None,
    ) -> None:
        self._transcript.add_tool_result(
            lines=lines, is_error=is_error, duration_seconds=duration_seconds
        )

    def rerender_custom_messages(self) -> None:
        self._transcript.rerender_custom_messages()

    def _force_full_redraw(self) -> None:
        # Deferred (unflushed) write so the clear-screen coalesces with the
        # flush of the immediately-following paint(), matching the buffered
        # pre-extraction behavior (no separate flush, no full-redraw flash).
        if not self._driver.write_deferred("\x1b[2J\x1b[H"):
            return
        self._painted_block_count = 0
        self._live_height = 0
        self._live_input_row = 0
        self.paint()

    def render_lines(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        pad: bool = True,
    ) -> list[str]:
        return [
            line.text for line in self._frame_lines(width=width, height=height, pad=pad)
        ]

    def _frame_lines(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        pad: bool = True,
    ) -> list[_FrameLine]:
        resolved_width, resolved_height = self._driver.size(width=width, height=height)
        snapshot = self._frame_snapshot(
            width=resolved_width,
            height=resolved_height,
            include_session_picker=False,
        )
        return list(render_full_frame(snapshot, pad=pad))

    def request_extension_chrome_render(self) -> None:
        """Request a chrome repaint, coalescing calls made during render()."""

        if self._closed:
            return
        with self._paint_lock:
            if self._painting:
                self._paint_requested_during_paint = True
                return
        self.paint()

    def paint(self) -> None:
        if self._closed:
            return
        with self._paint_lock:
            if self._painting:
                self._paint_requested_during_paint = True
                return
            self._painting = True
            try:
                self._paint_locked()
                if self._paint_requested_during_paint and not self._closed:
                    self._paint_requested_during_paint = False
                    self._paint_locked()
            finally:
                self._painting = False
                self._paint_requested_during_paint = False

    def _paint_locked(self) -> None:
        width, height = self._driver.size()
        snapshot = self._frame_snapshot(
            width=width, height=height, include_session_picker=True
        )
        plan = build_paint_plan(
            snapshot,
            PaintState(
                painted_block_count=self._painted_block_count,
                live_height=self._live_height,
                live_input_row=self._live_input_row,
            ),
            chrome_style_for(self.terminal_stream),
        )
        # Preserve the established failed-write contract: bookkeeping describes
        # the attempted frame even when TerminalDriver.write returns False.
        self._painted_block_count = plan.painted_block_count
        self._live_height = plan.live_height
        self._live_input_row = plan.live_input_row
        self._last_painted_size = plan.painted_size
        self._driver.write_frame(
            prior_live_height=plan.prior_live_height,
            prior_live_input_row=plan.prior_live_input_row,
            committed_rows=tuple(
                (row.text, row.erase_tail) for row in plan.committed_rows
            ),
            live_rows=tuple((row.text, row.erase_tail) for row in plan.live_rows),
            cursor_lines_up=plan.cursor_lines_up,
            cursor_col=plan.cursor_col,
            cursor_visible=plan.cursor_visible,
        )

    def _live_region_lines(self, *, width: int, height: int) -> list[_FrameLine]:
        snapshot = self._frame_snapshot(
            width=width, height=height, include_session_picker=True
        )
        return list(render_live_region(snapshot))

    def _frame_snapshot(
        self, *, width: int, height: int, include_session_picker: bool
    ) -> FrameSnapshot:
        """Resolve effectful sources, then publish one immutable render input.

        Extension/custom-component callbacks and mutable owner reads remain in
        this facade under its paint lock. The returned renderer snapshot holds
        only copied tuples, strings, integers, and frozen frame values.
        """

        overlay = self._active_overlay_region_lines(
            width=width,
            height=height,
            include_session_picker=include_session_picker,
        )
        if overlay is None:
            popup, pending, chrome, custom_rows = self._standard_frame_inputs(
                width=width, height=height
            )
        else:
            # Overlay paint returned before consulting ordinary chrome/input in
            # the pre-slice path. Do not execute hidden extension components.
            popup, pending, chrome, custom_rows = (), (), ChromeSnapshot(), None
        history = tuple(
            FrameBlock(kind, tuple(lines))
            for kind, lines in self._transcript.history_blocks
        )
        return FrameSnapshot(
            width=width,
            height=height,
            history=history,
            assistant_text=self._transcript.assistant_text,
            reasoning_text=self._transcript.reasoning_text,
            tool_output_text=self._transcript.tool_output_text,
            working_text=self._transcript.working_text,
            thinking_hidden=self._transcript.thinking_hidden,
            hidden_thinking_label=self._transcript.hidden_thinking_label,
            tools_expanded=self._transcript.tools_expanded,
            input=self.input_editor.snapshot(custom_rows),
            popup=tuple(popup),
            pending=tuple(pending),
            chrome=chrome,
            overlay=None if overlay is None else tuple(overlay),
            cursor_visible=self._overlays.active is None,
        )

    def _standard_frame_inputs(
        self, *, width: int, height: int
    ) -> tuple[
        tuple[_FrameLine, ...],
        tuple[_FrameLine, ...],
        ChromeSnapshot,
        tuple[_ResolvedCustomEditorLine, ...] | None,
    ]:
        """Resolve effectful ordinary-frame regions before freezing them."""

        popup = tuple(
            self._autocomplete.popup_menu_frame_lines(
                width=width, max_rows=max(1, height - 7)
            )
        )
        pending = tuple(self.pending_messages.region_lines(width))
        status = tuple(self._chrome.component.status_lines(width))
        header = tuple(self._chrome.component.header_lines(width))
        above = tuple(self._chrome.component.widget_lines("above_editor", width))
        below = tuple(self._chrome.component.widget_lines("below_editor", width))
        custom_footer = self._chrome.footer.lines(width)
        footer = (
            tuple(custom_footer)
            if custom_footer is not None
            else (
                _FrameLine(self._clip(self.footer_lines[0], width), "footer"),
                _FrameLine(self._clip(self.footer_lines[1], width), "footer"),
            )
        )
        custom_rows = None
        if self._custom_editor_active:
            custom_rows = tuple(self._custom_editor_frame_lines(width))
        return (
            popup,
            pending,
            ChromeSnapshot(header, above, below, footer, status),
            custom_rows,
        )

    def _active_overlay_region_lines(
        self,
        *,
        width: int,
        height: int,
        include_session_picker: bool = True,
    ) -> list[_FrameLine] | None:
        """Render the active overlay for the requested façade projection."""

        active = self._overlays.active
        if active == "custom":
            return custom_overlay_region_lines(
                self._overlays, width=width, height=height
            )
        if active in {"settings", "project_trust"}:
            return settings_dialog_region_lines(
                self._overlays,
                width=width,
                height=height,
                footer_lines=self.footer_lines,
            )
        if active == "session_picker" and include_session_picker:
            return session_picker_region_lines(
                self._overlays,
                width=width,
                height=height,
                footer_lines=self.footer_lines,
            )
        if active == "tree":
            return tree_selector_region_lines(
                self._overlays,
                width=width,
                height=height,
                footer_lines=self.footer_lines,
            )
        if active == "scoped_models":
            return scoped_models_region_lines(
                self._overlays,
                width=width,
                height=height,
                footer_lines=self.footer_lines,
            )
        if active == "model":
            return model_selector_region_lines(
                self._overlays,
                width=width,
                height=height,
                footer_lines=self.footer_lines,
            )
        return None

    def _styled_line(self, line: _FrameLine, *, style: ChromeStyle, width: int) -> str:
        return render_styled_line(line, style, width)

    def _startup_blocks(self) -> list[HistoryBlock]:
        raw_blocks: list[tuple[str, tuple[str, ...]]] = [
            ("normal", ("",)),
            ("title", (f" pipy v{pipy_version_label()}",)),
            (
                "controls",
                (
                    " escape interrupt · ctrl+c/ctrl+d clear/exit · ↑↓ history · "
                    "/ commands · @ files · ! bash · tab paths",
                    " shift+tab thinking · ctrl+p model · ctrl+o tool output · "
                    "ctrl+t thinking fold · ctrl+v paste image · drop files to attach",
                ),
            ),
            (
                "dim",
                (" Type /hotkeys for the full key reference and loaded resources.",),
            ),
            ("normal", ("",)),
            (
                "dim",
                (
                    " Pipy can explain its own features and look up its docs. "
                    "Ask it how to use or extend pipy.",
                ),
            ),
            ("normal", ("", "")),
        ]
        blocks: list[HistoryBlock] = [
            HistoryBlockTuple(kind, lines) for kind, lines in raw_blocks
        ]
        context = discover_loaded_resource_names(
            self.cwd,
            "context",
            include_workspace_defaults=self.include_workspace_defaults,
        )
        if context:
            blocks.append(
                HistoryBlockTuple(
                    "section",
                    ("[Context]",),
                    None,
                )
            )
            blocks.append(
                HistoryBlockTuple(
                    "resource",
                    (
                        f"  {', '.join(context)}",
                        "",
                    ),
                    None,
                )
            )
        skills = discover_loaded_resource_names(
            self.cwd,
            "skills",
            include_workspace_defaults=self.include_workspace_defaults,
        )
        if skills:
            blocks.append(
                HistoryBlockTuple(
                    "section",
                    ("[Skills]",),
                    None,
                )
            )
            blocks.append(
                HistoryBlockTuple(
                    "resource",
                    (
                        f"  {', '.join(skills)}",
                        "",
                        "",
                    ),
                    None,
                )
            )
        return blocks

    def _block_frame_lines(
        self,
        kind: str,
        block_lines: Iterable[str],
        *,
        width: int | None = None,
    ) -> list[_FrameLine]:
        resolved_width = width or self._driver.size()[0]
        return list(
            render_block_lines(
                FrameBlock(kind=kind, lines=tuple(block_lines)), resolved_width
            )
        )

    def _custom_editor_frame_lines(
        self, width: int, *, max_rows: int | None = None
    ) -> list[_ResolvedCustomEditorLine]:
        component = self._custom_editor_component
        raw: object = None
        if component is not None:
            renderer = getattr(component, "render", None)
            if callable(renderer):
                try:
                    raw = renderer(width)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException:  # noqa: BLE001 - fail-soft render
                    raw = ["(custom editor render error)"]
        if raw is None:
            raw = [
                self.input_editor.display_input_text(self._custom_editor_text()) or " "
            ]
        if isinstance(raw, str):
            raw_lines = raw.splitlines() or [raw]
        elif isinstance(raw, Iterable):
            raw_lines = [str(line) for line in raw]
        else:
            raw_lines = [str(raw)]
        if max_rows is not None and max_rows > 0 and len(raw_lines) > max_rows:
            raw_lines = raw_lines[-max_rows:]
        lines = [
            self._clip(_sanitize_custom_overlay_text(line or " "), width)
            for line in raw_lines
        ]
        if not lines:
            lines = [" "]
        meta = {"cursor_col": min(len(lines[-1]), max(0, width - 1))}
        return [
            _ResolvedCustomEditorLine(
                line, "input", meta if index == len(lines) - 1 else None
            )
            for index, line in enumerate(lines)
        ]

    @staticmethod
    def _input_index(lines: list[_FrameLine]) -> int:
        return render_input_index(tuple(lines))

    @staticmethod
    def _clip(text: str, width: int) -> str:
        return render_clip_text(text, width)

    @staticmethod
    def _pad(text: str, width: int) -> str:
        return render_pad_text(text, width)

    def _read_driver_key(self, key: str | None) -> str | None:
        """Copy a decoded paste's body from the driver into the UI buffer.

        The driver decodes keys over its owned fd and hands a bracketed-paste
        body back through :meth:`TerminalDriver.consume_paste`; the durable
        ``_pending_paste`` buffer that the key handlers consume stays owned by
        the UI, so every decode call site funnels through here.
        """

        if key == "paste":
            self.input_editor.stage_paste(self._driver.consume_paste())
        return key

    def _read_key_polling_resize(self, fd: int) -> str | None:
        """Block for the next key, repainting when the terminal is resized.

        Returns the decoded key, or ``None`` on EOF. While waiting it polls the
        live terminal size every ``_RESIZE_POLL_SECONDS`` and repaints the frame
        if it changed (or a SIGWINCH flagged a pending resize), so the inline
        layout stays coherent without entering the alternate screen. The
        fd-level read and key decoding are delegated to the terminal driver,
        which owns the input fd.
        """

        while True:
            self._chrome.footer.poll_branch()
            self._poll_resize_repaint()
            if self._driver.has_pending_input():
                return self._read_driver_key(self._driver.read_key(fd))
            readable, _, _ = select.select([fd], [], [], _RESIZE_POLL_SECONDS)
            if fd not in readable:
                continue
            return self._read_driver_key(self._driver.read_key(fd))

    def _poll_resize_repaint(self) -> bool:
        pending = self._driver.take_resize_pending()
        if pending or self._driver.size() != self._last_painted_size:
            self._repaint_after_resize()
            return True
        return False

    def _repaint_after_resize(self) -> None:
        """Repaint after a terminal resize without relying on stale geometry.

        A width change can reflow the previously drawn frame (e.g. a
        full-width separator wraps when the terminal shrinks), so the cached
        physical live-height/input-row no longer describe the screen and the
        normal relative-cursor erase would leave stale rows. Instead, clear the
        visible screen, home the cursor, and redraw the full frame
        (committed history + live region) fresh at the new size. This is
        drift-independent and stays inline — it never enters the alternate
        screen, and committed history stays in native scrollback above
        (re-rendered at the new width). Resizes are infrequent, so the redraw
        cost is acceptable for the coherence guarantee.
        """

        with self._paint_lock:
            if self._closed:
                return
            # Clear the visible screen and home the cursor (no \x1b[3J, so
            # the terminal's scrollback is preserved). Then force a full
            # redraw by resetting the committed-block and live-region
            # bookkeeping so _paint_locked re-emits every history block. The
            # clear is a deferred (unflushed) write so it coalesces with the
            # flush of the following _paint_locked(), matching the buffered
            # pre-extraction behavior (no separate flush, no resize flash).
            if not self._driver.write_deferred("\x1b[2J\x1b[H"):
                return
            self._painted_block_count = 0
            self._live_height = 0
            self._live_input_row = 0
            self._paint_locked()

    @contextmanager
    def external_io_suspension(self) -> Iterator[None]:
        """Scope one blocking foreign terminal consumer in cooked mode.

        Used by configured editors and ``/login`` so inherited terminal I/O is
        cooked and the inline frame is removed while the foreign flow owns it.
        Entry publishes suspension before mutating the live-region projection,
        so a failed cooked-mode handoff launches no consumer and leaves the
        frame intact. The paired ``finally`` resume is unavoidable for normal,
        exceptional, and nested exits; only the outermost scope repaints below
        the foreign output. A failed raw resume remains published in the driver
        for authoritative :meth:`close` recovery and is surfaced to the caller.
        No prompts, URLs, credentials, or edited text touch the session archive.
        """

        with self._paint_lock:
            self._driver.suspend_terminal_mode()
            output: list[str] = []
            if self._live_height > 0:
                if self._live_input_row > 0:
                    output.append(f"\x1b[{self._live_input_row}A")
                output.append("\r\x1b[J")
            output.append("\x1b[?25h")
            self._driver.write("".join(output))
            self._live_height = 0
            self._live_input_row = 0
        try:
            yield
        finally:
            with self._paint_lock:
                repaint = self._driver.resume_terminal_mode()
            if repaint:
                self.paint()

    def add_extension_autocomplete_provider(self, factory: object) -> None:
        self._autocomplete.add_extension_provider(factory)

    @staticmethod
    def _submitted_text_is_local_command(text: str) -> bool:
        """True when a mid-turn submission is a local command, not a prompt.

        Matches the session loop's local-command boundary: any line whose first
        non-space character is ``/`` (a slash command — known ones dispatch,
        unknown ones are reported, neither reaches the provider) or ``!`` (a
        bash shortcut). Such a line submitted with Enter mid-turn runs locally
        instead of being queued/steered to the model. Ordinary prose (which is
        what steering/follow-up actually carries) does not match.
        """

        stripped = text.strip()
        return stripped.startswith("/") or stripped.startswith("!")

    def _is_bash_mode(self) -> bool:
        """True when the editor buffer is a ``!``/``!!`` local-shell shortcut.

        Mirrors Pi's ``isBashMode`` editor border: while the first non-space
        character of the input is ``!`` the input frame paints a distinct
        bash-mode affordance (Enter runs a shell command, not a provider turn).
        """

        return self.input_editor.text.lstrip().startswith("!")


def run_project_trust_selector(
    ui: ToolLoopTerminalUi,
    *,
    cwd: Path,
    options: Sequence[ProjectTrustOption],
    saved_decision: ProjectTrustEntry | None = None,
    current_trusted: bool | None = None,
    startup: bool = False,
) -> ProjectTrustOption | None:
    """Drive the shared startup/``/trust`` selector in a live product TUI."""

    canonical_cwd = cwd.expanduser().resolve()
    display_cwd = sanitize_label_text(str(canonical_cwd))
    rows = [
        SettingsRow(label=display_cwd, kind="status"),
    ]
    if startup:
        rows.extend(
            (
                SettingsRow(
                    label="Trust enables project settings/resources and packages.",
                    kind="status",
                ),
                SettingsRow(
                    label="Trusted projects may execute project extensions.",
                    kind="status",
                ),
            )
        )
    else:
        if saved_decision is None:
            saved_label = "none"
        else:
            decision_label = "trusted" if saved_decision.decision else "untrusted"
            display_saved_path = sanitize_label_text(str(saved_decision.path))
            if saved_decision.path != canonical_cwd:
                saved_label = f"{decision_label} (inherited from {display_saved_path})"
            else:
                saved_label = f"{decision_label} ({display_saved_path})"
        rows.extend(
            (
                SettingsRow(label=f"Saved decision: {saved_label}", kind="status"),
                SettingsRow(
                    label=(
                        "Current session: "
                        f"{'trusted' if current_trusted else 'untrusted'}"
                    ),
                    kind="status",
                ),
            )
        )
    action_to_option: dict[str, ProjectTrustOption] = {}
    saved_row_index: int | None = None
    for index, option in enumerate(options):
        action = f"trust-option-{index}"
        action_to_option[action] = option
        rows.append(
            SettingsRow(
                label=sanitize_label_text(option.label),
                kind="action",
                action=action,
            )
        )
        if (
            saved_decision is not None
            and option.saved_path == saved_decision.path
            and option.trusted == saved_decision.decision
        ):
            saved_row_index = len(rows) - 1
    chosen = ui.run_settings_dialog(
        rows,
        on_local_action=lambda _action: rows,
        exit_actions=frozenset(action_to_option),
        current_index=saved_row_index,
        title="Trust project folder?" if startup else "Project trust",
        overlay_kind="project_trust",
    )
    return action_to_option.get(chosen) if chosen is not None else None


def run_startup_project_trust_selector(
    *, cwd: Path, options: Sequence[ProjectTrustOption]
) -> ProjectTrustOption | None:
    """Open the pre-runtime project-trust selector on a real TTY."""

    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return None
    except (ValueError, OSError):
        return None
    ui = ToolLoopTerminalUi(
        input_stream=sys.stdin,
        terminal_stream=sys.stdout,
        cwd=cwd,
    )
    try:
        return run_project_trust_selector(
            ui,
            cwd=cwd,
            options=options,
            startup=True,
        )
    finally:
        ui.close()


def run_startup_session_picker(
    *,
    project_sessions: Sequence[SessionListEntry],
    all_sessions: Sequence[SessionListEntry],
    current_cwd: str,
) -> Path | None:
    """Open the ``-r`` startup session picker on a real TTY.

    Constructs a standalone inline picker bound to ``sys.stdin``/``sys.stdout``
    and returns the chosen native session file (or ``None`` when there is no TTY
    or the user cancels). Rename/delete actions run through the same native
    boundaries as the in-session ``/resume`` picker; no provider turn runs.
    """

    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return None
    except (ValueError, OSError):
        return None

    from pipy_harness.native.session_tree_commands import delete_native_session

    def on_rename(path: Path, new_name: str) -> None:
        NativeSessionTree.open(path).append_session_info(new_name)

    def on_delete(path: Path) -> tuple[bool, str]:
        return delete_native_session(path)

    ui = ToolLoopTerminalUi(
        input_stream=sys.stdin,
        terminal_stream=sys.stdout,
        cwd=Path(current_cwd),
    )
    try:
        return ui.run_session_picker(
            project_sessions=project_sessions,
            all_sessions=all_sessions,
            current_path=None,
            on_rename=on_rename,
            on_delete=on_delete,
        )
    finally:
        ui.close()

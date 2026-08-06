"""Pipy-owned terminal UI shell for native tool-loop REPL sessions.

The line-oriented renderer prints prompt, loader, assistant text, tool blocks,
and footer as independent lines. This module is the stateful/effectful façade
for a small inline terminal frame; ``native.frame_renderer`` composes immutable
snapshots of its history, transient output, input, overlays, and chrome into
full/live rows and deterministic terminal paint plans.
"""

from __future__ import annotations

import os
import sys
from collections.abc import (
    Callable,
    Iterator,
    Sequence,
)
from contextlib import contextmanager
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from typing import Any, TextIO, cast

from pipy_harness.native.chrome import (
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
from pipy_harness.native.keybindings import (
    KeybindingsManager,
)
from pipy_harness.native.overlay_state import OverlayState
from pipy_harness.native.repl_input import (
    DEFAULT_REPL_COMMAND_DESCRIPTIONS,
)
from pipy_harness.native.terminal_driver import (
    TerminalDriver,
)
from pipy_harness.native.themes import NativeThemeStore, select_theme
from pipy_harness.native.ui.autocomplete import (
    AutocompleteComponent,
    CommandSurface,
)
from pipy_harness.native.ui.chrome_handoff import (
    ChromeAcceptanceResult,
    ChromeHandoffOperation,
    ExtensionChromeRouter,
)
from pipy_harness.native.ui.chrome_handoff import (
    _ExtensionChromeTuiHandle as _ChromeTuiHandle,
)
from pipy_harness.native.ui.clipboard_images import ClipboardConfig, ClipboardImages
from pipy_harness.native.ui.components.custom_editor import (
    CustomEditorEffects,
    CustomEditorOwner,
    CustomEditorState,
)
from pipy_harness.native.ui.components.extension_prompts import (
    ExtensionExternalEditor,
)
from pipy_harness.native.ui.components.footer import FooterComponent
from pipy_harness.native.ui.components.input_editor import (
    EditingAction,
    EditingKeyContext,
    EditingMode,
    InputEditor,
    LineEditingEffects,
    apply_editing_key,
)
from pipy_harness.native.ui.components.transcript import (
    HistoryBlock,
    HistoryBlockTuple,
    TranscriptComponent,
)
from pipy_harness.native.ui.composition import TerminalComponents
from pipy_harness.native.ui.extension_chrome import ExtensionChromeComponent
from pipy_harness.native.ui.extension_generation import (
    ExtensionChromeOwners,
    build_extension_chrome_owners,
)
from pipy_harness.native.ui.key_specs import matches_key_specs, resolved_key_specs
from pipy_harness.native.ui.modal_driver import TerminalModalDriver
from pipy_harness.native.ui.pending_messages import PendingMessages
from pipy_harness.native.ui.screen import (
    FrameRegionSources,
    FrameSources,
    Screen,
)
from pipy_harness.native.ui.terminal_input_listeners import TerminalInputListeners

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
# Outcomes of the active-turn watcher / mid-turn editor.
TURN_SETTLED = "settled"  # the provider turn finished on its own
TURN_ABORTED = "aborted"  # Escape/Ctrl-C cancelled the turn
TURN_STEERED = "steered"  # a steering message interrupted the turn
TURN_LOCAL_COMMAND = "local_command"  # a /… or !… command interrupted the turn


def _active_editing_mode(accept_queue: bool, accept_commands: bool) -> EditingMode:
    if accept_queue:
        return EditingMode.ACTIVE_QUEUE
    if accept_commands:
        return EditingMode.ACTIVE_COMMAND
    return EditingMode.ACTIVE_WATCH


class _LiveExtensionUiDriver:
    """Live `ExtensionUiDriver` backed by explicit concrete terminal owners."""

    def __init__(
        self,
        chrome: ExtensionChromeOwners,
        modals: TerminalModalDriver,
        transcript: TranscriptComponent,
        autocomplete: AutocompleteComponent,
        custom_editor: CustomEditorOwner,
        input_editor: InputEditor,
    ) -> None:
        self._chrome = chrome
        self._modals = modals
        self._transcript = transcript
        self._autocomplete = autocomplete
        self._custom_editor = custom_editor
        self._input_editor = input_editor
        # Ownership of chrome handoff is a transaction with no terminal access.
        # This binding applies each accepted event to the concrete owner graph.
        self._router = ExtensionChromeRouter(self._deliver_chrome_event)

    # -- chrome-transaction delegation -----------------------------------
    # `_LiveExtensionUiDriver` keeps its whole public surface: extensions, the
    # session and the generation proxy all reach the transaction through it.

    def new_candidate_sink(self) -> ExtensionChromeSink:
        return self._router.new_candidate_sink()

    def startup_chrome_sink(self) -> ExtensionChromeSink:
        return self._router.startup_chrome_sink()

    def prepare_candidate(
        self, prepared: ExtensionChromePrepareInput
    ) -> ExtensionChromeCommitToken | None:
        return self._router.prepare_candidate(prepared)

    def accept_candidate(
        self,
        candidate: ExtensionChromeSink,
        *,
        rollback_snapshot: ExtensionChromeSnapshot | None = None,
    ) -> ChromeAcceptanceResult:
        return self._router.accept_candidate(
            candidate, rollback_snapshot=rollback_snapshot
        )

    def owns_sink(self, sink: ExtensionChromeSink) -> bool:
        return self._router.owns_sink(sink)

    def dispose_retired_sink(self, retired: ExtensionChromeSink) -> str | None:
        return self._router.dispose_retired_sink(retired)

    def _route_sink_operation(self, operation: ChromeHandoffOperation) -> object:
        return self._router._route_sink_operation(operation)  # noqa: SLF001 - exact transaction owner

    def _route_bound_sink_operation(
        self, sink: ExtensionChromeSink, operation: ChromeHandoffOperation
    ) -> object:
        return self._router._route_bound_sink_operation(sink, operation)  # noqa: SLF001 - exact transaction owner

    def _dispose_handoff_listener(self, operation: ChromeHandoffOperation) -> None:
        self._router._dispose_handoff_listener(operation)  # noqa: SLF001 - exact transaction owner

    @contextmanager
    def _retiring_disposal_route(self) -> Iterator[None]:
        with self._router._retiring_disposal_route():  # noqa: SLF001 - exact transaction owner
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
        if event.kind == "reconcile":
            return self._chrome.generation.reconcile_generation(
                cast(ExtensionChromeSnapshot, event.values[0]),
                retirement_scope=self._retiring_disposal_route,
            )
        if event.kind in {"widget", "header", "footer", "title", "indicator"}:
            self._deliver_chrome_region_event(event)
            return None
        return self._deliver_chrome_input_event(event)

    def _deliver_chrome_region_event(self, event: ExtensionChromeEvent) -> None:
        values = event.values
        if event.kind == "widget":
            self._chrome.component.set_widget(
                cast(str, values[0]), values[1], placement=cast(str, values[2])
            )
        elif event.kind == "header":
            self._chrome.component.set_header(values[0])
        elif event.kind == "footer":
            self._chrome.footer.set_footer(values[0])
        elif event.kind == "title":
            self._chrome.component.set_title(cast(str, values[0]))
        elif event.kind == "indicator":
            self._chrome.component.set_working_indicator(values[0], values[1])

    def _deliver_chrome_input_event(self, event: ExtensionChromeEvent) -> object:
        values = event.values
        if event.kind == "hidden-thinking-label":
            self._transcript.set_hidden_thinking_label(cast("str | None", values[0]))
        elif event.kind == "autocomplete":
            self._autocomplete.add_extension_provider(values[0])
        elif event.kind == "editor-component":
            self._custom_editor.set_editor_component(values[0])
        elif event.kind == "listener":
            return self._chrome.listeners.add(
                cast("Callable[[str], object]", values[1])
            )
        return None

    def select(self, title: str, options: Sequence[str]) -> str | None:
        return self._modals.run_extension_select(title, options)

    def input(self, title: str, placeholder: str | None = None) -> str | None:
        return self._modals.run_extension_input(title, placeholder)

    def editor(self, title: str, prefill: str | None = None) -> str | None:
        return self._modals.run_extension_editor(title, prefill)

    def confirm(self, title: str, message: str) -> bool:
        return self._modals.run_extension_confirm(title, message)

    def set_status(self, key: str, text: str | None) -> None:
        self._chrome.component.set_status(key, text)

    def set_working_message(self, message: str | None = None) -> None:
        self._chrome.component.set_working_message(message)

    def set_working_visible(self, visible: bool) -> None:
        self._chrome.component.set_working_visible(visible)

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
        return self._input_editor.get_input_text()

    def set_editor_text(self, text: str) -> None:
        self._input_editor.set_input_text(text)

    def paste_to_editor(self, text: str) -> None:
        self._input_editor.paste_input_text(text)

    def add_terminal_input_listener(self, handler: Any) -> Callable[[], None]:
        operation = ChromeHandoffOperation("listener", (handler,))
        result = self._route_sink_operation(operation)
        if callable(result):
            return result
        return lambda: self._dispose_handoff_listener(operation)

    def get_tools_expanded(self) -> bool:
        return bool(self._transcript.tools_expanded)

    def set_tools_expanded(self, expanded: bool) -> None:
        # The terminal UI's verb bundles the retained rich-row rerender with
        # the flag write, so the two writers can never disagree on refresh.
        self._transcript.set_tools_expanded(bool(expanded))

    def add_autocomplete_provider(self, factory: object) -> None:
        self._route_sink_operation(ChromeHandoffOperation("autocomplete", (factory,)))

    def set_editor_component(self, factory: object | None) -> None:
        self._route_sink_operation(
            ChromeHandoffOperation("editor-component", (factory,))
        )

    def get_editor_component(self) -> object | None:
        return self._custom_editor.factory

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


@dataclass(slots=True)
class TerminalUi:
    """Stateful terminal frame for the native tool-loop REPL.

    The UI intentionally uses whole-frame repainting (`cursor home` +
    region composition) instead of relative row rewrites. The injected screen
    owner exposes deterministic frame inspection and draws real TTY frames.
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
    # the Ctrl+O/Ctrl+T view flags (``ui/components/transcript.py``).
    _transcript: TranscriptComponent = field(init=False)
    # One concrete graph exposes every composed owner without restating their
    # methods on the terminal shell.
    components: TerminalComponents = field(init=False)
    available_provider_count: int = 0
    # Single owner for the slash menu, the @/path completion popup, the
    # published CommandSurface (names/descriptions/extension shortcut keys),
    # the settings-driven row cap, and the extension provider registry effects
    # (``ui/autocomplete.py``). Session startup and ``/reload`` publish through
    # its ``replace_command_surface``/``set_max_visible`` verbs.
    _autocomplete: AutocompleteComponent = field(init=False)
    # Exactly one selector/dialog/custom overlay is active. This owner holds
    # only synchronous transition state; TerminalModalDriver owns orchestration.
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
    # One owner for painting, inline-scrollback bookkeeping, modal driving,
    # raw key reads, resize handling, close, and external-I/O suspension.
    _screen: Screen = field(init=False)
    # One owner for the live duck-typed extension editor's seven-field record,
    # wiring, action dispatch, text mirror, and frame projection.
    _custom_editor: CustomEditorOwner = field(init=False)
    keybindings_manager: KeybindingsManager | None = None
    # Constructor-only wiring record: ClipboardImages owns it after startup;
    # unlike the retired reader/path fields it cannot be rewritten piecemeal.
    clipboard_config: InitVar[ClipboardConfig | None] = None

    def __post_init__(self, clipboard_config: ClipboardConfig | None) -> None:
        editor = EditorState()
        self._overlays = OverlayState()
        chrome_record = ExtensionChromeState()
        self._driver = TerminalDriver(self.input_stream, self.terminal_stream)
        self._screen = Screen(
            self._driver,
            self._overlays,
            self.terminal_stream,
            input_fd=lambda: self.input_stream.fileno(),
        )
        external_editor = ExtensionExternalEditor(
            external_io_suspension=self._screen.external_io_suspension,
            terminal_write=self._driver.write,
            input_stream=self.input_stream,
            terminal_stream=self.terminal_stream,
        )
        self._custom_editor = CustomEditorOwner(
            CustomEditorState(),
            editor,
            self._screen.paint_lock,
            self._screen.paint,
            host=self,
            theme=lambda: chrome_style_for(self.terminal_stream),
            keybindings_manager=lambda: self.keybindings_manager,
            effects=CustomEditorEffects(
                restore_input_text=lambda text: self.input_editor.set_input_text(text),
                clear_initial_text=lambda: self.input_editor.clear_initial_text(),
                enqueue_follow_up=lambda text: self.pending_messages.enqueue_follow_up(
                    text
                ),
                restore_pending=lambda: (
                    self.pending_messages.restore_pending_to_editor()
                ),
                paste_clipboard_image=lambda: (
                    self.clipboard_images.paste_clipboard_image()
                ),
                external_editor=external_editor.run_configured,
                autocomplete_provider=lambda: (
                    self._autocomplete.custom_editor_provider()
                ),
            ),
        )
        self._autocomplete = AutocompleteComponent(
            editor,
            cwd=self.cwd,
            repaint=self._screen.paint,
            custom_editor=self._custom_editor,
            surface=CommandSurface(
                names=TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS,
                descriptions=dict(DEFAULT_REPL_COMMAND_DESCRIPTIONS),
            ),
        )
        self._transcript = TranscriptComponent(
            self._screen.paint_lock,
            self._screen.paint,
            reset_scrollback=self._screen.force_full_redraw,
            render_inputs=self._screen.render_inputs,
        )
        self.pending_messages = PendingMessages(
            editor,
            self._screen.paint_lock,
            self._screen.paint,
            custom_editor=self._custom_editor,
            refresh_slash_menu=self._autocomplete.refresh_slash_menu,
        )
        self.clipboard_images = ClipboardImages(
            editor,
            self._screen.paint_lock,
            self._screen.paint,
            cwd=self.cwd,
            config=clipboard_config,
            command_names=lambda: self._autocomplete.command_names,
            refresh_autocomplete=self._autocomplete.refresh,
            add_notice=self._transcript.add_notice,
            custom_editor=self._custom_editor,
        )
        self.input_editor = InputEditor(
            editor,
            self._screen.paint_lock,
            self._screen.paint,
            command_names=lambda: self._autocomplete.command_names,
            refresh_autocomplete=self._autocomplete.refresh,
            custom_editor=self._custom_editor,
            insert_paste=self.clipboard_images.insert_paste,
        )
        chrome = ExtensionChromeComponent(
            chrome_record,
            self._screen.paint_lock,
            self._screen.paint,
            tui_handle=_ChromeTuiHandle(self._screen.request_render),
            render_inputs=self._screen.render_inputs,
            push_title=self._driver.push_title,
            write_title=self._driver.write_title,
            restore_title=self._driver.restore_title,
            clear_working_text=self._transcript.discard_working_text,
        )
        footer = FooterComponent(
            chrome_record,
            self._screen.paint_lock,
            self._screen.paint,
            cwd=self.cwd,
            available_provider_count=lambda: self.available_provider_count,
            build_region=chrome.build_region,
            dispose_region=chrome.dispose_region,
            render_region=chrome.render_region,
            builtin_lines=self.footer_lines,
        )
        listeners = TerminalInputListeners(
            chrome_record, self._screen.paint_lock, self._screen.paint
        )

        chrome_owners = build_extension_chrome_owners(
            chrome_record,
            self._screen.paint_lock,
            self._screen.paint,
            component=chrome,
            footer=footer,
            listeners=listeners,
            editor=self.input_editor,
            autocomplete=self._autocomplete,
            custom_editor=self._custom_editor,
            reset_hidden_thinking_label=self._transcript.reset_hidden_thinking_label,
            set_hidden_thinking_label=self._transcript.set_hidden_thinking_label,
        )
        self._screen.bind(
            FrameSources(
                transcript=self._transcript,
                input_editor=self.input_editor,
                regions=FrameRegionSources(
                    popup=lambda width, height: (
                        self._autocomplete.popup_menu_frame_lines(
                            width=width, max_rows=max(1, height - 7)
                        )
                    ),
                    pending=self.pending_messages.region_lines,
                    status=chrome.status_lines,
                    header=chrome.header_lines,
                    above_editor=lambda width: chrome.widget_lines(
                        "above_editor", width
                    ),
                    below_editor=lambda width: chrome.widget_lines(
                        "below_editor", width
                    ),
                    footer=footer.lines,
                    custom_editor=lambda width: (
                        self._custom_editor.frame_lines(width)
                        if self._custom_editor.active
                        else None
                    ),
                ),
                footer_lines=footer.builtin_lines,
                poll_idle=footer.poll_branch,
            )
        )
        modals = TerminalModalDriver(
            self._overlays,
            self._screen,
            self.input_editor,
            external_editor,
            lambda: self.keybindings_manager,
        )
        self.components = TerminalComponents(
            driver=self._driver,
            screen=self._screen,
            overlays=self._overlays,
            input_editor=self.input_editor,
            transcript=self._transcript,
            chrome=chrome_owners,
            autocomplete=self._autocomplete,
            pending_messages=self.pending_messages,
            clipboard_images=self.clipboard_images,
            custom_editor=self._custom_editor,
            modals=modals,
        )

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

        transcript = self.components.transcript
        if not transcript.history_blocks:
            transcript.seed_history(self._startup_blocks())
        self.components.driver.install_resize_handler()
        self.components.screen.paint()

    def read_line(self, prompt_label: str, *, footer: str | None = None) -> str:
        """Read one input line while keeping the input/footer regions live."""

        del prompt_label
        if footer is not None:
            self.components.chrome.footer.set_builtin_text(footer)
        self.input_editor.begin_line()
        self._custom_editor.prepare_line(self.input_editor.text)
        self._screen.paint()
        fd = self.input_stream.fileno()
        editing_context = EditingKeyContext(
            mode=EditingMode.LINE,
            slash_menu_open=lambda: self._autocomplete.slash_menu_open,
            slash_menu_has_matches=lambda: bool(self._autocomplete.filtered_commands()),
            slash_menu_exact_match=lambda text: (
                text in self._autocomplete.filtered_commands()
            ),
            autocomplete_open=lambda: self._autocomplete.autocomplete_open,
            navigate_slash_menu=self._autocomplete.navigate_slash_menu,
            navigate_autocomplete=self._autocomplete.navigate,
            accept_slash_menu=self._autocomplete.accept_slash_menu_selection,
            accept_autocomplete=self._autocomplete.accept_selection,
            dismiss_slash_menu=self._autocomplete.dismiss_slash_menu,
            close_autocomplete=self._autocomplete.close,
            attempt_path_completion=self._autocomplete.attempt_path_completion,
            consume_paste=self.input_editor.consume_paste,
            insert_paste=self.clipboard_images.insert_paste,
            repaint=self._screen.paint,
            is_local_command=self._submitted_text_is_local_command,
            allow_history=True,
            allow_path_completion=True,
            line_effects=LineEditingEffects(
                matches_external_editor=lambda key: matches_key_specs(
                    key,
                    resolved_key_specs("app.editor.external", self.keybindings_manager),
                ),
                run_external_editor=lambda text: ExtensionExternalEditor(
                    external_io_suspension=self.components.screen.external_io_suspension,
                    terminal_write=self._driver.write,
                    input_stream=self.input_stream,
                    terminal_stream=self.terminal_stream,
                ).run_configured(text),
                paste_clipboard_image=self.clipboard_images.paste_clipboard_image,
                shortcut_keys=lambda: tuple(self._autocomplete.shortcut_keys),
                custom_editor_active=lambda: self._custom_editor.active,
                handle_custom_editor=self._custom_editor.handle_key,
                consume_custom_exit=self._custom_editor.consume_exit_requested,
            ),
            allow_listener_replacement=True,
            listener_replaced=lambda: self.components.chrome.listeners.last_replaced,
        )
        with self._driver.raw_mode():
            while True:
                key = self._screen.read_key_polling_resize(fd)
                if key is None:
                    return ""
                key = self.components.chrome.listeners.apply(key)
                outcome = apply_editing_key(self.input_editor, key, editing_context)
                if outcome.action is EditingAction.INTERRUPT:
                    raise KeyboardInterrupt
                if outcome.action is EditingAction.EOF:
                    return ""
                if outcome.action in {EditingAction.SUBMIT, EditingAction.APP_COMMAND}:
                    return f"{outcome.text}\n"

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
        editing_mode = _active_editing_mode(accept_queue, accept_commands)
        command_only = editing_mode is EditingMode.ACTIVE_COMMAND
        editing_context = EditingKeyContext(
            mode=editing_mode,
            slash_menu_open=lambda: self._autocomplete.slash_menu_open,
            slash_menu_has_matches=lambda: bool(self._autocomplete.filtered_commands()),
            slash_menu_exact_match=lambda text: (
                text in self._autocomplete.filtered_commands()
            ),
            autocomplete_open=lambda: self._autocomplete.autocomplete_open,
            navigate_slash_menu=self._autocomplete.navigate_slash_menu,
            navigate_autocomplete=self._autocomplete.navigate,
            accept_slash_menu=self._autocomplete.accept_slash_menu_selection,
            accept_autocomplete=self._autocomplete.accept_selection,
            dismiss_slash_menu=self._autocomplete.dismiss_slash_menu,
            close_autocomplete=self._autocomplete.close,
            attempt_path_completion=self._autocomplete.attempt_path_completion,
            consume_paste=self.input_editor.consume_paste,
            insert_paste=self.clipboard_images.insert_paste,
            repaint=self._screen.paint,
            is_local_command=self._submitted_text_is_local_command,
            allow_history=False,
            allow_path_completion=not command_only,
            tab_repaint="always",
        )
        with self._driver.raw_mode():
            while not done_event.is_set():
                self._screen.poll_resize_repaint()
                key = self._screen.read_driver_key(
                    self._driver.read_key_if_available(fd, poll_seconds)
                )
                if key is None:
                    continue
                outcome = apply_editing_key(self.input_editor, key, editing_context)
                if outcome.action is EditingAction.ABORT:
                    abort_event.set()
                    return TURN_ABORTED
                if outcome.action is EditingAction.INTERRUPT:
                    abort_event.set()
                    raise KeyboardInterrupt
                if outcome.action is EditingAction.LOCAL_COMMAND:
                    self.input_editor.set_pending_command(outcome.text or "")
                    abort_event.set()
                    self._screen.paint()
                    return TURN_LOCAL_COMMAND
                if outcome.action is EditingAction.STEER:
                    self.pending_messages.enqueue_steering(outcome.text or "")
                    abort_event.set()
                    return TURN_STEERED
                if outcome.action is EditingAction.FOLLOW_UP:
                    self.pending_messages.enqueue_follow_up(outcome.text or "")
                elif outcome.action is EditingAction.RESTORE_PENDING:
                    self.pending_messages.restore_pending_to_editor()
            return TURN_SETTLED

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

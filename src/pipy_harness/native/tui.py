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

from pipy_harness.native.extension_chrome_state import (
    ExtensionChromeCommitToken,
    ExtensionChromeEvent,
    ExtensionChromePrepareInput,
    ExtensionChromeSink,
    ExtensionChromeSnapshot,
)
from pipy_harness.native.keybindings import (
    KeybindingsManager,
)
from pipy_harness.native.startup_chrome import startup_history_blocks
from pipy_harness.native.themes import NativeThemeStore, select_theme
from pipy_harness.native.ui.autocomplete import AutocompleteComponent
from pipy_harness.native.ui.chrome_handoff import (
    ChromeAcceptanceResult,
    ChromeHandoffOperation,
    ExtensionChromeRouter,
)
from pipy_harness.native.ui.clipboard_images import ClipboardConfig
from pipy_harness.native.ui.components.custom_editor import CustomEditorOwner
from pipy_harness.native.ui.components.extension_prompts import (
    ExtensionExternalEditor,
)
from pipy_harness.native.ui.components.input_editor import (
    EditingAction,
    EditingKeyContext,
    EditingMode,
    InputEditor,
    LineEditingEffects,
    apply_editing_key,
    submitted_text_is_local_command,
)
from pipy_harness.native.ui.components.transcript import TranscriptComponent
from pipy_harness.native.ui.composition import (
    TerminalComponents,
    TerminalCompositionInput,
    build_terminal_components,
)
from pipy_harness.native.ui.extension_generation import ExtensionChromeOwners
from pipy_harness.native.ui.key_specs import matches_key_specs, resolved_key_specs
from pipy_harness.native.ui.modal_driver import TerminalModalDriver

TOOL_LOOP_TUI_RUNTIME_LABEL = "tool-loop-tui"
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
    # One concrete graph exposes every composed owner without restating their
    # methods on the terminal shell.
    components: TerminalComponents = field(init=False)
    available_provider_count: int = 0
    keybindings_manager: KeybindingsManager | None = None
    # Constructor-only wiring record: ClipboardImages owns it after startup;
    # unlike the retired reader/path fields it cannot be rewritten piecemeal.
    clipboard_config: InitVar[ClipboardConfig | None] = None

    def __post_init__(self, clipboard_config: ClipboardConfig | None) -> None:
        self.components = build_terminal_components(
            TerminalCompositionInput(
                input_stream=self.input_stream,
                terminal_stream=self.terminal_stream,
                cwd=self.cwd,
                host=self,
                builtin_footer_lines=self.footer_lines,
                available_provider_count=lambda: self.available_provider_count,
                clipboard_config=clipboard_config,
                keybindings_manager=lambda: self.keybindings_manager,
            )
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

        components = self.components
        transcript = components.transcript
        if not transcript.history_blocks:
            transcript.seed_history(
                startup_history_blocks(self.cwd, self.include_workspace_defaults)
            )
        components.driver.install_resize_handler()
        components.screen.paint()

    def read_line(self, prompt_label: str, *, footer: str | None = None) -> str:
        """Read one input line while keeping the input/footer regions live."""

        del prompt_label
        components = self.components
        driver = components.driver
        screen = components.screen
        input_editor = components.input_editor
        autocomplete = components.autocomplete
        custom_editor = components.custom_editor
        clipboard_images = components.clipboard_images
        chrome = components.chrome
        if footer is not None:
            chrome.footer.set_builtin_text(footer)
        input_editor.begin_line()
        custom_editor.prepare_line(input_editor.text)
        screen.paint()
        fd = self.input_stream.fileno()
        editing_context = EditingKeyContext(
            mode=EditingMode.LINE,
            slash_menu_open=lambda: autocomplete.slash_menu_open,
            slash_menu_has_matches=lambda: bool(autocomplete.filtered_commands()),
            slash_menu_exact_match=lambda text: (
                text in autocomplete.filtered_commands()
            ),
            autocomplete_open=lambda: autocomplete.autocomplete_open,
            navigate_slash_menu=autocomplete.navigate_slash_menu,
            navigate_autocomplete=autocomplete.navigate,
            accept_slash_menu=autocomplete.accept_slash_menu_selection,
            accept_autocomplete=autocomplete.accept_selection,
            dismiss_slash_menu=autocomplete.dismiss_slash_menu,
            close_autocomplete=autocomplete.close,
            attempt_path_completion=autocomplete.attempt_path_completion,
            consume_paste=input_editor.consume_paste,
            insert_paste=clipboard_images.insert_paste,
            repaint=screen.paint,
            is_local_command=submitted_text_is_local_command,
            allow_history=True,
            allow_path_completion=True,
            line_effects=LineEditingEffects(
                matches_external_editor=lambda key: matches_key_specs(
                    key,
                    resolved_key_specs("app.editor.external", self.keybindings_manager),
                ),
                run_external_editor=lambda text: ExtensionExternalEditor(
                    external_io_suspension=screen.external_io_suspension,
                    terminal_write=driver.write,
                    input_stream=self.input_stream,
                    terminal_stream=self.terminal_stream,
                ).run_configured(text),
                paste_clipboard_image=clipboard_images.paste_clipboard_image,
                shortcut_keys=lambda: tuple(autocomplete.shortcut_keys),
                custom_editor_active=lambda: custom_editor.active,
                handle_custom_editor=custom_editor.handle_key,
                consume_custom_exit=custom_editor.consume_exit_requested,
            ),
            allow_listener_replacement=True,
            listener_replaced=lambda: chrome.listeners.last_replaced,
        )
        with driver.raw_mode():
            while True:
                key = screen.read_key_polling_resize(fd)
                if key is None:
                    return ""
                key = chrome.listeners.apply(key)
                outcome = apply_editing_key(input_editor, key, editing_context)
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

        components = self.components
        driver = components.driver
        screen = components.screen
        input_editor = components.input_editor
        autocomplete = components.autocomplete
        clipboard_images = components.clipboard_images
        pending_messages = components.pending_messages
        fd = self.input_stream.fileno()
        editing_mode = _active_editing_mode(accept_queue, accept_commands)
        command_only = editing_mode is EditingMode.ACTIVE_COMMAND
        editing_context = EditingKeyContext(
            mode=editing_mode,
            slash_menu_open=lambda: autocomplete.slash_menu_open,
            slash_menu_has_matches=lambda: bool(autocomplete.filtered_commands()),
            slash_menu_exact_match=lambda text: (
                text in autocomplete.filtered_commands()
            ),
            autocomplete_open=lambda: autocomplete.autocomplete_open,
            navigate_slash_menu=autocomplete.navigate_slash_menu,
            navigate_autocomplete=autocomplete.navigate,
            accept_slash_menu=autocomplete.accept_slash_menu_selection,
            accept_autocomplete=autocomplete.accept_selection,
            dismiss_slash_menu=autocomplete.dismiss_slash_menu,
            close_autocomplete=autocomplete.close,
            attempt_path_completion=autocomplete.attempt_path_completion,
            consume_paste=input_editor.consume_paste,
            insert_paste=clipboard_images.insert_paste,
            repaint=screen.paint,
            is_local_command=submitted_text_is_local_command,
            allow_history=False,
            allow_path_completion=not command_only,
            tab_repaint="always",
        )
        with driver.raw_mode():
            while not done_event.is_set():
                screen.poll_resize_repaint()
                key = screen.read_driver_key(
                    driver.read_key_if_available(fd, poll_seconds)
                )
                if key is None:
                    continue
                outcome = apply_editing_key(input_editor, key, editing_context)
                if outcome.action is EditingAction.ABORT:
                    abort_event.set()
                    return TURN_ABORTED
                if outcome.action is EditingAction.INTERRUPT:
                    abort_event.set()
                    raise KeyboardInterrupt
                if outcome.action is EditingAction.LOCAL_COMMAND:
                    input_editor.set_pending_command(outcome.text or "")
                    abort_event.set()
                    screen.paint()
                    return TURN_LOCAL_COMMAND
                if outcome.action is EditingAction.STEER:
                    pending_messages.enqueue_steering(outcome.text or "")
                    abort_event.set()
                    return TURN_STEERED
                if outcome.action is EditingAction.FOLLOW_UP:
                    pending_messages.enqueue_follow_up(outcome.text or "")
                elif outcome.action is EditingAction.RESTORE_PENDING:
                    pending_messages.restore_pending_to_editor()
            return TURN_SETTLED

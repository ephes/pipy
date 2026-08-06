"""Concrete owner graph and composition transaction for one terminal UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from pipy_harness.native.chrome import chrome_style_for
from pipy_harness.native.coding.command_registry import project_command_completions
from pipy_harness.native.editor_state import EditorState
from pipy_harness.native.extension_chrome_state import ExtensionChromeState
from pipy_harness.native.keybindings import KeybindingsManager
from pipy_harness.native.overlay_state import OverlayState
from pipy_harness.native.repl_input import DEFAULT_REPL_COMMAND_DESCRIPTIONS
from pipy_harness.native.terminal_driver import TerminalDriver
from pipy_harness.native.ui.autocomplete import AutocompleteComponent, CommandSurface
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
from pipy_harness.native.ui.components.input_editor import InputEditor
from pipy_harness.native.ui.components.transcript import TranscriptComponent
from pipy_harness.native.ui.extension_chrome import ExtensionChromeComponent
from pipy_harness.native.ui.extension_generation import (
    ExtensionChromeOwners,
    build_extension_chrome_owners,
)
from pipy_harness.native.ui.modal_driver import TerminalModalDriver
from pipy_harness.native.ui.pending_messages import PendingMessages
from pipy_harness.native.ui.screen import FrameRegionSources, FrameSources, Screen
from pipy_harness.native.ui.terminal_input_listeners import TerminalInputListeners

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


@dataclass(frozen=True, slots=True)
class TerminalCompositionInput:
    """The exact constructor values needed to build one terminal owner graph."""

    input_stream: TextIO
    terminal_stream: TextIO
    cwd: Path
    host: object
    builtin_footer_lines: tuple[str, str]
    available_provider_count: Callable[[], int]
    clipboard_config: ClipboardConfig | None
    keybindings_manager: Callable[[], KeybindingsManager | None]


@dataclass(frozen=True, slots=True)
class TerminalComponents:
    """The exact concrete owners composed for one terminal session."""

    driver: TerminalDriver
    screen: Screen
    overlays: OverlayState
    input_editor: InputEditor
    transcript: TranscriptComponent
    chrome: ExtensionChromeOwners
    autocomplete: AutocompleteComponent
    pending_messages: PendingMessages
    clipboard_images: ClipboardImages
    custom_editor: CustomEditorOwner
    modals: TerminalModalDriver


def build_terminal_components(input: TerminalCompositionInput) -> TerminalComponents:
    """Build and bind one complete terminal component graph."""

    components: TerminalComponents
    editor = EditorState()
    overlays = OverlayState()
    chrome_record = ExtensionChromeState()
    driver = TerminalDriver(input.input_stream, input.terminal_stream)
    screen = Screen(
        driver,
        overlays,
        input.terminal_stream,
        input_fd=lambda: input.input_stream.fileno(),
    )
    external_editor = ExtensionExternalEditor(
        external_io_suspension=screen.external_io_suspension,
        terminal_write=driver.write,
        input_stream=input.input_stream,
        terminal_stream=input.terminal_stream,
    )
    custom_editor = CustomEditorOwner(
        CustomEditorState(),
        editor,
        screen.paint_lock,
        screen.paint,
        host=input.host,
        theme=lambda: chrome_style_for(input.terminal_stream),
        keybindings_manager=input.keybindings_manager,
        effects=CustomEditorEffects(
            restore_input_text=lambda text: components.input_editor.set_input_text(
                text
            ),
            clear_initial_text=lambda: components.input_editor.clear_initial_text(),
            enqueue_follow_up=lambda text: (
                components.pending_messages.enqueue_follow_up(text)
            ),
            restore_pending=lambda: (
                components.pending_messages.restore_pending_to_editor()
            ),
            paste_clipboard_image=lambda: (
                components.clipboard_images.paste_clipboard_image()
            ),
            external_editor=external_editor.run_configured,
            autocomplete_provider=lambda: (
                components.autocomplete.custom_editor_provider()
            ),
        ),
    )
    autocomplete = AutocompleteComponent(
        editor,
        cwd=input.cwd,
        repaint=screen.paint,
        custom_editor=custom_editor,
        surface=CommandSurface(
            names=TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS,
            descriptions=dict(DEFAULT_REPL_COMMAND_DESCRIPTIONS),
        ),
    )
    transcript = TranscriptComponent(
        screen.paint_lock,
        screen.paint,
        reset_scrollback=screen.force_full_redraw,
        render_inputs=screen.render_inputs,
    )
    pending_messages = PendingMessages(
        editor,
        screen.paint_lock,
        screen.paint,
        custom_editor=custom_editor,
        refresh_slash_menu=autocomplete.refresh_slash_menu,
    )
    clipboard_images = ClipboardImages(
        editor,
        screen.paint_lock,
        screen.paint,
        cwd=input.cwd,
        config=input.clipboard_config,
        command_names=lambda: autocomplete.command_names,
        refresh_autocomplete=autocomplete.refresh,
        add_notice=transcript.add_notice,
        custom_editor=custom_editor,
    )
    input_editor = InputEditor(
        editor,
        screen.paint_lock,
        screen.paint,
        command_names=lambda: autocomplete.command_names,
        refresh_autocomplete=autocomplete.refresh,
        custom_editor=custom_editor,
        insert_paste=clipboard_images.insert_paste,
    )
    chrome = ExtensionChromeComponent(
        chrome_record,
        screen.paint_lock,
        screen.paint,
        tui_handle=_ChromeTuiHandle(screen.request_render),
        render_inputs=screen.render_inputs,
        push_title=driver.push_title,
        write_title=driver.write_title,
        restore_title=driver.restore_title,
        clear_working_text=transcript.discard_working_text,
    )
    footer = FooterComponent(
        chrome_record,
        screen.paint_lock,
        screen.paint,
        cwd=input.cwd,
        available_provider_count=input.available_provider_count,
        build_region=chrome.build_region,
        dispose_region=chrome.dispose_region,
        render_region=chrome.render_region,
        builtin_lines=input.builtin_footer_lines,
    )
    listeners = TerminalInputListeners(chrome_record, screen.paint_lock, screen.paint)

    chrome_owners = build_extension_chrome_owners(
        chrome_record,
        screen.paint_lock,
        screen.paint,
        component=chrome,
        footer=footer,
        listeners=listeners,
        editor=input_editor,
        autocomplete=autocomplete,
        custom_editor=custom_editor,
        reset_hidden_thinking_label=transcript.reset_hidden_thinking_label,
        set_hidden_thinking_label=transcript.set_hidden_thinking_label,
    )
    screen.bind(
        FrameSources(
            transcript=transcript,
            input_editor=input_editor,
            regions=FrameRegionSources(
                popup=lambda width, height: autocomplete.popup_menu_frame_lines(
                    width=width, max_rows=max(1, height - 7)
                ),
                pending=pending_messages.region_lines,
                status=chrome.status_lines,
                header=chrome.header_lines,
                above_editor=lambda width: chrome.widget_lines("above_editor", width),
                below_editor=lambda width: chrome.widget_lines("below_editor", width),
                footer=footer.lines,
                custom_editor=lambda width: (
                    custom_editor.frame_lines(width) if custom_editor.active else None
                ),
            ),
            footer_lines=footer.builtin_lines,
            poll_idle=footer.poll_branch,
        )
    )
    modals = TerminalModalDriver(
        overlays,
        screen,
        input_editor,
        external_editor,
        input.keybindings_manager,
    )
    components = TerminalComponents(
        driver=driver,
        screen=screen,
        overlays=overlays,
        input_editor=input_editor,
        transcript=transcript,
        chrome=chrome_owners,
        autocomplete=autocomplete,
        pending_messages=pending_messages,
        clipboard_images=clipboard_images,
        custom_editor=custom_editor,
        modals=modals,
    )
    return components

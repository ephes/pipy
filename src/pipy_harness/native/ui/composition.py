"""Concrete owner graph for one terminal UI."""

from __future__ import annotations

from dataclasses import dataclass

from pipy_harness.native.overlay_state import OverlayState
from pipy_harness.native.terminal_driver import TerminalDriver
from pipy_harness.native.ui.autocomplete import AutocompleteComponent
from pipy_harness.native.ui.clipboard_images import ClipboardImages
from pipy_harness.native.ui.components.custom_editor import CustomEditorOwner
from pipy_harness.native.ui.components.input_editor import InputEditor
from pipy_harness.native.ui.components.transcript import TranscriptComponent
from pipy_harness.native.ui.extension_generation import ExtensionChromeOwners
from pipy_harness.native.ui.modal_driver import TerminalModalDriver
from pipy_harness.native.ui.pending_messages import PendingMessages
from pipy_harness.native.ui.screen import Screen


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

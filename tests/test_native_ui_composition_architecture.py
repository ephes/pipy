"""Focused contracts for the terminal composition/startup ownership cut."""

from __future__ import annotations

import ast
import inspect
import io
import re
from dataclasses import fields
from pathlib import Path
from typing import Any, cast

import pytest

from pipy_harness.native.tui import TerminalUi
from pipy_harness.native.ui.autocomplete import AutocompleteComponent
from pipy_harness.native.ui.clipboard_images import ClipboardImages
from pipy_harness.native.ui.components.input_editor import InputEditor
from pipy_harness.native.ui.composition import (
    TerminalComponents,
    TerminalCompositionInput,
    build_terminal_components,
)
from pipy_harness.native.ui.pending_messages import PendingMessages

REPO_ROOT = Path(__file__).resolve().parents[1]
TUI_PATH = REPO_ROOT / "src/pipy_harness/native/tui.py"
COMPOSITION_PATH = REPO_ROOT / "src/pipy_harness/native/ui/composition.py"
STARTUP_PATH = REPO_ROOT / "src/pipy_harness/native/startup_chrome.py"
INPUT_EDITOR_PATH = REPO_ROOT / "src/pipy_harness/native/ui/components/input_editor.py"
EXPECTED_SIGNATURE = (
    "(input_stream: 'TextIO', terminal_stream: 'TextIO', cwd: 'Path', "
    "include_workspace_defaults: 'bool' = False, runtime_label: 'str' = "
    "'tool-loop-tui', footer_lines: 'tuple[str, str]' = ('', ''), "
    "available_provider_count: 'int' = 0, keybindings_manager: "
    "'KeybindingsManager | None' = None, clipboard_config: "
    "'InitVar[ClipboardConfig | None]' = None) -> None"
)


def _class(path: Path, name: str) -> ast.ClassDef:
    return next(
        node
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _method(class_node: ast.ClassDef, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _composition_input(tmp_path: Path, *, host: object | None = None):
    return TerminalCompositionInput(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
        host=object() if host is None else host,
        builtin_footer_lines=("top", "bottom"),
        available_provider_count=lambda: 3,
        clipboard_config=None,
        keybindings_manager=lambda: None,
    )


def test_composition_records_have_the_exact_concrete_fields() -> None:
    assert tuple(field.name for field in fields(TerminalCompositionInput)) == (
        "input_stream",
        "terminal_stream",
        "cwd",
        "host",
        "builtin_footer_lines",
        "available_provider_count",
        "clipboard_config",
        "keybindings_manager",
    )
    assert tuple(field.name for field in fields(TerminalComponents)) == (
        "driver",
        "screen",
        "overlays",
        "input_editor",
        "transcript",
        "chrome",
        "autocomplete",
        "pending_messages",
        "clipboard_images",
        "custom_editor",
        "modals",
    )
    assert getattr(TerminalCompositionInput, "__dataclass_params__").frozen is True
    assert getattr(TerminalComponents, "__dataclass_params__").frozen is True
    assert "__dict__" not in TerminalCompositionInput.__slots__
    assert "__dict__" not in TerminalComponents.__slots__


def test_terminal_signature_fields_and_retained_definitions_are_exact() -> None:
    assert str(inspect.signature(TerminalUi)) == EXPECTED_SIGNATURE
    assert tuple(field.name for field in fields(TerminalUi)) == (
        "input_stream",
        "terminal_stream",
        "cwd",
        "include_workspace_defaults",
        "runtime_label",
        "footer_lines",
        "components",
        "available_provider_count",
        "keybindings_manager",
    )

    class_node = _class(TUI_PATH, "TerminalUi")
    assert tuple(
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    ) == (
        "__post_init__",
        "is_supported",
        "start",
        "read_line",
        "wait_for_active_turn_interrupt",
    )


def test_terminal_post_init_is_only_one_typed_builder_assignment() -> None:
    post_init = _method(_class(TUI_PATH, "TerminalUi"), "__post_init__")
    assert len(post_init.body) == 1
    assignment = post_init.body[0]
    assert isinstance(assignment, ast.Assign)
    assert [ast.unparse(target) for target in assignment.targets] == ["self.components"]
    assert isinstance(assignment.value, ast.Call)
    assert ast.unparse(assignment.value.func) == "build_terminal_components"
    assert len(assignment.value.args) == 1
    composition_input = assignment.value.args[0]
    assert isinstance(composition_input, ast.Call)
    assert ast.unparse(composition_input.func) == "TerminalCompositionInput"
    assert tuple(keyword.arg for keyword in composition_input.keywords) == (
        "input_stream",
        "terminal_stream",
        "cwd",
        "host",
        "builtin_footer_lines",
        "available_provider_count",
        "clipboard_config",
        "keybindings_manager",
    )


def test_footer_data_reads_mutated_terminal_provider_count(tmp_path: Path) -> None:
    terminal = TerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
        available_provider_count=2,
    )

    terminal.available_provider_count = 5

    footer_data = terminal.components.chrome.footer._footer_data()  # noqa: SLF001
    assert footer_data.available_provider_count == 5


def test_builder_constructs_one_complete_graph_and_preserves_owner_identities(
    tmp_path: Path,
) -> None:
    host = object()
    components = build_terminal_components(_composition_input(tmp_path, host=host))

    editor = components.input_editor.editor_state
    assert components.pending_messages._editor is editor  # noqa: SLF001
    assert components.clipboard_images._editor is editor  # noqa: SLF001
    assert components.custom_editor.editor_state is editor
    assert components.autocomplete._editor is editor  # noqa: SLF001
    assert components.custom_editor._host is host  # noqa: SLF001

    sources = components.screen._sources  # noqa: SLF001
    assert sources is not None
    assert sources.transcript is components.transcript
    assert sources.input_editor is components.input_editor
    assert (
        getattr(sources.regions.pending, "__self__", None)
        is components.pending_messages
    )
    assert getattr(sources.footer_lines, "__self__", None) is components.chrome.footer
    assert getattr(sources.poll_idle, "__self__", None) is components.chrome.footer

    editor_participant = cast(Any, components.chrome.generation._participants[1])  # noqa: SLF001
    assert editor_participant._editor is components.input_editor
    assert editor_participant._autocomplete is components.autocomplete
    assert editor_participant._custom_editor is components.custom_editor

    tree = ast.parse(COMPOSITION_PATH.read_text(encoding="utf-8"))
    builder = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "build_terminal_components"
    )
    calls = [
        node.func.id
        for node in ast.walk(builder)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    for constructor in (
        "EditorState",
        "OverlayState",
        "TerminalDriver",
        "Screen",
        "ExtensionExternalEditor",
        "CustomEditorOwner",
        "AutocompleteComponent",
        "TranscriptComponent",
        "PendingMessages",
        "ClipboardImages",
        "InputEditor",
        "ExtensionChromeComponent",
        "FooterComponent",
        "TerminalInputListeners",
        "FrameSources",
        "TerminalModalDriver",
        "TerminalComponents",
    ):
        assert calls.count(constructor) == 1
    assert calls.count("build_extension_chrome_owners") == 1


def test_all_lock_bearing_composed_owners_share_the_screen_lock(
    tmp_path: Path,
) -> None:
    components = build_terminal_components(_composition_input(tmp_path))
    lock = components.screen.paint_lock
    lock_bearing_owners = (
        components.input_editor,
        components.transcript,
        components.pending_messages,
        components.clipboard_images,
        components.custom_editor,
        components.chrome.component,
        components.chrome.footer,
        components.chrome.listeners,
        components.chrome.generation,
    )
    assert all(owner._paint_lock is lock for owner in lock_bearing_owners)  # noqa: SLF001
    assert tuple(item.name for item in components.screen.contributors.ordinary) == (
        "popup",
        "pending",
        "status",
        "header",
        "above_editor",
        "below_editor",
        "footer",
        "custom_editor",
    )
    assert tuple(item.name for item in components.screen.contributors.overlays) == (
        "custom",
        "settings",
        "project_trust",
        "session_picker",
        "tree",
        "scoped_models",
        "model",
    )


def test_deferred_cycle_callbacks_wait_for_and_resolve_the_complete_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, object]] = []

    def record(label: str):
        def callback(owner: object, *_args: object, **_kwargs: object) -> None:
            calls.append((label, owner))

        return callback

    monkeypatch.setattr(InputEditor, "set_input_text", record("restore"))
    monkeypatch.setattr(InputEditor, "clear_initial_text", record("clear"))
    monkeypatch.setattr(PendingMessages, "enqueue_follow_up", record("follow-up"))
    monkeypatch.setattr(PendingMessages, "restore_pending_to_editor", record("pending"))
    monkeypatch.setattr(ClipboardImages, "paste_clipboard_image", record("clipboard"))
    monkeypatch.setattr(
        AutocompleteComponent, "custom_editor_provider", record("autocomplete")
    )
    keybinding_reads: list[None] = []
    inputs = _composition_input(tmp_path)
    inputs = TerminalCompositionInput(
        input_stream=inputs.input_stream,
        terminal_stream=inputs.terminal_stream,
        cwd=inputs.cwd,
        host=inputs.host,
        builtin_footer_lines=inputs.builtin_footer_lines,
        available_provider_count=inputs.available_provider_count,
        clipboard_config=inputs.clipboard_config,
        keybindings_manager=lambda: keybinding_reads.append(None),
    )

    components = build_terminal_components(inputs)
    assert calls == []
    assert keybinding_reads == []

    effects = components.custom_editor._effects  # noqa: SLF001
    effects.restore_input_text("draft")
    effects.clear_initial_text()
    effects.enqueue_follow_up("later")
    effects.restore_pending()
    effects.paste_clipboard_image()
    effects.autocomplete_provider()
    assert calls == [
        ("restore", components.input_editor),
        ("clear", components.input_editor),
        ("follow-up", components.pending_messages),
        ("pending", components.pending_messages),
        ("clipboard", components.clipboard_images),
        ("autocomplete", components.autocomplete),
    ]


def test_recursive_ui_source_has_no_terminal_or_session_backedge() -> None:
    forbidden = (
        re.compile(r"(?:pipy_harness\.)?native\.tui(?:\b|\.)"),
        re.compile(r"(?:pipy_harness\.)?native\.repl(?:\b|\.)"),
        re.compile(r"(?:pipy_harness\.)?native\.tool_loop_session(?:\b|\.)"),
        re.compile(r"(?:pipy_harness\.)?native\.coding\.session(?:\b|\.)"),
    )
    offenders: list[tuple[Path, str]] = []
    for path in sorted((REPO_ROOT / "src/pipy_harness/native/ui").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            if pattern.search(source):
                offenders.append((path.relative_to(REPO_ROOT), pattern.pattern))
    assert offenders == []
    assert "TerminalUi" not in COMPOSITION_PATH.read_text(encoding="utf-8")


def test_startup_and_classifier_have_one_definition_site() -> None:
    tui_source = TUI_PATH.read_text(encoding="utf-8")
    startup_source = STARTUP_PATH.read_text(encoding="utf-8")
    input_source = INPUT_EDITOR_PATH.read_text(encoding="utf-8")
    assert "def _startup_blocks" not in tui_source
    assert "def _submitted_text_is_local_command" not in tui_source
    assert startup_source.count("def startup_history_blocks(") == 1
    assert input_source.count("def submitted_text_is_local_command(") == 1
    assert tui_source.count("startup_history_blocks(") == 1
    assert tui_source.count("submitted_text_is_local_command") == 3

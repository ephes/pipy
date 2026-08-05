"""Pipy-owned terminal UI shell for native tool-loop REPL sessions.

The line-oriented renderer prints prompt, loader, assistant text, tool blocks,
and footer as independent lines. This module is the stateful/effectful façade
for a small inline terminal frame; ``native.frame_renderer`` composes immutable
snapshots of its history, transient output, input, overlays, and chrome into
full/live rows and deterministic terminal paint plans.
"""

from __future__ import annotations

import inspect
import os
import select
import shlex
import subprocess
import sys
import tempfile
import termios
import threading
import time
from collections.abc import (
    Callable,
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
    Sequence,
)
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Protocol,
    TextIO,
    TypedDict,
    cast,
)

from pipy_harness.native.agent import (
    AgentCancellationReason,
    AgentToolCall,
    ProductContent,
)
from pipy_harness.native.autocomplete_provider import (
    AutocompleteApplyResult,
    AutocompleteContext,
    AutocompleteSuggestion,
    call_provider_method,
    coerce_apply_result,
    coerce_suggestion,
    cursor_to_line_col,
)
from pipy_harness.native.chrome import (
    ChromeStyle,
    chrome_style_for,
    discover_loaded_resource_names,
    pipy_version_label,
)
from pipy_harness.native.clipboard import ImageClipboardResult
from pipy_harness.native.coding import CodingInputQueue
from pipy_harness.native.coding.command_registry import project_command_completions
from pipy_harness.native.coding.effects import CodingEffectCoordinator
from pipy_harness.native.diagnostics import emit_diagnostic
from pipy_harness.native.editor_state import (
    CompletionItem,
    CompletionMode,
    EditorState,
    QueuedInputKind,
)
from pipy_harness.native.extension_chrome_state import (
    ChromeRegion,
    ExtensionChromeCommitToken,
    ExtensionChromeEvent,
    ExtensionChromePrepareInput,
    ExtensionChromeSink,
    ExtensionChromeSnapshot,
    ExtensionChromeState,
)
from pipy_harness.native.extension_runtime import (
    ExtensionCapabilityError,
    ExtensionTool,
    FooterData,
    QueuedCustomMessage,
    QueuedUserMessage,
    RegisteredEntryRenderer,
    RegisteredMessageRenderer,
    RenderedCustomEntry,
    ToolRenderDetailsSink,
    _custom_entry_redraw_rows,
    _custom_entry_renderer_payload,
    _custom_message_renderer_payload,
    drain_custom_messages,
    drain_user_messages,
    is_valid_custom_entry_type,
    render_extension_entry,
    render_extension_message,
    safe_custom_entry_data,
)
from pipy_harness.native.frame_renderer import (
    ChromeSnapshot,
    FrameBlock,
    FrameSnapshot,
    InputSnapshot,
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
    clip_custom_text as _clip_custom_overlay_text,
)
from pipy_harness.native.frame_renderer import (
    clip_text as render_clip_text,
)
from pipy_harness.native.frame_renderer import (
    display_input_text as render_display_input_text,
)
from pipy_harness.native.frame_renderer import (
    input_index as render_input_index,
)
from pipy_harness.native.frame_renderer import (
    input_lines as render_input_lines,
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
from pipy_harness.native.frame_renderer import (
    visible_len as render_visible_len,
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
)
from pipy_harness.native.overlay_state import (
    ScopedModelRow as ScopedModelRow,
)
from pipy_harness.native.overlay_state import (
    SettingsRow as SettingsRow,
)
from pipy_harness.native.overlay_state import (
    TreeSelectorRow as TreeSelectorRow,
)
from pipy_harness.native.project_trust import (
    ProjectTrustEntry,
    ProjectTrustOption,
)
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.repl_input import (
    DEFAULT_REPL_COMMAND_DESCRIPTIONS,
)
from pipy_harness.native.session_generation import SessionGenerationSnapshot
from pipy_harness.native.session_tree import (
    CustomEntry as _CustomEntry,
)
from pipy_harness.native.session_tree import (
    CustomMessageEntry as _CustomMessageEntry,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.session_tree_commands import (
    SessionListEntry,
    sanitize_label_text,
)
from pipy_harness.native.terminal_driver import (
    _RESIZE_POLL_SECONDS,
    _TITLE_MAX_CHARS,
    TerminalDriver,
)
from pipy_harness.native.themes import NativeThemeStore, select_theme
from pipy_harness.native.tool_renderers import (
    _parse_tool_input,
    _plain_tool_call_header,
    _ToolLoopRenderer,
    build_tool_render_theme,
    render_chrome_component,
)
from pipy_harness.native.ui.autocomplete import BuiltinAutocompleteProvider
from pipy_harness.native.ui.chrome_handoff import (
    ChromeAcceptanceResult,
    ChromeHandoffOperation,
    ExtensionChromeRouter,
)
from pipy_harness.native.ui.components.custom_editor import (
    ExtensionEditorComponent,
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
from pipy_harness.native.ui.components.transcript import (
    HistoryBlock,
    HistoryBlockTuple,
    TranscriptComponent,
)
from pipy_harness.native.ui.components.tree_selector import (
    TreeSelectorComponent,
    tree_selector_region_lines,
)
from pipy_harness.native.ui.key_specs import (
    matches_key_specs,
    resolved_key_specs,
)
from pipy_harness.native.ui.paint_lock import PaintLock

if TYPE_CHECKING:
    from pipy_harness.native.extension_types import (
        CustomComponentFactory,
        CustomComponentOptions,
        ToolRenderContext,
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
            self._terminal_ui.set_extension_widget(
                cast(str, values[0]), values[1], placement=cast(str, values[2])
            )
        elif kind == "header":
            self._terminal_ui.set_extension_header(values[0])
        elif kind == "footer":
            self._terminal_ui.set_extension_footer(values[0])
        elif kind == "title":
            self._terminal_ui.set_extension_title(cast(str, values[0]))
        elif kind == "indicator":
            self._terminal_ui.set_extension_working_indicator(values[0], values[1])
        elif kind == "hidden-thinking-label":
            self._terminal_ui.set_extension_hidden_thinking_label(
                cast("str | None", values[0])
            )
        elif kind == "autocomplete":
            self._terminal_ui.add_extension_autocomplete_provider(values[0])
        elif kind == "editor-component":
            self._terminal_ui.set_editor_component(values[0])
        elif kind == "listener":
            return self._terminal_ui.add_extension_terminal_input_listener(
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
        self._terminal_ui.set_extension_status(key, text)

    def set_working_message(self, message: str | None = None) -> None:
        self._terminal_ui.set_extension_working_message(message)

    def set_working_visible(self, visible: bool) -> None:
        self._terminal_ui.set_extension_working_visible(visible)

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
        return self._terminal_ui.get_input_text()

    def set_editor_text(self, text: str) -> None:
        self._terminal_ui.set_input_text(text)

    def paste_to_editor(self, text: str) -> None:
        self._terminal_ui.paste_input_text(text)

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
        self._terminal_ui.set_tools_expanded(bool(expanded))

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


_WIDGET_MAX_LINES = 10
_WIDGET_MAX_COUNT = 16
_HEADER_MAX_LINES = 8
_FOOTER_MAX_LINES = 4
# ``_TITLE_MAX_CHARS`` and ``_RESIZE_POLL_SECONDS`` are owned by and imported
# from ``native.terminal_driver`` (which owns the terminal-title write and the
# resize/size lifecycle); the UI reuses the former to cap its cached title
# state and the latter as its resize-polling select timeout.
_INDICATOR_MAX_FRAMES = 32


class _ExtensionChromeTuiHandle:
    """Small Pi-shaped TUI handle passed to extension chrome factories."""

    def __init__(self, ui: "ToolLoopTerminalUi") -> None:
        self._ui = ui

    def requestRender(self, force: bool = False) -> None:  # noqa: N802 - Pi API
        """Request a live repaint without producing a provider turn.

        Pi accepts a ``force`` flag that clears its incremental renderer state.
        Pipy's live-region renderer already repaints the full frame; the flag is
        accepted for API shape and currently needs no distinct handling.
        """

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


def _visible_len_allow_sgr(text: str) -> int:
    """Compatibility export for terminal-screen and TUI characterization tests."""

    return render_visible_len(text)


def _safe_extension_status_key(key: str) -> str | None:
    text = sanitize_label_text(str(key)).strip()
    if not text:
        return None
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in text)
    cleaned = cleaned.strip("-_.")
    return cleaned[:64] or None


class _CustomEntryRendererRunState(Protocol):
    """Live run-state fields read by the custom-entry terminal adapter."""

    @property
    def session_tree(self) -> NativeSessionTree: ...

    @property
    def extension_message_outbox(self) -> list[QueuedUserMessage]: ...

    @property
    def extension_custom_message_outbox(self) -> list[QueuedCustomMessage]: ...

    @property
    def extension_in_agent_turn(self) -> bool: ...


def _route_legacy_custom_message_input(
    content: str,
    options: Mapping[str, object],
    *,
    in_agent_turn: bool,
    enqueue_next_turn: Callable[[ProductContent], None],
    enqueue_steering: Callable[[ProductContent], None],
    enqueue_follow_up: Callable[[ProductContent], None],
    enqueue_prompt: Callable[[ProductContent], None],
) -> None:
    """Preserve established custom-message routing for live and accepted paths."""

    routed = ProductContent(content)
    deliver_as = options.get("deliverAs")
    if deliver_as is None:
        deliver_as = options.get("deliver_as")
    if deliver_as == "nextTurn":
        enqueue_next_turn(routed)
    elif deliver_as == "steer":
        enqueue_steering(routed)
    elif deliver_as in {"followUp", "follow_up"}:
        enqueue_follow_up(routed)
    elif not in_agent_turn and (
        options.get("triggerTurn") is True or options.get("trigger_turn") is True
    ):
        enqueue_prompt(routed)


@dataclass(frozen=True, slots=True, kw_only=True)
class AcceptedCustomMessageSinks:
    """Direct accepted-message sinks; deliberately has no custom outbox."""

    append_durable: Callable[[QueuedCustomMessage], object]
    render_or_diagnose: Callable[[QueuedCustomMessage, object], None]
    enqueue_next_turn: Callable[[ProductContent], None]
    enqueue_steering: Callable[[ProductContent], None]
    enqueue_follow_up: Callable[[ProductContent], None]
    enqueue_prompt: Callable[[ProductContent], None]
    in_agent_turn: Callable[[], bool]

    def deliver(self, message: QueuedCustomMessage) -> None:
        """Dispatch tree, optional render/diagnostic, then coding input."""

        appended = self.append_durable(message)
        if message.display:
            self.render_or_diagnose(message, appended)
        _route_legacy_custom_message_input(
            message.content,
            message.options,
            in_agent_turn=self.in_agent_turn(),
            enqueue_next_turn=self.enqueue_next_turn,
            enqueue_steering=self.enqueue_steering,
            enqueue_follow_up=self.enqueue_follow_up,
            enqueue_prompt=self.enqueue_prompt,
        )


@dataclass(frozen=True, slots=True)
class _CustomRendererProjectionSnapshot:
    messages: Mapping[str, RegisteredMessageRenderer]
    entries: Mapping[str, RegisteredEntryRenderer]


@dataclass(frozen=True, slots=True, kw_only=True)
class _CustomEntryRenderer:
    """Render custom entries and drain extension outboxes into terminal state.

    Renderer operations take one published generation snapshot; the live-state
    protocol retains only the session tree, agent-turn state, and R4a's
    legacy/harness direct-drain outboxes.
    """

    ctl: _CustomEntryRendererRunState
    terminal_ui: "ToolLoopTerminalUi | None"
    coding_input_queue: CodingInputQueue
    coding_effects: CodingEffectCoordinator
    error_stream: TextIO
    generation_snapshot: Callable[[], SessionGenerationSnapshot | None] | None = None

    def _snapshot(self) -> SessionGenerationSnapshot | None:
        provider = self.generation_snapshot
        if provider is None:
            return None
        try:
            return provider()
        except Exception:  # noqa: BLE001 - a failed generation snapshot degrades to no projection
            return None

    def _renderer_projection(self) -> _CustomRendererProjectionSnapshot:
        snapshot = self._snapshot()
        if snapshot is None or (projection := snapshot.generation.projection) is None:
            raise RuntimeError("published extension generation has no projection")
        return _CustomRendererProjectionSnapshot(
            projection.renderers.messages,
            projection.renderers.entries,
        )

    def render_extension_custom_message(
        self,
        custom_type: str,
        data: object | None,
        *,
        width: int,
        expanded: bool,
        stream: TextIO,
        renderer_projection: _CustomRendererProjectionSnapshot | None = None,
    ) -> RenderedCustomEntry:
        # Local import: the render-theme machinery is only needed on the
        # rarely hit custom-entry path, so keep it off this module's hot
        # import path (mirrors the tool-renderer ``_dispatch_render`` sites).
        from pipy_harness.native.chrome import chrome_style_for
        from pipy_harness.native.tool_renderers import build_tool_render_theme

        style = chrome_style_for(stream)
        return render_extension_message(
            (renderer_projection or self._renderer_projection()).messages,
            custom_type,
            data,
            width=width,
            expanded=expanded,
            theme=build_tool_render_theme(style),
        )

    def render_extension_custom_entry(
        self,
        entry: _CustomEntry,
        *,
        width: int,
        expanded: bool,
        stream: TextIO,
        renderer_projection: _CustomRendererProjectionSnapshot | None = None,
    ) -> RenderedCustomEntry | None:
        from pipy_harness.native.chrome import chrome_style_for
        from pipy_harness.native.tool_renderers import build_tool_render_theme

        return render_extension_entry(
            (renderer_projection or self._renderer_projection()).entries,
            _custom_entry_renderer_payload(entry),
            width=width,
            expanded=expanded,
            theme=build_tool_render_theme(chrome_style_for(stream)),
        )

    def add_rendered_custom_entry_to_terminal(
        self,
        entry: _CustomEntry,
        renderer_projection: _CustomRendererProjectionSnapshot | None = None,
    ) -> None:
        terminal_ui = self.terminal_ui
        if terminal_ui is None:
            return
        renderers = renderer_projection or self._renderer_projection()
        rendered = self.render_extension_custom_entry(
            entry,
            width=terminal_ui._driver.size()[0],
            expanded=terminal_ui.tools_expanded,
            stream=terminal_ui.terminal_stream,
            renderer_projection=renderers,
        )
        if rendered is None:
            return
        terminal_ui.add_entry_renderer_component(
            rendered.lines,
            custom_type=entry.custom_type,
            entry=_custom_entry_renderer_payload(entry),
            renderers=renderers.entries,
        )

    def render_custom_message_entry(
        self,
        entry: _CustomMessageEntry,
        *,
        width: int,
        expanded: bool,
        stream: TextIO,
        renderer_projection: _CustomRendererProjectionSnapshot | None = None,
    ) -> RenderedCustomEntry:
        renderers = renderer_projection or self._renderer_projection()
        if entry.custom_type not in renderers.messages:
            return RenderedCustomEntry(tuple(entry.content.splitlines() or [""]), False)
        return self.render_extension_custom_message(
            entry.custom_type,
            _custom_message_renderer_payload(entry),
            width=width,
            expanded=expanded,
            stream=stream,
            renderer_projection=renderers,
        )

    def add_rendered_entry_to_terminal(
        self,
        custom_type: str,
        rendered: RenderedCustomEntry,
        data: object | None,
        renderer_projection: _CustomRendererProjectionSnapshot | None = None,
    ) -> None:
        terminal_ui = self.terminal_ui
        if terminal_ui is None:
            return
        if rendered.styled:
            terminal_ui.add_custom_entry_styled(
                rendered.lines,
                custom_type=custom_type,
                data=data,
                renderers=(renderer_projection or self._renderer_projection()).messages,
            )
        else:
            terminal_ui.add_custom_entry(custom_type, rendered.lines)

    def add_custom_message_entry_to_terminal(
        self,
        entry: _CustomMessageEntry,
        renderer_projection: _CustomRendererProjectionSnapshot | None = None,
    ) -> None:
        terminal_ui = self.terminal_ui
        if terminal_ui is None or not entry.display:
            return
        renderers = renderer_projection or self._renderer_projection()
        rendered = self.render_custom_message_entry(
            entry,
            width=terminal_ui._driver.size()[0],
            expanded=terminal_ui.tools_expanded,
            stream=terminal_ui.terminal_stream,
            renderer_projection=renderers,
        )
        self.add_rendered_entry_to_terminal(
            entry.custom_type,
            rendered,
            _custom_message_renderer_payload(entry),
            renderers,
        )

    def replay_custom_entries_to_terminal(self) -> None:
        if self.terminal_ui is not None:
            renderers = self._renderer_projection()
            for entry in self.ctl.session_tree.get_branch():
                if isinstance(entry, _CustomEntry):
                    self.add_rendered_custom_entry_to_terminal(entry, renderers)
                elif isinstance(entry, _CustomMessageEntry) and entry.display:
                    self.add_custom_message_entry_to_terminal(entry, renderers)

    def redraw_custom_entries_for_active_branch(self) -> None:
        terminal_ui = self.terminal_ui
        if terminal_ui is None or not hasattr(terminal_ui, "redraw_custom_entries"):
            return
        renderers = self._renderer_projection()

        def render_for_redraw(entry: _CustomEntry) -> RenderedCustomEntry | None:
            return self.render_extension_custom_entry(
                entry,
                width=terminal_ui._driver.size()[0],
                expanded=terminal_ui.tools_expanded,
                stream=terminal_ui.terminal_stream,
                renderer_projection=renderers,
            )

        def render_message_for_redraw(
            entry: _CustomMessageEntry,
        ) -> RenderedCustomEntry:
            return self.render_custom_message_entry(
                entry,
                width=terminal_ui._driver.size()[0],
                expanded=terminal_ui.tools_expanded,
                stream=terminal_ui.terminal_stream,
                renderer_projection=renderers,
            )

        terminal_ui.redraw_custom_entries(
            _custom_entry_redraw_rows(
                self.ctl.session_tree.get_branch(),
                render_for_redraw,
                render_message_for_redraw,
                render_metadata=renderers.messages,
                entry_render_metadata=renderers.entries,
            )
        )

    def extension_append_entry(
        self, custom_type: str, data: object | None = None
    ) -> object:
        with self._accepted_coding_effect():
            safe_type = str(custom_type).strip()
            if not is_valid_custom_entry_type(safe_type):
                raise ValueError("invalid custom entry type")
            safe_data = safe_custom_entry_data(data)
            renderers = (
                self._renderer_projection() if self.terminal_ui is not None else None
            )
            with self.coding_effects.lock:
                appended = self.ctl.session_tree.append_custom(safe_type, safe_data)
            if self.terminal_ui is not None:
                self.add_rendered_custom_entry_to_terminal(appended, renderers)
            return appended.id

    def extension_send_message(
        self,
        custom_type: str,
        content: str,
        display: bool,
        options: Mapping[str, object],
        details: object | None = None,
    ) -> object:
        with self._accepted_coding_effect():
            renderers = self._renderer_projection() if display else None
            return self._deliver_custom_message_effects(
                QueuedCustomMessage(custom_type, content, display, details, options),
                renderers,
            )

    def _deliver_custom_message(
        self,
        message: QueuedCustomMessage,
        renderer_projection: _CustomRendererProjectionSnapshot | None = None,
    ) -> object:
        with self._accepted_coding_effect():
            return self._deliver_custom_message_effects(message, renderer_projection)

    def _deliver_custom_message_effects(
        self,
        message: QueuedCustomMessage,
        renderer_projection: _CustomRendererProjectionSnapshot | None = None,
    ) -> object:
        with self.coding_effects.lock:
            appended = self.ctl.session_tree.append_custom_message(
                message.custom_type,
                message.content,
                display=message.display,
                details=message.details,
            )
        if message.display:
            if self.terminal_ui is not None:
                self.add_custom_message_entry_to_terminal(appended, renderer_projection)
            else:
                rendered = self.render_custom_message_entry(
                    appended,
                    width=80,
                    expanded=False,
                    stream=self.error_stream,
                    renderer_projection=renderer_projection,
                )
                lines = "\n".join(str(line) for line in rendered.lines)
                emit_diagnostic(
                    self.terminal_ui,
                    self.error_stream,
                    f"{message.custom_type}:\n{lines}"
                    if lines
                    else message.custom_type,
                )
        _route_legacy_custom_message_input(
            message.content,
            message.options,
            in_agent_turn=self.ctl.extension_in_agent_turn,
            enqueue_next_turn=self.coding_input_queue.enqueue_next_turn_context,
            enqueue_steering=self.coding_input_queue.enqueue_extension_steering,
            enqueue_follow_up=self.coding_input_queue.enqueue_extension_follow_up,
            enqueue_prompt=self.coding_input_queue.enqueue_extension_prompt,
        )
        return appended.id

    @contextmanager
    def _accepted_coding_effect(self) -> Iterator[None]:
        with self.coding_effects.effect() as admitted:
            if not admitted:
                raise ExtensionCapabilityError("coding session is closed")
            yield

    def drain_extension_outboxes(self) -> None:
        """Move one coherent generation's scheduled messages into session queues."""

        if self.generation_snapshot is None:
            self._drain_extension_outboxes_direct(
                self.ctl.extension_message_outbox,
                self.ctl.extension_custom_message_outbox,
            )
            return
        snapshot = self._snapshot()
        if snapshot is None or (projection := snapshot.generation.projection) is None:
            raise RuntimeError("published extension generation has no projection")
        queues = projection.queues
        if queues.message_routing.route_drain(self._deliver_extension_outbox_batch):
            return
        self._drain_extension_outboxes_direct(
            queues.user.storage, queues.custom.storage
        )

    def _drain_extension_outboxes_direct(
        self,
        user_outbox: list[QueuedUserMessage],
        custom_outbox: list[QueuedCustomMessage],
    ) -> None:
        self._deliver_extension_outbox_batch(
            tuple(drain_user_messages(user_outbox)),
            tuple(drain_custom_messages(custom_outbox)),
        )

    def _deliver_extension_outbox_batch(
        self,
        user_messages: tuple[QueuedUserMessage, ...],
        custom_messages: tuple[QueuedCustomMessage, ...],
    ) -> None:
        for message in user_messages:
            self.coding_input_queue.enqueue_extension_prompt(
                ProductContent(message.content)
            )
        for custom_message in custom_messages:
            self._deliver_custom_message(custom_message)


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
    # Single source of truth for editable input, history, undo/redo, paste,
    # completion/menu navigation, rehydration, and queued terminal input. This
    # large dataclass already uses slots: retired field names therefore cannot
    # silently become dead instance attributes beside the narrow projections.
    _editor: EditorState = field(init=False)
    # Single owner for committed history blocks, the live stream buffers, and
    # the Ctrl+O/Ctrl+T view flags (``ui/components/transcript.py``). The
    # facade keeps thin verb delegates and two read-only flag projections.
    _transcript: TranscriptComponent = field(init=False)
    # Single owner for extension chrome values and listener/branch ledgers.
    # The facade retains all locking, factory/component execution, rendering,
    # terminal-title effects, filesystem branch reads, and disposal calls.
    _chrome: ExtensionChromeState = field(init=False)
    available_provider_count: int = 0
    command_names: tuple[str, ...] = TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS
    command_descriptions: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_REPL_COMMAND_DESCRIPTIONS)
    )
    # Max rows shown in the slash-command/autocomplete menu (Pi
    # ``autocompleteMaxVisible``; default 5, clamped 3..20 by the settings
    # getter). Overflow rows scroll behind a "… N more" tail.
    autocomplete_max_visible: int = 5
    # Decoded key strings (e.g. ``"ctrl-g"``) bound by activated extensions via
    # ``api.register_shortcut``. When the editor reads one of these keys it
    # returns the HOTKEY_EXTENSION_SHORTCUT sentinel so the session dispatches
    # the bound handler. Keys the decoder cannot produce (e.g. ``ctrl-.`` on a
    # non-kitty terminal) simply never fire — the registration is still valid.
    extension_shortcut_keys: frozenset[str] = frozenset()
    # Exactly one selector/dialog/custom overlay is active. Terminal I/O,
    # callbacks, extension execution, rendering, and lifecycle effects stay in
    # this facade; the owner holds only synchronous transition state.
    _overlays: OverlayState = field(init=False)
    # Clipboard / drag image paste (Pi Ctrl+V). ``clipboard_image_read`` reads an
    # image from the OS clipboard; ``clipboard_temp_dir`` is an owner-only dir
    # (also registered as an image reference root by the session) where pasted
    # image bytes are written before an ``@image:`` reference is inserted.
    clipboard_image_read: Callable[[], ImageClipboardResult] | None = None
    clipboard_temp_dir: Path | None = None
    _clipboard_image_count: int = 0
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

    def __post_init__(self) -> None:
        self._editor = EditorState()
        self._overlays = OverlayState()
        self._chrome = ExtensionChromeState()
        self._driver = TerminalDriver(self.input_stream, self.terminal_stream)
        self._transcript = TranscriptComponent(
            self._paint_lock,
            self.paint,
            reset_scrollback=self._force_full_redraw,
            frame_width=lambda: self._driver.size()[0],
            render_theme=lambda: build_tool_render_theme(
                chrome_style_for(self.terminal_stream)
            ),
        )

    # Narrow compatibility projections keep the product facade and existing
    # characterized callers stable without duplicating stored editor state.
    @property
    def input_text(self) -> str:
        return self._editor.text

    @input_text.setter
    def input_text(self, value: str) -> None:
        self._editor.text = value

    @property
    def input_cursor(self) -> int | None:
        return self._editor.cursor

    @input_cursor.setter
    def input_cursor(self, value: int | None) -> None:
        self._editor.cursor = value

    @property
    def input_history(self) -> list[str]:
        return self._editor.input_history

    @input_history.setter
    def input_history(self, value: list[str]) -> None:
        self._editor.input_history = value

    @property
    def _history_nav_index(self) -> int | None:
        return self._editor.history_nav_index

    @_history_nav_index.setter
    def _history_nav_index(self, value: int | None) -> None:
        self._editor.history_nav_index = value

    @property
    def _history_draft(self) -> str:
        return self._editor.history_draft

    @_history_draft.setter
    def _history_draft(self, value: str) -> None:
        self._editor.history_draft = value

    @property
    def _undo_stack(self) -> list[tuple[str, int]]:
        return self._editor.undo_stack

    @property
    def _redo_stack(self) -> list[tuple[str, int]]:
        return self._editor.redo_stack

    @property
    def _pending_paste(self) -> str:
        return self._editor.pending_paste

    @_pending_paste.setter
    def _pending_paste(self, value: str) -> None:
        self._editor.pending_paste = value

    @property
    def _pending_initial_text(self) -> str | None:
        return self._editor.pending_initial_text

    @_pending_initial_text.setter
    def _pending_initial_text(self, value: str | None) -> None:
        self._editor.pending_initial_text = value

    @property
    def slash_menu_open(self) -> bool:
        return self._editor.slash_menu_open

    @slash_menu_open.setter
    def slash_menu_open(self, value: bool) -> None:
        self._editor.slash_menu_open = value

    @property
    def slash_menu_selection(self) -> int:
        return self._editor.slash_menu_selection

    @slash_menu_selection.setter
    def slash_menu_selection(self, value: int) -> None:
        self._editor.slash_menu_selection = value

    @property
    def autocomplete_open(self) -> bool:
        return self._editor.autocomplete_open

    @autocomplete_open.setter
    def autocomplete_open(self, value: bool) -> None:
        self._editor.autocomplete_open = value

    @property
    def autocomplete_items(self) -> tuple[CompletionItem, ...]:
        return self._editor.autocomplete_items

    @autocomplete_items.setter
    def autocomplete_items(self, value: tuple[CompletionItem, ...]) -> None:
        self._editor.autocomplete_items = value

    @property
    def autocomplete_selection(self) -> int:
        return self._editor.autocomplete_selection

    @autocomplete_selection.setter
    def autocomplete_selection(self, value: int) -> None:
        self._editor.autocomplete_selection = value

    @property
    def autocomplete_mode(self) -> CompletionMode:
        return self._editor.autocomplete_mode

    @autocomplete_mode.setter
    def autocomplete_mode(self, value: CompletionMode) -> None:
        self._editor.autocomplete_mode = value

    @property
    def autocomplete_token_start(self) -> int:
        return self._editor.autocomplete_token_start

    @autocomplete_token_start.setter
    def autocomplete_token_start(self, value: int) -> None:
        self._editor.autocomplete_token_start = value

    @property
    def autocomplete_prefix(self) -> str:
        return self._editor.autocomplete_prefix

    @autocomplete_prefix.setter
    def autocomplete_prefix(self, value: str) -> None:
        self._editor.autocomplete_prefix = value

    @property
    def _autocomplete_active_provider(self) -> object | None:
        return self._editor.autocomplete_active_provider

    @property
    def _autocomplete_provider_factories(self) -> list[object]:
        return self._editor.autocomplete_provider_factories

    # Overlay/chrome projections are direct views into slotted owners. They
    # preserve characterized facade access without a second stored copy; an
    # ``*_open`` write changes the one active-overlay discriminator, so two
    # overlays cannot become renderable simultaneously.
    @property
    def tree_selector_filter(self) -> str:
        # Read by the session-side `/tree` handler after the selector closes
        # (`repl/session_commands.py` persists the last filter mode); the rest
        # of the tree-selector state is owned by `ui/components/tree_selector`.
        return self._overlays.tree_filter

    @property
    def custom_overlay_open(self) -> bool:
        return self._overlays.is_open("custom")

    @custom_overlay_open.setter
    def custom_overlay_open(self, value: bool) -> None:
        if value:
            self._overlays.supersede("custom")
        else:
            self._overlays.close("custom")

    @property
    def extension_working_message(self) -> str | None:
        return self._chrome.working_message

    @extension_working_message.setter
    def extension_working_message(self, value: str | None) -> None:
        self._chrome.working_message = value

    @property
    def extension_working_visible(self) -> bool:
        return self._chrome.working_visible

    @extension_working_visible.setter
    def extension_working_visible(self, value: bool) -> None:
        self._chrome.working_visible = value

    @property
    def extension_status(self) -> dict[str, str]:
        return self._chrome.statuses

    @extension_status.setter
    def extension_status(self, value: dict[str, str]) -> None:
        self._chrome.statuses = value

    @property
    def extension_widgets_above(self) -> dict[str, ChromeRegion]:
        return self._chrome.widgets_above

    @extension_widgets_above.setter
    def extension_widgets_above(self, value: dict[str, ChromeRegion]) -> None:
        self._chrome.widgets_above = value

    @property
    def extension_widgets_below(self) -> dict[str, ChromeRegion]:
        return self._chrome.widgets_below

    @extension_widgets_below.setter
    def extension_widgets_below(self, value: dict[str, ChromeRegion]) -> None:
        self._chrome.widgets_below = value

    @property
    def extension_header(self) -> ChromeRegion | None:
        return self._chrome.header

    @extension_header.setter
    def extension_header(self, value: ChromeRegion | None) -> None:
        self._chrome.header = value

    @property
    def extension_footer(self) -> ChromeRegion | None:
        return self._chrome.footer

    @extension_footer.setter
    def extension_footer(self, value: ChromeRegion | None) -> None:
        self._chrome.footer = value

    @property
    def extension_title(self) -> str | None:
        return self._chrome.title

    @extension_title.setter
    def extension_title(self, value: str | None) -> None:
        self._chrome.title = value

    @property
    def extension_indicator_frames(self) -> tuple[str, ...] | None:
        return self._chrome.indicator_frames

    @extension_indicator_frames.setter
    def extension_indicator_frames(self, value: tuple[str, ...] | None) -> None:
        self._chrome.indicator_frames = value

    @property
    def extension_indicator_interval_ms(self) -> float | None:
        return self._chrome.indicator_interval_ms

    @extension_indicator_interval_ms.setter
    def extension_indicator_interval_ms(self, value: float | None) -> None:
        self._chrome.indicator_interval_ms = value

    @property
    def _extension_footer_factory(self) -> object | None:
        return self._chrome.footer_factory

    @_extension_footer_factory.setter
    def _extension_footer_factory(self, value: object | None) -> None:
        self._chrome.footer_factory = value

    @property
    def _extension_footer_branch(self) -> str | None:
        return self._chrome.footer_branch

    @_extension_footer_branch.setter
    def _extension_footer_branch(self, value: str | None) -> None:
        self._chrome.footer_branch = value

    @property
    def _footer_branch_callbacks(self) -> dict[int, Callable[[], object]]:
        return self._chrome.footer_branch_callbacks

    @_footer_branch_callbacks.setter
    def _footer_branch_callbacks(self, value: dict[int, Callable[[], object]]) -> None:
        self._chrome.footer_branch_callbacks = value

    @property
    def _footer_branch_callback_next_id(self) -> int:
        return self._chrome.footer_branch_callback_next_id

    @_footer_branch_callback_next_id.setter
    def _footer_branch_callback_next_id(self, value: int) -> None:
        self._chrome.footer_branch_callback_next_id = value

    @property
    def _footer_branch_slots(self) -> tuple[int, ...]:
        return self._chrome.footer_branch_slots

    @_footer_branch_slots.setter
    def _footer_branch_slots(self, value: tuple[int, ...]) -> None:
        self._chrome.footer_branch_slots = value

    @property
    def _footer_branch_rebuild_slots(self) -> tuple[int, ...] | None:
        return self._chrome.footer_branch_rebuild_slots

    @_footer_branch_rebuild_slots.setter
    def _footer_branch_rebuild_slots(self, value: tuple[int, ...] | None) -> None:
        self._chrome.footer_branch_rebuild_slots = value

    @property
    def _footer_branch_rebuild_index(self) -> int:
        return self._chrome.footer_branch_rebuild_index

    @_footer_branch_rebuild_index.setter
    def _footer_branch_rebuild_index(self, value: int) -> None:
        self._chrome.footer_branch_rebuild_index = value

    @property
    def _footer_branch_rebuild_active_ids(self) -> frozenset[int]:
        return self._chrome.footer_branch_rebuild_active_ids

    @_footer_branch_rebuild_active_ids.setter
    def _footer_branch_rebuild_active_ids(self, value: frozenset[int]) -> None:
        self._chrome.footer_branch_rebuild_active_ids = value

    @property
    def _footer_branch_rebuild_new_slots(self) -> list[int]:
        return self._chrome.footer_branch_rebuild_new_slots

    @_footer_branch_rebuild_new_slots.setter
    def _footer_branch_rebuild_new_slots(self, value: list[int]) -> None:
        self._chrome.footer_branch_rebuild_new_slots = value

    @property
    def _footer_branch_rebuild_fire_ids(self) -> list[int]:
        return self._chrome.footer_branch_rebuild_fire_ids

    @_footer_branch_rebuild_fire_ids.setter
    def _footer_branch_rebuild_fire_ids(self, value: list[int]) -> None:
        self._chrome.footer_branch_rebuild_fire_ids = value

    @property
    def _footer_branch_last_check(self) -> float:
        return self._chrome.footer_branch_last_check

    @_footer_branch_last_check.setter
    def _footer_branch_last_check(self, value: float) -> None:
        self._chrome.footer_branch_last_check = value

    @property
    def _footer_branch_check_interval(self) -> float:
        return self._chrome.footer_branch_check_interval

    @_footer_branch_check_interval.setter
    def _footer_branch_check_interval(self, value: float) -> None:
        self._chrome.footer_branch_check_interval = value

    @property
    def _extension_terminal_input_listeners(self) -> dict[int, Callable[[str], object]]:
        return self._chrome.terminal_input_listeners

    @_extension_terminal_input_listeners.setter
    def _extension_terminal_input_listeners(
        self, value: dict[int, Callable[[str], object]]
    ) -> None:
        self._chrome.terminal_input_listeners = value

    @property
    def _extension_terminal_input_next_id(self) -> int:
        return self._chrome.terminal_input_next_id

    @_extension_terminal_input_next_id.setter
    def _extension_terminal_input_next_id(self, value: int) -> None:
        self._chrome.terminal_input_next_id = value

    @property
    def _extension_terminal_input_last_replaced(self) -> bool:
        return self._chrome.terminal_input_last_replaced

    @_extension_terminal_input_last_replaced.setter
    def _extension_terminal_input_last_replaced(self, value: bool) -> None:
        self._chrome.terminal_input_last_replaced = value

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
        self._editor.begin_line()
        if self._custom_editor_active:
            self._set_custom_editor_text(self.input_text)
        self.paint()
        fd = self.input_stream.fileno()
        with self._driver.raw_mode():
            while True:
                key = self._read_key_polling_resize(fd)
                if key is None:
                    return ""
                key = self._apply_extension_terminal_input_listeners(key)
                if self._custom_editor_active:
                    submitted = self._handle_custom_editor_key(key)
                    if submitted is not None:
                        if self._custom_editor_exit_requested:
                            self._custom_editor_exit_requested = False
                            self._reset_line_editor_state()
                            self.paint()
                            return ""
                        self._editor.record_history(submitted)
                        self._editor.reset_line_editor_state()
                        self.paint()
                        return f"{submitted}\n"
                    continue
                if key is None:
                    self.paint()
                    continue
                if key == "enter":
                    if self.autocomplete_open:
                        # Enter accepts the highlighted completion (Pi: Enter/Tab
                        # accept) and keeps editing rather than submitting.
                        self._accept_autocomplete_selection()
                        continue
                    if self.slash_menu_open and self._filtered_commands():
                        matches = self._filtered_commands()
                        if self.input_text not in matches:
                            self._accept_slash_menu_selection()
                    submitted = self._editor.submit_line()
                    self.paint()
                    return f"{submitted}\n"
                if key == "ctrl-c":
                    raise KeyboardInterrupt
                if key == "ctrl-d":
                    if not self.input_text:
                        return ""
                    continue
                if self._matches_keybinding(key, "app.editor.external"):
                    edited = self._run_configured_external_editor(self.input_text)
                    if edited is not None:
                        self._editor.snapshot_for_undo()
                        self._editor.reset_history_nav()
                        self._editor.set_buffer(edited)
                        self._editor.close_slash_menu()
                        self._editor.close_autocomplete()
                    self.paint()
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
                    self._editor.preserve_for_next_line()
                    return (
                        f"{HOTKEY_MODEL_CYCLE_PREV}\n"
                        if key == "shift-ctrl-p"
                        else f"{HOTKEY_MODEL_CYCLE_NEXT}\n"
                    )
                if key == "shift-tab":
                    # app.thinking.cycle: cycle the reasoning level. Dispatched
                    # by the session without a provider turn; the partially-typed
                    # buffer is preserved into the next prompt.
                    self._editor.preserve_for_next_line()
                    return f"{HOTKEY_THINKING_CYCLE}\n"
                if key in {"ctrl-o", "ctrl-t"}:
                    # app.tools.expand (ctrl+o) / app.thinking.toggle (ctrl+t):
                    # renderer view-flag toggles dispatched by the session (so the
                    # thinking-visibility setting can be persisted and a status
                    # shown). The partially-typed buffer is preserved.
                    self._editor.preserve_for_next_line()
                    return (
                        f"{HOTKEY_TOGGLE_TOOLS}\n"
                        if key == "ctrl-o"
                        else f"{HOTKEY_TOGGLE_THINKING}\n"
                    )
                if key == "paste":
                    self._insert_paste(self._editor.consume_paste())
                    self.paint()
                    continue
                if key == "ctrl-v":
                    # app.clipboard.pasteImage: read an image from the OS
                    # clipboard, write it to an owner-only temp file, and insert
                    # an @image: reference. No provider turn.
                    self._paste_clipboard_image()
                    self.paint()
                    continue
                if key in self.extension_shortcut_keys:
                    # An activated extension bound this key via
                    # api.register_shortcut. Preserve any partially-typed input
                    # into the next prompt (like the app hotkeys) and hand the
                    # session the sentinel so it dispatches the bound handler.
                    self._editor.preserve_for_next_line()
                    return f"{HOTKEY_EXTENSION_SHORTCUT_PREFIX}{key}\n"
                if key == "backspace":
                    self._delete_before_cursor()
                    self.paint()
                    continue
                if key == "esc":
                    if self.slash_menu_open:
                        self._editor.slash_menu_open = False
                        self.paint()
                    elif self.autocomplete_open:
                        self._close_autocomplete()
                        self.paint()
                    continue
                if key in {"up", "down"}:
                    if self.slash_menu_open:
                        self._navigate_slash_menu(key)
                    elif self.autocomplete_open:
                        self._navigate_autocomplete(key)
                    else:
                        self._navigate_history(key)
                    continue
                if key == "tab":
                    if self.slash_menu_open and self._filtered_commands():
                        self._accept_slash_menu_selection()
                    elif self.autocomplete_open:
                        self._accept_autocomplete_selection()
                    else:
                        self._attempt_path_completion()
                        self.paint()
                    continue
                if key in {"left", "right", "home", "end"}:
                    self._move_input_cursor(key)
                    self.paint()
                    continue
                if key == "ctrl-u":
                    self._kill_to_line_start()
                    self.paint()
                    continue
                if key == "ctrl-z":
                    self._undo_edit()
                    self.paint()
                    continue
                if key == "ctrl-y":
                    self._redo_edit()
                    self.paint()
                    continue
                if key.isprintable() and (
                    len(key) == 1 or self._extension_terminal_input_last_replaced
                ):
                    self._insert_input_text(key)
                    self.paint()

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
                        self._editor.consume_paste()
                    continue
                command_only = accept_commands and not accept_queue
                # In command-only mode, preserve the old "ignore random typing"
                # behavior until the user explicitly starts a local command.
                if command_only and not self.input_text and key not in {"/", "!"}:
                    if key == "paste":
                        self._editor.consume_paste()
                    continue
                # accept_queue / accept_commands: a mid-turn editor for
                # steering/follow-up and/or local commands.
                if key == "enter":
                    if self.autocomplete_open:
                        self._accept_autocomplete_selection()
                        continue
                    if self.slash_menu_open and self._filtered_commands():
                        matches = self._filtered_commands()
                        if self.input_text not in matches:
                            self._accept_slash_menu_selection()
                    text = self.input_text
                    self._reset_mid_turn_input()
                    if not text.strip():
                        self.paint()
                        continue
                    # A recognized local command (`/…` or `!…`) is never queued
                    # for the provider: like Pi's editor, Enter runs it
                    # immediately rather than steering. It interrupts the turn
                    # and is handed to the session loop to dispatch locally.
                    if self._submitted_text_is_local_command(text):
                        self._editor.set_pending_command(text)
                        abort_event.set()
                        self.paint()
                        return TURN_LOCAL_COMMAND
                    if command_only:
                        self.paint()
                        continue
                    self.enqueue_steering(text)
                    abort_event.set()
                    self.paint()
                    return TURN_STEERED
                if key == "alt-enter":
                    if command_only:
                        continue
                    text = self.input_text
                    self._reset_mid_turn_input()
                    self.enqueue_follow_up(text)
                    self.paint()
                    continue
                if key == "alt-up":
                    if command_only:
                        continue
                    self.restore_pending_to_editor()
                    self.paint()
                    continue
                if key == "paste":
                    if command_only:
                        self._editor.consume_paste()
                        continue
                    self._insert_paste(self._editor.consume_paste())
                    self.paint()
                    continue
                if key == "backspace":
                    self._delete_before_cursor()
                    self.paint()
                    continue
                if key in {"up", "down"}:
                    if self.slash_menu_open:
                        self._navigate_slash_menu(key)
                    elif self.autocomplete_open:
                        self._navigate_autocomplete(key)
                    continue
                if key == "tab":
                    if self.slash_menu_open and self._filtered_commands():
                        self._accept_slash_menu_selection()
                    elif self.autocomplete_open:
                        self._accept_autocomplete_selection()
                    elif not command_only:
                        self._attempt_path_completion()
                    self.paint()
                    continue
                if key in {"left", "right", "home", "end"}:
                    self._move_input_cursor(key)
                    self.paint()
                    continue
                if key == "ctrl-u":
                    self._kill_to_line_start()
                    self.paint()
                    continue
                if key == "ctrl-z":
                    self._undo_edit()
                    self.paint()
                    continue
                if key == "ctrl-y":
                    self._redo_edit()
                    self.paint()
                    continue
                if len(key) == 1 and key.isprintable():
                    self._insert_input_text(key)
                    self.paint()
            return TURN_SETTLED

    def _reset_mid_turn_input(self) -> None:
        self._editor.reset_mid_turn_input()

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
                    self._editor.consume_paste()
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
                    self._editor.consume_paste()
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
                    self._editor.consume_paste()
                    continue
                closed = dialog.handle_key(key)
                if closed is not None:
                    return closed.action

    def set_input_text(self, text: str) -> None:
        """Pre-fill the current or next ``read_line`` prompt with ``text``.

        Used by ``/tree`` and extension UI helpers to rehydrate the editor with
        selected text so the user can edit it into a new branch or follow-up.
        """

        value = str(text)
        if self._custom_editor_active:
            self._set_custom_editor_text(value)
        self._editor.stage_initial_text(value)

    def get_input_text(self) -> str:
        """Return the current core editor text, including pending prefill."""

        if self._custom_editor_active:
            return self._custom_editor_text()
        if self._pending_initial_text is not None:
            return self._pending_initial_text
        return self.input_text

    def set_editor_component(self, factory: object | None) -> None:
        """Install or clear a live extension custom editor component.

        Pi calls ``factory(tui, theme, keybindings)`` and swaps the returned
        editor into the main editor container. Pipy keeps the same ownership
        boundary with a small duck-typed adapter instead of a Pi TUI port:
        trusted extension components may render rows, consume decoded keys, and
        submit through wired callbacks. Bad factories fail closed to the built-in
        editor, and clearing preserves the component's current text.
        """

        current_text = self.get_input_text()
        self._custom_editor_submitted = None
        self._custom_editor_action = None
        self._custom_editor_changed_text = None
        self._custom_editor_exit_requested = False
        if factory is None:
            self._custom_editor_factory = None
            self._custom_editor_component = None
            self._custom_editor_active = False
            self.set_input_text(current_text)
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
            self._forward_autocomplete_to_custom_editor(component)
        self.paint()

    def get_editor_component(self) -> object | None:
        return self._custom_editor_factory

    def _wire_custom_editor_component(self, component: object) -> None:
        def submit(value: object | None = None) -> None:
            text = self._custom_editor_text() if value is None else str(value)
            self._custom_editor_submitted = text
            self._editor.set_buffer(text)

        def change(value: object | None = None) -> None:
            text = self._custom_editor_text() if value is None else str(value)
            self._custom_editor_changed_text = text
            self._editor.set_buffer(text)

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

    def _forward_autocomplete_to_custom_editor(self, component: object) -> None:
        setter = getattr(component, "set_autocomplete_provider", None) or getattr(
            component, "setAutocompleteProvider", None
        )
        if not callable(setter) or not self._autocomplete_provider_factories:
            return
        try:
            setter(self._autocomplete_provider())
        except Exception:  # noqa: BLE001 - fail-soft extension UI adapter
            pass

    def _custom_editor_text(self) -> str:
        component = self._custom_editor_component
        if component is None:
            return self.input_text
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
        return self.input_text

    def _set_custom_editor_text(self, text: str) -> None:
        component = self._custom_editor_component
        self._editor.set_buffer(str(text))
        if component is None:
            return
        for name in ("set_text", "setText"):
            setter = getattr(component, name, None)
            if callable(setter):
                try:
                    setter(self.input_text)
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
            self._editor.set_buffer(self._custom_editor_text())
            if action in {
                "app.model.cycleForward",
                "app.model.cycleBackward",
                "app.model.select",
                "app.thinking.cycle",
                "app.tools.expand",
                "app.thinking.toggle",
            }:
                if self.input_text:
                    self._editor.pending_initial_text = self.input_text
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
                    self.enqueue_follow_up(text)
                self._editor.clear_initial_text()
                self._set_custom_editor_text("")
                return None
            if action == "app.message.dequeue":
                self.restore_pending_to_editor()
                return None
            if action == "app.clipboard.pasteImage":
                self._paste_clipboard_image()
                return None
            if action == "app.interrupt":
                self._editor.clear_initial_text()
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
            self._editor.clear_initial_text()
            self._set_custom_editor_text("")
            return submitted
        self._editor.set_buffer(self._custom_editor_text())
        self.paint()
        return None

    def paste_input_text(self, text: str) -> None:
        """Paste text into the core editor through the paste path.

        Pi's extension ``pasteToEditor`` routes text through bracketed-paste
        handling rather than plain replacement. Pipy keeps the same live-editor
        semantics by inserting the literal text at the current cursor while
        preserving surrounding draft text and pasted newlines.
        """

        self._editor.clear_initial_text()
        self._insert_paste(str(text))

    def run_tree_selector(
        self,
        *,
        build_rows: Callable[[str], Sequence["TreeSelectorRow"]],
        filter_modes: Sequence[str],
        initial_filter: str,
        on_label_toggle: Callable[[str], None],
    ) -> str | None:
        """Drive the interactive ``/tree`` selector; return a chosen entry id.

        ``build_rows(filter_mode)`` returns the visible rows for a filter;
        up/down move the highlight, ``Ctrl-O`` cycles the filter mode, ``L``
        (Shift-L) toggles a label on the highlighted entry via
        ``on_label_toggle``, ``Enter`` selects the highlighted entry, and
        ``Esc``/``Ctrl-C``/``Ctrl-D``/EOF cancel. Runs no provider turn and no
        model-visible tool call; the caller applies the chosen entry's
        selection semantics afterward.
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
                    self._editor.consume_paste()
                    continue
                closed = selector.handle_key(key)
                if closed is not None:
                    return closed.entry_id

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

    def set_extension_status(self, key: str, text: str | None) -> None:
        """Set or clear an extension status row in the live frame."""

        safe_key = _safe_extension_status_key(key)
        if safe_key is None:
            return
        with self._paint_lock:
            self._chrome.set_status(
                safe_key, None if text is None else sanitize_label_text(str(text))
            )
        self.paint()

    def _chrome_theme(self) -> object:
        return build_tool_render_theme(chrome_style_for(self.terminal_stream))

    @staticmethod
    def _call_chrome_factory(
        source: object, args: tuple[object, ...], legacy_args: tuple[object, ...]
    ) -> object:
        """Call a chrome factory using Pi-shaped args when its arity allows it."""

        try:
            sig = inspect.signature(cast(Callable[..., object], source))
        except (TypeError, ValueError):
            return cast(Callable[..., object], source)(*args)
        try:
            sig.bind(*args)
        except TypeError:
            sig.bind(*legacy_args)
            return cast(Callable[..., object], source)(*legacy_args)
        return cast(Callable[..., object], source)(*args)

    def _build_region(
        self, source: object, *, footer_data: object | None, max_lines: int
    ) -> ChromeRegion | None:
        """Build a region by rendering ``source`` at the current width.

        A callable ``source`` is a factory (built once); a bare component object
        (callable ``render``) is retained directly. BOTH are reactive — their
        ``render(width)`` is re-called for each frame, their optional
        ``invalidate()`` runs on resize, and ``dispose()`` runs on replace/clear.
        A ``str``/``Sequence[str]`` source is static."""
        width, _height = self._driver.size()
        component: object | None = None
        is_factory = False
        render_source: object = source
        if callable(source) and not isinstance(source, (str, bytes, bytearray)):
            theme = self._chrome_theme()
            tui_handle = _ExtensionChromeTuiHandle(self)
            try:
                if footer_data is not None:
                    component = self._call_chrome_factory(
                        source, (tui_handle, theme, footer_data), (theme, footer_data)
                    )
                else:
                    component = self._call_chrome_factory(
                        source, (tui_handle, theme), (theme,)
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
            # A bare ChromeComponent object: reactive + lifecycle-managed.
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
    def _dispose_region(region: ChromeRegion | None) -> None:
        if region is None or region.component is None:
            return
        dispose = getattr(region.component, "dispose", None)
        if callable(dispose):
            try:
                dispose()
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - dispose must not break paint
                pass

    def set_extension_widget(
        self, key: str, content: object, *, placement: str = "above_editor"
    ) -> None:
        safe_key = _safe_extension_status_key(key)
        if safe_key is None:
            return
        target, other = self._chrome.widget_maps(placement)
        with self._paint_lock:
            if (
                content is not None
                and safe_key not in target
                and len(target) >= _WIDGET_MAX_COUNT
            ):
                return
            self._dispose_region(target.get(safe_key))
            self._dispose_region(other.pop(safe_key, None))
            if content is None:
                target.pop(safe_key, None)
            else:
                region = self._build_region(
                    content, footer_data=None, max_lines=_WIDGET_MAX_LINES
                )
                if region is None:
                    target.pop(safe_key, None)
                else:
                    target[safe_key] = region
        self.paint()

    def _detect_extension_footer_branch(self) -> str | None:
        """Return the current git branch label for live footer data."""

        candidate: Path | None = self.cwd
        while candidate is not None and candidate != candidate.parent:
            head = candidate / ".git" / "HEAD"
            try:
                text = head.read_text(encoding="utf-8")
            except OSError:
                candidate = candidate.parent
                continue
            text = text.strip()
            if text.startswith("ref: refs/heads/"):
                return text.split("refs/heads/", 1)[1]
            if text:
                return "detached"
            return None
        return None

    def register_footer_branch_change_callback(
        self, callback: Callable[[], object]
    ) -> Callable[[], None]:
        """Register a Pi-shaped footer branch-change callback."""

        # Reentrancy matters here: a footer factory may call onBranchChange
        # while _build_region already holds this lock during set/rebuild.
        with self._paint_lock:
            generation, callback_id = self._chrome.register_footer_branch_callback(
                callback
            )

        disposed = False

        def dispose() -> None:
            nonlocal disposed
            if disposed:
                return
            disposed = True
            with self._paint_lock:
                self._chrome.remove_footer_branch_callback(generation, callback_id)

        return dispose

    def _footer_data_snapshot(self) -> FooterData:
        branch = self._detect_extension_footer_branch()
        self._chrome.footer_branch = branch
        return FooterData(
            git_branch=branch,
            extension_statuses=dict(self.extension_status),
            available_provider_count=int(self.available_provider_count or 0),
            branch_change_registrar=self.register_footer_branch_change_callback,
        )

    def _clear_footer_branch_callbacks(self) -> None:
        self._chrome.clear_footer_branch_callbacks()

    def _refresh_extension_footer_branch(self, *, force: bool = False) -> None:
        factory = self._chrome.footer_factory
        if factory is None:
            return
        now = time.monotonic()
        if (
            not force
            and now - self._chrome.footer_branch_last_check
            < self._chrome.footer_branch_check_interval
        ):
            return
        self._chrome.footer_branch_last_check = now
        branch = self._detect_extension_footer_branch()
        if not force and branch == self._chrome.footer_branch:
            return
        with self._paint_lock:
            self._chrome.begin_footer_rebuild(branch)
            self._dispose_region(self._chrome.footer)
            try:
                self._chrome.footer = self._build_region(
                    factory,
                    footer_data=FooterData(
                        git_branch=branch,
                        extension_statuses=dict(self.extension_status),
                        available_provider_count=int(
                            self.available_provider_count or 0
                        ),
                        branch_change_registrar=self.register_footer_branch_change_callback,
                    ),
                    max_lines=_FOOTER_MAX_LINES,
                )
                callbacks = self._chrome.finish_footer_rebuild()
            finally:
                self._chrome.abort_footer_rebuild()
        for callback in callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001 - one bad footer callback must not stop repaints
                continue
        self.paint()

    def poll_extension_footer_branch(self) -> None:
        """Check for branch changes for tests and live input-loop ticks."""

        self._refresh_extension_footer_branch(force=False)

    def set_extension_header(self, factory: object | None) -> None:
        with self._paint_lock:
            self._dispose_region(self._chrome.header)
            if factory is None:
                self._chrome.header = None
            else:
                self._chrome.header = self._build_region(
                    factory, footer_data=None, max_lines=_HEADER_MAX_LINES
                )
        self.paint()

    def set_extension_footer(
        self, factory: object | None, footer_data: object | None = None
    ) -> None:
        with self._paint_lock:
            self._dispose_region(self._chrome.footer)
            self._clear_footer_branch_callbacks()
            self._chrome.footer_factory = factory
            if factory is None:
                self._chrome.footer = None
                self._chrome.footer_branch = None
            else:
                fd = (
                    footer_data
                    if footer_data is not None
                    else self._footer_data_snapshot()
                )
                if isinstance(fd, FooterData):
                    # Seed from the same detector used by the poller so the
                    # first poll does not rebuild solely because an external
                    # driver formatted its snapshot differently.
                    self._chrome.footer_branch = self._detect_extension_footer_branch()
                self._chrome.footer = self._build_region(
                    factory, footer_data=fd, max_lines=_FOOTER_MAX_LINES
                )
        self.paint()

    def set_extension_title(self, title: str | None) -> None:
        with self._paint_lock:
            if title is None:
                self._chrome.title = None
                self._driver.restore_title()
            else:
                self._driver.push_title()
                self._chrome.title = sanitize_label_text(str(title))[:_TITLE_MAX_CHARS]
                self._driver.write_title(self._chrome.title)
        # title is OS-level; no frame repaint needed.

    def set_extension_working_indicator(
        self, frames: object, interval_ms: object
    ) -> None:
        with self._paint_lock:
            cleaned: tuple[str, ...] | None = None
            replace_frames = frames is None
            if frames is not None:
                try:
                    cleaned = tuple(
                        sanitize_label_text(str(f))
                        for f in list(cast(Iterable[object], frames))[
                            :_INDICATOR_MAX_FRAMES
                        ]
                    )
                    replace_frames = True
                except (TypeError, ValueError):
                    # A non-iterable / bad frames leaves the current indicator
                    # unchanged rather than raising into the extension handler.
                    pass
            try:
                interval = (
                    None
                    if interval_ms is None
                    else max(10.0, float(cast(Any, interval_ms)))
                )
            except (TypeError, ValueError):
                interval = None
            self._chrome.set_indicator(
                frames=cleaned,
                interval_ms=interval,
                replace_frames=replace_frames,
            )
        self.paint()

    def add_extension_terminal_input_listener(
        self, handler: Callable[[str], object]
    ) -> Callable[[], None]:
        """Register a Pi-shaped live terminal-input listener.

        The returned disposer is idempotent. Listener failures are handled by
        :meth:`_apply_extension_terminal_input_listeners` so a bad extension
        cannot break the editor loop.
        """

        if not callable(handler):
            return lambda: None
        generation, listener_id = self._chrome.register_terminal_input_listener(handler)

        def dispose() -> None:
            self._chrome.remove_terminal_input_listener(generation, listener_id)

        return dispose

    def _apply_extension_terminal_input_listeners(self, key: str) -> str | None:
        self._chrome.terminal_input_last_replaced = False
        if not self._chrome.terminal_input_listeners:
            return key
        current = key
        for handler in tuple(self._chrome.terminal_input_listeners.values()):
            try:
                result = handler(current)
            except Exception:  # noqa: BLE001 - extension hooks fail soft
                continue
            consume = False
            replacement: object = None
            has_replacement = False
            if isinstance(result, Mapping):
                consume = bool(result.get("consume"))
                if "data" in result:
                    replacement = result.get("data")
                    has_replacement = True
            elif result is not None:
                consume = bool(getattr(result, "consume", False))
                if hasattr(result, "data"):
                    replacement = getattr(result, "data")
                    has_replacement = True
            if consume:
                return None
            if has_replacement:
                current = "" if replacement is None else str(replacement)
                self._chrome.terminal_input_last_replaced = True
        if current == "":
            return None
        return current

    def clear_extension_chrome(
        self,
        *,
        retirement_scope: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> None:
        """Detach, dispose unlocked, then retire all retained extension chrome."""

        # Reading custom text can call trusted extension code. It therefore
        # precedes the short state-detach section and holds no paint lock.
        had_custom_editor = self._custom_editor_active
        current_text = self.get_input_text() if had_custom_editor else None
        with self._paint_lock:
            regions = self._chrome.detach_generation_for_disposal()
            custom_editor = self._custom_editor_component if had_custom_editor else None
            self._editor.autocomplete_provider_factories.clear()
            self._editor.close_autocomplete()
            self._custom_editor_factory = None
            self._custom_editor_component = None
            self._custom_editor_active = False

        dispose_scope = retirement_scope() if retirement_scope else nullcontext()
        try:
            # Disposal is trusted extension code. No paint/owner/sink guard is
            # held, and an acceptance reconcile supplies an explicit route that
            # keeps synchronous reentrant registrations retiring.
            with dispose_scope:
                for region in regions:
                    self._dispose_region(region)
                if custom_editor is not None:
                    dispose = getattr(custom_editor, "dispose", None)
                    if callable(dispose):
                        try:
                            dispose()
                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except BaseException:  # noqa: BLE001 - extension cleanup
                            pass
        finally:
            with self._paint_lock:
                # Clear direct-UI registrations made during disposal while they
                # still carry the retiring id, then advance to a fresh id.
                self._chrome.retire_generation()
                self._editor.autocomplete_provider_factories.clear()
                self._editor.close_autocomplete()
                self._custom_editor_factory = None
                self._custom_editor_component = None
                self._custom_editor_active = False
                if had_custom_editor:
                    assert current_text is not None
                    self._editor.set_buffer(current_text)
                    self._editor.pending_initial_text = current_text
                self._transcript.reset_hidden_thinking_label()
                self._driver.restore_title()
        self.paint()

    def reconcile_extension_chrome(
        self,
        snapshot: ExtensionChromeSnapshot,
        *,
        retirement_scope: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> dict[int, Callable[[], None]]:
        """Replace accepted chrome, explicitly routing retiring disposal writes."""

        self.clear_extension_chrome(retirement_scope=retirement_scope)
        for key, content, placement in snapshot.widgets:
            self.set_extension_widget(key, content, placement=placement)
        self.set_extension_header(snapshot.header)
        self.set_extension_footer(snapshot.footer)
        if snapshot.title is not None:
            self.set_extension_title(snapshot.title)
        self.set_extension_working_indicator(
            snapshot.indicator_frames, snapshot.indicator_interval_ms
        )
        listener_disposers = {
            listener_id: self.add_extension_terminal_input_listener(handler)
            for listener_id, handler in snapshot.terminal_input_listeners
        }
        for factory in snapshot.autocomplete_providers:
            self.add_extension_autocomplete_provider(factory)
        self.set_editor_component(snapshot.editor_component)
        self.set_extension_hidden_thinking_label(snapshot.hidden_thinking_label)
        return listener_disposers

    def set_extension_working_message(self, message: str | None = None) -> None:
        """Set the sticky working label used by future provider turns."""

        with self._paint_lock:
            self._chrome.set_working_message(
                None if message is None else sanitize_label_text(str(message))
            )
        self.paint()

    def set_extension_working_visible(self, visible: bool) -> None:
        """Show or hide the sticky working row for future provider turns."""

        with self._paint_lock:
            self._chrome.set_working_visible(bool(visible))
            if not self._chrome.working_visible:
                self._transcript.discard_working_text()
        self.paint()

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
            consume_paste=self._editor.consume_paste,
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
    # flags live on ``self._transcript`` (ui/components/transcript.py). The
    # facade keeps one thin delegate per verb so the renderer adapters and the
    # session keep their established call surface until slices 15/23 repoint
    # them onto the component directly.

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

    def clear_working(self) -> None:
        self._transcript.clear_working()

    def append_assistant(self, chunk: str) -> None:
        self._transcript.append_assistant(chunk)

    def settle_assistant(self, final_text: str = "") -> None:
        self._transcript.settle_assistant(final_text)

    def show_operation_aborted(self) -> None:
        self._transcript.show_operation_aborted()

    def append_reasoning(self, chunk: str) -> None:
        self._transcript.append_reasoning(chunk)

    def set_thinking_hidden(self, hidden: bool) -> None:
        self._transcript.set_thinking_hidden(hidden)

    def set_tools_expanded(self, expanded: bool) -> None:
        self._transcript.set_tools_expanded(expanded)

    def set_extension_hidden_thinking_label(self, label: str | None = None) -> None:
        self._transcript.set_hidden_thinking_label(label)

    def add_notice(self, text: str) -> None:
        self._transcript.add_notice(text)

    def redraw_custom_entries(
        self,
        entries: Iterable[
            tuple[str, str, tuple[str, ...]]
            | tuple[
                str,
                str,
                tuple[str, ...],
                object | None,
                Mapping[str, RegisteredMessageRenderer]
                | Mapping[str, RegisteredEntryRenderer],
            ]
        ],
    ) -> None:
        self._transcript.redraw_custom_entries(entries)

    def add_custom_entry(self, custom_type: str, lines: Iterable[str]) -> None:
        self._transcript.add_custom_entry(custom_type, lines)

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

    def add_tool_call_custom(self, lines: Iterable[str]) -> None:
        self._transcript.add_tool_call_custom(lines)

    def add_tool_result_custom(
        self, lines: Iterable[str], *, duration_seconds: float | None = None
    ) -> None:
        self._transcript.add_tool_result_custom(
            lines, duration_seconds=duration_seconds
        )

    def add_custom_entry_styled(
        self,
        lines: Iterable[str],
        *,
        custom_type: str | None = None,
        data: object | None = None,
        renderers: Mapping[str, RegisteredMessageRenderer] | None = None,
    ) -> None:
        self._transcript.add_custom_entry_styled(
            lines, custom_type=custom_type, data=data, renderers=renderers
        )

    def add_entry_renderer_component(
        self,
        lines: Iterable[str],
        *,
        custom_type: str,
        entry: Mapping[str, object],
        renderers: Mapping[str, RegisteredEntryRenderer],
    ) -> None:
        self._transcript.add_entry_renderer_component(
            lines, custom_type=custom_type, entry=entry, renderers=renderers
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
            input=InputSnapshot(
                text=self.input_text,
                cursor=self._effective_input_cursor(),
                custom_rows=custom_rows,
            ),
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
            self._popup_menu_frame_lines(width=width, max_rows=max(1, height - 7))
        )
        pending = tuple(self._pending_region_lines(width))
        status = tuple(self._extension_status_lines(width))
        header = tuple(self._extension_header_lines(width))
        above = tuple(self._extension_widgets_lines("above_editor", width))
        below = tuple(self._extension_widgets_lines("below_editor", width))
        custom_footer = self._extension_footer_lines(width)
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

    # Max queued-message rows shown in the pending region. Bounded so a large
    # queue cannot grow the pinned chrome and push the input/footer out of the
    # live region; overflow is summarized in a single "+N more" row.
    _PENDING_REGION_MAX_ROWS = 6

    def _pending_region_lines(self, width: int) -> list[_FrameLine]:
        """Render the queued steering/follow-up messages (Pi pending area).

        Capped at :data:`_PENDING_REGION_MAX_ROWS` message rows so an unbounded
        queue cannot exceed the live region and push the input/footer out; the
        remainder is collapsed into a ``… +N more queued`` row.
        """

        if not self.has_pending_messages():
            return []
        queued = list(self._editor.pending_messages())
        cap = self._PENDING_REGION_MAX_ROWS
        visible = queued[:cap]
        lines: list[_FrameLine] = []
        for entry in visible:
            # Rendering vocabulary belongs to the frame adapter, not EditorState.
            kind = "Steering" if entry.kind == "steering" else "Follow-up"
            label = entry.content.replace("\n", " ")
            lines.append(_FrameLine(self._clip(f"  {kind}: {label}", width), "notice"))
        hidden = len(queued) - len(visible)
        if hidden > 0:
            lines.append(
                _FrameLine(
                    self._clip(f"  … +{hidden} more queued", width),
                    "slash_menu_scroll",
                )
            )
        lines.append(
            _FrameLine(
                self._clip(
                    "  (alt+up to restore queued messages to the editor)", width
                ),
                "slash_menu_scroll",
            )
        )
        return lines

    def _render_region_lines(
        self, region: ChromeRegion, *, width: int, max_lines: int
    ) -> tuple[str, ...] | None:
        """Return the region's snapshot lines (UNCLIPPED; the caller width-clips
        each line at frame-build time), or ``None`` when a factory re-render
        failed (the caller then drops the region — fail soft). Factory/component
        regions re-render every frame (component retained, not re-invoked), and
        invalidate on width changes; static regions keep their original lines
        unchanged, so narrowing-then-widening is non-lossy."""
        if not region.is_factory:
            return region.snapshot
        if region.component is None:
            return region.snapshot
        if region.width != width:
            invalidate = getattr(region.component, "invalidate", None)
            if callable(invalidate):
                try:
                    invalidate()
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException:  # noqa: BLE001
                    pass
        lines = render_chrome_component(
            lambda: region.component, width=width, max_lines=max_lines
        )
        if lines is None:
            return None
        region.snapshot = tuple(lines)
        region.width = width
        return region.snapshot

    def _clip_custom(self, text: str, width: int) -> str:
        cleaned = _sanitize_custom_overlay_text(text)
        if _visible_len_allow_sgr(cleaned) <= width:
            return cleaned
        return _clip_custom_overlay_text(cleaned, width)

    def _extension_header_lines(self, width: int) -> list[_FrameLine]:
        if self._chrome.header is None:
            return []
        with self._paint_lock:
            lines = self._render_region_lines(
                self._chrome.header, width=width, max_lines=_HEADER_MAX_LINES
            )
            if lines is None:
                self._dispose_region(self._chrome.header)
                self._chrome.header = None
                return []
        return [
            _FrameLine(self._clip_custom(line, width), "chrome_custom")
            for line in lines
        ]

    def _extension_widgets_lines(self, placement: str, width: int) -> list[_FrameLine]:
        regions = (
            self.extension_widgets_below
            if placement == "below_editor"
            else self.extension_widgets_above
        )
        if not regions:
            return []
        out: list[_FrameLine] = []
        failed: list[str] = []
        with self._paint_lock:
            for key, region in regions.items():  # insertion order
                lines = self._render_region_lines(
                    region, width=width, max_lines=_WIDGET_MAX_LINES
                )
                if lines is None:
                    failed.append(key)
                    continue
                for line in lines:
                    out.append(
                        _FrameLine(self._clip_custom(line, width), "chrome_custom")
                    )
            for key in failed:
                self._dispose_region(regions.pop(key, None))
        return out

    def _extension_footer_lines(self, width: int) -> list[_FrameLine] | None:
        """Return custom footer rows, or None to fall back to the built-in footer."""
        if self._chrome.footer is None:
            return None
        with self._paint_lock:
            lines = self._render_region_lines(
                self._chrome.footer, width=width, max_lines=_FOOTER_MAX_LINES
            )
            if lines is None:
                self._dispose_region(self._chrome.footer)
                self._chrome.footer = None
                return None
        return [
            _FrameLine(self._clip_custom(line, width), "chrome_custom")
            for line in lines
        ]

    def _extension_status_lines(self, width: int) -> list[_FrameLine]:
        """Render bounded extension status rows above the footer."""

        if not self.extension_status:
            return []
        with self._paint_lock:
            items = tuple(sorted(self.extension_status.items()))
        rows: list[_FrameLine] = []
        for key, raw_value in items[:3]:
            value = sanitize_label_text(raw_value)
            rows.append(_FrameLine(self._clip(f"  {key}: {value}", width), "notice"))
        hidden = len(items) - len(rows)
        if hidden > 0:
            rows.append(
                _FrameLine(
                    self._clip(f"  ... +{hidden} extension status rows", width),
                    "slash_menu_scroll",
                )
            )
        return rows

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

    @staticmethod
    def _display_input_text(text: str) -> str:
        return render_display_input_text(text)

    def _input_frame_lines(
        self, width: int, *, max_rows: int | None = None
    ) -> list[_FrameLine]:
        custom_rows = None
        if self._custom_editor_active:
            custom_rows = tuple(self._custom_editor_frame_lines(width))
        row_limit = 10**9 if max_rows is None else max_rows
        return list(
            render_input_lines(
                InputSnapshot(
                    text=self.input_text,
                    cursor=self._effective_input_cursor(),
                    custom_rows=custom_rows,
                ),
                width,
                max_rows=row_limit,
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
            raw = [self._display_input_text(self._custom_editor_text()) or " "]
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
            self._editor.stage_paste(self._driver.consume_paste())
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
            self.poll_extension_footer_branch()
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

    def _insert_input_text(self, text: str) -> None:
        self._editor.insert(text, self.command_names)
        self._refresh_autocomplete_state()

    def _insert_paste(self, text: str) -> None:
        """Insert pasted text literally as a single undo-able edit.

        Newlines are preserved in the buffer (so a multi-line paste is held
        verbatim) but never interpreted as Enter, so a paste cannot submit a
        command on its own. The slash menu only opens for a leading ``/`` with
        no whitespace, so pasted multi-token or multi-line text leaves it
        closed.
        """

        if not text:
            return
        # Terminal drag-drop arrives as a bracketed paste; a single existing
        # file path is treated as an attachment reference (Pi "drop files to
        # attach") — an image path becomes ``@image:``, any other existing path
        # becomes ``@path`` — so submit resolves it through the usual loaders.
        reference = self._as_drag_reference(text)
        if reference is not None:
            text = reference
        self._editor.insert(text, self.command_names)
        self._refresh_autocomplete_state()

    def _as_drag_reference(self, text: str) -> str | None:
        """Return an ``@image:``/``@path`` reference for a dropped file path.

        Returns ``None`` for ordinary pasted text (multi-line, or not an
        existing single file path), which is then inserted literally. Relative
        drops are resolved against the session workspace (``self.cwd``), not the
        process cwd, so a file dropped from the workspace resolves even when the
        two differ.
        """

        candidate = text.strip()
        if not candidate or "\n" in candidate:
            return None
        if (
            len(candidate) >= 2
            and candidate[0] == candidate[-1]
            and candidate[0] in "\"'"
        ):
            candidate = candidate[1:-1]
        if not candidate or "\x00" in candidate:
            return None
        try:
            resolved = Path(candidate).expanduser()
            if not resolved.is_absolute():
                resolved = self.cwd / resolved
            if not resolved.is_file():
                return None
        except OSError:
            return None
        # Re-quote a path containing a space so the reference resolves as a
        # single token (the @path/@image: resolvers accept @"…"); an unquoted
        # spaced path would otherwise break at the space.
        rendered = f'"{candidate}"' if " " in candidate else candidate
        image_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        if Path(candidate).suffix.lower() in image_suffixes:
            return f"@image:{rendered} "
        return f"@{rendered} "

    def _paste_clipboard_image(self) -> None:
        """Insert an ``@image:`` reference for the OS clipboard image (Ctrl+V).

        Reads the clipboard image through the injected reader, writes it to an
        owner-only temp file under the session clipboard dir (registered as an
        image reference root), and inserts an ``@image:<path>`` reference so the
        existing attachment resolver loads it on submit. Reports a local notice
        when no image / no tool is available; no image bytes reach the archive.
        """

        if self.clipboard_image_read is None or self.clipboard_temp_dir is None:
            self.add_notice("pipy: clipboard image paste is not available here.")
            return
        result = self.clipboard_image_read()
        if not result.found:
            self.add_notice(f"pipy: {result.detail}.")
            return
        extension = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
        }.get(result.media_type, "png")
        try:
            self.clipboard_temp_dir.mkdir(parents=True, exist_ok=True)
            try:
                self.clipboard_temp_dir.chmod(0o700)
            except OSError:
                pass
            self._clipboard_image_count += 1
            path = (
                self.clipboard_temp_dir
                / f"pipy-clipboard-{self._clipboard_image_count}.{extension}"
            )
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(result.data)
        except OSError:
            self.add_notice("pipy: could not save the pasted clipboard image.")
            return
        # Quote the reference when the temp path contains a space (e.g. a TMPDIR
        # with spaces) so the @image: resolver loads it as a single token.
        reference = f'"{path}"' if " " in str(path) else str(path)
        insertion = f"@image:{reference} "
        if self._custom_editor_active:
            self._set_custom_editor_text(f"{self._custom_editor_text()}{insertion}")
            return
        self._insert_input_text(insertion)

    def _delete_before_cursor(self) -> None:
        if self._editor.delete_before_cursor(self.command_names):
            self._refresh_autocomplete_state()

    def _kill_to_line_start(self) -> None:
        if self._editor.kill_to_line_start(self.command_names):
            self._refresh_autocomplete_state()

    def _reset_line_editor_state(self) -> None:
        self._editor.reset_line_editor_state()

    def _reset_history_nav(self) -> None:
        self._editor.reset_history_nav()

    def _snapshot_for_undo(self) -> None:
        self._editor.snapshot_for_undo()

    def _undo_edit(self) -> None:
        if self._editor.undo(self.command_names):
            self._refresh_autocomplete_state()

    def _redo_edit(self) -> None:
        if self._editor.redo(self.command_names):
            self._refresh_autocomplete_state()

    def _record_history(self, submitted: str) -> None:
        self._editor.record_history(submitted)

    def _navigate_history(self, key: str) -> None:
        if self._editor.navigate_history(key):
            self.paint()

    def _load_history_entry(self, text: str) -> None:
        self._editor.load_history_entry(text)
        self.paint()

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

    def _move_input_cursor(self, key: str) -> None:
        self._editor.move_cursor(key)

    def _effective_input_cursor(self) -> int:
        return self._editor.effective_cursor()

    def _refresh_slash_menu_state(self) -> None:
        self._editor.refresh_slash_menu(self.command_names)
        self._refresh_autocomplete_state()

    def _refresh_autocomplete_state(self) -> None:
        """Open/refresh the ``@`` file picker as the editor content changes.

        The slash menu keeps priority for a leading ``/``; while it is open the
        autocomplete popup stays closed so the two never co-open. Otherwise an
        ``@``-prefixed token at the cursor opens a scored, workspace-bounded
        file picker (Pi's content trigger). Tab path completion is forced (not
        auto), so it is not opened here.
        """

        if self.slash_menu_open:
            self._close_autocomplete()
            return
        suggestion = self._autocomplete_suggestions(force=False)
        if suggestion is None:
            self._close_autocomplete()
            return
        self._editor.open_autocomplete(
            items=tuple(suggestion.items),
            mode=suggestion.mode,
            token_start=suggestion.token_start,
            prefix=suggestion.prefix,
            active_provider=self._autocomplete_active_provider,
        )

    def add_extension_autocomplete_provider(self, factory: object) -> None:
        if callable(factory):
            self._autocomplete_provider_factories.append(factory)
            if self._custom_editor_component is not None:
                self._forward_autocomplete_to_custom_editor(
                    self._custom_editor_component
                )

    def _autocomplete_provider(self) -> object:
        provider: object = BuiltinAutocompleteProvider(self.cwd)
        for factory in self._autocomplete_provider_factories:
            try:
                wrapped = cast(Callable[[object], object], factory)(provider)
            except Exception:  # noqa: BLE001 - extension provider must fail soft
                continue
            if wrapped is not None:
                provider = wrapped
        return provider

    def _autocomplete_suggestions(
        self, *, force: bool
    ) -> AutocompleteSuggestion | None:
        cursor = self._effective_input_cursor()
        lines, cursor_line, cursor_col = cursor_to_line_col(self.input_text, cursor)
        provider = self._autocomplete_provider()
        if force:
            try:
                should = call_provider_method(
                    provider,
                    "should_trigger_file_completion",
                    "shouldTriggerFileCompletion",
                    lines,
                    cursor_line,
                    cursor_col,
                )
            except AttributeError:
                should = True
            except Exception:  # noqa: BLE001 - extension provider must fail soft
                should = True
            if not bool(should):
                return None
        try:
            raw = call_provider_method(
                provider,
                "get_suggestions",
                "getSuggestions",
                lines,
                cursor_line,
                cursor_col,
                AutocompleteContext(force=force, signal=None),
            )
        except Exception:  # noqa: BLE001 - extension provider must fail soft
            provider = BuiltinAutocompleteProvider(self.cwd)
            raw = provider.get_suggestions(
                lines,
                cursor_line,
                cursor_col,
                AutocompleteContext(force=force, signal=None),
            )
        suggestion = coerce_suggestion(raw)
        self._editor.autocomplete_active_provider = (
            provider if suggestion is not None else None
        )
        return suggestion

    def _close_autocomplete(self) -> None:
        self._editor.close_autocomplete()

    def enqueue_steering(self, text: str) -> None:
        self._editor.enqueue_steering(text)

    def enqueue_follow_up(self, text: str) -> None:
        self._editor.enqueue_follow_up(text)

    def has_pending_messages(self) -> bool:
        return self._editor.has_pending_messages()

    def promote_pending_to_drain(self) -> None:
        """Move queued messages into the sequential drain (steering first)."""

        self._editor.promote_pending_to_drain()

    def restore_pending_to_editor(self) -> None:
        """Restore queued messages into the editor joined by blank lines (Alt+Up
        / Escape-abort), then clear the lanes.

        Routed through ``_pending_initial_text`` as well as ``input_text``: an
        Escape-abort returns control to the outer loop, whose next ``read_line``
        resets ``input_text`` unless ``_pending_initial_text`` is set — so
        without this the restored messages would be wiped before the user saw
        them.

        Includes ``_pending_drain``: once a turn settles (or steering promotes)
        the lanes are emptied into the drain, so on an Escape-abort the
        not-yet-delivered drain entries must come back too — otherwise they stay
        hidden and keep auto-submitting to the provider after the cancellation.
        They lead (they are next to deliver) ahead of any steering/follow-up
        enqueued after promotion.
        """

        supplier = self._custom_editor_text if self._custom_editor_active else None
        if not self._editor.restore_pending_to_editor(custom_text_supplier=supplier):
            return
        if self._custom_editor_active:
            self._set_custom_editor_text(self.input_text)
            return
        self._refresh_slash_menu_state()

    def take_next_drain(self) -> str | None:
        """Pop the next queued message to deliver as a prompt, or None."""

        return self._editor.take_next_drain()

    def take_last_drain_kind(self) -> QueuedInputKind | None:
        """Return and clear the classification of the last drained prompt."""

        return self._editor.take_last_drain_kind()

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

    def take_pending_command(self) -> str | None:
        """Pop a local command submitted mid-turn (Enter), or None.

        The session loop reads this before the drain/read_line and dispatches it
        through the normal local-command path, so it is never sent to the
        provider (unlike a drained steering/follow-up message).
        """

        return self._editor.take_pending_command()

    def _is_bash_mode(self) -> bool:
        """True when the editor buffer is a ``!``/``!!`` local-shell shortcut.

        Mirrors Pi's ``isBashMode`` editor border: while the first non-space
        character of the input is ``!`` the input frame paints a distinct
        bash-mode affordance (Enter runs a shell command, not a provider turn).
        """

        return self.input_text.lstrip().startswith("!")

    def _navigate_autocomplete(self, key: str) -> None:
        if self._editor.navigate_autocomplete(key):
            self.paint()

    def _accept_autocomplete_selection(self) -> None:
        """Replace the active ``@``/path token with the highlighted candidate.

        Accepting an ``@`` candidate leaves a literal ``@path`` in the buffer so
        the existing ``file_references`` resolver loads its bounded excerpt on
        submit. Accepting a directory in path mode re-opens the popup for the
        next segment, mirroring Pi's progressive Tab completion.
        """

        selection = self._editor.completion_selection()
        if selection is None:
            return
        # Capture and validate one immutable owner snapshot before trusted
        # extension code runs. The callback may synchronously mutate the editor
        # or popup through its UI context; provider arguments, fallback splice,
        # accepted mode, and directory behavior must all use this same snapshot.
        if not selection.span_is_valid():
            self._editor.close_autocomplete()
            self.paint()
            return
        self._editor.snapshot_for_undo()
        self._editor.reset_history_nav()
        provider = selection.active_provider or BuiltinAutocompleteProvider(self.cwd)
        lines, cursor_line, cursor_col = cursor_to_line_col(
            selection.text, selection.cursor
        )
        try:
            raw_result = call_provider_method(
                provider,
                "apply_completion",
                "applyCompletion",
                lines,
                cursor_line,
                cursor_col,
                selection.item,
                selection.prefix
                or selection.text[selection.token_start : selection.cursor],
            )
            result = coerce_apply_result(raw_result)
        except Exception:  # noqa: BLE001 - extension provider must fail soft
            result = None
        if result is None:
            result = AutocompleteApplyResult(
                selection.text[: selection.token_start]
                + selection.item.value
                + selection.text[selection.cursor :],
                selection.token_start + len(selection.item.value),
            )
        self._editor.apply_completion_result(result.text, result.cursor)
        if selection.mode == "path" and selection.item.value.rstrip('"').endswith("/"):
            # Directory accepted: re-open the popup for the next segment.
            self._attempt_path_completion()
        self.paint()

    def _attempt_path_completion(self) -> bool:
        """Forced Tab path completion against the prefix before the cursor.

        Returns ``True`` when the prefix produced candidates (and the editor was
        updated/opened), ``False`` for a no-op. Uses the forced-Tab prefix so
        bare workspace prefixes (``README``, ``scr``) complete, not just
        path-like ones; Tab stays a no-op in prose because the empty-token case
        (e.g. after a trailing space) is skipped and a non-path word that
        matches no workspace entry yields no candidates. Completes the longest
        unambiguous prefix and opens the popup when more than one remains.
        """

        # Key dispatch gives an open slash menu first refusal (Tab accepts its
        # selected command). Keep the completion adapter honest when called
        # directly too: do not execute provider/filesystem lookup only to have
        # the owner reject the mutually exclusive autocomplete popup.
        if self.slash_menu_open:
            return False
        suggestion = self._autocomplete_suggestions(force=True)
        if suggestion is None:
            return False
        start = suggestion.token_start
        prefix = suggestion.prefix
        items = suggestion.items
        cursor = self._effective_input_cursor()
        common = self._longest_common_value(items)
        if common and len(common) > len(prefix):
            self._editor.snapshot_for_undo()
            self._editor.reset_history_nav()
            self._editor.set_buffer(
                self.input_text[:start] + common + self.input_text[cursor:],
                cursor=start + len(common),
            )
            cursor = self._effective_input_cursor()
            prefix = common
        if len(items) == 1:
            single = items[0].value
            self._editor.snapshot_for_undo()
            self._editor.reset_history_nav()
            self._editor.set_buffer(
                self.input_text[:start] + single + self.input_text[cursor:],
                cursor=start + len(single),
            )
            self._editor.close_autocomplete()
            return True
        self._editor.open_autocomplete(
            items=tuple(items),
            mode="path",
            token_start=start,
            prefix=prefix,
            active_provider=self._autocomplete_active_provider,
            reset_selection=True,
        )
        return True

    @staticmethod
    def _longest_common_value(items: Sequence[CompletionItem]) -> str:
        values = [item.value for item in items]
        if not values:
            return ""
        shortest = min(values, key=len)
        for index, char in enumerate(shortest):
            if any(value[index] != char for value in values):
                return shortest[:index]
        return shortest

    def _filtered_commands(self) -> tuple[str, ...]:
        return self._editor.filtered_commands(self.command_names)

    def _accept_slash_menu_selection(self) -> None:
        if self._editor.accept_slash_menu(self.command_names):
            self.paint()

    def _navigate_slash_menu(self, key: str) -> None:
        if self._editor.navigate_slash_menu(key, self.command_names):
            self.paint()

    def _popup_menu_frame_lines(self, *, width: int, max_rows: int) -> list[_FrameLine]:
        """Return the active in-frame completion popup (slash menu or editor).

        The slash menu keeps priority when it is open; otherwise the editor
        autocomplete popup (``@`` file picker or Tab path completion) draws in
        the same rows. The two never co-open, mirroring Pi.
        """

        if self.slash_menu_open:
            return self._slash_menu_frame_lines(width=width, max_rows=max_rows)
        if self.autocomplete_open:
            return self._autocomplete_frame_lines(width=width, max_rows=max_rows)
        return []

    def _autocomplete_frame_lines(
        self, *, width: int, max_rows: int
    ) -> list[_FrameLine]:
        items = self.autocomplete_items
        if not self.autocomplete_open or not items or max_rows <= 0:
            return []
        menu_cap = (
            self.autocomplete_max_visible if self.autocomplete_max_visible > 0 else 5
        )
        visible_count = min(len(items), max_rows, menu_cap)
        start = max(
            0,
            min(
                self.autocomplete_selection - (visible_count // 2),
                max(0, len(items) - visible_count),
            ),
        )
        visible = items[start : start + visible_count]
        total = len(items)
        lines: list[_FrameLine] = []
        for offset, item in enumerate(visible, start=start):
            prefix = "→ " if offset == self.autocomplete_selection else "  "
            label = item.label
            description_start = len(prefix) + len(label)
            line = f"{prefix}{label}"
            # Show the full inserted value (dimmed) when it differs from the
            # short label and the row has room, so a scoped/quoted path is
            # legible before acceptance.
            if item.value not in {label, f"@{label}"} and width > 40:
                spacing = " " * max(1, 24 - len(line))
                remaining = width - len(line) - len(spacing) - 2
                if remaining > 6:
                    line = f"{line}{spacing}{item.value[:remaining]}"
                    description_start = len(prefix) + len(label) + len(spacing)
            lines.append(
                _FrameLine(
                    self._clip(line, width),
                    "slash_menu_selected"
                    if offset == self.autocomplete_selection
                    else "slash_menu",
                    {"description_start": description_start},
                )
            )
        if start > 0 or start + visible_count < total:
            lines.append(
                _FrameLine(
                    self._clip(f"  ({self.autocomplete_selection + 1}/{total})", width),
                    "slash_menu_scroll",
                )
            )
        return lines

    def _slash_menu_frame_lines(self, *, width: int, max_rows: int) -> list[_FrameLine]:
        matches = self._filtered_commands()
        if not self.slash_menu_open or not matches or max_rows <= 0:
            return []
        menu_cap = (
            self.autocomplete_max_visible if self.autocomplete_max_visible > 0 else 5
        )
        visible_count = min(len(matches), max_rows, menu_cap)
        start = max(
            0,
            min(
                self.slash_menu_selection - (visible_count // 2),
                max(0, len(matches) - visible_count),
            ),
        )
        visible = matches[start : start + visible_count]
        lines: list[_FrameLine] = []
        total = len(matches)
        primary_width = self._slash_menu_primary_column_width(matches)
        for offset, command in enumerate(visible, start=start):
            description = self.command_descriptions.get(command, "")
            display_command = command[1:] if command.startswith("/") else command
            prefix = "→ " if offset == self.slash_menu_selection else "  "
            max_primary_width = max(1, primary_width - 2)
            display_command = display_command[:max_primary_width]
            spacing = " " * max(1, primary_width - len(display_command))
            description_start = len(prefix) + len(display_command)
            line = f"{prefix}{display_command}{spacing}"
            if description and width > 40:
                remaining = width - len(line) - 2
                if remaining > 10:
                    line = f"{line}{description[:remaining]}"
            lines.append(
                _FrameLine(
                    self._clip(line, width),
                    "slash_menu_selected"
                    if offset == self.slash_menu_selection
                    else "slash_menu",
                    {"description_start": description_start},
                )
            )
        if start > 0 or start + visible_count < total:
            lines.append(
                _FrameLine(
                    self._clip(f"  ({self.slash_menu_selection + 1}/{total})", width),
                    "slash_menu_scroll",
                )
            )
        return lines

    @staticmethod
    def _slash_menu_primary_column_width(matches: tuple[str, ...]) -> int:
        widest = 0
        for command in matches:
            display_command = command[1:] if command.startswith("/") else command
            widest = max(widest, len(display_command) + 2)
        return max(12, min(32, widest))


class _PendingToolRender(TypedDict):
    corr: str
    args: dict[str, object]
    state: dict[str, object]
    # The renderer resolved when the call was rendered. Pinning it here keeps a
    # result bound to the tool set advertised for its request: `/reload` may
    # replace the live renderer map while a tool call is in flight, and a
    # second lookup at result time would then render the result with a
    # different extension's renderer, or with none.
    tool: "ExtensionTool"


def _forward_legacy_render_details(ctx: ToolRenderContext, details: object) -> None:
    """Preserve opaque values manually inserted into the internal reader sink."""

    # The public context deliberately remains mapping-only. Older/manual callers
    # could still insert an opaque value into this internal handoff, so bypass the
    # frozen field only at this compatibility seam rather than widening its type.
    object.__setattr__(ctx, "details", details)


class _TuiToolLoopRenderer:
    """Tool-loop renderer backed by the pipy-owned terminal UI shell."""

    _SPINNER_FRAMES: ClassVar[tuple[str, ...]] = _ToolLoopRenderer._SPINNER_FRAMES
    _SPINNER_INTERVAL_SECONDS: ClassVar[float] = (
        _ToolLoopRenderer._SPINNER_INTERVAL_SECONDS
    )
    _RESULT_LINE_PREVIEW_MAX_LENGTH: ClassVar[int] = 5

    def __init__(
        self,
        *,
        ui: ToolLoopTerminalUi,
        tool_renderers: Mapping[str, ExtensionTool] | None = None,
        render_details_sink: ToolRenderDetailsSink | None = None,
    ) -> None:
        self._ui = ui
        self._streamed_any = False
        self._stop_working_event: threading.Event | None = None
        self._working_thread: threading.Thread | None = None
        self._last_tool_name = ""
        self._tool_renderers = dict(tool_renderers or {})
        self._render_details_sink = render_details_sink
        self._pending_render: _PendingToolRender | None = None

    @property
    def streamed_any(self) -> bool:
        return self._streamed_any

    def refresh_tool_renderers(
        self, tool_renderers: Mapping[str, ExtensionTool]
    ) -> None:
        self._tool_renderers = dict(tool_renderers)

    @property
    def stream_sink(self) -> StreamChunkSink:
        return self._handle_stream_chunk

    @property
    def reasoning_sink(self) -> StreamChunkSink:
        return self.handle_reasoning_chunk

    def start_assistant_message(self) -> None:
        """Reset and display provider-turn chrome for a canonical message start."""

        self.begin_provider_turn()
        self.show_working()

    def begin_provider_turn(self) -> None:
        self._stop_working(clear=True)
        self._streamed_any = False
        self._ui.begin_assistant_turn()

    def _effective_spinner(self) -> tuple[tuple[str, ...], float]:
        frames = self._ui.extension_indicator_frames
        interval = self._ui.extension_indicator_interval_ms
        if frames is None:
            eff_frames = self._SPINNER_FRAMES
        elif len(frames) == 0:
            eff_frames = ("",)  # hide the glyph, keep the message
        else:
            eff_frames = tuple(frames)
        eff_interval = (
            self._SPINNER_INTERVAL_SECONDS if interval is None else interval / 1000.0
        )
        return eff_frames, eff_interval

    def show_working(self) -> None:
        self._stop_working(clear=True)
        if not self._ui.extension_working_visible:
            return
        stop_event = threading.Event()
        self._stop_working_event = stop_event

        def _animate() -> None:
            frames, interval = self._effective_spinner()
            frame_index = 0
            while not stop_event.is_set():
                glyph = frames[frame_index % len(frames)]
                message = self._ui.extension_working_message or "Working..."
                # An empty glyph hides the spinner: show the message with no
                # leading space/prefix.
                self._ui.set_working(message if glyph == "" else f"{glyph} {message}")
                frame_index += 1
                stop_event.wait(interval)

        thread = threading.Thread(
            target=_animate,
            name="pipy-tool-loop-tui-spinner",
            daemon=True,
        )
        self._working_thread = thread
        thread.start()

    def complete_assistant_message(self, *, has_tool_calls: bool) -> None:
        del has_tool_calls
        self._finish_provider_turn()

    def _finish_provider_turn(self) -> None:
        self._stop_working(clear=True)
        self._ui.settle_assistant()

    def fail_assistant_message(self) -> None:
        self._finish_provider_turn()

    def cancel_assistant_message(self, reason: AgentCancellationReason) -> None:
        self._stop_working(clear=True)
        if reason is AgentCancellationReason.OPERATOR_ABORT:
            self._ui.show_operation_aborted()

    def render_user_message(self, text: str) -> None:
        self._ui.submit_user_message(text)

    def render_buffered_assistant_text(
        self, text: str, *, has_tool_calls: bool
    ) -> None:
        """Render a non-streamed assistant completion from its canonical event."""

        del has_tool_calls
        self._ui.append_assistant(text)
        self._streamed_any = True

    def render_tool_call(self, call: AgentToolCall) -> None:
        self._stop_working(clear=True)
        self._last_tool_name = call.tool_name
        self._pending_render = None
        tool = self._tool_renderers.get(call.tool_name)
        if tool is not None:
            args = _parse_tool_input(call.arguments_json.value)
            state: dict[str, object] = {}
            self._pending_render = {
                "corr": call.provider_correlation_id,
                "args": args,
                "state": state,
                "tool": tool,
            }
            if tool.render_call is not None:
                lines = self._dispatch_render(
                    tool.render_call,
                    args,
                    state,
                    is_result=False,
                    content=None,
                    details=None,
                    is_error=False,
                )
                if lines is not None:
                    self._ui.add_tool_call_custom(lines)
                    return
        self._ui.add_tool_call(_plain_tool_call_header(call))

    def tool_output_sink(self, chunk: str) -> None:
        self._ui.append_tool_output(chunk)

    def render_tool_result(
        self,
        *,
        output_text: str,
        is_error: bool,
        duration_seconds: float | None = None,
    ) -> None:
        pending = self._pending_render
        self._pending_render = None
        if pending is not None:
            tool = pending["tool"]
            if tool.render_result is not None:
                details: object | None = None
                if self._render_details_sink is not None:
                    details = self._render_details_sink.pop(pending["corr"], None)
                lines = self._dispatch_render(
                    tool.render_result,
                    pending["args"],
                    pending["state"],
                    is_result=True,
                    content=output_text,
                    details=details,
                    is_error=is_error,
                )
                if lines is not None:
                    self._ui.add_tool_result_custom(
                        lines, duration_seconds=duration_seconds
                    )
                    return
        if self._last_tool_name == "read" and not is_error:
            return
        lines = self._visible_tool_result_lines(output_text.splitlines() or [""])
        # Ctrl+O tool-output expansion: when expanded, commit the full retained
        # (already tool-bounded) output instead of the 5-line collapsed preview.
        if self._ui.tools_expanded:
            rendered = lines
        else:
            preview_lines = lines[: self._RESULT_LINE_PREVIEW_MAX_LENGTH]
            earlier = len(lines) - len(preview_lines)
            if earlier > 0:
                rendered = [
                    f"... ({earlier} earlier lines, ctrl+o to expand)",
                    *lines[-self._RESULT_LINE_PREVIEW_MAX_LENGTH :],
                ]
            else:
                rendered = preview_lines
        self._ui.add_tool_result(
            lines=rendered,
            is_error=is_error,
            duration_seconds=duration_seconds,
        )

    def _dispatch_render(
        self,
        renderer: Callable[[ToolRenderContext], object],
        args: Mapping[str, object],
        state: MutableMapping[str, object],
        *,
        is_result: bool,
        content: str | None,
        details: object | None,
        is_error: bool,
    ) -> list[str] | None:
        # Local imports: the render-theme machinery is only needed on the
        # rarely-hit custom-renderer branch, so it is imported here rather than
        # at module top to keep this module's import-time dependency surface
        # focused on the loop's hot path.
        from pipy_harness.extensions import ToolRenderContext
        from pipy_harness.native.chrome import chrome_style_for
        from pipy_harness.native.tool_renderers import (
            build_tool_render_theme,
            render_tool_phase,
        )

        style = chrome_style_for(self._ui.terminal_stream)
        typed_details = details if isinstance(details, Mapping) else None
        ctx = ToolRenderContext(
            tool_name=self._last_tool_name,
            args=args,
            is_result=is_result,
            is_error=is_error,
            content=content,
            details=typed_details,
            expanded=self._ui.tools_expanded,
            width=self._ui._driver.size()[0],
            theme=build_tool_render_theme(style),
            state=state,
        )
        if details is not None and typed_details is None:
            _forward_legacy_render_details(ctx, details)
        return render_tool_phase(renderer, ctx)

    def _visible_tool_result_lines(self, lines: list[str]) -> list[str]:
        if self._last_tool_name != "ls":
            return lines
        rendered: list[str] = []
        for line in lines:
            if line.startswith("file "):
                rendered.append(line[len("file ") :])
            elif line.startswith("directory "):
                rendered.append(line[len("directory ") :])
            elif line.startswith("other "):
                rendered.append(line[len("other ") :])
            else:
                rendered.append(line)
        return rendered

    def handle_reasoning_chunk(self, chunk: str) -> None:
        self._stop_working(clear=True)
        self._ui.append_reasoning(chunk)

    def _handle_stream_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        self._stop_working(clear=False)
        self._ui.append_assistant(chunk)
        self._streamed_any = True

    def _stop_working(self, *, clear: bool = True) -> None:
        if self._stop_working_event is not None:
            self._stop_working_event.set()
        if self._working_thread is not None:
            self._working_thread.join(timeout=0.2)
        self._stop_working_event = None
        self._working_thread = None
        if clear:
            self._ui.clear_working()


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

    from pipy_harness.native.session_tree import NativeSessionTree
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

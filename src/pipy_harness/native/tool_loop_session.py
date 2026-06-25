"""Bounded model-driven REPL session skeleton.

Slice 4 of the Tool-Loop Parity Track introduces a small `NativeToolReplSession`
class that wires the slice 2 contracts (`ToolDefinition`, `ToolRequest`,
`ToolExecutionResult`, `ToolPort`, `ToolContext`, `validate_arguments`) and the
slice 3 provider extension (`ProviderPort.supports_tool_calls`,
`ProviderToolCall`, `ProviderResult.tool_calls`) into a real turn loop.

The session is the product REPL behind `pipy repl --agent pipy-native`. It runs
the production tool registry (`read`, `ls`, `grep`, `find`, `write`, `edit`,
`bash`, ...); tests may inject a `_FixtureTool` through the registry argument to
verify loop behavior in isolation.

Invariants pinned by the focused tests:

- The session refuses providers that do not advertise
  `supports_tool_calls=True`.
- `--tool-budget` is bounded to `[1, 25]`; the constructor validates the
  value.
- Each user turn allows at most `tool_budget` tool invocations; subsequent
  model-emitted calls receive a deterministic "tool budget exhausted"
  observation.
- Malformed tool calls (unknown tool name, JSON decode error, schema
  violation) are returned to the model as `ToolResultMessage(is_error=True)`
  observations and increment a streak counter; three consecutive malformed
  turns end the loop with a deterministic stderr diagnostic.
- One successful invocation resets the malformed streak.
- The session does not write prompts, model text, tool payloads, file
  contents, or diffs to the archive; only safe counters and labels.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from typing import Any, ClassVar, TextIO

from pipy_harness.capture import sanitize_text
from pipy_harness.models import HarnessStatus
from pipy_harness.native.clipboard import (
    ClipboardResult,
    ImageClipboardResult,
    copy_to_clipboard,
    read_clipboard_image,
)
from pipy_harness.native.chrome import (
    BottomStatusFields,
    chrome_width,
    format_bottom_status_line,
    print_bottom_status_block,
    print_input_separator,
    print_startup_chrome,
    terminal_supports_truecolor,
)
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
)
from pipy_harness.native.automation.events import (
    AutomationEmitter,
    AutomationEventSink,
)
from pipy_harness.native.automation.serialize import parse_tool_arguments
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native._provider_helpers import failed_provider_result
from pipy_harness.native.provider import ProviderPort, StreamChunkSink
from pipy_harness.native.repl_input import (
    DEFAULT_REPL_COMMAND_DESCRIPTIONS,
    REPL_INPUT_RUNTIME_AUTO,
    NativeReplInput,
    native_repl_input_for,
)
from pipy_harness.native.repl_state import (
    NativeModelSelection,
    NativeReplProviderState,
    StaticNativeReplProviderState,
    normalize_repl_fake_selection,
    settings_overlay_lines,
)
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.changelog import (
    changelog_startup,
    read_changelog_entries,
    render_changelog,
)
from pipy_harness.native.keybindings import KeybindingsManager, render_hotkeys
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.scoped_models import filter_scoped_references, next_reference
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.version_check import pipy_version
from pipy_harness.native.export_distribution import (
    NativeExportError,
    ShareCancelled,
    ShareResult,
    default_html_export_path,
    export_native_branch_to_jsonl,
    export_native_session_to_html,
    import_native_session_jsonl,
    parse_command_path_argument,
    resolve_github_token,
    share_native_session,
)
from pipy_harness.native.session_compaction import (
    DEFAULT_KEEP_RECENT_GROUPS,
    compact_tool_loop_messages,
    should_compact_tool_loop_messages,
)
from pipy_harness.native.session_resume import (
    ResumeContext,
    compose_resume_status_line,
)
from pipy_harness.native.session_tree import (
    CompactionEntry as _CompactionEntry,
)
from pipy_harness.native.session_tree import (
    CustomEntry as _CustomEntry,
)
from pipy_harness.native.session_tree import (
    CustomMessageEntry as _CustomMessageEntry,
)
from pipy_harness.native.session_tree import (
    MessageEntry as _MessageEntry,
)
from pipy_harness.native.session_tree import (
    NativeSessionTree,
    default_native_session_dir,
)
from pipy_harness.native.session_tree_commands import (
    FILTER_MODES,
    abandoned_branch_messages,
    apply_tree_selection,
    branch_summary_attach_parent,
    delete_native_session,
    entry_preview,
    format_session_status,
    list_all_native_sessions,
    list_native_sessions,
    render_tree_lines,
    resolve_entry_ref,
    resolve_session_target,
    sanitize_label_text,
    visible_tree_entries,
)
from pipy_harness.native.extension_runtime import (
    EVENT_AGENT_END,
    EVENT_AGENT_START,
    EVENT_BEFORE_AGENT_START,
    EVENT_BEFORE_PROVIDER_REQUEST,
    EVENT_INPUT,
    EVENT_SESSION_SHUTDOWN,
    EVENT_SESSION_START,
    EVENT_SESSION_BEFORE_COMPACT,
    EVENT_SESSION_BEFORE_FORK,
    EVENT_SESSION_BEFORE_SWITCH,
    EVENT_SESSION_BEFORE_TREE,
    EVENT_TOOL_RESULT,
    EVENT_TURN_END,
    EVENT_TURN_START,
    EVENT_USER_BASH,
    LIFECYCLE_EVENTS,
    ExtensionCapabilityError,
    ExtensionTool,
    ExtensionUiDriver,
    FooterData,
    HookHandler,
    LifecycleEvent,
    QueuedUserMessage,
    RegisteredCommand,
    RegisteredFlag,
    RegisteredMessageRenderer,
    RegisteredProvider,
    RegisteredShortcut,
    RegisteredTool,
    RenderedCustomEntry,
    ToolResult,
    activate_extensions,
    dispatch_before_agent_start_hooks,
    dispatch_before_provider_request_hooks,
    dispatch_extension_command,
    dispatch_extension_shortcut,
    dispatch_input_hooks,
    dispatch_lifecycle_hooks,
    dispatch_session_before_hooks,
    dispatch_tool_call_hooks,
    dispatch_tool_result_hooks,
    dispatch_user_bash_hooks,
    drain_user_messages,
    extension_command_map,
    extension_event_hooks,
    extension_flags,
    extension_message_renderers,
    extension_providers,
    extension_shortcuts,
    extension_tool_call_hooks,
    extension_tools,
    extension_unregistered_providers,
    is_valid_custom_entry_type,
    make_extension_context,
    parse_extension_flag_tokens,
    render_extension_message,
    safe_custom_entry_data,
)
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.extension_provider_catalog import (
    extension_reserved_command_names,
    extension_reserved_tool_names,
)
from pipy_harness.native.package_runtime import compose_package_runtime
from pipy_harness.native.package_resources import PackageRoot
from pipy_harness.native.resources import (
    DISPATCH_LIST,
    WorkspaceResources,
    dispatch_resource_command,
)
from pipy_harness.native.themes import (
    NativeThemeStore,
    available_theme_names,
    resolve_active_theme_name,
    select_theme,
)
from pipy_harness.native.tui import (
    HOTKEY_EXTENSION_SHORTCUT_PREFIX,
    HOTKEY_MODEL_CYCLE_NEXT,
    HOTKEY_MODEL_CYCLE_PREV,
    HOTKEY_THINKING_CYCLE,
    HOTKEY_TOGGLE_THINKING,
    HOTKEY_TOGGLE_TOOLS,
    TURN_ABORTED,
    TURN_LOCAL_COMMAND,
    TURN_SETTLED,
    TURN_STEERED,
    ModelSelectorOption,
    ScopedModelRow,
    SettingsRow,
    TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS,
    ToolLoopTerminalUi,
)
from pipy_harness.native.tools.bash import LocalShellResult, run_local_command
from pipy_harness.native.file_references import resolve_file_references
from pipy_harness.native.image_attachment import (
    ProviderImageAttachment,
    resolve_image_attachments,
)
from pipy_harness.native.tools import (
    AssistantMessage,
    LoopMessage,
    ToolArgumentError,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
    ToolResultMessage,
    UserMessage,
    make_tool_request_id,
    validate_arguments,
)


@dataclass(frozen=True, slots=True)
class _PricingEntry:
    """Per-million-token pricing for one (provider, model) pair.

    Pricing is illustrative and conservative. The bottom status only
    shows the running total to the nearest mil-cent, so an exact match
    against the provider's billing portal is not required.
    """

    input_per_million: float
    output_per_million: float
    reasoning_per_million: float
    cache_read_per_million: float = 0.0
    cache_write_per_million: float = 0.0


_PRICING_TABLE: dict[tuple[str, str], _PricingEntry] = {
    # OpenAI Codex subscription (GPT-5.x family) — approximate.
    ("openai-codex", "gpt-5"): _PricingEntry(
        input_per_million=1.25, output_per_million=10.00, reasoning_per_million=10.00
    ),
}


def _pricing_for(provider_name: str, model_id: str) -> _PricingEntry | None:
    """Return per-million-token pricing for (provider, model), or None.

    Falls back to a model-family prefix lookup so e.g. ``gpt-5.5`` reuses
    the ``gpt-5`` entry. ``None`` disables cost rendering for that
    selection; the bottom status keeps showing ``$0.000``.
    """

    direct = _PRICING_TABLE.get((provider_name, model_id))
    if direct is not None:
        return direct
    for (entry_provider, entry_model), price in _PRICING_TABLE.items():
        if entry_provider != provider_name:
            continue
        if model_id.startswith(entry_model):
            return price
    return None


@dataclass(frozen=True, slots=True)
class _UnavailableAfterReloadProvider:
    """Fail-closed provider bound when reload removes the active provider."""

    name: str
    model_id: str
    error_message: str
    supports_tool_calls: bool = True

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del stream_sink, reasoning_sink, cancel_token
        return failed_provider_result(
            request,
            provider_name=self.name,
            started_at=datetime.now(UTC),
            error_type="ProviderUnavailableAfterReload",
            error_message=self.error_message,
        )


class _UsageAccumulator:
    """Running counters fed from each provider turn's usage payload.

    Captures input, output, cache-read/cache-write, and reasoning tokens plus
    an approximate USD cost. The last-turn total-token snapshot drives the context-window
    meter so the bottom status reflects real provider numbers when the
    adapter reports them and falls back to the deterministic estimate
    otherwise.
    """

    __slots__ = (
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "separate_cache_read_tokens",
        "separate_cache_write_tokens",
        "last_total_tokens",
        "cost_usd",
        "_pricing",
        "_provider_name",
        "_model_id",
    )

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.reasoning_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.separate_cache_read_tokens = 0
        self.separate_cache_write_tokens = 0
        self.last_total_tokens = 0
        self.cost_usd = 0.0
        self._pricing: _PricingEntry | None = None
        self._provider_name = ""
        self._model_id = ""

    def bind(self, provider_name: str, model_id: str) -> None:
        self._provider_name = provider_name
        self._model_id = model_id
        self._pricing = _pricing_for(provider_name, model_id)

    @property
    def cache_hit_percent(self) -> float | None:
        # OpenAI-style providers report cached tokens as a subset of input
        # tokens. Anthropic/Bedrock-style providers report cache reads/writes
        # separately; `absorb` classifies those per turn using total_tokens.
        denominator = float(
            self.input_tokens
            + self.separate_cache_read_tokens
            + self.separate_cache_write_tokens
        )
        if denominator <= 0:
            return None
        return 100.0 * self.cache_read_tokens / denominator

    def absorb(self, usage: Mapping[str, Any] | None) -> None:
        if not usage:
            return
        input_tokens = _coerce_int(usage.get("input_tokens"))
        output_tokens = _coerce_int(usage.get("output_tokens"))
        reasoning_tokens = _coerce_int(usage.get("reasoning_tokens"))
        cache_read_tokens = _coerce_int(usage.get("cached_tokens"))
        cache_write_tokens = _coerce_int(usage.get("cache_write_tokens"))
        total_tokens = _coerce_int(usage.get("total_tokens"))
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.reasoning_tokens += reasoning_tokens
        self.cache_read_tokens += cache_read_tokens
        self.cache_write_tokens += cache_write_tokens
        if _cache_counters_are_separate(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            total_tokens=total_tokens,
        ):
            self.separate_cache_read_tokens += cache_read_tokens
            self.separate_cache_write_tokens += cache_write_tokens
        if total_tokens > 0:
            self.last_total_tokens = total_tokens
        else:
            self.last_total_tokens = (
                input_tokens + output_tokens + reasoning_tokens
            )
        if self._pricing is not None:
            self.cost_usd += (
                input_tokens * self._pricing.input_per_million
                + output_tokens * self._pricing.output_per_million
                + reasoning_tokens * self._pricing.reasoning_per_million
                + cache_read_tokens * self._pricing.cache_read_per_million
                + cache_write_tokens * self._pricing.cache_write_per_million
            ) / 1_000_000.0


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _cache_counters_are_separate(
    *,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    total_tokens: int,
) -> bool:
    if cache_read_tokens <= 0 and cache_write_tokens <= 0:
        return False
    if total_tokens <= 0:
        return False
    minimum_total_if_separate = (
        input_tokens
        + output_tokens
        + reasoning_tokens
        + cache_read_tokens
        + cache_write_tokens
    )
    return total_tokens >= minimum_total_if_separate


@dataclass(frozen=True, slots=True)
class _ContextBudget:
    """Approximate provider/model context-window budget for the meter.

    ``token_budget`` is the absolute denominator; ``budget_label`` is the
    short label rendered into the bottom status (e.g. ``272k`` for the
    272 000-token GPT-5.5 context).
    """

    token_budget: int
    budget_label: str


_CODEX_GPT_5_5_BUDGET = _ContextBudget(token_budget=272_000, budget_label="272k")
_DEFAULT_CONTEXT_BUDGET = _ContextBudget(token_budget=128_000, budget_label="128k")


def _context_budget_for(provider_name: str, model_id: str) -> _ContextBudget:
    """Return the rough context-window budget label for the bottom status.

    The mapping deliberately covers the providers/models that pipy is
    tested against today. Unknown selections fall back to the safe
    128k default so the meter still renders. Switching to authoritative
    provider usage telemetry is a separate follow-up.
    """

    if provider_name == "openai-codex":
        if model_id.startswith("gpt-5"):
            return _CODEX_GPT_5_5_BUDGET
    if provider_name in {"anthropic"} and "sonnet" in model_id.lower():
        return _ContextBudget(token_budget=200_000, budget_label="200k")
    return _DEFAULT_CONTEXT_BUDGET


def _effort_label_for(provider_name: str, model_id: str) -> str:
    """Return the reasoning-effort label the bottom status surfaces.

    Pi shows ``high`` for the codex GPT-5.x family because those models
    default to high reasoning effort. Other providers / unknown
    configurations keep the safe ``default`` label.
    """

    if provider_name == "openai-codex" and model_id.startswith("gpt-5"):
        return "high"
    return "default"


def _friendly_cwd_label(cwd: Path) -> str:
    """Render ``cwd`` as ``~/<rel> (branch)`` when inside the user's home.

    Falls back to the absolute path when ``cwd`` is outside ``~`` or
    when the home directory cannot be resolved. The ``(branch)`` suffix
    is appended when ``cwd`` (or any parent up to the home directory)
    contains a ``.git`` directory whose ``HEAD`` can be read.
    """

    label = str(cwd)
    try:
        home = Path.home()
    except RuntimeError:
        home = None
    if home is not None:
        try:
            relative = cwd.resolve().relative_to(home.resolve())
            relative_str = relative.as_posix()
            label = "~" if relative_str in {"", "."} else f"~/{relative_str}"
        except ValueError:
            pass
    branch = _detect_git_branch(cwd)
    if branch:
        label = f"{label} ({branch})"
    return label


def _detect_git_branch(cwd: Path) -> str | None:
    """Walk up from ``cwd`` looking for ``.git/HEAD`` and return the branch."""

    candidate: Path | None = cwd
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
            return text[:7]
        return None
    return None


class _LiveExtensionUiDriver:
    """Live `ExtensionUiDriver` backed by the product TUI (one per session)."""

    def __init__(self, terminal_ui: "ToolLoopTerminalUi", cwd: Path) -> None:
        self._terminal_ui = terminal_ui
        self._cwd = cwd

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
        self._terminal_ui.set_extension_widget(key, content, placement=placement)

    def set_header(self, factory: object | None) -> None:
        self._terminal_ui.set_extension_header(factory)

    def set_footer(self, factory: object | None) -> None:
        footer_data = (
            None
            if factory is None
            else FooterData(
                git_branch=_detect_git_branch(self._cwd),
                extension_statuses=dict(self._terminal_ui.extension_status),
            )
        )
        self._terminal_ui.set_extension_footer(factory, footer_data)

    def set_title(self, title: str) -> None:
        self._terminal_ui.set_extension_title(title)

    def set_working_indicator(self, frames: object, interval_ms: object) -> None:
        self._terminal_ui.set_extension_working_indicator(frames, interval_ms)

    def get_editor_text(self) -> str:
        return self._terminal_ui.get_input_text()

    def set_editor_text(self, text: str) -> None:
        self._terminal_ui.set_input_text(text)

    def paste_to_editor(self, text: str) -> None:
        self._terminal_ui.paste_input_text(text)

    def apply_theme(self, name: str) -> tuple[bool, str | None]:
        """Switch the live chrome theme (rich-UI item E: ``ctx.ui.set_theme``).

        Reuses ``select_theme`` — the exact mechanism the ``/settings`` theme
        row uses — which validates the name (fail-closed on unknown), persists
        the non-secret name to the chrome store, and sets ``PIPY_THEME`` so the
        next ``chrome_style_for`` render repaints with the new palette. No
        provider turn, tool call, or archive write.
        """
        ok, message = select_theme(
            name, environ=os.environ, store=NativeThemeStore()
        )
        return ok, None if ok else message


def production_tool_registry() -> dict[str, ToolPort]:
    """Return the current production tool registry.

    `bash` is a real shell, matching Pi: it runs an arbitrary command in the
    workspace and returns combined, bounded stdout/stderr to the model. See
    `pipy_harness.native.tools.bash.BashTool`.
    """

    from pipy_harness.native.tools.bash import BashTool
    from pipy_harness.native.tools.edit import EditTool
    from pipy_harness.native.tools.edit_diff import EditDiffTool
    from pipy_harness.native.tools.find import FindTool
    from pipy_harness.native.tools.grep import GrepTool
    from pipy_harness.native.tools.ls import LsTool
    from pipy_harness.native.tools.read import ReadTool
    from pipy_harness.native.tools.truncate import TruncateTool
    from pipy_harness.native.tools.write import WriteTool

    return {
        "read": ReadTool(),
        "ls": LsTool(),
        "grep": GrepTool(),
        "find": FindTool(),
        "write": WriteTool(),
        "edit": EditTool(),
        "edit_diff": EditDiffTool(),
        "truncate": TruncateTool(),
        "bash": BashTool(),
    }


def _tool_loop_command_names(
    resources: WorkspaceResources,
    extension_command_names: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Tool-loop slash-menu command set, honest to what can execute.

    The static built-in set is augmented with the ``/skill`` resource
    entry point (which always at least lists), every discovered prompt
    template registered as its own ``/<name>`` command (Pi shape), every
    discovered, non-reserved custom ``/<name>`` command, and any activated
    extension ``/<name>`` commands (appended last, never shadowing a
    built-in or custom command).
    """

    names = list(TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS)
    insert_at = (names.index("/model") + 1) if "/model" in names else len(names)
    names[insert_at:insert_at] = ["/skill"]
    for slash_name in resources.template_slash_names():
        if slash_name not in names:
            names.append(slash_name)
    for slash_name in resources.custom_command_slash_names():
        if slash_name not in names:
            names.append(slash_name)
    for slash_name in extension_command_names:
        if slash_name not in names:
            names.append(slash_name)
    return tuple(names)


def _tool_loop_command_descriptions(
    resources: WorkspaceResources,
    extension_descriptions: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the slash-menu descriptions with dispatch-honest precedence.

    The menu description for a name must describe what dispatching that name
    actually runs. ``dispatch_resource_command`` resolves a colliding name in
    the order built-in > prompt template > custom command, and extension
    commands dispatch last (lowest precedence). Descriptions are layered in
    the reverse order (lowest precedence first) so a later ``update`` for a
    higher-precedence source wins a collision — i.e. for a name shared by a
    template and a custom command, the menu shows the *template's*
    description, matching what runs.
    """

    descriptions: dict[str, str] = {}
    if extension_descriptions:
        descriptions.update(extension_descriptions)
    descriptions.update(resources.custom_command_descriptions())
    descriptions.update(resources.template_descriptions())
    descriptions.update(DEFAULT_REPL_COMMAND_DESCRIPTIONS)
    return descriptions


class _ExtensionToolPort:
    """Adapt an extension `RegisteredTool` to the native `ToolPort`.

    The loop validates arguments against `definition.input_schema` before
    `invoke`, so the handler receives already-validated input. A handler
    exception becomes a bounded tool error (never a session crash), and
    the provider-visible output is bounded. `KeyboardInterrupt` /
    `SystemExit` propagate.

    Trust model (see the extension-api spec "Local trust boundary"):
    extension tool handlers are trusted local Python that runs in-process
    with the user's own OS permissions — the same trust level as the
    extension's `activate()` function. There is no in-process sandbox, so
    "read-only / pure" is the *documented convention* for this slice, not
    a runtime guarantee; capability *enforcement* (shell / network / write
    permission gates derived from the manifest `[permissions]` table) is a
    later, explicitly-scoped permission-policy slice. What pipy does
    enforce here is the provider boundary: schema-validated input, bounded
    output, and bounded errors.
    """

    def __init__(
        self,
        registered: RegisteredTool,
        *,
        has_ui: bool,
        notify_sink: Callable[[str, str], None] | None = None,
        flags: Mapping[str, object] | None = None,
        render_details_sink: MutableMapping[str, object] | None = None,
    ) -> None:
        self._registered = registered
        self._has_ui = has_ui
        self._notify_sink = notify_sink
        self._flags = dict(flags or {})
        self._render_details_sink = render_details_sink
        tool = registered.tool
        self._definition = ToolDefinition(
            name=tool.name,
            description=str(tool.description),
            input_schema=dict(tool.input_schema),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def invoke(
        self, request: ToolRequest, context: ToolContext
    ) -> ToolExecutionResult:
        ctx = make_extension_context(
            str(context.workspace_root),
            self._has_ui,
            self._notify_sink,
            flags=self._flags,
        )
        try:
            result = self._registered.tool.handler(ctx, dict(request.arguments))
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as err:  # noqa: BLE001 - bound a bad tool
            return ToolExecutionResult(
                tool_request_id=request.tool_request_id,
                output_text=f"extension tool error: {type(err).__name__}",
                is_error=True,
                provider_correlation_id=request.provider_correlation_id,
            )
        if isinstance(result, ToolResult) and isinstance(result.content, str):
            content = result.content
        elif isinstance(result, ToolResult):
            content = str(result.content)
        else:
            content = str(result)
        cap = ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH
        if len(content) > cap:
            content = content[: cap - 64] + "\n[pipy: extension tool output truncated]"
        if (
            self._render_details_sink is not None
            and self._registered.tool.render_result is not None
            and request.provider_correlation_id is not None
        ):
            details = result.details if isinstance(result, ToolResult) else None
            self._render_details_sink[request.provider_correlation_id] = (
                dict(details) if isinstance(details, Mapping) else None
            )
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=content,
            is_error=False,
            provider_correlation_id=request.provider_correlation_id,
        )


@dataclass(frozen=True, slots=True)
class _ExtensionRuntime:
    """The activated-extension contributions wired into one session run."""

    commands: dict[str, RegisteredCommand]
    menu_names: tuple[str, ...]
    descriptions: dict[str, str]
    tool_call_hooks: tuple[HookHandler, ...]
    lifecycle_hooks: dict[str, tuple[HookHandler, ...]]
    input_hooks: tuple[HookHandler, ...]
    before_agent_start_hooks: tuple[HookHandler, ...]
    tool_result_hooks: tuple[HookHandler, ...]
    user_bash_hooks: tuple[HookHandler, ...]
    before_provider_request_hooks: tuple[HookHandler, ...]
    session_before_switch_hooks: tuple[HookHandler, ...]
    session_before_fork_hooks: tuple[HookHandler, ...]
    session_before_compact_hooks: tuple[HookHandler, ...]
    session_before_tree_hooks: tuple[HookHandler, ...]
    outbox: list[QueuedUserMessage]
    tools: tuple[RegisteredTool, ...]
    shortcuts: dict[str, RegisteredShortcut]
    flags: tuple[RegisteredFlag, ...]
    providers: tuple[RegisteredProvider, ...]
    unregistered_providers: tuple[str, ...]
    message_renderers: dict[str, RegisteredMessageRenderer]


def _activate_workspace_extensions(
    cwd: Path,
    resources: WorkspaceResources,
    reserved_tool_names: tuple[str, ...] = (),
    *,
    package_roots: "Sequence[PackageRoot]" = (),
    extension_patterns: Sequence[str] = (),
    explicit_extension_paths: Sequence[Path] = (),
    include_default_extensions: bool = True,
) -> _ExtensionRuntime:
    """Discover + activate extensions and project their contributions.

    Reserved names are the executable built-in/custom command set, so an
    extension command can never shadow a built-in or a custom command.
    The result bundles the command map (for dispatch), the menu
    ``/<name>`` labels + descriptions, the ordered ``tool_call`` hooks,
    the per-event lifecycle hooks, the ``input`` and ``before_agent_start``
    hooks, and the shared ``send_user_message`` outbox. Activation runs
    extension code; any failing extension is disabled by
    ``activate_extensions`` without affecting the session.
    """

    reserved = extension_reserved_command_names(
        resources.custom_command_slash_names()
    )
    descriptors = discover_extensions(
        cwd,
        package_roots=tuple(package_roots),
        explicit_paths=explicit_extension_paths,
        include_defaults=include_default_extensions,
    )
    if extension_patterns:
        from pipy_harness.native.resource_enablement import is_resource_enabled

        descriptors = [
            descriptor
            for descriptor in descriptors
            if descriptor.source_kind == "cli"
            or is_resource_enabled(descriptor.name, list(extension_patterns))
        ]
    outbox: list[QueuedUserMessage] = []
    activated = activate_extensions(
        descriptors,
        reserved_command_names=reserved,
        reserved_tool_names=extension_reserved_tool_names(reserved_tool_names),
        message_outbox=outbox,
    )
    command_map = extension_command_map(activated)
    menu_names = tuple(f"/{name}" for name in command_map)
    descriptions = {
        f"/{command.name}": command.description for command in command_map.values()
    }
    tool_call_hooks = extension_tool_call_hooks(activated)
    lifecycle_hooks = {
        event: extension_event_hooks(activated, event) for event in LIFECYCLE_EVENTS
    }
    input_hooks = extension_event_hooks(activated, EVENT_INPUT)
    before_agent_start_hooks = extension_event_hooks(activated, EVENT_BEFORE_AGENT_START)
    tool_result_hooks = extension_event_hooks(activated, EVENT_TOOL_RESULT)
    user_bash_hooks = extension_event_hooks(activated, EVENT_USER_BASH)
    before_provider_request_hooks = extension_event_hooks(
        activated, EVENT_BEFORE_PROVIDER_REQUEST
    )
    session_before_switch_hooks = extension_event_hooks(
        activated, EVENT_SESSION_BEFORE_SWITCH
    )
    session_before_fork_hooks = extension_event_hooks(
        activated, EVENT_SESSION_BEFORE_FORK
    )
    session_before_compact_hooks = extension_event_hooks(
        activated, EVENT_SESSION_BEFORE_COMPACT
    )
    session_before_tree_hooks = extension_event_hooks(
        activated, EVENT_SESSION_BEFORE_TREE
    )
    return _ExtensionRuntime(
        commands=command_map,
        menu_names=menu_names,
        descriptions=descriptions,
        tool_call_hooks=tool_call_hooks,
        lifecycle_hooks=lifecycle_hooks,
        input_hooks=input_hooks,
        before_agent_start_hooks=before_agent_start_hooks,
        tool_result_hooks=tool_result_hooks,
        user_bash_hooks=user_bash_hooks,
        before_provider_request_hooks=before_provider_request_hooks,
        session_before_switch_hooks=session_before_switch_hooks,
        session_before_fork_hooks=session_before_fork_hooks,
        session_before_compact_hooks=session_before_compact_hooks,
        session_before_tree_hooks=session_before_tree_hooks,
        outbox=outbox,
        tools=extension_tools(activated),
        shortcuts=extension_shortcuts(activated),
        flags=extension_flags(activated),
        providers=extension_providers(activated),
        unregistered_providers=extension_unregistered_providers(activated),
        message_renderers=extension_message_renderers(activated),
    )


class _ExtensionAwareEmitter(AutomationEmitter):
    """`AutomationEmitter` that also fires extension lifecycle hooks.

    Mirrors Pi's lifecycle vocabulary onto the extension `@api.on(...)`
    observers at the existing emit points, so hook dispatch is not
    scattered through the loop. Lifecycle hooks are observe-only and
    fail-soft (a crashing observer never breaks the session). When an
    extension registers no lifecycle hooks this behaves exactly like the
    base emitter.
    """

    def __init__(
        self,
        sink: object,
        *,
        lifecycle_hooks: dict[str, tuple[HookHandler, ...]],
        cwd: Path,
        has_ui: bool,
        notify_sink: Callable[[str, str], None] | None = None,
        ui_driver: ExtensionUiDriver | None = None,
        flags: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(sink)  # type: ignore[arg-type]
        self._lifecycle_hooks = lifecycle_hooks
        self._lifecycle_cwd = str(cwd)
        self._lifecycle_has_ui = has_ui
        self._lifecycle_notify_sink = notify_sink
        self._lifecycle_ui_driver = ui_driver
        self._lifecycle_flags = dict(flags or {})

    def set_lifecycle_hooks(
        self, lifecycle_hooks: dict[str, tuple[HookHandler, ...]]
    ) -> None:
        self._lifecycle_hooks = lifecycle_hooks

    def set_flags(self, flags: Mapping[str, object]) -> None:
        self._lifecycle_flags = dict(flags)

    def fire_lifecycle(self, name: str, *, reason: str | None = None) -> None:
        hooks = self._lifecycle_hooks.get(name)
        if not hooks:
            return
        dispatch_lifecycle_hooks(
            hooks,
            LifecycleEvent(name=name, reason=reason),
            cwd=self._lifecycle_cwd,
            has_ui=self._lifecycle_has_ui,
            notify_sink=self._lifecycle_notify_sink,
            ui_driver=self._lifecycle_ui_driver,
            flags=self._lifecycle_flags,
        )

    def agent_start(self) -> None:
        super().agent_start()
        self.fire_lifecycle(EVENT_AGENT_START)

    def agent_end(self, messages, *, will_retry: bool = False) -> None:
        super().agent_end(messages, will_retry=will_retry)
        self.fire_lifecycle(EVENT_AGENT_END)

    def turn_start(self) -> None:
        super().turn_start()
        self.fire_lifecycle(EVENT_TURN_START)

    def turn_end(self, message, tool_results) -> None:
        super().turn_end(message, tool_results)
        self.fire_lifecycle(EVENT_TURN_END)


def _parse_tool_input(arguments_json: str) -> dict[str, object]:
    """Parse a tool call's argument JSON into a dict for hook inspection.

    A non-object or unparseable payload yields an empty mapping; hooks
    must tolerate missing keys. The parsed input is for live hook
    inspection only and is not archived.
    """

    try:
        parsed = json.loads(arguments_json)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass(frozen=True, slots=True)
class _TreeCommandOutcome:
    """Result of handling a ``/tree`` command in the tool loop.

    ``prefill`` is text to rehydrate into the next prompt (user-message
    selection); ``filter_mode`` is a new active ``/tree`` filter to remember.
    Both are ``None`` when unchanged.
    """

    prefill: str | None = None
    filter_mode: str | None = None


@dataclass(frozen=True, slots=True)
class NativeToolReplResult:
    """Bounded result returned by `NativeToolReplSession.run`.

    The fields are deliberately small and metadata-only. No prompts, model
    text, tool payloads, file contents, or diffs cross this boundary.
    """

    status: HarnessStatus
    exit_code: int
    started_at: datetime
    ended_at: datetime
    provider_name: str
    model_id: str
    user_turn_count: int = 0
    tool_invocation_count: int = 0
    resource_invocation_count: int = 0
    malformed_argument_count: int = 0
    consecutive_malformed_streak: int = 0
    budget_exhausted_count: int = 0
    file_reference_count: int = 0
    file_reference_loaded_count: int = 0
    file_reference_failed_count: int = 0
    image_attachment_count: int = 0
    image_attachment_loaded_count: int = 0
    image_attachment_failed_count: int = 0
    compaction_count: int = 0
    compaction_dropped_group_count: int = 0
    error_type: str | None = None
    error_message: str | None = None


@dataclass
class NativeToolReplSession:
    """Bounded model-driven tool loop, slice 4 skeleton.

    `tool_registry` defaults to the empty production registry; tests pass a
    mapping populated with a `_FixtureTool` (or later real tools) to exercise
    the loop. `tool_budget` is per-user-turn and capped at
    `MAX_TOOL_BUDGET`. The session reads one user turn per `readline()`
    call from `input_stream` and stops when the stream returns an empty
    string (EOF) or the malformed-tool-call streak reaches
    `MAX_MALFORMED_STREAK`.
    """

    provider: ProviderPort
    tool_registry: dict[str, ToolPort] = field(default_factory=production_tool_registry)
    tool_budget: int = 50
    workspace_root: Path | None = None
    input_runtime: str = REPL_INPUT_RUNTIME_AUTO
    reference_roots: tuple[Path, ...] = field(default_factory=tuple)
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None = None
    clipboard_copy: Callable[..., ClipboardResult] = copy_to_clipboard
    # OS clipboard-image reader for the editor's Ctrl+V image paste (Pi parity);
    # tests inject a deterministic fake. Returns image bytes + media type.
    clipboard_image_read: Callable[[], ImageClipboardResult] = read_clipboard_image
    prompt_history_store: PromptHistoryStore | None = None
    # Resolved keybindings for /hotkeys (and future bound surfaces). When not
    # injected the session loads <config>/keybindings.json via the shared config
    # home; tests inject a manager directly.
    keybindings_manager: "KeybindingsManager | None" = None
    # Resolved layered settings. When not injected the session loads the
    # global+project settings for the workspace, surfaced read-only by /settings.
    settings_manager: "SettingsManager | None" = None
    resume_context: ResumeContext | None = None
    resume_branch_label: str | None = None
    # Native product session tree (the product session source of truth). When
    # not injected the loop runs on an ephemeral in-memory tree that writes no
    # file; the CLI/adapter injects a persistent tree under the native-session
    # store. ``pipy-session`` remains a separate metadata-only archive.
    native_session: "NativeSessionTree | None" = None
    # Optional Pi-shaped session-event sink for the headless automation
    # transports (``--mode json``/``--mode rpc``). When ``None`` (the CLI/TUI
    # default) every emit is a no-op and behavior is unchanged; the events are
    # derived from this real loop, never a parallel session model.
    automation_observer: "AutomationEventSink | None" = None
    # Optional external abort signal for the headless automation RPC mode. When
    # set, a non-TUI provider turn runs on a worker thread with a cancel token
    # wired to this event, so an RPC ``abort`` cancels the in-flight turn at the
    # provider boundary. ``None`` (CLI/TUI/one-shot) keeps the simple blocking
    # provider call.
    abort_event: "threading.Event | None" = None
    resource_options: RuntimeResourceOptions = field(
        default_factory=RuntimeResourceOptions.empty
    )
    # Pi-shape ``pipy "<prompt>"``: positional prompts that seed the interactive
    # session's first user turn(s). They are delivered as provider-visible prompt
    # text (like a typed message, resolving @file/@image references) before the
    # loop blocks on fresh input. Empty for the bare ``pipy`` / piped-stdin case.
    initial_messages: tuple[str, ...] = field(default_factory=tuple)

    DEFAULT_TOOL_BUDGET: ClassVar[int] = 50
    MAX_TOOL_BUDGET: ClassVar[int] = 200
    MAX_MALFORMED_STREAK: ClassVar[int] = 3

    def __post_init__(self) -> None:
        if not self.provider.supports_tool_calls:
            raise ValueError(
                f"provider {self.provider.name!r} does not advertise "
                "supports_tool_calls=True; the pipy repl requires a "
                "tool-capable provider"
            )
        if isinstance(self.tool_budget, bool) or not isinstance(
            self.tool_budget, int
        ):
            raise TypeError("tool_budget must be an int")
        if self.tool_budget < 1 or self.tool_budget > self.MAX_TOOL_BUDGET:
            raise ValueError(
                "tool_budget must be in "
                f"[1, {self.MAX_TOOL_BUDGET}]; got {self.tool_budget}"
            )

    def run(
        self,
        *,
        workspace_root: Path | None = None,
        input_stream: TextIO,
        output_stream: TextIO,
        error_stream: TextIO,
        system_prompt: str = "",
        provider_name: str | None = None,
        model_id: str | None = None,
    ) -> NativeToolReplResult:
        cwd = workspace_root or self.workspace_root
        if cwd is None:
            raise ValueError("NativeToolReplSession.run requires a workspace_root")
        cwd = cwd.expanduser().resolve()
        if not cwd.is_dir():
            raise ValueError(f"workspace_root is not a directory: {cwd}")

        def _stderr_sink(text: str) -> None:
            error_stream.write(text)

        effective_provider_name = provider_name or self.provider.name
        effective_model_id = model_id or self.provider.model_id

        def _bind_unavailable_after_reload(message: str) -> None:
            self.provider = _UnavailableAfterReloadProvider(
                name=effective_provider_name,
                model_id=effective_model_id,
                error_message=message,
            )

        keybindings = self.keybindings_manager or KeybindingsManager.create()
        settings = self.settings_manager or SettingsManager.for_workspace(cwd)
        resource_options = self.resource_options
        # Compose installed package resources: resolve local paths and managed
        # git caches, then install the package theme registry so package
        # skills/prompts/extensions/themes flow through discovery at lowest
        # precedence with the Pi-shaped enablement filters applied.
        package_roots = compose_package_runtime(
            settings,
            cwd,
            include_package_themes=not resource_options.no_themes,
            explicit_theme_paths=resource_options.theme_paths,
        )
        # Apply the settings resource enable/disable directives (Pi pi config):
        # disabled skills/prompts are dropped from what is registered.
        workspace_resources = WorkspaceResources.discover(
            cwd,
            package_roots=package_roots,
            explicit_skill_paths=resource_options.skill_paths,
            explicit_prompt_template_paths=resource_options.prompt_template_paths,
            include_skills_defaults=not resource_options.no_skills,
            include_prompt_template_defaults=not resource_options.no_prompt_templates,
        ).with_enablement(
            skills_patterns=settings.get_skills_patterns(),
            prompts_patterns=settings.get_prompts_patterns(),
            enable_skill_commands=settings.get_enable_skill_commands(),
        )
        # Discover + activate Python extensions and project their slash
        # commands. Activation runs extension code; a failing extension is
        # disabled without affecting the session.
        # Built-in tool names are reserved so an extension tool can never
        # shadow a built-in tool.
        _ext_runtime = _activate_workspace_extensions(
            cwd,
            workspace_resources,
            tuple(self.tool_registry.keys()),
            package_roots=()
            if resource_options.no_extensions
            else package_roots.extensions,
            extension_patterns=settings.get_extensions_patterns(),
            explicit_extension_paths=resource_options.extension_paths,
            include_default_extensions=not resource_options.no_extensions,
        )
        extension_commands = _ext_runtime.commands
        extension_menu_names = _ext_runtime.menu_names
        extension_descriptions = _ext_runtime.descriptions
        extension_tool_call_hooks_ = _ext_runtime.tool_call_hooks
        extension_lifecycle_hooks = _ext_runtime.lifecycle_hooks
        extension_input_hooks = _ext_runtime.input_hooks
        extension_before_agent_start_hooks = _ext_runtime.before_agent_start_hooks
        extension_tool_result_hooks = _ext_runtime.tool_result_hooks
        extension_user_bash_hooks = _ext_runtime.user_bash_hooks
        extension_before_provider_request_hooks = (
            _ext_runtime.before_provider_request_hooks
        )
        extension_session_before_switch_hooks = _ext_runtime.session_before_switch_hooks
        extension_session_before_fork_hooks = _ext_runtime.session_before_fork_hooks
        extension_session_before_compact_hooks = (
            _ext_runtime.session_before_compact_hooks
        )
        extension_session_before_tree_hooks = _ext_runtime.session_before_tree_hooks
        extension_message_outbox = _ext_runtime.outbox
        extension_renderer_map = _ext_runtime.message_renderers
        extension_flag_values, extension_flag_error = parse_extension_flag_tokens(
            _ext_runtime.flags,
            tuple(resource_options.extension_flag_tokens),
        )
        if extension_flag_error is not None:
            print(f"pipy: {extension_flag_error}", file=error_stream)
            now = datetime.now(UTC)
            return NativeToolReplResult(
                status=HarnessStatus.FAILED,
                exit_code=2,
                started_at=now,
                ended_at=now,
                provider_name=effective_provider_name,
                model_id=effective_model_id,
                error_type="ExtensionFlagError",
                error_message=extension_flag_error,
            )
        if isinstance(self.provider_state, NativeReplProviderState):
            catalog_state = self.provider_state.catalog_state
            if catalog_state is not None:
                was_extension_selection = (
                    self.provider_state.current_selection_uses_extension_provider()
                )
                catalog_state.set_extension_provider_contributions(  # type: ignore[attr-defined]
                    _ext_runtime.providers,
                    _ext_runtime.unregistered_providers,
                )
                if (
                    not self.provider_state.current_selection_supported()
                    or (
                        was_extension_selection
                        and not self.provider_state.current_selection_uses_extension_provider()
                    )
                ):
                    fallback = self.provider_state.reset_to_first_available_model(
                        require_tool_calls=True
                    )
                    if fallback is None:
                        raise ValueError(
                            "selected provider is unavailable after extension "
                            "activation, and no available tool-capable fallback "
                            "was found"
                        )
                    self.provider = self.provider_state.current_provider()
                    effective_provider_name = fallback.provider_name
                    effective_model_id = fallback.model_id
                    print(
                        "pipy: active model disappeared on startup; selected "
                        f"{fallback.reference}.",
                        file=error_stream,
                    )
        # Prompts an extension enqueues via send_user_message become the
        # next prompts processed by the loop (deterministic turns).
        extension_pending_messages: list[str] = []
        # Positional prompts from ``pipy "<prompt>"`` seed the first user turn(s)
        # before the loop blocks on stdin. They drain ahead of everything else so
        # the seeded message is the session's first user message.
        seed_pending_messages: list[str] = [
            message for message in self.initial_messages if message
        ]
        terminal_ui = self._build_terminal_ui(
            input_stream=input_stream,
            error_stream=error_stream,
            workspace=cwd,
            resources=workspace_resources,
            autocomplete_max_visible=settings.get_autocomplete_max_visible(),
            extension_menu_names=extension_menu_names,
            extension_descriptions=extension_descriptions,
            extension_shortcut_keys=frozenset(_ext_runtime.shortcuts),
        )

        # Live UI sink for extension `ctx.ui.notify` from hooks and tools:
        # notifications are emitted as local diagnostics (interactive) and
        # degrade deterministically in non-interactive mode.
        def _extension_notify(_kind: str, message: str) -> None:
            safe_message = "\n".join(
                sanitize_label_text(line) for line in str(message).splitlines()
            )
            self._emit_diagnostic(terminal_ui, error_stream, safe_message)

        # A bounded one-shot completion handed to extension command handlers as
        # `ctx.complete(system_prompt, user_text)`: runs a single provider turn
        # with the active provider/model and no tools, and returns its text. It
        # is a normal provider call (subject to the same auth); inputs are
        # capped so a buggy handler cannot create unbounded provider input.
        _EXTENSION_COMPLETE_MAX_CHARS = 100 * 1024

        def _extension_complete(system_prompt: str, user_text: str) -> str:
            request = ProviderRequest(
                system_prompt=str(system_prompt)[:_EXTENSION_COMPLETE_MAX_CHARS],
                user_prompt=str(user_text)[:_EXTENSION_COMPLETE_MAX_CHARS],
                provider_name=effective_provider_name,
                model_id=effective_model_id,
                cwd=cwd,
                available_tools=(),
            )
            result = self.provider.complete(request)
            if result.status != HarnessStatus.SUCCEEDED:
                raise ExtensionCapabilityError(
                    f"completion failed ({result.error_type or result.status})"
                )
            return result.final_text or ""

        def _extension_custom_driver(factory: Any) -> object:
            # Only an interactive terminal can take over the screen; a
            # captured-stream run degrades to a deterministic no-op (also
            # enforced by ExtensionUi.custom when has_ui is False).
            if terminal_ui is None:
                return None
            return terminal_ui.run_custom_component(factory)

        extension_ui_driver = (
            _LiveExtensionUiDriver(terminal_ui, cwd) if terminal_ui is not None else None
        )

        # Merge activated extension tools into this run's tool registry
        # (the shared built-in registry is never mutated). Extension tools
        # join the bounded tool loop with the same schema validation +
        # output bounds as built-ins.
        run_tool_registry: dict[str, ToolPort] = dict(self.tool_registry)
        extension_render_details: dict[str, object] = {}
        extension_tool_renderers: dict[str, ExtensionTool] = {
            rt.tool.name: rt.tool
            for rt in _ext_runtime.tools
            if rt.tool.render_call is not None or rt.tool.render_result is not None
        }
        for _registered_tool in _ext_runtime.tools:
            _port = _ExtensionToolPort(
                _registered_tool,
                has_ui=terminal_ui is not None,
                notify_sink=_extension_notify,
                flags=extension_flag_values,
                render_details_sink=extension_render_details,
            )
            run_tool_registry[_port.definition.name] = _port
        active_tool_names: set[str] | None = None
        # Image attachments may reference an owner-only clipboard temp dir
        # (Ctrl+V paste); that dir is added to the image reference roots so a
        # pasted ``@image:<temp>`` resolves while the workspace path policy is
        # otherwise unchanged. File-reference (@path) reads do not use it.
        image_reference_roots = self.reference_roots
        if terminal_ui is not None:
            # Seed the thinking-block fold (Ctrl+T) from the persisted setting.
            terminal_ui.thinking_hidden = settings.get_hide_thinking_block()
            clipboard_dir = Path(tempfile.mkdtemp(prefix="pipy-clipboard-"))
            try:
                clipboard_dir.chmod(0o700)
            except OSError:
                pass
            terminal_ui.clipboard_temp_dir = clipboard_dir
            terminal_ui.clipboard_image_read = self.clipboard_image_read
            image_reference_roots = (*self.reference_roots, clipboard_dir)
        # Local-only persistent prompt-history store (independent of the
        # metadata-first session archive). Built once per session; the
        # ``/settings`` dialog toggles/clears it. When enabled, a fresh TUI
        # session seeds its in-memory recall buffer from the saved prompts.
        prompt_history_store = self.prompt_history_store or PromptHistoryStore()
        # Settings is the source of truth for the prompt-history toggle: when it
        # sets promptHistory.enabled, surface that into the store (which remains
        # the on-disk recall cache) so a fresh session honors the setting.
        if settings.get_prompt_history_enabled() and not prompt_history_store.enabled:
            prompt_history_store.set_enabled(True)
        if terminal_ui is not None and prompt_history_store.enabled:
            terminal_ui.input_history = list(prompt_history_store.entries())
        renderer: _ToolLoopRenderer | _TuiToolLoopRenderer
        if terminal_ui is not None:
            renderer = _TuiToolLoopRenderer(
                ui=terminal_ui,
                tool_renderers=extension_tool_renderers,
                render_details_sink=extension_render_details,
            )
        else:
            renderer = _ToolLoopRenderer(
                output_stream=output_stream,
                error_stream=error_stream,
                tool_renderers=extension_tool_renderers,
                render_details_sink=extension_render_details,
            )
        # Pi-shaped session-event emitter for the headless automation transports.
        # A no-op when no observer is attached (CLI/TUI), so the interactive path
        # is unchanged; otherwise it serializes this real loop's lifecycle onto
        # Pi's AgentSessionEvent vocabulary.
        # Extension-aware emitter: also fires the lifecycle `@api.on(...)`
        # observers at the existing agent/turn emit points (no-op when no
        # lifecycle hooks were registered).
        emitter = _ExtensionAwareEmitter(
            self.automation_observer,
            lifecycle_hooks=extension_lifecycle_hooks,
            cwd=cwd,
            has_ui=terminal_ui is not None,
            notify_sink=_extension_notify,
            ui_driver=extension_ui_driver,
            flags=extension_flag_values,
        )
        # `session_start` fires once the session is set up (reason "startup");
        # `session_shutdown` fires when the run ends.
        # Stream long-running tool output (e.g. pytest dots) into the live UI as
        # it is produced, matching Pi. Rebuilt here so the sink can target the
        # renderer that was just selected. For automation it also emits Pi's
        # ``tool_execution_update`` (bounded progress) for the currently
        # executing tool call; ``_active_tool_call`` carries that call.
        active_tool_call: list["ProviderToolCall | None"] = [None]

        def _tool_output_sink(chunk: str) -> None:
            renderer.tool_output_sink(chunk)
            if not emitter.enabled:
                return
            call = active_tool_call[0]
            if call is not None:
                emitter.tool_execution_update(
                    tool_call_id=call.provider_correlation_id,
                    tool_name=call.tool_name,
                    args=parse_tool_arguments(call.arguments_json),
                    partial_result=chunk,
                )

        context = ToolContext(
            workspace_root=cwd,
            stderr_sink=_stderr_sink,
            reference_roots=self.reference_roots,
            output_sink=_tool_output_sink,
        )

        started_at = datetime.now(UTC)
        # Native product session tree: the durable source of truth. When not
        # injected we run on an ephemeral in-memory tree (no file). The live
        # ``messages`` list mirrors the tree's active branch and remains the
        # provider-visible list (carrying any in-memory compaction); every
        # append is mirrored to the tree so /tree navigation, resume, fork,
        # clone, and durable compaction read the same conversation.
        session_tree = self.native_session or NativeSessionTree.create(
            cwd, persist=False
        )
        messages: list[LoopMessage] = list(session_tree.build_context().messages)
        resource_invocation_count = 0
        user_turn_count = 0
        tool_invocation_count = 0
        malformed_argument_count = 0
        consecutive_malformed_streak = 0
        budget_exhausted_count = 0
        file_reference_count = 0
        file_reference_loaded_count = 0
        file_reference_failed_count = 0
        image_attachment_count = 0
        image_attachment_loaded_count = 0
        image_attachment_failed_count = 0
        compaction_count = 0
        compaction_dropped_group_count_total = 0
        # Native session-tree command state. ``pending_prefill`` carries text
        # from a ``/tree`` user-message selection back into the next prompt
        # (rehydrated editor in the live TUI). ``tree_filter_mode`` is the
        # active ``/tree`` filter.
        pending_prefill: str | None = None
        tree_filter_mode = "default"
        # Mutable safe summary suffix appended to the system prompt after a
        # /compact or auto-compaction; the base system prompt itself is never
        # mutated. base_system_prompt already carries any resume seed block.
        base_system_prompt = system_prompt
        compaction_summary = ""
        usage_accumulator = _UsageAccumulator()
        usage_accumulator.bind(effective_provider_name, effective_model_id)

        def render_extension_custom_entry(
            custom_type: str,
            data: object | None,
            *,
            width: int,
            expanded: bool,
            stream: TextIO,
        ) -> RenderedCustomEntry:
            # Local import: the render-theme machinery is only needed on the
            # rarely hit custom-entry path, so keep it off this module's hot
            # import path (mirrors the tool-renderer ``_dispatch_render`` sites).
            from pipy_harness.native.chrome import chrome_style_for
            from pipy_harness.native.tool_renderers import build_tool_render_theme

            style = chrome_style_for(stream)
            return render_extension_message(
                extension_renderer_map,
                custom_type,
                data,
                width=width,
                expanded=expanded,
                theme=build_tool_render_theme(style),
            )

        def add_rendered_custom_entry_to_terminal(
            custom_type: str,
            data: object | None,
        ) -> None:
            if terminal_ui is None:
                return
            rendered = render_extension_custom_entry(
                custom_type,
                data,
                width=terminal_ui._dimensions()[0],
                expanded=terminal_ui.tools_expanded,
                stream=terminal_ui.terminal_stream,
            )
            if rendered.styled:
                terminal_ui.add_custom_entry_styled(rendered.lines)
            else:
                terminal_ui.add_custom_entry(custom_type, rendered.lines)

        def replay_custom_entries_to_terminal() -> None:
            if terminal_ui is not None:
                for entry in session_tree.get_branch():
                    if isinstance(entry, _CustomEntry):
                        add_rendered_custom_entry_to_terminal(
                            entry.custom_type, safe_custom_entry_data(entry.data)
                        )
                    elif isinstance(entry, _CustomMessageEntry) and entry.display:
                        terminal_ui.add_custom_entry(
                            entry.custom_type,
                            entry.content.splitlines() or [""],
                        )

        def extension_append_entry(custom_type: str, data: object | None = None) -> object:
            safe_type = str(custom_type).strip()
            if not is_valid_custom_entry_type(safe_type):
                raise ValueError("invalid custom entry type")
            safe_data = safe_custom_entry_data(data)
            appended = session_tree.append_custom(safe_type, safe_data)
            if terminal_ui is not None:
                add_rendered_custom_entry_to_terminal(safe_type, safe_data)
            else:
                rendered = render_extension_custom_entry(
                    safe_type,
                    safe_data,
                    width=80,
                    expanded=False,
                    stream=error_stream,
                )
                lines = "\n".join(str(line) for line in rendered.lines)
                self._emit_diagnostic(
                    terminal_ui,
                    error_stream,
                    f"{safe_type}:\n{lines}" if lines else safe_type,
                )
            return appended.id

        def refresh_footer_text() -> None:
            if terminal_ui is not None:
                terminal_ui.set_footer_text(
                    self._footer_text(
                        cwd=cwd,
                        provider_name=effective_provider_name,
                        model_id=effective_model_id,
                        user_turn_count=user_turn_count,
                        tool_invocation_count=tool_invocation_count,
                        error_stream=error_stream,
                        usage_accumulator=usage_accumulator,
                    )
                )

        def extension_set_active_tools(tool_names: Sequence[str]) -> bool:
            """Restrict model-visible tools for future provider requests."""

            nonlocal active_tool_names
            normalized = {str(name) for name in tool_names if str(name)}
            if not normalized:
                active_tool_names = set()
                return True
            if any(name not in run_tool_registry for name in normalized):
                return False
            active_tool_names = normalized
            return True

        def extension_set_model(reference: str) -> bool:
            ok, _message = apply_model_selection(reference)
            return ok

        def extension_set_thinking_level(level: str) -> bool:
            """Set the active reasoning level through the provider state."""

            state = self.provider_state
            if not isinstance(state, NativeReplProviderState):
                return False
            normalized = str(level).strip().lower()
            if normalized not in {"off", "minimal", "low", "medium", "high", "xhigh"}:
                return False
            current = state.current_selection()
            supports_thinking = any(
                option.selection.provider_name == current.provider_name
                and option.selection.model_id == current.model_id
                and bool(option.reasoning)
                for option in state.model_options()
            )
            if normalized != "off" and not supports_thinking:
                return False
            state.thinking_level = normalized
            session_tree.append_thinking_level_change(normalized)
            refresh_footer_text()
            return True

        def available_tool_definitions(
            override_names: Sequence[str] | None = None,
        ) -> tuple[ToolDefinition, ...]:
            allowed = (
                set(str(name) for name in override_names)
                if override_names is not None
                else active_tool_names
            )
            return tuple(
                port.definition
                for name, port in run_tool_registry.items()
                if allowed is None or name in allowed
            )

        def apply_model_selection(reference: str) -> tuple[bool, str]:
            """Select ``reference`` through the provider-state boundary.

            Mirrors the no-tool ``/model`` path: on success it rebinds the live
            provider, clears the in-memory conversation context, rebinds the
            usage meter, and refreshes the footer/status model label so the next
            provider turn is constructed with the new provider/model. The switch
            is refused (and the previous selection restored) when the chosen
            provider does not advertise tool-call support, which the product
            REPL requires. No provider turn happens here.
            """

            nonlocal effective_provider_name, effective_model_id
            nonlocal usage_accumulator, messages
            state = self.provider_state
            if not isinstance(state, NativeReplProviderState):
                return False, (
                    "pipy: /model is unavailable for this REPL provider state."
                )
            previous_selection = state.current_selection()
            ok, message = state.select_model(reference)
            if not ok:
                return False, message
            new_provider = state.current_provider()
            if not getattr(new_provider, "supports_tool_calls", False):
                # Restore the prior selection directly rather than via
                # select_model(): the previous selection may be an explicit,
                # tool-capable provider that is not "available" under the
                # env-credential probe (e.g. an injected provider), in which
                # case re-selecting it would fail and silently leave the
                # rejected selection (and persisted default) in place.
                state.selection = previous_selection
                state._save_default(previous_selection)
                return False, (
                    f"pipy: {reference} does not support tool calls in "
                    "tool-loop mode; selection unchanged."
                )
            self.provider = new_provider
            selection = state.current_selection()
            effective_provider_name = selection.provider_name
            effective_model_id = selection.model_id
            messages = []
            usage_accumulator = _UsageAccumulator()
            usage_accumulator.bind(effective_provider_name, effective_model_id)
            if terminal_ui is not None:
                terminal_ui.set_footer_text(
                    self._footer_text(
                        cwd=cwd,
                        provider_name=effective_provider_name,
                        model_id=effective_model_id,
                        user_turn_count=user_turn_count,
                        tool_invocation_count=tool_invocation_count,
                        error_stream=error_stream,
                        usage_accumulator=usage_accumulator,
                    )
                )
            return True, message

        def apply_auth_change(action: str, argument: str) -> str:
            """Run ``/login`` or ``/logout`` through the auth boundary.

            Mirrors the no-tool auth path through the same
            ``NativeReplProviderState``: it performs no provider turn and no
            tool call, clears the in-memory conversation, then rebinds the live
            provider/usage/footer so refreshed model-option availability and the
            (possibly reset) selection take effect on the next turn. Interactive
            login output (the OAuth URL/prompt) renders only on the live
            terminal — never in the session archive — and the TUI live region is
            suspended around it so the inline frame repaints coherently
            afterward.
            """

            nonlocal effective_provider_name, effective_model_id
            nonlocal usage_accumulator, messages
            state = self.provider_state
            if not isinstance(state, NativeReplProviderState):
                return (
                    f"pipy: /{action} is unavailable for this REPL provider state."
                )
            provider_name = argument or "openai-codex"
            if action == "login":
                if terminal_ui is not None:
                    terminal_ui.suspend_for_external_io()
                try:
                    _ok, message = state.login(
                        provider_name,
                        input_stream=input_stream,
                        output_stream=error_stream,
                    )
                except Exception as exc:  # noqa: BLE001 - report, never crash REPL
                    message = (
                        "pipy: openai-codex login failed with "
                        f"{type(exc).__name__}: {sanitize_text(str(exc))}"
                    )
            else:
                try:
                    _ok, message = state.logout(provider_name)
                except Exception as exc:  # noqa: BLE001 - report, never crash REPL
                    message = (
                        "pipy: openai-codex logout failed with "
                        f"{type(exc).__name__}: {sanitize_text(str(exc))}"
                    )
            # Clear context and rebind the live provider regardless of outcome,
            # so a credential change never leaks prior context or leaves a stale
            # provider bound (logout resets the selection to the local default).
            # The persisted default stays the inert ``fake-native-bootstrap``;
            # the product REPL upgrades the *live* fake selection to the
            # tool-capable ``fake-tools`` here so the next turn has tool support.
            state.selection = normalize_repl_fake_selection(state.current_selection())
            self.provider = state.current_provider()
            selection = state.current_selection()
            effective_provider_name = selection.provider_name
            effective_model_id = selection.model_id
            messages = []
            usage_accumulator = _UsageAccumulator()
            usage_accumulator.bind(effective_provider_name, effective_model_id)
            if terminal_ui is not None:
                terminal_ui.set_footer_text(
                    self._footer_text(
                        cwd=cwd,
                        provider_name=effective_provider_name,
                        model_id=effective_model_id,
                        user_turn_count=user_turn_count,
                        tool_invocation_count=tool_invocation_count,
                        error_stream=error_stream,
                        usage_accumulator=usage_accumulator,
                    )
                )
            return message

        def apply_compaction(trigger: str) -> str:
            """Compact the in-memory provider history at a user-turn boundary.

            Returns a safe diagnostic string. The cut keeps the most recent
            turns and replaces the dropped prefix with a metadata-only summary
            appended to the system prompt; provider/model, usage counters,
            prompt history, and the TUI frame are all left intact. No tool
            result is orphaned because the cut is at a UserMessage boundary.
            """

            nonlocal messages, compaction_summary, compaction_count
            nonlocal compaction_dropped_group_count_total
            decision = dispatch_session_before_hooks(
                extension_session_before_compact_hooks,
                operation="compact",
                cwd=str(cwd),
                has_ui=terminal_ui is not None,
                trigger=trigger,
                notify_sink=_extension_notify,
                set_active_tools_fn=extension_set_active_tools,
                set_model_fn=extension_set_model,
                set_thinking_level_fn=extension_set_thinking_level,
                flags=extension_flag_values,
            )
            if not decision.allow:
                reason = decision.reason or "blocked by extension"
                return f"pipy: compact blocked by extension: {reason}"
            result = compact_tool_loop_messages(messages)
            if not result.changed:
                return "pipy: nothing to compact yet."
            messages = list(result.messages)
            compaction_summary = f"\n\n{result.summary_block}"
            compaction_count += 1
            compaction_dropped_group_count_total += result.dropped_group_count
            # Durable compaction: append a real ``compaction`` entry to the
            # native session tree so resumed and /tree-navigated sessions
            # rebuild the same reduced context. The boundary is the first
            # retained user-turn on the active branch (after any prior
            # compaction); the live in-memory reduction above keeps the
            # provider request small for this session.
            _append_durable_compaction(result.summary_block, result.bytes_before)
            return (
                f"pipy: compacted conversation context ({trigger}; dropped "
                f"{result.dropped_group_count} earlier exchange(s), kept "
                f"{result.retained_group_count})."
            )

        def _append_durable_compaction(summary_block: str, bytes_before: int) -> None:
            branch = session_tree.get_branch()
            last_compaction = -1
            for i, entry in enumerate(branch):
                if isinstance(entry, _CompactionEntry):
                    last_compaction = i
            segment = branch[last_compaction + 1 :]
            user_entries = [
                entry
                for entry in segment
                if isinstance(entry, _MessageEntry)
                and isinstance(entry.message, UserMessage)
            ]
            if len(user_entries) <= DEFAULT_KEEP_RECENT_GROUPS:
                return
            first_kept = user_entries[len(user_entries) - DEFAULT_KEEP_RECENT_GROUPS]
            session_tree.append_compaction(
                summary=summary_block.strip(),
                first_kept_entry_id=first_kept.id,
                tokens_before=bytes_before,
            )

        repl_input = (
            terminal_ui
            if terminal_ui is not None
            else self._build_repl_input(
                input_stream=input_stream,
                error_stream=error_stream,
                workspace=cwd,
                resources=workspace_resources,
                extension_menu_names=extension_menu_names,
                extension_descriptions=extension_descriptions,
            )
        )
        if terminal_ui is None:
            print_startup_chrome(
                error_stream, cwd=cwd, quiet=settings.get_quiet_startup()
            )
            if self.resume_context is not None:
                print(
                    "pipy: "
                    + compose_resume_status_line(
                        self.resume_context,
                        branch_label=self.resume_branch_label,
                    ),
                    file=error_stream,
                )
        else:
            terminal_ui.set_footer_text(
                self._footer_text(
                    cwd=cwd,
                    provider_name=effective_provider_name,
                    model_id=effective_model_id,
                    user_turn_count=user_turn_count,
                    tool_invocation_count=tool_invocation_count,
                    error_stream=error_stream,
                    usage_accumulator=usage_accumulator,
                )
            )
            terminal_ui.start()
            if self.resume_context is not None:
                # Safe resumed-state notice committed to scrollback at startup:
                # prior session id, provider, model, turn count, finalized time
                # (and branch label) only — never prompts, output, or summary.
                terminal_ui.add_notice(
                    compose_resume_status_line(
                        self.resume_context,
                        branch_label=self.resume_branch_label,
                    )
                )
            replay_custom_entries_to_terminal()

        # Startup changelog: on a fresh session, show the entries new since the
        # stored lastChangelogVersion (or a condensed line under collapseChangelog)
        # and record the current version. First run / resumed sessions show
        # nothing. Runs no provider turn.
        changelog_lines, store_version = changelog_startup(
            read_changelog_entries(),
            last_version=settings.get_last_changelog_version(),
            current_version=pipy_version(),
            collapse=settings.get_collapse_changelog(),
            is_fresh=self.resume_context is None,
        )
        for line in changelog_lines:
            if terminal_ui is not None:
                terminal_ui.add_notice(line)
            else:
                print(line, file=error_stream)
        if store_version is not None:
            try:
                settings.set_last_changelog_version(store_version)
            except RuntimeError:
                pass

        def legacy_footer_enabled() -> bool:
            return terminal_ui is None and repl_input.runtime_label != "slash-menu"

        def refresh_legacy_footer() -> None:
            if legacy_footer_enabled():
                self._print_footer(
                    error_stream,
                    cwd=cwd,
                    provider_name=effective_provider_name,
                    model_id=effective_model_id,
                    user_turn_count=user_turn_count,
                    tool_invocation_count=tool_invocation_count,
                )

        def diag(message: str) -> None:
            self._emit_diagnostic(terminal_ui, error_stream, message)

        def extension_session_allows(
            hooks: Sequence[HookHandler],
            *,
            operation: str,
            target: str | None = None,
            trigger: str | None = None,
        ) -> bool:
            decision = dispatch_session_before_hooks(
                hooks,
                operation=operation,
                cwd=str(cwd),
                has_ui=terminal_ui is not None,
                target=target,
                trigger=trigger,
                notify_sink=_extension_notify,
                set_active_tools_fn=extension_set_active_tools,
                set_model_fn=extension_set_model,
                set_thinking_level_fn=extension_set_thinking_level,
                flags=extension_flag_values,
            )
            if decision.allow:
                return True
            reason = decision.reason or "blocked by extension"
            diag(f"pipy: {operation} blocked by extension: {reason}")
            return False

        def rebuild_messages_from_tree() -> None:
            """Rebuild the live provider-visible list from the active branch.

            Used after ``/tree`` navigation, ``/new``, and ``/resume``: the
            native tree is the source of truth, so the provider list and the
            system-prompt compaction suffix are reset to match the (possibly
            compacted) active branch.
            """

            nonlocal messages, compaction_summary
            messages = list(session_tree.build_context().messages)
            compaction_summary = ""

        def summarize_branch(
            branch_messages: list[LoopMessage], focus: str | None
        ) -> str | None:
            """Summarize an abandoned branch through the active provider.

            Runs one bounded provider turn (no tools) and returns the summary
            text, or ``None`` when the provider fails so the caller can leave
            the tree and leaf unchanged.
            """

            if not branch_messages:
                return None
            instruction = (
                "Summarize the following abandoned conversation branch "
                "concisely so it can be referenced later."
            )
            if focus:
                instruction += f" Focus on: {focus}."
            request = ProviderRequest(
                system_prompt=instruction,
                user_prompt="Provide the branch summary now.",
                provider_name=effective_provider_name,
                model_id=effective_model_id,
                cwd=cwd,
                messages=tuple(branch_messages),
                available_tools=(),
            )
            try:
                result = self.provider.complete(request)
            except Exception:  # noqa: BLE001 - never crash the REPL
                return None
            if result.status != HarnessStatus.SUCCEEDED:
                return None
            return (result.final_text or "").strip() or None

        def current_session_dir() -> Path:
            if session_tree.path is not None:
                return session_tree.path.parent
            return default_native_session_dir(cwd)

        def resolve_session_file(ref: str) -> Path | None:
            return resolve_session_target(current_session_dir(), ref)

        # Pi-parity: the slash-menu input adapter draws the bottom status
        # block (cwd + status line) live below the input area, so we only
        # emit a pre-loop frame for non-slash-menu runtimes. This avoids a
        # duplicate cwd/status row above the prompt area in TTY sessions,
        # while keeping the captured-stream/plain case visible on immediate
        # EOF. `_print_footer` re-emits it after each submission.
        if legacy_footer_enabled():
            self._print_footer(
                error_stream,
                cwd=cwd,
                provider_name=effective_provider_name,
                model_id=effective_model_id,
                user_turn_count=user_turn_count,
                tool_invocation_count=tool_invocation_count,
                usage_accumulator=usage_accumulator,
            )

        # session_start fires once the session is set up; session_shutdown
        # is fired from the finally below so it runs on EVERY exit path
        # (normal return, fatal return, or a propagated exception).
        emitter.fire_lifecycle(EVENT_SESSION_START, reason="startup")
        try:
            while True:
                if terminal_ui is None:
                    print_input_separator(error_stream)
                footer_text = self._footer_text(
                    cwd=cwd,
                    provider_name=effective_provider_name,
                    model_id=effective_model_id,
                    user_turn_count=user_turn_count,
                    tool_invocation_count=tool_invocation_count,
                    error_stream=error_stream,
                    usage_accumulator=usage_accumulator,
                )
                if pending_prefill is not None:
                    # A ``/tree`` user-message selection puts the chosen text back
                    # into the editor. The live TUI rehydrates the editor directly;
                    # captured-stream callers see a hint and type the (edited) text
                    # as the next line, which branches from the selected parent.
                    if terminal_ui is not None and hasattr(
                        terminal_ui, "set_input_text"
                    ):
                        terminal_ui.set_input_text(pending_prefill)
                    elif terminal_ui is None:
                        diag(
                            "pipy: editor rehydrated with selected message; "
                            "type your (edited) message to branch from here, or "
                            "submit as-is.\n"
                            f"  > {pending_prefill}"
                        )
                    pending_prefill = None
                # A local command (`/…`/`!…`) submitted with Enter mid-turn runs
                # locally (Pi): it is dispatched here through the NORMAL path (it is
                # not a drained message, so ``command_text`` keeps its value below
                # and local-command dispatch applies) before any queued prompts.
                # Drain any messages an extension enqueued via
                # send_user_message (from a command, hook, or other
                # callback) at the top of every iteration, so they are
                # always scheduled as deterministic prompts regardless of
                # which callback queued them.
                extension_pending_messages.extend(
                    message.content
                    for message in drain_user_messages(extension_message_outbox)
                )
                pending_command = (
                    terminal_ui.take_pending_command()
                    if terminal_ui is not None
                    else None
                )
                # Deliver any queued steering/follow-up messages (Pi) before reading
                # fresh input: a steering interrupt or a turn that settled with
                # follow-ups promotes them to a sequential drain, delivered in order
                # (all steering, then all follow-up) as the next prompts.
                drained = (
                    None
                    if pending_command is not None
                    else (
                        terminal_ui.take_next_drain() if terminal_ui is not None else None
                    )
                )
                # Positional-prompt seeds (`pipy "<prompt>"`) drain first, ahead
                # of extension messages and fresh input, so a seeded prompt is the
                # session's first user message. Like steering/extension prompts it
                # travels the `drained` path (provider-visible text, never parsed
                # as a local command).
                if (
                    drained is None
                    and pending_command is None
                    and seed_pending_messages
                ):
                    drained = seed_pending_messages.pop(0)
                # Prompts an extension enqueued via send_user_message are
                # delivered through the same `drained` path as Pi
                # steering/follow-ups: provider-visible prompt text, never
                # parsed as a local command (so a queued "/hotkeys" is a
                # prompt, not the hotkeys command). They come after user steering and
                # before blocking on fresh input.
                if (
                    drained is None
                    and pending_command is None
                    and extension_pending_messages
                ):
                    drained = extension_pending_messages.pop(0)
                if pending_command is not None:
                    line = f"{pending_command}\n"
                elif drained is not None:
                    line = f"{drained}\n"
                else:
                    try:
                        # Pi's input cursor has no leading `> ` glyph; the
                        # separator pair above and below the input area is the
                        # visual frame instead. Pass an empty prompt so the
                        # readline / slash-menu adapter renders just the cursor.
                        line = repl_input.read_line("", footer=footer_text)
                    except KeyboardInterrupt:
                        print(file=error_stream)
                        break
                if not line:
                    break
                user_input = line.rstrip("\n")
                stripped = user_input.strip()
                # Queued steering/follow-up messages (Pi) are provider-visible prompt
                # text, never local commands: a follow-up enqueued mid-turn that
                # happens to begin with `/` (slash command) or `!` (bash shortcut)
                # must reach the model verbatim, not be intercepted and silently
                # dropped from the conversation. ``command_text`` is the dispatch key
                # for every local command/hotkey below; it is blank for a drained
                # line so none match and it falls through to the provider-message
                # path (which still resolves any @file/@image references). Typed
                # input keeps ``command_text == stripped`` and is unaffected.
                command_text = "" if drained is not None else stripped
                # In-editor hotkeys arrive as private sentinel "commands" from the
                # TUI so they dispatch without rendering a user-message bubble.
                # Shift+Tab cycles the thinking level; Ctrl+P / Shift+Ctrl+P cycle
                # the model (translated to the existing /scoped-models dispatch).
                if command_text in {HOTKEY_TOGGLE_TOOLS, HOTKEY_TOGGLE_THINKING}:
                    self._toggle_view_fold(
                        stripped,
                        terminal_ui=terminal_ui,
                        error_stream=error_stream,
                        settings=settings,
                    )
                    continue
                if command_text == HOTKEY_THINKING_CYCLE:
                    self._cycle_thinking_level(
                        terminal_ui=terminal_ui,
                        error_stream=error_stream,
                        session_tree=session_tree,
                    )
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                            usage_accumulator=usage_accumulator,
                        )
                    continue
                if command_text.startswith(HOTKEY_EXTENSION_SHORTCUT_PREFIX):
                    # An activated extension's registered keyboard shortcut
                    # fired; dispatch its handler with the same mode-aware
                    # context as its command. Like the command path, a handler
                    # that calls api.send_user_message enqueues to the shared
                    # outbox, which is drained into a deterministic provider
                    # prompt at the top of the next iteration (see the
                    # drain_user_messages call above) — so the turn fires; this
                    # branch only needs to surface a handler failure and
                    # continue. Covered by
                    # test_shortcut_send_user_message_triggers_a_turn.
                    shortcut_key = command_text[
                        len(HOTKEY_EXTENSION_SHORTCUT_PREFIX) :
                    ]
                    shortcut_dispatch = dispatch_extension_shortcut(
                        shortcut_key,
                        _ext_runtime.shortcuts,
                        cwd=str(cwd),
                        has_ui=terminal_ui is not None,
                        messages=messages,
                        complete_fn=_extension_complete,
                        notify_sink=_extension_notify,
                        ui_custom_driver=_extension_custom_driver,
                        ui_driver=extension_ui_driver,
                        set_active_tools_fn=extension_set_active_tools,
                        set_model_fn=extension_set_model,
                        set_thinking_level_fn=extension_set_thinking_level,
                        append_entry_fn=extension_append_entry,
                        flags=extension_flag_values,
                        session_tree=session_tree,
                    )
                    if (
                        shortcut_dispatch is not None
                        and not shortcut_dispatch.ran
                        and shortcut_dispatch.error
                    ):
                        self._emit_diagnostic(
                            terminal_ui,
                            error_stream,
                            (
                                f"pipy: extension shortcut {shortcut_key!r} "
                                f"failed ({shortcut_dispatch.error})"
                            ),
                        )
                    continue
                from_hotkey = command_text in {
                    HOTKEY_MODEL_CYCLE_NEXT,
                    HOTKEY_MODEL_CYCLE_PREV,
                }
                if from_hotkey:
                    stripped = (
                        "/scoped-models next"
                        if stripped == HOTKEY_MODEL_CYCLE_NEXT
                        else "/scoped-models prev"
                    )
                    user_input = stripped
                    # Keep the dispatch key in sync with the translated command so
                    # the /scoped-models handler below matches (a hotkey is never a
                    # drained line, so this only rewrites typed-hotkey input).
                    command_text = stripped
                # Local shell shortcut: a submitted line whose first non-space
                # character is ``!`` runs a bash command from the editor with no
                # provider turn (Pi's ``handleBashCommand``). ``!cmd`` records the
                # command/output into the conversation context and native session
                # tree so the next turn and resume see it; ``!!cmd`` runs identically
                # but is excluded from context (a live-only diagnostic). Escape
                # cancels a running command. Intercepted before the user-message
                # panel so it renders as a shell block, not a chat bubble.
                if command_text.startswith("!"):
                    shell_context_text = self._run_local_shell_shortcut(
                        stripped,
                        terminal_ui=terminal_ui,
                        error_stream=error_stream,
                        cwd=cwd,
                        user_bash_hooks=extension_user_bash_hooks,
                        set_active_tools_fn=extension_set_active_tools,
                        set_model_fn=extension_set_model,
                        set_thinking_level_fn=extension_set_thinking_level,
                        flags=extension_flag_values,
                    )
                    if shell_context_text is not None:
                        shell_message = UserMessage(content=shell_context_text)
                        messages.append(shell_message)
                        session_tree.append_message(shell_message)
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                            usage_accumulator=usage_accumulator,
                        )
                    continue
                # Pi paints the submitted user message back on a muted
                # `userMessageBg` panel — distinct from the green tool
                # panel — so the prompt reads as a chat bubble. Overwrite
                # the readline echo line with the styled panel row when
                # the renderer can drive ANSI cursor controls.
                if stripped and not from_hotkey:
                    renderer.render_user_message(user_input)
                if not stripped:
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                        )
                    continue
                if command_text in {"/exit", "/quit"}:
                    break
                if command_text == "/hotkeys":
                    # ``/hotkeys`` renders the grouped keyboard-shortcut table
                    # from the resolved keybinding manager (reflecting any user
                    # keybindings.json overrides). Runs no provider turn.
                    hotkeys_text = render_hotkeys(keybindings)
                    if terminal_ui is not None:
                        terminal_ui.add_notice(hotkeys_text)
                    else:
                        print(hotkeys_text, file=error_stream)
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                        )
                    continue
                if command_text == "/reload":
                    # Local-only: re-read settings (both scopes), keybindings, and
                    # workspace resources, then re-apply derived UI settings. Runs
                    # between turns at the prompt, so no provider turn or compaction
                    # is in flight. A settings/theme load error keeps the prior good
                    # state for that scope; a malformed keybindings.json falls back
                    # to the built-in defaults. No provider turn, no tool call.
                    settings.reload()
                    keybindings.reload()
                    # Re-resolve package roots + re-install the theme
                    # registry so a package added/removed since startup is
                    # reflected after /reload.
                    package_roots = compose_package_runtime(
                        settings,
                        cwd,
                        include_package_themes=not resource_options.no_themes,
                        explicit_theme_paths=resource_options.theme_paths,
                    )
                    workspace_resources = WorkspaceResources.discover(
                        cwd,
                        package_roots=package_roots,
                        explicit_skill_paths=resource_options.skill_paths,
                        explicit_prompt_template_paths=resource_options.prompt_template_paths,
                        include_skills_defaults=not resource_options.no_skills,
                        include_prompt_template_defaults=not resource_options.no_prompt_templates,
                    ).with_enablement(
                        skills_patterns=settings.get_skills_patterns(),
                        prompts_patterns=settings.get_prompts_patterns(),
                        enable_skill_commands=settings.get_enable_skill_commands(),
                    )
                    # Re-discover + re-activate extensions on reload (Pi
                    # /reload also reloads extensions). A failing extension is
                    # disabled without affecting the session. Clear any chrome
                    # set by the prior generation first so a removed/disabled
                    # extension cannot leave stale widgets/header/footer/title.
                    if terminal_ui is not None:
                        terminal_ui.clear_extension_chrome()
                    _ext_runtime = _activate_workspace_extensions(
                        cwd,
                        workspace_resources,
                        tuple(self.tool_registry.keys()),
                        package_roots=()
                        if resource_options.no_extensions
                        else package_roots.extensions,
                        extension_patterns=settings.get_extensions_patterns(),
                        explicit_extension_paths=resource_options.extension_paths,
                        include_default_extensions=not resource_options.no_extensions,
                    )
                    extension_commands = _ext_runtime.commands
                    extension_menu_names = _ext_runtime.menu_names
                    extension_descriptions = _ext_runtime.descriptions
                    extension_tool_call_hooks_ = _ext_runtime.tool_call_hooks
                    extension_lifecycle_hooks = _ext_runtime.lifecycle_hooks
                    extension_input_hooks = _ext_runtime.input_hooks
                    extension_before_agent_start_hooks = (
                        _ext_runtime.before_agent_start_hooks
                    )
                    extension_tool_result_hooks = _ext_runtime.tool_result_hooks
                    extension_user_bash_hooks = _ext_runtime.user_bash_hooks
                    extension_before_provider_request_hooks = (
                        _ext_runtime.before_provider_request_hooks
                    )
                    extension_session_before_switch_hooks = (
                        _ext_runtime.session_before_switch_hooks
                    )
                    extension_session_before_fork_hooks = (
                        _ext_runtime.session_before_fork_hooks
                    )
                    extension_session_before_compact_hooks = (
                        _ext_runtime.session_before_compact_hooks
                    )
                    extension_session_before_tree_hooks = (
                        _ext_runtime.session_before_tree_hooks
                    )
                    extension_message_outbox = _ext_runtime.outbox
                    extension_renderer_map = _ext_runtime.message_renderers
                    reloaded_flag_values, reloaded_flag_error = (
                        parse_extension_flag_tokens(
                            _ext_runtime.flags,
                            tuple(resource_options.extension_flag_tokens),
                        )
                    )
                    if reloaded_flag_error is not None:
                        self._emit_diagnostic(
                            terminal_ui,
                            error_stream,
                            f"pipy: {reloaded_flag_error}",
                        )
                    else:
                        extension_flag_values = reloaded_flag_values
                        emitter.set_flags(extension_flag_values)
                    state = self.provider_state
                    if isinstance(state, NativeReplProviderState):
                        catalog_state = state.catalog_state
                        if catalog_state is not None:
                            was_extension_selection = (
                                state.current_selection_uses_extension_provider()
                            )
                            catalog_state.refresh()  # type: ignore[attr-defined]
                            catalog_state.set_extension_provider_contributions(  # type: ignore[attr-defined]
                                _ext_runtime.providers,
                                _ext_runtime.unregistered_providers,
                            )
                            selection_disappeared = (
                                not state.current_selection_supported()
                                or (
                                    was_extension_selection
                                    and not state.current_selection_uses_extension_provider()
                                )
                            )
                            if not selection_disappeared:
                                if state.current_selection_uses_extension_provider():
                                    refreshed_provider = state.current_provider()
                                    if getattr(
                                        refreshed_provider, "supports_tool_calls", False
                                    ):
                                        self.provider = refreshed_provider
                                    else:
                                        fallback = state.reset_to_first_available_model(
                                            require_tool_calls=True
                                        )
                                        if fallback is not None:
                                            self.provider = state.current_provider()
                                            effective_provider_name = (
                                                fallback.provider_name
                                            )
                                            effective_model_id = fallback.model_id
                                            messages = []
                                            usage_accumulator = _UsageAccumulator()
                                            usage_accumulator.bind(
                                                effective_provider_name,
                                                effective_model_id,
                                            )
                                            self._emit_diagnostic(
                                                terminal_ui,
                                                error_stream,
                                                "pipy: active model no longer "
                                                "supports tool calls after reload; "
                                                f"selected {fallback.reference}.",
                                            )
                                        else:
                                            message = (
                                                "active model no longer supports "
                                                "tool calls after reload and no "
                                                "available tool-capable fallback "
                                                "was found"
                                            )
                                            _bind_unavailable_after_reload(message)
                                            self._emit_diagnostic(
                                                terminal_ui,
                                                error_stream,
                                                f"pipy: {message}.",
                                            )
                            else:
                                fallback = state.reset_to_first_available_model(
                                    require_tool_calls=True
                                )
                                if fallback is not None:
                                    self.provider = state.current_provider()
                                    effective_provider_name = fallback.provider_name
                                    effective_model_id = fallback.model_id
                                    messages = []
                                    usage_accumulator = _UsageAccumulator()
                                    usage_accumulator.bind(
                                        effective_provider_name,
                                        effective_model_id,
                                    )
                                    self._emit_diagnostic(
                                        terminal_ui,
                                        error_stream,
                                        "pipy: active model disappeared on "
                                        "reload; selected "
                                        f"{fallback.reference}.",
                                    )
                                else:
                                    message = (
                                        "active model disappeared on reload and "
                                        "no available tool-capable fallback was "
                                        "found"
                                    )
                                    _bind_unavailable_after_reload(message)
                                    self._emit_diagnostic(
                                        terminal_ui,
                                        error_stream,
                                        f"pipy: {message}.",
                                    )
                    # Rebuild this run's tool registry with the reloaded
                    # extension tools.
                    run_tool_registry = dict(self.tool_registry)
                    for _registered_tool in _ext_runtime.tools:
                        _port = _ExtensionToolPort(
                            _registered_tool,
                            has_ui=terminal_ui is not None,
                            notify_sink=_extension_notify,
                            flags=extension_flag_values,
                            render_details_sink=extension_render_details,
                        )
                        run_tool_registry[_port.definition.name] = _port
                    if active_tool_names is not None:
                        active_tool_names = {
                            name
                            for name in active_tool_names
                            if name in run_tool_registry
                        }
                    # Refresh the emitter's lifecycle hooks so reloaded
                    # extensions observe subsequent agent/turn events.
                    emitter.set_lifecycle_hooks(extension_lifecycle_hooks)
                    # Re-apply the edited theme (settings is source of truth over the
                    # persisted store) and the derived UI settings.
                    reloaded_theme = settings.get_theme()
                    if reloaded_theme:
                        os.environ["PIPY_THEME"] = reloaded_theme
                    if terminal_ui is not None:
                        terminal_ui.autocomplete_max_visible = (
                            settings.get_autocomplete_max_visible()
                        )
                        terminal_ui.command_names = _tool_loop_command_names(
                            workspace_resources, extension_menu_names
                        )
                        terminal_ui.command_descriptions = _tool_loop_command_descriptions(
                            workspace_resources, extension_descriptions
                        )
                        terminal_ui.extension_shortcut_keys = frozenset(
                            _ext_runtime.shortcuts
                        )
                    load_errors = settings.load_errors()
                    if load_errors:
                        for scope, detail in load_errors.items():
                            self._emit_diagnostic(
                                terminal_ui,
                                error_stream,
                                f"pipy: kept prior {scope} settings ({detail}).",
                            )
                    if not settings.get_quiet_startup():
                        print_startup_chrome(error_stream, cwd=cwd)
                    self._emit_diagnostic(
                        terminal_ui,
                        error_stream,
                        "pipy: reloaded settings, keybindings, and resources.",
                    )
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                        )
                    continue
                if command_text == "/changelog":
                    # Local-only: render the full changelog (oldest-first) under a
                    # "What's New" header. Runs no provider turn.
                    changelog_text = render_changelog(read_changelog_entries())
                    if terminal_ui is not None:
                        terminal_ui.add_notice(changelog_text)
                    else:
                        print(changelog_text, file=error_stream)
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                        )
                    continue
                if command_text == "/export" or command_text.startswith("/export "):
                    argument = stripped[len("/export") :]
                    path_arg = parse_command_path_argument(argument)
                    try:
                        if path_arg and Path(path_arg).suffix.lower() == ".jsonl":
                            output_path = Path(path_arg).expanduser()
                            if not output_path.is_absolute():
                                output_path = cwd / output_path
                            exported = export_native_branch_to_jsonl(session_tree, output_path)
                            diag(f"pipy: exported native session JSONL to {exported}.")
                        else:
                            output_path = (
                                Path(path_arg).expanduser()
                                if path_arg
                                else default_html_export_path(session_tree, cwd=cwd)
                            )
                            if not output_path.is_absolute():
                                output_path = cwd / output_path
                            exported = export_native_session_to_html(
                                session_tree, output_path, system_prompt=system_prompt
                            )
                            diag(f"pipy: exported native session HTML to {exported}.")
                    except NativeExportError as exc:
                        diag(f"pipy: {exc}")
                    refresh_legacy_footer()
                    continue
                if command_text == "/import" or command_text.startswith("/import "):
                    argument = stripped[len("/import") :]
                    path_arg = parse_command_path_argument(argument)
                    if not path_arg:
                        diag("pipy: Usage: /import <path.jsonl>")
                        refresh_legacy_footer()
                        continue
                    confirm = "--yes" in argument.split()
                    source_path = Path(path_arg).expanduser()
                    if not source_path.is_absolute():
                        source_path = cwd / source_path
                    if not confirm:
                        print(
                            f"Replace current session with {source_path}? [y/N] ",
                            end="",
                            file=error_stream,
                            flush=True,
                        )
                        try:
                            confirm = input_stream.readline().strip().lower() in ("y", "yes")
                        except (OSError, ValueError):
                            confirm = False
                    if not confirm:
                        diag("pipy: /import cancelled.")
                        refresh_legacy_footer()
                        continue
                    if not extension_session_allows(
                        extension_session_before_switch_hooks,
                        operation="switch",
                        target=str(source_path),
                    ):
                        refresh_legacy_footer()
                        continue
                    try:
                        session_tree = import_native_session_jsonl(
                            source_path, session_dir=current_session_dir()
                        )
                    except NativeExportError as exc:
                        if "imported session cwd does not exist:" not in str(exc):
                            diag(f"pipy: {exc}")
                            refresh_legacy_footer()
                            continue
                        print(
                            f"{exc} Use current workspace {cwd}? [y/N] ",
                            end="",
                            file=error_stream,
                            flush=True,
                        )
                        try:
                            use_current = input_stream.readline().strip().lower() in ("y", "yes")
                        except (OSError, ValueError):
                            use_current = False
                        if not use_current:
                            diag("pipy: /import cancelled.")
                            refresh_legacy_footer()
                            continue
                        try:
                            session_tree = import_native_session_jsonl(
                                source_path,
                                session_dir=current_session_dir(),
                                missing_cwd=cwd,
                            )
                        except NativeExportError as second_exc:
                            diag(f"pipy: {second_exc}")
                            refresh_legacy_footer()
                            continue
                    rebuild_messages_from_tree()
                    diag(
                        "pipy: imported native session "
                        f"{sanitize_label_text(session_tree.session_id[:8])}."
                    )
                    refresh_legacy_footer()
                    continue
                if command_text == "/share":
                    token = resolve_github_token()
                    if not token:
                        diag("pipy: No GitHub token found. Set GITHUB_TOKEN or run `gh auth login`.")
                        refresh_legacy_footer()
                        continue
                    try:
                        result = self._share_native_session_command(
                            session_tree=session_tree,
                            token=token,
                            terminal_ui=terminal_ui,
                            error_stream=error_stream,
                        )
                    except NativeExportError as exc:
                        diag(f"pipy: {exc}")
                        refresh_legacy_footer()
                        continue
                    if result is None:
                        refresh_legacy_footer()
                        continue
                    if result.viewer_url:
                        diag(f"pipy: share URL: {result.viewer_url}\npipy: gist URL: {result.gist_url}")
                    else:
                        diag(f"pipy: gist URL: {result.gist_url}")
                    refresh_legacy_footer()
                    continue
                if command_text == "/settings":
                    if terminal_ui is not None:
                        self._drive_settings_dialog(
                            terminal_ui,
                            prompt_history_store,
                            apply_model_selection=apply_model_selection,
                            apply_auth_change=apply_auth_change,
                            settings=settings,
                            session_tree=session_tree,
                            error_stream=error_stream,
                        )
                    else:
                        for overlay_line in self._settings_overlay_lines(settings):
                            print(overlay_line, file=error_stream)
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                        )
                    continue
                if command_text == "/copy":
                    # Local-only command: copies the most recent assistant answer
                    # through a safe OS/terminal clipboard path. It never invokes
                    # the provider, tools, login/logout, or model switching.
                    self._emit_diagnostic(
                        terminal_ui,
                        error_stream,
                        self._copy_last_answer(messages, error_stream=error_stream),
                    )
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                        )
                    continue
                if command_text == "/compact":
                    # Local-only command: reduce the provider-visible history while
                    # keeping recent turns plus a safe metadata-only summary. No
                    # provider turn, tool call, or auth change.
                    self._emit_diagnostic(
                        terminal_ui, error_stream, apply_compaction("manual")
                    )
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                        )
                    continue
                if command_text == "/session":
                    # Local-only: report safe current native-session status. No
                    # provider turn, tool call, or transcript content.
                    diag(format_session_status(session_tree))
                    refresh_legacy_footer()
                    continue
                if command_text == "/name" or command_text.startswith("/name "):
                    argument = stripped[len("/name") :].strip()
                    if not argument:
                        diag(
                            "pipy: current session name: "
                            + (
                                sanitize_label_text(session_tree.name)
                                if session_tree.name
                                else "(unnamed)"
                            )
                        )
                    else:
                        session_tree.append_session_info(argument)
                        diag(f"pipy: session named {argument!r}.")
                    refresh_legacy_footer()
                    continue
                if command_text == "/new":
                    # Start a fresh native product session in the same store.
                    if not extension_session_allows(
                        extension_session_before_switch_hooks,
                        operation="switch",
                        target="new",
                    ):
                        refresh_legacy_footer()
                        continue
                    session_dir = (
                        session_tree.path.parent
                        if session_tree.path is not None
                        else None
                    )
                    session_tree = NativeSessionTree.create(
                        cwd,
                        session_dir=session_dir,
                        persist=session_tree.persist,
                    )
                    rebuild_messages_from_tree()
                    diag(
                        "pipy: started a new native session "
                        f"({sanitize_label_text(session_tree.session_id[:8])})."
                    )
                    refresh_legacy_footer()
                    continue
                if command_text == "/tree" or command_text.startswith("/tree "):
                    argument = stripped[len("/tree") :].strip()
                    tree_sub = argument.split(maxsplit=1)[0].lower() if argument else ""
                    tree_may_change = (
                        (not argument and terminal_ui is not None)
                        or tree_sub in {"select", "label", "filter"}
                    )
                    if tree_may_change:
                        if not extension_session_allows(
                            extension_session_before_tree_hooks,
                            operation="tree",
                            target=argument or None,
                        ):
                            refresh_legacy_footer()
                            continue
                    outcome = self._handle_tree_command(
                        argument,
                        session_tree=session_tree,
                        terminal_ui=terminal_ui,
                        error_stream=error_stream,
                        repl_input=repl_input,
                        filter_mode=tree_filter_mode,
                        rebuild_messages=rebuild_messages_from_tree,
                        summarizer=summarize_branch,
                    )
                    if outcome.filter_mode is not None:
                        tree_filter_mode = outcome.filter_mode
                    if outcome.prefill is not None:
                        pending_prefill = outcome.prefill
                    refresh_legacy_footer()
                    continue
                if command_text == "/resume" or command_text.startswith("/resume "):
                    argument = stripped[len("/resume") :].strip()
                    resume_tokens = argument.split()
                    resume_sub = resume_tokens[0].lower() if resume_tokens else ""

                    def _list_sessions(named_only: bool = False) -> None:
                        sessions = list_native_sessions(current_session_dir())
                        if named_only:
                            sessions = [s for s in sessions if s.name]
                        if not sessions:
                            diag("pipy: no native sessions found for this workspace.")
                            return
                        scope = "named " if named_only else ""
                        diag(f"pipy: {scope}native sessions (newest first):")
                        for index, entry in enumerate(sessions, start=1):
                            label = (
                                sanitize_label_text(entry.name)
                                if entry.name
                                else "(unnamed)"
                            )
                            diag(
                                f"  {index}. "
                                f"{sanitize_label_text(entry.session_id[:8])} "
                                f"{label} "
                                f"messages={entry.message_count} "
                                f"file={sanitize_label_text(entry.path.name)}"
                            )
                        diag("pipy: use '/resume <number|id>' to open a session.")

                    if not argument and terminal_ui is not None and hasattr(
                        terminal_ui, "run_session_picker"
                    ):
                        picked_session = self._run_interactive_session_picker(
                            session_tree=session_tree,
                            terminal_ui=terminal_ui,
                        )
                        if picked_session is None:
                            diag("pipy: /resume cancelled.")
                        elif (
                            session_tree.path is not None
                            and picked_session == session_tree.path
                        ):
                            diag("pipy: already on the selected native session.")
                        else:
                            if not extension_session_allows(
                                extension_session_before_switch_hooks,
                                operation="switch",
                                target=str(picked_session),
                            ):
                                refresh_legacy_footer()
                                continue
                            session_tree = NativeSessionTree.open(picked_session)
                            rebuild_messages_from_tree()
                            diag(
                                "pipy: resumed native session "
                                f"{sanitize_label_text(session_tree.session_id[:8])} "
                                f"({sanitize_label_text(session_tree.name) if session_tree.name else 'unnamed'})."
                            )
                    elif not argument:
                        _list_sessions()
                    elif resume_sub == "named":
                        _list_sessions(named_only=True)
                    elif resume_sub == "rename":
                        if len(resume_tokens) < 3:
                            diag("pipy: usage: /resume rename <number|id> <name>")
                        else:
                            target = resolve_session_file(resume_tokens[1])
                            if target is None:
                                diag(f"pipy: no native session matched {resume_tokens[1]!r}.")
                            else:
                                renamed = NativeSessionTree.open(target)
                                new_name = " ".join(resume_tokens[2:])
                                renamed.append_session_info(new_name)
                                diag(
                                    f"pipy: renamed session {sanitize_label_text(renamed.session_id[:8])} "
                                    f"to {new_name!r}."
                                )
                    elif resume_sub == "delete":
                        confirm = "--yes" in resume_tokens[1:]
                        refs = [t for t in resume_tokens[1:] if t != "--yes"]
                        if not refs:
                            diag("pipy: usage: /resume delete <number|id> --yes")
                        else:
                            target = resolve_session_file(refs[0])
                            if target is None:
                                diag(f"pipy: no native session matched {refs[0]!r}.")
                            elif session_tree.path is not None and target == session_tree.path:
                                diag("pipy: cannot delete the active native session.")
                            elif not confirm:
                                diag(
                                    "pipy: deletion needs confirmation; re-run "
                                    f"'/resume delete {refs[0]} --yes'. This removes "
                                    "only the native session file, never pipy-session "
                                    "archive records."
                                )
                            else:
                                ok, detail = delete_native_session(target)
                                diag(f"pipy: {detail}" if ok else f"pipy: {detail}")
                    else:
                        target = resolve_session_file(argument)
                        if target is None:
                            diag(f"pipy: no native session matched {argument!r}.")
                        else:
                            if not extension_session_allows(
                                extension_session_before_switch_hooks,
                                operation="switch",
                                target=str(target),
                            ):
                                refresh_legacy_footer()
                                continue
                            session_tree = NativeSessionTree.open(target)
                            rebuild_messages_from_tree()
                            diag(
                                "pipy: resumed native session "
                                f"{sanitize_label_text(session_tree.session_id[:8])} "
                                f"({sanitize_label_text(session_tree.name) if session_tree.name else 'unnamed'})."
                            )
                    refresh_legacy_footer()
                    continue
                if command_text == "/fork" or command_text.startswith("/fork "):
                    argument = stripped[len("/fork") :].strip()
                    if session_tree.path is None:
                        diag("pipy: /fork requires a persistent native session.")
                        refresh_legacy_footer()
                        continue
                    if argument:
                        target_entry = resolve_entry_ref(
                            session_tree, argument, filter_mode=tree_filter_mode
                        )
                        if target_entry is None:
                            diag(f"pipy: no tree entry matched {argument!r}.")
                            refresh_legacy_footer()
                            continue
                        fork_leaf: str | None = target_entry.id
                    else:
                        fork_leaf = session_tree.get_leaf_id()
                    if not extension_session_allows(
                        extension_session_before_fork_hooks,
                        operation="fork",
                        target=fork_leaf,
                    ):
                        refresh_legacy_footer()
                        continue
                    session_tree = NativeSessionTree.fork_from(
                        session_tree.path,
                        cwd,
                        leaf_id=fork_leaf,
                        session_dir=session_tree.path.parent,
                    )
                    rebuild_messages_from_tree()
                    diag(
                        "pipy: forked into new native session "
                        f"{sanitize_label_text(session_tree.session_id[:8])}."
                    )
                    refresh_legacy_footer()
                    continue
                if command_text == "/clone":
                    if session_tree.path is None:
                        diag("pipy: /clone requires a persistent native session.")
                        refresh_legacy_footer()
                        continue
                    if not extension_session_allows(
                        extension_session_before_fork_hooks,
                        operation="fork",
                        target=session_tree.get_leaf_id(),
                    ):
                        refresh_legacy_footer()
                        continue
                    session_tree = NativeSessionTree.fork_from(
                        session_tree.path,
                        cwd,
                        leaf_id=session_tree.get_leaf_id(),
                        session_dir=session_tree.path.parent,
                    )
                    rebuild_messages_from_tree()
                    diag(
                        "pipy: cloned active branch into new native session "
                        f"{sanitize_label_text(session_tree.session_id[:8])}."
                    )
                    refresh_legacy_footer()
                    continue
                if command_text == "/model" or command_text.startswith("/model "):
                    argument = stripped[len("/model") :].strip()
                    state = self.provider_state
                    if not isinstance(state, NativeReplProviderState):
                        self._emit_diagnostic(
                            terminal_ui,
                            error_stream,
                            "pipy: /model is unavailable for this REPL provider state.",
                        )
                    elif argument:
                        _ok, message = apply_model_selection(argument)
                        self._emit_diagnostic(terminal_ui, error_stream, message)
                    elif terminal_ui is not None:
                        ui_options, selections = self._model_selector_rows(state)
                        current = state.current_selection()
                        current_index = next(
                            (
                                index
                                for index, selection in enumerate(selections)
                                if selection.provider_name == current.provider_name
                                and selection.model_id == current.model_id
                            ),
                            0,
                        )
                        chosen = terminal_ui.run_model_selector(
                            ui_options, current_index=current_index
                        )
                        if chosen is not None:
                            _ok, message = apply_model_selection(
                                selections[chosen].reference
                            )
                            terminal_ui.add_notice(message)
                    else:
                        for overlay_line in self._settings_overlay_lines(settings):
                            print(overlay_line, file=error_stream)
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                            usage_accumulator=usage_accumulator,
                        )
                    continue
                if command_text == "/scoped-models" or command_text.startswith("/scoped-models "):
                    # Local-only: view/set/clear the enabledModels patterns that
                    # constrain the model cycle, or cycle (next/prev) over the scoped
                    # set (or full available catalog when empty). Cycling rebinds the
                    # live provider through the same select_model boundary as /model;
                    # no command here runs a provider turn or a tool call.
                    argument = stripped[len("/scoped-models"):].strip()
                    state = self.provider_state
                    available_refs = (
                        [o.selection.reference for o in state.model_options() if o.available]
                        if isinstance(state, NativeReplProviderState)
                        else []
                    )
                    patterns = settings.get_enabled_models()
                    scoped = filter_scoped_references(available_refs, patterns)
                    if (
                        not argument
                        and terminal_ui is not None
                        and isinstance(state, NativeReplProviderState)
                        and available_refs
                    ):
                        # Interactive multi-select overlay defining the Ctrl+P cycle
                        # scope (saved back as the enabledModels patterns).
                        self._open_scoped_models_overlay(
                            terminal_ui, state=state, settings=settings
                        )
                    elif not argument:
                        pattern_text = ", ".join(patterns) if patterns else "(none — full catalog)"
                        cycle_text = ", ".join(scoped) if scoped else "(none available)"
                        for line in (
                            "pipy: scoped models:",
                            f"  patterns: {pattern_text}",
                            f"  cycle set: {cycle_text}",
                        ):
                            self._emit_diagnostic(terminal_ui, error_stream, line)
                    elif argument == "clear":
                        try:
                            settings.set_enabled_models([])
                            msg = "pipy: scoped models cleared (cycle uses the full catalog)."
                        except RuntimeError as exc:
                            msg = f"pipy: could not update scoped models: {exc}"
                        self._emit_diagnostic(terminal_ui, error_stream, msg)
                    elif argument in {"next", "prev"}:
                        current_ref = (
                            state.current_selection().reference
                            if isinstance(state, NativeReplProviderState)
                            else ""
                        )
                        cycle_target = next_reference(
                            scoped, current_ref, forward=argument == "next"
                        )
                        if cycle_target is None:
                            self._emit_diagnostic(
                                terminal_ui, error_stream, "pipy: no models available to cycle."
                            )
                        else:
                            _ok, message = apply_model_selection(cycle_target)
                            self._emit_diagnostic(terminal_ui, error_stream, message)
                    else:
                        new_patterns = argument.split()
                        try:
                            settings.set_enabled_models(new_patterns)
                            msg = "pipy: scoped models set: " + ", ".join(new_patterns)
                        except RuntimeError as exc:
                            msg = f"pipy: could not update scoped models: {exc}"
                        self._emit_diagnostic(terminal_ui, error_stream, msg)
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                            usage_accumulator=usage_accumulator,
                        )
                    continue
                if command_text == "/login" or command_text.startswith("/login "):
                    # Auth-only command: no provider turn, no tool call. Runs the
                    # OAuth login through the provider-state boundary, refreshes
                    # model-option availability, and clears conversation context.
                    argument = stripped[len("/login") :].strip()
                    message = apply_auth_change("login", argument)
                    self._emit_diagnostic(terminal_ui, error_stream, message)
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                            usage_accumulator=usage_accumulator,
                        )
                    continue
                if command_text == "/logout" or command_text.startswith("/logout "):
                    # Auth-only command: no provider turn, no tool call. Removes the
                    # stored OAuth credentials, resets the selection to the local
                    # default, refreshes availability, and clears context.
                    argument = stripped[len("/logout") :].strip()
                    message = apply_auth_change("logout", argument)
                    self._emit_diagnostic(terminal_ui, error_stream, message)
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                            usage_accumulator=usage_accumulator,
                        )
                    continue
                # Resource dispatch (skills, prompt templates, custom commands)
                # runs through the same local-command boundary as the built-ins,
                # after them so a custom command can never shadow a built-in.
                resource_dispatch = dispatch_resource_command(
                    command_text, workspace_resources
                )
                resource_provider_text: str | None = None
                if resource_dispatch is not None and resource_dispatch.kind == DISPATCH_LIST:
                    self._emit_diagnostic(
                        terminal_ui, error_stream, resource_dispatch.message
                    )
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                        )
                    continue
                if resource_dispatch is not None and resource_dispatch.is_reject:
                    # Fail closed: diagnostic only, no provider turn, no archive
                    # write, no prompt-history or sidecar entry.
                    self._emit_diagnostic(
                        terminal_ui, error_stream, resource_dispatch.message
                    )
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                        )
                    continue
                if resource_dispatch is not None and resource_dispatch.is_run:
                    # The expanded/instruction text becomes the bounded
                    # provider-visible message; it never reaches prompt history,
                    # the sidecar body, or the metadata archive. Only the
                    # invocation counter is surfaced.
                    resource_invocation_count += 1
                    resource_provider_text = resource_dispatch.provider_text or ""
                    self._emit_diagnostic(
                        terminal_ui, error_stream, resource_dispatch.message
                    )
                # Extension commands dispatch AFTER built-ins and resource
                # commands (so they can never shadow them) and BEFORE the
                # not-handled fallback. The handler runs locally with no
                # provider turn; its notifications are live UI output only.
                if resource_provider_text is None:
                    extension_dispatch = dispatch_extension_command(
                        command_text,
                        extension_commands,
                        cwd=str(cwd),
                        has_ui=terminal_ui is not None,
                        messages=messages,
                        complete_fn=_extension_complete,
                        notify_sink=_extension_notify,
                        ui_custom_driver=_extension_custom_driver,
                        ui_driver=extension_ui_driver,
                        set_active_tools_fn=extension_set_active_tools,
                        set_model_fn=extension_set_model,
                        set_thinking_level_fn=extension_set_thinking_level,
                        append_entry_fn=extension_append_entry,
                        flags=extension_flag_values,
                        session_tree=session_tree,
                    )
                    if extension_dispatch is not None:
                        # Notifications already surfaced live via the sink while
                        # the handler ran; only the failure diagnostic remains.
                        if not extension_dispatch.ran and extension_dispatch.error:
                            self._emit_diagnostic(
                                terminal_ui,
                                error_stream,
                                (
                                    f"pipy: extension command /{extension_dispatch.name} "
                                    f"failed ({extension_dispatch.error})"
                                ),
                            )
                        # Messages a command enqueued via send_user_message
                        # are drained at the top of the next iteration.
                        if legacy_footer_enabled():
                            self._print_footer(
                                error_stream,
                                cwd=cwd,
                                provider_name=effective_provider_name,
                                model_id=effective_model_id,
                                user_turn_count=user_turn_count,
                                tool_invocation_count=tool_invocation_count,
                            )
                        continue
                if command_text.startswith("/") and resource_provider_text is None:
                    self._emit_diagnostic(
                        terminal_ui,
                        error_stream,
                        (
                            f"pipy: {stripped!r} is not handled in tool-loop mode; "
                            "supported local commands are /help, /hotkeys, /reload, "
                            "/changelog, /model, /scoped-models, /settings, "
                            "/login, /logout, /copy, /compact, /export, /import, "
                            "/share, /session, /name, "
                            "/new, /tree, /resume, /fork, /clone, /skill, "
                            "/exit, /quit "
                            "(plus any workspace prompt templates and custom "
                            "commands as /<name>, and activated extension "
                            "commands). Other prompts are sent to the model."
                        ),
                    )
                    if legacy_footer_enabled():
                        self._print_footer(
                            error_stream,
                            cwd=cwd,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                        )
                    continue
                # User-directed file context: a genuine prompt may name workspace
                # files with ``@path``. Resolve them through the shared bounded
                # reader (reusing this loop's ``read`` policy and reference roots),
                # append the bounded excerpts to the provider-visible user message,
                # and keep the literal prompt for the rendered panel, prompt
                # history, and the sidecar transcript.
                turn_attachments: tuple[ProviderImageAttachment, ...] = ()
                if resource_provider_text is not None:
                    # Resource turn: the bounded instruction/expansion is the
                    # provider message verbatim. @file augmentation, prompt
                    # history, and the sidecar body are all skipped so the
                    # literal resource text never leaks past the provider.
                    provider_user_input = resource_provider_text
                else:
                    # `input` hooks may transform the submitted prompt before
                    # the provider turn. The original `user_input` still goes
                    # to prompt history, the sidecar, and the rendered panel;
                    # only the provider-visible text and @file resolution use
                    # the transformed value.
                    transformed_input = dispatch_input_hooks(
                        extension_input_hooks,
                        user_input,
                        cwd=str(cwd),
                        has_ui=terminal_ui is not None,
                        notify_sink=_extension_notify,
                        set_active_tools_fn=extension_set_active_tools,
                        set_model_fn=extension_set_model,
                        set_thinking_level_fn=extension_set_thinking_level,
                    )
                    provider_user_input = transformed_input
                    file_references = resolve_file_references(
                        transformed_input,
                        workspace_root=cwd,
                        reference_roots=self.reference_roots,
                    )
                    if file_references.reference_count:
                        file_reference_count += file_references.reference_count
                        file_reference_loaded_count += file_references.loaded_count
                        file_reference_failed_count += file_references.failed_count
                        for diagnostic in file_references.diagnostics():
                            self._emit_diagnostic(terminal_ui, error_stream, diagnostic)
                        if file_references.used:
                            provider_user_input = file_references.augmented_prompt(
                                transformed_input
                            )
                    # User-directed image attachments (@image:<path>): bounded,
                    # fail-closed image loading that becomes provider-visible image
                    # blocks on the current user message. Raw bytes never reach the
                    # prompt history, the transcript sidecar, or the result.
                    image_attachments = resolve_image_attachments(
                        transformed_input,
                        workspace_root=cwd,
                        reference_roots=image_reference_roots,
                    )
                    if image_attachments.reference_count:
                        image_attachment_count += image_attachments.reference_count
                        image_attachment_loaded_count += image_attachments.loaded_count
                        image_attachment_failed_count += image_attachments.failed_count
                        for diagnostic in image_attachments.diagnostics():
                            self._emit_diagnostic(terminal_ui, error_stream, diagnostic)
                        turn_attachments = image_attachments.attachments()
                turn_user_message = UserMessage(content=provider_user_input)
                # `before_agent_start` hooks may inject bounded context into
                # this agent run's system prompt. Computed once per accepted
                # prompt; the injected text is provider-visible but not added
                # to the metadata archive.
                before_agent_result = dispatch_before_agent_start_hooks(
                    extension_before_agent_start_hooks,
                    cwd=str(cwd),
                    has_ui=terminal_ui is not None,
                    system_prompt=base_system_prompt,
                    notify_sink=_extension_notify,
                    set_active_tools_fn=extension_set_active_tools,
                    set_model_fn=extension_set_model,
                    set_thinking_level_fn=extension_set_thinking_level,
                    flags=extension_flag_values,
                )
                agent_system_prompt = base_system_prompt
                if before_agent_result.append_system_prompt:
                    agent_system_prompt = (
                        base_system_prompt
                        + "\n"
                        + before_agent_result.append_system_prompt
                    )
                # Automation: this accepted prompt begins one agent run. Snapshot the
                # message index so agent_end can report the messages this run added
                # (the user message and everything the loop appends below).
                agent_run_start_index = len(messages)
                emitter.agent_start()
                messages.append(turn_user_message)
                session_tree.append_message(turn_user_message)
                user_turn_count += 1
                # Persist the prompt for cross-session recall when the user has
                # enabled it from /settings. record() is a no-op when disabled and
                # writes only to the local prompt-history file — never the
                # metadata-first session archive. Slash commands and resource
                # invocations never reach here, so only genuine prompts are
                # persisted. The literal prompt (not the @file-augmented variant)
                # is recorded so history stays user text.
                if resource_provider_text is None:
                    prompt_history_store.record(user_input)

                invocations_this_turn = 0
                inner_iteration_cap = self.tool_budget + 2
                inner_iterations = 0

                while inner_iterations < inner_iteration_cap:
                    inner_iterations += 1
                    available_tools = available_tool_definitions()
                    # Automatic compaction: when the provider-visible history grows
                    # past the threshold, drop the oldest user-turn groups before
                    # building the next request. The cut is at a UserMessage
                    # boundary so no tool result is orphaned, and the safe summary
                    # rides in the system prompt suffix below.
                    if settings.get_compaction_enabled() and should_compact_tool_loop_messages(
                        messages
                    ):
                        notice = apply_compaction("auto")
                        self._emit_diagnostic(terminal_ui, error_stream, notice)
                    provider_request = ProviderRequest(
                        system_prompt=agent_system_prompt + compaction_summary,
                        user_prompt=provider_user_input,
                        provider_name=effective_provider_name,
                        model_id=effective_model_id,
                        cwd=cwd,
                        messages=tuple(messages),
                        available_tools=available_tools,
                        # Image attachments belong to the current user message, so
                        # they ride only the first provider call of this turn; later
                        # tool-loop iterations append tool results (also user-role),
                        # and re-sending would mis-attach the image to those.
                        attachments=turn_attachments if inner_iterations == 1 else (),
                    )
                    if extension_before_provider_request_hooks:
                        provider_transform = dispatch_before_provider_request_hooks(
                            extension_before_provider_request_hooks,
                            provider_request,
                            cwd=str(cwd),
                            has_ui=terminal_ui is not None,
                            notify_sink=_extension_notify,
                            set_active_tools_fn=extension_set_active_tools,
                            set_model_fn=lambda _reference: False,
                            set_thinking_level_fn=extension_set_thinking_level,
                            flags=extension_flag_values,
                        )
                        filtered_tools = available_tool_definitions(
                            provider_transform.available_tools
                        )
                        final_user_prompt = (
                            provider_transform.user_prompt
                            if provider_transform.user_prompt is not None
                            else provider_request.user_prompt
                        )
                        provider_messages = self._provider_messages_with_prompt(
                            tuple(messages),
                            original_prompt=provider_request.user_prompt,
                            provider_prompt=final_user_prompt,
                        )
                        provider_request = ProviderRequest(
                            system_prompt=provider_transform.system_prompt
                            if provider_transform.system_prompt is not None
                            else provider_request.system_prompt,
                            user_prompt=final_user_prompt,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            cwd=cwd,
                            messages=provider_messages,
                            available_tools=filtered_tools,
                            attachments=provider_request.attachments,
                        )
                    emitter.turn_start()
                    if inner_iterations == 1:
                        # Pi emits the user message_start/message_end pair after
                        # turn_start and before the assistant message begins.
                        emitter.non_streamed_message(turn_user_message)
                    emitter.assistant_message_start()
                    renderer.begin_provider_turn()
                    renderer.show_working()
                    provider_result = self._complete_provider_turn(
                        provider_request,
                        renderer=renderer,
                        terminal_ui=terminal_ui,
                        emitter=emitter,
                    )
                    if provider_result is None:
                        # Aborted/steered turn (RPC abort, or TUI Escape/steer).
                        # Close the assistant message and the turn so the automation
                        # lifecycle stays balanced (message_start has a matching
                        # message_end, turn_start a matching turn_end) before the
                        # agent_end emitted after the inner loop.
                        aborted_assistant_message = AssistantMessage(content="")
                        emitter.assistant_message_end(aborted_assistant_message)
                        emitter.turn_end(aborted_assistant_message, [])
                        break
                    usage_accumulator.absorb(provider_result.usage)
                    renderer.end_provider_turn(
                        final_text=provider_result.final_text or "",
                        has_tool_calls=bool(provider_result.tool_calls),
                    )
                    if provider_result.status != HarnessStatus.SUCCEEDED:
                        error_type = provider_result.error_type or "ProviderFailed"
                        error_message = (
                            provider_result.error_message
                            or f"provider {effective_provider_name!r} returned status "
                            f"{provider_result.status.value!r} without a final response"
                        )
                        safe_metadata = provider_result.metadata or {}
                        diagnostic_suffix = ""
                        response_status = safe_metadata.get("response_status")
                        if isinstance(response_status, str) and response_status:
                            diagnostic_suffix = f" (response_status={response_status})"
                        # Surface the failure on the error stream but keep the
                        # REPL alive: a transient HTTP error from a single
                        # provider turn (e.g. a 503 the retry helper exhausted
                        # against, or a brief network hiccup) should not tear
                        # the whole session down. The user can ask again at
                        # the next prompt.
                        self._emit_diagnostic(
                            terminal_ui,
                            error_stream,
                            (
                                "pipy: provider failure during turn: "
                                f"{error_type}: {error_message}{diagnostic_suffix}"
                            ),
                        )
                        if legacy_footer_enabled():
                            self._print_footer(
                                error_stream,
                                cwd=cwd,
                                provider_name=effective_provider_name,
                                model_id=effective_model_id,
                                user_turn_count=user_turn_count,
                                tool_invocation_count=tool_invocation_count,
                                usage_accumulator=usage_accumulator,
                            )
                        # Balance the assistant message_start emitted above and close
                        # the turn so the automation stream stays well-formed even on
                        # a failed provider turn.
                        failed_assistant_message = AssistantMessage(content="")
                        emitter.assistant_message_end(failed_assistant_message)
                        emitter.turn_end(failed_assistant_message, [])
                        break
                    tool_calls = tuple(provider_result.tool_calls)
                    turn_assistant_message = AssistantMessage(
                        content=provider_result.final_text or "",
                        tool_calls=tool_calls,
                    )
                    emitter.assistant_message_end(turn_assistant_message)
                    turn_tool_results: list[ToolResultMessage] = []
                    messages.append(turn_assistant_message)
                    session_tree.append_message(turn_assistant_message)

                    if not tool_calls:
                        if provider_result.final_text and not renderer.streamed_any:
                            print(provider_result.final_text, file=output_stream)
                        if legacy_footer_enabled():
                            self._print_footer(
                                error_stream,
                                cwd=cwd,
                                provider_name=effective_provider_name,
                                model_id=effective_model_id,
                                user_turn_count=user_turn_count,
                                tool_invocation_count=tool_invocation_count,
                                usage_accumulator=usage_accumulator,
                            )
                        emitter.turn_end(turn_assistant_message, turn_tool_results)
                        break

                    fatal = False
                    tool_interrupted_turn = False
                    for call_index, call in enumerate(tool_calls):
                        if invocations_this_turn >= self.tool_budget:
                            budget_exhausted_count += 1
                            renderer.render_tool_call(call)
                            renderer.render_tool_result(
                                output_text=(
                                    f"tool budget exhausted "
                                    f"(limit {self.tool_budget})"
                                ),
                                is_error=True,
                            )
                            budget_observation = self._error_observation(
                                call=call,
                                output_text=(
                                    f"tool budget exhausted "
                                    f"(limit {self.tool_budget})"
                                ),
                            )
                            emitter.tool_execution_start(call)
                            emitter.tool_execution_end(
                                tool_call_id=call.provider_correlation_id,
                                tool_name=call.tool_name,
                                result=budget_observation.output_text,
                                is_error=True,
                            )
                            turn_tool_results.append(budget_observation)
                            messages.append(budget_observation)
                            session_tree.append_message(budget_observation)
                            continue

                        renderer.render_tool_call(call)
                        # Extension `tool_call` policy gate: a registered hook
                        # may inspect the live tool name + parsed input and
                        # return a ToolBlock to block the call. The raw input
                        # is inspected live but never archived.
                        tool_block = dispatch_tool_call_hooks(
                            extension_tool_call_hooks_,
                            tool_name=call.tool_name,
                            tool_input=_parse_tool_input(call.arguments_json),
                            cwd=str(cwd),
                            has_ui=terminal_ui is not None,
                            notify_sink=_extension_notify,
                            set_active_tools_fn=extension_set_active_tools,
                            set_model_fn=lambda _reference: False,
                            set_thinking_level_fn=extension_set_thinking_level,
                            flags=extension_flag_values,
                        )
                        if tool_block is not None:
                            blocked_observation = self._error_observation(
                                call=call,
                                output_text=f"blocked by extension: {tool_block.reason}",
                            )
                            emitter.tool_execution_start(call)
                            emitter.tool_execution_end(
                                tool_call_id=call.provider_correlation_id,
                                tool_name=call.tool_name,
                                result=blocked_observation.output_text,
                                is_error=True,
                            )
                            renderer.render_tool_result(
                                output_text=blocked_observation.output_text,
                                is_error=True,
                            )
                            turn_tool_results.append(blocked_observation)
                            messages.append(blocked_observation)
                            session_tree.append_message(blocked_observation)
                            # A blocked call still consumes the per-turn tool
                            # budget, so a provider repeating a blocked call
                            # cannot drive the loop unbounded. It is not a real
                            # tool invocation, so `tool_invocation_count` is
                            # left unchanged.
                            invocations_this_turn += 1
                            continue
                        emitter.tool_execution_start(call)
                        active_tool_call[0] = call
                        tool_started_at = datetime.now(UTC)
                        try:
                            observation, tool_interrupt = self._invoke_interruptible(
                                call=call,
                                context=context,
                                registry=run_tool_registry,
                                terminal_ui=terminal_ui,
                            )
                        finally:
                            active_tool_call[0] = None
                        # tool_result hooks may transform the finalized,
                        # bounded observation before the emitter, renderer,
                        # model, and session tree see it. ToolResultMessage is
                        # frozen, so a changed result is rebuilt.
                        if extension_tool_result_hooks:
                            _transformed = dispatch_tool_result_hooks(
                                extension_tool_result_hooks,
                                tool_name=call.tool_name,
                                content=observation.output_text,
                                is_error=observation.is_error,
                                cwd=str(cwd),
                                has_ui=terminal_ui is not None,
                                notify_sink=_extension_notify,
                                set_active_tools_fn=extension_set_active_tools,
                                set_model_fn=lambda _reference: False,
                                set_thinking_level_fn=extension_set_thinking_level,
                                flags=extension_flag_values,
                            )
                            if _transformed != observation.output_text:
                                observation = ToolResultMessage(
                                    tool_request_id=observation.tool_request_id,
                                    output_text=_transformed,
                                    is_error=observation.is_error,
                                    provider_correlation_id=(
                                        observation.provider_correlation_id
                                    ),
                                )
                        tool_ended_at = datetime.now(UTC)
                        tool_duration = (
                            tool_ended_at - tool_started_at
                        ).total_seconds()
                        emitter.tool_execution_end(
                            tool_call_id=call.provider_correlation_id,
                            tool_name=call.tool_name,
                            result=observation.output_text,
                            is_error=observation.is_error,
                        )
                        turn_tool_results.append(observation)
                        renderer.render_tool_result(
                            output_text=observation.output_text,
                            is_error=observation.is_error,
                            duration_seconds=tool_duration,
                        )
                        if tool_interrupt in {TURN_ABORTED, TURN_LOCAL_COMMAND}:
                            messages.append(observation)
                            session_tree.append_message(observation)
                            for skipped_call in tool_calls[call_index + 1 :]:
                                skipped = self._error_observation(
                                    call=skipped_call,
                                    output_text=(
                                        "tool skipped because the turn was interrupted"
                                    ),
                                )
                                turn_tool_results.append(skipped)
                                messages.append(skipped)
                                session_tree.append_message(skipped)
                            tool_interrupted_turn = True
                            break
                        if observation.is_error:
                            malformed_argument_count += 1
                            consecutive_malformed_streak += 1
                            messages.append(observation)
                            session_tree.append_message(observation)
                            if consecutive_malformed_streak >= self.MAX_MALFORMED_STREAK:
                                self._emit_diagnostic(
                                    terminal_ui,
                                    error_stream,
                                    (
                                        "pipy: tool-loop ended after "
                                        f"{self.MAX_MALFORMED_STREAK} consecutive malformed "
                                        "tool calls"
                                    ),
                                )
                                fatal = True
                                break
                            continue

                        invocations_this_turn += 1
                        tool_invocation_count += 1
                        consecutive_malformed_streak = 0
                        messages.append(observation)
                        session_tree.append_message(observation)

                    emitter.turn_end(turn_assistant_message, turn_tool_results)
                    if tool_interrupted_turn:
                        break
                    if fatal:
                        emitter.agent_end(
                            messages[agent_run_start_index:], will_retry=False
                        )
                        ended_at = datetime.now(UTC)
                        try:
                            repl_input.close()
                        except Exception:
                            pass
                        return NativeToolReplResult(
                            status=HarnessStatus.FAILED,
                            exit_code=1,
                            started_at=started_at,
                            ended_at=ended_at,
                            provider_name=effective_provider_name,
                            model_id=effective_model_id,
                            user_turn_count=user_turn_count,
                            tool_invocation_count=tool_invocation_count,
                            resource_invocation_count=resource_invocation_count,
                            malformed_argument_count=malformed_argument_count,
                            consecutive_malformed_streak=consecutive_malformed_streak,
                            budget_exhausted_count=budget_exhausted_count,
                            file_reference_count=file_reference_count,
                            file_reference_loaded_count=file_reference_loaded_count,
                            file_reference_failed_count=file_reference_failed_count,
                            compaction_count=compaction_count,
                            compaction_dropped_group_count=compaction_dropped_group_count_total,
                            error_type="NativeToolLoopMalformedFatal",
                            error_message=(
                                f"{self.MAX_MALFORMED_STREAK} consecutive malformed "
                                "tool calls"
                            ),
                        )

                # The inner loop settled this accepted prompt's agent run (no tool
                # calls left, the budget cap, or a provider failure). Close it on the
                # automation stream before the outer loop reads the next prompt.
                emitter.agent_end(
                    messages[agent_run_start_index:], will_retry=False
                )

            try:
                repl_input.close()
            except Exception:
                pass
            ended_at = datetime.now(UTC)
            return NativeToolReplResult(
                status=HarnessStatus.SUCCEEDED,
                exit_code=0,
                started_at=started_at,
                ended_at=ended_at,
                provider_name=effective_provider_name,
                model_id=effective_model_id,
                user_turn_count=user_turn_count,
                tool_invocation_count=tool_invocation_count,
                resource_invocation_count=resource_invocation_count,
                malformed_argument_count=malformed_argument_count,
                consecutive_malformed_streak=consecutive_malformed_streak,
                budget_exhausted_count=budget_exhausted_count,
                file_reference_count=file_reference_count,
                file_reference_loaded_count=file_reference_loaded_count,
                file_reference_failed_count=file_reference_failed_count,
                image_attachment_count=image_attachment_count,
                image_attachment_loaded_count=image_attachment_loaded_count,
                image_attachment_failed_count=image_attachment_failed_count,
                compaction_count=compaction_count,
                compaction_dropped_group_count=compaction_dropped_group_count_total,
            )
        finally:
            emitter.fire_lifecycle(EVENT_SESSION_SHUTDOWN)
            if terminal_ui is not None:
                terminal_ui.clear_extension_chrome()

    def _build_repl_input(
        self,
        *,
        input_stream: TextIO,
        error_stream: TextIO,
        workspace: Path,
        resources: WorkspaceResources,
        extension_menu_names: tuple[str, ...] = (),
        extension_descriptions: dict[str, str] | None = None,
    ) -> NativeReplInput:
        return native_repl_input_for(
            input_stream=input_stream,
            error_stream=error_stream,
            input_runtime=self.input_runtime,
            workspace=workspace,
            command_names=_tool_loop_command_names(resources, extension_menu_names),
            command_descriptions=_tool_loop_command_descriptions(
                resources, extension_descriptions
            ),
        )

    def _build_terminal_ui(
        self,
        *,
        input_stream: TextIO,
        error_stream: TextIO,
        workspace: Path,
        resources: WorkspaceResources,
        autocomplete_max_visible: int = 5,
        extension_menu_names: tuple[str, ...] = (),
        extension_descriptions: dict[str, str] | None = None,
        extension_shortcut_keys: frozenset[str] = frozenset(),
    ) -> ToolLoopTerminalUi | None:
        if self.input_runtime not in {REPL_INPUT_RUNTIME_AUTO, "tool-loop-tui"}:
            return None
        if not ToolLoopTerminalUi.is_supported(input_stream, error_stream):
            return None
        return ToolLoopTerminalUi(
            input_stream=input_stream,
            terminal_stream=error_stream,
            cwd=workspace,
            command_names=_tool_loop_command_names(resources, extension_menu_names),
            command_descriptions=_tool_loop_command_descriptions(
                resources, extension_descriptions
            ),
            autocomplete_max_visible=autocomplete_max_visible,
            extension_shortcut_keys=extension_shortcut_keys,
        )

    @staticmethod
    def _provider_messages_with_prompt(
        messages: tuple[LoopMessage, ...],
        *,
        original_prompt: str,
        provider_prompt: str,
    ) -> tuple[LoopMessage, ...]:
        """Return provider-visible messages with the current prompt transformed.

        `before_provider_request` transforms are provider-payload changes. The
        durable native session tree and prompt history keep the user's literal
        prompt, but providers that serialize `request.messages` must still see
        the transformed current user message. Replace only the most recent
        matching `UserMessage` in this request-local tuple.
        """

        if provider_prompt == original_prompt or not messages:
            return messages
        replaced = list(messages)
        for index in range(len(replaced) - 1, -1, -1):
            message = replaced[index]
            if isinstance(message, UserMessage) and message.content == original_prompt:
                replaced[index] = UserMessage(content=provider_prompt)
                return tuple(replaced)
        return messages

    @staticmethod
    def _tee_stream_sink(
        base: StreamChunkSink, emitter: "AutomationEmitter | None"
    ) -> StreamChunkSink:
        """Tee provider text deltas into the automation emitter.

        Each streamed chunk becomes a Pi ``message_update`` with a
        ``text_delta`` ``assistantMessageEvent``. A no-op passthrough when the
        emitter is absent/disabled, so the interactive path is unchanged.
        """

        if emitter is None or not emitter.enabled:
            return base

        def _wrapped(chunk: str) -> None:
            base(chunk)
            emitter.assistant_text_delta(chunk)

        return _wrapped

    def _complete_headless_cancellable_turn(
        self,
        provider_request: ProviderRequest,
        *,
        renderer: "_ToolLoopRenderer | _TuiToolLoopRenderer",
        emitter: "AutomationEmitter | None",
    ) -> ProviderResult | None:
        """Run a non-TUI provider turn that honors an external abort event.

        Used by ``--mode rpc`` so an ``abort`` command cancels the live request
        at the provider boundary. Returns ``None`` when aborted before the
        provider settled, ending the current turn (the loop then drains/reads).
        """

        assert self.abort_event is not None
        cancel_token = CancelToken()
        abort_event = self.abort_event
        done_event = threading.Event()
        # Per-turn cancelled latch. Unlike the shared ``abort_event`` (which the
        # RPC server clears on ``agent_end`` so the next prompt can run), this
        # local flag stays set for the lifetime of this turn's worker. If a
        # provider thread lingers past the cancel join, a late chunk it produces
        # after ``agent_end`` is still suppressed here — no output leaks past the
        # turn's end into the JSON/RPC event stream.
        turn_cancelled = threading.Event()
        result_holder: list[ProviderResult] = []
        error_holder: list[BaseException] = []

        def _cancellable_sink(sink: StreamChunkSink) -> StreamChunkSink:
            def _wrapped(chunk: str) -> None:
                if turn_cancelled.is_set() or abort_event.is_set():
                    return
                sink(chunk)

            return _wrapped

        def _run_provider() -> None:
            try:
                result_holder.append(
                    self.provider.complete(
                        provider_request,
                        # Cancellable wraps the tee so an abort suppresses BOTH
                        # the renderer write and the automation text_delta event —
                        # no late chunk can leak after abort/agent_end.
                        stream_sink=_cancellable_sink(
                            self._tee_stream_sink(renderer.stream_sink, emitter)
                        ),
                        reasoning_sink=_cancellable_sink(renderer.reasoning_sink),
                        cancel_token=cancel_token,
                    )
                )
            except ProviderCancelledError:
                pass
            except BaseException as exc:  # pragma: no cover - re-raised below
                error_holder.append(exc)
            finally:
                done_event.set()

        worker = threading.Thread(
            target=_run_provider, name="pipy-rpc-provider-turn", daemon=True
        )
        worker.start()
        # Wake on either provider completion or an external abort.
        while not done_event.wait(timeout=0.05):
            if abort_event.is_set():
                # Latch the per-turn cancel before releasing the loop so any
                # chunk a lingering worker emits later stays suppressed even
                # after the RPC server clears the shared abort on agent_end.
                turn_cancelled.set()
                cancel_token.cancel()
                worker.join(timeout=self._CANCEL_JOIN_TIMEOUT_SECONDS)
                return None
        worker.join(timeout=self._CANCEL_JOIN_TIMEOUT_SECONDS)
        if error_holder:
            raise error_holder[0]
        if not result_holder:
            return None
        return result_holder[0]

    def _complete_provider_turn(
        self,
        provider_request: ProviderRequest,
        *,
        renderer: "_ToolLoopRenderer | _TuiToolLoopRenderer",
        terminal_ui: ToolLoopTerminalUi | None,
        emitter: "AutomationEmitter | None" = None,
    ) -> ProviderResult | None:
        if terminal_ui is None:
            if self.abort_event is not None:
                # Headless automation (RPC) abort: run the provider on a worker
                # so an external abort cancels the in-flight turn at the provider
                # boundary, mirroring the TUI cancel path without a terminal.
                return self._complete_headless_cancellable_turn(
                    provider_request, renderer=renderer, emitter=emitter
                )
            return self.provider.complete(
                provider_request,
                stream_sink=self._tee_stream_sink(renderer.stream_sink, emitter),
                reasoning_sink=renderer.reasoning_sink,
            )

        cancel_token = CancelToken()
        abort_event = cancel_token.event
        done_event = threading.Event()
        result_holder: list[ProviderResult] = []
        error_holder: list[BaseException] = []

        def _cancellable_sink(sink: StreamChunkSink) -> StreamChunkSink:
            def _wrapped(chunk: str) -> None:
                if abort_event.is_set():
                    return
                sink(chunk)

            return _wrapped

        def _run_provider() -> None:
            try:
                result_holder.append(
                    self.provider.complete(
                        provider_request,
                        # Cancellable wraps the tee so an abort suppresses BOTH
                        # the renderer write and the automation text_delta event.
                        stream_sink=_cancellable_sink(
                            self._tee_stream_sink(renderer.stream_sink, emitter)
                        ),
                        reasoning_sink=_cancellable_sink(renderer.reasoning_sink),
                        cancel_token=cancel_token,
                    )
                )
            except BaseException as exc:  # pragma: no cover - re-raised on main thread
                error_holder.append(exc)
            finally:
                done_event.set()

        worker = threading.Thread(
            target=_run_provider,
            name="pipy-provider-turn",
            daemon=True,
        )
        worker.start()
        # The mid-turn watcher returns one of settled/aborted/steered. Escape /
        # Ctrl-C abort: cancel the live provider request at its boundary (not
        # merely hide late output), reap the worker, render the aborted state,
        # and restore any queued messages to the editor. A steering submit also
        # cancels the request, but promotes the queued messages for sequential
        # delivery (no aborted banner). Both return None so the inner loop ends
        # this turn and the outer loop drains/reads next.
        try:
            outcome = terminal_ui.wait_for_active_turn_interrupt(
                done_event, abort_event, accept_queue=True
            )
        except KeyboardInterrupt:
            self._cancel_active_turn(cancel_token, worker)
            renderer.abort_provider_turn()
            terminal_ui.restore_pending_to_editor()
            return None
        if outcome == TURN_ABORTED:
            self._cancel_active_turn(cancel_token, worker)
            renderer.abort_provider_turn()
            terminal_ui.restore_pending_to_editor()
            return None
        if outcome == TURN_STEERED:
            self._cancel_active_turn(cancel_token, worker)
            renderer.steer_provider_turn()
            terminal_ui.promote_pending_to_drain()
            return None
        if outcome == TURN_LOCAL_COMMAND:
            # A local command (`/…`/`!…`) submitted mid-turn interrupts the turn
            # and runs locally. Cancel like a steer (no error banner); the
            # command is held on the UI and dispatched by the next loop
            # iteration through the normal local-command path (never the
            # provider). Any earlier-queued steering/follow-up still promotes.
            self._cancel_active_turn(cancel_token, worker)
            renderer.steer_provider_turn()
            terminal_ui.promote_pending_to_drain()
            return None
        worker.join()
        if error_holder:
            raise error_holder[0]
        # Follow-ups (and any steering) queued during a turn that settled on its
        # own are delivered in order after it.
        if terminal_ui.has_pending_messages():
            terminal_ui.promote_pending_to_drain()
        return result_holder[0]

    def _share_native_session_command(
        self,
        *,
        session_tree: NativeSessionTree,
        token: str,
        terminal_ui: ToolLoopTerminalUi | None,
        error_stream: TextIO,
    ) -> ShareResult | None:
        """Run ``/share`` with product cancellation when the TUI is active."""

        if terminal_ui is None:
            return share_native_session(
                session_tree,
                token=token,
                cancelled=(
                    self.abort_event.is_set if self.abort_event is not None else None
                ),
            )

        cancel_token = CancelToken()
        done_event = threading.Event()
        result_holder: list[ShareResult] = []
        error_holder: list[BaseException] = []

        def _run_share() -> None:
            try:
                result_holder.append(
                    share_native_session(
                        session_tree,
                        token=token,
                        cancelled=cancel_token.event.is_set,
                        cancel_token=cancel_token,
                    )
                )
            except BaseException as exc:  # pragma: no cover - re-raised below
                error_holder.append(exc)
            finally:
                done_event.set()

        self._emit_diagnostic(
            terminal_ui,
            error_stream,
            "pipy: sharing native session... press Escape to cancel.",
        )
        worker = threading.Thread(target=_run_share, name="pipy-share-gist", daemon=True)
        worker.start()
        try:
            outcome = terminal_ui.wait_for_active_turn_interrupt(
                done_event, cancel_token.event, accept_queue=False
            )
        except KeyboardInterrupt:
            cancel_token.cancel()
            worker.join(timeout=self._CANCEL_JOIN_TIMEOUT_SECONDS)
            self._emit_diagnostic(terminal_ui, error_stream, "pipy: Share cancelled.")
            return None
        if outcome == TURN_ABORTED:
            cancel_token.cancel()
            worker.join(timeout=self._CANCEL_JOIN_TIMEOUT_SECONDS)
            self._emit_diagnostic(terminal_ui, error_stream, "pipy: Share cancelled.")
            return None
        worker.join(timeout=self._CANCEL_JOIN_TIMEOUT_SECONDS)
        if error_holder:
            error = error_holder[0]
            if isinstance(error, ShareCancelled):
                self._emit_diagnostic(terminal_ui, error_stream, "pipy: Share cancelled.")
                return None
            if isinstance(error, NativeExportError):
                raise error
            raise error
        return result_holder[0] if result_holder else None

    # Bound on how long the main thread waits for a cancelled provider worker to
    # unwind after its connection is closed. The worker is a daemon thread, so
    # if the join times out the process can still exit and—because the turn
    # returns ``None``—the worker can no longer mutate provider/tool/context
    # state regardless.
    _CANCEL_JOIN_TIMEOUT_SECONDS: ClassVar[float] = 2.0

    # Bound on a ``!``/``!!`` editor shell command so it cannot hang the session
    # indefinitely (Escape cancels earlier in a live TTY; a non-TTY script has no
    # cancel key, so the deadline is the only bound there). Generous so ordinary
    # builds/tests finish well within it.
    _LOCAL_SHELL_TIMEOUT_SECONDS: ClassVar[int] = 600

    def _run_local_shell_shortcut(
        self,
        command_line: str,
        *,
        terminal_ui: ToolLoopTerminalUi | None,
        error_stream: TextIO,
        cwd: Path,
        user_bash_hooks: Sequence[HookHandler] = (),
        set_active_tools_fn: Callable[[Sequence[str]], bool] | None = None,
        set_model_fn: Callable[[str], bool] | None = None,
        set_thinking_level_fn: Callable[[str], bool] | None = None,
        flags: Mapping[str, object] | None = None,
    ) -> str | None:
        """Run a ``!``/``!!`` editor shell shortcut; return context text or None.

        ``!!`` excludes the command from provider context (returns ``None``);
        ``!`` returns the command/output text to record into the conversation
        and native session tree. Output streams live into a shaded shell block,
        and Escape cancels a running command (terminating its process group)
        without tearing down the session. Runs no provider turn.
        """

        exclude_from_context = command_line.startswith("!!")
        command = (
            command_line[2:] if exclude_from_context else command_line[1:]
        ).strip()
        if not command:
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                "pipy: ! needs a command, e.g. !ls (use !! to skip recording).",
            )
            return None

        decision = dispatch_user_bash_hooks(
            user_bash_hooks,
            command=command,
            exclude_from_context=exclude_from_context,
            cwd=str(cwd),
            has_ui=terminal_ui is not None,
            notify_sink=lambda kind, message: self._emit_diagnostic(
                terminal_ui, error_stream, message
            ),
            set_active_tools_fn=set_active_tools_fn,
            set_model_fn=set_model_fn,
            set_thinking_level_fn=set_thinking_level_fn,
            flags=flags,
        )
        if not decision.allowed:
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                f"pipy: shell command blocked by extension: {decision.reason}",
            )
            return None
        command = decision.command
        exclude_from_context = decision.exclude_from_context

        if terminal_ui is not None:
            terminal_ui.add_tool_call(f"$ {command}")
            sink: Callable[[str], None] = terminal_ui.append_tool_output
        else:
            print(f"$ {command}", file=error_stream)

            def sink(chunk: str) -> None:
                print(chunk, end="", file=error_stream, flush=True)

        if decision.result is not None:
            result = LocalShellResult(
                output=decision.result,
                exit_code=decision.exit_code,
                truncated=False,
                timed_out=False,
                cancelled=False,
                started=True,
            )
            sink(decision.result)
        else:
            result = self._execute_local_shell(
                command, sink=sink, terminal_ui=terminal_ui, cwd=cwd
            )

        output_text = result.output or "(no output)"
        # Status line mirrors the bash tool's _shape: a timeout, the exit code,
        # or cancellation. A non-zero exit (e.g. !false) is an error the model
        # should see, matching the real bash execution boundary.
        if result.cancelled:
            reason = result.cancel_reason or "escape"
            status_line = f"(cancelled by {reason})"
        elif result.timed_out:
            status_line = "(timed out)"
        else:
            status_line = f"exit code: {result.exit_code}"
        is_error = (
            result.timed_out
            or not result.started
            or (
                not result.cancelled
                and result.exit_code is not None
                and result.exit_code != 0
            )
        )
        if terminal_ui is not None:
            rendered = [status_line, *(output_text.splitlines() or [""])]
            terminal_ui.add_tool_result(lines=rendered, is_error=is_error)
        else:
            # Captured-stream path: the body already streamed through the sink,
            # so print only the status line (never re-print the output — that
            # duplicated every command's output).
            print(status_line, file=error_stream)

        if exclude_from_context or not result.started:
            return None
        return (
            "I ran a shell command in the workspace (not a tool call):\n\n"
            f"$ {command}\n{status_line}\n\n{output_text}"
        )

    # Pi's reasoning-level cycle order (THINKING_LEVELS in agent-session.ts).
    # Pipy's catalog adds an "xhigh" tier that is not part of the Shift+Tab
    # cycle; the cycle clamps to these five, matching Pi.
    _THINKING_CYCLE_LEVELS: ClassVar[tuple[str, ...]] = (
        "off",
        "minimal",
        "low",
        "medium",
        "high",
    )

    def _toggle_view_fold(
        self,
        hotkey: str,
        *,
        terminal_ui: ToolLoopTerminalUi | None,
        error_stream: TextIO,
        settings: "SettingsManager",
    ) -> None:
        """Toggle a renderer view fold (Pi Ctrl+O tool output / Ctrl+T thinking).

        Ctrl+O flips tool-output expansion (a pure live-render view flag); Ctrl+T
        flips thinking-block visibility and persists it to the non-secret
        settings store. Both run no provider turn and only mutate renderer view
        state (plus, for thinking, the settings file). A status is shown.
        """

        if hotkey == HOTKEY_TOGGLE_TOOLS:
            new_value = not (terminal_ui.tools_expanded if terminal_ui else False)
            if terminal_ui is not None:
                terminal_ui.tools_expanded = new_value
            label = "expanded" if new_value else "collapsed"
            self._emit_diagnostic(
                terminal_ui, error_stream, f"pipy: tool output: {label}"
            )
            return
        # HOTKEY_TOGGLE_THINKING
        current = (
            terminal_ui.thinking_hidden
            if terminal_ui is not None
            else settings.get_hide_thinking_block()
        )
        new_hidden = not current
        if terminal_ui is not None:
            # Route through set_thinking_hidden so unfolding reveals any
            # reasoning that settled while folded (deferred, not dropped).
            terminal_ui.set_thinking_hidden(new_hidden)
        try:
            settings.set_value("hideThinkingBlock", new_hidden)
        except RuntimeError:
            # A read-only/locked settings file must not break the live toggle.
            pass
        label = "hidden" if new_hidden else "visible"
        self._emit_diagnostic(
            terminal_ui, error_stream, f"pipy: thinking blocks: {label}"
        )

    def _cycle_thinking_level(
        self,
        *,
        terminal_ui: ToolLoopTerminalUi | None,
        error_stream: TextIO,
        session_tree: NativeSessionTree,
    ) -> None:
        """Cycle the reasoning level (Pi's Shift+Tab ``cycleThinkingLevel``).

        Cycles off→minimal→low→medium→high (wrapping), clamped to whether the
        active model advertises reasoning support, sets the runtime level on the
        provider state (so the footer effort label reflects it), appends a
        ``thinking_level_change`` native-tree entry, and shows a status. Runs no
        provider turn; the new level applies to the next turn.
        """

        state = self.provider_state
        if not isinstance(state, NativeReplProviderState):
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                "pipy: thinking-level cycling is unavailable for this REPL state.",
            )
            return
        current = state.current_selection()
        supports_thinking = any(
            option.selection.provider_name == current.provider_name
            and option.selection.model_id == current.model_id
            and bool(option.reasoning)
            for option in state.model_options()
        )
        if not supports_thinking:
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                "pipy: current model does not support thinking.",
            )
            return
        levels = self._THINKING_CYCLE_LEVELS
        current_level = state.thinking_level if state.thinking_level in levels else "off"
        next_level = levels[(levels.index(current_level) + 1) % len(levels)]
        state.thinking_level = next_level
        session_tree.append_thinking_level_change(next_level)
        self._emit_diagnostic(
            terminal_ui, error_stream, f"pipy: thinking level: {next_level}"
        )

    def _execute_local_shell(
        self,
        command: str,
        *,
        sink: Callable[[str], None],
        terminal_ui: ToolLoopTerminalUi | None,
        cwd: Path,
    ) -> LocalShellResult:
        """Execute ``command`` locally, watching stdin for Escape cancellation.

        With no live TUI (captured streams), runs synchronously. With a live
        TUI, runs the command on a worker thread while the same active-turn
        interrupt watcher used for provider turns reads stdin; Escape/Ctrl-C set
        the cancel event so the runner kills the child process group, then the
        worker is best-effort joined.
        """

        if terminal_ui is None:
            return run_local_command(
                command,
                workspace_root=cwd,
                output_sink=sink,
                timeout=self._LOCAL_SHELL_TIMEOUT_SECONDS,
            )

        cancel_event = threading.Event()
        done_event = threading.Event()
        holder: list[LocalShellResult] = []

        def _worker() -> None:
            try:
                holder.append(
                    run_local_command(
                        command,
                        workspace_root=cwd,
                        output_sink=sink,
                        cancel_event=cancel_event,
                        timeout=self._LOCAL_SHELL_TIMEOUT_SECONDS,
                    )
                )
            finally:
                done_event.set()

        worker = threading.Thread(
            target=_worker, name="pipy-local-shell", daemon=True
        )
        worker.start()
        outcome = TURN_SETTLED
        try:
            outcome = terminal_ui.wait_for_active_turn_interrupt(
                done_event, cancel_event, accept_commands=True
            )
        except KeyboardInterrupt:
            cancel_event.set()
            outcome = TURN_ABORTED
        worker.join(timeout=self._CANCEL_JOIN_TIMEOUT_SECONDS)
        cancel_reason = "local command" if outcome == TURN_LOCAL_COMMAND else "escape"
        if holder:
            result = holder[0]
            if result.cancelled:
                result.cancel_reason = cancel_reason
            return result
        return LocalShellResult(
            output="",
            exit_code=None,
            truncated=False,
            timed_out=False,
            cancelled=True,
            started=True,
            cancel_reason=cancel_reason,
        )

    def _cancel_active_turn(
        self, cancel_token: CancelToken, worker: threading.Thread
    ) -> None:
        """Cancel the in-flight provider request and best-effort join its worker.

        ``cancel`` sets the abort flag and shuts down the worker's registered
        HTTP connection so a blocking read raises ``ProviderCancelledError``
        instead of running to completion. Every shipped adapter observes the
        token, so the bounded join below normally reclaims the worker promptly.

        Cancellation is cooperative: a provider that ignores ``cancel_token``
        could leave the join to time out with the daemon worker still running.
        That worker still cannot corrupt the session — the turn returns ``None``
        (so no assistant/tool message or context mutation is appended) and late
        stream/reasoning chunks are suppressed by the cancellable sink — it can
        only finish its own request in the background and have its output
        discarded.
        """

        cancel_token.cancel()
        worker.join(timeout=self._CANCEL_JOIN_TIMEOUT_SECONDS)

    def _effort_label(self, provider_name: str, model_id: str) -> str:
        """Reasoning-effort label, preferring the live runtime thinking level.

        When the user has cycled the thinking level with Shift+Tab (or selected
        a ``model:level`` reference), the provider state carries the runtime
        level and the footer reflects it; otherwise it falls back to the
        model's default effort label.
        """

        level = getattr(self.provider_state, "thinking_level", None)
        if isinstance(level, str) and level:
            return level
        return _effort_label_for(provider_name, model_id)

    def _footer_text(
        self,
        *,
        cwd: Path,
        provider_name: str,
        model_id: str,
        user_turn_count: int,
        tool_invocation_count: int,
        error_stream: TextIO | None = None,
        usage_accumulator: "_UsageAccumulator | None" = None,
    ) -> str:
        plan_label = "sub" if provider_name == "openai-codex" else "api"
        budget = _context_budget_for(provider_name, model_id)
        used_pct = 0.0
        if budget.token_budget > 0:
            if usage_accumulator is not None and usage_accumulator.last_total_tokens > 0:
                used_pct = (
                    100.0
                    * usage_accumulator.last_total_tokens
                    / float(budget.token_budget)
                )
            else:
                estimated_tokens = self._estimated_context_tokens(
                    tool_invocation_count=tool_invocation_count,
                    user_turn_count=user_turn_count,
                )
                used_pct = 100.0 * estimated_tokens / float(budget.token_budget)
            used_pct = min(used_pct, 999.9)
        cost_label = (
            f"${usage_accumulator.cost_usd:.3f}"
            if usage_accumulator is not None
            else "$0.000"
        )
        cache_hit_percent = (
            usage_accumulator.cache_hit_percent
            if usage_accumulator is not None
            else None
        )
        fields = BottomStatusFields(
            cwd_label="",
            cost_label=cost_label,
            plan_label=plan_label,
            context_used_pct=used_pct,
            context_budget_label=budget.budget_label,
            context_budget_suffix="auto",
            provider_name=provider_name,
            model_id=model_id,
            effort_label=self._effort_label(provider_name, model_id),
            tokens_in=(
                usage_accumulator.input_tokens if usage_accumulator else 0
            ),
            tokens_out=(
                usage_accumulator.output_tokens if usage_accumulator else 0
            ),
            tokens_reasoning=(
                usage_accumulator.reasoning_tokens if usage_accumulator else 0
            ),
            tokens_cache_read=(
                usage_accumulator.cache_read_tokens if usage_accumulator else 0
            ),
            tokens_cache_write=(
                usage_accumulator.cache_write_tokens if usage_accumulator else 0
            ),
            cache_hit_percent=cache_hit_percent,
        )
        status_width = max(20, chrome_width(error_stream))
        status_line = format_bottom_status_line(status_width, fields)
        cwd_label = _friendly_cwd_label(cwd)
        return f"{cwd_label}\n{status_line}"

    def _estimated_context_tokens(
        self, *, tool_invocation_count: int, user_turn_count: int
    ) -> float:
        """Cheap upper-bound estimate for the prompt's context-window draw.

        We do not parse provider usage telemetry yet; until that lands the
        bottom-status meter shows a deterministic rough estimate that
        grows with tool invocations and user turns. This matches Pi's
        ``used%/budget`` shape without inventing fake exact counts.
        """

        per_turn_tokens = 2_000.0
        per_tool_tokens = 1_500.0
        return (
            user_turn_count * per_turn_tokens
            + tool_invocation_count * per_tool_tokens
        )

    def _print_footer(
        self,
        error_stream: TextIO,
        *,
        cwd: Path,
        provider_name: str,
        model_id: str,
        user_turn_count: int,
        tool_invocation_count: int,
        usage_accumulator: "_UsageAccumulator | None" = None,
    ) -> None:
        print_input_separator(error_stream)
        footer = self._footer_text(
            cwd=cwd,
            provider_name=provider_name,
            model_id=model_id,
            user_turn_count=user_turn_count,
            tool_invocation_count=tool_invocation_count,
            error_stream=error_stream,
            usage_accumulator=usage_accumulator,
        )
        cwd_label, _, status_line = footer.partition("\n")
        print_bottom_status_block(
            error_stream, cwd_label=cwd_label, status_line=status_line
        )

    def _settings_overlay_lines(
        self, settings_manager: "SettingsManager | None" = None
    ) -> list[str]:
        """Build the read-only settings/status overlay content.

        Reuses the shared no-tool ``/settings`` builder so the tool-loop TUI
        shows the same safe provider/model/status information and availability
        reasons, then appends a footer honest for the tool-loop surface (where
        ``/model``, ``/login``, and ``/logout`` are all executable). When no
        provider state is wired, a single-provider static view is shown and the
        footer says those commands are unavailable for that state.
        """

        state = self.provider_state or StaticNativeReplProviderState(self.provider)
        lines = settings_overlay_lines(state, settings_manager)
        if isinstance(state, NativeReplProviderState):
            lines.append(
                "  read-only view; use /model to switch provider/model and "
                "/login or /logout to manage openai-codex OAuth."
            )
        else:
            lines.append(
                "  read-only view; /model, /login, and /logout are not "
                "available for this REPL provider state."
            )
        return lines

    def _drive_settings_dialog(
        self,
        terminal_ui: ToolLoopTerminalUi,
        prompt_history_store: PromptHistoryStore,
        *,
        apply_model_selection: Callable[[str], tuple[bool, str]],
        apply_auth_change: Callable[[str, str], str],
        settings: "SettingsManager",
        session_tree: NativeSessionTree,
        error_stream: TextIO,
    ) -> None:
        """Open the live ``/settings`` dialog and act on the user's choices.

        Local toggles (persistent prompt-history on/off, clear persisted
        history) are handled in place by the dialog without leaving it.
        Provider/model and auth actions reuse the existing
        ``NativeReplProviderState`` boundaries (``apply_model_selection`` /
        ``apply_auth_change``) and run **no** provider or tool turn; afterward
        the dialog re-opens so the user can keep adjusting settings. The dialog
        closes on Esc/Ctrl-C/Ctrl-D.
        """

        state = self.provider_state or StaticNativeReplProviderState(self.provider)
        is_native = isinstance(state, NativeReplProviderState)
        # Actions that need the terminal themselves (an interactive selector or
        # auth flow) close the dialog and are returned for the caller's
        # post-return branch to drive; everything else is handled locally by
        # ``on_local_action`` while the dialog stays open. The theme picker is
        # available for any provider state with a live TUI, so it is always an
        # exit action; the provider/model, auth, and scoped-models flows are
        # native-only (scoped models builds model patterns from the native
        # provider state, and its row is shown only for that state).
        exit_actions = frozenset({"theme"}) | (
            frozenset({"model", "login", "logout", "scoped_models"})
            if is_native
            else frozenset()
        )

        def _rows() -> list[SettingsRow]:
            return self._settings_dialog_rows(
                state,
                prompt_history_store,
                in_memory_depth=len(terminal_ui.input_history),
                terminal_ui=terminal_ui,
            )

        def _local_action(action: str) -> list[SettingsRow]:
            if action == "toggle_history":
                prompt_history_store.set_enabled(not prompt_history_store.enabled)
            elif action == "clear_history":
                # Wipe only the persisted store; the current session's in-memory
                # Up/Down recall keeps working (the goal only requires that a
                # *fresh* session not recall cleared prompts, and record() never
                # re-persists the existing recall buffer — only new prompts).
                prompt_history_store.clear()
            elif action == "toggle_tools":
                self._toggle_view_fold(
                    HOTKEY_TOGGLE_TOOLS,
                    terminal_ui=terminal_ui,
                    error_stream=error_stream,
                    settings=settings,
                )
            elif action == "toggle_thinking":
                self._toggle_view_fold(
                    HOTKEY_TOGGLE_THINKING,
                    terminal_ui=terminal_ui,
                    error_stream=error_stream,
                    settings=settings,
                )
            elif action == "cycle_thinking":
                self._cycle_thinking_level(
                    terminal_ui=terminal_ui,
                    error_stream=error_stream,
                    session_tree=session_tree,
                )
            return _rows()

        while True:
            action = terminal_ui.run_settings_dialog(
                _rows(),
                on_local_action=_local_action,
                exit_actions=exit_actions,
            )
            if action is None:
                return
            if action == "model" and isinstance(state, NativeReplProviderState):
                ui_options, selections = self._model_selector_rows(state)
                current = state.current_selection()
                current_index = next(
                    (
                        index
                        for index, selection in enumerate(selections)
                        if selection.provider_name == current.provider_name
                        and selection.model_id == current.model_id
                    ),
                    0,
                )
                chosen = terminal_ui.run_model_selector(
                    ui_options, current_index=current_index
                )
                if chosen is not None:
                    _ok, message = apply_model_selection(selections[chosen].reference)
                    terminal_ui.add_notice(message)
                continue
            if action in {"login", "logout"}:
                message = apply_auth_change(action, "")
                terminal_ui.add_notice(message)
                continue
            if action == "scoped_models" and isinstance(
                state, NativeReplProviderState
            ):
                self._open_scoped_models_overlay(
                    terminal_ui, state=state, settings=settings
                )
                continue
            if action == "theme":
                self._open_theme_selector(terminal_ui, settings=settings)
                continue

    def _open_scoped_models_overlay(
        self,
        terminal_ui: ToolLoopTerminalUi,
        *,
        state: NativeReplProviderState,
        settings: "SettingsManager",
    ) -> None:
        """Open the multi-select scope overlay and persist the chosen scope.

        Builds a checklist of available models, pre-checks those matching the
        current ``enabledModels`` patterns, and on save writes the chosen
        ``provider/model`` references back as the patterns the Ctrl+P cycle uses.
        Runs no provider turn.
        """

        available_refs = [
            option.selection.reference
            for option in state.model_options()
            if option.available
        ]
        if not available_refs:
            terminal_ui.add_notice("pipy: no available models to scope.")
            return
        scoped = filter_scoped_references(available_refs, settings.get_enabled_models())
        rows = [ScopedModelRow(reference=ref, available=True) for ref in available_refs]
        pre_checked = [
            index for index, ref in enumerate(available_refs) if ref in scoped
        ]
        chosen = terminal_ui.run_scoped_models_selector(rows, checked=pre_checked)
        if chosen is None:
            return
        try:
            settings.set_enabled_models(sorted(chosen))
            message = (
                "pipy: scoped models set: " + ", ".join(sorted(chosen))
                if chosen
                else "pipy: scoped models cleared (cycle uses the full catalog)."
            )
        except RuntimeError as exc:
            message = f"pipy: could not update scoped models: {exc}"
        terminal_ui.add_notice(message)

    def _open_theme_selector(
        self,
        terminal_ui: ToolLoopTerminalUi,
        *,
        settings: "SettingsManager",
    ) -> None:
        """Open the theme picker and apply + persist the chosen chrome theme.

        Mirrors the ``action == "model"`` path: it builds one selectable row per
        registered theme (the active theme starts highlighted), opens the shared
        label/selectable selector with a theme-specific heading, and on a choice
        applies the theme via ``select_theme`` (which sets ``PIPY_THEME`` so the
        next rendered frame repaints and persists the non-secret name to the
        chrome store) and persists it through ``settings`` — the source of truth
        a later ``/reload`` re-reads. Runs no provider turn, tool call, or
        archive write; ``Esc`` leaves the theme unchanged.
        """

        names = available_theme_names()
        if not names:
            terminal_ui.add_notice("pipy: no themes available to select.")
            return
        active = resolve_active_theme_name(env=os.environ, store=NativeThemeStore())
        options = [
            ModelSelectorOption(
                label=f"{name} (active)" if name == active else name,
                selectable=True,
            )
            for name in names
        ]
        current_index = next(
            (index for index, name in enumerate(names) if name == active), 0
        )
        chosen = terminal_ui.run_model_selector(
            options, current_index=current_index, title="Select theme"
        )
        if chosen is None:
            return
        name = names[chosen]
        ok, message = select_theme(
            name, environ=os.environ, store=NativeThemeStore()
        )
        if ok:
            # Settings is the source of truth (a later /reload re-applies
            # settings.get_theme() over the chrome store), so persist the choice
            # there too. A write failure keeps the live selection.
            try:
                settings.set_theme(name)
            except (OSError, RuntimeError):
                pass
        terminal_ui.add_notice(message)

    def _settings_dialog_rows(
        self,
        state: "NativeReplProviderState | StaticNativeReplProviderState",
        prompt_history_store: PromptHistoryStore,
        *,
        in_memory_depth: int,
        terminal_ui: ToolLoopTerminalUi | None = None,
    ) -> list[SettingsRow]:
        """Build the interactive ``/settings`` dialog rows.

        Strictly local/read-only construction: it probes the current
        selection, openai-codex auth availability, and prompt-history state but
        runs no provider turn, tool call, or auth/model mutation. Actionable
        rows carry an identifier the dialog hands back when activated; headers
        and read-only status rows stay visible for context but are not
        choosable.
        """

        current = state.current_selection()
        rows: list[SettingsRow] = [
            SettingsRow(label="Provider / model", kind="header"),
            SettingsRow(
                label=f"active: {sanitize_text(current.reference)}", kind="status"
            ),
        ]
        if isinstance(state, NativeReplProviderState):
            rows.append(
                SettingsRow(
                    label="change provider/model…", kind="action", action="model"
                )
            )
            rows.append(SettingsRow(label="Authentication", kind="header"))
            if state.provider_available("openai-codex"):
                rows.append(
                    SettingsRow(
                        label="openai-codex: logged in — log out",
                        kind="action",
                        action="logout",
                    )
                )
            else:
                rows.append(
                    SettingsRow(
                        label="openai-codex: logged out — log in",
                        kind="action",
                        action="login",
                    )
                )
        rows.append(SettingsRow(label="Prompt history", kind="header"))
        enabled = prompt_history_store.enabled
        rows.append(
            SettingsRow(
                label=(
                    "persistent prompt history: "
                    f"{'on' if enabled else 'off'} — toggle"
                ),
                kind="action",
                action="toggle_history",
            )
        )
        rows.append(
            SettingsRow(
                label=(
                    "clear persisted history "
                    f"({len(prompt_history_store.entries())} saved)"
                ),
                kind="action",
                action="clear_history",
            )
        )
        rows.append(
            SettingsRow(
                label=f"in-memory recall this session: {in_memory_depth} prompts",
                kind="status",
            )
        )
        # Display / folding view flags and the thinking-level cycle (Ctrl+O /
        # Ctrl+T / Shift+Tab also drive these). Only meaningful with a live TUI.
        if terminal_ui is not None:
            rows.append(SettingsRow(label="Display", kind="header"))
            rows.append(
                SettingsRow(
                    label=(
                        "tool output: "
                        f"{'expanded' if terminal_ui.tools_expanded else 'collapsed'}"
                        " — toggle (ctrl+o)"
                    ),
                    kind="action",
                    action="toggle_tools",
                )
            )
            rows.append(
                SettingsRow(
                    label=(
                        "thinking blocks: "
                        f"{'hidden' if terminal_ui.thinking_hidden else 'visible'}"
                        " — toggle (ctrl+t)"
                    ),
                    kind="action",
                    action="toggle_thinking",
                )
            )
            level = getattr(state, "thinking_level", None) or "off"
            rows.append(
                SettingsRow(
                    label=f"thinking level: {level} — cycle (shift+tab)",
                    kind="action",
                    action="cycle_thinking",
                )
            )
            active_theme = resolve_active_theme_name(
                env=os.environ, store=NativeThemeStore()
            )
            rows.append(
                SettingsRow(
                    label=f"theme: {active_theme} — change…",
                    kind="action",
                    action="theme",
                )
            )
        if isinstance(state, NativeReplProviderState):
            rows.append(SettingsRow(label="Model cycle", kind="header"))
            rows.append(
                SettingsRow(
                    label="scoped models (Ctrl+P cycle set)…",
                    kind="action",
                    action="scoped_models",
                )
            )
        rows.append(SettingsRow(label="Providers (read-only)", kind="header"))
        for option in state.model_options():
            availability = (
                "available"
                if option.available
                else f"unavailable ({option.reason or 'unknown'})"
            )
            rows.append(
                SettingsRow(
                    label=(
                        f"{sanitize_text(option.selection.reference)} "
                        f"[{availability}]"
                    ),
                    kind="status",
                )
            )
        return rows

    def _model_selector_rows(
        self, state: NativeReplProviderState
    ) -> tuple[list[ModelSelectorOption], list[NativeModelSelection]]:
        """Build the interactive selector rows from the provider-state options.

        Returns the display rows (parallel to ``selections``) and the matching
        ``NativeModelSelection`` list so the caller can map a chosen index back
        to a provider/model reference. A row is selectable only when the
        provider is locally available *and* the built provider advertises
        tool-call support, which tool-loop mode requires. Unavailable or
        non-tool-capable rows stay visible with a reason but are not choosable,
        so the selector never lets a user pick a provider as if it were usable.
        """

        current = state.current_selection()

        def _matches_current(selection: NativeModelSelection) -> bool:
            return (
                selection.provider_name == current.provider_name
                and selection.model_id == current.model_id
            )

        ui_options: list[ModelSelectorOption] = []
        selections: list[NativeModelSelection] = []
        # The active selection may use a non-default model (explicit
        # --native-model or a prior /model <provider>/<custom-model>), which is
        # not present in model_options(). Surface it as the first row so the
        # selector can mark it "(current)" and start the highlight on it. The
        # active provider is tool-capable by the tool-loop invariant, so the row
        # is selectable.
        if not any(_matches_current(option.selection) for option in state.model_options()):
            selections.append(current)
            ui_options.append(
                ModelSelectorOption(
                    label=f"{current.reference}  [available] (current)",
                    selectable=True,
                )
            )
        for option in state.model_options():
            selection = option.selection
            selectable = option.available
            reason = option.reason
            if selectable and not self._selection_supports_tool_calls(
                state, selection
            ):
                selectable = False
                reason = "no tool-call support"
            if selectable:
                status = "available"
            else:
                status = f"unavailable: {reason or 'unknown'}"
            label = f"{selection.reference}  [{status}]"
            if _matches_current(selection):
                label = f"{label} (current)"
            ui_options.append(
                ModelSelectorOption(label=label, selectable=selectable)
            )
            selections.append(selection)
        return ui_options, selections

    @staticmethod
    def _selection_supports_tool_calls(
        state: NativeReplProviderState, selection: NativeModelSelection
    ) -> bool:
        """Return whether the provider for ``selection`` advertises tool calls.

        Builds the provider through the state's factory (cheap, side-effect-free
        construction) only to read ``supports_tool_calls``. Any construction
        failure is treated as "not tool-capable" so a broken selection is never
        offered as choosable.
        """

        # Prefer the catalog-aware construction boundary so a models.json custom
        # provider/model (api: openai-completions) is probed the way it will be
        # used, not via the legacy hardcoded factory.
        builder = getattr(state, "provider_for", None) or getattr(
            state, "provider_factory", None
        )
        if builder is None:
            return False
        try:
            provider = builder(selection)
        except Exception:
            return False
        return bool(getattr(provider, "supports_tool_calls", False))

    @staticmethod
    def _emit_diagnostic(
        terminal_ui: ToolLoopTerminalUi | None,
        error_stream: TextIO,
        message: str,
    ) -> None:
        if terminal_ui is not None:
            terminal_ui.add_notice(message)
            return
        safe_message = "\n".join(
            sanitize_label_text(line) for line in str(message).splitlines()
        )
        print(safe_message, file=error_stream)

    def _copy_last_answer(
        self, messages: list[LoopMessage], *, error_stream: TextIO
    ) -> str:
        """Copy the most recent assistant answer; return a local status line.

        This is a purely local operation: it reads the in-memory conversation,
        copies through the injected clipboard path, and reports what happened.
        It never invokes the provider, tools, login/logout, or model switching.
        """

        answer = self._last_assistant_answer(messages)
        if not answer:
            return "pipy: nothing to copy yet (no assistant answer in this session)."
        result = self.clipboard_copy(answer, terminal_stream=error_stream)
        if result.copied:
            return f"pipy: copied last answer to clipboard ({result.detail})."
        return f"pipy: could not copy last answer — {result.detail}."

    def _run_interactive_session_picker(
        self,
        *,
        session_tree: NativeSessionTree,
        terminal_ui: "ToolLoopTerminalUi",
    ) -> Path | None:
        """Drive the live-TTY ``/resume`` picker over native product sessions.

        Lists the current project's sessions (Tab toggles to all projects),
        offers in-overlay rename/delete (the active session cannot be deleted),
        and returns the chosen native session file or ``None`` on cancel. Runs
        no provider turn and no model-visible tool call.
        """

        session_dir = (
            session_tree.path.parent
            if session_tree.path is not None
            else default_native_session_dir(Path(session_tree.get_header().cwd))
        )
        sessions_root = session_dir.parent
        project_sessions = list_native_sessions(session_dir)
        all_sessions = list_all_native_sessions(sessions_root)

        def on_rename(path: Path, name: str) -> None:
            # Renaming the currently active session must update the live tree so
            # `/session` and the footer reflect the new name immediately; other
            # sessions are renamed through a separately opened tree.
            if session_tree.path is not None and path == session_tree.path:
                session_tree.append_session_info(name)
            else:
                NativeSessionTree.open(path).append_session_info(name)

        def on_delete(path: Path) -> tuple[bool, str]:
            return delete_native_session(path)

        return terminal_ui.run_session_picker(
            project_sessions=project_sessions,
            all_sessions=all_sessions,
            current_path=session_tree.path,
            on_rename=on_rename,
            on_delete=on_delete,
        )

    def _run_interactive_tree_selector(
        self,
        *,
        session_tree: NativeSessionTree,
        terminal_ui: "ToolLoopTerminalUi",
        error_stream: TextIO,
        filter_mode: str,
        rebuild_messages: Callable[[], None],
    ) -> _TreeCommandOutcome:
        """Drive the live-TTY ``/tree`` selector and apply the chosen entry.

        Builds filtered rows for the selector, toggles labels on demand, and on
        Enter applies Pi selection semantics: a user message rehydrates the
        editor for a new branch; any other entry sets the leaf with an empty
        editor. Escape cancels with the tree and leaf unchanged.
        """

        from pipy_harness.native.tui import TreeSelectorRow

        def build_rows(mode: str) -> list[TreeSelectorRow]:
            active_ids = {e.id for e in session_tree.get_branch()}
            rows: list[TreeSelectorRow] = []
            for entry in visible_tree_entries(session_tree, filter_mode=mode):
                rows.append(
                    TreeSelectorRow(
                        entry_id=entry.id,
                        label=entry_preview(session_tree, entry),
                        active=entry.id in active_ids,
                        labeled=session_tree.get_label(entry.id) is not None,
                    )
                )
            return rows

        def on_label_toggle(entry_id: str) -> None:
            existing = session_tree.get_label(entry_id)
            session_tree.append_label_change(
                entry_id, None if existing else "marked"
            )

        chosen = terminal_ui.run_tree_selector(
            build_rows=build_rows,
            filter_modes=FILTER_MODES,
            initial_filter=filter_mode
            if filter_mode in FILTER_MODES
            else "default",
            on_label_toggle=on_label_toggle,
        )
        new_filter = terminal_ui.tree_selector_filter
        if chosen is None:
            self._emit_diagnostic(
                terminal_ui, error_stream, "pipy: /tree cancelled."
            )
            return _TreeCommandOutcome(filter_mode=new_filter)
        selection = apply_tree_selection(session_tree, chosen)
        rebuild_messages()
        if selection.is_noop:
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                "pipy: already at the selected point (no change).",
            )
            return _TreeCommandOutcome(filter_mode=new_filter)
        if selection.is_user_selection:
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                "pipy: selected user message; rehydrating editor for a new "
                "branch.",
            )
            return _TreeCommandOutcome(
                prefill=selection.editor_text, filter_mode=new_filter
            )
        self._emit_diagnostic(
            terminal_ui,
            error_stream,
            f"pipy: continuing from entry {sanitize_label_text(chosen[:8])}.",
        )
        return _TreeCommandOutcome(filter_mode=new_filter)

    def _select_with_branch_summary(
        self,
        *,
        session_tree: NativeSessionTree,
        entry: object,
        directive: str,
        summarizer: Callable[[list[LoopMessage], str | None], str | None],
        rebuild_messages: Callable[[], None],
        terminal_ui: "ToolLoopTerminalUi | None",
        error_stream: TextIO,
    ) -> "_TreeCommandOutcome | None":
        """Record a branch summary while switching branches via ``/tree``.

        Collects the abandoned branch (old leaf back to the common ancestor of
        the target attachment point), summarizes it through the active
        provider, and appends a ``branch_summary`` entry at the attachment
        point, advancing the leaf to it. Returns ``None`` (falling back to a
        plain selection) when there is nothing to summarize or the summary is
        cancelled/fails, leaving the tree and leaf unchanged.
        """

        entry_id = entry.id  # type: ignore[attr-defined]
        old_leaf = session_tree.get_leaf_id()
        attach_parent = branch_summary_attach_parent(session_tree, entry_id)
        abandoned = abandoned_branch_messages(session_tree, old_leaf, attach_parent)
        if not abandoned:
            return None
        focus = directive.split(":", 1)[1] if ":" in directive else None
        summary_text = summarizer(list(abandoned), focus)
        if not summary_text:
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                "pipy: branch summary cancelled; tree and leaf unchanged.",
            )
            return _TreeCommandOutcome()
        session_tree.branch_with_summary(attach_parent, summary_text)
        rebuild_messages()
        editor_text: str | None = None
        message = getattr(entry, "message", None)
        if isinstance(entry, _MessageEntry) and isinstance(message, UserMessage):
            editor_text = message.content
        self._emit_diagnostic(
            terminal_ui,
            error_stream,
            "pipy: recorded branch summary and switched branches.",
        )
        return _TreeCommandOutcome(prefill=editor_text)

    def _handle_tree_command(
        self,
        argument: str,
        *,
        session_tree: NativeSessionTree,
        terminal_ui: "ToolLoopTerminalUi | None",
        error_stream: TextIO,
        repl_input: object,
        filter_mode: str,
        rebuild_messages: Callable[[], None],
        summarizer: Callable[[list[LoopMessage], str | None], str | None]
        | None = None,
    ) -> _TreeCommandOutcome:
        """Handle ``/tree`` and its captured-stream subcommands.

        This runs no model-visible tool call. With no argument it prints the
        current session tree (a live-TTY interactive selector is layered on in
        the TUI). The ``select``/``label``/``filter`` subcommands give
        captured-stream callers and scripts a deterministic way to drive Pi
        ``/tree`` selection semantics without a TTY. Appending ``summarize`` (or
        ``summarize:<focus>``) to ``select`` records a branch summary of the
        abandoned branch through the active provider before switching.
        """

        parts = argument.split(maxsplit=1)
        if not parts:
            if terminal_ui is not None and hasattr(
                terminal_ui, "run_tree_selector"
            ):
                return self._run_interactive_tree_selector(
                    session_tree=session_tree,
                    terminal_ui=terminal_ui,
                    error_stream=error_stream,
                    filter_mode=filter_mode,
                    rebuild_messages=rebuild_messages,
                )
            for line in render_tree_lines(session_tree, filter_mode=filter_mode):
                self._emit_diagnostic(terminal_ui, error_stream, line)
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                "pipy: use '/tree select <n|id>' to move, "
                "'/tree label <n|id> [text]' to (un)label, "
                "'/tree filter <mode>' to filter.",
            )
            return _TreeCommandOutcome()

        sub = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub == "select":
            select_tokens = rest.split()
            ref = select_tokens[0] if select_tokens else ""
            summarize_directive: str | None = None
            for token in select_tokens[1:]:
                if token == "summarize" or token.startswith("summarize:"):
                    summarize_directive = token
            entry = resolve_entry_ref(session_tree, ref, filter_mode=filter_mode)
            if entry is None:
                self._emit_diagnostic(
                    terminal_ui,
                    error_stream,
                    f"pipy: no tree entry matched {ref!r}.",
                )
                return _TreeCommandOutcome()
            if summarize_directive is not None and summarizer is not None:
                summary_outcome = self._select_with_branch_summary(
                    session_tree=session_tree,
                    entry=entry,
                    directive=summarize_directive,
                    summarizer=summarizer,
                    rebuild_messages=rebuild_messages,
                    terminal_ui=terminal_ui,
                    error_stream=error_stream,
                )
                if summary_outcome is not None:
                    return summary_outcome
            selection = apply_tree_selection(session_tree, entry.id)
            rebuild_messages()
            if selection.is_noop:
                self._emit_diagnostic(
                    terminal_ui,
                    error_stream,
                    "pipy: already at the selected point (no change).",
                )
                return _TreeCommandOutcome()
            if selection.is_user_selection:
                self._emit_diagnostic(
                    terminal_ui,
                    error_stream,
                    "pipy: selected user message; rehydrating editor for a "
                    "new branch.",
                )
                return _TreeCommandOutcome(prefill=selection.editor_text)
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                f"pipy: continuing from entry {sanitize_label_text(entry.id[:8])}.",
            )
            return _TreeCommandOutcome()

        if sub == "label":
            label_parts = rest.split(maxsplit=1)
            if not label_parts:
                self._emit_diagnostic(
                    terminal_ui,
                    error_stream,
                    "pipy: usage: /tree label <n|id> [text]",
                )
                return _TreeCommandOutcome()
            entry = resolve_entry_ref(
                session_tree, label_parts[0], filter_mode=filter_mode
            )
            if entry is None:
                self._emit_diagnostic(
                    terminal_ui,
                    error_stream,
                    f"pipy: no tree entry matched {label_parts[0]!r}.",
                )
                return _TreeCommandOutcome()
            label_text = label_parts[1].strip() if len(label_parts) > 1 else ""
            session_tree.append_label_change(entry.id, label_text or None)
            self._emit_diagnostic(
                terminal_ui,
                error_stream,
                (
                    f"pipy: labeled {sanitize_label_text(entry.id[:8])} {label_text!r}."
                    if label_text
                    else f"pipy: cleared label on {sanitize_label_text(entry.id[:8])}."
                ),
            )
            return _TreeCommandOutcome()

        if sub == "filter":
            mode = rest.lower()
            if mode not in FILTER_MODES:
                self._emit_diagnostic(
                    terminal_ui,
                    error_stream,
                    "pipy: filter must be one of " + ", ".join(FILTER_MODES),
                )
                return _TreeCommandOutcome()
            self._emit_diagnostic(
                terminal_ui, error_stream, f"pipy: /tree filter set to {mode}."
            )
            return _TreeCommandOutcome(filter_mode=mode)

        self._emit_diagnostic(
            terminal_ui,
            error_stream,
            f"pipy: unknown /tree subcommand {sub!r}; "
            "use select, label, or filter.",
        )
        return _TreeCommandOutcome()

    @staticmethod
    def _last_assistant_answer(messages: list[LoopMessage]) -> str:
        for message in reversed(messages):
            if isinstance(message, AssistantMessage):
                content = message.content.strip()
                if content:
                    return message.content
        return ""

    def _invoke_interruptible(
        self,
        *,
        call: ProviderToolCall,
        context: ToolContext,
        registry: dict[str, ToolPort] | None = None,
        terminal_ui: ToolLoopTerminalUi | None = None,
    ) -> tuple[ToolResultMessage, str]:
        """Invoke a tool while the live TUI can submit local commands.

        Model-driven bash tools run tests/builds on the tool loop's main path.
        Run the tool on a worker while the foreground thread watches the
        terminal, so `/quit`/`!…` submitted during live tool output can cancel
        the tool and dispatch locally instead of waiting for the command to
        finish. Tools receive a cancel event via ``ToolContext``; the bash tool
        observes it by killing its process group.
        """

        if terminal_ui is None:
            return self._invoke(call=call, context=context, registry=registry), TURN_SETTLED

        cancel_event = threading.Event()
        done_event = threading.Event()
        result_holder: list[ToolResultMessage] = []
        error_holder: list[BaseException] = []
        cancellable_context = replace(context, cancel_event=cancel_event)

        def _worker() -> None:
            try:
                result_holder.append(
                    self._invoke(
                        call=call, context=cancellable_context, registry=registry
                    )
                )
            except BaseException as exc:  # pragma: no cover - re-raised below
                error_holder.append(exc)
            finally:
                done_event.set()

        worker = threading.Thread(target=_worker, name="pipy-tool-call", daemon=True)
        worker.start()
        try:
            outcome = terminal_ui.wait_for_active_turn_interrupt(
                done_event, cancel_event, accept_commands=True
            )
        except KeyboardInterrupt:
            cancel_event.set()
            outcome = TURN_ABORTED
        if outcome in {TURN_ABORTED, TURN_LOCAL_COMMAND}:
            worker.join(timeout=self._CANCEL_JOIN_TIMEOUT_SECONDS)
            if not result_holder:
                label = (
                    "local command" if outcome == TURN_LOCAL_COMMAND else "escape"
                )
                # The user interrupted this tool; if the worker also raised at
                # the same time, preserve the user-visible cancellation outcome
                # and keep provider tool-result history balanced.
                return (
                    self._error_observation(
                        call=call, output_text=f"tool cancelled by {label}"
                    ),
                    outcome,
                )
        worker.join()
        if error_holder:
            raise error_holder[0]
        if result_holder:
            return result_holder[0], outcome
        return (
            self._error_observation(call=call, output_text="tool cancelled"),
            outcome,
        )

    def _invoke(
        self,
        *,
        call: ProviderToolCall,
        context: ToolContext,
        registry: dict[str, ToolPort] | None = None,
    ) -> ToolResultMessage:
        tool = (registry if registry is not None else self.tool_registry).get(
            call.tool_name
        )
        if tool is None:
            return self._error_observation(
                call=call,
                output_text=f"unknown tool: {call.tool_name}",
            )
        try:
            raw_args = json.loads(call.arguments_json)
        except json.JSONDecodeError as exc:
            return self._error_observation(
                call=call,
                output_text=f"invalid arguments JSON: {exc.msg}",
            )
        try:
            validated = validate_arguments(
                tool_name=call.tool_name,
                schema=tool.definition.input_schema,
                arguments=raw_args,
            )
        except ToolArgumentError as exc:
            return self._error_observation(call=call, output_text=str(exc))

        request_id = make_tool_request_id()
        tool_request = ToolRequest(
            tool_request_id=request_id,
            tool_name=call.tool_name,
            arguments=validated,
            provider_correlation_id=call.provider_correlation_id,
        )
        try:
            execution_result = tool.invoke(tool_request, context)
        except ToolArgumentError as exc:
            return self._error_observation(call=call, output_text=str(exc))

        if not isinstance(execution_result, ToolExecutionResult):
            raise TypeError(
                f"tool {call.tool_name!r} returned non-ToolExecutionResult value"
            )
        return ToolResultMessage(
            tool_request_id=execution_result.tool_request_id,
            output_text=execution_result.output_text,
            is_error=execution_result.is_error,
            provider_correlation_id=execution_result.provider_correlation_id,
        )

    @staticmethod
    def _error_observation(
        *,
        call: ProviderToolCall,
        output_text: str,
    ) -> ToolResultMessage:
        return ToolResultMessage(
            tool_request_id=make_tool_request_id(),
            output_text=output_text,
            is_error=True,
            provider_correlation_id=call.provider_correlation_id,
        )


class _ToolLoopRenderer:
    """Pi-parity live rendering for the bounded tool loop.

    Streams provider text deltas to ``error_stream`` as they arrive, then
    paints a styled header/body block around each tool invocation. Falls
    back to plain text on non-TTY streams or when ``NO_COLOR`` is set,
    so captured logs stay deterministic and tests can pin behavior.

    Style intent:
    - Streamed assistant text: dim cyan italic prefix `assistant >`, then
      raw deltas printed verbatim (the provider already shapes the text).
    - Tool call header: italic green prefix `→ <tool>(<arg-preview>)`.
    - Tool result body: dim/quiet block prefixed with `↳`, indented two
      spaces per line, with a leading `[error]` tag on failures.

    The renderer exposes ``streamed_any`` so the loop can avoid double-
    printing the final buffered text when streaming already covered it.
    """

    _ANSI_BOLD = "\x1b[1m"
    _ANSI_DIM = "\x1b[2m"
    _ANSI_ITALIC = "\x1b[3m"
    _ANSI_GREEN = "\x1b[32m"
    _ANSI_RED = "\x1b[31m"
    _ANSI_CYAN = "\x1b[36m"
    _ANSI_YELLOW = "\x1b[33m"
    _ANSI_RESET = "\x1b[0m"
    # Pi's `toolPendingBg` theme uses a *very* muted dark-olive panel
    # behind each tool block — almost a gray with a hint of green, not
    # a saturated forest green. We pin the same intent with a truecolor
    # RGB triplet (`\x1b[48;2;28;42;30m`) on terminals that advertise
    # 24-bit color, falling back to 256-color index 235 (a near-black
    # gray) when truecolor is unavailable. `\x1b[K` fills the rest of
    # the row with the same background so each panel row reads as a
    # contiguous strip.
    _ANSI_BG_TOOL_PANEL_TRUECOLOR = "\x1b[48;2;28;42;30m"
    _ANSI_BG_TOOL_PANEL_256 = "\x1b[48;5;235m"
    # Pi's `userMessageBg` theme paints a muted slate-gray panel
    # spanning the full row behind the user's typed message so it
    # reads as a chat bubble distinct from the green tool panel. The
    # bubble is three rows tall: one blank padding row above the text,
    # the text row itself, and one blank padding row below — mirror
    # pi by emitting all three with the same background and
    # `\x1b[K` clear-to-EOL.
    _ANSI_BG_USER_MESSAGE_TRUECOLOR = "\x1b[48;2;52;53;65m"
    _ANSI_BG_USER_MESSAGE_256 = "\x1b[48;5;237m"
    _ANSI_CLEAR_EOL = "\x1b[K"
    _ANSI_CURSOR_UP_ONE = "\x1b[1A"
    _ANSI_CLEAR_LINE = "\x1b[2K"

    _RESULT_LINE_PREVIEW_MAX_LENGTH = 12
    _ARGUMENT_VALUE_PREVIEW_LIMIT = 80

    def __init__(
        self,
        *,
        output_stream: TextIO,
        error_stream: TextIO,
        tool_renderers: "Mapping[str, ExtensionTool] | None" = None,
        render_details_sink: "MutableMapping[str, object] | None" = None,
    ) -> None:
        self._output_stream = output_stream
        self._error_stream = error_stream
        self._terminal_lock = threading.Lock()
        self._cursor_control_enabled = self._compute_cursor_control_enabled(error_stream)
        self._enabled = self._compute_enabled(error_stream)
        self._tool_panel_bg = (
            self._ANSI_BG_TOOL_PANEL_TRUECOLOR
            if self._supports_truecolor()
            else self._ANSI_BG_TOOL_PANEL_256
        )
        self._user_message_bg = (
            self._ANSI_BG_USER_MESSAGE_TRUECOLOR
            if self._supports_truecolor()
            else self._ANSI_BG_USER_MESSAGE_256
        )
        self._stream_active = False
        self._stream_emitted_any = False
        self._streamed_any = False
        self._working_shown = False
        self._working_mode = ""
        self._stop_working_event: threading.Event | None = None
        self._working_thread: threading.Thread | None = None
        self._reasoning_active = False
        self._reasoning_emitted_any = False
        self._tool_renderers = dict(tool_renderers or {})
        self._render_details_sink = render_details_sink
        self._pending_render: dict[str, object] | None = None
        self._last_tool_name = ""

    @staticmethod
    def _compute_enabled(stream: TextIO) -> bool:
        if "NO_COLOR" in os.environ:
            return False
        term = os.environ.get("TERM", "").lower()
        if term == "dumb":
            return False
        return bool(getattr(stream, "isatty", lambda: False)())

    @staticmethod
    def _compute_cursor_control_enabled(stream: TextIO) -> bool:
        term = os.environ.get("TERM", "").lower()
        if term == "dumb":
            return False
        return bool(getattr(stream, "isatty", lambda: False)())

    @staticmethod
    def _supports_truecolor() -> bool:
        """Return True when the active terminal advertises 24-bit color.

        Truecolor lets us pin Pi's exact muted-olive panel RGB. Falls
        back to a 256-color near-black on TERM strings that only carry
        eight, sixteen, or 256 color slots. RGB is used only when
        COLORTERM or TERM explicitly advertises truecolor/direct color.
        """

        return terminal_supports_truecolor(
            os.environ.get("TERM", ""), os.environ.get("COLORTERM", "")
        )

    @property
    def streamed_any(self) -> bool:
        return self._streamed_any

    @property
    def stream_sink(self) -> StreamChunkSink:
        return self._handle_stream_chunk

    def begin_provider_turn(self) -> None:
        self._close_reasoning()
        self._stream_active = False
        self._stream_emitted_any = False
        self._working_shown = False
        self._working_mode = ""
        self._reasoning_emitted_any = False

    @property
    def reasoning_sink(self) -> StreamChunkSink:
        return self.handle_reasoning_chunk

    _SPINNER_FRAMES: ClassVar[tuple[str, ...]] = (
        "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏",
    )
    _SPINNER_INTERVAL_SECONDS: ClassVar[float] = 0.08

    def show_working(self) -> None:
        """Animate a Pi-shape `⠋ Working...` line on the error stream.

        A background thread cycles through ``_SPINNER_FRAMES`` every
        80 ms and rewrites the line in place. The visible loader sits one
        row below the post-user-message cursor, matching Pi's active-turn
        spacing, while the terminal cursor returns to the row where streamed
        assistant text should begin. The thread is daemonized so it never
        blocks process exit, and stopped via ``_stop_working_event`` before
        the next visible block (stream text, tool block, or footer redraw)
        lands. On non-TTY streams the line and animation are suppressed
        entirely so captured logs stay deterministic.
        """

        if not self._enabled:
            self._working_shown = False
            return
        self._start_working_animation(mode="reserved")

    def _show_stream_working(self) -> None:
        if not self._enabled:
            self._working_shown = False
            return
        self._start_working_animation(mode="stream")

    def _start_working_animation(self, *, mode: str) -> None:
        self._stop_working_event = threading.Event()
        self._working_shown = True
        self._working_mode = mode

        def _animate(stop_event: threading.Event) -> None:
            frame_index = 0
            while not stop_event.is_set():
                glyph = self._SPINNER_FRAMES[
                    frame_index % len(self._SPINNER_FRAMES)
                ]
                marker = self._style(
                    f"{glyph} Working...",
                    self._ANSI_DIM,
                )
                try:
                    with self._terminal_lock:
                        self._error_stream.write(self._working_frame(marker, mode))
                        self._error_stream.flush()
                except (ValueError, OSError):
                    return
                frame_index += 1
                stop_event.wait(self._SPINNER_INTERVAL_SECONDS)

        thread = threading.Thread(
            target=_animate,
            args=(self._stop_working_event,),
            name="pipy-tool-loop-spinner",
            daemon=True,
        )
        self._working_thread = thread
        thread.start()

    @staticmethod
    def _working_frame(marker: str, mode: str) -> str:
        if mode == "stream":
            return f"\x1b7\x1b[2B\r\x1b[K {marker}\x1b8"
        return f"\x1b7\x1b[1B\r\x1b[K {marker}\x1b8"

    @staticmethod
    def _working_clear(mode: str) -> str:
        if mode == "stream":
            return "\x1b7\x1b[2B\r\x1b[K\x1b8"
        return "\x1b7\x1b[1B\r\x1b[K\x1b8"

    def _clear_working(self) -> None:
        if not self._working_shown:
            return
        mode = self._working_mode
        if self._stop_working_event is not None:
            self._stop_working_event.set()
        if self._working_thread is not None:
            self._working_thread.join(timeout=0.2)
        self._stop_working_event = None
        self._working_thread = None
        if self._enabled:
            try:
                with self._terminal_lock:
                    self._error_stream.write(self._working_clear(mode))
                    self._error_stream.flush()
            except (ValueError, OSError):
                pass
        self._working_shown = False
        self._working_mode = ""

    def end_provider_turn(
        self, *, final_text: str, has_tool_calls: bool
    ) -> None:
        del has_tool_calls
        self._clear_working()
        if self._stream_active:
            # Flush a trailing newline so the next render block starts
            # on its own line, even when the provider did not emit one,
            # then a second one so a blank row sits between the last
            # response line and the next input-frame separator, matching
            # pi's spacing below the assistant message.
            if not self._stream_emitted_any or not final_text.endswith("\n"):
                self._output_stream.write("\n\n")
            else:
                self._output_stream.write("\n")
            self._output_stream.flush()
        self._stream_active = False

    def abort_provider_turn(self) -> None:
        self._clear_working()
        if self._enabled:
            message = self._style(" Operation aborted", "\x1b[38;2;204;102;102m")
            try:
                with self._terminal_lock:
                    self._error_stream.write(f"\n{message}\n")
                    self._error_stream.flush()
            except (ValueError, OSError):
                pass
        else:
            print("Operation aborted", file=self._error_stream)
        self._stream_active = False

    def steer_provider_turn(self) -> None:
        # Captured-stream callers do not accept mid-turn input, so steering does
        # not occur here; provided for interface parity with the TUI renderer.
        self._clear_working()
        self._stream_active = False

    def _handle_stream_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        if not self._stream_active:
            self._clear_working()
            self._stream_active = True
            # Pi prints the final assistant answer with a one-space
            # left indent and a single blank row above. The bottom
            # padding row of the user-message bubble already provides
            # one of the two visual rows between the bubble text and
            # the answer; emit one more `\n` plus the leading indent
            # here. Subsequent lines within the same stream get their
            # indent from the newline rewrite below.
            with self._terminal_lock:
                self._output_stream.write("\n ")
                self._output_stream.write(chunk.replace("\n", "\n "))
                self._output_stream.flush()
            self._show_stream_working()
        else:
            with self._terminal_lock:
                self._output_stream.write(chunk.replace("\n", "\n "))
                self._output_stream.flush()
        self._stream_emitted_any = True
        self._streamed_any = True

    def handle_reasoning_chunk(self, chunk: str) -> None:
        """Render an italic dim reasoning-summary delta inline.

        Pi paints the model's reasoning summary between tool calls with
        an italicized prose voice and renders section titles in bold.
        Pipy mirrors that by routing the codex
        `response.reasoning_summary_text.delta` events through this
        method so the user sees the same "thinking" cues. ``**...**``
        spans inside the chunk are rendered as ANSI bold+italic so
        section titles like `**Investigating pi-mono and pipy**`
        appear as bold prose instead of literal asterisks.
        """

        if not chunk:
            return
        self._clear_working()
        if not self._reasoning_active:
            self._reasoning_active = True
            indent = self._style(" ", self._ANSI_DIM)
            self._error_stream.write("\n" + indent)
        for segment, is_bold in self._split_reasoning_segments(chunk):
            if not segment:
                continue
            if is_bold:
                styled = self._style(
                    segment, self._ANSI_BOLD + self._ANSI_ITALIC + self._ANSI_DIM
                )
            else:
                styled = self._style(
                    segment, self._ANSI_ITALIC + self._ANSI_DIM
                )
            self._error_stream.write(styled)
        self._error_stream.flush()
        self._reasoning_emitted_any = True

    @staticmethod
    def _split_reasoning_segments(text: str) -> list[tuple[str, bool]]:
        """Split a reasoning chunk into (segment, is_bold) pairs.

        ``**…**`` spans become bold segments; the literal asterisks are
        removed from the rendered output. Unmatched trailing ``**`` is
        emitted verbatim so partial deltas across chunk boundaries do
        not silently drop the open marker.
        """

        segments: list[tuple[str, bool]] = []
        cursor = 0
        while True:
            open_index = text.find("**", cursor)
            if open_index == -1:
                segments.append((text[cursor:], False))
                break
            if open_index > cursor:
                segments.append((text[cursor:open_index], False))
            close_index = text.find("**", open_index + 2)
            if close_index == -1:
                segments.append((text[open_index + 2 :], True))
                break
            segments.append((text[open_index + 2 : close_index], True))
            cursor = close_index + 2
        return segments

    def _close_reasoning(self) -> None:
        if not self._reasoning_active:
            return
        self._error_stream.write("\n")
        self._error_stream.flush()
        self._reasoning_active = False

    def render_user_message(self, text: str) -> None:
        """Paint the submitted user message on the user-message panel.

        Pi's user-message bubble is three rows tall and fills the row
        width: a blank padding row above the text, the text row, and a
        blank padding row below — all painted on the same
        ``userMessageBg`` background. The readline / slash-menu adapter
        has already echoed the typed text to the error stream; we
        overwrite that previous line plus the `print_input_separator`
        row above with `\\x1b[1A\\x1b[2K\\r` and re-render the bubble in
        place. Non-TTY streams skip the rewrite and just leave the
        readline echo in place.
        """

        if not text:
            return
        lines = text.splitlines() or [""]
        if self._cursor_control_enabled:
            # Step back over the readline echo plus the separator row
            # that `print_input_separator` drew above the input area.
            # The readline echo of a single logical line can wrap to
            # multiple visual rows on narrow panes (`ceil(len /
            # width)`), so count visual rows — not logical lines —
            # before clearing, otherwise stale echo fragments stay
            # above the rendered bubble.
            width = max(1, chrome_width(self._error_stream))
            visual_rows = 0
            for line in lines:
                # `len(line) + 1` accounts for the leading prompt-area
                # column pi-parity already reserves; `// width` plus
                # the always-present row itself gives the wrapped
                # count, with empty lines counting as one row.
                effective = max(1, len(line) + 1)
                visual_rows += (effective + width - 1) // width
            self._error_stream.write("\r")
            for _ in range(visual_rows + 1):
                self._error_stream.write(self._ANSI_CURSOR_UP_ONE + self._ANSI_CLEAR_LINE)
            self._error_stream.write("\r")
            # Top padding row of the bubble (full-width bg).
            self._error_stream.write(self._user_message_panel_blank_line())
        for line in lines:
            self._error_stream.write(self._user_message_panel_line(line))
        if self._cursor_control_enabled:
            # Bottom padding row of the bubble (full-width bg).
            self._error_stream.write(self._user_message_panel_blank_line())
        self._error_stream.flush()

    def _user_message_panel_line(self, text: str) -> str:
        """Render the text row of the user-message bubble."""

        if not self._enabled:
            return f" {text}\n"
        # Full-width bg behind the text row. We pad with spaces out to
        # the rendered chrome width instead of relying solely on
        # `\x1b[K` because `tmux capture-pane -e` drops cells that
        # carry attributes but no character — without explicit space
        # characters the bg disappears in screenshots and replay.
        width = chrome_width(self._error_stream)
        padding = " " * max(0, width - len(text) - 1)
        return (
            f"{self._user_message_bg} {text}{padding}{self._ANSI_CLEAR_EOL}"
            f"{self._ANSI_RESET}\n"
        )

    def _user_message_panel_blank_line(self) -> str:
        """Render an empty padding row in the user-message bubble.

        Filled with spaces (not just `\\x1b[K`) so tmux/screenshot
        replays still see the bg on every cell of the row — empty bg
        cells get dropped by `tmux capture-pane`.
        """

        if not self._enabled:
            return "\n"
        width = chrome_width(self._error_stream)
        padding = " " * width
        return (
            f"{self._user_message_bg}{padding}{self._ANSI_CLEAR_EOL}"
            f"{self._ANSI_RESET}\n"
        )

    def render_tool_call(self, call: ProviderToolCall) -> None:
        self._clear_working()
        self._close_reasoning()
        self._last_tool_name = call.tool_name
        self._pending_render = None
        tool = self._tool_renderers.get(call.tool_name)
        if tool is not None:
            args = _parse_tool_input(call.arguments_json)
            state: dict[str, object] = {}
            self._pending_render = {
                "corr": call.provider_correlation_id, "args": args, "state": state,
            }
            if tool.render_call is not None:
                lines = self._dispatch_render(tool.render_call, args, state,
                                              is_result=False, content=None,
                                              details=None, is_error=False)
                if lines is not None:
                    self._error_stream.write(self._tool_panel_blank_line())
                    for line in lines:
                        self._error_stream.write(self._tool_panel_line(line))
                    self._error_stream.write(self._tool_panel_blank_line())
                    self._error_stream.flush()
                    return
        # --- existing default body ---
        self._error_stream.write(self._tool_panel_blank_line())
        rendered = self._format_pi_call_header_rich(
            call.tool_name, call.arguments_json
        )
        self._error_stream.write(self._tool_panel_rich_line(rendered))
        self._error_stream.write(self._tool_panel_blank_line())
        self._error_stream.flush()

    def tool_output_sink(self, chunk: str) -> None:
        # Stream long-running tool output (e.g. pytest dots) live in the
        # captured/plain renderer, mirroring the TUI live region.
        if not chunk:
            return
        try:
            with self._terminal_lock:
                self._error_stream.write(chunk)
                self._error_stream.flush()
        except (ValueError, OSError):
            pass

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
            tool = self._tool_renderers.get(self._last_tool_name)
            if tool is not None and tool.render_result is not None:
                details = None
                if self._render_details_sink is not None:
                    details = self._render_details_sink.pop(str(pending["corr"]), None)
                lines = self._dispatch_render(
                    tool.render_result, pending["args"], pending["state"],
                    is_result=True, content=output_text, details=details,
                    is_error=is_error,
                )
                if lines is not None:
                    for line in lines:
                        self._error_stream.write(self._tool_panel_line(line))
                    if duration_seconds is not None:
                        self._error_stream.write(self._tool_panel_blank_line())
                        self._error_stream.write(self._tool_panel_line(
                            f"Took {duration_seconds:.1f}s", style=self._ANSI_DIM))
                    self._error_stream.write(self._tool_panel_blank_line())
                    self._error_stream.flush()
                    return
        # --- existing default body ---
        lines = output_text.splitlines() or [""]
        preview_lines = lines[: self._RESULT_LINE_PREVIEW_MAX_LENGTH]
        earlier = len(lines) - len(preview_lines)
        if earlier > 0:
            self._error_stream.write(
                self._tool_panel_line(
                    f"... ({earlier} earlier lines, ctrl+o to expand)",
                    style=self._ANSI_DIM,
                )
            )
            tail_preview = lines[-self._RESULT_LINE_PREVIEW_MAX_LENGTH :]
        else:
            tail_preview = preview_lines
        for line in tail_preview:
            self._error_stream.write(
                self._tool_panel_line(line, style=self._ANSI_DIM)
            )
        if is_error:
            self._error_stream.write(
                self._tool_panel_line(
                    "[error] tool reported a failure",
                    style=self._ANSI_RED + self._ANSI_DIM,
                )
            )
        # Pi keeps the `Took {n}s` caption inside the panel so the
        # block reads as one contiguous strip. Emit a blank panel row
        # for breathing room, then the duration, then a final blank
        # panel row before the next block starts.
        if duration_seconds is not None:
            self._error_stream.write(self._tool_panel_blank_line())
            self._error_stream.write(
                self._tool_panel_line(
                    f"Took {duration_seconds:.1f}s",
                    style=self._ANSI_DIM,
                )
            )
        self._error_stream.write(self._tool_panel_blank_line())
        self._error_stream.flush()

    def _dispatch_render(self, renderer, args, state, *, is_result, content,
                         details, is_error):
        # Local import: the render-theme machinery is only needed on the rarely
        # hit custom-renderer branch, so keep it off this module's hot import path.
        from pipy_harness.native.chrome import chrome_style_for
        from pipy_harness.native.tool_renderers import (
            build_tool_render_theme,
            render_tool_phase,
        )
        from pipy_harness.extensions import ToolRenderContext

        style = chrome_style_for(self._error_stream)
        ctx = ToolRenderContext(
            tool_name=self._last_tool_name, args=args, is_result=is_result,
            is_error=is_error, content=content, details=details,
            expanded=False, width=80,
            theme=build_tool_render_theme(style), state=state,
        )
        return render_tool_phase(renderer, ctx)

    def _tool_panel_line(
        self,
        text: str,
        *,
        style: str = "",
        bold: bool = False,
    ) -> str:
        """Render one row of a tool block inside the dark-green panel.

        Pads with a leading space (matches Pi's column gutter), applies
        the supplied style on top of the panel background, then writes
        `\\x1b[K` to fill the remainder of the row with the same
        background before resetting. On non-TTY streams the helper
        falls back to plain text with the leading space so captured
        logs stay readable.
        """

        if not self._enabled:
            return f" {text}\n"
        prefix = self._tool_panel_bg
        weight = self._ANSI_BOLD if bold else ""
        return f"{prefix}{weight}{style} {text}{self._ANSI_CLEAR_EOL}{self._ANSI_RESET}\n"

    def _tool_panel_blank_line(self) -> str:
        """Emit an empty row of the dark-green panel (spacing inside the block)."""

        if not self._enabled:
            return "\n"
        return f"{self._tool_panel_bg}{self._ANSI_CLEAR_EOL}{self._ANSI_RESET}\n"

    def _tool_panel_rich_line(
        self, segments: list[tuple[str, str]]
    ) -> str:
        """Render a multi-style row inside the dark-green panel.

        ``segments`` is an ordered sequence of ``(text, ansi_style)``
        pairs. Each segment is wrapped with its own ANSI weight/color
        on top of the panel background. The trailing `\\x1b[K` fills
        the rest of the row so the panel reads as a contiguous strip.
        On non-TTY streams the helper concatenates the text segments
        plain (no escapes) so captured logs stay readable.
        """

        if not self._enabled:
            return " " + "".join(text for text, _ in segments) + "\n"
        parts = [self._tool_panel_bg, " "]
        for text, style in segments:
            if style:
                parts.append(style)
                parts.append(text)
                parts.append(self._ANSI_RESET)
                parts.append(self._tool_panel_bg)
            else:
                parts.append(text)
        parts.append(self._ANSI_CLEAR_EOL)
        parts.append(self._ANSI_RESET)
        parts.append("\n")
        return "".join(parts)

    @staticmethod
    def _read_range_label(data: Mapping[str, Any]) -> str:
        """Format the ``:start-end`` line range for a ``read`` header.

        Pi's read tool natively exposes ``offset`` and ``limit`` style
        arguments. Pipy's bounded `read` tool uses a fixed line cap, but
        the codex provider may still emit the optional ``offset`` and
        ``limit`` properties that other read tools advertise. When
        present they shape the header label so the user sees the
        actual requested range; otherwise the default ``:1-200``
        matches the tool's hard-coded ``line_limit``.
        """

        start = data.get("offset")
        limit = data.get("limit")
        if isinstance(start, int) and start >= 0:
            start_line = start + 1
        else:
            start_line = 1
        if isinstance(limit, int) and limit > 0:
            end_line = start_line + limit - 1
        else:
            end_line = start_line + 199
        return f":{start_line}-{end_line}"

    def _format_pi_call_header_rich(
        self, tool_name: str, arguments_json: str
    ) -> list[tuple[str, str]]:
        """Return a list of (text, style) segments for a tool-call header.

        Pi styles the header per-segment: the verb (e.g. `read`,
        `ls`, `grep`) is bold white, the operand (path/pattern) is
        plain dim white, and the line range (`:1-200`) is yellow.
        We reproduce that by emitting separate text+style pairs,
        which `_tool_panel_rich_line` joins back into one panel row
        with each segment carrying its own ANSI weight/color while
        sharing the panel background.
        """

        try:
            data = json.loads(arguments_json)
        except (json.JSONDecodeError, ValueError):
            data = None
        if not isinstance(data, dict):
            data = {}
        bold = self._ANSI_BOLD
        plain = ""
        yellow = self._ANSI_YELLOW
        if tool_name == "read":
            path = str(data.get("path", ""))
            verb = "read resource" if path.startswith("/") else "read"
            range_label = self._read_range_label(data)
            return [
                (verb, bold),
                (" ", plain),
                (path, plain),
                (range_label, yellow),
            ]
        if tool_name == "ls":
            return [
                ("ls", bold),
                (" ", plain),
                (str(data.get("path", ".")), plain),
            ]
        if tool_name == "grep":
            return [
                ("grep", bold),
                (" ", plain),
                (f'"{data.get("pattern", "")}"', plain),
                (" ", plain),
                (str(data.get("path", ".")), plain),
            ]
        if tool_name == "find":
            return [
                ("find", bold),
                (" ", plain),
                (f'"{data.get("pattern", "")}"', plain),
                (" ", plain),
                (str(data.get("path", ".")), plain),
            ]
        if tool_name in {"write", "edit", "edit_diff"}:
            return [
                (tool_name, bold),
                (" ", plain),
                (str(data.get("path", "")), plain),
            ]
        if tool_name == "truncate":
            return [("truncate", bold)]
        preview = self._argument_preview(arguments_json)
        return [(f"{tool_name}({preview})", bold)]

    def _format_pi_call_header(self, tool_name: str, arguments_json: str) -> str:
        """Render a Pi-shape one-line tool header.

        Built-in read/ls/grep/find/write/edit tools render as Pi-style
        compact lines: ``read path:1-line_limit``, ``ls path``,
        ``grep "pattern" path``, ``find "pattern" path``. Unknown tools
        fall back to a ``name(args)`` form so the user can still see the
        invocation.
        """

        try:
            data = json.loads(arguments_json)
        except (json.JSONDecodeError, ValueError):
            data = None
        if not isinstance(data, dict):
            data = {}
        if tool_name == "read":
            path = data.get("path", "")
            prefix = "read resource" if str(path).startswith("/") else "read"
            range_label = self._read_range_label(data)
            return f"{prefix} {path}{range_label} (ctrl+o to expand)"
        if tool_name == "ls":
            path = data.get("path", ".")
            return f"ls {path}"
        if tool_name == "grep":
            pattern = data.get("pattern", "")
            path = data.get("path", ".")
            return f'grep "{pattern}" {path}'
        if tool_name == "find":
            pattern = data.get("pattern", "")
            path = data.get("path", ".")
            return f'find "{pattern}" {path}'
        if tool_name == "write":
            path = data.get("path", "")
            return f"write {path}"
        if tool_name == "edit":
            path = data.get("path", "")
            return f"edit {path}"
        if tool_name == "edit_diff":
            path = data.get("path", "")
            return f"edit_diff {path}"
        if tool_name == "truncate":
            return "truncate"
        preview = self._argument_preview(arguments_json)
        return f"{tool_name}({preview})"

    def _argument_preview(self, arguments_json: str) -> str:
        try:
            data = json.loads(arguments_json)
        except (json.JSONDecodeError, ValueError):
            preview = arguments_json.strip()
            if len(preview) > self._ARGUMENT_VALUE_PREVIEW_LIMIT:
                preview = preview[: self._ARGUMENT_VALUE_PREVIEW_LIMIT] + "…"
            return preview
        if not isinstance(data, dict):
            return ""
        pieces: list[str] = []
        for key, value in data.items():
            if isinstance(value, str):
                value_repr = value
                if len(value_repr) > self._ARGUMENT_VALUE_PREVIEW_LIMIT:
                    value_repr = value_repr[: self._ARGUMENT_VALUE_PREVIEW_LIMIT] + "…"
                pieces.append(f'{key}="{value_repr}"')
            elif isinstance(value, (int, float, bool)) or value is None:
                pieces.append(f"{key}={value}")
            else:
                pieces.append(f"{key}=…")
        return ", ".join(pieces)

    def _style(self, text: str, code: str) -> str:
        if not self._enabled:
            return text
        return f"{code}{text}{self._ANSI_RESET}"


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
        render_details_sink: MutableMapping[str, object] | None = None,
    ) -> None:
        self._ui = ui
        self._streamed_any = False
        self._stop_working_event: threading.Event | None = None
        self._working_thread: threading.Thread | None = None
        self._last_tool_name = ""
        self._tool_renderers = dict(tool_renderers or {})
        self._render_details_sink = render_details_sink
        self._pending_render: dict[str, object] | None = None

    @property
    def streamed_any(self) -> bool:
        return self._streamed_any

    @property
    def stream_sink(self) -> StreamChunkSink:
        return self._handle_stream_chunk

    @property
    def reasoning_sink(self) -> StreamChunkSink:
        return self.handle_reasoning_chunk

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

    def end_provider_turn(
        self, *, final_text: str, has_tool_calls: bool
    ) -> None:
        del has_tool_calls
        self._stop_working(clear=True)
        if final_text and not self._streamed_any:
            self._ui.append_assistant(final_text)
            self._streamed_any = True
        self._ui.settle_assistant()

    def abort_provider_turn(self) -> None:
        self._stop_working(clear=True)
        self._ui.show_operation_aborted()

    def steer_provider_turn(self) -> None:
        # A steering message interrupts the turn but is not an error, so stop the
        # spinner without the red "Operation aborted" banner.
        self._stop_working(clear=True)

    def render_user_message(self, text: str) -> None:
        self._ui.submit_user_message(text)

    def render_tool_call(self, call: ProviderToolCall) -> None:
        self._stop_working(clear=True)
        self._last_tool_name = call.tool_name
        self._pending_render = None
        tool = self._tool_renderers.get(call.tool_name)
        if tool is not None:
            args = _parse_tool_input(call.arguments_json)
            state: dict[str, object] = {}
            self._pending_render = {
                "corr": call.provider_correlation_id,
                "args": args,
                "state": state,
            }
            if tool.render_call is not None:
                lines = self._dispatch_render(tool.render_call, args, state,
                                              is_result=False, content=None,
                                              details=None, is_error=False)
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
            tool = self._tool_renderers.get(self._last_tool_name)
            if tool is not None and tool.render_result is not None:
                details = None
                if self._render_details_sink is not None:
                    details = self._render_details_sink.pop(str(pending["corr"]), None)
                lines = self._dispatch_render(
                    tool.render_result, pending["args"], pending["state"],
                    is_result=True, content=output_text, details=details,
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

    def _dispatch_render(self, renderer, args, state, *, is_result, content,
                         details, is_error):
        # Local imports: the render-theme machinery is only needed on the
        # rarely-hit custom-renderer branch, so it is imported here rather than
        # at module top to keep this module's import-time dependency surface
        # focused on the loop's hot path.
        from pipy_harness.native.chrome import chrome_style_for
        from pipy_harness.native.tool_renderers import (
            build_tool_render_theme,
            render_tool_phase,
        )
        from pipy_harness.extensions import ToolRenderContext

        style = chrome_style_for(self._ui.terminal_stream)
        ctx = ToolRenderContext(
            tool_name=self._last_tool_name, args=args, is_result=is_result,
            is_error=is_error, content=content, details=details,
            expanded=self._ui.tools_expanded,
            width=self._ui._dimensions()[0],
            theme=build_tool_render_theme(style), state=state,
        )
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


def _plain_tool_call_header(call: ProviderToolCall) -> str:
    """Return a concise tool-call label for the TUI history region."""

    try:
        data = json.loads(call.arguments_json)
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    path = data.get("path")
    if call.tool_name == "read" and isinstance(path, str):
        prefix = "read resource" if path.startswith("/") else "read"
        return f"{prefix} {path}{_ToolLoopRenderer._read_range_label(data)}"
    if call.tool_name == "ls" and isinstance(path, str):
        return "ls" if path == "." else f"ls {path}"
    if call.tool_name in {"grep", "find"}:
        pattern = data.get("pattern")
        root = path if isinstance(path, str) else "."
        if isinstance(pattern, str):
            return f'{call.tool_name} "{pattern}" {root}'
    preview = _argument_preview(data)
    return f"{call.tool_name}({preview})"


def _argument_preview(data: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in sorted(data):
        value = data[key]
        rendered = json.dumps(value, sort_keys=True)
        if len(rendered) > 40:
            rendered = rendered[:39] + "…"
        parts.append(f"{key}={rendered}")
    return ", ".join(parts)


__all__ = [
    "NativeToolReplResult",
    "NativeToolReplSession",
    "production_tool_registry",
]

"""Bounded model-driven REPL session skeleton.

Slice 4 of the Tool-Loop Parity Track introduces a small `CodingSession`
class that wires the slice 2 contracts (`ToolDefinition`, `ToolRequest`,
`ToolExecutionResult`, `ToolPort`, `ToolContext`, `validate_arguments`) and the
slice 3 provider extension (`ProviderPort.supports_tool_calls`,
`ProviderToolCall`, `ProviderResult.tool_calls`) into a real turn loop.

The session is the product REPL behind `pipy repl --agent pipy-native`. It runs
the exact production tool registry (`read`, `ls`, `grep`, `find`, `write`,
`edit`, `bash`); tests may inject a `_FixtureTool` through the registry
argument to verify loop behavior in isolation.

Invariants pinned by the focused tests:

- The session refuses providers that do not advertise
  `supports_tool_calls=True`.
- `--tool-budget` is bounded to `[1, 200]`; the constructor validates the
  value.
- Each user turn allows at most `tool_budget` tool invocations; subsequent
  model-emitted calls receive a deterministic "tool budget exhausted"
  observation.
- Malformed tool calls are authorized calls whose arguments fail JSON decoding
  or schema validation. They are returned as canonical error tool-result
  observations and increment a streak counter; three consecutive malformed
  calls end the loop with a deterministic stderr diagnostic.
- Calls to unknown or out-of-snapshot tools are returned as budget-consuming
  policy errors and neither increment nor reset the malformed-call streak.
- One valid tool invocation resets the malformed streak, even if the tool
  returns an execution error such as a failed read or timed-out shell command.
- The session does not write prompts, model text, tool payloads, file
  contents, or diffs to the archive; only safe counters and labels.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from typing import ClassVar, TextIO

from pipy_harness.native.agent import AgentEventSink
from pipy_harness.native.agent.loop_policy import MAX_AGENT_TOOL_BUDGET
from pipy_harness.native.agent.provider_turn import _AbortCallbackSignal
from pipy_harness.native.automation.events import AutomationEventSink
from pipy_harness.native.clipboard import (
    ClipboardResult,
    ImageClipboardResult,
    copy_to_clipboard,
    read_clipboard_image,
)
from pipy_harness.native.coding.result import CodingSessionResult
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.extension_runtime import _ExtensionCandidate
from pipy_harness.native.extensions.contracts import ExtensionActivationBatch
from pipy_harness.native.keybindings import KeybindingsManager
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.repl.reload import ImplicitTrustState
from pipy_harness.native.repl.wiring import SessionWiringInput, wire_session
from pipy_harness.native.repl_input import (
    REPL_INPUT_RUNTIME_AUTO,
    NativeReplInput,
    native_repl_input_for,
)
from pipy_harness.native.repl_state import (
    NativeReplProviderState,
    StaticNativeReplProviderState,
)
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.session_generation import (
    PreparedReloadEffects,
    ReloadEffectPreparationPorts,
    ReloadPreparationObserver,
    balance_startup_candidate,
    build_prepared_reload_effects,
)
from pipy_harness.native.session_resume import ResumeContext
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tool_capabilities import ToolFilterOptions
from pipy_harness.native.tools import ToolPort
from pipy_harness.native.tools.registry import production_tool_registry
from pipy_harness.native.tui import ToolLoopTerminalUi
from pipy_harness.native.ui.clipboard_images import create_clipboard_config


def _build_detached_reload_effects(
    ports: ReloadEffectPreparationPorts,
    *,
    step_observer: ReloadPreparationObserver | None = None,
) -> PreparedReloadEffects:
    """Pure R3b adapter; production startup/reload intentionally never calls it."""

    return build_prepared_reload_effects(ports, step_observer=step_observer)


# A bounded one-shot completion handed to extension command handlers as
# ``ctx.complete(system_prompt, user_text)`` caps its inputs so a buggy handler
# cannot create unbounded provider input.


@dataclass
class CodingSession:
    """Bounded model-driven tool loop, slice 4 skeleton.

    `tool_registry` defaults to the empty production registry; tests pass a
    mapping populated with a `_FixtureTool` (or later real tools) to exercise
    the loop. `tool_budget` is per-user-turn and capped at
    `MAX_TOOL_BUDGET`. The session reads one user turn per `readline()`
    call from `input_stream` and stops when the stream returns an empty
    string (EOF) or the malformed-tool-call streak reaches three.
    """

    provider: InitVar[ProviderPort]
    tool_registry: dict[str, ToolPort] = field(default_factory=production_tool_registry)
    tool_budget: int = 50
    workspace_root: Path | None = None
    input_runtime: str = REPL_INPUT_RUNTIME_AUTO
    reference_roots: tuple[Path, ...] = field(default_factory=tuple)
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None = (
        None
    )
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
    # Optional canonical event projection used by architecture adapters and
    # tests. It participates in the same synchronous, ordered mode composite.
    agent_event_sink: "AgentEventSink | None" = None
    # Optional external abort signal for the headless automation RPC mode. When
    # set, a non-TUI provider turn runs on a worker thread with a cancel token
    # wired to this event, so an RPC ``abort`` cancels the in-flight turn at the
    # provider boundary. ``None`` (CLI/TUI/one-shot) keeps the simple blocking
    # provider call.
    abort_event: "threading.Event | _AbortCallbackSignal | None" = None
    resource_options: RuntimeResourceOptions = field(
        default_factory=RuntimeResourceOptions.empty
    )
    # Pi-shape ``pipy "<prompt>"``: positional prompts that seed the interactive
    # session's first user turn(s). They are delivered as provider-visible prompt
    # text (like a typed message, resolving @file/@image references) before the
    # loop blocks on fresh input. Empty for the bare ``pipy`` / piped-stdin case.
    initial_messages: tuple[str, ...] = field(default_factory=tuple)
    tool_filter_options: ToolFilterOptions = field(
        default_factory=ToolFilterOptions.empty
    )
    # Pi `--verbose`: force startup/resource chrome even when quietStartup is
    # enabled in settings, without mutating the persisted setting.
    verbose_startup: bool = False
    # Exact final cwd that entered trusted state only because no protected
    # resource existed at startup. A later explicit /reload may persist trust
    # once if a protected resource has appeared (Pi's narrow safety exception).
    # Construction input only: the live one-shot is `implicit_trust`, which the
    # reload owner clears once it fires.
    auto_trust_on_reload_cwd: InitVar[Path | None] = None
    # Finalized startup activation shared with catalog construction. Only the
    # initial run consumes it; explicit /reload performs a fresh activation.
    initial_extension_batch: ExtensionActivationBatch | None = None
    _coding_state: CodingSessionState = field(init=False, repr=False)
    implicit_trust: ImplicitTrustState = field(init=False, repr=False)

    DEFAULT_TOOL_BUDGET: ClassVar[int] = 50
    MAX_TOOL_BUDGET: ClassVar[int] = MAX_AGENT_TOOL_BUDGET

    def __post_init__(
        self, provider: ProviderPort, auto_trust_on_reload_cwd: Path | None
    ) -> None:
        self.implicit_trust = ImplicitTrustState(
            cwd=(
                auto_trust_on_reload_cwd.expanduser().resolve()
                if auto_trust_on_reload_cwd is not None
                else None
            )
        )
        if not provider.supports_tool_calls:
            raise ValueError(
                f"provider {provider.name!r} does not advertise "
                "supports_tool_calls=True; the pipy repl requires a "
                "tool-capable provider"
            )
        self._coding_state = CodingSessionState(
            provider=provider,
            provider_name=provider.name,
            model_id=provider.model_id,
        )
        if isinstance(self.tool_budget, bool) or not isinstance(self.tool_budget, int):
            raise TypeError("tool_budget must be an int")
        if self.tool_budget < 1 or self.tool_budget > self.MAX_TOOL_BUDGET:
            raise ValueError(
                "tool_budget must be in "
                f"[1, {self.MAX_TOOL_BUDGET}]; got {self.tool_budget}"
            )

    @property
    def provider_port(self) -> ProviderPort:
        """Return the state-owned provider port outside an active run."""

        return self._coding_state.provider

    @balance_startup_candidate
    def run(
        self,
        candidate: _ExtensionCandidate,
        *,
        workspace_root: Path | None = None,
        input_stream: TextIO,
        output_stream: TextIO,
        error_stream: TextIO,
        system_prompt: str = "",
        provider_name: str | None = None,
        model_id: str | None = None,
    ) -> CodingSessionResult:
        cwd = workspace_root or self.workspace_root
        if cwd is None:
            raise ValueError("CodingSession.run requires a workspace_root")
        cwd = cwd.expanduser().resolve()
        if not cwd.is_dir():
            raise ValueError(f"workspace_root is not a directory: {cwd}")
        wiring = wire_session(
            SessionWiringInput(
                candidate=candidate,
                cwd=cwd,
                input_stream=input_stream,
                output_stream=output_stream,
                error_stream=error_stream,
                system_prompt=system_prompt,
                provider_name=provider_name,
                model_id=model_id,
                build_terminal_ui=self._build_terminal_ui,
                build_repl_input=self._build_repl_input,
                coding_state=self._coding_state,
                abort_event=self.abort_event,
                agent_event_sink=self.agent_event_sink,
                automation_observer=self.automation_observer,
                clipboard_copy=self.clipboard_copy,
                implicit_trust=self.implicit_trust,
                initial_extension_batch=self.initial_extension_batch,
                initial_messages=self.initial_messages,
                keybindings_manager=self.keybindings_manager,
                native_session=self.native_session,
                prompt_history_store=self.prompt_history_store,
                provider_state=self.provider_state,
                reference_roots=self.reference_roots,
                resource_options=self.resource_options,
                resume_branch_label=self.resume_branch_label,
                resume_context=self.resume_context,
                settings_manager=self.settings_manager,
                tool_budget=self.tool_budget,
                tool_filter_options=self.tool_filter_options,
                tool_registry=self.tool_registry,
                verbose_startup=self.verbose_startup,
            )
        )
        if wiring.startup_failure is not None:
            return wiring.startup_failure
        delegation = wiring.delegation
        if delegation is None:
            raise RuntimeError("successful session wiring has no loop delegation")
        return delegation.loop_controller.run_loop(
            step_once=delegation.step_once,
            finalize=delegation.finalize,
            fire_session_start=delegation.fire_session_start,
            fire_session_shutdown=delegation.fire_session_shutdown,
            consume_settle_pending=delegation.consume_settle_pending,
            close_extension_session=delegation.close_extension_session,
            clear_extension_chrome=delegation.clear_extension_chrome,
        )

    def _build_repl_input(
        self,
        *,
        input_stream: TextIO,
        error_stream: TextIO,
        workspace: Path,
        command_names: tuple[str, ...],
        command_descriptions: Mapping[str, str],
    ) -> NativeReplInput:
        return native_repl_input_for(
            input_stream=input_stream,
            error_stream=error_stream,
            input_runtime=self.input_runtime,
            workspace=workspace,
            command_names=command_names,
            command_descriptions=command_descriptions,
        )

    def _build_terminal_ui(
        self,
        *,
        input_stream: TextIO,
        error_stream: TextIO,
        workspace: Path,
        keybindings_manager: KeybindingsManager | None = None,
        include_workspace_defaults: bool = False,
    ) -> ToolLoopTerminalUi | None:
        if self.input_runtime not in {REPL_INPUT_RUNTIME_AUTO, "tool-loop-tui"}:
            return None
        if not ToolLoopTerminalUi.is_supported(input_stream, error_stream):
            return None
        clipboard_config = create_clipboard_config(self.clipboard_image_read)
        return ToolLoopTerminalUi(
            input_stream=input_stream,
            terminal_stream=error_stream,
            cwd=workspace,
            keybindings_manager=keybindings_manager,
            include_workspace_defaults=include_workspace_defaults,
            clipboard_config=clipboard_config,
        )


__all__ = [
    "CodingSession",
    "production_tool_registry",
]

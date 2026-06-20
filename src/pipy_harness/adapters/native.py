"""Native pipy runtime adapter."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import TextIO

from pipy_harness.adapters.base import EventSink
from pipy_harness.capture import CapturePolicy
from pipy_harness.native.automation.events import AutomationEventSink
from pipy_harness.models import AdapterResult, PreparedRun, RunRequest
from pipy_harness.native.fake import FakeNoOpNativeTool
from pipy_harness.native.models import NativeRunInput
from pipy_harness.native.provider import ProviderPort, StreamChunkSink
from pipy_harness.native.repl_state import NativeModelSelection, NativeReplProviderState
from pipy_harness.native.package_runtime import compose_package_runtime
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.settings import SettingsManager, resolve_config_home
from pipy_harness.native.skills import SkillFile, compose_skills_system_block
from pipy_harness.native.system_prompt_inputs import resolve_system_prompt
from pipy_harness.native.repl_input import REPL_INPUT_RUNTIME_AUTO
from pipy_harness.native.session import (
    NATIVE_TOOL_LOOP_SYSTEM_PROMPT,
    NativeAgentSession,
    SYSTEM_PROMPT_ID,
    SYSTEM_PROMPT_VERSION,
)
from pipy_harness.native.session_resume import (
    ResumeContext,
    compose_resume_system_block,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tool import ToolPort
from pipy_harness.native.tool_loop_session import (
    NativeToolReplSession,
    production_tool_registry,
)
from pipy_harness.native.tools import ToolPort as ModelDrivenToolPort
from pipy_harness.native.workspace_context import (
    WorkspaceInstructionLoader,
    compose_system_prompt,
    empty_workspace_instruction_loader,
    workspace_instruction_safe_metadata,
)


class PipyNativeAdapter:
    """Run one minimal native pipy turn through an injected provider."""

    name = "pipy-native"

    def __init__(
        self,
        provider: ProviderPort,
        tool: ToolPort | None = None,
        *,
        instruction_loader: WorkspaceInstructionLoader = empty_workspace_instruction_loader,
        stream_sink: StreamChunkSink | None = None,
    ) -> None:
        self.provider = provider
        self.tool = tool or FakeNoOpNativeTool()
        self.instruction_loader = instruction_loader
        self.stream_sink = stream_sink

    def prepare(self, request: RunRequest) -> PreparedRun:
        cwd = request.cwd.expanduser().resolve()
        if not cwd.exists():
            raise ValueError(f"cwd does not exist: {cwd}")
        if not cwd.is_dir():
            raise ValueError(f"cwd is not a directory: {cwd}")
        if request.command:
            raise ValueError("pipy-native runs do not accept a command after --")
        if not request.goal:
            raise ValueError("pipy-native runs require --goal")

        return PreparedRun(
            command=(),
            cwd=cwd,
            adapter=self.name,
            command_executable=self.name,
            goal=request.goal,
            native_provider=request.native_provider or self.provider.name,
            native_model=request.native_model or self.provider.model_id,
        )

    def run(
        self,
        prepared: PreparedRun,
        *,
        event_sink: EventSink,
        capture_policy: CapturePolicy,
    ) -> AdapterResult:
        run_output = NativeAgentSession(
            provider=self.provider,
            tool=self.tool,
            instruction_loader=self.instruction_loader,
            stream_sink=self.stream_sink,
        ).run(
            NativeRunInput(
                goal=prepared.goal or "",
                cwd=prepared.cwd,
                provider_name=prepared.native_provider or self.provider.name,
                model_id=prepared.native_model or self.provider.model_id,
                system_prompt_id=SYSTEM_PROMPT_ID,
                system_prompt_version=SYSTEM_PROMPT_VERSION,
            ),
            event_sink,
        )
        if self.stream_sink is None and run_output.final_text:
            print(run_output.final_text, file=sys.stdout)
        elif self.stream_sink is not None and run_output.final_text:
            sys.stdout.write("\n")
            sys.stdout.flush()

        return AdapterResult(
            status=run_output.status,
            exit_code=run_output.exit_code,
            started_at=run_output.started_at,
            ended_at=run_output.ended_at,
            metadata={
                "adapter": self.name,
                "provider": run_output.provider_name,
                "model_id": run_output.model_id,
                "usage": run_output.usage or {},
                "error_type": run_output.error_type,
                "error_message": run_output.error_message,
            },
        )


class PipyNativeToolReplAdapter:
    """Run a bounded native pipy tool-loop REPL through an injected provider.

    This adapter is the product REPL behind `pipy repl --agent pipy-native`.
    It constructs a
    `NativeToolReplSession` with the current production tool registry (which
    holds only `read` at this slice) and the configured tool budget, and
    runs the loop against an injected provider.

    The adapter does not change the metadata-first archive contracts: the
    `AdapterResult.metadata` mapping carries only safe counters and labels,
    and never raw prompts, model text, tool payloads, file contents, or
    diffs.
    """

    name = "pipy-native"

    def __init__(
        self,
        provider: ProviderPort | None = None,
        *,
        provider_state: NativeReplProviderState | None = None,
        tool_registry: dict[str, ModelDrivenToolPort] | None = None,
        tool_budget: int = NativeToolReplSession.DEFAULT_TOOL_BUDGET,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
        error_stream: TextIO | None = None,
        instruction_loader: WorkspaceInstructionLoader = empty_workspace_instruction_loader,
        input_runtime: str = REPL_INPUT_RUNTIME_AUTO,
        reference_roots: tuple[Path, ...] = (),
        resume_context: ResumeContext | None = None,
        resume_branch_label: str | None = None,
        native_session: "NativeSessionTree | None" = None,
        settings_manager: "SettingsManager | None" = None,
        system_prompt_source: str | None = None,
        append_system_prompt_sources: list[str] | None = None,
        automation_observer: "AutomationEventSink | None" = None,
        abort_event: "threading.Event | None" = None,
        resource_options: RuntimeResourceOptions | None = None,
        initial_messages: tuple[str, ...] = (),
    ) -> None:
        if provider is None and provider_state is None:
            raise ValueError(
                "PipyNativeToolReplAdapter requires provider or provider_state"
            )
        # Positional prompts (`pipy "<prompt>"`) that seed the interactive
        # session's first user turn(s); empty for bare/piped-stdin invocations.
        self.initial_messages = tuple(initial_messages)
        self.resume_context = resume_context
        self.resume_branch_label = resume_branch_label
        self.settings_manager = settings_manager
        self.system_prompt_source = system_prompt_source
        self.append_system_prompt_sources = append_system_prompt_sources
        # Optional Pi-shaped session-event sink for the headless automation
        # transports. Forwarded to the tool-loop session; ``None`` keeps the
        # interactive path unchanged.
        self.automation_observer = automation_observer
        self.abort_event = abort_event
        self.resource_options = resource_options or RuntimeResourceOptions.empty()
        # Pre-built native product session tree (the product session source of
        # truth). The CLI builds this from -c/-r/--session/--fork/--no-session
        # and injects it; when None the loop runs on an ephemeral in-memory tree
        # so tests and library callers never write to the native-session store.
        self.native_session = native_session
        self.provider = provider
        self.provider_state = provider_state
        self.tool_registry = (
            dict(tool_registry) if tool_registry is not None else production_tool_registry()
        )
        self.tool_budget = tool_budget
        self.input_stream = input_stream or sys.stdin
        self.output_stream = output_stream or sys.stdout
        self.error_stream = error_stream or sys.stderr
        self.instruction_loader = instruction_loader
        self.input_runtime = input_runtime
        for root in reference_roots:
            if not isinstance(root, Path):
                raise ValueError(
                    "PipyNativeToolReplAdapter reference_roots entries must be Path"
                )
            if not root.is_absolute():
                raise ValueError(
                    "PipyNativeToolReplAdapter reference_roots entries must be absolute"
                )
        self.reference_roots = tuple(reference_roots)

    def prepare(self, request: RunRequest) -> PreparedRun:
        cwd = request.cwd.expanduser().resolve()
        if not cwd.exists():
            raise ValueError(f"cwd does not exist: {cwd}")
        if not cwd.is_dir():
            raise ValueError(f"cwd is not a directory: {cwd}")
        if request.command:
            raise ValueError(
                "pipy-native repl does not accept a command after --"
            )

        selection = self._current_selection()
        return PreparedRun(
            command=(),
            cwd=cwd,
            adapter=self.name,
            command_executable=self.name,
            goal=request.goal or "Native tool-loop REPL",
            native_provider=request.native_provider or selection.provider_name,
            native_model=request.native_model or selection.model_id,
        )

    def run(
        self,
        prepared: PreparedRun,
        *,
        event_sink: EventSink,
        capture_policy: CapturePolicy,
    ) -> AdapterResult:
        provider = self._current_provider()
        if not provider.supports_tool_calls:
            raise ValueError(
                f"provider {provider.name!r} does not advertise "
                "supports_tool_calls=True; the pipy repl requires a "
                "tool-capable provider"
            )
        discovery = self.instruction_loader(prepared.cwd)
        # Apply system-prompt replace/append (flags or SYSTEM.md/APPEND_SYSTEM.md
        # auto-discovery) to the base prompt before workspace context is added.
        resolved_prompt = resolve_system_prompt(
            NATIVE_TOOL_LOOP_SYSTEM_PROMPT,
            cwd=prepared.cwd,
            config_home=resolve_config_home(),
            system_prompt_source=self.system_prompt_source,
            append_sources=self.append_system_prompt_sources,
        )
        base_prompt = resolved_prompt.base_prompt
        if self.reference_roots:
            ref_lines = ["", "Reference roots (read-only, absolute paths):"]
            for root in self.reference_roots:
                ref_lines.append(f"- {root}")
            base_prompt = base_prompt + "\n" + "\n".join(ref_lines)
        # Discover the workspace + global skills the model may load on demand.
        # The same loader the /skill command uses; obtained here (before the
        # session runs) so the advertisement can enter the system prompt and the
        # skill directories can widen the read-only reference roots.
        skills = self._discover_skill_files(prepared.cwd)
        # Add each discovered skill's PARENT DIRECTORY to the read-only reference
        # roots so the model can `read` skill bodies, including global skills
        # outside cwd. Bounded to discovered skill directories; deduped; absolute.
        reference_roots = self._reference_roots_with_skill_dirs(skills)
        # Inject the Pi-shaped skill advertisement only when the read tool is in
        # the active tool set (mirrors Pi's customPromptHasRead gate); the model
        # loads a skill body with that tool.
        if "read" in self.tool_registry:
            composed_system_prompt = compose_system_prompt(
                base_prompt, discovery
            ) + compose_skills_system_block(skills)
        else:
            composed_system_prompt = compose_system_prompt(
                base_prompt, discovery
            )
        if self.resume_context is not None:
            # Seed the resumed tool-loop session with only the safe
            # metadata-only resume block; no prior prompts/output/summary text.
            block = compose_resume_system_block(self.resume_context)
            if self.resume_branch_label:
                block += f" Branch: {self.resume_branch_label}."
            composed_system_prompt = f"{composed_system_prompt}\n\n{block}"
        instruction_metadata = workspace_instruction_safe_metadata(discovery)
        event_sink.emit(
            "native.workspace_context.loaded",
            summary=(
                "Native workspace context resolved: "
                f"files={len(discovery.instructions)}, "
                f"total_byte_cap_reached={discovery.total_byte_cap_reached}."
            ),
            payload={
                "adapter": self.name,
                "repl_mode": "tool-loop",
                **instruction_metadata,
                **resolved_prompt.safe_metadata(),
            },
        )
        session = NativeToolReplSession(
            provider=provider,
            provider_state=self.provider_state,
            tool_registry=self.tool_registry,
            tool_budget=self.tool_budget,
            input_runtime=self.input_runtime,
            reference_roots=reference_roots,
            resume_context=self.resume_context,
            resume_branch_label=self.resume_branch_label,
            native_session=self.native_session,
            settings_manager=self.settings_manager,
            automation_observer=self.automation_observer,
            abort_event=self.abort_event,
            resource_options=self.resource_options,
            initial_messages=self.initial_messages,
        )
        run_output = session.run(
            workspace_root=prepared.cwd,
            input_stream=self.input_stream,
            output_stream=self.output_stream,
            error_stream=self.error_stream,
            system_prompt=composed_system_prompt,
            provider_name=prepared.native_provider or provider.name,
            model_id=prepared.native_model or provider.model_id,
        )
        if run_output.compaction_count:
            # Aggregate, metadata-only compaction record for the catalog. The
            # tool-loop session returns counts only; no dropped content leaves
            # memory.
            event_sink.emit(
                "native.session.compacted",
                summary=(
                    "Native tool-loop context compacted "
                    f"{run_output.compaction_count} time(s)."
                ),
                payload={
                    "adapter": self.name,
                    "repl_mode": "tool-loop",
                    "compaction_count": run_output.compaction_count,
                    "compaction_dropped_group_count": (
                        run_output.compaction_dropped_group_count
                    ),
                },
            )
        return AdapterResult(
            status=run_output.status,
            exit_code=run_output.exit_code,
            started_at=run_output.started_at,
            ended_at=run_output.ended_at,
            metadata={
                "adapter": self.name,
                "provider": run_output.provider_name,
                "model_id": run_output.model_id,
                "repl_mode": "tool-loop",
                "tool_budget": self.tool_budget,
                "user_turn_count": run_output.user_turn_count,
                "tool_invocation_count": run_output.tool_invocation_count,
                "malformed_argument_count": run_output.malformed_argument_count,
                "budget_exhausted_count": run_output.budget_exhausted_count,
                "file_reference_count": run_output.file_reference_count,
                "file_reference_loaded_count": run_output.file_reference_loaded_count,
                "file_reference_failed_count": run_output.file_reference_failed_count,
                "image_attachment_count": run_output.image_attachment_count,
                "image_attachment_loaded_count": (
                    run_output.image_attachment_loaded_count
                ),
                "image_attachment_failed_count": (
                    run_output.image_attachment_failed_count
                ),
                "compaction_count": run_output.compaction_count,
                "error_type": run_output.error_type,
                "error_message": run_output.error_message,
                **instruction_metadata,
            },
        )

    def _current_selection(self) -> NativeModelSelection:
        if self.provider_state is not None:
            return self.provider_state.current_selection()
        if self.provider is None:
            raise ValueError(
                "PipyNativeToolReplAdapter requires provider or provider_state"
            )
        return NativeModelSelection(self.provider.name, self.provider.model_id)

    def _current_provider(self) -> ProviderPort:
        if self.provider is not None:
            return self.provider
        if self.provider_state is None:
            raise ValueError(
                "PipyNativeToolReplAdapter requires provider or provider_state"
            )
        return self.provider_state.current_provider()

    def _discover_skill_files(self, cwd: Path) -> tuple[SkillFile, ...]:
        """Discover the skills the model may load, mirroring the /skill loader.

        Uses the same workspace + global + package discovery and Pi-shaped
        enablement filters the tool-loop session applies, so the system-prompt
        advertisement and the read-only reference roots match what `/skill`
        can run. ``install_theme_registry=False`` avoids re-installing the
        theme registry (the session installs it when it runs). When no settings
        manager was injected, falls back to the workspace settings, matching the
        session's own fallback.
        """

        options = self.resource_options
        settings = self.settings_manager or SettingsManager.for_workspace(cwd)
        package_roots = compose_package_runtime(
            settings,
            cwd,
            install_theme_registry=False,
        )
        resources = WorkspaceResources.discover(
            cwd,
            package_roots=package_roots,
            explicit_skill_paths=options.skill_paths,
            explicit_prompt_template_paths=options.prompt_template_paths,
            include_skills_defaults=not options.no_skills,
            include_prompt_template_defaults=not options.no_prompt_templates,
        ).with_enablement(
            skills_patterns=settings.get_skills_patterns(),
            prompts_patterns=settings.get_prompts_patterns(),
            enable_skill_commands=settings.get_enable_skill_commands(),
        )
        return resources.skills

    def _reference_roots_with_skill_dirs(
        self, skills: tuple[SkillFile, ...]
    ) -> tuple[Path, ...]:
        """Union the configured reference roots with discovered skill dirs.

        Each discovered skill's parent directory is added (resolved, absolute,
        deduped) so the read tool can load skill bodies — including global
        skills outside cwd. Widening is bounded to skill directories; the
        configured ``--read-root`` roots are preserved and kept first.
        """

        roots: list[Path] = list(self.reference_roots)
        seen: set[Path] = {root.resolve() for root in roots}
        for skill in skills:
            skill_dir = skill.absolute_path.parent.resolve()
            if skill_dir in seen:
                continue
            seen.add(skill_dir)
            roots.append(skill_dir)
        return tuple(roots)

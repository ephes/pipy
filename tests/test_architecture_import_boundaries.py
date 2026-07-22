"""Static dependency gates for the native architecture migration.

The target packages are introduced incrementally.  A rule becomes active as soon
as its source exists as either a package directory or a single-file module,
without importing it or any of its potentially effectful entrypoints.

This deliberately checks only syntactic ``import`` and ``from`` statements.  It
cannot see dynamic loading through ``importlib``, ``__import__``, or plugin
machinery, and it does not compute a transitive dependency graph.  A permitted
intermediate module can therefore launder a forbidden dependency; runtime and
integration gates remain responsible for those cases.  Imports beneath
``TYPE_CHECKING`` guards and inside function bodies are deliberately scanned and
forbidden: type-time and function-local dependencies are still architectural
coupling.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src"


@dataclass(frozen=True)
class BoundaryRule:
    source_package: str
    forbidden_imports: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class ImportReference:
    module: str
    line: int


@dataclass(frozen=True)
class BoundaryViolation:
    rule: BoundaryRule
    path: Path
    imported_module: str
    line: int

    def format(self, source_root: Path) -> str:
        relative_path = self.path.relative_to(source_root)
        return (
            f"{relative_path}:{self.line}: {self.rule.source_package} imports "
            f"forbidden module {self.imported_module!r} ({self.rule.reason})"
        )


_LEGACY_CONCRETE_PROVIDER_MODULES = (
    "pipy_harness.native.anthropic_provider",
    "pipy_harness.native.azure_openai_provider",
    "pipy_harness.native.bedrock_provider",
    "pipy_harness.native.cloudflare_provider",
    "pipy_harness.native.ds4_provider",
    "pipy_harness.native.google_provider",
    "pipy_harness.native.google_vertex_provider",
    "pipy_harness.native.mistral_provider",
    "pipy_harness.native.openai_codex_provider",
    "pipy_harness.native.openai_completions_provider",
    "pipy_harness.native.openai_provider",
    "pipy_harness.native.openrouter_provider",
)

# These modules are provider-facing architecture sources even though their
# filenames are not concrete ``*_provider.py`` transports: the shared helper
# owns HTTP adapter utilities, construction selects transports, the port defines
# their contract, the fake implements that contract for headless tests, and the
# deferred-tool adapter projects canonical history into provider requests.
_PROVIDER_UI_SUPPORT_MODULES = (
    "pipy_harness.native._provider_helpers",
    "pipy_harness.native.deferred_tools",
    "pipy_harness.native.fake",
    "pipy_harness.native.provider",
    "pipy_harness.native.provider_construction",
)

_PROVIDER_UI_FORBIDDEN_IMPORTS = (
    "pipy_harness.native.ui",
    "pipy_harness.native.tui",
)

# Phase 5 will move the concrete transports under ``native.providers``.  Until
# that cutover, enforce the same boundary for every current top-level transport
# and the provider-facing ports, fakes, deferred-tool adapter, and shared HTTP
# helper so the planned package rule cannot silently no-op.
_CURRENT_PROVIDER_UI_BOUNDARY_SOURCES = (
    *_LEGACY_CONCRETE_PROVIDER_MODULES,
    *_PROVIDER_UI_SUPPORT_MODULES,
)

_AGENT_USAGE_FORBIDDEN_IMPORTS = (
    "pipy_harness.capture",
    "pipy_session",
    "pipy_harness.native.automation",
    "pipy_harness.native.extension_runtime",
    "pipy_harness.native.extensions",
    "pipy_harness.native.ui",
    "pipy_harness.native.tui",
    "pipy_harness.native.tool_loop_session",
    "pipy_harness.native.session",
    "pipy_harness.native.session_resume",
    "pipy_harness.native.session_tree",
    "pipy_harness.native.session_tree_commands",
    "pipy_harness.native.provider_construction",
    "pipy_harness.native.provider_registry",
    "pipy_harness.native.catalog",
    "pipy_harness.native.catalog_data",
    "pipy_harness.native.catalog_state",
    "pipy_harness.native.extension_provider_catalog",
    "pipy_harness.native.chrome",
    "pipy_harness.native.tool_renderers",
    "pipy_harness.native.themes",
    "pipy_harness.native.theme_files",
    "pipy_harness.native.terminal_compare",
    "pipy_harness.native.terminal_input",
    "pipy_harness.native.terminal_screen",
    "pipy_harness.native.agent_adapters",
    "pipy_harness.native.repl_state",
    "pipy_harness.native.provider",
    "pipy_harness.native.models",
    "pipy_harness.native.usage",
    "pipy_harness.native.providers",
    *_LEGACY_CONCRETE_PROVIDER_MODULES,
)

_AGENT_HISTORY_FORBIDDEN_IMPORTS = (
    "pipy_harness.capture",
    "pipy_session",
    "pipy_harness.native.agent_adapters",
    "pipy_harness.native.automation",
    "pipy_harness.native.extension_runtime",
    "pipy_harness.native.extensions",
    "pipy_harness.native.ui",
    "pipy_harness.native.tui",
    "pipy_harness.native.chrome",
    "pipy_harness.native.tool_renderers",
    "pipy_harness.native.themes",
    "pipy_harness.native.theme_files",
    "pipy_harness.native.terminal_compare",
    "pipy_harness.native.terminal_input",
    "pipy_harness.native.terminal_screen",
    "pipy_harness.native.tool_loop_session",
    "pipy_harness.native.session",
    "pipy_harness.native.session_resume",
    "pipy_harness.native.session_tree",
    "pipy_harness.native.session_tree_commands",
    "pipy_harness.native.prompt_history",
    "pipy_harness.native.conversation",
    "pipy_harness.native.settings",
    "pipy_harness.native.provider",
    "pipy_harness.native.provider_construction",
    "pipy_harness.native.provider_registry",
    "pipy_harness.native.providers",
    "pipy_harness.native._provider_helpers",
    "pipy_harness.native.deferred_tools",
    "pipy_harness.native.fake",
    "pipy_harness.native.models",
    "pipy_harness.native.models_json",
    "pipy_harness.native.usage",
    "pipy_harness.native.catalog",
    "pipy_harness.native.catalog_data",
    "pipy_harness.native.catalog_state",
    "pipy_harness.native.extension_provider_catalog",
    "pipy_harness.native.repl_state",
    "pipy_harness.native.model_resolver",
    "pipy_harness.native.routing",
    "pipy_harness.native.scoped_models",
    *_LEGACY_CONCRETE_PROVIDER_MODULES,
)

_CONCRETE_TOOL_MODULES = (
    "pipy_harness.native.tools.bash",
    "pipy_harness.native.tools.edit",
    "pipy_harness.native.tools.edit_diff",
    "pipy_harness.native.tools.find",
    "pipy_harness.native.tools.grep",
    "pipy_harness.native.tools.ls",
    "pipy_harness.native.tools.read",
    "pipy_harness.native.tools.truncate",
    "pipy_harness.native.tools.write",
)

_CODING_FORBIDDEN_IMPORTS = (
    "pipy_harness.capture",
    "pipy_session",
    "pipy_harness.native.agent_adapters",
    "pipy_harness.native.automation",
    "pipy_harness.native.extension_runtime",
    "pipy_harness.native.extensions",
    "pipy_harness.native.ui",
    "pipy_harness.native.tui",
    "pipy_harness.native.terminal_compare",
    "pipy_harness.native.terminal_input",
    "pipy_harness.native.terminal_screen",
    "pipy_harness.native.tool_renderers",
    "pipy_harness.native.tool_loop_session",
    "pipy_harness.native.provider_construction",
    "pipy_harness.native.providers",
    "pipy_harness.native.session",
    "pipy_harness.native.session_resume",
    "pipy_harness.native.session_tree",
    "pipy_harness.native.session_tree_commands",
    *_CONCRETE_TOOL_MODULES,
    *_LEGACY_CONCRETE_PROVIDER_MODULES,
)

_CODING_STATE_FORBIDDEN_IMPORTS = (
    "pipy_harness.cli",
    "pipy_harness.adapters",
    "pipy_harness.runner",
    *_CODING_FORBIDDEN_IMPORTS,
    "pipy_harness.native._provider_helpers",
    "pipy_harness.native.agent_adapters",
    "pipy_harness.native.agent_loop_policy",
    "pipy_harness.native.agent_request",
    "pipy_harness.native.agent_runtime",
    "pipy_harness.native.catalog",
    "pipy_harness.native.catalog_data",
    "pipy_harness.native.catalog_state",
    "pipy_harness.native.changelog",
    "pipy_harness.native.chrome",
    "pipy_harness.native.clipboard",
    "pipy_harness.native.conversation",
    "pipy_harness.native.deferred_tools",
    "pipy_harness.native.extension_provider_catalog",
    "pipy_harness.native.fake",
    "pipy_harness.native.keybindings",
    "pipy_harness.native.model_resolver",
    "pipy_harness.native.models_json",
    "pipy_harness.native.package_manager",
    "pipy_harness.native.package_resources",
    "pipy_harness.native.package_runtime",
    "pipy_harness.native.project_trust",
    "pipy_harness.native.prompt_history",
    "pipy_harness.native.provider_registry",
    "pipy_harness.native.repl_state",
    "pipy_harness.native.resource_loading",
    "pipy_harness.native.resources",
    "pipy_harness.native.routing",
    "pipy_harness.native.scoped_models",
    "pipy_harness.native.settings",
    "pipy_harness.native.theme_files",
    "pipy_harness.native.themes",
    "pipy_harness.native.tool_capabilities",
    "pipy_harness.native.usage",
    "pipy_harness.native.workspace_context",
)

_CODING_STATE_ALLOWED_RUNTIME_PREFIXES = (
    "pipy_harness.native.agent",
    "pipy_harness.native.coding.input_queue",
    "pipy_harness.native.coding.state",
    "pipy_harness.native.tools.base",
)

_CODING_STATE_ALLOWED_RUNTIME_EXACT = frozenset(
    {
        "pipy_harness",
        "pipy_harness.status",
        "pipy_harness.native",
        "pipy_harness.native.cancellation",
        "pipy_harness.native.coding",
        "pipy_harness.native.models",
        "pipy_harness.native.provider",
        "pipy_harness.native.tools",
    }
)

_CODING_STATE_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "dataclasses",
        "dataclasses.dataclass",
        "math",
        "math.isfinite",
        "pipy_harness.native.agent.content",
        "pipy_harness.native.agent.content.ProductContent",
        "pipy_harness.native.agent.identity",
        "pipy_harness.native.agent.identity.AGENT_TOOL_REQUEST_ID_PREFIX",
        "pipy_harness.native.agent.messages",
        "pipy_harness.native.agent.messages.AgentAssistantMessage",
        "pipy_harness.native.agent.messages.AgentMessage",
        "pipy_harness.native.agent.messages.AgentToolCall",
        "pipy_harness.native.agent.messages.AgentToolResultMessage",
        "pipy_harness.native.agent.messages.AgentUserMessage",
        "pipy_harness.native.agent.loop_policy",
        "pipy_harness.native.agent.loop_policy.AgentToolPolicyState",
        "pipy_harness.native.agent.loop_policy.MAX_AGENT_TOOL_BUDGET",
        "pipy_harness.native.agent.results",
        "pipy_harness.native.agent.results.AgentFailure",
        "pipy_harness.native.agent.results.AgentUsage",
        "pipy_harness.native.agent.usage",
        "pipy_harness.native.agent.usage.AgentProviderUsageSample",
        "pipy_harness.native.agent.usage.AgentUsageAccumulator",
        "pipy_harness.native.provider",
        "pipy_harness.native.provider.ProviderPort",
    }
)

_CODING_PRODUCT_SESSION_FORBIDDEN_IMPORTS = (
    *_CODING_STATE_FORBIDDEN_IMPORTS,
    "pipy_harness.native.cancellation",
    "pipy_harness.native.coding.session",
    "pipy_harness.native.models",
    "pipy_harness.native.provider",
    "pipy_harness.native.tools",
)

_CODING_PRODUCT_SESSION_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "collections.abc",
        "collections.abc.Callable",
        "dataclasses",
        "dataclasses.dataclass",
        "typing",
        "typing.Protocol",
        "typing.cast",
        "typing.runtime_checkable",
        "pipy_harness.native.agent.content",
        "pipy_harness.native.agent.content.ProductContent",
        "pipy_harness.native.agent.messages",
        "pipy_harness.native.agent.messages.AgentMessage",
        "pipy_harness.native.coding.state",
        "pipy_harness.native.coding.state.CodingSessionState",
        "pipy_harness.native.coding.state.require_exact_agent_message",
        "pipy_harness.native.coding.state.require_exact_agent_messages",
    }
)

_CODING_COMMANDS_FORBIDDEN_IMPORTS = (
    *_CODING_STATE_FORBIDDEN_IMPORTS,
    "pipy_harness.native.cancellation",
    "pipy_harness.native.coding.command_registry",
    "pipy_harness.native.coding.input_queue",
    "pipy_harness.native.coding.product_session",
    "pipy_harness.native.coding.session",
    "pipy_harness.native.coding.state",
    "pipy_harness.native.export_distribution",
    "pipy_harness.native.models",
    "pipy_harness.native.provider",
    "pipy_harness.native.tools",
)

_CODING_COMMANDS_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "dataclasses",
        "dataclasses.dataclass",
        "enum",
        "enum.StrEnum",
        "pipy_harness.native.agent.content",
        "pipy_harness.native.agent.content.ProductContent",
    }
)

# The declarative command registry owns built-in command classification: it
# enumerates every built-in in one frozen table and iterates it in
# `classify_coding_command`. It depends on the pure `native.coding.commands`
# outcome kernel (value objects + validator) and canonical product content only;
# the reverse dependency (`commands` -> `command_registry`) is forbidden above so
# the kernel stays a leaf.
_CODING_COMMAND_REGISTRY_FORBIDDEN_IMPORTS = _CODING_COMMANDS_FORBIDDEN_IMPORTS

_CODING_COMMAND_REGISTRY_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "collections.abc",
        "collections.abc.Callable",
        "dataclasses",
        "dataclasses.dataclass",
        "dataclasses.field",
        "enum",
        "enum.StrEnum",
        "pipy_harness.native.agent.content",
        "pipy_harness.native.agent.content.ProductContent",
        "pipy_harness.native.coding.commands",
        "pipy_harness.native.coding.commands.CodingCommandAction",
        "pipy_harness.native.coding.commands.CodingCommandFooterPolicy",
        "pipy_harness.native.coding.commands.CodingCommandOutcome",
        "pipy_harness.native.coding.commands.CodingCommandOutcomeKind",
        "pipy_harness.native.coding.commands._require_exact_product_content",
    }
)

_CODING_AGENT_RUN_FORBIDDEN_IMPORTS = (
    *_CODING_STATE_FORBIDDEN_IMPORTS,
    "pipy_harness.native.coding.commands",
    "pipy_harness.native.coding.product_session",
    "pipy_harness.native.coding.session",
    "pipy_harness.native.provider",
)

_CODING_AGENT_RUN_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "collections.abc",
        "collections.abc.Callable",
        "pipy_harness.native.agent.active_input",
        "pipy_harness.native.agent.active_input.AgentActiveInput",
        "pipy_harness.native.agent.loop",
        "pipy_harness.native.agent.loop.AgentLoop",
        "pipy_harness.native.agent.loop.AgentLoopOutcome",
        "pipy_harness.native.agent.loop.AgentLoopProviderTurn",
        "pipy_harness.native.agent.loop.AgentLoopRequestPreparation",
        "pipy_harness.native.agent.loop.AgentLoopRequestSource",
        "pipy_harness.native.agent.loop.AgentLoopRunInput",
        "pipy_harness.native.agent.loop.AgentLoopStatusPolicy",
        "pipy_harness.native.agent.loop_policy",
        "pipy_harness.native.agent.loop_policy.AgentProviderStatusDecision",
        "pipy_harness.native.agent.loop_policy.AgentToolPolicy",
        "pipy_harness.native.agent.loop_policy.AgentToolPolicyState",
        "pipy_harness.native.agent.messages",
        "pipy_harness.native.agent.messages.AgentMessage",
        "pipy_harness.native.agent.ports",
        "pipy_harness.native.agent.ports.AgentEventSink",
        "pipy_harness.native.agent.provider_turn",
        "pipy_harness.native.agent.provider_turn.ProviderTurnOutcome",
        "pipy_harness.native.agent.request",
        "pipy_harness.native.agent.request.AgentProviderRequestSnapshot",
        "pipy_harness.native.agent.results",
        "pipy_harness.native.agent.results.AgentCancellationReason",
        "pipy_harness.native.agent.results.AgentFailure",
        "pipy_harness.native.agent.runtime_ports",
        "pipy_harness.native.agent.runtime_ports.AgentQueuedInput",
        "pipy_harness.native.agent.runtime_ports.AgentQueuedInputPort",
        "pipy_harness.native.agent.runtime_ports.AgentRunEffectSink",
        "pipy_harness.native.agent.runtime_ports.AgentUsagePublisher",
        "pipy_harness.native.agent.tools",
        "pipy_harness.native.agent.tools.AgentToolCapabilities",
        "pipy_harness.native.agent.tools.ToolInterruptWaiter",
        "pipy_harness.native.agent.usage",
        "pipy_harness.native.agent.usage.AgentTokenPricing",
        "pipy_harness.native.coding.state",
        "pipy_harness.native.coding.state.CodingSessionState",
        "pipy_harness.native.models",
        "pipy_harness.native.models.ProviderResult",
        "pipy_harness.native.tools.base",
        "pipy_harness.native.tools.base.ToolDefinition",
    }
)

# Accepted-input preparation depends on the same forbidden categories as the
# agent-run collaborators: canonical native.agent contracts plus the injected
# coding state/input queue only, never composition, UI/terminal, extensions,
# concrete providers/tools, persistence coordination, automation/RPC, the SDK,
# capture, or the metadata-only workflow archive.
_CODING_ACCEPTED_INPUT_FORBIDDEN_IMPORTS = _CODING_AGENT_RUN_FORBIDDEN_IMPORTS

_CODING_ACCEPTED_INPUT_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "collections.abc",
        "collections.abc.Callable",
        "dataclasses",
        "dataclasses.dataclass",
        "typing",
        "typing.Protocol",
        "pipy_harness.native.agent.active_input",
        "pipy_harness.native.agent.active_input.AgentActiveInput",
        "pipy_harness.native.agent.content",
        "pipy_harness.native.agent.content.ProductContent",
        "pipy_harness.native.agent.loop_policy",
        "pipy_harness.native.agent.loop_policy.AgentToolPolicyState",
        "pipy_harness.native.agent.messages",
        "pipy_harness.native.agent.messages.AgentUserMessage",
        "pipy_harness.native.coding.state",
        "pipy_harness.native.coding.state.CodingSessionState",
        "pipy_harness.native.file_references",
        "pipy_harness.native.file_references.FileReferenceResolution",
        "pipy_harness.native.image_attachment",
        "pipy_harness.native.image_attachment.ImageAttachmentResolution",
        "pipy_harness.native.image_attachment.ProviderImageAttachment",
    }
)

# The shutdown result projection depends on the same forbidden categories as the
# agent-run collaborators: it maps a coding-session result snapshot into the
# bounded metadata-only run result and may not reach composition, UI/terminal,
# extensions, concrete providers/tools, persistence coordination, automation/RPC,
# the SDK, capture, or the metadata-only workflow archive.
_CODING_RESULT_FORBIDDEN_IMPORTS = _CODING_AGENT_RUN_FORBIDDEN_IMPORTS

_CODING_RESULT_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "dataclasses",
        "dataclasses.dataclass",
        "datetime",
        "datetime.datetime",
        "pipy_harness.models",
        "pipy_harness.models.HarnessStatus",
        "pipy_harness.native.coding.state",
        "pipy_harness.native.coding.state.CodingSessionResultSnapshot",
    }
)

# The outer-loop controller depends on the same forbidden categories as the
# agent-run collaborators: it owns input selection and the true-idle boundary
# behind injected ports and may not reach composition, UI/terminal, extensions,
# concrete providers/tools, persistence coordination, automation/RPC, the SDK,
# capture, or the metadata-only workflow archive.
# The outer-loop controller now also owns the built-in>resource>extension
# command-dispatch precedence and therefore depends on the headless
# native.coding.commands outcome contracts (the dispatch/resolution DTOs). That
# module is un-forbidden for this rule alone; the concrete resource/extension
# dispatch and every UI/session effect stay behind the injected effects port.
# It further owns the loop driver + start/shutdown lifecycle
# (`CodingSessionController.run_loop`) and therefore depends on the headless
# native.coding.result module for the bounded run-result return type; the loop
# body, run transition, and every UI/provider/persistence effect stay behind the
# injected `drive`/lifecycle closures.
_CODING_SESSION_CONTROLLER_FORBIDDEN_IMPORTS = tuple(
    forbidden_import
    for forbidden_import in _CODING_AGENT_RUN_FORBIDDEN_IMPORTS
    if forbidden_import != "pipy_harness.native.coding.commands"
)

_CODING_SESSION_CONTROLLER_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "collections.abc",
        "collections.abc.Callable",
        "dataclasses",
        "dataclasses.dataclass",
        "enum",
        "enum.Enum",
        "typing",
        "typing.Protocol",
        "typing.runtime_checkable",
        "pipy_harness.native.agent.content",
        "pipy_harness.native.agent.content.ProductContent",
        "pipy_harness.native.agent.runtime_ports",
        "pipy_harness.native.agent.runtime_ports.AgentQueuedInput",
        "pipy_harness.native.agent.runtime_ports.AgentQueuedInputPort",
        "pipy_harness.native.coding.command_registry",
        "pipy_harness.native.coding.command_registry.classify_coding_command",
        "pipy_harness.native.coding.commands",
        "pipy_harness.native.coding.commands.CodingCommandOutcome",
        "pipy_harness.native.coding.commands.CodingCommandOutcomeKind",
        "pipy_harness.native.coding.commands.CommandDispatchResolution",
        "pipy_harness.native.coding.commands.ExtensionDispatchResolution",
        "pipy_harness.native.coding.commands.ResourceDispatchKind",
        "pipy_harness.native.coding.commands.ResourceDispatchResolution",
        "pipy_harness.native.coding.input_queue",
        "pipy_harness.native.coding.input_queue.CodingInputQueue",
        "pipy_harness.native.coding.input_queue.CodingInputSelection",
        "pipy_harness.native.coding.input_queue.CodingInputSource",
        "pipy_harness.native.coding.result",
        "pipy_harness.native.coding.result.NativeToolReplResult",
        "pipy_harness.native.coding.state",
        "pipy_harness.native.coding.state.CodingSessionState",
    }
)

_AGENT_TOOL_CAPABILITIES_FORBIDDEN_IMPORTS = (
    "pipy_harness.cli",
    "pipy_harness.adapters",
    *_AGENT_HISTORY_FORBIDDEN_IMPORTS,
    "pipy_harness.native.tool",
    "pipy_harness.native.read_only_tool",
    *_CONCRETE_TOOL_MODULES,
)

# Phase 2.2b.1 deliberately keeps canonical usage arithmetic smaller than the
# broader agent-core boundary: it may use only these standard-library symbols
# and the canonical immutable result contract.  Include both the ``from`` base
# and selected name because ``_import_references`` records both; ``__future__``
# is an import in the syntax tree too, even though it changes compiler behavior.
_AGENT_USAGE_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "collections.abc",
        "collections.abc.Mapping",
        "dataclasses",
        "dataclasses.dataclass",
        "math",
        "math.isfinite",
        "pipy_harness.native.agent.results",
        "pipy_harness.native.agent.results.AgentUsage",
    }
)

_AGENT_HISTORY_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "collections.abc",
        "collections.abc.Sequence",
        "dataclasses",
        "dataclasses.dataclass",
        "pipy_harness.native.agent.messages",
        "pipy_harness.native.agent.messages.AgentAssistantMessage",
        "pipy_harness.native.agent.messages.AgentMessage",
        "pipy_harness.native.agent.messages.AgentToolResultMessage",
        "pipy_harness.native.agent.messages.AgentUserMessage",
    }
)

_AGENT_TOOLS_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "collections.abc",
        "collections.abc.Callable",
        "collections.abc.Mapping",
        "collections.abc.Sequence",
        "dataclasses",
        "dataclasses.dataclass",
        "dataclasses.replace",
        "enum",
        "enum.StrEnum",
        "json",
        "threading",
        "typing",
        "typing.Protocol",
        "typing.runtime_checkable",
        "pipy_harness.native.agent.content",
        "pipy_harness.native.agent.content.ProductContent",
        "pipy_harness.native.agent.messages",
        "pipy_harness.native.agent.messages.AgentToolCall",
        "pipy_harness.native.agent.messages.AgentToolResultMessage",
        "pipy_harness.native.tools.base",
        "pipy_harness.native.tools.base.ToolArgumentError",
        "pipy_harness.native.tools.base.ToolContext",
        "pipy_harness.native.tools.base.ToolDefinition",
        "pipy_harness.native.tools.base.ToolExecutionResult",
        "pipy_harness.native.tools.base.ToolPort",
        "pipy_harness.native.tools.base.ToolRequest",
        "pipy_harness.native.tools.base.make_tool_request_id",
        "pipy_harness.native.tools.base.validate_arguments",
    }
)

_TOOL_CAPABILITIES_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "collections.abc",
        "collections.abc.Callable",
        "collections.abc.Collection",
        "collections.abc.Mapping",
        "collections.abc.Sequence",
        "dataclasses",
        "dataclasses.dataclass",
        "dataclasses.replace",
        "pathlib",
        "pathlib.Path",
        "pipy_harness.native.agent.messages",
        "pipy_harness.native.agent.messages.AgentToolCall",
        "pipy_harness.native.agent.messages.AgentToolResultMessage",
        "pipy_harness.native.agent.tools",
        "pipy_harness.native.agent.tools.ToolExecutionOutcome",
        "pipy_harness.native.agent.tools.ToolExecutor",
        "pipy_harness.native.agent.tools.ToolInterruptWaiter",
        "pipy_harness.native.tools",
        "pipy_harness.native.tools.ToolContext",
        "pipy_harness.native.tools.ToolDefinition",
        "pipy_harness.native.tools.ToolPort",
    }
)


ARCHITECTURE_RULES = (
    BoundaryRule(
        source_package="pipy_harness.status",
        forbidden_imports=("pipy_harness", "pipy_session"),
        reason=(
            "the shared status vocabulary must stay dependency-neutral and "
            "must not depend on harness, native-runtime, or archive layers"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.agent",
        forbidden_imports=(
            "pipy_harness.cli",
            "pipy_harness.capture",
            "pipy_harness.adapters",
            "pipy_harness.runner",
            "pipy_session",
            "pipy_harness.native.automation",
            "pipy_harness.native.extension_runtime",
            "pipy_harness.native.extensions",
            "pipy_harness.native.ui",
            "pipy_harness.native.tui",
            "pipy_harness.native.coding",
            "pipy_harness.native.tool_loop_session",
            "pipy_harness.native.session",
            "pipy_harness.native.session_resume",
            "pipy_harness.native.session_tree",
            "pipy_harness.native.session_tree_commands",
            "pipy_harness.native.providers",
            *_LEGACY_CONCRETE_PROVIDER_MODULES,
            "pipy_harness.native.provider_construction",
        ),
        reason=(
            "the agent core must not depend on entrypoints, outer adapters, UI, "
            "extensions, product-session storage, concrete providers, or the "
            "workflow archive"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.agent_loop_policy",
        forbidden_imports=(
            "pipy_harness.cli",
            "pipy_harness.capture",
            "pipy_harness.adapters",
            "pipy_harness.runner",
            "pipy_session",
            "pipy_harness.native.automation",
            "pipy_harness.native.extension_runtime",
            "pipy_harness.native.extensions",
            "pipy_harness.native.ui",
            "pipy_harness.native.tui",
            "pipy_harness.native.coding",
            "pipy_harness.native.tool_loop_session",
            "pipy_harness.native.session",
            "pipy_harness.native.session_resume",
            "pipy_harness.native.session_tree",
            "pipy_harness.native.session_tree_commands",
            "pipy_harness.native.providers",
            *_LEGACY_CONCRETE_PROVIDER_MODULES,
            "pipy_harness.native.provider_construction",
            *_CONCRETE_TOOL_MODULES,
        ),
        reason=(
            "the product agent-loop policy adapter must remain a narrow callback "
            "bridge without UI, extension-host, session, provider, tool, or "
            "capture/archive ownership"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.agent_adapters",
        forbidden_imports=(
            "pipy_harness.cli",
            "pipy_harness.runner",
            "pipy_harness.native.ui",
            "pipy_harness.native.tui",
            "pipy_harness.native.tool_loop_session",
            "pipy_harness.native.session_tree",
            "pipy_harness.native.extension_runtime",
            *_LEGACY_CONCRETE_PROVIDER_MODULES,
        ),
        reason=(
            "canonical projection contracts must not depend on composition roots, "
            "UI, concrete storage, extension hosts, or concrete providers"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.agent.usage",
        forbidden_imports=_AGENT_USAGE_FORBIDDEN_IMPORTS,
        reason=(
            "canonical usage accounting must not depend on UI, product session, "
            "capture/archive, concrete providers, or product pricing catalogs"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.agent.history",
        forbidden_imports=_AGENT_HISTORY_FORBIDDEN_IMPORTS,
        reason=(
            "canonical history compaction must remain a mechanical transform "
            "without UI, product-session policy or persistence, provider, "
            "catalog, capture, or workflow-archive dependencies"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.agent.tools",
        forbidden_imports=_AGENT_TOOL_CAPABILITIES_FORBIDDEN_IMPORTS,
        reason=(
            "the canonical tool-capability port and reusable executor must not "
            "depend on product composition, UI, persistence, concrete tools or "
            "providers, capture, or workflow-archive code"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.tool_capabilities",
        forbidden_imports=_AGENT_TOOL_CAPABILITIES_FORBIDDEN_IMPORTS,
        reason=(
            "the product tool-capability facade may compose injected tool ports "
            "but must not construct concrete tools or depend on sessions, UI, "
            "providers, capture, or workflow-archive code"
        ),
    ),
    *(
        BoundaryRule(
            source_package=source_package,
            forbidden_imports=_PROVIDER_UI_FORBIDDEN_IMPORTS,
            reason=(
                "current provider transports, construction, ports, fakes, "
                "deferred-tool adapters, and shared HTTP helpers must not "
                "depend on UI code"
            ),
        )
        for source_package in _CURRENT_PROVIDER_UI_BOUNDARY_SOURCES
    ),
    BoundaryRule(
        source_package="pipy_harness.native.providers",
        forbidden_imports=_PROVIDER_UI_FORBIDDEN_IMPORTS,
        reason="future Phase 5 provider transports must not depend on UI code",
    ),
    BoundaryRule(
        source_package="pipy_harness.native.coding",
        forbidden_imports=_CODING_FORBIDDEN_IMPORTS,
        reason=(
            "the headless coding-session controller must depend on injected "
            "ports rather than UI/ANSI, archive, extension implementation, "
            "persistence, concrete providers/tools, or the old composition monolith"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.coding.state",
        forbidden_imports=_CODING_STATE_FORBIDDEN_IMPORTS,
        reason=(
            "coding-session state may depend only on canonical agent contracts "
            "and the injected provider port, never composition implementations"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.coding.product_session",
        forbidden_imports=_CODING_PRODUCT_SESSION_FORBIDDEN_IMPORTS,
        reason=(
            "product-session coordination may depend only on canonical content "
            "and coding state, never concrete persistence, UI, providers, tools, "
            "extensions, automation, SDK, capture, or workflow-archive code"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.coding.commands",
        forbidden_imports=_CODING_COMMANDS_FORBIDDEN_IMPORTS,
        reason=(
            "headless imperative command outcomes may depend only on canonical "
            "product content, never composition, UI, persistence, providers, "
            "tools, extensions, automation, capture, or archive implementations"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.coding.command_registry",
        forbidden_imports=_CODING_COMMAND_REGISTRY_FORBIDDEN_IMPORTS,
        reason=(
            "the declarative command registry may depend only on the pure "
            "command-outcome kernel and canonical product content, never "
            "composition, UI, persistence, providers, tools, extensions, "
            "automation, capture, or archive implementations"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.coding.agent_run",
        forbidden_imports=_CODING_AGENT_RUN_FORBIDDEN_IMPORTS,
        reason=(
            "the agent-run collaborator adapters may depend only on canonical "
            "native.agent contracts and injected coding state/input queues, never "
            "UI/terminal, extensions, concrete providers/tools, persistence "
            "coordination, automation/RPC, the SDK, capture, or the metadata-only "
            "workflow archive"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.coding.accepted_input",
        forbidden_imports=_CODING_ACCEPTED_INPUT_FORBIDDEN_IMPORTS,
        reason=(
            "accepted-input preparation may depend only on canonical native.agent "
            "contracts, injected coding state/input queues, and the @file/image "
            "resolution data contracts, never UI/terminal, extensions, concrete "
            "providers/tools, persistence coordination, automation/RPC, the SDK, "
            "capture, or the metadata-only workflow archive"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.coding.result",
        forbidden_imports=_CODING_RESULT_FORBIDDEN_IMPORTS,
        reason=(
            "the shutdown result projection may depend only on the standard "
            "library, the shared harness status enum, and the coding-session "
            "result snapshot, never UI/terminal, extensions, concrete "
            "providers/tools, persistence coordination, automation/RPC, the SDK, "
            "capture, or the metadata-only workflow archive"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.coding.session_controller",
        forbidden_imports=_CODING_SESSION_CONTROLLER_FORBIDDEN_IMPORTS,
        reason=(
            "the outer-loop controller may depend only on canonical native.agent "
            "content/runtime-port contracts, the injected input queue, and the "
            "coding-session state anchor, never UI/terminal, extensions, concrete "
            "providers/tools, persistence coordination, automation/RPC, the SDK, "
            "capture, or the metadata-only workflow archive"
        ),
    ),
    BoundaryRule(
        source_package="pipy_harness.native.ui",
        forbidden_imports=(
            "pipy_harness.native.coding.state",
            "pipy_harness.native.coding.session",
            "pipy_harness.native.tool_loop_session",
        ),
        reason="UI adapters must consume ports/events instead of session internals",
    ),
    BoundaryRule(
        source_package="pipy_harness.native.extensions",
        forbidden_imports=(
            "pipy_harness.native.tui",
            "pipy_harness.native.coding.state",
            "pipy_harness.native.coding.session",
            "pipy_harness.native.tool_loop_session",
        ),
        reason=(
            "package activation must use extension-host ports instead of concrete "
            "TUI/session implementations"
        ),
    ),
)

# These exact names are part of the migration plan but do not all exist yet.
# Keep this list exact rather than prefix-matching it: a new nested or transitional
# name must be reviewed explicitly instead of making stale rule entries look valid.
_PLANNED_IMPORT_PREFIXES = frozenset(
    {
        "pipy_harness.native.agent",
        "pipy_harness.native.coding",
        "pipy_harness.native.coding.session",
        "pipy_harness.native.extensions",
        "pipy_harness.native.providers",
        "pipy_harness.native.ui",
    }
)


def _package_directory(source_root: Path, package: str) -> Path:
    return source_root.joinpath(*package.split("."))


def _source_module_or_package_exists(source_root: Path, module: str) -> bool:
    package_directory = _package_directory(source_root, module)
    return package_directory.is_dir() or package_directory.with_suffix(".py").is_file()


def _unresolved_forbidden_imports(
    source_root: Path,
    rules: tuple[BoundaryRule, ...],
    *,
    planned_imports: frozenset[str],
) -> list[str]:
    return sorted(
        {
            forbidden_import
            for rule in rules
            for forbidden_import in rule.forbidden_imports
            if forbidden_import not in planned_imports
            and not _source_module_or_package_exists(source_root, forbidden_import)
        }
    )


def _module_name(source_root: Path, path: Path) -> str:
    relative = path.relative_to(source_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _discover_top_level_concrete_provider_modules(
    source_root: Path,
) -> tuple[str, ...]:
    """Discover concrete legacy transports independently of boundary constants."""

    native_directory = source_root / "pipy_harness" / "native"
    discovered: list[str] = []
    for path in sorted(native_directory.glob("*_provider.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        declares_transport = any(
            isinstance(node, ast.ClassDef)
            and node.name.endswith("Provider")
            and any(
                isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                and member.name == "complete"
                for member in node.body
            )
            for node in tree.body
        )
        if declares_transport:
            discovered.append(_module_name(source_root, path))
    return tuple(discovered)


def _current_package(source_root: Path, path: Path) -> str:
    module = _module_name(source_root, path)
    if path.name == "__init__.py":
        return module
    return module.rpartition(".")[0]


def _resolve_from_base(
    current_package: str,
    *,
    level: int,
    module: str | None,
) -> str:
    if level == 0:
        return module or ""

    package_parts = current_package.split(".") if current_package else []
    parents_to_drop = level - 1
    if parents_to_drop >= len(package_parts):
        raise ValueError(
            f"relative import level {level} escapes current package {current_package!r}"
        )
    base_parts = package_parts[: len(package_parts) - parents_to_drop]
    if module:
        base_parts.extend(module.split("."))
    return ".".join(base_parts)


def _import_references(source_root: Path, path: Path) -> tuple[ImportReference, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    current_package = _current_package(source_root, path)
    references: list[ImportReference] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            references.extend(
                ImportReference(module=alias.name, line=node.lineno)
                for alias in node.names
            )
            continue
        if not isinstance(node, ast.ImportFrom):
            continue

        try:
            base = _resolve_from_base(
                current_package,
                level=node.level,
                module=node.module,
            )
        except ValueError as error:
            raise ValueError(f"{path}:{node.lineno}: {error}") from error
        if base:
            references.append(ImportReference(module=base, line=node.lineno))
        references.extend(
            ImportReference(
                module=f"{base}.{alias.name}" if base else alias.name,
                line=node.lineno,
            )
            for alias in node.names
            if alias.name != "*"
        )

    return tuple(references)


def _unallowlisted_direct_imports(
    source_root: Path,
    path: Path,
    *,
    allowed_imports: frozenset[str],
) -> tuple[ImportReference, ...]:
    return tuple(
        reference
        for reference in _import_references(source_root, path)
        if reference.module not in allowed_imports
    )


def _matches_module_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _evaluate_rule(source_root: Path, rule: BoundaryRule) -> list[BoundaryViolation]:
    package_directory = _package_directory(source_root, rule.source_package)
    module_path = package_directory.with_suffix(".py")
    source_files: list[Path] = []
    if package_directory.is_dir():
        source_files.extend(sorted(package_directory.rglob("*.py")))
    if module_path.is_file():
        source_files.append(module_path)
    if not source_files:
        return []

    violations: list[BoundaryViolation] = []
    for path in source_files:
        references = _import_references(source_root, path)
        for prefix in rule.forbidden_imports:
            matching = next(
                (
                    reference
                    for reference in references
                    if _matches_module_prefix(reference.module, prefix)
                ),
                None,
            )
            if matching is not None:
                violations.append(
                    BoundaryViolation(
                        rule=rule,
                        path=path,
                        imported_module=matching.module,
                        line=matching.line,
                    )
                )
    return violations


def _write_module(source_root: Path, module: str, source: str) -> Path:
    path = source_root.joinpath(*module.split(".")).with_suffix(".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_import_parser_resolves_absolute_and_relative_imports(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    path = _write_module(
        source_root,
        "pipy_harness.native.agent.provider_turn",
        """\
import pipy_harness.cli as cli
from .. import tui
from ..providers.openai import OpenAIProvider
from .events import AgentEvent
""",
    )

    assert set(_import_references(source_root, path)) == {
        ImportReference("pipy_harness.cli", 1),
        ImportReference("pipy_harness.native", 2),
        ImportReference("pipy_harness.native.tui", 2),
        ImportReference("pipy_harness.native.providers.openai", 3),
        ImportReference("pipy_harness.native.providers.openai.OpenAIProvider", 3),
        ImportReference("pipy_harness.native.agent.events", 4),
        ImportReference("pipy_harness.native.agent.events.AgentEvent", 4),
    }


def test_import_parser_rejects_relative_import_beyond_top_level(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    path = _write_module(
        source_root,
        "pipy_harness.native.agent.provider_turn",
        "from ....providers import Provider\n",
    )

    with pytest.raises(
        ValueError,
        match=(
            rf"^{re.escape(str(path))}:1: relative import level 4 escapes current package "
            r"'pipy_harness\.native\.agent'$"
        ),
    ):
        _import_references(source_root, path)


def test_rule_evaluator_accepts_ports_and_rejects_forbidden_layers(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    _write_module(
        source_root,
        "pipy_harness.native.agent.allowed",
        """\
from pipy_harness.native.provider import ProviderPort
from ..models import ProviderRequest
""",
    )
    forbidden_path = _write_module(
        source_root,
        "pipy_harness.native.agent.forbidden",
        """\
from pipy_harness.native import tui
from pipy_harness.native.automation import events
from pipy_harness.adapters import native
from pipy_session.recorder import append_event
""",
    )
    agent_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.agent"
    )

    violations = _evaluate_rule(source_root, agent_rule)

    assert {
        (violation.path, violation.imported_module, violation.line)
        for violation in violations
    } == {
        (forbidden_path, "pipy_harness.native.tui", 1),
        (forbidden_path, "pipy_harness.native.automation", 2),
        (forbidden_path, "pipy_harness.adapters", 3),
        (forbidden_path, "pipy_session.recorder", 4),
    }


def test_shared_harness_status_boundary_is_dependency_neutral(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    status_path = _write_module(
        source_root,
        "pipy_harness.status",
        "from pipy_harness.models import RunResult\nimport pipy_session\n",
    )
    status_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.status"
    )

    assert {
        (violation.path, violation.imported_module)
        for violation in _evaluate_rule(source_root, status_rule)
    } == {
        (status_path, "pipy_harness.models"),
        (status_path, "pipy_session"),
    }
    assert _evaluate_rule(SOURCE_ROOT, status_rule) == []


def test_agent_rule_recursively_governs_tool_executor_module(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    executor_path = _write_module(
        source_root,
        "pipy_harness.native.agent.tools",
        """\
import pipy_harness.native.tui
import pipy_harness.native.extension_runtime
import pipy_harness.native.tool_loop_session
""",
    )
    agent_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.agent"
    )

    violations = _evaluate_rule(source_root, agent_rule)

    assert {
        (violation.path, violation.imported_module, violation.line)
        for violation in violations
    } == {
        (executor_path, "pipy_harness.native.tui", 1),
        (executor_path, "pipy_harness.native.extension_runtime", 2),
        (executor_path, "pipy_harness.native.tool_loop_session", 3),
    }


def test_agent_rule_recursively_governs_provider_turn_executor_module(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    provider_turn_path = _write_module(
        source_root,
        "pipy_harness.native.agent.provider_turn",
        """\
import pipy_harness.capture
import pipy_harness.native.automation
import pipy_harness.native.extension_runtime
import pipy_harness.native.tui
import pipy_harness.native.tool_loop_session
import pipy_harness.native.provider_construction
import pipy_harness.native.openai_provider
import pipy_session
""",
    )
    agent_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.agent"
    )

    violations = _evaluate_rule(source_root, agent_rule)

    assert {
        (violation.path, violation.imported_module, violation.line)
        for violation in violations
    } == {
        (provider_turn_path, "pipy_harness.capture", 1),
        (provider_turn_path, "pipy_harness.native.automation", 2),
        (provider_turn_path, "pipy_harness.native.extension_runtime", 3),
        (provider_turn_path, "pipy_harness.native.tui", 4),
        (provider_turn_path, "pipy_harness.native.tool_loop_session", 5),
        (provider_turn_path, "pipy_harness.native.provider_construction", 6),
        (provider_turn_path, "pipy_harness.native.openai_provider", 7),
        (provider_turn_path, "pipy_session", 8),
    }


def test_agent_and_usage_rules_recursively_govern_usage_module(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    usage_path = _write_module(
        source_root,
        "pipy_harness.native.agent.usage",
        """\
import pipy_harness.capture
import pipy_harness.native.tui
import pipy_harness.native.tool_loop_session
import pipy_harness.native.provider_registry
import pipy_harness.native.catalog
import pipy_harness.native.provider
import pipy_harness.native.models
import pipy_harness.native.usage
import pipy_harness.native.openai_provider
import pipy_session
import pipy_harness.native.chrome
import pipy_harness.native.agent_adapters
import pipy_harness.native.repl_state
""",
    )
    agent_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.agent"
    )
    usage_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.agent.usage"
    )

    assert {
        (violation.path, violation.imported_module, violation.line)
        for violation in _evaluate_rule(source_root, agent_rule)
    } == {
        (usage_path, "pipy_harness.capture", 1),
        (usage_path, "pipy_harness.native.tui", 2),
        (usage_path, "pipy_harness.native.tool_loop_session", 3),
        (usage_path, "pipy_harness.native.openai_provider", 9),
        (usage_path, "pipy_session", 10),
    }
    assert {
        (violation.path, violation.imported_module, violation.line)
        for violation in _evaluate_rule(source_root, usage_rule)
    } == {
        (usage_path, "pipy_harness.capture", 1),
        (usage_path, "pipy_harness.native.tui", 2),
        (usage_path, "pipy_harness.native.tool_loop_session", 3),
        (usage_path, "pipy_harness.native.provider_registry", 4),
        (usage_path, "pipy_harness.native.catalog", 5),
        (usage_path, "pipy_harness.native.provider", 6),
        (usage_path, "pipy_harness.native.models", 7),
        (usage_path, "pipy_harness.native.usage", 8),
        (usage_path, "pipy_harness.native.openai_provider", 9),
        (usage_path, "pipy_session", 10),
        (usage_path, "pipy_harness.native.chrome", 11),
        (usage_path, "pipy_harness.native.agent_adapters", 12),
        (usage_path, "pipy_harness.native.repl_state", 13),
    }


def test_agent_and_history_rules_recursively_govern_history_module(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    history_path = _write_module(
        source_root,
        "pipy_harness.native.agent.history",
        """\
import pipy_harness.capture
import pipy_harness.native.agent_adapters
import pipy_harness.native.tui
import pipy_harness.native.tool_loop_session
import pipy_harness.native.session_tree
import pipy_harness.native.provider
import pipy_harness.native.catalog
import pipy_harness.native.models
import pipy_harness.native.themes
import pipy_harness.native.openai_provider
import pipy_session
""",
    )
    agent_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.agent"
    )
    history_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.agent.history"
    )

    assert {
        (violation.path, violation.imported_module, violation.line)
        for violation in _evaluate_rule(source_root, agent_rule)
    } == {
        (history_path, "pipy_harness.capture", 1),
        (history_path, "pipy_harness.native.tui", 3),
        (history_path, "pipy_harness.native.tool_loop_session", 4),
        (history_path, "pipy_harness.native.session_tree", 5),
        (history_path, "pipy_harness.native.openai_provider", 10),
        (history_path, "pipy_session", 11),
    }
    assert {
        (violation.path, violation.imported_module, violation.line)
        for violation in _evaluate_rule(source_root, history_rule)
    } == {
        (history_path, "pipy_harness.capture", 1),
        (history_path, "pipy_harness.native.agent_adapters", 2),
        (history_path, "pipy_harness.native.tui", 3),
        (history_path, "pipy_harness.native.tool_loop_session", 4),
        (history_path, "pipy_harness.native.session_tree", 5),
        (history_path, "pipy_harness.native.provider", 6),
        (history_path, "pipy_harness.native.catalog", 7),
        (history_path, "pipy_harness.native.models", 8),
        (history_path, "pipy_harness.native.themes", 9),
        (history_path, "pipy_harness.native.openai_provider", 10),
        (history_path, "pipy_session", 11),
    }


@pytest.mark.parametrize(
    "unlisted_import",
    (
        "pipy_harness.native.chrome",
        "pipy_harness.native.agent_adapters",
        "pipy_harness.native.repl_state",
    ),
)
def test_agent_usage_direct_import_allowlist_rejects_outer_runtime_modules(
    tmp_path: Path,
    unlisted_import: str,
) -> None:
    source_root = tmp_path / "src"
    usage_path = _write_module(
        source_root,
        "pipy_harness.native.agent.usage",
        f"import {unlisted_import}\n",
    )

    assert _unallowlisted_direct_imports(
        source_root,
        usage_path,
        allowed_imports=_AGENT_USAGE_ALLOWED_DIRECT_IMPORTS,
    ) == (ImportReference(unlisted_import, 1),)


def test_agent_usage_direct_imports_match_explicit_allowlist() -> None:
    usage_path = SOURCE_ROOT / "pipy_harness" / "native" / "agent" / "usage.py"
    references = _import_references(SOURCE_ROOT, usage_path)

    assert not _unallowlisted_direct_imports(
        SOURCE_ROOT,
        usage_path,
        allowed_imports=_AGENT_USAGE_ALLOWED_DIRECT_IMPORTS,
    ), "canonical usage accounting gained a direct dependency outside its allowlist"
    assert {reference.module for reference in references} == (
        _AGENT_USAGE_ALLOWED_DIRECT_IMPORTS
    ), "remove stale allowances when canonical usage accounting drops imports"


@pytest.mark.parametrize(
    "unlisted_import",
    (
        "pipy_harness.native.agent_adapters",
        "pipy_harness.native.session_tree",
        "pipy_harness.native.provider",
        "pipy_harness.native.catalog",
        "pipy_harness.native.tui",
    ),
)
def test_agent_history_direct_import_allowlist_rejects_outer_runtime_modules(
    tmp_path: Path,
    unlisted_import: str,
) -> None:
    source_root = tmp_path / "src"
    history_path = _write_module(
        source_root,
        "pipy_harness.native.agent.history",
        f"import {unlisted_import}\n",
    )

    assert _unallowlisted_direct_imports(
        source_root,
        history_path,
        allowed_imports=_AGENT_HISTORY_ALLOWED_DIRECT_IMPORTS,
    ) == (ImportReference(unlisted_import, 1),)


def test_agent_history_direct_imports_match_explicit_allowlist() -> None:
    history_path = SOURCE_ROOT / "pipy_harness" / "native" / "agent" / "history.py"
    references = _import_references(SOURCE_ROOT, history_path)

    assert not _unallowlisted_direct_imports(
        SOURCE_ROOT,
        history_path,
        allowed_imports=_AGENT_HISTORY_ALLOWED_DIRECT_IMPORTS,
    ), "canonical agent history gained a direct dependency outside its allowlist"
    assert {reference.module for reference in references} == (
        _AGENT_HISTORY_ALLOWED_DIRECT_IMPORTS
    ), "remove stale allowances when canonical agent history drops imports"


@pytest.mark.parametrize(
    ("relative_path", "allowed_imports", "description"),
    (
        (
            ("pipy_harness", "native", "agent", "tools.py"),
            _AGENT_TOOLS_ALLOWED_DIRECT_IMPORTS,
            "canonical agent tools",
        ),
        (
            ("pipy_harness", "native", "tool_capabilities.py"),
            _TOOL_CAPABILITIES_ALLOWED_DIRECT_IMPORTS,
            "product tool-capability facade",
        ),
    ),
)
def test_tool_capability_direct_imports_match_explicit_allowlists(
    relative_path: tuple[str, ...],
    allowed_imports: frozenset[str],
    description: str,
) -> None:
    module_path = SOURCE_ROOT.joinpath(*relative_path)
    references = _import_references(SOURCE_ROOT, module_path)

    assert not _unallowlisted_direct_imports(
        SOURCE_ROOT,
        module_path,
        allowed_imports=allowed_imports,
    ), f"{description} gained a direct dependency outside its allowlist"
    assert {reference.module for reference in references} == allowed_imports, (
        f"remove stale allowances when {description} drops imports"
    )


@pytest.mark.parametrize(
    ("module_name", "intermediate_name", "module_source", "forbidden_import"),
    (
        (
            "pipy_harness.native.agent.tools",
            "pipy_harness.native.agent.messages",
            "from .messages import AgentToolCall\n",
            "pipy_harness.native.session_tree",
        ),
        (
            "pipy_harness.native.tool_capabilities",
            "pipy_harness.native.agent.tools",
            "from .agent.tools import AgentToolCapabilities\n",
            "pipy_harness.native.tools.read",
        ),
    ),
)
def test_tool_capability_fresh_graph_detects_allowed_intermediate_laundering(
    tmp_path: Path,
    module_name: str,
    intermediate_name: str,
    module_source: str,
    forbidden_import: str,
) -> None:
    source_root = tmp_path / "src"
    for package in (
        "pipy_harness.__init__",
        "pipy_harness.native.__init__",
        "pipy_harness.native.agent.__init__",
    ):
        _write_module(source_root, package, "")
    _write_module(source_root, module_name, module_source)
    _write_module(
        source_root,
        intermediate_name,
        f"import {forbidden_import}\n\nclass AgentToolCall:\n    pass\n"
        "class AgentToolCapabilities:\n    pass\n",
    )
    _write_module(source_root, forbidden_import, "")

    script = f"""\
import importlib
import sys

sys.path.insert(0, {str(source_root)!r})
importlib.import_module({module_name!r})

unexpected = sorted(
    module_name
    for module_name in sys.modules
    if module_name == {forbidden_import!r}
    or module_name.startswith({forbidden_import!r} + ".")
)
assert unexpected == [{forbidden_import!r}], unexpected
"""

    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "forbidden_import",
    (
        "pipy_harness.native.tool_renderers",
        "pipy_harness.native.themes",
        "pipy_harness.native.theme_files",
        "pipy_harness.native.terminal_compare",
        "pipy_harness.native.terminal_input",
        "pipy_harness.native.terminal_screen",
    ),
)
def test_agent_usage_fresh_graph_detects_rendering_dependency_laundering(
    tmp_path: Path,
    forbidden_import: str,
) -> None:
    source_root = tmp_path / "src"
    for package in (
        "pipy_harness.__init__",
        "pipy_harness.native.__init__",
        "pipy_harness.native.agent.__init__",
    ):
        _write_module(source_root, package, "")
    usage_path = _write_module(
        source_root,
        "pipy_harness.native.agent.usage",
        "from pipy_harness.native.agent.results import AgentUsage\n",
    )
    _write_module(
        source_root,
        "pipy_harness.native.agent.results",
        f"import {forbidden_import}\n\nclass AgentUsage:\n    pass\n",
    )
    _write_module(source_root, forbidden_import, "")

    assert not _unallowlisted_direct_imports(
        source_root,
        usage_path,
        allowed_imports=_AGENT_USAGE_ALLOWED_DIRECT_IMPORTS,
    )

    script = f"""\
import importlib
import sys

sys.path.insert(0, {str(source_root)!r})
importlib.import_module("pipy_harness.native.agent.usage")

forbidden_prefixes = {_AGENT_USAGE_FORBIDDEN_IMPORTS!r}
unexpected = sorted(
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in forbidden_prefixes
    )
)
assert unexpected == [{forbidden_import!r}], unexpected
"""

    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "forbidden_import",
    (
        "pipy_harness.capture",
        "pipy_session",
        "pipy_harness.native.agent_adapters",
        "pipy_harness.native.session_tree",
        "pipy_harness.native.provider",
        "pipy_harness.native.catalog",
        "pipy_harness.native.tui",
        "pipy_harness.native.tool_renderers",
    ),
)
def test_agent_history_fresh_graph_detects_allowed_intermediate_laundering(
    tmp_path: Path,
    forbidden_import: str,
) -> None:
    source_root = tmp_path / "src"
    for package in (
        "pipy_harness.__init__",
        "pipy_harness.native.__init__",
        "pipy_harness.native.agent.__init__",
    ):
        _write_module(source_root, package, "")
    history_path = _write_module(
        source_root,
        "pipy_harness.native.agent.history",
        """\
from collections.abc import Sequence
from dataclasses import dataclass
from .messages import (
    AgentAssistantMessage,
    AgentMessage,
    AgentToolResultMessage,
    AgentUserMessage,
)
""",
    )
    _write_module(
        source_root,
        "pipy_harness.native.agent.messages",
        f"""\
import {forbidden_import}

class AgentAssistantMessage:
    pass
class AgentMessage:
    pass
class AgentToolResultMessage:
    pass
class AgentUserMessage:
    pass
""",
    )
    _write_module(source_root, forbidden_import, "")

    assert not _unallowlisted_direct_imports(
        source_root,
        history_path,
        allowed_imports=_AGENT_HISTORY_ALLOWED_DIRECT_IMPORTS,
    )

    script = f"""\
import importlib
import sys

sys.path.insert(0, {str(source_root)!r})
importlib.import_module("pipy_harness.native.agent.history")

forbidden_prefixes = {_AGENT_HISTORY_FORBIDDEN_IMPORTS!r}
unexpected = sorted(
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in forbidden_prefixes
    )
)
assert unexpected == [{forbidden_import!r}], unexpected
"""

    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("first_module", "second_module"),
    (
        ("pipy_harness.native.tools", "pipy_harness.native.agent"),
        ("pipy_harness.native.agent", "pipy_harness.native.tools"),
    ),
)
def test_agent_and_tool_contract_packages_import_in_either_order(
    first_module: str,
    second_module: str,
) -> None:
    script = f"""\
import importlib

importlib.import_module({first_module!r})
importlib.import_module({second_module!r})

agent = importlib.import_module("pipy_harness.native.agent")
tools = importlib.import_module("pipy_harness.native.tools")
assert hasattr(agent, "AgentEvent")
assert not hasattr(agent, "ToolExecutor")
assert hasattr(tools, "ToolContext")
assert not hasattr(tools, "ReadTool")
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("first_module", "second_module"),
    (
        (
            "pipy_harness.native.agent.tools",
            "pipy_harness.native.tool_capabilities",
        ),
        (
            "pipy_harness.native.tool_capabilities",
            "pipy_harness.native.agent.tools",
        ),
    ),
)
def test_isolated_tool_capability_imports_stay_bounded_in_either_order(
    first_module: str,
    second_module: str,
) -> None:
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    forbidden_prefixes = _AGENT_TOOL_CAPABILITIES_FORBIDDEN_IMPORTS
    script = f"""\
import importlib
import sys
import types

def namespace_package(name, path):
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [path]
    sys.modules[name] = module
    return module

pipy_package = namespace_package("pipy_harness", {str(package_root)!r})
native_package = namespace_package("pipy_harness.native", {str(native_root)!r})
pipy_package.native = native_package

agent = importlib.import_module("pipy_harness.native.agent")
assert not hasattr(agent, "AgentToolCapabilities")
assert not hasattr(agent, "ToolExecutor")

importlib.import_module({first_module!r})
importlib.import_module({second_module!r})

agent_tools = importlib.import_module("pipy_harness.native.agent.tools")
product_tools = importlib.import_module("pipy_harness.native.tool_capabilities")
assert agent_tools.AgentToolCapabilities.__module__ == (
    "pipy_harness.native.agent.tools"
)
assert product_tools.NativeToolCapabilities.__module__ == (
    "pipy_harness.native.tool_capabilities"
)
assert product_tools.ToolFilterOptions.__module__ == (
    "pipy_harness.native.tool_capabilities"
)
assert not hasattr(agent, "AgentToolCapabilities")
assert not hasattr(agent, "ToolExecutor")

forbidden_prefixes = {forbidden_prefixes!r}
unexpected = sorted(
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in forbidden_prefixes
    )
)
assert not unexpected, unexpected
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_product_tool_capability_facade_structurally_implements_agent_port(
    tmp_path: Path,
) -> None:
    from pipy_harness.native.agent.tools import AgentToolCapabilities
    from pipy_harness.native.tool_capabilities import (
        NativeToolCapabilities,
        ToolFilterOptions,
    )

    capabilities = NativeToolCapabilities(
        {},
        {},
        workspace_root=tmp_path,
        reference_roots=(),
        stderr_sink=lambda _message: None,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.0,
    )

    assert isinstance(capabilities, AgentToolCapabilities)
    capabilities_port: AgentToolCapabilities = capabilities
    assert capabilities_port.definitions() == ()


def test_tool_capability_types_are_not_eager_native_root_exports() -> None:
    script = """\
import pipy_harness.native as native
import pipy_harness.native.agent as agent

assert not hasattr(agent, "AgentToolCapabilities")
assert not hasattr(agent, "ToolExecutor")
assert not hasattr(native, "NativeToolCapabilities")
assert not hasattr(native, "ToolFilterOptions")
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("first_module", "second_module"),
    (
        (
            "pipy_harness.native.provider",
            "pipy_harness.native.agent.provider_turn",
        ),
        (
            "pipy_harness.native.agent.provider_turn",
            "pipy_harness.native.provider",
        ),
    ),
)
def test_isolated_provider_turn_executor_import_stays_headless_in_either_order(
    first_module: str,
    second_module: str,
) -> None:
    # ``pipy_harness`` and ``pipy_harness.native`` are pre-existing composition
    # roots whose initializers eagerly load capture, concrete providers, and the
    # product session. Namespace stubs preserve normal child-package discovery
    # while excluding those unrelated initializers, so this fresh process
    # measures the new provider-turn executor dependency graph itself.
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    forbidden_prefixes = (
        "pipy_harness.capture",
        "pipy_session",
        "pipy_harness.native.automation",
        "pipy_harness.native.extension_runtime",
        "pipy_harness.native.extensions",
        "pipy_harness.native.ui",
        "pipy_harness.native.tui",
        "pipy_harness.native.tool_loop_session",
        "pipy_harness.native.session",
        "pipy_harness.native.session_compaction",
        "pipy_harness.native.session_resume",
        "pipy_harness.native.session_tree",
        "pipy_harness.native.provider_construction",
        *_LEGACY_CONCRETE_PROVIDER_MODULES,
    )
    script = f"""\
import importlib
import sys
import types

def namespace_package(name, path):
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [path]
    sys.modules[name] = module
    return module

pipy_package = namespace_package("pipy_harness", {str(package_root)!r})
native_package = namespace_package("pipy_harness.native", {str(native_root)!r})
pipy_package.native = native_package

importlib.import_module({first_module!r})
importlib.import_module({second_module!r})

agent = importlib.import_module("pipy_harness.native.agent")
provider_turn = importlib.import_module("pipy_harness.native.agent.provider_turn")
provider = importlib.import_module("pipy_harness.native.provider")
assert provider_turn.ProviderTurnExecutor.__module__ == (
    "pipy_harness.native.agent.provider_turn"
)
assert provider.ProviderPort.__module__ == "pipy_harness.native.provider"
assert not hasattr(agent, "ProviderTurnExecutor")

forbidden_prefixes = {forbidden_prefixes!r}
unexpected = sorted(
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in forbidden_prefixes
    )
)
assert not unexpected, unexpected
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_isolated_agent_usage_import_stays_dependency_neutral() -> None:
    # Stub only the pre-existing outer composition roots. Import the real agent
    # initializer first to prove it does not load or expose the usage runtime,
    # then measure the direct usage submodule's transitive dependency graph.
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    forbidden_prefixes = _AGENT_USAGE_FORBIDDEN_IMPORTS
    script = f"""\
import importlib
import sys
import types

def namespace_package(name, path):
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [path]
    sys.modules[name] = module
    return module

pipy_package = namespace_package("pipy_harness", {str(package_root)!r})
native_package = namespace_package("pipy_harness.native", {str(native_root)!r})
pipy_package.native = native_package

agent = importlib.import_module("pipy_harness.native.agent")
assert "pipy_harness.native.agent.usage" not in sys.modules
assert not hasattr(agent, "AgentTokenPricing")
assert not hasattr(agent, "AgentUsageAccumulator")

usage = importlib.import_module("pipy_harness.native.agent.usage")
assert usage.AgentTokenPricing.__module__ == "pipy_harness.native.agent.usage"
assert usage.AgentUsageAccumulator.__module__ == "pipy_harness.native.agent.usage"
assert not hasattr(agent, "AgentTokenPricing")
assert not hasattr(agent, "AgentUsageAccumulator")

forbidden_prefixes = {forbidden_prefixes!r}
unexpected = sorted(
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in forbidden_prefixes
    )
)
assert not unexpected, unexpected
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_isolated_agent_history_import_stays_dependency_neutral() -> None:
    # Stub only the pre-existing outer composition roots. Import the real agent
    # initializer first to prove it does not load or expose the history runtime,
    # then measure the direct history submodule's transitive dependency graph.
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    forbidden_prefixes = _AGENT_HISTORY_FORBIDDEN_IMPORTS
    script = f"""\
import importlib
import sys
import types

def namespace_package(name, path):
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [path]
    sys.modules[name] = module
    return module

pipy_package = namespace_package("pipy_harness", {str(package_root)!r})
native_package = namespace_package("pipy_harness.native", {str(native_root)!r})
pipy_package.native = native_package

agent = importlib.import_module("pipy_harness.native.agent")
assert "pipy_harness.native.agent.history" not in sys.modules
assert not hasattr(agent, "AgentHistoryCompaction")
assert not hasattr(agent, "compact_agent_history")
assert not hasattr(agent, "should_compact_agent_history")

history = importlib.import_module("pipy_harness.native.agent.history")
assert history.AgentHistoryCompaction.__module__ == (
    "pipy_harness.native.agent.history"
)
assert history.compact_agent_history.__module__ == (
    "pipy_harness.native.agent.history"
)
assert history.should_compact_agent_history.__module__ == (
    "pipy_harness.native.agent.history"
)
assert not hasattr(agent, "AgentHistoryCompaction")
assert not hasattr(agent, "compact_agent_history")
assert not hasattr(agent, "should_compact_agent_history")

forbidden_prefixes = {forbidden_prefixes!r}
unexpected = sorted(
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in forbidden_prefixes
    )
)
assert not unexpected, unexpected
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_superseded_session_compaction_module_is_deleted() -> None:
    assert not _source_module_or_package_exists(
        SOURCE_ROOT, "pipy_harness.native.session_compaction"
    )


@pytest.mark.parametrize(
    "source_package",
    (
        "pipy_harness.native.agent",
        "pipy_harness.native.agent_adapters",
    ),
)
@pytest.mark.parametrize(
    "forbidden_import",
    _PROVIDER_UI_FORBIDDEN_IMPORTS,
)
def test_each_canonical_agent_ui_boundary_rule_activates(
    tmp_path: Path,
    source_package: str,
    forbidden_import: str,
) -> None:
    source_root = tmp_path / "src"
    forbidden_path = _write_module(
        source_root,
        source_package,
        f"import {forbidden_import}\n",
    )
    rule = next(
        rule for rule in ARCHITECTURE_RULES if rule.source_package == source_package
    )

    violations = _evaluate_rule(source_root, rule)

    assert len(violations) == 1
    assert violations[0].path == forbidden_path
    assert violations[0].imported_module == forbidden_import


@pytest.mark.parametrize(
    "source_package",
    _CURRENT_PROVIDER_UI_BOUNDARY_SOURCES,
)
@pytest.mark.parametrize(
    "forbidden_import",
    _PROVIDER_UI_FORBIDDEN_IMPORTS,
)
def test_each_current_provider_ui_boundary_rule_activates(
    tmp_path: Path,
    source_package: str,
    forbidden_import: str,
) -> None:
    source_root = tmp_path / "src"
    provider_path = _write_module(
        source_root,
        source_package,
        f"import {forbidden_import}\n",
    )
    provider_rule = next(
        rule for rule in ARCHITECTURE_RULES if rule.source_package == source_package
    )

    violations = _evaluate_rule(source_root, provider_rule)

    assert len(violations) == 1
    assert violations[0].path == provider_path
    assert violations[0].imported_module == forbidden_import


@pytest.mark.parametrize("forbidden_import", _PROVIDER_UI_FORBIDDEN_IMPORTS)
def test_future_provider_package_ui_boundary_rule_activates(
    tmp_path: Path,
    forbidden_import: str,
) -> None:
    source_root = tmp_path / "src"
    provider_path = _write_module(
        source_root,
        "pipy_harness.native.providers.transport",
        f"import {forbidden_import}\n",
    )
    provider_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.providers"
    )

    violations = _evaluate_rule(source_root, provider_rule)

    assert len(violations) == 1
    assert violations[0].path == provider_path
    assert violations[0].imported_module == forbidden_import


def test_coding_rule_recursively_governs_headless_modules() -> None:
    coding_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.coding"
    )

    assert _package_directory(SOURCE_ROOT, coding_rule.source_package).is_dir()
    assert _evaluate_rule(SOURCE_ROOT, coding_rule) == []


def test_coding_state_direct_imports_match_explicit_allowlist() -> None:
    state_path = SOURCE_ROOT / "pipy_harness" / "native" / "coding" / "state.py"
    references = _import_references(SOURCE_ROOT, state_path)

    assert not _unallowlisted_direct_imports(
        SOURCE_ROOT,
        state_path,
        allowed_imports=_CODING_STATE_ALLOWED_DIRECT_IMPORTS,
    ), "coding-session state gained a direct dependency outside its allowlist"
    assert {reference.module for reference in references} == (
        _CODING_STATE_ALLOWED_DIRECT_IMPORTS
    ), "remove stale allowances when coding-session state drops imports"


def test_coding_product_session_direct_imports_match_explicit_allowlist() -> None:
    product_session_path = (
        SOURCE_ROOT
        / "pipy_harness"
        / "native"
        / "coding"
        / "product_session.py"
    )
    references = _import_references(SOURCE_ROOT, product_session_path)

    assert not _unallowlisted_direct_imports(
        SOURCE_ROOT,
        product_session_path,
        allowed_imports=_CODING_PRODUCT_SESSION_ALLOWED_DIRECT_IMPORTS,
    ), "product-session coordination gained a dependency outside its allowlist"
    assert {reference.module for reference in references} == (
        _CODING_PRODUCT_SESSION_ALLOWED_DIRECT_IMPORTS
    ), "remove stale allowances when product-session coordination drops imports"


def test_coding_commands_direct_imports_match_explicit_allowlist() -> None:
    commands_path = (
        SOURCE_ROOT / "pipy_harness" / "native" / "coding" / "commands.py"
    )
    references = _import_references(SOURCE_ROOT, commands_path)

    assert not _unallowlisted_direct_imports(
        SOURCE_ROOT,
        commands_path,
        allowed_imports=_CODING_COMMANDS_ALLOWED_DIRECT_IMPORTS,
    ), "headless command outcomes gained a dependency outside their allowlist"
    assert {reference.module for reference in references} == (
        _CODING_COMMANDS_ALLOWED_DIRECT_IMPORTS
    ), "remove stale allowances when headless command outcomes drop imports"


def test_coding_command_registry_direct_imports_match_explicit_allowlist() -> None:
    registry_path = (
        SOURCE_ROOT / "pipy_harness" / "native" / "coding" / "command_registry.py"
    )
    references = _import_references(SOURCE_ROOT, registry_path)

    assert not _unallowlisted_direct_imports(
        SOURCE_ROOT,
        registry_path,
        allowed_imports=_CODING_COMMAND_REGISTRY_ALLOWED_DIRECT_IMPORTS,
    ), "the declarative command registry gained a dependency outside its allowlist"
    assert {reference.module for reference in references} == (
        _CODING_COMMAND_REGISTRY_ALLOWED_DIRECT_IMPORTS
    ), "remove stale allowances when the command registry drops imports"


def test_coding_agent_run_direct_imports_match_explicit_allowlist() -> None:
    agent_run_path = (
        SOURCE_ROOT / "pipy_harness" / "native" / "coding" / "agent_run.py"
    )
    references = _import_references(SOURCE_ROOT, agent_run_path)

    assert not _unallowlisted_direct_imports(
        SOURCE_ROOT,
        agent_run_path,
        allowed_imports=_CODING_AGENT_RUN_ALLOWED_DIRECT_IMPORTS,
    ), "agent-run collaborator adapters gained a dependency outside their allowlist"
    assert {reference.module for reference in references} == (
        _CODING_AGENT_RUN_ALLOWED_DIRECT_IMPORTS
    ), "remove stale allowances when the agent-run adapters drop imports"


@pytest.mark.parametrize(
    "forbidden_import",
    (
        "pipy_harness.native.ui",
        "pipy_harness.native.tui",
        "pipy_harness.native.extensions",
        "pipy_harness.native.automation",
        "pipy_harness.native.coding.commands",
        "pipy_harness.native.coding.product_session",
        "pipy_harness.native.coding.session",
        "pipy_harness.native.provider",
        "pipy_harness.native.tools.bash",
        "pipy_harness.native.openai_provider",
        "pipy_harness.capture",
        "pipy_session",
    ),
)
def test_coding_agent_run_rule_blocks_outer_runtime_imports(
    tmp_path: Path,
    forbidden_import: str,
) -> None:
    source_root = tmp_path / "src"
    agent_run_path = _write_module(
        source_root,
        "pipy_harness.native.coding.agent_run",
        f"import {forbidden_import}\n",
    )
    rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.coding.agent_run"
    )

    violations = _evaluate_rule(source_root, rule)

    assert len(violations) == 1
    assert violations[0].path == agent_run_path
    assert violations[0].imported_module == forbidden_import


def test_coding_agent_run_rule_allows_canonical_agent_contracts(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    _write_module(
        source_root,
        "pipy_harness.native.coding.agent_run",
        """\
from pipy_harness.native.agent.loop import AgentLoopRequestPreparation
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.coding.input_queue import CodingInputQueue
from pipy_harness.native.models import ProviderResult
from pipy_harness.native.tools.base import ToolDefinition
""",
    )
    rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.coding.agent_run"
    )

    assert _evaluate_rule(source_root, rule) == []


def test_coding_accepted_input_direct_imports_match_explicit_allowlist() -> None:
    accepted_input_path = (
        SOURCE_ROOT / "pipy_harness" / "native" / "coding" / "accepted_input.py"
    )
    references = _import_references(SOURCE_ROOT, accepted_input_path)

    assert not _unallowlisted_direct_imports(
        SOURCE_ROOT,
        accepted_input_path,
        allowed_imports=_CODING_ACCEPTED_INPUT_ALLOWED_DIRECT_IMPORTS,
    ), "accepted-input preparation gained a dependency outside its allowlist"
    assert {reference.module for reference in references} == (
        _CODING_ACCEPTED_INPUT_ALLOWED_DIRECT_IMPORTS
    ), "remove stale allowances when accepted-input preparation drops imports"


@pytest.mark.parametrize(
    "forbidden_import",
    (
        "pipy_harness.native.ui",
        "pipy_harness.native.tui",
        "pipy_harness.native.extensions",
        "pipy_harness.native.extension_runtime",
        "pipy_harness.native.automation",
        "pipy_harness.native.coding.commands",
        "pipy_harness.native.coding.product_session",
        "pipy_harness.native.coding.session",
        "pipy_harness.native.provider",
        "pipy_harness.native.tools.bash",
        "pipy_harness.native.openai_provider",
        "pipy_harness.capture",
        "pipy_session",
    ),
)
def test_coding_accepted_input_rule_blocks_outer_runtime_imports(
    tmp_path: Path,
    forbidden_import: str,
) -> None:
    source_root = tmp_path / "src"
    accepted_input_path = _write_module(
        source_root,
        "pipy_harness.native.coding.accepted_input",
        f"import {forbidden_import}\n",
    )
    rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.coding.accepted_input"
    )

    violations = _evaluate_rule(source_root, rule)

    assert len(violations) == 1
    assert violations[0].path == accepted_input_path
    assert violations[0].imported_module == forbidden_import


def test_coding_accepted_input_rule_allows_canonical_contracts(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    _write_module(
        source_root,
        "pipy_harness.native.coding.accepted_input",
        """\
from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.loop_policy import AgentToolPolicyState
from pipy_harness.native.agent.messages import AgentUserMessage
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.coding.input_queue import CodingInputQueue
from pipy_harness.native.file_references import FileReferenceResolution
from pipy_harness.native.image_attachment import ImageAttachmentResolution
""",
    )
    rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.coding.accepted_input"
    )

    assert _evaluate_rule(source_root, rule) == []


def test_coding_result_direct_imports_match_explicit_allowlist() -> None:
    result_path = SOURCE_ROOT / "pipy_harness" / "native" / "coding" / "result.py"
    references = _import_references(SOURCE_ROOT, result_path)

    assert not _unallowlisted_direct_imports(
        SOURCE_ROOT,
        result_path,
        allowed_imports=_CODING_RESULT_ALLOWED_DIRECT_IMPORTS,
    ), "shutdown result projection gained a dependency outside its allowlist"
    assert {reference.module for reference in references} == (
        _CODING_RESULT_ALLOWED_DIRECT_IMPORTS
    ), "remove stale allowances when the result projection drops imports"


@pytest.mark.parametrize(
    "forbidden_import",
    (
        "pipy_harness.native.ui",
        "pipy_harness.native.tui",
        "pipy_harness.native.extensions",
        "pipy_harness.native.extension_runtime",
        "pipy_harness.native.automation",
        "pipy_harness.native.coding.commands",
        "pipy_harness.native.coding.product_session",
        "pipy_harness.native.coding.session",
        "pipy_harness.native.provider",
        "pipy_harness.native.tools.bash",
        "pipy_harness.native.openai_provider",
        "pipy_harness.capture",
        "pipy_session",
    ),
)
def test_coding_result_rule_blocks_outer_runtime_imports(
    tmp_path: Path,
    forbidden_import: str,
) -> None:
    source_root = tmp_path / "src"
    result_path = _write_module(
        source_root,
        "pipy_harness.native.coding.result",
        f"import {forbidden_import}\n",
    )
    rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.coding.result"
    )

    violations = _evaluate_rule(source_root, rule)

    assert len(violations) == 1
    assert violations[0].path == result_path
    assert violations[0].imported_module == forbidden_import


def test_coding_result_rule_allows_canonical_contracts(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    _write_module(
        source_root,
        "pipy_harness.native.coding.result",
        """\
from datetime import datetime
from pipy_harness.models import HarnessStatus
from pipy_harness.native.coding.state import CodingSessionResultSnapshot
""",
    )
    rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.coding.result"
    )

    assert _evaluate_rule(source_root, rule) == []


def test_coding_session_controller_direct_imports_match_explicit_allowlist() -> None:
    controller_path = (
        SOURCE_ROOT / "pipy_harness" / "native" / "coding" / "session_controller.py"
    )
    references = _import_references(SOURCE_ROOT, controller_path)

    assert not _unallowlisted_direct_imports(
        SOURCE_ROOT,
        controller_path,
        allowed_imports=_CODING_SESSION_CONTROLLER_ALLOWED_DIRECT_IMPORTS,
    ), "outer-loop controller gained a dependency outside its allowlist"
    assert {reference.module for reference in references} == (
        _CODING_SESSION_CONTROLLER_ALLOWED_DIRECT_IMPORTS
    ), "remove stale allowances when the outer-loop controller drops imports"


@pytest.mark.parametrize(
    "forbidden_import",
    (
        "pipy_harness.native.ui",
        "pipy_harness.native.tui",
        "pipy_harness.native.extensions",
        "pipy_harness.native.extension_runtime",
        "pipy_harness.native.automation",
        "pipy_harness.native.coding.product_session",
        "pipy_harness.native.coding.session",
        "pipy_harness.native.provider",
        "pipy_harness.native.tools.bash",
        "pipy_harness.native.openai_provider",
        "pipy_harness.capture",
        "pipy_session",
    ),
)
def test_coding_session_controller_rule_blocks_outer_runtime_imports(
    tmp_path: Path,
    forbidden_import: str,
) -> None:
    source_root = tmp_path / "src"
    controller_path = _write_module(
        source_root,
        "pipy_harness.native.coding.session_controller",
        f"import {forbidden_import}\n",
    )
    rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.coding.session_controller"
    )

    violations = _evaluate_rule(source_root, rule)

    assert len(violations) == 1
    assert violations[0].path == controller_path
    assert violations[0].imported_module == forbidden_import


def test_coding_session_controller_rule_allows_canonical_contracts(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    _write_module(
        source_root,
        "pipy_harness.native.coding.session_controller",
        """\
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.runtime_ports import AgentQueuedInputPort
from pipy_harness.native.coding.commands import CommandDispatchResolution
from pipy_harness.native.coding.input_queue import CodingInputQueue
from pipy_harness.native.coding.state import CodingSessionState
""",
    )
    rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.coding.session_controller"
    )

    assert _evaluate_rule(source_root, rule) == []


def test_coding_product_session_rule_blocks_outer_runtime_imports(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    product_session_path = _write_module(
        source_root,
        "pipy_harness.native.coding.product_session",
        """\
import pipy_harness.native.agent.messages
import pipy_harness.native.coding.state
import pipy_harness.native.provider
import pipy_harness.native.session_tree
import pipy_harness.native.tui
""",
    )
    product_session_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.coding.product_session"
    )

    assert {
        (violation.path, violation.imported_module, violation.line)
        for violation in _evaluate_rule(source_root, product_session_rule)
    } == {
        (product_session_path, "pipy_harness.native.provider", 3),
        (product_session_path, "pipy_harness.native.session_tree", 4),
        (product_session_path, "pipy_harness.native.tui", 5),
    }


def test_coding_commands_rule_blocks_outer_runtime_imports(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    commands_path = _write_module(
        source_root,
        "pipy_harness.native.coding.commands",
        """\
import pipy_harness.native.agent.content
import pipy_harness.native.coding.state
import pipy_harness.native.export_distribution
import pipy_harness.native.extension_runtime
import pipy_harness.native.project_trust
import pipy_harness.native.settings
import pipy_harness.native.session_tree
import pipy_harness.native.tui
""",
    )
    commands_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.coding.commands"
    )

    assert {
        (violation.path, violation.imported_module, violation.line)
        for violation in _evaluate_rule(source_root, commands_rule)
    } == {
        (commands_path, "pipy_harness.native.coding.state", 2),
        (commands_path, "pipy_harness.native.export_distribution", 3),
        (commands_path, "pipy_harness.native.extension_runtime", 4),
        (commands_path, "pipy_harness.native.project_trust", 5),
        (commands_path, "pipy_harness.native.settings", 6),
        (commands_path, "pipy_harness.native.session_tree", 7),
        (commands_path, "pipy_harness.native.tui", 8),
    }


def test_coding_command_registry_rule_blocks_outer_runtime_imports(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    registry_path = _write_module(
        source_root,
        "pipy_harness.native.coding.command_registry",
        """\
import pipy_harness.native.agent.content
import pipy_harness.native.coding.commands
import pipy_harness.native.coding.state
import pipy_harness.native.export_distribution
import pipy_harness.native.extension_runtime
import pipy_harness.native.project_trust
import pipy_harness.native.session_tree
import pipy_harness.native.tui
""",
    )
    registry_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.coding.command_registry"
    )

    assert {
        (violation.path, violation.imported_module, violation.line)
        for violation in _evaluate_rule(source_root, registry_rule)
    } == {
        (registry_path, "pipy_harness.native.coding.state", 3),
        (registry_path, "pipy_harness.native.export_distribution", 4),
        (registry_path, "pipy_harness.native.extension_runtime", 5),
        (registry_path, "pipy_harness.native.project_trust", 6),
        (registry_path, "pipy_harness.native.session_tree", 7),
        (registry_path, "pipy_harness.native.tui", 8),
    }


def test_coding_state_rule_recursively_blocks_composition_laundering(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    state_path = _write_module(
        source_root,
        "pipy_harness.native.coding.state",
        """\
import pipy_harness.native.agent.messages
import pipy_harness.native.provider
import pipy_harness.native.repl_state
import pipy_harness.native.session_tree
import pipy_harness.native.tool_capabilities
import pipy_harness.native.tui
""",
    )
    state_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.coding.state"
    )

    assert {
        (violation.path, violation.imported_module, violation.line)
        for violation in _evaluate_rule(source_root, state_rule)
    } == {
        (state_path, "pipy_harness.native.repl_state", 3),
        (state_path, "pipy_harness.native.session_tree", 4),
        (state_path, "pipy_harness.native.tool_capabilities", 5),
        (state_path, "pipy_harness.native.tui", 6),
    }


@pytest.mark.parametrize(
    ("intermediate_name", "state_source", "intermediate_symbol", "forbidden_import"),
    (
        (
            "pipy_harness.native.provider",
            "from pipy_harness.native.provider import ProviderPort\n",
            "ProviderPort",
            "pipy_harness.native._provider_helpers",
        ),
        (
            "pipy_harness.native.agent.messages",
            "from pipy_harness.native.agent.messages import AgentMessage\n",
            "AgentMessage",
            "pipy_harness.native.session_tree",
        ),
        (
            "pipy_harness.native.provider",
            "from pipy_harness.native.provider import ProviderPort\n",
            "ProviderPort",
            "pipy_harness.native.chrome",
        ),
    ),
)
def test_coding_state_fresh_graph_detects_allowed_intermediate_laundering(
    tmp_path: Path,
    intermediate_name: str,
    state_source: str,
    intermediate_symbol: str,
    forbidden_import: str,
) -> None:
    source_root = tmp_path / "src"
    for package in (
        "pipy_harness.__init__",
        "pipy_harness.native.__init__",
        "pipy_harness.native.agent.__init__",
        "pipy_harness.native.coding.__init__",
    ):
        _write_module(source_root, package, "")
    state_path = _write_module(
        source_root,
        "pipy_harness.native.coding.state",
        state_source,
    )
    _write_module(
        source_root,
        intermediate_name,
        f"import {forbidden_import}\n\nclass {intermediate_symbol}:\n    pass\n",
    )
    _write_module(source_root, forbidden_import, "")

    assert not _unallowlisted_direct_imports(
        source_root,
        state_path,
        allowed_imports=_CODING_STATE_ALLOWED_DIRECT_IMPORTS,
    )

    script = f"""\
import importlib
import sys

sys.path.insert(0, {str(source_root)!r})
importlib.import_module("pipy_harness.native.coding.state")

forbidden_prefixes = {_CODING_STATE_FORBIDDEN_IMPORTS!r}
unexpected = sorted(
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in forbidden_prefixes
    )
)
assert unexpected == [{forbidden_import!r}], unexpected
"""

    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_coding_input_policy_import_stays_headless_in_a_fresh_process() -> None:
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    forbidden_prefixes = _CODING_FORBIDDEN_IMPORTS
    script = f"""\
import importlib
import sys
import types

def namespace_package(name, path):
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [path]
    sys.modules[name] = module
    return module

pipy_package = namespace_package("pipy_harness", {str(package_root)!r})
native_package = namespace_package("pipy_harness.native", {str(native_root)!r})
pipy_package.native = native_package

module = importlib.import_module("pipy_harness.native.coding.input_queue")
assert hasattr(module, "CodingInputQueue")
forbidden = {forbidden_prefixes!r}
loaded = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
)
assert loaded == [], loaded
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_coding_state_import_stays_headless_in_a_fresh_process() -> None:
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    forbidden_prefixes = _CODING_STATE_FORBIDDEN_IMPORTS
    allowed_prefixes = _CODING_STATE_ALLOWED_RUNTIME_PREFIXES
    allowed_exact = _CODING_STATE_ALLOWED_RUNTIME_EXACT
    script = f"""\
import importlib
import sys
import types

def namespace_package(name, path):
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [path]
    sys.modules[name] = module
    return module

pipy_package = namespace_package("pipy_harness", {str(package_root)!r})
native_package = namespace_package("pipy_harness.native", {str(native_root)!r})
pipy_package.native = native_package

module = importlib.import_module("pipy_harness.native.coding.state")
assert hasattr(module, "CodingSessionState")
forbidden = {forbidden_prefixes!r}
loaded = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
)
assert loaded == [], loaded
allowed_prefixes = {allowed_prefixes!r}
allowed_exact = {allowed_exact!r}
unexpected = sorted(
    name
    for name in sys.modules
    if name.startswith("pipy_harness")
    and name not in allowed_exact
    and not any(
        name == prefix or name.startswith(prefix + ".")
        for prefix in allowed_prefixes
    )
)
assert unexpected == [], unexpected
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_coding_product_session_import_stays_headless_in_a_fresh_process() -> None:
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    # The allowed coding-state intermediate loads its canonical provider/model
    # contracts; the product-session module may not import those directly, but
    # its runtime closure is intentionally the already-gated state closure.
    forbidden_prefixes = _CODING_STATE_FORBIDDEN_IMPORTS
    allowed_prefixes = (
        *_CODING_STATE_ALLOWED_RUNTIME_PREFIXES,
        "pipy_harness.native.coding.product_session",
    )
    allowed_exact = _CODING_STATE_ALLOWED_RUNTIME_EXACT
    script = f"""\
import importlib
import sys
import types

def namespace_package(name, path):
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [path]
    sys.modules[name] = module
    return module

pipy_package = namespace_package("pipy_harness", {str(package_root)!r})
native_package = namespace_package("pipy_harness.native", {str(native_root)!r})
pipy_package.native = native_package

module = importlib.import_module("pipy_harness.native.coding.product_session")
assert hasattr(module, "CodingProductSessionCoordinator")
forbidden = {forbidden_prefixes!r}
loaded = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
)
assert loaded == [], loaded
allowed_prefixes = {allowed_prefixes!r}
allowed_exact = {allowed_exact!r}
unexpected = sorted(
    name
    for name in sys.modules
    if name.startswith("pipy_harness")
    and name not in allowed_exact
    and not any(
        name == prefix or name.startswith(prefix + ".")
        for prefix in allowed_prefixes
    )
)
assert unexpected == [], unexpected
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_coding_commands_import_stays_headless_in_a_fresh_process() -> None:
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    forbidden_prefixes = (
        *_CODING_STATE_FORBIDDEN_IMPORTS,
        "pipy_harness.native.cancellation",
        "pipy_harness.native.coding.product_session",
        "pipy_harness.native.coding.session",
        "pipy_harness.native.coding.state",
        "pipy_harness.native.export_distribution",
        "pipy_harness.native.models",
        "pipy_harness.native.provider",
        "pipy_harness.native.tools",
    )
    allowed_prefixes = (
        "pipy_harness.native.agent",
        "pipy_harness.native.coding.commands",
        "pipy_harness.native.coding.input_queue",
    )
    allowed_exact = frozenset(
        {
            "pipy_harness",
            "pipy_harness.native",
            "pipy_harness.native.coding",
        }
    )
    script = f"""\
import importlib
import sys
import types

def namespace_package(name, path):
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [path]
    sys.modules[name] = module
    return module

pipy_package = namespace_package("pipy_harness", {str(package_root)!r})
native_package = namespace_package("pipy_harness.native", {str(native_root)!r})
pipy_package.native = native_package

module = importlib.import_module("pipy_harness.native.coding.commands")
assert hasattr(module, "CodingCommandOutcome")
forbidden = {forbidden_prefixes!r}
loaded = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
)
assert loaded == [], loaded
allowed_prefixes = {allowed_prefixes!r}
allowed_exact = {allowed_exact!r}
unexpected = sorted(
    name
    for name in sys.modules
    if name.startswith("pipy_harness")
    and name not in allowed_exact
    and not any(
        name == prefix or name.startswith(prefix + ".")
        for prefix in allowed_prefixes
    )
)
assert unexpected == [], unexpected
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_coding_command_registry_import_stays_headless_in_a_fresh_process() -> None:
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    forbidden_prefixes = (
        *_CODING_STATE_FORBIDDEN_IMPORTS,
        "pipy_harness.native.cancellation",
        "pipy_harness.native.coding.product_session",
        "pipy_harness.native.coding.session",
        "pipy_harness.native.coding.state",
        "pipy_harness.native.export_distribution",
        "pipy_harness.native.models",
        "pipy_harness.native.provider",
        "pipy_harness.native.tools",
    )
    allowed_prefixes = (
        "pipy_harness.native.agent",
        "pipy_harness.native.coding.command_registry",
        "pipy_harness.native.coding.commands",
        "pipy_harness.native.coding.input_queue",
    )
    allowed_exact = frozenset(
        {
            "pipy_harness",
            "pipy_harness.native",
            "pipy_harness.native.coding",
        }
    )
    script = f"""\
import importlib
import sys
import types

def namespace_package(name, path):
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [path]
    sys.modules[name] = module
    return module

pipy_package = namespace_package("pipy_harness", {str(package_root)!r})
native_package = namespace_package("pipy_harness.native", {str(native_root)!r})
pipy_package.native = native_package

module = importlib.import_module("pipy_harness.native.coding.command_registry")
assert hasattr(module, "classify_coding_command")
forbidden = {forbidden_prefixes!r}
loaded = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
)
assert loaded == [], loaded
allowed_prefixes = {allowed_prefixes!r}
allowed_exact = {allowed_exact!r}
unexpected = sorted(
    name
    for name in sys.modules
    if name.startswith("pipy_harness")
    and name not in allowed_exact
    and not any(
        name == prefix or name.startswith(prefix + ".")
        for prefix in allowed_prefixes
    )
)
assert unexpected == [], unexpected
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_coding_package_does_not_eagerly_export_headless_layers() -> None:
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    script = f"""\
import types
import sys

def namespace_package(name, path):
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [path]
    sys.modules[name] = module
    return module

pipy_package = namespace_package("pipy_harness", {str(package_root)!r})
native_package = namespace_package("pipy_harness.native", {str(native_root)!r})
pipy_package.native = native_package

import pipy_harness.native.coding

assert "pipy_harness.native.coding.state" not in sys.modules
assert "pipy_harness.native.coding.product_session" not in sys.modules
assert "pipy_harness.native.coding.commands" not in sys.modules
assert "pipy_harness.native.coding.command_registry" not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_tool_loop_provider_access_is_state_backed_across_run_and_settings() -> None:
    session_path = SOURCE_ROOT / "pipy_harness" / "native" / "tool_loop_session.py"
    tree = ast.parse(session_path.read_text(encoding="utf-8"))
    session_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "NativeToolReplSession"
    )
    governed_methods = {
        "run",
        "_settings_overlay_lines",
        "_drive_settings_dialog",
    }
    provider_field_accesses = [
        (method.name, node.lineno)
        for method in session_class.body
        if isinstance(method, ast.FunctionDef) and method.name in governed_methods
        for node in ast.walk(method)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr == "provider"
    ]

    assert provider_field_accesses == []


@pytest.mark.parametrize(
    (
        "source_package",
        "allowed_import",
        "forbidden_import",
        "expected_import",
    ),
    [
        pytest.param(
            "pipy_harness.native.agent",
            "from pipy_harness.native.provider import ProviderPort\n",
            "from pipy_harness.native.openai_provider import OpenAIProvider\n",
            "pipy_harness.native.openai_provider",
            id="agent",
        ),
        pytest.param(
            "pipy_harness.native.providers",
            "from pipy_harness.native.models import ProviderRequest\n",
            "from pipy_harness.native.ui.state import UiState\n",
            "pipy_harness.native.ui.state",
            id="providers",
        ),
        pytest.param(
            "pipy_harness.native.coding",
            "from pipy_harness.native.agent.events import AgentEvent\n",
            "from pipy_harness.native.terminal_screen import TerminalScreen\n",
            "pipy_harness.native.terminal_screen",
            id="coding",
        ),
        pytest.param(
            "pipy_harness.native.ui",
            "from pipy_harness.native.agent.events import AgentEvent\n",
            (
                "from pipy_harness.native.tool_loop_session import "
                "NativeToolReplSession\n"
            ),
            "pipy_harness.native.tool_loop_session",
            id="ui",
        ),
        pytest.param(
            "pipy_harness.native.extensions",
            "from pipy_harness.native.agent.events import AgentEvent\n",
            "from pipy_harness.native.coding.session import CodingSession\n",
            "pipy_harness.native.coding.session",
            id="extensions",
        ),
    ],
)
def test_each_planned_package_boundary_activates_on_a_synthetic_tree(
    tmp_path: Path,
    source_package: str,
    allowed_import: str,
    forbidden_import: str,
    expected_import: str,
) -> None:
    source_root = tmp_path / "src"
    _write_module(source_root, f"{source_package}.allowed", allowed_import)
    forbidden_path = _write_module(
        source_root,
        f"{source_package}.forbidden",
        forbidden_import,
    )
    rule = next(
        rule for rule in ARCHITECTURE_RULES if rule.source_package == source_package
    )

    violations = _evaluate_rule(source_root, rule)

    assert len(violations) == 1
    assert violations[0].path == forbidden_path
    assert violations[0].imported_module == expected_import


def test_rule_is_inactive_until_its_source_package_exists(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    _write_module(
        source_root,
        "pipy_harness.native.tui",
        "from pipy_harness.native.tool_loop_session import NativeToolReplSession\n",
    )
    ui_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.ui"
    )

    assert _evaluate_rule(source_root, ui_rule) == []

    _write_module(
        source_root,
        "pipy_harness.native.ui.terminal",
        "from pipy_harness.native.tool_loop_session import NativeToolReplSession\n",
    )

    violations = _evaluate_rule(source_root, ui_rule)
    assert len(violations) == 1
    assert violations[0].imported_module == "pipy_harness.native.tool_loop_session"


def test_rule_activates_for_single_file_source_module(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    source_path = _write_module(
        source_root,
        "pipy_harness.native.extensions",
        "from pipy_harness.native.tui import ToolLoopTerminalUi\n",
    )
    extensions_rule = next(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package == "pipy_harness.native.extensions"
    )

    violations = _evaluate_rule(source_root, extensions_rule)

    assert len(violations) == 1
    assert violations[0].path == source_path
    assert violations[0].imported_module == "pipy_harness.native.tui"


def test_forbidden_prefix_validation_rejects_stale_legacy_names(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    _write_module(source_root, "example.existing", "VALUE = 1\n")
    rule = BoundaryRule(
        source_package="example.source",
        forbidden_imports=(
            "example.existing",
            "example.future",
            "example.removed_legacy",
        ),
        reason="synthetic rule",
    )

    assert _unresolved_forbidden_imports(
        source_root,
        (rule,),
        planned_imports=frozenset({"example.future"}),
    ) == ["example.removed_legacy"]


def test_repository_forbidden_import_prefixes_are_known() -> None:
    unresolved = _unresolved_forbidden_imports(
        SOURCE_ROOT,
        ARCHITECTURE_RULES,
        planned_imports=_PLANNED_IMPORT_PREFIXES,
    )

    assert not unresolved, (
        "forbidden import prefixes must resolve to source modules/packages or be "
        "explicitly planned; stale entries: " + ", ".join(unresolved)
    )


def test_provider_inventory_discovers_only_top_level_concrete_transports(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    _write_module(
        source_root,
        "pipy_harness.native.example_provider",
        """\
class ExampleProvider:
    def complete(self):
        return None
""",
    )
    _write_module(
        source_root,
        "pipy_harness.native.autocomplete_provider",
        "class AutocompleteSuggestion:\n    pass\n",
    )
    _write_module(
        source_root,
        "pipy_harness.native.nested.hidden_provider",
        """\
class HiddenProvider:
    def complete(self):
        return None
""",
    )

    assert _discover_top_level_concrete_provider_modules(source_root) == (
        "pipy_harness.native.example_provider",
    )


def test_current_provider_ui_boundary_rules_resolve_to_source() -> None:
    discovered_transports = _discover_top_level_concrete_provider_modules(SOURCE_ROOT)
    assert discovered_transports == _LEGACY_CONCRETE_PROVIDER_MODULES, (
        "top-level concrete provider inventory changed without matching boundary "
        "rules; discovered: " + ", ".join(discovered_transports)
    )
    expected_source_order = (
        *discovered_transports,
        *_PROVIDER_UI_SUPPORT_MODULES,
    )
    assert _CURRENT_PROVIDER_UI_BOUNDARY_SOURCES == expected_source_order

    expected_sources = set(expected_source_order)
    current_rules = tuple(
        rule
        for rule in ARCHITECTURE_RULES
        if rule.source_package in expected_sources
        and rule.forbidden_imports == _PROVIDER_UI_FORBIDDEN_IMPORTS
    )

    assert tuple(rule.source_package for rule in current_rules) == (
        expected_source_order
    )
    missing_sources = [
        rule.source_package
        for rule in current_rules
        if not _source_module_or_package_exists(SOURCE_ROOT, rule.source_package)
    ]
    assert not missing_sources, (
        "current provider UI-boundary rules must resolve to source modules: "
        + ", ".join(missing_sources)
    )


def test_repository_architecture_import_boundaries() -> None:
    native_package = SOURCE_ROOT / "pipy_harness" / "native"
    assert SOURCE_ROOT.is_dir() and native_package.is_dir(), (
        "architecture gate requires the real src/pipy_harness/native package layout: "
        f"expected {native_package}"
    )

    violations = [
        violation
        for rule in ARCHITECTURE_RULES
        for violation in _evaluate_rule(SOURCE_ROOT, rule)
    ]

    assert not violations, "architecture import boundary violations:\n" + "\n".join(
        violation.format(SOURCE_ROOT) for violation in violations
    )

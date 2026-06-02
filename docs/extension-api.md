# Pipy Extension API Spec

Status: draft target specification

This document defines the target shape for a future pipy extension API. Pipy
extensions are Python only: pipy does not run Pi's TypeScript extensions. The
target is Pi-shaped semantic compatibility through native Python code, not
source compatibility with Pi's TypeScript runtime. A Pi extension should be easy
to translate into Python because the lifecycle, event names, tool semantics, UI
concepts, and resource/provider hooks feel familiar, while the implementation
fits pipy's native runtime boundaries, metadata-first archive, and
standard-library-first posture.

This is not implemented yet. Current pipy runtime resources are limited to
bounded Markdown skills, prompt templates, custom slash commands, and chrome
themes. Those resources should remain supported when the Python extension API
lands.

## Goals

- Let trusted local Python code extend pipy without forking pipy internals.
- Keep `pipy-native` as the product runtime. Extensions decorate or register
  behavior through explicit ports; they do not replace the native session loop.
- Make common Pi-style workflows possible: permission gates, custom
  model-visible tools, stateful tools, custom slash commands, input transforms,
  context/system-prompt injection, custom compaction, provider/model
  registration, resource discovery, UI notifications/dialogs/widgets, and
  session lifecycle state restoration.
- Intentionally mirror Pi event names and lifecycle concepts where practical,
  while keeping Python APIs idiomatic and pipy-owned.
- Keep extension effects visible and testable through explicit registration
  objects and event hooks.
- Preserve archive privacy. Extension prompts, tool arguments, command output,
  UI text, file contents, and provider payloads must not enter the default
  session archive.

## Non-Goals

- Do not execute remote packages automatically in the first slice.
- Do not load or execute Pi TypeScript extensions. Translation to Python is the
  compatibility story.
- Do not expose internal session objects, recorder handles, terminal renderer
  internals, provider HTTP clients, raw transcript sidecars, or mutable archive
  files to extensions.
- Do not allow extensions to bypass workspace path policy, `.git` default-deny,
  symlink/path-escape checks, output bounds, secret redaction, or provider
  availability gates.
- Do not promise binary compatibility before the API reaches a stable version.
- Do not make extension behavior part of captured metadata beyond safe labels,
  versions, event counts, and policy outcomes.

## Design Principles

- Python modules, typed protocols. The API should use dataclasses, protocols,
  simple callables, and explicit return objects rather than a dynamic global
  object with arbitrary mutation.
- Capability-scoped context. Handlers receive a small context object tailored
  to the event. Trusted local extensions may inspect live runtime data that is
  necessary for the hook, such as tool arguments for a permission gate. They
  still cannot mutate the recorder, raw transcript sidecars, or terminal
  renderer internals.
- Pi-shaped names, Pythonic objects. Prefer Pi's event and API vocabulary
  (`tool_call`, `tool_result`, `before_agent_start`, `register_tool`) over
  pipy-only names when the concept matches. Use snake_case, dataclasses, and
  Python protocols for the actual surface.
- Registration over monkey-patching. Extensions register tools, commands,
  providers, hooks, themes, or UI contributions. They do not patch pipy modules.
- Fail closed. An extension import error, invalid manifest, unsafe resource
  path, duplicate reserved name, or invalid schema disables that extension and
  reports a safe diagnostic.
- Local trust boundary. Python extensions execute with the user's OS
  permissions. The initial API should load only explicit local paths or
  workspace/global extension directories, not arbitrary network sources.

## Discovery

Initial discovery should support:

- Workspace extensions: `.pipy/extensions/<name>/extension.py`
- Workspace single-file extensions: `.pipy/extensions/<name>.py`
- Global extensions:
  `<config>/extensions/<name>/extension.py` and `<config>/extensions/<name>.py`,
  where `<config>` follows the existing pipy config root resolution:
  `PIPY_CONFIG_HOME` -> `${XDG_CONFIG_HOME}/pipy` -> `~/.config/pipy`
- Explicit CLI paths later, probably repeated `--extension <PATH>`

The first implementation should not install dependencies or run package-manager
commands. If an extension needs dependencies, the user is responsible for
making them importable in the active Python environment.

## Manifest

Directory extensions may include `pipy-extension.toml`:

```toml
name = "protected-paths"
version = "0.1.0"
api_version = "0.1"
description = "Block writes to protected paths."

[entry]
module = "extension"
function = "activate"

[permissions]
workspace_read = true
workspace_write = false
shell = false
network = false
ui = true
```

If no manifest exists, pipy may infer:

- name from the file or directory name
- version as `"0.0.0-local"`
- `api_version` as the current draft version
- entry point as `activate`
- all permissions as `false` except registration-only behavior

Permission declarations are descriptive in the first slice and should become
enforceable as extension surfaces grow. They must be recorded only as safe
metadata labels.

## Entry Point

An extension exports a synchronous or asynchronous `activate` function:

```python
from pipy_harness.extensions import PipyExtensionAPI


def activate(api: PipyExtensionAPI) -> None:
    api.register_command(
        name="hello",
        description="Print a local greeting.",
        handler=lambda ctx, args: ctx.ui.notify(f"hello {args or 'world'}"),
    )
```

The function may also be `async def activate(api) -> None`. Pipy should await
async activation before the native session starts.

Activation receives only the extension API. Runtime event handlers receive
event-specific contexts later.

## Core API Shape

The public API should be importable from a stable module, tentatively
`pipy_harness.extensions`. Final placement is still open: implementation should
decide whether the stable surface belongs at top level or under the native
runtime namespace, such as `pipy_harness.native.extensions`.

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol


JsonSchema = Mapping[str, Any]
MessageContent = str | Sequence[Mapping[str, Any]]
ToolContent = Sequence[Mapping[str, Any]]


@dataclass(frozen=True)
class ExtensionTool:
    name: str
    description: str
    input_schema: JsonSchema
    handler: Callable[["ToolCallContext", Mapping[str, Any]], "ToolResult"]


@dataclass(frozen=True)
class ToolResult:
    content: ToolContent
    details: Mapping[str, Any] | None = None
    terminate: bool = False


class PipyExtensionAPI(Protocol):
    def register_tool(self, tool: ExtensionTool) -> None: ...

    def register_command(
        self,
        name: str,
        description: str,
        handler: Callable[["CommandContext", str], object],
    ) -> None: ...

    def register_provider(self, provider: "ExtensionProvider") -> None: ...

    def send_message(
        self,
        message: object,
        options: Mapping[str, Any] | None = None,
    ) -> None: ...

    def send_user_message(
        self,
        content: MessageContent,
        options: Mapping[str, Any] | None = None,
    ) -> None: ...

    def append_entry(
        self,
        entry_type: str,
        data: Mapping[str, Any] | None = None,
    ) -> None: ...

    def get_commands(self) -> Sequence["CommandInfo"]: ...
    def get_all_tools(self) -> Sequence["ToolInfo"]: ...
    def set_active_tools(self, tool_names: Sequence[str]) -> None: ...
    def set_model(self, provider: str, model: str) -> bool: ...
    def on(self, event: str, handler: Callable[..., object] | None = None) -> object: ...
```

Actual implementation should reuse existing native tool contracts where
possible instead of inventing a parallel provider-visible schema format.
Not every method above should land in the first implementation slice. The
target surface should be broad enough for Pi-style extension examples to
translate naturally; implementation should still stage these methods behind
small reviewed slices.
`api.on(...)` should support both direct registration and decorator style:
`api.on("tool_call", handler)` and `@api.on("tool_call")`.

## Event Hooks

Event names should mirror Pi where the lifecycle concept matches. The full
target vocabulary includes:

| Event | Purpose | Allowed return |
| --- | --- | --- |
| `session_start` | Restore per-session extension state after startup, resume, fork, clone, or reload. | None |
| `session_shutdown` | Cleanup local extension state before exit, reload, or session switch. | None |
| `resources_discover` | Contribute additional skills, templates, commands, themes, or extension resource paths. | `ResourceContribution` |
| `input` | Observe or transform submitted user input before a provider turn. | None or `InputTransform` |
| `before_agent_start` | Inject bounded context, alter system-prompt options, or add safe custom messages before a turn. | None or `BeforeAgentStartResult` |
| `agent_start` | Observe an agent run for one accepted user prompt starting. | None |
| `agent_end` | Observe the full agent run ending after turns and tools settle. | None |
| `turn_start` | Observe one model/tool loop turn starting. | None |
| `turn_end` | Observe one model/tool loop turn ending. | None |
| `context` | Observe or contribute bounded context before provider-visible context is finalized. | None or `ContextContribution` |
| `message_start` / `message_update` | Observe assistant/user/tool-result message lifecycle events. | None |
| `message_end` | Observe or replace the finalized message before later lifecycle hooks see it. | None or `MessageTransform` |
| `tool_call` | Inspect, mutate within policy, or block a model-selected tool call before execution. | None or `ToolBlock` |
| `tool_execution_start` | Observe extension or native tool execution starting. | None |
| `tool_execution_update` | Observe bounded progress updates from running tools. | None |
| `tool_execution_end` | Observe extension or native tool execution ending after `tool_result` hooks have produced the finalized result. | None |
| `tool_result` | Observe or transform bounded tool result metadata/content before the next model turn. | None or `ToolResultTransform` |
| `before_provider_request` | Inspect or transform the live in-memory provider request before the provider call. | None or `ProviderRequestTransform` |
| `after_provider_response` | Observe safe provider response metadata after the provider call. | None |
| `model_select` | Observe model/provider changes. | None |
| `thinking_level_select` | Observe thinking/reasoning level changes where the selected provider supports them. | None |
| `session_before_switch` | Observe or block a session switch before the current session is replaced. | None or `SessionSwitchDecision` |
| `session_before_fork` | Observe or block a fork/branch operation before it starts. | None or `SessionForkDecision` |
| `session_before_compact` | Observe or alter compaction settings before compaction. | None or `CompactionRequestTransform` |
| `session_compact` | Observe completed compaction metadata. | None |
| `session_before_tree` | Observe or block tree/session-history navigation before it starts. | None or `SessionTreeDecision` |
| `session_tree` | Observe completed tree/session-history navigation metadata. | None |

The first implementation should still start small. `tool_call` should be the
first policy hook because it enables high-value workflows such as protected
paths and command gating without requiring package loading or rich UI hooks.

```python
@dataclass(frozen=True)
class ToolBlock:
    reason: str


def activate(api):
    @api.on("tool_call")
    async def block_dangerous(event, ctx):
        if event.tool_name == "bash" and "rm -rf" in event.input.get("command", ""):
            if not ctx.has_ui:
                return ToolBlock(reason="dangerous command blocked")
            ok = await ctx.ui.confirm("Dangerous command", "Allow rm -rf?")
            if not ok:
                return ToolBlock(reason="blocked by user")
```

Hook event objects may expose live runtime data to trusted local Python
extensions when the event requires it. For example, `tool_call` must expose the
live tool name and parsed input so permission gates can inspect a bash command
or write path. That live access does not change archive policy: default pipy
records must not store prompts, raw tool inputs, raw tool results, file
contents, provider payloads, secrets, or UI text unless a specific safe
metadata rule allows it.

The same rule applies to provider hooks: a trusted extension may inspect or
transform the live in-memory provider request, but the default archive must
never record provider payloads.

## Commands

Extension commands are local slash commands. They run no provider turn unless
their handler explicitly requests one through a future bounded context method.

Rules:

- Names must be lowercase ASCII identifiers with optional `-`.
- Built-in command names cannot be shadowed.
- Markdown custom commands keep their current precedence and behavior.
- In the product TUI slash menu, extension commands should display safe name
  and description only.
- Command output is live UI output and not archived by default.

Example:

```python
def activate(api):
    def status(ctx, args):
        ctx.ui.notify("extension status: ok")

    api.register_command("ext-status", "Show extension status.", status)
```

## Context

Handlers and tools should receive a context object with Pi-shaped concepts,
implemented as Python attributes and methods. The target context should expose:

- `ctx.cwd`: the workspace root for the active session.
- `ctx.has_ui`: whether interactive UI methods can be used.
- `ctx.ui`: the UI capability object for the active mode.
- `ctx.session`: a read-only session view or append-only extension-state
  capability, not the mutable recorder.
- `ctx.model`: current provider/model selection and safe capability metadata.
- `ctx.signal`: cancellation signal for active turns and tools.
- `ctx.get_system_prompt()`: the current chained system prompt or safe
  system-prompt view for hooks that may affect it.
- `ctx.get_context_usage()`: safe context/window usage counters when known.

Context objects should be mode-aware. In non-interactive modes, UI calls must
return deterministic safe defaults, raise typed unsupported-mode errors, or
emit local diagnostics without blocking forever.

## Tools

Extension tools are model-visible tools that join the bounded tool registry.

Rules:

- Tool names must not shadow built-ins unless a later explicit override policy
  is designed.
- Tool schemas use the same JSON-schema subset as native tools.
- Tool descriptions are model-visible and should support optional
  tool-specific prompt guidance in a later slice.
- Tool handlers receive a workspace-scoped context with safe helper methods.
- Tool handlers receive parsed input, a cancellation signal, and a progress
  callback or sink for partial updates.
- Tool results use Pi-shaped `content` plus `details`, where `content` is the
  provider-visible result and `details` is structured local state/metadata that
  can support rendering, state reconstruction, or later hooks.
- Tool handlers may signal early termination for workflows where the tool
  result is final and no automatic follow-up model turn is needed.
- Raised exceptions become bounded tool errors, not uncaught session crashes.
- Stateful tools should be able to reconstruct safe local state from prior
  result `details` or extension-owned append-only entries.
- Tool results are bounded before returning to the model.
- Tool handlers do not write session archive records directly.
- Shell execution, network access, and writes require explicit future
  permission policy. Until that policy exists, extension tools should be
  read-only or pure transformations.

First-slice candidates:

- pure text transformation tools
- workspace read-only tools that reuse `ReadTool` path policy
- policy hooks that block built-in tools

## UI Surface

The first UI surface should be intentionally narrow:

```python
class ExtensionUi(Protocol):
    def notify(self, message: str, kind: Literal["info", "warning", "error"] = "info") -> None: ...
```

Later phases may add:

- `confirm(title, message) -> bool`
- `select(title, options) -> str | None`
- `input(title, placeholder) -> str | None`
- status/footer labels
- autocomplete providers
- focused custom TUI overlays

Every UI method must have deterministic behavior in non-interactive contexts:
return a safe default, raise a typed unsupported-mode error, or record a safe
local diagnostic without blocking forever.

## Providers And Models

Provider registration should be a later phase. The Pythonic target is not
Pi's `models.json` shape copied verbatim, but a registration object that can
compose with `ProviderPort`:

```python
@dataclass(frozen=True)
class ExtensionProvider:
    name: str
    default_model: str | None
    models: Sequence[str]
    factory: Callable[["ProviderContext"], "ProviderPort"]
```

Provider extensions must not receive existing auth stores wholesale. They
should either read their own environment variables or use a small future auth
capability with explicit provider labels.

## Packages

Package installation should be specified separately before implementation. The
eventual package story should probably support:

- local path packages first
- git/path packages later
- PyPI packages only after a supply-chain policy exists
- lock/pin metadata for non-local sources
- enable/disable resource filtering
- no lifecycle scripts

Unlike Pi, pipy should not assume npm semantics. A Python package may expose
extension entry points through `pyproject.toml`, but pipy should not require
installation into the active environment for local path extensions.

## Archive And Privacy Rules

Default archive records may include:

- extension name, version, and path label
- manifest validation status
- registered command/tool/provider names
- hook names and safe counts
- safe policy outcomes such as "blocked by protected-paths"

Default archive records must not include:

- extension source code
- command arguments that may contain prompts or secrets
- tool arguments beyond existing safe native metadata
- tool results
- UI text
- injected context text
- provider payloads or credentials

Opt-in transcript sidecars remain sensitive content and outside default
catalog/search/inspect surfaces.

## Compatibility And Versioning

The extension API should have an explicit `api_version`, starting at `0.1`.
Rules:

- Minor versions may add methods, events, and optional fields.
- Removing or changing a field requires a new major version.
- Extensions declare the API version they target in the manifest.
- Pipy refuses extensions that target a newer unsupported major version.
- Pipy may warn for old minor versions but should keep them working within the
  same major version.

## Golden Conformance Goal Example

Future implementation work should use a golden Python conformance extension as
the acceptance target for Pi-shaped behavior. This is a goal example suitable
for a future implementation agent:

> Build a Python-only Pipy extension API and a golden conformance extension
> that proves Pi-shaped extension behavior works. The goal is fulfilled when a
> single user trigger runs the extension flow and produces a machine-readable
> proof that every target API feature was exercised, while preserving Pipy's
> metadata-first archive privacy.

Use a deterministic slash command as the trigger:

```text
/pipy-extension-conformance
```

The command is intentionally the trigger because command dispatch should happen
before `input` hooks. To prove both command handling and the normal prompt
lifecycle, the command can record a safe marker and then call
`api.send_user_message("run conformance probe")`.

The first conformance target should exercise at least:

- extension discovery
- `activate(api)`
- `api.register_command`
- `api.register_tool`
- `api.on("session_start")`
- `api.on("session_shutdown")`
- `api.on("input")`
- `api.on("before_agent_start")`
- `api.on("agent_start")`
- `api.on("turn_start")`
- `api.on("tool_call")`
- `api.on("tool_result")`
- `api.on("turn_end")`
- `api.on("agent_end")`
- `ctx.cwd`
- `ctx.has_ui`
- `ctx.ui.notify`
- command handler execution
- tool execution
- tool result `content`
- tool result `details`
- safe proof logging without archive leakage

The proof mechanism should write safe feature markers to a test-controlled
JSONL file named by an environment variable, for example:

```sh
PIPY_EXTENSION_CONFORMANCE_PROOF=/tmp/pipy-extension-proof.jsonl
```

Each line must be metadata-only. Example markers:

```json
{"feature":"session_start","ok":true}
{"feature":"command_handler","ok":true}
{"feature":"input","source":"extension","ok":true}
{"feature":"before_agent_start","system_prompt_modified":true}
{"feature":"tool_call","tool_name":"conformance_probe","ok":true}
{"feature":"tool_execute","details_written":true}
{"feature":"tool_result","patched":true}
{"feature":"agent_end","ok":true}
{"feature":"session_shutdown","ok":true}
```

The proof file must not contain prompts, tool arguments, model output, file
contents, provider payloads, UI text, secrets, credentials, or sensitive
personal data.

The conformance flow should be deterministic and should not rely on a real
model choosing the extension tool:

1. User submits `/pipy-extension-conformance`.
2. Command handler records `command_handler`.
3. Command handler calls `send_user_message("run conformance probe")`.
4. `input` hook records `input`.
5. `before_agent_start` modifies the system prompt or injects safe context.
6. Fake or programmed provider calls `conformance_probe`.
7. `tool_call` hook records seeing the call.
8. Tool executes and returns `content` plus `details`.
9. `tool_result` hook patches the result.
10. Agent ends and records `agent_end`.
11. Session shutdown or reload proves cleanup through `session_shutdown`.

Acceptance criteria:

- one product-path test runs `/pipy-extension-conformance`;
- proof JSONL contains every required feature marker at least once;
- the registered extension tool is visible to the tool loop;
- the registered command appears in command discovery and menu surfaces where
  applicable;
- `tool_result` patch reaches the model-visible observation;
- `before_agent_start` modification reaches the provider request;
- `ctx.ui.notify` is called or safely degrades in non-interactive mode;
- the default pipy session archive contains no prompt bodies, tool arguments,
  tool results, UI text, provider payloads, or proof-file contents.

Suggested future task wording:

> Implement the first Python-only Pipy extension conformance slice. Do not
> support TypeScript. Add a Pi-shaped Python extension API and a golden
> conformance extension fixture. A single `/pipy-extension-conformance` trigger
> must exercise command registration, tool registration/execution, lifecycle
> events, input handling, before-agent-start context modification, tool-call and
> tool-result hooks, minimal UI notification, and shutdown cleanup. Write safe
> feature markers to a test proof JSONL file and add product-path tests
> asserting all markers are present and archive privacy is preserved.

## Suggested Implementation Slices

1. Docs and contracts only: this spec, backlog links, and no runtime behavior.
2. Discovery and manifest parser: find local extensions, validate manifests,
   record safe loaded/disabled metadata, but do not execute extension code.
3. Activation sandbox boundary: import explicit local modules, call
   `activate(api)`, support command registration only, and pin failure modes.
4. Command dispatch: run extension slash commands in both REPL product paths
   with safe diagnostics and no provider turn by default.
5. `tool_call` policy hook: allow extensions to inspect live parsed tool inputs
   and block built-in tool calls with safe reasons.
6. Lifecycle foundation: emit `session_start`, `session_shutdown`,
   `agent_start`, `turn_start`, `turn_end`, and `agent_end` with mode-aware
   contexts and safe archive metadata only.
7. Input and before-agent-start hooks: support `input` transforms,
   `before_agent_start` context/system-prompt modifications, and
   `send_user_message(...)` enough for a command to trigger a deterministic
   provider turn.
8. Pure/read-only tool registration: add extension tools to the bounded tool
   loop using existing JSON-schema validation and output bounds.
9. Tool result hooks: support Pi-shaped tool `content` plus `details`,
   bounded progress/update events, `tool_result` transforms, and deterministic
   propagation of transformed observations to the model.
10. Minimal UI notifications: expose `ctx.ui.notify` with deterministic
   non-interactive behavior.
11. Golden conformance extension: add the `/pipy-extension-conformance`
   fixture and product-path proof test after the command, lifecycle, input,
   before-agent-start, tool registration, tool-call, tool-result, agent-end,
   and minimal UI slices exist.
12. Provider registration and package installation only after the previous
   slices have review coverage and a security model.

## Open Questions

- Should extensions be enabled by default when discovered in the workspace, or
  require an explicit allowlist?
- Should project-local extensions be considered trusted when the workspace is a
  cloned repository?
- What is the right packaging format for shareable Python extensions:
  local-path manifests, Python entry points, git refs, PyPI packages, or a
  pipy-specific package index?
- How should extension state be stored without creating a second transcript or
  leaking prompts?
- Which UI hooks are valuable enough to expose before the full TUI is more
  stable?
- Should discovered extensions support in-session reload, and if so how should
  reload interact with fail-closed import errors, registered hooks, live tools,
  and already-running provider turns?

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

Implementation status: **partially implemented.** Slices 1–16 plus the first
package-source/update follow-on have landed (see "Suggested Implementation
Slices"), including **package runtime composition** — installed local-path and
managed git package resources (extensions/skills/prompts/themes) now flow
through discovery at lowest precedence with Pi-shaped enablement filters.
The pre-existing pipy runtime resources (bounded Markdown skills, prompt
templates, custom slash commands, and chrome themes) remain supported alongside
the Python extension API.

Comparability to Pi: Pipy is now **Pi-shaped for core local extension workflows,
but not Pi-equivalent as an extension platform**. The landed API is enough for
translated Python versions of common Pi patterns such as permission gates,
simple custom slash commands, simple model-visible tools, input transforms,
before-agent-start prompt/context injection, lifecycle observers, tool-result
patching, and basic provider experiments. A later block of interactive
command-context capabilities also landed (used by the `answer.py` example, a
port of Pi's `answer.ts`): `ctx.conversation.last_assistant_message()`, a
bounded one-shot `ctx.complete(system_prompt, user_text)`, a full-screen
custom interactive overlay `ctx.ui.custom(...)`, Pi-shaped simple UI primitives
(`ctx.ui.select`, `ctx.ui.input`, `ctx.ui.confirm`, `ctx.ui.set_status`,
`ctx.ui.set_working_message`, `ctx.ui.set_working_visible`), and
keyboard-shortcut registration `api.register_shortcut(...)`. It is not
source-compatible with Pi's TypeScript extensions, and it still lacks several
mature Pi surfaces: richer multi-widget TUI,
extension state/session-manager helpers, remote PyPI/npm package distribution,
and broader package ecosystem polish. A first custom session-entry/message
rendering slice has landed: extensions can register a text renderer for a custom
entry type and command/shortcut handlers can append JSON-safe custom entries to
the native product session tree. Extensions can also render their own tool
call/result rows with themed color (`render_call`/`render_result`).
Session switch/fork/tree/
compaction interception, dynamic active-tool/model/thinking controls,
`user_bash`, and `before_provider_request` provider-payload hooks now ship as a
live-session follow-on slice. Per-run source-loading flags for extensions,
skills, prompt templates, and themes have landed. A first dynamic extension flag
slice also ships for `pipy repl` tool-loop runs: extensions register
boolean/string `ExtensionFlag` objects, matching unknown CLI tokens are parsed
after activation, and commands/tools/hooks read the run-local values from
`ctx.flags`. (Package runtime composition for local-path and managed git
packages has landed.)

## Goals

- Let trusted local Python code extend pipy without forking pipy internals.
- Keep `pipy-native` as the product runtime. Extensions decorate or register
  behavior through explicit ports; they do not replace the native session loop.
- Make common Pi-style workflows possible: permission gates, custom
  model-visible tools, stateful tools, custom slash commands, keybindings/
  shortcuts, CLI flags, custom message renderers, input transforms,
  context/system-prompt injection, custom compaction, provider/model
  registration and unregistration, resource discovery, UI
  notifications/dialogs/widgets/status, and session lifecycle state
  restoration.
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
  keybindings/shortcuts, CLI flags, message renderers, providers, hooks, or UI
  contributions, and contribute themes through the `resources_discover` hook.
  They do not patch pipy modules. (Like Pi, there is no theme-registration API;
  themes are contributed as `theme_paths`, mirroring Pi's `themePaths`.)
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
- Explicit CLI paths: repeated `--extension <PATH>` accepts a Python file, a
  direct extension directory, or a directory of extension candidates. Matching
  `--no-extensions` disables workspace/global/package discovery but still
  honors explicit paths, and explicit paths also override persisted resource
  filters for the same resource name. The same per-run pattern exists for
  `--skill`, `--prompt-template`, and `--theme`.

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


@dataclass(frozen=True)
class ExtensionFlag:
    name: str
    flag_type: Literal["boolean", "string"]
    description: str | None = None
    default: bool | str | None = None


class PipyExtensionAPI(Protocol):
    def register_tool(self, tool: ExtensionTool) -> None: ...

    def register_command(
        self,
        name: str,
        description: str,
        handler: Callable[["CommandContext", str], object],
    ) -> None: ...

    def register_shortcut(
        self,
        shortcut: str,
        handler: Callable[["ShortcutContext"], object],
        description: str | None = None,
    ) -> None: ...

    def register_flag(self, flag: "ExtensionFlag") -> None: ...

    def register_message_renderer(
        self,
        custom_type: str,
        renderer: Callable[..., object],
    ) -> None: ...

    def register_provider(self, provider: "ExtensionProvider") -> None: ...

    def unregister_provider(self, name: str) -> None: ...

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

    # Runtime handlers append entries through `ctx.append_entry(...)`; the
    # activation-time API only registers the renderer.

    def get_commands(self) -> Sequence["CommandInfo"]: ...
    def get_all_tools(self) -> Sequence["ToolInfo"]: ...
    def on(self, event: str, handler: Callable[..., object] | None = None) -> object: ...


class CommandContext(Protocol):
    cwd: str
    has_ui: bool
    ui: "ExtensionUi"
    conversation: "ConversationView"
    flags: Mapping[str, object]

    def complete(self, system_prompt: str, user_text: str) -> str: ...
    def set_active_tools(self, tool_names: Sequence[str]) -> bool: ...
    def set_model(self, reference: str) -> bool: ...
    def set_thinking_level(self, level: str) -> bool: ...
    def append_entry(self, custom_type: str, data: object | None = None) -> object: ...
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
| `session_start` | Restore per-session extension state after startup, new, resume, fork, or reload. (Pi `SessionStartEvent.reason` is `"startup" \| "reload" \| "new" \| "resume" \| "fork"`.) | None |
| `session_shutdown` | Cleanup local extension state before exit, reload, or session switch. | None |
| `resources_discover` | Contribute additional skills, templates, commands, themes, or extension resource paths. | `ResourceContribution` |
| `input` | Observe or transform submitted user input before a provider turn. | None or `InputTransform` |
| `user_bash` | Observe or gate a user-run bash command entered via Pi's `!`/`!!` prefix, including whether it is excluded from model context. | None or `UserBashDecision` |
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
| `session_before_switch` | Observe or block a session switch before the current session is replaced. | None or `SessionDecision` |
| `session_before_fork` | Observe or block a fork/branch operation before it starts. | None or `SessionDecision` |
| `session_before_compact` | Observe or block compaction before it starts. | None or `SessionDecision` |
| `session_compact` | Observe completed compaction metadata. | None |
| `session_before_tree` | Observe or block tree/session-history navigation before it starts. | None or `SessionDecision` |
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

The full target UI surface should mirror Pi's `ExtensionUIContext`, which is
much richer than a single notification method. It is intentionally a target,
staged behind later slices, but the target shape should cover Pi's UI concepts
so Pi extensions translate naturally:

- Dialogs and prompts: `confirm(title, message) -> bool`,
  `select(title, options) -> str | None`,
  `input(title, placeholder) -> str | None`, and a multi-line
  `editor(title, prefill) -> str | None`.
- Status and indicators: `set_status(key, text)`, working-message and
  working-indicator controls, and a hidden-thinking label, mirroring Pi's
  `setStatus` / `setWorkingMessage` / `setWorkingIndicator` /
  `setHiddenThinkingLabel`.
- Widgets and chrome: `set_widget(key, content, options)` above/below the
  editor, plus custom header/footer/title (Pi's `setWidget` / `setFooter` /
  `setHeader` / `setTitle`).
- Editor integration: read/write editor text, paste into the editor, and a
  fully custom editor component (Pi's `getEditorText` / `setEditorText` /
  `pasteToEditor` / `setEditorComponent`).
- Custom overlays: a focused custom component with keyboard focus, mirroring
  Pi's `custom(...)` overlay.
- Autocomplete: stack an additional autocomplete provider on top of the
  built-in one, mirroring Pi's `addAutocompleteProvider`.
- Theme controls: read the active theme, list available themes, load a theme
  by name, and switch themes, mirroring Pi's `theme` / `getAllThemes` /
  `getTheme` / `setTheme`. (This is theme selection, not theme registration;
  themes are contributed via `resources_discover`.)
- Raw terminal input subscription in interactive mode, mirroring Pi's
  `onTerminalInput`.

Every UI method must have deterministic behavior in non-interactive contexts:
return a safe default, raise a typed unsupported-mode error, or record a safe
local diagnostic without blocking forever.

## Providers And Models

Provider registration now ships through pipy's native catalog. The Pythonic
surface is not Pi's `models.json` shape copied verbatim, but a registration
object that composes with `ProviderPort`:

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

Mirroring Pi, the provider surface supports both registration and
`unregister_provider(name)`. Registered providers contribute temporary per-run
catalog rows, appear in `--list-models` and `/model`, resolve at startup when
their extension is loaded, and construct through the registered `ProviderPort`
factory. `/reload` recomputes those contributions from current extension
discovery, so removed/disabled extension providers disappear. Unregister hides
the extension contribution and restores any built-in rows it overrode without
mutating the built-in catalog or `models.json`.

## Dynamic CLI Flags

Dynamic extension flags now ship for the tool-loop `pipy repl` product path.
An extension registers boolean or string flags with `ExtensionFlag`:

```python
from pipy_harness.extensions import ExtensionFlag


def activate(api):
    api.register_flag(ExtensionFlag("plan", "boolean", default=False))
    api.register_flag(ExtensionFlag("ticket", "string"))
```

Flag names use the same simple name syntax as extension commands and must be
unique across active extensions. Bad flag declarations or duplicate flag names
disable the registering extension without committing partial registrations.
Defaults must match the flag type: `bool` for boolean flags and `str` for string
flags.

At startup, pipy keeps normal argparse handling for built-in flags, passes only
the remaining unknown tokens into the activated extension runtime, validates
them against registered extension flags, and fails before the provider turn on
unknown or malformed tokens. Parsed values are available as `ctx.flags` in
extension commands, keyboard shortcuts, model-visible tools, and hook contexts.

Supported forms are `--flag`, `--flag=true|false`, `--name value`, and
`--name=value`. For one-shot `--print`/`--mode json` runs that also need a
positional prompt, prefer `--name=value`; the space-separated string form is
ambiguous with the prompt positional. Dynamic flags apply only to `pipy repl`
(the product tool-loop session); top-level `run` and the package-manager
commands remain strict and do not consume extension flags.

`/reload` reactivates extensions and reparses the original per-run extension
flag tokens. If the reloaded extension set no longer accepts those tokens, pipy
reports the flag error and keeps the last valid flag values for the existing
session instead of clearing them mid-run.

## Packages And Package-Manager CLI

Pi's extension platform includes a package manager, not just an extension loader.
The local reference implements it in `core/package-manager.ts`,
`package-manager-cli.ts`, and the settings/resource-discovery stack. Pipy's
Python package story must match the user-visible behavior while choosing
Python-native source kinds and staying stdlib-only.

### Source kinds and scopes

Pipy package sources should support these stages:

- **Local path** sources first: directories or files on disk, resolved relative
  to the current workspace for project operations. These are the first trusted
  source kind and require no installer.
- **Git** sources: HTTPS/git/file URLs and SSH scp-style sources behind the
  `git:` prefix (for example `git:git@host:owner/repo`) are cloned into pipy's
  managed package cache (`<config>/git` for user scope, `.pipy/git` for project
  scope) and updated through bounded fetch/reset. Runtime startup never clones
  or fetches; it only reads an already installed cache path from the configured
  scope, so a user package is not shadowed by a same-source project cache.
  Ref-pinned sources are reconciled to the configured ref. Credentialed URL
  userinfo, including `ssh://git@host/...`, is rejected rather than displayed or
  stored.
- **Python package / PyPI-style** sources only after a supply-chain policy is
  written. They must be installed into an isolated package cache or explicitly
  documented environment, never silently into the user's active project.
- A **temporary** scope for explicit CLI-loaded sources that applies to one run
  and is never persisted, matching Pi's temporary extension-source behavior.
- Persisted scopes: **user** (`<config>/...`) and **project** (`.pipy/...`),
  matching Pi's user/project split.

Unlike Pi, pipy should not assume npm semantics or lifecycle scripts. Pipy must
not run package lifecycle hooks automatically. A Python package may expose
extension entry points through a manifest or `pyproject.toml`, but local path
extensions must work without installing dependencies.

### Settings representation

Package and resource enablement belongs in the settings system specified in
`settings-config.md`:

- top-level arrays: `packages`, `extensions`, `skills`, `prompts`, `themes`;
- `PackageSource` may be a string source or an object `{source, extensions,
  skills, prompts, themes}` to filter resources from that package;
- enable/disable is represented with Pi-shaped `+pattern` / `-pattern` entries
  and package filters, **not** by deleting discovered resources;
- project writes go to `.pipy/settings.json`; user writes go to
  `<config>/settings.json`.

Resource discovery should resolve package resources after user/project local
resources, preserving Pi-like precedence: project explicit/local, project auto,
user explicit/local, user auto, then package resources. Built-in command names
cannot be shadowed by package commands.

### CLI surface

Implement Pi-shaped commands at the top-level `pipy` command:

```text
pipy install <source> [-l|--local]
pipy remove <source> [-l|--local]
pipy uninstall <source> [-l|--local]
pipy list
pipy config
pipy update <source>|--extensions|--extension <source> [--force]  # package-update target
```

`pipy update self|pipy [--force] [--dry-run]` ships as the
install-method-aware self-update surface in
[export-distribution.md](export-distribution.md). The package half now supports
managed git sources plus local-path no-op updates.

Behavior targets:

- `install <source>` installs/resolves the source, records it in user settings,
  or in project settings with `-l/--local`, and prints `Installed <source>`.
- `remove`/`uninstall` removes the matching configured source from the chosen
  settings scope and prints `Removed <source>`; when no source matches, it exits
  non-zero with a clear diagnostic.
- `list` prints configured user and project packages. Empty configuration prints
  a dim/no-packages message equivalent to Pi's `No packages installed.`.
- `config` opens the resource enable/disable selector when a TTY is available
  and also provides a non-interactive `--json`/flag surface for tests and
  automation. It edits settings filters, not package files.
- Bare `update` updates both installed extensions/packages and pipy itself,
  matching Pi's "all" target. `update self` / `update pipy` updates pipy only;
  `--extensions` updates packages only; `update <source>` or
  `--extension <source>` updates one package. `--force` forces self reinstall
  even when already current. The self-update half is specified in
  `export-distribution.md`; this extension spec owns the package half.
- Every subcommand supports `--help` and rejects invalid/conflicting options
  with non-zero exit status and a usage line.

### Install/update mechanics

Package resolution should be explicit and testable:

- local path packages are canonicalized, must stay within the requested source
  path, and are marked so cloud-sync recipes can ignore installed package
  caches when appropriate;
- git packages clone into a pipy package cache under local state/config, use a
  short timeout, and update through a bounded pull/fetch path;
- missing sources during normal startup fail closed with a safe diagnostic, but
  the package manager may support an explicit `onMissing` policy for install or
  skip during `resolve()`;
- package updates are explicit and scriptable: local paths are skipped, managed
  git sources fetch/reset in the cache, and unsupported remote sources fail
  closed;
- package manifests can contribute extensions, skills, prompts/templates, and
  themes. Pipy's manifest shape should be Python-native but map to Pi's
  `package.json` `pi.{extensions,skills,prompts,themes}` capability.

### Runtime composition

Installed package resources flow through the same runtime boundaries as local
resources:

- extension entry points load through the Python extension activation boundary;
- skills/templates/custom commands go through `pipy_harness.native.resources`;
- themes go through the theme registry and settings selection;
- provider registrations go through the provider catalog
  `register_provider`/`unregister_provider` boundary;
- package resources are included in `/reload` and `pipy config` discovery.

### Package conformance gate

Extend the future extension conformance gate, or add a dedicated package gate:

```sh
uv run python scripts/parity_checks/extension_package_conformance.py --json
```

The gate should use temporary config/workspace/package-cache directories and no
real network. It must prove:

1. local path package install persists the source to user and project settings;
2. package manifests contribute an extension, a skill, a prompt/template, and a
   theme, and resource precedence is deterministic;
3. `list` reports user/project packages and an empty state correctly;
4. `config` writes `+pattern`/`-pattern` filters and those filters affect
   runtime discovery;
5. `remove`/`uninstall` removes only the selected source/scope;
6. `update --extensions`, `update <source>`, `--extension <source>`, and bare
   `update` choose the right package targets; dry-run does not execute network
   operations, and the gate uses a local file-backed git remote for the real
   update path;
7. invalid/conflicting options and missing sources fail closed with usage;
8. no package source path, token, command output, extension code, prompt body,
   tool payload, or UI text leaks into the default metadata archive.
9. explicit CLI source-loading paths for extensions, skills, prompt templates,
   and themes still load when matching default discovery or persisted filters
   are disabled.

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

Slices 1 and 2 have **landed**. Slice 1:
`pipy_harness.native.extensions.discover_extensions` implements the inventory
boundary (`tests/test_native_extension_discovery.py`, gate
`scripts/parity_checks/extension_discovery_conformance.py --json`). Slice 2:
`pipy_harness.native.extension_runtime.activate_extensions` imports only
loadable descriptors and runs `activate(api)` with `register_command` support
via the public `pipy_harness.extensions.PipyExtensionAPI`, failing closed per
extension (`tests/test_native_extension_activation.py`, gate
`scripts/parity_checks/extension_activation_conformance.py --json`). Slice 3:
extension `/commands` dispatch through the live tool-loop REPL
(`dispatch_extension_command`, gate
`scripts/parity_checks/extension_dispatch_conformance.py --json`). Slice 4: the
`tool_call` policy hook (`api.on("tool_call")` → `ToolBlock`, gate
`scripts/parity_checks/extension_tool_call_conformance.py --json`). Slice 5: the
lifecycle event foundation (`session_start`/`agent_start`/`turn_start`/
`turn_end`/`agent_end`/`session_shutdown`, gate
`scripts/parity_checks/extension_lifecycle_conformance.py --json`). Slices 1–13
have **landed** (gate
`scripts/parity_checks/extension_live_session_conformance.py --json` plus the
earlier package gate
`scripts/parity_checks/extension_package_conformance.py --json`), including
**package runtime composition** — installed local-path and managed-git package
resources flow through discovery at lowest precedence (see slice 12 below) —
plus the follow-on per-run source-loading flags for explicit extensions, skills,
prompt templates, and themes. Discovery never imports extension code; activation
imports only loadable descriptors.

Beyond the first numbered slices, an **interactive command-context block** has also
landed to support porting Pi's `answer.ts`
(`docs/examples/extensions/answer.py`): the command/shortcut context now exposes
`ctx.conversation.last_assistant_message()` (read-only), a bounded one-shot
`ctx.complete(system_prompt, user_text)` on the active provider, a full-screen
`ctx.ui.custom(factory)` interactive overlay (`ToolLoopTerminalUi.run_custom_component`),
and `api.register_shortcut(key, handler)` keyboard shortcuts. The simple UI
primitive follow-on then added `ctx.ui.select`, `ctx.ui.input`,
`ctx.ui.confirm`, `ctx.ui.set_status`, `ctx.ui.set_working_message`, and
`ctx.ui.set_working_visible`: interactive runs delegate to the product TUI
(simple overlays, live status rows, and working-label/visibility controls),
while non-interactive runs return cancel/default values and record status
deterministically without blocking. These are covered by
`tests/test_native_extension_{conversation,completion,custom_ui,custom_ui_pty,shortcuts}.py`,
`tests/test_native_tool_loop_tui.py`, `tests/test_example_answer_extension.py`,
and the live `scripts/tmux_answer_verify.sh`.

1. Discovery and manifest inventory (no execution) — **landed**: find
   workspace/global local Python extension candidates, parse optional
   `pipy-extension.toml` manifests, infer safe defaults, validate names/API
   versions/permissions/source paths, record safe loadable/disabled descriptors
   and reason codes, and prove that top-level side effects in extension source
   files never run. Implemented as `ExtensionDescriptor` +
   `discover_extensions` + `safe_extension_metadata`.
2. Activation sandbox boundary — **landed**: import explicit local modules, call
   `activate(api)` (sync or async), support command registration only, and pin
   failure modes, duplicate registration behavior, and safe diagnostics.
   Implemented as `pipy_harness.native.extension_runtime.activate_extensions` +
   `PipyExtensionAPI` + `ActivatedExtension`, with the public
   `pipy_harness.extensions` surface.
3. Command dispatch — **landed**: run extension slash commands in the tool-loop
   REPL product path with safe diagnostics and no provider turn by default,
   after built-ins/custom commands (no shadowing), with menu listing and
   `/reload` re-activation. Implemented as
   `pipy_harness.native.extension_runtime.dispatch_extension_command` +
   `extension_command_map` + a mode-aware `CommandContext`/`ExtensionUi`, wired
   into `tool_loop_session`.
4. `tool_call` policy hook — **landed**: extensions register
   `@api.on("tool_call")` to inspect live parsed tool inputs and block built-in
   tool calls with safe reasons. Implemented as `api.on(...)` +
   `ToolBlock`/`ToolCallEvent` + `dispatch_tool_call_hooks`/`extension_tool_call_hooks`,
   wired into the `tool_loop_session` tool loop (first block wins; crashing hook
   fails closed; raw inputs inspected live but not archived).
5. Lifecycle foundation — **landed**: emit `session_start`, `session_shutdown`,
   `agent_start`, `turn_start`, `turn_end`, and `agent_end` with mode-aware
   contexts and safe archive metadata only. Implemented as `LifecycleEvent` +
   `dispatch_lifecycle_hooks` + `extension_event_hooks`, fired through an
   `_ExtensionAwareEmitter` (observe-only, fail-soft) wired into
   `tool_loop_session`.
6. Input and before-agent-start hooks — **landed**: support `input` transforms,
   `before_agent_start` context/system-prompt modifications, and
   `send_user_message(...)` enough for a command to trigger a deterministic
   provider turn. Implemented as `InputEvent`/`InputTransform`,
   `BeforeAgentStartEvent`/`BeforeAgentStartResult`, `QueuedUserMessage`,
   `dispatch_input_hooks`/`dispatch_before_agent_start_hooks`,
   `api.send_user_message` + `drain_user_messages`, wired into the
   `tool_loop_session` prompt/turn path.
7. Pure/read-only tool registration — **landed**: add extension tools to the
   bounded tool loop using existing JSON-schema validation and output bounds.
   Implemented as `ExtensionTool`/`ToolResult`/`RegisteredTool` +
   `api.register_tool` + `extension_tools` + the `_ExtensionToolPort` adapter
   wired into `tool_loop_session`'s per-run tool registry.
8. Tool result hooks — **landed** (`tool_result` transforms): an extension
   `@api.on("tool_result")` handler transforms the bounded observation
   (`ToolResultEvent`/`ToolResultTransform` + `dispatch_tool_result_hooks`)
   before the model sees it, chained + fail-safe + bounded. (Pi-shaped
   `content`+`details` blocks and bounded progress/update events remain a
   later refinement.)
9. Minimal UI notifications — **landed**: `ctx.ui.notify` from a command or
   hook handler surfaces to the live UI via a notify sink threaded through the
   dispatchers / tool adapter / `_ExtensionAwareEmitter`; deterministic
   (records + sink) in non-interactive mode.
10. Golden conformance extension — **landed**: the golden
    `docs/examples/extensions/pipy-extension-conformance.py` + product-path proof
    test (`tests/test_native_extension_conformance.py`) + gate
    (`scripts/parity_checks/extension_conformance_gate.py --json`). A single
    `/pipy-extension-conformance` trigger writes all 12 feature markers and the
    proof leaks no prompt/tool/UI bodies.
11. Provider registration — **landed**: `api.register_provider`/
    `api.unregister_provider` + `ExtensionProvider`/`ProviderContext`/
    `RegisteredProvider` + `build_extension_provider_port` compose a factory into
    a `ProviderPort` (staged/committed, duplicate/invalid disable, bounded
    factory failures). Registered providers now wire into the native catalog,
    startup resolver, `--list-models`, `/model`, and `/reload` as temporary
    per-run rows, with no extension source paths or provider payloads archived.
12. Package install/list/config CLI **and runtime composition** — **landed
    (local-path + managed git scope)**. The CLI half is implemented as
    `pipy_harness.native.package_manager` wired into `pipy_harness.cli`: `pipy
    install/remove/uninstall [-l]` and `pipy list` manage package
    sources in a `packages` array in user/project `settings.json` (preserving
    object-form `{source, ...}` entries), and `pipy config <enable|disable>
    <skill|prompt|theme|extension> <name>` writes `+pattern`/`-pattern` resource
    filters (never deleting discovered resources). Local paths must exist;
    supported git URLs clone into the managed package cache and are refreshed by
    `pipy update --extensions`, `pipy update <source>`, or `pipy update
    --extension <source>`. `git+`/`npm:`/PyPI/credentialed or ambiguous remote
    sources are rejected, a corrupt settings file is never clobbered, and no
    package lifecycle scripts run. **Runtime composition** resolves configured
    local-path sources and installed git caches into per-kind resource roots
    (`pipy_harness.native.package_resources.resolve_package_roots`, from an
    optional `pipy-package.toml` manifest mapping Pi's
    `pi.{extensions,skills,prompts,themes}`, or convention subdirs), composed
    once per session via `package_runtime.compose_package_runtime`: package
    skills/prompts flow through `WorkspaceResources.discover(package_roots=...)`,
    extensions through `discover_extensions(package_roots=...)`, and themes
    through a file-based loader + overlay registry
    (`theme_files.build_theme_registry` + `themes.set_active_theme_registry`) so
    a package theme becomes selectable via the `/settings` Theme picker (or
    `PIPY_THEME` / settings) and re-colors the chrome. All four
    kinds sit at lowest precedence (a workspace/global resource wins a name
    collision) and honor the `+/-pattern` filters; package resources are
    included in `/reload` and `pipy config` discovery. The example package lives
    at `docs/examples/packages/demo-pack/` with a live tmux proof
    (`scripts/tmux_package_verify.sh`). The package gate proves spec "Package
    conformance gate" items 2/4/8 (manifest contributes an
    extension/skill/prompt/theme with deterministic precedence; filters affect
    discovery; no source path or resource body leaks into safe metadata). The
    same gate now also proves per-run source-loading flags for explicit
    extension, skill, prompt-template, and theme paths, plus managed git
    install/update through a local file-backed remote and package-update
    dry-run target selection. Remote PyPI/npm source handling remains deferred
    until a broader supply-chain policy exists. Gate
    `scripts/parity_checks/extension_package_conformance.py --json`.
13. Live-session hooks and dynamic controls — **landed**: Pi-shaped
    `user_bash`, `before_provider_request`, `session_before_switch`,
    `session_before_fork`, `session_before_compact`, and `session_before_tree`
    hooks now dispatch from the product tool loop. Command/shortcut and safe
    pre-turn hook contexts expose `ctx.set_active_tools(...)`,
    `ctx.set_model(...)`, and `ctx.set_thinking_level(...)` through the
    existing provider-state, session-tree, and tool-registry boundaries; in-turn
    provider/tool hooks reject `ctx.set_model(...)` by returning `False` so they
    cannot clear conversation state mid-turn. `ctx.set_active_tools([])` is a
    real empty active-tool set, disabling model-visible tools until a later
    context selects known tool names again. `user_bash` hooks can block,
    rewrite, exclude, or synthesize a local `!`/`!!` shell result;
    `before_provider_request` hooks can transform bounded prompt fields and
    narrow tools for the current request; session-before hooks fail closed for
    stateful session operations. Gate
    `scripts/parity_checks/extension_live_session_conformance.py --json`.
14. Dynamic extension CLI flags — **landed for `pipy repl` tool-loop runs**:
    `ExtensionFlag`/`RegisteredFlag`, `api.register_flag(...)`,
    `extension_flags(...)`, and `parse_extension_flag_tokens(...)` collect
    boolean/string flags from activated extensions, parse only the leftover
    unknown CLI tokens after built-in argparse handling, fail closed before a
    provider turn on unknown/malformed tokens, and expose values as `ctx.flags`
    to commands, shortcuts, extension tools, and hook contexts. Product-path
    tests cover `pipy repl --extension <file> --plan --ticket PIPY-123`.
15. Simple extension UI primitives — **landed for command/shortcut contexts**:
    `ctx.ui.select`, `ctx.ui.input`, and `ctx.ui.confirm` run simple
    product-TUI overlays and return cancel/default values in headless mode;
    `ctx.ui.set_status` renders bounded live status rows; and
    `ctx.ui.set_working_message` / `ctx.ui.set_working_visible` control the
    provider-turn working row for subsequent turns until changed again or reset
    by the extension. This is still short of Pi's full widget/component surface:
    custom tool renderers, custom header/footer/editor, autocomplete providers,
    and extension state/session-manager helpers remain follow-ons.
16. Custom session entries and message renderers — **landed for command/shortcut
    contexts**: `api.register_message_renderer(custom_type, renderer)` accepts a
    bounded synchronous text renderer for JSON-safe custom entries, and handlers can call
    `ctx.append_entry(custom_type, data)` to append a `custom` entry to the
    native product session tree and receive the new entry id. The rendered entry
    appears in the product TUI or captured-stream diagnostics without a provider
    turn; renderer crashes
    fail soft with a bounded diagnostic, and non-JSON data is converted before
    persistence. Renderers receive the same JSON-safe value that is persisted,
    not the original live object, so the TUI and exports see one consistent
    payload; if the original value cannot be encoded, the renderer receives a
    stringified fallback, and if it exceeds the cap it receives a truncated
    marker object. `custom_type` must be the same command-shaped, lowercase
    identifier registered with the renderer (1-200 characters); unknown or differently-cased types
    render through the bounded generic fallback. This first slice renders custom
    entries when they are appended; replaying custom entries into a resumed TUI
    session is a later session-manager/rendering follow-on. This is the first
    Pi-shaped `appendEntry` / `registerMessageRenderer` slice. Multi-widget
    message components and extension session-manager helpers remain follow-ons.
17. Custom tool renderers — **landed**: an `ExtensionTool` may carry optional
    `render_call(ctx)` and `render_result(ctx)` callables that return a
    `ToolRenderComponent` (use the `lines_component(...)` convenience to wrap
    pre-rendered lines). pipy dispatches them when rendering that extension's own
    tool rows — in the product TUI and in captured (non-TTY) output — and commits
    the pre-styled lines under dedicated `tool_call_custom`/`tool_result_custom`
    line-kinds with default band framing. The renderer receives a read-only
    `ToolRenderContext` (`tool_name`, `args`, `is_result`, `is_error`, `content`,
    `details`, `expanded`, `width`, `theme`, and a `state` mapping shared from
    `render_call` to `render_result` for one tool execution); the extension's
    `ToolResult.details` reaches `render_result` through an in-memory,
    correlation-keyed sink that is never archived or sent to the provider. A
    bounded `ToolRenderTheme` (`theme.fg(color, text)` / `theme.bold` /
    `theme.dim`, with semantic colors `text`/`accent`/`success`/`warning`/`error`/
    `dim`) maps onto the active chrome palette and emits plain text when color is
    disabled (captured / `NO_COLOR`). Rendering is **render-once / snapshot**: the
    component's `render(width)` is called once per phase, output is coerced and
    length-bounded, and there is no live `invalidate`/re-render runtime yet. The
    whole path is **fail-soft**: a renderer (or its `render()`) that raises,
    returns a non-component, or returns an uncoercible value falls back to pipy's
    default tool-row rendering. Deferred: live invalidation/partial updates,
    `renderShell:"self"` self-framing, and overriding built-in tool renderers.
    Known follow-on: the extension tool-renderer **map is not refreshed across
    `/reload`** — the renderer is constructed once per session, so renderers added
    or changed by a reloaded extension are not picked up until restart (the
    details sink *is* wired on the reload path). This matches the reload Open
    Question below.

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

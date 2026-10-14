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

Implementation status: **partially implemented.** Slices 1–25 plus later
focused editor, chrome, message-delivery, entry-rendering, and OAuth follow-ons
have landed (see "Suggested Implementation Slices"), including **package
runtime composition** — installed local-path and managed git package resources
(extensions/skills/prompts/themes) now flow through discovery at lowest
precedence with Pi-shaped enablement filters.
The pre-existing pipy runtime resources (bounded Markdown skills, prompt
templates, custom slash commands, and chrome themes) remain supported alongside
the Python extension API.

The 2026-07-17 Pi refresh identified additional gaps beyond the older
rich-UI list. Project-trust extension decisions/reads,
`before_provider_headers`, the true-idle `agent_settled` lifecycle hook, and
durable TUI-only entry renderers have now shipped. Cache-friendly dynamic tool
loading now also ships for supported Anthropic Messages and OpenAI Responses /
Codex Responses models through durable tool-result load points. Kimi's Chat
Completions shape remains the next independent provider-owned follow-on.
Treat each provider shape as a separate slice; do not fold them into the broad
custom-component track.

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
(`ctx.ui.select`, `ctx.ui.input`, `ctx.ui.confirm`, `ctx.ui.editor`,
`ctx.ui.set_status`, `ctx.ui.set_working_message`, `ctx.ui.set_working_visible`,
and `ctx.ui.set_hidden_thinking_label` / `setHiddenThinkingLabel`), and
keyboard-shortcut registration `api.register_shortcut(...)`. Theme controls
also ship (`ctx.ui.theme`, `ctx.ui.get_all_themes`, `ctx.ui.get_theme`,
`ctx.ui.set_theme`), mirroring Pi's `theme`/`getAllThemes`/`getTheme`/`setTheme`
for theme *selection* (registration stays `resources_discover`-only). Tool-output
expansion controls also ship for command/shortcut contexts:
`ctx.ui.get_tools_expanded()` / `getToolsExpanded()` read the live product-TUI
expansion state, and `ctx.ui.set_tools_expanded(...)` / `setToolsExpanded(...)`
set it; headless contexts match Pi RPC by returning `False` and no-oping writes.
The extension
editor also supports Pi's `$VISUAL`/`$EDITOR` external-editor handoff from the
resolved `app.editor.external` keybinding, default Ctrl+G; that default binding
is reserved from extension shortcuts, so translated extensions must not register
Ctrl+G. Ctrl+G remains reserved even when a user rebinds `app.editor.external`
to another key. Raw terminal input subscriptions ship for live product-TUI command/shortcut contexts via
`ctx.ui.on_terminal_input(handler)` /
`ctx.ui.onTerminalInput(handler)`: handlers see decoded key strings before
built-in editor handling, may consume or replace them, and receive an
idempotent disposer; headless contexts return a no-op disposer like Pi RPC.
Retained chrome/listener/autocomplete/editor-component writes are staged in a
candidate-owned sink during reload. Invalid flags or another fallible candidate
failure leave the prior live chrome intact and never paint the candidate; an
accepted candidate reconciles only after its generation commits. Rejection does
not re-fire the retained generation's `session_start`, so append-style terminal
listeners and autocomplete providers are not duplicated and its editor instance
is not rebuilt. An accepted replacement's `session_start` retained writes use
that detached sink exactly once and become visible as one accepted snapshot;
writes racing the owner handoff are delivered once after the snapshot rather
than being wiped by it. Reconcile/paint, factories and callbacks, session-mutex
acquisition, and disposal run outside both the sink-local state guard and the
live driver's short owner-selection guard. If reconciliation fails, pipy keeps
or restores the prior chrome where possible and reports a bounded diagnostic;
it never closes a sink after transferring live ownership. Interrupts from a
retired listener disposer propagate after that transfer without closing the
new live sink. Once a
rejected candidate sink closes, late setters/registrations silently return
`None`, and `on_terminal_input()` returns an inert disposer, with no diagnostic.
R3c3 now installs one complete startup/reload generation, including its routing
owner and renderer snapshot provider. On reload, replacement `session_start`
runs exactly once against the candidate sink before acceptance and before any
accepted staged custom message becomes visible. If lifecycle, provider/catalog,
or final chrome preparation then refuses the reload, the candidate may
already have emitted non-staged, non-chrome lifecycle effects such as
`ctx.notify()`. Its candidate chrome is discarded and its staged messages are
suppressed. This refusal behavior is part of the existing `session_start`-
before-acceptance ordering delta, not a third delta. Accepted frozen messages
are delivered before the candidate route's two-phase release and gate drain.
On reload the publication gate remains closed to class-A mutation throughout
that delivery/release/drain sequence, even though the session mutex is normally
unlocked; custom-message rendering and other extension-visible sinks may
therefore observe `publication_pending` until drain completes. Candidate host
ownership publishes outside the session mutex; one subsequent uninterrupted
mutex section checks every expected live owner before any session publication
write and commits all writes only on a match. If an owner rotated in
between, pipy keeps the newer live generation, retires the terminal candidate
route, and closes candidate chrome. The published-but-unowned activation API is
therefore inert: contribution registration remains closed and retained sends
silently drop, with no double cleanup. Direct `ctx.send_message()` delivery
remains independent of routing. Every command, shortcut, hook, user-bash,
tool-call/result, session-gate, and extension-tool context binds its offered
model-runtime controls to the generation that created it. Under the session
mutex, the host checks that id, the publication gate, and terminal liveness
together with the complete in-memory mutation. A stale, publication-pending, or
post-run call returns `False` and changes nothing; a call committed before
gate-open remains applied through publication. Concurrent thinking changes
serialize their in-memory assignment and durable `thinking_level_change` JSONL
append in one order, release the session mutex before filesystem I/O, and
release the outer coordinator before footer paint. Allowed `set_model()` calls
first prepare catalog resolution, provider construction, and detached coding
replacements outside the session mutex. Their commit atomically rechecks the
creating generation, gate, terminal state, exact selection/default/thinking
expectation, and coding-binding identity, then assigns only the new selection,
provider binding, and cleared usage. Provider-visible history, compaction and
provider failure remain live. Footer presentation and fail-soft default persistence run
after unlock. A stale, gated, terminal, construction-failed, or superseded
candidate returns `False` without rebinding, painting, or persisting. The
pre-existing tool-capability-refusal compatibility remains narrower: selection
and coding state stay unchanged, while an explicit `:level` is retained as the
thinking level and the prior selection remains the pending default. A retained
call first released after teardown is refused before provider construction.
The `session_before_compact` gate continues to allow `set_model()`. A successful
replacement during automatic compaction invalidates the accepted coding run:
`CodingContextChangedError` escapes and existing exceptional cleanup closes the
session lifetime before stale history, tree entries, or usage can be published.
This is not a recoverable return to the prompt. Manual compaction outside an
accepted run admits the same mutation and permits a subsequent prompt with the
selected model. Mid-turn provider/tool hook contexts continue to expose a
generation-bound denied `set_model()` that always returns `False`. R4a now
synchronizes later accepted/live queue
append versus drain. R4c binds every ordinary retained-chrome read/write to the
exact `SessionGenerationSnapshot` that created its context: publication captures
the displaced handle under the session mutex, then closes and snapshots it under
only that handle's sink-local guard. A late widget/header/footer/title/indicator,
listener, autocomplete, editor-component, or hidden-thinking-label write through
a retired handle is ignored with the same closed-sink return shape; an editor-
component read returns `None`, and a terminal-input registration returns an inert
disposer. Callbacks, disposal, paint, and final reference release happen after
both guards. Terminal run finalization now closes that exact generation handle
and its outboxes before unlocked TUI disposal and paint.
Autocomplete provider wrappers ship for live product-TUI command/
shortcut contexts via `ctx.ui.add_autocomplete_provider` /
`ctx.ui.addAutocompleteProvider`, using Pi-shaped `get_suggestions` /
`getSuggestions`, `apply_completion` / `applyCompletion`, and optional
`should_trigger_file_completion` / `shouldTriggerFileCompletion` methods. It is
not source-compatible with Pi's TypeScript extensions, and it still lacks
several mature Pi surfaces: broader custom editor component-library parity
beyond the landed live integration, live custom message component
invalidation/re-rendering, remote PyPI/npm package distribution, and broader package ecosystem polish. The
bounded OAuth-provider extension `/login` slice now ships: extension providers
with `ExtensionOAuthConfig` are projected under the provider name as the derived
OAuth id, `/login <provider>` invokes the extension callback with stdio auth /
device-code / prompt / select / progress callbacks, and stores
`{"type":"oauth", ...credentials}` in pipy's `AuthStore`; `/logout <provider>`
removes it. Provider-factory credential injection and broader declarative
provider config remain deferred. The custom session-entry/message rendering
surface has landed, including extension provider OAuth metadata and **rich
message renderers**: extensions
register a renderer for a custom entry type and command/shortcut handlers append
JSON-safe custom entries to the native product session tree; a renderer that
requires a second `(data, ctx)` parameter receives a `MessageRenderContext` and
may return a themed component (committed SGR-preserving with no forced label),
while a 1-arg renderer stays plain text. Custom entries on the active branch
are replayed into startup-opened TUI sessions, including
`--session`/`--continue`/`--resume-session` opens, with the best available
registered renderer. Custom-message sending also ships for idle provider
delivery: `triggerTurn` starts a deterministic provider turn and
`deliverAs: "nextTurn"` injects custom context into every provider request of
the next accepted run; `deliverAs: "steer"` / `"followUp"` queue
provider-visible content through pipy's steering/follow-up drain. The
`nextTurn` context is a request-only overlay: it does not become a canonical
conversation/run-result message or add a second product-session entry, and it
disappears before the following run. Automatic compaction continues on durable
history while the overlay is active; manual `/compact` remains available. The
original bounded custom-message entry remains in the full-content native
product session, while the metadata workflow archive remains allowlisted and
content-free. In-session full-history redraw on `/resume`
switches and `CustomMessageEntry` rendering through registered renderers now
ship. Extensions can also render their own tool call/result rows with themed
color (`render_call`/`render_result`), and pin
persistent chrome — an above/below-editor widget, a custom header, a custom
footer, the terminal title, and a custom working indicator
(`ctx.ui.set_widget`/`set_header`/`set_footer`/`set_title`/`set_working_indicator`).
The footer factory receives read-only, Pi-shaped `FooterData` with
`getGitBranch()`, `getExtensionStatuses()`, `getAvailableProviderCount()`, and
live product-TUI `onBranchChange(...)` callbacks; headless snapshots keep a safe
no-op disposer.
Session switch/fork/tree/
compaction interception, dynamic active-tool/model/thinking controls,
`user_bash`, `before_provider_request` provider-payload hooks, and the
mutation-only `before_provider_headers` HTTP-header hook now ship as
live-session follow-on slices. Command/shortcut handlers also expose Pi-shaped session metadata helpers:
`ctx.set_session_name` / `ctx.setSessionName`, `ctx.get_session_name` /
`ctx.getSessionName`, and `ctx.set_label` / `ctx.setLabel` persist display-name
and entry-label changes through native session entries. The read-only
`ctx.session_manager` / `ctx.sessionManager` view covers the active native
session's cwd, dir/file/id, header, entries, labels, branch/tree, leaf, and
session name without handing extensions the mutable `NativeSessionTree`.
Completion, custom-entry, name, label, and custom-message effects are serialized
for the complete call, including provider, durable-tree, render, and queued-input
phases. Calls accepted before terminal close finish in order; later effectful
calls raise `ExtensionCapabilityError` without provider, tree, render, or input
effects. Read-only session-manager and session-name views remain available over
the coherent final tree. Per-run source-loading flags for extensions,
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
  UI text, file contents, and provider payloads must not enter the metadata-only
  `pipy-session` workflow archive/catalog. The immutable native product session
  tree is the separate full-content durable store.

## Non-Goals

- Do not execute remote packages automatically in the first slice.
- Do not load or execute Pi TypeScript extensions. Translation to Python is the
  compatibility story.
- Do not expose internal session objects, recorder handles, terminal renderer
  internals, provider HTTP clients, the removed transcript-sidecar path, or
  mutable workflow archive/catalog files to extensions. No transcript sidecar
  exists in the current architecture.
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
  still cannot mutate the metadata-only workflow recorder/catalog, access the
  removed transcript-sidecar path, or mutate terminal renderer internals. No
  transcript sidecar exists in the current architecture.
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
  `PIPY_CONFIG_HOME` -> `${XDG_CONFIG_HOME}/pipy` -> `~/.pipy` (when present)
  -> `~/.config/pipy`
- Explicit CLI paths: repeated `--extension <PATH>` accepts a Python file, a
  direct extension directory, or a directory of extension candidates. Matching
  `--no-extensions` disables workspace/global/package discovery (and the
  built-in [MCP support](mcp.md#turning-mcp-off)) but still
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
event-specific contexts later. Contribution registration is activation-bounded:
after activation succeeds or is rejected, every retained `register_*`,
`unregister_provider(...)`, and direct or decorator-form `on(...)` call raises
`ExtensionCapabilityError` at that call boundary. This includes void-return
registrations, `register_flag(...)`, `on(event, handler)`, and `on(event)`; pipy
does not return an inert decorator or fabricate a `RegisteredFlag`.

For ordinary validation failures, precedence remains family-compatible when
multiple fields are invalid: command/tool/flag reserved-or-duplicate checks
precede their remaining values; shortcut key shape and handler callability
precede normalized reserved/duplicate checks; provider factory/models/default/
OAuth validation precedes duplicate checking; and message/entry renderer
callability precedes duplicate checking. Unexpected exceptions from extension-
controlled normalization or copying instead fail closed to that registration
family's bounded `invalid_*` reason and type-only diagnostic. The first such
failure remains recorded even if extension code catches the raised validation
error, so this hostile-exception case intentionally does not promise exact pre-R1
reason behavior.

Names that are `str` subclasses—including `StrEnum`, default-stringifying
`(str, Enum)` constants, and subclasses overriding `__str__`—remain accepted
and are copied from their underlying value to plain strings without invoking
the override before reservation. A non-string, empty/whitespace-only, or
slash-containing
`unregister_provider(...)` argument records and raises the same
`invalid_provider` activation failure as invalid provider registration; it is
not silently ignored. Runtime message sending keeps its existing `None` return shape and is governed
separately from contribution registration. Messages staged before the activation
host seals are committed from that one frozen snapshot; a send racing after seal
while activation is still pending silently returns `None` and has no effect.
Accepted handlers send through their generation queue while it is live. Live
append and drain serialize under the session mutex, so a concurrent drain cannot
erase a writer that lands between copying and clearing. Once a candidate is
rejected or a live generation is retired, retained `api.send_user_message(...)`,
`api.send_message(...)`, and `api.sendMessage(...)` calls silently return `None`,
emit no diagnostic, and accumulate nothing; only the live generation drains.
The same silent refusal applies if an internal retained host no longer matches
its constructor-validated routing outboxes.
This closed-queue rule does not apply to command-context `ctx.send_message(...)`,
which targets the durable session tree/render/input sinks rather than a generation
outbox.

A provider-only catalog harvest detaches its immutable provider and
unregistration outputs, then finalizes each accepted activation host into a
narrow terminal state. Registration stays closed, sends are inert, and staged
registries/messages are cleared, while guarded registration-time default flag
values remain readable through `api.get_flag(...)` for a detached provider
factory that captured its activation API. The production catalog helper does not
parse or apply extension CLI tokens; parsed overrides are guaranteed only on the
live session activation path described under [Dynamic CLI Flags](#dynamic-cli-flags).
If a non-published host refuses catalog
finalization, pipy disposes it fail-closed under its acquired guard; a published
host is reported distinctly and left live, and an inaccessible/failing guard is
reported separately. The finalized host is neither candidate-publishable nor
a generally live runtime, and its caller must supply the diagnostic sink for
any finalization anomaly. Rejected or otherwise abandoned hosts still use full
disposal, including clearing flag values.

## Core API Shape

The public API is importable from the stable top-level
`pipy_harness.extensions` façade. That façade resolves each public name from
its authoritative native owner; extension authors do not depend on the
internal runtime module layout.

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
    handler: Callable[
        ["ToolCallContext", Mapping[str, Any]], "ToolResult | Awaitable[ToolResult]"
    ]
    # Keyword-only tool metadata; see "Tool model" below.
    exposure: Literal["direct", "model-only", "codemode", "deferred", "hidden"] = "direct"
    namespace: "ToolNamespace | None" = None
    annotations: "ToolAnnotations | None" = None
    output_schema: JsonSchema | None = None
    reaches_tools: Callable[[Mapping[str, Any]], Literal["all"] | Sequence[str]] | None = None


@dataclass(frozen=True)
class ToolResult:
    content: ToolContent
    details: Mapping[str, Any] | None = None
    structured_content: Any = NO_STRUCTURED_CONTENT
    is_error: bool = False
    # Pi's content array for a result with images: TextContent and
    # ImageContent values; their text is then the result's text.
    blocks: tuple["TextContent | ImageContent", ...] = ()
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
    # Shortcuts may not shadow core editor/app bindings; default Ctrl-G is
    # reserved for app.editor.external / external-editor handoff.

    def register_flag(self, flag: "ExtensionFlag") -> None: ...

    def get_flag(self, name: str) -> object | None: ...

    def register_message_renderer(
        self,
        custom_type: str,
        renderer: Callable[..., object],
    ) -> None: ...

    def register_entry_renderer(
        self,
        custom_type: str,
        renderer: Callable[..., object],
    ) -> None: ...

    def register_tool_renderer(self, resolver: "ToolRendererResolver") -> None: ...

    def register_mcp_server(self, name: str, config: Mapping[str, Any]) -> None: ...

    def unregister_mcp_server(self, name: str) -> None: ...

    def get_mcp_servers(self) -> list["RegisteredMcpServer"]: ...

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
    def on(self, event: str, handler: Callable[..., object] | None = None) -> object: ...


class SessionManagerView(Protocol):
    def get_cwd(self) -> str | None: ...
    def get_session_dir(self) -> str | None: ...
    def get_session_id(self) -> str | None: ...
    def get_session_file(self) -> str | None: ...
    def get_leaf_id(self) -> str | None: ...
    def get_leaf_entry(self) -> "SessionEntryView | None": ...
    def get_entry(self, entry_id: str) -> "SessionEntryView | None": ...
    def get_label(self, entry_id: str) -> str | None: ...
    def get_branch(self, from_id: str | None = None) -> tuple["SessionEntryView", ...]: ...
    def get_header(self) -> "SessionHeaderView": ...
    def get_entries(self) -> tuple["SessionEntryView", ...]: ...
    def get_tree(self) -> tuple["SessionTreeNodeView", ...]: ...
    def get_session_name(self) -> str | None: ...


class CommandContext(Protocol):
    cwd: str
    has_ui: bool
    ui: "ExtensionUi"
    conversation: "ConversationView"
    session_manager: "SessionManagerView"
    sessionManager: "SessionManagerView"  # Pi-shaped alias for translated extensions.
    flags: Mapping[str, object]

    def complete(self, system_prompt: str, user_text: str) -> str: ...
    def set_active_tools(self, tool_names: Sequence[str]) -> bool: ...
    def get_all_tools(self) -> tuple["ToolInfo", ...]: ...
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
| `project_trust` | Let pre-trust global/CLI extensions decide whether protected project inputs may load. **Shipped.** | `{trusted: "yes"|"no"|"undecided", remember?: bool}` |
| `session_start` | Restore per-session extension state after startup, new, resume, fork, or reload. (Pi `SessionStartEvent.reason` is `"startup" \| "reload" \| "new" \| "resume" \| "fork"`.) | None |
| `session_shutdown` | Cleanup local extension state before exit, reload, or session switch. | None |
| `resources_discover` | Contribute additional skills, templates, commands, themes, or extension resource paths. | `ResourceContribution` |
| `input` | Observe or transform submitted user input before a provider turn. | None or `InputTransform` |
| `user_bash` | Observe or gate a user-run bash command entered via Pi's `!`/`!!` prefix, including whether it is excluded from model context. | None or `UserBashDecision` |
| `before_agent_start` | Inject bounded context, alter system-prompt options, or add safe custom messages before a turn. | None or `BeforeAgentStartResult` |
| `agent_start` | Observe a low-level agent run starting, including each admitted retry continuation. | None |
| `agent_end` | Observe one low-level run ending, including failed attempts before retry. This metadata-only hook has no will-retry indicator; use agent_settled for true-idle work after the accepted prompt and queued continuations. | None |
| `agent_settled` | Observe true idle after retries, compaction work, and queued continuations are exhausted. **Shipped.** | None |
| `turn_start` | Observe one model/tool loop turn starting. | None |
| `turn_end` | Observe one model/tool loop turn ending. | None |
| `context` | Observe or contribute bounded context before provider-visible context is finalized. | None or `ContextContribution` |
| `message_start` / `message_update` | Observe assistant/user/tool-result message lifecycle events. | None |
| `message_end` | Observe or replace the finalized message before later lifecycle hooks see it. | None or `MessageTransform` |
| `tool_call` | Inspect, change the arguments of, or block a tool call after its arguments were validated and before it runs. | None or `ToolBlock` |
| `tool_execution_start` | Observe extension or native tool execution starting. | None |
| `tool_execution_update` | Observe bounded progress updates from running tools. | None |
| `tool_execution_end` | Observe extension or native tool execution ending after `tool_result` hooks have produced the finalized result. | None |
| `tool_result` | Observe or transform bounded tool result metadata/content before the next model turn. | None or `ToolResultTransform` |
| `before_provider_request` | Inspect or transform the live in-memory provider request before the provider call. | None or `ProviderRequestTransform` |
| `before_provider_headers` | Mutate assembled live request headers once per provider request; strings add/override and `None` deletes before transport. **Shipped.** | None (return values ignored) |
| `after_provider_response` | Observe safe provider response metadata after the provider call. | None |
| `model_select` | **Target; not yet shipped.** Observe model/provider changes. | None |
| `thinking_level_select` | Observe thinking/reasoning level changes where the selected provider supports them. | None |
| `session_before_switch` | Observe or block a session switch before the current session is replaced. | None or `SessionDecision` |
| `session_before_fork` | Observe or block a fork/branch operation before it starts. | None or `SessionDecision` |
| `session_before_compact` | Observe or block compaction before it starts. | None or `SessionDecision` |
| `session_compact` | Observe completed compaction metadata. | None |
| `session_before_tree` | Observe or block tree/session-history navigation before it starts. | None or `SessionDecision` |
| `session_tree` | Observe completed tree/session-history navigation metadata. | None |
| `mcp_servers_change` | Observe a change of the [MCP servers extensions registered](#mcp-servers). Handling it marks the extension as the one that connects them. **Shipped.** | None |

Ordinary retries call agent/turn lifecycle hooks for each attempt, while input,
before_agent_start and before_provider_request hooks run once for the captured
ordinary request. A prepared request/handle stays frozen across retries. Lifecycle
observers run outside mutation locks; original binding/context admission is
revalidated before a new provider phase. agent_settled fires only after retries
and queued continuations reach true idle. In RPC, the final agent_end hook runs
before the buffered terminal agent_end envelope and logical claim settlement;
intermediate retry envelopes precede their hooks. A blocking final hook therefore
keeps get_state.isStreaming true and a queued successor pending until it returns.

<a id="prompt-sections"></a>
### `before_agent_start`: the prompt and its sections

A `before_agent_start` handler runs once before a run starts, after the
`input` handlers. Its event has:

- `event.system_prompt`: the prompt for the sections as they are when it is
  read, as in Pi: it shows what earlier handlers and the handler itself
  changed, followed by the text earlier handlers appended.
- `event.system_prompt_options.sections`: a `dict` of the prompt sections the
  run adds to pipy's own, name to text. pipy puts the list of MCP servers
  there as `mcp_servers` (see
  [MCP](mcp.md#the-servers-in-the-system-prompt)); otherwise it is empty.

Change the `dict` in place to change the coming request's prompt:

```python
from pipy_harness.extensions import BeforeAgentStartResult


def activate(api):
    @api.on("before_agent_start")
    def sections(event, ctx):
        sections = event.system_prompt_options.sections
        sections["review_rules"] = "Never approve a change without tests."
        sections.pop("mcp_servers", None)  # do not list the MCP servers
```

- A new name adds a section. The text is wrapped in a tag of that name
  (`<review_rules>` ... `</review_rules>`) and follows the prompt's last
  section; several new sections keep the order they were set in.
- The name of a section the prompt already has (`tools`, `rules`, `docs`,
  `addendum`, `project_context`, `skills`, `cwd`, `resume`) replaces that
  section's text where it stands. pipy's own sections are not in the `dict`;
  read them from `event.system_prompt`.
- Deleting a name takes the run's own section out again. An empty text does
  the same for a new name and leaves one of pipy's sections as it is.
- A name must match `[a-z][a-z0-9_-]*` and must not be `preamble`, and a text
  must be a string. pipy leaves any other entry out and reports it
  (`pipy: ignored invalid system prompt section '<name>' set by a
  before_agent_start hook`).

The `dict` is built anew for every run, so set a section in every run you
want it in. Later handlers see the changes of earlier ones, in the `dict` and
in `event.system_prompt`; what a handler changed before it raised stays.

Sections are recorded in the conversation: when a run's sections differ from
the ones the conversation has, only the changed ones are appended as a change
of the system prompt (a section that is gone is removed by name), and earlier
messages stay as they are. Returning
`BeforeAgentStartResult(append_system_prompt=...)` works differently: the
text is appended to the prompt of that one run and is not recorded. Prefer
sections where a provider's prompt cache matters.

Different from Pi: `system_prompt_options` has only `sections`. Pi's other
fields (`selectedTools`, `toolSnippets`, `toolGuidelines`,
`promptGuidelines`, `appendSystemPrompt`, `contextFiles`, `skills`,
`customPrompt`, `forceSystemPrompt`) do not exist, and the event has no
`messageType`, `prompt` or `images`. `event.system_prompt` also holds the
text earlier handlers appended with `append_system_prompt`. An invalid
section name fails the prompt in Pi; pipy reports it and goes on. Assign into the `dict`; replacing
`system_prompt_options.sections` with another `dict` is not possible.

### D6b public and RPC transition ordering

D6b3 implements this ordering for public `ProductSession.fork`, `clone`,
`new_session`, and `switch_session`, and for the corresponding JSONL RPC
commands. D6c3/D6c5/D6c7 adopt terminal resume/new/fork/clone through the same
transition owner. D6c9 adopts terminal import while retaining its presentation
boundary below.

Public and RPC `fork`/`clone` and `new_session`/`switch_session` operations use
the already activated generation's session gates; they do not restart extension
lifecycle. Fork/clone resolves the persistent source and
selected exact entry, then calls `session_before_fork(operation="fork",
target=<entry id>)` before child creation. The public result distinguishes clone
from fork without expanding the existing extension operation vocabulary. Fresh
replacement calls
`session_before_switch(operation="switch", target="new")` before file creation.
Existing-session replacement canonicalizes its candidate and calls
`session_before_switch(operation="switch", target=<canonical path>)` before
claiming, strict-loading, workspace validation, lease handoff, or tree
publication. A same-target switch is a no-op and calls neither hook.

The existing gate dispatcher remains fail-closed: any `SessionDecision` denial,
including a hook failure, returns the one stable public `extension_refusal` and
the RPC cancelled projection. No adapter infers a hook crash from diagnostic
text. Neither case writes a child/fresh session, publishes a candidate tree, or
emits a completed transition observation. A successful
transition's public observation is its one immutable result. It does not send
`session_shutdown` followed by `session_start`, a new extension completion
event, or a UI rebind: those bookends continue to occur once when the
encompassing facade starts and retires. D6b3 owns the single RPC event/UI rebind
after an accepted native transition; refusal and same-target no-op do not
rebind. The state-first tree/context rebuild
boundary is not an atomic transaction; a post-publication rebuild error retires
the public lifetime before returning its typed published-target failure.

Terminal `/import` first retains its confirmation and path-presentation policy.
The detailed `session_before_switch(operation="switch")` target is the expanded,
cwd-joined source spelling without canonical resolution. The hook runs once,
after the first confirmation and source-slot validation but before source
validation, copy or missing-workspace recovery. A denial therefore creates no
import artifact. Once allowed, the terminal stages and permissively opens a
persistent copy; the native coordinator claims and publishes that copy through
the current terminal lease slot. Missing-workspace confirmation, controlled
import errors and partial-artifact behavior remain terminal import concerns.

The first implementation should still start small. `tool_call` should be the
first policy hook because it enables high-value workflows such as protected
paths and command gating without requiring package loading or rich UI hooks.

Project trust is now specified separately in the reviewed
[project-trust design](specs/2026-07-15-project-trust-design.md).
The core store/final-cwd/settings-resource gate, interactive/package
management integration, and extension-owned trust surfaces now ship. Pipy
activates only global and explicit CLI extension sources before trust resolves. Their `project_trust`
handlers run serially; `undecided` continues,
the first yes/no owns the decision, and only exact `remember: true` persists it.
The startup context has `cwd`, `mode`, `has_ui`/`hasUI`, and a bounded UI surface;
headless select/confirm/input calls return immediately without touching protocol
streams, while notify is stderr-only. Normal post-start contexts expose the
zero-argument read callbacks `ctx.is_project_trusted()` and
`ctx.isProjectTrusted()`. The ordered
[implementation plan](specs/2026-07-15-project-trust-implementation-plan.md)
keeps this extension-owned phase separate from trust core and interactive/
package integration. A reusable activation batch keeps global/CLI module code
single-run across trust resolution, provider catalog construction, and initial
session startup; `/reload` intentionally starts a fresh batch.

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
live tool name and validated input so permission gates can inspect a bash command
or write path. That live access does not change archive policy: the
metadata-only workflow archive/catalog must not store prompts, raw tool inputs,
raw tool results, file contents, provider payloads, secrets, or UI text unless
a specific safe metadata rule allows it. Full-content product history belongs
separately to the immutable native product session tree.

The same rule applies to provider hooks: a trusted extension may inspect or
transform the live in-memory provider request, but the metadata-only workflow
archive/catalog must never record provider payloads.

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

- The names of the seven built-in file and shell tools are reserved. An
  extension tool named `codemode` or `tool_search` replaces the built-in
  tool of that name, see [Replacing built-in tools](#replacing-built-in-tools).
- A tool name another extension registered is a duplicate and disables the
  second extension. An extension that registers one of its own tool names
  again replaces its earlier registration, as in Pi.
- Tool schemas use the same JSON-schema subset as native tools.
- Tool descriptions are model-visible and should support optional
  tool-specific prompt guidance in a later slice.
- Tool handlers receive a workspace-scoped context with safe helper methods.
- A tool, command or shortcut handler may be an `async def` function, as Pi's
  are: pipy runs the coroutine to its end on the thread that called the
  handler (its own event loop, or a helper thread when that thread already
  runs one) and uses what it returns or raises like a synchronous handler's.
- Tool handlers receive parsed input, a cancellation signal, and a progress
  callback or sink for partial updates.
- Tool results use Pi-shaped `content` plus `details`, where `content` is the
  provider-visible result and `details` is structured local state/metadata that
  can support rendering, state reconstruction, or later hooks.
- A tool result may also carry `structured_content` and `is_error`; see
  "Extension tool metadata" below.
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

### Tool model

Every tool, built-in, extension or MCP, is described by one native
`ToolDefinition` (`native/tools/base.py`). Besides the name, description and
input schema it carries the metadata Pi's tools carry, with Pi's meaning:

| Field | Values | Meaning |
| --- | --- | --- |
| `exposure` | `direct` (default), `model-only`, `codemode`, `deferred`, `hidden` | How the model reaches the tool. `direct`: declared while active and callable from scripts while active. `model-only`: declared while active, never callable from scripts. `codemode`: callable from scripts whenever registered, listed in the codemode description, not declared unless activated. `deferred`: as `codemode`, but not listed; tool search finds it. `hidden`: registered but unreachable. |
| `namespace` | `ToolNamespace(name, aliases, description, instructions)` | The group the tool belongs to, such as one MCP server. |
| `annotations` | `ToolAnnotations` with `read_only_hint`, `destructive_hint`, `idempotent_hint`, `open_world_hint` | The author's hints about what the tool does. They are reported, not verified. |
| `output_schema` | a JSON Schema object | The shape of the tool's `structured_content`. Declared, not enforced. |
| `reaches_tools` | a function from the call input to `"all"` or a tuple of words | For a tool that reaches other tools: which ones a call may reach. |
| `schema_mode` | `strict` (default), `foreign` | Which input schemas the definition accepts. |

The defaults describe today's tools, so existing tools are unchanged.
Extension tools set these fields on `ExtensionTool` (next section).

The exposure decides two sets:

- **Declared** to the model: the active tools, without `hidden` ones.
  Registering a `direct` or `model-only` tool activates it. A `codemode` or
  `deferred` tool is not activated by being registered; it is declared only
  after something names it, such as `ctx.set_active_tools([...])`. The model
  cannot call a tool that is not declared: a call to it is answered with
  `unknown tool: <name>`. A tool that `--tools`, `--exclude-tools` or
  `--no-tools` removed is not declared when something names it, and neither
  is an [MCP tool](usage.md#mcp-tools) that a `--tools` list keeps without
  naming it (unless `tool_search` could load it).
  `ctx.set_active_tools([...])` still accepts such a name.
- **Callable** from a [codemode](codemode.md) script: the active `direct`
  tools and every registered `codemode` or `deferred` tool, whether or not it
  is active. `model-only` and `hidden` tools and `codemode` itself are never
  callable. A tool that `--tools`, `--exclude-tools` or `--no-tools` removed is
  not callable either.

A script's nested calls pass the same argument validation and the same
`tool_call` and `tool_result` hooks as a direct call. An extension tool
reaches the same callable tools with `ctx.execute_tool`; see
[running other tools](#running-other-tools).

While the built-in `codemode` tool is declared it changes what the model
sees of the other tools, never what is registered or what
`ctx.get_all_tools()` reports: with `codemode.mode` `on` (the default) a
declared tool that scripts can call gets a last line in its description,
``Codemode: `tools.<name>(args)` resolves to a string.``, and with `only`
active `direct` tools are left out of requests and listed in the `codemode`
description. See [what the model is told](codemode.md#what-the-model-is-told).
A `before_provider_request` hook sees the tools a request declares, so with
`only` it does not see the tools that were left out. An extension tool does
the same with `prepare_loadout`; see
[presenting the loadout](#presenting-the-loadout).

`schema_mode="strict"` holds the input schema to the JSON-schema subset above
and caps the description at 4 KiB. `schema_mode="foreign"` is for schemas pipy
did not write, such as an MCP server's: the schema only has to be a JSON
object with `type: "object"`, the description has no cap, and the schema
reaches the provider unchanged. Arguments of a foreign tool are checked by a
standard-library validator (`native/tools/foreign_schema.py`) that enforces
what Pi's TypeBox validator does, except `unevaluatedProperties`,
`unevaluatedItems`, `$dynamicRef`, `$recursiveRef`, a `$ref` outside the
schema or under a nested `$id`, and the formats `hostname`, `idn-hostname`, `idn-email`, `iri`,
`iri-reference`, `url` and `regex`; patterns are Python regular expressions
and string lengths count code points. For those the tool's owner stays the
authority on what it accepts. The validator never rejects arguments because
of a keyword it does not enforce: a `oneOf` refuses a value two branches
match only when it enforces both in full, and `not`, `if` and `maxContains`
decide likewise only for schemas it enforces in full.
Before validating, pipy prepares the arguments as Pi's
`validateToolArguments` does for such a schema: an optional property sent as
`null` is dropped when its schema does not allow `null`, and primitives are
converted to the types the schema names (`"5"` to `5`, `"true"` to `true`, a
number to a string, `null` to `0`, `false` or `""`). Pi then validates with a
full JSON Schema implementation.

A tool result may carry `structured_content`, the machine-readable result for
programmatic callers such as codemode scripts. As in Pi it is never sent to a
provider and is not written to the session file; `content` stays the
model-facing result.

### Extension tool metadata

`ExtensionTool` takes the metadata as keyword arguments. `ToolNamespace`,
`ToolAnnotations` and `ToolInfo` are importable from `pipy_harness.extensions`.

```python
from pipy_harness.extensions import (
    ExtensionTool, ToolAnnotations, ToolNamespace, ToolResult,
)

def activate(api):
    api.register_tool(ExtensionTool(
        name="find_issue",
        description="Find an issue by key.",
        input_schema={"type": "object", "properties": {"key": {"type": "string"}},
                      "required": ["key"]},
        handler=find_issue,
        exposure="codemode",
        namespace=ToolNamespace(
            name="tracker",
            aliases=("issues",),
            description="Issue tracker",
            instructions="Search before you create an issue.",
        ),
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
        output_schema={"type": "object", "properties": {"title": {"type": "string"}}},
    ))
```

- `exposure` is one of the five values in the table above. Any other value
  disables the extension with `invalid_tool`, as does a `namespace` that is not
  a `ToolNamespace`, `annotations` that are not `ToolAnnotations`, an
  `output_schema` that is not a JSON object, or a `reaches_tools` that is not
  callable.
- `namespace.aliases` is a tuple of other names the group can be looked up by.
  `description` is the short summary shown with the group; `instructions` is
  longer guidance that is only returned when the namespace is described on
  request. Codemode scripts look a namespace up with
  `describe_namespace(name)` and `search_tools(query, namespace=name)`, by its
  name or an alias, as written or as the same identifier (`my-docs` and
  `my_docs` match alike); see [codemode globals](codemode.md#globals).
- `annotations` have the meaning of MCP tool annotations. Each hint is `True`,
  `False` or `None` (no hint). Missing hints take the MCP defaults: a tool is
  not read-only, and may be destructive and reach an open world. pipy does not
  verify them.
- `output_schema` is the JSON Schema of the tool's `structured_content`. It may
  use any JSON Schema keyword (the input schema stays in pipy's subset). pipy
  keeps a copy made at registration and does not enforce it.
- `reaches_tools(input)` is for a tool that reaches other tools. It returns
  `"all"`, or the words of the input that may name the tools it reaches; a
  word refers to every tool or namespace whose name it contains, so words may
  over-match. The built-in `codemode` tool declares it: `"all"` for a script
  that uses `search_tools`, `describe_tool`, `describe_namespace` or
  `ALL_TOOLS`, otherwise the words of the script. `tool_search` declares
  `"all"`.

  pipy uses the reach before each call of the tool, after the `tool_call`
  handlers: it waits for the MCP servers that are still connecting and that
  the call may reach, so their tools are registered when the handler runs.
  `"all"` waits for every enabled server; a word waits for the server whose
  namespace (`mcp__<server>`, with `-` as `_`) it contains. Returning `None`
  for a call means the call reaches no other tool. The function runs on the
  session thread and must not block. If it raises, or returns something
  else, the call waits for every server (Pi fails the call). See
  [waiting for servers](mcp.md#waiting-for-servers).

- `default_active=False` registers a `direct` or `model-only` tool without
  activating it (Pi `defaultActive`). It is declared once something names
  it: `--tools`, the `defaultTools` setting or `ctx.set_active_tools(...)`.
  The other exposures are never activated by being registered.
- `prepare_loadout(loadout)` is for a tool that presents other tools; see
  [presenting the loadout](#presenting-the-loadout).

**Results.** A handler that returns data declares an `output_schema` and
returns the data as `ToolResult(structured_content=...)` next to `content`. It
is any JSON value, `None` being `null`; a value that is not JSON is dropped,
and the result keeps a copy. A result without structured content has
`NO_STRUCTURED_CONTENT` there (importable from `pipy_harness.extensions`), the
default, as Pi's has `undefined`. To report a failure that still carries data, return the result with
`is_error=True` instead of raising: the model sees `content` as an error, and
the result keeps its `details` and `structured_content`. A codemode script
that calls a tool with an `output_schema` receives the structured content as a
Python value instead of the text, also from a result with `is_error=True`; a
tool without an `output_schema` gives scripts its text, and its failures raise
`ToolError` there. See [codemode](codemode.md#writing-a-script).

**Images.** A result with images is Pi's content array: return
`ToolResult(content="", blocks=(TextContent("..."), ImageContent(data,
"image/png")))` with `TextContent` and `ImageContent` from
`pipy_harness.extensions` (`data` is base64). The model receives the blocks
in their order, and the text of the text blocks, joined by newlines, is the
result's text; `content` is then not used. Their text is bounded to 60 KiB,
the bound of `tool_result` handlers, so the bound never costs the result its
images: past it, the text block that reaches it is cut and marked, later
text blocks are left out, and images are kept whole. Anything else in
`blocks` makes the result the error `extension tool error: invalid result
blocks`.

**Call hooks.** A `tool_call` handler receives a `ToolCallEvent` with
`tool_name`, `input`, `tool_call_id` and `parent_tool_call_id`. The handlers
run after argument validation, as in Pi: a call whose arguments do not fit
the tool's schema gets the validation error and reaches no `tool_call` or
`tool_result` handler. `input` is the validated arguments, the very dict the
tool runs with: to change the arguments, change `event.input` in place
(`event.input["path"] = ...`). Later handlers see the change, and the tool
runs with what the last handler left; the transcript keeps the arguments the
model sent. `tool_call_id` is the call's id. A call another tool made, a
codemode script's or one through `ctx.execute_tool`, has the id
`<calling id>/<n>`, and its `parent_tool_call_id` is the calling tool's id;
for a call the model made it is `None`. Different from Pi: the tool validates
changed arguments again, so a change the schema refuses gives the validation
error instead of running the tool (it does not count as a malformed call of
the model), and a handler that leaves a value that is not JSON in `input`
blocks the call with `tool_call hooks left arguments that are not JSON`.

**Result hooks.** A `tool_result` handler receives a `ToolResultEvent` with
`tool_name`, `content` (the result's text), `blocks` (Pi's content array of
`TextContent` and `ImageContent` when the result has an image, else empty),
`is_error`, `structured_content` (`NO_STRUCTURED_CONTENT` when there is none;
`None` is the value `null`), `details`, `input` (the arguments the tool ran
with), `tool_call_id` and `parent_tool_call_id` (as for
[call hooks](#extension-tool-metadata)); treat the values as read-only. It may
return a `ToolResultTransform`, Pi's `ToolResultEventResult`; a field it
leaves at its default stays as it is:

- `content` replaces the whole content with a text, images included, so
  `ToolResultTransform(content="")` removes an image-only result's image.
  `blocks` replaces it with text and image blocks instead (and wins over
  `content`); blocks of anything else make the transform change nothing.
  Replaced text is bounded to 60 KiB like an extension tool's blocks.
- The structured content belongs to the content it was produced with:
  `structured_content` replaces the result's (a value that is not JSON is
  dropped), and replaced content without one drops it, because it may no
  longer match. A handler that redacts `content` of a tool with an output
  schema therefore returns the redacted structured value as well, or scripts
  receive the redacted text.
- `is_error` replaces the error flag and `details` (a JSON object) the
  details.

Later handlers see what earlier ones left. A handler that returns nothing,
raises, or returns a field of the wrong kind changes nothing. A call whose
arguments failed validation, or that a `tool_call` handler blocked, reaches no
`tool_result` handler, as in Pi. Pi's transform can also replace usage; pipy
counts no usage of tools (DEV-13).

**Listing tools.** `ctx.get_all_tools()` returns a `ToolInfo` for every
registered tool, built-in and extension, active or not (the built-in
[`tool_search`](mcp.md#tool-search) is registered inactive, and naming it in
`ctx.set_active_tools(...)` activates it), in registration order, without the
tools that `--tools`, `--exclude-tools` or `--no-tools` removed: `name`, `description`, `input_schema` (a copy), `exposure`,
`namespace`, `annotations` and `reaches_tools` (the function, or `None`). It
is available in command, shortcut, hook and tool handler contexts of a
session and raises `ExtensionCapabilityError` where no session backs the
context. In Pi this is `pi.getAllTools()`; pipy puts it on the context, next
to `ctx.set_active_tools(...)`. A permission hook can decide by the
annotations:

```python
@api.on("tool_call")
def gate(event, ctx):
    hints = next(
        (tool.annotations for tool in ctx.get_all_tools()
         if tool.name == event.tool_name),
        None,
    )
    if hints is not None and hints.destructive_hint is True:
        return ToolBlock(reason=f"{event.tool_name} was not approved")
```

**Withdrawing a tool.** Tools cannot be unregistered. Register the name again
with `exposure="hidden"` to withdraw it: the tool stays registered and
`get_all_tools()` reports it as `hidden`, but no request declares it,
`ctx.set_active_tools(...)` cannot bring it back, and a model call to it is
answered with `unknown tool`. Registration is open only while `activate(api)`
runs, so a tool is withdrawn for a session by the extension's next activation
(a `/reload`); the transcript then tells the model that the tool was removed.

### Running other tools

A tool handler can run other tools while it runs. Its context is an
`ExtensionToolContext` (importable from `pipy_harness.extensions`): the
extension context plus `tools` and `execute_tool`.

```python
def read_all(ctx, params):
    parts = []
    for path in params["paths"]:
        outcome = ctx.execute_tool("read", {"path": path})
        if outcome.is_error:
            return ToolResult(
                content=f"{path}: {outcome.result.content}", is_error=True
            )
        parts.append(outcome.result.content)
    return ToolResult(content="\n".join(parts))
```

- `ctx.tools` is a tuple of `ToolInfo`, one per tool `execute_tool` can
  call, in registration order: the callable tools of the
  [tool model](#tool-model), as they were when the tool the model called
  started, the tool itself included when it is callable. A tool that is
  `model-only` is not in the list and cannot be called this way.
- `ctx.execute_tool(name, arguments, *, signal=None, on_update=None)` (also
  `ctx.executeTool(name, arguments, {"signal": ..., "onUpdate": ...})`) runs the tool
  and returns a `ToolCallOutcome`, Pi's `AgentToolCallOutcome`:
  `tool_call` (a `ToolCallInfo` with the call's `id`, `name` and
  `arguments`), `result` (a `ToolResult` with `content`, `details`,
  `structured_content`, `is_error` and, for a result with images, its
  `blocks`), `is_error`, and `duration_ms`, how long the call took (`None`
  for a name that is not callable). A tool can return the `blocks` of a
  nested result as its own, so an MCP screenshot reaches the model as an
  image. `arguments` is a mapping, or nothing
  for a tool without arguments. `signal`, a `threading.Event`, cancels this
  call alone when it is set: it ends with the error
  `tool cancelled by its caller`. Without it the call uses `ctx.signal`, the
  calling tool's own cancellation signal (`None` where it has none).
  `on_update` receives each chunk of the tool's live output (a `bash`
  command's output, an MCP server's progress), on the thread that produced
  it; what it raises is ignored. The call passes argument validation and the
  `tool_call` and `tool_result` handlers like a call the model made, and it
  counts against the tool budget of the turn.
- It never raises for a failure of the tool. A name that is not callable
  gives `Tool <name> not found`; invalid arguments, a call a `tool_call`
  handler blocked, a failed tool and an exhausted tool budget give the same
  error results the model would get. All of them have `is_error=True`.
- A tool called this way gets `ctx.tools` and `ctx.execute_tool` too, so a
  nested tool can run tools itself, at any depth, also a tool that calls
  itself; the tool budget bounds the depth. A tool a codemode script called
  can run tools the same way.
- The nested calls do not appear in the transcript: their results only reach
  the calling tool, which reports them in its own result. They have the ids
  `<calling id>/<n>` (`p1/2/1` is the first call of the second call of
  `p1`), and the session emits a start and an end event for
  each, and an update event for each chunk of its live output, with the id of
  the calling call (`parentToolCallId` in
  [JSON mode](json.md) and [RPC mode](rpc.md)). The result of the calling
  tool the model called keeps a bounded record of them, at every depth, as
  `nestedCalls` in its `details`, in the order the calls began
  (name, arguments, status, duration and error, never results; at most 256
  calls, arguments up to 8 KiB per call and 32 KiB in all, with
  `complete: false` when something was left out). A tool that called nothing
  has no such record. Compaction uses the record for its file lists and the
  HTML export shows it.
- Escape or an abort while a nested call runs cancels that call and the
  calling tool with it.

`execute_tool` may be called from any thread the handler starts, and calls
made from several threads run at the same time, as in Pi; each blocks its
caller until it ended. The handler runs on a worker thread, and the session
thread runs the pipeline of each call (validation, handlers, budget) while
the tools themselves run on threads of their own. When the tool the model
called returned, the nested calls still running are cancelled with the
error `tool cancelled by its caller`, as is a call still running when the
nested tool that made it returned, and a call that is still waiting or is
made later is answered with
`Nested tool calls are not available in this context`; so is a call of a
nested tool that already returned.

Different from Pi:

- The contexts of commands and hooks have an empty `ctx.tools`, and their
  `execute_tool` returns the error result
  `Nested tool calls are not available in this context`; Pi does not give
  them `executeTool`.
- `ctx.tools` and what `execute_tool` reaches, at every depth, are fixed when
  the tool the model called starts, after its `tool_call` handlers ran. Pi
  looks the callable tools up again for every call.
- Nested calls always run concurrently. Pi runs them one at a time when the
  agent executes tool calls sequentially or the tool's `executionMode` is
  `sequential`; pipy has neither setting.
- Pi lets a nested call still running when its calling tool returned go on;
  pipy cancels it when its calling tool returned.
- In the outcome, `result.content` is the result's text and `result.blocks`
  its ordered text and image blocks when it has an image; Pi's
  `result.content` is always the block list. A partial result is a chunk of
  text, as in pipy's own `tool_execution_update`; Pi's is a result object.
  A pipy tool handler reports no partial results of its own.
- Pi adds the usage that nested results report to the calling tool's
  result. pipy's tool results carry no usage.
- No event is emitted for a call to a name that is not callable; the record
  lists it.

### Presenting the loadout

A tool that orchestrates other tools can adjust what the model sees while
it is declared, with `prepare_loadout`. `ToolLoadout` and
`ToolLoadoutChanges` are importable from `pipy_harness.extensions`.

```python
def present(loadout):
    names = ", ".join(tool.name for tool in loadout.callable_tools)
    return ToolLoadoutChanges(
        descriptions={"run_tools": f"Runs tools: {names}"},
        hidden_declarations=["echo"],
    )

api.register_tool(ExtensionTool(
    name="run_tools", description="Runs tools.", input_schema={"type": "object"},
    handler=run_tools, exposure="model-only", prepare_loadout=present,
))
```

- The function receives a `ToolLoadout` with three tuples of tool
  definitions in registration order: `declared_tools` (the tools declared
  to the model, with their original descriptions), `callable_tools` (what
  `ctx.execute_tool` and scripts can call) and `registered_tools` (every
  registered tool, without those `--tools`, `--exclude-tools` or
  `--no-tools` removed). A definition has `name`, `description`,
  `input_schema`, `exposure`, `namespace`, `annotations` and
  `output_schema`.
- It returns `ToolLoadoutChanges`, or `None` to change nothing.
  `descriptions` maps the names of declared tools, also the tool's own, to
  the description the model is to see. `hidden_declarations` names declared
  tools whose declarations requests leave out: they stay active and
  callable, the transcript still records them as declared, and the system
  prompt leaves them out of its tool list and rules. A tool that hides
  declarations should present those tools itself, as `codemode` lists them
  in its description with mode `only`.
- It runs only while its tool is declared, once for each new set of
  active and registered tools (a change of the active tools, an MCP server's
  tools arriving, `/reload`). pipy keeps the result: the descriptions, the
  hidden declarations and the system prompt's tool list come from the same
  call. It may run on any thread that reads the declarations; should two
  threads read them for a new set at once, both call it and the first
  result is used.
- When several declared tools have the function (the built-in `codemode`
  counts as one), their changes are merged in the order the tools are
  declared: a later tool's description of a name replaces an earlier one's,
  and the hidden declarations add up.
- A function that raises, or returns something that is not
  `ToolLoadoutChanges` with texts by name and a collection of names,
  changes nothing. pipy shows one warning for it, `pipy: prepare_loadout of
  tool "<name>" failed: <error type>`, and goes on with the other tools' changes.
- The changes never alter what is registered, active or callable, or what
  `ctx.get_all_tools()` reports.

Together with `exposure`, `reaches_tools` and `ctx.execute_tool` this is
everything the built-in `codemode` uses of the tool system, so an extension
can build a tool that behaves like it under another name.

Different from Pi:

- Pi calls the function when the active tools change and again before
  each request, and keeps the result until the next call. pipy calls it
  once per set of active and registered tools, so a function whose answer
  changes without the tools changing is not asked again before the next
  request.
- Pi's loadout has `getExposure(name)`, `getNamespace(name)` and
  `getPromptGuidelines(name)`. In pipy the exposure and the namespace are
  fields of the definitions; extension tools have no prompt guidelines, and
  those of the built-in tools are not given.
- Pi reports a failing function as an error of its extension each time. pipy
  warns once per session, tool and error type, naming the tool and the type
  but not the error's text.

### Replacing built-in tools

The names of the seven built-in file and shell tools are reserved. The
names `codemode` and `tool_search` are not: an extension that registers a
tool of one of these names replaces the built-in tool of that name for the
session (Pi: its built-in `codemode` and `tool-search` extensions are
replaceable).

- The extension's tool is the one that is registered, declared and called.
  The built-in `codemode` is then never registered, so its runtime is not
  probed, and the built-in presenter of the loadout does not run; a
  replacement presents the tools with its own
  [`prepare_loadout`](#presenting-the-loadout) and runs them with
  [`ctx.execute_tool`](#running-other-tools).
- [MCP](mcp.md#control-tool-exposure) activates `codemode` for servers with
  `codemode` tools and `tool_search` for servers with `deferred` tools.
  It activates a replacement only when that tool declares `reaches_tools`;
  a tool of the name without it is left as it is, and when nothing else
  reaches the servers' tools pipy warns that they cannot be called. A
  replacement that should stay undeclared until MCP needs it registers with
  `default_active=False`.
- Before a call of a tool that declares `reaches_tools`, pipy waits for the
  servers the call may reach; a replacement without it is called at once.

An extension that registers the `/mcp` command replaces the built-in MCP
support; see [MCP](mcp.md#replace-the-built-in-mcp-support).

### Tool rendering

An `ExtensionTool`'s `render_call` and `render_result` draw the rows of its
calls (slice 17 below). `api.register_tool_renderer(resolver)` chooses the
renderers of the calls to any tool, also of a tool that is not registered:
one that an extension of an earlier session had, or one the model made up.

```python
from pipy_harness.extensions import ToolRenderers, lines_component


def activate(api):
    mine = ToolRenderers(
        render_call=lambda ctx: lines_component([f"jira {dict(ctx.args)}"]),
        render_result=lambda ctx: lines_component([ctx.content or ""]),
    )

    def resolve(tool_name, next):
        return next() or (mine if tool_name.startswith("jira_") else None)

    api.register_tool_renderer(resolve)
```

- `resolver(tool_name, next)` returns a `ToolRenderers`, an `ExtensionTool`,
  any object with the attributes `render_call` and `render_result` (each a
  callable or `None`), or `None`.
- `next()` returns what the resolvers registered after this one, then the
  tool itself, would use. Resolvers are asked in extension load order, and in
  the order one extension registered them. `next() or mine` therefore only
  fills in; returning `mine` without calling `next()` replaces the rows of
  that tool, a built-in tool's included, and returning `None` without it
  gives the tool pipy's generic row.
- What the tool itself has: for an extension tool that has a renderer, the
  `ExtensionTool`; for a built-in tool and for a call to
  `mcp__<server>__<tool>` (whether such a tool is registered or not), an
  object that stands for pipy's own rows. That object has `render_call` and
  `render_result` set to `None`: return it to keep pipy's rows, but there is
  nothing in it to call. For every other name it is `None`.
- A renderer of the returned object that is `None` leaves that part to
  pipy's generic row (`name key=value …`, the first 10 result lines), also
  when the tool is a built-in or MCP tool: nothing of pipy's own row for
  that tool is kept, including the diff preview of `edit`.
- A resolver is asked when a call's row starts and when a session's
  transcript is drawn again, which is before any request in a resumed
  session. The row keeps what was chosen for it.
- A resolver that raises or returns something else is left out, as if it
  had returned `next()`. Passing something that is not callable to
  `register_tool_renderer` disables the extension with
  `invalid_tool_renderer`.

Different from Pi:

- In Pi `next()` returns a registered tool's definition also when it has no
  renderers, and the built-in tools' and the MCP extension's real renderers,
  which a resolver can call and wrap. pipy returns `None` for an extension
  tool without renderers and a marker for its own rows.
- Pi's `renderShell` is not part of `ToolRenderers`.

`/export` to HTML draws the calls with the same renderers and stores the
rows in the file (see `docs/export-distribution.md` "Artifact shape"): an
extension tool's `render_call` and `render_result`, and what a resolver
chose, are called with `expanded` false and true, a `width` of 98 and
arguments, result text and details from which credentials were removed. As
in Pi, the export page draws the built-in tools itself, so renderers that a
resolver chose for a built-in tool are not used there. `pipy --export` and
`/share` do not run extension code.

<a id="mcp-servers"></a>
### MCP servers

`api.register_mcp_server(name, config)` adds an MCP server for the session
(Pi `registerMcpServer`). `config` is a dict with the shape of an
`mcpServers` entry of [`mcp.json`](mcp.md#configure-servers): `command`,
`args`, `env` and `cwd` for a stdio server, `url`, `headers`, `oauth` and
`auth` for an HTTP server, and `exposure`, `toolExposure`, `description`,
`enabled` and `timeout`.

```python
from pipy_harness.extensions import McpServerRegistrationError


def activate(api):
    api.register_mcp_server(
        "jira", {"url": "https://mcp.example.com/jira", "exposure": "codemode"}
    )

    def drop(ctx, args):
        api.unregister_mcp_server("jira")

    api.register_command("drop-jira", "Disconnect the jira server", drop)
```

- A server registered while the extension loads (in `activate`), or in a
  `session_start` handler, connects when the session's servers start, with
  those of `mcp.json` and after them in every list. A server registered
  later, from a command, a hook or a tool handler that kept `api`, connects
  at once, in the background.
- Nothing is saved: register on every load, for example from the
  extension's own settings. `/reload` loads the extensions again and
  keeps only what they register then; an `api` of the extensions it
  replaced raises `McpServerRegistrationError`.
- Registering a name again replaces the extension's registration. With the
  same configuration nothing happens; with another one the server's tools
  go away, its connection is closed, and it connects again once that
  connection has closed. The order of the keys of `toolExposure` is part of
  the configuration, because the first matching pattern decides.
- `api.unregister_mcp_server(name)` closes the connection of a server this
  extension registered and makes its tools unreachable. A name it did not
  register is left alone.
- `api.get_mcp_servers()` returns copies of every registration, in the order
  the names were first registered, as `RegisteredMcpServer(name, config,
  extension_path)`. `extension_path` is the path label of the extension
  (`.pipy/extensions/jira.py`), which is what `/mcp` and the messages show.
  Two extensions can have one label (two `--extension` directories of the
  same name); they are still two owners. While an extension loads, the list
  does not have its own registrations yet.
- A server in `mcp.json` with the same name, or a name that differs only in
  `-` and `_`, takes precedence: the registered one does not connect, and
  `/mcp` lists it as overridden (see [`/mcp`](mcp.md#the-mcp-command)).
- A registered server is in `/mcp` with `extension` as its source. Enabling,
  disabling and changing its exposure there last for the session and are
  not saved; they are undone when the server is registered with another
  configuration.
- With MCP [turned off](mcp.md#turning-mcp-off) registered servers do not
  connect.

After a server was registered or unregistered, hooks for
`mcp_servers_change` receive a `McpServersChangeEvent` whose `servers` is
every registration after the change, as copies:

```python
@api.on("mcp_servers_change")
def changed(event, ctx):
    for server in event.servers:
        ctx.ui.notify(f"{server.name} from {server.extension_path}")
```

- What extensions register while they load is not an event; read it with
  `api.get_mcp_servers()` in a `session_start` handler. A change made in
  a `session_start` handler is an event like a later one.
- The hooks run on the session thread, where the session shows its
  notices (before a prompt starts and when it becomes idle) and when a turn
  starts, one event per change and in the order of the changes, whatever
  threads made them. A hook that raises is skipped. A hook that registers
  or unregisters a server causes another event, which is fired at the next
  of those points.
- pipy connects registered servers itself, so the event is for an extension
  that connects them in pipy's place: one that
  [replaces the built-in MCP support](mcp.md#replace-the-built-in-mcp-support)
  with its own `/mcp` command, or one that runs while MCP is turned off. With MCP
  [turned off](mcp.md#turning-mcp-off) or replaced, and no hook for the event, nothing
  connects a registered server; pipy then reports each registration once
  per load of the extensions, also one that was unregistered again right
  away:

  ```text
  Extension ".pipy/extensions/jira.py" error: MCP server "jira" is registered, but no loaded extension connects MCP servers; another extension may have replaced the built-in MCP support
  ```

`register_mcp_server` raises `McpServerRegistrationError` (a `ValueError`):

| Case | Message |
| --- | --- |
| A name with other characters than letters, digits, `_` and `-`, or an entry `mcp.json` would refuse | `Invalid MCP server registered by extension "<path>": <reason>` |
| A name another extension registered | `MCP server "<name>" is already registered by extension "<path>"` |
| A name that differs from a registered one only in `-` and `_` | `MCP server "<name>" conflicts with registered server "<other>"` |

An extension that catches the error goes on. One that lets it leave
`activate` is disabled with `activation_error`, like for any other
exception, and nothing it registered is kept.

Different from Pi:

- Extensions that load before the project is trusted (user-level and
  `--extension` ones, when a `project_trust` decision is pending) do not see
  each other's registrations while they load, so two of them can register
  one name without an error. When they are finalized the later one is
  disabled with `duplicate_mcp_server`, as for a command name that is taken.
  Between loading and that point, a `project_trust` handler cannot register
  or list servers (`ExtensionCapabilityError`).
- One extension that registers `my-server` and `my_server` while it loads is
  refused the second name. Pi notices that clash only for a name that was
  registered before the extension loaded.
- `codemode` or `tool_search` is activated for a server registered during a
  run when the next turn starts, and for one registered while the session is
  idle before the next prompt; Pi activates it in the same step as the
  registration. The model's next request has it in both.
- Pi fires `mcp_servers_change` inside the call that registers; pipy fires
  it on the session thread at the points named above, so a server
  registered from another thread while the session waits for input is
  announced when the next prompt is sent. The server itself connects at
  once in both.
- In Pi the built-in MCP support is itself a handler of the event, and the
  report means that another extension replaced it. pipy's MCP support is
  not an extension; the report appears when it is turned off. The text is
  Pi's.
- A server that fails or needs a sign-in after it was registered later is
  reported like the [startup report](mcp.md#startup-report), with only that
  server in it, where the session shows its notices: before the next prompt.

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
  `editor(title, prefill) -> str | None`. These simple command/shortcut
  primitives now ship; `editor` also supports Pi's `$VISUAL`/`$EDITOR`
  external-editor handoff from the resolved `app.editor.external` keybinding
  (default Ctrl+G) in interactive TTY sessions. Ctrl+G is a reserved app binding;
  an extension that registers it as a shortcut is disabled rather than shadowing
  the editor.
- Status and indicators **(shipped for command/shortcut contexts)**:
  `set_status(key, text)`, working-message and working-indicator controls, and
  `ctx.ui.set_hidden_thinking_label(label=None)` /
  `setHiddenThinkingLabel(label=None)`, mirroring Pi's `setStatus` /
  `setWorkingMessage` / `setWorkingIndicator` / `setHiddenThinkingLabel`. The
  hidden-thinking label is live-only: it changes the label shown while thinking
  is folded, `None` restores Pi's default `Thinking...`, and headless contexts
  no-op like Pi RPC.
- Widgets and chrome: `set_widget(key, content, options)` above/below the
  editor, plus custom header/footer/title (Pi's `setWidget` / `setFooter` /
  `setHeader` / `setTitle`).
- Editor integration **(partly shipped)**: command/shortcut contexts can
  read/write the core input editor text and paste literal text at the current
  cursor using Pi-canonical camelCase (`ctx.ui.getEditorText`,
  `ctx.ui.setEditorText`, `ctx.ui.pasteToEditor`) plus Python convenience
  aliases (`ctx.ui.get_editor_text`, `ctx.ui.set_editor_text`,
  `ctx.ui.paste_to_editor`). The Pi-shaped custom editor component integration
  also ships (`ctx.ui.set_editor_component` / `setEditorComponent` and
  `ctx.ui.get_editor_component` / `getEditorComponent`): live contexts call the
  factory, route keys to the returned component, wire submit/change callbacks,
  forward autocomplete providers, pass a bounded keybinding/action adapter,
  wire app-action handlers plus `onEscape`/`onCtrlD`/`onPasteImage` callbacks
  for custom-editor delegation, preserve text when clearing, and headless
  contexts no-op/return `None` like Pi RPC. Ctrl-C remains outside the delegated
  custom-editor handler map, preserving the process-level interrupt path. The
  live getter returns the configured factory even when construction fails soft or
  returns no active component, matching Pi's stored-factory semantics. Broader Pi
  component-library parity remains deferred.
- Custom overlays **(bounded options + handle shipped)**: a focused custom
  component with keyboard focus, mirroring Pi's `custom(...)` overlay. The live
  product TUI accepts `custom(factory, options=None)` with Pi-shaped `overlay`,
  `overlayOptions`/`overlay_options`, and `onHandle`/`on_handle` fields; both
  overlay modes use pipy's existing inline overlay renderer in this bounded
  slice, while width hints, handle callbacks, dispose-on-close, and headless
  no-construction behavior are covered. The handle exposes Pi-shaped `hide`,
  `setHidden`, `isHidden`, `focus`, `unfocus`, and `isFocused`; in pipy's
  single-overlay bounded path, hidden overlays skip render/input, unfocused
  visible overlays keep rendering but skip input, and explicit `unfocus` targets
  are accepted but ignored until a real overlay focus graph ships.
- Tool-output expansion **(shipped)**: `ctx.ui.get_tools_expanded()` /
  `getToolsExpanded()` read the current live product-TUI expansion state, and
  `ctx.ui.set_tools_expanded(expanded)` / `setToolsExpanded(expanded)` set the
  same state used by the built-in toggle and repaint the frame. Headless/RPC-like
  contexts return `False` and no-op writes, matching Pi.
- Autocomplete: stack an additional autocomplete provider on top of the
  built-in one, mirroring Pi's `addAutocompleteProvider`.
- Theme controls **(shipped)**: read the active theme (`ctx.ui.theme`), list
  available themes (`ctx.ui.get_all_themes`), load a theme by name without
  switching (`ctx.ui.get_theme`), and switch themes (`ctx.ui.set_theme`),
  mirroring Pi's `theme` / `getAllThemes` / `getTheme` / `setTheme`. (This is
  theme selection, not theme registration; themes are contributed via
  `resources_discover`.) The read members are *ambient* — they resolve the
  globally-installed package theme registry plus `PIPY_THEME`/the chrome store,
  so they work deterministically even headless (more capable than Pi's headless
  `[]`/`undefined` no-ops). `set_theme` mutates the live theme and so requires a
  live UI: headless it returns `{"success": False, "error": "UI not available"}`
  without touching process state, matching Pi's headless `setTheme`; a live call
  reuses the `/settings` mechanism (`select_theme`, which sets `PIPY_THEME` so the
  next frame repaints and persists the non-secret theme name). `get_all_themes()`
  keeps Pi's `{name, path}` dict shape but `path` is always `None`: pipy's session
  theme registry retains only `name -> palette` and does not leak package theme
  file paths to extension code ("only the palette name reaches any persisted
  state").
- Raw terminal input subscription **(shipped for live command/shortcut
  contexts)**: `ctx.ui.on_terminal_input(handler)` / `onTerminalInput(handler)`
  register a decoded-key listener before built-in editor handling and return an
  idempotent disposer. A handler may return `{"consume": True}` to swallow the
  key or `{"data": "..."}` to replace the key string for later listeners and
  built-in handling. Headless contexts return a no-op disposer and never invoke
  the handler, matching Pi RPC. Inputs are live-only and are not archived.

Every UI method must have deterministic behavior in non-interactive contexts:
return a safe default, raise a typed unsupported-mode error, or record a safe
local diagnostic without blocking forever.

## Providers And Models

Provider registration now ships through pipy's native catalog. The Pythonic
surface is not Pi's `models.json` shape copied verbatim, but a registration
object that composes with `ProviderPort`:

```python
@dataclass(frozen=True)
class ExtensionOAuthConfig:
    name: str
    login: Callable[..., object]
    refresh_token: Callable[..., object]
    get_api_key: Callable[..., object]
    modify_models: Callable[..., object] | None = None
    is_subscription: bool = False

@dataclass(frozen=True)
class ExtensionProvider:
    name: str
    default_model: str | None
    models: Sequence[str]
    factory: Callable[["ProviderContext"], "ProviderPort"]
    oauth: ExtensionOAuthConfig | None = None
```

`ExtensionOAuthConfig` preserves Pi's `ProviderConfig.oauth` metadata (`name`,
`login`, `refreshToken`, `getApiKey`, optional `modifyModels`, and
`isSubscription`) using Python snake_case. With `is_subscription=True` and a
stored OAuth login, the footer marks the session cost `(sub)`, as Pi does; a
non-bool value fails activation. Pipy validates malformed OAuth metadata fail-closed during extension
activation and preserves the callbacks without invoking them during activation or
provider construction. Pi derives the OAuth provider id from the registered
provider name; pipy uses the normalized provider name as that id. For activated
OAuth-backed extension providers, `/login <provider>` calls `login(callbacks)`
with deterministic stdio callbacks (`onAuth`, `onDeviceCode`, `onPrompt`,
`onSelect`, `onProgress`, plus snake_case aliases), then stores the returned
mapping as `{"type": "oauth", ...credentials}` in pipy's `AuthStore` under the
provider name. `/logout <provider>` removes that stored entry. OAuth-backed
extension provider rows are unavailable with `login-required` until that entry is
present; non-OAuth extension providers remain factory-owned and available as
before. The declarative API-backed provider config
(`baseUrl`/`api`/`apiKey-or-oauth`) and passing shared credentials into arbitrary
provider factories remain deferred.

Provider extensions must not receive existing auth stores wholesale. Their
factory receives only `ProviderContext(provider_name, default_model, model_id)`;
it never receives the live or reload-shadow catalog/auth owners. Built-in
catalog-backed provider instances are likewise self-contained from resolved
scalar and copied credential/header/routing values. A reload-time auth/catalog
rotation can refuse the prepared candidate and affects future construction, but
cannot retarget an already accepted provider instance. If settings reload leaves
no auth owner, extension reload refuses without partial publication and still
runs the ordinary retained-provider/unknown-tool-filter refusal path; the
retained refresh returns before mutation. Restoring an explicit `AuthStore`
through the supported owner surface lets a later reload prepare normally, but
pipy never accepts a candidate while that owner is absent. Provider extensions
should either read their own environment variables or use the bounded OAuth
login capability with explicit provider labels.

Mirroring Pi, the provider surface supports both registration and
`unregister_provider(name)`. Registered providers contribute temporary per-run
catalog rows, appear in `--list-models` and `/model`, resolve at startup when
their extension is loaded, and construct through the registered `ProviderPort`
factory. `/reload` recomputes those contributions from current extension
discovery, so removed/disabled extension providers disappear. Unregister hides
the extension contribution and restores any built-in rows it overrode without
mutating the built-in catalog or `models.json`.

Provider ports that perform their own HTTP call use the public
`apply_provider_headers(request, headers)` helper from
`pipy_harness.extensions` at their final mutable-header seam. The helper fires
the active `before_provider_headers` callback once, works on a copy, and removes
`None` deletion sentinels before transport. Pipy's built-in adapters already do
this; delegating ds4 relies on the shared Chat Completions adapter so it does not
double-fire.

OpenAI Codex follows Pi's narrower ownership split: the hook sees the
request-scoped additional-header map, then the adapter derives provider-owned
Authorization/account and SSE/WebSocket protocol headers afterward. Custom
additions reach both transports and are reused across retry/fallback, but a hook
cannot inspect, override, or delete those later provider-owned Codex fields.

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
Extensions can also close over `api.get_flag(name)`: during activation it
returns that extension's registered default (or `None` for unregistered names),
and after startup parsing it returns the run-local parsed override for that
extension's own flag.

Supported forms are `--flag`, `--flag=true|false`, `--name value`, and
`--name=value`. For one-shot `--print`/`--mode json` runs that also need a
positional prompt, prefer `--name=value`; the space-separated string form is
ambiguous with the prompt positional. Dynamic flags apply only to `pipy repl`
(the product tool-loop session); top-level `run` and the package-manager
commands remain strict and do not consume extension flags.

`/reload` reactivates extensions and reparses the original per-run extension
flag tokens. If the reloaded extension set no longer accepts those tokens, pipy
reports the flag error and keeps the last valid flag values and retained TUI
chrome/listeners for the existing session instead of clearing them mid-run.
Candidate widgets, title, indicator, terminal-input listeners, autocomplete
providers, editor component, and hidden-thinking label are never delivered on
that rejection.

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
   tool payload, or UI text leaks into the metadata-only workflow
   archive/catalog.
9. explicit CLI source-loading paths for extensions, skills, prompt templates,
   and themes still load when matching default discovery or persisted filters
   are disabled.

## Product Session, Archive, And Privacy Rules

The immutable native product session tree is pipy's full-content durable
conversation store. It is separate from the `pipy-session` workflow
archive/catalog, whose DTOs are explicitly allowlisted and metadata-only. Full
content from live extension and JSON/RPC surfaces never crosses into the
workflow archive/catalog. There is no transcript sidecar: the former opt-in
sidecar path was removed rather than retained as a third store.

Metadata-only workflow archive/catalog records may include:

- extension name, version, and path label
- manifest validation status
- registered command/tool/provider names
- hook names and safe counts
- safe policy outcomes such as "blocked by protected-paths"

Metadata-only workflow archive/catalog records must not include:

- extension source code
- command arguments that may contain prompts or secrets
- tool arguments beyond existing safe native metadata
- tool results
- UI text
- injected context text
- provider payloads or credentials

Full-content conversation persistence belongs only to the native product
session tree. The removed opt-in transcript-sidecar path is not a current
storage or extension surface.

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
- `api.on("agent_settled")`
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
{"feature":"agent_settled","ok":true}
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
11. True idle records `agent_settled` after no continuation remains.
12. Session shutdown or reload proves cleanup through `session_shutdown`.

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
`pipy_harness.native.extensions.packages.discover_extensions` implements the inventory
boundary (`tests/test_native_extension_discovery.py`, gate
`scripts/parity_checks/extension_discovery_conformance.py --json`). Slice 2:
`pipy_harness.native.extensions.activation.activate_extensions` imports only
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
`turn_end`/`agent_end`/`agent_settled`/`session_shutdown`, gate
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

When adding a shipped item to the numbered implementation-slice list below,
renumber subsequent entries in the same change so the Markdown source stays
unambiguous for reviewers and future diffs.

Beyond the first numbered slices, an **interactive command-context block** has also
landed to support porting Pi's `answer.ts`
(`docs/examples/extensions/answer.py`): the command/shortcut context now exposes
`ctx.conversation.last_assistant_message()` (read-only; its `complete` is
false for an aborted or failed turn, Pi `stopReason !== "stop"`), a read-only
`ctx.session_manager` / `ctx.sessionManager` view over the active native session
(id/file/dir/cwd/header/entries/labels/branch/tree/name), a bounded one-shot
`ctx.complete(system_prompt, user_text)` on the active provider, a full-screen
`ctx.ui.custom(factory, options=None)` interactive overlay
(`TerminalUi.run_custom_component`), and
`api.register_shortcut(key, handler)` keyboard shortcuts. The `custom` options
surface accepts Pi-shaped `overlay`, `overlayOptions`/`overlay_options`, and
`onHandle`/`on_handle` fields: pipy's bounded inline overlay path honors static
or callable width hints, exposes a minimal hide/update/requestRender handle,
disposes components on close, and keeps headless contexts as no-construction
no-ops. The simple UI
primitive follow-on then added `ctx.ui.select`, `ctx.ui.input`,
`ctx.ui.confirm`, `ctx.ui.set_status`, `ctx.ui.set_working_message`, and
`ctx.ui.set_working_visible`: interactive runs delegate to the product TUI
(simple overlays, live status rows, and working-label/visibility controls),
while non-interactive runs return cancel/default values and record status
deterministically without blocking. These are covered by
`tests/test_native_extension_{conversation,completion,custom_ui,custom_ui_pty,shortcuts}.py`,
`tests/test_native_coding_session_terminal.py`, `tests/test_example_answer_extension.py`,
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
   Implemented as `pipy_harness.native.extensions.activation.activate_extensions`
   + `PipyExtensionAPI` + `ActivatedExtension`, with the public
   `pipy_harness.extensions` surface.
3. Command dispatch — **landed**: run extension slash commands in the tool-loop
   REPL product path with safe diagnostics and no provider turn by default,
   after built-ins/custom commands (no shadowing), with menu listing and
   `/reload` re-activation. Implemented as
   `pipy_harness.native.extensions.dispatch.dispatch_extension_command` +
   `extension_command_map` + a mode-aware `CommandContext`/`ExtensionUi`, wired
   into `coding.session`.
4. `tool_call` policy hook — **landed**: extensions register
   `@api.on("tool_call")` to inspect live parsed tool inputs and block built-in
   tool calls with safe reasons. Implemented as `api.on(...)` +
   `ToolBlock`/`ToolCallEvent` + `dispatch_tool_call_hooks`/`extension_tool_call_hooks`,
   wired into the `coding.session` tool loop (first block wins; crashing hook
   fails closed; raw inputs inspected live but not archived).
5. Lifecycle foundation — **landed**: emit `session_start`, `session_shutdown`,
   `agent_start`, `turn_start`, `turn_end`, `agent_end`, and true-idle
   `agent_settled` with mode-aware contexts and safe archive metadata only.
   `agent_settled` waits until queued continuations drain, still fires after an
   unexpected mid-run failure, and remains separate from JSON/RPC's mode-owned
   protocol event. Implemented as `LifecycleEvent` +
   `dispatch_lifecycle_hooks` + `extension_event_hooks`, fired after the
   synchronous canonical automation projection by the tool-loop composition
   sink. Automation failure propagates before an extension hook runs; extension
   observation remains fail-soft.
6. Input and before-agent-start hooks — **landed**: support `input` transforms,
   `before_agent_start` context/system-prompt modifications, and
   `send_user_message(...)` enough for a command to trigger a deterministic
   provider turn. Implemented as `InputEvent`/`InputTransform`,
   `BeforeAgentStartEvent`/`BeforeAgentStartResult`, `QueuedUserMessage`,
   `dispatch_input_hooks`/`dispatch_before_agent_start_hooks`,
   `api.send_user_message` + `drain_user_messages`, wired into the
   `coding.session` prompt/turn path. Pi-shaped custom messages also now
   ship for local session/display use: `api.send_message` / `api.sendMessage`
   and command/shortcut `ctx.send_message` / `ctx.sendMessage` accept
   `{customType, content, display?, details?}` plus optional `triggerTurn` /
   `deliverAs` options, append a bounded `custom_message` entry, and render
   displayed content locally. `triggerTurn` starts an idle deterministic
   provider turn, `deliverAs: "nextTurn"` queues custom context for the next
   accepted provider turn, and `deliverAs: "steer"` / `"followUp"` queue
   provider-visible content through pipy's steering/follow-up drain.
7. Pure/read-only tool registration — **landed**: add extension tools to the
   bounded tool loop using existing JSON-schema validation and output bounds.
   Implemented as `ExtensionTool`/`ToolResult`/`RegisteredTool` +
   `api.register_tool` + `extension_tools` + the `_ExtensionToolPort` adapter
   wired into `coding.session`'s per-run tool registry.
8. Tool result hooks — **landed** (`tool_result` transforms): an extension
   `@api.on("tool_result")` handler transforms the bounded observation
   (`ToolResultEvent`/`ToolResultTransform` + `dispatch_tool_result_hooks`)
   before the model sees it, chained + fail-safe + bounded. (Pi-shaped
   `content`+`details` blocks and bounded progress/update events remain a
   later refinement.)
9. Minimal UI notifications — **landed**: `ctx.ui.notify` from a command or
   hook handler surfaces to the live UI via a notify sink threaded through the
   dispatchers / tool adapter / canonical event composition sink; deterministic
   (records + sink) in non-interactive mode.
10. Golden conformance extension — **landed**: the golden
    `docs/examples/extensions/pipy-extension-conformance.py` + product-path proof
    test (`tests/test_native_extension_conformance.py`) + gate
    (`scripts/parity_checks/extension_conformance_gate.py --json`). A single
    `/pipy-extension-conformance` trigger writes all required feature markers
    (now including the slice-18 chrome markers `set_widget`/`set_header`/
    `set_footer`/`set_title`/`set_working_indicator`) and the proof leaks no
    prompt/tool/UI bodies.
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
    `PIPY_THEME` / settings) and re-colors the chrome. Package skills and
    prompts come first, as in Pi (a package resource wins a name collision);
    extensions and themes sit at lowest precedence. All four honor the
    `+/-pattern` filters; package resources are
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
    pre-turn hook contexts and extension-tool handlers expose
    `ctx.set_active_tools(...)`,
    `ctx.set_model(...)`, and `ctx.set_thinking_level(...)` through the
    existing provider-state, session-tree, and tool-registry boundaries. Each
    offered model mutation stays bound to the context's creating generation;
    `set_model` prepares the provider unlocked, atomically checks and commits
    selection/binding/history/usage under the session mutex, then performs
    fail-soft default persistence and presentation after unlock. Stale, gated,
    terminal, failed-construction, and superseded calls return `False` without a
    partial effect. A tool-incompatible resolved target retains the established
    explicit-thinking-level side effect while leaving selection/coding state
    unchanged. `session_before_compact` also offers model replacement; during
    automatic compaction, success invalidates the active run and closes the
    session with `CodingContextChangedError`. Manual compaction outside a run
    remains valid. In-turn provider/tool hooks reject `ctx.set_model(...)` by
    returning `False` so they cannot clear conversation state mid-turn.
    `ctx.set_active_tools([])` is a
    real empty active-tool set, disabling model-visible tools until a later
    context selects known tool names again. `user_bash` hooks can block,
    rewrite, exclude, or synthesize a local `!`/`!!` shell result;
    `before_provider_request` hooks can transform bounded prompt fields and
    narrow tools for the current request; session-before hooks fail closed for
    stateful session operations. Phase 2.2b.3 routes definition lookup and
    execution through the canonical `AgentToolCapabilities` port backed by the
    product `NativeToolCapabilities` facade without changing extension callback
    order or payloads. Phase 2.2b.4a closes the request-authorization boundary:
    returned `available_tools` values may only narrow the current detached
    snapshot, serial hooks cannot re-enable a removed name, and a provider call
    outside the exact final advertised set becomes the normal budget-consuming
    policy error before semantic hooks, execution, result hooks, or invocation
    counting. Phase 2.2b.5b routes the same ordering through the canonical
    loop-policy transition plus a synchronous product adapter; `tool_result`
    hooks can transform content but cannot replace request identities, error
    status, or added-tool metadata. `ctx.set_active_tools(...)` still mutates the active set for later
    provider iterations; it does not retroactively alter the already-built
    current request without an explicit narrowing transform. Gate
    `scripts/parity_checks/extension_live_session_conformance.py --json`.
    Extension-registered provider ports continue to receive ordinary recursively
    JSON-serializable tool-schema dictionaries/lists; the immutable authorization
    snapshot remains internal to the agent loop.
14. Dynamic extension CLI flags — **landed for `pipy repl` tool-loop runs**:
    `ExtensionFlag`/`RegisteredFlag`, `api.register_flag(...)`,
    `api.get_flag(...)`, `extension_flags(...)`, and
    `parse_extension_flag_tokens(...)` collect boolean/string flags from
    activated extensions, parse only the leftover unknown CLI tokens after
    built-in argparse handling, fail closed before a provider turn on
    unknown/malformed tokens, and expose values as `ctx.flags` plus the
    extension-owned `api.get_flag(...)` closure to commands, shortcuts,
    extension tools, and hook contexts. Product-path tests cover `pipy repl
    --extension <file> --plan --ticket PIPY-123`.
15. Simple extension UI primitives — **landed for command/shortcut contexts**:
    `ctx.ui.select`, `ctx.ui.input`, and `ctx.ui.confirm` run simple
    product-TUI overlays and return cancel/default values in headless mode.
    `ctx.ui.set_status` renders bounded live status rows; and
    `ctx.ui.set_working_message` / `ctx.ui.set_working_visible` control the
    provider-turn working row for subsequent turns until changed again or reset
    by the extension. This slice was still short of Pi's full
    widget/component surface:
    a custom editor component and broader extension state/session-manager helpers
    remained follow-ons at the time.
    (Custom tool renderers landed in slice 17 below; the persistent chrome
    widget/header/footer/title/indicator surface landed in slice 18 below; the
    first-class editor helper landed separately in slice 20.)
16. Custom session entries and message renderers — **landed for command/shortcut
    contexts**: handlers can call `ctx.append_entry(custom_type, data)` to append
    a durable, non-LLM `custom` entry to the native product session tree and
    receive its id. `api.register_message_renderer(custom_type, renderer)` is
    the separate `CustomMessageEntry` surface used by `send_message`; its
    bounded one-argument text compatibility remains supported. Non-JSON entry
    data is converted before persistence, and `custom_type` is a command-shaped
    lowercase identifier (1-200 characters). Durable entry display now belongs
    to slice 29's separate `register_entry_renderer`, correcting the earlier
    pipy-only routing of `append_entry` through message renderers.
    The rich **Component** upgrade landed in slice 19 below (rich-UI item C): a
    renderer that **requires** a second positional parameter `(data, ctx)`
    receives a `MessageRenderContext` and may return a component, committed
    SGR-preserving under the `custom_message_custom` kind with no forced
    `[custom_type]` label; a 1-arg `renderer(data)` keeps the slice-16 plain
    behavior described above. See slice 19 for the required-param rule and
    the fail-soft semantics.
17. Custom tool renderers — **landed**: an `ExtensionTool` may carry optional
    `render_call(ctx)` and `render_result(ctx)` callables that return a
    `ToolRenderComponent` (use the `lines_component(...)` convenience to wrap
    pre-rendered lines). pipy dispatches them when rendering that extension's own
    tool rows — in the product TUI and in captured (non-TTY) output. In the TUI
    their lines fill the tool's Pi box (pending, then success or error
    background; `docs/tui-workflow.md` "Tool rows"). The renderer receives a
    read-only `ToolRenderContext` (`tool_name`, `args`, `is_result`, `is_error`,
    `content`, `details`, `expanded`, `width`, `theme`, and a `state` mapping
    shared from `render_call` to `render_result` for one tool execution); the
    extension's `ToolResult.details` reaches `render_result` on the tool result
    itself, and like Pi the session file stores it (the JSON copy; a value that
    does not survive a JSON round trip becomes `None`, live and restored
    alike). It is never sent to the provider. A bounded `ToolRenderTheme`
    (`theme.fg(color, text)` / `theme.bold` / `theme.dim`, with semantic colors
    `text`/`accent`/`success`/`warning`/`error`/`dim`) maps onto the active
    chrome palette and emits plain text when color is disabled (captured /
    `NO_COLOR`). In the TUI the row keeps its inputs and both renderers run
    again on every redraw — Ctrl+O, live and after a resume — with the new
    `expanded` flag and the same `state`, like Pi's `setExpanded`; `width` is
    the box's content width (the terminal width less two columns). The whole
    path is **fail-soft**: a renderer (or its `render()`) that raises, returns a
    non-component, or returns an uncoercible value falls back to Pi's generic
    rows (`name key=value …` and the first 10 result lines). Deferred: live
    invalidation/partial updates and `renderShell:"self"` self-framing.
    A built-in tool's rows can be replaced with `api.register_tool_renderer`
    (see "Tool rendering").
    The extension tool-renderer map now refreshes on `/reload`: renderers added,
    removed, or changed by the reloaded extension generation are applied to the
    existing session renderer without restarting, and stale renderers disappear
    with removed/disabled extensions. Known limitation: the captured/non-TTY path
    dispatches
    `render_call`/`render_result` with a **fixed width (80 columns) and
    `expanded=False`** because it has no live terminal width or Ctrl+O expansion
    state; only the interactive TUI passes the real terminal width and the live
    expansion state in the `ToolRenderContext`.
18. Persistent chrome widgets — **landed**: a command/shortcut/lifecycle context
    exposes `ctx.ui.set_widget(key, content, placement=...)` (a keyed,
    insertion-ordered region pinned `"above_editor"` or `"below_editor"`),
    `ctx.ui.set_header(factory)`, `ctx.ui.set_footer(factory)`,
    `ctx.ui.set_title(title)`, and `ctx.ui.set_working_indicator(frames,
    interval_ms)`. Content is plain lines, a `lines_component(...)`, or a
    Pi-shaped `(tui, theme)` / `(tui, theme, footer_data)` factory returning a
    component with `render(width)`; the earlier `theme` / `(theme, footer_data)`
    Python factory shape remains accepted for local compatibility. The `tui`
    handle exposes `requestRender(force=False)` and `request_render(...)` to
    repaint the live chrome frame without a provider turn. The header factory
    receives the active chrome theme; the footer factory also receives a
    `FooterData` (`git_branch`, `extension_statuses`). Rendering is **live**:
    factory/widget/header/footer components are retained and their `render(width)`
    method is called on every frame, with optional `invalidate()` still called on
    width changes before re-render. Header and footer are
    **exclusive single slots** — setting a new one replaces the prior, and passing
    `None` clears it and restores pipy's built-in chrome. Widgets are bounded
    (line/row caps), keyed clears drop a single widget, and all regions are
    disposed (`component.dispose()` if present) on replace, clear, `/reload`, and
    shutdown. `set_title` writes an OSC 0 title only on a real TTY (a no-op for
    captured/non-TTY streams) and restores the saved title on clear. Pipy pins
    the custom header just above the input/working region rather than at the
    literal screen top (pipy paints a single live frame, not a full-screen
    alternate buffer, so it cannot own the host terminal's top row); aside from
    placement it is Pi's `setHeader`. `session_start` chrome renders live in an
    interactive TTY because lifecycle hooks now receive the live UI driver.
    `/reload` stages a fresh retained-chrome/listener sink while activation and
    flag validation remain fallible. Rejection closes that sink without paint,
    preserves the prior live chrome, and does not re-fire the retained
    generation's `session_start`. R3c3 installs candidate routing and invokes the
    replacement's `session_start` exactly once with `reason="reload"` against the
    detached candidate before semantic acceptance. A later refusal may retain
    immediate non-staged, non-chrome effects such as `ctx.notify()`, but closes
    the candidate sink without paint and suppresses its staged messages. On
    acceptance, candidate chrome reconciles only after publication, so removal
    clears old chrome only after acceptance. Chrome that hook sets is staged
    rather than painted early, then renders through the accepted sink.
    Registrations omitted
    by the replacement hook are cleared; custom-editor text is preserved in the
    built-in editor.
    The working indicator **does** animate: custom `frames` cycle through pipy's
    existing spinner loop at `interval_ms` (default interval otherwise). The live
    UI driver is now threaded into non-lifecycle event hooks too
    (`tool_call`/`tool_result`/`input`/`user_bash`/`before_*` and session gates),
    so Pi-shaped chrome/editor calls from those contexts paint immediately in
    the product TUI while headless contexts remain deterministic no-ops. Gate
    `scripts/parity_checks/extension_chrome_widgets_conformance.py --json` proves
    the chrome units; the golden
    `scripts/parity_checks/extension_conformance_gate.py --json` proves the
    end-to-end `session_start` dispatch and the no-leak guarantee.
19. Rich message renderers — **landed** (rich-UI item C): a
    `register_message_renderer` renderer may now return a themed
    `ToolRenderComponent` (use `lines_component(...)`). Back-compat is **by
    arity**: a renderer that **requires** a second positional parameter
    (`def render(data, ctx)` — a second positional parameter WITHOUT a default)
    receives a read-only `MessageRenderContext` (`custom_type`, `data`,
    `expanded`, `width`, `theme`) and may return a component; a 1-arg
    `renderer(data)` — including the capture-default idiom
    `renderer(data, prefix=captured)`, which still counts as 1-arg — keeps the
    exact slice-16 plain-text behavior. **The context parameter must be
    REQUIRED**: write `def render(data, ctx)`, *not* `def render(data, ctx=None)`
    — a defaulted second parameter is treated as the 1-arg plain path and the
    renderer never receives a context. When a component is returned its
    `render(width)` lines may carry `ctx.theme` SGR (a bounded `ToolRenderTheme`,
    plain text when color is disabled / `NO_COLOR` / captured) and are committed
    SGR-preserving under the dedicated `custom_message_custom` TUI line-kind with
    **no forced `[custom_type]` label** (the component owns its box, matching
    Pi's custom-message component replacing the default box). Rendering is
    a retained live snapshot in the product TUI: pipy re-renders the existing
    custom-message block in place when Ctrl+O / `ctx.ui.set_tools_expanded(...)`
    changes `ctx.expanded`, without appending a duplicate or mutating the
    session file. Width-change invalidation remains deferred. Rendering is
    length-bounded and **fail-soft**: a
    renderer (or its `render()`) that raises, returns a `str`/`Sequence[str]`, or
    returns an uncoercible value falls back to the slice-16 plain, sanitized
    path; a raising renderer surfaces only a bounded `render error:` diagnostic
    (no message body, data, or exception text leaked). The rendered body is
    **live-only** — it is never archived; only the JSON-safe entry `data` lives
    in the native product session tree. Active-branch custom entries replay into
    startup-opened TUI sessions and are redrawn after successful in-session
    `/resume` switches through the same renderer dispatch; if the renderer is
    absent or fails, pipy falls back to the plain sanitized path, and replay or
    redraw never mutates the session file. `ctx.send_message` / persisted
    `CustomMessageEntry` values with `display=True` also render through a
    registered renderer using a Pi-shaped payload (`customType`, `content`,
    `display`, `details`) and fall back to stored content when no renderer is
    registered. Deferred: live per-resize `invalidate`/re-render beyond expanded-flag refresh. Pi's
    `MessageRenderer` returns one `Component | undefined` per custom message,
    so pipy intentionally keeps one component per entry and does not add a
    separate multi-widget message collection API. Gate
    `scripts/parity_checks/extension_message_renderer_conformance.py --json`
    proves the dispatch/coercion units; the golden
    `scripts/parity_checks/extension_conformance_gate.py --json` records the
    `message_renderer_component` marker proving the rich renderer ran end to end
    with no leak.
20. Extension OAuth `/login` wiring — **landed for provider auth storage**:
    `ExtensionOAuthConfig` providers are projected under the derived provider id
    (the registered provider name), `/login <provider>` invokes the extension's
    sync `login(callbacks)` with Pi-shaped stdio callbacks, and pipy stores the
    returned mapping as `{"type":"oauth", ...credentials}` in `AuthStore` under
    that provider name. `/logout <provider>` removes it. OAuth-backed extension
    providers are `login-required` until stored credentials exist; non-OAuth
    extension providers retain the previous factory-owned availability. Broader
    declarative provider config and credential injection into provider factories
    remain deferred. Gate: `scripts/parity_checks/extension_providers_conformance.py --json`.
21. Extension UI editor helpers — **landed for command/shortcut contexts**:
    `ctx.ui.editor(title, prefill=None)` opens a focused multi-line product-TUI
    overlay in interactive sessions and returns `None` deterministically in
    headless/non-interactive contexts. The editor submits on Enter, inserts a
    newline on Shift+Enter where the terminal reports it and on Alt+Enter as
    pipy's portable fallback, supports basic cursor movement/backspace, cancels
    on Esc/Ctrl-C, and opens `$VISUAL`/`$EDITOR` on Ctrl+G when configured. The
    external editor receives a private temp markdown file, the TUI restores
    normal terminal mode while the editor owns stdio, successful exits replace
    the in-frame buffer, and failed exits keep the prior text. The returned text
    is live command-handler data; it is not written to the metadata-first
    archive by default. The same command/shortcut contexts now also expose
    Pi-canonical camelCase `ctx.ui.getEditorText()`,
    `ctx.ui.setEditorText(text)`, and `ctx.ui.pasteToEditor(text)` for the core
    prompt editor, with snake_case Python convenience aliases retained. Live paste
    inserts literal text at the current cursor while preserving surrounding
    draft text and pasted newlines, matching Pi's bracketed-paste path. These
    helpers are live-only mutations: headless reads return `""`, headless
    writes/pastes no-op, and live driver failures fail soft. The Pi-shaped
    custom editor component live integration also ships:
    `ctx.ui.set_editor_component(factory)` / `setEditorComponent(factory)` and
    `ctx.ui.get_editor_component()` / `getEditorComponent()` store and return a
    live factory, and `None` clears it. Headless contexts no-op and return
    `None`, matching Pi RPC. The live product TUI now calls the factory, routes
    keys, wires submit/change callbacks, forwards autocomplete, passes a
    bounded keybinding/action adapter, wires delegated app-action handlers
    including `app.editor.external` through the same `$VISUAL`/`$EDITOR` helper
    as `ctx.ui.editor(...)`, plus special Escape/Ctrl-D/paste-image callbacks,
    and preserves text when clearing. Ctrl-C remains outside the delegated handler map. Broader Pi
    component-library parity remains deferred.
22. Extension UI theme controls — **landed for command/shortcut contexts**
    (rich-UI item E): `ctx.ui.theme` (current active `ChromePalette`),
    `ctx.ui.get_all_themes()` (`{"name", "path": None}` per available theme,
    default first), `ctx.ui.get_theme(name)` (palette by name, `None` when
    unknown, without switching), and `ctx.ui.set_theme(name_or_palette)`
    (`{"success", "error"}`), mirroring Pi's `theme`/`getAllThemes`/`getTheme`/
    `setTheme`. The reads are *ambient* (the global package theme registry plus
    `PIPY_THEME`/the chrome store), so they are deterministic even headless;
    `set_theme` requires a live UI and returns
    `{"success": False, "error": "UI not available"}` headless without mutating
    process state, while a live call reuses the `/settings` `select_theme`
    mechanism so the next frame repaints. `path` is always `None`: the session
    theme registry retains only `name -> palette` and pipy does not leak package
    theme file paths to extension code.
23. Tool-output expansion controls — **landed for command/shortcut contexts**:
    `ctx.ui.get_tools_expanded()` / `getToolsExpanded()` read the same live
    product-TUI expansion bit that drives built-in tool-row expansion, and
    `ctx.ui.set_tools_expanded(expanded)` / `setToolsExpanded(expanded)` set it
    (coerced to bool) and request a repaint. Headless/no-UI contexts match Pi
    RPC/no-op behavior: getters return `False` and setters do nothing. The state
    is live-only and never archived.
24. Hidden-thinking label control — **landed for live product-TUI command/
    shortcut contexts**: `ctx.ui.set_hidden_thinking_label(label=None)` and
    Pi-shaped `ctx.ui.setHiddenThinkingLabel(label=None)` set the label shown
    when thinking blocks are folded. Omitting/passing `None` restores Pi's
    default `Thinking...`; headless contexts no-op like Pi RPC; the label is
    live-only and never archived.
25. Raw terminal input subscriptions — **landed for live product-TUI command/
    shortcut contexts**: `ctx.ui.on_terminal_input(handler)` and Pi-shaped
    `ctx.ui.onTerminalInput(handler)` register decoded-key listeners that run
    before built-in editor handling. Handlers may consume the key or replace it
    for later listeners/built-ins with Pi's `{consume, data}` result shape, bad
    handlers fail soft, and the returned disposer is idempotent. Headless
    contexts return a no-op disposer and never invoke the handler; raw inputs
    are live-only and never archived. Reload candidates now retain the exact
    handler/disposer identity in their guarded sink; rejection never registers
    it with the TUI, and a late registration after sink close returns an inert
    disposer.
26. Autocomplete provider wrappers — **landed for live product-TUI command/
    shortcut contexts**: `ctx.ui.add_autocomplete_provider(factory)` and the
    Pi-shaped `ctx.ui.addAutocompleteProvider(factory)` stack wrappers around
    the built-in editor provider. Providers receive full editor `lines`,
    `cursor_line`/`cursor_col`, and `{force, signal}` context; may implement
    snake_case or camelCase `get_suggestions`/`getSuggestions`,
    `apply_completion`/`applyCompletion`, and optional
    `should_trigger_file_completion`/`shouldTriggerFileCompletion`; and fail
    soft back to the built-in provider. Headless contexts accept the call as a
    deterministic no-op.
    The bounded overlay handle now exposes Pi-shaped `hide`, `setHidden`,
    `isHidden`, `focus`, `unfocus`, and `isFocused`; hidden overlays skip
    render/input and unfocused visible overlays keep rendering but skip input.
    Deferred: broader custom editor component-library parity, a full custom
    overlay stack/component library, target-aware overlay focus graph, and live
    per-frame component invalidation.
27. Project-trust extension surfaces — **landed**: pre-trust global and explicit
    CLI extensions receive the serial `project_trust` event before saved/default/
    UI fallback; `undecided` continues, the first `yes`/`no` owns the decision,
    handler errors warn and continue, and only exact `remember=True` persists
    the exact cwd. Headless selection/input calls are inert and notifications
    are stderr-only. The pre-trust instances are reused for provider catalog and
    live-session construction, while project/project-package code remains gated.
    Normal command, shortcut, lifecycle, input, tool, provider-request, and
    session-gate contexts expose zero-argument `is_project_trusted()` and
    `isProjectTrusted()` reads of the run-local state.
28. Provider-header hooks — **landed**: `before_provider_headers` handlers
    receive `BeforeProviderHeadersEvent(headers,
    type="before_provider_headers")` plus the normal mode-aware/session-manager
    context. They run serially in extension load order against one mutable
    `str | None` map; return values are ignored, handler failures do not stop
    later handlers, and `None` removes a header. Every pipy-owned real HTTP
    adapter dispatches at its owned seam; Bedrock signs the mutated base
    headers, Codex reuses one snapshot across retries/fallback, and ds4
    dispatches only in its delegated Chat Completions adapter. Codex handlers
    see request-scoped additional headers before provider-owned auth/transport
    fields are derived, so those later fields cannot be inspected or deleted.
    The golden
    `extension_conformance_gate.py --json` proves the live mutation and
    archive/protocol no-leak boundary.
29. Durable entry renderers — **landed**: `api.register_entry_renderer(type,
    renderer)` independently owns product-TUI rendering for `ctx.append_entry`
    records. The renderer receives a detached full stored entry (`type`, `id`,
    `parentId`, `timestamp`, `customType`, `data`) plus an
    `EntryRenderContext` (`expanded`, `width`, `theme`) and returns a bounded
    component such as `lines_component(...)`. Missing renderers, `None`, bad
    outputs, and failures omit the live row without affecting persistence.
    New appends, startup replay, `/resume`, expanded-state refresh, and `/reload`
    use the same active entry registry; message renderers remain independent,
    even for the same custom type. Entry renderers are never invoked in print,
    JSON, or RPC modes, and their rendered lines never enter session JSONL,
    provider context, protocol stdout, or the summary-safe archive. Gate:
    `scripts/parity_checks/extension_entry_renderer_conformance.py --json` plus
    the captured-mode proof in `extension_dispatch_conformance.py --json`.
30. Anthropic cache-friendly dynamic tool loading — **landed**: a successful
    extension tool that only adds registered active tools records the ordered
    names on its durable provider-agnostic `AgentToolResultMessage`. Removal,
    replacement, and handler failure retain the updated/fallback behavior but
    record no native-deferred marker. The Anthropic adapter resolves explicit
    `compat.supportsToolReferences` first, otherwise enabling only first-party
    non-Haiku Claude 4.5+ models; supported requests mark late definitions with
    `defer_loading: true` and replace the marked result content with deduplicated
    `tool_reference` blocks while preserving ordinary output as sibling text.
    Unsupported models and every other provider continue sending the full
    current active set. Native session JSONL preserves the summary-safe tool
    names for resume/provider switching. Gate:
    `scripts/parity_checks/extension_live_session_conformance.py --json` plus
    focused Anthropic construction/request tests. **Superseded by SYS1b**
    (Pi `9e05370b2` removed the markers): the next turn's transcript system
    message declares the added tools, and models with
    `supportsMidConvoToolChanges` load them with `tool_addition` blocks
    ([provider-catalog.md](provider-catalog.md#mid-conversation-system-messages)).
31. OpenAI Responses dynamic tool search — **superseded by SYS1b**: tool
    search now loads tools a later system message adds, on models with
    `supportsMidConvoSystemMessages` (and `additional_tools` first when the
    model supports it). The original landing: explicit Boolean
    `compat.supportsToolSearch` opts selected Responses models in (default
    false). Their top-level `tools` keeps only immediate definitions; each
    marked `function_call_output` is followed by a deterministic completed
    client `tool_search_call`/`tool_search_output` pair carrying the late
    definitions with `strict: false` and `defer_loading: true`. OpenAI Codex
    uses the full `call_id|item_id` correlation to derive the stable search id,
    and retries/fallback reuse the same prebuilt body. Kimi deferred tools
    remain an independent provider-owned slice.

## Open Questions

- What is the right packaging format for shareable Python extensions:
  local-path manifests, Python entry points, git refs, PyPI packages, or a
  pipy-specific package index?
- How should extension state be stored without duplicating the native product
  session tree or leaking prompts into the metadata-only workflow
  archive/catalog?
- Which UI hooks are valuable enough to expose before the full TUI is more
  stable?
- Should discovered extensions support in-session reload, and if so how should
  reload interact with fail-closed import errors, registered hooks, live tools,
  and already-running provider turns?

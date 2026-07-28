# Pipy Architecture

Status: living overview of the current native coding-agent product.

Pipy is a Python coding-agent application. Its primary path is the native
interactive product (`pipy` / `pipy repl`), not a wrapper around another agent
CLI. `pipy_harness` owns the agent runtime, providers, tools, private product
sessions, automation modes, extensions, and terminal UI. `pipy_session` is a
separate metadata-only workflow archive and catalog.

The Phase 0–7 [Architecture Migration](architecture-migration.md) is completed
historical evidence. Current structural work is ordered by the reviewed
[Architecture Quality Improvement Program](specs/2026-07-24-architecture-quality-improvement-plan.md)
and indexed in the [Backlog](backlog.md).

## Runtime structure

```mermaid
flowchart TB
  Entrypoints[Product CLI / inline TUI / JSON / RPC] --> Composition[Native product composition root]
  CompatEntrypoints[pipy run / narrow Python SDK] --> Compat[Harness compatibility runtime]
  Composition --> Coding[Headless coding-session layer]
  Coding --> Agent[Canonical UI-free agent loop]
  Agent --> Tools[Tool capability and executor ports]
  Agent --> ProviderExecutor[Canonical provider-turn executor]
  Compat --> ProviderExecutor
  ProviderExecutor --> ProviderPort[Injected provider port]
  Composition --> Runtime[Catalog-backed model runtime]
  Runtime --> Providers[Provider-family adapters]
  Providers -. implement .-> ProviderPort
  Providers --> HTTP[Shared HTTP / cancellation boundary]
  Composition --> Extensions[Extension generation, hooks, and host ports]
  Composition --> ProductTree[Private native product session tree]
  Agent --> Events[Canonical synchronous agent events]
  Events --> UI[Pure UI reducer and render adapter]
  Events --> Automation[JSON / RPC / SDK projections]
  Events --> ProductTree
  Events --> Workflow[Metadata-only workflow projection]
  Compat --> Workflow
  UI --> TUI[Inline terminal UI facade]
  TUI --> Editor[Terminal-independent editor state]
  TUI --> Driver[Terminal driver]

  classDef core fill:#eef2ff,stroke:#1d4ed8,color:#111111;
  classDef adapter fill:#fff7ed,stroke:#c2410c,color:#111111;
  classDef store fill:#ecfdf5,stroke:#047857,color:#111111;
  class Agent,Coding,Composition,Runtime,Events,ProviderExecutor,Editor core;
  class Entrypoints,CompatEntrypoints,Compat,Providers,HTTP,Extensions,UI,TUI,Driver,Automation adapter;
  class ProductTree,Workflow store;
```

The core is synchronous. Provider and tool work may use owned workers so a TTY
or RPC caller can cancel or inject input, but no asyncio loop owns the product
lifecycle. Canonical events use synchronous push semantics: each mode's fixed
composite accepts its ordered projections before the producer advances.

## Canonical agent layer

`src/pipy_harness/native/agent/` is the reusable, provider-neutral core. It
owns immutable full-content messages and events, active-input identity,
request-local tool authorization, usage accounting, history reduction, one
provider-turn execution, one tool-call execution, and the single accepted-run
`AgentLoop`. The loop runs against fake provider/tool/event ports without a
terminal, product session, extension implementation, concrete transport, or
workflow archive.

Product adapters in `native/agent_request.py`, `agent_loop_policy.py`,
`agent_runtime.py`, `agent_adapters.py`, and `tool_capabilities.py` bind that
core to catalog selections, extension policy, current product state, concrete
tools, rendering, and persistence. These adapters are composition seams; the
canonical package does not import them back.

## Headless coding-session layer

`src/pipy_harness/native/coding/` owns product policy above a single agent run:

- `input_queue.py` owns command precedence and steering/follow-up/trigger
  ordering;
- `state.py` owns the active provider/model labels, canonical history, usage,
  counters, compaction state, and provider-failure state;
- `accepted_input.py` prepares one accepted prompt and request-only context;
- `agent_run.py` assembles and invokes the canonical loop;
- `commands.py` and `command_registry.py` own the closed command vocabulary,
  classification, metadata, and dispatch outcomes;
- `product_session.py` coordinates state-first private-session transitions; and
- `session_controller.py` owns input selection, command/resource/extension
  precedence, true-idle settlement, and the outer session lifecycle.

This package is headless: terminal rendering, extension implementation,
provider construction, concrete tools, filesystem persistence, automation, and
the metadata archive are injected or remain outside it.

## Product composition and one-shot runtime

`native/tool_loop_session.py` is the interactive product composition root. It
constructs settings and trust state, catalog-backed providers, tools,
extensions, the private session tree, event projections, automation or terminal
input, and the headless coding/agent collaborators. Dynamic command effects and
cross-boundary orchestration remain here. The session-owned built-in effects
(status, compact, name, new, tree, resume, fork, and clone) execute through one
frozen `_SessionCommandEffects` bundle composed from narrow run-scope ports;
provider/configuration built-ins (hotkeys, changelog, copy, settings, trust,
model and scoped-model selection, login, and logout) execute through a separate
frozen `_ProviderConfigurationCommandEffects` bundle. That executor composes
with `_ProviderMutationEffects`, which remains the single owner for live model
and authentication mutation ordering, while preserving live-terminal versus
captured-stream presentation. Native session export, import, and share execute
through `_TransferCommandEffects`; import replaces the live tree and then uses
the session collaborator's authoritative history/input rebuild seam. `/reload`
executes through `_ReloadCommandEffects`, whose explicit phases preserve the
publication gate around configuration/package/resource recomposition,
extension generation activation/publication, provider refresh, tool-capability
publication, and terminal refresh. Provider fallback and unavailable binding
remain methods on `_ProviderMutationEffects`, rather than a second reload-only
mutation path. `_BuiltinCommandInterpreter` now contains only the closed
four-family routing plus the closed footer policy.

`native/session.py` owns the explicitly named
`NativeHarnessCompatibilityRuntime` used only by `pipy run` and the narrow
Python harness SDK. Slice 10 renamed the former `NativeAgentSession` rather than
leaving two runtimes that appeared semantically equivalent. For every turn, the
compatibility runtime invokes the canonical
`native.agent.provider_turn.ProviderTurnExecutor`, whose delta-admission gate
and typed outcome contract surround the injected provider call. A thin private
adapter makes the final `ProviderPort.complete(...)` call while preserving the
compatibility surface's text-only initial stream and buffered follow-up shape.
The runtime derives one required `stream_text_deltas` capability beside the SDK
event projection and explicitly disables it for both follow-up paths; provider
deltas flow only through the canonical agent-event sink. The adapter is not a
second provider-execution pipeline.

The Slice 10 convergence decision is **intentional separation**, based on the
executable contracts in `tests/test_native_one_shot_runtime_contract.py` and the
terminal-lifecycle characterization in `tests/test_native_session.py`:

- independently observable providers run through real executor calls on both
  sides for an ordinary successful completion, with equivalent emitted text
  deltas, final text, and all six normalized usage counters;
- provider-metadata fixture intents are not canonical `ProviderToolCall`
  values: the compatibility runtime may run one bounded no-op or approved file
  excerpt, synthesize a metadata-only observation, make at most one special
  follow-up provider request, and optionally consume separately injected,
  human-reviewed patch/verification requests;
- the canonical `AgentLoop` instead authorizes advertised provider tool calls,
  maintains full canonical history, and continues provider/tool iterations
  under its tool policy; it correctly ignores compatibility metadata fixtures;
  and
- the compatibility runtime projects `ProviderRequest` construction/validation
  failures and exceptions raised by the injected provider into its established
  failed result, including genuine provider `ValueError` and `TypeError`
  exceptions. `ProviderTurnDeltaPolicy` construction, executor collaborator
  validation, and compatibility-adapter channel violations are programming
  invariants that instead escape loudly. It maps the
  executor's typed provider-cancellation outcome to the same failure/archive
  shape without exposing provider detail, emits
  `native.session/provider/tool/...` metadata lifecycle events, and excludes
  prompt/model/tool content from the workflow archive. The canonical product
  loop propagates through its product status policy and full-content
  event/session projections.

Routing the compatibility contract through `AgentLoop` would therefore change
provider request history, tool authorization, event order, failure handling,
and archive behavior rather than merely remove duplication. Its fixture-shaped
`native.tool.ToolPort`, patch apply, and verification boundaries do not match
canonical model-driven tool execution and are deliberately not adapted into
it. Product one-shot `--mode json` and `--print` already use
`PipyNativeToolReplAdapter` and the canonical coding/agent loop; they are not
served by this compatibility runtime.

`pipy_harness.runner.HarnessRunner` and the adapter ports continue to support
conservative subprocess lifecycle capture. Subprocess wrapping is a reference
and capture facility, not the product runtime direction.

## Providers and HTTP ownership

`native.repl_state.ModelRuntime` composes `catalog_state.py` with
`provider_construction.py`. It is the product owner for resolving a provider /
model specification, determining availability and thinking levels, and
constructing the selected provider. Built-in rows, `models.json`, stored auth,
and extension-registered providers join at the catalog/construction boundary;
provider selection code does not bypass it with a second factory.

Concrete HTTP adapters live by protocol family under `native/providers/`, with
shared wire translators for OpenAI Responses, Chat Completions, Anthropic
Messages, and Gemini `generateContent`. The specialized Codex streaming adapter
remains `native/openai_codex_provider.py`. `native/http.py` owns common JSON and
SSE execution, timeout/error normalization, cancellation, and retryable
transport classification. Provider adapters return normalized results and never
write sessions or import UI code.

## Extensions

Extension ownership is split deliberately:

- `extensions.py` discovers and inventories loadable local resources;
- `extension_loader.py` owns isolated module loading and awaitable driving;
- `extension_types.py` owns the stable typed API, contribution, hook, host-port,
  and UI value vocabulary;
- `extension_runtime.py` activates one validated batch and owns commands,
  shortcuts, tools, providers, flags, renderers, queues, and host contexts;
- `extension_hooks.py` owns serial lifecycle, input, request, tool, trust, bash,
  and session-gate dispatch; and
- `extension_ui.py` owns the deterministic headless UI bridge, while
  `tui.py` owns the concrete live extension UI driver and chrome rendering.

The stable `pipy_harness.extensions` module is a façade, not another behavior
owner. Its exact 97-name `__all__` inventory imports each value directly from
the module above that owns it (plus the provider construction/header seams),
so its public contract does not depend on whatever `extension_runtime.py`
happens to import for its own implementation. `extension_runtime.py` keeps the
already-characterized direct-import compatibility identities explicit for
internal consumers, including the headless UI bridge and render helpers; those
aliases still denote the authoritative owner objects.

Activation fails closed per extension. Trusted extension code may perform its
own external effects, but pipy-owned registries do not publish a partial
activation.

The live extension state is one generation reached through
`native/session_generation.py`. `SessionGenerationRef` owns the generation
pointer, its identity, and the session's single mutex; `_RunControlState`
reaches the generation only through that reference. `/reload` parses the
candidate's flags before anything becomes live, so a malformed flag rejects the
whole candidate and the previous generation stays complete — its commands,
hooks, tools, providers, renderers and flag values together — instead of the
new runtime being paired with stale flags. Only the extension generation is
rejected: settings, keybindings, package roots, and workspace resources that
reloaded successfully stay applied, and the rest of the reload runs against the
unchanged generation.

While a reload republishes its derived projections the reference opens a
publication gate, and the extension mutation ports refuse changes for that
window. `set_active_tools` and `set_thinking_level` take the session mutex
across both the refusal check and their assignment; `set_model` still persists
part-way through its mutation, so for that one port the gate narrows the window
rather than closing it. Model defaults are queued during a selection and written
only after the selection is live, and a persistence failure is reported without
claiming the selection reverted. The reload effect owner closes the gate before
firing the replacement generation's `session_start` hook, then emits the final
reload diagnostic; the root footer policy runs only after that complete effect
returns.

## Sessions, automation, and trust domains

Two stores serve different purposes and must not be conflated:

1. `native/session_tree.py` is the private native product session source of
   truth. Its append-only JSONL tree intentionally stores full conversation,
   assistant, tool, custom-message, compaction, and branch content for resume,
   fork, clone, import, and export.
2. `pipy_session` is a separate metadata-only workflow archive. Its JSONL,
   Markdown summaries, and list/search/inspect/verify surfaces allowlist safe
   lifecycle metadata, counters, hashes, bounded labels, and summary-safe
   learning events. It excludes prompts, provider text, raw tool or command
   output, file content, diffs, paths, raw exception text, and credentials by
   default.

`--mode json`, `--mode rpc`, `--print`, and the Python SDK are explicit
full-content product transports, not workflow-archive channels.
`native/automation/` owns Pi-shaped event dictionaries, deterministic JSONL,
one-shot JSON/print drivers, and the long-lived RPC server. RPC additionally
owns command correlation, queued-input reservation/settlement, true-idle
notification, and its direct bash boundary.

Project trust is fail-closed. Final-workspace project settings, packages,
resources, and executable extensions are unavailable until saved or run-local
trust is resolved; global and explicit CLI sources follow their documented
separate policy. Workspace tools enforce containment after symlink resolution
and default-deny `.git` and ignored/generated paths where specified. Provider
auth is resolved at the catalog/construction boundary, secrets stay out of
session/archive diagnostics, and extension activation cannot silently widen
provider or tool authority.

## Terminal boundary

`native/ui/state.py` is a pure reducer from canonical agent events to render
decisions, and `native/ui/rendering.py` drives a renderer port.
`native/tui.py` owns the product's stateful inline-scrollback façade: selectors
and overlays, extension chrome, live frame composition, TUI-facing event
rendering, and translation of terminal/filesystem/clipboard/extension effects.
Finalized blocks are committed to the normal terminal buffer; pipy does not use
the alternate screen.

`native/editor_state.py` is the single typed owner for the editable buffer and
cursor, slash/completion selection and anchor state, prompt recall navigation,
undo/redo snapshots, bracketed-paste hand-off, initial-text rehydration, and the
terminal steering/follow-up/local-command queue. Its transitions are synchronous
and terminal-independent. It imports only the standard library and is unit-tested
without streams, file descriptors, termios, PTYs, or a `ToolLoopTerminalUi`.
`ToolLoopTerminalUi` retains narrow properties and methods for callers and
characterized test access, but they project directly to that owner rather than
storing a mirrored copy; its existing slots reject retired names instead of
silently accepting dead writes. Queue entries carry typed content/kind pairs,
while the frame adapter alone maps those kinds to rendering labels. Completion
lookup, extension-provider execution, clipboard/filesystem I/O, custom editor
execution, painting, and terminal locks remain in the façade/adapters and
translate their results into editor-state transitions. No-op editing and popup-
navigation transitions skip effectful refreshes/painting; path completion yields
to an open slash menu before lookup. Completion acceptance uses one immutable
owner snapshot and one span invariant across trusted extension callbacks, with
only the typed `at`/`path` mode domain. `EditorState` alone owns queue-
restoration draft precedence; the façade injects a lazy custom-editor text
callback that is skipped for an empty queue or staged initial text. Retired
copy-returning lane projections are rejected by slots rather than retained as
misleading compatibility surfaces.

`native/terminal_driver.py` owns terminal writes/flushes, raw-mode and bracketed
paste lifecycle, title restoration, decoded input bytes, SIGWINCH handling, and
live terminal geometry. Decoded paste bodies transfer once into `EditorState`;
the TUI decides what to draw and which pure transition to apply, while the driver
decides how bytes and terminal lifecycle transitions occur. Real-PTY tests
protect prompt readiness and restoration.

## Executable architecture gates

`tests/test_architecture_import_boundaries.py` statically rejects forbidden
imports without importing product entrypoints. It activates package- and
module-specific rules for the canonical agent, coding, UI, providers, HTTP,
extensions, terminal driver, persistence, automation, and composition layers;
focused fresh-process and exact-import tests strengthen important leaves.
Golden architecture contracts separately pin cross-mode event order and the
full-content product-session versus metadata-archive privacy split.

The complete Mypy strict-equivalent source frontier is the single override in
`pyproject.toml`, with exactly two structured package patterns:
`pipy_harness.*` and `pipy_session.*`. Those patterns cover both package
`__init__` modules and every descendant source module, including future source
modules, while top-level test modules retain the repository's existing
non-strict baseline. The override spells out every per-module sub-flag from
`--strict`; the three global-only flags (`warn_unused_configs`,
`warn_redundant_casts`, and `strict_bytes`) remain enabled globally. The
authoritative CI typecheck still runs `mypy src tests`, and the independent
repository-strict diagnostic `mypy --strict src` is clean across all 166 source
files.

`pipy_harness.status.HarnessStatus` is the sole enum definition and the
dependency-neutral owner. `pipy_harness.models` explicitly imports it through a
same-name alias, so Mypy recognizes the established
`pipy_harness.models.HarnessStatus` path as an intentional export under
`no_implicit_reexport`. The models, top-level package, SDK, native result
objects, and provider annotations all continue to expose that exact enum object;
members, serialized values, `AdapterResult`, and `RunResult` are unchanged.
Only the required `pipy_harness.models` public path gains that explicit
same-object export. Other combined source/test-graph findings are resolved at
their consumers: tests import the authoritative status and verification-request
owners and patch `pathlib` or stdlib terminal modules directly, so production
modules do not acquire test-only typed exports.

The package-wide gate preserves the owner and narrowing decisions established
while the frontier was enumerated. `PackageResourceRoots` remains defined by
`package_resources`, with `package_runtime` exposing only an explicit
same-object export. Package JSON/TOML and `models.json` still decode through
`object` and narrow with `Mapping`, `Sequence`, and scalar checks before use.
The optional prompt-toolkit key-binding class, decorators, events, and buffers
remain described by local protocols without importing or requiring that
package. Autocomplete provider dispatch remains intentionally duck-typed
behind an `object` boundary, while extension chrome and tool rendering reuse
the authoritative runtime-checkable contracts from `extension_types`.

Provider catalog construction continues to use the authoritative
`ProviderConfig`, `AuthStatus`, request, result, stream/reasoning, and
cancellation types. Dynamically decoded tool integers still reject `bool`
before `int` narrowing, and edit-diff's atomic writer continues to accept the
already-resolved `Path`. Export/distribution keeps its private generic-keyed
mapping redaction path; the public `redact_export_value(Any) -> Any` boundary is
intentionally dynamic. These are enduring ownership and validation contracts,
not exceptions to the complete strict source frontier.

Completing source strictness changes no runtime boundary. CLI, JSON/RPC, SDK,
provider, tool, session, extension, and archive schemas remain unchanged, as do
the one-way import rules and fail-closed trust gates. Credentials, tokens, and
secrets stay excluded; the private native product session remains the
full-content source of truth, and `pipy_session` remains a separate
metadata-only workflow archive.

Ruff C901 is a directional repository gate. Previously complex files are
explicitly pinned and no new pin may be added; a finding in a previously clean
file fails `just lint`. At the Slice 1 baseline, unignored Ruff reports 39
repository findings, 23 under `src`, while `src` contains one justified
`type: ignore`. Run the reproducible source-only inventory with:

```sh
uv run python scripts/architecture_metrics.py --json
```

## Intentionally remaining risks

The active program addresses ownership risks rather than cosmetic size:

- extension reload publishes one generation and rejects a candidate whole, but
  the candidate is not yet staged behind an isolated activation host. A
  candidate activates against the live host, so extension chrome must still be
  cleared before activation rather than after commit — which means a rejected
  candidate leaves the retained generation's chrome cleared — and a timed-out
  activation worker is not yet sealed out of its runtime;
- `set_model` persists a default part-way through its mutation, so its
  publication-gate admission is not yet atomic;
- `_ReplLoopStep.step_once` remains a high-complexity cross-boundary
  orchestrator with a wide collaborator list;
- the harness/SDK one-shot compatibility runtime remains a deliberately
  separate metadata-fixture contract, but its provider execution is canonical;
  executable Slice 10 boundary tests prevent it from drifting into a second
  silently equivalent agent loop;
- `ToolLoopTerminalUi` still combines overlay, extension chrome, and frame
  state, but editor ownership has moved behind `EditorState`; Slice 11 reduced
  the measured façade inventory from **128 to 104 fields**, exactly the slice
  ceiling; and
- load-sensitive PTY readiness races and the absence of a repository Ruff-format
  gate remain explicit quality work.

See the active improvement plan for ordered acceptance criteria. These are
intentional, measured residuals—not evidence that the completed migration is
still awaiting its original phases.

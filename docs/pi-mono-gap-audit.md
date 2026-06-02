# Pi-Mono Gap Audit

Status: comparison snapshot and implementation specification, 2026-06-02.

This audit compares pipy's current docs/specs and command help with the local Pi
reference in `/Users/jochen/src/pi-mono` plus the installed `pi 0.78.0` help.
It is a slice-selection aid: the detailed behavioral specs remain the source of
truth for each topic, but this page records the biggest remaining gaps in one
place and states what pipy should implement next.

## Sources checked

Pipy docs/specs:

- `docs/parity-plan.md`, `docs/backlog.md`, `docs/pi-parity.md`,
  `docs/parity-criterion.md`
- `docs/provider-catalog.md`, `docs/settings-config.md`,
  `docs/tui-workflow.md`, `docs/automation-rpc.md`,
  `docs/export-distribution.md`, `docs/extension-api.md`,
  `docs/session-tree.md`, `docs/user-documentation.md`
- current `uv run pipy --help`, `uv run pipy run --help`,
  `uv run pipy repl --help`
- current helper gate: `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`

Pi reference:

- `packages/coding-agent/src/core/slash-commands.ts`
- `packages/coding-agent/src/cli/args.ts`
- `packages/coding-agent/src/core/model-registry.ts`,
  `model-resolver.ts`, `auth-storage.ts`, `settings-manager.ts`,
  `keybindings.ts`, `resource-loader.ts`, `package-manager.ts`
- `packages/coding-agent/src/package-manager-cli.ts`
- `packages/coding-agent/src/modes/interactive/interactive-mode.ts`
- `packages/coding-agent/src/modes/print-mode.ts`,
  `modes/rpc/rpc-types.ts`, `modes/rpc/rpc-mode.ts`
- `packages/coding-agent/src/core/export-html/*`
- `packages/coding-agent/src/core/extensions/*`
- `packages/ai/src/models*.ts`, `oauth.ts`, `env-api-keys.ts`, and provider
  implementations
- installed `pi --help`

## Ranked biggest gaps

### 1. Provider/model catalog product wiring

**Why it is first:** this is the highest-leverage incomplete slice. The catalog,
matcher, `models.json`, auth helpers, OAuth registry, `--list-models`, and full
catalog `model_options()` foundation already exist and the helper-layer gate
passes. The remaining gap is that product provider turns still use legacy
provider construction.

Pi reference:

- `model-registry.ts` resolves the selected model, custom providers,
  request auth, headers, routing, and OAuth mutations before each request.
- `model-resolver.ts` owns exact/fuzzy/glob/`:thinking` matching for CLI,
  selectors, and scoped cycling.
- `args.ts` exposes `--provider`, `--model`, `--api-key`, `--thinking`,
  `--models`, and `--list-models` as one connected surface.

Pipy current state:

- `scripts/parity_checks/provider_catalog_conformance.py --json` passes helper
  checks but intentionally lacks product-path check 18.
- `uv run pipy run --help` says `--api-key`, `--thinking`, and `--models` are
  accepted but not fully applied.
- `docs/provider-catalog.md` is already the detailed owning spec.

Implement in pipy:

1. Add a catalog-backed provider-construction boundary that accepts the resolved
   `NativeModelSpec` plus resolved request config.
2. Make `models.json` custom OpenAI-compatible providers usable for actual
   `pipy run` and REPL product turns, not only listable.
3. Apply resolved `baseUrl`, merged headers, `authHeader`, routing blocks,
   runtime `--api-key`, stored/env/OAuth auth, and `models.json` key/env/
   `!command` fallback to outgoing adapter requests.
4. Extend `ProviderRequest` and the relevant adapters so active thinking levels
   from `--thinking` and `provider/model:level` are mapped through the model's
   `thinkingLevelMap` into provider-native request fields.
5. Route direct `/model <ref>` through the shared resolver so exact, bare-id,
   alias/fuzzy, colon-in-id, and invalid-level behavior match Pi.
6. Keep the legacy `--native-provider ds4` compatibility shim only as a bridge;
   the canonical ds4 path is the `models.json` custom-provider example.

Complete when:

```sh
uv run python scripts/parity_checks/provider_catalog_conformance.py --json
just check
```

The conformance script must include product-path check 18: fake HTTP captures
prove custom providers, auth, headers, routing, thinking, and direct `/model`
resolution reach real provider calls.

### 2. Settings, config, keybindings, and command realignment

**Why it is large:** Pi has a real user-editable configuration system; pipy has
only narrow local state files and the current interactive `/settings` overlay.
This gap also owns several missing Pi slash commands and flags.

Pi reference:

- `settings-manager.ts` loads global `~/.pi/agent/settings.json` plus project
  `.pi/settings.json`, migrates legacy keys, deep-merges, and writes modified
  fields under a lock.
- `keybindings.ts` defines 35+ context-scoped bindings and `keybindings.json`.
- `resource-loader.ts` supplies `SYSTEM.md`, `APPEND_SYSTEM.md`, skills,
  prompts, themes, and context-file toggles.
- `slash-commands.ts` includes `/hotkeys`, `/reload`, `/scoped-models`, and
  `/changelog`.
- `args.ts` exposes `--system-prompt`, repeated `--append-system-prompt`,
  `--tools`, `--no-tools`, `--no-builtin-tools`, `--exclude-tools`, resource
  load/disable flags, `--no-context-files`, `--verbose`, `--offline`, and
  `--version`.

Pipy current state:

- `/settings` is interactive but only controls the existing local choices.
- `--thinking`, `--models`, and `--api-key` are accepted ahead of full wiring.
- `--system-prompt`, `--append-system-prompt`, `--no-context-files`, Pi-style
  tool allow/deny flags, `/hotkeys`, `/reload`, and `/scoped-models` are not
  complete product surfaces.
- `docs/settings-config.md` is already the detailed owning spec.

Implement in pipy:

1. Add `pipy_harness.native.settings`: global config home
   `PIPY_CONFIG_HOME -> XDG_CONFIG_HOME/pipy -> ~/.config/pipy`, project
   `.pipy/settings.json`, Pi-style migration, one-level shallow deep-merge,
   parse-error isolation, field-scoped lock-guarded writes, unknown-key
   round-trip.
2. Migrate or mirror current `NativeDefaultsStore`, `NativeThemeStore`, and
   prompt-history enablement into settings while keeping backward compatibility.
3. Add `pipy_harness.native.keybindings`: Pi action names, default bindings,
   legacy-name migration, context-scoped lookup, and `/hotkeys` from resolved
   bindings.
4. Wire settings into runtime defaults: provider/model, theme, prompt history,
   quiet startup, thinking visibility, compaction/retry knobs, terminal/editor
   knobs, and HTTP idle timeouts.
5. Add system-prompt replacement/append flags and auto files:
   `.pipy/SYSTEM.md`, `<config>/SYSTEM.md`, `.pipy/APPEND_SYSTEM.md`,
   `<config>/APPEND_SYSTEM.md`; match Pi's text-or-file and unreadable-file
   fallback behavior.
6. Add `--no-context-files` and Pi-shaped tool/resource flags.
7. Add `/reload`, `/changelog`, `/hotkeys`, `/scoped-models`, and make theme
   selection a settings action rather than only `/theme`.

Complete when:

```sh
uv run python scripts/parity_checks/settings_config_conformance.py --json
just check
```

### 3. Pi-style JSON/RPC automation and retiring metadata-only JSON output

**Why it is large:** Pi exposes full-content automation modes; pipy only has
`pipy run`, `--stream`, metadata-only `--native-output json`, and the in-process
Python SDK.

Pi reference:

- `args.ts`: `--mode text|json|rpc`, `--print/-p`.
- `print-mode.ts` and `docs/json.md`: full session event JSONL stream.
- `modes/rpc/rpc-types.ts` and `rpc-mode.ts`: 29 command types, async prompt,
  mid-turn steer/follow-up/abort, session operations, extension UI bridge.

Pipy current state:

- `--native-output json` emits one final metadata object and is intentionally not
  Pi's full-event JSONL stream.
- The Python SDK exists, but there is no stdin/stdout RPC protocol.
- `docs/automation-rpc.md` is already the detailed owning spec.

Implement in pipy:

1. Add a stdlib LF-only JSONL reader/writer and single serialized stdout writer.
2. Serialize native session/tool/provider events as Pi-shaped
   `AgentSessionEvent` JSON objects with full message/tool/bash content.
3. Add `--mode json` one-shot event streaming and `--print/-p` text mode.
4. Add `--mode rpc` with the full Pi command vocabulary, correlated responses,
   asynchronous session events, prompt/steer/follow-up/abort, bash, compaction,
   model/thinking, session switch/fork/clone/new, state queries, and extension
   UI request/response.
5. Deprecate and then remove `--native-output json` as a product automation
   surface.

Complete when:

```sh
uv run python scripts/parity_checks/automation_rpc_conformance.py --json
just check
```

### 4. Interactive TUI/editor workflow depth and true cancellation

**Why it is user-visible:** pipy's daily-driver TUI is now usable, but Pi still
has richer editor behavior and true in-flight HTTP cancellation.

Pi reference:

- `interactive-mode.ts`: `!`/`!!` bash, queued steering/follow-up, Ctrl+P model
  cycling, Shift+Tab thinking cycle, Ctrl+O/Ctrl+T folding, image paste, and
  true abort.
- `packages/tui/src/autocomplete.ts`: `@` file picker uses exact/prefix/
  substring scoring, not fuzzy subsequence, and Tab path completion uses
  case-insensitive prefix matching.
- `packages/agent/src/agent.ts` plus provider calls pass an abort signal to
  the live HTTP request.

Pipy current state:

- Inline TUI, slash menu, `/settings`, `/model`, `/tree`, prompt history,
  bracketed paste, undo/redo, resize handling, typed `@path` and `@image:` all
  ship.
- Missing: `@` picker popup, general path completion, clipboard/drag image
  paste, `!`/`!!`, scoped model cycling, thinking hotkeys, folding, queued
  steering/follow-up, richer overlays, mouse-selection invariant tests, and
  true provider-request cancellation.
- `docs/tui-workflow.md` is already the detailed owning spec.

Implement in pipy:

1. Add the `@` file picker and path completion provider using stdlib walking and
   Pi's exact/prefix/substring ranking rules.
2. Add local `!`/`!!` bash shortcuts using the existing real bash executor.
3. Add Ctrl+P / Shift+Ctrl+P model cycling over scoped/available models and
   Shift+Tab thinking-level cycling.
4. Add Ctrl+O tool expansion and Ctrl+T thinking visibility toggles.
5. Add two-lane steering/follow-up queues and pending-message rendering.
6. Add provider cancellation tokens through `ProviderPort.complete(...)` and
   adapters so Escape closes live HTTP/SSE responses instead of only suppressing
   late chunks.
7. Add clipboard image paste and drag/drop path/image normalization.
8. Add `/hotkeys` and `/scoped-models` overlays, and assert mouse-tracking
   sequences are never enabled.

Complete when:

```sh
uv run python scripts/parity_checks/tui_workflow_conformance.py --json
just check
```

### 5. Full export/import/share/distribution surfaces

**Why it matters:** the native session tree now stores full product sessions,
but pipy's exported/shareable surface is still the metadata catalog or absent.

Pi reference:

- `core/export-html/*`: self-contained HTML export embeds full tree data.
- `agent-session.ts`: `exportToHtml`, `exportToJsonl`.
- `agent-session-runtime.ts`: `importFromJsonl`.
- `interactive-mode.ts`: `/export`, `/import`, `/share`, `/changelog`.
- `main.ts` and `args.ts`: `--export <file>` export-and-exit.
- `package-manager-cli.ts`, `version-check.ts`, and config helpers: install
  docs, version checks, update command family.

Pipy current state:

- Product native sessions exist.
- `pipy-session export` remains metadata-only and is not the product parity
  answer.
- `/export`, `/import`, `/share`, product `--export`, changelog startup, and
  self-update/distribution docs are not complete.
- `docs/export-distribution.md` is already the detailed owning spec.

Implement in pipy:

1. Add full native-session HTML export with inlined CSS/JS and embedded
   base64 session data.
2. Add JSONL active-branch export with linear parent re-chaining.
3. Wire `/export [path]`, `--export <session.jsonl> [output]`, and `/import
   <path.jsonl>` against the native session tree.
4. Add `/share` secret gist upload through stdlib `urllib` (or a documented
   `gh` token source), with token redaction and cancellation.
5. Add `/changelog`, startup changelog state, version checks, install-method
   detection, and `pipy update self|pipy` command planning/execution.

Complete when:

```sh
uv run python scripts/parity_checks/export_distribution_conformance.py --json
just check
```

### 6. Extension and package platform

**Why it is the largest platform gap:** Pi extensions can register tools,
commands, keybindings/shortcuts, CLI flags, providers, model catalog changes,
message renderers, lifecycle hooks, UI surfaces, resources, and package sources.
Pipy currently has bounded Markdown resources and themes, but no general Python
extension runtime or package manager.

Pi reference:

- `core/extensions/types.ts`, `loader.ts`, `runner.ts`: extension API, event
  hooks, tools, UI, providers, keybindings, flags.
- `core/package-manager.ts`: package source parsing, install/update/resolve,
  resource discovery, package manifest resources.
- `package-manager-cli.ts`: `install`, `remove`/`uninstall`, `list`, `config`,
  and `update` command behavior.
- `settings-manager.ts`: packages/extensions/skills/prompts/themes settings
  arrays and resource filters.

Pipy current state:

- `docs/extension-api.md` defines a Python extension API target.
- Runtime resources exist for `.pipy/skills`, `.pipy/templates`,
  `.pipy/commands`, and themes.
- Package install/remove/list/update/config is not implemented.

Implement in pipy:

1. Add a Python-only extension discovery and activation runtime for explicit
   local paths, workspace `.pipy/extensions`, and global config extensions.
2. Support command registration, safe lifecycle hooks, tool-call policy hooks,
   pure/read-only tool registration, minimal UI notifications, then the golden
   conformance extension.
3. Add provider registration by composing with the provider catalog
   `register_provider`/`unregister_provider` boundary.
4. Add package sources and commands: `pipy install <source> [-l]`,
   `remove`/`uninstall`, `list`, `config`, and `update [source|self|pipy]`.
5. Store package/resource enablement in settings using Pi-shaped arrays and
   `+pattern`/`-pattern` filters rather than deleting discovered resources.
6. Keep source execution local/trusted in the first implementation; network
   package sources need a pin/update/security story before automatic execution.

Complete when a golden extension/package conformance gate proves discovery,
activation, command/tool registration, hooks, UI degradation, provider
registration, package install/list/config/update, and archive secret hygiene.
The package-manager details are now added to `docs/extension-api.md`.

### 7. Session CLI and picker polish

**Why it remains despite the session-tree milestone:** the durable product
session tree shipped, but a few Pi CLI/session-picker surfaces remain incomplete.

Pi reference:

- `args.ts` and `main.ts`: `--session-id`, `--session-dir`, `--name/-n`, `-r`
  startup picker, cross-project session fork prompt, strict flag conflicts.
- `session-picker.ts` and interactive components: picker search, path toggle,
  sort toggle, named-only filter, rename/delete flows.

Pipy current state:

- Shipped: `/session`, `/name`, `/new`, `/tree`, `/resume` listing/subcommands,
  `/fork`, `/clone`, durable `/compact`, `-c`, `-r` most-recent behavior,
  `--session`, `--fork`, `--no-session`.
- Missing or deferred: exact `--session-id`, `--session-dir`, startup `--name`,
  true interactive `-r` picker, interactive `/resume` picker with search/sort/
  path/name filters and in-overlay rename/delete, and cleanup of old
  metadata-only `--resume`/`--branch` surfaces.

Implement in pipy:

1. Add `--session-id <id>` and `--session-dir <dir>` with Pi-equivalent lookup
   and creation semantics.
2. Add top-level startup `--name/-n <name>` for new sessions.
3. Promote `-r` to an actual TTY picker and keep most-recent behavior only for
   captured streams.
4. Add the full `/resume` picker overlay and reuse its actions in startup mode.
5. Enforce Pi's mutual-exclusion and cross-project fork prompts.
6. Retire or realign the old pipy-only `--resume RECORD`/`--branch LABEL`
   metadata flow.

Complete by extending `scripts/parity_checks/session_tree_conformance.py --json`
with the above CLI/picker follow-ups.

### 8. User documentation parity

**Why it is needed:** pipy has extensive internal specs, but Pi has product docs
for users. This gap will become more painful as the runtime matures.

Implement in pipy:

1. Add user-facing quickstart, usage, providers/models, sessions, settings,
   keybindings, customization, automation, terminal setup, tmux, SDK/RPC, and
   install/update pages.
2. Keep README short and outside-in.
3. Separate shipped behavior from target specs; do not present pipy-only
   divergences as parity.
4. Audit docs against `uv run pipy --help`, `uv run pipy repl --help`,
   `uv run pipy run --help`, and the relevant conformance gates.

Owning spec: `docs/user-documentation.md`.

## Recommended implementation order

1. Provider/model catalog product wiring.
2. Settings/config/keybindings core and command realignment.
3. Session CLI/picker polish that is small enough to land alongside settings,
   especially `--name`, `--session-id`, and `--session-dir`.
4. TUI workflow depth, with true provider-request cancellation promoted early.
5. Automation `--mode json`, then `--mode rpc`.
6. Export/import/share/distribution.
7. Extension/package platform in staged slices, starting with local Python
   extension activation and command/tool registration.
8. User documentation in parallel with each shipped surface.

The extension/package platform is the largest gap by surface area, but it should
not be first: it depends on the provider catalog, settings/resource enablement,
keybindings, RPC extension UI, and product session tree being stable enough to
be safe extension targets.

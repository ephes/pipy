# Pi-Mono Gap Audit

Status: comparison snapshot and implementation specification, 2026-06-02;
updated 2026-06-03 after settings/keybindings and the first provider-catalog
product-construction slice landed.

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
- current provider-catalog gate:
  `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`

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

## Fresh command-surface deltas from this pass

The direct help comparison is still the fastest sanity check for parity drift:

- `pi --help` is a single top-level product command with interactive, print,
  JSON, RPC, session, provider/model, settings/resource, package-management,
  export, and update flags. `uv run pipy --help` still exposes a harness-shaped
  `auth|run|repl` subcommand layout, so product CLI parity requires either a
  top-level compatibility layer or help/dispatch aliases that make common Pi
  invocations work without first choosing `run` or `repl`.
- Pi top-level package commands (`install`, `remove`/`uninstall`, `update`,
  `list`, `config`) are absent from pipy's help. This is part of the
  extension/package platform, not export polish.
- Pi automation flags (`--mode text|json|rpc`, `--print/-p`) are absent from
  pipy's top-level help; pipy's `--native-output json` remains a metadata-only
  `run` subcommand flag and is not a Pi-compatible replacement.
- Pi resource/settings flags are split: `--system-prompt`, repeated
  `--append-system-prompt`, `--no-context-files`, `--version`, resource
  enablement through `pipy config`, `/reload`, `/hotkeys`, `/scoped-models`, and
  `/changelog` now ship on pipy's current subcommand-shaped product surface.
  Top-level compatibility aliases, `--extension`, tool allow/deny flags,
  `--verbose`, `--offline`, and theme load flags remain open.
- Pi session flags `--session-id`, `--session-dir`, and `--name/-n` are still
  missing from pipy's REPL session-start surface, even though the native session
  tree itself has shipped.

These CLI deltas do not change the recommended next big topic: provider-catalog
closeout remains first because it is the smallest high-leverage runtime gap and
unblocks reliable provider/model behavior in one-shot, REPL, TUI, automation,
and later extension/provider registration. They do, however, make a later
top-level `pipy` compatibility dispatcher a real parity requirement rather than
docs polish.

## Ranked biggest gaps

### 1. Provider/model catalog closeout — shipped on current branch

**Why it was first:** this was the highest-leverage incomplete runtime slice.
The catalog, matcher, `models.json`, auth helpers, OAuth registry,
`--list-models`, full-catalog `model_options()`, direct `/model <ref>` resolver,
Chat-Completions-family product construction, implemented catalog-constructed
non-completions families, `pipy run` one-shot construction, and startup
provider/model resolution now exist on the current feature branch. The
conformance gate passes through item 24.

Pi reference:

- `model-registry.ts` resolves the selected model, custom providers,
  request auth, headers, routing, and OAuth mutations before each request.
- `model-resolver.ts` owns exact/fuzzy/glob/`:thinking` matching for CLI,
  selectors, and scoped cycling.
- `args.ts` exposes `--provider`, `--model`, `--api-key`, `--thinking`,
  `--models`, and `--list-models` as one connected surface.

Pipy current state:

- `scripts/parity_checks/provider_catalog_conformance.py --json` passes items
  1-24, including product-path fake HTTP captures for Chat Completions,
  non-completions families, one-shot construction, startup resolution, and
  archive secret checks.
- REPL product turns for the OpenAI-compatible Chat Completions family use
  catalog construction: custom `models.json` providers, ds4, OpenRouter, and
  OpenAI-style calls receive catalog base URL, model id, auth, headers, routing,
  and thinking config.
- The implemented catalog-constructed non-completions families now receive the
  selected `NativeModelSpec` and resolved request config through the same
  boundary: `anthropic-messages`, `openai-responses`,
  `google-generative-ai`, `google-vertex`, `amazon-bedrock`,
  `azure-openai-responses`, `cloudflare-workers-ai`, and `mistral`.
- `openai-codex-responses` deliberately stays on the legacy factory because it
  needs the settings-derived `RetryPolicy`; conformance covers that exception.
- `pipy run` one-shot construction uses the catalog-backed provider-state
  boundary, and startup `--native-provider`/`--native-model` resolve through the
  shared resolver, including custom `models.json` providers and bare refs.
- `docs/provider-catalog.md` is the detailed owning spec.

Implement next in pipy:

1. Finish branch closeout: keep backlog/parity docs aligned with the shipped
   state, run the provider catalog gate and `just check`, and complete an
   independent review pass before merging.
2. Treat live Anthropic/Copilot login UX, Vertex API-key auth, Anthropic
   adaptive thinking, Azure URL/api-version parity, and extension-registered
   providers as follow-on slices rather than part of this construction closeout.

Complete when:

```sh
uv run python scripts/parity_checks/provider_catalog_conformance.py --json
just check
```

The conformance script now includes explicit non-completions, one-shot, and
startup-resolution checks. Further provider work should add new focused checks
for the follow-on adapter behavior it changes.

### 2. Interactive TUI/editor workflow depth and true cancellation

**Why it is user-visible:** pipy's daily-driver TUI is now usable, but Pi still
has richer editor behavior and true in-flight HTTP cancellation.

Pi reference:

- `interactive-mode.ts`: `!`/`!!` bash, queued steering/follow-up, Ctrl+P model
  cycling, Shift+Tab thinking cycle, Ctrl+O/Ctrl+T folding, image paste, and
  true abort.
- `packages/tui/src/autocomplete.ts`: `@` file picker uses exact/prefix/
  substring scoring, not fuzzy subsequence, and Tab path completion uses
  case-insensitive prefix matching.
- `packages/agent/src/agent.ts` plus provider calls pass an abort signal to the
  live HTTP request.

Pipy current state:

- Inline TUI, slash menu, `/settings`, `/model`, `/tree`, prompt history,
  bracketed paste, undo/redo, resize handling, typed `@path` and `@image:`,
  `/hotkeys`, `/scoped-models`, and Ctrl+P model cycling all ship.
- Missing: `@` picker popup, general path completion, clipboard/drag image
  paste, `!`/`!!`, thinking hotkeys, folding, queued steering/follow-up, richer
  overlays, mouse-selection invariant tests, and true provider-request
  cancellation.
- `docs/tui-workflow.md` is the detailed owning spec.

Implement in pipy:

1. Add the `@` file picker and path completion provider using stdlib walking and
   Pi's exact/prefix/substring ranking rules.
2. Add local `!`/`!!` bash shortcuts using the existing real bash executor.
3. Add Shift+Tab thinking-level cycling.
4. Add Ctrl+O tool expansion and Ctrl+T thinking visibility toggles.
5. Add two-lane steering/follow-up queues and pending-message rendering.
6. Add provider cancellation tokens through `ProviderPort.complete(...)` and
   adapters so Escape closes live HTTP/SSE responses instead of only suppressing
   late chunks.
7. Add clipboard image paste and drag/drop path/image normalization.
8. Assert mouse-tracking sequences are never enabled.

Complete when:

```sh
uv run python scripts/parity_checks/tui_workflow_conformance.py --json
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

### 4. Full export/import/share/distribution surfaces

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

### 5. Extension and package platform

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

### 6. Session CLI and picker polish — ✅ shipped (2026-06-09)

**Status:** complete. All six implementation items below shipped:
`--session-id`/`--session-dir`/`-n`/`--name`, the interactive `-r` startup
picker and `/resume` picker overlay (search/scope/sort/named-only/rename/
delete), Pi mutual-exclusion errors, the cross-project `--session` fork prompt,
and the retirement of the old metadata-only `--resume RECORD`/`--branch LABEL`
flow. Proven through product paths by the extended
`scripts/parity_checks/session_tree_conformance.py --json` and the new
`scripts/parity_checks/session_tree_pi_comparison.py --json` Pi comparison. The
original gap analysis is retained below for historical record.

**Why it remained despite the session-tree milestone:** the durable product
session tree shipped, but a few Pi CLI/session-picker surfaces were incomplete.

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

### 7. User documentation parity

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

1. Provider/model catalog branch closeout: docs, verification, independent
   review, and merge readiness. Construction work is shipped on the current
   branch.
2. TUI workflow depth, with true provider-request cancellation promoted early.
3. Automation `--mode json`, then `--mode rpc`.
4. Session CLI/picker polish, especially `--name`, `--session-id`,
   `--session-dir`, and the interactive resume picker.
5. Export/import/share/distribution.
6. Extension/package platform in staged slices, starting with local Python
   extension activation and command/tool registration.
7. User documentation in parallel with each shipped surface.

The extension/package platform is the largest gap by surface area, but it should
not be first: it depends on the provider catalog, settings/resource enablement,
keybindings, RPC extension UI, and product session tree being stable enough to
be safe extension targets. The settings/config/keybindings core has shipped and
now supports the later extension/resource story.

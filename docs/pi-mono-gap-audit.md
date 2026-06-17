# Pi-Mono Gap Audit

Status: comparison snapshot and implementation specification, originally
written 2026-06-02 and groomed 2026-06-17 after session CLI/pickers,
settings/keybindings, TUI workflow, provider-catalog construction, JSON/RPC
automation, and extension/package slice-12 runtime composition shipped.

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

## Fresh command-surface deltas from this grooming pass

The direct help comparison remains the fastest sanity check for parity drift.
After the 2026-06-17 grooming pass, the important command-surface deltas are:

- `pi --help` is a single top-level product command with interactive, print,
  JSON, RPC, session, provider/model, settings/resource, package-management,
  export, and update flags. `uv run pipy --help` still exposes a
  harness-shaped `auth|run|repl` subcommand layout in places, even though many
  Pi-compatible surfaces now exist under `pipy repl`. Top-level compatibility
  dispatch/help remains a parity cleanup item.
- Pi top-level package commands are partially present in pipy: `install`,
  `remove`/`uninstall`, `list`, and `config` now manage local-path package
  sources and resource filters; installed local-path packages contribute
  extensions/skills/prompts/themes through discovery. `update` plus remote
  `git:`/PyPI/`npm:` sources remain deferred to a supply-chain policy.
- Pi automation modes (`--mode json`, `--mode rpc`, `--print`/`-p`) now ship in
  pipy under the REPL product path and are backed by the native session tree.
  The remaining automation cleanup is retiring pipy's old metadata-only
  `--native-output json` after callers move.
- Pi session flags and picker workflows now ship: `--session-id`,
  `--session-dir`, `--name/-n`, `-c`, `-r`, `--session`, `--fork`, and
  `--no-session`, with Pi-style mutual exclusion and the cross-project fork
  prompt.
- Pi resource/settings flags mostly ship on pipy's current product surface:
  system-prompt replace/append, `--no-context-files`, `--version`, resource
  enablement through `pipy config`, `/reload`, `/hotkeys`, `/scoped-models`,
  `/changelog`, settings/keybindings files, and scoped model cycling. Still
  open: `--extension`, tool allow/deny flags, `--verbose`, `--offline`, theme
  load flags, and resource-wrapper cleanup (`/skill`/`/template`/`/theme`).

The extension/package closeout changed the next-topic ordering. Core local
extension workflows and local-path package runtime composition have landed, so
the selected next implementation topic is now product export/import/share/
distribution. Extension/package work remains a large follow-on area, but its
next slices are remote source/update policy and richer platform APIs rather
than the just-landed local package runtime.

## Ranked biggest gaps

### 1. Export / import / share / distribution — selected next topic

**Why it is first now:** the native session tree now stores full product
sessions and the extension/package slice-12 closeout has landed, so Pi-style
full export/import/share is unblocked and comparatively bounded.

Pi reference:

- `core/export-html/*`: self-contained HTML export with embedded session data.
- `agent-session.ts`: `exportToHtml`, `exportToJsonl`.
- `agent-session-runtime.ts`: `importFromJsonl`.
- `interactive-mode.ts`: `/export`, `/import`, `/share`, `/changelog`.
- `main.ts` and `args.ts`: `--export <file>` export-and-exit.
- `package-manager-cli.ts`, `version-check.ts`, and config helpers: install
  docs, version checks, update command family.

Pipy current state:

- Product native sessions exist and contain full transcripts.
- `pipy-session export` remains metadata-only and is not the product parity
  answer.
- `/export`, `/import`, `/share`, product `--export`, and self-update/
  distribution docs are incomplete.

Implement next in pipy:

1. Full native-session HTML export with inlined CSS/JS and embedded base64
   session data.
2. JSONL active-branch export with linear parent re-chaining.
3. `/export [path]`, `--export <session.jsonl> [output]`, and `/import
   <path.jsonl>` against the native session tree.
4. `/share` secret gist upload or documented `gh` boundary with redaction and
   cancellation.
5. Install/update/version documentation and safe self-update planning.

Complete when the planned export conformance gate has been added and passes:

```sh
uv run python scripts/parity_checks/export_distribution_conformance.py --json
just check
```

### 2. Product-TUI long-input wrapping bug

**Why it is second:** the product TUI still horizontally scrolls a long editable
prompt in one physical input row. Pi soft-wraps long typed input inside the
input frame, with the cursor moving across wrapped rows while footer/status rows
stay pinned.

Implement in pipy:

1. Replace `ToolLoopTerminalUi._input_view(width)` and the one-row input
   `_FrameLine` projection with a soft-wrapped input region.
2. Reserve dynamic input height in the live-region budget while keeping footer
   and status rows pinned.
3. Map cursor index to wrapped row/column and preserve literal submitted text,
   including pasted newlines.
4. Add real-PTY coverage at 80x24 and 100x40 for long typing, paste, cursor
   movement, and resize.
5. Update docs/spec rows that currently describe horizontal scrolling as
   shipped parity.

### 3. Extension and package platform follow-ons

**Why it remains important:** Pi's extension/package story is still broader and
more mature. Pipy is now Pi-shaped for core local extension workflows, but not
Pi-equivalent as a platform.

Pipy current state:

- `docs/extension-api.md` defines and tracks a Python-only, Pi-shaped API.
- Extension slices 1–12 have shipped: local discovery/inventory, activation,
  command dispatch, `tool_call` gates, lifecycle/input/before-agent-start hooks,
  extension tool registration, `tool_result` transforms, minimal UI
  notification, golden conformance, shortcuts, provider-registration mechanics,
  local-path package CLI, and package runtime composition for installed
  local-path package resources.
- Package resources now flow through discovery at deterministic lowest
  precedence with filters applied, and the package conformance gate proves no
  source path or resource body leaks to safe metadata.

Follow-ons:

1. CLI source-loading flags (`--extension`/`--no-extensions`) and dynamic
   extension flags.
2. Richer Pi extension APIs: UI/rendering, session switch/fork/tree/compaction
   hooks, dynamic active-tool/model/thinking controls, `user_bash`,
   provider-payload hooks, and extension state/session-manager views.
3. Catalog/`/model` wiring for extension-registered providers.
4. Remote package sources and `update` only after a supply-chain/update policy
   and isolated package cache.

### 4. User documentation parity

**Why it is needed:** pipy now has enough shipped product surface that internal
specs are no longer sufficient. Pi has user-facing pages for installation,
first run, providers, settings, keybindings, sessions, compaction,
customization, automation, SDK/RPC, and terminal/platform setup.

Implement in pipy:

1. Add user-facing quickstart, usage, providers/models, sessions, settings,
   keybindings, customization, automation, terminal setup, tmux, SDK/RPC, and
   install/update pages.
2. Keep README short and outside-in.
3. Separate shipped behavior from target specs; do not present pipy-only
   divergences as parity.
4. Audit docs against `uv run pipy --help`, `uv run pipy repl --help`,
   `uv run pipy run --help`, and the relevant conformance gates.

Owning spec: [user-documentation.md](user-documentation.md).

### 5. Provider/model catalog follow-ons

**Why it is now narrower:** the catalog construction foundation has shipped for
the implemented adapter families, one-shot runs, and startup resolution.
Remaining work is adapter/product polish rather than the broad catalog track.

Follow-ons:

- live Anthropic and GitHub Copilot login UX;
- Vertex API-key auth;
- Anthropic adaptive-thinking request shape;
- Azure URL/api-version parity;
- broader local-provider maturity and benchmarking; and
- extension-registered providers after the extension API exists.

Owning spec: [provider-catalog.md](provider-catalog.md).

### 6. Top-level CLI compatibility and parity cleanup

Pipy still has a harness-shaped command layout and pipy-only historical surfaces
that should be removed or realigned as their owning areas are touched:
`--archive-transcript`, no-tool REPL/proposal commands, `/clear`, `/status`,
`/theme`, `/skill`, `/template`, `/help`, metadata-only `--native-output json`,
and exposed internal flags that do not map to Pi. This should be staged, not
landed as one large rewrite.

### 7. Verification policy through extensions

The former pipy-only `/verify just-check` command is gone. Richer verification
or permission policy should arrive as extension-defined tools/hooks once the
extension platform exists, matching Pi's extensibility posture rather than
adding another bespoke slash command.

## Recommended implementation order

1. Export/import/share/distribution, now unblocked by the native session tree
   and selected after the local extension/package closeout.
2. Product-TUI long-input wrapping.
3. Extension/package platform follow-ons: source-loading flags, richer hooks/UI,
   extension-provider catalog wiring, and remote sources/update after policy.
4. User documentation parity in parallel with implementation.
5. Focused provider/model catalog follow-ons.
6. Top-level CLI compatibility and pipy-only surface cleanup staged alongside
   the owning topics.
7. Verification/project policy through extension gates, not a revived `/verify`
   command.

The extension/package platform remains the largest follow-on by surface area,
but its first local-runtime slices have already landed. Future extension/package
work should start from the shipped package-runtime baseline and stay behind the
supply-chain/update policy boundary for remote sources.

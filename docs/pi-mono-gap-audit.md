# Pi-Mono Gap Audit

Status: comparison snapshot and implementation specification, originally
written 2026-06-02 and groomed 2026-06-15 after session CLI/pickers,
settings/keybindings, TUI workflow, provider-catalog construction, and
JSON/RPC automation shipped.

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
After the 2026-06-15 grooming pass, the important command-surface deltas are:

- `pi --help` is a single top-level product command with interactive, print,
  JSON, RPC, session, provider/model, settings/resource, package-management,
  export, and update flags. `uv run pipy --help` still exposes a
  harness-shaped `auth|run|repl` subcommand layout in places, even though many
  Pi-compatible surfaces now exist under `pipy repl`. Top-level compatibility
  dispatch/help remains a parity cleanup item.
- Pi top-level package commands (`install`, `remove`/`uninstall`, `update`,
  `list`, `config`) are absent from pipy's package-platform surface. This is
  now part of the extension/package platform, not export polish.
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

These deltas made extension/package support the next big topic. Since this
audit snapshot, the core Python extension runtime has landed through reviewed
slices; the highest-leverage remaining extension/package gap is now package
runtime composition plus richer Pi extension-platform follow-ons.

## Ranked biggest gaps

### 1. Extension and package platform — selected topic, in closeout

**Why it is first now:** Pi's extension/package story remains the largest
remaining platform gap and the surface that lets users adapt the agent without
forking. Pipy now has core local Python extension support, but it is
Pi-shaped rather than Pi-equivalent: common local automation patterns are
covered, while Pi's mature package distribution, rich UI/rendering, broader
session hooks, dynamic controls, and source-loading flags remain ahead.

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

- `docs/extension-api.md` defines and tracks a Python-only, Pi-shaped API.
- Runtime resources exist for `.pipy/skills`, `.pipy/templates`,
  `.pipy/commands`, and themes.
- Extension slices 1–11 have shipped: local discovery/inventory, activation,
  command dispatch, `tool_call` gates, lifecycle/input/before-agent-start hooks,
  extension tool registration, `tool_result` transforms, minimal UI
  notification, golden conformance, shortcuts, and provider-registration
  mechanics.
- Slice 12's local-path package CLI ships (`install/remove/uninstall [-l]`,
  `list`, `config`), but installed package resources are not yet loaded.

Implement next in pipy:

1. Package runtime composition: installed local-path package manifests contribute
   extensions/skills/prompts/themes through discovery at deterministic lowest
   precedence, with filters applied and archive-privacy proof.
2. CLI source-loading flags (`--extension`/`--no-extensions`) and dynamic
   extension flags after package composition is stable.
3. Richer Pi extension follow-ons: UI/rendering, session switch/fork/tree/
   compaction hooks, dynamic active-tool/model/thinking controls, `user_bash`,
   provider-payload hooks, and extension state/session-manager views.
4. Remote package sources and `update` only after a supply-chain/update policy.

Complete the closeout when the package conformance gate described in
[extension-api.md](extension-api.md) proves package resources flow through real
discovery and no source path/resource body leaks to the metadata archive.

### 2. Export / import / share / distribution

**Why it is second:** the native session tree now stores full product sessions,
so Pi-style full export/import/share is unblocked and comparatively bounded. It
is the best alternate next topic if extension-platform risk should be reduced.

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

Implement in pipy:

1. Full native-session HTML export with inlined CSS/JS and embedded base64
   session data.
2. JSONL active-branch export with linear parent re-chaining.
3. `/export [path]`, `--export <session.jsonl> [output]`, and `/import
   <path.jsonl>` against the native session tree.
4. `/share` secret gist upload or documented `gh` boundary with redaction and
   cancellation.
5. Install/update/version documentation and safe self-update planning.

Complete when:

```sh
uv run python scripts/parity_checks/export_distribution_conformance.py --json
just check
```

### 3. User documentation parity

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

### 4. Provider/model catalog follow-ons

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

### 5. Top-level CLI compatibility and parity cleanup

Pipy still has a harness-shaped command layout and pipy-only historical surfaces
that should be removed or realigned as their owning areas are touched:
`--archive-transcript`, no-tool REPL/proposal commands, `/clear`, `/status`,
`/theme`, `/skill`, `/template`, `/help`, metadata-only `--native-output json`,
and exposed internal flags that do not map to Pi. This should be staged, not
landed as one large rewrite.

### 6. Verification policy through extensions

The former pipy-only `/verify just-check` command is gone. Richer verification
or permission policy should arrive as extension-defined tools/hooks once the
extension platform exists, matching Pi's extensibility posture rather than
adding another bespoke slash command.

## Recommended implementation order

1. Extension/package platform, beginning with local Python extension discovery
   and manifest inventory with no code execution.
2. Export/import/share/distribution, now unblocked by the native session tree.
3. User documentation parity in parallel with implementation.
4. Focused provider/model catalog follow-ons.
5. Top-level CLI compatibility and pipy-only surface cleanup staged alongside
   the owning topics.
6. Verification/project policy through extension gates, not a revived `/verify`
   command.

The extension/package platform is still the largest gap by surface area, but the
first slice should be deliberately small: inventory and manifests only. Package
installation, provider registration, custom UI, and model-visible extension tools
come later after the local runtime boundary is reviewed.

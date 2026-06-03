# Pi-Style Settings, Config, and Keybindings Parity

Status: target specification researched from the local Pi reference on
2026-06-02; **implemented and shipped 2026-06-03** (see the per-section
"Shipped" notes and the conformance gate below).

This document defines the pipy target for real feature parity with Pi's
settings/config/keybindings system through pipy-owned Python boundaries. It is
not a TypeScript port. This surface now ships: a layered settings system
(`pipy_harness.native.settings`), a configurable `keybindings.json` (single or
alternative key specs per action) with `/hotkeys`, scoped models with Ctrl+P
cycling, transport/delivery + compaction/retry/branch-summary settings (honored
where a runtime surface exists, otherwise accepted + round-tripped + reported),
system-prompt replace/append inputs, resource-enablement config (`pipy config`),
context-file discovery toggles (`--no-context-files`), `/reload`, `/changelog`,
and a `--version` surface with a default-off update check. The objective for this
track was full Pi-equivalent settings capability, not a metadata-only subset; it
is verified by `scripts/parity_checks/settings_config_conformance.py`. The
sections below keep the full target spec and add a "Shipped" note where the
delivered behavior or a deliberate divergence needs calling out.

## Sources

Researched from the local reference checkout at `/Users/jochen/src/pi-mono`,
especially:

- `packages/coding-agent/src/core/settings-manager.ts` — settings schema,
  global+project discovery, deep-merge precedence, per-field modified tracking,
  lockfile-guarded writes, and the `migrateSettings` migrations.
- `packages/coding-agent/src/core/keybindings.ts` — `KEYBINDINGS` definitions
  (35+ app bindings layered on `TUI_KEYBINDINGS`), single-or-alternative
  bindings, the `keybindings.json` schema (string or `string[]` per action),
  legacy-name migration, ordering, and `KeybindingsManager.reload()`.
- `packages/coding-agent/src/modes/interactive/components/keybinding-hints.ts`
  and `interactive-mode.ts` `handleHotkeysCommand` (lines ~5243+) — `/hotkeys`
  table rendering from resolved bindings.
- `packages/coding-agent/src/cli/config-selector.ts` and
  `modes/interactive/components/config-selector.ts` — the `pi config` TUI for
  enabling/disabling package/top-level resources (extensions/skills/prompts/
  themes), grouped by user/project/package scope.
- `packages/coding-agent/src/modes/interactive/interactive-mode.ts`
  `handleReloadCommand` (lines ~4866+), `handleChangelogCommand` (~5208+),
  `getChangelogForDisplay`/`reportInstallTelemetry` (~857+), and
  `BUILTIN_SLASH_COMMANDS` in `core/slash-commands.ts`.
- `packages/coding-agent/src/core/system-prompt.ts` (`buildSystemPrompt`,
  `customPrompt` replacement vs `appendSystemPrompt`) and
  `cli/args.ts` (`--system-prompt`, `--append-system-prompt`,
  `--no-context-files`/`-nc`, `main.ts` wiring at ~547).
- `packages/coding-agent/src/config.ts` — `CONFIG_DIR_NAME` (`.pi`),
  `getAgentDir()` (`~/.pi/agent`), `VERSION`, `getChangelogPath`.
- `packages/coding-agent/src/utils/version-check.ts`,
  `utils/changelog.ts`, `core/telemetry.ts`.

Pipy current state:

- `docs/harness-spec.md` (`/settings`, `NativeThemeStore`, `PromptHistory`).
- `docs/backlog.md` Pi Gap Queue item 2 (interactive `/settings`) and
  "Current Largest Pi Feature Gaps" item 6 (settings/distribution polish).
- `src/pipy_harness/native/repl_state.py` (`NativeDefaultsStore`,
  `settings_overlay_lines`, `default_native_defaults_path`),
  `prompt_history.py` (`PromptHistoryStore` atomic owner-only write pattern),
  `themes.py`, `resources.py`, `workspace_context.py`, `chrome.py`.
- House style: `docs/session-tree.md` and `docs/extension-api.md`.

## Target Outcome / Goal

`pipy repl --agent pipy-native` (and `pipy run`) read a layered settings system
that reaches Pi-equivalent capability:

- A global settings file plus a project settings file, deep-merged with project
  precedence, lock-guarded, and migration-aware. Loading matches Pi (parse →
  migrate → cast to a typed `Settings`, with validation living in typed getters),
  not load-time JSON-schema validation; any additional manual per-field
  validation pipy adds is a pipy implementation choice, not Pi parity.
- A `keybindings.json` file with 35+ bindings (single or alternative key specs
  per action), defaults, legacy
  migration, and a `/hotkeys` table that renders the resolved bindings.
- Scoped models (`enabledModels`) for Ctrl+P-style cycling and a
  `/scoped-models` selector.
- Message-delivery (`steeringMode`, `followUpMode`) and transport (`transport`,
  `httpIdleTimeoutMs`) settings.
- Compaction, retry, and branch-summary tuning.
- System-prompt replacement (`--system-prompt`) and append
  (`--append-system-prompt`, repeatable, text-or-file) inputs.
- Resource-enablement toggles equivalent to the `pi config` TUI, plus
  context-file discovery toggles (`--no-context-files`).
- `/reload`, `/changelog`, `/hotkeys`, and version/update checks.
- Settings migration of legacy field names/shapes.

The work may land in reviewed milestones, but the objective is the full
Pi-equivalent settings/config/keybindings surface through pipy-owned Python.
The implementation is complete only when the conformance gate below passes.

Privacy note: pipy does **not** carry a "metadata-first privacy" restriction
into this track. Settings, keybindings, scoped-model lists, transport choices,
and resource-enablement state are ordinary local configuration and should reach
full Pi-equivalent capability. The only standing restriction is the normal one:
secrets/tokens (API keys, OAuth credentials) stay out of any session archive and
out of any settings file pipy writes — keep auth in the existing dedicated auth
stores, exactly as today.

## pipy Config Home (decision)

Pi uses `~/.pi/agent/settings.json` (global) and `<cwd>/.pi/settings.json`
(project), with `CONFIG_DIR_NAME = ".pi"` and `getAgentDir() = ~/.pi/agent`.

pipy already standardizes a config-home resolution chain in
`workspace_context.discover_workspace_instructions` and `extension-api.md`:
`PIPY_CONFIG_HOME` → `${XDG_CONFIG_HOME}/pipy` → `~/.config/pipy`. This track
**reuses that exact chain** rather than inventing a new root. Decision:

- Global config home (`<config>`):
  `PIPY_CONFIG_HOME` → `${XDG_CONFIG_HOME}/pipy` → `~/.config/pipy`. pipy reuses
  the single shared resolver `workspace_context.resolve_global_instruction_root`
  (so there is exactly one config root), which also probes `~/.pipy` **when that
  directory already exists** as a convenience step just ahead of
  `~/.config/pipy`; that extra step is a superset of the documented chain and is
  inert unless a `~/.pipy` directory is present.
  - Global settings: `<config>/settings.json`.
  - Keybindings: `<config>/keybindings.json`.
- Project config dir: `<cwd>/.pipy/`.
  - Project settings: `<cwd>/.pipy/settings.json`. This mirrors Pi's
    `<cwd>/.pi/settings.json` (Pi's `CONFIG_DIR_NAME = ".pi"`). pipy reads and
    writes only `.pipy/settings.json` for the project scope.

Pi reads **only** global `~/.pi/agent/settings.json` and project
`<cwd>/.pi/settings.json` for settings (`settings-manager.ts`, `config.ts`);
it does **not** read `.claude/settings.json` for settings. pipy therefore does
not read `.claude/settings.json` either — the project scope is `.pipy/` only.
(pipy still discovers `CLAUDE.md` as a context/instruction file via
`workspace_context`; that is unrelated to the settings system.)

Rationale: keeping the global root on the existing `PIPY_CONFIG_HOME` chain
avoids a second incompatible config root (the extension loader and workspace
context already use it), and `.pipy/settings.json` mirrors Pi's `.pi/settings.json`
exactly. Live runtime state that is not user configuration (native session
trees, prompt history, native defaults) stays under `~/.local/state/pipy/` as
today and is not moved into the config home.

Local-state files that already exist stay where they are and remain the
implementation for their narrow setting:

- `NativeDefaultsStore` → `~/.local/state/pipy/native-defaults.json`
  (`PIPY_NATIVE_DEFAULTS_PATH`).
- `PromptHistoryStore` → `~/.local/state/pipy/prompt-history.json`
  (`PIPY_PROMPT_HISTORY_PATH`).
- `NativeThemeStore` / `PIPY_THEME`.

These become *backed by* or *mirrored into* the settings system (see Migration),
but their on-disk runtime-state files are not the config home.

## Global vs Project settings.json — Discovery, Precedence, Schema

### Discovery and precedence

Pi's `FileSettingsStorage` resolves:

- global = `join(agentDir, "settings.json")`
- project = `join(cwd, CONFIG_DIR_NAME, "settings.json")`

and `SettingsManager` deep-merges global as base, project as overrides, with
`deepMergeSettings`. Note the precise (shallow) merge depth: `deepMergeSettings`
merges only **one level** of top-level nested objects — for a top-level key
whose value is a plain object in both layers, the two objects are shallow-merged
key-by-key (`{ ...base[key], ...override[key] }`); it does **not** recurse
further. So a deeper nested object such as `retry.provider` is replaced
**wholesale** by the higher-precedence layer, not recursively merged. Top-level
primitives and arrays are also replaced wholesale. pipy mirrors this exactly:

Effective settings = deep-merge(
  global `<config>/settings.json`,
  then project `<cwd>/.pipy/settings.json`,
), where the project layer overrides the global, top-level nested objects are
shallow-merged one level (e.g. `compaction`, `retry`, `terminal`), and deeper
nested objects (e.g. `retry.provider`), top-level scalars, and arrays are
replaced wholesale. CLI flags (`--system-prompt`, `--no-context-files`, etc.)
and process env apply as a final override layer on top of the merged file
settings (Pi's `applyOverrides`).

Pi does **not** run full JSON-schema validation on load. Parsed JSON is run
through `migrateSettings` and cast to the `Settings` TypeScript interface;
validation lives in individual getters (e.g. `getHttpIdleTimeoutMs` rejects
invalid values at read time). pipy mirrors the load behavior (parse → migrate →
typed accessors). pipy **may** additionally add its own manual per-field
validation in the loader/getters; that is a pipy implementation choice, not Pi
parity, and must still accept and round-trip unknown keys.

Missing files do not fail (treated as `{}`). A file that fails to parse is
recorded as a per-scope load error, the scope falls back to `{}`, and pipy must
surface a safe diagnostic without crashing — matching Pi's
`tryLoadFromStorage`/`drainErrors`. A scope with a load error must not be
written back over (Pi guards `save` / `saveProjectSettings` against load
errors).

### Writes

- pipy writes global fields to `<config>/settings.json` and project fields to
  `<cwd>/.pipy/settings.json`.
- Writes are field-scoped and lock-guarded: only fields modified during the
  session are merged into the current on-disk file, preserving
  unknown/forward-compatible keys and concurrently-written fields. This mirrors
  Pi's `modifiedFields` / `modifiedNestedFields` tracking and
  `persistScopedSettings` (re-read current file under lock, merge only modified
  fields/nested keys, re-serialize). Pi guards writes with `proper-lockfile`
  (retrying ~10× with ~20ms backoff on `ELOCKED`); it does **not** use an
  atomic temp-rename or `chmod 0600` for `settings.json`.
- pipy is stdlib-only, so it implements the lock with a sidecar lock file plus
  bounded retry. pipy **may additionally choose** atomic temp-sibling-write +
  `chmod 0600` owner-private replacement, reusing the
  `PromptHistoryStore`/`NativeDefaultsStore` pattern — this is a pipy hardening
  decision, not Pi parity. Lock acquisition must be best-effort and must never
  deadlock a non-interactive run.
- JSON is pretty-printed (2-space) for human editing, matching Pi.

### Schema

Target settings keys (Pi `Settings` interface; pipy uses snake- or
camel-compatible keys — accept Pi's camelCase on read for `.pipy` parity, and
pick one canonical casing for pipy writes, documented in the implementation).
All optional; defaults shown:

Top-level scalars:

- `lastChangelogVersion?: string`
- `defaultProvider?: string`, `defaultModel?: string`
- `defaultThinkingLevel?: "off"|"minimal"|"low"|"medium"|"high"|"xhigh"`
- `transport?: "auto"|"sse"|"websocket"` (default `"auto"`)
- `steeringMode?: "all"|"one-at-a-time"` (default `"one-at-a-time"`)
- `followUpMode?: "all"|"one-at-a-time"` (default `"one-at-a-time"`)
- `theme?: string`
- `hideThinkingBlock?: boolean` (default false)
- `shellPath?: string`, `shellCommandPrefix?: string`,
  `npmCommand?: string[]` (pipy: rename/remap to a Python-relevant equivalent or
  keep inert; document the decision — pipy has no npm install path today)
- `quietStartup?: boolean` (default false)
- `collapseChangelog?: boolean` (default false)
- `enableInstallTelemetry?: boolean` (Pi default true; **pipy default false** —
  see Version/Update Checks)
- `packages?: PackageSource[]`, `extensions?: string[]`, `skills?: string[]`,
  `prompts?: string[]`, `themes?: string[]` (resource paths/sources)
- `enableSkillCommands?: boolean` (default true)
- `enabledModels?: string[]` (scoped-model patterns)
- `doubleEscapeAction?: "fork"|"tree"|"none"` (default `"tree"`)
- `treeFilterMode?: "default"|"no-tools"|"user-only"|"labeled-only"|"all"`
- `editorPaddingX?: number` (0..3, default 0)
- `autocompleteMaxVisible?: number` (3..20, default 5)
- `showHardwareCursor?: boolean` (default from `PI_HARDWARE_CURSOR` →
  pipy `PIPY_HARDWARE_CURSOR`)
- `sessionDir?: string`
- `httpIdleTimeoutMs?: number` (0 disables; default = pipy HTTP default)

Nested objects (top-level keys are shallow-merged one level; deeper objects such
as `retry.provider` are replaced wholesale):

- `compaction?: { enabled?: boolean=true; reserveTokens?: number=16384;
  keepRecentTokens?: number=20000 }`
- `branchSummary?: { reserveTokens?: number=16384; skipPrompt?: boolean=false }`
- `retry?: { enabled?: boolean=true; maxRetries?: number=3;
  baseDelayMs?: number=2000; provider?: { timeoutMs?; maxRetries?;
  maxRetryDelayMs?: number=60000 } }`
- `terminal?: { showImages?: boolean=true; imageWidthCells?: number=60;
  clearOnShrink?: boolean (env `PIPY_CLEAR_ON_SHRINK`=1); showTerminalProgress?:
  boolean=false }`
- `images?: { autoResize?: boolean=true; blockImages?: boolean=false }`
- `thinkingBudgets?: { minimal?; low?; medium?; high? }`
- `markdown?: { codeBlockIndent?: string="  " }`
- `warnings?: { anthropicExtraUsage?: boolean=true }`

`PackageSource` = a string (load all resources from the source) or
`{ source: string; extensions?: string[]; skills?: string[]; prompts?: string[];
themes?: string[] }` to filter which resources load.

pipy may stage which keys are *honored* (e.g. `npmCommand` is inert until a
package story exists), but the loader must **accept and round-trip** unknown and
not-yet-honored keys without dropping them, so forward/Pi-written config is
preserved. The getter/setter surface should mirror Pi's typed accessors
(`get_compaction_enabled`, `set_theme`, etc.) so callers do not reach into raw
dicts.

## Keybindings.json — Schema, Bindings, Defaults, `/hotkeys`

### Schema

`keybindings.json` lives at `<config>/keybindings.json`. It is a flat JSON
object mapping an action id to either a single key spec or an array of
**alternative** key specs, exactly like Pi's `KeybindingsConfig`:

```json
{
  "app.model.cycleForward": "ctrl+p",
  "app.tree.foldOrUp": ["ctrl+left", "alt+left"],
  "app.session.new": []
}
```

Pi does **not** use multi-stroke chords (no "press X then Y" sequences). A key
spec is a single `+`-joined combination of modifiers (`ctrl`, `shift`, `alt`)
plus one key (`escape`, `enter`, `tab`, `up`, `left`, a letter, etc.). An array
value lists **alternative** specs that each trigger the same action (e.g.
`["ctrl+left","alt+left"]` means either combination fires `app.tree.foldOrUp`).
`[]` means the action is intentionally unbound by default. User bindings
override the built-in default for that action; unspecified actions keep their
defaults.

### Defaults (35+ app bindings)

pipy ships the same default app-binding table Pi defines in
`keybindings.ts` `KEYBINDINGS`, layered on the editor/select/input base
(`TUI_KEYBINDINGS` equivalent). The app bindings and their defaults:

- `app.interrupt` = `escape` — cancel/abort
- `app.clear` = `ctrl+c` — clear editor
- `app.exit` = `ctrl+d` — exit when editor empty
- `app.suspend` = `ctrl+z` (none on Windows) — suspend to background
- `app.thinking.cycle` = `shift+tab` — cycle thinking level
- `app.model.cycleForward` = `ctrl+p` — next model
- `app.model.cycleBackward` = `shift+ctrl+p` — previous model
- `app.model.select` = `ctrl+l` — open model selector
- `app.tools.expand` = `ctrl+o` — toggle tool output
- `app.thinking.toggle` = `ctrl+t` — toggle thinking blocks
- `app.session.toggleNamedFilter` = `ctrl+n`
- `app.editor.external` = `ctrl+g` — open external editor
- `app.message.followUp` = `alt+enter` — queue follow-up
- `app.message.dequeue` = `alt+up` — restore queued messages
- `app.clipboard.pasteImage` = `ctrl+v` (`alt+v` on Windows)
- `app.session.new` = `[]`, `app.session.tree` = `[]`,
  `app.session.fork` = `[]`, `app.session.resume` = `[]`
- `app.tree.foldOrUp` = `["ctrl+left","alt+left"]`
- `app.tree.unfoldOrDown` = `["ctrl+right","alt+right"]`
- `app.tree.editLabel` = `shift+l`
- `app.tree.toggleLabelTimestamp` = `shift+t`
- `app.session.togglePath` = `ctrl+p`
- `app.session.toggleSort` = `ctrl+s`
- `app.session.rename` = `ctrl+r`
- `app.session.delete` = `ctrl+d`
- `app.session.deleteNoninvasive` = `ctrl+backspace`
- `app.models.save` = `ctrl+s`, `app.models.enableAll` = `ctrl+a`,
  `app.models.clearAll` = `ctrl+x`, `app.models.toggleProvider` = `ctrl+p`,
  `app.models.reorderUp` = `alt+up`, `app.models.reorderDown` = `alt+down`
- `app.tree.filter.default` = `ctrl+d`, `.noTools` = `ctrl+t`,
  `.userOnly` = `ctrl+u`, `.labeledOnly` = `ctrl+l`, `.all` = `ctrl+a`,
  `.cycleForward` = `ctrl+o`, `.cycleBackward` = `shift+ctrl+o`

Several bindings deliberately reuse the same key spec (e.g. `ctrl+p`) because
they are scoped to different active contexts (editor vs tree selector vs model
selector vs session picker). The keybinding manager must be context-scoped, not
a single global table — matching Pi, where the same key spec resolves to
different actions per focused component.

pipy may bind only the actions for the surfaces it has implemented (its product
TUI already implements a subset). The full table is the target; an action whose
surface does not exist yet should still be present with its default so that
`/hotkeys` and future surfaces are consistent, even if pressing it is currently
a no-op.

### Migration and ordering

On load, pipy migrates legacy flat names to the namespaced names (Pi's
`KEYBINDING_NAME_MIGRATIONS`, e.g. `cursorUp` → `tui.editor.cursorUp`,
`newSession` → `app.session.new`). If both the legacy and new name exist, the
new name wins and the legacy is dropped. The resolved config is re-ordered to
match the canonical `KEYBINDINGS` key order, with unknown extras appended
sorted. This name-migration and re-ordering is applied **in memory only**; Pi
does not write the migrated/reordered result back to `keybindings.json`, and
neither should pipy. A malformed `keybindings.json` (parse error or non-object)
loads as `{}` and falls back to the built-in defaults without crashing,
matching `loadRawConfig` returning `undefined`. On `/reload`, a now-malformed
file likewise loads as `{}` and falls back to defaults — it does not keep the
previously-loaded user bindings.

### `/hotkeys`

`/hotkeys` renders a grouped table (Navigation / Editing / Other / App) built
from the **resolved** bindings, so user overrides are reflected. Pi groups
editor navigation, editing, and app actions and prints display strings via a
`keyDisplayText` helper (e.g. `Ctrl+P`, `Shift+Tab`). pipy reproduces the same
grouped table for the actions it supports, sourced from the live keybinding
manager (never a hardcoded string). The command runs no provider turn.

## Scoped Models

`enabledModels: string[]` holds model patterns (same format as a future
`--models` CLI flag) that constrain the Ctrl+P cycle set. Pi exposes a
`/scoped-models` selector (`ScopedModelsSelectorComponent`) with bindings
`app.models.save`/`enableAll`/`clearAll`/`toggleProvider`/`reorderUp`/
`reorderDown` to choose and order the cycling set; `session.scopedModels` then
drives both the startup banner ("scoped models: …") and the
`app.model.cycleForward`/`cycleBackward` behavior. When no scoped models are
set, cycling uses the full available catalog.

pipy target: persist `enabledModels` in settings (global by default; project
override allowed), expose a `/scoped-models` selector built from
`NativeReplProviderState.model_options()` (reusing the existing availability +
tool-call gating), and have `app.model.cycleForward`/`cycleBackward` cycle the
scoped set (or full catalog when empty). Selection runs no provider turn; a
successful change rebinds the live provider exactly like the existing `/model`
selector.

Shipped: the tool-loop `/scoped-models` command views the patterns and the
resolved cycle set (bare), sets them (`/scoped-models <pattern>…`, persisted to
settings), clears them (`/scoped-models clear`), and cycles
(`/scoped-models next` / `prev`). The pattern→reference match is exact or an
fnmatch glob (`openai/*`); see `pipy_harness.native.scoped_models`. The default
`app.model.cycleForward` binding (Ctrl+P) cycles forward live in the product
TUI through the same `select_model` rebind boundary (no provider turn);
backward cycling is available via `/scoped-models prev` (most terminals cannot
send a distinct `shift+ctrl+p`).

## Message Delivery and Transport

- `steeringMode` (`all` | `one-at-a-time`): how queued steering messages are
  delivered during an active turn. Default `one-at-a-time`. Migrated from the
  legacy `queueMode` key (Pi `migrateSettings`).
- `followUpMode` (`all` | `one-at-a-time`): how queued follow-up messages
  (`app.message.followUp` / `app.message.dequeue`) are delivered. Default
  `one-at-a-time`.
- `transport` (`auto` | `sse` | `websocket`): provider transport selection.
  Default `auto`. Migrated from a legacy `websockets: boolean`
  (`true`→`websocket`, `false`→`sse`).
- `httpIdleTimeoutMs`: HTTP header/body idle timeout in ms; `0` disables it;
  invalid values raise a clear error (Pi `getHttpIdleTimeoutMs`).

pipy honors these where its runtime has the matching surface. Where pipy has no
matching surface yet (e.g. websocket transport, queued steering during a turn —
backlog notes steering/follow-up queuing is still open), the setting must be
accepted, round-tripped, and reported by `/settings`, even if currently inert,
so config is forward-compatible and Pi-written files do not lose data.

## Compaction, Retry, and Branch-Summary Settings

- Compaction: `compaction.enabled` (default true), `reserveTokens` (16384),
  `keepRecentTokens` (20000). These feed pipy's `/compact` and the automatic
  compaction threshold; `session-tree.md` already references
  `branchSummary.reserveTokens`/`skipPrompt`, and this track makes the
  compaction settings live too.
- Retry: `retry.enabled` (true), `maxRetries` (3), `baseDelayMs` (2000,
  exponential backoff 2s/4s/8s), and `retry.provider.{timeoutMs, maxRetries,
  maxRetryDelayMs(60000)}`. These feed pipy's provider HTTP retry policy
  (`pipy_harness.native.retry`). Migrated legacy `retry.maxDelayMs` →
  `retry.provider.maxRetryDelayMs` (Pi `migrateSettings`).
- Branch summary: `branchSummary.reserveTokens` (16384), `skipPrompt` (false).
  Related to the `/tree` branch-summary flow in `session-tree.md`.

### Current honoring status (shipped)

These keys are all accepted, round-tripped, and reported by `/settings`. What is
actively *honored* today vs. accepted-and-reported-but-inert (because pipy's
runtime has no matching surface yet) is:

- **Honored:**
  - `retry.{enabled,maxRetries,baseDelayMs}` and
    `retry.provider.maxRetryDelayMs` — mapped onto the provider HTTP
    `RetryPolicy` via `settings.retry_policy_from_settings` and applied to the
    retry-aware native provider(s) (openai-codex) at REPL startup. `maxRetries`
    n → `max_attempts = n + 1`; ms→s; `enabled=false` → a single attempt;
    values are clamped to the `RetryPolicy` bounds.
  - `compaction.enabled` — gates pipy's automatic tool-loop compaction
    threshold (and `/compact` remains available regardless).
- **Accepted + round-tripped + reported, currently inert** (no matching pipy
  surface; preserved so Pi-written/forward config survives):
  - `compaction.reserveTokens` / `keepRecentTokens` — pipy's compaction is
    user-turn/exchange-count based, not token-budget based, so these token knobs
    are not yet consumed.
  - `branchSummary.reserveTokens` / `skipPrompt` — the `/tree` branch-summary
    attaches parent summaries by a different mechanism than a token reserve.
  - `transport` (no websocket transport surface), `steeringMode` / `followUpMode`
    (no in-turn steering/follow-up queue yet — see backlog), and
    `retry.provider.{timeoutMs,maxRetries}` (the shared `RetryPolicy` models a
    single attempts/delay policy, not separate provider-vs-app retry counts).
  These are surfaced by `/settings` so the user can see the effective value.

## System-Prompt Replace/Append Files

CLI inputs (Pi `cli/args.ts` + `core/resource-loader.ts` `resolvePromptInput`
+ `system-prompt.ts`). Pi resolves both flags through `resolvePromptInput`,
which treats the value as a **file path when it names an existing file**
(reading its contents) and otherwise as literal text:

- `--system-prompt <text-or-file>`: replaces the default system prompt entirely
  (Pi `buildSystemPrompt` `customPrompt` branch). The value is text **or a file
  path** (resolved by `resolvePromptInput`). Project context files and the
  skills section are still appended after the custom prompt, and the date/cwd
  footer is still added last.
- `--append-system-prompt <text-or-file>`: appends to the system prompt.
  Repeatable (accumulates into a list, joined). Each value is text **or a file
  path** (resolved by `resolvePromptInput`). The appended section is added after
  the base/custom prompt and before context files.

Auto system-prompt file discovery (Pi `resource-loader.ts`), independent of the
flags — pipy mirrors these with its `.pipy/`/config-home equivalents:

- Replace files: project `.pi/SYSTEM.md`, then global `~/.pi/agent/SYSTEM.md`.
  pipy equivalents: `.pipy/SYSTEM.md` (project) and `<config>/SYSTEM.md`
  (global). When present, these replace the default system prompt the same way
  `--system-prompt` does.
- Append files: project `.pi/APPEND_SYSTEM.md`, then global
  `~/.pi/agent/APPEND_SYSTEM.md`. pipy equivalents: `.pipy/APPEND_SYSTEM.md`
  and `<config>/APPEND_SYSTEM.md`. When present, these append to the system
  prompt the same way `--append-system-prompt` does.

Read behavior matches Pi exactly: reads are **unbounded** (`readFileSync` —
there is no byte cap on these inputs). If a value names an existing file that
**cannot be read**, Pi warns and **falls back to treating the literal input
string as the prompt text** (it does not fail closed). pipy mirrors this
warn-and-fall-back-to-literal behavior; it does not impose the context-file byte
caps on these inputs.

These compose with the existing `workspace_context` discovery: replace/append
is applied, then `<project_context>` instruction blocks, then skills, then
date/cwd. pipy records only safe metadata about which system-prompt inputs were
used (source label, sha256, byte length), never the prompt body, consistent with
the existing `workspace_instruction_files` metadata posture (this is the one
place the secrets-and-bodies-out rule keeps applying, because system-prompt text
can carry project content).

## Resource Enablement Toggles and `pi config` Equivalent

Pi's `pi config` opens a TUI (`config-selector.ts`) that lists every discovered
resource grouped by origin and scope:

- groups: `User (~/.pi/agent/)`, `Project (.pi/)`, package sources, and
  settings-declared paths;
- resource types per group: Extensions, Skills, Prompts, Themes;
- each row is an enable/disable toggle. Disabling does **not** remove the
  resource path from the settings array. Pi instead writes enable/disable
  **pattern entries** into the relevant settings array — a `-pattern` entry to
  disable a resource and a `+pattern` entry to (re-)enable one — and uses the
  per-source filter fields on a `PackageSource` object for package resources.
  The original discovered paths remain; the `-`/`+` patterns and package
  filters control what is actually loaded.

pipy target:

- A `pipy config` CLI subcommand (and/or a `/config` slash surface) that lists
  pipy's discoverable resources — the bounded Markdown skills (`.pipy/skills`),
  prompt templates (`.pipy/templates`), custom commands (`.pipy/commands`),
  themes, and (once the extension loader lands) extensions/packages — grouped by
  user/project/package scope, with enable/disable toggles.
- Enablement is persisted by writing `-pattern` / `+pattern` entries into the
  relevant settings arrays (`skills`, `prompts`, `themes`, `extensions`) and
  per-source filters on `packages` `PackageSource` objects — mirroring Pi rather
  than deleting discovered paths — at the chosen scope (global or project). The
  existing `pipy_harness.native.resources` loader applies those enable/disable
  patterns and package filters when deciding what to register.
- `enableSkillCommands` (default true) gates registering skills as
  `/skill:<name>` commands.
- A non-interactive form (flags / `--json`) so the conformance gate can assert
  enable/disable without a TTY.

The first milestone may implement the persisted toggles + non-interactive form
and defer the full interactive TUI selector, as long as the same settings keys
drive what `resources.py` loads.

Shipped: the non-interactive `pipy config` subcommand is implemented.
`pipy config list [--json] [--cwd PATH]` reports the discovered skills/prompts
and their resolved enabled state; `pipy config enable|disable <skill|prompt|
theme|extension> <name> [--scope global|project]` writes a `+name`/`-name` entry
into the relevant settings array (via
`pipy_harness.native.resource_enablement`), never removing the discovered path.
`WorkspaceResources.with_enablement` applies those directives at session startup
so a disabled skill/prompt is dropped from what is registered (last matching
directive wins; bare source-path entries are ignored for enablement and remain
for the extension/distribution track). `enableSkillCommands=false` drops all
skills from registration. The interactive `pi config`-style TUI selector and
package/`PackageSource` per-source filters remain on the extension/distribution
track.

## Context-File Discovery Toggles

- `--no-context-files` / `-nc`: disable `AGENTS.md` / `CLAUDE.md` discovery and
  loading for the run (Pi `args.ts` → `main.ts` `noContextFiles`). pipy wires
  this into `workspace_context.discover_workspace_instructions` so no instruction
  files are read, none are injected into the system prompt, and no
  `workspace_instruction_files` metadata is recorded for the run.
- A persisted settings equivalent may be added later
  (`contextFiles.enabled`), but the CLI flag is the parity-required surface.

## `/reload`

`/reload` reloads keybindings, extensions, skills, prompts, and themes without
restarting (Pi `handleReloadCommand`). Target behavior:

- Refuse while a provider turn is streaming or a compaction is running; show a
  safe warning and no-op.
- Show a transient "Reloading …" indicator in the live region.
- Re-read settings (both scopes, re-running migration and deep-merge),
  re-read `keybindings.json` (`KeybindingsManager.reload()` equivalent), re-run
  resource discovery (`resources.py`), re-resolve the theme, and re-apply
  derived UI settings (editor padding, autocomplete max-visible, hardware
  cursor, clear-on-shrink, HTTP idle timeout).
- On a settings or theme load error, surface a safe diagnostic and keep the
  prior good state for that scope; do not crash. Keybindings are different: a
  malformed `keybindings.json` reloads as `{}` and falls back to the built-in
  defaults (it does not retain the previously-loaded user bindings), matching
  Pi's `KeybindingsManager.reload()` → `loadFromFile` behavior.
- Run no provider turn. After reload, re-print the loaded-resources banner
  (honoring `quietStartup`) and report "Reloaded …".

Shipped: `/reload` is implemented in both the tool-loop and no-tool REPL paths.
It re-reads settings (both scopes, re-migrating + deep-merging), reloads
`keybindings.json` (malformed → built-in defaults, not the prior bindings),
re-runs resource discovery and re-applies the enablement directives, re-applies
the edited theme (settings is source of truth over the persisted store, so the
resolved theme is re-injected into `PIPY_THEME`), and (tool-loop) re-applies the
derived UI setting `autocompleteMaxVisible` plus the refreshed command menu. A
settings scope that became malformed keeps its prior good state and emits a
"kept prior <scope> settings" diagnostic. It runs no provider turn, re-prints
the startup banner honoring `quietStartup`, and reports "reloaded settings,
keybindings, and resources." `/reload` is dispatched between turns at the input
prompt, so no provider turn or compaction is ever in flight when it runs; the
editor-padding / hardware-cursor / clear-on-shrink / HTTP-idle-timeout settings
listed above are re-read from settings on the next use but have no live
re-application surface yet (they are accepted + reported, per the honoring-status
note in the Compaction/Retry section).

## `/changelog`

`/changelog` prints the full changelog (Pi `handleChangelogCommand` reads
`getChangelogPath()` → `parseChangelog`). The package `CHANGELOG.md` is parsed
newest-first, but `handleChangelogCommand` calls `allEntries.reverse()` before
rendering, so the **explicit `/changelog` command displays entries
oldest-first** as Markdown under a "What's New" header. (This differs from the
startup auto-display below, which shows only the new entries.) pipy ships a
`CHANGELOG.md` in the package and renders the command the same way
(oldest-first), runs no provider turn, and works in the captured-stream fallback
(plain text). Startup behavior (Pi `getChangelogForDisplay`):

- On a fresh session (no prior messages), compare `lastChangelogVersion` against
  the current pipy `VERSION`. On first run, record the version and show nothing.
  On a version bump, show the new entries since `lastChangelogVersion` and update
  the stored version. Skip entirely for resumed/continued sessions.
- `collapseChangelog`: show a condensed "Updated to vX. Use /changelog for full"
  line instead of the full new entries.
- `lastChangelogVersion` is stored in global settings (Pi
  `setLastChangelogVersion`).

Shipped: `pipy_harness.native.changelog` parses the repo/package `CHANGELOG.md`
(`read_changelog_entries`), `/changelog` renders it oldest-first under "What's
New" in both REPL paths (no provider turn, plain-text in the captured-stream
fallback), and `changelog_startup` implements the startup display — nothing on
first run (records `lastChangelogVersion`) or resumed sessions, the new entries
on a version bump, or a condensed "Updated to vX. Use /changelog…" line under
`collapseChangelog`. The stored version uses the package version from
`pipy_version()`.

## Version/Update Checks

Pi performs a best-effort version check (`utils/version-check.ts`, skipped under
`PI_SKIP_VERSION_CHECK`/`PI_OFFLINE`) and an anonymous install/update ping
(`reportInstallTelemetry` → `https://pi.dev/api/report-install`, gated on
`enableInstallTelemetry` default true and `PI_OFFLINE`).

pipy target (privacy-respecting by default):

- A `--version` / `pipy --version` surface that prints the pipy `VERSION`.
- An **opt-in** update check, gated on `PIPY_SKIP_VERSION_CHECK`/`PIPY_OFFLINE`
  and an `enableInstallTelemetry`/update-check setting whose pipy default is
  **false** (pipy is a local `uv`-driven project, not a published auto-updating
  binary). pipy must perform no network ping by default. This is the one place
  pipy intentionally diverges from Pi's default-on telemetry; everything else
  reaches Pi-equivalent capability.
- No secrets/credentials/identifiers in any check; only a version string when
  the user has explicitly opted in.

Shipped: `pipy --version` / `-v` prints the package version.
`pipy_harness.native.version_check` provides the opt-in gate
(`update_check_enabled` / `resolve_telemetry_enabled`): default off,
`PIPY_TELEMETRY` overrides the `enableInstallTelemetry` setting (pipy default
`false`), and `PIPY_OFFLINE` / `PIPY_SKIP_VERSION_CHECK` force it off. The module
contains **no network code** — a default run performs no ping; there is nothing
to send unless a future opt-in check is added behind this gate.

## Settings Migration

Reproduce Pi's `migrateSettings` on every load (and re-apply on write so old
files are normalized):

Three distinct deletion behaviors apply — do not generalize one to another:

- Rename keys (`queueMode` → `steeringMode`, `websockets: boolean` →
  `transport` with `true`→`websocket`/`false`→`sse`): Pi applies the rename
  **only when the replacement key is absent**, and deletes the legacy key only
  in that case. If the new key already exists, the legacy key is **left
  untouched** (the new value wins on read and the legacy key may remain in the
  file). pipy mirrors this.
- `skills` object form `{ enableSkillCommands, customDirectories }`: this is
  **not** like the rename keys. Whenever `skills` is an object, Pi **always**
  replaces it — with the `customDirectories` array when that array is present
  and non-empty, otherwise deleting the `skills` key — regardless of any other
  field. Only the `enableSkillCommands` **hoist** is conditional: the value is
  copied to the top-level `enableSkillCommands` only when a top-level
  `enableSkillCommands` does not already exist. So an object `skills` never
  survives migration even if the top-level `enableSkillCommands` is already set.
  pipy mirrors this.
- `retry.maxDelayMs` → `retry.provider.maxRetryDelayMs`: this is also **not**
  like the rename keys. Whenever `retry` is an object, Pi **always deletes**
  `retry.maxDelayMs` — unconditionally, even if `retry.provider.maxRetryDelayMs`
  already exists. The replacement-absent check only governs whether the legacy
  value is **copied into** `retry.provider.maxRetryDelayMs`; the legacy key is
  removed regardless. So `retry.maxDelayMs` never survives migration. pipy
  mirrors this unconditional deletion.

pipy-specific migrations: import the existing local-state values into settings
the first time the settings system runs, without breaking the runtime-state
files:

- `NativeDefaultsStore` provider/model → `defaultProvider`/`defaultModel`
  (settings becomes the source of truth; the state file may remain as a cache).
- `NativeThemeStore`/`PIPY_THEME` → `theme`.
- `PromptHistoryStore` enabled flag → a `promptHistory.enabled` setting (or an
  agreed key), so the `/settings` toggle reads/writes settings.

Keybinding name migration is handled separately by the keybindings loader
(above). Migration must be idempotent and must never drop unknown keys.

## Related Pi Surfaces Required for Full Parity

These Pi surfaces are part of the settings/config story but live outside
`settings.json`. They are required for full parity and pipy must provide
equivalents (here or by cross-reference):

- Environment overrides:
  - `PI_CODING_AGENT_DIR` (Pi `config.ts`) overrides the global agent config
    directory (`getAgentDir()`). pipy already has the equivalent override chain
    for its global config home — `PIPY_CONFIG_HOME` → `${XDG_CONFIG_HOME}/pipy`
    → `~/.config/pipy` — which is the pipy analogue of `PI_CODING_AGENT_DIR`.
  - `PI_TELEMETRY` (Pi `core/telemetry.ts`) overrides install telemetry when set
    to `1`/`true`/`yes` or `0`/`false`/`no`, taking precedence over the
    `enableInstallTelemetry` setting. pipy's analogue is `PIPY_TELEMETRY` with
    the same accepted values; combined with `PIPY_OFFLINE`/
    `PIPY_SKIP_VERSION_CHECK` and the default-off `enableInstallTelemetry`
    setting (see Version/Update Checks).
- Auto system-prompt files: `.pi/SYSTEM.md` / `~/.pi/agent/SYSTEM.md` (replace)
  and `.pi/APPEND_SYSTEM.md` / `~/.pi/agent/APPEND_SYSTEM.md` (append), with
  pipy equivalents under `.pipy/` and `<config>/`. These are specified in
  System-Prompt Replace/Append Files above and are part of the parity surface,
  not just the CLI flags.
- Custom provider/model configuration is **not** part of `settings.json`. Pi
  keeps custom providers/models, overrides, and per-model request auth in a
  separate `models.json` (Pi `core/model-registry.ts`,
  `getModelsJsonPath() = join(getAgentDir(), "models.json")`). pipy's
  provider/model catalog and any custom-provider configuration are tracked
  separately — see [provider-catalog.md](provider-catalog.md) — and are out of
  scope for this settings/config/keybindings track.

## Invariants

These hold throughout the track, not as later deferrals:

- pipy-owned Python boundaries. Not a TypeScript port; not the `pi-tui`
  keybindings library. Mirror Pi's vocabulary and behavior, implement with
  dataclasses, stdlib JSON, and pipy's existing store patterns.
- Stdlib-only. No new runtime dependencies (no pydantic, jsonschema, attrs,
  `proper-lockfile`). JSON parse/serialize, manual dict validation, and a
  stdlib advisory lock only.
- Reuse existing local-state store patterns (`NativeDefaultsStore`,
  `PromptHistoryStore`) for owner-only dirs and env-overridable paths. Pi guards
  `settings.json` writes with a lockfile only; any atomic temp-write +
  `chmod 0600` + replace for `settings.json` is a pipy hardening choice, not Pi
  parity.
- Config home = `PIPY_CONFIG_HOME` → `${XDG_CONFIG_HOME}/pipy` →
  `~/.config/pipy` for global; `.pipy/settings.json` for project (read and
  write). pipy does not read `.claude/settings.json` for settings (Pi reads only
  `.pi/`). Reuse the exact global-config-home chain already used by
  `workspace_context` and `extension-api.md`.
- Deep-merge precedence: global < `.pipy` project < CLI/env. Top-level nested
  objects are shallow-merged one level (matching Pi `deepMergeSettings`); deeper
  nested objects (e.g. `retry.provider`), top-level scalars, and arrays replace
  wholesale.
- Field-scoped, lock-guarded writes that preserve unknown keys and only rewrite
  modified fields/nested keys (atomic temp-replace + `chmod 0600` is an optional
  pipy hardening choice, not Pi parity). A scope with a parse/load error is never
  written back over.
- Forward compatibility: unknown and not-yet-honored keys are accepted and
  round-tripped, never dropped, so Pi-written and future config survive.
- No "metadata-first privacy" restriction on settings/keybindings/scoped-models/
  transport/resource-enablement: these reach full Pi-equivalent capability and
  are ordinary local config.
- Secrets/tokens stay out: no API keys or OAuth credentials are written to any
  settings file or session archive; auth stays in the dedicated auth stores.
  System-prompt bodies and context-file bodies remain out of the archive
  (record only safe source/sha/length metadata), because they can carry project
  content.
- `/reload`, `/changelog`, `/hotkeys`, `/scoped-models`, and `pipy config` run
  no provider turn and no model-visible tool call, and degrade to safe
  diagnostics in the captured-stream / non-TTY fallback.
- Update/telemetry network access is opt-in and off by default; default runs
  perform no network ping.
- Each slice ships focused tests, a green `just check`, updated docs, a
  conventional commit, and stops for review.

## Implementation Milestones (reviewed slices)

The track may land in reviewed milestones; the objective is full
Pi-equivalent capability. Work is complete only when the conformance gate
passes.

1. Docs only (this file) plus backlog/pi-parity links. No runtime behavior.
2. Settings core: `pipy_harness.native.settings` with a `Settings` value object
   (typed getters/setters), the deep-merge + precedence resolver, the
   migration pass (Pi migrations + pipy local-state imports), field-scoped
   lock-guarded writes with a stdlib advisory lock (optional `chmod 0600`
   temp-replace as a pipy hardening choice), parse-error isolation, and
   unknown-key round-trip. Discovery for `<config>/settings.json` and
   `.pipy/settings.json` (no `.claude/settings.json`). Focused unit tests for
   precedence, migration, partial-write merge, parse-error fallback, and
   unknown-key preservation. No REPL wiring.
3. Wire settings into startup/runtime: `defaultProvider`/`defaultModel`,
   `theme`, `quietStartup`, `hideThinkingBlock`, editor padding, autocomplete
   max-visible, hardware cursor, clear-on-shrink, `httpIdleTimeoutMs`, and the
   `promptHistory.enabled` toggle now read from settings (state files become a
   cache/back-compat). `/settings` overlay reports the resolved values.
4. Keybindings: `pipy_harness.native.keybindings` with the default `KEYBINDINGS`
   table, `keybindings.json` load (single key spec or array of alternatives),
   in-memory legacy-name migration and canonical ordering (never written back),
   malformed-file fallback to defaults, and a context-scoped resolver.
   Bind the actions the product TUI already implements; keep the rest present
   with defaults. Add `/hotkeys` rendering from the resolved bindings.
5. System-prompt inputs: `--system-prompt` (replace, text-or-file) and
   repeatable `--append-system-prompt` (append, text-or-file), plus the
   `SYSTEM.md` / `APPEND_SYSTEM.md` auto-discovery files, composed into the
   prompt builder around `workspace_context`, with safe-metadata-only recording.
   Read behavior matches Pi: unbounded read, warn-and-fall-back-to-literal on an
   unreadable file (not fail-closed). Add `--no-context-files`/`-nc`.
6. Delivery/transport/compaction/retry/branch-summary settings: accept,
   round-trip, report, and honor where pipy has the surface; document inert
   keys. Wire `retry.*` into the provider HTTP retry policy and `compaction.*`
   into `/compact` and the auto-threshold.
7. Scoped models: persist `enabledModels`, add a `/scoped-models` selector and
   `app.model.cycleForward`/`cycleBackward` cycling over the scoped set.
8. Resource enablement: a non-interactive `pipy config`
   (`--json` + enable/disable flags) that edits the `skills`/`prompts`/`themes`/
   `extensions`/`packages` arrays at a chosen scope and drives what
   `resources.py` registers; `enableSkillCommands` gate. Interactive
   `pi config`-style TUI selector as a later sub-slice.
9. `/reload`: re-read settings + keybindings, re-run resource discovery, re-apply
   derived UI settings, with streaming/compaction guards and error isolation.
10. `/changelog` + version surface: ship `CHANGELOG.md`, render `/changelog`,
    implement startup new-entry display with `lastChangelogVersion` +
    `collapseChangelog`, add `--version`, and add the opt-in (default-off)
    update check.

## Verification Plan

Add one top-level deterministic conformance gate and make it the implementation
source of truth:

```sh
uv run python scripts/parity_checks/settings_config_conformance.py --json
```

The conformance script drives pipy with the deterministic fake provider in a
temporary workspace and temporary config home (`PIPY_CONFIG_HOME` pointed at a
tmp dir, `PIPY_OFFLINE=1`), and fails unless the full settings/config/keybindings
surface works. It must verify:

1. Global `<config>/settings.json` is discovered; a missing project file does
   not fail; effective settings = deep-merge with project precedence.
2. Precedence: global < `.pipy/settings.json` < CLI (no `.claude/settings.json`
   read); top-level nested objects shallow-merge one level (e.g. `compaction`,
   `retry`), while deeper objects (`retry.provider`), scalars, and lists replace
   wholesale.
3. A field-scoped write to one setting preserves other (incl. unknown) keys in
   the file and only rewrites the modified field/nested key.
4. A malformed settings file is isolated to its scope (fallback `{}` + safe
   diagnostic) and is never written back over.
5. Migrations apply with three distinct deletion behaviors. (a) Rename keys
   (`queueMode`→`steeringMode`, `websockets`→`transport`): apply only when the
   replacement key is absent, and a pre-existing replacement key leaves the
   legacy key untouched. (b) `retry.maxDelayMs`→`retry.provider.maxRetryDelayMs`:
   `retry.maxDelayMs` is deleted unconditionally whenever `retry` is an object —
   even if `retry.provider.maxRetryDelayMs` already exists — so the legacy key
   never survives. (c) `skills` object: always replaced with `customDirectories`
   (or deleted when no non-empty `customDirectories`) whenever `skills` is an
   object, regardless of other fields; only the `enableSkillCommands` hoist is
   conditional (skipped when a top-level `enableSkillCommands` already exists),
   so an object `skills` never survives even if `enableSkillCommands` is already
   set. Idempotent; no unknown-key loss.
6. pipy local-state import: existing `NativeDefaultsStore`/`NativeThemeStore`/
   `PromptHistoryStore` values surface through settings without breaking the
   state files.
7. `keybindings.json` loads a single key spec or an array of alternatives,
   applies in-memory legacy-name migration (not written back), falls back to
   defaults (not prior bindings) on malformed input, and `/hotkeys` renders the
   resolved (overridden) bindings, not hardcoded defaults.
8. The 35+ default app bindings are present with their documented defaults.
9. Scoped models: `enabledModels` persists and constrains the model cycle; the
   `/scoped-models` selector runs no provider turn.
10. Delivery/transport/compaction/retry/branch-summary keys are accepted,
    round-tripped, and reported by `/settings`; honored where a surface exists.
11. `--system-prompt` (text or file) replaces the base prompt and
    `--append-system-prompt` (text or file, repeatable) appends, the `SYSTEM.md`
    / `APPEND_SYSTEM.md` auto-discovery files apply, an unreadable file warns and
    falls back to the literal input (not fail-closed), and the result reaches
    `ProviderRequest.system_prompt`; only safe metadata is archived (no body).
12. `--no-context-files` disables AGENTS/CLAUDE discovery and records no
    `workspace_instruction_files` metadata for the run.
13. Resource enablement: disabling a skill/prompt/theme via `pipy config`
    persists a `-pattern` entry (not a path removal) into the right settings
    array and removes it from what `resources.py` registers; re-enabling writes a
    `+pattern` and restores it; `enableSkillCommands=false` stops `/skill:<name>`
    registration.
14. `/reload` re-reads settings + keybindings + resources + theme, re-applies
    derived UI settings, runs no provider turn, refuses while streaming, and
    isolates a load error to its scope.
15. `/changelog` renders the changelog; startup shows new entries on a version
    bump and nothing on first run / resumed sessions; `collapseChangelog`
    condenses; `lastChangelogVersion` persists.
16. `--version` prints `VERSION`; the update check is off by default and makes
    no network request under default/`PIPY_OFFLINE` conditions.
17. No secrets/tokens are written to any settings file or the session archive;
    system-prompt and context-file bodies do not reach the archive.

Focused tests should cover:

- deep-merge precedence and one-level-shallow-merge vs wholesale-replace
  semantics (including `retry.provider` replaced wholesale);
- each Pi migration plus the pipy local-state imports, idempotent and
  key-preserving;
- field-scoped partial write under concurrent on-disk changes (advisory lock +
  re-read-and-merge);
- parse-error isolation per scope and no-write-back-over-error;
- keybindings single/array-of-alternatives parsing, in-memory legacy migration
  and ordering (not written back), malformed-file fallback to defaults, and
  context-scoped resolution of reused key specs;
- `/hotkeys` table built from resolved bindings;
- `--system-prompt`/`--append-system-prompt` and `SYSTEM.md`/`APPEND_SYSTEM.md`
  (text + file + repeat + unbounded read + warn-and-fall-back-to-literal on an
  unreadable file) round-tripping into the provider request, with
  body-not-archived assertions;
- `--no-context-files` suppressing discovery and metadata;
- resource enable/disable round-trip through settings into `resources.py`;
- `/reload` re-application and guards via a real-PTY product-path test;
- `/changelog` startup new-entry logic across first-run / bump / resume and
  `collapseChangelog`;
- version surface and default-off update check (no network).

Before treating implementation as complete, run:

```sh
uv run python scripts/parity_checks/settings_config_conformance.py --json
uv run pytest tests/test_native_settings*.py
uv run pytest tests/test_native_keybindings*.py
uv run pytest tests/test_native_settings_repl*.py
uv run pytest tests/test_native_tool_loop_tui_pty.py -k "settings or reload or hotkeys"
just check
```

Optionally add a Pi comparison smoke (e.g.
`scripts/tmux_settings_config_compare.sh <out-dir>`) to compare user-visible
semantics against the local Pi reference: `/hotkeys` reflects an edited
`keybindings.json`, `/reload` picks up an edited `settings.json`, `/changelog`
renders, and project `settings.json` overrides global. Exact Pi JSON
file-format matching is not the hard gate; deterministic pipy conformance is.

Update `docs/harness-spec.md`, `docs/backlog.md` (Pi Gap Queue item 2 and the
"settings/distribution polish" gap), `docs/pi-parity.md`, `README.md`, and this
spec to match shipped behavior, and get an independent review pass for the
settings-store and keybindings slices.

# UX1 — `/thinking` selector and truthful footer labels (plan)

Status: implemented 2026-09-29. Backlog item: `docs/backlog.md` "### 6. UX1".
Pi reference: `~/src/pi-mono` at `4df157433` (paths relative to
`packages/coding-agent/` unless noted). tau reference: `~/src/tau`.

## What Pi does (source of truth at `4df157433`)

`/thinking` was added in `496185f6e` and later reshaped (`--default` dropped,
fuzzy search input and `app.thinking.save` added). Current behaviour:

1. **Command** (`src/core/slash-commands.ts:23`):
   `{ name: "thinking", description: "Set thinking level", argumentHint: "<level>" }`,
   listed directly after `model`.
2. **Dispatch** (`interactive-mode.ts:3118-3123`): `/thinking` or
   `/thinking <term>`; the editor is cleared.
3. **`handleThinkingCommand(searchTerm)`** (`interactive-mode.ts:5023-5038`):
   - no term → `showThinkingSelector()`;
   - term → case-insensitive exact match against
     `session.getAvailableThinkingLevels()`; no match →
     `showError('Unknown thinking level "<term>". Available levels: a, b, c.')`;
     match → `selectThinkingLevel(level, persist=false)`.
4. **`selectThinkingLevel(level, persist)`** (`:5040-5049`):
   `session.setThinkingLevel(level, { persist })`, footer invalidate, then
   status `Default thinking level: <level>` (persist) or
   `Thinking level: <level>` (session only).
5. **`AgentSession.setThinkingLevel`** (`src/core/agent-session.ts:2523-2546`):
   clamp to available levels; assign `agent.state.thinkingLevel`; when
   `persist`, `settingsManager.setDefaultThinkingLevel(level)` (the requested
   level, even if unchanged); only when the effective level changes, append a
   `thinking_level_change` session entry and emit events. Session-scoped by
   default (`8d1b1178c` docs: model/thinking changes are session-scoped unless
   persisted).
6. **Available levels** (`packages/ai/src/models.ts:1211-1220`
   `getSupportedThinkingLevels`): non-reasoning model → `["off"]`; otherwise
   the extended order filtered by the thinking map (explicit `null` removes,
   `xhigh`/`max` only when mapped). pipy already ports this as
   `thinking.available_thinking_levels` / `ModelRuntime.thinking_levels`.
7. **Selector** (`components/thinking-selector.ts`): bordered list titled
   "Thinking Level", a hint line "`<shift+tab>` cycles thinking levels
   in-session", a fuzzy search input, one row per available level with label
   `✓ <level>` for the current level (`  <level>` otherwise) and description
   from `LEVEL_DESCRIPTIONS` plus ` · default` on the settings default
   (`settingsManager.getDefaultThinkingLevel() ?? DEFAULT_THINKING_LEVEL`),
   preselected current level, footer hint
   "Enter to select · Ctrl+S to set as default · Esc to cancel".
   Enter → session-only select; `app.thinking.save` (default `ctrl+s`,
   `src/core/keybindings.ts:104-107`) → select and persist default; Esc →
   cancel.
   `LEVEL_DESCRIPTIONS`: off "No reasoning", minimal "Very brief reasoning
   (~1k tokens)", low "Light reasoning (~2k tokens)", medium "Moderate
   reasoning (~8k tokens)", high "Deep reasoning (~16k tokens)", xhigh
   "Extra-high reasoning (~32k tokens)", max "Maximum reasoning".
8. **Argument completion** (`interactive-mode.ts:723-736`): fuzzy completion
   over available levels.
9. **Footer label** (`components/footer.ts:219-245`): right side is the model
   id; only when `state.model.reasoning` is true it appends
   ` • thinking off` (level `off`/unset) or ` • <level>`. A non-reasoning model
   shows no thinking segment. There is no hard-coded "high"/"default".
10. **Context meter** (`footer.ts:160`):
    `contextUsage?.contextWindow ?? state.model?.contextWindow ?? 0`.
11. **Timing / tok/s**: Pi has none. `grep` over `packages/*/src` finds
    `tokensPerSecond` only in the faux provider's simulation config
    (`packages/ai/src/providers/faux.ts`); the footer and assistant message
    components render no duration or throughput.
12. **Reactive model preview**: Pi applies `/model` and `/thinking`
    immediately; there is no pending "preview until next turn" state.

## Current pipy (verified against `28a15da`)

- No `/thinking` command. Only `app.thinking.cycle` (Shift+Tab) and the
  `/settings` "cycle thinking level" row. `app.thinking.save` is missing from
  `keybindings.APP_KEYBINDINGS`.
- **Stale provider bug (the "truthful" part).** The interactive
  `ProviderMutationEffects.cycle_thinking_level` (Shift+Tab and `/settings`)
  only assigns `NativeReplProviderState.thinking_level`; it never rebuilds the
  bound provider. Providers bake the mapped effort at construction
  (e.g. `OpenAIResponsesProvider.reasoning_effort`), so after Shift+Tab the
  footer shows the new level while the next request still sends the old
  effort. Probe: cycle → state `low`, `coding_state.provider` is the same
  object with the old `reasoning_effort`. The RPC path
  (`_rpc_set_thinking_level`) already does it right: prepare a provider
  off-lock with `prepare_thinking_mutation`, commit with an expected-state
  check, `coding_state.refresh_provider`, append the tree entry only on change,
  refresh the footer.
- **Footer effort label.** `chrome._ChromeFooterEffects._effort_label` uses the
  live level when present, else `_effort_label_for`, which hard-codes `"high"`
  for `openai-codex/gpt-5*` and `"default"` otherwise. Non-reasoning models
  (e.g. `fake`) show `• default`; a reasoning model with no level would show
  `high` though nothing is sent.
- **Context meter.** Already reads the resolved row's `declared_context_window`
  (MC1), falling back to the built-in row, then a 128k default when there is no
  row at all. Pi's model always has a row; pipy's 128k fallback only covers
  spec-less selections (bare `ds4`). No change in this slice.

## Design

### A. One interactive thinking mutation path

In `repl/provider_selection.py`:

- Rename `_rpc_set_thinking_level(level, commit_if_true_idle)` to
  `_set_thinking_level(level, commit_gate)` (no alias; no-deprecation policy)
  and keep the RPC port calling it with `commit_if_true_idle`.
- Add `set_thinking_level(level) -> RpcConfigurationResult` for the
  interactive loop, calling `_set_thinking_level` with a direct commit gate
  (`commit(); return True`). Interactive slash commands and hotkeys are only
  dispatched from the loop between turns (a slash command submitted mid-turn
  interrupts the turn and runs on the next loop iteration,
  `docs/tui-workflow.md` queued steering), so there is no in-flight run whose
  witness the refreshed binding could invalidate. This is the same direct,
  expected-state-checked commit the interactive `/model`
  (`apply_model_selection` → `_commit_model_mutation(generation_id=None)`)
  already uses.
- Replace the body of the interactive `cycle_thinking_level()` with: snapshot,
  return `None` when the model does not support thinking (unchanged
  behaviour), compute `next_thinking_level(levels, current)` and apply it via
  `set_thinking_level`. It now returns `RpcConfigurationResult | None` (same
  shape as the RPC port's `cycle_thinking_level`), so
  `cycle_thinking_level_action` can report failure separately from
  "unsupported". The provider is rebuilt, fixing the stale-effort bug for
  Shift+Tab and `/settings`.
- **Durable order.** The shared path used to release `mutation_io_lock`
  between the commit and the `thinking_level_change` append and then appended
  a *fresh* snapshot, so a concurrent commit in that gap could make one
  operation append the other's level and drop its own transition. The commit
  now pushes the committed level onto a pending list under
  `mutation_io_lock`; after the commit gate returns, the caller drains that
  list in order under `mutation_io_lock`. The extension path, which appends
  while still holding `mutation_io_lock`, drains the pending list before its
  own append. The RPC `set_model` commit, which can clamp the level on a model
  switch, pushes that clamped level onto the same list when it differs from
  the expected level and drains it the same way (replacing its old post-gate
  snapshot append). Every committed transition is therefore appended once, in
  commit order, without file I/O under the queue gate or the session mutex.
  The result snapshot carries the committed level, not a later live read.
  (The interactive `/model` path appends no thinking entry on a clamp today;
  that pre-existing gap vs Pi's `setModel` → `setThinkingLevel` is recorded as
  a follow-on, not changed here.)
- The extension `set_thinking_level` path is **not** otherwise changed: it can run
  mid-turn (tool hooks), where refreshing the binding would trip the run
  witness (`CodingContextChangedError`). Pi applies a mid-run change on the
  next request because the agent loop reads `state.thinkingLevel` per request;
  pipy would need per-request effort resolution. Recorded as a follow-on.

### B. `/thinking` command

- `CodingCommandAction.THINKING = "thinking"`; registry spec
  `BuiltinCommandSpec("/thinking", ACTION, OPTIONAL_ARG, THINKING,
  description="Set thinking level")`; add to `_ARGUMENT_ACTIONS`, to the
  provider/configuration action set in `command_router.py`, to the TUI slash
  completion list and REPL description list directly after `/model` (Pi
  order), and to the not-handled notice.
- `ProviderConfigurationCommandEffects._thinking` (new handler):
  - Non-native state → diagnostic `pipy: /thinking is unavailable for this REPL
    provider state.`
  - Argument → Pi's case-insensitive exact match against
    `state.current_thinking_levels()`; miss → `pipy: Unknown thinking level
    "<arg>". Available levels: a, b.`; hit → `set_thinking_level`, status
    `pipy: thinking level: <level>`.
  - No argument with a TUI → thinking selector (C). No argument without a TUI
    → print the current level and the available levels (pipy-only plain REPL
    fallback; Pi has no non-interactive `/thinking`).
- Ctrl+S / persist: apply the session change first; only when it succeeds,
  `settings.set_value("defaultThinkingLevel", level)` (global scope, the
  level requested) and status `pipy: default thinking level: <level>`. A
  failed session change writes nothing; a read-only settings failure is
  reported after the session change has applied.

### C. Thinking selector overlay

Reuse the provider/model selector overlay rather than adding a new component:

- `OverlayState.begin_model(..., hint=None)` stores `model_hint`; the title row
  renders `" {title} — {hint}"` with the existing hint as the default.
- `ModelSelectorComponent(..., save_keys=())`: a key matching `save_keys`
  (via `matches_key_specs`) closes with `ModelSelectorClose(index,
  save=True)`.
- `TerminalModalDriver.run_thinking_selector(options, *, current_index)`
  returns `(index, save) | None`, using `resolved_key_specs("app.thinking.save",
  …)` for the save key and title `Thinking Level`, hint
  `↑/↓ move · enter select · <save> set as default · esc cancel`.
- No fuzzy search input: pipy's selector overlays (`/model`,
  `/scoped-models`) have no search/filter path, and the thinking list holds at
  most seven rows. Intentional deviation, recorded as a follow-on together with
  Pi's `/thinking` argument completion (pipy has no slash-argument completion
  framework yet).
- Rows: `✓ <level>` / `  <level>`, padded, then Pi's description, plus
  ` · default` for the settings default
  (`get_default_thinking_level() or DEFAULT_THINKING_LEVEL`). Current level
  preselected.
- Add `"app.thinking.save": _kb("ctrl+s", "Save thinking level")` after
  `app.thinking.cycle` (Pi order), and add it to
  `ui/key_specs.USER_KEYBINDING_ACTIONS` so a `keybindings.json` remap is
  honoured by the selector like Pi's `getKeybindings().matches`; test a
  remapped save key.

### D. Truthful footer effort label

- Delete `_effort_label_for`. `_ChromeFooterEffects._effort_label` returns:
  - `""` when the model is not a reasoning model (resolved spec
    `reasoning` false, or no resolvable spec) — no ` • …` segment;
  - `"thinking off"` when reasoning and level is `None`/`off`;
  - the level otherwise.
  For a `StaticNativeReplProviderState` the built-in row's `reasoning` decides
  and the level is unknown → `thinking off` for a reasoning row (Pi's
  `state.thinkingLevel || "off"`).
- `format_bottom_status_line` omits ` • ` when `effort_label` is empty.

### E. Timing / tok/s and tau reactive preview (not adopted)

Pi has neither (items 11–12). tau `7b96883` adds per-response TTFT/duration
and branch TPS in its sidebar; tau `15ca794` previews model/thinking choices
until the next turn. Adopting either would add chrome Pi does not have, so both
are recorded as follow-ons in the backlog, not implemented.

## Tests (done-when)

1. Registry classifies `/thinking` and `/thinking high` to `THINKING` with the
   argument; completion/description lists include `/thinking` after `/model`.
2. `ProviderMutationEffects.set_thinking_level("high")` on openai gpt-5.5
   rebinds `coding_state.provider` with the new mapped effort, retains history
   and usage, appends one `thinking_level_change`; repeating the same level
   appends nothing.
3. Interactive `cycle_thinking_level()` rebuilds the provider (regression for
   the stale-effort bug); non-reasoning model returns `None`.
4. `/thinking <level>` handler: case-insensitive match; unknown level error
   text lists available levels; unavailable level for the model (e.g. `max` on
   a model without it) is rejected with the same error.
5. Selector: rows/labels/default marker/preselect; Enter → session-only (no
   settings write); Ctrl+S → settings `defaultThinkingLevel` written and level
   applied; Esc → no change.
6. Footer: non-reasoning (`fake`) has no ` • ` segment; reasoning with level
   `off` shows `• thinking off`; reasoning with `high` shows `• high`;
   `_effort_label_for` is gone.
7. `app.thinking.save` present with default `ctrl+s` (Pi's `/hotkeys` does
   not list it, so neither does pipy's).
10. Concurrency: a thinking commit whose append is delayed while a second
    thinking change (and, separately, an RPC model switch that clamps the
    level) commits still appends both levels in commit order.
8. Real PTY (tmux) run with the fake provider selected explicitly, with an
   isolated config whose `models.json` marks the fake row reasoning-capable
   (the built-in fake row is non-reasoning, whose selector offers only `off`
   and whose footer has no thinking segment — also checked): `/thinking` opens
   the selector with the model's levels, Enter changes the footer segment,
   Ctrl+S writes `defaultThinkingLevel` to the isolated settings file; no
   provider turn runs. If the fake row cannot be made reasoning-capable through
   `models.json`, the footer-change assertion moves to a unit test and the PTY
   run covers selector rendering and the settings write only.
9. `just check` green.

## Docs

`CHANGELOG.md` (Added `/thinking`, `app.thinking.save`; Fixed Shift+Tab stale
effort and footer label), `docs/usage.md` command table, `docs/keybindings.md`
and `docs/settings-config.md` keybinding list, `docs/tui-workflow.md`
thinking section, `docs/pi-parity.md` if it lists the command set,
`docs/backlog.md` UX1 marked done with deviations/follow-ons.

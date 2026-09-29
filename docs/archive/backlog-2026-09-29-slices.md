<!-- Archived from docs/backlog.md at 23fdc99b (2026-09-29). -->

> **Archived.** These are the per-slice done notes of the 2026-09-29 queue
> (MC1, CTX1, PC1, PC2, UX1, MC5, PR1/PR2, COST1 and the DH1 gate repair) as
> they stood in `docs/backlog.md` at `23fdc99b`, kept verbatim for their
> evidence. They are not an active queue; open follow-ons were consolidated
> into [docs/backlog.md](../backlog.md). Paths are relative
> to the repository root.

# Backlog done notes, 2026-09-29

### 1. MC1 — Current models and thinking maps (done 2026-09-29)

Landed via merge of `feat/model-currency` (`23eb706`). Rows, defaults and
thinking maps come from Pi's generator at `4df157433`; plan and deviations in
`docs/specs/2026-09-29-model-currency-plan.md` and `docs/provider-catalog.md`.
Remaining follow-ons, not queued separately yet:

- Live Anthropic 5.x smoke (no credentials during the slice) — part of DF1.
- Legacy rows (Claude 3.5, gpt-5.1-codex, Gemini 2.x) kept; prune later.
- Mid-conversation effort `configuration_update`, OpenAI `summary`/`include`
  on thinking requests, and `/model x:level` clamping remain Pi deviations.
- OpenRouter Claude rows need an Anthropic-Messages-over-OpenRouter transport.

### 2. CTX1 — Pi context-file and skill discovery (done 2026-09-29)

Landed on branch `feat/ctx1-context-discovery`. Context files and skills now
follow Pi `4df157433`. The plan and the deviations are in
`docs/specs/2026-09-29-ctx1-context-discovery-plan.md`, `docs/pi-parity.md` and
`docs/usage.md`.

- Context files use Pi's candidate order (`AGENTS.override.md`, `AGENTS.md`,
  `AGENTS.MD`, `CLAUDE.md`, `CLAUDE.MD`) and are not trust-gated. Pi's
  linked-worktree shadowing applies. `pipy.md`/`PIPY.md` were dropped.
- The prompt uses Pi's `<project_context>` and `<skills>` sections.
- Skills load from `--skill` paths, `.pipy/skills`, project `.agents/skills`
  (cwd up to the git root, trusted only), `<config>/skills`,
  `~/.agents/skills`, then packages. Every root uses Pi's `SKILL.md` layout with
  ignore files. A skill without a description is dropped. `.agents/skills` in
  any ancestor makes a project need trust.

Deviations kept as follow-ons, not queued separately yet:

- Done on `fix/gates-and-skill-symlinks`: skill roots and context files follow
  symlinks like Pi, and the global resource root (skills, templates, commands,
  extensions, `models.json`) uses `~/.pipy` like settings and context.
- Template and command stores still keep the symlink-containment guard (Pi's
  prompt templates follow symlinks).
- A plain `.md` skill without a frontmatter `name` keeps its file stem (Pi uses
  the parent directory name). Package skills come after the auto roots (Pi puts
  them first).
- Not ported: skill `disable-model-invocation`, the bash-only "Use bash to load"
  advertisement, and the rest of Pi's sectioned system prompt (`<tools>`,
  `<rules>`, `<cwd>`).

### 4. PC1 — OpenAI/Codex prompt-cache affinity (done 2026-09-29)

Landed on branch `feat/pc1-prompt-cache-affinity`. Codex, OpenAI Responses and
Azure now follow Pi `4df157433`; the plan is in
`docs/specs/2026-09-29-pc1-prompt-cache-affinity-plan.md` and the user docs in
`docs/providers.md` (OpenAI prompt caching).

- Main turns carry the current session id. Codex sends it, cut to 64
  characters, as `prompt_cache_key` and as the SSE and WebSocket `session-id`
  and `x-client-request-id` headers. Responses adds Pi's affinity headers.
  Azure sends the key only.
- Compaction and branch summaries use retention `none` and a fresh routing id,
  as in Pi. Azure ignores retention, so it still gets that id as its key; the
  backlog's "omit on summary calls" was wrong for Azure.
- `PIPY_CACHE_RETENTION=long` (Pi `PI_CACHE_RETENTION`) drives the Responses
  `prompt_cache_retention`/`prompt_cache_options`. The six OpenAI GPT-5.6/GPT-6
  rows carry Pi's generated `supportsExplicitPromptCacheMode`.
- Live: two `--mode json` turns in one session on openai-codex gpt-6-sol. The
  second turn read 4.4k of 4.5k input tokens from cache (footer `R4.4k
  CH97.7%`); the first had `CH0.0%`.

Deviations kept as follow-ons, not queued separately yet:

- openai-completions `prompt_cache_key` and affinity headers
  (Pi `openai-completions.ts:771-826`).
- Codex WebSocket connection reuse/continuation, and Pi's per-session
  SSE-fallback memory (pipy remembers the fallback per provider instance).
- Routing ids are uuid4 hex, not uuidv7.

### 5. PC2 — Anthropic `cache_control` and retention (done 2026-09-29)

Landed on branch `feat/pc2-anthropic-cache-control`. Anthropic Messages and
Bedrock now follow Pi `4df157433`; the plan is in
`docs/specs/2026-09-29-pc2-anthropic-cache-control-plan.md` and the user docs
in `docs/providers.md` (Anthropic prompt caching).

- Anthropic marks the system block, the last immediate tool and the last block
  of a trailing user turn. Bedrock marks the system block and the last user
  block, but not tools, and only for Pi's cache-capable Claude ids (or with
  `AWS_BEDROCK_FORCE_CACHE=1`).
- Retention reuses PC1's `PIPY_CACHE_RETENTION` and request field. `long`
  sends `ttl: "1h"`; `short` sends no ttl; summaries send `none`. Pi has no
  settings.json key for this, so the backlog's "`cacheRetention` setting" is
  the env var.
- `cache_creation.ephemeral_1h_input_tokens` becomes `cache_write_1h_tokens`,
  priced at 2x input like Pi's `calculateCost`. Pi has no separate 1h price
  in the catalog, so `NativeModelCost` is unchanged.
- models.json compat `supportsLongCacheRetention`,
  `supportsCacheControlOnTools`, `sendSessionAffinityHeaders` and
  `sessionAffinityFormat` resolve per flag as in Pi.
- Neither Pi nor pipy has a Vertex-Anthropic transport.
- Live: skipped. No Anthropic, Bedrock or Vertex credentials were available.
  The evidence is exact request-shape tests and updated golden fixtures.

Deviations kept as follow-ons, not queued separately yet:

- Session cost is priced only from `repl/turn_leaves.py` `PRICING_TABLE`
  (Codex gpt-5). Catalog-cost pricing for every row (Pi `calculateCost`) would
  make the 1h split visible for Claude.
- Pi's native tool changes and `__pi_deferred_placeholder__` keep the tool
  prefix stable when a deferred tool loads. pipy's `tool_reference` path does
  not, so the first activation misses the cache once.
- The Bedrock cache gate reads the model id only (Pi also reads `model.name`).
  Bedrock uses InvokeModel, so Converse cache points become `cache_control`.
- Pi prompt-cache warming (`c596d09d9`). tau's second breakpoint at the
  previous request boundary was not adopted, because Pi does not have it.

### 6. UX1 — `/thinking` selector and truthful footer labels (done 2026-09-29)

Landed on branch `feat/ux1-thinking-selector`. `/thinking` and the footer
thinking segment now follow Pi `4df157433`. The plan is in
`docs/specs/2026-09-29-ux1-thinking-selector-plan.md`; the user docs are in
`docs/tui-workflow.md` (Thinking-Level Hotkeys), `docs/usage.md` and
`docs/keybindings.md`.

- `/thinking <level>` matches the model's available levels case-insensitively
  and applies the level for the session only. A bare `/thinking` opens Pi's
  Thinking Level selector: the current level is marked `✓` and the default
  `· default`. Enter is session-only; `app.thinking.save` (Ctrl+S, rebindable)
  also writes `defaultThinkingLevel`.
- **Fixed a stale-provider bug.** Shift+Tab and the `/settings` cycle only
  assigned the level, so the footer showed the new level while requests kept
  the old effort. `/thinking`, Shift+Tab, `/settings` and RPC now share one
  path that rebuilds the provider with the level. `thinking_level_change`
  entries are appended in commit order, including a level clamped by an RPC
  model switch.
- The footer shows `• <level>`, or `• thinking off`, for a reasoning row and no
  segment for a non-reasoning row. `_effort_label_for` and its hard-coded
  `high`/`default` labels are gone. The context meter was already correct
  after MC1.
- Verified in a real tmux PTY with the fake provider selected explicitly (an
  isolated `models.json` made the fake row reasoning-capable). No provider turn
  ran.

Deviations kept as follow-ons, not queued separately yet:

- No fuzzy search in the selector and no `/thinking` argument completion.
  Pipy's selectors and slash commands have neither yet.
- An extension `setThinkingLevel` still only assigns the level and does not
  rebuild the bound provider. It can run mid-turn, where rebinding would break
  the run witness. Pi reads the level per request; pipy would need per-request
  effort resolution.
- The interactive `/model` path appends no `thinking_level_change` when a
  switch clamps the level (Pi `setModel` → `setThinkingLevel` does).
- Response time and tok/s (tau `7b96883`) are not adopted: Pi's footer and
  messages show no timing. The same goes for tau's reactive model/thinking
  preview (`15ca794`), since Pi applies `/model` and `/thinking` immediately.
  Both stay pipy product decisions for later.
- The context meter still falls back to 128k for a selection with no catalog
  row (bare `ds4`). Pi always has a model row.

### 8. MC5 — Catalog sync and drift check (done 2026-09-29)

Landed on branch `feat/mc5-catalog-sync`. The plan is in
`docs/specs/2026-09-29-mc5-catalog-sync-plan.md` and the user docs in
`docs/provider-catalog.md` (Catalog drift check).

- `just catalog-drift` (`scripts/catalog_drift.py`) reads Pi's generated
  catalog JSON and compares each shared row's values, thinking map and the
  compat keys pipy reads. It also lists pipy-only rows and Pi rows pipy does
  not carry. It is manual and is not part of `just check` or CI.
- `catalog_data.py` stays the curated layer. A generated file would need the
  same overrides: provider renames, pipy adapter families, base-URL
  conventions and legacy rows.
- Intentional differences are listed in `scripts/catalog_drift_allowlist.json`
  as `deviation` entries. Known drift waiting for a refresh is listed as
  `pending`. A stale entry fails the check.
- The first run found drift that MC1 had missed: the Mistral rows, Gemini 2.5
  Pro, OpenRouter GPT-5.1 Codex and `openai-completions/gpt-4.1`. It is fixed,
  so the pending list is empty. Mistral `reasoning_effort` now follows Pi's
  clamp-then-map. models.dev is not read directly: it lacks Pi's generator
  corrections.

Deviations kept as follow-ons, not queued separately yet:

- Mistral rows stay text-only, because the Chat Completions wire has no image
  serialization. A reasoning Mistral row without a map sends the raw level
  where Pi sends `prompt_mode: "reasoning"`.
- Display names are not compared.

### 9. PR1/PR2 — xai and github-copilot providers (done 2026-09-29)

Landed on branch `feat/pr-xai-copilot`. Both providers follow Pi `4df157433`;
the plan is in `docs/specs/2026-09-29-pr1-pr2-xai-copilot-plan.md` and the user
docs in `docs/providers.md` (GitHub Copilot) and `docs/provider-catalog.md`.

- **PR1:** `xai` is registered on the Responses family with `XAI_API_KEY` and
  default `grok-4.7`. It carries Pi's four Grok rows. Requests ask for the
  encrypted reasoning item on every reasoning request, as in Pi.
- **PR2:** `github-copilot` is registered with Pi's 33 rows across Anthropic
  Messages, Responses and Chat Completions, Copilot's editor headers, and
  default `gpt-5.4`. `/login github-copilot` runs Pi's device-code flow,
  including enterprise domains, the model list and policy enabling.
  `COPILOT_GITHUB_TOKEN` also works. Each request uses the token's `proxy-ep`
  endpoint and a token refreshed per request. The adapters add Pi's
  `X-Initiator`/`Openai-Intent`/`Copilot-Vision-Request` headers. Claude rows
  use Bearer auth, and the model lists follow the account's
  `availableModelIds`.
- On the way, the Responses adapter now sends Pi's on-state
  `reasoning.summary: "auto"` and encrypted-reasoning `include` for every
  `openai-responses` row, which closes that MC1 follow-on. The Chat
  Completions default branch honours `compat.supportsReasoningEffort`.
  `just catalog-drift` also compares row headers, and it is CLEAN with both
  providers included.
- Live smoke skipped: no `XAI_API_KEY`, `COPILOT_GITHUB_TOKEN` or stored
  Copilot login was available.

Deviations kept as follow-ons, not queued separately yet:

- Pi's xAI OAuth login (SuperGrok or X Premium) is not ported.
- A refreshed Copilot token is kept in memory, not written back to the auth
  store (Pi persists it under a file lock). The Copilot 429 retry does not read
  `Retry-After`.
- Cost tiers (both providers) are not modeled.

### 10. DH1 — Docs and repo hygiene (S)

- **Done in this rewrite:** this backlog, the archive and a historical marker on
  `docs/pi-mono-gap-audit.md`.
- **Remaining:**
  - Re-baseline the feature notes in `docs/pi-parity.md` when MC1/CTX1 land
    (the CTX1 rows were updated with the slice).
  - Delete the stale remote branches (`feat/catalog-non-completions`,
    `feat/extension-api`, `feat/tui-interaction-comfort`).
  - Review the four local stashes with their owner before dropping any.
- **Done (`fix/gates-and-skill-symlinks`):** seven stale
  `scripts/parity_checks/` gates were repaired against the refactors that broke
  them (`extension_package`, `project_trust`, `extension_chrome_widgets`,
  `session_tree`, `automation_rpc`, `settings_config`, `tui_workflow`). All
  gates pass except the two Pi comparisons, which need pi-mono's generated
  model data (`npm run hydrate-model-data` in `packages/ai`). The gates are
  still run by hand. Wiring them into pytest or CI stays out of scope by owner
  decision.

### 11. COST1 — Catalog-cost pricing for every model (done 2026-09-29)

Landed on branch `feat/cost1-catalog-pricing`. Session cost now follows Pi
`4df157433` `calculateCost`. The plan is in
`docs/specs/2026-09-29-cost1-catalog-pricing-plan.md` and the user docs in
`docs/providers.md` (Session cost).

- `PRICING_TABLE` is gone. Each turn is priced from the resolved row, built-in
  or `models.json` (overrides, custom models, `cost.tiers`). The price covers
  uncached input, output with reasoning inside it, cache reads and writes, and
  1h writes at twice the input rate. Request-wide tiers use the highest
  threshold the prompt exceeds.
- The old table charged cached input at the full rate and reasoning twice.
  Gemini, Chat Completions, Mistral and Responses now report the cache and
  thinking counters Pi prices.
- The footer shows `$0.123`, nothing at zero, and `$0.123 (sub)` on a
  subscription login (Codex, Anthropic or Copilot OAuth, or an extension OAuth
  provider with `is_subscription`). `(api)` is gone. RPC `get_session_stats`
  reports Pi's `tokens` and `cost` instead of zeros.
- Live: a tmux PTY and `--mode rpc` against a local Chat Completions stub
  (isolated `models.json`, 2k uncached, 10k cached, 800 output) showed `$0.021`
  and `cost: 0.021`, as computed. One openai-codex gpt-6-sol turn showed
  `$0.009 (sub)`, which is Pi's rule for a subscription. The fake provider
  reports no usage, so its footer shows no cost, as Pi's does at zero.

Deviations kept as follow-ons, not queued separately yet:

- **USAGE1:** per-message usage in JSON/RPC messages and the session tree,
  resumed-session totals, Pi's `/session` Cost section, and totals that survive
  a model switch (pipy's model switch resets usage by contract).
- Built-in rows carry no tiers until MC5 syncs them. Until then Codex gpt-5.5
  above 272k input is priced at base rates.
- Extension provider rows carry no cost. Pi service-tier pricing and Anthropic
  fallback-model pricing are absent because pipy lacks both features.
- `models.json` rejects negative or non-finite cost numbers at load. Pi's
  schema accepts any number.

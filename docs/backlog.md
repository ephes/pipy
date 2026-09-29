# Pipy backlog

Status: sole active task index, rewritten 2026-09-29.

## Status

- **D0–D8 daily-use program: complete.** It covered semantic compaction (D1),
  the persistent product-session API (D2/D6), model-aware request budgets (D3),
  canonical retry (D4), shared queue and RPC controls (D5), cancellable,
  streamed RPC bash (D7), and cross-provider tool-call replay (D8).
- **Last runtime commit:** `73f4aef` (2026-09-09, D8a1). Only `eb851ce`
  (2026-09-23, review rule) has landed since.
- **Tests:** `uv run --frozen pytest --co` collects 6,192 tests at `eb851ce`.
  The last recorded full gate passed 6,190 tests with two skipped at `73f4aef`.
- **Not verified live.** All D0–D8 evidence comes from deterministic and
  fake-transport tests. Nobody has observed credentialed provider runs, live
  semantic-summary quality, or cross-provider answer quality. DF1 owns that.
- **Model currency: done (MC1, 2026-09-29).** The catalog, provider defaults
  and thinking maps mirror Pi `4df157433`. Codex gpt-6-sol was smoke-tested
  live; Anthropic 5.x is covered by fake-transport tests only.

This document owns direction, order and task IDs. A queue item is a bounded
work order. It does not override a current runtime contract (see
[Contract map](#contract-map)). The per-slice D0–D8 narrative, the contracts
and the review evidence are archived in
[archive/backlog-2026-09.md](archive/backlog-2026-09.md).

## Evidence baseline

The queue below was re-baselined against these checkouts on 2026-09-29:

| Repository | Commit | Notes |
| --- | --- | --- |
| pipy | `eb851ce` | `main`, clean |
| pi-mono | `4df157433` | `packages/coding-agent` 0.87.1 plus Unreleased |
| tau | `15ca794` | latest tag `v0.4.5` |

Pi paths below are relative to `~/src/pi-mono` and tau paths to `~/src/tau`.
Pipy paths are relative to `src/pipy_harness/native/` unless they start with
`docs/`, `tests/` or `scripts/`. Line numbers are as of the baseline.

## Working rules

- **Trunk-based, one writer.** Work happens on `main` with one writer in the
  shared checkout, and each chunk is committed before the next starts
  (`AGENTS.md`). Read-only investigators can run concurrently. They report
  findings and do not edit this index.
- **Checks.** Run focused tests plus `just check`, and run `prek` when
  `.pre-commit-config.yaml` exists. Add `just docs-build` for doc changes and
  the PTY smoke tests for terminal changes. Test fixtures isolate HOME, so do
  not wrap `uv` or `just check` in a temporary HOME.
- **Review.** Use a fresh read-only reviewer that is separate from the
  implementer. The default is **GPT-6 Sol** through the `codex-review-loop` or
  `pi-review-loop` skill. Prefer a different-family reviewer when the change
  needs different-family evidence, and never present a same-family review as
  one. Cycles follow the **diminishing-returns rule** in `AGENTS.md` (Review
  budget, `eb851ce`), which has no fixed round cap:
  - Docs and plans can close on a clean first round.
  - Code continues only while rounds produce new, material findings.
  - Stop on self-inflicted churn.
  - Correctness defects in shared mutable state are exempt from every stopping
    rule.
  - Unresolved Critical/Warning findings block the commit.
  - Advisory findings are never reported as `CLEAN`.
  - The parity-loop workflow keeps its stricter different-family CLEAN gate.
- **Docs travel with behavior.** Each item updates its user docs, its topic
  spec and `CHANGELOG.md` in the same commit.
- **No deprecation shims.** Realign pipy-only surfaces to Pi directly.
- **Privacy is not a reason to diverge from Pi.** The metadata-only workflow
  archive stays separate from the full-content product session tree.

## Active queue

Take items in order unless one is blocked. Effort: S ≈ one focused slice,
M ≈ two or three slices.

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

### 3. DF1 — Live smoke and dogfooding acceptance (S–M)

- **Smoke check first.** Send one credentialed request per new family (codex
  gpt-6-sol, anthropic opus/sonnet 5.5) before claiming support. Fake-transport
  conformance cannot catch wrong IDs or effort values.
- **Then a scripted dogfooding run** that covers:
  - a real repository task and its checks
  - interrupt/steer
  - automatic and manual compaction
  - retry after a provider failure
  - resume/fork after exit
  - cancel
  - a cross-provider switch
- **Record** a short, dated acceptance note. It covers the "Minimum acceptance"
  bar below and is also usable as demo material.

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

### 7. RL1 — Release 0.2.0, talk-minimum (S)

The pyproject is still at `0.1.0`, the repo has no tags, and CHANGELOG
`[Unreleased]` runs from line 7 to line 877.

- Split the CHANGELOG into `0.2.0`.
- Tag the release.
- Make the README install note accurate for a checkout install.

Choosing a PyPI name and adding a SHA-pinned publish workflow can follow.

### 8. MC5 — Catalog sync and drift check (M)

Pi generates its catalog from models.dev (`packages/ai/scripts/generate-models.ts`)
plus a remote overlay (`7fd564cbb`). tau generates from models.dev (`3c6cf16`).
Pipy's `scripts/parity_checks/provider_catalog_conformance.py` checks structure
only.

- Add a sync script that emits pipy's supported API families from models.dev or
  Pi's generator output, keeping `catalog_data.py` as the correction layer.
- Add a `just` check that flags drift from Pi for supported providers.
- `models.json` (`models_json.py`) remains the user override path.

### 9. PR1/PR2 — xai and github-copilot providers (S–M)

- **PR1:** register xai (grok-4.7, the Pi default from `1a584a7a5`) on the
  Responses family with `XAI_API_KEY`.
- **PR2:** register github-copilot. It needs catalog rows and device login;
  `GitHubCopilotOAuthProvider` already exists (`oauth_providers.py:206`).
- Neither provider is in `provider_registry.py` today.

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

## Deferred and watch list

Revisit these when an active item needs them or the Pi surface stabilizes.

- **Pi MCP / codemode / tool search** (`8562bcf66`, 2026-09-29). This is L and
  still moving. Revisit in about two weeks.
- **Strict tool schemas** (Pi 0.86.0): only once a primary provider needs
  capability-gated constraints.
- **Per-model image input limits and resize** (Pi `f5c946480`).
- **Missing RPC commands:** `clear_queue` (`a79b37334`), prompt disposition
  (`e473b5cd8`), and `get_tree`/`get_entries` (`7ba1b6bfe`).
- **Extension events** that Pi's `core/extensions/types.ts` has and pipy's
  `extensions/activation.py` lacks.
- **Pi's shared OAuth callback server** (`4df157433`). This is a refactor only:
  pipy already has `AnthropicOAuthProvider` (`oauth_providers.py:117`).
- **Other deferred items:**
  - mid-conversation effort updates (Pi `9e05370b2`, `4e69b0c28`)
  - per-model compaction overrides (Pi `46bde88a1`)
  - live Codex model discovery
- **Standing deferrals:**
  - Parallel tools, until latency evidence starts with explicitly safe reads.
  - Image generation.
  - Distributed agents, durable lanes and server mode.
  - Full component/theme parity.
  - Measure startup/cancel latency and request size before setting performance
    gates.

## Minimum acceptance and constraints

Daily-use acceptance means:

- completing a real repository task and running its checks
- interrupting and steering work
- resuming after exit
- continuing after compaction without reconstructing the task by hand

A provider failure must leave a usable session, and an extension must be
addable through the documented APIs. Automated assertions cover lifecycle and
request content. Semantic summary quality and UX need live dogfooding (DF1).
Tests alone are not a daily-usability claim.

The following stay as they are:

- the synchronous native core
- provider-neutral authority and event contracts
- full-content private product sessions
- the separate metadata-only learning archive
- project trust and extension publication boundaries

Preserve the existing behavioral and shared-state tests. Do not add a new async
runtime, DI framework, universal event bus, decomposition program or exact
file-size gate. Isolate tests from global settings, and leave both local theme
stores on `pi`.

## Contract map

Current behavior lives in these docs:

- [Architecture](architecture.md)
- [Harness Spec](harness-spec.md)
- [Session Tree](session-tree.md)
- [Compaction](compaction.md)
- [Python SDK](sdk.md)
- [Extension API](extension-api.md)
- [Provider Catalog](provider-catalog.md)
- [Settings & Config](settings-config.md)
- [Automation & RPC](automation-rpc.md)
- [TUI Workflow](tui-workflow.md)
- [Export](export-distribution.md)
- [Session Storage](session-storage.md)
- the [transactional reload contract](specs/2026-07-25-transactional-extension-reload-rebuild.md)

A dated filename does not make a runtime contract obsolete.

The following are evidence, not alternative queues. Their next-step
instructions do not override this index.

- the [July assessment](2026-07-29-architecture-quality-assessment.md)
- the [Pi gap audit](pi-mono-gap-audit.md), pinned to Pi 0.82.0
- the [historical parity inventory](parity-plan.md)

## History

- **D0–D8 (2026-09-07 → 2026-09-09).** The task table, per-slice narrative,
  investigation basis, thread protocol and review record are in
  [archive/backlog-2026-09.md](archive/backlog-2026-09.md). Its key
  milestones:

  | Milestone | Commit |
  | --- | --- |
  | D0 baseline | `f1fa668` |
  | D1 semantic compaction | `527b846` |
  | D2 product sessions | `faffcd0` |
  | D3 budgets and within-run compaction | `68dddcf` |
  | D4 recovery | `8c34a9c` |
  | D5 shared controls | `112397a` → `0bfd48c` |
  | D6 reopen/transitions/SDK retirement | `6e38076`, `b370a8a`, `b275c82` |
  | D7 RPC bash | `66189ed` |
  | D8 replay | `73f4aef` |
  | MC1 model currency | `23eb706` |

- **Older programs.** The architecture migration, quality, transactional
  reload, comparative remediation and god-file decomposition programs are
  complete. Earlier backlogs and plans live in Git: use
  `git log --all -- <path>` and `git show <commit>:<path>`.

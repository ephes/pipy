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
- **Biggest user-facing gap: model currency.** The hand-maintained catalog stops
  at claude-opus-4-7, gpt-5.6-sol and gemini-3.1-pro-preview. Several provider
  defaults point at retired models. See MC1.

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

### 1. MC1 — Current models and thinking maps (S–M, in progress 2026-09-29)

Refresh the built-in catalog and thinking behavior in one slice, because new
Claude rows without the thinking fix would take the wrong `budget_tokens` path.

- **Rows.** Add these, taking IDs, cost, context and thinking maps from Pi's
  `packages/ai/scripts/generate-models.ts` and models.dev rather than from tau:
  - Claude opus/sonnet 5.5, opus/sonnet 5, fable-5, opus-4-8 and haiku-4-5.
  - gpt-6 sol/luna/astra and gpt-5.6 terra/luna on openai and openai-codex.
  - gemini-3.5-flash and 3.1-flash-lite.
  - The Claude rows mirrored onto openrouter, bedrock and vertex where Pi has
    them.
- **Removals and fixes.**
  - Fix the codex gpt-5.6-sol context from 372k to 272k (Pi `35f12c8c7`) in
    `catalog_data.py` and `chrome.py:335`.
  - Remove codex gpt-5.4 (Pi `2e6fe2f98`).
  - Drop the retired claude-3-5 and gpt-4o rows.
- **Defaults.** Mirror every provider default from Pi's
  `coding-agent/src/core/model-resolver.ts:21` (`defaultModelPerProvider`) into
  both `catalog.py:163` and `provider_registry.py`.
  - These are stale today: anthropic (`claude-3-5-sonnet-20241022`), google
    (`gemini-2.0-flash-exp`), google-vertex, amazon-bedrock, azure-openai
    (`gpt-4o`), openrouter, mistral and openai-completions/cloudflare.
  - For providers Pi does not key the same way, pick a current row.
  - Add a test that every default names a current row.
- **Thinking.**
  - Extend `ANTHROPIC_ADAPTIVE_MODEL_MARKERS` (`providers/anthropic_messages.py:57`,
    reused by `providers/bedrock.py`), or honor a
    `compat["forceAdaptiveThinking"]` flag, for opus-5, sonnet-5 and fable-5.
  - Pass `max` through the effort field.
  - Use Pi-exact maps: fable `off: null` with `{xhigh, max}` only; gpt-6
    sol/luna `off: "none"`; astra `off: null`; `minimal` unsupported on all
    gpt-6 rows.
  - Add one conformance test per family.
- **Docs.** `docs/provider-catalog.md`, `docs/providers.md` and `CHANGELOG.md`.

### 2. CTX1 — Pi context-file and skill discovery (S)

Pi loads `AGENTS.override.md`, `AGENTS.md`, `AGENTS.MD`, `CLAUDE.md` and
`CLAUDE.MD` per directory (`coding-agent/src/core/resource-loader.ts:162`). It
also loads `.agents/skills`, globally and under trust for projects
(`core/trust-manager.ts:181-197`). Pipy deliberately excludes `CLAUDE.md`
(`workspace_context.py:13-21`) and only knows `.pipy/skills` (`skills.py`), so
it runs without project instructions in CLAUDE.md-only repos. Adopt Pi's
candidate order and skill roots, keep the symlink-escape guard, and update
`docs/usage.md`
([Context and system prompt files](usage.md#context-and-system-prompt-files))
and `docs/pi-parity.md`.

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

### 4. PC1 — OpenAI/Codex prompt-cache affinity (S)

Pipy sends no `prompt_cache_key` today, and `openai_codex_provider.py:1284`
sets `session-id` to a fresh per-request ID, which defeats cache affinity.
Mirror Pi (`packages/ai/src/api/openai-codex-responses.ts:561`, `:1674`,
`openai-prompt-cache.ts`):

- Send the durable session ID, clamped, as the body `prompt_cache_key` and as
  the websocket `session-id` and `x-client-request-id` headers.
- Cover Codex, Responses and Azure.
- Omit the key when cache retention is `none` and on private summary calls.

The tau reference is `src/tau_ai/openai_cache.py` (`1401fcd`).

### 5. PC2 — Anthropic `cache_control` and retention (M)

- **Current state.** Pipy sends no `cache_control` today. It already parses
  cache-read and cache-write usage (`http.py:403`), has a
  `NativeModelCost.cache_write` field (`catalog.py:45`), and renders `CH%` in
  the footer (`chrome.py:651`).
- **Work.**
  - Add Pi-style breakpoints in `providers/anthropic_messages_wire.py`, plus
    Bedrock/Vertex where supported (Pi `api/anthropic-messages.ts:1091`).
  - Add a `cacheRetention` setting (5m/1h) and a 1h cache-write cost.
- **Follow-on.** Pi prompt cache warming (`c596d09d9`).
- **Tau reference:** `src/tau_ai/anthropic.py` and `dev-notes/prompt-caching.md`.

### 6. UX1 — `/thinking` selector and truthful footer labels (S–M)

- Add Pi's `/thinking` command (`496185f6e`). Its options come from the active
  row's thinking map. Pipy has only `app.thinking.cycle` (shift+tab,
  `keybindings.py:99`).
- Derive the footer context meter and effort label from catalog metadata.
  `_context_budget_for` and `_effort_label_for` (`chrome.py:340-369`) hard-code
  codex/sonnet and fall back to 128k and to "high"/"default". Compaction already
  reads the row's `context_window`
  (`repl/provider_selection.py:1005`).
- Show response time and tok/s (tau `7b96883`).

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
  - Re-baseline the feature notes in `docs/pi-parity.md` when MC1/CTX1 land.
  - Delete the stale remote branches (`feat/catalog-non-completions`,
    `feat/extension-api`, `feat/tui-interaction-comfort`).
  - Review the four local stashes with their owner before dropping any.

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

- **Older programs.** The architecture migration, quality, transactional
  reload, comparative remediation and god-file decomposition programs are
  complete. Earlier backlogs and plans live in Git: use
  `git log --all -- <path>` and `git show <commit>:<path>`.

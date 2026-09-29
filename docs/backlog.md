# Pipy backlog

Status: sole active task index, rewritten 2026-09-29.

## Status

- **D0–D8 daily-use program: complete** (2026-09-09). It covered semantic
  compaction, product sessions, request budgets, retry, shared queue and RPC
  controls, RPC bash and cross-provider replay.
- **Pi realignment to `4df157433`: done** (2026-09-29). MC1, CTX1, PC1, PC2,
  UX1, MC5, COST1, PR1/PR2, the gate and symlink repair, and DH1 landed; see
  [Done](#done-2026-09-29).
- **Remaining queue:** DF1 (live dogfooding) and RL1 (release 0.2.0).
- **Tests:** `uv run --frozen pytest --co` collects 6,571 tests on
  `chore/dh1-hygiene` (6,565 at `23fdc99b`).
- **Not verified live.** Only openai-codex gpt-6-sol has live evidence (MC1,
  PC1, COST1). Anthropic 5.x, xAI, Copilot, live semantic-summary quality and
  cross-provider answer quality are covered by fake-transport tests only. DF1
  owns that.
- **Pi comparison gates:** `session_tree_pi_comparison.py` passes against Pi.
  `automation_pi_comparison.py` fails on Pi's mid-conversation system messages
  (SYS1 below); every other check passes.

This document owns direction, order and task IDs. A queue item is a bounded
work order. It does not override a current runtime contract (see
[Contract map](#contract-map)). The per-slice D0–D8 narrative, the contracts
and the review evidence are archived in
[archive/backlog-2026-09.md](archive/backlog-2026-09.md).

## Evidence baseline

The queue below was baselined against these checkouts on 2026-09-29:

| Repository | Commit | Notes |
| --- | --- | --- |
| pipy | `23fdc99b` | `main`, clean (re-baselined by DH1) |
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

### 1. DF1 — Live smoke and dogfooding acceptance (S–M)

- **Smoke check first.** Send one credentialed request per new family (codex
  gpt-6-sol, anthropic opus/sonnet 5.5, xai, github-copilot) before claiming
  support. Fake-transport
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

### 2. RL1 — Release 0.2.0, talk-minimum (S)

The pyproject is still at `0.1.0`, the repo has no tags, and CHANGELOG
`[Unreleased]` runs from line 7 to line 1083.

- Split the CHANGELOG into `0.2.0`.
- Tag the release.
- Make the README install note accurate for a checkout install.

Choosing a PyPI name and adding a SHA-pinned publish workflow can follow.

## Done (2026-09-29)

Each slice follows Pi `4df157433`. Plans are in `docs/specs/2026-09-29-*`, and
the per-slice notes (evidence, live checks, deviations) are archived verbatim
in [archive/backlog-2026-09-29-slices.md](archive/backlog-2026-09-29-slices.md).

| ID | Change | Merge |
| --- | --- | --- |
| MC1 | Current model rows, provider defaults and thinking maps | `60d9de59` |
| CTX1 | Pi context-file and skill discovery | `1235bedc` |
| Gates | Seven stale parity gates repaired; skills and context files follow symlinks; global resources under `~/.pipy` | `2c97afe5` |
| PC1 | OpenAI/Codex/Azure prompt-cache affinity, `PIPY_CACHE_RETENTION` | `c638b675` |
| PC2 | Anthropic/Bedrock `cache_control`, 1h retention | `f731231c` |
| UX1 | `/thinking` selector; footer shows the live level | `ca2de136` |
| MC5 | `just catalog-drift` against Pi's generated catalog | `208e5a47` |
| PR1/PR2 | `xai` and `github-copilot` providers, `/login github-copilot` | `ca883f31` |
| COST1 | Catalog-cost pricing for every model (Pi `calculateCost`) | `23fdc99b` |
| DH1 | Pi comparison gates run on `node` again and fail when Pi is missing; explicit `fake-native-bootstrap` REPL crash fixed; docs re-baselined | `chore/dh1-hygiene` |

## Follow-ons

Open deviations collected from the done slices, not yet queued. Take one when
DF1 or a user need makes it matter.

- **USAGE1:** per-message usage in JSON/RPC messages and the session tree,
  resumed-session totals, Pi's `/session` Cost section, totals across a model
  switch, and Pi's footer token semantics (uncached `↑`, latest-message `CH`).
- **SYS1:** Pi's mid-conversation system messages (`9e05370b2`): the system
  prompt as a transcript message with sections and tool declarations. This
  keeps `automation_pi_comparison.py` red. It also carries mid-conversation
  effort updates (`4e69b0c28`).
- **Thinking and model switches:** an extension `setThinkingLevel` does not
  rebuild the provider; interactive `/model` appends no
  `thinking_level_change` on a clamp; `/model x:level` clamping; no fuzzy
  search in selectors or `/thinking` completion; the 128k context fallback for
  a selection without a catalog row.
- **Prompt cache:** openai-completions `prompt_cache_key` and affinity
  headers; Codex WebSocket reuse and per-session SSE fallback; uuidv7 routing
  ids; Pi's deferred-tool placeholder; Bedrock cache gate reads `model.name`;
  prompt-cache warming (`c596d09d9`).
- **Catalog and cost:** built-in rows carry no cost tiers (xAI and Copilot
  included); extension provider rows carry no cost; `models.json` rejects
  negative cost where Pi accepts it; Mistral rows are text-only and a reasoning
  Mistral row without a map sends the raw level; display names are not
  compared; legacy rows (Claude 3.5, gpt-5.1-codex, Gemini 2.x) can be pruned;
  OpenRouter Claude rows need Anthropic Messages over OpenRouter.
- **Providers:** xAI OAuth login; Copilot token persistence (Pi writes it back
  under a file lock) and `Retry-After` on 429.
- **Skills and prompt:** template and command stores keep the symlink guard; a
  plain `.md` skill keeps its file stem; package skills load after the auto
  roots; not ported: `disable-model-invocation`, the bash-only skill
  advertisement and Pi's `<tools>`/`<rules>`/`<cwd>` prompt sections.
- **Timing display:** response time and tok/s (tau `7b96883`) and tau's
  reactive model/thinking preview stay pipy product decisions; Pi has neither.
- **Repo hygiene (owner):** the fully merged remote branches
  `feat/catalog-non-completions`, `feat/extension-api` and
  `feat/tui-interaction-comfort` can be deleted; four local stashes from
  2026-07-01 to 2026-08-02 wait for an owner decision.

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
  | MC1–COST1, PR1/PR2, DH1 | see [Done](#done-2026-09-29) |

- **Older programs.** The architecture migration, quality, transactional
  reload, comparative remediation and god-file decomposition programs are
  complete. Earlier backlogs and plans live in Git: use
  `git log --all -- <path>` and `git show <commit>:<path>`.

# Pipy backlog

Status: sole active task index, rewritten 2026-09-29; upstream scope decision
updated 2026-10-04, 2026-10-12 (MCP1 selected), 2026-10-14 (MCP1 audited),
2026-10-15 (MCP1 goal-check tracks merged), 2026-10-16 (MCP1 done), and
2026-10-17 (main/history, docs, and Python compatibility audit).

## Status

- **Release 0.3.0** (2026-09-30) collects the post-0.2.0 parity slices; see
  `CHANGELOG.md`. Parity work is now in **maintenance mode** (owner decision
  2026-09-30): prefer gaps a user can see, and run `just catalog-drift` and the
  Pi comparison gates (`session_tree_pi_comparison.py`,
  `automation_pi_comparison.py`) weekly.
- **D0–D8 daily-use program: complete** (2026-09-09). It covered semantic
  compaction, product sessions, request budgets, retry, shared queue and RPC
  controls, RPC bash and cross-provider replay.
- **Pi realignment to `4df157433`: done** (2026-09-29). MC1, CTX1, PC1, PC2,
  UX1, MC5, COST1, PR1/PR2, the gate and symlink repair, and DH1 landed; see
  [Done](#done-2026-09-29).
- **MCP1 MCP and codemode tool reach: done (2026-10-16).** The final goal
  check found Pi parity reached; the owner approved the merge to `main`.
  The live evidence and the recorded differences stay open; see
  [MCP1](#mcp1-mcp-and-codemode-tool-reach-done-2026-10-16).
- **CM1 Python codemode: done (2026-10-09).**
  Public codemode remains opt-in. T4–T11 and A–K evidence are indexed in the
  [acceptance note](specs/2026-10-09-python-codemode-acceptance.md#acceptance-and-closeout-index).
  Per-slice and cumulative review/check outcomes and main/CI/worktree closeout
  are recorded in the coordinator campaign report. UP1
  completed its selected-surface audit; DF1 still lacks other-provider live evidence.
- **Python compatibility:** Python 3.11–3.15. Full-suite CI covers 3.11,
  3.14, and 3.15; real-PTY checks cover Linux/macOS on 3.14 and 3.15;
  runtime-required codemode probes cover Linux on both. Codemode's guest
  remains pinned to CPython 3.14.7. Use `uv run --frozen pytest --co -q`
  for the current test inventory; old release-branch counts are historical.
- **Not verified live.** openai-codex has live evidence for `gpt-6-sol` at
  off/medium/max and `gpt-6-luna` at low/max: effort values, footer, cache hits,
  a real repository task, steering, cancel, manual and automatic compaction
  (semantic summaries were accurate), retry, resume, fork and a switch between
  the two models (DF1). Anthropic 5.x, xAI, Copilot, Bedrock and Google have no
  credentials here. They and cross-provider answer quality are still covered by
  fake-transport tests only.
- **Pi comparison gates:** session-tree and automation comparisons passed again
  on 2026-10-17 against `c268154ca`. Freshly regenerated catalog data now shows
  17 unallowlisted differences across 97 shared rows, with no stale allowlist
  entries; see UP2 below. The earlier CLEAN result used September data.

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

- **`main` integrates; one writer per checkout.** Work lands on `main` in
  small committed chunks, with one writer per checkout. Independent slices may
  run in parallel in short-lived worktrees and merge to `main` in turn
  (`AGENTS.md`). Read-only investigators can run concurrently. They report
  findings and do not edit this index.
- **Checks.** Run focused tests plus `just check`, and run `prek` when
  `.pre-commit-config.yaml` exists. Add `just docs-build` for doc changes and
  the PTY smoke tests for terminal changes. Test fixtures isolate HOME, so do
  not wrap `uv` or `just check` in a temporary HOME.
- **Review.** Use a fresh read-only reviewer that is separate from the
  implementer. Default to a different model family through the installed
  review-loop skills: Codex/Pi implementations use Claude Opus 5.5 at medium
  effort; Claude implementations use Codex. Pi review gates use only
  `openai-codex/gpt-6.1-sol`. Honor an explicit owner-selected reviewer and
  never present a same-family review as different-family evidence. Cycles follow the **diminishing-returns rule** in `AGENTS.md` (Review
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

## Current queue

The owner requested the full [native durable execution plan](specs/2026-10-19-durable-execution-plan.md)
on 2026-10-19, against clean Pi durable 1.3.0 at
`2ec55d808d455b1a64a2bf7888198207a096120a`; see the
[published-surface audit](specs/2026-10-19-durable-upstream-audit.md).
**Execution is authorized; DE01a, R1 and DE01b1–DE01b3b landed; DE02 landed (DE02a1, DE02a2a, DE02a2b, DE02b1a, DE02b1b, DE02b2a, DE02b2b and DE02b3); DE03 is split a→b→c→d, DE03a and DE03b landed, and DE03c is split c1→c2 with DE03c delivered and DE03d split d1→d2, d1+d2 and cumulative DE03d/DE03 landed at `2dba6008`. DE32a landed at `52ecc1fc`; DE32 is IN_PROGRESS and DE32b OPEN. DE04a is IN_PROGRESS, DE04b OPEN. DE06a landed; DE06b remains OPEN.**
DE01a landed at `97d623f`; its failed initial CI is historical. R1 landed at
`35142ec` with audited [CI 39255373624](https://github.com/ephes/pipy/actions/runs/39255373624)
successful in all ten jobs. DE01b1 landed at `7acf5f7`, independently reviewed
Opus5.5/high CLEAN0; [CI39270913622](https://github.com/ephes/pipy/actions/runs/39270913622)
succeeded10/10 (each full suite12,849 passed/208 skipped). Its first ISSUES
review remains historical in the evidence ledger. DE01b proceeds sequentially:
DE01b1 owns strict multi-case/provenance/projection plus H1/C1 and lost-field/
stale-head negatives; DE01b2 owns H2/H3/E1 and duplicate-effect/post-abort-write
negatives. DE01b2 landed at `28bc886`, actual Opus5.5/high CLEAN0 and
[CI39286240678](https://github.com/ephes/pipy/actions/runs/39286240678)10/10:
each full suite12,919 passed/208 skipped, durable229/0. Its pre-review outcome
repair and first ISSUES/S1/S2 remain historical. DE01b3a landed at `e3b2b391`,
actual Opus5.5/high CLEAN, with [CI39302403566](https://github.com/ephes/pipy/actions/runs/39302403566)
success10/10 (each full suite12,996 passed/208 skipped, durable306/0). Its first
ISSUES and accepted test-evidence repairs remain historical. DE01b3a owns fresh local acceptance,
exported hardware/workloads, four fresh mutants and exhaustive mapping; DE01b3b
owns mandatory actual pinned CI, selective artifacts and pushed-job proof.
DE01b3b landed at `13d061d2`, actual Opus5.5 HIGH CLEAN and
[CI39370449156](https://github.com/ephes/pipy/actions/runs/39370449156) success11/11,
with every uploaded artifact entry audited.

### DE02a2 internal table/document children and the DE02b split

DE02 is split before product edits:
DE02a1 values/records/Storage Protocol landed at `b1aa0562`, actual Opus5.5 HIGH
CLEAN0 and audited [CI39387254837](https://github.com/ephes/pipy/actions/runs/39387254837)
success11/11. DE02a2a landed at `85390e2b`, actual Opus5.5 HIGH CLEAN and audited
[CI39408774643](https://github.com/ephes/pipy/actions/runs/39408774643) success11/11.
It owns internal atomic tables and the bounded actual Node/Python
table comparison. DE02a2b landed at `72253352` with raw-base documents inside the
same authority/candidate and cumulative Storage acceptance: round 1 Claude Code
Opus5.5 HIGH ISSUES (one Suggestion, repaired), round 2 Codex gpt-6.1-sol HIGH
delta CLEAN. Its push started CI39545767044, which was not awaited; from
2026-10-21 GitHub CI is not a delivery gate (local `just check`, Codex CLEAN and a
`git ls-remote`-verified push are), and Claude Code Opus5.5 HIGH implements while
Codex gpt-6.1-sol HIGH reviews. DE02a2 and DE02a are therefore delivered.
DE02b is split b1a→b1b→b2→b3: b1a, the private guarded Storage and lifecycle
owner with deterministic native tests, landed at `992c2102` (Codex gpt-6.1-sol
HIGH ISSUES, one Critical and one Warning, repaired by Opus5.5 HIGH; delta CLEAN;
no CI claim); b1b, its actual pinned Node guard oracle with paired recipes, guard
mutants and two fresh chains, landed at `cb38caf3` (Codex gpt-6.1-sol HIGH ISSUES,
two Warnings, repaired by Opus5.5 HIGH; delta CLEAN; no CI claim); b2 the first
internal Session/Tx over that same owner, split again b2a→b2b: b2a, the private
Tx core and mutable raw-base drafts without a commit entrypoint, landed at
`be6ee84e` (Codex gpt-6.1-sol HIGH ISSUES, one Critical and one Warning, repaired
by Opus5.5 HIGH; delta CLEAN; a package-inventory name repair, delta CLEAN; no CI
claim); b2b, the first exposed internal Session (the owner line, sync and async
commit, the cancellation matrix, publication and close), landed at `eb1e3ba7`
(Codex gpt-6.1-sol HIGH, five rounds to CLEAN; no CI claim), so DE02b2 is
delivered; b3, the cumulative paired Session acceptance and the final gate
(closed recipes against the pinned Pi `SessionImpl` and the native Session in
sync and async modes, with a closed map of every original DE02/DE02b cell),
landed at `d2e62390` (Codex gpt-6.1-sol HIGH, one Warning repaired, then CLEAN;
no CI claim). DE02b and DE02 are therefore delivered. No cumulative acceptance
or P row is removed. This internal stage has no product/default/SDK
availability or empty document shims.

### DE03 SQLite store split

DE03 is split a→b→c→d before code, every original cell retained: **a** path
ownership, owner-only files, shared-filesystem refusal and a minimal verified
connection lifecycle (landed at `0d37f64a`: Codex gpt-6.1-sol HIGH, six rounds
to CLEAN, round 4 resolved by Root's explicit contract boundary; no CI claim;
the on-disk contract is in
[the storage document](session-storage.md#durable-sqlite-store-internal)); **b**
schema v1, the migration runner, the header identity gate and open-time
integrity (landed at `f6024e56`: Codex gpt-6.1-sol HIGH, round 1 one Warning
repaired with the shared `damage_as_corruption` guard, round 2 CLEAN; no CI
claim); **c** `SqliteStorage` with contract cases, real failures and crash cuts,
split again before code into **c1** the table operations with the
backend-agnostic contract cases over Memory, SQLite and SQLite reopened after
every commit (landed `1563bb68`: Codex HIGH review, two repair rounds then CLEAN;
local `just check` 15,184 passed, 2 skipped, 54 xfailed and the known docs-preview
flake, isolated rerun 16/16; no CI claim) and **c2** documents, write refusals,
acknowledgement loss and crash cuts (landed `a2c4c6d2`, exact-tree Opus5.5 HIGH
R4 CLEAN, remote confirmed; known docs-preview full-check exception recorded
honestly in the evidence ledger); **d** split before code into **d1** native
Session over real SQLite and a bounded current-base workload (landed `8ab38d20`), then
**d2** the paired pinned Pi SQLite oracle, valid D2(a)–(f) probes, two fresh
chains and final cumulative gate (landed `2dba6008`, exact Opus5.5 HIGH R3
CLEAN, two fresh chains and owner known-preview exception). DE03c/d and
cumulative DE03 are delivered. Each DE03 slice also runs its new tests on the Ubuntu macmini
(Root decision D10).

### General durable execution and other current work

All original DE01 acceptance remains
cumulative and all P rows OPEN. See [reference reproduction](durable-reference.md).
The [evidence ledger](specs/2026-10-19-durable-execution-evidence.md) tracks all
DE00–DE45 slices and P01–P43 requirements. The final plan review was
Suggestions-only **ISSUES**, with both wording suggestions applied before the
handoff; it is not a CLEAN implementation gate. Delivery uses one writer on
`main`, GPT-6.1 Sol HIGH implementation, independent Claude Code Opus 5.5 HIGH
review and commit/push per green slice; from 2026-10-21 Claude Code Opus 5.5 HIGH
implements and Codex gpt-6.1-sol HIGH reviews, and GitHub CI is not a delivery gate. No production durable feature, migration
or default change is implemented in DE00. All parity requirements remain OPEN.

The [first prototype](durable-prototype.md) and
[canonical-loop spike](durable-loop-integration.md) provide measured crash and
rich-content evidence. Controlled single-run recovery is the first milestone;
the full plan also covers scheduler/queues, streaming, extensions, compaction,
owned subagents, Python codemode recovery and native SDK/TUI/RPC integration.
The prototype's unsafe blocking is an initial safety boundary; full parity
requires Pi's explicit interrupted-error continuation without repeated effects.
The historical experimental durable TUI was removed upstream; native pipy
integration remains required. Existing product defaults/storage are unchanged.

The [2026-10-18 quality audit and refactoring plan](specs/2026-10-18-refactoring-plan.md)
collects the current architecture, provider-streaming and documentation audit.
RF-C1 archive lifecycle/capture mapping ownership is complete (2026-10-18):
deterministic thread/process regressions, full checks and tracked documentation
validation passed, and independent high-effort review closed CLEAN.
RF-D1 documentation artifact exclusions are complete (2026-10-18): filtered
build/preview, artifact/search and interruption regressions, strict links,
full checks and independent high-effort re-review passed.
RF-S1 [provider capability/evidence matrix](provider-capabilities.md) is complete
(2026-10-18): real local SSE/WebSocket pre-terminal callbacks and buffered JSON
completion are pinned; dated DF1 live evidence remains scoped. Focused/full
checks, strict docs/links and independent high-effort review passed.
RF-D2a current-status corrections are complete (2026-10-18): public/native
runtime and hosted-doc claims now distinguish current contracts from historical
bootstrap rationale, preserving headings and anchors. Focused/full checks and
independent high-effort review passed. RF-D2b extension-runtime extraction is
complete (2026-10-18): the current component map links the controlling R0 contract
and R7 evidence, while the architecture retains its global overview and anchors.
Focused checks (34 tests), full `just check` (12,770 passed, two skipped), strict
docs/rendered links and all 105 original heading IDs passed; fresh independent
high-effort first review closed CLEAN. Other RF-D2 topic extractions remain
pending. RF-D3 onboarding/navigation is complete (2026-10-18): explicit
fake/real setup, compact concepts diagrams, grouped navigation, and mobile
keyboard access passed 34 focused checks, full `just check` (12,770 passed, two
skipped), strict docs/rendered links and actual 390/1440 browser QA. Fresh
independent high-effort review closed CLEAN with zero findings, including its
own browser/code-scroll verification. RF-P3 catalog triage is complete
(2026-10-18): 474 focused checks, catalog conformance, pinned Pi session-tree and
automation comparisons, full `just check` (12,790 passed, two skipped), and strict
docs/rendered links passed. Initial high-effort review and the targeted review
of stale historical test expectations both closed CLEAN. This closes the
selected campaign.
RF-P1 foreign-thinking request projection
is complete (2026-10-18): requests drop thinking unless provider/API/model all
match, while stored history and tool correlations remain unchanged. Focused
replay/SDK reopen tests, full checks and strict docs/links passed; independent
high-effort review closed CLEAN after synchronizing current source comments.
RF-S2 shared cancellable SSE payload ownership is complete (2026-10-18):
focused/full checks, strict docs/rendered links and independent high-effort
review passed. Codex retains its event/authentication/WebSocket/retry policy.
RF-CI1 product PTY process-exit synchronization is complete (2026-10-18):
focused Python 3.11/current checks, full checks, strict docs/rendered links and
independent high-effort first review passed. It retains the original deadline
and product/session assertions without runtime or CI policy changes. RF-S3a
ordinary Responses SSE adoption is complete (2026-10-18): default
OpenAI/xAI/Copilot/custom Responses construction streams, while explicit JSON
injection retains byte-identical buffered requests. Local wire, JSON/RPC and
real-PTY regressions cover incremental delivery, partial failure/cancellation and
fresh-attempt managed retries. Full `just check` passed (12,634 tests, two
skipped), with strict docs/rendered links; fresh independent high-effort review
closed CLEAN after fixing sink-error retry classification and cancellation
partial-history/status-body handling. RF-CI2 RPC signal-test stdout draining is
complete (2026-10-18): focused current/Python 3.11 PIPE/bounded-socket checks,
full checks, strict docs/rendered links and independent high-effort first review
passed. The old wait-before-read order fails under local backpressure; remote CI
pipe capacity remains an inferred trigger. RF-S3b Azure adoption is complete
(2026-10-18): default catalog construction streams through the shared consumer
with Azure-owned configuration, errors, usage and replay; explicit JSON injection
retains the original bytes. Local Azure wire, cancellation/retry and
JSON/RPC/real-PTY checks passed, together with full checks (12,659 passed, two
skipped) and a fresh independent high-effort first review CLEAN. No new
authenticated live evidence is claimed.
RF-C2 continuous Codemode input ownership is complete (2026-10-18): required-runtime
Codemode and terminal/nested checks passed, along with full `just check` (12,669
passed, two skipped), strict docs/rendered links and independent high-effort
review CLEAN. One changelog-order suggestion was applied. A single-Escape PTY barrier after a settled read
fails with the old binding and passes with the injected executable scope;
subsequent turns/replay preserve historical results. The remote CI output alone
did not identify the lost-byte cause; the separate local barrier proves it.
RF-P2a generic Chat reasoning fidelity is complete (2026-10-18): buffered
scalar/structured capture, canonical API provenance, exact-target continuation/
reopen and focused compatibility flags. Focused checks (427 tests), full
`just check` (12,747 passed, two skipped), strict docs/rendered links and fresh
independent high-effort first review CLEAN all passed.
RF-P2b Mistral native ordered thinking is complete (2026-10-18): focused checks
passed (357 tests), fresh independent high-effort first review closed CLEAN with
no findings, and full `just check` passed (12,770 tests, two skipped). Unsigned
native capture, canonical
`mistral-conversations` provenance, exact-target native replay and durable SDK
reopen retain existing tools/images/errors/usage. Routing and the Chat endpoint
remain unchanged; Chat streaming is not part of either final-content claim.
RF-CI3 is complete (2026-10-18): current and isolated Python 3.11 focus each
passed 122 tests; full `just check` passed (12,748 tests, two skipped), with
strict docs/rendered links and independent high-effort first review CLEAN.
Two pre-existing Python 3.11 test-contract failures from
[CI run 39128929802](https://github.com/ephes/pipy/actions/runs/39128929802)
are repaired with bounded event ordering: the tree-selector fixture distinguishes
its active watcher from the true final prompt, and the MCP callback test pins
attempt-thread self-refusal while exercising a delayed caller's current failed
state. Local scheduling probes reproduce both old assertions; the remote
schedule itself was not recorded. Runtime/flush/notification and CI policies
are unchanged. RF-CI3 preceded the subsequently completed RF-P2b slice.
MCP1, CM1 and UP2 retain their completed statuses; this plan does not reopen them.

**UP2 — CI stabilization and bounded upstream refresh (done 2026-10-17).**
The owner approved stabilizing CI, making catalog age explicit, then adopting
one relevant upstream correction. Implemented scope: the nested cancellation
test waits for the queue event and checks recorded settlement separately from
caller-thread completion order; the drift check rejects old or undated input
unless `--snapshot` is explicit; Azure comparisons use Pi's current `azure`
table; Codex GPT-6.1 Sol has Pi's 500K context (`5712b134e`). Validation:
212 focused tests, all 11 real-PTY smoke tests, both Pi comparison gates,
docs build and `just check` (12,527 passed, two skipped). Independent Opus 5.5
medium repair review returned CLEAN after fixing the initial stale row/meter
test expectations and two suggestions. Remote CI confirmation requires a new
push. At the UP2 closeout no further implementation slice was selected.

The UP2 baseline at Pi `c268154ca` reported 17 catalog findings. RF-P3 now
adopts only five existing-row metadata corrections (two cache-read rates and
three context windows), with review and full validation complete. The same generated
manifest dated `2026-10-17T19:08:42.111Z` leaves **12 intentional findings**
across 97 shared rows with `stale: []`; drift exit 1 remains expected. Each
remaining effort flag/map or missing row has an individual source/runtime/impact
gate in [the catalog triage](provider-catalog.md#bounded-catalog-refresh-rf-p3).
No new rows, allowlist entries or protocol flags are included. RF-P1's completed
foreign-thinking request projection remains separate; summary output limits
still need a provider request comparison before being called a gap.

Selected campaign status: RF-C1, RF-D1, RF-S1, RF-D2a, RF-P1, RF-S2,
RF-S3a/S3b, RF-P2a/P2b, RF-D2b and RF-D3 are complete, together with RF-C2
and RF-CI1/CI2/CI3 correctness/stabilization slices. RF-P3 is complete. RF-A1/A2/T1 and further RF-D2
topic extractions remain conditional on a concrete ownership or behavior need;
this does not promise every proposal or broader live-provider gap is complete.
MCP1 and CM1 remain completed work; live MCP evidence
still needs an authenticated provider and OAuth server account.

The 2026-10-17 audit compared `main` through `e67e759b` with the first-parent
history since release 0.3.0 and the merged MCP history. It found the main feature
status current, but stale test counts, pre-merge campaign wording, and older
follow-on claims. The owner confirmed that the repository is public;
`AGENTS.md` now reflects that, retaining the no-deprecation rule. The audit also found two Linux test failures in the MCP merge's
[CI run](https://github.com/ephes/pipy/actions/runs/38899409996): an oversized
fake-worker argv item and a duplicated teardown diagnostic. The test repairs
preserve payload/cleanup assertions; subsequent CI passed on `ad238ea3`,
`3ee2cf5d` and `0396b55d`. The [latest run](https://github.com/ephes/pipy/actions/runs/39052342303)
at `ae7b79c2` exposed the separate caller-thread ordering assumption addressed
by UP2. The source and user docs remain the behavior contracts.

## Recently completed campaigns

### MCP1 — MCP and codemode tool reach (done 2026-10-16)

Owner-requested 2026-10-12: the whole Pi MCP feature at functionality parity,
against Pi `a447ba11f`. The
[parity plan](specs/2026-10-12-mcp-parity-plan.md) holds the acceptance
checklist (292 rows with Pi sources), the deviations, the design, the
ordered slices S01–S40, X1, X2 and the goal-check slices G1–G9, L1–L4 and
N1–N4, and the test strategy. Scope: codemode reaching
extension and MCP tools with discovery, the MCP client (stdio and streamable
HTTP), `mcp.json` configuration and project overrides, exposure modes,
`tool_search`, the `mcp_servers` prompt section, resources, OAuth, result
truncation, `pipy mcp`, the `/mcp` manager, `--no-mcp` and `mcp__` tool
patterns, reload, and the extension and SDK hooks.

Fixed by the owner: the synchronous native core stays (no async runtime, no
`mcp` SDK dependency; the client is written in pipy), codemode stays Python
on WASI with Python names, and configuration uses Pi's `mcp.json` format at
pipy's locations under the existing project-trust rules. Nested calls run
concurrently since G3 (plan DEV-03). Decided
2026-10-12 (plan section 7): images in tool results and the extension
orchestration hooks are in scope, the client name is `pipy`, and slices land
on `feat/mcp-parity`, pushed per slice, as a campaign exception to the trunk
rule; the merge to `main` needed owner approval (given 2026-10-16). Decided 2026-10-14: no
default OAuth Client ID Metadata Document (dynamic client registration stays
the default, CIMD is opt-in through `oauth.clientMetadataBaseUrl`); stdio
servers on Windows, the Radius offer after a login and codemode `models.*`
are outside the parity goal; inline terminal images are in scope; Pillow is
the optional `images` extra.

State after the closing audit and the goal-check tracks (2026-10-16, plan
section 3.2 and the [acceptance note](acceptance/2026-10-13-mcp-parity.md)):
every slice is on `main` through merge `e67e759b`. Of the 292 rows, 165 are closed with a
test and 127 with a recorded limit or deviation; none is open. The official
MCP conformance suite gives the same result for pipy as for Pi
(`just mcp-conformance`, a manual recipe). Open:

- **Live evidence** (plan section 6.5): a stdio server through `uvx` or
  `npx` and an OAuth HTTP server, with a real model. Not recorded yet; it
  needs the owner's provider login and an account at an OAuth MCP server.
- **Owner decisions:** which of the recorded
  differences from Pi (plan section 3.1, DEV-15 to DEV-101) that plan
  section 7, Q11 to Q17, do not name are accepted. Q11 (2026-10-15)
  accepts the findings of the second goal check that its triage neither
  fixes nor refutes; Q12, Q13 and Q14 (2026-10-15) do the same for the
  third, fourth and fifth goal checks, Q15 (2026-10-16) records that
  the sixth goal check left nothing to accept, Q16 (2026-10-16)
  accepts DEV-95 and DEV-96 for the seventh, and Q17 (2026-10-16) accepts
  DEV-97 to DEV-101 and parts of DEV-36 and DEV-37 for the eighth. Q17
  also defined the final goal check, completed below. The detailed fix-slice
  and integration history lives in the parity plan and acceptance note.

Campaign done (2026-10-16): the final goal check Q17 describes ran at
`2f95587d` and found the goal reached in all six areas, each CLEAN with no
finding, so it added nothing to this backlog. The owner approved the merge
of `feat/mcp-parity` to `main` the same day. The follow-ons from the goal
checks stay listed under [Follow-ons](#follow-ons), and the live evidence
and the owner decisions above stay open.

### CM1 — Python codemode (done 2026-10-09)

Owner-requested 2026-10-04; bounded implementation and T11 user documentation
complete. Python is an intentional difference from Pi; pipy-native remains the
product runtime. Codemode is off by default. See [usage](codemode.md),
[light plan](specs/2026-10-04-python-codemode-plan.md),
[spike/contract](specs/2026-10-06-python-codemode-spike.md) and the
[T4–T11 / A–K evidence index](specs/2026-10-09-python-codemode-acceptance.md#acceptance-and-closeout-index).
[Developer capture/reproduction](specs/2026-10-09-python-codemode-developer.md#coordinator-terminal-capture)
preserves T9 instructions. Linux x86_64 runtime-required CI passed 315 tests;
that is historical CM1 evidence, separate from the current main CI status above.

The initial T10 live pair and separate interactive run passed exact facts and
expected calls. The repeat kept correct facts but first emitted bulk results,
then compacted with a second script: two parents/eight reads, 99,508 HTTP bytes,
10.542 s, strict pattern checker exit 1. Direct repeat passed: five requests,
four reads, 254,777 bytes, 13.659 s. Both fixed-order pairs are descriptive;
no third retry or relaxed checker hides the model-behavior limitation.

Per-slice full checks and independent reviews, the cumulative whole-campaign
review and final main/CI/worktree outcomes are recorded separately in the
coordinator campaign report. This product index does not assert a review verdict.

Extension-tool generalization and MCP/discovery/exposure are now MCP1 above.
Other follow-ons require separately selected slices: shared state (parallel
calls are MCP1 slice G3; `store()`/`load()` is MCP1 slice G4), images (the tool-result part is MCP1 slice S39), and `models.*`
(structured bash results and options lines are MCP1 slice G5; `models.*` is
outside the MCP parity goal by the owner's decision of 2026-10-14). Compact-script
model adherence needs further evidence; the T10 repeat is retained as a limitation.
**CM1-A1 — adherence guidance (review closed).** The owner-selected follow-on adds
direct string-processing guidance,
compact-output guidance and an executable advertised small-file example; see
the [adherence slice](specs/2026-10-09-codemode-adherence.md).
Fresh unchanged-instruction samples reproduced strict-pattern failure (one of
three passed); all three fixed modified-instruction samples passed the unchanged
strict checker. Focused required-runtime checks (186), full check (7,941 with
two skips), static checks, docs and PTY passed. Opus 5.5/high independent review
closed as advisory with one deferred Suggestion and no blocking findings; this
is not CLEAN. Historical failures remain evidence rather than being superseded.
A resident engine is an owner decision only after T10; retain fresh workers now.
Runtime provenance/mirroring/packaging and unverified platform support remain
open (Intel macOS, Windows, real Ubuntu host; outer-sandbox support qualified).
Crash durability needs an owner decision on per-call `CustomEntry`: current
parent-only evidence can be lost on a mid-script host crash. Canonical pipeline
exceptions preserve the original object through secondary parent evidence and
release-status failures, close the service and release the local reservation with
child counters retained. This bounded cumulative-review repair is implemented;
publication while unwinding is best-effort, without guaranteed saved history.

### UP1 — Refresh the established Pi agent comparison (completed 2026-10-02)

Inspected established-agent changes from `4df157433` through Pi 1.0.0
(`a13d35a74`), plus current upstream `b271b0a52`. The catalog, session-tree
and automation gates passed at the current checkout. The
[audit](pi-1.0-audit.md) records coverage, retained differences and deferred
candidates. Adopted header-only startup and capacity-error retry; MCP,
codemode and durable execution retain their existing scope boundaries.

Other implementation items remain unqueued. Promote a follow-on into a numbered
item when a user need makes it matter. Effort: S ≈ one focused slice,
M ≈ two or three slices.

## Upstream scope decision (2026-10-01)

The owner agreed to retain the **established Pi coding agent** as the reference
for selected daily-use parity and keep pipy-native as the Python product
runtime. Maintenance mode continues. Full coverage of every current Pi surface
would require a separately approved expansion of the program.

Source inspection of Pi `a4715ec9b` found the following; this is a planning
assessment, not a completed rebaseline or a fresh parity-gate result:

- Pi's release changed 0.87.1 directly to 0.99.0, but the September baseline
  already included unreleased features. The version jump is not a measure of
  newly outstanding work. GPT-6.1 Sol and OpenAI ChatGPT sign-in have already
  landed in pipy 0.3.0.
- **MCP / codemode / tool search** (Python-codemode planning split out as
  CM1 on 2026-10-04) was the next substantial feature-scope decision, separate
  from UP1. The owner selected it on 2026-10-12 as MCP1, with the bounded plan
  and the dependency decision (no `mcp` SDK, no async runtime) recorded in the
  [parity plan](specs/2026-10-12-mcp-parity-plan.md). OAuth, tool naming,
  discovery, exposure and reload behavior were still changing upstream until
  the plan's reference commit; later Pi changes are not in its checklist.
- **Virtual models and classifier operations** are separate coverage decisions;
  current realignment does not establish parity for them. Image generation
  remains deferred.
- **Pi durable execution** was a separate architecture watch track under the
  October scope decision. The owner expanded planning on 2026-10-19 to the full
  [durable execution program](specs/2026-10-19-durable-execution-plan.md).
  The former experimental SQLite TUI at `5609b0d6c` demonstrated recovery,
  subagents and task views, but was removed by `7472cda0d6e`; it is historical
  evidence, not the current package target. Pi durable 1.3.0 at the pinned ref
  owns the execution comparison. Pipy's existing JSONL tree is not a durable
  scheduler, and current production behavior stays unchanged in DE00.

UP1 should also triage smaller established-agent changes such as provider/model
CLI validation, provider retry and tool-schema handling, extension validation
and tool previews. Compare each with pipy's implementation before calling it a
gap. This decision did not authorize MCP implementation, parallel tools or a
durable-runtime migration; MCP was authorized separately on 2026-10-12 (MCP1).
The owner authorized the 2026-10-19 durable campaign, including parallel tool
rounds and native storage migration as later implementation slices. DE00
imports the experiments and work order; the later slices own those changes.

## Other work merged since 0.3.0

These entries supplement the historical September table. They are completed
changes or evidence, not another queue.

| Area | Evidence on main | State / remaining boundary |
| --- | --- | --- |
| Compaction accounting and loader | `368507f9` | DF2 landed; current `tokensBefore` and loader behavior are documented in [Compaction](compaction.md). |
| Summary pressure, stopped turns, ordinary/auxiliary retries, footer context | `3d15c7a2`, `0fc32de1`, `1802fac2`, `df63f902`, `e523b1b9` | F5b, F6b, and F4b implemented; the detailed evidence below retains advisory review outcomes and remaining differences. |
| Context elision evaluation | `83b1294b`, `12179ea2`, `04952279` | [Evaluation](specs/2026-10-11-context-elision-evaluation.md) completed; generic read-output elision was not adopted. Product compaction remains unchanged by that experiment. |
| Capture and transfer robustness | `bba19640`, `73d5c98e`, `7bc6d52b`, `1e08d86a`, `4b91b722` | Private export/import and token-count retention, single-state automatic-capture claims, scalar metadata validation, and terminal-safe archive output landed. See [Export](export-distribution.md) and [Session Storage](session-storage.md). |
| Reload and process/descriptor cleanup | `1e6d2768`, `c5a67e55`, `dc574acc`, `7c92a014`, `cc609926` | Reload refreshes instructions/prompt inputs; auth-command capture, attachment reads, clipboard helpers, and changelog decoding received bounded cleanup/fallback fixes. |

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
| DF1 (partial) | Live smoke and dogfooding on openai-codex, recorded in [the acceptance note](acceptance/2026-09-29-df1-dogfooding.md). Five bugs were fixed: a model switch kept no history, edit/write diffs corrupted the TUI, Escape froze the frame for about 3 s on the WebSocket close, tool rows showed argument dumps, and a refused switch changed the footer's thinking level. Gaps: no live evidence for other families (no credentials), automatic compaction can stop a session (DF1-F5), and follow-ons below | `chore/df1-dogfooding` |
| READ1 | `read` follows Pi: `offset`/`limit`, 2000 lines / 50 KB, `[Showing lines …] Use offset=N to continue` notices; no size cap or content refusal. Follow-ons READ2, READ-IMG, TOOLS1 | `fix/read1-read-tool` |
| RL1 | Release 0.2.0: version bump, CHANGELOG `[0.2.0] - 2026-09-29` with highlights, the wheel ships `CHANGELOG.md`, README/quickstart checkout-install note (no PyPI package). The release tag is applied on `main` after the merge | `release/0.2.0` |
| TOOLS1 | `bash`, `grep`, `find` and `ls` follow Pi's output handling: `bash` keeps the last 2000 lines / 50 KB and names a full-output temp file; `grep` (regex, `glob`, `ignoreCase`, `literal`, `context`), `find` (fd glob rules) and `ls` take a `limit` and end cut output with Pi's `limit=2N` / `50.0KB limit reached` notices. `grep` uses `rg` or a Python fallback; `find` walks in Python. Follow-on TOOLS2 | `feat/tools1-tool-output-parity` |
| DF1-F5 | An oversized turn no longer wedges the session. Summary requests cut each tool result to 2000 characters (Pi `serializeConversation`), and a persistent session's automatic cut that keeps only the new prompt writes a compaction entry that keeps no earlier entry (Pi `firstKeptEntryId ?? id`). The oversized turn is still refused (as in Pi); the next prompt recovers. Follow-on F5b | `fix/f5-oversized-turn` |
| DF1-F2/F3 | Resume shows and restores the session ([plan](specs/2026-09-29-f2-f3-resume-restore-plan.md)). Startup `-r`/`--continue`/`--session`, `/resume`, `/tree` navigation, `/fork`, `/clone`, `/new` and `/import` redraw the transcript from `build_context_entries()` (Pi `renderInitialMessages`), clearing the scrollback like Pi; `[compaction]`/`[branch]` rows and plain tool results follow Ctrl+O. A new branch records `model_change` and `thinking_level_change` with its first message, every model switch records `model_change` (and a clamped level), and opening a session restores its model and thinking level unless the CLI pins them (Pi `core/sdk.ts:194-263`). Deviations: model restore needs a `model_change` (pipy assistant messages name no model); a runtime fallback keeps the live model; `/new` keeps the live model and level. Follow-on DF1-F2b | `fix/f2-f3-resume-restore` |
| SYS1a | Pi system messages in the transcript ([plan](specs/2026-09-29-sys1-system-messages-plan.md)). The first run records `role: "system"` with the prompt as the `preamble` section and every tool in `toolsAdded`; later runs and in-run tool changes record only changes. JSON/RPC events, `agent_end.messages`, `get_messages`, the session file and the compaction checkpoint (`systemMessage`) carry it; the TUI draws nothing, `/tree` shows `[system]`. Provider requests are unchanged (Pi's collapse path). `automation_pi_comparison.py` is green. Remainder: SYS1b | `feat/sys1-system-messages` |
| DF1-F6 | Aborted and failed turns are kept ([plan](specs/2026-09-30-f6-aborted-turn-plan.md)). The assistant message stores the streamed partial text and `stop_reason` `aborted`/`error` (with `error_message`), is persisted and shown on resume as `Operation aborted` / `Error: …`, and is skipped by every provider request (`materialize_provider_request`, Pi `transformMessages`) and by the summary request. JSON/RPC messages carry `stopReason`/`errorMessage`. Original F6 limitations are closed by DF1-F6b below | `fix/f6-aborted-turn` |
| USAGE1 | Usage stored on every assistant message ([plan](specs/2026-09-30-usage1-message-usage-plan.md)). The loop records Pi's `usage` (uncached `input`, `output`, `cacheRead`, `cacheWrite`, `totalTokens`, `cost` parts from `calculateCost`) and the answering `provider`/`model`; the session file and every JSON/RPC assistant message carry them. The footer, RPC `get_session_stats` and `/session` sum every stored assistant message on every branch (Pi `getSessionStats`), so totals survive resume and a model switch. The footer follows Pi's parts (uncached `↑`, latest-message `CH`, `formatTokens`); `/session` prints Pi's `Session Info` with `Messages`, `Tokens` and `Cost` (per-model breakdown, `Cache Re-billed`). Old sessions load unchanged. Follow-on USAGE1b | `feat/usage1-message-usage` |
| READ2 | `read`, `ls`, `grep`, `find`, `@file` and `@image:` resolve paths like Pi's `resolveToCwd`/`resolveReadPath` ([plan](specs/2026-09-30-read2-path-policy-plan.md)): cwd-relative or absolute, `~`, `@` prefix, unicode spaces, macOS screenshot/NFD/curly-quote variants, no deny list. `grep`/`find` leave out only what `rg --hidden`/`fd --hidden` do (measured rules in `tools/ignore_walk.py`, differential tests against the binaries). `--read-root`, `PIPY_READ_ROOTS` and the `Reference roots` prompt block are removed. No documented security boundary covers the read tools (`bash` is a real shell); the allowlisted command sandbox keeps its own policy. Follow-on READ2b | `feat/read2-path-policy` |
| TOOLS2 + READ2b | `write`, `edit` and `bash` follow Pi ([plan](specs/2026-09-30-tools2-write-edit-bash-plan.md)). `write`/`edit` resolve like `resolveToCwd` with no deny list or size cap; `write` makes parent directories and overwrites; `edit` takes `edits[]` with Pi's exact/fuzzy matching, BOM/CRLF handling, error texts and numbered diff (jsdiff `diffLines` ported); one file's mutations are serialized. `bash` ends a failure with `Command exited with code N` / `timed out` / `aborted` as an error. The `!` shortcut uses Pi's executor output (sanitized, 2000 lines / 50 KB, temp file), `bashExecutionToText` record and component rows. Pi's grep/find/ls/write/edit headers; `ls` sorts by a Node-measured ICU approximation; the global git excludes file applies to the Python walks. No documented boundary covers the mutation tools. Deviations: the no-terminal `!` keeps a 600 s bound; collation outside the measured table; follow-on TOOLS3 | `feat/tools2-write-edit-bash` |
| TOOLS3 + DF1-F2b (part) | Tool rows follow Pi's `ToolExecutionComponent` and per-tool renderers ([plan](specs/2026-09-30-tools3-tool-rows-plan.md)): one box per call (pending, then success/error background), read/grep/find/ls/write/edit/bash collapsed and expanded texts, compact `read` classification, edit's diff preview and word-level highlights (jsdiff `diffWords` ported), bash's last-5-lines preview, warning footer and `Took` (bash only), extension renderers and Pi's generic fallbacks. Tool results carry and store Pi's `details` (truncation, limits, edit diff, extension details; never sent to providers). Ctrl+O re-renders every row, extension rows included, live and restored, clearing the scrollback like Pi's full render; the render-details sink and the tools' side output are removed. Deviations: no syntax highlighting, links or ticking `Elapsed`; follow-on TOOLS3b | `feat/tools3-renderers` |
| DF1-F7 + DF1-F2b (TUI) | TUI polish ([plan](specs/2026-09-30-f7-tui-polish-plan.md)). `/compact` and automatic compaction redraw the chat like Pi's `compaction_end` (kept rows, then the `[compaction]` row; the prompt being sent is drawn after the kept rows), and summary rows are Pi's padded `customMessageBg` boxes. The `pi` theme is Pi's `dark` theme (truecolor and 256-colour codes from Pi's `theme.ts`), and the editor border follows the thinking level or `bashMode` (no ` ! bash ` label). A running `bash` row ticks `Elapsed Ns` every second. Status lines are Pi's dim `showStatus` rows without the `pipy  pipy:` prefix, with Pi's hotkey texts. Paints wait for startup (no stale editor frame), `quietStartup` hides the TUI header, and `--print`/`--mode json`/`--mode rpc` write no chrome to stderr. Deviations and the model-selection and compaction-prompt bullets: follow-on DF1-F7b | `feat/f7-tui-polish` |
| DF1-F7b (selectors) | Pi's `fuzzy.ts` port; `/model` exact-ref switch or fuzzy selector of available models (Tab scope toggle, Ctrl+S saves default); `/thinking` selector with search; slash menu fuzzy ranking and `/model`/`/thinking` argument completion; `/resume` search via Pi's `parseSearchQuery`; slash commands no longer drawn as user messages; `Error:` lines ([plan](specs/2026-09-30-f7b-selectors-plan.md)). Remainder in the F7b follow-on | `feat/f7b-selectors` |
| SYS1b | System messages reach providers ([plan](specs/2026-09-30-sys1b-provider-system-messages-plan.md)). Catalog compat flags from Pi's data (`supportsMidConvoSystemMessages`, `supportsAdditionalTools`, `supportsMidConvoToolChanges`, `supportsDeveloperRole`; `supportsMidConvoToolAdditions` via `models.json`); requests carry the session's anchored system messages for adapters that accept them: Responses/Azure/Codex developer messages with `additional_tools`/`tool_search` loads, Chat Completions/Mistral, Anthropic held `system` messages with `tool_addition`/`tool_removal`, placeholder and beta; Google/Bedrock collapse. Tagged prompt sections; `added_tool_names` and `supportsToolReferences` removed (Pi). Remainder: SYS1c | `feat/sys1b-provider-system-messages` |

## Follow-ons

Remaining deviations and closeout evidence collected from the done slices.
Items explicitly marked complete are historical closeouts, not work to repeat.
Open items are not yet queued. Take one when
a user need makes it matter. The DF1 items come first; their repro steps are in
[the DF1 acceptance note](acceptance/2026-09-29-df1-dogfooding.md).

- **READ-IMG, images from `read`:** `read` attaches JPEG, PNG, GIF and
  WebP files (MCP parity plan S39 and G5). Without Pillow (the `images`
  extra) `read` does not resize an image over 2000 x 2000 pixels or 4.5 MB
  of base64 or convert a BMP; such an image is omitted with Pi's text. With
  Pillow it does (goal check r3, D2). The `Current model does not support
  images` note is not added.
- **TOOLS3b, what TOOLS3 left of tool rows** (see the TOOLS3 Done row):
  - no syntax highlighting in `read`/`write` rows (Pi uses highlight.js), no
    OSC 8 file links (DF1-F7 added the ticking `Elapsed`);
  - JSON/RPC `toolResult` messages carry `details` only for the parents of
    nested calls (minus the record, which is the message's `nestedCalls`
    field since MCP1 goal check r8, B; `tool_execution_end.result` carries
    them for every tool since MCP1 goal check r2); Pi's thrown tool errors
    store `details: {}`, pipy stores none;
  - `edit` stores no `details.patch` (jsdiff `createTwoFilesPatch`);
  - a hallucinated tool name gets the generic `name key=value` row where Pi,
    knowing no definition, prints the arguments as JSON;
  - the RPC `bash` command still runs through the allowlisted sandbox
    instead of Pi's executor.
- **DF1-F2b, restored-history rendering gaps** (left by F2/F3; TOOLS3 closed
  the extension-row expansion and tool-result details, F7 the live compaction
  redraw):
  - Reasoning storage ([plan](specs/2026-09-30-reasoning-storage-plan.md),
    branch `feat/reasoning-storage`) stores Pi's ordered content with its
    signatures and `api`, replays it per adapter and shows thinking on
    resume. RF-P2a restored generic Chat scalar/details capture and focused
    compatibility flags; RF-P2b restored Mistral native thinking. Left: Pi's
    per-run mouse toggle of a thinking block.
    Durations are not stored in Pi either: a restored `bash` row has no
    `Took` line in both.
  - A resumed session shows no `Session compacted N times` status (Pi
    `renderInitialMessages`).
- **DF1-F4b complete, what F4 left of retries:** F4 is merged (plan:
  `docs/parity-loop/plans/f4-retry-visibility.md`; closeout contract:
  [F4b retry closeout](specs/2026-10-10-f4b-retry-closeout.md)). Every provider is retried
  with Pi's classifier, also after partial output. The TUI shows `Error: ...`
  and `Retrying (n/3) in Ns... (escape to cancel)`. Codex `error` /
  `response.failed` events carry Pi's text and are classified by it. A failed
  turn estimates trailing context from the last successful stored branch usage.
  Slice 1 now persists every ordinary failed attempt and emits message/turn/agent
  boundaries with attempt-local messages. Separate logical settlement preserves
  SDK/RPC ownership; backoff cancellation keeps the last error without an extra
  aborted message or row.
  Slice 2 restores the footer anchor from active branch history and adds each
  trailing message's independently rounded chars/4 estimate. Model changes use
  the selected window; stopped usage never supplies an anchor. Compacted contexts
  retain a conservative full estimate while their summary suffix remains.
  Slice 3 now retries plain and prepared auxiliary providers and projects bounded
  summarization retry statuses directly to JSON/RPC and TUI surfaces, preserving
  private content, archive exclusion and guarded state-first acceptance.
  All four F4b bullets are complete. Final auxiliary review closed with no
  Critical or Warning findings and one applied advisory assertion; full
  verification passed 8,185 tests with two expected skips.
- **DF1-F5b, oversized auxiliary-summary recovery (prepared 2026-10-09;
  advisory review closure):** reproduced huge pasted text, large tool arguments
  and aggregate pressure on `ca608ce`. Compaction now preflights bounded incomplete
  excerpts, executes one private summary operation and bounds accepted recovery
  text, retaining an explicit durable information-loss warning. Fitting later
  prompts recover without `/new`; intrinsically oversized new prompts still
  refuse. Safe cuts, append-only transcript, cancellation/currentness and
  state-first persistence stay intact. This extends Pi; branch summaries and
  token-based retention are unchanged. See the [slice note](specs/2026-10-09-df1-f5b-summary-recovery.md).
- **DF1-F6b stopped turns: complete.** Live stopped markers (`67e20315`),
  display-only partial-call rows (`2ad2a1fb`) and the
  [semantics closeout](specs/2026-10-10-f6b-stopped-turn-semantics.md) implement
  all remaining F6b gaps: recoverable stopped arguments, paired tool settlement
  followed by an empty aborted assistant, and labelled stopped text in private
  summaries. The [dependency ADR](decisions/2026-10-10-stopped-partial-json.md)
  has Opus 5.5/high design repair review CLEAN. Final code repair review closed
  advisory (not CLEAN): all three Warnings repaired; one explanatory Suggestion
  addressed. Full check: 8046 passed, two expected skips; focused contracts 686,
  required runtime 49, PTY smoke 8, docs/lint/format/types green. Real CLI verifies
  failure/interruption/compaction/tree/resume and original F5b pressure/refusal/
  later fitting continuation without `/new`; authenticated Codex CLI verifies
  codemode/nested read. Finite arguments match Pi; whole-value non-finite fallback
  is explicit. Existing entries, ordinary replay exclusion, bounded attempted-file
  metadata and private summary budget/currentness/persistence contracts remain.
- **DF1-F7b, what F7 left of TUI polish** (see the F7 Done row):
  - Selectors: the F7b selectors slice
    ([plan](specs/2026-09-30-f7b-selectors-plan.md), branch
    `feat/f7b-selectors`) ported Pi's `fuzzyFilter`, `/model <ref>` exact
    matching, the searchable model and thinking selectors, the fuzzy slash
    menu with `/model`/`/thinking` argument completion, the `/resume`
    search, no user message for commands, and `Error:` lines on those
    paths. It leaves:
    - `/login` argument completion (Pi's login provider options); extension
      commands have no `getArgumentCompletions`;
    - the model selector does not refresh catalogs (Pi's `Refreshing model
      catalogs…` / `Model catalogs refreshed.` line), and `/model <ref>`
      does not refresh before its second lookup;
    - the search input lacks Pi `Input`'s undo, yank, word motion and word
      deletion and forward Delete (pipy decodes no `Delete` key), and edits
      by code point, not grapheme cluster;
    - the `/resume` picker has no `relevance`/`threaded` sort and searches no
      message text (`allMessagesText`); `re:` uses Python's regex syntax;
    - a skill run draws its expanded text as a user message; Pi draws a
      `SkillInvocationMessageComponent`;
    - argument completion opens on any edit after `/<command> `; Pi's editor
      opens it only for letters, digits, `.`, `-` and `_`;
    - the plain REPL keeps pipy's `/model <ref>` resolver and its command
      bubbles; the settings dialog's provider/model list is still pipy's
      list; the default model is pipy's defaults store, not Pi's
      `defaultProvider`/`defaultModel` settings.
  - The compaction summary treats its own instruction as a user request:
    pipy sends the removed messages as structured history followed by the
    instruction. Pi serializes them into one `<conversation>` text
    (`serializeConversation`) with its structured `SUMMARIZATION_PROMPT` /
    `UPDATE_SUMMARIZATION_PROMPT`, split-turn prefix summaries and the
    read/modified file lists (a compaction request slice; see also F6b).
  - Notice texts stay pipy's; outside `/model` and `/thinking`, notices that
    Pi shows with `showWarning`/`showError` (`Warning: …` / `Error: …`) are
    still dim status lines, and back-to-back statuses are not merged into one
    line. An unhandled `/…` line gets pipy's notice; Pi sends it as a prompt.
  - The non-quiet TUI header lists every hint; Pi shows its logo and a
    condensed list that Ctrl+O expands.
  - `!` rows keep pipy's boxed style; Pi's `BashExecutionComponent` draws
    `bashMode` borders and a bold `$ command`.
  - The expanded compaction and branch summaries and the expanded `[skill]`
    box are not Markdown-rendered (Pi renders them with `Markdown`).
  - pipy compacts before sending a prompt, so an automatic `[compaction]` row
    comes before that prompt's answer; Pi compacts after the answer (or
    after an overflow error) and draws the row after it.
  - Pi draws its working loader inside the editor's top border
    (`── ⠏ Working ──`) while a tool runs; pipy shows no loader there.
  - Pi's default theme is `system` (colours generated from the terminal's);
    pipy's `pi` theme is Pi's `dark`, and other Pi theme tokens (Markdown,
    syntax, `border`/`borderAccent`, `selectedBg`) have no pipy field. The
    `pipy` title stays (bold `accent`) where Pi draws its logo.

- **Publishing:** choose and own a PyPI distribution name, then add a
  publish workflow with SHA-pinned actions. Until then
  `pipy update self` refuses, and the README documents the checkout install.
- **Documentation hosting: done (2026-10-17).** Zensical deployment uses
  `.readthedocs.yaml`, the locked `docs` group, and Python 3.14. The owner
  imported `pipy-agent`; the site is published at
  <https://pipy-agent.readthedocs.io/en/latest/>. The initial build's missing
  uv executable was fixed by invoking `python -m uv`; [build 35212674](https://app.readthedocs.org/projects/pipy-agent/builds/35212674/)
  passed on `0396b55d`. The homepage, MCP, and codemode pages returned HTTP 200.
  Pushes to `main` trigger rebuilds. See
  [contributor setup](https://github.com/ephes/pipy/blob/main/CONTRIBUTING.md#documentation-hosting).
- **USAGE1b, what USAGE1 left of Pi's usage** (see the USAGE1 Done row):
  - footer meter branch-anchor restoration is closed by DF1-F4b slice 2;
    RPC `contextUsage` remains deferred. Pi `getContextUsage` reports unknown
    after compaction until a valid new response; pipy's footer deliberately keeps
    a full estimated projection while its summary suffix remains;
  - compaction and branch summaries record no `usage` (Pi stores it on the
    entry and counts it); there are no `usage` entries (Pi cache warming);
  - assistant messages carry `api` since reasoning storage; they still have
    no `responseModel`, `responseId` or `timestamp`. The `/session` breakdown
    keys on the requested model;
  - the streamed partial keeps `stopReason: "stop"` where Pi now sends
    `pending`, and has no `provider`/`model`;
  - `/session` has no `Cache Warming` section and no styling; resume and
    `/model` restore still need a `model_change` although assistant messages
    now name their model.
- **SYS1c, system-message remainder:** SYS1c
  ([plan](specs/2026-09-30-sys1c-system-message-remainder-plan.md), branch
  `feat/sys1c`) ported the leading prompt's shape (Responses/Azure first
  input item, Chat Completions instruction role, empty prompt omitted, Codex's
  empty-prompt default), sessions without a leading system message (made
  before SYS1a), `_restoreToolsFromTranscript` on `/tree` navigation (Pi's
  CLI never restores at startup: it always passes the configured tools) and
  Anthropic mid-conversation effort (`supportsMidConvoEffort`,
  `providerThinkingLevel`, `output_config` system messages, `block_binding`,
  the two betas). Still to port from Pi:
  - tool `strict` fields; Anthropic's other betas (fine-grained tool
    streaming, interleaved thinking, server-side fallback) and temperature;
  - OpenRouter Claude rows over the native Messages API (`4e69b0c28`), which
    also carry `supportsMidConvoEffort`;
  - `providerThinkingLevel` on `message_start`: stopped (aborted or failed)
    messages carry it since reasoning storage, but pipy's non-streaming
    Anthropic adapter picks the effort inside its request, after the loop
    emitted `message_start`;
  - kept on purpose: Pi's Codex adapter on a model without
    `supportsMidConvoSystemMessages` sends only the initial system message
    as `instructions`, dropping later section updates; pipy sends the
    replayed prompt.
- **Thinking and model switches:** an extension `setThinkingLevel` does not
  rebuild the provider; `/model x:level` clamping; the 128k context fallback for
  a selection without a catalog row.
- **Prompt cache:** openai-completions `prompt_cache_key` and affinity
  headers; Codex WebSocket reuse and per-session SSE fallback; uuidv7 routing
  ids; Bedrock cache gate reads `model.name`;
  prompt-cache warming (`c596d09d9`).
- **Catalog and cost:** built-in rows carry no cost tiers (xAI and Copilot
  included); extension provider rows carry no cost; `models.json` rejects
  negative cost where Pi accepts it; Mistral rows are text-only and a reasoning
  Mistral row without a map sends the raw level; display names are not
  compared; legacy rows (Claude 3.5, gpt-5.1-codex, Gemini 2.x) can be pruned;
  OpenRouter Claude rows need Anthropic Messages over OpenRouter.
- **Providers:** xAI OAuth login; Copilot token persistence (Pi writes it back
  under a file lock) and `Retry-After` on 429.
- **OpenAI Sign in with ChatGPT (Pi `02eed88fd`, `72abf01ba`):** done
  2026-09-30 (`/login openai`, the global `deviceId`, persisted rotated
  refresh under the auth-file lock, the omitted cache fields, the usage-limit
  link and retry codes; plan `docs/plans/2026-09-30-openai-chatgpt-sign-in-plan.md`).
  Still open: pipy's line-based `/login` has no Pi auth-type selector
  ("Sign in with ChatGPT" / "Sign in with an API key") or API-key dialog, and
  Pi's `PI_OAUTH_CALLBACK_HOST` is honoured only by this flow, not by the
  Codex login.
- **Skills and prompt:** done 2026-09-30 on branch
  `feat/prompt-sections-skills`
  ([plan](specs/2026-09-30-prompt-sections-skills-plan.md)): Pi's default
  prompt with its `tools`/`rules`/`docs` sections (rebuilt for each run's
  active tools, pinned against Pi's output under Node),
  `disable-model-invocation`, the bash skill advertisement, `/skill:name`
  with Pi's skill block and its `[skill]` box, `.md` skill naming, Pi's
  package-first / CLI-last resource order and symlinked templates and
  commands. Left for later slices:
  - skill and prompt-template discovery still skips secret-shaped,
    generated and binary files and stops at pipy's 64 KiB per-file /
    256 KiB total caps; Pi's loaders have none of these screens. MCP1 ported
    Pi's minimatch/brace-expansion resource patterns (`7d2229e4`, `d4ad1d7a`,
    `9dd6e77d`); that pattern work is complete;
  - Pi's bash guideline names `PI_*` session environment variables that
    pipy's bash tool does not set (pipy acts as Pi with
    `exposeSessionEnvironment: false`);
  - the `systemPromptOptions` of `before_agent_start` has only `sections`
    (`event.system_prompt_options.sections`); extension tools have
    `prompt_snippet`/`prompt_guidelines` since the MCP parity slice G9;
  - Pi also refreshes the sections before each turn inside a run; pipy
    rebuilds them per run;
  - package extensions and themes still load at the lowest precedence (Pi
    resolves package resources before the auto roots for every kind);
  - settings `skills`/`prompts` path entries; the wheel does not ship the
    README and docs the `docs` section names; skill descriptions are cut at
    256 characters and `<location>` is the resolved path.
- **Timing display:** response time and tok/s (tau `7b96883`) and tau's
  reactive model/thinking preview stay pipy product decisions; Pi has neither.
- **Repo hygiene:** done 2026-09-29. The three merged remote branches were
  deleted; the four stashes are kept as local `archive/stash-*` branches.

## Deferred and watch list

Revisit these when an active item needs them or the Pi surface stabilizes.

- **Pi MCP / tool search:** no longer deferred. Selected on 2026-10-12 as
  [MCP1](#mcp1-mcp-and-codemode-tool-reach-done-2026-10-16),
  which is done (2026-10-16). What it does not port is listed in
  the plan's deviations tables (parallel nested calls, the non-reach
  codemode globals, Radius setup, Windows process handling, and the
  differences the slices recorded).
- **Pi durable execution:** promoted from watch to full feature planning on
  2026-10-19; see the [ordered program](specs/2026-10-19-durable-execution-plan.md).
  The removed experimental TUI is historical integration evidence; current
  durable package semantics and native pipy integration define completion.
- **Virtual models and classifier operations:** separate feature-scope
  decisions, not covered by the completed realignment.
- **Strict tool schemas** (Pi 0.86.0): only once a primary provider needs
  capability-gated constraints.
- **Per-model image input limits** (Pi `f5c946480`). Tool result images
  are resized with Pi's default limits since MCP1 G7; per-model
  `inputLimits` are not read.
- **Missing RPC commands:** `clear_queue` (`a79b37334`) and
  `get_tree`/`get_entries` (`7ba1b6bfe`).
- **Extension events** that Pi's `core/extensions/types.ts` has and pipy's
  `extensions/activation.py` lacks.
- **`before_agent_start` breadth** (MCP1 goal check r2, finding S24; plan
  DEV-35): the event has no `messageType`, `prompt`, `images` or custom
  `message`, and the result has only `append_system_prompt`. Pi's result can
  also replace the whole system prompt (`systemPrompt`, seen by later
  handlers) and add a custom message to the run (`message`). Anyone porting
  a Pi extension that does either meets this; it belongs to the
  extension-API track, not MCP.
- **`constrained_sampling` on extension tools** (MCP1 goal check r3,
  finding S6; plan DEV-85): Pi's `constrainedSampling` asks the provider for
  grammar-constrained argument generation (Pi's own codemode tool uses a
  Lark grammar for the raw script). pipy's tool adapter cannot carry it, and
  it needs provider-side grammar support. Extension-API track.
- **Render context content blocks** (MCP1 goal check r4, finding D1; plan
  DEV-86): pass the result's content blocks (text and images, in order) to
  custom renderers in the terminal and the HTML export, as Pi's
  `renderResult` gets `content`. Extension-API track.
- **Rich partial results** (MCP1 goal check r4, finding S3; plan DEV-88):
  carry image blocks, `structured_content` and `is_error` of an `on_update`
  partial result through `ToolPartialResult`, the TUI row, JSON/RPC
  `partialResult` and nested `on_update`. Extension-API track.
- **`input` event breadth** (MCP1 goal check r4, finding S4; plan DEV-89):
  `images`, `source` (interactive, rpc, extension) and `streamingBehavior`
  on `InputEvent`, and image replacement in `InputTransform`. Extension-API
  track.
- **`send_user_message` options** (MCP1 goal check r4, finding S5; plan
  DEV-90): `deliver_as` (steer, follow-up) and `expand_prompt_templates`
  (command and template expansion, opt-in as in Pi). Extension-API track.
- **Trailing newlines of a print or JSON prompt** (MCP1 goal check r4,
  finding S6; plan DEV-91, low): keep the prompt's own trailing newlines so
  `/name\n` is not a command, as in Pi.
- **Non-object `details`** (MCP1 goal check r4, finding S8; plan DEV-92):
  allow any JSON value as a tool result's `details` (types, session entries,
  renderers, hooks). Extension-API track.
- **Report extension hook errors** (MCP1 goal check r4, finding S9; plan
  DEV-93): emit an extension error (extension path, event, message) when an
  `input`, `before_agent_start`, `tool_result` or observer handler raises,
  as Pi's `emitError`, while keeping the fail-safe behaviour. Extension-API
  track.
- **Tool source metadata** (MCP1 goal check r5, finding U3; plan DEV-25):
  `source_info` on `ToolInfo` and in `ctx.get_all_tools()`, as Pi's
  `sourceInfo` (`path`, `source`, `scope`, `origin`, `base_dir`), so an
  extension can tell which built-in, extension path or SDK call registered a
  tool. Extension-API track.
- **Wrapped `edit` row diff** (MCP1 goal check r7, finding S1; plan
  DEV-95): build the diff preview of a wrapped `edit` row from the
  arguments and the result the wrapper supplies, as Pi's edit renderer does
  (`computeEditsDiff` for the arguments it is given, `setEditPreview` from
  the result's `details.diff`), so that a wrapper can change or redact it.
  Extension-API track.
- **Every nested call in the live codemode row** (MCP1 goal check r7,
  finding D1; plan DEV-96): keep a full renderer history of a script's
  nested calls beside the bounded record, as Pi's `details.calls`, if a
  script with more than 256 calls ever needs all of them inspectable. The
  durable record stays bounded at 256 calls, as Pi's. Codemode track.
- **Zero-padded SSE `retry` values** (MCP1 goal check r8, finding P4; plan
  DEV-97, low): read a `retry` field of more than 18 digits by its value,
  as Pi's `Number(value)` does, so that a small delay padded with zeros is
  honoured, while still bounding the conversion. MCP track.
- **Pending restorations past an excluded tool** (MCP1 goal check r8,
  finding U1; plan DEV-98): keep tools that `--exclude-tools` removes out of
  the internal active selection, as Pi never registers them, so that an
  exposure change of their server keeps the pending names of other servers'
  restored tools. MCP track.
- **Text blocks of a tool result** (MCP1 goal check r8, finding U2; plan
  DEV-99): keep a text-only result's blocks for `tool_result` hooks and the
  provider request, as Pi's `convertMcpResult` and `_afterToolCall` do,
  instead of only their joined text. Extension-API track.
- **Pi's MCP name and exposure timing** (MCP1 goal check r8, findings U3
  and U4; plan DEV-36, DEV-37, low): pipy gives a suffixed name that
  already belongs to another tool a further suffix, and applies an exposure
  change to a server that is not connected at once; Pi reuses the name
  (the second tool replaces the first) and waits for the reconnect. Both
  are kept as the safer behaviour; revisit only if exact Pi names or timing
  matter to someone. MCP track.
- **Empty `prepare_loadout` descriptions** (MCP1 goal check r8, finding
  S1; plan DEV-100, low): let a loadout blank a tool's description, as Pi
  does, which needs declared copies to allow an empty description.
  Extension-API track.
- **`api.on()` after loading** (MCP1 goal check r8, finding S2; plan
  DEV-101): let an active extension subscribe to events after it has
  loaded (for example from `session_start`) and return a function that
  removes the handler, as Pi's `on()` does. Extension-API track.
- **Proxy support, pipy-wide** (MCP1 goal check r3, finding F2; plan
  DEV-82): Pi's `httpProxy` setting (global only) applied as
  `HTTP_PROXY`/`HTTPS_PROXY`, honoured by the MCP HTTP transport and the
  OAuth fetch (CONNECT tunnelling in their cancellable connect), with
  `NO_PROXY`. Model providers already follow the environment proxies through
  urllib, so behind a proxy MCP HTTP servers fail while model requests work.
- **`auth.json` config values** (MCP1 goal check r3, finding Q1; plan
  DEV-83): Pi's `${VAR}` templates, `$$`/`$!` escapes and the credential's
  `env` map for stored API keys, pipy-wide (model requests and MCP
  `auth.provider`). This changes how a plain env-variable name is read.
- **Pi's shared OAuth callback server** (`4df157433`). This is a refactor only:
  pipy already has `AnthropicOAuthProvider` (`oauth_providers.py:117`).
- **Other deferred items:**
  - per-model compaction overrides (Pi `46bde88a1`)
  - live Codex model discovery
- **Standing deferrals:**
  - Parallel tools (MCP plan DEV-87) remain unchanged in the shipped runtime;
    the durable plan includes concurrent rounds and `executionMode` in DE25.
  - General image generation remains separate; durable codemode model/image
    globals are explicitly included in DE38 of the durable plan.
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
request content. Semantic summary quality and UX need live dogfooding. DF1
covered this on openai-codex; the other families still need it. Tests alone are
not a daily-usability claim.

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
- [Extension runtime component map](extension-runtime.md)
- [Provider Catalog](provider-catalog.md)
- [Settings & Config](settings-config.md)
- [Automation & RPC](automation-rpc.md)
- [TUI Workflow](tui-workflow.md)
- [Export](export-distribution.md)
- [Session Storage](session-storage.md)
- the [transactional reload contract (R0)](specs/2026-07-25-transactional-extension-reload-rebuild.md#r0-current-reconciliation-2026-07-30)
  and [R7 evidence](specs/2026-07-25-transactional-extension-reload-rebuild.md#r7-closeout-reconciliation-2026-08-02)

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

## Additive canonical DTO codec (DE06a)

The independent DE06a lane adds the private [canonical DTO codec](durable-canonical-codec.md)
without runtime hookup. DE06a depends on DE02a1 and existing canonical values;
DE06b waits for DE05 and DE06a and owns actual checkpoint/phase integration.
The original DE06 acceptance remains cumulative and every P row remains OPEN.
Current owner assignment is Codex implementation/repair and Claude Code
`claude-opus-5-5` HIGH review via the installed harness; historical provenance
above is unchanged. The codec lane landed at
`caf4d34e`, with Opus5.5 HIGH R2 unscoped CLEAN, a green local full check,
two fresh reference chains and verified push. DE06 remains IN_PROGRESS;
DE06b is OPEN. The [evidence ledger](specs/2026-10-19-durable-execution-evidence.md#de06a-delivery-and-de03d1-repaired-integration-2026-10-22)
records the completed gates and current D1 integration boundary.

## Additive streaming line scanner (DE32a)

[DE32a](durable-line-scan.md) is the pure bounded scanner prerequisite, dependent only on DE00. It owns exact raw offsets and replacement-decoded sizes without retaining file contents. DE32b depends on DE31 + DE32a and retains all filesystem/reader/list/watch/shell/cancellation/spill/cleanup acceptance, including scanner wiring. DE32 remains IN_PROGRESS until both children land; DE33 still waits for full DE32 and every P row remains OPEN. DE32a landed at `52ecc1fc`; see the [delivery evidence](specs/2026-10-19-durable-execution-evidence.md#de32a-delivery-and-de04a-repaired-main-integration) for completed gates.



## Typed current documents (DE04a)

DE04a replaces the private raw precursor with immutable singleton/family tokens
and one session/conversation/task address resolver. Family identity includes an
empty key; first seeds are copied before awaiting, after the same-Tx memo cutoff.
The existing Session line couples entries/tasks/docs and terminal retirement of
all task members, including unknown kinds and more than one scan page. The
current-base cache contains only committed revisions. Session remains private.
DE04a is IN_PROGRESS; DE04b is OPEN for real normalized operations, deltas and
checkpoint-policy rollback. DE04 waits both children; DE05 still waits DE04.
All43 P rows remain OPEN. See the [document contract](durable-reference.md#typed-current-documents-de04a).

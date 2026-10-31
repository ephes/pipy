# Durable checkpoint — paused 2026-10-31

## Status and authority

The owner explicitly paused all feature work because the weekly subscription
limit was exceeded and requested that the work be documented, committed and
pushed. The complete DE00–DE45 / P01–P43 goal is **PAUSED, not complete**.
Do not restart agents, reviews, tests, CI dispatches or implementation until the
owner explicitly resumes. All known running agents/commands were stopped.

This is a **WIP preservation branch**, not an integration-ready release:
`wip/durable-paused-20261031`. The code checkpoint is
`5b47b6934b370dd48465dfe70c29a50a3741d16d`, tree `d84c08a73db7fa4250b038231cd6732000b52d4f`.
Its sole parent is Main `d2919cc8d671deece989b8f5b5331712dc419a7f`.
A following documentation commit contains this handoff and the unadopted
experiment patches. Main has not been advanced. Do not run the old inert
commit/Main-delivery controllers or reuse a consumed runtime GO.

Target: literal Pi **1.4.0**, commit
`4a080252f06caf63a525d7873c5d94d3c23887e0`. The clean local
`~/src/pi-mono` at `f09d01b85` is newer reference material, not the adopted
comparison target. Native `pipy-native` remains the product direction.

DE00–DE12 are delivered foundations; broader DE13–DE16 obligations remain open.
The current increment is DE16d, genuine retired ordinary WAITING-leaf abort.
DE17–DE45 remain mostly outstanding; DE32a is an earlier bounded prerequisite,
not completion of DE32. **All 43 parent P criteria remain OPEN.** Test counts
and the current DE number are not a meaningful overall completion percentage.
Read [the backlog](../backlog.md),
[the complete plan](../specs/2026-10-19-durable-execution-plan.md), and
[the DE16d contract](../specs/de16d-retired-waiting-leaf-abort.md).

## What is saved

The code checkpoint has 4,404 files and 53 changed paths against Main:
46 modified, seven added, no deletions. It includes:

- DE16d waiting-leaf cancellation implementation, focused tests and contract.
- Sixteen whole-module CI shards per Python version, exact-once aggregation,
  collection/source provenance and conservative failed-matrix handling.
- Actual-child readiness and blocked MCP-print fixture repairs, plus stable
  explicit gzip test IDs; original case payloads/assertions are preserved.
- Reviewed canonical key-validation allocation reduction; Unicode/string-key
  validation, wire formats and immutable ownership stay intact.
- A 60-second outer async hang guard **only** for the depth-1,500 retained-details
  functional scenario. Other cases retain 20 seconds. The scenario still performs
  create, first resume, saved-request readback, reopen, second resume and actual
  CLI serialization with every depth/value/ACK/call-count assertion unchanged.
  The old 20 seconds covered the entire scenario, not each resume; it was not a
  documented product latency SLA. This is a fixture correction, not proof of a
  Native performance improvement.
- One pre-existing CURRENT ABI inventory repair:
  `tool-details-abi-inventory.json /files/11/current_sha256` now matches the
  already-reviewed canonical codec. Only current dependency hashes follow it;
  all original/base inventories, dispositions, reference pins and historical
  D1 evidence remain unchanged.

All 441 `src/` files in the final guard successor are byte-identical to the
previous accepted Native310 source. The handoff/patch files in the next commit
are preservation artifacts, not additional active implementation.

## Verification: completed versus interrupted

Completed on the exact code tree `d84c08a…`:

- Whole retained-presence module: **11 PASS, zero failures/errors/skips**.
- Strict Current34 configuration/recipe/coverage checks: exit 0.
- Strict documentation build: exit 0, no issues.
- Independent Opus 5.5 Medium review: **CLEAN, zero findings**, 83.884 seconds;
  no redactions/omissions/denials, review copy removed, known processes reaped.
  The reviewer could not run pytest because pytest was absent in its copy;
  the separate actual QA above is the test evidence. Stop this clean review
  loop; do not buy another unchanged review.
- Earlier Native and fixture reviews remain qualified to their original source
  epochs. This code has not changed their Native implementation.

The new unfiltered `just check` was **interrupted at the owner's request**:

- Exact collection: **21,433 unique ordered tests**.
- Ruff passed; 1,293 files formatted; mypy passed over 1,159 files.
- At interruption: **3,327 completed PASS, one qualified memory-backend SKIP**;
  3,328 named JUnit cases form the exact collection prefix. One further started
  case and a nameless interrupted XML placeholder are **not passes**.
- In-flight case: `test_product_retry_queued_authority` / active resume / `[C2]`.
  Progress was about 15% of this test run, not of the feature.
- SIGINT was sent only to the owned test group. Recipe exit 130, controller exit
  1, about 820 seconds. A pytest temporary-directory session-finish interrupt
  trace is retained; it is not classified as a discovered product defect.
- All 26 known owned PID/PGIDs retired. Source, Main/index/refs, dependency,
  theme, pinned Pi and Node guards held. **This is not Full PASS.**

An earlier local Full on the older Native310 tree had 21,412 PASS and 21 qualified
SKIPs. Do not relabel that older result as the final successor's Full result.

## Last actual Linux CI result

[Run 41173675072, attempt 1](https://github.com/ephes/pipy/actions/runs/41173675072)
used head `d0c88683464123864d65098495abd54d5e2a9899`, tree310, before the
new guard/current-inventory correction. It is **completed FAILURE**:

| Python | PASS | SKIP | FAIL |
| --- | ---: | ---: | ---: |
| 3.11 | 21,199 | 233 | 1 |
| 3.14 | 21,206 | 227 | 0 |
| 3.15 | 21,206 | 227 | 0 |

There were 59 jobs: 55 successful, four failed. Only Python3.11 shard10 had a
failed test: `deep1500` hit the whole-scenario 20-second timeout while in the
second resume. The other three failures were aggregate gates; 3.14/3.15's own
exact-once validators passed but their conservative global-matrix guard failed.
All 59 job logs and 48 artifacts were retained and independently checked.
**No CI run has yet tested the final guard successor.**

Optional local Linux ARM64 exploration did not supply a clean replacement:
the unchanged deep test body completed in 7.749 seconds, but slim-image teardown
failed because `ps` was missing. A later complete-shard preparation stopped
before tests because Docker's existing filesystem had zero free space. Do not
prune unrelated images/containers or treat this as a blocker for normal GitHub
CI. No unverified dependency substitutions or system changes were made.

## Resume sequence

1. Obtain the owner's explicit resumption; keep the full original objective.
   Start from this pushed WIP branch in a fresh workspace. Re-read the backlog,
   current state and local evidence before relying on this historical handoff.
2. Keep historical failed/interrupted epochs immutable. The implementation
   checkpoint tree is d84; the documentation commit adds preservation files, so
   recompute the actual current file/source fingerprint rather than assuming
   the old 4,404-file whole-tree binding still applies.
3. Run a **fresh complete unfiltered `just check`**, then inspect all results,
   actual skips, exact inventory, source origins and process/environment closure.
   Do not continue from 15%, count the interrupted case as passed or reuse the
   old tree310 Full as acceptance of this branch.
4. The current guard/CURRENT metadata review is already CLEAN. Additional review
   is needed for material code/scope changes, not merely because time elapsed.
5. Dispatch one normal CI pilot on the exact saved/resumed head. Require all
   jobs, all 48 test artifacts, per-version exact-once coverage and actual
   qualified outcomes. Preserve logs and verify the pushed SHA yourself.
6. Only after Full, review and fresh CI gates pass, integrate the small increment
   on Main using the normal fast-forward/source checks and verify the remote.
   The old inert Main executor targets failed d0/tree310 and is not reusable.
7. Continue the entire Durable plan. DE16e has a **separate unresolved review
   escalation**, described below; it does not disappear on general resumption.

## DE16e: explicit decision still outstanding

Its actual PLAN_R3 returned a material starvation/fairness warning and harness
exit 4. Global dirty-metadata/U-reservation barriers can prevent enabled dispatch
under continuous publications. No E implementation was started.

The existing owner decision is whether to simplify this design and authorize one
additional targeted PLAN review, or keep DE16e stopped. Do not reopen the stopped
loop, reset its ledger or infer that decision from budget renewal/general
continuation. The proposal removes the redundant global dirty barrier while
keeping the Session FIFO/current-graph/final-overlay authority, physical phase
ownership and cleanup joins. CODE review remains mandatory after implementation.
Retained disposition/proposal:
`B/de16e-root-PLAN-R3-STOP-disposition-ecygqw58/`.

## P15 readiness, not implemented

The existing `pipy-durable-p15-tool-text-signatures` item remains IDEA. A final
read-only scout confirmed the gap at actual Pi1.4: Pi preserves opaque tool text
signatures through output bounding, including retaining the first/last text
block's signature during truncation. Current Native refuses signed tool text,
collapses text-only block content, and reconstructs trimmed text without that
signature. Assistant signatures are a separate existing behavior.

A small oracle appears independently investigable from E, but **no oracle,
Node/Product test, new model review or implementation was executed**. The
prospective public Harness + real SQLite + synthetic fetch plan has five cases:
unsigned control; signed short text-only; signed text with ordered images;
head truncation; tail truncation. Observe stored bounded blocks, next request,
actual adapter wire projection, image order and reopen without extra effects.
Use only synthetic signatures and the pinned profile. Old Pi2ec source findings
are not runtime proof for Pi1.4. Detailed checkpoint:
`B/durable-p15-readiness-PAUSED-9ql0ga61/CHECKPOINT.md`.

## Unadopted experiments preserved as patches

The following patches are gzip-compressed to preserve their exact original
bytes and deliberately **not applied** to the checkpoint code:

- [Copy elision](2026-10-31-durable-experiments/copy-elision.patch.gz): private parsed
  SQLite tree reuse and first owned-checkpoint snapshot reuse, plus an ownership
  regression. Local 1,316 semantic tests and one deep case passed; one Mac sample
  was 6.673 seconds. No Linux resolution or stable performance gain was proved.
- [Token-pair reuse](2026-10-31-durable-experiments/token-pair-reuse.patch.gz): reuse
  immutable non-float JSON token pairs. Local 1,351 semantic tests and one deep
  case passed, but the single 7.203-second sample was slower than A+B's 6.673.
  No gain was demonstrated; it was not adopted. Do not apply automatically.

## Evidence locations

`B` means `$HOME/.local/state/pipy/durable-checkpoints`. Raw transcripts/session
archives/test artifacts stay outside Git; these references are not permission to
promote private raw material. The code/curated patches and this handoff are saved
in Git. Important retained entries:

| Evidence | Path below B / identifying digest |
| --- | --- |
| Final source and focused QA | `durable-CI-deep-guard-current-successor-mx0p8ott/` |
| Focused QA acceptance | `durable-CI-root-d84-guard-QA-acceptance-rqgk_48b/ACCEPTANCE.json`, `10ba219e…` |
| Guard/CURRENT CODE acceptance | `durable-CI-root-d84-guard-CODE-acceptance-19kxstxc/ACCEPTANCE.json`, `9aa46423…` |
| Interrupted Full | `durable-CI-deep-guard-Full-FINAL-INERT-y7hyx0ol/USER-PAUSE-CLOSURE.json`, `e3d58d22…` |
| Full raw-member seal | Same directory, `USER-PAUSE-CLOSED-SEAL.json`, `e6319368…` (11,096 members) |
| Failed Linux pilot closure | `durable-310-REMOTE-runtime-INERT-5mp1zbm0/PILOT-CLOSURE.json` |
| Root acceptance of failed-pilot evidence only | `durable-310-root-pilot-closure-acceptance-qwwofop2/ACCEPTANCE.json`, `fc77623b…` |
| Retired inert ordinary-commit preparation | `durable-d84-COMMIT-runtime-INERT-c9a3vp1u/` (not executed) |
| Campaign local checkpoint | `full-campaign-20261023/COORDINATOR.json` |

The recorded feature goal is paused. The outstanding gates and full DE/P scope
must be revalidated; a saved branch is not completion or permission to ship.

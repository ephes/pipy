# Native durable execution evidence ledger

Status: **DE00, DE01 (DE01a, R1, DE01b1–DE01b3b) and DE02 (DE02a1 through DE02b3) landed; DE03a/b/c/d1/d2 and cumulative DE03d/DE03 LANDED; DE04a, DE04b1a, DE04b1b2 and DE04b1b1 LANDED; DE04/DE04b/DE04b1b/DE04b1b3 IN_PROGRESS, DE04b1b3a LANDED and DE04b1b3b IN_PROGRESS (owner policy266 resolved: persisted value); DE32a LANDED, DE32 IN_PROGRESS, DE32b OPEN; DE06a LANDED, DE06 IN_PROGRESS, DE06b OPEN; all parity rows OPEN**,
2026-10-22. This ledger accompanies the
[work order](2026-10-19-durable-execution-plan.md) and
[pinned upstream audit](2026-10-19-durable-upstream-audit.md). It is an acceptance
index, not an upstream comparison result. The prototypes are experimental
Python evidence only; they cannot close a P row.

## Provenance and ownership

| Artifact | Identity and meaning |
| --- | --- |
| Starting `main` | `71925366fe96fc235fd7040da4c8a04931b6e0d3`; real index empty at DE00 inventory |
| Experimental baseline | Local `refs/pipy/baselines/durable-2026-10-19` verified at `16e65c41c25ed6b4495d5356a3ce8d3b30ba8a63`; retained, never merged or reset into main |
| Plan-review snapshot | `e4c58446b41ddcc00a321b27947a7637225cbc17`; provenance object, not a landed commit |
| Frozen handoff | `2c219db98ab7a406fcf5e93c4e28731e4ff88a50`; all 15 initial dirty files matched this snapshot before DE00 edits |
| Upstream | Pi durable **1.3.0**, `2ec55d808d455b1a64a2bf7888198207a096120a`; initial source audit via read-only `git show`; DE01a actual reference evidence below |
| Plan review | Claude Code `claude-opus-5-5`, high effort: initial ISSUES (3 Warnings, 1 Suggestion), scoped follow-up ISSUES (2 Suggestions, no Warnings/Criticals). Both final wording suggestions were applied in the handoff. Neither verdict is CLEAN. |
| Execution authorization | Owner authorized DE00–DE45 after the planning handoff. This does not replace an implementation slice's independent CLEAN gate. |
| DE00 ownership | One implementation writer on `main`; coordinator assigns independent verification/review and commit/push separately |

The initial inventory contains only the 15 expected paths: `CHANGELOG.md`,
`docs/backlog.md`, `docs/harness-spec.md`, `docs/parity-plan.md`, both durable
experiment documents, the plan and audit, five `scripts/durable_prototype/*.py`
modules and two `tests/test_durable_*.py` modules. No production `src/` file,
entry point, dependency or user settings change is included. DE00 adds this
ledger and reconciles documentation status. The real index remains untouched.
DE00-PRE1 below explicitly extends the owned set with
`tests/test_docs_build.py` to repair a verification prerequisite discovered by
the independent gate; it changes no production code.

Local review results and full command logs stay outside git. The coordinator's
ignored `.pipy/` checkpoint records their exact paths, exit codes, active
summary-safe session and reviewed/committed/pushed identities. It must be updated
at those gates rather than inventing an identity in this document. Historical
plan-review logs are under `/tmp/pipy-durable-plan-review-5z3yxank/round1/` and
`round2/`; their JSON reports have no skips, redactions or truncations. Temp
artifacts are local evidence locators, not portable release dependencies.

## DE00 acceptance evidence

The preserved experiments have two distinct boundaries:

- [Text prototype](../durable-prototype.md): sequential task/admission/result
  atomicity, safe overwrite/read replay, unsafe bash uncertainty blocking,
  persistent OS ownership and poisoned-store reopen.
- [Canonical-loop spike](../durable-loop-integration.md): real `AgentLoop`,
  persisted provider/tool receipts, rich finite fields, reconstructed usage and
  commit-before-completed-message observation from a separate SQLite reader.

Both discard transient streaming, use fake providers and disposable workspaces,
and expose unsafe interruptions as blocked. Whole-loop recovery reruns pure,
fixed fixture callbacks. Source hashes refuse active upgrades. There is no
production scheduler, durable product publication, live paid-provider claim,
concurrent ownership/cancellation protocol, arbitrary extension safety, native
migration or Python codemode recovery. Full parity's unsafe interrupted-error
continuation is required later by DE13/DE24.

| Evidence ID | Reproduction / exact assertions | Current result |
| --- | --- | --- |
| E00-TEXT | `uv run pytest tests/test_durable_prototype.py -q`: process cuts, SIGKILL, SQLite refusal/ack loss, one owner, admission identity, pinned correlations, policy intersection, committed read reuse | Passed together with E00-LOOP: 105 tests, exit 0, no skips (`focused-tests-2.log`) |
| E00-LOOP | `uv run pytest tests/test_durable_loop_integration.py -q`: hard cuts, SIGKILL, receipt reuse, rich JSON fields including `compare=False`, exact usage, termination, separate-reader publication refusal, drift/corruption and immutable terminal outcomes | Same combined 105-test run; not an additional 105 tests |
| E00-MATRIX-TEXT | `uv run python scripts/durable_prototype/experiments.py --output <private-scratch>`; assert 33 reported cases, repeated resumes, effect/attempt/result counts and SQLite integrity | Exit 0: 28 done, 4 expected unsafe-blocked, 1 owner probe; 2 SIGKILL cases; no skips |
| E00-MATRIX-LOOP | `uv run python scripts/durable_prototype/loop_experiments.py --output <private-scratch>`; assert 46 reported cases, repeated resumes, rich usage, observation/effect counts and integrity | Exit 0: 42 done, 4 expected unsafe-blocked; 2 SIGKILL cases; no skips |
| E00-ISOLATION | Inspect production imports, packaging entry points and exact prototype/test content against the frozen handoff | Exit 0 (`provenance.log`): 369 production modules scanned; all seven Python experiment/test files byte-identical to baseline and handoff; no product/dependency diff |
| E00-CHECK | `just check`: Ruff, formatting, mypy and full pytest; inspect all skips and runtime availability | Exit 0 on final DE00-PRE1 tree, normal HOME: 12,896 passed, 2 skipped; `official-just-check-final.log`; earlier diagnostic failures remain separate |
| E00-PREK | `prek run --all-files` if `.pre-commit-config.yaml` exists; include new files explicitly | Not applicable: config absent; new Python files explicitly passed Ruff lint/format |
| E00-DOCS | `just docs-build`, rendered local file/anchor links and focused documentation checks | Exit 0 after DE00-PRE1: strict build, 887 local links across all 8 changed/new documentation pages, 44 focused docs-build/MCP audit tests; no skips or broken links |
| E00-REVIEW | Fresh independent Claude Code Opus 5.5 HIGH implementation review | CLEAN, zero findings, no skips/redactions/truncations; baseline `04cd3e190e4cae6d6198bfb89f18b1d8d6f49a3c`; `review-first-9uduk1h9/result.json` and `review-bundle.md` |
| E00-LAND | Owned paths/hunks only, green commit and confirmed push on `main` | `ad04766daefde48604f73112d1611f4389f40877`, tree `8488a8b2b004c0101613dc3555104efef69d7855`; remote SHA confirmed; `delivery-final.json` |

The initial parallel environment setup recreated an ignored local `.venv` using
Python 3.15.0. DE00 checks subsequently use a separate scratch venv with Python
3.14.8. One focused invocation failed during concurrent uv resynchronization
(`partial_json_parser` unavailable during import); it is retained as a tooling
failure, superseded by the successful 105-test repeat. The first full check was started with temporary HOME
contrary to the backlog's fixture-isolation rule. It is retained and evaluated,
but normal-HOME independent full verification remains a separate gate. Further
commands preserve HOME and rely on test fixtures for user-state/theme isolation.
No credentials or global configuration were copied or changed.

The first full runs exposed an imported documentation regression:
`test_mcp_parity_audit.py::test_q13_accepts_the_rows_of_the_fourth_triage`
requires the historical DEV-86–DEV-93 references. The frozen planning handoff's
standing-deferral replacement had removed DEV-87. DE00 restores that identifier
beside its durable DE25 work order; no test or product code was changed. The
failure reproduced independently and the entire 28-test MCP audit then passed.
Failed full-run logs remain evidence; they do not become green results because
the focused repair passes. A fresh independent normal-HOME full run owns the
repaired-tree gate.

### DE00-PRE1 — publish complete preview-test configuration

Status: **landed with DE00; focused/full gates and independent review passed**, an explicitly
bounded DE00 verification prerequisite, not a
durable product feature. Independent verification found the existing
`test_preview_mirrors_changes_reloads_config_and_cleans_up_child` could time out
after its config save. Its `Path.write_text` truncates live `zensical.toml`
before writing the replacement. The real preview may read that empty interval:
`scripts/docs.py::configuration` parses an empty TOML document and rejects its
missing `project`, then preview cleanup stops the child. A later valid write
cannot revive the exited preview. This is a publication race, not a slower
server. Production configuration rejection remains fatal and unchanged.

The owned repair is limited to `tests/test_docs_build.py`: publish this one
complete config update through a sibling temporary file and `os.replace`.
Preserve every existing assertion, the 20-second page deadline, all other
timeouts and signal/cleanup checks. A small deterministic regression must force
the real configuration reader between truncation and writing: the reader sees
the previous complete live config until replacement, then the new complete
config. Recreating the former direct live-file write must fail this regression.
This does not promise recovery from invalid configuration or add a production
retry/last-known-good policy.

The deterministic regression passed with atomic publication. A separate test
process restoring the former direct writer failed at the real reader's
`config["project"]` with `KeyError`, proving that the injected interval executed
and detects the observed failure. The complete docs-build module plus MCP audit
passed **44 tests without skips**; lint and formatting passed. Full logs and
the exact mutation invocation remain in the local checkpoint, outside git.

Acceptance: the complete docs-build test module and MCP audit, strict docs build,
rendered links, lint and formatting pass. The coordinator then runs the full
gate on the changed test tree and includes this explicit prerequisite in the
DE00 independent review. Full-run evidence from the earlier test tree cannot
close that new gate. No timeout increase, assertion removal, product-code edit
or separate product-feature claim is authorized by this prerequisite.

## Slice status

`OPEN` means no accepted delivery; `LANDED` rows retain their accepted evidence
below. Dependencies and full
acceptance cuts are defined by the work order; splitting a row must preserve
that coverage here. A local validation result is not a landed slice.

| Slice | Work | Status | Evidence / review / main commit / push |
| --- | --- | --- | --- |
| DE00 | Experiment and work-order import | LANDED | E00-*; Opus 5.5 HIGH CLEAN; main/pushed `ad04766daefde48604f73112d1611f4389f40877`; CI 39236305603 success |
| DE01a | Pinned bootstrap, trace/manifest contract and actual Kernel/Memory reference probe | LANDED | `97d623f`; initial CI failed; repaired by R1 `35142ec`; Python adapter OPEN |
| DE01b1 | Strict multi-case/provenance/projection; H1/C1; lost-field/stale-head negatives | LANDED | `7acf5f7`; actual independent CLEAN and CI10/10; historical first ISSUES retained below; all P OPEN |
| DE01b2 | H2 SQLite recovery, H3 abort, E1 actual env; duplicate-effect/post-abort-write negatives | LANDED | `28bc886`; actual independent CLEAN0; CI39286240678 success10/10; historical first ISSUES retained |
| DE01b3a | Fresh executable local acceptance, hardware/exported workloads, four fresh mutants, exhaustive mapping | LANDED | Commit e3b2b391; actual CLEAN and CI39302403566 success10/10; reconciliation below |
| DE01b3b | Mandatory real pinned CI, selective artifacts and successful pushed-job proof | LANDED | `13d061d2`; actual Opus5.5 HIGH CLEAN; CI39370449156 success11/11 and uploaded artifact audited; delivery below |
| DE02 | Records, memory storage and atomic session core | LANDED | Cumulative a1+a2+b all delivered (b closed by DE02b3 d2e62390); original parent acceptance retained; no P completion |
| DE02a | Owned records/values, Storage port and detached atomic Memory | LANDED | Cumulative a1+a2 both delivered; no P completion |
| DE02a1 | Owned finite JSON, versioned records and Storage Protocol/DTOs | LANDED | b1aa0562; actual Opus5.5 HIGH CLEAN; CI39387254837 success11/11 and artifact audited; historical ISSUES retained |
| DE02a2 | Atomic detached Memory, raw-base seam, ancestry/indices/concurrency and native storage comparisons | LANDED | Cumulative a2a+a2b both delivered; original backend acceptance retained |
| DE02a2a | Internal atomic table Memory and current native/reference oracle | LANDED | 85390e2b; actual Opus5.5 HIGH CLEAN; CI39408774643 success11/11, complete logs/artifact audited; review history preserved |
| DE02a2b | Current raw-base documents in same candidate and cumulative bounded Protocol proof | LANDED | 72253352; Opus5.5 HIGH ISSUES (one Suggestion) repaired, Codex gpt-6.1-sol HIGH delta CLEAN; push verified; CI not a delivery gate (run 39545767044 started, not awaited) |
| DE02b | Session/Tx admission, seal/guard/adopt/publication/close | LANDED | Cumulative b1a+b1b+b2+b3 all delivered; all original Session/draft/failure obligations retained |
| DE02b1a | Private guarded Storage and lifecycle owner over real Memory | LANDED | 992c2102; Codex gpt-6.1-sol HIGH ISSUES (one Critical, one Warning) repaired by Opus5.5 HIGH, delta CLEAN; push verified; no CI claim |
| DE02b1b | Actual pinned Node guard oracle and paired guard recipes | LANDED | cb38caf3; Codex gpt-6.1-sol HIGH ISSUES (two Warnings) repaired by Opus5.5 HIGH, delta CLEAN; push verified; no CI claim |
| DE02b2 | First internal Session/Tx over the same owner | LANDED | Cumulative b2a+b2b both delivered (be6ee84e, eb1e3ba7); callback API decision recorded before code |
| DE02b2a | Private Tx core and mutable raw-base drafts, no commit entrypoint | LANDED | be6ee84e; Codex gpt-6.1-sol HIGH ISSUES (one Critical, one Warning) repaired by Opus5.5 HIGH, delta CLEAN; inventory-name repair delta CLEAN; push verified; no CI claim |
| DE02b2b | First exposed internal Session: line, async driver, publication and close | LANDED | eb1e3ba7; Codex gpt-6.1-sol HIGH r1 ISSUES (one Critical, four Warnings), r2 (one Critical, one Warning), r3 (one Critical, three Warnings; Root removed the claimer-delivery mechanism), r4 (one Warning), r5 CLEAN; push verified; no CI claim |
| DE02b3 | Cumulative paired Session acceptance and final b gate | LANDED | d2e62390; Codex gpt-6.1-sol HIGH r1 ISSUES (one Warning) repaired by Opus5.5 MAX, r2 CLEAN; local just check 14223 passed; push verified; no CI claim |
| DE03 | SQLite authority and local ownership | LANDED | Cumulative a+b+c+d (split 2026-10-21); every original cell retained |
| DE03a | Path ownership, owner-only files and minimal connection lifecycle | LANDED | 0d37f64a; Codex gpt-6.1-sol HIGH r1 three Warnings, r2–r5 one or two Criticals each (r4 harness non-convergence, Root simplification with an explicit contract boundary), r6 CLEAN, repaired by Opus5.5 MAX; local just check 14392 passed; push verified; no CI claim |
| DE03b | Schema v1, migration runner and open-time integrity | LANDED | f6024e56; Codex gpt-6.1-sol HIGH r1 ISSUES (one Warning) repaired by Opus5.5 MAX, r2 CLEAN; local just check 14459 passed; push verified; no CI claim |
| DE03c | SqliteStorage, contract cases, real failures and crash cuts | LANDED | Cumulative c1+c2 delivered at 1563bb68 and a2c4c6d2; every original cell retained |
| DE03c1 | SqliteStorage tables and backend-agnostic contract cases | LANDED | `1563bb68`; Codex HIGH r3 CLEAN after two repairs; local known docs-preview flake, isolated rerun 16/16; remote verified |
| DE03c2 | SqliteStorage documents, write refusals and crash cuts | LANDED | a2c4c6d2; final Opus5.5 HIGH R4 CLEAN unscoped, reviewed tree matched commit, push remote verified; known docs-preview exception recorded below |
| DE03d | Paired Pi SQLite oracle, Session over SQLite and P43 workload | LANDED | Cumulative d1+d2; all original acceptance retained |
| DE03d1 | Session over SQLite and bounded native current-base workload | LANDED | 8ab38d20; final Opus5.5 HIGH R4 CLEAN, owner known-preview-flake exception, isolated16 PASS, push/remote verified |
| DE03d2 | Closed paired SQLite oracle and final DE03 gate | LANDED | 2dba6008; final Opus5.5 HIGH R3 CLEAN, known-preview exception, two fresh chains, push/remote verified |
| DE04 | Minimal typed documents | LANDED | Cumulative acceptance below; local checks, retained prerequisites and actual paired proofs complete; normal commit/push remains the delivery gate |
| DE04a | Typed current documents and coupled task lifetime | LANDED | e6c1239d; exact Opus5.5 HIGH R3 CLEAN, green full check, fresh chains, push/remote verified; no CI claim |
| DE04b | Real operation/delta/checkpoint prerequisite | LANDED | Cumulative acceptance below; local checks, retained prerequisites and actual paired proofs complete; normal commit/push remains the delivery gate |
| DE04b1a | Pure decoded operations; depends DE02a1 | LANDED | 6e3b838f; exact Opus5.5 HIGH R4 CLEAN, green full check/docs, fresh pairs and push/remote verified; no CI claim |
| DE04b1b | Cumulative normalized tracking/current deltas/checkpoints | LANDED | Cumulative acceptance below; local checks, retained prerequisites and actual paired proofs complete; normal commit/push remains the delivery gate |
| DE04b1b1 | T pure provenance/normalized ops; depends DE04a + DE04b1a | LANDED | 71cb8c91; exact Opus5.5 HIGH R2 CLEAN, green full/docs/fresh chains, push/remote verified; no Session activation |
| DE04b1b2 | S independent current deltas/schema2; depends DE03d + DE04b1a | LANDED | 8c8f30d7; exact Opus5.5 HIGH R2 CLEAN, green full/docs/fresh chains, push/remote verified; no dependency on T |
| DE04b1b3 | J Draft/Tx/checkpoint/cache/publication; depends T + S + DE04a + owner policy266 | LANDED | Cumulative acceptance below; local checks, retained prerequisites and actual paired proofs complete; normal commit/push remains the delivery gate |
| DE04b1b3a | Tracker-backed default delta/count/publication join | LANDED | 33d39457; exact Opus5.5 HIGH R3 CLEAN/full/docs/fresh chains/push/remote verified; no checkpoint/unload claim |
| DE04b1b3b | Checkpoint callbacks, actual unload and final cumulative join | LANDED | Real policy/authority/unload with cumulative acceptance below; local checks and actual paired proofs complete; normal commit/push remains the delivery gate |
| DE05 | Minimal task runtime and recovery scheduler | OPEN | None; all gates OPEN |
| DE06 | Canonical checkpoint/request/outcome codecs; cumulative a+b join | IN_PROGRESS | Original acceptance retained; DE06a LANDED, DE06b OPEN |
| DE06a | Additive canonical DTO codec; depends DE02a1 | LANDED | caf4d34e; Opus5.5 HIGH R2 CLEAN unscoped, full local check green, two fresh chains and push/remote verified |
| DE06b | Actual task checkpoint/envelope integration; depends DE05 + DE06a | OPEN | None; all gates OPEN |
| DE07 | Durable generation phases | OPEN | None; all gates OPEN |
| DE08 | Sequential tool intents and blocked uncertainty | OPEN | None; all gates OPEN |
| DE09 | Completed-message projection | OPEN | None; all gates OPEN |
| DE10 | Compatibility, inspect and recovery resolutions | OPEN | None; all gates OPEN |
| DE11 | Opt-in Python product session | OPEN | None; all gates OPEN |
| DE12 | Opt-in headless CLI recovery | OPEN | None; all gates OPEN |
| DE13 | Unsafe top-level interrupted-error continuation | OPEN | None; all gates OPEN |
| DE14 | Full task recovery and definition migration | OPEN | None; all gates OPEN |
| DE15 | Ownership, waiting and completing | OPEN | None; all gates OPEN |
| DE16 | Durable cancellation and close | OPEN | None; all gates OPEN |
| DE17 | Sleep, retry, deferred poll and wake | OPEN | None; all gates OPEN |
| DE18 | Submission admission and deduplication | OPEN | None; all gates OPEN |
| DE19 | Inbox and failed-run policies | OPEN | None; all gates OPEN |
| DE20 | Historical context and named system state | OPEN | None; all gates OPEN |
| DE21 | Registry and agent configuration | OPEN | None; all gates OPEN |
| DE22 | Durable hooks and first-writer memos | OPEN | None; all gates OPEN |
| DE23 | Registry/resource reload and handover | OPEN | None; all gates OPEN |
| DE24 | Prepared and nested tool calls | OPEN | None; all gates OPEN |
| DE25 | Concurrent tool rounds and controls | OPEN | None; all gates OPEN |
| DE26 | Committed streaming and bounded progress | OPEN | None; all gates OPEN |
| DE27 | Document histories and retention | OPEN | None; all gates OPEN |
| DE28 | Convergent views, watches and task graph | OPEN | None; all gates OPEN |
| DE29 | Durable compaction | OPEN | None; all gates OPEN |
| DE30 | Fork, reset and handoff | OPEN | None; all gates OPEN |
| DE31 | Owned native subagents | OPEN | None; all gates OPEN |
| DE32 | Execution environment capabilities | IN_PROGRESS | Cumulative; waits DE32a + DE32b |
| DE32a | Pure streaming scanner | LANDED | 52ecc1fc; exact-tree Opus5.5 HIGH R2 CLEAN, green local full check, fresh chains/scanner proofs, push/remote verified |
| DE32b | Remaining environment adapters | OPEN | Every original DE32 acceptance retained |
| DE33 | Built-in tools and image semantics | OPEN | None; all gates OPEN |
| DE34 | Tool search and codemode discovery | OPEN | None; all gates OPEN |
| DE35 | Python WASI deterministic start/journal | OPEN | None; all gates OPEN |
| DE36 | Codemode replay and divergence barrier | OPEN | None; all gates OPEN |
| DE37 | Atomic codemode store/results and outputs | OPEN | None; all gates OPEN |
| DE38 | Codemode catalog/classify/image globals | OPEN | None; all gates OPEN |
| DE39 | Telemetry and host reports | OPEN | None; all gates OPEN |
| DE40 | Portable JSONL backend | OPEN | None; all gates OPEN |
| DE41 | Legacy migration and unified persistence | OPEN | None; all gates OPEN |
| DE42 | Terminal recovery integration | OPEN | None; all gates OPEN |
| DE43 | JSON/RPC and public SDK parity | OPEN | None; all gates OPEN |
| DE44 | Native default adoption | OPEN | None; all gates OPEN |
| DE45 | Independent end-to-end acceptance | OPEN | None; all gates OPEN |

## Parity status

Every requirement below remains **OPEN**. Tests from E00-* may inform a later
implementation; none is an upstream reference comparison. `None` in the evidence
column means no Python conformance test/reference fixture/review/main commit has
been accepted for that row. DE01 establishes the fixture schema and oracle;
implementing slices then append exact test/fixture IDs, review verdict, main
commit, pushed ref and any explicit platform exclusion from plan section 1.

| Requirement | Contract index | Owning slices | Status | Python tests / reference fixtures / review / main commit |
| --- | --- | --- | --- | --- |
| P01 | Atomic commit/adoption | DE02–04 | OPEN | None |
| P02 | SQLite schema and migration | DE03, DE27 | OPEN | None |
| P03 | Alternative backend semantics | DE02, DE40 | OPEN | None |
| P04 | Typed document lifecycle | DE04, DE27 | OPEN | None |
| P05 | Document migration, forks and cache | DE27, DE30 | OPEN | None |
| P06 | Task phases and recovery | DE05, DE14 | OPEN | None |
| P07 | Definition/restart/blocked behavior | DE14, DE23 | OPEN | None |
| P08 | Durable sleep and wake | DE17 | OPEN | None |
| P09 | Owned work and waits | DE15, DE31 | OPEN | None |
| P10 | Abort barrier and close | DE16 | OPEN | None |
| P11 | Submission lifecycle | DE18 | OPEN | None |
| P12 | Inbox selection | DE19, DE30 | OPEN | None |
| P13 | Failed-run input settlement | DE19 | OPEN | None |
| P14 | Context and system baseline | DE20, DE30 | OPEN | None |
| P15 | Rich canonical fields | DE06–08, DE24, DE33 | OPEN | None |
| P16 | Pinned request/provider identity | DE06, DE07, DE10, DE12 | OPEN | None |
| P17 | Provider interruption and result reuse | DE07, DE17, DE26 | OPEN | None |
| P18 | Retry/backoff/overflow | DE17, DE29 | OPEN | None |
| P19 | Usage/cache/cost fidelity | DE06, DE07, DE26, DE29–31 | OPEN | None |
| P20 | Tool validation/preparation/replay | DE08, DE21, DE24 | OPEN | None |
| P21 | Unsafe interrupted-error continuation | DE13, DE16, DE24, DE31 | OPEN | None |
| P22 | Tool rounds and controls | DE25, DE30 | OPEN | None |
| P23 | Nested tool lifecycle | DE24, DE25 | OPEN | None |
| P24 | Registry and host boundaries | DE21, DE23 | OPEN | None |
| P25 | Hook composition and memos | DE22 | OPEN | None |
| P26 | Reload and version transitions | DE23, DE32 | OPEN | None |
| P27 | Committed live progress | DE26, DE32–33 | OPEN | None |
| P28 | Views and subscription baseline | DE09, DE28 | OPEN | None |
| P29 | Task graph and cache bounds | DE27–28, DE31 | OPEN | None |
| P30 | Compaction | DE29 | OPEN | None |
| P31 | Fork/reset/handoff | DE27, DE30 | OPEN | None |
| P32 | Per-conversation agents and subagents | DE31 | OPEN | None |
| P33 | Execution environments | DE32 | OPEN | None |
| P34 | Built-in tools and images | DE33 | OPEN | None |
| P35 | Deferred discovery | DE34 | OPEN | None |
| P36 | Codemode deterministic replay | DE35–36 | OPEN | None |
| P37 | Codemode divergence and cleanup | DE35–36 | OPEN | None |
| P38 | Codemode globals/store/outputs | DE34, DE37 | OPEN | None |
| P39 | Codemode model operations | DE38 | OPEN | None |
| P40 | Telemetry | DE39 | OPEN | None |
| P41 | Native SDK/CLI/TUI/RPC adoption | DE11–12, DE41–44 | OPEN | None |
| P42 | Private data lifecycle and migration | DE10–12, DE40–44 | OPEN | None |
| P43 | Conformance and performance evidence | DE01, every slice, DE45 | OPEN | None |

## Next delivery gate

DE04a is LANDED at `e6c1239d`; pure decoded-operation DE04b1a is LANDED at
`6e3b838f`, with exact final review/full/docs/fresh-pair/push gates verified.
DE04/DE04b/DE04b1b remain IN_PROGRESS. S (DE04b1b2) is verified LANDED at
`8c8f30d7`, with exact final review/full/docs/fresh-chain/push gates recorded in
the [S delivery successor](#de04b1b2-verified-delivery-and-t-integration-successor).
T (DE04b1b1) is verified LANDED at `71cb8c91`; the [delivery and J admission](#de04b1b1-verified-delivery-and-owner-selected-j-admission) records its final gates and the owner decision.
J (DE04b1b3) is IN_PROGRESS: owner policy266 selects the persisted/replayed value
for callbacks, cache and publications. DE04b1b3a is verified LANDED at `33d39457`; [delivery and successor admission](#de04b1b3a-verified-delivery-and-de04b1b3b-admission) records its final gates.
DE04b1b3b is IN_PROGRESS with real checkpoints, scoped callback authority, actual
unload and the final cumulative DE04 gate. No further split is needed before code. The [delivery/admission successor](#de04b1a-verified-delivery-and-de04b1b-pre-code-admission)
retains verified B1a gates and the exact dependency map. DE05 waits full DE04;
DE27 retains full history/as-of/fork/migrations. All 43 P rows remain OPEN.

DE00 through DE03 remain delivered; DE03c is LANDED and DE03d is LANDED.
DE06a is LANDED; DE06b is OPEN, parent DE06
IN_PROGRESS. DE32a is LANDED, DE32b OPEN and parent DE32 IN_PROGRESS; DE33 waits
full DE32. Codex owns implementation/repairs and the installed Claude Code
`claude-opus-5-5` HIGH harness owns independent delivery review. Root alone owns dispatch,
shared metadata, final gates and landing. This pre-code metadata folds into the
first actual child landing; it is not a planning-only delivery. GitHub CI is not
claimed. Historical roles and dated outcomes below remain unchanged.

## DE00 delivery reconciliation (2026-10-19)

The coordinator verified delivery at `/tmp/pipy-de00-gate-20261019.G2vxIC/`:
`delivery-final.json`, `official-just-check-final.log`, `review-manifest-audit.json`,
`review-first-9uduk1h9/result.json`, `ci-state-final.json` and
`ci-log-audit-final.json` retain full local evidence. The exact reviewed tree is
`8488a8b2b004c0101613dc3555104efef69d7855`, landed/pushed at
`ad04766daefde48604f73112d1611f4389f40877` on `main`.
[CI run 39236305603](https://github.com/ephes/pipy/actions/runs/39236305603)
completed all ten jobs successfully. Each full Python 3.11/3.14/3.15 CI suite
reported 12,690 passed and 208 skipped (not passes; exact skipped IDs were not
printed by those runs). The two runtime-required codemode jobs each passed 441
tests without skips with `PIPY_CODEMODE_REQUIRE_RUNTIME=1`; four real-PTY jobs
each passed 17 tests. The quality/docs job passed. These gates cover the DE00
import, not Pi-reference execution or any P-row parity. The historical plan
review remains ISSUES/Suggestions-only, separately from the implementation CLEAN.

## DE01 split acceptance

DE01a owns only reproducible pinned bootstrap, strict versioned trace/manifest
contract, a real minimal Kernel/MemoryStorage reference run and infrastructure
negatives. DE01b retains semantic Harness/tool/env fixtures, lost-field/duplicate-
effect/post-abort-write/stale-head oracle cases, hardware/reference workloads,
complete DE/P mapping and mandatory explicit reference CI. All original DE01
acceptances remain required; no row is dropped. The Python durable adapter is
OPEN until a later implementing slice provides real operations. Current CI has
no reference execution, and its green result cannot imply that reference gate.

## DE01a local reference evidence

The following local-phase entries preserve their then-open statuses and first
ISSUES verdict. Actual subsequent DE01a/R1 landing and CI are recorded under
[historical landing](#historical-de01a-and-r1-landing-de01b-split-before-implementation).

Status: **implemented locally; independent review and landing OPEN**. See
[reference reproduction and contract](../durable-reference.md). The exact final
bootstrap command was `uv run python scripts/durable_pi_comparison.py reference
--pi-source /Users/jochen/src/pi-mono --output <new-private-scratch>`. It returned
0 after an actual locked install and kernel execution, producing ten ordered
steps and 702 checked module paths. No product code or Python durable kernel
exists in this slice; P01–P43 remain OPEN.

The local evidence root is `/tmp/pipy-de01a-s8jg5gbw/`. Command metadata includes
full logs, exits and before/after owned-file hashes. `reference-freeze/run.json`
and its command logs contain pinned ref/tree/source/archive/lock/runtime,
installed dependency integrities, driver/fixture/oracle hashes, module hashes
and the actual trace. Both fixture recording and a fresh full bootstrap ran
successfully. The earlier bootstrap's caller code changed during collection;
`reference-freeze` is the final runner evidence after checkout-root validation,
not those earlier collections.

| Evidence | Result and limit |
| --- | --- |
| Kernel/Memory reference | Exact Pi 1.3.0 pin, Node v26.12.0/npm 11.20.0; actual commits, rollback, prior snapshot stability, publications and close matched `kernel-memory-v1` |
| Known trace mutation | Actual `compare` command returned 1 for altered committed count; no parity claim from equal traces |
| Missing prerequisites | Actual commands returned 2 for missing fixture, ref and Node; unit tests additionally cover runtime/dependency/hash/schema drift |
| Missing actual dependency | A fault injection after successful bootstrap/validation removed physically resolved typebox: actual Node child exit 1 became outer runner exit 2, never reference success (`missing-dependency-probe-result.json`); earlier recording-snapshot probe was restored; no upstream mutation |
| Infrastructure tests | 81 focused tests, no skips; strict JSON/schema, mismatch, module/lock provenance, environment and deadline cleanup |
| Full/docs checks | Exact command logs and outcomes recorded in the local checkpoint at freeze; no absent runtime or skip substitutes for a reference pass |
| Independent review / landing | First Opus 5.5 HIGH review: ISSUES (one Warning, one Suggestion); accepted local repairs below, scoped re-review and landing OPEN |

The full upstream MIT notice is committed beside the synthetic fixture. Four
precise upstream extraneous lock records are identified separately as physically
absent, never as installed or used modules. DE01b retains all remaining original
DE01 acceptance, including the four semantic oracle negatives, real fake-Harness/
tool/env fixtures, hardware/workloads, complete DE/P mapping and explicit pinned
reference CI. No current CI success is labeled a Node/reference gate.

### DE01a numeric gate repair

The independent precheck found that decimal/exponent JSON integers could bypass
version 1's safe-integer rejection: `9007199254740992.0` and
`9007199254740993.0` both parse to the same Python float. The earlier comparator
therefore incorrectly reported equality. The repaired finite-value validator
applies the same safe-integer limit to integral floats as to Python integers,
matching the existing JS driver's `Number.isInteger`/`Number.isSafeInteger`
checks. The contract and fixture remain unchanged; precision loss is not an
accepted normalization.

The original actual CLI repro now returns 2. Regressions cover decimal and
exponent spellings, signed unsafe values, safe boundaries, direct trace/compare
calls and actual CLI subprocesses. Boolean `true`/`false` remain distinct from
numeric 1/0, including float representations. The focused suite passes 63 tests
without skips; explicit Ruff, format and mypy checks pass.

The repaired runner was exercised in another actual pinned bootstrap, exit 0,
with matching ten-step trace and the same checked 1,961 source files, 702 modules,
346 present dependency records and four separately identified absent extraneous
records. Its new oracle hash and full evidence are retained at
`/tmp/pipy-de01a-numeric-hi9s6gmu/reference-numeric/run.json`; full focused and
original-repro logs are beside that directory. Earlier reference manifests attest
the earlier candidates, not the repaired runner. The local checkpoint records
the new twelve-file freeze. Independent full checks, review and delivery remain
coordinator-owned and OPEN.

### DE01a accepted provenance and invalid-evidence review repairs

The actual independent Opus 5.5 HIGH review returned **ISSUES**, with one
Warning and one Suggestion, no scope omissions. Its result is retained at
`/tmp/pipy-de01a-gate.OJD8G0/numeric/review-first/result.json`; the reviewed
baseline is `70709a2341b1811df815872ee4a7d44708483007`. The coordinator accepted
both findings. Local repairs do not turn that verdict into CLEAN; the scoped
independent re-review, final full check and landing remain OPEN.

The Warning demonstrated two pinned-source provenance bypasses: a workspace
specifier could resolve under a nested `node_modules` directory, and an extra
unarchived source file could pass merely by living under `packages/`. Workspace
specifiers now reject any `node_modules` component. The resolution validator
requires the actual pre-install archive inventory and checks every non-registry
module's membership and exact SHA. No source exception or instrumentation
allowlist is introduced. The driver itself is not recorded by its hook and
retains its separate exact caller artifact hash; the preloader retains its
separate archived-source hash.

The Suggestion demonstrated malformed evidence escaping as AttributeError and
process exit 1, which belongs only to valid trace mismatches. Explicit object
and string validation now precedes lock `.items`/`.get` and resolution
`.startswith` operations. Invalid lock-package objects, lock records and
resolution entries raise EvidenceError, so the outer runner returns 2. The
exception catch was not broadened.

The focused suite now passes 81 tests without skips, including both original
provenance probes, exact source-hash drift, malformed object/string cases,
subprocess CLI error-boundary tests returning 2 and an actual valid-mismatch
CLI test returning 1. Explicit Ruff, format and mypy checks pass. A fresh actual
pinned reference bootstrap with the repaired runner returned 0 with the same
ten-step fixture, 1,961 unchanged archived files and 702 verified module files.

Three additional full-run fault probes used fresh private snapshots and real
locked installs: both provenance corruptions were injected after successful
actual kernel execution; malformed installed `packages` was injected after
successful `npm ci`. Every outer runner returned 2, emitted the specific evidence
error and produced no reference-success manifest. The probe wrapper's exit 0
means these expected refusals occurred, not reference success. Exact commands,
full logs, exits and before/after owned hashes live under
`/tmp/pipy-de01a-reviewfix-dksx2jzb/`, including `reference-repair/run.json` and
`review-fault-probes-results.json`. The checkpoint records the new twelve-file
freeze and the unchanged future DE01b boundaries. No P row is closed.

## DE01a-R1 — isolate the two evidenced CI test prerequisites

Historical writer-freeze status: **local test-isolation repair verified; independent gates and landing OPEN**.

| R1 part | Concrete prerequisite | Boundary |
| --- | --- | --- |
| R1a | Missing-npm unit test uses the manifest's successful Node-version response | No host Node prerequisite, runner or pin change |
| R1b | Existing PTY selector test uses its neighbor's watcher/true-prompt fence | No product flush change, weaker assertion or longer timeout |

Base: `97d623f599e43eeb856cf5566ad5411d8d962830` on main. The previous
independent CLEAN and landed commit remain historical evidence, unchanged.
Post-push [CI run 39247137514](https://github.com/ephes/pipy/actions/runs/39247137514)
newly failed `test_missing_runtime_is_error_not_pass[npm]` on Python 3.11 and
3.14: the unit test called real host Node before its missing-npm injection.
A non-pinned or unavailable host Node therefore ended the runner before npm was
attempted. This is a unit-test isolation defect; the reference runner correctly
refuses runtime drift. The initially separate unchanged PTY failure was subsequently admitted as
explicit R1b scope, below, before any PTY edit.

Before changing the test, reproduce the failure with a synthetic foreign Node
version and with no Node on PATH. R1a supplies only the preceding successful Node
version response from the fixture manifest and rejects unexpected stub commands.
Preserve missing-binary FileNotFoundError, outer exit 2 and the exact attempted
binary assertion for both node/npm variants. Never relax the reference pin,
install Node in ordinary CI as a workaround, change PTY assertions/timeouts or
advance the DE01b reference-CI boundary. Owned paths are this evidence ledger,
the durable comparison test module, the one targeted terminal PTY test module
and release notes. This four-file scope was explicitly extended before the PTY
change; it is not an unrecorded addition to the npm fix.

Acceptance: deterministic before/after host-version/unavailable probes, focused
checks on available real Python 3.11/3.14/3.15 in separate environments with normal
HOME, explicit lint/format/mypy and docs/rendered links. The coordinator owns the
definitive full check, a new independent full-hotfix Opus 5.5 HIGH review and
separate commit/push. No amend or force-push. No product, runner, manifest,
reference-source or other PTY-test change is included.

R1 local verification uses `/tmp/pipy-de01a-r1-p6iwkb8p/`. An exact private
archive of the base commit's comparison scripts, fixture and test module retains
the old behavior without rewriting main: targeted tests fail only the npm case
with either synthetic `node --version` = `v0.0.0` or no Node on PATH. The repaired
targeted tests pass both variants in both environments. The synthetic PATHs keep
`ps` available for the unrelated cleanup fixture. Earlier diagnostic PATHs also
hid `ps`, causing an extra teardown error; those logs are retained and superseded
by the corrected before/after probes.

The test now returns the manifest's exact successful Node-version response before
its missing-npm injection. Any other unexpected prerequisite command fails the
test rather than escaping to host execution. Both missing-binary paths still
raise FileNotFoundError and assert outer exit 2 and the exact attempted binary.
The runner, manifest, runtime pin, schema and timeout are byte-unchanged
from the base commit.

Fresh separate locked environments using real Python 3.11.13, 3.14.8 and 3.15.0
each pass all 81 focused tests without skips. Their sync, Python-version and test
commands retain complete logs, exit codes and before/after owned hashes; HOME
remains normal. Explicit Ruff lint/format and mypy pass. Documentation build and
rendered links are recorded at freeze. No actual Pi reference run is claimed by
these unit tests, and no new reference run is required for this unchanged runner.
No broad full check or review was run by the writer; the coordinator owns the
new R1 independent full-hotfix gate and subsequent separate commit/push.

### R1b scope extension before the PTY test edit

The coordinator explicitly expanded the same small R1 repair to the existing
`test_pty_tree_selector_escape_label_and_filter`, alongside R1a's npm unit test.
The target previously interpreted the first raw-mode acknowledgement after a
provider MARK as final prompt readiness. A late active-turn watcher can emit
that acknowledgement before the true outer prompt acquires raw mode; queuing
`/tree` then lets the unchanged TCSAFLUSH discard its bytes.

A deterministic real-PTY witness at
`/tmp/pipy-de01a-pty-scout.Ucdc3Z/echo-drained/` demonstrates this race class on
Python 3.14.8: MARK offset 13,526, watcher ACK 14,580, held true prompt 18,488,
true prompt ACK 18,513. The former helper accepted the watcher ACK, six `/tree`
bytes were queued, and FIONREAD changed from six before unchanged `tty.setraw`
to zero afterward; the selector title never appeared. Cleanup ended the worker.
This does not establish that the abbreviated CI capture used that exact
interleaving. The scout's earlier incomplete probe remains separate evidence.

R1b reuses the existing `_TreeMarkProvider(wait_for_watcher=True)` and
`_tree_turn_readiness` fence already used by the immediate rehydration neighbor.
Order watcher acquisition before MARK, hold the true prompt, prove that no
post-MARK readiness acknowledgement exists while held, release it and wait for
the true acknowledgement before sending `/tree`. Preserve one monotonic
first-turn budget, cleanup release, all label/filter/cancel/provider/history
assertions, timeouts and product TCSAFLUSH behavior. No new generic helper or
product change is authorized. Focused acceptance includes the exact real-PTY
case and adjacent existing readiness tests plus a meaningful old/new witness
where practical. The independent full check and fresh whole-R1 Opus review cover
both R1a/R1b before a separate repair commit/push.

R1b local checks pass the exact real-PTY case, its immediate rehydration neighbor
and all `test_pty_sync.py` cases on Python 3.11.13, 3.14.8 and 3.15.0: 16 passed,
no skips in each environment. The existing helper and every other test/class
definition remain unchanged; an AST audit also confirms R1a changes only its
one intended test definition. Lint and formatting pass for both touched test
modules. Mypy passes with the repository's local `src` included; an earlier
partial invocation lacked that source discovery and is retained separately,
without new ignores or product changes.

A private diagnostic re-executes the exact base commit's old target function
in memory, leaving repository files unchanged, under the original forced-late-
watcher witness. It fails at the selector-ready assertion with MARK 13,526,
watcher ACK 14,580, held prompt 18,488 and true prompt ACK 18,513; six queued bytes
become zero after the unchanged flush. The selector title is absent and cleanup
ends the worker. The base source hash was checked against its actual Git object.

The repaired target was separately observed without forcing that now-impossible
watcher order and without adding gates, sleeps, acknowledgements or echo drains.
It passes with watcher ACK 13,511 before MARK 13,534, held prompt 18,488 and true
ACK 18,512. The zero-time readiness check while held returns none; true ready-end
18,520 equals the captured-output end before `/tree` is queued. The selector
title appears at 25,276 and the worker ends. The existing test still checks
label persistence, filter cycling, Escape cancellation and no extra provider
turn. This establishes the race class and repaired fixture ordering, not the
exact scheduling of the abbreviated original CI capture.

Full logs, original/fixed captures and checked offsets are retained under the
R1 evidence root, including `pty-old-new-audit.json`, `scope-ast-audit.json` and
the two private witness directories. The combined four-file freeze preserves
all runner/product/manifest bytes and both previous commit/review histories.
The coordinator's independent final full check and fresh whole-R1 review remain
OPEN; no worker review, stage, commit or push has occurred.

## Historical DE01a and R1 landing; DE01b split before implementation

DE01a landed at `97d623f599e43eeb856cf5566ad5411d8d962830` after independent
Opus 5.5 HIGH follow-up CLEAN (`/tmp/pipy-de01a-gate.OJD8G0/reviewfix/review-delta/`,
recorded baseline `26121262b6ba819ee37482059bf823f2dc6d5e81`). Its initial
[CI 39247137514](https://github.com/ephes/pipy/actions/runs/39247137514) failed;
that failed result and earlier ISSUES remain history, not retroactive successes.
R1 independently fixed the evidenced npm prerequisite and PTY readiness tests.
Its first review was ISSUES; the coordinator rejected the paint-only warning
using unchanged readiness-helper dispatch and an actual probe, and accepted the
missing-table-blank-line suggestion. The resulting Opus 5.5 HIGH follow-up was
CLEAN, result `/tmp/pipy-de01a-r1-gate.veuqv9ul/doc-table/review-delta/result.json`,
recorded baseline `b2087d66ddb995491fc4ecfe2f25eb5eb2582789`.

R1 landed at `35142ec28f720f010ffe670de86a2bc9f0cc016e`, tree
`9d6a5d5d56daa48907441f7b46783d41e7e0e733`, independently confirmed as remote
main. [CI 39255373624](https://github.com/ephes/pipy/actions/runs/39255373624)
succeeded in all ten jobs. Each full Python 3.11/3.14/3.15 job recorded 12,771
passed and 208 skipped; those skips are not passes and exact IDs were not printed.
Each of two required-WASI jobs passed 441 with zero skips; four real-PTY jobs
passed 17 each with zero skips. Full delivery/log audit:
`/tmp/pipy-de01a-r1-gate.veuqv9ul/delivery/delivery-final.json` (141 log files,
12,676 lines audited). These jobs do not execute the durable Node reference.

Before DE01b implementation the authorized delivery split is b1 -> b2 -> b3,
then DE02. b1 owns strict registry/provenance/projection and H1/C1 with two
schema-valid semantic negatives; b2 retains actual H2/H3/E1 and the other two
negatives; b3 retains hardware/workloads, exhaustive mapping and mandatory real
reference CI. All original DE01 acceptance remains required, all P rows OPEN,
and the Python durable comparison adapter remains absent. No b1 gate or review
is claimed by this pre-implementation index entry.

## DE01b1 — multi-case reference evidence

Historical writer-freeze status: **implemented locally; independent full check/review/landing OPEN**.
Base is R1 `35142ec28f720f010ffe670de86a2bc9f0cc016e`, confirmed clean on main
before the delivery-index split and implementation. All P01–P43 rows and the
absent Python comparison adapter remain OPEN. DE02 remains blocked by b3.

The strict v2 registry executes exactly kernel-memory-v1, harness-round-v1 and
context-heads-v1. Four exact MTS artifacts and both Python artifacts form closed
hashed inventories; original sources, caller adapters and locked registry
modules have separate provenance. No caller directory is an archive exception.
New raw traces use collision-free tagged JS values; expected/actual projection
and pointer witnesses are derived by the same Python owner while raw evidence
is retained. The kernel fixture is byte-unchanged, SHA-256
`a74321597569dbe95e2d82516e4559ebf9b839c1f9f87156157879c3d48a5ef1`.

H1 is an explicit source adaptation combining the named upstream tool-round
and explicit-null-details tests. Its actual run settles done with five complete
entries, three terminal tasks, one echo invocation and two provider requests.
It retains the own null details, offered schema, structured diagnostics,
call/task/submission correlations, nonzero usage/cache counters, reports and
close. Faux's API is a fixed input, not normalized random text; historical
message/Harness clocks are explicit, and actual tool duration is retained raw.
C1 executes actual self-head reset, assistant, summary and tail. Full history
and both ContextViews preserve head-marker identity versus target, contributions,
messages and final cutoff. No hand-authored expected runtime trace is used.

A final fidelity check found a real pre-freeze oracle defect: globally swapping
C1 old/reset IDs consistently could hide reversed ID-ordered history while
remaining schema-valid. Source `packages/durable/docs/spec.md:259,5089,5179`
defines ordered entry IDs and ID scans. The narrowly accepted repair retains
adjacent order relations only for recorded history and H1 task scans. It neither
sorts events nor imposes cross-kind allocation order. The original equal-0
probe and mutant remain in `/tmp/pipy-de01b1-tgygcjxb/c1-id-order-probe.json` and
`c1-id-order-mutant.json`; repaired CLI regressions require valid mismatch 1.
Earlier reference runs remain evidence only for their earlier exact code hashes.

The initial frozen candidate passed 153 tests with zero skips on each real Python
3.11.13, 3.14.8 and 3.15.0, using normal HOME and isolated existing environments.
It covers both valid semantic negatives, tagged undefined/null/literal fidelity,
closed manifests/adapter modules, global ID collisions and scan-order changes,
provider/duration copy correlation, arbitrary payload preservation, finite JSON,
missing evidence, privacy and deadline cleanup. Explicit lint/format/mypy and
rendered documentation verification are included in the freeze logs.

Every actual complete run uses a fresh private exact-commit archive and original
locked scratch npm installation. Source/dependency inventories and per-child
commands, full stdout/stderr, exits and deadlines are retained under
`/tmp/pipy-de01b1-tgygcjxb/`. Actual dependency refusal removes a previously
resolved physical typebox module from a private COPY of a successful snapshot:
Node exits 1, the outer runner exits 2, and no success manifest is produced.
The original successful snapshots and upstream remain unmodified. Missing
Node/ref/fixture and drift also exit 2; actual H1 lost-field and C1 stale-head
mutants validate and exit 1, while unmutated traces exit 0.

Remaining acceptance is explicit: b2 still owns H2 SQLite safety intersections,
H3 durable abort/late-write refusal, E1 real env and duplicate-effect/post-abort
mutants; b3 still owns exported workload/hardware measurement, exhaustive DE/P
mapping and mandatory real pinned reference CI with pushed-job evidence. None
of those boundaries is satisfied by b1 or ordinary Python CI. The upstream
MIT notice (Mario Zechner 2025) remains complete and adapted files cite it.

A final provenance guard also pins manifest bytes from before validation through
execution and final revalidation. Its deterministic isolated control-flow
regression changes a previously compared golden and its matching manifest hash:
the old path returns 0, the repaired path returns 2 with no success manifest.
This mocked bootstrap regression supplies no real reference evidence; its
before/after logs are `manifest-drift-before.log` and
`focused-manifest-guard.log`. The final actual runs after both accepted guards
are `reference-freeze-a/` and `reference-freeze-b/`; earlier run directories
remain tied to their earlier comparison-artifact hashes.


## DE01b1 first-review repair disposition

The first independent Opus 5.5/high review completed **ISSUES**, with three
Suggestions and no Critical or Warning findings. Its historical result remains
`/tmp/pipy-de01b1-gate.bUqBfmEu/review-first/result.json`, baseline
`e6dcfe461f3f0dd287b768468d9d476c71241859`, reviewed tree
`3165f199809a8ddfa03f44527d2dc5d60b72bdee`. The coordinator accepted all three
small, material repairs; this does not rename that review CLEAN.

- S1: reject sparse JS arrays before serialization, rather than aliasing holes
  to null or own undefined. The permanent actual-encoder regression is
  `tests/durable/test_reference_codec.mts`, explicitly invoked with pinned
  Node v26.12.0; it adds no Node requirement to ordinary pytest. The same test
  fails before the guard and passes afterward. Dense goldens remain unchanged.
- S2: mutate actual v2 `cases[i].trace_schema` and `cases[i].id` fields; boolean
  and floating-point schema versions are refused directly. The unsupported
  top-level schema-version-3 negative and parity-claim negative remain.
- S3: synchronize the next-gate paragraph and the authorized harness-spec
  summary to landed DE01a/R1 and sequential b1/b2/b3 before DE02.

The explicitly extended owned set is 21 files: the previous 19 plus
`docs/harness-spec.md` and the standalone MTS codec regression. Repair logs,
before/after hashes, two fresh pinned reference runs, semantic/prerequisite
negatives and rendered docs evidence are retained under
`/var/folders/79/dxyqffhn7753q_b9602pnfnm0000gn/T/pipy-de01b1-reviewfix-0zn2bzuq/`. The earlier freeze and its reference runs
remain evidence for their earlier exact hashes. A fresh independent full check
and scoped follow-up review are pending; the proved JS codec collision and its
real regression justify that follow-up. No b2 implementation, P-row closure,
Python durable adapter or reference-CI completion is claimed.

Repair-focused acceptance is 159 passed with zero skips on each real Python
3.11.13, 3.14.8 and 3.15.0, with normal HOME. Explicit Python lint, formatting
and mypy pass. The permanent Node regression records Exit 1 before the guard
and Exit 0 after it; a separate pre-fix probe records the actual hole/null
collision. The two repaired-candidate reference directories are again named
`reference-freeze-a/` and `reference-freeze-b/` under the new repair-log root;
their manifests bind the new exact MTS hash closure, with unchanged golden
hashes. All three cases and both schema-valid semantic negatives remain required.


## DE01b1 delivered; DE01b2 implementation admission

DE01b1 landed/pushed at `7acf5f7ea629d64f87db181032e0c3a0427c7821`, reviewed
tree `434403c4103f936ade9a07c011b3aa4a0d5b09c1`, after actual independent
Opus 5.5/high CLEAN with zero findings, result
`/tmp/pipy-de01b1-repair-gate.jYFB2tRX/review-delta/result.json`, SHA-256
`57a5982a8a21de53c7d3e3285eedd6bae23d56f8b36bfadcfd6e4cc77eab1741`, baseline
`99eb239cfe314919cd76bb22708a036d3c87e81d`. Its first ISSUES result above remains
historical. The coordinator independently audited remote SHA and
[CI39270913622](https://github.com/ephes/pipy/actions/runs/39270913622): all ten
jobs succeeded. Three full suites each reported 12,849 passed/208 skipped;
159 durable tests had zero skips; two required-WASI jobs passed441/0 skips;
four real-PTY jobs passed17/0 skips. Complete delivery evidence is
`/tmp/pipy-de01b1-repair-gate.jYFB2tRX/delivery/delivery-final.json`, including
141 logs/12,686 lines and hashes. Ordinary Python CI does not execute the pinned
reference or standalone Node codec test; b3 retains that mandatory CI gate.

DE01b2 is authorized from this clean delivered base, before implementation:
H2 actual private SQLite close/reopen across unsafe/unsafe, safe/safe and both
mixed stored/current policies; H3 actual durable mark and late memo/commit
refusal; E1 two actual exported Node environment conformance cases. Three new
case modules extend the closed registry to six families. Raw observations,
bounded typed projections and provenance remain required. Schema-valid
duplicate-effect/post-abort mutants must mismatch1 alongside existing lost-field
and stale-head mutants. All three existing goldens and the permanent separate
Node codec regression remain intact. Python adapter/all P rows remain OPEN;
b3 workloads/hardware/exhaustive mapping/mandatory actual reference CI still
block DE02. No b2 check, review or landing is claimed by this admission record.


## DE01b2 — real recovery, abort and environment fixtures

Status: **implemented locally; independent full check/review/landing OPEN**.
Base is delivered DE01b1 `7acf5f7ea629d64f87db181032e0c3a0427c7821`.
The strict manifest2/trace2 registry now contains six complete case families,
seven copied MTS artifacts and the same two hashed Python artifacts. Existing
kernel/H1/C1 golden bytes and the permanent standalone MTS codec test are
unchanged. All P01–P43 and the absent Python durable comparison adapter remain
OPEN. DE01b3 remains wholly required before DE02.

H2 executes four real private Node SQLite databases, one per stored/current
policy combination, retaining the same faux/models/registry/counter between
cooperative close and reopen. Actual effect counts are 1/2/1/1 for unsafe/unsafe,
safe/safe, safe/unsafe, unsafe/safe. Before-close running and pre-resume reopened
pending tasks retain execute checkpoints and durable partial output/details;
close, not a claimed crash recovery, accounts for pending. The provider identity
survives reopen; responses/cache are not reset. Final histories/tasks, usage,
errors/diagnostics and two provider requests remain complete. Each DB is closed,
retained privately and content-hashed in the required raw sidecar. The manifest
and result hashes attest source, adapters, fixtures and sidecars separately.

The first H2 runs correctly failed at the unchanged60s child deadline: actual
phase logs reached execute, then waited before started at api.details. A fixed
clock could not reach Chord Progress's throttle deadline after the preceding
output commit. Sources are `harness/tool.ts:350–357,462–475` and
`packages/chord/src/output.ts:374–403`; the original chat helper uses Date.now.
The narrowly authorized fixture adapts `harness-sleep.test.ts`'s controlled
clock/fakeTimers through supported now/setTimer hooks, queues foreground
callbacks asynchronously and advances only to their actual requested deadline.
Cancellation and background-decline semantics are explicit. Every timer fact
and semantic timestamp remains raw/significant, with no global patch or new
clock normalizer. The actual MTS helper probe confirms asynchronous callbacks,
cancellation suppression and no background clock advance. This is bounded
fixture scheduling, not arbitrary concurrent-timer/product performance proof.
The later all-four H2 probe completed; an intermediate DTO failure was retained
before replacing the Submission handle with its actual status record.

H3 executes the source-derived marked task on actual MemoryStorage. Immutable
commit publications retain seq5 mark and seq6 terminal records in this fixture;
the asynchronous run continuation follows the host's durable-mark observation.
Internal physical signal dispatch is not claimed to follow that host listener.
The kept memo reads1, late memo/commit both reject with actual durable-mark
errors, forbidden callback stays unentered, and one distinct abort runtime
commits aborted/mark. Task IDs/copies and only the exact registered error-ID
template use bounded projection; memos/events/seq/statuses remain significant.

E1 executes the two exact exported createEnvConformance cases against actual
NodeExecutionEnv in separate empty private directories: real positional bytes,
invalid/fractional-range and aborted/closed reads, FileInfo/Error DTOs, literal
argv, settled per-stream output, false pwned existence and awaited cleanup.
Only scoped cwd/FileInfo.path/FileError.path and FileInfo.mtimeMs are projected;
raw fractional milliseconds and every projection witness remain retained.
Class handles and Error/Uint8Array/void Results use explicit semantic DTOs.
Raw OS chunks are retained separately and hash-bound; comparison concerns
settled ordered text per stream, not a fixed OS chunk partition. This supplies
no Windows/watch/full-platform or real-provider billing claim.

Both added mutants start from actual raw fixtures. H2 adds a second correlated
post-reopen execute fact plus effect count2 while retaining its interrupted
receipt; H3 makes late commit fulfilled/callback entered and appends a correlated
forbidden entry. They remain schema-valid and compare with CLI1, alongside the
existing lost-field/stale-head negatives. Correct effect counts and refusal are
oracle outcomes, never schema constraints masquerading as negative evidence.

The initial frozen candidate passed190 tests with zero skips under real Python3.11.13,
3.14.8 and3.15.0 using normal HOME. Explicit Ruff lint/format and mypy pass;
the unchanged standalone Nodecodec regression separately executes Nodev26.12.0.
Complete logs/commands/exits/before-after inventories, source/dependency/module
hashes, raw/projected/witness evidence and phase failures are retained in
`/tmp/pipy-de01b2-vs2swa2c/`. The first complete six-family run was
`reference-first/` on its earlier exact hashes. Final candidates use two fresh
archive/original npm-ci bootstraps `reference-freeze-a/` and
`reference-freeze-b/`; only their exact manifests support final frozen claims.
The full audit and all four semantic/prerequisite CLI probes accompany freeze.
No ordinary Python CI job executes these real reference probes today.

Upstream MIT Copyright(c)2025 Mario Zechner remains complete; adapted case and
barrier/clock helper headers identify provenance, and exact source/test hashes
are in the manifest. No new provider, product runtime, CI workflow, user state,
upstream checkout/install/build or source mutation is introduced. b3 retains
actual exported workload correctness/hardware measurements, exhaustive DE/P
mapping and mandatory real pinned CI including the standalone Nodecodec command,
all six families/four mutants, sanitized artifacts and actual pushed-job proof.


### DE01b2 pre-review invalid-outcome repair

Before independent full check or Opus review, the gate found that changing only
H3 final.state.outcome to JSON null caused an uncaught AttributeError/CLI1 rather
than invalid-evidence2. Concrete evidence is
`/tmp/pipy-de01b2-gate.uQWKeSga/malformed-terminal-outcome-result.json`; the
initial20-file freeze remains `/tmp/pipy-de01b2-vs2swa2c/freeze.json`. This is a
pre-review correctness repair, not an Opus finding or completed b2 review.

The accepted minimal change validates the new task visitor's outcome as an
object before reading result, using the existing explicit record guard. There
is no broad AttributeError catch. The other existing chained outcome access is
already preceded by bind_tasks' dictionary check; the new E1 chained paths have
explicit dictionary checks. No other contract or projection scope is changed.
The new actual CLI regressions show null/list/bool/string/numeric outcome forms
return2 without traceback. Valid completed/null-result, aborted/mark and failed
object outcomes remain comparable0/1, and absent nonterminal outcomes still work.
The malformed regression first failed on all six forms, then all nine new
positive/negative cases passed after the guard.

The repaired candidate passes199 focused tests/zero skips on each real Python
3.11.13,3.14.8,3.15.0 plus explicit lint/format/mypy. Final fresh six-family
reference bootstraps and four valid mismatch/prerequisite negatives are retained
under `/tmp/pipy-de01b2-prereview-fix-k688hf7j/`, with full per-command logs,
before/after hashes and raw/projection/witness/source/dependency evidence.
All six goldens, MTS artifacts, permanent Nodecodec test and MIT notice remain
unchanged from the initial b2 freeze. No b2 independent review, full check,
commit/push/CI or P-row closure is claimed. b3 and the DE02 dependency remain.


### DE01b2 first Opus review — accepted sidecar and raw-data clarifications

The first valid independent Opus5.5/high review completed **ISSUES**, with two
Suggestions and zero Critical/Warning findings, baseline
`b20e98510d3c2d1cda4774366e48857efaf8951a`, reviewed tree
`71e4a3f9211a645e483257503255cf1460b22095`. Result remains
`/tmp/pipy-de01b2-fixed-gate.CfeddTaH/review-first/result.json`. The coordinator
accepted both findings. Its `sidecar-replay-results.json` reproduces acceptance
of empty sidecars by replaying retained evidence through the validator; it is
explicitly not a new Node/reference runtime pass.

S1 repair adds narrow Python checks in the existing runner: exactly four unique
policy DB records, closed=true, exact controlled paths, nonsymlink regular files,
and recorded size/SHA matched against actually opened/read bytes. Every verified
DB digest is now directly attested in result.json. Versioned raw-chunks evidence
contains both selected Env scenarios, including the reader's empty chunk list;
chunk text and stream labels validate and per-stream concatenation must equal
the corresponding decoded raw settled observation. No OS chunk partition or
independent stream interleaving is added to golden comparison semantics.

Permanent regressions include empty/missing/duplicate variants and scenarios,
wrong closure/size/hash/file bytes, missing/escaping/symlink/nonregular DBs and
malformed or conflicting chunks. The two outer invalid2 tests fail against the
reviewed old runner because empty sidecars are accepted; they pass after repair.
These isolated wiring/file validator probes are not runtime execution evidence.
Existing four schema-valid semantic mutants remain mismatches1; invalid sidecar
evidence is2 and never counts as a successful semantic negative.

S2 clarifies data provenance: inputs are scripted synthetic scenarios, while
raw expected/runtime values retain observed controlled scratch paths, actual
mtime and generated provider UUIDs. Only declared typed projection/witness rules
derive comparison values; no raw-value removal is claimed. All six golden files
and the exact reference pin remain unchanged. No b3 implementation is included.

The repaired candidate passes229 focused tests with zero skips on each real
Python3.11/3.14/3.15, explicit Ruff lint/format and mypy, and the standalone
Nodev26.12.0 codec regression. Thirty sidecar regressions include the two
outer-invalid wiring checks. These Python tests do not require a host Node.

New repair logs, before/after regressions, raw sidecar-negative validator probes,
focused/static/docs checks and two fresh exact-candidate reference runs are
retained under `/tmp/pipy-de01b2-sidecar-fix-d15_518z/`. Earlier freezes and
ISSUES remain historical. A renewed independent full and scoped Opus review are
pending; the concrete empty-sidecar defect and new runner guard justify that
follow-up, rather than reviewer agreement alone. Owned set remains20 files;
all P/Python-adapter rows stay OPEN and DE02 remains blocked through b3.

## DE01b2 delivery reconciliation and DE01b3a admission

The pre-review malformed-outcome fix was not an Opus review. The first actual
Opus5.5/high review remained ISSUES (two Suggestions, no Critical/Warning),
with both sidecar/provenance findings accepted and repaired as recorded above.
The subsequent actual independent scoped review is CLEAN0, baseline
`a38d1a89318effa4168c02904c1fa5ba439b48e6`, reviewed tree
`0ffcda19fd6b5d648c90c8ab1178fc7e24eb5aff`. The delivered/pushed main commit is
`28bc886065a1086875e4b817c480f88152cf8933`.
[CI39286240678](https://github.com/ephes/pipy/actions/runs/39286240678)
succeeded10/10: each Python3.11/3.14/3.15 full suite12,919 passed/208 skipped,
durable229 passed/zero skipped; four PTY jobs17/0, required WASI441/0 with actual
runtime flag/pin. Coordinator checked remote/API and all141 archived files,
12,690 lines plus hashes/counts; concrete delivery and complete-log evidence:
`/tmp/pipy-de01b2-sidecar-gate.hQDe3D4Q/delivery/delivery-final.json` and
`ci/complete-log-audit.json` there. Earlier GitHub status delay is historical.
No historical ISSUES or earlier failed probe is relabeled CLEAN.

Before code edits, b3a/b3b are split in the delivery index above and plan/backlog.
b3a owns fresh local acceptance that bootstraps the reference once, preserves
all six cases/H2x4/E1x2, executes four fresh schema-valid CLI1 mutants and actual
exported twelve-read/three-write MemoryStorage workloads with correctness and
persisted readbacks before bounded timing, records hardware/counts and a complete
DE/P current/future index. b3b still owns mandatory actual pinned CI, selective
artifact collection and actual successful pushed-job proof. Neither local b3a
success nor six-case reference0 satisfies that remaining gate. No native durable
kernel/Python adapter/P-row completion or performance equality is claimed.

### DE01b3a local acceptance evidence

Status: **implemented locally; independent full/review/landing OPEN**. Base is
landed b2 `28bc886065a1086875e4b817c480f88152cf8933`. The owned scope is16 files,
including exactly the six pre-authorized new files. No CI workflow or collector
is implemented here. Manifest3 hashes eight copied MTS adapters, three Python
verification artifacts, five verification tests, the closed one-workload/four-
oracle registry and95-row coverage index. All six raw golden bytes, permanent
Nodecodec test and upstream MIT notice remain byte-identical to delivered b2.

The new `acceptance` frontdoor creates an exclusive private execution root and
reference child itself; it has no historical-reference input. It bootstraps once,
then requires six cases/H2x4/E1x2, actual exported workload correctness/readbacks,
four freshly derived schema-valid compareCLI1 mutants, positiveCLI0 controls,
coverage and unchanged exact provenance. Missing/malformed/drifted evidence is2;
a required valid reference mismatch is1. A six-case `reference`0 is explicitly
not full acceptance0, and local acceptance does not satisfy mandatory b3b CI.

The first actual development acceptance completed0 in37.30s and is retained as
its own earlier hash candidate. Final fresh candidate runs `acceptance-final-a`
and `acceptance-final-b` completed0 in28.49s and38.36s respectively. Full commands,
exits, normal HOME and before/after hashes are retained under
`/var/folders/79/dxyqffhn7753q_b9602pnfnm0000gn/T/pipy-de01b3a-hfilr9k6/`.
Each has a separate invocation/execution ID, exact pinned archive/lock/npm-ci,
1,961 original files,28,647 physical dependency files,346 installed records and
four precisely absent extraneous records (never usable dependencies). The six
module closures remain703/761/760/768/761/13; the actual workload closure is18.
`audit-final-results.json` verifies all current source/adapter/dependency/module,
case input/raw/projection/witness/result/auxiliary/DB, parent manifest/run and
workload/oracle/CLI artifact hashes, repeat projected trace equality and unchanged
kernel bytes. An initial private audit used the macOS `/var` alias instead of
the execution's canonical `/private/var` path and correctly failed its exact
command-path check; the canonical-path audit passed without source changes.

Workload report schema1 retains tagged raw observations and actual hardware:
Darwin27.0.0 arm64, Apple M4 Max,16 logical CPUs,128GiB RAM; Nodev26.12.0,
npm11.20.0, Python3.15.0. No hostname/username/environment dump is collected.
TIMING_SCALE is1,000 entries/300 tasks/300 family documents and the actual
exported primary count is1,870. All12 untimed exported read checks and all3
fresh write/readback checks finish before measurement. Each acceptance records
120 measured read calls and9 measured writes, with12 distinct freshly seeded
write stores including correctness. All stores await close in finally. Real
persisted entry/task/submission/address/document reads, full baseline preservation,
counts, commit sequence and mixed correlations are checked; no return-literal or
captured-write-list shortcut can establish persisted effects.

These are contextual local milliseconds, not thresholds or timing goldens:

| Exported run span | A median ms | B median ms | Samples per run |
| --- | --- | --- | --- |
| All12 read scenarios pooled | 0.017834 | 0.017792 | 120; per-scenario samples retained raw |
| commit one entry | 0.005084 | 0.005708 | 3 fresh stores |
| commit100 entries | 0.093125 | 0.097167 | 3 fresh stores |
| commit mixed entry/task/submission/document | 0.011333 | 0.011792 | 3 fresh stores |

All12 individual read actuals equal their export's expected(dataset); the filter
returns5 and ID-returning reads use the actual dataset IDs. Raw spans separate
seed/checks/run/readback; write run includes mint/batch work and one transparent
successful-commit observer append. Child seconds include Node startup/imports.
There are no warmups; this is bounded-local-v1, not the upstream Vitest or
retained-memory protocol. No retained heap, backend-wide benchmark, throughput
parity or Python performance conclusion is claimed.

Permanent host-independent focused tests pass288/zero skips on each actual
Python3.11.13/3.14.8/3.15.0. They preserve the prior229 tests and add workload
shape/results/readbacks/timing, strict manifest3, complete coverage/ownership/
future/link checks, actual compareCLI mutants and exclusive fresh-bootstrap
wiring. No-op0, invalid-mutant2, lying readback, artifact/manifest identity drift
cannot produce acceptance success. Unit execution doubles are explicitly labeled
wiring checks. Separate actual Node fault probes inject a wrong exported read,
a lying writer and a missing persisted readback: each throws before any timed
read round, with all opened stores closed and archived/adapted bytes unchanged.
These are fault-injection results, not extra successful baseline runs.

All four fresh actual semantic mutants remain valid and compare1 in each final
acceptance; originals compare0. Post-abort unique IDs are derived from the raw
observed integer domain rather than assuming a fixed unused ID. Source/ref/
fixture/runtime/dependency prerequisites still fail2, including removal of a
physically resolved dependency in a private copied snapshot; original successful
snapshots are unchanged. Exact source/test hashes and adapted Pi MIT provenance
are in the manifest and workload header; full notice is retained.

`coverage.json` accounts for all DE00–DE45, all P01–P43 and six DE01 children,
including parent DE01/b3 and b3a/b3b. Each canonical future contract and original
owner remains explicit; source-only/reference-only/delivery evidence never
claims a native comparison. All P and Python adapter remain OPEN. Mandatory real
pinned CI, selective artifacts and successful exact-pushed-job proof remain
DE01b3b; DE02 stays blocked until that slice is green. Independent full and
Opus review, commit/push and remote CI for b3a are not claimed by local evidence.

### DE01b3a first Opus review — accepted five-finding repair

The first actual independent Opus5.5/high review completed ISSUES with one
Warning and four Suggestions, baseline `73e958774b21f613144d940b608e9fc807fe6fb5`,
reviewed tree `5cc184b1bba52e4f099551931b6c5c64747b12ff`; result remains
`/tmp/pipy-de01b3a-gate.bp_agb0y/review-first/result.json`. All five concrete
findings were accepted. The material false-positive regression plus explicit
new guards justify renewed independent full and scoped review, not agreement alone.

W1: the original invalid-mutator wiring test corrupted the shared raw object,
so its positive compare failed2 and no negative compare ran. Its earlier green
result did not prove rejection of negativeCLI2; that test-coverage claim is
withdrawn. Repair deep-copies before corruption and explicitly requires positive0
and negative2, plus a guard-mutant test that must detect accepting2 as mismatch.
The actual acceptance's four valid semantic mutants and original production
exit check remain unchanged.

S1 synchronizes plan status and the harness b3a/b3b split with delivered b2 and
the DE02 dependency. S2 aligns coverage statuses with this ledger, separates
unimplemented b3b's empty own current evidence from inherited reference facts,
checks exact P source cells and concrete DE slice/line contracts and bounded
existing evidence. Original future requirements remain. S3 adds explicit
mutation/plan-row form and completeness guards; malformed evidence is2 without
StopIteration/IndexError or a broad catch. S4 tests the forbidden historical
reference option using both valid required arguments and its exact argparse
unrecognized-option diagnostic. Owned set becomes17 solely for the authorized
harness-spec synchronization; no b3b code, golden or projection changes.

Complete repair logs and before/after proofs are retained under
`/tmp/pipy-de01b3a-reviewfix-0umail3a/`; the initial16-file freeze and first
ISSUES remain historical. Current local checks and renewed independent review
are separate gates; no b3a CLEAN, delivery or hosted CI is claimed here.

The repaired permanent invalid2 test now reaches positiveCLI0 and negativeCLI2,
with no success manifest. A private single-condition guard mutation that accepts
negative2 as mismatch makes that SAME permanent test fail (DID NOT RAISE), and
would write an incorrect success; this is recorded in
`invalid-before-after-mutant-results.json`. Against the exact reviewed old helper,
the four missing-tool-result/empty-model/missing-late-commit/empty-variants CLI
regressions fail with1/lookup tracebacks; repaired code returns2 without traceback.
The plan-row regressions also require actual Python CLI2 for malformed shapes.
These are isolated CLI/wiring proofs, not reference runtime passes.

Coverage2 adds a closed inherited_reference boundary and retains all95 original
IDs and byte-equal contract/future/owner/plan-cell values (`future-preservation-results.json`).
Own b3b current evidence is empty/source-only; inherited local facts name their
actual b1/b2/b3a owners and do not establish CI. DE line/slice contract anchors,
matching bounded existing evidence and P source cells are checked; statuses match
this ledger with cumulative parents derived from children. No future requirement
or P-row acceptance is reduced. All six golden/input pairs, Nodecodec, workload
MTS and Pi MIT notice remain unchanged from the first b3a freeze.

The repaired focus passes306 tests/zero skips on each real Python3.11/3.14/3.15,
with explicit lint/format/mypy and separate actual pinned Nodecodec. New exact-
candidate fresh acceptance/audit/prerequisite/docs command logs and hashes are
retained under the repair scratch above. The independent gate owns the renewed
normal-HOME full and scoped Opus review of accepted W1/S1–S4 plus repair delta;
no writer full, review, stage, commit or push is performed.

### DE01b3a scoped S3 test-evidence correction

The next actual scoped Opus5.5/high review remains **ISSUES**, with one
Suggestion and zero Critical/Warning, baseline
`9e3a2ecd1a750e165ac099ed352e1428667b4440`, reviewed tree
`13b9a0f4a329ca9fcb1d17d5d21760793ed06d6b`;
`/tmp/pipy-de01b3a-repair-gate._9rrthe_/review-delta/result.json`.
The coordinator accepted this finding as directly coupled to S3. A further
scoped round is valuable because the claimed guard coverage was not measured,
not because another reviewer agreement is desired. Earlier ISSUES stays history.

The prior plan tests supplied one-row files, so deleting the shape guard still
returned2 from canonical completeness. Those tests did not independently prove
that the shape guard ran. They now copy the entire canonical plan, corrupt only
the one identified existing row, preserve every other row, and require the exact
`Malformed plan row` CLI diagnostic. The validator-only CLI adapter returns0
when parsing succeeds, so a deleted shape guard cannot produce a different
successful-looking invalid2. No production parser/guard changes were needed.

Mutation tests now require their exact reached diagnostics. Missing tool-result,
empty model and missing late commit reach their explicit mutation guards.
Empty variants is honestly rejected earlier by trace validation with
`Incomplete recovery policy matrix`; it is not evidence that the later,
unreachable missing-variant mutation guard ran. No production behavior is
changed merely to make a test guard reachable.

Before correction, private removal of the plan shape guard left all4 old tests
green, and removal of the recovery-matrix schema guard left its old test green.
After correction the8 original guard tests pass; the same permanent tests fail
against private single-guard removals: all4 plan cases see accepted parsing/CLI0,
three mutation cases see missing-guard IndexError/CLI1, and empty variants still
returns2 but with the DIFFERENT later mutation diagnostic. Its diagnostic
assertion therefore detects removal of the actual upstream schema boundary.
Private mutants never modify production files and are not reference executions.

Full command logs and original/private-mutant results are retained under
`/tmp/pipy-de01b3a-s3-tests-2upjsast/`. This delta changes only the two tests,
their closed manifest verification hashes and this precise evidence description
within the existing17 owned paths. All production/verifier/MTS bytes, coverage,
goldens/inputs, Nodecodec, MIT notice and prior broad runtime/full-check results
remain unchanged. Prior acceptance runs attest their earlier manifest/test
hashes; they are not relabeled fresh runs of this new manifest. Only relevant
focus/static/docs checks repeat; independent scoped review remains pending.


## DE01b3a delivery and DE01b3b admission

DE01b3a landed at `e3b2b391c3d6c92515ccd9f6acd2ce853f20bca2`,
reviewed tree `7d4159859ae0ebe179b98645caf74b4b62a7023f`, after actual
Opus 5.5 high CLEAN. Earlier ISSUES and both accepted repairs remain historical.
[CI39302403566](https://github.com/ephes/pipy/actions/runs/39302403566)
succeeded in all ten jobs: three full suites each 12996 passed/208 skipped,
306 durable tests without skips, four PTY jobs each17 passed, and both required
WASI jobs each441 passed with the pinned runtime. The coordinator audited all
141 archive files/12714 lines; archive SHA256
`8e586e87dfe2b13e235ddf6cef1e98e8d97c39e4752ffdd20479280d7b3ffeaf`.
Delivery report: `/tmp/pipy-de01b3a-s3-gate.di84dosu/delivery/delivery-final.json`,
SHA256 `476cda78e264974289b61a7d5050491218aaa5b413587256193fb8eef96e94be`.

Before code edits, b3b is admitted to implement the mandatory real pinned CI
job and selective collector in the approved fourteen paths. Local acceptance
and collection do not establish a successful pushed job or uploaded artifact.
The coordinator still owns full validation, independent review and delivery.
DE02 stays blocked until b3b actually succeeds remotely. All P rows and the
Python adapter remain OPEN; every original b3 acceptance is cumulative.


## DE01b3b local CI and artifact implementation

Status: **implementation in progress; independent full/review and actual hosted
job/upload proof OPEN**. The fourteen owned paths add a mandatory workflow job,
a closed textual collector and host-independent tests, retaining the six raw
goldens, four actual semantic negatives and permanent standalone Node codec.
Verification closure increases from five to eight exact artifacts by adding
the workflow, collector and collector test. The three Python comparison tools
and eight MTS adapters retain their separate ownership. Manifest schema3 and
coverage schema2 remain; there is no manifest/tool hash cycle.

The workflow uses anonymous exact-commit Git acquisition, official checksum-pinned
Node26.12.0/npm11.20.0, an explicit minimal child environment, standalone codec
and exactly one fresh acceptance bootstrap. Only registered textual files are
staged. The original full bundle is attested locally, including closed SQLite
bytes; expected projected/witness files are checked against the registered
goldens. DB/archive/runtime binaries remain excluded and are explicitly marked
not uploaded. Failure before invocation or command metadata stays partial/failed.
Details and the exact collector command are in
[the reference guide](../durable-reference.md#mandatory-reference-ci-and-selective-artifacts).

The mandatory workflow job is separate from server branch-protection enforcement;
this slice changes no remote settings. Successful local acceptance/staging does
not establish a pushed hosted pass. Actual run, numeric job, artifact ID/digest,
index and all uploaded bytes must be independently audited after authorized push.
DE02 remains blocked until that delivery; all P/Python parity stays OPEN.


### DE01b3b local verification and delivery boundary

Local logs and exclusive evidence roots are retained at
`/tmp/pipy-de01b3b-stp504as/`. The final focus set passes351 tests without skips
on real Python3.11.13,3.14.8 and3.15.0; it includes all306 delivered tests and
new collector/closure/status cases. Ruff lint/format and explicit mypy on all
five changed Python files pass. Ordinary Python tests require neither Node nor
network. The separate actual Node26.12.0 codec and two fresh acceptance executions
(`ship-a`, `ship-b`) each pass, each with its own single new bootstrap,
six cases, H2x4/E1x2, twelve read correctness checks/120 timed reads, three write
checks/nine fresh timed writes and four original0/mutant1 CLI pairs.

Both final textual stages verify140 exact files (including original raw/witness/
versioned chunks and DB result attestations); four SQLite binaries and two git
archive copies are explicitly not uploaded. Local origin and run0 are declared;
these are local stage proofs, not GitHub uploads. Nine actual collector CLI
validator replays on the fresh observed bundle reject missing/changed golden
projections, empty DB/chunk sidecars, changed DB bytes, symlinked raw evidence,
invalid oracle metadata and absence before invocation, all with exit2. Every
temporarily changed source byte is restored and rehashed; these probes are
validator replays, never additional runtime passes. Missing Node/npm/exact
reference/fixture probes each return2.

The collector pins each directory descriptor while opening, refusing exchanged
intermediate symlinks and hardlink aliases. Its two permanent boundary tests
pass on the actual implementation and both fail against a private mutant
restoring the old full-path open/removing the hardlink guard. This is a targeted
regression effectiveness proof, not an independent review.

Development failures remain in the command index: initial mypy typing/stub
issues, one new diagnostic-test typo and a private local wrapper using macOS's
`/tmp` alias (actual acceptance passed; collector correctly refused the alias).
The wrapper now uses canonical private paths; no guard was relaxed. Earlier
pre-directory-guard reference runs are historical, superseded by the two final
hash-bound executions. All six golden traces/inputs, eight MTS adapters, Nodecodec
and upstream MIT notice are byte-unchanged. No upstream write/install occurred.

The final freeze binds the exact fourteen owned paths and full command/log/hash
index. Independent normal-HOME full check and actual Opus5.5 high review remain
coordinator gates, followed by authorized commit/push, actual mandatory hosted
job and downloaded artifact/digest/index-byte audit. This local record claims
none of those later outcomes. DE02 remains blocked until they pass; all43 P rows
and the Python adapter remain OPEN.

The uploader SHA's official package metadata identifies version7.0.2/Node24.
Its YAML annotation was corrected without changing the action SHA or behavior;
the final ship-a/ship-b runs bind the updated verification/manifest hashes.
Earlier freeze-a/freeze-b runs remain historical with their original manifest.


### DE01b3b first Opus review — accepted C1/W1/S1–S3 repair

The first actual Opus5.5 high review returned **ISSUES**, not CLEAN: one Critical,
one Warning and three Suggestions. Result is
`/tmp/pipy-de01b3b-gate.v7_k_ugk/review-first/result.json`, baseline
`505a710e66f033ccab024644ee10c856d80eaaca`, reviewed tree
`b40e374dcfe80c49e717612593212b589c97d662`. All five findings are accepted.
A new scoped review is valuable because the workflow was actually invalid and
two malformed shapes actually escaped the collector boundary; this is a
correctness repair, not a search for reviewer agreement. The first14-file
freeze and its logs/hashes remain historical unchanged.

C1 was reproduced with official actionlint1.7.12: complete workflow Exit1,
`runner` unavailable in job `env`. The downloaded darwin-arm64 release archive
SHA256 `aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f`
was checked against official release checksums and API metadata. Root paths now
use supported Acquire/Collector step-env and uploader step-with contexts;
complete actionlint then returns0. No early-failure or always-upload path is
made optional. The permanent context regression rejects the actual old workflow.

W1 retains every semantic inner deadline. The actual eleven wrapper ceilings
sum3180seconds; Acquire has3300seconds with120seconds overhead. Checkout/setup/
collector/upload each have300seconds and identity recording60seconds, totaling
4560seconds across steps; job5100seconds adds540seconds reserve. The outer
acceptance still sends INT at1800seconds, KILL after30 and wrapper waits1840.
Permanent tests derive these bounds from actual inline Python/YAML and reject
the old40-minute job cap.

S1 was proven through the actual public collector CLI on the original full
observed bundle: `auxiliary_sha256=[]` and source inventory `null` (with matching
local metadata hashes) each produced AttributeError/Exit1 and no index before
repair. Original evidence bytes were restored and rehashed. The outer helper
attestation boundary now classifies the proven AttributeError as invalid2 and
preserves a safe failed index; no blanket Exception handler was introduced.
Gather examines every registered role and reports missing separately from
unreadable/unsafe roles, even when an early symlink fails. Failure staging keeps
only independently readable registered wrapper text; invalid bytes are absent.
Permanent actual-CLI inventory-null/list/string/integer tests reach that real
helper boundary without Node/network or fabricated exceptions.

S2 moves the new release note under Added. S3 declares PyYAML directly in the
dev group: the approved owned set grows14→16 with pyproject.toml and uv.lock.
Offline lock regeneration changes exactly the two pipy dev metadata entries;
no package version/artifact/hash changes. Verification remains the exact eight
registered tool/test/workflow files; owned dependency configuration is frozen
separately. No six-case golden/input, MTS adapter, Nodecodec or Pi pin changes.

Repair evidence and complete command logs live at
`/private/tmp/pipy-de01b3b-reviewfix-9izsflyd/`. The permanent context/budget
tests fail against the original workflow, individual removed context/budget
guards are detected, the old AttributeError boundary fails all four actual-CLI
shape tests and the old aborting Gather fails the missing/invalid-role test.
These expected mutant failures are regression effectiveness proofs, not
independent review or reference runtime passes. The coordinator owns renewed
normal-HOME full and scoped actual Opus5.5 high review against the accepted
baseline plus this repair. Hosted proof, DE02 and all P/Python parity remain OPEN.


The repaired focus set passes360/0 on real Python3.11.13,3.14.8 and3.15.0.
Explicit Ruff lint/format, mypy and the complete-workflow actionlint check pass.
Two final fresh acceptance+standalone Nodecodec+collector executions pass in
38.36/38.27seconds at manifest
`dab63fea6fa768cc145ed8db0f8a9a5fc4c19f6f56067237fece7e253b65aca3`.
Each stages140 byte-identical text files with six explicit binary exclusions
and retains all six cases, H2x4/E1x2, workload checks/readbacks and four valid
original0/mutant1 pairs. The two proven malformed forms now return2 with safe
failed indices; a real CLI early-unsafe-plus-late-missing replay reports both
roles truthfully. Nine other collector negatives and four prerequisites stay2.
Temporary evidence mutations are restored/rehashed, never labelled fresh runtime.
The first docs build rejected an unquoted literal square-bracket example;
backtick quoting fixes that link-parser error, with the failed log retained.
No full/review/stage/commit/push is performed by the writer; new16-path freeze,
normal-HOME independent full and scoped Opus review precede any delivery claim.

## DE01b3b delivery reconciliation and DE02 admission

DE01b3b landed/pushed at `13d061d2dd19ce85db097ef33938fa9869593e36`, tree
`54f1fd6c87c94be772a276cf58f450b7bbb903a4`. The actual Opus5.5 HIGH implementation
review is CLEAN; its first ISSUES and accepted context/budget/evidence repairs
remain historical below and are not renamed. The coordinator independently
verified [CI39370449156](https://github.com/ephes/pipy/actions/runs/39370449156),
attempt1 success11/11: three full suites each13,050 passed/208 skipped; durable360
without skips; four PTY jobs17/0; required WASI jobs441/0 with the actual pin.
Independent local full:13,256 passed/2 skipped. Uploaded artifact12240918411,
job118456897132, contains168 text payloads plus index,169 ZIP entries and seven
explicit exclusions. The actual2,047,556-byte ZIP SHA256 is
`d31beca75a87c7b489acdfc798047a92dcdd8bd5ce4fe895e953ea0e3007ad7a`.
The coordinator independently rehashed every entry and the complete153-entry,
13,476-line CI archive (SHA256
`208824bdcc95b033d20cc90dcff8becdf603441136abfae38d16740c212eb378`).
Full report: `/tmp/pipy-de01b3b-repair-gate.59ovcqqu/delivery/delivery-final.json`,
SHA256 `c3b20f0bd4ae41b6d81d2d6becba6ce04d8ee251758212ce1e1b5d14a5eda591`.
These are coordinator-audited delivery facts, not a new writer execution.

### DE02 split before product implementation

The approved DE02a/DE02b and DE02a1/DE02a2 split preserves the full original
parent row. DE02a1 owns a versioned owned-value/record-tree codec and Storage
Protocol/DTOs; DE02a2 owns the genuine atomic Memory and raw-base seam required
by DE02b's revocable drafts. DE02b retains admission/cancellation, seal/prepare,
read-after-write, owner checks, guard/poisoning, before/after acknowledgement
failures, adoption-before-publication, listener/reentry and close/drain cuts.
No Session behavior or native backend is implemented by a1. Typed documents,
delta/copy/as-of/fork materialization and retention remain required by their
owning later slices. Unsupported raw-base commands must reject entire batches.

The approved product value domain is full finite binary64, including signed
zero, above-safe integral payloads and lone-surrogate strings/indices. Exact
Python integers must have an exact finite binary64 representation; IDs/Seq use
a separate positive-safe domain and cursor positions a signed-safe domain.
No arbitrary depth256 or duplicate-parser restriction is introduced. The codec
owns already parsed values and versioned record trees; no unnecessary public
text/byte parser is added. Rawbase is assigned to a2, not silently removed.
All P rows stay OPEN. No product default/session integration changes are included.

### DE02a1 local value and record evidence

Status: **implementation in progress; independent review and delivery OPEN**.
The native product now has an owned finite value codec, complete versioned
record trees and validated Storage port DTOs. It has no MemoryStorage or Session.
A separate verification manifest closes product/tests/input/driver/script and
pinned sources; the original six-family registry and eight MTS adapter inventory
remain unchanged. The bounded comparison is actual Chord value copy versus
Python codec, and Pi-consumed records versus Python DTO roundtrip, never a
Python backend comparison. Full Memory/Seq/index/ancestry/concurrency/raw-base
and storage-mutant proofs remain required by DE02a2; Session/draft/adoption
proofs remain DE02b. Local results and exact final hashes are recorded after
execution, not inferred from this implementation status. All P rows OPEN.

#### DE02a1 UTF-16 representability repair before independent gate

The first candidate's real15-value/31-record probe and two later same-candidate
paired runs were green at native manifest
`853bcdb1345ecd85244b15af9b79c6454f22baceefeed00a3c14af3505ed6589`.
They do not cover the following newly reproduced input. Public OwnedJson and
versioned decode accepted a Python dict with both astral and literal surrogate-
pair keys, although its two members have one JS UTF-16 property identity.
Actual pinned Node/copyJson showed one last-wins property, not a Node refusal.
The accepted repair rejects only that unrepresentable Python dict, preserving
all individual strings, single keys and lone surrogates unchanged. It introduces
no parser duplicate policy or valid-JS-value exclusion. DE02a2 explicitly retains
UTF-16-equivalent request/task/document index semantics.

Permanent direct-value/versioned-value/record tests failed3/4positive against the
old guard and pass after repair. Removing just the new guard privately reproduces
those same3 failures while all4 positive spelling cases still pass. The first
private-plugin attempt failed to import; it is preserved as a tooling failure,
not counted as a guard proof. The effective mutant adds only the private plugin
path and leaves tracked bytes unchanged. Full before/after logs and the actual
Node recipe are under `/private/tmp/pipy-de02a-cjkqx7yn/utf16-*`. Final fresh
17-value/31-record paired results and exact hashes follow after execution.

#### DE02a1 first frozen local verification (2026-10-20; superseded below)

The repaired candidate binds native manifest
`a0ea49e30eafd11eb201689c6056080bf53f4e1a313ab9d1500407b53ebdb3b0`.
This is the first frozen candidate, not the later plain-key repair candidate.
Two independent fresh baseline acceptances each execute all six families,
H2x4/E1x2, twelve read/three write workloads and four original semantic negatives.
Their separately executed a1 probes each compare17 values/31 consumed records
and preserve actual UTF-16 recipe facts: same units, one JS property, last value2.
Each fresh field/ID/signed-zero/alias-witness mutant is valid mismatch1 with a
separate positive0. Source and dependencies are re-attested before/after; neither
probe claims to bootstrap or implement native Memory/Session.

| Local gate | Actual result / complete local log locator |
| --- | --- |
| Fresh pair1 | Bootstrap0/91.16s, a1 comparison0/8.54s; `utf16-final-bootstrap-1/`, `utf16-final-comparison-1/` |
| Fresh pair2 | Bootstrap0/91.66s, a1 comparison0/8.54s; `utf16-final-bootstrap-2/`, `utf16-final-comparison-2/` |
| Permanent durable focus | Python3.11.13,3.14.8,3.15.0 each507 passed,0 skipped; `utf16-final-focus311/`, `utf16-final-focus314/`, `utf16-final-focus315/` |
| Codec and static | Actual separate Nodev26.12.0 codec0, same byte-identical permanent MTS target; Ruff, formatting, strict product and combined mypy0 |
| Public invalid controls | Positive0; malformed record, missing parent and missing file each2; closed manifest version/ref/inventory/hash guards covered by permanent actual CLI tests |
| Review/full/delivery | OPEN; independent coordinator-owned normal-HOME full and actual Opus5.5 HIGH still required; writer performed neither |

All local locators are below `/private/tmp/pipy-de02a-cjkqx7yn/`; full commands,
stdout/stderr, actual exits/HOME and source hashes are retained. `final-pairs.json`
records distinct parent/probe execution IDs and exact result hashes. Earlier
15-value candidates and failed diagnostics remain separate evidence. This is
bounded JSON/record DTO proof; DE02a2 Memory/index/Seq/ancestry/raw-base/race and
DE02b Session/draft/adoption/failure requirements remain OPEN. No P row closes.

#### DE02a1 plain-key admission repair before independent full/review

The independent pre-gate rejected the first freeze before running full or Opus:
`/tmp/pipy-de02a1-gate.8o3ghnto/gate-fail-early.json` and its
`record-key-hooks-result.{json,py,log}` show EntryRecord optional omission invoking
a caller str-subclass hash hook and accepting its nonplain key. Direct OwnedJson
already rejected the same input without hooks. This is not an Opus review finding;
no a1 review verdict exists yet. The first freeze and its earlier green results
remain historical evidence, not evidence for the repaired bytes.

The same narrow public-boundary investigation reproduced two connected envelope
holes: decode_record's shape membership and decode_value's key-set comparison
accepted str-subclass keys after invoking equality/hash hooks. Exactly three
explicit exact-string-key guards now precede omission/copy or envelope membership.
They use generic diagnostics without rendering caller values. The Storage DTOs
already owned input before validating shape; their product code is unchanged.
A separate public before/after probe also covers an opaque object key: previously
its hash ran during record copying; the repaired boundary rejects it without hooks.
No JSON domain, depth, number, Unicode or parser policy is changed, and no Node
record-constructor validation is claimed.

Permanent tests reset hook counters immediately before the public call. Against
private product files whose four SHA256 values exactly match the first freeze,
the final tests produce5 failures/3 safe-DTO passes. Removing only the record,
record-envelope or value-envelope guard separately produces3/1/1 targeted failures;
non-omitted record keys fail the empty-hook assertion, not merely a broad exception
expectation. The repaired full durable focus passes515 cases with0 skips on each
of Python3.11.13,3.14.8,3.15.0, including existing plain-key/optional-omission
positives. Ruff, formatting, strict product and combined mypy pass; the actual
separate Nodev26.12.0 codec test also passes.

Full stdout/stderr, commands, actual exits, normal HOME and source hashes for this
repair are under `/private/tmp/pipy-de02a1-key-repair-ecn39ojk/`. Original public
observations are in `public-original/`, repaired observations in `public-repaired/`,
permanent original regressions in `original-permanent-regressions/`, and individual
guard probes in `guard-mutant-{record,shape,envelope}/`. These expected negative
exits remain explicitly separate from positive checks. Fresh hash-bound pairs and
final ownership evidence follow below; independent full/review/delivery, DE02a2,
DE02b and all P rows remain OPEN.

The final plain-key candidate binds native manifest
`922867181e1336bc25f7273ed0c6871dae6e8ce2e602c51a45497c54db77a911`.
Both new baseline acceptances are actual fresh bootstraps at the unchanged Pi pin;
each executes six families, H2x4/E1x2, twelve reads/three writes and four original
semantic mutants. Their separate a1 probes each compare17 values/31 records,
attest14 original source modules and retain actual UTF-16 last-wins recipe facts.
Each probe freshly executes field/ID/number/alias-witness negative CLI1 and a
separate positive CLI0; malformed record/missing parent/missing file controls
remain invalid CLI2, never mismatch1. The original baseline manifest remains
`81f6d1aca17ebe4f5927682d42a4cc08f4a5cddf0a783d084ca52be5a8bedf99`.

| Final plain-key pair | Actual result / complete log locator |
| --- | --- |
| Pair1 | Fresh acceptance0/138.80s in `acceptance-1/`; separate a1 comparison0/8.60s in `comparison-1/` |
| Pair2 | Fresh acceptance0/138.64s in `acceptance-2/`; separate a1 comparison0/8.61s in `comparison-2/` |
| Invalid controls | `final-invalids/` and `final-invalid-artifacts/`; positive0, three invalid2 |
| Closure/source audit | `final-audit/`, `audit-results.json`, `final-pairs.json`; original95 futures/contracts,99 rows/43 OPEN, six fixtures/eight adapters/Node codec/MIT unchanged |

All these locators are under the repair directory above. Both pairs have distinct
parent and probe execution IDs and exact result hashes in `final-pairs.json`.
The first freeze remains `/private/tmp/pipy-de02a-cjkqx7yn/freeze.json`; its logs
are not relabeled as current. The new freeze binds all22 owned paths and records
the precise delta to that first frozen state. No native MemoryStorage, Session,
parent delivery, parity closure or independent reviewer verdict is inferred.

#### DE02a1 first independent review and accepted repair

The first actual Opus5.5 HIGH review is valid **ISSUES**,2 Warnings/2 Suggestions,
not CLEAN: `/private/tmp/pipy-de02a1-repair-gate.csjz5sr3/review-startup-retry/result.json`.
Its recorded baseline is `f46d8dc90d860a1509452eac0b8ddc6001f6ecb8`, reviewed tree
`3341c9a4555135ff909444a58f286588e59d3f40`. All four findings were accepted;
the scoped follow-up is justified by real signed-zero equality collapse and
run-mismatch misclassification, not a request for cosmetic agreement. Previous
first-freeze and plain-key candidate logs remain historical and unchanged.

| Finding | Accepted repair / proof boundary |
| --- | --- |
| W1 owned equality/hash | Explicit representation-exact equality: float bits, Python int/float type, object-member order and Python string spelling. Derived identity tokens only revisit already validated scalars; DTO owners inherit it. JS-number/UTF-16 comparison and future index identity remain separate. |
| W2 actual run mismatch | Valid Pi/native and Pi-consumed DTO roundtrip differences return1 with hash-bound mismatch report/result; invalid input/provenance remains2. Separate real runtime fault-injection runs test both differences, not compare-only units. |
| S1 repeated sample | Remove duplicate17th transport value, retain16 distinct values/31 records. UTF-16 decoding selects astral spelling; literal-pair Python spelling is covered by native unit tests, not falsely claimed as a second Pi value. |
| S2 decoder omission | Strict owned JSON validation precedes record shape validation and direct owner adoption; constructor-only OMITTED cannot disappear on decode. Constructors still omit selected optional fields, without key hooks. |

The earlier17-value logs count17 executed inputs, not17 distinct JS values or two
transported Python spellings. They are not overwritten or reused as final repair
proof. Targeted permanent before tests failed11 with2 representation-preservation
positives; initial after12/13 exposed one incorrect new test expectation (the
scripted astral-key payload is1), corrected without changing that payload. The
old execute-function injection fails exactly2 new run tests while positive0 and
invalid2 controls pass. Reverting only owned equality produces9 failures with3
representation-preservation positives; reverting only decoder JSON admission
reproduces its omission failure. Complete logs and exact final repair results
are recorded under `/private/tmp/pipy-de02a1-opus-repair-z1wy4_sx/` below.

The first static pass flagged test complexity; two small unit closure factories
were extracted in the same owned test file, retaining every assertion. Earlier
532-case passes preceded that test-only cleanup, so final focus checks are rerun
at the final manifest. Neither this repair nor its tests implement Memory/Session,
change the six-family/eight-adapter registry, or close a P row. Independent scoped
review/full/delivery remain pending; no earlier ISSUES result is relabeled CLEAN.

#### DE02a1 final local evidence after first-review repair

Final native manifest:
`ccda52728c99eec928e11c94b3a9c8ed7de96865496c0aad7d9a49ca11d1c45c`.
Two fresh baseline acceptances remain six-family/H2x4/E1x2/workload/four-oracle
executions; each separately runs the final16-distinct-value/31-record native
comparison, preserving14 actual source-module hashes. Both normal probes pass0
and preserve four valid semantic negatives1 with independent positives0.

| Final gate | Actual result / full log locator below repair directory |
| --- | --- |
| Fresh pair1 | `acceptance-1/`0/89.15s; `comparison-1/`0/9.78s |
| Fresh pair2 | `acceptance-2/`0/88.64s; `comparison-2/`0/9.76s |
| Final durable focus | `final-focus311/`, `final-focus314/`, `final-focus315/`: each532P0S |
| Static/codec | Final Ruff/format/combined mypy0, strict product mypy0; actual isolated Node26.12.0 permanent codec0 |
| Actual native-output fault | `actual-run-native/`1/8.07s; `run-native-output/result.json` status mismatch, equality flags false/true |
| Actual DTO-roundtrip fault | `actual-run-roundtrip/`1/8.00s; `run-roundtrip-output/result.json` status mismatch, flags true/false |
| Original execute runtime controls | `actual-old-run-native/`, `actual-old-run-roundtrip/`: each actual Node0 then outer2/no mismatch result, reproducing W2 |
| Invalid controls | `final-invalids/` positive0 and malformed/missing-parent/missing-file2; actual invalid-output run also2/no result |

The fault-injection probes really execute the pinned Node driver and the actual
run/main path; they alter a valid Python output or the post-native DTO decoder.
They reuse the identified newly bootstrapped parents and reset only the exact
private caller adapter between runs. They are **not new bootstrap or candidate
PASS claims**. Original execute controls use only its exact pre-repair function
in a private injection wrapper; no tracked/gate/upstream source changes occur.
`actual-run-proofs.json` binds each exit/report/observed-output/runtime command,
compares report hashes and verifies valid mismatches remain CLI1. Report raw trees
and all stdout/stderr are retained. Ordinary pytest's four execute/main tests use
an explicitly labeled bootstrap stub, independently of these actual Node proofs.

All local files are below `/private/tmp/pipy-de02a1-opus-repair-z1wy4_sx/`.
`final-pairs.json` records distinct parent/probe IDs and result hashes;
`command-index.json` includes expected nonzero repro/mutant/control commands,
not just successful tails. Full prior evidence and ISSUES remain unchanged.
The freeze includes all22 owned hashes, exact accepted-baseline repair delta and
caller-authored review context inputs. Final docs/render/link checks are recorded
in that freeze. Independent full/scoped Opus review/delivery remain coordinator-
owned and pending; DE02a2/DE02b and all parity rows stay OPEN.

## DE02a1 delivery and DE02a2 pre-code split

Actual landed main b1aa0562e3d096cd7dbc3da992ab92b310936bf1 has actual
Opus5.5 HIGH CLEAN0, independent full13428P2S and CI39387254837 attempt1 all11
SUCCESS. The coordinator independently audited all153 logs and the uploaded
artifact. Earlier ISSUES2W2S and plain-key pre-gate evidence remain unchanged;
the landed repairs preserve repr-exact equality/hash, strict decoder JSON,
16 distinct transport values and run mismatch1 versus invalid2.

The approved a2a/a2b split retains every original DE02a2 requirement. A2a is an
internal table stage with one authority/private candidate/one adoption and its
own actual Node/Python oracle, ownership and deterministic concurrency. A2b adds
raw-base documents inside that SAME candidate and completes cumulative bounded
Protocol acceptance. No Session or product integration is authorized.

Exactly101 coverage IDs retain original95 future/contract text and all43 P OPEN.
A2a current evidence is populated only after actual execution; a2b remains
source-only. Neither parent a2/a nor b/DE03 can land early. This split is recorded
before any new product implementation in this slice.


## DE02a2a local table evidence

This section preserves the first freeze candidate and its checks. It was
superseded by the pre-review lazy-ancestry finding documented below; no full or
Opus review ran on it, and these checks are not a CLEAN verdict.

The sole-writer candidate implements internal `MemoryStorage` tables in
`src/pipy_harness/native/durable/memory.py`, with one common authority across
commit, mint, every read/index/scan and close. A complete private replacement
contains tables/indices/global IDs/commit Seq; fallible preparation finishes
before one adoption. Supported final-write validation and injected private-index
failure leave old public observations/counters intact. Deterministic contended
write/write, write/read, write/mint and write/close tests witness attempted common
lock acquisition and join all threads. Exact/read/page/retained-input and old
snapshot ownership, both scan orders, ancestry caps/segment ordering, marker vs
head, complete replacements, UTF-16 equivalent index keys and distinct lone
surrogates are tested. Legacy cursor order follows the table default; removing
a request winner has no fallback. No global clock or ID normalization is used.

This child explicitly refuses document reads/finds/scans and whole batches with
document writes. There is no product factory/default/SDK integration, full
Storage claim, Session admission/Tx or P-row completion. DE02a2b must add current
raw-base documents inside this same candidate/authority and finish all cumulative
bounded Protocol acceptance. Original delta/fork document and Session-owned
requirements remain with their original later slices; a2/a/b/DE03 cannot land early.

The new own `storage-memory-v1` input/manifest and caller select17 actual exported
Node conformance cases by exact names at pinned storage-conformance.ts98–1674.
Their real255 calls/arguments/results/errors remain in `conformance-raw.json`;
exactly-once callbacks and finally-close facts are separate from the seven
**derived paired recipes**,62 ordered actual Node/Python operations. This is not
Python execution of the exported TypeScript cases. The source indexed-string
case1552 additionally covers documents; a2a's table projection is labelled as
derived. A1's16 value/31 record probes and all six DE01 families remain intact.

Raw traces retain finite binary64 bits, UTF-16 units, own missing/null/undefined,
fields, ordered effects and errors. Only verification object-key ordering,
backend label and explicit RuntimeError/StorageClosedError→Error language names
are projected. Typed signed cursor Number→Python integer conversion is limited
to that role. Payload negative zero/large finite numbers are untouched; native
literal-pair spelling tests and repr-exact OwnedJson identity remain distinct
from JS UTF-16 index identity. Serialized alias facts are observations from
actual commit/read/mutate/reread calls, not substitutes for live ownership proofs.

Local evidence root: `/private/tmp/pipy-de02a2a-0k0b6h0a/`. Complete commands,
normal caller HOME, exits and stdout/stderr are retained per-command. The initial
exploratory chain (baseline38.38s, a1 7.14s, table9.39s, all0) is historical evidence
for its earlier manifest, not one of the two final chains. Initial fixture setup
used an unsuitable system Python and failed import; the logged project-environment
retry succeeded. A signed-cursor transport-role failure, initial static/type/test
failures and a private mutation-hook setup failure are preserved, not pass claims.
The run-report regression caught projected backend labels being fed back into a
raw schema: reports now bind raw compare-compatible traces plus projected hashes.

Focused Python3.11/3.14/3.15 suites each passed598 tests with0 skips. Ruff lint,
format check, strict product mypy and combined tool/test mypy passed. The actual
permanent Node26.12.0 codec test passed separately under minimal child environment.
Seven private individual product guard removals each fail the corresponding
permanent test: namespace, Seq, private-candidate isolation, conditional request
removal, cursor fallback, UTF-16 identity and common read authority. Oracle guard
mutants prove accepting invalid2 or reclassifying valid run mismatch as2 breaks
permanent tests. These private mutants are negative controls, not candidate passes.
Unit-only run/controller tests clearly replace bootstrap/child execution; actual
Node-backed fault runs and final chain results are indexed separately below.

MIT Copyright(c)2025 Mario Zechner and the complete upstream license are retained;
the new manifest pins exact original five source hashes and the16-artifact caller/
native/test/input/helper closure. Hashes flow baseline→a1→a2a with no self-cycle.
Independent full, actual Opus review, commit/push and remote evidence remain
coordinator-owned and pending for this candidate. All43 P remain OPEN.


Final local chain/negative index (same candidate table manifest
`fe7a2d3115f3f93fcb9b47527edfea034446c5dfe91d8a7a38df1a5867e7fbd9`):

| Proof | Actual outcome and retained evidence |
| --- | --- |
| Fresh chain1 | `final-1-baseline/`0/44.68s → `final-1-a1/`0/7.16s → `final-1-a2a/`0/9.37s; parent execution `4ced5843-3770-4540-9ad9-4731aa392934` |
| Fresh chain2 | `final-2-baseline/`0/40.83s → `final-2-a1/`0/7.62s → `final-2-a2a/`0/9.82s; parent execution `a88013c4-7cba-4f09-9c68-bd39e69007b1` |
| Both table runs |17 exported Node cases/255 calls;7 paired scenarios/62 operations;7 fresh positive0/negative1 mutants;16 resolved original reference modules |
| Actual run mismatches | `actual-run-{field,id,seq,request-index,status-index,cursor,alias}/`: actual Node0 then outer1, hash-bound raw/projected mismatch reports |
| Actual invalid run | `actual-run-invalid/`: actual Node0 then invalid native outcome gives outer2; no result/mismatch report |
| Missing runtime | `actual-run-missing-runtime/`2: real missing Node spawn, no result/mismatch report |
| Audits | `final-audit/`0, `actual-run-audit/`0; complete `final-pairs.json`, `audit-results.json`, `actual-run-proofs.json`; no orphan runtime children |

Actual run fault probes reuse the first complete fresh parent and reset only its
exact hash-checked private caller file. They execute the real pinned driver and
public run/main path, intentionally alter a valid native output or prerequisite,
and are neither fresh bootstrap nor candidate PASS claims. Each valid mismatch
report's raw pair re-compares as1; invalid2 never receives a mismatch report.
The separate unit bootstrap stubs are not included in these runtime claims.
`command-index.json` and the18-file freeze include full logs/exits, expected
negative controls, earlier failures, docs/render-link verification and exact
current hashes. No writer full/review/commit/push was performed; independent
whole-slice gates and actual delivery remain open.


## DE02a2a pre-review lazy-ancestry repair

The independent gate stopped **before full or Opus review** with `FAIL_EARLY`:
`/private/tmp/pipy-de02a2a-gate.cjopgym2/gate-fail-early.json`, SHA
`09a4c9586c91de9b95e69d6a6a870bae9cc03a7f303190289e1d772331f5c773`.
It compared actual pinned Node with the first frozen candidate, not a source-only
hypothesis. A raw batch with root1, conversation2(parent999,at10), entries3(head3)
and4 is accepted. Node returns local page4/next4, scoped entry3/Seq1 and marker3;
Python eagerly visited999 and raised KeyError for all three. This is an accepted
correctness defect; the first freeze and earlier passing checks remain unchanged
at `/private/tmp/pipy-de02a2a-0k0b6h0a/`, not a CLEAN or final-candidate claim.

The repair makes segment/descending-entry iteration lazy; scoped entry stops at
its first result, head returns the first child marker, and scans consume only
`limit+1` entries. Both orders stop before following a parent whose accumulated
cap is below the minimum. Ascending still deliberately builds the complete
capped segment list before yielding. No Parent/Owner/cycle validation, raw-storage
scope reduction, exception translation or generic error normalization was added.
Global authority/private candidate/one adoption, indices, cursors, source-order
request winners, UTF-16 identity and all later raw-base/Session obligations remain.

New private evidence root: `/private/tmp/pipy-de02a2a-lazy-xv90sflp/`. All18 original
owned bytes were checked and archived before repair; the root's full finding and
actual Node/Python repros were read. Permanent native regressions first produced
5 failed/3 passed (`lazy-before/`): three local reads and two minimum-cap stops
fail, while required-parent controls already pass. After repair,28 native tests
pass (`lazy-after/`). Five individually removed page/point/head/min-cap/ascending
guards each fail the intended permanent test with the expected KeyError999 or
DID NOT RAISE diagnostic (`guard-matrix/`, `guard-results.json`). These are actual
negative controls, not exception-only pass claims.

The versioned ancestry recipe now has eight extra actual paired operations:
accepted raw dangling-parent seed, local page/point/marker, descending/ascending
minimum-cap stops, absent point above parent cap, and capped ascending local
results. The seven derived scenarios now contain70 operations.17 exact exported
Node cases/255 calls are unchanged and remain separate from those paired recipes.
A separate actual Node control reads only the exact pinned snapshot: six local/
capped results plus three required-parent TypeErrors (ascending, insufficient
page lookahead and head below local bound), with all nine loaded source hashes
matching git objects. It preserves raw errors without projecting them into Python
or claiming another fresh bootstrap (`actual-node-controls/`). Native controls
also require those parent accesses rather than swallowing their KeyErrors.

Current table manifest SHA:
`846c71bb477600032b8d61dea94c03c1753d4fec50bc65a8b47d0bbfe885b333`.
Closed hashes/coverage are updated without changing original fixtures/goldens,
adapter closure, historical future requirements or Session/document scope.
Focused3.11/3.14/3.15 suites each pass606 tests/0 skips; Ruff/format, strict product
and combined mypy pass. The actual separate Node26.12.0 codec also passes.
Two entirely new final original parents bootstrap all six DE01 families/workloads/
four mutants before a1 and the repaired table run:

| New final chain | Actual exits/times |
| --- | --- |
|1 | baseline0/82.80s → a1 0/7.99s → table0/10.52s |
|2 | baseline0/79.20s → a1 0/8.00s → table0/10.52s |

Each table execution has17 exported cases,255 calls,70 paired operations,16
resolved original reference modules and seven newly derived valid positiveCLI0/
negativeCLI1 mutants. `final-pairs.json` binds distinct parent/a1/table execution
IDs and exact output/manifest hashes; `audit-results.json` verifies provenance.
Raw observations and complete logs remain private. These new runs supersede no
historical result by renaming: they explicitly validate the repaired candidate.
Independent whole-slice full/actual Opus and delivery are still pending; no Opus
review exists for this a2a candidate. All18 paths and all43 P OPEN are retained;
a2b raw-base in the same state authority and cumulative Protocol proof remain
mandatory before a2/a/DE02b/DE03 can complete.


The repaired-candidate `actual-run-field/` executes actual pinned Node0 then
public run/main1 with a valid native field mutation and a hash-bound raw/projected
mismatch report. `actual-run-invalid/` executes actual Node0 then malformed native
outcome2; `actual-run-missing-runtime/` is real missing-Node2. Neither invalid run
emits result/mismatch reports. These explicit fault probes reuse new final
parent1, are not new bootstraps/PASS claims, and are audited in
`actual-run-proofs.json`; no orphan runtime child remains. The earlier seven
actual run faults remain evidence only for the first freeze. The final command
index, rendered-links check and18-SHA freeze record this new candidate separately.


## DE02a2a pre-review recorded-argument repair

A second independent pre-Opus gate stopped the lazy-repaired freeze754d after its
completed normal-HOME full:13502 passed/2 skipped, exit0,1134.12s. This full is
**historical pre-material-repair evidence only**, not the current full gate. Its
full log SHA is `c5c591447df3e6e67e87cd8dc47aa0aa7f11ea6c1ba803c0b745edfe8e881657`;
skips were the unset codemode-required-runtime flag and case-insensitive filesystem.
All18 bytes and the product map were unchanged before/after that full. No Opus
review has started for a2a. The final gate finding is
`/private/tmp/pipy-de02a2a-repair-gate.ot9_76od/gate-fail-early-arguments.json`,
SHA `301d3776f4a340d2ef0ea545a5f3e1b6a64ea130ef7c7949b60b26f5a7ddac88`;
its repro SHA is `60c6a19ee55a85cecb2feaf1ca6bf1f6c54b8a6ee7d5869584f51d120665d24a`.
The actual Node trace's root commit argument pack alone was changed to tagged
null. `_recipe_args` already rejected it, but `trace` checked only generic owned
JSON, so the public compare returned semantic1 instead of invalid2. This is an
accepted evidence-classification defect, not a runtime mismatch or review verdict.

The sole production-tool change is one line: trace now calls existing
`_recipe_args(op, decoded_args)` instead of only `OwnedJson(decoded_args)`.
There is no recipe-value equality or argument/result truth hardcoding. Product
source, MTS caller and70-operation recipe remain byte-unchanged from freeze754d.
The shared shape/DTO guard covers all14 registered operation roles; only12 are
currently present in paired traces. Exact task/submission reads have direct
argument-guard tests, not fabricated paired public-CLI evidence. Null packs and
malformed write/query/ID/cursor/limit/request/zero-argument fields are invalid2;
valid wrong IDs, an absent optional write field, different queries and signed
cursor arguments remain semantic1. Existing seven valid output mutants remain1.

New private evidence root: `/private/tmp/pipy-de02a2a-args-lqi_ig86/`. All18 previous
frozen bytes were verified and archived before edits. The permanent matrix first
produced21 failed/18 passed (`arguments-before/`): all12 public-trace null packs
and nine malformed field cases wrongly returned1;14 existing helper tests and
four valid-wrong-argument cases already passed. After the one-line fix all39 pass
(`arguments-after/`). Every invalid public compare has its own positive0 control
and specific guard diagnostic. The unchanged original gate Node trace replays as
positive0/null-root-argument2 (`gate-original-positive/`, `gate-original-invalid/`);
this is explicitly compare-only replay, not another Node execution or bootstrap.
Focused3.11/3.14/3.15 each pass645 tests/0 skips; Ruff/format and strict product/
combined mypy pass. Hash closure/coverage observables are updated, with all101
IDs,99 prior contract/future strings and all43 P OPEN preserved. The earlier
freezes, failures and full logs remain immutable. No new product/MTS/recipe,
Session/default or document capability is introduced; a2b remains mandatory.


Current table manifest SHA:
`d817cee520791ddea9c9a7d6acba7eef0212d96090add8b33923db0c74d43f88`.
Two new original complete parents execute the entire pinned baseline acceptance
once each, then actual a1 and the current table adapter:

| New argument-repair chain | Actual exits/times |
| --- | --- |
|1 | baseline0/86.93s → a1 0/7.68s → table0/10.22s |
|2 | baseline0/86.81s → a1 0/7.68s → table0/10.21s |

Each table run retains17 exported Node cases/255 calls, seven paired scenarios/
70 operations and seven newly generated valid positive0/negative1 mutants.
`final-pairs.json` binds distinct execution IDs and exact hashes; the complete
archive/source/module/dependency/DB attestations are checked before and after.
The permanent Node26.12.0 codec test passes separately under minimal child env.
`actual-argument-matrix/` is compare-only replay of this candidate's freshly
observed Node trace: all12 recorded roles have positive0/null-pack2 with the exact
guard diagnostic, and four valid wrong argument changes remain1. Its complete
logs and source trace hash are indexed in `actual-argument-results.json`; the
task/submission roles remain direct-helper tests, not fabricated CLI cases.

`actual-run-field/` really executes pinned Node0 and public run/main1 with a
hash-bound raw/projected mismatch report. `actual-run-invalid/` really executes
Node0 then malformed outcome2; real missing Node is2. Neither invalid run emits
result/mismatch reports. These explicitly reuse new parent1/reset only its exact
private caller, never claim another fresh bootstrap/PASS, and are hash-audited
in `actual-run-proofs.json` with no orphan children. Entire phase/stdout/stderr/
child-command evidence remains under the new private root. Final docs/rendered
links and the18-file freeze complete the local handoff; independent post-material-
repair full and FIRST whole-slice actual Opus review are still required.


## DE02a2a first-review raw-limit disposition

The first actual whole-slice Opus5.5 HIGH review returned **ISSUES**, one Warning
and two Suggestions, not CLEAN. Its result is
`/private/tmp/pipy-de02a2a-args-gate.yog2zvok/review-first/result.json`,
SHA `482713fcbbf030136980dc8014b8348253ba3d8a83cd6f2831faf2255557db23`,
baseline `e522ab23cda55d654f6e9b47e8049d4d4e38eb6c`, reviewed tree
`bccaa4fcd3a38aa7afdac8367dddad44267f23e6`. Earlier pre-review failures and all
original frozen artifacts retain their original meaning.

| Finding | Disposition and bounded change |
| --- | --- |
| W1: raw nonpositive page-limit behavior lacked an explicit contract/regression | Accepted for permanent tests/docs after actual pinned observation. Preserve the actual behavior; add no positivity policy or error normalization. |
| S1: full candidate/index reconstruction can cost proportional to stored data per commit | Declined for this slice. Correctness is unaffected and no performance claim is made. An unmeasured incremental index redesign would change the shared-state authority proof; retain one complete private candidate and one adoption. This suggestion is not reported as fixed. |
| S2: duplicate active child-status block under Backlog History | Accepted. Merge into the active durable entry, preserving the `de02a2-internal-tabledocument-children` heading anchor and all parent dependencies. |

The original private investigation is
`/private/tmp/pipy-de02a2a-review-limits-jufyo7i7/with-minus2/`.
It executes actual pinned Node MemoryStorage and current native storage against
identical source-derived seeds: four scans × empty/nonempty/filtered-nomatch ×
limits0,-1,-2,+1 =48 rows. Its `matrix.json` SHA is
`12e0f90ac2fe08b56391386f9b73c4885efc46d0c35ccd6daaa7038dd5689fb6`.
Twenty rows fulfill and28 reject. Limit0 without matches returns an empty page,
while matching limit0 fails; -1/-2 fail even without matches; +1 has exact pages
and ordered cursors. Node throws raw TypeError (`Cannot read properties of
undefined (reading 'id')`); Python throws raw IndexError (`tuple index out of range`).
Both complete raw result files retain messages/stacks and nine exact original
source-module hashes. This is a real read-only probe of an identified retained
snapshot, **not** another bootstrap or full acceptance. The source page builder
is pinned `memory.ts:203–210`, with scan loops447–462/515–538/547–566/575–592.
No claim about every nonpositive limit or equal language exception classes follows.

New private repair evidence is
`/private/tmp/pipy-de02a2a-review-fix-9571ocdp/`. All18 original frozen bytes were
verified and archived before edits. The permanent native48-row matrix passes;
it checks full independent records, exact cursor presence/order and raw Python
IndexError type/message, without requiring Node in ordinary pytest. A private
positivity-policy mutant fails36 tests/12 pass, while a private error-normalizer
mutant fails28/20 pass. Both use the same permanent tests and fail on the intended
outcome boundary, not setup; their expected exit1 logs remain intact. The original
backend passes all48. No product, comparison runner, MTS caller or recipe changed.

`runtime-before.json` attests409 files byte-for-byte against the reviewed baseline:
374 product Python files, six comparison Python files, ten MTS reference artifacts,
15 input/golden recipes, the permanent Node codec test and three package/lock/CI
files. The canonical map SHA is
`35b63f8b6fb4245d2856b955a966ea12b7a33efc40ae29b71c6e015034b1f8da`.
The independent normal-HOME full from that baseline passed13541/2 skipped, exit0,
1137.74s; complete log SHA
`533ae72a91f053d37689cf22965d4e7a57992bc841c9fb591cf7bd42db56819a`.
Its skips are the unset codemode-required-runtime flag and case-insensitive
filesystem. This is unchanged-runtime evidence **before** the new tests/docs/hash
metadata, not a claim that a new whole full was run. Current focused Python3.11,
3.14 and3.15 Durable suites each pass693/0 skipped. Ruff/format, strict product
mypy and combined mypy pass; the standalone actual Node26.12.0 codec passes with
minimal child environment. The18-path ownership and all43 P OPEN are retained;
a2b raw-base inside the same authority/candidate, cumulative Storage and Session
DE02b remain required. The next independent review is scoped to these accepted
findings, the declined performance suggestion and the repair delta from e522,
not another first whole-slice review. No self-review or delivery pass is claimed.


Current closed table manifest SHA:
`3dd333040fe0d1cb1bf25e96ee8517914fa71009a4789e969473c0c05c87835b`.
Two entirely new parent bootstraps execute complete DE01 acceptance, followed by
real a1 and table execution at these final code/test/coverage/manifest hashes:

| Raw-limit regression final chain | Actual exits/times |
| --- | --- |
|1 | baseline0/75.69s → a1 0/8.04s → table0/10.57s |
|2 | baseline0/75.27s → a1 0/7.96s → table0/10.58s |

Both table runs retain17 actual exported cases/255 calls, seven derived paired
scenarios/70 operations and seven freshly generated valid positiveCLI0/negativeCLI1
mutants. `final-pairs.json` binds distinct parent/a1/table execution IDs and all
manifest/result hashes; `audit-results.json` re-attests archive, dependency,
source/module and database evidence and preserves all99 prior contract/future
texts,101 IDs and31 protected artifacts. Child a1/table execution explicitly
reuses each complete fresh original parent, not another bootstrap.


`actual-run-field/` really executes pinned Node0 then public run/main1 with a
schema-valid native field mutation and hash-bound raw/projected mismatch report.
`actual-run-invalid/` executes Node0 then malformed outcome2; real missing Node
is2 in `actual-run-missing-runtime/`. Invalid/prerequisite runs emit no result
or mismatch report. These fault probes explicitly reuse new final parent1 and
reset only its exact private caller; they are not new bootstraps or pass claims.
`actual-run-proofs.json` verifies the distinct boundaries and no orphan children.
Full stdout/stderr/child-command logs are preserved. The final rendered-docs/
local-link audit, runtime-after409-byte map and18-SHA freeze complete this local
handoff; independent scoped review/delivery remain root-owned and pending.


## DE02a2a delivery and DE02a2b admission

DE02a2a landed at `85390e2b2c3504902ca1e917ea333aedfecbf1d7`, exact independently
CLEAN tree `b0b93167fb27199ba6839196f3d39ee07db2b4df`, actual Opus5.5 HIGH.
[CI39408774643](https://github.com/ephes/pipy/actions/runs/39408774643) attempt1
succeeded11/11; the coordinator independently audited all153 logs and the actual
uploaded artifact. Prior FAIL_EARLY findings, first ISSUES/W1/S1/S2 and scoped
navigation ISSUES retain their historical verdicts; the performance suggestion
was declined, not fixed. The navigation-only repair preserved all runtime bytes.

Before a2b product edits, the existing a2a→a2b split remains unchanged: a2b adds
current raw-base documents INSIDE the same authority/private candidate/adoption,
global IDs/Seq and all cumulative bounded Storage cuts. A2/a wait both delivered
children; Session/Tx DE02b waits a2b and DE03 waits b. All101 IDs, original
contract/future text and all43 P OPEN remain. Delta/copy/numeric historical/fork
materialization remain later DE04/DE27; no product/default/SDK integration follows.


## DE02a2b local cumulative raw-base evidence

Local implementation evidence, not a landed verdict or P closure. Private root:
`/private/tmp/pipy-de02a2b-w0d996ij/`. The initial clean HEAD/tree were exactly
`85390e2b2c3504902ca1e917ea333aedfecbf1d7` /
`b0b93167fb27199ba6839196f3d39ee07db2b4df`; all18 owned files were archived before
edits. Actual a2a delivery/review history and the pre-code a2b admission above are
retained. The original101 IDs and every preexisting contract/future cell remain;
a2a is LANDED, a2b IN_PROGRESS, cumulative a2/a IN_PROGRESS, b/DE03 OPEN, all43 P OPEN.

Raw-base document state, exact address pointers and scope indices now live in
the same `_State`, lock, global IDs/Seq and private commit candidate as tables.
Per-ID grouping permits create+retire and live base-change+retire in either order;
duplicate content/retire, unknown/retired IDs and global reuse fail. Full value/
version replacement, exact record policy fields, empty lifetimes/claimed IDs,
conditional address-pointer removal, singleton/empty-family and exact scope-kind
identity follow pinned `memory.ts:606–675,733–858`. Current exact reads never
follow address replacements. Document cursors ignore order and emit ascending
continuations. Unsupported delta/copy/numeric historical/fork materialization
remains an explicit whole-batch/request boundary for DE04/DE27, not fake empties.
No a1 codec/records/Storage-port source, Session, product factory/default/SDK,
original six fixtures/eight-adapter closure, hosted CI or collector is changed.

The native late-conflict/allocation regression reaches
`_validate_document_candidate` after private primary tables, owner/status/request/
entry indices, actual documents, addresses and scope indices all exist. Its spy
asserts those candidate fields before the supported final cardinality failure;
MemoryError is separately injected after successful validation but before the
single adoption. Both preserve the original authority object, every public
primary/index/read, global counters and already-minted gap, and permit later
use. Unsupported preflight alone does not prove this cut. Mixed write/write,
write/read, write/mint, write/close and failed-writer/waiter tests witness an actual
blocked acquisition at the same authority using Events/Barrier-style boundaries,
with release/join cleanup. Static assignment checks the full existing Storage
Protocol; closed refusal covers all methods and a supported mixed batch.

Permanent native table/document coverage passes119 tests, including all prior
76 table tests/boundaries with the obsolete document-refusal expectation replaced
by supported current behavior and retained unsupported/closed refusal. Against
the original a2a backend,34 selected public new-document regressions fail due to
actual document refusal (`old-public-doc-regression/`); the broader41-row old
probe also contains expected absent preparation hooks/barriers and is not claimed
as41 reached public guards. On the final implementation, five private guard
mutants fail the intended permanent tests: removed cardinality1F/1P, unconditional
pointer removal3F/5P, wrong cursor label1F/2P, merged scope-kind1F and raw Python
string identity1F. Whole logs, not just summaries, are retained.

The separate current phase is `memory-current-base-a2b-v1`; old table-only
bundles are not accepted as this cumulative phase. It retains all17 exact
exported Node table cases and all original seven paired recipes/70 operations.
Four further exact exported Node cases cover indexed strings, logical addresses,
lifecycle and rollback:21 actual Node cases/326 calls total. Some full exported
Node cases exercise deferred delta/history APIs; they remain Node-only assertions,
not a full Python suite claim. Four added **derived paired** document/mixed/
UTF-16/live-ownership recipes bring the actual paired trace to11 scenarios/128
operations.14 independent valid field/ID/Seq/table/document-address/scope/cursor/
alias mutants require actual positiveCLI0 and negativeCLI1. Calls, fields, IDs,
sequences, missing/null, signed binary64 bits and UTF-16 units are preserved.
Native spelling witnesses separately construct five literal Python surrogate pairs.

The18-role argument guard covers17 actual recorded roles; only exact submission
lacks a paired trace operation and has a direct helper guard instead. Malformed
document ID/address/point/size is public CLI2 with specific diagnostics; valid
wrong document ID/address/scope arguments remain schema-valid CLI1. New tests
initially had two inaccurate expected diagnostic strings (2F/5P), corrected to
the actual precise existing guard messages; no validator was relaxed. The current
seven diagnostic/mismatch controls pass on all three Python versions.

Current focused Python3.11/3.14/3.15 suites each pass766/0 skips. Ruff/format,
strict product mypy and combined10-file mypy pass; the actual standalone
Node26.12.0 codec passes separately with minimal child environment. One expected
diagnostic literal received formatting while the focused suites were starting;
its before/after AST is identical (`test-format-identity.json`), and the seven
relevant controls were rerun on all three versions after formatting. No semantic
code change followed the final closure. Initial Unicode/helper invocation/input
construction, path-collision, lint/type/format failures remain explicit local
history rather than passes; executable verification phases retain full logs.

A separate actual pinned Node/native12-row document scan probe executes fresh
instances using the identified exploratory parent's pinned snapshot. Empty/
nonempty/filtered-nomatch ×0/-1/-2/+1 yields five fulfilled/seven rejected rows,
with exact successful pages/cursors. Raw Node TypeError and native IndexError
names/messages/stacks stay distinct; all nine resolved original source hashes
match exact git objects and snapshot bytes. `raw-document-limits-final/matrix.json`
binds the current product hash and both complete raw results. This is an actual
retained-snapshot probe, **not another bootstrap** or equal-error-class claim.
No positivity rule or general error normalization is added.

The earlier exploratory parent acceptance0/41.43s and actual a2b0/15.13s demonstrate
first runtime progress at their own earlier candidate hashes. The wrapper-label/
output-directory collision exited2 before execution; the corrected unique output
is separately logged. Those exploratory artifacts do not substitute for the two
new final complete baseline→a1→a2b chains required below. Original a2a and earlier
DE01/a1 failures, local references, independent reviews and delivered CI remain
immutable history. All substantial source adaptation retains MIT Copyright(c)2025
Mario Zechner and the unchanged full upstream notice. The writer runs no full or
independent review; the coordinator owns the definitive full/first a2b Opus gate
and authorized delivery. No self-verdict is supplied.


Final cumulative table/document manifest SHA:
`2b0952403cadc03c37073970c9d0897af37b35f25637f733217d80112043f67a`.
Two new private original parents execute the complete pinned six-family/H2/E1/
workload/four-mutant DE01 acceptance, then actual a1 and cumulative a2b:

| Final a2b chain | Actual exits/times |
| --- | --- |
|1 | baseline0/104.21s → a1 0/7.57s → a2b0/16.19s |
|2 | baseline0/100.65s → a1 0/7.59s → a2b0/16.21s |

`final-pairs.json` binds distinct parent/a1/a2b execution IDs and exact
acceptance/result/manifest hashes. Each child honestly records no fresh bootstrap
of its own. `audit-results.json` re-attests complete archive/source/module/lock/
dependency/DB evidence before and after, verifies21/326 and retained17/255, exact
old seven recipes and all101 original contract/future cells. The separate final
raw-document matrix SHA is
`d6f3e9afd8459485976b208138f253aac4c1bcb68e5937311acf89ce2a8a5ab6`;
it binds current product SHA
`22e6d43bdd69962725bddf59986f3b546e124055793ce703bc18ce98fb596ca3`.

`actual-run-document-address/` executes actual pinned Node0 and native code,
then injects a schema-valid wrong document-address result: public run/main exits1
and emits hash-bound raw/projected `result.json`/`mismatch-report.json` which can
be rechecked through compare. `actual-run-invalid/` executes Node0 then a
malformed native outcome2; `actual-run-missing-runtime/` has real missing-Node2.
Invalid/prerequisite runs emit no result or mismatch report. These separate
fault probes reuse new final parent1/reset only its exact private caller, not
another bootstrap or passing acceptance. `actual-run-proofs.json` verifies these
boundaries and child cleanup. All complete command/stdout/stderr and child
metadata remain private under the new root. The final18-file freeze carries
local docs/rendered-link/static evidence and the caller-authored whole-slice
review context. Independent full/first a2b Opus5.5 HIGH review, commit/push and
hosted CI remain coordinator-owned and pending; no writer full or self-pass.


## DE02a2b pre-review foreign-point admission repair

The independent gate stopped the first local18-file freeze **before any full or
Opus review**. Original freeze SHA
`bc943b721f3e513c372570346b3e8317fb56227dcc78d2c0e9fb7b738f1457c3` and all
prior local proofs remain immutable. The gate finding is
`/private/tmp/pipy-de02a2b-gate.039qzxfv/gate-fail-early.json`, SHA
`f511666d142af37408717292c6ddbaf289cf9f22f1d6a18fc26a8ffc2cad7771`;
its point-data SHA is
`0ed5d45fc1abe8f07db45a8737f61275fe952393901b773d7cd7305385fbb064` and executed
probe SHA `3b60c47438efc2d388c1510a4693da3b4463fa632d7662f966ee84086346a3d7`.
`_current` compared an unadmitted foreign Python object with `current` while
holding the Storage authority. Its `__ne__` both forged admission and ran under
the lock; reentering public mint_id deadlocked. The gate bounded, terminated and
reaped its own child group. This is invalid Python input and a real authority
callback defect, not a raw-version or schema-valid Pi Point parity discrepancy.

Before the code repair, Plan/Harness/port reproduction explicitly required exact
built-in admission before comparison/coercion/hash/repr and prohibited arbitrary
caller hooks under authority. No original101 contract/future cell changed.
The only product repair is `_current`: exact builtin-str/current first, otherwise
existing exact positive-safe integer admission, then declared unsupported numeric
history. Foreign objects and str/int subclasses reach no hook. No RLock, bypass,
foreign call outside the lock, new positivity policy or error normalization was
introduced. Closed refusal still linearizes under the same authority.

The connected ingress audit found that owned DocumentQuery/Address/StorageWrite
construction already rejects foreign values through OwnedJson before internal
point/value comparisons. Raw scan query types and cursors have exact type guards;
those remain intact. No extra a1 file edit is needed. Direct document/find,
query-point construction and raw scan ingress are all covered, together with
closed-before-invalid behavior. Existing primitive/finite/UTF-16/ID/Seq, atomic
candidate and table/page behavior remain required.

New private root: `/private/tmp/pipy-de02a2b-point-qx3o7xg5/`. Forty-four permanent
cases first produced12 failed/32 passed: ten direct admission cases failed,
while query ownership/closed/exact controls already held; both real reentry
children timed out after executing the hook under lock. After repair all44 pass.
Hooks cover eq/ne/repr/str/hash/int/index, str/int subclasses and an eq-only
fallback, with counters reset immediately before the public boundary and no
input representation in errors. Exact-current succeeds and numeric1 remains
Unsupported. Raw content versions0/-1/1.5/1 remain accepted and retained, separate
from defineDoc's positive-safe definition policy; the gate independently proved
that raw/definition distinction before discovering the point bug.

The permanent child regression starts a new own session/group, verifies its PID/
PGID, fences import readiness separately (10s), then allows the point call with
an independent2s operation deadline. On failure it terminates/escalates only its
own group, communicates/waits and records reaped/group-absent evidence; ordinary
pytest requires no Node or network. On the corrected code both children reject
without entering any hook and retain next minted ID2. A private exact removed-
guard mutant reproduces12F/32P, including both real reentry timeouts; all child
lifecycle artifacts prove cleanup. Earlier before/after and current mutant logs
remain separate, never reported as passes. No a2b full/review result yet exists.


Current repair checks: Python3.11/3.14/3.15 each810 passed/0 skipped, including163
Storage cases. Ruff/format, strict product5-file and combined10-file mypy pass;
actual standalone Node26.12.0 codec passes separately. All prior five document
index/cursor/UTF-16 guard mutants still fail the intended tests at the current
product hash. `point-child-lifecycle.json` audits14 actual own-child records:
four bounded deadlock negatives (before and removed-guard), ten hook-free
positives (after/current three-version suites), all reaped and group-absent.
No test depends on host Node/network, copied credentials or global state.

Two entirely new complete final chains bind current closure/manifest
`bef8cecaa5059344ee38f92212237cc247b59d323ec924a38c5fbbb16c6dfd7d`:

| Point-repair final chain | Actual exits/times |
| --- | --- |
|1 | baseline0/71.44s → a1 0/7.43s → a2b0/16.15s |
|2 | baseline0/68.20s → a1 0/7.24s → a2b0/15.93s |

They retain21 actual exported Node cases/326 calls, all original17/255 and seven
unchanged paired table recipes,11 paired scenarios/128 operations total, plus14
fresh schema-valid positiveCLI0/negativeCLI1 mutants each. Full reference/source/
module/dependency/archive/DB and current closure attestations are repeated before/
after and bound by distinct execution IDs in `final-pairs.json`. An a1/a2b child
is explicitly not another bootstrap. The current raw document12-row matrix SHA
is `c28ad7ebbe14bed076d691b121887f306360bb3f6e81753aa3779b7604dca3fb`, bound to
product SHA `257104652872dd949d24e2b5f0350512f627ecbb596c2049a3cff913f4b1ba2c`.

`version-domain-proof.json` SHA
`70e94233ceeb7bfc715d764a2e60775f0726307b4276c7f38d53990d8eada39d`
records a new actual pinned Node/current-native raw create/change/read probe for
versions0/-1/1.5/1, all four retained exactly. Actual defineDocFamily separately
rejects0/-1/1.5 and accepts1. Its32 original source and664 physical pinned
package-module hashes are validated against the original source/dependency
inventories plus complete parent reference attestation. This uses an identified
new final parent's snapshot, not another bootstrap; real raw StorageWrite and
minted DocumentId annotations are retained, with no tsc-validation claim.

Actual current Node-backed run document-address mismatch1 again emits a bound
raw/projected report; malformed native outcome2 and missing Node2 emit no result/
mismatch report. These additional public-run fault probes honestly reuse final
parent1; they do not turn old/copy-only evidence into fresh acceptance. All
commands/stdout/stderr/child artifacts are retained under the new root. The
original failed freeze and gate proofs remain unchanged. Source/fixtures/Session
boundaries and all P OPEN remain; no a2b independent full or Opus result exists.
The next gate is one independent full followed by the first whole-slice actual
Opus5.5 HIGH review, coordinator-owned, not a scoped reuse of an a2a review.


## DE02a2b delivery and DE02b split

DE02a2b landed at `72253352d243fb12491e7c4429c6265e0e7e3149` on origin/main
(tree `8f85fe7befcb2db4178a4bed939aa07a378a13a7`). Round 1 whole-slice review,
Claude Code `claude-opus-5-5` HIGH: **ISSUES** with one Suggestion (a stale
"seven" scenario count in the `scripts/durable_memory_comparison.py` docstring).
Claude Opus 5.5 HIGH repaired it by removing the number. Round 2 delta review,
Codex `gpt-6.1-sol` HIGH through `codex-review-loop`: **CLEAN**, model/effort
proven, no omissions. The push started
[CI39545767044](https://github.com/ephes/pipy/actions/runs/39545767044), which was
not awaited; no CI result is claimed for a2b. With a2a and a2b delivered, the
cumulative DE02a2 and DE02a parents are LANDED; all43 P rows stay OPEN.

Owner decisions of 2026-10-21 (plan §7, appended): Claude Code
`claude-opus-5-5` HIGH implements and repairs, Codex `gpt-6.1-sol` HIGH reviews;
GitHub CI is no longer a delivery gate (local `just check`, Codex CLEAN and a
`git ls-remote`-verified push are). Earlier slices keep their recorded
GPT-implemented/Opus-reviewed history.

Before product edits, the coordinator split DE02b into four children, preserving
every original DE02/DE02b cell cumulatively (plan §5 rows, coverage index
105 rows): **b1a** private guarded Storage plus lifecycle owner (first failure,
admission seal, registration, drain, backend-close-once, report-once) with
deterministic permanent native tests; **b1b** the actual pinned Node guard oracle
through a bounded `SessionImpl` driver subclass, paired recipes, mutants, closed
manifest and two fresh chains; **b2** the first internal Session+Tx over the same
owner (FIFO line, sync and suspending callbacks, drafts, pending-operation drain,
adoption, acknowledgement cuts, publication, close), which may split again before
code; **b3** cumulative paired Session acceptance and the final b gate. DE02b
waits all four, DE02 waits a1+a2+b and DE03 waits b.


## DE02b1a local guard evidence

Local implementation evidence, not a landed verdict or P closure. Implementer:
Claude Code `claude-opus-5-5` HIGH, sole writer on main from clean HEAD
`72253352`. New private module `src/pipy_harness/native/durable/session.py`
(not exported from `native/durable/__init__.py`) and permanent tests
`tests/durable/test_session_storage_guard.py`. No codec/records/Storage-port/
Memory product file, reference adapter, fixture input or CI file changed.
Semantics are adapted from pinned `session/session.ts` (`fail`, `close`,
`FailureLatch`, `GuardedStorage`; blob `b22e9c56…`, sha256
`cc0e6af2159b5e746bee809c478a4f3946573b97314c9c6995e8ab3d690a9186`) and
`errors.ts` (`SessionFailed`), MIT Copyright (c) 2025 Mario Zechner.

`SessionLifecycle` is the single owner of one backend. One ordinary
`Condition` guards only its state: first failure, guard seal, registered-call
count and close state (idle, requested, running, done). Linearization points:
admission checks health, then the seal, and registers the call in one locked
step; the backend call runs unlocked; classification and the first-failure latch
happen in one locked step after the call; retirement decrements under the lock.
`fail` latches once, seals and requests close but never waits. Close is
"last retirer closes": the thread that retires the final registered call, or the
requester when none is underway, claims the requested close and runs backend
`close()` once outside the lock, then publishes the shared outcome. No cleanup
thread exists, so none needs reaping. `GuardedStorage` is a Storage-shaped facade
with identical signatures for all sixteen methods; `reading(view)` binds an
immutable `CancelView` over an owned `CancelLatch` to the same owner, consulted
only to classify a thrown read error.

Classification mirrors `#call`: a failed owner turns any later error or success
into `SessionFailed(first cause)`; reads exempt only `StorageRequestError` or a
cancelled caller view; commit and mint are never exempt; a non-`Exception`
`BaseException` is never exempt, latches if first and is re-raised unchanged.
Admission after failure raises `SessionFailed`, after the seal
`SessionClosedError("Session is closed")`. A healthy backend-close failure becomes
the cause; with an earlier failure the cause stays and the close error still
reaches every closer. A cancelled or timed-out close waiter raises without undoing
the seal, drain or pending close. Reports run once, outside the lock, after the
seal and after the failing call retired; reporter `Exception`s are contained
(Pi Harness `safeReport`), other `BaseException`s propagate. Python-spelling
differences, recorded rather than hidden: a healthy close failure is reported
after the outcome is published (a reporter calling close cannot wait on its own
cleanup), and another thread may close a drained backend while the failing
thread reports. Arguments pass unchanged, so a raw `RecordError` for an ill-typed
argument fails the owner; typed ingress belongs to Session/Tx.

The permanent file registers 99 scenarios, each run against the real module
and, for item 8, against private source mutants: all sixteen delegates through
both facades with exact args (both `entry` forms, defaults, cursors, current
point) and identical result objects; the 13 read roles × request-error exempt,
cancelled-view exempt, ordinary poisons and cancelled success returned; commit/
mint never exempt; Pi 132 (mint/commit × late success/late error), Pi 156 with a
witnessed blocked close wait and an exact backend-state check at close entry,
Pi 192 race and healthy close failure, external double `fail`, concurrent and
cancelled closers including a synchronous close throw, Pi 267/291/301/410,
reporter outside the lock, reporter process control, `BaseException` decisions,
the a2b foreign-point pass-through and closed refusal before Memory. Two further
tests check static and `inspect.signature` Protocol conformance and argument
validation. Eight private mutants (read-role exemption, post-call health check,
late-error health check, admission seal, registration, drain wait,
first-cause-only, close-once) each fail their designated reached-guard scenarios
in the permanent suite; the unmodified module passes them.

Full private mutant matrix (all 99 scenarios per module, scratch runner, bounded
waits lowered to 1s): unmodified 0/99 failed; read-role exemption 28 failed
(13 request-error, 13 cancelled-view, Pi 301, Pi 410); post-call health check 3;
late-error health check 3; admission seal 4; registration 8; drain wait 8;
first-cause-only 2; close-once 9; no worker thread left alive. The implementer
reported repeated deterministic runs (20 consecutive clean runs of the guard
file), the durable directory, Ruff, mypy and the strict docs build to the
coordinator with exact commands and log paths, on Python 3.15 only (the existing
`.venv`). No 3.11/3.14 run, full suite, independent review or reference chain is
claimed for this slice yet.

### DE02b1a round-1 Codex review repair

Round 1 review, Codex `gpt-6.1-sol` HIGH: **ISSUES**, one Critical and one
Warning; the coordinator verified both at source and accepted them. Repaired by
Claude Code `claude-opus-5-5` HIGH; no CI result is claimed.

- **Critical — final classification and retirement were two lock sections.** A
  `fail()` from another thread could latch between a call's post-call health
  check (or late-failure classification) and its unregistration, so a call still
  registered at the failure returned success or an exempt/original error instead
  of `SessionFailed`. Pi runs catch, `finally` deletion and `assertHealthy`
  synchronously. Now unregistration and final classification (success: health;
  error: late failure, exemption and first-failure latch) are ONE locked step,
  followed by report and close claim outside the lock. A `finally` guard
  unregisters only if something escapes before that step.
- **Warning — cleanup could replace an in-flight `BaseException`.** A process-
  control backend-close error (e.g. `SystemExit`) raised by the last retirer
  replaced the call's own exception (e.g. `KeyboardInterrupt`). Now a close error
  never replaces an exception the claiming thread is already raising: it is
  latched as the first cause if none exists, reported once then, and raised to
  every `close()` caller. A process-control close error is additionally raised by
  a claimer with nothing else to raise (`fail`, `request_close`, or a guarded call
  that would otherwise return), so it is never silently lost. A reporter's own
  process-control exception still propagates as before.

New permanent scenarios (105 total, 118 tests in the file):
`settle-atomic-race[acquire|release-success|exempt-error]` use a test-only
`RaceCondition` that runs `fail()` on another, joined thread exactly at the
target call's first owner-lock acquire or release after the backend returned
(no sleeps) and assert Pi's invariant: a call registered when the failure
latched ends with `SessionFailed`, otherwise it keeps its own outcome.
`close-error-never-replaces-inflight-base-exception` covers
KeyboardInterrupt + SystemExit and the closer's view;
`close-process-control-raised-when-nothing-else-raises` covers `fail` and a
retiring successful read as claimers. The pre-repair module fails exactly these
three regressions (race release success/exempt-error, in-flight replacement).
Three new private mutants each fail their designated scenario: split success
settle 1/105, split error settle 1/105, close error replaces in-flight 1/105.
Full matrix (bounded waits 1s): unmodified 0/105; read-role exemption 29;
post-call health check 4; late-error health check 4; admission seal 4;
registration 11; drain wait 9; first-cause-only 4; close-once 13; no worker
thread left alive.

## DE02b1a delivery and DE02b1b admission

DE02b1a landed at `992c210287b5f890322d494a4159c51e3ff78dfb` on origin/main (tree
`15cba7f5`, verified with `git ls-remote`). Root's local `just check` exited 0
with 13832 passed and the same two known skips; no CI result is claimed. Round 1
review, Codex `gpt-6.1-sol` HIGH: **ISSUES** with one Critical (final
classification and retirement were not one atomic step) and one Warning (a close
`BaseException` replaced an in-flight `BaseException`); both were accepted and
repaired by Claude Opus 5.5 HIGH (see the round-1 repair above). Round 2 delta
review: **CLEAN**. DE02b1a is LANDED; DE02b and DE02 stay IN_PROGRESS, DE03 OPEN
and all43 P rows OPEN.

The coordinator issued the DE02b1b work order the same day, with the read-only
oracle prep as its binding design. Root decisions on top of the prep: the
recipes stay out of Pi's window between `session.close()` and its later
line-job guard seal (that window belongs to the DE02b2 Session line); report
versus outcome order for a healthy close failure, Python-only `BaseException`
handling, non-Error JS throw values, Harness `safeReport` and close listeners are
explicitly not compared; the Node side closes only through `SessionImpl.close`
because Pi's `GuardedStorage.close` is not close-once; ordinary-error fatality is
sampled on 3 of 13 read roles (b1a's native suite covers all 13); the
implementer runs two fresh chains with no fallback runtime; b1a's private
mutants are rerun through `native()` as negative controls; every Python
observation follows a join, Event or held blocker.

## DE02b1b local guard oracle evidence

Local implementation evidence, not a landed verdict or P closure. Implementer:
Claude Code `claude-opus-5-5` HIGH, sole writer on main from clean HEAD
`992c2102`; no subagents. b1a's `session.py` and its test file are unchanged; every
prep §5 API assumption held against the landed module (the round-1 repair added
only private helpers). Upstream was read only through `git show` at the pin.

New: `scripts/durable_session_guard_comparison.py`, the driver
`scripts/durable_reference/cases/session-guard.mts`, the closed recipe
`tests/fixtures/durable/session-guard-v1.input.json`, its closed manifest
`session-guard-v1.manifest.json` and `tests/durable/test_session_guard_comparison.py`.
The coverage index moves DE02b1a to LANDED and gives DE02b1b the bounded-native
case `session-guard-b1b-v1` (IN_PROGRESS); DE02b/DE02/DE03 stay cumulative and
all43 P rows OPEN. The acceptance script and coverage tests add b1b's exclusive
ownership of that case. The manifest cascade (baseline → a1 → a2b → b1b) ran last.

**Pre-existing a2b manifest drift, repaired in the cascade.** Since the a2b
delivery commit `72253352`, `storage-memory-v1.manifest.json` pinned
`scripts/durable_memory_comparison.py` at `59ac046f…` while the committed file
hashes to `5678689d…` (the round-1 docstring repair landed after the final
manifest). `durable_memory_comparison.py run` therefore exited2 ("Table artifact
drift") on HEAD, and b1b's cascading `configuration()` would too. The cascade
refreshes exactly that one closure entry; it asserts every other a2b closure
entry already matched. A new permanent test runs the cascaded b1b
`configuration()`, which also validates the current a2b and a1 manifests.

**Design.** The Node driver subclasses the pinned `SessionImpl` (imported from its
module, since the package exports only `createSession`) with a `guarded` getter
over the protected Storage and a recording `report`; nothing else is overridden.
It closes only through `SessionImpl.close`. A controlled backend wraps real pinned
`MemoryStorage`; the native side wraps real native `MemoryStorage` the same way
and drives `SessionLifecycle` with named, non-daemon, reaped threads. 27 paired
scenarios/307 steps cover all16 delegates, 14 read variants under request error,
cancelled error and cancelled success, never-exempt commit/mint, three sampled
fatal read roles plus fatal mint, Pi tests 121/132/156/192/267/276/291/301/358/410,
a direct `fail`, health before seal, concurrent and cancelled closers, a
synchronous throw and a refused commit proven unapplied. The comparison covers
per-step outcomes, the ordered effect log and the terminal end, with a closed
error-kind projection and no class-name map.

**Not compared (Root decisions).** Pi's window between `session.close()` and its
later line-job guard seal belongs to DE02b2; recipes stay out of it and the
validator enforces a `quiesce` or close-entry barrier. Report versus outcome
order for a healthy close failure, Python-only `BaseException`, non-Error JS
throws, Harness `safeReport` and close listeners are not paired; b1a's native
tests keep the Python-only behavior. Ordinary-error fatality is sampled on 3 of
13 read roles.

**Mutants and negative controls.** All18 schema-valid guard mutants of the actual
trace give compare1 and the unmodified positive0; each also runs in the permanent
tests. Rerunning b1a's eleven private source mutants through `native()` (bounded
waits 1s) gives mismatch1 for eight: read-role exemption, post-call health check,
late-error health check, admission seal, registration, drain wait,
first-cause-only and close-once. The other three stay equal (0) by design:
split success settle and split error settle need the exact lock-boundary race
that only b1a's `RaceCondition` scenarios reach, and close-error-replaces-inflight
needs a Python-only process-control exception. No worker thread outlives a
scenario. The native recipe set is identical across 20 consecutive runs in one
test. Five actual Node runs on the exploratory development parent's snapshot (a
probe, not chain evidence) produced byte-identical traces equal to native.

**Two fresh chains.** After all code, test and coverage edits and the final
cascade, two new owner-only private roots each ran a fresh pinned baseline
acceptance, then a1, a2b and b1b against that same parent, on Node26.12.0 and
npm11.20.0 with no fallback runtime. No process group outlived its step.

| Chain | Actual exits/times |
| --- | --- |
| 1 | baseline0/45.93s → a1 0/8.00s → a2b0/16.26s → b1b0/22.48s |
| 2 | baseline0/33.34s → a1 0/7.43s → a2b0/15.98s → b1b0/21.97s |

`final-pairs.json` (sha256 `a637e0e1…d811`) binds eight distinct execution IDs
(parents `6f9723cc…`/`e654895c…`; b1b `7d305846…`/`062ed8f6…`), each child's
parent execution, the acceptance/result hashes and the manifest hashes:
baseline `829fb0e9…364c`, a1 `aed14397…09d5`, a2b `21f23ba5…dad3`, b1b
`e1fa4bf3…10b5`. Every child records `fresh_reference_bootstrap: false` and
PASS; each b1b run compared Node to native at0 and ran all18 mutants as
positive0/negative1 (72 child exits in total). Separate public CLI controls on
chain 1's evidence gave0 for observed versus native,1 for every mutant and2 for
seven invalid traces, a missing parent and an existing output, with nothing
written. The standalone `node tests/durable/test_reference_codec.mts` passes on
Node26.12.0. Ruff check/format, `mypy src tests`, `pytest tests/durable`, the
strict docs build and the rendered-link check pass locally on Python 3.15 only;
`just check`, independent review, commit and push remain Root's. No CI result is
claimed.

### DE02b1b round-1 Codex review repair

Round 1 review, Codex `gpt-6.1-sol` HIGH: **ISSUES**, two Warnings, both accepted
by the coordinator and repaired by Claude Code `claude-opus-5-5` HIGH; no CI
result is claimed.

- **Warning — an unreaped native worker could hang `run`.** Workers are
  non-daemon and `native()` ran inside the CLI process, so after cleanup raised
  the unreaped-thread error, interpreter shutdown waited on the stuck thread and
  exit2 never arrived. `run` now executes the native recipes in a bounded child
  (`… native OUTPUT`, internal subcommand) through the existing fail-closed
  `o.run` with a minimal environment and a 120s deadline; the child writes
  `native.json`/`native-context.json` and leaves through `os._exit`, so a stuck
  worker cannot block its exit, and at the deadline the whole process group is
  killed and reaped. The child's `native-guard.json` metadata is bound into
  `result.json`. New tests: a real child with a never-released worker exits2
  promptly with no `native.json`; `run` with a real native child that never exits
  returns2 after the (test-lowered) deadline, the child process is gone, and no
  `result.json`, `mismatch-report.json` or `native.json` exists. The in-process
  unreaped-thread unit test still releases its gate, so pytest never waits on it.
- **Warning — `compare` artifact overstatement.** The reference section implied
  that a `compare` mismatch writes hash-bound reports. It now states that only
  `run` writes `result.json`/`mismatch-report.json`; `compare` returns its exit
  code only and attests no provenance. The manifest scope text and this ledger
  made no such claim.

The repair changes the comparison script and its test file, both in the b1b
manifest closure, so the cascade reran (baseline, a1 and a2b manifests
unchanged; b1b manifest now `ee8dbe9d…18c6`) and both chains ran fresh again. The
earlier two chains above are superseded (their `final-pairs.json` is retained as
`final-pairs-superseded-pre-r1.json`).

| Repair chain | Actual exits/times |
| --- | --- |
| 1 | baseline0/41.50s → a1 0/7.76s → a2b0/15.98s → b1b0/22.93s |
| 2 | baseline0/40.34s → a1 0/7.62s → a2b0/16.09s → b1b0/22.79s |

The new `final-pairs.json` (sha256 `fedbb973…cf66`) binds eight distinct
execution IDs (parents `7fcae3e2…`/`cb0a2226…`; b1b `2819c878…`/`e27f03f6…`),
each with PASS, `fresh_reference_bootstrap: false`, native child exit0, Node
versus native0 and all18 mutants positive0/negative1 (72 child exits). The 29
public CLI controls on repair chain 1 again match0/1/2 exactly. `pytest
tests/durable` passes 1028; ruff, format, `mypy src tests`, the strict docs build
and the rendered-link check pass (Python 3.15 only).


## DE02b1b delivery and DE02b2a admission

DE02b1b landed at `cb38caf3d2225e4b06467208b8f31475812cc76e` on origin/main (tree
`0f105928`, verified with `git ls-remote`). Root's local `just check` exited 0
with 13924 passed and the same two known skips; no CI result is claimed. Round 1
review, Codex `gpt-6.1-sol` HIGH: **ISSUES** with two Warnings (a stuck native
worker blocked exit 2; the docs overstated `compare` artifacts), both repaired by
Claude Opus 5.5 HIGH with both fresh chains rerun (see the round-1 repair above).
Round 2 delta review: **CLEAN**, scoped only by the unchanged 557 KB recipe
fixture being too large for the review bundle. DE02b1b is LANDED; DE02b and DE02
stay IN_PROGRESS, DE03 OPEN and all43 P rows OPEN.

The coordinator issued the DE02b2a work order the same day, with the read-only
DE02b2 design proposal as its binding design. Root accepted decisions D1–D6 and
settled its open points:

1. DE02b2 splits into b2a and b2b. b2a is the private Tx core and raw-base
   drafts with no `commit`/`acommit` and no public `Session`; its tests compose
   the guard, the core, the guard's commit and adoption only inside the test
   module. b2b is the first exposed Session: owner line, async driver,
   cancellation matrix, publication and the close amendments.
2. The core is async-ready now: its operations are coroutines whose only
   suspension is the private `_Io` backend request. b2a ships the inline sync
   driver; b2b adds the async driver over the same core.
3. Asyncio cancellation reaching a running callback is the callback's own
   failure before backend admission and cannot cut owned settlement afterwards.
   This is b2b work, recorded now in plan §3.2 and the harness spec.
4. Value-based raw-base no-op suppression is accepted. Pi
   `session-documents.test.ts` 190 (a structural shift/unshift no-op still writes
   a delta) is a cumulative DE04/DE27 obligation.
5. Plan §3.2 "stop publication" after a failure means later publications.
6. Deferred scope keeps its named owners; `RawDoc` refuses task scope.
7. b2a must not change the guard/lifecycle behaviour of `session.py`; the
   owner amendments (line, close predicate) belong to b2b.
8. The clock is an injected millisecond `now`; tests use a deterministic one.

## DE02b2a local Tx core evidence

Local implementation evidence, not a landed verdict or P closure. Implementer:
Claude Code `claude-opus-5-5` HIGH, sole writer on main from clean HEAD
`cb38caf3`. Nothing is committed or pushed and no CI result is claimed.

**Shape.** The new private module `native/durable/tx.py` is not exported from
the package and has no `commit`/`acommit` or `Session` name; `session.py` is
unchanged.

- **Core and driver.** Each `_TxCore` operation is a synchronous prologue in Pi's
  order: open check (then thread affinity), the phase rule (a write sets the
  table-write phase, a read applies `ReadAfterWrite`), argument ingress and
  memo lookup. One `async` body follows. A body awaits only `_Io`, one guarded backend
  call that runs at most once, so a memoized committed task read shares its
  `_Io`. The driver turns each body into a handle that the core tracks (Pi
  `#track`), which is how a duplicate `doc()` returns the same handle. The sync
  driver runs bodies inline on the thread that created it. Retiring after an
  acquisition asks the driver for a follower handle instead of awaiting another
  operation. `settle_failure()` returns pending handles, and `settle_success()`
  raises `PendingTxOperations` carrying them; draining them is the driver's job.
- **Drafts.** One `_DraftChange` owns each document's working tree. Every
  `Draft`/`DraftList` method checks first that the change is open
  (`DraftRevoked`, a `TypeError`), then the driver's affinity
  (`TxAffinityError`). Child views are cached by node identity. Every placement
  validates and deep-copies its value, and placing a view copies its subtree.
  Slices and `pop` return detached plain values. Copy, deepcopy and pickle are
  refused, drafts are unhashable and `repr` never raises. Prepare flips the
  state for every view at once (nested, escaped or returned), before assembly
  and so before Storage admission; abort and adoption keep them revoked.
- **Cache and adoption.** `_DocumentCache` holds only `CommittedDocument`
  values, under its own lock. A cold load installs the committed incarnation, as
  Pi `#loadDocument` does. Adoption installs a created incarnation, swaps a
  changed loaded one only while it is still the occupant, and evicts a retired
  record. It returns `RawDocumentChange` values that share the cached
  `OwnedJson`.

**Test to Pi anchor mapping** (`tests/durable/test_session_tx_core.py`). The
module composes the b1a guard, the core, the guard's commit and adoption only
inside the test, in Pi `#runCommit` order, over real `MemoryStorage` behind a
delegating wrapper. Pending and late operations use a test-only stepping
driver that suspends bodies at their `_Io`, which is how b2b's async driver
will interleave them.

| Pi anchor (pinned tests) | Scenario |
| --- | --- |
| tables 82 | reads only before the first table write; documents after it |
| tables 121 | sync `set_task`/`settle_submission`/`place_submission` set the phase, also when their arguments are invalid |
| tables 104, 132 | limits and cursors pass through unchanged; minted IDs and staging |
| tables 188, 213 | staged task owner; missing, abort-marked, terminal and completing owners changed later in the same callback; nothing leaks into storage |
| tables 303, 330 | owned strict table JSON; complete replacement with deterministic `startedAt`/`endedAt` |
| tables 354 (principle) | conversation document after a table write, publication conversation |
| docs 78, 120, 137 | create on first access; cache identity; publication shares the cached value |
| docs 158, 174 | copy per placement; value no-op writes and publishes nothing |
| docs 207, 459 | escaped, nested and returned drafts revoked; cross-thread read while the backend commit is held raises `DraftRevoked` and the old revision stays visible |
| docs 228, 405 | callback failure and assembly failure abort every change |
| docs 250, 363, 380 | memoized acquisition; scope/version mismatch; non-JSON rejected before admission |
| docs 479, 519, 549, 591 | retire/recreate, retire without acquisition, retire after acquisition (sync), cold reload |
| docs 284, 311, 352 | pending acquisition rejected and drained; no late initializer or mint; expired Tx |

Pi's prologue order (`#write`, `#read`, `settleSubmission`, `setTask`) has its
own scenarios. An invalid write still sets the phase: the three sync writers,
and `append_entry`, `create_conversation`, `create_task` and `create_submission`,
with ingress failures and a validation failure inside the body. A read with
invalid arguments sets nothing, and after a write it raises `ReadAfterWrite`
first. `set_task` creates its task entry and runs the candidate checks before it
validates the record shape. Further scenarios cover submission resolution,
ingress before the guard,
draft affinity and API, the cache pointer swap and evict, adopt-once and
`RawDoc` validation.

**Negative controls.** Ten private source mutants are compiled from `tx.py`
and each must fail its designated scenarios, which pass unmodified. The mutants
remove the phase check, owner validation, the open re-check after `_Io`, the
revocation check, copy-on-placement and no-op suppression. One restores
"ingress before phase" (the phase is set only after argument admission). Three
restore the round-1 review defects: live containers in comparisons, non-exact
draft keys, and caller values in `RawDoc` and cache keys. The matrix log shows
25 of 25 designated runs failing for the intended reason and no unreaped
thread.

| Check (Python 3.15, `.venv`) | Exit / result |
| --- | --- |
| `pytest tests/durable/test_session_tx_core.py tests/durable/test_pi_comparison_coverage.py tests/durable/test_pi_comparison_acceptance.py` | 0; 175 passed |
| `pytest tests/durable/test_session_tx_core.py`, 20 sequential runs | 20 of 20 exit 0; 60 passed each |
| the same file under `-W error` (unraisable warnings as errors) | 0; 60 passed |
| `pytest tests/durable` | 0; 1092 passed |
| negative-control matrix script | 0; 25/25 designated failures, unmodified 25/25 pass |
| manifest cascade baseline → a1 → a2b → b1b, then closure verification | 0; every hashed path matches (12, 11, 18, 25 entries) |
| `ruff check .`, `ruff format --check .` | 0, 0 |
| `mypy src tests` | 0; 891 files |
| `scripts/docs.py build` (zensical `--strict`) and a rendered local link/anchor check of the five changed pages | 0 ("No issues found"); 0, 834 local links, 0 bad |

The coverage and acceptance wiring adds child rows DE02b2a (IN_PROGRESS) and
DE02b2b (OPEN) under DE02b2, moves DE02b1b to LANDED, and records Pi
`session-documents.test.ts` 190 in the DE04/DE27 observables. Because that
wiring changes files hashed by the baseline manifest, the cascade ran last: the
baseline manifest is now `e9ca8b7a…b583`, records `2429e234…bc10`, memory
`0ddf041a…9ce7` and guard `f87ca6ba…68f0`. No closure source file changed, so
no comparison chain reran.

**Pre-review ordering repair.** The coordinator's pre-review check rejected the
earlier "ingress before the phase rule" spelling as an observable divergence
from pinned Pi. `#write`, `settleSubmission` and `setTask` set the write phase
right after `#assertOpen`, before touching their arguments; `#read` applies
`ReadAfterWrite` before them. Every `_TxCore` prologue now follows that order,
and the "bad ingress flips nothing" assertions were replaced by the Pi-faithful
scenarios above, guarded by the new "ingress before phase" mutant. A re-scan of
the other prologues found one more ordering divergence, also repaired: Pi
`setTask` creates the task entry and runs the candidate checks before its copy,
so a failed call still fixes the task's position in the batch. The checks above
are the post-repair reruns (logs `fix-order-*`). Python ingress that remains
narrower than Pi's is reported to the coordinator, not changed: synchronous
ID, ownership, settlement-status and flag checks, and JSON validation of a plain
`set_task` tree before the candidate checks.

**Deviations, with reasons.**

- Operations are a synchronous prologue plus a spawned body, not one `async def`
  each. Pi's prologue errors are synchronous and its duplicate `doc()` returns
  the memoized promise; keeping both in the core avoids duplicating staging
  logic in b2b. `_Io` stays the only body suspension.
- Adoption swaps under the cache's own lock, not the owner lock, because
  `session.py` must not change; b2b may move it under the line.
- Thread affinity is enforced by the driver, so drafts carry it in b2a.
- The "open re-check before initializer, mint or install" is read as staging the
  target (Pi X612–637). Like Pi, a late cold load still installs a committed
  incarnation, which is never a candidate.
- Deferred with their owners: task-scope documents and terminal-task document
  retirement (DE04); fork, copies and families (DE27); root creation,
  `conversationCreated`, submission trace links and telemetry injection (Harness
  and observation slices); `CommitPublication` and listeners (b2b).

### DE02b2a round-1 Codex review repair

Round 1 review, Codex `gpt-6.1-sol` HIGH, of tree `e8b3474c`: **ISSUES**, one
Critical and one Warning. The coordinator accepted both, and Claude Code
`claude-opus-5-5` HIGH repaired them. No CI result is claimed. Both belong to
the same class as the earlier a2b foreign-point defect: caller-controlled Python
objects reaching internals or running hooks.

- **Critical: draft comparisons handed out the live tree.** `Draft.__eq__` and
  `DraftList.__eq__` compared their backing containers. A reflected `__eq__` on a
  caller object could therefore keep the live tree and mutate the candidate
  without validation, copy or thread checks, even after prepare revoked the
  draft. Comparisons and membership now use detached copies, so even a nested
  operand sees only copies. Every other draft path was audited (list below).
- **Warning: caller values in cache keys.** `RawDoc` accepted a `str`
  subclass as its scope, and `_resolve` put that object into the cache key, so
  its `__hash__`/`__eq__` ran under the non-reentrant cache lock (the reviewer
  reproduced a deadlock). `RawDoc` now accepts exact built-ins only for kind,
  version, scope, history and fork, and stores canonical literals. `_resolve`
  re-checks them and builds keys only from canonical built-ins, so a spec changed
  after construction raises `TypeError` before any lock.

**Audited draft paths and disposition.**

| Path | Disposition |
| --- | --- |
| `Draft`/`DraftList` `__eq__` (and the derived `__ne__`) | Both sides detached; operands are views only when their exact type is `Draft`/`DraftList` |
| `DraftList` `__contains__`, `index`, `count`, `remove` | Detached copy; `start`/`stop` must be exact ints |
| `Draft` `values()`/`items()` membership | Goes through the views' detached `__eq__` |
| `Draft` keys: `__getitem__`, `__delitem__`, `__contains__`, `get`, `pop`, `setdefault`, `__setitem__` | Exact `str` only; other keys raise or miss without hashing |
| `DraftList` indices and slices: get, set, delete, `insert`, `pop` | Exact ints; slice bounds exact ints or `None`; no caller `__index__` runs |
| Placement (`__setitem__`, `insert`, `append`, `extend`, `update`, slice assignment) | Exact view types (a spoofed `__class__` is never consulted); `OwnedJson` copy. A caller iterable runs only while it supplies values and gets no container |
| Iteration: `Draft.__iter__`, `DraftList.__iter__`/`__reversed__`, `keys()` | Yields only keys, scalars or guarded views, with a revocation check per step |
| Slicing reads, `pop`, `popitem`, `snapshot()` | Detached plain values or `OwnedJson` |
| `__repr__`, `__format__` | No payload, never raises |
| `__copy__`, `__deepcopy__`, `__reduce_ex__`, `__reduce__`, `__getstate__`, `__setattr__`, `__hash__` | Refused or unhashable |
| No `sort` or key-callback API | Not offered |

Core paths where caller values meet hashing or comparison were also checked.
The ownership kind and keys are now checked as exact strings before any set
membership test. Entry, submission and `set_task` trees need exact `str` keys.
The entry `head` is compared only as an exact `str`. A settlement is detached
before its status check, and `submission_by_request` takes an exact `str`.
Queries, limits and cursors still pass through unchanged; Memory admits them
by exact type before using them under its lock. The guard runs no argument code
under the owner lock.

New scenarios:

- `draft-comparison-never-hands-out-live-containers`. A retaining matcher is
  passed through `==` on both views and as a nested operand, then through `in`,
  `count`, `index`, `remove` and the `values()`/`items()` views. Every retained
  container is mutated before and after commit; the candidate and committed
  value stay unchanged and nothing retained is a view.
- `draft-foreign-keys-indices-and-spoofs-run-no-hook`: hashed and `str`-subclass
  keys, `__index__` objects as indices and slice bounds, and a spoofed
  `__class__`. No hook runs.
- `raw-doc-exact-builtins-and-no-hook-under-cache-lock`. A spec tampered with
  after construction goes to `cache.peek` on a joined worker thread; its hook
  uses a bounded lock acquire to detect the deadlock shape. The scenario also
  covers `str`/`int` subclass fields in the constructor and canonical stored
  values.

The three new mutants make these fail. The `RawDoc` mutant records the hook
running under the cache lock instead of hanging. The checks table above is the
post-repair rerun (logs `repair-r1-*`); the cascade reran because the coverage
observables and the plan cell changed.

### DE02b2a package inventory name repair

Root's `just check` on the round-2 snapshot (tree `7e1a96eb`) exited 1. Two
package-wide AST inventory tests in
`tests/test_native_session_extension_generation.py` scan every native module for
watched attribute names, and they counted private names in `tx.py`: attribute
loads of the plan/memo field `retire` (r3c2) and the `_DraftChange.abort()` call
(r3b). The private names were renamed (`retire` to `retiring`, `abort` to
`discard`) with no behaviour change; the inventory tests keep their scope. Both
tests, the whole extension-generation file, the architecture tests, the Tx test
file (also 20 times and under `-W error`), `pytest tests/durable` (1092), the
matrix (25/25), ruff, format and `mypy src tests` exit 0 (logs `fix-names-*`).
`tx.py` is in no manifest closure, so no hashed file changed.


## DE02b2a delivery and DE02b2b admission

DE02b2a landed at `be6ee84e0f356a62dda5adbf0ec365f05711e99f` on origin/main (tree
`b64d82bf`, verified with `git ls-remote`). Root's local `just check` exited 0
with 13988 passed and the same two known skips; no CI result is claimed. Round 1
review, Codex `gpt-6.1-sol` HIGH: **ISSUES**, one Critical (draft `__eq__` handed
live backing containers to a foreign reflected `__eq__`) and one Warning (a
`str`-subclass `RawDoc` scope hashed under the cache lock), repaired with a class
audit; round 2 delta **CLEAN**. The full check then failed two package-wide AST
inventory tests on the private names `retire` and `abort` in `tx.py`, fixed by
renaming; round 3 rename delta **CLEAN**. A coordinator pre-review also corrected
Pi's phase-before-ingress order. DE02b2a is LANDED; DE02b2, DE02b and DE02 stay
IN_PROGRESS, DE03 OPEN and all43 P rows OPEN.

The coordinator issued the DE02b2b work order the same day. The read-only DE02b2
design proposal (§1, §2, §4, §5 and the b2b rows of §7) is binding, with these
Root decisions:

1. Everything coupled to the first commit surface lands in b2b: the FIFO line
   inside the b1a `SessionLifecycle` (no second failure state), the async driver
   over the existing `_TxCore`, `commit`/`acommit`, `read_on_line`/
   `aread_on_line`, raw `snapshot`/`asnapshot`, pending seal and drain, the
   cancellation matrix, reentry by holder identity (no `RLock`), both
   acknowledgement cuts, adopt failure rejecting the triggering commit, an
   internal-listener failure poisoning while the durable commit resolves,
   contained host listeners, close listeners and every close and wait entry.
   b3 is the paired Pi acceptance only.
2. The owner amendments in `session.py` are authorized exactly as proposal §5:
   line state, tickets counting toward the close-drain predicate (requested, no
   call underway, no holder and an empty queue), the claim on line release, the
   close hook and async closed-waiters woken with `call_soon_threadsafe`. b1a and
   b1b behaviour without a line must stay identical; a change to a hashed closure
   file needs the manifest cascade last and both fresh b1b chains.
3. Asyncio cancellation reaching a running callback is the callback's own
   outcome; once the callback settles, settlement is owned and shielded and only
   the waiter gets `CancelledError`.
4. The loop's default executor, no owned threads; the capped-executor slowdown
   risk is documented.
5. Publications carry the adopted cached `OwnedJson` (identity-shared with
   `snapshot`); table changes carry the committed `StorageWrite` objects.
6. b2a carry-forwards: await an in-flight memoized `_Io` instead of re-running
   it; drain pending handles before the line is left; loop-bound affinity for
   `AsyncTx` and drafts; off-line snapshot readers see the old or the new
   revision, never a candidate.
7. b1b carry-forwards: Pi's close-to-guard-seal window (off-line guard reads
   admitted until the close line job) and `fail()`→close routed behind the
   running line job, both proven natively.
8. The Python-only rules are documented in `docs/harness-spec.md`.

## DE02b2b local Session evidence

Local implementation evidence, not a landed verdict or P closure. Implementer:
Claude Code `claude-opus-5-5` HIGH, sole writer on main from clean HEAD
`be6ee84e`; no subagents. Nothing is committed or pushed and no CI result is
claimed. Upstream was read only through `git show` at the pin.

**Shape.** The new private module `native/durable/session_core.py` holds
`Session`, `SyncTx`/`AsyncTx`, `TxOp`, the async driver and
`CommitPublication`; it is not exported from the package. `session.py` gains
the authorized owner amendments only; `tx.py` gains a shared document loader
(used by Tx acquisition and the snapshot miss) and the table-write count that
publication needs.

- **Line state machine.** The b1a `SessionLifecycle` owns one FIFO of
  `_LineTicket`s and at most one holder, under its existing `Condition`, with
  no failure state of its own. Admission checks health, then the line seal, and
  grants at once when idle. Leaving grants the next queued ticket (a sync
  ticket wakes through the Condition, an async one through
  `call_soon_threadsafe`; a wake whose loop is closed counts as cancelled and
  passes the line on). The first failure seals the line and rejects every queued
  ticket at once with `SessionFailed`. Close seals the line and runs the close
  listeners once; when the line has no holder and no ticket, the drained hook
  clears listeners and the document cache, then the guard is sealed and backend
  close requested. The backend close claim requires a requested close, no
  guarded call underway, no holder and an empty queue. Without a line every
  predicate holds, so b1a behaviour is unchanged.
- **Async driver.** `acommit()` binds the Tx to the running loop. Each
  `AsyncTx` operation's body runs eagerly up to its first backend call, which
  goes to the loop's default executor; a task continues the body on the loop.
  An in-flight memoized `_Io` is awaited through the driver's map of submitted
  calls, never run again. `TxOp.__await__` is `asyncio.shield`, every op
  outcome is observed, and a failed op nobody awaited never logs.
- **Cancellation linearization.** The cancel view is read only at the grant,
  after health. A callback runs in the caller's thread or task; asyncio
  cancellation reaching it is its own outcome. When it settles, the core is
  sealed synchronously (failure: abort; success: pending check and prepare,
  eagerly up to the first backend call), and the rest (drain, assembly, the
  guarded commit, adoption, publication, leaving the line) is an owned task the
  waiter awaits through `asyncio.shield`. A process-control exception after
  backend admission fails the owner.
- **Publication.** Built after adoption and before leaving the line, only when
  a listener exists: the committed table `StorageWrite` objects in batch order,
  then the adopted `RawDocumentChange`s, whose values are the very `OwnedJson`
  objects that `snapshot()` returns. Internal listeners run first and fail the
  owner on an exception (the rest of the publication is still delivered and
  the commit resolves); host commit and close listeners are contained and
  reported as `"listener_error"`.

**Test to Pi anchor mapping** (`tests/durable/test_session_commit.py`, real
`MemoryStorage` behind a delegating wrapper that holds a call, or faults before
delegating or after the real call). Each behaviour runs as a sync `commit()` and
an async `acommit()` variant unless noted; async variants run inside
`asyncio.run` with loop exception reports treated as failures.

| Pi anchor or plan cut | Scenario |
| --- | --- |
| docs 605, 137, 120 | publication delivered before the commit returns; table changes are the committed `StorageWrite` objects; document value `is` the snapshot value; snapshots identity-stable |
| docs 459, 207 | old revision visible (same object) while the backend commit is held; an escaped nested draft read from another thread or the loop raises `DraftRevoked`; a returned draft (async: a returned op handle) is revoked |
| docs 591 | cold cache reloads an equal, distinct incarnation; absent document is `None` |
| docs 352 | a captured Tx rejects `doc`, `conversation` and `set_task` |
| docs 174, 228, 479 | value no-op and failed callbacks publish nothing; retire and recreate publish the retired then the created change with `retiredAt`/`createdAt` |
| docs 284, 311, 337 (async only) | the line stays held (holder and queue witnessed) until a held pending acquisition drains; `PendingTxOperations`, the handle raises `TransactionSettled`, no initializer or mint; the callback error wins |
| docs 250, 549 (async only) | concurrent duplicate `doc()` returns the same handle and draft, one init, one mint; retire racing pending acquisition |
| tables 82 (async only) | an unawaited write sets the phase before suspension |
| b2a carry-forward (async only) | an in-flight memoized committed task read is awaited by a second body, one backend call (two-worker executor, FIFO sentinel) |
| queued cancel versus running callback | view-cancelled queued ticket never entered, FIFO kept; Task-cancelled queued (async) and granted-but-unused (async) tickets pass the line on; a view cancelled after the grant or during a held commit is ignored; `Task.cancel` in the callback rolls back, or commits when swallowed (async); `Task.cancel` during a held backend commit: durable, published, waiter `CancelledError` (async) |
| H276 | a caught failed read, then a write raises `SessionFailed`; then a plain return is health-checked |
| late-read discard | a held Tx read while an off-line mint fails ends with `SessionFailed`; nothing committed |
| H267, H291, docs 440 | cut before delegating, after the real commit, and a `StorageRequestError` commit: original error, owner failed, no adoption, publication or retry; the after-commit probe shows the write |
| commit-before-adopt; adopt failure | an internal listener sees the backend seq returned and the cache adopted; an injected adoption failure rejects the durable commit and fails the Session |
| H328, H466, H431 | internal listener failure poisons while the commit resolves and the rest is delivered; host commit and close listeners contained (`seen == ["commit", "close"]`); Future and coroutine listeners reported (async) and an internal coroutine listener fails the Session |
| reentry | callbacks and listeners calling commit, close, read-on-line or a snapshot miss get `ReentrantSessionCall` (a hit is allowed; `request_close()` from a listener is allowed); a spawned task queues behind its spawner (async); sync entries on a loop thread raise `RuntimeError` (async) |
| docs 622, 632 and close | close listeners once with disposers; admitted and queued commits and a queued snapshot miss settle before close; later admission "Session is closed"; guard reads admitted until the line drains, then sealed; backend closed once; a failure under a held line closes Storage only after release; close listeners before the report; bulk rejection of a queued ticket; `aclose` on the holder's loop does not deadlock (async); a cancelled close wait keeps the seal and drain |
| Python-only | sync callback returning an awaitable rolls back; process-control exception in the callback rolls back; a listener's process-control exception fails and propagates (sync) |

Two owner-level unit tests cover abandoning a queued sync ticket and a wake whose
loop is closed passing the line on, and a test checks that a lifecycle without
any line ticket still seals and closes at once (b1a behaviour).

**Negative controls.** Twelve private source mutants are built consistently
(every module from the mutated one onward, so `session.py` and `tx.py` mutants
reach `session_core.py`), and each must fail designated scenarios that pass
unmodified: line hold during drain, the pending check (`tx.py`), the settlement
shield, the `TxOp` shield, the queued-cancel cut, the post-callback health
check, adopt-fail→fail, internal-versus-host routing, the line-drain close
predicate and close-listener ordering (`session.py`), bulk rejection
(`session.py`) and the in-flight memoized call. 22 of 22 designated runs fail
for the intended reason, with no unreaped thread.

**session.py changed, so the b1b chains reran.** The cascade ran last
(baseline → a1 → a2b → b1b; only the acceptance script, the coverage test and
`coverage.json` drifted in the baseline and only `session.py` in the guard
closure): baseline `e29d0e33…d3cf`, a1 `97ab2699…8b10`, a2b `12d71a36…11e2`, b1b
`afb1a739…9b6a`; every hashed path matches (12, 11, 18, 25 entries). Two new
owner-only roots then each ran a fresh pinned baseline acceptance, a1, a2b and
b1b against that same parent on Node 26.12.0 and npm 11.20.0, with no fallback
runtime and no process group left behind.

| Chain | Actual exits/times |
| --- | --- |
| 1 | baseline0/49.86s → a1 0/8.21s → a2b0/17.21s → b1b0/26.63s |
| 2 | baseline0/49.01s → a1 0/8.90s → a2b0/18.18s → b1b0/24.26s |

`final-pairs.json` (sha256 `f7cb82e7…f2da`) binds eight distinct execution IDs
(parents `6c8111cc…`/`d8aab6e5…`; b1b `9f9178dc…`/`85e71beb…`), each child's
parent execution and the manifest hashes above. Every child records PASS and
`fresh_reference_bootstrap: false`; each b1b run has 27 scenarios and all18
mutants positive0/negative1.

| Check (Python 3.15, `.venv`) | Exit / result |
| --- | --- |
| focused: new file, b1a guard, b1b comparison, b2a core, coverage, acceptance | 0; 469 passed |
| `pytest tests/durable/test_session_commit.py`, 20 sequential runs | 20 of 20 exit 0; 94 passed each |
| the same file under `-X dev -W error` | 0; 94 passed |
| `pytest tests/durable` (after the cascade) | 0; 1186 passed |
| `tests/test_native_session_extension_generation.py` and `tests/test_architecture_*.py` | 0; 348 passed |
| negative-control matrix script | 0; 22/22 designated failures, unmodified 22/22 pass |
| manifest cascade and closure verification | 0; no mismatch |
| two fresh b1b chains | 0 and 0 (table above) |
| `ruff check .`, `ruff format --check .` | 0, 0 |
| `mypy src tests` | 0; 893 files |
| `scripts/docs.py build` (zensical `--strict`) and a rendered local link/anchor check of the five changed pages | 0 ("No issues found"); 0, 842 local links, 0 bad |

**Deviations, with reasons.**

- `aclose()` and `await_closed()` take no cancel view: cancelling the awaiting
  task is the asyncio give-up, and it keeps the seal, drain and backend close
  like a cancelled `close()`. A view would need async watchers on `CancelLatch`,
  which Root did not request.
- Every sync entry that may wait for the line (`read_on_line`, a snapshot miss,
  `close`, `wait_closed` as well as `commit`) refuses a running-loop thread, so
  no blocking wait can deadlock against an async holder on that loop.
- `read_on_line()` refuses a job returning an awaitable, like `commit()`.
- Close and failure: when the line is idle at close, the drained job runs at
  once instead of after Pi's few microtasks; Pi's close-to-guard-seal window
  exists while the line is busy, which the native test proves.
- Following an already-settled acquisition, `retire_doc` returns a settled
  handle (also in the sync driver); Pi's `.then` follower stays pending for one
  microtask.
- Close listeners on failure run on the thread that latched it (possibly an
  executor thread for an async Tx read).
- Cache adoption stays under the cache lock: all cache writes are line jobs, so
  an off-line snapshot reader sees the old or the new incarnation per document.

**Open risks for b3.** A capped or replaced default executor serializes
operations (correct, slower); asyncio shutdown cancelling a leftover settlement
after admission fails the Session (unknown outcome); the bulk rejection,
reentry and loop-thread rules have no Pi analogue and are not paired; the
listener context argument, retention/eviction, `unloadDocuments`, scoped
commits and typed documents remain with their owners.

### DE02b2b round-1 Codex review repair

Round 1 review, Codex `gpt-6.1-sol` HIGH, of tree `8d8685df`: **ISSUES**, one
Critical and four Warnings, all concurrency defects; the coordinator accepted
all five and Claude Code `claude-opus-5-5` HIGH repaired them. No CI result is
claimed.

- **Critical: lost wake-up in the line hand-off.** The leaver cleared the holder
  in one locked step and granted the next ticket in a later one. An entrant in
  between could grant the queued ticket without notifying or waking it, so both
  stayed stuck and close was blocked. Leaving now clears the holder and grants
  the next ticket in one locked step (`_pass_line`): a sync grant is notified
  under the lock, and an async grant is returned and woken outside it by the
  leaver. A wake whose loop is closed passes the line on in the same way. A line
  without a holder therefore has an empty queue, so an entrant can only grant
  itself and FIFO order holds.
- **Warning: drain under a running close hook.** A second closer could drain
  the line, clearing the close listeners, after the first closer began close but
  before its hook copied them. The drain now also requires the close-begin hook
  to have finished (`_close_hook_done`); the hook's thread drains afterwards.
- **Warning: backend close on the event loop.** `aclose()`, or a settlement
  leaving the line, could run the blocking backend `close()` on the loop. The
  owner now takes an optional close runner: the Session's runner hands a close
  claimed on a running loop to the loop's default executor (joined by
  `asyncio.run`). The claim, admission seal, close-once and shared outcome
  (`_close_error`, the end) are unchanged, and a claimer with no running loop
  still closes inline as in b1a.
- **Warning: `wait_closed`/`await_closed` from the holder.** Both now raise
  `ReentrantSessionCall` for the line holder, as `close`/`aclose` do.
- **Warning: listener finalizers under the owner lock.** Clearing the registries
  under the owner lock could drop a last reference and run a callable's
  `__del__` there. Registries are now detached under the lock and dropped after
  it (`take()`, and `discard()` returns the old list). Other drops were audited:
  tickets, futures and wake partials stay referenced by their waiters or are
  released after unlocking; publications, handles and drafts are never dropped
  under a Session lock; the document cache lock drops only owned committed values.

Regressions in `tests/durable/test_session_commit.py`:

- `hand-off-never-loses-a-wake-up` (sync and async waiter) replaces the owner
  Condition with an instrumented one that pauses the leaver right after a lock
  exit where no one holds the line but a ticket is queued. The pre-repair split
  hits that window: an entrant then grants the queued ticket there and it is
  never woken. In the repaired owner the window never occurs, and A, B, C commit
  in FIFO order.
- `second-closer-never-drains-under-a-running-close-hook` pauses the first
  closer at the same kind of barrier, once close has begun and before its hook
  ran.
- `blocking-backend-close-runs-off-the-event-loop` holds the backend close; the
  loop keeps running and the `aclose` waiter can be cancelled.
- `backend-close-awaiting-a-loop-task-completes`: a failing async commit leaves
  the line on the loop, and the backend close waits for a task on that loop.
- `wait-closed-from-the-holder-is-reentrant` (sync and async).
- `listener-finalizers-never-run-under-the-owner-lock` (sync and async): commit,
  internal and close listeners whose finalizer takes the owner lock with a
  bounded acquire.

The async `failure-while-line-held` witness now checks that the close is not even
claimed, because a claimed close may now complete on an executor thread.

Five new private mutants each make their regressions fail: the split hand-off,
the removed close-hook gate, an inline close on the loop, missing wait reentry
checks, and listeners dropped under the lock. Seventeen mutants in all; the
matrix shows 31 of 31 designated runs failing for the intended reason, with no
unreaped thread (`repair-r1-mutant-matrix.log`).

`session.py` changed again, so the cascade reran last (baseline drift: only
`coverage.json`; guard closure: only `session.py`). The new hashes are baseline
`f07ff822…3697`, a1 `d1cce3ae…c413`, a2b `7ce9cc57…8482` and b1b `34d2d284…aa1b`;
every hashed path matches. Both chains then ran fresh again on Node 26.12.0 and
npm 11.20.0, on new owner-only roots. The chains above are superseded; their
`final-pairs.json` is kept as `final-pairs-superseded-pre-r1.json`.

| Repair chain | Actual exits/times |
| --- | --- |
| 1 | baseline0/51.73s → a1 0/7.65s → a2b0/16.05s → b1b0/22.63s |
| 2 | baseline0/48.30s → a1 0/7.83s → a2b0/16.70s → b1b0/23.53s |

The new `final-pairs.json` (sha256 `59cbee88…01eb`) binds eight distinct execution
IDs (parents `a81d7374…`/`04801775…`; b1b `45c6fffa…`/`682cf10d…`). Every child
records PASS and `fresh_reference_bootstrap: false`, and each b1b run has 27
scenarios with all18 mutants positive0/negative1.

| Check (repair rerun, logs `repair-r1-*`) | Exit / result |
| --- | --- |
| focused: new file, b1a guard, b1b comparison, b2a core, coverage, acceptance | 0; 483 passed |
| new file, 20 sequential runs | 20 of 20 exit 0; 108 passed each |
| new file under `-X dev -W error` | 0; 108 passed |
| `pytest tests/durable` | 0; 1200 passed |
| extension-generation inventory and architecture tests | 0; 348 passed |
| negative-control matrix | 0; 31/31 |
| cascade and closure verification | 0; no mismatch |
| `ruff check .`, `ruff format --check .`, `mypy src tests` | 0, 0, 0 (893 files) |
| strict docs build; rendered link check of the five changed pages | 0; 0 bad links |

### DE02b2b round-2 Codex review repair

Round 2 delta review, Codex `gpt-6.1-sol` HIGH, of tree `7e763112`: **ISSUES**,
one Critical and one Warning, both introduced by the round-1 repair. The
coordinator accepted both and Claude Code `claude-opus-5-5` HIGH repaired them.
No CI result is claimed.

- **Critical: a close listener calling `close()` deadlocked.** The round-1 gate
  makes the line drain only after the close-begin hook has finished, so a close
  listener that waited for close (`subscribe_close(session.close)`) waited for its
  own hook. The pinned Pi `close()` sets `#closing` before `#notifyClose()`, so a
  nested `close()` returns the same pending closing promise without blocking or
  throwing. The owner now records the thread running the close-begin hook
  (`_close_hook_thread`). `close()` from that thread requests close and returns
  without waiting (`SessionLifecycle.close`, also reached through
  `GuardedStorage.close`); `Session.close()` skips its holder and loop-thread
  refusals there, and `aclose()` likewise returns without waiting. `wait_closed()`
  and `await_closed()` from the hook thread raise `ReentrantSessionCall`, since no
  end exists yet. `request_close()` stays allowed, and commits are refused as
  after any seal. The round-1 guarantee stands: an independent second closer
  still never drains before the first closer's hook has run.
- **Warning: a reporter process-control exception during an off-loop close was
  consumed.** The Session now records the off-loop close job. The `aclose()` or
  the settlement leave (`_aleave`) that triggered it awaits it (`_take_close`), as
  an inline claimer would. A reporter exception propagates to that claimer, and a
  process-control close error is raised there unless it is already raising.
  Other closers get the recorded close error. A job no Session waiter took goes to
  the loop's exception handler instead of being dropped. The inline path is
  unchanged.

Regressions: `close-listener-calling-close-completes` (the reviewer's exact
case, sync). `close-listeners-reentering-the-session` covers close listeners
calling `close`, `request_close`, `wait_closed` and `commit`, and through
`asyncio.run` also `aclose` and `await_closed` (sync), or spawning `aclose`,
`await_closed` and `acommit` tasks around an outer `aclose` (async). Only the
waits are reported (`ReentrantSessionCall`), plus the sealed commit.
`off-loop-close-delivers-reporter-exception-to-its-claimer` (async): the backend
close raises `RuntimeError`, the reporter raises a `BaseException`; the
triggering `aclose` gets it and a second `aclose` gets the close error.
`inline-close-reporter-exception-still-propagates` (sync) shows the inline path
is unchanged.

Two new mutants (a nested close that waits, and an `aclose` that does not take
its close job) each fail their regressions; the 19-mutant matrix shows 34 of 34
designated runs failing, with no unreaped thread (`repair-r2-mutant-matrix.log`).

`session.py` changed again, so the cascade reran last (baseline drift: only
`coverage.json`; guard closure: only `session.py`). The new hashes are baseline
`67f7ac88…9fe0`, a1 `c57695640…30fb`, a2b `d64b231e…d093` and b1b
`1335c84f…9934`, with every hashed path matching. Both chains ran fresh again on
Node 26.12.0 and npm 11.20.0 on new owner-only roots. The round-1 chains are
superseded (`final-pairs-superseded-r1.json`).

| Repair-2 chain | Actual exits/times |
| --- | --- |
| 1 | baseline0/40.94s → a1 0/7.33s → a2b0/15.42s → b1b0/23.78s |
| 2 | baseline0/39.34s → a1 0/7.82s → a2b0/16.63s → b1b0/23.43s |

The new `final-pairs.json` (sha256 `0597bf5e…2b49`) binds eight distinct
execution IDs (parents `c5b1a135…`/`8e21e7c3…`; b1b `5a959fdd…`/`6157bd2b…`).
Every child records PASS and `fresh_reference_bootstrap: false`, and each b1b run
has 27 scenarios with all18 mutants positive0/negative1.

| Check (repair-2 rerun, logs `repair-r2-*`) | Exit / result |
| --- | --- |
| focused: new file, b1a guard, b1b comparison, b2a core, coverage, acceptance | 0; 490 passed |
| new file, 20 sequential runs | 20 of 20 exit 0; 115 passed each |
| new file under `-X dev -W error` | 0; 115 passed |
| `pytest tests/durable` | 0; 1207 passed |
| extension-generation inventory and architecture tests | 0; 348 passed |
| negative-control matrix | 0; 34/34 |
| cascade and closure verification | 0; no mismatch |
| `ruff check .`, `ruff format --check .`, `mypy src tests` | 0, 0, 0 (893 files) |
| strict docs build; rendered link check of the five changed pages | 0; 0 bad links |

### DE02b2b round-3 review: simplification by Root decision

Round 3 delta review, Codex `gpt-6.1-sol` HIGH, of tree `7d31de8b`: **ISSUES**,
one Critical and three Warnings, all in the round-2 mechanism that handed an
off-loop backend close's outcome to the waiter that triggered it (`_close_job`,
`_take_close`, `_close_job_taken`, `_aleave` awaiting the close):

- Critical: a commit awaited the background close before returning, so it
  deadlocked when a commit listener requested close and the backend close
  waited for an event set after `await acommit(...)`.
- Warning: the taken flag was set before the shielded wait finished, so
  cancelling the claiming `aclose()` suppressed the fallback report.
- Warning: a second closer on another loop could take the first closer's
  future.
- Warning: the drain after a callback failure let a process-control close error
  replace the callback's error.

The finding count rose from two to four inside one mechanism, so Root
decided to remove it rather than patch it further. Claude Code
`claude-opus-5-5` HIGH removed the mechanism entirely. Async line leaves,
settlement and the drain never await a backend close again, so a commit
returns as soon as it is settled and published. The round-1 off-loop
runner stays: the close is claimed under the lock, runs in the loop's default
executor, and records the shared outcome (`_close_error`, the end) for every
closer. A process-control exception escaping that background job (a
reporter's) is caught on the executor thread and handed to the owning loop's
`call_exception_handler` through `call_soon_threadsafe`, or to
`sys.unraisablehook` if the loop is closed. It is not delivered to a specific
closer. The inline path (no running loop) is unchanged: a reporter's
process-control exception propagates to the inline claimer, as in b1a.
`_drain_async` now always leaves with `raising=True`. The round-1 fixes and the
round-2 nested-close fix are kept. `session.py` is unchanged in this round
(guard closure unchanged).

Regressions:

- `commit-result-independent-of-backend-close`: the reviewer's Critical case
  now completes.
- `concurrent-aclose-on-different-loops`: two `asyncio.run(aclose())` threads
  share one held close.
- `callback-error-survives-a-process-control-close-error` (sync and async): the
  `ValueError` reaches the committer, and closers get the recorded close error.
- `off-loop-close-reporter-outcome-reaches-loop-handler` and
  `cancelled-aclose-never-loses-the-reporter-outcome`: a custom loop exception
  handler sees the reporter's `BaseException` exactly once, while closers get
  the recorded `RuntimeError`.

The `off-loop-close-delivery` mutant is replaced by
`background-close-outcome-reported`, which swallows the outcome and fails both
handler regressions; the 19-mutant matrix shows 35 of 35 designated runs
failing, with no unreaped thread (`repair-r3-mutant-matrix.log`).

The coverage observables changed, so the cascade reran (baseline drift: only
`coverage.json`; guard closure unchanged): baseline `44d93af6…ac55`, a1
`ea6c32ce…aedc`, a2b `57981eaa…1008` and b1b `94e86343…7be8`, with every hashed
path matching. The b1b manifest file changed, so both chains ran fresh again on
Node 26.12.0 and npm 11.20.0 on new owner-only roots. The round-2 chains are
superseded (`final-pairs-superseded-r2.json`).

| Repair-3 chain | Actual exits/times |
| --- | --- |
| 1 | baseline0/47.75s → a1 0/7.43s → a2b0/16.50s → b1b0/22.05s |
| 2 | baseline0/70.28s → a1 0/8.53s → a2b0/15.88s → b1b0/22.00s |

The new `final-pairs.json` (sha256 `dcf828d1…73a7`) binds eight distinct execution
IDs (parents `d30712a0…`/`c17534ba…`; b1b `a43f106e…`/`5983eebb…`). Every child
records PASS and `fresh_reference_bootstrap: false`, and each b1b run has 27
scenarios with all18 mutants positive0/negative1.

| Check (repair-3 rerun, logs `repair-r3-*`) | Exit / result |
| --- | --- |
| focused: new file, b1a guard, b1b comparison, b2a core, coverage, acceptance | 0; 495 passed |
| new file, 20 sequential runs | 20 of 20 exit 0; 120 passed each |
| new file under `-X dev -W error` | 0; 120 passed |
| `pytest tests/durable` | 0; 1212 passed |
| extension-generation inventory and architecture tests | 0; 348 passed |
| negative-control matrix | 0; 35/35 |
| cascade and closure verification | 0; no mismatch |
| `ruff check .`, `ruff format --check .`, `mypy src tests` | 0, 0, 0 (893 files) |
| strict docs build; rendered link check of the five changed pages | 0; 0 bad links |

### DE02b2b round-4 Codex review repair

Round 4 delta review, Codex `gpt-6.1-sol` HIGH, of tree `2cdac4ac`: **ISSUES**,
one Warning caused by the round-3 repair and accepted by the coordinator. No CI
result is claimed.

- **Warning: the closed-loop fallback lost the reporter's exception.** Once its
  loop is closed, background close work passed a `SimpleNamespace` to
  `sys.unraisablehook`, which the default hook rejects with `TypeError`, so the
  exception was lost. The fallback (`_report_unhandled`) now calls
  `threading.excepthook` with a public `threading.ExceptHookArgs`. The default
  hook prints the traceback to stderr and ignores `SystemExit`. If a custom hook
  fails, the traceback is printed to stderr directly; the fallback itself never
  raises. The loop-handler path and everything else are unchanged.

Regressions (sync, on a manually managed loop whose executor holds the backend
close while the loop is closed):

- `closed-loop-background-outcome-reaches-default-excepthook` installs the stock
  `threading.__excepthook__` (pytest replaces it during tests).
- `closed-loop-background-outcome-survives-a-failing-excepthook` installs a hook
  that raises.

Both capture stderr and assert a unique marker in the reporter's exception
appears exactly once. Closers still get the recorded close error. The existing
`background-close-outcome-reported` control now also swallows the closed-loop
fallback and fails both new regressions, so there are still 19 mutants; the
matrix shows 37 of 37 designated runs failing, with no unreaped thread
(`repair-r4-mutant-matrix.log`).

No hashed file changed in this round: `session.py`, `coverage.json`, the
acceptance script and every closure file are unchanged, and a cascade dry run
reports no manifest change. The round-3 chains and `final-pairs.json`
(`dcf828d1…73a7`) therefore still bind the current manifests, and no chain
reran.

| Check (repair-4 rerun, logs `repair-r4-*`) | Exit / result |
| --- | --- |
| focused: new file, b1a guard, b1b comparison, b2a core, coverage, acceptance | 0; 497 passed |
| new file, 20 sequential runs | 20 of 20 exit 0; 122 passed each |
| new file under `-X dev -W error` | 0; 122 passed |
| `pytest tests/durable` | 0; 1214 passed |
| extension-generation inventory and architecture tests | 0; 348 passed |
| negative-control matrix | 0; 37/37 |
| cascade dry run (no hashed change) | 0; no manifest change |
| `ruff check .`, `ruff format --check .`, `mypy src tests` | 0, 0, 0 (893 files) |
| strict docs build; rendered link check of the five changed pages | 0; 0 bad links |


## DE02b2b delivery and DE02b3 admission

DE02b2b landed at `eb1e3ba739fb71b99eaad2621fc5544d07eef74f` on origin/main (tree
`c3bd05dc`, verified with `git ls-remote`). Root's local `just check` exited 0
with 14110 passed and the same two known skips; no CI result is claimed. Review,
Codex `gpt-6.1-sol` HIGH: round 1 **ISSUES**, one Critical (a lost wake-up in the
line hand-off) and four Warnings (close hook versus drain race; a blocking backend
close on the loop; missing wait reentry checks; a listener `__del__` under the
owner lock); round 2 one Critical and one Warning caused by that repair (a nested
close deadlock, fixed by following Pi's same-pending-promise behaviour; a
swallowed off-loop reporter outcome); round 3 one Critical and three Warnings,
all caused by the round-2 claimer-delivery mechanism, which Root removed (commits
never await a backend close; unobserved background-close outcomes reach the loop
exception handler); round 4 one Warning (the closed-loop fallback used an object
the default unraisable hook rejects; now `threading.excepthook` with a stderr
fallback); round 5 **CLEAN**. DE02b2b and the derived DE02b2 parent are LANDED;
DE02b and DE02 stay IN_PROGRESS, DE03 OPEN and all43 P rows OPEN.

The coordinator issued the DE02b3 work order the same day, with the read-only
DE02b3 oracle prep as its binding design and these Root decisions:

1. Probe first: a three-scenario probe (a table commit, a document
   create/change/adopt with publication, a failure/acknowledgement cut) on both
   sides and both native modes, stopping for a b3a/b3b split if a b2b product
   repair were needed, or if the work clearly exceeded about 40 scenarios or 2.5k
   new lines. Product code stays untouched inside this slice.
2. Re-check all eleven prep API assumptions against the landed b2b and adapt the
   driver, not the product.
3. Not paired, stated in the docs and the manifest scope: adoption failure,
   reentry, every `Task.cancel` row, affinity, coroutine listeners,
   bulk-rejection timing and background-close reporter routing.
4. Accepted oracle adaptations: `checkpointWhen: () => true` for byte-comparable
   raw-base writes and a structural task-definition stub.
5. DE02b3 moves to IN_PROGRESS with case `session-commit-b3-v1`; Root moves it,
   DE02b and DE02 to LANDED in the next slice's bookkeeping; all P rows stay OPEN
   and plan §5 row text is unchanged.
6. The manifest cascade baseline → a1 → a2b → b1b → b3 runs last, then two fresh
   chains on Node 26.12.0/npm 11.20.0 bind every execution ID in
   `final-pairs.json`.

The worker stopped once, at the size trigger, after the probe: no b2b product
repair was needed, and the full oracle was green in-process but about 3k lines.
Root's decisions on that report (2026-10-21):

- The sync inline-close timing (below) is accepted as a documented Python
  spelling, stays native-only in the scope string and is a DE03 risk.
- One slice, about 3k lines accepted: the script was compacted (a table-driven
  backend wrapper, dispatch tables, merged validator rules) and all four C901
  findings were fixed by restructuring, without `noqa`.
- The reported deviations are accepted: the closed `error{message}` kind with
  byte-equal messages, `unclassified` carrying its producing side, conversation
  ids in publications, return values in backend return effects, B13 through Pi
  D405's own cut, and Pi's terminal-task scan of task-scope documents named as
  not compared (DE04); the d405 bind-site fix and its validator rule stay.
- The driver's bounded private reads of `owner._cond`/`owner._queue` are
  acceptable and documented; the entry-draft copy-timing difference is recorded
  as not exercised, with a cheap paired scenario for the part both sides share.

The owner's 2026-10-21 decisions (implementer effort MAX for new dispatches;
review/repair cycles continue per slice and the owner is asked only beyond 12
review rounds) are appended to plan §7.

## DE02b3 local Session comparison evidence

Local implementation evidence, not a landed verdict or P closure. Implementer:
Claude Code `claude-opus-5-5` MAX, sole writer on main from clean HEAD
`eb1e3ba7`; no subagents. No product module or native test changed
(`session.py`, `session_core.py`, `tx.py`, `memory.py` and the b1a/b2a/b2b suites
are untouched). Upstream was read only through `git show` at the pin.

**Probe first.** Three probe scenarios (a table commit with minted IDs, a
document create/change/adopt with publication, a failure after the storage
acknowledgement) ran on a probe-only development parent against both native
modes. All eleven prep API assumptions held on the landed b2b, so no b2b product
repair was needed. The one difference was a timing rule, not a defect: in sync
mode a failing commit whose backend close the recipe held stayed unsettled where
Pi had already rejected it, because without an event loop the failing caller's
thread runs the claimed backend close inline before it returns (b2b's close rule).
Recipes therefore release such a held close before settling the commit; Root
accepted this as the Python spelling, the harness spec states it, and it is
native-only in the manifest scope.

New: `scripts/durable_session_commit_comparison.py`, the driver
`scripts/durable_reference/cases/session-commit.mts`, the closed recipe
`tests/fixtures/durable/session-commit-v1.input.json`, its closed manifest
`session-commit-v1.manifest.json` and `tests/durable/test_session_commit_comparison.py`.
The coverage index moves DE02b2b and DE02b2 to LANDED and gives DE02b3 the
bounded-native case `session-commit-b3-v1` (IN_PROGRESS); DE02b/DE02 stay
cumulative, DE03 OPEN and all43 P rows OPEN. The acceptance script and coverage
tests add b3's exclusive ownership of that case. The manifest cascade (baseline
→ a1 → a2b → b1b → b3) ran last; only the baseline entries for the acceptance
script, the coverage test and `coverage.json` changed, plus the parent hashes
that follow from them.

**Design.** The Node driver reuses b1b's bounded `SessionImpl` subclass
(`guarded` getter, recorded `report`; nothing else overridden, closed only
through `SessionImpl.close`) over a controlled backend wrapping real pinned
`MemoryStorage`, with per-operation FIFO directives (pass, throw before or after
the inner call, abort then throw, optional hold). Documents are `defineDoc`
tokens with `checkpointWhen: () => true`; `createTask` gets a structural
task-definition stub; the clock is constant and the timer declined, and the
driver asserts zero sweeps and that every admitted `document.change` is a base.
The native driver wraps real native `MemoryStorage` the same way and runs each
scenario in its mode: sync commits on named, reaped threads; async commits as
tasks on one `asyncio.run` loop whose default executor has eight workers.
Callbacks are closed programs that both sides interpret identically. 41
scenarios (405 steps; 35 scenarios/343 steps also in sync) cover the cell map's
22 atoms; the comparison covers per-step outcomes, the ordered effect log (calls
and returns with values, publications with their changes, close listeners,
reports, initializers, marks, caught errors, close entry and exit) and the
terminal end. The validator rejects unjoined concurrent forks before an
effect-producing step, guard reads after an idle close without a `quiesce`, and
any bind name bound at two sites.

**Deviations from the prep (accepted by Root).** Plain core errors that are not
a closed Session, a settled Tx or pending operations project to
`error{message}` with byte-equal messages instead of new per-class kinds;
`unclassified` carries its producing side, so it never compares equal;
publication changes carry their conversation id; backend return effects carry
the returned values; the assembly-rollback cell (prep B13) is paired through
Pi's own D405 cut; and Pi's terminal-task scan of task-scope documents is named
as not compared (DE04). The d405 scenario's snapshot binds first collided with
its callback's draft binds, so both sides agreed on a meaningless identity; the
binds were renamed and the validator now rejects any bind name bound at two
sites. The native driver's `await_queued` barrier reads the lifecycle owner's
private `_cond` and `_queue` under its condition, read-only and only in the
driver, to see that a ticket is enqueued; Pi admits at the call, so the Node
side needs no barrier.

**Entry-draft copy timing, not exercised.** Pi's `#appendEntry` reads the
caller's entry draft after its awaits; the native core copies it at the call.
A nested mutation while the append is pending is captured by both sides, and the
paired `ingress-pending-nested-copy` scenario proves it. Mutating a top-level key
while the append is pending diverges (probe only: Pi stores the changed key,
native the original, async mode), so that case is not exercised and is a
DE03/DE04 follow-up.

**Mutants and negative controls.** All24 schema-valid trace mutants give
compare1 against every native mode they run in (permanent tests), and in every
`run` the observed trace gives0 against itself and1 against each mutant
(after the round-1 repair below). b2b's nineteen private source mutants, rerun
through the native runner, flip exactly the designed cells: the line hold during
drain and the pending check in async mode; the queued-cancel cut, the
post-callback health check, internal-versus-host listener routing, the close
predicate and close-listener order in both modes. Eleven stay equal (0) by
design because only native-only paths reach them: adoption failure,
`Task.cancel` shields (two), bulk rejection, the in-flight memo, the atomic
hand-off and close-hook races, wait-closed reentry, finalizers under the lock,
nested close from a close listener and background-close routing. A close run on
the event loop fails closed (exit2: the held backend call is never released).
The native recipe set is identical across 20 consecutive runs per mode, with
151 threads started and reaped per run and none left over; three actual Node runs
on the probe parent's snapshot (a probe, not chain evidence) produced
byte-identical traces (`db979d14…8609`) equal to both native modes.

**Two fresh chains.** After all code, test and coverage edits and the final
cascade, two new owner-only private roots each ran a fresh pinned baseline
acceptance, then a1, a2b, b1b and b3 against that same parent, on Node26.12.0 and
npm11.20.0 with no fallback runtime. No process outlived its step.

| Chain | Actual exits/times |
| --- | --- |
| 1 | baseline 0/37.99s → a1 0/8.49s → a2b 0/17.61s → b1b 0/24.60s → b3 0/52.14s |
| 2 | baseline 0/40.11s → a1 0/8.33s → a2b 0/17.05s → b1b 0/24.44s → b3 0/51.86s |

`final-pairs.json` (sha256 `523e81eb…3652`) binds ten distinct execution IDs
(parents `bc60e9b4…`/`49cbf98d…`; b3 `e1652696…`/`2321d556…`), each child's
parent execution, the acceptance/result hashes and the manifest hashes: baseline
`d0c53635…9bbc`, a1 `3a0ea476…7a4c`, a2b `fdb12150…e882`, b1b `50bd7e21…b9ae`, b3
`acc9d121…dcfd`. Every child records `fresh_reference_bootstrap: false` and PASS.
Each b3 run compared Node to native at0 in both modes and ran all24 mutants as
positive0/negative1 in every mode they run in (90 child exits per run, 180 in
total); each b1b run again ran its 18 mutants as positive0/negative1. Both chains'
Node traces are byte-identical (`db979d14…8609`, as in the three probe runs), and
so are their native traces. Separate public CLI controls on chain 1's evidence
gave0 for observed versus both native modes,1 for all 45 mutant/mode pairs and2
for 18 invalid traces, a sync-versus-async native pair, a missing parent and an
existing output, with nothing written. Ruff check/format, `mypy src tests`, the
new and coverage tests under `-X dev -W error`, `pytest tests/durable` (1326
passed), the docs-dependent suites outside it, the extension-generation inventory
and architecture tests (348 passed), the strict docs build and the rendered-link
check (866 local links on the five changed pages) pass locally on Python 3.15
only; `just check`, independent review, commit and push remain Root's. No CI
result is claimed.

**Open risks for DE03.** The sync inline close makes a failing sync commit's
caller wait for its backend close, so a slow close adds latency to that one
caller; a backend close that waited on the caller's post-commit work would
deadlock (the harness spec now forbids it). The `await_queued` barrier depends on
private owner fields that a refactor could rename; a renamed field raises in the
bounded native child, which exits2, so the comparison cannot pass silently. Pi's late read of top-level entry-draft keys is
not exercised and must be decided before the product surface exposes appends.

### DE02b3 round-1 Codex review repair

Round 1 review, Codex `gpt-6.1-sol` HIGH on tree `ad6d023b`: **ISSUES**, no
Critical and one Warning, accepted by the coordinator and repaired by Claude Code
`claude-opus-5-5` MAX; no CI result is claimed.

- **Warning — a valid mismatch became exit2.** `execute_mutants()` ran each
  mutant's positive control as `compare observed.json native-<mode>.json`. Once
  the Node-versus-native comparison had already found a valid mismatch, that
  control failed ("Positive session comparison failed"), so `run` exited2
  instead of1 and wrote neither `result.json` nor `mismatch-report.json` (the
  reviewer reproduced it with the schema-valid late-read-leak mutant). The
  controls now follow a1, a2b and b1b: in every `run` the observed Node trace is
  compared with itself (positive0) and with each mutant (negative1),
  independent of the native outcome. A Node pair compares both mode
  projections, and a projection only selects scenarios, so a mutant is still
  caught in every mode that contains it; the permanent tests keep comparing each
  mutant with every native mode it runs in. a1, a2b and b1b already compare
  against the observed trace and do not have this latent pattern; nothing
  outside b3 changed. A new controller test runs the real `execute_mutants()`
  (only the Node and native children are unit children, through the existing
  `o.run` seam; every control is an actual CLI compare child) on a schema-valid
  native mismatch (late-read-leak in async mode). It requires exit1,
  `result.json` with status mismatch and Node/native codes sync0/async1, all48
  controls at0/1, and `mismatch-report.json` bound in `artifacts_sha256` and
  binding the compared traces. On the unrepaired script the same test fails
  with "Positive session comparison failed" (exit2).

The repair changes the comparison script and its test file, both in the b3
closure, so the cascade reran (baseline, a1, a2b and b1b manifests unchanged;
b3 manifest now `8dcec3f8…bace`) and both chains ran fresh again. The two chains
above are superseded; their `final-pairs.json` is retained as
`final-pairs-superseded-pre-r1.json` (sha256 `523e81eb…3652`), and the new file
binds that hash.

| Repair chain | Actual exits/times |
| --- | --- |
| 1 | baseline 0/39.33s → a1 0/8.04s → a2b 0/16.68s → b1b 0/23.63s → b3 0/30.80s |
| 2 | baseline 0/35.20s → a1 0/7.99s → a2b 0/16.88s → b1b 0/23.85s → b3 0/31.79s |

The new `final-pairs.json` (sha256 `715c3246…4613`) binds ten distinct execution
IDs (parents `6dd327dd…`/`e31ce725…`; b3 `5e8a44f5…`/`ac36aeb0…`), each with PASS
and `fresh_reference_bootstrap: false`. Each b3 run compared Node with native
at0 in both modes and ran all24 mutants as positive0/negative1 (48 control
children per run, 96 in total); each b1b run again gave its 18 mutants
positive0/negative1. The traces are unchanged: Node `db979d14…8609` and
identical native traces in both chains. The 68 public CLI controls on repair
chain 1 again match0/1/2 exactly, with nothing written. The comparison tests (102
passed, 176 with the coverage tests under `-X dev -W error`), `pytest
tests/durable` (1327 passed), the extension-generation inventory and
architecture tests (348 passed), ruff check/format, `mypy src tests`, the strict
docs build and the rendered-link check pass locally on Python 3.15 only.

## DE02b3 delivery and DE03 split

DE02b3 landed at `d2e62390612b73f5e877a21dacaeaf64c337afda` on origin/main (tree
`750ea302`, verified with `git ls-remote`). Root's local `just check` exited 0
with 14223 passed and the same two known skips; no CI result is claimed. Review,
Codex `gpt-6.1-sol` HIGH: round 1 **ISSUES**, no Critical and one Warning (a
valid Node/native mismatch became exit2 because the mutant positive controls
compared against native traces), repaired by Claude Code `claude-opus-5-5` MAX
(the controls compare the observed Node trace with itself and each mutant, as in
a1/a2b/b1b; a real-subprocess regression; both fresh chains rerun); round 2
**CLEAN**. With b1a, b1b, b2 and b3 delivered, the cumulative DE02b parent is
LANDED, and with a1, a2 and b delivered, so is DE02; the acceptance script
derives both statuses from their children. All43 P rows stay OPEN.

The coordinator issued the DE03a work order the same day, with the read-only DE03
SQLite prep as its binding design. Root accepted its recommendations D1–D13 as
written:

1. D1: payload columns are BLOB surrogatepass-UTF-8 JSON (lossless); identity
   columns are ASCII JSON.
2. D2: where Pi's Memory and SQLite disagree, Python SQLite follows Memory
   reference semantics, each difference proven by an actual paired probe in a
   later slice.
3. D3: `synchronous=FULL` plus `fullfsync` and `checkpoint_fullfsync` on macOS
   for the product; NORMAL stays available for parity and benchmark runs.
4. D4: an error raised by COMMIT poisons the raw instance.
5. D5: an existing own regular file with `nlink == 1` and group/other bits is
   chmod-ed to 0600; symlinks, hardlinks and foreign owners are refused.
6. D6: open-time `quick_check` only after an unclean shutdown; tests run
   `integrity_check` after every cut.
7. D7: no table prefix.
8. D8: shared and network filesystems are refused in DE03a.
9. D9: explicit `create` and `open` opener modes, so nothing is created by
   accident.
10. D10 (owner-approved 2026-10-21): every DE03 slice also runs its new tests on
    the Ubuntu macmini over SSH, in an isolated directory with a project-local
    venv on the system Python, recorded here as Linux evidence.
11. D11: schema v1 is Pi's v2 shape with Pi's names plus the Python deltas; DE27
    bumps the format version with the first delta.
12. D12: fork cleanup and the in-process lock-inode registry belong to DE03a.
13. D13: DE03 is split a→b→c→d, recorded before DE03a code.

Before code, the coordinator's split was recorded, preserving every original
DE03 cell cumulatively (plan §5 rows; the coverage index now has 111 rows):
**a** path ownership, owner-only files, shared-filesystem refusal and a minimal
verified connection lifecycle; **b** schema v1, the migration runner and
open-time integrity; **c** `SqliteStorage` with backend-agnostic contract cases,
real failures and crash cuts (it may split into c1 and c2 before code); **d** the
paired pinned Pi SQLite oracle, Session over SQLite and the P43 workload. The
original cells map to the children as follows: two processes or aliases, a (d
adds the Session close releasing the lock); SIGKILL owner then reopen, a, b, c
and d; transactional schema failure and newer refusal, b; acknowledgement loss,
c and d; integrity checks, b and c; no world-readable database, journal or lock,
a (c again after real workloads); query cursors, c and d; storage and private
lifecycle docs, a through d. DE03 waits all four children and DE04 waits DE03.
All43 P rows stay OPEN.

## DE03a local ownership evidence

Local implementation evidence, not a landed verdict or P closure. Implementer:
Claude Code `claude-opus-5-5` MAX, sole writer on main from clean HEAD
`d2e62390`; no subagents. Upstream was read only through `git show` at the pin.
Nothing is committed or pushed and no CI result is claimed.

**Bookkeeping before code.** The split, the DE02b3 delivery and the coverage
wiring were recorded first: plan §5 rows DE03a–DE03d (the DE03 acceptance cell
now ends "DE03 completes only after its a+b+c+d children"), 111 coverage rows,
and in the acceptance script the four children, the plan and ledger row
patterns for `c`/`d` suffixes, the cumulative DE03 parent and the evidence
anchors. The coverage tests then move to DE02b/DE02 LANDED, keep DE03 from
landing before all four children, accept only the exact child IDs and let no
DE03 row claim case evidence. The four new rows shift the plan lines of DE04–DE45
by four, so every DE source anchor was recomputed. The carried DE02b3 risk (Pi's
late read of top-level entry-draft keys) is recorded on DE04, its owner.

**Design.** Two private modules, neither exported:

- `native/durable/ownership.py`. Exact `str` path and exact `create`/`open`
  ingress, then read-only resolution: `realpath` of the deepest existing
  ancestor plus the file name (only `create` continues below missing
  directories, through plain names). Read-only prechecks refuse a missing
  (`open`) or existing (`create`, also any `-wal`/`-shm`/`-journal`) database,
  symbolic links, second hard links, other users' files and files the owner
  cannot read and write, and the filesystem classifier refuses shared and
  network mounts (macOS `fstatfs` `MNT_LOCAL` on APFS/HFS+; Linux the type of
  the directory descriptor's own `mnt_id` line in `/proc/self/mountinfo`). Only
  then are missing directories created `0o700` and the never-deleted
  `<db>.lock` opened `O_NOFOLLOW|O_CLOEXEC`, made `0600`, checked against the
  in-process registry (keyed by the lock inode) and `flock`ed non-blocking; the
  lock name must still name the locked inode, and diagnostic JSON is written.
  Under the lock `create` makes the database `O_EXCL` `0600` and flushes the
  directory (F_FULLFSYNC on macOS); `open` re-checks and tightens the database
  and sidecars (D5) and records the `-wal` size for the D6 policy. The registry
  lock is held across `fork()`; the child closes every inherited lock copy,
  poisons the owners and pins inherited connections.
- `native/durable/sqlite_connection.py`. `open_owned_database(path, mode=,
  durability=)` admits its input and checks SQLite (3.37+, serialized) before
  any effect, takes ownership, connects with `mode=rw`, checks the opened file's
  identity, sets every PRAGMA and reads it back, requires WAL (`create` switches
  the new database; `open` refuses a non-WAL file unchanged), and checks
  identity again. A failure removes what `create` made and always frees the
  lock; a cleanup failure is a note on the original error. Close: TRUNCATE
  checkpoint, connection close, lock release, each in `finally`; a repeated
  close raises the first close's error.

**Probes before code** (scratch, macOS Python 3.15.0/SQLite 3.53.1 and the
macmini's Python 3.12.3/SQLite 3.45.1): a second open file description in one
process is refused (`EAGAIN`) on both; Linux `fdinfo` `mnt_id` selects the
mount line exactly (ext4 home and `/tmp`, tmpfs `/dev/shm`); `journal_mode=WAL`
on an empty database writes a 4096-byte header at once; SQLite deletes stale
`-journal`/`-wal` files next to a new empty database; under umask `0o777` a
pre-created `0600` database still gets `0600` `-wal`, `-shm` and `-journal`;
`F_FULLFSYNC` works on an APFS directory descriptor. **Fork hazard, reproduced
on both systems:** a child forked from an owner that closes or merely finalizes
the inherited connection after the owner's clean close deletes a later owner's
crash-left `-wal` and `-shm` by name; the later owner's committed rows are lost
while `integrity_check` still says `ok`. Exiting with `os._exit` or pinning the
connection (`Py_IncRef`) keeps every row. This is why the fork hook pins.

**Tests** (`tests/durable/test_sqlite_ownership.py`, 41 functions and 115
cases; 13 cases run real children through the new bounded helper
`tests/durable/sqlite_child_support.py`, which follows `test_memory_storage.py`:
own session and process group, a JSON-line handshake, per-event deadlines,
killing and reaping the whole group and a group-gone check).

- Ingress without effects: 23 invalid inputs (bytes, `Path`, `PurePosixPath`, a
  hooked `str` subclass as path, mode or durability, NUL, empty, a trailing `/`,
  `.` and `..`, a 248-byte name, an unencodable name, `missing/..` and the four
  reserved suffixes) leave the directory empty and run no hook; the 247-byte
  boundary works; an `open` of a missing file or of missing directories creates
  nothing; an old or non-serialized SQLite is refused first; `create` leaves an
  existing database or stale sidecar untouched; a file used as a parent
  directory is refused.
- Lifecycle: `create` then `open` keep `0700` directories and `0600` files,
  write the diagnostics, use close-on-exec and remove `-wal`/`-shm` on close;
  every setting reads back for `full` and `normal`; a setting that does not
  read back refuses the store; `open` refuses a rollback-journal database and a
  garbage file unchanged, twice, so the lock was freed; a failed `create`
  removes only its own files; a cleanup failure becomes a note on the cause;
  close frees the lock and raises its first failure again; an abandoned
  transaction is discarded; a database swapped in before `connect` is never
  written; one replaced during configuration is refused; a lock file replaced
  while it is being locked is refused and its old inode is not held; every new
  directory entry is flushed.
- Aliases and links: a second in-process open through the same, a relative, a
  `..`, a `.` or a symbolic-link directory path meets the same owner; case and
  Unicode-normalization variants meet the same lock where the filesystem
  aliases them (runtime detection, never skipped) and name another file where it
  does not; a symbolic-link database name and a hard-linked database are refused
  even when unowned and without creating a lock; symbolic or hard links as
  `-wal`, `-shm`, `-journal` or lock are refused; files of another effective
  user are refused (simulated in-process, since `chown` needs privileges);
  group and other bits are removed; files the owner cannot read and write are
  refused; eight concurrent threads, opening or creating, admit exactly one
  owner; the fork hooks hold the registry lock.
- Filesystem classifier: twelve mount-table fixtures (ext4, XFS, tmpfs and an
  overmounted Btrfs accepted; proc, nfs4, cifs, fuse.sshfs, 9p, virtiofs,
  overlay and vfat refused), three fail-closed `fdinfo` cases, ten macOS cases,
  the real test directory classified local on each host, and an injected NFS
  mount refused before any effect.
- Real processes: a second process is refused with the holder's process ID in
  the diagnostic and admitted after the owner closes (same and case-variant
  spelling); SIGKILL at idle after five committed WAL transactions, then a
  reopen reports `wal_size_at_open > 0`, all rows, `integrity_check` ok, `0600`
  modes, a free lock and replaced stale diagnostics; umasks 000, 002, 022, 077
  and 777 keep every mode; an exec child holds no store descriptor; a forked
  grandchild has closed its lock copy, is poisoned, and the store opens while it
  lives on after the owner is SIGKILLed; the forked-finalization scenario with
  its unpinned negative control; garbage and misleading lock contents never
  decide.

**Private negative controls** (scratch matrix, not in the permanent suite).
Each of 27 source mutants of the two modules was applied in place, the whole new
suite ran against it, and the original bytes were restored and checked by
sha256 after every mutant. Every mutant with designated tests made them fail
(failing cases in brackets): registry check (7), `flock` (3), lock name
re-check (1), fork-child close (1), fork pin (1), fork lock hold (1), hard links
(5), symbolic links (5), owner (1), owner access by name (4), `fchmod` (2),
directory `chmod` (1), stale-sidecar refusal (3), classifier call (1), read-only
precheck (10), diagnostics (5), reserved suffixes (2), an inheritable lock (2),
non-WAL open (1), identity before configuration (2) and after it (1), lock
release on a failed close (1), failed-create cleanup (3), PRAGMA read-back (2),
`trusted_schema` (2) and the runtime check (2). The directory-flush mutant
survives by design: a flush's durability is not observable without power loss.
The permanent suite keeps the unpinned fork negative control.

| Check (macOS 27.0.1 arm64, `.venv` Python 3.15.0, SQLite 3.53.1) | Exit / result |
| --- | --- |
| `pytest tests/durable/test_sqlite_ownership.py` under `-X dev -W error`, 20 sequential runs | 20 of 20 exit 0; 115 passed each |
| the new and coverage tests together under `-X dev -W error` | 0; 216 passed |
| coverage and acceptance tests, after the cascade | 0; 153 passed |
| `pytest tests/durable` | 0; 1469 passed |
| `tests/test_native_session_extension_generation.py` and `tests/test_architecture_*.py` | 0; 348 passed |
| the 13 test files that read the changed documents | 0; 677 passed |
| `ruff check .`, `ruff format --check .` | 0, 0 |
| `mypy src tests` | 0; 898 files |
| `scripts/docs.py build` (zensical `--strict`) and a rendered-link check of the six changed pages | 0 ("No issues found"); 1026 local links on the six pages, none broken |
| private mutant matrix | 0; 27 mutants, no designated failure missing |

**Linux evidence (macmini, Ubuntu 24.04, Python 3.12.3, SQLite 3.45.1)**, per
Root decision D10. The isolated directory
`~/pipy-de03-linux/de03a-20261021T164132Z/` (mode `0700`) holds a copy of the
working tree (`git ls-files -co --exclude-standard`, 3929 files, sent with
`rsync` over SSH using the launchd agent socket for those commands only) and a
project-local venv on the system Python (`UV_PYTHON_DOWNLOADS=never uv venv
--python /usr/bin/python3.12`, then `uv sync --frozen`). The uv cache, `TMPDIR`
and every pytest `--basetemp` stayed inside it; nothing else was installed or
changed. The directory is on ext4. The pre-code probes above ran there first.

| Remote run (`run-de03a.sh`) | Exit / result |
| --- | --- |
| `uv venv`, `uv sync --frozen` | 0, 0 |
| coverage and acceptance tests | 0; 153 passed |
| the new and coverage tests under `-X dev -W error` | 0; 216 passed |
| the new tests under `-X dev -W error`, 20 runs | 20 of 20 exit 0; 115 passed each |
| processes of this user naming the directory, outside the run's own session | 0 |

The first Linux run (its logs kept as `run1`, before the early identity check)
found a time-dependent assertion: a refusal after the lock is taken rewrites
the lock's diagnostic `acquired_at`, which has one-second resolution, and the
read-back test compared the lock file's bytes. It failed in that run and in 2 of
its 20 repeats; the test now compares everything except the diagnostic
contents. That run's leftover check also matched its own SSH wrapper shell,
gone afterwards; the check now excludes its own session. On Linux the case and
normalization variants take the case-sensitive branch and the classifier the
`mnt_id` branch; the umask, fork, finalization and SIGKILL tests behave as on
macOS. Logs: `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/logs/` (local) and
`/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/linux/remote-logs/` (copied back; `run1/` holds the first run).

**Manifest cascade and chain binding.** Only three files the baseline manifest
hashes changed: the acceptance script, the coverage test and `coverage.json`. No
file of the a1, a2b, b1b or b3 closures changed. The cascade (baseline → a1 →
a2b → b1b → b3) ran last over closure and parent hashes only: baseline
`90d6d629…3d57`, a1 `177d7259…2bd9`, a2b `a6f81107…3f58`, b1b `171d8820…ab69`
and b3 `0484a4ad…265d`. Closure verification exits 0: b3's `configuration()`,
which validates b1b, a2b, a1 and the baseline, passes; no closure entry is
stale; and the key-level diff against HEAD is exactly those three baseline
entries plus the parent hashes derived from them. No chain-bound source
changed, so no chain reran, and the DE02b3 binding still holds:
`final-pairs.json` (sha256 `715c3246…4613`) binds the manifests committed at
`d2e62390` byte for byte, both chains' acceptance and child results are intact
and PASS, and of the 63 files any landed manifest hashes only the three
bookkeeping files differ from HEAD.

**Deviations from the prep, with reasons.**

1. The connection lifecycle lives in a new `sqlite_connection.py`, so
   `ownership.py` imports no `sqlite3` and DE03c builds `sqlite.py` on top.
2. Exact `str` paths only (the prep also allowed an exact `PurePath`): a probe
   showed `str(PurePosixPath("a") / StrSubclass("b"))` runs the subclass's
   `startswith` and `__radd__` hooks.
3. The fork hook also pins inherited connections, beyond "never touch an
   inherited connection": Python finalizes them at exit otherwise (probe above).
4. `open` requires an existing WAL database instead of switching it, so a
   foreign rollback-journal database keeps its bytes (a DE03b acceptance).
5. Database names ending in `.lock`, `-wal`, `-shm` or `-journal` are refused,
   so another store's sidecar is never opened as a database.
6. The Linux classifier selects the mount table line by the descriptor's
   `mnt_id`, not by path prefix; overlayfs, FUSE, 9p, virtiofs and FAT-family
   types fail closed. macOS requires `MNT_LOCAL` and an APFS or HFS+ type.
7. D5 as implemented: a file found by name must be readable and writable by its
   owner (a read-only `0400` database is refused, not made writable); a file this
   process just created under a restrictive umask is fixed through its open
   descriptor.
8. New directory entries are flushed with `F_FULLFSYNC` on macOS, matching the
   D3 durability class.

**Prep versus reality, flagged.** Ubuntu's SQLite 3.45.1 is built with
`SQLITE_SECURE_DELETE` (default on) while the macOS development build has it
off; the prep assumed "not enabled". DE03a leaves `secure_delete` at the build
default and reads it nowhere; freed-page residue therefore differs by platform
until Root decides. Root has since decided `secure_delete=ON` on every platform
(see [the secure delete decision](#de03a-secure-delete-decision)). Every other
prep assumption held on both systems.

**Open risks for DE03b and later.** (1) `open` refuses non-WAL files before any
write (round 1 showed that a hot rollback journal was still rolled back first;
see [the repair](#de03a-round-1-codex-review-repair)), but a foreign WAL-mode
file is opened, and close checkpoints it; DE03b's
application-id check must run before anything writes, and its refusal path
should close without a checkpoint if "bytes unchanged" is to hold for foreign
WAL files. (2) Close's TRUNCATE checkpoint can return busy while a foreign
reader (a DE10 inspector) holds a snapshot; it is not an error and the WAL then
stays. (3) Raw-connection users must serialize with `close()`; DE03c's authority
lock owns that. (4) A child forked by native code that skips Python's fork hooks
keeps a lock copy until it exits or executes (the owner's `LOCK_UN` still
releases it), and its inherited connection is not pinned. (5) Deleting the lock
file or hard-linking the database while it is open, and bind mounts of a single
file, are outside the contract. (6) Settled by
[the secure delete decision](#de03a-secure-delete-decision); old WAL frames keep
deleted content until a checkpoint truncates the log.

### DE03a secure delete decision

Root decision on the flagged `secure_delete` difference (2026-10-21): set
`PRAGMA secure_delete=ON` explicitly in the connection lifecycle on both
platforms and read it back like every other setting, so deleted private content
leaves no plaintext residue in freed database space and macOS and Linux behave
the same. Write-ahead-log frames and freed filesystem blocks remain a
documented limit: `secure_delete` zeroes freed b-tree content, not earlier WAL
frames, which a checkpoint removes when it truncates the log.

**Implementation.** `("secure_delete", "ON", 1)` joins the explicit settings in
`sqlite_connection._settings`. Like the others it is a connection-level setting
applied before the journal mode and read back (`PRAGMA secure_delete` must return
1), so a store whose SQLite does not honour it is refused. The module
docstring, `docs/session-storage.md` ("Deleted content"), the harness-spec
spelling list and the CHANGELOG entry state it with the limit: in WAL mode the
database file keeps earlier page images until a checkpoint copies the zeroed
pages in, and earlier WAL frames keep them until a checkpoint truncates the log.
The DE03a coverage observables now name the setting and the probe, so the
manifest cascade ran again (below).

**Tests.** The read-back test requires `secure_delete` 1 for `full` and `normal`
on both systems. A new deterministic residue probe,
`test_a_deleted_row_leaves_no_residue_in_the_database_file[ON|OFF]`, commits a
random marker row, which the closing checkpoint writes into the database file
(the closed file contains it). A second session inserts a second marker and
deletes both. While that session is open, the `-wal` still holds both markers in
its earlier frames, which is the documented limit. The `-wal` carries no SQLite
lock; the database file is read only while closed, because closing another
descriptor of it would drop SQLite's per-process locks. After close the `-wal`
and `-shm` are gone and neither marker is in the database file. The `OFF`
variant monkeypatches the setting off as a negative control and must find both
markers in the file, so the probe demonstrably sees residue.

**Harness fix found by the rerun.** One of the first twenty macOS repeats, and
one mutant run, failed in the forked-finalization test's teardown, not in its
assertions. After the forked grandchild exits and before launchd reaps it, its
process group holds only a zombie, and macOS answers `killpg` on such a group
with `EPERM`; Linux accepts it. A probe that leaves a zombie with
`waitid(WNOWAIT)` shows both behaviours. The helper read `EPERM` as "alive" and
then raised from `killpg(SIGKILL)`. It now ignores `EPERM` (and a vanished
group) when killing, and still waits, bounded, for the group to disappear; a new
deterministic test builds a zombie-only group the same way. The suite now has
118 cases.

**Negative control.** The new `secure-delete` mutant (the setting removed) fails
the read-back test for both durabilities and the probe's `ON` variant on macOS,
whose SQLite defaults to off. On Ubuntu the build default would mask it, which
is why the setting is explicit. The final matrix: 28 mutants; the unmodified
module passes all 118 cases, every mutant with designated tests fails them, and
the directory-flush mutant survives by design.

**Reruns after every change.** These results supersede the DE03a tables and
manifest hashes above.

| Check | macOS 27.0.1 arm64, Python 3.15.0, SQLite 3.53.1 | Linux macmini, Ubuntu 24.04, Python 3.12.3, SQLite 3.45.1 |
| --- | --- | --- |
| new tests under `-X dev -W error`, 20 sequential runs | 20 of 20 exit 0; 118 passed each | 20 of 20 exit 0; 118 passed each |
| the new and coverage tests under `-X dev -W error` | 0; 219 passed | 0; 219 passed |
| coverage and acceptance tests | 0; 153 passed | 0; 153 passed |
| `pytest tests/durable` | 0; 1472 passed | not run |
| extension-generation inventory and `test_architecture_*` | 0; 348 passed | not run |
| the 13 test files that read the changed documents | 0; 677 passed | not run |
| `ruff check .`, `ruff format --check .`, `mypy src tests` | 0, 0, 0 (898 files) | not run |
| strict docs build and rendered links on the six changed pages | 0 ("No issues found"); 1030 local links, none broken | not run |
| private mutant matrix | 0; 28 mutants, no designated failure missing | not run |
| leftover processes naming the directory | not applicable | 0 |

The Linux set ran in a new isolated directory,
`~/pipy-de03-linux/de03a-20261021T175216Z/`, with the same procedure: a copy of
the working tree (`git ls-files -co --exclude-standard`, 3929 files), a
system-Python venv with `uv sync --frozen`, and the uv cache, `TMPDIR` and pytest
temporary directories inside it. Its first run (logs kept as `run1/`, before the
harness fix, 117 cases) was green on every step, as `EPERM` cannot occur on
Linux; the final run above used the final tree. After it succeeded the earlier
directory `~/pipy-de03-linux/de03a-20261021T164132Z/` was deleted; nothing else
was removed and no process was left. Logs: `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/secure-delete-logs/`
(macOS) and `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/secure-delete-linux/remote-logs/` (Linux, copied back).

**Cascade.** Only `coverage.json` changed among hashed files this time. The
cascade ran last again: baseline `63f2ded1…6a08`, a1 `6b568eb9…adc0`, a2b
`774e14c8…b4e4`, b1b `643f5cc2…2ca7` and b3 `f54bd94b…eb9a`. Closure
verification exits 0 with no stale entry, and the key-level diff against HEAD
is still the three bookkeeping hashes plus the parent hashes derived from them.
No chain-bound file changed, and the DE02b3 `final-pairs.json` binding still
holds (the binding check exits 0).

### DE03a round-1 Codex review repair

Round 1 review, Codex `gpt-6.1-sol` HIGH on tree `d3bd3a8e`: **ISSUES**, no
Critical and three Warnings, all accepted by the coordinator and repaired by
Claude Code `claude-opus-5-5` MAX; no CI result is claimed.

- **Warning 1: a refused `open` let SQLite roll a hot journal back first.**
  `_configure` read `PRAGMA journal_mode` on a writable connection, and that
  first read recovered a crashed rollback-journal writer before the non-WAL
  check refused. Reproduced before the repair: the database shrank from
  1,024,000 to 8,192 bytes and its journal was deleted. Now an existing
  `-journal` refuses `open`, in the read-only precheck and again under the lock
  (a durable store is WAL-only, so a journal can only belong to a crashed
  foreign writer or an interrupted switch to WAL). `open` also reads the
  100-byte header itself through a check that `ownership.acquire` runs on a
  read-only descriptor, before the lock (a refused file gets no lock file) and
  again under it, before any mode change: the magic string, a power-of-two page
  size from 512 to 65536, and write/read versions 2 (WAL). The journal-mode check
  after connecting stays as defense in depth. After the repair the reproduction
  refuses with both files byte-identical.
- **Warning 2: a failed directory flush after `create` left a zero-byte
  database.** The flush ran outside creation's cleanup, so an injected `EIO`
  left the empty database, the lock was released without an owner, and later
  `create` and `open` both refused. The flush now sits inside the cleanup
  scope, which removes the new file only if it is still the inode this create
  made and keeps the flush error as the cause (a cleanup failure is a note).
  Reproduced before the repair; after it only the lock file remains and
  `create` succeeds on retry.
- **Warning 3: `os.waitid` in a test.** It exists on macOS only from Python
  3.13 (the local 3.11.13 and 3.12.15 interpreters have none). The helper now
  waits for the zombie state through `/proc/<pid>/stat` on Linux and
  `ps -o stat=` on macOS, checked on macOS 3.15 and 3.11 and on Linux 3.12. A
  scan of the four new files found no other 3.12+ or 3.13+ API (`pidfd`,
  `os.path.splitroot`, `Path.walk`, `itertools.batched`, `typing.override`,
  sqlite3 `autocommit`/`setconfig`), and all four byte-compile under 3.11.

**New tests** (128 cases). Header refusals for an empty file, foreign text, a
truncated header, a bad page size, a bad magic string with every other field
valid, and a real rollback-journal database: `StorageConfigurationError`,
twice, with bytes, modes and the absence of a lock file unchanged. The
journal-mode check is still exercised with the header check disabled. A real
hot journal comes from a child that crashes (`os._exit`) mid-transaction with
spilled pages; plain SQLite rolls a copy back, which proves the journal is hot,
while `open` refuses with both files byte-identical and no lock file. A stale
journal next to a WAL store is refused with everything, the lock's contents
included, unchanged. A journal that appears after the precheck, and a database
replaced with garbage, are refused under the lock. An injected flush `EIO`
after `create` leaves only the lock, and `create` then works. Tests that assert
"nothing changed" for an existing store now first give its lock distinctive
contents, so a refusal that wrongly took the lock shows even when the same
process wrote the same diagnostics within the same second; the matrix found
that gap.

**Negative controls.** Eight new mutants: the journal refusal (shared, precheck
only, under the lock only), the header check (precheck only, under the lock
only), the magic string, the format versions and the flush outside the cleanup.
A first matrix run showed that the precheck-journal and magic mutants survived;
the lock marking and the bad-magic fixture above close both gaps. Final matrix:
36 mutants, the unmodified modules pass all 128 cases, every mutant with
designated tests fails them, and only the directory-flush mutant survives, by
design.

**Results after the repair.** These supersede the earlier DE03a tables.

| Check | macOS 27.0.1, Python 3.15.0, SQLite 3.53.1 | Linux macmini, Ubuntu 24.04, Python 3.12.3, SQLite 3.45.1 |
| --- | --- | --- |
| new tests under `-X dev -W error`, 20 sequential runs | 20 of 20 exit 0; 128 passed each | 20 of 20 exit 0; 128 passed each |
| the new and coverage tests under `-X dev -W error` | 0; 229 passed | 0; 229 passed |
| coverage and acceptance tests | 0; 153 passed | 0; 153 passed |
| `pytest tests/durable` | 0; 1482 passed | not run |
| extension-generation inventory and `test_architecture_*` | 0; 348 passed | not run |
| the 13 test files that read the changed documents | 0; 677 passed | not run |
| `ruff check .`, `ruff format --check .`, `mypy src tests` | 0, 0, 0 (898 files) | not run |
| strict docs build and rendered links | 0 ("No issues found"); 1033 local links on the six pages, none broken | not run |
| private mutant matrix | 0; 36 mutants, no designated failure missing | not run |
| leftover processes naming the directory | not applicable | 0 |

The new tests also ran once on the locally installed uv Python 3.11.13 (SQLite
3.49.1) and 3.12.15 under `-X dev -W error`: 128 passed each. They used
throwaway venvs in scratch with their own uv cache, built from a snapshot of the
working tree; the project `.venv` and `uv.lock` were untouched. A first macOS
battery run of the docs-dependent tests failed the preview-server timing test
(`test_docs_build.py`, which builds its own temporary project) while those
installs loaded the machine; on a quiet machine it passed, as above.

Linux ran in a new isolated directory,
`~/pipy-de03-linux/de03a-20261021T183831Z/`, with the same procedure. Its first
run (logs kept as `run1/`, before the test tightening) was green too. After the
final run the previous directory `~/pipy-de03-linux/de03a-20261021T175216Z/` was
deleted; nothing else was removed and no process was left.

**Cascade.** `coverage.json` changed again (the observables name the header
check, the journal refusal and the flush cleanup), so the cascade ran last:
baseline `6b97645a…4df3`, a1 `ed5c537d…2729`, a2b `10833934…a19e`, b1b
`d9ea20e4…a0ce` and b3 `ac145f5f…f961`. Closure verification exits 0 with no
stale entry, the key-level diff against HEAD is still the three bookkeeping
hashes plus the derived parents, and the DE02b3 binding check exits 0.

**What remains for DE03b.** A foreign WAL-mode SQLite file passes the format
check. SQLite may then run WAL recovery, which writes only the `-shm`, and close
checkpoints it into the database. The same pre-connect header read is the place
for DE03b's identity gate (the application ID at bytes 68–71, the user version
at 60–63), so foreign or newer stores are refused before SQLite opens them and
those refusals also leave bytes unchanged. A stale `-wal` from another database
next to a valid header is still SQLite's to judge.

Logs: `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/repair-r1-logs/` (macOS: `macos-final-*.log`,
`mutant-matrix-final.log`, the reproductions `hot-journal-*-fix.log` and
`flush-eio-*-fix.log`, cascade, closure and binding), `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/repair-r1-pycompat/`
(Python 3.11 and 3.12) and `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/repair-r1-linux/remote-logs/` (Linux).

### DE03a round-2 Codex review repair

Round 2 review, Codex `gpt-6.1-sol` HIGH on tree `79b1a51b`: **ISSUES**, one
Critical, caused by the round-1 repair, accepted by the coordinator and
repaired by Claude Code `claude-opus-5-5` MAX; no CI result is claimed.

- **Critical: a refused second open released the owner's SQLite locks.** The
  round-1 header precheck opened and closed the database before the in-process
  registry was consulted. POSIX record locks belong to the process, and
  closing any descriptor of a file drops all of them, SQLite's included.
  Reproduced before the repair on macOS, and on Linux against the round-1
  sources: with owner A holding a committed row, an external
  `locking_mode=EXCLUSIVE` writer got "database is locked" before the refused
  second open and wrote after it, and A's later write dropped the intruder's
  row. After the repair the writer stays blocked, A's rows survive and
  `integrity_check` is ok.

**Repair.** The descriptor rule is now an invariant, documented in both
modules' docstrings and in the harness spec for the later DE03 slices (DE03b's
identity gate reads header bytes too):

- The read-only precheck uses `lstat` only again. The header check moved into
  a gate that runs after the directory is opened and before the lock, under
  the registry lock, and only once neither the lock inode nor the database
  inode is owned in this process.
- The registry also holds each owner's database inode, claimed under the
  registry lock before any descriptor opens it and before connecting. An owned
  database under another name (a rename, or a bind-mounted single file) is
  refused unread as well. (Round 5: renaming a held store's files is outside
  the contract; this refusal then holds only within the owning process, as
  best-effort detection.)
- Every descriptor on an existing store file (database, sidecar or lock file)
  is opened under the registry lock and identified by `fstat` first. One that
  reached another owner's claimed database, because a rename raced the open,
  is parked on that owner and closed in its release, after its connection is
  closed; any other is closed under the same lock hold, so no owner can claim
  and connect in between. This also closes a second data-loss path in the
  round-1 code, probed with its sources: a rename racing the lock-file open
  let the Linux lock step take `flock` on the owned database and write its
  diagnostics into it (8192 bytes became 86, and after the owner closed SQLite
  reported "file is not a database"); on macOS the `flock` conflicted and the
  open was refused.
- The storage document tells callers not to open an owned store with a second
  SQLite connection from the owning process.

**Audit.** Every place the two modules and the test helpers open a descriptor
on a store file, or might while a connection of the process exists:

| Site | Round 1 | Now | Why it is safe |
| --- | --- | --- | --- |
| `open` precheck | opened and closed the database for the header before the registry check: **unsafe** (the Critical) | `lstat` only | no descriptor |
| header gate (new), before the lock | — | under the registry lock, after the lock-inode and database-inode check | no owner of that inode exists in the process, and owners claim before they connect; an inode a racing rename substituted is identified and parked if claimed |
| lock step: lock file open, `fchmod`, diagnostics, close on refusal | identity checked only after `flock`: **unsafe under a race** (the truncation above) | the descriptor is identified first and parked if a rename put a claimed database there | `flock` belongs to one open file description, so closing another descriptor of the lock file never releases it (probed on macOS and Linux); SQLite never opens the lock file |
| header re-read under the lock | own lock held, not connected; a rename between `lstat` and `open` could reach another owner's database: **unsafe under a race** | claim, then the identified open, all under the registry lock | as for the gate |
| owner-only normalization (`fchmod`, D5) of the database and sidecars | the same race | the identified open under the registry lock | as above; sidecars are not claimed (documented limit: another owner's live `-shm` reaches a sidecar name only by a rename that already breaks that owner's store) |
| `create` | new `O_EXCL` inode | claimed under the registry lock before `fchmod` | nobody holds locks on a new inode |
| failed-create cleanup, `discard_created_files` | directory descriptor and `unlink` by name | unchanged | no descriptor on the file |
| identity checks after connecting | `os.stat` | unchanged | no descriptor |
| close and failed open | SQLite closes its own descriptors, then the lock descriptor closes | the release also closes parked descriptors, after `flock` is released and still under the registry lock | the owner's connection is closed, and no new owner claims and connects during the release |
| fork child hook | closes inherited lock copies | also parked copies | record locks are not inherited, and closing (never `LOCK_UN`) a shared `flock` description keeps the parent's lock |
| lock diagnostics read and write | lock file only | unchanged | lock file |
| filesystem classifier (`/proc/self/fdinfo`, `/proc/self/mountinfo`), directory descriptors and flushes | not store files | unchanged | SQLite takes no locks there |
| test helper `tree()`, which reads every file | ran in the alias test while the store was open: **unsafe** | that test uses the new `lstat`-only `listing()`; every other call runs with no store open in the process, as its docstring now states | no descriptor while connected |
| residue test reading `-wal` while connected | harmless (SQLite takes no lock on `-wal`) but against the rule | reads it in a child process | another process |
| other test reads (`read_bytes`, raw `sqlite3.connect`) | all with no store open in the process | unchanged | checked one by one |

**New tests** (136 cases, 8 new):

- The alias test now holds a committed row, tries `open` and `create`
  through each alias (the same path, relative, `..`, `.`, a symlinked
  directory), and asserts that an external `locking_mode=EXCLUSIVE` writer
  stays blocked, that nothing was opened (no parked descriptor) and, after a
  further write and a reopen, that every row is there and `integrity_check` is
  ok. The case and Unicode-normalization variants (runtime-detected) and the
  concurrent in-process open race check the same. Behavior is asserted before
  mechanism, so the tests fail on other implementations for the lost lock.
- An owned database renamed to another name, or moved in under the opened
  name after the lock, is refused unread. The moved-in case runs through
  `open_owned_database` and through `acquire_path`, which reads no header, so
  only the claim can refuse it. (Round 5: both are best-effort detection.)
- The lock step's own registry check refuses a second in-process owner before
  `flock`.
- A rename that puts an owned database under the opened name at each
  descriptor open (the gate, the re-read under the lock, the normalization,
  the lock file) is refused, the external writer stays blocked, and the parked
  descriptor is closed once the owner closes.
- Against the exact round-1 sources (from tree `79b1a51b`), 14 of the 17
  selected tests fail on macOS: 7 with `'wrote' == 'database is locked'` (the
  five aliases and both case or normalization variants), the renamed and
  moved-in cases with `disk I/O error` or "DID NOT RAISE", and the race cases
  with another error or none. The three that pass are the lock step's registry
  check (round 1 had it) and the two concurrent runs, which only catch the bug
  when a refused thread reads after the winner connected. On Linux 12 of 17
  fail: the five aliases with `'wrote'` (ext4 is case-sensitive, so the
  variants name other files and pass), and the same inode and race cases.

**Negative controls.** Seven new mutants: the round-1 shape (a header read
through a raw descriptor before the gate), the gate without its ownership
check, database inodes ignored by the registry check, no claim under the lock,
a claimed descriptor closed instead of parked, the lock step without its
identity check, and parked descriptors never closed. Six mutants were
re-anchored to the moved code. Removing the whole precheck no longer fails the
hard-link test, because the gate now repeats the `lstat` checks before the
lock in `open` mode, so that mutant is designated to the `create` and
stale-journal refusals; it still fails 21 tests. Final matrix: 43 mutants, the
unmodified modules pass all 136 cases, every mutant fails its designated
tests, and only the directory-flush mutant survives, by design.

**Results after the repair.** These supersede the earlier DE03a tables.

| Check | macOS 27.0.1, Python 3.15.0, SQLite 3.53.1 | Linux macmini, Ubuntu 24.04, Python 3.12.3, SQLite 3.45.1 |
| --- | --- | --- |
| new tests under `-X dev -W error`, 20 sequential runs | 20 of 20 exit 0; 136 passed each | 20 of 20 exit 0; 136 passed each |
| the new and coverage tests under `-X dev -W error` | 0; 237 passed | 0; 237 passed |
| coverage and acceptance tests | 0; 153 passed | 0; 153 passed |
| `pytest tests/durable` | 0; 1490 passed | not run |
| extension-generation inventory and `test_architecture_*` | 0; 348 passed | not run |
| the 13 test files that read the changed documents | 0; 677 passed | not run |
| `ruff check .`, `ruff format --check .`, `mypy src tests` | 0, 0, 0 (898 files) | not run |
| strict docs build and rendered links | 0 ("No issues found"); 1035 local links on the six pages, none broken | not run |
| private mutant matrix | 0; 43 mutants, no designated failure missing | not run |
| the reviewer's scenario (probe) | round 1: the writer wrote; now: blocked, rows and `integrity_check` ok | the same |
| closing another lock-file descriptor (probe) | `flock` kept | `flock` kept |
| rename racing the lock-file open (probe) | round 1: refused; now: refused, data intact | round 1: database truncated, "file is not a database"; now: refused, data intact |
| new regressions on the round-1 sources | exit 1; 14 of 17 fail | exit 1; 12 of 17 fail |
| leftover processes naming the directory | not applicable | 0 |

The new tests also ran on the locally installed uv Python 3.11.13 (SQLite
3.49.1) and 3.12.15 (SQLite 3.53.1) under `-X dev -W error`: 136 passed each,
in throwaway venvs in scratch built from a snapshot of the working tree; the
project `.venv` and `uv.lock` were untouched. A first macOS battery and a first
Linux run (kept as `run1/`), before the behavior-first reordering of the
assertions, were green with the same counts.

Linux ran in a new isolated directory,
`~/pipy-de03-linux/de03a-20261021T194236Z/`, with the same procedure plus the
probes and the round-1 sources for the negative control. After the final run
the previous directory `~/pipy-de03-linux/de03a-20261021T183831Z/` was deleted;
nothing else was removed and no process was left.

**Cascade.** `coverage.json` changed again: the DE03a observables name the
ownership check before the header read, the database-inode claims, the parked
descriptors and the lost-lock regressions. That one string is the only change
against the round-1 tree. The cascade ran last: baseline `0be20682…c0e2`, a1
`7a081f05…bd6d`, a2b `ca921c09…fd02`, b1b `23b30250…13ca` and b3
`6f40e51d…da39`. Closure verification exits 0 with no stale entry, the
key-level diff against HEAD is still the three bookkeeping hashes plus the
derived parents, and the DE02b3 binding check exits 0 (no chain-bound source
changed).

**What remains for DE03b.** The identity gate (application ID, user version)
reads header bytes too, so it runs inside the same format check, or through
another descriptor opened by the same identified-open rule.

Logs: `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/repair-r2-logs/` (macOS: `macos-final2-*.log`,
`mutant-matrix-final2.log`, the probes `lost-locks-*-fix.log`,
`flock-close-macos.log` and `probe-lockfile-race-*-macos.log`,
`regressions-on-round1-sources-final.log`, cascade, closure and binding),
`/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/repair-r2-pycompat/` (Python 3.11 and 3.12) and
`/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/repair-r2-linux/remote-logs/` (Linux).

### DE03a round-3 Codex review repair

Round 3 review, Codex `gpt-6.1-sol` HIGH on tree `9634edeb`: **ISSUES**, one
Critical, caused by the round-2 repair, accepted by the coordinator with a Root
decision, and repaired by Claude Code `claude-opus-5-5` MAX; no CI result is
claimed.

- **Critical: a claim moved while a descriptor of its first inode stayed
  parked.** `_claim_database` replaced an owner's claim. A's lock step claimed
  X, a racing open parked an X descriptor on A, a replacement made A's checks
  under the lock move the claim to Y, and another owner then opened X; closing
  A closed the parked X descriptor and released the new owner's SQLite locks.
  Reproduced before the repair on macOS and Linux: an external
  `locking_mode=EXCLUSIVE` writer was blocked before A closed, wrote after, and
  its row was gone after the new owner's next write.
- **Root decision:** a database-inode claim is never reassigned. A name that
  now names another inode, before or after connecting, fails the open; the
  claim stays until everything parked on it is closed under the registry
  lock, and only then is released.

**Repair.**

- `_claim_database` refuses (`UnsupportedStoragePath`, "changed while
  opening") when the owner already claims another inode; it never moves a
  claim. Parked descriptors therefore always reference their owner's claimed
  inode, and the owner's release closes them in the registry lock hold that
  drops the claim.
- Right after connecting, before any SQL, `PathOwner.check_connected()`
  proves from the process's descriptors (`/dev/fd`, `/proc/self/fd`) that
  SQLite opened the claimed inode: a descriptor of it that is neither a lock
  nor a parked descriptor exists. It replaces the name check before
  configuration; the name is still checked after configuration. Name checks
  cannot see a rename away and back around the connect, and that case, probed
  on the round-2 code on both systems, left A's connection on an inode nobody
  had claimed: Y's own next owner read its header through a raw descriptor,
  which dropped A's locks, and an external writer got in.
- A failed `create` removes files by name only while the name still names the
  database it made. Before, the sidecar names were unlinked unconditionally,
  so a file renamed under one of them was deleted; now the skipped cleanup is
  a note on the original error.
- Docs: both module docstrings, the harness-spec invariant (now a list), the
  storage document and the changelog.

**The invariant, site by site.** A descriptor of inode I is closed only while
no SQLite connection of this process can hold locks on I. Every connection of
the process belongs to an owner (a second connection from the owning process
is outside the contract); it holds no lock before its first statement, and
`check_connected` proves before that statement that it sits on its owner's
claimed inode. Claims are exclusive, never move, and are dropped only after
the owner's connection is closed. So a connection that can hold locks on I
means I is claimed by a live owner.

| Site | Why it holds |
| --- | --- |
| header gate before the lock | under the registry lock, I is claimed by nobody (checked by `lstat` before opening, and the opened descriptor is identified by `fstat`: a claimed inode is parked, never closed); nobody can claim and connect before the close in the same hold |
| lock step | the lock descriptor is identified first and parked if it reached a claimed database; otherwise it is the lock file, which SQLite never opens, or an unclaimed inode; closing another descriptor of the lock file keeps its `flock` |
| claim and checks under the lock | the claim is made (or the open fails) before any descriptor; the re-read and the owner-only normalization identify each descriptor, park a claimed one, and close only this owner's claimed inode, which it has not connected to, or a sidecar (unclaimed by design; documented limit) |
| `create` | a new `O_EXCL` inode, claimed under the registry lock before `fchmod`; nobody holds locks on it |
| connecting | SQLite opens by name; until its first statement it holds no locks, and `check_connected` refuses before that statement unless it sits on the claimed inode; refusing closes it through SQLite, whose own descriptors are released by SQLite |
| release (`_unlock`) | parked descriptors reference this owner's claimed inode, which only this owner may connect to; its connection is closed before the release; they are closed in the registry lock hold that drops the claim, after `flock` is released, so nobody claims and connects in between |
| failed open, failed `acquire` | the connection, if any, is closed first; cleanup uses a directory descriptor and unlinks by name only while the name names the created inode; then the release above |
| close | checkpoint, connection close, then the release above |
| fork child hook | closes inherited lock and parked copies, never `LOCK_UN`; record locks are per process and not inherited, so the child holds none, and the parent's `flock` description stays locked by its own copy; inherited connections are pinned |
| descriptor scan, identity checks after connecting | `fstat` and `os.stat` only; listing `/dev/fd` or `/proc/self/fd` opens and closes a descriptor of that directory, which no lock covers |
| tests | `tree()` only with no store open in the process, `listing()` by `lstat`, the `-wal` scan in a child; the new tests read Y's bytes only after the refused connection was closed |

**New tests** (141 cases, 4 new, one case added to an existing test):

- The reviewer's sequence, through a seam around the checks under the lock:
  A's open is refused, the parked descriptor is closed at its release, and
  X's next owner keeps the external writer out and its rows after A is gone.
  The assertions are behavior first, so the test fails on the round-2 code
  with `'wrote' == 'database is locked'`.
- The same race with the replacement after the checks under the lock: the
  connection lands on Y and is refused right after connecting, Y's bytes are
  unchanged, and X's next owner keeps its locks.
- A rename away and back around the connect: the open is refused although
  every name check passes, and Y's own next owner keeps the external writer
  out.
- A failed `create` whose name was replaced removes nothing by that name: a
  foreign file under a sidecar name survives, with the skipped cleanup as a
  note.
- A valid store moved under the name after the lock is refused, a third case
  of the change-after-precheck test; garbage is now written in place there, so
  that case still reaches the header re-read under the lock.
- Against the exact round-2 sources (from tree `9634edeb`), on macOS and
  Linux alike, 4 of the 7 selected tests fail: the sequence and the
  away-and-back case with `'wrote'`, the moved-in store with "DID NOT RAISE"
  and the failed create with the foreign file deleted. The connect-time
  variant passes there too (round 2 refused it already; it covers the
  release order).

**Negative controls.** Three new mutants (a claim that moves, no descriptor
check after connecting, failed-create cleanup without the identity check);
the old name-check-before-configuration mutant became the descriptor-check
mutant, and the claim mutant was re-anchored. Final matrix: 45 mutants, the unmodified modules pass
all 141 cases, every mutant fails its designated tests, and only the
directory-flush mutant survives, by design.

**Results after the repair.** These supersede the earlier DE03a tables.

| Check | macOS 27.0.1, Python 3.15.0, SQLite 3.53.1 | Linux macmini, Ubuntu 24.04, Python 3.12.3, SQLite 3.45.1 |
| --- | --- | --- |
| new tests under `-X dev -W error`, 20 sequential runs | 20 of 20 exit 0; 141 passed each | 20 of 20 exit 0; 141 passed each |
| the new and coverage tests under `-X dev -W error` | 0; 242 passed | 0; 242 passed |
| coverage and acceptance tests | 0; 153 passed | 0; 153 passed |
| `pytest tests/durable` | 0; 1495 passed | not run |
| extension-generation inventory and `test_architecture_*` | 0; 348 passed | not run |
| the 13 test files that read the changed documents | 0; 677 passed | not run |
| `ruff check .`, `ruff format --check .`, `mypy src tests` | 0, 0, 0 (898 files) | not run |
| strict docs build and rendered links | 0 ("No issues found"); 1037 local links on the six pages, none broken | not run |
| private mutant matrix | 0; 45 mutants, no designated failure missing | not run |
| the reviewer's sequence (probe) | round 2: the writer wrote after A closed; now: A refused, blocked, rows and `integrity_check` ok | the same |
| a rename away and back around the connect (probe) | round 2: A opened on Y and the writer got in; now: refused | the same |
| new regressions on the round-2 sources | exit 1; 4 of 7 fail | exit 1; 4 of 7 fail |
| leftover processes naming the directory | not applicable | 0 |

The new tests also ran on the locally installed uv Python 3.11.13 (SQLite
3.49.1) and 3.12.15 (SQLite 3.53.1) under `-X dev -W error`: 141 passed each,
in throwaway venvs in scratch built from a snapshot of the working tree; the
project `.venv` and `uv.lock` were untouched.

Linux ran in a new isolated directory,
`~/pipy-de03-linux/de03a-20261021T205626Z/`, with the same procedure plus the
probes and the round-2 sources for the negative control. After the run the
previous directory `~/pipy-de03-linux/de03a-20261021T194236Z/` was deleted;
nothing else was removed and no process was left.

**Cascade.** `coverage.json` changed again: the DE03a observables name the
never-moving claims, the descriptor check after connecting, the failed-create
rule and the round-3 regressions. That one string is the only change against
the round-2 tree. The cascade ran last: baseline `fd565374…82fb`, a1
`ec656105…ae24`, a2b `89a78a92…30cd`, b1b `2542a650…47b1` and b3
`785ac100…8990`. Closure verification exits 0 with no stale entry, the
key-level diff against HEAD is still the three bookkeeping hashes plus the
derived parents, and the DE02b3 binding check exits 0 (no chain-bound source
changed).

Logs: `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/repair-r3-logs/` (macOS: `macos-final-*.log`,
`mutant-matrix-final.log`, the probes `claim-reassign-*-fix.log` and
`connect-aba-*-fix.log`, `regressions-on-round2-sources.log`, cascade, closure
and binding), `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/repair-r3-pycompat/` (Python 3.11 and 3.12) and
`/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/repair-r3-linux/remote-logs/` (Linux).

### DE03a round-4 Codex review repair and contract boundary

Round 4 review, Codex `gpt-6.1-sol` HIGH on tree `0f4d2771`: **ISSUES**, two
Critical, and the review harness reported non-convergence (exit 4: the
Critical count did not fall over two rounds). Repaired by Claude Code
`claude-opus-5-5` MAX under a Root decision to simplify; no CI result is
claimed.

- **C1, coupled to the round-3 repair.** The descriptor scan after connecting
  could not prove that the matching descriptor belonged to this connection.
  Overlapping opens can each pass on the other connection's descriptor, and
  pinned descriptors inherited across `fork()` fool it too.
- **C2, caused by the round-3 repair.** Failed-create cleanup checked the
  database's identity before the sidecar loop and then unlinked the database
  unconditionally, so a replacement arriving during the loop was deleted.
  Reproduced before the repair with an injected configuration failure and a
  replacement at the first sidecar `lstat`: the replacement was gone.
- **Root decision.** Every finding since round 2 needed store files renamed
  or replaced during an open. Instead of patching further, that window
  becomes an explicit contract boundary, the descriptor scan goes, and C2 is
  fixed anyway.

**Removed.** `PathOwner.check_connected()`, its `/dev/fd` and `/proc/self/fd`
scan (`_open_descriptors`, `_identity`, `_DESCRIPTORS`), the test
`test_a_connection_that_landed_on_another_inode_is_refused_though_the_name_returns`
(it asserted a guarantee for a rename away and back around the connect, now
outside the contract, and only the removed scan made it pass) and the scan's
mutant.

**Kept.** Exact-`str` ingress; the `lstat`-only precheck; the in-process
registry check by lock inode and database inode before any descriptor on a
store file is opened (round 2: a second open in the owning process never opens
or closes a descriptor of an owned inode); claims that never move (round 3);
parking of any descriptor that `fstat` shows reached a claimed inode, closed
only with its claim; and the `os.stat` name checks against the claimed inode
under the lock, right after connecting (restored in place of the scan) and
after configuration, refusing on a mismatch as best-effort detection.

**C2 repair.** `discard_created_files` re-checks, right before each unlink (a
sidecar or the database), that the name still names the inode the create made;
otherwise it unlinks nothing more and the cause becomes a note on the original
error. The regression replays the reviewer's sequence and fails on the exact
round-3 sources (the only test of the file that does).

**Contract boundary**, now in both module docstrings, the harness spec and the
storage document:

> The store directory is private to the owning user (owned by that user, mode
> `0700`; missing directories are created that way, and keeping an existing one
> private is the caller's part). Renaming, replacing or hard-linking store
> files (the database, its `-wal`, `-shm` and `-journal`, and the lock file),
> by any actor, while an open, create or close of that store is in progress is
> outside the contract: the identity checks under the lock, after connecting
> and after configuration, the parking of descriptors that reach a claimed
> database and the re-check before each unlink of a failed create are
> best-effort detection, not a guarantee. Outside that window the guarantees
> hold: one owner per store across processes and within the process,
> owner-only file modes, no descriptor of a store file another owner in this
> process holds is ever closed by pipy, and invalid input, an `open` of a
> missing path and a store refused before its lock is taken have no filesystem
> effect. Pi durable 1.3.0 has no cross-process locking at all, so this remains
> strictly stronger than the reference.

This supersedes the guarantee wording of the round-2 and round-3 sections for
renames during an open. The changelog says the same in one sentence. (Round 5:
this boundary is superseded; it now covers the whole ownership lifetime.)

**Tests.** 141 cases.

- Removed: the away-and-back test above.
- Reclassified, unchanged, into a "Best-effort detection (outside the
  contract)" section at the end of the file, with their two helpers. They
  stay because each pins a check the code keeps and passes deterministically:
  - the change after the precheck (journal, garbage in place, a valid store
    moved in);
  - the lock file replaced while locking;
  - the database swapped before connecting;
  - the database replaced while configuring;
  - an owned database moved in after the lock (both openers);
  - a rename racing each descriptor open (four moments);
  - the round-3 claim sequence;
  - the replacement seen at connect;
  - the failed create whose name was replaced;
  - the new cleanup regression.
- Kept as guarantees, since none renames anything during an open, create or
  close: the second process and every in-process alias, case and Unicode
  variants, the concurrent in-process opens, the lock step's registry check, an
  owned database under another name (renamed while idle, before the second open
  starts; round 5 moved it to best-effort detection), hard links and symbolic
  links made before the open, and all mode,
  umask, fork, exec, SIGKILL, header, journal, durability and residue tests.

**Negative controls.** The scan's mutant was dropped. The mutant for the name
check right after connecting is back, and the round-3 cleanup mutant was
replaced by two, one for the re-check before a sidecar unlink and one for the
re-check before the database unlink. Final matrix: 46 mutants,
the unmodified modules pass all 141 cases, every mutant fails its designated
tests, and only the directory-flush mutant survives, by design.

**Results after the repair.** These supersede the earlier DE03a tables.

| Check | macOS 27.0.1, Python 3.15.0, SQLite 3.53.1 | Linux macmini, Ubuntu 24.04, Python 3.12.3, SQLite 3.45.1 |
| --- | --- | --- |
| new tests under `-X dev -W error`, 20 sequential runs | 20 of 20 exit 0; 141 passed each | 20 of 20 exit 0; 141 passed each |
| the new and coverage tests under `-X dev -W error` | 0; 242 passed | 0; 242 passed |
| coverage and acceptance tests | 0; 153 passed | 0; 153 passed |
| `pytest tests/durable` | 0; 1495 passed | not run |
| extension-generation inventory and `test_architecture_*` | 0; 348 passed | not run |
| the 13 test files that read the changed documents | 0; 677 passed | not run |
| `ruff check .`, `ruff format --check .`, `mypy src tests` | 0, 0, 0 (898 files) | not run |
| strict docs build and rendered links | 0 ("No issues found"); 1039 local links on the six pages, none broken | not run |
| private mutant matrix | 0; 46 mutants, no designated failure missing | not run |
| the whole test file on the round-3 sources | exit 1; only the cleanup regression fails (1 failed, 140 passed) | exit 1; the same |
| leftover processes naming the directory | not applicable | 0 |

The new tests also ran on the locally installed uv Python 3.11.13 (SQLite
3.49.1) and 3.12.15 (SQLite 3.53.1) under `-X dev -W error`: 141 passed each,
in throwaway venvs in scratch built from a snapshot of the working tree; the
project `.venv` and `uv.lock` were untouched.

Linux ran in a new isolated directory, `~/pipy-de03-linux/de03a-20261021T214058Z/`, with the round-3 sources
for the negative control. After the run the previous directory
`~/pipy-de03-linux/de03a-20261021T205626Z/` was deleted; nothing else was
removed and no process was left.

**Cascade.** `coverage.json` changed again: the DE03a observables drop the
scan and the removed test, and name the contract boundary and the re-check
before each unlink. That one string is the only change against the round-3
tree. The cascade ran last: baseline `ae883f3c…f0ac`, a1 `76d2df95…5e3b`, a2b
`30d55063…b51f`, b1b `50585529…e757` and b3 `e6332c7e…d96b`. Closure
verification exits 0 with no stale entry, the key-level diff against HEAD is
still the three bookkeeping hashes plus the derived parents, and the DE02b3
binding check exits 0 (no chain-bound source changed).

Logs: `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/repair-r4-logs/` (macOS: `macos-final-*.log`,
`mutant-matrix-final.log`, `c2-regression-before-fix.log`,
`tests-on-round3-sources.log`, cascade, closure and binding),
`/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/repair-r4-pycompat/` (Python 3.11 and 3.12) and
`/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/repair-r4-linux/remote-logs/` (Linux).

### DE03a round-5 Codex review repair: the boundary covers the ownership lifetime

Round 5 review, Codex `gpt-6.1-sol` HIGH on tree `4e2f27e5`: **ISSUES**, one
Critical in the documentation, accepted with a Root decision and repaired by
Claude Code `claude-opus-5-5` MAX. There is no product code change, only
docstrings, documents and tests; no CI result is claimed.

- **Critical: the storage document promised one owner per store across processes
  after renames outside an open, create or close, and that fails.** Reproduced
  by the reviewer: create and checkpoint a store, rename its database while the
  owner is idle, and open the renamed path in another process. The second owner
  succeeds and commits while the first connection returns stale rows; renaming
  only the idle owner's lock file also admits a second owner. The database-inode
  registry protects only the owning process, and a lock file beside a name
  cannot hold across a rename of the file or of the lock.
- **Root decision:** the boundary covers the whole ownership lifetime instead of
  being enforced.

**Contract boundary.** Now word for word, markup aside, in both module
docstrings, the harness spec, the storage document, the changelog and here; a
test checks that:

> **Contract boundary.** The store directory is private to the owning user
> (owned by that user, mode `0700`; missing directories are created that way,
> and keeping an existing one private is the caller's part). While any owner
> holds a store, from the moment an open or create of it starts until its
> close has completed, renaming, replacing, hard-linking or deleting any store
> file (the database, its `-wal`, `-shm` and `-journal`, and the lock file),
> by any actor, is outside the contract. pipy detects some of these cases on a
> best-effort basis (the identity checks under the lock, after connecting and
> after configuration; the parking of descriptors that reach a claimed
> database; the re-check before each unlink of a failed create) but guarantees
> nothing about them. SQLite's own guidance is the same: do not rename, unlink
> or replace an open database or its journal. With the store files left in
> place, the guarantees hold: one owner per store across processes and within
> the process, owner-only file modes, no descriptor of a store file held by
> another owner in this process is ever closed by pipy, and invalid input, an
> `open` of a missing path and a store refused before its lock is taken have
> no filesystem effect. Pi durable 1.3.0 has no cross-process locking at all,
> so this remains strictly stronger than the reference.

This supersedes the round-4 boundary and the rename-related guarantee wording of
the round-2 to round-4 sections, which now carry short round-5 notes. The plan's
DE03a cell (aliases meet one lock, a second process is refused) stays true with
the store files left in place.

**Tests.** 142 cases.

- Reclassified: `test_an_owned_database_under_another_name_is_refused_unread`
  (an owned database renamed while idle) moved into the best-effort section. It
  shows that the owning process's registry still refuses the renamed database
  before any descriptor opens it and that the owner's locks survive; across
  processes nothing holds, as the reviewer showed.
- New: `test_the_contract_boundary_reads_the_same_everywhere_it_is_stated` takes
  the boundary from `ownership`'s docstring and requires it word for word
  (markup, line breaks and indentation aside) in `sqlite_connection.py`, the
  harness spec, the storage document, this ledger and the changelog.
- No test was deleted this round, and no other test changed.
- What the kept guarantee tests prove, with no store file renamed, replaced,
  hard-linked or deleted while a store is held:
  - a second process is refused while the owner lives, and after SIGKILL the
    lock is free again, so ownership is exclusive across processes;
  - a second open in the owning process through every path alias (the same path,
    relative, `..`, `.`, a symbolic-link directory) and through a case or
    Unicode-normalization variant is refused before any descriptor of the store
    opens; `create` through the alias is refused; an external
    `locking_mode=EXCLUSIVE` writer stays blocked; and later writes keep every
    row;
  - concurrent in-process opens admit exactly one owner, and the refused ones
    never release its locks; the lock step's own registry check refuses before
    `flock`;
  - hard links and symbolic links made while no owner holds the store are
    refused even unowned;
  - and the mode, umask, owner-only normalization, fork, exec, SIGKILL recovery,
    header, journal, durability and residue tests are unchanged.

**Results after the repair.** These supersede the earlier DE03a tables.

| Check | macOS 27.0.1, Python 3.15.0, SQLite 3.53.1 | Linux macmini, Ubuntu 24.04, Python 3.12.3, SQLite 3.45.1 |
| --- | --- | --- |
| new tests under `-X dev -W error`, 20 sequential runs | 20 of 20 exit 0; 142 passed each | 20 of 20 exit 0; 142 passed each |
| the new and coverage tests under `-X dev -W error` | 0; 243 passed | 0; 243 passed |
| coverage and acceptance tests | 0; 153 passed | 0; 153 passed |
| `pytest tests/durable` | 0; 1496 passed | not run |
| extension-generation inventory and `test_architecture_*` | 0; 348 passed | not run |
| the 13 test files that read the changed documents | 0; 677 passed | not run |
| `ruff check .`, `ruff format --check .`, `mypy src tests` | 0, 0, 0 (898 files) | not run |
| strict docs build and rendered links | 0 ("No issues found"); 1041 local links on the six pages, none broken | not run |
| leftover processes naming the directory | not applicable | 0 |

The mutant matrix and the Python 3.11 and 3.12 runs were not repeated: no
product code changed, and the moved test kept its name, so every mutant's
designated tests are the same.

Linux ran in a new isolated directory,
`~/pipy-de03-linux/de03a-20261021T221825Z/`. After the run the previous
directory `~/pipy-de03-linux/de03a-20261021T214058Z/` was deleted; nothing else
was removed and no process was left.

**Cascade.** `coverage.json` changed again: the DE03a observables name the
lifetime boundary, move the renamed-while-idle case to best-effort detection and
add the consistency check. That one string is the only change against the
round-4 tree. The cascade ran last: baseline `5511ec99…1d57`, a1
`7a22652a…a2b4`, a2b `bbe41291…45a9`, b1b `051fd6b2…3348` and b3
`5b08e135…c2e5`. Closure verification exits 0 with no stale entry, the key-level
diff against HEAD is still the three bookkeeping hashes plus the derived
parents, and the DE02b3 binding check exits 0 (no chain-bound source changed).

Logs:
`/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/repair-r5-logs/`
(macOS) and
`/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03a/repair-r5-linux/remote-logs/`
(Linux).

## DE03a delivery and DE03b admission

DE03a landed at `0d37f64a0a80b45b359ac1a1b48beb25e6db6354` on origin/main (tree
`4b1a0bb5`, verified with `git ls-remote`). Root's local `just check` exited 0
with 14392 passed and the same two known skips; no CI result is claimed. Review,
Codex `gpt-6.1-sol` HIGH: round 1 **ISSUES**, three Warnings (a hot rollback
journal was recovered before the refusal; the directory flush ran outside the
create cleanup; `os.waitid` is missing on macOS before Python 3.13); round 2 one
Critical (the header precheck closed a database descriptor and dropped an
owner's POSIX locks); round 3 one Critical (a claim moved to another inode);
round 4 two Criticals and harness non-convergence, after which Root simplified
(the descriptor scan removed, an explicit contract boundary); round 5 one
Critical in the documentation (the boundary widened to the whole ownership
lifetime); round 6 **CLEAN**. The repairs are recorded in the DE03a sections
above. DE03a is LANDED; DE03 stays IN_PROGRESS until b, c and d land, and all43
P rows stay OPEN.

The coordinator issued the DE03b work order on 2026-10-22. Its binding design is
the DE03b row of the read-only DE03 SQLite prep with Root decisions D1–D13 (D6
open-time `quick_check` only after an unclean shutdown, `integrity_check` in
tests after every cut; D7 no table prefix; D11 schema v1 is Pi's version 2 shape
and names plus the Python deltas, and DE27 bumps the version with its first
delta), and these Root decisions for the slice:

1. The identity gate lives in the header read DE03a performs before any
   connection: `application_id` (bytes 68–71) and `user_version` (bytes 60–63),
   so foreign and newer stores are refused with bytes unchanged. This closes
   DE03a's open risk that a foreign WAL-mode file is opened and checkpointed at
   close.
2. DE03a's POSIX invariant is binding: new header reads go through the existing
   gate, never a new raw descriptor.
3. An interrupted create (0-byte or header-less database) gets a decided and
   documented recovery rule; refusal plus an explicit procedure is preferred
   over silent adoption, justified against Pi and the plan.
4. Migrations are contiguous and transactional (`BEGIN IMMEDIATE`); a newer
   version is refused before any write; a failing synthetic migration rolls back
   version, probe table and data and can be retried; a synthetic table rebuild
   keeps rows and indexes; SIGKILL mid-migration in a real child leaves the old
   version and the next open migrates; foreign, corrupt and missing-metadata
   refusals leave bytes unchanged.
5. Integrity: open-time `quick_check` only after an unclean shutdown, with what
   "unclean" means and what a failure does (refuse, bytes unchanged) defined.

## DE03b local schema evidence

Local implementation evidence, not a landed verdict or P closure. Implementer:
Claude Code `claude-opus-5-5` MAX, sole writer on main from clean HEAD
`0d37f64a`; no subagents. Upstream was read only through `git show` at the pin.
Nothing is committed or pushed and no CI result is claimed.

**Design.** One new private module, `native/durable/sqlite_schema.py`, runs
inside DE03a's `open_owned_database`; there is no second entry point, so
nothing opens a store without its identity and schema checks.

- *Schema v1* is Pi durable 1.3.0's SQLite schema version 2 (pinned
  `packages/durable/src/storage/sqlite/migrations.ts`, blob `a5ee244a…`, sha256
  `dd0f6445…dea9`) with Pi's names and no prefix, plus the Python deltas in the
  mapping below. `TABLES` and `INDEXES` keep Pi's identifier history, so a
  prefix could still be added.
- *Migrations*, a port of Pi chord's `applySqliteMigrations` (blob `d4dc7efe…`):
  `MIGRATIONS` is an immutable, append-only tuple of `Migration(version,
  statements)`; a list with non-contiguous versions or foreign types is refused
  before any SQL ("SQLite migrations must have contiguous versions starting at
  1"). One `BEGIN IMMEDIATE` transaction creates `durable_schema` when missing,
  reads its version, refuses a newer one ("SQLite schema version N is newer than
  supported version M"), and applies every pending migration with its version
  bump in `durable_schema` and `PRAGMA user_version`; version 1 also sets
  `PRAGMA application_id`. An error rolls everything back and propagates
  unchanged; a failed rollback raises `SqliteRollbackError` carrying both errors
  (chord's facade rule). The first transaction of `create` therefore commits
  the identity, the schema and the metadata row together.
- *Identity gate.* DE03a's pre-connect header read (`check_wal_header`, now
  `check_store_header`; no shim under the no-deprecation policy) reads the
  header once through ownership's descriptor rule and refuses, in this order, an
  interrupted create (an empty file, or at most one page of zeros), anything
  that is not an SQLite database in WAL mode (DE03a), a foreign application ID
  (`ForeignStoreError`), a `user_version` above the supported version
  (`NewerStoreError`, Pi's message) and the pipy application ID without a
  version (`StorageCorruption`). Such a refusal happens before the lock: no
  byte changes and no lock file appears. The gate also receives the `-wal` size
  by `lstat` (`ownership.ExistingCheck` gained that argument; no descriptor),
  because one shape cannot be decided from the database file alone: after the
  WAL switch the header shows one page, application ID 0, user version 0 and
  schema cookie 0, and a committed migration reaches the file only at a
  checkpoint (probe below). An identity-less one-page database with no `-wal`
  content is an interrupted create; with `-wal` content it is decided after WAL
  recovery.
- *Integrity* (`validate_store`, named so because a package-wide inventory test
  watches attribute calls named `validate`). On the connection it requires the
  identity again, the exact `sqlite_schema` rows `(type, name, tbl_name, sql)`
  of the store's version, computed by replaying the migrations on an in-memory
  database with the same SQLite (no extra table, index, view or trigger; every
  definition as created), a `durable_schema` version equal to `user_version`
  (a larger one is `NewerStoreError`, Pi's tamper) and the metadata singleton
  with `2 <= next_id <= 2**53` and `1 <= next_seq <= 2**53`; Pi's messages
  "SQLite schema metadata is missing" and "Durable SQLite metadata is missing"
  are kept. Damage SQLite reports while reading (`SQLITE_CORRUPT`,
  `SQLITE_NOTADB`) becomes `StorageCorruption`, a `RuntimeError`. (Round 1:
  at every step of opening, migrating and closing a store; see
  [the repair](#de03b-round-1-codex-review-repair).)
- *D6 policy.* "Unclean" means a non-empty `-wal` found by `lstat` under the
  lock before connecting (DE03a's `wal_size_at_open`). Then a read-only
  connection (`mode=ro`, `trusted_schema=OFF`, `temp_store=MEMORY`, read back,
  pinned for forks) lets SQLite recover the log into its `-shm` index, runs
  `validate_store` and `PRAGMA quick_check`, and is closed before the owner
  connects. A read-only connection never checkpoints, so every refusal there
  leaves the database and `-wal` bytes unchanged. That connection opens and
  closes descriptors of the owner's claimed inode while no other connection of
  this process exists, which keeps DE03a's POSIX invariant. After a clean
  shutdown no `quick_check` runs; the owner's connection validates, and a
  refusal there writes nothing (no frame, so the close checkpoint copies
  nothing).
- *Open order.* Admission and runtime check, ownership with the header gate,
  the read-only validation after an unclean shutdown, the owner's connection
  and its PRAGMAs (DE03a), then `prepare`: `create` migrates the empty database
  and validates; `open` validates, applies pending migrations only to a valid
  older store, and validates again. `OwnedDatabase.schema_version` reports the
  result.
- *Recovery rule.* An interrupted create is refused (`InterruptedCreateError`)
  and never adopted: nothing proves which process made the file, an empty
  foreign database looks the same, and plan §6 and D9 forbid accidental
  creation by `open`; Pi opens or creates and would adopt it. The documented
  procedure (storage document): while no process owns the store, delete the
  database and any `-wal`, `-shm` and `-journal` (the lock file may stay), then
  create again. No committed data can be lost, because a create that committed
  its schema is complete and `open` recovers it, also while its identity is
  still only in the `-wal`.

**Schema v1 mapping** (Pi schema version 2 to pipy schema version 1; the test
`test_schema_v1_is_pi_version_2_with_exactly_the_python_deltas` derives the v1
statements from Pi's pinned DDL plus exactly these deltas).

| Pi version 2 | pipy version 1 | Reason |
| --- | --- | --- |
| `durable_schema` (runner bootstrap) | same DDL | none |
| `durable_metadata.next_id TEXT`, seed `'2'` | `INTEGER`, seed `2` | Pi works around `node:sqlite` refusing unsafe integers |
| `record_ids` and all 16 indexes | identical names and definitions | none |
| `record TEXT NOT NULL CHECK (json_valid(record))` in `conversations`, `entries`, `tasks`, `submissions`, `documents` | `record BLOB NOT NULL` | D1: lossless UTF-8 JSON with lone surrogates; no JSON1 functions |
| `tasks` rebuilt by version 2 for `sleeping` | created directly in that shape | no version 1 history (D11) |
| `document_revisions.version INTEGER NOT NULL` | `version BLOB NOT NULL` | raw versions 0, −1, 1.5, −0.0 and integers beyond 64 bits survive, as in Memory (D2 (e)) |
| `document_revisions.content TEXT NOT NULL CHECK (json_valid(content))` | `content BLOB NOT NULL` | D1 |
| (none) | `submission_requests(conversation_id, request_id, submission_id, PRIMARY KEY (conversation_id, request_id))` | D2 (a): Memory's last writer of a request ID without fallback; DE03c fills it |
| (no header identity) | `application_id` `0x50495059`, `user_version` 1 | the pre-connect identity gate |

The identity columns (task `kind`, `request_id`, document `kind`,
`key_value`) stay `TEXT`; DE03c fills them with ASCII JSON (D1).

**Probes** (scratch, macOS 27.0.1, `.venv` Python 3.15.0 with SQLite 3.53.1 and
uv Python 3.11.13 with SQLite 3.49.1; logs `probe_*.log`):

- `PRAGMA journal_mode=WAL` on a new 0-byte file writes one 4096-byte page:
  versions 2/2, page count 1, schema cookie 0, schema format 0, application ID
  0, user version 0, and no `-wal` yet. In a directory that forbids new files
  the switch fails ("attempt to write a readonly database"): it writes through
  a transient rollback journal, so a crash there can leave a `-journal`, which
  DE03a's journal refusal catches.
- `PRAGMA application_id` and `user_version` inside a transaction roll back
  with it. After a committed migration the database file's header still shows
  application ID 0, user version 0 and schema cookie 0 until a checkpoint
  copies page 1; the identity is then only in the `-wal`.
- A transaction with nothing to write appends no frame; setting
  `application_id` to its current value does (4152 bytes), so only version 1
  sets it.
- A read-only (`mode=ro`) connection to a database whose writer crashed sees
  the committed `-wal` rows, ignores uncommitted spilled frames, sees an
  identity that is only in the `-wal`, refuses writes, and on close leaves the
  database and `-wal` byte-identical; only the `-shm` changes, or is created
  when it was missing. A read-write connection then sees the same state.
- An invalid b-tree page type on an index root makes `quick_check` report two
  findings and `integrity_check` raise "database disk image is malformed",
  while `sqlite_schema`, PRAGMAs and other tables still read; random bytes in a
  cell's payload are found by neither.
- With `cache_size=1` the create's own migration spills 119,512 bytes of
  uncommitted frames into the `-wal` by its last statement; with the default
  cache the `-wal` stays empty.
- The vendored Pi DDL in the mapping test equals the pinned `migrations.ts`
  (sha256 `dd0f6445…dea9`) rendered with Pi's default identifiers: 25 version
  1 statements, the version 2 tasks table and its five indexes
  (`vendored-ddl-check.log`, exit 0).

**Tests.** `tests/durable/test_sqlite_schema.py` (new): 27 functions, 55
cases, 8 of them with real children through the DE03a helper (own session and
process group, JSON-line handshake, per-event deadlines, killing and reaping
the group). Files are read or tampered with only while no store is open in the
process.

- Mapping and shape: the v1 statements equal Pi's pinned version 2 DDL with
  exactly the listed deltas; a created store carries the header identity, Pi's
  table and index names plus `submission_requests` and two autoindexes, no view
  or trigger, `durable_schema` (1, 1), metadata (1, 2, 1) and
  `integrity_check` ok; the `BLOB` columns keep `0`, `-1`, `1.5`, `-0.0` and
  `2**64` versions and lone surrogates, and STRICT refuses text in them.
- Runner (Pi chord's tests): non-contiguous lists (`[2]`, `[1, 3]`, `[1, 1]`,
  `[2, 1]`) and ill-typed ones (a list, a foreign element, version 0, a `bool`
  version, a list of statements, a `str` subclass statement) are refused before
  any SQL; the runner applies repeatably and writes no frame when nothing is
  pending; a bootstrap plus a failing second migration roll back together
  (no object, application ID 0, user version 0, no open transaction) and the
  fixed list then applies; a newer store raises Pi's message; a failed
  rollback raises `SqliteRollbackError` with both errors.
- Migrations through `open` (Pi's durable tests): a failing synthetic version 2
  (probe table, probe row, a metadata update, a deletion, then invalid SQL)
  leaves every store file but the lock's diagnostics byte-identical, twice, and
  the fixed version 2
  then migrates with the rows and metadata kept; a failing first migration
  leaves only the lock file and `create` works again; a synthetic `tasks`
  rebuild (Pi's version 1 to 2 pattern with a new status) keeps all 40 rows and
  the five indexes, leaves no `tasks_v2`, accepts the new status, still refuses
  an unknown one and plans `tasks_by_status`; opening a current store twice
  writes nothing.
- Identity gate: a plain WAL database, a GeoPackage application ID, user
  version 5, a dropped-table database and the pipy application ID without a
  version are refused with no `sqlite3.connect` call, the same bytes and modes
  and no lock file, twice; a crashed foreign writer's database, `-wal` and
  `-shm` stay byte-identical; a newer store by header, by `user_version` alone
  (both before any connection) and by `durable_schema` alone (Pi's tamper,
  after connecting, writing nothing) is refused with Pi's message, and the
  newer code then opens it.
- Integrity: an extra table, index, view or trigger, a dropped or changed
  index, a missing `durable_schema` or metadata row (Pi's messages), a version
  mismatch and counters just outside the range are refused with the bytes
  unchanged and the lock free, twice; `2**53` counters are accepted; damage
  SQLite reports while validating becomes `StorageCorruption`.
- D6, both sides: a corrupted index root after an unclean shutdown (a real
  child killed while owning the store) is refused by `quick_check` on the
  read-only connection with the database and `-wal` unchanged; after a clean
  shutdown the same store opens and `integrity_check` finds the damage; an
  unclean shutdown with valid content connects read-only, then read-write, and
  opens with every row, `integrity_check` ok and `0600` sidecars.
- Interrupted create and recovery: an empty, a 512-byte and a 4096-byte
  zero-filled file are refused before the lock and `create` refuses them;
  after the documented procedure `create` works. A real child killed before
  its schema commit, with and without spilled frames, leaves a store that
  `open` refuses (with spilled frames only after a read-only connection) with
  the database and `-wal` unchanged and that the procedure recovers; one killed
  right after the commit leaves a store whose identity is only in the `-wal`,
  which opens as a complete version 1 store and carries the identity in the
  file after its close.
- Migration cuts: SIGKILL in a real child inside a version 2 table rebuild of
  1500 rows with spilled frames leaves version 1 with every row and
  `integrity_check` ok, then version 2 migrates them; a child killed right
  after a version 2 commit leaves version 2 only in the `-wal`, which version 1
  code refuses with the database and `-wal` unchanged and version 2 code opens.

**DE03a tests adapted** (`tests/durable/test_sqlite_ownership.py`, 142 cases,
and `sqlite_child_support.py`). Every store now has schema v1, so DE03a's
scratch table `t` would fail the exact-schema check: the tests keep their
scratch rows as raw bytes in the v1 `conversations` table through `put` and
`stored` (also in the child prelude), which nothing reads as records; the
residue probe deletes from `conversations`. An empty file is now refused as
an interrupted create (`InterruptedCreateError`) instead of "not an SQLite
database"; the "header check missed" control patches `check_store_header`. No
assertion was weakened, and DE03a's 46-mutant matrix, re-anchored, still fails
every designated test (below).

**Private negative controls** (scratch matrix `mutants.py`, not in the
permanent suite). Each of 30 source mutants was applied in place, both SQLite
test files ran against it, and the original bytes were restored and checked by
sha256 after every mutant. Every mutant made its designated tests fail
(failing cases in brackets): the identity gate disabled (9), application ID 0
accepted (11), the header newer check (2), the `durable_schema` newer check
(1), the newer check after WAL recovery (1), the runner's newer check (1), the
rollback skipped (3), a commit per migration (1), no `user_version` bump (110),
no application ID (109), the contiguity check (4), exact migration types (1),
no `quick_check` (1), `quick_check` on every open (18), the validation
connection read-write (5), unclean shutdowns not detected (5), the unwritten
file check (4), always deferring the identity-less page (1), adopting an
interrupted create after recovery (1), never deferring (6), the `-wal` size not
passed by ownership (6), extra objects tolerated (4), names compared without
SQL (1), the missing metadata check (1), each counter bound (1, 1, 2, 1), the
version mismatch check (1) and the damage mapping (1).

**Results.**

| Check | macOS 27.0.1 arm64, Python 3.15.0, SQLite 3.53.1 | Linux macmini, Ubuntu 24.04, Python 3.12.3, SQLite 3.45.1 |
| --- | --- | --- |
| `test_sqlite_schema.py` and `test_sqlite_ownership.py` under `-X dev -W error`, 20 sequential runs | 20 of 20 exit 0; 197 passed each | 20 of 20 exit 0; 197 passed each |
| the same plus the coverage tests under `-X dev -W error` | 0; 298 passed | 0; 298 passed |
| coverage and acceptance tests, after the cascade | 0; 153 passed | 0; 153 passed |
| `pytest tests/durable` | 0; 1551 passed | not run |
| extension-generation inventory and `test_architecture_*` | 0; 348 passed | not run |
| the 13 test files that read the changed documents | 0; 677 passed | not run |
| `ruff check .`, `ruff format --check .`, `mypy src tests` | 0, 0, 0 (900 files) | not run |
| strict docs build and rendered links on the five changed pages, after the final ledger edit | 0 ("No issues found"); 908 local links, none broken | not run |
| DE03b mutant matrix | 0; 30 mutants, no designated failure missing | not run |
| DE03a mutant matrix, re-anchored, on the adapted DE03a tests | 0; 46 mutants, every designated test fails (one under its renamed test ID, confirmed by a rerun), the directory-flush mutant survives by design | not run |
| a store made by the other host's SQLite | the 3.45.1 store opens and validates | the 3.53.1 store opens and validates |
| leftover processes naming the directory | not applicable | 0 |

The new and adapted tests also ran on the locally installed uv Python 3.11.13
(SQLite 3.49.1) and 3.12.15 (SQLite 3.53.1) under `-X dev -W error`: 197 passed
each, in throwaway venvs in scratch built from a snapshot of the working tree
and installed offline from the existing scratch uv cache; the project `.venv`
and `uv.lock` were untouched. Stores made by SQLite 3.49.1 and 3.53.1 open on
the other one. The first full inventory run failed one package-wide test on the
attribute call `sqlite_schema.validate` (the rename above); the run above is
after the rename.

Linux ran in a new isolated directory,
`~/pipy-de03-linux/de03b-20261022T000549Z/` (mode `0700`, on ext4), with
DE03a's procedure: a copy of the working tree (`git ls-files -co
--exclude-standard`, 3931 files, sent with `rsync` over SSH using the launchd
agent socket for those commands only; eight key files compared by sha256), a
project-local venv on the system Python (`UV_PYTHON_DOWNLOADS=never uv venv
--python /usr/bin/python3.12`, then `uv sync --frozen`), and the uv cache,
`TMPDIR` and pytest temporary directories inside it. Each Linux repeat took
about 82 seconds against about 21 on macOS. After the run the previous
directory `~/pipy-de03-linux/de03a-20261021T221825Z/` was deleted; nothing else
was removed, exactly one DE03 directory remains, and no process of the user
names it outside the checking session.

**Reruns on the final tree.** Two documentation-only edits followed the runs
above (the `sqlite_schema` docstring on the header lagging the `-wal`, and a
comment in `ownership._gate_existing`; no code change), so everything ran again
on the final tree, with the same results: the macOS battery (`final2-*`), whose
docs-dependent run failed twice in `test_docs_build.py`'s preview-server timing
test, which builds its own temporary project and waits at most 20 seconds,
while the load average was about 6 (DE03a saw the same under load), and passed
677 of 677 when rerun at once. The closing run after the last ledger edit hit
it once more and passed on rerun, and a clean `git archive` of HEAD, without any
DE03b change, fails the same test under the same load (two of two whole-file
runs; `docs-build-test-compare.log`), so it is environmental. Python 3.11 and
3.12 passed 197 each; and on
Linux the same directory, re-synced with ten key files compared by sha256,
which ran the coverage and acceptance tests (153), the new and coverage tests
under `-X dev -W error` (298), the 20 repeats (197 each) and the cross-version
check again, with no leftover process. The ledger's own last prose edits came
after these runs; they change no line a test reads, and the document-reading
checks ran again after them.

Logs: `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03b/logs/` (macOS: `final-*.log`, `mutant-matrix.log`,
`mutant-matrix-de03a*.log`, `cascade.log`, `closure-verify.log`,
`binding-check.log` and the superseded `binding-check-head-relative.log`,
`docs-build.log`, `rendered-links.log`, `vendored-ddl-check.log`,
`xver-local.log`, and the final-tree reruns `final2-*.log`), `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03b/probes/` (probes), `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03b/pycompat/` (Python
3.11 and 3.12; the first run in `run1/`) and `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03b/linux/remote-logs/` and `remote-logs-final/` (Linux, copied back, with the
cross-version logs in `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03b/linux/`).

**Manifest cascade and chain binding.** Only two files the baseline manifest
hashes changed: the coverage index (DE03a LANDED with its delivery, DE03b
IN_PROGRESS with its observables, the DE03 parent text) and the coverage test
(DE03a LANDED, DE03b IN_PROGRESS, and the parent-cannot-land test also flips a
LANDED child). The acceptance script did not change, because the DE03 rows
keep their split anchor. No file of the a1, a2b, b1b or b3 closures changed.
The cascade (baseline → a1 → a2b → b1b → b3) ran last over closure and parent
hashes only: baseline `5ae95450…0733`, a1 `bfb819f3…619e`, a2b
`84153685…f874`, b1b `48a17af0…89b9e5` and b3 `54e7ec1c…9ab9`. Closure
verification exits 0: b3's `configuration()`, which validates b1b, a2b, a1 and
the baseline, passes; no closure entry is stale; and the key-level diff against
HEAD is exactly those two baseline entries plus the parent hashes derived from
them. The DE03a-era binding check compared HEAD's manifests with DE02b3's
binding, which stopped holding when DE03a landed its own bookkeeping cascade
(its log is kept, exit 1). The binding is therefore now checked against the
commit it was made for, `d2e62390`: `final-pairs.json` (sha256 `715c3246…4613`)
names exactly that commit's five manifests, both chains' acceptance and child
results are intact and PASS, of the 63 files any of those manifests hashes
only the three bookkeeping files differ in the working tree (the acceptance
script changed in DE03a), and the manifest diff against `d2e62390` is those
entries plus derived parent hashes (exit 0). No chain-bound file changed, so no
chain reran.

**Deviations from the prep and the work order, with reasons.**

1. `ownership.ExistingCheck` gained the `-wal` size (by `lstat`, no
   descriptor) so the gate can tell an interrupted create from a create that
   crashed after its commit; the probe shows the file header lags the `-wal`.
2. One shape is decided after WAL recovery instead of by the header alone: an
   identity-less one-page database with a non-empty `-wal`. An empty foreign
   database in that state is opened read-only (only its `-shm` index is
   rebuilt) and then refused.
3. `quick_check` and the validation after an unclean shutdown run on a separate
   read-only connection, because only that keeps the database and `-wal` bytes
   unchanged on every supported Python: 3.11 has no `Connection.setconfig` for
   `SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE`, and a read-write connection checkpoints
   when it closes.
4. `document_revisions.version` is a `BLOB` of the version's JSON number. The
   prep named a "lossless version" delta without a type; `ANY` cannot bind
   integers beyond 64 bits, which the finite-JSON codec allows.
5. `submission_requests` is created in v1, as the prep's "request-winner table
   if D2" (D2 accepted), although DE03b does not use it; DE03c fills it, or
   adds a version if its paired probe needs another shape.
6. The DE03a test file was adapted (scratch rows in `conversations`, the empty
   file reclassified, the renamed gate patched); `check_wal_header` became the
   format part of `check_store_header` without a shim.
7. The new errors (`ForeignStoreError`, `NewerStoreError`,
   `InterruptedCreateError` under `StoreIdentityError`; `StorageCorruption`;
   `SqliteRollbackError`) are `RuntimeError`s defined in `sqlite_schema`, not
   `StorageConfigurationError`s, so `sqlite_schema` imports nothing from the
   connection module; DE03c reuses `StorageCorruption` and
   `SqliteRollbackError`.
8. `validate_store`, not `validate`: a package-wide inventory test watches
   attribute calls named `validate` (the first full inventory run failed on
   it; the rename has no behaviour change).
9. `OwnedDatabase` reports `schema_version`.

**Prep versus reality, flagged.** (a) The database file's header lags the
`-wal`: identity and `user_version` reach the file only at a checkpoint, so a
header-only gate would refuse a create that crashed after committing and would
miss a newer version committed only in the `-wal`; handled by the deferral and
the post-recovery validation above. (b) Keeping bytes unchanged after
recovery needs a read-only connection on Python 3.11. (c) The WAL switch of a
new file goes through a transient rollback journal. Every other prep
assumption held.

**Open risks for DE03c and later.** (1) `submission_requests` is designed
ahead of its user; DE03c must keep it consistent in every submission write, or
add a version. (2) The exact-schema check compares SQLite's stored SQL text,
which held across SQLite 3.45.1, 3.49.1 and 3.53.1 (cross-version checks above); a
future SQLite that stored it differently would refuse older stores, closed but
needing a migration of the check. (3) After a clean shutdown corruption is
found only when records are read: DE03c must turn decode and shape failures
into `StorageCorruption` and run its statements on the raw connection inside
`damage_as_corruption` (round 1), and a damaged index can still answer queries
wrongly until an operator runs `integrity_check`. (4) `quick_check` after an
unclean shutdown costs time proportional to the store; DE03d's P43 budget
should measure it. (5) `MIGRATIONS` is a module global that tests replace;
DE03c and DE27 must keep it append-only, and the mapping test pins version 1.
(6) DE10's read-only inspection must call `validate_store` without
`apply_migrations` and handle an unclean store without writing. (7) DE03c's
commit inherits the runner's transaction pattern; the D4 COMMIT poisoning and
`SqliteRollbackError` semantics are its decisions.

### DE03b round-1 Codex review repair

Round 1 review, Codex `gpt-6.1-sol` HIGH on tree `df197827`: **ISSUES**, no
Critical and one Warning, accepted by the coordinator and repaired by Claude Code
`claude-opus-5-5` MAX; no CI result is claimed.

- **Warning: damage SQLite reported while configuring escaped as a raw
  `sqlite3.DatabaseError`.** Only `validate_store` and `quick_check` converted
  SQLite's corruption errors, but the owner's PRAGMA configuration loads the
  schema first. Reproduced before the repair: a closed store with byte 100 (the
  b-tree type of page 1, the root of `sqlite_schema`) set to `0x01` made `open`
  raise `sqlite3.DatabaseError: database disk image is malformed`
  (`SQLITE_CORRUPT`) from `_configure`; the bytes stayed unchanged. On the
  unclean path the same damage already surfaced as `StorageCorruption`, but only
  because the read-only validation connection first loaded the schema inside
  `validate_store`.

**Repair.** One context manager, `sqlite_schema.damage_as_corruption(label)`,
converts exactly SQLite's corruption errors, `SQLITE_CORRUPT` with its extended
codes and `SQLITE_NOTADB` (the primary code of `sqlite_errorcode`, which Python
3.11 and later set on errors that come from SQLite), into `StorageCorruption`
with the SQLite error as its cause. Any other error passes unchanged, and so
does an error the `sqlite3` module raises itself without a code; nothing else is
reclassified as damage. It now wraps every step of opening a store after
ownership (the read-only validation after an unclean shutdown, the owner's
connection, its configuration and read-back, the identity checks and
`prepare`), so the cleanup (closing the connection, removing what `create`
made, releasing the lock) sees the converted error; `apply_migrations`, outside
its rollback; `validate_store` and `quick_check`, replacing their own handlers;
and `OwnedDatabase.close()`, whose stored error, raised again by a repeated
close, is the converted one. `apply_migrations` takes the store's `label` for
the message.

**Audit of the entry points.**

| Entry point | SQLite work | Before | Now |
| --- | --- | --- | --- |
| `open_owned_database`, clean shutdown | owner's connection, PRAGMA configuration and read-back, `prepare` (`validate_store`, `apply_migrations`) | only `validate_store` converted; configuration and a migration's reads raised raw errors | all of it in one guard; the cleanup sees `StorageCorruption` |
| `open_owned_database`, unclean shutdown | read-only connection, its settings and read-back, `validate_store`, `quick_check` | the settings and read-back were outside every guard | inside the same guard |
| `apply_migrations` (tests now, DE10 later) | `BEGIN IMMEDIATE`, bootstrap, statements, `COMMIT` | raw after the rollback | guarded, after the rollback |
| `validate_store`, `quick_check` | reads | their own handlers | the shared guard |
| `OwnedDatabase.close()` | rollback of an abandoned transaction, TRUNCATE checkpoint, connection close | raw, stored and raised again | guarded; the stored error is the converted one |
| `OwnedDatabase.connection()` | none (returns the raw connection) | statements on it raise SQLite's errors | unchanged: DE03c wraps its statements in `damage_as_corruption` |
| `check_store_header` and the identity checks | header bytes through `os.pread`, no SQLite call | no SQLite error possible; a malformed header is `StorageConfigurationError` or an identity error by design | unchanged |
| in-memory schema replay | `:memory:` only | an error there is a broken migration, not damage | unchanged |
| `ownership`, `check_sqlite_runtime` | no SQLite I/O | none | unchanged |
| cleanup closes after a failure | connection close | a close failure becomes a note on the first error | unchanged |

**Regressions** (12 new cases; `tests/durable/test_sqlite_schema.py` now has 32
functions and 67 cases, 11 of them with real children):

- A damaged schema page on both open paths: page 1 (byte 100) or the last leaf
  of `sqlite_schema` (page 1's right-most pointer; the v1 schema spans pages,
  so page 1 is an interior page). `open` raises `StorageCorruption` twice,
  whose cause is a `sqlite3.DatabaseError` with `SQLITE_CORRUPT` or
  `SQLITE_NOTADB`. After a clean shutdown only the owner's read-write connection
  opened, no byte changed and no `-wal` or `-shm` remained. After an unclean
  shutdown (a real child killed while owning the store, its `-wal` holding a
  `record_ids` frame) only the read-only validation connected, the database and
  `-wal` stayed byte-identical and only the `-shm` index was rebuilt. The lock
  is free afterwards.
- Damage a migration reads (the `tasks` root corrupted, which validation never
  reads; a version 2 rebuild copies the table): `StorageCorruption` after the
  rollback, no byte changed, the header still at version 1, the lock free. The
  same through `apply_migrations` on a plain connection, which is not left in
  a transaction.
- A real non-corruption error: a foreign process holding the store in
  `locking_mode=EXCLUSIVE` (busy timeout 0.2 seconds) makes `open` raise
  `sqlite3.OperationalError` with `SQLITE_BUSY`, unchanged, with no byte changed
  and the lock free; the store opens once the holder is gone.
- Close: a checkpoint failure injected at its seam with `SQLITE_CORRUPT`, the
  extended code 267 or `SQLITE_NOTADB` is raised as `StorageCorruption` with
  that cause, and a repeated close raises the same object; `SQLITE_IOERR` and
  `SQLITE_BUSY` pass unchanged; the lock is released either way.
- Against the reviewed sources (`df197827`, swapped in and restored by sha256)
  7 of the 12 fail: both clean-path schema cases, the migration and runner
  cases and the three close damage codes. The 5 that pass are the three
  non-corruption controls and the unclean path, where the read-only
  connection happened to reach `validate_store`'s own conversion first.

**Negative controls.** The DE03b matrix has 34 mutants now (three re-anchored
to the new indentation). Removing the open-level guard fails the two clean-path
schema cases (`validate_store` and `apply_migrations` keep their own guards);
removing the runner's guard fails the direct runner case; removing the close
guard fails the three close damage codes; a guard that converts every
`sqlite3.DatabaseError` fails six cases (the real `SQLITE_BUSY`, the close
`SQLITE_IOERR` and `SQLITE_BUSY`, and the three existing cases that expect a
failing migration's raw syntax error). The damage-mapping mutant now fails 10
cases. Every mutant fails its designated tests and the unmodified sources pass
all 209 cases of both files. DE03a's matrix, with its two identity-check anchors
moved into the guard, fails every designated test again (45 of 45; the
directory-flush mutant survives by design).

**Results after the repair.** These supersede the earlier DE03b tables.

| Check | macOS 27.0.1, Python 3.15.0, SQLite 3.53.1 | Linux macmini, Ubuntu 24.04, Python 3.12.3, SQLite 3.45.1 |
| --- | --- | --- |
| `test_sqlite_schema.py` and `test_sqlite_ownership.py` under `-X dev -W error`, 20 sequential runs | 20 of 20 exit 0; 209 passed each | 20 of 20 exit 0; 209 passed each |
| the same plus the coverage tests under `-X dev -W error` | 0; 310 passed | 0; 310 passed |
| coverage and acceptance tests, after the cascade | 0; 153 passed | 0; 153 passed |
| `pytest tests/durable` | 0; 1563 passed | not run |
| extension-generation inventory and `test_architecture_*` | 0; 348 passed | not run |
| the 13 test files that read the changed documents | 0; 677 passed | not run |
| `ruff check .`, `ruff format --check .`, `mypy src tests` | 0, 0, 0 (900 files) | not run |
| strict docs build and rendered links on the five changed pages, after the final ledger edit | 0 ("No issues found"); 911 local links, none broken | not run |
| DE03b mutant matrix | 0; 34 mutants, no designated failure missing | not run |
| DE03a mutant matrix, re-anchored | 0; 46 mutants, every designated test fails, the directory-flush mutant survives by design | not run |
| the new regressions on the reviewed sources (`df197827`) | exit 1; 7 of 12 fail | not run |
| a store made by the other host's SQLite | the 3.45.1 store opens and validates | the 3.53.1 store opens and validates |
| leftover processes naming the directory | not applicable | 0 |

The new and adapted tests also ran on the locally installed uv Python 3.11.13
(SQLite 3.49.1, so `sqlite_errorcode` is checked on the oldest supported Python)
and 3.12.15 (SQLite 3.53.1) under `-X dev -W error`: 209 passed each, in
throwaway venvs in scratch built from a snapshot of the working tree and
installed offline from the existing scratch uv cache.

Linux ran in a new isolated directory,
`~/pipy-de03-linux/de03b-20261022T015019Z/` (mode `0700`, on ext4), with the
same procedure as before: a fresh copy of the working tree (3931 files; ten key
files compared by sha256), a project-local venv on the system Python with
`uv sync --frozen`, and the uv cache, `TMPDIR` and pytest temporary
directories inside it. After the run the previous directory
`~/pipy-de03-linux/de03b-20261022T000549Z/` was deleted; nothing else was
removed, exactly one DE03 directory remains, and no process of the user names
it outside the checking session.

**Cascade.** `coverage.json` changed again (the DE03b observables name the
repair and its regressions; nothing else), so the cascade ran last: baseline
`15aac65c…4141`, a1 `2a40f652…9f5f`, a2b `57a27cc2…097d`, b1b
`b8b1bcc1…68ea` and b3 `e11b9c7c…8068`. Closure verification exits 0 with no
stale entry, the key-level diff against HEAD is still the two bookkeeping
entries plus the derived parent hashes, and the binding check against
`d2e62390` exits 0 (only the three bookkeeping files of the 63 chain-hashed
files differ).

Logs: `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03b/repair-r1-logs/` (the reproduction before and after the fix, the
`SQLITE_BUSY` probe, `macos-*.log`, both matrices, the regressions on the
reviewed sources, coverage update, cascade, closure, binding and the closing
checks), `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03b/repair-r1-probes/`, `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03b/repair-r1-pycompat/` (Python 3.11 and
3.12) and `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03b/repair-r1-linux/remote-logs/` (Linux, copied back, with the
cross-version logs in `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03b/repair-r1-linux/`).

## DE03b delivery and DE03c split

DE03b landed at `f6024e562d083ec456a8fca05901faa56d39b485` on origin/main (tree
`d42e4b1b`, verified by Root with `git ls-remote`). Root's local `just check`
exited 0 with 14459 passed and the same two known skips; no CI result is claimed.
Review, Codex `gpt-6.1-sol` HIGH: round 1 **ISSUES**, no Critical and one Warning
(SQLite corruption raised while the owner's connection was configured escaped as
a raw `sqlite3.DatabaseError`), repaired by Claude Code `claude-opus-5-5` MAX
with the shared `damage_as_corruption` guard over opening, migrating, validating,
`quick_check` and closing (see [the repair](#de03b-round-1-codex-review-repair));
round 2 **CLEAN**. DE03b is LANDED; DE03 stays IN_PROGRESS until c and d land,
and all43 P rows stay OPEN.

The coordinator issued the DE03c1 work order on 2026-10-22. Its binding design is
the DE03 SQLite prep (§3.3 commit protocol: Pi's order, `BEGIN IMMEDIATE`,
`ROLLBACK` only while a transaction is open, `SqliteRollbackError` chaining and
D4; the codec and identity rules of D1; the §4 contract-case plan), the landed
DE03a contract boundary and POSIX invariant, and DE03b's schema v1 with its rule
that every statement on the raw connection runs inside `damage_as_corruption`.
Where Pi's Memory and SQLite backends disagree, Python SQLite follows Memory
(D2).

Root split the prep's DE03c row (1.5–2.2k lines, "may split c1 tables / c2
documents and cuts before code") before code, keeping every DE03c cell
cumulative. This record, the plan §5 rows and the coverage index were updated
before any DE03c1 code:

- **DE03c1 / DE03b** (in progress): `SqliteStorage` for the table operations
  (commit of table writes, `mint_id`, conversation, entry in both forms, the
  latest head marker, task and submission reads and scans, and
  `submission_by_request` through the `submission_requests` last-writer table of
  D2 (a)); the document methods and document writes refuse with the same
  `UnsupportedStorageOperation` class Memory uses for unsupported operations, as
  an explicit interim owned by DE03c2; the backend-agnostic contract-case module
  run over Memory, SQLite and SQLite reopened after every commit; lossless values
  across reopen; keyset cursors; EXPLAIN index use for table scans; contended
  threads linearizing under one authority lock; snapshot reads;
  `damage_as_corruption` around every statement; `integrity_check` after each
  test's writes. The table differences D2 (a) duplicate request-ID winner, (c)
  descending scans at 2^53−1, (d) negative limits and (f) dangling-parent errors
  follow Memory and are each pinned by a contract case; their paired probe
  against real Pi SQLite stays an open obligation (the work order assigns it to
  DE03d, and lists D2 (b)–(f) paired probes under DE03c2).
- **DE03c2 / DE03c1** (OPEN): documents (create, change and retire, current
  reads and scans, Pi's retention rows), D2 (b)–(f) proven by actual paired
  probes, real write refusals (`query_only`, `max_page_count`, a foreign write
  lock), acknowledgement loss (an after-COMMIT seam; a raising COMMIT poisons per
  D4), SIGKILL before and after COMMIT, and WAL truncate, page reuse and bounded
  size.
- **DE03d / DE03c**: unchanged (the paired oracle against Pi SQLite, Session over
  SQLite, the P43 workload and the final DE03 gate); it waits the cumulative
  DE03c, which lands only with c1 and c2.

The original DE03c cells map to the children as follows: contract cases over
Memory, SQLite and SQLite across reopen, c1 (tables) and c2 (documents);
lossless values across reopen, c1 (table records) and c2 (document content);
retention rows, c2; EXPLAIN index use, c1 (table scans) and c2 (document
queries); WAL truncate, page reuse and bounded size, c2; real write refusals, c2;
acknowledgement loss, c2 (c1 implements the commit protocol with D4); SIGKILL
before and after COMMIT, c2; contended threads, c1; snapshot reads, c1 (tables)
and c2 (documents); `integrity_check` after every cut, c1 and c2;
codec/identity, Memory-versus-Pi-SQLite and durability-class docs, c1 for
tables and c2 for documents and durability. DE03c waits c1 and c2. All43 P rows
stay OPEN.

## DE03c1 local table evidence

Local implementation evidence, not a landed verdict or P closure. Implementer:
Claude Code `claude-opus-5-5` MAX, sole writer on main from clean HEAD
`f6024e56`; no subagents. Upstream was read only through `git show` at the pin.
Nothing is committed or pushed and no CI result is claimed.

**Design.** One new private module, `native/durable/sqlite.py`:
`open_sqlite_storage(path, mode=..., durability=...)` opens a store through
`open_owned_database` and wraps it in `SqliteStorage`; a failure in the wrapper
closes the database again (a close failure becomes a note on the first error).
`SqliteStorage` has MemoryStorage's sixteen signatures (a test compares them)
and MemoryStorage's semantics for the four tables.

- *Ingress* is MemoryStorage's and comes first: the closed check, then its own
  helpers `_query` (exact query class, exact `int` page size) and `_start`
  (exact `Cursor`, order conflict), `safe_integer` for IDs, and its two inline
  checks repeated word for word (an exact tuple of exact `StorageWrite`s, an
  exact `str` request ID). Pages are built by Memory's `_page`, so raw limits
  keep Memory's page shapes and errors. Memory's capability refusal for delta and
  copy writes comes next with its message; any other document write, and the
  three document methods (after the closed check), refuse with
  `UnsupportedStorageOperation("SqliteStorage documents require DE03c2")`: the
  explicit interim of the split.
- *Authority.* One non-reentrant `threading.Lock` per instance around every
  method including `close`; nothing caller-supplied runs under it. Reads that
  need several statements (entry ancestry, the head marker, entry scans) run in
  one deferred read transaction, so they see one committed state even when a
  foreign process commits in between.
- *Commit*, Pi's order: closed check, ingress, capability bound, `BEGIN
  IMMEDIATE`, the metadata row (`sqlite_schema.read_metadata`, so the next
  sequence and the persisted ID counter are authoritative in the database),
  sequence exhaustion with Memory's message, the global ID claims against
  `record_ids` with Memory's messages, the table writes in batch order, the
  metadata update, a private test seam `_before_commit`, `COMMIT`; the in-memory
  ID counter is adopted only after `COMMIT` returned. An error before `COMMIT`
  rolls back only while a transaction is still open (`sqlite_schema.roll_back`,
  shared with the migration runner) and leaves the storage usable. An error
  raised by `COMMIT` poisons the instance (D4): every later call raises
  `StorageClosedError` with that error as its cause until close and reopen. A
  failed rollback raises `SqliteRollbackError` and poisons the instance too
  (see deviations).
- *Request winner* (D2 (a)): a submission write drops the `submission_requests`
  binding of its previous request ID only when it holds it, then binds its new
  one, last writer wins; `submission_by_request` reads the binding, with no
  fallback.
- *Fork chains*: `_segments` is Memory's walk over the conversations table,
  reading each parent's record only when the walk is resumed, so a dangling
  parent raises `KeyError(parent)` exactly where Memory's walk reads it (D2
  (f)). Entry scans collect what Memory collects: Memory appends before it
  checks the limit, so even a negative limit reads one entry (and walks past an
  empty segment); table scans check before they append, so a negative limit
  reads nothing. Descending scans start at the top without an ID bound and
  include 2^53−1 (D2 (c)). Page sizes beyond SQLite's integers are clamped to
  2^53 rows.
- *Values* (D1): each record is compact JSON text in a surrogatepass UTF-8
  `BLOB`, the exact text `json.dumps(value, ensure_ascii=False, allow_nan=False,
  separators=(",", ":"))` writes, but written and read by an iterative encoder
  and a token-at-a-time reader (see prep versus reality). The reader refuses
  malformed text, NaN and infinities, and repeated keys; the record constructor
  then validates the tree. Task kinds and request IDs are ASCII JSON in their
  index columns (UTF-16 identity).
- *Read-time integrity*: every row is decoded and validated, and its index
  columns are recomputed from the record and compared; a request binding must
  name an existing submission with that request; an entry needs a commit
  sequence in range. Every failure is `StorageCorruption`, never a request
  error, and every statement runs inside `damage_as_corruption`.
- `sqlite_schema` refactor: `_check_metadata` became `read_metadata`
  (returns the counters) and `_roll_back` became `roll_back`, both reused; the
  two DE03b test references were renamed. No behaviour change.

**Contract cases** (`tests/durable/storage_contract_cases.py`, collected by
`tests/durable/test_sqlite_storage.py`). The cases are MemoryStorage's own acceptance tests, `test_memory_storage.py`,
which stays unchanged (it is hashed in the a2b, b1b and b3 closures) and still
runs on its own. Every one of its 33 test functions is accounted for, and a
test parses the file to keep that account closed:

- *23 original bodies run unchanged over every backend* through
  `test_memory_storage_body`: the backend's store factory replaces the module's
  `MemoryStorage` name, through which each body builds its stores (a check
  after each body proves the backend made them). 12 are table bodies, one is
  the closed-document-ingress body (a closed store refuses first), and 9 are
  document bodies, marked
  `xfail(strict=True, raises=UnsupportedStorageOperation)` on the two SQLite
  backends, so each must fail with exactly the interim refusal (and an
  unexpected pass fails the run) until DE03c2 removes the mark.
- *4 table variants* make the observations the originals make on Memory
  internals (`_state` identity, a monkeypatched `_indexes`, a replaced `_lock`)
  through backend hooks that mean the same on every backend: `state` (the
  complete stored state: Memory's `_State`, or every row of the nine tables),
  `at_commit` (a callback at the last point of a commit: Memory's final
  candidate validation, or `_before_commit` inside the SQLite transaction),
  `set_next_seq`, `locked` and `replace_authority`. The contention variant runs
  on Memory and SQLite only: the reopening wrapper replaces its instance between
  calls, so threads would race the wrapper instead of one store's authority.
- *1 mixed body* also has its table and close halves split out as two cases on
  every backend, because its document half stops the SQLite runs early.
- *7 document bodies that observe Memory internals* (`DE03C2_PORTS`) need SQLite
  document views that only DE03c2 can define; they are listed explicitly, a
  test checks the list, and DE03c2 ports them. Until then they run on Memory in
  the original file only.
- *Added cases* (not ports; the Memory runs show Memory's behaviour): keyset
  scans reach 2^53−1 in both orders and continue from real cursors (D2 (c));
  page sizes of 2^64 read every row; an entry scan with a negative limit walks
  past an empty segment into a dangling parent and raises `KeyError`, while a
  dangling parent's own entries are still read before its record is needed (D2
  (d), (f)); IDs and every other table argument are admitted as exact built-ins
  before any hook runs, without holding the authority afterwards, and a closed
  store refuses before any admission.

The backends: `memory` (`MemoryStorage`), `sqlite` (`open_sqlite_storage(...,
mode="create")`, durability `full`) and `reopen` (Pi's `ReopeningStorage`: a
created and closed store, opened again after every commit, failed commits
included, each method reaching the instance current at call time). After
every case the fixture closes the case's stores and, with no store open in the
process, checks each file: `integrity_check` ok, database and lock `0600`, no
`-wal`, `-shm` or `-journal` left.

**SQLite-only tests** (`tests/durable/test_sqlite_storage.py`; real SQLite
failures come from SQLite itself, and files are read or tampered with only
while no store is open in the process):

- The account of the original file; the sixteen signatures equal
  MemoryStorage's; a structural `Storage` check for mypy.
- *Values*: the payload text equals `json.dumps`'s for 23 edge samples and 400
  seeded random trees (lone and split surrogates, signed zero, subnormals,
  integers beyond 64 bits, control characters, key order), and decoding equals
  `json.loads`; 23 malformed texts are refused as `json.loads` refuses them,
  plus NaN, infinities and repeated keys; a 100,000-deep value round-trips
  through a real store across reopen (`json` stops at depth ~1000 on Python
  3.11, ~3000 to 10,000 on 3.12, 10,000 to 100,000 on 3.15); task kinds and
  request IDs are stored as ASCII JSON while the record bytes keep each
  spelling; records, sequence and ID allocation persist across reopen (Pi's
  first SQLite test).
- *Statements*: `EXPLAIN QUERY PLAN` of exactly the statements the
  implementation builds (`_scan_sql`, `_segment`, the head, winner and exact
  reads) uses the named index for every filter of every table scan in both
  orders with and without a cursor, the integer primary key for cursor scans
  and exact reads, `entries_by_conversation`, `entry_heads_by_conversation` and
  the `submission_requests` key, and never sorts in a temporary b-tree.
- *Snapshot reads*: a real child process with its own plain SQLite connection
  commits between two statements of one read (a trace callback blocks the second
  statement until the child reports its commit): an entry scan in both orders
  and the head marker miss the foreign entry, and an entry lookup through a fork
  chain still sees the parent the foreign write removed; a fresh read after
  each sees the change.
- *Damage*: eight damaged rows (not JSON, not UTF-8, not a record, a damaged
  parent conversation, an index column that contradicts its record, a zero
  commit sequence, a binding to a missing submission, a binding to another
  request's submission) are `StorageCorruption` with a specific message and no
  payload, never a request error, while other rows still read; an invalid
  b-tree page type in the root page of `entries`, `tasks`, `submission_requests`
  or `record_ids` makes each kind of read, and the commit's claim read, raise
  `StorageCorruption` with SQLite's `SQLITE_CORRUPT` as its cause.
- *Commit failures*: a commit that runs into `max_page_count` raises SQLite's
  own `SQLITE_FULL` error unchanged after SQLite already rolled the whole
  transaction back (no rollback is attempted), leaves nothing behind and the
  store usable; a late failure whose `ROLLBACK` an authorizer denies raises
  `SqliteRollbackError` with both errors, the connection is still inside the
  transaction, and the instance is poisoned; a `COMMIT` an authorizer denies
  (D4) poisons the instance with that error as the cause, close releases the
  lock, and the reopened store shows the batch absent and commits again.
- *Interim and opener*: the document methods and every document write refuse
  with `UnsupportedStorageOperation` naming DE03c2 (delta keeps Memory's
  message) and change nothing; a failing wrapper closes the database and frees
  the lock.

**Probes** (scratch; logs in `probes/`). `json.dumps` and `json.loads` raise
`RecursionError` at nesting depth 1000 on Python 3.11.13, between 3000 and
10,000 on 3.12.15 and between 10,000 and 100,000 on 3.15.0, while owned values
have no depth limit. A configured store under `max_page_count` raises
`SQLITE_FULL` with the whole transaction already rolled back
(`in_transaction` false) on 3.11/SQLite 3.49.1, 3.12/3.53.1 and 3.15/3.53.1;
an authorizer that denies `COMMIT` raises `SQLITE_AUTH` with the transaction
still open. `EXPLAIN QUERY PLAN` of the built statements: every filter column
reaches its index (for two filters the planner takes one of the two indexes),
cursor scans and exact reads use the integer primary key, the request winner the
`submission_requests` key, and no plan sorts.

**Private negative controls** (scratch matrix `tools/mutants.py`, not in the
permanent suite). Each of 46 source mutants was applied in place to
`sqlite.py` (one to the shared `roll_back` in `sqlite_schema.py`), the new test
module ran against it, and the original bytes were restored and checked by
sha256 after every mutant. Every mutant made its designated tests fail: the
authority dropped from a read, a mint or close, or the transaction run outside
it; the read transaction removed (for all reads, and for each multi-statement
read alone); inclusive keyset bounds in either order, Pi's descending bound
below 2^53−1, an inclusive entry cursor, an unclamped page size and an entry
scan that reads nothing for negative limits; the request winner kept by the
first writer, never dropped or dropped unconditionally; lenient write,
request-ID and ID ingress and the closed check moved after admission; the
corruption guard removed from an exact read, a scan, the winner read and the
commit; decode errors escaping, index columns, the winner key, a dangling
winner and the commit sequence unchecked; an unconditional rollback, a missing
rollback, a failed rollback or a raising COMMIT that does not poison; the
counter not adopted, sequence exhaustion unchecked, cross-kind and duplicate
claims accepted; ASCII payload JSON, a recursive encoder, a reader that accepts
repeated keys or trailing text, non-ASCII identity columns; a parent read
eagerly or a dangling parent that ends the walk; and documents that silently
read as empty. The first run left one survivor, a parent read eagerly; it
revealed that no case pinned Memory reading a dangling parent's own entries
before its record, so that case was added and the mutant now fails it.

**Results.**

| Check | macOS 27.0.1 arm64, Python 3.15.0, SQLite 3.53.1 | Linux macmini, Ubuntu 24.04.5, Python 3.12.3, SQLite 3.45.1 |
| --- | --- | --- |
| coverage and acceptance tests, after the cascade | 0; 165 passed | 0; 165 passed |
| the new module with the DE03a, DE03b, Memory and coverage tests under `-X dev -W error` | 0; 891 passed, 54 xfailed | 0; 891 passed, 54 xfailed |
| `test_sqlite_storage.py` under `-X dev -W error`, 20 sequential runs | 20 of 20 exit 0; 406 passed and 54 xfailed each | 20 of 20 exit 0; 406 passed and 54 xfailed each |
| `pytest tests/durable` | 0; 1981 passed, 54 xfailed | not run |
| extension-generation inventory and `test_architecture_*` | 0; 348 passed | not run |
| the 13 test files that read the changed documents | 0; 677 passed | not run |
| `ruff check .`, `ruff format --check .`, `mypy src tests` | 0, 0, 0 (903 files) | not run |
| strict docs build and rendered links on the five changed pages, after the ledger edit | 0 ("No issues found"); 917 local links, none broken | not run |
| DE03c1 mutant matrix on the final tree | 0; 46 mutants, every designated test fails | not run |
| records written through the other host's SQLite read back exactly | the 3.45.1 store reads back | the 3.53.1 store reads back |
| leftover processes naming the directory | not applicable | 0 |

The 54 xfails are the 27 document-body instances on each SQLite backend, each
failing with exactly the interim refusal; nothing is skipped. The new and DE03
tests also ran on the locally installed uv Python 3.11.13 (SQLite 3.49.1) and
3.12.15 (SQLite 3.53.1) under `-X dev -W error`: 778 passed and 54 xfailed
each. Nothing was installed: each version got a bare `python -m venv
--without-pip` in scratch whose only addition is a `.pth` naming a snapshot of
the working tree and the site-packages of DE03b's existing scratch venv, so the
`-I` children import the snapshot too.

Linux ran in a new isolated directory,
`~/pipy-de03-linux/de03c1-20261022T041230Z/` (mode `0700`, on ext4), with
DE03b's procedure: a copy of the working tree (`git ls-files -co
--exclude-standard`, 3934 files, sent with `rsync` over SSH using the launchd
agent socket for those commands only; eleven key files compared by sha256), a
project-local venv on the system Python (`UV_PYTHON_DOWNLOADS=never uv venv
--python /usr/bin/python3.12`, then `uv sync --frozen`), and the uv cache,
`TMPDIR` and pytest temporary directories inside it. Each Linux repeat took
about two minutes against about 21 seconds on macOS. After the run the previous
directory `~/pipy-de03-linux/de03b-20261022T015019Z/` was deleted; nothing else
was removed, exactly one DE03 directory remains, and no process of the user
names it outside the checking session.

**Closing runs after the final ledger edit.** The strict docs build, the
rendered links, the coverage and acceptance tests (165), the new, DE03 and
coverage tests under `-X dev -W error` (891 passed, 54 xfailed), the inventory
and architecture tests (348), ruff, format and mypy all exited 0 again. The 13
document-reading files failed one or two cases of `test_docs_build.py`'s
preview test in each of three closing attempts (676 or 675 of 677 passed; the
test waits on file-change events from a preview server it starts for its own
temporary project) while the host's load average was between 19 and 34 with
`fseventsd` at full CPU; a clean `git archive` of HEAD, without any DE03c1
change, fails the same two cases under the same load
(`docs-build-test-head.log`), so the failure is environmental, as DE03b
recorded. The full battery above, at lower load, passed all 677. Linux received
the final tree (only this ledger changed in content) and passed the coverage
and acceptance tests again (165).

Logs: `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/logs/` (macOS: `final-*.log`, `mutant-matrix.log` and
the first run `mutant-matrix-run1.log`, `mutant-parent-read-eagerly.log`,
`coverage-update-*.log`, `cascade.log`, `closure-verify.log`,
`binding-check.log`, `closing-*.log`), `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/probes/` (probes),
`/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/pycompat/` (Python 3.11 and 3.12) and `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/linux/remote-logs/`
(Linux, copied back, with the cross-version logs in `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/linux/`).

**Manifest cascade and chain binding.** Only the three bookkeeping files the
baseline manifest hashes changed: the coverage index (DE03b LANDED with its
delivery, DE03c a cumulative parent, new DE03c1 and DE03c2 rows, the DE03
parent text, every DE source anchor recomputed for the two new plan lines), the
coverage test (113 rows; DE03b LANDED; the DE03c parent cannot land with an open
child; exact child IDs; the c rows cannot claim case evidence) and the
acceptance script (the DE03c1 and DE03c2 IDs, the plan and ledger row pattern,
the DE03c cumulative rule and the split evidence anchor). No file of the a1,
a2b, b1b or b3 closures changed (`test_memory_storage.py`, `memory.py` and the
Storage port are untouched). The cascade (baseline → a1 → a2b → b1b → b3) ran
last over closure and parent hashes only, and again after `ruff format` changed
the layout of the coverage test and the acceptance script: baseline
`41ee1f51…f8e2`, a1 `91d95fdf…4303`, a2b `e410a9a6…bb19`, b1b `1a98a0fb…a49e`
and b3 `c2684f4d…19c6`. Closure verification exits 0 with no stale entry, and
the binding check against `d2e62390` (DE02b3's `final-pairs.json`) exits 0: of
the 63 chain-hashed files only the three bookkeeping files differ, and the
manifest diff is those entries plus derived parent hashes. No chain-bound file
changed, so no chain reran.

**Size.** The new code is `sqlite.py` (905 lines including its design
docstring), `storage_contract_cases.py` (794) and `test_sqlite_storage.py`
(656), 2,355 lines, over the work order's ~2k guidance. A first contract
module that copied every original body came to 1,680 lines; running the
original bodies themselves cut it to under half, and the seven document bodies
that need SQLite document views were handed to DE03c2 as the coherent stopping
point (they would have added about 370 lines that cannot run on SQLite before
DE03c2).

**Deviations from the prep and the work order, with reasons.**

1. The contract cases run the original Memory bodies over every backend by
   replacing the module's `MemoryStorage` name, instead of copied ports; only
   bodies that observe Memory internals have variants. The Memory run is
   therefore literally the unchanged body, and nothing is duplicated.
2. Seven document bodies that observe Memory internals are listed for DE03c2
   (`DE03C2_PORTS`, checked by a test) instead of being ported and marked here:
   their hooks need SQLite document views that DE03c2 designs, and the size
   guidance. The nine public-API document bodies are marked.
3. The marker is `xfail(strict=True, raises=UnsupportedStorageOperation)`. The
   repository has no precedent for marking deferred capability cases (its skips
   are all environmental: POSIX, Node, symlinks); a strict, `raises`-limited
   xfail still runs every case, proves that the interim refusal is its only
   failure, fails the run as soon as DE03c2 makes one pass, and leaves the two
   known skips of `just check` unchanged.
4. The payload codec writes and reads the `json.dumps` text without recursion
   instead of calling `json.dumps`/`json.loads` (prep §3.4): those stop at
   Python's recursion limit and would make SQLite refuse values Memory keeps.
5. A failed rollback poisons the instance, in addition to D4: the connection
   may still be inside the failed transaction, where later reads on the same
   connection would see its uncommitted rows.
6. D4 is implemented and tested here (an authorizer denies COMMIT), and the
   rollback rule with a real `SQLITE_FULL` from `max_page_count`; both overlap
   DE03c2's list, which keeps the write-refusal suite, the after-COMMIT seam
   and the SIGKILL cuts.
7. The ID counter is adopted immediately after `COMMIT` returns, inside the
   commit, without the prep's after-COMMIT seam and `finally`; DE03c2 adds the
   seam with its acknowledgement-loss cut.
8. Read-time integrity goes beyond the prep's decode check: index columns,
   request bindings and the commit sequence are cross-checked.
9. Page sizes are clamped to 2^53 rows for SQLite's integer binding; Memory
   accepts any integer, and the result is the same.
10. The interim document methods refuse right after the closed check, without
    admitting their arguments; DE03c2 adds Memory's document ingress.
11. `sqlite_schema` gained `read_metadata` and `roll_back` (renamed from
    private helpers, reused); `test_sqlite_schema.py` follows the rename.

**Prep versus reality, flagged.** (a) `json` recursion limits (probe above):
D1's text is kept, its implementation is iterative. (b) Memory's entry scan
appends before it checks the limit and its table scan checks before it
appends, so the prep's single clamp `LIMIT max(limit+1−n, 0)` holds for table
scans but would end an entry scan before Memory's walk reaches a dangling
parent; the entry scan reads at least one row per page. (c) Memory's
`entry(id, conversation_id=...)` walks the ancestry even when the entry does not
exist, and a dangling parent's own entries are read before its record; Pi's
SQLite returns early in the first case and fails on `undefined` in the second.
The port follows Memory in both. (d) Pi's `ReopeningStorage` delegates at call
time; a wrapper that hands out bound methods of the current instance would
keep calling a closed one. Every other prep assumption held.

**Open obligations and risks for DE03c2 and DE03d.** (1) DE03c2 implements
documents, removes the nine strict-xfail marks (each will then pass and fail
the run until removed), ports `DE03C2_PORTS` with SQLite document views in the
harness, and adds `document.create` IDs to the commit's ID candidate (only table
writes reach the commit today). (2) DE03c2 owns the after-COMMIT seam with the
counter adopted in a `finally`, SIGKILL before and after COMMIT, `query_only`
and foreign-lock refusals, WAL truncation, page reuse and the size bound. (3)
The paired Pi SQLite probes of D2 (a), (c), (d) and (f), and of (b) and (e),
stay open (DE03d, or DE03c2 where it runs one). (4) The codec is pure Python:
reads and writes cost more than C `json`; DE03d's P43 budget should measure it.
(5) One `SqliteStorage` per `OwnedDatabase`: a second wrapper over the same
database would split the authority; only `open_sqlite_storage` constructs it
today, and DE11's product factory must keep that. (6) The snapshot tests use a
foreign writer only to prove read isolation; a foreign writer remains outside
the ownership contract. (7) Each page item is decoded once and copied once by
Memory's `_page`.

### DE03c1 round-1 Codex review repair

Round 1 review, Codex `gpt-6.1-sol` HIGH on tree `4704db79`: **ISSUES**, no
Critical and three Warnings, accepted by the coordinator and repaired by Claude
Code `claude-opus-5-5` MAX; no CI result is claimed. Each finding was
reproduced before the repair (`repair-r1-probes/probe_findings_before.log`).

- **Warning 1: a forked child could wait forever for the instance lock.** Every
  public method took `_lock` first; the fork check came only later, inside
  `OwnedDatabase.connection()`. Reproduced: a child process whose writer thread
  was paused at `_before_commit`, holding the lock, forked, and the
  grandchild's `close()` had not returned after 5 seconds when it was killed.
- **Warning 2: entry scans and the head marker skipped the commit-sequence
  check.** Only `_lookup` (both `entry` forms) checked `commit_seq`; `_entries`
  and `_head` called `_record` directly. Reproduced: with `commit_seq = 0`,
  `entry(2)` raised `StorageCorruption` while `scan_entries` returned the entry
  and `find_latest_head_marker` returned it as the head.
- **Warning 3: `sqlite3` decoded stored text before any check.** Reproduced:
  invalid UTF-8 in `tasks.kind` or `status`, in `submissions.request_id` or
  `status` (written past their CHECKs with `ignore_check_constraints`), or in
  `record_ids.record_type` made `task`, `scan_tasks`, `submission`,
  `submission_by_request` and the commit's claim read raise
  `sqlite3.OperationalError: Could not decode to UTF-8 column ...`, which has no
  `sqlite_errorcode` (so `damage_as_corruption` passed it on) and quotes the
  stored text.

**Audit beyond the findings.**

- *Fork.* DE03a's owners check `forked` (a pid comparison without a lock)
  before their locks: `PathOwner.check_usable`, `OwnedDatabase.connection()` and
  `close()`. `SqliteStorage` was the only layer that locked first. DE03a resets
  no instance lock at fork (its fork hooks hold only the module registry lock
  across `fork()`), and once every path checks first, no path of a child takes
  the inherited lock at all, so no at-fork reset was added.
- *Row checks, table by table.* Conversations: the exact read, the scan and the
  fork walk's parent reads. Entries: both `entry` forms, the head marker and
  both scan orders. Tasks and submissions: the exact read and the scan;
  submissions also the request winner. All of them now reach one function that
  makes every check of a stored row. The checks only the winner read can make
  (a binding to a missing submission or to another request's) stay there, since
  only it reads `submission_requests`. `record_ids` is read only by the commit's
  claims, which now decode and check the record type.
- *Text beyond the TEXT columns.* The tables are STRICT, but SQL alone can
  defeat that: rewrite a table's schema without `STRICT` through
  `writable_schema`, write, and restore the schema text, which open-time
  validation then finds unchanged. Reproduced
  (`probe_strict_defeat_before.log`): invalid-UTF-8 TEXT in `entries.head`,
  `commit_seq` or `record` and in `tasks.background` or `record` made every read
  of the row raise the same raw `OperationalError` (`PRAGMA integrity_check`
  reports "non-INTEGER value in entries.head"). DE03b's open-time reads had the
  same exposure: schema text with invalid UTF-8 in an SQL comment made `open`
  raise it (`probe_schema_text_before.log`), and the schema version and the
  metadata counters can hold such TEXT through the same round trip. The repair
  covers both, as the finding extends to `sqlite_schema` read paths if
  affected. `quick_check` findings are SQLite's own text about objects
  `validate_store` has already checked, and `_pragma` reads header integers.

**Repair.**

1. *Fork first.* Every method except `close` enters through one context
   manager, `SqliteStorage._authority()`: the fork check without a lock, raising
   DE03a's `StorageForkedError("A durable store owner cannot be used after
   fork()")`, then the lock, then the closed and poisoned checks, yielding the
   connection. `close()` returns at once in a forked child, before the lock.
2. *Every row check on every read.* `_record` makes every check of a stored
   row: strict decoding of its TEXT columns, decoding and validating the
   record, comparing the index columns and, for an entry, the commit sequence.
   `_lookup` only wraps its result, so the head marker, both scan orders and
   both `entry` forms check alike.
3. *No implicit decoding.* One function builds every SELECT list. The TEXT
   columns (`kind`, `status`, `request_id`) are read as `CAST(col AS BLOB)` and
   decoded strictly by `_record`; a decoding error is a `ValueError`, so it is
   "unreadable record" `StorageCorruption`. Every other column goes through the
   new `sqlite_schema.read_column`, `CASE typeof(col) WHEN 'text' THEN x'' ELSE
   col END`, so TEXT where a STRICT column keeps none reads as an empty `BLOB`
   that no check accepts. The claim read selects `CAST(record_type AS BLOB)`,
   decodes strictly and accepts only the five record types. The last-writer
   drop no longer reads the previous request key into Python: one statement
   deletes the binding `WHERE submission_id = ? AND (conversation_id,
   request_id) = (SELECT conversation_id, request_id FROM submissions WHERE id =
   ?)`, with the submission ID bound twice, the same rule; its plan searches
   the binding's primary key and the submission's rowid, without a scan.
   `submission_by_request` compares the decoded record's key, which `_record`
   has proven equal to the row's columns.
4. *`sqlite_schema`.* `_objects` reads the four schema columns as bytes and
   decodes them strictly ("The durable store's schema text is not UTF-8"); the
   schema version and both counters are selected through `read_column`, and the
   version must be an `int` ("SQLite schema metadata is not an integer", where
   `int(row[0])` accepted text before). No message quotes stored text.

**Regressions.** `test_sqlite_storage.py` has 580 cases now (526 pass, plus
the same 54 strict xfails; 460 before), `test_sqlite_schema.py` 71 (67 before):

- *Fork under contention* (one case, real processes): a child opens the store;
  a writer thread commits and pauses at `_before_commit` holding the lock (a
  deterministic barrier); the child forks. The grandchild calls all sixteen
  methods (`entry` in both forms, `close` twice) and reports that every call
  raised `StorageForkedError` with DE03a's message, both closes returned
  `None`, the inherited lock was still locked (the fork really happened under
  contention), and all of it took milliseconds. The child reaps it with
  `WNOHANG` against a 20-second deadline (a hang would be killed and fail the
  case) and releases the writer, whose commit lands (sequence 3) and reads
  back; the test process reopens the store, finds the entry and passes
  `integrity_check`, and the process group is reaped.
- *Damage on every read* (67 cases, replacing the 8 single-read damage cases):
  19 damaged rows (records that are not JSON, not UTF-8 or not a record;
  contradicting index columns; commit sequence 0 or 2^53; invalid UTF-8 in each
  TEXT column of `tasks` and `submissions`; a binding to a missing submission or
  to another request's; an unreadable or unknown claim), each against every
  read method that reaches the row (exact, both `entry` forms, the head marker,
  both scan orders, filtered and unfiltered scans, the request winner, the
  commit's claims). Each raises `StorageCorruption` with its specific message,
  never a request error and never quoting stored text, while another table
  still reads.
- *TEXT in STRICT non-TEXT columns* (52 cases): 13 INTEGER and BLOB columns of
  the four tables, through the schema round trip, against every read that
  reaches the row, each with its message. In `test_sqlite_schema.py`, schema
  text that is not UTF-8 and TEXT in the schema version or either counter are
  refused at open, twice, with no byte changed and the lock free.
- *Valid text unchanged* (7 cases, across reopen): empty, ASCII, non-ASCII, an
  astral character, a lone high and a lone low surrogate and JSON
  metacharacters as task kind and request ID read back exactly through strict
  decoding, find their task by kind (and nothing for another kind) and win
  their request (D1: identity columns are ASCII JSON).
- *Plans*: the new winner drop and the claim read search their keys; neither
  scans.
- *Placeholders*: no string literal of `sqlite.py` or `sqlite_schema.py` holds
  a numbered placeholder (below).

**Linux found one more defect.** The first Linux run of the repair (Python
3.12.3, `logs-attempt1`, copied back to `repair-r1-linux/remote-logs-attempt1/`)
failed 186 cases under `-X dev -W error`, the same 186 in each of the 20
repeats: the winner drop first used the numbered placeholder `?1` for both
uses of the submission ID, and Python 3.12.3's `sqlite3` takes `?1` for a
named placeholder when the parameters are a sequence, warning
(`DeprecationWarning`, an error under `-W error`) on every submission write.
The uv builds of Python 3.11.13, 3.12.15 and 3.15.0 accept it, so macOS could
not see it. The drop now binds the submission ID twice through plain `?`
placeholders, the static placeholder check above keeps every statement literal
plain, and the final Linux run, after the fix, is the one reported below.

**Side by side** (`repair-r1-sbs/`). The final tests ran against the reviewed
sources (`4704db79`'s `sqlite.py` and `sqlite_schema.py`) and against the
repaired ones, each in a snapshot tree with its own bare venv of the same
Python 3.15, so the `-I` children import that side too. Reviewed: exit 1, 78
failed, 519 passed, 54 xfailed. Repaired: exit 0, 597 passed, 54 xfailed. The
78: the fork case (the grandchild never reported and was killed at the
deadline, while the writer's commit still landed); 6 commit-sequence cases
that did not raise (head marker and both scan orders); 69 raw
`OperationalError` "Could not decode to UTF-8" (12 TEXT-column cases, the
unreadable claim, the 52 STRICT cases, the three counters and the schema
text); the unknown claim, which raised `RuntimeError("ID 3 already belongs to
nothing")`; and the plan case, which is only a statement-shape artifact (the
reviewed drop statement takes three bindings).

**Negative controls.** The scratch matrix has 61 mutants now (46 before): 15
new ones (the fork check moved after the lock, a forked close that waits for
it, the authority without its lock, the commit-sequence check removed from
`_record` and put back in `_lookup` only, TEXT columns, other columns, claims
and schema text decoded by `sqlite3` again, a decoding error escaping
`_record`'s guard or the claim's, an unknown claim type accepted, a schema
version of any type, the winner drop as a table scan, and the drop with
numbered placeholders as first written), 12 re-anchored to the new code and 6
with updated designations. The first run left one survivor,
`dangling-winner-unchecked`: the rewritten damage cases checked only the
exception class, and a binding to a missing submission is refused anyway as an
unreadable record. Every damage case now also matches its message, which the
replaced cases had done, and the mutant fails. The final run, on the tree with
plain placeholders, fails every designated test of all 61 mutants, and the
unmodified sources pass all 651 cases of both files
(`repair-r1-matrix/matrix-run1.log`, `matrix-run2.log` before the placeholder
fix, `matrix.log`).

**Results after the repair.** These supersede the DE03c1 table above.

| Check | macOS 27.0.1 arm64, Python 3.15.0, SQLite 3.53.1 | Linux macmini, Ubuntu 24.04.5, Python 3.12.3, SQLite 3.45.1 |
| --- | --- | --- |
| coverage and acceptance tests, after the cascade | 0; 165 passed | 0; 165 passed |
| the new module with the DE03a, DE03b, Memory and coverage tests under `-X dev -W error` | 0; 1015 passed, 54 xfailed | 0; 1015 passed, 54 xfailed |
| `test_sqlite_storage.py` under `-X dev -W error`, 20 sequential runs | 20 of 20 exit 0; 526 passed and 54 xfailed each | 20 of 20 exit 0; 526 passed and 54 xfailed each |
| `pytest tests/durable` | 0; 2105 passed, 54 xfailed | not run |
| extension-generation inventory and `test_architecture_*` | 0; 348 passed | not run |
| the 13 test files that read the changed documents | 0; 677 passed | not run |
| `ruff check .`, `ruff format --check .`, `mypy src tests` | 0, 0, 0 | not run |
| strict docs build and rendered links on the five changed pages, after the ledger edit | 0 ("No issues found"); 919 local links, none broken | not run |
| DE03c1 mutant matrix on the final tree | 0; 61 mutants, every designated test fails | not run |
| the final tests on the reviewed sources (`4704db79`) | exit 1; 78 failed, 519 passed, 54 xfailed | not run |
| records written through the other host's SQLite read back exactly | the 3.45.1 store reads back | the 3.53.1 store reads back |
| leftover processes naming the directory | not applicable | 0 |

The new and DE03 tests also ran on the locally installed uv Python 3.11.13
(SQLite 3.49.1) and 3.12.15 (SQLite 3.53.1) under `-X dev -W error`: 902
passed and 54 xfailed each, with the same bare-venv snapshot procedure as
before (`repair-r1-pycompat/`). The table's macOS column is the battery on the
final tree (`repair-r1-final-*.log`); the first battery, before the placeholder
fix (`repair-r1-*.log`), also passed except for one case of
`test_docs_build.py`'s preview test under load, the environmental failure
recorded above.

Linux ran in a new isolated directory,
`~/pipy-de03-linux/de03c1-20261022T064246Z/` (mode `0700`, on ext4), with the
same procedure as before: a copy of the working tree (3934 files, sent with
`rsync` over SSH using the launchd agent socket for those commands only; key
files compared by sha256), a project-local venv on the system Python with `uv
sync --frozen`, and the uv cache, `TMPDIR` and pytest temporary directories
inside it. After the first attempt the three changed files were sent again
(eleven key files compared by sha256 again) and the tests reran in the same
venv; the first attempt's logs are kept as `logs-attempt1`. After the run the
previous directory `~/pipy-de03-linux/de03c1-20261022T041230Z/` was deleted;
nothing else was removed, exactly one DE03 directory remains, and no process of
the user names it outside the checking session.

**Cascade.** `coverage.json` changed again (the DE03c1 observables name the
repair and its regressions; nothing else), so the cascade ran last: baseline
`0845440b…2f0f`, a1 `7b9b0d4a…ff05`, a2b `db981ebc…1371`, b1b `615a6efa…fd8c`
and b3 `ec7690a8…9699`. No file of any closure changed otherwise: the
repair's sources and tests are not hashed. Closure verification exits 0 with
no stale entry, the key-level diff against HEAD is still the three bookkeeping
entries plus the derived parent hashes, and the binding check against
`d2e62390` exits 0 (only the three bookkeeping files of the 63 chain-hashed
files differ).

**Size.** Against `4704db79`: `sqlite.py` 905 → 955 lines (+140, −90),
`sqlite_schema.py` 666 → 705 (+54, −15), `test_sqlite_storage.py` 656 → 1135
(+511, −32) and `test_sqlite_schema.py` 1330 → 1395 (+65);
`storage_contract_cases.py` is unchanged. Most of the test growth is the
damage tables, one row per damage with its reads and message.

**Deviations, with reasons.** (1) The text repair covers every selected
column, not only the TEXT columns the finding named, and DE03b's open-time
reads: the same raw error was reproducible there with SQL alone. (2) The
schema version must now be an `int`; text was accepted. (3) The winner drop
became one SQL statement instead of a Python read of the previous request ID.
(4) No at-fork lock reset (audit above). (5) A static test bans numbered
placeholders, a Python 3.12.3 portability rule that only the Linux run could
show.

Logs: `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/repair-r1-logs/` (macOS: `repair-r1-final-*.log` and
`repair-r1-*.log`, coverage update, cascade, closure, binding, docs build,
rendered links and `closing-*.log`), `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/repair-r1-probes/`
(reproductions before and after), `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/repair-r1-matrix/`, `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/repair-r1-sbs/`,
`/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/repair-r1-pycompat/` and `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/repair-r1-linux/remote-logs/` (Linux,
copied back, with the cross-version logs in `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/repair-r1-linux/`).

### DE03c1 round-2 Codex review repair

Round 2 delta review, Codex `gpt-6.1-sol` HIGH on tree `1043a5cd`: **ISSUES**,
no Critical and one Warning, caused by the round-1 repair, accepted by the
coordinator and repaired by Claude Code `claude-opus-5-5` MAX; no CI result is
claimed.

- **Warning: `CAST(... AS BLOB)` erased the storage class.** Round 1 read the
  TEXT columns as `CAST(col AS BLOB)`, so a BLOB stored in a TEXT column read
  back as the same bytes and passed. Reproduced
  (`repair-r2-probes/probe_storage_classes_before.log`, values stored through
  the schema round trip): with `tasks.kind` a BLOB, `task(3)` and an unfiltered
  `scan_tasks` returned the task while `scan_tasks` filtered by its kind found
  nothing. The same probe found more of the class: BLOBs in `tasks.status`,
  `submissions.request_id` and `status`, and `record_ids.record_type` (the
  commit's claim accepted it), and numerically equal REALs (`1.0`, `0.0`) in
  `tasks.conversation_id` and `background`, `entries.conversation_id` and
  `head` and a binding's `submission_id` and `conversation_id`, were all
  accepted; SQLite's own schema rows with a BLOB `sql` or `name` opened. An
  INTEGER `kind` and a TEXT payload were refused, but only by the index
  cross-check and the payload decoder.

**Repair.** Two helpers in `sqlite_schema`: `typed_columns(*names)` selects
each column's `typeof` and its value (text as `CAST(... AS BLOB)`, so
`sqlite3` never decodes it), and `typed_values(row, classes, refuse)` checks
every column's storage class against the classes its declared type allows
(`integer`, `text` or `blob`, plus `null` only where the column is nullable)
before anything is decoded, then decodes text strictly. Every read uses them:
the four record tables (`_COLUMNS` lists each column with its classes; the
index columns derive from it), `submission_by_request` (the binding's three
columns, then a missing submission, then the submission row), the commit's ID
claims, SQLite's schema rows, the schema version and the metadata counters. A
refusal names the row (by ID where it has one), the column, the class found and
the classes allowed, never stored data. Round 1's `read_column` is gone.

**Audit: every column and the read that checks it.** One test
(`test_read_classes_are_the_schema_declared_types`) derives these classes from
`PRAGMA table_xinfo` (declared type, NOT NULL, primary key) and compares them
with the read tables.

| Table | Column | Allowed storage classes | Read sites |
| --- | --- | --- | --- |
| `conversations` | `id` (rowid) | integer | `conversation`, `scan_conversations`, the fork walk's parent reads (`entry` with a conversation, the head marker, entry scans) |
| `conversations` | `owner_conversation_id`, `owner_task_id` | integer or null | the same, then cross-checked with the record |
| `conversations` | `record` | blob | the same |
| `entries` | `id` (rowid) | integer | both `entry` forms, the head marker, both scan orders |
| `entries` | `conversation_id`, `commit_seq` | integer | the same (`commit_seq` then 1..2^53−1) |
| `entries` | `head` | integer or null | the same |
| `entries` | `record` | blob | the same |
| `tasks` | `id`, `conversation_id`, `abort_requested`, `background` | integer | `task`, `scan_tasks` with any filter |
| `tasks` | `kind`, `status` | text | the same |
| `tasks` | `record` | blob | the same |
| `submissions` | `id`, `conversation_id` | integer | `submission`, `scan_submissions` with any filter, `submission_by_request` |
| `submissions` | `request_id` | text or null | the same |
| `submissions` | `status` | text | the same |
| `submissions` | `record` | blob | the same |
| `submission_requests` | `conversation_id`, `submission_id` | integer | `submission_by_request` |
| `submission_requests` | `request_id` | text | `submission_by_request` |
| `record_ids` | `record_type` | text, one of five types | the commit's claims (`id` is the rowid key) |
| `durable_metadata` | `next_id`, `next_seq` | integer, then in range | `read_metadata`: open-time validation, `SqliteStorage`, every commit |
| `durable_schema` | `version` | integer | `validate_store`, the migration bootstrap |
| `sqlite_schema` (SQLite's, not STRICT) | `type`, `name`, `tbl_name` | text | `validate_store` |
| `sqlite_schema` | `sql` | text or null | `validate_store` |

Nothing else is read into Python: `documents` and `document_revisions` are
not read before DE03c2; `_pragma` reads header integers and `quick_check`
SQLite's own report; the last-writer drop and the upserts compare stored values
inside SQL only, and a binding they leave stale is refused by the winner read.

*Filtered reads.* SQLite never finds TEXT or a BLOB equal to an integer filter,
nor a BLOB or a number equal to a text filter, so such a value hides its row
from an equality filter on that very column; the read returns without the row,
never the row with the wrong value. Every read that reaches the row refuses
it, and a numerically equal REAL in an INTEGER column is found by its filter
and refused. `PRAGMA quick_check` reports every such value (the probe shows
"non-TEXT value in tasks.kind"), but it runs only after an unclean shutdown
(D6); any tampered index column hides rows from filters the same way.

**Regressions.** `test_sqlite_storage.py` has 752 cases now (698 pass, plus the
same 54 strict xfails; 580 before), `test_sqlite_schema.py` 83 (71 before):

- *Values of another storage class* (223 cases): 45 values stored through the
  schema round trip, each asserted to hold its class, against every read that
  reaches the row (each refuses with the class message) and every filtered
  read that cannot (each returns without the row): BLOB, INTEGER, REAL and NULL
  in the TEXT columns (`kind` and `status`, `request_id` and `status`, the
  binding's `request_id`, `record_type`); TEXT (invalid and valid UTF-8), REAL
  and BLOB in the INTEGER columns of the four tables and of the binding; TEXT
  and numbers in each payload.
- The invalid-UTF-8 TEXT cases now name the column ("whose kind column is not
  UTF-8"); round 1's 52 TEXT-in-STRICT cases are part of the 223.
- `test_sqlite_schema.py`: a BLOB `sql`, `name`, `type` or `tbl_name` in
  SQLite's schema rows is refused at open (a REAL `sql` is SQLite's own
  malformed-schema refusal), and the schema version (invalid and valid text,
  REAL, BLOB, NULL) and the counters (invalid text, REAL, BLOB; text, REAL) are
  refused with no byte changed and the lock free.
- The declared-classes test above.

The round-1 helper became `store_in_strict_column(target, table, column, value,
where)`: it rewrites the table's schema without STRICT and with the column
untyped and nullable (BLOB affinity, so SQLite converts nothing: a REAL `1.0`
stays REAL), writes, restores the schema text and returns the classes now
stored.

**Side by side** (`repair-r2-sbs/`, the final tests on `1043a5cd`'s sources
and on the repaired ones, as in round 1). Reviewed: exit 1, 234 failed, 547
passed, 54 xfailed. Repaired: exit 0, 781 passed, 54 xfailed. Of the 234, 65
accepted a value of the wrong class (61 storage-class cases across 14 values:
the BLOB `kind`, `status`, `request_id` and claim; REAL conversation IDs,
heads, flags and binding columns; and the binding's TEXT `submission_id`,
which SQLite's rowid lookup converts to an integer; plus the 4 BLOB schema
rows); 168 were refused with an older, less specific message (contradicting
index columns, an unreadable record, a missing commit sequence, "not an
integer", "out of range"); 1 is the declared-classes test, which needs this
repair's column tables.

**Negative controls.** The matrix has 62 mutants: 47 unchanged, 6 re-anchored
to the new row checks, and 9 new for round 2, replacing 8 whose code is gone:
the row, binding, claim, schema, version and counter classes each accepting
every class, values decoded by `sqlite3`, a decoding error escaping its guard
and the schema read untyped (DE03b's original read). The first round-2
run missed one designated test: with the commit run outside the instance lock
(`commit-outside-authority`), the `close` contention case passed, and targeted
reruns showed the `read` case could too. The contention harness only asserted
that someone held the authority after the contender's attempt, and a contender
running without the writer's lock can hold it at that moment and still win the
race. `storage_contract_cases._run_contended` now also checks, at the writer's
late commit point and before any contender starts, that the authority cannot
be taken (a non-blocking acquire fails); with that, the mutant failed all four
contention cases in 5 of 5 targeted runs. The final full run, on the final
tree, fails every designated test of all 62 mutants, and the unmodified sources
pass all 835 cases of both files (`repair-r2-matrix/matrix.log`;
`matrix-run1-full.log` and `commit-outside-authority-*.log` before the
harness check).

**Results after the round-2 repair.** These supersede the round-1 table.

| Check | macOS 27.0.1 arm64, Python 3.15.0, SQLite 3.53.1 | Linux macmini, Ubuntu 24.04.5, Python 3.12.3, SQLite 3.45.1 |
| --- | --- | --- |
| coverage and acceptance tests, after the cascade | 0; 165 passed | 0; 165 passed |
| the new module with the DE03a, DE03b, Memory and coverage tests under `-X dev -W error` | 0; 1199 passed, 54 xfailed | 0; 1199 passed, 54 xfailed |
| `test_sqlite_storage.py` under `-X dev -W error`, 20 sequential runs | 20 of 20 exit 0; 698 passed and 54 xfailed each | 20 of 20 exit 0; 698 passed and 54 xfailed each |
| `pytest tests/durable` | 0; 2289 passed, 54 xfailed | not run |
| extension-generation inventory and `test_architecture_*` | 0; 348 passed | not run |
| the 13 test files that read the changed documents | 0; 677 passed | not run |
| `ruff check .`, `ruff format --check .`, `mypy src tests` | 0, 0, 0 | not run |
| strict docs build and rendered links on the five changed pages, after the ledger edit | 0 ("No issues found"); 921 local links, none broken | not run |
| DE03c1 mutant matrix on the final tree | 0; 62 mutants, every designated test fails | not run |
| the final tests on the round-1 sources (`1043a5cd`) | exit 1; 234 failed, 547 passed, 54 xfailed | not run |
| records written through the other host's SQLite read back exactly | the 3.45.1 store reads back | the 3.53.1 store reads back |
| leftover processes naming the directory | not applicable | 0 |

The new and DE03 tests also ran on the locally installed uv Python 3.11.13
(SQLite 3.49.1) and 3.12.15 (SQLite 3.53.1) under `-X dev -W error`: 1086
passed and 54 xfailed each (`repair-r2-pycompat/`).

Linux ran in a new isolated directory,
`~/pipy-de03-linux/de03c1-20261022T091203Z/` (mode `0700`, on ext4), with the
same procedure: a copy of the working tree (3934 files, sent with `rsync` over
SSH using the launchd agent socket for those commands only; eleven key files
compared by sha256), a project-local venv on the system Python with `uv sync
--frozen`, and the uv cache, `TMPDIR` and pytest temporary directories inside
it. The first attempt ran the final sources with the harness before its
ownership check: coverage 165, the `-X dev -W error` set 1199 passed and 54
xfailed, and 12 repeats at 698 passed and 54 xfailed each, when the harness
changed; it was stopped by terminating its process group, no process remained,
and its logs are kept as `logs-attempt1`. The changed files were sent again
(seven key files compared by sha256) and the tests reran in the same venv;
the table shows that run. After it the round-1 directory
`~/pipy-de03-linux/de03c1-20261022T064246Z/` was deleted; nothing else was
removed, exactly one DE03 directory remains, and no process of the user names
it outside the checking session.

**Cascade.** `coverage.json` changed again (the DE03c1 observables name the
storage-class checks, their regressions and the round-2 repair; nothing else),
so the cascade ran last: baseline `2c5b4d89…34c2`, a1 `49b36901…82f5`, a2b
`f75b0258…45df`, b1b `f1810004…3015` and b3 `3c9c2b32…6023`. No file of any
closure changed otherwise. Closure verification exits 0 with no stale entry,
and the binding check against `d2e62390` exits 0 (only the three bookkeeping
files of the 63 chain-hashed files differ).

**Size.** Against `1043a5cd`: `sqlite.py` 955 → 1011 lines (+125, −69),
`sqlite_schema.py` 705 → 771 (+119, −53), `test_sqlite_storage.py` 1135 → 1437
(+385, −83), `test_sqlite_schema.py` 1395 → 1509 (+138, −24) and
`storage_contract_cases.py` 794 → 801 (+7, the harness check).

**Deviations, with reasons.** (1) The check covers every column read, not
only the TEXT columns of the finding: the probe found REALs in INTEGER columns
and BLOB schema rows accepted as well. (2) The binding's three columns are
checked although only `conversation_id` and `submission_id` can reach a read
with another class: one rule for every read. (3) The contention harness gained
the ownership check (test-only) so that its negative control is deterministic.
(4) SQLite's own schema rows: a BLOB there is refused by the class check, a
number by SQLite itself.

Logs: `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/repair-r2-logs/` (macOS battery `repair-r2-*.log`,
coverage update, cascade, closure, binding, docs build, rendered links and
`closing-*.log`), `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/repair-r2-probes/`, `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/repair-r2-matrix/`,
`/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/repair-r2-sbs/`, `/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/repair-r2-pycompat/` and
`/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/repair-r2-linux/remote-logs/` (Linux, copied back, with
`remote-logs-attempt1/` and the cross-version logs in
`/private/tmp/claude-501/-Users-jochen-projects-pipy/3a0db0a1-872c-41aa-9bfe-94a686df1f96/scratchpad/de03c1/repair-r2-linux/`).

### DE03c1 delivery and DE03c2 implementation ownership

DE03c1 landed on `main` and origin as
`1563bb6871f869abfb93d704b01c637b2b2ef7c1`; the previous coordinator verified
its push with `git ls-remote`. Its implementer remains Claude Opus 5.5, with
Codex `gpt-6.1-sol` HIGH independent review: round 1 ISSUES (three Warnings),
round 2 ISSUES (one Warning caused by the repair), both repaired as recorded
above; round 3 CLEAN. Local `just check` reported 15,184 passed, 2 skipped,
54 xfailed and one failure in the known load-dependent docs-preview test.
The isolated `tests/test_docs_build.py` rerun passed 16/16. This is not a
claim of a clean full-suite result or a GitHub CI result.

The owner changed implementation ownership from DE03c2 onward: Codex writes
implementation and repairs; independent Claude Code `claude-opus-5-5`, effort
HIGH, reviews exclusively through the installed `claude-review-loop` harness.
Earlier implementer/reviewer identities are unchanged. The local work order
also reconciles the c2 plan cell: native contract cases pin D2 (b)/(e) here;
actual paired Pi SQLite probes of all D2 (a)–(f) belong to DE03d. DE03c waits
c1+c2, DE03 waits a+b+c+d, and every P row remains OPEN.

### DE03c2 native documents and failure cuts — implementation, gates pending

**Behavior and authority.** `native/durable/sqlite.py` extends the same private
Storage authority to document create, raw-base change and retire within mixed
commits. Global ID claims include document creates and their IDs advance the
commit candidate; retired IDs remain claimed. Grouped per-ID actions and final
address cardinality follow Memory, including replacement-before-retirement and
create-and-retire in one batch. SQL writes prepare together before the last
private commit seam; an address conflict is rejected after that preparation,
inside the same transaction. Delta/copy batches and numeric history retain
Memory's whole-batch refusal and exact ingress order.

Document record columns and revision columns are all read through
`typed_columns`/`typed_values`, with storage-class validation, strict UTF-8 for
TEXT, payload validation and index cross-checks. No stored text reaches
`sqlite3`'s implicit decoder, and errors do not quote stored values. A current
materialization reads the record and latest base in one read transaction;
address/scope/kind scans use the schema indexes. Payloads and revision versions
are lossless JSON BLOBs, including raw versions `0`, `-1`, `1.5`, `-0.0` and
integers beyond 64 bits (D2 (b)/(e)). A current-only base replaces older
revision rows and retirement reclaims them; rewindable documents retain their
base rows. Historical reconstruction and the full retention proof remain DE27.

**Contract cases.** The 54 strict document xfails are removed. All seven
pending bodies now have backend variants through document views (grouping,
ordinary failures, late mixed failure, common contended authority, unsupported
mixed batches, exact point ingress and bounded real-child reentry refusal).
`DE03C2_PORTS` and `DE03C2_XFAILS` are empty, with an inventory assertion.
Additional Memory/SQLite/reopen cases retain signed-zero payloads and all the
raw numeric version values. The reopening wrapper deliberately loses an
uncommitted mint when it reopens after a failed commit, exactly as Pi's
wrapper does; the shared case accounts for that instead of claiming minted
IDs were durable.

**Real failures and cuts.** `test_sqlite_documents.py` drives SQLite's own
`query_only` (`SQLITE_READONLY`), `max_page_count` (`SQLITE_FULL`, including its
automatic rollback) and a foreign child holding `BEGIN IMMEDIATE`
(`SQLITE_BUSY` after an actual 20-ms busy timeout, restored to five seconds).
Each checks the original SQLite error class/code, no adopted IDs/sequence,
rollback only while in a transaction, usable storage and closed-file
`integrity_check`. A denied COMMIT extends D4 poisoning to document methods.
The private no-op `_after_commit` seam runs only after COMMIT returned and the
ID counter was adopted: its exception propagates, the instance and reopened
store see the durable mixed commit, and the counter includes document IDs.

Three real children block at the before-COMMIT, after-COMMIT and mid-batch
cuts. The parent observes readiness, sends actual SIGKILL to its own process
group, reaps it within deadlines, reopens and checks all-or-none entry and
document rows, sequence/IDs, integrity, `0600` modes and free ownership. No
sleep establishes cut ordering. A real foreign writer committing between the
document-record and revision statements proves one materialization snapshot.
Wrong-class column tests cover every document/revision column besides the
SQLite rowid, specific malformed rows and damaged b-tree pages cover both
document reads and mixed writes. The schema-declared-classes inventory now
includes documents and revisions.

**WAL and bound.** With `secure_delete=ON`, twenty cycles create a document with
a 128,000-character value, replace it with another value of the same size and
retire it. The regression asserts a concrete database bound of 512 KiB and no
more than four pages growth after the first cycle, then observes close's
TRUNCATE checkpoint and an absent or empty WAL. Retired records and immutable
ID claims still grow with identity count; this is a bounded workload, not a
constant-space guarantee or a power-loss experiment.

**Checks at the initial source freeze.** macOS native development Python
3.15.0 / SQLite 3.53.1: `python -X dev -W error -m pytest
 tests/durable/test_sqlite_documents.py tests/durable/test_sqlite_storage.py`
exit 0, **974 passed, zero xfails**, in 79.73 seconds. The dedicated new file
alone passed 75/75. Extension-generation and package architecture inventory:
exit 0, **348 passed**. Changed-file ruff: exit 0. `mypy src tests`: exit 0,
no issues in 904 source files (existing annotation notes only).

Logs under `/private/tmp/pipy-de03c2-logs/`: `focused-final.log`,
`documents-fourth.log`, `architecture.log`, `ruff.log`, `mypy.log`.
Twenty warning-clean sequential repeats, an isolated scratch mutant matrix,
Linux/3.11/3.12 checks, docs/coverage and the exact-tree full/review/delivery
gates are still pending at this entry. The coordinator owns platform tests,
`just check`, strict docs build/rendered links, independent review and delivery;
this implementation entry claims none of those outcomes prematurely.

### DE03c2 round-1 repair and completed validation — final gates pending

This later entry supersedes the initial freeze's pending checks only where an
outcome is explicitly stated below. It preserves the initial 974-test result
and earlier implementation history. DE03c1 is LANDED at `1563bb68`; DE03c2
remains IN_PROGRESS, every P row remains OPEN, and neither a final independent
CLEAN nor delivery is claimed.

**Independent review.** Claude Code `claude-opus-5-5`, effort HIGH, through the
installed `claude-review-loop` harness, round 1: **ISSUES**, one Warning and
four Suggestions, all accepted by the coordinator and repaired by Codex. The
Warning found that the privacy sentinel had never actually been stored. The
repaired cases persist distinct private payload, kind and key sentinels, retain
private text in malformed record/content payloads and store a distinct invalid
version sentinel; they assert that stored data is absent from the outer message
and every cause/context message, with cycle protection. The new negative control
actually leaks that stored invalid version into `StorageCorruption` and the
repaired test detects it.

The port-strength repair restores the owner-conversation 99 write, in-candidate
task/status/owner/entry/scope observations and every original public read after
rollback, all-method refusal after the contended close, and empty stderr from
the bounded reentry child. The seven ported bodies contain **133 actual cases**;
removing the one obsolete interim-refusal test gives a net increase of 132
before the other new cases. A startup/readiness failure can no longer bypass
cleanup: the foreign writer enters its lifetime context before readiness is
observed, and the store closes on every exit. A real child holding
`BEGIN IMMEDIATE` receives an injected readiness-observation failure; the new
regression verifies SIGKILL, reaping, a gone process group, closed pipes, free
ownership and unchanged committed state after reopen. The dead line and source
string replacement in the SIGKILL child are removed. The product source change
in this repair is its accurate authority/snapshot-order docstring; production
behavior is unchanged. The storage document now describes a real
`SQLITE_FULL` caused by a page-count limit, rather than claiming that a disk was
filled.

**Final worker checks on the repaired source/test freeze.** macOS development
Python 3.15.0 / SQLite 3.53.1, with `-X dev -W error` for execution tests:

| Check | Result |
| --- | --- |
| combined document and Storage suites | exit 0; **975 passed**, zero xfails, 80.56 s |
| document-selected cases, twenty sequential runs | **20/20 exit 0**, each **353 passed, 622 deselected**; 479.25 s aggregate |
| isolated negative-control matrix | exit 0; **77/77** mutants fail every designated test |
| unmodified isolated three-module control | exit 0; **1058 passed**, 100.77 s |
| extension-generation and package architecture inventory | exit 0; **348 passed**, 24.15 s |
| coverage and acceptance | exit 0; **165 passed**, 7.92 s |
| Ruff check, Ruff format check, mypy | exit 0 each; 971 formatted files, no mypy issues in 904 source files |
| manifest closure and bound-chain check | exit 0 each; **STALE 0**, **PROBLEMS `[]`** |

The final document selection includes every new document test and all shared
document bodies/ports across Memory, SQLite and reopen. The old broad repeats
1–14 each passed 974 cases; they belong to the earlier test revision and are
not relabeled final repetitions. Round 15's footer reported 974 passed, but the
runner was stopped before its exit status was collected, so no result is
claimed for it. The earlier standalone durable suite passed 2565 cases in
359.72 seconds, also on the earlier test revision; the coordinator's final full
check covers the repaired tree.

The first private matrix attempt reported an apparent scan-guard survivor:
equal-size mutations written within one timestamp second could reuse the
preceding mutant's Python bytecode. The repaired runner removes the affected
module's cached bytecode before each mutation and after byte-exact restoration.
All inherited controls are retained/reanchored, including replacing the removed
interim-refusal control with a real silently-empty document read. The complete
repaired matrix has 62 inherited controls, 14 document controls and the new
stored-version privacy control, all caught. Source/test hashes after restoration
match the live frozen files; no live repository source was mutated by the
matrix.

Worker logs: `/private/tmp/pipy-de03c2-logs/repair-r1-focused.log`,
`final-document-repeats.log`, `final-documents-repeat-01.log` through
`final-documents-repeat-20.log`, `final-document-repeats-summary.json`,
`repair-r1-matrix.log`, `repair-r1-matrix-control.log`,
`repair-r1-inventory.log`, `repair-r1-coverage.log`, `repair-r1-ruff-final.log`,
`repair-r1-format.log`, `repair-r1-mypy-final.log`, `repair-r1-closure.log` and
`repair-r1-binding.log`. Matrix details and restoration proof:
`/private/tmp/pipy-de03c2-matrix/results.json` and
`/private/tmp/pipy-de03c2-matrix/final-restored-sha256.json`; the initial cache
attempt remains in `matrix-attempt1.log`.

**Completed coordinator checks on the repaired freeze.** Local scratch Python
3.11.13 / SQLite 3.49.1: **1363 passed**, exit 0, 117.27 seconds. Python
3.12.15 / SQLite 3.53.1: **1363 passed**, exit 0, 129.28 seconds. Both run under
`-X dev -W error`, without installing dependencies; logs are under
`/private/tmp/pipy-de03c2-gates.UhFxpa/r2/`.

The coordinator's full `just check` returned **exit 1**, **15,460 passed,
2 skipped, 2 failed**, in 1590.96 seconds. The two failures are precisely the
known load-dependent docs-preview cases
`test_preview_mirrors_changes_reloads_config_and_cleans_up_child[2]` (SIGINT)
and `[15]` (SIGTERM). An isolated `tests/test_docs_build.py` rerun passed
**16/16**, exit 0, 17.10 seconds. This records the failure honestly; it does not
claim a green full check. The skip audit passed **97 tests, 2 skipped**, in
12.44 seconds: `PIPY_CODEMODE_REQUIRE_RUNTIME` was unset, and this filesystem is
case-insensitive. The initial strict docs build passed and its rendered link
check found **1065 links, zero broken**; final docs validation still runs after
this closing documentation update.

Linux macmini's repaired broad five-module suite passed **1363 tests**, exit 0,
in **473.25 seconds**. Its final twenty document-selected repetitions are still
running at this entry (at least nine have completed cleanly); **no completed
20-run Linux result is claimed**. The coordinator will record the remaining
Linux repetitions, quiet full recheck, final docs gates, independent review and
verified delivery separately. No GitHub CI result is claimed.


### DE03c2 delivery and DE06a integration admission

DE03c2 landed on main and origin as
`a2c4c6d2595e7c782d139b0380f918ac24887efd`; the coordinator verified the remote
SHA (`/private/tmp/pipy-de03c2-gates.UhFxpa/delivery.json` and `push-final.log`).
Its exact committed tree is `39cad2419d8d4e5d1abffd4187eec9ad6ccffef9`.
DE03c2 and cumulative DE03c are now LANDED; DE03 stays IN_PROGRESS and DE03d
stays OPEN. All 43 P rows remain OPEN.

Codex implemented and repaired c2. Independent Claude Code `claude-opus-5-5`
HIGH, through the installed harness: round 1 ISSUES, one Warning and four
Suggestions, all repaired; rounds 2 and 3 CLEAN but with scope-filter
false positives caused by a dummy constant, followed by alpha-renames. The
final round 4 was **unscoped CLEAN**, 52.737 seconds, baseline
`fb40f81a1cef687c515cf3bcb547d82ead5de85f`, on the exact committed tree, with
no omissions, denied or forbidden tools, and the throwaway copy removed.
Production behavior did not change after the round-1 repair. The final document
test hash is `9e09100ab01a1c0e93bbc7828dab35c708f29ce5a0fe9cf11d77ee995f032f01`.
Earlier GPT/Claude implementer and reviewer identities remain historical.

Final local c2 proof: 975 focused tests; twenty runs of 353 document-selected
cases (479.25 seconds total); 77/77 source mutants detected and 1058 scratch
control tests; Python 3.11 and 3.12 each 1363 passed. Linux: 1363 passed in
473.25 seconds and twenty runs of 353 in 2471.83 seconds, followed by the final
renamed selection (76 passed, 40.38 seconds) and final privacy-tag selection
(9 passed, 5.35 seconds). No process remained naming the Linux directory;
exactly the current directory remained and only the previous c1 directory was
removed. The final strict docs build passed in 10.36 seconds; rendered links:
1067 checked, zero broken. All five final closure validators passed.

The first full `just check` was **exit 1**, 15,460 passed, 2 skipped and 2 known
preview failures in 1590.96 seconds; the quieter rerun was **exit 1**, 15,461
passed, 2 skipped and one failure in 1491.35 seconds, solely the same unchanged
preview edit/reload timeout `[2]`. Earlier isolated normal docs tests passed
16/16 in 17.10 seconds; the latest normal rerun was 15 passed, one failure
in 63.52 seconds on `[2]` adding a page. An additional `-X dev -W error` run
was 14 passed, two failures and one teardown error in 36.96 seconds, with
HTTP 404/deallocator ResourceWarnings in the unchanged docs helper. System load
was 14.08/21.20/17.35. The owner explicitly allowed the documented known-flake
exception; **this is not a green full-check claim**. The skips were the unset
optional codemode-runtime requirement and a case-insensitive filesystem. No
GitHub CI result is claimed. Coordinator gate logs are under
`/private/tmp/pipy-de03c2-gates.UhFxpa/r2/`; earlier pending entries above are
superseded only by the explicit completed results in this entry.

DE06a's additive worker tree was preserved as
`refs/pipy/baselines/durable-de06a-worker-20261022`, commit
`5561f2a9f4d3d5b77394953a8362ad27e5122d59`. Its unchanged codec/source tests are
now integrated over delivered c2, with c2 state/docs retained. DE06a and parent
DE06 are IN_PROGRESS; DE06b is OPEN. This admission does not claim a codec
CLEAN, full gate, fresh reference chain or delivery.

## DE06 pre-runtime codec split

Before implementation, DE06 is split into additive DE06a (DE02a1 and existing
canonical DTOs only) and DE06b (DE05 + DE06a, actual runtime checkpoint/envelope
integration). The cumulatively incomplete parent retains every original DE06/P15/P16/P19 criterion and
all 43 P rows remain OPEN. Codex implements this new slice; independent review
is Claude Code `claude-opus-5-5` HIGH through `claude-review-loop`. Earlier GPT
and Claude implementation provenance is unchanged. An isolated worktree is not
delivery: integrated main still requires local checks, exact-tree CLEAN and a
verified normal push.

### DE06a isolated implementation evidence (not delivered)

Codex implemented additive `native/durable/canonical_codec.py` and
`tests/durable/test_canonical_codec.py`. The explicit version-1 field schema
covers every supported message/request/provider/tool outcome field, including
compare=False metadata. Requests freeze through the canonical request boundary;
live header callbacks and nondefault registered tool metadata refuse explicitly.
The [codec contract](../durable-canonical-codec.md) describes these boundaries,
finite value fidelity, surrogate spelling transport and full-content privacy.
No existing canonical DTO, durable owner/Session/storage module or runtime path
changed. DE06b waits for actual DE05 task shapes and owns integration.

Local worker checks on macOS, Python 3.15.0: 140 codec tests passed; 20 of 20
sequential `-X dev -W error` repeats passed (140 each); the codec also passed
on bare scratch Python 3.11.13 and 3.12.15 environments reusing installed pure
Python dependencies, with no install. Before the final malformed-tree additions,
codec/coverage/package AST inventory passed 570 tests under `-X dev -W error`;
the final codec/coverage/acceptance set passed 306. The final hostile-metaclass
regressions pin top-level ingress and nested system sections; both predicates
use exact identity checks. Ruff and mypy on the new source and tests passed. Seven actual source mutants each failed their designated
conformance tests: lost thinking metadata, stripped raw whitespace, absent
structured content becoming null, signed zero lost, newer version accepted,
shared arena reference accepted and live callback silently discarded.

Logs are `/private/tmp/pipy-de06a-tests-j6VmRp/`: `focused-expanded.log`,
`focused-inventory.log`, `cascade-tests.log`, `repeat-01.log` through
`repeat-20.log`, `compat-3.11.13.log`, `compat-3.12.15.log`, `mutants-results.json`
and `mutants/*/run.log`. The branch cascade runs baseline → a1 → a2b → b1b → b3
and all five configuration validators pass (`closure.log`). Its changed
acceptance script and coverage test are chain-bound source changes; no old
chain is called fresh. The coordinator will reconcile shared files and run both
fresh reference chains on integrated main. Branch docs build/rendered links
were deferred to integrated main by the coordinator to avoid concurrent load
on DE03c2. Full `just check`, independent Opus 5.5 HIGH review, commit and verified push
remain required before delivery. No GitHub CI result is claimed; all P rows stay
OPEN, and no different-family CLEAN has yet been obtained for this slice.


### DE06a integrated-main source freeze — independent gates pending

The codec and its field tests were copied unchanged from the protected worker
tree onto delivered c2. Main source SHA256:
`370e4b9ddc2fba93364a29eb23d371ca5eb33331397c03623b416662777cef76`;
test SHA256:
`29379d4ba69b7c81832cc76e4e26e11b81013616b35cadc9c7b4bc5976a7a2b4`.
Existing canonical DTOs, Session/Storage/SQLite source and c2 tests are unchanged.
Shared documentation retains c2 delivery and its exact known-flake exception;
coverage has 115 rows with c2/DE03c LANDED, DE03 IN_PROGRESS, DE06a/DE06
IN_PROGRESS, DE06b OPEN and all 43 P rows OPEN. DE03c's status tests now pin
its delivered-parent behavior; DE03 and DE06 retain incomplete-parent checks.

On integrated main, `-X dev -W error` codec/coverage/acceptance tests passed
**307 tests**, exit 0, 6.34 seconds; extension-generation and package architecture
inventory passed **348**, exit 0, 18.69 seconds. Repository-wide Ruff check,
Ruff format check (973 files) and `mypy src tests` (906 source files) passed;
existing mypy annotation notes are unchanged. Pre-cascade integration attempts
failed on status-test reconciliation and stale comparison/verification hashes;
the status assertions and final cascade corrected these before the successful
307-test run. No codec production or codec test change was needed.

The final cascade baseline → a1 → a2b → b1b → b3 and all five configuration
validators passed. Exactly the three existing chain-bound inputs named above
changed; root will run both fresh reference chains on this integrated tree.
The worker did not run reference chains, full `just check`, docs build/rendered
links, independent review, commit or push. Those are still pending root gates;
no CLEAN or DE06a delivery is claimed. Main integration logs and hashes are in
`/private/tmp/pipy-de06a-main-integration-AlQxIf/`: `focused-postcascade.log`,
`inventory.log`, `ruff.log`, `format.log`, `mypy.log`, `cascade.log`,
`closure.log`, `final-inventory.json` and `binding-classification.json`.


### DE06a round-1 review repairs — final gates pending

Independent Claude Code `claude-opus-5-5` HIGH through the installed harness
reviewed baseline `4f4bf05289996ac086eb9bf80c7256d3430802cd`, tree
`5f984cf290a3e9f85d0b752d3cf8e02fe1b327ee`: **ISSUES**, two Warnings and
three Suggestions, with no omissions. The coordinator accepted all five;
Codex repaired their code/test/documentation boundaries. This entry does not
claim a follow-up CLEAN or delivery.

Current coverage summaries for DE03, DE03c and DE03c2 are rewritten completely:
c2/DE03c LANDED, DE03 IN_PROGRESS and DE03d OPEN. The current next-delivery gate
now names the DE06a lane and Claude Code Opus 5.5 HIGH reviewer. Dated historical
results and earlier reviewer identities are retained. The spec/reference link
to backlog/evidence for mutable status and gates; the topic document states
stable format and cumulative completion contracts.

The reviewed missing-field negative control reproduced the gap: the exact
reviewed 140-case scratch control passed, and a real source mutant relaxing
`_construct` to allow a subset of fields also passed all 140. Repaired tests
remove each field's matching edge and complete unreachable subtree, compact
all references and independently prove the arena remains a complete forward
tree. A constructor-entry witness proves decode reaches the intended missing
field at the DTO schema boundary. Every serialized field of every registered
DTO is covered, including defaulted compare=False fields. Against the repaired
tests, that same permissive-schema mutant fails 70 default-field cases (47
required-field cases still refuse through normal constructor validation).
Unknown-field and malformed-arena controls remain separate.

Datetime decode now requires the parsed timestamp to reproduce its stored
ISO spelling; path decode similarly requires the platform Path to reproduce
its stored spelling. Basic/date-only/Z/space timestamp variants and normalized
path spellings are refused. Encoder-produced timestamps, fixed-offset names,
fold, paths and surrogate spelling still round-trip. Eight new strict-spelling
cases fail on the reviewed source and pass after repair. The detachment test
now changes a real reachable leaf, proves prior decoded outputs unchanged and
proves a subsequent decode observes that changed value.

The repaired codec suite passed **271 cases**. A new isolated scratch control
also passed 271 before the mutant matrix. All **10** actual source mutants
failed their designated tests: the seven inherited controls, missing default
fields and both datetime/path canonicality guards. Twenty final sequential
`-X dev -W error` codec runs passed 271 each; scratch Python 3.11.13 and 3.12.15
also passed 271 each without installing dependencies. A reused helper's first
repeat invocation selected the preserved old worker checkout; those old
140-case results are discarded. The corrected helper asserts main's checkout
and writes the final 271-case evidence under `finalruns/`; no preserved worker
tracked source/test file changed.

Repair logs are `/private/tmp/pipy-de06a-r1-repair-NYreT5/`:
`reviewed-reproduction.json`, `reviewed-control/run.log`,
`reviewed-permissive-missing/run.log`, `new-focused.log`,
`final-scratch-control/run.log`, `reviewed-canonical-spellings/run.log`,
`matrix.log`, `matrix-results.json`, `mutant-*/run.log`,
`repeat-compat-final-summary.log` and `finalruns/` (twenty repetitions and both
compatibility logs). Root will rerun both fresh chains after the final cascade
because coverage changed; the earlier passed chains are not rebound or called
final. Independent delta review, broad local gates, commit and verified push
remain pending, with every P row OPEN.


The repaired integrated codec/coverage/acceptance set passed **440 tests**,
exit 0, 7.41 seconds; final extension-generation and package architecture
inventory passed **348**, exit 0, 19.94 seconds. Repository-wide Ruff check,
Ruff format check (973 files) and mypy (906 source files) passed. The final
baseline → a1 → a2b → b1b → b3 cascade and all five configuration validators
passed before the final focused run. Logs in the repair directory are
`combined.log`, `inventory.log`, `ruff.log`, `format.log`, `mypy.log`,
`cascade.log`, `closure.log` and `final-inventory.json`. This completes worker
repair validation; the independent review/full/documentation/reference gates
still belong to the coordinator.


### DE03c2 delivery and DE03d pre-code split

DE03c2 delivered as `a2c4c6d2595e7c782d139b0380f918ac24887efd`, tree
`39cad2419d8d4e5d1abffd4187eec9ad6ccffef9`, exactly the final independent
Claude Code `claude-opus-5-5` HIGH R4 CLEAN, unscoped. Codex implemented and
repaired; guard, normal push and `git ls-remote` confirmed the commit. R1
ISSUES (one Warning/four Suggestions) were repaired; R2/R3 CLEAN were scoped
by an innocent synthetic constant classified as secret; R4's final equal-length
`_PRIVATE_TAG` rename produced no redactions and passed format and privacy tests.
Earlier identities and outcomes remain historical.

The final quiet `just check` was exit 1, 15,461 passed, 2 skipped, one failure
in the known unchanged docs-preview `[2]` case, 1491.35 seconds. The final
standard isolated docs file had 15 passed/one same preview failure, 63.52 seconds;
an earlier standard isolated run passed 16/16, 17.10 seconds. The stronger
`-X dev -W error` isolated attempt had 14 passed/two failures/one teardown error,
36.96 seconds, including Python 3.15 HTTPError temporary-file ResourceWarnings.
Load was above the owner's known flake threshold; Root applied the explicit
known-flake exception, recorded honestly rather than claiming a green full
check or modifying unrelated docs-build code. Linux final broad1363 passed,
20/20 document repeats353 passed/622 deselected (2471.83 seconds aggregate),
final naming-only file76 passed and final TAG privacy9 passed. All other local,
platform, docs, link, closure and independent gates passed; no CI claim.

DE03d is split before implementation into sequential d1 and d2. d1 owns native
Session/SQLite commit, acknowledgement/error/close evidence and a bounded
current-base workload at upstream timing scale. d2 owns the actual paired Pi
SQLite/SQLite-reopen/Memory oracle, all valid D2(a)–(f) probes, mutants, two fresh
chains and final cumulative DE03 gate. DE03d derives d1+d2 before DE03 derives
a+b+c+d. DE04 still depends on DE03, and all 43 P rows remain OPEN. Neither a
native workload nor a source-only child can claim paired Pi execution or close
P43. Production delta/as-of/copy/history capabilities remain DE27's boundary.


### DE03d1 native implementation — independent gates pending

Codex implements in its isolated `codex/durable-de03d1` checkout, based on
`a2c4c6d2`; main's independent DE06 lane is not touched. A worktree-local venv
uses existing dependency files without installing anything; isolated `-I`
imports resolve this checkout's `src/pipy_harness/native/durable/sqlite.py`.
No production capability or Session/Storage implementation change was needed.

`tests/durable/test_session_sqlite.py` covers both existing commit facades over
real SQLite: mixed conversation/entry/task/submission/raw-base commits,
backend-return-before-publication and adopted document identity, detached data,
fresh Session reopen and counter continuation; thrown acknowledgement after
actual COMMIT, no false publication/adoption or retry, failed Session and
recovered durable batch; actual query_only/FULL page-limit/foreign writer lock
failures, Session poisoning and no partial reopened state; normal/failing
checkpoint close releasing ownership to a bounded fresh child; held real commit
and close/drain interleavings. The held-thread scenarios run in separately
bounded/reaped processes, so even a deadlocked non-daemon thread cannot escape
the test's watchdog. Events and locked owner-state observations establish order.

`scripts/durable_sqlite_workload.py` and its closed input/validator tests add
only the current-base timing subset documented in the reference guide. Seed
scale 1000/300/300, batches 100, three read rounds and three samples of each
of the three upstream write shapes; complete persisted readbacks, commit
sequences, ID continuation, page/filter/address/request indexes, clean reopen
and actual SIGKILL followed by recovery in a third bounded child. Configuration
inspection and integrity checking use the existing owned raw connection;
all workload record reads/writes use public Storage. Runtime/script/input/
artifact identities and process-group cleanup are validated. Native results
never claim Pi execution or close a P row.

Initial macOS end-to-end execution exited 0: clean seed 0.155 s, selected reads
0.020 s, reopen 0.0009 s, database 716800 bytes; unclean seed 0.168 s, selected
reads 0.028 s, recovery/open 0.0069 s, database 634880 bytes, committed WAL
1260752 bytes. These are initial observations, not final cross-platform gates.
The initial combined native/validator suite passed 52 cases in 8.87 seconds;
final repetitions, negative controls, platform/full/docs checks and independent
Opus5.5 HIGH review are recorded separately when completed. Missing startup
readiness exposed a test-runner cleanup issue: macOS can refuse killpg on an
already-zombie child; cleanup now tolerates that result and still reaps/closes
all pipes, matching the existing bounded Child helper. No production defect
was inferred from that harness issue.

The exact d1/d2 IDs, plan/ledger regexes, parent derivation order, status rows,
source anchors and native-only evidence ownership are synchronized before d1
implementation. This branch has 115 coverage rows; the parallel codec lane's
children must be reconciled at integration rather than copied blindly. The
changed acceptance executable requires fresh integrated reference chains before
delivery; Root owns that gate. DE03d/DE03 and all 43 P rows remain OPEN or
IN_PROGRESS as appropriate, with no native/P43 or paired-oracle closure claim.


### DE03d1 worker validation — native scope, coordinator gates pending

The final combined Session/SQLite and workload-validator suite passed 60 cases
under `-X dev -W error` (13.18 seconds); the unchanged 16 Session cases passed
20/20 sequential runs. Five independent source controls were caught: missing
adoption, false publication after lost acknowledgement, omitted failure latch,
retained ownership after close failure, and close before the active line drains.
The initial close control targeted a redundant guard and survived; it was
replaced with the actual line-drain guard and caught, not silently counted as a
pass. Scratch mutations were restored byte-exactly, with modified-module caches
removed. Logs: `/private/tmp/pipy-de03d1-session-final-repeats.log` and
`/private/tmp/pipy-de03d1-native-controls-final.log`; per-control artifacts are
under `/private/tmp/pipy-de03d1-controls/`.

A final validator audit reproduced an acceptance bug: Python equality treated a
persisted JSON `false` as expected integer `0`, a float `0.0` as integer `0`,
and a boolean return count as integer `1`. This was a new evidence-validator
bug, not a Storage defect. Persisted trees, read shapes and return values now
compare through OwnedJson, retaining object order and exact JSON scalar types.
Each of the three actual regression tests fails the old validator and passes
the repair; unmodified real clean and SIGKILL/recovered evidence passes too.
Actual CLI controls rehash the changed evidence before verifying, proving exit
1 for these well-formed behavioral mismatches rather than merely tripping an
artifact hash. Closed-input identity/configuration failures remain exit 2.
The old/new proof is `/private/tmp/pipy-de03d1-owned-regression.log`.

The final native CLI run and independent verify exited 0 at
`/private/tmp/pipy-de03d1-owned-final/`; clean seed/read/reopen were
0.166/0.021/0.0009 seconds. The manifest binds the closed input, script, runtime
closure, persisted databases and reports, including original SIGKILL evidence
and separately reaped recovery. All observations are native macOS evidence;
Linux, compatibility, final full/docs/link gates, fresh integrated reference
chains and the independent Opus5.5 HIGH gate belong to the coordinator and are
not claimed here. Package architecture/inventory passed 348 cases (19.69 s),
Ruff and format passed (974 files), and mypy passed 907 source files.
The changed acceptance executable still requires fresh integrated chains;
manifest closure verification alone does not replace those executions.
DE03d1 remains IN_PROGRESS, DE03d2 OPEN, and every P row remains OPEN.

### DE03d1 R1 repair — native observations and fail-closed evidence

Independent Claude Code `claude-opus-5-5`, effort HIGH, returned ISSUES with one
Warning and five Suggestions at baseline
`db3c1bbd3a52d7aa0d4df4972b1bb30328c4ece1`. All six were accepted as coupled
validator, process lifetime, configuration and observation repairs. No final
CLEAN or delivery is claimed here; the coordinator owns the integration gate.

Malformed manifests now pass exact object/map admission before use; an
unexpected Exception becomes invalid evidence (exit 2), while Mismatch is
handled first as exit 1 and BaseException interruption still propagates.
Actual CLI regressions cover the same-names artifact-list exploit and deeply
nested JSON, rehashed boolean/float metadata, counters, sample sequences,
PRAGMAs and all three process records. Product JSON value mismatches retain
exit 1. Exact integer admission applies uniformly to metadata counts, IDs,
commit sequences and process codes; recovery receives the same full event,
signal, stdout/stderr and reaping checks as the other children.

The native Storage contract returns a Seq; actual returned sequences are now
captured in sample observations, checked with exact integer type and compared
with commit/readback sequence evidence. `submitted_count` explicitly names the
length of the supplied batch. Before/after counts come from bounded public
Storage scans, not the workload's expected dataset. Clean WAL bytes are measured
before closing, and a separate after-close measurement is retained. The final
native CLI run and verify both exited 0 at
`/private/tmp/pipy-de03d1-r1-native-final/`: clean seed/read/reopen
0.188/0.021/0.0012 seconds, database 716800 bytes and pre-close WAL 1796352
bytes; unclean 0.183/0.023/0.0063 seconds, database 634880 bytes and WAL 1260752
bytes. Both after-close WAL measurements were zero. These supersede earlier
hard-coded clean-WAL reports as measurement evidence.

Owned child targets have an explicit no-descendant contract. Exceptional paths
signal the group while its leader is unreaped, then communicate/reap and close
pipes. Normal completion never signals or probes the reusable PGID after
communicate. Actual normal, startup-failure and readiness/exit-timeout cases
assert this order; 20/20 repetitions passed, three cases each (60.33 seconds
aggregate). This bounded lifetime does not claim general descendant supervision.
Loaded configuration drives seeding counts/batches, read rounds/pages, samples,
validation expectations and deadlines; a smaller private configuration proves
that changing those values changes actual work without opening arbitrary CLI
inputs. The closed public fixture remains 1000/300/300, batch100,3/3.

Final focused tests: 79 passed in 24.18 seconds under `-X dev -W error`;
`/private/tmp/pipy-de03d1-r1-final-focus.log`. The unchanged 16 Session cases
retain their completed 20/20 proof; all five original source controls were
rerun and caught. Seven additional source controls exercise malformed-manifest
refusal, numeric admission, process-code admission, post-reap signaling,
tautological counts, unconsumed input and coerced actual commit sequences.
All seven were caught; the byte-exact restored real workload-validator suite
passed 63 cases in 19.29 seconds. Logs:
`/private/tmp/pipy-de03d1-r1-controls.log`, designated mutant logs and restored
baseline under `/private/tmp/pipy-de03d1-r1-validator-controls/`, and original
controls `/private/tmp/pipy-de03d1-r1-native-controls.log`. Ruff/format passed
974 files and mypy passed 907 source files. No production source was changed.
The stale current DE03c2 pending-review clause was removed; chronological
historical entries remain intact. Platform/full/docs/fresh-integrated-chain
and final independent gates remain coordinator work; all P rows remain OPEN.


### DE06a delivery and DE03d1 repaired integration (2026-10-22)

DE06a was delivered on main and origin at
`caf4d34e3c8fbbdc8f97b4b5e15435640897fb29`; reviewed tree
`9086b2bae12178aca85ccc01a11743c1c5ec8e0a` matched the final commit tree.
Codex implemented and repaired; independent Claude Code `claude-opus-5-5`,
effort HIGH, R1 had two Warnings and three Suggestions, all repaired, and
R2 was CLEAN unscoped. Local `just check` exited 0: 15737 passed, two skipped,
1544.97 seconds, including all16 docs-preview tests. Focused440 and inventory348
passed; 20x271 repeats and 10/10 source mutants passed; Python3.11 and3.12
coordinator runs each passed271. Strict docs build took7.89 seconds and977
rendered links had zero broken. Two final fresh reference chains under
`/private/tmp/pipy-de06a-gates.5b_mxbq1/` (WItfNX campaign) passed all ten
unique/current-manifest and parent bindings. Normal push and `git ls-remote`
confirmed the delivered SHA. No GitHub CI result is claimed. DE06 remains
IN_PROGRESS, DE06b OPEN, and all43 P rows OPEN.

The coordinator protected D1's repaired isolated tree at
`5f77d97fcb9668b5eec6eca3f8e1c467e0f88161` before integration. Its four new
mechanism/test/input files are copied byte-exactly; shared metadata merges retain
the delivered codec, fixed c2 summaries and stable backlog/evidence links.
The combined inventory has117 rows: both DE06a/b and DE03d1/d2. D1 R1
ISSUES (one Warning/five Suggestions) repairs are implemented, with final79
native focused cases; final independent integrated R2/full/docs/platform/fresh
chain gates remain pending. Prior platform60-case runs are historical evidence
of the earlier script revision, not final79-case claims. Session16 source is
unchanged, so its original20x proof remains applicable. No native production
source or delivered C2 tests are changed by this integration.

Integrated D1 pre-gate validation on main over delivered `caf4d34e`: 79 native
Session/workload cases passed (22.74 seconds), package architecture/inventory348
passed (19.57 seconds), combined coverage/acceptance173 passed (6.61 seconds),
Ruff and format976 passed, and mypy909 source files passed. All four new files
match the protected repaired-tree hashes byte-exactly; all native production
sources, canonical codec implementation/tests/docs and delivered C2 document
tests remain unchanged from the delivered main baseline. The 117-row plan/index
joins and every plan anchor were reconciled before cascading the five existing
manifests; closure configuration and every pinned hash verified with zero stale
entries. These are local integration checks, not the final independent review,
Linux/compatibility refresh, full/docs gates or required fresh reference chains.
Logs use `/private/tmp/pipy-de03d1-integrated-` prefixes; no delivery is claimed.


### DE03d1 relative CLI path repair during coordinator gates

The coordinator's Linux run of the frozen integrated revision passed79 focused
cases (64.96 seconds), then an actual `run --output workload-evidence-r2`
invocation exposed a supported relative-path defect: launch changed cwd to the
child folder while also passing the relative folder as the child's root. The
child resolved it a second time and the parent found no database at the intended
output location. This is a coordinator-discovered CLI defect, not an independent
review result, and the earlier79 tests do not prove the repaired script.

The output root is now resolved once before mkdir or any child launch. The
existing absent-output rule remains unchanged. An actual subprocess regression
runs with a relative output from a private cwd, verifies using that relative
path, checks both intended database locations and rejects a second run into the
same output without losing its evidence. Source/test identity and final platform,
full, docs and independent exact-tree gates must be refreshed after this repair;
the interrupted full check (exit130) is not a passing result. No production or
codec source changed, and no delivery or P closure is claimed.

The repaired local suite passed80 cases under `-X dev -W error` (27.95 s),
including the relative run/verify/repeated-output refusal regression. That exact
new regression fails the scratch old code and passes its byte-exact restored
repair; `/private/tmp/pipy-de03d1-relative-control.log`. Ruff check/format and
mypy909 passed. Independent Opus5.5 HIGH R2 subsequently returned CLEAN with
zero items at baseline `20256d91aeb8ab6e2756d43ce06243fe72af595f`, covering the
accepted R1 repairs and main integration before this relative-path correction.
The actual newly discovered CLI defect justifies a small R3 delta review of
this repair; R2 is not being relabeled as covering the later tree. Coordinator
platform/full/docs refresh and final exact-tree delivery remain pending.


### DE03d1 R3 symlink refusal repair

Independent Opus5.5 HIGH R3 returned ISSUES with one Warning at baseline
`bf0e67bfed06185d1f282b400cd70f116b0eb477`: the relative-path repair's
`resolve()` followed an existing dangling output symlink and created its target,
regressing the absent-output refusal contract. This first self-inflicted
correctness regression is accepted. Anchoring with `absolute()` preserves the
caller cwd without following the output symlink. An actual CLI regression checks
exit2, retained symlink and absent target; the ordinary relative-output run and
verify remain covered. The interrupted R3 full check is not a passing result;
prior platform observations remain historical until the final refresh. R4 is a
small delta review of this correction, not a reopened audit. No production or
codec source changes and no delivery claim.

Local final focused81 passed in28.24 seconds under `-X dev -W error`;
`/private/tmp/pipy-de03d1-symlink-focus.log`. The new symlink regression fails
the prior `resolve()` code in scratch; the byte-exact restored repair passes
both symlink refusal and ordinary relative run/verify controls
(`/private/tmp/pipy-de03d1-symlink-control.log`). Ruff check/format and mypy909
passed; existing manifest closure remains current without a cascade. These
local checks do not replace R4 or the coordinator's final platform/full gates.


### DE03d1 delivery and DE03d2 implementation admission (2026-10-22)

D1 delivered on main and origin at `8ab38d2010ea8e7f8660124085b04b6f0e821d94`,
exact reviewed tree `5113e7c423cb0b35a89b99e12bef783dba954ec3`. Codex implemented
and repaired; independent Claude Code `claude-opus-5-5` HIGH final R4 CLEAN.
Normal push guard and remote SHA verification passed. Final full `just check`
exited1 with15821 passed, two skips and one known unchanged `docs-preview[15]`
failure (1667.82s); isolated16 passed22.06s. The owner's explicit known-flake
exception applies; this is not a green full check or a CI result. Final local,
Python3.11/3.12 and Linux81 focused cases,20x2 CLI regressions, docs1109 rendered
links and final fresh chains passed. Full proof:
`/private/tmp/pipy-de03d1-gates.ng4jsu9e/final-gate-proof.json`.

D2 now implements the closed paired SQLite/SQLite-reopen/Memory comparison,
using the existing11/128 scripted recipes and21 exported conformance cases plus
23 new valid D2(a)–(f) recipes/130 operations. Preliminary reused-parent Pi
observations are preparation only, not the final gate. Recipe-local integral
limit ingress mirrors the existing cursor role adapter without changing payload
or raw-version signed zero. Exact raw witnesses remain beside closed scoped
projections and divergence declarations. Native SQLite and reopen must match
Memory semantics where Pi SQLite differs. D2/d/DE03 remain IN_PROGRESS and all43
P rows OPEN; DE04 still waits for cumulative DE03 completion.


### DE03d2 paired implementation and approved lifetime boundary

Initial executable integration passed the new three-backend Pi driver and all
21 exported cases per backend, separately from34/258 native SQLite/reopen
scripted operations. The development parent was reused, so this is not a fresh
bootstrap or delivery gate. Initial real native comparison65 cases passed36.92s
before later refusal controls were added; final focused/platform/fresh-chain
and independent gates are still pending.

The complete existing document-atomic recipe exposed exactly one lifetime
boundary: after an unused mint and a rejected mixed commit, live Memory and
SQLite return7 while Pi/native SQLite-reopen return6. The existing store
contract already says unused minted IDs are not stored and may be minted again
after reopen. The coordinator confirmed that authority; no product storage
change is made. A separate exact witness binds all five backend values at this
one operation, and forcing native-reopen7 is a valid mismatch1. All other126
closed Pi SQLite divergence pointers require native Memory semantics.64 exact
error-spelling projections retain raw error/code/data witnesses; broad numeric
or exception normalization is refused. Closed fixture observations are unit
fixtures, not renamed fresh executions. Delivery still requires two actual
fresh complete six-stage chains on the final closure. All P rows remain OPEN.

### DE03d2 final worker checks — coordinator gates pending

The final private native reopen wrapper delegates every actual commit through
close/open in finally, including the nested ownership/document-ownership
helpers. A pre-freeze adapter audit found that an earlier outer-step wrapper
missed those helper commits; that earlier adapter's focused/repetition logs are
historical only. An actual open-count regression and a source mutant bypassing
only helper reopens now discriminate this mechanism even when record outputs
are unchanged. No production source changed.

Final focused comparator/coverage/acceptance256 passed58.40s:83 comparator cases
plus173 coverage/acceptance cases. Ruff check and format978 passed; mypy910
passed. Package architecture/inventory348 passed20.32s. Final warnings-error/
development repetitions ran20/20 rounds of11 cases (72 deselected),231.45s
aggregate, on byte-identical comparator/test/input sources; each round includes
real native SQLite/reopen scenarios and adapter/helper open counts. Seven source
controls were caught, including missing integral-role conversion, omitted exact
divergence gate, broad error projection, post-reap signaling, missing mismatch
exit, unexpected exception escape and skipped helper reopen. Byte-exact restored
control83 passed47.32s. All21 schema-valid behavior mutants were freshly run
through actual CLI compare, positive0/negative1; invalid2 was not counted as
behavior detection.

Logs: `/private/tmp/pipy-de03d2-wrapper-final-focus.log`,
`/private/tmp/pipy-de03d2-wrapper-final-repeats.log`,
`/private/tmp/pipy-de03d2-wrapper-source-controls.log` and designated controls
under `/private/tmp/pipy-de03d2-wrapper-source-controls/`. Final actual CLI
artifacts are `/private/tmp/pipy-de03d2-final-cli-controls-0epmame_/`.
The paired development driver executed all34/258 scripted operations and21
exported cases on each of three Pi backends, with native SQLite/reopen compared
separately. The captured expected fixture is unit evidence from that reused
parent, not a fresh bootstrap. Final execution requires the coordinator's two
new complete six-stage chains, Linux/compatibility, full/docs/link gates and
independent Claude Code Opus5.5 HIGH review. D2/d/DE03 remain IN_PROGRESS and
all43 P rows OPEN; no delivery or CLEAN is claimed here.


### DE03d2 review-input representation repair — review still pending

The coordinator's initial frozen tree completed two fresh full six-stage chains
(`F763ts`, including raw and projected comparison0) and compatibility runs of164
cases on Python3.11/3.12 (80.01s/76.89s). These are pre-representation-repair
results. Strict docs build found the unescaped preview-case marker above.
The first Opus5.5 HIGH invocation performed no review: its actual provider result
was "Prompt is too long" from the inline3.2MB captured unit fixture, rather than
quota or model unavailability. No CLEAN or review findings resulted.

Only that test fixture is now represented as standard zlib+base64 in a closed
JSON wrapper, with a bounded test-only decoder. Its complete original3,204,104
bytes remain bound to SHA256
`fcd4caa2b7f8fd4ee032103994c6f0b1d1771167fea8774d41ea8d3e258f3642`.
Roundtrip and malformed/oversize/trailing-data/hash controls pin losslessness and
bounds. Production comparator, driver and actual fresh execution artifacts retain
their raw JSON protocol unchanged. The coordinator will rerun final closure,
fresh-chain and independent review gates on the repaired representation; all
current implementation and P statuses remain unchanged.


After this representation repair, focused comparator/coverage/acceptance264
passed51.25s (91 comparator,173 coverage/acceptance), including eight new
losslessness/decoder refusal cases. The same11 native lifecycle/role/error cases
passed20/20 fresh rounds (80 deselected),198.75s aggregate with source identity
checked before and after each round. Ruff check/format and mypy910 passed.
The comparator and Pi driver are byte-identical to the preceding worker freeze;
previous seven source-control detections and21 actual CLI behavior controls
remain applicable. The first local post-edit focused attempt reported84 passes
and the expected stale-closure failure before manifest regeneration; it is not
counted as a passing gate. Old five closures were verified unchanged, and the
new SQLite manifest is regenerated last after these evidence edits.

Logs: `/private/tmp/pipy-de03d2-compressed-focus-final.log`,
`/private/tmp/pipy-de03d2-compressed-repeats.log`,
`/private/tmp/pipy-de03d2-compressed-{ruff,format,mypy,closure}.log`.
Independent Opus5.5 HIGH review and coordinator final gates remain pending.


### DE03d2 first actual Opus review and accepted repairs

The first actual Claude Code Opus5.5 HIGH review (R1b, after the earlier prompt
size failure performed no review) returned ISSUES with two warnings and two
suggestions. All four were accepted. Comparison now requires raw Pi reference
profiles with exact observed error-spelling usage; projected Pi EXPECTED is
not CLI ingress. Native ACTUAL may use raw or already-projected spelling with
the same verdict through its conditional idempotent dictionary. Conditional native spelling entries may go unused, so a genuine
unexpected native error still yields mismatch1. A missing actual native cursor
now records a causal blocked continuation instead of inventing a call or
escaping as invalid2; the same run comparison boundary writes hash-bound raw
and projected mismatch reports. Real SQLite cursor-drop and raw-to-canonical
source controls discriminate these repairs. Documentation identifies the Pi
reopen leg as a driver-local reconstruction of the pinned test wrapper, using
call-time delegation and finally after every commit; dated D1/D2 evidence links
are synchronized. Final repair validation and independent delta review remain
pending. Existing root platform/chains results precede these repairs, and all
statuses remain117 rows with all43 P rows OPEN.


Accepted-repair validation:268 focused cases passed65.26s (95 comparator and173
coverage/acceptance). All20 rounds of the changed15-case selection passed
(80 deselected),368.19s aggregate; comparator/test/input identities were checked
before and after each round. Ten source mutants were caught at their designated
regressions, including canonical Pi drift, escaping missing-cursor observations
and losing the mismatch-report result; byte-exact restored95 passed56.35s.
Package architecture/inventory348 passed21.12s; Ruff check/format970 and mypy910
passed. Two initial focused attempts stopped after95 passes on the evidence-link
registry/order and then the D1 dated-heading suffix; both bookkeeping failures
were repaired before final268 success. They are not passing gates. The final
plan summary now records D1 and DE06a delivered while D2/d/DE03 remain in
progress, without changing historical delivery records or closing any P row.

Logs: `/private/tmp/pipy-de03d2-r2-focus-closed.log`,
`/private/tmp/pipy-de03d2-r2-repeats.log`,
`/private/tmp/pipy-de03d2-r2-source-controls.log` and its designated control
folder, `/private/tmp/pipy-de03d2-r2-inventory.log`, and
`/private/tmp/pipy-de03d2-r2-{ruff,format,mypy}-final.log`.
The six-manifest cascade is regenerated last after these edits; final coordinator
platform, fresh-chain, docs/full and Opus5.5 HIGH delta-review gates remain pending.


### DE03d2 second actual review — bounded wording correction

Claude Code Opus5.5 HIGH R2 returned ISSUES with one suggestion and no warnings.
The accepted correction narrows the raw-input wording to the Pi reference
EXPECTED side. Native ACTUAL accepts raw or already-projected spellings through
the conditional idempotent dictionary, with the same comparison verdict. No
code, test, type or runtime semantics changed; preceding implementation proofs
remain applicable. This prose/closure delta awaits independent review and final
coordinator gates; no CLEAN or delivery is claimed.

Focused metadata/config174 passed6.86s after this prose correction. All six
manifest bytes and bound source/test hashes remain unchanged; full closure
verification reports zero stale entries. Final review remains pending.

## DE32 pre-code scanner split

DE32a depends only on DE00 and owns the pure bounded streaming scanner and immutable seven-field result. DE32b depends on DE31 and DE32a and retains every original DE32 filesystem, reader, listing, watch, shell, cancellation, window/spill and cleanup acceptance, including scanner integration. DE32 is IN_PROGRESS and completes only when both children land. DE33 still depends on full DE32; DE34/35 are unchanged. All 43 P rows remain OPEN. Local implementation evidence and integration review are pending.

### DE32a local scanner evidence

The private scanner implements all seven raw-offset/decoded-size fields without
filesystem or runtime integration. The standalone Node driver imports the actual
pinned scanner/decoder, preloads the pinned resolver and refuses unexpected
loaded modules. A closed tiny seed profile generates 5000 upstream seeds and
30000 paired observations (random chunks plus1/2/3/7/65536); all seven fields
matched in the first actual run. Full inputs/results remain ignored private
artifacts, not oversized committed fixtures. Python independently checks each
field by whole-file slices. Exact ingress/lifecycle, malformed decoding and
file-leading-only BOM are covered by goldens; 32 MiB generated streams and a
single 8 MiB chunk enforce a 128 KiB traced-allocation ceiling without assembling
stream contents. Seven designated private source mutants are caught, and the
tracked source remains byte-identical after the probes.

Own scratch Python 3.11.13 and 3.12.15 each passed the focused scanner, comparator
and coverage tests under `-X dev -W error`, private TMPDIR and `--basetemp`.
The 117-test scanner/corpus subset passed 20/20 repeats on 3.11.13; the existing
session-generation and package-wide architecture inventories passed 348 tests.
Ruff and mypy include the new standalone comparator. Initial 3.15 focused tests
passed 218 but shared pytest garbage cleanup warned afterward; final proof uses
private temporary roots. No test is skipped. Exact logs, source hashes, final
compatibility counts, closure verification and limitations are in the ignored
worker handoff. Root still owns full `just check`, docs/rendered links, two
fresh integrated chains, Opus5.5 HIGH review, commit and push. DE32a and DE32
remain IN_PROGRESS here; DE32b and all 43 P rows remain OPEN.

Final Python 3.11.13 and 3.12.15 focused runs each passed 240 tests. Actual pinned Node comparisons on both interpreters each matched all 30000 observations and recorded imports from this worktree. Independent closure verification recomputed 110 local/parent hashes and passed all six actual configuration validators. These two minimal scanner snapshots are separate from Root's mandatory fresh full campaign chains.

## DE03d2 delivery and DE32a main integration (2026-10-22)

DE03d2 delivered on main and origin at `2dba600879a9f1f8ff4e9616360d9631dfc2f87e`.
The exact reviewed tree `a87dc715` received Opus5.5 HIGH R3 CLEAN (70.958s).
R1 ISSUES (two Warnings, two Suggestions) and R2 ISSUES (one wording Suggestion)
were repaired. Full check recorded15916 passed, two skipped and the known
`Preview[15]` ADDED_PUBLIC timeout (1754.81s; wrapper1785.51s); the owner accepted
that documented flake exception. Standard isolated preview rerun16 passed in
53.89s. Earlier EXTRA strict diagnostics (two failures,14passed and one resource
warning ERROR in36.54s) were not a pass. No green-full or CI outcome is claimed.

The D2 delivery proof retains focused268+metadata174,20x15 repeats, ten source
controls,348 inventory tests, Python3.11/3.12 each176 and Linux176 (247.50s) plus
20x15 repeats, all21 final hashes and process/artifact cleanup. Strict docs
passed7.98s and1125 rendered links across six pages had zero broken links. Two
fresh92HgTJ chains bind12IDs/six manifests/ten parent bindings, each D2 recursive
485 hashes,24 loaded modules and21 mutants. Exact proof and delivery records
remain `/private/tmp/pipy-de03d2-gates.fn0ewo7x/final-gate-proof.json` and
`delivery.json`. D1/C2/codec history, D1 evidence and D2's126 semantic pointers,
64-spelling dictionary and one-lifetime-reopen7/6 witness are unchanged.

DE32a integration preserves every additive scanner production/driver/input
byte from protected worker commit `311bce7303646b389da6fe167ae80a9d812c0567`
(tree `52a7f7d2f2ccccb5811a1e893cfa8d45965fc5cb`). Shared changes merge into the
D2-delivered tree, retaining its typed evidence registry and comparison repairs.
The scanner manifest is refreshed after the delivered SQLite manifest. Prior
branch30000-comparison Node proofs are historical-source-valid; Root will produce
current integrated bindings. Root identified the existing output-entry defect: resolving a dangling output symlink could create its absent target. The comparator now preserves the requested absolute entry and explicitly refuses existing entries/symlinks. Permanent CLI refusal and relative-output regressions plus a designated source control cover the repair; the comparator/test hashes intentionally differ from the branch. Product scanner bytes are unchanged, so prior scanner corpus/repeat/mutant proofs remain source-valid. Integrated gates remain pending.

### DE32a integrated local focus and output-entry repair

Main integration keeps the pure scanner, native scanner tests, Node driver and
closed input byte-identical to the protected worker. The comparator and its test
file intentionally change for Root's output-entry finding; the topic document
records the refusal contract. Actual CLI relative-output execution with pinned
Node matched all30000 observations on main. Actual CLI dangling-symlink refusal
leaves the target absent; the old-resolve source control fails by creating that
target. Its private source variant never replaces the tracked file. The two
changed output-entry regressions passed20/20 repeats under `-X dev -W error`.

Integrated scanner/E1/reference/acceptance/coverage focus passed523 tests; all95
D2 comparator regressions passed separately. Ruff lint/format and mypy include
the repaired comparator. No product scanner or D2 comparator/driver/recipe/test
bytes changed; original worker scanner corpus,117-test repeats and seven source
controls remain source-valid history. Coverage now contains119 IDs (the existing
117 plus DE32a/b), with actual registry-derived cardinality tests. DE03/d/d2 are
LANDED, DE06a LANDED/b OPEN/parent IN_PROGRESS, DE32a/parent IN_PROGRESS and b
OPEN; all43 P rows remain OPEN. Root's integrated full/docs/platform/fresh-chain
and Opus5.5 HIGH review gates remain pending. Exact logs, byte comparisons and
final closure/inventory proof are in the ignored main-integration handoff.

### DE32a Opus R1 findings and repair

Opus5.5 HIGH R1 took307.749s and returned ISSUES: one Warning and one Suggestion,
both accepted as material. The Warning identified relative-source resolution
against the child snapshot cwd and a missing checkout-root gate allowing a
subdirectory source to bypass the real root's output containment. The repair
resolves source once up front, requires `.git` at that root and passes the
absolute source to every git show call. Private fake-checkout negative tests
prove subdirectory/non-checkout/inside-root refusal before output creation; no
negative proof uses or writes inside actual upstream. A relative-source plus
relative-output valid CLI proof executes actual pinned Pi outside upstream.

The Suggestion identified silent duplicate-key acceptance in plain Node-response
json.loads. The repaired parser uses the existing duplicate-refusing pair hook
(and finite-constant hook). Duplicate observed/loaded and observation-field
keys now produce invalid2; existing shape/value mismatch semantics are unchanged.
Both defects have designated private source controls and focused regressions.
The previously completed Evidence literal backtick fix is included in the R2
delta. Product scanner/driver/input/native scanner tests remain unchanged, so
unchanged product repeats/mutants are not rerun. R1 platform/oracle results remain
historical until fresh immutable final bindings; R2 review and Root's full/docs/
platform/fresh-chain gates remain pending. No green-full or CI claim is made.

R1 repair local proof: comparator21 passed, combined scanner/coverage/acceptance
focus301 passed (9.52s), and the nine changed source/output/duplicate-key cases
passed20/20 repeats under `-X dev -W error` with private temporary roots. Two
private source controls failed exactly as intended, with tracked source bytes
unchanged. Actual pinned Node CLI with relative source and relative output
matched30000 observations. Final cascade and independent configuration/hash
proof bind the repaired source; Root owns R2 delta review and all final gates.



### DE04a implementation admission and DE03 delivery

DE03d2 delivered at `2dba600879a9f1f8ff4e9616360d9631dfc2f87e` with exact reviewed
tree `a87dc7155d23c366ca721c16a0e073ba4d2723d5`, final Claude Code Opus5.5 HIGH R3 CLEAN and confirmed remote.
The full check reported15916 passes, two skips and one known
`tests/test_docs_build.py::test_preview_mirrors_changes_reloads_config_and_cleans_up_child[15]`
`ADDED_PUBLIC_SENTINEL` preview-page timeout (1754.81s pytest,1785.51s wall);
standard isolated preview16 passed53.89s. This is the
explicit owner exception, not a green full check or CI result. Both fresh
complete chains `92HgTJ` attested12 execution IDs; Linux176 and20x15 passed.
Root proof: `/private/tmp/pipy-de03d2-gates.fn0ewo7x/final-gate-proof.json`.
DE03d/DE03 are cumulatively delivered. Earlier implementer/reviewer identities
remain historical and unchanged.

DE04 splits into a (typed current document acquisition, task lifetime and atomic
coupling) and b (real normalized operations/delta/checkpoint prerequisite and
rollback), preserving every original acceptance cell. DE04a is IN_PROGRESS, b
OPEN; DE04 waits both children and DE05 still waits full DE04. Full-base/value
suppression remains explicit in a. All43 P rows remain OPEN.

### DE04a typed current documents — implementation and focused proof

Codex implemented private immutable `Doc`/`DocFamily` definitions and replaced
`RawDoc` outright. Session, conversation and task singleton/family addresses use
one existing Session mutation authority. First seeds are copied before awaits;
same-transaction memo hits retain Pi's duplicate-admission cutoff. The loaded
member path deliberately keeps its owner-check bypass, while absent task
acquisition rechecks the latest candidate after await. Staged terminal tasks
reject acquisition before memo lookup. Terminal assembly retires every page of
known and unknown task documents, dedupes staged members and skips scans for new
terminal tasks. Coupled entries/tasks/document writes, publication ownership,
cache eviction and committed snapshot identity use the existing commit/adopt
boundary. No Storage schema or product default changed. Full bases/value-based
suppression remain explicit interim behavior; real operations, deltas and
checkpoint predicate rollback remain DE04b acceptance.

The paired adapter actually executed all24 async recipes against pinned Pi and
all24 against each native Memory/SQLite/SQLite-reopen leg (72 native profiles).
Comparison returned0 with11 exact declared error-spelling projections; error
codes and complete raw observations remain retained. Role-local absence/cursor
projections retain their raw undefined values. Reference checkpoint adaptation
produces full bases and does not claim operation/checkpoint parity. This was an
exploratory copy of the immutable historical `92HgTJ` parent, not a fresh current
acceptance gate. Its702 actual loaded modules comprise38 source-pinned upstream
modules and664 npm integrity-pinned dependency modules; the own driver has a
separate SHA. Full raw/profile/module identities are in
`/private/tmp/pipy-de04a-reference-osj0mnis/exploratory-attestation.json`.
The bounded lossless unit-only fixture retains the complete2,314,697-byte
captured profile with SHA
`7cc663104b1af089b6aaf9d64a9518f39cca95db643719d39fb9a604b0031979`;
actual CLI/reference outputs remain raw JSON.

The separate native sync/async actual-backend matrix includes18 explicit skips
for same-transaction pending/held/unawaited cuts that the blocking sync facade
cannot express. These are not omitted async or backend profiles. Nine focused
modules passed655 cases with those18 skips (131.25s); current SQLite document
cases added76 passes (10.93s). The six valid behavior controls each required
positive0 and negative1; malformed provenance/type/shape controls require2.
Fourteen actual-source controls were compiled into the real module classes in
isolated child interpreters, without modifying source/manifest files. Every
control failed its designated regression, including seed aliasing, terminal
memo order, absent-task recheck, later retirement pages/dedupe/new-task scan,
publication/cache ownership and historical-versus-current D1 binding.

All20 rounds of the changed race/lifecycle selection passed24 cases each, with
three explicit sync-facade skips and129 deselected (56.21s aggregate wall).
Four mechanism/test hashes remained identical before and after every round.
Architecture/inventory348 passed21.69s; Ruff check and format975 files passed;
mypy915 files passed. The additional split/metadata check passed175 cases7.24s,
retaining DE04b's second-predicate rollback and DE05's full-parent dependency.
Initial fixture-level focused attempts and first typing failures were repaired
before these passing results; they are not passing gates.

D1's delivered source/proof identities remain immutable historical literals.
The current migrated Session tests and new imported document helper are bound
independently in current closures; source-only snapshots require neither git
history nor private proof paths. Guard, commit, SQLite and typed-document static
inventories explicitly include `documents.py`. The ordered six-parent cascade
and seventh typed-document manifest validate current hashes; final coordinator
fresh chains, platform/full/docs gates and mandatory Opus5.5 HIGH review remain
pending. DE04a/DE04 remain IN_PROGRESS, DE04b OPEN, all43 P rows OPEN.

Logs: `/private/tmp/pipy-de04a-focused-all-first.log`,
`/private/tmp/pipy-de04a-sqlite-documents.log`,
`/private/tmp/pipy-de04a-source-controls/results.json`,
`/private/tmp/pipy-de04a-final-runs.log`,
`/private/tmp/pipy-de04a-inventory-first.log`,
`/private/tmp/pipy-de04a-mypy-second-all.log`,
`/private/tmp/pipy-de04a-{ruff,format}-final.log`,
`/private/tmp/pipy-de04a-final-metadata.log` and
`/private/tmp/pipy-de04a-cascade-final.log`.

Local scratch compatibility used Python3.11.13/SQLite3.49.1 and
Python3.12.15/SQLite3.53.1 with no package installation. Both imported this
worktree's current source and passed299 cases with18 explicit sync-facade skips
(99.64s and91.42s pytest respectively). The five-module selection covered typed
definitions, the new Session matrix/comparator, migrated D1 Session SQLite and
D2 historical/current attestation regressions. Full logs and interpreter/import
proofs are under
`/var/folders/79/dxyqffhn7753q_b9602pnfnm0000gn/T/pipy-de04a-compat-f7a2xv9g/`;
the invocation/result log is `/private/tmp/pipy-de04a-compat.log`.

Final restored comparator/metadata validation passed200 cases29.86s after the
split test and closure refresh (`/private/tmp/pipy-de04a-final-restored.log`).

### DE04a first independent review — import and child-environment repair

Claude Code Opus5.5 HIGH R1 returned ISSUES with two warnings and no suggestions;
both were accepted. The actual module counts and hashes in the exploratory
record remain historical observations, but the first CLI implementation did
not enforce the complete loaded-source allowlist or each npm file's recorded
hash. It also used predecessor HOME/TMP paths instead of the existing isolated
child environment. That initial implementation is not a passing independent
gate. The repair binds all38 actual upstream source modules (rather than the
inherited12-module subset), checks every dependency file against the existing
parent inventory and rejects any unbound import. It reuses `child_env(output)`
for reference/runtime execution and output-local HOME for native/control
children; no Session, Storage or product behavior changed.

New actual CLI subprocess regressions use private synthetic predecessor
attestations and captured Node observations. These are validator-wiring tests,
not fresh Pi execution. They require clean0, dependency drift2 and unbound
source2; actual compare/control subprocesses still require each unchanged
positive0 and six valid negatives1. They check that all pre-existing parent
bytes remain unchanged, allowing only the explicitly copied own driver, and
that HOME/TMP/npm cache/config writes are output-local. Both compiled actual
source controls (removing the import guard or restoring predecessor child
paths) were caught by their designated assertions. Initial focused wiring
validation passed3 cases18.61s; the source-control results are
`/private/tmp/pipy-de04a-r1-controls/results.json`. Further repaired validation
and independent delta review remain pending; no CLEAN or delivery is claimed.

Repaired final validation:203 comparator/metadata cases passed45.75s;
architecture/inventory348 passed21.21s; Ruff check/format975 files and mypy915
files passed. Python3.11.13 and3.12.15 each passed four changed CLI/closure cases
(20.84s/19.68s). All20 final rounds of the three changed CLI guard cases passed
(25 deselected each),377.26s aggregate wall, with unchanged comparator/test
hashes before and after every round. The new outer CLI test child also uses the
existing bounded/reaping process helper. Nine earlier clean rounds precede that
test-only lifetime change; the identity guard ended that series and it is not
relabeled final20. Native production and earlier Session/race proof sources
remain byte-identical to the first branch freeze.

Final logs: `/private/tmp/pipy-de04a-r1-final-focus.log`,
`/private/tmp/pipy-de04a-r1-inventory.log`,
`/private/tmp/pipy-de04a-r1-{ruff,format,mypy}.log`,
`/private/tmp/pipy-de04a-r1-final-compat/`,
`/private/tmp/pipy-de04a-r1-final-repeats/results.json` and
`/private/tmp/pipy-de04a-r1-controls/results.json`.
The ordered seven-manifest cascade is refreshed last; exact current closure,
new fresh reference chains and independent Opus5.5 HIGH delta review remain
coordinator gates. D10's Linux requirement concerns DE03; no DE04a Linux gate
or result is inferred or claimed.

### DE32a delivery and DE04a repaired main integration

DE32a delivered on main/origin at
`52ecc1fc75063e0d46bf72872a6c606f67e6ef7c`, exact reviewed tree
`56429222c235de487d1f437e3443c8734aa2f584`. Opus5.5 HIGH R1's one Warning
and one Suggestion were repaired; R2 was unscoped CLEAN (58.815s). Full local
`just check` exited0 with16045 passes/two expected skips (1678.34s pytest,
1709.48s wrapper), without a preview failure or CI claim. Skips were inactive
codemode runtime and case-insensitive filesystem. Python3.11.13/3.12.15 each
passed301 cases and actual30000 seven-field scanner observations. Docs passed
8.16s with1028 rendered links over six pages and zero broken links.

Two fresh core chains `H6Gucv` attested12 distinct IDs, six manifests,10 parent
bindings and485 D2 artifacts/24 modules/21 controls per chain. Two separate
minimal scanner snapshots each observed30000; they do not invent execution IDs
or a full-bootstrap claim. Root proof is
`/private/tmp/pipy-de32a-gates.kyesq2z1/final-gate-proof.json`, SHA
`d0e92a2548fd2657bfd57ae520334017e8e6e4ae60ed54e24a6bcbe7bc255e80`.
The old worker checkout/branch was removed normally with its archive retained;
both theme stores were `pi`. DE32a is LANDED; DE32 remains IN_PROGRESS and
DE32b OPEN, with all original environment acceptance retained.

The protected repaired DE04a worker snapshot is
`fe48e1d85c991af63f17bb1acc0308928007f730`, tree
`fbd5fec7dd6982b845930c837713be1e369d90db`. Its five-path accepted R1 repair
is now integrated with delivered DE32a, preserving both splits and deriving121
coverage rows. This combines the actual main integration and accepted oracle
repairs for the next Opus5.5 HIGH delta review against
`da42ad7a9a2ebd6ac0e20d678c5e088ace42c6ed`; R1 remains ISSUES until that gate
passes. Native typed-document mechanisms and scanner implementation/source
corpus bytes are preserved; source-preservation evidence and current closure
bindings are checked separately. Historical D1 source/proof identities remain
portable literals. No DE04b1a work or rows are imported. Final integrated
validation, fresh chains/scanner snapshots, docs/full check and exact-tree
independent review remain pending; no CLEAN or DE04a delivery is claimed.

Integrated focused validation passed404 cases with18 explicit sync-facade skips
(63.40s): typed definitions/actual-backend Session matrix, migrated D1 Session
SQLite, typed comparator, coverage/acceptance and scanner comparator.
Architecture/inventory348 passed20.49s; Ruff check/format980 files and mypy919
files passed. The first narrower integration run stopped after149 passes at an
existing current-status prose assertion; the gate description was corrected
without weakening that test. Source-preservation proof confirms18 worker paths
byte-exact, all seven scanner mechanism/test/input paths byte-exact to delivered
main, and all prior main documentation headings retained. A mechanical conflict
helper's initial output failed the line-count check and was discarded; the
seven conflicted files were rebuilt from main plus the protected worker before
these passing checks. No historical sections were dropped.

The branch's repaired20x3 guard proof and earlier production/source controls
remain source-valid; current integration changes shared metadata and bindings.
No unchanged production mutant/race matrix is relabeled as newly executed.
Logs: `/private/tmp/pipy-de04a-main-focus.log`,
`/private/tmp/pipy-de04a-main-inventory.log`,
`/private/tmp/pipy-de04a-main-{ruff,format,mypy}.log` and
`/private/tmp/pipy-de04a-main-source-preservation.json`.
Eight final validators/hash checks follow the ordered cascade; final coordinator
fresh chains, local platforms/docs/full check and Opus5.5 HIGH exact integrated
review remain pending. DE04a/DE04 remain IN_PROGRESS, DE04b OPEN, all43 P OPEN.

Final integrated metadata179 passed7.38s. All eight actual configuration
validators passed, with every local closure entry independently rehashed and
every declared upstream source/test hash verified using pinned `git show`.
Native/scanner closure entry counts are10/16/23/31/50/10/56 (plus baseline's
nested inventories); the typed phase retains its six semantically relevant
parents and does not invent a scanner dependency. The old-five audit reports
zero stale hashes. Proofs: `/private/tmp/pipy-de04a-main-validators.json`,
`/private/tmp/pipy-de04a-main-validators.log`,
`/private/tmp/pipy-de04a-main-closure.log` and
`/private/tmp/pipy-de04a-main-metadata-final.log`.

### DE04a post-R2 platform and actual-chain corrections

Opus5.5 HIGH R2 returned CLEAN on exact integrated tree
`e0030d1278f39716128f94d6cfa1ae32917a90f5` (baseline
`415472122543a69c8ec8e42bc8b5f51ba3c3467f`). This remains a historical review
result, not a delivery gate for the repairs below. Subsequent Python3.11
compatibility failed the same-holder `await_closed` scenario and its designated
negative control. The coordinator reproduced both failures on the already
delivered DE32 snapshot (two failures7.44s), proving inherited test-harness
behavior. A stdlib probe showed that `wait_for` changes the Task on3.11 while
`asyncio.timeout` preserves it on both supported interpreters. The holder-local
assertion now uses the timeout context; the production reentry guard is
unchanged. Scratch probes proved original success and guard-removed mutant
failure in both sync/async modes on3.11.13/3.12.15, without surviving workers.
Other bounded oracle waits were checked: completion/cleanup waits occur outside
the holder, and whole-case bounds establish their owners inside the same Task.

The fresh `Ofiv1L` chain then exited2 before creating document output because
its attestor looked for `d2/mutants/field.json`. The delivered D2 generator
actually writes `d2/field-mutant.json` and all other `<id>-mutant.json` controls
directly under its output. The consumer now reads that actual flat contract.
The synthetic predecessor fixture no longer bypasses `attest_sqlite`: it emits
the closed result, complete21 control records and exact flat files, then runs
the actual admission function. Its separately tested D2 comparison and upstream
attestations remain explicitly synthetic wiring doubles. A negative layout
case moves those controls into the previously assumed directory, rebinds the
artifact inventory and must fail2 before output creation. The unchanged flat
layout succeeds0 and actual document compare positives/negatives retain0/1.
Restoring the wrong path in the actual attestor is caught by the positive
fixture; new four-case admission validation passed19.20s.

The old `Ofiv1L` parent/D2 artifacts remain immutable partial historical
proof. They are neither rewritten to new manifests nor called complete fresh
DE04a acceptance. Both actual failed gate attempts justify the next narrow
Opus5.5 HIGH R3 delta and new full source-bound reference chains. No signed-zero
or future tracker policy change is introduced. Production files remain
unchanged; DE04a/DE04 remain IN_PROGRESS and all43 P rows OPEN.

Evidence: `/private/tmp/pipy-de04a-gates.gki5xivj/asyncio-task-affinity-probe.json`,
`/private/tmp/pipy-de04a-gates.gki5xivj/baseline-311-reentry/summary.json`,
`/private/tmp/pipy-de04a-affinity-scratch-{3.11.13,3.12.15}.json`,
`/private/tmp/pipy-de04a-integrated-chains-Ofiv1L/chain-1/documents.log`,
`/private/tmp/pipy-de04a-r3-predecessor.log` and
`/private/tmp/pipy-de04a-r3-controls/result.json`.

R3 validation passed330 focused cases75.20s. After a Ruff complexity finding
moved negative-layout construction into its existing fixture builder, final
comparator/metadata208 passed45.41s, Ruff check/format980 files and mypy919
passed. Architecture/inventory348 passed19.62s. Each supported interpreter
then passed the final seven-case holder-original/mutant/predecessor selection:
Python3.11.13 seven passes22.39s and3.12.15 seven passes21.96s (144 deselected).
Each also completed20 repetitions of the three holder/lifecycle tests under
`-X dev -W error` (55.15s/56.69s aggregate wall). Their test module and production
sources remained byte-identical; the later test-only fixture-builder extraction
is outside that repeated module. Three new admission guard/source-control
checks discriminate restoring the wrong D2 path and removing the earlier
import/environment guards. No surviving workers were observed.

Logs: `/private/tmp/pipy-de04a-r3-focus.log`,
`/private/tmp/pipy-de04a-r3-final-focus.log`,
`/private/tmp/pipy-de04a-r3-final-validation/`,
`/private/tmp/pipy-de04a-r3-validation/results.json`,
`/private/tmp/pipy-de04a-r3-final-controls/result.json`,
`/private/tmp/pipy-de04a-r3-import-env-controls/results.json`,
`/private/tmp/pipy-de04a-r3-inventory.log` and
`/private/tmp/pipy-de04a-r3-{ruff,format,mypy}.log`.
The eight-manifest cascade is regenerated last; exact new review and all final
coordinator gates remain pending.

The coordinator also found three current owner statements inconsistent with the
already accepted early-delta prerequisite. DE03b's plan acceptance, its exact
coverage copies and the SQLite schema module docstring now name DE04b's format
bump before current deltas; DE27 still owns full histories/as-of/fork/migrations.
No future child names or rows are imported. The sole additional production-file
byte change is that module docstring: the complete AST after removing only the
module docstring equals delivered `52ecc1fc` exactly. Proof:
`/private/tmp/pipy-de04a-r3-schema-docstring-proof.json`. Runtime/schema logic and
previous lifecycle proof sources remain unchanged.

## DE04 pre-code operation prerequisite split

Recorded before DE04b1a code on 2026-10-22. DE04a / DE03 retains typed current-base documents and terminal-task lifetime. DE04b / DE04a retains normalized operations, current deltas and real checkpoint rollback; it completes only after DE04b1a + DE04b1b join end to end. DE04b1a / DE02a1 is the independent pure decoded operation validator/owner/applier; operation grammar requires no typed runtime. DE04b1b / DE04a + DE04b1a retains all normalization/tracker, Memory+SQLite semantic schema2/current delta persistence, actual checkpoint callbacks, atomic rollback and linked publication. Any later subdivision is recorded immediately before its code. DE05 waits full DE04. DE27 retains full history/as-of/fork/migrations; acceptance owners and all43 OPEN P rows are unchanged. DE03 is LANDED; current DE06a/b and DE32a/b statuses remain intact. No independent review or runtime hookup is claimed here.

### DE04b1a local decoded-operation evidence

The private pure primitive now exists with owned decoded r/s/d/a/t/p/m
validation and detached atomic application. It reuses `codec.OwnedJson`;
`ownership.py` remains the separate SQLite path authority. General JSON roots
are supported; future document Storage retains the object-final-root gate.
No draft, tracker, transaction, Memory/SQLite, schema, checkpoint or runtime
hookup is introduced. DE04b1a remains IN_PROGRESS pending coordinator delivery.
The [contract](../durable-delta-ops.md) records exact bounds and CLI syntax.

The actual pinned Node source oracle passes60 closed recipes, including all
seven tags, reserved payload keys versus unsafe paths, own-path resolution,
array gap/append/delete/permutation boundaries, UTF-16 prefix removal and
identity, huge integral finite coordinate clamping, signed-zero payload and
mixed first-error precedence. Six actual loaded upstream modules are closed
and hash-pinned, with two targeted upstream test-source identities. These tests
are source-derived evidence; the oracle does not execute the upstream Vitest
suite. No npm installation occurs and no npm-package attestation is claimed.
Errors compare only the three precise classes; object profiles sort member key
identities and retain exact binary64 bits/UTF-16 units. The private source
snapshot/import inventory and child-local HOME/TMP/npm config/cache are retained
in the output. A first local attempt failed due an inherited unused resolver
argument; removing that argument restored the direct dependency-free closure.

Local focused tests cover native ownership, late failures, exact built-in ingress,
strict JSON duplicate/nonfinite refusal, closed provenance, fake-checkout output
boundaries, CLI0/1/2, nineteen schema-valid semantic mutants and seven actual
private native source mutants defeated by designated permanent tests. The initial
471 inventory tests passed, including coverage, all architecture inventories and
native session extension generation. Twenty sequential dev/warnings-as-errors
runs and Python3.11.13/3.12.15 compatibility also passed before the final
first-error-order case was added; final reruns and hash closures are recorded in
the frozen handoff. No full check, docs build, independent review, commit or push
was performed by this implementer. All43 P rows remain OPEN.

Final local reruns after the first-error-order change: 108 focused tests pass on
Python3.15, 3.11.13 and3.12.15, with the latter two logging imports from this
checkout's `src`; focused+coverage is234 passed. Twenty sequential runs of all58
pure primitive cases pass with `-X dev -W error`, unique TMP/basetemp directories
and bounded reaped children. Ruff and scoped mypy pass. The ordered manifest
cascade validates every baseline/records/Memory/guard/commit/SQLite/scanner
configuration, followed by the new primitive manifest. The frozen handoff owns
final artifact paths/hashes, and coordinator integration still owns full checks,
docs build and the exact Opus5.5 HIGH gate. This is local implementation evidence,
not delivery or acceptance-row closure.

### DE04b1a Opus R1 repairs

The exact Opus5.5 HIGH first review returned ISSUES (two Warnings and four
Suggestions), not CLEAN. Both Warnings and all four concrete Suggestions were
accepted. Append and prefix-removal results now share a UTF-16 surrogatepass
round trip, joining valid pairs while preserving lone surrogates. This touched
string spelling rule leaves untouched/replacement values and the OwnedJson
domain unchanged. Six permanent Python canonical-spelling/ownership cases catch
split-pair append, empty append and zero-prefix removal behavior that UTF-16
unit profiles alone cannot detect. An eighth private source mutant restores the
old append behavior and is defeated by those tests. The pinned source oracle
now compares66 recipes, including the review's string examples.

The current harness and session-storage contracts both assign the semantic
schema bump for first current deltas to DE04b1b; full history remains DE27.
Backlog/reference/harness now have dedicated DE04b1a headings. Exact owned
object keys use a direct lookup before the UTF-16-equivalent-key fallback.
The copied seven-field comment is corrected. Private fake-checkout refusal
cases distinguish a non-checkout from a checkout subdirectory, assert the exact
refusal reason, and require absent output directories for containment failures.

Local R1 repair checks:115 focused tests pass on Python3.15,3.11.13 and3.12.15;
twenty sequential64-case primitive runs pass `-X dev -W error` with private
TMP/basetemp. Ruff, scoped mypy and diff whitespace checks pass. The final
ordered eight-manifest cascade and fresh actual Node output identities are
recorded in the frozen R1 handoff. Root retains the next independent review,
full checks, docs build, integration and delivery; every P row remains OPEN.

### DE04b1a Opus R2 metadata repair

Opus5.5 HIGH R2 returned ISSUES with one Suggestion, not CLEAN. Its remaining
current acceptance-owner contradiction was accepted: the DE03b plan acceptance
cell and both exact coverage copies now assign the semantic format bump for
first current deltas to DE04b1b, retaining history/as-of/fork/migrations in DE27.
(The review called this the DE03c row; the exact canonical ID is DE03b.) A wider
current-contract search also found and repaired the same obsolete ownership
example in `sqlite_schema.py`'s module documentation. No schema, product or test
logic changed. Genuinely historical evidence wording remains unchanged.

The previous repair stopped short of this current-cell/source-doc audit; that
incomplete-repair churn is recorded rather than relabeled CLEAN. Focused metadata
checks, exact canonical cells/line anchors and all eight manifest validators are
recorded in the next frozen handoff. Root plans the next review on the actual
integrated tree after DE04a delivery, with no redundant branch-only docs review.
R2 remains ISSUES until that final independent CLEAN gate. All P rows remain OPEN.

### DE04b1a detached integration preparation

This detached candidate starts from protected DE04a review baseline145dc1cb and
merges the protected pure primitive9ca2bf77 without runtime hookup. DE04a remains
IN_PROGRESS until actual commit/push/remote facts arrive. DE32a stays LANDED,
DE03 stays LANDED, codec states and all43 OPEN P rows are preserved. The current
schema-bump contract is cumulative DE04b, implemented by DE04b1b; the pure b1a
child changes no schema. Typed-document source, controls and actual predecessor
admission remain preserved. This is preparation only; no full-check, delivery,
review or fresh integrated-reference claim is made here.

## DE04a verified delivery and DE04b1a integration successor

The coordinator verified actual main and origin at
`e6c1239d543a1415bb0ee9cf9f3770d36f7d7ac8`, tree
`72f330f65a25efef9c74c1cdbdd993f97a77cf5b`, exactly matching protected baseline
`145dc1cbfe3b5bdede43946a73cb5e8e9d0dbc70`'s tree and the final unscoped
Opus5.5 HIGH R3 CLEAN (81.975s), with no omissions. DE04a is LANDED;
DE04/DE04b/DE04b1a remain IN_PROGRESS, DE04b1b OPEN, DE05 waits full DE04,
and all43 P rows remain OPEN. No signed-zero policy decision is introduced.

A's actual full `just check` exited0:16,241 passed,20 skipped,one warning,
1695.49s pytest/1727.024s wrapper. Eighteen skips are explicitly async-only
interleavings unavailable to the sync facade; the other two are the existing
optional-runtime and case-insensitive-filesystem cases. The warning is assertion
rewriting of an already-imported shared fixture, not a failure. Strict docs
completed7.77s,1206 links,7 pages,zero broken links. Each supported interpreter
(3.11.13 and3.12.15) passed387 cases with18 explicit sync-facade skips and
compared72 actual native profiles to fresh Pi with exit0. These are A's verified
local gates; no GitHub CI claim is made.

Fresh `CFgtY4` chains supply12 core execution IDs with six manifests/ten bindings
plus two typed-document executions,14 IDs total. Each typed execution compared
24 actual Pi cases/72 native cases with38 actual source modules,664 dependency
file hashes and six controls. Two minimal scanner snapshots matched30,000
observations across all seven fields each. Root gate artifacts are under
`/private/tmp/pipy-de04a-gates.gki5xivj`; final-proof SHA256 is
`24e8fab1ee448e354177fb6d0c3153b6a2939356f1efc7f934a047e8999467fd`.
`delivery.json` confirms guard0, push0 and exact `ls-remote` identity.

The detached B successor preserves all A runtime/test/oracle bytes except the
schema module docstring's parent/child ownership clarification. Its runtime AST
matches A with only that module docstring removed. Seven pure B source/fixture
files remain exact protected `9ca2bf77` bytes, with no runtime hookup. The final
registry derives123 rows, retaining A's121 plus b1a/b1b. The typed phase retains
its six semantic parents, real flat D2 control admission/fixture,38-source guard,
per-file dependency guard and output-local environment. Historical D1 evidence
remains portable and unchanged; the current migrated closure is independently
rebound. The final nine-manifest cascade and local integration proof are recorded
in the frozen preparation handoff; Root owns transfer to actual main and the
next exact B review against R2 baseline `a0d00a9b`. B R1/R2 remain ISSUES until
that integrated independent CLEAN. No preparatory review or new full/docs gate
was run by the B integration writer.

### DE04b1a actual-main R3 current-evidence repair

Actual-main Opus5.5 HIGH R3 returned ISSUES with two Warnings, not CLEAN, on
baseline `f17ef67bada6a409578e8a1addfb8a5ae2b58fc4`. Both are accepted current
metadata defects: the evidence Status header and DE04/DE04a registry observables
still carried pre-delivery A state despite the delivered ledger table. They now
match A LANDED at `e6c1239d`, DE04/DE04b/DE04b1a IN_PROGRESS and DE04b1b OPEN.
DE04a's current evidence link points to the verified-delivery successor section;
the registry's enforcing evidence-anchor declaration is updated with it. The
cumulative b observable now distinguishes executed pure b1a evidence from the
still-open tracker/current-delta/checkpoint b1b join. No current observable
claims A delivery is pending. Dated historical review/admission sections remain
unchanged. The canonical123 rows/cells and all43 OPEN P statuses are preserved.
No product, pure primitive, schema, test logic or signed-zero policy changed.

Root owns the next exact R4 delta review and renewed final docs/closure gates;
R1/R2/R3 remain ISSUES until that independent CLEAN. The repair writer runs only
focused metadata checks, the final nine-manifest cascade/validators/hash audit,
and diff checks. No full/docs/review/commit/push/ledger command is run here.

## DE04b1a verified delivery and DE04b1b pre-code admission

Root verified actual main and origin at
`6e3b838fd253e7793c1d1365c6efc2d233a0ecce`, exact reviewed tree
`1c32bd820765d1a2a20d05c63a3951e549373dd8`. Final unscoped Opus5.5 HIGH R4
returned CLEAN106.245s with no omissions. Full `just check` exited0:16,359 passed,
20 explained skips and one existing shared-fixture assertion-rewrite warning,
1462.49s pytest/1493.39994s wrapper. The skips remain18 explicit async-only
sync-facade interleavings plus optional runtime and case-insensitive filesystem.
Docs preview passed all16 cases with no flake; strict docs completed8.12s,
1269 links,8 pages,zero broken links. Each supported interpreter passed357 cases
and compared66 actual final Node profiles. Fresh `qYPmoT` supplies two full
chains with14 core/document execution IDs, plus scanner30,000 and delta66 minimal
snapshots each; all nine validators passed. The final primitive manifest is
`465bcb0c21d85070745309df9d90aab9b0e627e32f3b5c6897ebe43365f77079`;
final-proof SHA256 is
`cc63fd825fbed8ac7bc603faae4b671a1dd8423de4b717638a302239e1b6ad4e`.
Push/remote were verified by Root. No GitHub CI claim is made. Old B/preparation
worktrees and branch were normally removed after protected26/27-path byte proofs;
external proof artifacts remain historical, not new child dependencies.

Recorded BEFORE any T/S production writer begins: DE04b1b splits into three
immediate cumulative children. T (`DE04b1b1`) depends on delivered DE04a +
DE04b1a and owns pure provenance/normalized operations, not Session activation.
S (`DE04b1b2`) depends on delivered DE03d + DE04b1a and independently owns
supplied-batch current Memory/SQLite deltas and semantic schema2; it does not
import or depend on T. J (`DE04b1b3`) depends on T + S + DE04a and the explicit
answer to owner policy266 BEFORE authoritative candidate/operation selection.
No signed-zero policy answer or default is inferred. The OPEN owner question is:
use the replayed/stored value as the authoritative callback/cache candidate for
the confirmed signed-zero prepared/replay discrepancy (Root's recommendation),
or reject affected transactions before commit? There is NO answer/default yet;
only J depends on this choice. The recommendation is an authoritative replayed/
stored candidate, not global zero normalization or silently changing ops. One
pinned exploratory false/delta-checkpoint example on Memory (also observed on
SQLite) starts from `{n:+0,x:0}` and mutates `n=1; n=-0; x=1`: Pi normalizes to
only `s(x,1)`, while the prepared/warm value contains `n:-0` and stored/replayed
value contains `n:+0`. This does not generalize to Memory true/base, which stores
`n:-0` and agrees. The existing16-case actual pinned Session/Memory/SQLite report
distinguishes backends and true/false policies; it remains EXPLORATORY evidence,
not a new final gate. T records exact ops plus separate
prepared/replay profiles; S applies valid supplied operations losslessly.
T may expose owned ops and
explicitly named replayed_value, while separately profiling Chord prepared and
replayed values and retaining both signed-zero witnesses. S applies supplied
valid ops with native binary64/UTF-16 fidelity; it chooses no Session candidate.
J must join actual Draft/Tx preparation, checkpoint callbacks, committed cache/
count and linked publication on the existing authority under the decided policy.

DE04b1b/T/S are IN_PROGRESS by admission only, J OPEN. Existing typed/full-base
Session and delivered pure applier are partial prerequisite seams, not evidence
that tracking, schema2, deltas or checkpoint integration now exist. No new source,
fixture, oracle, result or manifest is invented for these children. Every original
DE04b1b/b/DE04 acceptance remains cumulative: normalized provenance/placement/
movement/detachment/reserved keys/strings/dense bounds; immutable ops/value and
all-view revocation; current tail/version/count/base/schema/corruption; policy
ordering/second-predicate rollback; and atomic backend/ack/retirement/cache/
publication and deterministic readers. Only joined end-to-end evidence may close
these parents. DE27 retains full history/as-of/fork/migrations/retention and DE05
waits complete DE04; all43 P rows remain OPEN.

The corrected work orders were read from Root's exploratory preparation; they
are guidance, not production dependencies or delivery proof. Summary-safe archive
search `durable draft` returned no matches; no raw transcript was read. Root owns
future shared metadata and assigns disjoint topic-only write ownership to T/S.
The owner-authorized review cap is12 rounds per slice, with value-driven stopping
earlier; only Root runs the mandatory Opus5.5 HIGH gate. No implementation,
review/full/docs/Node/ledger/commit/push command is performed by this metadata
writer. This admission will fold into the first actual child landing, not a
planning-only delivery.


## DE04b1b2 supplied current delta integration candidate

2026-10-22: S is integrated as an implementation candidate over actual main
`6e3b838fd253e7793c1d1365c6efc2d233a0ecce` plus the recorded126-row pre-code
contract. B1a's actual delivery and final gates remain recorded in the preceding
verified-delivery/admission successor; no historical evidence is rewritten.
The17 source/topic/fixture files exactly match Root's protected worker snapshot
`1a82ae31b843baf9f5fa88ab7580246f05f2ce25` before cumulative manifest rebinding.
No T source is copied. The shipping-tree S row is LANDED conditional on final
Root gates; cumulative DE04b1b/b/DE04 remain IN_PROGRESS;
final unscoped Opus5.5 HIGH, full/local/docs/fresh-chain and delivery gates are
Root-owned and pending. This is not a final gate or planning-only delivery.

The [S contract](../durable-current-deltas.md) describes owned sequential
matching-version object-root materialization, exact per-command tail counts,
base reset, actual SQLite operation rows, one-snapshot required-tail replay,
corruption refusal and semantic `Migration(2, ())`. The immutable v1 DDL/base
bytes remain unchanged. A portable test fixture binds nine actual delivered
pre-schema2 source files to git-show6e3b hashes; the actual old opener refuses
checkpointed and WAL-only version2 without needing Git at test runtime.
Copy/numeric-history/as-of/fork/full migration policy remain DE27.

Preliminary worker verification only:1,354 focused tests;73 final S tests on
each current/3.11/3.12 interpreter;20 repetitions of14 critical cases; nine
designated private source mutants killed after discarding an initial wrong-import
harness attempt. Worker Ruff/mypy and diff checks passed. Two actual final
worker minimal Node snapshots each paired nine scenarios across Memory/SQLite/
reopen, with24 upstream sources,22 loaded modules and27 local closure entries;
actual comparator CLI controls returned0/1/2. These source-bound worker results
are not final integrated-manifest, fresh cumulative bootstrap or delivery proof.
Tail0/8/32/128 decoded3/19/67/259 items independently of retained older history;
measurements are observations, not performance bounds. Current-tail replay can
accumulate quadratic repeated preparation cost; no forced base/cache is added.

The registered helper is `scripts/durable_current_deltas_comparison.py`:
`run --pi-source /absolute/checkout --output /new/private/output`, and
`compare observed.json native.json`. It uses closed pinned git-show sources,
Node26.12.0/npm11.20.0, output-local environment and bounded/reaped children,
without package installation. It has no parent/D2 arguments, execution UUID or
fresh-full-bootstrap claim. Native Memory/SQLite/reopen agree losslessly; named
Pi SQLite JSON signed-zero and INTEGER-version differences remain explicit.
Historical raw-base comparator lanes retain their measured scope.

The registry retains126 derived canonical rows, all original dependencies and
acceptance cells, all43 P rows OPEN, and DE05 waiting for complete DE04. T remains
IN_PROGRESS without source integration here. J remains OPEN and requires the
unanswered policy266 choice before authoritative callback/cache candidate and
operation selection; supplied Storage operations make no such choice. J still
owns real Draft/Tx preparation, immutable batches, policy ordering, second-policy
rollback, cache/count adoption and linked publication. The pre-code admission
folds into this actual child implementation candidate; final delivery facts
will be appended only after Root verifies them.


Integration-local checks (not Root's final delivery gates): the current main
interpreter imports the actual main source, and all16 non-manifest S files
remain byte-identical to the protected worker. All nine historical source
fixture hashes match actual git-show6e3b bytes. All126 canonical plan cells and
owners, every P row and DE05 remain unchanged from the protected admission.
The focused backend/comparison/metadata run recorded1,774 passes and two current
coverage wording assertion failures; both retained structural-checkpoint and
append-admission boundaries were restored, and the complete137-case coverage
rerun passed. No product/test logic was weakened. The initial attempted focused
command had a nonexistent test filename and collected nothing; the corrected
run above is the actual evidence. Package architecture/inventory checks passed
348 cases, Ruff passed15 touched Python files and mypy passed five source files.
The Memory oracle closure now includes the decoded applier actually imported by
Memory, propagating through Session/SQLite/document closures. The final ten
manifest validators and local/reference/parent hash audit are retained in the
external frozen integration handoff; no new Node/full/docs/review/commit/push
result is claimed here.


## DE04b1b2 R1 current-capability repair

Root's exact unscoped Opus5.5 HIGH R1 returned ISSUES in193.031s on protected
baseline `9fe1e4b8fe6b507516a1e410a89e39f1ca6b2b13`, reviewed tree
`6d6fca81a48a067f7efac4df15be1557c4d5b21e`: three Suggestions and no Criticals
or Warnings, with no omissions/redactions/truncations. All three are accepted:
the SQLite opener docstring now states immutable v1 DDL/current semantic2;
SQLite ingress describes supplied current deltas with copies still refused;
and both mixed-prefix rollback tests retain their actual copy-refusal case
without singleton parametrization or unreachable delta-refusal branches. The
same rollback assertions for entries/documents/state/ID minting remain intact.
Root's additional current-reference clarification scopes A2b's refusal and
then-pending Session/DE03 statements to that historical measured lane, links
current S, and distinguishes its original16-path closure from the current17
including the imported decoded applier.

The earlier16-file exact worker-byte proof is historical to R1. These repairs
change only the SQLite docstring and two test cleanup files among those16;
the other13 stay exact. The additional SQLite connection file changes only its
docstring. Product AST excluding docstrings is identical to R1, and the portable
actual nine-source older-opener fixture is byte-identical. No backend behavior,
policy266 choice, parent acceptance/dependency, DE05 or P status changes.
The staged R1 index remains untouched during repair; Root owns staging/final
gates. R1 is ISSUES, not CLEAN; no full/docs/Node/push result is claimed.

The small follow-up review is valuable because these current capability
statements were false and dead test branches misleading, and the repaired tree
requires its exact delta gate. It is not an open-ended search for agreement.
Root owns R2 against the actual R1 baseline and the unchanged owner12-round cap.
Focused copy/schema/opener/current-delta/coverage regressions, AST equivalence,
Ruff/mypy, and the final ten-manifest cascade/actual validators are recorded in
the external frozen repair handoff; final delivery remains conditional on Root.


Repair-local verification:11 selected actual copy-refusal/mixed-prefix cases
passed (697 deselected by the explicit filter, not skipped); all293 current
schema/opener/current-delta/coverage regressions passed; package architecture/
inventory checks passed348 cases. Ruff check/format passed four touched Python
files and mypy passed the two docstring-touched source files. Product AST without
docstrings matches R1. A structural test-AST check confirms only the singleton
parameter/mark and unreachable delta branch were removed, with all remaining
setup, refusal and rollback assertions identical. The other13 non-manifest
worker files and the actual older-source fixture remain exact. The final ten
manifest validators/hash audit and unchanged staged R1 tree proof are retained
in the frozen repair handoff. These checks do not replace Root's pending exact
R2/full/docs/fresh-chain/delivery gates.


## DE04b1b1 pure provenance integration preparation

2026-10-23: independent T preparation is stacked on protected S review baseline
`86392ae58cb00257cc920c39ef1004a0d369ff95`, tree
`623053e459491c4361bc1d74bc5b375dbc6c76a5`. S's final Opus5.5 HIGH R2 returned
CLEAN101.149s; Root reports two actual fresh `yebDAG` chains, five attesters and
supported-interpreter27 native profiles passed. S subsequently delivered at
`8c8f30d7`, with final full/push/remote confirmation recorded in the
[S delivery successor](#de04b1b2-verified-delivery-and-t-integration-successor). The detached review baseline has exactly
the released S tree, so this overlay applies to the actual S release.

The initial historical eight-file copy was replaced by Root's protected final
numeric-profile snapshot below; no live T worker source is imported or copied. The tracker product identity remains
`3efab910abc8873b7a6fe3398a316264cb40ff2858e957193aeeae55d1313354`.
The [T contract](../durable-draft-tracker.md) and seven non-manifest files match
the protected final worker source. Worker and preparation checks are separated
below; no final T gate is claimed. The shipping-tree T row is LANDED conditional
on final Root gates;
S stays independent, cumulative DE04b1b/b/DE04 IN_PROGRESS and J OPEN.

The final agreed comparison boundary keeps numeric-looking object keys as
ordinary supported JSON. Native Python insertion order does not change.
Comparison-only profile `js-array-index-own-key-order-v1` projects canonical
ASCII array-index keys0..4294967294; nonindex member order, operation/path/list
order, types, signed zero and UTF-16 remain strict. Raw ordered profiles and
raw differences remain retained. The final protected corpus has1,612 recipes:
original1,591 byte values preserved plus21 explicit-key cases. Its result uses
`equivalent`, `comparison_profile`, `raw_ordered_profiles_equal` and a raw-diff
list, with `projection-controls.json` containing18 controls including a
5,000-digit nonindex key. The actual worker source pair and preparation tests
are recorded below as preliminary evidence, with final Root T gates pending;
no raw ordered equality claim is made for every numeric-key case. This is no
JSON-domain exclusion, native ordering change or policy266 choice.

T only exposes owned immutable operations and an explicitly named replayed
value, with Chord's prepared value recorded separately. J still requires the
unanswered policy266 decision before authoritative callback/cache candidate or
operation choice. No Session/Draft/Tx hook, backend/schema change or checkpoint
activation is included. The126 canonical cells/dependencies remain unchanged,
all43 P rows OPEN and DE05 waits full DE04. The minimal T source helper has no
parent acceptance/D2 binding, execution UUID or full-bootstrap claim. Source
closure and actual validators bind the final protected source and current
shared metadata. Root owns final compatibility/full/docs/independent review/fresh chains/
commit/push; this preparer runs none of those gates.


### Protected final T source replaces the historical initial copy

Root supplied the actual final eight-file protected snapshot
`c3c481bf969eb32b05f3b40e3889837a63abee09`; all eight were copied from that Git
object, without reading/importing the live worker checkout. The tracker remains
`3efab910abc8873b7a6fe3398a316264cb40ff2858e957193aeeae55d1313354` and Node
driver bytes remain unchanged. The original1,591 ordered typed JSON case values
are preserved and21 explicit-key recipes appended, for1,612 total. Seven topic/
product/test/driver/input files remain exact; the eighth manifest is rebound to
this preparation's shared metadata. S product and its R2 repairs are unchanged.

Preliminary worker proof, historical to its own manifest:127 focused tests
passed, including nine private production mutants and five comparator mutants;
20 repetitions of18 projection controls passed. Its actual pinned Node26.12.0/
npm11.20.0 minimal snapshot paired1,612 recipes with `equivalent:true` under
`js-array-index-own-key-order-v1`. `raw_ordered_profiles_equal:false` and ten
named raw differences remain visible (five operation-payload member differences,
ten replay member differences). All18 controls, raw profiles/values and both
signed-zero prepared/replay witnesses remain retained. This is actual closed
source execution, not npm installation/package or fresh full-bootstrap evidence.
Worker3.15 proof is not a new supported-interpreter or final integrated gate.
The preparer ran focused/static/architecture checks after the final source
copy/cascade. S is subsequently verified delivered; no T final review/full/docs/
fresh-chain/delivery is claimed by this preparation.


Preparation-local checks over the protected final source:264 focused cases
passed (127 T tests including its designated mutants plus137 coverage checks),
and348 package architecture/session-extension checks passed. Ruff check/format
passed six Python files and mypy passed four. Actual imports resolve to this
detached preparation's source. Seven T files still match the protected final
snapshot exactly; only the manifest is rebound. All existing S product files
and its R2 repairs are unchanged. All126 canonical cells/owners, whole43 P rows
and DE05 remain unchanged from the protected S baseline. Eleven actual manifest
validators and every local/reference/semantic-parent hash passed after the
pre-delivery cascade. No actual integrated Node source pair or final review/
full/docs/delivery gate is claimed. S delivery is now verified below and the
final cascade/freeze binds those facts. Root alone transfers to actual main.


## DE04b1b2 verified delivery and T integration successor

2026-10-23: Root verified main and origin at
`8c8f30d7e40157284a2a5ab65563965e508838d4`, exact reviewed tree
`623053e459491c4361bc1d74bc5b375dbc6c76a5`. Guard and push exited0 and remote
`refs/heads/main` matched exactly. Final-gate-proof SHA256 is
`020955e058b6e0f38a6455d79d8d9eec90fa237ede9bd44fcb257a3271dc3520`.
Final exact Opus5.5 HIGH R2 returned CLEAN101.149s on baseline
`86392ae58cb00257cc920c39ef1004a0d369ff95`, with no omissions. R1's three
Suggestions remain historical ISSUES and all accepted repairs are present.

Full `just check` exited0:16,435 passed,20 explained skips, no warnings and no
docs-preview flake;1510.27s pytest/1542.6956473329337s wrapper. The20 skips remain
18 explicit async-only cases on the sync facade, optional codemode runtime and
case-insensitive filesystem. Mypy annotation-unchecked notes are not warning
failures. Strict docs passed8.149s,1298 links/eight pages/zero broken links.
Each supported3.11/3.12 interpreter passed214 final affected cases and27 actual
native source profiles. Fresh `yebDAG` supplies two independent complete chains,
14 core/document IDs, ten core parent bindings, scanner30,000, pure delta66 and
nine current-delta scenarios per backend per chain; five attesters and all ten
actual validators passed. Minimal phases make no full-bootstrap or UUID claim.
Source/freeze/manifests, reaped process group and both `pi` themes were verified.
No GitHub CI result is claimed.

The T preparation's detached baseline and this actual S release have identical
trees. The final overlay preserves all S product/R2 bytes and the actual older
source fixture, adds the protected pure T prerequisite and updates shared
metadata/manifests. T remains a shipping candidate conditional on final Root
gates, with policy266 unresolved in J. No T final CLEAN/full/docs/fresh-source/
commit/push fact is inferred from S's delivery. All126 canonical criteria and
dependencies, all43 OPEN P rows, DE05 and cumulative parent acceptance remain
unchanged. Root owns actual-main transfer and final T gates after this freeze.


After the actual S delivery facts and successor anchors were updated, all137
coverage checks passed again. The final eleven-manifest cascade and actual
validators/hash audit bind that predecessor truth. Seven non-manifest T files,
all existing S product/R2 test and fixture bytes, and all126 canonical cells/
owners plus whole P/DE05 rows remain preserved. The final frozen overlay is
based on actual S release `8c8f30d7`'s tree; the preparation creates no commit or
main write. Final T source pairing/review/full/docs/fresh-chain/delivery remain
Root-owned and pending.


## DE04b1b1 R1 observation-boundary and evidence-claim repair

2026-10-23: mandatory unscoped Opus5.5 HIGH R1 returned ISSUES (one Warning,
one Suggestion;320.25s) on protected baseline
`d65911b86480cf2faec81284a4ded0ebc53ba0e5`, with no omissions. The Warning
identified missing Node prepared observations reaching an uncaught lookup and
exit1, which is reserved for valid semantic mismatch. The Suggestion identified
that the21 `surface/*` recipes translate scenarios into primitives rather than
pairing actual Python method dispatch with Node.

The producer boundary now requires a valid Node prepared profile while preserving
the general/native observation shape. Unexpected ordinary exceptions return2
with a fixed diagnostic that reveals neither payload nor traceback; process
interrupts still propagate. Eight added tests exercise actual `main` and `run`
with explicitly simulated child responses: missing prepared before native
execution, run/native KeyError/IndexError/AssertionError failures and an
unhandled KeyboardInterrupt. These are boundary tests, not new actual Node
oracle executions. The existing real subprocess comparison checks retain
positive0, semantic mismatch1 and invalid2 behavior. Those nine boundary/CLI
cases and three actual mapping/list/inherited-method tests passed locally.

The topic now states the primitive-translation limit explicitly. Existing native
mapping-method emitted-operation assertions remain; list-slice/extend/remove/
pop and inherited helper tests additionally pin their normalized operations.
No action grammar, corpus, result schema or acceptance criterion changes.
Tracker runtime and Node driver bytes remain unchanged. The1,612 recipes,
comparison-only numeric-key projection, raw ordered profiles and two separate
signed-zero prepared/replay witnesses remain unchanged; policy266 is unresolved.

The earlier protected source and R1 proof remain historical. This repair does
not claim a final CLEAN, fresh integrated Node pair, full/docs gate or delivery.
Root owns those gates and staging after the focused repair freeze. The staged
R1 index is left untouched during repair; the final eleven-manifest cascade
binds the repaired source/tests/topic. This note records those changes.


## DE04b1b1 verified delivery and owner-selected J admission

2026-10-23: Root verified main and origin at
`71cb8c911005b57acf9d67b0b8390307b852dffc`, exact reviewed tree
`f0b5925e90f5161f9e6d889318e0e177c2e5c418`. Guard and normal push exited0;
remote `refs/heads/main` matched exactly. Final-gate-proof SHA256:
`ce93204eee760e8681b6caddbb9f9ab619a18de716dfdc0a87076de519f01bd2`.
Logs and receipts: `/private/tmp/pipy-de04b1b1-gates.sg3iel74`.
Opus5.5 HIGH R2 returned unscoped CLEAN in89.292s on baseline
`a55dbb7f8e2ab351a280249ef299d24da5287574`, with no omitted inputs.
R1 remains historical ISSUES (one Warning, one Suggestion); both were repaired.

Full `just check`:16,570 passed,20 explained skips, no warnings or preview flake;
1530.92s pytest/1561.8717246669112s wrapper. Skips are18 async-only cases on
the sync facade, optional CodeModeRuntime and case-insensitive filesystem.
Strict docs passed with1318 rendered links/eight pages/zero broken links.
Python3.11 and3.12 each passed525 affected cases with18 explained skips and
1612 actual paired native source profiles. Eleven actual manifest validators,
local hashes and pinned upstream git-show hashes passed. Fresh independent
cumulative chains at `/private/tmp/pipy-de04b1b1-integrated-chains-ymVifF`
passed all six attesters:14 core/document IDs, ten core parent bindings,
scanner30,000, decoded deltas66, nine supplied-delta cases per backend and
Tracker1612 per chain. Tracker's explicit numeric-key comparison projection
retains ten raw ordered differences and both signed-zero prepared/replay
witnesses; this projection is not a native runtime rule. Minimal phases claim
neither full bootstrap nor UUID lineage. Process groups were reaped, both
themes remained `pi`, and all3989 frozen source hashes matched. No CI claim.

The owner directly answered policy266: **“Use the persisted value”**. This selects
the replayed/persisted owned value for checkpoint callbacks, warm cache and
publications; valid affected transactions are not rejected. Creation keeps its
actual final created object as a base with empty publication ops. Loaded empty
normalized operations retain the prior committed object and count. The narrow
pinned-Pi prepared/replayed signed-zero difference remains independently visible,
while native Memory/SQLite/reopen agree losslessly. The work-app question266 was
closed as resolved directly in chat, with the answer recorded in note12230.

Before production changes, J is split into sequential DE04b1b3a (actual Tracker
Draft adapters, absent-policy delta selection, count adoption and linked ops
publication) and DE04b1b3b (real checkpoint policy, post-I/O callback authority,
actual unload lifecycle and final cumulative gate). All original DE04b1b3,
DE04b1b, DE04b and DE04 criteria remain cumulative. DE05 still waits full DE04;
all43 P rows remain OPEN. Existing comparison drivers that forced checkpoints
must be migrated honestly to current default-policy execution; their historical
full-base results keep their original scope. No product always-base shim,
historical-native import or numeric-key projection substitutes for integration.
Astra MAX implements the difficult join; independent review remains exclusively
the installed Claude Opus5.5 HIGH harness. This admission is not an independent
review or delivery and folds into the actual first child implementation.


## DE04b1b3a default delta integration candidate

2026-10-23: Astra MAX implemented the admitted first J child directly on main
after the explicit owner answer. The only production change is `tx.py`: actual
Draft/DraftList adapters over one delivered Tracker, exact normalized operation
selection, default delta writes, replay-authoritative loaded value, committed
value/count adoption and immutable linked operations publication. Creation keeps
its actual final base; loaded empty operations retain the old incarnation;
structural nonempty operations and root fallback write actual deltas. Coupled
table/retirement/acknowledgement semantics remain on the existing authority.
See [the Session delta contract](../durable-session-deltas.md).

Protected worker implementation: `7fee476cbea24224468687ae81826f0590516a18`,
tree `5e4502665bcd0b7ffb26b7c2ee7c7e930e471d49`, based on delivered T71cb8c91.
Root independently verified all13 assigned file hashes and22 unchanged pre-code
files, unchanged index/HEAD, the original A fixture and absence of all56 recorded
process groups. Handoff: `/private/tmp/pipy-j1-implementation.44wUSF/HANDOFF.md`,
SHA256 `f7ca274e9f3a366ed39af8748f4265bdc6ed5b6504d015e157cc8a2de10d68ab`.
Frozen product SHA256 is
`2dbceba432ccc860f93adbec012e6ab5d6c9a1d58c2a0d0c7d4455f7cf1a8481`.

Worker focused validation:570 passed,18 explicitly async-only skips on the sync
facade and six manifest-dependent deselections;213.64s. Afterwards30 additional
surface cases passed separately and all65 core cases passed after a test-loader
typing repair. These are separate runs, not a claimed single600-case run.
All348 architecture/session-extension checks passed. Ruff and nonincremental
targeted mypy passed seven files; existing untyped-body notes remain notes.
Python3.11.13 and3.12.15 each passed20 repetitions of41 deterministic critical
barriers (820 each), under isolated `-I -X dev -W error`, with bounded/reaped
children and absent groups. Root inspected every repeat result. This is not a
full supported-interpreter or final integrated gate.

Both migrated reference drivers executed actual pinned Pi source with absent
checkpoint policy in a fresh copy of a verified reused dependency closure:
53 git-show source files,666 dependency files,703 loaded modules per driver
(38 sources,664 dependencies,one adapter). Node26.12.0/npm11.20.0 were checked;
no upstream working tree or historical native source was used. Session has41
Node scenarios and13 actual delta changes. Actual native pairs returned0 for
sync35/async41 scenarios and typed documents24 per Memory/SQLite/reopen backend.
The native imports and source hashes are bound in `actual-pairs/provenance.json`.
This is reused-closure worker execution, not fresh cumulative bootstrap evidence.

The new39,557-byte compressed expected fixture expands to2,428,093 bytes,
SHA256 `e592dcf85c2e5a1329436e0f1f62936eb8cfce60cca242eac89b552c315608fd`.
Root independently checked exact byte equality with the actual Node observation.
The original38,557-byte A fixture remains byte-exact to HEAD with its historical
forced-base scope. Current comparison schemas retain writes/counts/ops and
positive0/mismatch1/invalid2 controls; relabelled native Session schema fixtures
remain unit data, not Node evidence. Actual method routing is additionally
tested natively; translated exploratory Pi method recipes claim no Python dispatch.

Retained diagnostics: initial207P18S7F identified old assertions and stale source
mutants; the scalar-only copying mutant survived until a nested alias made its
ownership defect reachable. New expected-op assertions caused12 failures across
two parameter families and were corrected to actual removal/insertion and write
order, without changing product output. A four-case SQLite test wrongly assumed
cache residency survived failure-close; corrected spies prove no adoption while
permitting existing close clearing, and22 SQLite cases then passed. Initial mypy
typing errors and the six stale manifest failures remain in their original logs.
Comparator tests132P excluded exactly those six manifest-dependent cases; Root
must rerun all six after the final cascade. No failed attempt is called CLEAN.

Root now owns shared synchronization, the final eleven-manifest cascade, exact
Opus5.5 HIGH independent review, local full/docs/rendered-link checks, fresh
cumulative chains, final supported-interpreter checks and normal commit/push/
remote confirmation. The LANDED row denotes the shipping candidate conditional
on those final gates; this preparation claims no final CLEAN, full/docs gate,
commit or delivery. Final receipts are retained externally and actual delivery
is reconciled in the successor slice, as before. Real checkpoint callbacks,
count reset, post-I/O callback authority, actual unload and complete final J
proofs remain DE04b1b3b. Every original DE04/b/b1b/J cell is retained, DE05 waits
full DE04 and all43 P rows remain OPEN. No CI or new Linux result is claimed.


## DE04b1b3a R1 scoped documentation and copy repair

2026-10-23: mandatory Opus5.5 HIGH R1 returned ISSUES with three Suggestions,
no Criticals or Warnings, in329.084s on baseline
`1348912edaf478494701ec1960331e82bee89902`. The review had no skipped files,
truncations, redactions or denied tool inputs; the isolated copy was removed.
Suggestions were accepted because they resolve contradictory current contracts
and an unnecessary whole-document copy, rather than seeking cosmetic agreement.
This is not CLEAN; exact independent delta review remains required.

Tracker's class docstring now names the owner's persisted/replayed candidate
decision while preserving the helper's pure role. The Session reference prose
now says default-delta documents and names checkpoint callbacks in the same
exclusion list as the synchronized scope manifest. Loaded acquisition passes
its existing owned value straight to Tracker; a private created flag replaces
the redundant raw-tree/baseline pair and removes the unused full thaw. Creation
retains its actual final-object snapshot and normal Tracker preparation/revocation;
no normalization or candidate policy changes. The topic states that remaining
creation cost explicitly. No extra creation optimization is introduced.

Before R1, Root's integrated check passed742 cases and18 explained skips but
failed two Coverage assertions: replacing stale current-observation prose had
accidentally removed the carried Pi190 structural/checkpoint obligation and
DE02b3 append-admission boundary before DE11. Both notes were restored without
weakening tests; all138 coverage checks and all11 manifests then passed. The
product/comparator606 cases, including all six earlier manifest-dependent tests,
had passed in that integrated run. Original failure output remains under
`/private/tmp/pipy-de04b1b3a-gates.kr8afv9b/focused-r1` and the isolated repair
under `coverage-repair-r1`. Strict docs passed9 changed pages/1364 local links/
zero broken targets or anchors. These are preliminary R1-tree results, not the
post-repair final gates. Root will cascade the changed Tracker closure, validate
the repair, obtain exact delta review and rerun required final gates. All parent
criteria, open checkpoint/unload successor, DE05 and43 OPEN P rows remain intact.

Opus5.5 HIGH R2 returned ISSUES with one Warning: this new section had literal
leading diff markers and lacked its final newline. Root removed those markers
and restored the newline, preserving the recorded facts. The previous link-only
check did not detect that presentation defect; the repair additionally checks
the rendered section for stray markers and unintended list items. R2 is not
CLEAN. Exact independent delta review and final delivery gates remain pending.


## DE04b1b3a verified delivery and DE04b1b3b admission

2026-10-23: Root verified main and origin at
`33d39457ba73c583e2590e8d42f4f7e782a15ed6`, exact reviewed37-file tree
`f328e3929f2d36f94281da9e1a2e232544f6e2d9`, parent delivered T71cb8c91.
Guard and normal push exited0; `git ls-remote origin refs/heads/main` matched
exactly and the checkout was clean. Final proof SHA256:
`73742a1485347238e61ab01a7c96db0eaa529ba52826b182415906319f094c02`.
Receipts/logs: `/private/tmp/pipy-de04b1b3a-gates.kr8afv9b`.

Exact mandatory Opus5.5 HIGH R3 returned unscoped CLEAN64.646s, baseline
`80d8fd081a3bc94dc273b55b4ff6d7d1ec102409`; no omitted, redacted, truncated
or denied inputs and the isolated copy was removed. R1's three Suggestions
(329.084s) and R2's evidence-format Warning (92.521s) remain historical ISSUES;
all accepted repairs are present. Root verified all3992 source hashes and the
staged/committed tree after every final gate.

Full `just check` exited0:16,832 passed,20 explained skips, no warnings and no
preview flake;1660.93s pytest/1692.9611452079844s wrapper. Ruff999 formatted and
mypy925 source files passed. Skips:18 explicit async-only document cases on
the blocking sync facade, optional CodeMode worker runtime and case-insensitive
filesystem. Both supported3.11.13/3.12.15 independently passed744 affected
cases with18 explained skips, with imports from the exact final snapshot.
Each also passed20 final repetitions of41 critical barriers (820 each), with
isolated state, bounded/reaped children and absent groups. These final repeats
exercise the repaired constructor/source, separately from earlier worker runs.
Strict docs passed9 changed pages/1366 links/zero broken targets or anchors;
the repaired evidence section additionally had zero stray diff markers and zero
unintended list items. Both global themes remained `pi`.

Two actual fresh cumulative chains at
`/private/tmp/pipy-de04b1b3a-integrated-chains-PdN7nD` passed in400.2367s.
Six independent artifact attesters passed:12 core IDs/ten parent bindings,
14 core/document IDs,24 Pi document recipes and72 native observations per
chain,38 pinned source/664 dependency hashes, scanner30,000, decoded deltas66,
supplied deltas9 per native backend, and Tracker1612 with its explicit key-order
comparison profile, ten retained raw differences and two signed-zero witnesses.
Minimal phases make no full-bootstrap/UUID-lineage claim. A further Root J1
attester verified702 loaded file hashes and recursive artifacts, absent policy
and13 delta changes, sync35/async41 Session scenarios and24 document cases per
native backend,26 Session and8 typed-document schema-valid mutants. Twelve
additional real CLI controls exercised0/1/2 across both phases and both chains.
All final process groups were reaped; after the full supervisor exited, Root
found no owned marker process and no remaining full process group. No CI or
new Linux result is claimed.

Private-checker diagnostics remain retained: the inherited document attester
expected six controls and was updated to the exact eight IDs, including the
new publication-ops/default-base controls. An extra checker initially assumed
the wrong mutant folder and an unsupported mode flag; corrected invocations
then passed all twelve controls. Those were private verifier errors, not product
repairs or new source changes after CLEAN. Original attempts remain available.

DE04b1b3b is now admitted as one coherent implementation slice. Astra MAX's
read-only assessment of delivered33d39457 estimates250–400 production lines in
documents/tx/session_core plus a narrow ticket field/contract in session.py;
tests and the closed actual Pi/native oracle are larger, about1.5–2.5k lines.
The mechanisms share one acceptance boundary, so no further cut or owner
decision is needed. Root remains the sole metadata writer until the worker GO;
this admission folds into actual implementation, not a planning-only delivery.

Before callbacks are active, establish the authority/lifetime contract: scoped
driving-task identity surrounds every synchronous eager/resumed core send/throw,
never an await, and restores in finally, including nested advances/errors.
Persistent caller/settlement identities and sync holder-thread semantics remain.
Task identity is compared with `is`; foreign threads are excluded before reading
loop-confined actor fields. All readers/writers must preserve that confinement
or consistently use the existing lifecycle condition, with current-task capture
and displaced reference drops outside it. No extra lock or growing helper
registry. Current ticket.tasks is not a demonstrated race. Exceptional task
factory/submit/owned-handoff cleanup must be proven before enabling callbacks;
never strand a ticket, unawaited body or live authority marker.

The owner-selected persisted/replayed value remains authoritative for callbacks,
cache and publication; exact ops and prior counts are immutable. Every view is
revoked and all validation/terminal pages/publication ownership/submissions finish
before policy. Eligible loaded nonempty changes alone invoke exact synchronous
bool policy; true selects base/reset, false/absent delta/increment, including root
fallback. Second-predicate failure rolls back all coupled candidates. Foreign
callbacks/result disposal/finalizers run outside locks, thrown error identity is
preserved and post-policy health permits requested close but blocks failed owners.

Actual unload_documents/aunload_documents reuse the existing line/cache.clear,
with same-holder refusal, independent async queuing, foreign-thread sync waiting
and existing own-loop sync refusal. No writes/publication or test-only eviction
substitute. Cold value/version/count, old snapshots, all scopes/keys, queued cancel,
close/failure and fresh SQLite reopen remain required. The final oracle executes
actual pinned Pi Session Memory/SQLite/reopen and actual native backends with
separate typed observations, explicit signed-zero/runtime boundaries, closed
schemas, mutants and fresh cumulative source chains. Every original DE04/b/b1b/J
cell remains cumulative; DE05 waits full DE04 and all43 P rows remain OPEN.


## DE04b1b3b resumed integration

2026-10-23: The owner requested continuation. Root restored the complete WIP
commit `3b1c395d` as a squash delta against delivered main `33d39457`; the
preservation branch remains intact. No historical checkpoint receipt is promoted
to a fresh final gate. Product source is unchanged from the preserved worker
freeze. Shared docs and release notes now describe the implemented checkpoint
and unload behavior, with its bounded actual Pi/native phase registered for B.
The checkpoint manifest and all11 predecessor closure identities are integrated.
The repaired focused integration selection passed267 cases; coverage passed138
and reference artifacts49. The initial docs build passed with1384 rendered local
links across9 changed pages and zero broken targets/anchors. Two fresh cumulative
chains, full checks and exact independent review were still running at this
intermediate observation. DE04 parents remain IN_PROGRESS and all43 P
rows remain OPEN.


The first whole-slice Opus5.5 HIGH review returned ISSUES: one Warning for Root's
accidental insertion of the new changelog entry under all six Added headings,
and one Suggestion about the stale operational continuation note. Root retained
the entry only in Unreleased and rewrote that note as dated history, linking
the live backlog/evidence. No product-code finding was reported. The review had
no skipped, truncated, redacted, excluded or denied inputs, and its isolated copy
was removed. Earlier oversized attempts failed before any verdict and are not
review evidence. Consolidating staged and unstaged changes prevented duplicate
old/new fixture diffs in the review bundle. The actual capture's exact raw hash
is unchanged after LZMA repacking. A delta review is warranted to verify these
accepted documentation repairs; final delivery gates remain pending.


## DE04b1b3b cumulative acceptance

2026-10-23: B completes the retained J/b1b/b/DE04 implementation acceptance.
The owner-selected persisted/replayed candidate remains authoritative; no
product runtime default or P requirement is closed. DE05 is the next bounded
slice. Full history/as-of/fork/migrations remain DE27.

| Retained acceptance | Executable ownership |
| --- | --- |
| Definitions, scopes and captured policy identity | document definitions and Session Tx core tests |
| Exact normalized operations, replay fidelity, empty versus structural edits | delivered Tracker/current-delta tests plus Session delta matrix |
| Immutable policy candidate/ops/prior count, true base/reset and false delta/increment | Session checkpoint matrix over sync/async Memory, SQLite and reopen |
| Full validation/revocation before policy, second-policy rollback and terminal pages | Session checkpoint rollback and actual257-member retirement probes |
| Eager/resumed authority, exceptional factory drain, health and reentry | Session callback-authority barriers and retained commit/guard suites |
| Actual unload, retained snapshots, queued cancel/close and cross-thread waiting | Session unload tests using the real line-owned methods |
| Acknowledgement/adoption failures and durable outcomes | actual SQLite checkpoint-reset refusal/ack-loss/reopen tests |
| Actual pinned pairing and strict native backend equality | session-checkpoints-j-v1:52 recipes per each3 Pi/native backends,8 schema-valid mutants and invalid2 |

The full local `just check` passed: 17260 passed, 20 skipped in 1916.91s (0:31:56). Ruff1004 formatted and Mypy929
source files passed. Compatibility checks with `-I -X dev -W error` passed
772 cases on Python3.11 with4 unavailable eager-factory skips, and776 on
Python3.12. Both bounded leaders were reaped with absent process groups.
Focused integration passed267, coverage138 and reference artifacts49. Docs
build and rendered-link checks passed; the pre-closeout render checked1381
local links across9 pages with zero broken targets or anchors.

Two fresh cumulative reference chains passed on pinned Node26.12.0/npm11.20.0.
Seven independent artifact attesters passed: core, typed documents, scanner,
decoded operations, current deltas, Tracker and checkpoints. Each checkpoint
phase verified710 loaded-module hashes and466 artifact hashes; actual CLI
0/1/2 and strict native Memory/SQLite/reopen sign/member-order equality passed.
Both earlier document fixtures remain byte-identical; the new LZMA fixture
preserves the actual raw capture SHA256 recorded above. Pure minimal phases
retain their explicit reused-dependency scope and make no bootstrap claim.
No new Linux or GitHub CI result is claimed.

Whole-slice Opus5.5 HIGH R1 reported one Warning and one Suggestion, both
accepted and repaired; exact-baseline R2 was unscoped CLEAN with no skipped,
truncated, redacted, excluded or denied inputs and its isolated copy removed.
The final closeout changes only documentation, status/coverage expectations and
the consequent manifest hashes. The product source and oracle mechanisms are
unchanged from that CLEAN and full-check snapshot. Final delivery requires
focused validation and exact independent review of that closeout delta, plus a
guarded normal push with remote SHA verification. These receipts are retained
locally outside git, separately from the earlier preservation archive.

Workflow lesson: consolidate a restored staged snapshot before assembling its
review bundle, and validate changelog edits within Unreleased. The oversized
review attempts and initial metadata failures are retained diagnostics, not
passing gates. Promotion: incident; local repair and explicit receipts suffice.

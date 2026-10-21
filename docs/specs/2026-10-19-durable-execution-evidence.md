# Native durable execution evidence ledger

Status: **DE01a, R1 and DE01b1–DE01b3b landed; DE02a1, DE02a2a and DE02a2b landed; DE02b1a in progress; all parity rows OPEN**,
2026-10-21. This ledger accompanies the
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

`OPEN` means no accepted delivery. DE00 is landed with the evidence below. Later rows have no accepted implementation,
validation, review, main commit or pushed-ref evidence. Dependencies and full
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
| DE02 | Records, memory storage and atomic session core | IN_PROGRESS | Cumulative a1+a2+b; original parent acceptance retained |
| DE02a | Owned records/values, Storage port and detached atomic Memory | LANDED | Cumulative a1+a2 both delivered; no P completion |
| DE02a1 | Owned finite JSON, versioned records and Storage Protocol/DTOs | LANDED | b1aa0562; actual Opus5.5 HIGH CLEAN; CI39387254837 success11/11 and artifact audited; historical ISSUES retained |
| DE02a2 | Atomic detached Memory, raw-base seam, ancestry/indices/concurrency and native storage comparisons | LANDED | Cumulative a2a+a2b both delivered; original backend acceptance retained |
| DE02a2a | Internal atomic table Memory and current native/reference oracle | LANDED | 85390e2b; actual Opus5.5 HIGH CLEAN; CI39408774643 success11/11, complete logs/artifact audited; review history preserved |
| DE02a2b | Current raw-base documents in same candidate and cumulative bounded Protocol proof | LANDED | 72253352; Opus5.5 HIGH ISSUES (one Suggestion) repaired, Codex gpt-6.1-sol HIGH delta CLEAN; push verified; CI not a delivery gate (run 39545767044 started, not awaited) |
| DE02b | Session/Tx admission, seal/guard/adopt/publication/close | IN_PROGRESS | Cumulative b1a+b1b+b2+b3; all original Session/draft/failure obligations retained |
| DE02b1a | Private guarded Storage and lifecycle owner over real Memory | LANDED | 992c2102; Codex gpt-6.1-sol HIGH ISSUES (one Critical, one Warning) repaired by Opus5.5 HIGH, delta CLEAN; push verified; no CI claim |
| DE02b1b | Actual pinned Node guard oracle and paired guard recipes | LANDED | cb38caf3; Codex gpt-6.1-sol HIGH ISSUES (two Warnings) repaired by Opus5.5 HIGH, delta CLEAN; push verified; no CI claim |
| DE02b2 | First internal Session/Tx over the same owner | LANDED | Cumulative b2a+b2b both delivered (be6ee84e, eb1e3ba7); callback API decision recorded before code |
| DE02b2a | Private Tx core and mutable raw-base drafts, no commit entrypoint | LANDED | be6ee84e; Codex gpt-6.1-sol HIGH ISSUES (one Critical, one Warning) repaired by Opus5.5 HIGH, delta CLEAN; inventory-name repair delta CLEAN; push verified; no CI claim |
| DE02b2b | First exposed internal Session: line, async driver, publication and close | LANDED | eb1e3ba7; Codex gpt-6.1-sol HIGH r1 ISSUES (one Critical, four Warnings), r2 (one Critical, one Warning), r3 (one Critical, three Warnings; Root removed the claimer-delivery mechanism), r4 (one Warning), r5 CLEAN; push verified; no CI claim |
| DE02b3 | Cumulative paired Session acceptance and final b gate | IN_PROGRESS | Local paired Session evidence below; independent gates pending |
| DE03 | SQLite authority and local ownership | OPEN | None; all gates OPEN |
| DE04 | Minimal typed documents | OPEN | None; all gates OPEN |
| DE05 | Minimal task runtime and recovery scheduler | OPEN | None; all gates OPEN |
| DE06 | Canonical checkpoint/request/outcome codecs | OPEN | None; all gates OPEN |
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
| DE32 | Execution environment capabilities | OPEN | None; all gates OPEN |
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

DE00 through DE01b3b, DE02a1, DE02a2a, DE02a2b, DE02b1a, DE02b1b, DE02b2a and
DE02b2b have landed, so the cumulative DE02a2, DE02a and DE02b2 parents are
delivered. DE02b is split b1a→b1b→b2→b3 before product edits: b1a the private
guarded Storage/lifecycle owner and b1b its actual pinned Node oracle (both
landed), b2 the first internal Session/Tx over that same owner (landed through
b2a, the private Tx core and raw-base drafts, and b2b, the first exposed
Session), b3 cumulative paired Session acceptance and the final b gate (in
progress).
DE02b2 waited both, DE02b waits all four children,
DE02 waits a1+a2+b and DE03 waits b. Original parent acceptance remains
unchanged. All P rows OPEN; no partial backend/product factory/default release.
Delivery from 2026-10-21: local `just check`, Codex gpt-6.1-sol HIGH CLEAN and a
pushed SHA verified with `git ls-remote`; GitHub CI is not a delivery gate.

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

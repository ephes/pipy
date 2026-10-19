# Native durable execution evidence ledger

Status: **DE01a, R1, DE01b1 and DE01b2 landed; DE01b3a in progress; all parity rows OPEN**,
2026-10-19. This ledger accompanies the
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
| DE01b3a | Fresh executable local acceptance, hardware/exported workloads, four fresh mutants, exhaustive mapping | IN_PROGRESS | Depends on landed b2; all original DE01b acceptance retained cumulatively |
| DE01b3b | Mandatory real pinned CI, selective artifacts and successful pushed-job proof | OPEN | Depends on b3a; DE02 blocked until green b3b |
| DE02 | Records, memory storage and atomic session core | OPEN | Blocked until DE01b3b is green |
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

DE00, DE01a/R1, DE01b1 and DE01b2 have landed; historical local freezes and
ISSUES remain below. Before b3a implementation the authorized b3 split is:
local executable acceptance/workloads/hardware/four fresh mutants/exhaustive
mapping in b3a, then mandatory real pinned CI/selective artifacts and actual
successful pushed-job evidence in b3b. Every original b3 acceptance remains
cumulative. DE02 is blocked until b3b is green; all P/Python-adapter rows OPEN.

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

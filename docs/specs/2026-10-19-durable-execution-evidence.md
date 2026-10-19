# Native durable execution evidence ledger

Status: **DE00 preparation in progress; no slice landed; all parity rows OPEN**,
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
| Upstream | Pi durable **1.3.0**, `2ec55d808d455b1a64a2bf7888198207a096120a`; source inspected with read-only `git show`; no reference runner or comparison yet |
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
| E00-CHECK | `just check`: Ruff, formatting, mypy and full pytest; inspect all skips and runtime availability | OPEN pending the full gate on the DE00-PRE1 test tree; earlier diagnostic failure and repaired-tree evidence remain separate |
| E00-PREK | `prek run --all-files` if `.pre-commit-config.yaml` exists; include new files explicitly | Not applicable: config absent; new Python files explicitly passed Ruff lint/format |
| E00-DOCS | `just docs-build`, rendered local file/anchor links and focused documentation checks | Exit 0 after DE00-PRE1: strict build, 887 local links across all 8 changed/new documentation pages, 44 focused docs-build/MCP audit tests; no skips or broken links |
| E00-REVIEW | Fresh independent Claude Code Opus 5.5 HIGH implementation review | OPEN; coordinator-owned, not run by the implementer |
| E00-LAND | Owned paths/hunks only, green commit and confirmed push on `main` | OPEN; no stage/commit/push performed |

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

Status: **local focused checks passed; full gate and review OPEN**, an explicitly
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

`OPEN` means no accepted delivery. DE00 remains OPEN until its independent
review, commit and confirmed push are recorded. Later rows have no implementation,
validation, review, main commit or pushed-ref evidence. Dependencies and full
acceptance cuts are defined by the work order; splitting a row must preserve
that coverage here. A local validation result is not a landed slice.

| Slice | Work | Status | Evidence / review / main commit / push |
| --- | --- | --- | --- |
| DE00 | Experiment and work-order import | OPEN | E00-* preparation; review, commit and push OPEN |
| DE01 | Pinned reference fixtures and comparison oracle | OPEN | None; all gates OPEN |
| DE02 | Records, memory storage and atomic session core | OPEN | None; all gates OPEN |
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

Complete DE00 validation and independent implementation review, then record the
actual main commit and confirmed push before DE01 starts. DE01 owns the pinned
reference adapter, known-mismatch negatives, sanitized fixture manifest,
licensing/provenance and comparison schema. No required P behavior, real-provider
evidence or performance budget has been measured by DE00. Subsequent gates may
only close from concrete evidence; unavailable tooling/auth or an ISSUES verdict
stays explicit.

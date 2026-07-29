# Architecture quality assessment — 2026-07-29

Status: Slice 16 implementation complete; independent review complete; landed
Slice 16 commit pending. No Slice 16 commit hash exists yet.

This is the durable closeout assessment for the
[Architecture Quality Improvement Program](specs/2026-07-24-architecture-quality-improvement-plan.md).
It separates verified facts, intentional product differences, actual gaps, and
queued recommendations. It does not authorize product-parity implementation.

## Revisions and audit method

The assessed pipy baseline is clean `main` at
`fe474e0e55b3d1e8ae370534acb54a0a5fd9496b` and the implementation checkpoint
is clean `main` at `e35a0d54898c160ac37acbdbdd35fff727569508`. The assessed
range is therefore
`fe474e0e55b3d1e8ae370534acb54a0a5fd9496b..e35a0d54898c160ac37acbdbdd35fff727569508`:
eight commits, 287 changed files, 15,922 insertions, and 5,454 deletions. Before
Slice 16 it was 0 commits behind and 8 ahead of `origin/main`.

Slices 10–15 are exactly:

| Slice | Commit | Subject |
| --- | --- | --- |
| 10 | `ed85849b03c43e6214d8c32e7eafa8e2292b35a2` | `refactor: decide one-shot runtime convergence` |
| 11 | `8f61ffe0acd8e7b6c950b5c052b905041230c111` | `refactor: extract terminal editor state` |
| 12 | `44c094879542612a811046f7017e7a4734d6ac41` | `refactor: extract overlay and chrome state` |
| 13 | `eacb74275117b52b80c635bd18866433d043c32a` | `refactor: extract pure terminal frame composition` |
| 14 | `03753be66d4726cc8008d502df1f22df7344abcc` | `test: make PTY synchronization deterministic` |
| 15a | `f02255a82a2eeed10185fdb7977ec440ba1eb6d1` | `style: format examples scripts and source` |
| 15b | `d1c8cbccfe1992dc080bc79e7ba7eaba149dddcb` | `style: format tests` |
| 15c | `e35a0d54898c160ac37acbdbdd35fff727569508` | `chore: enforce Ruff formatting` |

The comparison worktrees were independently checked clean at these exact
revisions:

- Tau clean `main` at `edd4ccc6171420015fa0f04bec75d38fe32beb68`, tag `v0.3.1`,
  package version `0.3.1` (`release: prepare 0.3.1 (#462)`).
- Pi clean `main` at `7df73a00c6cf85c000bf1ce1594c9284067a92f0`, coding-agent
  package version `0.82.0` (`Add [Unreleased] section for next cycle`). The
  monorepo root's separate version is not the coding-agent product version.

Three independent read-only audits used the exact model
`openai-codex/gpt-5.6-sol` in these roles:

1. `internal-pipy-architecture-quality`;
2. `pipy-versus-current-local-tau`; and
3. `pipy-versus-current-local-pi`.

Their claims were cross-checked against the three repositories, owners, tests,
configuration, and program ledger before being retained here. Test counts are
not compared between different products.

The historical ledger also records review-protocol deviations rather than
hiding them: the Slice 3 contract took 33 plan rounds, Slice 10 took four valid
implementation-review rounds, and Slice 12 took eight valid rounds, beyond the
program plan's ordinary three-round code cap. They are explicit deviations,
not retroactively described as protocol-compliant. The Slice 10/12 entries
preserve their contemporaneous stop rationales, including the consecutive
self-inflicted-churn rule; other historical entries retain explicit operator
authorization where it was actually recorded. These are process facts, not
retrospective review verdicts for Slice 16.

## Before and after

Two baseline axes are retained so unlike checkpoints are not silently merged.
`ba5d030394123fe831ea747031e6162158a74034` is the full architecture-program
baseline recorded by the 2026-07-24 plan. `fe474e0` is the narrower Slice 10
starting checkpoint after strict-source completion. The final implementation
checkpoint is `e35a0d5`; Slice 16 changes assessment/documentation and one
focused documentation-consistency test only.

| Measure | Full-program baseline at `ba5d030` | Slice 10 start at `fe474e0` | Implementation checkpoint at `e35a0d5` |
| --- | ---: | ---: | ---: |
| Full tests | 4,585 passed / 2 skipped | 4,667 / 2 | 4,826 / 2 |
| Diagnostic `mypy --strict src` | 144 errors in 41 files | clean, 165 files | clean, 169 files |
| Combined `mypy src tests` inventory | not recorded | clean, 425 files | clean, 437 files |
| Unignored Ruff C901, repository / `src` | 39 / 23 | 38 / 22 | 34 / 18 |
| Source `type: ignore` uses | 1 | 1 | 1 |
| `ToolLoopTerminalUi` measured fields | authoritative baseline later fixed at 128 | 128 | 43 |
| Source / test Python physical lines | 77,982 / 112,562 | not recorded | 81,738 / 121,025 |
| `tool_loop_session.py` / `tui.py` physical lines | 5,085 / 7,017 | about 5,433 / 7,018 | 5,433 / 6,329 |
| `_BuiltinCommandInterpreter.interpret` | complexity 97; 861 lines; 35 parameters | complexity 9; 27 lines; 2 parameters | complexity 9; 27 lines; 2 parameters |
| `_ReplLoopStep.step_once` | complexity 43; 559 lines; 37 parameters | wide composition owner | complexity 43; still wide |
| Ruff formatter | 261 files would reformat; no gate | no repository gate | clean and enforced across 479 files |
| PTY sequencing | load-sensitive paint/readiness races were recorded | races still queued | byte/offset readiness handshakes; bounded polling only |

The final uncommitted Slice 16 documentation worktree leaves source metrics
unchanged and adds 27 physical test lines for its documentation-consistency
check: **81,738 / 121,052** source/test lines, **34 / 18** C901 findings,
**43** TUI fields, and **1** source suppression. Ruff still discovers **479**
files because the focused check extends an existing test module. Its full gate
is **4,827 passed / 2 skipped**.

The initial plan's 129-field TUI number was an audit estimate. Slice 1 added the
AST metric and fixed the authoritative baseline at 128; 128 is the denominator
used above. Physical-line growth reflects explicit typed owners and substantial
characterization as well as product work that preceded this narrow range; line
count is evidence, not an optimization target. The material outcomes are the
ownership boundaries, 85 fewer façade fields, four retired TUI complexity
findings, complete strict source coverage, deterministic PTY sequencing, and a
single formatter owner.

## Ownership improvements

All statements in this section are **verified facts**.

- **Slice 10 — explicit compatibility ownership.** `pipy run` and the narrow
  Python SDK retain `NativeHarnessCompatibilityRuntime` because their
  metadata-fixture tool, follow-up, failure, and workflow-archive contracts are
  intentionally not canonical `AgentLoop` semantics. Actual provider turns on
  both paths use `ProviderTurnExecutor`, so intentional separation does not
  retain a second provider-completion pipeline. Product interactive, JSON,
  print, and RPC modes use the canonical coding session and `AgentLoop`.
- **Slice 11 — editor ownership.** `EditorState` now owns buffer/cursor,
  completion, recall, undo/redo, paste, rehydration, and queued input through
  terminal-independent transitions. The TUI façade adapts terminal,
  filesystem, clipboard, extension, and paint effects instead of mirroring the
  state.
- **Slice 12 — overlay, chrome, and lifecycle ownership.** `OverlayState` owns
  the closed active-overlay discriminator and typed nested restoration.
  `ExtensionChromeState` owns extension chrome values, listener identities, and
  footer rebuild bookkeeping. `TerminalDriver` separately owns balanced raw,
  foreign-TTY suspension, and forced-close recovery.
- **Slice 13 — pure frame ownership.** `frame_renderer.py` consumes immutable
  snapshots and produces deterministic logical frame/paint plans without
  terminal writes, callbacks, owner mutation, or reverse imports. The façade
  remains the effectful snapshot adapter and `TerminalDriver` remains the byte
  sink. Four TUI C901 findings left with this ownership move.
- **Slice 14 — observable PTY sequencing.** Test-only `tests/pty_sync.py`
  centralizes output-then-readiness observations after `TCSAFLUSH`, using exact
  byte offsets or fresh acknowledgement counts under one monotonic deadline.
  No test-only product byte or API was introduced; Linux and macOS real-PTY CI
  remains.
- **Slice 15 — one formatter owner.** `just format-check` owns
  `ruff format --check .`; `just check` and CI delegate to it, while `just
  format` applies the formatter. There is no custom formatter exclusion.

Tau 0.3.1 remains a useful positive reference for concise package and
contributor orientation, a compact reusable harness/event loop, a distinct TUI
state/adapter seam, strict catalog validation, broader ordinary Ruff rules, and
mature package/wheel verification. Pipy now has equivalent conceptual
agent/coding/UI boundaries and stronger executable import-direction, C901,
PTY/platform, suppression, and formatter gates. Tau's Textual and physical
package choices are references, not prescriptions; pipy's synchronous normal-
buffer inline-scrollback architecture is intentional. Tau's documented linear
dependency arrow is not copied as a normative model because its code does not
fully support that simplification.

## Residuals and proportional dispositions

### Correctness and ownership residuals

| Classification | Residual and evidence | Proportional disposition |
| --- | --- | --- |
| **Actual gap** | Reload candidate activation still uses the live host and clears retained chrome before activation; a rejected candidate can therefore leave the prior semantic generation without its chrome. | Queue one bounded reload-contract completion/reconciliation before ordinary product-parity work. Use a candidate-owned staging host and commit chrome only after acceptance; do not reopen unrelated architecture work. |
| **Actual gap** | A timed-out activation worker is not sealed from later contribution registration. | The same bounded reload slice must seal/dispose rejected activation registration and sidecars. |
| **Actual gap** | `SessionExtensionGeneration` freezes only runtime plus flag values; tool capability, renderer, emitter/lifecycle, and presentation projections are published separately. | Complete a single frozen projection or formally narrow the old ideal contract and prove the replacement. Current safety ratchets remain useful in either outcome. |
| **Actual gap** | `SessionGenerationRef.snapshot()` says production operation-level adoption is pending, and production consumers still read the current generation per access. | Adopt one production snapshot per extension operation before any concurrent publisher is introduced. |
| **Actual gap** | No production mutation port captures or validates a `generation_id`; stale generation-bound refusal promised by the ideal contract is absent. | Bind class-A mutation ports to their creating generation and reject stale calls under the shared mutex in the queued reload slice. |
| **Actual gap** | `extension_set_model` checks publication separately from a mutation that includes provider construction/persistence, so admission is not atomic. | Split preparation, in-memory commit, and fail-soft persistence; do not hold the session mutex across I/O. |
| **Queued recommendation** | `_ReplLoopStep.step_once` remains the principal high-complexity, wide cross-boundary composition owner. | After reload reconciliation, extract one cohesive effect/input family only; do not optimize line count or move the branch chain intact. |
| **Intentional difference** | `ToolLoopTerminalUi` remains effectful with 43 measured fields. | Retain terminal locks, extension/component calls, filesystem/git inspection, driver effects, and snapshot adaptation in the façade. Move more only when a cohesive independently testable owner appears. |
| **Intentional difference** | The one-shot compatibility runtime remains separate. | Retain it while its metadata-fixture and archive contracts exist; keep canonical provider-turn execution and executable non-equivalence tests. |
| **Intentional difference** | Source packages are strict-equivalent; tests retain their non-strict baseline while combined Mypy checks the complete source+test graph. | Keep this explicit scope. Tightening tests is a separate quality program, not a hidden source exclusion. |
| **Intentional difference** | PTY helpers retain sleeps and deadlines. | Keep them only as bounded read/backoff and process-failure limits; observable bytes and offsets, never sleep duration, sequence actions. |
| **Verified fact** | The sole source suppression is the runtime-selected stdlib HTTP connection subclass in `native/http.py`, with an adjacent Mypy-limitation rationale. | Retain the narrow `misc, valid-type` suppression until the type checker can express the runtime base safely; the count stays ratcheted at one. |
| **Queued recommendation** | Package publication metadata remains provisional while the repository is private. | Keep version/distribution identity, license/URLs, wheel contents, and installed-entry-point verification as release-triggered work. Do not create metadata churn in Slice 16. |

The original Slice 3 ideal transaction is therefore **partially implemented**,
not wholly satisfied. The shipped generation pointer, mutex, publication gate,
atomic thinking/tool mutations, post-selection defaults persistence, and
whole-candidate runtime+flag rejection are real safety ratchets. The missing
staging, sealing, complete projection, operation snapshots, and generation-
bound mutations are a bounded outstanding concurrency contract. That exception
does not make the architecture program as a whole a failure.

### Every remaining C901-pinned file

The directional ratchet has 34 findings in 13 pinned files. Each pin remains
because the file still has the specific ownership below, not merely because an
aggregate baseline exists. No new pin is authorized.

| Pinned file (findings) | Proportional ownership/rationale |
| --- | --- |
| `docs/examples/extensions/answer.py` (2) | A teaching extension intentionally demonstrates one input-hook decision and one render projection in executable form. Keep the example cohesive; simplify only if its demonstrated surface changes. |
| `docs/examples/extensions/pipy-extension-conformance.py` (1) | One golden activation function registers the complete conformance surface in source order. Splitting it would obscure the integrated fixture more than it would reduce product risk. |
| `scripts/parity_checks/extension_message_renderer_conformance.py` (1) | `run_checks` is an end-to-end fixture/process assertion owner. It is test orchestration, not product policy; decompose only with the renderer protocol family. |
| `scripts/parity_lessons.py` (4) | This script owns lesson schema validation, materialization checks, append transitions, and CLI routing. It is a credible future workflow-owner extraction, but not ahead of the reload correctness gap. |
| `scripts/parity_runner.py` (4) | The runner owns no-push guards, lesson gating, one bounded run lifecycle, and CLI selection; `run` remains especially wide. Queue cohesive lifecycle/policy extraction separately from product runtime work. |
| `src/pipy_harness/cli.py` (5) | The top-level entrypoint still composes argparse routing, trust startup, automation selection, extension decisions, and reference-root discovery. Split by command/startup owner when touched; do not replace explicit routing with an untyped context. |
| `src/pipy_harness/native/tool_loop_session.py` (3) | This is the product composition root: `step_once`, run wiring, and settings-dialog effects cross real owner boundaries. The first priority inside it is the reload contract; later extract one cohesive REPL effect family. |
| `src/pipy_harness/native/tui.py` (9) | The façade still coordinates key dispatch, active-turn interruption, settings/custom-editor/component/session-picker effects, and listeners. Pure editor/overlay/chrome/frame state has already moved; remaining complexity is proportionate only while it stays effect orchestration. |
| `src/pipy_session/cli.py` (1) | One secondary metadata-archive CLI routes the closed recorder/catalog command family. A command-table/executor extraction is reasonable if this utility grows, but it is not current product risk. |
| `tests/test_native_agent_loop_policy_adapters.py` (1) | One recursive revalidation test deliberately keeps correlated malformed schema families together so first-failure behavior is visible in one scenario. |
| `tests/test_native_tool_loop_session_fork_clone.py` (1) | One failure-timing test keeps the fork/clone cutoff matrix together to prove later effects do not run after each injected failure. |
| `tests/test_native_tool_loop_session_tree.py` (1) | One resume-switch failure-timing test keeps ordered partial-state and cutoff assertions together; splitting would weaken correlation. |
| `tests/test_parity_probe_trust.py` (1) | One direct probe verifies all workspace extension fixtures declare trust explicitly; the branch set is the test's complete policy inventory. |

## Pi comparison and next selection

At Pi `7df73a00c6cf85c000bf1ce1594c9284067a92f0` / 0.82.0, these are
**actual product gaps**, not work authorized by this assessment:

- constrained sampling and fail-closed capability gates;
- parallel tool execution (Pi defaults to parallel, permits per-tool sequential
  overrides, emits completion events in completion order, and retains result
  artifacts in source order; pipy's canonical loop is sequential);
- canonical retry, compaction-summary retry, usage, and routing/cache lifecycle;
- direct RPC bash correlated updates **and actual cancellation** (pipy's direct
  RPC bash has neither update events nor external cancellation while running);
- provider-owned OAuth/provider families and catalog refresh depth; and
- bounded CLI/editor/extension-presentation deltas.

The following remain **intentional differences**: Python-owned boundaries rather
than TypeScript package compatibility; the narrow `pipy run`/SDK compatibility
runtime; separate full-content private product sessions and metadata-only
workflow archives; and pipy's commit-once normal-buffer frame architecture.
Credential exclusion, project trust, path containment, and catalog-driven
construction remain invariants, not excuses to omit comparable Pi behavior.

Disposition is unambiguous:

1. complete or formally reconcile the bounded transactional reload contract;
2. rerun focused concurrency/extension/TUI evidence and independently review it;
3. then select exactly one product gap using the canonical
   [parity loop](parity-loop/skill-body.md), capability-first while preserving
   Pi command/flag fidelity where user-visible behavior specifies it.

## Verification evidence

Repository-recorded implementation evidence:

- Slice 10 focused gates: 336 agent/session/architecture, 145 SDK/CLI/archive,
  and 291 coding/import/RPC tests; final checkpoint then 4,679 passed / 2
  skipped.
- Slices 11–13 covered terminal-independent owners, façade adapters, import
  boundaries, deterministic renderer behavior, real TUI/PTY paths, and all TUI
  workflow checks. Slice 13's focused renderer group passed 652 tests, all 74
  then-current real-PTY tests, and the full 4,808 / 2 gate.
- Slice 14 passed 20-run focused batches, ten-run complete changed-module
  batches, `just test-pty-smoke` 5/5, and the complete 75/75 real-PTY inventory.
  Linux and macOS real-PTY CI jobs remain configured.
- Slice 15's focused gate passed 7 tests; strict source Mypy was clean across
  169 files, combined Mypy across 437 files, docs built, formatting was clean
  across 479 files, and `just check` reported 4,826 / 2.

Slice 16 implementation verification on the uncommitted documentation diff:

- focused architecture quality/metrics: **8 passed**;
- `uv run mypy --strict src`: clean across **169 source files**;
- `uv run just typecheck`: clean across **437 source/test files**;
- `uv run just check`: Ruff lint and formatting, Mypy, and **4,827 passed / 2
  skipped**;
- `uv run just test-pty-smoke`: **8 passed**; the stronger Slice 14 stress and
  75/75 real-PTY evidence remains the sequencing reliability proof;
- `uv run just docs-build`: no issues;
- `uv run ruff format --check .`: **479 files already formatted**;
- architecture metrics: **34 / 18** C901, **43** fields, **1** suppression,
  **81,738 / 121,052** source/test physical lines, and **5,433 / 6,329** lines
  in `tool_loop_session.py` / `tui.py`;
- both local theme sources: `pi`; `.pre-commit-config.yaml`: absent; and
- `git diff --check`: clean.

Independent Slice 16 review used a fresh read-only Pi reviewer with exact model
`openai-codex/gpt-5.6-sol` at high thinking. Two rounds were valid. Round 1
exited 0 and covered the complete **114,716-byte / 1,407-line**, 14-file patch
through EOF with no Critical or Warning finding and one Suggestion: Tau's tag
had to be written exactly as `v0.3.1`, not `0.3.1`. A fresh exact-model Pi
implementation agent accepted that finding and changed only the assessment
text. One subsequent invocation is recorded as **INVALID** and discarded as
review evidence: it read all 14 files and reported zero findings, but treated
the intentionally unavailable shell/git/hash tools as a scoped omission. The
fresh valid round-2 retry exited 0, covered the complete final **114,722-byte /
1,407-line** patch through EOF with all 14 files visible, reported
`SCOPED_OMISSIONS: none`, and had zero forbidden tool uses, skipped files,
truncations, redactions, Critical, Warning, or Suggestion findings; its
structured result was **CLEAN**. Review stopped because the accepted exactness
finding was fixed and the final complete patch was valid CLEAN, so another
per-slice round would add no material value. The landed Slice 16 commit and its
hash remain pending; the post-commit complete program-range integration review
will cover this mechanical review-ledger synchronization.

## Preserved invariants and metadata disposition

Slice 16 changes durable assessment/documentation and documentation-consistency
coverage only. It changes no product behavior, dependency, schema, public CLI,
JSON/RPC/SDK/provider/session/extension API, event order, import direction,
terminal byte/lifecycle behavior, formatter exclusion, Mypy scope, C901 pin, or
type suppression.

The private native product session remains the full-content source of truth;
the workflow archive remains metadata-only and excludes prompts, provider text,
tool/command output, files, diffs, paths, raw exception text, and credentials by
default. Project trust and path containment remain fail-closed. Catalog-driven
provider construction and credential exclusion remain unchanged. Both local
theme sources must remain `pi`.

`pyproject.toml` already describes the package accurately as a native Python
coding agent with TUI, providers, tools, extensions, and headless automation;
no description edit is warranted. Runtime transports are
standard-library-first and use no third-party provider SDK, while the Codex
WebSocket transport uses the declared `websockets` dependency. Version 0.1.0,
distribution/publication identity, license/URLs, and wheel verification remain
provisional while the project is private.

No changelog or release-note entry applies: Slice 16 records durable assessment,
current documentation, and consistency checks without changing user-visible
product behavior.

# Comparative Review Remediation Plan

Status: landing-gated. While this plan/backlog diff is uncommitted,
implementation is blocked on the required independent plan review. Landing the
planning commit on `main` certifies that the review completed, its material
findings were addressed, and implementation is authorized starting at G0.

Date: 2026-07-30.

This plan turns the surviving findings from a three-way review of `pipy`,
`pi-mono`, and `tau` into small commits. It is subordinate to the
[2026-07-29 architecture assessment](../2026-07-29-architecture-quality-assessment.md):
the bounded transactional-reload correctness boundary must be completed or
formally reconciled before ordinary refactoring or product-parity work begins.
This plan does not treat that boundary as optional.

The plan and backlog update are one planning commit, not an implementation
slice. Before that commit, a fresh Claude Code Opus reviewer must review the
complete docs diff. The default budget is at most two valid docs rounds,
stopping at the first CLEAN result as required by `AGENTS.md`. Round 2 produced
new actionable correctness and process findings about projection-source
deletion, review-cap semantics, and slice execution, so the operator explicitly
authorized one additional docs review of the resulting corrections. That
one-off authorization does not enlarge any later slice's budget. If the
applicable cap is reached with an unresolved Critical, Warning, correctness
contradiction, or other material blocker, report it and do not land the plan.
Thus the pre-commit worktree remains honestly proposed, while the committed
text is self-authorizing evidence that this gate passed.

## Verified baseline and corrections

The following facts were rechecked against current `main` at `622fde1`. Counts
are point-in-time evidence, not permanent quotas.

- `tests/test_architecture*.py` contains 7,082 lines, including 3,694 in
  `test_architecture_import_boundaries.py`. That gate scans direct imports and
  has selected fresh-process anti-laundering tests; its own header explicitly
  says it does **not** compute a general transitive dependency graph. The
  earlier claim of general transitive-laundering detection was too broad.
- The source tree is strict-Mypy clean and retains one explained source
  suppression. The architecture assessment remains the authoritative dated
  measurement rather than this plan restating all of its frozen counts.
- The real-PTY inventory is eleven product test modules plus
  `tests/test_pty_sync.py`; matching those surfaces currently totals 6,350
  lines. The test tier is a strength and remains mandatory for reload or
  terminal-affecting slices.
- `tests/test_architecture_quality_gates.py` requires a fixed closeout
  vocabulary across seven historical/living documents. It includes test counts,
  line counts, C901 totals, field counts, commit facts, review facts, and nav
  presence. Product changes therefore incur unrelated historical prose edits.
- `docs/index.md` contains 11 `CLEAN` tokens and three `reviewed endpoint`
  phrases. The exact A-G closeout measurement string appears in seven
  established documents.
- Ruff currently reports 198 `I001` findings repository-wide: 62 in `src`, 129
  in tests, and seven in scripts. It also reports 18 `UP035`, 13 `B905`, and one
  `B008` repository-wide. Selecting broad `B` would additionally surface many
  `B009`/`B010` and test-only findings, so this plan does not pretend that
  enabling all of `B` is a ten-line change. The `B008` default is a frozen
  `ProviderTurnDeltaPolicy`, not a shared mutable object; changing it is lint
  hygiene, not a latent-state bug.
- `production_tool_registry()` exposes nine names. The two extra names are
  `edit_diff` and `truncate`; Pi exposes seven agent tools. Removing either is a
  user-visible behavior change, not a mechanical helper move.
- `native/tool_loop_session.py` still publishes extension runtime/flags, tool
  capability, renderer, lifecycle, provider, menu, and chrome effects in
  separate steps. It clears live chrome before candidate flag validation.
  `SessionGenerationRef.snapshot()` documents that production adoption is
  pending, class-A ports do not capture a generation id, and `set_model`
  admission remains non-atomic. These are current-code facts and are the first
  implementation priority.
- The repeated six provider test names do occur in eight provider files, but
  shared names do not prove equivalent wire contracts. In particular, Azure
  Responses cases are not interchangeable with chat-completions cases.
  Parameterization would reduce source duplication, not the number of collected
  provider scenarios.
- There are no tags, the changelog has one long Unreleased section, and the
  README retains publication placeholders. The dated assessment explicitly
  keeps distribution identity, license/URLs, and release verification
  provisional while the repository is private. This plan therefore does not
  invent an owner, distribution name, or release date.
- The observed `.venv/bin` failure involved the ignored local Claude hook
  command and concurrent `uv run` processes. It is evidence of an invocation
  hazard, but not yet a reproducible pipy product defect. No implementation
  slice may deliberately race the shared worktree's real `.venv`.

Summary-safe archive searches for `transactional reload` and `comparative
review` returned no matching records. The recent session list exposed metadata
and summary availability only; no raw transcript body was inspected.

### Bounded reload vocabulary

The authoritative definitions are in the transactional spec's
[Straggler-reachable mutation ports](../specs/2026-07-25-transactional-extension-reload-rebuild.md#straggler-reachable-mutation-ports)
and [What a generation is](../specs/2026-07-25-transactional-extension-reload-rebuild.md#what-a-generation-is)
sections. In this plan, **class A** means generation-bound session-state mutation
ports whose liveness/gate check and mutation are atomic under the session mutex;
**class D** means activation-scoped contribution registrations that are sealed
when a candidate is frozen or disposed; and **queue sidecars** means the
generation-owned mutable user/custom-message and notification queues (alongside
the separately named chrome sidecar), which only the live generation drains.

## Program invariants

Every implementation slice preserves these unless its **Scope** explicitly
names and tests a behavior change.

1. Project trust, path containment, provider/tool admission, credential
   exclusion, and the separation between full-content private product sessions
   and metadata-only workflow archives remain fail-closed.
2. CLI text, JSON/RPC schemas, provider wire requests, session formats, event
   ordering, extension contracts, terminal bytes/lifecycle, and command
   precedence do not change accidentally.
3. The single session mutex is taken by every reader and writer of any guarded
   field. No check-then-mutate window, lost update, callback, provider
   construction, rendering, filesystem I/O, or finalizer release is permitted
   under that mutex. A candidate-registration guard or candidate sink-local
   guard never nests with the session mutex in either acquisition order: code
   must snapshot/seal under the candidate guard, release it, and only then enter
   a separate session-mutex publication phase (with cleanup after both).
4. No runtime dependency, unchecked `Any`, unexplained suppression, Mypy
   exclusion, new C901 pin, deprecation alias, or compatibility re-export is
   added. Pipy-only surfaces are removed outright when realigned to Pi.
5. Mechanical moves, lint autofixes, behavior-sensitive changes, lint-rule
   enablement, and historical-doc cleanup are separate commits. A line-count
   reduction is never acceptance by itself.
6. A later slice may split only by revising this plan first. It may not quietly
   widen its named owning-module, touched-family, file, or behavior bound while
   implementing.

## Universal slice and commit gate

This is a universal gate, **not a final numbered slice**. Apply it to every
slice below.

1. Start a fresh Pi implementation agent on clean `main`; implement only the
   named slice and update the single-line **Active/next slice:** field in the
   landing-gated program section of `docs/backlog.md`. That labeled backlog
   status edit is implied for **every** slice even when its local docs/release
   disposition does not repeat `backlog`; do not rewrite the adjacent landing
   certification prose.
2. Add characterization before changing behavior. Run the listed focused
   checks, `git diff --check`, `just check`, and `just docs-build`. Run
   `just test-pty-smoke` plus the relevant real-PTY module for reload, chrome,
   input, renderer, or terminal work. If `.pre-commit-config.yaml` appears,
   also run `prek run --all-files`.
3. State the docs and release-note disposition in the diff. User-visible
   behavior changes update the relevant user/parity docs and `CHANGELOG.md` in
   the same commit. Mechanical/test-policy/docs-only slices explicitly record
   that no release note applies.
4. After gates pass, use a fresh independent Claude Code Opus context to review
   the complete exact diff, with no raw-session input and no implementer
   self-grading. Respect `AGENTS.md`: at most two valid rounds for docs-only
   slices and at most three for code slices; stop at the first CLEAN round.
   A shared-mutable-state correctness finding never grants an extra review
   round automatically: if unresolved at the cap, it blocks commit and all
   dependent work and forces contract/plan revision or a stop. Only explicit
   operator authorization based on new, actionable, materially valuable
   feedback may extend the review budget.
5. Fix material findings, rerun all gates, and review the changed diff again.
   A slice may proceed to commit only when the latest valid review has no
   unresolved Critical, Warning, or other material blocker. If the review cap
   is reached without that state, **do not commit**: revise this plan, re-split
   the slice, or stop the program as appropriate, then report the blocker.
   Where a slice's own acceptance requires `CLEAN`, only an explicit valid
   `CLEAN` satisfies it.
6. Only after step 5 permits landing, commit the unchanged, green, reviewed diff
   directly to `main` as exactly one commit. Do not amend, rebase, push, create
   a branch, or bundle the next slice.

## Dependency order

`G0` is a test-policy prerequisite. It may precede reload work because it
changes no product behavior and removes an unrelated seven-document tax from
the correctness slices. `R0` through `R7` then close the assessment's mandatory
reload boundary. The mandatory order is `G0` → `R0` → `R1` → `R2` → `R3` →
`R4a` → `R4b` → `R4c` → `R5` → `R6` → `R7`; none may be reordered or run in
parallel. No `D`, `L`, `P`, `A`, `T`, or `C` slice may begin until `R7` records
completion or an independently reviewed formal reconciliation that proves an
alternative contract. Merely writing `R0` does not clear the gate.

The queue contains exactly **26 numbered execution slices**: G0; ten ordered
reload slices (R0, R1, R2, R3, R4a, R4b, R4c, R5, R6, R7); D1; nine lint slices
(L1-L9); P1-P2; A1; T1; and C1. The planning commit itself and the universal
gate are not additional slices.

### G0 — retire frozen closeout synchronization

**Kind:** test-policy behavior; no product behavior.

**Scope:** In `tests/test_architecture_quality_gates.py`, remove the commit,
partition, stable-metric, stale-status, reviewed-endpoint, and seven-document
synchronization machinery. Retain the formatter/local-CI contract tests. Move
the two README WebSocket/dependency assertions to a narrowly named README test;
do not retain nav or revision identity assertions. Add one sentence to the
dated assessment saying its measurements are a point-in-time snapshot and are
not test-enforced. Preserve all historical facts.

**Acceptance:** the focused test module passes; grep scoped to `tests/` and
`src/` finds no `STABLE_VERIFICATION_FACTS`, `PARTITION_FACTS`, or
`test_architecture_program_closeout_ledgers_stay_synchronized`; changing a
metric sentence in a temporary copy of a living doc cannot fail a test.

**Docs/release/commit:** assessment and backlog only; no changelog entry.
Commit: `test: retire frozen architecture closeout metrics`.

### R0 — reconcile the bounded reload contract

**Kind:** docs/spec decision; mandatory correctness planning.

**Scope:** Re-audit the current implementation against
`docs/specs/2026-07-25-transactional-extension-reload-rebuild.md`. Add a current
clause-disposition table to that spec: `landed`, `required in R1-R6`, or
`formally narrowed`, with code/test evidence for every narrowed clause. The
minimum required set cannot be narrowed away: rejected activation must preserve
old chrome, timed-out/rejected registration must be sealed/disposed, published
extension projections must be coherent, operations must use one snapshot,
class-A ports must reject stale generations atomically, and `set_model` must
separate fallible preparation/in-memory commit/fail-soft persistence. The audit
must enumerate the complete current class-A port inventory by port and owner.
The expected inventory is the three families planned below:
`set_active_tools`, `set_thinking_level`, and `set_model`. If any other class-A
port exists, stop before code and revise/re-split this plan, including the
reported slice count and dependency order, rather than silently assigning it to
R5 or R6.

The decision must explicitly settle which settings/resources and queue-sidecar
clauses remain transactional. For settings, it must record exactly one
evidence-backed disposition without pre-deciding it here: if settings remain
transactional, R3 must include an immutable settings projection supplied by an
allowed session/settings-owned adapter with no extension-boundary reverse
import, and R4a must consume that projection; if settings are formally narrowed,
R3 must omit that projection and R4a must not consume settings from the
generation snapshot. It must also record the non-nesting protocol from
invariant 3 for candidate registration/sink guards and the session mutex,
including the seal/snapshot, publication, and cleanup phase order. A formal
narrowing is valid only if the current single-session-thread and detached-worker
reachability proof shows no lost update, torn read, or lock cycle. It may not
relabel an observed failure as intentional.

**Acceptance:** every `Actual gap` in the assessment's correctness-residual
table maps to one of R1-R6; the complete class-A inventory names every port and
owner and confirms it is exactly the three expected families, otherwise the
plan, dependency order, and slice count are corrected before code; lock
ownership and all readers/writers are named; the settings disposition selects
and evidences exactly one of the two R3/R4a paths above, with no unresolved
placeholder; each R1-R6 sub-slice stays within its stated owning-module/
touched-family bound and one concurrency mechanism; the acquisition graph
proves candidate guards and the session mutex cannot nest in either direction;
two reports of the same shared-state defect force another contract revision
before code, not automatic review rounds beyond the cap.

**Docs/release/commit:** transactional spec, architecture/backlog pointers; no
changelog entry. Commit: `docs: reconcile reload completion contract`.

### R1 — seal candidate contribution registration

**Kind:** concurrency behavior at the activation boundary.

**Scope:** Give candidate activation a candidate-owned registration host with a
one-way sealed/disposed state under its own guard. Seal before harvesting a
successful candidate and before disposing a timeout/rejection. Late class-D
registrations must fail closed and cannot enter either the candidate projection
or the live runtime. Do not integrate chrome publication or alter live
selection in this slice.

**Bound:** production edits are limited to the activation/registration-host
owner and reload composition adapter family; live chrome, selection, provider,
and consumer-dispatch owners are out of scope.

**Acceptance:** deterministic tests pause an activation worker, time it out,
seal/dispose the host, then release the worker and prove late commands, tools,
providers, hooks, renderers, flags, shortcuts, and listeners are absent.
Instrumented guards fail on attempted candidate-guard/session-mutex nesting in
**both** directions, and barrier tests cover registration-versus-seal and
seal-versus-publication order. No session mutex is held while extension code
runs or cleanup callbacks execute.

**Docs/release/commit:** update the transactional clause table and extension
implementation notes; add a changelog fix entry for rejected/timed-out late
registration. Commit: `fix: seal rejected extension activation`.

### R2 — stage chrome and dispose rejected candidates

**Kind:** user-visible correctness fix.

**Scope:** Stop clearing live TUI chrome before activation/flag validation.
Collect candidate chrome/listener requests in candidate-owned sinks. On
rejection, close/dispose those sinks without delivery; on acceptance, reconcile
new chrome only after the candidate is committed. Preserve the prior chrome
when any fallible candidate step fails.

**Bound:** production edits are limited to candidate chrome/listener sinks,
reload orchestration, and the TUI chrome reconciliation adapter; other
contribution projections and selection state are out of scope.

**Acceptance:** captured and real-PTY tests prove (a) invalid flags and injected
activation failure retain old title/widgets/listeners, (b) rejected candidate
chrome never paints, and (c) successful removal clears old chrome exactly once
after acceptance. `just test-pty-smoke` and the focused chrome PTY module pass.

**Docs/release/commit:** update extension/TUI behavior docs and add a changelog
fix entry. Commit: `fix: stage extension chrome until reload commit`.

### R3 — build one immutable extension projection

**Kind:** ownership/concurrency foundation; no intended visible behavior.

**Scope:** Extend the session-owned generation value (not
`extension_runtime.py`) with immutable, candidate-built projections required by
current consumers: runtime/flags, commands/menu/descriptions/shortcuts,
lifecycle and request hooks, extension tool ports plus candidate capability
state, renderer mappings, provider contributions, and candidate-owned sidecar
handles. If R0 retains settings transactionally, also include an immutable
settings projection supplied by an allowed session/settings-owned adapter; if
R0 formally narrows settings, omit that projection. Build and validate the whole
value before publication. This slice constructs the value and tests it; it does
not yet switch all live consumers.

**Bound:** production edits are limited to the session-generation value,
candidate projection builder, and their composition/adaptation points, plus,
only under R0's retained-settings disposition, the allowed
session/settings-owned adapter that supplies the immutable settings projection.
Existing command, request, tool, renderer, provider, menu, lifecycle, and
chrome consumer families remain unchanged.

**Acceptance:** injected failure at each projection builder leaves the live
reference and every existing live adapter unchanged; retained old projections
share no mutable mapping/list with the candidate except explicitly reconciled
sidecars; for every projection family, a separate characterization arm proves
the newly built value is equivalent to what the still-live legacy source and
adapters observe from the same candidate. Each arm remains a required gate
until the slice in which that family's last legacy consumer moves and its
legacy source is deleted; that same slice removes the arm as part of the proven
deletion. No arm may disappear before its last legacy consumer moves and its
legacy source is deleted, and R4c removes only the final remaining arms. Under
R0's retained-settings disposition, the immutable settings projection has the
same isolation/equivalence characterization, is supplied through the bounded
adapter, and adds no extension-boundary reverse import; under the narrowed disposition,
the generation value, builder, and characterization arms contain no settings
projection. The extension boundary gains no provider/TUI reverse import.

**Docs/release/commit:** update architecture/spec ownership; no changelog entry.
Commit: `refactor: build immutable reload projections`.

### R4a — snapshot command and request-hook operations

**Kind:** concurrency behavior; intended external behavior is coherence only.

**Scope:** Convert extension command, shortcut, input, before-agent,
before-provider, tool-result, and session-gate dispatch to take one R3 snapshot
at operation start and use its runtime, flags, and sidecars throughout. Under
R0's retained-settings disposition, each applicable operation also uses the
snapshot's immutable settings projection; under the narrowed disposition, R4a
does not consume settings from the generation snapshot. R3 must have built every
applicable snapshot field before R4a begins. If a converted family's separately
refreshed legacy source has no consumer left, delete that source and remove only
its corresponding R3 equivalence arm in this same slice; every arm backed by a
still-consumed source remains a gate. Do not convert tool execution/rendering,
provider binding, menu, lifecycle, or chrome in this slice.

**Bound:** production edits are limited to command/shortcut/input dispatch,
request/session-hook dispatch, and their snapshot adapter, plus the reload
publication/composition owner strictly for deletion of a converted family's
legacy source after its last consumer moves. Tool, renderer, provider, menu,
lifecycle, and chrome consumers are out of scope; R3's projection builder and
settings-supplying adapter are out of scope.

**Acceptance:** barrier tests publish between reads for every converted family
and each operation reports one generation id with matching flags/hooks/sidecar
and, only under R0's retained-settings disposition, settings; under the narrowed
disposition no converted path reads a settings view from the generation
snapshot. No converted path reads `generation_ref.current` twice or retains a
separately refreshed hook/flag map. For each converted family whose last legacy
consumer moves, the proof names that consumer, deletes the legacy source through
the bounded publication/composition-owner edit, and only then removes that
family's R3 equivalence arm; no other arm is removed.

**Docs/release/commit:** architecture/spec table; no changelog because only torn
mixed-generation observations are removed. Commit:
`refactor: snapshot extension dispatch operations`.

### R4b — snapshot tool, renderer, and provider projections

**Kind:** concurrency behavior; intended external behavior is coherence only.

**Scope:** Make tool advertisement/execution, renderer selection, and extension
provider contribution/refresh consume the R3 snapshot's candidate-built values.
After the last consumer of each corresponding separately published map/state
moves, delete that legacy source and remove that family's R3 equivalence arm in
the same proven deletion. No tool, renderer, or provider arm may be removed
before its source and last consumer; all arms for legacy sources outside these
families remain gates. Provider selection and coding-state rebinding keep the
R0 contract and no provider construction occurs under the session mutex. Do
not include class-A mutation admission.

**Bound:** production edits are limited to the tool advertisement/execution,
renderer selection, and provider contribution/refresh consumer families plus
their snapshot adapters, and to the reload publication/composition owner
strictly for deletion of a converted family's legacy source after its last
consumer moves. Class-A mutation, menu, lifecycle, and chrome are out of scope.

**Acceptance:** a tool call and provider turn spanning reload retain their
advertised tool/renderer/provider generation; a later turn sees the new one;
injected projection failure leaves old capability/renderer/provider consumers
unchanged; each tool, renderer, and provider equivalence arm is removed only
with proof that its last legacy consumer moved and its legacy source was
deleted, while every arm whose legacy source remains still passes; focused
provider, tool, renderer, and real-PTY rendering suites pass.

**Docs/release/commit:** architecture/spec table; no separate changelog entry
because R4b is an internal prerequisite to the coherent-reload fix recorded at
R4c. Commit: `refactor: snapshot extension execution projections`.

### R4c — publish menu, lifecycle, and chrome from one generation

**Kind:** concurrency behavior; intended external behavior is atomic coherence.

**Scope:** Convert menu/descriptions/shortcuts, lifecycle emitter inputs, and
R2 chrome sinks to the R3 snapshot, then make reload's semantic commit solely a
non-fallible generation-pointer publication under the session mutex. After its
last consumer moves, delete the final separately refreshed contribution source
and remove only the final remaining R3 equivalence arms as part of that proven
deletion. Post-commit paint, diagnostics, lifecycle notification, and
persistence remain fail-soft and outside the mutex.

**Bound:** production edits are limited to menu/description/shortcut consumers,
lifecycle emitter inputs, chrome reconciliation, and the reload publication
composition owner. Tool/provider/request dispatch and class-A mutation are out
of scope.

**Acceptance:** barrier tests across all R4a-R4c families observe one old or one
new generation id, never mixed flags/tools/renderers/hooks/providers/menu/
chrome; commit critical-section tests prove no I/O, callback, rendering,
construction, or last-reference release occurs under the lock; successful and
rejected reload PTY behavior remains correct. Only the final remaining R3
equivalence arms are removed here, each after this slice proves its last legacy
consumer moved and its corresponding legacy projection source was deleted;
arms already removed with proven R4a/R4b family deletions are not recreated.

**Docs/release/commit:** architecture/spec table; extend the reload fix entry
to state that accepted generations publish coherent tool/provider/render/UI
projections. Commit: `refactor: publish one extension generation snapshot`.

### R5 — bind active-tool and thinking mutations to a generation

**Kind:** concurrency correctness behavior.

**Scope:** Contexts/ports for `set_active_tools` and `set_thinking_level` capture
the creating generation id. Under the shared mutex, compare id and publication
gate and apply the complete in-memory mutation in the same critical section.
Move diagnostics, persistence, tree writes, footer updates, and callbacks after
unlock. Do not include `set_model`.

**Bound:** production edits are limited to active-tool/thinking context and port
owners, their guarded selection-state owner, and post-lock presentation/
persistence adapters; model/provider construction is out of scope.

**Acceptance:** stale and publication-pending calls return `False` and change
nothing; a call admitted before gate-open survives publication; every reader
and writer of the guarded selection uses the same mutex; no slow/arbitrary
work or displaced-value destruction occurs under it.

**Docs/release/commit:** extension API concurrency semantics and spec table;
changelog fix entry if stale-call behavior was previously observable. Commit:
`fix: reject stale extension selection mutations`.

### R6 — make model mutation admission atomic

**Kind:** concurrency correctness behavior.

**Scope:** Split extension `set_model` into fallible provider/catalog
preparation outside the mutex, an expected-generation/gate recheck plus
non-fallible in-memory provider/coding-state commit under the mutex, and
fail-soft default persistence/presentation after unlock. A stale or gated
candidate returns `False`; prepared values cannot overwrite a newer selection.
Do not hold the session mutex across provider construction or file I/O.

**Bound:** production edits are limited to the extension model-mutation port,
provider/catalog preparation adapter, guarded provider/coding selection owner,
and post-lock persistence/presentation adapter; no other class-A port changes.

**Acceptance:** deterministic barriers cover stale-after-prepare,
gate-open-during-prepare, provider-construction failure, persistence failure,
and successful commit. History, usage, compaction, provider binding, selection,
and defaults retain the reconciled contract; failure diagnostics contain no
credential/private detail.

**Docs/release/commit:** extension/provider docs and changelog fix entry. Commit:
`fix: commit extension model changes atomically`.

### R7 — close the reload correctness boundary

**Kind:** integration tests and durable reconciliation; no new mechanism.

**Scope:** Run the R0 scenario matrix over success, rejection, timeout,
cancellation stragglers, teardown, and post-commit failure. Add only missing
integration characterization. Update the transactional spec, architecture,
assessment follow-up note, and backlog to state exactly what shipped and what
was formally narrowed. Do not add a new concurrency abstraction in closeout.

**Acceptance:** focused extension/reload/concurrency suites, relevant real-PTY
modules, `just test-pty-smoke`, `just check`, and docs build pass; independent
Opus review is explicitly `CLEAN` with no skipped concurrency surface; the dated
assessment's six residuals are individually closed or linked to an explicit
proved reconciliation. If the review cap is reached without `CLEAN`, R7 does
not commit, the reload boundary remains open, and the plan must be revised,
re-split, or stopped and reported. Only a committed R7 may authorize `D1`.

**Docs/release/commit:** closeout docs; changelog only for behavior not already
recorded in R1-R6. Commit: `docs: close transactional reload boundary`.

### D1 — make the documentation entry point reader-facing

**Kind:** docs/navigation behavior.

**Scope:** Rewrite `docs/index.md` around what pipy is, install/first run, user
guides, and the seven behavioral-contract documents. Remove review verdicts,
endpoint hashes, and partition ledgers from the entry page. Replace the copied
A-G block in `docs/architecture.md` with a link to the dated assessment. Remove
Assessment, Backlog, and Architecture Migration from `zensical.toml` navigation
without claiming that nav removal unpublishes them; site search inclusion is a
separate deferred decision. Keep the dated assessment intact.

**Acceptance:** zero `CLEAN` or `reviewed endpoint` tokens in `docs/index.md`;
the historical partition string remains only in historical closeout documents;
`just docs-build` passes; quickstart, usage, providers, settings, and sessions
user guides are unchanged.

**Docs/release/commit:** docs/config only; no changelog entry. Commit:
`docs: restore a reader-facing project index`.

### L1 — normalize source import order

**Kind:** mechanical Ruff autofix; no behavior.

**Scope:** Apply only Ruff `I001` fixes to `src/` (62 findings at baseline). No
rule enablement and no manual cleanup.

**Acceptance:** `uv run ruff check --select I001 src` is empty; source diff is
import-only; full gate passes.

**Docs/release/commit:** backlog only; no changelog. Commit:
`style: normalize source import order`.

### L2 — normalize every non-source Python import block

**Kind:** mechanical Ruff autofix; no behavior.

**Scope:** Apply only `I001` fixes to every tracked Python path outside `src/`:
tests (129 baseline), scripts (seven baseline), and any root-level or other
tool/config Python path found by `git ls-files '*.py'`. Do not mix source or
configuration edits.

**Acceptance:** after L1, `uv run ruff check --select I001 .` is empty; every
changed file is in the tracked non-source inventory and its diff is import-only;
full gate passes.

**Docs/release/commit:** backlog only; no changelog. Commit:
`style: normalize non-source import order`.

### L3 — enable the exact import-order gate

**Kind:** quality-gate configuration; no product behavior.

**Scope:** Add exact rule `I001`, not category `I`, beside `C901` in Ruff
`extend-select` and update the adjacent configuration comment. Rule enablement
is the only intended code/config change; if import drift appeared after L2,
stop and create a separately reviewed mechanical slice rather than mix it here.

**Acceptance:** `uv run ruff check --select I001 .` and `just lint` pass; a
focused configuration test proves `I001` remains selected without enabling
`I002`.

**Docs/release/commit:** contributor command wording if needed; no changelog.
Commit: `chore: enforce Ruff import ordering`.

### L4 — modernize deprecated import forms

**Kind:** mechanical Ruff autofix; no behavior.

**Scope:** Apply only the 18 baseline `UP035` fixes repository-wide. Do not
change the B008 default or lint configuration.

**Acceptance:** `uv run ruff check --select UP035 .` is empty; the diff changes
only import spellings with no name/owner change; full gate passes.

**Docs/release/commit:** backlog only; no changelog. Commit:
`style: modernize deprecated imports`.

### L5 — make the frozen policy default explicit

**Kind:** behavior-sensitive lint hygiene; no intended behavior.

**Scope:** Replace the one `B008` frozen `ProviderTurnDeltaPolicy` call default
with an explicit typed immutable default such as a module singleton. Do not
apply UP035 fixes or enable a rule.

**Acceptance:** default value, identity assumptions, explicit override, and
provider-turn behavior are characterized before the rewrite;
`uv run ruff check --select B008 .` is empty; full gate passes.

**Docs/release/commit:** backlog only; no changelog. Commit:
`refactor: make the provider turn policy default explicit`.

### L6 — enable modern-import and safe-default gates

**Kind:** quality-gate configuration; no product behavior.

**Scope:** Enable exactly `UP035` and `B008`, not broad `UP` or `B`, and update
the adjacent configuration comment. Do not include autofixes or default
rewrites; unexpected drift returns to a new separately reviewed slice.

**Acceptance:** both exact-rule checks and `just lint` pass; a focused
configuration test freezes both exact rules without broad categories.

**Docs/release/commit:** backlog/config comment; no changelog. Commit:
`chore: enforce modern imports and safe defaults`.

### L7 — preserve intentional zip truncation

**Kind:** behavior-preserving explicitness; no rule enablement.

**Scope:** Add `strict=False` only at the five baseline sites whose contract is
deliberate shortest-input or adjacent-pair iteration: one export-distribution
conformance site, two terminal-comparison sites, one terminal-screen adjacency
site, and one export-distribution test site. Explain the unequal-length
contract next to each family. Do not touch the eight refusal sites.

**Acceptance:** unequal-length characterization proves each family retains its
existing shortest-input/adjacent-pair result; exactly those five baseline
findings disappear; full gate passes.

**Docs/release/commit:** backlog only; no changelog. Commit:
`style: preserve intentional zip truncation`.

### L8 — reject mismatched paired assertions

**Kind:** behavior-sensitive harness/test contract; no product behavior.

**Scope:** Add `strict=True` to the remaining eight baseline B905 sites, bounded
to four files/families: extension-package conformance (one), agent-event
identity assertions (one), coding-product-session identity assertions (five),
and model-selector row pairing (one). Add unequal-length characterization
before each family changes. Do not edit product zip loops or lint configuration.

**Acceptance:** all four families fail deterministically rather than silently
truncate on unequal lengths; the equal-length scenarios remain unchanged;
`uv run ruff check --select B905 .` is empty; full gate passes.

**Docs/release/commit:** backlog only; no changelog because only harness/test
assertion behavior changes. Commit: `test: reject mismatched paired assertions`.

### L9 — enable the zip-length gate

**Kind:** quality-gate configuration; no product behavior.

**Scope:** Enable exactly `B905`, not broad `B`, and update the adjacent
configuration comment. Do not include site changes; unexpected drift returns to
a new separately reviewed behavior-preserving or behavior-changing slice.

**Acceptance:** `uv run ruff check --select B905 .` and `just lint` pass; a
focused configuration test freezes exact `B905` selection without broad `B`.

**Docs/release/commit:** backlog/config comment; no changelog. Commit:
`chore: enforce explicit zip length contracts`.

### P1 — remove the agent-visible `truncate` tool

**Kind:** intentional product-parity behavior change.

**Scope:** Remove `truncate` from the production registry, prompts/catalog
inventories, render policy, docs, and tests. Because no internal production
consumer uses `TruncateTool`, delete the pipy-only tool module/class and its
tool-specific tests rather than leaving a dead helper or alias. Keep the
harness's independent automatic output bounding unchanged.

**Acceptance:** production requests never advertise or dispatch `truncate`;
automatic read/bash/provider output bounds retain focused coverage; no
`TruncateTool` symbol or tool-schema claim remains.

**Docs/release/commit:** update parity/user tool docs and changelog removal
entry. Commit: `refactor: remove the model-visible truncate tool`.

### P2 — remove the second agent-visible edit path

**Kind:** intentional product-parity behavior change.

**Scope:** Remove `edit_diff` from the production registry and all model-visible
inventories. Its unified-diff implementation has no non-tool production
consumer, so delete it and its tool-only tests outright under the no-deprecation
policy. Keep `edit` as the sole edit tool and preserve its path/trust policy.

**Acceptance:** the production registry has exactly the seven-name set
`{read, ls, grep, find, write, edit, bash}`, with the pre-existing relative
order of those surviving tools preserved; a single characterization freezes
both the set and relative-order preservation. Any reorder requires a revised
plan that explicitly names and verifies that provider-visible behavior change.
Provider tool schemas, prompts, extension reserved names, render policy, and
parity docs agree; no `EditDiffTool` or compatibility alias remains.

**Docs/release/commit:** update tool/parity docs and changelog removal entry.
Commit: `refactor: match Pi's seven agent tools`.

### A1 — extract the agent-turn status effect family

**Kind:** behavior-preserving ownership refactor.

**Scope:** Move the cohesive status callbacks currently nested in
`_ReplLoopStep.step_once`—run entered, input accepted, result observed,
cancellation observed, tool-policy sync, provider success/failure,
no-tool-assistant, and malformed-fatal—behind one typed collaborator. Keep
accepted-input preparation, provider-turn construction, local input dispatch,
and run coordination in place. Do not set a line-count or C901-removal quota.

**Acceptance:** the root no longer defines those nested callbacks; the new owner
has only the narrow state/presentation ports it needs and no provider transport,
extension activation, package, or concrete TUI import; event, history, prompt
recall, pending-input, diagnostic, footer, and cancellation ordering are
characterized in captured and PTY paths.

**Docs/release/commit:** architecture/backlog; no changelog because behavior is
unchanged. Commit: `refactor: extract agent-turn status effects`.

### T1 — share only the proven provider test contract

**Kind:** test-only refactor.

**Scope:** Introduce a small parameterized contract for the six genuinely
near-verbatim chat-completions scenarios and migrate only Mistral and
Cloudflare first. Preserve provider-specific request/header/wire assertions in
their original files. Do not migrate Azure Responses or infer equivalence from
a shared test name.

**Acceptance:** the twelve Mistral/Cloudflare scenarios still collect and run
independently with readable provider ids; each failure identifies the provider
and scenario; no production file changes; collected scenario count does not
drop. Recount the remaining clones before proposing another migration.

**Docs/release/commit:** backlog only; no changelog. Commit:
`test: share provider completion contract cases`.

### C1 — add a human contributor path

**Kind:** docs/process only.

**Scope:** Add `CONTRIBUTING.md` covering setup, trunk-based work on `main`,
architecture/import boundaries, focused tests, `just check`, docs build,
review-budget rules, and privacy/trust invariants. Link it from README. Do not
invent security contacts, CODEOWNERS identities, or publication policy.

**Acceptance:** commands work from a clean checkout; contributor prose agrees
with `AGENTS.md` and executable boundary tests; docs build and full gate pass.

**Docs/release/commit:** contributor docs only; no changelog. Commit:
`docs: add the human contribution workflow`.

## Deferred or rejected recommendations

These are not implementation slices, so “implement every resulting slice” does
not authorize them.

- **Release workflow, metadata, changelog closure, and tag:** wait for an
  operator to provide an owned distribution name, release trigger, author and
  security identities, and publication decision. Then write a separate release
  plan; do not fabricate `v0.1.0` or publish private code from this plan.
- **Mass sub-packaging of 87 flat native modules:** flat count alone is not an
  ownership defect, existing `agent/providers/tools/coding/automation/ui`
  namespaces are already gated, and one-release re-exports would violate the
  no-deprecation policy. Move a module only with a cohesive owner slice and
  remove old paths outright; no arbitrary “15 top-level modules” target.
- **Whole `tool_loop_session.py` decomposition:** superseded by bounded A1.
  Extracting ten closures, rewriting `run`, splitting `step_once`, and severing
  all TUI wiring in one commit is not mechanical or reviewable. Reassess after
  A1 from measured ownership, not a 120-line quota.
- **TUI decomposition:** retain the assessment's intentional-disposition.
- **Broad Ruff `B`, `UP`, or `SIM` adoption:** only exact `I001`, `UP035`,
  `B008`, and `B905` enablement in L3, L6, and L9 is authorized. Remaining
  cosmetic findings need a separate mechanical plan.
- **Remaining provider-test migrations:** T1 is a two-provider proof. Migrate
  another wire family only after a fresh equivalence inventory; do not optimize
  collected test count.
- **Claude hook concurrency:** first reproduce in a disposable copied project
  and disposable virtual environment. If reproducible, separately plan a
  tracked invocation using a non-syncing/installed executable path and update
  README/session-storage examples. Never race or repair the shared `.venv` as a
  test.
- **SECURITY, CODEOWNERS, Dependabot, issue templates:** require operator-owned
  contacts/ownership and a public-contribution decision. C1 deliberately does
  not guess them.
- **Release-triggered package breadth, MCP, provider breadth, and cosmetic
  metrics:** remain outside this remediation program.

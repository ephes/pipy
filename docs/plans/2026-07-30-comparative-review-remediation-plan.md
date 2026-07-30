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
new actionable shared-state correctness findings about candidate-host guard
ownership, exhaustive queue writers, terminal mutation scope, and closed-sink
refusal shapes, so the operator explicitly authorized one additional docs
review of the resulting corrections. That one-off authorization does not enlarge
any later slice's budget. If the
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
- The R0 re-audit corrected current ownership assumptions.
  `extension_loader._drive_awaitable()` joins its private `pipy-ext-activate`
  worker without a timeout, but R1 remains present correctness because retained
  post-activation `register_*`/`on` calls appear to succeed into harvested host
  state. R1 guards all staged fields and makes every late class-D call raise
  `ExtensionCapabilityError` at its call boundary. Separately, a retained API's
  cancellable `pipy-tool-call` worker can race the session worker's outbox copy/
  clear; R3b/R3c/R4a own the complete generation-outbox writer/drain set. Accepted
  staged custom messages and `ExtensionCodingSessionControl` instead target
  provider, durable tree, rendering, and `CodingInputQueue` sinks. A retained
  control can race live session/RPC use: `NativeSessionTree` releases its partial
  `_write_lock` before `_write_entry()` and `CodingInputQueue` has no guard. R5
  is split. R5a promotes the existing per-run `mutation_io_lock` into one
  coding-effect coordinator adopted by active-tree pointer access, every mutable
  tree/input API, retained coding-session writers, and terminal teardown; R5b
  keeps active-tool/thinking generation admission bounded. Closing a retired
  generation outbox still changes no delivery, while R4a's live append-erasure
  fix remains user-visible. The class-A count stays three. The corrected R3
  split below increases the queue from 27 to exactly 29 slices.
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
when a candidate is frozen or disposed; and **queue sidecar** means the
user/custom-message queue owned by one generation. Notifications remain
immediate effects under R0, and chrome is a separately named generation
sidecar.

### R3 split amendment (main `606a860`)

The uncommitted R3 attempt is evidence, not a shipped slice. It successfully
materializes the projection builder and broad equivalence coverage, but it also
changes reload/startup orchestration and claims R3 complete. Closure work proved
that those two families cannot land coherently together:

1. activation-staged user/custom messages are accepted-only;
2. the `606a860` observable reload order delivers accepted staged custom
   messages before replacement `session_start`;
3. replacement `session_start` is what populates the exact detached R2 chrome
   sidecar;
4. provider/catalog/fallback, coding, and capability preparation must then
   complete against detached owners, because their current live mutation has no
   narrow reliable undo;
5. the complete prepared value freezes once after those mutable builders finish;
6. exact chrome prepare is the final fallible step and may refuse; and
7. only its irrevocable success permits message visibility and immutable
   generation publication.

R3 is therefore replaced by **R3a**, **R3b**, and **R3c** below. R3a remains the
small projection-construction slice. R3b is a second behavior-neutral
preparatory slice: it builds detached effect values and owns the staged-delivery
sequencer, but no production startup/reload path calls or installs them. R3c is
the first effect boundary and the only one of the three that changes production
orchestration. This dependency split keeps partial orchestration out of both
preparatory commits.

R3b defines one typed immutable `PreparedReloadEffects` value, detached
provider/coding/capability preparation, a two-phase chrome prepare/commit port,
and the ordered-delivery gate/token contract. Detached mutable builders finish
first; the value is then assembled and frozen exactly once, rather than
incrementally mutating a nominally immutable holder. R3c invokes all provider
catalog/factory/refresh/fallback, coding, history/usage/compaction, unavailable/
default, and tool-capability preparation before chrome prepare. Chrome prepare
is the final fallible operation; its success is the irrevocable acceptance point
and returns a token whose commit cannot refuse. A free-form undo log,
compensating live provider rollback, and a second `session_start` invocation are
forbidden. Before acceptance, all pipy-owned candidate writes target detached
owners and no live provider, coding, queue, tree/input, paint, presentation,
persistence, or generation owner is mutated. In particular, the candidate API
used by replacement `session_start` routes post-freeze sends to the detached
candidate generation queue sidecar rather than R1's sealed activation host.
Refusal disposes that queue with the other detached owners; it does not restore
a destructively mutated live provider.

This mechanism requires two explicit documented deltas from `606a860`.
First, replacement `session_start` runs against detached candidate sinks before
accepted activation-staged custom messages become live; `606a860` exposes those
messages first. Second, the frozen staged batch becomes visible only after
successful acceptance, so a pre-acceptance replacement-`session_start`, provider
preparation, or chrome-preparation refusal/failure suppresses messages that
`606a860` could already have exposed. R3c must document and characterize both in
extension behavior docs and the changelog and may not call the result behavior-
preserving. All other startup/reload ordering remains pinned. After irrevocable
acceptance, R3c reserves/installs R3b's ordered gate and performs the finally-protected,
non-fallible generation, chrome, prepared-reference, and temporary-legacy-
adapter publication under the session mutex. Only after unlock does it deliver
the complete frozen staged batch through the newly published projection.
Accepted staged custom messages bypass `custom_outbox` and go directly to
durable tree, render/diagnostic, and input-queue sinks. It then releases and
drains the gate so replacement-`session_start` candidate sends already in the
adopted queue, followed by concurrent candidate/live sends, use normal delivery
through that same projection. Fail-soft staged delivery cannot undo or prevent
publication, and gate release/drain is finally-protected. Callback, provider
construction/refresh, chrome materialization, paint, rendering, diagnostics,
persistence, sink delivery, and last-reference release remain outside every
session/chrome guard.

The attempted diff is reduced by ownership rather than discarded. R3a keeps the
projection values/builder, pure construction adapters, construction validation,
and per-family equivalence/no-alias evidence. R3b keeps only detached prepared-
effect values/adapters and unit/barrier characterization; it neither publishes
nor changes a consumer source of truth. R3c owns every current diff hunk that
installs or reads the projection, changes startup/reload sequencing, overrides
candidate lifecycle inputs, invokes exact chrome acceptance, prepares or
publishes provider/capability state, installs or consults the R3b ordered gate in
the generation/queue sidecar and existing activation-send/renderer-drain hooks,
invokes the synchronized accepted staged flush, paints/persists, or restores
after failure. It must rewrite destructive reconcile/repair hunks rather than
carry them forward. R4a and later contain no leftover preparation/publication
or gate-definition/staged-sequencing orchestration. R1's authoritative freeze/seal
and R2's rejected-sidecar preservation, closed-write shapes, exact-handle
ownership, and unlocked paint/callback guarantees are prerequisites, not
narrowed clauses.

## Program invariants

Every implementation slice preserves these unless its **Scope** explicitly
names and tests a behavior change.

1. Project trust, path containment, provider/tool admission, credential
   exclusion, and the separation between full-content private product sessions
   and metadata-only workflow archives remain fail-closed.
2. CLI text, JSON/RPC schemas, provider wire requests, session formats, event
   ordering, extension contracts, terminal bytes/lifecycle, and command
   precedence do not change accidentally.
3. Every field has one named guard taken by all readers and writers. The R1
   candidate-host guard owns staged registration/message/value/failure and seal/
   dispose state. The session mutex guards generation/selection and generation-
   outbox state; chrome sidecars use sink-local guards. R5a promotes the existing
   per-run `mutation_io_lock` into one reentrant coding-effect coordinator used
   by every effectful retained coding-session adapter, active-tree pointer read/
   rebind, mutable `NativeSessionTree` API, and `CodingInputQueue` API. Atomic
   admission claims one exclusive owner/depth lease (same-thread nested calls
   re-enter), serializing retained effects while provider/render/callback work
   runs with the lock released; terminal condition-waits for the owner to clear. Shared tree/input
   phases take `mutation_io_lock`; durable tree append alone holds it across I/O.
   The only nested order is `mutation_io_lock → session mutex`; all reverse
   edges, candidate/session nesting, session/chrome nesting, and chrome/paint
   nesting are forbidden and tested. The session mutex never spans provider or
   filesystem I/O, and there is no check-then-act or lost update.
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
reload boundary. The mandatory order is `G0` → `R0` → `R1` → `R2` → `R3a` →
`R3b` → `R3c` → `R4a` → `R4b` → `R4c` → `R5a` → `R5b` → `R6` → `R7`; none
may be reordered or run in parallel. No `D`, `L`, `P`, `A`, `T`, or `C` slice
may begin until `R7` records completion or an independently reviewed formal
reconciliation proves an alternative contract. Merely writing `R0` does not
clear the gate.

The queue contains exactly **29 numbered execution slices**: G0; thirteen
ordered reload slices (R0, R1, R2, R3a, R3b, R3c, R4a, R4b, R4c, R5a, R5b, R6,
R7); D1; nine lint slices (L1-L9); P1-P2; A1; T1; and C1. The planning commit
itself and the universal gate are not additional slices.

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
clause-disposition table to that spec: `landed`, `required in R1-R6` (including
R5a/R5b), or `formally narrowed`, with code/test evidence for every narrowed clause. The
minimum required set cannot be narrowed away: rejected activation must preserve
old chrome, rejected/otherwise-abandoned registration must be sealed/disposed,
published extension projections must be coherent, operations must use one
snapshot,
class-A ports must reject stale generations atomically, and `set_model` must
separate fallible preparation/in-memory commit/fail-soft persistence. The audit
must enumerate the complete current class-A port inventory by port and owner.
The expected inventory is the three families planned below:
`set_active_tools`, `set_thinking_level`, and `set_model`. If any other class-A
port exists, stop before code and revise/re-split this plan, including the
reported slice count and dependency order, rather than silently assigning it to
R5a, R5b, or R6.

The decision must explicitly settle which settings/resources and queue-sidecar
clauses remain transactional. For settings, it must record exactly one
evidence-backed disposition without pre-deciding it here: if settings remain
transactional, R3a must include an immutable settings projection supplied by an
allowed session/settings-owned adapter with no extension-boundary reverse
import, and R4a must consume that projection; if settings are formally narrowed,
R3a must omit that projection and R4a must not consume settings from the
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
and evidences exactly one of the two R3a/R4a paths above, with no unresolved
placeholder; queue and chrome sidecars have explicit retained/narrowed dispositions; the
reachable outbox copy/clear and retained coding-session tree/input races each
have an owning slice; the guarded table names the active-tree pointer, every
mutable tree/input reader/writer, extension writer adapter, durable-write order,
and terminal refusal; each R1-R6 sub-slice stays within its bound and mechanism;
the acquisition graph proves the only nested order is
`mutation_io_lock → session mutex`, with the reverse edge forbidden and
provider/render/paint work unlocked; candidate guards and the session mutex
cannot nest;
two reports of the same shared-state defect force another contract revision
before code, not automatic review rounds beyond the cap.

**Docs/release/commit:** transactional spec, architecture/backlog pointers; no
changelog entry. Commit: `docs: reconcile reload completion contract`.

### R1 — seal candidate contribution registration

**Kind:** concurrency behavior at the activation boundary.

**Scope:** Give candidate activation a candidate-owned registration host with
one-way sealed/committed/published/disposed candidate transitions under its own
guard, plus a narrow accepted-catalog terminal transition, with host-internal
lifecycle methods and atomic all-host publication authored by the
activation hosts; only the internal ownership protocol can authenticate the
published transition. Host lifecycle is the sole ownership state machine: batch
publication adds no second state/lock, and the pre-runtime reload seam uses only
a session-thread-owned optional holder. That guard owns all command,
shortcut, hook, tool, provider/unregistration, flag/value, message/entry-renderer,
user/custom-message staging, first-failure state, `_activated`, and guarded
parsed flag-value application/read views (no direct mutable
`RegisteredFlag.values` alias). Replace the current separate `staged_*` harvest
reads with one atomic seal/freeze snapshot before accepting a successful
candidate; seal/dispose any rejected or otherwise abandoned host. Every late
class-D `register_*`, `unregister_provider`, or `on` call raises
`ExtensionCapabilityError` at that call boundary and cannot enter either the
candidate projection or live runtime. This fixes present behavior
where a retained post-activation API returns normally while mutating already-
harvested dead/partially shared host state. A published `pipy-tool-call` cannot
race its own initial harvest, but extension-created activation threads can; both
reader/writer families take the host guard. Current activation waits without a
production timeout; this slice also makes abandonment timeout-safe but does not
add or select a timeout policy. Do not integrate chrome publication or alter
live selection in this slice.

**Bound:** production edits are limited to the activation/registration-host
owner and reload composition adapter family; live chrome, selection, provider,
and consumer-dispatch owners are out of scope. R1 guards and seals staged
messages with the host. R3b owns the accepted staged-message sequencer/direct-
sink adapter, R3c invokes it at acceptance/publication, and R4a owns later live
runtime append/drain conversion after R3a creates the sidecar value.

**Acceptance:** deterministic tests pause an activation worker, abandon and
seal/dispose its host through the bounded host seam, then release the worker and
prove late commands, tools, providers/unregistrations, hooks, message/entry
renderers, flags/value changes, shortcuts, and staged user/custom messages are
absent. Separate post-success and rejection arms assert
`ExtensionCapabilityError` for every extension-visible return-shape family:
void-return `register_*`/unregister calls, direct `on(event, handler)`, decorator-
factory `on(event)`, and `register_flag`. No test or implementation invents an
inert decorator or `RegisteredFlag`. One frozen snapshot contains every staged
contribution family but not the host's future live `_activated` state; it is
authoritative for messages, so post-seal sends while activation is still pending
have no effect and finalization flushes its user/custom messages
exactly once. `ActivatedExtension.hooks` is immutable for activated, disabled,
and discovery-passthrough outcomes. Parser/get-value tests prove flag values use
the host-owned guarded view; instrumented access proves every named reader/writer takes the candidate
guard. Extension-controlled validation and normalization run outside that guard.
One typed registration-staging helper owns every `register_*` family's open/name/
availability/value/recheck/commit flow and encodes its prior reason order for
ordinary validation: command/tool/flag availability precedes remaining value
validation; shortcut key shape and handler callability precede normalized
reserved/duplicate checks; provider factory/models/default/OAuth precede
duplicate checking; and message/entry renderer callability precedes duplicate
checking. Unexpected extension-controlled normalization/copy exceptions record
the first bounded family-invalid reason and type-only diagnostic even when
extension code catches the raised error; exact pre-R1 reason behavior is not
retained for that hostile case. Extension-controlled work remains unlocked,
followed by a guarded recheck-and-commit that refuses if
seal won. Accepted runtime message
routing after activation commit releases the host guard before any session-
mutex append. The seam is ready for a future
timeout without adding one here. Instrumented guards fail on attempted
candidate-guard/session-mutex nesting in **both** directions, and barrier tests
cover registration-versus-seal and seal-versus-publication order. Contribution
names accept prior-compatible `str` subclasses (including `StrEnum`, default-
stringifying `(str, Enum)` values, and subclasses overriding `__str__`), detach
their underlying values to exact plain strings without invoking the override,
and prepare the complete successor reservation state before frozen messages
flush, then publish it with one non-fallible assignment. Invalid
provider unregistration raises and records `invalid_provider` rather than
silently disappearing. Mixed/corrupted rejection cleanup disposes every
unpublished sibling, returns structured skipped-published/inaccessible anomalies
through one cleanup reporter (never `warnings.warn`), and never disposes a live
published host. Startup/reload use their existing sinks; provider-only catalog
harvest requires its caller's sink and finalizes accepted hosts after immutable
provider/unregistration outputs detach. A refused non-published finalization
uses host-owned disposal under the acquired guard; a published refusal is
counted and left live, and guard inaccessibility/failure is counted separately.
Successful finalization clears all staging and outbox reachability, refuses
registration/sends/publication, and retains only guarded registration-time
default flag values needed by provider factories that captured the API; the
catalog helper does not parse/apply CLI tokens. Rejected/abandoned disposal
still clears flags. An open/unsealed sibling refuses
all-host publication while leaving the complete candidate disposable. Recursive
inventory pins `activate_extension_batch(...)`, `activate_extensions(...)`,
provider catalog harvest, every cleanup-reporting seam, and every production
caller; each finalization path forwards the correct diagnostic sink, and pending
pre-trust batches are finalized or abandoned once.
Reload constructs
the exact generation before ownership transfer
and leaves only its non-fallible pointer publication before the generation is
live; startup constructs its generation reference before transfer. No session
mutex is held while extension code runs or cleanup callbacks execute.
Chrome/listener sinks remain R2-owned. The candidate host guard is released
before any later queue/session-mutex handoff; R1 introduces no nested edge.

**Docs/release/commit:** update the transactional clause table and
`docs/extension-api.md` to say late class-D calls raise
`ExtensionCapabilityError`; add matching changelog fix wording for retained APIs
after activation or rejection, without claiming a timeout or inert return.
Commit:
`fix: seal rejected extension activation`.

### R2 — stage chrome and dispose rejected candidates

**Kind:** user-visible correctness fix.

**Scope:** Stop clearing live TUI chrome before activation/flag validation.
Collect candidate retained chrome/listener requests in candidate-owned sinks:
header/footer/widgets/title/indicator, terminal-input listeners, autocomplete
providers, editor-component and hidden-thinking-label registrations, and their
callback/disposer identities. On rejection, close/dispose those sinks without
delivery; on acceptance, reconcile new chrome only after the candidate is
committed. Preserve the prior chrome when any fallible candidate step fails.
Session-scoped sticky status rows and working message/visibility, plus imperative
dialog/editor/overlay/theme effects, retain the R0 narrowed dispositions.

**Bound:** production edits are limited to candidate chrome/listener sinks,
reload orchestration, and the TUI chrome reconciliation adapter; other
contribution projections and selection state are out of scope. One sink-local
guard serializes each sidecar's closed-check+write and close. Paint, callbacks,
disposal, and the session mutex never run while that guard is held.

**Acceptance:** captured and real-PTY tests prove (a) invalid flags and injected
activation failure retain old title/widgets/listeners, (b) rejected candidate
chrome never paints, (c) successful removal clears old chrome exactly once after
acceptance, and (d) each retained class-B write racing/after close silently
no-ops with its existing `None` return and no diagnostic/exception, while
`on_terminal_input()` returns an inert disposer. `just test-pty-smoke` and the
focused chrome PTY module pass.

**Docs/release/commit:** update `docs/extension-api.md`, extension/TUI behavior
docs, and a changelog fix entry that includes late writes to a rejected
candidate's closed chrome being ignored. Retired-live invocation remains R4c and
terminal invocation remains R5a. Commit:
`fix: stage extension chrome until reload commit`.

### R3a — build detached immutable extension projections

**Kind:** ownership/construction foundation; no product behavior.

**Scope:** Add a standalone session-owned candidate projection value (not an
installed field of the live `SessionExtensionGeneration`) and its builder for
runtime/flags, commands/menu/descriptions/shortcuts, lifecycle/request hooks,
extension tool ports plus candidate capability state, renderer mappings,
provider contributions, generation queue handles, and the exact R2 chrome
handle. Freeze copied mappings/tuples and validate constructor inputs, including
queue/reference mutex identity, entirely before any host transfer. R0 selected
the settings-omitted path, so settings, keybindings, resources, and a settings
adapter are absent. Add only pure composition adapters that construct this value;
no startup or reload path may call them yet.

**Bound:** production edits are limited to
`src/pipy_harness/native/session_generation.py` and pure builder/port adapter
functions in `src/pipy_harness/native/tool_loop_session.py`. The live generation
shape/reference, activation-host transfer, startup/reload execution, lifecycle
callback overrides, provider/catalog/coding state, capability publication,
queue append/drain/close, chrome selection/reconciliation, TUI paint,
presentation, and persistence are excluded. Existing consumers and all
observable startup/reload ordering remain byte-for-byte/base-order compatible.

**Acceptance:** each projection family has a separate equivalence arm against
its still-live `606a860` legacy source/adapter; candidate and retained values
share no mutable mapping/list except explicitly identified unconsumed sidecar
storage handles; tool ports keep their historical private flag-dict copies; each
builder/validation injection fails before returning a candidate and cannot
change a live reference or adapter. Source/AST inventory proves no production
startup/reload caller constructs, installs, publishes, snapshots, or consumes
the projection, and no lifecycle/provider/TUI reverse import enters the
extension boundary. Every equivalence arm remains until the appropriate R4
slice moves its last consumer and deletes its legacy source. R3b and R3c may not
remove any arm.

**Current-attempt reduction:** retain the projection dataclasses/freezing and
builder portions of `session_generation.py`, the pure projected/legacy tool-port
and candidate-builder adapters in `tool_loop_session.py`, the new
`tests/session_generation_test_support.py` only where needed by this unit
surface, and the per-family builder/equivalence/no-alias portions of
`tests/test_native_session_extension_generation.py`. Park all reference
publication/validation, startup/reload integration, lifecycle override, exact-
chrome acceptance, queue-close, provider/capability publication, and integration
test hunks for R3b/R3c.

**Exact landing manifest/checks:** the R3a commit may change only
`src/pipy_harness/native/session_generation.py`, the pure adapter region of
`src/pipy_harness/native/tool_loop_session.py`,
`tests/session_generation_test_support.py`,
`tests/test_native_session_extension_generation.py`, `docs/architecture.md`,
`docs/specs/2026-07-25-transactional-extension-reload-rebuild.md`, and
`docs/backlog.md`. Run
`uv run pytest tests/test_native_session_extension_generation.py`,
`uv run mypy src/pipy_harness/native/session_generation.py src/pipy_harness/native/tool_loop_session.py`,
`git diff --check`, `just check`, and
`just docs-build`. Any required edit outside this manifest means stop and revise
the slice before code.

The definitions are intentionally not yet called by production. Ruff's enabled
rules do not reject typed module-level definitions merely for lacking a
production caller, there is no repository coverage threshold, and the focused
unit/equivalence suite directly exercises every builder and validation branch;
source/AST inventory is the additional no-caller gate. R3c must make startup and
reload call these same R3a builder/adapters, with an equivalence test proving
both paths receive equal candidate inputs and projections.

**Docs/release/commit:** architecture/spec/backlog ownership only; explicitly
record that no runtime path uses the value and no changelog applies. Commit:
`refactor: build detached reload projections`.

### R3b — build detached reload effects and ordered delivery

**Kind:** ownership/preparation foundation; no product behavior.

**Scope:** Add a typed, frozen `PreparedReloadEffects` value and pure preparation
ports for candidate lifecycle inputs, detached provider catalog/factory/refresh/
fallback and unavailable/default selection, coding binding/history/usage/
compaction disposition, tool capability, temporary legacy adapters,
presentation/persistence payloads, and the exact R2 chrome prepare input. All
mutable detached builders complete before this value is assembled and frozen
once. It is never an incrementally mutated holder. Chrome prepare itself is not
invoked in this slice.

R3b also defines one ordered-delivery gate/token and accepted-delivery
sequencer. The gate is generation/queue-sidecar state with explicit reserve,
queue, release, and drain operations: while reserved, producer sends enqueue and
no ordinary drain may pass them; the authoritative R1-frozen staged user/custom
batch is delivered first, then release/drain admits queued candidate/live sends
in queue order. Gate state changes use the session mutex, while every sink
operation is unlocked. Accepted staged custom messages bypass `custom_outbox`
and dispatch directly, in order, to durable tree, render/diagnostic, and
`CodingInputQueue` sinks. The sequencer provides the one contract R3c must
install and invoke; R4a may convert synchronization around the same hook but may
not redefine the token or recreate staged sequencing. No production startup/
reload path or live producer/drain hook may call, install, or consult these
preparation/delivery definitions yet, and this slice publishes and consumes no
projection or temporary legacy value.

**Bound:** only detached values, pure adapters, and the uninstalled ordered-
delivery gate/token definitions in `extension_hooks.py`,
`session_generation.py`, `tool_loop_session.py`, and `tui.py` are in scope.
Production startup/reload control flow, `SessionGenerationRef` publication,
activation-host transfer, lifecycle invocation, live producer/drain hook wiring,
live provider/catalog/coding/capability mutation, chrome prepare/commit
invocation, live sink selection/paint, persistence, and all R4 consumers are
excluded. Candidate, session, and chrome guards never nest; no callback,
provider work, render, diagnostic, paint, I/O, or disposer runs under a guard.
R3b changes no consumer source of truth, has no production caller, and creates
no partial production orchestration.

**Acceptance:** unit failure injection covers detached activation inputs, the
R3a projection, provider catalog/factory/refresh/fallback, coding/history/usage/
compaction, unavailable/default payload, capability, temporary legacy adapter,
presentation/persistence payload, and chrome-prepare-input builders in that
order. Every failure precedes any chrome prepare call, disposes detached values,
and leaves all live identities unchanged. Construction instrumentation proves
all mutable builders finish before exactly one `PreparedReloadEffects` assembly/
freeze and that no later mutation exists. A deterministic unit barrier exercises
the uninstalled gate contract: reserve prevents an ordinary drain, queued
candidate/live sends remain FIFO behind the
complete frozen staged batch, release permits one drain, and no callback or sink
delivery occurs under the session mutex. A focused sink test proves accepted
staged custom messages never touch `custom_outbox` and characterizes the exact
durable-tree, render/diagnostic, and input-queue calls and order. Source/AST
inventory proves no production startup/reload caller or live `_ActivationApi`
send name/alias or `_CustomEntryRenderer` drain/delivery hook uses the new gate
or any new adapter, and no consumer source changes.

**Current-attempt reduction:** after R3a lands, retain here only immutable
prepared-effect shapes, detached/pure preparation adapters, ordered-delivery
gate/token and sequencer definitions, and their unit/barrier tests. Park every
startup/reload call, gate installation or live producer/drain consultation,
reference/legacy publication, lifecycle invocation, chrome prepare/commit,
provider/capability live assignment, paint/persistence, and restoration/retry
hunk for R3c. The preserved destructive reconcile/repair code is evidence only
and must not enter R3b.

**Exact landing manifest/checks:** R3b may change only
`src/pipy_harness/native/extension_hooks.py`,
`src/pipy_harness/native/session_generation.py`,
`src/pipy_harness/native/tool_loop_session.py`,
`src/pipy_harness/native/tui.py`,
`tests/session_generation_test_support.py`,
`tests/test_native_extension_activation_sealing.py`,
`tests/test_native_extension_chrome_staging.py`,
`tests/test_native_session_extension_generation.py`,
`tests/test_native_tool_loop_session.py`, `docs/architecture.md`,
`docs/specs/2026-07-25-transactional-extension-reload-rebuild.md`, and
`docs/backlog.md`. Run the four named `test_native_*` modules above,
`uv run pytest tests/test_native_extension_providers.py tests/test_native_provider_catalog.py tests/test_native_dynamic_provider_swap.py`,
`uv run mypy` on the four named source modules, `just test-pty-smoke`,
`uv run pytest tests/test_native_tui_chrome_pty.py`, `git diff --check`,
`just check`, and `just docs-build`. Before review, `git diff --numstat` for
production and tests must total no more than 1,200 added-plus-deleted lines, no
single source file may exceed 400 changed lines, and all paths must match this
manifest. Crossing any limit or needing another path means stop and split/revise
the plan before code.

**Docs/release/commit:** architecture/spec/backlog ownership only; explicitly
record no production caller and no changelog entry. Commit:
`refactor: prepare detached reload effects`.

### R3c — accept and publish one prepared reload

**Kind:** user-visible transactional reload ordering and correctness.

**Scope:** Install the R3a/R3b construction paths in production startup and
reload. Startup and reload must call the same R3a builder/adapters; a focused
equivalence trace proves equal candidate inputs produce equal projections while
startup retains its exact `606a860` catalog/factory/fallback/rebind/message/
notice order. Reload performs the only authorized sequence: activate and build
the candidate; invoke replacement `session_start` once, unlocked, against
candidate hooks/flags and detached sinks, with its candidate API routing
post-freeze sends into the detached candidate generation queue sidecar; complete
every detached provider/catalog/factory/refresh/fallback, coding/history/usage/
compaction, unavailable/default, and capability preparation; assemble/freeze
`PreparedReloadEffects` once after those mutable builders finish; then call
chrome prepare as the final fallible step. Chrome prepare materializes without
selecting, reconciling, or painting the live sink. Its success is irrevocable
acceptance and returns a commit token whose application cannot refuse.

Any activation, builder, `session_start`, provider/coding/capability preparation,
prepared-value construction, or chrome-prepare refusal/failure closes/disposes
the candidate hosts, candidate generation queue, chrome, provider values, and
prepared effects. Replacement-`session_start` sends therefore reach no sink on
refusal. The old generation, legacy adapters, provider/coding/capability state,
tree/input, presentation, and persistence remain identity-equal; no compensating
live provider rollback or duplicate lifecycle call is permitted. Because R3b's
frozen staged delivery is accepted-only, failures from `session_start` onward
suppress messages that `606a860` could already have exposed.

After acceptance, invoke R3b's sequencer exactly once to reserve/install its
ordered gate in the candidate generation/queue sidecar. In a narrow, explicit
carve-out from R4a's consumer exclusion, wire the existing live
`_ActivationApi.send_user_message()` and `send_message()`/`sendMessage()` paths
and `_CustomEntryRenderer` drain/delivery hook to consult that gate. The same
finally-protected non-fallible section under the session mutex publishes
together the R3a generation projection, gated queue sidecar, preaccepted chrome
routing token, prepared in-memory provider/coding/capability references, and
temporary legacy adapters, while pinning retired values for release after
unlock. No staged or queued sink delivery occurs in that section.

Only after unlock, deliver the complete frozen staged user/custom batch through
the newly published projection; custom messages go directly to its durable tree,
render/diagnostic, and input-queue sinks, never `custom_outbox`. In `finally`,
release and drain the ordered gate so replacement-`session_start` sends from the
adopted candidate queue, then concurrent candidate/live sends, follow through
normal delivery against that same projection. Fail-soft staged delivery cannot
undo or prevent publication or leave the gate reserved. Thus R3c moves no other
consumer source of truth, and every R3a equivalence arm remains until the
appropriate R4 consumer moves and its legacy source is deleted. Paint,
diagnostics, callbacks, rendering, provider work, filesystem writes,
persistence, sink delivery, and release remain outside the commit.
`KeyboardInterrupt`/`SystemExit` after acceptance completes publication and gate
release before propagation; no post-acceptance path repairs to an old pointer.

The two intentional deltas are replacement `session_start` preceding accepted
staged custom-message visibility and pre-acceptance lifecycle/provider/chrome
failure suppressing staged messages that `606a860` could expose. Preserving
replacement-`session_start` post-freeze sends in its candidate queue across
acceptance is a loss-prevention mechanism inside that first delta, not a third
documented delta. It is distinct from R1's retained pending-activation API
contract, where post-seal sends silently do nothing. Rejected staged and
candidate-queue messages remain invisible, hooks do not run twice, provider/
fallback and coding outcomes remain otherwise compatible, and all other
startup/reload ordering is pinned.

**Bound:** production ownership is startup/reload composition;
`SessionGenerationRef` validation/publication; candidate lifecycle hooks/flags/
UI override; invocation of R2 chrome prepare/commit and exact-handle selection;
provider/catalog/coding/capability prepared-value publication; installation and
invocation of the R3b staged sequencer/gate in the generation/queue sidecar;
consultation of that gate by the existing live `_ActivationApi` send names/alias
and `_CustomEntryRenderer` drain/delivery hook; temporary legacy dual-
publication; and post-commit presentation/persistence. This gate consultation
is the sole narrow carve-out from R4 consumer-dispatch exclusion. All other R4
consumer dispatch, class-A admission, coding-session control serialization, and
terminal teardown remain excluded. Guards never nest, and no callback, provider
work, render, paint, I/O, diagnostic, or
last-reference release runs under them.

**Acceptance:** exact headless and TUI traces pin both documented deltas and
every unchanged `606a860` event. Deterministic failure/refusal injection is
ordered as activation → R3a builder → replacement `session_start` → provider
catalog/factory/refresh/fallback → coding/history/usage/compaction → unavailable/
default/capability preparation → one prepared-value freeze → chrome prepare
(final fallible/acceptance) → gate reserve/install plus final assignments/chrome-
token/legacy-adapter publication under the session mutex → unlock → accepted
staged delivery through the published projection → gate release/drain →
presentation/persistence. Focused `session_start`, provider-prepare, and chrome-
prepare refusal/failure tests prove no staged user/custom or replacement-
`session_start` candidate-queue message reaches any sink although `606a860`
characterization shows the staged batch would have; all old live identities
remain unchanged, the refused candidate queue is disposed, and no provider
rollback runs. An accepted arm proves replacement-`session_start` post-freeze
sends survive in the detached queue and drain normally after the authoritative
frozen staged batch. This test explicitly contrasts those preserved candidate-
API sends with R1's pending-activation post-seal silent no-op. Post-acceptance
interrupt/fail-soft arms prove publication happens once, gate release/drain still
runs, and no rollback is claimed.

A deterministic accepted-flush-versus-live-send barrier invokes the R3b gate
through production reload and proves the published projection identity is used
for every staged and queued resolution; the frozen staged batch is complete
before queued replacement-`session_start` sends, which are complete before a
racing live send reaches its sink. Sink characterization proves accepted staged
custom messages bypass `custom_outbox` and hit the newly published durable tree,
renderer/diagnostic, and input queue in order. A replacement hook fires once and
writes only exact candidate sidecars. Provider factory/fallback, history/usage/
compaction, unavailable/default,
successful removal, malformed flags, and real-PTY chrome cases retain focused
coverage. Static lock instrumentation proves the final section contains only
gate state changes and prevalidated assignments/pinning, publishes generation
and chrome routing together, and contains no callback, provider work,
construction, rendering, sink delivery, I/O, paint, diagnostic, or release.
Startup/reload equivalence proves both use the same R3a builder/adapters; startup
construction failure disposes unpublished hosts without a partial
projection.

**Current-attempt ownership:** after R3a/R3b reduction, all remaining integration
hunks in `extension_hooks.py`, `session_generation.py`,
`tool_loop_session.py`, and `tui.py`, including generation/queue-sidecar gate
installation, candidate API routing, live `_ActivationApi` send-name/alias gate
consultation, and `_CustomEntryRenderer` gate-aware drain/delivery; the
publication/integration portions of
`test_native_extension_activation_sealing.py`,
`test_native_extension_chrome_staging.py`,
`test_native_session_extension_generation.py`, and
`test_native_tool_loop_session.py`; and the current architecture/spec behavior
claims belong here. Rewrite restoration/retry tests around detached refusal and
one non-fallible commit; do not preserve destructive reconcile/repair or add
compensating rollback.

**Exact landing manifest/checks:** R3c may change only the four source and four
`test_native_*` files named in **Current-attempt ownership**,
`docs/architecture.md`,
`docs/specs/2026-07-25-transactional-extension-reload-rebuild.md`,
`docs/backlog.md`, `docs/extension-api.md`, and `CHANGELOG.md`. The four-source
manifest explicitly authorizes only the gate installation/consultation carve-out
named in **Bound**; it does not authorize any other R4 consumer conversion. Run
those four test modules,
`uv run pytest tests/test_native_extension_providers.py tests/test_native_provider_catalog.py tests/test_native_dynamic_provider_swap.py`,
`uv run mypy` on the four source modules, `just test-pty-smoke`,
`uv run pytest tests/test_native_tui_chrome_pty.py tests/test_native_extension_custom_ui_pty.py`,
`git diff --check`, `just check`, and `just docs-build`. Before review,
production and tests must total no more than 1,200 added-plus-deleted lines by
`git diff --numstat`, no single source file may exceed 400 changed lines, and no
path may fall outside the manifest. Crossing a limit, needing another path, or
requiring another observable delta means stop and split/revise before code.

**Docs/release/commit:** architecture, transactional spec, backlog,
`docs/extension-api.md`, and the existing `CHANGELOG.md` `### Fixed` bullet
beginning “Extension reload no longer clears live retained TUI chrome before
activation” must state both intentional deltas. Commit:
`fix: publish accepted reload effects`.

### R4a — snapshot command/request operations and live outboxes

**Kind:** user-visible concurrency correctness: coherent snapshots and no erased
live queue append; retired-handle close does not add delivery semantics.

**Scope:** Convert extension command, shortcut, input, before-agent,
before-provider, tool-result, and session-gate dispatch to take one published
R3c snapshot at operation start and use its runtime, flags, and queue sidecars
throughout. Convert accepted/live `_ActivationApi.send_user_message()` and
`send_message()`/`sendMessage()` writers plus only
`_CustomEntryRenderer.drain_extension_outboxes()` at the drain end. R3b already
defines, and R3c already installs and invokes, the authoritative ordered gate/
token, frozen staged-message sequencer, and direct-sink delivery; these existing
producer/drain hooks already consult that gate. R4a must not redefine the token,
change staged-first sequencing, or reimplement delivery. It converts only the
same hooks' append/drain synchronization. Under the same session mutex, live
closed-check+append, atomic detach/drain, and retirement close serialize; sink
delivery/cleanup follow after unlock. A stale
activation-api append silently returns its existing `None` with no diagnostic or
accumulation. Capture `project_trusted` in the provider-header request snapshot,
removing the detached provider-worker reach into `SettingsManager`. Settings,
keybindings, and resources remain absent from the snapshot.

**Bound:** command/shortcut/input/request/session-hook dispatch, the two live
activation send names/alias, the renderer outbox drain method only, provider-
header trust capture, retirement close, and proven legacy-source deletion.
Definition/installation of the ordered gate/token, staged activation sequencing/
delivery, candidate-queue preservation, and publication order are R3b/R3c-owned;
all reload/startup/chrome/provider orchestration is R3c-owned. They are out of
scope, and R4a may only add append/detach/drain synchronization around the
already gate-aware hooks.
Coding-session controls, tool/renderer/provider execution consumers, menu,
lifecycle, chrome, class-A ports,
and the R3a builder are excluded.

**Acceptance:** barriers publish between reads for every converted family and
report one matching generation id/flags/hooks/sidecar. A retained provider-
header callback holds no `SettingsManager`. A cancelled `pipy-tool-call` writer
racing drain cannot append between detach and clear and be erased. Inventory
covers both live activation send names/alias and the renderer drain, proves each
retains R3c's gate consultation, and proves R3b's token, staged sequencer, and
direct-sink delivery are neither redefined nor reimplemented; coding-session
callables touch only
provider/tree/render/input sinks. Rejected/retired queues silently refuse later live-handle appends, only
the live generation drains, and retired-handle delivery remains absent as on
`606a860`. No converted operation reads `current` twice. A family equivalence
arm is removed only with its last legacy consumer/source.

**Docs/release/commit:** architecture/spec and closed-queue extension API; a
changelog fix for a live extension message no longer being erased by concurrent
drain. Commit: `fix: snapshot extension dispatch and live queues`.

### R4b — snapshot tool, renderer, and provider projections

**Kind:** concurrency behavior; intended external behavior is coherence only.

**Scope:** Make tool advertisement/execution, renderer selection, and extension
provider contribution/refresh consume the published R3a/R3c snapshot values.
After the last consumer of each corresponding separately published map/state
moves, delete that legacy source and remove that family's R3a equivalence arm in
the same proven deletion. No tool, renderer, or provider arm may be removed
before its source and last consumer; all arms for legacy sources outside these
families remain gates. Provider selection and coding-state rebinding keep the
R0 contract and no provider construction occurs under the session mutex. Do
not include class-A mutation admission.

**Bound:** production edits are limited to the tool advertisement/execution,
renderer selection/rendering **except the R4a-owned outbox drain method**, and
provider contribution/refresh consumer families plus their snapshot adapters,
and to composition wiring strictly for deletion of a converted family's legacy
source after its last consumer moves. R3c reload publication is out of scope.
Class-A mutation, menu, lifecycle, and chrome are out of scope.

**Acceptance:** a tool call and provider turn spanning reload retain their
advertised tool/renderer/provider generation; a later turn sees the new one;
each tool, renderer, and provider equivalence arm is removed only
with proof that its last legacy consumer moved and its legacy source was
deleted, while every arm whose legacy source remains still passes; focused
provider, tool, renderer, and real-PTY rendering suites pass.

**Docs/release/commit:** architecture/spec table; no separate changelog entry
because R4b adopts the coherent value already published by R3c. Commit:
`refactor: snapshot extension execution projections`.

### R4c — snapshot menu, lifecycle, and chrome from one generation

**Kind:** concurrency behavior; intended external behavior is atomic coherence.

**Scope:** Convert menu/descriptions/shortcuts, ordinary lifecycle emitter
inputs, and R2 chrome reads/writes to the already-published R3c snapshot. R3c,
not this slice, owns reload acceptance, exact chrome prepare/commit, provider
preparation, presentation/persistence, and semantic publication. Snapshot a
retired chrome handle under the session mutex, release it, then perform closed-
check/snapshot/close under only that handle's sink-local guard; paint/disposal
follow after all guards. R5a invokes the completed close path from run
finalization. After its last consumer moves, delete the final separately
refreshed contribution source and remove only the final remaining R3a
equivalence arms as part of that proven deletion.

**Bound:** production edits are limited to menu/description/shortcut consumers,
lifecycle emitter inputs and ordinary chrome snapshot/retirement close. Reload
publication and candidate reconciliation remain R3c-owned. Tool/provider/request
dispatch, class-A mutation,
and run-finally wiring are out of scope. The session mutex and chrome sink guard
are named serial owners: never nested; TUI paint runs after both.

**Acceptance:** barrier tests across all R4a-R4c families observe one old or one
new generation id, never mixed flags/tools/renderers/hooks/providers/menu/
chrome; snapshot/retirement critical-section tests prove no I/O, callback,
rendering, construction, or last-reference release occurs under the lock;
successful and
rejected reload PTY behavior remains correct. Only the final remaining R3a
equivalence arms are removed here, each after this slice proves its last legacy
consumer moved and its corresponding legacy projection source was deleted;
arms already removed with proven R4a/R4b family deletions are not recreated.

**Docs/release/commit:** architecture/spec table and the already-established
closed-chrome API note; extend the same `CHANGELOG.md` `### Fixed` bullet
beginning “Extension reload no longer clears live retained TUI chrome before
activation” to state that accepted generations publish coherent tool/provider/
render/UI projections and stale writes through retired chrome handles are
ignored. Commit:
`refactor: snapshot extension presentation consumers`.

### R5a — serialize coding-session effects and terminal teardown

**Kind:** shared-state and durable-order correctness.

**Scope:** Promote the existing per-run `mutation_io_lock` plus a condition on
that lock into one coding-effect coordinator. Inject its reentrant lock into the
active `NativeSessionTree` and `CodingInputQueue`; every mutable-state reader and
writer in those owners takes it. Tree append methods hold it across id/parent
selection, entries/index/leaf/name/label mutation, and `_write_entry()` so
memory and JSONL share one order. Input-queue APIs hold it across complete check/
use/mutate paths. Guard `_RunControlState.session_tree` pointer reads/rebinds.
Completion, custom-entry append, name/label mutation, and custom-message writer
adapters atomically reject terminal or claim one exclusive owner/depth lease,
waiting if another thread owns it; same-thread nested effects re-enter. They
release the lock while provider/render/callback work runs, then a `finally`
decrements depth, clears the owner at zero, and notifies. Shared tree/input
phases reacquire the same lock. Custom-message tree, unlocked render, and input
phases keep their existing order. Read-only tree/name views use
guarded APIs and remain readable at terminal.

In `CodingSessionController.run_loop()`'s `finally`, preserve settle then
`session_shutdown`. An inner teardown `finally` atomically closes admission and
condition-waits for every accepted coding effect; the wait releases `mutation_io_lock`. Once the
active owner clears it briefly takes the session mutex under `mutation_io_lock` to
invalidate/detach the live generation and close/detach its generation outbox.
Release both before R4c chrome close and paint/disposal. This terminal state is shared with R5b/R6. No provider or
filesystem I/O occurs under the session mutex.

**Bound:** production edits are limited to the coding-effect coordinator and
composition wiring; `NativeSessionTree`; `CodingInputQueue` and its session-
controller/agent/RPC adapters; active-tree pointer owner; effectful coding-
session context/writer adapters; session-finalization ports; and terminal
invocation of existing generation-queue/chrome close paths. Provider selection,
class-A generation admission, and `set_model` construction are excluded. The
single mechanism is the existing `mutation_io_lock` and a condition backed by
it; tree/input nested calls are reentrant uses, not extra locks. The only cross-
owner edge is `mutation_io_lock → session mutex` during terminal state. Provider,
render, callbacks, and paint run unlocked; reverse acquisition is forbidden, and
R4a releases the session mutex before input-queue delivery.

**Acceptance:** deterministic barriers prove two retained/session tree appends
cannot duplicate id/parent decisions or reverse in-memory versus JSONL order;
name/label maps and RPC/read-only snapshots never tear. Inventory tests prove
every active-tree pointer rebind/read family, mutable `NativeSessionTree` API,
`CodingInputQueue` mutable-state API, and coding-session writer adapter enters
the same
coordinator. Queue enqueue/take/clear and next-turn-context races lose no item or
check. A retained accepted completion or custom-message call paused before its
last effect finishes keeps the exclusive lease; terminal waits while releasing
the coordinator lock, the call completes, and later calls raise
`ExtensionCapabilityError` with no effect. Exception and same-thread nested-call
arms prove owner depth always releases; if close starts during an accepted
owner, only its same-thread nested effects re-enter and unrelated waiters raise. Lock instrumentation rejects `session mutex → mutation_io_lock`; blocked
provider/render callbacks can use guarded read APIs, and a blocked tree write
proves the session mutex remains acquirable during filesystem I/O.
Terminal queue/chrome close runs once even if `session_shutdown` propagates.

**Docs/release/commit:** update `docs/extension-api.md`, architecture/spec tables,
and changelog wording for concurrent/reordered durable coding-session effects
and post-run refusal. Commit:
`fix: serialize extension coding session effects`.

### R5b — bind active-tool and thinking mutations to a generation

**Kind:** generation-admission correctness.

**Scope:** Contexts/ports for `set_active_tools` and `set_thinking_level` capture
the creating generation id. Under the shared session mutex, compare id and gate
and apply the complete in-memory mutation atomically. Thinking mutation keeps
R5a's coordinator lock outermost through in-memory commit and durable tree I/O,
releases the inner session mutex before that I/O and the outer lock before footer
paint, and therefore preserves mutation/JSONL order without holding the session
mutex across I/O. Reuse R5a terminal state: both methods
return `False` after teardown. Do not include `set_model`.

**Bound:** production edits are limited to active-tool/thinking context and port
owners, guarded selection state, session-thread cycle and RPC thinking adapters,
and post-lock tree/footer adapters. Coding-session callable/terminal wiring is
R5a and model/provider construction is R6. Named order is only
`mutation_io_lock → session mutex`; reverse acquisition is forbidden.

**Acceptance:** stale, publication-pending, and terminal calls return `False`
and change nothing; a call admitted before gate-open survives publication;
every selection reader/writer uses the session mutex. Thinking in-memory and
JSONL order agree under concurrent callers, filesystem I/O occurs after session
unlock, and lock-order instrumentation proves no reverse edge.

**Docs/release/commit:** update extension API concurrency semantics and the spec
table; changelog fix wording covers stale selection refusal. Commit:
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
and successful commit. A retained `set_model` call released after terminal
teardown returns `False` and cannot construct/publish a provider, rebind coding
state, refresh presentation, or persist a default. History, usage, compaction,
provider binding, selection, and defaults retain the reconciled contract;
failure diagnostics contain no credential/private detail.

**Docs/release/commit:** extension/provider docs and changelog fix entry. Commit:
`fix: commit extension model changes atomically`.

### R7 — close the reload correctness boundary

**Kind:** integration tests and durable reconciliation; no new mechanism.

**Scope:** Run the R0 scenario matrix over success, rejection, the abandonment
seam (without adding a product timeout), cancellation stragglers, teardown, and
post-commit failure. Add only missing
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
recorded in R1-R6 (including R5a/R5b). Commit:
`docs: close transactional reload boundary`.

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

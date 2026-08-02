# Pipy Backlog

Status: planning index

This backlog records the current implementation direction at a task-slice
level. It is not a full issue tracker. Use it to choose the next small,
reviewable change while keeping the source-of-truth design constraints in
`docs/harness-spec.md` and `docs/session-storage.md`.

**Parity policy and historical matrices live in
[parity-plan.md](parity-plan.md).** Its command/flag policy, accidental-surface
ledger, and topic-spec index remain authoritative, while its older matrices are
historical. The current architecture disposition is the
[2026-07-29 assessment](2026-07-29-architecture-quality-assessment.md), and the
latest ranked Pi comparison is [pi-mono-gap-audit.md](pi-mono-gap-audit.md).
Use that audit for product selection only after the assessment's reload-contract
follow-up. The big-topic specs indexed by the plan are
[session-tree.md](session-tree.md),
[extension-api.md](extension-api.md), [provider-catalog.md](provider-catalog.md),
[settings-config.md](settings-config.md), [automation-rpc.md](automation-rpc.md),
[tui-workflow.md](tui-workflow.md), and
[export-distribution.md](export-distribution.md).

## Landing-Gated Program — Comparative Review Remediation

The
[2026-07-30 comparative review remediation plan](plans/2026-07-30-comparative-review-remediation-plan.md)
is the landing-gated dependency-ordered queue from the three-way `pipy`/
`pi-mono`/`tau` review. Authorization is commit-state based: while the complete
plan/backlog diff is uncommitted, implementation remains blocked on its fresh
Claude Code Opus review under the default two-round docs cap. Round 2 produced
new actionable shared-state correctness findings about candidate-host guard
ownership, exhaustive queue writers, terminal mutation scope, and closed-sink
refusal shapes, so the operator explicitly authorized exactly one additional
docs review of the resulting corrections. Landing the planning commit on `main`
certifies the required review completed, material findings were addressed, and
G0 is authorized.

**Active/next slice:** **T1 — share only the proven provider test contract**

D1 is complete: it restored a concise reader-facing index, replaced the copied
architecture-quality ledger in the living architecture overview with a link to
the dated assessment, and removed Architecture Quality Assessment, Architecture
Migration, and Backlog from the site navigation. Removing those navigation
entries does not claim that the documents are unpublished; site search inclusion
remains a separate deferred decision. D1 changed docs/config only, so no
changelog entry applies. L1 is complete: Ruff fixed exactly 64 `I001` findings
across 61 source paths, and the source diff is import-order-only. L1 changed no
behavior or rule configuration and requires no changelog entry. L2 is complete:
Ruff fixed exactly 141 `I001` findings across 131 tracked non-source Python
paths (seven scripts and 124 tests), and the diff is import-order-only. L2
changed no behavior or rule configuration and requires no changelog entry. L3
is complete: Ruff now selects exact `I001` beside `C901`, and focused
configuration coverage prevents category `I`, `I002`, or another broad import
selector from being enabled. L3 changed no product behavior and requires no
changelog entry. L4 is complete: the 18 Ruff-autofixable `UP035` findings were
mechanical `typing` to `collections.abc` import-source moves across 18 paths,
and the later remediation-introduced `typing.ContextManager` finding was
narrowly migrated to `contextlib.AbstractContextManager` plus its annotation
name. The repository-wide `UP035` count is 19 to 0. L4 changed no behavior or
rule configuration and requires no changelog entry. L5 is complete: the frozen
provider-turn delta policy now has one explicit typed immutable module default,
with focused characterization preserving its exact all-enabled value, identity,
exact-type validation, explicit overrides, and synchronous and interruptible
provider-turn output/event behavior. L5 changed no behavior or rule
configuration and requires no changelog entry. L6 is complete: Ruff now selects
exact `UP035` and `B008` beside `C901` and `I001`, and focused configuration
coverage prevents broad `UP`, `B`, `ALL`, other same-category selectors, or
ignore variants from neutralizing the exact gates. L6 changed no product
behavior and requires no changelog entry. L7 is complete: exactly five
shortest-input or adjacent-pair sites now say `strict=False`, with unequal-input
characterization preserving their exact prior results. The repository-wide
`B905` inventory is 14 to 9. The additional catalog field/value identity
assertion in `tests/test_native_models_json.py` was introduced after the plan's
13-finding baseline; it is a verified mismatch-refusal site and remains L8-owned
beside the eight planned refusal sites. L7 changed no behavior or rule
configuration and requires no changelog entry. L8 is complete: git blame and
history confirm the ninth refusal site, the catalog field/value identity
assertion in `tests/test_native_models_json.py`, entered in R3c1c commit
`5cdb084` after the plan's 13-finding baseline and before this lint-remediation
sequence began. It is semantically the same strict paired-identity assertion as
the planned sites, so L8 reconciles it beside the original eight. All nine
remaining sites now use `strict=True`; focused unequal-length characterization
covers extension-package source/results, agent-event identities, the five
coding-product-session identities, catalog field/value identities, and model-
selector rows while the existing equal-length scenarios stay unchanged. The
repository-wide `B905` inventory is 9 to 0. L8 changes only tests and harness
assertion behavior, changes no product behavior or rule configuration, and
requires no changelog entry. L9 is complete: Ruff now selects exact `B905`
alongside `C901`, `I001`, `UP035`, and `B008`, and focused configuration
coverage rejects broad or alternate selectors and any ignore variant that
would neutralize `B905`. L9 is a configuration-only quality-gate slice, changes
no product behavior, and requires no changelog entry. P1 is complete: the
pipy-only agent-visible `truncate` tool, its module, schema, special rendering,
prompt/catalog inventory entries, and tool-specific tests are removed without
an alias or deprecation shim. Independent automatic read, bash, and
provider-visible output bounds remain covered, and the removal is recorded in
the changelog. P2 is complete: the pipy-only model-visible `edit_diff` tool,
unified-diff implementation, schema, registry/prompt/catalog/render entries,
and tool-only tests are removed outright without an alias or compatibility
dispatch. The production manifest is now exactly `read`, `ls`, `grep`, `find`,
`write`, `edit`, `bash` in that order; `edit` retains the existing path and
trust policy as the sole edit tool. A1 is complete: one typed
`CodingAgentTurnStatusEffects` collaborator now owns run entry, accepted-input
accounting and literal prompt recall, result/cancellation pending-input
transitions, tool-policy synchronization, provider settlement, no-tool footer
refresh, and malformed-fatal diagnostics behind narrow state and presentation
ports. `_ReplLoopStep.step_once` retains accepted-input preparation,
provider-turn construction, local input dispatch, and run coordination; it no
longer defines the owned callbacks. Captured and PTY characterization preserves
event/history, prompt-recall, pending-input, diagnostic, footer, and
cancellation ordering. A1 changes no behavior and requires no changelog entry.
T1 is next.

G0 is complete: this test-policy-only slice retired frozen closeout
synchronization, changed no product behavior, and requires no changelog entry.
The dated assessment is the point-in-time source; other historical copies remain
preserved. R0 is complete as a docs/spec decision: the transactional spec now
maps every assessment residual and ideal clause, confirms the class-A inventory
is exactly `set_active_tools`, `set_thinking_level`, and `set_model`, selects the
settings-omitted R3a/R4a path after enumerating RPC, worker, adapter, and external
manager surfaces; and retains generation queue/chrome sidecars. The exhaustive
guarded-field table now assigns every staged contribution/message registry,
flag-value/failure field, `_activated`, and sealed/disposed state to the R1
candidate-host guard; the atomic freeze snapshot carries contributions, not a
second live-state bit. R1 is complete: candidate
activation now seals one guarded host into one complete snapshot. The guarded
host lifecycle is the sole ownership state machine; all-host publication holds
every host guard, refuses the whole set when any sibling is still open/unsealed,
and a lock-free optional runtime holder publishes once or disposes rejected and
otherwise abandoned hosts through the bounded loader/composition seam. Cleanup
returns structured skipped/failed anomaly counts through one reporting seam:
production startup/reload use their existing diagnostic sinks, while the
provider-only catalog harvest requires its caller's sink and terminally finalizes
accepted hosts after detaching immutable provider/unregistration outputs. A
refused non-published transition disposes under the acquired host guard; a
published refusal is reported and left live, while guard inaccessibility/failure
is counted separately. The narrow finalized state retains guarded registration-
time default flag reads for provider factories that captured the API; the catalog
helper does not parse/apply CLI tokens. It clears staging/outboxes and refuses
publication, registration, and sends; rejected/abandoned disposal still clears
flags. Recursive producer, caller, and
reporting-seam inventory plus one-shot pending-batch finalization pin
those routes, and no raw warning path remains. Retained class-D
`register_*`, `unregister_provider`, and direct/decorator `on` calls raise
`ExtensionCapabilityError` without inert return values. Accepted `str`
subclasses, including `(str, Enum)` values and subclasses with an overridden
`__str__`, are detached from their underlying value to plain strings before
reservation without invoking the override; invalid unregistration records
`invalid_provider` rather than disappearing.
One typed registration-staging seam preserves each prior family order for
ordinary validation before a guarded atomic recheck: command/tool/flag
availability precedes remaining value validation; shortcut key shape and
callable validation precede normalized reserved/duplicate checks; provider
factory/models/default/OAuth precede a duplicate; and message/entry renderer
callability precedes a duplicate. Unexpected extension-controlled normalization/
copy exceptions record the first bounded family-invalid reason and type-only
diagnostic even when extension code catches the raised error, rather than
preserving exact pre-R1 reason behavior. All extension-controlled validation
remains unlocked. Flag parsing and reads use
guarded host-owned state rather than a mutable
`RegisteredFlag.values` alias. Barrier and lock instrumentation cover
registration versus seal, seal before publication, post-success/rejection
refusal, malformed-flag reload rejection, and candidate/session non-nesting.
The frozen snapshot is authoritative for staged user/custom messages: post-seal
sends while activation is still pending silently do nothing and only that
snapshot may flush. No activation timeout policy was added. R2 is complete:
candidate chrome/listener staging preserves rejected chrome without re-firing
retained `session_start`; accepted replacements fire once after activation-host
commit, and delivery, paint, callbacks, disposal, and session-mutex acquisition
remain outside its guards.

R3 is complete through shipped R3c3. Startup and reload now build and install
R3a projections, while reload consumes the R3b prepared-effect/gate sequence,
R3c1 owner APIs, and R3c2 routing/snapshot seam in one accepted publication.
R4a is complete: command, shortcut, input, before-agent, before-provider,
tool-result, and session-gate operations use one published projection snapshot.
Startup and reload refuse a projection-less generation before candidate
ownership publication or any active state change; projection-less construction
remains a low-level legacy/harness shape only and never triggers direct runtime
fallback.
Input preserves its flag-less context and no-hook tool results preserve
`ProductContent` identity. The real provider route rebuilds a header callback per
request from the current generation hooks and current session tree; a retained
callback holds copied trust rather than `SettingsManager`.
The session mutex serializes live outbox append, detach/drain, and only the O(1)
retirement mark/reference-detach phase. `accept_prepared_reload()` explicitly
finalizes retention/copy/clear/release after its outer mutex block; direct startup
and rejection wrappers finalize after their own acquisition, so sinks, variable
cleanup, and finalizers remain unlocked. Snapshot-backed drains require a
projection and never direct-fallback around an installed provider. The shipped R3b token,
staged-first sequence, direct custom sinks, candidate preservation, and R3c3
publication/release order are unchanged. Rejected or retired activation sends
silently accumulate nothing, and a live append can no longer be erased by a
concurrent drain. R4b is complete: each provider iteration now holds one
published tool registry/executor, tool-call hook/flag view, filtered renderer
map, and already-constructed coding provider across reload, while custom
message/entry rendering takes one renderer projection per operation. Startup and
refusal-path provider contribution consumers use one generation snapshot; R0
selection and coding refresh/fallback rebinding remain unchanged, and no
provider is constructed under the session mutex. The obsolete legacy tool-port
builder, runtime-to-renderer mapper, direct runtime renderer/provider reads,
separately published renderer map, and only their tool/renderer/provider R3a
equivalence arms are deleted with static proof. No changelog entry applies
because R4b consumes the coherence R3c3 already published. R4c is complete:
startup/reload menus, ordinary lifecycle hooks, user-bash and tool-call hook UI,
and all retained chrome operations now use one generation snapshot. Reload
captures the displaced generation's exact chrome handle under the session mutex,
then closes and snapshots it under only its sink guard; callbacks, disposal,
paint, and last-reference release remain unlocked. Retired handles silently
ignore stale writes. The separately stored generation flag map, temporary
lifecycle/flag reload effect, direct runtime menu/lifecycle reads, and final R3a
equivalence arms are deleted with static proof. R5a is complete: one run-scoped
coding-effect coordinator now serializes retained completion, custom-entry,
name, label, and custom-message effects; active-tree and input-queue state share
its reentrant lock; tree append preserves in-memory/JSONL order; and terminal
finalization waits for accepted owners before detaching generation outboxes and
closing chrome. Later effectful coding-session calls raise
`ExtensionCapabilityError`, while read-only final-tree views remain available.
R5b is complete: every retained command, shortcut, hook, user-bash, tool-call,
tool-result, session-gate, and extension-tool active-tool/thinking context now
captures its creating generation id. The shared session mutex admits and commits
only a matching live generation while the publication gate is closed; stale,
publication-pending, and terminal calls return `False` without mutation. The
same mutex now guards every live provider selection/thinking reader and writer,
including session-thread cycling and RPC assignment. Thinking commits retain
`mutation_io_lock → session mutex`, release the session mutex before durable
JSONL append, and release the outer coordinator before footer paint, preserving
in-memory/JSONL order. R6 is complete: every allowed `set_model` callable now
captures its creating generation id and claims the R5a effect lease. It performs
an initial terminal/generation/gate admission and expected-owner capture, builds
the provider plus detached coding replacements outside the session mutex, then
rechecks terminal, generation, gate, exact selection/default/thinking state, and
coding-binding identity under `mutation_io_lock → session mutex`. Only
prevalidated selection, provider binding, empty history, and cleared usage are
assigned there. Stale, gated, terminal, or owner-mismatched candidates return
`False` with no state, presentation, or default-persistence effect. Provider
construction and defaults I/O are unlocked from the session mutex; footer
refresh and fail-soft default persistence run post-commit. Compaction and
provider failure remain live, while model rebind still clears history and usage.
R7 is complete subject to its required review/commit gate: one compact product
integration test composes a successful reload across real command, lifecycle/
before-agent hook, flag, tool, and renderer owners, while the durable R0 matrix links
existing rejection, unbounded-join abandonment, cancellation-straggler,
teardown, and post-commit-failure evidence. All six assessment Actual gaps are
closed or linked individually in the transactional spec and dated assessment.
No timeout, cancellation policy, concurrency abstraction, product source, or
production behavior was added. R0's sticky chrome, immediate UI/notification,
queue-protocol, independent settings/resources, and fail-soft process/
persistence narrowings remain explicit. R7 adds no changelog entry because R1–
R6 already recorded every behavior change. D1, L1–L9, P1, and P2 are complete
as recorded above. A1 is also complete as the behavior-preserving typed status-
effect ownership extraction described above, and T1 is next.

The former one-shot R3c contract was non-executable and was split around the
real `_ActivationApi` send owner. Material review then proved the original exact
R3c1 manifest could not also implement the coding usage-accumulator and full
catalog/auth refresh owner seams. R3c1 is therefore replaced by R3c1a, R3c1b,
and R3c1c before the retained R3c2 routing and R3c3 composition slices.
`NativeReplProviderState` owns selection and pending-default state in
`repl_state.py`; `_ProviderMutationEffects` currently orchestrates reload
selection, fallback, and defaults. `NativeToolCapabilities` already ships the
required `ToolCapabilityState` prepare/publish APIs. Private-field substitution,
guard nesting, and list-subclass interception remain forbidden.

**R3c1a — local reload owner values** is shipped in the intended same commit as
its code. Observable message order/content and reload behavior are unchanged,
but the slice includes an internal coding-history representation/performance
tradeoff rather than being unqualifiedly behavior-neutral. Live `_messages` is
now an immutable tuple; append uses tuple replacement and is O(n) per append
instead of amortized O(1). When history is unchanged, the `messages` property
and result snapshots may share that same tuple identity rather than returning a
fresh list-to-tuple copy. This immutable shape enables alias-free, assignment-
only prepared fallback history publication. No changelog applies.

R3c1a adds detached preparation and assignment-only publication for the
extension-provider overlay, not `ModelCatalog` or `AuthStore` refresh. Both live
and detached provider-overlay maps use the same immutable `MappingProxyType`
runtime shape. Coding prepared binding values carry exact expected and
replacement `CodingProviderBinding` values; coding refresh publishes binding
only, while coding fallback publishes binding plus immutable empty replacement
history exactly like live `rebind_provider()`. The publishers write replacement
values only: refresh never republishes retained history, and neither coding path
restores compaction or provider failure from a preparation snapshot.
`prepare_reload_state()` itself captures expected live selection and
`pending_default` while its caller briefly holds the shared session mutex; only
replacement values are caller-supplied. R3c3 now compares and publishes in one
uninterrupted mutex section. `snapshot_reload_state()` is absent and never
existed in the committed baseline, so there is no REPL refresh snapshot/publish
path for retained selection/default; REPL publication also never restores
`thinking_level`. A concurrent accepted thinking change is therefore preserved,
and this owner-state freshness does not change the later R5b/R6 generation-bound
class-A scopes. `PreparedReloadEffects` uses concrete values for only those
families and the existing `ToolCapabilityState`; at R3c1a shipment,
`CodingCompactionValue`, `CodingUsageValue`, and `ProviderRefreshValue` were
opaque and uninstalled. R3c1b made usage concrete, and R3c1c has now made
provider refresh concrete; only compaction remains opaque and uninstalled. The
concrete catalog/coding/REPL
imports in `session_generation.py` are type-checking only. The executable
synthetic-parent test proves only that `session_generation.py`'s own runtime
dependency closure does not import the catalog/auth/coding/REPL owner stacks; it
does not exercise or prove bypass of real parent package `__init__` modules. No
production caller is installed. The exact source manifest remains
`catalog_state.py`, `coding/state.py`, `repl_state.py`, and
`session_generation.py`; exact editable tests remain
`tests/test_native_catalog_state.py`, `tests/test_native_coding_state.py`,
`tests/test_native_repl_state.py`, and
`tests/test_native_session_extension_generation.py`; only this backlog, the
remediation plan, transactional spec, and architecture docs are editable.
Focused tests prove exact expected-token current/mismatch behavior, exact
publisher shape, no production callers, the type-only imports within
`session_generation.py`'s own runtime dependency closure, and the then-opaque
package-wide uninstalled inventory. They include those four
modules plus the unchanged tool-capability, extension-provider, provider-catalog,
and dynamic-provider-swap characterization suites; Mypy, `just check`, `just
docs-build`, exact manifest, 1,200 production+test changed lines, and 400 per
source are gates. No changelog; commit
`refactor: prepare local reload owner values`.

**R3c1b — usage accumulator reload owner** is shipped and behavior-neutral.
`AgentUsageAccumulator` owns an immutable detached refresh characterization and
builds a fresh cleared fallback accumulator from the supplied prototype's
pricing. The prepared fallback does not alias the caller's prototype.
`CodingSessionState` re-enters the exact shared session `RLock` and publishes
fallback by one pointer assignment; coding never accesses accumulator-private
fields. Neither path compares high-churn live usage: refresh is an explicit
no-op, while fallback uses an immutable identity token to refuse an intervening
accumulator pointer swap before installing the owner-built accumulator. The
token does not reference or retain the old accumulator. The prepared
replacement's complete cleared-state integrity is validated before
the mutex section and is not repeated there because the detached value is
exclusively owned until publication. Ordinary
counter absorption does not invalidate either path. The old accumulator and
provider failure remain untouched. `PreparedReloadEffects.coding_usage` now
carries concrete `AgentUsageReloadValue`; at R3c1b shipment compaction and
provider refresh stayed opaque. R3c1c has now made provider refresh concrete.
The coding annotations use the existing allowlisted usage-module
dependency; widening the concrete-class import allowlist would require an out-
of-manifest architecture-test edit. AST inventory proves the accumulator methods
have only the uninstalled coding adapters as callers and those adapters have no
production caller. Focused tests pin detachment, later-live usage retention,
equal-binding owner-swap refusal by constant-time identity token, phase-A
prepared-replacement integrity validation, pricing behavior, shared-lock use,
exact no-op/assignment publisher shapes, complete
slot coverage, total family checks, and preparation isolation. Exact
sources/tests and the 1,200/400 gates matched the manifest. No user-visible
behavior changed and no changelog applies.

**R3c1c — catalog/auth refresh reload owners** shipped first as a behavior-neutral
foundation without a caller; R3c3 now invokes it. No separate changelog applies. `AuthStore` and
`ModelCatalog` are synchronous, single-session-thread-confined owners, not
thread-safe shared objects. Every current production read and write, OAuth flow,
provider registration, refresh, and R3c3 check/publication runs on that one
session thread. No background thread, executor, `to_thread`, callback
on another thread, or parallel writer may call either owner. Copy-on-write owner
updates therefore have no concurrent lost-update window. A future cross-thread
production path requires a named guard acquired by every reader and writer to
land first in its own reviewed slice.

The landed contract has three phases. **A, before taking
the session mutex:** complete every fallible file read, parse, validation, merge,
OAuth modifier callback, credential load, construction, immutable detachment,
and deep replacement/shadow self-consistency check. Before any callback, capture both
exact owner tokens plus only the detached catalog preparation inputs: OAuth
modifiers and detached extra/registered providers. Auth capture returns only its
owner token. Catalog/auth leaf prepared values retain only the expected-owner
token and validation/replacement state until consumed. `ModelCatalogRefreshValue`
has a wholly opaque repr; auth and aggregate values remain redacted. Public leaf
capture/prepare-from-snapshot owner APIs keep token and detached-input logic
local. Recursive detachment accepts immutable mapping proxies and rebuilds them
as detached ordinary containers before existing preparation and validation. OAuth model-modifier callbacks are pure catalog-row transforms and
must not mutate `AuthStore` or any other owner. The built-in bound modifier
captures credential data but no `AuthStore` capability. The adversarial callback
characterization is token-rotation refusal, not auth snapshotting: a reentrant
callback mutation rotates the affected token, and phase B refuses the candidate.
R3c3/operator retry is meaningful only after that violating mutation source
stops. **B, immediately before acceptance under the session mutex:** perform only
bounded constant-time, allocation-free owner identity/token comparisons by
delegating to the catalog/auth leaf match APIs. Every supported
`ModelCatalog` and `AuthStore` mutation API rotates or replaces its token. An
inverse AST inventory checks writes through known/current typed or aliased
production owner references and forbids writes to owned fields outside the
declaring owner classes. Prepared-replacement
drift validation is not repeated there because the detached value is
exclusively owned between phase-A validation and publication. The R3c3 session
mutex serializes reload with every other session-owned mutation. **C, after
acceptance while still holding the mutex:** publication is assignment-only or
calls only the two vetted non-fallible leaf owner publishers. Phases B and C run
without yielding or unlocking. Each leaf transfers
its prebuilt mutable live-shape replacement, then assignment-neutralizes the
consumed secret, validation, and replacement-data fields with prebuilt empty
values; the aggregate clears retained owner references. Publication also clears
replacement and expected tokens. Consumed values fail phase B; duplicate leaf or
aggregate publication takes only a cheap, nonfallible, allocation-free return and
leaves live state unchanged. R3c3 owns the one successful match and aggregate
publish. No consumed prepared
value keeps a credential, private header, catalog row/config secret, or mutable
live handle.

Ordinary live `ModelCatalog.refresh()` and `AuthStore.reload()` preserve their
existing behavior, reset/failure semantics, and live representations.
`ProviderCatalogState.auth_store` is an optional construction surface;
it remains the single authoritative normalized store, so public reassignment and
live/prepared refresh cannot diverge. Successful catalog refresh rotates owner identity
after final rows assignment as well as early enough to invalidate failure.
Owner-lifetime paths/config inputs and direct public result containers are
immutable by contract after construction/publication; only
supported owner APIs may replace them. The inventory covers known/current typed or aliased production owner
references, while tests may deliberately violate ownership only to exercise
failure preparation. Live auth set/get now deep-detaches nested values to prevent caller
aliases from mutating credentials. List-versus-tuple representation tagging is
auth-specific; catalog compat/config list/tuple handling is validation
canonicalization. Frozen validation values remain separate from detached
publication replacements until both are cleared after successful publication.
The separate R3c1a extension-provider overlay publication remains distinct.
R3c3 now invokes it explicitly, with a non-empty overlay equivalence arm; full
catalog/auth publication does not rebuild the overlay.
`ModelDefinition.cost` and `NativeModelSpec.cost` are immutable
`NativeModelCost`; partial override cost mappings must be copied and frozen.
Characterization is field-complete across captured owner tokens and detached
preparation inputs, prepared values, replacements, tokens, rows, provider config,
auth values, and the aggregate.
The AST inventory covers mutation-token paths and calls/writes through its
known/current aliases. It is bounded regression evidence for confinement, not
exhaustive proof of dynamic aliases,
reflection, indirect callbacks, or runtime thread reachability. R3c1c's
no-production-caller inventory includes aggregate
`prepare_catalog_auth_refresh()`, `validate_prepared_catalog_auth_refresh()`,
`catalog_auth_refresh_matches_expected()`, and `publish_catalog_auth_refresh()`
entry points as well as leaf APIs. Inventory
also proves redacted secret repr, exact publisher shape, and no phase-B
validation, callback, I/O, construction, or allocation.
`PreparedReloadEffects.provider_refresh` now carries concrete
`ProviderCatalogRefreshValue`.
Exact sources were
`src/pipy_harness/native/auth_store.py`,
`src/pipy_harness/native/models_json.py`,
`src/pipy_harness/native/catalog_state.py`, and
`src/pipy_harness/native/session_generation.py`; exact editable tests are
`tests/test_native_auth_store.py`, `tests/test_native_models_json.py`,
`tests/test_native_catalog_state.py`, and
`tests/test_native_session_extension_generation.py`; only the same four planning
docs are otherwise editable. Run those focused modules and relevant unchanged
provider characterization, Mypy on the four sources, `git diff --check`,
`just check`, and `just docs-build`; enforce 1,200 production+test changed lines
and 400 per source. No changelog; commit
`refactor: prepare reload catalog and auth state`.

**R3c2 — installable generation message routing seam: SHIPPED in this
change.** It is behavior-neutral only on the ordinary uninstalled path. One
strongly owned typed `GenerationMessageRouting` belongs to one
`SessionExtensionGeneration` and its exact ordinary outbox lists. Batch
construction creates/reuses it explicitly; every host, runtime, and queue
projection receives that same owner, and valid same-owner/pair composition is
idempotent and order-independent. Production composition has no permanent
no-mutex owner: every production `SessionGenerationRef` construction explicitly
supplies the live session `RLock`, and construction/pre-publication
unconditionally binds the required typed `_ExtensionRuntime.message_routing`
member. `ExtensionQueueProjection` idempotently binds that same uninstalled
owner to its exact queue mutex; both paths preserve the identity and reject a
different mutex. A
still-unbound direct R1 fallback cannot be installed; binding leaves lifecycle
`uninstalled` and grants no routing or host authority. `_ActivationApi`
validates the owner/list pair without a tautological mutex parameter;
`ExtensionQueueProjection` validates the exact owner/list/session-mutex triple. Global or weak registries, outbox-pair
registries, identity lookup by outbox objects, and routing discovery by rereading
outboxes are forbidden.

Every send follows one two-step protocol with no guard nesting. Under only the
host guard it stages an open-host message, refuses an ineligible host, or creates
an immutable `GenerationMessageReservation` only when separately granted
accepted-after-seal authority is present; sealing does not grant it. Reservation
creation binds the exact user/custom outbox target and exact routing owner/
generation authority needed later. Send versus host disposal linearizes there:
a reservation created first wins even if disposal later clears or rebinds host
fields, while disposal first prevents later reservations. After host unlock,
route resolution uses only that immutable reservation and never rereads the
host's current outbox, lifecycle, or authority; no cross-guard reread is allowed.

The exact queue/reference session mutex is the sole guard for routing lifecycle,
attached gate/storage, attached FIFO, and queue/gate mutable state. Installed
state is `candidate -> releasing -> live`, with retirement from any installed
state. Acceptance in `candidate` or `releasing` appends only to the attached
FIFO; live acceptance detaches an immutable claim strongly bound to the exact
old-generation gate/storage. Publication and retirement are bounded constant-
time and nonblocking mark/swap/detach operations with no wait, yield, sleep, I/O,
callback/arbitrary sink, or unlock/relock-to-wait. Retirement-first and post-
retirement claims silently drop. A claim linearized before retirement may
finish after unlock only against detached old-generation state and can never
affect the new generation. Held old snapshots, detached release batches,
already-submitted gate callbacks, and in-flight pre-retirement claims retain the
old owner or its exact immutable gate/storage handle. Reclamation occurs only
after retirement detaches it, attached pending work is detached for post-unlock
drop, and all those strong references release. The retired
`GenerationMessageRouting` remains sole mutable-state owner, guarded by the same
session mutex until reclamation.

Candidate release is bounded to two finite FIFO batches. Phase 1 takes the
session mutex, validates `candidate`, atomically transitions `candidate ->
releasing`, detaches the current finite FIFO prefix, and leaves an attached tail
FIFO. It then unlocks and submits the prefix in order through the exact named,
vetted `OrderedDeliveryGate`, outside both session mutex and candidate-host
guard. Accepted reservations during `releasing` take the mutex and append only
to the attached tail, so they cannot overtake the prefix. After prefix
submission completes, phase 2 reacquires the session mutex exactly once. If the
owner is still `releasing`, it detaches the then-current finite tail, submits it
through the exact same `OrderedDeliveryGate` while holding the mutex, and flips
`releasing -> live` before unlock. New accepts block during that bounded final
handoff and then use the live path, so they cannot overtake the tail. There is no
retry loop and continuous sends cannot starve release.

The phase-2 gate submission is an approved narrow exception:
`OrderedDeliveryGate.append_reserved()` is the vetted leaf performing only
bounded pure in-memory ordered append into detached/candidate generation
storage. It performs no I/O,
waits, yields, user/package callbacks, arbitrary sinks, rendering, delivery
callbacks, or candidate-host guard acquisition. Every other callback, sink,
I/O, direct delivery, rendering, commit flush, prefix submission, ordered
forwarding/delivery, and detached-value release remains unlocked.

Retirement stays constant-time/nonblocking under the session mutex. Retirement
while `uninstalled` is a nonfallible no-op preserving state, exact outbox
identities, and later direct R1 append/custom behavior. If it wins while prefix
submission is unlocked, it marks `retired`, detaches/drops the attached tail,
and returns without waiting. Phase 2 observes `retired`, does not submit that
dropped tail, does not flip live, and stops. The already-detached pre-retirement
prefix may finish only against detached old-generation storage and cannot affect
the newly published generation. Injected phase-1 `append_reserved()` failure
reacquires the mutex exactly once, terminalizes and detaches any still-attached
tail unless already retired, unlocks, drops, and re-raises. Phase-2 failure
terminalizes/detaches all attached state under the mutex and re-raises after
unlock. Both terminal paths leave later sends, drains, releases, and retirements
silent/nonraising and unable to affect a successor.

R3c2 defines `_CustomEntryRenderer`'s typed coherent
`SessionGenerationSnapshot` provider seam but does not wire it in production;
R3c3 atomically publishes/installs it with the generation/owner. Drain may use
one coherent snapshot. Durable direct custom tree/render/input delivery remains
outside routing retirement and always calls `_deliver_custom_message()` with
its existing R1 return value, unlocked; it does not consult routing in R3c2.
Only drain may perform the nonraising typed coherent routing side effect, so
unavailable, uninstalled, mismatched, or retired routing cannot suppress or
alter direct custom delivery. The no-provider legacy/harness drain fallback
remains direct and nonraising; once a provider is installed, a missing snapshot
or projection raises before touching an outbox rather than bypassing routing. Stable explicit
boundary instrumentation, not `sys.settrace` or list-subclass hooks, pins these
boundaries.

The projection-owned R3c3 seams are `install_candidate_route()`,
`release_pending_route()`, and `retire_route()`. A complete executable inventory
must include every direct call and recognized state-write path that grants or
revokes host eligibility or can install, release/publish, retire, or publish a
routing owner—not only installer calls. It records host-local eligibility
separately because host eligibility is not routing install authority. The
expected R3c2 production set for routing-authority commit/install, release/publish,
retire, and combined owner publication is empty. The inventory recognizes
positional/keyword calls, `**` expansion, aliases/factory forwarding, and
post-construction provider mutation, proving renderer-provider wiring is empty;
R3c3 updates it when installing the route. R3c2 has no production startup/reload installer. Its exact source
manifest was `extension_runtime.py`, `session_generation.py`,
`extension_hooks.py`, and `tui.py`; its exact editable test manifest is
`tests/test_native_extension_activation_sealing.py`,
`tests/test_native_extension_chrome_staging.py`,
`tests/test_native_extension_custom_ui.py`,
`tests/test_native_session_extension_generation.py`, and
`tests/test_native_tool_loop_session.py`. PTY modules are checks, not editable
manifest paths. The same 1,200/400 budgets apply. No changelog; commit
`refactor: route extension messages through queue sidecars`.

**R3c3 — accept and publish one prepared reload: SHIPPED.** This slice retains the prior user-visible
composition scope and consumes R3c1a–R3c1c plus R3c2. It is the first production
startup/reload installer. It publishes candidate-host ownership unlocked, then
under one uninterrupted session-mutex acquisition checks all expected owners
before atomically publishing/installing the complete `SessionExtensionGeneration`,
routing owner, renderer snapshot provider, and prepared owner effects through
`SessionGenerationRef`; no separately published renderer/owner/outbox pointer
exists. The publication/retirement critical section is
bounded constant-time and nonblocking: it marks the old owner retired, swaps out
its attached pending FIFO for post-unlock drop, detaches the old owner, and
publishes the new pointer. It never waits, yields, sleeps, performs I/O, invokes
callbacks/sinks, or unlocks/relocks to wait for active work. Old snapshots and
pre-retirement detached work retain only detached old-generation state under the
lifetime rule above.

R3c3 preserves the three-phase preparation/check/publication boundary. Its exact
sequence is activation → R3a builder → install candidate routing and run
replacement `session_start` once → provider catalog/factory/refresh/fallback →
coding/history/usage/compaction → unavailable/default/capability → exact owner-
token and detached preparation-input capture plus deep replacement/shadow
validation → one prepared freeze → chrome prepare as the final fallible
preparation → complete the R3b gate reservation with no caller-held session
mutex → publish candidate-host ownership unlocked → acquire the session mutex
once and perform only constant-time, allocation-free identity/token comparisons
before the first session write → on success perform only constant-time route
retirement/pointer publication and vetted nonfallible owner assignments without
unlocking → unlock → frozen staged delivery → release phase 1 transitions `candidate ->
releasing`, detaches the finite prefix under the mutex, and submits it through
the exact `OrderedDeliveryGate` unlocked → release phase 2 reacquires the mutex
exactly once, submits the finite attached tail through that same vetted gate
under the mutex if still `releasing`, and flips `releasing -> live` before
unlock → gate release/drain → presentation/persistence. The first
`generation_ref.publishing()` section remains open through accepted staged
sinks, both route-release phases, and drain; those paths may run extension-
visible code while `publication_pending` is true, and each `unlock` above means
the session mutex only. Any expected binding, selection, or pending-default
mismatch invokes no publisher and unlocks before caller cleanup. Because host publication is then terminal, cleanup retires the
candidate route and closes candidate chrome instead of disposing it; retained
sends drop, class-D calls stay closed, and the newer live generation/owners stay
untouched. Candidate and session guards never nest. Installed candidate/releasing
routing queues post-freeze sends; an ordinary uninstalled R1 sealed-pending host
remains a silent no-op.

Static lock instrumentation permits constant-time gate/routing mark/swap/detach
state, bounded allocation-free identity/token comparisons and refusal, retired/
drop-batch pinning, direct assignments, calls to explicitly vetted non-fallible
owner publishers, and only the phase-2 bounded pure in-memory tail submission to
the exact vetted `OrderedDeliveryGate` under the session mutex. It forbids all
other gate work, waiting, yielding, sleeping, I/O, callbacks, arbitrary sinks,
direct delivery, rendering, commit flush, prefix submission, delivery callbacks,
and unlock/relock used to wait for active claims or reservations. The successful
owner checks and publication occupy one uninterrupted mutex section,
so no session-owned mutation can land between them. Consumed values fail phase B; duplicate publication is a non-destructive
consumed-state no-op. R3c3 owns the one successful match and aggregate-publish
call.
Existing executable evidence
`tests/test_native_coding_state.py::test_coding_state_shares_the_session_mutex_when_bound`
pins `CodingSessionState._state_lock` to the exact supplied session
`threading.RLock`, so its publisher may re-enter only that same RLock under the
outer section and must never acquire a distinct coding/owner guard. The R3c1a
provider-catalog overlay and `NativeReplProviderState` publishers have no inner
guard and are called only while the shared session mutex is already held. The
new coding, overlay, and REPL publisher-shape tests pin the exact assignment and
guard/no-guard bodies. A vetted owner publisher is nonfallible and assignment-
only, writes replacement values only for fields changed by the corresponding
live transition, and never restores retained history, compaction, provider-
failure, or thinking values from preparation. The expected-state checks are
owner freshness for R3c3 acceptance, not the later generation-bound class-A API
conversion. R5b/R6 must use the current generation id and each publication-gate
section; unlocked host prepublication is not generation acceptance, and the gap
between refusal cleanup and the ordinary retained-refresh gate remains an
independent admission interval. No second guard, factory, callback, I/O,
construction, diagnostic, persistence, disposal, or last-reference release is
permitted there. Candidate/chrome/paint guards and all callbacks,
provider work, delivery, cleanup, I/O, and persistence remain outside. Candidate↔session, session→chrome, session→`mutation_io_lock`, and
chrome→paint nesting are forbidden. Free-form undo logs, compensating live
provider rollback, duplicate `session_start`, mutable prepared holders, and
preserved destructive reconcile/repair hunks are forbidden. R3c3 retains exactly
two documented deltas. First, replacement `session_start` runs before acceptance
and accepted staged custom-message visibility, so a later refusal can retain
already-emitted non-staged, non-chrome lifecycle effects such as `notify` while
candidate chrome is discarded and staged messages are suppressed; that is part
of the first ordering delta, not a third delta. Second, pre-acceptance lifecycle/
provider/chrome refusal suppresses staged messages that `606a860` could expose.
Its exact manifest now includes the controlling remediation plan. The reviewed
correctness repair has a one-slice 1,650-production/test-line and 425-per-source
budget: Opus found the original 1,200/400 limit forced formatting suppression in
the publication path, while four fresh-Pi compaction attempts increased churn or
weakened regression coverage. At the first-amendment review the readable diff initially
measured 1,353 production/test lines with a 418-line maximum source. The round-10
baseline at that first amendment used exactly 1,400 such lines with a 424-line
maximum source. Six independently material Opus findings required executable
coverage in release aggregation, `models.json` shadow/publish, retired-slot AST,
and candidate lifecycle override. Two independent fail-closed fresh-Pi repair
attempts proved that a total at or below 1,400 forced material coverage loss:
round 11 could meet 1,400 only by deleting material startup/provider coverage;
round 12 peaked at 1,551 and could reach only roughly 1,442 by weakening the
required combined staged-sink-plus-release-failure test. The prior 1,500 cap was
a bounded 100-line increase below that uncompacted draft, so consolidation
remained required while complete tests could fit. The round-13 patch used
exactly 1,500 production/test changed lines with a 424-line maximum source. At
this second amendment, the valid round-14 worktree baseline uses exactly 1,499
such lines and reaches the hard 425-line per-source cap, leaving 151 total lines
of headroom under the formal 1,650 cap.
`src/pipy_harness/native/tool_loop_session.py` is already at 425/425, so round-15
changes there must be net-neutral or reverting. Coverage may not be deleted to
fit `tool_loop_session.py`. Additive implementation and coverage must instead use
`src/pipy_harness/native/session_generation.py`, currently at 339 changed lines
with 86 lines of source headroom, `src/pipy_harness/native/extension_hooks.py`,
`src/pipy_harness/native/tui.py`, or the existing test paths. This allocation is
planning guidance within the unchanged manifest, not a scope change.

A fresh independent Opus round 14 left seven material findings open, requiring
Critical atomic validate/publish/commit repair, provider-instance owner binding,
startup cleanup, reload-refusal/retained-refresh repair, documentation and
chrome repair, and executable concurrency and `BaseException` unwind coverage.
The observed complete round-14 repair draft was specifically 1,585
production/test changed lines. The formal 1,650 cap is 65 lines above that known
complete draft. Those extra 65 lines are a bounded allowance for the newly
reviewed Critical atomicity, provider-owner, cleanup, refusal, documentation,
and chrome fixes plus their executable tests, without another coverage-deleting
squeeze. This amendment marks no finding fixed and changes neither the 425
per-source cap, exact 14-path manifest/scope, behavior or ordering beyond the
already-open reviewed findings, nor any future-slice budget. Round 15 resolves
them by removing the check/publish gap, proving terminal refusal cleanup and
provider-instance detachment, using local startup body outcome, routing auth-owner
refusal through retained refresh/unknown-filter diagnostics, and covering live-
chrome `BaseException` unwind. Catalog-backed providers retain only resolved
credential/header/routing values and extension factories receive only
`ProviderContext`; auth/catalog rotation affects owner checks and future builds,
not an accepted provider instance. The commit remains
`fix: publish accepted reload effects`,
and the changelog target is the existing
`### Fixed` bullet beginning “Extension reload no longer clears live retained
TUI chrome before activation”.

The shipped prefix is R3c1a → R3c1b → R3c1c → R3c2 → R3c3 → R4a → R4b → R4c → R5a → R5b → R6 → R7 → D1 → L1 → L2 → L3 → L4 → L5 → L6 → L7 → L8 → L9 → P1 → P2;
the mandatory remaining order begins with T1. R4a converted only live
append/detach/drain/close synchronization and did not redefine R3b's token or
staged sequence; R4b then converted only tool/renderer/provider consumers and
their proven legacy-source deletion; R4c completed the menu/lifecycle/chrome
consumer conversion and final legacy-source deletion.
R5a/R5b/R6 ownership and the class-A count of three are unchanged. Provenance
is fixed: the R5a split brought the
queue to 27 slices, the R3a/R3b/R3c split brought it to 29, the initial
R3c1/R3c2/R3c3 split brought it to 31, and splitting R3c1 into R3c1a–R3c1c
brings it to exactly 33. R7's integration evidence and durable reconciliation close the
transactional boundary represented by the
[2026-07-29 assessment](2026-07-29-architecture-quality-assessment.md). D1 is
also complete; no unrelated cleanup, parity, lint, provider-test, or contributor
work is bundled into either closeout.

The queue has exactly **33 numbered execution slices**: G0; seventeen R slices
(R0, R1, R2, R3a, R3b, R3c1a, R3c1b, R3c1c, R3c2, R3c3, R4a, R4b, R4c, R5a,
R5b, R6, R7); D1;
nine separately bounded lint/fix/enablement slices L1-L9; P1-P2; A1; T1; and C1. The plan's commit gate
is universal rather than a final slice: every slice
uses a fresh Pi implementer, focused gates while the diff moves, one supervising-
root `just check` after review and before commit, an update to the labeled
**Active/next slice:** field, and a docs/release-note disposition. Routine
implementation review is one fresh supervised Claude Sonnet pass plus at most
one fresh Sonnet re-review after material fixes, with no implementer-internal
Claude. Stop on `CLEAN` or explicitly non-material suggestion-only feedback.
Opus is reserved for operator-directed escalation of unresolved high-risk
shared-state or architecture findings. A permitted slice lands as exactly one
commit on `main`.
Release publication, mass native-module moves, broad Ruff rules, unproved
provider-test migrations, and destructive shared-`.venv` hook reproduction are
explicitly deferred rather than silently included.

## Completed Reviewed Program — Architecture Quality

The ordered
[Architecture Quality Improvement Program](specs/2026-07-24-architecture-quality-improvement-plan.md)
is completed/reconciled historical evidence. It follows the completed Phase 0–7
[Architecture Migration](architecture-migration.md) without reopening that
historical ledger.

The final integration ledger is closed/reconciled at reviewed endpoint
`87c6f887f4afb719da89e68074551e9b8786ac1d`: 13 program/integration commits
since `fe474e0e55b3d1e8ae370534acb54a0a5fd9496b`, with 298 changed paths. The
exhaustive A-G partition union exactly covers all 298 changed paths:

- A: 29/29, 220,750 bytes/5,384 lines, valid complete CLEAN.
- B: 22/22, 359,459 bytes/8,776 lines, valid complete CLEAN.
- C: 14/14, 111,705 bytes/2,418 lines, valid complete CLEAN.
- D: 103/103, 410,314 bytes/9,494 lines, valid complete CLEAN.
- E: 150/150, 406,331 bytes/9,333 lines, valid complete CLEAN.
- Refreshed F: 19/19, 139,365 bytes/1,892 lines, valid complete CLEAN.
- G: 8/8, 36,717 bytes, valid complete CLEAN.

Slice 16 landed as `7deb8d8807f4e7eb52f7c9c8bd9e0ad30cb60727`
(`docs: close architecture quality program`). The three integration-fix commits
are the original Bundle F ledger fix
`ffeb86f0319efd28f6f360174ae640fa358761d0`
(`docs: reconcile architecture program ledger`), warning-state closure
`aea52b438713ce04fcad93ae32927ff156574aac`
(`docs: record integration warning closure`), and README/provider-catalog
closure `b64ceb7db9581bf3ebfab51f5803c513c1fdb549`
(`docs: align provider catalog status`). The prior valid complete exact-schema
cross-cutting review by Pi `openai-codex/gpt-5.6-sol` at committed endpoint
`b64ceb7` found the sole incomplete-ledger Warning: living ledgers omitted
refreshed F and `aea52b4`/`b64ceb7`. It found zero Critical or Suggestion
findings, omissions, forbidden tool uses, skips, truncations, or redactions.
Fresh exact-model Pi implementation fixed the ledger/test and metric
synchronization. A first focused review then found inaccurate
implementation/endpoint attribution and missing ratchets; those were corrected.
The final focused exact-schema G review covered 8/8 files and 36,717 bytes and
was valid CLEAN with no findings or coverage defects. That synchronization
landed as `87c6f887f4afb719da89e68074551e9b8786ac1d`
(`docs: sync final integration ledger`).

A fresh valid complete exact-schema cross-cutting re-review by Pi
`openai-codex/gpt-5.6-sol` at reviewed endpoint `87c6f88` covered A-G
manifests/reports, prior cross-cutting evidence, final ledger files, and
unchanged cross-contracts. The A-G manifest union exactly covers all 298 changed
paths. It returned `STATE: CLEAN`, `COVERAGE_COMPLETE: yes`,
`PARTITION_UNION_COMPLETE: yes`, and `VERDICT: CLEAN`, with zero Critical,
Warning, or Suggestion findings; `SCOPED_OMISSIONS: none`,
`FORBIDDEN_TOOL_USES: 0`, `SKIPPED_FILES: none`, `TRUNCATIONS: none`, and
`REDACTIONS: none`. Review stopped because the sole prior ledger Warning was
fixed and the fresh complete re-review was CLEAN; further review would add no
material value unless scope changes.

Latest stable verification for reviewed endpoint `87c6f88` is strict Mypy
across 169 source files, combined Mypy across 438 source/test files, and `just
check` at 4,829 passed / 2 skipped. Ruff formatting covers 480 files. Stable
metrics are 34 / 18 repository/source C901 findings, 81,738 / 121,191
source/test physical lines, 43 `ToolLoopTerminalUi` fields, one source ignore,
and 5,433 / 6,329 lines in `tool_loop_session.py` / `tui.py`. Docs are clean,
diff is clean, both theme sources are `pi`, and pre-commit is absent. Slice 14
stress evidence remains focused 20x, groups 10x, PTY smoke 5x, then the full
check; the latest PTY smoke is 8/8.

The architecture-quality program and final integration review are closed/reconciled. Its durable output is the
[2026-07-29 architecture quality assessment](2026-07-29-architecture-quality-assessment.md).
The explicit next architecture boundary is bounded transactional-reload
contract completion or formal reconciliation before ordinary product-parity
selection.

Progress:

- **Slice 0 — reviewed program plan: SHIPPED.** Commit `b4e9daa`
  (`docs: plan architecture quality improvements`) records the reviewed scope,
  invariants, ordering, acceptance criteria, and coordinator protocol; no
  production code changed.
- **PTY gate prerequisite from Slice 14: SHIPPED.** Commit `3d2fd0b`
  (`test: synchronize local-command PTY readiness`) replaces the
  notice-paint/readiness race in the multi-tool local-command PTY proof with an
  observable post-`TCSAFLUSH` raw-mode byte handshake. The formerly failing
  case passed 20 isolated runs, ten three-test grouped runs, its 49-test module,
  the eight-test PTY smoke gate, and the full 4,585-test check. Pi review was
  CLEAN in one round with zero findings. The rest of Slice 14 remains queued.
- **Slice 1 — living documentation and reproducible metrics: SHIPPED in this
  commit** (`docs: establish living architecture baseline`). The slice refreshes the architecture,
  migration notice, backlog, Pi 0.82 audit, docs index, and package description,
  and adds the read-only architecture metrics helper plus focused tests. The
  helper establishes `ToolLoopTerminalUi` baseline **B = 128** with explicit
  field names. Its Slice 1 snapshot reports 77,982 `src` Python lines, 112,778
  test Python lines (including the new focused tests), 39 repository / 23 `src`
  C901 findings, one `src` type-ignore, 5,085 `tool_loop_session.py` lines, and
  7,017 `tui.py` lines. The four focused tests and all 4,589 repository tests
  pass, `just docs-build` is clean, and Pi review is CLEAN in two rounds with
  zero findings (the second pass covers this final ledger/pointer update).
- **Slice 2 — canonical session-extension generation: SHIPPED in this commit**
  (`refactor: unify session extension state`). `_RunControlState` now owns one
  typed extension generation (activated runtime plus parsed flags) instead of a
  runtime handle plus 23 mirrored contribution fields. Commands, hooks, outboxes, renderers,
  providers, tools, shortcuts, and flags resolve through that generation; a
  narrow TUI adapter preserves live outbox/renderer lookup without adding a
  second source of truth. Focused extension/reload/RPC/TUI coverage passes (463
  tests), including a new exact outbox-identity and generation-rebind
  characterization; the full gate passes with 4,591 tests and two skips. One
  unrelated project-trust PTY selector timing failure passed both isolated and
  on the immediate full retry. Ruff, focused Mypy, and the docs build are clean.
  Reload ordering, custom-message delivery, old/new outbox separation, and the
  intentionally non-transactional malformed-flag behavior remain unchanged;
  transactional replacement stays deferred to Slice 3. The final Pi review is
  CLEAN with zero findings and no scoped omissions; one preceding transport
  attempt was INVALID before emitting a review and was retried fail-closed.
- **Slice 3 — transactional extension reload: rebuild plan SHIPPED in this
  commit** (`docs: plan transactional extension reload rebuild`). A first
  implementation attempt built a distributed prepare/apply/rollback transaction
  across the provider state, coding state, settings, keybindings, package
  resources, global theme state, and persisted defaults; repeated review kept
  finding fresh interleavings because compensating rollback and unsynchronized
  revision checks are not a concurrency model. That attempt was discarded
  without landing. The reviewed replacement is
  [Transactional extension reload — rebuild plan and concurrency contract](specs/2026-07-25-transactional-extension-reload-rebuild.md):
  one documented session synchronization boundary, candidate-only fallible
  preparation, a commit consisting solely of non-fallible pointer assignments
  over immutable generation state, and idempotent post-commit persistence and
  presentation. It also fixes the bounded sub-slices S3.0–S3.9, the stop
  conditions, and the behavioral scenario checklist. No production code changed
  in this commit; the sub-slices below it are still unimplemented. `git diff
  --check` and `just docs-build` are clean. Reaching an explicit Pi CLEAN with
  `openai-codex/gpt-5.6-sol` took 33 review rounds and 48 closed findings, with
  no skipped files and no truncations, before the confirming round over this
  ledger entry. Those findings tightened the guarded-state set until both sides
  of every lock were covered, added the publication gate and its atomic message
  cutoff so a candidate cannot overwrite a concurrently accepted mutation,
  replaced check-then-dispatch chrome and notification ports with
  generation-owned sinks that are read only while live, replaced copy-and-clear
  message delivery with an in-order gap-free cursor over an idempotent sink,
  bounded queue growth, and made "nothing is released under the lock" a general
  rule so finalizers cannot run inside a critical section. The round count is
  itself the slice's main finding: the abandoned attempt's mechanism was
  unsound, and the contract stabilized only once every cross-thread field named
  one guard taken by all of its readers and writers.

- **Slice 3 — extension reload safety-ratchet sub-slices S3.0–S3.9 LANDED;
  IDEAL CONTRACT PARTIAL.** Commits, in order: `6e66b74` (ruff-format baseline
  for the files the slice touches), `46cf091` (tool result renderers pinned to their originating call,
  so a reload mid-call cannot re-target them), `f61d492` (tool capabilities
  behind one immutable `ToolCapabilityState` with candidate `prepare_extensions`
  and non-fallible `publish`), `486272a` (`native/session_generation.py`: the
  generation pointer, its identity, and the session's single mutex),
  `94060c5` (keybinding overrides as one frozen value), `77a85c0` (layered
  settings as one frozen value with an I/O-ordering lock and stale-candidate
  refusal), `13f3df7` (coding provider binding, history, usage, and compaction
  guarded by the session mutex on both sides), `909ef78` (publication gate
  refusing extension mutations while a reload republishes), `5aad547`
  (thinking-level admission made atomic, its persistence ordered), `c935a22`
  (model defaults persisted only after the selection is live, with a fail-soft
  diagnostic), and this commit (candidate flags parsed before anything goes
  live; a malformed flag rejects the whole candidate).

  Every sub-slice passed `just check`, `just docs-build`, `git diff --check`
  and an explicit Pi CLEAN from `openai-codex/gpt-5.6-sol` with no skipped or
  truncated files. Review rounds per sub-slice: 1, 1, 3, 3, 4, 4, 2, 3, 3, 3,
  plus 33 for the plan itself. The full gate is 4,639 passed / 2 skipped.

  **Behavior intentionally changed** (the one characterized change in this
  slice): a malformed extension flag on `/reload` previously left the newly
  activated runtime live against the previous generation's flag values; it now
  rejects the candidate whole and keeps the prior generation, reporting
  `keeping the previous extensions`. Settings, keybindings, package roots, and
  workspace resources that reloaded successfully stay applied, and the rest of
  the reload runs against the unchanged generation.

  **Outstanding ideal clauses, recorded rather than claimed:** the candidate is
  not activated against an isolated staging host; timed-out registration is not
  sealed; rejected listeners/chrome requests are not candidate-owned/disposed;
  live-host chrome clears before activation; production consumers do not adopt
  one generation snapshot per operation; class-A mutation ports do not capture
  a generation id; and tool/renderer/lifecycle/presentation projections publish
  separately. `set_model` also persists part-way through its mutation, so its
  publication-gate admission narrows but does not close its race window. The
  [reconciled rebuild contract](specs/2026-07-25-transactional-extension-reload-rebuild.md)
  and dated assessment queue bounded completion or formal reconciliation.

- **Slice 4 — session command effect family: SHIPPED in this commit**
  (`refactor: extract session command effects`). The eight session-owned
  built-ins (status, compact, name, new, tree, resume, fork, and clone), together
  with their session-switch/tree/fork extension gates, now execute through one
  frozen, slotted, keyword-only `_SessionCommandEffects` bundle assembled from
  14 narrow run-scope collaborators. `_BuiltinCommandInterpreter.interpret`
  keeps the headless controller's built-in/resource/extension precedence and
  delegates the complete family once; provider/configuration and
  transfer/package/reload effects remain for Slices 5 and 6. A focused AST
  characterization freezes the executor shape, exact collaborator set, single
  delegation, and absence of the eight actions from the root interpreter.

  The six focused session/command/import-boundary modules pass 577 tests; the
  full gate passes 4,640 tests with two skips, `just docs-build` and
  `git diff --check` are clean, and the load-sensitive project-trust PTY module
  passes all six cases both alone and in the final full gate. The first full
  gate also caught and closed an overgrown `run()` composition shell before
  review. `interpret` complexity falls **102 -> 63** while repository/src C901
  totals remain **39 / 23**, `tool_loop_session.py` grows **5,308 -> 5,391**
  physical lines as the former nested chain becomes explicit typed ownership,
  and the `src` type-ignore count remains **1**. Pi review with
  `openai-codex/gpt-5.6-sol` is CLEAN in two rounds with zero findings, no
  skipped files, and no truncations; the second round confirms this final
  ledger and active-pointer diff.

- **Slice 5 — provider and configuration command effect family: SHIPPED in
  this commit** (`refactor: extract provider configuration command effects`).
  Hotkeys, changelog, copy-last-answer, settings, project trust, model
  selection, scoped-model viewing/mutation/cycling, login, and logout now
  execute through one frozen, slotted, keyword-only
  `_ProviderConfigurationCommandEffects` bundle. It composes directly with
  `_ProviderMutationEffects`, which remains the single owner for live
  model/auth mutation, context clearing, provider and usage rebinding, footer
  refresh, and persistence ordering. Live-terminal and captured-stream
  presentation remain separate. The still-inline Slice 6 `/reload` branch
  receives its shared settings and keybindings through a separate frozen
  `_ReloadConfigurationDependencies` bundle; no reload sequencing or behavior
  moved.

  A focused AST characterization freezes both dataclass shapes, the provider/
  configuration executor's exact ten collaborators, the reload bundle's exact
  two fields, the complete nine-action family absence from the root
  interpreter, its single closed delegation, and removal of the five loose
  provider/settings/keybinding parameters. The requested 12-module non-PTY
  gate passes **746 tests**; the PTY smoke gate passes **8 tests**, and the
  complete presentation-preservation PTY modules pass **55 tests**. The full
  gate passes **4,641 tests with two skips**; Mypy, Ruff, `git diff --check`,
  and `just docs-build` are clean.

  `_BuiltinCommandInterpreter.interpret` falls from **63 -> 38** complexity,
  **32 -> 29** direct parameters, and **671 -> 488** lines. The `run()`
  composition shell remains below its guard at **789 -> 795** AST lines.
  `tool_loop_session.py` grows **5,391 -> 5,486** physical lines as the
  extracted branches become explicit typed methods; repository/src C901 totals
  remain **39 / 23**, and the `src` type-ignore count remains **1**. Pi review
  with `openai-codex/gpt-5.6-sol` is CLEAN in two rounds with zero findings, no
  skipped files, and no truncations; the second round confirms this final
  ledger and active-pointer diff.

- **Slice 6 — transfer, package, and reload command families: SHIPPED.**
  Commit `refactor: extract transfer and reload command effects` moves native
  session export/import/share through a frozen, slotted, keyword-only
  `_TransferCommandEffects` owner.
  `/reload` executes through a separate typed `_ReloadCommandEffects` owner
  whose explicit phases preserve configuration and package/resource
  recomposition, extension activation/publication, provider refresh,
  tool-capability publication, terminal refresh, publication-gate closure,
  post-gate lifecycle firing, and the final diagnostic order. The uncommitted
  R3 attempt sought to move replacement `session_start` before pointer
  publication; the R3a/R3b/R3c1a/R3c1b/R3c1c/R3c2/R3c3 split leaves this
  shipped order unchanged until
  R3c3 owns the two documented candidate-effect visibility changes.
  `_ProviderMutationEffects` remains authoritative for reload provider refresh,
  fallback, unavailable binding, usage rebinding, and default-persistence
  diagnostics.

  `_BuiltinCommandInterpreter` now routes only the session,
  provider/configuration, transfer, and reload families before applying its
  closed footer policy. Its complexity falls **38 -> 9**, direct parameters
  **29 -> 2**, and size **488 -> 27** lines. The `run()` composition shell
  falls **795 -> 772** AST lines, and `tool_loop_session.py` falls
  **5,486 -> 5,427** physical lines. Repository/src C901 totals fall
  **39 / 23 -> 38 / 22**, with the `src` type-ignore count unchanged at
  **1**.

  One focused AST test freezes the three new dataclass shapes, exact
  collaborators, complete transfer/reload action absence from the root, four
  exact delegation paths, phased reload order, the exact publication context,
  post-gate reload lifecycle event/reason (still the committed baseline until
  R3c3), final diagnostic/footer ordering,
  narrow root parameters, and removal of the transitional reload dependency
  bundle. The
  requested non-PTY gate passes **949 tests**; PTY smoke passes **8 tests** and
  the complete real-TUI PTY module passes **49 tests**. `just check` passes
  **4,642 tests with two skips**, including Ruff and Mypy. Documentation and
  final diff gates are clean. No changelog entry applies because behavior is
  unchanged. Pi review with `openai-codex/gpt-5.6-sol` at high thinking is
  CLEAN in two rounds. Round 1 reported zero Critical or Warning findings and
  one Suggestion to strengthen the exact publication-gate, lifecycle,
  diagnostic, and footer-order characterization; it was accepted and fixed.
  Round 2 was CLEAN with zero findings, no skipped or truncated files, and no
  subagent contribution. The active pointer advances to Slice 7.

- **Slice 7 — strict extension and public-facade typing: SHIPPED in this
  commit** (`refactor: enforce strict extension API typing`). The
  existing enumerated strict-equivalent Mypy override now covers
  the exact seven-module surface: `pipy_harness.extensions`,
  `pipy_harness.native.extensions`, `extension_loader`, `extension_types`,
  `extension_ui`, `extension_runtime`, and `extension_hooks`. Six exact module
  entries were added because `native.extensions` was already selected; the
  override inventory grows **12 -> 18** entries without a wildcard, global
  `strict = true`, exclusion, relaxed sub-flag, suppression, or new unchecked
  `Any`.

  The stable façade still exposes the same ordered **97 -> 97** `__all__`
  names. Each name now imports directly from its authoritative owner:
  extension value/protocol objects from `extension_types`, render helpers from
  `extension_ui`, dispatchers from `extension_hooks`, activation/runtime values
  from `extension_runtime`, discovery values from `native.extensions`, and the
  existing provider seams from their provider owners. Characterization checks
  the exact inventory, resolution and owner identity of all 97 names, the exact
  seven-module strict frontier and strict-equivalent flags, and the deliberate
  `extension_runtime` compatibility identities. The headless editor-component
  getter and terminal-input listener now narrow optional driver capabilities
  through runtime-checkable protocols; JSON round trips validate every decoded
  scalar, list, and string-keyed mapping recursively before returning it.

  The combined selected-surface diagnostic falls **54 errors in four files ->
  zero**. Diagnostic `mypy --strict src` falls **143 errors in 41 files -> 67
  errors in 35 files**: the additional reduction comes from making the
  already-used runtime compatibility aliases explicit for strict TUI,
  tool-loop, agent-request, and tool-renderer consumers. `just typecheck` is
  clean. The requested focused extension/API/import-boundary gate passes **418
  tests**; extension discovery, activation, lifecycle, notification, and golden
  conformance gates all report `"passed": true`. Repository/src C901 remains
  **38 / 22**, and the `src` type-ignore count remains **1**. No changelog entry
  applies because runtime behavior and the public API are unchanged.
  `just check` passes **4,646 tests with two skips**; `just docs-build` and
  `git diff --check` are clean.

  Fresh read-only Pi review used `openai-codex/gpt-5.6-sol` at high thinking,
  with no subagents and no skipped or truncated diff. Round 1 reported zero
  Critical, three Warning, and zero Suggestion findings: prove source-level
  façade owner provenance, freeze the complete strict configuration rather
  than only the selected subset, and replace the extension spec's stale
  tentative façade placement. All three were accepted and fixed. Round 2
  reported one Warning that the `extension_types` module documentation still
  named the pre-extraction UI and indirect façade owners; it was accepted and
  fixed. One empty-log Fish transport stall before Round 2 produced no review
  and consumed no round; the approved model was verified directly before the
  fresh retry. Round 3 verified all four prior findings fixed and reported one
  remaining Warning that this ledger still said review pending and had not
  advanced the pointer. That finding was accepted. Because the task required
  the ledger/pointer update only after CLEAN, the operator explicitly
  authorized this factual update plus one exceptional fourth fresh
  confirmation beyond the normal three-round cap. The active pointer now
  advances to Slice 8. Round 4 was CLEAN with zero Critical, Warning, or
  Suggestion findings. Cumulatively the reviews reported five Warnings; all
  five were accepted and fixed, with zero rejected or deferred findings.

- **Slice 8a — strict settings, package-manager, and session-tree-command
  typing: SHIPPED IN THIS COMMIT** (`refactor: enforce strict settings and
  session support`). The exact selected
  inventory is `pipy_harness.native.settings`,
  `pipy_harness.native.package_manager`, and
  `pipy_harness.native.session_tree_commands`; no other Slice 8 module or
  wildcard joins the ratchet. The enumerated strict-equivalent override grows
  **18 -> 21** exact entries, keeps every explicit strict sub-flag enabled, and
  adds no global `strict`, exclusion, relaxed flag, suppression, cast, or
  unchecked `Any`.

  Settings integer boundaries now assign dynamic values to `object` before
  executable `bool`/`int` narrowing, retaining the existing default/error
  split, explicit boolean rejection, and zero-timeout behavior. Package
  settings reads validate the decoded top-level document into a string-keyed
  object and keep nested values intact; lenient display reads, strict mutation
  reads, corrupt-file clobber refusal, string entries, object entries, and all
  four per-package resource-filter arrays survive the same read/modify/write
  paths. Session-tree DFS imports and uses the authoritative
  `SessionTreeNode`, with its Ruff-only annotation suppression removed.

  The selected diagnostic falls **8 errors in three files -> zero**.
  Diagnostic `mypy --strict src` falls **67 errors in 35 files -> 59 errors in
  32 files**; the remaining findings belong to later Slice 8 batches and Slice
  9. `just typecheck` is clean across **424 source files**. Characterization
  now freezes the ordered 21-entry override, every strict-equivalent flag,
  absence of global strict/exclusions/native wildcarding, nested integer
  boolean rejection, and complete object-form package filter preservation.
  The required focused gate passes **312 tests** and the broader owner-surface
  gate passes **418 tests**. The final `just check` rerun passes **4,651 tests
  with two skips**; an earlier run's single settings-dialog PTY readiness
  failure passed immediately in isolation, and a separate rerun that stalled
  in an unrelated resume/compact PTY case was stopped after that complete
  two-case module passed directly. Repository/src C901 remains **38 / 22** and
  the `src` type-ignore count remains **1**. `just docs-build` and final diff
  hygiene are clean. No changelog entry applies because behavior and public
  APIs are unchanged.

  Fresh read-only Pi review used the required
  `openai-codex/gpt-5.6-sol` model at high reasoning with no subagents. Round 1
  returned explicit CLEAN with zero Critical, Warning, or Suggestion findings
  and said the cycle can close. No review fix or scope change followed, so a
  second identical pass would add no value under the bounded review policy.
  The active pointer remains **Slice 8**. The recommended next bounded owner
  group is
  `pipy_harness.native.package_resources`,
  `pipy_harness.native.package_runtime`, and
  `pipy_harness.native.resources`.

- **Slice 8b — strict package-resource, package-runtime, and resource-dispatch
  typing: SHIPPED.** Commit `515b463`
  (`refactor: enforce strict package resource support`) records the exact selected
  inventory is `pipy_harness.native.package_resources`,
  `pipy_harness.native.package_runtime`, and
  `pipy_harness.native.resources`; no other Slice 8 module or wildcard joins
  the ratchet. The enumerated strict-equivalent override grows **21 -> 24**
  exact entries and retains every explicit strict sub-flag plus the three
  required global-only flags. It adds no global `strict`, exclusion, relaxed
  flag, suppression, cast, or unchecked `Any`.

  The direct selected diagnostic remains **zero errors before -> zero after**.
  Normal repository-graph checking reproduced the existing
  `no_implicit_reexport` finding for the composition root's import of
  `PackageResourceRoots` through `package_runtime`. That module now makes its
  existing same-name export explicit; an identity characterization proves it
  is still the exact class object authoritatively defined by
  `package_resources`, with no duplicate type, protocol, moved ownership, or
  compatibility shim. Diagnostic `mypy --strict src` falls **59 errors in 32
  files -> 58 errors in 32 files**; the other `tool_loop_session` finding is
  unrelated, so the affected-file count does not fall.

  No package/resource implementation was redesigned. String and object package
  entries, all four filter arrays, the originating-scope marker, dynamic
  manifest TOML, convention fallback, invalid-manifest failure, local
  canonicalization/containment, managed-cache scope selection, source
  precedence, safe labels, resource size caps/deduplication, trust gates,
  reserved-command handling, dispatch outcomes, theme composition, and
  metadata privacy retain their executable `object`/`Mapping`/`Sequence`/`str`
  narrowing and existing behavior. Runtime composition still only reads
  directories/manifests/theme data: it does not clone, fetch, import package
  code, or run lifecycle scripts.

  Characterization freezes the new same-object export and the complete ordered
  24-entry strict override, every strict-equivalent sub-flag, both required
  global warning flags, the required global `strict_bytes` flag, and the
  absence of global strict/exclusions/native wildcarding.
  The focused owner gate passes **228 tests**, the broader owner surface passes
  **305 tests**, and `just check` passes **4,652 tests with two skips**.
  `just typecheck` is clean across **424 source files**. Repository/src C901
  remains **38 / 22**, and the `src` type-ignore count remains **1**. No
  changelog entry applies because behavior and public APIs are unchanged.

  Independent Pi review with `openai-codex/gpt-5.6-sol` at high reasoning is
  CLEAN after two rounds. Round 1 reported one Warning that Mypy 1.20's
  `--strict` bundle includes the global-only `strict_bytes` flag; the finding
  was accepted, fixed in the configuration/test/docs, and followed by a full
  green gate. Round 2 reported zero Critical, Warning, or Suggestion findings
  and explicitly said another cycle would add no value. One model-list
  preflight stalled before Round 1, was stopped without consuming a review
  round, and the exact approved model was then verified directly before the
  fresh review. No subagent participated. The active pointer remains **Slice
  8**. From the remaining
  diagnostics, the next bounded terminal-support/rendering group is
  `pipy_harness.native.repl_input`,
  `pipy_harness.native.autocomplete_provider`, and
  `pipy_harness.native.tool_renderers`; it remains deferred until a separately
  selected Slice 8 batch.

- **Slice 8c — strict REPL-input, autocomplete-provider, and tool-renderer
  typing: SHIPPED IN THIS COMMIT.** The exact selected
  inventory is `pipy_harness.native.repl_input`,
  `pipy_harness.native.autocomplete_provider`, and
  `pipy_harness.native.tool_renderers`; no other Slice 8 module or wildcard
  joins the ratchet. The enumerated strict-equivalent override grows **24 ->
  27** exact entries after the Slice 8b group, preserving every explicit
  per-module strict sub-flag and all three required global-only flags. There is
  no global `strict`, exclusion, relaxed flag, suppression, new cast, or
  unchecked `Any`.

  The selected diagnostic falls **11 errors in three files -> zero**.
  Diagnostic `mypy --strict src` falls **58 errors in 32 files -> 47 errors in
  29 files**; the selected owners contribute no remaining findings.
  `call_provider_method` keeps its deliberately duck-typed extension surface,
  including snake-before-camel lookup, non-callable handling, unchanged
  positional forwarding, and provider exception propagation, while arguments
  and results cross an explicit `object` boundary before the existing lenient
  coercion paths. Focused characterization pins those dispatch rules,
  including present falsey non-callables and falsey callable objects.

  Prompt-toolkit remains optional and dynamically loaded. Narrow local
  protocols describe only its key-binding factory, decorator, event, and
  buffer operations, with a generic return preserving concrete fake and real
  key-binding class shape. Enter/Ctrl-J still submit and Escape+Enter/
  Escape+Ctrl-J still insert a newline. Automatic backend selection remains
  slash-menu -> prompt-toolkit -> readline -> plain, with explicit failures and
  automatic fail-soft behavior unchanged.

  Chrome and tool rendering now narrow factory/component values through the
  authoritative runtime-checkable `ChromeComponent` and
  `ToolRenderComponent` contracts from `extension_types`. The captured
  `_ToolLoopRenderer._dispatch_render` uses the TUI dispatcher's authoritative
  renderer callable, `ToolRenderContext`, mapping, content/details, and return
  types. A typed pending-render record preserves argument/state correlation;
  the original renderer remains pinned across reload/removal, and manually
  injected opaque details still cross only the existing internal compatibility
  seam. Bare strings/lists, zero-argument factories, width forwarding,
  truncation, ordinary fail-soft failures, interrupt propagation, fallback,
  output bytes/order, duration/error presentation, and one-way imports are
  unchanged.

  `just typecheck` is clean across **424 source files**. The focused
  terminal-support gate passes **343 tests**, the broader owner surface passes
  **524 tests**, PTY smoke passes **8 tests**, and the focused renderer/TUI PTY
  gate passes **50 tests**. The full repository gate passes **4,659 tests with
  two skips** after clean Ruff and Mypy phases, and `just docs-build` is clean.
  Repository/src C901 remains **38 / 22**, and the `src` type-ignore count
  remains **1**. Fresh read-only Pi review used
  `openai-codex/gpt-5.6-sol` at high reasoning and is **CLEAN in two rounds**.
  Round 1 reported one Warning and no Critical or Suggestion findings:
  truthiness-based snake/camel selection did not honor the documented
  precedence for present falsey attributes. The Warning was accepted and
  fixed with sentinel-based presence detection plus focused falsey
  callable/non-callable characterization. Round 2 closed that finding and
  reported no new findings, skipped content, or truncation; the reviewer said
  another cycle would add no value. Cumulative accepted/fixed/rejected/
  deferred counts are **1 / 1 / 0 / 0**. The direct exact-model PTY preflight
  completed normally, so no failed preflight consumed a round. No subagent
  participated. Public extension contracts, optional dependencies, terminal
  restoration, product session content, and metadata-archive privacy are
  unchanged. No changelog entry applies. The active pointer remains **Slice
  8**. Commit subject:
  `refactor: enforce strict terminal rendering support`.

- **Slice 8d — strict routing, built-in OAuth-provider, and models.json
  policy typing: SHIPPED IN THIS COMMIT.** The exact selected
  inventory is `pipy_harness.native.routing`,
  `pipy_harness.native.oauth_providers`, and
  `pipy_harness.native.models_json`; no other Slice 8 module or wildcard joins
  the ratchet. The enumerated strict-equivalent override grows **27 -> 30**
  exact entries after the Slice 8c group, retaining every explicit per-module
  strict sub-flag and all three required global-only flags. There is no global
  `strict`, exclusion, relaxed flag, suppression, cast, unchecked `Any`, or
  broad ellipsis callable.

  The selected diagnostic falls **19 errors in three files -> zero**.
  Diagnostic `mypy --strict src` falls **47 errors in 29 files -> 28 errors in
  26 files**; the selected owners contribute no remaining findings.
  `models_json` continues to accept decoded JSON as `object`, then executably
  narrows string-keyed objects and arrays at validation and merge boundaries.
  The unused private `_opt_str_map` helper was removed after complete call-site
  inspection found no consumer. Parsing leniency, path-qualified errors,
  boolean rejection for numbers, number-to-int conversion, explicit-zero cost
  overrides, absent/present-empty field semantics, deep compatibility merging,
  dynamic registration, refresh, and precedence remain unchanged.

  Built-in OAuth transports now satisfy a keyword-aware protocol with the
  exact `headers` and `data` call shape. Credential and decoded response
  objects are string-keyed object mappings, the registry factory has one
  precise callable type, and the existing three IDs remain in order with
  unknown lookup returning `None`. A runtime-checkable modifier-provider
  protocol and a single `ModelRowsModifier` callable type connect the optional
  Copilot row mutation to catalog-state closures without broadening the
  extension OAuth contract. Routing narrows compatibility data into copied
  `dict[str, object]` request additions while preserving the OpenRouter and
  Vercel URL gates, nesting, truthy forwarding, and empty-block omission.

  `just typecheck` is clean across **424 source files**. The focused
  model-policy gate passes **221 tests**, the broader owner surface passes
  **453 tests**, and the full repository gate passes **4,664 tests with two
  skips** after clean Ruff and Mypy phases. `just docs-build` is clean.
  Repository/src C901 remains **38 / 22**, and the `src` type-ignore count
  remains **1**. Provider request behavior, OAuth transport/status/expiry and
  credential behavior, model mutation and modifier ordering, provider
  construction, authoritative identities and one-way imports, full-content
  product sessions, metadata-only archive privacy, and both `pi` theme sources
  remain unchanged. `docs/provider-catalog.md` remains accurate because no
  provider contract changed, and no changelog entry applies.

  Fresh read-only Pi review used `openai-codex/gpt-5.6-sol` at high reasoning
  and is **CLEAN in one round**. Round 1 reported zero Critical, Warning, or
  Suggestion findings and explicitly said the cycle can close with no follow-up
  warranted absent further changes. Cumulative accepted/fixed/rejected/deferred
  counts are **0 / 0 / 0 / 0**. The reviewer inspected every touched file and
  the relevant owners/tests without truncation; its read-only tools did not
  expose command execution or a direct VCS-diff operation, so the implementer
  independently supplied and reran all verification evidence.

  The Fish-captured model-list preflight stayed alive past its 60-second bound
  with an empty log; the tmux session and process group were terminated, no
  orphan survived, and no review had begun, so it consumed no round. A direct
  PTY check with the isolated Pi agent directory confirmed the exact approved
  model. One subsequent fresh review-command launch exited with an empty log,
  no sentinel, no surviving process, and no worktree change; it was an invalid
  transport attempt and also consumed no round. The fresh diagnostic retry
  produced the complete CLEAN report. No subagent participated. The active
  pointer remains **Slice 8**. Commit subject:
  `refactor: enforce strict provider model policy`.

- **Slice 8e — strict provider catalog-state, construction, and ds4 support
  typing: SHIPPED IN THIS COMMIT.** The exact selected
  inventory is `pipy_harness.native.catalog_state`,
  `pipy_harness.native.provider_construction`, and
  `pipy_harness.native.ds4`; no other Slice 8 module or wildcard joins the
  ratchet. The enumerated strict-equivalent override grows **30 -> 33** exact
  entries after the Slice 8d group, retaining every explicit per-module strict
  sub-flag and all three required global-only flags. There is no global
  `strict`, exclusion, relaxed flag, suppression, cast, unchecked `Any`,
  dependency, or C901 pin.

  The selected diagnostic falls **5 errors in three files -> zero**.
  Diagnostic `mypy --strict src` falls **28 errors in 26 files -> 23 errors in
  23 files**; the selected owners contribute no remaining findings.
  `ds4_preset_dict` exposes its unchanged static JSON-compatible value as
  `dict[str, object]`. Catalog-state's optional extra-provider overlay is typed
  as a mapping to the authoritative `ProviderConfig`, and `auth_status`
  returns the authoritative `AuthStatus`. `_FailedAuthProvider.complete`
  imports and uses the exact `ProviderRequest`, `ProviderResult`,
  `StreamChunkSink`, and `CancelToken` identities from the provider boundary,
  preserving its deliberate ignored optional values and sanitized
  `CatalogAuthError` result.

  `just typecheck` is clean across **424 source files**. The focused
  provider-construction gate passes **308 tests**, the broader provider/catalog
  surface passes **448 tests**, and the full repository gate passes **4,664
  tests with two skips** after clean Ruff and Mypy phases. `just docs-build`
  and `git diff --check` are clean. Repository/src C901 remains **38 / 22**,
  the `src` type-ignore count remains **1**, and the active pointer remains
  **Slice 8**.

  Provider/catalog behavior, ds4 preset/shim equivalence, extra/file/dynamic
  precedence, extension replacement/restoration/order, OAuth modifiers,
  auth-status side-effect rules, request shapes, fail-closed auth, credentials,
  full-content product sessions, and metadata-only archive privacy are
  unchanged. `docs/provider-catalog.md` remains accurate because no provider
  contract or behavior changed, and no changelog entry applies.

  Fresh read-only Pi review used `openai-codex/gpt-5.6-sol` at high reasoning
  and is **CLEAN in one round**. Round 1 reported zero Critical, Warning, or
  Suggestion findings, inspected all seven touched files and the relevant
  authoritative owners/tests without relevant truncation, and said another
  pass would add no value unless the worktree changed. Cumulative
  accepted/fixed/rejected/deferred counts are **0 / 0 / 0 / 0**. The direct
  PTY model preflight completed normally; there were no failed preflight or
  transport attempts and no unconsumed review attempts. The tmux session was
  closed after the sentinel-bearing report, no reviewer process survived, and
  the worktree contained only the expected seven files. No subagent
  participated. Commit subject:
  `refactor: enforce strict provider construction support`.

- **Slice 8f — strict native tools package typing: COMPLETE.** The exact
  selected override entry is
  `pipy_harness.native.tools.*`; it is the one narrow package wildcard in this
  batch, while no export-distribution owner, other Slice 8 module, Slice 9
  owner, or broader `pipy_harness.native.*` wildcard joins the ratchet. The
  enumerated strict-equivalent override grows **33 -> 34** ordered entries
  after the Slice 8e group, retaining every explicit per-module strict sub-flag
  and all three required global-only flags. There is no global `strict`,
  exclusion, relaxed flag, suppression, cast, unchecked `Any`, dependency, or
  C901 pin. Characterization freezes the package's current recursive file
  inventory so future additions or removals require an intentional test
  inventory update.

  The selected diagnostic falls **2 errors in two files -> zero**, with all
  **11** package source files strict-clean. Diagnostic `mypy --strict src`
  falls **23 errors in 23 files -> 21 errors in 21 files**: one remaining
  error is the separately checkpointed
  `pipy_harness.native.export_distribution` return boundary for Slice 8g, and
  the other 20 are the deferred `HarnessStatus` implicit-export cascade rooted
  in `pipy_harness.models` for Slice 9.
  `base._validate_integer` now accepts its dynamically decoded value as
  `object` and uses the existing executable `bool` rejection and `int`
  narrowing before returning it. `edit_diff._atomic_write` now accepts the
  authoritative `pathlib.Path` already returned by workspace preflight.

  `just typecheck` is clean across **424 source files**. The focused tool,
  schema, capability, import-boundary, and strict-frontier gate passes **392
  tests**. The full repository gate passes **4,665 tests with two skips** after
  clean Ruff and Mypy phases; `just docs-build` and `git diff --check` are
  clean. Repository/src C901 remains **38 / 22**, and the `src` type-ignore
  count remains **1**. No changelog entry applies because behavior and public
  contracts are unchanged.

  Integer validation retains exact value identity, explicit boolean rejection,
  minimum/maximum behavior, error text, and field-path propagation.
  Edit-diff retains containment and ignored-path policy, complete
  read/parse/apply validation before writing, sibling temporary files, original
  mode preservation, atomic replacement, failure cleanup and exception
  propagation, and diff streaming only after successful replacement. Tool
  schema materialization, request/result identities, provider visibility,
  full-content product sessions, metadata-only archive privacy, credentials,
  imports, and both `pi` theme sources remain unchanged. No subagent
  participated in implementation or review. Direct read-only Claude Code
  review with `claude-opus-5` at `xhigh` effort found no production behavior or
  typing defect. Its documentation and inventory suggestions produced the
  precise narrow-package-wildcard wording and recursive file characterization
  included here. The active pointer remains **Slice 8**. This commit closes
  the Slice 8f checkpoint; Slice 8g may begin only from the clean committed
  state. Commit subject:
  `refactor: enforce strict native tool typing`.

- **Slice 8g — strict native export/distribution typing: COMPLETE.** The sole
  selected owner is `pipy_harness.native.export_distribution`; no other root
  module, Slice 9 owner, or broader native wildcard joins the ratchet. The
  enumerated strict-equivalent override grows **34 -> 35** ordered entries,
  with the exact module immediately after `pipy_harness.native.tools.*`, while
  every explicit per-module strict sub-flag and all three global-only flags
  remain enabled. There is no global `strict`, exclusion, relaxed flag,
  suppression, cast, assertion workaround, unchecked `Any`, dependency, C901
  pin, schema duplication, or public TypedDict hierarchy.

  The selected diagnostic falls **1 error in one file -> zero**. Diagnostic
  `mypy --strict src` falls **21 errors in 21 files -> 20 errors in 20 files**;
  every remaining finding is the deferred Slice 9 `HarnessStatus`
  implicit-export cascade rooted in `pipy_harness.models`.
  `session_export_payload` keeps its naturally inferred heterogeneous static
  mapping and returns through a private generic-keyed
  `Mapping[..., object] -> dict[str, Any]` redaction helper. The public
  `redact_export_value(Any) -> Any` boundary delegates its mapping branch to
  the same helper and remains intentionally dynamic. Executable
  characterization freezes arbitrary mapping-key `str()` conversion,
  recursive mapping/list processing, tuple-to-list conversion, scalar
  preservation, sensitive-key and string redaction, and the exact ordered
  top-level payload keys `header`, `entries`, `leafId`, `systemPrompt`, and
  `tools`.

  `just typecheck` is clean across **424 source files**. The focused
  export/import/architecture/strict-frontier gate passes **244 tests**, all
  **11** export/distribution conformance checks pass, and the full repository
  gate passes **4,667 tests with two skips** after clean Ruff and Mypy phases.
  `just docs-build` and `git diff --check` are clean. Repository/src C901
  remains **38 / 22**, and the source type-ignore count remains **1**.

  Full-tree HTML and active-branch JSONL bytes and identities, import
  collision handling/source immutability/missing-CWD replacement, secret-gist
  payloads and cancellation, update/version/install planning, recursive
  redaction, authorization-token exclusion, one-way imports, full-content
  native product sessions, and metadata-only workflow-archive privacy remain
  unchanged. `docs/export-distribution.md` remains accurate without an edit,
  and no changelog entry applies because observable behavior is unchanged.
  Implementation was direct with no coding subagent. The independent gate is a
  fresh read-only Claude Code review using exactly `claude-opus-5` at `xhigh`
  effort over the complete final code, tests, configuration, and closeout
  documentation; detailed round outcomes belong in the summary-safe workflow
  record and implementer report rather than durable review narration here.
  Commit subject:
  `refactor: enforce strict export distribution typing`.

  This closes Slice 8 completely. The active pointer advances to **Slice 9 —
  strict source frontier completion**; its `HarnessStatus` export cleanup and
  package-wide strict-frontier work remain untouched.

- **Slice 9 — strict source frontier completion: LANDED.** Commit `b08b02a`
  (`refactor: complete strict source frontier`). The enumerated strict-equivalent
  Mypy override is replaced rather than supplemented: **35 entries -> 2** exact
  structured package patterns,
  `pipy_harness.*` and `pipy_session.*`. Every per-module strict-equivalent
  sub-flag and all three global-only flags remain enabled. There is no global
  or per-module `strict = true`, exclusion, relaxed flag, suppression, cast,
  assertion workaround, unchecked `Any`, dependency, or C901 pin. Top-level
  tests retain their existing non-strict baseline, while `just typecheck`
  continues to run `mypy src tests` and CI continues to invoke that
  authoritative gate.

  `pipy_harness.status.HarnessStatus` remains the sole authoritative enum
  definition. `pipy_harness.models` now uses a same-name import alias to make
  its established export explicit under `no_implicit_reexport`; the models,
  top-level package, SDK, native result objects, and provider annotations still
  resolve to that exact object with the same members and serialized values.
  Combined source/test checking also exposed four test accommodations:
  `native.models.HarnessStatus`, `verification.NativeVerificationRequest`, the
  terminal driver's stdlib monkeypatch modules, and `chrome.Path`. Only the
  required public `pipy_harness.models.HarnessStatus` path becomes an explicit
  same-object export. The other consumers now import the authoritative status
  and verification-request owners or patch `pathlib`/stdlib modules directly,
  without adding test-only production exports, wrappers, or duplicate types.
  The complete configuration characterization now lives in the dedicated
  `tests/test_typing_config.py`; the former native-tools recursive inventory
  pin is intentionally removed because the final package-wide pattern covers
  present and future source modules without a per-package file list.

  Diagnostic `mypy --strict src` falls **20 errors in 20 files -> zero** and is
  clean across all **165 source files**. `just typecheck` is clean across
  **425 source/test files**. The focused architecture/SDK/harness gate passes
  **271 tests**, and `just check` passes **4,667 tests with two skips** after
  clean Ruff and Mypy phases. `just docs-build` and `git diff --check` are
  clean. Repository/source C901 remains **38 / 22**, and the source type-ignore
  count remains exactly **1**, the documented runtime-selected stdlib subclass
  line in `native/http.py`.

  This is behavior-preserving typing and export ownership work, so no changelog
  entry applies. SDK, runner, adapter, provider success/failure/cancellation,
  CLI/JSON/RPC/tool/session/archive schemas, public imports, and one-way
  architecture boundaries are unchanged. Credentials and tokens remain
  excluded, the native product session remains full-content, the workflow
  archive remains metadata-only, and both theme sources remain `pi`.
  Implementation was direct with no coding subagent; the contemporaneous
  review-ready record preceded the landed commit above. The historical pointer
  then advanced to **Slice 10 — one-shot runtime convergence decision**; no Slice 10
  implementation is included, and its one-shot/runtime-convergence outcome
  remains intentionally undecided.

- **Slice 10 — one-shot runtime convergence decision: LANDED.** Commit
  `ed85849` (`refactor: decide one-shot runtime convergence`). Executable
  overlap/difference contracts select intentional
  compatibility ownership rather than a false `AgentLoop` convergence. An
  ordinary successful provider completion has equivalent final text, token
  counters, and text-delta delivery at the shared provider-turn boundary. In
  contrast, the one-shot path consumes pipy-owned metadata fixture intents,
  may synthesize one special observation/follow-up request, optionally consumes
  separately injected human-reviewed patch/verification requests, catches
  provider exceptions into its historical failed-result shape, and emits the
  metadata-only `native.session/provider/tool/...` archive lifecycle. The
  canonical loop consumes advertised `ProviderToolCall` values, owns full
  history and provider/tool iteration, and correctly ignores those metadata
  fixtures.

  The former `NativeAgentSession` is therefore renamed, without an alias or
  deprecation shim, to `NativeHarnessCompatibilityRuntime`. Both buffered and
  streamed turns now run under the canonical `ProviderTurnExecutor` delta gate
  and typed outcome contract. A thin private compatibility adapter performs the
  final injected `ProviderPort.complete(...)` call with an exact
  `ProviderTurnDeltaPolicy` that preserves initial-turn-only text streaming,
  no compatibility reasoning sink, and buffered follow-up turns. The initial
  text-delta capability is one required boolean derived beside the SDK event
  projection; both follow-up call sites explicitly disable it, so no callback
  is retained as a second or presence-only delta path. No parallel
  provider-execution pipeline remains. The old
  `native.tool.ToolPort`, supervised patch apply, and verification contracts do
  not match canonical model-driven tool calls and remain isolated rather than
  being wrapped as equivalent. Product `--mode json`, `--print`, interactive,
  and RPC execution already use the canonical coding session and `AgentLoop`;
  only `pipy run` and the narrow Python SDK retain this compatibility runtime.

  Boundary characterization adds executable contracts that drive independent,
  observable providers through real executor calls on both sides and compare
  emitted text deltas, final text, and all six normalized usage counters; prove
  metadata-fixture divergence while both providers execute; pin the exact
  text-only/no-cancellation wire shape and fail-loud rejection of unsupported
  channels; prove policy/executor/adapter wiring defects escape rather than
  being projected as provider failures, while `ProviderRequest` construction or
  validation failures, genuine provider `ValueError` and `TypeError`
  exceptions, and typed provider cancellation retain the historical failed
  result/archive lifecycle without private detail; enforce removal of the retired
  runtime name from both module and package exports; and statically prove that
  the executor plus its thin adapter are the only provider-completion ownership
  path. Focused gates pass **336** agent/session/architecture tests, **145**
  SDK/CLI/archive/workspace tests, and **291** coding-loop/import/RPC tests;
  focused Ruff and Mypy are clean. `just check` is clean across **426
  source/test files** and **4,679 passed / 2 skipped**. The first full run had
  two unrelated known PTY-readiness failures
  (project-trust selection and bracketed-paste exit); both passed immediately
  in isolation, and the complete rerun passed. `just docs-build` and final diff
  hygiene are clean. The after snapshot is **79,498** source / **116,248** test
  Python physical lines and remains **38 / 22** repository/source C901
  findings, **1** documented source type-ignore, **165** strict source files,
  and **128** `ToolLoopTerminalUi` fields; no dependency, `Any`, suppression, Mypy
  exclusion, C901 pin, schema, trust, session-format, or privacy change is
  introduced. The active pointer advances to **Slice 11 — editor state
  extraction**.

  Independent review used the exact `claude-opus-5` model at xhigh effort in a
  read-only harness. One initial harness invocation was **INVALID** because of
  an absolute Glob target; it had zero findings and did not count as a review.
  Four valid complete-bundle rounds followed, with **12 valid findings: 4
  Warnings + 8 Suggestions**. All four Warnings and five material Suggestions
  were accepted and fixed (**9 total**). The final round had **3 Suggestions**
  and zero Critical or Warning findings. Those final suggestions were
  proportionally declined because they concern unreachable or unused
  consistency cleanup introduced by prior fixes: a duplicate gated sink-policy
  expression, invariant subtype consistency on an impossible cancellation
  branch, and defensive wrapping of a buffered sink the executor does not call.
  This was the second consecutive self-inflicted-churn signal; another
  fix/re-review round would not materially improve correctness, privacy, API,
  architecture, tests, or confidence. No substantive finding remains. Every
  valid round had zero skipped files, truncations, redactions, and forbidden
  tool uses. Pi implementer self-verdicts were discarded. The contemporaneous
  “commit pending” state is superseded by landed commit `ed85849`.

- **Slice 11 — editor state extraction: LANDED.** Commit `8f61ffe`
  (`refactor: extract terminal editor state`). A new
  stdlib-only `native.editor_state.EditorState` is the single typed owner for
  editable buffer/cursor state, slash and completion selection/anchors,
  session-local prompt recall and draft navigation, bounded undo/redo, decoded
  paste hand-off, initial-text rehydration, extension completion-factory
  retention, and the terminal steering/follow-up/local-command queue. Its pure
  transitions enforce cursor bounds, popup priority, history and snapshot caps,
  completion-anchor closure, steering-before-follow-up promotion, and typed
  queue entries whose content/kind pairing cannot desynchronize, without
  streams, file descriptors, termios, PTYs, or a `ToolLoopTerminalUi`.
  `CompletionItem` moves to this dependency-neutral owner
  and remains explicitly available from `native.editor_completion` as the same
  object.

  `ToolLoopTerminalUi` remains the product façade and the sole adapter for
  decoded terminal bytes, filesystem completion lookup, clipboard/image and
  drag-path I/O, custom extension editor/provider execution, rendering, locks,
  and terminal lifecycle. Narrow properties and methods preserve characterized
  caller/test access while projecting directly to `_editor`; the façade's
  existing slots reject retired names rather than accepting dead writes, and no
  mirrored UI dataclass fields or convention-based synchronization remains.
  `read_line`, the active-turn editor, custom-editor adaptation, completion
  acceptance, prompt history, undo/redo, paste, queue promotion/restoration/
  drain, and local-command hand-off delegate state transitions to that owner.
  No-op Backspace/Ctrl-U/Ctrl-Z/Ctrl-Y transitions now skip effectful completion
  refreshes exactly as before extraction, while empty character insertion keeps
  its historical edit boundary and refresh. Slash/completion navigation paints
  only when selection really moves, and Tab completion refuses an open slash
  menu before provider/filesystem lookup. Completion acceptance captures one
  immutable selection before extension code runs; its only span invariant lives
  on that snapshot, completion modes are the closed `at`/`path` domain, and
  empty provider candidates preserve the pre-existing closed-popup/unbound-
  provider outcome. `EditorState` alone owns queue-restoration draft precedence;
  the façade injects a lazy custom-editor text callback that is skipped for an
  empty queue or staged initial text. The misleading copy-returning
  `_pending_steering` façade projection is retired;
  rendering labels remain solely in the frame adapter. Existing callers in
  `tool_loop_session` require no schema or queue-port adaptation.

  Terminal-independent owner, complete table-driven façade adaptation, and
  regression coverage produce a net increase of exactly **52 collected tests**
  relative to the Slice 10 checkpoint. Focused owner/façade/completion/custom-editor/queue/TUI tests pass
  **293 cases**, and the architecture metric/import gate passes **174**; the
  complete real product TUI PTY module passes **49 tests**, PTY smoke passes **8
  tests**, and all **12** deterministic TUI-workflow conformance checks pass,
  including prompt/image/provider archive non-leakage. Diagnostic strict Mypy is
  clean across **166 source files**, and combined typecheck is clean across
  **429 source/test files**. `just docs-build` is clean. The first `just check`
  run reached one load-sensitive custom-overlay PTY typing/render timeout; that
  exact case then passed five consecutive isolated runs. During the post-review
  fix verification, one unrelated active-turn Escape PTY follow-up readiness
  deadline missed under full-suite load; both parametrizations and the exact
  Escape case passed immediately in isolation, and the full pytest retry is
  clean at **4,731 passed / 2 skipped**.

  Architecture metrics report `ToolLoopTerminalUi` state fields **128 -> 104**,
  exactly the `B - 24` ceiling; `tui.py` physical lines **7,018 -> 6,894**;
  source/test Python lines **79,498 / 116,248 -> 79,846 / 116,948**;
  repository/source C901 remains **38 / 22**; and the source type-ignore count
  remains exactly **1**. No dependency, unchecked `Any`, suppression, Mypy
  exclusion, C901 pin, compatibility shim, schema, command-precedence,
  extension-contract, session-format, privacy, or theme change is introduced.
  This behavior-preserving ownership refactor has no changelog entry. The active
  pointer advances to **Slice 12 — overlay and extension-chrome state
  extraction**.

  Independent review used the exact `claude-opus-5` model at `xhigh` effort in
  a read-only harness. One initial invalid attempt used an absolute-path tool
  target; it was discarded and did not count as review evidence. Four valid
  complete rounds followed, all with zero skipped files, truncations, redactions,
  forbidden tool uses, or scoped omissions. Round 1 reported **4 Warnings + 5
  Suggestions**; all nine were fixed by a fresh Pi
  `openai-codex/gpt-5.6-sol` implementer. Round 2 reported **2 Warnings + 3
  Suggestions**; all five were fixed by a fresh exact-model Pi implementer.
  Round 3 reported **4 Suggestions**; all four were fixed by a fresh exact-model
  Pi implementer. Pi implementer self-verdicts were discarded.

  Round 4 reported **3 Suggestions** and no Critical or Warning findings. They
  were proportionally declined: mixed façade access idioms are characterized
  private/test compatibility, and consolidating them now adds churn without a
  demonstrated defect; source-introspection equality for editor-backed
  properties would be brittle and would continue the consecutive
  self-inflicted projection-test review churn; and accurate micro-behavior prose
  may be revisited during the final Slice 16 documentation disposition but is
  not a correctness defect. The project's “two consecutive self-inflicted
  churn” stop signal applies to the projection-test chain, every remaining
  finding is a non-material Suggestion, and another round would not improve
  confidence. No correctness, privacy, trust, concurrency, data-loss, or
  public-contract finding remained. The contemporaneous “commit pending” state
  is superseded by landed commit `8f61ffe`.

- **Slice 12 — overlay and extension-chrome state extraction: LANDED.** Commit
  `44c0948` (`refactor: extract overlay and chrome state`). New terminal-independent
  `native.overlay_state.OverlayState` is the single owner for model, settings,
  project-trust, tree, scoped-model, session-picker, and custom-extension
  overlays. Its closed `active` discriminator and typed owner frames make the
  renderable stack explicit: a distinct nested overlay restores its outer kind
  on close, same-kind re-entry adds no bogus frame, and direct façade projection
  writes intentionally supersede stale nesting. Settings and project-trust keep
  explicit discriminators but share one payload family; a suspended settings-
  family frame owns and restores the exact rows, title, and selection before the
  outer dialog continues. Pure transitions own
  wrapping, selectable/actionable constraints, scoped membership,
  session-picker query/scope/sort/submodes and rows, and custom completion state
  without streams, termios, PTYs, terminal writes, or extension execution.

  New `native.extension_chrome_state.ExtensionChromeState` owns header/footer,
  insertion-ordered widget placements, title, status, working/indicator state,
  terminal-input listeners, and footer branch-listener rebuild bookkeeping.
  Clear disposes snapshotted regions through the effectful façade before it
  advances the local generation and drops generation-owned regions, footer
  factory/branch/callback/rebuild state, title/indicator state, and terminal-
  input registrations. Chrome and listeners synchronously registered by
  `dispose()` are therefore retired too; retained old-generation listener/footer
  disposers are generation-checked and inert even if a numeric id is reused.
  Product-visible status rows and sticky working message/visibility retain their
  pre-extraction cross-generation behavior and survive clear/reload.
  `ToolLoopTerminalUi` remains the product façade and existing live extension
  UI bridge. It still owns the
  paint lock, raw mode and decoded bytes, extension factory/callback/component
  execution, invalidate/render/dispose calls, git/filesystem inspection,
  sanitization and caps, title push/write/restore, frame calculation, and
  painting. Slotted compatibility projections read/write these owners directly
  and cannot create mirrored stored state.

  `TerminalDriver` now distinguishes three lifecycle operations. Its scoped
  balanced raw ownership acquires before installing a matching release, so a
  suspended nested attempt cannot consume another scope's owner and a failed
  physical entry cannot fabricate one. Inner overlays share the outer
  transition without a second `TCSAFLUSH`, and
  only the outermost release disables bracketed paste and restores the original
  termios. The custom-component lifecycle uses an equivalent successful-entry
  guard so its established disposal/repaint-before-release ordering remains
  exact. Configured external editors and blocking login/OAuth prompts instead
  use one scoped, nest-guarded external-I/O façade: entry immediately disables
  bracketed paste and restores cooked termios even above raw depth one without
  consuming logical owners, while its unavoidable paired exit re-enters
  physical raw mode with the documented `TCSAFLUSH` policy. A scope is also
  published when no raw owner exists, raw acquisition while suspended fails
  loudly, and unmatched resume fails rather than claiming a physically false
  owner. Failed cooked entry launches no foreign consumer; failed raw resumption
  keeps recovery suspended for the forced-close boundary, while a completed
  external-editor file is inspected before exit so the edit is retained.
  Local shell/model-tool subprocesses keep detached stdin and therefore remain
  under the raw active-turn watcher rather than suspending the TTY. The actual
  `ToolLoopTerminalUi.close` recovery boundary remains a separate forced restore
  that zeroes abandoned raw and suspension ownership and safely restores the
  saved terminal whether physically raw or suspended; repeated close is
  idempotent.

  Terminal-independent transition/ownership tests and import gates cover the
  active-overlay invariant and nested restoration, exact settings -> project-
  trust -> settings payload continuation (including an empty nested candidate
  leaving the exact outer payload untouched), navigation and unavailable rows
  (including single-row repaint parity), project-trust identity, a foreign
  editor observing canonical mode at nested raw depth, physical raw resumption
  with all logical owners intact, final balanced release, and forced close
  recovery after either an unmatched acquisition or a suspended handoff,
  transition failures, missing/misordered pairing, scoped nesting, exceptional
  exit, and failed nested raw acquisition without outer-owner loss; real
  `termios.error` editor suspend/resume regressions preserve both no-launch and
  completed-edit behavior. The trust-session terminal double is again a real
  context manager, while existing login/auth tests exercise the live scoped
  path; the
  captured `render_lines()` session-picker exclusion versus live paint,
  session row mutation, sticky versus generation-owned chrome clear semantics,
  stale façade/owner disposer refusal under forced id reuse, footer callback-slot
  rebuilds, direct assignment write-through identity, and absence of the retired
  fields from the façade dataclass. The four characterization-only one-line
  transition delegates were deleted, and tests now name the owner methods
  directly. The earlier broad Pi focused gate passed **633 tests**. The
  coordinator's distinct final changed-boundary focused gate passed **466
  tests**; it was not the same selector as the 633-test gate. The round-7
  terminal-driver/all-raw-user/auth/owner focused gate passed **243 tests**, and
  the architecture gate passed **174**. The complete relevant real-PTY
  selector, session-picker, custom UI/foreign-editor, project-trust, chrome, and
  live reload gate passed **70 tests**, including the nested-depth foreign-
  consumer proof; the round-7 directly affected PTY subset passed **66 tests**,
  and the PTY smoke gate passed **8 tests**. Strict
  `uv run mypy --strict src` is clean across **168 source files**, and the
  combined typecheck is clean across **432 source/test files**.

  Architecture metrics report `ToolLoopTerminalUi` state fields **104 -> 43**
  (well below the cumulative **89-field** ceiling), source/test Python physical
  lines **79,846 / 116,948 -> 80,914 / 118,414**,
  `tool_loop_session.py` **5,433** lines, and `tui.py` **6,894 -> 7,066** lines.
  Repository/source C901 remains **38 / 22**, preserving the Slice 12 starting
  baseline rather than improving it, and the source type-ignore count remains
  exactly **1**.

  One complete `just check` attempt failed
  `test_pty_extension_editor_external_editor_invalid_utf8_keeps_text`: the
  rendered external-editor hint was observed and Ctrl+G was written before the
  existing outer `TCSAFLUSH` raw transition, so the editor never launched. The
  exact case passed once and then reproduced on repetition run 2; its containing
  PTY module immediately passed **8/8**. The relevant **70-case** real-PTY group
  and **8-case** smoke gate passed, and the complete repository retry passed
  **4,766 tests / 2 skipped**. This is explicit queued **Slice 14 — deterministic
  PTY synchronization** observable-readiness evidence. The round-7 complete
  full final repository gate passed **4,772 tests / 2 skipped**. No sleep or timeout
  increase, timing workaround, or coverage weakening entered Slice 12.
  Ruff, both typechecks, all **12** deterministic TUI workflow conformance
  checks, `git diff --check`, and the docs build are clean. Both theme stores are
  `pi`.

  Independent review used the exact `claude-opus-5` model at `xhigh` effort in
  a read-only harness. Eight valid complete rounds covered Slice 12; every round
  had zero skipped files, truncations, redactions, forbidden tool uses, scoped
  omissions, or provider errors. Rounds 1–7 reported the findings and received
  the fixes recorded in the implementation and focused-test account above.
  Round 7 specifically reported **1 Warning + 2 Suggestions**; all three fixes
  are represented in the final code and gates. Round 8 reported **4
  Suggestions** and no Critical or Warning findings. No self-produced review
  verdict is used as review evidence.

  The four Round 8 Suggestions received these individual proportionality
  dispositions:

  1. `/login` scope-exit outcome asymmetry is currently unreachable because
     login runs only after `read_line` releases raw ownership. Existing
     exception handling preserves the current contract. A hypothetical future
     authentication flow introduced inside raw ownership must define that
     outcome then; this is not a current defect.
  2. Balanced or forced restoration while an external-I/O scope remains open is
     not a reachable synchronous product ordering. Forced close remains the
     authoritative shutdown boundary; this slice does not add speculative
     lifecycle semantics.
  3. Mixed owner/projection reads are private, characterized compatibility
     access—not parallel stored state or a behavior defect. Changing them would
     continue projection churn without architectural value.
  4. Successful configured-editor entry intentionally clears/resets the live
     region before the launch notice through the documented scoped external-I/O
     contract; failed entry preserves the frame. This corrects stale tracking
     and is already documented, while another characterization-only test would
     not improve confidence.

  Rounds 6 and 7 were consecutive findings caused by the preceding review
  fixes. Round 8 contains only the non-material Suggestions dispositioned above,
  so the project's two-consecutive self-inflicted-churn stop signal applies and
  another round would not add material value. No substantive finding remains;
  this is a value-based stopping disposition, not a claim of Claude CLEAN.

  No dependency, unchecked `Any`, suppression, Mypy exclusion, C901 pin,
  compatibility shim, alternate-screen behavior, schema, trust/session/privacy
  contract, or changelog entry is introduced. The ownership refactor is
  behavior-preserving, so the no-changelog disposition remains. Slice 12
  implementation, verification, independent review, and documentation were
  complete before landed commit `44c0948` advanced the historical pointer.

- **Slice 13 — pure frame composition: LANDED.** Commit `eacb742`
  (`refactor: extract pure terminal frame composition`). New
  `native.frame_renderer` is the cohesive terminal-independent owner for full
  and live frame composition. Frozen `FrameSnapshot` values contain copied raw
  history blocks, transient assistant/reasoning/tool/working strings, editor
  text/cursor or already-resolved custom-editor `FrameLine` rows (including
  their kind and cursor metadata), resolved overlay and extension-chrome rows,
  geometry, and cursor-visibility intent. Callback-bearing live custom-history
  rerender state is deliberately excluded: only its current `(kind, lines)`
  projection crosses the boundary. The renderer
  owns block wrapping, safe clipping, row selection and budgeting, dynamic input
  wrapping/windowing, extension-chrome clamping, footer/input pinning, style
  mapping, cursor metadata/relative placement, and deterministic logical paint
  plans. Frozen frame-line metadata is exposed through read-only mappings;
  rendering a snapshot repeatedly is deterministic and does not mutate it.
  Resolved custom-editor rows retain the pre-extraction tail-window policy and
  HEAD's plain control/SGR treatment: the façade sanitizes and clips them once
  into immutable `ResolvedCustomEditorLine` values. That explicit `FrameLine`
  subtype survives full-frame assembly, so finishing preserves the exact bytes
  and only adds the requested pre-extraction right padding; input layout windows
  the rows without re-sanitizing, re-clipping, or relocating cursor metadata.

  `ToolLoopTerminalUi` remains the product façade and effect adapter. Live paint
  holds its existing paint lock while resolving trusted extension factories/
  components, invalidation/disposal, custom editor and overlay rows, git/filesystem-backed
  chrome, and live geometry before publishing copied immutable values. It
  retains re-entry/coalescing flags, error handling, native-scrollback
  publication, resize clear/home, forced redraw, and restoration.
  `TerminalDriver` remains the sole write/flush owner and serializes logical
  plans into ANSI erase/newline/relative-cursor sequences. Its documented
  `write_frame(...)` contract accepts logical rows, performs one flushing frame
  write, owns final cursor visibility, and reports write/flush failure as
  `False`. The pure paint plan is published to `_painted_block_count`, `_live_height`,
  `_live_input_row`, and `_last_painted_size` before the write attempt, exactly
  preserving failed-write bookkeeping; a failed deferred clear still returns
  before resetting those fields. Deferred clears remain unflushed and coalesce
  with the immediately following paint flush. Ordinary paints commit each new
  history block once and never redraw its rows, while resize/full redraw resets
  the committed count only after a successful deferred clear. Captured
  `render_lines()` still excludes the session picker and live paint still
  includes it. Empty overlays are safe with or without newly committed history:
  they preserve the old live-row bookkeeping shape, keep the hardware cursor
  hidden, and never index an absent row. Non-positive editor row budgets now
  explicitly restore one input row and cursor. The façade's plain clip/pad
  wrappers delegate to the renderer, and the renderer reuses the existing
  shared label sanitizer rather than maintaining a second implementation.

  Direct immutable-renderer tests construct snapshots without a terminal and
  cover deterministic/no-mutation behavior, wrapping/clipping, footer pinning,
  styles, empty and populated overlays, cursor metadata/placement, commit-once
  paint plans, custom-editor multi-row/window/control/SGR and no-double-finish
  parity through both direct snapshots and full-frame façade rendering, state-bearing
  custom-history detachment, last-user overflow retention, history-tail
  compaction, footer truncation, clipped-marker priority, widths 0/1, and tiny
  heights. Façade characterizations cover the same custom-editor window and
  byte policy, zero-row-budget restoration, detached snapshot publication,
  failed paint publication, failed deferred-clear preservation, and the rule
  that an active overlay does not execute hidden extension chrome; existing
  chrome, terminal-driver, terminal-screen, captured, TUI, custom editor,
  re-entry/coalescing, resize, and native-scrollback gates remain in place. The
  focused renderer/custom-editor/TUI/chrome/history/driver/screen group passes
  **652 tests**, and the complete `_pty.py` real-PTY group passes **74**. The
  PTY smoke gate passes **8 tests**
  and the complete repository gate passes **4,808 tests / 2 skipped**. Strict
  source Mypy is clean across **169 source files** and combined typecheck is
  clean across **434 source/test files**. No transient PTY readiness race
  reproduced during Slice 13 verification or review-round 3 fixes.

  Architecture metrics move repository/source C901 **38 / 22 -> 34 / 18**.
  All four removed findings are real ownership moves from TUI
  (`_frame_lines`, `_paint_locked`, `_styled_line`, `_block_frame_lines`); the
  new renderer has no finding, and TUI findings fall **13 -> 9**. State fields
  remain **43**. Source/test Python physical lines move **80,914 / 118,414 ->
  81,112 / 119,046**; `tool_loop_session.py` remains **5,433** lines and
  `tui.py` falls **7,066 -> 6,329** while the new renderer is **890** lines.
  The source type-ignore count remains exactly **1**. No dependency,
  suppression, Mypy exclusion, C901 pin, compatibility shim, alternate-screen
  behavior, schema, event-order, trust/path, session/archive privacy, or
  changelog change is introduced. Both themes remain `pi`.

  Independent review used Claude Opus 5 for the first round before the operator
  explicitly redirected remaining reviews to fresh read-only Pi
  `openai-codex/gpt-5.6-sol` processes because of the weekly Opus limit. The
  first Pi CLEAN was discarded because its own scope report could not establish
  baseline-diff coverage. An exhaustive Pi comparison against the detached
  Slice 12 checkpoint then found one Warning (full-frame finishing
  reprocessed resolved custom-editor rows) and one Suggestion (document
  `TerminalDriver.write_frame`); a fresh Pi implementer fixed both. The final
  exhaustive Pi re-review inspected every changed ownership region and both new
  files against that checkpoint and returned valid, unscoped **CLEAN** with
  zero Critical, Warning, or Suggestion findings, no skips/redactions/forbidden
  tools, and no worktree mutation. Slice 13 implementation, verification,
  documentation, and review are complete. **Slice 14 — deterministic PTY
  synchronization** is now active; the Slice 13 commit integrates this ledger
  and advances the pointer atomically.

- **Slice 14 — deterministic PTY synchronization: LANDED.** Commit `03753be`
  (`test: make PTY synchronization deterministic`). One typed
  test-only owner, `tests/pty_sync.py`, waits on bytes already emitted by the
  product. A rendered title, notice, answer, input frame, or external-editor
  hint is presentation only. Input is writable only after the later
  `ESC[?2004h` acknowledgement emitted by `TerminalDriver` after a successful
  outer `TCSAFLUSH` raw transition. Helpers chain from the precise preceding
  output offset, or count fresh acknowledgements where an active-work raw owner
  is followed by a prompt raw owner. The two output/readiness phases consume
  one monotonic timeout budget. The shared typed fd aggregate immediately
  searches already captured bytes from a supplied offset before reading more,
  preserves exact output/readiness match ends, and uses one absolute deadline
  across both ordered phases, so coalesced and split title/readiness output are
  accepted while stale acknowledgements are rejected. Resize tests keep their
  existing owner. Snapshot paths wait for the fresh clear and then a unique
  visible final-footer sentinel under one deadline; a clear prefix alone does
  not prove a split PTY read captured the complete redraw. No product API, byte,
  timeout, or terminal policy changed.

  Recorded race inventory:

  - the shipped multi-tool/local-command prerequisite remains intact, now using
    the shared post-`Keyboard Shortcuts` raw acknowledgement rather than a
    one-off partition scan;
  - startup chrome, the project-trust selector, session picker, extension
    chrome/message commands, resources, and resume/compact painted before their
    first or next outer raw owner; each write now follows that owner's byte;
  - the extension editor's Ctrl+G hint painted before the outer raw transition,
    and the launch notice preceded the post-editor `TCSAFLUSH` resume; both
    invalid-UTF8 and ordinary failure paths now handshake on each transition;
  - custom-overlay typing/cancel/editor/shortcut tests could write after paint
    but before raw entry; a delayed-transition real-PTY characterization proves
    paint alone cannot acknowledge readiness;
  - active-turn Escape/Ctrl-C painted `Operation aborted` before the follow-up
    prompt acquired raw mode; follow-up input now waits for the later byte;
  - thinking/model/folding hotkey notices painted before the next input owner;
    every hotkey now starts at a fresh capture offset and waits through its
    notice to the following readiness acknowledgement;
  - queued steering reached the third provider call before its result and next
    prompt were ready; final exit now waits for `DRAINED_TURN_3` and the later
    prompt acknowledgement while preserving provider-call and drain-order
    assertions;
  - a mid-turn `/hotkeys` driver returned after presentation only, leaving its
    helper's final Ctrl-D exposed; it now starts at the command invocation
    offset and waits through `Keyboard Shortcuts` to ready input;
  - settings close/reopen, persistent-history sessions, tree turns/selectors,
    scoped-model save, and local-command cancellation used fixed sleeps or
    output-only waits across ownership handoffs; they now use byte offsets,
    acknowledgement counts, or ordered bytes under one already-ready owner;
  - final exits after `/copy`, the balanced multi-tool follow-up, and scoped-
    model save now record fresh invocation/output offsets and wait for the
    later prompt acknowledgement before writing Ctrl-D;
  - project-trust/session-picker resize tests now observe the clear/redraw byte
    instead of sleeping for the resize poll; and
  - the former bracketed-paste exit race is covered by the shared startup and
    post-turn prompt handshake, so an exit byte cannot be discarded by the
    next outer raw transition.

  The remaining sleeps in `tests/*_pty.py` are only output-poll cadence and
  macOS PTY EIO/empty-read backoff. Join/provider/process timeouts remain
  bounded failure deadlines.

  Verification on macOS/Python 3.14.5: before review, eight formerly flaky
  focused nodes (14 parameter cases per batch) passed 20 consecutive batches;
  after the round-1 fixes, the helper, complete project-trust module, and three
  final-exit nodes (18 parameter cases per batch) passed
  **20 consecutive batches**, 360 checks total. The exhaustive nine changed
  test modules (81 cases including the helper tests) passed **10 consecutive
  batches**, 810 checks total. `just test-pty-smoke` passed **5/5** runs at 8
  tests each, and the complete real-PTY inventory passed at **75/75**.
  Diagnostic `mypy --strict src` passed **169** files, followed serially by
  `just typecheck` across **436** source/test files. `just docs-build` completed
  with no issues, and final `just check` passed Ruff, Mypy, and Pytest (**4,816
  passed, 2 skipped**). Final architecture metrics remain **34** repository /
  **18** source C901 findings, **43** `ToolLoopTerminalUi` fields, and **1**
  source `type: ignore`. CI still defines both Linux and macOS real-PTY jobs;
  settings and native theme stores both remain `pi`; no pre-commit configuration
  is present. Independent Pi review round 1 reported three Warnings: one
  duplicated two-phase timeout budget, one non-offset-aware project-trust fd
  aggregate, and three final-exit handoff omissions. All three were accepted and
  fixed in the active worktree.

  Independent review round 2 found four accepted gaps: remaining manual
  output-then-readiness chains could still spend two nominal timeout budgets;
  direct/nested model, settings, auth, and scoped-model paths had paint-before-
  raw or notice-before-prompt handoffs without fresh invocation offsets; count
  waits skipped an already captured acknowledgement at zero/negative timeout
  and could oversleep their budget; and the documentation overstated the audit
  before those paths were exhaustive. The fixes route every two-phase chain
  through the shared absolute deadline, make count waits inspect first and sleep
  only for their clamped remaining budget, and handshake direct and settings-
  nested model selectors, settings open/reopen/close, login/logout
  continuations, scoped-model open/toggle-repaint/save, and all three changed
  final exits. Intermediate availability, selector repaint, settings
  navigation/resize, scoped checked-state, auth availability, provider-turn,
  and persistence assertions remain intact.

  Round-2 verification on macOS/Python 3.14.5: the helper and every newly
  synchronized overlay/auth/final-exit node passed **20 consecutive batches**
  at **23 cases** each (**460 checks**). An implementation-agent run initially
  failed the long-input resize node once for 80x24→100x40, after which that node
  passed 20/20 alone and a restarted ten-batch changed-module group passed. A
  coordinator rerun then passed batch 1 at 83/83 but failed batch 2 for the
  opposite 100x40→80x24 direction. This was not an unrelated existing timeout.
  The test waited for the final 24 bytes of a prompt made from one repeated
  12-byte token; that same suffix appeared after only the first 24 input bytes,
  so the test could resize while most of the burst remained unread. In the
  split-input/output PTY harness there is no foreground-terminal SIGWINCH owner.
  A geometry change between `_read_key_polling_resize()` and the ensuing
  ordinary key paint therefore let that paint adopt the new size and update
  `_last_painted_size`; the next polling boundary saw no size delta and emitted
  no full-clear byte. Assertion cleanup then detached the output wrapper while
  the worker was still polling, producing the secondary `ValueError` recorded
  in `/tmp/pipy-s14-coordinator-modules-2.log`.

  The test-only fix uses a unique final rendered glyph as the acknowledgement
  that the complete repetitive input burst has been consumed, records the
  output length immediately before `TIOCSWINSZ`, and accepts only `ESC[2J`
  after that fresh offset. Every tool-loop resize path now uses the same fresh-
  offset clear contract. Failure cleanup clears the editor, requests exit, and
  joins the worker before teardown; if a bounded join still leaves it active,
  teardown retains rather than detaches its stream wrappers, preserving the
  primary failure. The ioctl continues to target the output slave read by
  `TerminalDriver.size()`; executable evidence did not justify a product
  change. Foreground-terminal SIGWINCH remains the product's parallel resize
  trigger.

  Final resize-fix verification on macOS/Python 3.14.5: both resize directions
  passed **20 consecutive focused batches** at 2 cases each (**40 checks**),
  followed from batch zero by all nine changed test modules passing **10
  consecutive batches** at 83 cases each (**830 checks**). The PTY smoke gate
  passed **5/5** at 8 tests each, and the complete real-PTY inventory passed
  **75/75**. Strict source Mypy passed **169** files, followed serially by the
  combined typecheck across **436** source/test files. `just docs-build`,
  `git diff --check`, and final `just check` pass; the latter reports **4,818
  passed / 2 skipped**. Architecture metrics remain **34 / 18**
  repository/source C901 findings, **43** `ToolLoopTerminalUi` fields, and **1**
  source `type: ignore`. CI retains Linux and macOS PTY jobs, both theme stores
  remain `pi`, and no `.pre-commit-config.yaml` exists.

  Independent review round 3 found one Warning and one Suggestion, both
  accepted. The remaining thinking/model/folding hotkey waits and mid-turn
  `/hotkeys` driver treated their notices as completion, while the steering
  path treated the third provider call as completion before
  `DRAINED_TURN_3` and the next prompt were observable. Each affected action
  now records a fresh invocation offset and waits through its exact notice or
  result marker to the following raw-input acknowledgement; intermediate UI
  state, provider-call, and steering-order assertions remain. Architecture now
  records the verified strict-Mypy count of **169** source files.

  Round-3 verification on macOS/Python 3.14.5: the four affected nodes passed
  **20 consecutive batches** at 7 parameter cases each (**140 checks**), and
  all nine changed test modules passed **10 consecutive batches** at 83 cases
  each (**830 checks**). PTY smoke passed **5/5** at 8 tests each, and all
  `tests/*_pty.py` passed **75/75**. Strict source Mypy passed **169** files,
  followed serially by combined typecheck across **436** source/test files.
  `just docs-build`, architecture metrics JSON, `git diff --check`, and final
  `just check` pass; the latter reports **4,818 passed / 2 skipped**. Metrics
  remain **34 / 18** repository/source C901 findings, **43**
  `ToolLoopTerminalUi` fields, and **1** source `type: ignore`. CI retains Linux
  and macOS PTY jobs, both theme stores remain `pi`, and no
  `.pre-commit-config.yaml` exists.

  Two subsequent attempted round-4 outputs were formally invalid because they
  did not satisfy the review gate's required output schema. They do **not**
  count as review round 4 and are not independent review verdicts. Their logs
  are `/tmp/pipy-slice14-pi-review-r4.CSmg5f/review.log` and
  `/tmp/pipy-slice14-pi-review-r4-retry.uwYfrI/review.log`. The repeated
  diagnostics were nevertheless accepted as actionable implementation
  evidence: project-trust's fd title/warning and readiness calls each started a
  fresh timeout, and four immediate resize snapshots parsed after only the
  clear prefix even though the PTY master may split the coalesced flush.

  The accepted fd fix adds one typed output-then-readiness observation carrying
  the preserved aggregate, first match end, and readiness match end. One
  absolute monotonic deadline covers searches and fd reads in both phases; the
  readiness search begins at the exact first match end. Deterministic tests pin
  total-budget and nonpositive-budget behavior, already-coalesced bytes, split
  reads, aggregate preservation, and stale-marker rejection. Both startup trust
  and untrusted-warning paths use this helper.

  The accepted resize fix uses the shared ordered-byte helper to observe a fresh
  `ESC[2J` and then a unique visible footer sentinel. The product serializer
  emits that footer after the input, menu/settings rows, and separators needed
  by each parsed snapshot, so this establishes post-resize capture completion
  even when the PTY read splits clear from paint. The unique long-input glyph
  remains a separate pre-resize consumption acknowledgement. Both geometry
  directions, real PTYs, overlay navigation/selection, multiline paste, long
  input rewrap, pinned footers, settings state, and no-alternate-screen checks
  remain. Failure cleanup now asks live workers to exit and joins before stream
  teardown, retaining wrappers if a worker survives the bounded join.

  Post-diagnostic verification on macOS/Python 3.14.5: all **14** deterministic
  helper cases pass. The six project-trust cases and eight resize parameter
  cases passed together for **20 consecutive batches** at 14 cases each (**280
  checks**) in both the implementation-agent run and an independent coordinator
  run. All nine changed test modules passed **10 consecutive batches** at 88
  cases each (**880 checks**) in both runs. `just test-pty-smoke` passed
  **5/5** at eight cases per run, and all `tests/*_pty.py` passed **75/75**.
  Strict source Mypy passed **169** files, followed serially by combined
  typecheck across **436** source/test files. `just docs-build`, architecture
  metrics JSON, and `git diff --check` pass; final `just check` reports **4,823
  passed / 2 skipped**. Metrics remain **34 / 18** repository/source C901
  findings, **43** `ToolLoopTerminalUi` fields, and **1** source
  `type: ignore`. CI retains Linux and macOS real-PTY jobs, settings and native
  theme stores remain `pi`, and no `.pre-commit-config.yaml` exists.

  A first post-fix structured round-4 attempt ended in a provider error and is
  also invalid; it supplied no review verdict or additional evidence. A fresh
  exact-model retry then completed with exit 0. The configured reviewer was Pi
  `openai-codex/gpt-5.6-sol`, restricted to read/grep/find/ls. Its structured
  result was **CLEAN** with complete, unscoped coverage of all fourteen Slice
  14 files and supporting ownership code, zero forbidden tool uses, no skipped
  files, truncations, or redactions, and zero Critical, Warning, or Suggestion
  findings. Repository status was unchanged by the review. Review stopped at
  CLEAN because another round would add no material evidence. Slice 14 is
  complete; this commit advances the active pointer to **Slice 15 — repository
  formatting baseline and gate**.

- **Slice 15 — repository formatting baseline and gate: LANDED.** Slice 15a is commit
  `f02255a82a2eeed10185fdb7977ec440ba1eb6d1` (`style: format examples scripts
  and source`): Ruff formatter output only across **103** Python files. Slice
  15b is commit `d1c8cbccfe1992dc080bc79e7ba7eaba149dddcb` (`style: format
  tests`): Ruff formatter output only across **150** Python test files. Neither
  mechanical batch contains a semantic, gate, test-behavior, or documentation
  prose edit.

  Slice 15c is commit `e35a0d54898c160ac37acbdbdd35fff727569508`
  (`chore: enforce Ruff formatting`) and gives the repository-wide check one
  local owner: `just format-check` runs `uv run ruff format --check .`, `just check` depends on
  that recipe, and the CI quality job invokes the same recipe rather than
  duplicating its command. `just format` applies `uv run ruff format .`.
  Contributor setup documentation names both direct commands. Three focused
  architecture-quality tests pin recipe ownership, aggregate-local and CI
  enforcement, contributor-command documentation, and the absence of custom
  Ruff exclusions.

  The formatter-only baseline was clean across **478** Ruff-discovered files;
  the final gate is clean across **479** after adding the focused Slice 15c test.
  No generated or fixture file needed exclusion, and there is no configured
  formatter exclusion, dependency, suppression, Mypy exclusion, C901 pin,
  `.pre-commit-config.yaml`, compatibility shim, changelog entry, or release
  note. The formatting series and gate do not change CLI, JSON/RPC, provider,
  session, extension, trust/path, privacy, event-ordering, or TUI behavior.

  Focused architecture-quality and metrics verification passes **7 tests**.
  Strict source Mypy is clean across **169 source files** and combined
  typecheck is clean across **437 source/test files**. `just docs-build` reports
  no issues, and the final `just check` runs lint, the repository formatter
  check, Mypy, and Pytest successfully at **4,826 passed / 2 skipped**.
  Architecture metrics remain **34 / 18** repository/source C901 findings,
  **43** measured `ToolLoopTerminalUi` fields, and **1** source `type: ignore`;
  source Python remains **81,738** physical lines, while tests are **121,025**
  after the 50-line focused gate test.

  Fresh independent read-only Pi review used exactly
  `openai-codex/gpt-5.6-sol` at high thinking. Two valid rounds exited 0 and
  returned structured **CLEAN** with zero Critical, Warning, or Suggestion
  findings: round 1 read the complete **9,762-byte / 227-line** six-file patch,
  and—after a fresh Pi implementation agent inserted this review record—round 2
  read the complete final **10,242-byte / 236-line** six-file patch. Both read
  through EOF plus relevant supporting files with complete, unscoped coverage
  (`SCOPED_OMISSIONS: none`), zero forbidden tool uses, and no skipped files,
  truncations, or redactions. Review stopped at the final complete CLEAN because
  there was no actionable feedback and another round would add no material
  evidence. Slice 15c and Slice 15 closed in landed commit `e35a0d5`; the
  historical pointer then advanced to Slice 16.

- **Slice 16 — final documentation, disposition, and fresh comparison:
  LANDED.** Commit `7deb8d8807f4e7eb52f7c9c8bd9e0ad30cb60727`
  (`docs: close architecture quality program`). The
  [2026-07-29 assessment](2026-07-29-architecture-quality-assessment.md)
  consolidates three exact-model read-only audits, records the pipy/Tau/Pi
  revisions and versions, before/after evidence, every residual and C901 pin,
  and package/publication disposition. The existing native-agent package
  description is accurate and unchanged; version/publication details remain
  provisional while private. Living architecture, the transactional reload
  contract, session/harness migration prose, Pi comparison documents, provider
  dependency wording, and documentation navigation now agree. Slice
  16 changes no product behavior or package metadata and adds no changelog
  entry. Focused architecture quality/metrics pass **8 tests**; strict source
  Mypy is clean across **169** files, combined Mypy across **437** files, PTY
  smoke passes **8/8**, docs and diff hygiene are clean, Ruff formatting covers
  **479** files, and `just check` reports **4,827 passed / 2 skipped**. Final
  worktree metrics are **34 / 18** repository/source C901, **43** TUI fields,
  **1** source suppression, **81,738 / 121,052** source/test physical lines,
  and **5,433 / 6,329** lines in `tool_loop_session.py` / `tui.py`. Both theme
  sources remain `pi`.

  Fresh independent read-only Pi review used exact
  `openai-codex/gpt-5.6-sol` at high thinking. Two valid rounds exited 0: round
  1 covered the complete **114,716-byte / 1,407-line**, 14-file patch through
  EOF with no Critical or Warning and one Suggestion to spell Tau's tag exactly
  `v0.3.1`; a fresh exact-model Pi implementation agent accepted it and changed
  only that assessment text. One intervening invocation is **INVALID** and
  discarded because, despite reading all 14 files and reporting zero findings,
  it treated the intentionally unavailable shell/git/hash tools as a scoped
  omission. The fresh valid retry covered the complete final **114,722-byte /
  1,407-line** patch through EOF with all 14 files visible,
  `SCOPED_OMISSIONS: none`, zero forbidden tool uses, skipped files,
  truncations, redactions, or findings, and structured **CLEAN**. Review stopped
  because the accepted exactness finding was fixed and the final complete patch
  was valid CLEAN; another per-slice round would add no material value. The
  subsequent integration evidence, sole ledger Warning and fixes, and final
  complete cross-cutting CLEAN are recorded in the opening integration ledger.
  The architecture-quality program and final integration review are
  closed/reconciled. R7 now represents the bounded transactional-reload
  correctness boundary as complete subject to its review/full-gate commit; D1's
  reader-facing documentation entry point is the next queue item.

## Current State

Pipy is a native coding-agent runtime with a Pi-shape REPL, twelve real
providers plus the deterministic fake provider, standard-library-first
transports with no third-party provider SDKs (`websockets` supplies the Codex
WebSocket transport), a bounded model-driven
tool loop, and a full-transcript native session tree as the product session
store. The older metadata-first `pipy-session` archive is now an optional
summary-safe catalog/learning utility, not the product parity surface. The first
local model path is `ds4` (`antirez/ds4` DeepSeek V4 Flash) through the
OpenAI-compatible Chat Completions machinery with tool-loop support. Specific
feature coverage and parity status live in [pi-parity.md](pi-parity.md). Code
shape lives in [architecture.md](architecture.md). The Phase 0–7 internal migration in
[architecture-migration.md](architecture-migration.md) is completed historical
evidence. The reviewed architecture-quality program is completed/reconciled
through landed Slice 16 commit
`7deb8d8807f4e7eb52f7c9c8bd9e0ad30cb60727`
(`docs: close architecture quality program`); its assessment and the bounded
reload-contract follow-up supersede the old migration ordering—but not its
recorded findings or ledger.

This page is the forward-planning index:

- `Completed Reviewed Program` names the exact landed A–G disposition, closed
  integration status, and reload-contract follow-up that precedes product
  parity. The prior incomplete-ledger Warning was fixed and the fresh A–G
  cross-cutting re-review was CLEAN; the later H disposition focused review was
  also CLEAN. The complete A–H synthesis found only the stale-paragraph Warning,
  fixed by the final stale-pending correction plus optional A–G regex fix. Its
  fresh exact-schema focused re-review by Pi `openai-codex/gpt-5.6-sol` covered
  the complete patch (11,186 bytes / 185 lines, all 8 files) and returned
  `STATE: CLEAN`, `COVERAGE_COMPLETE: yes`, and `VERDICT: CLEAN`, with zero
  Critical, Warning, or Suggestion findings; `SCOPED_OMISSIONS: none`,
  `FORBIDDEN_TOOL_USES: 0`, `SKIPPED_FILES: none`, `TRUNCATIONS: none`, and
  `REDACTIONS: none`. This later docs-only correction does not invalidate the
  already-reviewed A–G cross-contract CLEAN because its cross-contract evidence
  is unchanged, or reopen the closed/reconciled program and final integration
  disposition.
- `Pi Parity Roadmap` and the refreshed Pi audit rank product gaps without
  silently adding them to architecture work.
- The named parity and quality tracks below preserve shipped detail and
  historical evidence.
- `Near Term`, `Deferred`, and `Explicitly Not Now` retain relevant boundaries;
  they do not override the current disposition above.

## Pi Parity Roadmap

Pipy is a Python slopfork of Pi, so the long-term product target is Pi-class
native coding-agent capability — including the terminal UI — through
pipy-owned Python boundaries. Parity selection is capability-first and does not
require a literal TypeScript or `pi-tui` port, while Pi command names and flags
remain the reference wherever user-visible behavior specifies them.

The broad parity ladder, applied with small-slice discipline:

- Shell chrome and orientation: startup header, safe loaded-resource labels,
  compact command affordances, and status/footer-style state presentation.
- Interactive input ergonomics: input-adapter boundary, slash-menu raw-mode
  line editor, optional prompt-toolkit line-editor adapter, stdlib readline
  fall-through, and plain captured-stream fallback. The product TUI now ships
  the core daily-driver editor ergonomics: in-memory Up/Down prompt history,
  ANSI bracketed paste (literal multi-line insert, no accidental submission),
  Ctrl-Z/Ctrl-Y undo/redo, and poll-based terminal resize handling that keeps
  the inline frame coherent at 80x24 and 100x40. Optional persistent
  cross-session prompt history now ships too (behind the `/settings` toggle,
  off by default, local-only state file). A fuller TUI is still on the ladder.
  The input-adapter boundary preserves plain captured-stream fallback.
  The stdlib-only `slash-menu` raw-mode line editor, stdlib `readline`
  adapter, and Workspace-relative path completion remain part of the
  input-parity ladder.
- Context/resource loading: safe pipy-owned instruction discovery
  with metadata-only archive behavior. Runtime resource loading for skills,
  prompt templates, and custom slash commands has now shipped (see the
  Runtime Resource Loading Track below): `/skill`, prompt templates as their
  own `/<template-name>` commands, and workspace custom `/<name>` commands run
  through `pipy_harness.native.resources` in the product tool-loop session, and
  the `[Skills]` chrome section lists the loadable skill names. Chrome color
  themes now ship through `pipy_harness.native.themes` and are selected in
  `/settings`. Pi-shaped per-run source-loading flags now
  load explicit extension/skill/prompt-template/theme files or directories
  while the matching `--no-*` flags disable default discovery. Managed git
  package install/update now ships; a mature PyPI/npm package ecosystem remains
  later parity work. The `/settings` (interactive control
  dialog) and `/model` (interactive selector) surfaces are exposed inside the
  product TUI, and `/login`/`/logout` are executable inside the TUI too
  (through the same auth boundary, with no provider turn).
- Tool parity: the bounded multi-step model/tool loop has landed; user-directed
  `@file` content injection has shipped (a submitted prompt's `@path`
  references load bounded excerpts through the shared bounded reader in both
  REPL modes); the model-visible `bash` tool has shipped as a real shell
  matching Pi (arbitrary commands in the workspace, combined bounded output,
  optional timeout, streamed live); the remaining gaps are
  tool breadth inside that loop, broader verification, and follow-up tool
  observations behind pipy-owned boundaries, explicit scopes, and privacy
  invariants.
- Session workflow parity: durable sessions, resume/search/inspect surfaces,
  compaction/summarization, branch/fork-style exploration, and review-cycle
  learning.
- Extension/RPC parity: the headless automation protocol has **shipped** —
  `--mode json` (full Pi-shaped event stream), `--print`/`-p` (one-shot text),
  and `--mode rpc` (long-lived stdin/stdout JSONL with the 31 Pi command names,
  including RPC thinking-level changes threaded into catalog-backed provider
  request construction), gated by
  `scripts/parity_checks/automation_rpc_conformance.py --json`
  ([automation-rpc.md](automation-rpc.md)). Command-name coverage does not imply
  semantic equivalence: direct RPC bash still lacks correlated updates and
  actual running-command cancellation. Extension UI and broader live switching
  also remain.

### Prioritized Pi Gap Queue (2026-05-28)

This queue reflects the 2026-05-28 multi-agent comparison against the local Pi
reference plus the existing summary-safe parity history. It is a planning order,
not a promise to skip review when a smaller, safer slice appears.

1. Product-TUI reasoning italics. Implemented. `ChromeStyle.dim_italic`
   composes the italic SGR (`3`) with the existing secondary-dim color while
   respecting TTY/`NO_COLOR`/truecolor behavior, and `ToolLoopTerminalUi`
   renders `reasoning` rows with it so the product TUI matches Pi and pipy's
   captured-stream fallback renderer.
2. Product-TUI settings/model/provider controls. Interactive provider/model
   selection has landed in the product TUI alongside the existing read-only
   `/settings` overlay. `/model` opens a keyboard-navigable selector
   (`ToolLoopTerminalUi.run_model_selector`) built from
   `NativeReplProviderState.model_options()`: rows show the `provider/model`
   reference plus availability state and reasons, the active selection is marked
   `(current)`, Up/Down move the highlight (wrapping), Enter chooses an
   available row, and Esc cancels. Unavailable providers — and providers that do
   not advertise tool-call support, which tool-loop mode requires — stay visible
   with a reason but cannot be chosen. A successful choice switches through
   `NativeReplProviderState.select_model`, rebinds the live provider, clears the
   in-memory conversation context, refreshes the footer/status model label,
   persists the non-secret default, and constructs the next provider turn with
   the new provider/model; selection runs no provider turn. A direct
   `/model <provider>/<model>` form also switches (and works in the
   captured-stream fallback). The slash menu now lists `model`, `login`, and
   `logout` alongside `help`, `settings`, `copy`, `exit`, and `quit`. `/login
   [openai-codex]` and `/logout [openai-codex]` are now executable in the
   product TUI through the same `NativeReplProviderState` auth boundary the
   no-tool REPL uses: they run no provider turn and no tool call, clear the
   in-memory conversation, refresh `model_options()` availability, and rebind
   the live provider/footer (logout resets the selection to the local default).
   Interactive login output (the OAuth URL/prompt) renders only on the live
   terminal — the inline frame is suspended around it and repainted afterward —
   and never reaches the session archive. 2026-05-29 parity gap (now closed):
   `/settings` is an interactive in-frame control dialog
   (`ToolLoopTerminalUi.run_settings_dialog`) drawn in the live region, matching
   Pi's shape — highlighted actionable rows, scroll/windowing when the list
   overflows, bottom-key affordances (`↑/↓ move · enter/space act · esc close`),
   and Esc/Ctrl-C/Ctrl-D cancel. It exposes read-only status rows (active
   selection and per-provider availability) plus actionable rows: change
   provider/model (reuses the `/model` selector), openai-codex auth (reuses the
   `/login`/`/logout` boundary), toggle persistent prompt history, and clear
   persisted history. All actions run no provider turn and no tool call; the
   provider/model and auth actions reuse the existing `NativeReplProviderState`
   boundaries. Verified by real-PTY product-path tests at 80x24 and 100x40 that
   open the dialog, inspect the live overlay before any action, navigate/toggle/
   clear, resize while the dialog is open, and cancel back to the input.
3. Full interactive TUI ergonomics. The product TUI now renders inline (no
   alternate screen): finalized blocks commit once into the terminal's normal
   buffer so the host terminal/multiplexer keeps them in native scrollback, and
   only a small live region (bounded stream tail + separator/input/separator
   frame + slash menu + two footer rows) is redrawn in place, pinned at the
   bottom. This landed the three previously observed gaps: `/copy` is now an
   executable local command (safe OS clipboard / OSC 52, no provider turn);
   scrolling to review prior output works in both a native Ghostty terminal and
   a zellij pane because native scrollback is no longer suppressed; and a
   full-size window uses its full height (content fills down to the bottom-
   pinned input/footer) instead of only the upper half. Verified by real-PTY
   product-path tests at 100x40 and 80x24 (`tests/test_native_tool_loop_tui_pty.py`)
   plus tmux captures. In-app `/model` selection has since landed as an
   interactive keyboard-navigable selector (see Pi Gap Queue item 2). The core
   editor-ergonomics gap has since closed too: in-app `/login`/`/logout`,
   in-memory Up/Down prompt history, ANSI bracketed paste (literal multi-line
   insert with no accidental submission), Ctrl-Z/Ctrl-Y undo/redo, and
   poll-based resize/SIGWINCH handling now ship and are covered by real-PTY
   keystroke tests (history recall, paste, undo/redo, fake-auth login/logout
   without a provider turn, and TIOCSWINSZ resize at 80x24 and 100x40). Optional
   persistent cross-session prompt history has since landed too: behind the
   `/settings` "persistent prompt history" toggle (off by default), submitted
   prompts are saved to a local-only, capped, owner-private state file
   (`PromptHistoryStore`, `~/.local/state/pipy/prompt-history.json`, overridable
   via `PIPY_PROMPT_HISTORY_PATH`; never the metadata-first session archive) and
   a fresh session seeds Up/Down recall from them; "clear persisted history"
   wipes it. Covered by store unit tests and a cross-session real-PTY recall
   test. **The remaining editor-workflow gaps have since shipped** through the
   [tui-workflow.md](tui-workflow.md) track (gated by
   `scripts/parity_checks/tui_workflow_conformance.py`): the `@` file picker
   (exact/prefix/substring ranking) and Tab path completion, `!`/`!!` shell
   shortcuts, `Shift+Tab` thinking-level and `Ctrl+P`/`Shift+Ctrl+P` model
   cycling, `Ctrl+O`/`Ctrl+T` folding, queued steering/follow-up, `Ctrl+V`
   clipboard / drag image references, the `/scoped-models` + `/hotkeys` overlays
   and new `/settings` rows, and the mouse-selection invariant.
4. Extension and resource runtime. Pi has first-class extensions, packages,
   command/theme registration, prompt templates, skills, and UI hooks. Runtime
   resource loading has landed for skills, prompt templates, custom slash
   commands, and chrome themes. The Python extension runtime has also landed for
   core local automation (commands/shortcuts, tools, lifecycle/input/prompt
   hooks, `tool_call` gates, `tool_result` transforms, minimal UI notices, and
   provider-registration mechanics wired into the native catalog/model selector)
   and local-path/managed-git package runtime composition plus package `update`
   (installed packages contribute skills/prompts/themes/extensions through
   discovery). It is still **Pi-shaped rather than Pi-equivalent**: rich
   extension UI/rendering, broader session hooks, dynamic controls, extension
   state helpers beyond the landed read-only session-manager view, and PyPI/npm
   package sources remain follow-ons.
5. Session workflows — **shipped (2026-06-02)**. The native product session
   tree (`pipy_harness.native.session_tree`) is now pipy's product session
   store, a raw private append-only JSONL tree like Pi's
   `~/.pi/agent/sessions/...` files. Product sessions persist raw
   user/assistant/tool history, rebuild provider context from the active
   branch, and expose `/session`, `/name`, `/new`, `/tree`, `/resume`
   (interactive picker overlay + non-TTY subcommands), `/fork`, `/clone`,
   durable `/compact`, the full Pi-style startup session flag set
   (`-c`/`-r`/`--session`/`--session-id`/`--session-dir`/`--name`/`--fork`/
   `--no-session`, mutual exclusion, cross-project fork prompt; the old
   `--resume`/`--branch` metadata flags retired), and branch summaries through
   pipy-owned boundaries. The existing metadata-first `pipy-session` archive
   stays a separate learning/catalog surface and is not the product session
   source. Design, behavior, and the passing conformance + Pi-comparison gates
   are in [session-tree.md](session-tree.md).
6. Tool breadth and project policy. The bounded multi-step loop is real and the
   model-visible `bash` tool is now a real shell matching Pi (arbitrary
   commands, combined bounded output, optional timeout, streamed live). The
   former pipy-specific `/verify just-check` REPL command has been removed; any
   future project-defined verification policy needs its own spec and should map
   to Pi's broad `bash`/extension-gate workflow rather than a Pi slash command.
7. User-directed context and attachments. Workspace instruction files are
   auto-loaded into the system prompt, repeated bounded file reads can load
   multiple text files across turns, and user-directed `@file` references in a
   submitted prompt now load bounded excerpts (multiple per turn, de-duped,
   fail-closed) into the next provider request in the product REPL through the
   shared bounded reader. Still missing are pasted image/binary attachments,
   richer context pickers, and broader repo/resource maps.
8. Active-turn cancellation fidelity — **resolved**. Escape **and** Ctrl-C during
   an active turn now truly cancel the in-flight provider request: a per-turn
   `CancelToken` (`pipy_harness.native.cancellation`) is threaded through
   `ProviderPort.complete(...)` into the `urllib`/SSE HTTP boundary. The
   underlying connection registers on the token at `connect()` time. On abort,
   the composition-owned TUI wait adapter signals
   `native.agent.provider_turn.ProviderTurnExecutor`, which shuts the socket down —
   interrupting both the header wait (non-streaming JSON blocks inside
   `urlopen()` until generation finishes) and any body/stream read — so the
   worker's blocking read raises `ProviderCancelledError`. The executor then
   best-effort joins the daemon worker and the rendering subscriber shows red
   `Operation aborted`, returning to a usable prompt. Cancellation
   is cooperative: the aborted turn returns without appending an assistant/tool
   observation and late chunks are suppressed, so even a provider that ignored
   the token cannot mutate session/context state. The socket-shutdown read path
   tolerates CPython's `http.client._close_conn` shutdown race — a concurrent
   `self.fp = None` can surface as `AttributeError: 'NoneType' object has no
   attribute 'close'` rather than `OSError` — by mapping that (plus
   `OSError`/`ValueError`/`HTTPException`) to `ProviderCancelledError` only when
   the token is cancelled, so an aborted body read never leaks a spurious
   provider error while a genuine non-cancel error still propagates. Proven by
   boundary unit tests (real-socket proofs for the header wait, a
   `Content-Length` body read, and a `Connection: close` body read; an SSE
   EOF-on-cancel guard; deterministic `AttributeError`-mapping proofs in both the
   cancelled and not-cancelled directions) and a real-PTY test that drives the
   actual Escape/Ctrl-C key sequences mid-turn.
9. Multi-agent orchestration, indexing, and local-provider maturity. The
   headless RPC/JSON automation protocol has shipped (item 5 above). The local
   ds4 provider now has real large-model one-shot and tool-call smoke coverage;
   remaining product maturity work is broader local-provider benchmarking after
   the core shell, tool, session, and settings surfaces settle.

Textual, prompt-toolkit, curses, and a small custom terminal layer were
compared at the terminal-layer checkpoint. The current direction is a narrow
`prompt-toolkit` line-editor adapter as the first step. Textual, curses, and a
custom terminal layer stay on the table for when the product needs a fuller UI
surface or lower-level terminal ownership.

### Historical Pi Feature Gap Snapshot (groomed 2026-07-14)

This retained snapshot compares the 2026-07-14 worktree with local Pi main at `b084d2fb`
(`0.80.6` plus the 2026-07-13 unreleased changes). It supersedes the 2026-07-06
ranking after the OpenAI-Codex transport closeout. It is historical evidence,
not a current slice-selection aid. Use the refreshed
[Pi gap audit](pi-mono-gap-audit.md), `docs/pi-parity.md`, and the per-topic
specs for current product-gap decisions.

Shipped foundations that should no longer be selected as large topics:

- native product session tree, `/session`/`/name`/`/new`/`/tree`/`/resume`/
  `/fork`/`/clone`, durable `/compact`, and the full Pi startup-session flag
  set;
- product TUI/editor workflow depth, including `@` picker, path completion,
  `!`/`!!`, thinking/model hotkeys, folding, queued steering/follow-up,
  clipboard/drag image references, overlays, mouse-selection invariant, and
  true provider-request cancellation, plus soft-wrapped long editable prompts
  with cursor mapping and resize-safe footer pinning;
- layered settings/keybindings, scoped models, resource toggles, `/reload`,
  `/changelog`, and `--version`;
- Pi-shaped `--mode json`, `--print`/`-p`, and `--mode rpc`; and
- provider/model catalog construction for the implemented adapter families,
  one-shot runs, startup resolution, and extension-registered provider rows; and
- core local extension/package workflows: discovery/activation, commands,
  shortcuts, lifecycle/input hooks, tool gates/tools/result transforms,
  provider-registration mechanics wired into `--list-models`, startup
  resolution, `/model`, and `/reload`, local-path/managed-git package CLI,
  package `update`, and package runtime composition for
  extensions/skills/prompts/themes;
- extension API follow-ons for custom session entries and rich message
  renderers, command/shortcut editor helpers, session-manager views and metadata
  actions, tool-output expansion controls, hidden-thinking labels, terminal
  input subscriptions, footer data, and custom-message delivery; and
- product export/import/share/distribution baseline: `/export` HTML and JSONL,
  `/import`, `/share`, top-level `--export`, self-update planning, install docs,
  and `scripts/parity_checks/export_distribution_conformance.py --json`.

The immediate queue is ordered by reviewable value, while the strategic ranking
still recognizes that extension/package parity is the largest surface by area:

1. **GPT-5.6 Sol plus the `max` thinking level — shipped (2026-07-14).**
   `openai-codex/gpt-5.6-sol` (372K context, image input) is a built-in row, the
   thinking vocabulary now runs through `max`, and a Codex-scoped clamp carries
   the selected effort into the legacy Codex provider
   ([gpt-5-6-sol-plan.md](gpt-5-6-sol-plan.md)). Terra/Luna, API pricing tiers,
   other provider rows, and generalized cross-provider clamping stayed out of the
   slice; the last is a named follow-on.
2. **Project trust and project-local configuration safety.** The design slice
   shipped 2026-07-15 and pins Pi's ancestry, loading order, protected/exempt
   sources, mode/default/override behavior, `/trust`/reload semantics, and
   extension ownership
   ([design](specs/2026-07-15-project-trust-design.md),
   [implementation plan](specs/2026-07-15-project-trust-implementation-plan.md)). Pi gates
   project-local settings/resources/packages behind saved or temporary trust,
   exposes `defaultProjectTrust`, `--approve`/`--no-approve`, `/trust`, and the
   extension `project_trust`/`ctx.isProjectTrusted()` surface. Pipy now ships
   the trust core, final-cwd settings/resource gate, global default, saved
   ancestry, run-only overrides, interactive selector, `/trust`, guarded reload
   persistence, package/config integration, pre-trust global/CLI extension
   decision ownership with activation reuse, and run-local trust read aliases.
   The project-trust track is complete.
3. **Current RPC delta.** Pipy's gated RPC baseline covers Pi's 31 command
   names: the read-only `get_entries` (including `since`) and
   `get_tree` **shipped** 2026-07-14. The asynchronous `agent_settled` event also
   shipped that day at the true-idle boundary on both the `--mode rpc` and
   `--mode json` streams. The extension-surface hook shipped independently on
   2026-07-16. Direct bash updates/cancellation and true in-turn injection
   remain separate semantic follow-ons.
4. **Extension lifecycle and rendering deltas.** The request-scoped
   `before_provider_headers` hook and the true-idle `agent_settled` lifecycle
   hook **shipped** 2026-07-16, and durable TUI-only entry renderers
   (`registerEntryRenderer` over `appendEntry`) shipped 2026-07-17. Message and
   entry renderers now have independent ownership. The broader custom editor/
   component/overlay stack, live tool-render invalidation, richer multi-widget
   UI, and RPC extension-UI channel remain strategic extension follow-ons.
5. **Cache-friendly dynamic tool loading.** The provider-agnostic durable
   load-point marker and supported Anthropic `defer_loading` / message-anchored
   `tool_reference` path **shipped** 2026-07-17. OpenAI/Codex Responses
   `tool_search_call`/`tool_search_output` placement has now shipped too. Kimi's
   Chat Completions deferred-tool shape remains an independent provider-owned
   slice.
6. **Package/update realignment.** Pi's bare `update` is now self-only;
   `--all` composes self plus packages, and `--extensions` is packages-only.
   Pipy's project-local `config -l` and its trust integration already ship, but
   bare update still composes both halves. Realign only the update CLI outright
   under the no-deprecation policy; keep remote PyPI/npm execution behind the
   broader supply-chain decision.
7. **July provider/request-shape deltas.** Audit and split rather than bundle:
   forced tool choice, OpenRouter session affinity, Copilot MAI routing,
   Bedrock/Cloudflare ambient/API-key auth, pricing tiers, and generated catalog
   refreshes have different ownership boundaries. Live Anthropic/Copilot login
   UX, a full `detectCompat` port, and broader local-provider benchmarking also
   remain. Spec: [provider-catalog.md](provider-catalog.md).
8. **Small TUI/settings polish.** Pi now binds Ctrl+X to copy the last assistant
   message (or the selected `/tree` entry), while pipy exposes `/copy` but not
   that general keybinding. Prompt-cache miss notices, automatic theme mode,
   output padding, and the configured external-editor/shell refinements are
   lower priority and should stay independent slices.

User documentation and top-level CLI consolidation are no longer blocking
topics. Keep their pages synchronized as the queue lands. Verification remains
the model-visible `bash` tool plus extension-defined gates; do not revive the
pipy-only `/verify` command. Reusable `pi-agent`/`pi-ai`/`pi-tui` library API
parity and the experimental orchestrator remain outside the pipy-native product
target.

### Fresh TUI parity bug reports (2026-06-19)

These are small, user-visible polish/parity items reported from a live
`openai-codex` / `gpt-5.5` product-TUI session. They should be fixed as narrow
TUI/status slices rather than bundled into the broad extension/package track.

- **Right-edge TUI gutter / footer clipping — resolved.** Live-frame painting
  now reserves a consistent one-cell safety gutter and footer/status formatting
  uses the same safe width, so `• high` remains visible without wrapping into
  the unsafe final column. The real-PTY resize/settings regressions now expect
  the safe-width separator.
- **Working spinner color parity — resolved.** Working rows render with the
  theme accent color instead of secondary gray while preserving the existing
  `NO_COLOR`/non-TTY and extension working-message behavior; an ANSI style
  regression pins this.
- **Footer usage/status parity — partially resolved.** The footer accumulator
  now tracks cache-read (`cached_tokens`) and cache-write counters, renders Pi's
  `R`/`W` cache labels plus `CH…%`, and keeps provider reasoning counters out
  of the Pi `R` cache-read label. Focused formatter/usage tests cover the new
  semantics; provider-specific cache pricing can still be refined from catalog
  rates later.

## Parity Cleanup: accidental pipy-only surfaces to remove or realign

These surfaces exist only in pipy and not in Pi. Per the parity principle
(`parity-plan.md` §1), a pipy-only surface is removed or realigned to Pi unless
there is a genuinely good reason to keep it — **privacy and security are not good
reasons.** Pi stores full session transcripts, streams full session events, and
exports full sessions; pipy's "metadata-first" posture is a pipy preference, not
a parity virtue, and must not justify diverging from Pi. The full table with
rationale is in [parity-plan.md](parity-plan.md) §3; the actionable removals are:

- **Metadata-first `pipy-session` archive as the product session store.**
  **Resolved (2026-06-02).** The full-transcript native session tree
  ([session-tree.md](session-tree.md)) is now the product store; `pipy-session`
  is a separate, non-default metadata catalog/learning utility. The docs/specs
  no longer present metadata-first as the product session source.
- **`--archive-transcript` sidecar.** **Removed (2026-06-20).** The native
  session tree is the transcript; use `/export` or top-level `--export` for
  product-session exports.
- **`--native-output json` (metadata-only).** **Removed (2026-06-20).**
  Automation callers use Pi's `--mode json` full-event stream, `--mode rpc`, or
  `--print` ([automation-rpc.md](automation-rpc.md)).
- **No-tool REPL mode and its `/read` `/ask-file` `/propose-file`
  `/apply-proposal` commands.** **Removed (2026-06-20).** There is one product
  REPL, the model-visible tool-loop session.
- **`/clear` → `/new` + `/compact`; `/status` → `/session`.** **Removed
  (2026-06-20), no aliases.** Use Pi's `/new` and `/session`
  ([session-tree.md](session-tree.md)).
- **`/theme` command, `/template` dispatcher command, `/help`.** **Removed
  (2026-06-20), no aliases.** Theme selection lives in `/settings`, prompt
  templates register as their own `/<name>` commands, and `/hotkeys` is rendered
  from the resolved keybinding manager. `/skill` is kept as parity-consistent
  with Pi's skill expansion model, and discovered skills are advertised in the
  product system prompt for model-side loading via `read`.
  ([settings-config.md](settings-config.md), [provider-catalog.md](provider-catalog.md)).
- **Hardcoded `ds4` built-in provider.** Reframe as a `models.json`
  custom-provider preset ([provider-catalog.md](provider-catalog.md)).
- **Verify-and-decide:** `--read-root(s)`, `--tool-budget`, `--input-runtime`,
  and persistent prompt history (`PromptHistoryStore`) are pipy-only mechanisms
  with possible non-privacy justifications; keep only if they map to a real Pi
  workflow or are cheap, clearly-useful conveniences, otherwise drop.
- **Keep (non-feature):** the CQ-A..F code-quality audit tracks are pipy
  engineering hygiene, not Pi features, and stay as internal cleanup work.
- **Already done:** the `/verify just-check` command was removed (not a Pi
  feature).

`docs/parity-criterion.md`, `docs/pi-parity.md`, and `docs/session-storage.md`
have been updated so they no longer present the metadata-first/privacy posture as
a parity virtue or a reason to diverge from Pi.

## Tool-Loop Parity Track

The bounded model-selected tool loop behind the product `pipy repl` path is now
implemented. It shipped as twelve reviewed slices plus an OpenRouter
response-parser follow-up and a first-review fix-up commit. Each slice
landed as a named conventional commit with focused tests, `just check`,
updated docs, and a stop for review. OpenRouter was the first real
provider with `supports_tool_calls=True`; OpenAI Responses and OpenAI
Codex parsers now ship through the separate
[OpenAI Responses + OpenAI Codex Tool-Call Parity Track](#openai-responses-openai-codex-tool-call-parity-track).

Use this section together with the matching design notes in
`docs/harness-spec.md` (`Native Tool-Loop Parity Track`) and the parity-map
entry in `docs/pi-parity.md` (`Native Tool-Loop Parity Track`).

### Goal

- A real model-driven loop over `openai`, `openai-codex`, and `openrouter` with
  bounded `read`, `write`, `edit`, `ls`, `grep`, and `find` tools, producing a
  useful end-to-end change against this repo with `just check` green.
- Pi-shaped behavior: the model picks files, edits them directly, the resulting
  unified diff is written to stderr, no approval popups appear, and the loop
  iterates within a bounded tool budget.
- Historical note: this original track preserved the no-tool REPL slash
  commands while the model-visible tool loop was landing. The later 2026-06-20
  parity cleanup removed `--repl-mode no-tool` and `/read`, `/ask-file`,
  `/propose-file`, and `/apply-proposal`; the current surface is the single
  product REPL with model-visible tools.

### Planned Slices

1. Docs only. Record the tool-loop parity goal, invariants, and deferred work
   in `docs/pi-parity.md`, `docs/backlog.md`, and `docs/harness-spec.md`.
2. `tools/base.py` contracts: `ToolDefinition`, `ToolRequest`,
   `ToolExecutionResult`, `ToolArgumentError`, `ToolContext`, and `ToolPort`,
   built from stdlib dataclasses with manual JSON-schema validation. Focused
   contract tests, no provider or REPL wiring.
3. `ProviderPort` extension: a `supports_tool_calls` capability flag (real
   providers stay `False`), a `ProviderToolCall` value object, `tool_calls` on
   `ProviderResult`, and a provider-agnostic message envelope
   (`user`/`assistant`/`tool_result`). The fake provider gains
   `programmable_tool_calls` for tests; real adapters stay inert.
4. `NativeToolReplSession` skeleton: bounded turn loop with `--tool-budget`
   defaulting to 10 (max 25), malformed tool arguments returned to the model as
   an observation (fatal after three consecutive malformed turns), valid tool
   execution errors returned as ordinary observations, a test-only `_FixtureTool`
   injected by tests, and an empty production tool registry.
5. `read` tool: reuses `read_only_tool.py` validation. The first real provider
   adapter flips `supports_tool_calls` to `True`; a manual smoke run lands with
   the slice.
6. `ls` tool: bounded directory entries returned as workspace-relative paths.
7. `grep` tool: `subprocess.run` to `rg` with no `shell=True`, a fixed argv, a
   workspace `cwd`, a timeout, and bounded results, with a stdlib fallback when
   `rg` is unavailable.
8. `find` tool: bounded glob lookup.
9. `write` tool: create-only; refuses existing files, `.git`, and paths that
   escape the workspace; applies directly and writes the unified diff to
   stderr. Tests pin: file mutation, diff lands only on stderr, archive remains
   untouched, and the diff lands in the opt-in sidecar only when enabled.
10. `edit` tool: string-replace with a unique-`old_string` default and an
    opt-in `replace_all`; reuses `patch_apply.py`. Same diff and archive
    privacy tests.
11. Opt-in `TranscriptSink`: a sidecar JSONL at
    `~/.local/state/pipy/transcripts/<id>.jsonl`, enabled by
    `--archive-transcript`, marked sensitive, written outside the metadata
    archive, and excluded from `pipy-session list/search/inspect`. Focused
    privacy tests.
12. Flip the default `--repl-mode` to `tool-loop` when the selected provider
    supports tool calls. The `no-tool` mode stays available. Update README and
    user-facing docs.

### Invariants

These hold throughout the track, not as later deferrals:

- Metadata-first archive privacy is preserved exactly across the whole track.
  `pipy_session.recorder` records no prompts, model text, tool payloads, file
  contents, or diffs in any slice. Any leak fails the slice.
- `.git` is default-deny across all model-driven tools. Slash commands are
  unaffected.
- No new runtime dependencies. Stdlib plus manual dict validation only; no
  pydantic.
- `NativeToolResult` carries archive-safe metadata only;
  `ToolExecutionResult` carries provider-visible payloads. The two shapes are
  not conflated.
- The internal pipy-owned `tool_request_id` does not leak as a provider id;
  provider identifiers are carried separately as `provider_correlation_id`.
- The existing no-tool REPL and the listed slash commands keep working in both
  modes.
- Each slice ships focused tests, a green `just check`, updated docs, a
  conventional commit, and stops for review.

### Out Of Scope For This Track

These were explicitly deferred for the original tool-loop track; some have now
shipped in later parity work:

- Arbitrary shell execution. **Update (shipped):** the model-visible `bash`
  tool is now a real shell, matching Pi's bash tool — it spawns `bash -c
  <command>` in the workspace root with the inherited environment, an optional
  timeout (the process group is killed when it elapses), and returns combined,
  bounded stdout/stderr to the model. Pipes, redirection, substitution,
  globbing, chaining, and any executable on `PATH` are allowed. Only metadata
  (counters, labels) is recorded at the archive boundary — never the raw
  command or output.
- Project-defined verification policy beyond the Pi-style model-visible `bash` workflow.
- Live session resume, branch/fork navigation, and compaction. A metadata-only
  resume reader shipped first; live `--resume`, `--branch`, and `/compact`
  (plus an automatic compaction threshold) shipped later through the Native
  Session Workflow Track below.
- RPC mode and SDK embedding. The in-process Python SDK shipped, and the
  headless `--mode json`/`--mode rpc`/`--print` automation protocol has now
  shipped too ([automation-rpc.md](automation-rpc.md)); only the
  network/socket daemon remains deferred.
- Extensions, package loading, theme integration, and slash-command loading for
  skills and prompt templates. A pure theme registry shipped later.
- ~~Automatic `@file` content reads from completion-only references.~~
  (Historical: was out of scope for this track. User-directed `@file` content
  reads subsequently shipped — a submitted prompt's `@path` references load
  bounded excerpts through the shared bounded reader in the product REPL.)
- Persistent shell history and a full interactive TUI.
- Additional providers beyond `openai`, `openai-codex`, and `openrouter`.
  Shipped later for the eight providers listed in `docs/parity-criterion.md`.
- Removing the no-tool REPL or its slash-command boundaries.

## OpenAI Responses + OpenAI Codex Tool-Call Parity Track

The Native Tool-Loop Parity Track originally shipped end-to-end with
OpenRouter as the only real provider advertising
`supports_tool_calls=True`. This follow-up track extended the same
loop closure to `OpenAIResponsesProvider` and
`OpenAICodexResponsesProvider`, so `pipy repl --agent pipy-native
--native-provider openai` and `--native-provider openai-codex` now
drive the existing bounded tool loop end-to-end against their
respective endpoints, matching the bar set by OpenRouter in
`tests/test_tool_loop_end_to_end.py`,
`tests/test_tool_loop_end_to_end_openai.py`, and
`tests/test_tool_loop_end_to_end_openai_codex.py`.

Use this section together with the matching design notes in
`docs/harness-spec.md` (`OpenAI Responses + OpenAI Codex Tool-Call
Parity Track`) and the parity-map entry in `docs/pi-parity.md`
(`OpenAI Responses + OpenAI Codex Tool-Call Parity Track`).

### Goal

- `OpenAIResponsesProvider` serializes the provider-agnostic message
  envelope plus `available_tools` into the OpenAI Responses API
  `input`/`tools` shape, parses `function_call` outputs into
  `ProviderToolCall` values on `ProviderResult.tool_calls`, serializes
  `AgentToolResultMessage` as Responses `function_call_output` items, and
  flips `supports_tool_calls=True`.
- `OpenAICodexResponsesProvider` does the same over Codex Responses
  streaming, assembling function calls across the SSE event stream
  (`response.output_item.added` / `response.function_call_arguments.delta`
  / `response.output_item.done` or equivalents) and flipping
  `supports_tool_calls=True`.
- Each provider ships a hermetic end-to-end loop-closure test against a
  stub transport (JSON for `openai`, SSE for `openai-codex`), mirroring
  the OpenRouter bar in `tests/test_tool_loop_end_to_end.py`.
- Legacy no-tool / single-turn callers (`/ask-file`, `/propose-file`,
  `pipy run --agent pipy-native --goal ...`) keep their existing
  behavior; their tests stay green unchanged.

### Planned Slices

1. Docs only. Record the OpenAI parity goal, invariants, slice plan,
   and deferred work in `docs/pi-parity.md`, `docs/backlog.md`,
   `docs/harness-spec.md`, `docs/architecture.md`, and `README.md`.
2. `OpenAIResponsesProvider` tool-call wiring: serialize messages and
   tools into the Responses `input`/`tools` shape, parse `function_call`
   outputs into `ProviderToolCall`, serialize `AgentToolResultMessage` as
   `function_call_output`, flip `supports_tool_calls=True`, ship the
   hermetic JSON-transport end-to-end test, and update the existing
   `test_real_providers_advertise_tool_call_support_correctly`
   assertion that pins `openai.supports_tool_calls is False`.
3. `OpenAICodexResponsesProvider` tool-call wiring: serialize messages
   and tools into the Codex Responses streaming shape, assemble
   function calls across the SSE event stream, allow terminal
   `response.completed` without final text when tool_calls are present,
   serialize `AgentToolResultMessage` as `function_call_output`, flip
   `supports_tool_calls=True`, and ship the hermetic SSE-transport
   end-to-end test.
4. README and cross-doc cleanup: remove any remaining "follow-up" /
   "OpenRouter is the only" phrasing once both providers have shipped.

### Invariants

These hold throughout the track, not as later deferrals:

- Metadata-first archive privacy is preserved exactly. `pipy_session.recorder`
  records no prompts, model text, tool payloads, file contents, or diffs
  in any slice. Pinned by tests.
- `.git` is default-deny across all model-driven tools, including the
  resolved-symlink check via `_resolved_relative_label`.
- No new runtime dependencies. Stdlib plus manual dict validation only.
  No pydantic, jsonschema, or attrs.
- Reuse the existing tool-loop contracts and helpers (`ToolDefinition`,
  `ToolRequest`, `ToolExecutionResult`, `ToolPort`, `validate_arguments`,
  the `AgentMessage` envelope, `NativeToolReplSession`). Do not redesign
  the loop.
- `NativeToolResult` (archive-safe metadata) and `ToolExecutionResult`
  (provider-visible payload) stay strictly separate; do not conflate.
- Pipy-owned `tool_request_id` (`pipy-tool-` prefix) stays internal;
  provider identifiers ride separately as `provider_correlation_id`.
- The no-tool REPL and the existing slash commands keep working in
  both modes.
- The removed `--archive-transcript` sidecar is not reintroduced. Full-content
  history stays in the private native product session tree and remains separate
  from `pipy-session list/search/inspect` metadata archive surfaces.
- Each slice ships focused tests, a green `just check`, updated docs,
  a conventional commit, and stops for review.

### Out Of Scope For This Track

- Project-defined verification policy beyond the Pi-style `bash` workflow, session
  resume/branch/compaction, RPC mode, SDK embedding, extensions,
  theme/package loading, persistent history, and a full TUI. (User-directed
  `@file` content reads were once out of scope here and have since shipped.)
- Removing the no-tool REPL or redesigning the tool-loop contracts.

## Workspace Context Loading Parity Track

The named Pi-parity track after the
[OpenAI Responses + OpenAI Codex Tool-Call Parity Track](#openai-responses-openai-codex-tool-call-parity-track)
added pipy-owned workspace instruction discovery and injection into the native
pipy system prompt. It now ships end-to-end through `pipy run --agent
pipy-native` and the single product tool-loop REPL for the native providers. The
loader resolves the global pipy config root, walks from the workspace upward
through every parent directory, picks the first existing file per directory in
the candidate list `AGENTS.md > AGENTS.MD > pipy.md > PIPY.md`, and dedupes by
canonical path; the returned list is composed global-first, then ancestors from
the root-most ancestor down to the workspace's direct parent, then the workspace
itself last, so more-specific instructions override earlier ones. `CLAUDE.md`,
`.codex/...`, and other neighboring-agent files are intentionally not loaded.

Use this section together with the matching design notes in
`docs/harness-spec.md` (`Workspace Context Loading Parity Track`)
and the parity-map entry in `docs/pi-parity.md`
(`Workspace Context Loading Parity Track`).

### Goal

- `pipy repl --agent pipy-native` and `pipy run --agent pipy-native`
  send a system prompt that includes the workspace's selected instruction file
  (`AGENTS.md`, `AGENTS.MD`, `pipy.md`, or `PIPY.md`) plus any parent-walk and
  global pipy instructions, across native providers.
- A round-trip smoke shows the model honoring an instruction stated
  only in `AGENTS.md` and a hermetic test pins the same against the
  fake provider.
- Discovery rules are pinned by focused unit tests: per-directory
  candidate filename precedence, nested-workspace ordering, missing
  files do not fail, symlinks must resolve inside the directory where
  they are found, the global root respects `PIPY_CONFIG_HOME`, then
  `XDG_CONFIG_HOME/pipy`, then `~/.config/pipy`, and bounded
  per-file and total byte caps apply with deterministic truncation
  labels.
- `pipy-session` records only metadata about which instruction files
  were loaded: workspace-relative path or `<global>` label, sha256,
  byte length, and a `truncated` flag, plus a
  `total_byte_cap_reached` boolean. A test pins that no instruction
  body reaches session JSONL, the Markdown summary, or the opt-in
  `--archive-transcript` sidecar.

### Planned Slices

1. Docs only. Record the workspace-context parity goal, invariants,
   slice plan, and deferred work in `docs/pi-parity.md`,
   `docs/backlog.md`, and `docs/harness-spec.md`.
2. Workspace instruction loader. Add
   `pipy_harness.native.workspace_context` with a
   `WorkspaceInstructionFile` value object and a
   `discover_workspace_instructions(...)` helper that mirrors
   `loadProjectContextFiles` through pipy-owned Python: per-directory
   candidate precedence, parent-walk ordering (root-most ancestor
   first, the workspace itself last), the global root resolved
   through `PIPY_CONFIG_HOME` then `XDG_CONFIG_HOME/pipy` then
   `~/.config/pipy`, deduplication by canonical absolute path,
   symlink resolution that stays inside the containing directory, bounded
   per-file and total byte caps with deterministic truncation
   labels, and "missing files do not fail" semantics. Focused unit
   tests pin every rule. No REPL or run wiring in this slice.
3. System-prompt wiring and archive metadata. Compose the system
   prompt from the existing bootstrap base plus the discovered
   instructions, and pass it through the one-shot native runner and product
   tool-loop REPL. Record per-run
   `workspace_instruction_files` metadata (workspace-relative or
   `<global>` label, sha256, byte length) in the session safe
   context. Pin that bodies never appear in JSONL, the Markdown
   summary, or the native session tree. Ship a hermetic
   round-trip test against a request-capturing fake provider that
   proves an `AGENTS.md` instruction reaches
   `ProviderRequest.system_prompt` across the product REPL and one-shot
   runner.
4. Docs cleanup and close. Move the parity-map row to "Implemented",
   remove the "Still To Slopfork" / "Deferred" wording for
   AGENTS/pipy-style context discovery, refresh the Pi Parity
   Roadmap context/resource-loading bullet, and run a real-provider
   smoke (recorded as a metadata-only `pipy-session`) that honors an
   `AGENTS.md`-only instruction end-to-end.

### Invariants

These hold throughout the track, not as later deferrals:

- Metadata-first archive privacy is preserved exactly.
  `pipy_session.recorder` records no instruction bodies in any
  slice; only safe per-file metadata (path label, sha256, byte
  length). Pinned by tests.
- The full native session tree is the transcript store; instruction bodies
  never reach the metadata-first archive.
- `.git` default-deny posture remains in force; file access now goes through
  model-visible tools rather than the removed no-tool proposal commands.
- No new runtime dependencies. Stdlib plus manual dict validation
  only. No pydantic, jsonschema, or attrs.
- Reuse the existing `ProviderPort` message envelope and
  `NativeToolReplSession`. Do not redesign the loop.
- Per-file and total byte caps are enforced before the prompt is
  composed; an over-cap file is included up to its slice with a
  deterministic truncation marker, and over-total reads stop at the
  total cap with a deterministic notice.
- Symlinks that resolve outside the containing directory are skipped; their
  metadata is not recorded.
- Each slice ships focused tests, a green `just check`, updated
  docs, a conventional commit, and stops for review.

### Out Of Scope For This Track

These remain explicitly deferred while the track lands and after
it lands. They are not later slices of this track:

- Slash-command loading for skills and prompt templates, extensions, and
  package loading. (Resolved later: runtime `/skill`, prompt templates as their
  own `/<name>` slash commands, and custom `/<name>` loading shipped in the
  Runtime Resource Loading Track below. General extensions, package loading, and
  a theme registry shipped later through the extension/package tracks.)
- Live session resume, branch/fork, compaction, and share. A metadata-only
  resume reader shipped first; live `--resume`, `--branch`, and `/compact`
  (with an automatic threshold) have since shipped through the Native Session
  Workflow Track below.
- Full TUI and persistent cross-session history. (Resize handling, in-memory
  prompt history, bracketed paste, undo/redo, an interactive `/settings` control
  dialog, and optional persistent cross-session prompt history have since shipped
  in the product TUI.)
- Project-defined verification policy beyond the Pi-style model-visible `bash` workflow.
- Watching the workspace for instruction-file changes during a
  session. The current track resolves instructions once per run.

## Streaming Output Parity Track

The named Pi-parity track after the
[Workspace Context Loading Parity Track](#workspace-context-loading-parity-track)
closes parity-criterion row C14 ("streaming output
(provider→stdout)"). Pi exposes provider chunks through
`AssistantMessageEventStream` in
`pi-mono/packages/ai/src/utils/event-stream.ts`; pipy slopforks the
useful surface — incremental text deltas reaching a configurable sink
during `pipy run` — through pipy-owned Python boundaries, not as a
literal event-stream port. The track ships as four reviewed slices
(docs-only opener, `ProviderPort` stream sink plus fake-provider
wiring, first real-provider streaming on `OpenAICodexResponsesProvider`,
and `pipy run --stream` plumbing plus the matching docs flip).

Use this section together with the matching design notes in
`docs/harness-spec.md` (`Streaming Output Parity Track`) and the
parity-map entry in `docs/pi-parity.md`
(`Streaming Output Parity Track`).

### Goal

- `pipy run --agent pipy-native --stream` routes provider-emitted
  text deltas to stdout as they arrive, while the final successful provider
  text and the metadata-first archive records are unchanged. The former
  `--native-output json` mode has since been removed; current automation uses
  `pipy repl --mode json` for the separate full-content Pi-shaped session event
  stream, and that mode is not combined with `pipy run --stream`.
- One real provider (`openai-codex`, whose SSE parser already
  iterates `response.output_text.delta` events) flips on streaming
  first. Other tool-capable providers (`openai`, `openrouter`) stay
  non-streaming for this track and remain functional on the existing
  buffered path.
- A hermetic streaming-stub test pins the chunk order and proves the
  same final `ProviderResult` shape whether streaming is enabled or
  not. The fake provider gains a `programmable_text_chunks` field
  for unit-level coverage that does not depend on transport details.
- `pipy run` without `--stream`, the no-tool REPL, the tool-loop
  REPL, `/ask-file`, `/propose-file`, `/apply-proposal`, and
  the no-tool REPL does not force any path through streaming
  through streaming.

### Planned Slices

1. Docs only. Record the streaming parity goal, invariants, slice
   plan, and deferred work in `docs/pi-parity.md`, `docs/backlog.md`,
   and `docs/harness-spec.md`.
2. `ProviderPort` stream sink. Add a `StreamChunkSink` callable
   alias in `pipy_harness.native.provider`, extend `complete(...)`
   with an optional keyword-only `stream_sink` parameter that
   defaults to `None`, and let `FakeNativeProvider` push a new
   `programmable_text_chunks` tuple through the sink when supplied.
   Existing real-provider implementations accept the keyword and
   ignore it; their existing buffered behavior is unchanged. Focused
   contract tests pin: missing sink keeps current behavior
   bit-for-bit; supplied sink receives chunks in order; the final
   `ProviderResult.final_text` is the concatenation of the supplied
   chunks.
3. First real-provider streaming. Wire
   `OpenAICodexResponsesProvider` to call the supplied sink for each
   parsed `response.output_text.delta` event before returning the
   buffered final text. A hermetic SSE-transport test injects a
   multi-delta stream and asserts: chunks reach the sink in source
   order, the final `ProviderResult.final_text` is byte-equivalent
   to the non-streaming case, and the session archive records no
   chunk bodies.
4. `pipy run --stream` plumbing plus C14 close. Add the `--stream`
   flag to `pipy run` (default off), route chunks to stdout in text
   mode and stderr in JSON mode, fail closed with a metadata-only
   stderr diagnostic when the active provider does not advertise
   streaming, flip parity-criterion C14 to `✅`, refresh
   `docs/pi-parity.md` (this section moves to the "What Has Been
   Slopforked" table), and re-run `just parity-score`.

### Invariants

These hold throughout the track, not as later deferrals:

- Metadata-first archive privacy is preserved exactly.
  `pipy_session.recorder` records no streamed chunk bodies, deltas,
  prompts, model text, tool payloads, file contents, or diffs in
  any slice. Pinned by tests.
- The full native session tree is the transcript store; streamed chunks never
  reach the metadata-first archive.
- `.git` default-deny posture remains in force; file access now goes through
  model-visible tools rather than the removed no-tool proposal commands.
- No new runtime dependencies. Stdlib plus manual dict validation
  only. No pydantic, jsonschema, attrs, anyio, or trio.
- Reuse the existing `ProviderPort`, `ProviderRequest`, and
  `ProviderResult` shapes. The streaming surface is an optional
  keyword on `complete(...)`, not a new method or a new request
  envelope.
- Streaming is purely additive: a provider that does not implement
  the keyword keeps working, a caller that does not supply a sink
  keeps working, and `pipy run` without `--stream` keeps the
  existing default-text stdout contract.
- The internal pipy-owned `tool_request_id` and
  `provider_correlation_id` boundaries are unaffected; streaming
  carries no tool-call payloads in this track.
- Each slice ships focused tests, a green `just check`, updated
  docs, a conventional commit, and stops for review.

### Out Of Scope For This Track

These remain explicitly deferred while the track lands and after
it lands. They are not later slices of this track:

- Streaming tool-call argument deltas; tool calls remain buffered.
- Streaming thinking/reasoning deltas. Pipy stays metadata-only on
  thinking content.
- Streaming in `--repl-mode no-tool` and `--repl-mode tool-loop`;
  the initial track wires `pipy run` only.
- Streaming for providers other than `openai-codex`; the other
  eleven adapters stay on their buffered paths in this track.
- Image, binary, or multimodal chunks; the sink carries text only.
- Cancellation, backpressure, and async streaming. The sink is a
  synchronous callable invoked from the provider's existing
  thread/transport.

## Code Quality Audit Track (2026-05-26)

A seven-agent comparative audit ran against `pi-mono` on 2026-05-26 with the
brief: find AI slop, plausible-but-wrong control flow, permissive error
handling, and bad-state-handled-instead-of-prevented patterns that have
accreted in the pipy slopfork. The audits live under
`docs/audit/2026-05-26/code-quality-audit/` (151 findings, line-cited):

- `01-session-repl.md` — native session + REPL (20 findings, 4 high)
- `02-providers.md` — 12 provider adapters (20 findings, 5 high)
- `03-tools.md` — tools layer + dual `ToolPort` (24 findings, 6 high)
- `04-cli-runner.md` — CLI, runner, adapters (22 findings, 3 high)
- `05-session-storage.md` — `pipy_session` recorder/catalog (20 findings, 5 high)
- `06-chrome-resources.md` — chrome + resource discovery (23 findings, 7 high)
- `07-value-objects.md` — value objects + state (24 findings, 5 high)

The dominant signal: pipy ships ~29 KLoC against pi-mono's ~28 KLoC for
the equivalent feature surface (excluding pi-mono's `tui/`, `ai/` model
registry, and most providers), but at least 4–6 KLoC of that is
demonstrable slop: dead modules with zero production callers, parallel
families with overlapping responsibilities, eleven-fold duplication of
the same provider scaffolding, and defensive runtime guards on closed
type universes.

This track is not a single linear roadmap; it is a list of small,
reviewable cleanup slices grouped into six themed tracks. Each slice is
intended to ship as a focused conventional commit with `just check`
green and documentation updates. Pick the next slice from whichever
track has the highest leverage at the time. Audit file references are
shorthand for the detailed finding (e.g. `01:F3` is finding F3 in
`01-session-repl.md`).

### Invariants (apply across all tracks)

- No new runtime dependencies. Stdlib plus manual dict validation only.
  No pydantic, jsonschema, attrs, or typebox port.
- Metadata-first archive privacy is preserved exactly. No prompt, model
  text, tool payload, file content, or diff reaches JSONL, Markdown, or
  the catalog. Each cleanup slice that touches the boundary re-pins the
  invariant.
- `.git` default-deny, symlink containment, and bounded byte caps
  remain in force across every tool and resource loader.
- The bounded model-driven tool loop, native session tree, and current
  `/login`/`/logout`/`/model`/`/settings` commands keep working through the
  single product REPL.
- "Bad state impossible by construction" beats "bad state handled at
  runtime." When a finding offers both options, prefer the structural
  fix.

### Track CQ-A: Dead code removal

These modules ship with zero production callers, are wired through one
test, or cannot fire because an upstream cap blocks them. Removing each
also removes its tests and any docs claims that quietly assume the
module is live.

1. Remove `pipy_harness.native.dynamic_provider` (140 L wrapper around
   one `state.select_model` call, used only by its own test).
   Refs: `02:F18`, `07:F10`. **Done, and the capability (E5) is now
   ✅** — not by recreating the wrapper, but by verifying the live
   `/model` swap through the shared `NativeReplProviderState` boundary in the
   product REPL (`scripts/parity_checks/dynamic_provider_behavior.py`).
2. Remove `pipy_harness.native.approval_prompt` (410 L; ten reasons,
   six statuses, three value objects, zero non-test, non-re-export call
   sites). Refs: `07:F11`.
3. Remove `pipy_harness.native.session_branching` (157 L; recorder
   admits the wiring is deferred). Refs: `07:F13`.
4. Remove `pipy_harness.native.session_compaction` (210 L; cannot fire
   because `NativeConversationState.MAX_TURNS = 8` is below the
   compaction threshold). Refs: `07:F12`. If compaction is desired,
   first lift the conversation cap (Track CQ-D slice 5) and only then
   bring this back. **Superseded and now done:** the no-tool REPL and its dead
   compaction path were retired, while the live product tool loop gained real
   manual/automatic and durable compaction. Phase 2.2b.2 deletes the obsolete
   mixed module and moves only the live canonical-message reduction to
   `native.agent.history`; product policy and persistence stay in the session.
5. Remove `pipy_harness.native.image_attachment` (196 L; plumbed through
   `ProviderRequest.image_attachments` but consumed by no provider).
   Refs: `07:F14`. Re-introduce only when an actual provider parses it.
   **Reintroduced (D8 now ✅)**: a bounded, fail-closed `@image:` loader
   feeds `ProviderRequest.attachments`, which the Anthropic / OpenAI-Responses
   / Google adapters now render as native image blocks; both REPL paths
   wire it in and the archive keeps only safe metadata
   (`scripts/parity_checks/attachment_behavior.py`).
6. Remove `pipy_harness.native.themes` and remove the unused theme
   registry surfaces (test-only). Refs: `06:F22`. **Reintroduced (D7 now
   ✅)**: `themes.py` is the palette registry behind `chrome.ChromeStyle`.
   Theme selection now lives in `/settings` (the pipy-only `/theme` command was
   removed in the 2026-06-20 cleanup), and rendering still honors `PIPY_THEME`
   (`scripts/parity_checks/theme_behavior.py`).
7. Remove `pipy_harness.native.skills`, `prompt_templates`,
   `custom_commands` and the chrome-side wiring that calls into them.
   Refs: `06:F1`. Reintroduce only when a runtime path consumes them.
   Until then, the banner stops advertising `[Skills]`, `[Prompts]`,
   and `[Extensions]` for inert paths.
8. ~~Remove the `BashTool` module until a real shell sandbox lands.~~
   **Done (shipped):** `pipy_harness/native/tools/bash.py` is registered as a
   real shell matching Pi — it spawns `bash -c <command>` in the workspace
   root with the inherited environment, an optional timeout (the process group
   is killed when it elapses), streams combined output live, and returns a
   bounded tail. Only metadata (counters, labels) is archived. B7 is a green
   behavior check. Refs: `03:F6`.
9. ~~Remove the pipy-only `truncate` and second `edit_diff` paths from the
   model-visible registry.~~ **Done (P1/P2):** both modules, schemas, special
   render policy, prompt/catalog inventory entries, and tool-specific tests are
   removed outright. The exact ordered manifest is now `read`, `ls`, `grep`,
   `find`, `write`, `edit`, `bash`; `edit` is the sole edit path, while read,
   bash, and provider-visible outputs retain independent automatic bounds.
   Refs: `03:F5`, `03:F15`.
10. Remove the archive-side parallel tool family
    (`read_only_tool.NativeExplicitFileExcerptTool`,
    `patch_apply.NativePatchApplyTool`,
    `verification.NativeVerificationTool` — ~1,500 L) once the slash
    commands they back are migrated to call the model-driven
    `Read`/`Edit`/`Write` tools directly through the same
    archive-safe wrapper. Refs: `03:F1`, `03:F2`, `03:F8`.
11. Remove `pipy_session.catalog.verify_session_archive` and
    `reflect_on_finalized_sessions` (60+ L dead surface with an
    18-event registry, no production caller). Refs: `05:F5`, `05:F6`.
12. Remove the speculative `auto_capture.py` surfaces
    (`reference_pi_session`, `_public_model_from_argv`, most of
    `prune_auto_capture_state` — no production caller). Refs: `05:F9`.
13. Remove `pipy_harness.adapters.subprocess.SubprocessAdapter` if its
    only consumer is the test suite. If the "support path" claim is
    real, surface a runtime consumer. Refs: `04:F14`.
14. Remove `pipy_harness.sdk` if `__init__.py` already re-exports the
    same primitives. Or make `sdk` the documented surface and demote
    `__init__.py`. Choose one. Refs: `04:F20`.
15. Remove the `[Extensions]` banner section and the `ctrl+o` hint
    (advertised, unwired). Refs: `06:F3`, `06:F11`.

### Track CQ-B: Provider layer consolidation

Twelve adapters total ~7 KLoC for four wire shapes (OpenAI Responses,
OpenAI Chat Completions, Anthropic Messages, Gemini `generateContent`).
The bulk is mechanical duplication. pi-mono's
`openai-responses-shared.ts` is the model.

1. Extract a single `pipy_harness.native.http` module owning the
   `JsonHTTPClient` / `UrllibJsonHTTPClient` boundary and the four-class
   exception hierarchy. Delete the per-provider copies. Refs:
   `02:F1`, `02:F2`. **Landed (migration Slice 5.1):**
   `native.http` owns the transport boundary — the
   `JsonHTTPClient`/`JsonResponse` contract, cancellable request
   execution (`open_url_cancellable`/`urlopen_read_cancellable` plus the
   connection-registration machinery), JSON body decoding, HTTP-error
   metadata, and safe usage-field extraction — plus the shared
   `UrllibJsonHTTPClient` and the shared `ProviderHTTPError` base with its
   declarative `from_http_error`/`ApiErrorField` normalization. The eight
   plain-JSON adapters (`openai`, `openai_completions`, `mistral`,
   `openrouter`, `cloudflare`, `azure_openai`, `google`, `google_vertex`)
   now reparent their named error types as thin subclasses and build the
   shared client via a `<provider>_http_client()` factory; their
   `UrllibJsonHTTPClient`/`from_http_error`/`_decode_json_object`/`_extract_usage`
   copies are deleted. Anthropic and Bedrock have since folded onto the same
   shared `UrllibJsonHTTPClient` and `ProviderHTTPError` base via
   `anthropic_http_client()`/`bedrock_http_client()`; SigV4 signing still runs
   in the Bedrock adapter before the shared client sends, and
   `BedrockHTTPStatusError` keeps its own `from_http_error` (its error envelope
   is a top-level `message`/`__type` shape, not the shared nested-`error`
   shape). Codex has since reparented `OpenAICodexProviderError` onto the shared
   `ProviderHTTPError` base and reuses the shared `iter_sse_event_payloads` SSE
   line-framer, `transport_exception_retryable` network-exception classifier,
   and `extract_responses_usage` (its `_iter_sse_stream`,
   `_transport_exception_retryable`/`_RETRYABLE_TRANSPORT_ERRNOS`, and
   `_extract_usage` copies are deleted); it keeps its own OAuth/WebSocket
   transports, HTTP-status normalizer, domain retry classifier, and
   retry/fallback loop. The Gemini `generateContent` adapter has since moved
   verbatim to `pipy_harness.native.providers.google_generative_ai` (migration
   Slice 5.2-gemini, cut 1), translation-only over the shared `native.http`
   primitives, keeping `GoogleGenerativeAIProvider`, `google_http_client()`, the
   `GoogleProviderError` hierarchy, the URL-embedded `?key=` auth, the per-model
   `generationConfig.thinkingConfig` shape, and `GOOGLE_USAGE_FIELDS`
   byte-for-byte; its Vertex sibling has since moved verbatim to
   `pipy_harness.native.providers.google_vertex` (migration Slice 5.2-gemini,
   cut 2), keeping `GoogleVertexProvider`, `google_vertex_http_client()`, the
   `GoogleVertexProviderError` hierarchy, the Express-vs-ADC auth switch, the
   regional/Express endpoint templates, the `vertex_auth_mode`/
   `google_cloud_location` metadata, and the per-model
   `generationConfig.thinkingConfig` shape byte-for-byte. The byte-identical
   Gemini `generateContent` wire translation both adapters duplicated has since
   been consolidated into `pipy_harness.native.providers`
   `.google_generate_content_wire` (migration Slice 5.2-gemini, cut 3),
   owning `gemini_contents`/`envelope_to_content`/`serialize_tool_for_gemini`/
   `parse_response`/`extract_final_text`/`extract_tool_calls` and the shared
   `ParsedGeminiResponse`, parameterized only by parse-error class, response
   label, usage-field tuple, tool-call correlation prefix, and the Google-only
   `attach_images` flag; each adapter is now a thin auth/URL/thinking shell and
   the two per-adapter thinking-config mappings stay divergent.
2. Extract a shared Chat-Completions wire translator for the Chat-Completions
   wire shape. Collapse OpenAI-Completions, OpenRouter, Mistral, and Cloudflare
   onto it. Refs: `02:F3`. **Done (migration Slice 5.2-chat, cuts 1–4):**
   the canonical OpenAI-compatible Chat Completions adapter moved verbatim to
   `pipy_harness.native.providers.openai_completions` (with its ds4 reuse in
   `pipy_harness.native.providers.ds4`) in cut 1, the Mistral and OpenRouter
   clones moved verbatim to `pipy_harness.native.providers.mistral` and
   `pipy_harness.native.providers.openrouter` in cut 2, and the Cloudflare
   Workers AI adapter moved verbatim to
   `pipy_harness.native.providers.cloudflare` in cut 3 — all translation-only
   over the shared `native.http` primitives; ds4 keeps its own endpoint
   normalization and its divergent transport-vs-status labelling, Mistral keeps
   its `reasoning_effort` passthrough, OpenRouter keeps its nested `reasoning`
   normalization, and Cloudflare keeps its `{account_id}` base-URL template
   resolution. Cut 4 then consolidated the byte-identical wire helpers into
   `pipy_harness.native.providers.chat_completions_wire`, which now owns
   `chat_messages` / `parse_response` / the `ParsedChatCompletion` result,
   parameterized only on each adapter's parse-error class, response label,
   tool-call provider prefix, and usage-field remap; the four adapters (ds4 via
   `OpenAIChatCompletionsProvider`) are thin auth/URL + dataclass shells over it
   with their duplicate `_chat_messages`/`_parse_response`/`Parsed*Response`
   copies deleted, and the four provider dataclasses and their separate error
   hierarchies stay unmerged.
3. Extract a shared OpenAI Responses wire-shape translator and collapse
   the copies. **Done for the two relocated Responses adapters:**
   `pipy_harness.native.providers.openai_responses_wire` now owns the
   `responses_input` / `envelope_to_input_items` / `parse_response` /
   `extract_final_text` / `ParsedResponse` translation, parameterized on the
   OpenAI-only deferred-tools/attachment extension, the per-provider
   parse-error class, the response label, the nested-usage field tuple, and the
   tool-call provider prefix; `providers.openai_responses` and
   `providers.azure_openai_responses` are thin auth/URL + dataclass shells over
   it (their duplicate copies deleted). Remaining: fold the
   `openai_codex_provider` Responses/SSE path onto the same translator once its
   streaming contract is characterized. Refs: `02:F16`.
4. Extract a shared Anthropic Messages wire-shape translator and
   collapse Anthropic + Bedrock onto it. Refs: `02:F4`. **Done (migration
   Slice 5.2-anthropic, cuts 1–3).** Cut 1 moved the Anthropic Messages adapter
   verbatim to `pipy_harness.native.providers.anthropic_messages`,
   translation-only over the shared `native.http` primitives, keeping
   `AnthropicProvider`, the `anthropic_http_client()` factory,
   `ANTHROPIC_MESSAGES_URL`, the thinking-budget/adaptive constants, and
   `supports_adaptive_thinking`; cut 2 moved the Bedrock InvokeModel adapter
   verbatim to `pipy_harness.native.providers.bedrock`, keeping
   `AmazonBedrockProvider`, `bedrock_http_client()`,
   `BedrockHTTPStatusError`/`BedrockAuthError`, the region-templated endpoint,
   `anthropic_version` envelope, env-resolved credentials, and the pure-stdlib
   `_sigv4_sign` chain byte-for-byte. Cut 3 then consolidated the byte-identical
   wire helpers into `pipy_harness.native.providers.anthropic_messages_wire`,
   which now owns `messages_payload`/`envelope_to_message`/`convert_tool_result`/
   `parse_response`/`extract_final_text`/`extract_tool_calls` and the shared
   `ParsedAnthropicMessagesResponse` result, parameterized only by each adapter's
   parse-error class, response label, tool-call provider prefix, and the
   Anthropic-only message extensions Bedrock omits (tool-result coalescing,
   deferred `tool_reference` emission, and image attachment); the two provider
   dataclasses and their separate error hierarchies stay unmerged, and SigV4
   signing still runs in the Bedrock adapter before the shared client sends.
5. Move `_safe_response_label`, `_extract_usage`, and `_utc_now` into
   the shared http/parsing modules. Delete the per-provider copies.
   Refs: `02:F2`, `02:F13`, `02:F20`.
6. Wire `pipy_harness.native.retry.retry_with_policy` into every real
   provider HTTP entry point. Today it is well-tested but used by one
   of nine real providers. Refs: `02:F7`, `07:F15`.
7. Decide the streaming contract. Either (a) implement
   `StreamChunkSink` and `ReasoningSink` in every adapter that the
   protocol claims to support, or (b) remove the sink parameters from
   `ProviderPort.complete` and re-introduce them per-provider when a
   real streaming path exists. Today 10 of 11 real adapters accept
   them and immediately `del` them. Refs: `02:F6`.
8. Introduce a per-model registry module
   (`pipy_harness.native.model_registry`) that owns `max_tokens`,
   `supports_tool_calls`, `default_temperature`, default `max_tokens`,
   and reasoning-effort support per `(provider, model)`. Stop
   hardcoding `max_tokens=4096` across all Anthropic models, stop
   hard-coding `gpt-5.5` and `gpt-5.1-codex` as defaults, and let
   `Cloudflare` only advertise `supports_tool_calls=True` for models
   that actually support function calling. Refs: `02:F8`, `02:F9`,
   `02:F17`, `07:F9`. **Foundation (migration Slice 5.3a):** the
   `ModelRuntime` construction/spec owner now composes `ProviderCatalogState`
   (the merged built-in + `models.json` + auth catalog) with the
   `provider_construction` boundary as the single owner of spec resolution and
   provider construction, extracted out of `NativeReplProviderState` (which
   holds a typed `model_runtime` and delegates). **Foundation (migration Slice
   5.3b):** `ModelRuntime.construct` is now total — the by-name legacy provider
   factory (`_native_provider_for_selection`/`_provider_factory_for`/the
   `NativeProviderFactory` protocol) is deleted and every selection (including
   `openai-codex`, `fake`, and the bare built-in `ds4`) is built through
   `provider_construction`, threading the settings-derived `ConstructionOptions`.
   This gives the Phase 5.3 catalog/model-facts consolidation one construction
   owner to build on. **Foundation (migration Slice 5.3c):**
   `NativeReplProviderState.model_runtime` is now required (no longer `| None`),
   so model listing, selection, availability, and thinking-level cycling flow
   solely through the catalog-backed `ModelRuntime`; the legacy
   one-default-per-provider registry branches and the
   `_provider_available`/`_provider_unavailable_message`/`_resolve_model_reference`
   helpers are deleted, with `ProviderCatalogState.provider_available`
   (resolving through `auth_store.provider_available`, Pi's `hasAuth`, plus the
   `fake`/`openai-codex`/extension-OAuth special-cases) remaining the catalog's
   single availability owner and `native_provider_available` staying as the
   deliberately separate startup auto-default env probe (reached only via
   `repl_state._provider_available_in_env`). The per-model capability/default de-hardcoding above
   is still open. **Phase 5 accepted (2026-07-22):** all five Phase 5 acceptance
   criteria are met (see the migration doc's "Phase 5 acceptance closure"), and
   the fresh Pi-head audit note there records the later Pi feature gaps —
   remote/generated catalog refresh plus Pi's reset-of-dynamic-registrations
   semantics (the `refresh()`/dynamic-registration mechanism itself is already
   shipped and wired on `/reload`), extension-provider OAuth resolution on the
   request/construction path (`get_api_key`/`refresh_token`; the registration
   surface and `/login` are already implemented), Chat Completions (Kimi)
   deferred tools, and local-model routing — as explicit future work that was
   deliberately kept out of the consolidation commits, not shipped behavior.
9. Fix `GoogleProvider` / `GoogleVertexProvider` tool-call id
   fabrication: stop synthesizing ids from loop index and propagate the
   real id from the response. Refs: `02:F5`.
10. Fix the Codex OAuth refresh path so a refreshed token that omits
    `account_id` is rejected at refresh time, not the next request.
    Refs: `02:F10`.

### Track CQ-C: Bad-state-impossible refactors

Ronacher's rule applied directly: where pipy currently *handles* a bad
state, redesign the type so the bad state cannot exist.

1. Replace `ProviderResult` with a discriminated union (or
   factory-only constructors) so `SUCCEEDED` cannot carry an
   `error_message`, `PENDING` cannot appear from a completed call,
   `FAILED` cannot carry `tool_calls`. Delete the `__post_init__`
   guards. Refs: `07:F1`.
2. Replace `NativeToolSandboxPolicy` / `NativeToolApprovalPolicy`
   with mode-tagged value objects whose fields cannot be set
   incoherently (e.g. `NO_WORKSPACE_ACCESS` cannot have
   `workspace_read_allowed=True`). Refs: `07:F2`.
3. Close the metadata-key universe: convert the 27 frozensets of
   string keys in `models.py` into `Literal[...]` types or enums.
   Make the archive-safe allowlist a type, not a runtime check. Refs:
   `07:F6`.
4. Make `NativeRunInput.system_prompt_id` / `system_prompt_version`
   `Literal[...]` once they have one production value each. Refs:
   `07:F3`.
5. Validate `NativeToolRequest.tool_kind` against a closed enum at
   construction; do not accept `str`. Refs: `07:F4`.
6. Remove the nine "always False" storage booleans from
   `NativeTurnMetadata.archive_payload()` and reflect the actual policy
   in the type instead. Refs: `07:F5`.
7. Replace `recorder.finalize_session` recovery branch with a
   constructor-time check that finalize cannot be called twice or on
   an already-renamed directory. Today the recovery branch *handles*
   the case. Refs: `05:F1`.
8. Make `recorder.append_event` refuse appends to a finalized record
   structurally (no `.in-progress/pipy` path → no append), instead of
   relying on the caller not to call. Refs: `05:F2`.
9. Fix `_unique_path` so the canonical filename round-trips through
   `FILENAME_RE` instead of being mangled. The "uniqueness" suffix
   should not break the format. Refs: `05:F3`.
10. Stop swallowing every `(OSError, UnicodeError,
    json.JSONDecodeError)` in catalog readers. A finalized record that
    will not parse is a recorder bug, not a catalog edge case. Refs:
    `05:F7`. The downstream `verify_session_archive` surface that
    exists to compensate for this can then be deleted (already in
    Track CQ-A).
11. Bind `NativeVerificationRequest.command_label` to a closed enum;
    do not accept arbitrary 80-char strings into a "safe label"
    position. Refs: `07:F16`.
12. Replace the free-form `request_source == "pipy-owned-human-reviewed"`
    check in `NativePatchApplyRequest` with a discriminator enum that
    only the pipy-owned construction site can produce. Refs: `07:F22`.

### Track CQ-D: Structural simplification

Collapse the parallel families.

1. Collapse `NativeAgentSession`, `NativeNoToolReplSession`, and
   `NativeToolReplSession` into one session driven by an explicit
   state machine. **Superseded by the 2026-06-20 cleanup:** the no-tool REPL
   and its shadow `/read`/`/ask-file`/`/propose-file`/`/apply-proposal`
   commands were removed rather than rerouted. Refs: `01:F3`, `01:F2`.
2. Replace the 350-line `if/elif` REPL command-dispatch chain in
   `session.py` with a command table (name → handler + descriptor).
   The chrome menu, the help printer, and the dispatcher all read the
   same table. Refs: `01:F4`.
3. Centralize the slash-menu / readline / prompt-toolkit / plain
   adapters behind one input port and remove the hand-rolled ANSI
   cursor logic from the slash-menu adapter (~280 L). Refs:
   `01:F18`, `01:F19`.
4. Collapse `PipyNativeAdapter`, `PipyNativeReplAdapter`, and
   `PipyNativeToolReplAdapter` into one adapter parameterized by
   `RunMode` (one-shot / no-tool-repl / tool-loop-repl). They already
   share the name `pipy-native`. Refs: `04:F1`.
5. Decide whether `AgentPort` is real polymorphism (subprocess +
   native + future) or a phantom protocol with one consumer. If
   phantom: inline. If real: write the missing consumer (e.g. a
   real RPC adapter) before the protocol is allowed to stay. Refs:
   `04:F2`.
6. Split `pipy_session.catalog` (1,179 L) into focused modules:
   `catalog/list.py`, `catalog/search.py`, `catalog/inspect.py`. Drop
   `verify` and `reflect` per Track CQ-A. Refs: `05:F4`.
7. Split `pipy_harness.native.read_only_tool` (715 L) into the
   archive-safe one-call boundary it claims to be plus a separate
   path-validation helpers module shared with the model-driven tools.
   Refs: `03:F2`.
8. Resolved: `NativeConversationState.MAX_TURNS` was lifted from 8 to 256
   (with matching turn-identity bounds), so the interactive REPL no longer
   refuses ordinary sessions at eight turns and compaction has room to run.
   Refs: `07:F7`.
9. Consolidate the 13-way provider switch in `repl_state.py` (four
   copies) into one provider-descriptor table that owns every
   per-provider fact. Refs: `02:F19`, `07:F8`.
10. Resolve the dual `ToolPort` Protocol name clash by giving the
    archive-safe variant a distinct name (e.g. `ArchiveToolPort`) or
    by deleting the archive-side family per Track CQ-A slice 10.
    Refs: `03:F1`.

### Track CQ-E: Plausible-but-wrong correctness fixes

Concrete bugs surfaced by the audits. Each warrants a focused test.

1. Resolved: `session.finalized` is appended only after `recorder.finalize()`
   returns and remains the last lifecycle event in finalized JSONL records.
   Refs: `04:F7`, `04:F8`.
2. Resolve the chrome banner / loader path disagreement. Pick one
   canonical layout for global resources (currently chrome says
   `~/.pipy/...`, loader says `~/.config/pipy/...`) and one for
   workspace resources (chrome says `.pipy/commands` for prompts,
   loader says `.pipy/templates`). Refs: `06:F4`, `06:F5`, `06:F6`,
   `06:F7`.
3. Stop printing `final_text` to `sys.stdout` from inside
   `PipyNativeAdapter.run`. The adapter does not own stdout. Refs:
   `04:F21`.
4. Resolved: `_resolve_repl_mode` now uses catalog/provider-registry metadata
   (plus the automation fake exception) instead of constructing a live provider
   just to read `supports_tool_calls`. Refs: `04:F9`.
5. Resolved: the old ambiguous `harness.run.failed` event was split into
   `harness.run.adapter_failed` (adapter returned failed status) and
   `harness.run.exception` (exception escaped prepare/run). Refs: `04:F16`.
6. Fix `_resource_files.discover_resource_files`: enforce the workspace
   byte cap *before* `_read_capped_bytes` streams the whole file, not
   after. Today an over-cap file is read fully and then discarded.
   Refs: `06:F14`.
7. Fix `_resource_files._path_label_for` so symlink resolution does
   not lose the workspace prefix. Refs: `06:F15`.
8. Resolved: `_load_first_candidate` now continues past a seen candidate and
   can load the next candidate in the same directory. Refs: `06:F16`.
9. Tighten the two competing secret detectors (`looks_sensitive`
   substring vs `has_secret_shaped_content` regex) into one helper
   with one definition. Apply at one layer. Refs: `03:F9`.

### Track CQ-F: Deduplication

Remove copy-paste that the type system could have caught.

1. Deduplicate `_safe_component`, `_filename_stamp`,
   `_looks_sensitive`, and `_redacted_argv` between
   `pipy_harness.capture` and `pipy_session.auto_capture` (~80 L).
   Refs: `05:F16`.
2. Deduplicate the three near-identical `_validate_safe_label` /
   `_validate_scope_label` helpers. Refs: `07:F24`.
3. Replace the six identical footer-repaint call sites with one
   `_redraw_footer()` helper that reads from a single source of
   truth. Refs: `01:F5`.
4. Replace `_final_status` / `_native_error_type` /
   `_native_error_message` with one dispatcher that returns the
   tuple. Refs: `01:F17`.
5. Stop swallowing `ValueError` on `ProviderToolCall` construction
   in 11 places. Move the construction into a single helper. Refs:
   `02:F12`.
6. Consolidate the chrome status block's overloaded
   `context_budget_suffix` (currently encoding two distinct facts in
   one field). Refs: `06:F20`.
7. Delete duplicate byte-cap checks in `discover_resource_files`
   (three checks for the same value, one structurally unreachable).
   Refs: `06:F12`.
8. Consolidate the copies of `_extract_usage` into the shared
   provider module (Track CQ-B). Refs: `02:F13`. **Landed
   (migration Slice 5.1):** the eight plain-JSON adapters use the
   shared `native.http` `extract_usage_from_fields`/`extract_responses_usage`
   helpers, `anthropic`/`bedrock` share `native.http.extract_anthropic_usage`
   (the identical total-synthesizing Anthropic Messages usage extractor both
   adapters had already converged on), and `openai_codex` now calls the shared
   `extract_responses_usage(usage, OPENAI_CODEX_NESTED_USAGE_FIELDS)` — no
   provider keeps a local `_extract_usage`.

### Out Of Scope For This Track

These remain explicitly deferred and are not slices of the audit
track:

- Rewriting pipy in TypeScript or porting pi-mono's TUI library.
- Adding pydantic, typebox, jsonschema, attrs, or another validation/typing
  runtime dependency during extraction. A later dependency change requires the
  separate ADR gate in the Architecture Migration Plan, recorded under
  `docs/decisions/YYYY-MM-DD-<slug>.md` and independently reviewed.
- Adding unrelated product features while performing an architectural
  extraction. RPC mode, the inline product TUI, persistent history, and
  extension/package loading now ship and are protected migration inputs, not
  deferred work. This does not authorize an alternate-screen or Textual UI.
- Adding product-level multi-agent orchestration. Using bounded coding subagents
  to implement and review this migration does not add a multi-agent product
  surface.
- Re-introducing dead modules removed in Track CQ-A as a "future"
  hedge. They come back only when a runtime path consumes them.
- Touching the public archive privacy invariants. The audit's bad-
  state fixes (Track CQ-C) tighten them; they never relax them.

### Cross-cutting reminders

- Every slice in this track ships with a focused test, a green
  `just check`, an updated `docs/architecture.md` codebase map row if
  a file moves or disappears, and a conventional commit.
- Slice ordering is a recommendation, not a constraint. Pick the
  highest-leverage slice from any track that fits the next review
  cycle.
- Each audit finding cites file:line in the audit file. The audit
  files are the authoritative detail; this section is the planning
  index.

## Done

Historical done ledger preserved for documentation-contract tests:
Native inert read-only tool request value objects.
Native explicit file excerpt read-only tool implementation.
Native provider-visible repo context policy.
Native bounded read-only tool observation into follow-up provider turn.
file excerpts, proposal drafts, patch text, verification output.
Native approval and sandbox enforcement baseline; Native inert read-only tool
request value objects; Native explicit file excerpt read-only tool
implementation; OpenAI subscription-backed native auth decision
`blocked-for-now` on 2026-05-07 because unsupported credential scraping and
CLI/product wrapping are rejected; Native OpenRouter Chat Completions provider
with `--native-provider openrouter --native-model <provider/model>` and
`OPENROUTER_API_KEY`; Native bounded post-tool provider turn against synthetic
sanitized observations; Native bounded read-only tool observation into
follow-up provider turn; Native patch proposal boundary before writes; Native
provider-visible repo context policy; Native supervised patch apply boundary
using NativePatchApplyRequest and native.patch.apply.recorded; Native
allowlisted verification-command boundary using NativeVerificationRequest and
native.verification.recorded; First supervised self-bootstrap trial
implementation as a test-only trial; First supervised self-bootstrap review;
Product-direction checkpoint after first native smoke test toward a Pi-like
native shell.

Native conversation state and bounded provider-turn loop foundation:
pipy_harness.native.conversation, metadata-only per-turn payloads, Native
one-shot run rebased on conversation state, provider turn indexes and labels,
per-run in-memory native conversation identity/state. Native minimal no-tool
REPL: `pipy repl --agent pipy-native`, `no_tool_repl`. Native visible approval
and sandbox prompt foundation: stream-based approval resolver and attempted
capability escalation. Native interactive read-only REPL command behind the
prompt gate: `/read <workspace-relative-path>` records only metadata-only tool
lifecycle events. Native explicit provider-visible `/ask-file` REPL boundary:
`/ask-file <workspace-relative-path> -- <question>` labeled `ask_file_repl`.
Native `/ask-file` smoke and separator hardening used a whitespace-delimited
`--` separator; OpenRouter smoke was skipped. Native REPL command help and
usage diagnostics added local `/help` command and unsupported slash commands;
Native REPL command help and usage diagnostics review second review reported
no findings and All four were accepted and fixed.

Native REPL next-boundary decision selected a proposal-only
`/propose-file <workspace-relative-path> -- <change-request>` path. No runtime
behavior changed. Native proposal-only `/propose-file` REPL boundary now
accepts `/propose-file <workspace-relative-path> -- <change-request>` labeled
`propose_file_repl`. Native proposal-only `/propose-file` review and smoke:
fake-provider terminal smoke; No implementation hardening was required. Native
REPL next-boundary decision after proposal-only review selected a human-applied
proposal trial and public REPL stays proposal-only. OpenAI Codex OAuth provider
correction from Pi reference selected a distinct `openai-codex` provider path
using packages/ai/src/utils/oauth/openai-codex.ts and
packages/ai/src/providers/openai-codex-responses.ts at
https://chatgpt.com/backend-api/codex/responses. Pi-like no-approval shell
direction correction: No permission popups, packages/coding-agent/src/core/tools/read.ts.
Native REPL approval prompt removal uses `not-required` approval policy data
and is no longer wired into the normal product REPL path.

Native `openai-codex` OAuth provider from Pi reference:
`--native-provider openai-codex --native-model <model>`,
`pipy auth openai-codex login`,
`${PIPY_AUTH_DIR:-~/.local/state/pipy/auth}/openai-codex.json`. Native OpenAI
Codex provider SSE transport correction: SSE Responses request with
`stream: true` to `https://chatgpt.com/backend-api/codex/responses`. Native
REPL auth/model commands and late-bound provider selection: `pipy` now starts
the native REPL; `/login [openai-codex]`, `/logout [openai-codex]`; model
selection is resolved before each provider-visible turn. Native human-applied
`/propose-file` trial through shell auth/model commands used
`/model openai-codex/gpt-5.2`, secret_looking_content, and was useful enough
to justify a narrow write-capable boundary design slice.

Native one-file `/apply-proposal` REPL command:
/apply-proposal <workspace-relative-path>, same-session `/propose-file`,
NativePatchApplyRequest, native.patch.apply.recorded. Native REPL `/verify
just-check` command: NativeVerificationRequest, native.verification.recorded.
Native REPL `/verify just-check` review and smoke: Fake-provider terminal smoke
runs exercised propose/apply/verify success; `pipy-session verify`, `list`,
`search`, and `inspect` remained compatible. Native first pipy-applied,
pipy-verified tiny change: 2026-05-11, `openai-codex/gpt-5.2`,
`/propose-file pyproject.toml -- <change-request>`,
`/apply-proposal pyproject.toml`, `/verify just-check`,
`native-self-bootstrap-trial`, no runtime dependencies are declared.

Native next-boundary decision after the first self-bootstrap trial:
summary-safe inspection of the finalized `native-self-bootstrap-trial`; The
selected next boundary is therefore a failed-read recovery slice. Native
bounded read-failure recovery for explicit REPL file commands: one failed or
skipped read attempt can happen before that successful excerpt; Archive
payloads remain metadata-only and add only safe budget booleans. Native
bounded read-failure recovery review and smoke: split-budget implementation
aligned with the selected contract; local `/help`, `/model`, `/apply-proposal`,
and `/verify just-check`; fake-provider REPL smoke exercised failed-read
recovery.

Native no-tool REPL conversation-context decision after read-failure recovery
review selected bounded in-memory context for ordinary no-tool REPL turns under
explicit turn and byte limits. File excerpts, proposal drafts, patch text,
verification output are excluded. The decision slice changed no runtime
behavior. Native bounded no-tool REPL conversation context:
`NativeNoToolReplConversationContext`, 4 KiB provider-visible byte budget,
clears on login, logout, provider/model changes; raw prompts, provider final
text, excerpts are not archived. Native bounded no-tool REPL conversation
context review and smoke: two-round independent review cycle, second round
reported zero findings, implementer-side closeout audit, fake-provider REPL
smoke with two ordinary turns. The next selected native-shell boundary is a
local `/clear` command. **Retired (Track CQ-A, migration Slice 7.1, 2026-07-23):**
the no-tool REPL and this in-memory conversation-context feature were removed by
the 2026-06-20 parity cleanup and the Phase 7.1 dead-code deletion; the
`NativeNoToolReplConversationContext`/`NativeNoToolReplExchange` types, their
`NATIVE_NO_TOOL_REPL_CONTEXT_*` constants, and the
`ProviderRequest.no_tool_repl_context` plumbing no longer exist. The historical
behavior record is preserved and test-pinned; the paragraph above stays as the
audit trail only.

Native local `/clear` REPL command now accepts `/clear` as a local command;
malformed `/clear <text>` stays local and does not clear history; does not
reset provider/model selection, auth state, read budgets. Native local
`/clear` review and smoke: two-round independent review cycle, two
suggestion-level test coverage items, both were accepted and fixed,
post-clear verification availability coverage, second review found no
findings, fake-provider `/clear` REPL smoke. Native next-boundary decision
after `/clear` review and smoke. Native next-boundary decision after
`/clear`: summary-safe archive reflection found the `/clear`
implementation review cycle clean; The selected next boundary is a local
`/status` REPL command. This decision slice changed no runtime behavior.
Native local `/status` REPL command now accepts `/status` as a local command;
pending proposal availability, and verification availability; archive raw
command text remains forbidden.

Native next-boundary decision after `/status` selected next boundary is
Pi-like REPL startup chrome. This is a user-facing shell ergonomics slice.
Native Pi-like REPL startup chrome: bare `pipy` and `pipy repl --agent
pipy-native` now print compact chrome derived from the same safe display state
used by `/status`. Native next-boundary decision after startup chrome selected
next boundary is a Pi-like visual/resource-label pass. Native Pi-like startup
visual/resource-label pass: ANSI title/section/dim styling only for suitable
TTY streams and existence-level workspace-relative resource source labels.
Local Zensical documentation preview/build: `just docs-serve` starts the local
preview server; `just docs-build` builds the static site; Zensical is a
dev/tooling dependency only.

Native grouped slash-command discovery: one stable grouped command reference
on stderr for controls, local state, provider/model, file context, proposal.
Native post-help input ergonomics decision selected one more line-oriented
implementation boundary. state-aware prompt label before each input. Native
line-oriented state-aware prompt label replace the fixed prompt with a compact
stderr prompt label. Native terminal-layer direction checkpoint selected a
narrow `prompt-toolkit` line-editor adapter investigation; Textual was judged
too application-like; current plain line-oriented runtime as the required
fallback. Native prompt-toolkit line-editor feasibility boundary:
`NativeNoToolReplSession` now reads input through a small internal adapter;
`--input-runtime plain|prompt-toolkit|auto`; safe `input_runtime` label.

Native prompt-toolkit slash-command completion boundary: leading slash-command
completer; Prompt-toolkit remains an optional opportunistic line-editor path;
Focused tests cover the attached completer. Native prompt-toolkit file/path
completion boundary suggests existing workspace-relative path labels and
command handlers remain the source of truth. Native prompt-toolkit multiline
input boundary: Enter submits the current buffer; Esc+Enter inserts a newline.
Native prompt-toolkit bottom-toolbar status decision: defer bottom-toolbar
behavior. real-TTY prompt-toolkit hardening pass found an async completion
protocol compatibility gap. Native prompt-toolkit real-TTY input hardening
disables prompt-toolkit cursor-position requests and handles CR and LF terminal
encodings.

Native prompt-toolkit next-boundary decision after real-TTY hardening selected
prompt-toolkit-only `@file` reference completion. Completion-only. Resilient
resize behavior was rejected. Persistent history was rejected. Bottom-toolbar
behavior remains deferred. Native prompt-toolkit `@file` reference completion
boundary suggests safe workspace-relative `@file` labels. Accepting a
completion inserts only text and does not read files, attach context, invoke
providers or tools. Native next-boundary decision after `@file` completion
selected a narrow explicit multi-file context budget: two successful
workspace-relative excerpts per REPL session. Automatic `@file` reads,
model-selected paths remained deferred at that point. Native provider-visible
repo context policy is complete. (Update: user-directed `@file` context has
since shipped — a submitted prompt's `@path` references load bounded excerpts
through the shared bounded reader in the product REPL and product TUI.)

Native tool-loop TUI shell: real-TTY tool-loop sessions now use a pipy-owned
alternate-screen terminal UI with retained startup/context rows, submitted
prompt bands, active assistant output, transient working state, compact shaded
model-selected tool rows, footer/status pinning, slash-menu input behavior, and
active provider-turn Escape that renders red `Operation aborted` while
suppressing late chunks. The product TUI slash menu now lists only executable
local tool-loop commands (`help`, `exit`, `quit`). The slice shipped with
stdlib ANSI screen-cell verification, tmux product-path artifacts, Pi comparison
artifacts, focused TUI/renderer tests, docs updates, `just check`, and a clean
second review after the inert-command menu finding was fixed. (This shell was
later reworked into the inline-scrollback model with full-height use, native
scrolling, the `/copy` command, and the interactive `/model` selector — see Pi
Gap Queue items 2 and 3 above for the current behavior; the menu now lists
`help`, `model`, `settings`, `copy`, `exit`, `quit`.)

## Next Slice (historical architecture-migration marker; completed)

The heading is retained because executable historical backlog contracts use it
as a section boundary. The entries in this section are the former migration
queue. They are preserved
for auditability and do **not** identify the current implementation target; use
`Completed Reviewed Program` above.

### Architecture migration baseline — SHIPPED (2026-07-17)

Phase 0.1 of the reviewed
[Architecture Migration Plan](architecture-migration.md) investigated the
historical order/global-state failure exposed by
`tests/test_parity_probe_trust.py::test_legacy_parity_score_opts_into_trusted_workspace_fixtures`,
but the isolated probe, actual predecessor order, reverse order, and repeated
full suites were green; no concrete leak justified a retry or synthetic test
patch. Checked-in CI now uses the repository's `just` entry points for
lint/types/docs on Python 3.14, full tests on Python 3.11 and 3.14, and an
explicit real-PTY smoke recipe on named Linux and macOS jobs. The existing
Ruff-format debt remains a separate mechanical normalization slice rather than
being hidden in this baseline.

### Architecture characterization contracts — SHIPPED (2026-07-18)

Phase 0.2 now freezes current provider/tool/error/cancellation/queue/extension/
JSON/RPC/SDK event and lifecycle boundaries, proves raw native product-session
content cannot cross into the metadata-only workflow archive, and checks planned
dependency directions without importing runtime modules. The import harness
activates for both module-first and package-first layer migrations and fails
closed on stale forbidden names. Existing specialized retry, tool-progress,
PTY/TUI, and extension-hook tests remain the detailed contracts for those
surfaces.

### Canonical typed agent events — SHIPPED (2026-07-18)

Phase 1.1 adds the side-effect-free `native.agent` contract package: immutable,
runtime-validated messages and events; provider/pipy tool identity; normalized
usage, failure, turn, cancellation, and run outcomes; redacted full-content
payload representations; and a synchronous ordered sink. The strengthened
dependency gate keeps this core independent of outer adapters, automation,
extensions, UI, persistence, providers, the runner, and the workflow archive.
No current mode or public wire format changed.

### Canonical event adapters — SHIPPED (2026-07-18)

Implement Phase 1.2 from the reviewed
[Architecture Migration Plan](architecture-migration.md): project typed agent
events into the existing Pi-shaped automation dictionaries, extension lifecycle,
rendering, SDK, and persistence boundaries without changing JSON/RPC snapshots or
callback order. Keep cumulative assistant partials, malformed argument parsing,
and camelCase fields in the Pi adapter; keep RPC queue/idle transitions on its
existing serialized boundary. Replace the legacy tool-loop message envelope
atomically rather than leaving aliases, and add no new public records for
reasoning, usage, cancellation, or provider failures unless explicitly planned.

Delivered as one atomic cutover because the conversation envelope and event
producer are one connected graph. The tool loop and one-shot SDK emit canonical
events through fixed-order synchronous projections for automation, extensions,
rendering, SDK callbacks, metadata-only workflow counts, and the defined future
product-session subscriber. JSON/RPC/session/SDK formats and callback order stay
stable; RPC still owns queue reservation and `agent_settled`, while direct
product-session writes remain in place until Phase 3.3. The superseded
`native.tools.messages` envelope, exports, and `AutomationEmitter` are deleted.
The Phase 1.2 consumer cutover is followed by the shipped reusable tool
executor and the active provider-turn boundary.

### Reusable tool executor — SHIPPED (2026-07-19)

Phase 2.1 extracts the synchronous per-call tool path from
`NativeToolReplSession` into UI-free `native.agent.tools`. The executor owns
lookup, JSON/schema validation, pipy request identity, invocation, live
`ToolContext` output, normalized canonical observations, exact malformed/error
mapping, and a closed settled/operator-abort/local-command result. The session
adapts terminal wait outcomes and retains budgets, extension hooks, timing,
event ordering, malformed-fatal policy, provider history, persistence, and
run/turn outcomes. Cancellation is ordered against worker completion, and a
per-invocation live-output gate prevents an abandoned worker from emitting into
the next call. `native.tools` is contract-only; concrete implementations are
imported from their defining modules. Caller scheduling remains sequential and
no parallel-tool scheduler is introduced; an uncooperative cancelled worker may
outlive its bounded join with new output admissions closed. An already admitted
backpressured callback may finish later, still bound to the original turn and call.
Parallel tools, richer
termination, provider-turn extraction, and async conversion stay deferred.

The private `_invoke`, `_invoke_interruptible`, and `_error_observation` paths
were removed in the same slice. Direct executor characterization, tool-loop,
extension, rendering, bash, TUI, and explicit canonical import-boundary tests
gate commit `5348127` (`refactor: extract reusable tool executor`). The next
ordered migration boundary is Phase 2.2a, one provider-turn completion.

### Provider-turn executor — SHIPPED (2026-07-19)

Phase 2.2a extracts one synchronous provider completion into UI-free
`native.agent.provider_turn.ProviderTurnExecutor`. It owns canonical text/reasoning delta
publication, the optional worker and `CancelToken`, exact cancellation versus
completion ordering, bounded cleanup, a late-delta admission gate, and a typed
provider-result or closed cancellation-reason outcome. `NativeToolReplSession`
adapts the current TUI and RPC/external-abort wait policies and retains queue
storage/promotion, provider-request construction, extensions, tool cycles and
budgets, plus the current zero-retry and usage-accumulation policy.

The superseded `_ProviderTurnCompletion`, `_agent_text_sink`,
`_agent_reasoning_sink`, `_complete_headless_cancellable_turn`,
`_complete_provider_turn`, and `_cancel_active_turn` paths are deleted. Fresh
process and recursive import gates keep the new module free of UI/TUI,
automation, extensions, product-session and compaction code, provider
construction and concrete transports, capture, and the metadata workflow
archive; `native.agent` does not eagerly re-export the executor. The extraction
uses the dependency-neutral canonical `pipy_harness.status.HarnessStatus` so
public harness/native model exports retain their runtime enum identity and
resolvable annotations without loading capture/archive infrastructure. It does
not change public JSON/RPC/SDK/session/extension behavior, retry policy, or
queued-input ordering.

Phase 2.2b remains the full provider/tool cycle: assistant-message assembly,
tool-call iterations, budgets, queue-port consumption, request policy, and the
final typed run result must still leave `NativeToolReplSession.run()`. Parallel
tools, richer termination, async conversion, persistence relocation, and UI or
extension redesign remain deferred.

Commit `925bd24` (`refactor: extract provider-turn executor`) passed direct
provider-turn, tool-loop, SDK, import, RPC, extension, TUI, PTY, documentation,
and diff gates. Final `just check` reported 3,330 passed and 2 skipped with Ruff
and mypy clean across 338 sources. Pi `openai-codex/gpt-5.6-sol` found and fixed
three warnings before two explicit CLEAN rounds, including a post-Fable pass.
Claude Fable found two suggestions, both fixed, then returned valid unscoped
CLEAN with no scope omissions or forbidden tool use. All five findings were
accepted and fixed; none were rejected or deferred.

### Canonical agent usage accounting — SHIPPED (2026-07-19)

Phase 2.2b.1 moves `_UsageAccumulator` and its provider-neutral helpers from
`NativeToolReplSession` into `native.agent.usage.AgentUsageAccumulator`. It owns
cumulative input/output/reasoning/cache counters, the last-turn context total,
cache-hit denominator classification, immutable `AgentUsage` projection, and
cost application from injected frozen `AgentTokenPricing`. The composition root
retains provider/model pricing lookup and passes the selected rate into each
accumulator; the canonical agent layer does not import that product catalog and
does not eagerly export the runtime helper.

This slice preserves usage coercion, footer/context behavior, canonical usage
events, and per-prompt versus session-total reset semantics. It does not change
provider selection, requests/history, retry or token-budget policy, compaction,
tools, queue/active-input storage, persistence, UI, RPC, SDK, or extensions.
Pricing/catalog consolidation remains Phase 5.3.

The `refactor: extract canonical agent usage` slice deletes the superseded
session-local usage/pricing helpers and migrates all six construction/reset
sites to the dependency-neutral accumulator. Direct arithmetic, lifecycle
ordering, pricing-prefix, reset-path, and static/recursive/fresh-process import
contracts passed. Final `just check` reported 3,382 passed and 2 skipped with
Ruff and mypy clean across 340 sources; docs and diff gates passed. Pi
`openai-codex/gpt-5.6-sol` returned explicit CLEAN after four accepted/fixed
warnings and Claude Fable returned valid unscoped CLEAN with no findings or
scope omissions. Commit `c261f2c` records this slice.

### Canonical agent-history compaction — SHIPPED (2026-07-19)

Phase 2.2b.2 moves only the mechanical canonical-message reduction into
`native.agent.history`. The new module accepts caller-owned limits, cuts at
`AgentUserMessage` group boundaries, and returns an immutable retained history
plus structural counters. It owns no compaction enablement, threshold defaults,
summary formatting, provider-request construction, extension hooks,
diagnostics, product-session write, or archive projection. It is neither
eagerly exported from `native.agent` nor given an archive convenience
serializer.

`NativeToolReplSession` retains manual/automatic trigger policy, exact
counts-only summary text and provider-system-prompt injection, extension-hook
ordering, aggregate result counters, and durable session-tree compaction
entries. The obsolete `native.session_compaction` module and its unused no-tool
path are deleted without an alias. Direct allowlist, recursive synthetic, and
isolated fresh-process import gates cover UI/render/theme/terminal,
session/persistence/settings, provider/catalog, extension, automation,
capture, and workflow-archive dependencies, including laundering through the
allowed canonical-message module.

Slice 2.2b.4b replaces the reproduced absolute-index hazard with an
identity-bound active input. Extension `deliverAs=nextTurn` context is now a
detached provider-request overlay for exactly one accepted run, so automatic
compaction can operate on durable history in that same run. The overlay remains
visible through every provider/tool iteration, cannot enter canonical run
results or add a duplicate product-session message, and cannot be mistaken for
an equal-text accepted prompt during hook transformation. Manual `/compact`,
the original bounded `CustomMessageEntry`, public formats, and archive privacy
stay unchanged.

The completed ordered Phase 2.2 cuts are:

1. **2.2b.3 — session tool-capability port seam (shipped):** inject tool
   capabilities while preserving sequential scheduling, budgets, and extension
   ordering.
2. **2.2b.4a — final provider-request snapshot and authorization (shipped):**
   freeze the exact advertised tool set after monotonic request-hook narrowing
   and reject out-of-snapshot returned calls before semantic hooks/execution.
3. **2.2b.4b — identity-safe active-input overlay (shipped):** remove absolute-index
   transient cleanup and the temporary automatic-compaction deferral while
   preserving exactly one-run provider visibility and archive privacy.
4. **2.2b.4c — run-effect, usage, and queue-facing ports (shipped):** establish the
   remaining typed loop seams while Phase 3 retains queue/lifecycle ownership
   and Phase 3.3 retains persistence-write relocation.
5. **2.2b.5 — full headless `AgentLoop` ownership cutover (shipped):** complete four
   independently green cuts: (a) rename the already-shipped one-provider
   executor to `native.agent.provider_turn` and reserve `native.agent.loop`,
   (b) extract typed request/tool/status policy collaborators, (c) move the
   single-run provider/tool cycle into the headless `AgentLoop`, and (d) close
   the controller-owned queued-input handoff while preserving separate-run
   semantics and the serialized RPC boundary.

Slices 2.2b.3–2.2b.5d are shipped. The next ordered migration target is Phase
3.1, the headless coding-session state machine.
Parallel tools,
richer termination, async
conversion, persistence relocation, UI/extension redesign, and provider catalog
work are not part of the Phase 2.2b cutover.

### Typed agent-loop policy collaborators — SHIPPED (2026-07-19)

Phase 2.2b.5b moves the remaining request, tool-admission/counter, and provider
status decisions behind `native.agent.loop_policy`. Its frozen state preserves
the existing budget-before-authorization-before-extension ordering, makes
budget exhaustion a nonterminal error-result condition, carries the
session-wide malformed streak across accepted runs, resets that streak only
after a valid settled execution, and normalizes provider failures with no
agent-loop retry. The canonical named 200-call maximum is reused by the product
session cap. There is no token-budget policy in this extraction.

The callback-composed `native.agent_loop_policy` adapters bind product request
construction and extension tool hooks without importing extension, session,
terminal, persistence, concrete-provider, or concrete-tool implementations.
Tool-result hooks can replace only `ProductContent`; provider and pipy request
identity, error status, and added-tool metadata remain canonical. All calls are
synchronous and callback failures propagate before subsequent events or work.
Because `ProviderRequest` itself is only shallowly frozen, the canonical input
detaches and recursively freezes tool-schema dictionaries/lists; a separate
provider-bound projection rematerializes ordinary JSON containers before any
built-in or extension provider invocation while authorization retains the
immutable snapshot.

`NativeToolReplSession` still owns queue storage and RPC settlement,
diagnostics/rendering, compaction, accepted-input preparation, and persistence.
The single-run cycle and its one-item typed queue-port poll now live in
`native.agent.loop`; the product controller starts any returned item as a
separate run. Public formats, extension callback order, persistence data, and
archive privacy do not change.

### Single-run headless AgentLoop — SHIPPED (2026-07-20)

Phase 2.2b.5c moves one already accepted prompt's complete synchronous
provider/tool cycle into `native.agent.loop.AgentLoop`. The canonical loop owns
the run/turn/message/tool lifecycle, streamed assistant assembly, normalized
usage, sequential tool scheduling, budget/authorization/extension-policy
ordering, session-carried malformed state, cancellation, the existing
`tool_budget + 2` fallthrough guard, and the typed final result/history/tool
state. Budget exhaustion remains a normal error tool result; provider failures
retain zero retry and do not terminate the REPL; only the existing third
consecutive malformed call requests product-session termination.

The product session now composes that loop through synchronous request,
provider-turn, status, event/effect, usage, and tool-policy adapters. It retains
compaction/summary injection, ordinary provider-request materialization, fresh
terminal/RPC abort binding, renderer refresh, diagnostics/footer behavior,
prompt history, durable writes, queue storage/priority/reservation, and RPC
settlement. Typed state callbacks keep the product counter mirrors current at
the historical per-transition points and before `AgentRunCompleted`; append
effects update the live product message mirror before the durable write. Direct
fake-port tests require no terminal, filesystem, concrete
provider/tool, extension runtime, or product session, and static/recursive/
fresh-process gates keep those dependencies out of the canonical layer. The old
inline cycle and its session-local live-tool-output helper are deleted.

### Typed queued-input handoff — SHIPPED (2026-07-20)

Phase 2.2b.5d closes the controller-owned steering/follow-up seam. One accepted
queued prompt enters `AgentLoopRunInput` as an atomic `AgentQueuedInput`; after
the synchronous `AgentRunCompleted` event, a non-terminating loop asks the
controller port for at most one next item and returns it as
`AgentLoopOutcome.next_input`. The product starts that value as a distinct next
run, preserving steering-before-follow-up priority and the existing local-command
boundary.

RPC uses the same whole DTO for a post-run reservation and for an idle blocking
input wake. Queue storage, reservation, active/idle transitions, abort clearing,
`queue_update`, and protocol `agent_settled` remain at the serialized RPC
boundary. The split `_QueuedDeliverySource`, `_PromptChannel._last_delivery_kind`,
and `take_delivery_kind` path is deleted without an alias. Queued `/...` and
`!...` content remains provider-visible rather than re-entering local dispatch;
JSON/RPC/session/extension formats, callback order, persistence, and archive
privacy are unchanged. Phase 3.1 is the next ownership move.

### Headless coding-session input policy — SHIPPED (2026-07-20)

Phase 3.1a introduces `native.coding.input_queue` as the synchronous, UI-free
owner of product input priority. It stores retained `AgentLoopOutcome.next_input`
handoffs, positional seeds, extension steering/follow-up/trigger delivery, and
one-shot `deliverAs=nextTurn` context; polls injected RPC/input-stream and
terminal queue ports in order; and defers local commands without consuming a
queued prompt. Retained post-run handoffs are FIFO, so a higher-priority local
resource command may run first without overwriting an older handoff or rejecting
a new one returned by the command's provider run. A command that appears during
a registered blocking wake remains first while the already-read ordinary line
and any newer mismatching queued DTO are retained in their existing order and
delivered exactly once. The exact full-content `AgentQueuedInput` remains
attached so queued slash and shell-looking content cannot re-enter local
dispatch.

The product composition root now adapts terminal/RPC sources and delegates both
outer selection and the active-loop queue port to that policy. The superseded
four extension lists, seed/retained/deferred-command locals, selection closures,
and queue-clear helper are deleted. Direct fake-source tests require no
terminal, filesystem, extension runtime, provider transport, tool, or session
tree; strengthened static and fresh-process import gates enforce that boundary.
RPC reservation, idle transitions, `agent_settled`, lifecycle, commands,
provider/model/settings/resource coordination, rendering, and persistence write
ownership remain for later Phase 3 slices. The next target is Phase 3.1b,
coding-session state and transitions.

### Headless coding-session state — SHIPPED (2026-07-20)

Phase 3.1b introduces `native.coding.state` as the synchronous, UI-free owner of
the active provider binding, canonical live history, usage and result counters,
compaction suffix/metrics, and unresolved provider failure. The product
composition root now applies named transitions instead of maintaining parallel
run-local integers and lists. Provider selection/construction, pricing lookup,
durable session-tree writes, commands, extension hooks, rendering, RPC
settlement, and reusable-loop invocation stay in their existing owners.

Direct fake-provider tests cover atomic binding, immutable message snapshots,
exact message identity, counter synchronization, compaction and rebuild
lifetime, usage reset, and provider-failure state without a terminal,
filesystem, concrete provider, extension runtime, or session tree. Stricter
static, recursive, and fresh-process import gates protect the new layer. The
current behavior in which provider/model/auth rebinds preserve an in-memory
compaction suffix is explicitly characterized; any correction is a separate
behavior slice. The next target is Phase 3.1c, the typed persistence
coordination seam without moving write ownership.

**Deferred compatibility correction — fatal image-result metadata:** after a
valid image reaches the first provider request, malformed-tool fatal results
currently retain zero-default image counters. Correct this only in a dedicated
public-metadata slice with JSON, adapter, and workflow-archive compatibility and
privacy contracts; Phase 3.1b deliberately characterizes rather than changes
the behavior.

### Product-Session Persistence Coordination — SHIPPED (2026-07-20)

Phase 3.1c introduces `native.coding.product_session` as the synchronous,
headless boundary between live `CodingSessionState` transitions and the current
private native-tree callbacks. Exact frozen/slotted DTOs carry canonical
full-content histories and compaction data. Message append and compaction apply
live state first and invoke the durable callback second; callback failures and
invalid asynchronous/non-`None` returns propagate before any later lifecycle
work. Session commands still replace or move the concrete tree first, then load
and validate its active history through the coordinator, rebuild live state,
and finally clear extension-scoped pending input.

Concrete `NativeSessionTree` and filesystem write ownership, summary formatting,
tree create/open/fork/import policy, command dispatch, rendering, extensions,
and RPC settlement remain in the product composition root. The Phase 1.2
product-session event projection is not activated as a second writer; actual
write relocation remains Phase 3.3. Exact import allowlists plus recursive,
fresh-process, and no-eager-export gates keep the coordination module free of
those outer implementations. Its full-content values never cross the separate
counts-only workflow archive. The next target is Phase 3.1d, typed imperative
command outcomes without introducing the Phase 3.2 declarative registry.

### Typed Coding-Command Outcomes, Control Kernel — SHIPPED (2026-07-20)

Phase 3.1d is split into independently green command-family cuts because the
current imperative region combines presentation, persistence, provider/auth,
session navigation, external I/O, reload, resources, and extensions. Slice
3.1d.1 adds direct-import-only `native.coding.commands` with exact frozen/
slotted outcome, action, and footer-policy values. It atomically migrates blank
input, `/exit`, `/quit`, `/hotkeys`, `/changelog`, `/copy`, and `/session` and
deletes their superseded monolith branches.

Composition still renders the submitted user bubble before ordinary command
classification, performs dynamic keybinding/changelog/clipboard/session-status
effects, and paints the footer. Exit remains footer-free. Every unmigrated
command returns the exact unhandled outcome to the one residual precedence
skeleton; no command registry or parallel dispatcher was introduced. The
non-empty queued/RPC `/...` and `!...` content bypasses the kernel and remains
provider-visible. Classified empty or whitespace-only content still takes the
unconditional blank outcome and is consumed locally, matching the prior branch.
Static exact-allowlist, recursive, fresh-process, and no-eager-export gates
exclude all outer implementations and the full-content outcome values never
enter the metadata workflow archive.

### Typed Compact and Session-Name Commands — SHIPPED (2026-07-20)

Phase 3.1d.2a adds exact `COMPACT` and `SESSION_NAME` actions to the same closed
outcome. `/compact` carries no payload. `/name` carries one exact full-content
argument: empty reports the current private session name, while non-empty
appends a private `session_info` entry. The direct classifier remains strict
about its already-stripped input contract; composition retains the existing
outer strip and exact internal name spacing.

Composition reuses the one manual/automatic compaction adapter, preserving the
extension gate, live-state-first product-session coordination, concrete durable
tree write, diagnostic, and footer sequence. Automatic compaction is not a
command and remains a separate caller of that adapter. Name query/set, escaped
legacy repr diagnostics, persistence and write-failure timing remain unchanged.
The old `/compact` and `/name` branches are deleted. Non-empty classified queued
forms still bypass local effects, and neither name/compaction content nor
dropped transcript bodies enter the metadata workflow archive.

### Typed Provider-Control Commands — SHIPPED (2026-07-20)

Phase 3.1d.2b adds exact typed `MODEL`, `SCOPED_MODELS`, `LOGIN`, and `LOGOUT`
actions to `native.coding.commands`. Bare and literal-space argument forms carry
an exact full-content control argument, and a second closed footer policy
preserves the usage-aware footer used by all four command families. Direct
classification stays strict about already-stripped input; local composition
continues to own outer trimming and model hotkey translation.

The product interpreter reuses the existing selector, catalog/provider state,
scoped settings, OAuth/auth store, provider reconstruction, fresh usage
accumulator, external-I/O suspension, diagnostic, and footer adapters. The four
late monolith branches are deleted, while selector cancellation, unavailable or
non-tool-capable refusal, scoped persistence failures, auth failure rebinding,
queued/RPC provider delivery, and credential privacy retain their current
behavior and order.

This extraction explicitly characterizes, rather than fixes, the current lack
of native-tree `model_change` writes and extension `model_select` hook dispatch
for selection/cycling. Those behavior corrections require dedicated persistence,
extension, resume, and TUI coverage; catalog refresh and auth diagnostic cleanup
also remain outside this ownership cut.

The remaining Phase 3.1d order is product-session navigation, external/UI
effects, reload, then resource/extension precedence closure. Phase 3.2 still
owns command names, aliases, descriptions,
availability, help, completion, menus, and the final declarative registry.

**Deferred compatibility correction — model-change native-tree entries:** the
product specs describe `/model` and model cycling as appending a durable
`model_change` entry, but the current tool-loop selection path does not do so.
Phase 3.1d must characterize and preserve that pre-existing behavior while
extracting command ownership. Correct it only in a dedicated behavior slice
with native-session JSON/reload/resume, provider-state, extension, and TUI
compatibility coverage; do not smuggle it into a command-family refactor.

**Settings resource-enablement fixture repair — SHIPPED (2026-07-20):**
`scripts/parity_checks/settings_config_conformance.py` check 13 now explicitly
models a trusted workspace at all three direct resource-discovery calls. This
restores its intended disable, re-enable, and command-gating assertions after
workspace-default discovery became fail-closed. All 17 settings checks pass;
production trust defaults, settings schema, and resource behavior did not
change.

### Typed New-Session Command — SHIPPED (2026-07-20)

Phase 3.1d.3a adds an exact payload-free `NEW_SESSION` action for `/new` with
the standard footer. The product interpreter preserves the existing switch
veto, private-tree creation and persistence-policy inheritance, typed active-
history rebuild, extension-input clearing, sanitized diagnostic, and footer
order. The late branch is deleted; queued/RPC `/new` remains provider-visible,
and no provider/tool turn runs for the local command.

Failure and compatibility characterization pins veto, hook error,
`KeyboardInterrupt`/`SystemExit`, create/rebuild partial-state cutoffs,
persistent and ephemeral sessions, and exact fresh context on the next prompt.
The slice adds no post-switch lifecycle or completed tree hook, registry
metadata, write relocation, public format, or workflow-archive projection.

### Typed Session-Tree Command — SHIPPED (2026-07-20)

Phase 3.1d.3b adds exact full-content `SESSION_TREE` outcomes for bare and
literal-space `/tree` forms with the standard footer. Composition preserves the
mutating-form `session_before_tree` gate, captured rendering and inline
selector, run-local filter and next-iteration prefill, same-file leaf movement,
label writes, optional branch summaries, history rebuild, extension-input
clearing, diagnostics, and partial-state failure timing. The late branch is
deleted; queued/RPC forms remain provider-visible.

The extraction deliberately does not add the target completed `session_tree`
hook, persist `treeFilterMode`, wire summary cancellation/usage/reserve settings,
or correct empty custom-message selection. These are behavior changes, not
typed-command ownership.

### Typed Resume Command — SHIPPED (2026-07-20)

Phase 3.1d.3c adds exact full-content `SESSION_RESUME` outcomes for bare and
literal-space `/resume` forms with the standard footer. Composition preserves
captured all/named listings, native rename/delete actions, live picker cancel
and current-session no-ops, switch-only extension gates, and successful
open→history rebuild→extension-input clear→custom redraw→diagnostic→footer
ordering. The late branch is deleted; queued/RPC forms remain provider-visible,
and the metadata-only workflow archive remains untouched.

The extraction deliberately preserves current direct-active reopen behavior,
run-lifetime provider/counter/filter state, captured active-rename staleness,
explicit-path deletion policy, ordinary TUI scrollback, and absent post-switch
lifecycle hooks. Those are separate behavior decisions, not typed ownership.

### Typed Fork and Clone Commands — SHIPPED (2026-07-20)

Phase 3.1d.3d adds exact full-content `SESSION_FORK` and payload-free
`SESSION_CLONE` outcomes with standard footers. Composition preserves the
persistent-store precondition, filtered explicit/current-leaf resolution,
`session_before_fork` gate, same-store `parentSession` lineage, fresh copied
entry IDs, typed history rebuild, extension-input clearing, sanitized
diagnostics, and failure cutoffs. The two late branches are deleted; queued/RPC
forms remain provider-visible and full content remains native-store-only.

The extraction deliberately preserves any-entry fork targets, empty persistent
clone, copied names/labels/compaction entries, absent custom/ordinary TUI
redraw, and absent post-fork lifecycle hooks. Those gaps require dedicated
behavior decisions.

### Typed Trust Command — SHIPPED (2026-07-20)

Phase 3.1d.4a adds the exact payload-free `TRUST_PROJECT` outcome for `/trust` with the
standard footer. Captured execution keeps the sanitized interactive-TUI
diagnostic and never opens the trust store or reads captured stdin. Live
execution keeps closest exact/inherited saved-decision read, selector display,
atomic selected-option write, fixed restart-required notice, and footer order.
Cancel performs no write; handled read/write errors cut off the later effects
but retain a sanitized notice and footer. The late path is deleted, and
non-empty queued/RPC `/trust` remains provider-visible.

This extraction does not mutate the current run's trust, hot-load or unload
protected inputs, or cross session/archive privacy boundaries. `/settings`,
export/import/share, `/reload`, resource/custom-command and extension
precedence, Phase 3.2 registry metadata, and Phase 3.3 write ownership remain
outside this slice.

### Typed Settings Command — SHIPPED (2026-07-20)

Phase 3.1d.4b adds the exact payload-free `SETTINGS` outcome for `/settings`
with the standard footer. Composition retains outer trimming, the submitted
user bubble, and non-empty queued/RPC bypass. Live execution still drives
`_drive_settings_dialog`; captured execution still prints the safe
`_settings_overlay_lines` view. The live dialog keeps cancel, in-place local
toggles, and in-place thinking-level cycling, which rebuilds the current rows
and may append one private entry. It does not enter the outer close/subflow/
reopen loop. Model, login/logout auth, scoped-model, theme, and default-project-
trust actions retain their nested close/subflow/reopen behavior, including
selector cancellation returning to the dialog; OAuth retains cooked-mode
suspension. One standard footer is painted only after the surface finally
closes. No provider or tool turn runs.

The old late branch is deleted. The command itself adds no native-session
entry; the existing in-place thinking-level action may still append its private
`thinking_level_change` entry. Already-applied local effects remain applied if
a later nested effect fails, and fatal propagation retains its pre-footer
cutoff. Prompt-history bodies stay in `PromptHistoryStore`, OAuth material in
the terminal/auth store, non-secret preferences in settings, and none enters
the metadata workflow archive.

This ownership slice records but does not correct a pre-existing mismatch:
the settings documentation describes `promptHistory.enabled` as the dialog
toggle's source of truth, while the actual dialog toggles the private
`PromptHistoryStore` and startup only applies `promptHistory.enabled` as a one-
way enable. A dedicated behavior slice must reconcile that contract.

### Typed Export Command — SHIPPED (2026-07-20)

Phase 3.1d.4c adds the exact full-content `SESSION_EXPORT` outcome for bare and
literal-space `/export` forms with the standard footer. The action carries the
exact `ProductContent` argument, including empty. Composition retains outer
trimming, the submitted user bubble, and non-empty queued/RPC bypass at the
existing serialized boundary.

The composition interpreter continues to own Pi-shaped quoted/unquoted path
parsing, home and cwd resolution, default HTML naming, format routing, export
side effects, and controlled `NativeExportError` diagnostics. Bare `/export`
remains full-tree HTML; an explicit case-insensitive `.jsonl` path remains a
re-chained active-branch export; every other explicit path remains HTML. One
standard footer follows success or a controlled export error. Uncontrolled
write failures retain their earlier pre-diagnostic, pre-footer cutoff. The old
late `/export` branch is deleted.

HTML and JSONL remain full-content product exports behind the existing
credential-redaction boundary. Their native-session bodies and the exact
command path never enter the metadata-only workflow archive. This extraction
does not change top-level CLI export behavior, formats, redaction, defaults,
filesystem semantics, terminal behavior, or queue/RPC ownership.

Focused and full verification passed, including export and automation/RPC
conformance plus the PTY smoke gate. Mandatory Pi review returned explicit
CLEAN with no findings, and the different-family Claude Fable review returned
direct unscoped CLEAN with no omissions. `/import`, `/share`, `/reload`,
resource/custom-command and extension precedence, the model-change/tree and
extension-hook gap, Phase 3.2 registry metadata, Phase 3.3 write ownership, and
Phase 4 UI movement remain outside this slice.

### Typed Import Command — SHIPPED (2026-07-20)

Phase 3.1d.4d adds the exact full-content `SESSION_IMPORT` outcome for bare and
literal-space `/import` forms with the standard footer. The action carries the
exact `ProductContent` argument, including empty. Composition retains outer
trimming, the submitted user bubble, and non-empty queued/RPC bypass at the
existing serialized boundary.

The composition interpreter continues to own quoted/unquoted path parsing,
`--yes` detection, home and cwd resolution, direct-stream confirmation, the
`session_before_switch` gate, collision-safe copy/open in the native product
store, missing-cwd recovery, typed history rebuild, extension-input clearing,
diagnostics, and footer timing. The preserved success order is parse/resolve,
confirmation unless `--yes`, switch gate, import, optional missing-cwd
confirmation and retry, history rebuild, extension-input clear, success
diagnostic, then one standard footer. Usage, cancellation, veto, and controlled
import errors keep their existing diagnostic and footer cutoffs; later failures
do not roll back earlier filesystem or live-state effects.

The action, source path, and imported transcript are private full-content
product values and never enter the metadata-only workflow archive. An import
exception escaping through a harness adapter may retain caller-facing or in-
memory detail, but durable metadata JSONL and Markdown retain only its bounded
type and fixed lifecycle metadata, never exception text, paths, or session
content. Direct `NativeToolReplSession` propagation is unchanged. This is an
ownership-only cut: it preserves direct-stream confirmation in both live and
captured modes, the second direct missing-cwd prompt, the absent post-switch
`session_start(reason=resume)` event, absent custom-entry and ordinary-
scrollback redraw, and raw unsanitized confirmation-path output. Fixing those
current-versus-target gaps requires dedicated UI, lifecycle, and security
decisions.

The command extraction changes no product behavior or public product format.
The accepted review fix narrows the durable metadata-archive exception
projection and is release-noted. `/share`, `/reload`, registry metadata,
persistence relocation, UI movement, path-parser or native-store/format
changes, confirmation-path security correction, rollback or atomic-write work,
and async conversion remain outside it.

Phase 3.1d.4x-share adds the exact payload-free `SESSION_SHARE` outcome for
bare `/share` with the standard footer, classified alongside `/hotkeys`,
`/settings`, and `/trust` in the payload-free tuple loop. `/share foo` and every
altered form fall through to UNHANDLED and reach resource/custom-command
dispatch unchanged, so no new argument grammar is introduced and the built-in
still wins over any custom command of the same name. Composition continues to
own the entire share effect sequence — `resolve_github_token()`, the no-token
diagnostic, the cancellation-worker `_share_native_session_command`
(`CancelToken`, worker thread, `wait_for_active_turn_interrupt`, Escape-cancel,
`Share cancelled.` messaging) guarded so only `NativeExportError` maps through
the sanitized diagnostic path, the cancelled `result is None` path, and the
viewer_url/gist_url diagnostics — but the centralized standard-footer refresh
after the CONTINUE block now owns the single post-command footer refresh,
matching `/export` and `/import`. The GitHub secret-gist creation, the
never-send-token-in-body privacy guarantee, the `ShareResult` output shape, and
`share_native_session` are untouched; neither token nor transcript enters the
metadata-only workflow archive. `/reload`, resource/custom-command and extension
precedence, `tui.py`/`repl_input.py` menu entries, registry metadata,
persistence relocation, UI movement, and async conversion remain outside it.
The extraction changes no product behavior or public product format.

Phase 3.1d.4d-reload adds the exact payload-free `RELOAD` outcome for bare
`/reload` with the standard footer, classified in the payload-free tuple loop
alongside `/hotkeys`, `/changelog`, `/copy`, `/session`, `/compact`, `/new`,
`/clone`, `/settings`, `/trust`, and `/share`. `/reload anything` and every
altered form fall through to UNHANDLED and reach
`dispatch_resource_command`/`dispatch_extension_command` unchanged, so no
argument grammar is introduced and the built-in still wins over any custom
command of the same name. This cuts the last raw built-in slash branch onto the
typed kernel path. Composition continues to own the entire reload effect
sequence — settings/keybindings reload, package-runtime/workspace re-discovery
and extension re-activation, extension-flag re-parse, catalog refresh with
extension-provider contributions and the tool-capability fallback rebind, tool
renderer/registry replacement, emitter/theme/derived-UI re-apply, custom-entry
redraw, `load_errors` diagnostics, startup chrome, the guarded implicit-trust
save, the `EVENT_SESSION_START` `reason='reload'` lifecycle, and the
reloaded-settings diagnostic — moved verbatim into an `elif` arm of the CONTINUE
chain, while the centralized standard-footer refresh after the chain now owns
the single post-command footer paint, matching `/export`, `/import`, and
`/share`. The superseded raw `if command_text == "/reload":` late branch is
deleted. Reload semantics, effect ordering, diagnostics, provider-fallback
behavior, and the reload-fired session-start lifecycle are unchanged; neither
reloaded settings nor keybindings enter the metadata-only workflow archive.
`RESERVED_COMMAND_NAMES`, slash-menu names/descriptions, completion, registry
metadata, persistence relocation, UI movement, and async conversion remain
outside it. The extraction changes no product behavior or public product format.

### Dispatch Precedence Closure — SHIPPED (2026-07-21)

Phase 3.1d typed-command-family ownership is now complete and closed. With
`/reload` the final built-in to leave the raw late-branch path, the dispatch
precedence is locked and characterized: the outcome kernel
(`classify_coding_command`) is the sole classifier for every built-in slash
command — only the `HOTKEY_*` sentinels and the `!` shell prefix precede
classification — and the kernel's `UNHANDLED` outcome is the single delegation
boundary, in the fixed order `dispatch_resource_command` ->
`dispatch_extension_command` -> the unknown-`/` fallback diagnostic -> the
provider turn. Built-in-over-custom precedence holds because the kernel
intercepts before resource dispatch, and custom/template-over-extension
precedence holds because a resource claim guards out extension dispatch, which
in turn runs before the unknown-`/` fallback. `dispatch_resource_command` and
`dispatch_extension_command` remain the delegation targets pending Phase 3.2's
declarative registry.

This closure is characterization plus documentation only, with no production
dispatch-logic change (matching the 3.1d.2b-test precedent). A single end-to-end
characterization test in `tests/test_native_tool_loop_session.py` drives `run()`
to pin all four orderings. It deliberately did not expand
`RESERVED_COMMAND_NAMES` to the full built-in set: that set governs which
colliding custom commands are advertised or dropped in slash discovery, so
widening it is a behavior change (a colliding `reload`/`tree`/`new` custom
command was still advertised even though the kernel prevents it running). That
advertising-completeness correction was deferred to Phase 3.2 and is now shipped
by the "reserve every built-in command name" sub-slice above, which derives both
reserved sets from the declarative registry's full name+alias set unioned with
the `skill`/`theme` adjuncts.

### Declarative command registry drives classification — SHIPPED (2026-07-22)

Phase 3.2 begins by making a declarative registry the sole classification
source. The new `native.coding.command_registry` holds a frozen
`BuiltinCommandSpec` table (`_BUILTIN_COMMANDS`) that enumerates every built-in
exactly once — the blank-input spec, the two `/exit`/`/quit` EXIT specs, and one
ACTION spec per `CodingCommandAction` — each carrying `name`, an `aliases` tuple,
an always-true `availability` predicate, a closed `BuiltinArgumentContract`
(`NONE`/`OPTIONAL_ARG`/`USAGE_AWARE`), and a `BuiltinCommandKind`
(`ACTION`/`EXIT`/`BLANK`). `classify_coding_command` moves out of the pure
`native.coding.commands` outcome kernel (whose AST/import gate keeps it a leaf
that cannot import the registry) into the registry, where it iterates that single
table through `_match_builtin` and builds the byte-identical
`CodingCommandOutcome` for every input. The three hardcoded if/elif tuple loops,
the inline `/exit`/`/quit` literals, and the kernel's now-unused
`_continue_outcome` helper are deleted; no second dispatcher or metadata table
survives. The import-boundary gate adds a `command_registry` rule + exact
allowlist, forbids the reverse `commands` -> `command_registry` edge, and points
the `session_controller` allowlist and fresh-process checks at the new owner.
This first sub-slice deliberately adds no description field, no
completion/menu/help consumption, no availability enforcement (the predicate
stays trivially true; gating remains in the interpreter), and no
`RESERVED_COMMAND_NAMES` widening — those remain later Phase 3.2 sub-slices. No
public CLI/JSON/RPC/session-format change, and no new runtime dependency, `Any`,
or `type: ignore`.

### Command metadata sourced from the registry — SHIPPED (2026-07-22)

The second Phase 3.2 sub-slice makes the registry the single source of advertised
command metadata. `BuiltinCommandSpec` gains a validated `description: str = ""`
field, and the sixteen advertised built-ins carry their prior description strings
verbatim. The registry adds four pure projection helpers — `builtin_command_names()`,
`builtin_command_description(name)`, `project_command_completions(names, *, adjunct_names=…)`,
and `project_command_descriptions(names, *, adjunct_descriptions=…)`. The three
metadata consumers become curated ordered projections: `native.repl_input` builds
`DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS`/`DEFAULT_REPL_COMMAND_DESCRIPTIONS` and
`native.tui` builds `TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS` from explicit name
lists validated against the registry with descriptions read from it, keeping
resource-owned `/skill` as an explicit adjunct so its advertised text is
preserved. The independently typed description dict literal and both duplicated
command-string tuples are deleted. Byte-identical behavior — every completion
tuple's members/order and every description string are preserved, the divergent
tuples are not unified, and the advertised set is unchanged. Availability
enforcement in menus/help remains a later sub-slice; the `RESERVED_COMMAND_NAMES`
widening is the next sub-slice below. No public CLI/JSON/RPC/session-format
change, and no new runtime dependency, `Any`, or `type: ignore`.

### Reserve every built-in command name — SHIPPED (2026-07-22)

The third Phase 3.2 sub-slice lands the one intended behavior change of the
phase and closes the advertising-completeness gap deferred from Phase 3.1d.
`RESERVED_COMMAND_NAMES` (`native.resources`) is now derived from the single
declarative-registry source — `frozenset(name.lstrip("/") for name in
builtin_command_names())` unioned with an explicit
`_RESOURCE_ADJUNCT_COMMAND_NAMES = frozenset({"skill", "theme"})` — replacing the
prior nine-name hand-maintained literal, and the built-in half of
`extension_reserved_command_names` (`native.extension_provider_catalog`) reuses
that same set (was the union of the two curated completion-menu subsets +
`/skill`), still unioning discovered custom-command slash names on top. A
colliding custom command, prompt template, or extension command named after ANY
built-in (`reload`, `tree`, `new`, `fork`, `session`, `compact`, `export`,
`import`, `clone`, `resume`, `name`, `share`, `trust`, `scoped-models`,
`hotkeys`, `changelog`, plus the nine already covered) is no longer advertised in
slash discovery / the menu and can no longer be registered by an extension.
`template` stays unreserved (no `/template` built-in); `skill`/`theme` stay
reserved. Runtime dispatch is unchanged: the kernel already intercepted every
built-in before resource/extension dispatch, so this only widens which colliding
resources are dropped from the advertised surface. Characterization landed first
in `tests/test_native_resources.py` and the `tests/test_native_tool_loop_session.py`
precedence test; the `extension_activation_conformance.py`,
`extension_dispatch_conformance.py`, and `settings_config_conformance.py` gates
add fixtures/checks exercising the widened set. No public
CLI/JSON/RPC/session-format change beyond the intended advertising widening, and
no new runtime dependency, `Any`, or `type: ignore`.

### Persistence subscriber (Slice 3.3) — SHIPPED (2026-07-22)

Phase 3.3 makes durable product-session persistence a live projection inside
each mode's fixed composite instead of a reusable-loop effect, as one atomic
ownership cut. `_ExtensionAwareAgentEventSink` constructs its
`ProductSessionEventProjection` with a typed `NativeProductSessionActionSink`
(new, in `native.agent_adapters` beside the projection,
`ProductSessionActionSink`, and `AppendProductMessage`, because the
`native.agent_runtime` import boundary forbids depending on `agent_adapters`);
the sink forwards each projected `AppendProductMessage` to
`product_session.append_message`, preserving the coordinator's exact
coding-state-then-session-tree write. The emitter construction moved just below
the session-tree/`ctl`/product-session setup band so the composite can hold the
live sink — a construction-order change only; the composite's fixed emission
order (renderer, automation, persistence projection, workflow archive, caller
sink, lifecycle hooks) is unchanged and no event is added or removed. In the
same change the superseded effect path is DELETED with no alias:
`AgentLoop._append_message` keeps `state.history` maintenance but no longer emits
an append effect; the `run_effect_sink` port/field/validation is removed from
`AgentLoop` and `CodingAgentRunCoordinator` (and its loop construction);
`NativeAgentRunEffectSink` construction/wiring is removed from
`tool_loop_session`; and `AppendAgentMessage`/`AgentRunEffect`/
`AgentRunEffectSink` are removed from `native.agent.runtime_ports` with
`NativeAgentRunEffectSink` deleted from `native.agent_runtime`. Provider requests
are byte-identical: the loop still appends the accepted user message to
`state.history` before the turn loop, and `_prepare_loop_request` still mirrors
that authoritative history into coding-state (and compacts) before each request,
so event-driven persistence never feeds request construction; the final coding
state stays authoritative through the coordinator's per-turn and end-of-run
`mirror_history`. The only divergence from the deleted effect path is a
transient, mirror-vs-projection double of the accepted user message in live
coding-state within its own turn — cleared by the next `mirror_history`, never
durable, never fed to a provider request, and unobserved across the full suite
(extension `agent_end`/lifecycle hooks, footers, `/tree`, resume, compaction, and
the metadata-only archive all read the corrected state). The interactive
`!`-shell-message append and durable compaction stay imperative through the
coordinator; the raw native tree stays distinct from the counts-only archive
(allowlist unchanged). Boundary gates updated for the removed ports; new
`NativeProductSessionActionSink` characterization lives in
`tests/test_native_agent_event_adapters.py`, and the effect-timing loop and
session-integration assertions were retargeted onto `outcome.final_history` and
the projection action sink. No new runtime dependency, `Any`, or `type: ignore`.

### Terminal output + raw-mode + restoration driver (Slice 4.2) — SHIPPED (2026-07-22)

Phase 4.2's first cut extracts `native.terminal_driver`. The new strict-typed
`TerminalDriver` owns `ToolLoopTerminalUi`'s terminal I/O: the input/terminal
streams, the error-swallowing `write(text) -> bool` write/flush sink, the termios
raw-mode lifecycle (`_old_termios`/`enter_raw_mode`/`restore_terminal_mode`),
bracketed-paste toggling (`_set_bracketed_paste` + the relocated
`_BRACKETED_PASTE_ENABLE`/`_DISABLE` toggle sequences), and the xterm
terminal-title OSC push/write/restore (`push_title`/`write_title`/`restore_title`
+ the relocated `_TITLE_MAX_CHARS` cap and control-character sanitization). The
UI builds the driver once in `__post_init__` and routes every terminal
write/flush through it (paint, forced full redraw, resize screen-clear, `close`
teardown, `external_io_suspension`, external-editor notice), with
`_force_full_redraw` and the resize handler branching on the returned `bool` to
keep the exact skip-bookkeeping-on-failed-frame behavior. The two `\x1b[2J\x1b[H`
screen-clear sites route through a write-without-flush `write_deferred(text) ->
bool` (not the flushing `write`) because the pre-extraction code wrote those
clears unflushed so they coalesced with the flush of the immediately-following
paint; a separate flush there could reintroduce a resize/full-redraw flash the
buffered original avoided. The six superseded
methods, the three fields (`_old_termios`/`_bracketed_paste_active`/
`_extension_title_pushed`), the two toggle constants, the `_TITLE_MAX_CHARS`
definition, and the now-unused `import tty` are DELETED from `tui.py` with no
alias or second write path; the bracketed-paste `_START`/`_END` decoding markers
stay with the key decoder (Slice 4.2b). `push_title` is now internally idempotent,
so the set-extension-title single-push guard is dropped without changing OSC
emission. Typeahead policy is characterized and preserved: `enter_raw_mode` calls
`tty.setraw(fd)` with the stdlib default `termios.TCSAFLUSH`, which flushes
input queued before the transition, so consumers sync on prompt readiness. The
agent/coding import-boundary gates forbid depending on `native.terminal_driver`.
PTY-free `tests/test_native_terminal_driver.py` (15 tests, including the
deferred-write no-flush/coalesce characterization) plus focused TUI,
editor, custom-UI, PTY, and import-boundary suites pass; `just test-pty-smoke`
(restoration after success/error/cancellation), `automation_rpc_conformance.py`,
`just check` (Ruff, mypy, 4453 passed/2 skipped clean off the driver path save
the documented rotating load-dependent PTY timing flakes that each pass in
isolation, parity 49/49), and
`just docs-build` are green. Behavior-preserving control-plane move only: no
change to which bytes are written or when, event ordering, or CLI/JSON/RPC/
session/extension formats; no key-decoding, resize/size-resolution,
alternate-screen, or async work (Slices 4.2b/4.2c and later); no new runtime
dependency, `Any`, or `type: ignore`.

### Low-level input reading + key decoder onto the driver (Slice 4.2b) — DONE (2026-07-22)

Phase 4.2b relocates the fd-level read primitives and key decoder onto
`TerminalDriver`, which already owns the input fd. The driver gains
`read_key`/`read_key_if_available` (public, replacing the UI's private
`_read_key`/`_read_key_if_available`), the private `_read_escape_sequence`/
`_read_bracketed_paste`/`_read_byte`/`_read_byte_with_timeout`, the
`_pending_input_bytes` UTF-8 over-read buffer (exposed via
`has_pending_input()`), and the relocated bracketed-paste *decode* markers
`_BRACKETED_PASTE_START`/`_BRACKETED_PASTE_END` (joining the enable/disable
toggles moved in 4.2). Decode logic is lifted verbatim, so every named key, C0
`ctrl-<letter>`, CSI arrow/home/end, Shift+Tab, all four Shift+Ctrl+P forms,
Alt+Up/Alt+Enter, and multi-byte UTF-8 scalar decode byte-identically, and the
paste body keeps its `\r\n`/`\r` -> `\n` normalization and bounded 2.0s read.
Because the durable `_pending_paste` buffer stays UI-owned, the driver returns a
decoded paste body to the caller rather than storing it: the read returns
`"paste"` and stashes the body in a transient `_last_paste` that the caller
drains via `consume_paste()`. A new UI seam `_read_driver_key(key)` copies that
body into `_pending_paste` on `"paste"` and is the single funnel every decode
call site passes through. `_read_key_polling_resize` keeps its footer-branch and
resize-polling loop in the UI but delegates the fd read+decode to
`self._driver.read_key(fd)` (its over-read guard now asks
`self._driver.has_pending_input()`), and the mid-turn
`wait_for_active_turn_interrupt` call site delegates to
`self._driver.read_key_if_available(fd, poll_seconds)`; both wrap the result in
`_read_driver_key`. The six moved methods, the `_pending_input_bytes` field, the
two decode-marker constants, and the now-unused `read_terminal_utf8_char` import
are DELETED from `tui.py` with no alias or shadow copy. No key->action mapping
inside `read_line`/`wait_for_active_turn_interrupt` changed; no resize/size,
output, mode, or layout move. `tests/test_native_terminal_driver.py` gains
PTY-free key-decoder coverage over a real pipe fd, and the four TUI-side call
sites that drove the real decoder were repointed onto the driver (paste helper
routed through `_read_driver_key`). Focused TUI/completion/image-paste/custom-
editor/extension-custom-UI/PTY suites, `just test-pty-smoke` (8/8),
`automation_rpc_conformance.py` (ALL PASS), `just check` (Ruff, mypy clean, 4457
passed/2 skipped save three load-dependent PTY timing flakes that each pass in
isolation off the decoder path), and `just docs-build` are green. Control-plane
move only: no change to decoded keys, paste bodies, event ordering, or CLI/JSON/
RPC/session/extension formats; no new runtime dependency, `Any`, or `type:
ignore`.

### SIGWINCH resize lifecycle + terminal-size resolution onto the driver (Slice 4.2c) — DONE (2026-07-22)

Phase 4.2c moves the resize/size concern onto `TerminalDriver`, which already
owns the fd it paints to. The driver gains the SIGWINCH lifecycle
(`install_resize_handler`/`remove_resize_handler`/`_on_resize_signal`, the
`_resize_pending` flag, the saved `_prev_winch_handler` disposition), the public
`take_resize_pending()` drain, and the live terminal-size resolver
`size(*, width=None, height=None)` (the relocated `_dimensions`) backed by the
private `_terminal_size`/`_env_terminal_size`, plus the relocated
`_MIN_WIDTH`/`_MIN_HEIGHT`/`_DEFAULT_SIZE`/`_RESIZE_POLL_SECONDS` constants. All
logic is lifted verbatim, so geometry resolves identically (explicit override,
then `COLUMNS`/`LINES` env, then the real output `winsize`, then the `shutil`
fallback, `None` for a non-TTY capture keeping the caller's defaults, each
dimension clamped to the min floors with the default fallback) and the SIGWINCH
handler still only flips a flag (installing off the main thread is caught and
ignored). The UI wires `install_resize_handler` from `start()` and
`remove_resize_handler` from `close()`; its layout-coupled
`_poll_resize_repaint`/`_repaint_after_resize` stay in `ToolLoopTerminalUi` but
query `self._driver.size()` and drain `self._driver.take_resize_pending()`,
keeping only `_last_painted_size`. Every other `self._dimensions(...)` call site
(five internal in `tui.py`, five `._dimensions()[0]` in `tool_loop_session.py`,
one in `tests/test_native_terminal_screen.py`) is repointed to
`self._driver.size(...)`/`ui._driver.size()`. The three resize methods, the two
fields, `_dimensions`/`_terminal_size`/`_env_terminal_size`, the four constants,
and the now-unused `import signal`/`import shutil` are DELETED from `tui.py` with
no alias; the UI imports `_RESIZE_POLL_SECONDS` from the driver for its
resize-polling `select` timeout (matching the `_TITLE_MAX_CHARS` pattern).
`tests/test_native_terminal_driver.py` gains PTY-free resize/size coverage, and
the two `tests/test_native_tool_loop_tui.py` resize characterizations were
repointed onto the driver (`_poll_resize_repaint`/`_repaint_after_resize`/
`_last_painted_size` stay UI-side and unchanged). Focused driver/TUI/terminal-
screen/chrome-widget/import-boundary suites, `just test-pty-smoke` (8/8) and the
resize PTY cases, `automation_rpc_conformance.py` (ALL PASS), `just check` (Ruff,
mypy clean, 4467 passed/2 skipped, no flakes), and `just docs-build` are green.
Control-plane move only: no change to resolved sizes, resize repaint behavior
(still an inline clear-and-redraw at the new width), event ordering, or CLI/JSON/
RPC/session/extension formats; no new runtime dependency, `Any`, or `type:
ignore`.

### Extension API vocabulary + value-object leaf + sandbox loader (Slices 6.1a/6.1b/6.1c) — DONE (2026-07-22)

Phase 6.1 begins with sub-slice 6.1a: the new stdlib-only leaf
`native.extension_types` takes sole ownership of the fail-closed extension
vocabulary both the runtime and the later loader depend on. The twenty-one
`REASON_*` activation reason codes, the internal `_ActivationError`, the
type-name-only `_safe_diagnostic`, the Pi command-name character rules
(`_is_valid_command_name`/`is_valid_custom_entry_type`), the reserved-shortcut
layer (`RESERVED_SHORTCUT_KEYS`, `_SHORTCUT_MODIFIERS`, `normalize_shortcut_key`),
and the bound constants (`_DIAGNOSTIC_MAX_LENGTH`, `_CUSTOM_ENTRY_TYPE_MAX_CHARS`)
move verbatim out of `extension_runtime.py`; the originals are deleted with no
shadow copy or compatibility alias. Because the module has no project imports it
can never form an import cycle with the runtime/loader that import it.
`extension_runtime` re-imports every still-referenced name, `pipy_harness.extensions`
keeps re-exporting `normalize_shortcut_key` unchanged, and `tool_loop_session`/
`tui` keep importing the shortcut/entry-type helpers from `extension_runtime`
with no source change. The import-boundary suite forbids `native.extension_types`
beside `native.extension_runtime` in every agent- and coding-layer rule.
Behavior-preserving move only: no callback, ordering, reason-code string, or
public import change; no new runtime dependency, `Any`, or `type: ignore`.
Focused extension shortcut/activation/conformance and import-boundary suites
passed (205), `extension_conformance_gate.py` reported ALL PASS, and `just check`
(Ruff, mypy clean, 4,500 passed/2 skipped) plus `just docs-build` are green.

Sub-slice 6.1b then relocates the stable frozen value-object dataclasses into the
same leaf: the hook events/transforms/results (`ProjectTrustEvent`/`Context`/
`HandlerError`/`DispatchResult`, `LifecycleEvent`, `InputEvent`/`InputTransform`,
`BeforeAgentStartEvent`/`Result`, `QueuedUserMessage`/`QueuedCustomMessage`,
`ToolResultEvent`/`ToolResultTransform`/`ToolResult`, `ToolBlock`/`ToolCallEvent`,
`UserBashEvent`/`Decision`/`Dispatch`, `BeforeProviderRequestEvent`/
`BeforeProviderHeadersEvent`/`ProviderRequestTransform`, `SessionBeforeEvent`/
`SessionDecision`), the neutral tool/flag descriptors (`ExtensionTool`,
`RegisteredTool`, `ExtensionFlag`, `RegisteredFlag`), and the `ExtensionMode`
literal move verbatim out of `extension_runtime.py`, originals deleted with no
alias. `extension_runtime` re-imports every one and `pipy_harness.extensions`
keeps re-exporting the public subset unchanged, so the public import path stays
byte-identical. `ProjectTrustContext.ui` and `ExtensionTool.render_call`/
`render_result` forward-reference two UI types Slice 6.4 still owns
(`ExtensionUi`/`ToolRenderContext`); those are resolved for mypy through a single
`if TYPE_CHECKING:` import from `extension_runtime` — a type-checking-only edge
with no runtime import, so the leaf stays runtime-cycle-free — and repoint to
their `extension_types` home when 6.4 moves them. Provider-port value objects
(6.3), UI protocols/renderers (6.4), and dispatch/activation logic (6.2) are
untouched. Behavior-preserving move only: no field, ordering, callback, default,
or public import change; no new dependency, `Any`, or `type: ignore`. Focused
`test_native_extension_dispatch`/`tools`/`input_hooks`/`tool_result_hooks`/
`project_trust` plus the import-boundary suite passed (241),
`extension_conformance_gate.py` reported ALL PASS, and `just check` (Ruff, mypy
clean, 4,500 passed/2 skipped) plus `just docs-build` are green. Review: Claude
Opus panel (user-directed substitution for the different-family gate) — 2 rounds,
2 findings total, final round clean across both lenses (behavior; invariants).

Sub-slice 6.1c then creates `native.extension_loader` and moves the low-level
extension sandbox out of `extension_runtime.py`: the on-disk import path
(`_import_entry_module`, `_load_standalone_module`, `_load_package_submodule`,
`_purge_modules`, `_safe_module_segment`) and the awaitable driver
(`_run_awaitable`, `_drive_awaitable`, `_event_loop_is_running`,
`_as_coroutine`) move verbatim, originals deleted with no alias. The loader
imports only stdlib plus `_ActivationError`/`REASON_IMPORT_ERROR`/
`_safe_diagnostic` from `extension_types` and `ExtensionDescriptor` from
`native.extensions`, so there is no cycle back to `extension_runtime`, which
imports the three still-called entry points (`_import_entry_module`/
`_run_awaitable`/`_drive_awaitable`); activation orchestration stays in
`extension_runtime`. The now-unused `hashlib`/`importlib.machinery`/
`importlib.util`/`sys` imports and the non-re-exported `REASON_IMPORT_ERROR` are
dropped from the runtime. The import-boundary suite adds
`native.extension_loader` beside `extension_runtime`/`extension_types` in every
agent- and coding-layer rule (10 rules). Behavior-preserving move only: no
change to `sys.modules` namespacing, fail-closed import, relative-import
isolation, coroutine-driving behavior, public imports, dependencies, `Any`, or
`type: ignore`. Focused extension discovery/activation/packages/answer-example
plus the import-boundary suite passed (244);
`extension_discovery_conformance.py`/`extension_activation_conformance.py`
reported ALL PASS (`extension_package_conformance.py` fails identically on the
pristine tree — a pre-existing environment-specific `agent/loop.py` error, its
pytest green); `just check` (Ruff, mypy clean, 4,503 passed/2 skipped) plus
`just docs-build` are green. Review: Claude Opus panel (user-directed
substitution for the different-family gate) — pending review.

### Turn hook-dispatch families (Slice 6.2a) — DONE (2026-07-22)

Sub-slice 6.2a creates `native.extension_hooks` and moves the five per-turn
hook-dispatch families plus their shared collectors and bound constants verbatim
out of `extension_runtime.py`: `extension_event_hooks`,
`extension_tool_call_hooks`, `dispatch_input_hooks`,
`dispatch_before_agent_start_hooks`, `dispatch_tool_result_hooks`,
`dispatch_lifecycle_hooks`, `dispatch_tool_call_hooks`, and the
`_TOOL_RESULT_MAX_CHARS` / `_BEFORE_AGENT_START_MAX_CHARS` truncation bounds. The
originals are deleted with no shadow copy or alias. The new module imports
`_drive_awaitable` from `extension_loader`, the hook value objects from
`extension_types`, and the `_CommandContext`/`_CollectingUi` builders plus the
`EVENT_TOOL_CALL` constant from `extension_runtime` (one-way, cycle-free —
`extension_runtime` never imports back). `tool_loop_session`, the
`pipy_harness.extensions` re-export block, the `extension_tool_call_conformance`
gate, and the direct-import extension tests are repointed to `extension_hooks`;
the nine hook value objects `extension_runtime` re-imported solely for those
functions become explicit `# noqa: F401` re-exports (public path via
`pipy_harness.extensions` unchanged). The import-boundary suite adds
`native.extension_hooks` beside `extension_runtime`/`extension_loader`/
`extension_types` in every agent- and coding-layer forbidden rule (10 rules). No
signature, ordering, fail-soft/fail-closed, truncation-bound, callback, or
public-import-path change; no new dependency, `Any`, or `type: ignore`. The gate
family (`project_trust`/`user_bash`/`session_before`) and the
provider-request/headers dispatchers stay in `extension_runtime` for later 6.2
cuts. Focused extension lifecycle/input/tool-result/tool-call/dispatch plus the
import-boundary and tool-loop suites passed; the five extension conformance gates
and PTY smoke reported ALL PASS; `just check` (Ruff, mypy clean, 4,506 passed/2
skipped) and `just docs-build` are green. Review: Claude Opus panel
(user-directed substitution for the different-family gate) — 1 round, 0 findings,
final round clean, both lenses (behavior; invariants).

### Gate hook-dispatch family (Slice 6.2b) — DONE (2026-07-22)

Sub-slice 6.2b relocates the serial fail-closed gate dispatchers verbatim out of
`extension_runtime.py` into `native.extension_hooks`:
`dispatch_project_trust_hooks`, `dispatch_user_bash_hooks`, and
`dispatch_session_before_hooks`. The originals are deleted with no shadow copy or
alias. The move reuses the `extension_hooks` -> `extension_loader`/
`extension_types`/`extension_runtime` dependency established in 6.2a, adding only
`EVENT_PROJECT_TRUST` from `extension_runtime` and the project-trust/user-bash/
session value objects (`ProjectTrust*`, `UserBash*`, `SessionDecision`,
`SessionBeforeEvent`, `ExtensionMode`) plus `_safe_diagnostic` from
`extension_types`, so no new import edge or cycle appears. `tool_loop_session`,
the `pipy_harness.extensions` re-export block, and the `cli.py` local import are
repointed to `extension_hooks`; the direct-import tests
(`test_native_extension_project_trust`, `test_native_extension_live_session_hooks`)
follow. The now-unused `extension_types` re-imports that stay part of the public
subset (`SessionBeforeEvent`, `SessionDecision`, `UserBash*`) become explicit
`# noqa: F401` re-exports; the private-only `ProjectTrust*`/`ExtensionMode`
imports (not re-exported) are dropped. No change to serial ordering,
first-blocking-decision semantics, fail-closed-on-crash behavior, remember/
undecided handling, or public imports; no new dependency, `Any`, or `type:
ignore`. Focused project-trust/live-session/dispatch/import-boundary suites
passed (230); `extension_live_session_conformance`, `extension_dispatch_conformance`,
and `extension_conformance_gate` reported ALL PASS; the 49-test TUI PTY file and
PTY smoke (8) passed; `just check` (Ruff, mypy clean, full suite green) and `just
docs-build` are green. The provider-request/headers dispatchers stay in
`extension_runtime` for the next 6.2 cut. Review: Claude Opus panel (user-directed
substitution for the different-family gate). Pending review.

### Provider-request hook-dispatch family (Slice 6.2c) — DONE (2026-07-22)

Sub-slice 6.2c relocates the last hook family verbatim out of
`extension_runtime.py` into `native.extension_hooks`:
`dispatch_before_provider_request_hooks`, `dispatch_before_provider_headers_hooks`,
their helper `_bounded_provider_field`, and the `_PROVIDER_REQUEST_FIELD_MAX_CHARS`
(128 KiB) field bound. The originals are deleted with no shadow copy or alias. The
move reuses the `extension_hooks` -> `extension_loader`/`extension_types`/
`extension_runtime` dependency established in 6.2a/6.2b, adding only the
`_ConversationView` builder from `extension_runtime`, the provider value objects
(`BeforeProviderRequestEvent`/`BeforeProviderHeadersEvent`/`ProviderRequestTransform`)
from `extension_types`, plus `MutableMapping` and a type-check-only
`NativeSessionTree` import, so no new import edge or cycle appears.
`extension_runtime` no longer references any moved function, so its now-unused
`_drive_awaitable` import is dropped and the three provider value-object re-imports
(still re-exported through `pipy_harness.extensions`) become explicit
`# noqa: F401` re-exports. `agent_request.py`, `tool_loop_session`, the
`pipy_harness.extensions` re-export block, the direct-import tests, and the
`test_architecture_agent_request_boundaries` allow-list are repointed to
`extension_hooks` (public paths byte-identical; `ProviderRequestTransform` stays
sourced from `extension_runtime`). After this cut `extension_hooks` is the sole
owner of all extension hook dispatch, leaving `extension_runtime` with activation,
registration, context builders, protocols, and renderers. No change to structural
request-attribute reading, the field-truncation bound, mutation-only header
semantics, fail-safe/fail-soft behavior, or public imports; no new dependency,
`Any`, or `type: ignore`. Focused dispatch/providers/live-session/project-trust/
policy-integration/import-boundary/agent-request-boundary suites passed (272);
`extension_conformance_gate`, `extension_dispatch_conformance`,
`extension_live_session_conformance`, and `automation_rpc_conformance` reported ALL
PASS; the 49-test TUI PTY file and PTY smoke (8) passed; `just check` (Ruff, mypy
clean, full suite green) and `just docs-build` are green. Review: Claude Opus panel
(user-directed substitution for the different-family gate). Pending review.

### Extension provider registration onto the shared construction port (Slice 6.3a) — DONE (2026-07-22)

Sub-slice 6.3a moves the extension provider-port value objects and their build
functions out of `extension_runtime.py`, originals deleted with no shadow copy or
alias. The four descriptors (`ProviderContext`, `ExtensionOAuthConfig`,
`ExtensionProvider`, `RegisteredProvider`) relocate verbatim into the
`native.extension_types` value-object leaf (their 6.1b-deferred home);
`ExtensionProviderBuildResult` plus `build_extension_provider_port` /
`try_build_extension_provider_port` relocate into `native.provider_construction`,
already the single provider-construction owner for built-ins, which gains one
cycle-free edge to the stdlib-only `extension_types` leaf
(`ProviderContext`/`RegisteredProvider`/`_safe_diagnostic`). `repl_state` drops
its runtime→`extension_runtime` construction edge and builds extension providers
through the same `provider_construction` seam as built-ins; `catalog_state` and
`extension_provider_catalog` repoint `RegisteredProvider` to `extension_types`;
`extension_runtime` re-imports the four descriptors (still used in
staging/registration; body-unused `ProviderContext` re-exported via `# noqa: F401`)
and keeps only activation/registration/staging. `pipy_harness.extensions` keeps
every public name — sourcing `build_extension_provider_port` from
`provider_construction` and the descriptors from `extension_runtime` — so the
public import path is byte-identical; the characterization suite and
`extension_providers_conformance.py` repoint their direct construction imports.
No change to provider request/response semantics, factory-failure fail-closed
behavior, `ProviderContext` field shape, catalog selection, or public extension
imports; no new dependency, `Any`, or `type: ignore`. Focused providers/
activation/agent-runtime-ports/import-boundary suites passed (249);
`provider_catalog_conformance`, `extension_providers_conformance`,
`extension_conformance_gate`, and `automation_rpc_conformance` reported ALL PASS;
PTY smoke (8) passed; `just check` (Ruff, mypy clean, full suite green) and
`just docs-build` are green. Review: Claude Opus panel (user-directed substitution
for the different-family gate). Pending review.

Host-port bundling (6.3b/6.3c) and the UI bridge (6.4) remain deferred; Slice 6.2
hook dispatch is complete and 6.3 extension-provider construction now shares the
built-in construction seam.

### Typed extension model-runtime control host port (Slice 6.3b) — DONE (2026-07-22)

Sub-slice 6.3b groups the three loose model-runtime control callables
(`set_active_tools_fn` / `set_model_fn` / `set_thinking_level_fn`) behind one
frozen port owned by the extension layer. The new `ExtensionModelRuntimeControl`
value object plus the `ControlSet*Fn` aliases live in the `native.extension_types`
leaf (only `ExtensionModelRuntimeControl` is re-consumed by `extension_runtime`;
the aliases have no outside consumer and are not re-exported) and the bundle is
threaded as a single
`model_runtime` parameter through `make_extension_context`,
`dispatch_extension_command` / `dispatch_extension_shortcut` /
`_run_extension_handler`, and all eight model-runtime hook dispatchers in
`extension_hooks`; the three per-call parameters are deleted at each seam, and
`_CommandContext` reads the fields off the stored bundle. `_ProviderMutationEffects`
gains one adapter, `model_runtime_control(*, allow_model=…)`, over its existing
`extension_set_*` methods; `NativeToolReplSession`, `_ReplLoopStep`, and
`_run_local_shell_shortcut` call the adapter instead of passing three bare
callables. The three mid-turn hook paths (before_provider_request, tool_call,
tool_result) pass `allow_model=False`, preserving the old fail-closed
`set_model` behavior via the shared `_deny_model_mutation` helper.
`NativeProviderRequestHookContext` carries one `model_runtime` field. No change to
control-callback semantics, bool acceptance, hook ordering, which callables each
dispatcher applies, or the public `pipy_harness.extensions` surface; no new
dependency, `Any`, or `type: ignore`. `_ExtensionToolPort` keeps its single
`set_active_tools_fn` collaborator (wrapped into the bundle only at the
`make_extension_context` seam), and the `_BuiltinCommandInterpreter`
single-callable port is untouched. Focused dispatch/input/tool-result/
live-session/lifecycle/theme-controls/session-tree/agent-request suites passed
(178 + 307); `extension_conformance_gate`, `extension_dispatch_conformance`,
`extension_live_session_conformance`, and `automation_rpc_conformance` reported
ALL PASS; the 49-test TUI PTY file and 8-test PTY smoke passed; `just check`
(Ruff, mypy clean, 4,506 passed, 2 skipped) and `just docs-build` are green.
Review: Claude Opus panel (user-directed substitution for the different-family
gate) — 1 round, 2 findings total, final round clean across both lenses
(behavior; invariants). Coding-session loose params remain 6.3c; the UI callables
remain 6.4.

### Strict Mypy gate for the native session core (Slice 7.6a) — DONE (2026-07-23)

First strict-root follow-on slice. Add `pipy_harness.native.session` to the
existing enumerated strict-Mypy override (never `strict = true`) and resolve the
four measured `no-any-return` errors through real narrowing/typing in that
module. Preserve provider turns, safe metadata projection, intent/read-only
classification, optional-text sanitization, events, usage, and every
session/archive format. The gate must stay scoped and `no_implicit_reexport`
remains governed by the exporting module. No behavior change, unchecked
`Any`, `type: ignore`, relaxed flag, exclusion, dependency, or C901 pin.

Landed shape: `pipy_harness.native.session` now participates in the existing
enumerated strict-Mypy override. Its four measured `no-any-return` paths use
concrete runtime narrowing: the required provider-turn label and sanitized
optional text prove `str`; read-only intent classification proves both labels
are strings; and the provider-metadata boundary retains only the scalar shapes
its closed allowlist projectors already produce after sanitization. Valid
provider-turn, intent, error-text, and archive payloads therefore keep their
existing values, while malformed or future values fail closed. No flag was
relaxed and no exclusion, `Any`, `type: ignore`, dependency, or C901 pin was
added. The focused session/privacy/archive suite passed 71 tests; final
`just check` passed Ruff, Mypy, and 4,509 tests (2 skipped);
`just docs-build` reported no issues. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### C901 strict-leaf invariant decomposition (Slice 7.7a) — DONE (2026-07-23)

First directional complexity burn-down batch. Remove the single C901 finding
from each of `native.agent.loop_policy`, `native.agent.results`, and
`native.coding.command_registry` by separating each value object's primitive
shape checks from its coherent cross-field invariant checks. Preserve exact
exception classes/messages and validation order; do not replace branches with
tables or otherwise game McCabe. Each clean file must leave the C901 pin list
in the same slice. No behavior, public type, event/policy/command semantics,
dependency, unchecked `Any`, `type: ignore`, Mypy exclusion, or new pin.

Landed shape: each value object's `__post_init__` now delegates first to a
primitive field/type/shape validator and then to one named cross-field
invariant validator: interruption/malformed policy payloads, terminal
failure/retry/cancellation outcomes, and command-kind/action/argument
contracts, respectively. The original statements retain their order, exception
classes, and messages. All three files are C901-clean and their pin entries are
deleted, lowering the repository baseline 142/70 -> 139/67 (`src` 126/61 ->
123/58). No pin or exclusion was added. Focused policy/event/command and
architecture verification passed 372 tests; final `just check` passed Ruff,
Mypy, and 4,509 tests (2 skipped); `just docs-build` reported no issues.
Review: Pi GPT-5.6 Sol, 1 round, 0 findings, explicit CLEAN.

### Callable coding-command effects adapter relocation (Slice 7.5e) — DONE (2026-07-23)

Fifth composition-root slimming cut. Relocate the private callable-backed
implementation of `CodingCommandEffects` from `native.tool_loop_session` to
`native.coding.session_controller`, beside the port and dispatch precedence it
implements. Keep it private and callback-only: the strict coding owner must not
gain product UI, resource, extension, session, provider, or persistence
imports. The root constructs the imported adapter with the same six live
callables and deletes its local class. Preserve built-in > resource > extension
ordering, diagnostics, footer refresh, invocation counting, reload-visible
resolution, and every command outcome. No public export, behavior change,
dependency, unchecked `Any`, `type: ignore`, C901 pin, or Mypy exclusion.

Landed shape: the byte-identical six-callable adapter now lives privately as
`_CallableCodingCommandEffects` beside the strict `CodingCommandEffects` port;
it is not exported. The product root imports and constructs it with the same
diagnostic, footer, built-in interpretation, resource-count, resource-resolver,
and extension-resolver callables, so live `/reload` rebinding remains visible.
The coding owner's import set remains headless and unchanged in kind, and the
superseded root class is deleted. The composition root falls from 6,238 to
6,182 lines. Repository C901 stays 139/67 and `src` `type: ignore` stays 32;
no pin/exclusion was added. Focused coding/session/import-boundary verification
passed 558 tests; final `just check` passed Ruff, Mypy, and 4,509 tests
(2 skipped); `just docs-build` reported no issues. Review: Pi GPT-5.6 Sol,
1 round, 0 findings, explicit CLEAN.

### Strict Mypy gate for extension discovery (Slice 7.6b) — DONE (2026-07-23)

Second strict-root follow-on slice. Add `pipy_harness.native.extensions` to the
existing enumerated strict-Mypy override and verify the real repository
typecheck remains clean. A standalone `--follow-imports=skip` audit reports one
apparent `no-any-return` at the local import of typed
`resource_enablement.is_resource_enabled`; the normal repository graph follows
that module and proves the bool return, so do not add a cast, coercion, runtime
guard, or source churn for the audit artifact. Preserve discovery ordering,
package filters, containment, metadata projection, and extension contracts. No
`strict = true`, relaxed flag, exclusion, dependency, unchecked `Any`,
`type: ignore`, C901 pin, or behavior change.

Landed shape: `pipy_harness.native.extensions` now participates in the existing
enumerated strict-Mypy override with no source edit. The real `just typecheck`
graph follows the typed `resource_enablement` dependency and stays clean; the
skipped-import audit artifact was deliberately not encoded as a cast,
coercion, guard, or ignore. The explanatory strict-frontier comment advances
with the module, while every sub-flag remains unchanged. Focused extension
discovery/package and import-boundary verification passed 206 tests; final
`just check` passed Ruff, Mypy, and 4,509 tests (2 skipped);
`just docs-build` reported no issues. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### C901 provider preflight and response-text decomposition (Slice 7.7b) — DONE (2026-07-23)

Second directional complexity batch. Remove the single C901 finding from each
of the strict Azure Responses and OpenAI Chat Completions adapters by hoisting
their ordered model/base-url/auth configuration preflight into a named typed
helper, and remove the single finding from the shared OpenAI Responses wire
translator by isolating its nested message/content text traversal. Preserve
the first failure selected, environment/header read timing, request bytes,
error metadata, parsed output ordering, and tool/usage results. Each clean file
must leave the C901 pin list. No protocol consolidation, behavior change,
dependency, unchecked `Any`, `type: ignore`, Mypy exclusion, or new pin.

Landed shape: the Azure adapter's typed preflight resolves its base URL,
deployment, API key, explicit-auth state, and ordered header snapshot once
after cancellation while retaining model -> base URL -> auth failure order;
the OpenAI Completions preflight retains model -> auth order, the untrimmed
request model, and trimmed key. The shared Responses translator delegates only
its nested message/content traversal to a pure ordered chunk collector.
Golden fixtures remain byte-identical. All three files are C901-clean and
their pins are deleted, lowering repository C901 139/67 -> 136/64 (`src`
123/58 -> 120/55). Focused provider/fixture verification passed 46 tests;
final `just check` passed Ruff, Mypy, and 4,509 tests (2 skipped);
`just docs-build` reported no issues. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### Strict Mypy gate and OAuth typing for REPL state (Slice 7.6c) — DONE (2026-07-23)

Third strict-root follow-on slice. Add `pipy_harness.native.repl_state` to the
enumerated strict override; type the catalog row lists and failed extension
provider method; narrow generic prompt mappings without hiding `Any`; and type
extension OAuth helpers with `RegisteredProvider`, proving the OAuth-map
invariant before use. Remove all three `attr-defined` ignores from this module,
taking the repository's `src` ignore count below 30 through real typing.
Preserve model/provider resolution, interactive prompts, OAuth login/logout and
credential storage, fallback failures, and every CLI/TUI message. No
`strict = true`, relaxed flag, cast hiding `Any`, new ignore, exclusion,
dependency, C901 pin, or behavior change.

Landed shape: `pipy_harness.native.repl_state` now participates in the
enumerated strict override. Catalog rows use `NativeModelSpec`; the failed
extension provider fully implements the typed provider signature; and prompt
selection gives the validated mapping an `object`-typed view, retaining
arbitrary IDs without a cast. Both OAuth helpers accept `RegisteredProvider`
and locally assert the catalog's non-None OAuth-map invariant before invoking
the callback. The three `attr-defined` ignores are deleted with no replacement,
lowering `src` `type: ignore` from 32 to 29. Existing provider-registry
attributes remain explicit re-exports under `no_implicit_reexport`. Focused
REPL/provider/OAuth/settings/import-boundary verification passed 247 tests;
final `just check` passed Ruff, Mypy, and 4,509 tests (2 skipped);
`just docs-build` reported no issues. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### Custom-entry renderer helper relocation (Slice 7.5f) — DONE (2026-07-23)

Sixth composition-root slimming cut. Relocate the custom-message payload,
stored custom-entry payload, redraw-row type, and pure active-branch redraw
projection from `native.tool_loop_session` into `native.extension_runtime`,
beside registered message/entry renderers, `safe_custom_entry_data`, and the
extension render dispatchers they compose. Keep the stateful
`_CustomEntryRenderer` terminal adapter in the root, importing the four moved
helpers. Repoint direct tests and delete the superseded definitions. Preserve
Pi-shaped payload keys, sanitization, entry/message filtering, renderer
metadata, fallback lines, ordering, styled/plain tags, and terminal redraw
bytes. No public extension surface, behavior change, dependency, unchecked
`Any`, `type: ignore`, C901 pin, or Mypy exclusion.

Landed shape: the four private definitions now live only in
`native.extension_runtime`; the redraw function imports the two session-tree
entry classes locally for cycle-safe runtime checks, while postponed
annotations retain strict types. The root imports the payload/redraw helpers
for its unchanged stateful `_CustomEntryRenderer`, and direct tests import the
extension owner. Payloads, sanitization, branch order, tags, metadata rows, and
fallback lines are unchanged. The composition root falls from 6,182 to 6,100
lines. Repository C901 stays 136/64 and `src` `type: ignore` stays 29; no
pin/exclusion was added. Focused renderer/session/import-boundary verification
passed 311 tests; final `just check` passed Ruff, Mypy, and 4,509 tests
(2 skipped); `just docs-build` reported no issues. Review: Pi GPT-5.6 Sol,
1 round, 0 findings, explicit CLEAN.

### Strict Mypy gate for the top-level CLI (Slice 7.6d) — DONE (2026-07-23)

Fourth strict-root follow-on slice. Add `pipy_harness.cli` to the enumerated
strict override. The measured strict surface is one stale
`type: ignore[attr-defined]` on argparse's dynamically reached subparser action
list; `getattr` already gives that private compatibility path the permissive
type it needs, so strict Mypy reports the ignore as unused. Delete it rather
than replacing it. Preserve parser construction, help/list output, routing,
trust startup, automation, native provider/model selection, exit codes, and
all CLI messages. No `strict = true`, relaxed flag, new ignore, exclusion,
dependency, C901 pin, or behavior change.

Landed shape: `pipy_harness.cli` now participates in the enumerated strict
override. Its sole measured strict finding was the unused argparse private-path
ignore; deleting only that comment leaves the `getattr` compatibility walk and
all parser logic byte-identical. No replacement cast or ignore was added,
lowering `src` `type: ignore` from 29 to 28. The strict-frontier comment
advances with the module and every sub-flag remains unchanged. Focused
top-level/native/automation/session CLI verification passed 122 tests; final
`just check` passed Ruff, Mypy, and 4,509 tests (2 skipped);
`just docs-build` reported no issues. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### C901 provider thinking and Cloudflare preflight decomposition (Slice 7.7c) — DONE (2026-07-23)

Third directional complexity batch. Remove the single C901 finding from the
strict Anthropic Messages and Bedrock adapters by isolating each adapter's
coherent thinking-wire mutation, and remove Cloudflare's single finding by
hoisting its ordered model/endpoint/account/auth configuration preflight into a
typed resolved value. Preserve adaptive/budget/disabled thinking shapes,
GovCloud display omission, first-failure order, credential/header reads,
request/signature bytes, errors, output, tools, and usage. Each clean file must
leave the pin list. No provider consolidation, behavior change, dependency,
unchecked `Any`, `type: ignore`, Mypy exclusion, or new pin.

Landed shape: adapter-local Anthropic and Bedrock request-body builders own
tool serialization plus their distinct thinking mutations; Anthropic retains
adaptive/budget/disabled shapes and Bedrock retains adaptive/budget shapes with
GovCloud display omission. Cloudflare's typed preflight carries the resolved
model, URL, trimmed token, explicit-auth decision, and ordered header snapshot
from the unchanged post-cancellation validation phase. Golden request and
Bedrock SigV4 fixtures remain byte-identical. All three files are C901-clean
and their pins are deleted, lowering repository C901 136/64 -> 133/61 (`src`
120/55 -> 117/52). Focused provider/fixture verification passed 50 tests;
final `just check` passed Ruff, Mypy, and 4,509 tests (2 skipped);
`just docs-build` reported no issues. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### C901 extension-runtime boundary decomposition (Slice 7.7d) — DONE (2026-07-23)

Fourth directional complexity batch, bounded to the existing extension runtime
owner. Decompose its six measured findings along existing semantic families:
provider model/OAuth normalization; message and entry renderer invocation plus
component coercion; boolean/string flag token parsing; preloaded-contribution
collision/commit; and module activation resolution/execution/contribution
commit. Preserve exact validation/failure order, fail-soft/fail-closed
boundaries, interrupt propagation, registration ordering, staged-state
atomicity, payload rendering, flag errors, and activation metadata. The file
leaves the C901 pin list only when all six functions are honestly clean. No
extension API redesign, behavior change, dependency, unchecked `Any`,
`type: ignore`, Mypy exclusion, or new pin.

Landed shape: provider name/model/default/OAuth normalizers retain the original
validation and callback short-circuit order; renderer invocation/component
coercion helpers retain one-call, fail-soft, awaitable-close, and interrupt
semantics; typed flag-token results retain exact consumption/errors; and typed
contribution bundles separate ordered collision checks from all-at-once
commits for preload and ordinary activation. Entry resolution/execution remain
inside the fail-closed boundary. All six original functions are complexity
4–6, no new finding appears, and the file's pin is deleted. Repository C901
falls 133/61 -> 127/60 (`src` 117/52 -> 111/51). Focused verification passed
303 tests; activation, extension gate, entry/message renderer,
provider, and tool conformance scripts all passed; final `just check` passed
Ruff, Mypy, and 4,512 tests (2 skipped); `just docs-build` reported no issues.
Review: Pi GPT-5.6 Sol, 1 round, 0 findings, explicit CLEAN.

### C901 Codex transport and event decomposition (Slice 7.7e1) — DONE (2026-07-23)

Fifth directional complexity batch, bounded to the transport-facing half of
`native.openai_codex_provider`. Decompose the four measured findings in
`WebsocketsSyncClient.post_events`, its event iterator,
`UrllibSseHTTPClient.post_sse`, and `_parse_response_events` along genuine
connection setup, stream-read normalization, event-family accumulation, and
terminal finalization seams. Preserve exact WebSocket/SSE request bytes and
headers, cancellation registration and precedence, retryability metadata,
fallback eligibility, progress marking, sink/event order, first-terminal
authority, iterator closing, error sanitization, tool-call assembly, and usage
extraction. This partial owner slice does not remove the file pin; the provider
request/retry/error half follows independently. No protocol redesign,
provider consolidation, behavior change, dependency, unchecked `Any`,
`type: ignore`, Mypy exclusion, or new pin.

Landed shape: typed WebSocket and streaming-HTTP protocols make the optional
runtime boundaries explicit. WebSocket handshake, send, receive, interruption,
registration, and close handling are separate helpers; SSE opening/status
normalization and lazy event iteration/cleanup are separate helpers.
Transport-neutral response assembly uses one typed accumulator with distinct
content, function-call, terminal, status, and finalization helpers. Exact
cancellation precedence, progress marking, first-terminal closure, sanitized
errors, retry/fallback metadata, sink ordering, and tool-call assembly remain
covered by the existing focused tests. The four target functions and every new
helper are C901-clean; the four deferred request/retry/error findings keep the
file pinned for Slice 7.7e2. Repository C901 falls 127 -> 123 findings across
the same 60 files (`src` 111 -> 107 across 51). Focused verification passed
127 tests; final `just check` passed Ruff, Mypy, and 4,513 tests (2 skipped);
`just docs-build` reported no issues. Review: Pi GPT-5.6 Sol, 2 rounds,
1 finding, final round explicit CLEAN. Round one caught that a malformed
arguments-done event no longer reserved its valid item ID's source-order slot;
the reservation order was restored and a regression test added.

### C901 Codex request, retry, and error decomposition (Slice 7.7e2) — DONE (2026-07-23)

Sixth directional complexity batch, bounded to the remaining half of
`native.openai_codex_provider`. Decompose `OpenAICodexResponsesProvider.complete`,
its nested `_attempt`, `OpenAICodexHTTPStatusError.from_http_error`, and
`_parse_retry_after_seconds` along genuine completion preflight/request,
attempt-local retry/fallback, sanitized HTTP-error body/label extraction, and
header-duration parsing seams. Preserve exact validation/auth first failure,
extension-header hook count and ordered request bytes, transport choice and
sticky fallback, connection-limit retry, progress/reset behavior, backoff and
cancellation, retry-after caps, error classes/messages/metadata, result
timestamps, output/tools/usage, and all body-read privacy boundaries. The file
must leave the C901 pin list only when every original and new helper is below
the threshold. No provider redesign, public export, behavior change,
dependency, unchecked `Any`, `type: ignore`, Mypy exclusion, or new pin.

Landed shape: `_prepare_codex_completion` preserves model-before-auth
validation and delegates body/header/client construction to a frozen typed
configuration. `_OpenAICodexAttemptRunner` owns the former closure's mutable
attempt/progress state and separates SSE, WebSocket, connection-limit,
fallback, retry classification/delay, sleep, and failure-result concerns.
HTTP-error body reading and bounded API-label projection are distinct
cancellation-aware helpers; Retry-After milliseconds, numeric seconds,
HTTP-date parsing, and finite/nonnegative bounding are distinct helpers. A new
test pins model validation before any auth call. The owner is C901-clean and
its pin is deleted, lowering repository C901 123/60 -> 119/59 (`src` 107/51
-> 103/50). Focused verification passed 128 tests; final `just check` passed
Ruff, Mypy, and 4,514 tests (2 skipped); `just docs-build` reported no issues.
Review: Pi GPT-5.6 Sol, 1 round, 0 findings, explicit CLEAN.

### External-abort provider-turn adapter relocation (Slice 7.5g) — DONE (2026-07-23)

Seventh composition-root slimming cut. Relocate the private
`_AbortCallbackSignal`, `_StartGatedProvider`, and `_wait_for_external_abort`
family from `native.tool_loop_session` into the existing strict, UI-free
`native.agent.provider_turn` owner. The composition root imports those private
boundary adapters and retains only terminal-UI interruption translation.
Preserve callback registration before provider start, immediate-abort
acceptance, post-done abort recheck, cancellation setting, unregister timing,
poll interval, start-gate forwarding, provider properties, exception
propagation, and RPC result ordering. Direct tests move to the owner; no
compatibility alias. No provider-turn redesign, behavior/public export change,
dependency, unchecked `Any`, `type: ignore`, C901 pin, or Mypy exclusion.

Landed shape: the runtime-checkable callback signal, start-gated ProviderPort
adapter, and external-abort waiter now live only in strict
`native.agent.provider_turn`; the composition root imports them and keeps its
terminal-UI interruption translators. The direct post-done accepted-abort test
moves to the owner. Callback registration/start release, abort acceptance and
recheck, cancellation/unregister ordering, polling, properties, and completion
forwarding are byte-identical. The composition root shrinks 6,100 -> 6,016
lines; C901 remains 119/59 and `src` `type: ignore` remains 28. Focused
provider-turn/session/automation verification passed 150 tests; architecture
boundaries passed 169 tests; final `just check` passed Ruff, Mypy, and 4,514
tests (2 skipped); `just docs-build` reported no issues. Review: Pi GPT-5.6
Sol, 1 round, 0 findings, explicit CLEAN.

### C901 workspace and pure-UI leaf decomposition (Slice 7.7f) — DONE (2026-07-23)

Seventh directional complexity batch. Remove the sole finding from each of
`native.workspace_context`, `native.ui.state`, and `native.ui.rendering` by
splitting candidate validation/materialization, assistant-vs-tool pure event
reduction, and assistant-vs-tool decision application. Preserve candidate
precedence/fallthrough, symlink containment and canonical dedup, bounded
reading/truncation labels, exact immutable UI transitions/decision order, and
renderer call/sink order. Every clean file leaves the pin list. No workspace
policy, UI contract, terminal behavior, public export, dependency, unchecked
`Any`, `type: ignore`, Mypy exclusion, or new pin.

Landed shape: workspace candidate directory resolution, containment/dedup
validation, and successful materialization are separate helpers; a typed
`partial` preserves ancestor label capture while satisfying the full Mypy
gate. The UI reducer separates assistant lifecycle from stateless tool events,
and the rendering adapter separates assistant from tool decisions while
retaining exhaustive `assert_never`. A new test pins read-error fallthrough,
successful-only seen-path mutation, and label timing. All three files are
C901-clean and leave the pin list, lowering repository C901 119/59 -> 116/56
(`src` 103/50 -> 100/47). Focused verification passed 89 tests (2 skipped);
final `just check` passed Ruff, Mypy, and 4,515 tests (2 skipped);
`just docs-build` reported no issues. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### C901 core file-tool decomposition (Slice 7.7g) — DONE (2026-07-23)

Eighth directional complexity batch. Remove the sole finding from each of the
strictly bounded `read`, `write`, and `ls` tool owners by separating target
resolution/preflight, filesystem read/write/list execution, content/child
validation, and result formatting. Preserve ToolArgumentError vs error-result
boundaries, exact first-failure order/messages, workspace/reference-root and
ignore/symlink policy, read privacy checks and truncation, create-only write
and diff emission, deterministic listing/filter/order/truncation, and provider
correlation. Every clean file leaves the pin list. No tool schema/policy,
archive, behavior, dependency, unchecked `Any`, `type: ignore`, Mypy
exclusion, or new pin.

Landed shape: `ReadTool` separates path resolution, target preflight,
stat/read, content validation, and line-before-byte excerpting; `WriteTool`
separates path/content argument validation, ordered destination preflight,
filesystem mutation, and post-success diff streaming; `LsTool` separates
target resolution, sorted listing, child visibility/containment, type
classification, row capping, and output formatting. Typed local target/failure
values preserve ToolArgumentError vs provider-visible error results. New tests
pin UTF-8 truncation order, path-before-content validation, and ignored-child
filtering before the row cap. All three owners are C901-clean and leave the pin
list, lowering repository C901 116/56 -> 113/53 (`src` 100/47 -> 97/44).
Focused verification passed 61 tests; final `just check` passed Ruff, Mypy, and
4,518 tests (2 skipped); `just docs-build` reported no issues. Review: Pi
GPT-5.6 Sol, 1 round, 0 findings, explicit CLEAN.

### Reload-unavailable provider adapter relocation (Slice 7.5h) — DONE (2026-07-23)

Eighth composition-root slimming cut. Relocate the private fail-closed
ProviderPort adapter used when `/reload` removes the active extension provider
from `native.tool_loop_session` into strict `native.repl_state`, beside the
existing failed-extension-provider adapter and provider/model selection state.
The composition root must only construct the relocated collaborator. Preserve
the selected provider/model identity, tool-call capability, exact
`ProviderUnavailableAfterReload` failure type and message, UTC start timestamp,
reload selection/state transitions, output, and event ordering. Delete the
superseded root definition in the same slice and add focused characterization
at the owning boundary; no compatibility alias. No reload or provider-state
redesign, public API or behavior change, dependency, unchecked `Any`,
`type: ignore`, C901 pin, or Mypy exclusion.

Landed shape: `UnavailableAfterReloadProvider` now lives only in strict
`native.repl_state` beside the existing failed-extension adapter; the
composition root imports and constructs it, and the superseded root definition
and now-unused imports are deleted. A direct owner test pins the active
provider/model identity, tool capability, ignored sinks and cancellation,
failed status, exact failure type/message, and current UTC timestamp while the
existing reload-flow tests continue to pin integration behavior. The
composition root shrinks 6,016 -> 5,989 lines. Repository C901 remains 113/53
(`src` 97/44) and `src` `type: ignore` remains 28; no ratchet grows. Focused
verification passed 137 tests; final `just check` passed Ruff, Mypy, and 4,519
tests (2 skipped); `just docs-build` reported no issues. Review: Pi GPT-5.6
Sol, 1 round, 0 findings, explicit CLEAN.

### C901 typed state/package/provider leaf decomposition (Slice 7.7h) — DONE (2026-07-23)

Ninth directional complexity batch. Remove the sole C901 finding from each of
the strict `native.coding.state`, `native.package_resources`, and
`native.provider_registry` owners through coherent message-family validation,
package-source resolution/materialization, and availability-policy evaluation
helpers. Preserve exact validation/failure order and messages; package entry
precedence, diagnostics, dedup timing, manifest fail-closed behavior, resource
ordering and filters; and every provider availability expression including
auto-default, environment truthiness, Vertex, Azure, and unknown schemes.
Every clean file must leave the pin list. No state/package/provider API or
behavior change, provider consolidation, dependency, unchecked `Any`,
`type: ignore`, Mypy exclusion, cosmetic branch-count gaming, or new pin.

Landed shape: exact user, assistant, and tool-result validators retain the
public canonical-message dispatcher and all recursive first-failure checks.
Package resolution now uses one typed ordered accumulator with separate
entry/source resolution, safe disabled-source recording, successful-source
deduplication, and manifest/resource materialization. Provider availability
separates static/login, named-environment, and compound Vertex/Azure policy
families while retaining unknown-policy false. New tests pin message-family
first failures and the distinction between repeated unresolved sources and a
resolved invalid-manifest source. All three owners are C901-clean and their
pins are deleted, lowering repository C901 113/53 -> 110/50 (`src` 97/44 ->
94/41). Focused verification passed 150 tests; final `just check` passed Ruff,
Mypy, and 4,521 tests (2 skipped); `just docs-build` reported no issues. The
first full run hit the documented unrelated PTY worker-timing flake, which
passed immediately in isolation; the complete rerun was green. Review: Pi
GPT-5.6 Sol, 1 round, 0 findings, explicit CLEAN.

### Extension-aware agent event adapter relocation (Slice 7.5i) — DONE (2026-07-23)

Ninth composition-root slimming cut. Relocate the agent-event-to-extension-
lifecycle wrapper from `native.tool_loop_session` into the existing
`native.extension_hooks` owner while leaving construction of the fixed
renderer/automation/product/archive/caller projection chain in the composition
root. The relocated adapter receives that immediate sink and adds only the
extension lifecycle projection. Preserve renderer-first synchronous ordering,
stop-on-first-failure behavior, caller/archive/product ordering, exact
agent/turn lifecycle mapping, observe-only `agent_settled`, mutable hook/flag
replacement, extension context, and JSON/RPC settled-event ownership. Delete
the root class and update direct tests/import boundaries with no alias. No
event, lifecycle, extension, automation, persistence, or privacy redesign;
no public SDK change, dependency, unchecked `Any`, `type: ignore`, C901 pin,
or Mypy exclusion.

Landed shape: private `_ExtensionLifecycleAgentEventAdapter` now lives in
`native.extension_hooks`, receives one already-composed immediate sink, and
owns only immediate-first delivery, canonical run/turn lifecycle mapping,
replaceable hook/flag snapshots, lifecycle dispatch context, and extension-only
settled notification. The composition root still explicitly assembles
renderer, optional automation, product persistence, metadata-only workflow
archive, and optional caller sinks in the original order. Direct tests move to
the owner and add immediate-before-extension/failure-stop plus dispatch-context
characterization. The root shrinks 5,989 -> 5,904 lines. Repository C901 stays
110/50 (`src` 94/41) and `src` `type: ignore` stays 28. Focused verification
passed 305 tests; the coordinator’s full Mypy gate caught one test-only opaque
driver typed as `object`, fixed with an explicit `ExtensionUiDriver` cast.
Final `just check` passed Ruff, Mypy, and 4,523 tests (2 skipped);
`just docs-build` reported no issues. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### C901 agent request/executor and resource-dispatch decomposition (Slice 7.7i) — DONE (2026-07-23)

Tenth directional complexity batch. Remove the sole C901 finding from each of
the strict `native.agent.request`, strict `native.agent.tools`, and
`native.resources` owners through coherent recursive schema-family validation,
interrupt-wait/outcome resolution, and skill-versus-template/custom-command
dispatch helpers. Preserve exact schema validation order/messages and recursive
shape rules; worker start/completion/cancellation/output-gate/join/exception
ordering and completion-versus-interruption precedence; and built-in
fallthrough, skill/list/load/reject behavior, template-before-command
precedence, expansion, metadata, labels, and provider text. Every clean file
must leave the pin list. No agent/tool/resource API or behavior change,
dependency, unchecked `Any`, `type: ignore`, Mypy exclusion, cosmetic
branch-count gaming, or new pin.

Landed shape: schema semantics now validate scalar keywords, exact-string
sequences, then recursive properties/items in the original order. The
interruptible executor moves only waiter invocation/normalization and its
failure cleanup into one method; worker lifecycle and every post-wait
cancellation/completion/join/result decision remain in place. Resource command
dispatch keeps top-level recognition/reserved fallthrough, delegates `/skill`
list/load/reject behavior, then preserves direct-template-before-custom-command
resolution. New schema characterization pins cross-family and recursive
first-error order; the existing executor race suite remains green. All three
owners are clean and their pins are deleted, lowering repository C901 110/50
-> 107/47 (`src` 94/41 -> 91/38). Focused verification passed 61 tests; final
`just check` passed Ruff, Mypy, and 4,526 tests (2 skipped);
`just docs-build` reported no issues. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### Footer/status effects relocation to chrome owner (Slice 7.5j) — DONE (2026-07-23)

Tenth composition-root slimming cut. Relocate `_FooterEffects` from
`native.tool_loop_session` into the existing `native.chrome` owner, injecting
the session’s current footer-text and footer-print callables so chrome does not
import the composition root or TUI. Preserve the coding-state snapshot reads,
TUI footer text, plain-stream/slash-menu eligibility, legacy separator/status
printing, usage/no-usage selection, stream width/color behavior, refresh
timing, and existing monkeypatch seams. Delete the root class with no alias and
add a direct owner characterization/import boundary if useful. No footer
format, status meter, TUI, provider, command, or architecture redesign; no
public API change, dependency, unchecked `Any`, `type: ignore`, C901 pin, or
Mypy exclusion.

Landed shape: private `_ChromeFooterEffects` now lives in `native.chrome` and
receives precisely typed footer-text/print callables plus structural footer-UI
and REPL-runtime ports, so chrome imports neither the root nor TUI. The root
injects its current bound `_footer_text`/`_print_footer` methods, preserving
their formatting logic and monkeypatch seams. Live coding-state reads,
TUI-only refresh, slash-menu legacy suppression, usage/no-usage calls, and
startup/command/provider/loop timing remain unchanged. The direct constant-time
state test now verifies the chrome owner and absence of the root class. The
composition root shrinks 5,904 -> 5,829 lines. Repository C901 remains 107/47
(`src` 91/38) and `src` `type: ignore` remains 28. Focused verification passed
418 tests; final `just check` passed Ruff, Mypy, and 4,526 tests (2 skipped);
`just docs-build` reported no issues. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### C901 auth/fake/Vertex provider decomposition (Slice 7.7j) — DONE (2026-07-23)

Eleventh directional complexity batch. Remove the sole C901 finding from each
of `native.auth_store`, `native.fake`, and strict
`native.providers.google_vertex` through coherent request-auth key/header
resolution, fake streaming/result assembly, and Vertex express-versus-ADC
configuration preflight helpers. Preserve auth priority and config-value call
order; fake cancellation/stream/reasoning/counter/fixture metadata/tool-call
semantics; and Vertex first-failure order, endpoint quoting, auth headers,
location/project handling, request bytes, thinking, error/result metadata,
tools, and usage. Every clean file must leave the pin list. No auth/provider
policy, wire format, fixture surface, behavior, dependency, unchecked `Any`,
`type: ignore`, Mypy exclusion, cosmetic splitting, or new pin.

Landed shape: request auth now separates credential priority, ordered
models.json/model header resolution, and auth-header failure/overwrite
finalization. The live complexity finding was `FakeNativeProvider.complete`;
it now separates cancellable entry, reasoning-before-text streaming,
status/stream final text, copied fixture metadata, and indexed tool-call
selection while `AutomationFakeProvider` remains untouched. Vertex uses frozen
typed configuration/failure values for model and Express/ADC preflight before
the unchanged request/response path. New tests pin resolver invocation/header
overwrite order, fake stream/metadata/counter behavior, and Vertex model ->
project -> token -> location failures. All three owners are clean and their
pins are deleted, lowering repository C901 107/47 -> 104/44 (`src` 91/38 ->
88/35). Focused verification passed 139 tests; final `just check` passed Ruff,
Mypy, and 4,531 tests (2 skipped); `just docs-build` reported no issues. The
first full run hit the documented unrelated PTY worker-timing flake, which
passed immediately in isolation; the full rerun was green. Review: Pi GPT-5.6
Sol, 2 rounds, 1 finding, final explicit CLEAN. Round one found that reasoning
callbacks could mutate fake status before the separately guarded text stream;
the second success check and a regression test were restored.

### C901 native request-invariant decomposition (Slice 7.7k) — DONE (2026-07-23)

Twelfth directional complexity slice, bounded to `native.models`. Decompose the
three request-value findings (`NativeReadOnlyToolRequest`,
`NativePatchProposal`, and `NativePatchApplyRequest`) into coherent identity,
policy/sandbox, bounded-count/operation, privacy-storage, and optional-scope
invariant helpers. Preserve exact constructor validation order, exception
types/messages, exact enum/object requirements, path overlap timing, and every
metadata-only/privacy fail-closed rule. The file leaves the pin list only when
all three original and new helpers are below threshold. No model/schema/public
API or serialization change, loosened validation, dependency, unchecked
`Any`, `type: ignore`, Mypy exclusion, cosmetic splitting, or new pin.

Landed shape: shared typed helpers now own pipy identity, read-only and patch
policy/sandbox rules, bounded fields and labels, patch-operation shape and
rename/delete constraints, ordered overlap/path limits, false privacy-storage
fields, and optional scope validation. The three class dispatchers retain the
original first-failure order; new tests pin rename self-overlap and
per-operation validation before the distinct-path limit. The owner is clean
and its pin is deleted, lowering repository C901 104/44 -> 101/43 (`src`
88/35 -> 85/34). Focused verification passed 56 tests; final `just check`
passed Ruff, Mypy, and 4,533 tests (2 skipped); `just docs-build` passed.
Review: Pi GPT-5.6 Sol, 1 round, 0 findings, explicit CLEAN.

### Strict Mypy gate for the tool-loop composition root (Slice 7.6e) — DONE (2026-07-23)

Fifth strict-root follow-on slice. Add
`pipy_harness.native.tool_loop_session` to the existing enumerated per-module
strict override. The measured focused surface is already clean after the
adapter relocations, so this should be a ratchet-only change. Verify the full
Mypy graph and focused session/import boundaries; make no source change unless
the actual scoped gate requires a real typed fix. No `strict = true`, sub-flag
relaxation, ignore, cast hiding `Any`, exclusion, dependency, C901 pin, or
behavior change.

Landed shape: the composition root joins the enumerated strict override. Full
Mypy exposed seven intentional module-attribute compatibility/monkeypatch
seams; same-name import aliases make those re-exports explicit under
`no_implicit_reexport` with no runtime change. No other source edit was needed.
Focused verification passed 288 tests; final `just check` passed Ruff, Mypy,
and 4,533 tests (2 skipped); `just docs-build` passed. C901 remains 101/43,
`src` ignores remain 28, and the root remains 5,829 lines. Review: Pi GPT-5.6
Sol, 1 round, 0 findings, explicit CLEAN.

### Strict Mypy gate for the native TUI (Slice 7.6f) — DONE (2026-07-23)

Sixth and final requested strict-root follow-on. Add `pipy_harness.native.tui`
to the enumerated override and close its measured 28 errors: one tuple subclass
signature family, typed chrome-style returns, and the custom tool-render
dispatcher signature. Preserve all terminal rendering, styling, extension
renderer, input, and TUI behavior. No `strict = true`, relaxed flag, ignore,
unchecked `Any`, exclusion, dependency, C901 pin, or redesign.

Landed shape: the native TUI joins the enumerated strict override, completing
the requested strict-root frontier. Typed tuple construction, chrome-style
returns, pending custom-render state, and custom-render dispatch close the 28
measured errors. The render-details handoff gains a write-only protocol plus
separate typed terminal/captured sinks so manually injected non-mapping
details continue to reach extension renderers unchanged; a regression test
pins that compatibility seam. The full gate caught the composition-root
architecture guard at 805 lines after the first typed setup; a small
module-level sink-bundle helper restored `NativeToolReplSession.run()` to 798
AST lines. Focused verification passed 299 tests; final `just check` passed
Ruff, Mypy, and 4,534 tests (2 skipped); `just docs-build` passed. The first
full run encountered two PTY timing failures; the editor case passed in a
paired isolation run, the known multi-tool race passed alone, and the complete
rerun was green. C901 remains 101/43, `src` ignores remain 28, and the root
remains 5,829 physical lines. Review: Pi GPT-5.6 Sol, 3 rounds, 2 findings,
final explicit CLEAN. Round one caught the mapping-only internal reader type;
round two rejected widening the public `ToolRenderContext.details` contract.
The final shape keeps extension writes and the public context mapping-typed
while isolating legacy opaque reader values at one internal compatibility
bridge.

### Extension activation-bundle relocation (Slice 7.5k) — DONE (2026-07-23)

Eleventh composition-root slimming cut. Relocate `_ExtensionRuntime` and
`_activate_workspace_extensions` from `native.tool_loop_session` into the
existing `native.extension_runtime` owner, which already owns activation plus
every projected command, hook, tool, flag, provider, renderer, shortcut, and
outbox contribution. Repoint the root and direct source-loading tests, delete
the superseded definitions and unused imports, and preserve discovery,
enablement, reserved-name, batch-finalization, ordering, fail-closed
activation, and outbox identity exactly. No new boundary, public re-export,
behavior, dependency, `Any`, ignore, Mypy exclusion, or C901 pin.

Landed shape: the pure `_ExtensionRuntime` contribution bundle moves to
`native.extension_runtime`, while the session-run discovery/activation/
projection builder moves to the existing one-way `native.extension_hooks`
aggregation owner. The coordinator rejected the first implementation before
review because putting the builder in `extension_runtime` introduced reverse
lazy imports into `extension_hooks` and `extension_provider_catalog`; the
recovered placement preserves the documented cycle-free dependency direction.
The root imports the two private seams from their owners, the direct source-
loading test repoints, and the superseded definitions and unused imports are
deleted. Discovery inputs, reserved-name policy, enablement, activation-batch
finalization, ordered contribution projection, custom-message filtering, and
outbox list identity are unchanged. The composition root shrinks 5,829 ->
5,659 physical lines. Focused verification passed 352 tests plus fresh-process
import-order smoke checks; final `just check` passed Ruff, Mypy, and 4,534
tests (2 skipped). C901 remains 101/43 and `src` ignores remain 28. Review:
Pi GPT-5.6 Sol, 1 round, 0 findings, explicit CLEAN.

### Decompose strict agent/automation/clipboard leaves (Slice 7.7l) — DONE (2026-07-23)

Thirteenth directional C901 batch. Decompose the sole finding in each of three
already-strict owners along cohesive existing boundaries:
`agent.loop._validate_provider_result` by result identity/status/request
identity, scalar/timestamp/text fields, optional usage/metadata, and tool-call
validation; `automation.agent_events.AutomationAgentEventAdapter._project` by
canonical event families while preserving cumulative assistant text and the
unsupported-event failure; and `clipboard._default_run_capture` by bounded
pipe reading versus process cleanup/result classification while preserving
deadline, kill, cap, and non-zero-exit semantics. Add characterization only
where order or cleanup is not already pinned. Each clean file leaves the C901
pin list. No behavior, event/wire shape or ordering, clipboard security bound,
dependency, `Any`, ignore, Mypy exclusion, or new pin.

Landed shape: provider-result validation delegates the same ordered identity,
status/request, scalar/text, optional usage/metadata, and tool-call families.
Automation projection delegates run/turn/message, assistant text, tool,
retry, and ignored-bookkeeping event families while retaining cumulative text
reset/update timing and the unsupported-event TypeError. Clipboard capture
separates bounded deadline/select/read accumulation, cleanup, and result
classification while preserving the original `finally` cleanup and capped
overflow result. Existing characterization coverage required no change. All
three strict owners are clean and their pins are deleted, lowering repository
C901 101/43 -> 98/40 (`src` 85/34 -> 82/31). Focused verification passed 95
tests; final `just check` passed Ruff, Mypy, and 4,534 tests (2 skipped).
`src` ignores remain 28 and the root remains 5,659 lines. Review: Pi GPT-5.6
Sol, 1 round, 0 findings, explicit CLEAN.

### Custom-entry renderer adapter relocation to TUI (Slice 7.5l) — DONE (2026-07-23)

Twelfth composition-root slimming cut. Relocate the stateful
`_CustomEntryRenderer` from `native.tool_loop_session` into strict
`native.tui`, which already owns `ToolLoopTerminalUi`, live custom-component
rendering, chrome/theme projection, and the extension message/entry render
helpers. Describe the mutable run-state and diagnostic host through narrow
structural protocols so TUI never imports the composition root; continue
reading the same live state object after `/reload`/session rebinds. Repoint the
root construction and delete the superseded class/imports. Preserve custom
entry/message persistence, display, captured rendering, metadata, redraw,
outbox drain and `deliverAs`/`triggerTurn` queue timing exactly. No new module,
public re-export, behavior, dependency, `Any`, ignore, Mypy exclusion, or C901
pin.

Landed shape: `_CustomEntryRenderer` moves mechanically into strict
`native.tui`. Private read-only protocols expose only its live session-tree,
renderer-map, outbox, agent-turn, and diagnostic-host needs, so the same
mutable run state remains visible without a TUI-to-root import. The root
imports and constructs the private adapter exactly where it did before, and
the original class plus obsolete imports are deleted. A new architecture test
enforces that `native.tui` cannot import `native.tool_loop_session`; fresh
processes import both orders cleanly. Custom entry/message persistence,
captured and live rendering, metadata/redraw, outbox drain, and queue delivery
timing remain unchanged. The root shrinks 5,659 -> 5,382 physical lines.
Focused verification passed 283 tests; final `just check` passed Ruff, Mypy,
and 4,535 tests (2 skipped). C901 remains 98/40 and `src` ignores remain 28.
Review: Pi GPT-5.6 Sol, 1 round, 0 findings, explicit CLEAN.

### Close the Phase 7 quality burn-down disposition (Slice 7.8b) — DONE (2026-07-23)

Final coordinator-owned documentation slice. Refresh the Phase 7 disposition
and ledger with the measured end state; close the four-track burn-down without
claiming that the repository default is strict or that residual cross-boundary
or TUI state machines are honestly decomposable inside this scope. Record the
remaining C901 pins and one stdlib typing suppression with their rationale,
the completed requested strict-root frontier, and the composition-root
reduction. No production, configuration, test, behavior, or ratchet change.

Landed disposition: repository C901 closes at 39 findings / 13 pinned files
from 142/70 (`src` 23/4 from 126/61); the requested strict frontier covers all
six named root modules in addition to the strict leaf/provider/HTTP areas;
`src` `type: ignore` closes at 1 from 32 with its stdlib dynamic-subclass
limitation justified adjacent to the line; and the composition root closes at
5,085 physical lines from 7,626 (2,541 lines / 33.3% smaller). The architecture
ledger records the missing follow-on rows, the residual pin categories, the
scope distinction between the completed strict-root frontier and a repo-wide
strict flip, the remaining cross-boundary composition coordinators, and the
original no-extreme-complexity end-state as explicitly deferred residual risk.
No production/configuration/test change.
Final `just check` passed Ruff, Mypy, and 4,585 tests (2 skipped);
`just docs-build` completed cleanly. Review: Pi GPT-5.6 Sol, 2 rounds,
1 finding fixed, explicit CLEAN.

### Remove and justify the remaining source typing suppressions (Slice 7.8a) — DONE (2026-07-23)

Final `src` `type: ignore` sweep. Replace suppressions with real typing where
the runtime contract is statically expressible: preserve concrete append-entry
return types, typed awaitable/error transport, literal trust choices, extension
component protocols, and model override field types. Retain a suppression only
at a genuinely dynamic boundary (heterogeneous provider test-client injection
or stdlib `urllib` connection subclassing), and give every survivor its own
specific adjacent justification. Preserve runtime behavior, exception identity,
extension fail-soft handling, provider client injection, and cancellation
socket registration exactly. No looser annotation, unchecked `Any`, dependency,
Mypy exclusion, C901 pin, or behavior change.

Landed shape: a bounded generic preserves concrete session-entry subtypes;
typed awaitable worker state re-raises the original `BaseException`; the trust
selector carries the existing literal union; the custom overlay uses the
existing `CustomComponent` protocol; model overrides pass explicit typed
dataclass fields; and one shared `JsonHTTPClient` protocol plus a typed optional
kwargs shape covers every provider adapter and injected test client. A bounded
stdlib connection TypeVar removes the two `urllib.do_open` argument
suppressions. The only survivor is the runtime-selected `HTTPConnection`
subclass declaration, immediately justified as a Mypy limitation with the
runtime bound and override safety stated beside it. `src` `type: ignore` falls
27 -> 1. Focused verification passed 315 tests; final `just check` passed Ruff,
Mypy, and 4,585 tests (2 skipped); `just docs-build` completed cleanly.
Repository C901 remains 39/13 (`src` 23/4) and the root is 5,085 lines after
the literal trust-value typing expanded its declaration by five physical lines.
Review: Pi GPT-5.6 Sol, 1 round, 0 findings, explicit CLEAN.

### Decompose resource discovery, terminal decoding, and archive catalog (Slice 7.7w) — DONE (2026-07-23)

Twenty-fourth directional C901 batch. Decompose the seven findings across
`native._resource_files`, `native.terminal_driver`, and `pipy_session.catalog`
at source/candidate/load/frontmatter phases, scalar/control/escape decoding,
archive traversal/classification, record resolution, and metadata/event/
Markdown search. Preserve explicit/default/package precedence, containment and
first-wins dedup, filter-before-cap accounting, bounded reads and hashing,
frontmatter/body bytes, UTF-8 buffering, control and CSI/kitty key mappings,
archive privacy and symlink/finalization checks, ambiguity/error text, and
summary-safe search ordering. Add characterization only where precedence is
not already pinned. Each clean file leaves the C901 pin list. No behavior,
resource/session format, dependency, unchecked `Any`, ignore, Mypy exclusion,
or new pin.

Landed shape: resource discovery assembles typed sources, screens contained
candidates, performs bounded pre-cap loads, applies filter/name selection, and
hashes only accepted records; frontmatter delimiter, field, and body-newline
phases remain stdlib-only. Terminal decoding separates explicit control/scalar
aliases from CSI reads and paste/modifier/legacy classification without changing
UTF-8 buffering or key names. Archive verification separates partial-file and
per-entry classification, record resolution separates explicit paths from
name/stem matches, and search projects only allowlisted metadata, event
type/summary, and Markdown summary phases with the original fail-soft ordering.
All three owners are clean and their pins are deleted, lowering repository
C901 46/16 -> 39/13 (`src` 30/7 -> 23/4), meeting the directional sub-40
target. Focused verification passed 128 tests; final `just check` passed Ruff,
Mypy, and 4,585 tests (2 skipped); `just docs-build` completed cleanly. `src`
ignores remain 27 and the root remains 5,080 lines. Review: Pi GPT-5.6 Sol,
1 round, 0 findings, explicit CLEAN.

### Decompose coding-session control and provider construction (Slice 7.7v) — DONE (2026-07-23)

Twenty-third directional C901 batch. Decompose the five findings across
`native.coding.session_controller` and `native.provider_construction` at
closed-value validation, lifecycle-loop routing, built-in/resource/extension
dispatch, auth/routing/thinking resolution, and API-family provider
construction phases. Preserve true-idle settle and lifecycle ordering,
input/command precedence, fail-closed resource/extension outcomes, provider
auth/header/base-URL precedence, every thinking-format on/off request shape,
API-family selection, injected-client behavior, and exact errors. Add
characterization only where precedence is not already pinned. Each clean file
leaves the C901 pin list. No behavior, provider request/wire shape, dependency,
unchecked `Any`, ignore, Mypy exclusion, or new pin.

Landed shape: coding-loop step validation separates exact closed field types
from payload/EOF invariants; `run_loop` keeps its architecture-enforced literal
loop skeleton while delegating callable-port validation, and command dispatch
projects built-in, resource, extension, and unhandled phases in the original
precedence. Provider construction resolves thinking through typed
format-specific handlers for every mapped/off state, then delegates only the
authenticated API-family adapter selection while preserving lazy imports and
injected/default HTTP clients. Both owners are clean and their pins are
deleted, lowering repository C901 51/18 -> 46/16 (`src` 35/9 -> 30/7).
Focused verification passed 231 tests; final `just check` passed Ruff, Mypy,
and 4,585 tests (2 skipped); `just docs-build` completed cleanly. `src`
ignores remain 27 and the root remains 5,080 lines. Review: Pi GPT-5.6 Sol,
2 rounds, 1 finding fixed, explicit CLEAN.

### Decompose native session policy, reconstruction, and startup resolution (Slice 7.7u) — DONE (2026-07-23)

Twenty-second directional C901 batch. Decompose the eight findings across
`native.session`, `native.session_tree`, and `native.session_tree_commands` at
run orchestration, intent/read-only/patch validation, entry decoding, active
branch/context reconstruction, preview projection, and startup-mode resolution
phases. Preserve agent event ordering, safe metadata and privacy defaults,
tool/follow-up/patch/verification gate timing, session JSONL compatibility,
compaction/branch-summary replay, model/thinking reconstruction, terminal-safe
previews, local/global reference precedence, cross-project fork confirmation,
and exact errors and session-file mutation order. Add characterization only
where precedence or failure order is not already pinned. Each clean file leaves
the C901 pin list. No behavior, session/wire format, dependency, unchecked
`Any`, ignore, Mypy exclusion, or new pin.

Landed shape: the native run now delegates the tool phase, missing-intent
outcome, noop/read-only follow-up, and patch/verification phase through typed
results while retaining the original event and gate order. Intent, read-only
fixture, and patch-proposal validation separate identity/envelope, tool-policy,
privacy, count/label, and typed decode checks in their original precedence.
Session-tree loading separates fail-soft entry identity from typed entry
decoding; context reconstruction separates active-path walking, last-setting
selection, and compaction-aware message projection. Tree commands project
message/metadata previews separately and resolve continue, session-id, named,
and fork startup modes through narrow helpers with unchanged matching,
confirmation, errors, and mutation order. All three owners are clean and their
pins are deleted, lowering repository C901 59/21 -> 51/18 (`src` 43/12 ->
35/9). Focused verification passed 156 tests; final `just check` passed Ruff,
Mypy, and 4,580 tests (2 skipped); `just docs-build` completed cleanly. `src`
ignores remain 27 and the root remains 5,080 lines. Review: Pi GPT-5.6 Sol,
1 round, 0 findings, explicit CLEAN.

### Decompose tool rendering, diff application, and grep (Slice 7.7t) — DONE (2026-07-23)

Twenty-first directional C901 batch. Decompose the eight findings across
`native.tool_renderers`, `native.tools.edit_diff`, and `native.tools.grep` at
chrome coercion, tool-result/header projection, edit target/content/parse/
hunk-application, and grep search-root/backend/result phases. Preserve
renderer fail-soft and styled/plain output bytes/order, tool detail correlation,
diff validation/error and atomic-write ordering, hunk matching/newline
semantics, grep rg/stdlib parity, containment/ignore/binary/decode/limit rules,
and exact tool-result/error shapes. Add characterization only where precedence
is not already pinned. Each clean file leaves the C901 pin list. No behavior,
tool/wire shape, dependency, unchecked `Any`, ignore, Mypy exclusion, or new
pin.

Landed shape: tool rendering separates fail-soft extension invocation,
extension-detail correlation, chrome/header projection, and the default
result-tail/duration panel while preserving styled and plain output ordering.
Edit-diff parses typed file/hunk phases, validates targets and bodies
separately, and applies hunks through the same exact context/delete checks
before the original atomic write and streamed diff. Grep resolves a typed
search location, selects the `rg` or bounded stdlib backend, and projects the
same capped result/error shapes with the original containment, ignore, binary,
decode, and output limits. All three owners are clean and their pins are
deleted, lowering repository C901 67/24 -> 59/21 (`src` 51/15 -> 43/12).
Focused verification passed 93 tests; final `just check` passed Ruff, Mypy,
and 4,580 tests (2 skipped); `just docs-build` completed cleanly. `src`
ignores remain 27 and the root remains 5,080 lines. Review: Pi GPT-5.6 Sol,
1 round, 0 findings, explicit CLEAN.

### Decompose editor and extension candidate discovery (Slice 7.7s) — DONE (2026-07-23)

Twentieth directional C901 batch. Decompose the six findings across
`native.editor_completion` and `native.extensions` at query/search-root,
workspace-walk, source-tier enumeration, dedup/filter, and candidate inventory
phases. Preserve completion parsing/scoring/order/quoting, containment and
symlink/ignore rules, extension global/package/workspace/explicit precedence,
trust/default/filter handling, descriptor order/dedup, manifest/entry/name/API/
permissions validation, diagnostics, and fail-closed reasons. Add focused
characterization only where precedence or failure order is not already pinned.
Each clean file leaves the C901 pin list. No behavior, extension contract,
dependency, unchecked `Any`, ignore, Mypy exclusion, or new pin.

Landed shape: editor path completion resolves one typed search context, filters
and projects entries separately, bounds workspace directories/files through
policy helpers, and isolates raw-prefix target expansion. Extension discovery
builds ordered source tiers, streams explicit/default candidates through
package filtering and resolved-name first-wins dedup, classifies visible
entries, and inventories candidate, manifest, identity, entry, permissions/API,
and entry-file phases in their original failure order. New tests pin
explicit/workspace/global/package order, manifest-name dedup, unsafe-name
screening before symlink handling, and permissions before unsupported-API
failure. Both owners are clean and their pins are deleted, lowering repository
C901 73/26 -> 67/24 (`src` 57/17 -> 51/15). Focused verification passed 102
tests; final `just check` passed Ruff, Mypy, and 4,580 tests (2 skipped);
`just docs-build` completed cleanly. `src` ignores remain 27 and the root
remains 5,080 lines. Review: Pi GPT-5.6 Sol, 1 round, 0 findings, explicit
CLEAN.

### Decompose command and model resolution kernels (Slice 7.7r) — DONE (2026-07-23)

Nineteenth directional C901 batch. Decompose the seven findings across
`native.coding.commands`, `native.model_resolver`, and `native.models_json` at
closed-outcome invariant, glob-token, scope/direct-model resolution,
semantic-validation, and catalog-merge phases. Preserve exact exception/error
messages and first-failure order, outcome payload invariants, Pi-compatible
glob and thinking-level precedence, model order/dedup/fallback/warnings, and
models.json override/default/extension merge precedence and row identity.
Add characterization only where precedence is not already pinned. Each clean
file leaves the C901 pin list. No behavior, catalog/config shape, dependency,
unchecked `Any`, ignore, Mypy exclusion, or new pin.

Landed shape: command outcomes separate exact-field validation from
kind-specific payload/footer invariants, and dispatch resolutions apply the
same field then non-run payload checks. Model resolution tokenizes globs,
expands each scope pattern before ordered first-match dedup, and separates CLI
request preparation, exact/inferred resolution, parsed projection, and custom
fallback. Models.json delegates provider/model semantics and built-in,
request-config, and custom-row merge phases, with replacement retaining its
baseline position and new rows appending in config order. New tests pin
invariant first-failure order, scope expansion/level precedence, case-insensitive
explicit-provider stripping, model semantic precedence, and merge position.
All three owners are clean and their pins are deleted, lowering repository
C901 80/29 -> 73/26 (`src` 64/20 -> 57/17). Focused verification passed 295
tests; final `just check` passed Ruff, Mypy, and 4,578 tests (2 skipped);
`just docs-build` completed cleanly. `src` ignores remain 27 and the root
remains 5,080 lines. Review: Pi GPT-5.6 Sol, 1 round, 0 findings, explicit
CLEAN.

### Decompose REPL input editor and backend selection (Slice 7.7q) — DONE (2026-07-23)

Eighteenth directional C901 batch. Decompose the four findings in
`native.repl_input`: slash-menu editor key dispatch, escape/control key
decoding, explicit/automatic backend construction, and safe workspace path
completion. Preserve terminal state restoration, render/finalize/return and
exception order, all byte-sequence mappings and bounded reads, explicit
runtime errors versus automatic fail-soft fallback precedence, and completion
sort/containment/ignore/type-label behavior. Add focused characterization for
dispatch and fallback precedence where not already pinned. The clean file
leaves the C901 pin list. No behavior, TTY/output/completion shape, dependency,
unchecked `Any`, ignore, Mypy exclusion, or new pin.

Landed shape: the slash-menu editor separates raw-mode setup, read-until-done,
typed terminal actions, and menu/editing dispatch while retaining one finalizer
and unconditional original-termios restoration. Escape/CSI decoding and
ordinary control mapping are separate bounded paths. Explicit runtime
construction is isolated from automatic slash-menu > prompt-toolkit > readline
> plain attempts, and completion directory resolution is isolated from sorted
child projection. New tests pin forwarded auto-runtime options and fail-soft
precedence, all supported key encodings, menu dispatch plus termios calls, and
inside/outside symlink completion. The owner is clean and its pin is deleted,
lowering repository C901 84/30 -> 80/29 (`src` 68/21 -> 64/20). Focused
verification passed 75 tests; final `just check` passed Ruff, Mypy, and 4,572
tests (2 skipped); `just docs-build` completed cleanly. `src` ignores remain
27 and the root remains 5,080 lines. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### Decompose terminal-screen parsing and verification (Slice 7.7p) — DONE (2026-07-23)

Seventeenth directional C901 batch. Decompose the four findings in
`native.terminal_screen`: CSI cursor/clear/mode dispatch, SGR attribute
application, anomaly policy collection, and semantic visual-region
classification. Preserve ANSI parsing, cursor/wrap/clear/private-mode state,
SGR value consumption and unsupported-code behavior, anomaly ordering/text,
and visual-region precedence/order/summary bytes. Add focused
characterization for multi-value CSI/SGR and overlapping visual-region
precedence where not already pinned. The clean file leaves the C901 pin list.
No behavior, artifact shape, dependency, unchecked `Any`, ignore, Mypy
exclusion, or new pin.

Landed shape: CSI dispatch separates cursor movement from display/SGR/private
mode handling; SGR processing applies toggle, palette, reset, and truecolor
codes through small left-to-right helpers. Anomaly policy delegates the same
prompt/working/output/cursor/footer/input checks in their original append
order. Visual verification independently records separator/cursor rows before
one precedence-preserving semantic region classifier. New tests pin
overlapping prompt/tool/menu/footer classification and exact multi-anomaly TSV
order. The owner is clean and its pin is deleted, lowering repository C901
88/31 -> 84/30 (`src` 72/22 -> 68/21). Focused verification passed 12 tests;
final `just check` passed Ruff, Mypy, and 4,549 tests (2 skipped);
`just docs-build` completed cleanly. `src` ignores remain 27 and the root
remains 5,080 lines. Review: Pi GPT-5.6 Sol, 1 round, 0 findings, explicit
CLEAN.

### Decompose bash streaming, edit, and find tool owners (Slice 7.7o) — DONE (2026-07-23)

Sixteenth directional C901 batch. Decompose
`native.tools.bash._stream_output`, `native.tools.edit.EditTool.invoke`, and
`native.tools.find.FindTool.invoke` at cohesive streaming-state,
path/content-preflight, and search-root/result-projection boundaries. Preserve
timeout/cancellation/process-group cleanup and live-chunk order; edit
validation/error/write/diff order; and find pattern/path/ignore/glob/sort/
truncation semantics. Add characterization only where first-failure or cleanup
order is not already pinned. Each clean file leaves the C901 pin list. No
behavior, tool-result/wire shape, dependency, unchecked `Any`, ignore, Mypy
exclusion, or new pin.

Landed shape: bash streaming owns bounded raw/decoder/emission state and
separates the selectable read, post-EOF process wait, and post-kill drain while
retaining one deadline and cancellation precedence. Edit delegates validated
arguments, resolved target, bounded text read, replacement, write, and
post-write diff streaming through typed outcomes. Find delegates pattern
validation, search-root resolution, match containment/filtering, and output
projection. New tests pin decoder finalization/stdout cleanup, edit
argument/error and write-before-diff ordering, and sorted reference-root
projection with symlink and `.git` exclusion. All three owners are clean and
their pins are deleted, lowering repository C901 91/34 -> 88/31 (`src` 75/25
-> 72/22). Focused verification passed 50 tests; final `just check` passed
Ruff, Mypy, and 4,548 tests (2 skipped); `just docs-build` completed cleanly.
`src` ignores remain 27 and the root remains 5,080 lines. Review: Pi GPT-5.6
Sol, 1 round, 0 findings, explicit CLEAN.

### Relocate status, compaction-summary, and render-sink helpers (Slice 7.5n) — DONE (2026-07-23)

Fourteenth composition-root slimming cut. Relocate the context-budget,
reasoning-effort, cwd/branch-label, agent-history compaction-summary, and
extension render-details sink helpers from `native.tool_loop_session` into
their existing `native.chrome`, `native.agent.history`, and
`native.tool_renderers` owners. Repoint direct private-helper tests to those
owners; delete the superseded root definitions without aliases. Preserve exact
labels, fallback budgets, git traversal, summary bytes, sink identity, captured
versus TUI selection, and renderer behavior. No behavior, public export,
dependency reversal, unchecked `Any`, ignore, Mypy exclusion, or C901 pin.

Landed shape: the context-budget value and model budget/effort/cwd/branch
projections now live beside `BottomStatusFields` in `native.chrome`; the
count-only compaction summary lives beside `AgentHistoryCompaction`; and the
typed captured/TUI render-details sink selection lives beside the concrete
tool renderer. Direct private-helper tests import the new owners, render-sink
tests pin writer identity and opposite-sink absence, and the superseded root
definitions are deleted. The composition root falls 5,206 -> 5,080 physical
lines. Focused plus architecture verification passed 286 tests; final
`just check` passed Ruff, Mypy, and 4,543 tests (2 skipped);
`just docs-build` completed cleanly. Repository C901 remains 91/34 (`src`
75/25), `src` ignores remain 27, and no ratchet changed. Review: Pi GPT-5.6
Sol, 2 rounds, 1 finding (direct private imports preserved the former root
paths; fixed with qualified owner-module references), explicit CLEAN.

### Decompose resource discovery, terminal comparison, and schema validation (Slice 7.7n) — DONE (2026-07-23)

Fifteenth directional C901 batch. Decompose the sole finding in
`native.chrome.discover_loaded_resource_names`,
`native.terminal_compare.compare_screen_metrics`, and
`native.tools.base._validate_schema_shape` at cohesive category, comparison,
and schema-kind boundaries. Preserve resource discovery order, trust and
enablement semantics; metric/anomaly order and artifact bytes; and schema
validation error type, message, and first-failure order. Add characterization
only where those contracts are not already pinned. Each clean file leaves the
C901 pin list. No behavior, wire/artifact shape, dependency, unchecked `Any`,
ignore, Mypy exclusion, or new pin.

Landed shape: startup chrome delegates context, skill, and directory-store
discovery to category-specific helpers while preserving deduplication, limits,
loader/filter parity, trust, and fail-safe behavior. Terminal comparison now
separates per-frame delta collection from metric, viewport, prompt-background,
and visual-region anomaly projection without changing list order or artifacts.
Schema-shape validation dispatches to object/array/scalar validators while
retaining key, property, recursive, required, and additional-property
first-failure order and exact errors. All three owners are clean and their pins
are deleted, lowering repository C901 94/37 -> 91/34 (`src` 78/28 -> 75/25).
Focused verification passed 74 tests; final `just check` passed Ruff, Mypy, and
4,542 tests (2 skipped); `just docs-build` completed cleanly. `src` ignores
remain 27 and the root remains 5,206 lines. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### Decompose runner, trust, and read-only policy owners (Slice 7.7m) — DONE (2026-07-23)

Fourteenth directional C901 batch. Decompose the sole finding in
`runner.HarnessRunner.run`, `native.project_trust.resolve_project_trust`, and
`native.read_only_tool.NativeExplicitFileExcerptTool.invoke` at cohesive
lifecycle/policy/I/O boundaries. Preserve runner event/write/clock ordering and
archive redaction, project-trust winning-rung and fail-closed persistence/UI
semantics, and read-only approval/path/generated/stat/limit/read/content
validation first-failure order. Add characterization where timing/order is not
already pinned. Each clean file leaves the C901 pin list. No behavior,
privacy/trust weakening, session/archive or tool-result shape, dependency,
`Any`, ignore, Mypy exclusion, or new pin.

Landed shape: runner preserves its straight-line lifecycle and extracts only
the terminal-status-to-event mapping; a clock characterization pins every
event/finalize/result call. Project trust delegates extension-decision,
store-error, and UI-selection rungs while retaining exact precedence,
diagnostics, atomic updates, and fail-closed outcomes. Read-only invocation
uses typed path/stat/bounds preflight and content-read values before the same
result builder, retaining generated/path/existence/stat/read/content order and
pre/post-read limits. The first implementation wrapper hung after leaving an
overbroad lifecycle state-machine and formatter churn; the recovery removed
that design and minimized the diff before verification. New tests pin
remember-write failure before saved/UI fallbacks and generated-target rejection
before missing-file shape. All three owners are clean and their pins are
deleted, lowering repository C901 98/40 -> 95/37 (`src` 82/31 -> 79/28).
Focused verification passed 101 tests; final `just check` passed Ruff, Mypy,
and 4,537 tests (2 skipped). `src` ignores remain 28 and the root remains
5,382 lines. Review: Pi GPT-5.6 Sol, 1 round, 0 findings, explicit CLEAN.

### Session-tree command adapter relocation (Slice 7.5m) — DONE (2026-07-23)

Thirteenth composition-root slimming cut. Relocate `TreeCommandOutcome`, branch
summary selection, and captured `/tree` show/select/label/filter dispatch from
`native.tool_loop_session` into the existing
`native.session_tree_commands` owner. Inject only diagnostics and the optional
interactive selector callback so the owner remains TUI/root independent; keep
the live selector wrapper in the root. Split moved subcommands along their
actual semantics so the root's C901 finding disappears rather than moving to
the already-pinned owner. Type the selected entry with the existing
`SessionEntry` union and remove the attr-defined ignore. Preserve selection,
summary, mutation, rebuild, diagnostic, filter, and prefill order exactly. No
new module, public CLI surface, behavior, dependency, ignore, Mypy exclusion,
or C901 pin.

Landed shape: `TreeCommandOutcome`, branch-summary switching, and captured
show/select/label/filter/unknown dispatch move to
`native.session_tree_commands`. The owner accepts only diagnostic and optional
interactive-selector callbacks and gains an import rule forbidding TUI/root
dependencies. The root retains its live selector and a thin wrapper, with the
superseded outcome/summary/dispatcher bodies deleted. Moved subcommands own
cohesive helpers, so no finding moves into the already-pinned owner and the
root loses `_handle_tree_command` C901. Existing `SessionEntry` typing removes
the attr-defined ignore. Five new owner tests pin captured diagnostics,
selection/prefill/rebuild, summary cancellation/success, label/filter, and
interactive callback routing. The root shrinks 5,382 -> 5,206 physical lines;
repository C901 95/37 -> 94/37 (`src` 79/28 -> 78/28); `src` ignores 28 -> 27.
Focused verification passed 231 tests; final `just check` passed Ruff, Mypy,
and 4,542 tests (2 skipped). Review: Pi GPT-5.6 Sol, 1 round, 0 findings,
explicit CLEAN.

### Extension tool-port adapter relocation to the extension runtime (Slice 7.5d) — DONE (2026-07-23)

Fourth composition-root slimming cut. Relocate `_ExtensionToolPort` from
`native/tool_loop_session.py` into `native.extension_runtime`, which already
owns `RegisteredTool`, `ExtensionTool`, `ToolResult`, context construction, and
extension handler fail-soft policy. The composition root imports the adapter
for initial activation and reload, direct custom-renderer tests import the
extension owner, and the superseded definition is deleted. Preserve schema-
validated input, trusted-local handler execution, bounded error/output shaping,
model-runtime controls, flags/trust/UI context, render-details capture, and
provider correlation exactly. No permission redesign, feature, public surface,
dependency, unchecked `Any`, `type: ignore`, C901 pin, or Mypy exclusion.

Landed shape: the byte-identical `_ExtensionToolPort` now lives only in
`native.extension_runtime`; the composition root imports it for initial
activation and reload, and the direct render-details tests import the extension
owner. Obsolete root imports and the superseded class are deleted. The
composition root falls from 6,340 to 6,238 lines; repository C901 stays 142/70
and `src` `type: ignore` stays 32, with no pin/exclusion added. Focused
extension-tool/renderer/session/import-boundary verification passed 269 tests;
final `just check` passed Ruff, Mypy, and 4,509 tests (2 skipped);
`just docs-build` reported no issues. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### TUI tool-loop renderer relocation to the terminal owner (Slice 7.5c) — DONE (2026-07-23)

Third composition-root slimming cut. Relocate `_TuiToolLoopRenderer` from
`native/tool_loop_session.py` into `native.tui`, beside the
`ToolLoopTerminalUi` state it drives. Relocate the shared extension-renderer map
and plain tool-call header/argument-preview helpers into
`native.tool_renderers`, then consume them from both the terminal owner and the
composition root without a reverse import. Repoint direct TUI-renderer tests and
delete every superseded definition. Preserve streaming/reasoning/working state,
tool-call/result expansion and live-tail behavior, extension renderer
fail-soft fallback, spinner selection, event ordering, and all terminal bytes.
No feature, public surface, behavior redesign, dependency, unchecked `Any`,
`type: ignore`, C901 pin, or Mypy exclusion.

Landed shape: `_TuiToolLoopRenderer` now lives beside `ToolLoopTerminalUi` in
`native.tui`; the extension-renderer map plus plain tool-call header and
argument-preview helpers live in `native.tool_renderers`. The composition root
imports both collaborators and has no remaining renderer/helper definition.
Direct unit and real-PTY tests import the terminal owner. Mechanical comparison
to the pre-move class and helper block is exact. The composition root falls
from 6,653 to 6,340 lines; the TUI's existing 13 C901 findings and
`tool_renderers`' existing 3 remain unchanged, so repository C901 stays 142/70
without a new pin; `src` `type: ignore` stays 32. Focused TUI/renderer/
session/import-boundary verification passed 420 tests including the real-PTY
case; final `just check` passed Ruff, Mypy, and 4,509 tests (2 skipped);
`just docs-build` reported no issues. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### Line-oriented tool-loop renderer relocation to its rendering owner (Slice 7.5b) — DONE (2026-07-23)

Second composition-root slimming cut. Relocate the complete line-oriented
`_ToolLoopRenderer` implementation and its JSON tool-input coercion helper out
of `native/tool_loop_session.py` into the existing
`native/tool_renderers.py` owner. The composition root continues to construct
and type against the imported collaborator; the TUI renderer remains in place
for its own later slice. Repoint direct renderer tests to the owner while
preserving root-level monkeypatch seams that exercise composition. Preserve
every captured/non-TTY byte, ANSI/TUI detection rule, spinner/reasoning stream,
tool-call/result panel, custom extension renderer fallback, duration caption,
and error/cancellation path. No C901 gaming or behavior redesign; no new public
surface, runtime dependency, unchecked `Any`, `type: ignore`, C901 pin, or
Mypy exclusion.

Landed shape: the 850-line `_ToolLoopRenderer` and `_parse_tool_input`
definitions now live only in `native.tool_renderers`; the composition root
imports both collaborators and retains `_TuiToolLoopRenderer` for Slice 7.5c.
Direct renderer tests import the new owner while the root's imported binding
continues to support composition-level monkeypatch tests. A new import-boundary
rule prevents `native.tool_renderers` from importing `tool_loop_session` or
`tui`. Mechanical comparison against the pre-move class showed only its
now-redundant local self-imports removed. The composition root falls from 7,530
to 6,653 lines. Repository C901 remains 142/70: two findings move with the
class from `tool_loop_session` (7 -> 5) to the already-pinned
`tool_renderers` owner (1 -> 3), so no pin is added; `src` `type: ignore`
remains 32. Focused renderer/session/import-boundary verification passed 344
tests; final `just check` passed Ruff, Mypy, and 4,509 tests (2 skipped);
`just docs-build` reported no issues. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### Live extension UI driver relocation to the terminal owner (Slice 7.5a) — DONE (2026-07-23)

First directional Phase 7 follow-on cut. Relocate the concrete
`_LiveExtensionUiDriver` adapter out of the composition root
`native/tool_loop_session.py` and into `native/tui.py`, which owns the
`ToolLoopTerminalUi` boundary it delegates to. Repoint the composition root and
the direct chrome-driver characterization tests to the new owner, update the
extension-UI ownership documentation and import-boundary wording, and delete the
superseded definition in the same slice. Preserve every driver method,
`FooterData` snapshot, theme-selection result, branch-change callback, terminal
input listener, chrome/editor mutation, and TUI behavior byte for byte. No new
boundary, feature, public extension surface, runtime dependency, unchecked
`Any`, `type: ignore`, C901 pin, or strict-Mypy exclusion.

Baseline before the follow-on burn-down: repository-wide Ruff C901 is 142
findings across 70 pinned files (126 findings across 61 `src` files);
`src` contains 32 `type: ignore` uses; and
`native/tool_loop_session.py` is 7,626 lines.

Landed shape: `_LiveExtensionUiDriver` now lives beside
`ToolLoopTerminalUi` in `native.tui`; `tool_loop_session` imports the adapter
and only constructs it. The original class is deleted, the direct chrome-driver
tests import the terminal owner, the extension-UI module docstring and
architecture-boundary wording describe the new ownership, and the headless
`native.extension_ui` no-`tui`/no-`tool_loop_session` rule remains unchanged.
The composition root falls by 96 lines to 7,530; the C901 (142/70) and
`type: ignore` (32) ratchets are unchanged and no pin/exclusion was added.
Focused live-driver/import-boundary/theme/chrome-session verification passed
195 tests; final `just check` passed Ruff, Mypy, and 4,509 tests (2 skipped);
`just docs-build` reported no issues. Review: Pi GPT-5.6 Sol, 1 round,
0 findings, explicit CLEAN.

### Ruff C901 complexity gate at the Phase 7 baseline + final Phase 7 status (Slice 7.4) — DONE (2026-07-23)

Fourth and final Phase 7 cut: the complexity ratchet gate plus the recorded
Phase 7 completion disposition. A new `[tool.ruff.lint]` section in
`pyproject.toml` adds `extend-select = ["C901"]` (over `select`, so Ruff's
default E4/E7/E9+F set and therefore `just lint`'s prior behavior are preserved
exactly) and a `[tool.ruff.lint.per-file-ignores]` block pins C901 for every file
that already carried a finding, so `ruff check .` now fails on any new function
past mccabe's default complexity-10 threshold in a previously-clean file. The
pin is file-granular (Ruff has no per-function baseline) — an accepted,
documented burn-down limitation. The pre-gate baseline is measured, not the
132 planning-goal estimate (the original guardrail pins no count): 144 findings
across 72 files (128 `src`, 4 `tests`, 9 `scripts`, 3 `docs`). The slice honestly lowers the pinned baseline to 142
findings / 70 files by decomposing one genuinely separable function in each of
two single-finding files, dropping both off the pin list: `image_attachment.py`'s
`_resolve_one` (12) hoists its resolve/stat-check guard chain into a pure
`_prevalidate_candidate` helper, and `command_sandbox.py`'s `run_command` (11)
hoists its argv-parse / executable-resolution / path-policy preflight into a
`_resolve_invocation` helper. Both behavior-preserving (rejection reasons, spawn
confinement, byte-for-byte output shaping unchanged; pinned by
`test_command_sandbox.py` / `test_native_image_attachment.py`). The `<40` C901
and `<30` `type: ignore` end-states are explicitly NOT forced in one slice and
stay directional (142 C901, 32 `src` `type: ignore` remain). The
architecture-migration Completion Criteria gained a dated Phase 7 disposition.
Verification: `just lint` clean with C901 active and the baseline holding, the two
reduced functions under complexity 11, focused suites passed (47); final
`just check` (Ruff + mypy clean, 4,509 passed / 2 skipped) and `just docs-build`
green. No behavior/CLI/JSON/RPC/session-format change; no new runtime dependency,
`Any`, or `type: ignore`. Review: Pending review — Claude Opus panel
(user-directed substitution for the different-family gate).

### Strict Mypy gate extended to the providers package and http boundary (Slice 7.3) — DONE (2026-07-23)

Third Phase 7 cut, advancing the type ratchet frontier. The existing
`[[tool.mypy.overrides]]` strict block gains two module patterns —
`pipy_harness.native.providers.*` and `pipy_harness.native.http` — so
`just typecheck` (`mypy src tests`) now fails on any strict regression across the
provider adapters and the HTTP transport boundary as well as the four leaf
packages gated in 7.2. Clearing the ~3 resulting strict errors is
annotation-and-narrowing-only, no request/wire/behavior change: in
`native/http.py`, the `_RegisteringConnection` subclass keeps only its
still-required `type: ignore[misc]` (the now-unnecessary `valid-type` code and
the fully-redundant `super().connect()` ignore are dropped under
`warn_unused_ignores`), and `_usage_int` is reordered to a positive
`isinstance(value, int) and not isinstance(value, bool)` narrowing so the `int`
branch returns a narrowed `int` instead of `Any` (removing the
`no-any-return`; behavior is identical — bool and non-int still yield `None`).
Provider adapters (bedrock and siblings) import `HarnessStatus` from the
non-gated `pipy_harness.models`, whose implicit re-export stays legal exactly as
in 7.2 (`no_implicit_reexport` is governed by the exporting module), so no
provider source needed an `__all__` or import change. A pre-existing
HEAD-level redundant `cast(dict[str, Any], contents[-1])` in
`tests/test_native_attachment_provider_consumption.py` (already flagged by the
global `warn_redundant_casts` after mypy version drift since 7.2) is removed to
keep `just check` green. The heavier-debt root `native/` modules
(`session.py`, `extensions.py`, `tool_loop_session.py`), a repo-wide strict flip,
and a C901 gate stay out of scope. Focused attachment / every
`test_native_*_provider` / provider-cancellation / retry / http-transport-
primitives / usage / import-boundary suites passed (505+); automation-RPC
conformance (ALL PASS) and PTY smoke (8/8) passed; final `just check` (Ruff and
mypy clean across 420 sources, full suite green) and `just docs-build` are green.
No new runtime dependency, unchecked `Any`, or unexplained `type: ignore`.
Review: Pending review — Claude Opus panel (user-directed substitution for the
different-family gate).

### Strict Mypy gate for the ui/agent/coding/automation leaf packages (Slice 7.2) — DONE (2026-07-23)

Second Phase 7 cut and the first type ratchet gate. A new `[tool.mypy]` section
in `pyproject.toml` keeps the repository default non-strict but adds one
`[[tool.mypy.overrides]]` block opting the four fully-typed leaf packages
(`pipy_harness.native.ui`, `native.agent`, `native.coding`, `native.automation`,
wildcard patterns that also cover each package `__init__`) into
`--strict`-equivalent enforcement, so `just typecheck` (`mypy src tests`) now
fails on any strict regression there. The override lists the per-module strict
sub-flags explicitly rather than `strict = true`, because Mypy 1.20 applies a
per-module `strict = true` globally (it leaked the strict checks onto every
non-gated module and the whole test suite — 2,645 spurious errors); the explicit
flags stay scoped, and the two safe global-only components `warn_unused_configs`
and `warn_redundant_casts` (neither settable per-module) sit in the base
`[tool.mypy]`, keeping the gate genuinely `--strict`-equivalent. Clearing the
resulting strict errors is annotation-and-export-only, no
behavior/request/session-format change: `pipy_harness.models` needs no change —
`no_implicit_reexport` is enforced by the exporting module, and `models` is
non-gated, so its implicit re-export of `HarnessStatus` stays legal;
`native/agent/request.py`'s `_ProviderRequestMapping.__iter__` gains
`-> Iterator[str]` (and the request layer's exact-import allow-list gains
`collections.abc.Iterator`); `native/automation/run_modes.py`'s `_run_oneshot`
is narrowed `-> Any` → `-> AdapterResult`, removing both `no-any-return`s; and
`native/coding/session_controller.py` (a gated re-exporter of the registry
classifier) adds `classify_coding_command` to its `__all__` so the two
monkeypatch tests reading `controller_module.classify_coding_command` as a
module attribute (`tests/test_native_tool_loop_session_settings_command.py:141`,
`tests/test_native_tool_loop_session.py:2471`) survive `no_implicit_reexport`.
Providers, `native/http.py`, a repo-wide strict flip, and a C901 gate stay out
of scope (providers/http deferred to Slice 7.3). The shipped gate is
`just typecheck` (`mypy src tests`), clean with the scoped override — not a
blanket `uv run mypy --strict` per package, since a global `--strict` also
enables `no_implicit_reexport` on the non-gated `models` and would falsely flag
its legal implicit `HarnessStatus` re-export (`uv run mypy --strict
src/pipy_harness/native/coding` fails at `result.py:15` with `attr-defined`);
strict coverage of the four leaves was confirmed by injection instead. Focused
coding-result / automation-rpc /
automation-json-mode / automation-cli / agent-request-policy / request-boundary /
settings-command / tool-loop-session suites passed; `just check` (Ruff, mypy
strict-gated on the four packages and clean across 420 sources, full suite green)
and `just docs-build` are green. Review: Pending review — Claude Opus panel
(user-directed substitution for the different-family gate).

### Headless extension UI bridge module (Slice 6.4c) — DONE (2026-07-23)

Third Phase 6.4 cut: the deterministic, headless extension UI bridge moves
verbatim out of `extension_runtime.py` into the new
`native/extension_ui.py`, originals deleted with no shadow copy or alias.
Moved: `_CollectingUi` (the mode-aware `ExtensionUi` implementation handling
notices, dialogs, overlays, status/working controls, widgets, editor text,
autocomplete, and theme reads), the `_safe_ui_key` helper, the
`coerce_tool_render_lines` / `_LinesComponent` / `lines_component`
chrome-component helpers, and the `_CUSTOM_RENDER_MAX_CHARS` render-truncation
bound. The new module imports only the `extension_types` contracts
(`CustomComponentDriver` / `CustomComponentFactory` / `CustomComponentOptions` /
`ExtensionUiDriver` / `ToolRenderComponent` / `WidgetPlacement`) and the
`native.themes` registry helpers (`ChromePalette` / `NativeThemeStore` /
`available_theme_names` / `is_known_theme` / `resolve_active_theme_name` /
`resolve_palette`), so it never reaches `tool_loop_session` or `tui`.
`extension_runtime` re-imports `_CollectingUi` (body-used by
`make_extension_context` / `_ActivationApi`), `coerce_tool_render_lines`
(body-used by the surviving message/entry renderers), `_CUSTOM_RENDER_MAX_CHARS`
(body-used by `_coerce_rendered_lines` / `_bounded_render_text`), and
`lines_component` (`# noqa: F401` re-export); its now-unused `themes` import
block, `cast`, and `CustomComponentOptions` are dropped, and
`CustomComponentFactory` / `WidgetPlacement` / `ToolRenderComponent` become
body-unused `# noqa: F401` re-exports. `_LiveExtensionUiDriver` (coupled to
`ToolLoopTerminalUi`), `render_extension_message` / `render_extension_entry`, and
the tool-render dispatch stay in `extension_runtime` / `tool_loop_session`.
`pipy_harness.extensions` re-exports `coerce_tool_render_lines` /
`lines_component` byte-identically; extension-hooks and the direct-import tests
that pull `_CollectingUi` from `extension_runtime` stay valid and resolve to the
same object. The import-boundary suite adds `native.extension_ui` to every agent-
and coding-layer forbidden-import list and a dedicated rule asserting
`extension_ui` never imports `tool_loop_session` or `tui`. No notice / dialog /
widget / editor / theme semantics, deterministic non-interactive behavior, or
public-surface change; no new dependency, `Any`, or `type: ignore`. Focused
ui-notify / custom-ui / custom-editor / theme-controls / headless-host /
chrome-collecting / autocomplete / tool-render-contract / tool-render-dispatch /
import-boundary suites passed; `extension_ui_notify_conformance`,
`extension_conformance_gate`, and `extension_dispatch_conformance` reported ALL
PASS; the custom-ui PTY test and `just test-pty-smoke` (8/8) passed; `just check`
(Ruff, mypy clean, full suite green) and `just docs-build` are green. Review:
Pending review — Claude Opus panel (user-directed substitution for the
different-family gate).

### Extension UI protocol contracts leaf relocation (Slice 6.4a) — DONE (2026-07-23)

First Phase 6.4 cut: the extension UI protocol contracts move verbatim out of
`extension_runtime.py` into the `native.extension_types` leaf, originals deleted
with no alias. Moved: the `ExtensionUi` / `ExtensionUiDriver`
`@runtime_checkable` protocols, the `ToolRenderContext` frozen dataclass, the
`CustomComponent` protocol plus its `CustomComponentFactory` /
`CustomComponentOptions` / `CustomComponentDriver` aliases, and the
`WidgetPlacement` literal. This discharges the Slice 6.1b promise:
`ProjectTrustContext.ui` and `ExtensionTool.render_call` / `render_result` now
annotate leaf-local `ExtensionUi` / `ToolRenderContext`, so the
type-checking-only edge that imported those names from `extension_runtime` into
the leaf is DELETED, leaving only the `NativeSessionTree` forward reference.
`ChromePalette` (the `ExtensionUi.theme` / `get_theme` / `set_theme` annotation)
becomes a `TYPE_CHECKING`-only import from `native.themes` — annotation-only,
cycle-free. `extension_runtime` re-imports every moved name (`CustomComponent` /
`ToolRenderContext` body-unused → `# noqa: F401 - re-exported via
pipy_harness.extensions`; the rest stay body-used) and drops its now-unused
`MutableMapping` import; `pipy_harness.extensions` re-exports the public subset
byte-identically. `_CollectingUi`, `_LiveExtensionUiDriver`, the message/entry
renderers, and the remaining render/theme/component value objects stay in
`extension_runtime` for 6.4b. No signature, callback, default, hook-ordering, or
public-surface change; no new dependency, `Any`, or `type: ignore`. Focused
custom-ui/tool-renderer/project-trust/tools/headless-host/chrome-contract/
chrome-driver/theme-controls/autocomplete-provider/import-boundary suites passed
(257); `extension_conformance_gate`, `extension_tool_renderer_conformance`,
`extension_tools_conformance`, and `extension_ui_notify_conformance` reported ALL
PASS; `just check` (Ruff, mypy clean, full suite green) and `just docs-build` are
green. Review: Pending review — Claude Opus panel (user-directed substitution for
the different-family gate). The UI implementations/renderers remain 6.4b.

### Extension render/theme/component value objects leaf relocation (Slice 6.4b) — DONE (2026-07-23)

Second Phase 6.4 cut: the remaining rich-UI value-object vocabulary moves
verbatim out of `extension_runtime.py` into the `native.extension_types` leaf,
originals deleted with no alias. Moved: the `ThemeColor` literal, the
`ToolRenderTheme` / `ToolRenderComponent` `@runtime_checkable` protocols, the
`MessageRenderContext` / `EntryRenderContext` frozen dataclasses, the
`MessageRenderComponent` alias, the `RenderedCustomEntry` frozen dataclass, the
`ChromeComponent` protocol, and the `FooterData` frozen dataclass (its
`branch_change_registrar` field and Pi-shaped snake/camel method pairs
unchanged). This completes the extension UI contract vocabulary in the
stdlib-only leaf; only the concrete UI implementations (`_CollectingUi`,
`_LiveExtensionUiDriver`), the message/entry/tool-phase render logic, and the
`coerce_tool_render_lines` / `_LinesComponent` / `lines_component` helpers stay
in `extension_runtime`. `FooterData` adds one new stdlib leaf import,
`from types import MappingProxyType`; `extension_runtime` keeps every top-level
import it still uses. `extension_runtime` re-imports every moved name
(`ChromeComponent` / `FooterData` / `MessageRenderComponent` / `ThemeColor` /
`ToolRenderTheme` body-unused → `# noqa: F401 - re-exported via
pipy_harness.extensions`; the rest stay body-used) and `pipy_harness.extensions`
re-exports the public subset byte-identically. No field, ordering, default,
callback, hook-ordering, or public-surface change; no new dependency, `Any`, or
`type: ignore`. Focused tool-renderer / message-renderer / entry-renderer /
theme-controls / chrome-widgets / chrome-collecting / chrome-contract /
chrome-driver / chrome-session / import-boundary suites passed (288);
`extension_tool_renderer_conformance`, `extension_message_renderer_conformance`,
`extension_entry_renderer_conformance`, `extension_chrome_widgets_conformance`,
and `extension_conformance_gate` reported ALL PASS; `just check` (Ruff, mypy
clean, full suite green) and `just docs-build` are green. Review: Pending review
— Claude Opus panel (user-directed substitution for the different-family gate).

### Typed extension coding-session host port + headless fake-host coverage (Slice 6.3c) — DONE (2026-07-23)

Sub-slice 6.3c groups the eight loose coding-session parameters
(`complete_fn` / `append_entry_fn` / `set_session_name_fn` /
`get_session_name_fn` / `set_label_fn` / `send_message_fn` / `session_tree` and
the `messages` conversation snapshot) behind one frozen port owned by the
extension layer. The new `ExtensionCodingSessionControl` value object plus the
relocated `CompletionFn` / `AppendEntryFn` / `SetSessionNameFn` /
`GetSessionNameFn` / `SetLabelFn` / `SendMessageFn` aliases live in the
`native.extension_types` leaf (only `ExtensionCodingSessionControl` and the
public `CompletionFn` alias are re-consumed/re-exported by `extension_runtime`;
the five private aliases have no outside consumer and are not re-exported), and
the bundle is threaded as a single `coding_session` parameter through
`make_extension_context`, `dispatch_extension_command` /
`dispatch_extension_shortcut` / `_run_extension_handler`, and the shared
`_CommandContext` constructor; the loose parameters are deleted at each seam.
`_CommandContext` reads the six callables off the stored bundle and builds
`ctx.conversation` / `ctx.session_manager` from its `messages` / `session_tree`.
`_SessionCollaborators` gains one adapter, `coding_session_control()`, over its
`extension_complete` / session-name / label methods and the `_CustomEntryRenderer`
append/send writers plus the live `ctl.session_tree` and `coding_state.messages`,
built fresh per dispatch so the snapshot and a `/new` / `/resume` / `/fork` /
`/clone` rebind stay current; `dispatch_extension_effect` calls it directly and
`_ReplLoopStep.step_once`'s six loose callables collapse to one
`coding_session_control` factory param. The two `extension_hooks` sites pass a
narrow `ExtensionCodingSessionControl(messages=…)` /
`ExtensionCodingSessionControl(session_tree=…)`. A new import-boundary rule
asserts the four extension activation/host-port modules never import
`tool_loop_session`, and a new headless fake-host test drives a command and an
input hook against fake coding-session and model-runtime ports with no terminal
or concrete `NativeToolReplSession`. No change to capability semantics,
conversation-view contents, append/send ordering, event ordering,
session/JSON/RPC formats, or the public `pipy_harness.extensions` surface
(`ExtensionCodingSessionControl` is host-internal); no new dependency, `Any`, or
`type: ignore`. Focused dispatch/send-message/conversation/entry-renderer/
completion/shortcuts/answer-example/project-trust/headless-host/import-boundary
suites passed; `extension_conformance_gate`, `extension_dispatch_conformance`,
and `automation_rpc_conformance` reported ALL PASS; `just test-pty-smoke` 8/8;
`just check` (Ruff, mypy clean, 4,509 passed, 2 skipped) and `just docs-build`
are green. Review: Pending review — Claude Opus panel (user-directed substitution
for the different-family gate). The UI callables remain 6.4.

### Pure UI state reducer (Slice 4.1) — SHIPPED (2026-07-22)

Phase 4.1 introduces the terminal-free `native.ui` package. `native.ui.state`
adds the frozen `UiState` (`assistant_active`/`assistant_streamed`/
`assistant_completion_suppressed`) and the pure `reduce(state, event) ->
(UiState, tuple[RenderDecision, ...])`, which owns the assistant message
lifecycle and imports only the canonical `native.agent` `events`/`messages`/
`results` value types. The decision machine — start-and-reset, non-empty stream
accumulation, reasoning passthrough, `ProviderFailed`/`RunCancelled` suppression
(fail/cancel only while active, suppress always), and `MessageCompleted`
buffered-vs-streamed body + `has_tool_calls` + complete-once — is lifted verbatim
out of `RenderingAgentEventAdapter`, whose inline `_assistant_active`/
`_assistant_streamed`/`_assistant_completion_suppressed` booleans are DELETED.
The protocol and adapter move to `native.ui.rendering`. Slice 4.1b then folds
the three remaining tool-event renders into the same `reduce` (`ToolCallStarted`
-> `RenderToolCall`, `ToolCallUpdated` -> `StreamToolOutput`, `ToolCallCompleted`
-> `RenderToolResult` forwarding `output_text`/`is_error`/`duration_seconds`), so
`reduce` is the sole owner of every agent-event-to-render-decision mapping and the
adapter's `emit` becomes a pure driver — `reduce` then apply — with zero residual
inline event branching; tool events leave `UiState` untouched. Because a UI
projection now lives in `native.ui`, `native.agent_adapters` drops the renderer
protocol/adapter and its now-unused lifecycle event imports; `tool_loop_session`
and the three adapter/rendering/TUI tests import `RenderingAgentEventAdapter` from
`native.ui`. The declared `native.ui` import-boundary rule (forbidding
`coding.state`/`coding.session`/`tool_loop_session`) is now active against the
real tree. PTY-free `tests/test_native_ui_state.py` pins every decision,
including the three tool decisions with their exact argument forwarding and their
interleaving with the message lifecycle; `just test-pty-smoke` and
`automation_rpc_conformance.py` pass; `just check` (Ruff, mypy, 4436 passed/2
skipped, one load-dependent PTY timing flake off the reducer path passing in
isolation) and `just docs-build` are green. No CLI/JSON/RPC/session/extension
format, event-ordering, or terminal-behavior change; no new runtime dependency,
`Any`, or `type: ignore`. Footer/status and coding-state projection (Phase 4
boundary), terminal driver (Slice 4.2), and extension-UI relocation (Slice 6.4)
remain deferred.

### Agent-run collaborator adapter relocation — SHIPPED (2026-07-21)

Phase 3.1e (accepted-input and agent-run coordinator) begins with sub-slice
3.1e.1: relocate the three generic agent-loop collaborator wrappers from the
`native.tool_loop_session` monolith into the new strict-typed module
`native.coding.agent_run`. `AgentLoopRequestSourceAdapter`,
`AgentLoopProviderTurnAdapter`, and `AgentLoopStatusPolicyAdapter` are now public
typed adapters conforming to the canonical `native.agent.loop` request-source,
provider-turn, and status-policy protocols, with identical positional-only
signatures. `NativeToolReplSession.run()` imports and constructs them exactly as
before; the three superseded in-monolith classes are deleted with no alias or
re-export shim.

This is a behavior-preserving move only: no change to event ordering, provider
requests, request/provider-turn closures, status-callback timing, persistence
writes, or queue ownership, and no new runtime dependency, `Any`, or
`type: ignore`. The import-boundary gate adds an explicit
`native.coding.agent_run` rule allowing only canonical `native.agent` contracts
plus injected `native.coding.state`/`native.coding.input_queue`, and forbidding
UI/terminal, extensions, concrete providers/tools, persistence coordination,
automation/RPC, the SDK, capture, and the metadata-only workflow archive; an
exact direct-import allowlist pins the module's dependency surface.

Sub-slice 3.1e.2 (2026-07-21) adds `CodingAgentRunCoordinator` to the same
module. It receives the three relocated adapters plus the composed reusable-loop
ports (`tool_capabilities`, `tool_policy`, the emitter `event_sink`,
`run_effect_sink`, `usage_publisher`, `coding_input_queue.agent_loop_port`, and
an optional `tool_waiter`), the live `CodingSessionState`, and the input-queue
retention seam (`coding_input_queue.retain_agent_input`). A single
`run_turn(active_input, initial_tool_state, *, pricing, accepted_queued_input)`
method builds the canonical `AgentLoop`, constructs `AgentLoopRunInput` from
`coding_state.messages` plus the accepted input and initial tool state, invokes
`agent_loop.run(...)`, mirrors `outcome.final_history` back into session state,
and forwards `outcome.next_input` to the retention seam.
`NativeToolReplSession.run()` builds the coordinator once per accepted turn and
calls `run_turn(...)`; the inline `AgentLoop(...)` construction, the
`agent_loop.run(...)` call, and the post-run
`mirror_history`/`retain_agent_input` lines are deleted with no alias, and the
monolith no longer imports `AgentLoop`/`AgentLoopRunInput`. This is a
behavior-preserving move only: the request-preparation and provider-turn
closures remain composition-root policy in `run()`; queue
storage/ordering/reservation/idle/lifecycle stay with the 3.1a controller;
persistence writes remain Phase 3.3; and accepted-input preparation
(`active_input`/`initial_tool_state`) stays inline for Phase 3.1e.3. Event and
`agent_settled`/`agent_end` ordering, cancellation, terminate-session assembly,
and public formats are unchanged. The `native.coding.agent_run` boundary rule
and its exact allowlist are extended to the coordinator's added
`native.agent.loop`/`loop_policy`/`runtime_ports`/`tools`/`usage` contracts plus
`native.coding.state.CodingSessionState`, keeping the earlier forbidden surfaces
intact.

Sub-slice 3.1e.3 (2026-07-21) extracts the run()-inline accepted-turn
preparation into the new strict-typed module `native.coding.accepted_input`: the
frozen/slotted `CodingAcceptedTurn` DTO (`turn_user_message`, `active_input`,
`initial_tool_state`, `provider_user_input`, `turn_attachments`,
`agent_system_prompt`) and `CodingAcceptedInputPreparer`. A single
`prepare(*, user_input, resource_provider_text, selected_provider_content,
base_system_prompt)` call reproduces the exact prior logic behind injected
product ports — an input-hook transform, an `@file` resolver, an image-attachment
resolver, a `before_agent_start` suffix source (`str | None`, with the
single-newline concatenation owned by the preparer), a next-turn-context source,
a diagnostic sink, and a state recorder (`record_file_references`/
`record_image_attachments` plus the tool-counter snapshot via the module's
`CodingSessionAcceptedInputRecorder` over the live `CodingSessionState`). It
preserves the resource-vs-literal branch, the transformed-vs-original prompt
split, the hook ordering (input hook → `@file` → image → `before_agent_start`,
suffix before the next-turn-context read), the suffix appended exactly once, and
the diagnostic text. `NativeToolReplSession.run()` builds thin adapters over
`dispatch_input_hooks`, `resolve_file_references`, `resolve_image_attachments`,
`dispatch_before_agent_start_hooks`, `self._emit_diagnostic`,
`coding_input_queue.take_next_turn_context`, and the recorder, calls
`prepare(...)`, and unpacks the returned DTO — feeding
`active_input`/`initial_tool_state` into the 3.1e.2 coordinator; the inline block
and the now-unused `ProviderImageAttachment` import are deleted with no alias.
Behavior-preserving move only: the metadata-only workflow archive stays intact
(transformed provider text, `@file` excerpts, image bytes, and injected
system-prompt context ride the returned turn's provider-visible fields and never
enter JSONL/Markdown/archive), and queue ownership, persistence writes,
prompt-history recording, resource-turn skip semantics, event/settle ordering,
and public formats are unchanged. The `native.coding.accepted_input`
import-boundary rule (agent-run forbidden categories) plus an exact allowlist
admit only canonical `native.agent` contracts (`active_input`/`content`/
`loop_policy`/`messages`), `native.coding.state.CodingSessionState`, and the
`native.file_references`/`native.image_attachment` resolution data contracts.

Phase 3.1e is complete; the outer lifecycle/composition-shell cutover remains
Phase 3.1f.

Sub-slice 3.1f.1 (2026-07-21) establishes the shutdown transition's ownership
boundary. The bounded metadata-only `NativeToolReplResult` dataclass moved
verbatim from `native.tool_loop_session` into the new strict-typed headless
module `native.coding.result` (imports only stdlib +
`pipy_harness.models.HarnessStatus` +
`native.coding.state.CodingSessionResultSnapshot`), which adds the pure
projection `build_repl_result(snapshot, *, status, exit_code, started_at,
ended_at, error_type=None, error_message=None)`. It reproduces the two prior
inline builders byte-identically: the terminate `FAILED` non-image counter
subset carrying the unpacked loop failure through plain error strings (kept out
of the projection's imports because a malformed-fatal terminate's failure is not
the recorded `snapshot.provider_failure`), and the `SUCCEEDED` full subset
including image counters plus the projected
`provider_failure_type`/`provider_failure_message`.
`NativeToolReplSession.run()` calls the projection at both shutdown returns; the
in-monolith class body and both duplicated field-mapping blocks are deleted, and
`NativeToolReplResult` leaves the monolith's `__all__`. The public surface is
preserved as a legitimate public re-export — `native/__init__` re-exports
`NativeToolReplResult` from `native.coding.result` — and the two direct-import
test files were repointed. No `NativeToolReplResult` field name/default/type/value
changed, so CLI exit codes and JSON/RPC/SDK final-result payloads are unchanged;
the while-loop, input selection, true-idle firing, command dispatch,
run-transition wiring, and the `session_shutdown`/`agent_settled`/
`clear_extension_chrome` `try/finally` stay inline. The import-boundary gate adds
a `native.coding.result` `BoundaryRule` (agent-run forbidden categories) plus an
exact direct-import allowlist admitting only stdlib,
`pipy_harness.models.HarnessStatus`, and
`native.coding.state.CodingSessionResultSnapshot`. The controller class begins in
Phase 3.1f.2; the while-loop/composition-shell cutover remains the rest of
Phase 3.1f.

Sub-slice 3.1f.2 (2026-07-21) introduces the headless controller
`native.coding.session_controller.CodingSessionController` and gives it ownership
of the two most tightly-coupled outer transitions: input selection and the
true-idle (`agent_settled`) boundary. Its single `select_next_step(*,
settle_pending, drain_outbox, read_fresh_line, input_queued_input_port)`
reproduces the former top-of-loop block exactly — drain outboxes, take one queued
input through the 3.1a `CodingInputQueue` priority, fire `emitter.agent_settled()`
exactly once when nothing local-command/retained-fresh/provider-visible is
pending and re-drain/re-poll, otherwise read one fresh line and apply the
`classify_external_wake` overlay — and returns a frozen discriminated
`CodingLoopStep` (`LOCAL_COMMAND`/`RETAINED_FRESH`/`PROVIDER_CONTENT` with optional
`queued_input`/`FRESH_LINE`/`EOF` carrying `keyboard_interrupt`) plus the
post-boundary `settle_pending`. It is injected exactly the four ports named in
the cut (the already-owned `CodingInputQueue`, an outbox-drain callable, a
fresh-line reader callable wrapping `repl_input.read_line`, and the settled
emitter) plus its exact `CodingSessionState` anchor. `NativeToolReplSession.run()`
builds the controller once per run, calls `select_next_step` each iteration,
assigns `agent_settled_pending` back from the step, and prints the Ctrl-C newline
before breaking on an `EOF` step; the inline selection/settled/`classify_external_wake`/
EOF block is deleted with no alias and the now-unused `CodingInputSource` import
is dropped. The `while True` skeleton, separator print, footer text, `/tree`
prefill rehydration, command dispatch, run transition, result building, lifecycle
firing, and the `session_shutdown`/`agent_settled`/`clear_extension_chrome`
`try/finally` stay inline; `agent_settled_pending` remains a `run()` local so the
shutdown-time settle fire is byte-unchanged. Behavior-preserving move only: input
priority order, once-only `agent_settled` timing, drain-outbox ordering,
external-wake behavior, EOF/Ctrl-C handling, and CLI/JSON/RPC/TUI event ordering
are unchanged; no new runtime dependency, `Any`, or `type: ignore`. The
import-boundary gate adds a `native.coding.session_controller` `BoundaryRule`
(agent-run forbidden categories) plus an exact direct-import allowlist admitting
only stdlib, the canonical `native.agent` `content`/`runtime_ports` contracts,
`native.coding.input_queue`, and `native.coding.state.CodingSessionState`. The
remaining outer transitions (start/command/run) and the sub-800-line
composition-shell reduction remain the rest of Phase 3.1f.

Sub-slice 3.1f.3 (first cut, 2026-07-21) gives the controller ownership of the
built-in>resource>extension command-dispatch precedence *tail*. `native.coding.commands`
gains closed dispatch/resolution outcome contracts — `ResourceDispatchResolution`/
`ResourceDispatchKind` (LIST/REJECT/RUN), `ExtensionDispatchResolution`, and
`CommandDispatchResolution`/`CommandDispatchResolutionKind`
(CONTINUE_LOOP/PROCEED_TO_RUN carrying `user_input`/`resource_provider_text`/
`selected_provider_content`) — and `CodingSessionController.dispatch_command(*,
command_text, user_input, selected_provider_content, effects)` owns only the
ordering/precedence: resource dispatch first (list/reject consumed locally with
diagnostic + footer; run records the invocation counter, carries the bounded
provider text, paints no footer), then extension dispatch under the exact
`resource_provider_text is None` guard, then the byte-identical unhandled-`/`
notice, otherwise `PROCEED_TO_RUN`. Every effect runs through the new
`CodingCommandEffects` port (protocol defined with the controller; the concrete
`_CodingCommandEffectsAdapter` over run() closures stays composition-root and
maps the concrete `ResourceDispatch`/`ExtensionCommandDispatch` onto the narrow
resolutions). `NativeToolReplSession.run()` deletes the inline resource dispatch,
extension dispatch, and unhandled-`/` fallback (~95 lines) and replaces them with
one `dispatch_command(...)` call plus a `CONTINUE_LOOP`/`continue` branch and a
`resource_provider_text` read feeding the untouched run transition. Byte-identical
CLI/JSON/RPC/session behavior; no new runtime dependency, `Any`, or `type: ignore`.
The import-boundary gate un-forbids `native.coding.commands` for the
`session_controller` rule alone (filtering the shared agent-run forbidden set,
leaving `result`/`accepted_input` unchanged) and extends the controller's exact
allowlist with the four dispatch/resolution contracts. This first cut keeps the
built-in classification, the `/exit`/`/quit` `EXIT`, and the 29-branch `CONTINUE`
`CodingCommandAction` interpretation inline: those branches reassign `run()`-local
control state (`session_tree`, `tree_filter_mode`, `pending_prefill`, the
`/reload` extension-runtime bundle), so relocating them (and the `EXIT`/`CONTINUE`
outcome-kind routing) into `dispatch_command` needs the mutable effect-handler
design and is deferred to the next 3.1f cut, along with the start/run transitions
and the sub-800-line composition-shell reduction.

Sub-slice 3.1f.3 (continuation 1, 2026-07-21) gives `dispatch_command` the *full*
built-in>resource>extension precedence. `classify_coding_command` moved from the
inline `run()` guard into `CodingSessionController.dispatch_command(*, command_text,
stripped, user_input, selected_provider_content, effects)` and runs FIRST so a
resource/extension can never shadow a built-in: `/exit`/`/quit` resolve to the new
`CommandDispatchResolutionKind.EXIT_LOOP` (the loop breaks) and every other
continuing built-in to the new `INTERPRET_BUILTIN` variant carrying the classified
`CodingCommandOutcome` in the new `interpret_outcome` field, gated by the exact
former inline condition (`selected_provider_content is None or not stripped`,
threaded as the new `stripped` parameter). `CommandDispatchResolution` gains
`exit_loop()`/`interpret_builtin(outcome)` factories and validation (INTERPRET_BUILTIN
requires a CONTINUE outcome; `interpret_outcome` rejected on other kinds;
EXIT_LOOP/INTERPRET_BUILTIN carry no payload). `NativeToolReplSession.run()` calls
`dispatch_command` once at the former classification site and routes on the kind —
EXIT_LOOP breaks, INTERPRET_BUILTIN binds `command_outcome = resolution.interpret_outcome`
and runs the still-inline 29-branch interpretation byte-identically (no re-indent),
CONTINUE_LOOP continues, PROCEED_TO_RUN feeds the run transition — and deletes the
superseded inline `classify_coding_command` call, the inline `EXIT`-break, the
duplicate `dispatch_command` call site, and the now-unused monolith
`classify_coding_command` import. Byte-identical CLI/JSON/RPC/session behavior; no
new runtime dependency, `Any`, or `type: ignore`. The import-boundary gate extends
the `session_controller` exact allowlist with `classify_coding_command` and
`CodingCommandOutcomeKind`. Still deferred (needs the mutable effect-handler
design): physically relocating the 29-branch per-action effect interpretation
(reassigns `run()`-local `session_tree`/`tree_filter_mode`/`pending_prefill`/`/reload`
bundle) behind per-effect ports, the pre-dispatch hotkey/shortcut/shell routing, and
the `run()` shrink.

Sub-slice 3.1f.4 (first cut, 2026-07-21) gives the controller ownership of the
loop driver and start/shutdown lifecycle. `CodingSessionController.run_loop(*,
drive, fire_session_start, fire_session_shutdown, consume_settle_pending,
clear_extension_chrome)` fires `session_start` outside the try (so a setup-fire
failure never runs the shutdown bookend for a session that never started), drives
the injected `drive` closure — the `while True` step loop whose exit paths return
the bounded `NativeToolReplResult` (terminate `FAILED` or post-loop `SUCCEEDED`) —
and guarantees, on every exit path (normal/fatal/exception), the once-only
true-idle settle (fired through the controller's own settled emitter when
`consume_settle_pending()` returns True), the `session_shutdown` fire, and the
extension-chrome clear, in that exact order, each through an injected port.
`NativeToolReplSession.run()` wraps its former inline `while True` skeleton +
post-loop `SUCCEEDED` return in a `_drive_repl_loop()` closure (byte-identical
body — no re-indent, no control-flow rewrite — sharing the run's mutable control
state, incl. the live `session_tree`, `tree_filter_mode`, `pending_prefill`,
`agent_settled_pending`, `extension_in_agent_turn`, and the whole `/reload`
bundle, via `nonlocal`), defines four thin lifecycle closures, and returns
`loop_controller.run_loop(...)`; the prior inline `session_start` fire and the
entire `try/finally` firing the final `agent_settled`/`session_shutdown`/
`clear_extension_chrome` are deleted, and `emitter.agent_settled()` no longer
appears in the monolith (moved into the controller). Behavior-preserving move
only: event ordering, the finally-always shutdown/clear-chrome guarantee, provider
requests, cancellation, terminate-session assembly, persistence write timing, and
every public CLI/JSON/RPC/session/extension format are unchanged; no new runtime
dependency, `Any`, or `type: ignore`. The import-boundary gate extends the
`session_controller` exact allowlist with `native.coding.result`/
`NativeToolReplResult`. **Sub-800 shell deferred:** `run()` is still ~2,849 lines
because `_drive_repl_loop`'s ~1,470-line body is a closure lexically nested in
`run()`; physically relocating it out of `run()` behind injected ports (with a
mutable holder for the `session_tree`/`tree_filter_mode`/`pending_prefill`/
`/reload`-bundle control state) to drop `run()` under 800 lines — at which point
`while True` moves into `run_loop` proper — and the run transition, remain the
rest of Phase 3.1f.4.

Sub-slice 3.1f.4 (continuation 2, 2026-07-21) moves the `while True` skeleton into
`run_loop` proper: `CodingSessionController.run_loop` now takes `step_once` (one
iteration → the new frozen `LoopStepSignal`: `CONTINUE`/`BREAK`/`RETURN_RESULT`)
plus `finalize` (the post-loop `SUCCEEDED` projection) in place of the single
`drive` port, and runs the loop itself — routing `CONTINUE` back into the loop,
`BREAK` through `finalize`, and `RETURN_RESULT` returning the terminate `FAILED`
projection the step already built. `NativeToolReplSession.run()`'s former
`_drive_repl_loop` closure is split into `_repl_step` (one iteration, returning a
`LoopStepSignal` — every inline `break`/`continue` and the terminate return became
the matching signal, the natural end-of-iteration a `CONTINUE`) and
`_finalize_repl_loop`; the `while True` and its exit routing no longer live in the
monolith. Behavior-preserving: event ordering, the finally-always
shutdown/clear-chrome guarantee on normal/fatal/exception exit, and every public
CLI/JSON/RPC/session/extension format are unchanged; no new runtime dependency,
`Any`, or `type: ignore`. **Still deferred (sub-800 shell + `< 800` assertion):**
`run()` is still ~2,794 lines because `_repl_step`'s ~1,470-line body remains a
closure lexically nested in `run()`, sharing the `session_tree`/`tree_filter_mode`/
`pending_prefill`/`extension_in_agent_turn`/`/reload`-bundle control state with the
composition-root closures through `nonlocal`. Physically relocating that body (and
the setup closures) out of `run()` behind a mutable holder for the shared control
state plus injected ports for every concrete UI/renderer/`repl_input`/provider/
session-tree/persistence effect it performs — to drop `run()` under 800 lines and
add the `run()`-length `< 800` assertion — remains the last cut of Phase 3.1f.4,
alongside the separately-deferred 3.1f.3 remainder (relocating the 29-branch
`CodingCommandAction` interpretation and `EXIT`/`CONTINUE` routing into
`dispatch_command`).

Sub-slice 3.1f-completion enabler (control-state holder, 2026-07-21) lands the
mutable holder both deferred remainders named. The ~40 run-scope names shared
through `nonlocal` are consolidated into one slotted strictly-typed
`_RunControlState` instance (`ctl`) local to `run()`, holding the 32 genuinely
cross-closure-shared names (`session_tree`, `tree_filter_mode`, `pending_prefill`,
`line`, `package_roots`, `workspace_resources`, the `_ExtensionRuntime` bundle and
its projected command/menu/description/hook/outbox/renderer-map/flag-values/
tool-renderer/tool-registry names, `extension_activation_custom_messages`,
`agent_settled_pending`, `extension_in_agent_turn`). Every run-bound read/write of
those names in `_interpret_builtin_effect`, `_repl_step`,
`_dispatch_resource_effect`, `_dispatch_extension_effect`, `_agent_loop_entered`,
`_consume_agent_settled_pending`, and the persistence/extension/renderer/footer
adapter closures now routes through `ctl.<attr>` (186 references across 173 lines,
decided with a `symtable` scope resolver that distinguishes the `run()` binding from shadowing
params/comprehension temporaries), and all four `nonlocal` blocks are deleted. Ten
confirmed assign-before-read `_interpret_builtin_effect` transients stay
function-local rather than joining `ctl`: the `_registered_tool`/`_port`/
`custom_message` loop variables (a `for`-target would force an Optional attribute
and defeat the non-optional `RegisteredTool`/`QueuedCustomMessage`/`ToolPort`
contracts) and the `/reload` provider-refresh + tool-filter-check transients
`fallback`/`fallback_provider`/`catalog_state`/`was_extension_selection`/
`unknown_filter_names`/`known`/`unknown`. `ctl` is constructed once
`session_tree` is bound (before the first setup-time closure call) and seeded from
the setup locals; `pending_prefill`/`tree_filter_mode` carry their literal
initializers into the constructor and `line` uses the dataclass default. No
closure body leaves `run()` (still 2,825 `ast`-lines) and no `< 800` assertion is
added yet. Byte-identical behavior; the metadata-only workflow archive is
untouched; no new runtime dependency, `Any`, or `type: ignore`. This removes the
run-scope free-variable capture so the remaining last cut can physically relocate
the `_interpret_builtin_effect`/`_repl_step` bodies out of `run()` into methods
that receive `ctl` explicitly, dropping `run()` under 800 lines and adding the
`< 800` assertion.

Sub-slice 3.1f-completion (built-in interpretation relocation, 2026-07-22)
completes the deferred 3.1f.3 remainder: the 886-`ast`-line
`_interpret_builtin_effect(command_outcome)` closure nested in `run()` is
physically relocated into a new module-level composition-root handler
`_BuiltinCommandInterpreter` (stateless, `__slots__ = ()`) whose
`interpret(command_outcome, *, session, ctl, …)` method the existing
`_CodingCommandEffectsAdapter` reaches through the already-wired
`CodingCommandEffects.interpret_builtin(outcome)` port, symmetric with the
resource/extension dispatch ports. The handler receives `ctl` plus the 31
run-loop collaborators (resolved with `symtable`) as strictly-typed keyword-only
arguments and mutates `ctl` in place, so `run()` reads the reassigned
`session_tree`/`tree_filter_mode`/`pending_prefill`/`/reload`-bundle control state
back byte-identically. The superseded closure is DELETED with no alias; the
adapter's `interpret` slot becomes a thin `lambda outcome:
builtin_interpreter.interpret(outcome, session=self, ctl=ctl, …)` dispatch, so
`run()` drops from 2,825 to 1,975 `ast`-lines (−850). The relocation is a uniform
4-space dedent plus a single `\bself\b`→`session` token rename (verified safe:
every `self` in the body was a `self.`-attribute access on the session, none in a
string/comment), so the per-action effects, footer policies, built-in>resource>
extension precedence, and every CLI/JSON/RPC/session/extension/TUI format are
byte-identical; no new runtime dependency, `Any`, or `type: ignore`
(`extension_session_allows` is `Callable[..., bool]` with an inline note for its
keyword-only gate arguments).

Sub-slice 3.1f-completion (repl loop step relocation, 2026-07-22) relocates the
per-iteration REPL loop step and its lifecycle bookends out of `run()`. The
538-`ast`-line `_repl_step` closure (with its nested `_prepare_loop_request` and
the per-turn provider/status/coordinator closures) plus the five bookends
`_finalize_repl_loop`/`_fire_session_start`/`_fire_session_shutdown`/
`_consume_agent_settled_pending`/`_clear_extension_chrome_after_run` are
physically relocated into a new module-level composition-root handler
`_ReplLoopStep` (stateless, `__slots__ = ()`), symmetric with
`_BuiltinCommandInterpreter`. Its `step_once(*, session, ctl, loop_controller,
…)` performs one iteration and returns only the routing `LoopStepSignal`, and its
`finalize`/`fire_session_start`/`fire_session_shutdown`/`consume_settle_pending`/
`clear_extension_chrome` methods build the terminal projections and fire the
lifecycle effects. `run()` reaches the handler through the unchanged
`run_loop(step_once=, finalize=, …)` ports by passing each method
`functools.partial`-bound to the run-scope collaborators; the six superseded
closures are DELETED with no alias. The 42 `_repl_step` free variables (a
superset of the bookends') were resolved with `symtable` and became the handlers'
keyword-only parameter lists; the relocation is a uniform 4-space dedent plus a
single `\bself\b`→`session` token rename (verified safe: every `self` in the body
is a `self.`-attribute access on the session, and the bare word `session` occurs
only in comments), so the loop routing, once-only true-idle settle, lifecycle
fires, extension-chrome clear, hotkey/shortcut/`!`-shell pre-dispatch,
`dispatch_command` precedence, accepted-input preparation, provider-turn
execution, cancellation, event ordering, and every CLI/JSON/RPC/session/
extension/TUI format are byte-identical. `run()` drops from 1,975 to 1,469
`ast`-lines (−506). No new runtime dependency, `Any`, or `type: ignore`
(`_extension_custom_driver` is `Callable[..., object]`), and `run_loop`'s port
contract, `LoopStepSignal`, and the metadata-only workflow archive are untouched.
**Still deferred (the last 3.1f cut):** splitting `interpret` into per-effect
port methods, relocating the residual renderer/provider-mutation/adapter closures
still in `run()`, and dropping `run()` under 800 lines with the `< 800`
assertion.

Sub-slice 3.1f-completion (custom-entry renderer relocation, 2026-07-22)
relocates the ~208-line custom-entry/custom-message rendering and extension-outbox
band out of `run()`. The eleven closures `render_extension_custom_message`,
`render_extension_custom_entry`, `add_rendered_custom_entry_to_terminal`,
`render_custom_message_entry`, `add_rendered_entry_to_terminal`,
`add_custom_message_entry_to_terminal`, `replay_custom_entries_to_terminal`,
`redraw_custom_entries_for_active_branch`, `extension_append_entry`,
`extension_send_message`, and `drain_extension_outboxes` are physically relocated
into a new module-level composition-root handler `_CustomEntryRenderer`, symmetric
with `_ReplLoopStep`/`_BuiltinCommandInterpreter`. Because these closures call one
another densely, the handler is a frozen/slotted/kw-only dataclass holding the
mutable `ctl` holder (its `session_tree`, renderer maps, outboxes, and
`extension_in_agent_turn` flag are read fresh so a `/reload`/`/new`/`/resume`/
`/fork`/`/clone` rebind is reflected inline) plus the stable run-scope
collaborators — the owning session (for `_emit_diagnostic`), the terminal UI, the
coding input queue, and the error stream — and its methods call each other through
`self`. `run()` constructs it once (after `coding_input_queue`/`loop_controller`)
and passes each bound method where the deleted closures were consumed: the
`_ReplLoopStep.step_once` `drain_extension_outboxes`/`extension_append_entry`/
`extension_send_message` ports, the `_BuiltinCommandInterpreter.interpret`
`redraw_custom_entries_for_active_branch`/`extension_send_message` ports, the
`_dispatch_extension_effect` `append_entry_fn`/`send_message_fn` seams, the
startup `replay_custom_entries_to_terminal()` call, and the activation
custom-message replay loop; the eleven superseded closures are DELETED with no
alias. Body-preserving move (locals rebound as `terminal_ui = self.terminal_ui`,
`ctl`→`self.ctl`, cross-closure calls prefixed `self.`, `self._emit_diagnostic`→
`self.session._emit_diagnostic`), so the custom payloads, non-styled fallback,
terminal replay order, redraw rows, outbox drain-into-prompt/steer/follow-up/
next-turn timing, and every renderer-map registration are byte-identical. `run()`
drops from 1,469 to 1,265 `ast`-lines (−204); the handler is 259 `ast`-lines. No
new module (an intra-module relocation like the two prior handlers), so the
import-boundary gate is unchanged; no new `Any`, `type: ignore`, or runtime
dependency, and the metadata-only workflow archive is untouched. **Still deferred
(the last 3.1f cut):** splitting `interpret` into per-effect port methods,
relocating the residual footer/persistence/adapter closures still in `run()`, and
dropping `run()` under 800 lines with the `< 800` assertion (the provider-mutation
band landed in the sub-slice below).

Sub-slice 3.1f-completion (provider/model/auth/compaction mutation relocation,
2026-07-22) relocates the seven mutation closures `apply_model_selection`,
`apply_auth_change`, `apply_compaction`, `_append_durable_compaction`,
`extension_set_active_tools`, `extension_set_model`, and
`extension_set_thinking_level` out of `run()` into a new module-level
composition-root handler `_ProviderMutationEffects`, symmetric with
`_CustomEntryRenderer`/`_ReplLoopStep`/`_BuiltinCommandInterpreter`. Because these
effects call one another densely (`extension_set_model` re-enters
`apply_model_selection`; `apply_compaction`'s before-compact hook dispatch passes
the three `extension_set_*` peers), the handler is a frozen/slotted/kw-only
dataclass holding the mutable `ctl` holder (its
`extension_session_before_compact_hooks`, `extension_flag_values`, and
`session_tree` are read fresh so a `/reload`/`/new`/`/resume`/`/fork`/`/clone`
rebind is reflected inline) plus the stable run-scope collaborators — the owning
session (for its live `provider_state`), the coding state, the product session,
the terminal UI, the tool-capability facade, settings, cwd, the input/error
streams, the `refresh_footer_text` port, and the extension notify sink / UI
driver — and its methods call each other through `self`. `run()` constructs it
once (right after `refresh_footer_text`) and passes each bound method where the
deleted closures were consumed: the `_BuiltinCommandInterpreter.interpret`
`apply_compaction`/`apply_model_selection`/`apply_auth_change`/
`extension_set_active_tools` ports, the `_ReplLoopStep.step_once`
`apply_compaction`/`extension_set_*` ports, the `_dispatch_extension_effect`
`set_active_tools_fn`/`set_model_fn`/`set_thinking_level_fn` seams, the
provider-request/tool-policy hook contexts, the `_ExtensionToolPort`
`set_active_tools_fn`, the `extension_session_allows` gate, and the
product-session `_persist_compaction` durable-append callback; the seven
superseded closures are DELETED with no alias. Body-preserving move (collaborators
reached through `self.session.provider_state`/`self.coding_state`/
`self.refresh_footer_text`/`self.ctl`/`self.product_session`/
`self.extension_set_*`), so the rebind semantics are byte-identical — a
provider/model/auth rebind clears only the live provider history and resets usage
via a fresh `AgentUsageAccumulator` while preserving the in-memory compaction
suffix and leaving the durable session tree intact, the tool-call-support refusal
restores the prior selection, `/login` still suspends the TUI live region for
archive-free interactive OAuth output, and compaction still keeps the recent
user-turn groups and appends the metadata-only durable summary. `run()` drops from
1,265 to 1,084 `ast`-lines (−181); the handler is 259 `ast`-lines. No new module
(an intra-module relocation like the three prior handlers), so the import-boundary
gate is unchanged; no new `Any`, `type: ignore`, or runtime dependency, and the
metadata-only workflow archive is untouched. **Still deferred (the last 3.1f
cut):** splitting `interpret` into per-effect port methods, relocating the
residual footer/persistence/adapter closures still in `run()`, and dropping
`run()` under 800 lines with the `< 800` assertion.

Sub-slice 3.1f-completion (residual composition adapters + Phase 3.1 acceptance,
2026-07-22) closes Phase 3.1. The last band of substantial composition-root
collaborator closures leaves `NativeToolReplSession.run()`, which now measures
793 `ast`-lines (down from 1,084). Two new module-level frozen/slotted/kw-only
handlers, symmetric with the four earlier ones, own the relocated bodies:
`_FooterEffects` owns the footer/status-line set (`coding_footer_text`,
`refresh_footer_text`, `legacy_footer_enabled`, `refresh_legacy_footer`,
`refresh_legacy_footer_with_usage`); `_SessionCollaborators` owns `diag`, the
session-name setters, `current_session_dir`/`resolve_session_file`,
`rebuild_messages_from_tree`, `summarize_branch`, `extension_session_allows`, the
extension completion/custom-UI driver, the provider-request/tool-policy hooks, and
the resource/extension command-dispatch effects. Both hold the mutable `ctl`
holder (extension command/hook/flag bundle read fresh so a `/reload` rebind is
reflected inline) plus the stable run-scope collaborators; methods call one
another through `self`. Each body is a mechanical `self.`-prefixing move
(byte-identical footer/diagnostic/session-resolution/summary/hook-dispatch/
precedence behavior). The construction order was adjusted so
`_FooterEffects.refresh_footer_text` feeds `_ProviderMutationEffects` and its
`extension_set_*` feed `_SessionCollaborators` (the two handlers and the two policy
wrappers moved below the `repl_input`/startup/changelog band); the superseded
closures are DELETED with no alias and their consumers pass the bound handler
methods. No new module (intra-module relocation), so the import-boundary gate is
unchanged; no new `Any`, `type: ignore`, or runtime dependency. The persistence
write callbacks (`_load_product_session_history`, `_persist_agent_message`,
`_persist_compaction`) intentionally stay run-scope closures: `_persist_compaction`
reaches `provider_mutation` through a late name reference while `product_session`
(which consumes all three at construction) is built earlier — a genuine
construction cycle whose clean resolution is write-ownership relocation
(Phase 3.3). The ownership gate
`test_session_controller_owns_the_loop_skeleton_and_lifecycle` now asserts `run()`
stays under 800 `ast`-lines (the honest guard, added only now the shell is
genuinely 793). This completes Phase 3.1: the headless state machine, loop
skeleton/lifecycle, command dispatch, built-in interpretation, custom-entry
rendering, provider mutation, and residual collaborators all live in
`native.coding.*` composition-root handlers reached through typed ports.
Splitting the single `interpret_builtin` port into per-effect port methods remains
Phase 3.2, and persistence write ownership remains Phase 3.3.

Sub-slice 3.1f.3 (continuation 2, 2026-07-21) makes the continuing built-in's
per-action effect chain run THROUGH the command-dispatch effect port, symmetric
with resource/extension dispatch, so the classified outcome no longer crosses the
controller→composition boundary as data. `CodingCommandEffects` gains
`interpret_builtin(outcome)`; `dispatch_command`, for a continuing built-in, calls
`effects.interpret_builtin(outcome)` and returns `CONTINUE_LOOP` instead of an
`INTERPRET_BUILTIN` resolution. The superseded contract is deleted with no alias:
`CommandDispatchResolutionKind.INTERPRET_BUILTIN`, the
`CommandDispatchResolution.interpret_outcome` field, the `interpret_builtin`
factory, and their validation all leave `native.coding.commands`, leaving the
contract exactly `{CONTINUE_LOOP, PROCEED_TO_RUN, EXIT_LOOP}`. The 893-line
per-action effect chain moved verbatim (uniform 4-space dedent) out of
`_repl_step`'s inline INTERPRET_BUILTIN branch into a new run-scope closure
`_interpret_builtin_effect(command_outcome)`, performed via
`_CodingCommandEffectsAdapter`'s new `interpret`/`interpret_builtin` slot; the
closure declares the run's control state (`session_tree`/`tree_filter_mode`/
`pending_prefill`/the `/reload` bundle, ~40 names) `nonlocal` so it mutates the
same run-scope bindings the deleted branch did and `run()` reads them back
byte-for-byte, and `_repl_step`'s own `nonlocal` set shrinks to the four flags it
still assigns. Byte-identical CLI/JSON/RPC/session behavior; metadata-only archive
intact; no new runtime dependency, `Any`, or `type: ignore`. The import-boundary
gate extends the `session_controller` exact allowlist with
`native.coding.commands.CodingCommandOutcome`. **Still deferred (needs the mutable
control-state holder + closure-ecosystem cascade):** `_interpret_builtin_effect`
(893 `ast`-lines) and `_repl_step`'s ~1,470-line body are still closures lexically
nested in `run()`, so `run()` still measures ~2,797 `ast`-lines (the block moved
to a sibling run-closure, not out of the function). Splitting `interpret_builtin`
into per-effect port methods, physically relocating those bodies out of `run()`
behind a mutable holder (dropping `run()` under 800 lines with a `< 800`
assertion), and the pre-dispatch hotkey/shortcut/shell (`!`/`!!`) routing
relocation remain the last cut of Phase 3.1f.3/3.1f.4.

### Session tool-capability port seam — SHIPPED (2026-07-19)

Phase 2.2b.3 defines the runtime-checkable
`native.agent.tools.AgentToolCapabilities` port for detached definition tuples,
synchronous one-call execution, and canonical error-result creation.
The product `native.tool_capabilities.NativeToolCapabilities` facade owns
injected built-in/extension registry composition, frozen `ToolFilterOptions`,
active-tool selection, extension reload, workspace `ToolContext`, and reusable
executor construction. Concrete production-tool construction stays in the
product composition root. Neither runtime helper is eagerly exported.

The session uses the port inside the provider/tool cycle while retaining
strictly sequential scheduling, budgets and invocation counts, extension
preflight/result dispatch and ordering, provider-request construction,
canonical events, terminal/RPC waiting, dynamic-tool annotations, and durable
writes. Public JSON/RPC/SDK/extension/session formats and the metadata-only
archive allowlist do not change. Static exact allowlists, recursive synthetic
laundering checks, isolated import-order checks, and structural protocol
conformance cover the new ownership boundary.

Characterization preserves two pre-existing defects for a mandatory 2.2b.4
correctness closure: `before_provider_request.available_tools` can currently
re-enable a registered name outside the prior active/final request snapshot,
and a provider-returned call outside the exact advertised set can reach hooks
and execution. The closure must intersect hook names with the prior snapshot
and produce the normal budget-consuming policy error for any returned
out-of-snapshot call before hooks, execution, or invocation counting. The
unrelated June precedence/unknown-name conflict is not part of 2.2b.3.

Candidate verification is green: `just check` reports Ruff and mypy clean
across 342 source files with 3,433 tests passed and 2 skipped. Documentation,
diff, 8 PTY smoke tests, extension and automation/RPC conformance gates, and
the 49/49 parity score also pass. An independent integration audit fixed three
warnings and two suggestions and then returned CLEAN. Pi round 1 found one
warning and one suggestion; both were fixed, and Pi round 2 returned explicit
CLEAN with no findings. Claude Fable returned valid unscoped CLEAN with no
findings or relevant scope omissions. The first Fable attempt was invalidated
by the harness before verdict after an out-of-scope path request; the fresh
replacement is the recorded gate.

### Final provider-request snapshot and authorization — SHIPPED (2026-07-19)

Phase 2.2b.4a introduces an exact canonical binding between one provider
request and its ordered advertised tool names, plus a product adapter that owns
request construction and serial `before_provider_request` dispatch. Returned
tool names are monotonic intersections of the current detached tuple: hook
order cannot re-enable a name, definition order wins over returned order, and
duplicates or unknown names disappear. `ctx.set_active_tools(...)` continues
to mutate later provider iterations, while the already-built current request
changes only through an explicit narrowing transform.

The provider response remains bound to that exact snapshot. After the existing
budget-exhaustion precedence, any out-of-snapshot call receives a balanced
start/completion pair and the normal pipy-owned `unknown tool` result. It
consumes budget and is appended once to canonical history and the full-content
native session, but cannot reach tool-call/result hooks, execution, live
output, malformed accounting, or global invocation accounting. Dynamic tools
activated by an earlier call do not become authorized later in the same
provider response.

This slice does not restrict the lower-level capability facade, change
sequential scheduling or public formats, relocate persistence, or touch the
metadata-only archive allowlist. The active-input/compaction closure remains
2.2b.4b; effect/usage/queue-facing ports remain 2.2b.4c; the full loop remains
2.2b.5.

### GPT-5.6 Sol plus model-aware `max` thinking — SHIPPED (2026-07-14)

Delivered per [gpt-5-6-sol-plan.md](gpt-5-6-sol-plan.md): the
`openai-codex/gpt-5.6-sol` catalog row (372K context, image input); the canonical
thinking vocabulary extended to `off|minimal|low|medium|high|xhigh|max`; a
Codex-scoped clamp-then-map (`clamp_thinking_level`/`resolve_codex_effort`)
mirroring Pi's per-request `clampThinkingLevel` that carries the selected effort
into the legacy Codex provider (stored `max` → `effort: "max"` on Sol, clamps to
`xhigh` on GPT-5.5); model-aware Shift+Tab cycling; and Sol's 372K status budget.
GPT-5.5 remains the Codex default. Generalized cross-provider clamping is the one
named follow-on. A direct different-family (Pi) review was CLEAN.

### Project-trust design — SHIPPED (2026-07-15)

The Pi-sourced design and ordered implementation plan now pin the canonical
trust-store schema/ancestry, protected versus exempt inputs, final-runtime-cwd
loading order, interactive/headless mode matrix, global-only
`defaultProjectTrust`, CLI/package overrides, `/trust` and reload behavior, and
extension decision/read ownership. A direct fresh-context Claude Opus review was
CLEAN. No runtime trust behavior is claimed by this design-only slice.

### Trust core and settings/resource gate — SHIPPED (2026-07-15)

Slice 1 from the reviewed
[implementation plan](specs/2026-07-15-project-trust-implementation-plan.md)
now ships the store/detector, trust-aware settings manager, source-provenance
resource/package gate, final-cwd resolver, run overrides, headless mode matrix,
and deterministic conformance gate.

### Interactive trust and package/config integration — SHIPPED (2026-07-15)

Slice 2 now ships the startup selector, `/trust` and reload persistence, global
`defaultProjectTrust` control, and package/config command trust handling.

### Extension-owned project-trust decision/read APIs — SHIPPED (2026-07-16)

Slice 3 now activates only global/explicit-CLI extensions before unresolved
trust, runs their `project_trust` handlers serially until the first yes/no,
honors exact `remember=True`, reuses those instances across provider-catalog and
live-session startup, and exposes zero-argument `is_project_trusted()` /
`isProjectTrusted()` reads on normal contexts. The product JSON conformance row
proves project code stays gated and stdout remains protocol-only.

### `before_provider_headers` extension hook — SHIPPED (2026-07-16)

Serial mutation-only handlers now receive the assembled request header map on
every real HTTP adapter. Strings add/override and `None` deletes; handler errors
fail soft. Bedrock applies mutations before SigV4 signing, OpenAI Codex reuses
one transformed snapshot across retries and WebSocket-to-SSE fallback, and ds4
dispatches exactly once through its Chat Completions delegate. The golden
extension gate proves transport delivery and no archive/protocol leakage.

### Extension-surface `agent_settled` — SHIPPED (2026-07-16)

Extensions can now observe one payload-free `agent_settled` callback after the
provider/tool run, retry/compaction work, and every queued continuation are
idle. It fires after unexpected mid-run failures too, and a settled handler may
schedule a new run without blocking on stdin. The hook remains separate from
the mode-owned JSON/RPC event, so those streams still emit exactly one protocol
event.

### Durable TUI-only extension entry renderers — SHIPPED (2026-07-17)

`api.register_entry_renderer` now owns live product-TUI components for durable
`ctx.append_entry` records, with the full stored entry, current expanded/width/
theme context, startup and `/resume` replay, expanded-state rerender, `/reload`
replacement, and headless omission. Message renderers remain separate.

### Anthropic cache-friendly dynamic tool loading — SHIPPED (2026-07-17)

Purely additive active-tool changes made by extension tools now persist ordered
load points on native tool results. Supported first-party Anthropic Claude 4.5+
models (or explicit compat opt-ins) keep late definitions out of the immediate
prefix with `defer_loading: true` and load them at the result through
`tool_reference`; output is preserved as sibling content. Removals,
replacements, failures, unsupported models, and other providers retain the safe
full-current-tool fallback. The reviewed design and implementation plan are in
[`docs/specs/2026-07-17-anthropic-dynamic-tool-loading-design.md`](specs/2026-07-17-anthropic-dynamic-tool-loading-design.md)
and
[`docs/plans/2026-07-17-anthropic-dynamic-tool-loading-implementation-plan.md`](plans/2026-07-17-anthropic-dynamic-tool-loading-implementation-plan.md).

### OpenAI Responses dynamic tool search — SHIPPED (2026-07-17)

Supported OpenAI Responses and OpenAI Codex Responses rows now keep dynamically
activated definitions out of top-level `tools` and place deterministic completed
client `tool_search_call` / `tool_search_output` pairs immediately after the
durable marked result. Explicit Boolean `compat.supportsToolSearch` wins and
defaults false; unsupported rows keep the safe full current-tool list. Kimi Chat
Completions, package-update realignment, and broader extension UI remain separate
gaps.

### Recently shipped: OpenAI-Codex transport reliability

The operator-selected transport-reliability gap has shipped. Research and the
reviewed implementation plan are committed under `docs/specs/` and `docs/plans/`; the
runtime and integration closeout now provide:

- OpenAI-Codex SSE and WebSocket receives use a validated 300,000 ms idle
  timeout by default instead of the former provider-local 60-second socket
  timeout. `httpIdleTimeoutMs` and `retry.provider.timeoutMs` configure it in
  integer milliseconds, with `0` disabling the timeout.
- Recognized open/read/reset/truncation failures are normalized into sanitized
  provider-domain failures. Deliberate cancellation wins every normalization
  race and is never retried.
- The retry boundary owns the complete request-plus-stream attempt. Bounded,
  cancellation-aware retries cover transient HTTP and pre-event transport
  failures, honor capped `Retry-After`, and stop after the first parsed provider
  event so visible text, reasoning, tool assembly, and tool execution cannot be
  duplicated.
- `transport: auto|sse|websocket` is live for OpenAI-Codex. `auto` and explicit
  `websocket` start on the Responses WebSocket path, fall back to SSE only for
  recognized pre-event transport failures, remember fallback for later `auto`
  calls, and never fall back after provider progress. Long-lived WebSocket
  reuse/continuation caching and post-event replay remain out of scope.
- Parity-runner defense-in-depth for the historical raw timeout ships: runner
  logs record structured child attempt start/finish events, distinguish runner
  timeouts from signal exits, and retry only when the normalized provider
  diagnostic or exact legacy `pipy: The read operation timed out` tail appears
  with no branch/HEAD/ref/worktree progress.

Closeout verification covers:

```sh
uv lock --check
uv run pytest tests/test_parity_runner.py -q
uv run pytest tests/test_native_openai_codex_provider.py tests/test_openai_codex_retry.py -q
just check
```

### Strategic follow-on: broader custom editor/component-library parity

The extension editor text-helper alias slice has shipped: Pi-canonical
`ctx.ui.getEditorText`, `ctx.ui.setEditorText`, and `ctx.ui.pasteToEditor` now
delegate to the existing live editor helpers, with snake_case names retained as
Python convenience aliases.

The rich message resume/redraw slice and bounded OAuth-provider `/login` slice
have shipped. The broader extension/package platform remains the highest-impact
parity area. Recent focused increments shipped Pi-faithful custom-editor getter
semantics and bounded app-action delegation: live
`ctx.ui.getEditorComponent()` now returns the configured factory even when
construction fails soft or produces no active component; custom editors receive
Pi-style keybinding specs, special `onEscape`/`onCtrlD`/`onPasteImage`
callbacks, delegated model/thinking/tool/follow-up/external-editor handlers
(`app.editor.external` through `$VISUAL`/`$EDITOR`), draft preservation, and
Ctrl-C remains outside the delegated handler map. Non-lifecycle extension
hooks now receive the live product-TUI UI driver too, so their Pi-shaped
chrome/editor calls paint immediately while headless contexts stay no-op. The
bounded custom overlay handle slice has shipped too: `ctx.ui.custom(...,
options)` `onHandle` callbacks now receive Pi-shaped `hide`, `setHidden`,
`isHidden`, `focus`, `unfocus`, and `isFocused` methods, with hidden overlays
skipping render/input and unfocused visible overlays skipping input only. A
future small follow-on is custom editor/component-library parity beyond the
landed live `setEditorComponent` integration and bounded
`ctx.ui.custom(..., options)` overlay path; it is strategic extension work, not
the selected next slice.

Keybinding follow-on: `app.editor.external` now honors `keybindings.json` in
the built-in editor, custom editor, and `ctx.ui.editor(...)` overlay paths, but
the rest of the built-in prompt hotkeys (`app.model.*`, `app.tools.expand`,
`app.thinking.*`) still use the legacy hard-coded read-loop branches. Dynamic
extension-shortcut reservation for user-rebound app keys is also deferred. Until
that lands, a user who rebinds `app.editor.external` away from Ctrl-G still
cannot give Ctrl-G to an extension, and startup warns if the rebound key is also
registered by an extension shortcut because the live editor action wins. If
`app.editor.external` is rebound onto one of the still hard-coded built-in prompt
hotkeys, the external-editor action takes precedence over that hard-coded branch
in the built-in editor. A broader keybinding pass should make built-in prompt
hotkeys, custom editor adapters, and extension shortcut reservation resolve from
one action/key table with explicit conflict precedence.

Keep this slice focused on one Pi-shaped editor/component API increment. Do not
reopen completed rich-message, chrome `requestRender`, footer branch-change,
idle custom-message delivery, or extension OAuth-provider `/login` work unless
the chosen custom editor slice exposes a direct integration bug in those paths.

The bounded OAuth-provider `/login` slice has shipped: extension OAuth metadata
is projected under the provider-name id, `/login <provider>` stores
`{"type":"oauth", ...credentials}` in `AuthStore`, `/logout <provider>` removes
it, and OAuth-backed extension providers are `login-required` until credentials
exist. Do not expand into PyPI/npm package sources until the broader
supply-chain policy is written.

## Recent Closeout

### Extension API slice 12 closeout: package runtime composition — LANDED

Slices 1–12 have **landed**. The managed git package-source/update follow-on
has also landed, as has the first custom session-entry/message-rendering
follow-on. Installed local-path and managed git package resources now flow
through discovery (see the closing note below). Landed so far:

- Slice 1 (discovery + manifest inventory, no execution):
  `pipy_harness.native.extensions.discover_extensions` returns deterministic
  loadable/disabled `ExtensionDescriptor` records, parses optional
  `pipy-extension.toml` with stdlib `tomllib`, fails closed on unsafe
  names/paths/manifests/api_versions/duplicates/binary entries, and never
  imports extension code. Gate:
  `scripts/parity_checks/extension_discovery_conformance.py --json`.
- Slice 2 (activation sandbox boundary):
  `pipy_harness.native.extension_runtime.activate_extensions` imports only
  `loadable` descriptors, calls `activate(api)` (sync or async), supports
  `register_command` only via the public `pipy_harness.extensions.PipyExtensionAPI`,
  and fails closed per extension on import / no-activate / activation-exception /
  invalid / duplicate / reserved command name. Disabled descriptors are never
  imported. Gate:
  `scripts/parity_checks/extension_activation_conformance.py --json`.
- Slice 3 (command dispatch): activated extension `/<command>`s dispatch through
  the live tool-loop REPL (`dispatch_extension_command`), after built-ins and
  custom commands (no shadowing) and before the not-handled fallback, running
  the handler with a mode-aware context and the raw args, emitting `ctx.ui.notify`
  output as live UI, with **no provider turn**. Names/descriptions appear in the
  slash menu; `/reload` re-activates. Gate:
  `scripts/parity_checks/extension_dispatch_conformance.py --json`.
- Slice 4 (`tool_call` policy hook): an extension registers
  `@api.on("tool_call")` (or `api.on("tool_call", handler)`) to inspect a
  model-selected tool call's live name + parsed input before execution and
  return `ToolBlock(reason=...)` to block it. Wired into the tool loop
  (`dispatch_tool_call_hooks` before `ToolExecutor.execute()`); first block wins; a crashing
  hook fails closed; raw inputs are inspected live but not archived. Gate:
  `scripts/parity_checks/extension_tool_call_conformance.py --json`.
- Slice 5 (lifecycle events): `session_start`, `session_shutdown`,
  `agent_start`, `agent_end`, `turn_start`, and `turn_end` fire to `@api.on(...)`
  observers after the canonical automation projection in the tool-loop
  composition sink (`dispatch_lifecycle_hooks`); observe-only, fail-soft. Gate:
  `scripts/parity_checks/extension_lifecycle_conformance.py --json`.
- Slice 6 (`input`/`before_agent_start` + `send_user_message`): input transform,
  before_agent_start system-prompt injection, send_user_message-triggered turn.
  Gate: `scripts/parity_checks/extension_input_hooks_conformance.py --json`.
- Slice 7 (extension tool registration): `api.register_tool(ExtensionTool(...))`
  joins the bounded tool registry via `_ExtensionToolPort`; the model can call
  it, its `ToolResult(content, details)` flows back (schema-validated input,
  bounded output, handler exceptions → bounded tool errors); shadowing a
  built-in / invalid schema / duplicate disables the extension. Handlers are
  trusted local code (user OS permissions, no in-process sandbox, per the spec
  "Local trust boundary"); "read-only / pure" is a convention for this slice —
  capability enforcement (shell/network/write gates from the manifest
  `[permissions]`) is a later permission-policy slice. Gate:
  `scripts/parity_checks/extension_tools_conformance.py --json`.
- Slice 8 (`tool_result` hooks): after any tool runs, `@api.on("tool_result")`
  may transform the bounded observation; chained, fail-safe, bounded. Gate:
  `scripts/parity_checks/extension_tool_result_conformance.py --json`.
- Slice 9 (minimal UI notifications): `ctx.ui.notify(message, kind)` surfaces to
  the live UI via a notify sink threaded through the dispatchers / tool adapter /
  emitter; deterministic in non-interactive mode. Gate:
  `scripts/parity_checks/extension_ui_notify_conformance.py --json`.
- Slice 10 (golden conformance extension): the golden
  `docs/examples/extensions/pipy-extension-conformance.py` + product-path test +
  gate prove a single `/pipy-extension-conformance` trigger exercises the whole
  API (12 markers) with no body leaks to proof/archive/result. Gates:
  `tests/test_native_extension_conformance.py`,
  `scripts/parity_checks/extension_conformance_gate.py --json`.
- Slice 11 (provider registration and catalog wiring):
  `api.register_provider(ExtensionProvider(name, default_model, models, factory))`
  + `api.unregister_provider(name)` (staged, committed on success, duplicate
  across extensions disables the later one, invalid disables, factory failures
  bounded); `build_extension_provider_port` composes the factory into a
  `ProviderPort`; `ProviderContext` carries only safe selection metadata (never
  the shared auth store). Registered providers now contribute temporary per-run
  native catalog rows, appear in `--list-models`, resolve at startup, switch via
  `/model`, and are recomputed by `/reload`. Gates:
  `scripts/parity_checks/extension_providers_conformance.py --json` and
  `scripts/parity_checks/provider_catalog_conformance.py --json`.
- Slice 12 package CLI (settings management): `pipy install/remove/uninstall
  [-l]` and `pipy list` manage Pi-shaped local-path and managed git package
  sources recorded in a `packages` array in user `<config>/settings.json` or
  project `<cwd>/.pipy/settings.json` (with `-l`), preserving object-form
  `{source, ...}` entries; `pipy config <enable|disable>
  <skill|prompt|theme|extension> <name>` writes `+pattern`/`-pattern` resource
  filters (never deleting discovered resources). Supported git sources clone
  into the configured-scope cache and refresh through `pipy update`; PyPI/npm,
  `git+...`, credentialed URL userinfo, and ambiguous unsupported remote
  schemes fail closed, a missing local path fails closed, removing an
  unconfigured source exits non-zero, a corrupt settings file is never
  clobbered, and no package lifecycle scripts run. Gate:
  `scripts/parity_checks/extension_package_conformance.py --json`.

- Slice 12 package runtime composition: configured local-path sources and
  installed managed git caches resolve into per-kind roots
  (`package_resources.resolve_package_roots`, from an optional
  `pipy-package.toml` manifest mapping Pi's
  `pi.{extensions,skills,prompts,themes}`, else convention subdirs), composed
  once per session by `package_runtime.compose_package_runtime`. Package skills
  and prompts flow through `WorkspaceResources.discover(package_roots=...)`,
  extensions through `discover_extensions(package_roots=...)`, and themes
  through a file-based loader + overlay registry
  (`theme_files.build_theme_registry` + `themes.set_active_theme_registry`) so
  package themes are selectable through the `/settings` theme picker and re-color
  the chrome. All four kinds sit at the spec's lowest precedence (a workspace/global resource wins a
  name collision) and honor `+/-pattern` filters; package resources appear in
  `/reload` and `pipy config` discovery. Example
  `docs/examples/packages/demo-pack/`; live tmux proof
  `scripts/tmux_package_verify.sh`. Gate items 2/4/8 proven (manifest
  contributes an extension/skill/prompt/theme with deterministic precedence;
  filters affect discovery; no source path or resource body leaks into the
  archive-safe metadata). `pipy install` now both records a source **and**
  loads its resources.

- Managed git package-source/update follow-on: supported git sources install
  into `<config>/git` for user scope or `<cwd>/.pipy/git` for project scope,
  package `update` refreshes those caches through bounded fetch/reset, local
  path updates are no-ops, and runtime startup never clones or fetches. Runtime
  resolution preserves the configured cache scope so a user git package cannot
  be shadowed by a project cache with the same source. PyPI/npm sources remain
  deferred to a broader supply-chain policy.

- Source-loading flags: `pipy repl` accepts Pi-shaped `--extension`/`-e`,
  `--no-extensions`/`-ne`, `--skill`, `--no-skills`/`-ns`,
  `--prompt-template`, `--no-prompt-templates`/`-np`, `--theme`, and
  `--no-themes`. Explicit paths are per-run additive and still load when the
  matching `--no-*` flag disables default workspace/global/package discovery or
  a persisted resource filter disables the same resource name. The package gate
  now includes this check.

Custom session-entry/message-rendering follow-on: command and shortcut handlers
can call `ctx.append_entry(...)` to write JSON-safe durable `custom` entries;
`api.register_entry_renderer(...)` independently renders them only in the live
product TUI, including startup/`/resume` replay, expanded-state refresh, and
`/reload`. `api.register_message_renderer(...)` remains the separate custom-
message surface, including its captured-mode fallback. Renderer failures stay
bounded, non-JSON entry data is converted before persistence, and rendered
component bodies remain live-only. Render-once custom tool renderers also ship
(slice 17). Live invalidation for tool renderers and broader per-frame component
invalidation remain follow-ons.

Extension UI editor follow-on: command/shortcut handlers can call
`ctx.ui.editor(title, prefill=None)` to open a focused multi-line editor overlay
in the product TUI; headless/non-interactive dispatch returns `None` like Pi's
no-op UI context. The overlay submits on Enter, inserts newlines with
Shift+Enter where decoded and Alt+Enter as pipy's portable fallback, supports
basic cursor movement/backspace, cancels on Esc/Ctrl-C, and opens `$VISUAL` or
`$EDITOR` on Ctrl+G like Pi; successful editor exits replace the buffer and
failed exits keep the prior text. Main-prompt read/write/paste helpers,
autocomplete provider wrappers, bounded live custom editor component integration
including app-hotkey delegation through a keybinding/action adapter, and
`ctx.ui.custom(factory, options=None)` overlay option handling now ship; broader
Pi component-library and full overlay-stack parity remains a follow-on.

Remaining package work (deferred): PyPI/npm source kinds and richer package
ecosystem policy. Managed git sources, the isolated package cache, and package
`update` now ship.

Compatibility assessment for slice selection: treat Pipy's extension support as
**comparable for core local automation, not comparable to Pi as a mature
extension platform**. The landed slices cover the high-value Python equivalents
of Pi permission gates, custom slash commands, simple model-visible tools,
input/system-prompt hooks, lifecycle observation, minimal UI notices,
simple `ctx.ui` select/input/confirm/editor/status/working primitives,
live-session operation gates, user-bash adapters, provider-request transforms,
and active tool/model/thinking controls. Autocomplete provider wrappers now
ship for live product-TUI `@` and forced Tab completion. They do not yet cover
Pi's richer extension APIs: live (invalidate-driven) tool rendering beyond the
landed render-once snapshot, richer multi-widget `ctx.ui` dialogs, broader custom
editor component-library parity beyond the landed live integration,
message-entry APIs beyond the shipped
append/startup replay and idle `send_message` delivery, TypeScript source
compatibility, broader dynamic extension flag integration beyond the landed
`api.get_flag`/`ctx.flags` surface, or PyPI/npm package distribution. Managed
git sources, package `update`, and extension OAuth-provider `/login` wiring now
ship; broader remote package sources remain deferred.

Acceptance criteria when that queued follow-on is selected:

```sh
uv run python scripts/parity_checks/extension_package_conformance.py --json
uv run python scripts/parity_checks/extension_conformance_gate.py --json
uv run python scripts/parity_checks/extension_live_session_conformance.py --json
just check
```

## Near Term

The near-term product direction is a real `pipy-native` runtime with a Pi-like
interactive shell. The shell should be a thin user interface over pipy-owned
provider, session, turn, tool, sandbox, and archive boundaries, not a separate
runtime and not a wrapper around Codex, Claude, Pi, or another agent CLI. The
product posture is explicitly Pi-like: no permission popups for normal
interactive use.

Provider access direction: OpenAI Codex subscription auth remains the preferred
near-term hosted real-provider path. The existing `openai` provider remains the
pay-by-token OpenAI Platform API-key baseline; the subscription path is the
separate `openai-codex` provider modeled on Pi's PKCE OAuth and
`chatgpt.com/backend-api/codex/responses` implementation. OpenRouter is useful
for ad-hoc smoke testing with `OPENROUTER_API_KEY` but is not the preferred
default. Anthropic subscription access is not a near-term native provider
target because subscription-backed coding-agent usage is expected to stay
within Claude Code. The first selected local integration is `ds4`, using
`deepseek-v4-flash` through a local OpenAI-compatible Chat Completions server;
it is registered as tool-loop capable after live ds4 smoke proved OpenAI-style
tool-call round trips with pipy's loop.

The current disposition and next target are in `Completed Reviewed Program`
above: Slice 16 is landed, and the explicit next architecture boundary is
bounded transactional-reload contract completion or formal reconciliation
before ordinary product-parity selection.

Historical gates before the single product REPL are preserved in `Done` and
`docs/harness-spec.md` for auditability, but they are not current product
surfaces. The former no-tool REPL and its `/read`, `/ask-file`, `/propose-file`,
`/apply-proposal`, `/clear`, `/status`, `/help`, `/theme`, and `/template`
commands were removed in the 2026-06-20 parity cleanup. Current equivalents are
the model-visible tool loop (`read`/`edit`/`write`/`bash`), user-directed
`@path`/`@image:` references, `/new`, `/session`, `/hotkeys`, theme selection in
`/settings`, and prompt templates as their own `/<name>` slash commands.

Completed near-term foundations that remain relevant context: the Tool-Loop
Parity Track and the OpenAI Responses + OpenAI Codex Tool-Call Parity Track
landed end-to-end; startup chrome, visual resource labels, prompt/input
ergonomics, prompt-toolkit/readline/slash-menu fallbacks, `@file` completion,
multi-file context loading, product TUI workflow depth, native session tree,
settings/keybindings, automation RPC, export/import/share, provider catalog
construction, package runtime composition, and the shipped extension slices are
current foundations. Self-bootstrap readiness gates remain historical context.

Invariants that must hold for any near-term slice:

- default native stdout remains successful final text only on success, with
  diagnostics, finalization, progress, and errors on stderr
- the existing `pipy-session` metadata archive remains metadata-only and never
  includes raw prompts, model output, provider
  responses, request bodies, raw patch text, raw diffs, file contents, raw tool
  observations, command stdout, command stderr, auth tokens, cookies,
  credentials, secrets, private keys, or sensitive personal data; this does not
  prohibit the separate private native product session tree from storing the raw
  conversation needed for Pi-style resume and `/tree`
- metadata records still pass `pipy-session verify`, and `pipy-session list`,
  `search`, and `inspect` stay compatible

## Deferred

### Deferred For Self-Bootstrap

- Full tool-capable native pipy agent runtime beyond the provider,
  conversation, approval, sandbox, and tool-boundary slices.
- General native model/tool loop beyond bounded provider turns and explicitly
  approved tool boundaries. The bounded Pi-shaped slice of this work is now
  planned as the `Tool-Loop Parity Track` above; broader model/tool-loop
  capabilities outside that track stay deferred.
- Arbitrary shell execution. **Update (shipped):** the model-driven `bash` tool
  is now a real shell matching Pi — arbitrary commands run in the workspace
  (`bash -c <command>`), with combined bounded output and an optional timeout.
  Only metadata is archived.
- Project-defined verification policy beyond the Pi-style model-visible `bash` workflow. The
  former `/verify just-check` command has been removed from the user-facing REPL.
- Broad repo maps or persistent workspace summaries beyond the first bounded
  provider-visible context policy.
- Local model provider integrations for Ollama, llama.cpp, MLX, LM Studio, or
  similar runtimes until separate benchmark work identifies the best first
  local runtime and connection shape.
- Generic OpenAI subscription-backed native provider auth beyond the distinct
  `openai-codex` provider path until official OpenAI docs expose a stable
  third-party/native provider auth flow that is not specific to Codex,
  ChatGPT, or another OpenAI product client.

### Deferred For Product Maturity

- Codex JSONL event adapter.
- Claude integration beyond the existing conservative `pipy-session auto`
  metadata capture.
- Raw transcript import with explicit opt-in and redaction policy.
- Indexed archive search or SQLite-backed query layer.
- Review-cycle metadata shape for summary-safe appended events, including
  explicit per-round versus cumulative scope, review round number, and optional
  cycle identity so future archive analysis does not double-count iterative
  reviews. The former `pipy-session workflow` and `reflect` commands have been
  removed.
- Full interactive TUI behavior beyond the shipped product TUI shell. Prompt
  history, bracketed paste, undo/redo, resize/SIGWINCH handling, an interactive
  `/settings` control dialog, optional persistent cross-session prompt history,
  `@` file picker/path completion, clipboard/drag image paste, `!`/`!!`,
  thinking/model hotkeys, output/thinking folding, queued steering/follow-up,
  `/scoped-models`, `/hotkeys`, mouse-selection invariants, and true
  provider-request cancellation now ship. Theme controls
  (`ctx.ui.theme`/`get_all_themes`/`get_theme`/`set_theme`) and autocomplete
  provider wrappers also ship. Still deferred are the remaining richer
  extension-owned UI surfaces Pi exposes: broader editor/component-library
  behavior beyond the landed live `setEditorComponent` path, a full custom
  overlay stack beyond the bounded Python `ctx.ui.custom(factory, options=None)`
  path, and custom session-entry/message-rendering follow-ons.
  Render-once custom tool rendering now ships; live (invalidate-driven) tool
  rendering remains deferred.
- Extension/package platform follow-ons: package runtime composition for
  installed local-path/managed-git packages, package `update`, per-run
  source-loading flags, live-session operation gates, `user_bash`,
  provider-request transforms, dynamic active tool/model/thinking controls, and
  the first custom session-entry/message-rendering slice have landed. Remaining
  Pi gaps are broader dynamic-flag integration, richer extension UI/rendering,
  PyPI/npm package sources, and the corresponding supply-chain/security model.
  The target specification is [extension-api.md](extension-api.md).
- Provider/model catalog follow-ons after the selected closeout slices: live
  Anthropic/Copilot login UX and adapter parity polish.
- RPC and automation follow-ons: the stdin/stdout JSON/RPC mode ships; true
  in-turn steering, native/socket daemon transport, full session fork/switch
  over RPC, and the RPC extension-UI bridge remain follow-ons.
- Project-defined verification policy beyond the Pi-style model-visible `bash`
  and future extension-gate workflow.
- Multi-agent task delegation.
- Long-running dev server.

Historical deferral wording retained for tests: additional OAuth providers;
Full interactive TUI beyond the selected narrow `prompt-toolkit`; Textual or
another full-screen TUI framework; RPC mode.

## Explicitly Not Now

- Making Codex, Claude, or another coding-agent CLI wrapper the main product
  path.
- Storing full system prompts, user prompts, model outputs, stdout, stderr,
  tool payloads, secrets, tokens, credentials, private keys, or sensitive
  personal data in the `pipy-session` metadata archive, docs, or synced
  artifacts by default. The private native product session tree is the explicit
  Pi-like exception for raw conversation history.
- Building broad approvals, sandboxing, raw transcript import,
  non-allowlisted verification commands, Textual or another full-screen TUI
  framework, network/socket daemon transports, or orchestration
  opportunistically. Provider registry/catalog work is allowed only inside the
  selected provider-catalog track and its conformance gate; additional OAuth
  providers remain part of that track's reviewed milestones, not opportunistic
  side work.
- Using unsupported subscription auth, scraping browser or CLI session stores,
  or treating another product's login/session as pipy-native provider
  credentials.

## Runtime Resource Loading Track (landed 2026-05-30)

Closes parity rows D4 (skills), D5 (prompt templates), and D6 (custom slash
commands) with real runtime behavior, not file-existence rubber-stamps. This
is deliberately **not** a general extension API: only three bounded resource
kinds load, through the existing provider/session/tool/archive boundaries.

What shipped:

- `pipy_harness.native._resource_files` (shared discovery), `skills`,
  `prompt_templates` (with `$ARGUMENTS`/`$1..$9` expansion), and
  `custom_commands` loaders were reintroduced **with** a runtime consumer.
  Discovery is workspace-first then global (`PIPY_CONFIG_HOME` →
  `${XDG_CONFIG_HOME}/pipy` → `~/.config/pipy`), `*.md` one level deep,
  deduped by canonical path. Safety policy rejects secret-shaped filenames,
  binary content (NUL byte), generated/`.gitignore`-matched filenames,
  oversized bodies (per-file + total byte caps with truncation marker), and
  symlink-escapes.
- `pipy_harness.native.resources` is the registry + pure
  `dispatch_resource_command` consumed by the product tool-loop session.
  `/skill` lists or runs discovered skills; prompt templates register as their
  own `/<template-name>` commands; custom `/<name>` commands run through the
  same local-command boundary as built-ins and cannot shadow a reserved built-in
  name. Unknown/unsafe/empty resources fail closed with no provider turn.
- Wiring: `tool_loop_session.py` plus the product TUI. The TUI slash menu
  advertises `/skill`, prompt-template commands, and discovered custom
  commands. The `[Skills]` chrome section now lists loadable skill names from
  the loader.

Privacy: only safe counters/labels are recorded. The no-tool path emits
`native.resource.invoked` / `native.resource.rejected` events carrying
`{resource_kind, name, path_label, sha256, byte_length, truncated}` and a
`resource_invocation_count` in the completion event; the tool-loop path
returns `resource_invocation_count` in `NativeToolReplResult`. Resource bodies,
expanded prompts, and command text never reach the `pipy-session` JSONL archive,
Markdown summaries, prompt history, or exported metadata summaries.

Verification: unit tests for parser/discovery/precedence/safety and the
dispatcher; no-tool and tool-loop product-path tests (incl. archive non-leak);
real-PTY product-TUI tests at 80x24 and 100x40 for custom-command
discovery/execution and unsafe-resource rejection. The D4/D5/D6 parity-score
checks are behavior checks (`scripts/parity_score.sh`).

Out of scope for *this* track (skills/templates/commands): a general
extension/package loader and runtime UI hooks. Themes (D7) and image
attachments (D8) have since landed on their own — see the Native Session
Workflow / parity rows and `docs/pi-parity.md`.

## Native Session Workflow Track (landed 2026-05-30)

Closes parity rows E2 (session compaction) and E3 (session branching) with
real product behavior, and upgrades E1 (session resume) from a metadata-only
reader to a live runtime resume. Metadata-first archive defaults stay
mandatory throughout.

> **Superseded (2026-06-09):** the metadata-only `--resume RECORD` /
> `--branch LABEL` repl flags described below were **retired** in favor of the
> native product session tree ([session-tree.md](session-tree.md)), which is now
> the product session source for resume/branch/fork. This section is retained as
> historical record; `pipy-session resume-info` remains the separate archive
> utility.

What shipped (historical; `--resume`/`--branch` repl flags now retired):

- **Live resume.** `pipy repl --agent pipy-native --resume <stem>` seeds a
  fresh no-tool or tool-loop session from the existing metadata-only
  `ResumeContext`/`compose_resume_system_block` (prior provider/model/turn
  labels only). The prior finalized record is never mutated and no raw
  transcript sidecar is copied. Both REPL surfaces show a safe resumed-state
  banner; the tool-loop product TUI commits it to scrollback at startup.
- **Branch/fork.** `pipy repl --resume <stem> --branch <label>` forks a child
  with a validated safe label (`--branch` requires `--resume`; unsafe labels
  fail closed via `validate_branch_label`). `pipy_harness.models.SessionLineage`
  carries the safe parent id, relationship, branch label, fork timestamp, and
  prior provider/model/turn counters.
- **Compaction.** The live tool-loop's canonical message reduction now lives in
  `pipy_harness.native.agent.history`; product policy and persistence remain in
  the session. `/compact` (and an automatic threshold) reduce the
  provider-visible context while keeping recent turns plus a safe metadata-only
  summary appended to the system prompt. The retired no-tool path historically
  compacted its bounded exchange context; the tool loop cuts `AgentMessage`
  history only at
  `AgentUserMessage` group boundaries, so compaction never orphans a tool result,
  reorders a tool-call/observation pair, or exposes a raw tool payload.
- **Archive + catalog.** The runner writes a safe `resume` object onto
  `session.started` and emits `native.session.resumed`; compaction emits
  `native.session.compacted` counters. `pipy-session list/inspect/export/
  resume-info` surface the lineage and compaction metadata read-only and reject
  malformed/ambiguous/symlinked/active/out-of-archive records without leaking
  bodies.

Verification: unit tests (compaction trigger/state reduction + protocol
validity, lineage/branch-label validation, reader safety, runner archive
wiring); no-tool product tests (`--resume`, `--branch`, `/compact`, automatic
threshold, rejections, parent immutability); tool-loop product tests
(resumed/compacted provider requests, valid tool-message history, archive
non-leak); real-PTY product-TUI tests at 80x24 and 100x40 for resumed-state
visibility and `/compact`. The E2/E3 parity-score rows are behavior checks
(`scripts/parity_checks/compaction_behavior.py`,
`scripts/parity_checks/branching_behavior.py`).

Superseding direction: this landed track remains the metadata-archive resume /
branch / compaction baseline, but it is no longer the final product session
workflow. The shipped native session tree replaces metadata-only product resume
with a Pi-like private native session tree that stores raw conversation history
for `/tree`, `/resume`, `/fork`, `/clone`, durable compaction replay, and branch
summaries. Raw transcript import from external agents remains deferred.

## Maintenance Notes

- Reopen or replace `Completed Reviewed Program` only when the coordinator
  authorizes a new bounded program; the git log is the authoritative record of
  shipped work.
- Keep deferred items here brief; put detailed design and rationale in
  `docs/harness-spec.md`.
- Keep archive and privacy rules aligned with `docs/session-storage.md`.

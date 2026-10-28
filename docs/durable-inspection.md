# Durable inspection and operator recovery (DE10)

This private native boundary uses the existing Session, Storage and scheduler. The [DE11 async product factory](durable-product-session.md) composes this boundary; CLI activation remains DE12.
The canonical DE10 acceptance includes compatibility, metadata-only inspection,
supported versions, terminal reads without credentials, and serialized operator
resolution; it is not only an inspection formatter.

## Pre-code invariants

Inspection owns one committed Session read-line job. It never opens or recovers
a scheduler, enables execution, initializes/migrates documents or tasks, captures
live ports, prepares arguments, reconstructs a provider, dereferences private
material or refreshes auth. Finite support descriptors are captured when a task
definition is admitted. Session-only inspection always reports invocation
ownership unknown. Stored running does not prove a live worker. Opaque material
reference validity does not prove executability.

Default output is a closed allowlist of numeric identities/versions, known
families/statuses/phases/reasons and bounded dependency metadata. Raw task kinds,
checkpoints, requests, prompts, arguments, workspace/endpoint/header selectors,
material pointers, arbitrary error text and secret-derived hashes are excluded.
Unknown/newer envelopes retain safe outer metadata without payload decoding.
Terminal metadata requires no current definition, auth or resource.

The existing SQLite owner gains an explicit validate-current policy: effective
schema is read through a read-only WAL-aware preflight under the same PathOwner,
then a current supported store uses the existing writable connection with a
second validation and no migration. Older/newer effective schemas refuse before
that writable connection. Read-only preflight can create an absent empty WAL,
SHM and lock diagnostics. Writable refusal cleanup may implicitly checkpoint;
successful close may checkpoint/remove sidecars. Logical records remain read-only;
byte preservation is claimed only at applicable read-only refusal cuts.

Operator resolution is a closed scheduler-owned operation on the same Session
line. Its private expected-state capability binds the Session, current task,
parent/round and receipt existence/value. A durable conversation-scoped nonce
winner deduplicates acknowledgement loss and reopen. Foreign/stale targets,
abort/close, actual owned work and incompatible support refuse before effects.
No I/O, callbacks, joins, Future completion or foreign-reference disposal occur
under the lifecycle condition.

For operator retry, ANY receipt on a pending blocked tool means held-needs-repair: no retry,
mutation, retirement or settlement path. This hold also takes precedence over
an abort request; without a receipt, an abort request keeps its abort reason.
Automatic valid receipt reuse belongs to an actual still-settle task, which is
ineligible for operator retry. The separate [C2 adjudication operation](durable-adjudication.md)
can explicitly preserve supported blocked receipt authority or repair an exact
known empty default; this does not change the retry predicate. Receipt absence
does not prove that an unsafe effect did not occur.

An eligible tool retry is a NEW authorized child. One transaction retains old
uncertainty only in private conversation audit, records the old public outcome
exactly as `{"status":"aborted","reason":"operator_retry"}`, replaces only
the matching round slot/tools/on identity, and admits the new child. Existing
results, later slots, receipts and usage are unchanged. A generation retry needs
an explicit new seed and an eligible ownerless terminal task. It never resumes
a blocked terminate hold or changes an old success. Effects belong only to the
subsequent acknowledged scheduler reservation of the new task.

Nonfinal terminate controls are derived from actual round slots and committed
result controls: a waiting parent and later pending child can be held. Final/only
terminate leaves a pending blocked generation. Neither is terminal success or
progress. Full graph/control/repair/migration/unsafe continuation remain
DE13/14/15/24/25/30; graph parity and request-history/publication budgets remain
DE15/27/28/41. All43 whole parity rows remain OPEN.

## Private APIs and operation order

Existing active owners call `inspect_task(session, task_id)` or paged
`inspect_live(session, conversation_id=..., limit=..., cursor=...)`.
`ainspect_task`/`ainspect_live` check caller reentry before off-host work; canceling
a waiter does not detach an accepted physical read. Optional
`InspectionSupport((generation, tool))` is admitted from already captured
Task definitions before inspection. Do not construct live ports or factories
merely to fill that optional value. Supported means finite definition/format fit;
`execution_material=not_checked` always remains separate. Current auth, private
material or arbitrary environment availability is not probed.

For an existing closed store, `inspect_store(path, limit=..., cursor=...)` pages live metadata and
`inspect_store_task(path, task_id)` also reads terminal metadata. Both use
`mode="open", schema_policy="validate_current"`, refuse a missing path instead
of creating it, and refuse another process's lease. Invalid queries are admitted
before opening. An already active Session supplies its own line; no second opener
or scheduler is created. No operator CLI or product factory is activated here.

The owning `TaskScheduler` supplies synchronous/async pairs:

```python
target = scheduler.resolution_target(task_id)
# Only after an explicit operator decision authorizing a NEW attempt:
result = scheduler.resolve(target, nonce=uuid4().hex)
# Eligible ownerless terminal generation additionally requires new_request=seed.
```

Use the same nonce to recover a lost response; a different proposal with that
nonce conflicts. The response exposes only source_task_id/new_task_id. The
private target is bound to this scheduler/Session and is reacquired after reopen;
never serialize it, its expected state or the private audit to operator/workflow
output. A prior durable winner is read before stale/busy checks. A new winner
requires current exact state, receipt absence, matching original offered and
current supported tool declaration/workspace/implementation/schema/replay,
matching direct parent/round/slots and no real owned invocation/finalizer. The
same Session scoped transaction writes the audit, old outcome, new row and exact
slot/on replacement. Different concurrent approvals cannot mint two attempts.

A new attempt may repeat an old unsafe external effect; it is explicit new
authority, not proof that the old action failed or an exactly-once guarantee.
Old intents/keys/results and uncertainty are never rewritten into safe replay or
old success. Blocked receipt-present tasks need later repair. Still-settle
receipt reuse follows existing normal execution without current resources and is
not an operator-retry branch. Terminal completed tasks and retained terminate
holds cannot be retried by this operation. Old terminal generation rows remain
unchanged; an eligible new generation seed authorizes fresh preparation and
identity, without silently re-preparing an existing request.

Failures expose finite fixed codes only: closed/busy/stale/incompatible,
not_supported/conflict/receipt_held_needs_repair/retained_control/unavailable.
No stored transport/SQLite/port error or private payload is interpolated.
Invalid task or conversation IDs use `InspectionError("invalid_request")` on
sync, async and offline inspection, and `ResolutionError("incompatible")` on
resolution-target admission, before storage reads or offline path access.

## Evidence boundary

The nineteenth source-closed oracle uses genuine pinned Harness.inspect/getTask.
Only declared logical metadata/version fit, unchanged records and zero task-code
observations are paired. Pi raw reports/records remain separate; Pi has no generic
resolve API and its retryTask clears a parked restart count. Native redaction,
operator authorization, process cuts and physical OS ownership are native proof.

The separate native relational fixture actually preserves an uncertain applied
effect, closes/reopens, authorizes a new owned child, closes/reopens its nonce
winner before dispatch, then completes the round. Independent calls/applications/
preparations, acknowledged publication versus actual invoke/finalizer timestamps,
committed output/usage, old public outcome and private audit drive its predicates.
No lifetime/reuse/usage predicate is copied as a Pi constant. Both common and
native-only semantic controls execute real CLI0/1, separately from malformed2.

Every attester runs in a fresh process and imports explicit pure lazy dependency
roots matching actual execution scope. It rechecks sources/modules/inputs/
artifacts/processes without re-executing effects; any loaded-unlisted path or
scope/hash mismatch refuses. A shared interpreter's growing module map and a
missing lazy root are distinct evidence failures. Recipes/producer roots stay
immutable; all mutants are under mutants/ and all process receipts/streams under
processes/. Exclusions apply only to the current output root's stores/ and result;
nested result.json or an ancestor stores directory never disappears by basename.

Offline continuation reuses the existing positional Storage Cursor (after/order)
with the same store and filter, even after an empty terminal-only page. Every
page is a new committed read. It is not a durable replay cursor, a snapshot
revision or a cross-store capability; no Session identity or lease is bypassed.


The opt-in [DE11 async product session](durable-product-session.md) now composes
this existing native authority. It admits one input atomically, executes only
on explicit resume, and closes through actual Session ownership. Python
activation does not activate the CLI or change legacy defaults. Full private
target binding still occurs at first prepare; CLI/live and pre-prepare P16
acceptance remain DE12. Broader parity obligations remain OPEN.


## Recovered execute classification (DE13c1)

Inspection shares the finite noninvoking execute/receipt classifier used by
execution and public resume. It does not invoke ports, construct providers or
create documents. After operational restart parking admits dispatch, a valid
receipt settles without consulting later restart metadata as new receipt
evidence; without a receipt, actual current source counters must be exact
nonnegative safe integers or absent. The new interrupted cause adds no inspection
reason or waiter enum. Historical blocked records keep their existing reasons,
and every receipt on blocked work remains `receipt_held_needs_repair`, including
an older runtime's blocked/intent-None round trip. C2 owns migration; automatic
resume never replaces the existing operator-new-key authorization contract.


## Bounded restart parking (DE14a)

[Restart parking and same-task reset](durable-restart-parking.md) add an immutable
per-owner default-five ceiling and allowlisted inspection metadata. Public reset
requires existing profile/selection/ownership admission and does not dispatch;
explicit resume remains separate. It is distinct from new-attempt authorization.
Operational parking can delay valid receipt settlement without rewriting it.
Live settings and broader migration/ownership behavior remain deferred.


## Cold generic migration metadata (DE14b1)

[Generic migration](durable-task-migration.md) retains the exact 17-field report.
For current-newer generic definitions, absent migrators report
`unsupported/migration_failed`; present migrators report
`unknown/migration_required` until an owner actually observes failure. The
private scheduler's `inspect_task`/`ainspect_task` includes that owner's guarded
failure snapshot. Ordinary callback-free support carries only presence and cannot
fabricate observed history. No inspection calls a migrator.

`definition_version_mismatch` remains for incompatible built-in newer definitions;
model/tool frames are never admitted to generic migration. Missing/stored-newer,
terminal/completing and native authority precedence remain unchanged. The
inspection-v1 generic older-row comparison changes deliberately to
`migration_failed`, preserving its separate nine-field common projection and all
privacy/read-only controls.

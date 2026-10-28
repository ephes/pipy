# Cold task-definition migration (DE14b1)

The private native scheduler can migrate a generic task when a fresh owner has a
newer captured definition. Each catalog generation is immutable. A fresh owner
can restore missing code; the private idle controls below can publish a generic
definition on an existing owner. Neither path installs a public live registry or
migrates generation/model-tool authority; those records keep their existing
version, catalog and receipt fences.

## Definition and reservation

`Task` and `define_task` accept keyword-only `migrate=`. The synchronous callback
receives detached `(input, checkpoint, from_version)` and returns an exact plain
dictionary containing exactly `input` and `checkpoint`, both strict JSON. It has
no Runtime or transaction capability and must perform no external effects.
Coroutine/foreign-awaitable results are rejected without execution. Initializers
are never substituted for missing migrators.

Equal numeric versions use the original path without a callback or rewrite.
Versions retain the existing finite binary64 domain, including negative/fractional
values and signed-zero equality. Missing definitions and stored-newer versions
remain live without invocation. Eligible generic tasks with a newer definition
migrate during reservation, not open or inspection. Abort, ownership, historical
hold and restart-parking fences apply before the callback.

The Session line owns migration and reservation. Both validated outputs and the
new version enter the same commit as pending-to-running. A handler starts only
after acknowledged admission. Migration preserves identity, memos and restart
streak; it is not task-state progress. A JSON checkpoint with no handler can still
produce the existing subsequent phase fault.

An ordinary callback/validation failure leaves that generic task unchanged and is
remembered for the source/captured definition binding in this owner. The same transaction
continues scanning later candidates and preserves legitimate earlier kernel
reconciliation. No unrelated wake is required to run eligible work behind the
failed task. Repeated resume, inspect, notifications and same-task reset do not
retry that failed definition. Fresh owners may attempt their own definition.

Backend failure remains infrastructure failure. Before commit, the old task is
authoritative; COMMIT-then-raise may leave a migrated running task with no handler
ACK. Reopen recovers the actual committed state. Closing/cancelling waits for real
callback/backend ownership; no exactly-once migration or external-effect claim is
made. There is no durable migration failure/audit document.

## Inspection

Ordinary `InspectionSupport` captures only a migrator-presence boolean. Existing
`inspect_task`/`ainspect_task` and live-page functions never call it. The private
`scheduler.inspect_task(id)` and `await scheduler.ainspect_task(id)` additionally
observe that owner's remembered failure, taking the Session read line before its
metadata guard. Detached support cannot report an attempt it did not observe.

The full output remains exactly 17 fields. For generic newer definitions, a
present migrator yields `unknown/migration_required`; no migrator or an observed
owner failure yields `unsupported/migration_failed`. Missing and stored-newer
reasons remain unchanged. Built-in newer-definition refusals retain
`definition_version_mismatch`, regardless of callback attachment. Terminal and
completing metadata stays code-independent. Existing abort/receipt/graph/hold
reason precedence remains; `restart_parked` can be true even when a stronger
reason is displayed. No callback, exception text, input or checkpoint is exported.

The existing `inspection-v1` generic older-version observation deliberately now
uses `migration_failed`; its pinned Pi reason mapping and exact native expectation
change together. The nine-field common comparison remains separate from the
17-field native report. Historical raw acceptance retains its original meaning.

## Scope and evidence

See the [adopted contract](specs/2026-10-28-de14b1-cold-task-migration-plan.md) and
[evidence ledger](specs/2026-10-19-durable-execution-evidence.md#de14b1-cold-task-migration).
A bounded `task-migration-cold-v1` reference child follows the prior 25 phases.
Native detached callback inputs are an explicit difference from the pinned Pi
callsite; output ownership and committed state are compared without inventing
public API parity. Public registry replacement, active handover, live settings, public retry
enable, abort/orphan/ownership completion and model/tool migration remain separate
work. No DE14, DE13/P21 or whole parity row is closed by this increment.


## Private idle definition publication (DE14b3)

An opened `TaskScheduler` has `install_definition(task)` and
`remove_definition(name)`, plus `ainstall_definition` and `aremove_definition`.
The boolean acknowledges whether its immutable generation changed. New names
append; replacing a name preserves its position. The exact built-in names
`pi.generation`, `pi.tool` and `pi.compaction` refuse these controls. Constructor
registration remains separate and grants no generic built-in migration authority.

A publication uses the Session line and actual owner condition. Both an admitted
drain and an active invocation cause a busy `RuntimeError`; inspecting or pausing
at an earlier time is not admission authority. Enabled publication is best-effort:
a previous wake may already have admitted a pass. Successful installation,
including the identical-source false ACK, requests the existing wake only when
enabled. Changed removal does likewise; absent removal returns false without a
wake after normal health/open/activity checks. A paused owner stays paused.

The original Task object identifies its captured declaration. Unrelated updates
reuse captured bindings. Removing and reinstalling the same failed source keeps
its failure memo; a distinct Task object may attempt migration again. Reflection
that changes a known source is rejected against its early detached capture;
construct a new Task for an intentional declaration change. Installed bindings
and live single-task failure memos are the only retained identity authorities.
Terminal publication, replacement of a memo and owner retirement release unused
references outside the metadata condition.

Publication invokes no task code and writes no durable state, receipt, header or
counter. Async cancellation joins accepted line work and does not promise to
undo publication. Owner inspection and each reservation use one generation;
previously returned support/inspection snapshots remain stable. Resolution reads
current support inside its committing job, after saved-winner and metadata gates.
The full native DTO remains 17 fields and the Pi common projection remains nine.
These controls are not exposed by Product, the public SDK or CLI. See the
[contract](specs/de14b3-definition-publication.md) and
[development evidence](evidence/de14b3-definition-publication.md).

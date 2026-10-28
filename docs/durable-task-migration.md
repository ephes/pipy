# Cold task-definition migration (DE14b1)

The private native scheduler can migrate a generic task when a fresh owner has a
newer captured definition. Its catalog stays immutable for that owner. Close the
old owner and construct a fresh one to restore missing code or replace a failing
definition. This does not install a live registry or migrate generation/model-tool
authority; those records keep their existing version, catalog and receipt fences.

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
remembered only for the captured definition in this owner. The same transaction
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
public API parity. Registry replacement/handover, live settings, public retry
enable, abort/orphan/ownership completion and model/tool migration remain separate
work. No DE14, DE13/P21 or whole parity row is closed by this increment.

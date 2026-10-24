# Durable task runtime (DE05b)

Implemented against delivered DE05a `47a81103`. The private native scheduler
has one sequential invocation and a dedicated asyncio host. It does not activate
the product runtime, export a CLI bridge, or close a parity row.

## Ownership and ordering

The existing SessionLifecycle condition is the single explicit guard for both
lifecycle and scheduler metadata. Every reader and writer uses it: task index,
enabled/closing state, drain scheduling, tentative/active/ended invocations and
join ownership. No callback, backend call, future completion, loop wake, thread
join or final foreign reference disposal runs under that guard. The Session
line remains the only transaction authority.

Reservation rereads the task on the line, resolves an immutable definition and
registers its exact tentative invocation before staging running intent. A failed
reservation retires only that identity. Handler admission follows the completed,
acknowledged Session commit and line release; post-adoption observers only update
the index and request a kick. Phase-start admission and close sealing use the
same guard. A phase admitted before sealing remains owned through its finalizer.

Runtime writes check invocation validity before enqueue and again on the line,
including the latest running state and durable abort mark. Invocation eligibility
ends on the line. Its actual coroutine remains join-owned until it exits.
Cancellation of an API waiter does not cancel accepted scheduler or close work.

Lifecycle alone seals, drains and closes Storage. Its universal close predicate
requires completed close listeners, invocation join, ordinary line drain and the
exclusive terminal line stage, including a failed owner's already-REQUESTED
path. A lifecycle-owned join thread signals phases while their host loop stays
alive, joins their real finalizers and stops/joins that host. Only then can the
healthy terminal stage reread the latest admitted records and rewrite still
running tasks to pending without a restart increment or publication. Failed
owners skip the rewrite. Backend close and path-lock release are last. A phase
that ignores its signal leaves close pending; a timeout never releases ownership.

## Persisted task semantics

Open changes interrupted running tasks to pending and increments their restart
count once; it dispatches nothing. Explicit resume is idempotent. Definition
names, phase names and memo names use UTF-16 identity, including equivalent
Python spellings after JSON normalization. Version compatibility is numeric.
Creation still accepts arbitrary finite JSON checkpoints without phase checks.

Task progress uses structural JSON equality: object order and numeric zero sign
do not matter, booleans differ from numbers, arrays remain ordered, and missing
differs from null. Document OwnedJson equality remains strict. A changed state
resets restarts; document or memo chatter does not. Memos are first-writer-wins,
including null. Terminal state, entries and task-document retirement share one
transaction; terminal state drops memos and restarts.

Unknown/incompatible definitions, abort-marked work and waiting/sleeping/
completing records remain inspectable and durable. Unsupported ownership joins
must refuse before running a phase or committing a terminal outcome. Full
definition migration/restart policy is DE14; ownership/waits/completing DE15;
cancellation DE16; hooks DE22; reload DE23; rich codecs DE06b. These retained
boundaries do not remove any original DE05 acceptance cell.

## Required acceptance

Actual SQLite child-process SIGKILL cuts cover before/during reservation, after
running-intent COMMIT before acknowledgement/phase, and before/after checkpoint
COMMIT/acknowledgement. Repeated open/resume preserves input, checkpoint, memos,
abort marks and correct restart counts. Process groups use bounded stdin/stdout
barriers and full reap, followed by independent reads, integrity checks and lock
reacquisition. ControlledStorage interleavings supplement these real cuts.

Keyed intent/effect/receipt fixtures count external calls and applications
independently. An unsafe effect without a receipt remains uncertain; a safe
fixture reuses its committed idempotency key. Neither proves arbitrary effects
exactly once. Tests also cover end barriers, atomic terminal cleanup, close versus
held reservation/phase/checkpoint, cancelled waiters, actual Storage errors,
acknowledgement loss, adoption failure and failing final rewrite. Failed owners
never dispatch or retry later work. Native Linux, supported Python, actual pinned
Pi comparison, full checks, docs and independent review remain delivery gates.

## Use and scope

Create a private `Session`, then `TaskScheduler(session, definitions)`. `open()`
(or `await aopen()`) reconciles stored records without dispatch. Call `resume()`
explicitly to enable execution; repeated calls do not create another invocation.
`inspect()` returns an immutable tuple of owned records; `to_tree()` detaches
values. Close the Session, which owns the scheduler lifetime. Sync waiting
methods refuse event-loop threads. A scheduler opens once; reopening storage
uses a fresh Session and scheduler.

A phase receives `(current_task, runtime, context)`. `await runtime.commit(change)`
passes a scoped `TaskTx` and detached latest running record to `change`. Return
`None` for entry/document-only changes, a running checkpoint state for progress,
or a terminal outcome. DE07 additionally admits its narrowly validated initial
generation tool wait. `TaskTx.append_entry(draft)`, `doc(token, key=..., seed=...)`
and `retire_doc(token, key=...)` use the invocation scopes; there is no raw task
replacement or foreign-ID mutation through this facade. DE07 adds scoped
`entry(id)`/`entries()` reads and typed `create_task(task, input)` with the current
task forced as owner and its conversation forced as scope. A foreign entry read
refuses. The only new waiting transition is the actual generation receipt plus
all newly admitted model ToolTasks; unrelated ownership joins and terminal
live-child bypasses still refuse. See [generation](durable-generation.md).
`await runtime.memo(name)` returns `OMITTED` when absent; a candidate argument establishes a durable first
winner. `await context.signal.wait()` cooperates with shutdown. A phase that
returns without checkpoint progress faults; a committed terminal outcome wins
against a later exception.

A failed host-thread startup fails and closes its Session. A failed join-worker
launch reports failure while retaining ownership; a later `close()` retries that
launch. No timeout or launch failure authorizes releasing a running phase.

The [acceptance ledger](specs/2026-10-19-durable-execution-evidence.md#de05b-runtime-reservation-recovery-acceptance)
maps the original DE05 cells to actual native process/race tests and the pinned
reference comparison. All43 parity rows remain OPEN. The bounded scheduler scans
all current tasks; live-graph indexing/retention and performance ownership remain
DE27/28. It has no migration, park/retry, full task ownership, sleep, abort-handler,
tool execution or product activation claim. DE07 supplies the bounded provider
phase lifetime and real tool admission described in the generation contract;
[DE08](durable-tools.md) supplies the direct owned model-tool leaf and exact
matching-parent allSettled wake; general waits/graphs remain DE15.


The direct model-tool exception is checked at candidate, phase-start and runtime
commit boundaries, with current token/abort/input/owner/conversation/round-slot
and prior-result checks. It distinguishes a leaf being owned from a task owning
live descendants. Terminal live-child refusal stays universal. Graph I/O runs
only on the Session line; the lifecycle condition guards scheduler metadata and
never wraps callbacks, backend reads, joins or foreign-reference disposal.

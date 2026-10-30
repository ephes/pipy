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

[DE09 completed observation](durable-completed-projection.md) is a private reader
of this authority. Its complete synchronous read/registration job owns a real
Session line ticket in an off-host `owned_call`; the caller checks Session
reentry before scheduling it. Cancellation can finish its API/asyncio wrapper
while that ticket still retains actual reads and SQLite ownership. Observer
callbacks are caller-owned and do not participate in the runtime invocation join.
The projection neither dispatches on open nor rewrites task state/usage.

Open changes interrupted running tasks to pending and increments their restart
count once; it dispatches nothing. Explicit resume is idempotent. Definition
names, phase names and memo names use UTF-16 identity, including equivalent
Python spellings after JSON normalization. Version compatibility is numeric.
Creation still accepts arbitrary finite JSON checkpoints without phase checks.

Task progress uses structural JSON equality: object order and numeric zero sign
do not matter, booleans differ from numbers, arrays remain ordered, and missing
differs from null. Document OwnedJson equality remains strict. A changed state
resets restarts; document or memo chatter does not. Memos are first-writer-wins,
including null. Deciding an outcome drops memos and restarts at completing or
terminal. Only actual terminal state retires task documents atomically with
entries; a held completing outcome keeps those documents available.

Unknown/incompatible definitions, unsupported abort-marked work and sleeping
records remain inspectable and durable. The internal DE16a implementation adds
only the terminal-only ordinary root-leaf abort subset described below. Ordinary allSettled waits are resumable when every named
target is terminal; generic failFast remains unsupported. Ordinary generic ownership and completing
outcomes have the bounded [DE15b consumer](specs/de15b-owned-completing.md).
Reserved model/tool graphs keep their exact existing authority; other unsupported
joins refuse before a phase or terminal outcome. [Cold generic migration](durable-task-migration.md) is supplied by DE14b1;
private owner-local policy publication is supplied by DE14b2. Live definition
replacement and public live settings remain DE14b; full ownership/waits DE15;
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

After open, private `set_restart_limit(value)` / `await aset_restart_limit(value)`
publish a policy without durable writes, counter reset, wake or enable. Each
reservation, retry and owner inspection uses one snapshot on the Session line.
Explicit `resume()` requests a pass; see [publication and lifecycle semantics](durable-restart-parking.md#private-owner-local-publication-de14b2).

A phase receives `(current_task, runtime, context)`. `await runtime.commit(change)`
passes a scoped `TaskTx` and detached latest running record to `change`. Return
`None` for entry/document-only changes, a running checkpoint state for progress,
an ordinary allSettled waiting state, or a terminal outcome. DE07 retains its
narrowly validated initial generation tool wait. `TaskTx.append_entry(draft)`, `doc(token, key=..., seed=...)`
and `retire_doc(token, key=...)` use the invocation scopes; there is no raw task
replacement or foreign-ID mutation through this facade. DE07 adds scoped
`entry(id)`/`entries()` reads and typed `create_task(task, input)` with the current
task forced as owner and its conversation forced as scope. A foreign entry read
refuses. Reserved generation waiting requires the actual generation receipt plus
all newly admitted model ToolTasks. Ordinary waits use the separate
[DE15c graph validation](specs/de15c-ordinary-waiting.md). A healthy ordinary
generic terminal intent holds as completing while ordinary descendants remain;
final creation under a terminal/completing owner still refuses. See [generation](durable-generation.md).
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
DE27/28. The original DE05b evidence made no migration, park/retry, full task ownership,
sleep, abort-handler, tool execution or product activation claim. Later
[restart parking](durable-restart-parking.md) and
[cold generic migration](durable-task-migration.md) have separate bounded contracts. DE07 supplies the bounded provider
phase lifetime and real tool admission described in the generation contract;
[DE08](durable-tools.md) supplies the direct owned model-tool leaf and exact
matching-parent allSettled wake. DE15c supplies bounded ordinary allSettled waits;
full waits/graphs remain DE15.


The direct model-tool exception is checked at candidate, phase-start and runtime
commit boundaries, with current token/abort/input/owner/conversation/round-slot
and prior-result checks. It distinguishes a leaf being owned from a task owning
live descendants. Its reserved-task authority remains separate from the DE15b
ordinary completion hold; neither path may publish an early terminal owner. Graph I/O runs
only on the Session line; the lifecycle condition guards scheduler metadata and
never wraps callbacks, backend reads, joins or foreign-reference disposal.

## Closed operator authority (DE10)

[Inspection/recovery](durable-inspection.md) does not call open/recover/resume or
use the scheduler's candidate path as an inspection probe. Session-only reads
report invocation ownership unknown. The actual owning scheduler additionally
supplies private expected-state capture and explicit nonce-deduplicated new-
attempt authorization through the same scoped Session commit. Its existing
condition guards only owned metadata; current records/rounds/receipts are checked
on the line. Still-owned invocation/finalizer returns busy even after a blocked
checkpoint commits. Late runtime writes remain fenced after retirement, and
close never releases backend/path ownership early. No new scheduler flag, guard,
observer writer, general retry/migration or graph engine is introduced.


The opt-in [DE11 async product session](durable-product-session.md) now composes
this existing native authority. It admits one input atomically, executes only
on explicit resume or admitted same-task retry, and closes through actual Session ownership. Python
activation does not activate the CLI or change legacy defaults. Full private
target binding still occurs at first prepare; CLI/live and pre-prepare P16
acceptance remain DE12. Broader parity obligations remain OPEN.


## Bounded restart parking (DE14a)

[Restart parking and same-task reset](durable-restart-parking.md) add an immutable
per-owner default-five ceiling and allowlisted inspection metadata. Public retry
requires existing profile/selection/ownership admission and acknowledges reset
plus admission of an owned continuation. Active public resume joins that work;
new-attempt authorization remains separate.
Operational parking can delay valid receipt settlement without rewriting it.
Live settings and broader migration/ownership behavior remain deferred.

## Ordinary generic execution and held completion (DE15b)

The private scheduler dispatches compatible ordinary generic owned tasks and
permits their actual commit/memo calls through the same guarded invocation and
Session line. Existing TaskTx.create_task remains the typed creation API: a child
may be created in a legitimate earlier progress commit; creating owned work and
finishing its owner in the same final candidate still meets the existing strict
assembly refusal. No public SDK/CLI query or ownership mutation is added.

A completed terminal intent becomes completing while live ordinary descendants
remain. Traversal crosses ended ordinary intermediates and owned conversations,
but excludes background task subtrees. The one-active-phase scheduler retires
the phase normally so descendants can run. A Session-line fixed point finalizes
held outcomes bottom-up after children settle, also without any pi.tool definition
and during metadata-only cold recovery. Open still dispatches no handler.

Effective foreground ancestor cancellation intent blocks fresh ordinary dispatch
and gated mutation callbacks: nonterminal abortRequested or a held outcome other
than completed. Every reachable ancestor matters, with background cuts; a
background owner's own intent still governs its foreground children. No descendant
abort mark, abort handler, forced signal or fake terminal cleanup is manufactured.
Failed/marked owners remain held while ordinary descendants remain.

For an already admitted handler, previously admitted off-line work is still owned
and may finish; this is not a DE16 cancellation barrier. Later commit/memo refuses.
Before fault/no-progress handling, the phase-step decision preserves its checkpoint
and restart count in pending, including a propagated InvocationEnded or a swallowed
refusal followed by a no-progress return. Actual finalizers still join before
close releases backend/lease ownership. Ordinary allSettled waiting and cycle
rejection have the bounded DE15c consumer below; failFast, abort cascade and whole
DE14/DE15/P09/P10 acceptance remain open.

## Ordinary allSettled waiting (DE15c)

An ordinary handler may return a waiting RawTaskState from its existing
TaskRuntime.commit callback, retaining the exact durable checkpoint and naming
existing task IDs. The invocation ends normally. Explicit enabled scheduling
reserves it as running at that checkpoint only when every target is terminal,
regardless of outcome; completing remains live. Empty lists are ready, duplicate
IDs do not create additional authority, and a live target lacking code stays live.
Cold open is metadata-only and preserves waiting checkpoints and restart counts.

The same transaction's immutable capture and genuine staged create/replace
records determine target existence, readiness and combined wait/ownership cycles.
Validation performs no backend reads after callback writes. Terminal targets stop
wait traversal; terminal ownership intermediates still expose live descendants.
Background subtrees cut ownership, while an explicitly named background task
retains its own wait edges. Abort-marked historical waiters contribute ownership
edges without their on edges. Traversal is iterative and evaluates no task code.

Unknown targets and cycles raise UnsupportedTaskExecution before publication;
malformed states retain RecordError. Generic failFast is refused up front until
DE16 provides actual cancellation/settlement. Intentional earlier ID allocation
is distinct from durable table rollback. Existing current-token, Session-line,
FIFO, close, restart and reserved generation/tool checks remain authoritative.
Private native development on immutable tree7ec9 passed Primary206 + retained223
(429 distinct) and independent Broad18, with whole mypy1143, Ruff/format1275 and
configuration/coverage/strict-doc checks. These results remain qualified to that
source epoch. Independent CODE R1 was accepted as advisory with 0 Critical,
0 Warning and 1 wording Suggestion fixed, not CODE CLEAN. The subsequent private
473→581f integration retained all nine separately corrected upstream test pins.
Joint 4b088 focused 709 and combined b06 finite 27/whole-quality checks remain
separate source epochs; they do not rebind 7ec9 or establish a new paired profile.
Full P09/failFast/DE16 and all 43 parent criteria are not closed. See the
[bounded specification](specs/de15c-ordinary-waiting.md) for the historical
checkpoint and integrated source boundary.

## Terminal-only ordinary leaf abort (DE16a)

[DE16a](specs/de16a-ordinary-leaf-abort.md) adds internal `aabort_leaf` marking
for a paused, retired ordinary pending root leaf. Mark ACK never enables work;
explicit resume captures immutable abort mode and calls the actual definition's
abort handler. Supported cleanup ends terminal, throws or returns without an
outcome; the latter two fault through a genuine transaction. Nonterminal
progress/wait/sleep/completing and newly owned work refuse before publication.
Current-token, failed-owner, candidate graph and actual close joins are retained.

First mark removes the previous run counter; repeat marking rechecks exact
current-definition eligibility and does not reset the abort counter. Actual
running recovery charges an interrupted attempt, while a pending mark alone
proves no callback entry. The finite abort cutoff faults before code resolution.
The private [DE16b successor](specs/de16b-marked-missing-definition.md) settles an
already-marked eligible pending leaf as `orphaned/missing_task` when the captured
current generation has no definition. This is a terminal transaction, not an
invocation: no initial/run/abort/migrate callback or attempt increment occurs.
Task memos and task-scoped documents retire normally in that transaction.
Present older/newer definitions still hold indefinitely until exact-version
restoration; missing code grants no new marking right. Mark admission, complete
candidate graph, lifecycle/enable gates and cutoff-first ordering remain intact.
No automatic retry, migration, cascade or public abort authority is added.

Native 0d3 Core/retained523 yielded 522 PASS and one qualified Memory cold-reopen
skip; dfdb Broad33/migration62 were separate successful runs. The 512a repair
passed 114 of 115 scoped cases with the same skip and received independent CODE
R2 CLEAN, preserving advisory R1 separately. Combined b06 finite27 and whole
quality1151/1283/configuration/docs checks retain their own epoch.

DE16b Core94 on 74f0 yielded 93 PASS and the same qualified Memory skip. Source
9cc retains those production/Core bytes; its separately executed Broad42, whole
quality and current34/strict-doc checks passed and implementation CODE R1 was
CLEAN. Root accepted one public SQLite mark/remove/resume/terminal-retirement
program per Native/Pi4a08 lane, with initial1/docinitial1/run0/abort0/migrate0
including Native postclose. It is not a cold-crash, internal-counter or durable
Pi uninstall-ACK proof. The external fixture review is a separate gate. These
scoped receipts do not themselves establish a final-source Full or delivery.
Generic failFast, full DE16 and all 43 parent criteria remain open; physical
interrupted-abort observations belong to actual Broad cuts, not typed controls.

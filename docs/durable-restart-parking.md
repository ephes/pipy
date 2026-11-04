# Durable restart parking (DE14a)

Pi durable release 1.5.0, the campaign target since 2026-11-03, removed restart
parking and `retryTask()`: a task cut off at the restart limit now ends visibly
instead. Bridge slice R15-2 ports that in sub-slices; see the
[1.5 bridge](specs/2026-11-03-durable-release-1.5-bridge.md#r15-2-restart-limit-endings-instead-of-parking-and-retry).
R15-2a removed same-task retry: the Product and SDK `retry_task`,
`DurableTaskRetry`, the `pipy durable retry-task` command and the private
scheduler retry. R15-2b ends the built-in `pi.tool` and `pi.generation` tasks at
the limit (see [below](#built-in-restart-limit-endings-r15-2b)); only generic
tasks still park until R15-2c, so the rest of this page describes that
remaining 1.4-aligned parking. Bridge slice R15-1b withdrew the pinned
comparisons of this behavior,
which have no 1.5 reference: the phases `restart-parking-v1` and
`task-product-retry-enable-v1` and the parked cases of the migration, live
policy and orphan phases (see the
[dispositions](durable-reference.md#release-15-dispositions-r15-1b)).

The native scheduler parks a recovered generic task after five consecutive
restarts without a structurally changed task checkpoint. Parking prevents the next
reservation; it does not rewrite the checkpoint or produce a result. Opening a
scheduler recovers an interrupted running row to pending and increments once,
without dispatching. A healthy close preserves the count without incrementing.
Reopening an already pending row does not increment again.

Changed task state clears the current streak, as do completing and terminal
transitions. Equal checkpoints, memo writes and document-only changes do not.
In particular, an acknowledged retained tool-output or details update preserves
the restart count: that ACK confirms a progress-document commit, not a changed
task checkpoint.

## Owner policy and inspection

`create_durable_product_session` and `open_durable_product_session` accept
`restart_limit=5`. The private `TaskScheduler` and inspection APIs accept the
same keyword. The policy is not stored in the header or database. A fresh owner
can raise the limit or pass `math.inf` to disable parking; that is the only way
to run parked work again, because no same-task reset exists. Public Product
owners keep their constructor policy for their lifetime; they expose no live
update. The private scheduler can publish a new immutable policy as described
below.

Raw Python policy values must be exact builtin numbers representable as finite
binary64. Values at least one are floored. Invalid values, bools, subclasses,
NaN, negative infinity, values below one and nonexact/overflowing integers use
the default five without conversion errors. Positive float infinity is the
explicit unlimited sentinel. This is not a safe-ID bound: `2**1000` is valid,
whereas `2**53 + 1` is not exact binary64 and defaults to five. Stored task
records retain their existing numeric ABI; their counters are not rounded.

All five durable CLI commands accept `--restart-limit`. It accepts the exact
spelling `Infinity` or finite JSON-number syntax. Exponent forms are allowed;
`nan`, `inf`, `+Infinity`, `-Infinity` and overflow such as `1e400` are invalid
requests. CLI text deliberately parses as binary64: `9007199254740993` rounds
to `9007199254740992`. The equivalent Python float selects that same limit,
while the exact raw Python integer defaults to five.

Inspection exposes `restart_count` (null when missing), `restart_limit` (null
when unlimited), and `restart_parked`, which is never true for a built-in
task. `restart_limit` is also a reason when no more specific hold reason
applies. Passive store inspection describes its own
explicit/default policy, not the configuration of an unrelated live owner.
It performs no recovery or dispatch and exposes no task payload. Public resume
returns held when parked work prevents continuation.

## Built-in restart-limit endings (R15-2b)

The built-in `pi.tool` and `pi.generation` kinds never park. Their phases check
the count when they start, reading the owner's current policy only for a task
that was cut off at all, and at the limit end the task instead of running its
work again, as Pi durable release 1.5 does (bridge U01, U05):

- A tool call in its `call` or `execute` phase, a safe replay included, ends
  with an error result built from its retained progress, as an interrupted call
  is: the committed output, details and diagnostics. Its native receipt keeps
  the `interrupted` ending with the recovery reason `restart_limit` (upstream's
  diagnostic code), the attempt count and the restart count, and the model sees
  `Tool execution was interrupted N times in a row; it is not run again.` as the
  closing harness notice ("once" for one restart). The task fails with error
  detail `{"reason": "restart_limit"}`, and the run continues. In the `call`
  phase no intent or progress exists yet: the result is empty, its receipt has
  no intent, and the tool is neither resolved nor prepared. A count outside the
  interrupted-cause domain (not a nonnegative safe integer, which only a foreign
  or typed record can hold) cannot be recorded in the cause: the phase refuses
  it and the task faults, while Product resume still refuses such an
  execute-phase call in its preflight, before scheduling. Because the ending
  needs no current capability, Product resume admits a call at the limit under
  a changed tool catalog, where a call that would run again still refuses; the
  limit includes the restart that opening the scheduler adds to a cold running
  call, and only a count the ending can record qualifies.
- A generation in its `prepare`, `request` or `tools` phase fails with
  `The generation was interrupted N times in a row in its <phase> phase` and the
  same detail, without another provider request. Native generation has no
  `pi.live` run or input records yet, so the run failure record and the inputs'
  `unanswered` settlement belong to DE19. Until DE17 charges a request that was
  in flight at host death, recovery resends it, so repeated host death in
  `request` ends a run `restart_limit` where 1.5 ends it `interrupted`.
- A `settle` phase only commits what a receipt already holds and runs no tool
  or provider again, so it settles that first winner at the limit too, as an
  answered 1.5 run completes. The settled outcome then decides the task.

Committed task progress still starts the count over, and a terminal outcome
drops it. The ending records its reason and restart count (in the tool receipt
and in the error message) for the DE39 reports; pipy has no task report channel
yet, so nothing reports `task_interrupted`. The pinned
`interrupted-tool-continuation-v1` comparison adds the case `safe_limit`: an
actual release 1.5.0 run of a safe call cut off once at limit 1 and pipy's
SIGKILL cut and CLI resume with `--restart-limit 1` agree that the call is not
run again, keeps its retained output and details, fails with the
`restart_limit` cause and lets the run answer.

## Private owner-local publication (DE14b2)

After opening a healthy private scheduler, `set_restart_limit(value)` and
`await aset_restart_limit(value)` return whether its normalized limit changed.
An exact `RestartPolicy` copies its limit; other inputs use the same constructor
rules above. Publication uses the existing Session line and owner condition,
without a Storage commit, counter reset, notification, enable or invocation.

Use `scheduler.resume()` to request a new pass after raising the limit. A paused
owner stays paused; an independently triggered pass or an already enabled drain
can observe the policy at its next reservation. Lowering a limit does not revoke
an admitted invocation. Each reservation pass and same-owner inspection uses one
policy snapshot. `scheduler.inspect_task` and
`await scheduler.ainspect_task` observe that owner; standalone inspection still
uses its own explicit/default limit. A fresh owner does not recover an earlier
owner's unpersisted policy.

The setter requires completed open/recovery. A cancelled `aopen` waiter does not
cancel recovery; eligibility follows actual open publication. Failed, closed,
not-yet-opened and reentrant calls retain their bounded refusal. The synchronous
setter cannot block an event-loop thread. Async cancellation joins the actual
line job and does not promise rollback: publication may already have happened.
A repeated value then returns false on the same still-live owner. Close retains
physical ownership until accepted work retires.

This private API is not exposed through Product, SDK or CLI. Updating a Product's
internal scheduler is unsupported: its public admission and inspection continue
to use its immutable constructor policy. The [bounded contract](specs/2026-10-28-de14b2-live-restart-policy-plan.md)
keeps public live settings, listeners and registry handover separate.

## Receipts and uncertainty

A valid receipt is the call's first winner at any count. A receipt-bearing tool
task with count `7.5`, `2**53` or `1e300` settles its receipt at the default
limit, although those values are outside C1's narrower interrupted-cause
counter domain; the saved winner and its counters remain byte-identical.
Below-limit malformed/missing-counter behavior and opaque receipt fences remain
unchanged, and a held historical block stays held: the limit never dispatches
it. A C2 decision on such a block commits its interrupted receipt, which then
settles even at the limit, and decision-only nonce replay stays available.

An interrupted or restart-limit receipt records the current streak that the
owners counted. There is no cumulative restart audit, and neither a higher limit
nor the limit ending authorizes an old unsafe invocation or repairs historical
blocked authority.

The [implementation contract](specs/2026-10-28-de14a-restart-parking-plan.md)
keeps public live settings, registry handover, orphan/cleanup and abort budgets
for remaining DE14b and later ownership work; its reset and retry obligations
ended with R15-2a. Generic cold migration is supplied
by [DE14b1](durable-task-migration.md). General cancellation/graph behavior, provider
attempt backoff, and whole DE14/DE13/P21 parity remain open.

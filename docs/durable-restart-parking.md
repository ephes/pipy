# Durable restart parking (DE14a)

The native scheduler parks a recovered task after five consecutive restarts
without a structurally changed task checkpoint. Parking prevents the next
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
can raise the limit or pass `math.inf` to disable parking. Public Product owners
keep their constructor policy for their lifetime; they expose no live update.
The private scheduler can publish a new immutable policy as described below.

Raw Python policy values must be exact builtin numbers representable as finite
binary64. Values at least one are floored. Invalid values, bools, subclasses,
NaN, negative infinity, values below one and nonexact/overflowing integers use
the default five without conversion errors. Positive float infinity is the
explicit unlimited sentinel. This is not a safe-ID bound: `2**1000` is valid,
whereas `2**53 + 1` is not exact binary64 and defaults to five. Stored task
records retain their existing numeric ABI; their counters are not rounded.

All six durable CLI commands accept `--restart-limit`. It accepts the exact
spelling `Infinity` or finite JSON-number syntax. Exponent forms are allowed;
`nan`, `inf`, `+Infinity`, `-Infinity` and overflow such as `1e400` are invalid
requests. CLI text deliberately parses as binary64: `9007199254740993` rounds
to `9007199254740992`. The equivalent Python float selects that same limit,
while the exact raw Python integer defaults to five.

Inspection exposes `restart_count` (null when missing), `restart_limit` (null
when unlimited), and `restart_parked`. `restart_limit` is also a reason when no
more specific hold reason applies. Passive store inspection describes its own
explicit/default policy, not the configuration of an unrelated live owner.
It performs no recovery or dispatch and exposes no task payload. Public resume
returns held when parked work prevents continuation.

## Private owner-local publication (DE14b2)

After opening a healthy private scheduler, `set_restart_limit(value)` and
`await aset_restart_limit(value)` return whether its normalized limit changed.
An exact `RestartPolicy` copies its limit; other inputs use the same constructor
rules above. Publication uses the existing Session line and owner condition,
without a Storage commit, counter reset, notification, enable or invocation.

Use `scheduler.resume()` to request a new pass after raising the limit. A paused
owner stays paused; an independently triggered pass or an already enabled drain
can observe the policy at its next reservation. Lowering a limit does not revoke
an admitted invocation. Each reservation pass, retry transaction and same-owner
inspection uses one policy snapshot. `scheduler.inspect_task` and
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

## Reset the same task, then resume explicitly

```python
ack = await product.retry_task(task_id, selection=selection)
assert ack.task_id == task_id
# ack.reset says whether this operation removed the current count.
result = await product.resume(selection)
```

The public reset is noninvoking. It requires the matching profile, catalog,
workspace, header and selected-run ownership. It removes only the current
parked task's restart count; task ID, checkpoint, input, memos, receipts and
audits remain unchanged. It creates neither a new tool attempt nor a nonce.
A repeated reset normally acknowledges `reset=false`; after a later crash a
new reset can remove the newly accumulated streak. This is not a durable
historical idempotency promise.

```sh
pipy durable retry-task --store run.sqlite --workspace . \
  --provider openai --model MODEL --target-task 123 --format json
pipy durable resume --store run.sqlite --workspace . \
  --provider openai --model MODEL --format json
```

For a retry-successor selection, add the existing `--task` and
`--resolution-nonce` pair to both commands. `--target-task` alone identifies the
counter-reset target. `authorize-retry` still authorizes a separate NEW attempt
and retains its existing receipt fences and nonce semantics.

A surviving stored running task has not yet acquired its crash-recovery count.
A cold reset refuses `unavailable` without writes; explicitly resume first.
For example, running count four becomes pending count five and held on resume;
reset then removes the count, and a further explicit resume continues. No
implicit recovery occurs during reset. Unknown/foreign/incompatible targets,
wrong loops, reentry and closed owners keep their existing bounded error codes.

The private scheduler's `retry`/`aretry` requires an opened healthy owner. It
clears only parked live work and enables scheduling after a successful call,
including a no-op. Public product reset deliberately does not enable; full Pi
public retry-enable parity remains open.

## Receipts and uncertainty

There is no receipt exemption from a valid finite operational ceiling. Even a
receipt-bearing task with count `7.5`, `2**53` or `1e300` parks at the default
limit, although those values are outside C1's narrower interrupted-cause
counter domain. Reset or an unlimited fresh owner allows the existing
receipt-first classifier to proceed; the saved winner and its counters remain
byte-identical. Below-limit malformed/missing-counter behavior and opaque
receipt fences remain unchanged. C2 validated decision-only nonce replay is
still available while work is parked.

Reset intentionally discards the current budget observation. If an interrupted
receipt is first created after reset, its absent current counter is recorded as
null/unknown, never zero or the discarded old count. A later genuine crash
records its new streak. There is no cumulative restart audit. Reset never
authorizes an old unsafe invocation or repairs historical blocked authority.

The [implementation contract](specs/2026-10-28-de14a-restart-parking-plan.md)
keeps public live settings, registry handover, orphan/cleanup and abort budgets
for remaining DE14b and later ownership work. Generic cold migration is supplied
by [DE14b1](durable-task-migration.md). General cancellation/graph behavior, provider
attempt backoff, and whole DE14/DE13/P21 parity remain open.

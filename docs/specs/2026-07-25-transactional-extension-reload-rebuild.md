# Transactional extension reload — rebuild plan and concurrency contract

Status: reviewed plan for the active Slice 3 of the
[Architecture Quality Improvement Program](2026-07-24-architecture-quality-improvement-plan.md).

Date: 2026-07-25.

This document replaces an abandoned Slice 3 implementation attempt. It records
the ownership and synchronization contract the rebuild must obey, the bounded
sub-slices, the stop conditions, and the behavioral scenarios the finished
slice must demonstrate. It describes intended work, not landed behavior;
`docs/architecture.md` continues to describe only what has shipped.

## Why the first attempt was abandoned

The first attempt grew into a distributed prepare/apply/rollback transaction
spanning `NativeReplProviderState`, `CodingSessionState`, `SettingsManager`,
`KeybindingsManager`, package/workspace resources, process-global theme state,
and persisted defaults. Each participant gained a revision counter, a prepared
value, apply/rollback methods, and compensating restoration. Independent review
kept finding new interleavings, because the mechanism itself was unsound:

1. **Compensating rollback is not atomicity.** Restoring a previous value after
   a partial apply is a second mutation, not an undo. Any reader between the
   apply and the restore observes a state that never existed as a generation.
2. **Optimistic revision checks without a shared lock do not exclude anyone.**
   Reading a revision and then assigning is a check-then-mutate window. Unless
   every writer of that field takes the same lock — or the field is
   single-thread-owned — the check proves nothing.
3. **Fallible work cannot live inside the commit.** Holding a generation lock
   across file writes, provider construction, extension activation, diagnostics,
   message delivery, rendering, or arbitrary callbacks converts every one of
   those failures into a partially committed generation.
4. **"Immutable" generations that share mutable containers are mutable.** A
   retained generation whose tool visibility, provider selection, settings
   contents, or renderer map is the same object as its successor's is not a
   generation at all.

The rebuild keeps the goal — publish one complete extension generation
atomically — and discards the mechanism.

## Executable facts this contract is built on

These were verified against the tree at `3dae3b8` and are the reason the
contract can be small.

**Exactly one thread runs a session.** Every mode drives the coding session on a
single thread:

- interactive TTY and captured-stream modes run it on the process main thread;
- `--mode json` / `--print` run it on the calling thread; and
- `--mode rpc` runs it on one dedicated worker (`rpc.py` `_run_worker`), while
  the socket reader thread only pushes text into a FIFO channel.

Command interpretation, `/reload`, provider-request preparation, event
projection, and session-tree writes all execute on that one thread. Call it the
**session thread**.

**Detached workers are the only real concurrency.** The session thread spawns
`pipy-provider-turn` (`agent/provider_turn.py`), `pipy-tool-call`
(`agent/tools.py`), `pipy-ext-activate` (`extension_loader.py`),
`pipy-local-shell`, and RPC bash workers. In the settled path the session thread
joins them before continuing, so they are not concurrent in any observable
sense. **On cancellation they are joined with a timeout and the session thread
proceeds regardless** (`agent/tools.py` `_execute_interruptibly`,
`agent/provider_turn.py`). A cancelled-but-still-running extension tool handler
therefore outlives the operation that started it and can call back into the
session afterwards — including after a subsequent `/reload`.

**A straggler's *result* is already discarded; only its *side effects* escape.**
This bounds the problem sharply, and it is existing behavior, not an aspiration:

- `agent/tools.py` `_execute_interruptibly` returns a cancellation outcome and
  never reads `result_holder` when completion did not precede cancellation, so a
  late tool result reaches neither history, nor the session tree, nor the event
  projection;
- `agent/provider_turn.py` does the same for a late provider completion; and
- the invocation output gate is closed on cancellation, so a straggler's
  streamed output stops reaching the event sink.

A late completion therefore cannot mutate a newly published generation through
the normal result path. What a straggler *can* still do is call back into pipy
from inside its handler — and those calls are exactly the four port classes
below, governed by their rules rather than by the result path. The contract must
keep this true, so a test pins that a tool worker completing after its
cancellation contributes no result message, no result-derived session-tree
entry, and no result-derived event.

That single window is the whole concurrency problem. It does not require a
general-purpose transaction; it requires that (a) the live generation pointer is
published under a lock the stragglers also take, and (b) every straggler-
reachable port is bound to the generation it was created for.

## The concurrency contract

### One synchronization boundary

`_RunControlState` owns exactly one `threading.RLock`, named
`session_state_lock`, created once per `NativeToolReplSession.run()`. It is
reentrant because a mutation port may be invoked from inside another port on the
session thread (`extension_set_model` re-enters the peer provider effects).

**Guarded state.** A field is guarded when a straggler can reach it, directly or
through any port. **Every** reader and writer of guarded state takes
`session_state_lock` — the session thread included. A lock that only one side
takes excludes nobody, which is exactly how the abandoned attempt failed. The
guarded set is:

- `_RunControlState.extension_generation`, the generation identity used for
  staleness checks, and the `publication_pending` gate described below;
- provider selection and thinking level in `NativeReplProviderState` — reachable
  from `extension_set_model` / `extension_set_thinking_level` and written by
  `/model`, model cycling, and auth changes on the session thread;
- the provider binding, canonical history, usage accumulator, and compaction
  state in `CodingSessionState` — `extension_set_model` reaches these through
  the provider rebind, which clears live history and resets usage;
- the provider-visible active tool selection in the tool-capability owner —
  reachable from `extension_set_active_tools`;
- the frozen state value inside each of `SettingsManager` and
  `KeybindingsManager` — extension tool ports and hook contexts are constructed
  against settings-derived values such as project trust, so a straggler can
  observe them;
- the enqueue and reading of a generation's user, custom, and notification
  queues, and their delivered cursors; and
- a generation's extension chrome state, on both the writing and rendering side.

The staging host is the one piece of cross-thread state guarded by something
other than `session_state_lock`: the activation worker writes staged
contributions while the session thread harvests and seals them, so the host's
staged contributions **and** its sealed flag share the host's own guard, taken
by writers and harvester alike. It is a separate guard because the staging host
exists before any generation does, and it is never held while
`session_state_lock` is held.

**Session-thread-owned state — no lock, and documented as unreachable from a
straggler:**

- `_RunControlState.session_tree`, `tree_filter_mode`, `pending_prefill`,
  `agent_settled_pending`, and `extension_in_agent_turn`;
- `_RunControlState.package_roots` and `workspace_resources` — read only on the
  session thread, but assigned inside the commit critical section so publication
  stays one step; and
- all terminal UI and renderer state (the TUI keeps its own paint lock for
  terminal writes; that lock is unrelated and is never nested inside
  `session_state_lock`).

If a future change gives a worker a path to any session-thread-owned field, that
field moves into the guarded set in the same change.

**The lock is never held across slow work.** An operation that needs guarded
state takes the lock, reads one consistent snapshot (generation plus the
provider/history/tool values it needs), releases it, and runs on the snapshot.
Provider turns, tool calls, rendering, and file I/O all happen outside the
critical section. Serializing turns is not the goal; excluding torn reads and
lost updates is.

**Nothing is released under the lock.** Dropping the last reference to a value
runs its finalizer and any weakref callbacks right there, which would smuggle
arbitrary code into a critical section that claims to contain none. So a single
rule applies everywhere, not only at commit: **any value displaced while the
lock is held is moved into a retired holder that outlives the critical section,
and is released after the lock.** This covers the commit's superseded generation,
settings, keybindings, resources, history container, and usage accumulator; it
equally covers queue entries compacted below a delivered cursor and chrome
values replaced by a class B write. A critical section overwrites references and
hands the old ones outward; it never lets them die.

### What a generation is

A generation has three parts, and conflating them is what made "immutable
generation" ambiguous in the first attempt:

- a **frozen projection** — commands, hooks, tools, providers, renderers, flags,
  shortcuts, parsed flag values, and the derived tool-capability and renderer
  state. Once published this never changes. "A retained old generation remains
  unchanged" is a statement about exactly this part, and it is what every
  consumer snapshots and reads.
- **owned mutable sidecars** — the generation's user/custom message queues, its
  notification queue, and its extension chrome state. These are append/update
  targets by design; the outbox list identities are load-bearing and pinned by
  `tests/test_native_session_extension_generation.py`. A straggler may still
  write into a retired generation's sidecars, and that is harmless precisely
  because nothing reads a non-live generation's sidecars.

- **generation-scoped selection state** — the active provider selection, the
  thinking level, and the active tool set. These are mutable *while the
  generation is live*: class A ports and session-thread writers such as `/model`
  change them under the lock, which is the whole point of class A. They are not
  part of the frozen projection and must not be described as if they were.

  At commit they are **rebased, not carried by reference**. The candidate
  computes each one from the outgoing generation's value — read in the same
  critical section that opened the publication gate, so no accepted mutation is
  missed — combined with the new catalog and registry: the provider selection
  survives unless it disappeared or lost tool-call support, the thinking level
  survives, and the active tool set survives filtered to names the new registry
  still defines. That filtering is today's behavior. The published generation
  then owns its own selection state, and the retired generation's copy stops
  being read at the swap.

  **A prepared value must never restore a superseded selection.** Reading the
  outgoing value at gate-open is only sound while the gate is open. Where a
  component publishes without one — a sub-slice landing before the gate exists,
  or any publication path that does not open it — the rebind must instead read
  the live selection *inside the publication critical section* and assign that
  reference onto the value being published. This is still a reference
  assignment, so it does not weaken the pointer-only rule, and it is strictly
  stronger than the gate for this field: no mutation accepted at any point
  before the swap can be overwritten. Publishing a selection captured earlier,
  outside the critical section, is the lost-update bug this contract exists to
  prevent.

So a retired generation is immutable where it matters and inert where it is not.
No part is ever shared with a successor: a new generation gets new projections,
new sidecars, and its own rebased selection state.

### Straggler-reachable mutation ports

These are the pipy-owned entry points an extension handler can call from a
worker thread. Every one of them belongs to exactly one of four classes, and the
class fixes its rule. There is no fifth, undocumented case: a new extension-
facing port is assigned a class in the same change that adds it.

| Class | Ports | Rule |
| --- | --- | --- |
| A — session-state mutation | `extension_set_model`, `extension_set_thinking_level`, `extension_set_active_tools`, and the hook-context model controls | generation-bound, gate-checked, lock-guarded; check and mutation in one critical section; fails closed |
| B — live-surface state | extension chrome: header, footer, widgets, title | writes into the *owning* generation's chrome state; only the live generation's chrome state is rendered |
| C — generation-owned queues | `send_user_message`, the custom-message equivalents, and extension notifications | appends to the *owning* generation's queue under the lock; only the live generation's queues are drained |
| D — contribution registration | commands, tools, providers, shortcuts, flags, message and entry renderers, event hooks | activation-scoped; sealed when the candidate is frozen or disposed |

**Class A** captures the `SessionGenerationId` of the generation it was
constructed for. Under `session_state_lock` it compares that id with the live
generation's id and checks the publication gate below; on a mismatch or an open
gate it mutates nothing and fails closed (`False`). Check and mutation happen
inside the same critical section, so this is not the rejected optimistic-CAS
pattern.

**Classes B and C never check liveness and never fail *for staleness*.** They
can still be refused for capacity or discarded after their sidecar is closed —
see those rules below — but there is no liveness comparison in them to fail, and
that is the property that matters here. Their acceptance semantics are exactly
three: accepted, refused because the queue is at capacity, or discarded because
the sidecar is closed. Where a port already returns a boolean, that boolean
reports acceptance; where it returns nothing today, the outcome surfaces only
through the diagnostic described under the capacity rule, so no existing
signature changes. A check-then-act port
cannot be made safe here: whatever it verified under the lock can be superseded
before the effect lands, which is the same check-then-mutate window that sank
the first attempt. Instead these ports are *write-only into the generation that
owns them*, and liveness is enforced at the reading end, where the reader
already holds the lock and the live pointer at the same instant:

- a class B write updates its own generation's chrome state; the TUI renders
  chrome from whichever generation is live;
- a class C append lands on its own generation's queue; the session drains only
  the live generation's queues.

**Both ends of both classes take `session_state_lock`.** Write-only-into-my-own-
generation removes the *staleness* window; it does not remove the *tearing*
window. A reader that takes the lock while writers do not is synchronized with
nobody. So a class B write updates chrome state under the lock, and a render
takes the lock, copies an immutable chrome snapshot, releases it, and paints
from the copy — painting never happens inside the critical section, and the
TUI's own paint lock is still never nested inside `session_state_lock`.
Likewise, class C appends and drains both take the lock, and delivery of a
drained batch happens after release — with the single named exception for user
messages described below, whose effect is an in-memory queue push.

A retired generation's writes therefore land in state nothing reads, with no
window between the pointer swap and disposal. A candidate's writes land in the
candidate's own chrome state and queues — that *is* the isolated staging sink
the candidate phase requires — and become visible at the instant the candidate
is published, never before. This is also how "removed or disabled extension
chrome clears only after a successful commit" holds: the committed generation's
chrome state simply lacks the removed extension's contributions, and post-commit
reconciliation repaints from it.

**Class C detail — appends land on the generation's own sink, bound at
drain.**
`send_user_message` and the custom-message equivalents append to the outbox list
objects that the generation's extensions captured at activation. Those list
identities are load-bearing and are pinned by
`tests/test_native_session_extension_generation.py`, so a straggler's append is
*not* refused: it lands on its own generation's outbox, exactly as it does
today. Staleness is enforced at the other end — the session drains only the live
generation's outboxes, so an append to a superseded generation is retained and
never delivered. Rejected candidates are disposed before they are ever live, so
their outboxes are likewise never drained. "No delivery from a non-live
generation" is the invariant; refusing the append is not.

Outbox contents are guarded state, so **both ends take the lock**: enqueue
appends under `session_state_lock`, and the session reads under the same lock.
Without that, an append racing a read is lost.

**Delivery is an acknowledged cursor, not a copy-and-clear.** Clearing a queue
before performing fallible delivery loses whatever was in flight when delivery
fails, and makes retry unsafe — the opposite of the idempotence the post-commit
phase claims. Each generation's queues are therefore append-only and never
cleared, and the session holds a per-queue **delivered cursor** as guarded
state:

1. under the lock, copy the pending slice from the cursor to the end;
2. release the lock and deliver, in order, advancing the cursor under the lock
   after each individual message rather than once per batch.

A failure part-way through a batch therefore leaves the cursor at the last
acknowledged message and the next drain resumes exactly there. Concurrent
appends land past the end of the copied slice and are picked up by the next
drain.

**The guarantee is in-order, gap-free delivery over an idempotent sink — not
at-least-once delivery.** Stated as the single rule an implementation and its
tests can enforce:

1. messages are delivered in queue order, starting at the delivered cursor;
2. no message is skipped — if message *i* was delivered, every message before it
   was delivered;
3. a delivery pass stops at its first failure and reports it; the messages
   behind that failure stay pending, are retried by the next drain while the
   generation is live, and are dropped with a diagnostic if the generation
   retires first; and
4. a message may be attempted more than once, and a repeat is harmless.

Nothing here promises eventual delivery, because a failed attempt is fail-soft.
Correctness therefore rests on an idempotent sink — not on a dedup table.
Delivery performs its effect before
the cursor advances, so a failure between the effect and the advance re-delivers
that message. A "seen ids" set does not fix this: unless recording the id is
atomic with the effect, the same window simply moves. The sink itself must be
idempotent.

So:

- every queued message carries a **session-scoped id**, assigned at enqueue
  under `session_state_lock` from a monotonic counter owned by the session
  rather than by a generation. Ids are therefore unique across generations and
  across reloads. This counter is the **one explicit exemption** to the
  candidate phase's "touches no live state" rule: a candidate's extensions may
  enqueue during activation, so they consume ids. Consuming an id mutates no
  semantic state — ids are opaque and only ever compared — so a rejected
  candidate leaves nothing behind but a gap in the sequence, which no consumer
  can observe. The alternative, generation-local ids, would break the
  cross-generation uniqueness the cutoff and the tree-entry identity both rely
  on;
- **custom messages** have a multi-step delivery — session-tree append, event
  projection, render — and only the first step is retried. The durable append
  *is* the delivery: its entry identity is derived deterministically from the
  message id, so a repeat is a no-op, and the delivered cursor advances as soon
  as it succeeds. Event projection and render then run as **post-delivery
  presentation derived from the committed tree**, under the same fail-soft rule
  as every other presentation effect in this contract: a failure is diagnosed,
  not retried. Nothing re-emits an event, so nothing can duplicate one, and no
  "already emitted" bookkeeping has to exist. The cost is stated plainly: a
  projection or render failure leaves the message durably recorded but not shown,
  which is exactly the trade the post-commit phase already makes for chrome and
  theme;
- **user messages** are different, because replaying one would start a duplicate
  turn. Their delivery effect — pushing the message onto the session input
  queue — happens *inside the same critical section* that advances the delivered
  cursor, so record and effect are atomic and a user message cannot be replayed
  at all. The session also keeps a monotonic highest-accepted-id watermark,
  checked in that same section, so an out-of-order or repeated id is refused.

  This is the one **named exception** to "deliver outside the lock", and it is
  narrow by construction. The rule that matters is *no slow or arbitrary work
  under the lock*: a push onto an in-memory queue calls no handler, renders
  nothing, touches no file, and releases nothing. Custom messages and
  notifications get no such exception, because their effects include session-tree
  appends and rendering; they deliver outside the lock and rely on idempotence
  instead; and
- **notifications** are a redraw and are idempotent by construction.

With an idempotent sink there is no dedup state to own, lock, or retain, and no
record-then-effect race. The delivered cursor stops being a correctness
mechanism and becomes what it should be — an optimization that avoids
re-attempting work already known to be done. The guarantee this contract makes
is in-order, gap-free delivery over an idempotent sink; a test covers a duplicate
delivery attempt explicitly and asserts a single applied effect.

**The no-loss guarantee is scoped to one generation's lifetime.** A retired
generation's queue is never read again and is never handed to its successor, so
anything still undelivered on it when the pointer swaps is dropped. That is the
current base's behavior — a reload abandons the old outbox and the new runtime
gets new lists — and this rebuild does not change it.

The ordinary case is nonetheless made lossless, via an **atomic cutoff** rather
than a drain-then-gate sequence. Draining first and opening the gate afterwards
would leave a window in which an append lands after the drain but before the
gate — a message that arrived "before the gate" yet gets dropped at publication.
So the two are one step, and they form the cutover phase described below:

1. in a single critical section, open the publication gate **and** record, per
   queue, the id of its last message as the drain cutoff;
2. deliver every pending message whose id is at or below that cutoff, using the
   normal locked-copy / unlocked-deliver / locked-advance rule.

The cutoff is a **message id, not a queue index**. An index would be invalidated
by the front compaction described above, which rebases a queue and its cursor
while the gate is open and could shift a post-cutoff message below a recorded
index. Ids are monotonic and never reused, so "at or below this id" survives any
amount of compaction.

Every message appended after the gate opened is unambiguously post-cutoff. The
pre-cutoff range is attempted **in order, up to the first failure**: the cursor
is contiguous and delivery order is observable, so a message that fails cannot
be skipped to reach the ones behind it. The cutover therefore stops at that
point and emits one diagnostic naming the failure and the number of pre-cutoff
messages left unattempted. It does not abort the reload, and those remaining
messages are dropped with the retired generation. That is a deliberate,
diagnosed loss on a failure path, not a silent one. Reload runs on
the session thread between turns, so in practice the pre-cutoff set is exactly
what a normal drain would have taken, and the only post-cutoff writer is a
straggler from a cancelled operation — whose append is dropped at the swap
exactly as it is today, or delivered normally if the reload is rejected and the
generation stays live. If the pre-cutoff delivery itself fails, the failure is
reported and the reload proceeds; the messages it could not deliver are dropped
with the retired generation rather than blocking the reload.

**Growth is bounded two ways.** Append-only is a delivery rule, not a retention
rule: under the lock, entries strictly below the delivered cursor may be
detached and the cursor rebased in the same critical section, with the detached
entries handed out to be released after the lock per the rule above. This
preserves the list *object* identity the existing characterization pins — only
its already-delivered contents go away, exactly as they do today.

Compaction only helps once messages are delivered, so each queue additionally
carries a **bounded pending capacity**. While the publication gate is open the
session thread is busy preparing a candidate and drains nothing beyond the
recorded cutoff, so a hung reload plus a hung straggler would otherwise grow the
live queue without limit. An append that would exceed the capacity is discarded
under the lock and counted, and the first discard emits one diagnostic naming
the queue. Dropping the oldest undelivered message instead is not an option —
delivery order is observable — so the newest is refused, which is also the
behavior an extension can reason about.

That handles the live generation. A *retired* generation's queues are never read
again, so nothing would ever compact them, and a hung worker could append to one
forever. Disposal therefore **closes** the retired generation's sidecars: a
one-way flag on the sidecar itself, set under the lock, after which an append is
discarded and a chrome write is dropped. This is not a liveness check — it does
not compare anything to the live pointer, so it reintroduces no check-then-act
window. Before the flag is set the writes land somewhere nothing reads; after it
is set they land nowhere. Either way no consumer sees them, and memory is
bounded. Rejected candidates are closed the same way at disposal.

### The publication gate

Generation binding alone does not prevent lost updates. Candidate preparation
reads live provider selection, thinking level, and active tool visibility, then
publishes derived values some time later. A straggler that mutates one of those
fields *validly* in between — its generation is still live, so its check passes —
would be silently overwritten by the commit. Serializing only the final
assignments does not close that window.

`_RunControlState` therefore carries one more guarded field,
`publication_pending`, holding the id of the generation being superseded, or
`None`.

- The cutover phase opens the gate under `session_state_lock`, in the same
  critical section in which it reads the live values the candidate will build on
  and records the outgoing message-queue cutoff described under class C. The
  lock is then released; the gate stays open, the lock does not.
- While the gate is open, every generation-bound mutation port fails closed
  (`False`) even though its generation id still matches. Refusal is already the
  defined contract for these ports, and a mutation arriving in this window comes
  from a straggler whose originating operation was cancelled.
- The gate closes when the *whole* publication is done, not at the pointer
  swap. A reload swaps the generation pointer partway through and republishes
  provider selection, tool visibility, and renderer projections afterwards;
  reopening mutations at the swap would let a change be accepted and then
  overwritten by those later projections.
- The gate also closes *before* post-reload extension lifecycle hooks run. A
  `session_start` hook from the freshly activated generation may legitimately
  call `setModel`; refusing it would be a behavior change, not a protection.
- A rejected candidate closes the gate under the lock with no swap, and
  mutations are accepted again. **Closing the gate is guaranteed, not
  best-effort:** it happens in a `finally`, and before the candidate's fallible
  cleanup — sealing, closing sidecars, releasing listeners and chrome requests —
  rather than after it. Cleanup that raises or hangs must never leave the live
  generation permanently gated, which would silently disable every extension
  mutation for the rest of the session. The candidate is not live, so
  re-admitting mutations before its cleanup finishes is safe.

**Admission must be atomic with the effect.** Reading the gate and then
applying a mutation are two critical sections; a port that does so can pass the
check and land its effect after a reload has already read the state it will
republish. A gate-checking port must therefore hold the session mutex across
both the check and the effect. That is only possible where the effect is purely
in memory: `set_active_tools` qualifies, while `set_model` and
`set_thinking_level` currently persist a default and append to the session tree
inside the mutation, and holding the mutex across file I/O is forbidden above.
Until provider construction and persistence are lifted out of those two ports
(S3.7c and S3.8), the gate narrows their window rather than closing it. This is
a recorded residual, not a met guarantee.

No accepted mutation can occur between the candidate's read of live state and
the commit, so the candidate cannot overwrite one. Because the gate is opened
and closed under the lock but not *held* across the candidate phase, no fallible
or slow work runs inside a critical section. A reload that never finishes leaves
the gate open and extension mutations failing closed — the fail-closed direction.

`/model`, auth, and other session-thread writers take the lock like everyone
else. They cannot observe an open gate, because the session thread is the one
running the reload.

**Candidate activation is not affected by the gate**, and this is a structural
fact rather than a lucky ordering. Class A ports are exposed on the *handler*
context built per command, tool, or hook invocation — `make_extension_context`
wires `set_model`, `set_thinking_level`, and `set_active_tools` at invoke time —
not on the activation API an extension's `activate()` receives. An activating
extension therefore has no way to call them, with or without a gate, so opening
the gate before activation changes no extension-visible behavior. The contract
depends on that separation, so a test pins it: the activation API exposes no
class A port, and adding one would require staging the mutation into the
candidate instead.

### Four phases

**Cutover phase (touches the live generation, before any candidate exists).**
This phase exists because the candidate phase must be genuinely isolated, and
the two things that unavoidably touch live state cannot be smuggled into it. In
one critical section: open the publication gate and record the outgoing message
queues' cutoff. Then, outside the lock, deliver the pre-cutoff messages by the
normal locked-copy / unlocked-deliver / locked-advance rule, in order and
stopping at the first failure. A delivery failure here is reported and does not
abort the reload. Nothing about the candidate exists yet.

**Candidate phase (fallible, no lock held across its work, nothing live is
touched except the exempted message-id counter).** Build every
value that can fail, against a staging host that is not reachable from the live
generation: candidate settings/keybindings state, package roots and workspace
resources, the candidate `_ExtensionRuntime` (own outbox lists, own listener
registrations, own chrome request sink), parsed candidate flags, derived tool
ports, tool-capability state, renderer maps, provider catalog contributions,
command names/descriptions, shortcut keys, and — when the active selection would
be rebuilt or would disappear — the replacement or fallback provider object,
and for the fallback case a fresh usage accumulator and a fresh empty
live-history container. Any failure disposes the
candidate — close the publication gate first and unconditionally, then seal
contribution registration, close its sidecars, and release its listeners and
chrome requests without delivery — emits the existing diagnostic, and returns.
The live generation is untouched because nothing ever pointed at the candidate.

**Sealing the staging host.** Disposal is not enough on its own. Extension
activation is driven on the `pipy-ext-activate` worker and is bounded by a
timeout, so a slow or hung `activate` coroutine can keep running after the
session thread has given up on it — still holding the staging host and still
able to register commands, tools, providers, renderers, flags, shortcuts, chrome
requests, listeners, and messages. Dropping the sinks it already filled does not
stop the next registration.

The staging host therefore carries a one-way **sealed** flag, set under the
staging host's own guard. It applies to **class D — contribution registration —
and only class D**. Those registrations become the frozen contents of a
generation value, so they must stop the moment the candidate is frozen. This
matches the current base, where contributions are harvested from the activation
API once and a later registration already has no effect; sealing makes that
explicit and observable rather than incidental. A registration attempted after
the seal reports the same disabled-extension outcome activation already uses.

Classes A, B, and C need no seal, because their staleness is already handled:

- **Success:** seal class D *before* freezing the candidate, so the published
  generation cannot gain a late contribution and stays immutable. Classes A, B,
  and C stay live and belong to that generation, which is required — a command
  or tool handler calls `send_user_message` or updates its footer long after
  activation.
- **Rejection:** seal class D, then drop the sinks. A rejected candidate is
  never assigned a live generation id, so its class A ports fail their liveness
  check forever, and its class B chrome state and class C queues are never read.
  Nothing it produced is delivered, and nothing it produces later can be.
- **Retirement:** when a generation is superseded, its class A ports start
  failing their liveness check at the instant of the pointer swap, and its class
  B chrome state and class C queues stop being read at that same instant.
  Post-commit disposal then releases its
  listeners and chrome requests. There is no window between the swap and
  disposal in which a straggler can act on the live session.

**Commit phase (non-fallible, under `session_state_lock`).** One critical
section assigns **everything the reload prepared**, from values the candidate
phase already built:

1. the settings manager's frozen state value and the keybindings manager's
   frozen state value (each manager keeps its object identity);
2. `_RunControlState.package_roots` and `workspace_resources`;
3. the generation's frozen capability and renderer state, its provider catalog
   contributions, and its rebased selection state — provider selection, thinking
   level, and active tool set, computed as described under "What a generation
   is";
4. the coding-state provider binding, whenever the candidate phase determined
   the bound provider *object* changes. There are two such cases and both exist
   in the current base:

   - **Refresh** — the selection still resolves, but the reloaded catalog
     produced a new provider object for it. This is the ordinary case for an
     extension-contributed provider, which is rebuilt on every reload. Today's
     `refresh_provider` covers it: the binding moves to the new object while
     history and usage are preserved. Publishing a new catalog while coding
     state still points at the previous generation's provider object would both
     violate the coherent-generation requirement and keep a disposed extension's
     provider alive, so this case is staged, not skipped.
   - **Fallback** — the selection disappeared or lost tool-call support.
     Today's `rebind_provider` covers it: the binding moves to the fallback
     provider, live history is cleared, and usage is reset.

   In both cases the new provider object is constructed in the candidate phase,
   as are the fresh usage accumulator and fresh empty live-history container the
   fallback needs, so the commit *assigns* them rather than clearing anything in
   place. In-place clearing would drop references and run finalizers inside the
   critical section, which the pointer-only rule forbids; the superseded
   history and accumulator are moved into the retired record and released after
   the lock, exactly like the superseded generation. The resulting history clear
   and usage reset are the existing characterized rebind behavior and land in
   the same critical section as the new provider, so no reader observes a new
   provider against old usage.

   **Compaction state is deliberately not touched by either case.** Today's
   `rebind_provider` documents that the compaction suffix survives the
   transition, to preserve the characterized provider/auth/reload behavior, and
   `refresh_provider` retains all state. So the fallback needs no prepared
   compaction value and the commit assigns none; compaction is guarded because a
   straggler-triggered rebind executes alongside it, not because a reload
   replaces it. The only change here is mechanical: the in-place
   `self._messages.clear()` becomes an assignment of the prepared empty
   container, so no finalizer runs inside the critical section.

   When neither case applies — the selection resolves to the same provider
   object — reload touches coding state not at all. It never computes a
   candidate history or usage from the live values, so a history or usage update
   accepted before the gate opened cannot be overwritten by a wholesale
   replacement;
4. `_RunControlState.extension_generation` and its identity; and
5. the publication gate, closed.

Nothing published here is left to the post-commit phase. Items 1 and 2 are what
make "settings, trust, keybindings, package resources, and theme state remain
old on rejection and become new on success" a whole-generation property instead
of a sequence of independent updates; publishing them anywhere else would let a
reader see new commands against old settings. Package roots and workspace
resources are otherwise session-thread-owned and need no lock for their own
sake, but they are assigned here so publication is one indivisible step.

The critical section contains pointer assignments and nothing else. No I/O, no
provider construction, no activation, no callbacks, no rendering, no
diagnostics, no compensating restoration.

Assignment alone can still run arbitrary code: dropping the last reference to a
replaced value invokes finalizers and weakref callbacks at that point. The
commit therefore **pins what it replaces**. Every superseded value — the old
generation, the old settings and keybindings state, the old resources, and any
history container or usage accumulator displaced by a fallback rebind — is moved
into a locally held retired record before it is overwritten, and that record
outlives the critical section. Disposal of the retired generation — releasing its
listeners and chrome requests — is post-commit work, never a step inside the
lock.

**Post-commit phase (fail-soft, lock held only for brief reads).** Dispose the
retired generation, deliver the committed generation's custom messages,
reconcile terminal chrome, refresh footers and command menus, mirror the
committed theme into the process-global `PIPY_THEME` and repaint, and persist
implicit project trust and defaults. These are idempotent effects derived from
the committed snapshot. A failure here reports a diagnostic and never claims the
semantic generation rolled back, because it did not.

"Post-commit" means no slow or arbitrary effect runs under the lock; it does not
mean the phase is lock-free, and it does not exclude the named user-message
exception above. The reads it needs follow the same rule as everywhere else: copy the
live generation's pending message slice under `session_state_lock`, deliver
after release, then advance the delivered cursor under the lock; take a chrome
snapshot under the lock and paint from the copy after release. Each locked step
is a copy or a cursor advance, nothing more.

**The theme is two different things and the split matters.** The *semantic*
theme is the value inside the committed settings state; it is published in the
critical section with everything else and is therefore atomic. The
*presentation* theme is the process-global `PIPY_THEME` mirror and the repaint
that follows; those are post-commit and fail-soft. If the mirror or repaint
fails, the committed theme setting is still the new one and every subsequent
read of it sees the new value — the terminal is simply still showing the old
colors until the next successful paint, and a diagnostic says so. The scenario
checklist is satisfied at the semantic level; it does not claim the process
environment is transactional, because it cannot be.

### Run teardown

The lock, the generation pointer, and the whole contract are scoped to one
`NativeToolReplSession.run()`. That leaves one last straggler case: a worker
that outlives the run entirely. Its class A ports would still find a matching
generation id, and if the process reuses provider or coding state for a later
run, those ports would mutate it under a lock the new run does not hold.

Teardown therefore closes the session explicitly. In one critical section at the
end of `run()`, and in a `finally` so an aborted run is covered too:

- replace the live generation pointer with a terminal "no live generation"
  value, so every class A liveness check fails from that instant on; and
- close the sidecars of that generation, so class B and C writes are discarded
  rather than accumulating.

Superseded values are pinned and released after the lock, as everywhere else. A
later run constructs its own lock, its own generation, and its own ports; a
straggler from the previous run can match none of them.

### Snapshot discipline

Every operation that reads extension-owned state takes **one** snapshot at its
start, in a single locked read, and reads from that snapshot for its whole
duration. No consumer keeps a separately refreshed contribution map. This is
what makes "either wholly old or wholly new, never a mixture" an observable
property rather than an aspiration.

The snapshot pins the whole *configuration and contribution* view, not just the
generation pointer:

- the generation's frozen projection — commands, hooks, tools, providers,
  renderers, flags, shortcuts, parsed flag values, tool-capability and renderer
  state — and a handle to its sidecars;
- the `SettingsManager` and `KeybindingsManager` **frozen state values**, not the
  managers. The managers keep their identities and their state is replaced at
  commit, so a consumer that reads through a manager mid-operation would see new
  settings against an old generation. Generation-bound consumers therefore read
  the pinned values, and hook and tool contexts are constructed against them;
  and
- `package_roots` and `workspace_resources`.

**When this becomes load-bearing.** While the only publisher is `/reload` on
the session thread, a consumer that re-reads the live generation per access
cannot observe two generations within one operation — there is no concurrent
writer to interleave with. Snapshot discipline is therefore introduced with the
reference (S3.4) but only becomes *required* in the slice that lets a detached
worker publish or that adds generation-bound ports (S3.7). That slice must
convert consumers before, not after, it introduces the second publisher;
shipping the publisher first would open exactly the mixed-generation window
this section forbids.

The snapshot deliberately does **not** pin provider selection, thinking level,
or the active tool set. Established behavior lets a `before_agent_start` hook
change the model for the current turn, so those are read under the lock at their
point of use rather than frozen at operation start. That is a characterized
exception, not an oversight; changing it requires a dedicated behavior slice.

### What is not transactional, by construction

- **Trusted extension module side effects.** `import`/`activate` in a trusted
  extension may touch the filesystem, spawn processes, or make network calls.
  Those are outside pipy's in-process transaction and are documented as such;
  pipy-owned registries still publish nothing until commit.
- **Persisted defaults and implicit trust.** File writes are post-commit,
  idempotent, and atomic at the file level where practical. They are not
  rollback participants.
- **Process-global state** such as the `PIPY_THEME` mirror. It is post-commit
  and last-writer-wins by definition. The committed theme *setting* is
  transactional; its process-global reflection is not.

### Rules this contract makes non-negotiable

- No new revision counter, rollback framework, consistency exception hierarchy,
  or transaction participant may be added without amending this document first.
- No inheritance from an uninitialized concrete class to fake a surface; use a
  narrow `Protocol` when the composition root needs two implementations.
- No `object.__new__` hand-copying of managers; build state values through
  normal typed APIs.
- Tests exercise observable behavior and explicit synchronization seams, not
  private transaction internals. Green tests are evidence, never a concurrency
  proof — the ownership model above must stay small enough to reason about
  directly.

## Bounded sub-slices

Hard bounds, per semantic slice: at most ~400 changed lines, at most four
production source files, at most one new module-level abstraction. Formatting-
only and docs-only slices are exempt from the line bound but must stay
mechanically or textually isolated. If a slice needs provider state,
configuration managers, and coding state at once, it is split before it is
written.

### S3.0 — formatting-only baseline

`ruff format` output for exactly the files later Slice 3 sub-slices touch:
`native/keybindings.py`, `native/tui.py`,
`tests/test_native_extension_lifecycle.py`,
`tests/test_native_extension_tool_renderer.py`,
`tests/test_native_tool_capabilities.py`, and
`tests/test_native_tool_loop_session.py`. Formatter output only; no manual
edits, no behavior change, no backlog or changelog update. This is a strict
subset of the eventual Slice 15 formatting program and does not pre-empt its
batching.

### S3.1 — concurrency contract

This document, landed as the reviewed contract before any code depends on it.

### S3.2 — renderer pinning

`native/tool_renderers.py`, `native/tui.py`, and the focused renderer test. The
renderer map used to render a tool result must be the one associated with the
tool set advertised for that request, not whatever visibility is live when the
result renders. Both renderer owners (captured-stream and live TUI) must obey
the same rule.

### S3.3 — tool-capability candidate values

`native/tool_capabilities.py`, the narrow reload construction seam in
`native/tool_loop_session.py`, and `tests/test_native_tool_capabilities.py`.
Build fresh or copy-on-write capability state for a candidate instead of
mutating a published registry through `replace_extensions`. Introduce a narrow
`Protocol` if the composition root needs more than one implementation.

### S3.4 — session-owned generation reference

A session-owned module (`native/session_generation.py` if a module is
warranted) holding the generation reference, its identity, and the shared lock.

Two pieces are deliberately **not** in this slice, because landing them here
would mean production state with no caller and no test that exercises it:

- the **publication gate**, which lands with the provider projection in S3.7
  where the class A ports that must observe it exist; and
- the **consumer conversion to one snapshot per operation**. This slice
  provides `snapshot()` and routes every read through the session mutex, but
  consumers still read per access. That is sound only while `/reload` on the
  session thread is the sole publisher — see "When this becomes load-bearing"
  under Snapshot discipline. S3.7 must convert consumers *before* it introduces
  a second publisher.

`_ExtensionRuntime` ownership stays in `extension_runtime.py`; that module must
not start importing settings, keybindings, provider construction, coding state,
or the TUI. Separately refreshed hook/flag paths are deleted only once the
snapshot path is complete.

### S3.5 — immutable settings and keybinding state

`native/settings.py`, `native/keybindings.py`, their focused tests, and at most
one composition seam. Each caller-owned manager keeps its object identity and
owns exactly one frozen state value (`SettingsManager._raw`/`_errors` and
`KeybindingsManager._user` collapse into one frozen value each). Reload builds a
new value through the normal typed load path; publication is one assignment.
Settings and keybindings split into separate commits if the bounds require it.

### S3.6 — coding/provider binding under the contract

`native/coding/state.py`, the generation boundary, and
`tests/test_native_coding_state.py`. Bring provider binding, canonical history,
usage, and compaction state under `session_state_lock` on both sides, as the
guarded set requires: the session thread's own reads and writes take the lock
too, not only the straggler-reachable rebind path. Message history and usage
survive concurrent and stale operations. No optimistic check whose writers
bypass the boundary.

### S3.7 — provider projection and generation-private catalog

Expected to be the largest area; split further rather than exceed bounds. The
candidate provider catalog, selection, and binding must be unreachable from the
old generation. `/model`, auth, thinking, and extension hook controls publish
against the operation's expected generation; stale operations fail closed rather
than mutate the latest generation. No provider construction under the lock.
`before_agent_start` model changes keep their current effect on the current turn
unless a dedicated behavior slice changes that contract.

### S3.8 — post-commit defaults persistence

Owned by the settings/default storage boundary, not by a transaction
participant in `tool_loop_session.py`. Persistence runs after the semantic
generation is committed, is idempotent, and is atomic at the file level where
practical. Explicit coverage for the no-prior-file path, an unwritable
directory, and a concurrent overwrite. Failure emits a safe diagnostic and never
claims a semantic rollback.

The concurrency policy for that file is **last writer wins, with no conflict
detection and no merge** — stated here so the concurrent-overwrite test has
something to assert. Atomic replacement guarantees a reader never sees a torn or
partial file; it does not guarantee that a concurrent writer's values survive,
and this slice does not try to make it. A second process that rewrites the file
between our read and our write loses its values to ours, exactly as today. The
test therefore asserts that the file is complete and parseable after interleaved
writes, not that both writers' values are present.

### S3.9 — reload integration and documentation

Only after the preceding foundations are reviewed clean: stage the candidate
runtime and every fallible derived projection, dispose rejected candidates
without delivery, commit through the non-fallible pointer assignments, reconcile
chrome and deliver committed custom messages afterwards, then run the full
scenario checklist and update `docs/architecture.md` and `docs/backlog.md` with
what actually landed.

## Behavioral scenario checklist

The finished slice must demonstrate, as observable behavior:

- a retained old generation's frozen projection is unchanged after a newer
  generation is published, and a straggler's write into that generation's
  sidecars reaches no live consumer;
- a stale model mutation cannot overwrite a newer generation;
- a stale thinking-level mutation cannot overwrite a newer generation;
- stale hook model controls fail closed;
- a stale active-tool mutation cannot alter the current generation;
- a tool worker that completes after its own cancellation contributes no
  *result* — no tool-result message, no result-derived session-tree entry, and
  no result-derived event. A message that handler deliberately sends through
  `send_user_message` is a separate extension effect governed by class C, and is
  accepted or dropped by that class's rules, exactly as today;
- appends and chrome writes to a closed retired or rejected sidecar are
  discarded rather than accumulating;
- a rejected reload whose candidate cleanup raises still leaves the live
  generation ungated and accepting mutations;
- a worker that outlives `run()` can mutate nothing: teardown invalidates the
  generation and closes its sidecars;
- a mutation that arrives while a publication is pending fails closed and is
  neither applied to the outgoing generation nor lost inside the incoming one;
- the activation API exposes no class A port, so an activating extension's
  behavior is unchanged by the gate;
- a mutation accepted before the gate opens is visible to the candidate and
  survives publication rather than being silently overwritten;
- candidate user and custom outboxes are not delivered when the candidate is
  rejected;
- a delivery that fails part-way through a batch resumes at the first
  undelivered message and loses none within that generation's lifetime;
- a reload delivers, in order, every message that existed when the publication
  gate opened — the cutoff and the gate being recorded in one critical section —
  stopping at the first delivery failure and diagnosing it together with the
  count left unattempted, while messages a straggler appends afterwards are
  dropped with the retired generation, as they are today, or delivered normally
  if the reload is rejected;
- an append that would exceed a queue's pending capacity is refused and
  diagnosed rather than growing the queue without bound;
- a custom message whose durable append succeeded is never appended twice, and a
  projection or render failure after it is diagnosed rather than retried,
  leaving the message recorded but unshown;
- rejected candidate listeners and chrome requests are disposed;
- a contribution registered after the staging host is sealed — the timed-out
  activation case — reaches neither the rejected candidate nor the published
  generation, while the published generation's own handlers keep sending
  messages and updating chrome normally;
- a candidate flag failure retains the complete prior generation — old flags
  paired with old commands, hooks, tools, providers, and renderers;
- a derived tool, provider, or renderer build failure retains the prior
  generation;
- a successful reload publishes commands, hooks, tools, providers, renderers,
  flags, shortcuts, and UI projections coherently;
- provider fallback after reload stays behaviorally compatible;
- a reload that rebuilds an extension-contributed provider under an unchanged
  selection leaves coding state bound to the new provider object, with history
  and usage preserved and no reference to the retired generation's provider;
- active-tool filtering stays compatible;
- lifecycle hook ordering stays compatible;
- removed or disabled extension chrome clears only after a successful commit;
- concurrent coding history and usage updates are not lost;
- settings, trust, keybindings, package resources, and the committed theme
  setting remain old on rejection and become new on success, with no observable
  point at which new commands run against old settings or resources;
- a failed post-commit theme mirror or repaint leaves the committed theme
  setting new and reports a diagnostic, without reverting the semantic
  generation;
- first-ever defaults persistence works with no prior file present;
- post-commit persistence failure reports a diagnostic while the semantic
  generation remains wholly new;
- renderer selection stays tied to the tool set advertised for the request; and
- `before_agent_start` model changes retain their established effect on the
  current turn.

## Verification and review gate

Each sub-slice runs focused tests, `git diff --check`, `just check`, and
`just docs-build`, then the mandatory independent review loop at high reasoning
until an explicit clean verdict with no relevant skipped or truncated files.
Findings are fixed repository-wide — every analogous instance, not the one
reported — before a fresh review round.

## Stop conditions

Implementation stops and reports rather than continuing when:

- the same substantive finding survives two consecutive fix attempts;
- findings oscillate between incompatible requirements;
- a slice exceeds the bounds above and cannot be split;
- two reviews find the same class of unsynchronized check-then-mutate bug, in
  which case this contract is revised before more code is written;
- the review gate is persistently unavailable;
- the change requires authority beyond this slice; or
- the contract above proves insufficient.

## Behavior preserved

This rebuild is behavior-preserving except where the scenario checklist names an
explicitly characterized change. CLI text, JSON/RPC schemas, provider wire
requests, session formats, event ordering, extension contracts, TUI behavior,
and command precedence stay as they are. `CHANGELOG.md` is not updated unless a
user-visible behavior change is intentionally introduced.

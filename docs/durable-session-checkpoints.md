# Internal Session checkpoints and unload

The private native `Session` now joins document checkpoint policies, default
deltas, and explicit cache unloading on its existing mutation line. This is
DE04b1b3b. These modules are not exported as a product API, and this change does
not enable durable execution by default. Full history, as-of reads, fork and
migration behavior remain DE27. The broader parity inventory remains open.

```python
from pipy_harness.native.durable.documents import define_doc

notes = define_doc(
    "notes",
    1,
    "session",
    lambda: {"items": []},
    checkpoint_when=lambda value, ops, info: info.deltas_since_base >= 8,
)
```

`Doc`, `DocFamily`, `define_doc` and `define_doc_family` require keyword-only
`checkpoint_when`, preserving positional `history` and `fork` arguments.
It must be callable; omission and `None` are rejected. address resolution rechecks that
ingress before a transaction memo hit. Acquisition captures the callable once.
A bound method keeps its normal Python receiver.

The callback receives the exact immutable `OwnedJson` candidate, exact immutable
normalized operations, and frozen `CheckpointInfo(deltas_since_base)`. The count
belongs to the prior committed incarnation. Returning exact `True` writes a
base and resets the count to zero; an explicit exact `False` writes a
delta and increments the count. An ordinary checkpoint publication still carries
the original operations. A root replacement operation is a delta under the
explicit false policy.

Every draft is prepared and revoked before the first policy runs, including
nested and detached views and suspended iterators. Owner checks, task replacement
checks, every terminal-document scan page, publication ownership and submission
resolution also finish first. Eligible policies run once in document plan order.
A second policy failure aborts the whole candidate batch, including tables,
submissions, documents and retirements. No candidate value or count is adopted.
A healthy Session can accept a later transaction.

Creation persists the actual final created object as a base and publishes empty
operations. It does not call the policy. Loaded empty operations retain the old
committed object and count, with no document write or publication. Structural
operations that restore an equal value remain nonempty and are persisted. A
dirty document being retired still runs its policy and writes content before
retirement; retirement publishes no value or version and empty operations.
Clean retire-only and absent-retirement paths do not call a policy.

Policy results must have `type(result) is bool`. `None`, integers, Futures,
custom awaitables and native coroutines are invalid. A known native coroutine
that has never started is closed without running it. Caller Futures and arbitrary
`close`, truthiness, equality or attribute hooks are not used. Callbacks, result
disposal and caller finalizers run outside the owner, cache and backend locks.

The owner's persisted-value decision is binding: a loaded candidate is the exact
replay of its prepared operations over the committed value. That same owned
object reaches the policy, cache and ordinary publication. There is no global
signed-zero or member-order normalization and no rejection of an otherwise valid
transaction because its working tree differs from replay. Native Memory, SQLite
and reopen reads preserve the same binary64 signs and ordered UTF-16 members.
See [default deltas](durable-session-deltas.md) and the
[tracker](durable-draft-tracker.md) for operation ownership and supported methods.

## Callback authority and health

An asynchronous transaction has its caller and settlement task identities, plus
one temporary driving-task identity. The driver installs that identity around
each synchronous coroutine `send` or `throw`, including eager and resumed
preparation, and restores the previous identity in `finally`. It never retains
the temporary identity across an await, executor submission or backend call.
Nested eager advances restore their enclosing identity. Independent tasks do not
inherit authority.

The identity fields belong to the ticket's event-loop thread. Foreign threads
are excluded before those fields are read; holder publication and retirement
continue to use the existing owner condition. Comparisons use identity only.
There is no second line, second lock, growing task registry or inherited context
variable. An exceptional task-factory handoff aborts views and drains pending
backend work before leaving the line. Only that failure cleanup bypasses the
failing task factory to create its owned drain.

Same-holder commits, line jobs, snapshot misses, unloads and closed-waits refuse
with `ReentrantSessionCall`. A committed snapshot hit and `request_close` remain
allowed. Existing absent-document initializers also run inside this driving
scope, including after cold I/O. A separately scheduled task queues normally.
After a policy returns a valid bool, Session health is checked again: requesting
close permits the already accepted transaction to finish, while failing the
owner—or catching a failed guarded read—prevents further policies and admission.
The first owner failure remains authoritative. A policy's own thrown exception,
including a process-control exception, propagates as a pre-admission application
failure; it does not by itself poison a healthy backend.

## Explicit unloading and acknowledgement

`Session.unload_documents()` and `await Session.aunload_documents()` clear only
committed document residency, through the existing sync and async line helpers.
Even an empty unload takes its turn. Unload performs no Storage call, publication,
retirement, initialization or ID mint. It affects every scope and family key.
Retained immutable snapshots remain valid; the next actual snapshot or acquisition
cold-loads stored value, version and delta count.

An unload queued behind a commit runs after acknowledgement, adoption and
publication. Cancellation detaches a queued async caller. Accepted unloads drain
before close; new calls after close or failure receive the existing lifecycle
errors. A foreign synchronous thread can wait for the line. A synchronous call
on an event-loop thread refuses rather than blocking that loop. Same-holder
callbacks refuse, while independently scheduled async tasks queue normally.

Value and count adoption remains atomic and acknowledgement-dependent. An actual
SQLite after-COMMIT acknowledgement loss, or failure during adoption, can leave a
durable write without a publication or cache update. The Session fails and closes;
a fresh Session reopening SQLite observes the durable value and count. These
outcomes are not reported as a database rollback. A before-COMMIT failure retains
the prior durable state.

## Reference evidence and differences

`scripts/durable_session_checkpoints_comparison.py` runs actual pinned Pi
`SessionImpl` with Memory, SQLite and reopen, and actual native Session with all
three backends. Its closed phase is `session-checkpoints-j-v1`. The expected
fixture stores LZMA-compressed bytes from actual Pi execution, with a bounded
decompressor and an exact raw size/SHA-256 check. The earlier historical
full-base document fixture and the delivered default-delta fixture remain intact.

The capture records 52 recipes per backend, including both zero directions,
simple and nested access, genuinely untouched nested containers, unrelated edits,
empty operations, counts, root fallback, retirement, all terminal scan pages and
mixed rollback. Instrumentation never recursively reads a live Pi draft. The
working value is explicitly a plain derived mirror. Callback arguments are actual
post-prepare observations; publication, warm, stored and cold values are recorded
separately. `stored_replay` is a computation over the actual pre-commit stored
value and actual normalized operations. `owner_replay` labels its source as
either the plain final creation mirror or pinned Chord replay; neither is an
internal Session observation.

Raw PiMemory prepared/warm values can differ from replay in signed-zero cases.
PiSQLite additionally loses negative zero in its JSON storage. The comparison
allows only the named zero member in the declared recipes to differ between the
two zero signs; unrelated values and operation bytes still discriminate. Pi
callback arguments are mutable, Promise-returning policies use JavaScript
truthiness, unawaited nested calls queue, and unload can run after close. These
are recorded runtime boundaries. Native requires immutable arguments, exact bool,
same-holder refusal and existing closed-owner admission checks.

Native backend consistency is checked before any comparison projection, retaining
exact signs and member order. Only Pi-versus-native comparison uses the existing
JavaScript array-index own-key ordering profile. The command returns 0 for the
declared comparison, 1 for a schema-valid semantic mismatch and 2 for invalid
evidence. These captures complement the deterministic native lifecycle, authority,
failure and SQLite tests; they do not claim product integration or full parity.

Release1.4 requires the callback declaration. The [release bridge](specs/2026-10-29-durable-release-1.4-bridge.md) records the v2 case migration and historical-subset unit oracle; current release runtime evidence remains separate.

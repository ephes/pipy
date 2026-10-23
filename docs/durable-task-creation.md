# Typed durable task creation (DE05a)

The private durable Session creates tasks from immutable executable definitions.
This supplies definitions and actual creation; scheduling remains DE05b.

```python
from pipy_harness.native.durable.tasks import define_task

async def start(task, runtime, context):
    # A future execution owner supplies runtime/context and dispatches phases.
    await runtime.commit(lambda tx, current: {
        "status": "terminal", "outcome": {"status": "completed", "result": None},
    }, context)

async def abort(task, runtime, context):
    await runtime.commit(lambda tx, current: {
        "status": "terminal", "outcome": {"status": "aborted"},
    }, context)

work = define_task("work", 1, lambda value: {"phase": "start", "value": value},
                   {"start": start}, abort)
# In a Session.commit callback:
# id = tx.create_task(work, {"items": []},
#                     ownership={"kind": "conversation"}, conversation_id=1)
# AsyncTx returns an awaitable TxOp for the same operation.
```

`Task` is an identity token: equality and hashing use its identity without
comparing or hashing caller callbacks. It captures an exact string name, finite
numeric version, synchronous
initializer, copied immutable phase map and abort callable. Versions retain the
existing finite JSON numeric domain, including zero, negative and fractional
values and negative zero. Definition keys use persisted UTF-16 identity rules;
callables retain their Python receiver and identity. Creation snapshots the
validated definition before suspension. No legacy raw creation alias remains.

Creation owns input and trace before its first storage suspension. It checks the
owner and conversation, invokes the captured initializer on detached input, owns
both final input and checkpoint, then mints and stages one pending task. Mutations
of that detached input become its stored input, as with pinned Pi's initializer;
the caller's original object remains unchanged. Escaped initializer objects cannot
change the staged record. This early exact-JSON ownership is a native admission
boundary: Pi copies after initialization and can mutate its original input.
Invalid input therefore refuses before native user code or storage reads; invalid
initializer output refuses before mint. A failed initializer or JSON copy aborts
the transaction's tables and documents, with no backend commit or publication.
The existing final candidate owner checks still run during assembly.

An initializer must return synchronous finite JSON. Known unstarted native
coroutines are closed without execution; Futures and foreign awaitables refuse
without arbitrary cleanup, truthiness or equality hooks. Callback execution and
reference disposal occur outside lifecycle/cache/backend locks. The current
Session driving authority applies after cold I/O: same-holder line reentry
refuses, while already accepted work may finish after a close request. A failed
owner refuses further task minting.

Creation does not dispatch handlers, require a registry, resolve definition
compatibility or check phase-map membership. Those checks belong at the actual
reservation boundary. A stored checkpoint may be any finite JSON value at this
creation layer; an execution owner must resolve whether it can run it. The
phase handler type remains an awaitable callback until DE05b supplies the actual
runtime/context types. No runtime stub or invocation fence is exposed here.

## Verification and retained execution boundary

`tests/durable/test_task_creation.py` exercises actual sync/async Session creation
over Memory, SQLite and reopen, copied definitions/input, version fidelity,
initializer mutation/ordering, ownership refusal, rollback, coroutine cleanup,
foreign-awaitable refusal and async cold-read callback authority. Existing
transaction/document/SQLite tests and comparison adapters use real task
definitions while retaining predecessor observations and historical fixtures.

`scripts/durable_task_creation_comparison.py` executes the immutable pinned Pi
`defineTask`, `Transaction.createTask` and `SessionImpl` over Memory, and actual
native creation over Memory/SQLite/reopen. Its twelve cases compare numeric
versions, input/checkpoint, ownership, initializer order and errors, persisted
writes and publications. Original caller aliasing is outside this paired trace
and is asserted directly by native tests; no persisted field is projected.
The closed `task-creation-v1` manifest binds source and artifact hashes. Eight
schema-valid mutants distinguish lost kind/version/input/checkpoint/ownership,
ordering, rollback and publication; actual compare CLI exits are equal0,
mismatch1 and invalid2. The driver runs only from a freshly attested parent
reference snapshot and records loaded-module provenance; it makes no independent
bootstrap claim.

DE05 remains cumulative and IN_PROGRESS. DE05b retains scoped runtime
commits/memos, sequential reservation, invocation end, no dispatch on open,
recovery and real SQLite process-death cuts, keyed intent/effect/receipt
uncertainty, terminal cleanup and the lifecycle-owned close/join/latest-checkpoint
rewrite before backend release. Fatal storage errors and acknowledgement loss
seal the same owner. No mocked close join or pre-acknowledgement dispatch can
satisfy that successor. DE14/15/16/23 and other later requirements keep their
original ownership. All43 P rows remain OPEN. Document persisted-value/count
contracts and product defaults are unchanged.

## Evidence artifact ownership

The oracle separates producer artifacts, control inputs and subprocess receipts
before execution. At the output root, `input.json` is the immutable reference
recipe; Node alone writes `observed.json`, `resolutions.json` and
`raw-errors.json`; the native producer alone writes `native.json`; and the
coordinator writes `result.json` only after validation. No control may replace
any of those files. All mutated and malformed inputs live in `mutants/`,
including `mutants/input.json` and `mutants/invalid.json`. The process recorder
alone owns `processes/<label>.json`, `.stdout` and `.stderr`. Environment and
SQLite files have separate private directories.

Attestation checks the retained recipe against the actual recipe, every retained
control against its exact expected mutation, each actual argv against that
input's path, and all recorded artifact and stream hashes. The real CLI tests
include the `input` mutant and assert the recipe and malformed input survive
recording. A real pinned execute-to-attest run must pass before the expensive
final cumulative chains and full checks.

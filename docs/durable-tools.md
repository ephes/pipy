# Durable tool execution (DE08)

[DE09 completed observation](durable-completed-projection.md) reads the actual
saved slot prefix and committed closed-round order, retaining native tool output
payloads without live registrations or effects. A nonfinal terminate result can
be a completed child fact while its waiting parent and later unstarted children
remain live; no closed round or terminal parent is invented. DE10 must explain
the retained-control hold. Full termination/graph policy stays with DE25/30 and
DE15/28; public product activation stays DE11/12.

## Pre-code contract

DE08 extends delivered DE07 `9f6ef9cf` on the existing native Session authority.
It is private opt-in execution; product/CLI activation remains DE11/12. The
original DE08 acceptance and all43 P rows remain whole. This contract precedes
implementation and does not itself claim executed acceptance.

Default rounds retain atomic admission of every offered real `pi.tool` version2
child. Execution is sequential in the committed round's slot order, including
unavailable calls answered without a child. Reservation, phase start and runtime
writes verify the same direct model-child/owner/conversation/assistant/call
relation. Being owned is distinct from owning live descendants. The narrow
exception never permits an arbitrary owned task or terminal live-child escape.

Only the earliest unfinished compatible child can execute. A blocked, aborted,
faulted or missing earlier receipt holds later pending children and the parent.
Unrelated conversations remain independent. The matching parent wakes from its
captured allSettled wait on the Session line after actual children settle,
including reopen and empty waits. Observers notify; they do not write the graph.
Open reconciles without dispatch; explicit resume enables acknowledged work.
Graph entry dependencies are captured on the line before its first table write;
post-write checks use those owned values plus exact staged replacements. The Tx
read-after-write restriction remains unchanged.

The model call comes from the committed assistant and saved advertised request.
It must also be currently callable through the admitted fixed registration.
Trusted immutable implementation/schema/replay descriptors default unsafe;
read-only/idempotent annotations remain unverified hints. Registration metadata
and callback code never enter the model declaration codec. No safety inference
comes from names, unchanged files, missing effect markers or output text.

Preparation uses the existing ToolExecutor parse/prepare/schema boundary. Final
arguments are detached and persisted with stable child/call idempotency identity
before any effect. Only the acknowledged fresh call continuation can initially
run an unsafe intent. Recovery from execute never prepares again: stored-safe
AND current-safe plus compatible implementation/schema/policy are required.
The explicit safe fixture must actually deduplicate applications by that stable
key, while counting all invocation attempts separately. No universal exactly-once
claim follows.

Unsafe or incompatible execute recovery becomes a validated pending blocked
checkpoint, not a new task status, terminal success, timer or self-wait. Resume
does not dispatch it. Possible effects remain inspectable even when no effect
was observed. Explicit resolution and interrupted-error continuation remain
DE10/13; nested/general extension and MCP contracts remain DE24.

An actual canonical outcome commits as a task-scoped receipt before result-entry
publication. Receipt reuse requires neither current tool resources nor another
prepare/resolve/invoke. Entry, real accounting where present and child terminal
outcome settle atomically with matching parent reconciliation. A committed
receipt survives failed publication; an uncommitted in-memory result does not.
Before-commit failure differs from commit-before-ack loss; failed owners seal and
recovery reads persisted truth.

The private versioned nativeToolOutput boundary retains the canonical outcome:
rich ordered blocks/images, native/provider correlation, details, missing versus
null structured content, flags, terminate and equality-excluded duration. Unknown
or incompatible data refuses with fixed payload-free scheduler diagnostics.
Actual tool error content belongs to the full-content product result, never to
metadata workflow fields. No auth/credential material is needed for tool fixtures.

The synchronous ToolExecutor branch (`wait_for_interrupt=None`, explicit saved
arguments) runs through DE07's owned_call. A host watcher propagates TaskSignal
only through a thread-safe cancellation event. The actual invoke and finalizers
stay phase-owned until they exit; noncooperation holds close and the SQLite path
lock. API waiter cancellation cannot detach accepted cleanup. Legacy UI executor
semantics remain unchanged. No foreign thread inspects asyncio Task/Future state.

Parent continuation consumes committed slots/results in assistant call order,
not physical result-entry order. It preserves a closed-round order index for
later context preparation, advances a new request/attempt/seed boundary and uses
per-request material memos. Recovered request/receipt phases retain their saved
identity/pricing/cache semantics. A blocked round never issues that next request.
Existing request-snapshot retention cost remains explicit DE27/28 debt; this
slice does not redesign historical request storage.

General graph/failFast/completing/cycles/background cleanup stays DE15; full
parallel/mode/addTools/handoff/termination orchestration stays DE25/30. Current
native control values cannot be silently dropped; an unsupported control blocks
the supported continuation. Full product content is distinct from the optional
metadata-first workflow archive described in session-storage.md.

## Private interface

`ToolRegistration(port, implementation, revision, replay="unsafe")` captures a
trusted fixed implementation/schema identity and its explicit replay contract.
`define_tool_task(registrations, workspace_root=absolute_path)` captures the
registry map and callable identities, returning the actual `pi.tool` version2
definition. Register that definition beside `define_generation(...)` on the same
private `TaskScheduler`. The original `generation_tasks.TOOL_TASK` remains an
admission-only token. `builtin_registrations()` marks all current native tools
unsafe; no MCP or annotation-derived contract is invented.

The admitted `DurableToolContext.call_key` is a stable task memo winner. A tool
claiming replay-safe must really deduplicate its applications by that key and
final arguments; UUID-shaped request IDs alone provide no replay contract. The
registry is a captured snapshot, not DE21/23 live reload. Current callable and
saved offered declaration must agree before preparation/effect. Hidden/codemode,
loadout/reachable-tool callbacks and unsupported sequential policies block before
calling preparation or execution. Full extension controls remain DE24/25/30.

An intent stores the real model-call input, detached final args, definition
projection, workspace binding and durable call key. `execute(arguments=...)`
revalidates final args through the existing ToolExecutor without another prepare.
Version1 `nativeToolOutput` retains a full canonical ToolExecutionOutcome and
completion time in the task receipt and committed result entry. A malformed
argument result retains its actual malformed flag without inventing a runnable
intent. Missing and explicit null structured content, rich blocks/details,
provider/native IDs and equality-excluded duration remain distinct.

## Logical round and graph boundary

Pinned Pi `generation.ts:finishToolRound` finishes the tools generation task and
creates a successor generation task. This bounded native adapter advances the
same parent through a new prepare/request/attempt/turn boundary. The paired
profile compares the logical closed round and replay/effect prefix, not task
shape equality. Exact raw task graphs remain retained. DE15 and DE28 must verify
exposed task ownership, lifecycle and graph semantics against the actual pinned
reference before whole-feature/P acceptance; this difference is not silently
normalized into a whole-graph parity claim.

The saved order index references actual result entries for every closed round,
including unavailable results that physically precede earlier calls' results.
Subsequent requests preserve all closed-round orders. Each new prepare admits a
new seed/request/attempt and distinct material memo. Recovery of an already
admitted request or receipt never re-prepares it. The existing quadratic request
snapshot format and its versioned migration/storage budget remain DE27/28 debt.

## Executable acceptance

| Original DE08 boundary | Actual executable evidence |
| --- | --- |
| Pending once; actual parent/owned-child/parent loop | Memory/SQLite full loop, atomic all-child admission then saved-order dispatch; three provider turns and unavailable-only/empty waits |
| Safe explicit fixture retry and four intersections | Actual SQLite process-death at effect; independent calls/applications tables deduplicate by persisted key and final args; current/stored policies both required |
| Unsafe intent/effect uncertainty even without observed effect | Real pre-invoke and post-effect cuts; validated pending block prevents every later call/provider request; annotation hints do not authorize replay |
| Receipt survives result-commit failure | Before/after receipt and result COMMIT, final-child before/ack/adoption faults; actual backend reads/reopen consume receipt without registry/preparer/invoker |
| Definition/policy/schema/input/owner drift fails closed | Revision/schema/missing/workspace drift, malformed/newer/wrong-key receipts, late ownership/input/abort gates and separate healthy conversation |
| Rich fidelity and unsupported controls | Canonical rich result/receipt, raw arguments, missing/null/object structured content, malformed flags; terminate holds later work rather than silently continuing |
| Real lifecycle ownership | Actual ToolExecutor invoke/finalizer, cooperative/noncooperative close and cancelled API waiter; fresh-process opener refused during held noncooperative invoke and both finalizers, admitted after exit/join |
| Actual pinned comparison | Harness tool intents/transactions/waits/results/SQLite reopen with explicit fake tool/provider seams; raw unsafe continuation differs honestly; finite declared common profile and actual CLI0/1/2 controls |

Tests live in `tests/durable/test_tool_{execution,boundaries,sqlite,faults,lifecycle,comparison}.py`.
Raw receipts remain outside git. A selected focused run is diagnostic until the
exact reviewed source passes supported-Python/Linux, full local check, strict
docs and both fresh complete reference chains. The acceptance ledger owns the
current gate disposition. Product/CLI/public completed-message projection remains
DE09–12; all43 P rows retain their original complete OPEN requirements.

Each phase's postexecution attester runs in its own fresh Python process.
Explicit pure dependency roots reproduce the modules actually loaded by the
phase, including lazy history/graph helpers, without reexecuting tool/provider
effects. Every loaded product path must belong to the closed source inventory
and match its actual module origin/hash. DE07's rejected shared-process module
contamination and DE08's rejected missing lazy-history root were distinct proof
failures; both diagnostics remain retained. Neither permits narrowing the
recorded module map or importing multiple attesters into one shared interpreter.

Terminate is retained without implementing the full termination policy. A
non-final terminated receipt holds the parent in waiting and later children in
unstarted call checkpoints; the committed receipt is the durable hold reason.
A terminated final/only call closes that round's children and leaves the
parent in validated pending/blocked instead. Neither state is completed
termination support. DE10 inspection must derive and display this retained
control hold from the round/receipt, rather than present unexplained active work.
Full all-terminate/any-handoff behavior remains DE25/30. The unused tool
checkpoint reason unsupported_control is not an admitted emitted state.

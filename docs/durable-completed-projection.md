# Committed completed projection (DE09)

## Authority and lifetime contract

DE09 builds on delivered DE08 `8571f88b`. The private Python observer reads the
existing Session authority. It neither activates the product factory/CLI nor
executes, prepares, resolves, reprices or accumulates anything while observing.

Acquisition captures one committed conversation baseline and registers later
notifications on the same Session line. The entire synchronous `read_on_line`
call runs off-host through `owned_call`; its actual worker owns the line ticket
until its read/registration body exits. Guarded reads and SQLite's existing
mutex retain backend/path ownership through cancellation and close.
The calling loop first applies Session's existing reentry guard, before any
await or drain-lock acquisition. Moving the read to a worker must not hide an
async caller that already holds that same Session line.
Acquisition has one strongly retained, loop-owned shielded `owned_call` task.
Canceling its API waiter latches abandonment immediately; subscriptions are
detached outside guards and a late worker offer unregisters itself. Cancellation
or teardown may finish every asyncio wrapper while the synchronous worker still
holds its real line ticket. Session close and SQLite path release wait for that
ticket, including when no backend call is currently underway.

Completed snapshots preserve canonical message/outcome values, real entry commit
sequences and store-scoped entity IDs. Open tool rounds expose only their actual
completed slot prefix; closed rounds use committed `pi.tool.round-order`, never
physical result-entry order. Whole native terminal outcomes and logical closed
rounds are distinct. Waiting/pending terminate holds are not terminal success.

Each attachment has one capture, one in-flight consumer and one coalesced pending
replacement. Synchronous notifications only filter owned changes and signal the
consumer loop. No callback, I/O, loop wake, Future completion or foreign-reference
disposal occurs under the lifecycle condition or the observer's signal lock.
Publisher threads never inspect foreign asyncio Tasks/Futures.

The caller initializes from `snapshot`, then starts one asynchronous listener.
Drain queues a real capture barrier and waits for successful delivery of that
revision or a newer replacement. It is not runtime idle or a durable consumer
acknowledgement. Stop prevents later callback admission and drops pending work;
an already admitted consumer callback remains caller-owned and is not joined.
Session close still retains actual accepted reads and runtime finalizers.

Reconnect acquires a fresh snapshot. Facts may be seen again; stable entry/task/
assistant IDs permit dedupe within the same store. There is no persistent watch
cursor or exactly-once callback promise. Listener failure affects only that
attachment. Internal Session failure preserves its current raw-publication
listener semantics; reopening recovers committed facts, not queued callbacks.

All original DE09 acceptance remains: independent commit-before-callback reads,
real process death after COMMIT/before delivery, ambiguous callback acknowledgement,
counted effects, listener isolation, coalescing and truthful drain/stop lifetime.
All 43 whole P rows remain OPEN. Full watch/stream/context/graph behavior retains
DE20/26/27/28; unsafe continuation DE13; the opt-in [Python factory](durable-product-session.md) in DE11 and CLI in DE12. The native
same-parent/Pi successor-task distinction remains DE15/28, and quadratic saved
request-history retention remains DE27/28 debt.

## Private API

`await observe_completed(session, conversation_id)` returns a `CompletedWatch`.
The calling loop owns its `snapshot`, `start(listener)`, `drain()` and
`wait_closed()` operations. `stop()` can seal admission from another thread.
The API is a private native foundation; DE11/12 own product factory and CLI/TUI
activation. Existing state-first coding-session and AgentLoop paths are not
wrapped or replaced here.

Read `snapshot` immediately after attachment to initialize a consumer, then
call `start` once with an asynchronous listener. Start does not invoke that
listener inline or replay the acquisition value. Later dirty captures replace
one pending value. `snapshot` exposes the latest captured immutable value;
successful `drain()` returns the value actually acknowledged by that listener.
Drain queues its own capture barrier even when nothing changed. An older read
already in flight cannot acknowledge a newer drain. Canceling a drain waiter
detaches that waiter while capture/delivery continues. Calling drain directly
from its own callback, or either API from the same Session's held async line,
refuses promptly.

Callers must stop an acquired watch before destroying its loop; DE11/12
activation must own that cleanup. If a caller abandons it instead, the next
publication checks the loop's public closed flag before relevance filtering and
detaches even for another conversation. This uses no foreign Task/Future access
and does not advance the chosen conversation's sequence. A completely idle
Session can retain the abandoned subscription until its next publication or
Session close; this is a lazy fallback, not an idle-loop termination guarantee.
DE28 retains stronger abandoned-observer lifecycle semantics if needed for full
watch adoption. No timer, GC hook or second lifecycle owner is introduced.

Each message carries its actual entry ID and persisted `EntryLookup.commit_seq`.
`publication_seq` is the most recent relevant public commit notification covered
by the held-line capture; the acquisition baseline has no invented global
watermark. `revision` is local to this attachment and is neither a public commit
sequence nor a durable replay cursor. Reconnect starts a new local revision
sequence from a new snapshot. Consumers can use conversation plus entry/task or
assistant IDs to deduplicate within the same store; IDs do not identify entities
across unrelated stores.

Snapshots contain immutable canonical completed messages, retained provider
outcome payloads, native tool outputs including completion time/duration, logical
closed rounds, actual terminal task outcomes and the committed usage document.
There is no second usage accumulator. Calling `CompletedMessage.decode()` or
thawing an owned payload yields fresh descendants, retaining raw argument text,
correlation/signatures/images, absent versus null structured output, numeric
type/signed zero and optional usage presence. Outward completion DTOs exclude
saved requests, provider settings and private material pointers. Observation
neither reconstructs credentials nor requires current tool registrations.

Task receipt documents retire at terminal settlement. The projection reads
retained assistant/result entries and terminal records instead. An actual
terminal outcome can be failed, aborted, orphaned or faulted; terminal does not
mean completed success. A `ClosedRound` exists only when committed
`pi.tool.round-order` says so. The same native generation parent can close a
round and prepare a fresh request while remaining nonterminal. A nonfinal
terminate receipt holds the waiting parent and later unstarted children; a
final/only terminate leaves the generation pending blocked. Neither creates a
closed round or terminal parent. DE10 must derive the hold reason from retained
control data when inspecting it.
Terminate controls execution eligibility, not visibility of already committed
facts: a following unavailable-call error remains visible until an actual
unfinished earlier slot ends the completed prefix.

Unknown completion formats, newer document versions, unsupported parent/fork/
head/edit context, foreign links and malformed correlations end only the
attachment with fixed `capture_error`. A real guarded storage failure keeps
Session's existing failure/close semantics. `listener_error`, `stopped`,
`session_closed` and `loop_closed` are the other bounded end tags. Raw backend,
consumer and material exception text is not carried by these tags. This is not
the full P28 watch-end/event-stream API.

## Publication cost debt (DE27/28/41)

The pending count is bounded; snapshot bytes and synchronous filter work are
not constant. Each active watch currently thaws complete immutable table writes
to inspect their identity, including task checkpoints on other conversations.
For W watches and H publication payload bytes this costs O(W×H) copying on the
post-adoption mutation-line publication path, outside both guards. Rich saved
request history makes H grow with the existing DE27/28 retention debt. User
callbacks and guarded snapshot reads still run off that path.

Before enabling a default durable product path or accepting the full feature,
DE28/DE41 must quantify and enforce allocation/latency budgets against declared
history-byte and watcher-count workloads on supported platforms. If those
budgets require a cheap immutable identity projection, refactor it at the
existing publication DTO authority; test foreign/relevant filtering, sequence
coverage, full canonical fidelity and shared-field synchronization. DE27/28
must retain the original request-history format/migration and storage-byte
budget obligations. This private unactivated foundation claims no completed
performance parity or budget compliance.

## Acceptance and reference boundary

Focused Memory and real SQLite tests exercise automatic commit-to-callback
delivery before explicit drain, actual two-tool rounds and the following answer,
independent entry/task readback, saved slot prefix ordering, rich fidelity and
consumer isolation. They include actual process death after COMMIT/before the
callback and after a consumer's keyed write/before callback acknowledgement.
Fresh reopen plus two drains preserve independently counted provider/tool
attempts and applications. The consumer ledger demonstrates caller-owned
deduplication; it is not projection state or a universal exactly-once guarantee.
Before-commit, lost-ack and adoption faults expose no unacknowledged round and
reuse old receipts; an explicitly resumed following provider turn is a new
legitimate continuation.

Actual held reads, cancellation after registration and complete consumer-loop
teardown prove independent-process SQLite open refusal until real ticket exit.
Stopped or failed watches drop pending deliveries and do not join an admitted
arbitrary consumer callback. Callback lifetime is distinct from Session-owned
read, provider, tool and finalizer lifetime.

The source-closed `completed-projection-v1.manifest.json` is parented by the
existing tool phase. `scripts/durable_completed_comparison.py` executes pinned
Pi `Harness` and `Conversation.watch` with only a fixed provider stream and
synthetic tool boundary. Release 1.5 removed `watchEvents()`, so automatic
delivery and reconnect are observed through the conversation watch alone. The common projection compares
logical completed roles/results, actual usage/ledger totals, automatic delivery,
independent commit readback, listener isolation, same-store reconnect identity,
stop observation and unchanged effect counts. Pi's raw views/task graph
and exact native canonical facts remain separately retained. It does not claim
full structural view ordering, Pi's 100-frame buffer, event-stream/partial parity
or provider/finalizer lifetime parity. Pi successor-generation tasks versus the
native same-parent fresh request remain DE15/28 obligations.

Every semantic mutant executes the real comparison CLI with positive exit0 and
negative exit1; malformed bodies separately require exit2. Raw schema-valid
faulted tasks and doubled committed aggregate usage without changing settlement
keys must fail the actual completed/usage predicates. Postexecution attestation
runs in a separate fresh Python process and binds source, modules, artifacts,
inputs, actual process receipts and both raw/common numeric profiles. Final
independent review, full/platform/docs checks and two fresh cumulative18-phase
chains still gate delivery. Full Durable feature completion is later work.

## Operator outcome privacy (DE10)

[Operator retry](durable-inspection.md) preserves old uncertainty only in a
private conversation audit. CompletedSnapshot continues projecting actual
terminal outcomes: the old tool outcome is exactly
`{"status":"aborted","reason":"operator_retry"}`, with no result, replacement
linkage, checkpoint, intent, prepared arguments or material authority. Numeric
replacement linkage belongs only to the explicit operation response/private
winner. Real saved-frame/material negatives test actual DTO values, not repr.
Pure operator metadata uses a separate allowlist and never attaches a watch.


The opt-in [DE11 async product session](durable-product-session.md) now composes
this existing native authority. It admits one input atomically, executes only
on explicit resume, and closes through actual Session ownership. Python
activation does not activate the CLI or change legacy defaults. Full private
target binding still occurs at first prepare; CLI/live and pre-prepare P16
acceptance remain DE12. Broader parity obligations remain OPEN.

## Diagnostic and failed tool facts

DE13b permits the strictly validated ordinary `tool_error` failed child as a
result-bearing slot. Every pure reader uses the shared ownership/cause/arena
predicate. Its internal entry carries the actual saved receipt2; completed
messages expose only the nested full fact and deterministic model projection,
never private receipt intent or prepared/workspace/key fields. Existing normal
fact1 entries keep their historical format and do not acquire a receipt-carrier
requirement. C1 additionally permits the exact `interrupted` fact2/cause with
all three required recovery keys and the matching raw FAILED error. The public
message exposes retained output/details/diagnostics and the fixed uncertainty
notice; its private receipt intent/key/workspace remain excluded. Generic failed,
foreign or malformed children do not become continuable. Missing counter keys,
wrong controls and wrong terminal causes refuse through the same shared reader.
See the [C1 tool contract](durable-tools.md#interrupted-recovered-execute-continuation-de13c1).


## Explicit historical decision acknowledgements

An adjudication acknowledgement is not a CompletedMessage or a tool result. The
separate private audit never enters projection. Normal settlement keeps the
existing fact1 projection or single actual receipt2 carrier; post-decision
fault/abort replay acknowledges only the decision, without inventing an entry.
See [historical adjudication](durable-adjudication.md).

## Committed event projection (S6 skeleton)

`CommittedEventProjection` (`native/durable/event_projection.py`) turns these
snapshots into the agent loop's own `AgentEvent` vocabulary for an
`AgentEventSink`. This is the mapping of the
[loop integration design](durable-loop-integration-design.md), section 4.3,
before DE26:

| Committed fact (key) | Events |
| --- | --- |
| input entry (`entry:<id>`) | `AgentRunStarted`, `TurnStarted(0)`, `MessageStarted`/`MessageCompleted` of the input |
| assistant entry (`entry:<id>`) | `TurnStarted(n)` after a closed round, `MessageStarted(n)` with the loop's empty assistant, `MessageCompleted(n)`; without calls also `TurnCompleted(n)`, failed for Pi's error stop; a provider-cancelled (aborted) assistant emits `RunCancelled` with the adapter's reason before its message and a cancelled turn, and the run ends cancelled |
| tool result entry (`entry:<id>`) | `ToolCallStarted(n, call)`, `ToolCallCompleted(n, result)` with the committed execution time |
| closed round (`round:<assistant id>`) | `TurnCompleted(n, succeeded, assistant, results)` |
| generation terminal outcome (`outcome:<task id>`) | `AgentRunCompleted` and `AgentRunSettled`; an aborted run first emits `RunCancelled`, a failed one carries the durable cause as its failure (`durable_<status>`) |

A fact's events are emitted together and once: the projection skips keys it
has delivered, so a redelivered snapshot or a reconnect with the delivered
keys emits nothing. The keys are the caller's cursor (`delivered`,
`on_delivered`); like DE09's consumer ledger, a crash inside one fact's
delivery delivers that fact again. Facts follow the snapshot's committed
order, which is commit order except that a tool round follows its committed
round order. Each `ProjectedEvent` carries its fact's key and commit sequence:
the entry's own, a closed round's last committed result, and for a run outcome,
which has no entry, the publication that made it visible (so only a lower
bound, tied to the snapshot that showed it). The sequence is not an ordering
key, because a round's results follow the round order. A generation that ended
before committing an assistant (for example at the restart limit in its
prepare phase) belongs to the last input before it (entries and tasks share
one global id sequence, and a generation is created after its input) and
ends that run, after the loop's failed `TurnCompleted(0)` for a preparation
failure. When an earlier run has a failed, faulted or aborted generation, any
later run's generation, with or without an assistant, may instead be an
operator retry of it (a retry is allowed after a later input, and the
snapshot does not record what it retries), so it is refused instead of being
projected into a run it may not belong to; so is an assistant of a generation
created before its run's input. A refusal raises before anything is emitted
and blocks the whole conversation's projection, earlier completed runs
included; even a cancelled run 1 followed by a second message triggers it.
Multi-input conversations are not reachable through the product yet
(`DurableProductSession` admits one input): DE18/DE19's run records must
replace this refusal before multi-input product wiring (DE42/DE43). The run's usage is the loop's: the raw provider samples of the
committed provider outcomes, and the cost each assistant was priced at. An
operator retry's new generation in a run whose generation ended, with or
without an assistant and whichever of the two committed one, is refused with
an error, not projected, until DE18/DE19's run records; so is a generation
older than every input.

The skeleton has no product wiring: it is not used by `DurableProductSession`,
the CLI or the TUI. Queued-input, retry and provider-failure events wait for
the run records of DE18/DE19, a terminate hold or blocked generation emits no
run end yet (DE25), and partials and tool progress are DE26.
`tests/durable/test_event_projection_crash.py` kills a real SQLite run after
the generation's settle commit, after the first tool result's commit, and
after the run settled; the reopened process emits each remaining event once
and the two processes together emit the loop's events for the run in order.

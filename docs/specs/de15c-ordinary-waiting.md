# DE15c: ordinary allSettled waiting

## Historical source checkpoint — 2026-10-30

The initial private DE15b tree473fff source checkpoint was not delivered Main.
The adopted design had received an independent CLEAN planning review; authored
Core and Broad tests had not yet run. That dated checkpoint made no runtime,
CODE, paired-reference or final acceptance claim.

## Source-qualified development evidence — 2026-10-30

Native implementation tree `7ec9ca0c10bc16c303080d8ec6f5782f5ac00568`
passed Primary206 and retained223 (429 distinct tests), plus the independent
Broad18 suite: 429+18 distinct tests, with no failures, errors or skips. Whole
mypy checked 1143 source files; Ruff and format checks passed with 1275 files
unchanged. Current configuration, recipe, coverage and strict documentation
checks passed. These results belong to that immutable source epoch; the current
documentation sync does not rebind them to a later source tree. Earlier failed
and successful development epochs remain separate evidence.

Independent CODE R1 on private tree3950 (Opus5.5 MEDIUM) reported ISSUES with
0 Critical, 0 Warning and 1 Suggestion: stale Broad docstring/comment wording.
That wording is fixed with executable AST unchanged. Root accepted the result
as terminal advisory, not CODE CLEAN; no second review or runtime replay is
claimed. These reviews and tests establish the bounded native consumer, not full
P09, generic failFast, whole DE16 or any of the 43 parent criteria.

The private integration on delivered Main ac65/tree 581f completed the 473→581f
release-pin reconciliation and retained all nine separately corrected upstream
test pins. Later joint 4b088 focused 709 and combined b06 finite 27/whole-quality
checks have their own source qualifications; they do not rebind the 447 bodies
to a new tree or create fresh paired-profile acceptance.

## Behavior and authority

The existing TaskRuntime.commit callback may return a typed waiting RawTaskState
with policy allSettled, a durable checkpoint and on IDs. It ends the invocation
and releases the scheduler to other tasks. Once every named target is terminal,
explicit enabled scheduling reserves the same task directly as running at the
saved checkpoint. All five nonterminal states block, including completing;
faulted, aborted and other non-success terminal outcomes release it. Empty on is
ready and duplicates have no extra authority. Cross-conversation targets are
allowed when they exist in the same coherent candidate view.

Ordinary includes custom pi.user-* names. Exact reserved pi.generation/pi.tool
authority remains unchanged. Generic failFast refuses before persistence, even
with empty on or no existing failure. It is not admitted until DE16 implements
real sibling cancellation and settlement. Sleeping, abort handlers, new public
wait/create/query APIs and active cascade are not supplied here.

## Transaction-local graph

Before callback table writes, the scheduler captures typed task/conversation
projections through its genuine AsyncTx. The existing SyncTx OwnedLiveQuery and
its four scalar task projection remain unchanged. Added immutable on tuples
contain only task IDs; they retain no checkpoint, inputs, documents or callbacks.
Each answer overlays actual staged records using the existing core write-kind
identity and ownership validation. No stale bool/on cache, caller-supplied graph
or backend read after callback writes supplies authority. Existing typed-row
fetch/decoding costs remain separate from graph traversal; no RSS claim is made.

New waits reject unknown targets and cycles with UnsupportedTaskExecution;
malformed state fields retain RecordError. Combined edges include an unmarked
waiting task's on list and a live task's live ordinary foreground descendants.
Terminal wait targets stop traversal. Ownership traversal crosses terminal
intermediates and owned conversations, and cuts background subtrees. Explicit
waits on background targets still inspect those targets' wait edges. Historical
failFast rows contribute their literal graph facts without gaining execution
authority. Abort-marked rows contribute owned work but no on edges.

Traversal is iterative: visited task IDs and ownership nodes bound repeated
work. A live direct foreground child already holds its owner and adds no new edge.
A native-only genuinely committed owned-background row retains an explicit on
edge; constructing that row is not claimed as Pi public-API parity. The
existing global ownership validation still rejects malformed ownership graphs;
this is not a replacement for transaction assembly validation.

## Lifecycle and evidence boundaries

Current invocation, health, close, definition/restart and Session FIFO gates stay
authoritative. DE15b genuine completing finalization precedes readiness. Commit
wake scheduling is reused without a timer/index. Cold recovery preserves waits
and dispatches nothing; close joins admitted handlers and finalizers before
lease release. Refusal rolls back staged durable writes under existing rules;
intentional mint/staging allocation and external callback effects are not undone.

Core owns real child/checkpoint continuation, five-live-state readiness,
empty/duplicate terminal waits, typed/no-write refusals and basic mixed cycles
with positive neighbors. The existing unsupported-record row now names failFast,
while the new Core module owns the supported empty allSettled case. Independent
Broad owns deep/paged graphs, genuine later candidates, FIFO, cold state, SQLite
cuts and joined lifetime witnesses. The current native results are recorded above;
the historical source-only checkpoint remains distinct. No new reference profile,
phase or parent closure is created by this slice.

## Integrated source boundary

The combined b06 source uses delivered Main `ac65c02e` (tree `581f7120`) and the
real common Git ancestor `2da6ee7a`; it retains all nine corrected literal release
1.4 upstream test pins and derives parent hashes from the same manifest paths.
Reviewed DE15b/c production bytes are preserved. The migration and
reserved-lineage fixture corrections retain their separately qualified evidence.
The 447 passing bodies remain 7ec9 evidence, and CODE 3950 remains ISSUES/advisory
with its sole wording Suggestion fixed. No new public API, paired profile or
parent closure follows.

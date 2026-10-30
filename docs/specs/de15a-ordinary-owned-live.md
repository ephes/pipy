# DE15a — private ordinary-owned-live query

This bounded read-only prerequisite is implemented on delivered B7 commit
`5a9bb4874a158427fb5876670064ce2cc838789f`, tree
`ad61d8c835594c666801b3052cb20ecf442a1cba`. It extracts the ownership-query part
of the original DE15/DE14 dependency without closing DE14/P07, DE15/P09 or any
of the 43 parent criteria. The [test matrix](de15a-ordinary-owned-live-tests.md)
separates requirements from the [dated development evidence](../evidence/de15a-ordinary-owned-live.md).

## Private API and coherent view

A single function that scans SyncTx after candidates are staged is invalid:
Tx._reading rejects every table read after the first table write. A caller-supplied
record overlay or done-ID set would introduce unproved candidate/removal authority.
Replacing scheduler._owned or require_unmarked_root_leaf would change admission.
Reject those options.

Private module `ordinary_ownership.py` provides `OwnedLiveQuery.capture(tx: SyncTx)`
and `query.has(task_id: int) -> bool`, unexported from SDK/Product/CLI. This is a
transaction-local read view, not a persistent cache, scheduler index or DTO. Capture
all committed tasks/conversations before any table write. Retain the actual tx
lifecycle authority and only immutable graph projections for that callback: task
and conversation IDs, ownership/containing-conversation edges, task background and
terminal-state classification. Query-owned graph storage must not retain full
TaskRecords/ConversationRecords, inputs, checkpoints, memos, documents or their
payload trees. The underlying live transaction retains its existing authority and
backend objects; this is not a new whole-object retention or RSS guarantee. Each has call checks that exact
transaction's existing _core._enter lifecycle/thread gate, then obtains its real
staged_tasks/staged_conversations tuples and builds a fresh effective view. Those
two existing accessors do NOT themselves assert open; relying on them alone would
permit stale post-settlement use. No new mutable Tx field or facade API is needed. Construction is private to
capture(tx): no constructor/factory accepts caller record maps, overlays or graph
projections. Every has access, including repeated and post-transaction calls, gates
the actual transaction lifecycle/thread before reading candidates, validating the
argument or answering. Keep this private lifecycle dependency explicit and tested. Capture after a table
write propagates existing ReadAfterWrite; settled/wrong-thread access propagates
existing TransactionSettled/thread-affinity error. No bypass of read/write phase.

A synchronous Session commit callback supplies one coherent line-owned view. Capture
performs real complete paged reads; no other Session transaction interleaves. There
is no scheduler condition/cache involvement: this query reads only durable records
and the same transaction's candidates, never generation/invocation/inspection state.
The query itself never stages, mints, commits, enables/notifies, fits or calls user
callbacks. A surrounding transaction that intentionally creates/replaces records
may write; distinguish its writes from query-only no-write tests.

## Effective graph and candidate authority

Project exact real staged TaskRecords and newly staged ConversationRecords into
the same minimal immutable fields, then merge those projections with captured
committed projections. This query classifies the complete committed plus genuine
staged ownership graph; it does NOT predict transaction assembly or commit eligibility.
Staged tuples alone are NOT create/replace authority: they omit whether a node was
actually minted or is a replacement of existing committed work. After the exact tx._core._enter()
lifecycle/thread check, read that same core's existing private _tasks entries and
the staged tuples on the synchronous Session line, without yielding/callbacks.
For each non-None entry.write, read its existing ("create" | "replace", TaskRecord)
tag/record; require entry key = record ID and the record is the actual matching
staged_tasks item. Conversely every staged task must have that exact write entry.
Ignore read-only entries with write=None. Read _tasks directly: never call
_task_entry/_committed, mutate the map or writes, invent a tag, inject a candidate,
or add a Tx field/facade. Unknown/inconsistent write metadata is invalid graph.
This explicit private dependency carries existing authority; it is not a new one.

A "create" tag is produced by real create_task after minting and is preserved by
subsequent set_task of that new task. Its ID must be absent from the committed
projection and must not collide with another node. The final current creation
candidate supplies its typed graph fields: no earlier create payload/history is
retained or invented. A "replace" requires that ID in the committed projection
and that committed task was nonterminal. Reject EVERY replacement of a committed
terminal task, including terminal-to-terminal. These are bounded graph-authority
exclusions, not a promise to replicate all final Tx validators.
A replacement for an uncommitted ID is a phantom, not a new graph node. Refuse
these cases before traversal, even when unrelated to the requested root.

For a replacement, conversationId, owner (including absence) and background must
match the committed immutable graph projection exactly. Refuse candidate parent
or background changes; do not guess Pi graph-index relink semantics. Candidate
state can change live membership: terminal removes the task from LIVE matching
while retaining structural edges, so ordinary live descendants remain discoverable.
Newly created task candidates have no committed predecessor; their genuine create
tag and final typed graph are evaluated as creation, not as a claimed historical
replacement. Newly staged conversations must not overwrite committed conversations.
The create tag proves minted-node authority, not legality or readiness to commit.
A real create followed by set_task changing its owner or background is evaluated
from its final typed graph when that topology is valid, without inventing original
creation history or applying committed-replacement immutability to it. Such mutation
witnesses are native-only; no Pi immutable-index mutation parity is claimed.
Create-side assembly validation is explicitly out of scope: new tasks and owned
conversations may have an owner later made terminal, completing or abort-marked in
the same transaction. The graph query can answer normally while the genuine outer
transaction subsequently refuses assembly and rolls back. Keep the graph answer,
query-only no-write observation and actual commit/refusal/rollback outcome separate.
Do not add owner-state or abort-mark eligibility to this read primitive, call assembly
from it, or modify Tx/backend validation policy.
Release temporary row/entry/tuple references after projection; store no candidate
payloads/write entries or effective-view cache across has calls.

Physical record/subtree deletion, None/tombstones, caller maps/candidate lists and
Pi completion-pass done/pruning sets have no authority here and remain deferred.
The bounded query is stricter than Tx for changes of committed ownership fields;
it does not change Tx/backend policy. In contrast, an ALREADY COMMITTED task-owned
background record is a valid native traversal boundary: create_task prohibits that
combination, but a separate genuine set_task transaction can commit it. Capture it
in a later transaction and exclude that task and its descendants. Distinguish this
native-only committed-state witness from a candidate background flip, which this
query refuses. No Pi ownership-mutation parity is claimed.

Build task-parent edges from owner when present, otherwise containing conversation.
Build conversation-parent edges only from owner.taskId, verifying the owner task's
conversationId equals owner.conversationId. Conversation.parent/fork lineage,
waiting.on, links and trace are NOT ownership edges. Every task must have a known
containing conversation. Reject task/conversation ID overlap, dangling ownership,
wrong-conversation child ownership, duplicate/nonmonotone pages/cursors and ownership
cycles across both node kinds. Validate the complete committed/effective view before
returning true: malformed unrelated topology does not become a successful answer.
Global refusal is intentional even for an unrelated requested root. Dangling,
cross-conversation, self-owned and cyclic task-owner edges can be produced by
separate genuine set_task commits: replacement validation does not constrain those
owner fields. They are real native committed-state witnesses, not kernel-only or
SQL-corruption fixtures. Capture/query after the offending commit must refuse the
whole invalid graph. This query detects such topology without repairing it or
changing the underlying write/commit policy.
Use exact existing typed records/finite safe IDs. Do not add orphan status/abortmark/
reserved-kind restrictions: this read primitive classifies records, not execution
or settlement eligibility. Built-in kinds receive no invocation authority.

From owner task, walk descendants iteratively. Do not count the root itself or its
ancestors. Count any reached non-background task whose state is not terminal,
including completing. Traverse through terminal ordinary tasks and owned conversations.
A background task is neither counted nor entered; all descendants beneath it are
excluded. An unrelated background sibling does not hide a later foreground sibling.
The queried owner may itself be background: traversal starts below it, matching Pi's
start-node semantics. No wait-policy, failure propagation or cascade is inferred.

Page size128 is a fetch bound, not a graph-size cap; continue to exhaustion with
strict ascending/progress validation. Graph-projection validation, adjacency construction and traversal are O(V+E)
with O(V+E) graph storage, iterative and without silent truncation or an arbitrary
new graph limit. Existing typed-row fetch/decoding can read payloads and has its own
cost; it is not covered by that graph-only complexity statement. No new RSS,
payload-size, decoder-memory, total retention or backend performance guarantee,
and no persisted index, follows from this query.

## Errors, observation and scope

Validate active transaction before has(task_id) argument handling, then exact safe-ID
rules; invalid ID types/ranges use existing TypeError/ValueError conventions.
Missing owner in the effective complete view raises ValueError('Unknown task').
Malformed graph/protocol or unsupported candidate parent change raises
ValueError('Invalid ordinary ownership graph'); it never returns false. Native
RecordError/ReadAfterWrite/TransactionSettled/backend errors remain unwrapped.
Do not import scheduler errors or change orphan refusal wording. Neither existing
conservative _owned nor strict-root-leaf helper is replaced, generalized or called
through this query. All dispatch/completing/wait/abort/handover behavior is unchanged.

Future comparison uses genuine Pi Full Harness public held completion and raw
records/source-proven ordinary ownership behavior; never instrument #ownedLive.
Native ended intermediates are produced explicitly by real Session commits with
direct tx.set_task terminalization after constructing the owned graph, with native
execution disabled. They are not scheduler-executed/completing counterparts of Pi
FullHarness completion. Preserve raw outcome, startedAt/endedAt, callback and
lifecycle differences; only the stated ownership boolean/held-state projection is
common. This fixture authority adds no native dispatch/completing behavior.
Native boolean true projects only to Pi owner completion being held by ordinary
work at the corresponding witnessed boundary; false projects to completed release
once that work settles. Preserve all raw differences/callbacks/ACKs/versions/records.
The native side only queries actual Tx graphs; do not implement native completing
or execute unsupported owned work to manufacture symmetric output. Completion-pass
done optimization, malformed graphs and ephemeral candidate controls stay separate
native tests unless a genuine public Pi witness exists.

## File ownership and integration boundary

The native owner maintains `ordinary_ownership.py`,
`test_ordinary_owned_live_core.py`, these three DE15a documents, release/central
status synchronization, and subsequent canonical/registry/coverage integration.
The isolated broad partition is `test_ordinary_owned_live.py` plus
`ordinary_owned_live_support.py`. The reference partition owns the bounded
ordinary-owned-live comparator, native/value/TypeScript producers, comparison
test/support and input fixture. The parent profile is the existing
`orphan_missing_migration` profile; ancestry does not expand its semantics.

The query does not replace either existing ownership fence. Physical deletion,
done sets, reparenting, dispatch/completing/wait/cascade behavior, public abort
and handover remain outside this increment. The dated evidence records which
parts have actually executed; the remaining integration is not implied by the
implemented private API.

## Bounded reference profile

`task-ordinary-owned-live-v1` follows `orphan_missing_migration` in the cumulative
recipe. Its common profile is `ordinary-owned-live-held-completion-common-v1`.
The native side uses the private SyncTx query and direct real Tx terminalization;
the real Pi Harness supplies public held-completion/release witnesses. Raw state,
callback, time and lifecycle differences remain lane-specific. Candidate and
invalid-graph controls remain native-only. Registration closes the actual query,
core/broad/reference support and document sources, without changing old profile
IDs, member order, Pi pins or parent edges. Configuration is not runtime acceptance.

## Later DE15b private scheduler consumer

DE15a's SyncTx-only capture/has API and its original reference profile remain
unchanged in scope. The later [bounded DE15b implementation](de15b-owned-completing.md)
uses an awaited genuine AsyncTx capture sharing pure Page/projection/graph and
candidate validation, without coercing AsyncTx into SyncTx or changing the read
query into admission authority. Only that scheduler consumer applies ancestor
cancellation refusal and healthy owned/completing execution. The existing strict
orphan and model/tool fences remain distinct; no DE15a runtime result is rebound.

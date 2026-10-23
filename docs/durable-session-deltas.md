# Default current-document deltas (DE04b1b3a)

The private `native/durable/tx.py` joins the delivered
[draft Tracker](durable-draft-tracker.md) to actual Session transactions and
[current-delta Storage](durable-current-deltas.md). A new document writes one
base. An ordinary loaded document writes a delta when its normalized operations
are nonempty. Definitions still have no checkpoint callback API. This changes
the private durable Session; it does not make durable execution the product
runtime default or close any P row.

`Draft` and `DraftList` remain the actual Python mapping/sequence types returned
by `SyncTx.doc` and `AsyncTx.doc`. They are guarded adapters over one `Tracker`
per change. Nested adapter identity is cached, and every mutation uses that
Tracker's facade. There is no second raw mutable tree or second normalizer.

The supported mapping methods include assignment, deletion, update, pop,
popitem, clear and setdefault. Sequence methods include integer and contiguous
or extended slice assignment/deletion, append, extend, self-extend, `+=`, remove,
insert, pop, reverse and clear, with negative indices and either slice direction.
Reads, iterators, snapshots and comparison methods retain the existing Python
surface. Slice/pop results and operands handed to foreign equality are detached.
Placing a live or removed view copies its current subtree. Replaced and removed
handles remain locally usable until settlement, but no longer modify their old
path. Unsupported JS Proxy/coercion, holes, sort, fill and copyWithin surfaces
are not introduced.

The outer guard preserves `DraftRevoked` before driver thread/loop affinity.
Wrong-thread access keeps `TxAffinityError`; resumed iterators check again.
Foreign iterables are admitted outside locks, followed by liveness and coordinate
checks. Empty update/extend iterables also receive a final liveness check.
Copy/deepcopy/pickle, attribute mutation and reinitialization are refused;
representations contain no document content. Deliberate reflection into private
implementation state is outside this private facade contract.

## Candidate, operations and write selection

The owner selected **“Use the persisted value.”** For loaded documents the
immutable candidate is exactly `Tracker.prepare().replayed_value`: application
of the normalized operations to the committed baseline. Preparation revokes all
views before assembly; any failure aborts every document change. Publication
retains the same owned operation object prepared by the Tracker, and its value
is the same owned object installed in the committed cache. Thawing either object
cannot mutate the other or a stored revision.

Creation snapshots the actual final created object before preparation revokes
its views. It writes that object as a base and publishes empty operations. This
preserves final member order and signed zero even when normalization of the
initial-to-final change would omit a scalar write. A loaded empty operation batch
writes and publishes nothing and keeps the old committed incarnation.
Loaded acquisition passes its owned baseline directly to Tracker, avoiding an
unused full copy. Creation still runs Tracker preparation for uniform revocation,
then uses its separately captured final object for the base.

Operation emptiness is the write decision, not final-value equality. Appending
and then popping an introduced value can normalize to no operations. Removing
an existing array element and inserting an equal value retains structural
operations, writes a delta and adopts a new snapshot even when its value equals
the prior value. Reserved-key folding and the greater-than-4,096-operation root
replacement are still deltas; there is no hidden base checkpoint threshold.

A pinned Pi signed-zero case has an unrelated field update as its only operation,
while Pi's prepared/warm/publication zero differs from replay and stored zero.
Native uses the replayed zero consistently for loaded cache/publication/storage.
It does not reject the valid transaction or globally normalize zero or object
keys. Native tests use strict owned equality for binary64 signs, finite extremes,
lone UTF-16 surrogates and member order. The predecessor driver recipes below
do not constitute the complete later callback/signed-zero comparison matrix.

## Counts, admission and failure

`CommittedDocument.deltas_since_base` is required committed state. Cold load
retains Storage's `deltasSinceBase`. Creation adopts zero; an acknowledged loaded
delta adopts the previous count plus one. Empty content retains the prior count.
Value and count reside in one immutable cache object swapped under the cache
lock after backend acknowledgement, so concurrent snapshot hits see a committed
pair. A fresh SQLite Session reloads the persisted count and continues its tail.

Existing validation, complete terminal-task scans, submission resolution,
table-before-document ordering, one guarded backend admission, adoption and
publication ordering remain. A dirty document that retires still writes content
before retirement. Retirement overrides publication with null value/version and
empty operations and evicts by incarnation. No transient retiring candidate is
installed. Same-transaction retirement/recreation retains its distinct records.

Preparation or late validation failure performs no backend admission or adoption.
Backend failure keeps the previous retained snapshots and publishes nothing.
Session failure/close may then clear cache residency. A real SQLite COMMIT whose
acknowledgement is lost may have persisted the delta and coupled table writes
without adoption/publication. The Session fails and closes; a fresh Session
observes the durable result. There is no retry or claim of rollback after COMMIT.
Storage schema, codec, locking and path/file-descriptor ownership are unchanged.

## Verification boundaries

`tests/durable/test_session_deltas.py` drives actual Session Draft methods in
sync/async modes over Memory, SQLite and reopen fixtures. It covers exact
operations, empty versus structural edits, signed-zero authority, immutable
operation/value identity, creation, fallback, detached handles, foreign iterable
reentry, preparation failure and private facade guards. The existing transaction,
document and SQLite suites retain their atomicity/lifecycle controls and add
committed counts, delta write shapes, real delta acknowledgement loss, real
SQLite refusal and fresh-Session count continuation. Source mutants exercise
active copying, revocation, ingress, empty/structural selection, count adoption,
cold counts and publication operations.

The existing Session-commit and typed-document predecessor drivers now omit
`checkpointWhen` and compare complete actual writes, stored counts and published
operations. Their closed schemas reject missing or malformed publication ops;
semantic controls distinguish lost ops and an ordinary delta replaced by a base.
The Session-commit schema/CLI test fixture remains relabelled native test data,
not Node evidence. Actual pinned source executions are still required by its gate.

The new `session-documents-deltas-v1.expected.json` is a compressed capture of
actual pinned Node execution of the migrated 24-recipe document driver. Its raw
size is 2,428,093 bytes and raw SHA-256 is
`e592dcf85c2e5a1329436e0f1f62936eb8cfce60cca242eac89b552c315608fd`.
The original `session-documents-v1.expected.json` remains byte-for-byte historical
A evidence with the old forced-base adaptation. The new capture used a verified
pinned source/dependency closure with reused dependencies; it does not substitute
for a fresh complete acceptance chain.

DE04b1b3b retains real checkpoint callbacks, eligibility and post-I/O authority,
base reset, actual line-owned unload and complete paired proofs. Test-only cache
clear used to exercise cold load is not unload lifecycle acceptance. History,
as-of, fork and migrations remain DE27. DE04 and its parent acceptance remain
incomplete until the retained cells finish; DE05 still waits for complete DE04.

# Supplied current document deltas (DE04b1b2)

The private Memory and SQLite Storage backends accept owned
`StorageWrite` batches containing current document deltas. This is the independent
storage slice S: it does not integrate tracking, Draft/Tx preparation, checkpoint
callbacks, Session adoption or linked publication. Those remain in DE04b1b3,
including the owner's still-open policy266 decision about candidate selection.
Copy, numeric historical reads, fork reconstruction and full history policy
remain DE27. No product factory or default persistence path changes.

A base creates or replaces an object and resets `deltasSinceBase` to zero.
A delta requires an existing unretired document with a numerically matching
finite version. It applies the supplied operations sequentially to a detached
candidate using [the decoded applier](durable-delta-ops.md), requires an object
final root, preserves the document record and base version, and increments the
count once per committed delta command. An empty batch, equal replacement or
structural no-op still increments the count. Storage does not decide whether a
caller should submit that command. Positive and negative zero compare equal as
version numbers; the materialized version retains the base's original bits.

DTO intake owns the JSON and checks the content envelope and batch array shape.
It does not prevalidate later operation grammar ahead of an earlier path
failure. Every operation validates when it is applied. A temporary nonobject
root inside a batch is allowed if the final root is an object. Reserved path
segments are refused, while those names in replacement payloads remain valid.
Binary64 bits, the full finite number range, UTF-16 identities, lone surrogates
and deep values use the existing lossless codec. Retained `StoredDocument`
values and caller trees never mutate.

Mixed table/document commits keep the existing single authority and atomic
candidate adoption or SQLite transaction. A late invalid operation, version
mismatch, nonobject result or address conflict publishes no tables, documents,
indices, IDs or sequence advance. Existing SQL failure rules remain: a failure
before COMMIT rolls back and permits reuse; a raising COMMIT or failed rollback
poisons the instance until close/reopen. A lost acknowledgement after successful
COMMIT propagates while the committed rows remain durable.

## SQLite format and current reconstruction

Semantic schema version 2 is an appended empty migration. Version 1's DDL and
all existing base bytes remain unchanged. Header and `durable_schema` versions
advance together under the existing migration/path authority. A test-only
fixture contains the exact delivered pre-schema2 source at
`6e3b838fd253e7793c1d1365c6efc2d233a0ecce`, with per-file SHA-256 provenance.
Tests execute that actual old opener against both a checkpointed version-2
header and an upgrade held only in the WAL; it refuses before modifying the
database or WAL. The fixture works without a Git checkout and is not a product
legacy implementation.

A delta writes its actual `kind`, version and operations to a new revision row.
Current-only documents retain their required base and tail. A new current-only
base replaces the prior revisions, and retirement reclaims them. Rewindable
records retain older rows, including on retirement, while numeric historical
reads remain unsupported.

Current reads use one existing read transaction for the document record,
authoritative sequence bound and revision scan. The indexed scan walks backward
only to the newest required base, then replays those deltas in ascending order.
Every reached revision column is storage-class checked before decoding; identity,
sequence bounds/order, kind, finite version, operation validity and object roots
are checked. A current-only document cannot have revisions before its base.
Malformed required content raises `StorageCorruption` with content-free messages
and is never repaired by falling back to a prior value. Rewindable history older
than the current base is outside current reconstruction. This is structural
validation of the reached chain, not cryptographic detection of arbitrary
external deletion or rewriting of otherwise-valid history.

A cold read and delta preparation replay the required current tail, so their
cost grows with that tail and the values/operations it touches. Across N repeated
deltas, repeated cold reconstruction can accumulate quadratic replay work.
There is no hidden forced base, count cap or new cache. SQL appends only the new
revision; it does not rewrite the retained base/tail or read older irrelevant
rewindable revisions. Measurements must report current-tail replay separately
from retained-history size; no O(1) reconstruction or performance-parity claim
is made.

## Pinned direct-storage comparison

```sh
PATH=/path/to/node26.12.0/bin:$PATH PYTHONPATH=src python \
  scripts/durable_current_deltas_comparison.py run \
  --pi-source /absolute/pi-mono-checkout --output /new/private/output
PYTHONPATH=src python scripts/durable_current_deltas_comparison.py compare \
  /new/private/output/observed.json /new/private/output/native.json
```

The driver executes actual pinned Memory, SQLite and SQLite-close/reopen storage
from read-only `git show` at `2ec55d808d455b1a64a2bf7888198207a096120a`, using
Node26.12.0/npm11.20.0. It copies a closed source snapshot and binds loaded
modules, native source, inputs and test support by hashes. Each process uses
private output-local HOME/TMP/npm paths and bounded process-group execution and
reaping. No dependency installation occurs. This is direct supplied-operation
source evidence, not a fresh cumulative bootstrap or Session integration claim.

Nine scenarios cover all seven operations, ordered repeated tails, empty and
structural no-ops, base reset, finite extremes, signed zero, UTF-16 and reserved
payload keys. Results compare binary64-bit and UTF-16-unit profiles. Native
Memory/SQLite/reopen agree. Pi SQLite's established JSON normalization loses
negative zero and rejects fractional/huge content versions in its INTEGER
column. The comparator declares these exact scenario differences; it does not
normalize native storage. Invalid native batch rollback and corruption are
native tests, not claims that Pi validates malformed batches at commit time.

The CLI returns 0 for equality, 1 for a schema-valid mismatch and 2 for invalid
evidence or prerequisites. Each run executes positive and mismatch controls and
retains source, import, loaded-module and artifact hashes. The native tests also
exercise real query-only, page-limit, foreign-lock, COMMIT and acknowledgement
failures, bounded SIGKILL cuts, snapshot concurrency and actual old-source
refusal. Final integration, cumulative evidence and independent review belong
to the root gate; this document does not close DE04b1b, DE04b, DE04 or any P row.

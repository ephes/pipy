# Pure draft operation provenance (DE04b1b1)

`native/durable/draft_tracker.py` is a private prerequisite for the later
Draft/Tx join. It does not change the active Session drafts, definitions,
checkpoint policy, Storage, or SQLite schema. It imports only the delivered
owned JSON codec and decoded-operation validator/applier, plus the standard
library. Current runtime integration remains DE04b1b3; current delta persistence
is the independent DE04b1b2 slice.

`Tracker(owned_base, check=None)` creates one callback-local working change over
an owned object or array. `root` exposes private mapping/sequence views.
`prepare()` revokes every view before emitting, validating or replaying operations
and returns frozen `PreparedOperations(ops, replayed_value)`, both `OwnedJson`.
`discard()` also revokes all views. Each thaw/snapshot is detached. The optional
check is the integration point for the existing driver guard; it is invoked
before an operation, with revocation checked before thread affinity and again
after the check. Preparation cannot be retried after emission/validation fails.
The caller must retain its existing single mutation authority. This helper adds
no lock, transaction owner, scheduler, publication mechanism, or public draft API.
The later join must route **all** existing draft mutators through this helper and
preserve its existing exception types/lifecycle at that boundary.

## Normalization and ownership

Object provenance retains first-write order, deletions and delete/re-add history.
Native drafts, owned operation payloads and replay retain Python mapping member
order, including exact string keys such as `"2"`, `"1"` and `"01"`. Preparation
does not reorder them into JavaScript enumeration order. Placement copies retain
that native order; the separate comparison projection below never changes a
product value, publication, or stored representation.
Arrays retain baseline element identity separately from introduced elements;
structural edits never infer identity from value equality. Base ranges are
compressed, observed base entries are sparse, and container metadata is created
on access or placement rather than recursively for the whole tree. Inserted
runs coalesce at the append boundary. Reindexing uses retained identity, and
replaced/removed handles remain locally usable until settlement while ceasing
to contribute operations. Placing any view copies its current subtree, including
when it has become detached or belongs to another open context.

Emission follows pinned Chord ordering: reverse-index removal runs, retained
permutation, insertion runs, base overrides, then descendants at their final
paths; shallower nodes precede deeper nodes and peers retain first-dirty order.
Object re-add deletes precede writes, which precede ordinary deletes. Introduced
subtrees fold into their placement payload. Reserved path keys fold to the
nearest safe ancestor replacement, including mutations restored to equal values.
Ordinary equal scalar writes normalize away; equal container placement still
detaches the previous handle even when the operation later normalizes away.

String operations use UTF-16 units, the pinned 65,536-unit overlap window,
64/1-unit probes and eight-candidate limit. `t` removes a prefix. At 256 distinct
dirty positions, dense regions use pinned gap/density rules; restored descendant
writes can still produce a region replacement while restored direct scalar
array overrides disappear. Any effective structural mutation permanently
disables that array's dense folding for the change. The pinned operation-count
fallback replaces the root after more than 4,096 ordinary emitted operations.

The private facade preserves mapping update/pop/popitem/clear/setdefault and
sequence append/extend/self-extend/pop/remove/insert/reverse/clear, negative
indices, contiguous slices and positive/negative extended slices. Contiguous
slice writes are structural splices. Extended slice writes admit the complete
input and verify length before changing entries; extended deletes run in reverse
index order. Slice/pop results are detached. Exact keys/indices and owned JSON
admission reject foreign coercion, subclasses, cycles, nonfinite/inexact numbers
and duplicate UTF-16 key identities. Comparison methods operate on detached
snapshots; foreign equality never receives live state. Iterable admission may
execute caller code, so liveness and coordinates are checked again afterwards.
There is no foreign callback or lock inside normalization/replay.

## Candidate value remains an explicit join decision

`replayed_value` names application of the exact operations to the baseline. It
is **not** an authoritative callback/cache candidate. Owner policy266 remains
open for the final join. In two measured signed-zero recipes, pinned Chord's
simple-object preparation returns the opposite signed zero from replay while
emitting only an unrelated field update. The comparison records Chord's prepared
profile independently and asserts that both discrepancies remain visible.
Native tests do not erase sign bits or label those candidates equivalent.
The separate native storage codec's lossless binary64/UTF-16 contract is unchanged.

## Verification and limits

`tests/durable/test_draft_tracker.py` exercises normalization, method routing,
placement/reindex/detachment, ownership, failure revocation, foreign inputs and
comparisons, iterator-finalizer/reentry behavior, thread affinity, deep values,
and sparse metadata. It tests a 100,000-element baseline plus 1,000 appends with
zero observed base entries and two pieces, and a 1,600-level edited path without
recursive product walks. Initial admission and final replay still copy the
whole owned value; sparse provenance does not promise constant total commit cost.
Fragmented-array coordinate lookup scans pieces; this is not a balanced sequence
index or a universal sublinear-update guarantee. Held-entry preparation indexes
observed identities by sorted baseline positions rather than rescanning every
piece for every handle. Arbitrary Python reflection that deliberately mutates
private implementation attributes is outside the existing private-view contract.

The new closed comparison is:

```sh
python scripts/durable_draft_tracker_comparison.py run \
  --pi-source /absolute/path/to/pi-mono \
  --output /absolute/path/to/new-output
```

Use the pinned Node 26.12.0/npm 11.20.0 toolchain on `PATH`. The driver snapshots
only manifest-listed files using `git show` at
`2ec55d808d455b1a64a2bf7888198207a096120a`, performs no dependency install, checks
the actually loaded Node module set and this checkout's native import paths,
and bounds/reaps child process groups. The manifest binds native source, tests,
recipe data and driver bytes before and after execution. No historical parent
or prototype implementation is imported.

The 1,612 promoted recipes comprise 44 representative cases, 1,000 fixed-seed
map/list recipes, 500 nested/held-handle recipes, 14 dense/operation boundaries,
21 primitive translations of Python-method scenarios, 12 numeric/UTF-16 precision cases and
21 explicit numeric-looking-key recipes. The last group includes edits,
delete/re-add, nested payloads and placement without caller iteration; these
are ordinary supported operations. The 21 `surface/*` recipes execute explicit
set/delete/splice primitives on both sides; they do not pair actual Python
method dispatch against Node. Native tests separately invoke the real mapping
and list methods and assert representative emitted operations and replay.
Actual Node and Python independently profile binary64 bits, distinct bool tags,
UTF-16 string/key units and **ordered** object members before serialization.
Those raw strict profiles remain in the artifacts. Only operations, replay and
revocation are paired; Node prepared values and the two signed-zero witnesses
are retained separately. Raw JSON outputs accompany profiles and can themselves
normalize negative zero. Node producer observations must include a valid prepared
profile; general/native observations may omit it. The CLI returns 0 for equivalent
evidence, 1 for valid semantic mismatch and 2 for invalid evidence or unexpected
producer/native exceptions. Unexpected exception payloads and tracebacks are not
printed; process interrupts remain interrupts.

The paired relation is `js-array-index-own-key-order-v1`: for comparison only,
object members whose keys are canonical ASCII decimal array indices `0` through
`4294967294` project in ascending numeric order before all other string members.
The latter retain their relative order. `"01"`, `"4294967295"`, Unicode digits
and arbitrarily long digit strings are ordinary non-index keys. Classification
examines at most ten UTF-16 units and never parses an unbounded integer. This
projection applies at every object depth on both sides. It preserves every key,
value, binary64 bit and UTF-16 unit, all operation/path/list ordering and every
non-index member's relative order. It never globally sorts object members.

For example, explicit writes of `"2"` then `"1"` produce the same ordered `s`
operations, but native replay enumerates `2,1` and JS replay enumerates `1,2`.
Nested replacement payloads have the same representation difference. The gate
asserts ordered operation and typed-value **equivalence under this declared
projection**, not raw ordered-profile or serialized-byte equality. `result.json`
names the relation, reports `equivalent` and `raw_ordered_profiles_equal`
separately, and lists every raw operation/replay profile difference. Raw profiles
are never rewritten to conceal these witnesses.

This preserves the delivered `OwnedJson` order-sensitive representation and A's
Python mapping surface. The decoded-op prerequisite already compares object
content without enumeration order; this tracker relation is stricter because
non-index member order and operation sequence still matter. The plan excludes
Chord wire encoding, so cross-language JSON payload text need not be byte-identical.
Native prepare/replay/cache/storage agreement must still use actual owned values,
not the comparison projection. No candidate-policy decision follows from it.

Each run records 18 projection controls: three accepted index-enumeration
differences and 15 rejected differences covering boundary/ASCII spelling,
ordinary member and operation order, missing/changed keys and values, bool/number,
signed zero and UTF-16. Native tests independently pin unchanged draft, payload,
replay and codec round-trip order. Private comparator and production source
mutants exercise the respective ordering, normalization and lifetime mechanisms.
Malformed evidence fails separately from a valid semantic mismatch
(`run`/`compare`: exit 0 equivalent, 1 mismatch, 2 invalid).

This corpus does not establish parity for JS Proxy descriptors/coercions, holes,
borrowed methods, comparator-based sort, fill/copyWithin, or mutation decisions
based on JS integer-key enumeration. Those are not the existing Python draft
surface; explicit-key edits and numeric-looking payload members are included as
described above. Full history/as-of/fork/migrations remain DE27. No DE04 parent or parity
row closes merely because the pure helper passes its focused checks.

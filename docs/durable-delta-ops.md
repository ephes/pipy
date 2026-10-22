# Pure decoded durable operations (DE04b1a)

The private `native/durable/delta_ops.py` primitive owns, validates and applies
already-decoded Chord operations to owned finite JSON. It has no runtime import,
operation tracker, draft integration, Storage capability or schema change.
`own_operations(batch)` returns immutable `OwnedJson`; `apply_operations(value,
batch)` returns a detached final `OwnedJson` or raises without altering either
input. The optional `own_operations` convenience validates the entire batch
grammar at once. Application also accepts a raw `OwnedJson` batch and validates
operations sequentially, so an earlier path failure wins over later malformed
grammar, as in the pinned applier. Validation and replay touch only the private
candidate; no intermediate candidate is published.

The exact decoded vocabulary is:

| Tag | Tuple | Effect |
| --- | --- | --- |
| `r` | tag, value | Replace any root with JSON |
| `s` | tag, nonempty path, value | Set object member or existing/append array element |
| `d` | tag, nonempty path | Delete object member (missing is harmless) or splice existing array element |
| `a` | tag, nonempty path, string | Append to an existing string |
| `t` | tag, nonempty path, count | Remove a UTF-16 prefix from an existing string |
| `p` | tag, path, index, remove, items | Splice array; oversize index/removal clamp |
| `m` | tag, path, permutation | Reorder array with `new[i] = old[permutation[i]]` |

Paths are exact arrays of strings or nonnegative integral finite binary64
numbers. Only `p` and `m` admit empty paths. Numeric array segments are required;
strings such as `"0"` do not index arrays. Set may append exactly at length but
cannot create holes. Delete at array length fails. Every traversal uses own
members and must resolve through containers. Object numeric segments use JS
number property spelling, and string keys match UTF-16 identities. Reserved
**path segments** `__proto__`, `constructor`, and `prototype` are refused; those
names remain valid inside replacement/set/splice JSON payloads. Permutations
must be complete bijections of the target length. There is no wire dictionary,
path ID, omitted path, `#` operation, or generic JSON Patch coercion.

Coordinates preserve pinned `Number.isInteger` semantics, including integral
floats and huge finite numbers; there is no invented safe-integer cap. Existing
`OwnedJson` rejects Python integers without exact finite binary64 representation,
nonfinite values, cycles, container/scalar subclasses, foreign objects and
UTF-16-duplicate object keys. Outputs retain finite binary64 bits (including
negative zero) and UTF-16 string identity. Each thaw returns a fresh plain tree.
For touched strings, both append and prefix removal return the Python spelling
obtained by a UTF-16 surrogatepass encode/decode round trip: valid surrogate
pairs become a scalar code point, including split-pair appends, empty appends
and zero-prefix removals. Lone surrogates remain admissible. Untouched strings,
replacement payloads and the existing `OwnedJson` domain are unchanged.
Python-only canonical-spelling tests supplement the UTF-16 unit profiles, which
cannot distinguish equivalent Python spellings.

A final scalar/list root is valid for this general JSON primitive. Future
**document Storage must separately enforce an object final root**.

## Pinned comparison

```sh
PATH=/path/to/node26.12.0/bin:$PATH PYTHONPATH=src python \
  scripts/durable_delta_ops_comparison.py run \
  --pi-source /absolute/pi-mono-checkout --output /new/private/output
PYTHONPATH=src python scripts/durable_delta_ops_comparison.py compare \
  /new/private/output/observed.json /new/private/output/native.json
```

`run` uses only read-only `git show` at
`2ec55d808d455b1a64a2bf7888198207a096120a`, copying a closed source snapshot.
It executes actual pinned `applyImmutable` and `JsonRevisionValidator` with
Node26.12.0/npm11.20.0. Driver load hooks refuse any dependency outside the six
closed source modules. The index's actual transitive tracker/diff/json/trusted
imports are hash-bound even though the driver never invokes tracking. No npm
installation or package bootstrap is performed; this is actual source evidence,
not npm package attestation. Every child uses private output-local HOME, TMP,
npm configs/cache, bounded process-group execution and reaping.

The closed66-case corpus compares exact operation and result profiles using
binary64 bits and UTF-16 units, with object members sorted by key identity.
Object enumeration order is not part of this JSON-value comparison. Errors
compare only the precise agreed class (`TypeError`, `PathError`,
`UnsafePathError`); error messages are deliberately not asserted. Python-only
exact-type ingress/ownership tests supplement this pairing. The Python JSON
reader refuses duplicate keys, nonfinite constants and overflow before writing
the Node input; the Node revision validator validates its parsed strict JSON.

Both commands return0 for equality,1 for a schema-valid mismatch,2 for invalid
input/evidence/prerequisites. Only `run` writes artifacts. Positive0 and valid
negative1 controls execute in each run. The manifest closes all loaded upstream
sources, driver, native primitive/codec, fixtures, tests and evidence helpers.
Existing/symlink output entries, output inside upstream, relative source paths,
non-root checkouts, source/driver/native drift and unexpected imports refuse.

DE04a retains typed current-base documents. DE04b1b depends on DE04a + DE04b1a
and retains normalized operation provenance/tracking, Memory/SQLite current
deltas and semantic schema2, real checkpoint callbacks, rollback and linked
publication. DE04b and DE04 wait for their cumulative children; DE05 waits full
DE04. Full history/as-of/fork/migrations remain DE27. All43 P rows remain OPEN.

The pure prerequisite landed at `6e3b838f`. The remaining parent is now split
before code into T (`DE04b1b1`, pure provenance/ops), independent S (`DE04b1b2`,
current delta Storage/schema2), and J (`DE04b1b3`, real Draft/Tx/checkpoint/cache/
publication join). S now uses this applier for [supplied current Storage deltas
and semantic schema2](durable-current-deltas.md), delivered at `8c8f30d7`. Independent [T pure draft provenance](durable-draft-tracker.md) uses the
applier to validate/replay immutable operations; it does not activate Session.
J requires the answer to owner policy266 before authoritative candidate/operation
selection.

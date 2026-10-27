# Tool details and presence

Normal native tool results and partial results carry arbitrary finite local JSON
in `details`: objects, arrays, strings, numbers, booleans and null. This value is
separate from `structured_content`, which belongs to programmatic callers.
Neither renderer-only details nor nested-call evidence is sent to a provider.
The canonical request still retains those local fields in its exact history.

Import the presence values from `pipy_harness.extensions` or
`pipy_harness.sdk`:

```python
from pipy_harness.extensions import NO_TOOL_DETAILS, ToolResult, ToolResultTransform

ToolResult(content="done")                         # absent details
ToolResult(content="done", details=None)           # explicit JSON null
ToolResult(content="done", details=[False, 0, "x"]) # a local JSON array
ToolResultTransform(details=NO_TOOL_DETAILS)         # clear details
ToolResultTransform(details=None)                    # replace with null
```

A transform left at `UNCHANGED_TOOL_DETAILS` preserves the original details.
Absence, unchanged and null are distinct; false, zero and empty arrays/objects
are values. Constructors and consumer projections detach JSON descendants.
Invalid finite-JSON data is refused by the value copier. There is no new global
JSON node, depth or byte cap on owned details.

Normal details use an iterative value snapshot. Tuple descendants normalize to
arrays, and Python integers retain their value, including integers that binary64
cannot represent exactly. Stable JSON container/scalar subclasses become builtin
values. Snapshots detach descendants, preserve integer/float types and signed
zero, and retain exact Python string and key representations. Non-finite
numbers, cycles, non-string object keys and unsupported values are refused.
The copier adds no recursion gate to old deeply nested message/v1 data and does
not pass owned values through a lossy JSON text roundtrip. Text serializers keep
their existing JSON interpretation separately: an explicit surrogate pair can
read back as an astral character from JSONL, while raw canonical v1 keeps its
original representation exactly. Schema and structured-content validators are
unchanged.

New `ToolExecutionResult` values separately retain native stdlib JSON encoding
admission. The detached snapshot must be encodable by the running interpreter;
encoding does not replace or normalize it. An unsupported depth or integer
decimal size raises a fixed `ValueError` at result construction. These limits
also apply to new ToolPort results used by the durable SDK; unrestricted deep
ToolPort production is not promised.

Extension final/partial forwarding and explicit replacement-hook details retain
their established JSON text-copy policy: the owned value must survive both
stdlib encoding and decoding, and the decoded value is forwarded. This converts
an explicit Python surrogate pair to its astral character in strings and object
keys, preserving the old successful UTF-8 output. A lone surrogate keeps its
existing UTF-8 writer limitation. Invalid extension details fall back to absence;
an invalid replacement makes the whole hook transform a no-op. Explicit absence
clears details, while `None` remains present null. `UNCHANGED_TOOL_DETAILS` copies
existing facts with the iterative value copier, without producer admission or
forwarding normalization. Encoding and decoding depth limits can differ.

Historical message/partial values and canonical decoding retain their broader
owned-value domain. That does not guarantee that every manually constructed
value can pass a normal JSONL/RPC serializer: those transports retain their
existing encoding/reading limits and string behavior. Producer admission is not
a new universal guarantee about full-envelope depth near an interpreter limit.

Durable ingress is a separate, stricter boundary: canonical records require
plain JSON and exact finite binary64 numbers. A normal value outside that domain
is refused before a durable result commits; it is never rounded into storage.
The ordinary SDK can return such normal values, while the durable SDK retains
the stricter storage contract. This Python compatibility is not a claim of
cross-language numeric parity.

The shared executor and non-durable loop preserve the final returned presence.
An absent final result does **not** acquire details from an earlier partial.
For example Bash can publish a partial with `{}` and finish with absent details;
its final JSONL/SDK value stays absent. Durable retained-progress fallback belongs
to the later DE13 continuation work and is not active in this ABI slice.

## Saved records and rich values

Session JSONL writes a `details` key for a value, including null, and omits it for
absence. Existing mapping/absent serialization remains unchanged. The internal
[canonical codec](durable-canonical-codec.md) keeps its version-1 schemas/arenas
and uses version 2 only when needed for new presence or typed nested evidence.
Known old version-1 `None` did not record absent versus null: decoding represents
it as `LEGACY_TOOL_DETAILS_NONE` and re-encoding preserves the original bytes.
It never invents historical null presence. New request histories can mix this
legacy state with new rich values without rewriting an admitted old request.

The public legacy marker supports reading and copying those existing facts.
New tool results, partial updates and replacement transforms must not emit it;
use the ordinary absence marker or a JSON value. The low-level copier preserves
historical markers and does not authenticate their origin.

The opt-in [headless CLI](durable-headless.md) run envelope is version 2. Its
tool message has `details_state` (`absent`, `value`, `legacy_unspecified`), and
includes `details` only for `value`. Its ordinary private tool-output/receipt
wrapper remains version 1 with an independently versioned canonical arena.
Inspection and retry envelopes retain their existing formats. Full-content
product output is not metadata-safe workflow data; do not copy it to workflow
events or automatically archive it.

## Finite nested-call evidence

Existing composite results with mapping/absent details keep their legacy merged
`nestedCalls`/`argumentPreviews` representation. A new scalar, array or null
details value cannot hold those fields. Such a result preserves the original
details and carries the actual existing service record in one optional typed
`NestedCallEvidence` field instead. Its calls, status, duration, bounded argument
record/error and previews use the existing record domain. It is renderer data,
not a new invocation or ownership authority, and does not use structured content.

Canonical v2 and normal forwarding preserve this field; JSONL uses
`nestedCallEvidence`, and CLI completed messages use `nested_call_evidence`.
An absent optional evidence field is omitted from the arena; an explicit null
for that typed field is invalid. JSON null remains a valid `details` value.
Renderers, HTML export, hooks and compaction read that field or the legacy
details record. They do not retain two authoritative copies. A hook that changes
details back to a mapping preserves the actual record in the legacy channel;
intentional summary/retention clears remove both kinds of local metadata.

This normal-value ABI is the DE13a prerequisite. DE13b adds retained progress
and ordinary failed-result continuation; DE13c1 adds interrupted continuation
for intact recovered execute and a saved/current next-tool intersection.
Historical blocked migration, operational restart ceilings, omitted-output
parity and general durable nesting remain with their later owners.
The full DE13 crash/effect/continuation acceptance and all whole parity rows stay
open. Provisional future progress admission limits are not limits or parity
claims for this final-details ABI.

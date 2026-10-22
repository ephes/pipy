# Internal canonical DTO codec

DE06a adds `native.durable.canonical_codec.encode_canonical` and
`decode_canonical`. These private functions encode native message, request,
provider result and tool outcome DTOs as a versioned finite JSON tree. They do
not enable durable execution or change a public API. Actual task checkpoints,
prepare/request/outcome phase envelopes and recovery integration belong to
DE06b after DE05. Cumulative DE06 and P15/P16/P19 completion requires the
corresponding integrated acceptance; current status is in the
[backlog](backlog.md#additive-canonical-dto-codec-de06a).

The codec explicitly lists each supported DTO and each field in its version-1
schema. It preserves fields excluded from dataclass equality: provider/API/model
and thinking metadata, rich usage and cost, structured tool results, diagnostic
details, termination controls and tool execution timing. Ordered text, thinking,
redaction, signatures, images, raw argument whitespace and both correlation ID
domains survive. Missing structured content is the canonical
`NO_STRUCTURED_CONTENT` enum; JSON null remains `None`.

## Format and validation

The envelope has exactly `codec_version: 1` and a nonempty `nodes` array. Node
zero is the root. Nodes carry closed tags and either scalar values or ordered
child references. Each reference names a strictly later node and every nonroot
node has exactly one incoming reference. Unknown versions, tags, fields,
duplicate keys, unused nodes, backward references, shared references and cycles
are refused. A decoder never imports a class named by stored data. Each DTO is
rebuilt through its normal constructor; requests also pass through
`freeze_provider_request`, and authorization snapshots retain their exact
advertised-tool validation.

The flat arena keeps arbitrary product JSON nesting independent of Python's
recursion limit. The existing ToolDefinition schema constructor and request
freezer retain their own recursive validation boundary: excessively deep tool
schemas are refused with a codec error. This is not a fixed-depth cutoff on
product JSON, details or structured results.

Only exact builtin containers/scalars, the explicitly registered native DTOs,
the request freezer's exact immutable schema mapping, native platform `Path`,
and exact naive or fixed-offset `datetime` values are traversed. Foreign
subclasses, arbitrary mappings, custom timezone code and arbitrary objects are
refused without invoking their traversal/equality/hash protocols. Decoder
output detaches from the input tree and from every other decode. Repeated input
placements get independent owned copies, rather than shared mutable outputs.

Numbers follow `OwnedJson`: finite binary64 values, exactly representable
integers (including values above 64 bits), original int/float type and signed
zero. Strings and mapping keys use canonical lower-case UTF-8-with-surrogatepass
hex inside the transport tree. This preserves lone surrogates and the spelling
difference between a Python surrogate pair and one supplementary character
across an ordinary JSON text round trip. Mapping keys must still have distinct
UTF-16 identities, just as in `OwnedJson`. Mapping insertion order is retained.
Timestamps preserve microseconds, offset, fixed-offset timezone name and fold;
paths retain their canonical platform spelling without resolving filesystem
state. Decode refuses alternate datetime spellings and paths that the platform
would normalize; encoder-produced values always use the accepted spelling.

## Request and credential boundary

Requests preserve their ordered message cutoff, provider/model, working path,
turn index/label, frozen model-visible tool declarations and authorization,
ordered anchored system messages, hidden/undeclared tools, current attachments,
provider session and cache-retention identity. Decode freezes the tool catalog
through the existing canonical request boundary. API/thinking fields that exist
on assistant/provider results are retained there. This slice does not invent
request options or task-envelope identity fields missing from current DTOs;
DE06b and the generation integration own those durable contracts.

`provider_header_callback` must be `None`. A live callback is refused explicitly,
never invoked or silently omitted. Credentials are resolved privately at the
future invocation boundary, and secret header dictionaries have no supported
request field in this format. Semantic header callbacks are also refused: a
future reconstructable semantic representation is required before such a
request can be checkpointed. No callable or authentication object is serialized.
Legacy `tool_observation` inputs are outside the canonical agent request domain
and must be `None`.

A tool declaration contains `name`, `description`, `input_schema` and
`schema_mode`. The other ToolDefinition fields must retain their normal defaults:
exposure `direct`, default activation true, empty prompt guidelines and all
other metadata/hooks absent. Registered tool namespaces, annotations, output
schemas, exposure/activation changes, execution modes, snippets/guidelines and
reach/loadout hooks are explicit refusals. Callers must use the existing
model-visible request projection first; this codec never silently drops
nondefault semantic registration fields. Durable tool registry identity and
recovery compatibility are later contracts, not an implicit promise here.

This is full-content product data, including image bytes and tool details, and
must stay in the future private product store. It must not enter the separate
metadata-only workflow archive. Finite JSON payloads are not automatic secret
redaction: callers must keep credentials out of arbitrary user/tool content.
Codec errors report only fixed structural messages and never quote stored data.

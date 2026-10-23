# Internal canonical DTO codec

## DE06b integration contract

Canonical checkpoints are an opt-in adapter over the existing typed tasks and
`TaskRuntime.commit`; generic finite-JSON tasks keep their contract. A closed
outer version/phase/role/definition identity wraps the unchanged canonical arena.
The phase adapter validates the whole checkpoint and exact DTO role before
calling a handler. Encoding owns nested inputs before suspension. Recovery uses
the committed envelope, never a borrowed candidate or current catalog defaults.
Terminal results remain readable without credentials or dispatch.

Request semantics come from the existing construction policy: API/model,
thinking, transcript/cache compatibility, native transport options and saved
pricing. Configured headers, source expressions and unclassified endpoint/routing
values live only in an auth-owned private companion, under the existing auth-file
lock. Session stores immutable random references, never private values or their
hashes. Material is durable before reference adoption; missing/corrupt/divergent
material refuses before effects. Orphans are retained conservatively. No second
Session writer or mutable AuthStore on the scheduler thread is introduced.

Known API/OAuth authentication resolves through the existing live authority and
may rotate. Configured arbitrary header sources/values must retain their captured
semantics on reissue; ambiguous drift refuses, without guessing roles from names.
IAM requests additionally bind the actual Bedrock region or Vertex target and
Express/ADC mode in protected material. The same pure environment-default helpers
feed adapter construction and compatibility checks; signing keys/access tokens
stay live. Target drift refuses before transport. No global environment mutation
is used to reconstruct a saved request.
Live header callbacks are explicitly refused without execution. Provider adapter
header ordering stays authoritative. Diagnostics are fixed and payload-free,
including exceptions that the scheduler may persist as a fault.

DE06b proves actual task transitions and process recovery with fixed fixtures;
generation dispatch, unsafe tool effects, partials, broad extension hooks and the
full usage ledger remain with their original later slices. This contract does
not itself claim passing acceptance or close any P row.

DE06a supplies `native.durable.canonical_codec.encode_canonical` and
`decode_canonical`, the private finite DTO arena. DE06b activates it through
`checkpoint_codec` on real versioned tasks, with `provider_settings`,
`provider_inputs` and `provider_checkpoint` supplying owned request semantics
and reconstruction. These remain internal APIs: product defaults, AgentLoop and
CLI execution do not switch to durable generation. See the
[acceptance mapping](specs/2026-10-19-durable-execution-evidence.md#de06b-canonical-checkpoint-acceptance).

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
never invoked or silently omitted. Credentials resolve privately through the existing auth precedence and OAuth
refresh owner. Header dictionaries have no field in the canonical request arena;
configured literal/env/command headers instead use the auth-owned protected
material reference described below. Semantic header callbacks remain refused:
there is no reconstructable durable hook identity yet. No callable or
authentication object is serialized.
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

## Checkpoint and reconstruction APIs

`encode_checkpoint` synchronously owns the identity, payload arena and optional
prepared provider settings before queueing or suspension. `decode_checkpoint`
requires the expected task name, finite numeric definition version, actual phase
and role. Envelope version1 is separate from task and canonical codec versions.
Roles are prepare (no payload), request (exact request snapshot), provider outcome
and tool outcome. Identity contains attempt/request anchors and a concrete entry
cutoff. Missing/newer/unknown fields, wrong root DTOs or phase-role combinations
fail with fixed diagnostics.

`define_canonical_task` returns an ordinary captured Task token whose phase
wrappers validate before calling a typed handler. Initial input is already an
encoded envelope; initialization does not dispatch. `commit_checkpoint` owns its
candidate synchronously and delegates coupled entries/documents and running or
terminal state to the existing TaskRuntime commit. Memos, invocation end,
reservation acknowledgement, error sealing, close and backend ownership remain
B's runtime authority. Generic finite-JSON tasks are unaffected. A caller must
use the persisted memo winner when allocating a stable private material ID.

`prepare_provider_settings` owns the request and selected construction inputs,
then invokes the existing provider-construction policy outside the Session line.
It captures requested/resolved thinking, API/model/provider, transcript/cache
compatibility, native transport/retry/timeout options and saved pricing rates and
tiers. The canonical request separately retains session/cache, history, tools,
attachments and system anchors. Unsupported requested profiles fail explicitly;
unknown values do not acquire guessed defaults. Native request options are not a
claim of every upstream stream option or deferred-handle support.

`auth_material` is part of the existing provider-auth authority. Its private
`auth.json.request-material` companion uses the same auth-file lock, strict
versioned parsing, immutable random store/material IDs, file600/directory700 and
atomic file replacement with file/directory fsync. This is local permission
protection, not encryption. It neither adds synthetic providers to auth.json nor
changes OAuth's credential schema. Concurrent credential merges cannot overwrite
material records. Missing/corrupt/binding-mismatched records refuse; capture
before Session adoption may leave harmless retained orphans. No material GC is
implemented.

`reconstruct_provider` checks the saved profile and bound material, resolves live
credentials with a detached credential lookup and the existing OAuth cache, and
builds through the existing construction owner. Arbitrary configured header
values/selectors and sensitive endpoints/routing are compared only privately;
ambiguous drift refuses before provider transport. Known auth rotation is live,
including authHeader regeneration and OAuth-derived endpoint renewal. Runtime
key bindings must still be supplied on recovery. Nothing infers auth from custom
header names. Saved pricing wins changes to catalog prices.

All current catalog API families are represented: completions, responses, Azure,
Anthropic, Google, Mistral, Cloudflare, Bedrock, Vertex, Codex and deterministic
fake. Bedrock/Vertex use shared adapter-owned environment-default helpers and an
optional detached environment input; no global environment is changed. Their
exact region/project/location and Express/ADC target mode live in protected
material, while signing keys/access tokens remain live. This does not add a
previously absent AWS credential-chain or Google JWT/ADC implementation.
Codex static configured headers now reach its adapter; its existing callback
ordering is retained, with live Authorization/account headers overwriting custom
values. Live callbacks, extension provider factories and unsupported option
capabilities remain explicit refusals, with their original later-slice owners.
Legacy selection-to-profile/product factory wiring remains DE11/12.

## Executed observables and representation accounting

The `canonical-checkpoint-b-v1` comparison runs genuine pinned Harness tasks
through typed creation, phase commits, terminal settlement and SQLite close/reopen.
A frozen common rich-value recipe is an input, not a captured native trace.
Native tasks construct their DTOs, persist canonical envelopes and project actual
decoded dataclass fields, including equality-excluded fields. Field-loss controls
cover every registered schema field;32 domain controls exercise actual CLI0/1,
and a malformed outer trace exercises CLI2. Independent native checks retain
original scalar types, sentinel/null/presence, timestamp offset/fold/name and
signed-zero bits in actual committed arenas.

The common numeric profile is
`finite-binary64-live-v1/persisted-json-zero-v1`: finite int/float values use the
shared binary64 number profile; bool is distinct. Actual pinned SQLite task
results serialize negative zero as positive zero, while both live phase
observations retain its sign. The Python arena intentionally retains signed zero
and original numeric types under the DE06a contract. Only terminal/reopened
common views project negative zero to positive zero. `native-exact.json` and the
unmodified Pi observations retain this source-observed difference; live views,
None, missing/unset markers, every field and substantive numeric value remain
untouched. This is declared representation accounting, not raw result equality
or a product numeric change.

Native private-material/fake-wire tests separately cover static header layering,
API/OAuth rotation, actual signed Bedrock and Vertex ADC/Express requests, Codex
SSE/auth overwrite, target/source drift, malformed refs, concurrent auth writes,
and callback refusal with zero callback calls. Real SQLite process barriers cover
material-before-adoption, request/outcome/terminal commits and acknowledgement
loss. They do not claim DE07 generation execution, DE08 tool replay safety,
DE09 publication/reconnect, DE17 retries or DE26 durable partials. All43 P rows
remain OPEN and retain their complete original owners.

### Detached adapter defaults

Reconstruction binds the effective Azure base URL, deployment and API version,
Responses/Anthropic/Bedrock cache-retention default, and Bedrock force-cache
eligibility in protected material. Shared adapter resolvers compute these from
selected detached inputs. Constructed adapters retain only the relevant copied
environment selectors, so later ambient changes cannot reroute or recache an
already prepared request. Recovery compares the current detached effective
values before transport; credential rotation remains live. No environment dump
is stored in the checkpoint or private material.

Codex uses the existing dedicated `openai-codex.json` credential store beside the
selected `auth.json`, through its existing refresh manager. Configured and
callback header names merge case-insensitively before live auth/account fields;
mandatory protocol headers likewise have one spelling on either transport.

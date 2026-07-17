# Anthropic Cache-Friendly Dynamic Tool Loading Design

Status: parity-loop design for one bounded provider-owned slice

## Scope

Add Pi's message-anchored dynamic-tool load point to pipy's provider-agnostic
tool-result envelope and consume it in the first-party Anthropic Messages
adapter. When an extension tool changes the active set by only adding registered
tools during its own execution, the next supported Anthropic request keeps the
new definitions out of the stable immediate-tool prefix, marks those definitions
with `defer_loading`, and inserts `tool_reference` blocks at that tool result.

This slice does not add OpenAI Responses `tool_search_call` /
`tool_search_output`, Kimi Chat Completions deferred tools, a search algorithm,
or a new extension registration API. Extensions continue to register tools and
use the existing `ctx.set_active_tools(...)` control. Package-update realignment
and broader extension UI remain separate gaps.

Pi reference (local checkout `/Users/jochen/src/pi-mono` at `c8560b8d`):

- `packages/coding-agent/src/core/extensions/wrapper.ts:16-38` snapshots active
  tools around one extension-tool execution and records only a purely additive
  change.
- `packages/agent/src/agent-loop.ts:775-787` transfers non-empty
  `addedToolNames` onto the provider-visible tool-result message.
- `packages/ai/src/types.ts:400-416` owns the provider-agnostic optional marker.
- `packages/ai/src/utils/deferred-tools.ts:1-43` splits the current tool list
  from message history, ignores missing definitions, and keeps a tool immediate
  when it was used before its marker.
- `packages/ai/src/api/anthropic-messages.ts:179-200,926-949,1054-1086,
  1089-1230` owns compatibility detection, deferred definitions, reference
  placement, ordinary-result preservation, and consecutive-result grouping.
- `packages/ai/test/deferred-tools.test.ts:179-335` pins the Anthropic request
  matrix.

## Provider-agnostic load-point contract

`ToolResultMessage` gains `added_tool_names: tuple[str, ...] = ()`. It contains
registered tool names that became active during the extension tool represented
by that result. It is summary-safe tool metadata, not tool output. Native
session JSONL persists the names with the result so `/resume`, provider
switches, and later requests retain the load point; old records without the
field decode to an empty tuple. The marker does not enter the metadata-first
`pipy-session` archive or diagnostics.

The product loop wires `ctx.set_active_tools(...)` into extension-tool handler
contexts. Immediately around that handler invocation, it snapshots the ordered
active definitions. It records newly active names only when every name active
before execution remains active afterward. A removal or replacement still
changes the active set but records no marker, forcing every provider to use its
existing safe full-tool-list fallback. Handler failures record no marker.
Changes made by tool-call or tool-result hooks are outside this snapshot,
matching Pi's extension-tool wrapper ownership.

Only names that survive the existing registered-tool validation can be marked.
The marker order follows the active definition/registry order and is unique.
All non-Anthropic providers ignore the field and continue sending their normal
current active tool list, so dynamic activation remains functional without
claiming cache preservation.

## Anthropic compatibility resolution

The provider construction boundary resolves one Boolean
`supports_tool_references` value. Pi's `compat.supportsToolReferences`
optionality and precedence are pinned as follows:

- An explicit Boolean `model.compat.supportsToolReferences` wins, including
  explicit `false`. This allows a verified custom Anthropic-compatible endpoint
  to opt in and a built-in row to opt out.
- Otherwise support is true only when `provider_name == "anthropic"`, the model
  id does not contain `haiku`, and the id matches
  `^claude-(opus|sonnet|fable)-(major)(-minor)?(-|$)` at Claude 4.5 or newer.
- An eight-digit date-like second numeric component is not a minor version; it
  resolves as minor zero, matching Pi. Claude 3.x, Opus/Sonnet 4.0 and 4.1,
  Haiku, unrecognized ids, and non-first-party providers default false.

There is no upstream API default to inherit: the forced false cases omit both
`defer_loading` and client-authored `tool_reference`; the forced true cases use
those native fields only when the history contains a valid additive load point.
No derived identifier is introduced. Pipy has no Anthropic OAuth name rewrite,
so Pi's Claude-Code tool-name canonicalization is not part of this adapter
slice.

## Anthropic request ownership and exact shape

Before building the request, the adapter deduplicates current
`available_tools` by name and scans messages in order:

1. Assistant tool calls add their names to `used_names`.
2. A result marker adds a name to the deferred set only when that name was not
   used earlier.
3. Only definitions still present in current `available_tools` can be deferred;
   a stale marker never resurrects a removed tool.
4. Immediate definitions serialize exactly as today. Deferred definitions use
   the same `name`, `description`, and `input_schema` fields plus the forced
   literal `defer_loading: true`.
5. If the split would leave no immediate tool, every definition stays immediate
   and no references emit, matching Pi's at-least-one-immediate fallback.

At each marked result, emit one `{type: "tool_reference", tool_name: <name>}`
for a current deferred definition not already loaded by an earlier result. An
Anthropic `tool_result` cannot mix reference blocks with ordinary result text,
so a reference-bearing result uses the reference list as its `content` and its
original bounded `output_text` becomes a sibling `{type: "text", text: ...}`
block. Consecutive tool results are grouped into one user message: all
`tool_result` blocks first, followed by displaced sibling text in result order.
Unmarked, stale, duplicate, unsupported, or fallback markers preserve the
ordinary tool-result shape and text.

The marker is reconstructed from complete conversation history on every
request; no mutable provider cache or one-shot consumption state is added.
Cancellation, request headers, thinking, attachments, usage, and response
parsing are unchanged.

## Tests and objective gate

Focused tests will prove:

- extension-tool execution can call `ctx.set_active_tools(...)`; a purely
  additive change marks the matching successful result, while replacement and
  handler failure do not;
- session-tree round-trip preserves ordered markers and old records without the
  field still load;
- supported first-party 4.5+ Anthropic models defer a current late tool and
  place its reference at the marked result while preserving ordinary output;
- prior use, missing current definitions, duplicate markers, and an all-deferred
  set stay immediate or otherwise avoid invalid references as Pi specifies;
- unsupported Haiku/old/custom defaults use the normal full list, and explicit
  true/false compat overrides win independently;
- consecutive tool results keep Anthropic-valid grouping and output ordering;
- another provider ignores the marker and receives the complete current active
  set.

Extend the extension conformance surface with a deterministic dynamic-tool row
that exercises the actual extension handler and provider request boundary. The
existing extension package/live-session gates remain green.

## Done when

- Additive extension-tool activation creates a durable provider-agnostic load
  point without changing removal/replacement fallback behavior.
- Supported Anthropic requests use Pi's exact deferred-definition/reference
  placement and unsupported requests remain byte-shape compatible apart from
  intentional consecutive-result normalization.
- Focused tests and the extension conformance gates pass.
- Extension docs, parity docs, backlog/audit, and release notes mark only the
  Anthropic slice shipped and retain OpenAI/Kimi as explicit follow-ons.
- `just check` passes and a fresh direct Claude-family review returns CLEAN over
  the exact complete diff committed on `main`.

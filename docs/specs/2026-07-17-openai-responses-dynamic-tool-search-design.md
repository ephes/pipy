# OpenAI Responses Dynamic Tool Search Design

Status: parity-loop design for one bounded provider-owned slice

## Scope

Add Pi's OpenAI Responses client-side dynamic-tool placement on top of pipy's
shipped durable `ToolResultMessage.added_tool_names` load point. Supported
OpenAI Responses and OpenAI Codex Responses models keep definitions added
during an extension-tool result out of the stable top-level `tools` prefix and
insert a completed client `tool_search_call` plus matching
`tool_search_output` immediately after that result's
`function_call_output`.

This slice does not add Kimi Chat Completions deferred tools, a tool-search
algorithm, new extension registration APIs, package-update realignment, or
broader extension UI. It does not refresh unrelated catalog rows or change
response parsing, transport, retries, auth, signing, headers, reasoning,
attachments, or usage.

Pi reference (local checkout `/Users/jochen/src/pi-mono` at `c8560b8d`):

- `packages/ai/src/utils/deferred-tools.ts:1-43` owns the ordered current-tool
  split, explicit enable switch, stale-marker filtering, and prior-use rule.
- `packages/ai/src/api/openai-responses.ts:62-72,230-267` resolves
  `supportsToolSearch`, passes deferred definitions into message conversion,
  and sends only immediate definitions in top-level `tools`.
- `packages/ai/src/api/openai-codex-responses.ts:480-528` applies the same split
  to the Codex Responses body.
- `packages/ai/src/api/openai-responses-shared.ts:90-101,130-298,304-320`
  owns load placement, deduplication, the derived call id, and the deferred
  Responses tool shape.
- `packages/ai/src/utils/hash.ts:1-13` defines the exact `shortHash` algorithm.
- `packages/ai/src/providers/openai.models.ts:495-665` and
  `packages/ai/src/providers/openai-codex.models.ts:1-130` pin model opt-ins.
- `packages/ai/test/deferred-tools.test.ts:391-451` pins the OpenAI/Codex
  supported and unsupported request matrix.

## Compatibility resolution

The provider construction boundary resolves one Boolean
`supports_tool_search` field for the two Responses adapters. Pi's
`model.compat.supportsToolSearch` contract is optional and independent:

- An explicit Boolean `compat.supportsToolSearch` wins, including explicit
  `false`; non-Boolean values are not opt-ins.
- Missing support defaults to `false`. There is no provider/base-URL detector
  and no upstream API default to inherit.
- Pipy built-in rows opt in only where the same Pi row opts in. Within pipy's
  current catalog that is `openai/gpt-5.4`, `openai/gpt-5.5`,
  `openai-codex/gpt-5.4`, `openai-codex/gpt-5.5`, and
  `openai-codex/gpt-5.6-sol`. The current `openai/gpt-5.1-codex`,
  `openai/gpt-4o*`, and `openai-codex/gpt-5.1-codex` rows remain false.
- `models.json` provider/model compat merging and fallback-row inheritance
  already preserve this field. OpenAI Responses receives the resolved value
  through catalog construction. The legacy-constructed Codex adapter receives
  the selected row's value through `NativeReplProviderState`, alongside its
  existing catalog-derived reasoning effort.

Direct adapter construction defaults false so tests and non-catalog callers do
not claim support accidentally; they may explicitly opt in for a verified
model. With support false, all current definitions remain in top-level `tools`
and no search items emit, exactly preserving today's safe behavior.

## Tool split and placement

The provider-agnostic split is shared with the Anthropic slice and reconstructed
from the complete request every time:

1. Deduplicate current `available_tools` by name with last definition winning
   and stable first-insertion position, matching Pi's `Map` behavior.
2. Scan messages in order. Assistant calls add their tool names to `used_names`;
   result markers add names to the deferred set only if the name has not already
   been used. A malformed Responses result without the provider correlation
   needed to derive its search id cannot be a load point, so its names stay
   immediate; Anthropic's independent reference placement does not need this
   additional guard.
3. Only definitions still present in current `available_tools` can be deferred.
   Stale markers never resurrect removed definitions.
4. Top-level `tools` contains only immediate definitions. Unlike Anthropic,
   OpenAI has no at-least-one-immediate fallback: an all-deferred set omits
   top-level `tools` and is loaded from the transcript.
5. Each `ToolResultMessage` first emits its unchanged
   `function_call_output`. Its ordered marker names then select current deferred
   definitions not loaded by an earlier marker in the same request. Duplicate,
   stale, already-used, unsupported, or already-loaded names add nothing.
6. A non-empty selection appends exactly two adjacent input items after the
   result: one completed client search call and its completed client output.
   Multiple marked results receive independent pairs at their original history
   positions.

The search-call fields changed by this slice are complete and exact:

```json
{
  "type": "tool_search_call",
  "call_id": "pi_tool_load_<shortHash>",
  "execution": "client",
  "status": "completed",
  "arguments": {"query": "<space-joined ordered names>", "limit": 1}
}
```

`limit` equals the number of definitions in that load event. The paired output
uses the same `call_id` and exact outer fields:

```json
{
  "type": "tool_search_output",
  "call_id": "pi_tool_load_<shortHash>",
  "execution": "client",
  "status": "completed",
  "tools": [
    {
      "type": "function",
      "name": "late_tool",
      "description": "...",
      "parameters": {},
      "strict": false,
      "defer_loading": true
    }
  ]
}
```

The deferred definition's `strict: false` is Pi's forced conversion default,
not an upstream default. `defer_loading: true` is also forced. Ordinary
top-level tool fields outside the placement change retain pipy's existing
serializer shape in this slice; broader strict-mode request parity is a
separate provider-field concern.

## Derived identifier

The search id is
`pi_tool_load_${shortHash(seed)}`. The seed is the full provider correlation id
stored on the marked result, followed by `:`, followed by selected tool names
joined with commas. For Codex, the full correlation includes both Responses
identifiers (`call_id|item_id`); only the preceding `function_call_output`
continues stripping the item-id suffix as today. The correlation is already
required by both Responses serializers, so no fallback id is synthesized.

`shortHash` is ported exactly from Pi: two 32-bit `Math.imul` accumulators over
JavaScript UTF-16 code units, two final mixing rounds, then unsigned base-36
`h2` plus `h1`. Known parity vectors include:

- `call_abc:late_tool` -> `1o0l89w1i7wxtx`
- `call_abc|fc_abc:late_tool` -> `xvuydyik9a48`
- `call_loader:late_tool,later_tool` -> `dulyo1k6qd28`

The id is deterministic across retries and provider reconstruction; no mutable
provider cache or one-shot consumption state is introduced.

## Tests and documentation

Focused tests will prove:

- the Pi hash vectors and full call-id construction, including Codex's combined
  correlation id;
- supported OpenAI and Codex requests keep a base tool top-level, place the
  result before the paired search items, preserve ordered query/limit and emit
  exact deferred-definition fields;
- prior use, stale definitions, duplicate markers, repeated load points, and
  all-deferred tool sets follow Pi's matrix;
- explicit true/false compat resolution and the built-in supported/unsupported
  model rows are independent for both Responses families;
- unsupported direct adapters and non-Responses providers retain the full
  current list and omit search items;
- retry/WebSocket-to-SSE reuse keeps the same prebuilt body and derived ids.

Update provider/extension docs, parity docs, backlog/audit, user documentation,
architecture notes where ownership is described, and release notes. Mark only
the OpenAI Responses tool-search slice shipped; retain Kimi and other follow-ons.

## Done when

- Supported OpenAI Responses and Codex Responses requests use Pi's exact
  immediate/deferred split and client tool-search placement.
- Unsupported models and providers keep the safe complete current-tool list.
- Focused provider, construction/catalog, extension, and retry tests pass.
- Docs and release notes match the shipped behavior and strike this gap from
  the ranked source without broadening the slice.
- `just check` passes and a fresh direct Claude-family review returns CLEAN over
  the exact complete diff committed on `main`.

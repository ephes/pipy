# COST1 — Catalog-cost pricing for every model (plan)

Reference: Pi `~/src/pi-mono` at `4df157433`. Gap source: `docs/backlog.md`
item 11 (found during PC2).

## Backlog claims, verified

- pipy prices a session only through `repl/turn_leaves.py` `PRICING_TABLE` /
  `pricing_for`, which has one entry, `("openai-codex", "gpt-5")`, prefix-matched.
  Every other row (all Anthropic rows, gpt-6-sol, all API providers) prices at
  zero, even though each `NativeModelSpec` carries `NativeModelCost`. Verified:
  all Anthropic and Codex GPT-5.5+/6 rows carry non-zero Pi costs.
- The table entry also double-charges: `reasoning_per_million=10` prices
  `reasoning_tokens` on top of `output_tokens`, which already includes them for
  every pipy provider that reports reasoning (Responses, Codex, Azure,
  completions, OpenRouter). Cached input is charged at the full input rate
  because the sample's `input_tokens` is inclusive for OpenAI-style APIs.
- Pi has no special case equivalent to the table. Cost always comes from the
  model object.

## What Pi does

### `calculateCost` (`ai/src/models.ts:1187-1207`)

```ts
const inputTokens = usage.input + usage.cacheRead + usage.cacheWrite;
let rates = model.cost; let matchedThreshold = -1;
for (const tier of model.cost.tiers ?? [])
  if (inputTokens > tier.inputTokensAbove && tier.inputTokensAbove > matchedThreshold)
    { rates = tier; matchedThreshold = tier.inputTokensAbove; }
const longWrite = usage.cacheWrite1h ?? 0; const shortWrite = usage.cacheWrite - longWrite;
cost.input = rates.input/1e6*usage.input; cost.output = rates.output/1e6*usage.output;
cost.cacheRead = rates.cacheRead/1e6*usage.cacheRead;
cost.cacheWrite = (rates.cacheWrite*shortWrite + rates.input*2*longWrite)/1e6;
```

Every adapter calls it with the request's model object after normalizing usage
(`openai-responses-shared.ts:563-578`, `openai-completions.ts:1522-1550`,
`anthropic-messages.ts:615-626`, `google-*.ts`, `bedrock-converse-stream.ts:711-718`,
`mistral-conversations.ts:608-613`). The request's model object already has
`models.json` overrides applied, so user cost overrides price the session.

Pi `Usage` semantics the formula relies on:

| Field | Meaning |
| --- | --- |
| `input` | uncached prompt tokens. OpenAI Responses/Codex/Azure: `input_tokens - cached - cache_write`; completions: `max(0, prompt - cacheRead - cacheWrite)`; Anthropic/Bedrock: `input_tokens` as reported (already separate). |
| `output` | all output tokens, reasoning included (`completion_tokens`/`output_tokens` already contain reasoning). Reasoning is never priced separately. |
| `cacheRead`, `cacheWrite`, `cacheWrite1h` | as reported; `cacheWrite1h` is the 1h part of `cacheWrite` |

### `ModelCost` (`ai/src/types.ts:1055-1070`, `coding-agent/src/core/model-config.ts:125-138,197,212-219`)

| Field | Optionality | Notes |
| --- | --- | --- |
| `input`, `output`, `cacheRead`, `cacheWrite` | required on a model / custom `models.json` model | $/million |
| `tiers[]` | optional | each `{inputTokensAbove, input, output, cacheRead, cacheWrite}`, all required |
| override `cost.*` | each optional | `provider-composer.ts:173-181`: each rate `override ?? model`, `tiers: override.cost.tiers ?? model.cost.tiers` (replaced wholesale, not merged) |

Custom models without `cost` default to all-zero (`provider-composer.ts:222`).

### Footer cost segment (`coding-agent/.../components/footer.ts:189-196`)

```ts
const usingSubscription = state.model
  ? state.model.provider === "kimi-coding" || modelRuntime.isUsingSubscription(state.model.provider)
  : false;
if (usageTotals.cost || usingSubscription)
  statsParts.push(`$${usageTotals.cost.toFixed(3)}${usingSubscription ? " (sub)" : ""}`);
```

- No cost and no subscription: the segment is omitted. There is no `(api)`
  label anywhere in Pi.
- `isUsingSubscription` (`model-runtime.ts:534-540`) = the stored credential for
  the provider has `type === "oauth"` AND the provider's OAuth definition has
  `isSubscription === true`. Built-in subscription OAuth providers: anthropic,
  openai-codex, github-copilot, xai, meta, kimi-coding (`ai/src/providers/*.ts`).
  Extension OAuth providers declare `oauth.isSubscription`
  (`extensions/types.ts:1911-1914`).
- A subscription still shows the **computed** cost (openai-codex rows carry Pi
  API-equivalent rates), marked ` (sub)`. Pi does not zero the cost.

### RPC `get_session_stats` (`agent-session.ts:4082-4133`, `SessionStats` at 320-338)

`tokens: {input, output, cacheRead, cacheWrite, total}` with
`total = input + output + cacheRead + cacheWrite` (Pi-normalized `input`, i.e.
uncached), and `cost: number` (sum of message `cost.total`).

## pipy change (ownership)

1. **Pricing value** (`agent/usage.py`, dependency-neutral agent tier).
   `AgentTokenPricing` becomes Pi `ModelCost`: `input_per_million`,
   `output_per_million`, `cache_read_per_million`, `cache_write_per_million`,
   `tiers: tuple[AgentTokenPricingTier, ...] = ()`. `reasoning_per_million` is
   removed (no-deprecation policy; Pi never prices reasoning separately).
   `AgentTokenPricingTier` carries `input_tokens_above` plus the four rates.
   Every reader of the removed field moves with it, including
   `agent/loop.py` `_revalidate_pricing`, which revalidates tiers too.
2. **Per-turn cost** (`agent/usage.py` `_turn_cost`). Pi-normalize the sample
   first, reusing the accumulator's existing `_cache_counters_are_separate`
   decision (Anthropic/Bedrock synthesize `total_tokens` = in+out+cache, so
   they are "separate"; OpenAI-style totals exclude a second copy of the cache
   counters, so they are "inclusive"; a missing total means inclusive). That
   check currently adds `reasoning_tokens` on top of `output_tokens`, counting
   reasoning twice, so a separate-cache sample that reports reasoning would be
   misread as inclusive. The check drops the reasoning term (reasoning is part
   of output for every pipy adapter); the OpenAI inclusive case is unchanged
   because `input + output + cache_read > total` whenever a cache read exists.
   The cache-hit percentage shares the fixed check. Test: a separate-cache
   sample with reasoning stays separate.
   - `uncached = input if separate else max(0, input - cache_read - cache_write)`
   - tier by `uncached + cache_read + cache_write` with Pi's strict `>` and
     highest-threshold rule
   - `cost = (uncached*in + output*out + cache_read*cr + short_write*cw + long_write*in*2) / 1e6`,
     where `long_write = min(cache_write_1h, cache_write)` (existing clamp for
     malformed samples) and `short_write = cache_write - long_write`.
   The accumulator also keeps an `uncached_input_tokens` counter with the same
   per-sample rule, so RPC stats report Pi `tokens.input`.
3. **Catalog cost tiers** (`catalog.py`, not `catalog_data.py`).
   `NativeModelCost` gains `tiers: tuple[NativeModelCostTier, ...] = ()`.
   Built-in rows keep `()` until catalog data carries tiers (MC5 owns
   `catalog_data.py`). `models.json` parses `cost.tiers` on custom models and
   overrides; override tiers replace the row's tiers wholesale, as in Pi.
4. **Row → pricing** (`repl/turn_leaves.py`). `PRICING_TABLE` is deleted.
   `pricing_for(provider_state, provider_name, model_id)` resolves the row the
   session uses — `NativeReplProviderState.model_runtime.resolve_spec(...)`
   (built-in + `models.json` + fallback row), or the built-in catalog row for a
   non-catalog (injected/static) state — and maps its `NativeModelCost` to
   `AgentTokenPricing`. No row → `None` (zero cost). Every existing call site
   (startup, `/model`, login/logout rebind, reload fallback, extension-startup
   fallback, the agent-loop run lookup) passes its provider state.
5. **Subscription predicate** (`catalog_state.py` / `repl_state.py`).
   `ProviderCatalogState.is_using_subscription(provider)`: built-in OAuth ids
   (`get_oauth_provider_ids()`: anthropic, github-copilot, openai-codex — all
   Pi `isSubscription: true`) with a stored `type: "oauth"` credential; for
   openai-codex also pipy's Codex OAuth file (its only auth path); an extension
   OAuth provider with a stored oauth credential and `oauth.is_subscription`.
   `ExtensionOAuthConfig` gains `is_subscription: bool = False` (Pi
   `oauth.isSubscription`). `NativeReplProviderState` exposes it.
6. **Footer** (`chrome.py`). `BottomStatusFields.plan_label` is replaced by
   `using_subscription: bool`; the cost segment follows Pi exactly: omitted when
   cost is zero and not subscription, `$x.xxx` for API cost, `$x.xxx (sub)` for
   subscription; `kimi-coding` counts as subscription as in Pi. A static/injected
   provider state is never a subscription.
7. **RPC `get_session_stats`** (`automation/rpc.py`). Replace the zero
   placeholders with the live session accumulator through a new immutable
   `usage_snapshot()` on `RpcProviderConfigurationPort`: `tokens.input` =
   uncached input, `output`, `cacheRead`, `cacheWrite`, `total` = their sum,
   `cost` = accumulated cost. These are the same live totals the footer shows.
   Their lifetime is pipy's current contract, not Pi's: pipy's accumulator is
   live-only and is replaced on a model bind (see Deviations), so the stats
   cover the turns since the last model bind in this process. Pi sums persisted
   per-message usage over the whole session file. Closing that needs
   per-message usage persistence and a change to the documented model-switch
   reset (`docs/architecture.md`: "Model switches retain the existing
   coding-history and usage reset"), which is its own slice (USAGE1, recorded in
   the backlog).
8. **Google usage normalization** (`providers/google_generate_content_wire.py`).
   Pi (`google-generative-ai.ts:232-241`, `google-vertex.ts:240-249`) prices
   Gemini with `output = candidatesTokenCount + thoughtsTokenCount`,
   `cacheRead = cachedContentTokenCount`, `input = promptTokenCount - cached`.
   pipy maps only prompt/candidates/total today, so thinking output and cache
   reads are unpriced. One shared `extract_google_usage` maps
   `promptTokenCount → input_tokens` (inclusive; step 2 subtracts the cache),
   `candidates + thoughts → output_tokens`, `thoughts → reasoning_tokens`,
   `cachedContentTokenCount → cached_tokens`, `totalTokenCount → total_tokens`
   for both Google adapters, replacing the two `*_USAGE_FIELDS` tuples. With a
   cache read the inclusive test holds (`total = prompt + candidates + thoughts`
   is below the separate-counter sum).
9. **Other inclusive-cache adapters** (added during implementation, same
   reason as step 8: without the cache counters a cached prompt is priced at
   the full input rate). Chat Completions (`openai_completions`, `openrouter`,
   `cloudflare`) read Pi `parseChunkUsage` (`openai-completions.ts:1522-1550`):
   cache read `prompt_tokens_details.cached_tokens ?? prompt_cache_hit_tokens ??
   cached_tokens`, cache write `prompt_tokens_details.cache_write_tokens`,
   reasoning `completion_tokens_details.reasoning_tokens`. Mistral reads Pi
   `getMistralCachedPromptTokens` (prompt-details cached counters, then
   `num_cached_tokens`, clamped to `[0, prompt]`). Responses/Codex/Azure also
   read `input_tokens_details.cache_write_tokens`
   (`openai-responses-shared.ts:563-576`). Top-level OpenRouter
   `reasoning_tokens` is no longer read (Pi does not read it).

## Implementation order

1. `agent/usage.py` pricing value, tiers, Pi cost, fixed separate/inclusive
   check, `uncached_input_tokens`; `agent/loop.py` revalidation. Tests in
   `test_native_agent_usage.py`.
2. `catalog.py` tiers; `models_json.py` tier parsing and override merge. Tests in
   `test_native_models_json.py`.
3. `turn_leaves.py` `pricing_for(provider_state, …)` and every call site.
4. Subscription predicate (`catalog_state.py`, `repl_state.py`,
   `extension_types.py`, `provider_normalization.py`) and the footer.
5. RPC stats via `RpcProviderConfigurationPort.usage_snapshot()`.
6. Adapter usage normalization (steps 8-9).
7. Existing test expectations that encoded `(api)`, the zero `$0.000` segment,
   `reasoning_per_million`, or the removed field tuples; new
   `test_native_catalog_pricing.py`.
8. Docs, CHANGELOG, backlog.

## Tests

- `_turn_cost`/accumulator: Anthropic-style separate sample; OpenAI-style
  inclusive sample (cached subtracted, not double-charged); reasoning not
  charged on top of output; 1h split; tier selection (below, equal = not
  above, above, two tiers pick highest matching); `uncached_input_tokens`.
- `pricing_for`: built-in Anthropic row priced; `models.json` override cost and
  tiers win; unknown row → `None`; static state uses the built-in row.
- models.json: custom `cost.tiers` parse + validation errors; override tiers
  replace row tiers; partial override keeps row tiers.
- Footer: omitted at zero cost (api); `$0.123`; `$0.000 (sub)` and
  `$0.123 (sub)` for subscription; `kimi-coding`.
- Subscription predicate: codex file, anthropic oauth vs api_key credential,
  extension oauth with/without `is_subscription`.
- RPC `get_session_stats` returns the accumulator's tokens and cost.
- Google: a `usageMetadata` with thoughts and cached content maps to Pi's
  buckets for both adapters and prices thoughts at the output rate and cached
  content at the cache-read rate.
- Update the existing footer/golden assertions that encoded `(api)`/`(sub)`.

## Done when

- `PRICING_TABLE` is gone; every row prices from its resolved `NativeModelCost`
  with Pi's formula, including `models.json` overrides. Tier selection works for
  every row that carries tiers: `models.json` rows today, built-in rows once the
  catalog data carries Pi's tiers. Built-in tier data is a prerequisite owned by
  MC5 (`catalog_data.py`); until it lands, a built-in tiered model such as
  Codex gpt-5.5 prices above 272k input at its base rates, and the backlog
  states this.
- Gemini usage is normalized as in Pi (thoughts in output, cached content as
  cache reads).
- Footer and RPC stats show Pi-shaped cost; subscription providers show `(sub)`.
- Docs (`providers.md`, `provider-catalog.md`, `rpc.md`/`automation-rpc.md`,
  `harness-spec.md`, `extension-api.md`), CHANGELOG, and the backlog entry are
  updated. `just check` green; different-family review CLEAN.
- Live check: a tmux PTY run with the fake provider selected explicitly and one
  with a local stub provider that reports usage (cost visible), plus one tiny
  openai-codex turn showing `$x.xxx (sub)` if credentials work.

## Deviations and follow-ons (kept out of this slice)

- **USAGE1: per-message usage and session-wide totals.** pipy's canonical
  `AgentAssistantMessage` carries no usage, so `--mode json`/RPC message objects
  have no `usage`/`cost`, the session tree persists no usage, resumed sessions
  start at zero, and `/session` is pipy's one-line status rather than Pi's
  Session Info block with a Cost section. pipy's `/model`, login/logout rebind
  and reload fallback also replace the accumulator with the history, per the
  documented model-switch contract, while Pi keeps session-wide totals across
  models. Both need per-message usage persisted in the session tree and totals
  summed from it; that is one follow-on slice, recorded in the backlog.
- `/reload` refresh keeps the accumulator's pricing; a `models.json` cost edit
  applies on the next model bind, not mid-session.
- Footer token semantics (`↑` is raw input, `CH` is cumulative) stay as they
  are; Pi shows uncached input and the latest message's hit rate.
- Pi service-tier pricing (`applyServiceTierPricing`) and Anthropic
  `allowedFallbackModels` usage-model pricing: pipy has neither feature.
- Built-in catalog tiers (e.g. gpt-5.5 above 272k) arrive with catalog data
  (MC5); the pricing path already honors them.
- Pi subscription providers pipy lacks (xai, meta, kimi-coding OAuth) follow
  their provider slices.
- Extension-registered provider rows carry no cost (pipy's `ExtensionProvider`
  has model ids only; Pi's `ProviderModelConfig` carries `cost`), so they price
  at zero.

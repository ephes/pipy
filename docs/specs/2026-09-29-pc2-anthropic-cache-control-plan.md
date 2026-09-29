# PC2 — Anthropic `cache_control` and retention (plan)

Status: implemented (backlog item PC2, 2026-09-29). Pi reference: `~/src/pi-mono` at
`4df157433`, paths relative to `packages/`. tau reference:
`~/src/tau/src/tau_ai/anthropic.py` and `dev-notes/prompt-caching.md`.

## Backlog claims, verified

- "Pipy sends no `cache_control` today": true for both the Anthropic Messages
  adapter (`native/providers/anthropic_messages.py`) and the Bedrock InvokeModel
  adapter (`native/providers/bedrock.py`). Both send `system` as a bare string.
- "Parses cache-read and cache-write usage": true (`http.py`
  `extract_anthropic_usage`, `cache_creation_input_tokens` →
  `cache_write_tokens`, `cache_read_input_tokens` → `cached_tokens`). The 1h
  split (`usage.cache_creation.ephemeral_1h_input_tokens`) is not parsed.
- "`NativeModelCost.cache_write` exists": true, but **pipy never prices a turn
  from catalog cost**. Session cost comes from the hard-coded
  `repl/turn_leaves.py` `PRICING_TABLE` (openai-codex gpt-5 only). See
  "Cost" below.
- "Add a `cacheRetention` setting (5m/1h)": Pi has **no settings.json key** for
  this. Pi's coding agent reads only the `PI_CACHE_RETENTION` env var
  (`anthropic-messages.ts:60-68`, `bedrock-converse-stream.ts:816-824`); the
  request option `cacheRetention` is set programmatically (compaction passes
  `"none"`, `coding-agent/src/core/compaction/compaction.ts:653`). PC1 already
  added pipy's equivalent: `PIPY_CACHE_RETENTION` plus
  `ProviderRequest.cache_retention`, with compaction/branch summaries forcing
  `"none"`. PC2 reuses that plumbing; no new setting.
- "Bedrock/Vertex where supported": Pi has **no Vertex-Anthropic transport**
  (`anthropic-messages.ts:279` only mentions passing a pre-built
  `AnthropicVertex` client); pipy has no Vertex-Anthropic path either
  (`google_vertex.py` is Gemini). Bedrock is in scope.

## What Pi does (fields this slice changes)

Fields this slice changes; adjacent fields (thinking, betas, tool shapes,
temperature, metadata) are already matched or tracked elsewhere.

### Retention (shared by both adapters)

`resolveCacheRetention(cacheRetention, env)`: explicit option wins; else
`"long"` when env `PI_CACHE_RETENTION === "long"`; else `"short"`. Identical in
`anthropic-messages.ts:60-68` and `bedrock-converse-stream.ts:816-824`, and to
PC1's pipy `resolve_cache_retention` (`providers/openai_prompt_cache.py`),
which PC2 reuses as-is (`PIPY_CACHE_RETENTION`).

### anthropic-messages (`ai/src/api/anthropic-messages.ts`)

`getCacheControl` (`:70-84`): retention `none` → no cache control at all.
Otherwise `{type: "ephemeral"}`, plus `ttl: "1h"` only when retention is
`long` **and** `compat.supportsLongCacheRetention` (default `true`, `:210`).
Short retention sends no `ttl` (the API default 5m); Pi never sends `"5m"`.

Breakpoints (non-OAuth API-key path; pipy has no Claude Code OAuth path):

| Where | Pi rule | Source |
| --- | --- | --- |
| `system` | Sent only when the initial system text is non-empty, as `[{type: "text", text, cache_control?}]` (a block list even when retention is `none`, then without `cache_control`). Empty system text → no `system` key. | `:1102-1110` |
| `tools` | `cache_control` on the **last** tool of the list passed to `convertTools` (`index === tools.length - 1`), only when `compat.supportsCacheControlOnTools` (default `true`, `:213`). With native tool changes, only the initial (active) tools get it; the deferred placeholder and later `defer_loading` tools never do. | `:1119`, `:1130-1145`, `:1497` |
| messages | After conversion, if the **last** message has role `user` (or `system`) and its content is a list whose last block type is `text`, `image`, `tool_result`, `tool_addition` or `tool_removal`, set `cache_control` on that block. A string content is wrapped into one text block carrying it. Otherwise (e.g. last message is assistant) no message breakpoint. | `:1407-1432` |

Count: at most three breakpoints on the API-key path (system, last tool, last
user block) — under Anthropic's limit of four. Pi does not add more.

Session affinity (`createClient`, `:971-983`): when `cacheSessionId`
(`retention === "none" ? undefined : options.sessionId`, `:563-564`) is set and
`compat.sendSessionAffinityHeaders` (default: provider is `openrouter` or the
base URL contains `openrouter.ai`, `:207-212`) is true, send
`x-session-id` (format `openrouter`) or `x-session-affinity` (any other
format; default format is `openrouter` for OpenRouter endpoints, else unset).
The header is merged **before** `model.headers` and the request headers, so both
override it.

Compat resolution (per flag, independent): explicit boolean
`compat.supportsLongCacheRetention` / `compat.supportsCacheControlOnTools` /
`compat.sendSessionAffinityHeaders` wins, else the default above. Pi's
generator sets `supportsCacheControlOnTools: false` and
`supportsLongCacheRetention: false` only on Fireworks' anthropic compat
(`generate-models.ts:1633-1638`); pipy has no Fireworks rows, so built-in rows
keep the defaults and only models.json rows can flip them.

### Bedrock (`ai/src/api/bedrock-converse-stream.ts`)

Pi talks Converse; pipy talks InvokeModel with an Anthropic Messages body. The
Converse `cachePoint` blocks translate to Anthropic `cache_control` on the
preceding content block, the InvokeModel equivalent.

- Gate `supportsPromptCaching(model, env)` (`:855-876`): over the lowered id
  and its `[\s_.:]+ → -` form, a candidate must contain `claude`; else caching
  is on only with env `AWS_BEDROCK_FORCE_CACHE=1`. Among Claude ids: true when a
  candidate contains `fable-5`, `opus-5` or `sonnet-5`, or `-4-`, or
  `claude-3-7-sonnet`, or `claude-3-5-haiku`; else false. (Pi also checks
  `model.name`; pipy's Bedrock adapter matches on id only, like its existing
  adaptive-thinking predicate.)
- Retention: same resolver; `none` → no cache points.
- TTL: `long` → `ttl: "1h"` (`CacheTTL.ONE_HOUR`) **unconditionally** — the
  Bedrock path has no `supportsLongCacheRetention` check. `short` → no ttl.
- System (`:886-905`): only when the system prompt is non-empty; a cache point
  follows the system text → pipy sends
  `system: [{type: "text", text, cache_control}]`. When caching is off the
  system stays a single text block (pipy: `[{type: "text", text}]`).
- Messages (`:1106-1118`): if the last message is role `user`, a cache point is
  appended to its content, whatever the last block type → pipy sets
  `cache_control` on the last block of the last user message.
- Tools: **no** cache point on Bedrock tools (`convertToolConfig`, `:1122+`).
- No session-affinity headers on Bedrock.

### Usage and cost (`ai/src/types.ts:433`, `ai/src/models.ts:1187-1206`)

- `Usage.cacheWrite1h`: subset of `cacheWrite` written with 1h retention.
  Anthropic: `usage.cache_creation.ephemeral_1h_input_tokens` (message_start,
  `:622`, and message_delta when present, `:780-785`). Bedrock:
  sum of `cacheDetails[].inputTokens` with `ttl === "1h"` (`:713-716`).
- `calculateCost`: `shortWrite = cacheWrite - cacheWrite1h`;
  `cost.cacheWrite = (rates.cacheWrite * shortWrite + rates.input * 2 * longWrite) / 1e6`.
  There is **no separate 1h price in the catalog**; the 1h rate is derived as
  2× base input. So `NativeModelCost` gets no new field.

## pipy change (ownership)

1. `providers/openai_prompt_cache.py`: `resolve_cache_retention` stays where
   PC1 put it and is imported by the Anthropic and Bedrock adapters (module
   docstring notes it is shared). No new env var.
2. `providers/anthropic_messages_wire.py`: add the shared Anthropic helpers,
   beside `messages_payload`:
   - `anthropic_cache_control(retention, *, long_ttl: bool) -> dict | None`.
   - `system_blocks(system_prompt, cache_control) -> list | None` (None when
     empty).
   - `apply_last_user_cache_breakpoint(items, cache_control, *, eligible_types)`
     — Anthropic passes Pi's eligible type set
     (`text`/`image`/`tool_result`), Bedrock passes `None` (any block).
3. `providers/anthropic_messages.py`: `AnthropicProvider` gains
   `supports_long_cache_retention: bool = True`,
   `supports_cache_control_on_tools: bool = True`,
   `send_session_affinity_headers: bool | None = None` (None → Pi default from
   provider name / endpoint) and `session_affinity_format: str | None = None`.
   `_build_anthropic_request_body` takes the resolved `cache_control`: system
   blocks (omitted when empty), `cache_control` on the last **immediate** tool
   (never on a `defer_loading` tool — Pi's native-tool-changes rule; pipy's
   deferred tools are appended after the immediate ones), and on the last user
   block. Headers: affinity header after the base headers and before
   `extra_headers`, then `x-api-key`, then the request hook.
4. `provider_construction.py`: resolve the three Anthropic compat flags plus
   `sessionAffinityFormat` independently for `api == "anthropic-messages"` and
   pass them to `AnthropicProvider` (same pattern as PC1's
   `resolve_responses_prompt_cache`).
5. `providers/bedrock.py`: `bedrock_supports_prompt_caching(model_id, env)`;
   `_build_bedrock_request_body` resolves retention, emits system blocks and the
   last-user-block `cache_control` (ttl `1h` on long), no tool breakpoint.
6. Usage: `extract_anthropic_usage` maps
   `cache_creation.ephemeral_1h_input_tokens` to a new normalized counter
   `cache_write_1h_tokens` (added to `NORMALIZED_PROVIDER_USAGE_KEYS`; Bedrock
   InvokeModel returns the same Anthropic usage body, so both adapters get it).
   `AgentProviderUsageSample` gains `cache_write_1h_tokens`, and `_turn_cost`
   prices it Pi's way: `cache_write_per_million * (write - write_1h) +
   input_per_million * 2 * write_1h` (with `write_1h` clamped to `write`). The
   accumulated counters, footer and `AgentUsage` shape are unchanged.

## Tests (exact request shapes; no live Anthropic credentials)

- Anthropic: short/long/none matrix on system/tools/last-user block; long with
  `supportsLongCacheRetention: false` → no ttl; `supportsCacheControlOnTools:
  false` → no tool breakpoint; deferred tools → breakpoint on the last immediate
  tool only; last message assistant → no message breakpoint; image as last block
  → breakpoint on the image; coalesced tool results with a sibling text → on the
  sibling text; empty system → no `system` key; `PIPY_CACHE_RETENTION=long`
  with no explicit retention → ttl `1h`; summary request (`cache_retention:
  "none"`) → no `cache_control` anywhere; breakpoint count ≤ 4.
- Anthropic affinity: OpenRouter base URL → `x-session-id`; explicit
  `sendSessionAffinityHeaders: true` → `x-session-affinity`; retention none or
  no session → none; model headers override it.
- Bedrock: Claude 4.x/5.x/3.7-sonnet/3.5-haiku gate true; non-Claude false;
  `AWS_BEDROCK_FORCE_CACHE=1` with a non-Claude id → true; long → ttl 1h; none
  → no cache_control; tools never carry it; last message assistant → none.
- Construction: compat flags resolved and passed through for anthropic rows.
- Usage/cost: `ephemeral_1h_input_tokens` parsed; `_turn_cost` 1h split; golden
  fixtures updated to the new wire shape.

## Done when

- The request-shape tests above pass; the Anthropic and Bedrock golden fixtures
  carry Pi's breakpoints.
- `just check` is green.
- `docs/providers.md` gains an "Anthropic prompt caching" section; CHANGELOG and
  `docs/backlog.md` (PC2 done) updated.
- Different-family review CLEAN.

## Deviations and follow-ons (kept out of this slice)

- Catalog-cost pricing: pipy prices sessions only from `PRICING_TABLE`, so the
  1h split is exact in `_turn_cost` but no Anthropic row is priced yet. Wiring
  `NativeModelCost` into `pricing_for` (Pi `calculateCost` for every model) is
  a separate follow-on.
- Deferred tools: Pi's native-tool-changes path (mid-conversation
  `tool_addition`/`tool_removal` system messages) declares
  `__pi_deferred_placeholder__` from the first request, so a later deferred
  tool does not change the cached tool prefix. pipy still uses the older
  `tool_reference`-in-`tool_result` mechanism and has no placeholder, so the
  first activation of a deferred tool changes the tools list and misses the
  cache once. Porting native tool changes (with the placeholder) is its own
  follow-on; PC2 only places the breakpoint on the last immediate tool.
- Bedrock caching gate uses the model id only (Pi also checks `model.name`).
- Bedrock InvokeModel instead of Converse (existing transport deviation);
  cache points become Anthropic `cache_control` blocks.
- Pi prompt-cache warming (`c596d09d9`, `coding-agent/src/core/cache-warmer.ts`).
- Claude Code OAuth identity system block (pipy has no Anthropic OAuth path).
- tau's second breakpoint at the previous request boundary (tau
  `_apply_message_cache_breakpoints`) is a tau improvement, not Pi; not adopted.

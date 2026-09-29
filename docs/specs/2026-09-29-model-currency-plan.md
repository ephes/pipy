# Model currency: catalog rows, provider defaults, thinking maps (MC1+MC3)

Status: implemented (2026-09-29). Reference: pi-mono `4df157433` (2026-09-29).

## Pi reference

- Rows, costs, context windows, max tokens, thinking maps and compat flags come
  from Pi's generator: `packages/ai/scripts/generate-models.ts`. It was run at
  `4df157433` against the live models.dev feed with
  `--json-only --json-output <scratch>`, so every value below is Pi's generated
  output, not a hand estimate. The thinking-map overrides are
  `generate-models.ts:1080-1100`: opus-4-7+/opus-5/sonnet-5 merge
  `{xhigh, max}`, fable-5 merges `{off: null, xhigh, max}`, and codex
  xhigh-capable rows merge `{minimal: "low"}`. `isAnthropicAdaptiveThinkingModel`
  (`:607-624`) sets `compat.forceAdaptiveThinking` on anthropic-messages rows.
- Defaults: `packages/coding-agent/src/core/model-resolver.ts:21-36`
  (`defaultModelPerProvider`).
- `getSupportedThinkingLevels` (`packages/ai/src/models.ts:1211-1220`):
  - a non-reasoning model offers `["off"]`;
  - any level mapped to `null` is removed, **including `off`**;
  - `xhigh` and `max` need an explicit mapping;
  - ordinary levels are identity-available.

  `clampThinkingLevel` is at `:1222-1241`, and `cycleThinkingLevel` at
  `agent-session.ts:2552-2561`. An index of -1 there cycles to `levels[0]`.
- Responses-family thinking:
  - `openai-responses.ts:231-232,343-357`
  - `azure-openai-responses.ts:182-183,330-344`
  - `openai-codex-responses.ts:514-515,582-597`

  Each one clamps the requested level. `off` becomes `undefined`. The on-state
  then emits `reasoning.effort = map[level] ?? level`. The off-state
  (`model.reasoning && map.off !== null`) emits
  `reasoning: {effort: map.off ?? "none"}`, with no `summary`.
- Anthropic (`anthropic-messages.ts:842-915,1159-1190`):
  - Adaptive thinking is gated on `compat.forceAdaptiveThinking === true`.
  - The effort is `map[level]` when that is a string; otherwise
    minimal/low→low, medium, high, and anything else → high.
  - `thinking:{type:"disabled"}` is emitted only when thinking is off **and**
    `map.off !== null`.

  Anthropic does not clamp at request time. Pi's session clamps the level
  instead.
- Bedrock (`bedrock-converse-stream.ts:750-808,1236-1280`):
  - Adaptive thinking is chosen by an id marker list: opus-4-6/4-7/4-8, opus-5,
    sonnet-4-6, sonnet-5 and fable-5. There is no mythos marker.
  - Native `xhigh` is sent for opus-4-7/4-8, opus-5, sonnet-5 and fable-5.
  - Thinking is off → no thinking fields.
- Google and Vertex clamp at request time (`google-generative-ai.ts:323`,
  `google-vertex.ts:329`). pipy's Google disabled config already matches Pi's
  `getDisabledGoogleThinkingConfig` result for Gemini 3.

## Fields this slice changes

### Catalog rows (`native/catalog_data.py`)

Values are Pi-exact: cost is input/output/cacheRead/cacheWrite, followed by
context window and max tokens. Pi maps that are PARTIAL (they omit an ordinary
level) have their identity ordinary levels spelled out. pipy's
`map_thinking_level` reads map keys, so a spelled-out map is behaviourally
equal to Pi's partial one. A Pi `null` stays `None`. A key Pi omits stays
omitted, and that matters for `off`: an absent `off` and `off: None` behave
differently.

- **anthropic** (all rows carry `compat.forceAdaptiveThinking: true`, image,
  reasoning, 1,000,000 context, 128,000 max tokens):

  | Row | Status | Cost | Pi map |
  |---|---|---|---|
  | `claude-opus-5-5` | new | 4/20/0.2/5 | `{off:null, minimal:null, low, medium, high, xhigh, max}` (full) |
  | `claude-sonnet-5-5` | new | 2/10/0.2/2.5 | same as opus-5-5 |
  | `claude-opus-5` | new | 5/25/0.5/6.25 | `{off:null, xhigh, max}` |
  | `claude-sonnet-5` | new | 2/10/0.2/2.5 | `{xhigh, max}` |
  | `claude-fable-5` | new | 10/50/1/12.5 | `{off:null, xhigh, max}` |
  | `claude-fable-5-1` | new; Pi has it | 10/50/0.25/12.5 | `{off:null, xhigh, max}` |
  | `claude-opus-4-8` | new | 5/25/0.5/6.25 | `{xhigh, max}` |
  | `claude-opus-4-7` | existing; map was `{xhigh}` | unchanged | `{xhigh, max}`, and the compat flag is added |

  - `claude-haiku-4-5` and `claude-haiku-4-5-20251001` are new. They take
    reasoning, have no map, cost 1/5/0.1/1.25, and have a 200,000 context
    window with 64,000 max tokens.
  - The context window of `claude-sonnet-4-5` and `-20250929` goes from 200,000
    to 1,000,000, as in Pi.
- **openai** (openai-responses, all 272,000 context and 128,000 max tokens):
  - `gpt-6-sol` (2/10/0.2/2.5) and `gpt-6-luna` (0.1/0.5/0.01/0.125) use
    `{off:"none", minimal:null, low, medium, high, xhigh, max}`.
  - `gpt-6-astra` (10/50/1/12.5) uses `{off:null, minimal:null, ...}`.
  - `gpt-5.6-sol` (4/20/0.4/5), `gpt-5.6-terra` (2/12/0.2/2.5) and
    `gpt-5.6-luna` (0.2/1.2/0.02/0.25) use the sol/luna map.
  - The existing `gpt-5.5` changes to 5/30/0.5/0 with a 272,000 context window.
  - The existing `gpt-5.4` changes to 2.5/15/0.25/0 with a 272,000 context
    window.
  - Both existing rows get the map
    `{off:"none", minimal:null, low, medium, high, xhigh, max:null}`.
  - `compat.supportsToolSearch` is set to true on every row where Pi sets it.
- **openai-codex** (all 272,000 context and 128,000 max tokens; compat
  `supportsToolSearch` is set to true on all of them):
  - `gpt-6-sol` (2/10/0.2/2.5) and `gpt-6-luna` (0.1/0.5/0.01/0.125) use
    `{off:"none", minimal:"low", low, medium, high, xhigh, max}`.
  - `gpt-6-astra` (10/50/1/12.5) uses the same map with `off:null`.
  - `gpt-5.6-terra` (2/12/0.2/2.5) and `gpt-5.6-luna` (0.2/1.2/0.02/0.25) use
    Pi's `{xhigh, max, minimal:"low"}`, spelled out. It has no `off` key.
  - `gpt-5.6-sol` changes context from 372,000 to 272,000 and cost to
    4/20/0.4/5. Its map is Pi's `{xhigh, max, minimal:"low"}`, and the old
    explicit `off: None` is removed.
  - `gpt-5.5` changes to 5/30/0.5/0 with a 272,000 context window. Its map
    becomes `{xhigh, minimal:"low"}`, spelled out.
  - `gpt-5.4` is removed (Pi `2e6fe2f98`).
- **google** and **google-vertex** (1,048,576 context, 65,536 max tokens):
  - `gemini-3.5-flash` (1.5/9/0.15/0) and `gemini-3.1-flash-lite`
    (0.25/1.5/0.025/0) are new. Both use
    `{off:null, minimal, low, medium, high, xhigh:null, max:null}`.
  - The existing `gemini-3.1-pro-preview` changes to 2/12/0.2/0 with a
    1,048,576 context window, and gets
    `{off:null, minimal:null, low, medium, high, xhigh:null, max:null}`.
  - Pi has no Claude rows on Vertex, so none are mirrored.
- **amazon-bedrock** (the `us.` inference-profile ids pipy already uses; all
  1,000,000 context and 128,000 max tokens except haiku):

  | Row | Cost | Pi map |
  |---|---|---|
  | `us.anthropic.claude-opus-5-5` | 4.4/22/0.22/5.5 | `{xhigh, max}` |
  | `us.anthropic.claude-opus-5` | 5.5/27.5/0.55/6.875 | `{xhigh, max}` |
  | `us.anthropic.claude-sonnet-5` | 2.2/11/0.22/2.75 | `{xhigh, max}` |
  | `us.anthropic.claude-fable-5` | 11/55/1.1/13.75 | `{off:null, xhigh, max}` |
  | `us.anthropic.claude-opus-4-8` | 5.5/27.5/0.55/6.875 | `{xhigh, max}` |
  | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | 1.1/5.5/0.11/1.375 | no map; 200,000 context and 64,000 max tokens |

  - The existing `us.anthropic.claude-opus-4-6-v1` gets Pi's `{max}` map,
    spelled out. Its cost becomes 5.5/27.5/0.55/6.875.
- **openrouter**: Pi routes `anthropic/claude-*` through `anthropic-messages`
  against `openrouter.ai/api`, with OpenRouter session-affinity headers. pipy's
  openrouter rows use openai-completions only, so this slice adds no OpenRouter
  Claude rows (see Deviations). The existing `moonshotai/kimi-k2.6` row, now the
  default, gets Pi's values: 0.65/3.41/0.15/0, a 262,144 context window, 235,929
  max tokens, and image input.
- **cloudflare**: the existing `@cf/moonshotai/kimi-k2.6` row, now the default,
  gets Pi's values: 0.95/4/0.16/0, a 262,144 context window, 256,000 max tokens,
  image input, and the map `{off:"none", minimal:null, low:null, medium:null,
  high, xhigh:null, max:null}`.
- **azure-openai**: the existing `gpt-5.4` row, now the default, gets Pi's
  values: 2.5/15/0.25/0, a 1,050,000 context window, and the map
  `{off:null, xhigh}`, spelled out.

Retired or pipy-only rows are kept, not refreshed. That covers the anthropic
and bedrock 3.5 rows, `gpt-5.1-codex`, and the Gemini 2.x rows. Their existing
maps (such as `off: None` on the gpt-5.1-codex rows) keep today's behaviour.
Pruning them is out of scope.

### Defaults (`native/catalog.py` and `native/provider_registry.py`)

Both maps are set to Pi's `defaultModelPerProvider`:

| Provider | Default |
|---|---|
| anthropic | `claude-opus-4-8` |
| openai | `gpt-5.5` |
| openai-codex | `gpt-5.5` |
| google | `gemini-3.1-pro-preview` |
| google-vertex | `gemini-3.1-pro-preview` |
| mistral | `devstral-medium-latest` |
| amazon-bedrock | `us.anthropic.claude-opus-4-6-v1` |
| azure-openai | `gpt-5.4` |
| openrouter | `moonshotai/kimi-k2.6` |
| cloudflare | `@cf/moonshotai/kimi-k2.6` |
| openai-completions | `gpt-5.5` |

- `azure-openai` is pipy's name for Pi's `azure-openai-responses`, and
  `cloudflare` is pipy's name for `cloudflare-workers-ai`.
- `openai-completions` is a pipy-only provider name. Pi has no entry for it, so
  pipy uses Pi's `openai` default (`gpt-5.5`). The new
  `openai-completions/gpt-5.5` row copies Pi's openai gpt-5.5 metadata.

A new test pins both maps to this table and requires that every default
resolves to a catalog row.

### Thinking

1. **Available levels (`thinking.available_thinking_levels`).** Pi's
   `getSupportedThinkingLevels` is ported exactly: an explicit `off: None`
   removes `off`. The Shift+Tab cycle (`repl_state.cycle_thinking_level`) takes
   Pi's index -1 path, so a current level missing from the list cycles to
   `levels[0]`. Before this change it raised `ValueError` whenever `off` was
   absent. The only existing rows that carry `off: None` are the openai and
   openai-codex rows, which are refreshed above, and `gpt-5.1-codex`. That
   model is always-reasoning, so dropping `off` from its cycle is the Pi
   semantic.
2. **Responses family (openai-responses, azure-openai-responses,
   openai-codex-responses).** One shared helper,
   `thinking.resolve_responses_reasoning(model, level)`, replaces
   `resolve_codex_effort`. It returns `(effort, off_state)`:
   - If `level` is unset (`None`), it returns `(None, False)`. pipy's unset
     level means "provider default" and keeps today's wire; see Deviations.
   - If the model is non-reasoning, it returns `(None, False)`.
   - An explicit `off` is not clamped, because Pi passes
     `reasoning: undefined` for `off`. It returns the off-state:
     `map["off"]` when the key is present (its `None` means omit), else
     `"none"`. It then returns `(that, True)`.
   - Any other level is clamped, and the result is
     `(map.get(clamped) or clamped, False)`.

   For openai-responses and azure, the effort goes into the adapter's existing
   `reasoning_effort` field, so the body becomes `reasoning: {effort}` in both
   states. That is exactly Pi's off-state. For the on-state, Pi also sends
   `summary:"auto"` and `include`. That was an existing gap and stays out of
   scope. The codex adapter gets `reasoning_off: bool`:

   | State | Codex `reasoning` body |
   |---|---|
   | on | `{summary:"auto", effort}` (unchanged) |
   | off with an effort | `{effort}` |
   | off with `None` | key omitted |
   | unset | `{summary:"auto"}` (unchanged) |
3. **Anthropic (`providers/anthropic_messages.py`).**
   - `supports_adaptive_thinking` keeps the id-marker fallback, extended to
     Pi's generator predicate: `opus-4-6`/`4.6`, `opus-4-7`/`4.7`,
     `opus-4-8`/`4.8`, `opus-5`/`opus.5`, `sonnet-4-6`/`4.6`,
     `sonnet-5`/`sonnet.5`, `fable-5` and `mythos-5`.
   - `AnthropicProvider` gains `force_adaptive_thinking: bool | None`.
     Catalog construction always passes
     `compat.get("forceAdaptiveThinking") is True`. That is exactly Pi's
     runtime gate (`anthropic-messages.ts:886,1173`), so an unflagged custom
     `models.json` id such as `my-opus-5` takes the budget path, as in Pi. Only
     a directly constructed adapter (`None`, which the catalog path never
     passes) falls back to the id-marker predicate, which now mirrors Pi's
     generator list. Every built-in adaptive row carries the compat flag
     explicitly.
   - `max` already passes through `output_config.effort`. The `{max: "max"}`
     maps make it reachable.
   - `thinking_disabled` is suppressed for anthropic-messages when the row's
     map has an explicit `off: None`, matching Pi's `map.off !== null` gate.
     Google and Vertex keep their own disabled shapes.
4. **Bedrock (`providers/bedrock.py`).**
   - Bedrock gets its own marker tuple, matching Pi's bedrock runtime list:
     opus-4-6/4-7/4-8, opus-5, sonnet-4-6, sonnet-5 and fable-5. It has no
     mythos or dotted forms.
   - The adaptive effort is `xhigh` when native xhigh is supported. Otherwise
     it is the mapped value. The existing minimal→low clamp stays.
5. **Google/Vertex.** Construction clamps the level (Pi's request-time clamp)
   before mapping it. A clamped `off` produces the existing disabled shape.
   This makes the new `minimal: null` and `off: null` entries resolve as Pi
   does.
6. **Startup thinking level.** This ports the subset of Pi `sdk.ts:238-263`
   that pipy has inputs for. The initial REPL / `pipy run` level is
   `--thinking` ?? the settings `defaultThinkingLevel` ?? `"medium"`
   (`DEFAULT_THINKING_LEVEL`, `core/defaults.ts:3`), clamped to the startup
   model with `clamp_thinking_level`. That clamp relies on item 1's
   `available_thinking_levels` port, in which `off: None` removes `off`, so
   Fable's `off` clamps forward to `minimal`. A non-reasoning model clamps to
   `off`.

   Today pipy starts unset, and on Anthropic reasoning rows unset means
   `thinking:{type:"disabled"}`. Without this change, a fresh session on the
   new `claude-opus-4-8` default would run with thinking disabled, while Pi
   runs medium adaptive thinking.

   Pi's session-restore and per-model-settings rungs are not ported. pipy has
   no per-model thinking setting, and its resume path keeps its existing
   restore behaviour. The unset (`None`) rules in items 2-3 now matter only
   for direct construction.
7. **Chrome meter (`native/chrome.py`).** The Sol entry of 372k is removed.
   Every openai-codex gpt-5 and gpt-6 row shows 272k, as Pi's catalog does.

## Deviations from Pi (intentional; recorded in docs)

- **OpenRouter Claude rows are deferred.** Pi uses `anthropic-messages` over
  OpenRouter, with `x-session-id` affinity headers. pipy has no OpenRouter
  Anthropic transport, and putting these rows on openai-completions would
  diverge from Pi's API family.
- **Mid-conversation managed effort is deferred.** On Pi rows with
  `supportsMidConvoEffort` (opus-5, opus-5-5, sonnet-5-5, fable-5-1), Pi always
  sends adaptive thinking, `effort:"high"` and a `configuration_update` system
  message. pipy sends the plain adaptive shape with `output_config.effort`.
  That is the shape Pi uses for opus-4-8 and sonnet-5.
- **An unset thinking level keeps the provider default.** This matters only
  for direct construction, because startup now defaults to `medium` (item 6).
  Pi never has an unset level. pipy's construction boundary still accepts
  `None`, and it maps `None` to "no thinking field" (on the Responses family)
  rather than to the off-state, so a caller that omits the level does not
  silently turn reasoning off.
- **Session clamp.** This is no longer a deviation; the code review added it.
  - The carried level is clamped on every model switch, as in Pi `setModel`
    through `setThinkingLevel`. It is also clamped at startup (item 6). A
    stored `off` on an `off:null` row, or `minimal` on opus-5-5, therefore
    becomes the nearest offered level before it reaches a request, as in Pi.
  - Anthropic and Bedrock still do not clamp at request time, as in Pi.
  - The remaining gap is an explicit `/model provider/model:level` suffix.
    pipy passes it through unchanged, while Pi clamps it.
- **Temperature and prompt caching need no action or are deferred.** pipy sends
  no Anthropic temperature, so `supportsTemperature: false` needs no action.
  The promptCache/inputLimits/fallback-model metadata is not carried; prompt
  caching is out of scope.

## Done when

- The new and refreshed rows match Pi's generated values. A test asserts the
  Pi-derived expectations for every row this slice touches: cost, context
  window, max tokens, the available-levels list, and the clamp and map result
  for off/minimal/xhigh/max.
- The defaults test pins both maps to Pi's table, and each default resolves to a
  catalog row.
- Per-family conformance tests cover the wire shape:
  - anthropic: opus-5-5 max → adaptive `output_config.effort:"max"`; fable
    off → no thinking key; sonnet-5 off → disabled; opus-4-7 max → adaptive
    max; a compat-forced adaptive custom id;
  - bedrock: opus-5-5 xhigh → adaptive xhigh; fable max;
  - openai-responses: gpt-6-sol off → `{effort:"none"}`; astra off → no
    `reasoning`; minimal → clamped `low`; unset → no `reasoning`;
  - azure: gpt-5.4 off → no `reasoning`;
  - codex: gpt-6-sol off → `{effort:"none"}` with no summary; astra off → no
    `reasoning`; gpt-5.5 off → `{effort:"none"}`; minimal → `low`; unset →
    `{summary:"auto"}`;
  - google: gemini-3.1-pro minimal → LOW; gemini-3.5-flash off → MINIMAL.
- Startup tests cover these cases:
  - no `--thinking` and no setting → `medium`;
  - the settings `defaultThinkingLevel` wins over `medium`;
  - `--thinking` wins over the setting;
  - on a non-reasoning model the level clamps to `off`;
  - on Fable, `off` clamps to `minimal`.
- The cycle test covers a model without `off`. The chrome meter shows 272k for
  codex gpt-5.6-sol and gpt-6-sol.
- `just check` passes. `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`
  passes.
- `docs/provider-catalog.md`, `docs/providers.md` and CHANGELOG `[Unreleased]`
  are updated.

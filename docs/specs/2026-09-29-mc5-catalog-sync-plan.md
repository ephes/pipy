# MC5 — Catalog sync and drift check (plan)

Status: planned (backlog item MC5, 2026-09-29). Pi reference: `~/src/pi-mono`
at `4df157433`, paths relative to `packages/ai/`. tau reference:
`~/src/tau/scripts/generate_models.py`.

## Backlog claims, verified

- "Pi generates its catalog from models.dev (`scripts/generate-models.ts`) plus
  a remote overlay": true. The generator writes gitignored JSON: `npm run
  hydrate:model-data` fills `src/providers/data/<provider>.json` (grouped by api,
  keys `chat:<id>`, plus `.manifest.json`), and
  `node scripts/generate-models.ts --strict --json-only --json-output <dir>`
  writes `<dir>/models.json` (`{provider: {id: model}}`, chat models only).
- "`provider_catalog_conformance.py` checks structure only": true. Nothing
  compares row values with Pi.
- "Keep `catalog_data.py` as the correction layer": kept (see Decision).
- tau's `generate_models.py` reads models.dev directly. Pipy's parity target is
  Pi, and models.dev lacks Pi's generator corrections (thinking maps,
  `forceAdaptiveThinking`, explicit prompt-cache compat, context overrides), so
  pipy reads Pi's generated output, not models.dev.

## Decision: curated table plus a drift check, not a generated data file

Evidence from Pi `4df157433` against pipy's 70 built-in rows:

- Pi ships about 870 chat rows for the providers pipy implements (Bedrock 174,
  OpenRouter 394). Pipy carries a curated subset on purpose.
- Pipy rows carry pipy conventions that a generator would have to re-apply as
  overrides anyway: provider renames (`azure-openai`, `cloudflare`, the
  pipy-only `openai-completions`), adapter families that differ from Pi's
  transport (`amazon-bedrock` InvokeModel vs Pi `bedrock-converse-stream`,
  `mistral` chat completions vs Pi `mistral-conversations`,
  `cloudflare-workers-ai` vs Pi `openai-completions`), base-URL conventions
  (Codex `/codex`, Google `/v1beta` added by the adapter, Vertex region
  templating), provider-prefixed display names, spelled-out partial thinking
  maps, and legacy rows Pi dropped.

A generated file would need that override layer plus the curation list, which is
what `catalog_data.py` already is. So `catalog_data.py` stays the curated layer
and a manual drift check keeps it honest.

## Tool: `scripts/catalog_drift.py`

Stdlib plus pipy imports; run with `uv run python scripts/catalog_drift.py`.

- **Input** `--pi-data PATH`: a `models.json` file from `--json-output`, a
  `--json-output` directory, or the hydrated `src/providers/data` directory.
  Default: `$PI_MONO/packages/ai/src/providers/data`, `PI_MONO` defaulting to
  `~/src/pi-mono`. Missing input exits 2 with the regenerate command.
- **Provider mapping** (pipy → Pi): `azure-openai` → `azure-openai-responses`,
  `cloudflare` → `cloudflare-workers-ai`, `openai-completions` → `openai`,
  others identical. `fake` is skipped.
- **Compared fields** on every row both sides carry: `api`, `reasoning`,
  `input`, `cost` (all four), `contextWindow`, `maxTokens`, `thinkingLevelMap`,
  `baseUrl`, and every compat key pipy's request construction reads
  (`forceAdaptiveThinking`, `supportsToolSearch`, `supportsToolReferences`,
  `supportsExplicitPromptCacheMode`, `supportsLongCacheRetention`,
  `supportsCacheControlOnTools`, `sendSessionAffinityHeaders`,
  `sessionAffinityFormat`, `thinkingFormat`, `supportsReasoningEffort`,
  `openRouterRouting`, `vercelGatewayRouting`). Compat values are compared as
  written, with a missing key distinct from any value, because Pi's runtime
  defaults differ per key (`supportsLongCacheRetention` defaults to true). Where
  pipy reaches the same effective value by detection instead of an explicit row
  value (for example OpenRouter's `thinkingFormat` from the provider name), the
  allowlist records that as a deviation with the reason. Display names are not
  compared: pipy prefixes them per provider by convention.
- **Thinking-map normalization** compares effective behavior per level
  (`off, minimal, low, medium, high, xhigh, max`), with Pi semantics on the Pi
  side and pipy semantics on the pipy side:
  - Pi (`models.ts` `getSupportedThinkingLevels`): on a reasoning row an
    ordinary level missing from the map is identity; `off`/`xhigh`/`max` are
    raw (absent, `null`, or a string). A non-reasoning row has no levels.
  - pipy (`thinking.py`): an empty map on a reasoning row means ordinary
    identity; a non-empty map is read by key, so a missing ordinary level is
    unsupported. That makes a partial pipy map that forgets to spell out an
    identity level show up as drift.
- **Row sets.** `pipy-only`: a pipy row Pi does not ship. `not-carried`: a Pi
  row for a mapped provider that pipy does not carry.
- **Allowlist** `scripts/catalog_drift_allowlist.json`: a list of
  `{kind, match, field, reason}`.
  - `kind` is `deviation` (intentional, documented) or `pending` (known drift
    awaiting a refresh, printed in its own section so it stays visible).
  - `match` is an `fnmatch` pattern over `pipy-provider/model-id`; an
    optional `except` list of patterns carves rows back out (Bedrock: every
    not-carried row except `us.anthropic.claude-*`, which get narrow entries so
    a new current Claude mirror shows up as drift).
  - `field` is `pipy-only`, `not-carried`, or a compared field name
    (`compat.<key>` for compat).
  - `reason` is required.
- **Stale entries.** An entry that matches no current finding is reported as
  stale and fails the check, so the allowlist shrinks when Pi or pipy converge.
- **Output.** A text report with sections: drift (fails), stale allowlist
  entries (fails), pending (listed), summary counts (deviations, not-carried).
  Each drift line shows the pipy value and the Pi value in `_m(...)` keyword
  form, which is the proposed patch. `--json` prints the same data as JSON.
  `--verbose` also lists allowlisted deviations and not-carried rows.
- **Exit codes:** 0 clean, 1 drift or stale entries, 2 input error.
- **Recipe:** `just catalog-drift *args`. Manual only; not wired into
  `just check` or CI (the owner ruled out automating parity checks). Focused
  unit tests (fixture JSON, no pi-mono needed) do run in `just check`.

## Real drift found today, fixed in a second commit

Rows below differ from Pi's generated values and are not documented deviations.
The first commit (the tool) lists them as `pending` allowlist entries, so the
check is green and the drift stays visible. The second commit brings them to
Pi-exact values and removes those entries:

- `openai-completions/gpt-4.1`: context 1,047,576.
- `openrouter/openai/gpt-5.1-codex`: cost (1.25, 10, 0.13, 0); Pi map
  `{off: null, minimal: null, low, medium, high, xhigh: null, max: null}`.
- `google/gemini-2.5-pro`: cacheRead 0.125, context 1,048,576.
- `google-vertex/gemini-2.5-pro`: cost (1.25, 10, 0.125, 0), context 1,048,576.
- `mistral/mistral-large-latest`: cost (0.5, 1.5, 0.05, 0), context and max
  262,144.
- `mistral/devstral-medium-latest`: cacheRead 0.04, context and max 262,144.
- `mistral/mistral-small-latest`: cost (0.15, 0.6, 0.015, 0), context and max
  256,000, `reasoning: true`, map
  `{off: "none", minimal: null, low: null, medium: null, high: "high",
  xhigh: null, max: null}`.

### Mistral reasoning request shape (Pi `api/mistral-conversations.ts:199-213`)

Making `mistral-small-latest` a reasoning row reaches pipy's Mistral
`reasoning_effort` field. Pi's resolution, pinned:

- `clamped = options.reasoning ? clampThinkingLevel(model, level) : undefined`;
  `reasoning = clamped === "off" ? undefined : clamped`.
- `effortMap = model.reasoning ? model.thinkingLevelMap : undefined`.
- With a map: on-state sends `effortMap[reasoning] ?? "high"`; off/unset sends
  `effortMap.off ?? undefined` (a missing or `null` `off` sends nothing).
- Without a map on a reasoning row Pi sends `prompt_mode: "reasoning"` instead.
- A non-reasoning row sends neither field.

Pipy port in `provider_construction._resolve_family_thinking`, `api == "mistral"`:
non-reasoning → no effort; reasoning with a map → the rules above via
`clamp_thinking_level`; reasoning without a map keeps pipy's current raw-level
`reasoning_effort` (Pi's `prompt_mode` needs a new Mistral adapter field; kept
as a documented deviation). Tests: on-state `high`; `medium` clamps to `high`;
off and unset send `"none"`; an identity-available unmapped ordinary level sends
`"high"`; `off: null` and a missing `off` send nothing; a non-reasoning row with
a map sends nothing.

### Kept as documented deviations

- Mistral `input: image` (Pi) — pipy's chat-completions wire has no image
  serialization, so the Mistral rows stay text-only.
- `openai-completions/*` `api`: the pipy-only provider serves Pi's `openai`
  rows (Pi `openai-responses`) over Chat Completions.
- Mistral `api`/`baseUrl` (chat completions vs Pi conversations), Bedrock `api`
  (InvokeModel), Cloudflare `api` (dedicated adapter), Codex/Google/Vertex/Azure
  `baseUrl` conventions.
- `pipy-only` rows: Claude 3.5 (anthropic, Bedrock), `gpt-5.1-codex` (openai,
  Codex), Gemini 2.0, Cloudflare Llama 3.x, OpenRouter `openai/gpt-4o:extended`,
  and OpenRouter Claude (Pi routes it over `anthropic-messages`, which pipy lacks).
- `not-carried`: Pi rows outside pipy's curated subset, by provider pattern.
  Patterns are narrow enough that a new current-generation row (for example a
  new `claude-*` or `us.anthropic.claude-*` id) shows up as drift.

## Done when

- `just catalog-drift` against Pi `4df157433` output exits 0 with an empty or
  near-empty pending section.
- Focused tests cover normalization, allowlist matching, stale detection, both
  input layouts, and the Mistral reasoning resolution.
- `just check` is green.
- Docs: `docs/provider-catalog.md` (drift check section and updated
  deviations), `docs/providers.md` where Mistral rows are described,
  `CHANGELOG.md`, `docs/backlog.md` MC5 marked done.

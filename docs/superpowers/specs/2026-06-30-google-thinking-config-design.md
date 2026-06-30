# Plan — google-generative-ai `generationConfig.thinkingConfig` injection

Parity gap (gap audit item 21; owning spec `docs/provider-catalog.md` lines
116-118):

> google's `thinkingConfig` shape is per-model (level enum vs token budget) and
> not yet catalog-encoded, so google thinking is not injected.

## Scope

One reviewable slice: inject Pi's `generationConfig.thinkingConfig` into the
**`google-generative-ai`** request body so a reasoning-capable Gemini/Gemma model
sends the per-model thinking shape Pi sends — either a `thinkingLevel` enum
(Gemini 3 Pro/Flash, Gemma 4) or a `thinkingBudget` token count (Gemini 2.5
family) with `includeThoughts: true` when thinking is on, and a per-model
*disabled* config when the model is reasoning-capable but thinking is off/unset.

This slice changes **only** the `generationConfig.thinkingConfig` field group on
the `google-generative-ai` request. All other request fields (`contents`,
`systemInstruction`, `tools`, the `?key=` auth, the endpoint) are already matched
and unchanged. The **`google-vertex`** adapter is intentionally **not** changed
here — it is gap audit item 22, has its own `THINKING_LEVEL_MAP` detail, and
stays a documented follow-on.

## Pi reference (pinned)

`~/src/pi-mono/packages/ai/src/providers/google.ts`.

### Product path — `streamSimpleGoogle` (lines ~280-315)

```ts
if (!options?.reasoning) {
    return streamGoogle(model, context, { ...base, thinking: { enabled: false } });
}
const clampedReasoning = clampThinkingLevel(model, options.reasoning);
const effort = (clampedReasoning === "off" ? "high" : clampedReasoning);
if (isGemini3ProModel(m) || isGemini3FlashModel(m) || isGemma4Model(m)) {
    return streamGoogle(..., { thinking: { enabled: true, level: getThinkingLevel(effort, m) } });
}
return streamGoogle(..., { thinking: { enabled: true, budgetTokens: getGoogleBudget(m, effort, options.thinkingBudgets) } });
```

### Apply path — `buildParams` (lines ~369-379)

```ts
if (options.thinking?.enabled && model.reasoning) {
    const thinkingConfig: ThinkingConfig = { includeThoughts: true };
    if (options.thinking.level !== undefined) thinkingConfig.thinkingLevel = options.thinking.level;
    else if (options.thinking.budgetTokens !== undefined) thinkingConfig.thinkingBudget = options.thinking.budgetTokens;
    config.thinkingConfig = thinkingConfig;
} else if (model.reasoning && options.thinking && !options.thinking.enabled) {
    config.thinkingConfig = getDisabledThinkingConfig(model);
}
```

`config` is the GoogleGenAI SDK config, serialized into the REST
`generateContent` body as `generationConfig`. So `config.thinkingConfig` is wire
`generationConfig.thinkingConfig`. `ThinkingConfig` (REST v1beta) keys used:
`includeThoughts` (bool), `thinkingLevel` (enum string), `thinkingBudget` (int).

### Model-family helpers (lines ~398-503)

- `isGemini3ProModel`: `/gemini-3(?:\.\d+)?-pro/` on lowercased id.
- `isGemini3FlashModel`: `/gemini-3(?:\.\d+)?-flash/`.
- `isGemma4Model`: `/gemma-?4/`.
- `GoogleThinkingLevel` enum values: `"MINIMAL" | "LOW" | "MEDIUM" | "HIGH"`
  (`THINKING_LEVEL_UNSPECIFIED` also exists but is never emitted).
- `getThinkingLevel(effort, model)`:
  - Gemini 3 Pro: `minimal|low -> "LOW"`, `medium|high -> "HIGH"`.
  - Gemma 4: `minimal|low -> "MINIMAL"`, `medium|high -> "HIGH"`.
  - else (Gemini 3 Flash + default): `minimal->"MINIMAL"`, `low->"LOW"`,
    `medium->"MEDIUM"`, `high->"HIGH"`.
- `getGoogleBudget(model, effort, customBudgets?)`:
  - `customBudgets?.[effort]` wins when present (out of scope — see below).
  - `id.includes("2.5-pro")`: `{minimal:128, low:2048, medium:8192, high:32768}`.
  - `id.includes("2.5-flash-lite")`: `{minimal:512, low:2048, medium:8192, high:24576}`.
  - `id.includes("2.5-flash")`: `{minimal:128, low:2048, medium:8192, high:24576}`.
  - else: `-1` (Gemini "dynamic"/auto budget — a legitimate emitted value).
- `getDisabledThinkingConfig(model)`:
  - Gemini 3 Pro: `{ thinkingLevel: "LOW" }`.
  - Gemini 3 Flash: `{ thinkingLevel: "MINIMAL" }`.
  - Gemma 4: `{ thinkingLevel: "MINIMAL" }`.
  - else (Gemini 2.x): `{ thinkingBudget: 0 }`.
  - Note: the disabled config carries **no** `includeThoughts` (hidden thinking
    stays invisible).

### Pinned field group this slice changes

`generationConfig.thinkingConfig` — object, nested under a (newly emitted when
needed) top-level `generationConfig`.

- **Enabled** (reasoning model + thinking on): `{ includeThoughts: true,
  thinkingLevel: <enum> }` for the level-path families OR `{ includeThoughts:
  true, thinkingBudget: <int, may be -1> }` for the budget-path families.
  Exactly one of `thinkingLevel`/`thinkingBudget` is present.
- **Disabled** (reasoning model + thinking off/unset): the per-model disabled
  config above; **no** `includeThoughts`.
- **Omitted** (non-reasoning model): no `generationConfig.thinkingConfig` at all
  (Pi's `model.reasoning` guard). pipy already omits — preserved.
- **Pi-forced defaults vs upstream API defaults:** `includeThoughts: true` on the
  enabled path is a Pi-forced default (the REST API default is `false`/omitted).
  The disabled per-model configs (`thinkingLevel: "LOW"/"MINIMAL"`,
  `thinkingBudget: 0`) are Pi-forced because Gemini 3 models cannot fully disable
  thinking, so Pi pins the lowest supported level without `includeThoughts`
  rather than relying on omission. Cite: google.ts:415-427.
- **Derived identifiers:** none. Model family is derived from `model_id` by the
  pinned regexes; no new identifier is constructed.

## Effort source / mapping (pinned divergence, scoped)

Pi computes `effort` via `clampThinkingLevel(model, options.reasoning)` then
`clampedReasoning === "off" ? "high"`. pipy's catalog already resolves the
thinking level through `map_thinking_level(spec, thinking_level)` in
`resolve_construction`, producing `reasoning_effort`:

- For a google catalog model (which carries **no** `thinkingLevelMap`, matching
  Pi where google providers define none and the provider computes level/budget),
  `map_thinking_level` returns the requested level verbatim when supported —
  i.e. one of `minimal|low|medium|high`. This is exactly Pi's clamped `effort`
  for the dominant path.
- When thinking is off/unset, `map_thinking_level` returns `None` and the
  existing `thinking_disabled` flag is set for reasoning models — mirroring Pi's
  `!options.reasoning -> thinking.enabled=false -> getDisabledThinkingConfig`.
- **Scoped-out edge:** Pi's `clampedReasoning === "off" ? "high"` only triggers
  when a *truthy* `reasoning` value clamps to `"off"`. pipy never feeds a truthy
  `"off"` to this layer (the CLI/product normalize off/unset to `None`), so this
  branch is unreachable here; pipy takes the disabled path instead. Documented
  boundary, not a behavior pipy can produce.
- **Scoped-out:** custom per-effort `thinkingBudgets` (Pi's
  `options.thinkingBudgets`, settings-derived) — pipy's catalog construction has
  no such override surface, so it is not plumbed. Noted for a future slice.
- **Unknown effort guard:** if a google `models.json` provider *did* define a
  non-standard `thinkingLevelMap` yielding an effort outside
  `minimal|low|medium|high`, the budget helper returns `-1` (Gemini dynamic) and
  the level helper falls back to the default-family mapping by treating the
  effort as best-effort; no crash. This matches Pi's exhaustive-switch intent
  while staying fail-soft for pipy's open `models.json` universe.

## pipy current state

`src/pipy_harness/native/google_provider.py`: builds the body with `contents`,
`systemInstruction`, `tools`. Sends **no** `generationConfig` and **no** thinking
shape. Docstring on `extra_headers` explicitly says thinking "is intentionally
not injected here."

`src/pipy_harness/native/provider_construction.py`: `resolve_construction`
already computes `reasoning_effort` (set for google since it is not the OpenRouter
nested format) and `thinking_disabled` (reasoning model + off/unset). The
`google-generative-ai` branch of `build_provider` constructs
`GoogleGenerativeAIProvider` but passes **neither** `reasoning_effort` nor
`thinking_disabled`.

## Design

Mirror the existing anthropic pattern: construction computes the intent
(`reasoning_effort`, `thinking_disabled` — both already exist), the adapter owns
the wire shape. Add a small, self-contained google-thinking helper used by the
adapter.

1. **`google_provider.py` — module-level helpers** (faithful ports, stdlib only):
   - `_is_gemini3_pro(model_id)`, `_is_gemini3_flash(model_id)`,
     `_is_gemma4(model_id)` — the three pinned regexes on the lowercased id.
   - `_google_thinking_level(effort, model_id) -> str` — the per-family enum map.
   - `_google_thinking_budget(model_id, effort) -> int` — the per-family budget
     tables, else `-1`.
   - `_disabled_thinking_config(model_id) -> dict` — the per-model disabled shape.
   - `_build_thinking_config(model_id, reasoning_effort, thinking_disabled) ->
     dict | None`:
     - `reasoning_effort` truthy → enabled config: `{includeThoughts: True}` plus
       `thinkingLevel` (level families) or `thinkingBudget` (budget families).
     - else if `thinking_disabled` → `_disabled_thinking_config(model_id)`.
     - else → `None` (omit).
2. **`GoogleGenerativeAIProvider`** — add fields `reasoning_effort: str | None =
   None` and `thinking_disabled: bool = False` (both default-safe, so existing
   direct constructions and non-reasoning models keep omitting thinking). In
   `complete`, after building `body`, compute `_build_thinking_config(...)`; if
   not `None`, set `body["generationConfig"] = {"thinkingConfig": <config>}`
   (merge-safe if a `generationConfig` ever pre-exists; today it does not).
   Update the stale docstring line.
3. **`build_provider` google branch** — pass
   `reasoning_effort=resolved.reasoning_effort` and
   `thinking_disabled=resolved.thinking_disabled` to the adapter.

No new runtime dependencies; stdlib only. No `ResolvedConstruction` change needed
(both fields already exist). The vertex adapter and `_build_iam_provider` stay
untouched.

## Tests (TDD)

Focused unit tests in `tests/` (new `test_native_google_thinking.py` or extend
the existing google provider test):

- Enabled budget path: `gemini-2.5-pro` + `reasoning_effort="high"` →
  `body["generationConfig"]["thinkingConfig"] == {"includeThoughts": True,
  "thinkingBudget": 32768}`.
- Enabled budget tables: `2.5-flash` high → 24576; `2.5-flash-lite` minimal →
  512; an unknown budget model → `-1`.
- Enabled level path: a `gemini-3.1-pro` id + `medium` → `{"includeThoughts":
  True, "thinkingLevel": "HIGH"}`; `low` → `"LOW"`. A `gemini-3-flash` id +
  `minimal` → `"MINIMAL"`. A `gemma-4` id + `low` → `"MINIMAL"`.
- Disabled path: reasoning model id + `reasoning_effort=None,
  thinking_disabled=True` → gemini-2.5 → `{"thinkingBudget": 0}` (no
  `includeThoughts`); gemini-3.1-pro → `{"thinkingLevel": "LOW"}`; gemini-3-flash
  → `{"thinkingLevel": "MINIMAL"}`.
- Omit path: `reasoning_effort=None, thinking_disabled=False` (non-reasoning) →
  no `generationConfig`/`thinkingConfig` in the body; existing tests for the
  no-thinking body stay byte-identical.
- Helper unit tests for the regexes and tables.

## Conformance gate (item 21)

Extend `scripts/parity_checks/provider_catalog_conformance.py` 21a: capture a
google construction with `thinking_level="high"` for `gemini-2.5-pro` and assert
`g_sent["body"]["generationConfig"]["thinkingConfig"] == {"includeThoughts":
True, "thinkingBudget": 32768}`, keeping the existing URL/`?key=`/no-Authorization
assertions. Keep the original `thinking_level=None` assertion (no thinking
injected for the off/unset construction of the non-... — note gemini-2.5-pro is
reasoning, so `thinking_level=None` yields the **disabled** budget-0 config; pin
that explicitly as a separate sub-check).

## Done-when

- google enabled request carries `generationConfig.thinkingConfig` with the
  correct per-family level/budget + `includeThoughts: true`.
- google reasoning model with thinking off/unset carries the per-model disabled
  config (no `includeThoughts`).
- google non-reasoning model omits `generationConfig.thinkingConfig` entirely;
  pre-existing google body tests unchanged.
- `build_provider` threads `reasoning_effort` + `thinking_disabled` to the
  google adapter.
- vertex unchanged (item 22 follow-on).
- conformance item 21 extended and green; `just check` green.
- docs updated (provider-catalog.md, pi-mono-gap-audit.md, backlog.md, release
  notes) — strike the "google thinking is not injected" line, keep vertex as the
  remaining follow-on.

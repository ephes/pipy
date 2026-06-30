# google-vertex `generationConfig.thinkingConfig` injection (Pi parity)

Status: design/plan for one parity-loop slice. Source of truth for behavior:
Pi's `packages/ai/src/providers/google-vertex.ts` in `~/src/pi-mono`.

## Gap

`docs/pi-mono-gap-audit.md` §5 lists `google-vertex` thinking (its
`THINKING_LEVEL_MAP` variant) as the one remaining google thinking follow-on:
the `google-generative-ai` adapter now injects per-model
`generationConfig.thinkingConfig` (commit `9cc9b97`), but `google-vertex` does
not. `GoogleVertexProvider` carries no `reasoning_effort`/`thinking_disabled`
fields and never emits `thinkingConfig`; `provider_construction._build_iam_provider`
does not forward thinking into it (its docstring says "vertex thinking is
per-model and not yet injected").

Scope: make the `google-vertex` adapter emit Pi's per-model
`generationConfig.thinkingConfig` exactly as `google-vertex.ts` does, and have
catalog construction forward the resolved `reasoning_effort`/`thinking_disabled`
into it (the same threading already used for `google-generative-ai`). This is a
**provider request-shape** slice on the `google-vertex` path only. The
generative-ai path is already shipped and is not changed.

## Ownership boundary (where Pi computes the shape)

Pi computes the thinking shape **inside the provider**, not in catalog/model
metadata. `streamGoogleVertexResponses` (google-vertex.ts:300-329) maps the
clamped reasoning effort to either a `level` or a `budgetTokens` per model
family, and `buildParams` (google-vertex.ts:458-468) turns that into the wire
`thinkingConfig`. pipy already matches this boundary for generative-ai: catalog
construction resolves only `reasoning_effort` (effort string) and
`thinking_disabled` (bool); the adapter owns the per-family wire shape. The
vertex slice keeps the identical boundary — no new catalog fields.

## Pinned request-shape field list (the fields this slice changes)

This slice changes only `body["generationConfig"]["thinkingConfig"]` on the
`google-vertex` request. All other request fields (`contents`,
`systemInstruction`, `tools`, auth headers, endpoint) are already matched and
are **not** touched.

Pi reference: `buildParams` (google-vertex.ts:458-468):

```ts
if (options.thinking?.enabled && model.reasoning) {
    const thinkingConfig: ThinkingConfig = { includeThoughts: true };
    if (options.thinking.level !== undefined) {
        thinkingConfig.thinkingLevel = THINKING_LEVEL_MAP[options.thinking.level];
    } else if (options.thinking.budgetTokens !== undefined) {
        thinkingConfig.thinkingBudget = options.thinking.budgetTokens;
    }
    config.thinkingConfig = thinkingConfig;
} else if (model.reasoning && options.thinking && !options.thinking.enabled) {
    config.thinkingConfig = getDisabledThinkingConfig(model);
}
```

Resulting `thinkingConfig` shapes and optionality:

| State | Field | Optionality / value |
|---|---|---|
| thinking enabled, level family | `includeThoughts` | always `true` |
| thinking enabled, level family | `thinkingLevel` | one of `MINIMAL`/`LOW`/`MEDIUM`/`HIGH` |
| thinking enabled, budget family | `includeThoughts` | always `true` |
| thinking enabled, budget family | `thinkingBudget` | integer token count, or `-1` (dynamic) for unknown models |
| reasoning model, thinking off/unset | `thinkingLevel` **or** `thinkingBudget` | per-model disabled config (no `includeThoughts`) |
| non-reasoning model | (whole `thinkingConfig` omitted) | — |

### The three states, disambiguated

Pi's `buildParams` (google-vertex.ts:458-468) emits the disabled config **only**
when `model.reasoning && options.thinking && !options.thinking.enabled`, and the
stream wrapper (google-vertex.ts:301-306) always sets `thinking:{enabled:false}`
when no reasoning is requested. So for a **reasoning-capable** model:

- thinking active → enabled config;
- thinking off/unset → **disabled config is sent** (not omitted).

`thinkingConfig` is **omitted entirely** only when `model.reasoning` is false (a
non-reasoning model), since neither `buildParams` branch fires.

pipy mirrors this through the two adapter fields resolved by
`resolve_construction`: `reasoning_effort` (set only for a reasoning model with
active thinking) and `thinking_disabled` (`bool(spec.reasoning) and
reasoning_value is None and thinking off/unset`). The adapter helper resolves:

1. `reasoning_effort` truthy → enabled config;
2. else `thinking_disabled` truthy → disabled config;
3. else → omit (covers the non-reasoning model, and the bare-default adapter
   construction where neither field is set, e.g. direct/legacy construction).

This is exactly `google_provider._build_thinking_config`'s contract.

### `THINKING_LEVEL_MAP` is identity at the wire level

`THINKING_LEVEL_MAP` (google-vertex.ts:52-58) maps each `GoogleThinkingLevel`
key to the same-named `ThinkingLevel` enum member. The vendored SDK enum
(`~/src/pi-mono/node_modules/@google/genai/dist/genai.d.ts:10367-10388`)
serializes to the string values `"THINKING_LEVEL_UNSPECIFIED"`, `"MINIMAL"`,
`"LOW"`, `"MEDIUM"`, `"HIGH"`. So `THINKING_LEVEL_MAP["LOW"]` reaches the wire as
`"LOW"` — identical to the generative-ai path, which passes the
`GoogleThinkingLevel` string straight through (google.ts:373,
`thinkingLevel = options.thinking.level as any`). pipy therefore emits the bare
uppercase strings; no enum object is needed. The `thinkingConfig` is delegated
to the `@google/genai` SDK in Pi, but the wire field/value were pinned from the
SDK enum source above rather than inferred from the wrapper.

### Per-family routing (level vs budget)

`streamGoogleVertexResponses` (google-vertex.ts:300-329):

- Non-reasoning request (`!options.reasoning`): `thinking.enabled = false` →
  disabled branch.
- `effort = clampedReasoning === "off" ? "high" : clampedReasoning`
  (∈ `minimal`/`low`/`medium`/`high`).
- `isGemini3ProModel || isGemini3FlashModel` → `level =
  getGemini3ThinkingLevel(effort, model)`.
- everything else → `budgetTokens = getGoogleBudget(model, effort, ...)`.

`getGemini3ThinkingLevel` (google-vertex.ts:512-536): for a Gemini 3 **Pro**
model, `minimal`/`low` → `LOW`, `medium`/`high` → `HIGH`; otherwise (Gemini 3
Flash) `minimal`→`MINIMAL`, `low`→`LOW`, `medium`→`MEDIUM`, `high`→`HIGH`.

`getGoogleBudget` (google-vertex.ts:538-568): `2.5-pro` →
`{minimal:128, low:2048, medium:8192, high:32768}`; `2.5-flash` →
`{minimal:128, low:2048, medium:8192, high:24576}`; otherwise `-1`.

`getDisabledThinkingConfig` (google-vertex.ts:496-510): Gemini 3 Pro →
`{thinkingLevel: "LOW"}`; Gemini 3 Flash → `{thinkingLevel: "MINIMAL"}`;
otherwise (Gemini 2.x) → `{thinkingBudget: 0}`.

## Vertex-vs-generative-ai divergences (verified per path — do NOT reuse google_provider helpers blindly)

The audit groups "google thinking", but the two providers' wire logic diverges
in two verified places. The slice implements the **vertex** variant:

1. **No `2.5-flash-lite` special case.** `google.ts:getGoogleBudget`
   (google.ts:~430-490) checks `2.5-flash-lite` *before* `2.5-flash` and gives
   it `minimal: 512`. `google-vertex.ts:getGoogleBudget` has **no** flash-lite
   branch, so `gemini-2.5-flash-lite` matches the `2.5-flash` branch →
   `minimal: 128`. pipy's `google_provider._google_thinking_budget` has a
   separate flash-lite table (`minimal: 512`); the vertex helper must **not** —
   flash-lite gets the flash table (`minimal: 128`).
2. **No Gemma 4 branch.** `google.ts` special-cases `gemma-?4` in both the level
   set and the disabled config (→ `thinkingLevel: "MINIMAL"`). `google-vertex.ts`
   has **no** Gemma handling (Gemma is not a Vertex Gemini model). The vertex
   helpers must not reference Gemma 4: only Gemini 3 Pro/Flash use the level
   path; everything else uses the budget path; the disabled config has only the
   Gemini 3 Pro/Flash branches plus the `thinkingBudget: 0` fallback.

`getGemini3ThinkingLevel` is identical between the two files, so the level
mapping logic matches pipy's existing `_google_thinking_level` *minus* its
gemma4 branch (which is never reached for vertex anyway because gemma4 is not in
the vertex level set).

### Forced defaults vs upstream API defaults

- `includeThoughts: true` is a **Pi-forced default** on every enabled-thinking
  vertex request (google-vertex.ts:459). The Gemini API default is to omit
  thoughts; Pi always sets it on so thinking is surfaced. pipy matches.
- `thinkingBudget: -1` for unknown/non-table models is a **Pi-forced default**
  (google-vertex.ts:567 `return -1`), meaning Gemini "dynamic" budget rather
  than the API's model-specific default. pipy matches.
- Disabled config for Gemini 3 models is `thinkingLevel` with **no**
  `includeThoughts` (google-vertex.ts:496-509 comment: hidden thinking stays
  invisible), diverging from any API default of fully-off; Gemini 2.x disables
  via `thinkingBudget: 0`. pipy matches.

These divergence citations are scoped to the named functions above and are not
generalized beyond them.

## pipy constraints (AGENTS.md)

- No new runtime dependencies; stdlib + manual dict handling only (parity track
  invariant). The thinking shape is built with plain dicts, like
  `google_provider._build_thinking_config`.
- No secret material on archived fields; `thinkingConfig` carries no secrets.
- Match Pi behavior; remove no tests to pass a gate.

## Done-when

1. `GoogleVertexProvider` gains `reasoning_effort: str | None = None` and
   `thinking_disabled: bool = False` fields and emits
   `body["generationConfig"]["thinkingConfig"]` matching the vertex shape above,
   in **both** auth modes (Express api-key and ADC bearer).
2. Vertex-specific thinking helpers implement the two divergences (no flash-lite
   table, no gemma4) — verified against `google-vertex.ts`.
3. `provider_construction._build_iam_provider` forwards
   `resolved.reasoning_effort` and `resolved.thinking_disabled` into
   `GoogleVertexProvider`; the module docstring no longer says vertex thinking is
   uninjected.
4. Focused tests (`tests/test_native_google_vertex_thinking.py`) pin: enabled
   budget (2.5-pro high → 32768; 2.5-flash high → 24576; **2.5-flash-lite
   minimal → 128**, the divergence; unknown → -1), enabled level (gemini3 pro
   medium → HIGH, pro low → LOW, flash minimal → MINIMAL), disabled config per
   family (2.5-pro → budget 0; gemini3 pro → level LOW; gemini3 flash → level
   MINIMAL), omission for a non-reasoning model and for the bare-default adapter
   construction (neither field set), **no gemma4 special-case**
   (gemma model uses the budget path / -1, not a level), and that thinking is
   injected in both Express and ADC request modes.
5. Conformance gate item 22 grows a `22_vertex_thinking_config` check proving an
   enabled-thinking vertex construction sends `includeThoughts: true` + a budget
   for a 2.5 model.
6. Docs updated: `docs/pi-mono-gap-audit.md` §5 and `docs/backlog.md` strike the
   vertex-thinking follow-on; `docs/provider-catalog.md` records the shipped
   shape; `CHANGELOG.md` notes it; `provider_construction.py` docstring updated.
7. `just check` green; different-family review CLEAN over the full diff.

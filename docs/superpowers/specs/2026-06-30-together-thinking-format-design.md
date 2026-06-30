# Together thinking-format request shape — design/plan

Status: design + implementation plan for one parity slice.
Date: 2026-06-30. Branch: `main` (trunk).
Pi reference checkout: `~/src/pi-mono`.

## Gap (one paragraph)

For the `openai-completions` API family, Pi emits a Together-specific reasoning
request shape when a **reasoning-capable** model uses the **`together`** thinking
format: it always sets a top-level `reasoning: {enabled: <bool>}` object
(`enabled: true` when a reasoning level is active, `enabled: false` when
off/unset), and — **only** when the model `supportsReasoningEffort` — additionally
sets a top-level `reasoning_effort` on the on-state. pipy currently models the
`openrouter` nested-reasoning format and the `deepseek` `thinking:{type}` object;
a Together-format model (Pi keys it off provider `together` or an
`api.together.ai`/`api.together.xyz` base URL, or an explicit
`compat.thinkingFormat`) resolves to its own name and falls through to the
default OpenAI-style top-level `reasoning_effort`, so it gets a plain
`reasoning_effort` on-state and **no** `reasoning: {enabled}` object at all, and
no explicit disable when thinking is off. This slice adds the `together`
thinking-format request shape, directly mirroring the already-shipped `deepseek`
slice (same gate semantics, different body keys).

## Pi reference — exact field list

Source: `packages/ai/src/providers/openai-completions.ts`, the
`thinkingFormat === "together"` branch (lines 586–594):

```ts
} else if (compat.thinkingFormat === "together" && model.reasoning) {
    const togetherParams = params as Omit<typeof params, "reasoning_effort"> & {
        reasoning?: { enabled: boolean };
        reasoning_effort?: string;
    };
    togetherParams.reasoning = { enabled: !!options?.reasoningEffort };
    if (options?.reasoningEffort && compat.supportsReasoningEffort) {
        togetherParams.reasoning_effort = model.thinkingLevelMap?.[options.reasoningEffort] ?? options.reasoningEffort;
    }
}
```

`options?.reasoningEffort` is the **post-clamp** reasoning value (same as the
deepseek slice: `clampedReasoning = options?.reasoning ?
clampThinkingLevel(model, options.reasoning) : undefined`, then `reasoningEffort =
clampedReasoning === "off" ? undefined : clampedReasoning`). For Pi it is either a
supported level or `undefined` (off/unset).

`thinkingFormat`/`supportsReasoningEffort` come from `getCompat`
(openai-completions.ts:1157–1183 = explicit `model.compat.*` `??` `detectCompat`):

- `detectCompat` (lines 1080–1082, 1126–1136): `isTogether = provider ===
  "together" || baseUrl.includes("api.together.ai") ||
  baseUrl.includes("api.together.xyz")`; `thinkingFormat = isDeepSeek ?
  "deepseek" : isZai ? "zai" : isTogether ? "together" : isAntLing ? "ant-ling" :
  isOpenRouter ? "openrouter" : "openai"`. So Together's own provider/base URL
  **auto-detects** to `thinkingFormat="together"`.
- `supportsReasoningEffort` (lines 1118–1119): `!isGrok && !isZai && !isMoonshot
  && !isTogether && !isCloudflareAiGateway && !isNvidia && !isAntLing`. This is
  resolved **independently** of `thinkingFormat` (getCompat resolves each field
  separately). Together **is** in the exclusion set (`!isTogether`), so a
  *detected* together model has `supportsReasoningEffort = false`: Pi emits
  `reasoning: {enabled: true}` **without** `reasoning_effort`. `reasoning_effort`
  rides along **only** when something flips `supportsReasoningEffort` back to true
  — i.e. an explicit `compat.supportsReasoningEffort = true`, or an explicit
  `compat.thinkingFormat="together"` placed on a provider/base URL that is **not**
  in the exclusion set (then `thinkingFormat="together"` **and**
  `supportsReasoningEffort=true`). This is the inverse of the deepseek case
  (deepseek auto-detects `supportsReasoningEffort=true`; together auto-detects
  `false`), and it is exactly the "explicit-format-on-non-excluded-provider"
  mismatch the skill body warns about.

  The exclusion predicates (detectCompat lines 1079–1110):

  | flag | provider name | base-URL substring(s) |
  | --- | --- | --- |
  | isGrok | `xai` | `api.x.ai` |
  | isZai | `zai` | `api.z.ai` |
  | isMoonshot | `moonshotai`, `moonshotai-cn` | `api.moonshot.` |
  | isTogether | `together` | `api.together.ai`, `api.together.xyz` |
  | isCloudflareAiGateway | `cloudflare-ai-gateway` | `gateway.ai.cloudflare.com` |
  | isNvidia | `nvidia` | `integrate.api.nvidia.com` |
  | isAntLing | `ant-ling` | `api.ant-ling.com` |

  (This `_NO_REASONING_EFFORT_SIGNALS` table and the `_supports_reasoning_effort`
  predicate already exist in pipy from the deepseek slice; this slice **reuses**
  them unchanged.)

### The fields this slice changes (Together thinking-format path only)

This slice changes exactly two body keys, only for a reasoning-capable model that
resolves to the `together` thinking format:

| Body key | Pi value | Optionality |
| --- | --- | --- |
| `reasoning` (object) | `{enabled: true}` when a level is active; `{enabled: false}` when off/unset | emitted for **every** reasoning-capable together-format request (on **and** off); never for non-reasoning models |
| `reasoning_effort` (top-level string) | `thinkingLevelMap?.[level] ?? level` | **only** on-state **and** `supportsReasoningEffort` (auto-`false` for Together); omitted otherwise |

Gate semantics (identical to the shipped deepseek path, body keys aside):

| Aspect | Pi | pipy |
| --- | --- | --- |
| Model reasoning-capable | `model.reasoning` truthy | `bool(spec.reasoning)`; else emit nothing |
| On-state (`enabled: true`) | `options?.reasoningEffort` truthy (post-clamp) | `reasoning_value is not None` |
| Off-state (`enabled: false`) | `!options?.reasoningEffort` (clamped to off/unset) | reasoning-capable **and** `reasoning_value is None` **and** raw `thinking_level` in `{None, "off"}` |
| `reasoning_effort` add | on-state **and** `supportsReasoningEffort` | on-state **and** `_supports_reasoning_effort(spec)` (existing faithful exclusion predicate, **not** a blanket `True`) |

### Forced default vs upstream API default

- The off-state `reasoning: {enabled: false}` is a **Pi-forced default**: Pi
  explicitly disables Together reasoning rather than omitting the field and
  letting the Together API apply its own per-model behavior. pipy must match this
  forced emission, exactly as the shipped `deepseek` `{type:"disabled"}`,
  `openrouter` `{effort:"none"}`, and `anthropic-messages` `{type:"disabled"}`
  off-states do. Cited scope: the `togetherParams.reasoning = { enabled:
  !!options?.reasoningEffort }` assignment (line 590) — unconditional for a
  reasoning together model, both sub-states.
- The on-state `reasoning_effort` value is the **model-mapped** value
  (`thinkingLevelMap?.[level] ?? level`), matching the convention pipy already
  uses (`reasoning_value = map_thinking_level(spec, level)` is the mapped value).

### Derived identifiers

- None. No new model id, base URL, or auth identifier is derived. Together-format
  detection reuses provider name / base URL / explicit `compat.thinkingFormat`,
  the same signals the shipped deepseek/openrouter detection uses.

## Pi predicate / known divergence: unsupported clamped level

Pi *clamps* an unsupported requested level up to a supported one (so a together
model still emits `reasoning:{enabled:true}`). pipy does **not** clamp:
`map_thinking_level` returns `None` for an unsupported level. pipy's existing
deepseek/openrouter/anthropic slices deliberately gate the off-state on the
**raw** level (`thinking_level is None or thinking_level == "off"`) so an
unsupported level emits **neither** the on-state nor the off-state (it is
"still-thinking-and-clamp" per Pi, not off). This slice keeps that exact
pre-existing divergence for consistency (covered by an explicit test, mirroring
`test_deepseek_unsupported_level_emits_neither`). Closing the no-clamp divergence
is a separate, cross-cutting gap and is **not** in scope.

## pipy mapping

In `src/pipy_harness/native/provider_construction.py`, add a `together` branch
to the existing resolved-format thinking block (after the `deepseek` branch,
before the default `elif reasoning_value is not None`). The branch reuses the
already-present `_supports_reasoning_effort(spec)` helper (no new predicate):

```python
elif thinking_format == "together" and bool(spec.reasoning):
    # ``reasoning: {enabled: bool}`` is emitted for every reasoning-capable
    # Together request; ``reasoning_effort`` rides along on the on-state only
    # when the model supports it (openai-completions.ts:586-594). Together's own
    # provider/base URL auto-detects supportsReasoningEffort=False, so the
    # on-state normally omits reasoning_effort unless an explicit compat flag
    # (or an explicit thinkingFormat="together" on a non-excluded provider)
    # flips it back on.
    if reasoning_value is not None:
        body_extra["reasoning"] = {"enabled": True}
        if _supports_reasoning_effort(spec):
            reasoning_effort = reasoning_value
    elif thinking_off:
        body_extra["reasoning"] = {"enabled": False}
```

Extend `_resolve_thinking_format` to detect Together (mirroring Pi's
`detectCompat` `isTogether`), inserted **before** the openrouter check to match
Pi's `thinkingFormat` precedence order (`isDeepSeek` > `isZai` > `isTogether` >
`isAntLing` > `isOpenRouter` > `"openai"`; openai-completions.ts:1126–1136 —
Together is evaluated **before** OpenRouter):

```python
if provider == "deepseek" or "deepseek.com" in base_url:
    return "deepseek"
if provider == "together" or "api.together.ai" in base_url or "api.together.xyz" in base_url:
    return "together"
if provider == "openrouter" or "openrouter.ai" in base_url:
    return "openrouter"
return "openai"
```

Pi's chain places `isTogether` ahead of `isOpenRouter`, so the new together check
must precede the existing openrouter check; otherwise a row matching both signals
(e.g. an `openrouter.ai` base URL combined with a `together` provider name) would
resolve to `openrouter` in pipy but `together` in Pi. The `zai`/`ant-ling` rungs
of Pi's chain sit between together and openrouter but are deferred follow-ons in
pipy (they fall through to the `"openai"` default), so the faithful pipy order is
deepseek → together → openrouter → openai.

(Explicit `compat.thinkingFormat` still wins over all detection — the existing
first branch of `_resolve_thinking_format` is unchanged, so getCompat precedence
is preserved.)

Note: the still-unimplemented formats (`zai`, `qwen`, `qwen-chat-template`,
`ant-ling`, `string-thinking`) continue to resolve via `_resolve_thinking_format`
to their own name (or `"openai"` for the never-auto-detected ones) and fall
through to the default `elif reasoning_value is not None` branch (top-level
`reasoning_effort`), identical to today's behavior — a documented deferral, no
regression.

The `together`-format on-state sets `body_extra["reasoning"]` and (when
supported) `reasoning_effort`; the completions adapter already writes `extra_body`
entries and `reasoning_effort` to the request body, so both reach the wire with
no adapter change.

## Adjacent Pi fields explicitly scoped OUT of this slice

- **Other `thinkingFormat` variants** (`zai`, `qwen`, `qwen-chat-template`,
  `ant-ling`, `string-thinking`) and a full `detectCompat` port. Still deferred
  follow-ons; they keep their current default behavior.
- **`useMaxTokens` / `maxTokensField`** (Together uses `max_tokens`, not
  `max_completion_tokens`), **`supportsStore`**, **`supportsStrictMode`**,
  **`supportsLongCacheRetention`**, **`zaiToolStream`**, and the `isNonStandard`
  family — these are *separate* `detectCompat` compat fields on the same Together
  request path, **not** thinking-shape fields. They are independent gaps, out of
  scope here; this slice changes only the `reasoning`/`reasoning_effort` thinking
  shape, exactly as the deepseek/openrouter slices were scoped.
- **No-clamp divergence** for unsupported levels (see above). Separate gap.
- The **deepseek**/**openrouter** emissions and the default OpenAI-style on-state
  (`reasoning_effort = mapped`) are unchanged.

## Implementation tasks (ordered, testable)

1. **TDD red.** Add focused tests in `tests/test_native_provider_construction.py`
   (a `_together_spec` helper: `provider_name="together"`,
   `api="openai-completions"`, `base_url="https://api.together.xyz/v1"`,
   `reasoning=True`, `thinking_level_map={"high": "high"}`):
   - on-state `thinking_level="high"` (auto-detected together,
     `supportsReasoningEffort` False) → `body_extra["reasoning"] ==
     {"enabled": True}` **and** `reasoning_effort is None`.
   - off/unset `thinking_level=None` and `"off"` → `body_extra["reasoning"] ==
     {"enabled": False}` **and** `reasoning_effort is None`.
   - non-reasoning together row → no `reasoning` key, no `reasoning_effort`.
   - explicit `compat.supportsReasoningEffort=True` + on-state → `reasoning ==
     {"enabled": True}` **and** `reasoning_effort == "high"` (proves the
     secondary flag is honored, gating `reasoning_effort` on it).
   - explicit `compat.thinkingFormat="together"` on a **non-excluded** base URL
     (e.g. `https://api.openai.com/v1`) + on-state → `reasoning ==
     {"enabled": True}` **and** `reasoning_effort == "high"` (proves
     `supportsReasoningEffort` is resolved **independently** of `thinkingFormat`;
     the explicit-format-on-non-excluded-provider mismatch).
   - unsupported level (clamped away → `reasoning_value None`, raw level not off)
     → **neither** `reasoning` nor `reasoning_effort` (documents the no-clamp
     divergence).
   - end-to-end adapter test (mirror
     `test_deepseek_thinking_reaches_request_body`): on-state and off-state
     `reasoning` reach the request body through `build_provider`.
   - deepseek/openrouter regression guard: an explicit
     `compat.thinkingFormat="deepseek"` on an `api.together.xyz` base URL still
     uses the `thinking:{type}` object (proves getCompat precedence: explicit
     compat wins over the new together base-URL detection) — already covered by
     `test_deepseek_explicit_format_on_excluded_provider_omits_effort`; confirm it
     stays green.
   *Acceptance:* the new together tests fail before the code change; existing
   deepseek/openrouter/anthropic/default tests stay green.
2. **Implement** the `together` branch + the `_resolve_thinking_format` together
   detection in `provider_construction.py`. *Acceptance:* new tests green; all
   existing construction tests unchanged.
3. **Conformance gate.** Add an `18i` check to
   `scripts/parity_checks/provider_catalog_conformance.py`: a models.json Together
   provider (`api="openai-completions"`, `baseUrl` `…api.together.xyz…`, a
   reasoning model) asserts on-state `reasoning == {"enabled": True}` + **no**
   `reasoning_effort` (auto-detected `supportsReasoningEffort` False), and
   off-state `reasoning == {"enabled": False}` + no `reasoning_effort`.
   *Acceptance:*
   `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`
   passes.
4. **Docs.** Update `docs/provider-catalog.md` (add a Together shipped note;
   strike `together` from the deferred thinkingFormat lists in the openrouter and
   deepseek bullets), `docs/pi-mono-gap-audit.md` (the deferred thinkingFormat
   lists in items 3 and 5), and `docs/backlog.md`. *Acceptance:* docs describe the
   behavior; the slice is struck from the gap source.

## Done-when

- `just check` green.
- `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`
  passes.
- Different-family review (pi-review-loop) CLEAN over the full code+docs diff in
  the same iteration.
- A reasoning-capable Together-format model emits `reasoning: {enabled: true}`
  (and `reasoning_effort` only when `supportsReasoningEffort`, auto-`false` for
  Together) on-state and `reasoning: {enabled: false}` (no `reasoning_effort`)
  off/unset; non-reasoning rows emit neither; an unsupported clamped level emits
  neither; an explicit `supportsReasoningEffort=True` (or
  `thinkingFormat="together"` on a non-excluded provider) adds `reasoning_effort`;
  the shipped deepseek/openrouter/default/anthropic paths are unchanged.

## Constraints (AGENTS.md)

- stdlib-only, no new runtime dependencies; reuse the existing
  `ProviderRequest`/`ProviderResult`/construction boundary and the existing
  `_supports_reasoning_effort` / `_NO_REASONING_EFFORT_SIGNALS` predicate.
- No secret leakage into results/metadata (covered by existing check 18f).
- Match Pi behavior through pipy-owned Python boundaries; this is not a literal
  TS port.
- No deprecation shims / pipy-only accretions.

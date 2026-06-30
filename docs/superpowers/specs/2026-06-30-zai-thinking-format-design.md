# Z.ai (zai) thinking-format request shape — design/plan

Status: design + implementation plan for one parity slice.
Date: 2026-06-30. Branch: `main` (trunk).
Pi reference checkout: `~/src/pi-mono`.

## Gap (one paragraph)

For the `openai-completions` API family, Pi emits a Z.ai-specific reasoning
request shape when a **reasoning-capable** model resolves to the **`zai`**
thinking format: it sets a single top-level boolean `enable_thinking`
(`true` when a reasoning level is active, `false` when off/unset) and emits
**no** `reasoning_effort` at all. pipy currently models the `openrouter`
nested-reasoning format, the `deepseek` `thinking:{type}` object, and the
`together` `reasoning:{enabled}` object; a Z.ai-format model (Pi keys it off
provider `zai` or an `api.z.ai` base URL, or an explicit
`compat.thinkingFormat="zai"`) is **not** detected by pipy's
`_resolve_thinking_format` and falls through to the default OpenAI-style
top-level `reasoning_effort`, so a Z.ai model wrongly gets a plain
`reasoning_effort` on-state and **no** `enable_thinking` flag at all, and no
explicit off-state when thinking is off. This slice adds the `zai`
thinking-format request shape, mirroring the already-shipped `deepseek` and
`together` slices in structure (same reasoning-capable gate and on/off
semantics) but with a distinct, simpler body shape.

## Pi reference — exact field list

Source: `packages/ai/src/providers/openai-completions.ts`, the
`thinkingFormat === "zai"` branch (lines 556–557):

```ts
if (compat.thinkingFormat === "zai" && model.reasoning) {
    (params as any).enable_thinking = !!options?.reasoningEffort;
}
```

`options?.reasoningEffort` is the **post-clamp** reasoning value (same as the
deepseek/together slices: `clampedReasoning = options?.reasoning ?
clampThinkingLevel(model, options.reasoning) : undefined`, then `reasoningEffort =
clampedReasoning === "off" ? undefined : clampedReasoning`). For Pi it is either a
supported level or `undefined` (off/unset). `!!options?.reasoningEffort` is
therefore `true` on-state and `false` off/unset.

`thinkingFormat` comes from `getCompat`
(openai-completions.ts:1157–1183 = explicit `model.compat.*` `??` `detectCompat`):

- `detectCompat` (lines 1079, 1126–1136): `isZai = provider === "zai" ||
  baseUrl.includes("api.z.ai")`; `thinkingFormat = isDeepSeek ? "deepseek" :
  isZai ? "zai" : isTogether ? "together" : isAntLing ? "ant-ling" : isOpenRouter
  ? "openrouter" : "openai"`. So Z.ai's own provider/base URL **auto-detects** to
  `thinkingFormat="zai"`.

### Per-field compat-flag gating

| Field | Gated by | How that flag resolves |
| --- | --- | --- |
| `enable_thinking` | `thinkingFormat === "zai"` **and** `model.reasoning` | `thinkingFormat`: explicit `compat.thinkingFormat` wins, else `detectCompat`'s `isDeepSeek?…:isZai?"zai":…` chain (isZai = provider `zai` / `api.z.ai`). `model.reasoning`: the row's reasoning capability. |

**No secondary flag.** Unlike the `deepseek`/`together` branches, the `zai`
branch **never** reads `supportsReasoningEffort` and **never** emits a top-level
`reasoning_effort`. `supportsReasoningEffort` is resolved independently by
`getCompat` (and `detectCompat` does set it `false` for `isZai` via the
`!isZai` exclusion), but the `zai` thinking-format branch does not consult it,
so it has **no effect** on the emitted shape. There is therefore no
explicit-format-on-excluded-provider `reasoning_effort` mismatch to test for this
format (the `zai` branch emits the same `enable_thinking` boolean regardless of
`supportsReasoningEffort`); the relevant precedence test for this slice is the
**detection-chain position** test below, not a secondary-flag test.

### The fields this slice changes (zai thinking-format path only)

This slice changes exactly one body key, only for a reasoning-capable model that
resolves to the `zai` thinking format:

| Body key | Pi value | Optionality |
| --- | --- | --- |
| `enable_thinking` (top-level bool) | `true` when a level is active; `false` when off/unset | emitted for **every** reasoning-capable zai-format request (on **and** off); never for non-reasoning models |

No `reasoning_effort` is ever emitted by this branch (contrast deepseek/together,
which add it on-state when `supportsReasoningEffort`).

Gate semantics (the reasoning-capable gate and on/off keying are identical to the
shipped deepseek/together paths; the emitted key differs):

| Aspect | Pi | pipy |
| --- | --- | --- |
| Model reasoning-capable | `model.reasoning` truthy | `bool(spec.reasoning)`; else emit nothing |
| On-state (`enable_thinking: true`) | `options?.reasoningEffort` truthy (post-clamp) | `reasoning_value is not None` |
| Off-state (`enable_thinking: false`) | `!options?.reasoningEffort` (clamped to off/unset) | reasoning-capable **and** `reasoning_value is None` **and** raw `thinking_level` in `{None, "off"}` |
| `reasoning_effort` | never emitted by the zai branch | never set in the zai branch |

### Forced default vs upstream API default

- The off-state `enable_thinking: false` is a **Pi-forced default**: Pi
  explicitly disables Z.ai thinking rather than omitting the field and letting the
  Z.ai API apply its own per-model behavior. pipy must match this forced emission,
  exactly as the shipped `deepseek` `{type:"disabled"}`, `together`
  `{enabled:false}`, `openrouter` `{effort:"none"}`, and `anthropic-messages`
  `{type:"disabled"}` off-states do. Cited scope: the `(params as
  any).enable_thinking = !!options?.reasoningEffort` assignment (line 557) —
  unconditional for a reasoning zai model, both sub-states. The off value here is
  the literal boolean `false`; it does **not** consult `thinkingLevelMap.off` (the
  way the `openrouter`/`string-thinking` off-states do), so there is no
  `null`-suppression case for this format.

### Derived identifiers

- None. No new model id, base URL, or auth identifier is derived. Z.ai-format
  detection reuses provider name / base URL / explicit `compat.thinkingFormat`,
  the same signals the shipped deepseek/together/openrouter detection uses.

## Pi predicate / known divergence: unsupported clamped level

Pi *clamps* an unsupported requested level up to a supported one (so a zai model
still emits `enable_thinking: true`). pipy does **not** clamp:
`map_thinking_level` returns `None` for an unsupported level. pipy's existing
deepseek/together/openrouter/anthropic slices deliberately gate the off-state on
the **raw** level (`thinking_level is None or thinking_level == "off"`) so an
unsupported level emits **neither** the on-state nor the off-state (it is
"still-thinking-and-clamp" per Pi, not off). This slice keeps that exact
pre-existing divergence for consistency (covered by an explicit test, mirroring
`test_together_unsupported_level_emits_neither`). Closing the no-clamp divergence
is a separate, cross-cutting gap and is **not** in scope.

## pipy mapping

In `src/pipy_harness/native/provider_construction.py`, add a `zai` branch to the
existing resolved-format thinking block. Per Pi's branch order
(zai → qwen → qwen-chat-template → deepseek → openrouter → ant-ling → together →
string-thinking → default), the in-code `elif` ordering between mutually
exclusive `thinking_format` string values does not affect resolution (a model has
exactly one `thinking_format`), so the new branch may be placed alongside the
existing format branches; this plan places it **before** the `deepseek` branch to
read top-to-bottom like Pi. The branch consults no secondary flag:

```python
elif thinking_format == "zai" and bool(spec.reasoning):
    # ``enable_thinking: bool`` is the entire Z.ai thinking shape: true when a
    # reasoning level is active, false when off/unset, for every
    # reasoning-capable zai-format request (openai-completions.ts:556-557). The
    # zai branch emits NO reasoning_effort and never consults
    # supportsReasoningEffort (unlike deepseek/together).
    if reasoning_value is not None:
        body_extra["enable_thinking"] = True
    elif thinking_off:
        body_extra["enable_thinking"] = False
```

Extend `_resolve_thinking_format` to detect Z.ai (mirroring Pi's `detectCompat`
`isZai`), inserted **before** the `together` and `openrouter` checks to match
Pi's `thinkingFormat` precedence order (`isDeepSeek` > `isZai` > `isTogether` >
`isAntLing` > `isOpenRouter` > `"openai"`; openai-completions.ts:1126–1136 — Z.ai
is evaluated **before** Together and OpenRouter):

```python
if provider == "deepseek" or "deepseek.com" in base_url:
    return "deepseek"
if provider == "zai" or "api.z.ai" in base_url:
    return "zai"
if provider == "together" or "api.together.ai" in base_url or "api.together.xyz" in base_url:
    return "together"
if provider == "openrouter" or "openrouter.ai" in base_url:
    return "openrouter"
return "openai"
```

Pi's chain places `isZai` ahead of `isTogether` and `isOpenRouter`, so the new
zai check must precede both existing checks; otherwise a row matching two signals
(e.g. a `zai` provider name combined with an `api.together.xyz` or `openrouter.ai`
base URL) would resolve to `together`/`openrouter` in pipy but `zai` in Pi. The
`ant-ling` rung of Pi's chain sits between `together` and `openrouter` but is a
deferred follow-on in pipy (it falls through to the `"openai"` default), so the
faithful pipy order is deepseek → zai → together → openrouter → openai. The
deferred `ant-ling` rung must not silently reorder the rungs pipy does implement;
inserting `zai` between `deepseek` and `together` preserves the relative order of
every implemented rung.

(Explicit `compat.thinkingFormat` still wins over all detection — the existing
first branch of `_resolve_thinking_format` is unchanged, so getCompat precedence
is preserved.)

Note: the still-unimplemented formats (`qwen`, `qwen-chat-template`, `ant-ling`,
`string-thinking`) continue to resolve via `_resolve_thinking_format` to their own
name (or `"openai"` for the never-auto-detected ones, and the never-auto-detected
`ant-ling`'s own name only via explicit compat) and fall through to the default
`elif reasoning_value is not None` branch (top-level `reasoning_effort`), identical
to today's behavior — a documented deferral, no regression.

The `zai`-format on-state sets `body_extra["enable_thinking"]`; the completions
adapter already writes `extra_body` entries to the request body, so it reaches the
wire with no adapter change.

## Adjacent Pi fields explicitly scoped OUT of this slice

- **Other `thinkingFormat` variants** (`qwen`, `qwen-chat-template`, `ant-ling`,
  `string-thinking`) and a full `detectCompat` port. Still deferred follow-ons;
  they keep their current default behavior. (`qwen` shares the identical
  `enable_thinking` shape but is **explicit-compat-only** — Pi's `detectCompat`
  has no `qwen` rung — so it is intentionally not included here; this slice is the
  single auto-detected `zai` format.)
- **`useMaxTokens` / `maxTokensField`**, **`supportsStore`**,
  **`supportsStrictMode`**, **`supportsLongCacheRetention`**, **`zaiToolStream`**,
  and the `isNonStandard` family — these are *separate* `detectCompat` compat
  fields on the same Z.ai request path, **not** thinking-shape fields. They are
  independent gaps, out of scope here; this slice changes only the
  `enable_thinking` thinking shape, exactly as the deepseek/together/openrouter
  slices were scoped.
- **No-clamp divergence** for unsupported levels (see above). Separate gap.
- The **deepseek**/**together**/**openrouter** emissions and the default
  OpenAI-style on-state (`reasoning_effort = mapped`) are unchanged.

## Implementation tasks (ordered, testable)

1. **TDD red.** Add focused tests in `tests/test_native_provider_construction.py`
   (a `_zai_spec` helper: `provider_name="zai"`, `api="openai-completions"`,
   `base_url="https://api.z.ai/api/paas/v4"`, `reasoning=True`,
   `thinking_level_map={"high": "high"}`):
   - on-state `thinking_level="high"` (auto-detected zai) → `body_extra
     == {"enable_thinking": True}` semantics, i.e.
     `body_extra["enable_thinking"] is True` **and** `reasoning_effort is None`.
   - off/unset `thinking_level=None` and `"off"` → `body_extra["enable_thinking"]
     is False` **and** `reasoning_effort is None`.
   - non-reasoning zai row (`reasoning=False`, `thinking_level=None`) → no
     `enable_thinking` key, no `reasoning_effort`.
   - explicit `compat.supportsReasoningEffort=True` + on-state → still
     `enable_thinking is True` **and** `reasoning_effort is None` (proves the zai
     branch ignores `supportsReasoningEffort` and never emits `reasoning_effort`,
     unlike together/deepseek).
   - unsupported level (clamped away → `reasoning_value None`, raw level not off)
     → **neither** `enable_thinking` nor `reasoning_effort` (documents the
     no-clamp divergence).
   - end-to-end adapter test (mirror
     `test_together_thinking_reaches_request_body`): on-state and off-state
     `enable_thinking` reach the request body through `build_provider`.
   - precedence test: a `zai` provider on an `openrouter.ai` base URL (and a
     second on `api.together.xyz`) resolves to the zai shape
     (`enable_thinking`), not the openrouter nested `reasoning:{effort}` or the
     together `reasoning:{enabled}` — proves the zai rung precedes both in
     pipy's chain (mirrors `test_together_detection_precedes_openrouter`).
   - getCompat precedence guard: an explicit `compat.thinkingFormat="openrouter"`
     on an `api.z.ai` base URL uses the openrouter nested reasoning object, not
     `enable_thinking` (explicit compat wins over the new zai base-URL detection).
   *Acceptance:* the new zai tests fail before the code change; existing
   deepseek/together/openrouter/anthropic/default tests stay green.
2. **Implement** the `zai` branch + the `_resolve_thinking_format` zai detection
   in `provider_construction.py`. *Acceptance:* new tests green; all existing
   construction tests unchanged.
3. **Conformance gate.** Add an `18j` check to
   `scripts/parity_checks/provider_catalog_conformance.py`: a models.json Z.ai
   provider (`api="openai-completions"`, `baseUrl` `…api.z.ai…`, a reasoning
   model) asserts on-state `enable_thinking == True` + **no** `reasoning_effort`,
   and off-state `enable_thinking == False` + no `reasoning_effort`.
   *Acceptance:*
   `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`
   passes.
4. **Docs.** Update `docs/provider-catalog.md` (add a Z.ai shipped note; strike
   `zai` from the deferred thinkingFormat lists), `docs/pi-mono-gap-audit.md` (the
   deferred thinkingFormat lists in items 3 and 5), and `docs/backlog.md`.
   *Acceptance:* docs describe the behavior; the slice is struck from the gap
   source.

## Done-when

- `just check` green.
- `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`
  passes.
- Different-family review (pi-review-loop) CLEAN over the full code+docs diff in
  the same iteration.
- A reasoning-capable Z.ai-format model emits `enable_thinking: true` on-state and
  `enable_thinking: false` off/unset, with **no** `reasoning_effort` in either
  state; non-reasoning rows emit neither; an unsupported clamped level emits
  neither; an explicit `supportsReasoningEffort=True` does **not** add
  `reasoning_effort`; a zai row on an openrouter.ai/together base URL resolves to
  the zai shape; the shipped deepseek/together/openrouter/default/anthropic paths
  are unchanged.

## Constraints (AGENTS.md)

- stdlib-only, no new runtime dependencies; reuse the existing
  `ProviderRequest`/`ProviderResult`/construction boundary.
- No secret leakage into results/metadata (covered by existing check 18f).
- Match Pi behavior through pipy-owned Python boundaries; this is not a literal
  TS port.
- No deprecation shims / pipy-only accretions.

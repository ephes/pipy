# ant-ling reasoning request shape — design (parity gap)

## Gap (single reviewable slice)

Port Pi's `openai-completions` `thinkingFormat === "ant-ling"` request shape to
pipy. This is the next remaining completions `thinkingFormat` variant after
`deepseek`/`together`/`zai`/`qwen`/`qwen-chat-template` already shipped. Unlike
`qwen`/`string-thinking`, `ant-ling` **is** an auto-detected rung in Pi's
`detectCompat` `thinkingFormat` chain (`isAntLing`), so this slice adds both a
detection rung (at its Pi-faithful chain position) **and** a request-shape
branch. `string-thinking` and a full `detectCompat` port remain separate
follow-ons and are explicitly out of scope.

Reference: `~/src/pi-mono/packages/ai/src/providers/openai-completions.ts`.

## Pi reference — pinned behavior

### Request-shape branch (openai-completions.ts:581-585)

```ts
} else if (compat.thinkingFormat === "ant-ling" && model.reasoning && options?.reasoningEffort) {
    const effort = model.thinkingLevelMap?.[options.reasoningEffort];
    if (typeof effort === "string") {
        (params as typeof params & { reasoning?: { effort: string } }).reasoning = { effort };
    }
}
```

Pinned semantics, field by field:

- **Branch gate** is `compat.thinkingFormat === "ant-ling" && model.reasoning &&
  options?.reasoningEffort`. The `&& options?.reasoningEffort` is part of the
  branch condition itself — so the branch only fires in the **on-state** (a level
  was requested). There is **NO off-state emission at all**: when reasoning is
  off/unset, ant-ling emits nothing (no `reasoning`, no `reasoning_effort`,
  no disabled marker). This is unlike `deepseek`/`together`/`zai`/`qwen`
  (which all emit an explicit off/disable shape) and unlike `openrouter`/
  `string-thinking` (which emit a mapped off value). ant-ling is the only
  emitting completions variant with a fully silent off-state.
- **Emitted field**: `reasoning: { effort: <string> }` — a single nested object,
  the only field this branch adds.
- **Value source**: `effort = model.thinkingLevelMap?.[options.reasoningEffort]`
  — the **RAW** `thinkingLevelMap` lookup keyed by the requested level. There is
  **NO `?? options.reasoningEffort` fallback** here (contrast deepseek/together/
  openrouter, which all use `... ?? options.reasoningEffort`). The object is
  emitted **only** when `typeof effort === "string"`. So:
  - map present and `map[level]` is a string -> emit `reasoning: {effort: map[level]}`.
  - map present and `map[level]` is `null`/absent -> emit nothing.
  - **map absent entirely -> emit nothing** (raw lookup is `undefined`, not a
    string). This is the crux divergence vs. pipy's `map_thinking_level`, which
    falls back to the raw requested level when a model declares no map.
- **`supportsReasoningEffort` is NEVER consulted** by this branch (no top-level
  `reasoning_effort` is ever emitted). ant-ling **is** in `detectCompat`'s
  `supportsReasoningEffort` exclusion list (`!isAntLing`, line 1119), but the
  branch never reads that flag — so the exclusion is irrelevant to the emitted
  shape, the same INVERSE trap already handled for `zai`/`qwen`.

### Detection chain (openai-completions.ts:1126-1136)

```ts
thinkingFormat: isDeepSeek ? "deepseek"
  : isZai ? "zai"
  : isTogether ? "together"
  : isAntLing ? "ant-ling"
  : isOpenRouter ? "openrouter"
  : "openai"
```

with `isAntLing = provider === "ant-ling" || baseUrl.includes("api.ant-ling.com")`
(line 1087). The Pi-faithful position is **after `together`, before
`openrouter`**. So pipy's `_resolve_thinking_format` chain must place the new
ant-ling rung between the existing `together` rung and the `openrouter` rung. The
deferred `string-thinking` variant has no `detectCompat` rung and stays a
default fall-through — it does not affect ordering.

Explicit `model.compat.thinkingFormat` still wins over detection (getCompat,
line 1174) — already handled by the resolver's existing first branch.

## Ownership boundary

This is provider-local request-shape logic computed at construction time in pipy
(`src/pipy_harness/native/provider_construction.py`), mirroring Pi computing it
inside the openai-completions provider. No catalog field or new cross-boundary
state is added; the existing `thinking_level` / `thinking_level_map` /
`reasoning` / `compat` model fields fully determine the shape. Wire delivery is
the existing completions `body_extra` -> `extra_body` plumbing (already proven by
the zai/together/deepseek tests), so no new SDK/runtime delegation to inspect.

## pipy implementation outline

1. **Detection rung** in `_resolve_thinking_format` (provider_construction.py),
   inserted between the `together` rung and the `openrouter` rung:

   ```python
   if provider == "ant-ling" or "api.ant-ling.com" in base_url:
       return "ant-ling"
   ```

   Update the resolver docstring's detection-order line to include the now-detected
   ant-ling rung at its Pi position (it currently says ant-ling falls through).

2. **Request-shape branch** in `resolve_construction`, placed after the
   `together` branch and before the default `elif reasoning_value is not None`.
   Because ant-ling needs the **raw** map lookup (no fallback), it must NOT reuse
   `reasoning_value`; add a small dedicated helper. The branch elif is **not**
   gated on `bool(spec.reasoning)` — it consumes **all** `thinking_format ==
   "ant-ling"` cases so a non-reasoning ant-ling model can never fall through to
   the default `elif reasoning_value is not None` branch (the helper's internal
   `spec.reasoning` check returns `None` for it, emitting nothing).

   **Why this matters (review finding):** unlike the existing zai/together/qwen
   branches, ant-ling deliberately drops the `and bool(spec.reasoning)` elif gate.
   `map_thinking_level` keys off `supported_thinking_levels`, which returns the
   `thinking_level_map` keys **regardless of `model.reasoning`**. So a
   non-reasoning ant-ling model that declares a `thinkingLevelMap` and is asked for
   a mapped level would otherwise compute a non-`None` `reasoning_value` and the
   default branch would wrongly emit a top-level `reasoning_effort` — Pi emits
   nothing (its branch and its default both gate on `model.reasoning`). Consuming
   all ant-ling cases in the dedicated branch closes that leak.

   ```python
   elif thinking_format == "ant-ling":
       # Pi ant-ling (openai-completions.ts:581-585): emit reasoning:{effort}
       # ONLY on the on-state, and ONLY when the RAW thinkingLevelMap lookup for
       # the requested level is a string. No `?? level` fallback (so a model with
       # no map emits nothing), no off-state, and reasoning_effort/
       # supportsReasoningEffort are never consulted. The branch is unconditional
       # on thinking_format so a non-reasoning ant-ling row never leaks to the
       # default reasoning_effort branch; the helper returns None for it.
       effort = _ant_ling_effort(spec, thinking_level)
       if effort is not None:
           body_extra["reasoning"] = {"effort": effort}
   ```

   Helper:

   ```python
   def _ant_ling_effort(spec: NativeModelSpec, thinking_level: str | None) -> str | None:
       """Raw thinkingLevelMap[level] lookup for ant-ling; string-only, no fallback."""
       if not thinking_level or thinking_level == "off" or not spec.reasoning:
           return None
       level_map = spec.thinking_level_map
       if not level_map:
           return None
       value = level_map.get(thinking_level)
       return value if isinstance(value, str) else None
   ```

3. Update the request-shape block's leading comment, the default branch's
   "not-yet-ported (ant-ling/string-thinking)" note (drop ant-ling, keep
   string-thinking), and the resolver docstring.

## Tests (mirror the existing zai/together blocks)

In `tests/test_native_provider_construction.py`, add an ant-ling block with an
`api.ant-ling.com` helper spec, covering:

- **on-state**: with map `{"high": "hi"}` and requested level `"high"`, assert
  `reasoning == {"effort": "hi"}` (the **mapped** value, proving it is the map
  result and not the raw requested level `"high"`), and `reasoning_effort is None`.
- **off-state silent**: for `None` and `"off"`, `"reasoning" not in body_extra`
  AND `reasoning_effort is None` (ant-ling's distinctive fully-silent off-state).
- **non-reasoning model (no map)**: emits neither.
- **non-reasoning model WITH a thinkingLevelMap, requested level "high"**: emits
  neither (proves the ant-ling branch consumes the case and no top-level
  `reasoning_effort` leaks via the default branch — the review-finding regression
  test).
- **unsupported level**: emits neither (clamp-to-None, no-clamp divergence).
- **no thinkingLevelMap -> emits nothing on-state** (the crux raw-lookup
  divergence: a reasoning ant-ling model with no map requested "high" must emit
  NO `reasoning`, even though pipy's `map_thinking_level` would otherwise yield
  the raw level). This is the test that distinguishes the faithful port from a
  naive `reasoning_value` reuse.
- **ignores supportsReasoningEffort**: explicit `compat.supportsReasoningEffort=True`
  still yields only `reasoning:{effort}` and never a top-level `reasoning_effort`
  (INVERSE secondary-flag guard, like the zai/qwen tests).
- **reaches request body**: on-state `reasoning:{effort}` survives onto the wire;
  off-state body has neither `reasoning` nor `reasoning_effort`.
- **detection precedence (collision row)**: `provider="ant-ling"` on an
  `openrouter.ai` base URL resolves to the **ant-ling** shape (`reasoning:{effort}`
  from the raw map), not openrouter's nested `reasoning:{effort}` (which uses the
  fallback-bearing `reasoning_value`). To make the two distinguishable, give the
  collision spec a map like `{"high": "hi"}` so ant-ling emits `{"effort": "hi"}`;
  openrouter would emit `{"effort": "hi"}` too here — so instead use a row where
  the behaviors differ: a collision spec with **no** map requested "high" -> ant-ling
  emits NOTHING while openrouter would emit `{"effort": "high"}`. Asserting
  `"reasoning" not in body_extra` proves the ant-ling rung won. Also assert the
  explicit-compat precedence: explicit `thinkingFormat="openrouter"` on an
  `api.ant-ling.com` base URL uses the openrouter shape.
- **together precedes ant-ling** is already covered structurally (together rung is
  before ant-ling); add a check that an `ant-ling` provider on an `api.together.xyz`
  base URL resolves to **together** (together rung wins, earlier in the chain).

## Conformance gate

Add an `18m` (and `18n` for precedence) check to
`scripts/parity_checks/provider_catalog_conformance.py` mirroring the zai/qwen
product-boundary checks: a built-in-style `ant-ling` provider models.json with an
`api.ant-ling.com` baseUrl + a reasoning model with a `thinkingLevelMap`, asserting
the on-state body carries `reasoning:{effort: <mapped>}`, no `reasoning_effort`,
and the off-state body carries neither.

## Docs

- `docs/provider-catalog.md`: add an "ant-ling reasoning request shape has
  shipped" bullet after the qwen bullet; update the three "(ant-ling,
  string-thinking)" remaining-follow-on phrases to "(string-thinking)"; add
  `ant-ling` to the `thinkingFormat` enum list (line ~592).
- `docs/pi-mono-gap-audit.md`: convert the ant-ling clause in the provider
  follow-on list to a "(shipped)" bullet; narrow remaining-variant phrases to
  `string-thinking` only.
- Release notes if a user-facing changelog exists for provider shapes.

## Done-when

- ant-ling auto-detected at its Pi-faithful chain position; on-state emits
  `reasoning:{effort}` from the raw map only, silent off-state, never any
  `reasoning_effort`; map-absent on-state emits nothing.
- New focused tests + conformance check pass; `just check` green.
- Docs/gap-audit reflect ant-ling shipped, string-thinking still pending.
- Different-family review CLEAN over the full diff.

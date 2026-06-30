# Qwen (qwen / qwen-chat-template) thinking-format request shape — design/plan

Status: design + implementation plan for one parity slice.
Date: 2026-06-30. Branch: `main` (trunk).
Pi reference checkout: `~/src/pi-mono`.

## Gap (one paragraph)

For the `openai-completions` API family, Pi emits a Qwen-specific reasoning
request shape when a **reasoning-capable** model resolves to the **`qwen`** or
**`qwen-chat-template`** thinking format. Both are the same `enable_thinking`
bare-boolean family as the already-shipped `zai` format: `qwen` sets a single
top-level boolean `enable_thinking` (`true` when a reasoning level is active,
`false` when off/unset) and emits **no** `reasoning_effort`;
`qwen-chat-template` instead nests that same boolean inside a
`chat_template_kwargs` object, `{enable_thinking: <bool>, preserve_thinking:
true}`, also with **no** `reasoning_effort`. pipy currently models the
`openrouter`, `deepseek`, `together`, and `zai` formats; a `qwen` /
`qwen-chat-template` model falls through pipy's request-shape block to the
default OpenAI-style top-level `reasoning_effort`, so it wrongly gets a plain
`reasoning_effort` on-state and **no** `enable_thinking` flag at all, and no
explicit off-state when thinking is off. This slice adds both `qwen` and
`qwen-chat-template` thinking-format request shapes, mirroring the shipped `zai`
slice in structure (same reasoning-capable gate, same on/off semantics, same
no-`reasoning_effort` rule) but with their distinct body keys.

## Pi reference — exact field list

Source: `packages/ai/src/providers/openai-completions.ts`, the `qwen` and
`qwen-chat-template` branches (lines 558–564):

```ts
} else if (compat.thinkingFormat === "qwen" && model.reasoning) {
    (params as any).enable_thinking = !!options?.reasoningEffort;
} else if (compat.thinkingFormat === "qwen-chat-template" && model.reasoning) {
    (params as any).chat_template_kwargs = {
        enable_thinking: !!options?.reasoningEffort,
        preserve_thinking: true,
    };
}
```

`options?.reasoningEffort` is the **post-clamp** reasoning value (identical to the
zai/deepseek/together slices: `clampedReasoning = options?.reasoning ?
clampThinkingLevel(model, options.reasoning) : undefined`, then `reasoningEffort =
clampedReasoning === "off" ? undefined : clampedReasoning`). For Pi it is either a
supported level or `undefined` (off/unset). `!!options?.reasoningEffort` is
therefore `true` on-state and `false` off/unset, in **both** branches.

### thinkingFormat resolution — explicit-compat-only (no detection rung)

`thinkingFormat` comes from `getCompat` (openai-completions.ts:1157–1183 =
explicit `model.compat.thinkingFormat` `??` `detectCompat`). **Crucially,
`detectCompat` (lines 1075–1152) has NO `qwen` or `qwen-chat-template` rung** —
its `thinkingFormat` chain is only `isDeepSeek ? "deepseek" : isZai ? "zai" :
isTogether ? "together" : isAntLing ? "ant-ling" : isOpenRouter ? "openrouter" :
"openai"`. There is no `isQwen`. So **the only way a model resolves to `qwen` or
`qwen-chat-template` is an explicit `model.compat.thinkingFormat`**. This is the
documented "explicit-only `qwen`" family from the skill body. Consequence for
pipy: `_resolve_thinking_format` already returns any explicit
`compat.thinkingFormat` verbatim (its first branch), so **no new detection rung
is added** and **no detection-chain ordering question arises** for this slice
(contrast the `zai` slice, which added an auto-detected `isZai` rung whose chain
position had to be pinned). The only code change in the request-shape block is the
two new `elif` branches.

### Per-field compat-flag gating

| Field | Gated by | How that flag resolves |
| --- | --- | --- |
| `enable_thinking` (qwen) | `thinkingFormat === "qwen"` **and** `model.reasoning` | `thinkingFormat`: explicit `compat.thinkingFormat="qwen"` only (no `detectCompat` rung). `model.reasoning`: the row's reasoning capability. |
| `chat_template_kwargs` (qwen-chat-template) | `thinkingFormat === "qwen-chat-template"` **and** `model.reasoning` | `thinkingFormat`: explicit `compat.thinkingFormat="qwen-chat-template"` only (no `detectCompat` rung). `model.reasoning`: the row's reasoning capability. |

**No secondary flag.** This is the `enable_thinking` BARE-BOOLEAN family
(skill-body pin): both branches emit `enable_thinking = !!options.reasoningEffort`
and NOTHING else — they **never** consult `compat.supportsReasoningEffort` and
**never** emit a top-level `reasoning_effort` (unlike the deepseek/together
branches). The omission is **structural** to the branch, not a consequence of any
`detectCompat` exclusion. Note the trap the skill body calls out: a Qwen provider
is not even in `detectCompat`'s `supportsReasoningEffort` exclusion list, but that
is irrelevant — the branch never reads the flag. The correct precedence guard for
this family is therefore the **INVERSE test**: force an explicit
`compat.supportsReasoningEffort=true` and assert the request STILL omits
`reasoning_effort` (only `enable_thinking` / `chat_template_kwargs` appears) — NOT
a deepseek-style explicit-format-on-excluded-provider mismatch test.

### The fields this slice changes (qwen / qwen-chat-template paths only)

This slice changes exactly one body key per format, only for a reasoning-capable
model that resolves to that thinking format:

| thinkingFormat | Body key | Pi value | Optionality |
| --- | --- | --- | --- |
| `qwen` | `enable_thinking` (top-level bool) | `true` on-state; `false` off/unset | emitted for **every** reasoning-capable qwen-format request (on **and** off); never for non-reasoning models |
| `qwen-chat-template` | `chat_template_kwargs` (object) | `{enable_thinking: true, preserve_thinking: true}` on-state; `{enable_thinking: false, preserve_thinking: true}` off/unset | emitted for **every** reasoning-capable qwen-chat-template request (on **and** off); never for non-reasoning models |

No `reasoning_effort` is ever emitted by either branch (contrast
deepseek/together, which add it on-state when `supportsReasoningEffort`).
`preserve_thinking` is a literal constant `true` — it is **not** keyed off the
reasoning state; only `enable_thinking` toggles.

Gate semantics (the reasoning-capable gate and on/off keying are identical to the
shipped zai path; the emitted keys differ):

| Aspect | Pi | pipy |
| --- | --- | --- |
| Model reasoning-capable | `model.reasoning` truthy | `bool(spec.reasoning)`; else emit nothing |
| On-state | `options?.reasoningEffort` truthy (post-clamp) | `reasoning_value is not None` |
| Off-state | `!options?.reasoningEffort` (clamped to off/unset) | reasoning-capable **and** `reasoning_value is None` **and** raw `thinking_level` in `{None, "off"}` |
| `reasoning_effort` | never emitted by either qwen branch | never set in either qwen branch |

### Forced default vs upstream API default

- The off-state `enable_thinking: false` (qwen) and
  `chat_template_kwargs.enable_thinking: false` (qwen-chat-template) are
  **Pi-forced defaults**: Pi explicitly disables Qwen thinking rather than
  omitting the field and letting the provider apply its own per-model behavior.
  pipy must match this forced emission, exactly as the shipped `zai`
  `enable_thinking: false`, `deepseek` `{type:"disabled"}`, `together`
  `{enabled:false}`, `openrouter` `{effort:"none"}`, and `anthropic-messages`
  `{type:"disabled"}` off-states do. Cited scope: the unconditional assignments at
  lines 558–559 (qwen) and 560–564 (qwen-chat-template) — fired for a reasoning
  qwen-format model in both sub-states. The off value here is the literal boolean
  `false`; it does **not** consult `thinkingLevelMap.off` (the way
  `openrouter`/`string-thinking` off-states do), so there is no `null`-suppression
  case for either format.
- `preserve_thinking: true` (qwen-chat-template only) is likewise a Pi-forced
  literal constant present in **both** sub-states (line 563), independent of the
  reasoning state.

### Derived identifiers

- None. No new model id, base URL, or auth identifier is derived. Both formats are
  selected solely by an explicit `compat.thinkingFormat` value; no provider name
  or base URL participates in selecting them.

## Pi predicate / known divergence: unsupported clamped level

Pi *clamps* an unsupported requested level up to a supported one (so a qwen model
still emits `enable_thinking: true`). pipy does **not** clamp:
`map_thinking_level` returns `None` for an unsupported level. pipy's existing
zai/deepseek/together/openrouter/anthropic slices deliberately gate the off-state
on the **raw** level (`thinking_level is None or thinking_level == "off"`) so an
unsupported level emits **neither** the on-state nor the off-state (it is
"still-thinking-and-clamp" per Pi, not off). This slice keeps that exact
pre-existing divergence for both qwen formats (covered by explicit tests,
mirroring `test_zai_unsupported_level_emits_neither`). Closing the no-clamp
divergence is a separate, cross-cutting gap and is **not** in scope.

## pipy mapping

In `src/pipy_harness/native/provider_construction.py`, add `qwen` and
`qwen-chat-template` branches to the existing resolved-format thinking block.
Per Pi's branch order (zai → qwen → qwen-chat-template → deepseek → openrouter →
ant-ling → together → string-thinking → default), the in-code `elif` ordering
between mutually exclusive `thinking_format` string values does not affect
resolution (a model has exactly one `thinking_format`), so the new branches may be
placed alongside the existing format branches; this plan places them immediately
**after** the existing `zai` branch to read top-to-bottom like Pi and to keep the
whole `enable_thinking` family together. Both branches consult no secondary flag:

```python
elif thinking_format == "qwen" and bool(spec.reasoning):
    # ``enable_thinking: bool`` is the entire Qwen thinking shape: true when a
    # reasoning level is active, false when off/unset, for every reasoning-capable
    # qwen-format request (openai-completions.ts:558-559). Like zai (and unlike
    # deepseek/together) the qwen branch emits NO reasoning_effort and never
    # consults supportsReasoningEffort. qwen is explicit-compat-only — Pi's
    # detectCompat has no qwen rung — so it is never auto-detected.
    if reasoning_value is not None:
        body_extra["enable_thinking"] = True
    elif thinking_off:
        body_extra["enable_thinking"] = False
elif thinking_format == "qwen-chat-template" and bool(spec.reasoning):
    # Same bare-boolean enable_thinking semantics as qwen, but nested in a
    # ``chat_template_kwargs`` object with a constant ``preserve_thinking: true``
    # (openai-completions.ts:560-564). No reasoning_effort; explicit-compat-only.
    if reasoning_value is not None:
        body_extra["chat_template_kwargs"] = {
            "enable_thinking": True,
            "preserve_thinking": True,
        }
    elif thinking_off:
        body_extra["chat_template_kwargs"] = {
            "enable_thinking": False,
            "preserve_thinking": True,
        }
```

**No change to `_resolve_thinking_format`.** Because `qwen` and
`qwen-chat-template` are never auto-detected (no `detectCompat` rung), the
resolver's existing first branch — return any explicit `compat.thinkingFormat`
verbatim — already yields `"qwen"` / `"qwen-chat-template"` for a model with that
explicit compat, and yields nothing new for any other model. No provider/base-URL
detection is added; no detection-chain ordering is affected. The resolver's
docstring is updated to move `qwen`/`qwen-chat-template` out of the "deferred
follow-ons" list and note they resolve via the explicit-compat branch only.

The on-state sets `body_extra["enable_thinking"]` /
`body_extra["chat_template_kwargs"]`; the completions adapter already writes
`extra_body`/`body_extra` entries to the request body, so both reach the wire with
no adapter change (same plumbing the shipped zai/deepseek/together emissions use).

## Adjacent Pi fields explicitly scoped OUT of this slice

- **Other `thinkingFormat` variants** (`ant-ling`, `string-thinking`) and a full
  `detectCompat` port. Still deferred follow-ons; they keep their current default
  behavior (resolve to their own explicit name or `"openai"` and fall through to
  the default top-level `reasoning_effort` branch). After this slice the only
  remaining unimplemented completions thinking formats are `ant-ling` and
  `string-thinking`.
- **`useMaxTokens` / `maxTokensField`**, **`supportsStore`**,
  **`supportsStrictMode`**, **`supportsLongCacheRetention`**, and the
  `isNonStandard` family — separate `detectCompat` compat fields, **not**
  thinking-shape fields. Independent gaps, out of scope; this slice changes only
  the qwen thinking shapes.
- **No-clamp divergence** for unsupported levels (see above). Separate gap.
- The **zai/deepseek/together/openrouter** emissions and the default OpenAI-style
  on-state (`reasoning_effort = mapped`) are unchanged.

## Implementation tasks (ordered, testable)

1. **TDD red.** Add focused tests in `tests/test_native_provider_construction.py`.
   Both formats are explicit-compat-only, so each helper spec sets
   `compat={"thinkingFormat": "qwen"}` / `{"thinkingFormat": "qwen-chat-template"}`
   on an otherwise generic `api="openai-completions"` reasoning row
   (`reasoning=True`, `thinking_level_map={"high": "high"}`). For **qwen**:
   - on-state `thinking_level="high"` → `body_extra["enable_thinking"] is True`
     **and** `reasoning_effort is None`.
   - off/unset `thinking_level=None` and `"off"` →
     `body_extra["enable_thinking"] is False` **and** `reasoning_effort is None`.
   - non-reasoning row (`reasoning=False`) → no `enable_thinking`, no
     `reasoning_effort`.
   - **INVERSE secondary-flag test:** explicit `compat.supportsReasoningEffort=True`
     (alongside the explicit `thinkingFormat="qwen"`) + on-state → still
     `enable_thinking is True` **and** `reasoning_effort is None` (proves the qwen
     branch ignores `supportsReasoningEffort` and never emits `reasoning_effort`).
   - unsupported level (`thinking_level="medium"`, no map entry) → **neither**
     `enable_thinking` nor `reasoning_effort` (documents the no-clamp divergence).
   - end-to-end adapter test (mirror `test_zai_thinking_reaches_request_body`):
     on- and off-state `enable_thinking` reach the request body through
     `build_provider`.
   For **qwen-chat-template**: the same matrix, asserting
   `body_extra["chat_template_kwargs"] == {"enable_thinking": True,
   "preserve_thinking": True}` on-state and `{"enable_thinking": False,
   "preserve_thinking": True}` off/unset, `reasoning_effort is None` throughout,
   the INVERSE `supportsReasoningEffort=True` guard, the non-reasoning/unsupported
   emit-neither cases, and an end-to-end `chat_template_kwargs`-reaches-the-body
   test.
   *Acceptance:* the new qwen tests fail before the code change; existing
   zai/deepseek/together/openrouter/anthropic/default tests stay green.
2. **Implement** the `qwen` and `qwen-chat-template` branches in
   `provider_construction.py` (after the `zai` branch), and update the
   `_resolve_thinking_format` docstring (move qwen/qwen-chat-template out of the
   deferred list; note explicit-compat-only). No detection rung is added.
   *Acceptance:* new tests green; all existing construction tests unchanged.
3. **Conformance gate.** Add `18k` (qwen) and `18l` (qwen-chat-template) checks to
   `scripts/parity_checks/provider_catalog_conformance.py`: a models.json provider
   with a reasoning model carrying explicit `compat.thinkingFormat="qwen"` /
   `"qwen-chat-template"` asserts on-state `enable_thinking == True` /
   `chat_template_kwargs == {enable_thinking: True, preserve_thinking: True}` +
   **no** `reasoning_effort`, and the off-state false variants + no
   `reasoning_effort`. *Acceptance:*
   `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`
   passes.
4. **Docs.** Update `docs/provider-catalog.md` (add a qwen shipped note; strike
   `qwen`/`qwen-chat-template` from the deferred thinkingFormat lists),
   `docs/pi-mono-gap-audit.md` (the deferred thinkingFormat lists in items 3 and
   5 — the remaining variants become just `ant-ling`, `string-thinking`), and
   `docs/backlog.md`. *Acceptance:* docs describe the behavior; the slice is
   struck from the gap source.

## Done-when

- `just check` green.
- `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`
  passes.
- Different-family review (pi-review-loop) CLEAN over the full code+docs diff in
  the same iteration.
- A reasoning-capable model with explicit `compat.thinkingFormat="qwen"` emits
  `enable_thinking: true` on-state and `enable_thinking: false` off/unset, with
  **no** `reasoning_effort`; one with `"qwen-chat-template"` emits
  `chat_template_kwargs={enable_thinking, preserve_thinking:true}` with the same
  on/off boolean and no `reasoning_effort`; non-reasoning rows emit neither; an
  unsupported clamped level emits neither; an explicit
  `supportsReasoningEffort=True` does **not** add `reasoning_effort`; the shipped
  zai/deepseek/together/openrouter/default/anthropic paths are unchanged.

## Constraints (AGENTS.md)

- stdlib-only, no new runtime dependencies; reuse the existing
  `ProviderRequest`/`ProviderResult`/construction boundary.
- No secret leakage into results/metadata (covered by existing check 18f).
- Match Pi behavior through pipy-owned Python boundaries; this is not a literal
  TS port.
- No deprecation shims / pipy-only accretions.

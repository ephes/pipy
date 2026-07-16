# DeepSeek thinking-format request shape — design/plan

Status: design + implementation plan for one parity slice.
Date: 2026-06-30. Branch: `main` (trunk).
Pi reference checkout: `~/src/pi-mono`.

## Gap (one paragraph)

For the `openai-completions` API family, Pi emits a DeepSeek-specific reasoning
request shape when a **reasoning-capable** model uses the **`deepseek`** thinking
format: it always sets a top-level `thinking: {type: "enabled"|"disabled"}`
object (enabled when a reasoning level is active, disabled when off/unset), and —
because DeepSeek `supportsReasoningEffort` — additionally sets top-level
`reasoning_effort` when a level is active. pipy currently models only the
`openrouter` nested-reasoning format and the default OpenAI-style top-level
`reasoning_effort`; a DeepSeek-format model (Pi keys it off provider `deepseek`
or a `deepseek.com` base URL, or an explicit `compat.thinkingFormat`) therefore
gets the plain `reasoning_effort` on-state and **no** `thinking` object at all,
and no explicit disable when thinking is off. This slice adds the `deepseek`
thinking-format request shape, directly mirroring the already-shipped
`openrouter` off-state slice and the `anthropic-messages`
`thinking: {type: "disabled"}` off-state pattern.

## Pi reference — exact field list

Source: `packages/ai/src/providers/openai-completions.ts`, the
`thinkingFormat === "deepseek"` branch (lines 565–570):

```ts
} else if (compat.thinkingFormat === "deepseek" && model.reasoning) {
    (params as any).thinking = { type: options?.reasoningEffort ? "enabled" : "disabled" };
    if (options?.reasoningEffort && compat.supportsReasoningEffort) {
        (params as any).reasoning_effort =
            model.thinkingLevelMap?.[options.reasoningEffort] ?? options.reasoningEffort;
    }
}
```

`options?.reasoningEffort` is the **post-clamp** reasoning value: line 439–440
sets `clampedReasoning = options?.reasoning ? clampThinkingLevel(model, options.reasoning) : undefined`
then `reasoningEffort = clampedReasoning === "off" ? undefined : clampedReasoning`.
`clampThinkingLevel` (`models.ts:61–80`) snaps an unsupported requested level to
the nearest *supported* level (never to `off` for a reasoning model that declares
supported levels), so for Pi `options.reasoningEffort` is either a supported
level or `undefined` (off/unset).

`thinkingFormat`/`supportsReasoningEffort` come from `getCompat`
(openai-completions.ts:1157–1183 = explicit `model.compat.*` `??` `detectCompat`):

- `detectCompat` (lines 1110, 1126–1136): `isDeepSeek = provider === "deepseek"
  || baseUrl.includes("deepseek.com")`; `thinkingFormat = isDeepSeek ? "deepseek"
  : …`.
- `supportsReasoningEffort` (lines 1118–1119): `!isGrok && !isZai && !isMoonshot
  && !isTogether && !isCloudflareAiGateway && !isNvidia && !isAntLing`. This is
  resolved **independently** of `thinkingFormat` (getCompat resolves each field
  separately). DeepSeek's own provider/base URL is in **none** of those exclusion
  sets, so a *detected* deepseek model has `supportsReasoningEffort = true`; but
  an **explicit** `compat.thinkingFormat="deepseek"` placed on a provider/base URL
  Pi excludes (e.g. Together / Cloudflare AI Gateway / Nvidia / Moonshot / xAI /
  z.ai / ant-ling) yields `thinkingFormat="deepseek"` **and**
  `supportsReasoningEffort=false`, so Pi emits `thinking` **without**
  `reasoning_effort`. An explicit `model.compat.supportsReasoningEffort` overrides
  the detected value (getCompat `??`).

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

### The fields this slice changes (DeepSeek thinking-format path only)

This slice changes exactly two body keys, only for a reasoning-capable model that
resolves to the `deepseek` thinking format:

| Body key | Pi value | Optionality |
| --- | --- | --- |
| `thinking` (object) | `{type: "enabled"}` when a level is active; `{type: "disabled"}` when off/unset | emitted for **every** reasoning-capable deepseek-format request (on **and** off); never for non-reasoning models |
| `reasoning_effort` (top-level string) | `thinkingLevelMap?.[level] ?? level` | **only** on-state **and** `supportsReasoningEffort`; omitted in the off/unset case |

Gate semantics (mirroring pipy's existing openrouter/anthropic off-state gates):

| Aspect | Pi | pipy |
| --- | --- | --- |
| Model reasoning-capable | `model.reasoning` truthy | `bool(spec.reasoning)`; else emit nothing |
| On-state ("enabled") | `options?.reasoningEffort` truthy (post-clamp) | `reasoning_value is not None` |
| Off-state ("disabled") | `!options?.reasoningEffort` (clamped to off/unset) | reasoning-capable **and** `reasoning_value is None` **and** raw `thinking_level` in `{None, "off"}` |
| `reasoning_effort` add | on-state **and** `supportsReasoningEffort` | on-state **and** `_supports_reasoning_effort(spec)` (faithful exclusion predicate, **not** a blanket `True`) |

### Forced default vs upstream API default

- The off-state `thinking: {type: "disabled"}` is a **Pi-forced default**: Pi
  explicitly disables DeepSeek reasoning rather than omitting the field and
  letting the DeepSeek API apply its own per-model behavior. pipy must match this
  forced emission, exactly as the shipped `openrouter` `{effort:"none"}` and
  `anthropic-messages` `{type:"disabled"}` off-states do. Cited scope: the
  `thinking = { type: options?.reasoningEffort ? "enabled" : "disabled" }`
  assignment (line 566) — unconditional for a reasoning deepseek model, both
  sub-branches.
- The on-state `reasoning_effort` value is the **model-mapped** value
  (`thinkingLevelMap?.[level] ?? level`), matching the convention pipy already
  uses (`reasoning_value = map_thinking_level(spec, level)` is the mapped value).

### Derived identifiers

- None. No new model id, base URL, or auth identifier is derived. DeepSeek-format
  detection reuses provider name / base URL / explicit `compat.thinkingFormat`,
  the same signals the shipped openrouter detection uses.

## Pi predicate / known divergence: unsupported clamped level

Pi *clamps* an unsupported requested level up to a supported one (so a deepseek
model still emits `thinking:{type:"enabled"}` + the clamped `reasoning_effort`).
pipy does **not** clamp: `map_thinking_level` returns `None` for an unsupported
level. pipy's existing openrouter/anthropic slices deliberately gate the
off-state on the **raw** level (`thinking_level is None or thinking_level ==
"off"`) so an unsupported level emits **neither** the on-state nor the off-state
(it is "still-thinking-and-clamp" per Pi, not off). This slice keeps that exact
pre-existing divergence for consistency (covered by an explicit test, mirroring
`test_openrouter_unsupported_level_does_not_emit_off_state`). Closing the
no-clamp divergence is a separate, cross-cutting gap and is **not** in scope.

## pipy mapping

In `src/pipy_harness/native/provider_construction.py`, replace the current
openrouter-vs-default thinking resolution (lines ~136–157) with a single
**resolved-format** branch that mirrors Pi's `getCompat` precedence (explicit
`compat.thinkingFormat` wins over detection; openai-completions.ts:1174):

```python
reasoning_value = map_thinking_level(spec, thinking_level)
thinking_off = (
    bool(spec.reasoning)
    and reasoning_value is None
    and (thinking_level is None or thinking_level == "off")
)
reasoning_effort: str | None = None
thinking_format = _resolve_thinking_format(spec)

if thinking_format == "openrouter":
    if reasoning_value is not None:
        body_extra["reasoning"] = {"effort": reasoning_value}
    elif thinking_off:
        off_effort = _openrouter_off_effort(spec)
        if off_effort is not None:
            body_extra["reasoning"] = {"effort": off_effort}
elif thinking_format == "deepseek" and bool(spec.reasoning):
    if reasoning_value is not None:
        body_extra["thinking"] = {"type": "enabled"}
        if _supports_reasoning_effort(spec):
            reasoning_effort = reasoning_value
    elif thinking_off:
        body_extra["thinking"] = {"type": "disabled"}
elif reasoning_value is not None:
    reasoning_effort = reasoning_value

thinking_disabled = thinking_off
```

New private helpers, placed beside `_openrouter_off_effort` so the new logic is
diff-local with its only dependencies:

- `_resolve_thinking_format(spec) -> str`: explicit `compat.thinkingFormat`
  string wins; else `"deepseek"` for provider `deepseek`/`deepseek.com` base URL;
  else `"openrouter"` for provider `openrouter`/`openrouter.ai` base URL; else
  `"openai"`. This *replaces* `_uses_openrouter_thinking` (its two call sites
  become the `== "openrouter"` branch). For models without an explicit
  `compat.thinkingFormat`, the resolved value is identical to the prior
  `_uses_openrouter_thinking` result, so the shipped openrouter behavior and its
  tests are unchanged; it only *adds* the correct getCompat precedence (explicit
  compat wins over base-URL detection) and the deepseek format.
- `_supports_reasoning_effort(spec) -> bool`: explicit
  `compat.supportsReasoningEffort` bool wins; else Pi's `detectCompat`
  exclusion predicate — `False` when the provider name / base URL matches any of
  the isGrok/isZai/isMoonshot/isTogether/isCloudflareAiGateway/isNvidia/isAntLing
  rows in the table above, `True` otherwise. This resolves `supportsReasoningEffort`
  independently of `thinkingFormat`, so an explicit `thinkingFormat="deepseek"` on
  an excluded provider correctly omits `reasoning_effort` (matching Pi). It is a
  single bounded predicate, not a full `detectCompat` port; pipy's **default**
  OpenAI-style branch deliberately keeps its existing behavior and is **not**
  re-gated on this helper in this slice (a separate pre-existing gap).

Note: the unimplemented formats (`zai`, `qwen`, `qwen-chat-template`,
`together`, `ant-ling`, `string-thinking`) resolve via `_resolve_thinking_format`
to their own name and fall through to the default `elif reasoning_value is not
None` branch (top-level `reasoning_effort`), which is **identical** to today's
behavior for those models — a documented deferral, no regression.

The `deepseek`-format on-state sets `body_extra["thinking"]` **and**
`reasoning_effort`; the completions adapter already writes `extra_body` entries
and `reasoning_effort` to the request body, so both reach the wire with no
adapter change.

## Adjacent Pi fields explicitly scoped OUT of this slice

- **Other `thinkingFormat` variants** (`zai`, `qwen`, `qwen-chat-template`,
  `together`, `ant-ling`, `string-thinking`) and a full `detectCompat` port.
  Still deferred follow-ons; they keep their current default behavior.
- **`requiresReasoningContentOnAssistantMessages` (deepseek)** — a *message
  serialization* transform (`isDeepSeek`), not a request thinking-shape field.
  Separate gap; **not** changed here.
- **No-clamp divergence** for unsupported levels (see above). Separate gap.
- The **openrouter** on/off emission and the default OpenAI-style on-state
  (`reasoning_effort = mapped`) are unchanged.

## Implementation tasks (ordered, testable)

1. **TDD red.** Add focused tests in `tests/test_native_provider_construction.py`
   (a `_deepseek_spec` helper: `api="openai-completions"`,
   `base_url="https://api.deepseek.com/v1"`, `reasoning=True`,
   `thinking_level_map={"high": "high"}`):
   - on-state `thinking_level="high"` → `body_extra["thinking"] ==
     {"type": "enabled"}` **and** `reasoning_effort == "high"`.
   - off/unset `thinking_level=None` and `"off"` → `body_extra["thinking"] ==
     {"type": "disabled"}` **and** `reasoning_effort is None`.
   - non-reasoning deepseek row → no `thinking` key, no `reasoning_effort`.
   - explicit `compat.supportsReasoningEffort=False` + on-state → `thinking ==
     {"type": "enabled"}` but **no** `reasoning_effort`.
   - explicit `compat.thinkingFormat="deepseek"` on an **excluded** base URL
     (e.g. `https://api.together.xyz/v1`) + on-state → `thinking ==
     {"type": "enabled"}` but **no** `reasoning_effort` (proves
     `supportsReasoningEffort` is resolved independently of `thinkingFormat`).
   - unsupported level (clamped away → `reasoning_value None`, raw level not off)
     → **neither** `thinking` nor `reasoning_effort` (documents the no-clamp
     divergence).
   - end-to-end adapter test (mirror
     `test_openrouter_off_state_reaches_request_body`): on-state and off-state
     `thinking` reach the request body through `build_provider`.
   - openrouter regression guard: an explicit `compat.thinkingFormat="openrouter"`
     on a `deepseek.com` base URL still uses the nested `reasoning` object (proves
     getCompat precedence: explicit compat wins over base-URL detection).
   *Acceptance:* the new deepseek tests fail before the code change; existing
   openrouter/anthropic/default tests stay green.
2. **Implement** `_resolve_thinking_format` + `_deepseek_supports_reasoning_effort`
   and the restructured thinking branch in `provider_construction.py`.
   *Acceptance:* new tests green; all existing construction tests unchanged.
3. **Conformance gate.** Add a `18g` check to
   `scripts/parity_checks/provider_catalog_conformance.py`: a models.json DeepSeek
   provider (`api="openai-completions"`, `baseUrl` `…deepseek.com…`, a reasoning
   model) asserts on-state `thinking == {"type": "enabled"}` + `reasoning_effort`,
   and off-state `thinking == {"type": "disabled"}` + no `reasoning_effort`.
   *Acceptance:*
   `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`
   passes.
4. **Docs.** Update `docs/provider-catalog.md` (the thinkingFormat follow-on note
   — strike `deepseek` from the deferred list, describe the shipped shape),
   `docs/pi-mono-gap-audit.md` (the openrouter follow-on bullet's deferred list),
   and `docs/backlog.md`. *Acceptance:* docs describe the behavior; the slice is
   struck from the gap source.

## Done-when

- `just check` green.
- `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`
  passes.
- Different-family review (pi-review-loop) CLEAN over the full code+docs diff in
  the same iteration.
- A reasoning-capable DeepSeek-format model emits `thinking: {type: "enabled"}` +
  `reasoning_effort` on-state and `thinking: {type: "disabled"}` (no
  `reasoning_effort`) off/unset; non-reasoning rows emit neither; an unsupported
  clamped level emits neither; explicit `supportsReasoningEffort=False` omits
  `reasoning_effort`; the shipped openrouter/default/anthropic paths are unchanged.

## Constraints (AGENTS.md)

- stdlib-only, no new runtime dependencies; reuse the existing
  `ProviderRequest`/`ProviderResult`/construction boundary.
- No secret leakage into results/metadata (covered by existing check 18f).
- Match Pi behavior through pipy-owned Python boundaries; this is not a literal
  TS port.
- No deprecation shims / pipy-only accretions.

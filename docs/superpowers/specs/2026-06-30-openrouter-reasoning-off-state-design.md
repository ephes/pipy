# OpenRouter reasoning off-state — design/plan

Status: design + implementation plan for one parity slice.
Date: 2026-06-30. Branch: `main` (trunk).
Pi reference checkout: `~/src/pi-mono`.

## Gap (one paragraph)

For the `openai-completions` API family, when a **reasoning-capable** model that
uses the **OpenRouter** thinking format runs with thinking **off or unset**, Pi
emits an explicit reasoning-disable in the request body —
`reasoning: {effort: <off-value>}` — to turn reasoning off at the OpenRouter
router. pipy currently emits the nested `reasoning: {effort}` object **only when
a reasoning level is active** and emits **nothing** in the off/unset case, so a
pipy OpenRouter turn with thinking off does not actively disable reasoning the
way Pi does. This diverges on **built-in** providers: pipy's catalog ships
reasoning-capable OpenRouter rows (`openai/gpt-5.1-codex`,
`anthropic/claude-opus-4-7`, `moonshotai/kimi-k2.6`), none of which carry an
explicit `thinking_level_map`. This slice makes the OpenRouter off-state match
Pi, mirroring the already-shipped `anthropic-messages`
`thinking: {type: "disabled"}` off-state pattern.

## Pi reference — exact field list

Source: `packages/ai/src/providers/openai-completions.ts`, the
`thinkingFormat === "openrouter"` branch (lines 571–581):

```ts
} else if (compat.thinkingFormat === "openrouter" && model.reasoning) {
    // OpenRouter normalizes reasoning across providers via a nested reasoning object.
    const openRouterParams = params as typeof params & { reasoning?: { effort?: string } };
    if (options?.reasoningEffort) {
        openRouterParams.reasoning = {
            effort: model.thinkingLevelMap?.[options.reasoningEffort] ?? options.reasoningEffort,
        };
    } else if (model.thinkingLevelMap?.off !== null) {
        openRouterParams.reasoning = { effort: model.thinkingLevelMap?.off ?? "none" };
    }
}
```

`thinkingFormat === "openrouter"` is set by `detectCompat`
(openai-completions.ts:1118–1180) when the provider is `openrouter` (Pi also keys
off other signals for other formats; pipy approximates with explicit
`compat.thinkingFormat == "openrouter"` plus the `openrouter.ai` base-URL
heuristic already in `_uses_openrouter_thinking`).

### The single field this slice changes

- **Body key `reasoning.effort`** (nested object), for the OpenRouter
  thinking-format path **only**, in the **off/unset** case.

| Aspect | Pi value | Optionality |
| --- | --- | --- |
| Gate: model must be reasoning-capable | `model.reasoning` truthy | required; non-reasoning models emit no `reasoning` key |
| Gate: no active level (off/unset) | `!options?.reasoningEffort` | this branch only |
| Suppress entirely | `model.thinkingLevelMap?.off === null` | when off is explicitly null, emit **nothing** |
| Emitted value | `model.thinkingLevelMap?.off ?? "none"` | absent off key → `"none"`; string off value → that string |

### Off-value resolution table (the `?? "none"` after the `!== null` gate)

| `thinking_level_map["off"]` | Pi behavior | pipy emission |
| --- | --- | --- |
| key absent (`undefined`) | `undefined !== null` → true; `undefined ?? "none"` → `"none"` | `reasoning: {effort: "none"}` |
| explicit `null` | `null !== null` → false | **no `reasoning` key** |
| string `s` | `s !== null` → true; `s ?? "none"` → `s` | `reasoning: {effort: s}` |

### Forced default vs upstream API default

- The emitted `"none"` (for the absent-off-key case) is a **Pi-forced default**:
  Pi sends `reasoning: {effort: "none"}` to explicitly disable reasoning rather
  than omitting the field and letting OpenRouter apply its own per-model default
  (which may leave reasoning on). pipy must match this forced default, not the
  upstream omit-and-default behavior. Cited scope: the
  `else if (model.thinkingLevelMap?.off !== null)` branch above; the comment on
  line 572 ("OpenRouter normalizes reasoning … via a nested reasoning object")
  applies to both the on and off sub-branches.

### Derived identifiers

- None. No new model id, base URL, or auth identifier is derived. The OpenRouter
  detection reuses the existing `_uses_openrouter_thinking(spec)`
  (`compat.thinkingFormat == "openrouter"` or `openrouter.ai` in the base URL).

## pipy mapping

In `src/pipy_harness/native/provider_construction.py`, the current logic
(lines ~136–142):

```python
reasoning_value = map_thinking_level(spec, thinking_level)
reasoning_effort: str | None = None
if reasoning_value is not None:
    if _uses_openrouter_thinking(spec):
        body_extra["reasoning"] = {"effort": reasoning_value}
    else:
        reasoning_effort = reasoning_value
```

`map_thinking_level(spec, level)` returns `None` when the level is `None`/`off`,
the model is non-reasoning, **or** the level is unsupported (clamped away). The
off-state branch must therefore **not** key on `reasoning_value is None` alone —
that would also fire for non-reasoning models and for unsupported-level requests.

Mirror the off-state gate that the shipped `anthropic-messages` work already uses
(`thinking_disabled`, this file lines ~149–153): fire only when the model is
reasoning-capable **and** the raw level is off/unset (`thinking_level is None or
thinking_level == "off"`), never for an unsupported clamped level (Pi treats an
unsupported level as still-thinking-and-clamp, not off).

New off-state emission (only when `reasoning_value is None`, OpenRouter format,
`spec.reasoning` true, and the level is off/unset):

- Resolve `off_value`:
  - `"off" not in spec.thinking_level_map` → emit `{"effort": "none"}`.
  - `spec.thinking_level_map["off"] is None` → emit nothing (skip).
  - `spec.thinking_level_map["off"]` is a string `s` → emit `{"effort": s}`.

This will be a small private helper (e.g. `_openrouter_off_effort(spec)`)
returning the string to emit or `None` to skip, placed beside
`_uses_openrouter_thinking` so the new constant/logic is diff-local with its only
dependency.

## Adjacent Pi fields explicitly scoped OUT of this slice

These share the openai-completions reasoning path but are **separate gaps**, not
part of this slice (and are not currently regressions this slice introduces):

- **Other `thinkingFormat` variants** (`deepseek`, `zai`, `qwen`,
  `qwen-chat-template`, `together`, `ant-ling`, `string-thinking`) and a full
  `detectCompat` port. pipy models only the OpenRouter format (explicit/base-URL).
  Separate gaps.
- **Default openai-style off-value emission** — Pi's final branch
  (`else if (!options?.reasoningEffort && model.reasoning && compat.supportsReasoningEffort)`)
  emits top-level `reasoning_effort: thinkingLevelMap.off`. That depends on
  modeling `supportsReasoningEffort` (not in pipy's `NativeModelSpec`). Separate
  gap; **not** changed here.
- **`requiresReasoningContentOnAssistantMessages` (deepseek)** message transform.
  Separate gap.
- The **on-state** OpenRouter emission (`reasoning: {effort: <level>}` when a
  level is active) is already Pi-correct and unchanged.

## Implementation tasks (ordered, testable)

1. **TDD red.** Add focused tests in
   `tests/test_native_provider_construction.py`:
   - built-in reasoning OpenRouter row + `thinking_level=None` →
     body has `reasoning == {"effort": "none"}` and no top-level
     `reasoning_effort`.
   - non-reasoning OpenRouter row (`openai/gpt-4o:extended`) + `thinking_level=None`
     → no `reasoning` key.
   - models.json OpenRouter row with `thinkingLevelMap.off = null` +
     `thinking_level=None` → no `reasoning` key.
   - models.json OpenRouter row with `thinkingLevelMap.off = "minimal"` +
     `thinking_level=None` → `reasoning == {"effort": "minimal"}`.
   - reasoning OpenRouter row + an **unsupported** level (clamped away) → off-state
     **not** emitted (matches Pi's still-thinking-and-clamp; documents the
     deliberate divergence from a naive `reasoning_value is None` gate).
   - on-state regression guard: `thinking_level="high"` →
     `reasoning == {"effort": "high"}` (unchanged).
   *Acceptance:* the off-state tests fail before the code change.
2. **Implement** the `_openrouter_off_effort` helper + the off-state branch in
   `provider_construction.py`. *Acceptance:* new tests green; existing
   construction tests unchanged.
3. **Conformance gate.** Extend
   `scripts/parity_checks/provider_catalog_conformance.py` check `18d`
   (or add `18g`) so an off/unset reasoning OpenRouter construction asserts
   `reasoning == {"effort": "none"}`. *Acceptance:*
   `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`
   passes.
4. **Docs.** Update `docs/provider-catalog.md` follow-on note,
   `docs/pi-mono-gap-audit.md`, and `docs/backlog.md` to record the shipped
   off-state. *Acceptance:* docs describe the behavior; the slice is struck from
   the gap source.

## Done-when

- `just check` green.
- The conformance gate passes.
- Different-family review (pi-review-loop) CLEAN over the full code+docs diff in
  the same iteration.
- Built-in OpenRouter reasoning rows emit `reasoning: {effort: "none"}` when
  thinking is off/unset; non-reasoning rows and explicit `off: null` rows emit no
  `reasoning` key; the on-state path is unchanged.

## Constraints (AGENTS.md)

- stdlib-only, no new runtime dependencies; reuse the existing
  `ProviderRequest`/`ProviderResult`/construction boundary.
- No secret leakage into results/metadata (covered by existing check 18f).
- Match Pi behavior through pipy-owned Python boundaries; this is not a literal
  TS port.

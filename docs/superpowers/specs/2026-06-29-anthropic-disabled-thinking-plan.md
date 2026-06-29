# Plan — Anthropic-messages explicit `thinking: {type: "disabled"}` for thinking-off

Parity gap (gap audit item 5, owning spec `docs/provider-catalog.md`):

> Neither adapter emits Pi's explicit `thinking: {type: "disabled"}` when a
> reasoning-capable model is run with thinking off, because the adapters only
> receive the resolved `reasoning_effort` and not the model's reasoning-
> capability flag; threading that flag through is a follow-on.

## Scope

One reviewable slice: thread the model's reasoning-capability intent into the
**`anthropic-messages`** adapter so that, for a reasoning-capable Claude model run
with thinking **off/unset**, the request body carries `thinking: {type:
"disabled"}` instead of omitting the key — matching Pi's product path. The
`amazon-bedrock` adapter is intentionally **not** changed (see "Bedrock
exclusion" — Pi omits there; pipy already omits).

This slice changes **only** the thinking-off branch of the anthropic-messages
request shape. The adaptive (`type: "adaptive"` + `output_config.effort`) and
budget (`type: "enabled"` + `budget_tokens`) enabled paths, the forced
`display: "summarized"`, and all non-thinking fields are already matched and are
out of scope.

## Pi reference (pinned)

`~/src/pi-mono/packages/ai/src/providers/anthropic.ts`:

- `streamSimpleAnthropic` (the product path, lines ~746-748):
  ```ts
  const base = buildBaseOptions(model, options, apiKey);
  if (!options?.reasoning) {
      return streamAnthropic(model, context, { ...base, thinkingEnabled: false });
  }
  // else thinkingEnabled: true with effort (adaptive) or budget (older models)
  ```
  So in the product path `thinkingEnabled` is **always** explicitly `true`/`false`
  — `false` whenever the resolved thinking level is falsy (covers both "off" and
  "unset", since `SimpleStreamOptions.reasoning` excludes `off`, which arrives as
  `undefined`).
- `buildParams` thinking block (lines 949-978):
  ```ts
  if (model.reasoning) {
      if (options?.thinkingEnabled) { /* adaptive / budget */ }
      else if (options?.thinkingEnabled === false) {
          params.thinking = { type: "disabled" };
      }
  }
  ```

### Pinned field list for the changed path

The slice changes exactly one emitted field group on the anthropic-messages
request:

- `thinking` — object.
  - When emitted in the disabled case its **only** key is `{"type": "disabled"}`.
    No `display`, no `budget_tokens`, no `output_config`. (Confirmed
    anthropic.ts:976 — the disabled branch sets `params.thinking = { type:
    "disabled" }` with nothing else.)
  - **Optionality / trigger:** emitted **only** when the model is
    reasoning-capable (`model.reasoning` truthy) **and** thinking resolves to
    off/unset (no effort resolved). For a **non-reasoning** model the key is
    omitted entirely (the outer `if (model.reasoning)` guard). For an enabled
    request the existing adaptive/budget shape is emitted unchanged.
- **Pi-forced defaults:** none are added to the disabled shape. (`display:
  "summarized"` is a Pi-forced default on the *enabled* paths only; it is **not**
  present on the disabled shape.)
- **Divergence from the upstream API default:** when `thinking` is omitted, the
  Anthropic Messages API default is "no extended thinking". Pi nonetheless sends
  an explicit `{type: "disabled"}` for reasoning-capable models rather than
  relying on the omission default — making the off-state explicit on the wire.
  This slice reproduces that explicitness; it is a Pi-forced behavior, not an
  upstream API default. Cite: anthropic.ts:949-977.
- **Derived identifiers:** none.

## Bedrock exclusion (pinned)

`~/src/pi-mono/packages/ai/src/providers/amazon-bedrock.ts`,
`buildAdditionalModelRequestFields` (lines 943-949):

```ts
if (!options.reasoning || !model.reasoning) {
    return undefined;   // omit thinking-related additionalModelRequestFields
}
```

Pi's Bedrock adapter **omits** thinking fields entirely when reasoning is
off/unset or the model is non-reasoning; it has **no** `{type: "disabled"}`
branch. pipy's `amazon-bedrock` adapter already omits `thinking` when
`reasoning_effort is None`, so it is **already Pi-correct**. Therefore the gap
audit's "Neither adapter emits" framing overstates the bedrock side: only the
anthropic-messages adapter needs a change. The docs update corrects this.

## pipy current state

`src/pipy_harness/native/anthropic_provider.py`: the adapter holds
`reasoning_effort: str | None`. When `reasoning_effort is not None` it emits the
adaptive or budget shape; when `None` it emits no `thinking` key. The adapter has
no knowledge of whether the model is reasoning-capable.

`src/pipy_harness/native/provider_construction.py`:
`resolve_construction(...)` computes `reasoning_value = map_thinking_level(spec,
thinking_level)`. `map_thinking_level` returns `None` for level `None`/`"off"`,
for non-reasoning models, and for unsupported levels — so `reasoning_effort is
None` currently conflates "thinking off on a reasoning model", "unset", and
"non-reasoning model". The model's capability flag (`spec.reasoning`, a `bool`)
is available here but is not threaded to the adapter.

## Design

Compute the disabled-thinking intent in `resolve_construction` (where both
`spec.reasoning` and the raw `thinking_level` are known) and pass a single boolean
to the adapter, mirroring how Pi's product layer computes `thinkingEnabled: false`
and the adapter applies it.

1. `ResolvedConstruction`: add `thinking_disabled: bool = False`.
2. `resolve_construction`: set
   `thinking_disabled = bool(spec.reasoning) and reasoning_value is None and
   (thinking_level is None or thinking_level == "off")`.
   - Basing the flag on the **raw** off/unset level (not merely
     `reasoning_value is None`) keeps an *unsupported* thinking level on a
     reasoning model out of the disabled branch — Pi treats an unsupported level
     as still-thinking-and-clamp, never disabled. For such a level pipy continues
     to omit (its pre-existing clamping behavior). Documented boundary; in
     practice reasoning Claude models map all standard levels, so this edge is
     largely theoretical.
   - `thinking_disabled` and `reasoning_effort` are mutually exclusive by
     construction: when `reasoning_effort is not None`, `reasoning_value` was not
     `None`, so `thinking_disabled` is `False`.
3. Anthropic branch of `construct_provider`: pass
   `thinking_disabled=resolved.thinking_disabled` to `AnthropicProvider`.
4. `AnthropicProvider`: add field `thinking_disabled: bool = False`. In the
   thinking block:
   ```python
   if self.reasoning_effort is not None:
       ...  # existing adaptive / budget
   elif self.thinking_disabled:
       body["thinking"] = {"type": "disabled"}
   ```
   New field defaults `False`, so every existing direct construction and the
   non-reasoning path keep omitting `thinking` — backward compatible.

No new runtime dependencies; stdlib only. Adapter stays "dumb" (applies a
computed intent), matching Pi's split between the agent layer (computes
`thinkingEnabled`) and `buildParams` (applies it).

## Done-when

- `AnthropicProvider(..., reasoning_effort=None, thinking_disabled=True)` emits
  `body["thinking"] == {"type": "disabled"}` and no `output_config`.
- `thinking_disabled=False` with `reasoning_effort=None` still omits `thinking`
  (non-reasoning / unset-flag path).
- `reasoning_effort` set still emits the adaptive/budget shape regardless of
  `thinking_disabled` (mutually exclusive, but pin precedence).
- `resolve_construction` sets `thinking_disabled=True` for a reasoning-capable
  spec with `thinking_level` `None`/`"off"`, and `False` for a non-reasoning spec
  or when an effort was resolved.
- `construct_provider` threads the flag into the anthropic-messages adapter.
- Bedrock unchanged and still omits.
- `just check` green; docs (provider-catalog.md, pi-mono-gap-audit.md,
  backlog.md, release notes) updated.

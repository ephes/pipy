# String-thinking `openai-completions` request-shape parity

## Gap and Pi reference

Gap: port the last deferred `openai-completions` `thinkingFormat` request-shape variant, `string-thinking`, through pipy's provider-construction boundary. This is a single request-shape slice; the full Pi `detectCompat` port and unrelated provider polish remain deferred.

Pi reference: `/Users/jochen/src/pi-mono/packages/ai/src/providers/openai-completions.ts`.

Relevant Pi fields in this slice:

- `compat.thinkingFormat === "string-thinking"` branch at lines 595-601 (nearby): emits a top-level string field `thinking` only when `model.reasoning` is truthy.
- On-state (`options.reasoningEffort` truthy): `thinking = model.thinkingLevelMap?.[options.reasoningEffort] ?? options.reasoningEffort`. This has the `?? level` fallback, unlike `ant-ling`, so pipy's existing `map_thinking_level`/`reasoning_value` helper is the right value source.
- Off/unset state (`!options.reasoningEffort`): if `model.thinkingLevelMap?.off !== null`, emits `thinking = model.thinkingLevelMap?.off ?? "none"`. Therefore absent `off` maps to the Pi-forced default string `"none"`; explicit `off: null` suppresses the field; explicit string is emitted verbatim. This forced default diverges from an upstream generic OpenAI default because this is Pi's own compatibility branch.
- Unsupported explicit level: pipy intentionally keeps the existing clamped-away behavior from the other thinking-format slices: if `map_thinking_level` returns `None` for an unsupported non-off level, no on-state field and no off-state field are emitted.
- Secondary compat flags: none apply. The branch never consults `compat.supportsReasoningEffort` and never emits top-level `reasoning_effort`.
- Detection: `string-thinking` is explicit-compat-only. Pi's `detectCompat` chain is `isDeepSeek > isZai > isTogether > isAntLing > isOpenRouter > openai` and has no `isStringThinking` rung. Pipy must not add resolver detection or precedence tests; `_resolve_thinking_format` already returns explicit `compat.thinkingFormat` verbatim.

## Pipy design

Add an `elif thinking_format == "string-thinking" and bool(spec.reasoning)` request-shape branch in `src/pipy_harness/native/provider_construction.py`, adjacent to the other `thinkingFormat` branches. It will:

1. Emit `body_extra["thinking"] = reasoning_value` when `reasoning_value` is a string.
2. For off/unset reasoning (`thinking_off`), use a small helper like `_openrouter_off_effort` that tests key membership before lookup:
   - `"off" not in spec.thinking_level_map` emits Pi's default `"none"`.
   - `spec.thinking_level_map["off"] is None` suppresses the field.
   - a string value is emitted verbatim.
   This preserves Pi's `model.thinkingLevelMap?.off !== null` distinction between a missing key and an explicit `null`; do not use `.get("off")` as the gate because it conflates those cases.
3. Keep pipy's established level-validation/clamping boundary: despite Pi's raw `?? options.reasoningEffort` fallback for arbitrary truthy efforts inside the provider, pipy only forwards already-supported product thinking levels through `map_thinking_level`. Therefore an unsupported explicit level remains clamped away and emits no `thinking`/`reasoning_effort`, matching the existing deepseek/together/openrouter behavior in this codebase.
4. Never set `reasoning_effort` and never call `_supports_reasoning_effort` for this branch.
5. Keep `_resolve_thinking_format` detection unchanged except for comments/docstrings that no longer call `string-thinking` deferred.

Ownership boundary: Pi computes this wire shape during provider request construction, not in catalog metadata. Pipy should therefore keep this as construction-time `body_extra` and not add catalog fields.

## Tests and docs

Focused tests in `tests/test_native_provider_construction.py`:

- explicit `compat={"thinkingFormat": "string-thinking"}` on-state emits `body_extra["thinking"] == <mapped>` and no `reasoning_effort`;
- on-state without a map falls back to the raw requested level;
- unsupported explicit level emits neither `thinking` nor `reasoning_effort` at pipy's product validation boundary;
- off/unset emits `"none"` by default;
- off explicit string emits that value;
- off explicit `None` suppresses emission;
- non-reasoning model emits neither `thinking` nor `reasoning_effort`;
- explicit `compat.supportsReasoningEffort=True` still emits only `thinking`, proving no secondary flag applies;
- end-to-end `build_provider` request body contains `thinking` and omits `reasoning_effort`.

Docs: update `docs/provider-catalog.md`, `docs/pi-mono-gap-audit.md`, and `docs/backlog.md` to mark `string-thinking` shipped and leave only the full `detectCompat` port / other already-deferred follow-ons.

Done when: focused tests pass, `just check` passes, docs/gap sources no longer list `string-thinking` as deferred, and a different-family review returns CLEAN over the complete diff.

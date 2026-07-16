# ant-ling thinking format — implementation plan

Reviewed design: `2026-07-01-ant-ling-thinking-format-design.md` (plan review CLEAN).

## Tasks (ordered, TDD)

1. **Tests first** — add an ant-ling block to
   `tests/test_native_provider_construction.py` after the qwen block, mirroring the
   zai/together helpers:
   - `_ant_ling_spec(**over)`: provider `ant-ling`, base_url
     `https://api.ant-ling.com/v1`, api `openai-completions`, reasoning True,
     `thinking_level_map={"high": "hi"}`.
   - `_resolve_al(spec, tmp_path, level)` with env `{"ANT_LING_API_KEY": "k"}`.
   - Cases (acceptance criteria):
     - on-state: `reasoning == {"effort": "hi"}`, `reasoning_effort is None`.
     - off-state (`None`, `"off"`): `"reasoning" not in body_extra` and
       `reasoning_effort is None` (silent off-state).
     - non-reasoning (no map override → keep map): with `reasoning=False` and
       level `"high"` → emits neither (no-leak regression test).
     - non-reasoning, level `None` → emits neither.
     - unsupported level (`"medium"`, map only has `high`) → emits neither.
     - no thinkingLevelMap (`thinking_level_map={}`) + level `"high"` → emits
       neither (raw-lookup divergence: must NOT emit the raw level).
     - ignores supportsReasoningEffort: `compat={"supportsReasoningEffort": True}`
       + level `high` → `reasoning == {"effort": "hi"}`, `reasoning_effort is None`.
     - reaches request body: on-state body has `reasoning == {"effort": "hi"}` and
       no `reasoning_effort`; off-state body has neither.
     - detection precedence vs openrouter: `provider="ant-ling"`,
       `base_url="https://openrouter.ai/api/v1"`, **no map** (`thinking_level_map={}`),
       level `"high"` → `"reasoning" not in body_extra` (ant-ling rung wins; an
       openrouter resolution would emit `{"effort": "high"}` via the fallback).
     - together precedes ant-ling: `provider="ant-ling"`,
       `base_url="https://api.together.xyz/v1"`, level `"high"` → together shape
       `reasoning == {"enabled": True}` (together rung is earlier).
     - explicit precedence: `compat={"thinkingFormat": "openrouter"}` on an
       ant-ling base/provider → openrouter shape `reasoning == {"effort": "hi"}`
       (explicit wins; with map `{"high":"hi"}` openrouter emits the mapped value).
   - Run: expect failures (no ant-ling branch yet).

2. **Implement detection rung** in `_resolve_thinking_format`
   (`src/pipy_harness/native/provider_construction.py`): add
   `if provider == "ant-ling" or "api.ant-ling.com" in base_url: return "ant-ling"`
   between the `together` and `openrouter` rungs. Update the docstring's
   detection-order sentence (ant-ling is now a detected rung at its Pi position;
   only `string-thinking` remains deferred).

3. **Implement `_ant_ling_effort` helper** + the `elif thinking_format ==
   "ant-ling":` branch (unconditional on reasoning; helper does the
   reasoning/level/raw-map/string checks) per the design. Update the request-shape
   block's leading comment and the default branch's "(ant-ling/string-thinking)"
   note → "(string-thinking)".

4. **Conformance gate** — add `18m` (shape) and `18n` (precedence) checks to
   `scripts/parity_checks/provider_catalog_conformance.py` mirroring the zai/qwen
   product-boundary checks. Acceptance: gate passes with the new checks.

5. **Docs** — `docs/provider-catalog.md` (new shipped bullet + enum + narrow the
   three "(ant-ling, string-thinking)" phrases to "(string-thinking)"),
   `docs/pi-mono-gap-audit.md` (ant-ling clause → "(shipped)"; narrow remaining
   phrases). Update release notes if a provider-shape changelog exists.

6. **Gates** — `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`
   and `just check` green. Then different-family review (pi-review-loop) over the
   full diff until CLEAN.

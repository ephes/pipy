# Implementation plan — google-vertex thinkingConfig injection

Design: `2026-06-30-vertex-thinking-config-design.md` (Pi-reviewed CLEAN).
TDD where it applies; one focused commit at the end.

## Task 1 — vertex thinking helpers + field plumbing (adapter)

File: `src/pipy_harness/native/google_vertex_provider.py`.

1.1 Add model-family regexes/predicates: `_is_gemini3_pro`, `_is_gemini3_flash`
    (reuse the `gemini-3(?:\.\d+)?-pro` / `-flash` patterns). **No** gemma4
    predicate — vertex has none.
1.2 `_uses_thinking_level(model_id)` → `_is_gemini3_pro or _is_gemini3_flash`
    (no gemma4).
1.3 `_google_thinking_level(effort, model_id)` matching
    `getGemini3ThinkingLevel`: gemini3-pro → `minimal`/`low`→`LOW`,
    `medium`/`high`→`HIGH`; else passthrough
    `{minimal:MINIMAL, low:LOW, medium:MEDIUM, high:HIGH}` (default `HIGH`).
1.4 `_google_thinking_budget(model_id, effort)` matching vertex `getGoogleBudget`:
    `2.5-pro` → `{128,2048,8192,32768}`; `2.5-flash` → `{128,2048,8192,24576}`;
    else `-1`. **No** flash-lite branch (flash-lite falls into `2.5-flash`).
1.5 `_disabled_thinking_config(model_id)`: gemini3-pro → `{"thinkingLevel":"LOW"}`;
    gemini3-flash → `{"thinkingLevel":"MINIMAL"}`; else `{"thinkingBudget":0}`.
    **No** gemma4 branch.
1.6 `_build_thinking_config(model_id, reasoning_effort, thinking_disabled)`:
    reasoning_effort → `{"includeThoughts":True, level|budget}`; elif
    thinking_disabled → disabled config; else `None`.
1.7 Add fields to `GoogleVertexProvider`: `reasoning_effort: str | None = None`,
    `thinking_disabled: bool = False`. Update the field-block comment (drop "not
    yet injected").
1.8 In `complete`, after building `body`, inject
    `body.setdefault("generationConfig", {})["thinkingConfig"] = cfg` when
    `_build_thinking_config(...)` is not None. Place this so it applies to **both**
    Express and ADC modes (the body is built once after the auth branch).

## Task 2 — forward thinking through catalog construction

File: `src/pipy_harness/native/provider_construction.py`.

2.1 In `_build_iam_provider`, pass `reasoning_effort=resolved.reasoning_effort`
    and `thinking_disabled=resolved.thinking_disabled` to `GoogleVertexProvider(...)`.
2.2 Update the module docstring (line ~29) and `_build_iam_provider` docstring:
    vertex now injects per-model `generationConfig.thinkingConfig` (level enum vs
    budget); drop "vertex thinking is per-model and not yet injected".

## Task 3 — tests (focused)

File: `tests/test_native_google_vertex_thinking.py` (model on
`tests/test_native_google_thinking.py`).

- enabled budget: 2.5-pro high → 32768; 2.5-flash high → 24576;
  **2.5-flash-lite minimal → 128** (divergence vs generative-ai's 512);
  unknown model → -1; each with `includeThoughts: True`.
- enabled level: gemini3-pro medium → HIGH, pro low → LOW, flash minimal →
  MINIMAL.
- disabled config: 2.5-pro → `{thinkingBudget:0}` (no includeThoughts);
  gemini3-pro → `{thinkingLevel:LOW}`; gemini3-flash → `{thinkingLevel:MINIMAL}`.
- omission: non-reasoning intent (neither field) → no generationConfig; default
  construction omits.
- **no gemma4 special-case**: a `gemma-4`-style id uses the budget path (→ -1),
  not a level — proving the divergence from generative-ai.
- both-mode injection: thinkingConfig present in an Express (api-key) request and
  an ADC (bearer) request via capturing HTTP clients.
- helper unit tests: `_google_thinking_budget`, `_disabled_thinking_config`,
  `_uses_thinking_level` (no gemma4).

## Task 4 — conformance gate

File: `scripts/parity_checks/provider_catalog_conformance.py`.

4.1 Add `22_vertex_thinking_config`: construct a vertex 2.5 model with
    `thinking_level="high"`, drive a capturing HTTP client, assert the sent body
    has `generationConfig.thinkingConfig == {"includeThoughts": True,
    "thinkingBudget": <table value>}`.

## Task 5 — docs + changelog

- `docs/pi-mono-gap-audit.md` §5: mark google-vertex thinking shipped (strike the
  follow-on).
- `docs/backlog.md`: same in the provider follow-ons list.
- `docs/provider-catalog.md`: record the shipped vertex thinking shape + the
  vertex-vs-generative-ai divergences (flash-lite, no gemma4).
- `CHANGELOG.md`: add an entry.

## Task 6 — gate + review

- `just check` green.
- Re-run Pi review over the full diff (code + docs); fix→re-gate→re-review until
  CLEAN.
- Commit on main (clean message). Capture lessons (runner mode: capture only).

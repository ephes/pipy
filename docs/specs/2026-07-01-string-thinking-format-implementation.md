# String-thinking implementation plan

1. Add focused construction tests.
   - Create an explicit-compat-only `_string_thinking_spec` helper in `tests/test_native_provider_construction.py`.
   - Cover on mapped value, on raw fallback without a map, unsupported explicit level clamped away, default off `"none"`, explicit off string, explicit off `None` suppression, non-reasoning skip, `supportsReasoningEffort=True` ignored, and end-to-end request-body plumbing.
   - Acceptance: tests fail before implementation for the new request-shape expectations.

2. Implement request-shape construction.
   - Add a `_string_thinking_off_value` helper that mirrors `_openrouter_off_effort` membership semantics.
   - Add a `thinking_format == "string-thinking" and bool(spec.reasoning)` branch that writes top-level `body_extra["thinking"]` and never `reasoning_effort`.
   - Keep resolver detection unchanged; update only comments/docstrings that describe deferral.
   - Acceptance: focused tests pass.

3. Update parity docs.
   - Mark `string-thinking` shipped in `docs/provider-catalog.md`, `docs/pi-mono-gap-audit.md`, and `docs/backlog.md`.
   - Leave full `detectCompat` and other unrelated follow-ons deferred.
   - Acceptance: no docs still say `string-thinking` is a remaining request-shape gap.

4. Validate and review.
   - Run focused pytest and `just check` (plus `prek` only if a pre-commit config exists).
   - Run different-family review over the complete diff and fix any ISSUES before commit.
   - Acceptance: gates green and review CLEAN over the exact committed diff.

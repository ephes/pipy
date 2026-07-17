# OpenAI Responses Dynamic Tool Search Implementation Plan

Status: ordered implementation plan from the reviewed design

Design: [2026-07-17-openai-responses-dynamic-tool-search-design.md](../specs/2026-07-17-openai-responses-dynamic-tool-search-design.md)

1. **Share the durable deferred-tool split and exact Pi hash.**
   - Move the provider-agnostic current-definition/history split out of the
     Anthropic adapter without changing its behavior.
   - Add Pi's UTF-16/32-bit/base-36 `shortHash` port and deterministic vectors.
   - Acceptance: existing Anthropic dynamic-tool tests remain green; unit tests
     pin the reviewed hash vectors, stable deduplication, prior-use handling,
     stale markers, and all-deferred output.

2. **Wire catalog compatibility independently.**
   - Add `supports_tool_search` to resolved construction and both Responses
     adapters, defaulting false and accepting only explicit Boolean compat.
   - Mark only the reviewed built-in OpenAI/Codex rows true.
   - Thread Codex's selected-row value through its legacy construction path
     without coupling it to reasoning effort.
   - Acceptance: construction/catalog/repl-state tests prove explicit
     true/false, supported rows, unsupported rows, custom compat, and fallback
     inheritance.

3. **Implement exact OpenAI Responses placement.**
   - Send only immediate definitions top-level when enabled.
   - After each marked `function_call_output`, append the completed client
     `tool_search_call` and matching `tool_search_output` with ordered query,
     count, deterministic id, and forced `strict: false` /
     `defer_loading: true` definitions.
   - Acceptance: focused tests pin exact items for supported, unsupported,
     duplicate, stale, prior-use, multiple-marker, and all-deferred cases.

4. **Apply the same placement to Codex transports.**
   - Reuse the provider-neutral Responses item builder while retaining Codex's
     existing combined correlation-id handling and top-level request shape.
   - Keep the body built once outside retry and WebSocket/SSE fallback loops.
   - Acceptance: focused Codex tests pin full-correlation hash input, exact
     item placement, model opt-out, and identical retry/fallback request ids.

5. **Update conformance and user-facing documentation.**
   - Extend the extension/provider conformance surface for the real load-point
     through Responses placement.
   - Update extension API, provider catalog/parity docs, architecture/user docs,
     backlog, gap audit, parity plan, and release notes where this behavior is
     tracked.
   - Acceptance: only OpenAI Responses tool search is marked shipped; Kimi and
     unrelated follow-ons remain explicit.

6. **Run the exact-diff commit gate.**
   - Run focused tests, `just check`, and `prek run --all-files` only if the
     repository contains `.pre-commit-config.yaml`.
   - Run a fresh direct Claude-family review over code and docs together. Fix
     every required finding, rerun all gates, and re-review until CLEAN.
   - Acceptance: the final CLEAN review covers the unchanged diff that is
     committed on `main`; no push occurs.

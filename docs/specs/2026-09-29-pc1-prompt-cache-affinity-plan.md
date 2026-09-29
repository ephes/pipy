# PC1 — OpenAI/Codex prompt-cache affinity (plan)

Status: implemented (backlog item PC1, 2026-09-29). Pi reference: `~/src/pi-mono` at
`4df157433`, paths relative to `packages/`. tau reference:
`~/src/tau/src/tau_ai/openai_cache.py`.

## Backlog claims, verified

- "Pipy sends no `prompt_cache_key` today": true for Codex, Responses and Azure.
- "`openai_codex_provider.py:1284` sets `session-id` to a fresh per-request ID":
  true (now `:1303-1308`, `_websocket_attempt`). The id is drawn per WebSocket
  attempt, and the SSE path sends no session headers at all.
- "Omit the key when cache retention is `none` and on private summary calls":
  true for Codex and Responses. **Not true for Azure**: Pi's Azure adapter sends
  `prompt_cache_key` from `options.sessionId` without consulting retention
  (`ai/src/api/azure-openai-responses.ts:308`), so a summary call sends a key
  derived from its fresh routing id. pipy mirrors Pi, not the backlog wording.
- `ProviderRequest` has no session id, so none of this is reachable today.

## What Pi does (fields this slice changes)

This is the list of fields this slice changes on each request path, not the
complete request shape. Adjacent fields (reasoning, tools, text verbosity,
`max_output_tokens`, service tier) are already matched or tracked elsewhere.

### Shared helper (`ai/src/api/openai-prompt-cache.ts`)

- `OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH = 64`.
- `clampOpenAIPromptCacheKey(key)`: `undefined` stays `undefined`; otherwise the
  first 64 **code points** (`Array.from`). Python `str` slicing is code-point
  based, so `key[:64]` is exact. An empty string stays empty (not reachable:
  pipy session ids match `[A-Za-z0-9_-]{1,128}`).
- `CacheRetention = "none" | "short" | "long"` (`ai/src/types.ts:218`).

### openai-codex-responses (`ai/src/api/openai-codex-responses.ts`)

| Field | Value | Optionality |
| --- | --- | --- |
| retention | `options.cacheRetention` only; no env lookup. Only `"none"` has an effect. | — |
| `cacheSessionId` | `retention === "none" ? undefined : options.sessionId` (`:275`) | — |
| `codexSessionId` | `clamp(cacheSessionId)` (`:276`) | — |
| body `prompt_cache_key` | `codexSessionId` (`:561`), after `include`, before `tool_choice` | omitted when undefined |
| SSE `session-id`, `x-client-request-id` | both `codexSessionId` (`:1673-1676`), set after the base/extension headers | both omitted when undefined |
| WS `session-id`, `x-client-request-id` | both `codexSessionId \|\| uuidv7()` (`:282`, `:1694-1695`), derived once per `stream()` call | always present |

### openai-responses (`ai/src/api/openai-responses.ts`)

| Field | Value | Optionality |
| --- | --- | --- |
| retention | `options.cacheRetention`, else `"long"` when env `PI_CACHE_RETENTION === "long"`, else `"short"` (`:58-66`) | — |
| affinity format | explicit `compat.sessionAffinityFormat`, else `"openrouter"` when `provider === "openrouter"` or the base URL contains `openrouter.ai`, else `"openai"` (`:50-52`, `:72`) | — |
| headers | from the **unclamped** `sessionId` unless retention is `none` (`:145`, `:259-268`): `openrouter` → `x-session-id`; `openai` → `session_id` + `x-client-request-id`; `openai-nosession` → `x-client-request-id` | omitted with no session or `none` |
| body `prompt_cache_key` | `none ? undefined : clamp(sessionId)` (`:314`) | omitted when undefined |
| body `prompt_cache_retention` | `"24h"` when `long` and `supportsLongCacheRetention` (default true) and not `supportsExplicitPromptCacheMode` (`:83-89`) | otherwise omitted |
| body `prompt_cache_options` | only when `supportsExplicitPromptCacheMode` (default false): `none` → `{mode: "explicit"}`; `long` and `supportsLongCacheRetention` → `{ttl: "30m"}`; else omitted (`:91-100`) | otherwise omitted |

Header ordering: the session headers are written after `model.headers` and
before `options.headers`, so request-scoped headers can override them.

Catalog: Pi's generator sets `compat.supportsExplicitPromptCacheMode: true` on
every `openai` / `openai-responses` row with `cost.cacheWrite > 0`
(`ai/scripts/generate-models.ts:948-958`). Six pipy rows qualify: gpt-6
sol/luna/astra and gpt-5.6 sol/terra/luna. Custom `models.json` rows do not get
it unless they set it.

### azure-openai-responses (`ai/src/api/azure-openai-responses.ts:308`)

| Field | Value | Optionality |
| --- | --- | --- |
| body `prompt_cache_key` | `clamp(options.sessionId)`; retention is ignored | omitted without a session id |
| headers | none | — |

### Callers (`coding-agent/src/core/`)

- Main agent turns pass `sessionId: sessionManager.getSessionId()` and no
  `cacheRetention` (`sdk.ts:413`, via `agent/src/agent.ts:472`). A session
  replacement (`/new`, `/resume`, `/fork`) builds a new session, so the id is
  always the current session's.
- Compaction passes no session id (`agent-session.ts:2651`), and branch
  summaries pass none either. `completeSummarization`
  (`compaction/compaction.ts:649-655`) forces `cacheRetention: "none"` and
  `sessionId: options.sessionId ?? uuidv7()`, so each summary call gets a fresh
  routing id that retries reuse.
- An extension calling pi-ai `complete()` itself passes no session id.

## pipy design

1. **`native/providers/openai_prompt_cache.py`** (new, mirrors
   `openai-prompt-cache.ts`): `OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH`,
   `clamp_openai_prompt_cache_key`, `CacheRetention` literal, and
   `resolve_cache_retention(explicit, env)` for the Responses family.
   The env var is `PIPY_CACHE_RETENTION` (pipy's `PI_*` → `PIPY_*` convention,
   as with `PIPY_OFFLINE`), read at request time like Pi.
2. **`ProviderRequest`** gains `session_id: str | None = None` and
   `cache_retention: CacheRetention | None = None`. The snapshot validator
   (`agent/request.py`) checks both types. `replace()`-based snapshots carry
   them unchanged.
3. **Main turns** (`repl/loop_step.py::_request_values`) set
   `session_id=scope.ctl.session_tree.session_id`, read per request so `/new`,
   `/resume` and `/fork` switch it. `cache_retention` stays `None` (Pi default).
4. **Summary requests** (`coding/compaction.py::build_summary_request`, used by
   compaction and branch summaries) set `cache_retention="none"` and a fresh
   `session_id` (uuid4 hex) per request, so retries/reissues reuse it.
5. **Extension `ctx.complete`** (`repl/collaborators.py`) and the legacy
   compatibility runtime (`session.py`) keep `session_id=None`.
6. **Codex** (`openai_codex_provider.py`): derive `codex_session_id` once in
   `_codex_completion_configuration`; add `prompt_cache_key` to the body in
   Pi's key position; add the SSE session headers after the base headers; build
   WebSocket headers once per configuration from `codex_session_id or
   request_id_factory()`.
7. **Responses** (`providers/openai_responses.py`): new construction fields
   `session_affinity_format`, `supports_long_cache_retention`,
   `supports_explicit_prompt_cache_mode`, resolved in `provider_construction.py`
   from `spec.compat` plus Pi's detection. Pi resolves them inside the adapter
   from the model; pipy adapters do not hold the spec, so construction is the
   ownership seam (same as `supports_tool_search`). Retention and headers are
   resolved at request time. Session headers are written after `extra_headers`
   (model headers) and before `apply_provider_headers` (the request-scoped hook).
8. **Azure** (`providers/azure_openai_responses.py`): body
   `prompt_cache_key = clamp(request.session_id)` when present.
9. **Catalog** (`catalog_data.py`): add `supportsExplicitPromptCacheMode: True`
   to the six openai-responses rows with `cache_write > 0`.

## Deviations and follow-ons (recorded in docs)

- Routing ids are uuid4 hex, not uuidv7; opaque either way.
- Not in this slice: openai-completions `prompt_cache_key` and session-affinity
  headers (`openai-completions.ts:771-826`); Anthropic `cache_control` (PC2);
  Pi's cache warmer; Codex WebSocket connection reuse/continuation and the
  per-session SSE-fallback map keyed by session id (pipy remembers SSE fallback
  per provider instance).

## Tests (done-when)

- Clamp: short key unchanged, 64 kept, 65+ truncated, non-BMP code points
  counted as one.
- Codex: session → body key + SSE headers + WS headers equal clamped id; long id
  clamped everywhere; `none` → no key, no SSE session headers, WS uses the
  factory id; no session → same as `none`; WS id stable across the
  connection-limit retry within one call; session headers override extension
  headers on SSE.
- Responses: default → `session_id` + `x-client-request-id` (unclamped) and a
  clamped key, no retention fields; `openrouter` (provider name, base URL,
  explicit compat) → `x-session-id` only; `openai-nosession` →
  `x-client-request-id` only; `none` → no key/headers, and
  `{mode: "explicit"}` only with explicit mode; `PIPY_CACHE_RETENTION=long` →
  `prompt_cache_retention: "24h"` without explicit mode, `{ttl: "30m"}` with it,
  neither when `supportsLongCacheRetention` is false; explicit
  `cache_retention` wins over env.
- Azure: key clamped from the session id; still sent with `none`; absent
  without a session id.
- Callers: a REPL turn's request carries the session tree id; a compaction and
  a branch-summary request carry `cache_retention="none"` and a fresh id
  different from the session id; extension `complete` carries none.
- Catalog: the six rows carry the explicit-mode bit; others do not.
- `just check` green.

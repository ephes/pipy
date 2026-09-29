# PR1/PR2 — xai and github-copilot providers (plan)

Backlog item 9 (`docs/backlog.md`). Pi reference: `~/src/pi-mono` @ `4df157433`.
Catalog values come from Pi's generated data
(`packages/ai/src/providers/data/{xai,github-copilot}.json`, generated
2026-09-29T15:03:22Z), not from reading generator overrides.

## What Pi does

### xai (PR1)

- `providers/xai.ts`: provider `xai`, base URL `https://api.x.ai/v1`, API family
  `openai-responses` for every row, API key from `XAI_API_KEY`
  (`env-api-keys.ts:92`). Pi also offers an xAI OAuth login ("SuperGrok or X
  Premium", `auth/oauth/xai.ts`).
- Default model `grok-4.7` (`coding-agent/src/core/model-resolver.ts:35`).
- Four rows: `grok-4.3`, `grok-4.5`, `grok-4.6`, `grok-4.7`. All reasoning,
  text+image, `compat.supportsLongCacheRetention: false`, cost tiers above 200k
  input tokens. Maps: 4.3 `{off:"none", minimal:null, low, medium, high,
  xhigh:null, max:null}`; 4.5 `{off:null, minimal:null, low, medium, high,
  xhigh:null, max:null}`; 4.6/4.7 as 4.5 but `xhigh:"xhigh"`.
- Request shape (`api/openai-responses.ts:343-358`): the ordinary Responses
  path, plus `params.include = ["reasoning.encrypted_content"]` whenever
  `model.provider === "xai" && model.reasoning`, in both the on- and off-state.
  Off-state `reasoning:{effort: map.off ?? "none"}` is sent only when
  `map.off !== null` (so only grok-4.3 sends `effort:"none"`).
- `getCompat` (`openai-responses.ts:68-80`): session affinity format `openai`
  (not an openrouter URL), `supportsLongCacheRetention` false from the row.

### github-copilot (PR2)

- `providers/github-copilot.ts`: provider `github-copilot`, default base URL
  `https://api.individual.githubcopilot.com`, three API families
  (`anthropic-messages`, `openai-completions`, `openai-responses`), auth by
  `COPILOT_GITHUB_TOKEN` env or OAuth. `filterModels`: with an OAuth credential
  whose `availableModelIds` is a string list, only those ids are available
  (`models.ts:701` applies it in `getAvailable`).
- Default model `gpt-5.4` (`model-resolver.ts:32`).
- 33 rows: 10 `anthropic-messages` (Claude), 6 `openai-completions` (Gemini,
  Kimi), 17 `openai-responses` (GPT, Grok, MAI). Every row carries the static
  headers `User-Agent: GitHubCopilotChat/0.35.0`, `Editor-Version:
  vscode/1.107.0`, `Editor-Plugin-Version: copilot-chat/0.35.0`,
  `Copilot-Integration-Id: vscode-chat`, plus per-row compat.
- OAuth (`auth/oauth/github-copilot.ts`, `auth/oauth/device-code.ts`):
  - login prompts "GitHub Enterprise URL/domain (blank for github.com)";
    non-blank input must normalize to a hostname or login fails.
  - device flow: `POST https://<domain>/login/device/code` form
    `client_id`, `scope=read:user`; validates `device_code`, `user_code`,
    `verification_uri` (http/https only), optional numeric `interval`, numeric
    `expires_in`. Notifies the user code and URI.
  - polling (`pollOAuthDeviceCodeFlow`): waits one interval before the first
    poll; interval default 5 s, minimum 1 s; `slow_down` uses the server
    `interval` when given, else +5 s; deadline `expires_in`; outcomes
    `access_token` (done), `authorization_pending`, `slow_down`, other error
    (fail with `Device flow failed: <error>[: <description>]`), timeout
    (`Device flow timed out`, or the clock-drift message after a `slow_down`).
  - token exchange: `GET https://api.<domain>/copilot_internal/v2/token` with
    `Authorization: Bearer <github token>` + the Copilot headers; credential
    `{type:"oauth", refresh:<github token>, access:<token>, expires:
    expires_at*1000 - 5 min, enterpriseUrl:<domain or undefined>}`.
  - model catalog: `GET <base>/models` with Bearer + Copilot headers +
    `X-GitHub-Api-Version: 2026-06-01`; parse `data[]`, drop
    `capabilities.supports.tool_calls === false`; picker ids =
    `model_picker_enabled && policy.state !== "disabled"`; policy fallback only
    on `https://api.individual.githubcopilot.com` when no picker ids; policy ids
    = `unconfigured` and known to Pi's catalog and (picker or fallback).
  - login enables each policy id (`POST <base>/models/<id>/policy`,
    `{"state":"enabled"}`, `openai-intent`/`x-interaction-type:
    chat-policy`); a 429 after retries stops the batch; other failures skip.
    Stored credential gets `availableModelIds = unique(available + enabled)`.
  - refresh re-exchanges the token and re-fetches the model list (no 429
    retries) and replaces `availableModelIds`.
  - `toAuth`: `apiKey = access`, `baseUrl` = `https://api.<proxy-ep minus
    "proxy.">` from the token, else `https://copilot-api.<enterprise>`, else the
    individual default. `models.ts:857` makes `auth.baseUrl` override the
    row's base URL.
  - `auth/resolve.ts:101-160`: a stored OAuth credential refreshes when
    `now + 5 min >= expires`, under a store lock, and is persisted.
- Request shape:
  - all three adapters add `X-Initiator` (`agent` when the last message is not
    a user message, else `user`), `Openai-Intent: conversation-edits`, and
    `Copilot-Vision-Request: true` when a user or tool-result message carries
    an image (`api/github-copilot-headers.ts`). Order: row headers, then these,
    then request headers (`openai-responses.ts:249-275`,
    `openai-completions.ts:761-790`, `anthropic-messages.ts:926-947`).
  - anthropic-messages: Bearer auth (`authToken`), never `x-api-key`; no
    session-affinity header on this branch.
  - openai-responses: no off-state `reasoning` for `github-copilot`
    (`openai-responses.ts:353`), even when `map.off` is a string
    (gpt-6-luna, gpt-6-sol).
  - openai-completions (Gemini/Kimi rows): `supportsReasoningEffort: false`,
    so the OpenAI-style default branch sends no `reasoning_effort`
    (`openai-completions.ts:964-972`). `supportsStore:false` and
    `supportsDeveloperRole:false` match what pipy's adapter already sends (no
    `store`, system role).

## How pipy matches it

1. **Catalog (`catalog_data.py`, `catalog.py`).** `_m` gains `headers`. Add the
   base URLs, the 4 xai rows and the 33 github-copilot rows with Pi's exact
   values and full Pi compat mapping (incl. keys pipy does not read yet), and
   the row headers. Partial Pi maps are spelled out per the catalog rule. Pi cost
   tiers are not modeled (pipy `NativeModelCost` has no tiers; pricing belongs to
   the concurrent cost slice). Add defaults `xai: grok-4.7`,
   `github-copilot: gpt-5.4`.
2. **Registry/auth.** `provider_registry.py` entries (xai `env:XAI_API_KEY`;
   github-copilot `env:COPILOT_GITHUB_TOKEN`, unavailable message naming
   `/login github-copilot`). `auth_store` env map gains `xai: XAI_API_KEY`.
   Append both to `AUTO_DEFAULT_PROVIDER_PRIORITY` before
   `openai-completions` (pipy's order is pipy-owned; env availability only).
3. **Responses reasoning shape (xai and Copilot, and every
   `openai-responses` row).** Port Pi `buildParams` (`openai-responses.ts:
   343-358`) into the Responses adapter: with an on-state effort, send
   `reasoning: {effort, summary: "auto"}` and `include:
   ["reasoning.encrypted_content"]` (Pi does this for every provider on this
   API, so it also corrects the existing `openai` rows; Azure and Codex have
   their own adapters and are unchanged). The off-state stays
   `reasoning: {effort: map.off ?? "none"}` with no summary. `ResolvedConstruction`
   gains `always_include_encrypted_reasoning` (openai-responses only:
   `provider_name == "xai" and spec.reasoning`), which adds the same `include`
   in the off-state too. The adapter already skips non-message output items.
4. **Copilot request shape.**
   - `provider.py` gains `copilot_dynamic_headers(request, images_sent)`:
     `X-Initiator` from the last `request.messages` entry (`user` when there
     are none — the legacy single-turn prompt is a user turn),
     `Openai-Intent`, and `Copilot-Vision-Request` when `images_sent`. The
     anthropic and Responses adapters pass `bool(request.attachments)` (they
     render attachments); the Chat Completions adapter passes `False` (it does
     not render attachments, so no image is sent). pipy tool results carry no
     images. Applied after the row headers and before the request-header hook,
     only when `provider_name == "github-copilot"`.
   - Anthropic adapter: for `github-copilot`, `Authorization: Bearer <key>`
     instead of `x-api-key` (an explicit models.json Authorization still wins)
     and no session-affinity header.
   - `_resolve_family_thinking`: Responses off-state returns no effort for
     `provider_name == "github-copilot"`.
   - Chat Completions default ("openai" format) branch: gate the on-state
     `reasoning_effort` on `_supports_reasoning_effort(spec)` for
     `api == "openai-completions"` (Pi's default-branch gate). This also
     applies to custom rows that set `supportsReasoningEffort:false` or hit an
     exclusion signal — matching Pi. The off-state string branch
     (`map.off` string) is left as-is (no current row reaches it; noted).
5. **Copilot OAuth (`oauth_providers.py`).** Rewrite
   `GitHubCopilotOAuthProvider` to Pi's shape with the injectable transport plus
   injectable `sleep`/clock: `login(prompt, notify)`, `refresh_token(cred)`
   (exchange + model list), `request_base_url(cred)` (toAuth), `filter_models`
   (availableModelIds), device-code polling, model-list parsing, policy
   enabling with a bounded 429 retry (max 2 retries, 5 s budget, exponential
   500 ms backoff; `Retry-After` is not read because the stdlib transport
   returns no headers — deviation). Remove the load-time `modify_models`
   base-URL rewrite and `enable_model`; the per-request `request_base_url`
   replaces the former (Pi removed `modifyModels` for Copilot) — no shim.
6. **Per-request OAuth (`oauth_providers.py`, `provider_construction.py`,
   `repl_state.py`).**
   - Thread ownership: `AuthStore` is read only on its owner (session) thread.
     `ModelRuntime.construct` (owner thread) takes a detached snapshot of the
     stored github-copilot OAuth credential (`store.get` returns a copy) and
     hands only that snapshot to the per-request provider. `complete()` (which
     may run on a worker thread) never touches the `AuthStore`.
   - `OAuthCredentialCache` (owned by `ProviderCatalogState`, its own lock,
     holds no `AuthStore` reference) maps provider id to the freshest
     credential seen for a given `refresh` value. `fresh(provider, snapshot)`
     under the lock: take the cached credential when it has the snapshot's
     `refresh` value and a later `expires`, else the snapshot; refresh it when
     `now + 5 min >= expires`; store and return the result. It never writes
     the `AuthStore` or the auth file; the short-lived token is re-exchanged
     at most once per expiry per process. Not persisting is a deviation from
     Pi (which persists under a file lock), documented.
   - `resolve_construction(..., oauth_credential=...)` runs only on the owner
     thread, inside `ModelRuntime.construct`. Given an OAuth credential (and no
     runtime key override), the API key is `credential.access` and the base URL
     is `request_base_url(credential)`; headers, routing and thinking resolve
     as for any row. At construct time the credential is
     `cache.current(snapshot)` (no network refresh while binding).
   - `ModelRuntime.construct` wraps github-copilot OAuth rows in a
     `PerRequestAuthProvider` holding the owner-thread `ResolvedConstruction`,
     the spec/thinking/options needed by `build_provider`, the credential
     snapshot, and the cache. Every `complete()` calls `cache.fresh(snapshot)`
     and builds the adapter from
     `replace(resolved, api_key=fresh.access, base_url=request_base_url(fresh))`
     — it never re-runs `resolve_construction` and never reads the
     `AuthStore`. The only token-derived header is the `Authorization` that
     `authHeader: true` (models.json provider config) generates; the wrapper
     records that flag at construct time and regenerates
     `Authorization: Bearer <fresh access>` per request, so a refreshed token
     never leaves a stale explicit header (tested). Every other resolved header
     is token-independent. A refresh failure returns a failed `ProviderResult` with a safe
     error (Pi fails the request). Pi resolves auth per request; pipy binds
     providers across turns, and the Copilot token lives ~30 min. `/login`,
     `/logout`, and model switches rebind the provider on the owner thread,
     which takes a new snapshot.
   - `ProviderCatalogState.get_available()` (owner thread) applies
     `filter_models` using the cache's refreshed credential when it matches
     the stored `refresh` value, else the stored credential, so a refresh that
     changes `availableModelIds` is reflected (Pi `filterModels`).
7. **Login/logout (`repl_state.py`).** `/login github-copilot` runs the device
   flow through the existing line-based callbacks (prompt, device code,
   progress), stores `{type:"oauth", ...}` in the auth store, and does not open
   a browser. `/logout github-copilot` removes it and resets a copilot
   selection. Error messages name the provider instead of `openai-codex`.
   `availability_reason` reports `login-required` for github-copilot.
8. **Drift tool.** Rows are compared automatically once carried. Add `headers`
   to the compared value fields (only Copilot/NVIDIA rows carry headers in Pi;
   NVIDIA is not carried). Expect 0 drift, 0 stale; no allowlist entries should
   be needed.

## Deviations (documented, not implemented)

- xAI OAuth (SuperGrok/X Premium) login.
- Cost tiers (both providers) — pricing slice.
- Refreshed Copilot tokens are not persisted; `Retry-After` is not honored in
  the policy/model-list 429 retry.
- The completions default-branch off-state string (`reasoning_effort = map.off`)
  is not ported.

## Done when

- Rows, defaults, registry, env vars land; `just catalog-drift` CLEAN with
  both providers compared (0 drift, 0 stale).
- Request-shape tests: Responses on-state `summary:"auto"` + `include` for
  openai, xai and Copilot rows; off-state without summary; xai include on/off;
  grok-4.3 off `effort:"none"`,
  grok-4.7 off no reasoning; copilot dynamic headers per family (user/agent
  initiator, vision on attachments for anthropic/responses, never for
  completions); copilot anthropic Bearer, no x-api-key; copilot responses
  off-state omitted for gpt-6-sol; copilot completions no `reasoning_effort`;
  static row headers sent; base URL from token proxy-ep / enterprise /
  default.
- OAuth tests with fake transport and fake sleep: device flow success,
  pending→slow_down→success intervals, failure, timeout, invalid URI, enterprise
  domain normalization, model-list parsing incl. policy fallback, policy
  enabling incl. 429 stop, refresh updates availableModelIds, cache refresh
  window and no store writes, per-request wrapper refreshes an expired token
  between turns.
- `/login github-copilot` and `/logout github-copilot` through the REPL state
  with fakes; `get_available` filtering.
- Docs: `docs/providers.md`, `docs/provider-catalog.md`, CHANGELOG, backlog
  item 9 marked done. `just check` green.

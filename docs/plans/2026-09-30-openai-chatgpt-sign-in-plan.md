# OpenAI Sign in with ChatGPT — parity plan

Slice from the `docs/backlog.md` follow-on list. Pi reference
`~/src/pi-mono` @ `1b347794e`, commits `02eed88fd` (feature) and `72abf01ba`
(bundle-only fix: the lazy OAuth entry list; no runtime behaviour to port).

## What Pi does

1. **OAuth method on the `openai` provider** (`packages/ai/src/providers/openai.ts`,
   `auth/oauth/openai-chatgpt.ts`). The `openai` provider keeps
   `envApiKeyAuth(OPENAI_API_KEY)` and gains
   `oauth: {name: "OpenAI (ChatGPT subscription)", isSubscription: true,
   loginLabel: "Sign in with ChatGPT"}`.
2. **Login flow** (`loginOpenAIChatGPT`):
   - `hostId = "urn:uuid:" + deviceId.toLowerCase()`; the device id comes from
     `LoginOptions.getDeviceId()` and must match
     `/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i`, else
     `Sign in with ChatGPT requires a device ID (UUID) for this installation`
     (thrown before anything else happens).
   - PKCE (`verifier = base64url(32 random bytes)`,
     `challenge = base64url(sha256(verifier))`), `state` and `nonce` =
     `base64url(32 random bytes)`.
   - Callback server on `PI_OAUTH_CALLBACK_HOST || 127.0.0.1`, port `1455`,
     path `/auth/callback`; `redirect_uri` is always
     `http://127.0.0.1:1455/auth/callback`. Route other than the path → 404;
     `error` param → 400 and the login rejects with
     `ChatGPT authorization failed: <error>`; an invalid callback (missing
     code, missing state, state mismatch, missing/blank `client_id`) → 400 and
     the server keeps waiting; success → 200. If the server cannot listen, Pi
     notifies `info` `Could not listen on <redirect>; paste the final redirect
     URL to continue. <error>` and continues manual-only.
   - Authorize URL `https://auth.openai.com/api/accounts/authorize` with, in
     order: `client_id=dynamic_agent_client`, `agent_name_hint=Pi`,
     `ext_agent_host_id=<hostId>`, `response_type=code`,
     `redirect_uri`, `resource=https://api.openai.com/v1`,
     `scope=openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`,
     `state`, `code_challenge`, `code_challenge_method=S256`, `nonce`.
     Notified as `auth_url` with the instructions
     `Complete sign-in in your browser. If the callback does not complete, paste the final redirect URL here.`
   - A `manual_code` prompt (`Complete login in your browser, or paste the final
     redirect URL here:`, placeholder = redirect URI) races the callback.
     Manual input must parse as a URL whose origin+path equal the redirect URI
     (`Paste the full callback URL from the browser` /
     `The pasted callback URL must start with <redirect>`); an `error` param
     rejects; then the callback rules (code, state, state match, `client_id`)
     apply, with the messages `Missing authorization code`,
     `Missing OAuth state`, `OAuth state mismatch`,
     `OpenAI OAuth registration callback did not contain an issued client ID`.
   - Progress `Exchanging authorization code for tokens...`, then POST
     `https://auth.openai.com/api/accounts/oauth/token` (form,
     `accept: application/json`) with `grant_type=authorization_code`,
     `client_id=<issued client id from the callback>`, `code`,
     `code_verifier`, `redirect_uri`, `resource`. A non-2xx response throws
     `OpenAI OAuth token request failed (<status>): <body or statusText>`; a
     non-object JSON body throws. The exchange additionally requires a
     non-blank `id_token` string (`...did not contain an ID token`); it is not
     stored or decoded.
   - Credential (field list, all required): `type: "oauth"`, `access`
     (non-blank string), `refresh` (non-blank string),
     `expires = now + expires_in*1000 - 180000` (`expires_in` a finite
     number > 0; 3-minute margin), `clientId` (the issued client id),
     `scopes` (the whitespace-split `scope` string, which must be non-blank and
     must contain `chatgpt.tokens.use.direct`, else
     `OpenAI OAuth grant did not include chatgpt.tokens.use.direct`).
   - Refresh: requires a non-blank stored `clientId`
     (`Stored OpenAI OAuth credential does not contain an issued client ID; reconnect ChatGPT`);
     POST `grant_type=refresh_token`, `client_id`, `refresh_token`,
     `resource`; the response goes through the same credential validation (no
     `id_token` requirement). `toAuth` = `{apiKey: credential.access}` — base
     URL unchanged.
   - Pi refreshes a stored OAuth credential inside `credentials.modify` under
     the auth-file lock (`auth/resolve.ts` `resolveStoredOAuth`): re-check
     expiry under the lock, refresh once, persist the rotated credential before
     releasing; a credential logged out meanwhile yields no auth.
3. **`deviceId` setting** (`settings-manager.ts`): `getOrCreateDeviceId()` reads
   only the *global* settings (`if (!globalSettings.deviceId)` → `randomUUID()`,
   saved to the global file); project settings are ignored. Interactive
   `/login` passes `{getDeviceId: () => settingsManager.getOrCreateDeviceId()}`.
   `bug-report.ts` redacts it (pipy has no bug report).
4. **Request shape** (`api/openai-responses.ts` `buildParams`, fields this slice
   changes): `isChatGPTSignIn = model.provider === "openai" &&
   model.baseUrl === "https://api.openai.com/v1" && apiKey !== undefined &&
   !apiKey.startsWith("sk-")`. When true, `prompt_cache_retention`,
   `prompt_cache_options`, `max_output_tokens` and `temperature` are omitted.
   Unchanged on that path: `prompt_cache_key`, the session-affinity headers,
   `store: false`, reasoning, tools.
5. **Usage-limit error**: in the `openai-responses` stream error path (any
   provider on that API), an error message containing
   `subscription_sharing_usage_limit_exceeded` gets
   `\nCheck your ChatGPT usage: https://chatgpt.com/settings/usage` appended —
   both for an HTTP rejection (429 body) and a `response.failed` event.
6. **Retry classifier** (`utils/retry.ts`): non-retryable pattern gains
   `subscription_sharing_usage_limit_exceeded`; retryable pattern gains
   `subscription_sharing_usage_unavailable` and
   `subscription_sharing_user_unavailable`.
7. **Rename**: the `openai-codex` provider's display `name` becomes
   `OpenAI Codex (legacy)`.
8. **COST1**: `ModelRuntime.isUsingSubscription("openai")` becomes true when
   the stored credential is OAuth (the method is `isSubscription: true`), so
   the footer cost shows `(sub)`.

## How pipy matches it

- `native/oauth_providers.py`: new `OpenAIChatGPTOAuthProvider` next to the
  existing built-ins (`id = "openai"`, `name`, `login_label`), with `login`,
  `refresh_token`, `get_api_key`, and the callback-server/parse helpers ported
  one to one. Injectable transport, clock, authorize/token URLs, callback
  host/port (tests use port 0 and a local stub token server). The existing
  shared `Transport` (form body, status, text) is reused. PKCE: a
  `generate_pkce()` helper used by both this flow and the Codex
  `create_authorization_flow` (Pi shares `pkce.ts` between them).
  Register `"openai"` in `_BUILTIN_OAUTH_PROVIDERS`, so
  `get_oauth_provider_ids()` includes it and `is_using_subscription("openai")`
  follows the stored credential type with no further change (COST1).
- **Per-request refresh with persistence.** Add `"openai"` to
  `PER_REQUEST_OAUTH_PROVIDERS`, so `ModelRuntime.construct` binds a
  `PerRequestOAuthProvider` over a detached snapshot when the stored
  credential is OAuth (an `api_key` credential or env key keeps the plain
  path). `_oauth_request_auth` takes the resolved base URL as the default
  when a provider has no `request_base_url` (Pi `toAuth` returns only
  `apiKey`). OpenAI rotates refresh tokens, so unlike Copilot the refreshed
  credential must be persisted: `OAuthCredentialCache` gets the auth-file path
  and, for providers in a new `PERSISTED_OAUTH_REFRESH_PROVIDERS = {"openai"}`,
  runs Pi's `resolveStoredOAuth` shape: under an exclusive auth-file lock,
  re-read the stored entry; logged out → `OAuthError`; no longer expiring →
  use it (another process refreshed); else refresh, write the rotated
  credential, release. A new `modify_stored_credential(path, provider, fn)` in
  `auth_store.py` is the locked single-key read-modify-write (Pi
  `AuthStorage.modify`). To keep an owner-thread write from reverting the
  rotated credential, `AuthStore.set`/`remove` become the same locked
  single-key merge against the file (Pi `modify`/`delete`), updating the
  in-memory data to the merged result. The lock is `fcntl.flock` on an
  `auth.json.lock` sidecar plus an in-process lock; worker threads never touch
  the `AuthStore` object.
- **deviceId**: `SettingsManager.get_or_create_device_id()` reads the global
  scope only (`raw_scope(SCOPE_GLOBAL)`), creates `str(uuid4())` when the value
  is falsy and persists it with `set_value("deviceId", ..., scope=global)`.
- **`/login openai`** (`repl_state.NativeReplProviderState.login`): after the
  extension-provider check (an extension may still own `openai`), run the
  ChatGPT flow through the line-based callbacks already used for Copilot and
  extension OAuth; `auth_url` also opens the browser (Pi `showAuth` calls
  `openBrowser`), through an injectable opener. Store the credential under
  `openai` in the auth store. `/logout openai` removes the stored `openai`
  entry through the existing `_stored_oauth_logout`. A new injectable
  `device_id_provider` on the provider state is wired in `cli.py` to the
  settings manager; absent → Pi's device-id error.
- **Request shape** (`providers/openai_responses.py`): pass the resolved base
  URL into the adapter; `_apply_prompt_cache_fields` skips
  `prompt_cache_retention` and `prompt_cache_options` when
  `provider_name == "openai" and base_url == "https://api.openai.com/v1" and
  api_key is not None and not api_key.startswith("sk-")` (exact Pi predicate,
  including an env key that does not start with `sk-`). `max_output_tokens` and
  `temperature`: pipy's Responses adapter sends neither today (no
  maxTokens/temperature options exist), so they are already absent on this
  path — recorded, no code.
- **Usage-limit link**: in the adapter's failure path, when the error text
  (message + lifted `api_error_type`/`api_error_code`) contains
  `subscription_sharing_usage_limit_exceeded`, the visible message becomes
  `<pipy status message> subscription_sharing_usage_limit_exceeded: <sanitized
  provider error.message, when present>` followed by Pi's
  `\nCheck your ChatGPT usage: https://chatgpt.com/settings/usage`, so the user
  sees the code, the provider's words and the link as in Pi
  (`<label> (<status>): <code>: <message>\nCheck ...`). Other errors keep
  pipy's existing sanitized status messages (pipy's adapters do not carry
  provider error prose in general; that stays a separate, pre-existing
  difference). pipy's Responses adapter is non-streaming, so the
  `response.failed` case is the final body with `status: "failed"`:
  `parse_response` lifts `error.code` / `error.type` into `api_error_code` /
  `api_error_type` metadata, so the link, the visible text and the retry
  classifier see the code on both paths. The provider's `error.message` is
  lifted (sanitized) only for the usage-limit code, and only into the visible
  text, never the result metadata: other provider error prose can echo request
  content (an existing test pins that). pipy's provider error messages are
  sanitized to one line, so the link follows after a space instead of Pi's
  newline. Tests assert the full visible text for the 429 and the failed-body
  cases.
- **Retry** (`agent/provider_retry.py`, F4's classifier): add the three
  patterns exactly where Pi has them. `retry_error_text` already appends
  `api_error_code`, so a 429 with the limit code is not retried and the two
  unavailable codes are.
- **Rename**: pipy has no provider display-name registry (the `/login`
  surface, footer and `--list-models` show provider ids), so there is no pipy
  surface for `OpenAI Codex (legacy)`; recorded as not applicable. The
  `/login`/`/logout` command descriptions and the settings hint name `openai`
  next to `openai-codex` and `github-copilot`.

## Deliberate deviations (documented)

- `agent_name_hint` is `pipy`, not `Pi` — the consent screen names the app the
  user runs; same precedent as the Codex flow's `originator: "pipy"`.
- Line-based `/login`: pipy's `/login` reads lines, so the manual prompt cannot
  race the callback. Pressing Enter on an empty line waits for the browser
  callback (Ctrl-C cancels); a pasted URL is validated with Pi's rules. The
  pre-existing gap that pipy's `/login` has no auth-type selector ("Sign in
  with ChatGPT" / "Sign in with an API key") or API-key dialog stays open.
- HTML callback pages are plain short pages (no Pi `oauth-page.ts` styling).
- The ChatGPT flow prints its URL and progress text with control whitespace
  collapsed but without pipy's secret-word redaction: the authorize URL's
  scope (`chatgpt.tokens.use.direct`) and "Exchanging authorization code for
  tokens..." would otherwise print as `[REDACTED]`. The token endpoint's error
  body keeps that redaction.
- `/logout openai` (and `github-copilot`) moves the selection only when the
  provider has no other auth left; Pi never moves it.
- Pi's 15 s refresh timeout: pipy's shared OAuth transport keeps its 30 s.

## Tests (done-when)

1. `tests/test_openai_chatgpt_oauth.py`: authorize URL params (order and
   values, lower-cased host id), device-id validation error, manual-URL login
   against a stub token server (exchange body, stored credential fields and
   expiry margin, `id_token` required, scope check, `client_id` required),
   callback-server login (a real HTTP GET to the local callback on port 0:
   404 route, invalid callback keeps waiting, `error` rejects), port-busy
   info notice, refresh (body, rotated credential, missing `clientId`).
2. Auth store: `modify_stored_credential` single-key merge; `set`/`remove`
   preserve an entry another writer changed on disk.
3. Credential cache: persisted refresh writes the rotated credential; a
   stored credential refreshed by "another process" is reused without a
   network refresh; logged-out-meanwhile fails; Copilot keeps the in-memory
   path.
4. Construction: stored OAuth `openai` binds `PerRequestOAuthProvider`; a
   request sends `Bearer <access>` to `https://api.openai.com/v1/responses`
   with no `prompt_cache_retention`/`prompt_cache_options`; an expiring
   credential refreshes through the stub and is persisted; an `sk-` key keeps
   the fields; a non-OpenAI base URL keeps them.
5. Adapter: usage-limit link on a 429 and on a `failed` body; other errors
   unchanged.
6. Retry: limit code not retried; the two unavailable codes retried.
7. Settings: `get_or_create_device_id` creates once, persists globally,
   ignores a project value, reuses across managers.
8. REPL state: `/login openai` stores the credential (stub flow, injected
   browser opener and device id), `/logout openai` removes it,
   `is_using_subscription("openai")` true for OAuth and false for an API key.
9. Existing fixtures: the adapter's new `base_url` is set only by catalog
   construction (a directly constructed adapter has `None`, so the
   `test_native_prompt_cache_affinity.py` fixtures with `api_key="key"` keep
   their cache fields). Construction-path tests that resolve `openai` with a
   non-`sk-` key (`OPENAI_API_KEY: "k"`/`"ok"`/`"x"`) and assert cache fields
   switch to `sk-` keys, as Pi did in `cache-retention.test.ts` and
   `openai-responses-compat.test.ts`; the whole prompt-cache and construction
   suites run in `just check`.
10. Docs and release notes: `docs/providers.md` (the `openai` row gains Sign in
    with ChatGPT; `/login openai`, the empty-line callback wait, `/logout
    openai`, the global `deviceId`, the omitted request fields, the usage link
    and retry codes), `docs/provider-catalog.md` (per-request OAuth now covers
    `openai` with persisted rotation), `docs/settings.md` (`deviceId`),
    `CHANGELOG.md` `[Unreleased]`, and the `docs/backlog.md` entry struck.
11. Live: one `openai-codex` `gpt-6.1-sol` turn with isolated config/sessions
    and the real Codex auth read-only still works.

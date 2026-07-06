# Extension OAuth /login wiring plan

Gap: extension-provider OAuth `/login` and auth-storage wiring.

Pi reference paths:

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/model-registry.ts` lines around `registerProvider`: Pi validates dynamic provider config and, when `config.oauth` is present, registers an OAuth provider object with `id` derived from the provider name (`{...config.oauth, id: providerName}`). Unregister/refresh reset dynamic OAuth providers alongside dynamic providers.
- `/Users/jochen/src/pi-mono/packages/ai/src/utils/oauth/index.ts`: OAuth provider registry exposes built-ins plus dynamic `registerOAuthProvider`, `unregisterOAuthProvider`, and `getOAuthProvider`. The stored credential shape is `{type:"oauth", ...credentials}` keyed by provider id.
- `/Users/jochen/src/pi-mono/packages/ai/src/cli.ts`: login calls `provider.login(callbacks)`, stores `{type:"oauth", ...credentials}`, and lists/selects providers from the OAuth registry. Callbacks provide URL/device-code/prompt/select/progress UI.

Pi field list and optionality for this slice:

- Extension provider OAuth metadata: `name` required non-empty string; `login(callbacks)` required; `refreshToken(credentials)` required; `getApiKey(credentials)` required; `modifyModels(rows, credentials)` optional. Pipy already exposes the Python snake_case equivalent as `ExtensionOAuthConfig(name, login, refresh_token, get_api_key, modify_models=None)` and validates it fail-closed during activation.
- Derived identifier: OAuth provider id is the normalized extension provider name, not a user-supplied OAuth id. This slice must preserve that derivation.
- Stored auth entry: Pi stores the object returned by `login` plus `type:"oauth"`, keyed by the OAuth/provider id. Pipy should store exactly that safe shape in `AuthStore` for extension providers.
- Request auth: Pi resolves OAuth credentials through the provider's `getApiKey`/refresh layer. Pipy extension provider factories do not receive the shared auth store; this slice should add only storage/availability/model-login wiring, not pass credentials into arbitrary provider factories.

Pipy implementation plan:

1. Add an extension OAuth registry helper in `extension_runtime` that projects activated `RegisteredProvider` values with `provider.oauth is not None` into a map keyed by provider name. Built-ins remain owned by `oauth_providers.py`; dynamic extension OAuth is per-run and comes from activated extensions.
2. Thread that map through `ProviderCatalogState`: extension OAuth providers should count as available only when their provider has stored credentials in `AuthStore` (matching Pi provider auth gating for OAuth-backed registered providers), not unconditionally. Non-OAuth extension providers remain available as today because their factory owns auth.
3. Extend `NativeReplProviderState.login/logout` so `/login <extension-provider>` and `/logout <extension-provider>` call the extension OAuth callbacks, store/remove `AuthStore` entries under the provider name, and refresh catalog availability. The login callbacks should be deterministic stdio callbacks: print URL/device/progress/select prompts safely and read prompt/select answers from the supplied streams. Awaitables are out of scope for this stdlib sync slice and should fail closed with a bounded unsupported diagnostic.
4. Add focused tests for: OAuth extension provider is unavailable before login and available after storing credentials; `/login` invokes callbacks and persists `{type:"oauth", ...credentials}` under the derived provider name; `/logout` removes those credentials; non-OAuth extension provider availability is unchanged.
5. Update `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, and `docs/backlog.md` to mark extension OAuth `/login` wiring shipped for the bounded provider-name/id + auth-store slice, while keeping provider-factory credential injection and broader provider config helpers deferred.

Done when:

- Focused tests pass.
- `just check` passes.
- Different-family review returns CLEAN over the plan and then the complete diff.

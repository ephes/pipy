# Extension OAuth /login implementation plan

1. Registry projection
   - Add `extension_oauth_providers(activated)` returning a deterministic mapping of normalized provider name to `RegisteredProvider` for extension providers with OAuth metadata.
   - Acceptance: duplicate/hidden behavior follows existing accepted `RegisteredProvider` list; no callbacks invoked while building the map.

2. Availability gating
   - Store the OAuth map on `ProviderCatalogState`, update it with extension provider contributions, and make OAuth-backed extension providers unavailable until `AuthStore.get(provider)` has `type == "oauth"`.
   - Acceptance: non-OAuth extension providers stay available; OAuth-backed rows report `login-required` before credentials and available after credentials.

3. Login/logout runtime
   - Extend `NativeReplProviderState.login/logout` to recognize extension OAuth providers from the catalog state.
   - Implement bounded sync stdio callback adapter for `onAuth`, `onDeviceCode`, `onPrompt`, `onSelect`, and `onProgress`; store `{"type":"oauth", **credentials}` under the derived provider name; remove on logout.
   - Acceptance: callbacks are invoked, prompt/select read supplied streams, unsupported awaitables/failures return sanitized failure messages, and openai-codex behavior remains unchanged.

4. Tests
   - Add focused unit coverage in provider/auth tests for availability, login persistence, and logout removal.
   - Run the focused tests before full `just check`.

5. Docs
   - Update extension API and parity docs/backlog to mark the bounded OAuth login/auth-store wiring shipped and keep credential injection/broader provider config deferred.

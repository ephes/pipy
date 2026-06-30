# Pi-Style Provider And Model Catalog

Status: catalog foundation (2026-06-02) plus product provider construction for
the OpenAI-Chat-Completions API family (2026-06-03), the Tier 1 api-key families
`anthropic-messages`, `openai-responses`, `mistral`, the Tier 2 composed-endpoint
families `google-generative-ai`, `azure-openai-responses`,
`cloudflare-workers-ai`, and the Tier 3 IAM families `amazon-bedrock`,
`google-vertex` (2026-06-03), gated end to end. Every real adapter family is now
catalog-constructed except `openai-codex-responses` (kept on the legacy factory
for its settings-derived `RetryPolicy`) and the deterministic `fake` bootstrap.
A models.json custom provider/model now runs a real turn using the catalog
baseUrl/model/auth/headers/routing/thinking, in both the REPL and the one-shot
`pipy run` path. Startup `--native-provider`/`--native-model` resolution now
also routes through the catalog. Extension-registered providers now contribute
temporary per-run catalog rows and construct through their registered
`ProviderPort` factories. The provider/model catalog construction track is
fully wired for current provider sources (only the deliberate
`openai-codex` legacy-factory exception and documented adapter follow-ons remain
— see below).

## Implemented (foundation + product construction)

The catalog is implemented through pipy-owned Python modules and gated by
`scripts/parity_checks/provider_catalog_conformance.py`, which now covers all of
Verification-Plan items 1-25 — including product provider-construction paths
for Chat Completions, non-completions families, `pipy run`, and startup
resolution plus extension-provider catalog wiring — at the construction/request
layer with capturing fake HTTP clients, all passing with no network. Product
turns for the
`openai-completions` API family (custom models.json providers, ds4, OpenRouter,
openai-completions) construct from the catalog via `native/provider_construction.py`
(`resolve_construction` + `build_provider`), invoked through
`NativeReplProviderState.provider_for`/`current_provider`:

- **Built-in catalog** (`native/catalog.py`, `native/catalog_data.py`):
  `NativeModelSpec`/`NativeModelCost` rows with real capability metadata,
  multiple rows per implemented provider, `default_model_per_provider`. ds4 is
  intentionally absent.
- **Matcher** (`native/model_resolver.py`): `find_exact_model_reference`,
  `parse_model_pattern` (`provider/id:level`, colon-in-id, strict vs scope),
  fuzzy alias-over-dated, `resolve_model_scope` (minimatch-style globs where
  `*`/`?` do not cross `/`), `resolve_cli_model` (provider inference + fallback
  synthesis).
- **`models.json`** (`native/models_json.py`): comment/trailing-comma strip,
  pipy-owned validation with dot-path errors, provider/per-model override
  deep-merge, custom-model merge by `provider+id`, graceful degradation,
  `refresh()`, dynamic `register_provider`/`unregister_provider`, OAuth
  modify-models hooks.
- **Routing** (`native/routing.py`): OpenRouter `provider` param + Vercel
  `providerOptions.gateway` (gated on `only`/`order`) reach the request body for
  the completions family via `extra_body`.
- **Thinking** (`native/thinking.py`): six-level validation + per-model mapping;
  the mapped value reaches the request as `reasoning_effort` (OpenAI-style) or
  nested `reasoning.effort` (OpenRouter) for the completions family.
- **Auth** (`native/auth_store.py`): owner-only auth store, `resolve_config_value`
  (literal/env-name/`!command`), env + ambient credential detection (Bedrock,
  Vertex ADC, Azure, Cloudflare), Pi-order request-auth resolution, `AuthStatus`
  labels (no `!command`/refresh on status), availability gate. For the
  completions family the resolved api key + merged headers reach the real call
  (`--api-key` wins), and a catalog-wired auth failure fails closed.
- **OAuth** (`native/oauth_providers.py`): stdlib registry for Anthropic
  (5-min expiry margin), GitHub Copilot (proxy-ep rewrite + per-model policy
  enable), and OpenAI Codex (no margin), with injectable HTTP.
- **ds4 reframe** (`native/ds4.py` + `docs/examples/ds4.models.json`): ds4 is a
  `models.json` custom provider; the `PIPY_DS4_BASE_URL`/`PIPY_DS4_API_KEY`
  env shim synthesizes the same entry.
- **CLI/REPL** (`native/catalog_state.py`, `cli.py`, `native/repl_state.py`):
  `--list-models [search]` (Pi column parity, verified live against
  `pi --list-models`), the `/model` selector / `model_options()` over the full
  catalog with the shared availability gate (the tool-capability probe builds
  via `provider_for`, so a custom provider is probed as it will be used), and
  direct `/model <ref>` resolved through the shared `resolve_cli_model`
  (exact/bare/fuzzy/`:level`/colon-in-id/invalid-suffix fallback) gated by
  availability.
- **Extension providers** (`native/extension_provider_catalog.py`,
  `native/catalog_state.py`, `native/repl_state.py`): activated Python
  extensions that call `api.register_provider(ExtensionProvider(...))`
  contribute safe transient provider/model rows to the active catalog. Rows
  appear in `--list-models`, `/model`, startup selection, and scoped model
  matching for that run; selecting one constructs the registered `ProviderPort`
  with the selected model in `ProviderContext.model_id`. `/reload` recomputes
  contributions from current extension discovery and settings filters, and
  `unregister_provider(name)` hides extension rows while restoring any built-in
  provider rows it overrode. Absolute extension paths, factories, provider
  payloads, prompts, and source bodies are not catalog/listing metadata.

Tier 1 catalog construction (shipped 2026-06-03):

- `anthropic-messages`, `openai-responses`, and `mistral` are catalog-constructed
  by `build_provider`. Each derives its endpoint by appending the family path
  suffix to the catalog `base_url` (`/v1/messages`, `/responses`,
  `/chat/completions`), routes the resolved key into the family's native auth
  header (anthropic `x-api-key`; the others `Authorization: Bearer`, with an
  explicit models.json `Authorization` winning), merges models.json/model
  headers, and places the mapped thinking effort in the family's native body key
  (responses `reasoning.effort`; anthropic adaptive `output_config.effort` for
  the adaptive Claude models and `thinking.budget_tokens` via Pi's default
  per-level budgets otherwise, both with `display: "summarized"`; mistral
  `reasoning_effort`). Covered by conformance item 20.

Tier 2 catalog construction (shipped 2026-06-03):

- `google-generative-ai`, `azure-openai-responses`, and `cloudflare-workers-ai`
  are catalog-constructed by `build_provider` with per-family endpoint
  composition: google builds `base_url/v1beta/models/{model}:generateContent`
  with the key as the `?key=` query param (no auth header); azure normalizes the
  base to Pi's `/openai/v1` surface and composes
  `{normalized_base}/responses?api-version={api_version}` (default `v1`) with the
  deployment as the body `model` field, the `api-key` header, and the Responses
  `reasoning.effort` thinking shape;
  cloudflare substitutes `{ENV}` placeholders (e.g. `{CLOUDFLARE_ACCOUNT_ID}`)
  into the `base_url` — failing closed if a referenced var is unset, matching
  Pi's `resolveCloudflareBaseUrl` — appends `/chat/completions`, sends the
  resolved key as `Authorization: Bearer`, and uses the OpenAI-compatible
  top-level `reasoning_effort`. `{ENV}` base-URL substitution is implemented in
  `resolve_construction`. google's `thinkingConfig` shape is now injected
  per-model: a `thinkingLevel` enum for Gemini 3 Pro/Flash and Gemma 4, a
  `thinkingBudget` token count for the Gemini 2.5 family (`includeThoughts: true`
  when thinking is on), and a per-model *disabled* config (no `includeThoughts`)
  when a reasoning-capable model runs with thinking off/unset, matching Pi's
  `google.ts`. The effort comes from the existing `reasoning_effort`/
  `thinking_disabled` resolution; the adapter owns the per-family wire shape.
  Covered by conformance item 21.

Tier 3 catalog construction (shipped 2026-06-03):

- `amazon-bedrock` and `google-vertex` are catalog-constructed by
  `build_provider`. Their auth (AWS SigV4 / GCP ADC OAuth token) and the
  region/project-derived endpoint stay self-resolved by the adapter from the
  environment — the api-key shape does not apply, so the resolved api key is not
  forwarded as a credential. Catalog construction injects model_id +
  provider_name + merged headers, plus thinking for bedrock (Bedrock Claude
  speaks the Anthropic body, so thinking is placed at the body top level:
  adaptive thinking — `thinking:{type:"adaptive"}` + `output_config.effort` — for
  the adaptive Claude models (Opus 4.6/4.7/4.8, Sonnet 4.6, per Pi's
  `supportsAdaptiveThinking`), and the `thinking.budget_tokens` path otherwise;
  both paths force `display:"summarized"` except on GovCloud targets).
  Custom bedrock headers are merged into the SigV4-signed request with the
  reserved `authorization`/`host`/`x-amz-*` set dropped so they cannot collide
  with the signing headers. Vertex thinking now ships too: catalog construction
  forwards the resolved `reasoning_effort`/`thinking_disabled` into the
  `google-vertex` adapter, which injects Pi's per-model
  `generationConfig.thinkingConfig` from `google-vertex.ts` (its
  `THINKING_LEVEL_MAP` variant) in both Express (api-key) and ADC (bearer) modes:
  a `thinkingLevel` enum for Gemini 3 Pro/Flash, a `thinkingBudget` token count
  otherwise (`includeThoughts: true` when on), and a per-model *disabled* config
  (no `includeThoughts`) when a reasoning model runs with thinking off/unset. It
  deliberately diverges from `google-generative-ai` in two places (matching
  `google-vertex.ts`): **no** `2.5-flash-lite` budget table — flash-lite falls
  into the `2.5-flash` branch (minimal `128`, not `512`) — and **no** Gemma 4
  special-casing (Gemma is not a Vertex Gemini model).
  Covered by conformance item 22.
- `openai-codex-responses` is deliberately NOT catalog-constructed: the legacy
  factory builds it with a settings-derived `RetryPolicy` (cli.py) that catalog
  construction would drop, and its OAuth/SSE auth is fully self-contained.
  `build_provider` returns `None` for it so it keeps the legacy factory.

Remaining adapter/product follow-ons:

- Vertex API-key auth has shipped: the vertex adapter now supports Pi's Vertex
  Express api-key mode alongside the existing ADC bearer path. When an api key
  resolves (runtime `--api-key`, stored key, or `GOOGLE_CLOUD_API_KEY`) and is not
  a placeholder/sentinel, the request goes to the global
  `https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent`
  host (no project/location path segment) with the `x-goog-api-key` header,
  mirroring the `@google/genai` Express path that Pi's `createClientWithApiKey`
  drives. Catalog construction forwards the resolved key to the adapter (bedrock's
  is still not forwarded); the `<authenticated>` ambient sentinel and the
  `gcp-vertex-credentials` marker are rejected as express keys (placeholder
  pattern) and fall back to ADC, matching Pi's `resolveApiKey`. `google-vertex`
  is now available with either `GOOGLE_CLOUD_API_KEY` or ADC creds. Covered by
  conformance item 22 (22c/22e). Native service-account JWT signing for the ADC
  path remains a future extension.
- Anthropic-messages adaptive thinking has shipped: the `anthropic-messages`
  adapter now switches the adaptive Claude models (Opus 4.6/4.7/4.8, Sonnet 4.6,
  per Pi's `compat.forceAdaptiveThinking`) to the adaptive
  `thinking: {type: "adaptive"}` + `output_config.effort` shape, and keeps the
  `thinking.budget_tokens` path for older reasoning Claude models. Both paths
  send `display: "summarized"` (Pi forces this; the adaptive models' API default
  is `"omitted"`). The adaptive model markers, the `minimal -> low` effort clamp,
  and `supports_adaptive_thinking` are shared with the bedrock adapter from
  `anthropic_provider`. The bedrock adapter now also forces
  `display: "summarized"` on both its adaptive and budget thinking paths,
  omitting it only on GovCloud targets (configured region `us-gov-*`, or a model
  id starting `us-gov.` / `arn:aws-us-gov:`, whose Converse schema rejects the
  field), matching Pi's `isGovCloudBedrockTarget` carve-out.
- Explicit `thinking: {type: "disabled"}` has shipped for the
  `anthropic-messages` adapter: when a reasoning-capable Claude model is run with
  thinking off/unset, the request body now carries `thinking: {type: "disabled"}`
  (only `type`, no `display`/budget) rather than omitting the key, matching Pi's
  product path (`streamSimpleAnthropic` passes `thinkingEnabled: false` whenever
  the resolved level is falsy, and `buildParams` emits the disabled shape under
  `if (model.reasoning)`; anthropic.ts:746-748, 949-977). The model's
  reasoning-capability intent is threaded through `ResolvedConstruction`
  (`thinking_disabled`, computed from `spec.reasoning` and the raw off/unset
  level, mutually exclusive with `reasoning_effort`) into the adapter; an
  unsupported thinking level on a reasoning model stays out of the disabled
  branch (Pi treats that as still-thinking-and-clamp), and non-reasoning models
  still omit `thinking` entirely. The **bedrock** adapter is intentionally **not**
  changed: Pi's `buildAdditionalModelRequestFields` returns `undefined` (omits the
  thinking fields) when reasoning is off/unset or the model is non-reasoning
  (amazon-bedrock.ts:943-949) — it has no disabled shape — and pipy's bedrock
  adapter already omits `thinking` when no effort is resolved, so it is already
  Pi-correct.
- Azure URL/api-version parity has shipped: the azure adapter now matches Pi's
  `AzureOpenAI` SDK v1 surface. The default api-version is `v1` (overridable by
  `AZURE_OPENAI_API_VERSION`); Azure-host base URLs (`*.openai.azure.com`,
  `*.cognitiveservices.azure.com`) whose path is empty/`/`/`/openai` are
  normalized to `/openai/v1` (mirroring Pi's `normalizeAzureBaseUrl`); the
  request URL is `{normalized_base}/responses?api-version={api_version}` (the
  deployment is no longer a URL path segment); and the deployment name is carried
  as the body `model` field (Pi's `buildParams` sets `model: deploymentName`).
  Custom/non-Azure base URLs are respected verbatim. Covered by conformance
  item 21. The Azure config-source conveniences now match Pi's
  `resolveAzureConfig`/`resolveDeploymentName`: the base URL resolves from env
  `AZURE_OPENAI_BASE_URL` (trimmed) > a default base built from the resource name
  (`resource_name` field or env `AZURE_OPENAI_RESOURCE_NAME`, read verbatim) as
  `https://{name}.openai.azure.com/openai/v1` > the catalog/`models.json`
  `base_url` (Pi's `model.baseUrl`); the deployment resolves from the explicit
  `deployment` field > the `AZURE_OPENAI_DEPLOYMENT_NAME_MAP`
  (`modelId=deployment,...`) map keyed by `model_id` > `model_id` itself; and the
  api-version resolves from `AZURE_OPENAI_API_VERSION` or `v1`. The pipy-only
  `AZURE_OPENAI_ENDPOINT` env name was dropped for parity with Pi's
  `AZURE_OPENAI_BASE_URL`.

- OpenRouter reasoning off-state has shipped: for the `openai-completions`
  OpenRouter thinking format, a reasoning-capable model run with thinking
  off/unset now emits `reasoning: {effort: <off-value>}` to disable reasoning at
  the router rather than omitting the field, matching Pi's
  `thinkingFormat === "openrouter"` off branch (`openai-completions.ts:578-580`).
  The off value follows Pi's `thinkingLevelMap?.off ?? "none"` after the
  `!== null` gate: an absent `off` mapping emits `"none"` (the built-in OpenRouter
  reasoning rows, which carry no `thinking_level_map`), an explicit `null`/`None`
  off mapping suppresses the emission, and a string off mapping is emitted
  verbatim. The branch is gated on the model being reasoning-capable and the raw
  level being off/unset (not an unsupported clamped level, which Pi treats as
  still-thinking-and-clamp), mirroring the `anthropic-messages`
  `thinking: {type: "disabled"}` off-state gate. The on-state nested
  `reasoning: {effort: <level>}` emission is unchanged. Covered by conformance
  item 18 (18d). The other completions `thinkingFormat` variants (qwen,
  ant-ling, string-thinking) and a full `detectCompat` port remain separate
  follow-ons.

- DeepSeek reasoning request shape has shipped: for the `openai-completions`
  `deepseek` thinking format, a reasoning-capable model emits a top-level
  `thinking: {type: "enabled"|"disabled"}` object — `enabled` when a level is
  active, `disabled` (a Pi-forced explicit disable, like the OpenRouter
  `effort:"none"` and anthropic `type:"disabled"` off-states) when thinking is
  off/unset — and additionally sends top-level `reasoning_effort` on the on-state
  when the model `supportsReasoningEffort`, matching Pi's `thinkingFormat ===
  "deepseek"` branch (`openai-completions.ts:565-570`). The format is resolved
  with Pi's `getCompat` precedence (explicit `compat.thinkingFormat` over the
  `deepseek`/`deepseek.com` provider/base-URL detection), and
  `supportsReasoningEffort` is resolved **independently** of `thinkingFormat`
  via Pi's `detectCompat` exclusion list (xAI/z.ai/Moonshot/Together/Cloudflare
  AI Gateway/Nvidia/ant-ling), so an explicit `thinkingFormat="deepseek"` on an
  excluded provider correctly omits `reasoning_effort`. The off-state is gated on
  the model being reasoning-capable and the raw level being off/unset (an
  unsupported clamped level emits neither, since pipy does not clamp — the same
  documented divergence as the OpenRouter path). Covered by conformance item 18
  (18h). The remaining completions `thinkingFormat` variants (qwen,
  ant-ling, string-thinking), the `requiresReasoningContentOn
  AssistantMessages` DeepSeek message transform, and a full `detectCompat` port
  remain separate follow-ons.

- Together reasoning request shape has shipped: for the `openai-completions`
  `together` thinking format, a reasoning-capable model emits a top-level
  `reasoning: {enabled: true|false}` object — `enabled: true` when a level is
  active, `enabled: false` (a Pi-forced explicit disable, like the DeepSeek
  `type:"disabled"`, OpenRouter `effort:"none"`, and anthropic `type:"disabled"`
  off-states) when thinking is off/unset — and additionally sends top-level
  `reasoning_effort` on the on-state **only** when the model
  `supportsReasoningEffort`, matching Pi's `thinkingFormat === "together"` branch
  (`openai-completions.ts:586-594`). The format is resolved with Pi's `getCompat`
  precedence (explicit `compat.thinkingFormat` over the
  `together`/`api.together.ai`/`api.together.xyz` provider/base-URL detection,
  which Pi's `detectCompat` chain evaluates **before** `openrouter`), and
  `supportsReasoningEffort` is resolved **independently** of `thinkingFormat` via
  Pi's `detectCompat` exclusion list (which **includes** Together) — so an
  **auto-detected** Together model has `supportsReasoningEffort=false` and omits
  `reasoning_effort`, while an explicit `compat.supportsReasoningEffort=true` (or
  an explicit `thinkingFormat="together"` on a non-excluded provider) flips it on
  and adds `reasoning_effort`. The off-state is gated on the model being
  reasoning-capable and the raw level being off/unset (an unsupported clamped
  level emits neither, since pipy does not clamp — the same documented divergence
  as the DeepSeek/OpenRouter paths). Covered by conformance item 18 (18i). The
  remaining completions `thinkingFormat` variants (qwen, ant-ling,
  string-thinking) and a full `detectCompat` port remain separate follow-ons.

- Z.ai (`zai`) reasoning request shape has shipped: for the `openai-completions`
  `zai` thinking format, a reasoning-capable model emits a single top-level
  boolean `enable_thinking` — `true` when a level is active, `false` (a Pi-forced
  explicit disable, like the DeepSeek `type:"disabled"`, Together
  `enabled:false`, OpenRouter `effort:"none"`, and anthropic `type:"disabled"`
  off-states) when thinking is off/unset — and emits **no** `reasoning_effort` at
  all, matching Pi's `thinkingFormat === "zai"` branch
  (`openai-completions.ts:556-557`). The format is resolved with Pi's `getCompat`
  precedence (explicit `compat.thinkingFormat` over the `zai`/`api.z.ai`
  provider/base-URL detection, which Pi's `detectCompat` chain evaluates **before**
  `together` and `openrouter` — a `zai` row on a together/openrouter base URL
  resolves to the `zai` shape). Unlike DeepSeek/Together, the `zai` branch never
  consults `supportsReasoningEffort` (so an explicit
  `compat.supportsReasoningEffort=true` does not add `reasoning_effort`). The
  off-state is gated on the model being reasoning-capable and the raw level being
  off/unset (an unsupported clamped level emits neither, since pipy does not clamp
  — the same documented divergence as the DeepSeek/Together/OpenRouter paths).
  Covered by conformance item 18 (18j). The remaining completions
  `thinkingFormat` variants (qwen, ant-ling, string-thinking) and a full
  `detectCompat` port remain separate follow-ons.

Recently shipped closeout wiring:

- `pipy run` (non-REPL one-shot) provider construction now
  routes through catalog construction via `_run_provider_for_selection`
  (`NativeReplProviderState.current_provider`), the same boundary as the REPL, so
  `--api-key`, `--thinking`, custom `models.json` providers, base URLs, headers
  and routing all reach the one-shot turn. As a result `pipy run` for a custom
  `openai-completions` provider (openrouter, ds4) now uses the catalog
  completions adapter rather than the legacy per-provider adapter — matching the
  REPL. `openai-codex` and `fake` keep the legacy factory. Covered by conformance
  item 23.
- Startup CLI model resolution. The argparse `choices`
  constraint is removed from `--native-provider`, and launch-time
  `--native-provider`/`--native-model` resolve through the catalog via
  `resolve_cli_selection`/`default_selection_for(rows=...)`
  (`resolve_cli_model`): a custom `models.json` provider name is accepted, a bare
  `--native-model <ref>` resolves its provider (instead of `fake/<ref>`), a
  provider-only flag resolves the provider's default catalog model, and an
  unknown provider/model errors clearly — matching mid-session `/model`. The
  one-shot `pipy run` requires-an-explicit-model rule is preserved for built-in
  real providers. Covered by conformance item 24.

Current closeout sequence (all shipped 2026-06-03):

1. Wire non-completions API-family construction first. This extends the
   already-proven construction boundary while preserving adapter-specific
   request shapes. The gate captures deterministic fake requests per newly wired
   API family and asserts the selected model id, base URL, auth/header source,
   and provider-specific thinking/routing shape where applicable.
2. Move `pipy run` one-shot provider construction onto the same
   catalog-backed provider-state/construction boundary.
3. Route startup `--native-provider`/`--native-model` through
   `resolve_cli_model`, including custom `models.json` providers and bare
   provider/model references, and remove the argparse `choices` constraint that
   rejects custom providers at launch.

Other product follow-ons: live OAuth login orchestration for Anthropic/Copilot
(callback server / device-code prompting), adapter parity polish listed above,
and broader local-provider benchmarking. `/scoped-models` and `--models` Ctrl+P
live cycling ship through the settings/keybindings track.

This document defines the pipy target and partial shipped behavior for full
feature parity with Pi's provider/model catalog system. Before this track, pipy shipped
a small hardcoded static registry
(`pipy_harness.native.provider_registry.NATIVE_PROVIDER_REGISTRY`, ~13 provider
selections with one default model each). Pi has a broad built-in model catalog,
a `~/.pi/agent/models.json` custom provider/model override system with request
routing, subscription/OAuth auth (Anthropic, OpenAI Codex/ChatGPT, GitHub
Copilot), per-model thinking levels, `--list-models`, `--models` for Ctrl+P
scoped-model cycling, and a layered model-pattern matcher (`provider/id`,
`provider/id:level`, fuzzy substring, and glob).

Pipy should match Pi's user-facing capability through pipy-owned Python
boundaries. This is not a TypeScript port. It reuses the existing
`ProviderPort` / `ProviderRequest` / `ProviderResult` shapes and the
standard-library-first posture (urllib + stdlib `json`, mirroring the existing
adapters; no `httpx`, `boto3`, `anthropic`, or `google` SDK runtime
dependencies). The target is full Pi-equivalent capability, not a reduced
"metadata-first" subset of it. The only privacy constraint carried into this
track is the standard one every pipy surface already obeys: auth secrets and
tokens must never enter any session archive. That is not a divergence from Pi;
it is normal credential hygiene.

This is also a redesign direction for the hardcoded `ds4` provider. Pi has no
`ds4` built-in. ds4 is a local OpenAI-compatible server, and Pi already models
that exact case through `models.json` custom providers. The target reframes
`ds4` as a shipped `models.json` example/preset rather than a special-cased
built-in entry in the static registry.

## Sources

The reference behavior is taken from the local checkout at
`/Users/jochen/src/pi-mono`:

- `packages/coding-agent/src/core/model-registry.ts` — built-in + custom model
  load, `models.json` schema (TypeBox), provider/model overrides, deep merge,
  per-request auth/header resolution, dynamic `registerProvider`/`refresh`.
- `packages/coding-agent/src/core/model-resolver.ts` — `defaultModelPerProvider`,
  exact `provider/id` match, `provider/id:level` parsing, fuzzy substring +
  alias-vs-dated preference, glob scoping (`minimatch`), CLI `--provider`/
  `--model` resolution, initial-model priority, session restore fallback.
- `packages/coding-agent/src/core/auth-storage.ts` — `auth.json` credential
  store, runtime `--api-key` override, env-var fallback, OAuth token refresh
  with file locking, `AuthStatus` (the `models.json` `apiKey` is resolved by
  `ModelRegistry.getApiKeyAndHeaders` after the auth-store path, not via the
  auth-store fallback resolver under `includeFallback:false`).
- `packages/ai/src/env-api-keys.ts` — provider-specific env/ambient credential
  detection (AWS Bedrock profiles/IAM/bearer/ECS/IRSA, Vertex ADC, Azure,
  Cloudflare), beyond a single `<PROVIDER>_API_KEY` var.
- `packages/ai/src/oauth.ts` and `packages/ai/src/utils/oauth/`
  (`index.ts`, `anthropic.ts`, `github-copilot.ts`, `openai-codex.ts`,
  `device-code.ts`, `types.ts`) — built-in OAuth provider registry, Anthropic
  PKCE callback-server login, GitHub Copilot device-code login + per-model
  policy enable + `proxy-ep` base-URL extraction, OpenAI Codex (ChatGPT) PKCE.
- `packages/ai/src/providers/` — provider classes and auth/compat styles
  (`anthropic.ts`, `openai-responses.ts`, `openai-completions.ts`,
  `openai-codex-responses.ts`, `azure-openai-responses.ts`, `google.ts`,
  `google-vertex.ts`, `mistral.ts`, `amazon-bedrock.ts`, `cloudflare.ts`,
  `github-copilot-headers.ts`, `register-builtins.ts`).
- `packages/ai/src/models.ts`, `packages/ai/src/models.generated.ts` —
  `getProviders()`/`getModels()` and the generated built-in catalog (~900
  model rows across 30+ providers).
- `packages/ai/src/types.ts` — `Model<Api>` shape, `ThinkingLevel`,
  `ModelThinkingLevel`, `ThinkingLevelMap`, provider `compat` shapes.
- `packages/coding-agent/src/cli/args.ts` — `--provider`, `--model`,
  `--api-key`, `--thinking`, `--models`, `--list-models` parsing and help.
- `packages/coding-agent/src/cli/list-models.ts` — `--list-models` table.
- `packages/coding-agent/src/modes/interactive/interactive-mode.ts` —
  Ctrl+P scoped-model cycling and the interactive model selector.

Pipy-side mapping references:

- `src/pipy_harness/native/provider_registry.py` — legacy compatibility layer
  for provider-level facts still consumed while construction moves fully through
  the catalog.
- `src/pipy_harness/native/provider.py` — `ProviderPort`, streaming sinks.
- `src/pipy_harness/native/models.py` — `ProviderRequest`, `ProviderResult`,
  `ProviderToolCall`, `ProviderImageAttachment`.
- `src/pipy_harness/native/repl_state.py` — `NativeModelSelection`,
  `NativeModelOption`, `NativeReplProviderState`, `model_options()`,
  `select_model()`, `NativeDefaultsStore`.
- `src/pipy_harness/native/<provider>_provider.py` — the existing 13 adapters.
- `docs/harness-spec.md` (Native Runtime Bootstrap), `docs/pi-parity.md`
  (Provider/model catalog row), `docs/parity-criterion.md` (A-rows, ds4 note).

## Target Outcome

Pipy resolves provider/model selection through a pipy-owned model catalog that
is functionally equivalent to Pi's `ModelRegistry` + `model-resolver`:

- a broad built-in catalog of provider/model rows with real capability metadata
  (context window, max output, reasoning support, image input, per-model
  thinking-level map, default cost), loaded from a pipy-owned data table rather
  than the current one-default-per-provider registry;
- a `models.json` custom provider/model + override layer with request routing
  knobs, merged over the built-ins;
- a layered pattern matcher used identically by `--native-model`/`--provider`,
  the interactive `/model` selector, `--models` scoped cycling, and
  `--list-models`;
- per-model thinking levels parsed from `provider/id:level` and `--thinking`;
- subscription/OAuth auth for Anthropic (Claude Pro/Max), OpenAI Codex
  (ChatGPT), and GitHub Copilot, alongside API-key and env-var auth;
- `--list-models [search]`, an interactive `/model` selector, and `--models`
  Ctrl+P scoped cycling, all reading the same catalog;
- a refresh path so editing `models.json` or logging in/out updates the catalog
  without restarting the process.

`pipy run`, `pipy repl`, and the product-TUI `/model` selector all consume this
catalog through `NativeReplProviderState` and the
existing `provider_factory` boundary. Concrete adapters keep deciding how to
call their upstream API; the catalog only chooses *which* provider/model/auth
to construct and supplies the resolved request config.

The track may land in reviewed milestones, but the objective goal is full
Pi-equivalent catalog capability gated by the conformance script below.

## Built-In Catalog

Pi's built-in catalog (`models.generated.ts`, surfaced through
`getProviders()`/`getModels()`) is a large generated table: ~900 model rows
across 30+ providers, each row a `Model<Api>`:

```ts
interface Model<TApi> {
  id; name; api; provider; baseUrl;
  reasoning: boolean;
  thinkingLevelMap?: Partial<Record<"off"|"minimal"|"low"|"medium"|"high"|"xhigh", string|null>>;
  input: ("text"|"image")[];
  cost: { input; output; cacheRead; cacheWrite };
  contextWindow: number; maxTokens: number;
  headers?; compat?;
}
```

`defaultModelPerProvider` (model-resolver.ts) maps each known provider to its
default model id (e.g. `anthropic -> claude-opus-4-7`, `openai-codex ->
gpt-5.5`, `openrouter -> moonshotai/kimi-k2.6`).

Pipy target:

- Replace the one-default-per-provider `NATIVE_PROVIDER_REGISTRY` with a
  pipy-owned built-in catalog of `NativeModelSpec` rows. Each row mirrors the
  fields above as a frozen dataclass: `provider_name`, `model_id`,
  `display_name`, `api` (the adapter family: `openai-responses`,
  `openai-completions`, `anthropic-messages`, etc.), `base_url`, `reasoning`,
  `thinking_level_map`, `input` (`("text",)` / `("text","image")`),
  `cost` (`input`/`output`/`cache_read`/`cache_write`), `context_window`,
  `max_tokens`, optional `headers`, optional `compat`.
- Keep provider-level metadata (availability rule, `supports_tool_calls`,
  `auto_default`, `requires_model_for_run`, OAuth capability) on a
  `NativeProviderSpec` keyed by provider name; models reference their provider.
- Ship the catalog as a pipy-owned static data table (a generated
  `native/catalog_data.py` or a packaged JSON read at import time). It does not
  have to be byte-identical to Pi's generated table, but it must cover every
  provider pipy already implements (the A-rows in `parity-criterion.md`) and
  carry enough rows per provider that pattern matching, `--list-models`, and
  `/model` selection are useful (multiple aliases + dated versions per provider,
  not just one default).
- Keep a pipy-owned `default_model_per_provider` map (Pi's
  `defaultModelPerProvider` equivalent) used by initial-model selection and the
  per-provider fallback synthesis below.
- Each catalog row must map to exactly one existing `ProviderPort` adapter
  family. Adding catalog rows must not require a new adapter unless the row uses
  an API family pipy does not yet implement.

`DEFAULT_NATIVE_MODELS` and `SUPPORTED_NATIVE_PROVIDERS` are derived from the
catalog so existing callers in `cli.py`/`repl_state.py` keep working.

## `models.json` Custom Provider/Model Overrides And Routing

Pi loads `~/.pi/agent/models.json` (path = `join(getAgentDir(), "models.json")`)
in `ModelRegistry.loadCustomModels`. It strips `//` line comments and trailing
commas, validates against a TypeBox schema, and merges the result over the
built-in catalog. Custom models win on `provider+id` conflicts; provider-level
and per-model overrides are deep-merged onto built-ins.

Pipy target: load a pipy-owned `models.json` from the existing config root
resolution used by skills/templates/commands
(`PIPY_CONFIG_HOME` -> `${XDG_CONFIG_HOME}/pipy` -> `~/.config/pipy`), at
`<config>/models.json` (overridable for tests). Parse with stdlib `json` after a
comment/trailing-comma strip step that matches Pi's behavior and leaves string
literals untouched. Validate with a pipy-owned validator (dataclass + explicit
checks; no new dependency) that produces Pi-shaped, path-qualified error
messages, and surface load errors without discarding the built-in catalog (Pi
keeps built-ins when `models.json` fails).

### Schema

Top level is `{ "providers": { "<name>": ProviderConfig, ... } }`. Each
`ProviderConfig` (all fields optional unless noted):

- `name` — accepted by the schema but not used for provider display.
  `getProviderDisplayName()` resolves the label from dynamically registered
  providers, OAuth providers, then the built-in display-name map (model-
  registry.ts); a `models.json` `name` does not set it.
- `baseUrl` — provider base URL (override built-ins or define a custom server).
- `apiKey` — API key, an env-var name, or a `!command` value resolved at
  request time (see auth paths).
- `api` — adapter family for custom models (`openai-completions`,
  `openai-responses`, `anthropic-messages`, ...).
- `headers` — per-provider request headers (values may be env names/`!command`).
- `authHeader` — when true, send `Authorization: Bearer <apiKey>`.
- `compat` — provider-compat knobs (see below).
- `models` — array of custom `ModelDefinition` rows.
- `modelOverrides` — map of `modelId -> ModelOverride` applied to built-ins.

`ModelDefinition` (custom model): `id` (required), `name`, `api`, `baseUrl`,
`reasoning`, `thinkingLevelMap`, `input`, `cost`, `contextWindow`, `maxTokens`,
`headers`, `compat`. Defaults for local models: `input=["text"]`,
`reasoning=false`, `contextWindow=128000`, `maxTokens=16384`, zero cost.

`ModelOverride` (built-in override): every field optional and merged onto the
built-in row. `cost` is a partial merge (each sub-field falls back to the
built-in). `thinkingLevelMap` and `compat` are deep-merged.

`compat` is a union of three shapes mirroring Pi:

- OpenAI Completions compat: `supportsStore`, `supportsDeveloperRole`,
  `supportsReasoningEffort`, `supportsUsageInStreaming`, `maxTokensField`
  (`max_completion_tokens`|`max_tokens`), `requiresToolResultName`,
  `requiresAssistantAfterToolResult`, `requiresThinkingAsText`,
  `requiresReasoningContentOnAssistantMessages`, `thinkingFormat`
  (`openai`|`openrouter`|`together`|`deepseek`|`zai`|`qwen`|`qwen-chat-template`),
  `cacheControlFormat` (`anthropic`), `openRouterRouting`,
  `vercelGatewayRouting`, `supportsStrictMode`, `supportsLongCacheRetention`.
- OpenAI Responses compat: `sendSessionIdHeader`, `supportsLongCacheRetention`.
- Anthropic Messages compat: `supportsEagerToolInputStreaming`,
  `supportsLongCacheRetention`, `sendSessionAffinityHeaders`,
  `supportsCacheControlOnTools`, `forceAdaptiveThinking`.

### Routing

`openRouterRouting` and `vercelGatewayRouting` are request-routing preference
objects merged into `compat`:

- OpenRouter routing: `allow_fallbacks`, `require_parameters`,
  `data_collection` (`deny`|`allow`), `zdr`, `enforce_distillable_text`,
  `order`, `only`, `ignore`, `quantizations`, `sort` (string or `{by, partition}`),
  `max_price` (`{prompt, completion, image, audio, request}`),
  `preferred_min_throughput`, `preferred_max_latency` (number or percentile
  cutoffs `{p50,p75,p90,p99}`).
- Vercel AI Gateway routing: `only`, `order`.

Pipy target: store these as nested dataclasses on the resolved model's `compat`
and forward them to the relevant adapters in the request body using each
provider's own request shape (openai-completions.ts): OpenRouter routing is sent
as the top-level `provider` param (`params.provider = openRouterRouting`, gated
on an `openrouter.ai` base URL), while Vercel AI Gateway routing is sent as
`params.providerOptions = { gateway: { only, order } }` (gated on an
`ai-gateway.vercel.sh` base URL) — it is not a `provider` block. The current
OpenRouter adapter does not send routing; this track wires it so that a
`models.json` routing block reaches the request. Routing is provider-config and
is never archived.

### Merge And Validation Rules

Match Pi's `loadModels`/`mergeCustomModels`/`validateConfig`:

1. Load built-ins, then apply provider-level overrides (`baseUrl`, `compat`)
   and per-model overrides (deep merge) to built-in rows.
2. Merge custom `models` by `provider+id` (custom replaces built-in on
   conflict, otherwise appends).
3. Let OAuth providers mutate their rows after merge (e.g. GitHub Copilot
   rewrites `baseUrl` from the token's `proxy-ep`; see auth paths).
4. Validation: an override-only provider config (no `models`) must specify at
   least one of `baseUrl`, `headers`, `compat`, or `modelOverrides`. A
   non-built-in provider that defines custom `models` requires `baseUrl` and
   `apiKey`. Built-in providers may define custom models without `baseUrl`/
   `apiKey`/`api` (inherited from built-in defaults). Each custom model needs an
   `api` resolvable at model/provider/built-in level; `contextWindow`/`maxTokens`
   must be positive when present.

### ds4 As A `models.json` Example

`ds4` should stop being a special-cased built-in registry entry. Pi has no
`ds4`. ds4 is a local OpenAI-compatible Chat Completions server, exactly the
case `models.json` custom providers exist for. Reframe it as:

- a documented `models.json` example a user can paste, e.g. a `ds4` provider
  with `api: "openai-completions"`,
  `baseUrl: "http://127.0.0.1:8000/v1"` (overridable),
  and a `deepseek-v4-flash` model with `reasoning`/`input`/`contextWindow` set
  for the q2-imatrix target, plus `supports_tool_calls` semantics via the
  catalog/provider-spec layer. Because `validateConfig()` requires both
  `baseUrl` and `apiKey` for a non-built-in provider that defines custom
  `models` (model-registry.ts), the ds4 preset must include `apiKey` even though
  the local server is keyless — use a placeholder/dummy value (e.g.
  `"apiKey": "local"`); the local server ignores it;
- optionally a shipped preset file pipy can install on request, so the daily
  workflow is unchanged while the registry stops carrying a Pi-divergent
  built-in.

The `PIPY_DS4_BASE_URL`/`PIPY_DS4_API_KEY` env path may stay as a convenience
shim that synthesizes the same `models.json`-style entry, but the canonical
model is "ds4 is a custom provider", not "ds4 is built in".

## Model-Pattern Matching

Pi's matcher (`model-resolver.ts`) is layered. Pipy must reproduce all layers
through pipy-owned helpers operating over catalog rows.

1. Exact reference match (`findExactModelReferenceMatch`): case-insensitive
   match on `provider/id`; if exactly one, return it; if ambiguous, reject. Then
   split on first `/` into `provider` + `id` and match both; then bare-`id`
   match (ambiguous bare ids across providers are rejected).
2. `provider/id:thinking-level` parsing (`parseModelPattern`): try the full
   pattern as a model first via `tryMatchModel` — note this already includes the
   fuzzy substring step below, not just an exact lookup, so ids containing colons
   such as OpenRouter `model:exacto` match before any colon split. On no match,
   split on the *last* colon: if the suffix is a valid thinking level
   (`off`/`minimal`/`low`/`medium`/`high`/`xhigh`), recurse on the prefix and
   attach the level; if the suffix is invalid, in scope mode warn and use the
   default level, in strict CLI mode (`allowInvalidThinkingLevelFallback:false`)
   `parseModelPattern` returns no model. Strict mode is *not* a guaranteed
   failure for the overall CLI resolution, though: when a provider is known,
   `resolveCliModel` then synthesizes a per-provider fallback model whose id
   includes the unmatched `:suffix` via `buildFallbackModel` (model-resolver.ts),
   with a warning.
3. Fuzzy substring (the second half of `tryMatchModel`): when the exact
   reference match misses, filter rows whose `id` or `name` contains the pattern
   (case-insensitive). This fuzzy step runs as part of the "try the full pattern
   as a model" call in step 2, before any colon split. Prefer aliases (ids
   without a `-YYYYMMDD` date suffix and not `-latest`... actually `-latest`
   counts as an alias) over dated versions; among aliases pick the highest by
   reverse `localeCompare`; otherwise pick the latest dated version.
4. Glob scoping (`resolveModelScope` for `--models`): when a pattern contains
   `*`, `?`, or `[`, optionally strip a trailing `:level`, then glob-match
   against both `provider/id` and bare `id` (case-insensitive, fnmatch-style),
   collecting all unique matches. Non-glob patterns go through the
   exact/level/fuzzy path. Unmatched patterns warn and are skipped.
5. CLI resolution (`resolveCliModel`): build a case-insensitive provider map
   from *all* catalog rows (not just authed ones, so `--api-key` first-time
   setup works); honor `--provider` + `--model`; infer provider from a
   `provider/...` prefix when `--provider` is absent; prefer the provider
   interpretation over an id that literally contains a slash; on no match within
   an inferred provider, fall back to a full-string id match across all rows;
   and synthesize a per-provider fallback model (clone the provider default,
   replace `id`/`name`) when a provider is known but the specific id is not
   found, with a warning. Strict mode (`allowInvalidThinkingLevelFallback:
   false`) is used for CLI `--model` so an invalid `:suffix` does not silently
   resolve a neighbor model inside `parseModelPattern`; instead, when a provider
   is known, the unmatched id (suffix included) becomes a synthesized fallback
   custom model. CLI resolution only errors when no provider is known and no
   match is found.

Pipy target: implement helpers (`find_exact_model_reference`,
`parse_model_pattern`, `resolve_model_scope`, `resolve_cli_model`) returning a
`ScopedModel`/`ParsedModelResult` analogue (`model`, `thinking_level`,
`warning`, `error`). Use `fnmatch` (stdlib) for globs (`minimatch` analogue,
case-insensitive). These helpers are the single matching surface for
`--native-model`/`--native-provider`, `/model <ref>`, the `/model` selector,
`--models` cycling, and `--list-models` filtering.

## Thinking Levels

The thinking-level vocabulary is split across packages. In
`packages/ai/src/types.ts`, `ThinkingLevel = minimal|low|medium|high|xhigh`
(no `off`), and `ModelThinkingLevel = "off" | ThinkingLevel` adds `off`. The
coding-agent / agent-core CLI surface (`args.ts`) uses the six-value set
*including* `off`: `off|minimal|low|medium|high|xhigh`. Each model carries an
optional `thinkingLevelMap` mapping a level to the provider-specific reasoning
value (or `null` to disable). The actually-supported levels for a given model
are computed/clamped per model from that map (packages/ai/src/models.ts); in
particular `xhigh` is only available when the model maps it. `--thinking
<level>` sets the session default; `provider/id:level` sets a per-selection
level; `DEFAULT_THINKING_LEVEL` applies otherwise. `args.ts` validates the
six-value set and warns on invalid input. Pipy already has a `reasoning_sink`
and renders italic reasoning text; the native session already records a
`thinking_level_change` entry (see `docs/session-tree.md`).

Pipy target:

- Add a `--thinking` flag to `pipy run`/`pipy repl` validated against the same
  six values, warning (not failing) on invalid input.
- Carry the active thinking level on the model selection / request so adapters
  that support reasoning map it through the model's `thinking_level_map` to the
  upstream reasoning parameter (`reasoning_effort`, Anthropic adaptive thinking
  budget, etc.). Clamp the level to what the model actually maps (mirroring
  packages/ai/src/models.ts); `off`/unsupported models ignore it, and `xhigh` is
  only honored when the model maps it.
- Parse `:level` in `/model` and `--native-model` via the matcher above.
- Persist the selected level in `NativeDefaultsStore` and the native session
  `thinking_level_change` entry (already specified) so resume restores it.
- A thinking hotkey/cycle in the TUI is a follow-on UI nicety, not a catalog
  prerequisite.

## Subscription / OAuth Auth Paths

Pi resolves per-request auth in `ModelRegistry.getApiKeyAndHeaders` (model-
registry.ts). It first calls `authStorage.getApiKey(provider, {includeFallback:
false})`, which inside `AuthStorage.getApiKey` (auth-storage.ts) tries the
runtime `--api-key` override, then the `auth.json` api_key, then the `auth.json`
OAuth token (auto-refreshed on expiry under a file lock), then the env var —
with `includeFallback:false` it does not consult the auth-storage fallback
resolver. Only if that returns nothing does the registry fall back to the
provider's `models.json` `apiKey` from `providerRequestConfigs`. In other words
the `models.json` key is resolved by the registry after the auth-storage path,
not via the `AuthStorage` fallback resolver. `models.json` `apiKey` values may
be literal keys, env-var names, or `!command` values (resolved at request time,
never archived). The built-in OAuth registry
(`oauth.ts`) holds three providers, each implementing
`login`/`refreshToken`/`getApiKey`/optional `modifyModels`:

- Anthropic (Claude Pro/Max): PKCE authorization-code flow with a local
  callback server (binds `127.0.0.1`, port `53692`) and redirect URI
  `http://localhost:53692/callback`, manual-paste fallback for the redirect URL,
  token exchange at `platform.claude.com/v1/oauth/token`, refresh support,
  expiry stored with a 5-minute safety margin (anthropic.ts).
- GitHub Copilot: device-code flow against `github.com` (or an enterprise
  domain), then a Copilot token exchange; `modifyModels` rewrites the Copilot
  rows' `baseUrl` from the token's `proxy-ep=...` claim; login enables each
  Copilot model via the `/models/<id>/policy` endpoint; Copilot editor headers
  are required.
- OpenAI Codex (ChatGPT): PKCE flow (already implemented in pipy as a separate
  provider with its own auth state). Unlike Anthropic/Copilot, Codex stores
  expiry as `Date.now() + expires_in*1000` with no 5-minute safety margin
  (openai-codex.ts); do not assume a margin or a locked-refresh-with-margin path
  for Codex.

`getProviderAuthStatus`/`getAuthStatus` report whether/how a provider is
configured (`stored`, `runtime`, `environment`, `fallback`, `models_json_key`,
`models_json_command`) without executing `!command` values or refreshing
tokens. `isUsingOAuth` flags subscription-backed models.

Pipy target:

- Keep the existing OpenAI Codex OAuth provider and its
  `${PIPY_AUTH_DIR:-~/.local/state/pipy/auth}/openai-codex.json` state.
- Add a pipy-owned auth store and a small OAuth provider registry with the same
  three built-ins, all stdlib-only (urllib + `http.server` for the local
  callback server + stdlib `json`/`base64`/`hashlib` for PKCE/JWT-claim
  extraction), modeled on the Pi flows above. Anthropic uses a local callback
  server with manual-paste fallback; GitHub Copilot uses device-code +
  per-model policy enable + `proxy-ep` base-URL rewrite.
- Resolve per-request auth/headers with Pi's priority order (the auth-store
  path first, then the registry-level `models.json` key): runtime `--api-key`
  -> stored api_key -> stored OAuth (refresh on expiry, owner-only file, lock to
  avoid concurrent-refresh races) -> env var, and only if all of those miss, the
  provider's `models.json` `apiKey` (literal/env-name/`!command`) resolved by the
  catalog layer rather than the auth store. Support `authHeader` (send
  `Authorization: Bearer`) and merged per-provider/per-model headers.
- Add a pipy `AuthStatus` analogue with the same source labels and the same
  rule that status checks never execute `!command` values or refresh tokens.
- Expose login/logout for all three OAuth providers through the existing
  `pipy auth <provider> login/logout` command and the in-shell `/login`/
  `/logout` boundary (which already exist for openai-codex), with interactive
  output rendered only on the live terminal and never archived.
- `getAvailable()` analogue: a model is available when its provider has
  configured auth (stored/env/runtime/`models.json` key) — the existing
  availability gate, extended to consult the auth store and `models.json` keys,
  not just env vars. The `/model` selector and `--list-models` use this.
- Provider-specific ambient/env credentials matter beyond simple API-key env
  vars (env-api-keys.ts). The availability gate must honor these
  provider-specific sources, not just a single `<PROVIDER>_API_KEY`:
  - Amazon Bedrock: `AWS_PROFILE`, `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`,
    `AWS_BEARER_TOKEN_BEDROCK`, ECS task-role URIs
    (`AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`/`_FULL_URI`), and IRSA
    (`AWS_WEB_IDENTITY_TOKEN_FILE`).
  - Google Vertex: an explicit API key (`GOOGLE_CLOUD_API_KEY`) *or*
    Application Default Credentials. The ADC path counts as available only when
    ADC credentials exist (`GOOGLE_APPLICATION_CREDENTIALS`, else the default
    ADC path) *and* a project (`GOOGLE_CLOUD_PROJECT` or `GCLOUD_PROJECT`) *and*
    a location (`GOOGLE_CLOUD_LOCATION`) are set — all three are required.
  - Azure OpenAI: availability is determined only by `AZURE_OPENAI_API_KEY`. The
    endpoint/resource configuration is resolved later by the provider, not by
    the auth-availability check.
  - Cloudflare: availability is determined only by `CLOUDFLARE_API_KEY`. The
    account/gateway ids are substituted later (in `resolveCloudflareBaseUrl`),
    not checked by auth availability.
  pipy's existing `availability` rules already encode several of these
  (`env-all`/`env-google-vertex`); this track keeps and extends them so the
  ambient-credential providers report availability correctly rather than
  appearing unconfigured.

The `pipy-session` archive surfaces (JSONL, Markdown, catalog/search/inspect)
are metadata-only: prompts, model output, and tool payloads never reach them.
The headless automation surfaces emit real conversation content, so
metadata-first redaction does not apply to them: `--mode json` and `--mode rpc`
are full-event transports that deliberately emit prompts, tool arguments, tool
results, and model output, while `--print`/`-p` prints the one-shot final
assistant text only (see `docs/automation-rpc.md`).

What is withheld from *every* surface — the archive, the full-event transports,
and the one-shot print — is credential material: secrets, tokens, refresh
tokens, authorization URLs, PKCE verifiers, and `Authorization` headers. This is
the standard credential-hygiene rule, not a capability reduction.

## `--list-models` And `/model` Selector Data

Pi's `--list-models [search]` prints a sorted table of *available* models
(provider, model, context, max-out, thinking yes/no, images yes/no), optionally
fuzzy-filtered by the search term over `provider id`, with a leading warning if
`models.json` failed to load and a guidance message when no models are
available. The interactive selector and Ctrl+P cycling read the same
`getAvailable()` rows.

Pipy target:

- Add `--list-models [search]` to the CLI: print the same column set sorted by
  provider then id, fuzzy-filtered by the optional search term over
  `provider id`, with the same `models.json`-error warning and
  no-models-available guidance. Output goes to stdout and exits; it runs no
  provider turn.
- The product-TUI `/model` selector
  (`ToolLoopTerminalUi.run_model_selector` over `model_options()`) reads the
  full catalog with availability state and reasons. The existing selector
  already gates unavailable/non-tool-capable providers; this track widens
  `model_options()` from one-default-per-provider to the full catalog, with the
  same availability gate, the same context-clear/rebind on a successful choice,
  and `:level` support in `/model <ref>`.
- `NativeModelOption` carries enough capability metadata (context window,
  reasoning, image input) to render Pi-equivalent rows.

## `--models` Scoped-Model Cycling

Pi's `--models <patterns>` takes a comma-separated list of patterns (globs and
fuzzy, each with optional `:level`), resolved via `resolveModelScope` into the
session's `scopedModels`. Ctrl+P cycles forward/backward through the scoped set
(or, with no scope, all available models); each scoped entry may pin a thinking
level. `findInitialModel` prefers the first scoped model when not
continuing/resuming.

Pipy target:

- Add `--models <patterns>` to `pipy run`/`pipy repl`, split on commas and
  trimmed, resolved through `resolve_model_scope` into a session-scoped ordered
  list of `(selection, thinking_level)`.
- Add forward/backward cycling (Ctrl+P or a documented keybinding) in the
  product TUI over the scoped set, falling back to the full available set when
  no scope is set, with a "only one model" notice when the set has one entry.
  Cycling rebinds the live provider/model (and pinned level) exactly like a
  `/model` switch: availability gate, context clear on success, footer/status
  refresh, no provider turn during selection.
- Initial-model selection honors the first scoped model (when not
  continuing/resuming) ahead of saved defaults, matching `findInitialModel`.

## Model-List Update / Refresh Machinery

Pi's `ModelRegistry.refresh()` clears request configs, resets dynamic
API/OAuth registrations, reloads `models.json` + built-ins, and reapplies
dynamically registered providers; `registerProvider`/`unregisterProvider`
support extension-registered providers and trigger a refresh.

Pipy target:

- A `refresh()` on the pipy catalog that reloads `models.json` + built-ins and
  reapplies OAuth-driven model mutations (e.g. Copilot base-URL rewrite after a
  fresh login). Triggered after `/login`/`/logout` and after a documented
  `models.json` reload command, so a mid-session credential or config change
  updates `/model`, `--list-models`-equivalent surfaces, and availability
  without restarting.
- Dynamic extension-provider registration now has the first product wiring:
  Python `ExtensionProvider` rows are applied over the active catalog for the
  current run, construct through the registered factory, and are recomputed on
  `/reload`. OAuth-provider registration and richer override-only extension
  controls remain future extension/API work.

## Invariants

- Pipy-owned Python boundaries. This is a capability match, not a TypeScript
  port. Reuse `ProviderPort`, `ProviderRequest`, `ProviderResult`,
  `ProviderToolCall`, and `ProviderImageAttachment`; do not invent a parallel
  request shape.
- Stdlib only, no new runtime dependencies. Use `urllib` + stdlib `json`
  (mirroring the existing adapters), `fnmatch` for globs, `http.server` for the
  OAuth callback server, and `hashlib`/`hmac`/`base64` for PKCE/SigV4/JWT-claim
  work. No `httpx`, `boto3`, `anthropic`, `google` SDK, `minimatch`, `typebox`,
  or `proper-lockfile`.
- Full Pi-equivalent capability is the goal. Do not carry a reduced
  "metadata-first" subset as the target. Every catalog/auth/selection feature Pi
  exposes is in scope.
- The only privacy constraint is credential hygiene: api keys, OAuth
  access/refresh tokens, PKCE verifiers, authorization URLs, `!command` values,
  and `Authorization` headers must never enter any session archive surface.
  This is standard, not a divergence; model ids, provider names, thinking
  levels, availability state, and capability metadata are safe and may be shown
  and recorded.
- ds4 is a `models.json` custom provider, not a special-cased built-in. The
  static registry must not carry a Pi-divergent `ds4` built-in row.
- `models.json` load failures degrade gracefully: keep the built-in catalog,
  surface a path-qualified error, never crash startup.
- Adding catalog rows must not require a new adapter unless the row uses an API
  family pipy does not implement; catalog rows map onto existing adapters.
- Concrete adapters keep deciding how to call their upstream API; the catalog
  only resolves which provider/model/auth/routing to use.
- Availability and matching helpers are deterministic and side-effect-free
  (no provider turn, no tool call, no network) except for explicit OAuth
  login/refresh, which is gated behind `/login`/`auth`.

## Implementation Milestones

The track may land in reviewed slices, but the objective goal is the full
Pi-equivalent catalog. Work is complete only when the conformance gate proves
both the catalog/helper layers and the real product provider-construction /
request paths.

1. Built-in catalog data model: replace one-default-per-provider with
   `NativeModelSpec` rows + `NativeProviderSpec` provider metadata + a
   pipy-owned `default_model_per_provider`; derive `DEFAULT_NATIVE_MODELS`/
   `SUPPORTED_NATIVE_PROVIDERS`; cover every implemented provider with multiple
   rows; keep existing callers working.
2. Pattern matcher: `find_exact_model_reference`, `parse_model_pattern`
   (incl. `provider/id:level`, colon-in-id handling, strict vs scope mode),
   fuzzy alias-vs-dated preference, `resolve_model_scope` globs (`fnmatch`),
   `resolve_cli_model` (provider inference, per-provider fallback synthesis).
   Route mid-session `/model <ref>` through it. Startup
   `--native-model`/`--native-provider` routing is still a closeout slice.
3. `models.json` loader: config-root resolution, comment/trailing-comma strip,
   stdlib parse, pipy-owned schema validation with path-qualified errors,
   provider/model override deep-merge, custom-model merge by `provider+id`,
   graceful degradation, `refresh()`.
4. Routing + compat: nested routing/compat dataclasses, merged onto resolved
   models, forwarded to OpenRouter/gateway/OpenAI-completions/Anthropic adapters
   as request config.
5. Thinking levels: `--thinking` flag, `:level` parsing wired to the matcher,
   per-model `thinking_level_map` mapping in adapters, persistence in defaults +
   `thinking_level_change` session entry.
6. Auth resolution: pipy auth store + per-request auth/header resolution with
   Pi's priority order, `authHeader`, `models.json` key/env/`!command` fallback,
   `AuthStatus` with source labels, availability gate extended to consult it.
7. OAuth subscription providers: stdlib OAuth registry with Anthropic
   (callback-server PKCE + manual paste), GitHub Copilot (device-code +
   per-model policy enable + `proxy-ep` base-URL rewrite), and the existing
   OpenAI Codex flow; `/login`/`/logout`/`pipy auth` for all three; refresh on
   expiry under a lock.
8. `--list-models [search]`: sorted, fuzzy-filtered table with capability
   columns, `models.json`-error warning, no-models guidance.
9. `/model` selector over the full catalog with availability/reasons,
   `:level` support, context-clear/rebind, no provider turn.
10. `--models` scoped cycling: scope resolution, Ctrl+P forward/backward,
    pinned levels, initial-model preference for the first scoped model.
    **Shipped through the settings/keybindings track.**
11. ds4 reframe: remove the special-cased built-in `ds4` registry row, ship a
    documented `models.json` ds4 example/preset, keep the env-var convenience
    shim as a synthesized custom-provider entry.
12. Refresh + dynamic registration wiring: catalog `refresh()` after
    login/logout and a `models.json` reload command so mid-session changes take
    effect, plus a `register_provider`/`unregister_provider` boundary on the
    catalog mirroring Pi's `registerProvider`/`unregisterProvider` (full
    replacement, override-only, OAuth registration). The extension-facing API
    that drives this boundary is specified in `docs/extension-api.md`; the
    underlying registry capability ships here.
    **Shipped for Python extension provider rows; OAuth-provider registration
    and richer extension override controls remain follow-ons.**
13. Product provider-construction wiring for the OpenAI-compatible Chat
    Completions family: construct real provider adapters from the selected
    `NativeModelSpec` and resolved request config, including custom
    `models.json` providers, `baseUrl`, headers, `authHeader`, routing,
    runtime/stored/env auth, mapped thinking levels, and direct `/model <ref>`
    through the shared resolver. **Shipped 2026-06-03** and covered by
    conformance item 18 with fake HTTP.
14. Product provider-construction wiring for non-completions API families,
    extending the same boundary with per-family request-shape assertions for
    auth, headers, base URL/model id, and mapped thinking where supported.
    - Tier 1 (`anthropic-messages`, `openai-responses`, `mistral`): pure
      api-key + endpoint adapters. **Shipped 2026-06-03**, conformance item 20.
    - Tier 2 (`google-generative-ai`, `azure-openai-responses`,
      `cloudflare-workers-ai`): template/composed endpoints + multi-part auth
      (`{ENV}` base-URL substitution). **Shipped 2026-06-03**, conformance
      item 21.
    - Tier 3 (`amazon-bedrock`, `google-vertex`): IAM/SigV4/OAuth auth that does
      not fit the api-key shape — auth + region/project endpoint stay
      env-resolved; construction injects model id + headers + thinking (bedrock
      Anthropic budget; vertex thinking deferred). `openai-codex-responses` is
      kept on the legacy factory for its settings-derived `RetryPolicy`.
      **Shipped 2026-06-03**, conformance item 22.
15. One-shot construction: make `pipy run` use the catalog-backed construction
    boundary instead of `_adapter_for`, preserving existing text/stream/json
    output contracts while honoring custom providers, runtime auth, base URLs,
    headers, routing, and thinking. **Shipped 2026-06-03** via
    `_run_provider_for_selection` (`current_provider`), conformance item 23.
16. Startup CLI resolution: route launch-time `--native-provider` and
    `--native-model` through `resolve_cli_model`, including custom
    `models.json` providers and bare provider/model refs; remove argparse
    `choices` that reject custom providers; keep the legacy
    `--native-provider ds4` shim only as a compatibility bridge.
    **Shipped 2026-06-03** via `resolve_cli_selection` /
    `default_selection_for(rows=...)`, conformance item 24.

## Verification Plan

Add one top-level deterministic conformance gate and make it the implementation
source of truth:

```sh
uv run python scripts/parity_checks/provider_catalog_conformance.py --json
```

The conformance script drives the catalog and product provider-construction
paths with deterministic fixtures (a temp config root, a temp `models.json`, a
fake auth store, fake HTTP transports, no network) and fails unless the covered
capability set works. The current landed gate covers items 1-25, including the
Chat-Completions product path, non-completions product paths, one-shot
construction, startup resolution, extension-provider catalog wiring, and
archive secret checks. It must verify:

1. the built-in catalog loads with multiple rows per implemented provider and
   real capability metadata (context window, max out, reasoning, image input,
   thinking-level map);
2. exact `provider/id` matching, ambiguity rejection, and bare-id matching;
3. `provider/id:level` parsing including colon-in-id models (matched via the
   fuzzy `tryMatchModel` step before any colon split), plus the strict-CLI vs
   scope-mode behavior on an invalid `:suffix`: scope mode warns and uses the
   default level; strict CLI mode does not resolve a neighbor inside
   `parseModelPattern`, but `resolveCliModel` synthesizes a per-provider fallback
   custom model (suffix included) when a provider is known, and only errors when
   no provider is known and nothing matches;
4. fuzzy substring matching with alias-over-dated preference;
5. glob scoping over `provider/id` and bare `id` with optional `:level`;
6. `resolve_cli_model` provider inference, slash-prefix handling, and
   per-provider fallback synthesis with the expected warning;
7. a `models.json` with a custom provider + custom models + provider-level
   override + per-model override merges correctly over built-ins (custom wins
   on `provider+id`; cost/compat/thinking deep-merge);
8. comment/trailing-comma stripping and graceful degradation: a malformed
   `models.json` yields a path-qualified error and keeps the built-in catalog;
9. validation rejects an override-only provider with no usable fields and a
   non-built-in provider with custom models but no `baseUrl`/`apiKey`;
10. routing/compat blocks survive the merge and reach the resolved model's
    request config;
11. auth resolution priority (runtime `--api-key` -> stored key -> stored OAuth
    -> env -> `models.json` key/env-name/`!command`), `authHeader` injection,
    and merged provider/model headers;
12. `AuthStatus` source labels are correct and status checks execute no
    `!command` and refresh no token;
13. OAuth provider shape: Anthropic/Copilot/Codex login/refresh/getApiKey/
    modifyModels behave against fake HTTP fixtures; Copilot `proxy-ep` base-URL
    rewrite and per-model enable are exercised; expiry refresh works under the
    lock;
14. `--thinking`/`:level` set the active level, persist, and map through the
    model's thinking-level map for a reasoning-capable adapter (and are ignored
    by `off`/unsupported models);
15. the availability gate (`getAvailable` analogue) reflects auth store +
    `models.json` keys, not just env vars;
16. ds4 resolves as a `models.json` custom provider with no special-cased
    built-in row, and the env-var shim produces an equivalent entry;
17. catalog `refresh()` picks up a `models.json` edit and a simulated
    login/logout without a process restart;
18. Chat-Completions-family product provider-construction paths use the selected
    `NativeModelSpec` and resolved request config: custom `models.json`
    providers can complete a fake provider turn, built-in custom rows preserve
    `baseUrl`/headers/routing, runtime `--api-key` reaches the provider auth
    boundary, `--thinking` reaches a reasoning-capable adapter, and direct
    `/model <ref>` goes through the shared resolver;
19. no secret/token/`Authorization`/PKCE-verifier/authorization-URL value
    appears in any archive surface produced during the run.
20. Tier 1 non-completions product construction uses the selected
    `NativeModelSpec` and resolved request config for the direct api-key +
    endpoint adapter families (`anthropic-messages`, `openai-responses`, and
    `mistral`), with fake HTTP captures proving model id, base URL,
    auth/header source, routing/compat where supported, and thinking shape where
    supported;
21. Tier 2 non-completions product construction uses the selected
    `NativeModelSpec` and resolved request config for the derived-url api-key +
    account adapter families (`google-generative-ai`, `azure-openai-responses`,
    and `cloudflare-workers-ai`), including fail-closed behavior for missing
    required endpoint/account config;
22. Tier 3 non-completions product construction uses the selected
    `NativeModelSpec` and resolved request config for the environment/SDK-shape
    adapter families (`amazon-bedrock` and `google-vertex`), including Bedrock
    thinking-body behavior; `openai-codex-responses` is explicitly covered as a
    deliberate legacy-factory exception for its settings-derived `RetryPolicy`;
23. `pipy run` one-shot product construction uses the same catalog-backed
    boundary as REPL product turns, including custom providers and runtime auth,
    without changing stdout/stderr output conventions;
24. startup `--native-provider`/`--native-model` resolve through the shared
    matcher, accepting custom provider names and bare refs while preserving
    documented fallback/error behavior.

Focused tests should also cover:

- `--list-models` and `--list-models <search>` output (sorted columns, fuzzy
  filter, `models.json`-error warning, no-models guidance) through the real CLI;
- `--models` scope resolution + forward/backward cycling rebinding the live
  provider/model and pinned level with no provider turn (product TUI);
- the `/model` selector over the full catalog with availability reasons,
  `:level` support, and context-clear/rebind, including a real-PTY product-path
  flow;
- initial-model priority order (`findInitialModel` analogue): CLI args, first
  scoped model when not resuming, saved default, first available default,
  first available;
- session resume restoring a `models.json` custom model and a saved thinking
  level, with the documented fallback when a restored model no longer exists or
  loses auth (`restoreModelFromSession` analogue).

An optional Pi comparison smoke (e.g.
`scripts/tmux_provider_catalog_compare.sh <out-dir>`) may compare user-visible
behavior — `--list-models` columns, `/model` selector contents, `--models`
cycling, and `provider/id:level` resolution — between pi and pipy. Exact Pi JSON
/ table byte-matching is not the hard gate; deterministic pipy conformance is.

Before treating implementation as complete, run:

```sh
uv run python scripts/parity_checks/provider_catalog_conformance.py --json
uv run pytest tests/test_native_provider_catalog*.py
uv run pytest tests/test_native_model_resolver*.py
uv run pytest tests/test_native_models_json*.py
uv run pytest tests/test_native_oauth*.py
uv run pytest tests/test_native_provider_construction*.py
uv run pytest tests/test_harness_native_cli.py -k "list_models or models or thinking"
uv run pytest tests/test_native_tool_loop_tui_pty.py -k "model"
just check
```

Update `docs/harness-spec.md` (Native Runtime Bootstrap / provider registry),
`docs/pi-parity.md` (Provider/model catalog row), `docs/parity-criterion.md`
(A-rows and the ds4 note), `README.md`, and this spec to match shipped behavior,
and get an independent review pass for the auth/OAuth and `models.json` loader
slices.

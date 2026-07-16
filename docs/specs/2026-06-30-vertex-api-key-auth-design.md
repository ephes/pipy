# Vertex API-key auth (express mode) — design

Gap source: `docs/pi-mono-gap-audit.md` §5 "Provider/model catalog follow-ons"
("Vertex API-key auth") and `docs/provider-catalog.md` "Remaining adapter/product
follow-ons" first bullet. Reference: `~/src/pi-mono`.

## Scope (one slice)

pipy's `google-vertex` adapter is ADC/OAuth-bearer-token-only today: it requires a
pre-obtained `GOOGLE_ACCESS_TOKEN` plus project + location and sends
`Authorization: Bearer <token>` to the regional endpoint. Pi *also* supports a
Vertex **API key** (`GOOGLE_CLOUD_API_KEY`) — "Vertex Express" mode — which uses a
different host, no project/location path, and a different auth header. This slice
adds that API-key path to pipy's vertex adapter and wires the catalog to forward
the resolved key, while leaving the existing ADC bearer path unchanged.

This is an auth + provider-request-shape slice, so the field/optionality/default
pins below are the contract the review must check.

## Pinned Pi reference behavior

Pi's `google-vertex.ts` chooses the client by api key:

```
const apiKey = resolveApiKey(options);
const client = apiKey
  ? createClientWithApiKey(model, apiKey, headers)   // express mode
  : createClient(model, project, location, headers); // ADC mode
```

- `resolveApiKey(options)` (`google-vertex.ts:397-407`): returns
  `options.apiKey.trim()` **unless** it is empty, equals the sentinel
  `GCP_VERTEX_CREDENTIALS_MARKER = "gcp-vertex-credentials"`, or matches the
  placeholder pattern `^<[^>]+>$` — in those cases it returns `undefined` (→ ADC).
  `<authenticated>` (pipy's ambient sentinel, `auth_store._AMBIENT_AUTHENTICATED`)
  matches the placeholder pattern, so it correctly falls through to ADC.
- `options.apiKey` originates from Pi's standard auth precedence
  (`env-api-keys.ts:108`: `"google-vertex": "GOOGLE_CLOUD_API_KEY"`), i.e.
  runtime `--api-key` → stored key → `GOOGLE_CLOUD_API_KEY`.
- `createClientWithApiKey` → `new GoogleGenAI({ vertexai: true, apiKey,
  apiVersion: "v1", httpOptions })`. The `@google/genai` SDK (vendored in
  `~/src/pi-mono/node_modules/@google/genai/dist/node/index.cjs`) resolves this
  to the concrete HTTP request as follows:
  - **Host/baseUrl** (`index.cjs:12978-12982`): when `apiKey` is set,
    `baseUrl = "https://aiplatform.googleapis.com/"` — the **global** host, **no**
    `{location}-aiplatform` prefix.
  - **apiVersion** = `"v1"` (Pi passes it explicitly).
  - **Resource path** (`index.cjs:2983`, `15089`): base models →
    `publishers/google/models/{model}:generateContent`.
  - **No project/location prefix** (`shouldPrependVertexProjectPath`,
    `index.cjs:13104-13105`): returns `false` whenever `apiKey` is set, so
    `projects/{project}/locations/{location}` is omitted.
  - **Full URL** =
    `https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent`.
  - **Auth header** (`NodeAuth.addKeyHeader`, `index.cjs:19459-19489`):
    `x-goog-api-key: {apiKey}` — **not** `Authorization: Bearer`.
- Request **body** shape (contents / systemInstruction / tools / functionResponse)
  is identical between express and ADC mode — both front the same Gemini
  `generateContent` surface. This slice does **not** touch body building.
- Custom `model.baseUrl` / per-model headers compose on top in Pi; pipy already
  merges `extra_headers`, and a custom base URL for vertex is a separate,
  already-deferred concern (not in this slice).

### Fields this slice changes

| Field | Optionality | Default | Divergence from upstream |
|-------|-------------|---------|--------------------------|
| express api key source | optional | `GOOGLE_CLOUD_API_KEY` env, else catalog-forwarded `resolved.api_key` | none — matches `env-api-keys.ts` |
| express endpoint host | n/a | `https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent` | matches SDK express baseUrl + path |
| express auth header | n/a | `x-goog-api-key: <key>` | matches `NodeAuth.addKeyHeader` |
| sentinel/placeholder reject | n/a | reject `""`, `gcp-vertex-credentials`, `^<[^>]+>$` | matches `resolveApiKey` |

Adjacent vertex fields explicitly **out of scope** (already matched or separate
gaps): ADC bearer path (unchanged), per-model `thinkingConfig` (already deferred,
`provider-catalog.md`), custom vertex `model.baseUrl` rewrite, multi-regional
`*.rep.googleapis.com` hosts (Pi `MULTI_REGIONAL_LOCATIONS`, only relevant to ADC
project/location mode — not express).

## pipy implementation shape

1. **Adapter** (`google_vertex_provider.py`):
   - Add module constants `GOOGLE_VERTEX_EXPRESS_ENDPOINT_TEMPLATE =
     "https://aiplatform.googleapis.com/v1/publishers/google/models/{model_id}:generateContent"`
     and `GCP_VERTEX_CREDENTIALS_MARKER = "gcp-vertex-credentials"`.
   - Add field `api_key: str | None = field(default_factory=lambda:
     os.environ.get("GOOGLE_CLOUD_API_KEY"), repr=False)`, mirroring the existing
     `access_token` env default.
   - Add `_resolve_express_api_key(self) -> str | None` mirroring Pi's
     `resolveApiKey`: trim; return `None` if empty, equal to the marker, or
     matching `^<[^>]+>$`.
   - In `complete()`: after the `model_id` guard, compute
     `express_key = self._resolve_express_api_key()`.
     - **Express path** (`express_key` truthy): skip the project / access-token /
       location requirements; build the express URL (quote `model_id`); set
       header `x-goog-api-key: <express_key>` instead of `Authorization: Bearer`;
       merge `extra_headers`; record metadata `"vertex_auth_mode": "api-key"`
       (no `google_cloud_location`).
     - **ADC path** (no express key): unchanged — existing project/token/location
       guards, regional URL, `Authorization: Bearer`, metadata
       `"vertex_auth_mode": "adc"` + `google_cloud_location`.
   - The key is never logged/archived; only sanitized metadata leaves the
     boundary (existing invariant preserved — the key is not put in metadata).
2. **Catalog** (`provider_construction.py`):
   - `_build_iam_provider`: forward `api_key=resolved.api_key` to
     `GoogleVertexProvider` **only** (bedrock stays as-is — it has no api-key
     path). Forwarding a sentinel (`<authenticated>`)/placeholder is safe because
     the adapter's `_resolve_express_api_key` rejects it → ADC, exactly mirroring
     Pi forwarding `getEnvApiKey()`'s `<authenticated>` into `options.apiKey` and
     filtering it in `resolveApiKey`.
   - Update the module docstring + the `_build_iam_provider` docstring: vertex now
     forwards the resolved key (express mode); bedrock still does not.
3. **Availability** (`provider_registry.py`):
   - `env-google-vertex` branch: available when `GOOGLE_CLOUD_API_KEY` is set
     **or** the existing ADC condition (`GOOGLE_ACCESS_TOKEN` + project) holds —
     matching Pi's "either API key or ADC". Update the `unavailable_message`.

## Tests (TDD)

- Adapter express mode: with `api_key="vk"` (and no access token/project), the
  request URL is the global express URL, the header is `x-goog-api-key: vk` with
  **no** `Authorization`, and body/tool-call parsing is unchanged. A success
  response returns final text.
- Adapter sentinel/placeholder: `api_key="<authenticated>"` and
  `api_key="gcp-vertex-credentials"` fall back to ADC (require access token; send
  `Authorization: Bearer`).
- Adapter ADC unchanged: existing tests stay green (helper pins `api_key=None`).
- Catalog forwarding: `build_provider` for a vertex row with `GOOGLE_CLOUD_API_KEY`
  set forwards the key to the adapter (`vx_provider.api_key == "vk"`).
- Availability: vertex available with only `GOOGLE_CLOUD_API_KEY`; still available
  via ADC; unavailable with neither.
- Conformance gate `provider_catalog_conformance.py` item 22: update the "api_key
  is NOT forwarded" comment for vertex; add a check that the resolved key reaches
  the adapter and produces the express URL + `x-goog-api-key` header.

## Done-when

- Express mode reaches the pinned URL + header; ADC mode byte-identical to today.
- Availability honors `GOOGLE_CLOUD_API_KEY`.
- `provider-catalog.md`, `pi-mono-gap-audit.md`, `backlog.md` updated: the Vertex
  API-key follow-on struck/marked shipped.
- `just check` green; conformance gate green; different-family review CLEAN over
  the full diff.

## Constraints (AGENTS.md)

Stdlib only (no new deps); the API key is secret material — never logged or placed
in archive/session metadata; sanitized metadata only at the boundary; match Pi
behavior through pipy-owned Python rather than porting the SDK.

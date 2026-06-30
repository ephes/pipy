# Providers and models

Pipy's provider/model surface follows Pi's catalog model: a run starts with a
provider and model, the interactive TUI can switch models without leaving the
session, and custom `models.json` entries merge with the built-in catalog.
This page is the user guide for that shipped behavior. The implementation
contract lives in [Provider Catalog](provider-catalog.md).

## List available models

Use `--list-models` before starting a session:

```sh
pipy --list-models
pipy --list-models claude
pipy repl --list-models openrouter
```

The table shows provider, model id, context window, maximum output, whether the
row supports thinking, and whether it accepts image inputs. The optional search
filters over the combined `provider model` text and exits without running a
provider turn.

The built-in catalog includes rows for the implemented adapter families:

- `fake` — deterministic local bootstrap provider.
- `openai` and `openai-completions` — OpenAI Responses and Chat Completions.
- `openai-codex` — ChatGPT/Codex OAuth-backed responses.
- `anthropic`, `mistral`, `google`, `google-vertex`, `amazon-bedrock`,
  `azure-openai`, `cloudflare`, and `openrouter`.

Package or per-run extensions may add temporary provider rows for the current
process. `models.json` may also add custom providers and models.

## Choose a provider and model

Startup defaults to the deterministic fake provider so a checkout can smoke-test
without network access. Choose a real provider at startup with:

```sh
pipy --native-provider anthropic --native-model claude-3-5-sonnet-20241022
pipy -p --native-provider openai --native-model gpt-4o-mini "summarize this repo"
```

Inside the product TUI, use `/model` to open the provider/model selector or
`/model provider/model` to switch directly. Unavailable rows stay visible with a
reason, but cannot be selected. Switching models clears the in-memory provider
conversation context and keeps the session file as the durable transcript.

Use `/scoped-models` or the `--models` flag to constrain Ctrl+P model cycling:

```sh
pipy --models 'anthropic/*,openai/gpt-4o-mini'
```

Patterns are globs over `provider/model`. A `:level` suffix is accepted for
Pi-shaped syntax, but the initial per-pattern thinking preference is not yet
applied.

## Credentials and auth sources

Pipy never writes API keys or OAuth refresh material to session transcripts,
exports, or shared artifacts. Configure credentials through environment
variables, provider auth commands, or `models.json` references.

Common built-in sources:

| Provider | Typical credential source |
| --- | --- |
| `openai`, `openai-completions` | `OPENAI_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `google` | `GOOGLE_API_KEY` or `GEMINI_API_KEY` |
| `mistral` | `MISTRAL_API_KEY` |
| `azure-openai` | (`AZURE_OPENAI_BASE_URL` or `AZURE_OPENAI_RESOURCE_NAME`) and `AZURE_OPENAI_API_KEY` |
| `cloudflare` | `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` |
| `amazon-bedrock` | AWS environment/profile credentials used by the adapter |
| `google-vertex` | `GOOGLE_CLOUD_API_KEY` (Vertex Express), or `GOOGLE_ACCESS_TOKEN` + project + location |
| `openai-codex` | `pipy auth openai-codex login` or `/login openai-codex` |

`--api-key` is a runtime override for catalog-constructed providers and is kept
out of archives. Prefer environment variables or `models.json` env-name
references for repeatable local setup.

### Azure OpenAI configuration

The `azure-openai` provider resolves its endpoint and deployment from these env
vars (matching Pi):

- `AZURE_OPENAI_BASE_URL` — the base URL. Azure hosts are normalized to the
  `/openai/v1` surface; custom gateway URLs are used verbatim.
- `AZURE_OPENAI_RESOURCE_NAME` — used when no base URL is set, to build
  `https://{name}.openai.azure.com/openai/v1`.
- `AZURE_OPENAI_DEPLOYMENT_NAME_MAP` — a `modelId=deployment,...` map that
  overrides the deployment name per model id (otherwise the model id is the
  deployment).
- `AZURE_OPENAI_API_VERSION` — the API version (default `v1`).
- `AZURE_OPENAI_API_KEY` — the `api-key` credential.

## Custom providers with `models.json`

Pipy loads custom model configuration from:

```text
${PIPY_CONFIG_HOME}/models.json
${XDG_CONFIG_HOME}/pipy/models.json
~/.config/pipy/models.json
```

The file may contain `//` comments and trailing commas. At a high level:

```jsonc
{
  "providers": {
    "local-openai": {
      "baseUrl": "http://127.0.0.1:8000/v1",
      "api": "openai-completions",
      "apiKey": "LOCAL_OPENAI_API_KEY",
      "models": [
        {
          "id": "my-local-model",
          "name": "My local model",
          "contextWindow": 128000,
          "maxTokens": 16384,
          "input": ["text"]
        }
      ]
    }
  }
}
```

`apiKey` and header values may be literal values, environment-variable names, or
`!command` values resolved at request time. Status and listing paths do not run
`!command` values. Custom rows merge with built-ins by `provider + id`, and
`modelOverrides` can override built-in metadata without redefining the entire
row. See [Provider Catalog](provider-catalog.md#modelsjson-custom-providermodel-overrides-and-routing)
for the full schema.

### ds4 local provider preset

The first local-model path is `ds4` (`antirez/ds4` DeepSeek V4 Flash). The
recommended durable setup is a `models.json` provider using the
`openai-completions` adapter; see `docs/examples/ds4.models.json` for a working
example. The convenience environment shim `PIPY_DS4_BASE_URL` and
`PIPY_DS4_API_KEY` can synthesize the same provider for local experiments.

## Thinking, images, and current limits

The model table's `thinking` and `images` columns are capability metadata from
the catalog. `--thinking off|minimal|low|medium|high|xhigh` stores the selected
thinking level in local provider-selection state and the catalog construction
layer maps shipped adapter families where supported. Some provider-specific
request shapes remain follow-up work; when a row or adapter cannot apply a
level, pipy falls back safely rather than inventing unsupported parameters.

Images are accepted only for rows marked `images yes`; attach files using the
current image/file-reference workflows described in [Using pipy](usage.md).

## Follow-ons

Provider/model parity is mostly wired, but these user-visible improvements are
still tracked:

- live Anthropic and GitHub Copilot login UX; and
- broader local-provider maturity and benchmarking.

(Shipped: Vertex API-key (Express) auth via `GOOGLE_CLOUD_API_KEY`; the Anthropic
adaptive-thinking request shape; Azure URL/api-version parity.)

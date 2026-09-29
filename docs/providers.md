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

Supported Anthropic Claude 4.5+ and selected OpenAI Responses/Codex Responses
models preserve prompt-cache prefixes when an extension tool activates new
tools: the definitions load at that tool result instead of moving into the
earlier request prefix. Custom models opt in explicitly with
`compat.supportsToolReferences` (Anthropic) or `compat.supportsToolSearch`
(Responses); both features default off for unverified endpoints.

## Choose a provider and model

Startup defaults to the deterministic fake provider so a checkout can smoke-test
without network access. Choose a real provider at startup with:

```sh
pipy --native-provider anthropic --native-model claude-sonnet-5-5
pipy -p --native-provider openai --native-model gpt-6-sol "summarize this repo"
```

With only `--native-provider`, pipy uses that provider's default model, which
mirrors Pi's `defaultModelPerProvider`:

| Provider | Default |
|---|---|
| anthropic | `claude-opus-4-8` |
| openai and openai-codex | `gpt-5.5` |
| google and google-vertex | `gemini-3.1-pro-preview` |
| amazon-bedrock | `us.anthropic.claude-opus-4-6-v1` |
| azure-openai | `gpt-5.4` |
| openrouter | `moonshotai/kimi-k2.6` |
| cloudflare | `@cf/moonshotai/kimi-k2.6` |
| mistral | `devstral-medium-latest` |
| openai-completions | `gpt-5.5` |

The built-in catalog tracks Pi's current rows:

| Provider | Models |
|---|---|
| Anthropic | Claude Opus/Sonnet 5.5, Opus/Sonnet 5, Fable 5/5.1, Opus 4.8/4.7, Sonnet 4.5, Haiku 4.5 |
| OpenAI and Codex | GPT-6 Sol/Luna/Astra, GPT-5.6 Sol/Terra/Luna, GPT-5.5 |
| Gemini | 3.1 Pro, 3.5 Flash, 3.1 Flash Lite |
| Bedrock | `us.` Claude mirrors |

`pipy --list-models` shows the full table.

Inside the product TUI, use `/model` to open the provider/model selector or
`/model provider/model` to switch directly. Unavailable rows stay visible with a
reason, but cannot be selected. Switching models clears the in-memory provider
conversation context and keeps the session file as the durable transcript.

Extension commands and safe pre-turn hooks may make the same switch through
`ctx.set_model(...)`. Pipy resolves and constructs that candidate provider
before taking the session mutex, then atomically verifies the context's creating
generation, reload publication gate, terminal state, expected live selection,
and coding provider binding before committing the in-memory rebind. A stale,
gated, terminal, failed-construction, or superseded candidate returns `False`
and cannot change the selection or coding context. A successful switch still
clears in-memory provider history and usage, retains compaction/provider-failure
state, and is visible to the current turn when called from
`before_agent_start`. For compatibility, resolving a tool-incompatible target
with an explicit `:level` still retains that thinking level while leaving the
provider selection and coding state unchanged. Footer refresh and fail-soft
default persistence happen
after the mutex is released; provider construction and defaults-file I/O never
run while it is held.

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

### OpenAI-Codex timeout and failure behavior

OpenAI-Codex SSE requests and WebSocket receives use a five-minute (300000 ms) idle
timeout by default, rather than a 60-second total-turn deadline. Configure
`httpIdleTimeoutMs` in settings, or use `retry.provider.timeoutMs` as the
provider override. Values are integer milliseconds; `0` disables the deadline.
The timeout restarts naturally after successful socket activity.

Recognized connection/header failures and interrupted reads become sanitized
provider failures. They do not expose raw socket messages, response bodies,
prompts, auth values, or tool payloads. Cancellation remains a distinct abort,
and an exhausted failure returns control to the REPL so a later prompt can run.
The provider owns a bounded request-plus-stream attempt loop. Transient HTTP,
connection, header, and stream failures are retried only before the first
accepted provider event; after any metadata, reasoning, text, or tool event,
the turn fails without replay so visible output and tool work cannot be
duplicated. Backoff is cancellation-aware and honors bounded `retry-after-ms`
and `Retry-After` delays. `retry.provider.maxRetries` overrides the global
retry count; `retry.enabled=false` makes exactly one outer attempt. There is no
higher-level automatic turn replay. OpenAI-Codex now honors `transport: auto`,
`sse`, and `websocket`: `auto` and explicit `websocket` try the Responses
WebSocket path first, fall back to SSE only on recognized pre-event transport
failures, remember SSE fallback for later `auto` calls, and never fall back
after provider progress. Long-lived WebSocket reuse/continuation caching remains
out of scope.

### OpenAI prompt caching

pipy mirrors Pi's prompt-cache affinity for the OpenAI families, so turns in
one session land on the same provider cache:

- **OpenAI-Codex** sends the current session id, cut to 64 characters, as the
  body `prompt_cache_key` and as the `session-id` and `x-client-request-id`
  headers. SSE and WebSocket both send it. The WebSocket uses a fresh id per
  call when there is no cache session.
- **OpenAI Responses** sends the same key and Pi's affinity headers with the
  full session id. The headers are `session_id` and `x-client-request-id`, or
  `x-session-id` for OpenRouter. A row's `compat.sessionAffinityFormat`
  (`openai`, `openai-nosession`, `openrouter`) overrides the choice.
  `PIPY_CACHE_RETENTION=long` (Pi's `PI_CACHE_RETENTION`) requests long
  retention: `prompt_cache_retention: "24h"`, or `prompt_cache_options.ttl:
  "30m"` on rows with `compat.supportsExplicitPromptCacheMode` (the built-in
  GPT-5.6 and GPT-6 rows). `compat.supportsLongCacheRetention: false` turns it
  off.
- **Azure OpenAI** sends the key only.

Compaction and branch summaries are private one-off calls. As in Pi, they use a
fresh routing id and cache retention `none`: no key and no affinity headers,
plus `prompt_cache_options: {mode: "explicit"}` on explicit-mode rows. Azure
ignores retention, so it still gets the routing id as its key. Extension
`ctx.complete` calls send no session id.

### Anthropic prompt caching

pipy places Pi's Anthropic prompt-cache breakpoints, so a session's growing
prefix is read from the cache on the next turn:

- **Anthropic Messages** marks three places with
  `cache_control: {type: "ephemeral"}`. They are the system prompt (sent as one
  text block, and omitted when empty), the last tool, and the last block of the
  conversation when the last message is a user turn (text, image or
  `tool_result`). Deferred (`defer_loading`) tools never get a marker; the last
  immediate tool does.
- **Amazon Bedrock** marks the system prompt and the last block of a trailing
  user message, not tools. It does so only for the Claude families Pi caches:
  3.5 Haiku, 3.7 Sonnet, 4.x and 5.x. Set `AWS_BEDROCK_FORCE_CACHE=1` for model
  ids that do not name Claude, such as application inference profiles.
- `PIPY_CACHE_RETENTION=long` (Pi's `PI_CACHE_RETENTION`) adds `ttl: "1h"`.
  The default is the provider's 5-minute cache. Compaction and branch summaries
  use retention `none` and send no markers.
- The usage split `cache_creation.ephemeral_1h_input_tokens` is kept as
  `cache_write_1h_tokens`. Session cost prices it at twice the input rate, as
  in Pi (see [Session cost](#session-cost)).
- models.json rows can set Pi's compat flags:
  - `supportsLongCacheRetention: false` drops the 1h TTL.
  - `supportsCacheControlOnTools: false` drops the tool marker.
  - `sendSessionAffinityHeaders` sends the session id as `x-session-affinity`,
    or as `x-session-id` when `sessionAffinityFormat` is `openrouter`. Both are
    on by default for OpenRouter endpoints. Model headers override the
    affinity header.

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
~/.pipy/models.json          (when ~/.pipy exists)
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
the catalog. `--thinking off|minimal|low|medium|high|xhigh|max` sets the level
that the catalog construction layer maps to each adapter family.

Without `--thinking`, a session starts at the settings `defaultThinkingLevel`,
or at `medium` if that is unset, as in Pi. The level is clamped to what the
startup model supports, and again on every model switch.

Which levels a model offers follows Pi:

- `xhigh` and `max` are offered only by models that map them. For example,
  `openai-codex/gpt-6-sol` and `anthropic/claude-opus-5-5` map both.
- A level a model maps to `null` is not offered. On models that cannot switch
  thinking off, that includes `off`. Examples are Claude Fable 5, Claude
  Opus/Sonnet 5.5, GPT-6 Astra and Gemini 3.x.

Shift+Tab cycling and the OpenAI/Azure/Codex/Gemini request-path clamp follow
this per-model support. On OpenAI Responses, Azure and Codex, `off` sends Pi's
explicit `reasoning.effort: "none"` (or the row's own off value). No
`reasoning` field is sent when the model cannot switch thinking off. Some provider-specific
request shapes remain follow-up work; when a row or adapter cannot apply a
level, pipy falls back safely rather than inventing unsupported parameters.

Images are accepted only for rows marked `images yes`; attach files using the
current image/file-reference workflows described in [Using pipy](usage.md).

## Session cost

Every turn is priced from the selected model's catalog row, with Pi's
`calculateCost`. That includes rows changed by `models.json` `cost` overrides
and custom models (which cost nothing unless they set `cost`).

- Rates are dollars per million tokens for uncached input, output, cache reads
  and cache writes. Reasoning tokens are part of output. 1h cache writes cost
  twice the input rate.
- Uncached input is the prompt minus cache reads and writes. Anthropic and
  Bedrock report those separately. OpenAI, Codex, Azure, Chat Completions,
  OpenRouter, Mistral and Gemini count them inside the prompt, and pipy
  subtracts them as Pi does. Gemini thinking tokens count as output.
- `cost.tiers` (`inputTokensAbove` plus the four rates) price the whole request
  at the highest threshold its prompt exceeds. `models.json` can set tiers on a
  custom model or in `modelOverrides`, where they replace the row's tiers.
  Built-in rows carry no tiers yet, so a long-context request on a tiered Pi
  model (such as Codex `gpt-5.5` above 272k) is priced at base rates.
- The footer shows the cost as `$0.123`, and hides it at zero. On a
  subscription login (OpenAI Codex, Anthropic or GitHub Copilot OAuth, or an
  extension OAuth provider with `is_subscription=True`) it always shows the
  cost marked `$0.123 (sub)`. That is the API-equivalent price, not a bill.
- RPC `get_session_stats` returns the same totals: `tokens.input` is uncached
  input, `total` sums input, output and both cache counters, and `cost` is the
  dollar total.
- The totals cover the live session since the model was last selected. A
  model switch or re-login starts them at zero, and a resumed session starts at
  zero, because pipy does not yet store usage per message (Pi does).

## Follow-ons

Provider/model parity is mostly wired, but these user-visible improvements are
still tracked:

- live Anthropic and GitHub Copilot login UX; and
- broader local-provider maturity and benchmarking.

(Shipped: Vertex API-key (Express) auth via `GOOGLE_CLOUD_API_KEY`; the Anthropic
adaptive-thinking request shape; Azure URL/api-version parity.)

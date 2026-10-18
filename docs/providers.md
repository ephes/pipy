# Providers and models

Pipy's provider/model surface follows Pi's catalog model: a run starts with a
provider and model, the interactive TUI can switch models without leaving the
session, and custom `models.json` entries merge with the built-in catalog.
This page is the user guide for that shipped behavior. The implementation
contract lives in [Provider Catalog](provider-catalog.md).

See [Provider capabilities and evidence](provider-capabilities.md) for which
API families deliver incremental text/thinking, which return buffered answers,
and the distinction between deterministic tests and dated live evidence.

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
- `xai` — Grok over the OpenAI Responses API.
- `github-copilot` — GitHub Copilot's Claude, GPT, Grok, Gemini, Kimi and MAI
  models, each on the API family Copilot serves it with (Anthropic Messages,
  OpenAI Responses, or Chat Completions).

Package or per-run extensions may add temporary provider rows for the current
process. `models.json` may also add custom providers and models.

Models that accept mid-conversation system messages keep their prompt-cache
prefix when the prompt or the tool set changes: the change arrives as a later
system message where it happened (Pi `9e05370b2`). Tools added later load in
place (`additional_tools` or a client `tool_search` pair on OpenAI
Responses/Codex, `tool_addition` blocks on Anthropic). The built-in rows set
the flags from Pi's catalog; custom models opt in with
`compat.supportsMidConvoSystemMessages` plus `supportsAdditionalTools` /
`supportsToolSearch` (Responses), `supportsMidConvoToolAdditions` (Chat
Completions) or `supportsMidConvoToolChanges` (Anthropic). All default off,
and every other model receives the current prompt and tools as before. See
[provider-catalog.md](provider-catalog.md#mid-conversation-system-messages).

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
| openai | `gpt-5.5` |
| openai-codex | `gpt-6.1-sol` |
| google and google-vertex | `gemini-3.1-pro-preview` |
| amazon-bedrock | `us.anthropic.claude-opus-4-6-v1` |
| azure-openai | `gpt-5.4` |
| openrouter | `moonshotai/kimi-k2.6` |
| cloudflare | `@cf/moonshotai/kimi-k2.6` |
| mistral | `devstral-medium-latest` |
| openai-completions | `gpt-5.5` |
| xai | `grok-4.7` |
| github-copilot | `gpt-5.4` |

The built-in catalog tracks Pi's current rows:

| Provider | Models |
|---|---|
| Anthropic | Claude Opus/Sonnet 5.5, Opus/Sonnet 5, Fable 5/5.1, Opus 4.8/4.7, Sonnet 4.5, Haiku 4.5 |
| OpenAI and Codex | GPT-6.1 Sol, GPT-6 Sol/Luna/Astra, GPT-5.6 Sol/Terra/Luna, GPT-5.5 |
| Gemini | 3.1 Pro, 3.5 Flash, 3.1 Flash Lite |
| Bedrock | `us.` Claude mirrors |
| xAI | Grok 4.3, 4.5, 4.6, 4.7 |
| GitHub Copilot | Pi's 34 Copilot rows (Claude, GPT, Grok, Gemini, Kimi, MAI) |

`pipy --list-models` shows the full table.

Inside the product TUI, use `/model` to open Pi's searchable model selector,
which lists only models whose provider has auth, or `/model provider/model` to
switch directly when the reference names exactly one available (or scoped)
model; any other text opens the selector with it as the search. Enter
switches for the session; Ctrl+S also saves the default. See
[tui-workflow.md](tui-workflow.md#searchable-selectors-and-slash-completion-df1-f7b).
The model switch keeps the conversation (Pi `setModel`) and keeps the session
file as the durable transcript.

Extension commands and safe pre-turn hooks may make the same switch through
`ctx.set_model(...)`. Pipy resolves and constructs that candidate provider
before taking the session mutex, then atomically verifies the context's creating
generation, reload publication gate, terminal state, expected live selection,
and coding provider binding before committing the in-memory rebind. A stale,
gated, terminal, failed-construction, or superseded candidate returns `False`
and cannot change the selection or coding context. A successful switch keeps
in-memory provider history (Pi `setModel`) and session usage totals, retains
compaction/provider-failure state, and is visible to the current turn when called from
`before_agent_start`. A refused tool-incompatible target leaves the provider
selection, the thinking level (an explicit `:level` or a clamp to the target's
levels included) and the coding state unchanged. Footer refresh and fail-soft
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
| `openai` | `OPENAI_API_KEY`, or `/login openai` (Sign in with ChatGPT) |
| `openai-completions` | `OPENAI_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `google` | `GOOGLE_API_KEY` or `GEMINI_API_KEY` |
| `mistral` | `MISTRAL_API_KEY` |
| `azure-openai` | (`AZURE_OPENAI_BASE_URL` or `AZURE_OPENAI_RESOURCE_NAME`) and `AZURE_OPENAI_API_KEY` |
| `cloudflare` | `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` |
| `amazon-bedrock` | AWS environment/profile credentials used by the adapter |
| `google-vertex` | `GOOGLE_CLOUD_API_KEY` (Vertex Express), or `GOOGLE_ACCESS_TOKEN` + project + location |
| `openai-codex` | `pipy auth openai-codex login` or `/login openai-codex` |
| `xai` | `XAI_API_KEY` |
| `github-copilot` | `/login github-copilot`, or `COPILOT_GITHUB_TOKEN` |

### OpenAI: Sign in with ChatGPT

`/login openai` signs the `openai` provider in with a ChatGPT subscription
instead of an API key, as Pi does. Pipy prints the OpenAI authorization URL,
opens it in the browser, and starts a local callback listener on
`127.0.0.1:1455` (`PI_OAUTH_CALLBACK_HOST` changes the listening host). Finish
the sign-in in the browser, then press Enter: pipy takes the browser's
callback. When the callback cannot reach pipy (for example over SSH), paste
the final redirect URL (`http://127.0.0.1:1455/auth/callback?...`) instead.
Pipy's `/login` reads lines, so it cannot notice the callback before you press
Enter; Ctrl-C cancels the login.

The login registers a pipy client for your ChatGPT account and names this
installation with a stable device ID: the `deviceId` global setting, created
on the first sign-in. The credential (access and refresh token, expiry, the
issued client ID and the granted scopes) is stored in the pipy auth store under
`openai`; a stored login takes precedence over `OPENAI_API_KEY`.
`/logout openai` removes it and leaves the model selected when
`OPENAI_API_KEY` still covers it.

The access token is refreshed before a request when it has less than five
minutes left. OpenAI replaces the refresh token on every refresh, so pipy
writes the new credential back to the auth store under a file lock, and
another pipy process that already refreshed it is reused rather than
refreshed again.

A request whose credential does not start with `sk-` and goes to
`https://api.openai.com/v1` is treated as a ChatGPT token and sends no
`prompt_cache_retention` or `prompt_cache_options` (pipy's OpenAI adapter sends
no `max_output_tokens` or `temperature` either); `prompt_cache_key` and the
session headers stay. Usage through this login counts against the ChatGPT
subscription, so the footer marks its cost `(sub)`. When the subscription's
shared usage limit is reached (`subscription_sharing_usage_limit_exceeded`),
the error names the code and links to `https://chatgpt.com/settings/usage`, and
pipy does not retry it; `subscription_sharing_usage_unavailable` and
`subscription_sharing_user_unavailable` are retried like other temporary
provider errors.

Pi also renames the `openai-codex` provider to "OpenAI Codex (legacy)". Pipy
lists providers by id, so nothing changes there.

### GitHub Copilot

`/login github-copilot` runs GitHub's device-code login, as Pi does. Pipy asks
for a GitHub Enterprise URL or domain (leave it blank for github.com), prints
the verification URL and a user code, and waits while you approve the code in
a browser. It does not open the browser itself. When you have approved it,
pipy exchanges the GitHub token for a Copilot token, lists the models your
account can use, and enables the models whose policy is still unconfigured.
The credential is stored in the pipy auth store; `/logout github-copilot`
removes it.

After an OAuth login, only the models your account can use are offered in
`/model` and the other model lists. Each request uses the Copilot endpoint named
in the token (for example `api.business.githubcopilot.com`), or
`copilot-api.<enterprise domain>`. The short-lived Copilot token is refreshed
before a request when it has less than five minutes left. The refreshed token
is kept in memory for the running process; the stored GitHub token is what
survives restarts.

`COPILOT_GITHUB_TOKEN` is sent as-is to the default endpoint
(`api.individual.githubcopilot.com`), as in Pi.

Every Copilot request carries Copilot's editor headers plus `X-Initiator`
(`agent` after a tool or assistant turn, else `user`), `Openai-Intent:
conversation-edits`, and `Copilot-Vision-Request: true` when the request sends
an image, a tool result's image included. Claude models authenticate with a Bearer token.

Pipy does not offer Pi's xAI OAuth login (SuperGrok or X Premium); use
`XAI_API_KEY`.

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
Inside an agent turn, retries follow Pi's agent-level auto-retry, which applies
to every provider, not only OpenAI-Codex (see [Retries](#retries) below). A
stream `error` event reads `Codex error: <message or code>` and a
`response.failed` terminal reads its `response.error.message` (else `Codex
response failed`), as in Pi, so the retry classifier can see the server's
wording. Called directly, outside the agent's managed retry, the provider
keeps its own bounded request-plus-stream attempt loop: transient
HTTP, connection, header, and stream failures are retried only before the
first accepted provider event. Backoff is cancellation-aware and honors
bounded `retry-after-ms` and `Retry-After` delays. `retry.provider.maxRetries`
overrides the global retry count; `retry.enabled=false` makes exactly one
attempt. OpenAI-Codex now honors `transport: auto`,
`sse`, and `websocket`: `auto` and explicit `websocket` try the Responses
WebSocket path first, fall back to SSE only on recognized pre-event transport
failures, remember SSE fallback for later `auto` calls, and never fall back
after provider progress. Long-lived WebSocket reuse/continuation caching remains
out of scope.

### Retries

Like Pi's agent-level auto-retry, a failed provider request in a turn is
retried for **every** provider when `retry.enabled` is on (the default):

- **What is retried:** Pi's `isRetryableAssistantError` patterns over the
  error message plus the API error labels pipy lifts from the error body:
  overloaded (e.g. an Anthropic 529 `overloaded_error`), "Selected model is
  at capacity", rate limits, HTTP
  429/500/502/503/504/520/524, service unavailable, server/internal errors,
  network, connection, socket and timeout failures, closed WebSockets, early
  stream ends, and "you can retry your request". Failures a provider marks as
  transient are retried too. Quota or billing exhaustion (`insufficient_quota`,
  `billing`, ...) and context-overflow errors (Pi's `isContextOverflow`
  patterns) are never retried.
- **Mid-stream errors:** as in Pi, a failure after text already streamed is
  retried; each failed partial remains in durable history and the retried
  request starts a fresh assistant message. Tools only run after a successful response, so nothing runs
  twice.
- **Backoff:** `retry.maxRetries` retries (default 3) after
  `retry.baseDelayMs * 2^(n-1)` (default 2 s, 4 s, 8 s), capped by
  `retry.provider.maxRetryDelayMs`, without jitter. A longer server
  `retry-after` is honored (Pi's agent retry ignores it).
- **Display:** the TUI shows the failed attempt's `Error: <message>` and
  replaces `Working...` with `Retrying (1/3) in 2s... (escape to cancel)`,
  counting down. Escape during the wait cancels the retry, which reads
  `Retry failed after 1 attempts: Retry cancelled`; exhausted retries read
  `Retry failed after 3 attempts: <error>`. JSON and RPC modes emit
  `auto_retry_start` per retry and one `auto_retry_end` per sequence.

Each ordinary attempt completes its own assistant/turn/agent visibility lifecycle.
Failed assistants are stored with stop reason `error` and omitted from ordinary
provider replay. JSON/RPC publish `message_end(error)` and
`agent_end(willRetry=true)` before scheduling; each actual reissue starts a fresh
agent/turn/assistant lifecycle. Attempt `agent_end.messages` is local to that
low-level run; the SDK terminal result enumerates the complete accepted prompt.
One internal logical settlement retains queue and cancellation ownership across
all attempts. Backoff cancellation keeps the last error and adds no aborted
message or `Operation aborted` row; cancelling an active provider attempt keeps
its aborted partial. Compaction and branch summaries retry every provider with
one frozen, settings-derived policy and no jitter. Their content/usage stays
private; JSON/RPC and the TUI show dedicated bounded summarization retry statuses,
without ordinary assistant attempt messages or Error rows. Finished status means
provider retry execution finished, not that the summary was accepted or persisted.
Generic abort/Escape cancels them; abort_retry targets only ordinary retries.
See the [F4b contract](specs/2026-10-10-f4b-retry-closeout.md).

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
  Opus/Sonnet 5.5, GPT-6 Astra, GPT-6.1 Sol and Gemini 3.x. A carried `off`
  clamps to the lowest level such a model offers (on GPT-6.1 Sol: `minimal`,
  sent as `low`, on Codex; `low` on OpenAI and Copilot).

Shift+Tab cycling and the OpenAI/Azure/Codex/Gemini request-path clamp follow
this per-model support. On OpenAI Responses, Azure and Codex, `off` sends Pi's
explicit `reasoning.effort: "none"` (or the row's own off value). No
`reasoning` field is sent when the model cannot switch thinking off. On the
OpenAI Responses API (OpenAI, xAI, Copilot GPT/Grok rows), a thinking level
also sends `reasoning.summary: "auto"` and asks for the encrypted reasoning
item (`include: ["reasoning.encrypted_content"]`), as Pi does; xAI asks for it
even with thinking off. Copilot sends no `reasoning` field when thinking is
off, and its Gemini/Kimi rows never send `reasoning_effort`. Some provider-specific
request shapes remain follow-up work; when a row or adapter cannot apply a
level, pipy falls back safely rather than inventing unsupported parameters.

When replaying assistant history, thinking is sent only to the exact provider,
API and model that produced it. A model or provider switch drops thinking from
the outgoing request, including readable, signed, redacted and encrypted blocks;
it does not turn private thinking into visible assistant text. Older messages
without recorded API provenance are also treated as foreign. Same-model replay
retains signed/redacted blocks (including empty encrypted reasoning) and
nonblank unsigned thinking for the adapter to serialize. Ordinary assistant
text and tool calls/results remain; foreign text and Gemini tool-call
signatures are stripped. The stored [session history](sessions.md) is unchanged.

Images are accepted only for rows marked `images yes`; attach files using the
current image/file-reference workflows described in [Using pipy](usage.md).

A tool result can hold images (an MCP tool's screenshot, for example). They
are sent to the model as Pi sends them:

| Adapter family | Where the images go |
| --- | --- |
| Anthropic Messages, Bedrock | Inside the `tool_result`, text and image blocks in the tool's order. A result that is images only starts with the text `(see attached image)`. |
| OpenAI Responses, Azure, Codex | The `function_call_output` is a list: the result's text as one `input_text`, then one `input_image` per image. |
| Gemini, Vertex | Inside the function response (`parts`) for Gemini 3 and later and for models that are not Gemini. For Gemini before 3, in a user turn `Tool result image:` after the function response. |
| Chat Completions (OpenAI-compatible, OpenRouter, Mistral, Cloudflare, ds4) | The `tool` message keeps the text (`(see attached image)` for images only). The images of a run of tool results follow it in one user message `Attached image(s) from tool result:`. |

A model whose row says `images no` gets the text `(tool image omitted: model
does not support images)` in place of each run of images. The session keeps
the images, so a later switch to a model that takes them sends them. A model
that no catalog row describes is sent the images, also when its provider is
a known one.

Different from Pi: Pi's Mistral adapter puts the images into the `tool`
message itself and marks an error result's text with `[tool error]`; pipy's
Mistral adapter is a Chat Completions adapter and sends them as that family
does. Pi's Chat Completions adapter adds an assistant message before the image
message for models that need one (`requiresAssistantAfterToolResult`); pipy
has no such switch.

### Size of tool result images and `images` settings

Tools that make images themselves (MCP servers, codemode scripts, extension
tools) can return images of any size, and a provider that rejects one rejects
every later request of the session, since the result is stored. As in Pi,
every image of a tool result is therefore processed once, after the
`tool_result` hooks and before it is shown, stored or sent; this holds for a
call a script or another tool made as well:

- PNG, JPEG, GIF and WebP stay in their type (`image/jpg` is renamed
  `image/jpeg`). Another type pipy can decode (BMP, ICO, TIFF, TGA, PNM, DDS)
  becomes a PNG, followed by the text `[Image converted from <type> to
  <new type>.]`.
- With `images.autoResize` (default `true`), an image wider or taller than
  2000 pixels, or with more than 4.5 MB of base64 data, is scaled down to fit
  (a JPEG or WebP is first turned upright by its EXIF orientation). It is
  then sent as a PNG or, if that is too large, as a JPEG of falling quality,
  whichever first fits under 4.5 MB; failing that, the size shrinks by a
  quarter at a time. A text follows the image: `[Image: original 3000x1000,
  displayed at 2000x667. Multiply coordinates by 1.50 to map to original
  image.]`.
- An image that cannot be decoded or made small enough is kept as the tool
  returned it.

The built-in `read` tool processes an image file the same way before it
returns it, as Pi's `read` does: a BMP is converted to PNG and, with
`images.autoResize`, a larger image is resized, and the hint lines follow its
`Read image file [<type>]` line. An image file it cannot convert or resize is
not attached; `read` returns that line and Pi's text for it, such as
`[Image omitted: could not be resized below the inline image size limit.]`.

This needs Pillow, from the `images` extra (`uv sync --extra images` in a
checkout, `uv tool install '.[images]'` as a tool). Without it every image
is kept as the tool returned it, which is also what Pi does when its image
library cannot load. `read` then attaches a PNG, JPEG, GIF or WebP file that
already fits the limits (with `images.autoResize` off, any such file) and
returns Pi's `[Image omitted: ...]` text for a larger one or a BMP.

With `images.blockImages` set to `true`, no tool result image is sent: each
image becomes the text `Image reading is disabled.`, and of several such
texts in a row only the first remains. The `@image` attachments of the
user's turn are not sent either: the user message gains one line `Image
reading is disabled.` in their place, as Pi replaces a user message's
images. The setting is read for every request, so it takes effect at once,
and the session keeps the images.

Different from Pi:

- Pi takes the limits from the model's catalog row (`inputLimits`); pipy's
  catalog has none and uses Pi's defaults, which every row of Pi's catalog
  repeats.
- Pi resizes with Photon, pipy with Pillow: the resized bytes differ, and an
  image of more than about 89 million pixels is kept as it is rather than
  decoded.
- A note that would take a result's text past 64 KiB is left out; the image
  is still resized.

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
- The totals sum usage stored on every assistant message in the session,
  including all branches and compacted history, as Pi does. They survive model
  switches, re-login and resume, and reset only with a new session.

## Credential command helpers

Configured auth `!command` helpers print the value on stdout, read as UTF-8
with invalid bytes replaced by U+FFFD and trimmed as JavaScript's `trim()`
trims (which also drops a byte order mark), as in Pi. Nonzero exits, timeouts,
empty output and stdout above 1 MiB (Node's `maxBuffer`, as in Pi) quietly
resolve as unavailable, without logging output. As in Pi, a helper's
stderr is discarded and does not count toward that limit. Helpers run in an owned POSIX
process group with a ten-second deadline; cleanup sends TERM, waits up to
0.2 seconds for the whole group (even when the direct child already exited),
then sends KILL only if members remain and waits up to 0.2 seconds more.
Descendants must stay in that group; deliberately detached helpers are unsupported.

Successful completed helpers retain background work with redirected stdio.
Helpers run as background jobs; interactive terminal input is unsupported.

A helper stored as an API key in `auth.json` (`pipy auth` or `/login`) runs
once per process, as in Pi: its first result, also a failure, is kept for
every later model request and MCP `auth.provider` request until pipy exits.
A `models.json` `apiKey` or header helper runs every time it is resolved, as
in Pi.

## Follow-ons

Provider/model parity is mostly wired, but these user-visible improvements are
still tracked:

- live Anthropic login UX; and
- broader local-provider maturity and benchmarking.

(Shipped: Vertex API-key (Express) auth via `GOOGLE_CLOUD_API_KEY`; the Anthropic
adaptive-thinking request shape; Azure URL/api-version parity; the `xai` and
`github-copilot` providers with `/login github-copilot`; Sign in with ChatGPT
for `openai` with `/login openai`.)

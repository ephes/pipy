# Settings

Pipy reads JSON settings from a global config file and an optional project file.
Project settings override global settings, and nested objects are deep-merged.

| Location | Scope |
| --- | --- |
| `$PIPY_CONFIG_HOME/settings.json` | Global when `PIPY_CONFIG_HOME` is set |
| `${XDG_CONFIG_HOME}/pipy/settings.json` | Global fallback |
| `~/.config/pipy/settings.json` | Global default |
| `.pipy/settings.json` | Project settings for the current directory |

`~/.pipy` is also honored as a legacy/convenience config root when it already
exists. Pipy intentionally uses `.pipy`, not Pi's `.pi`, for project config.

Project settings load only when the final runtime directory is trusted. Pipy
stores saved decisions in `<global-config-root>/trust.json`; the closest saved
directory or ancestor wins. A global-only setting controls the fallback:

```json
{
  "defaultProjectTrust": "ask"
}
```

Accepted values are `ask` (the default), `always`, and `never`; `/settings`
exposes them as `Ask`, `Trust`, and `Do not trust`. For a single run,
`--approve` / `-a` trusts project inputs and `--no-approve` / `-na` blocks them
without changing `trust.json`; if repeated, the last flag wins. An unresolved
interactive `ask` opens the five-choice trust selector. `/trust` shows the saved
exact/inherited decision and current run state, then saves a next-restart
decision without hot-loading resources. Headless print/JSON/RPC paths fail
closed silently so their protocols stay clean.

Trust only controls which project inputs pipy loads. It is not a sandbox, and a
trusted project can still influence tools and execute extension code with the
pipy process's permissions.

Use `/settings` for common interactive controls and `/reload` after editing
settings by hand. Provider secrets stay in auth stores or environment variables;
do not put API keys in `settings.json`.

## Common examples

Choose a default model and theme:

```json
{
  "defaultProvider": "openai",
  "defaultModel": "gpt-4.1",
  "theme": "dark"
}
```

Set project-specific session files and quieter startup chrome:

```json
{
  "sessionDir": ".pipy/sessions",
  "quietStartup": true
}
```

Constrain Ctrl+P model cycling:

```json
{
  "enabledModels": ["openai/gpt-*", "anthropic/claude-*"]
}
```

## Settings reference

Pipy accepts and preserves the Pi-shaped settings below, but support is bounded
by the native Python runtime. Some fields are active only when the selected
provider, terminal, package source, or TUI surface supports them; otherwise they
are harmless future-compatible configuration rather than guaranteed behavior.
The notes call out the most important limits.

### Model and thinking

| Setting | Type | Notes |
| --- | --- | --- |
| `defaultProvider` | string | Default provider id. `/model` and CLI flags can override it. |
| `defaultModel` | string | Default model id for the provider. |
| `defaultThinkingLevel` | string | `off`, `minimal`, `low`, `medium`, `high`, or `xhigh`; provider request mapping is still provider-dependent. |
| `hideThinkingBlock` | boolean | Hide thinking blocks where the renderer/provider supports it. |
| `enabledModels` | string array | Patterns used by `/scoped-models` and Ctrl+P cycling. |

### UI and startup

| Setting | Type | Notes |
| --- | --- | --- |
| `theme` | string | Active chrome theme name. Can also be changed in `/settings`. |
| `quietStartup` | boolean | Hide verbose startup/resource chrome. `--verbose` overrides for one run. |
| `collapseChangelog` | boolean | Show condensed changelog output. |
| `editorPaddingX` | number | Input editor horizontal padding, `0`-`3`. |
| `autocompleteMaxVisible` | number | Visible autocomplete rows, `3`-`20`. |
| `showHardwareCursor` | boolean | Show the terminal cursor while the TUI positions it. |
| `promptHistory.enabled` | boolean | Enable local persistent prompt history. Off by default. |
| `defaultProjectTrust` | `ask` / `always` / `never` | Global scope only; project values cannot choose their own trust. Default `ask`. |

### Sessions and compaction

| Setting | Type | Notes |
| --- | --- | --- |
| `sessionDir` | string | Native product session root. CLI `--session-dir` wins. |
| `compaction.enabled` | boolean | Enable durable compaction when enough context exists. |
| `compaction.reserveTokens` | number | Tokens reserved for the response. |
| `compaction.keepRecentTokens` | number | Recent tokens kept outside the summary. |
| `branchSummary.reserveTokens` | number | Token budget for abandoned-branch summaries. |
| `branchSummary.skipPrompt` | boolean | Skip the `/tree` branch-summary prompt. |

### Retry, delivery, and transport

| Setting | Type | Notes |
| --- | --- | --- |
| `retry.enabled` | boolean | Enable agent/provider retry policy. |
| `retry.maxRetries` | number | Global maximum retry count (default 3); bounded OpenAI-Codex retries occur only before the first provider event. |
| `retry.baseDelayMs` | number | Initial exponential retry delay in milliseconds (default 2000). |
| `retry.provider.timeoutMs` | number | OpenAI-Codex idle-timeout override in milliseconds; inherits `httpIdleTimeoutMs`; `0` disables. |
| `retry.provider.maxRetries` | number | OpenAI-Codex retry-count override; invalid/unset values inherit `retry.maxRetries`. |
| `retry.provider.maxRetryDelayMs` | number | Cap for exponential, jitter, and server-requested retry delays. |
| `steeringMode` | string | `one-at-a-time` or `all`; active only for the shipped queue surfaces. |
| `followUpMode` | string | `one-at-a-time` or `all`; active only for the shipped queue surfaces. |
| `transport` | string | `auto`, `sse`, or `websocket` where a provider supports choices; OpenAI-Codex applies it with WebSocket-first fallback semantics. |
| `httpIdleTimeoutMs` | number | Header/body idle timeout in integer milliseconds; default `300000`; `0` disables. OpenAI-Codex applies it to SSE socket idleness and WebSocket receives, not total turn time. |
| `websocketConnectTimeoutMs` | number | WebSocket open timeout in integer milliseconds; default `15000`; `0` disables. |

### Resources and packages

| Setting | Type | Notes |
| --- | --- | --- |
| `packages` | array | Installed local-path or managed-git package sources, or objects with resource filters. |
| `extensions` | string array | Extension path patterns. |
| `skills` | string array | Skill path patterns. |
| `prompts` | string array | Prompt-template path patterns. |
| `themes` | string array | Theme path patterns. |
| `enableSkillCommands` | boolean | Register skills as slash-command resources. |

Resource arrays support include/exclude patterns as used by `pipy config`.
Local-path and managed-git package sources are supported; PyPI/npm package
sources remain deferred pending supply-chain policy.

### Privacy and network behavior

`enableInstallTelemetry` is accepted, but pipy's default is off. Use `--offline`
or `PIPY_OFFLINE=1` to disable startup network operations for a run. Auth
credentials and API keys are handled outside `settings.json`.

## Project overrides

When the project is untrusted, this entire scope is skipped without opening or
parsing `.pipy/settings.json`; global settings and CLI overrides still apply.

Nested project objects merge over global objects:

Global `settings.json`:

```json
{
  "theme": "dark",
  "compaction": { "enabled": true, "reserveTokens": 16384 }
}
```

Project `.pipy/settings.json`:

```json
{
  "compaction": { "reserveTokens": 8192 }
}
```

The effective settings keep `theme: "dark"` and `compaction.enabled: true`, but
use `compaction.reserveTokens: 8192`.

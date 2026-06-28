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
| `enabledModels` | string[] | Patterns used by `/scoped-models` and Ctrl+P cycling. |

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
| `retry.maxRetries` | number | Maximum retry count. |
| `retry.baseDelayMs` | number | Initial retry delay in milliseconds. |
| `retry.provider.maxRetryDelayMs` | number | Cap for provider-requested retry delays. |
| `steeringMode` | string | `one-at-a-time` or `all`; active only for the shipped queue surfaces. |
| `followUpMode` | string | `one-at-a-time` or `all`; active only for the shipped queue surfaces. |
| `transport` | string | `auto`, `sse`, or `websocket` where a provider supports choices; otherwise accepted but no-op. |
| `httpIdleTimeoutMs` | number | HTTP idle timeout in milliseconds; `0` disables. |

### Resources and packages

| Setting | Type | Notes |
| --- | --- | --- |
| `packages` | array | Installed local-path or managed-git package sources, or objects with resource filters. |
| `extensions` | string[] | Extension path patterns. |
| `skills` | string[] | Skill path patterns. |
| `prompts` | string[] | Prompt-template path patterns. |
| `themes` | string[] | Theme path patterns. |
| `enableSkillCommands` | boolean | Register skills as slash-command resources. |

Resource arrays support include/exclude patterns as used by `pipy config`.
Local-path and managed-git package sources are supported; PyPI/npm package
sources remain deferred pending supply-chain policy.

### Privacy and network behavior

`enableInstallTelemetry` is accepted, but pipy's default is off. Use `--offline`
or `PIPY_OFFLINE=1` to disable startup network operations for a run. Auth
credentials and API keys are handled outside `settings.json`.

## Project overrides

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

# Quickstart

This page gets you from a checkout to a useful first `pipy` session.

## Install

For local development, install from this checkout with `uv`:

```bash
uv sync
uv tool install .
```

If you are working in the repository, you can also run commands without an
installed tool:

```bash
uv run pipy --help
uv run pipy
```

Published distribution packaging is still project-owned and may use a different
package name. Until an owned distribution name exists, use the checkout install
above rather than guessing a package name.

### Update

For an installed copy, `pipy update self` plans an update from the detected
install method (`uv tool`, `pipx`, `pip`, or user `pip`):

```bash
pipy update self --dry-run
pipy update self
```

Development checkouts and unknown installs are refused instead of updated
blindly. Version checks and update planning honor `PIPY_SKIP_VERSION_CHECK=1`
and `PIPY_OFFLINE=1`.

## First session

Start pipy in the project directory you want it to work on:

```bash
cd /path/to/project
pipy
```

Type a request and press Enter:

```text
Summarize this repository and tell me how to run its checks.
```

A bare positional prompt seeds the first message, so quote it as one shell
argument:

```bash
pipy "Summarize README.md"
```

Pipy defaults to a deterministic fake provider when no real provider is
selected. That is useful for smoke tests and documentation examples because it
does not contact a model provider. For real work, choose a provider/model with
`--native-provider` and `--native-model`, or use `/model` after provider setup.

## Authenticate or configure a model

Pipy supports built-in and `models.json` providers through the native provider
catalog. API-key providers usually read their standard environment variables,
for example:

```bash
export OPENAI_API_KEY=sk-...
pipy --native-provider openai --native-model <model>
```

OpenAI Codex auth is available through the product command surface:

```bash
pipy auth openai-codex login
# or inside the TUI:
/login openai-codex
```

Some provider polish is still tracked in
[Provider Catalog](provider-catalog.md): live Anthropic and GitHub Copilot login
UX, Vertex API-key auth, Anthropic adaptive-thinking request shape, and Azure
URL/api-version parity.

## Give pipy project instructions

Pipy loads instruction files into the system prompt at startup:

- a global pipy config root selected from `PIPY_CONFIG_HOME`,
  `${XDG_CONFIG_HOME}/pipy`, `~/.pipy` when present, then `~/.config/pipy`;
- `AGENTS.md`, `AGENTS.MD`, `pipy.md`, or `PIPY.md` from parent directories
  and the current directory.

Add an `AGENTS.md` file to a project to tell pipy how to work there:

```markdown
# Project Instructions

- Run `just check` after code changes.
- Do not run production migrations locally.
- Keep responses concise.
```

Restart pipy, or run `/reload`, after changing context files. Use
`--no-context-files` when you want a run without automatic instruction loading.

## Common things to try

### Reference files

Type `@` in the product TUI to pick a workspace file, or include an `@path`
reference in a prompt:

```bash
pipy "@README.md summarize this"
pipy -p "@src/app.py explain the main flow"
```

Text file references are loaded as bounded excerpts. Clipboard and drag image
references are supported in the TUI where the terminal exposes the needed data;
see [Terminal Setup](/terminal-setup/) for caveats.

### Run shell commands

In interactive mode, start a line with `!` to run a local shell command and send
its output to the model:

```text
!just test
```

Use `!!command` to run a command without adding its output to the model
context. The model-visible `bash` tool is also available during agent turns when
tools are enabled.

### Switch models and thinking level

Use `/model` to select a provider/model. `Ctrl+L` opens the same selector in the
TUI. Use `Shift+Tab` to cycle thinking level and `Ctrl+P` /
`Shift+Ctrl+P` to cycle through scoped models.

### Continue later

Native product sessions are saved automatically by default:

```bash
pipy -c                  # Continue the most recent session
pipy -r                  # Browse or continue previous sessions
pipy --name "my task"    # Set session display name at startup
pipy --session <path|id> # Open a specific session
pipy --no-session        # Ephemeral mode; do not save
```

Inside pipy, use `/resume`, `/new`, `/tree`, `/fork`, `/clone`, and `/compact`
to manage session history.

### Non-interactive mode

Use Pi-shaped one-shot and automation modes through the product command:

```bash
pipy -p "Summarize this codebase"
cat README.md | pipy -p
pipy --mode json "Summarize README.md"
pipy --mode rpc
```

`-p` reads piped stdin only when no positional prompt is provided. `--mode json`
also accepts either a quoted prompt or piped stdin, and emits a full session
event stream as JSON lines. `--mode rpc` starts a long-lived stdin/stdout JSONL
protocol.

## Where local state is written

Pipy keeps two stores with different purposes:

- the **native product session tree** stores full product transcripts under
  `~/.local/state/pipy/native-sessions/--<encoded-cwd>--/` and powers `/tree`,
  `/resume`, `/fork`, `/clone`, `/compact`, and startup session flags;
- the separate **`pipy-session` catalog** under `~/.local/state/pipy/sessions/`
  stores summary-safe learning/capture metadata and is not the product session
  source of truth.

Provider credentials and settings live in pipy-owned config/state files; do not
commit secrets or local state files to a project.

## Next steps

- [Using pipy](usage.md) — interactive mode, slash commands, sessions, context
  files, and CLI reference.
- [Terminal Setup](/terminal-setup/) and [tmux Setup](/tmux/) — terminal
  behavior, paste, scrollback, clipboard/images, and key caveats.
- [Python SDK and Headless Embedding](sdk.md) — in-process embedding.
- [Automation & RPC](automation-rpc.md) — JSON/RPC details.
- [Provider Catalog](provider-catalog.md) — provider/model setup and remaining
  provider parity work.

# Quickstart

This page gets you from a checkout to a useful first `pipy` session.

## Install

Requires Python 3.11–3.15 and [uv](https://docs.astral.sh/uv/). Clone the
repository, then install from the checkout:

```bash
git clone https://github.com/ephes/pipy.git
cd pipy
uv sync --python 3.15
uv tool install --python 3.15 .
```

If you are working in the repository, you can also run commands without an
installed tool:

```bash
uv run pipy --help
uv run pipy
```

pipy 0.3.0 is not published on PyPI or any other package index. Until an owned
distribution name exists, use the checkout install above rather than guessing a
package name.

### Update

To update a checkout install, run `git pull` and then `uv tool install .`
again; it rebuilds and replaces the installed copy.

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

Choose an **offline smoke test** or a **real provider** before sending a task.
The [concepts guide](concepts.md) explains the request/tool loop and where its
conversation is stored.

### Offline smoke test: no model contacted

This uses the deterministic fake provider, disables startup network operations,
and avoids writing a product session:

```bash
pipy --offline --native-provider fake --no-session -p "Hello"
```

Expect `SEEN:Hello`, not a model answer. This checks the local product path; it
cannot summarize a repository or demonstrate provider quality. `--offline`
disables startup network operations, while `--native-provider fake` explicitly
selects the local fake. Without a configured real provider, pipy also defaults
to that fake.

For useful model work, continue with authentication and select a real
provider/model below. This path contacts your selected model provider.

## Authenticate or configure a model

API-key providers read their standard environment variables. For example, set a
valid OpenAI API key, list the available models, then launch a selected one:

```bash
export OPENAI_API_KEY="your-api-key"
pipy --list-models openai
pipy --native-provider openai --native-model gpt-6.1-sol
```

Replace the placeholder key with your own. The model list does not run a model
turn; without configured authentication it may say no models match. Choose a
model your account supports. The example uses a current catalog row; see
[Providers and models](providers.md) for other providers and custom models.

For a ChatGPT subscription, authenticate the separate Codex provider first:

```bash
pipy auth openai-codex login
pipy --list-models openai-codex
pipy --native-provider openai-codex --native-model gpt-6.1-sol
```

Inside the TUI, `/login openai-codex` can authenticate and `/model` can switch
models. Provider transports, capabilities, and dated live evidence are described
in the [capability matrix](provider-capabilities.md); these setup instructions do
not claim a new live-provider test.

### Send your first useful request

Start the configured provider in the project you want it to work on, then type:

```text
Summarize this repository and tell me how to run its checks.
```

A positional prompt can seed the first message. For example, after the OpenAI
setup above:

```bash
cd /path/to/project
pipy --native-provider openai --native-model gpt-6.1-sol "Summarize README.md"
```

Pipy can read files and execute tools in that workspace. Product sessions save
full conversation content by default; use `--no-session` for an ephemeral run.
See [Sessions](sessions.md) for storage and resume controls.

## Give pipy project instructions

Pipy loads instruction files into the system prompt at startup:

- a global pipy config root selected from `PIPY_CONFIG_HOME`,
  `${XDG_CONFIG_HOME}/pipy`, `~/.pipy` when present, then `~/.config/pipy`;
- the first of `AGENTS.override.md`, `AGENTS.md`, `AGENTS.MD`, `CLAUDE.md`, or
  `CLAUDE.MD` from each parent directory and the current directory (Pi's
  candidate order).

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
see [Terminal Setup](terminal-setup.md) for caveats.

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

### MCP and optional codemode

Connect external tools through [MCP](mcp.md). For example, with Node.js/`npx`
installed:

```bash
pipy mcp add filesystem --exposure direct -- npx -y @modelcontextprotocol/server-filesystem .
pipy mcp list
```

Restart pipy, or run `/reload` in an existing session; `/mcp` manages connected
servers. The `direct` exposure works without the optional codemode runtime.
To let the model call tools from Python scripts, install and enable
[Python codemode](codemode.md). Image resizing/conversion is available with the
optional `images` extra.

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
- [Pipy Packages](packages.md) — install, update, filter, and share trusted
  local or managed-git extensions, skills, prompts, and themes.
- [Terminal Setup](terminal-setup.md) and [tmux Setup](tmux.md) — terminal
  behavior, paste, scrollback, clipboard/images, and key caveats.
- [Python SDK and Headless Embedding](sdk.md) — in-process embedding.
- [Automation & RPC](automation-rpc.md) — JSON/RPC details.
- [Provider Catalog](provider-catalog.md) — provider/model setup and remaining
  provider parity work.

# pipy

A native Python coding agent inspired by [Pi](https://github.com/badlogic/pi-mono).
Pipy combines an interactive terminal UI, direct model providers, coding tools,
private persistent sessions, Python extensions, MCP, and headless automation.
The product runtime is `pipy-native`.

[Quickstart](docs/quickstart.md) · [How pipy works](docs/concepts.md) ·
[User guide](docs/usage.md) ·
[Documentation](https://pipy-agent.readthedocs.io/en/latest/) · [Changelog](CHANGELOG.md) ·
[Contributing](CONTRIBUTING.md)

## Install and update

Requires **Python 3.11–3.15** and [uv](https://docs.astral.sh/uv/).
The current release is **0.3.0**; `main` also contains the changes listed under
[Unreleased](CHANGELOG.md#unreleased), including Python codemode and MCP support.
Pipy is not published on PyPI or another package index. Install from a checkout:

```sh
git clone https://github.com/ephes/pipy.git
cd pipy
uv tool install --python 3.15 .
pipy --version   # pipy 0.3.0
```

This installs `pipy` and `pipy-session` in uv's tool bin directory. Run
`uv tool update-shell` if it is not on `PATH`. To update the installed copy,
run `git pull` in the checkout and repeat `uv tool install --python 3.15 .`.
Keep any extras selected on the original install when reinstalling.
Inside the repository, `uv run pipy` works without a tool installation.

`pipy update self` and `pipy update self --dry-run` plan updates for detected
install methods. Automatic updates require a real owned distribution name;
until one exists, use the checkout update above. See
[installation and updates](docs/quickstart.md#update).

## First session

Start with an explicit local smoke test; it contacts no model and does not save
a product session:

```sh
pipy --offline --native-provider fake --no-session -p "Hello"
```

Expect `SEEN:Hello`. The fake checks the local product path; it cannot do useful
model work. [How pipy works](docs/concepts.md) explains the tool loop and the
separate product-session and workflow-archive stores.

Choose a provider and model for real work. For example, with an OpenAI API key
already set in `OPENAI_API_KEY`:

```sh
cd /path/to/project
pipy --list-models openai
pipy --native-provider openai --native-model <model-id>
```

For a ChatGPT subscription, authenticate the separate Codex provider:

```sh
pipy auth openai-codex login
pipy --list-models openai-codex
pipy --native-provider openai-codex --native-model <model-id>
```

Replace `<model-id>` with a model from the corresponding list. Without a
configured real provider, pipy defaults to a deterministic fake for offline
smoke tests. See [providers](docs/providers.md) for other providers and auth.
Provider transports use no third-party provider SDKs; the OpenAI
Codex WebSocket transport uses the declared `websockets` dependency.

Model and adapter details are documented in the
[provider catalog](docs/provider-catalog.md).

Ask pipy to inspect a repository, make a change, or run its checks. It can call
`read`, `ls`, `grep`, `find`, `write`, `edit`, and `bash`. Paths resolve relative
to the workspace or as absolute paths; `bash` runs a real shell with the
inherited environment. Output limits are documented per tool in the
[user guide](docs/usage.md). Project instructions load from `AGENTS.md` and
related context files; run `/reload` after changing them.

Useful interactive controls:

| Action | Control |
| --- | --- |
| Choose a model | `/model` or Ctrl+L |
| Change thinking level | `/thinking` or Shift+Tab |
| Expand tool output / thinking | Ctrl+O / Ctrl+T |
| Interrupt the active request | Escape or Ctrl+C |
| Resume, branch, or compact | `/resume`, `/tree`, `/fork`, `/clone`, `/compact` |
| Manage MCP servers | `/mcp` |
| Settings and keybindings | `/settings`, `/hotkeys` |

Sessions save automatically. Use `pipy -c` to continue the latest session,
`pipy -r` to browse previous sessions, or `pipy --no-session` for an ephemeral
run. See [sessions](docs/sessions.md) and [terminal setup](docs/terminal-setup.md).

## MCP and Python codemode

Connect MCP servers over stdio or streamable HTTP. Servers are configured in
`mcp.json`, with project overrides in trusted `.pipy/mcp.json` files. For example,
with Node.js and `npx` installed:

```sh
pipy mcp add filesystem --exposure direct -- npx -y @modelcontextprotocol/server-filesystem .
pipy mcp list
```

Pipy supports OAuth, tool exposure, `tool_search`, resources, reload, and
extension/SDK hooks. See the [MCP guide](docs/mcp.md) for configuration,
authentication, and supported protocol boundaries.

Optional Python codemode lets the model call tools from an isolated Python
script and return compact results. Install the extra and pinned WASI runtime,
then enable it:

```sh
# From the pipy checkout:
uv tool install --python 3.15 '.[codemode]'
pipy codemode install
pipy --tools +codemode
```

The codemode guest stays on its pinned CPython 3.14.7 runtime independently of
the host Python version. MCP's default `codemode` exposure can activate codemode
for its tools; use `direct` exposure if you want model calls without that
runtime. The optional `images` extra adds Pillow for image resizing/conversion:
`uv tool install --python 3.15 '.[codemode,images]'`.
See [codemode](docs/codemode.md) for isolation, installation, and limitations.

## Automation and embedding

```sh
pipy -p "Summarize this codebase"
cat README.md | pipy -p
pipy --mode json "Summarize README.md"
pipy --mode rpc
```

These modes use the same product session and tools. JSON and RPC carry full
conversation and tool content. See [JSON mode](docs/json.md),
[RPC mode](docs/rpc.md), and the [transport contract](docs/automation-rpc.md).

The persistent [Python SDK](docs/sdk.md) accepts an explicitly injected provider
and retains conversation history across submissions. Runnable offline examples:

```sh
uv run python docs/examples/product_session_embedding.py
uv run python docs/examples/sdk_codemode_mcp.py
```

[Extensions](docs/extension-api.md), [skills and prompts](docs/customization.md),
and [packages](docs/packages.md) customize tools and workflows. Package commands
(`pipy install`, `list`, `update`, `remove`) support local paths and managed git
sources; PyPI/npm package sources remain deferred.

## Local state

The **native product session tree** stores private full-content conversations
under `~/.local/state/pipy/native-sessions/--<encoded-cwd>--/`. It powers resume,
branching, compaction, and export; see [Session Tree](docs/session-tree.md).

The separate optional **`pipy-session` workflow archive** under
`~/.local/state/pipy/sessions/` stores summary-safe metadata for learning and
review. See [Session Storage](docs/session-storage.md) and
[workflow capture and sync](docs/workflow-capture.md) for recorder commands,
external CLI wrappers, Claude hooks, and machine-to-machine sync.
Codex, Claude Code, and Pi wrapping are capture/reference workflows.

## Development

```sh
uv sync --python 3.15
just test          # pytest
just lint          # ruff check
just format-check  # ruff format --check across the repository
just typecheck     # mypy
just check         # lint, format-check, typecheck, then test
just docs-build    # static documentation in site/
just docs-serve    # preview at http://localhost:8000
just loc           # slopscope line-count summary
```

Check formatting with `uv run ruff format --check .`; apply it with
`uv run ruff format .` (or `just format`). `just loc` uses a frozen uv overlay
with pre-release resolution; `SLOPSCOPE_SPEC` selects a package or local checkout.
CI runs full tests on Python 3.11, 3.14, and 3.15, plus real-PTY checks on Linux
and macOS and runtime-required codemode probes on Linux. The documentation is
built locally and in CI. [Read the Docs configuration](.readthedocs.yaml) uses
Zensical and locked documentation dependencies to publish
[pipy-agent.readthedocs.io](https://pipy-agent.readthedocs.io/en/latest/).
[Contributing](CONTRIBUTING.md#documentation-hosting) describes the hosted build
and setup.

The [contributor guide](CONTRIBUTING.md) covers setup, architecture gates, review,
and privacy. The [backlog](docs/backlog.md) owns current priorities and records
remaining parity differences and live-verification gaps.

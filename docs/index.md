# pipy documentation

## What pipy is

Pipy is a native Python coding agent with an interactive terminal UI, direct
provider integrations, model-driven tools, extensions, private product sessions,
and headless JSON, RPC, and Python SDK modes. Its primary runtime is native;
subprocess wrapping is limited to reference and capture workflows.

Product sessions contain private, full conversation content. The separate
metadata-only workflow archive supports summary-safe capture and learning and is
not the product session store. See [Architecture](architecture.md) for runtime
structure and ownership boundaries.

## Installation and first run

Start with the [Quickstart](quickstart.md) to install pipy from a checkout,
configure a provider, launch the interactive product, and learn where local state
is stored. For terminal-specific preparation, see
[Terminal Setup](terminal-setup.md) and [tmux Setup](tmux.md).

## User guides

- [Using pipy](usage.md) covers interactive mode, slash commands, context files,
  sessions, and the CLI.
- [Providers and models](providers.md), [Settings](settings.md), and
  [Keybindings](keybindings.md) cover model selection and product configuration.
- [Customization](customization.md) and [Packages](packages.md) cover skills,
  prompts, themes, extensions, and trusted resource packages.
- [Sessions](sessions.md) and [Compaction](compaction.md) cover resume, fork,
  clone, tree, and long-context workflows.
- [JSON Mode](json.md), [RPC Mode](rpc.md), and the
  [Python SDK](sdk.md) cover automation and embedding.
- [Session Storage](session-storage.md) documents the separate metadata-only
  workflow catalog.

## Behavioral contracts

These documents define the behavior and parity boundaries for the major product
surfaces:

1. [Session Tree](session-tree.md)
2. [Extension API](extension-api.md)
3. [Provider Catalog](provider-catalog.md)
4. [Settings & Config](settings-config.md)
5. [Automation & RPC](automation-rpc.md)
6. [TUI Workflow](tui-workflow.md)
7. [Export & Distribution](export-distribution.md)

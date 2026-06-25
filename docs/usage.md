# Using pipy

This page collects day-to-day product usage details that do not fit on the
quickstart page.

## Interactive mode

Run `pipy` in a project directory to start the product TUI. A quoted positional
prompt seeds the first message:

```bash
pipy
pipy "explain this repository"
```

The interface has four main areas:

- **Startup header** — orientation, loaded context/resource labels, skills, and
  extension/package resources.
- **Messages** — user messages, assistant responses, tool calls/results,
  notifications, errors, and extension-rendered rows.
- **Editor** — where you type; long input soft-wraps and the footer stays
  pinned.
- **Footer/status** — working directory/session/model/status, usage counters,
  context, and current thinking level.

The editor can be replaced temporarily by built-in overlays such as `/settings`,
`/model`, `/hotkeys`, `/resume`, and `/tree`, or by extension UI helpers.

### Editor features

| Feature | How |
| --- | --- |
| File reference | Type `@` to pick a project file, or type an `@path` reference |
| Path completion | Press Tab where path completion is active |
| Multi-line input | Shift+Enter where the terminal reports it; Alt+Enter is pipy's portable fallback |
| Images | Paste with Ctrl+V, Alt+V on Windows, or drag into supported terminals |
| Shell command | `!command` runs and sends output to the model |
| Hidden shell command | `!!command` runs without sending output to the model |
| External editor | Ctrl+G opens `$VISUAL` or `$EDITOR` through a temp file |
| Prompt history | Up/Down recalls in-memory history; optional persistent history is controlled in `/settings` |
| Undo/redo | Ctrl+Z / Ctrl+Y in the TUI editor |

See `/hotkeys` for the live keybinding view. The maintainer-level details live
in [TUI Workflow](tui-workflow.md) and [Settings & Config](settings-config.md).

## Slash commands

Type `/` in the editor to open command completion. Extensions can register
custom commands, skills are available through `/skill`, and prompt templates are
invoked as their own `/<template-name>` commands.

| Command | Description |
| --- | --- |
| `/login`, `/logout` | Manage supported provider credentials |
| `/model` | Switch provider/model interactively or with `/model provider/model` |
| `/scoped-models` | View or change the Ctrl+P model cycle set |
| `/settings` | Open interactive settings/status controls |
| `/resume` | Pick or continue a previous native product session |
| `/new` | Start a new session |
| `/name <name>` | Set the session display name |
| `/session` | Show session file, ID, message count, tokens, and cost/status details |
| `/tree` | Browse the native session tree and continue from a selected point |
| `/fork` | Create a new session from a previous session/message |
| `/clone` | Duplicate the current active branch into a new session |
| `/compact [prompt]` | Compact context, optionally with custom instructions |
| `/copy` | Copy the last assistant message to the clipboard where supported |
| `/export [file]` | Export the current session to HTML, or active-branch JSONL when requested |
| `/import <file>` | Import a native session JSONL file |
| `/share` | Upload the session as a private GitHub gist when configured |
| `/reload` | Reload settings, keybindings, extensions, skills, prompts, themes, and context files |
| `/hotkeys` | Show keyboard shortcuts |
| `/changelog` | Display version history |
| `/skill` | List/load discovered skills |
| `/quit`, `/exit` | Quit pipy |

Pipy intentionally removed earlier pipy-only command wrappers such as `/clear`,
`/status`, `/help`, `/theme`, and `/template`; use `/new`, `/session`,
`/hotkeys`, `/settings`, and template-specific slash commands instead.

## Message queue and cancellation

During an active turn, pipy supports Pi-style steering and follow-up delivery:

- **Enter** queues a steering message, delivered after the current assistant
  turn finishes its active tool work.
- **Alt+Enter** queues a follow-up message for after the agent finishes all work.
- **Escape** or **Ctrl+C** aborts the active provider request and restores queued
  text to the editor.
- **Alt+Up** retrieves queued messages back to the editor.

The cancellation path closes the in-flight provider connection where supported,
so late chunks do not mutate session/context state.

## Sessions

Native product sessions are saved automatically under
`~/.local/state/pipy/native-sessions/`, organized by encoded working directory.

```bash
pipy -c                  # Continue the most recent session
pipy -r                  # Browse/select in a TTY; continue most recent outside a TTY
pipy --no-session        # Ephemeral mode; do not save
pipy --name "my task"    # Set session display name at startup
pipy --session <path|id> # Use a specific session file or partial UUID
pipy --fork <path|id>    # Fork a session file or partial UUID into a new session
pipy --session-dir DIR   # Use a custom native session store root
pipy --session-id ID     # Open or create a session with an exact ID
```

Useful session commands:

- `/session` shows the current session file and ID.
- `/tree` navigates the in-file session tree and can summarize abandoned
  branches.
- `/fork` creates a new session from an earlier user message.
- `/clone` duplicates the current active branch into a new session file.
- `/compact` summarizes older messages to free context.

The separate `pipy-session` command family is a summary-safe catalog/learning
surface, not the product session source of truth.

## Context and system prompt files

Pipy loads `AGENTS.md`, `AGENTS.MD`, `pipy.md`, or `PIPY.md` at startup from
parent directories and the current directory, plus the first matching file in
the global pipy config root (`PIPY_CONFIG_HOME`, `${XDG_CONFIG_HOME}/pipy`,
`~/.pipy`, then `~/.config/pipy`). Use context files for project conventions,
commands, safety rules, and preferences. Disable loading with
`--no-context-files` or `-nc`.

Replace or append to the default system prompt with CLI flags:

```bash
pipy --system-prompt ./SYSTEM.md
pipy --append-system-prompt ./APPEND_SYSTEM.md
```

Pipy also auto-discovers `.pipy/SYSTEM.md` and `.pipy/APPEND_SYSTEM.md`, then
matching config-directory files, when the flags are omitted.

## Exporting and sharing sessions

Use `/export [file]` to write the current session to HTML. Top-level
`--export` exports an existing native session JSONL file and exits:

```bash
pipy --export ~/.local/state/pipy/native-sessions/--project--/session.jsonl out.html
```

Use `/share` to upload a private GitHub gist with a shareable HTML link when the
required GitHub authentication is configured. Export/share are full-content
product surfaces; review files before publishing them.

## CLI reference

```bash
pipy [options] [prompt]
pipy <command> ...
```

Without a recognized subcommand or top-level flag, pipy launches the interactive
session. A prompt is a single shell token, so quote it.

### Package commands

```bash
pipy install <source> [-l]
pipy remove <source> [-l]
pipy uninstall <source> [-l]
pipy update [source]
pipy update --extensions
pipy update self [--dry-run]
pipy list
pipy config
```

Local paths and managed git sources are supported. PyPI/npm package sources are
deferred pending supply-chain policy.

### Modes

| Flag | Description |
| --- | --- |
| default | Interactive product TUI/text mode |
| `-p`, `--print` | Run one non-interactive turn and print final assistant text |
| `--mode json` | Emit a full Pi-shaped session event stream as JSON lines |
| `--mode rpc` | Start the long-lived stdin/stdout JSONL RPC protocol |
| `--export <in> [out]` | Export a native session JSONL file to HTML and exit |

In print mode, piped stdin is merged into the initial prompt:

```bash
cat README.md | pipy -p "Summarize this text"
```

### Model options

| Option | Description |
| --- | --- |
| `--native-provider <name>` | Built-in or custom `models.json` provider |
| `--native-model <id>` | Provider model identifier |
| `--list-models [search]` | List available provider/models and exit |
| `--thinking <level>` | Store accepted thinking level (`off|minimal|low|medium|high|xhigh`) |
| `--models <patterns>` | Comma-separated model patterns for `/scoped-models` and Ctrl+P cycling |
| `--api-key <key>` | Runtime API-key override, kept out of archives; auth wiring is provider-dependent |

`--thinking`, per-pattern thinking suffixes in `--models`, and some auth flows
still have provider-specific follow-up work tracked in
[Provider Catalog](provider-catalog.md).

### Session options

| Option | Description |
| --- | --- |
| `-c`, `--continue` | Continue the most recent native product session |
| `-r`, `--resume-session` | Browse/select in a TTY; continue most recent outside a TTY |
| `--session <path|id>` | Use a specific native session file or partial UUID |
| `--fork <path|id>` | Fork a session file or partial UUID into a new session |
| `--session-id <id>` | Open or create a session with an exact ID |
| `--session-dir <dir>` | Custom native session store root |
| `--no-session` | Ephemeral mode; do not save |
| `--name <name>`, `-n <name>` | Set session display name at startup |

### Tool options

| Option | Description |
| --- | --- |
| `--tools <list>`, `-t <list>` | Allowlist specific built-in, extension, and custom tools |
| `--exclude-tools <list>`, `-xt <list>` | Disable specific built-in, extension, and custom tools |
| `--no-builtin-tools`, `-nbt` | Disable built-in tools but keep extension/custom tools enabled |
| `--no-tools`, `-nt` | Disable all tools |

Built-in tools include `read`, `bash`, `edit`, `write`, `grep`, `find`, and
`ls`.

### Resource options

| Option | Description |
| --- | --- |
| `--extension PATH`, `-e PATH` | Load an extension file or directory for this run |
| `--no-extensions`, `-ne` | Disable default extension discovery; explicit `--extension` still loads |
| `--skill PATH` | Load a skill Markdown file or directory for this run |
| `--no-skills`, `-ns` | Disable default skill discovery; explicit `--skill` still loads |
| `--prompt-template PATH` | Load a prompt-template Markdown file or directory |
| `--no-prompt-templates`, `-np` | Disable default prompt-template discovery; explicit templates still load |
| `--theme PATH` | Load a theme TOML file or directory so it can be selected |
| `--no-themes` | Disable package theme discovery; explicit and built-in themes remain available |
| `--read-root PATH` | Add a read-only root for read/ls/grep/find absolute paths |

`--read-root` is a pipy-owned harness convenience; mutation tools always stay
inside the workspace.

### Other useful options

| Option | Description |
| --- | --- |
| `--cwd DIR` | Working directory for the native provider |
| `--tool-budget N` | Per-turn model tool invocation budget |
| `--input-runtime auto|plain|prompt-toolkit|readline|slash-menu` | Input adapter selection |
| `--root DIR` | Metadata catalog root for `pipy-session` records |
| `--slug NAME` | Short run label for metadata/catalog records |
| `--goal TEXT` | Optional short goal for metadata/catalog records |
| `--version`, `-v` | Print the pipy version and exit |

Some of these options are pipy harness controls rather than Pi product flags;
they remain documented here because they are exposed on the current CLI.

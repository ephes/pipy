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

The context percentage estimates the active branch: the last successful assistant
with positive stored usage supplies the anchor, then each trailing message adds
its characters divided by four, rounded up separately. Failed and aborted usage
never anchors it. Resume and branch changes restore the branch's history; changing
models changes the context window denominator. Without an anchor, pipy estimates
all messages and the current system prompt. A compacted context estimates all
retained messages, system state and the summary suffix once while that suffix
remains, including after another successful response. Pi instead reports unknown
just after compaction until a valid new response. This conservative estimate is
not a guarantee that a provider accepts the request. Session token/cost totals
still include stored assistants across all branches.


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
custom commands, each skill is a `/skill:<name>` command, and prompt templates
are invoked as their own `/<template-name>` commands.

| Command | Description |
| --- | --- |
| `/login`, `/logout` | Manage supported provider credentials |
| `/model` | Switch provider/model interactively or with `/model provider/model` |
| `/thinking [level]` | Set the thinking level for this session; bare `/thinking` opens a selector of the model's levels (Ctrl+S there also saves it as the default) |
| `/scoped-models` | View or change the Ctrl+P model cycle set |
| `/settings` | Open interactive settings/status controls |
| `/resume` | Pick or continue a previous native product session |
| `/new` | Start a new session |
| `/name <name>` | Set the session display name |
| `/session` | Show session name, ID, leaf, message and branch counts, and file (Pi's token and cost breakdown is not ported yet; the footer shows the cost) |
| `/tree` | Browse the native session tree and continue from a selected point |
| `/fork` | Create a new session from a previous session/message |
| `/clone` | Duplicate the current active branch into a new session |
| `/compact` | Compact context when enough history exists |
| `/copy` | Copy the last assistant message to the clipboard where supported |
| `/export [file]` | Export the current session to HTML by default, or active-branch JSONL when `file` ends in `.jsonl` |
| `/import <file>` | Import a native session JSONL file |
| `/share` | Upload the session as a private GitHub gist when configured |
| `/reload` | Reload settings, keybindings, extensions, skills, prompts, themes, and context files, and start the [MCP servers](mcp.md#reloading) again from `mcp.json` |
| `/mcp`, `/mcp login [server]`, `/mcp logout [server]`, `/mcp reconnect [server]` | Open the [MCP manager](mcp.md#the-mcp-manager) to see the servers and sign in, reconnect, enable, disable or change the exposure of one; the subcommands sign in to, sign out of or reconnect a server directly (not available with MCP turned off); see [MCP](mcp.md#the-mcp-command) |
| `/hotkeys` | Show keyboard shortcuts |
| `/changelog` | Display version history |
| `/skill:<name> [args]` | Run a discovered skill (the text after the name follows it) |
| `/quit`, `/exit` | Quit pipy |

Pipy intentionally removed earlier pipy-only command wrappers such as `/clear`,
`/status`, `/help`, `/theme`, and `/template`; use `/new`, `/session`,
`/hotkeys`, `/settings`, and template-specific slash commands instead.

Missing, unreadable or invalid UTF-8 changelog resources do not block startup.
`/changelog` reports no entries when release notes are unavailable.

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

A stopped provider answer keeps its partial output and ends with
`Operation aborted` for cancellation (including steering and local-command
interruptions), or `Error: …` for a provider failure. The same marker appears on
resume for answers without tool calls. Stopped partial tool calls appear as failed rows in both the live display and
on resume. These calls were not executed; the rows show the stopping reason. Retry attempts
retain their existing error/countdown notices; a terminal
stopped answer gets one marker, and the next prompt remains usable. An interruption
during tool execution records the settled results followed by an empty aborted
answer. Stopped call arguments preserve recoverable streamed JSON values; invalid
or non-finite input becomes `{}`. Old saved entries are unchanged. Ordinary
requests skip stopped answers; private compaction and branch summaries may quote
their unfinished partial text as data. Attempted-file metadata may include
partial paths from unexecuted stopped intents, labelled as such.

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

Pipy loads context files at startup the way Pi does. In each directory it takes
the first file that exists from `AGENTS.override.md`, `AGENTS.md`, `AGENTS.MD`,
`CLAUDE.md`, then `CLAUDE.MD`. It reads the global pipy config root first
(`PIPY_CONFIG_HOME`, `${XDG_CONFIG_HOME}/pipy`, `~/.pipy`, then
`~/.config/pipy`). Then it reads every parent directory from the filesystem root
down, and the current directory last, so more specific files come later. A file
reached twice is loaded once.

Inside a linked git worktree that sits inside its main checkout (for example
`.claude/worktrees/<name>`), the main checkout's context file with the same name
as the worktree's own file is skipped, so the repository's instructions are not
applied twice.

The files go into the system prompt as Pi's `<project_context>` section: one
`<project_instructions path="...">` block per file. Context files are loaded
whether or not the project is trusted, and a symlinked context file is followed
wherever it points, like Pi. Treat them as untrusted input.

`/reload` re-reads context files and system-prompt replace/append inputs before
the next submission, including file paths supplied by explicit flags. Deleted
auto-discovery files fall back to the next discovery location or the default
prompt; deleted context files drop out. Explicit flags retain their startup
precedence and text-or-file resolution rules. Project `SYSTEM.md` and
`APPEND_SYSTEM.md` remain trust-filtered; context files still load regardless
of trust, and `--no-context-files` remains in effect. Conversation history is
preserved. Caller-supplied fixed prompts/templates without a file loader stay
unchanged. Invalid UTF-8 in replace/append inputs or an instruction-loader
read error keeps the previous prompt inputs and reports a session diagnostic;
other resources still reload. Context-file decoding retains its existing
replacement-character behavior.
The metadata archive records startup input hashes, not subsequent reloads.
A deleted explicit file input or an existing unreadable replace/append input
follows the normal text-or-file rule: its path value becomes literal text
(with a session warning for unreadable files).

Use context files for project conventions, commands, safety rules, and
preferences. Disable loading with `--no-context-files` or `-nc`.

The default system prompt is Pi's, with pipy's name: a short preamble, the
active tools with one-line descriptions (`<tools>`), the rules those tools add
(`<rules>`), where pipy's own docs live (`<docs>`), then the appended prompt,
context files, skills and working directory. The tool and rule sections follow
the tools active for each prompt.

Replace or append to the default system prompt with CLI flags:

```bash
pipy --system-prompt ./SYSTEM.md
pipy --append-system-prompt ./APPEND_SYSTEM.md
```

A replaced prompt drops the tool, rule and docs sections, as in Pi.

Pipy also auto-discovers `.pipy/SYSTEM.md` and `.pipy/APPEND_SYSTEM.md`, then
matching config-directory files, when the flags are omitted.

## Exporting and sharing sessions

Use `/export` to write the current session to the default HTML export path, or
`/export <file>` to choose an output path. The in-session dispatcher treats a
`.jsonl` output suffix as an active-branch JSONL export; other suffixes export
HTML. Top-level `--export` exports an existing native session JSONL file and
exits:

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
deferred pending supply-chain policy. See [Pipy Packages](packages.md) for
source syntax, security notes, package layout, filtering, and update behavior.

### MCP commands

```bash
pipy mcp add <server> [options] -- <command> [args...]
pipy mcp add <server> [options] --url <url>
pipy mcp remove <server> [-l]
pipy mcp list [--json]
pipy mcp login <server> [--timeout <seconds>]
pipy mcp logout <server>
```

`add` and `remove` change `mcp.json` in the config home, or `.pipy/mcp.json`
of the current directory with `-l`. `list` connects to every enabled server,
prints its state, tools and errors, and exits with status 1 when an entry is
invalid or a server is not connected. The commands start no session and load
no extensions. `pipy mcp --help` lists the options of `add`; see
[MCP](mcp.md#the-pipy-mcp-commands) for the details. `pipy mcp login <server>`
signs in to an HTTP server that uses OAuth, through your browser, and `pipy
mcp logout <server>` deletes the stored credentials; a running session uses
a new sign-in from its next turn on. In a session, [`/mcp`](mcp.md#the-mcp-command)
opens a manager for the same servers: it reconnects, enables or disables
one, changes its exposure and signs in to or out of one.

### Modes

| Flag | Description |
| --- | --- |
| default | Interactive product TUI/text mode |
| `-p`, `--print` | Run one non-interactive turn and print final assistant text |
| `--mode json` | Emit a full Pi-shaped session event stream as JSON lines |
| `--mode rpc` | Start the long-lived stdin/stdout JSONL RPC protocol |
| `--export <in> [out]` | Export a native session JSONL file to HTML and exit |

In print mode, piped stdin is used as the prompt when no positional prompt is
provided. If you pass a quoted prompt, pipy uses that prompt and does not append
stdin to it:

```bash
cat README.md | pipy -p
```

The prompt of `--print`, `--mode json` and RPC mode is not a typed line. A
prompt that names an extension command (`pipy -p /mcp`) runs it without
asking the model, skills and prompt templates are expanded, and everything
else, the built-in slash commands included, goes to the model. See
[Commands in a prompt](automation-rpc.md#commands-in-a-prompt).

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
| `--verbose` | Force startup/resource chrome for this run, even when `quietStartup` is enabled |
| `--offline` | Disable startup network operations for this run (`PIPY_OFFLINE=1` and `PIPY_SKIP_VERSION_CHECK=1`) |
| `--approve`, `-a` | Trust project settings/resources for this run only; does not persist |
| `--no-approve`, `-na` | Block project settings/resources for this run only; does not persist |

When both trust flags are repeated, the last one wins. Saved closest-ancestor
decisions live in the global config root's `trust.json`; global
`defaultProjectTrust` controls the `ask|always|never` fallback. See
[Settings](settings.md). Interactive `ask` opens the trust selector; `/trust`
saves a current/parent decision for the next restart. Trust gates project inputs
but is not a sandbox. Global and explicit CLI extensions may decide unresolved
trust before saved/default/UI fallback; project extensions remain gated.

### Tool options

| Option | Description |
| --- | --- |
| `--tools <list>`, `-t <list>` | Allowlist built-in, extension, and custom tools by name or pattern, or apply exact `+name`/`-name` modifiers to `defaultTools`. MCP tools are kept unless an entry starts with `mcp__` (see [MCP tools](#mcp-tools)) |
| `--exclude-tools <list>`, `-xt <list>` | Disable tool names or patterns after all other selection options, MCP tools included |
| `--no-builtin-tools`, `-nbt` | Start with the built-in tools disabled but keep extension/custom tools enabled |
| `--no-tools`, `-nt` | Start with all built-in, extension, custom and MCP tools disabled |

`--no-tools` and `--no-builtin-tools` empty the default selection, as in Pi;
they do not veto what `--tools` selects. A plain `--tools` list replaces the
selection whatever they say, so `pipy --no-tools --tools tool_search` and
`pipy --no-builtin-tools --tools tool_search` declare `tool_search`. `+name`
entries start from the empty selection: `pipy -nbt --tools +read` activates
`read` and keeps the extension tools, and with `--no-tools` the tools they add
are the only ones. An empty list, `--tools ''`, disables every tool, MCP tools
included, like `--no-tools`. Under `--no-builtin-tools` the built-in tools stay
registered without being active, so an extension can still select one, and the
`defaultTools` setting is not read.

Built-in tools are `read`, `ls`, `grep`, `find`, `write`, `edit`, and `bash`,
in that order. Read excerpts, shell output, and provider-visible tool results
are bounded automatically. Optional `codemode` is off by default; enable with
`--tools +codemode` or `"defaultTools": ["+codemode"]` after explicit
[runtime installation](codemode.md#installing). Modifiers use exact names and
cannot be mixed with a plain allowlist. `pipy --tools read,codemode` overrides
the settings selection; `pipy --tools +codemode,-read` modifies it. Unavailable
selected codemode is omitted with one TUI notice or headless stderr warning;
remaining tools continue with no automatic install/fallback. See [default selection](settings-config.md#default-tool-selection).

An MCP server whose tools scripts call turns `codemode` on as well, and one
with `deferred` tools turns `tool_search` on, see
[control tool exposure](mcp.md#control-tool-exposure).

Optional `tool_search` is off by default as well; enable it with
`--tools +tool_search` or `"defaultTools": ["+tool_search"]`. It ranks the
registered tools that are not declared to the model (exposure `codemode` or
`deferred`) for a query and declares the matches from the next model request
on. Loaded tools are recorded in the session like other tool changes, so they
stay declared on that branch. See [tool search](mcp.md#tool-search).

`codemode` and `tool_search` come from the built-in extensions
`builtin:codemode` and `builtin:tool-search`. `--no-extensions` or a
`-builtin:<name>` entry in the `extensions` setting removes them; see
[turning MCP off](mcp.md#turning-mcp-off).

Entries of `--tools` and `--exclude-tools` are tool names or patterns in which
`*` matches any characters: `--exclude-tools 'ext_*'` disables every tool
whose name starts with `ext_`. Nothing else is special in a pattern, and it
has to match the whole name. Quote a pattern so that the shell does not expand
it. `+name` and `-name` entries take exact names, not patterns; use
`--exclude-tools` to disable tools by pattern. A name that no tool has is
ignored, as in Pi; pipy names it in a notice at startup (`pipy: unknown tool
name(s): ...`). A pattern that matches nothing gets no notice, and neither
does the name of an MCP tool, because a server's tools are registered when it
connects.

#### MCP tools

MCP tools are the tools of MCP servers, named `mcp__<server>__<tool>`, and the
three resource tools `list_mcp_resources`, `list_mcp_resource_templates` and
`read_mcp_resource`, which exist when a server offers
[resources](mcp.md#use-resources). pipy connects to the stdio and HTTP
servers of `mcp.json`, see [MCP](mcp.md#quick-setup).

`--tools` selects the tools declared to the model. It does not remove MCP
tools, whose reach is set by their exposure: `pipy --tools read,codemode`
keeps every MCP tool callable from codemode scripts. An MCP tool that no entry
names or matches is never declared directly, whatever its exposure, also when
an extension activates it or `/tree` restores it; only `tool_search`, if
listed, can load it, and only when its exposure is not `direct`. Once an entry
starts with `mcp__`, `--tools` filters MCP tools too, so this keeps only the
tools of the `radius` server:

```bash
pipy --tools read,bash,codemode,'mcp__radius__*'
```

To remove MCP tools, use `--exclude-tools 'mcp__*'` (the server tools) or name
the tools; the resource tools have no `mcp__` prefix and must be named. `--no-tools` removes them as well. [`--no-mcp`](#resource-options)
turns MCP off altogether.

### Resource options

| Option | Description |
| --- | --- |
| `--extension PATH`, `-e PATH` | Load an extension file or directory for this run, or a built-in extension: `builtin:mcp`, `builtin:codemode` or `builtin:tool-search` |
| `--no-extensions`, `-ne` | Disable default extension discovery and the built-in extensions (MCP, `codemode`, `tool_search`); explicit `--extension` still loads, so `pipy -ne -e builtin:mcp` keeps MCP |
| `--no-mcp` | Disable the built-in MCP support for this run: no `mcp.json` is read, no server connects and there are no MCP tools. See [turning MCP off](mcp.md#turning-mcp-off) |
| `--skill PATH` | Load a skill Markdown file or directory for this run |
| `--no-skills`, `-ns` | Disable default skill discovery; explicit `--skill` still loads |
| `--prompt-template PATH` | Load a prompt-template Markdown file or directory |
| `--no-prompt-templates`, `-np` | Disable default prompt-template discovery; explicit templates still load |
| `--theme PATH` | Load a theme TOML file or directory so it can be selected |
| `--no-themes` | Disable package theme discovery; explicit and built-in themes remain available |

`read`, `ls`, `grep`, `find`, `write` and `edit` take any path, relative to
`--cwd` or absolute, like Pi's tools.

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

## Optional codemode runtime management

`pipy codemode install [--from-file PATH] [--force]` installs the pinned
CPython-on-WASI runtime and runs its availability self-test. Local archives
must match the pinned provenance. `pipy codemode status` checks availability
without downloading or installing a runtime; both commands work without
provider authentication, a session or project trust. Status returns 0 when
available and 1 otherwise; install failures return 1 and argument errors 2.
See [Python codemode](codemode.md) for dependencies and installation details.
Enable the available builtin with `--tools +codemode` or settings; see
[enabling codemode](codemode.md#enabling).

During compaction or branch summarization, a dedicated "Retrying summarization"
countdown replaces the working loader. Actual retry admission restores the
compaction loader or "Summarizing branch" loader. Escape cancels through the
existing auxiliary operation; failed retries show only generic status and do not
add ordinary assistant Error rows. Summary content and usage remain private.

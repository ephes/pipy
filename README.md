# pipy

Python slop fork experiments for a coding-agent harness inspired by Pi and
clean architecture.

Pipy's product runtime is `pipy-native`: a Python runtime that owns provider
access, tool boundaries, session semantics, and privacy-conscious archive
metadata. Mentions of Codex, Claude Code, or Pi below refer to metadata
capture or subprocess wrapping for external tools — they are not supported
product runtime backends.

For the design rationale, runtime diagrams, archive layout, and parity
status, start at [`docs/index.md`](docs/index.md).

## Headless Python Embedding

Pipy is intended to be usable without its CLI/TUI from Python programs that need
agentic workflow support. The in-process surface is `pipy_harness.sdk`; it lets
callers build a native run request, inject providers or stream sinks, execute one
`pipy-native` turn, and receive a `RunResult` as a Python object:

```python
from pathlib import Path

from pipy_harness.sdk import make_native_run_request, run_native

request = make_native_run_request(
    goal="Summarize the current repository state",
    cwd=Path.cwd(),
)
result = run_native(request)
```

By default this example uses the deterministic fake provider, so it is safe for
smoke tests and does not contact a model provider. The stable SDK is
intentionally narrow today. Richer multi-turn/session-control embedding is a
design goal; Pi-style JSON/RPC automation is specified separately for
out-of-process callers. See [`docs/sdk.md`](docs/sdk.md) and
[`docs/automation-rpc.md`](docs/automation-rpc.md).

## Development Setup

Install the Python tooling with `uv`:

```sh
uv sync
```

Run local verification through `just`:

```sh
just test       # pytest
just lint       # ruff check
just typecheck  # mypy
just check      # lint, typecheck, then test
just loc        # slopscope line-count summary
```

During the `slopscope` pre-release phase, `just loc` runs a sibling checkout
with `uv run --with-editable`. Set `SLOPSCOPE_PATH` when that checkout lives
somewhere else.

## Documentation Site

Preview the Markdown docs locally with Zensical:

```sh
just docs-serve              # http://localhost:8000 by default
just docs-serve localhost:8001
just docs-build              # static output in site/
```

## Session Sync Setup

Copy the direnv example and approve it:

```sh
cp .envrc.example .envrc
direnv allow
just sessions-init
```

Sync finalized session records with the paired Tailscale machine:

```sh
just sessions-sync
```

On `studio`, the default remote is `atlas.tailde2ec.ts.net`. On `atlas`, the
default remote is `studio.tailde2ec.ts.net`.

Pipy keeps two local stores by design. The **native product session tree** is
the product session source of truth for `pipy repl --agent pipy-native`: a
private append-only JSONL conversation tree under
`~/.local/state/pipy/native-sessions/--<encoded-cwd>--/`, like Pi's own session
files. It powers in-place `/tree` navigation, sibling branches, `/session`,
`/name`, `/new`, `/resume`, `/fork`, `/clone`, durable `/compact`, branch
summaries, and the startup flags `-c`/`-r`/`--session`/`--fork`/`--no-session`.
The separate **`pipy-session` metadata archive** (active records in
`.in-progress/pipy/`, finalized records in `pipy/YYYY/MM/` under
`~/.local/state/pipy/sessions/`) is a summary-safe catalog/learning surface and
is **not** the product session source. See
[`docs/session-tree.md`](docs/session-tree.md) and
[`docs/session-storage.md`](docs/session-storage.md) for the full lifecycle, and
`scripts/parity_checks/session_tree_conformance.py --json` for the conformance
gate.

## Pipy Run Harness

`pipy run --agent pipy-native` runs one minimal native turn through a direct
provider boundary while pipy records conservative lifecycle metadata into the
session archive:

```sh
uv run pipy
uv run pipy repl
uv run pipy run --agent pipy-native --slug native-smoke --goal "Native bootstrap smoke"
uv run pipy run --agent pipy-native --native-output json --slug native-json --goal "Native JSON smoke"
uv run pipy run --agent pipy-native --native-provider openai --native-model <model> --slug openai-smoke --goal "Say hello briefly"
uv run pipy auth openai-codex login
uv run pipy run --agent pipy-native --native-provider openai-codex --native-model <model> --slug codex-smoke --goal "Say hello briefly"
```

The same command can also wrap an arbitrary subprocess for conservative
lifecycle capture. This is a foundation and smoke-test path, not the
long-term agent runtime:

```sh
uv run pipy run --agent custom --slug smoke -- echo hello
uv run pipy run --agent codex --slug codex-capture --cwd . -- codex exec "..."
```

For subprocess capture runs, the harness streams child stdout and stderr to
the caller, finalizes the pipy record, and returns the child exit code. Pipy
does not own the subprocess prompt stack, model calls, tool behavior,
approval model, or transcript format.

### Headless automation (Pi `--mode json` / `--mode rpc` / `--print`)

The product tool loop exposes Pi-compatible headless automation surfaces on
`pipy repl`. These are **full-content** transports (assistant text, tool
arguments/results, and bash output are emitted like Pi; only auth
secrets/tokens are never emitted), independent of the metadata-first
`pipy-session` archive. See [`docs/automation-rpc.md`](docs/automation-rpc.md).

```sh
# Full Pi-shaped session event stream as LF-only JSONL on stdout (one object
# per line): a native session header, then agent/turn/message/tool events.
uv run pipy repl --mode json "Summarize README.md"

# One-shot: print only the final assistant text (Pi -p). Failures -> stderr.
uv run pipy repl --print "What is 2 + 2?"

# Long-lived stdin/stdout JSONL RPC protocol (Pi command vocabulary): send one
# JSON command per line on stdin; responses (correlated by `id`) and async
# session events are emitted on stdout.
uv run pipy repl --mode rpc
```

Mode selection follows Pi's `resolveAppMode` precedence (`--mode rpc` >
`--mode json` > `--print` > interactive); pipy keeps piped (non-TTY) stdin as
interactive REPL input, so one-shot/RPC modes are selected explicitly via
`--mode json|rpc` or `--print`/`-p` (a positional prompt without one of those is
rejected rather than silently switching modes). The conformance gate is
`uv run python scripts/parity_checks/automation_rpc_conformance.py --json`.

### Flags

Required:

- `--agent <name>`: `pipy-native` for the product runtime; `custom`, `codex`,
  `claude`, `pi` are subprocess/capture labels only.
- `--slug <slug>`: short label used in the session filename.
- `--goal <text>`: short goal, required for `--agent pipy-native`. Treat as
  user-visible archive metadata; do not paste prompts, secrets, credentials,
  or sensitive personal data.
- command after `--`: subprocess command, required except for
  `--agent pipy-native`.

Optional:

- `--cwd <path>`: child process working directory.
- `--root <path>`: session root override (matches `pipy-session --root`).
- `--record-files`: after the child exits, record changed git file paths
  only. Without it, changed paths are not recorded.
- `--native-provider <id>`: native provider for `--agent pipy-native`,
  defaulting to `fake`. Supported ids: `fake`, `ds4`, `openai`,
  `openai-codex`, `openrouter`, `anthropic`, `google`, `google-vertex`,
  `mistral`, `amazon-bedrock`, `azure-openai`, `cloudflare`,
  `openai-completions`. All real providers are stdlib-only (no third-party SDK
  dependencies).
- `--native-model <id>`: model label for the native provider. Most real
  providers require it in one-shot `pipy run`; `ds4` defaults to
  `deepseek-v4-flash`.
- `--native-output json`: **deprecated** for `--agent pipy-native`; emits a
  single metadata-only JSON status object (record paths, counters) instead of
  provider final text. It is not a Pi-style event stream and has no Pi
  equivalent — use `pipy repl --mode json "<prompt>"` for the full Pi-shaped
  session event stream (see "Headless automation" below and
  [`docs/automation-rpc.md`](docs/automation-rpc.md)).
- `--list-models [search]`: print the table of available provider/models
  (provider, model, context, max-out, thinking, images), optionally fuzzy-
  filtered over `provider id`, then exit without running a provider turn. Reads
  the same catalog as the `/model` selector. Column shape matches Pi's
  `pi --list-models`.
- `--thinking <level>` / `--api-key <key>`: session thinking level and a runtime
  API-key override. For the OpenAI-Chat-Completions API family (custom
  models.json providers, ds4, OpenRouter, openai-completions) these reach the
  real request — mapped thinking as `reasoning_effort`/`reasoning.effort` and the
  key as the `Authorization` header (highest auth priority). Non-completions
  families are still tracked in `docs/provider-catalog.md`.
- `--models <patterns>`: Pi-style scoped-model patterns. In `pipy repl` these
  apply as a final CLI override of `enabledModels` (CLI wins over
  `settings.json`), constraining the `/scoped-models` set and live Ctrl+P
  cycling for the session. A colon inside a model id is preserved; only a
  trailing known `:level` thinking suffix is stripped, and that per-pattern
  initial-level preference is not yet applied (tracked in
  `docs/provider-catalog.md`).

Provider/model catalog (`docs/provider-catalog.md`):

- The catalog is the pipy-owned analogue of Pi's `ModelRegistry`: a built-in
  table with multiple rows per provider, plus a `models.json` custom-provider /
  override layer loaded from `<config>/models.json` (`PIPY_CONFIG_HOME`, else
  `${XDG_CONFIG_HOME}/pipy`, else `~/.config/pipy`). The foundation supports
  comment/trailing-comma stripping, provider/per-model overrides (deep-merged),
  OpenRouter/Vercel routing, per-model thinking, and graceful degradation (a
  malformed `models.json` keeps the built-ins and reports a path-qualified
  error). For the OpenAI-Chat-Completions API family (custom models.json
  providers, ds4, OpenRouter, openai-completions), a mid-session `/model`
  selection runs a real turn that uses the catalog baseUrl/model/auth/headers/
  routing/thinking. Catalog-driven construction for the non-completions families
  and startup/`pipy run` resolution remain (see `docs/provider-catalog.md`).
- `ds4` is not a built-in catalog row: it is a `models.json` custom provider.
  Paste `docs/examples/ds4.models.json` into your `models.json`, or set
  `PIPY_DS4_BASE_URL` (and optionally `PIPY_DS4_API_KEY`) to have pipy
  synthesize the same custom-provider entry. A legacy `--native-provider ds4`
  adapter path remains for compatibility.

Native product session-tree controls (Pi-style, `pipy repl`):

- `-c`/`--continue`: continue the most recent native session for the workspace.
- `-r`/`--resume-session`: open the native session picker at startup on a real
  TTY (same overlay as in-session `/resume`); on a non-TTY stream it continues
  the most recent native session.
- `--session <path|id>`: open a specific native session file or partial id. A
  partial id that matches only a session in a *different* project prompts to
  fork it into the current workspace (aborts cleanly if declined).
- `--session-id <id>`: open the native session with this exact id for the
  workspace, or create a fresh one carrying it.
- `--session-dir <dir>`: use `<dir>` as the native session store root (the
  separate `$PIPY_SESSION_DIR` metadata-archive root is never reused for it).
- `-n`/`--name <name>`: name the native session for the run.
- `--fork <path|id>`: fork a native session into a new one. `--fork` and
  `--session-id` are each mutually exclusive with `--session`/`--continue`/
  `--resume-session`/`--no-session` (combining them is an error).
- `--no-session`: ephemeral mode — no native session tree and no `pipy-session`
  metadata record for the run.

(The old metadata-only `--resume RECORD` / `--branch LABEL` repl flags are
retired in favour of the native session tree; `pipy-session resume-info` remains
the separate archive utility.)

In a `pipy repl` session the slash commands `/session`, `/name <name>`, `/new`,
`/tree`, `/resume`, `/fork`, and `/clone` operate on the native session tree.
`/tree` opens an interactive in-frame selector in a TTY (movement, label with
`L`, filter with `Ctrl-O`, select with Enter, cancel with Escape) and accepts
scriptable `select`/`label`/`filter` subcommands in captured-stream mode.
`/resume` opens an interactive session picker overlay on a TTY — type to search,
`Tab` toggles current-project/all-projects scope, `Ctrl+P` the path column,
`Ctrl+S` the sort, `Ctrl+N` named-only, `Ctrl+R` renames, `Ctrl+X` deletes after
a `[y/N]` confirmation (the active session is protected), Enter opens, and
`Esc`/`Ctrl+C`/`Ctrl+D` cancel. On a non-TTY stream `/resume` lists/opens prior
native sessions and supports `named`, `rename <ref> <name>`, and `delete <ref>
--yes` (delete needs explicit confirmation, prefers the `trash` CLI, and never
touches `pipy-session` records).

### Providers

- `openai` reads `OPENAI_API_KEY` and calls the Responses API.
- `openai-codex` is the distinct ChatGPT Plus/Pro subscription path. Log in
  with `uv run pipy auth openai-codex login` (or `/login openai-codex` from
  the REPL). Pipy stores its own OAuth state under
  `${PIPY_AUTH_DIR:-~/.local/state/pipy/auth}/openai-codex.json`; it does not
  read or copy Pi's `~/.pi/agent/auth.json`.
- `openrouter` reads `OPENROUTER_API_KEY` and is convenient for ad-hoc smoke
  tests against many models:

  ```sh
  uv run pipy repl --agent pipy-native --slug openrouter-smoke \
    --native-provider openrouter \
    --native-model openai/gpt-5.1-codex
  ```
- `ds4` talks to a locally running
  [`antirez/ds4`](https://github.com/antirez/ds4) OpenAI-compatible Chat
  Completions server. In the catalog it is represented as a `models.json`
  custom provider (see `docs/examples/ds4.models.json`); the compatibility
  `--native-provider ds4` path defaults to base URL `http://127.0.0.1:8000/v1`
  and model `deepseek-v4-flash`. Override them with `PIPY_DS4_BASE_URL` and
  `--native-model`. `PIPY_DS4_API_KEY` is optional and, when set, is sent as a
  bearer token. The provider advertises `supports_tool_calls=True`; a live ds4
  smoke verified OpenAI-style `tool_calls` responses and pipy's bounded tool
  loop.

  ```sh
  # Outside the pipy repo. The q2-imatrix download is large and resumes.
  git clone https://github.com/antirez/ds4 ~/src/ds4
  cd ~/src/ds4
  make
  ./download_model.sh q2-imatrix   # about 81 GB; rerun to resume
  ./ds4-server --ctx 100000 --kv-disk-dir /tmp/ds4-kv --kv-disk-space-mb 8192

  uv run pipy run --agent pipy-native \
    --native-provider ds4 \
    --native-model deepseek-v4-flash \
    --slug ds4-smoke \
    --goal "Reply with exactly one sentence explaining what ds4 is."
  ```
- `fake` is the deterministic no-credential default.

### Native REPL

`pipy` and `pipy repl` start the interactive native shell with default slug
`native-repl`. `--repl-mode` defaults to `auto`: when the selected provider
advertises `supports_tool_calls=True`, the shell launches the bounded
model-driven tool loop with `read`, `ls`, `grep`, `find`, `write`, `edit`,
`edit_diff`, `truncate`, and `bash`. Pass `--repl-mode no-tool` or
`tool-loop` to force a mode.
`--tool-budget` (default 10,
max 25) caps invocations per user turn. Filesystem tools refuse generated,
`.git`, symlink-escaped, and oversized targets.

`bash` is a real shell, matching Pi: a prompt like "run the tests" lets the
model run an arbitrary command in the workspace (e.g. `just test`, `uv run
pytest`, `git status`, pipelines) and get the combined stdout/stderr back. Like
Pi's bash tool it spawns a real shell (`bash -c <command>`) in the workspace
root with the inherited environment and an optional `timeout` in seconds (the
whole process group is killed when it elapses); output is bounded to a byte
ceiling. A non-zero exit code is a normal observation the model reacts to, not
a tool error. The combined output is returned to the model only; the archive
boundary records counters and labels alone — never the raw command or output.

#### Resume, branch, and compaction

- `pipy repl --agent pipy-native --resume <stem>` starts a fresh session
  seeded from a finalized record's safe metadata-only `ResumeContext` (prior
  provider/model/turn labels only — never prompts, model output, tool
  payloads, file contents, diffs, or raw Markdown summary text). Both REPL
  modes show a safe resumed-state banner (prior session id, provider, model,
  turn count, finalized time). The parent record is never modified and no raw
  transcript sidecar is copied; the child archive records only a safe `resume`
  object on `session.started` plus a `native.session.resumed` event.
- `pipy repl --resume <stem> --branch <label>` forks a child *branch* from the
  parent with a validated safe label (`--branch` requires `--resume`; unsafe
  labels fail closed). The child records safe parent id, branch label, fork
  timestamp, and relationship; the parent stays byte-for-byte immutable.
- `/compact` reduces the provider-visible context in place (and an automatic
  threshold does the same) while keeping recent turns plus a safe
  metadata-only summary. In the tool loop the cut happens at a user-turn
  boundary, so a tool result is never orphaned and no raw tool payload leaks.
  `pipy-session list/inspect/export/resume-info` surface the lineage and
  compaction counters read-only.

The line-oriented mode also exposes these explicit commands:

- `/settings`, `/status`, `/clear`, `/compact`
- `/hotkeys` (resolved keyboard-shortcut table), `/reload` (re-read settings,
  keybindings, and resources), `/changelog` (release notes)
- `/login [openai-codex]`, `/logout [openai-codex]`,
  `/model [<provider>/<model>]`
- `/theme [<name>]` — list available chrome themes, or switch the terminal
  palette. Selecting a theme only swaps chrome colors; it runs no provider
  turn and records only the non-secret theme name.
- `/skill [<name>]` — list workspace/global skills, or load one named skill's
  instruction body as a bounded provider turn.
- `/template [<name> [args]]` — list prompt templates, or run one with
  `$ARGUMENTS`/`$1..$9` expansion as a bounded provider turn.
- `/read <path>` — bounded excerpt printed to stdout only; not
  provider-forwarded or archived.
- `/ask-file <path> -- <question>` — forwards one bounded excerpt plus
  question to a single provider turn labeled `ask_file_repl`.
- `/propose-file <path> -- <change-request>` — produces an in-memory
  proposal draft. No write.
- `/apply-proposal <path>` — consumes the same-session draft and invokes
  the `NativePatchApplyTool` on exactly one file.

`/read`, `/ask-file`, and `/propose-file` share a two-successful-excerpt
budget per REPL session and never display approval prompts. Workspace
context (`AGENTS.md` / `CLAUDE.md` ancestors plus the global pipy config
root) is discovered and composed into the native bootstrap system prompt
across the real providers, bounded by 64 KiB per file and 256 KiB total.

### Runtime resources: skills, prompt templates, custom commands

Both REPL modes load three bounded resource kinds from pipy-owned Markdown
stores, workspace-first then global (`<workspace>/.pipy/{skills,templates,commands}/`
then `<config>/{skills,templates,commands}/`, where `<config>` resolves through
`PIPY_CONFIG_HOME` → `${XDG_CONFIG_HOME}/pipy` → `~/.config/pipy`). Each `*.md`
file may carry optional `---` frontmatter with `name` and `description`; the
body is the instruction/template text.

- `/skill <name>` loads a skill body as a bounded provider turn; `/skill`
  lists available skills.
- `/template <name> [args]` runs a prompt template, expanding `$ARGUMENTS` /
  `$1..$9`; `/template` lists templates.
- A `.pipy/commands/<name>.md` file becomes a `/<name>` custom slash command
  that runs through the same local-command boundary as the built-ins (it
  cannot shadow a built-in) and appears in the tool-loop TUI slash menu and
  no-tool completion. Unknown/unsafe/empty resources fail closed with no
  provider turn.

Discovery rejects secret-shaped filenames, binary content, generated /
`.gitignore`-matched filenames, oversized bodies (64 KiB/file, 256 KiB total),
and symlink-escapes. This is deliberately not a general extension API — only
these three kinds load, through the existing provider/session/tool/archive
boundaries. Resource bodies, expanded prompts, and command text never enter
the metadata archive, prompt history, or transcript sidecar; only safe
counters/labels (name, path label, sha256, byte length, truncated) are
recorded.

REPL input goes through `--input-runtime auto|plain|prompt-toolkit`. `auto`
keeps plain stdin/stderr for captured streams and uses the optional
prompt-toolkit line editor on real TTYs when the package is available
(slash-command, path, and `@file` completion plus Enter / Esc+Enter
multiline editing). Prompt-toolkit is optional and not a declared runtime
dependency.

A genuine user prompt that names workspace files with `@path` (one or more
references) loads bounded UTF-8 excerpts for those files into the next provider
request in both `pipy repl --agent pipy-native` and `--repl-mode tool-loop`
(including the product TUI). The references resolve through the same bounded
reader as `/read` and the model-selected `read` tool, so missing, ignored,
binary, oversized, secret-shaped, and out-of-workspace paths fail closed with
safe local diagnostics; the user's literal prompt text is preserved and only
safe counters reach the session archive.

A prompt may also attach images with `@image:<path>` (one or more references).
Multimodal-capable adapters (Anthropic, OpenAI Responses, Google Gemini)
receive bounded, magic-byte-validated (PNG/JPEG/GIF/WebP) images as native
image blocks on the current user message; non-multimodal providers and
missing, oversized, out-of-workspace, or mistyped paths fail closed with safe
local diagnostics. Only safe metadata (reference/loaded/failed counts and, per
loaded image, media type, byte count, and sha256) reaches the session archive
— never the raw image bytes.

### Interactive editor (product TUI)

In a real TTY the tool-loop product TUI offers Pi-style interactive editing
(all stdlib-only, inline, no alternate screen):

- `@` file picker — type `@query` to open a ranked picker over workspace files
  (exact/prefix/substring scoring, not fuzzy); Up/Down move, Tab/Enter accept
  the chosen `@path`, Esc closes. `Tab` also completes a path-like prefix
  (`./src/`, `~/pr`) and is a no-op in prose.
- `!cmd` / `!!cmd` — run a shell command from the editor without a provider
  turn; `!` records the command/output into the conversation, `!!` runs it
  live-only. Escape cancels a running command.
- `Shift+Tab` cycles the thinking level; `Ctrl+P` / `Shift+Ctrl+P` cycle the
  model through the scoped/available set; `Ctrl+O` expands tool output;
  `Ctrl+T` folds thinking blocks (persisted).
- During an active turn, Enter queues a **steering** message (interrupts and
  redirects), `Alt+Enter` queues a **follow-up** (runs after), and `Alt+Up`
  restores queued messages to the editor; queued messages render in a pending
  region and drain steering-first.
- `Ctrl+V` pastes an image from the OS clipboard (written to an owner-only temp
  file and attached on submit); dropping a file onto the terminal inserts an
  `@image:`/`@path` reference.
- `/scoped-models` opens a multi-select overlay defining the Ctrl+P cycle set;
  `/hotkeys` lists every binding. Terminal-native mouse text selection keeps
  working (the renderer never enables xterm mouse tracking).

These surfaces are specified in `docs/tui-workflow.md` and gated by
`scripts/parity_checks/tui_workflow_conformance.py --json`.

### What pipy-native records — and doesn't

By default the harness stores only safe lifecycle metadata: agent, adapter,
provider/model labels, run id, workspace basename + path hash, status, exit
code, normalized usage counters (`input_tokens`, `output_tokens`,
`total_tokens`, `cached_tokens`, `reasoning_tokens`), provider storage
booleans, safe tool-intent labels, approval/sandbox policy labels, and
verification status. `--record-files` additionally records changed paths
from `git status --porcelain`, redacting assignment-style secret values.

It does **not** store raw prompts, model output, provider responses,
provider-native tool-call payloads, function arguments, response ids that
could reveal payloads, tool results, stdout, stderr, diffs, file contents,
secrets, credentials, tokens, private keys, or sensitive personal data.

Successful native runs print provider final text to stdout; finalization
messages and diagnostics go to stderr. `--native-output json` emits one
versioned metadata-only status object on stdout after recorder
finalization.

The opt-in `--archive-transcript` flag writes raw loop turns to
`~/.local/state/pipy/transcripts/<id>.jsonl` outside the pipy session
archive; this sidecar is sensitive content and is excluded from
`pipy-session list/search/inspect`.

For the full invariant set, capture-policy rationale, and deferred
boundaries see [`docs/harness-spec.md`](docs/harness-spec.md) and
[`docs/architecture.md`](docs/architecture.md). Pi parity status lives in
[`docs/pi-parity.md`](docs/pi-parity.md).

## Session Recorder CLI

Use `pipy-session` to create active JSONL records, append structured
events, and finalize immutable records into the syncable archive:

```sh
active="$(uv run pipy-session init --agent codex --slug session-storage-work)"
uv run pipy-session append "$active" --type decision.recorded --summary "Use finalized immutable JSONL files for sync."
uv run pipy-session finalize "$active" --summary "# Summary

Implemented the local recorder foundation."
```

Finalized filenames are shaped like:

```text
YYYY-MM-DDTHHMMSSZ-<machine>-<agent>-<slug>.jsonl
YYYY-MM-DDTHHMMSSZ-<machine>-<agent>-<slug>.md
```

For partial reconstructions, pass `--partial`:

```sh
uv run pipy-session init --agent codex --slug manual-reconstruction --partial
```

### Read-only inspection

- `list [--json]` — finalized archive listing with Markdown-summary
  presence.
- `search <query> [--json]` — case-insensitive substring match against
  listing metadata, event types, event summaries, and Markdown summaries.
- `inspect <name> [--json]` — metadata, event counts, and Markdown summary
  for one finalized record (accepts path, basename, or stem).
- `export <name>` — metadata-only JSON of one record; raw transcript
  sidecar included only with explicit `--include-transcript`.
- `resume-info <name>` — JSON-only continuation metadata, including safe
  resume/branch lineage and compaction-event counts.
- `verify [--json]` — scans the archive for structural issues (malformed
  first events, orphan Markdown summaries, stray `*.partial` files,
  unexpected files under `pipy/`, ambiguous basenames or stems).

All read commands omit raw JSONL event bodies, prompt text, tool output,
and transcript payloads.

## External Tool Capture

External tool capture is adapter-specific and secondary to `pipy-native`.
No single hook mechanism reliably covers Claude Code, Codex, and Pi, and
these paths do not make those tools product runtime backends.

| Platform | pipy capture/reference path | Capture status |
| --- | --- | --- |
| Claude Code | `pipy-session auto hook claude` handles `SessionStart`, metadata events, and `SessionEnd` when configured in Claude Code project settings. | Partial; lifecycle and conservative metadata, not raw transcripts. |
| Codex | `pipy-session wrap --agent codex -- ...` records wrapper start/end metadata. The documented `Stop` hook is turn-scoped, so no Codex hook adapter is installed. | Partial. |
| Pi | `pipy-session wrap --agent pi -- ...` records pipy wrapper metadata. | Partial; no raw transcript import. |

Drive an automatic partial capture directly:

```sh
active="$(uv run pipy-session auto start --agent codex --slug wrapper-test --session-id codex-123)"
uv run pipy-session auto event --agent codex --session-id codex-123 --type codex.turn.observed --summary "Observed a turn boundary."
uv run pipy-session auto stop --agent codex --session-id codex-123
```

Automatic state mappings live under
`~/.local/state/pipy/sessions/.in-progress/pipy/.state/`. Clean up stale
mappings without touching session records:

```sh
uv run pipy-session auto prune --dry-run
uv run pipy-session auto prune
```

### Claude Code Hook Capture

This repository does not install hooks into your dotfiles or commit live
hook settings. To enable Claude Code capture for your local checkout, add
`.claude/settings.local.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear",
        "hooks": [
          {
            "type": "command",
            "command": "cd \"${CLAUDE_PROJECT_DIR:?}\" && uv run pipy-session auto hook claude",
            "timeout": 5
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "cd \"${CLAUDE_PROJECT_DIR:?}\" && uv run pipy-session auto hook claude",
            "timeout": 5
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash|Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "cd \"${CLAUDE_PROJECT_DIR:?}\" && uv run pipy-session auto hook claude",
            "timeout": 5
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "cd \"${CLAUDE_PROJECT_DIR:?}\" && uv run pipy-session auto hook claude",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

Claude Code must trust the project settings for project-local hooks to
run. The adapter stores prompt text and assistant/tool output as redacted
metadata counts, not raw content. The example observes every prompt and
high-signal write-like tool event; tighten matchers or drop
`UserPromptSubmit` if the overhead is noticeable.

### Wrapper Capture

Use wrapper capture when a platform lacks a verified start/end hook:

```sh
uv run pipy-session wrap --agent codex --slug codex-work -- codex
uv run pipy-session wrap --agent pi --slug pi-work -- pi
```

Wrapper records are partial because they record pipy lifecycle metadata
around the process, not the platform's full transcript.

Raw transcript import for Codex / Claude / Pi is intentionally out of
scope: it would require explicit opt-in and a concrete redaction policy.
SQLite or another indexed store is also deferred — the JSONL + Markdown
archive remains the source of truth.

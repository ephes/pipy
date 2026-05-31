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

Session records live outside git under `~/.local/state/pipy/sessions/`,
with active records in `.in-progress/pipy/` and finalized records in
`pipy/YYYY/MM/`. See [`docs/session-storage.md`](docs/session-storage.md) for
the full lifecycle.

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
- `--native-output json`: for `--agent pipy-native` only; emits a single
  metadata-only JSON status object instead of provider final text.

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
  Completions server. It defaults to base URL `http://127.0.0.1:8000/v1` and
  model `deepseek-v4-flash`; override them with `PIPY_DS4_BASE_URL` and
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
`edit_diff`, `truncate`, and `bash`. Pass `--repl-mode no-tool` or `tool-loop`
to force a mode.
`--tool-budget` (default 10,
max 25) caps invocations per user turn. Filesystem tools refuse generated,
`.git`, symlink-escaped, and oversized targets.

`bash` runs through a shared safe command-execution substrate
(`pipy_harness.native.command_sandbox`): a no-shell, allowlisted-executable
boundary that enforces `.git` default-deny, symlink/path-escape refusal,
secret-shaped output redaction, bounded output, and a timeout/kill ceiling.

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
  proposal draft. No write, no verify.
- `/apply-proposal <path>` — consumes the same-session draft and invokes
  the `NativePatchApplyTool` on exactly one file.
- `/verify just-check` — available only after a successful same-session
  `/apply-proposal`; runs `just check` through `NativeVerificationTool`.

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

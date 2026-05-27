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
  defaulting to `fake`. Supported ids: `fake`, `openai`, `openai-codex`,
  `openrouter`, `anthropic`, `google`, `google-vertex`, `mistral`,
  `amazon-bedrock`, `azure-openai`, `cloudflare`, `openai-completions`. All
  real providers are stdlib-only (no third-party SDK dependencies).
- `--native-model <id>`: model label for the native provider; required for
  real providers in one-shot `pipy run`.
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
- `fake` is the deterministic no-credential default.

### Native REPL

`pipy` and `pipy repl` start the interactive native shell with default slug
`native-repl`. `--repl-mode` defaults to `auto`: when the selected provider
advertises `supports_tool_calls=True` (all three real adapters do), the
shell launches the bounded model-driven tool loop with `read`, `ls`, `grep`,
`find`, `write`, `edit`, `edit_diff`, and `truncate`. Pass `--repl-mode
no-tool` or `tool-loop` to force a mode. `--tool-budget` (default 10,
max 25) caps invocations per user turn. Filesystem tools refuse generated,
`.git`, symlink-escaped, and oversized targets.

`bash` is intentionally not exposed in the production model loop until it
has a real shell sandbox.

The line-oriented mode also exposes these explicit commands:

- `/settings`, `/status`, `/clear`
- `/login [openai-codex]`, `/logout [openai-codex]`,
  `/model [<provider>/<model>]`
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

REPL input goes through `--input-runtime auto|plain|prompt-toolkit`. `auto`
keeps plain stdin/stderr for captured streams and uses the optional
prompt-toolkit line editor on real TTYs when the package is available
(slash-command, path, and `@file` completion plus Enter / Esc+Enter
multiline editing). Prompt-toolkit is optional and not a declared runtime
dependency.

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
- `resume-info <name>` — JSON-only continuation metadata.
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

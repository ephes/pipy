# Workflow capture and sync

The optional `pipy-session` archive records summary-safe workflow metadata.
The native product conversation store is documented in [Session Tree](session-tree.md);
archive lifecycle and privacy rules are in [Session Storage](session-storage.md).

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
[Session Tree](session-tree.md) and
[Session Storage](session-storage.md) for the full lifecycle, and
`scripts/parity_checks/session_tree_conformance.py --json` for the conformance
gate.

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
- `export <name>` — metadata-only JSON of one record (export schema v2; no
  transcript payload — the native session tree is the transcript).
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

Archive lifecycle and automatic capture ownership are serialized for cooperating
local threads and processes. A late append to an already finalized active path
fails, and a new capture uses a distinct record name. See the
[ownership and recovery contract](session-storage.md#automatic-capture) for lock
ordering and interrupted-finalization recovery.

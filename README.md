# pipy

Python slop fork experiments for a coding-agent harness inspired by Pi and clean architecture.

The repository currently contains the first project infrastructure slices:
durable session-storage policy, a small local session-recorder CLI, and
explicit sync between the `studio` and `atlas` development machines.

## Development Setup

Install the Python tooling with `uv`:

```sh
uv sync
```

## Session Sync Setup

Copy the direnv example and approve it:

```sh
cp .envrc.example .envrc
direnv allow
just sessions-init
```

Then sync finalized session records with the paired Tailscale machine:

```sh
just sessions-sync
```

On `studio`, the default remote is `atlas.tailde2ec.ts.net`.
On `atlas`, the default remote is `studio.tailde2ec.ts.net`.

Raw session records live outside git by default:

```text
~/.local/state/pipy/sessions/
```

Active records should be written under:

```text
~/.local/state/pipy/sessions/.in-progress/pipy/
```

Finalized immutable records should be moved to:

```text
~/.local/state/pipy/sessions/pipy/YYYY/MM/
```

See `docs/session-storage.md` for the full lifecycle.

## Session Recorder CLI

Use `pipy-session` to create active JSONL records, append structured events,
and finalize immutable records into the syncable archive:

```sh
active="$(uv run pipy-session init --agent codex --slug session-storage-work)"
uv run pipy-session append "$active" --type decision.recorded --summary "Use finalized immutable JSONL files for sync."
uv run pipy-session finalize "$active" --summary "# Summary

Implemented the local recorder foundation."
```

The recorder resolves its root from `PIPY_SESSION_DIR`, or defaults to:

```text
~/.local/state/pipy/sessions/
```

It writes active records under `.in-progress/pipy/` and finalizes them under
`pipy/YYYY/MM/` with filenames shaped like:

```text
YYYY-MM-DDTHHMMSSZ-<machine>-<agent>-<slug>.jsonl
YYYY-MM-DDTHHMMSSZ-<machine>-<agent>-<slug>.md
```

For partial reconstructions, use `--partial` when initializing the record:

```sh
uv run pipy-session init --agent codex --slug manual-reconstruction --partial
```

List finalized records in the local archive:

```sh
uv run pipy-session list
uv run pipy-session list --json
```

The list command is read-only. It scans finalized `pipy/YYYY/MM/*.jsonl`
records, ignores `.in-progress/` and `*.partial` files, and reports whether a
matching Markdown summary exists. It skips archive JSONL files whose first line
is missing, not valid UTF-8, malformed JSON, or not a `session.started` event.

Search finalized records in the local archive:

```sh
uv run pipy-session search session-storage
uv run pipy-session search session-storage --json
```

The search command is read-only and scans only finalized `pipy/YYYY/MM/*.jsonl`
records plus sibling Markdown summaries. It requires a non-empty query and
performs case-insensitive substring matching against listing metadata, event
types, event `summary` strings, and Markdown summary text. It ignores active
`.in-progress/` records, automatic state files, `*.partial` files, unsupported
archive files, and malformed or unreadable finalized JSONL records. Human
output is tab-separated; JSON output includes structured match fields. Search
does not print raw JSONL event bodies, payload values, prompt text, tool output,
or transcript bodies.

Inspect one finalized record by path, basename, or stem:

```sh
uv run pipy-session inspect 2026-05-02T064433Z-studio-codex-session-work
uv run pipy-session inspect 2026-05-02T064433Z-studio-codex-session-work.jsonl --json
```

The inspect command is also read-only and only opens finalized archive JSONL
records directly under `pipy/YYYY/MM/`. It reports metadata, event counts,
event type counts, and the matching Markdown summary text when present. It does
not dump raw JSONL event bodies.

Verify local archive health without repairing or mutating files:

```sh
uv run pipy-session verify
uv run pipy-session verify --json
```

The verify command scans the resolved session root for finalized archive
structure issues. It reports malformed finalized JSONL first events, unreadable
finalized JSONL first-line read failures, orphan Markdown summaries,
sync-excluded `*.partial` leftovers, unexpected files under `pipy/`, and
duplicate finalized record basenames or stems that would make `inspect <name>`
ambiguous. The report contains paths, issue kinds, severities, and structural
details only; it does not print raw JSONL event bodies, prompt text, tool
output, raw exception text, or transcript payloads.

## Automatic Capture

Automatic capture is adapter-specific. There is no single hook mechanism that
reliably covers Claude Code, Codex, and Pi.

Current support matrix:

| Platform | pipy support | Capture status |
| --- | --- | --- |
| Claude Code | `pipy-session auto hook claude` handles official hook JSON for `SessionStart`, metadata events, and `SessionEnd` when configured in Claude Code project settings. | Partial by default; stores lifecycle and conservative metadata, not raw prompt/tool transcripts. |
| Codex | `pipy-session wrap --agent codex -- ...` records wrapper start/end metadata. Codex also has official hooks behind a feature flag, but this slice does not rely on them for full lifecycle finalization. | Partial. Do not treat this as complete automatic transcript capture. |
| Pi | `pipy-session wrap --agent pi -- ...` can record pipy wrapper metadata. Pi already auto-saves sessions under its own session store. | Partial pipy metadata only; Pi-native session import is not implemented yet. |

Start, append, and stop an automatic partial capture directly:

```sh
active="$(uv run pipy-session auto start --agent codex --slug wrapper-test --session-id codex-123)"
uv run pipy-session auto event --agent codex --session-id codex-123 --type codex.turn.observed --summary "Observed a turn boundary."
uv run pipy-session auto stop --agent codex --session-id codex-123
```

The automatic state mapping is stored under the sync-excluded active area:

```text
~/.local/state/pipy/sessions/.in-progress/pipy/.state/
```

If hooks or wrappers are interrupted, stale automatic state mappings can be
inspected and removed without deleting session records:

```sh
uv run pipy-session auto prune --dry-run
uv run pipy-session auto prune
```

Prune scans only `.in-progress/pipy/.state/*.json` under the selected session
root. It removes stale state files that are malformed or no longer point to an
existing active `.jsonl` record under `.in-progress/pipy/`. It does not remove
active JSONL records, finalized archive files, Markdown summaries, or
`*.partial` staging files. Output is tab-separated, with one line per stale
state file and a final summary line:

```text
would-remove	/path/to/state.json	active-not-found
summary	would-remove	1
```

### Claude Code Hook Setup

Claude Code support is implemented as a hook adapter, but this repository does
not install hooks into your user dotfiles or commit live hook settings. To enable
it for your local checkout, add `.claude/settings.local.json`:

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

Claude Code must trust the project settings for project-local hooks to run. The
adapter intentionally stores prompt text and assistant/tool output as redacted
metadata counts instead of raw content. The example observes every prompt and
high-signal write-like tool event, so each matching hook starts `uv`; tighten
matchers further or remove `UserPromptSubmit` if that overhead is noticeable.

### Wrapper Capture

Use wrapper capture when a platform does not provide a verified start/end hook
that pipy can finalize reliably:

```sh
uv run pipy-session wrap --agent codex --slug codex-work -- codex
uv run pipy-session wrap --agent pi --slug pi-work -- pi
```

Wrapper records are marked partial because they only record pipy lifecycle
metadata around the process. They do not capture the platform's full transcript.

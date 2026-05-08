# pipy

Python slop fork experiments for a coding-agent harness inspired by Pi and clean architecture.

The repository currently contains the first project infrastructure slices:
durable session-storage policy, a small local session-recorder CLI, an initial
`pipy run` subprocess harness, a native pipy runtime bootstrap, and explicit
sync between the `studio` and `atlas` development machines.

Product direction: `pipy-native` is the agent runtime. It should talk directly
to model providers through pipy's own provider ports, prompt construction, tool
boundary, and session semantics. Mentions of Codex, Claude Code, or Pi below
refer to metadata capture, subprocess wrapping, or session-reference workflows
for external tools. They are not supported product runtime backends and should
not become the core agent loop.

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

Near-term implementation planning lives in `docs/backlog.md`. Design rationale
and product boundaries live in `docs/harness-spec.md`.

## Pipy Run Harness

Use `pipy run --agent pipy-native` for the native product runtime. It runs one
minimal native turn through a direct provider boundary while pipy records
conservative partial lifecycle metadata into the session archive:

```sh
uv run pipy run --agent pipy-native --slug native-smoke --goal "Native bootstrap smoke"
uv run pipy run --agent pipy-native --native-output json --slug native-json --goal "Native JSON smoke"
uv run pipy run --agent pipy-native --native-provider openai --native-model <model> --slug openai-smoke --goal "Say hello briefly"
```

The same command can also wrap an arbitrary subprocess for conservative
lifecycle capture. This is a foundation and smoke-test path, not the long-term
agent runtime:

```sh
uv run pipy run --agent custom --slug smoke -- echo hello
uv run pipy run --agent codex --slug codex-capture --cwd . -- codex exec "..."
```

Required flags:

- `--agent <name>`: use `pipy-native` for the product runtime; other names such
  as `custom`, `codex`, `claude`, or `pi` are subprocess/capture labels only
- `--slug <slug>`: short run label used in the session filename
- `--goal <text>`: short goal, required for `--agent pipy-native`
- command after `--`: subprocess command to run, required except for `--agent pipy-native`

Optional flags:

- `--cwd <path>`: child process working directory, defaulting to the current directory
- `--root <path>`: session root override, matching `pipy-session --root`
- `--record-files`: after the child exits, record changed git file paths only
- `--native-provider fake|openai|openrouter`: native provider for `--agent pipy-native`, defaulting to `fake`
- `--native-model <id>`: model label for the native provider; required for `--native-provider openai` or `openrouter`
- `--native-output json`: for `--agent pipy-native` only, print one metadata-only JSON status object instead of provider final text

Treat `--goal` as user-visible archive metadata; do not paste full prompts,
secrets, credentials, or sensitive personal data into it.

For `--agent pipy-native`, pipy runs one minimal native turn through an
injected provider. The deterministic fake provider remains the default
smoke-test boundary and does not require credentials. Native tool invocation is
driven only by one sanitized supported intent from provider metadata; provider
success with final text and no intent completes without tool events. The no-op
tool path proves tool lifecycle and policy records without inspecting or
mutating the workspace. The explicit-file-excerpt read-only path is
fixture-gated, consumes only supported pipy-owned request/gate/target data,
forwards a successful bounded excerpt only in memory to one follow-up provider
turn, and never archives raw excerpt text. The OpenAI provider calls the
Responses API through a small standard-library HTTP boundary, reads its API key
from `OPENAI_API_KEY`, requires an explicit `--native-model`, sends pipy's
internal system prompt as `instructions`, sends the short `--goal` as `input`,
and requests `store: false`. It does not enable provider-side tools, streaming,
retries, conversation state, background mode, model fallback, or raw transcript
import. Provider final text is printed to stdout by the CLI contract only when
the native run succeeds, but the pipy archive still stores only lifecycle
metadata.

For subprocess capture runs, the harness streams child stdout and stderr to the
caller, finalizes the pipy record, and then returns the child process exit code.
Pipy does not own the subprocess prompt stack, model calls, tool behavior,
approval model, or transcript format. Use this path only when you want a
metadata record around another command.

The current native intent path can make exactly one bounded post-tool provider
call when the first provider result carries either a supported safe no-op intent
plus an explicitly supported synthetic sanitized observation fixture, or a
supported read-only explicit-file-excerpt intent plus a supported pipy-owned
read fixture. That follow-up turn receives only generated observation metadata
and, for the read-only path, bounded in-memory provider-visible context.
Archives remain metadata-only: safe turn/provider/model labels, status,
durations, normalized usage counters, storage booleans, safe observation
labels, and read-tool counts/source labels may be recorded, but raw excerpts,
raw tool results, stdout, stderr, diffs, patches, file contents, prompts, model
output, provider responses, provider-native tool-call or result objects,
function arguments, provider response ids that could reveal payload content,
raw tool arguments, shell commands, model-selected filesystem paths, secrets,
credentials, tokens, private keys, and sensitive personal data must not be
stored by default.

`pipy repl --agent pipy-native` is the first interactive native shell. Ordinary
non-command input lines remain bounded no-tool provider turns. The explicit
`/read <workspace-relative-path>` command may run once per REPL session after a
visible approval/sandbox prompt on stderr; a successful bounded excerpt prints
only to the interactive stdout stream and is not provider-forwarded or archived.
The explicit `/ask-file <workspace-relative-path> -- <question>` command shares
that one-read limit and approval path, then forwards the successful excerpt plus
question only in memory to one provider turn labeled `ask_file_repl`; it prints
only provider final text to stdout. Denied, unavailable, unsafe, skipped,
failed, malformed, and repeated read-command cases fail closed before provider
visibility. REPL archives remain metadata-only and omit raw approval prompts,
raw tool arguments, raw tool results, stdout, stderr, full file contents,
prompts, model output, provider responses, auth material, secrets, credentials,
tokens, private keys, and sensitive personal data.
The explicit `/propose-file <workspace-relative-path> -- <change-request>`
command shares the same one-read approval path, forwards one excerpt plus
change request only in memory to a provider turn labeled `propose_file_repl`,
records only metadata-only proposal status when supported, and does not apply
edits or run verification.

Native stdout is intentionally human-readable by default: a successful
`pipy-native` run prints only the provider final text to stdout. Session
finalization messages, diagnostics, and errors go to stderr so stdout remains
usable in shell pipelines. Failed native runs do not print provider final text
to stdout.

Use `--native-output json` with `--agent pipy-native` for structured
automation output. That mode emits one final versioned JSON object on stdout
after the native run and recorder finalization attempt, with diagnostics and
finalization still on stderr. It is a metadata-only status surface, not a
transcript or final-text channel. The JSON includes summary-safe values such as
schema/version, run id, status, exit code, agent, adapter, provider, model,
duration, normalized usage counters when available, storage booleans, and
finalized-record references. It does not emit raw prompts, model output,
provider responses, provider-native payloads, tool arguments or results,
stdout, stderr, command output, diffs, file contents, secrets, credentials,
tokens, private keys, or sensitive personal data by default. `--native-output`
is rejected for non-native agents before a run record is created.

By default `pipy run` does not store child stdout, child stderr, full argv,
prompt text, model output, raw HTTP payloads, diffs, or file contents. It
records safe metadata such as agent, adapter, provider, model id, run id,
workspace basename plus path hash, status, exit code, safe usage counters when
available, provider storage booleans, safe tool-intent labels when present,
no-op tool name/kind when invoked, approval and sandbox policy labels, and tool
storage booleans. Injected verification records add only safe command labels,
status, exit code, duration, reason/error labels, policy booleans, and false
stdout/stderr/command-output storage booleans. `--record-files` records
relative changed paths from `git status --porcelain`; without it, changed paths
are not recorded.
Native provider usage is normalized to finite non-negative allowlisted token
counters: `input_tokens`, `output_tokens`, `total_tokens`, `cached_tokens`,
and `reasoning_tokens`. Unknown provider-native usage fields and unavailable
counters are omitted rather than guessed.

Finalized records remain compatible with `pipy-session verify`, `list`,
`search`, `inspect`, and `reflect`.

The subprocess harness is a foundation and smoke-test path, not the long-term
agent runtime. The native bootstrap path establishes that pipy owns its system
prompt, provider boundary, tool boundary, and session semantics instead of
delegating to `codex`, `claude`, or another coding-agent CLI. It now includes a
small OpenAI Responses provider boundary and one bounded fake no-op tool-intent
path that may make one follow-up provider turn only from a synthetic sanitized
observation fixture. It also includes a fixture-gated explicit-file-excerpt
read-only path that can forward one bounded in-memory excerpt to a follow-up
provider turn, plus a supervised patch-apply boundary for injected
human-reviewed requests and an injected post-apply allowlisted verification
boundary for `just check`. The REPL exposes only the explicit approved `/read`
and `/ask-file` commands plus the proposal-only `/propose-file` command; normal
OpenAI/OpenRouter CLI runs still do not expose general model-selected tool use,
provider-side tools, public patch-apply or verification controls, arbitrary
shell execution, retries, streaming, provider registry, OAuth, or raw
transcript import.

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

Record workflow-learning events when a session should help compare agent/model
roles, review quality, or subagent usage:

```sh
uv run pipy-session workflow role <active-path> --role implementer --agent codex --model gpt-5.3-codex
uv run pipy-session workflow role <active-path> --role reviewer --agent claude --model claude-opus
uv run pipy-session workflow review-outcome <active-path> \
  --implementer-agent codex --implementer-model gpt-5.3-codex \
  --reviewer-agent claude --reviewer-model claude-opus \
  --high 1 --medium 2 --low 4 \
  --accepted 7 --fixed 7 --rejected 0 --deferred 0
uv run pipy-session workflow subagent <active-path> --role explorer --agent codex --outcome findings-used
uv run pipy-session workflow evaluation <active-path> \
  --pattern codex-implementation-claude-opus-review \
  --confidence medium --recommendation keep-testing \
  --summary "Reviewer found lifecycle risks implementer missed."
```

These commands append summary-safe `workflow.role`, `review.outcome`,
`subagent.used`, and `workflow.evaluation` events. Automatic adapters also
append `model.used` when a model identifier is exposed safely by hook metadata
or wrapper argv. Their generated summaries are searchable and appear in
`reflect`; do not include prompts, transcripts, tool output, secrets, or
sensitive personal data in those fields.

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
not dump raw JSONL event bodies. Human labels collapse control whitespace so
record metadata or event type strings cannot forge extra table or label lines;
`--json` keeps structured values non-lossy.

Reflect on finalized records to extract summary-safe learning signals:

```sh
uv run pipy-session reflect
uv run pipy-session reflect --json
```

The reflect command is read-only. It scans finalized records and reports archive
counts, event type counts, low-signal partial capture counts, and curated
learning signals from event `summary` strings and Markdown summary snippets. It
does not print raw JSONL event bodies, payload values, prompt text, tool output,
or transcript bodies. Workflow-learning events are grouped so the archive can
answer questions about role/model combinations, review outcomes, subagent use,
and whether a workflow pattern should be kept or compared further. Use it as
the first pass before promoting durable decisions, lessons, ADRs, prompts,
hooks, or skills into git.

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

## External Tool Capture

External tool capture is adapter-specific and secondary to `pipy-native`. There
is no single hook mechanism that reliably covers Claude Code, Codex, and Pi,
and these capture paths do not make those tools product runtime backends.

Current capture/reference matrix:

| Platform | pipy capture/reference path | Capture status |
| --- | --- | --- |
| Claude Code | `pipy-session auto hook claude` handles official hook JSON for `SessionStart`, metadata events, and `SessionEnd` when configured in Claude Code project settings. | Partial by default; stores lifecycle and conservative metadata, not raw prompt/tool transcripts. |
| Codex | `pipy-session wrap --agent codex -- ...` records wrapper start/end metadata. Codex hooks were rechecked against current OpenAI docs and local CLI support; no pipy Codex hook adapter is installed because the documented `Stop` hook is turn-scoped, not a reliable session finalizer. | Partial. Do not treat this as complete automatic transcript capture. |
| Pi | `pipy-session wrap --agent pi -- ...` can record pipy wrapper metadata. `pipy-session auto reference-pi <path>` creates a finalized pipy reference to a Pi-native session file without copying its contents. | Partial pipy metadata/reference only; no raw Pi transcript import by default. |

Start, append, and stop an automatic partial capture directly:

```sh
active="$(uv run pipy-session auto start --agent codex --slug wrapper-test --session-id codex-123)"
uv run pipy-session auto event --agent codex --session-id codex-123 --type codex.turn.observed --summary "Observed a turn boundary."
uv run pipy-session auto stop --agent codex --session-id codex-123
```

Reference an existing Pi-native session file without importing its raw JSONL
body:

```sh
uv run pipy-session auto reference-pi ~/.pi/agent/sessions/session.jsonl --slug pi-session-note
```

The Pi reference workflow requires an explicit existing file path, creates a
finalized partial `agent="pi"` record immediately, stores conservative metadata
such as the source filename, file size, mtime, and SHA-256 hash of the resolved
absolute path, and writes a Markdown summary saying the record is a reference,
not a transcript import. It does not read, copy, print, or store raw Pi session
content or the absolute source path.

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

### Claude Code Hook Capture

Claude Code capture is implemented as a hook adapter, but this repository does
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

### Codex Hook Status

OpenAI's current Codex docs include lifecycle hooks, and the local CLI reports
the `codex_hooks` feature as stable and enabled. Pipy still does not provide
`pipy-session auto hook codex` because the available reliable local finalization
surface is not a session end event: the documented `Stop` hook runs at turn
scope. Until Codex exposes stable local session start/end semantics that map
cleanly to pipy's active/finalized lifecycle, use wrapper capture for Codex.

### Raw Transcript Import

Raw transcript import is intentionally out of scope by default. Importing raw
Codex, Claude, or Pi transcript bodies would require explicit opt-in behavior
and a concrete redaction policy for prompts, assistant messages, tool inputs,
tool outputs, secrets, tokens, credentials, private keys, and sensitive personal
data. Current automatic commands capture metadata or references only.

### Indexed Storage

The finalized JSONL and Markdown archive remains the current implementation and
source of truth. SQLite or another indexed store is deferred future
query/performance work, not a blocker for moving on to the coding-agent harness.

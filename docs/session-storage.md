# Session Storage

Pipy should learn from coding-agent work without coupling the core product to any one agent UI. Session storage is therefore treated as a product capability, not as a Textual, CLI, Codex, Claude, or Pi feature.

## Goals

- Preserve useful context from coding sessions without making the repository large or noisy.
- Make sessions searchable and useful to future agents.
- Support later extraction of prompts, skills, hooks, tests, and architecture decisions.
- Keep the first version simple enough to maintain by hand.

## Storage Locations

Raw session records should not be committed to git by default.

Default local state location:

```text
~/.local/state/pipy/sessions/<project>/YYYY/MM/
```

Optional project-local location:

```text
.pipy/sessions/YYYY/MM/
```

`.pipy/` is ignored by git. Use it only when project-local data is more convenient than user-level state.

Project-local `.pipy/sessions/` records are intentionally not synced by the default recipes unless `PIPY_SESSION_DIR` is pointed there.

Repository-tracked files should be limited to:

- storage policy and schemas
- documentation
- curated lessons
- architecture decision records
- reusable prompts
- skills and hooks that have been intentionally promoted from raw sessions

## Current Format

Each finalized meaningful session must have:

- `*.jsonl`: append-only machine-readable events.

Each finalized meaningful session should usually also have:

- `*.md`: a human-readable summary and reflection.

Filenames should include enough uniqueness to avoid conflicts across machines:

```text
YYYY-MM-DDTHHMMSSZ-<machine>-<agent>-<slug>.jsonl
YYYY-MM-DDTHHMMSSZ-<machine>-<agent>-<slug>.md
```

For example:

```text
2026-04-30T133000Z-macbook-codex-session-storage-bootstrap.jsonl
```

JSONL records should use one JSON object per line:

```json
{"type":"session.started","timestamp":"2026-04-30T15:30:00+02:00","agent":"codex","goal":"Design initial session storage"}
{"type":"decision.recorded","timestamp":"2026-04-30T15:35:00+02:00","summary":"Use JSONL records first; add Markdown summaries when they help human review."}
```

The format is intentionally loose at this stage. Prefer stable fields where possible:

- `type`: event name such as `user.message`, `assistant.message`, `tool.command`, `decision.recorded`, `file.changed`, `verification.performed`, or `lesson.learned`.
- `timestamp`: ISO-8601 timestamp with timezone when known.
- `agent`: the agent or tool responsible for the event.
- `summary`: concise human-readable content.
- `payload`: structured details when useful.

## File Lifecycle

Session recorders should not sync files while they are still being written.

Recommended lifecycle:

- write active session data under `.in-progress/<project>/`
- flush and close the files when the session ends
- atomically rename the completed files to their final `*.jsonl` and `*.md` names
- treat finalized files as immutable

Sync recipes exclude `.in-progress/` and `*.partial`, so mid-session files are not copied to another machine. `.in-progress/` is the preferred convention; `*.partial` is excluded as a safety net.

If a finalized record needs correction, create a new sibling file or append a correction event to a new follow-up JSONL file. Do not edit the finalized original in place.

Follow-up filenames should keep the original slug and add a suffix:

```text
YYYY-MM-DDTHHMMSSZ-<machine>-<agent>-<slug>-followup-1.jsonl
YYYY-MM-DDTHHMMSSZ-<machine>-<agent>-<slug>-followup-1.md
```

## Recorder CLI

The `pipy-session` command is the first local recorder foundation. It does not
capture Codex, Claude, or Pi transcripts automatically. Instead, it provides a
generic way to create manual or reconstructed records that already follow the
storage lifecycle used by `just sessions-sync`.

Install the project environment first:

```sh
uv sync
```

Initialize an active record:

```sh
uv run pipy-session init --agent codex --slug session-storage-work
```

This creates a JSONL file under:

```text
${PIPY_SESSION_DIR:-~/.local/state/pipy/sessions}/.in-progress/pipy/
```

Append structured events while the record is active:

```sh
uv run pipy-session append <active-path> --type decision.recorded --summary "Use immutable finalized files for sync."
uv run pipy-session append <active-path> --event-json '{"type":"verification.performed","summary":"uv run pytest passed."}'
```

Finalize the record when the session ends:

```sh
uv run pipy-session finalize <active-path>
```

To include a matching Markdown summary, pass either text or an existing summary
file:

```sh
uv run pipy-session finalize <active-path> --summary-file summary.md
uv run pipy-session finalize <active-path> --summary "# Summary

Captured the useful session decisions."
```

Finalization moves the JSONL file to:

```text
${PIPY_SESSION_DIR:-~/.local/state/pipy/sessions}/pipy/YYYY/MM/
```

The finalized basename keeps the documented conflict-resistant shape:

```text
YYYY-MM-DDTHHMMSSZ-<machine>-<agent>-<slug>.jsonl
YYYY-MM-DDTHHMMSSZ-<machine>-<agent>-<slug>.md
```

The normal append API refuses finalized archive paths. If a finalized record
needs correction, create a follow-up record instead of editing the original.

List finalized records without modifying them:

```sh
uv run pipy-session list
uv run pipy-session list --json
```

`list` scans only finalized archive records under `pipy/YYYY/MM/*.jsonl`. It
does not read or sync active records under `.in-progress/`, does not include
`*.partial` staging files, and does not mutate finalized JSONL or Markdown
files. The tabular output includes the start time, machine, agent, slug,
partial/complete marker, summary presence, and JSONL path.
It skips archive JSONL files whose first line is missing, not valid UTF-8,
malformed JSON, or not a `session.started` event.

Search finalized records without indexing or mutating them:

```sh
uv run pipy-session search <query>
uv run pipy-session search <query> --json
```

`search` scans only finalized archive JSONL records directly under
`pipy/YYYY/MM/` and sibling Markdown summaries when present. It does not search
active records under `.in-progress/pipy/`, automatic state files under
`.in-progress/pipy/.state/`, `*.partial` staging files, unsupported archive
files, or arbitrary files outside the finalized archive.

Search is a local, read-only, case-insensitive substring scan. The query must
not be empty or only whitespace. It matches:

- finalized listing metadata: start time, machine, agent, slug, capture marker,
  JSONL path, and Markdown path when present
- JSONL event `type`
- JSONL event `summary` when the summary is a string
- sibling Markdown summary text when the summary can be read as UTF-8

Search returns results newest first, following the same ordering as `list`.
Malformed or unreadable per-record JSONL data is skipped quietly so one bad
record does not prevent discovery; use `verify` for archive-health reporting.
Markdown read failures skip only Markdown matching for that record.

Human output is a stable tab-separated table with start time, machine, agent,
slug, capture marker, structural match labels, and JSONL path. JSON output is a
list of structured result objects with metadata and match entries containing a
field name, optional event type, optional line number, and a short snippet.
Snippets may come only from metadata, event `type`, event `summary`, or
Markdown summary text. Search does not print raw JSONL event bodies, payload
values, prompt text, tool output, transcript bodies, raw invalid bytes, or raw
exception messages. It does not repair, delete, move, rewrite, index, import,
cache, or sync session records.

Inspect one finalized record without printing raw JSONL event bodies:

```sh
uv run pipy-session inspect <record>
uv run pipy-session inspect <record> --json
```

`<record>` may be an absolute finalized archive path, a relative finalized
archive path, a finalized JSONL basename, or a finalized JSONL stem. Basename
and stem resolution searches only finalized records under `pipy/*/*/` and fails
instead of guessing when more than one record matches.

`inspect` is read-only. It accepts only finalized `.jsonl` records directly
under `${PIPY_SESSION_DIR:-~/.local/state/pipy/sessions}/pipy/YYYY/MM/`. It
rejects active records under `.in-progress/pipy/`, automatic state files under
`.in-progress/pipy/.state/`, `*.partial` staging files, arbitrary paths outside
the finalized archive, and malformed archive records whose first line is
missing, invalid JSON, or not a `session.started` event.

The human output reports the same metadata as `list`, plus total event count,
event counts by type, and matching Markdown summary text when a `.md` sibling
exists. The JSON output includes those fields as structured data, with
`summary_text` set to the Markdown content or `null` when no summary exists.
Neither output includes full JSONL events, payloads, prompt text, tool output,
or other raw transcript content by default. Markdown summaries are shown
because they are intentional human-review artifacts.

Verify finalized archive health without modifying records:

```sh
uv run pipy-session verify
uv run pipy-session verify --json
```

`verify` is read-only and local. It scans the resolved session root for
structural archive issues and exits successfully when the scan completes, even
when issues are reported. A non-zero exit means the command failed to run, such
as an operating-system error while reading the session root.

The archive verifier reports:

- malformed finalized JSONL files under `pipy/YYYY/MM/*.jsonl`, based on the
  first event only: empty first line, non-UTF-8 first line, invalid JSON,
  non-object JSON, or a first event whose `type` is not `session.started`
- unreadable finalized JSONL files under `pipy/YYYY/MM/*.jsonl` when the
  verifier cannot open the file or read its first line
- orphan Markdown summaries under `pipy/YYYY/MM/*.md` with no sibling JSONL
- `*.partial` leftovers anywhere under the session root, including
  `.in-progress/`
- unexpected files under `pipy/`, including files outside `YYYY/MM/`, files
  directly under `pipy/YYYY/`, unsupported suffixes under `pipy/YYYY/MM/`, and
  malformed finalized JSONL filenames
- duplicate finalized JSONL basenames or stems across `pipy/*/*/`, because
  those names make `inspect <basename-or-stem>` ambiguous

Active JSONL files under `.in-progress/pipy/` and automatic state files under
`.in-progress/pipy/.state/` are mutable operational files and are not treated as
malformed finalized records. The verifier may report `*.partial` files in the
active area because sync excludes them and they can indicate an interrupted
write.

The human output is tab-separated. JSON output contains `ok`, `issue_count`,
`root`, and a list of issues with `severity`, `kind`, `path`, and `detail`.
Neither output prints full JSONL events, payloads, prompt text, tool output, raw
exception text, or raw transcript bodies. `verify` does not repair, delete,
move, rewrite, index, or import session records.

## Privacy

Session records must not include secrets, API keys, credentials, private keys, tokens, or sensitive personal data. Redact sensitive values before writing.

If raw tool output contains sensitive data, store a summary instead of the raw output.

## Partial Transcripts

Some coding-agent environments do not expose a complete raw transcript to the running agent. In that case, store a partial reconstruction and mark it clearly:

```json
{"type":"capture.limitations","summary":"Partial reconstruction from visible conversation context; no raw platform transcript export was available."}
```

The CLI can add this marker during initialization:

```sh
uv run pipy-session init --agent codex --slug manual-reconstruction --partial
```

## Automatic Capture

Automatic capture uses the same recorder lifecycle as manual capture:

- active JSONL records live under `.in-progress/pipy/`
- adapter state lives under `.in-progress/pipy/.state/`
- finalized JSONL and Markdown records move to `pipy/YYYY/MM/`
- finalized records remain immutable and syncable

The `.state/` files map a platform session id to a pipy active JSONL file. They
are operational state, not durable history, and they stay under `.in-progress/`
so `just sessions-sync` does not copy them.

The scriptable commands are:

```sh
uv run pipy-session auto start --agent claude --slug some-work --session-id platform-id
uv run pipy-session auto event --agent claude --session-id platform-id --type claude.userpromptsubmit --summary "Observed prompt metadata."
uv run pipy-session auto stop --agent claude --session-id platform-id
uv run pipy-session auto prune --dry-run
uv run pipy-session auto prune
uv run pipy-session auto hook claude
uv run pipy-session wrap --agent codex --slug codex-work -- codex
```

`auto hook claude` reads the official Claude Code hook JSON from stdin. It
handles:

- `SessionStart`: creates an active partial pipy record and state mapping
- `UserPromptSubmit`, `PostToolUse`, and other metadata events: appends a
  conservative metadata event if state exists
- `SessionEnd`: appends an end marker, finalizes the record, and removes state

The Claude adapter is metadata-first. It does not write raw prompt text, raw
assistant messages, raw tool inputs, raw tool responses, secrets, tokens,
credentials, or private keys by default. Prompt and assistant text are recorded
as redacted character counts. Tool payloads are represented by tool names, ids,
and JSON key names when available.

Metadata values and keys with sensitive markers such as `token`, `secret`,
`password`, `credential`, or `api_key` are redacted before they are written.
Platform session ids containing those markers use a stable redacted hash in
state filenames and records.

Codex and Pi currently use wrapper-based pipy capture unless a future adapter
adds a verified lifecycle bridge:

- Codex: current official Codex docs include hooks behind a feature flag, but
  the start/end lifecycle needed for reliable finalization is not treated here
  as complete transcript capture. Use `pipy-session wrap --agent codex -- ...`
  for partial lifecycle metadata.
- Pi: Pi already auto-saves its own JSONL sessions under `~/.pi/agent/sessions/`.
  Pipy does not import or duplicate those native sessions in this slice. Use
  `pipy-session wrap --agent pi -- ...` only when you want a pipy-side partial
  lifecycle marker around the Pi process.

Records created by these automatic commands are partial unless `auto start
--complete` is used by an adapter that truly captures a complete transcript.
Do not use `--complete` for the Claude, Codex wrapper, or Pi wrapper flows
documented here.

### Automatic State Pruning

Interrupted hooks, killed wrappers, or sessions that never send a matching end
event can leave abandoned files under `.in-progress/pipy/.state/`. These files
are adapter bookkeeping only. They are not durable history.

Use dry-run mode first to inspect stale mappings:

```sh
uv run pipy-session auto prune --dry-run
```

Then remove them:

```sh
uv run pipy-session auto prune
```

Prune scans only:

```text
${PIPY_SESSION_DIR:-~/.local/state/pipy/sessions}/.in-progress/pipy/.state/*.json
```

A state file is stale when it is malformed, is not a JSON object, lacks an
`active_path`, points at a missing file, or points at something other than an
existing active `.jsonl` record directly under `.in-progress/pipy/`. Live state
that references an existing active JSONL record is preserved.

Prune removes only stale `.state/*.json` files. It does not remove active JSONL
records, finalized `pipy/YYYY/MM/` archive records, Markdown summaries, or
`*.partial` staging files. It also does not import transcripts, install hooks,
or schedule background maintenance.

Output is tab-separated and avoids printing raw JSON state contents:

```text
would-remove	/path/to/state.json	active-not-found
summary	would-remove	1
```

## Current Session Example

The bootstrap record for the current Codex conversation was stored outside the repository at:

```text
~/.local/state/pipy/sessions/pipy/2026/04/2026-04-30T133000Z-studio-codex-session-storage-bootstrap.jsonl
~/.local/state/pipy/sessions/pipy/2026/04/2026-04-30T133000Z-studio-codex-session-storage-bootstrap.md
```

## Sync

Session storage needs to work across machines without putting raw transcripts in the git repository.

The recommended default is file-based sync over finalized immutable records:

- Write each finalized session to unique immutable files.
- Keep active sessions in `.in-progress/`.
- Avoid editing finalized JSONL or Markdown files after a session has been captured.
- If a summary needs correction, create a follow-up file with a `summary.corrected` or `reflection.added` event.
- Use timestamp, machine, agent, and slug in filenames to prevent two machines from writing the same path.
- Keep sync conflicts as separate files and resolve them manually; do not silently merge transcripts.

Recommended sync options:

- rsync via `just` recipes for explicit sync between trusted development machines.
- Syncthing for live sync across trusted personal machines when automatic background sync is useful.
- rclone for cloud or object-storage backup/sync.
- git-annex only if session artifacts grow into large datasets that need content-addressed archival workflows.

For this project, start with this layout on each machine:

```text
~/.local/state/pipy/sessions/
  .in-progress/
    pipy/
      current-session.jsonl
  pipy/
    2026/
      04/
        2026-04-30T133000Z-macbook-codex-session-storage-bootstrap.jsonl
        2026-04-30T133000Z-macbook-codex-session-storage-bootstrap.md
```

The `just` recipes sync finalized records directly between each machine's `PIPY_SESSION_DIR`.

This repository uses `direnv` plus `just` for the initial sync workflow.

Copy `.envrc.example` to `.envrc`, adjust values if needed, and approve it:

```sh
cp .envrc.example .envrc
direnv allow
```

The default machine pairing is:

- `studio.tailde2ec.ts.net` pulls from and pushes to `atlas.tailde2ec.ts.net`.
- `atlas.tailde2ec.ts.net` pulls from and pushes to `studio.tailde2ec.ts.net`.

The `.envrc` file exports:

- `PIPY_SESSION_DIR`: stable local state path.
- `PIPY_SESSION_REMOTE_PATH`: home-relative session path on the other machine.
- `PIPY_SESSION_REMOTE`: rsync remote for the other machine.

Recipes:

```sh
just sessions-init
just sessions-pull
just sessions-push
just sessions-sync
just sessions-verify
```

The normal command is:

```sh
just sessions-sync
```

It performs two steps:

- pulls missing records from `PIPY_SESSION_REMOTE`
- pushes missing records to `PIPY_SESSION_REMOTE`

After it succeeds, both machines should have the same finalized session records, assuming both machines are reachable and no finalized files were edited in place.

For one-off targets, use `just sessions-pull-from <remote>` or `just sessions-push-to <remote>`.

Use `just sessions-verify` to report finalized files that differ between this machine and `PIPY_SESSION_REMOTE`.

The sync recipes use `--ignore-existing` so one machine does not overwrite another machine's finalized session records. This is only correct because active files are excluded from sync and finalized files are immutable.

Because session data can contain sensitive project context, use a sync backend that matches the privacy level of the data. For cloud sync, prefer encryption before upload.

## Future Direction

The first implementation can stay file-based. Later, the same records can be imported into SQLite or another indexed store behind a `SessionRepository` port.

Future automatic-capture work should keep the adapter boundary explicit:

- add a Codex hook adapter only if it can reliably map platform lifecycle events
  to pipy start/finalize semantics without overstating transcript completeness
- add a Pi importer or bridge that references Pi-native session files without
  copying sensitive raw data into git
- add opt-in raw transcript import only with clear redaction behavior

Likely future abstractions:

- `SessionRecorder`
- `SessionRepository`
- `PromptRepository`
- `SkillRepository`
- `ReflectionRepository`

The application core should depend on these ports, while CLI, Textual, web, and external integrations provide adapters.

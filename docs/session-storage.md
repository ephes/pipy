# Session Storage

> **Scope note (2026-06-02):** this document describes the metadata-first
> `pipy-session` archive, which is a **pipy-specific catalog/learning utility**,
> not the product session store. For real Pi parity the product session store is
> the full-transcript native session tree (`docs/session-tree.md`), and the
> metadata-first archive is demoted to an optional, non-default utility. Its
> "metadata-only / privacy-first" posture is a pipy preference, **not** a parity
> virtue, and must not be cited as a reason to diverge from Pi (which stores,
> streams, and exports full session content). See [parity-plan.md](parity-plan.md)
> §3. The privacy rules below still govern *this* archive while it exists.

Pipy should learn from coding-agent work without coupling the core product to any one agent UI. Session storage is therefore treated as a product capability, not as a Textual, CLI, Codex, Claude, or Pi feature.

Semantic compaction requests and generated summaries belong to the private native
product conversation, including any restored prior summary. Auxiliary provider
output is not streamed through ordinary transcript or workflow projections.
Workflow events may record bounded outcomes and counters, but never summary
bodies or provider error text from summary generation.

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

Full raw transcript capture is a separate adapter capability from summary-safe
learning events. When a platform exposes a reliable full-session export, pipy
may store or reference that private raw artifact under the same local session
root, outside git. Commands such as `search`, `inspect`, and `verify` must still default to
finalized metadata, event types, event summaries, Markdown summaries, and
explicitly allowlisted learning fields rather than raw transcript bodies. This
keeps complete capture useful for private forensics while preserving safe
day-to-day inspection.

Escaped adapter exceptions follow the same split. A caller-facing or in-memory
result may retain failure detail where its product contract requires it, but a
durable `harness.run.exception` event and its derived Markdown summary record
only the bounded exception type plus fixed lifecycle metadata. They never store
raw exception text, paths, command arguments, prompts, imported session content,
or other product-session data. In particular, an exception escaping the native
`/import` adapter path may name its type in the metadata archive, but its message
and any source-path or transcript detail remain outside both archive formats.

## File Lifecycle

Session recorders should not sync files while they are still being written.

Recommended lifecycle:

- write active session data under `.in-progress/<project>/`
- flush and close the files when the session ends
- atomically rename the completed files to their final `*.jsonl` and `*.md` names
- treat finalized files as immutable

Sync recipes exclude `.in-progress/` and `*.partial`, so mid-session files are not copied to another machine. `.in-progress/` is the preferred convention; `*.partial` is excluded as a safety net.

If a finalized record needs correction, create a new sibling file or append a correction event to a new follow-up JSONL file. Do not edit the finalized original in place.

### Resume, branch, and compaction metadata

> **Note (2026-06-09):** the pipy-only metadata-only `pipy repl --resume <stem>`
> / `--branch <label>` repl flags are **retired**. Product resume/branch/fork is
> now the native session tree (`-c`/`-r`/`--session`/`--session-id`/`--fork`,
> `/resume`/`/tree`/`/fork`/`/clone`; see [session-tree.md](session-tree.md)).
> The `pipy-session resume-info <stem>` archive utility and the lineage metadata
> described below still apply to archive records produced by other flows (e.g.
> forks recorded as new finalized records); they are no longer reachable through
> retired repl flags.

Resumed/forked **child** archive records and in-session compaction (`/compact`
plus an automatic threshold) preserve the metadata-first contract:

- A resumed or forked **child** session is always a brand-new finalized record.
  The parent record is read read-only and never mutated; no raw transcript
  sidecar is copied. The child records lineage as a safe `resume` object on its
  `session.started` event — `{parent_session_id, relationship, branch_label,
  fork_timestamp}` — plus a `native.session.resumed` lifecycle event carrying
  the same safe labels and the prior provider/model/turn counters. These are
  short labels only; prompts, model output, tool payloads, file contents,
  diffs, and Markdown summary text never enter them.
- Compaction is recorded only as one aggregate metadata-only
  `native.session.compacted` event after a product adapter run that compacted at
  least once. Its payload carries the adapter/mode labels, total compaction
  count, and cumulative dropped-group count. Detailed per-reduction counters
  and byte totals remain inside the canonical/product runtime; the dropped raw
  context is simply discarded from the in-memory provider-visible history and
  is never written to the archive. The safe summary injected back into the
  provider system prompt is counts only.
- The catalog surfaces this read-only: `list` shows the lineage relationship
  and branch label, `inspect` adds the parent id and `compaction_event_count`,
  `export` adds a safe `resume` lineage object and `compaction_event_count`,
  and `resume-info` adds the branch/parent/compaction fields. All of them
  reject malformed, ambiguous, symlinked, active (`.in-progress`), or
  out-of-archive records without printing raw bodies or unsafe labels.

This describes the metadata-archive workflow. It is **not** the product session
source. Pi-compatible product sessions, `/tree`, `/resume`, `/fork`, `/clone`,
and durable compaction are served by a separate private **native product
session tree** store (`pipy_harness.native.session_tree`) that contains full
conversation history by design, like Pi's own session files, while keeping
`pipy-session` metadata surfaces body-free. That store ships at:

```text
~/.local/state/pipy/native-sessions/--<encoded-cwd>--/<timestamp>_<uuid>.jsonl
```

It is append-only JSONL (a `session` header followed by tree entries), written
with owner-only permissions, kept outside git, and never synced by the
`pipy-session` archive recipes. Product context reconstruction walks the active
branch of that native tree, not `pipy-session resume-info`. Pi-style ephemeral
mode (`pipy repl ... --no-session`) suppresses both native session-tree writes
and the `pipy-session` metadata record (via `NullSessionRecorder`). The full
storage split, command behavior, and the deterministic conformance gate
(`scripts/parity_checks/session_tree_conformance.py --json`) are specified in
[`session-tree.md`](session-tree.md).

The completed architecture migration now routes canonical agent completion
events through `ProductSessionEventProjection` and the typed native product-
session action sink. That live projection is the single durable writer for
ordinary canonical message appends; the synchronous Phase 3.1c coordinator
still owns full-content active-history loads and compaction/session transitions.
Its exact product-content DTOs are private session values and must never cross
into the separate counts-only workflow projection, which never dereferences
canonical full-content payloads. Historical tool results whose tool name cannot
be inferred from their own branch ancestry remain private storage records: they
preserve JSON and tree ancestry but are not supplied to providers.

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

Active records must be regular `.jsonl` files directly under that directory.
Recorder APIs reject symlinked active records instead of appending through them
or finalizing them into the archive.

Append structured events while the record is active:

```sh
uv run pipy-session append <active-path> --type decision.recorded --summary "Use immutable finalized files for sync."
uv run pipy-session append <active-path> --event-json '{"type":"verification.performed","summary":"uv run pytest passed."}'
```

Automatic adapters also append `model.used` when a model identifier is exposed
safely by hook metadata or wrapper argv. Their summaries are searchable by
`pipy-session search`. Do not include prompts, transcript bodies, tool output,
secrets, credentials, or sensitive personal data in model, role, outcome, or
summary fields. (The dedicated `pipy-session workflow` subcommand was removed
as part of the 2026-05-26 code-quality audit cleanup; see Track CQ-A in
`docs/backlog.md` for context.)

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

`search` scans only regular finalized archive JSONL records directly under
`pipy/YYYY/MM/` and regular sibling Markdown summaries when present. It ignores
symlinked archive entries instead of following them. It does not search active
records under `.in-progress/pipy/`, automatic state files under
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

`inspect` is read-only. It accepts only regular finalized `.jsonl` records
directly under `${PIPY_SESSION_DIR:-~/.local/state/pipy/sessions}/pipy/YYYY/MM/`. It
rejects active records under `.in-progress/pipy/`, automatic state files under
`.in-progress/pipy/.state/`, `*.partial` staging files, arbitrary paths outside
the finalized archive, symlinked archive records, and malformed archive records
whose first line is missing, invalid JSON, or not a `session.started` event.

The human output reports the same metadata as `list`, plus total event count,
event counts by type, and matching Markdown summary text when a regular `.md`
sibling exists. The JSON output includes those fields as structured data, with
`summary_text` set to the Markdown content or `null` when no summary exists.
Neither output includes full JSONL events, payloads, prompt text, tool output,
or other raw transcript content by default. Markdown summaries are shown
because they are intentional human-review artifacts. Human metadata and event
type labels collapse whitespace and escape terminal controls so malformed
archive values cannot forge output lines or terminal commands; JSON output
preserves structured values.

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
- symlinked finalized archive entries, because archive JSONL and Markdown
  summaries must be regular files
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

### The Native Session Tree Is the Transcript

There is no separate transcript sidecar. The removed `--archive-transcript` flag (which wrote raw loop turns to `~/.local/state/pipy/transcripts/<id>.jsonl`) and its `TranscriptSink` writer are gone; the flag is now rejected with guidance. The native product session tree is the full-content record of the conversation.

To write a portable copy of a session, use `/export` in-session or the top-level `pipy --export`. These operate on the native session tree, not on the metadata-first `pipy-session` archive.

`pipy-session export` validates present event fields before producing output:
`type`, `timestamp`, `summary`, `agent`, `machine`, `project`, and `slug` must
be strings; `partial` must be a boolean and `sequence` an integer (not a
boolean). The header `goal` must be a string or null; later events' goals
are excluded. Missing fields retain their existing
defaults. Wrong scalar types and containers reject the whole record with a
bounded field-only error; the CLI exits 2 without emitting JSON or the rejected
value. Explicitly authored string summaries and goals are preserved verbatim.
Unknown event fields and payloads remain excluded. Markdown summaries and safe
resume lineage retain their existing policies.

The `pipy-session` metadata archive remains metadata-first and never stores raw loop turns; its `export` command emits a metadata-only JSON object (export schema v2 — the former opt-in `transcript_events` / `transcript_path_label` fields were removed along with the sidecar).

Human catalog list/search/inspect/verify output escapes terminal controls,
lone surrogates and bidirectional controls in metadata, paths, event types
and Markdown summaries. Table cells (including verification paths/details)
collapse whitespace; summaries retain line breaks. Other Unicode, including
emoji joiners, remains readable. Stored evidence and JSON retain original values.

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

Recorder creation, append and finalization share one OS-backed lifecycle lock
per session root (`.in-progress/pipy/.recorder.lock`). Each operation opens its
own lock descriptor, so both threads and processes participate. Append resolves
and opens an existing active file only while holding this lock; finalization
waits for admitted writes to close before renaming. Late appends fail rather
than recreating an active path. Creation reserves names against both active and
finalized records, so a retired path cannot identify a replacement generation.
This intentionally serializes short archive writes across identities.

Automatic start, identity-based event/stop, and prune share a persistent lock
per mapping (`.state/<identity>.lock`). Both supplied identities and identities
computed from a new active filename must recheck live state under this owner.
If a computed identity is already live, discard only the unpublished new record
and resume the existing generation, just as a supplied identity does. Never
overwrite a live mapping or orphan its record. Hold the owner across lookup and the complete
transition, including stop's finalization and mapping retirement. Prune decides
and removes under that same lock. Direct `--active` operations use the recorder
lock; when an identity is also supplied they take its lock and retire only a
mapping that still names that active record. Without an identity they leave
stale mappings for prune. The order is identity lock then recorder lock;
recorder code never takes an identity lock. Locks are non-reentrant and helpers
do not reacquire their own owner. Lock sidecars are never deleted, avoiding
split ownership of old/new inodes, and all are excluded from sync under
`.in-progress/`.

Publication uses owned unique temporary files; failed start removes only its
new active record and temporary mapping. Start owns its unpublished active
record until either publication transfers ownership to the mapping, or discard
retires that ownership exactly once. Retire cleanup authority before invoking
discard: after it releases the recorder owner, the same pathname may identify
a different newly created record. Resuming an existing mapping after discard
never carries cleanup authority over the discarded pathname, even if resume
fails. Cleanup must not infer continuing ownership from pathname equality. Locks release on exceptions and process
exit. Failed summary staging removes its owned partial file. If JSONL rename
succeeds but Markdown publication fails, JSONL remains finalized and immutable;
the mapping may remain stale for prune. A failure before rename leaves the
active record recoverable (an end marker may already have been appended).
These are local cooperating-writer guarantees; external file edits and
cross-machine concurrent writers to a shared filesystem are outside the lock
contract. Metadata sanitization and immutable-file sync rules are unchanged.

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
`password`, `credential`, `api_key`, or `api-key` are redacted before they are
written.
Platform session ids containing those markers use a stable redacted hash in
state filenames and records. Automatic-capture Markdown summaries apply the
same line-level redaction to explicit summaries and end-reason text.

Codex and Pi currently use wrapper-based or reference-based pipy capture unless
a future adapter adds a verified lifecycle bridge:

- Codex: current official Codex docs and local CLI support include hooks, and
  the local `codex_hooks` feature is stable and enabled, but the documented
  `Stop` hook is turn-scoped rather than a reliable local session finalizer.
  Pipy therefore does not install `pipy-session auto hook codex` in this slice.
  Use `pipy-session wrap --agent codex -- ...` for partial lifecycle metadata.
- Pi: Pi already auto-saves its own JSONL sessions under `~/.pi/agent/sessions/`.
  Use `pipy-session wrap --agent pi -- ...` when you want a pipy-side partial
  lifecycle marker around the Pi process. (The dedicated `auto reference-pi`
  subcommand was removed as part of the 2026-05-26 cleanup; see Track CQ-A in
  `docs/backlog.md`.)

Records created by these automatic commands are partial unless `auto start
--complete` is used by an adapter that truly captures a complete transcript.
Do not use `--complete` for the Claude, Codex wrapper, or Pi wrapper flows
documented here.

### Raw Transcript Import Policy

Raw transcript import remains deferred and out of scope by default. A future
importer must be explicit opt-in and must define redaction behavior before it
handles prompt text, assistant messages, tool inputs, tool outputs, raw
exception text, secrets, API keys, tokens, credentials, private keys, or
sensitive personal data. Metadata capture, wrapper lifecycle records, and
Pi-native references are not raw transcript import.

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

## Durable SQLite store (internal)

> Internal groundwork of the [durable execution campaign](specs/2026-10-19-durable-execution-plan.md)
> (slices DE03a, DE03b and DE03c): not exported, not used by any product path,
> and separate from the workflow archive above. A durable store never syncs through
> the archive recipes.

A durable store is one SQLite database file that a single process owns from
opening until it closes. These rules describe the files on disk.

| File | Purpose | Lifetime |
| --- | --- | --- |
| `<name>` | SQLite database in WAL mode, named a pipy durable store in its header | until the user deletes it |
| `<name>-wal`, `<name>-shm` | SQLite write-ahead log and its index | while open; a clean close removes them, a crashed owner leaves them for the next open to recover |
| `<name>-journal` | SQLite rollback journal | transient |
| `<name>.lock` | owner lock | never deleted |

**Create and open.** `create` makes the database, and any missing parent
directories, and refuses an existing database or a leftover `-wal`, `-shm` or
`-journal` (SQLite would silently delete stale sidecars next to a new empty
database). `open` reads the 100-byte database header itself before SQLite sees
the file: anything but a pipy durable store (see **Identity**) in WAL mode is
refused, and so is an
existing `-journal`, the trace of a crashed rollback-journal writer or of an
interrupted switch to WAL that SQLite would otherwise roll back into the
database on its first read. A refused file keeps its bytes and mode and gets no
lock file. A database renamed or replaced while it is being opened is refused
where the change is seen, even when the replacement is a valid store; this is
best-effort detection (see the contract boundary below). Invalid input, and an
`open` of a missing path, change nothing on disk. A failed `create`, including
one whose new directory entry cannot be flushed, removes the database it made,
as long as its name still names it; the lock file and created directories stay,
and `create` can simply be retried. A database name cannot end in `.lock`,
`-wal`, `-shm` or `-journal`, so another store's sidecar is never opened as a
database.

**Identity.** The database header names the store: application ID
`0x50495059` ("PIPY", header bytes 68–71) and the schema version as
`user_version` (bytes 60–63). `open` reads both in the same header read as the
WAL check and refuses, before SQLite sees the file, another application's
database (`ForeignStoreError`), a schema version newer than this pipy supports
(`NewerStoreError`, with Pi's message "SQLite schema version N is newer than
supported version M") and the remains of an interrupted create
(`InterruptedCreateError`, below). Such a refusal changes no byte and creates
no lock file. One shape is decided later: an empty one-page database without
identity or schema next to a non-empty `-wal` is what a create that crashed
right after committing its schema leaves, because the identity reaches the
database file only at a checkpoint. It is validated after WAL recovery on a
read-only connection (see **Integrity**), which rebuilds only SQLite's `-shm`
index.

**Schema and migrations.** Immutable schema version 1 has the shape of Pi durable 1.3.0's
SQLite schema version 2, with Pi's table, index and column names and no table
prefix, plus Python deltas: the payload columns (`record` and revision
`content`) and the revision `version` are `BLOB`s holding JSON, so every value,
lone surrogates, `-0.0` and integers beyond 64 bits included, round-trips
exactly; `next_id` is an `INTEGER`; a `submission_requests` table records the
last writer of each request ID, as the in-memory store does; and the header
carries the identity. Migrations form an append-only list with contiguous
versions. `create` and `open` apply every pending migration in one `BEGIN
IMMEDIATE` transaction under the owner lock, together with the version in
`durable_schema` and in the header. A failing migration rolls back completely
and its error is raised; a crash leaves the previous version, and the next
`open` migrates again. A newer store is refused before anything is written. A
change to what stored bytes mean needs a new schema version even without new
tables or columns. S (DE04b1b2) appends semantic `Migration(2, ())`: current
deltas change stored meaning while v1 DDL and existing base bytes stay unchanged.
The actual delivered pre-schema2 opener refuses both a checkpointed version2
header and a WAL-only upgrade before modifying database/WAL bytes. Full history
remains DE27. A later read-only
inspection (DE10) validates without migrating.

**Integrity.** Every `open` requires the exact schema objects of the store's
version (no extra table, index, view or trigger, and each definition as the
migrations create it), a `durable_schema` version equal to the header's, and
the metadata row with its ID and sequence counters in range; otherwise it
raises `StorageCorruption`. After an unclean shutdown, recognized by a
non-empty `-wal` found under the lock before connecting, a read-only connection
first validates the recovered store the same way and runs `PRAGMA
quick_check`. A read-only connection never checkpoints, so its refusal leaves
the database and `-wal` bytes unchanged; only SQLite's `-shm` index is rebuilt.
After a clean shutdown `quick_check` is skipped (Root decision D6): its cost
grows with the store, and the Storage layer (slice DE03c) checks records as it
reads them. `PRAGMA integrity_check` stays an operator and test tool. Nothing
is repaired automatically. Damage SQLite itself reports at any step of opening,
migrating or closing a store (`SQLITE_CORRUPT` with its extended codes,
`SQLITE_NOTADB`) is also raised as `StorageCorruption`, with SQLite's error as
its cause and the same cleanup as any other failure; every other error is
raised unchanged.

**Records.** The store keeps the four record tables of the in-memory store
(conversations, entries, tasks and submissions) with the same behaviour (slice
DE03c1). Each record is compact JSON text in a `BLOB`, encoded as UTF-8 that
keeps lone surrogates, so it reads back exactly as written: signed zero, large
integers, key order and string spelling included, at any nesting depth. Task
kinds and request IDs are also stored as ASCII JSON in their index columns,
which compares them by UTF-16 code units as the in-memory store does. A commit
is one `BEGIN IMMEDIATE` transaction. A failure before `COMMIT` leaves nothing
behind and the store usable. An error from `COMMIT` itself, or a rollback that
fails, makes that instance refuse every later call until it is closed and the
store opened again, because only a reopen can tell what was committed. IDs
that were minted but not yet used by a commit are not stored; after a reopen
they can be minted again, as in Pi. A read that needs several statements sees
one committed state. Every read of a row makes every check: it decodes the
record, compares it with the row's index columns and, for an entry, checks its
commit sequence. Before anything is decoded, each column must hold exactly the
kind of value its declared type allows (integer, text or binary, and no value
only where the column may be empty); the strict tables keep that, but a schema
rewritten and restored through SQL can store anything. Stored text is never
decoded by Python's `sqlite3` module: it is read as bytes and decoded strictly,
so text that is not valid UTF-8 is damage too. Damage is reported as
`StorageCorruption`, never as an invalid request, its message never quotes
stored data, and nothing is repaired. A value of the wrong kind in an indexed
column also hides its row from lookups by that column, which SQLite never finds
equal; every read that reaches the row refuses it.

**Current base/delta documents (DE03c2 + DE04b1b2).** Create, base change, supplied
delta and retire participate in
that same mixed commit and global ID namespace. Current reads and indexed
address/scope scans have the in-memory store's exact semantics, including UTF-16
address identity, singleton versus empty family key, ascending document cursors
and raw page-limit behavior. Raw values and versions are separate lossless JSON
`BLOB`s: `0`, `-1`, `1.5`, `-0.0` and integers beyond 64 bits round-trip as in
memory (D2 (b)/(e)). Every document and revision column has the same storage-class,
strict text and corruption checks as the table reads. Materializing a document
reads its record, authoritative sequence bound and required newest base/tail
inside one snapshot, then replays actual stored operations in ascending order.
A delta requires an unretired matching-version object base, applies sequentially
to a detached candidate and requires a final object root. Even empty or
value-equivalent supplied delta commands increment `deltasSinceBase`; bases
reset it to zero. Numerically equal versions preserve the base version bits.
Every reached revision is checked for storage class, identity, sequence/order,
kind, version and valid content; malformed required rows raise content-free
`StorageCorruption` rather than falling back. Current-only documents retain the
required base/tail, reject stray rows before that base, and reclaim prior rows
on base reset or retirement. Rewindable older history is retained without
decoding irrelevant rows. Copy and numeric history remain unsupported (DE27),
and a batch containing an unsupported command is refused whole. Document records and
ID claims remain after retirement, so IDs cannot be reused. This does not
implement historical reconstruction or a complete retention proof (DE27).

A failure after `COMMIT` returned propagates while the durable rows and adopted
ID counter remain; reopening sees the commit. A `COMMIT` that raises still
poisons the instance (D4). Real read-only, page-count-limit (`SQLITE_FULL`) and foreign-lock refusals
roll back only while SQLite still has a transaction, adopt no counter and leave
the instance usable. Real-process SIGKILL cuts before, during and after a mixed
document commit recover either all committed rows or none, with integrity and
owner-only modes checked. These are process-death tests, not power-loss tests.

The bounded churn regression writes and replaces a 128,000-character base and
retires its document twenty times. With `secure_delete=ON`, its database stays
within 512 KiB and grows at most four pages after the first cycle; retained
retired records and ID claims still grow with the number of identities. This
workload bound is not a general constant-space guarantee. Close's TRUNCATE
checkpoint removes or empties the WAL. The actual paired Pi SQLite divergence
probes and the cumulative P43 workload remain DE03d obligations.

**Session integration evidence (DE03d1).** The existing internal sync/async
Session is tested over this real backend without adding a product factory.
Mixed tables/raw-base documents publish only after backend commit/adoption,
and a fresh Session reads them after reopen. A thrown acknowledgement after a
real COMMIT fails Session without publishing an unacknowledged change, while
reopen sees the durable rows; real read-only/page-limit/foreign-lock errors fail
Session and leave no partial commit. Close drains a held real commit, and a
fresh bounded process can own the store after normal or checkpoint-failing
close. The native current-base workload and its retention/history exclusions
are described in [the reference guide](durable-reference.md#native-current-base-sqlite-workload-de03d1).

**Interrupted create.** A crash during `create` before its schema commits
leaves an empty or zero-filled database, or a one-page database without
identity or schema. `open` refuses it (`InterruptedCreateError`) and `create`
refuses any existing database. Neither adopts it: nothing proves which process
made the file, and an empty foreign database looks the same (Pi, which opens or
creates, would adopt it). Recovery: while no process owns the store, delete the
database and any `-wal`, `-shm` and `-journal` next to it (the lock file may
stay), then run `create` again. No committed data can be lost, because a create
that committed its schema leaves a complete store that `open` recovers and
opens normally, even while its identity is still only in the `-wal`. A crash
while SQLite switches the new file to WAL can also leave a `-journal`; `open`
refuses it as a rollback journal, and the same procedure applies.

**Permissions.** Directories created for a store are `0700`. The database, its
SQLite sidecars and the lock file are `0600` whatever the umask; SQLite gives
its sidecars the database's mode. An existing file with group or other bits is
set to `0600` when it is a regular file owned by the user, readable and
writable by it and with a single link. Symbolic links, hard links, other users'
files and files the owner cannot read and write are refused. Existing parent
directories keep their permissions, so keep a store in a private directory.

**Ownership.** The owner holds an exclusive, non-blocking `flock` on
`<name>.lock` until it closes. A second owner, in another process or in the same
process through any path, is refused at once; nothing waits and nothing is
stolen by age. The kernel releases the lock when the owner exits or is killed.
The lock file holds diagnostic JSON (process ID, acquisition time, pipy
version) that only appears in error messages and never decides ownership. The
lock is not taken on the database itself: on macOS that makes SQLite report
"database is locked". A refused second open never opens the owned database,
because closing any descriptor of it would release the owner's SQLite locks;
for the same reason, do not open an owned store with another SQLite connection
from the owning process.

**Aliases.** The directory is resolved with `realpath`, so symbolic-link and
relative directories and `..` reach the same lock. On case- or
normalization-insensitive filesystems (APFS by default) case and Unicode
variants of the name reach the same lock file. The database name itself must
not be a symbolic link, and a database, sidecar or lock with a second hard link
is refused, because its other names would carry different lock and journal
names. Bind mounts of a single file, and deleting the lock file or hard-linking
the database while it is open, are outside this contract.

**Contract boundary.** The store directory is private to the owning user (owned
by that user, mode `0700`; missing directories are created that way, and keeping
an existing one private is the caller's part). While any owner holds a store,
from the moment an open or create of it starts until its close has completed,
renaming, replacing, hard-linking or deleting any store file (the database, its
`-wal`, `-shm` and `-journal`, and the lock file), by any actor, is outside the
contract. pipy detects some of these cases on a best-effort basis (the identity
checks under the lock, after connecting and after configuration; the parking of
descriptors that reach a claimed database; the re-check before each unlink of a
failed create) but guarantees nothing about them. SQLite's own guidance is the
same: do not rename, unlink or replace an open database or its journal. With the
store files left in place, the guarantees hold: one owner per store across
processes and within the process, owner-only file modes, no descriptor of a
store file held by another owner in this process is ever closed by pipy, and
invalid input, an `open` of a missing path and a store refused before its lock
is taken have no filesystem effect. Pi durable 1.3.0 has no cross-process
locking at all, so this remains strictly stronger than the reference.

**Processes and fork.** Every descriptor is close-on-exec. A child forked from
an owner closes its copy of the lock and cannot use the store. It also never
closes the inherited SQLite connection, not even at interpreter exit: closing it
after the owner's clean close would delete a later owner's write-ahead log by
name and lose committed transactions. A child forked by native code that skips
Python's fork hooks keeps a lock copy until it exits or executes another
program; the owner's close still releases the lock. The record storage refuses
a forked child the same way before it waits for anything: every call raises
`StorageForkedError` and close returns at once, even when another thread of
the parent was in the middle of a call at the moment of the fork.

**Unsupported filesystems.** Shared and network filesystems are refused before
any effect. On macOS the directory must be on a local APFS or HFS+ volume. On
Linux the directory's own mount must be ext2, ext3, ext4, XFS, Btrfs, F2FS,
tmpfs, ZFS, bcachefs, JFS, ReiserFS or NILFS2; NFS, SMB/CIFS, FUSE, 9p,
virtiofs, overlayfs, FAT, exFAT and unknown types are refused.

**Durability.** The store uses `synchronous=FULL`, plus `fullfsync` and
`checkpoint_fullfsync` on macOS, so a commit survives power loss when the
device honours flushes; new directory entries are flushed the same way. Pi's
`NORMAL`, where the newest commit may be lost on power or host failure, stays
available for parity and benchmark runs. Every other setting is explicit and
read back on open: WAL with a 1000-page automatic checkpoint, a 5 second busy
timeout, `temp_store=MEMORY` (no temporary on-disk copies of rows),
`trusted_schema=OFF` and `secure_delete=ON`. Close runs a TRUNCATE checkpoint,
then closes the connection, then releases the lock, each step even when the
previous one failed. SQLite 3.37 or newer in serialized threading mode is
required.

**Deleted content.** `secure_delete=ON` is set on every platform (Ubuntu's
SQLite enables it by default, the SQLite bundled with the macOS development
Python does not), so SQLite overwrites deleted rows and freed pages with zeros.
It does not reach older copies. In WAL mode the zeroed pages reach the database
file only at a checkpoint, so until then the file can still hold the earlier
page images; write-ahead-log frames written before a deletion keep them until a
checkpoint truncates the log. Every clean close runs that checkpoint; a crashed
owner's log keeps them until a later one. Blocks that the filesystem freed when
a file was truncated or removed are outside SQLite's control.

**Backup and deletion.** Back up a closed store (the database plus any `-wal`)
or use SQLite's backup API. To delete a store, remove the database and its
`-wal`, `-shm` and `-journal`; remove the lock file only while no process owns
the store.

## Future Direction

The current implementation stays file-based. Finalized JSONL and Markdown files
remain the source of truth, and the read-only catalog commands scan those files
directly. SQLite or another indexed store is future query/performance work
behind a `SessionRepository` port, not a capture-quality prerequisite for moving
on to the main coding-agent harness.

Future automatic-capture work should keep the adapter boundary explicit and
secondary to the native pipy runtime:

- prioritize the native pipy runtime path that owns prompt construction,
  provider calls, the tool boundary, and session semantics
- do not make `codex`, `claude`, or another coding-agent CLI the main product
  execution path; wrapping them would inherit their prompt stack, approval
  model, transcript shape, and execution loop
- add a Codex hook or subprocess adapter only if it is explicitly scoped as
  external-agent capture/reference work and can reliably map platform lifecycle
  events to pipy start/finalize semantics without overstating transcript
  completeness
- keep Pi bridging reference-only unless raw import becomes explicit opt-in with
  a concrete redaction policy
- add opt-in raw transcript import only with clear redaction behavior

Likely future abstractions:

- `SessionRecorder`
- `SessionRepository`
- `PromptRepository`
- `SkillRepository`
- `ReflectionRepository`

The application core should depend on these ports, while CLI, Textual, web, and external integrations provide adapters.

For task-slice ordering, use `docs/backlog.md`; this document remains the
source of truth for archive lifecycle and privacy constraints.

## Historical Native Runtime Archive Boundaries

Approval and sandbox records must stay metadata-only. The first native visible
approval prompt foundation does not add a JSONL event; it normalized decision
labels `pending`, `allowed`, `denied`, `skipped`, and `failed`, sandbox modes
`no-workspace-access`, `read-only-workspace`, and `mutating-workspace`, and the
capability booleans `workspace_read_allowed`,
`filesystem_mutation_allowed`, `shell_execution_allowed`, and
`network_access_allowed`. Read-only tools produce provider-visible repo context,
write tools or patch application may mutate only through explicit boundaries,
and verification commands. The current native verification boundary supports
only the `just-check` label mapped internally to `just check`. Approval
required/resolved booleans, `tool_request_id`, `turn_index`,
`duration_seconds`, exit codes, safe command labels, and storage booleans are
archive-safe; full file contents, shell commands, command output, raw args, and
attempted capability escalation must fail closed.
read-only tools produce provider-visible repo context.
approval required/resolved booleans.

Provider-visible repo context is not archive content. bounded explicit file
excerpts, bounded search-result excerpts, explicit per-turn workspace summaries,
and sanitized tool-observation summaries may be forwarded in memory only;
broad repo maps, unbounded file contents, and model-selected paths are dropped
or skipped before provider visibility. JSONL/Markdown/catalog surfaces record
metadata-only context fields, never raw excerpt text. The direct native explicit
file excerpt tool keeps successful excerpt text in memory only, and the runtime
may forward that text only to the one bounded follow-up provider turn.

The direct native explicit file excerpt tool keeps successful excerpt text in
memory only. The explicit file excerpt tool keeps successful excerpt text in
memory only and excludes raw excerpt text from the archive.

The now-removed proposal-only REPL boundary (`/propose-file`, `/apply-proposal`)
was part of the deleted no-tool REPL. The model now edits files through the
model-driven `write` / `edit` tools instead. When that boundary existed it
recorded a metadata-only `native.patch.proposal.recorded` event, labeled its
provider turn `propose_file_repl`, applied no edits itself, and kept raw patch
text out of provider lifecycle payloads. The model-driven mutation tools
(`write` / `edit`) do apply edits, but share that archive boundary: their raw
diffs and payloads never enter provider lifecycle payloads.


The internal DE03d2 comparison separately binds the unused-mint reopen lifetime:
Memory/live SQLite retains an unused local mint across a rejected commit;
closing/reopening discards that unstored reservation. The exact paired witness
and raw error/data boundaries are described in the
[closed SQLite oracle](durable-reference.md#closed-sqlite-oracle-and-lifetime-witness-de03d2).
It does not add delta/history capability or change the store contract.


### Typed current documents above SQLite (DE04a)

The private Session now resolves singleton/family addresses across session,
conversation and task scope. Existing schema-v1 fields already hold family keys
and task scope; DE04a changes no Storage signature or SQLite capability. Task
terminal settlement retires every current member in the same table/document
commit; cache adoption and publication follow Storage success. Real
checkpoint callbacks and line-owned unloading are described in
[the checkpoint contract](durable-session-checkpoints.md). DE04b1b3a activates actual normalized
default deltas through the existing Session authority, adopting persisted value
and committed count atomically and publishing the same immutable operation batch.
Empty normalized batches retain the prior snapshot; structural nonempty batches
still commit. Creation stores its actual final object as a base. See
[the Session delta contract](durable-session-deltas.md).
Historical delivered D1 workload/source hashes stay immutable identities of
that delivery; current closure hashes and migrated Session tests attest the
current tree separately. No private scratch path or runtime git history is
required to validate those historical identities.

Independent S (DE04b1b2) now implements [supplied current deltas and semantic
schema2](durable-current-deltas.md), delivered at `8c8f30d7` with [verified
gates](specs/2026-10-19-durable-execution-evidence.md#de04b1b2-verified-delivery-and-t-integration-successor).
Cold reads and delta preparation cost O(required current tail), and repeated
reconstruction can accumulate quadratic work. No cap, forced base, cache or
performance-parity bound is introduced. Structural validation does not detect
arbitrary external rewriting/deletion of otherwise-valid rows. Independent [T pure provenance/operations](durable-draft-tracker.md)
is an additive prerequisite and does not change Storage or activate Session.
J (DE04b1b3) retains the actual Draft/Tx/checkpoint/cache/
publication join. Owner policy266 selects the persisted/replayed value for
callbacks, cache and publications; default deltas are DE04b1b3a, real checkpoints
and actual unload DE04b1b3b. Storage itself chooses no authoritative Session
candidate. Historical raw-base oracle results retain their original measured
scope, rather than becoming new delta evidence.

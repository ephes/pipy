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

## Privacy

Session records must not include secrets, API keys, credentials, private keys, tokens, or sensitive personal data. Redact sensitive values before writing.

If raw tool output contains sensitive data, store a summary instead of the raw output.

## Partial Transcripts

Some coding-agent environments do not expose a complete raw transcript to the running agent. In that case, store a partial reconstruction and mark it clearly:

```json
{"type":"capture.limitations","summary":"Partial reconstruction from visible conversation context; no raw platform transcript export was available."}
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

Automatic capture from Codex, Claude, and Pi is not implemented yet. The next implementation slice should add platform-specific wrappers or hooks that write active records under `.in-progress/<project>/`, finalize them at session end, and leave finalized records for `just sessions-sync`.

Likely future abstractions:

- `SessionRecorder`
- `SessionRepository`
- `PromptRepository`
- `SkillRepository`
- `ReflectionRepository`

The application core should depend on these ports, while CLI, Textual, web, and external integrations provide adapters.

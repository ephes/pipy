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

Full raw transcript capture is a separate adapter capability from summary-safe
learning events. When a platform exposes a reliable full-session export, pipy
may store or reference that private raw artifact under the same local session
root, outside git. Commands such as `search`, `inspect`, `verify`, and
`reflect` must still default to finalized metadata, event types, event
summaries, Markdown summaries, and explicitly allowlisted learning fields rather
than raw transcript bodies. This keeps complete capture useful for private
forensics while preserving safe day-to-day reflection.

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

Record workflow-learning details explicitly when the session should teach future
agents about model, role, review, or subagent choices:

```sh
uv run pipy-session workflow role <active-path> \
  --role implementer \
  --agent codex \
  --model gpt-5.3-codex \
  --phase implementation

uv run pipy-session workflow role <active-path> \
  --role reviewer \
  --agent claude \
  --model claude-opus \
  --phase review

uv run pipy-session workflow review-outcome <active-path> \
  --implementer-agent codex \
  --implementer-model gpt-5.3-codex \
  --reviewer-agent claude \
  --reviewer-model claude-opus \
  --high 1 --medium 2 --low 4 \
  --accepted 7 --fixed 7 --rejected 0 --deferred 0

uv run pipy-session workflow evaluation <active-path> \
  --pattern codex-implementation-claude-opus-review \
  --confidence medium \
  --recommendation keep-testing \
  --summary "Reviewer found lifecycle risks implementer missed."
```

Use `workflow subagent` when delegation materially affects the result:

```sh
uv run pipy-session workflow subagent <active-path> \
  --role explorer \
  --agent codex \
  --model gpt-5.3-codex \
  --task-kind review-support \
  --outcome findings-used
```

These commands append summary-safe events such as `workflow.role`,
`review.outcome`, `workflow.evaluation`, and `subagent.used`. Automatic
adapters also append `model.used` when a model identifier is exposed safely by
hook metadata or wrapper argv. The generated summaries are intentionally
searchable by `pipy-session search` and surfaced by `pipy-session reflect`. Do
not include prompts, transcript bodies, tool output, secrets, credentials, or
sensitive personal data in model, role, outcome, or summary fields.

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
because they are intentional human-review artifacts. Human metadata and event
type labels collapse control whitespace so malformed archive values cannot forge
extra physical output lines; JSON output preserves structured values.

Reflect on finalized records without indexing or mutating them:

```sh
uv run pipy-session reflect
uv run pipy-session reflect --json
```

`reflect` builds a summary-safe learning report over finalized archive records.
It uses the same finalized-record discovery rules as `list`, skips malformed or
unreadable records quietly, and does not read active records, automatic state
files, `*.partial` staging files, or arbitrary files outside the archive.

The report includes:

- record counts by agent and capture marker
- total Markdown-summary coverage
- event type counts
- count of low-signal partial captures that contain only lifecycle or hook
  metadata
- curated learning items from event `summary` strings for event types such as
  `decision.recorded`, `lesson.learned`, `recommendation.recorded`,
  `model.used`, `workflow.role`, `subagent.used`, `review.findings`,
  `review.outcome`, `review.followup.completed`, `workflow.evaluation`,
  `implementation.completed`, `file.changed`, `verification.performed`, and
  `research.performed`
- short Markdown summary snippets when they are not generic automatic-capture
  summaries

Human output is Markdown intended for review. JSON output is a structured report
with the same fields. Neither output includes raw JSONL event bodies, payload
values, prompt text, tool output, transcript bodies, raw invalid bytes, or raw
exception messages. The command does not repair, delete, move, rewrite, index,
import, cache, sync, or promote session records. Use the report as an input to
explicit promotion work: ADRs, curated lessons, prompts, hooks, skills, or
documentation changes that intentionally belong in git.

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

## Pipy Run Harness

The top-level `pipy` CLI is the initial product harness surface. Its first
implemented command is `pipy run`. It can run one arbitrary subprocess command
or one minimal native pipy bootstrap turn, records conservative lifecycle
metadata through the same recorder lifecycle, finalizes the record, and returns
the adapter exit code. The first interactive native shell is `pipy repl
--agent pipy-native`; it uses the same recorder lifecycle for bounded provider
turns plus explicit approved `/read` and `/ask-file` read-only commands.

```sh
uv run pipy run --agent custom --slug smoke -- echo hello
uv run pipy run --agent custom --slug smoke --root /tmp/pipy-sessions --cwd . -- echo hello
uv run pipy run --agent pipy-native --slug native-smoke --root /tmp/pipy-sessions --goal "Native bootstrap smoke"
uv run pipy run --agent pipy-native --native-provider openrouter --native-model <provider/model> --slug openrouter-smoke --goal "Say hello briefly"
uv run pipy repl --agent pipy-native --slug native-repl --root /tmp/pipy-sessions
```

`pipy run` creates partial records. The harness does not import raw transcripts
and does not store child stdout, child stderr, full system prompts, prompt text,
model output, full argv, diffs, tool payloads, or file contents. It records safe
lifecycle metadata such as:

- `run_id`, `event_id`, `sequence`, and `harness_protocol_version`
- logical agent and adapter name
- workspace basename plus SHA-256 hash of the resolved workspace path
- process start and exit events
- run status and exit code
- capture policy markers such as `argv_stored=false`, `stdout_stored=false`,
  `stderr_stored=false`, and `raw_transcript_imported=false`
- native bootstrap markers such as `system_prompt_id`,
  `system_prompt_version`, `prompt_stored=false`,
  `model_output_stored=false`, and `tool_payloads_stored=false`

`--record-files` is the only file-path capture option in this slice. When set,
the harness runs `git status --porcelain` in the selected `--cwd` after the
child exits and records relative changed file paths only. It does not store
diffs or file contents. Non-git directories are handled without failing the
run. Relative path strings are normalized to collapse control whitespace, but
ordinary filenames such as `secret_config.py` or `auth_token.py` are preserved
because path recording is already explicit opt-in. Without `--record-files`,
changed paths are not recorded.

The subprocess adapter inherits stdin from the parent process. This keeps
generic commands and future agent CLIs usable when they intentionally read from
stdin, but stdin content is not captured by pipy.

The native bootstrap adapter is selected with `--agent pipy-native`. It owns
system prompt construction, calls one initial provider, and invokes a native
tool boundary only when the provider returns one sanitized supported intent.
The no-op path remains fixture-gated and uses the deterministic fake no-op
tool. The read-only path is also fixture-gated: one supported pipy-owned
explicit-file-excerpt fixture can run the bounded read-only file tool, emit
metadata-only tool and observation events, and send the successful excerpt only
as in-memory provider-visible context to exactly one follow-up provider call.
The fake provider and fake no-op tool are for tests and smoke runs, not a
production AI/tool runtime. The runtime does not execute shell commands, mutate
the workspace, or run provider-side tools. Provider final text prints to stdout
through the explicit CLI contract when the native run succeeds. API-key
providers currently include `openai` with `OPENAI_API_KEY` and `openrouter`
with `OPENROUTER_API_KEY`; both require an explicit `--native-model` and do not
add credential storage. The distinct `openai-codex` provider is selected with
`--native-provider openai-codex --native-model <model>` and uses pipy-owned
OAuth state from
`${PIPY_AUTH_DIR:-~/.local/state/pipy/auth}/openai-codex.json`, created by
`pipy auth openai-codex login` or the native REPL `/login openai-codex`
command with private file permissions. The native REPL may also persist the
selected provider/model default as non-secret JSON under local pipy state, for
example `~/.local/state/pipy/native-defaults.json`; that file stores only
provider and model identifiers, not auth material. Pipy does not read or copy
Pi's `~/.pi/agent/auth.json`, and auth material from the `openai-codex`
credential file is not copied into JSONL or Markdown archives.
JSONL and Markdown archive records store only provider/session/tool lifecycle
metadata, safe labels, durations, normalized usage counters, policy labels, and
storage booleans. Normalized provider usage is limited to finite non-negative
`input_tokens`, `output_tokens`, `total_tokens`, `cached_tokens`, and
`reasoning_tokens`; unknown provider-native usage keys and unavailable counters
are omitted. Native runs require `--goal`; that field remains user-visible
archive metadata, so keep it short and non-sensitive.

The native REPL is selected with `pipy repl --agent pipy-native`. It reads input
lines from stdin, sends each non-empty non-command line to the selected provider
as one provider turn, prints successful provider final text to stdout, and keeps
prompts, help, approval prompts, diagnostics, finalization, interrupt handling,
command-skip messages, and turn-limit notices on stderr. `/help` prints only
static supported command usage without invoking the provider or tools. `/exit`
and `/quit` terminate cleanly. `/read <workspace-relative-path>` runs the
bounded explicit-file-excerpt read without a visible approval prompt, prints a
successful excerpt only to interactive stdout, and does not provider-forward
the excerpt. `/ask-file <workspace-relative-path> -- <question>` uses a
whitespace-delimited `--` separator and shares the same one-read per-session
limit and read path, but forwards the successful excerpt plus question only in
memory to one provider turn labeled `ask_file_repl`; it prints only provider
final text to stdout. `/propose-file <workspace-relative-path> --
<change-request>` uses the same separator and shared one-read path, forwards
the successful excerpt plus change request only in memory to one provider turn
labeled `propose_file_repl`, parses at most one pipy-owned
structured patch proposal metadata object, records at most one metadata-only
`native.patch.proposal.recorded` event, and prints only provider final text to
stdout. `/apply-proposal <workspace-relative-path>` is available only after a
successful same-session `/propose-file` for the exact same normalized path. It
uses one pending in-memory, human-reviewed proposal draft to invoke the
existing patch-apply boundary, emits only `native.patch.apply.recorded` when it
reaches that tool, prints only safe status diagnostics to stderr, and does not
call a provider or run verification itself. `/verify just-check` is available
only after a successful same-session `/apply-proposal` mutation. It maps the
safe label `just-check` to the internal `just check` argv through the existing
verification boundary, emits only `native.verification.recorded`, suppresses
command stdout and stderr, and fails the REPL run when verification is skipped
or fails. Malformed supported slash commands and unsupported slash commands
print static usage diagnostics on stderr without provider/tool execution,
read-limit consumption, tool events, or raw command archiving. REPL session
records use the same metadata-only lifecycle vocabulary and do not archive raw
input lines, provider final text, provider metadata, provider-returned tool
intent markers, raw approval prompts, raw tool arguments, raw tool results,
stdout, stderr, prompts, model output, provider responses, provider-native
payloads, repo context, patches, command output, full file contents, auth
material, secrets, credentials, tokens, private keys, or sensitive personal
data. The REPL still does not run arbitrary shell commands, enable
provider-side tools, create another provider turn after proposal parsing,
persist repo context, accept non-allowlisted verification commands, or expose a
general model/tool loop. In particular, `/propose-file` must not apply edits or
store raw proposal data, and `/apply-proposal` must not read proposal data back
from archives.

The native intent path remains bounded to one initial provider turn, at most
one no-op or explicit-file-excerpt read-only tool invocation, and at most one
fixture-gated follow-up provider turn. The
`native.tool.observation.recorded` event is anchored to pipy's
`tool_request_id` and `turn_index`; there is no observation `started` event
because the observation is derived metadata, not a raw output handling phase.
Unsafe or unsupported observation or read-only context fixture data is skipped
and fails closed before provider visibility.

The proposal-only REPL boundary is available as
`/propose-file <workspace-relative-path> -- <change-request>`. It is the
interactive counterpart to the metadata-only patch proposal event: it forwards
only one approved in-memory excerpt and one change request to
`propose_file_repl`, then stops after optional proposal metadata parsing.

The first public write-capable REPL boundary is available as
`/apply-proposal <workspace-relative-path>`. The command may consume only one
same-session, in-memory, human-reviewed proposal draft for the exact same
normalized workspace-relative path and normalize it into one
`NativePatchApplyRequest`. It must not read raw proposal text, patch text,
diffs, replacement file contents, prompts, model output, provider responses, or
provider metadata back from JSONL, Markdown, catalog/search/inspect surfaces,
or structured stdout. The archive surface for the command is only the existing
metadata-only `native.patch.apply.recorded` event.
If provider final text contains a visible apply draft but no structured
proposal metadata, the REPL may keep only the pending in-memory apply draft; it
must not synthesize `native.patch.proposal.recorded`. Local REPL commands,
unsupported slash commands, provider failures, later provider-visible turns,
and any apply attempt clear the pending draft so archive surfaces never become
the source of apply data.

After a successful explicit-file-excerpt read-only observation and successful
follow-up provider turn, the native runtime may record one metadata-only patch
proposal event before writes. `native.patch.proposal.recorded` stores only
pipy-owned `tool_request_id`, `turn_index`, proposal `status`, safe
`reason_label`, file and operation counts, closed operation labels, and storage
booleans such as `patch_text_stored=false`, `diffs_stored=false`,
`file_contents_stored=false`, `prompt_stored=false`,
`model_output_stored=false`, `provider_responses_stored=false`,
`raw_transcript_imported=false`, and `workspace_mutated=false`. It does not
apply edits, mutate files, run shell commands, run verification commands, or
create another provider turn.

JSONL and Markdown records may contain only the allowlisted observation
metadata: `tool_request_id`, `turn_index`, safe tool name/kind labels, terminal
`status`, safe `reason_label`, `duration_seconds`, and explicit storage booleans
for tool payloads, stdout, stderr, diffs, file contents, prompts, model output,
provider responses, and raw transcript import. The first observation event shape
does not include normalized counters or optional metadata; those require a later
explicit schema update. Observation records must not contain raw tool result
payloads, stdout, stderr, diffs, patches, file contents, prompts, model output,
provider responses, provider-native tool-call or tool-result payloads, function
arguments, provider response ids that could reveal payload content, raw tool
arguments, shell commands, model-selected filesystem paths, secrets,
credentials, tokens, private keys, or sensitive personal data by default.

Patch proposal records must also remain metadata-only. They must not contain
raw patch text, raw diffs, file contents, model-selected paths, raw prompts,
model output, provider responses, provider-native payloads, raw tool payloads,
stdout, stderr, shell commands, auth material, secrets, credentials, tokens,
private keys, or sensitive personal data. REPL proposal records must also omit
raw provider proposal objects, raw provider metadata, raw approval prompts, raw
tool arguments, raw tool results, command output, and replacement file
contents. Unsafe or unsupported proposal data is dropped before persistence and
may be represented only by a skipped proposal event with a safe reason label.

Patch apply records must remain metadata-only as well. An `/apply-proposal`
record may store only pipy-owned `tool_request_id`,
`turn_index`, terminal status and reason labels, duration, file and operation
counts, closed operation labels, approval/sandbox labels and booleans,
`workspace_mutated`, optional safe scope labels, and false storage booleans for
patch text, diffs, file contents, prompts, model output, provider responses,
and raw transcript import. It must not store raw proposal text, raw patch text,
raw diffs, replacement file contents, target paths, prompts, model output,
provider responses, provider-native payloads, raw provider metadata, raw tool
payloads, stdout, stderr, command output, shell commands, auth material,
secrets, credentials, API keys, tokens, private keys, or sensitive personal
data. Verification remains manual for the first public write-capable command;
the later verification-command boundary must record only its own safe
metadata-only status if implemented.

Provider-visible repo context is not archive content. The context policy in
`docs/harness-spec.md` allows only bounded explicit file excerpts, bounded
search-result excerpts, explicit per-turn workspace summaries, short
user-provided goal metadata, and sanitized tool-observation summaries to become
provider input after approval and sandbox checks exist. The implemented native
runtime path currently supports only one bounded explicit file excerpt through
supported pipy-owned fixture data. It forbids broad
repo maps, unbounded file contents, persistent workspace summaries, raw diffs
or patches, raw stdout or stderr, shell command output, raw tool payloads, raw
tool arguments, provider-native payloads, raw provider responses, model output,
prompt fragments, model-selected paths, secrets, credentials, API keys, tokens,
private keys, and sensitive personal data.

When bounded repo context is produced, unsafe data must be dropped or skipped
before provider visibility and before any archive event is written. Binary or
unreadable content, unsupported encodings, generated files, ignored files,
oversized files, secret-looking content, and excerpts that cannot be proven
within limit must fail closed with safe skip or failure metadata. JSONL,
Markdown, and `--native-output json` may record only metadata-only context
fields such as source labels, counts, byte and line counts, excerpt counts,
distinct file counts, redaction and skipped booleans, safe reason labels,
`duration_seconds`, storage booleans, `tool_request_id`, `turn_index`, and
finalized-record references. They must not store raw excerpt text, file
contents, search result text, raw prompts, model output, provider responses,
raw tool payloads, stdout, stderr, diffs, patches, shell commands, raw args,
model-selected paths, secrets, credentials, tokens, private keys, or sensitive
personal data. The direct native explicit file excerpt tool keeps successful
excerpt text in memory only and exposes a separate metadata-only helper; the
runtime may forward that text only to the one bounded follow-up provider turn
and never archives it.

Approval and sandbox records must stay metadata-only. The enforcement baseline
in `docs/harness-spec.md` defines approval decision labels as
`pending`, `allowed`, `denied`, `skipped`, and `failed`; sandbox mode labels as
`no-workspace-access`, `read-only-workspace`, and `mutating-workspace`; and
independent capability booleans for `workspace_read_allowed`,
`filesystem_mutation_allowed`, `shell_execution_allowed`, and
`network_access_allowed`. Approval is required before future read-only tools
produce provider-visible repo context, before write tools or patch application
mutate the workspace, before shell execution, before network access, and before
verification commands. The current native verification boundary supports only
the pipy-owned `just-check` label mapped internally to `just check` after
explicit approval.

The first native visible approval prompt foundation resolves read-only
workspace approval through injected streams and maps the result to the existing
`NativeReadOnlyGateDecision`. That helper does not add a JSONL event, Markdown
field, catalog-visible field, or `--native-output json` field in this slice.
If approval or sandbox gates are archived later, JSONL, Markdown, and
`--native-output json` may record only policy labels, approval
required/resolved booleans, decision labels, safe reason labels, capability
booleans, `tool_request_id`, `turn_index`, safe tool name/kind labels, status,
`duration_seconds`, counts, byte and line counts, exit codes, safe command
labels, storage booleans, and optional finalized-record references. They must
not store raw prompts, model output, provider responses, provider-native
payloads, raw tool payloads, stdout, stderr, command output, diffs, patches,
full file contents, shell commands, raw args, model-selected paths,
provider-selected paths as authority, secrets, credentials, API keys, tokens,
private keys, or sensitive personal data.
Missing policy, unsupported policy or sandbox modes, denied approval,
unavailable approval UI, sandbox mismatch, unsafe request data, model-selected
paths, and attempted capability escalation must fail closed before execution
and before any provider-visible context is produced.

The native read-only request value objects are contract data, not archive
content and not execution records. They may name safe request kind labels,
bounded limit metadata, pipy-owned `tool_request_id` and `turn_index`, required
approval policy, read-only sandbox policy, capability booleans, optional safe
scope labels, and false storage booleans. They still must not store raw prompts,
model output, provider responses, raw tool payloads, stdout, stderr, diffs,
patches, file contents, excerpt text, search result text, shell commands, raw
args, model-selected paths, provider-selected paths as authority, secrets,
credentials, API keys, tokens, private keys, or sensitive personal data. The
direct native explicit file excerpt tool consumes these request objects only
with explicit pipy-owned gate and target data; default `pipy-native` session
records do not archive or execute read-only requests.

The native stdout/stderr split is part of the storage privacy contract:
successful provider final text is terminal output, not archived session data.
Session finalization messages, diagnostics, and errors go to stderr. Failed
native runs do not print provider final text to stdout.

For the REPL, each successful provider turn follows the same split: provider
final text prints to stdout, while the `pipy-native>` prompt and all
harness/session messages stay on stderr. `/help` and static usage diagnostics
for malformed or unsupported slash commands also stay on stderr without
provider/tool execution. Successful `/read` excerpt text prints only to
interactive stdout. Successful `/ask-file` and `/propose-file` excerpt text is
forwarded only in memory to one provider turn; only that provider final text
prints to stdout. The REPL does not add structured stdout, JSONL event
streaming, transcript export, raw conversation storage, or raw excerpt storage.

Structured native stdout is available only through explicit
`--native-output json` on `--agent pipy-native`. Non-native runs reject
`--native-output` before creating a record. JSON mode follows the same
metadata-only boundary: it emits one final versioned JSON object, not a JSONL
event stream, only after the native run and recorder finalization attempt
complete. Allowed fields are summary-safe status metadata: schema/version,
run id, status, exit code, agent, adapter/provider/model labels, duration,
normalized usage counters when available, storage booleans such as
`prompt_stored=false` and `model_output_stored=false`, and finalized-record
references that do not reveal raw record contents. It must not expose raw
prompts, model output, provider responses, provider-native payloads, tool
arguments, tool results, stdout, stderr, diffs, file contents, secrets,
credentials, tokens, private keys, or sensitive personal data by default.

The first harness event stream follows this shape:

```text
session.started
capture.limitations
harness.run.started
agent.process.started
agent.process.exited
workspace.files.changed         # only when --record-files finds changed paths
harness.run.completed | harness.run.failed | harness.run.aborted
session.finalized
```

One-shot native runs add a small native lifecycle vocabulary before the final
harness completion event:

```text
native.session.started
native.provider.started
native.provider.completed | native.provider.failed
native.tool.intent.detected        # only for a safe supported intent
native.tool.started
native.tool.completed | native.tool.failed | native.tool.skipped
native.tool.observation.recorded   # only for bounded supported observations
native.patch.proposal.recorded     # only after read-only follow-up metadata
native.session.completed
```

`native.tool.started` is emitted only when a safe supported no-op intent causes
the no-op tool to be invoked. Provider successes with no intent complete without
tool events. Provider failures and provider successes with unsupported or unsafe
intent data record `native.tool.skipped` with safe reason metadata instead of
emitting `native.tool.intent.detected` or `native.tool.started`. Safe no-op
tool success is followed directly by `native.session.completed`; there is no
post-tool `native.provider.started` event in the current contract.

REPL records use the existing session and provider lifecycle events for
ordinary non-command provider turns:

```text
native.session.started
native.provider.started              # once per non-empty input line
native.provider.completed | native.provider.failed
native.session.completed
```

The explicit `/read` command may add one metadata-only read-tool lifecycle:

```text
native.tool.started
native.tool.completed | native.tool.skipped | native.tool.failed
```

The explicit `/ask-file` command uses the same read-tool lifecycle and, after a
successful approved excerpt, may add one metadata-only observation event plus
one provider turn:

```text
native.tool.started
native.tool.completed
native.tool.observation.recorded
native.provider.started              # label ask_file_repl
native.provider.completed | native.provider.failed
```

The `/propose-file` command uses the same read-tool lifecycle and, after one
successful approved excerpt, may add one provider turn plus one metadata-only
proposal event:

```text
native.tool.started
native.tool.completed
native.tool.observation.recorded
native.provider.started              # label propose_file_repl
native.provider.completed | native.provider.failed
native.patch.proposal.recorded
```

The `/apply-proposal` command may add only one metadata-only
patch apply event after a same-session pending proposal has been reviewed by
the user and normalized into one pipy-owned one-file request:

```text
native.patch.apply.recorded
```

The `/verify just-check` command may add only one metadata-only verification
event after a successful same-session `/apply-proposal` mutation:

```text
native.verification.recorded
```

The event must not include command stdout, command stderr, shell text, prompts,
model output, provider responses, patch text, diffs, file contents, auth
material, secrets, credentials, tokens, private keys, or sensitive personal
data.

REPL provider lifecycle payloads include provider turn indexes and labels from
`NativeConversationState`, but they intentionally store
`provider_metadata={}` so provider-returned tool intent markers or raw provider
details cannot become archive content. The `/propose-file` boundary may inspect
only the pipy-owned structured proposal metadata key in memory after the
provider turn; that raw provider metadata must not be copied into provider
lifecycle payloads, JSONL, Markdown, catalog surfaces, or structured stdout.

The current native identity and observation planning boundaries do not change
archive lifecycle or privacy policy. `turn_index` is a pipy-assigned small
non-negative integer for the provider turn that produced a sanitized internal
tool intent; the current bounded runtime may archive only `turn_index=0`.
`request_id` is an opaque pipy-generated id used internally to correlate one
safe tool intent with its matching no-op lifecycle events inside a single
record, and lifecycle payloads expose that id as `tool_request_id`. It must not
be copied from provider-native tool-call ids or derived from prompt text, model
output, provider responses, raw tool arguments, shell commands, filesystem
paths selected by the model, stdout, stderr, diffs, patches, file contents,
secrets, credentials, private keys, tokens, or sensitive personal data. Any
future post-tool observation remains metadata-only: safe labels, terminal
status, duration, storage booleans, and sanitized reason labels. The first
future lifecycle shape is a single `native.tool.observation.recorded` event;
live emission, archive writes for real observations, provider forwarding, and
post-tool provider turns remain out of scope.

`session.finalized` is appended while the JSONL record is still active. The
recorder then moves the JSONL and Markdown summary into the finalized archive
under `pipy/YYYY/MM/`. Records produced by `pipy run` are compatible with
`pipy-session verify`, `list`, `search`, `inspect`, and `reflect`.

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
uv run pipy-session auto reference-pi ~/.pi/agent/sessions/session.jsonl --slug pi-session-note
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

Codex and Pi currently use wrapper-based or reference-based pipy capture unless
a future adapter adds a verified lifecycle bridge:

- Codex: current official Codex docs and local CLI support include hooks, and
  the local `codex_hooks` feature is stable and enabled, but the documented
  `Stop` hook is turn-scoped rather than a reliable local session finalizer.
  Pipy therefore does not install `pipy-session auto hook codex` in this slice.
  Use `pipy-session wrap --agent codex -- ...` for partial lifecycle metadata.
- Pi: Pi already auto-saves its own JSONL sessions under `~/.pi/agent/sessions/`.
  Use `pipy-session auto reference-pi <pi-session-path>` when you want a
  finalized pipy record that points at a Pi-native session file without copying
  raw transcript content. Use `pipy-session wrap --agent pi -- ...` only when
  you want a pipy-side partial lifecycle marker around the Pi process.

Records created by these automatic commands are partial unless `auto start
--complete` is used by an adapter that truly captures a complete transcript.
Do not use `--complete` for the Claude, Codex wrapper, or Pi wrapper flows
documented here.

### Pi-Native Session References

`auto reference-pi` is a conservative bridge for cases where Pi already wrote a
native session file and pipy only needs a durable pointer:

```sh
uv run pipy-session auto reference-pi ~/.pi/agent/sessions/session.jsonl --slug pi-session-note
```

The command requires an explicit existing file path and rejects missing paths or
directories. It creates and immediately finalizes a partial `agent="pi"` pipy
record. The JSONL record stores only conservative metadata:

- adapter name
- source filename
- source file size
- source mtime
- SHA-256 hash of the resolved absolute source path
- markers that the source path itself was not stored and raw content was not imported

The command does not read, copy, print, or store the Pi-native JSONL body or the
absolute source path. The Markdown summary explicitly says the pipy record is a
reference to a Pi-native session, not a transcript import.

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

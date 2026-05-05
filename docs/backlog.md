# Pipy Backlog

Status: planning index

This backlog records the current implementation direction at a task-slice
level. It is not a full issue tracker. Use it to choose the next small,
reviewable change while keeping the source-of-truth design constraints in
`docs/harness-spec.md` and `docs/session-storage.md`.

## Done

- Durable file-based session archive with active/finalized lifecycle.
- `pipy-session` recorder, catalog, search, inspect, reflect, verify, sync, and
  conservative automatic-capture helpers.
- `pipy run` subprocess harness with lifecycle recording, conservative privacy
  defaults, changed-file path opt-in, and finalized partial records.
- Native pipy runtime bootstrap behind `--agent pipy-native` with a minimal
  provider/session boundary, internal system prompt construction, deterministic
  fake provider, and privacy-safe native lifecycle events.
- Native OpenAI Responses provider behind `ProviderPort`, selected with
  `--native-provider openai --native-model <model>`, using `OPENAI_API_KEY`,
  an injectable standard-library HTTP boundary, `store: false`, stdout-only
  final text, and privacy-safe partial archive metadata.
- Native tool boundary with explicit request/result/status, approval policy, and
  sandbox policy data, plus a deterministic fake no-op tool path that emits
  privacy-safe lifecycle events without inspecting or mutating the workspace.
- Native loop planning documented: future provider output becomes a sanitized
  internal tool-request intent before any tool lifecycle event is recorded, with
  fake/no-op execution only, metadata-only archives, and real execution,
  approvals, sandbox enforcement, retries, streaming, fallback, OAuth, provider
  registry, raw transcript import, and orchestration deferred.
- Native fake tool-intent path: fake providers can deterministically expose one
  sanitized internal no-op intent through explicit fixture metadata; native
  sessions invoke the injected no-op tool only for that safe intent, while
  provider success with no intent completes without tool events and
  unsafe/unsupported intent data is skipped with metadata-only lifecycle
  records.
- Native provider usage metadata normalization: native archives allow only
  finite non-negative normalized `input_tokens`, `output_tokens`,
  `total_tokens`, `cached_tokens`, and `reasoning_tokens` counters;
  provider-native raw usage payloads and unavailable counters are omitted
  rather than guessed.
- Native final text stdout mode decision: successful native provider final text
  remains the default human-readable stdout payload, while session
  finalization, diagnostics, progress, and errors stay on stderr; structured
  machine-readable native stdout is deferred to a future explicit flag, and
  archives remain metadata-only without model output.
- Native post-tool provider turn decision: the current native fake intent path
  remains bounded to one provider turn plus optional one fake no-op tool
  invocation; any post-tool provider turn is deferred until permission prompts,
  sandbox enforcement, and real tool-result observation semantics are designed,
  and future metadata must remain summary-safe and metadata-only.

## Next Slice

### Native Structured Stdout Flag Contract Decision

Goal: decide whether a future structured native stdout flag should emit JSON
status records, while preserving the current successful-provider-final-text
human-readable stdout default.

Candidate shape:

- document the flag name and whether output is JSON object, JSONL event stream,
  or another explicitly versioned machine-readable shape
- preserve the default native stdout contract: successful provider final text
  only, with diagnostics, finalization, progress, and errors on stderr
- define how structured stdout relates to archive metadata without storing raw
  prompts, model output, provider responses, tool payloads, stdout, stderr,
  diffs, or file contents
- keep records compatible with `pipy-session verify`, `list`, `search`,
  `inspect`, and `reflect`

Keep out of scope:

- changing the current human-readable default stdout mode
- streaming
- retries or model fallback
- real filesystem or shell tool execution
- approval prompts or sandbox enforcement
- post-tool provider turns or a general model/tool loop
- provider-side built-in tools
- raw prompt/model output storage in JSONL, Markdown, or structured stdout
- raw provider responses, tool payloads, stdout, stderr, diffs, file contents,
  secrets, credentials, or sensitive personal data in JSONL, Markdown, or
  structured stdout
- Codex, Claude, Pi, or another CLI wrapper as the main product path

Acceptance checks:

- docs clearly describe whether structured native stdout is deferred or selected
  for implementation and what its contract would be
- native default stdout remains successful final text only
- native behavior still uses the bounded deterministic fake/no-op execution path
- native records still pass `pipy-session verify`
- `pipy-session list`, `search`, and `inspect` stay compatible
- raw system prompts, user prompts beyond the short `--goal` metadata, model
  output, provider responses, tool payloads, stdout, stderr, diffs, file
  contents, secrets, and credentials are not persisted or emitted in structured
  automation output by default

## Near Term

- Decide the next native runtime boundary after structured stdout, keeping real
  execution, approvals, sandboxing, and broader model/tool loops deferred until
  their contracts are explicit.

## Deferred

- Full native pipy agent runtime beyond the provider and tool-boundary slices.
- Codex JSONL event adapter.
- Claude integration beyond the existing conservative `pipy-session auto`
  metadata capture.
- Pi-native session inspection beyond metadata references.
- Raw transcript import with explicit opt-in and redaction policy.
- Indexed archive search or SQLite-backed query layer.
- Repo maps or workspace summaries.
- Interactive TUI.
- RPC mode.
- Multi-agent task delegation.
- Long-running dev server.
- Docs server such as Zensical.

## Explicitly Not Now

- Making Codex, Claude, or another coding-agent CLI wrapper the main product
  path.
- Storing full system prompts, user prompts, model outputs, stdout, stderr,
  tool payloads, secrets, tokens, credentials, private keys, or sensitive
  personal data by default.
- Building approvals, sandboxing, retries, streaming, OAuth, provider registry,
  raw transcript import, TUI, RPC, compaction, branching, or orchestration in
  the next native slice.

## Maintenance Notes

- Move completed slices from `Next Slice` or `Near Term` to `Done` in the same
  change that implements them.
- Keep deferred items here brief; put detailed design and rationale in
  `docs/harness-spec.md`.
- Keep archive and privacy rules aligned with `docs/session-storage.md`.

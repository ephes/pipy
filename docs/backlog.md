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

## Next Slice

### Native Post-Tool Provider Turn Decision

Goal: decide when a post-tool provider turn is useful after the fake intent
path, while keeping execution fake until permission prompts and sandbox
enforcement are designed.

Candidate shape:

- document whether the current one-provider-turn fake tool path should remain
  bounded or gain a second provider turn after a no-op tool result
- keep the fake/no-op execution boundary; do not add real filesystem, shell, or
  provider-side tool execution
- define what summary-safe metadata a post-tool turn would need before any
  implementation
- preserve successful final text stdout and metadata-only archive behavior

Keep out of scope:

- OAuth or a provider registry
- retries, streaming, or model fallback
- real filesystem or shell tool execution
- approval prompts or sandbox enforcement
- provider-side built-in tools such as web search, file search, code
  interpreter, computer use, or background mode
- raw prompt/model output storage in JSONL or Markdown
- raw provider tool-call payloads, tool arguments, stdout, stderr, diffs, or
  file contents in JSONL or Markdown
- Codex, Claude, or Pi CLI wrapping as the main product path

Acceptance checks:

- docs clearly describe whether a post-tool provider turn is deferred or
  selected for the fake intent path
- native behavior still uses deterministic fake/no-op execution only
- native records still pass `pipy-session verify`
- `pipy-session list`, `search`, and `inspect` stay compatible
- raw system prompts, user prompts beyond the short `--goal` metadata, model
  output, provider responses, tool payloads, stdout, stderr, diffs, file
  contents, secrets, and credentials are not persisted by default

## Near Term

- Decide whether a future structured native stdout flag should emit JSON status
  records, and define that output contract without changing the current
  human-readable default.

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

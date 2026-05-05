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

## Next Slice

### Native Provider Usage Metadata Normalization

Goal: decide and implement the smallest provider-usage normalization layer that
keeps native provider metadata useful without leaking prompt or output content.

Candidate shape:

- define allowlisted normalized usage keys shared by fake and OpenAI providers
- keep provider-native raw usage payloads out of JSONL and Markdown by default
- preserve existing safe counters such as input, output, total, cached, and
  reasoning token counts when available
- document unknown or unavailable counters as omitted rather than guessed
- keep the provider boundary standard-library first and do not add dependencies

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

- normalized provider usage remains metadata-only and privacy-safe
- fake-provider and OpenAI-provider tests remain deterministic
- native records still pass `pipy-session verify`
- `pipy-session list`, `search`, and `inspect` stay compatible
- raw system prompts, user prompts beyond the short `--goal` metadata, and
  model output are not persisted by default
- raw provider responses, tool payloads, stdout, stderr, diffs, file contents,
  secrets, and credentials are not persisted by default

## Near Term

- Decide whether native run final text should eventually support a structured
  machine-readable stdout mode.
- Decide when a post-tool provider turn is useful after the fake intent path,
  while keeping execution fake until permission and sandbox enforcement are
  designed.

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

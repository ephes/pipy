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

## Next Slice

### Native Tool Boundary

Goal: define the native tool request/result boundary without adding real
filesystem or shell execution yet.

Candidate shape:

- add value objects for a native tool request, result, and lifecycle status
- keep a fake or no-op tool implementation for deterministic tests
- emit privacy-safe tool lifecycle events without storing tool payloads
- make approval and sandbox policy explicit data, but do not enforce real
  workspace mutation yet
- keep the provider turn single-shot; do not add a full agent loop

Keep out of scope:

- OAuth or a provider registry
- retries, streaming, or model fallback
- real filesystem or shell tool execution
- approval prompts or sandbox enforcement
- raw prompt/model output storage in JSONL or Markdown
- Codex, Claude, or Pi CLI wrapping as the main product path

Acceptance checks:

- fake-provider tests remain deterministic
- fake-tool tests remain deterministic
- native records still pass `pipy-session verify`
- `pipy-session list`, `search`, and `inspect` stay compatible
- raw system prompts, user prompts beyond the short `--goal` metadata, and
  model output are not persisted by default
- tool payloads, stdout, stderr, diffs, file contents, secrets, and credentials
  are not persisted by default

## Near Term

- Add a no-op or fake tool path to test native tool lifecycle events without
  touching the workspace.
- Design the permission and sandbox policy as explicit data, not implicit CLI
  behavior.
- Decide how native provider usage metadata should be normalized without
  leaking prompt or output contents.
- Decide whether native run final text should eventually support a structured
  machine-readable stdout mode.

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
  the next provider-boundary slice.

## Maintenance Notes

- Move completed slices from `Next Slice` or `Near Term` to `Done` in the same
  change that implements them.
- Keep deferred items here brief; put detailed design and rationale in
  `docs/harness-spec.md`.
- Keep archive and privacy rules aligned with `docs/session-storage.md`.

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
  machine-readable native stdout was reserved for an explicit flag later named
  `--native-output json`, and archives remain metadata-only without model
  output.
- Native post-tool provider turn decision: the current native fake intent path
  remains bounded to one provider turn plus optional one fake no-op tool
  invocation; any post-tool provider turn is deferred until permission prompts,
  sandbox enforcement, and real tool-result observation semantics are designed,
  and future metadata must remain summary-safe and metadata-only.
- Native structured stdout flag contract decision: current `pipy-native`
  stdout remains successful provider final text only by default, while
  structured automation output is reserved for explicit `--native-output json`
  emitting one final metadata-only JSON object with no raw prompts, model
  output, provider responses, tool payloads, stdout, stderr, diffs, file
  contents, secrets, credentials, tokens, private keys, or sensitive personal
  data.
- Native structured stdout JSON mode: `pipy run --agent pipy-native
  --native-output json` emits one final versioned metadata-only JSON object
  after the native run and recorder finalization attempt complete, suppresses
  provider final text in JSON mode, includes finalized record references and
  safe capture booleans, preserves the default final-text stdout contract when
  omitted, and rejects `--native-output` for non-native agents before creating
  a record.
- Native runtime boundary decision after structured stdout: the selected next
  native boundary is a tool request identity and turn-index contract, chosen
  because the current fake no-op path already records one sanitized tool
  request with `request_id` and `turn_index`, while post-tool provider turns,
  real tool observations, and broader loops remain deferred.
- Native tool request identity and turn-index contract: native sessions now use
  an explicit pipy-owned identity value object for the current bounded
  `turn_index=0` and one no-op request position, preserving the archive-facing
  `tool_request_id` shape while rejecting provider-supplied request ids as
  unsafe input.

## Next Slice

### Native Post-Tool Observation Contract Decision

Goal: decide the smallest summary-safe shape for a future native post-tool
observation before adding any second provider call or broader execution
behavior.

Selected shape:

- keep the current runtime bounded to one provider turn plus optional one fake
  no-op tool invocation
- keep the implemented pipy-owned `turn_index` and `tool_request_id` contract
  unchanged
- design what, if anything, a future provider turn may observe from a tool
  result without archiving raw tool payloads, stdout, stderr, diffs, file
  contents, prompts, model output, provider responses, secrets, credentials,
  tokens, private keys, or sensitive personal data
- decide summary-safe labels and storage booleans for future tool observations
  before any post-tool provider call exists
- keep `pipy-native` as the product runtime direction rather than wrapping
  Codex, Claude, Pi, or another CLI as the main path
- preserve metadata-only archives and the default native stdout/stderr contract

Keep out of scope:

- changing the current human-readable default stdout mode
- changing `--native-output json`
- streaming
- retries or model fallback
- provider registry or OAuth
- real filesystem or shell tool execution
- approval prompts or sandbox enforcement
- implementing post-tool provider turns or a general model/tool loop
- multiple tool requests per provider turn
- provider-side built-in tools
- raw prompt/model output storage in JSONL, Markdown, or structured stdout
- raw provider responses, tool payloads, stdout, stderr, diffs, file contents,
  secrets, credentials, or sensitive personal data in JSONL, Markdown, or
  structured stdout
- Codex, Claude, Pi, or another CLI wrapper as the main product path

Acceptance checks:

- native default stdout remains successful final text only on success, with
  diagnostics, finalization, progress, and errors on stderr
- native behavior still uses the bounded deterministic fake/no-op execution path
- no post-tool provider call is implemented while the observation shape is only
  being decided
- native records still pass `pipy-session verify`
- `pipy-session list`, `search`, and `inspect` stay compatible
- raw system prompts, user prompts beyond the short `--goal` metadata, model
  output, provider responses, tool payloads, stdout, stderr, diffs, file
  contents, secrets, credentials, tokens, private keys, and sensitive personal
  data are not persisted or emitted in automation output by default

## Near Term

- Decide the summary-safe shape of a future post-tool observation contract
  before any second provider call exists.

## Deferred

- Full native pipy agent runtime beyond the provider and tool-boundary slices.
- Native post-tool observation contract and post-tool provider turn.
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
  raw transcript import, multiple native tool requests, post-tool provider
  turns, TUI, RPC, compaction, branching, or orchestration in the next native
  slice.

## Maintenance Notes

- Move completed slices from `Next Slice` or `Near Term` to `Done` in the same
  change that implements them.
- Keep deferred items here brief; put detailed design and rationale in
  `docs/harness-spec.md`.
- Keep archive and privacy rules aligned with `docs/session-storage.md`.

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
  invocation; any post-tool provider turn is deferred pending permission
  prompts, sandbox enforcement, and real execution work, and future metadata
  must remain summary-safe and metadata-only.
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
- Native post-tool observation contract decision: future post-tool observations
  are documented as internal sanitized metadata records anchored to pipy-owned
  `tool_request_id` and `turn_index`, with only safe labels, counters,
  durations, storage booleans, and sanitized reason/status metadata allowed;
  raw tool results, stdout, stderr, diffs, patches, file contents, prompts,
  model output, provider responses, provider-native tool-call/result objects,
  function arguments, provider response ids that could reveal payload content,
  raw tool arguments, shell commands, model-selected paths, secrets,
  credentials, tokens, private keys, and sensitive personal data remain
  forbidden, and the current runtime still does not make a post-tool provider
  call.
- Native tool observation value object stub: `NativeToolObservation` now exists
  as an internal inert value object with only correlation keys, safe
  tool/status/reason labels, optional duration, and false-by-default storage
  booleans. It is test-covered and is not emitted, archived, provider-forwarded,
  or threaded through the runtime.
- Sanitized observation lifecycle event shape: future sanitized native tool
  observations now have one inert terminal archive event contract,
  `native.tool.observation.recorded`, with an explicit metadata-only payload
  allowlist, terminal status labels, closed safe reason labels, no observation
  `started` event, no normalized counters in the first shape, and no runtime
  emission, archive writes, provider forwarding, or post-tool provider turn.
- Native provider-visible repo context policy: future provider-visible repo
  context is documented as bounded sanitized provider input rather than archive
  content, with allowed source types, forbidden source types/content, fixed
  upper limits, path-label rules, fail-closed redaction behavior, and
  metadata-only archive rules, while the current runtime still performs no
  real repo reads, context forwarding, live observation emission, archive writes
  for live context, or post-tool provider turn.
- Native approval and sandbox enforcement baseline: future native tool gates
  are documented before real execution exists, with approval decision labels,
  approval requirements for read, context, write, patch, shell, network, and
  verification operations, sandbox mode labels, independent capability
  booleans, fixed gate order, fail-closed behavior, and metadata-only archive
  rules, while the current runtime still has no live approval prompts, sandbox
  enforcement, real repo reads, provider-visible context forwarding, live
  observation emission, archive writes for live context or real execution, or
  post-tool provider turn.
- Native inert read-only tool request value objects: native models now export
  metadata-only request kind labels, bounded limit metadata, a read-only request
  shape with pipy-owned `tool_request_id` and `turn_index`, required approval
  policy, read-only workspace sandbox policy, independent capability booleans
  including `workspace_read_allowed`, false storage booleans, and optional safe
  scope labels, while the current runtime still does not execute real reads,
  run searches, resolve paths, forward provider-visible repo context, emit live
  observations, archive live context, enforce approvals or sandboxing, or make
  a post-tool provider turn.

## Next Slice

### Add one bounded read-only tool implementation slice

Goal: implement the first real bounded native read-only workspace tool, likely
an explicit file excerpt or search excerpt, using the completed policy,
approval/sandbox data, inert request shapes, and provider-visible context limits
without widening archives into raw content stores.

Selected shape:

- keep the current runtime bounded to one provider turn plus optional one fake
  no-op tool invocation until this slice explicitly chooses the first bounded
  read path
- wire only one narrowly scoped read-only tool implementation behind the
  documented gates
- require approval before any workspace read, search, directory inspection, or
  provider-visible repo context production
- enforce the read-only workspace sandbox and independent capability booleans
  before any read
- apply the documented limits, redaction rules, ignore/generated-file rules,
  path-label rules, and fail-closed behavior before provider visibility
- preserve the implemented pipy-owned `turn_index`, `tool_request_id`, and
  `native.tool.observation.recorded` contracts unchanged
- preserve metadata-only archives and the default native stdout/stderr contract:
  archive only safe status, duration, counters, labels, and storage booleans,
  never raw read results
- keep `pipy-native` as the product runtime direction rather than wrapping
  Codex, Claude, Pi, or another CLI as the main path

Keep out of scope:

- changing the current human-readable default stdout mode
- changing `--native-output json`
- streaming
- retries or model fallback
- provider registry or OAuth
- write tools, patch tools, shell execution, network access, and verification
  command execution
- implementing post-tool provider turns or a general model/tool loop
- provider-visible file contents or search result text outside the sanitized
  bounded context shape
- emitting or archiving raw live tool observations
- multiple tool requests per provider turn unless explicitly required by the
  single selected bounded-read implementation
- provider-side built-in tools
- raw prompt/model output storage in JSONL, Markdown, or structured stdout
- raw provider responses, tool payloads, stdout, stderr, diffs, patches, file
  contents, secrets, credentials, or sensitive personal data in JSONL,
  Markdown, structured stdout, or provider-visible context
- Codex, Claude, Pi, or another CLI wrapper as the main product path

Acceptance checks:

- native default stdout remains successful final text only on success, with
  diagnostics, finalization, progress, and errors on stderr
- native behavior still uses the bounded deterministic fake/no-op execution path
- no post-tool provider call or general model/tool loop is introduced
- the selected read-only tool executes only after approval, sandbox capability,
  path/context validation, limit, and redaction gates succeed
- provider-visible context, if produced, is bounded and sanitized before it
  reaches the provider and never archived as raw content
- archives record only safe labels, counters, byte and line counts, statuses,
  reasons, `duration_seconds`, `tool_request_id`, `turn_index`, and storage
  booleans
- raw prompts, model output, provider responses, tool payloads, stdout, stderr,
  diffs, patches, full file contents, shell commands, raw args, model-selected
  paths, provider-selected paths as authority, secrets, credentials, tokens,
  private keys, and sensitive personal data are not persisted or emitted in
  automation output by default
- native records still pass `pipy-session verify`
- `pipy-session list`, `search`, and `inspect` stay compatible

## Near Term

The current next slice remains prerequisite policy work, while the near-term
trajectory stays supervised self-bootstrap: make `pipy-native` capable of
reading bounded repo context, taking one follow-up model turn, and eventually
making reviewed edits without turning archives into raw transcripts.
Post-tool provider turns and real tool execution remain deferred until the
policy, approval, sandbox, and bounded-read prerequisites below are documented
and implemented in order.

Small reviewable slices, in intended order:

1. Add one bounded read-only tool implementation slice, likely `rg`-style
   search or explicit file read, implementing the limits, redaction rules, and
   approval/sandbox gates from the policy slices; archives record only safe
   status, duration, counters, labels, and storage booleans, never raw results.
2. Add one bounded post-tool provider turn against synthetic sanitized
   observation fixtures and the provider-visible context shape, with a hard
   stop after that turn and no real read-tool output or general model/tool loop.
3. Wire the bounded read-only tool observation into the one follow-up provider
   turn, consuming only the sanitized observation shape from the completed
   lifecycle-event slice and the completed provider-visible context policy.
4. Add a patch proposal boundary before writes: provider may propose a
   structured edit plan or patch candidate, but applying edits remains separate
   and human-reviewed; archives record only metadata such as proposal status,
   file counts, and storage booleans, not raw patch text.
5. Add an explicit patch-apply slice with conservative file scope, no shell
   execution, metadata-only archives, and focused tests.
6. Add an allowlisted verification-command slice, starting with `just check`,
   behind explicit policy and with only exit code, status, duration, and safe
   labels recorded; stdout/stderr remain excluded from archives.
7. Run the first human-supervised self-bootstrap trial on a tiny docs-only or
   test-only change, with independent review before treating it as usable.

## Deferred

- Full native pipy agent runtime beyond the provider and tool-boundary slices.
- General native model/tool loop beyond a single bounded follow-up turn.
- Codex JSONL event adapter.
- Claude integration beyond the existing conservative `pipy-session auto`
  metadata capture.
- Pi-native session inspection beyond metadata references.
- Raw transcript import with explicit opt-in and redaction policy.
- Indexed archive search or SQLite-backed query layer.
- Broad repo maps or persistent workspace summaries beyond the first bounded
  provider-visible context policy.
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
  turns, write tools, verification-command execution, TUI, RPC, compaction,
  branching, or orchestration in the upcoming value-object, lifecycle, and
  policy slices; real execution work must wait for its named slice.

## Maintenance Notes

- Move completed slices from `Next Slice` or `Near Term` to `Done` in the same
  change that implements them.
- Keep deferred items here brief; put detailed design and rationale in
  `docs/harness-spec.md`.
- Keep archive and privacy rules aligned with `docs/session-storage.md`.

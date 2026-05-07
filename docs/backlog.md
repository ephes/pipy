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
- Native explicit file excerpt read-only tool implementation: a direct native
  `NativeExplicitFileExcerptTool` can perform one real bounded UTF-8 text file
  read only from explicit pipy-owned request, gate, and target data. It enforces
  required approval, `read-only-workspace`, `workspace_read_allowed=true`, no
  mutation, no shell, no network, workspace-relative target validation,
  conservative ignored/generated-file rejection, binary/unreadable/unsupported
  encoding checks, secret-looking content rejection, documented byte and line
  limits, and metadata-only archive helper output that includes
  `workspace_read_allowed` only on the new read-tool metadata surface. Raw
  excerpt text stays in memory on the result object and is not wired into
  `NativeAgentSession`, provider calls, stdout, JSON output, Markdown, or JSONL.
- OpenAI subscription-backed native auth decision: decision label
  `blocked-for-now` on 2026-05-07. Official OpenAI API, ChatGPT billing,
  ChatGPT Plus, Codex authentication, Codex pricing, and Codex SDK docs show a
  supported API-key path for direct API calls and a supported
  subscription-backed sign-in path for Codex product clients, but not an
  official generic third-party native provider auth path for calling OpenAI
  models directly with a ChatGPT or Codex subscription. The existing API-key
  OpenAI Responses provider remains the OpenAI baseline; unsupported credential
  scraping, token/cache reuse, reverse engineering, and CLI/product wrapping are
  rejected. OpenRouter provider support with explicit model selection is
  promoted as the next provider-access slice.
- Native OpenRouter Chat Completions provider behind `ProviderPort`, selected
  with `--native-provider openrouter --native-model <provider/model>`, using
  `OPENROUTER_API_KEY`, an injectable standard-library HTTP boundary, one
  non-streaming chat-completion request with pipy-owned system/user messages,
  normalized usage mapping from `prompt_tokens`, `completion_tokens`, and
  `total_tokens`, stdout-only final text on successful default runs, and
  metadata-only archives/JSON output without raw prompts, model output, request
  bodies, provider responses, auth material, provider-native payloads, tools,
  streaming, retries, fallback, OAuth, provider registry, or post-tool turns.
- Native bounded post-tool provider turn against synthetic sanitized
  observations: after one safe supported no-op tool intent and successful no-op
  tool result, native sessions can consume an explicitly supported synthetic
  observation fixture, emit one metadata-only
  `native.tool.observation.recorded` event anchored to pipy's
  `tool_request_id` and `turn_index`, make exactly one follow-up provider call
  with generated observation metadata only, aggregate normalized provider usage
  counters, and hard-stop after that turn. Unsafe or unsupported fixtures fail
  closed before provider visibility, existing no-fixture fake/OpenAI/OpenRouter
  behavior remains compatible, default stdout remains final text only on
  successful text mode, and archives/JSON output still omit raw prompts, model
  output, raw provider responses, request bodies, raw tool observations,
  provider-native payloads, auth material, secrets, credentials, tokens, private
  keys, and sensitive personal data.
- Native bounded read-only tool observation into follow-up provider turn: after
  one safe supported explicit-file-excerpt read-only intent and one supported
  pipy-owned read fixture, native sessions run the existing bounded
  `NativeExplicitFileExcerptTool`, emit metadata-only tool and observation
  events, forward the successful excerpt only as in-memory provider-visible
  context to exactly one follow-up provider turn, aggregate normalized usage,
  and hard-stop. Unsafe, unsupported, denied, ignored/generated, oversized,
  binary, unreadable, unsupported-encoding, secret-looking, or limit-exceeding
  read data fails closed before provider visibility. Archives, Markdown,
  default stdout, and `--native-output json` remain metadata-only and omit raw
  excerpts, file contents, prompts, model output, provider responses, request
  bodies, provider-native payloads, auth material, secrets, credentials, tokens,
  private keys, and sensitive personal data.
- Native patch proposal boundary before writes: after one successful bounded
  read-only tool observation and one successful follow-up provider turn, native
  sessions may parse a pipy-owned structured patch proposal fixture from
  provider metadata, emit one metadata-only
  `native.patch.proposal.recorded` event, and hard-stop without applying edits.
  Supported proposal metadata is limited to status, file and operation counts,
  closed operation labels, pipy-owned `tool_request_id` and `turn_index`, false
  storage booleans, and safe reason labels. Unsafe or unsupported proposal data
  is recorded only as skipped metadata and raw patch text, diffs, file contents,
  provider-native payloads, prompts, model output, provider responses, stdout,
  stderr, secrets, credentials, tokens, private keys, and sensitive personal
  data remain out of JSONL, Markdown, default stdout, and
  `--native-output json`.
- Native supervised patch apply boundary: after a supported patch proposal,
  native sessions may apply one injected pipy-owned, human-reviewed
  `NativePatchApplyRequest` through explicit approval and `mutating-workspace`
  policy. The first implementation supports bounded whole-file create, modify,
  delete, and rename operations, validates the full plan before mutation,
  rejects overlapping operation paths, requires expected file hashes for
  existing files, rejects unsafe/generated targets and secret-looking
  replacement text, truthfully records partial mutation if an unexpected write
  error happens after at least one operation applies, emits one metadata-only
  `native.patch.apply.recorded` event, and preserves the default CLI behavior
  because normal fake/OpenAI/OpenRouter runs do not supply a patch apply
  request. Archives, Markdown, default stdout, and `--native-output json` still
  omit raw patch text, raw diffs, file contents, target paths, prompts, model
  output, provider responses, provider-native payloads, stdout, stderr, shell
  commands, secrets, credentials, tokens, private keys, and sensitive personal
  data.

## Next Slice

### Add an allowlisted verification-command slice

Goal: add the first supervised verification command boundary after patch apply,
starting with `just check`, while keeping command scope allowlisted and archive
records metadata-only.

Selected shape:

- consume only a pipy-owned verification request for an allowlisted command
- start with `just check` only
- require explicit approval and policy before execution
- keep arbitrary shell, network, retries, and provider routing out of scope
- record only metadata such as command label, status, exit code, duration,
  storage booleans, and safe reason/error labels
- preserve default stdout and `--native-output json` metadata-only behavior

Keep out of scope:

- arbitrary shell execution and network access
- provider-side built-in tools or function calling
- multiple tool requests or unbounded turns
- streaming, retries, fallback, OAuth, provider registry, and provider routing
- raw prompts, model output, raw provider responses, provider-native payloads,
  raw tool observations, stdout, stderr, raw diffs, full file contents,
  command output, auth material, secrets, credentials, tokens, private keys, or
  sensitive personal data in JSONL, Markdown, structured stdout, or
  provider-visible context

Acceptance checks:

- verification accepts only the documented pipy-owned request shape
- unsupported or unsafe command data fails closed before execution
- records preserve existing pipy-owned `tool_request_id`, `turn_index`, and
  provider turn metadata
- existing `fake`, API-key `openai`, and API-key `openrouter` provider
  behavior remains compatible
- default native stdout remains successful final text only on success, with
  diagnostics, finalization, progress, and errors on stderr
- archives and `--native-output json` remain metadata-only and never include
  raw prompts, model output, provider responses, request bodies, raw patch text,
  raw diffs, file contents, raw tool observations, command stdout, command
  stderr, auth tokens, cookies, credentials, secrets, private keys, or
  sensitive personal data
- native records still pass `pipy-session verify`, and `pipy-session list`,
  `search`, and `inspect` stay compatible

## Near Term

The near-term trajectory stays supervised self-bootstrap. With OpenRouter
access, bounded read-only context, metadata-only patch proposals, and an
injected supervised patch apply boundary in place, the next priority is an
allowlisted verification-command boundary.
OpenAI
subscription-backed native provider auth is `blocked-for-now` because the
official docs checked on 2026-05-07 document ChatGPT/Codex subscription auth for
OpenAI product clients, not a generic third-party native provider auth path.
OpenRouter support with explicit model selection is implemented as the preferred
provider-access path after that decision. Pay-by-token OpenAI API access remains
implemented as a baseline but is not the preferred usage path for this project.
Anthropic
subscription access is not a near-term native provider target because
subscription-backed coding-agent usage is expected to stay within Claude Code.
Local-model providers remain interesting but deferred until benchmark work in a
separate repo clarifies whether Ollama, llama.cpp, MLX, LM Studio, or another
runtime should be the first local integration.

Small reviewable slices, in intended order:

1. Add an allowlisted verification-command slice, starting with `just check`,
   behind explicit policy and with only exit code, status, duration, and safe
   labels recorded; stdout/stderr remain excluded from archives.
2. Run the first human-supervised self-bootstrap trial on a tiny docs-only or
   test-only change, with independent review before treating it as usable.

Self-bootstrap readiness gates. The first supervised trial picks whichever gate
has been reached when it is run:

- Proposal-only trial: available after the patch proposal boundary exists.
  Pipy may use the existing bounded read-only context and propose structured
  edit metadata, but writes remain manual and archives stay metadata-only.
- Human-applied patch trial: available once proposal output is useful enough
  for a human to apply or translate manually. No new slice is required for this
  gate, but independent review is still required before trusting the result.
- Pipy-applied patch trial: available only after the explicit patch-apply slice
  exists, with conservative file scope, no arbitrary shell execution, and
  metadata-only archives.
- Verified patch trial: available only after allowlisted verification-command
  execution exists, starting with `just check` and recording only safe status,
  exit-code, duration, and label metadata; stdout/stderr remain excluded from
  archives.

## Deferred

- Full native pipy agent runtime beyond the provider and tool-boundary slices.
- General native model/tool loop beyond a single bounded follow-up turn.
- Codex JSONL event adapter.
- Claude integration beyond the existing conservative `pipy-session auto`
  metadata capture.
- Pi-native session inspection beyond metadata references.
- Raw transcript import with explicit opt-in and redaction policy.
- Indexed archive search or SQLite-backed query layer.
- Review-cycle metadata for `pipy-session workflow review-outcome`, including
  explicit per-round versus cumulative scope, review round number, and optional
  cycle identity so `reflect` can avoid double-counting iterative reviews.
- Broad repo maps or persistent workspace summaries beyond the first bounded
  provider-visible context policy.
- Local model provider integrations for Ollama, llama.cpp, MLX, LM Studio, or
  similar runtimes until separate benchmark work identifies the best first
  local runtime and connection shape.
- OpenAI subscription-backed native provider auth until official OpenAI docs
  expose a stable third-party/native provider auth flow that is not specific to
  Codex, ChatGPT, or another OpenAI product client.
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
- Building broad approvals, sandboxing, retries, streaming, OAuth, provider
  registry, raw transcript import, multiple native tool requests, post-tool
  provider turns, general write tools beyond supervised patch apply,
  non-allowlisted verification commands, TUI, RPC, compaction, branching, or
  orchestration in the upcoming slices; real execution work must wait for its
  named slice.
- Using unsupported subscription auth, scraping browser or CLI session stores,
  or treating another product's login/session as pipy-native provider
  credentials.

## Maintenance Notes

- Move completed slices from `Next Slice` or `Near Term` to `Done` in the same
  change that implements them.
- Keep deferred items here brief; put detailed design and rationale in
  `docs/harness-spec.md`.
- Keep archive and privacy rules aligned with `docs/session-storage.md`.

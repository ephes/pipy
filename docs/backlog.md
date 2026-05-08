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
- Native allowlisted verification-command boundary: after a successful
  supervised patch apply, native sessions may run one injected pipy-owned
  `NativeVerificationRequest` through explicit human-reviewed approval and
  read-only workspace policy with shell execution allowed only for the
  allowlisted command label `just-check`. The first implementation maps that
  label internally to `just check`, emits one metadata-only
  `native.verification.recorded` event, and makes skipped or failed
  verification fail the native run when verification was requested. Unsupported
  or unsafe command data, denied or missing approval, unsafe sandbox policy,
  missing `just`, command failure, and execution errors fail closed with only
  safe labels. Archives, Markdown, default stdout, and `--native-output json`
  still omit command stdout, command stderr, shell command text, raw prompts,
  model output, provider responses, provider-native payloads, raw diffs, file
  contents, auth material, secrets, credentials, tokens, private keys, and
  sensitive personal data.
- First supervised self-bootstrap trial implementation: a tiny in-process
  test-only trial exercises the bounded read-only context, metadata-only patch
  proposal, supervised patch apply, and allowlisted verification-command
  boundaries against a temporary docs file. The trial keeps review and approval
  data pipy-owned and human-reviewed, adds no public CLI automation controls,
  and asserts that events remain metadata-only without raw prompts, model
  output, patch text, diffs, file contents, command output, stdout, or stderr.
- First supervised self-bootstrap review: the trial received an independent
  review, and the accepted stale-doc finding was fixed before commit.
- Product-direction checkpoint after first native smoke test: manual `pipy run
  --agent pipy-native` smoke testing clarified that the eventual product target
  is a real Pi-like native shell, while the next safe implementation work is
  still conversation and turn-loop foundation rather than a rushed UI.
- Native conversation state and bounded provider-turn loop foundation:
  `pipy_harness.native.conversation` now defines pipy-owned conversation
  identity, turn identity, closed turn role/status labels, metadata-only
  per-turn payloads with false storage booleans, and immutable bounded
  in-memory provider-turn state. No runtime behavior, stdout contract,
  structured JSON output, archive event shape, provider calls, tool execution,
  shell UI, or public CLI controls changed in this slice.
- Native one-shot run rebased on conversation state: `NativeAgentSession.run`
  now creates a per-run in-memory native conversation identity/state and
  allocates the initial and optional post-tool provider turn indexes and labels
  from that state. The current runtime remains bounded to one initial provider
  turn plus at most one follow-up provider turn after a supported observation,
  with no new archive event types, CLI controls, stdout/JSON behavior changes,
  or provider/tool behavior changes.
- Native minimal no-tool REPL: `pipy repl --agent pipy-native` now runs a
  bounded interactive shell over the same native provider/session/conversation
  core. Each non-empty input line makes one provider call, the first turn uses
  the conversation-state `initial` label, subsequent turns use the closed
  `no_tool_repl` label, provider final text prints to stdout, prompts and
  finalization stay on stderr, and `/exit`, `/quit`, EOF, interrupt, or the
  fixed turn bound terminate cleanly. The REPL does not parse, execute, archive,
  or provider-forward tool intents, tool observations, read-only context,
  patches, patch apply requests, shell commands, verification requests, or
  provider metadata; archives remain metadata-only and one-shot
  `pipy run --agent pipy-native` stdout/JSON behavior remains unchanged.
- Native visible approval and sandbox prompt foundation: `pipy_harness.native`
  now exposes an injected stream-based approval resolver for the existing
  read-only workspace inspection request shape. It renders safe approval policy,
  sandbox mode, capability booleans, operation/tool labels, and optional safe
  scope labels before execution, maps approved/denied/unavailable/failed
  outcomes to the existing `NativeReadOnlyGateDecision`, and fails closed for
  missing UI, unsupported request kind, unsupported approval policy,
  unsupported sandbox mode, sandbox mismatch, unsafe request data, and
  attempted capability escalation. This slice adds no public tool-capable REPL
  command, no new archive event, and no new structured stdout fields.
- Native interactive read-only REPL command behind the prompt gate:
  `pipy repl --agent pipy-native` now accepts one explicit `/read
  <workspace-relative-path>` command per session. The command builds
  pipy-owned explicit-file-excerpt request and target data, resolves the
  visible approval/sandbox prompt on stderr before the bounded file tool can
  read, prints a successful excerpt only to the interactive stdout stream, and
  records only metadata-only tool lifecycle events. Denied, unavailable,
  unsafe-target, skipped, and repeated read-command cases fail closed without
  reading. Ordinary non-command REPL messages remain no-tool provider turns,
  one-shot native stdout/JSON/archive contracts remain unchanged, and archives
  still omit raw approval prompts, raw tool arguments, raw tool results,
  stdout, stderr, diffs, full file contents, command output, auth material,
  secrets, credentials, tokens, private keys, and sensitive personal data.
- Native explicit provider-visible `/ask-file` REPL boundary:
  `pipy repl --agent pipy-native` now accepts `/ask-file
  <workspace-relative-path> -- <question>` as the first approved interactive
  context handoff. It shares the one-read per-session limit with `/read`, uses
  the same visible approval/sandbox gate and bounded explicit-file-excerpt
  tool, emits metadata-only tool and observation lifecycle events, forwards the
  successful excerpt plus question only in memory to exactly one provider turn
  labeled `ask_file_repl`, and prints only provider final text to stdout.
  Denied, unavailable, malformed, unsafe-target, skipped, failed, and repeated
  read-command cases fail closed before provider visibility. Ordinary
  non-command REPL messages remain no-tool provider turns, `/read` remains
  display-only, one-shot native stdout/JSON/archive contracts remain
  unchanged, and archives still omit raw prompts, model output, provider
  responses, provider metadata, raw approval prompts, raw tool arguments, raw
  tool results, stdout, stderr, diffs, full file contents, command output,
  auth material, secrets, credentials, tokens, private keys, and sensitive
  personal data.
- Native `/ask-file` smoke and separator hardening: focused CLI tests and
  fake-provider terminal smoke runs confirmed the explicit `/read` and
  `/ask-file` approval prompts, stdout/stderr behavior, provider turn label,
  and metadata-only archive shape. `pipy-session verify`, `list`, `search`, and
  `inspect` remained compatible with finalized REPL records. OpenRouter smoke
  was skipped because `OPENROUTER_API_KEY` was unavailable in the local
  environment. The smallest hardening selected from the smoke pass was command
  parsing: `/ask-file` now accepts a whitespace-delimited `--` separator, such
  as spaces or tabs around `--`, while preserving ordinary non-command REPL
  turns as no-tool provider turns. Follow-up review hardening also made the
  REPL dispatcher recognize `/read` and `/ask-file` followed by any whitespace
  rather than only a literal space, strengthened archive privacy assertions for
  the whitespace-separator test, and updated the malformed `/ask-file`
  diagnostic to name the whitespace-delimited separator.
- Native REPL command help and usage diagnostics: `pipy repl --agent
  pipy-native` now accepts a local `/help` command that prints only static
  supported command shapes on stderr without invoking providers, tools, reads,
  or archive events. Malformed supported slash commands such as `/read` and
  `/ask-file`, as well as unsupported slash commands, now use one static usage
  diagnostic path on stderr without archiving raw command text, paths,
  questions, or excerpt data. Ordinary non-command REPL turns remain no-tool
  provider turns, and existing `/read` and `/ask-file` behavior remains
  bounded by the same one-read approval path and metadata-only archive rules.
- Native REPL command help and usage diagnostics review: the independent review
  cycle completed cleanly after two rounds. The first round reported four
  suggestions: remove an unreachable empty-target fallback, avoid rebuilding
  exit-command order inside the REPL loop, add malformed `/help <text>`
  coverage, and split a harness-spec fail-closed sentence. All four were
  accepted and fixed; the second review reported no findings. Focused REPL
  tests, `just check`, and metadata-only archive assertions remained green, and
  no redundant review pass is warranted while scope stays unchanged.
- Native REPL next-boundary decision: selected a proposal-only
  `/propose-file <workspace-relative-path> -- <change-request>` command as the
  next small implementation slice. The command should reuse the existing
  approved explicit-file-excerpt path, send one in-memory file excerpt plus one
  change request to exactly one provider turn, accept at most one pipy-owned
  metadata-only patch proposal, and hard-stop without applying edits,
  verification, shell execution, network access, provider-side tools, multiple
  tool requests, or a general model/tool loop. No runtime behavior, stdout or
  stderr contract, archive schema, provider calls, tool execution, or public
  CLI controls changed in this decision slice.
- Native proposal-only `/propose-file` REPL boundary: `pipy repl --agent
  pipy-native` now accepts `/propose-file <workspace-relative-path> --
  <change-request>` as the first proposal-only interactive context boundary.
  It shares the one-read per-session limit with `/read` and `/ask-file`, uses
  the same visible approval/sandbox gate and bounded explicit-file-excerpt
  tool, emits metadata-only tool and observation lifecycle events, forwards the
  successful excerpt plus change request only in memory to exactly one provider
  turn labeled `propose_file_repl`, parses at most one pipy-owned structured
  patch proposal metadata object, and records at most one metadata-only
  `native.patch.proposal.recorded` event. Denied, unavailable, malformed,
  unsafe-target, skipped, failed, and repeated read-command cases fail closed
  before provider visibility or proposal parsing. The command hard-stops after
  the proposal result without applying edits, running verification, executing
  shell commands, using network access, invoking provider-side tools, creating
  another provider turn, or persisting raw proposal/context data. One-shot
  native stdout/JSON/archive behavior remains unchanged.
- Native proposal-only `/propose-file` review and smoke: focused review found
  the implemented proposal-only boundary aligned with the selected shape.
  Focused CLI, native session, and proposal value-object tests covered
  malformed input, denied/unavailable/unsafe/repeated read-command paths,
  supported and skipped metadata-only proposal events, provider metadata
  suppression, and archive privacy assertions. A fake-provider terminal smoke
  confirmed the visible approval prompt, provider turn label
  `propose_file_repl`, stdout/stderr split, finalized archive compatibility,
  and `pipy-session verify` compatibility. No implementation hardening was
  required in this review slice. OpenRouter smoke was skipped because
  `OPENROUTER_API_KEY` was unavailable in the local environment.

## Next Slice

### Choose the next native REPL boundary after proposal-only review

Goal: select the next small native-shell boundary now that `/propose-file` has
been reviewed and smoked. This should be a decision slice, not an implementation
slice, so the project does not jump from metadata-only proposal records straight
into broad mutation, shell execution, or a general model/tool loop.

Decision points:

- whether the next user-visible shell capability should stay proposal-only,
  move to a human-applied patch trial, expose a supervised patch-apply trial, or
  add another read-only/provider-visible affordance first
- which existing boundaries can be reused unchanged: approval prompt, sandbox
  policy, explicit-file-excerpt reads, patch proposal metadata, supervised patch
  apply, allowlisted verification, and the existing `propose_file_repl`
  provider turn label
- which safety gate must happen before any write-capable public REPL command:
  human-reviewed request data, explicit approval, mutating-workspace sandbox
  policy, metadata-only archive events, and focused tests
- whether the next slice needs real provider smoke, fake-provider smoke only, or
  no runtime smoke because it changes documentation and planning only

Keep out of scope for the decision slice:

- applying, writing, creating, deleting, renaming, or editing files from the
  public REPL
- raw patch text, diffs, replacement file contents, model-selected paths,
  prompts, model output, provider responses, stdout, stderr, or command output
  in archives, Markdown, structured stdout, or catalog surfaces
- arbitrary shell execution, network access, provider-side built-in tools,
  multiple tool requests, unbounded turns, retries, streaming, fallback, OAuth,
  provider registry, provider routing, broad TUI work, RPC, or persistent
  history

## Near Term

The near-term product direction is still a real `pipy-native` runtime with a
Pi-like interactive shell. The shell should be a thin user interface over
pipy-owned provider, session, turn, approval, sandbox, and archive boundaries,
not a separate runtime and not a wrapper around Codex, Claude, Pi, or another
agent CLI.

The immediate implementation path stays architecture-first:

1. Choose the next native REPL boundary after proposal-only review.

Manual `pipy run --agent pipy-native` smoke tests are useful product checks,
but today they exercise a one-shot runner: `--goal` is the input, provider final
text is stdout, finalization is stderr, and the process exits. The persistent
shell is available through `pipy repl --agent pipy-native`; it now has one
approved local `/help` command, one display-only `/read
<workspace-relative-path>` command, and one approved provider-visible
`/ask-file <workspace-relative-path> -- <question>` command with a
whitespace-delimited `--` separator sharing the same one-read limit. Malformed
or unsupported slash commands print static usage diagnostics on stderr without
provider/tool execution or raw command archiving, but the REPL still does not
apply patches, execute commands, run verification, expose provider-side tools,
or support a general model/tool loop. The proposal-only
`/propose-file <workspace-relative-path> -- <change-request>` command is now
implemented and reviewed; the selected next step is choosing the next small
native REPL boundary while the broader mutation and tool-loop boundaries remain
deferred.

Provider access remains OpenRouter-first for near-term manual smoke testing.
OpenAI subscription-backed native provider auth is `blocked-for-now` because
the official docs checked on 2026-05-07 document ChatGPT/Codex subscription
auth for OpenAI product clients, not a generic third-party native provider auth
path. Pay-by-token OpenAI API access remains implemented as a baseline but is
not the preferred usage path for this project. Anthropic subscription access
is not a near-term native provider target because subscription-backed
coding-agent usage is expected to stay within Claude Code. Local-model
providers remain interesting but deferred until benchmark work in a separate
repo clarifies whether Ollama, llama.cpp, MLX, LM Studio, or another runtime
should be the first local integration.

Small reviewable slices, in intended order:

1. Choose the next native REPL boundary after proposal-only review.

Foundation gates toward an interactive shell:

- One-shot provider gate: available now through `pipy run --agent
  pipy-native`; useful for provider smoke tests but not an interactive shell.
- Conversation-state gate: available now in the one-shot runtime; provider turn
  indexes and labels are allocated from per-run in-memory conversation state
  without changing archive or stdout contracts.
- No-tool provider-turn REPL gate: available now through `pipy repl --agent
  pipy-native`.
  It reuses the same conversation state for repeated no-tool provider turns and
  keeps archives metadata-only.
- Visible approval prompt gate: available now as an injected native helper for
  read-only workspace inspection and is wired into the explicit `/read`
  command.
- Narrow read-only shell command gate: available now through `/read
  <workspace-relative-path>`, bounded to one approved explicit-file-excerpt
  request per REPL session.
- Provider-visible interactive context gate: available now through `/ask-file
  <workspace-relative-path> -- <question>` with a whitespace-delimited `--`
  separator, bounded to one approved explicit-file-excerpt request shared with
  `/read`, one in-memory provider handoff, and metadata-only archive handling.
- Command help and usage-diagnostic gate: available now through local `/help`
  and static stderr usage diagnostics for malformed or unsupported slash
  commands. These paths do not invoke providers, execute tools, consume the
  one-read limit, emit tool events, or archive raw command text, paths,
  questions, or excerpt data.
- Proposal-only interactive file gate: available now through `/propose-file
  <workspace-relative-path> -- <change-request>`. It reuses the existing
  approved explicit-file-excerpt path, sends one in-memory excerpt plus request
  to one provider turn labeled `propose_file_repl`, records only allowlisted
  metadata-only patch proposal status, and stops before any mutation,
  verification, shell execution, network access, or follow-up provider turn.
- Proposal-only review gate: available now. Focused tests and fake-provider
  terminal smoke covered the boundary and found no required implementation
  hardening; broader mutation remains behind a new named slice.

Self-bootstrap readiness gates remain historical context for supervised writes:

- Proposal-only trial: available now in the interactive shell. Pipy may use the
  existing bounded read-only context and propose structured edit metadata, but
  writes remain manual and archives stay metadata-only.
- Human-applied patch trial: available once proposal output is useful enough
  for a human to apply or translate manually. No new slice is required for this
  gate, but independent review is still required before trusting the result.
- Pipy-applied patch trial: available after the explicit patch-apply slice,
  with conservative file scope, no arbitrary shell execution, and metadata-only
  archives.
- Verified patch trial: exercised by the first supervised self-bootstrap trial
  using the allowlisted verification-command boundary, starting with `just
  check` and recording only safe status, exit-code, duration, and label
  metadata; stdout/stderr remain excluded from archives.

Do not move to a tool-capable shell until these existing invariants still hold:

- default native stdout remains successful final text only on success, with
  diagnostics, finalization, progress, and errors on stderr
- archives and `--native-output json` remain metadata-only and never include
  raw prompts, model output, provider responses, request bodies, raw patch text,
  raw diffs, file contents, raw tool observations, command stdout, command
  stderr, auth tokens, cookies, credentials, secrets, private keys, or
  sensitive personal data
- native records still pass `pipy-session verify`, and `pipy-session list`,
  `search`, and `inspect` stay compatible

## Deferred

- Full tool-capable native pipy agent runtime beyond the provider,
  conversation, approval, sandbox, and tool-boundary slices.
- General native model/tool loop beyond bounded provider turns and explicitly
  approved tool boundaries.
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

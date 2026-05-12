# Pipy Backlog

Status: planning index

This backlog records the current implementation direction at a task-slice
level. It is not a full issue tracker. Use it to choose the next small,
reviewable change while keeping the source-of-truth design constraints in
`docs/harness-spec.md` and `docs/session-storage.md`.

## Current State

Pipy has crossed from capture-only infrastructure into a small native product
runtime. The current native shell can authenticate to the `openai-codex`
provider, switch models, make ordinary no-tool provider turns, read one
explicit workspace-relative file excerpt, ask a provider about that excerpt,
request a proposal-only change for that excerpt through `/propose-file`, and
apply one same-session reviewed proposal through `/apply-proposal`, then run
one post-apply allowlisted verification command through `/verify just-check`.
It can also clear retained no-tool conversation context locally with `/clear`
and inspect local shell state with `/status`. The public shell still cannot
execute arbitrary shell commands, request
provider-side tools, read multiple files per session, run non-allowlisted
verification commands, or run a general model/tool loop.

Use this page as a planning index:

- `Done` is the detailed historical ledger. Keep it useful for archaeology,
  review, and tests, but do not make future agents infer the next slice from
  this section alone.
- `Next Slice` is the only current implementation target.
- `Near Term` explains why that slice is the next one and how it fits the
  self-bootstrap path.
- `Deferred` and `Explicitly Not Now` define boundaries that should not be
  crossed opportunistically.

## Self-Bootstrap Roadmap

- Proposal-only file context: available now through `/propose-file
  <workspace-relative-path> -- <change-request>`.
- Human-applied proposal trial: available now; the first real
  `openai-codex/gpt-5.2` trial produced a useful small change that was applied
  manually outside the REPL.
- Public one-file apply boundary: available now through `/apply-proposal
  <workspace-relative-path>`. It stays human-reviewed, pipy-owned,
  same-session, one-file, one-operation, and metadata-only in the archive.
- Public verification command: available now through `/verify just-check` after
  a successful same-session `/apply-proposal`. It stays allowlisted,
  post-apply-only, metadata-only, and fails the REPL run if verification is
  skipped or fails.
- First pipy-applied pipy change: completed through the public native REPL.
  The read-failure recovery boundary from that trial is now implemented,
  reviewed, and smoked; bounded no-tool REPL conversation context is now
  implemented, reviewed, and smoked; a local clear command for interactive
  conversation state is now implemented, reviewed, and smoked; a local status
  command for safe shell-state inspection is now implemented. The next
  milestone is Pi-like REPL startup chrome so running `pipy` feels like a
  native shell instead of a plain line-oriented prompt, without adding new
  execution powers.

The stored session archive supports this direction: repeated workflow
evaluations favor small native boundary slices, focused tests, documentation
updates, summary-safe workflow capture, and independent review, with review
cycles stopping after a clean second review unless scope or risk changes.

## Pi Parity Roadmap

Pipy is a Python slopfork of Pi, so the long-term product target is Pi-class
native coding-agent capability, not only a thin capture wrapper. Parity means
matching the useful product surfaces and workflows in pipy's architecture; it
does not require copying Pi's TypeScript implementation, custom `pi-tui`
library, exact command names, or all capabilities in one slice.

Use this as the broad parity ladder while keeping the current small-slice
discipline:

- Shell chrome and orientation: startup header, safe loaded-resource labels,
  compact command affordances, and status/footer-style state presentation.
  Current target: Pi-like REPL startup chrome.
- Interactive input ergonomics: richer editor behavior, multiline input,
  slash-command discovery, file references, autocomplete, and resilient terminal
  resize behavior. This likely requires a later TUI-framework decision.
- Context/resource loading: safe AGENTS/CLAUDE-style instruction discovery,
  prompts, skills, extensions, and model/provider defaults, with metadata-only
  archive behavior.
- Tool parity: read, edit/write, bash/shell, verification, and follow-up tool
  observations behind pipy-owned boundaries, explicit scopes, and privacy
  invariants.
- Session workflow parity: durable sessions, resume/search/inspect surfaces,
  compaction/summarization, branch/fork-style exploration, and review-cycle
  learning.
- Extension/RPC parity: extension APIs, custom commands/UI, headless protocol,
  and richer integration points after the core shell/tool/session model is
  stable.

Textual, prompt-toolkit, curses, or a small custom terminal layer are candidate
Python UI directions for the later interactive input/TUI step. Do not select
one inside the startup-chrome slice; first prove the Pi-like shell frame with
plain terminal output and the existing REPL.

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
- Native REPL next-boundary decision after proposal-only review: selected a
  human-applied proposal trial using the existing `/propose-file
  <workspace-relative-path> -- <change-request>` command as the next small
  native-shell boundary. The public REPL stays proposal-only for this next
  slice: a human may translate or apply the provider suggestion outside the
  REPL, but pipy-native must not apply edits, expose a write-capable slash
  command, run verification from the REPL, execute shell commands, add
  provider-side tools, read multiple files, create multiple tool requests, or
  expand into a general model/tool loop. The trial should reuse the existing
  approval prompt, read-only sandbox policy, explicit-file-excerpt path,
  `propose_file_repl` label, metadata-only observation and proposal events,
  and stdout/stderr/archive privacy contracts, then evaluate whether a later
  write-capable boundary is justified.
- OpenAI Codex OAuth provider correction from Pi reference: local prior art in
  `/Users/jochen/src/pi-mono` shows a distinct `openai-codex` provider path
  for ChatGPT Plus/Pro Codex subscription use. Pi implements PKCE OAuth in
  `packages/ai/src/utils/oauth/openai-codex.ts` with client id
  `app_EMoamEEZ73f0CkXaXp7hrann`, authorize/token URLs under
  `https://auth.openai.com/oauth`, redirect
  `http://localhost:1455/auth/callback`, scope
  `openid profile email offline_access`, local callback plus manual paste
  fallback, JWT `chatgpt_account_id` extraction, refresh-token support, and
  storage under `~/.pi/agent/auth.json` through
  `packages/coding-agent/src/core/auth-storage.ts`. Pi sends those credentials
  through `packages/ai/src/providers/openai-codex-responses.ts` to
  `https://chatgpt.com/backend-api/codex/responses` with
  `Authorization: Bearer <access-token>`, `chatgpt-account-id`, `originator`,
  and `OpenAI-Beta` headers. This supersedes the earlier practical assumption
  that OpenRouter should stay first for near-term provider access, while still
  rejecting credential-store scraping, token copying, CLI wrapping, raw auth
  archiving, and treating the normal `openai` API-key provider as subscription
  auth.
- Pi-like no-approval shell direction correction: `pipy-native` should follow
  Pi's "no permission popups" product posture rather than a Claude Code-style
  approval prompt model. Local Pi reference
  `/Users/jochen/src/pi-mono/packages/coding-agent/README.md` says "No
  permission popups" and Pi's read tool in
  `packages/coding-agent/src/core/tools/read.ts` reads through the configured
  tool path without an interactive approval question. Pipy should remove
  visible approval prompts and approval decision gates from explicit
  user-entered REPL read/context commands, including `/read`, `/ask-file`, and
  `/propose-file`. Keep non-interactive safety boundaries such as explicit
  command syntax, workspace-relative path validation, ignored/generated-file
  rejection, bounded size/line limits, secret-looking content rejection,
  metadata-only archives, and stdout/stderr separation. Future write or shell
  behavior should not add approval popups by default; use Pi-like operational
  posture instead: run in a trusted workspace/container or add explicit
  extension/configuration policy later if needed.
- Native REPL approval prompt removal: explicit user-entered `/read`,
  `/ask-file`, and `/propose-file` commands no longer display `pipy approval
  required` prompts or consume approval responses. These commands use
  `not-required` approval policy data while preserving one-read session limits,
  workspace-relative path validation, ignored/generated-file rejection, size
  and line limits, encoding checks, secret-looking content rejection,
  metadata-only archives, and stdout/stderr separation. The historical
  approval prompt helper remains test-covered but is no longer wired into the
  normal product REPL path.
- Native `openai-codex` OAuth provider from Pi reference:
  `pipy run` and `pipy repl` now accept
  `--native-provider openai-codex --native-model <model>` as a distinct native
  provider selection, separate from the existing `openai` API-key provider.
  `pipy auth openai-codex login` implements a pipy-owned PKCE OAuth login
  boundary with local callback and manual-paste fallback, stores credentials
  under `${PIPY_AUTH_DIR:-~/.local/state/pipy/auth}/openai-codex.json` rather
  than Pi's `~/.pi/agent/auth.json`, refreshes expiring tokens through an
  injected auth boundary, and sends one SSE Responses request to
  `https://chatgpt.com/backend-api/codex/responses` with pipy-owned
  system/user inputs, `store: false`, `stream: true`, and safe required
  provider headers. The
  provider maps final text and normalized usage into the existing provider
  result shape while JSONL, Markdown, default stdout, and
  `--native-output json` remain metadata-only. Raw auth material, tokens,
  refresh tokens, request bodies, prompts, model output, provider responses,
  headers with credentials, provider-native payloads, stdout, stderr, diffs,
  patches, command output, file contents, secrets, credentials, private keys,
  and sensitive personal data remain out of archives.
- Native REPL auth/model commands and late-bound provider selection:
  `pipy` now starts the native REPL in the current directory with slug
  `native-repl`, while `pipy repl --agent pipy-native` remains compatible.
  The REPL accepts local `/login [openai-codex]`, `/logout [openai-codex]`,
  and `/model [<provider>/<model>|<model>]` commands. Login reuses the
  pipy-owned `OpenAICodexAuthManager` OAuth boundary; logout removes
  pipy-owned OpenAI Codex credentials; model selection is resolved before each
  provider-visible turn so subsequent ordinary, `/ask-file`, and
  `/propose-file` turns use the current provider/model without restarting.
  `/model` with no args prints current selection and conservative configured
  provider/model information to stderr only. Local auth/model commands do not
  invoke providers, consume provider turns, consume the one-read limit, execute
  tools, or archive raw command text, prompts, authorization URLs, provider
  responses, tokens, auth material, excerpts, file contents, stdout, stderr,
  diffs, patches, secrets, credentials, private keys, or sensitive personal
  data. Successful model selections persist only non-secret provider/model
  defaults under local pipy state.
- Native OpenAI Codex provider SSE transport correction: the
  `openai-codex` provider now matches the local Pi reference by sending one
  SSE Responses request with `stream: true`, Responses-style user input,
  `Accept: text/event-stream`, `OpenAI-Beta: responses=experimental`,
  `store: false`, and the existing pipy-owned OAuth headers. It parses streamed
  text and terminal usage into the existing `ProviderResult` shape while
  archives remain metadata-only and continue to omit raw request bodies,
  prompts, model output, provider responses, SSE events, headers with
  credentials, auth material, tokens, file contents, diffs, stdout, stderr,
  secrets, credentials, private keys, and sensitive personal data.
- Native human-applied `/propose-file` trial through shell auth/model
  commands: the shell-first flow was run on 2026-05-09 with `uv run pipy`,
  `/login openai-codex`, `/model openai-codex/gpt-5.2`, and `/propose-file
  pyproject.toml -- <change-request>` against a 28-line file within the
  explicit-file-excerpt limits. An initial attempt against
  `tests/test_native_usage.py` failed closed with `secret_looking_content`
  because the test file intentionally contains archive privacy sentinel text,
  and the one-read session limit blocked a second read in that same REPL
  record. The successful trial produced a useful readability-only proposal to
  document the intentionally empty runtime dependencies list; the suggestion
  was manually applied outside the REPL as a one-line TOML comment. The public
  REPL remained proposal-only: no `/apply`, automatic writes, verification,
  shell execution, provider-side tools, multi-file reads, multiple tool
  requests, or general model/tool loop were exposed. Metadata-only archive
  contracts held, and the trial outcome is useful enough to justify a narrow
  write-capable boundary design slice, with model availability/defaults tracked
  separately because `gpt-5.4`, `gpt-5.1-codex`, and
  `gpt-5.1-codex-mini` were rejected by this ChatGPT-backed Codex account while
  `gpt-5.2` succeeded.
- Native write-capable REPL boundary decision after proposal trial: selected
  `/apply-proposal <workspace-relative-path>` as the next implementation slice.
  The command should be available only after a successful same-session
  `/propose-file <workspace-relative-path> -- <change-request>` result for the
  same path. It should consume one in-memory, pipy-owned, human-reviewed
  proposal draft, normalize it into a `NativePatchApplyRequest`, and invoke the
  existing `NativePatchApplyTool` boundary for exactly one file and one
  operation. The explicit slash command is the Pi-like user review signal; no
  visible approval popup should be added for normal interactive use. Safety
  checks stay non-interactive: exact pending-proposal match, workspace-relative
  target validation, ignored/generated-file rejection, bounded UTF-8 text,
  secret-looking content rejection, `mutating-workspace` policy with only
  workspace read and filesystem mutation allowed, no shell or network access,
  and expected SHA-256 validation for existing files before mutation. The raw
  proposal, replacement text, patch text, diffs, file contents, prompts, model
  output, provider responses, command output, auth material, secrets,
  credentials, tokens, private keys, and sensitive personal data must stay out
  of JSONL, Markdown, catalog/search/inspect surfaces, and structured stdout.
  Verification remains manual outside the first write-capable command; a
  separate later `/verify just-check`-style REPL slice can wire the existing
  `NativeVerificationRequest` boundary after the write path is reviewed.
- Native one-file `/apply-proposal` REPL command: the interactive native shell
  now accepts `/apply-proposal <workspace-relative-path>` only after a
  successful same-session `/propose-file` for the exact same normalized
  workspace-relative path. The command consumes one pending in-memory visible
  proposal draft, builds one `NativePatchApplyRequest` with a single
  whole-file operation, uses a pipy-owned human-reviewed allow gate and
  `mutating-workspace` policy, invokes `NativePatchApplyTool`, emits only the
  existing metadata-only `native.patch.apply.recorded` event, and clears the
  pending draft after any apply attempt, mismatch, unsupported draft, provider
  failure, local REPL command, unsupported slash command, or later
  provider-visible turn. Visible drafts without structured proposal metadata
  may enable a same-session apply, but they do not synthesize a
  `native.patch.proposal.recorded` event. It does not call a provider, run
  verification, execute shell commands, invoke provider-side tools, support
  multi-file writes, or read proposal data back from archives.
- Native REPL `/verify just-check` command: the interactive native shell now
  accepts `/verify just-check` only after a successful same-session
  `/apply-proposal` mutation. The command builds one pipy-owned
  `NativeVerificationRequest`, maps only the safe label `just-check` to the
  internal `just check` argv through `NativeVerificationTool`, emits only the
  metadata-only `native.verification.recorded` event, suppresses command stdout
  and stderr, and fails the REPL run if verification is skipped or fails. It
  does not call a provider, accept arbitrary command text, permit
  provider-selected commands, allow filesystem mutation or network access,
  retry, stream, create a provider follow-up turn, invoke provider-side tools,
  support multi-file context, or broaden `/apply-proposal`.
- Native REPL `/verify just-check` review and smoke: focused review found the
  implemented verification boundary aligned with the selected contract. Focused
  verification-tool, CLI, native-session, value-object, and documentation
  policy tests covered the post-apply-only gate, unsupported command labels,
  failed and skipped verification behavior, metadata-only archive payloads, and
  the guarantee that `/apply-proposal` does not run verification itself.
  Fake-provider terminal smoke runs exercised propose/apply/verify success and
  a failing `just check` path with real workspace mutation and real
  `NativeVerificationTool` execution. The success smoke exited `0`; the failure
  smoke exited `1` after recording only safe `command_failed` metadata.
  `pipy-session verify`, `list`, `search`, and `inspect` remained compatible
  with both finalized REPL records, and no implementation hardening was
  required.
- Native first pipy-applied, pipy-verified tiny change: on 2026-05-11, a fresh
  `pipy-native` REPL session from the repository root used
  `openai-codex/gpt-5.2` and `/propose-file pyproject.toml -- <change-request>`
  against the small non-secret `pyproject.toml` file. The visible one-file
  draft was reviewed in the terminal, then applied through `/apply-proposal
  pyproject.toml` and verified in the same session with `/verify just-check`.
  The applied change only reworded the comment above the empty runtime
  dependency list to state that no runtime dependencies are declared and that
  development tools live in the dev dependency group. Verification succeeded,
  `pipy-session verify` reported `ok`, and summary-safe `list`, `search`, and
  `inspect` surfaces remained compatible with the finalized
  `native-self-bootstrap-trial` record. The trial did not broaden provider
  auth, token storage, provider routing, model defaults, arbitrary shell
  execution, non-allowlisted verification commands, multi-file reads, multiple
  tool requests, automatic write selection, provider follow-up turns, or the
  general model/tool loop, and raw prompts, model output, proposal text, diffs,
  file contents, command output, auth material, secrets, credentials, tokens,
  private keys, and sensitive personal data remained out of the archive.
- Native next-boundary decision after the first self-bootstrap trial:
  summary-safe inspection of the finalized `native-self-bootstrap-trial`
  record showed a successful metadata-only propose/apply/verify flow with
  provider, tool, patch-apply, and verification events only. Archive reflection
  continued to favor small native shell slices, focused tests, metadata-only
  capture, and stopping review cycles after a clean second review. The repeated
  concrete friction was read recovery: the earlier human-applied proposal trial
  failed closed on a secret-looking first target, and the one-read session limit
  prevented a second explicit target in the same REPL. The selected next
  boundary is therefore a failed-read recovery slice that preserves at most one
  successful explicit file excerpt per REPL session, but allows one narrowly
  bounded failed or skipped explicit-read attempt to leave that success budget
  available. This is a narrow exception to the broader multiple-tool-request
  deferral, not multi-file context, not a second successful read, not provider
  path selection, not arbitrary shell execution, and not a general model/tool
  loop.
- Native bounded read-failure recovery for explicit REPL file commands:
  `/read`, `/ask-file`, and `/propose-file` now use split in-memory read
  budgets in the interactive native shell. One successful explicit file
  excerpt remains the per-session product boundary, shared by all three
  commands, while one failed or skipped read attempt can happen before that
  successful excerpt without consuming the success budget. Malformed supported
  slash commands, unsupported slash commands, `/help`, `/login`, `/logout`,
  `/model`, `/apply-proposal`, and `/verify just-check` remain outside both
  budgets. After a successful excerpt, later read/context attempts still fail
  closed before reading, before provider visibility, and before proposal
  parsing. A second failed recovery attempt closes the recovery path before a
  later read can run. Archive payloads remain metadata-only and add only safe
  budget booleans; raw paths, prompts, excerpts, provider output, command
  output, proposal text, patch text, diffs, file contents, auth material,
  secrets, credentials, tokens, private keys, and sensitive personal data
  remain out of JSONL, Markdown, catalog/search/inspect surfaces, stdout,
  stderr, and structured output.
- Native bounded read-failure recovery review and smoke: focused review found
  the split-budget implementation aligned with the selected contract. Existing
  and added CLI coverage pins successful excerpt budget use, failed/skipped
  attempt budget use, second failed recovery-attempt exhaustion, second
  successful read/context blocking across `/read`, `/ask-file`, and
  `/propose-file`, malformed supported command diagnostics, unsupported slash
  commands, and local `/help`, `/model`, `/apply-proposal`, and
  `/verify just-check` paths staying outside explicit-read budgets. A
  fake-provider REPL smoke exercised failed-read recovery and finalized archive
  verification. `pipy-session verify` compatibility and metadata-only archive
  rules held, no implementation hardening beyond the focused coverage pin was
  required, and no provider auth, model routing, shell execution, multi-file
  context, provider-side tools, second successful read, automatic write
  selection, or general model/tool loop behavior changed.
- Native no-tool REPL conversation-context decision after read-failure
  recovery review: summary-safe archive inspection of the latest
  read-failure recovery review/smoke records showed a clean closeout, green
  focused tests and `just check`, fake-provider REPL smoke with finalized
  archive verification, and a high-confidence recommendation to proceed to the
  next boundary decision. Adjacent self-bootstrap, `/apply-proposal`, and
  `/verify just-check` records showed that propose/apply/verify is useful and
  reviewed, but still deliberately narrow. Comparing candidate boundaries
  against the Pi-like shell direction selected bounded in-memory context for
  ordinary no-tool REPL turns as the next implementation slice: later ordinary
  non-command turns may receive prior successful ordinary no-tool user/provider
  exchanges in memory only, under explicit turn and byte limits, while file
  excerpts, proposal drafts, patch text, verification output, command output,
  provider metadata, auth material, and tool observations remain excluded from
  history. The decision slice changed no runtime behavior, stdout/stderr
  contract, archive schema, provider auth, model routing, explicit-read
  budgets, writes, verification, or tool-loop behavior.
- Native bounded no-tool REPL conversation context: ordinary successful
  non-command `pipy-native` REPL turns are now retained in a bounded in-memory
  `NativeNoToolReplConversationContext` and forwarded only to later ordinary
  no-tool provider requests as prior user/assistant messages. The history keeps
  only successful ordinary user prompts and provider final text, is bounded by
  the REPL provider-turn limit and a 4 KiB provider-visible byte budget, drops
  oldest exchanges before provider visibility, and clears on login, logout,
  provider/model changes, provider failure, or ambiguous carryover paths.
  `/read`, `/ask-file`, `/propose-file`, `/apply-proposal`, `/verify
  just-check`, `/help`, `/clear`, `/login`, `/logout`, `/model`, malformed
  commands, and unsupported slash commands stay outside retained history.
  Archives remain
  metadata-only and record only safe provider-call forwarded-context counters
  and terminal session retained-at-end context counters; raw
  prompts, provider final text, excerpts, questions, change requests,
  proposal text, patch text, diffs, file contents, command output, provider
  metadata, tool observations, auth material, secrets, credentials, tokens,
  private keys, and sensitive personal data remain out of JSONL, Markdown,
  catalog/search/inspect surfaces, stdout, stderr, and structured output.
- Native bounded no-tool REPL conversation context review and smoke:
  summary-safe archive checks showed the implementation had already received a
  two-round independent review cycle. The first round found one warning and
  three suggestions, all accepted and fixed; the second round reported zero
  findings and recommended stopping the review cycle. The implementer-side
  closeout audit of the in-memory history boundary, provider request shapes,
  clear conditions, fixed bounds, and metadata-only counters found no
  additional issues.
  Focused no-tool context tests, the CLI finalized-record coverage pin, `just
  check`, and a fake-provider REPL smoke with two ordinary turns plus
  `pipy-session verify` all passed. The next selected native-shell boundary is
  a local `/clear` command because bounded history is now useful enough to need
  an explicit user reset path, while provider-side tools, arbitrary shell
  execution, multi-file reads, broader writes, streaming, retries, fallback,
  TUI/RPC, and a general model/tool loop remain deferred.
- Native local `/clear` REPL command: the interactive native shell now accepts
  `/clear` as a local command that clears retained bounded no-tool conversation
  context and discards any pending same-session proposal draft without invoking
  providers, tools, reads, writes, auth, verification, shell commands, or
  provider-visible context handoff. `/clear` appears in `/help` and static
  usage diagnostics; malformed `/clear <text>` stays local and does not clear
  history. The command does not reset provider/model selection, auth state,
  read budgets, verification availability, or provider turn indexes. Archives
  remain metadata-only and do not persist raw command text, prompts, provider
  output, excerpts, proposals, diffs, file contents, stdout, stderr, auth
  material, secrets, credentials, tokens, private keys, or sensitive personal
  data.
- Native local `/clear` review and smoke: the implemented `/clear` boundary
  completed a two-round independent review cycle. The first review found no
  critical or warning findings and two suggestion-level test coverage items;
  both were accepted and fixed by adding post-clear verification availability
  coverage and stronger no-tool-event assertions. The second review found no
  findings and recommended stopping the review cycle. Focused tests, `just
  check`, and a fake-provider `/clear` REPL smoke with `pipy-session verify`
  passed. No runtime behavior beyond the implemented local clear command was
  broadened.
- Native next-boundary decision after `/clear` review and smoke: summary-safe
  archive reflection found the `/clear` implementation review cycle clean
  after the two accepted coverage fixes, with a clean second review and a
  later closeout audit both recommending no further review unless scope or
  risk changes. Adjacent workflow evaluations continued to favor small native
  shell boundaries, metadata-only archive behavior, focused tests, and
  stopping after clean second reviews. The selected next boundary is a local
  `/status` REPL command that reports only safe shell-state labels and counters
  on stderr without invoking providers, tools, reads, writes, verification, or
  archive-visible raw command content. This decision slice changed no runtime
  behavior.
- Native local `/status` REPL command: the interactive native shell now accepts
  `/status` as a local command that prints only safe shell-state labels and
  counters to stderr: provider/model selection, provider turn count and limit,
  retained no-tool history counts and bytes, explicit-read budget booleans,
  pending proposal availability, and verification availability. `/status`
  appears in `/help` and static usage diagnostics; malformed `/status <text>`
  stays local. The command does not invoke providers, tools, reads, writes,
  patch apply, verification, shell commands, provider-visible context, or
  provider-side tools; it does not consume budgets or provider turns, mutate
  retained history, clear pending proposals, change provider/model selection,
  change auth state, change verification availability, emit archive events, or
  archive raw command text.
- Native next-boundary decision after `/status`: summary-safe archive
  reflection and `/status` review outcomes showed the local state-inspection
  boundary is clean, with recent native REPL records exercising the shell and
  repeated workflow evaluations favoring small UI/runtime slices. The selected
  next boundary is Pi-like REPL startup chrome: a plain terminal startup/header
  and safe shell-state presentation pass inspired by Pi's initial screen,
  sharing static help/status data where practical. This is a user-facing shell
  ergonomics slice, not a TUI/RPC slice, not provider-side tool use, not
  arbitrary shell execution, not multi-file context, not persistent transcript
  storage, and not a general model/tool loop.

## Next Slice

### Native Pi-like REPL startup chrome

Goal: make bare `pipy` and `pipy repl --agent pipy-native` open with a compact
Pi-like native shell frame while preserving the current command, privacy, and
archive boundaries.

Completion focus:

- add a startup header on stderr with `pipy` version, compact interrupt/exit
  and slash-command affordances, and one short product sentence
- keep all startup chrome on stderr before the first input prompt; stdout
  remains reserved for provider final text and explicit command output
- show safe loaded-context/resource labels such as discovered instruction file
  labels and supported local command groups without reading or printing file
  contents
- reuse the same safe state data as `/status` where practical so provider/model,
  workspace, and budget indicators do not drift across shell surfaces
- keep this as plain terminal output; do not introduce a full-screen TUI,
  alternate screen buffer, keybinding framework, RPC mode, or long-running
  daemon
- keep deferred boundaries closed: arbitrary shell execution, provider-side
  tools, multi-file context, non-allowlisted verification commands,
  persistent transcript storage, raw prompt/model-output display, TUI/RPC
  behavior, and a general model/tool loop

## Near Term

The near-term product direction is still a real `pipy-native` runtime with a
Pi-like interactive shell. The shell should be a thin user interface over
pipy-owned provider, session, turn, tool, sandbox, and archive boundaries, not
a separate runtime and not a wrapper around Codex, Claude, Pi, or another
agent CLI. The product posture is now explicitly Pi-like: no permission
popups for normal interactive use.

The immediate path is now Pi-like REPL startup chrome after the implemented and
reviewed local `/status` command. The decision slice used summary-safe archive
reflection, the current shell surface, the screenshot-driven product direction,
and the deferred boundaries below to select exactly one small native-shell
slice before runtime behavior changes again.

Manual `pipy run --agent pipy-native` smoke tests are useful product checks,
but today they exercise a one-shot runner: `--goal` is the input, provider final
text is stdout, finalization is stderr, and the process exits. The persistent
shell is available through bare `pipy` or `pipy repl --agent pipy-native`; it
now has local `/help`, `/clear`, `/status`, `/login`, `/logout`, and `/model`
commands, one display-only `/read <workspace-relative-path>` command, and one
provider-visible `/ask-file <workspace-relative-path> -- <question>` command
with a whitespace-delimited `--` separator sharing the same successful-read
budget.
Explicit read/context commands do not display approval popups. Auth/model
commands and malformed or unsupported slash commands print stderr diagnostics
without provider/tool execution, read-limit consumption, or raw command
archiving. The REPL can now apply exactly one same-session reviewed proposal
through `/apply-proposal <workspace-relative-path>` and then run one
allowlisted post-apply verification command through `/verify just-check`, but
it still does not execute arbitrary shell commands, run non-allowlisted
verification commands, expose provider-side tools, or support a general
model/tool loop.
The proposal-only
`/propose-file <workspace-relative-path> -- <change-request>` command is now
implemented, reviewed, and trialed with a real `openai-codex` provider turn;
the first public write boundary is implemented as the narrow same-session
`/apply-proposal <workspace-relative-path>` command, and the first public
verification boundary is implemented and reviewed as `/verify just-check`;
broader tool-loop boundaries remain deferred.

Provider access is corrected back to OpenAI Codex subscription auth as the
preferred near-term real-provider path. The existing `openai` provider remains
the pay-by-token OpenAI Platform API-key baseline, while the implemented
subscription path is the separate `openai-codex` provider modeled on Pi's PKCE
OAuth and `chatgpt.com/backend-api/codex/responses` implementation.
OpenRouter remains implemented and useful for immediate manual smoke testing
with `OPENROUTER_API_KEY`, but it should no longer be treated as the preferred
default provider-access direction. Anthropic subscription access is not a
near-term native provider target because subscription-backed coding-agent usage
is expected to stay within Claude Code. Local-model providers remain
interesting but deferred until benchmark work in a separate repo clarifies
whether Ollama, llama.cpp, MLX, LM Studio, or another runtime should be the
first local integration.

The broader slopfork direction is Pi parity through pipy-owned Python
boundaries. Startup chrome is the first visible parity step; the next larger UI
question is whether to adopt Textual, prompt-toolkit, curses, or a small custom
terminal layer for Pi-style editor/footer/overlay behavior. That framework
choice is deliberately deferred until the plain startup shell frame is useful
and tested.

Small reviewable slices, in intended order:

1. Native Pi-like REPL startup chrome.

Foundation gates toward an interactive shell:

- One-shot provider gate: available now through `pipy run --agent
  pipy-native`; useful for provider smoke tests but not an interactive shell.
- Conversation-state gate: available now in the one-shot runtime; provider turn
  indexes and labels are allocated from per-run in-memory conversation state
  without changing archive or stdout contracts.
- No-tool provider-turn REPL gate: available now through `pipy repl --agent
  pipy-native`.
  It reuses the same conversation state for repeated no-tool provider turns and
  keeps archives metadata-only. Later ordinary no-tool turns now receive
  bounded in-memory history only from prior successful ordinary no-tool
  exchanges; file/tool/write/verification context is not retained in that
  history. The implementation is reviewed and smoked.
- Local conversation clear gate: available now through `/clear`. It resets
  bounded retained no-tool conversation context and pending proposal state
  through a local command path without provider calls, tool execution,
  read-budget consumption, raw command archiving, provider/model selection
  changes, auth changes, or broader model/tool-loop behavior. The gate is
  reviewed and smoked.
- Next-boundary decision gate after local clear: available now. Summary-safe
  session reflection and the current shell constraints selected a local
  `/status` command as the next native-shell boundary without changing runtime
  behavior or crossing deferred tool-loop, shell-execution,
  multi-file-context, TUI/RPC, or persistent-transcript boundaries.
- Local status command gate: available now through `/status`. It reports only
  safe shell-state labels and counters on stderr, including provider/model
  selection, provider turn count/limit, retained no-tool history counts and
  byte counts, explicit-read budget booleans, pending proposal availability,
  and verification availability. It does not invoke providers, tools, reads,
  writes, verification, shell commands, or archive raw command text.
- Pi-like startup chrome decision gate: available now. The next implementation
  slice should make the shell startup/header/status presentation feel closer to
  Pi while staying line-oriented and privacy-safe; it must not add a full TUI,
  new keybinding runtime, new provider/tool capability, broad context loading,
  raw transcript display, or archive content beyond existing metadata.
- Historical visible approval prompt gate: available as test-covered helper
  code, but removed from the normal product REPL path.
- Narrow read-only shell command gate: available now through `/read
  <workspace-relative-path>`, currently bounded to one explicit-file-excerpt
  request per REPL session without approval popups.
- Provider-visible interactive context gate: available now through `/ask-file
  <workspace-relative-path> -- <question>` with a whitespace-delimited `--`
  separator, bounded to one explicit-file-excerpt request shared with
  `/read`, one in-memory provider handoff, and metadata-only archive handling.
- Command help and usage-diagnostic gate: available now through local `/help`
  and static stderr usage diagnostics for malformed or unsupported slash
  commands. These paths do not invoke providers, execute tools, consume the
  one-read limit, emit tool events, or archive raw command text, paths,
  questions, or excerpt data.
- Auth/model command gate: available now through local `/login`, `/logout`, and
  `/model` commands. These reuse the pipy-owned OpenAI Codex auth boundary,
  late-bind provider construction before each provider-visible turn, persist
  non-secret provider/model defaults, and keep auth/model status on stderr
  without consuming provider turns or the one-read limit.
- Proposal-only interactive file gate: available now through `/propose-file
  <workspace-relative-path> -- <change-request>`. It reuses the existing
  explicit-file-excerpt path, sends one in-memory excerpt plus request to one
  provider turn labeled `propose_file_repl`, records only allowlisted
  metadata-only patch proposal status, and stops before any mutation,
  verification, shell execution, network access, or follow-up provider turn.
- Proposal-only review gate: available now. Focused tests and fake-provider
  terminal smoke covered the boundary and found no required implementation
  hardening; broader mutation remains behind a new named slice.
- Human-applied proposal trial gate: available now. A real `openai-codex`
  proposal turn produced a useful tiny readability change that was manually
  applied outside the REPL, confirming that the workflow is ready to inform a
  later write-capable boundary.
- One-file write-boundary decision gate: available now. The selected first
  public mutation command is `/apply-proposal <workspace-relative-path>`,
  limited to a same-session human-reviewed proposal for one file and one
  operation, with metadata-only archives.
- Allowlisted verification gate: available now through `/verify just-check`
  after a successful same-session `/apply-proposal`. It records only
  metadata-only status, suppresses command stdout/stderr, and fails the REPL run
  on skipped or failed verification.
- Read-failure recovery review gate: available now. The split explicit-read
  budget implementation is reviewed and smoked, and summary-safe archive
  signals selected no-tool REPL conversation context as the next boundary.

Self-bootstrap readiness gates remain historical context for supervised writes:

- Proposal-only trial: available now in the interactive shell. Pipy may use the
  existing bounded read-only context and propose structured edit metadata, but
  writes remain manual and archives stay metadata-only.
- Human-applied patch trial: selected through the existing `/propose-file`
  command. No public write command is added for this gate, and independent
  review is still required before trusting the result.
- Pipy-applied patch trial: available now through
  `/apply-proposal <workspace-relative-path>`, with conservative one-file
  scope, no arbitrary shell execution, and metadata-only archives.
- Verified patch trial: available now through `/verify just-check` after a
  successful same-session `/apply-proposal`, starting with `just check` and
  recording only safe status, exit-code, duration, and label metadata;
  stdout/stderr remain excluded from archives.

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

### Deferred For Self-Bootstrap

- Full tool-capable native pipy agent runtime beyond the provider,
  conversation, approval, sandbox, and tool-boundary slices.
- General native model/tool loop beyond bounded provider turns and explicitly
  approved tool boundaries.
- Broad repo maps or persistent workspace summaries beyond the first bounded
  provider-visible context policy.
- Local model provider integrations for Ollama, llama.cpp, MLX, LM Studio, or
  similar runtimes until separate benchmark work identifies the best first
  local runtime and connection shape.
- Generic OpenAI subscription-backed native provider auth beyond the distinct
  `openai-codex` provider path until official OpenAI docs expose a stable
  third-party/native provider auth flow that is not specific to Codex,
  ChatGPT, or another OpenAI product client.

### Deferred For Product Maturity

- Codex JSONL event adapter.
- Claude integration beyond the existing conservative `pipy-session auto`
  metadata capture.
- Pi-native session inspection beyond metadata references.
- Raw transcript import with explicit opt-in and redaction policy.
- Indexed archive search or SQLite-backed query layer.
- Review-cycle metadata for `pipy-session workflow review-outcome`, including
  explicit per-round versus cumulative scope, review round number, and optional
  cycle identity so `reflect` can avoid double-counting iterative reviews.
- Interactive TUI, including the decision between Textual, prompt-toolkit,
  curses, or a small custom terminal layer for Pi-style editor/footer/overlay
  behavior.
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
- Building broad approvals, sandboxing, retries, streaming, additional OAuth
  providers, provider registry, raw transcript import, multiple native tool
  requests, post-tool provider turns, general write tools beyond supervised
  patch apply, non-allowlisted verification commands, Textual or another TUI
  framework, RPC, compaction, branching, or orchestration in the upcoming
  slices; real execution work must wait for its named slice.
- Using unsupported subscription auth, scraping browser or CLI session stores,
  or treating another product's login/session as pipy-native provider
  credentials.

## Maintenance Notes

- Move completed slices from `Next Slice` or `Near Term` to `Done` in the same
  change that implements them.
- Keep deferred items here brief; put detailed design and rationale in
  `docs/harness-spec.md`.
- Keep archive and privacy rules aligned with `docs/session-storage.md`.

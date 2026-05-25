# Pipy Backlog

Status: planning index

This backlog records the current implementation direction at a task-slice
level. It is not a full issue tracker. Use it to choose the next small,
reviewable change while keeping the source-of-truth design constraints in
`docs/harness-spec.md` and `docs/session-storage.md`.

## Current State

Pipy has crossed from capture-only infrastructure into a small native product
runtime. The current native shell can authenticate to the `openai-codex`
provider, switch models, make ordinary no-tool provider turns, read explicit
workspace-relative file excerpts, ask a provider about one excerpt,
request a proposal-only change for that excerpt through `/propose-file`, and
apply one same-session reviewed proposal through `/apply-proposal`, then run
one post-apply allowlisted verification command through `/verify just-check`.
It can also clear retained no-tool conversation context locally with `/clear`
and inspect local shell state with `/status`; bare `pipy` and
`pipy repl --agent pipy-native` now print styled, sectioned startup chrome with
safe metadata-only resource labels before the first prompt, `/help` plus static
usage diagnostics show grouped slash-command discovery, each REPL input prompt
now carries safe state labels for provider/model, turns, read, proposal, and
verification availability, and the REPL now reads input through a small adapter
boundary with plain captured-stream fallback plus optional prompt-toolkit
line-editor support, including leading slash-command name completion, when
explicitly selected or auto-available on real TTY streams. The optional
prompt-toolkit path now also suggests workspace-relative file/path labels while
editing the path argument for explicit file commands, inserts completion-only
`@file` reference labels in ordinary prompts and supported command free-text,
and supports prompt-toolkit-only multiline entry with Enter submitting and
Esc+Enter inserting a newline, and the prompt-toolkit completion adapter now
supports the async completion protocol used by current prompt-toolkit releases.
The existing optional real-TTY prompt-toolkit input path is now hardened for
cursor-position warning noise and LF-encoded Enter/Esc+Enter key sequences,
and explicit file-context commands now share a two-successful-excerpt REPL
budget. The bottom-toolbar status decision remains deferred. The public shell
still cannot execute arbitrary shell commands, request provider-side tools,
read multiple files in one provider turn, run non-allowlisted verification
commands, or run a general model/tool loop.

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
  command for safe shell-state inspection, a compact startup chrome pass, a
  styled Pi-like visual/resource-label pass, the grouped-help input-ergonomics
  decision, grouped slash-command discovery, the post-help prompt-label
  decision, the safe state-aware prompt label, the terminal-layer direction
  checkpoint, and the prompt-toolkit line-editor feasibility boundary are now
  implemented; the first richer prompt-toolkit follow-up, leading slash-command
  name completion, is now implemented; workspace-relative file/path completion
  for explicit file commands is now implemented; prompt-toolkit-only multiline
  entry is now implemented; the bottom-toolbar status decision is complete and
  deferred footer behavior; the optional real-TTY prompt-toolkit input
  hardening pass is now implemented; and prompt-toolkit-only `@file` reference
  completion is now implemented; and the narrow explicit multi-file context
  budget is now implemented while any automatic file-content reads, full TUI,
  alternate-screen app, or general keybinding runtime remains deferred.

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
  Current state: styled Pi-like startup/resource labels, grouped
  slash-command discovery, and safe prompt-state labels are implemented.
- Interactive input ergonomics: the first input-adapter boundary now preserves
  plain captured-stream fallback and can use optional prompt-toolkit line-editor
  input on real TTY streams, with leading slash-command name completion and
  workspace-relative path completion for explicit file commands, completion-only
  `@file` references in ordinary prompts and supported command free-text, and
  prompt-toolkit-only multiline entry in that optional path. The real-TTY
  prompt-toolkit path is hardened against cursor-position warning noise and
  LF-encoded newline key sequences. Explicit file-context commands now share a
  two-successful-excerpt REPL budget. Resilient terminal resize behavior,
  persistent history, and a fuller TUI remain future slices.
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

Textual, prompt-toolkit, curses, and a small custom terminal layer were compared
at the terminal-layer checkpoint. The selected direction is a narrow
`prompt-toolkit` line-editor adapter investigation because it is the best fit
for multiline input, completions, and a bottom-toolbar-style status line while
preserving the current stdout/stderr and metadata-only contracts. Textual,
curses, and a custom terminal layer remain deferred until the product needs a
fuller UI surface or lower-level terminal ownership.

## Tool-Loop Parity Track

The next-boundary decision after the explicit multi-file context budget selects
a bounded model-selected tool loop behind
`pipy repl --agent pipy-native --repl-mode tool-loop` as the next visible
Pi-parity step. It is planned as twelve reviewed slices that ship alongside
the existing no-tool REPL and the existing slash-command boundaries. None of
the slices land in one change; each is a named conventional commit with
focused tests, `just check`, updated docs, and a stop for review.

Use this section together with the matching design notes in
`docs/harness-spec.md` (`Native Tool-Loop Parity Track`) and the parity-map
entry in `docs/pi-parity.md` (`Native Tool-Loop Parity Track`).

### Goal

- A real model-driven loop over `openai`, `openai-codex`, and `openrouter` with
  bounded `read`, `write`, `edit`, `ls`, `grep`, and `find` tools, producing a
  useful end-to-end change against this repo with `just check` green.
- Pi-shaped behavior: the model picks files, edits them directly, the resulting
  unified diff is written to stderr, no approval popups appear, and the loop
  iterates within a bounded tool budget.
- Slash commands `/read`, `/ask-file`, `/propose-file`, `/apply-proposal`, and
  `/verify just-check` keep working unchanged in both `--repl-mode no-tool` and
  `--repl-mode tool-loop`.

### Planned Slices

1. Docs only. Record the tool-loop parity goal, invariants, and deferred work
   in `docs/pi-parity.md`, `docs/backlog.md`, and `docs/harness-spec.md`.
2. `tools/base.py` contracts: `ToolDefinition`, `ToolRequest`,
   `ToolExecutionResult`, `ToolArgumentError`, `ToolContext`, and `ToolPort`,
   built from stdlib dataclasses with manual JSON-schema validation. Focused
   contract tests, no provider or REPL wiring.
3. `ProviderPort` extension: a `supports_tool_calls` capability flag (real
   providers stay `False`), a `ProviderToolCall` value object, `tool_calls` on
   `ProviderResult`, and a provider-agnostic message envelope
   (`user`/`assistant`/`tool_result`). The fake provider gains
   `programmable_tool_calls` for tests; real adapters stay inert.
4. `NativeToolReplSession` skeleton: bounded turn loop with `--tool-budget`
   defaulting to 10 (max 25), malformed tool arguments returned to the model as
   an observation (fatal after three consecutive malformed turns), a test-only
   `_FixtureTool` injected by tests, and an empty production tool registry.
5. `read` tool: reuses `read_only_tool.py` validation. The first real provider
   adapter flips `supports_tool_calls` to `True`; a manual smoke run lands with
   the slice.
6. `ls` tool: bounded directory entries returned as workspace-relative paths.
7. `grep` tool: `subprocess.run` to `rg` with no `shell=True`, a fixed argv, a
   workspace `cwd`, a timeout, and bounded results, with a stdlib fallback when
   `rg` is unavailable.
8. `find` tool: bounded glob lookup.
9. `write` tool: create-only; refuses existing files, `.git`, and paths that
   escape the workspace; applies directly and writes the unified diff to
   stderr. Tests pin: file mutation, diff lands only on stderr, archive remains
   untouched, and the diff lands in the opt-in sidecar only when enabled.
10. `edit` tool: string-replace with a unique-`old_string` default and an
    opt-in `replace_all`; reuses `patch_apply.py`. Same diff and archive
    privacy tests.
11. Opt-in `TranscriptSink`: a sidecar JSONL at
    `~/.local/state/pipy/transcripts/<id>.jsonl`, enabled by
    `--archive-transcript`, marked sensitive, written outside the metadata
    archive, and excluded from `pipy-session list/search/inspect`. Focused
    privacy tests.
12. Flip the default `--repl-mode` to `tool-loop` when the selected provider
    supports tool calls. The `no-tool` mode stays available. Update README and
    user-facing docs.

### Invariants

These hold throughout the track, not as later deferrals:

- Metadata-first archive privacy is preserved exactly across the whole track.
  `pipy_session.recorder` records no prompts, model text, tool payloads, file
  contents, or diffs in any slice. Any leak fails the slice.
- `.git` is default-deny across all model-driven tools. Slash commands are
  unaffected.
- No new runtime dependencies. Stdlib plus manual dict validation only; no
  pydantic.
- `NativeToolResult` carries archive-safe metadata only;
  `ToolExecutionResult` carries provider-visible payloads. The two shapes are
  not conflated.
- The internal pipy-owned `tool_request_id` does not leak as a provider id;
  provider identifiers are carried separately as `provider_correlation_id`.
- The existing no-tool REPL and the listed slash commands keep working in both
  modes.
- Each slice ships focused tests, a green `just check`, updated docs, a
  conventional commit, and stops for review.

### Out Of Scope For This Track

These remain explicitly deferred while the tool-loop track lands and after it
lands:

- A `bash` tool or any arbitrary shell execution tool.
- Generalizing `/verify` beyond the allowlisted `just check` boundary.
- Session resume, branch/fork navigation, and compaction.
- RPC mode and SDK embedding.
- Extensions, skills, prompt templates, and theme/package loading.
- Automatic `@file` content reads from completion-only references.
- Persistent shell history and a full interactive TUI.
- Additional providers beyond `openai`, `openai-codex`, and `openrouter`.
- Removing the no-tool REPL or its slash-command boundaries.

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
  invoke providers, consume provider turns, consume explicit-read budgets,
  execute tools, or archive raw command text, prompts, authorization URLs, provider
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
- Native Pi-like REPL startup chrome: bare `pipy` and
  `pipy repl --agent pipy-native` now print compact plain terminal startup
  chrome to stderr before the first prompt. The chrome includes
  the `pipy` version, interrupt/exit/help affordances, one short native-shell
  product sentence, static safe resource/command labels, and a compact status
  line derived from the same safe display state used by `/status`: provider,
  model, workspace label, provider turns, retained no-tool context, read
  budget, pending proposal availability, and verification availability. It
  does not call providers, tools, reads, writes, verification, shell commands,
  network access, provider-visible context, or provider-side tools; it does not
  consume provider turns, mutate retained history, change provider/model/auth
  state, consume read budgets, or archive raw startup text. Stdout remains
  reserved for provider final text and explicit command output.
- Native next-boundary decision after startup chrome: first implementation
  review found no critical or warning findings and identified only
  suggestion-level polish and documentation follow-ups. User screenshot
  comparison confirmed the implemented chrome is functionally correct but not
  visually close to Pi yet. The selected next boundary is a Pi-like
  visual/resource-label pass: ANSI styling, clearer section hierarchy, better
  wrapping, workspace/model/footer-style labels, and metadata-only resource
  labels for context, skills, prompts, and extensions where safe label sources
  exist. This next slice must remain presentation-oriented and must not read
  file contents, load broad context, add a TUI framework, execute shell
  commands, expose provider-side tools, or change archive privacy.
- Native Pi-like startup visual/resource-label pass: bare `pipy` and
  `pipy repl --agent pipy-native` now render the startup frame as sectioned
  stderr chrome before the first prompt, with ANSI title/section/dim styling
  only for suitable TTY streams and a plain fallback for captured or non-TTY
  streams. The frame wraps long text, groups controls, safe resources, and
  status data, shows a compact footer-style workspace/model/turn label, and
  discovers only existence-level workspace-relative resource source labels for
  context, skills, prompts, and extensions. It does not read file contents,
  list resource contents, invoke providers or tools, execute shell commands,
  consume provider turns or read budgets, mutate retained history, change
  provider/model/auth state, or add archive content.
- Local Zensical documentation preview/build: the repository now has a minimal
  local documentation site for the existing Markdown docs. `zensical.toml`
  configures a short explicit nav for `docs/index.md`, this backlog, the
  harness spec, and the session-storage policy; `just docs-serve` starts the
  local preview server and `just docs-build` builds the static site into
  ignored `site/` output. Zensical is a dev/tooling dependency only. This
  docs-tooling slice does not publish docs, add CI/deploy workflows, add a web
  UI, change native runtime behavior, change session archive layout, or weaken
  the metadata-only privacy rules.
- Native input-ergonomics decision after startup chrome: inspection of the
  existing line-oriented REPL showed a useful asymmetry: startup chrome and
  `/status` are grouped by concern, while `/help` and malformed-command
  diagnostics still print one flat command list. Summary-safe session
  reflection and Pi parity notes support keeping the next step small and
  line-oriented. The selected next implementation slice is grouped
  slash-command discovery: make `/help` and static supported-command
  diagnostics present the existing commands in stable concern-based groups,
  with concise command shapes and no new execution capability. This decision
  explicitly defers multiline input, autocomplete, file references, terminal
  resize behavior, a keybinding framework, and any full TUI framework.
- Native grouped slash-command discovery: `/help`, malformed supported-command
  diagnostics, and unsupported slash-command diagnostics now render one stable
  grouped command reference on stderr. The groups cover controls, local state,
  provider/model, file context, proposal, verification, and exit. The shared
  renderer stays line-oriented and captured-stream friendly, invokes no
  providers or tools by itself, consumes no provider turns or explicit-read
  budgets, archives no raw command text, and leaves command names, parser
  behavior, stdout/stderr contracts, provider visibility, read budgets,
  proposal/apply/verification behavior, and metadata-only archive rules
  unchanged.
- Native post-help input ergonomics decision: inspection of the implemented
  line-oriented REPL, grouped help/status output, Pi parity notes, and
  summary-safe session archive signals selected one more line-oriented
  implementation boundary before any TUI/input-runtime investigation. The next
  slice is a state-aware prompt label before each input, reusing the existing
  safe display-state data for provider/model, turn, read, proposal, and
  verification availability labels. This decision explicitly keeps multiline
  input, autocomplete, file references, terminal resize behavior, a keybinding
  framework, Textual/prompt-toolkit/curses selection, RPC, broader context
  loading, and any new execution capability deferred.
- Native line-oriented state-aware prompt label: bare `pipy` and
  `pipy repl --agent pipy-native` now replace the fixed prompt with a compact
  stderr prompt label before each input. The label is derived from the same
  safe display state used by startup chrome and `/status`, showing
  provider/model reference, provider turn count/limit, read availability,
  pending proposal availability, and verification availability. It updates
  after ordinary provider turns and local state changes from `/model`, `/read`,
  `/ask-file`, `/propose-file`, `/apply-proposal`, `/verify`, `/clear`,
  `/login`, `/logout`, `/help`, and `/status` without changing command names,
  parser behavior, stdout contracts, provider visibility, read budgets,
  proposal/apply/verification behavior, archive event shapes, or
  metadata-only privacy rules.
- Native terminal-layer direction checkpoint: inspection of the implemented
  line-oriented shell, Pi parity needs, and current privacy/runtime contracts
  selected a narrow `prompt-toolkit` line-editor adapter investigation as the
  next implementation boundary. Textual was judged too application-like for
  the next slice because it implies a fuller TUI runtime and alternate-screen
  questions; curses stays too low-level and terminal-owning for portable
  editor/completion behavior; a custom terminal layer preserves zero
  dependencies but would recreate keybinding, completion, and resize machinery
  prematurely. `prompt-toolkit` is the best next candidate because it can be
  isolated behind an input adapter, keep the current plain line-oriented
  runtime as the required fallback, and later support multiline input,
  completion, and
  bottom-toolbar-style status without changing command names, parser behavior,
  stdout/stderr contracts, provider-turn behavior, read budgets,
  proposal/apply/verification behavior, archive event shapes, or
  metadata-only privacy rules.
- Native prompt-toolkit line-editor feasibility boundary:
  `NativeNoToolReplSession` now reads input through a small internal adapter
  boundary. The default `auto` runtime keeps plain stdin/stderr behavior for
  captured and non-TTY streams, can use optional prompt-toolkit line-editor
  input only on the process stdin/stderr TTY streams when the package is
  available, and exposes `--input-runtime plain|prompt-toolkit|auto` for
  explicit smoke testing. The first prompt-toolkit path is input-only and
  presentation-only: it does not add multiline input, history persistence,
  autocomplete, file references, alternate-screen behavior, overlays, RPC,
  provider-side tools, arbitrary shell execution, broader context loading, or
  new provider-visible behavior. Focused tests cover plain input, auto
  captured-stream fallback, explicit prompt-toolkit rejection on captured
  streams, and a faked prompt-toolkit TTY line-editor path. Native REPL archive
  events record only the safe `input_runtime` label and remain metadata-only.
- Native prompt-toolkit slash-command completion boundary:
  The prompt-toolkit REPL input path now attaches a small leading slash-command
  completer that suggests existing command names such as `/help`, `/status`,
  `/model`, `/read`, `/ask-file`, `/propose-file`, `/apply-proposal`,
  `/verify`, `/exit`, and `/quit` only while editing the first slash-prefixed
  token. The plain input path, captured-stream fallback, command names, parser
  behavior, stdout/stderr contracts, prompt labels, provider turns, read
  budgets, proposal/apply/verification behavior, and archive event shapes are
  unchanged. Prompt-toolkit remains an optional opportunistic line-editor path,
  not a declared runtime dependency. The completion layer does not add
  multiline input, file/path completion, file references, persistent history,
  a bottom toolbar, a full-screen TUI, an alternate screen buffer, overlays,
  RPC, broad context loading, automatic file-content reads, arbitrary shell
  execution, provider-side tools, non-allowlisted verification, persistent
  transcript storage, raw prompt/model-output display, or a general model/tool
  loop. Focused tests cover the attached completer, command-prefix filtering,
  and unchanged fallback behavior.
- Native prompt-toolkit file/path completion boundary:
  The prompt-toolkit REPL input path now suggests existing workspace-relative
  path labels only while editing the path argument for `/read`, `/ask-file`,
  `/propose-file`, and `/apply-proposal`. Completion stays prompt-toolkit-only,
  optional, opportunistic, and presentation-only: captured streams, non-TTY
  streams, and explicit `--input-runtime plain` keep the plain adapter, while
  command handlers remain the source of truth for path validity, ignored or
  generated file rejection, read budgets, proposal matching, apply behavior,
  verification behavior, and provider-visible context handoff. The completer
  lists directory entries but does not read file contents, invoke providers,
  execute tools, mutate workspace state, consume budgets, archive raw command
  text, or store completion buffers. It does not add multiline input, file
  references, persistent history, bottom toolbar behavior, full-screen TUI,
  alternate screen buffer, overlays, RPC, broad context loading, arbitrary
  shell execution, provider-side tools, non-allowlisted verification,
  persistent transcript storage, raw prompt/model-output display, or a general
  model/tool loop.
- Native prompt-toolkit multiline input boundary:
  The prompt-toolkit REPL input path now enables multiline editing only on the
  optional real-TTY prompt-toolkit adapter. Enter submits the current buffer so
  existing command entry remains one-line-compatible, while Esc+Enter inserts a
  newline for ordinary provider prompts or explicit command text. Captured
  streams, non-TTY streams, and explicit `--input-runtime plain` continue to
  use plain `readline()` input. The boundary does not add persistent history,
  file references, bottom toolbar behavior, full-screen TUI, alternate screen
  buffer, overlays, RPC, broad context loading, automatic file-content reads,
  arbitrary shell execution, provider-side tools, non-allowlisted
  verification, persistent transcript storage, raw prompt/model-output
  display, or a general model/tool loop. Archives still record only the safe
  `input_runtime` label and never raw input buffers.
- Native prompt-toolkit bottom-toolbar status decision:
  The decision is to defer bottom-toolbar behavior for now and make the next
  concrete native-shell slice a real-TTY prompt-toolkit hardening pass. The
  existing prompt label already shows the safe provider/model, turn,
  read/proposal/verification availability labels at the input point, while
  startup chrome and `/status` cover the fuller safe state view. Summary-safe
  session checks favored small native input-adapter slices, and a real PTY
  smoke found prompt-toolkit compatibility work to address before adding
  another display surface. This slice fixed the current prompt-toolkit async
  completion protocol compatibility gap inside the adapter without changing
  command parsing, stdout/stderr contracts, provider turns, read budgets,
  proposal/apply/verification behavior, archive event shapes, optional
  dependency posture, or metadata-only privacy rules. Bottom-toolbar behavior,
  persistent footer ownership, full-screen TUI, alternate screen buffer,
  overlays, selectors, RPC, broad context loading, automatic file-content
  reads, arbitrary shell execution, provider-side tools, non-allowlisted
  verification, persistent transcript storage, raw prompt/model-output display,
  and a general model/tool loop remain deferred.
- Native prompt-toolkit real-TTY input hardening:
  The optional prompt-toolkit REPL input adapter now disables prompt-toolkit
  cursor-position requests on its stderr output object, avoiding cursor-position
  warning noise observed in test PTYs without adding footer behavior or
  changing the plain input fallback. The multiline key bindings now handle both
  CR and LF terminal encodings for Enter-submit and Esc+Enter newline
  insertion. Focused tests pin explicit prompt-toolkit setup, CPR disabling,
  CR/LF key-sequence handling, sync and async completion surfaces, file/path
  completion, captured-stream fallback, and explicit prompt-toolkit fail-closed
  behavior. A real-TTY smoke with the optional package installed covered local
  state commands, ordinary no-tool turns, multiline entry, and
  completion-capable startup without storing raw input buffers.
- Native prompt-toolkit next-boundary decision after real-TTY hardening:
  Summary-safe archive reflection and focused prompt-toolkit inspection
  selected prompt-toolkit-only `@file` reference completion as the next small
  input-ergonomics boundary. The selected slice should complete safe
  workspace-relative file labels while editing ordinary provider prompts and
  command free-text arguments, but must remain completion-only: accepting a
  reference inserts text into the input buffer and does not read file contents,
  create provider-visible context, consume read budgets, invoke providers or
  tools, mutate workspace state, change parser behavior, or archive raw command
  text or completion buffers. Resilient resize behavior was rejected because
  the current prompt-toolkit smoke did not expose a concrete resize bug after
  CPR and key-sequence hardening. Persistent history was rejected because raw
  history storage needs a larger privacy design and must not land as an
  opportunistic editor feature. Another hardening pass was rejected because the
  known compatibility issues are already addressed. Bottom-toolbar behavior
  remains deferred because prompt labels, startup chrome, and `/status` still
  cover safe status display without adding footer ownership.
- Native prompt-toolkit `@file` reference completion boundary:
  The optional prompt-toolkit REPL completer now suggests safe
  workspace-relative `@file` labels while editing an `@`-prefixed token in
  ordinary provider prompts and the free-text side of `/ask-file ... --` and
  `/propose-file ... --`. The implementation reuses the existing conservative
  workspace path filtering, including ignored/generated and sensitive-looking
  path rejection, and preserves existing leading slash-command completion,
  explicit file-command path completion, multiline input, plain captured-stream
  fallback, and explicit prompt-toolkit fail-closed behavior. Accepting a
  completion inserts only text into the prompt-toolkit buffer; it does not read
  files, attach context, invoke providers or tools, consume read budgets,
  change parsing, alter proposal/apply/verification behavior, or archive raw
  prompts, file contents, text, or completion buffers.
- Native next-boundary decision after `@file` completion: summary-safe archive
  reflection and the current Pi-like shell gaps selected a narrow explicit
  multi-file context budget as the next implementation slice. The selected
  boundary should raise the successful explicit file-excerpt budget from one to
  two successful workspace-relative excerpts per REPL session across `/read`,
  `/ask-file`, and `/propose-file`; any further budget increase requires a
  separate slice. It should preserve explicit user-owned paths, one file per
  command, one provider turn per provider-visible command, the same
  failed/skipped recovery budget, proposal/apply exact-path constraints,
  allowlisted verification, stdout/stderr contracts, and metadata-only archive
  rules. Automatic `@file` reads, model-selected paths, broad context loading,
  provider-side tools, arbitrary shell execution, non-allowlisted verification,
  multi-file proposal/apply, raw history, persistent transcripts, and a general
  model/tool loop remain deferred.
- Native narrow explicit multi-file context budget: `/read`, `/ask-file`, and
  `/propose-file` now share a budget of two successful workspace-relative file
  excerpts per REPL session. Each command still names one explicit path, and
  `/ask-file` or `/propose-file` still forwards only one successful excerpt to
  one provider turn. The existing failed/skipped recovery budget remains
  bounded and separate. Prompt labels and startup chrome now show the safe
  remaining/limit read budget, `/status` reports safe read counters and flags,
  and session completion metadata keeps the legacy successful-read boolean
  while adding count, limit, and remaining counters. Archives remain
  metadata-only and omit raw paths, prompts, excerpts, completion buffers,
  model output, provider responses, stdout, stderr, diffs, file contents,
  command output, auth material, secrets, credentials, tokens, private keys,
  and sensitive personal data.
- Native explicit multi-file context budget review and smoke: the
  two-successful-excerpt REPL budget was reviewed for stale one-read
  assumptions across implementation, prompt/startup/status labels, session
  counters, tests, and docs. Focused captured-stream fake-provider smoke now
  covers mixed `/read` plus `/ask-file` success followed by an over-budget
  `/propose-file` that fails closed before a third read, provider visibility,
  proposal metadata, stdout leakage, or archive leakage. Existing broader
  budget tests still cover other mixed command orderings, failed/skipped
  recovery, exact-path proposal/apply constraints, `/verify just-check`, and
  metadata-only archive compatibility. The first independent review found no
  critical or warning issues and two optional suggestions; both were accepted
  and fixed by making the proposal-metadata smoke assertion non-vacuous and by
  tightening the next-slice deferred-boundary wording.
- Tool-loop parity track docs pass (slice 1 of the Tool-Loop Parity Track):
  `docs/pi-parity.md`, `docs/backlog.md`, and `docs/harness-spec.md` now record
  the goal, the twelve planned slices, the cross-track invariants
  (metadata-first archive privacy, `.git` default-deny, stdlib-only,
  `NativeToolResult` vs `ToolExecutionResult` separation, pipy-owned
  `tool_request_id` vs `provider_correlation_id`, no-tool REPL plus
  slash-command parity), and the out-of-scope items (no `bash` tool, no
  `/verify` generalization, no resume/branch/compaction, no RPC/SDK, no
  extensions/skills, no automatic `@file` content reads, no persistent
  history, no full TUI, no additional providers, no removal of the no-tool
  REPL). No runtime behavior, public CLI shape, stdout/stderr contracts,
  archive contents, dependency set, or test surface changed in this slice.
- Native pipy tool contracts (slice 2 of the Tool-Loop Parity Track): the new
  `pipy_harness.native.tools` subpackage exposes `ToolDefinition`,
  `ToolRequest`, `ToolExecutionResult`, `ToolArgumentError`, `ToolContext`,
  and the `ToolPort` Protocol, plus a `make_tool_request_id()` helper and a
  `validate_arguments()` stdlib JSON-schema-subset validator. Contract tests
  pin: model-visible tool names are alphanumeric plus underscore; schemas are
  validated at definition time and reject unsupported types or keys;
  pipy-owned `tool_request_id` carries the `pipy-tool-` prefix and is
  rejected on `ToolRequest`/`ToolExecutionResult` when a provider id is
  passed in its place; `provider_correlation_id` rides separately and is
  opaque to the contracts; `ToolExecutionResult` has no archive-safe metadata
  fields, keeping it strictly distinct from
  `pipy_harness.native.models.NativeToolResult`; `validate_arguments`
  rejects missing required keys, unsupported extras, wrong scalar types,
  `bool`-as-int/`bool`-as-string confusion, integer/string bound violations,
  enum violations, array-item violations, and non-object top-level schemas,
  and returns a defensive `dict` copy; the `_FixtureEchoTool` round-trip
  passes `isinstance(tool, ToolPort)`; the subpackage does not re-export the
  archive-safe `NativeToolResult` shape; no `pydantic`, `jsonschema`, or
  `attrs` imports are added. No production tool implementations, provider
  wiring, REPL session changes, archive events, public CLI shape changes, or
  workspace-effecting code lands in this slice; the contracts are inert until
  later slices.
- Native ProviderPort tool-call extension (slice 3 of the Tool-Loop Parity
  Track): `pipy_harness.native.provider.ProviderPort` gains a
  `supports_tool_calls` capability flag (declared as a `@property` on the
  `@runtime_checkable` Protocol); `pipy_harness.native.models` adds a
  `ProviderToolCall` value object with bounded `provider_correlation_id`,
  `tool_name`, and `arguments_json`; `ProviderResult.tool_calls: tuple[
  ProviderToolCall, ...]` defaults to `()`; and
  `pipy_harness.native.tools.messages` introduces a provider-agnostic
  `UserMessage`/`AssistantMessage`/`ToolResultMessage` envelope plus a
  `LoopMessage` tagged union. `OpenAIResponsesProvider`,
  `OpenAICodexResponsesProvider`, and `OpenRouterChatCompletionsProvider`
  all carry `supports_tool_calls: bool = False` and emit no `tool_calls`;
  `FakeNativeProvider` gains `supports_tool_calls: bool = False` plus a
  `programmable_tool_calls: tuple[tuple[ProviderToolCall, ...], ...]` script
  that is consumed in order across `complete()` calls and returns `()` when
  exhausted or when `supports_tool_calls=False`. Focused tests pin: real
  adapters all satisfy the `ProviderPort` Protocol; the new
  `ProviderResult.tool_calls` defaults to `()` on every existing producer;
  `ProviderToolCall` rejects empty fields and over-length values; the
  envelope shapes reject non-string content, non-tuple tool calls,
  non-`ProviderToolCall` entries, non-pipy-owned `tool_request_id` values,
  oversized outputs, non-bool `is_error`, and empty
  `provider_correlation_id`s; and the `LoopMessage` union accepts each
  message kind. No public CLI shape, REPL behavior, archive events, or
  workspace effect changes in this slice; tool-call capability is declared
  but inert outside `FakeNativeProvider` scripts.
- Native tool-loop REPL session skeleton (slice 4 of the Tool-Loop Parity
  Track): `pipy_harness.native.tool_loop_session` adds
  `NativeToolReplSession`, the metadata-only `NativeToolReplResult` shape,
  and a `production_tool_registry()` helper that returns `{}` until later
  slices populate it. The session refuses providers that do not advertise
  `supports_tool_calls`, refuses tool budgets outside `[1, 25]` (default
  10), reads one user turn per `input_stream.readline()`, calls
  `ProviderPort.complete()` per assistant turn, and for each
  `ProviderToolCall`: allocates a pipy-owned `tool_request_id` via
  `make_tool_request_id()`, parses `arguments_json`, runs
  `validate_arguments()` against the selected tool's
  `ToolDefinition.input_schema`, and either invokes the tool or returns a
  deterministic `ToolResultMessage(is_error=True)` observation to the
  model. Unknown tool names, JSON decode errors, schema violations, and
  `ToolArgumentError`s raised from `tool.invoke()` all flow through the
  same observation path. The session enforces a per-user-turn invocation
  budget that emits a "tool budget exhausted" observation when reached and
  a `MAX_MALFORMED_STREAK` of three consecutive malformed calls that ends
  the loop with a deterministic stderr diagnostic; one successful
  invocation resets the streak. Tests pin: an injected `_FixtureEchoTool`
  drives a successful invocation; unknown tools, malformed JSON, and
  schema violations each surface as one malformed-streak step; three
  consecutive malformed turns - whether across or within one provider
  response - end the loop with `error_type="NativeToolLoopMalformedFatal"`;
  one success resets the streak; budgets cap invocations without raising;
  empty input produces zero turns; `final_text` is printed on stdout when
  no tools are emitted; `NativeToolReplResult` carries no payload, diff,
  file-content, prompt, model-output, or provider-response fields. The
  production tool registry stays empty; no CLI mode is wired in this
  slice. The existing no-tool REPL, slash commands, archive contracts, and
  `/verify just-check` boundary are unchanged.

## Next Slice

### Add the read tool and wire the first tool-loop CLI mode (slice 5 of the Tool-Loop Parity Track)

Goal: ship the first model-driven tool (`read`), wire
`pipy repl --agent pipy-native --repl-mode tool-loop` through a new
adapter that runs `NativeToolReplSession`, and flip the first real
provider's `supports_tool_calls` to `True` once its response parser can
surface `ProviderToolCall`s. A manual smoke run lands with the slice.

Implementation focus:

- add a `pipy_harness.native.tools.read.ReadTool` that reuses
  `read_only_tool.py` workspace-relative validation, bounded byte/line
  limits, `.git` default-deny, and metadata-only archive behavior; it
  returns provider-visible content through `ToolExecutionResult`, with
  archive-safe counters in a separate `NativeToolResult` recording path
- populate `production_tool_registry()` with the `read` tool only; later
  slices add `ls`, `grep`, `find`, `write`, and `edit`
- add a `--repl-mode {no-tool, tool-loop}` CLI flag defaulting to
  `no-tool` so the existing REPL stays the default; add a
  `--tool-budget` CLI flag (default 10, capped at 25) that is honored
  only in `tool-loop`
- introduce a `PipyNativeToolReplAdapter` that mirrors
  `PipyNativeReplAdapter` but constructs a `NativeToolReplSession` with
  the production registry and the selected tool budget
- flip exactly one of `openai`/`openai-codex`/`openrouter` to
  `supports_tool_calls=True` once its response parser surfaces tool
  intents as `ProviderToolCall`s; the other two stay inert; document the
  chosen first provider and the manual smoke run in the Done entry
- ship focused tests that pin: the read tool's definition, schema, and
  workspace-relative path validation; the production registry now has
  exactly `{"read"}`; `--repl-mode tool-loop` is rejected when the
  selected provider does not support tool calls; the no-tool REPL and
  slash commands keep working in both modes; the metadata-first archive
  contracts and `.git` default-deny posture hold across the new tool

The remaining seven slices of the Tool-Loop Parity Track stay closed in
this slice. The full slice list, invariants, and deferred items are in
the `Tool-Loop Parity Track` section above.

## Near Term

The near-term product direction is still a real `pipy-native` runtime with a
Pi-like interactive shell. The shell should be a thin user interface over
pipy-owned provider, session, turn, tool, sandbox, and archive boundaries, not
a separate runtime and not a wrapper around Codex, Claude, Pi, or another
agent CLI. The product posture is now explicitly Pi-like: no permission
popups for normal interactive use.

The immediate path is now landing the Tool-Loop Parity Track, slice by
reviewed slice, after the reviewed narrow explicit multi-file context budget
and the next-boundary decision recorded above. That track follows the
styled Pi-like startup visual/resource-label pass, grouped slash-command
discovery, post-help input ergonomics decision, state-aware prompt label,
terminal-layer direction checkpoint, prompt-toolkit feasibility boundary,
leading slash-command completion, workspace-relative path completion,
multiline input, bottom-toolbar status decision, real-TTY prompt-toolkit
hardening, next-boundary decision, completion-only `@file` references, the
post-`@file` next-boundary decision, the budget implementation, and the
budget review/smoke slice. Those implemented boundaries prove the current
shell can preserve plain captured-stream behavior while isolating optional
prompt-toolkit input behind a small adapter. The bottom-toolbar decision
deferred footer behavior because the
prompt label already carries the safe input-time status labels, and the
hardening pass resolved the adapter compatibility work found by real-TTY smoke
testing before adding another terminal display surface.

Manual `pipy run --agent pipy-native` smoke tests are useful product checks,
but today they exercise a one-shot runner: `--goal` is the input, provider final
text is stdout, finalization is stderr, and the process exits. The persistent
shell is available through bare `pipy` or `pipy repl --agent pipy-native`; it
now has local `/help`, `/clear`, `/status`, `/login`, `/logout`, and `/model`
commands, a display-only `/read <workspace-relative-path>` command, and one
provider-visible `/ask-file <workspace-relative-path> -- <question>` command
with a whitespace-delimited `--` separator sharing the same
two-successful-excerpt budget.
Explicit read/context commands do not display approval popups. Auth/model
commands and malformed or unsupported slash commands print stderr diagnostics
without provider/tool execution, explicit-read budget consumption, or raw command
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
boundaries. Startup chrome, grouped command discovery, the state-aware prompt
label, the terminal-layer direction checkpoint, and the prompt-toolkit
input-adapter boundary are the first visible parity steps: they now provide a
styled, sectioned, resource-label frame, a grouped command reference, compact
current-state input labels, and an optional prompt-toolkit line-editor path
with leading slash-command name completion and workspace-relative file/path
completion for explicit file commands, completion-only `@file` references, and
prompt-toolkit-only multiline input with hardened cursor-position and
newline-key handling while still keeping the shipping runtime metadata-only and
captured-stream compatible. The next visible parity step is the Tool-Loop
Parity Track recorded above and in `docs/pi-parity.md` and
`docs/harness-spec.md`: a bounded model-selected `read`/`write`/`edit`/`ls`/
`grep`/`find` loop behind `pipy repl --agent pipy-native --repl-mode tool-loop`,
landed as twelve reviewed slices alongside the existing no-tool REPL and
slash-command boundaries.

Small reviewable slices, in intended order:

1. Add the read tool and wire the first tool-loop CLI mode (slice 5 of the
   Tool-Loop Parity Track).

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
- Pi-like startup chrome gate: available now. Bare `pipy` and
  `pipy repl --agent pipy-native` print compact startup chrome on stderr before
  the first prompt, with version, controls, static resource labels, and safe
  status labels shared with `/status`.
- Pi-like visual/resource-label decision gate: available now. This selected the
  implemented startup presentation improvement: styling, section hierarchy,
  wrapping, footer-style labels, and metadata-only resource labels while
  staying line-oriented and privacy-safe, without adding a full TUI, new
  keybinding runtime, new provider/tool capability, broad context loading, raw
  transcript display, or archive content beyond existing metadata.
- Pi-like startup visual/resource-label gate: available now. Startup chrome is
  sectioned and wrapped, uses TTY-only ANSI styling with captured-stream
  fallback, shows compact footer-style workspace/model/turn labels, and
  displays only safe existence-level resource source labels.
- Input-ergonomics decision gate: available now. The selected next
  line-oriented boundary is grouped slash-command discovery in `/help` and
  static supported-command diagnostics; multiline editing, autocomplete, file
  references, terminal resize behavior, a keybinding framework, and a TUI
  framework remain deferred.
- Grouped slash-command discovery gate: available now. Existing command help
  and usage diagnostics are organized into stable concern-based groups without
  adding commands, changing parser behavior, invoking providers/tools,
  consuming budgets, archiving raw command text, or changing stdout/stderr
  contracts.
- Post-help input ergonomics decision gate: available now. The selected next
  boundary is another line-oriented slice: a compact state-aware prompt label
  before each input. A named Textual, prompt-toolkit, curses, or custom
  terminal-layer investigation remains deferred until a later ergonomics gate.
- Line-oriented state-aware prompt label gate: available now. The REPL prompt
  uses existing safe display-state labels from startup chrome and `/status` to
  render provider/model, turn, read, proposal, and verification availability
  before each input, without changing command parsing, stdout/stderr contracts,
  provider visibility, archive privacy, or execution capability.
- Terminal-layer direction checkpoint gate: available now. The selected next
  input-runtime investigation is a narrow `prompt-toolkit` line-editor adapter;
  Textual, curses, and a custom terminal layer remain deferred, and the current
  plain line-oriented runtime remains the required fallback.
- Prompt-toolkit line-editor feasibility gate: available now. The REPL input
  path is behind a small adapter boundary; `auto` keeps plain captured-stream
  fallback and can use optional prompt-toolkit only on real TTY streams, while
  `--input-runtime plain|prompt-toolkit|auto` enables explicit smoke testing.
- Prompt-toolkit slash-command completion gate: available now. The optional
  prompt-toolkit input path suggests existing slash command names while editing
  a leading slash-prefixed first token. Prompt-toolkit remains optional rather
  than a declared runtime dependency, and the plain input path plus captured
  streams remain unchanged.
- Prompt-toolkit file/path completion gate: available now. The optional
  prompt-toolkit input path suggests workspace-relative path labels for
  explicit file commands while preserving stdout/stderr, prompt-label, parser,
  provider-turn, budget, proposal/apply/verification, and metadata-only archive
  contracts.
- Prompt-toolkit multiline input gate: available now. The optional
  prompt-toolkit input path enables multiline buffers on real TTY streams while
  keeping Enter as submit and Esc+Enter as newline insertion; plain captured
  streams remain one-line `readline()` input.
- Prompt-toolkit bottom-toolbar status decision gate: available now. Footer
  behavior is deferred because startup chrome, `/status`, and the state-aware
  prompt label already expose the same safe state labels; the follow-up
  real-TTY prompt-toolkit hardening pass is also available now.
- Prompt-toolkit real-TTY input hardening gate: available now. The optional
  prompt-toolkit adapter disables cursor-position requests to avoid warning
  noise in PTY-like terminals and handles both CR and LF encodings for
  Enter-submit and Esc+Enter newline insertion, while preserving optional
  dependency posture, plain fallback, explicit prompt-toolkit fail-closed
  behavior, command parsing, stdout/stderr contracts, and metadata-only
  archives.
- Prompt-toolkit next-boundary decision gate: available now. Summary-safe
  session lessons and the hardened adapter selected prompt-toolkit-only
  `@file` reference completion as the next input slice, while keeping
  bottom-toolbar behavior, resilient resize work, persistent history, automatic
  file-content reads, and broader TUI behavior deferred.
- Prompt-toolkit `@file` reference completion gate: available now. The optional
  prompt-toolkit completer suggests safe workspace-relative `@file` labels in
  ordinary provider prompts and supported command free-text while preserving
  completion-only behavior, plain fallback, explicit prompt-toolkit
  fail-closed behavior, command parsing, read budgets, provider/tool behavior,
  and metadata-only archives.
- Next-boundary decision gate after `@file` completion: available now. The
  selected next implementation is a narrow explicit multi-file context budget:
  two successful user-named file excerpts per REPL session across `/read`,
  `/ask-file`, and `/propose-file`, with automatic `@file` reads, broad context
  loading, model-selected paths, provider-side tools, multi-file proposal/apply,
  arbitrary shell execution, raw history, persistent transcripts, and a general
  model/tool loop still deferred.
- Narrow explicit multi-file context budget gate: available now. `/read`,
  `/ask-file`, and `/propose-file` share two successful user-named file
  excerpts per REPL session, still one explicit file per command and at most
  one provider-visible excerpt per provider turn, with safe prompt/startup
  labels, `/status` counters, metadata-only session counters, and the existing
  failed/skipped recovery budget preserved.
- Historical visible approval prompt gate: available as test-covered helper
  code, but removed from the normal product REPL path.
- Narrow read-only shell command gate: available now through `/read
  <workspace-relative-path>`, currently bounded by the two-successful-excerpt
  REPL budget without approval popups.
- Provider-visible interactive context gate: available now through `/ask-file
  <workspace-relative-path> -- <question>` with a whitespace-delimited `--`
  separator, bounded by the shared explicit-file-excerpt budget with
  `/read`, one in-memory provider handoff, and metadata-only archive handling.
- Command help and usage-diagnostic gate: available now through local `/help`
  and static stderr usage diagnostics for malformed or unsupported slash
  commands. These paths do not invoke providers, execute tools, consume
  explicit-read budgets, emit tool events, or archive raw command text, paths,
  questions, or excerpt data.
- Auth/model command gate: available now through local `/login`, `/logout`, and
  `/model` commands. These reuse the pipy-owned OpenAI Codex auth boundary,
  late-bind provider construction before each provider-visible turn, persist
  non-secret provider/model defaults, and keep auth/model status on stderr
  without consuming provider turns or explicit-read budgets.
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
  approved tool boundaries. The bounded Pi-shaped slice of this work is now
  planned as the `Tool-Loop Parity Track` above; broader model/tool-loop
  capabilities outside that track stay deferred.
- A model-driven `bash` tool or any arbitrary-shell tool. The
  `Tool-Loop Parity Track` deliberately excludes this.
- Generalizing `/verify` beyond the allowlisted `just check` boundary. The
  `Tool-Loop Parity Track` keeps `/verify just-check` intact and does not
  introduce additional verification commands.
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
- Full interactive TUI beyond the selected narrow `prompt-toolkit` input-adapter
  investigation, including Textual, curses, a custom terminal layer,
  alternate-screen behavior, overlays, selectors, and persistent footer
  ownership.
- RPC mode.
- Multi-agent task delegation.
- Long-running dev server.

## Explicitly Not Now

- Making Codex, Claude, or another coding-agent CLI wrapper the main product
  path.
- Storing full system prompts, user prompts, model outputs, stdout, stderr,
  tool payloads, secrets, tokens, credentials, private keys, or sensitive
  personal data by default.
- Building broad approvals, sandboxing, retries, streaming, additional OAuth
  providers, provider registry, raw transcript import, multiple native tool
  requests, post-tool provider turns, general write tools beyond supervised
  patch apply, non-allowlisted verification commands, Textual or another
  full-screen TUI framework, RPC, compaction, branching, or orchestration in the
  upcoming slices; real execution work must wait for its named slice.
- Using unsupported subscription auth, scraping browser or CLI session stores,
  or treating another product's login/session as pipy-native provider
  credentials.

## Maintenance Notes

- Move completed slices from `Next Slice` or `Near Term` to `Done` in the same
  change that implements them.
- Keep deferred items here brief; put detailed design and rationale in
  `docs/harness-spec.md`.
- Keep archive and privacy rules aligned with `docs/session-storage.md`.

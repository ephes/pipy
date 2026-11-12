# Durable execution: canonical-loop integration design

Status: design, 2026-11-11. Concept work only; no production change is made by
this document. It answers one question left open since the
[canonical-loop integration spike](durable-loop-integration.md) of 2026-10-19:
how durable recovery becomes the behaviour of the product's own agent loop
(TUI, JSON, RPC and the synchronous SDK), with Pi parity, instead of staying a
separate opt-in runtime. It also designs the two named gaps that the
[plan](specs/2026-10-19-durable-execution-plan.md) carved out of its parity
rows and that the ledger tracks as `pipy-durable-p15-tool-text-signatures`
and `pipy-durable-p16-omitted-tool-output`.

The reference is literal Pi durable release 1.5.0 at
`f9a26365568127461c47a07a37dcb42c140a3ac5` (`packages/durable` in pi-mono),
as the [1.5 bridge](specs/2026-11-03-durable-release-1.5-bridge.md) fixed it.
Paths under `native/` mean `src/pipy_harness/native/`; line numbers refer to
main `67654e45`.

## 1. Goal and non-goals

Goal: one execution owner per session. In a durable session the durable
scheduler drives generation and tool phases, the product consumes committed
state, and the visible behaviour of the TUI, JSON, RPC and SDK surfaces stays
what the existing Pi parity gates accept. The canonical turn semantics that
`AgentLoop` encodes today are shared with the durable tasks as code, not
re-implemented. P15 (tool-result text signatures) and P16 (omitted final tool
output, retained-output API) close in both hosts, ordinary and durable, with
the same observable results.

Non-goals:

- This document does not re-plan DE17–DE45. It names the seams those slices
  attach to and orders the first six Opus-sized slices; the plan's rows,
  acceptance cells and dependency order stay authoritative.
- No default switch. Durable execution stays opt-in until DE44's gate evidence
  exists; the plan (§5, Milestone D) keeps that one small final change.
- No second persistence authority. The JSONL session tree becomes a derived
  export in the durable route (DE41); this design does not keep it writable
  beside SQLite.
- No change to the P01–P43 status. Every row stays OPEN until its owning slice
  appends comparison evidence.

## 2. Current state

### 2.1 Two drivers, shared values, unshared round policy

The product runs the synchronous `AgentLoop`
(`native/agent/loop.py:417`): `run()` at line 498 seeds `_RunState`, appends
the accepted input and iterates `_run_iteration` (line 565) up to the tool
budget plus two. Each iteration prepares a request through the
`AgentLoopRequestSource` port (line 271), completes it through
`AgentLoopProviderTurn` (line 285), settles the provider result
(`_settle_provider_result`, line 777), runs the tool cycle
(`_run_tool_cycle`, line 902; `_execute_admitted_tool`, line 1069) and applies
the policy's result transform (`_transform_tool_result`, line 1799). The loop
emits the `AgentEvent` vocabulary of `native/agent/events.py` through the
`AgentEventSink` port (`native/agent/ports.py:11`). The product composes it in
`CodingAgentRunCoordinator.run_turn` (`native/coding/agent_run.py:273`, loop constructed at line 282) with
the request-source, provider-turn and status adapters of the same module,
begins and ends an agent run on `CodingSessionState`, and mirrors the final
history back (`mirror_history`, line 314). Persistence is the JSONL session
tree written state-first by the coding product session.

The durable runtime is a second driver: `TaskScheduler`
(`native/durable/scheduler.py:572`) reserves and invokes task phases; the
`pi.generation` task (`native/durable/generation.py:337`, `_Generation` with
`prepare`, `request`, `settle` and `tools`) and the `pi.tool` task
(`native/durable/tool_execution.py`) own progress as committed checkpoints on
the Session line. The product reaches it only through
`DurableProductSession` (`src/pipy_harness/durable_product_api.py:183`;
`submit` 322, `inspect` 375, `observe_completed` 414, `resume` 614, `close`
915) and the `pipy durable` command family
(`src/pipy_harness/durable_cli.py:250`). The architecture guide records the
boundary: the package "remains outside the default AgentLoop/CLI/TUI
execution path; later durable adoption owns that"
(`docs/architecture.md:1079`).

Values are shared; the round policy is not. The durable package imports about
forty value types from `native/agent/` (request snapshots, messages,
`ToolExecutor`, usage pricing, content blocks), but from
`native/agent/loop_policy.py` it takes only `AgentProviderStatusAction` and
`normalize_provider_status` (`generation.py:30–33`). Nothing under
`native/durable/` calls `decide_tool_admission`, `settle_tool_execution`,
`apply_tool_policy_decision` or an `AgentToolPolicy.transform_result`.
`_Generation.settle` admits every offered call as a `pi.tool` task and writes a
`tool_unavailable` result for a call the request did not offer
(`generation.py:623–631`); it has no tool budget, no unauthorized-tool
admission and no result transform. So two things are duplicated or missing,
not one: the driver (how a provider result becomes an assistant entry plus
children, how a tool outcome becomes a result entry, how a round closes), and
the product's tool policy, which exists only on the `AgentLoop` side today.
Section 4.2 treats both explicitly; adopting the policy in the durable path is
a behaviour change there and is gated as one.

### 2.2 What the spike proved and what the plan decided

The spike (`docs/durable-loop-integration.md`, "Actual integration boundary")
ran the unchanged `AgentLoop.run()` from its accepted input with I/O receipts
around the provider-turn and tool-execution ports. It passed 46 cases and
showed that canonical history, budgets and usage reconstruct by rerunning the
loop, for pure and pinned callbacks. It also showed the limits: callbacks,
request preparation and hooks rerun on every resume; the transcript serializer
omits structured content and termination; replay cost grows with the
transcript. The plan then decided (§3.1) that "the durable scheduler owns
progress", that "replaying the whole synchronous loop remains a useful
reference fixture, not the mandated final scheduler", and that no hook, queue,
compaction or child lifetime "must be smuggled through a receipt adapter that
reruns untracked external effects". This design follows that decision.

### 2.3 Delivered and open

Delivered on main (evidence ledger "Slice status"): DE00–DE12, DE13a/b/c1/c2,
DE14a and DE14b1–b7, DE15a–c, DE16a–c, DE32a, and bridge slices R15-0 to
R15-2. R15-3 (batch `Storage.commit`) is an unreviewed WIP on branch
`wip/durable-r15-3-20261104` (commit `1821eac4`, 34 files; its resume note
says no gate ran). R15-4 and DE17–DE45 are open. All 43 parity rows are OPEN.

### 2.4 The P15 gap: tool-result text signatures

Pi's `ToolResultMessage.content` is `(TextContent | ImageContent)[]`
(pi-ai `types.ts:645`), and `TextContent` carries an optional
`textSignature` (`types.ts:422–426`), the OpenAI Responses message id or a
Gemini thought signature. Pi preserves that field wherever it copies tool
content: the durable harness bounds a result with `boundContent`
(`harness/tool.ts:700`), which spreads each item, so a truncated signed text
block keeps its signature. The ledger's source finding of 2026-10-27 recorded
the same for the earlier pin; the oracle in section 6 re-verifies it on 1.5.0.
Pi's provider adapters read the signature back only on assistant messages;
tool-result text is sent as plain text.

pipy refuses the field on tool results. `normalized_tool_result_blocks`
(defined at `native/agent/content.py:184`; the refusal is line 205) raises for
a signed `TextContent`, and `AgentToolResultMessage.__post_init__`
(`native/agent/messages.py:324`, call at line 352) runs it on every
construction, including canonical reads. `require_tool_result_blocks`
(`content.py:217–222`) and the docstrings at `content.py:189–192` and
`messages.py:293–297` state the image-only rule the refusal implements. The
durable projection `_projected_blocks`
(`native/durable/tool_output_values.py:106`) rebuilds a truncated block as
`TextContent(text)`, which would drop a signature even if ingress allowed it.
The ordinary host's JSONL codec drops it too: `tool_result_blocks_to_json`
writes `{"type": "text", "text": ...}` without a signature and
`tool_result_blocks_from_json` rebuilds unsigned `TextContent`
(`native/assistant_blocks_json.py:110–136`), used by the session tree at
`native/session_tree.py:370` and `:493`. The canonical codec already has room
for the field: its schema declares `TextContent: {"text": S, "signature": (S,
N)}` and `AgentToolResultMessage.blocks: [(TextContent, ImageContent)]`
(`native/durable/canonical_codec.py:82` and `:123`); that `blocks` schema has
been in place since codec version 2 and the current version is 3
(`canonical_codec.py:257`), so no new codec version is needed. Two tests
assert today's refusal (`tests/durable/test_tool_reporting_projection.py:73`
and `:110`). The Responses wire parses a text signature only for assistant
replay (`native/providers/openai_responses_wire.py:209`), which matches Pi.

### 2.5 The P16 gap: omitted final output and the retained-output API

Pi's durable `ToolExecutionResult` (`harness/types.ts:197–213`) makes
`output` optional: "Omitted: the retained `output()` text"; `details` omitted
means the last `details()` value; `diagnostics` are appended after those
recorded through `api.diagnostic()`. The tool API (`types.ts:266–281`) offers
`output(chunk, skipped?)`, `outputWindow`, `retainedOutput()` returning
`{text, truncated}`, `diagnostic()` and `details()`. `finalResult`
(`harness/tool.ts:684–701`) applies the fallbacks, adds the `truncated`
diagnostic ("Output truncated to its beginning|end: N lines, M bytes
dropped", `tool.ts:1031`) only while the result's output is the retained one,
then bounds the content with the tool's limits (defaults 50 KiB and 2000
lines, `retain: "head"` unless the tool declares `tail`, which Pi's bash tool
does at `tools/bash.ts:161` and `:185`). The test "uses retained output and
the last details when the result omits them, with diagnostics in order"
(`test/harness-tools.test.ts:349`) is the reference behaviour.

pipy's `ToolExecutionResult` (`native/tools/base.py:511`) requires
`output_text` (line 520). `ToolContext` (`base.py:702`) has a push-only
`output_sink` and no retained-output read. The durable `DurableToolContext`
(`native/durable/tool_execution.py:86`) adds `report_details` and
`report_diagnostic`, backed by `RetainedToolProgress`
(`native/durable/tool_progress.py:59`), and the durable transport already
applies the details fallback (`tool_execution.py:302–304`). The durable
window keeps the **last** 50 KiB or 2,000 lines, not the head: the reporter
appends to a `Tail` (`native/durable/tool_progress_values.py:393`) and bounds
it with `truncate_tail` (line 432; `docs/durable-tools.md:254`, "Retained
output: Last 50 KiB UTF-8 / 2,000 lines"). The ordinary executor runs tools
through `_InvocationOutputGate(context.output_sink)` (`native/agent/tools.py:371`);
its `retain` flag (line 197) changes only sink exposure and drain-on-deactivate
and keeps no text, because the window lives in the durable
`RetainedToolProgress` subclass. `ToolExecutor.execute` also refuses a
retaining gate together with `wait_for_interrupt` ("Retained output requires
the physically owned executor", `tools.py:367–370`), and the product's path is
the interruptible one (`loop.py:1105`). Pipy's bounds are the same numbers as
Pi's (`native/tools/truncate.py:21–22`, `tool_progress_values.py:28–29`), but
the retained end differs from Pi's head default. The DE13b comparison records
the consequence: "Pi's normal fixture omits final output; the native normal
fixture explicitly returns the reported text because `ToolExecutionResult`
currently requires it" (`docs/durable-tools.md`, DE13b section).

### 2.6 Gates that shape the slices

- Test tiers (`CONTRIBUTING.md:91`): `just test` selects by change,
  `just test-durable` about 350 s, `just test-core` about 120 s,
  `just test-full` about 450–500 s on the 16-CPU host (measured 2026-11-10).
  The routing plugin escalates a change to a hash-pinned file to the durable
  tier.
- Hash-pinned closures: every comparison manifest under
  `tests/fixtures/durable/` records a `closure` of SHA-256 digests over about
  570 repository files, and each comparison module's `configuration()`
  requires `sha(ROOT / name) == digest` for every one of them
  (`scripts/durable_reporting_comparison.py:105`, "source drift").
  Manifests also pin their parent manifest's digest, so a changed file
  cascades: `CHANGELOG.md`, `docs/backlog.md`, `docs/architecture.md`,
  `zensical.toml` and every module named in this design are pinned by 13–19
  manifests each. Recent delivery commits re-pinned 19 manifests apiece
  (for example `f267c4cf`). A slice that edits a pinned file carries that
  re-pin in the same commit, and the `durable-reference` CI job
  (`.github/workflows/ci.yml:197`) reruns the fresh chain against pinned
  pi-mono source with Node v26.13.0.
- Review: each slice gets an independent Claude Code Opus 5.5 HIGH review
  through `claude-review-loop`, then `just check`, `just test-durable`, and
  `just test-full` before a durable delivery, plus `just docs-build`.

## 3. Options considered

### 3.1 Integration shape

**A. Receipt replay around the `AgentLoop` ports (the spike).** Keep
`AgentLoop` as the driver and resume by rerunning it from the accepted input
with stored provider and tool receipts. Rejected as the product path, for the
plan's reasons (§3.1): every callback, hook and request preparation reruns on
resume; extension hooks, queued inputs, compaction and child lifetimes would
pass through receipts that cannot make their effects replay-safe; replay cost
grows with history; and the product would hold two authorities (replayed RAM
state and the store). It stays what the plan says it is: a reference fixture
(`tests/test_durable_loop_integration.py`) that proves canonical history and
usage reconstruct.

**B. Durable scheduler as the sole execution owner; the loop's turn
semantics shared as code; the product consumes committed state.** Chosen.
It is the plan's architecture, it is Pi's shape (the durable harness has no
separate agent loop; `pi.generation` and `pi.tool` are the loop), and the
delivered DE07/DE08/DE09/DE11 slices already provide the owner, the tasks and
a committed publication the product can consume. What is missing is the
adapter from committed publications to the product's event vocabulary, the
route from the product's turn into the durable session, and the extraction of
the remaining turn semantics that `generation.py` re-implements.

**C. Merge the drivers: `AgentLoop` becomes a synchronous host that runs the
scheduler over Memory storage.** Deferred, not rejected. It would remove the
second driver entirely, but it needs committed streaming (DE26) before the
live event stream the TUI renders can come from committed state, and it adds
a Memory-backed lifecycle (owner, close, join) to every non-durable run and
test. Option B's shared turn kernel (section 4.2) is the precondition for C,
so choosing B now loses nothing; DE44 evaluates C with measured cost.

### 3.2 P16 shape

1. **A sentinel for omitted output on `ToolExecutionResult`, with the
   retained-output fallback in both hosts.** Chosen. One tool API for both
   executors, as the ledger asks ("ordinary/durable behavior, explicit
   presence"); Pi's semantics; additive, because omitting output is a type
   error today, so no existing tool changes behaviour.
2. Durable-only fallback (ordinary executor keeps requiring text). Rejected:
   a tool would behave differently by host, and the comparison would still
   pair different producer inputs.
3. `output_text: str | None` with `None` meaning omitted. Rejected: `None`
   already means null in the neighbouring `details` and `structured_content`
   fields, and a positional required argument becoming optional-by-None
   hides omission; a named sentinel keeps omission explicit at the call site.

### 3.3 P15 shape

1. **Widen the tool-result block domain to signed text; keep blocks when
   they carry information beyond the text.** Chosen. Pi's data shape; the
   codec already carries it; the refusal flips to acceptance with the
   invariants below.
2. Strip signatures at ingress. Rejected: Pi preserves them through storage
   and truncation, and a stored result would differ from Pi's.
3. A side table for signatures next to the result. Rejected: not Pi's shape,
   a second place to keep in step with `blocks`, and the codec would need a
   new version for no gain.

## 4. Chosen design

### 4.1 Ownership

A durable session has one execution owner: the `TaskScheduler` under the
SQLite `PathOwner` and Session line, exactly as DE05–DE12 built it. The
product never runs `AgentLoop` for such a session. `AgentLoop` keeps serving
the legacy JSONL path unchanged until DE44 and stays the reference fixture of
the spike. There is no third driver and no receipt adapter in the product.

Invariants carried over unchanged from the plan (§3.2–3.4): durable intent
commits before any external effect; no provider, tool, process or human
effect runs on the mutation line; publication comes only from committed
state; unsafe interrupted model-issued calls end as `interrupted` results and
the run continues (DE13); restart limits end built-in tasks visibly (R15-2);
configuration drift fails closed before new external work; no exactly-once
claim for external acknowledgements.

### 4.2 The shared turn kernel

Extract the turn semantics that both drivers need into pure functions in
`native/agent/turn.py` (new) next to `loop_policy.py`. `AgentLoop` adopts
them first without behaviour change; the durable tasks adopt them in
explicitly gated steps, because for the durable path they add behaviour it
does not have today (section 2.1):

| Kernel function | Today in `loop.py` | Durable today | Durable adoption |
| --- | --- | --- | --- |
| `settle_provider_result(state, snapshot, result) -> (assistant, admissions, status)`, admissions through the existing `decide_tool_admission` | `_settle_provider_result` 777, `_publish_usage` 854 | `_Generation.settle` (generation.py 473) builds the assistant entry itself and admits every offered call; unoffered calls get `tool_unavailable` results (623–631); no budget | First DE25 sub-step: budget-aware admission in `_tool_round`, a precondition of the run route (section 4.4); until then S5 pins the gap with an equivalence fixture (budget-free, all calls offered) |
| `transform_tool_result(policy, call, result, arguments, parent)` | `_transform_tool_result` 1799 | none: no `AgentToolPolicy` in the durable path | With DE22's hook composition (`afterTool`), where the transform belongs in Pi |
| `skipped_tool_results(state, calls)` | `_append_skipped_tools` 1547 | round close writes no skipped results | DE25 |
| `round_disposition(results) -> continue \| stop` | terminate/handoff decision in `_run_tool_cycle` 902 | `_Generation.tools` (generation.py 527) decides its own continuation | DE25 |

Rules: no I/O, no events, no Session access inside the kernel; inputs and
outputs are the existing canonical values; `AgentLoop` keeps emitting its
events around the calls. Each extraction carries a kernel-equivalence test:
the same fixture (provider result, tool outcomes, policy state) through the
kernel and through the current durable task yields equal canonical messages
and usage where the durable path has the behaviour, and the fixture names the
cells where it does not yet (budget exhaustion, unauthorized tool, policy
transform). Those tests are the parity guard for the transition period in
which both drivers exist, and the checklist for the DE22/DE25 adoption.

### 4.3 The committed event projection

Add `CommittedEventProjection` in `native/durable/` that turns the
`CompletedWatch` publications of DE09 (`observe_completed()`) into the
existing `AgentEvent` vocabulary and delivers them to an `AgentEventSink`, so
`CodingSessionController`, the reducer, the TUI projection and the RPC/JSON
serializers stay unchanged. Mapping, before DE26:

| Committed fact | Events emitted |
| --- | --- |
| run admitted (`submit`) | `AgentRunStarted`, consumed queued input, `TurnStarted(0)` |
| generation `settle` committed | `MessageStarted(turn)` immediately followed by the completed assistant message event; the tool policy state committed with the round (section 4.4) |
| `pi.tool` receipt committed | `ToolCallStarted(turn, call)` then `ToolCallCompleted(turn, result, duration, execution_seconds)` in commit order |
| round closed without calls, or `terminate` | `AgentRunCompleted(result)`, `AgentRunSettled(result)` |
| run failed, interrupted at the restart limit, or blocked | the existing failure/cancellation events with the durable cause as the reason |

After DE26, committed partials and tool progress map to the partial-content
and `tool_execution_update` events from the same projection; no second path
is added for streaming. Ordering invariant: events follow commit sequence;
every event carries the commit sequence and entity key, and the projection is
idempotent on reconnect (a redelivered publication emits nothing). The
projection reads only committed publications and never the scheduler's
in-memory state. Until DE26 the opt-in route therefore shows completed
messages only, which the user-facing docs state plainly; it is a reason not
to make the route the default, not a reason to stream from uncommitted state.

### 4.4 The run route

Add `DurableAgentRunCoordinator.run_turn` in `native/coding/` beside
`CodingAgentRunCoordinator.run_turn` (`agent_run.py:273`), with the same
signature and the same `AgentLoopOutcome` result, so `CodingSessionController`
calls either without knowing which. It:

1. admits the accepted input through the durable session (`submit` today;
   after DE18 a submission with the product's request id, so a retried admit
   deduplicates);
2. resumes the scheduler and drives the projection until the run settles,
   with the product's cancel path mapped to the durable abort mark (DE16);
3. builds the outcome from committed state: final history from committed
   model entries, provider status from the settled generation, `next_input`
   from the durable inbox after DE19 and `None` before it, and the product's
   `AgentToolPolicyState` read from the committed round.

The last point has a precondition. `_tool_round` (`generation.py:610–632`)
creates a `pi.tool` task for every authorized call in the transaction that
commits the assistant entry, so a route that only sees committed facts learns
about the calls after they are admitted; it cannot enforce a per-turn budget
from outside, and deriving a policy state by re-admitting calls that already
ran would contradict the transcript. The route is therefore not built until
the durable round takes the remaining budget as an admission input: the
generation's admission runs `decide_tool_admission` with the submission's
`AgentToolPolicyState`, writes the loop's `budget_exhausted` error result for
a call over the budget (as `_settle_tool_call` does at `loop.py:1048`) in the
same transaction, and commits the resulting policy state with the round. That
is the first DE25 sub-step and the route reads the committed state back
instead of recomputing it. It is not a small edit, because no channel for the
policy state exists today. It touches at least: the admission input, which
is the DE18 submission (today `DurableProductSession.submit` rejects extra
options through `_options(unsupported)`, `durable_product_api.py:325`, and
its admission seed carries only the provider snapshot,
`product_binding.py:136`); the generation's `ROUND_DOC`, which today holds
`assistant`, `tools` and `slots` (`generation.py:109`, `:634`) and gains the
committed policy state under a new document version with a migration (P04,
P05); and the comparison cell that pins budget exhaustion against the loop's
error result. The canonical message codec (version 3) is untouched, because
the policy state is round-document state, not a message field. So the run
route waits on DE18's submission carrying the policy state and on this
first DE25 sub-step; until both land, durable runs through
`DurableProductSession` keep today's budget-free admission, documented as
such.

`mirror_history` becomes a read of committed entries; the controller's
in-memory mirror is a cache of the store, never an authority. The route is
selected by an explicit opt-in (a store path on the command line or in
settings) until DE44; while the Milestone A allowlist stands, the route
refuses compaction, fork/tree operations, queued steering, extension hooks
and RPC streaming controls before admission, exactly as `DurableProductSession`
does, so a user who opts in sees an actionable refusal rather than a silent
fallback to the legacy loop.

### 4.5 Persistence authority

In the durable route SQLite is the only writer. The JSONL tree is a derived,
regenerable export (DE41 owns the migration and export). The metadata-only
workflow archive stays a separate optional record and cannot drive recovery.

### 4.6 P16: omitted output and the retained-output API

Data model (`native/tools/base.py`):

- `ToolExecutionResult.output_text: str | RetainedOutput` where
  `RETAINED_OUTPUT` is a module-level sentinel (Pi: `output` omitted). An
  explicit empty string stays empty content (Pi: `output: []`).
- `ToolContext.retained_output() -> RetainedOutputSnapshot(text, truncated)`
  (Pi `api.retainedOutput()`). Without an output gate it returns the empty
  snapshot.
- `report_details` and `report_diagnostic` move from `DurableToolContext` up
  to `ToolContext` as the ordinary API; the durable context keeps the
  committed-prefix acknowledgement semantics behind them.

Executor behaviour, identical in both hosts:

1. Both executors retain a bounded window of the live output. The window is
   the durable host's existing one: the last 50 KiB or 2,000 lines
   (`Tail` and `truncate_tail`, section 2.5). The pure `Tail` moves from
   `native/durable/tool_progress_values.py` to `native/tools/truncate.py`
   so the ordinary host can use it, and `native/agent/tools.py` gains a
   non-committing `RetainedOutputWindow` gate (an `_InvocationOutputGate`
   that owns a `Tail`); `RetainedToolProgress` keeps committing progress on
   top of the same window as today. The "physically owned executor"
   restriction (`tools.py:367–370`) exists because the committing reporter
   must own the executor's lifetime; the non-committing window has no such
   need, so the restriction stays for `RetainedToolProgress` and is lifted
   for the window, and the interruptible product path (`loop.py:1105`)
   retains like the durable one. Until DE33 introduces per-tool
   `outputLimits` with Pi's `head` default and bash's `tail`, both hosts
   keep the last window; that is one shared behaviour and one recorded
   difference from Pi's default, not two host behaviours.
2. Final result: output omitted means the retained text (empty text means
   empty content); details omitted means the last reported details (already
   implemented in the durable transport, now shared); diagnostics are the
   reported ones followed by the result's own.
3. The `truncated` diagnostic (`code: "truncated"`, severity `warn`, Pi's
   message text with "to its end" while the window is a tail) is added only
   when the final output is the retained one and the window dropped bytes,
   before the bound of explicit text. An explicit output that exceeds the
   bound gets the same diagnostic from the bound.
4. `AgentToolResultMessage.CONTENT_MAX_LENGTH` (64 KiB) is above the 50 KiB
   bound plus the diagnostics block; the slice asserts that with the largest
   retained window.

The DE13b four-case comparison then pairs Pi's normal omitted-output case with
a native omitted-output case instead of recording a producer-input
difference; `pipy-durable-p16-omitted-tool-output` closes when that pairing
and the ordinary-host matrix pass.

### 4.7 P15: signed tool-result text

- `normalized_tool_result_blocks` accepts `TextContent` with a signature.
  Blocks are kept when they carry information beyond the joined text: at
  least one image or at least one signature. `require_tool_result_blocks`
  and the two docstrings state the same rule. A text-only, unsigned result
  still stores no blocks, so existing stored results compare equal.
- The JSONL session tree carries the signature as Pi's session file does:
  `tool_result_blocks_to_json` writes `textSignature` when present and
  `tool_result_blocks_from_json` reads it back
  (`native/assistant_blocks_json.py:110–136`). Without this the ordinary
  host would save a signed result and reload it unsigned, and history
  equality would differ across a save and reload.
- `tool_result_text` is unchanged; readers that show or measure text see no
  difference.
- `_projected_blocks` carries the original block's signature onto a
  truncated slice (`replace(block, text=text)` instead of
  `TextContent(text)`), the way Pi's `boundContent` spreads the item. A
  signature is never attached to the harness suffix block.
- Provider wire adapters keep ignoring tool-result text signatures
  (`chat_completions_wire`, `anthropic_messages_wire`,
  `google_generate_content_wire`, `openai_responses_wire`), as Pi's do.
  Replay projection for a changed model strips assistant signatures as today
  and leaves tool-result signatures in place, because they are never sent.
- History equality includes the signature, as Pi's structural equality does;
  the canonical codec stores it in the existing `blocks` schema (unchanged
  since version 2; the round trip is asserted at the current version 3). A
  signed result must be ingested and read back byte-identical through the
  canonical codec, the checkpoint and completed-projection reads, and the
  JSONL tree.

`pipy-durable-p15-tool-text-signatures` closes when the oracle of section 6
pairs a signed text result through truncation and the next request on both
sides. This is the tool-result metadata part of plan row P15; the row itself
stays with DE06–08, DE24 and DE33.

## 5. Risks and failure modes

- **Two drivers drift during the transition.** S5 moves the loop onto the
  kernel but leaves the durable path unswitched, so until DE22/DE25 the
  duplicated driver code remains. Mitigation: the kernel-equivalence fixture
  is the only guard in that period; it runs in the durable tier on every
  change to either driver, and the DE22/DE25 adoption removes the duplicate.
- **Durable adoption of the policy kernel is a behaviour change.** The
  durable round has no budget, no unauthorized-tool admission and no result
  transform today. Adopting them (DE22, DE25) changes what a durable run
  admits and stores. Mitigation: the run route is not built before
  budget-aware admission exists in the durable round (section 4.4), so no
  product run depends on a budget the store does not enforce; the
  equivalence fixture names the cells that will change, so the adoption
  slices have their expected deltas before they start.
- **Projection emits a duplicate or out-of-order event on reopen.** The TUI
  would show a result twice. Mitigation: sequence-keyed idempotence, and
  crash cuts that kill between commit and emit and assert one rendering.
- **The opt-in route shows completed messages only until DE26.** A user may
  read that as a hang during a long generation. Mitigation: a visible
  "waiting for the committed answer" status from the existing status policy,
  documented in the opt-in docs; no default switch before DE26.
- **Hash-pinned closure cascade.** Each of the six slices edits pinned files,
  so each carries a 13–19 manifest re-pin and the CI reference job must stay
  green; forgetting the re-pin fails `just test` with "source drift". The
  cascade is mechanical but adds review noise; keep code and docs of a slice
  in one commit.
- **R15-3 WIP overlaps.** `wip/durable-r15-3-20261104` touches
  `tool_execution.py` and the Storage port. Landing P16 first forces a
  rebase of that WIP; landing R15-3 first delays P16. Owner question below.
- **Retention cost in the ordinary host.** A 50 KiB window per running tool
  is bounded, but long-running parallel bash calls now hold it in memory even
  when the tool returns explicit output. Mitigation: the window is the same
  buffer the live output already passes through; measure in the slice.
- **Request snapshot retention debt** (`docs/durable-generation.md`, "Request
  snapshot retention debt"): quadratic storage per generation. Not a blocker
  for the opt-in route; a blocker for DE44, owned by DE27/28.
- **Extension hooks rerun on resume** until DE22. The route refuses
  extensions in the allowlist; a hook that reaches the durable route before
  DE22 would be a defect in the refusal, so the refusal gets a test per hook
  kind.
- **Test-time growth.** New comparison cases add to the durable tier. Keep
  new full matrices in `just test-full` only, as `33df2a0d` did for the
  costliest matrix.

## 6. Test and qualification plan

Per slice: focused unit tests and mutants for the changed contract; `just
check`; `just test-durable`; `just test-full` before the delivery commit;
`just docs-build`; independent Opus 5.5 HIGH review; manifest re-pin; CI
green including the `durable-reference` job.

P16 qualification:

- Ordinary host: a fixture tool that omits output after reporting two chunks,
  a tool that reports and then returns explicit text, a tool that omits
  output with no reported output (empty content), a tool whose reported
  output exceeds the window (truncation diagnostic text and code), and a
  tool that reports details then omits them. The same five run through the
  durable transport with a committed-prefix cut between report and return.
- Oracle: extend `retained-tool-reporting-v1` so Pi's normal omitted-output
  case pairs with a native omitted-output producer; the common observables
  gain the final output text, the `truncated` diagnostic and the diagnostic
  order. Run against pinned 1.5.0 with the existing script
  (`scripts/durable_reporting_comparison.py run --acceptance ...`), then
  `attest` in a fresh process.

P15 qualification:

- Unit: signed text ingests, round-trips through the canonical codec at
  version 3, the checkpoint and completed projection, and the JSONL session
  tree (save and reload with a signed text-only result and with a signed
  text plus image result); truncation keeps the signature on the kept slice;
  the suffix block is unsigned; text-only unsigned results still store no
  blocks and old session files load unchanged; wire bodies of all four
  adapter families contain no signature for tool results; history equality
  distinguishes signed from unsigned.
- Oracle: a new pinned Harness case (or a case in the reporting phase) with a
  tool whose result carries a signed text block and an image, bounded by
  `maxLines`, followed by the next model request through the real OpenAI
  adapter with fake fetch. Common observables: stored result content with
  signatures, bounded content with signatures, the next request's tool
  message text, and reopen. The oracle also re-confirms the `boundContent`
  spread on 1.5.0 before the native projection change is accepted.

Kernel and projection qualification:

- Kernel-equivalence tests over shared fixtures for each extracted function.
- Projection crash cuts: SIGKILL after the generation settle commit and
  before emit, after the tool receipt commit and before emit, and after the
  run settles; reopen emits each event once, in commit order; a redelivered
  publication emits nothing.
- Route: a real-PTY smoke run of the opt-in route (`just test-pty-smoke`
  family) that kills during a tool call and reopens, asserting no duplicate
  visible result; the existing automation comparison gate stays green for the
  legacy route.

## 7. Implementation plan

Six Opus-sized slices, in order, each 30–90 minutes of implementation work
excluding the review round and the manifest re-pin. Each slice edits
hash-pinned files and therefore carries its manifest re-pin and a short
`docs/durable-tools.md` or `docs/tool-details.md` update in the same commit.

| # | Slice | Files touched | Acceptance |
| --- | --- | --- | --- |
| S1 | **P16 data model and ordinary host (90 min).** `RETAINED_OUTPUT` sentinel on `ToolExecutionResult`; `ToolContext.retained_output()`, `report_details`, `report_diagnostic` on the base context; the pure `Tail` moves to `native/tools/truncate.py`; a non-committing `RetainedOutputWindow` gate in `native/agent/tools.py` that the interruptible executor accepts (the "physically owned executor" refusal stays for the committing reporter); final-result fallback and the `truncated` diagnostic in the shared settlement. | `native/tools/base.py`, `native/tools/truncate.py`, `native/agent/tools.py`, `native/durable/tool_progress_values.py` (import move), `native/durable/tool_execution.py` (context subclass only), `native/durable/tool_output_values.py`, tests under `tests/` and `tests/durable/`, `docs/tool-details.md` | The five ordinary-host cases of section 6 pass on the interruptible executor; the durable window is byte-identical to today (existing DE13b tests green without expectation edits); explicit `""` stays empty; content bound assertion; mypy/ruff clean. |
| S2 | **P16 oracle (60 min).** Native omitted-output producer in the reporting comparison; common observables extended; fresh chain run and attest; manifest re-pin. | `scripts/durable_reporting_comparison.py`, `tests/fixtures/durable/retained-tool-reporting-v1.*`, `docs/durable-reference.md`, `docs/durable-tools.md` | Pi normal case and native normal case pair on output text, truncation diagnostic and order; `differences.json` no longer lists the producer-input delta; CI reference job green. |
| S3 | **P15 domain (90 min).** Signed text accepted in tool-result blocks; keep-when-image-or-signature rule in `normalized_tool_result_blocks`, `require_tool_result_blocks` and both docstrings; JSONL codec writes and reads `textSignature`; projection preserves signatures on truncated slices; wire adapters asserted signature-free for tool results; the two refusal tests flip to acceptance with new negatives. | `native/agent/content.py`, `native/agent/messages.py` (docstring), `native/assistant_blocks_json.py`, `native/durable/tool_output_values.py`, `tests/durable/test_tool_reporting_projection.py`, session-tree and provider wire tests, `docs/durable-tools.md`, `docs/session-tree.md` | Unit list of section 6 passes; canonical codec round trip byte-identical at version 3 with no new version; JSONL save and reload keep the signature; unsigned text-only results and old session files unchanged. |
| S4 | **P15 oracle (60 min).** Pinned Harness case with a signed text block, an image and `maxLines` truncation, plus the next request; native counterpart; attest; manifest re-pin. | comparison script of the chosen phase, its fixtures and manifest, `docs/durable-reference.md` | Both sides keep the signature through storage and bounding and send none on the wire; `boundContent` spread confirmed on 1.5.0; reopen equal. |
| S5 | **Turn kernel extraction, first two functions (90 min).** `settle_provider_result` and `transform_tool_result` move to `native/agent/turn.py`; `AgentLoop` calls them (no behaviour change); the durable path is not switched. The kernel-equivalence fixture runs the kernel and the current `_Generation.settle` on the same provider results and asserts equal assistant entries and admissions for the budget-free, all-offered case, and records the cells where durable lacks the behaviour (budget exhaustion, unauthorized tool, transform) as the expected deltas for DE22/DE25. | `native/agent/turn.py` (new), `native/agent/loop.py`, `tests/test_native_agent_*`, `tests/durable/test_generation*.py` (fixture only), `docs/architecture.md`, `docs/durable-generation.md` | Every existing loop and durable test green without expectation edits; the equivalence fixture passes on the shared cells and names the three missing cells; `just test-full` green. |
| S6 | **Committed event projection skeleton (90 min).** `CommittedEventProjection` over `observe_completed()` emitting the pre-DE26 mapping of section 4.3 to an `AgentEventSink`; sequence-keyed idempotence; crash cuts; no product wiring yet. | `native/durable/event_projection.py` (new), `tests/durable/test_event_projection*.py`, `docs/durable-completed-projection.md` | The three crash cuts of section 6 emit each event once in commit order; a redelivered publication emits nothing; `DurableProductSession` behaviour unchanged. |

After S6 the remaining work maps onto the plan's rows without new seams:
submissions and the inbox are DE18/DE19; the run route (section 4.4) is
DE42's terminal integration and DE43's JSON/RPC/SDK parity, and it waits on
DE18's submission carrying the policy state and on the first DE25 sub-step
(budget-aware admission in `_tool_round` with a versioned round document),
so DE42 cannot start before those two land; streaming through the same
projection is DE26; the JSONL export is DE41; the default switch is DE44.
The durable adoption of the kernel's transform is DE22; the remaining kernel
functions (`skipped_tool_results`, `round_disposition`) move with the rest of
DE25's round scheduler.

Ledger: `pipy-durable-loop-integration` moves to `designed` with this
document merged; `pipy-durable-p15-tool-text-signatures` and
`pipy-durable-p16-omitted-tool-output` reference sections 4.6, 4.7 and the
slices S1–S4; `pipy-durable-feature-plan` gets a note.

## 7a. Implementation record

- **R15-3 first (owner default for question 1).** R15-3 landed before S1 as
  `32fe8d2c`. It did not touch `tool_execution.py` after all (section 5 assumed
  it did); its overlap with S1 to S4 was the manifest re-pin only.
- **S1 decisions** (made in the slice, class A):
  - The ordinary host renders reported diagnostics and the `truncated`
    diagnostic as the durable projection does, in a `<harness>` block after the
    output text, so both hosts give the same model text. The ordinary result
    has no other diagnostics channel.
  - A partial result's details are not reported details in the ordinary host:
    Pi's agent does not fall back to them, and an existing test pins that. Only
    `report_details()` fills absent final details there. The durable reporter
    keeps taking a partial's details, as before.
  - Item 3's bound of an explicit output applies in the durable host only, as
    today. The ordinary host keeps an explicit result as the tool returned it,
    so no existing tool's ordinary result changes. The one exception is a tool
    that reported diagnostics and whose output and diagnostics together would
    exceed a result: its output is bounded to the window's end with the
    durable projection's `[tool output truncated]` notice, not Pi's
    `truncated` diagnostic, so both hosts give the same text.
  - The ordinary executor always gives a tool an output sink, so output is
    retained without a live consumer. The window costs about 150 KB per running
    call (owner question 3, measured with `tracemalloc`).
  - In the durable host, the `truncated` diagnostic of an omitted output is
    reported through the reporter after the tool returns, so it is committed
    with the others and the projection renders it.

- **S2 record.** The native normal fixture of `retained-tool-reporting-v1`
  now omits its output, and a fifth case (2100 lines) pairs the `truncated`
  diagnostic: its code, dropped lines, order and the kept lines. Pi's window
  keeps the beginning ("l\n" * 2000, 200 bytes dropped) and native's the end
  ("l\n" * 1999 + "l", 201 bytes dropped); the retained end, the byte count and
  the final newline are a declared raw difference until DE33. The producer-input
  difference left `differences.json`, a mutant `truncated_lost` was added, and
  `pipy-durable-p16-omitted-tool-output` closes with S1 and S2.

## 8. Open owner questions

1. Soll R15-3 (Storage-Batch-Commit, unreviewter WIP auf
   `wip/durable-r15-3-20261104`) zuerst landen, oder dürfen die Scheiben S1
   bis S4 vorgezogen werden? Beide berühren `tool_execution.py`; wer zweiter
   ist, muss rebasen. Empfehlung: S1 bis S4 zuerst, weil sie klein sind und
   R15-3 ohnehin neu aufgesetzt werden muss; ohne Antwort bleibt die
   Reihenfolge des 1.5-Bridge-Plans (R15-3 zuerst).
2. Soll die Durable-Route in der TUI schon vor DE26 (nur abgeschlossene
   Nachrichten, kein Streaming) als Opt-in freigeschaltet werden, oder erst
   mit dem committeten Streaming? Empfehlung: erst mit DE26 sichtbar machen;
   ohne Antwort bleibt die Route bis DE26 auf SDK und `pipy durable`
   beschränkt.
3. Darf der gewöhnliche (nicht-durable) Executor das 50-KiB-Fenster der
   Tool-Ausgabe für jeden laufenden Aufruf im Speicher halten, damit das
   Weglassen der Ausgabe in beiden Hosts gleich funktioniert? Empfehlung: ja,
   mit Messung in S1; ohne Antwort wird so gebaut.

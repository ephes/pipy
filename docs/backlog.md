# Pipy planning

Status: sole active planning basis, consolidated 2026-09-07.

## Start here

This document owns direction, priorities, task IDs, dependencies, and dispatch
state. Read the selected task and its referenced contracts, not old execution
ledgers. A task card is a bounded work order; it does not override a current
runtime contract. The orchestrator alone updates this index.

**Next dispatch:** D1b — canonical semantic compaction generation and guarded
conditional publication, after the preparation-cancellation refinement in this
planning chunk commits. Its Opus 5 review ended advisory with three
clarifications applied and no Warning/Critical; full checks remained green. D1a landed at `17089f3`: 5,405 tests
passed (two skipped), eight PTY smoke tests, lint/format/Mypy and docs build green;
Opus 5 returned two advisory suggestions, no Warning/Critical, with full coverage.
D0 landed at `f1fa668` with full validation and advisory Opus disposition. A/B/C
contracts landed at `ba08e9b` after two advisory Opus rounds. None of those
advisory dispositions is CLEAN. D2a/D2b follow D1b and refresh against its code.

**Completed:** the architecture migration, quality, transactional reload,
comparative remediation (including T1/C1), and god-file decomposition programs.
The September comparison and initial RPC documentation corrections are also
complete. No old “next slice” inside a historical document can reopen them.

**Scope:** a dependable, extensible daily-use native coding harness. Full Pi
feature parity is not required. Existing Pi-shaped interfaces and the
no-deprecation policy remain in force when a selected surface is realigned.

## Decision

Keep pipy's native runtime and its existing ownership boundaries. Prioritize
context continuity, one reusable product-session control surface, and recovery
over additional provider families or broad feature parity. Most of the necessary
product foundations already exist. The missing piece is consistent, dependable
behavior through those foundations, not another whole-repository decomposition.

The first acceptance target is interactive terminal coding, with a headless
session API that can support automation and extensions without reproducing
product policy. Live provider dogfooding is still outstanding. No new daemon, distributed scheduler, database, or async rewrite is
required by this plan.

## Evidence and limits

This is a source comparison of clean local checkouts on 2026-09-07:

| Repository | Commit | Role in this comparison |
| --- | --- | --- |
| pipy | `6bcc6670578d6f62562a37c8bfc2b1d07dbb4870` | Current implementation, tests, and documented contracts |
| pi-mono | `aa23e784c647d713e775a8adcaf3c219e84f5068` | Mature coding-session composition and daily workflows |
| tau | `f9e21ee60c9903548db0550faaf0a15f16c79a57` | Python harness/session separation and provider-history contracts |

References below are relative to each named repository. This is neither an
exhaustive parity audit nor a live provider benchmark. The July Pi gap audit is
historical; its reload prerequisite is complete and its ranking does not own
task selection. Its detailed provider gaps remain candidates, not prerequisites.

Pipy's decomposition and transactional reload programs are complete. At comparison
baseline `6bcc667`, `scripts/architecture_metrics.py --json` reported 91,749
source Python lines, 142,021 test Python lines, a 336-line coding-session composition root, and a
580-line TUI file (230 class AST lines, five definitions, nine fields). These
are snapshot measurements, not new acceptance limits. Small facades alone do
not establish a reusable API or prove daily usability.

Summary-safe archive searches found earlier architecture and review records,
including a six-round plan review and a separate explicit recommendation to
stop after a clean follow-up. Those are workflow context, not proof that this
design is correct. No raw transcripts were inspected.

## What is already worth keeping

- The provider-neutral `native/agent/` loop, immutable request authorization,
  injected tools/providers, and synchronous canonical events.
- The `native/coding/` state, input, command, and product-session owners; private
  native session trees and the existing terminal resume/fork/compact workflows.
- Extension generations and guarded publication, project trust, tool filtering,
  settings, resource loading, and the existing terminal editor/component owners.
- The separation between full-content product history and the optional,
  metadata-only `pipy_session` workflow archive.
- Strict source typing, import-boundary tests, focused concurrency contracts,
  and PTY coverage. Preserve semantic boundaries; do not add exact file-size or
  method-count gates for the proposed work.

## Findings that change the priority order

| Finding | Current evidence | Consequence |
| --- | --- | --- |
| Compaction loses old task facts from model context | pipy `native/agent/history.py::_agent_history_summary` emits counts and explicitly says details are unavailable; `native/repl/provider_selection.py::apply_compaction` persists that text | Semantic compaction is the first product improvement. The original private transcript remains stored; this is model-context loss, not deletion of the archive. |
| Context pressure is handled mechanically | pipy `native/repl/loop_step.py::_compact_if_needed` uses message/byte thresholds; `native/agent/history.py` cuts user groups | Long sessions and a large single run need model-aware context budgeting and safe request-boundary recovery. |
| Reusable internals lack a public product-session API | pipy `sdk.py::run_native` uses the intentionally separate compatibility runtime; `native/coding/session.py::run` takes streams and composes terminal/REPL input | Embedders cannot obtain the real multi-turn product with a small supported API. |
| RPC is a partial second control authority | pipy `native/automation/rpc.py` owns queues/flags, rejects live model changes, has no compact/session-switch handlers, and only records auto-compaction/auto-retry flags | Accepted command vocabulary must not be counted as implemented behavior. Route controls to one product owner rather than implementing another session engine in RPC. |
| Transient recovery is incomplete | pipy `native/agent/loop_policy.py` enforces zero retries; RPC `abort_retry` is inert | Existing Codex transport recovery does not supply an agent-wide retry lifecycle. |
| Direct RPC bash cannot be aborted | pipy `native/automation/rpc.py::_cmd_abort_bash` refuses a running command; `_cmd_bash` uses `command_sandbox.run_command`, independently of the model's `BashTool` | A responsive input loop is insufficient if the operation cannot stop. Preserve each caller's policy while sharing process lifetime capabilities. |
| Provider-neutral history is narrower than the references | pipy `native/agent/messages.py` retains assistant text and separate tool calls, without an ordered rich-content union or assistant provider provenance | Audit same-provider resume and cross-provider continuation before broadening providers. A schema migration is conditional on demonstrated lost information, not assumed necessary. |

Pi's `packages/coding-agent/src/core/sdk.ts::createAgentSession` exposes product
construction with resource, model, session, and tool injection;
`core/agent-session.ts` owns product control; modes consume that session.
`core/compaction/compaction.ts` uses semantic summarization and context budgeting.
These are the useful design lessons. Pi's newer `packages/agent/src/harness/`
durable operations/lanes are a separate scope and not needed for a local coding
assistant. Pi also retains large product owners: copying its file layout would
not automatically simplify pipy.

Tau's `src/tau_agent/harness.py::AgentHarness` separates its portable harness
from `src/tau_coding/session.py` and the TUI. Its typed blocks in
`src/tau_agent/messages.py` and `tests/test_cross_provider_history.py` are
useful reference contracts. It also has very large coding-session and TUI owners; adopt bounded
interfaces and behavioral tests, not its monoliths or an async architecture just
because it uses one.

## Target ownership

| Layer | Owns | Must not become |
| --- | --- | --- |
| Provider adapters | Wire encoding, capabilities, auth/transport integration, provider replay constraints | Product queues, session lifecycle, terminal policy |
| Canonical agent | One accepted run, provider/tool execution, request authority, canonical events | A CLI, persistent-session manager, or extension implementation |
| Coding session | Active model/history/tree, input admission, compaction, retry policy, lifecycle and control snapshots | Separate mutable state for each frontend |
| Composition/resources | Construct providers, tools, settings, storage and extension generations | A second agent loop |
| TUI, print, JSON, RPC, Python API | Input/output adaptation over the same product operations and events | Alternate implementations of compaction, queues, or model selection |
| Workflow archive | Summary-safe optional learning records | Provider-visible conversational memory |

Keep the synchronous core. A session has one authority for accepting and
settling operations. Existing session/publication locks continue to protect
their documented fields; frontend and worker controls enter those owners rather
than writing parallel state. Provider I/O, extension callbacks, and terminal
painting remain outside shared-state locks. Cancellation, EOF, close, and late
worker completion must converge on the same lifecycle owner. Do not introduce
a universal event bus, generic repository hierarchy, or service locator.

## Thread execution and commit protocol

Different threads provide fresh contexts; they do not imply simultaneous writes.
This repository uses `main`, and the shared checkout has **one assigned writer**.
Read-only auditors can run concurrently with that writer. They return findings
by message, never edit the index or fix code opportunistically. Do not create
side branches, run concurrent mutating checks, or reset another thread's work.

The orchestrator dispatches each task with the actual baseline commit, task ID,
allowed write set, accepted dependencies, and required review status. A thread
must check that baseline before starting; if it changed, revalidate its proposal
against the new code before requesting or receiving write ownership. For the
initial read-only wave, the reports must name their inspected commit and any
uncommitted delta. They are proposals, not accepted contracts.

For every implementation chunk:

1. Orchestrator selects one small outcome with its reviewed contract and grants
   one writer. The implementer does not independently spawn agents or reviewers.
2. Writer updates implementation, focused tests, matching docs and release notes.
   Shared files such as `CHANGELOG.md` belong to the current writer for that
   chunk; the orchestrator updates task state after the writer releases it.
3. Validate the complete chunk with focused tests and `just check`; use relevant
   PTY checks and `just docs-build`. Run `prek run --all-files` if a
   `.pre-commit-config.yaml` is present. Do not widen tests without a reason.
4. Freeze repository writes. Orchestrator invokes direct read-only Claude Code
   `claude-opus-5` through `claude-review-loop` over **all changed code**, tests,
   documentation and the caller-selected contract. Inspect bundle coverage;
   omitted changed code is not an acceptable review of the chunk.
5. Triage findings. Return repairs to the same writer. A follow-up review checks
   accepted findings and the repair delta; reopen broad scope only for a
   documented cross-cutting change. Default maximum: two plan/spec rounds,
   three code rounds, with the repository's shared-state exception intact.
6. Stop at CLEAN or when only explicitly adjudicated advisory feedback remains
   and another pass would add no material confidence. Never call advisory
   ISSUES “CLEAN”. Unresolved Critical/Warning findings block the commit; report
   them rather than continuing indefinitely. A failed review is not a verdict.
   If using the parity-loop workflow, its stricter CLEAN gate still applies.
7. Record summary-safe outcome and task state, then **commit this completed
   chunk on `main` before the next implementation task starts**. Include those
   bookkeeping changes in the validation/review scope. No task is `done` merely
   because an agent reports success: record its integrated commit and evidence.

An implementer returns an uncommitted scoped delta to the orchestrator, who owns
review and commit. A normal implementation chunk must finish before unrelated
changes accumulate. Pure deletion of completed historical documents can have a
large line count without being bundled with runtime implementation. Do not add
budget caps to subscription reviewer commands or substitute a model silently.

## Accepted investigation basis

A/B/C inspected clean `main` at `8cd69db0594f7eb9375f7060c3a320086f6811f0`
concurrently without repository writes, provider calls, or new test execution.
The following integrates their source/test evidence. The supporting D1/D2
contracts passed the planning commit gate with advisory Opus 5 disposition. Live provider configuration, semantic-summary quality, and
daily usability remain unverified.

### A — baseline evidence and D0 acceptance

| Scenario | Existing assertion / gap | Owner of next evidence |
| --- | --- | --- |
| Edit/check/reopen | `test_native_tools_edit.py::test_edit_tool_replaces_unique_match_and_streams_diff` proves mutation; `test_native_coding_session_tree.py::test_tool_loop_context_reconstructed_from_resumed_tree` resumes plain messages. No combined production-tool round trip. | D0 |
| Interrupt/steer | `test_native_coding_session_terminal_pty.py::test_pty_active_turn_interrupt_cancels_and_returns_to_prompt` and `::test_pty_steering_and_follow_up_queue_and_drain_order` cover fake-provider terminal recovery/order. | Cite existing coverage; live observation outstanding |
| Provider failure | `test_coding_session_provider_failure.py::test_exhausted_transport_failure_leaves_repl_usable_for_next_prompt` proves recovery on a new prompt, without retry. | D4a pins retry after earlier tool effects |
| Repeated compaction | `test_native_coding_session_resume_compact.py` pins count-only whole-group reduction; `test_native_coding_session_tree.py::test_durable_compaction_entry_survives_reload` pins one durable reduction. | D1 semantic and repeated-reopen coverage |
| Extension lifecycle | `test_native_extension_conformance.py::test_golden_conformance_extension` exercises the existing example; `test_native_coding_session.py::test_successful_reload_publishes_one_coherent_generation_across_real_consumers` pins reload coherence. | D8 reuses these; do not duplicate examples |
| Headless workflow | RPC tests pin event/correlation/history behavior; `test_architecture_mode_contracts.py::test_json_mode_preserves_real_loop_order_with_mode_boundaries` pins canonical order. Public multi-turn embedding is absent. | D2b; RPC controls remain D5 |

D0 implements exactly one deterministic scenario in
`tests/test_native_daily_use_baseline.py`: a scripted recording provider drives
production `read`, `edit`, and `bash` against a temporary tiny program/check;
assert actual file mutation, successful check and correlated model-visible tool
observations, then reopen the durable tree in a fresh `CodingSession` and assert
ordered, exactly-once replay into its first continuation request. Reopening must
not execute tools again. This is object-lifecycle restart evidence, not a live
provider or separate-process claim. Reuse existing tree/provider fixture patterns;
do not expand the production fake provider or fix runtime behavior incidentally.

D0's other writes are `docs/sessions.md` for the truthful support matrix and
`docs/rpc.md` to distinguish implemented behavior from accepted vocabulary and
its separate direct-command policy. `docs/backlog.md` remains orchestrator-owned.
No release note is needed for characterization alone; behavior changes need a
separate selected slice. Run the new module plus existing resume/tree, edit,
provider-failure, extension-conformance and RPC modules, `just check`,
`just docs-build`, and `git diff --check`. The PTY smoke recipe covers streaming,
chrome and trust; the interrupt/steer module is separate.

### B — D1 contract decisions

The reviewed [whole-group semantic contract](compaction.md#d1-implementation-contract)
retains state-first persistence and positive dropped-user-group counts. Generate
from prior summary plus dropped messages through canonical cancellable provider
execution. Capture owner identity, then conditionally publish once using existing
locks; generation failure and post-acceptance persistence failure are distinct.

Two inspected correctness boundaries belong to D1 integration: resumed summaries
must be separated structurally from real user groups, and durable cuts must name
the actual first retained entry even when it precedes the previous compaction.
The current `append_durable_compaction` counts only users after the last
compaction and can skip that second valid durable cut. Do not reproduce it.
Budgeting, within-run cuts, retries and custom compaction instructions remain later
work. Source owners and decisive checks are in the contract; refresh the exact
write manifest separately when D1a and D1b receive ownership.

### D1 staging after source refresh

The read-only refresh at `ba08e9b` splits D1 into two independently useful
commits under the same reviewed contract. D1a fixes durable cuts and rebuilds
while generation stays count-only; D1b adds semantic generation and its provider-
I/O freshness window. This changes commit size, not product scope or priority.

D1a's tree-owned coding projection carries real messages, separate prior summary,
and structural origin IDs while ordinary `build_context()` and JSONL stay
compatible. The existing product coordinator may retain the immutable loaded
projection to map reconstructed custom/branch-summary objects by identity; it
must not maintain another mutable conversation or match repeated text. Resolve
`first_kept_entry_id` into the compaction action before live acceptance. One
outer tree/effect lock then session-mutex section covers snapshot, pure cut,
mapping and state acceptance; the narrow coordinator seam releases the session
mutex before persistence while retaining outer tree ordering. No provider I/O or
new freshness epochs are needed in D1a. Keep the state-first failure contract.

D1a owns `session_tree.py`, `coding/product_session.py`, `coding/state.py`,
`repl/provider_selection.py` and `repl/wiring.py`, their focused tests and docs.
Pin repeated cuts after one new group, duplicate-text custom/branch origins,
compacted/uncompacted destination rebuilds and unchanged cumulative counters.
Update both the harness DTO description and rebuild sentence. A restored branch
summary can coexist with zero new-run compaction counters. D1b's handoff then
refreshes the guarded writer inventory and cancellation adapters; reload/model
publishers must remain assignment-only, using prebuilt binding identity for
invalidation rather than adding an allocating revision update there.

### D1b cancellation refinement

Source refresh at `17089f3` found that automatic compaction runs inside request
preparation, whose current result has no cancellation alternative. A cancelled
summary followed by a string notice would still run ordinary request hooks and
provider execution. The selected [canonical preparation alternative](harness-spec.md#d1b-request-preparation-cancellation-contract)
returns typed cancellation with history and delegates to existing loop settlement.
A hidden preparation/completion handoff was declined because it still requires a
request snapshot and exception/reset bookkeeping. This is a narrow D1b contract
refinement, not a new lifecycle or queue owner. Opus 5 reviewed it with three
advisory clarifications and no Warning/Critical; implement after this commit.

Add `native/agent/loop.py`, `test_native_agent_loop.py`, and
`test_native_coding_agent_run.py` to the refreshed D1b manifest. The coding adapter
already forwards exact preparation values. The existing D1b owner
`native/repl/loop_step.py::_RequestPreparationEffects` produces the typed
cancellation from automatic compaction; its manual command wrapper keeps the
string-notice port. Pin cancellation before ordinary
request hooks/render refresh/provider execution, valid anchor/overlay handling,
retained earlier tool effects, existing queue handoff and next-run reuse. The
bounded semantic helper reuses branch-summary construction, puts the final
instruction in actual request messages, and uses the captured binding plus the
run-owned canonical executor. Freshness guards and assignment-only model/reload
publishers remain as specified above.

Summary-safe cancellation search/list before this refinement found the current
compaction record, the daily-harness comparison, and an earlier canonical
cancellation-contract review. These support reusing established cancellation
ownership; no raw transcripts were inspected and no priority was changed.

### C — D2 contract decisions

The reviewed [product-session boundary](sdk.md#d2-implementation-contract) keeps
one composition, state, queue, controller and lifecycle across submissions.
`CodingSession.run` resets run state and wires a new lifetime; calling it for
every submission would lose continuity. The startup-candidate decorator already
hides private extension candidates and remains their owner.

Use a synchronous construction-thread driver with an explicit controller idle
yield. D2a first establishes the persistent controller/composition seam and
preserves the stream-driven lifecycle. D2b exposes a distinct supported SDK
factory with two-turn acceptance. Neither uses EOF as idle, starts a second
queue/worker, or changes `run_native`. Cancel is the only cross-thread entry;
full mutable result snapshots are not made thread-safe by assumption. D5a owns
queue admission/settlement migration; D6 owns session replacement and explicit
compatibility retirement. Refresh this proposal against D1 before dispatch.

## Implementation queue

D0 is committed at `f1fa668` and D1a at `17089f3`. D1b follows this
preparation-cancellation contract review and commit.
Each successor needs a refreshed bounded handoff and reviewed contract when its prerequisites are accepted. If its concrete diff
would contain multiple independently useful changes, split the task here before
dispatch; the table is not permission for a large uncommitted batch. A thread implements
only its assigned task; completing a prerequisite does not authorize all of its
successors. Source write sets identify expected owners, not permission to edit
every listed module. Add a file only when the selected behavior needs it.

| ID | Prerequisites | Bounded outcome / expected write owners | Acceptance |
| --- | --- | --- | --- |
| D0 | Complete at `f1fa668`; A accepted | Reuse/extend one deterministic daily-use scenario and record a truthful support matrix; focused tests and user docs only | Tool-backed edit/check/resume evidence; identify uncovered cancel/compaction/extension behavior; live-provider status explicit; no incidental fixes |
| D1a | Complete at `17089f3`; D0 committed; B reviewed | Structural origin and prior-summary projection, pre-resolved durable cut, destination-owned context rebuild; matching docs/release note | Repeated durable cuts and equivalent reopen; no synthetic summary group; no text-based origin lookup; state-first failure preserved |
| D1b | D1a; reviewed preparation-cancellation refinement | Canonical semantic generation and guarded conditional publication through existing compaction owners; matching docs/release note | Prior summary and task facts survive; failed/cancelled/stale generation publishes nothing; cancelled preparation invokes no ordinary request construction/hooks/render refresh/provider turn; late completion, overlays and privacy pinned |
| D2a | D1b; C reviewed and refreshed | Persistent controller/composition lifetime with explicit idle yield; existing stream driver delegates to it | Start/shutdown once, idle is not EOF, shared state/queue preserved; existing lifecycle/event/terminal checks green |
| D2b | D2a | Distinct supported product-session SDK factory over that lifetime; API tests and SDK docs | Two-turn tool workflow, events, cross-thread cancel, owner-thread snapshot and disposal; no caller streams/private candidates or duplicate state |
| D3a | D1b | Model-aware budgeting at current safe request boundaries; model/request measurement and compaction trigger | Count system text, tools, attachments, history and output reserve; small-context tests; explicit recoverable refusal when no safe sufficient cut exists |
| D3b | D3a; dedicated safe-cut spec | Long-single-run compaction; history representation plus all product/persistence/reconstruction consumers | Explicit replacement of positive dropped-group invariant; no orphan tools, duplicated entries or lost prior summary; cancellation and resume coverage |
| D4a | D0; refresh against D1b/D2b | Bounded cancellable provider retry; canonical agent/provider-turn mechanism and coding policy/configuration | Retry the unchanged failed request within one accepted iteration, not the whole run; bound nested transport attempts; preserve prior tool effects; cancel/exhaustion/event-order tests |
| D4b | D1b, D4a | Apply the same retry mechanism to summaries | Failed/cancelled summaries publish nothing; accepted retry persists once; true-idle waits for settlement |
| D5a | D2b | Extend session facade with queue admission/settlement; migrate RPC prompt/queue/abort/state | Inventory guarded readers/writers; preserve reservation, agent-end/settled atomicity, isStreaming and abort/close boundaries; no old/new dual writers; API/RPC equivalence |
| D5b | D5a | RPC model/thinking controls through existing session model-selection owner | Truthful snapshots, real next-request selection, existing refresh/trust rules, correlated responses |
| D5c | D5a, D1b | RPC compaction and auto-compaction controls | Controls affect real session policy; preserve documented event names, reason values, framing and correlation |
| D5d | D5a, D4a | RPC retry enable/abort | Controls reach the real retry owner; abort/backoff/settlement races covered |
| D6a | D2b, D5a | Session resume/close API | Equivalent reconstructed context; extension lifecycle once; no late writes after retirement |
| D6b | D6a | Fork/clone/session replacement API | Existing tree and extension veto contracts; rebind observations once; caller-visible outcomes and persistence agree |
| D6c | D6b | Incremental frontend adoption and deliberate compatibility SDK retirement, one entry point per chunk | Same session owner across SDK/modes; executable embedding example; update docs and remove replaced surfaces without aliases |
| D7a | D0; coordinate with D5 writer | Direct RPC bash process cancellation; `command_sandbox.py`, existing process-lifetime capabilities, RPC bash handlers and tests | Process-tree termination; timeout differs from explicit abort; one terminal response; preserve direct-command versus model-tool policies |
| D7b | D7a | Correlated incremental RPC bash output | No updates after completion; bounded output, JSONL purity and EOF disposal |
| D8 | D0; refresh embedding after D2b/D6c | Reuse existing extension conformance example/tests; audit provider replay before selecting changes | `docs/examples/extensions/pipy-extension-conformance.py` already covers tools/commands/events: prove missing behavior before adding examples; same-provider resume/cross-provider history evidence before schema work |

D1a/D1b deliver the first product improvement. D2a/D2b are the main
extensibility investment.
D3 and D4 can be reordered after measured failures justify it; D7 can move earlier
if direct-command cancellation matters to the selected workflow. The
orchestrator records such a decision here before dispatch. No simultaneous
runtime writers: the D1a–D4b slices share session/provider integration, and the
D5/D7 families share
`rpc.py`. Read-only investigation of later tasks can proceed during any of them.

For retry ownership, the coding session owns limits and enable/abort policy;
the canonical agent/provider-turn boundary owns request reissue before final
failed-turn settlement. The no-progress guard applies to the retried request
attempt. Never repeat an accepted run or previously executed tools. The selected
D4a spec owns event details and replacement of the zero-retry invariant.

### Implementation handoff requirements

The orchestrator supplies a concrete implementation prompt only at dispatch.
It must name the exact commit, task ID, accepted spec decisions and allowed
write set; absolute paths for required reading and primary files; explicit
non-goals; tests to run; docs/release notes; and a descriptive session slug.
It must say “implementation task, not review”, prohibit invoking other agents
or reviewer commands, and require actual code changes within the assigned scope.
If new state ownership is necessary, stop and report it for a spec decision.

Return: files changed, behavior implemented, focused/full check results, docs
and release-note changes, complete/partial status, out-of-scope work, risks, and
summary-safe workflow events. Do not commit independently: release write
ownership to the orchestrator for review, then commit through the chunk protocol.
A self-produced review verdict is not evidence. D1a receives the next handoff
only after D0 passes its review and commit gate.

## Minimum acceptance and deferred work

Daily-use acceptance means completing a real repository task, running its
checks, interrupting/steering work, resuming after exit, and continuing after
compaction without reconstructing the task manually. Provider failure leaves a
usable session; an extension can be added through documented APIs. Automated
assertions cover lifecycle and request content; live dogfooding checks semantic
summary quality and UX. Tests alone are not a daily-usability claim.

Keep the synchronous native core, provider-neutral authority and event contracts,
full-content private product sessions, separate metadata-only learning archive,
project trust and extension publication boundaries. Preserve existing behavioral
and shared-state tests; retire only historical queue/line-count narration that
no longer describes an active requirement. No new exact shape gates.

Defer parallel tools until latency evidence justifies them (start with explicitly
safe reads); strict schemas until a primary provider needs capability-gated
constraints; additional provider/OAuth families, image generation, distributed
agents, durable lanes, server mode, full component/theme parity and packaging.
No new async runtime, DI framework, universal event bus or decomposition program.
Measure startup/cancel latency and request size before setting performance gates.
Isolate tests from global settings; leave both local theme stores on `pi`.

## Contract map and history

Current behavior lives in [Architecture](architecture.md),
[Harness Spec](harness-spec.md), [Session Tree](session-tree.md),
[Extension API](extension-api.md), [Provider Catalog](provider-catalog.md),
[Settings & Config](settings-config.md), [Automation & RPC](automation-rpc.md),
[TUI Workflow](tui-workflow.md), [Export](export-distribution.md), and
[Session Storage](session-storage.md). Retain the detailed
[transactional reload contract](specs/2026-07-25-transactional-extension-reload-rebuild.md).
A dated filename does not make a runtime contract obsolete.

The [July assessment](2026-07-29-architecture-quality-assessment.md),
[Pi gap snapshot](pi-mono-gap-audit.md), and [historical parity inventory](parity-plan.md)
are evidence, not alternative queues. Existing feature plans/specs remain
supporting rationale where linked by current contracts; their old next-step or
full-parity instructions do not override this index. Do not delete those specs
without migrating their unique behavior contracts first.

The old backlog and completed migration/quality/remediation/decomposition plans
are removed from the live planning surface. Retrieve them from Git when needed:
`git log --all -- <path>` and `git show <commit>:<path>`. The integrated direction
replaces the dated September comparison plan; do not maintain both copies.

The September comparison baseline passed lint, formatting, Mypy and 5,384 tests
(two skipped), plus documentation builds. Its two substantive Opus 5 reviews
ended with advisory suggestions and no remaining Warning/Critical findings;
it was not represented as CLEAN. Those outcomes do not review future specs or
code. Each selected chunk records its own review and commit evidence through
the normal summary-safe session archive, without copying transcripts here.

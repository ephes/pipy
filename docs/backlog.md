# Pipy planning

Status: sole active planning basis, consolidated 2026-09-07.

## Start here

This document owns direction, priorities, task IDs, dependencies, and dispatch
state. Read the selected task and its referenced contracts, not old execution
ledgers. A task card is a bounded work order; it does not override a current
runtime contract. The orchestrator alone updates this index.

**Next task:** D4b3b canonical private branch execution, retry and cancellation,
after D4b3a guarded publication is reviewed and committed from `836b413`.
Reuse that acceptance boundary and the D4a/D4b1 executor contract; no new session,
queue or lifecycle owner. Refresh the implementation handoff against the committed
seam before dispatch.

D4b3a implements original-work freshness checks and coherent tree/coding acceptance
through existing owners, followed by branch-bound input clearing and exact-entry
persistence under the outer mutation lock. Append failure preserves accepted live
state and propagates without success notice or prefill. The full runtime gate
passed 5,971 tests (two skipped), static checks and unchanged interpreter snapshots.
A later test-only correction isolates header mutation to the branch operation;
the affected integration module passed 65 tests and full typing passed. PTY smoke
passed eight; docs/diff checks passed. Root independently passed 246 focused tests and all static checks. One complete
Opus 5 High code round closed advisory, not CLEAN: no warnings or critical findings.
Two optional suggestions (an extra projection list copy and callback field naming)
were left unchanged; existing call sites and ownership remain correct. No material
finding remains; stop at diminishing returns without another review round.

D4b2 `836b413` selected this ownership boundary in one adjudicated advisory Opus
plan round. D4b1 `9ccb22e` completed semantic-compaction retry in one advisory code
round with no material findings remaining. These were advisory outcomes, not CLEAN.

D4b0 `6262965` was the scheduled grooming after D4a1–D4a3. D4b1 and D4b3a are the
two implementation chunks since that point; D4b3b will complete the three-commit
interval, then groom at the recovery-to-RPC phase boundary. The D4b2 ownership
refresh inspected summary-safe D4b search results and Markdown summaries plus
current source; no raw transcripts were read. Earlier D0/D1, D2 and D3 outcomes
remain complete. Live-provider dogfooding, budget accuracy and semantic summary
quality remain unverified.

Validation lesson: test fixtures isolate HOME. Do not wrap project `uv` or
`just check` in a temporary HOME; isolate standalone product experiments
separately. Capture interpreter links and configuration immediately before and
after full validation, before another `uv` command can repair a mutation.

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

## Initial findings that set the priority order

| Initial finding | Evidence at comparison baseline | Consequence |
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
| Headless workflow | RPC tests pin event/correlation/history behavior; `test_architecture_mode_contracts.py::test_json_mode_preserves_real_loop_order_with_mode_boundaries` pins canonical order. D2b adds two-turn product embedding and cancellation coverage. | D2b complete; RPC controls remain D5 |

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
D1a at `17089f3` removed the former since-last-compaction user-count lookup and
now persists the pre-resolved retained entry ID. Preserve that structural mapping.
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
advisory clarifications and no Warning/Critical at `297b055`; D1b implements it
after the guarded-run prerequisite.

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

### D1b0 — guarded coding-run prerequisite

Source verification at `297b055` confirmed a supported lost update before the
summary freshness window: retained command/input/before-agent-start model
controls may replace the binding and clear history after `run_turn` reads it,
then request preparation mirrors the old history back. The summary-only safe
stop would miss this write, canonical message appends, and usage publication.
The D1b0 implementation at `8ad7d66` repairs that shared-state defect before
dependent semantic work resumes.

The [guarded run contract](harness-spec.md#guarded-coding-run-publication-contract)
is implemented with a scoped state-owned witness captured atomically with initial
history. Deterministic tests exercise retained controls before preparation, after
its mirror and hooks, during turn start, after tools, before usage and final
settlement; newer binding/history/tree/usage survive and cleanup closes the lifetime.
Binding identity and a replacement epoch detect context changes while ordinary
canonical appends/mirrors/compaction remain valid. Guard preparation/final mirrors,
canonical appends, compaction and usage plus run-dependent history/binding reads
under the state mutex. No active witness leaves ordinary shell/manual-compaction
writes admitted. Capture binding labels for pricing and provider execution; order
live and durable appends with the existing outer tree lock. A stale writer raises before
mutation and existing cleanup closes the session lifetime. No recovery framework,
queue migration, event schema change, or assignment-only publisher weakening is
selected. D1b still needs its separate full summary-freshness snapshot.

D1b0's bounded write set is `coding/state.py`, `coding/agent_run.py`,
`repl/wiring.py` and `repl/loop_step.py`, their focused tests and matching
docs/release notes. Preserve
canonical duplicate-message normalization and completed tool effects. Summary-
safe history searches found no prior lost-update lesson to substitute for these
source/test contracts. D1b committed at `527b846`: canonical semantic requests,
guarded freshness and state-first persistence are implemented. Later slices must
retain the live-run witness, guarded history/usage publication, complete
binding/tree/pointer/generation freshness, and cancelled-preparation early exit.
The durable [compaction contract](harness-spec.md#canonical-agent-history-compaction)
and [user behavior](compaction.md) replace the completed dispatch instructions.

### C — D2 completed ownership boundary

D2a `4b5baa9` provides the persistent controller lifetime; D2p `365e0ae` shares
adapter-owned preparation; D2b `faffcd0` exposes the synchronous product SDK.
The [SDK contract](sdk.md#d2-implementation-contract) owns entry, cancellation,
privacy, diagnostics and cleanup. One state, queue, controller and generation
remain authoritative; no per-submit archive run or replacement event history.
The D2a0 facade decision `936b96d` remains a measured 399-line downward ratchet,
with prepared composition in existing wiring. Native modules cannot import the
outer product facade/SDK; existing automation entrypoint adapter composition is
unchanged. D5 owns queue admission migration; D6 owns lifecycle expansion and
explicit compatibility retirement; D7 owns direct RPC bash cancellation.

### D3a — Completed request-budget boundary

D3a0 `5819b25` fixed the [budget contract](harness-spec.md#model-aware-request-budget-contract).
D3a1 `d4b5c50` supplies estimates and declaration provenance; D3a2 `4d7be4d`
supplies recoverable preparation refusal; D3a3 `9f750e7` composes live policy.
Known limits combine declared metadata and an optional explicit ceiling, include
an estimated output reserve, and replace the legacy trigger. Unknown limits
retain legacy policy; `keepRecentTokens` is inactive. Current contracts and user
recovery are in [Compaction](compaction.md), not pending implementation cards.

The durable first-iteration origin boundary remains explicit: the accepted user
is persisted after preparation, so a latest-group cut to it can refuse before
summary generation. Normal turn settlement records it once. D3b preserves this boundary; do not move publication or fabricate provenance.

### D3b0 — Safe-cut contract and scheduled grooming

This is the required grooming after the three D3a implementation commits.
Summary-safe `pipy-session search` found current budget evidence and nine
compaction-related records; the catalog was inspected through `list`. D1 records
reinforce state-first persistence, guarded origin and cancellation rules. Current
source/tests at `9f750e7` supply the cut evidence. Stale D3a dispatch pointers and
long completed status prose are removed; priority remains unchanged.

The [selected D3b contract](harness-spec.md#within-run-compaction-contract-d3b)
resolves the read-only canonical, durable and admission reports:

- Preserve terminal results with a private appended-message list owned by the
  existing synchronous canonical run. Provider history can shrink independently;
  request rewrites and empty failure artifacts are not appended output.
- Require actual nonempty removal and ordered identity-preserving retention.
  Whole-group counts stay truthful, including zero for an intra-group cut.
- Retain the exact accepted user and newest complete tool cycle. Initially refuse
  intra-group selection for incomplete or ambiguous current groups; manual and
  unknown-limit policy stay unchanged.
- Represent the retained user separately from the suffix. Once such a cut occurs,
  later cuts use the effective projection so ordinary compaction cannot resurrect
  removed cycles. Strict new-field parsing and both-reference fork remapping are
  part of the durable prerequisite; legacy-only records keep current behavior.
  Model/thinking settings still reconstruct from the full ancestor path.
- Choose any compound whole-group/older-cycle removal before one summary request.
  Summarize explicit removed messages with separately labelled retained task
  orientation; preserve exact preflight, final admission and state-first failure.

The optional format field requires an anchor-aware reader; no general version
compatibility gate, manual fallback, terminal event rewrite or new session owner
is selected. The first-iteration unresolved origin remains a refusal. The current
run retains appended payloads until settlement, so context compaction does not
promise bounded total output memory. Live-provider quality remains unverified.

### Completed validation prerequisites and D3b1 grooming

D0h `1a81e71` isolated external-editor test dependencies and file effects.
Its focused/full/PTY checks passed, but full validation still relocated the
interpreter into temporary HOME. D0i0 `bff2e32` therefore specified existing-venv
score execution; D0i `fc786bf` implemented `PIPY_PARITY_PYTHON`, preflight of all
four dispatch targets before scoring, direct installed Python/pytest/CLI use,
and a rejecting uv trap. All 49 real checks and default standalone behavior remain.
Both D0i full runs passed 5,802 tests (two skipped) with unchanged interpreter
links, target metadata and configuration; a plain-shell run also passed 49/49.
The unsafe uv path is removed; the individual historical relink was not causally
reproduced. Future validation must inspect the actual interpreter links and
configuration before another uv command can repair an environment mutation.

D0h and D0i each received one exact Opus 5 code review with Suggestions only;
aligned comments were fixed, optional defensive refinements declined. D0i0 used
two plan rounds: its final preflight finding was repaired locally at the cap and
then verified by D0i's executable tests and independent code review. These are
adjudicated advisory outcomes, not CLEAN verdicts.

D3b1 `cc902a8` preserves canonical terminal appends, D3b2 `0a3d7ef` proves
safe cuts and actual removal, D3b3 `2b5217d` persists retained-user anchors,
and D3b4 `68dddcf` activates known-limit cuts through the existing typed
compaction port. Full checks passed 5,811 / 5,834 / 5,856 / 5,864 tests
respectively (two skipped each), with matching docs and adjudicated advisory
Opus reviews. Manual and unknown-limit cuts retain their previous policy.
All four slices are complete; the contract above and matching runtime specs
remain the basis for later work.

## Implementation queue

D0 is committed at `f1fa668`, D1a at `17089f3`, and D1b0 at `8ad7d66`.
D1b is committed at `527b846`; D2a0 at `936b96d`; D2a at `4b5baa9`;
D2p at `365e0ae`; D2b at `faffcd0`.
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
| D1b0 | Complete at `8ad7d66`; full checks and advisory Opus review; contract `4c5986d` | State-owned run witness and guarded history/compaction/usage publication; ordered durable append; matching docs/release note | Supported context mutation cannot be overwritten by preparation, message, usage or final settlement; stale run closes safely; ordinary multi-iteration runs and publisher guards preserved |
| D1b | Complete at `527b846`; full checks and advisory Opus review; D1b0 and preparation-cancellation contract accepted | Canonical semantic generation and guarded conditional publication through existing compaction owners; matching docs/release note | Prior summary and task facts survive; failed/cancelled/stale generation publishes nothing; cancelled preparation invokes no ordinary request construction/hooks/render refresh/provider turn; stale automatic context closes before further mutable publication; late completion, overlays and privacy pinned |
| D2a0 | Complete at `936b96d`; full checks and advisory Opus plan review | Docs-only scoped facade placement and size-gate contract | Independent plan review and commit before dependent test/code changes |
| D2a | Complete at `4b5baa9`; full checks and advisory Opus code review; D2a0 accepted | Persistent controller/composition lifetime with explicit idle yield; existing stream driver delegates to it | Start/shutdown once, idle is not EOF, shared state/queue preserved; existing lifecycle/event/terminal checks green |
| D2b0 | Complete at `e0f82b6`; full checks and advisory Opus plan review | Docs-only provider, preparation and cancellation ownership contract; bounded D2p split | Independent plan review and commit before dependent code |
| D2p | Complete at `365e0ae`; full checks and advisory Opus code review; D2b0 | Shared preparation in existing `CodingSessionAdapter`; stream adapter delegates without changing archive or failure ordering | Same prompt, settings/trust identity, tools/resources/reference roots; preparation emits no archive events; no public API or cancellation changes |
| D2b | Complete at `faffcd0`; full checks and Opus follow-up CLEAN; D2p/D2b0 | Distinct supported product-session SDK factory over that lifetime with explicit provider injection; canonical provider/summary/model-tool cancellation; API/RPC tests, SDK/RPC contracts and release notes | Two-turn tool workflow, events, cross-thread cancel and settlement race, owner-thread snapshot and disposal; no caller streams/private candidates or duplicate state |
| D3a0 | Complete at `5819b25`; full checks and advisory Opus plan review; D1b/D2b | Reviewed budget, provenance, refusal and cut contract; backlog grooming | Docs-only independent review and commit before new contracts are implemented |
| D3a1 | Complete at `d4b5c50`; full checks and focused Opus follow-up CLEAN; D3a0 | Pure request estimates and declared context-limit provenance; `coding/request_budget.py`, catalog data/config/resolver owners, focused tests/docs | System/effective messages/tools/images/framing/reserve; explicit versus placeholder/unknown limits; no runtime admission or new provider calls |
| D3a2 | Complete at `4d7be4d`; full checks and advisory Opus code review; D3a1 | Typed recoverable preparation refusal in canonical loop/status ports and existing coding-state/result projections | No provider invocation/usage or fake ProviderFailed event; accepted input/prior tools once; queue handoff once; next prompt succeeds; guarded failure publication |
| D3a3 | Complete at `9f750e7`; full checks and advisory Opus review; D3a2 | Model-aware request admission and bounded semantic compaction through current product request/compaction owners | Pre-hook trigger and summary preflight; hooks once; final frozen request refusal; one whole-group attempt; cancellation/staleness/privacy/reopen tests |
| D3b0 | Complete at `1a712f3`; full checks and advisory Opus plan review; D3a3 | Safe-cut, run-result and durable-origin contract; scheduled grooming | Independent spec review and commit before new contracts are implemented |
| D0h | Complete at `1a81e71`; full checks and advisory Opus review; D3b0 | Isolate external-editor test mocks and temporary file effects; `tests/test_native_extension_external_editor.py` and backlog only | Global stdlib callables stay untouched; mocks validate editor argv and test-owned paths before effects; existing editor behavior/cleanup assertions, full checks, interpreter-link inspection performed and unresolved outcome recorded |
| D0i0 | Complete at `bff2e32`; two Opus plan rounds, final repair locally adjudicated; D0h | Bounded existing-interpreter score-test contract above; backlog only | Independent review and commit before D0i code |
| D0i | Complete at `fc786bf`; full checks and advisory Opus code review; D0i0 | Isolate legacy score-test interpreter execution; expected script/test owners above | All real score checks retained; no test-triggered uv environment mutation; unchanged interpreter links/configuration after focused/full validation |
| D3b1 | Complete at `cc902a8`; full checks and advisory Opus review; D3b0, D0h, D0i | Preserve appended current-run terminal results in existing `agent/loop.py`; remove obsolete `agent/active_input.py::result_messages`; focused canonical/automation/extension tests and docs | Reduced prepared history cannot cause terminal results to omit earlier appends in the same run; exact anchor/overlay checks, terminal/callback semantics, counters and fresh queued runs preserved |
| D3b2 | Complete at `0a3d7ef`; full checks and two advisory Opus rounds; D3b1 | Explicit pure cut/cycle analysis and actual-removal acceptance proof; `agent/history.py`, `coding/product_session.py`, `coding/state.py`, current action construction; update harness Canonical Agent-History Compaction and Native Session Workflow Decision contracts | Safe newest-cycle retention, identity partition/counts, truthful zero groups, guarded no-op/replacement refusal; existing live whole-group behavior only |
| D3b3 | Complete at `2b5217d`; full checks and two adjudicated Opus rounds; D3b2 | Optional retained-user durable reference and effective reconstruction; `session_tree.py`, `coding/product_session.py`, current persistence callback; update `session-tree.md` planned-field paragraph and matching docs | Strict new-field errors, origin validation, both-reference fork remap; repeated anchor then whole-group cuts never resurrect removed cycles; no automatic activation |
| D3b4 | Complete at `68dddcf`; full checks and two adjudicated Opus rounds; D3b3 | Known-limit automatic within-run activation; existing `repl/loop_step.py`, typed `repl/loop_scope.py` port, `provider_selection.py`, `coding/compaction.py` and coupled tests/docs | One chosen compound cut and summary; explicit removed input/task orientation; exact effects/usage/terminal results, preflight/cancel/stale/refusal/persistence/reopen coverage |
| D4a0 | Complete at `e0f2f84`; full checks and two adjudicated Opus plan rounds; D0/D1/D2/D3 complete | Reviewed retry ownership/attempt contract and scheduled grooming; backlog and harness/RPC specs only | Review and commit before new retry contracts are implemented |
| D4a1 | Complete at `d52778e`; full checks and CLEAN focused Opus follow-up; D4a0 | Optional prepared provider-attempt capability; `native/provider.py`, `openai_codex_provider.py`, existing start-gated forwarding in `agent/provider_turn.py`, focused provider/executor tests | One body/header preparation; no inner retry/backoff; explicit logical/physical accounting; prepared Retry-After hint without standalone-policy cap, ordinary path caps unchanged; existing WS fallback and direct/compatibility behavior preserved; no canonical retry activation |
| D4a2 | Complete at `ffb4e25`; full checks and CLEAN third Opus round; D4a1 | Opt-in canonical request retry mechanism, pure policy and injected caller-thread `before_reissue` admission port in existing agent/provider-turn boundary, narrow value helper if needed, coupled tests/docs | One accepted execution lifetime, conservative no-progress eligibility, bounded cancellable waits, owner-thread retry events, no late reissue or callback after retirement; default executor remains one-call |
| D4a3 | Complete at `cf8f194`; full checks and one adjudicated advisory Opus round; D4a2 | Ordinary product policy capture, guarded admission closure and activation; current `repl/loop_step.py`, scope/wiring and settings owners, canonical/product/automation acceptance tests and docs | Retry unchanged failed request within its accepted iteration; earlier tools/history/usage once; guarded stale admission, true settlement and usable next prompt; non-capable providers stay single-call despite enabled policy; compatibility unchanged |
| D4b0 | Complete at `6262965`; full checks and one adjudicated advisory Opus plan round; D4a3 | Scheduled grooming and semantic-summary retry contract; backlog and harness spec | Separate the two summary paths; fix the next implementation boundary without prematurely specifying branch publication |
| D4b1 | Complete at `9ccb22e`; full runtime checks and one adjudicated advisory Opus round; D4b0, D1b, D4a3 | Semantic-compaction retry in existing `ProviderMutationEffects`, optional shared policy conversion, focused compaction tests/docs | One captured policy/request/cut; original full compaction witness before reissue and acceptance; private retry events; cancellation and stale work publish nothing; accepted state persists once without rollback |
| D4b2 | Complete `836b413`; full checks and one adjudicated advisory Opus plan round; D4b1 | Branch-summary capture/conditional-acceptance ownership contract in existing session/tree owners; planning only | Resolve the return-to-append gap, tree/context rebuild and durable append ordering before branch retry code; inventory guarded readers/writers and preserve state-first persistence semantics |
| D4b3a | Complete in this chunk; full checks and one adjudicated advisory Opus code round; D4b2 | Guarded branch-summary capture/acceptance and narrow tree/product acceptance-persistence seam; existing ordinary provider behavior | Reject stale generated text without tree/history/editor mutation; tree/leaf and coding projection accept coherently, branch-bound inputs clear, durable append follows under existing outer ordering; append failure retains accepted state |
| D4b3b | D4b3a, D4b1 | Canonical private branch execution, retry and cancellation through the committed branch owner | Frozen request/policy/witness, prepared and non-capable provider paths, no private event/usage leak, cancellation in provider/backoff phases and correct manual input settlement; no repeat acceptance or persistence |
| D5a | D2b | Extend session facade with queue admission/settlement; migrate RPC prompt/queue/abort/state | Inventory guarded readers/writers; preserve reservation, agent-end/settled atomicity, isStreaming and abort/close boundaries; no old/new dual writers; API/RPC equivalence |
| D5b | D5a | RPC model/thinking controls through existing session model-selection owner | Truthful snapshots, real next-request selection, existing refresh/trust rules, correlated responses |
| D5c | D5a, D1b | RPC compaction and auto-compaction controls | Controls affect real session policy; preserve documented event names, reason values, framing and correlation |
| D5d | D5a, D4a3 | RPC retry enable/abort | Controls reach the real retry owner; abort/backoff/settlement races covered |
| D6a | D2b, D5a | Session resume/close API | Equivalent reconstructed context; extension lifecycle once; no late writes after retirement |
| D6b | D6a | Fork/clone/session replacement API | Existing tree and extension veto contracts; rebind observations once; caller-visible outcomes and persistence agree |
| D6c | D6b | Incremental frontend adoption and deliberate compatibility SDK retirement, one entry point per chunk | Same session owner across SDK/modes; executable embedding example; update docs and remove replaced surfaces without aliases |
| D7a | D0; coordinate with D5 writer | Direct RPC bash process cancellation; `command_sandbox.py`, existing process-lifetime capabilities, RPC bash handlers and tests | Process-tree termination; timeout differs from explicit abort; one terminal response; preserve direct-command versus model-tool policies |
| D7b | D7a | Correlated incremental RPC bash output | No updates after completion; bounded output, JSONL purity and EOF disposal |
| D8 | D0; refresh embedding after D2b/D6c | Reuse existing extension conformance example/tests; audit provider replay before selecting changes | `docs/examples/extensions/pipy-extension-conformance.py` already covers tools/commands/events: prove missing behavior before adding examples; same-provider resume/cross-provider history evidence before schema work |

D1a/D1b deliver the first product improvement. D2a/D2b are the main
extensibility investment.
D4 recovery remains next; D7 can move earlier
if direct-command cancellation matters to the selected workflow. The
orchestrator records such a decision here before dispatch. No simultaneous
runtime writers: the D1a–D4b slices share session/provider integration, and the
D5/D7 families share
`rpc.py`. Read-only investigation of later tasks can proceed during any of them.

### D4a — Refreshed retry execution basis

Read-only Sol reports inspected `68dddcf`. Codex prepares body/auth/extension
headers once, then `_OpenAICodexAttemptRunner` retries logical attempts; one
attempt can make up to three WS/SSE requests. `tests/test_openai_codex_retry.py`
pins header-callback-once and pre-event retry, while
`tests/test_native_openai_codex_provider.py` pins bounded fallback. Repeated
ordinary `complete()` calls would repeat preparation and nest allowances.
The [selected retry contract](harness-spec.md#bounded-request-retry-contract-d4a)
therefore uses an optional prepared capability with one logical-attempt budget,
retaining the existing bounded transport negotiation. No global provider policy
mutation, request-field rewrite or provider-name capability inference is allowed.

The executor owns only request reissue, cancellation and retry events. Existing
agent result/usage/tool settlement and product state/lifecycle remain authoritative.
D4a1 is independently useful as the safe provider boundary; D4a2 proves the
opt-in mechanism before D4a3 enables ordinary product requests. Summary adoption
is D4b, queue/RPC policy migration D5. Each successor gets a handoff refreshed
against its committed prerequisite, not all listed files by default.

### D4b — Summary adoption after ordinary retry

At `cf8f194`, `repl/provider_selection.py::compact_context` already executes a
frozen request through the canonical executor with `PrivateSummaryEvents` and
both delta channels disabled. `_CompactionWork` retains coding context, exact
cut, tree identity/mutation epoch, tree-pointer epoch, generation identity/id and
publication epoch. Existing conditional acceptance checks that full witness and
terminal/publication state under mutation-I/O then generation/session lock.
D4b1 adds the captured managed policy and reissue admission to this owner; it
does not replace that witness with a fresh context or rerun cut selection,
request budgeting, headers or hooks. The [semantic retry contract](harness-spec.md#semantic-compaction-retry-contract-d4b1)
fixes privacy, stale/manual/automatic handling and state-first persistence.

Primary D4b1 write set: `repl/provider_selection.py`, a small policy-conversion
helper in the existing settings owner only if useful to both product callers,
`repl/loop_step.py` only to adopt that helper, focused semantic-compaction tests,
and matching harness/compaction/settings/SDK/architecture docs and release notes.
No queue, public session API, transport or branch-summary ownership changes.
Acceptance adds transient recovery, policy capture, progress/usage refusal,
backoff/in-flight cancellation, stale original witness before reissue, private
output/events, one persistence after acceptance, and persistence-failure behavior.
Existing manual and automatic whole-group/within-run cut tests remain intact.

D4b3a's `repl/collaborators.py::select_with_branch_summary` retains the original
work across ordinary `provider.complete`, then prepares and conditionally accepts
the exact tree entry and coding projection. The generic command helper receives
only the owner outcome and emits diagnostics/prefill after persistence. The
[selected branch ownership contract](session-tree.md#branch-summary-ownership-contract-d4b2)
keeps capture, acceptance, loaded provenance and persistence with existing owners;
provider callbacks and projection run outside the inner session mutex. Stale work
accepts nothing; append failure leaves accepted memory and cleared branch inputs.

The committed publication seam spans `session_tree.py`,
`coding/product_session.py`, `repl/collaborators.py`, `repl/session_commands.py`
and `session_tree_commands.py`, with matching guard and behavioral tests and docs.
It adds no public SDK port or generic transaction framework.
D4b3b reuses those owners plus existing provider-turn wiring/private summary helpers;
refresh its exact handoff against D4b3a rather than automatically editing this whole
set. D4b3b acceptance must cover real callback-based abort as well as plain signals.

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
A self-produced review verdict is not evidence. Each successor receives its
implementation handoff only after its prerequisite passes the review and commit gate.

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
no longer describes an active requirement. The explicit D2a0 exception above
permits one measured reset of the two live facade size bounds, preserving the
ratchet and all other behavioral/ownership gates. No new exact shape gates.

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

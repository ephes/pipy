# Pipy planning

Status: sole active planning basis, consolidated 2026-09-07.

## Start here

This document owns direction, priorities, task IDs, dependencies, and dispatch
state. Read the selected task and its referenced contracts, not old execution
ledgers. A task card is a bounded work order; it does not override a current
runtime contract. The orchestrator alone updates this index.

**Next task:** complete independent review and commit of the D7b0 incremental
direct-bash-output contract, then dispatch D7b1 as the sole implementation slice.

Recovery is complete: D4b1 `9ccb22e` added semantic-summary retry, D4b3a `30d6d33`
guarded branch acceptance and state-first persistence, and D4b3b `8c34a9c`
activated canonical private branch retry/cancellation. Their complete Opus 5
reviews closed advisory, not CLEAN, with no unresolved material findings.
D4b3b's full runtime check passed 5,980 tests (two skipped), static checks and
unchanged interpreter snapshots; PTY smoke passed eight and docs built.
Subsequent test/docstring-only repairs passed focused and static checks.

D5a0 `c08aa72` completed the scheduled grooming after those three implementation commits.
Its full checks passed 5,980 tests (two skipped), static checks, unchanged
interpreter snapshots and docs-build. One complete Opus 5 High plan round closed
advisory with five Suggestions and no Warnings/Critical findings. Test/wording
clarifications and activation lock witnesses were added; a runtime lock-ownership
assertion was declined for this unadopted internal seam. The explicit guard-free
abort precondition and activation inventory remain mandatory. No material finding
remains; stop at diminishing returns.
Summary-safe D4b search/list and five finalized recovery summaries confirm the
commit sequence; D2b summaries confirm its public API and cancellation boundary.
Read-only Sol inspections of `30d6d33`, reconciled against `8c34a9c`, found that
RPC still owns active/reserved state and queues. Its immediate agent-end/settled
pair is earlier than controller idle and extension settlement. Moving that pair
without preserving command serialization would create a false-idle race.
Therefore D5a is split below: first prove queue transitions in the existing owner,
then review frontend activation against executable evidence. No new planning file
or concurrent runtime writer is authorized. D0–D4 remain complete. Live-provider
dogfooding, budget accuracy and semantic summary quality remain unverified.

D5a1 `bb98e61` implements the internal managed external queue seam without frontend
activation. Exact reservation tokens, claimed-only settlement, steering-first
FIFO promotion and fresh-latch abort behavior have executable race coverage.
Full checks passed 5,997 tests (two skipped), unchanged interpreter snapshots,
static checks and docs-build. Root independently passed 130 focused tests.
Post-full thread-test cleanup and the review repair passed focused/static checks;
runtime stayed unchanged. Per the user's reviewer change below, two fresh Sol
code rounds replaced Opus: R1 found one Warning in order-specific returned-snapshot
assertions; the focused test repair passed 50 queue tests independently and R2
returned CLEAN. Complete R1 coverage plus focused R2 closes the gate. These are
same-model-family reviews, not different-family evidence. D5a1 is the first
implementation after D5a0 grooming; D5a2's ownership decision is the next gate.

D5a2a `277b991` committed the reviewed submit-to-idle adoption contract after
full checks and one fresh Sol High plan review returned CLEAN. D5a2b `4c62378`
routes ordinary product submits through an atomic queue claim to true idle; the
native lifetime settles the exact token on every exit, and the facade delegates
cancellation through a once-bound native queue view. Independent focused checks
passed 160 tests; the full gate passed 6,006 tests (two skipped), static checks
and unchanged interpreter snapshots, and docs built without issues. One fresh
read-only Terra High implementation review returned CLEAN with no findings. This
is an independent context and model variant, not different-family evidence.
D5a2b is the second implementation after D5a0 grooming.

D5a3a `65c2015` composes one transport-neutral control over the queue owner,
detached immutable snapshots and transitions, a coherent one-shot startup
bridge, an exact worker claim capability, guard-free abort signaling and a
post-extension true-idle
readiness port. The seam remains private and dormant; RPC, current selectors,
ProductSession and the compatibility SDK are unchanged. Root validation passed
131 focused tests and the full 6,019-test gate (two skipped), with lint,
formatting, Mypy, unchanged interpreter snapshots, docs-build and diff hygiene
green. Fresh Terra High review R1 found three Critical defects in attachment
rollback, extension-phase failure publication and bridge-state synchronization,
plus one Warning for a potentially mixed-owner ready tuple. The bounded repair
added exact-token cleanup, complete failure publication, one control-gate regime
and owner validation. Focused R2 returned CLEAN. These are independent contexts
within the selected Terra model family, not different-family evidence. D5a3a was
the third implementation after D5a0.

The scheduled post-D5a3a grooming inspected summary-safe D5a3 records and the
actual `65c2015` RPC, controller, wiring and queue paths. It found no reason to
reorder the program: D5a3b is the sole next eligible implementation. The
committed seam is intentionally dormant, so D5a3b must connect all of its
activation points together: stream-driven `CodingSession.run` readiness and
pre-end failure cleanup, a claim-bearing worker selection at the existing
external-input priority, provider/model-tool cancellation through the bound
stable abort view, same-gate run-end output, and later true-idle publication.
The current RPC prompt channel still owns content/kind and the current wiring
strips the bridge from runtime abort paths; neither may survive as a partial
fallback. Focused architecture/doc checks passed 216 tests; the full gate passed
6,019 tests (two skipped), static checks, unchanged interpreter snapshots and
docs-build. One focused Terra High plan review returned CLEAN with no findings.
This is same-model-family independent review evidence. No material product-policy
decision remains open.

D5a3b atomically adopts the native control in RPC. The transport now retains
framing, correlation, protocol projection, wake/EOF delivery and direct-bash
state while the native queue/control owns prompt, steering, follow-up, abort,
reservation, exact claims, settlement, state snapshots and true-idle admission.
Literal product content no longer crosses line framing, startup publishes ready
only after `session_start`, and every startup or pre-end failure unblocks and
settles through the matching native capability. Root validation passed 276
focused tests and the full 6,022-test gate (two skipped), with lint, formatting,
Mypy, unchanged interpreter snapshots, docs-build and diff hygiene green. Fresh
Terra High review R1 found one Critical preparation-failure hang and one Warning
for stale ownership text. The bounded repair moved preparation inside atomic
failure publication, added deterministic no-intake coverage and corrected the
contract; focused R2 returned CLEAN. These are independent contexts within the
selected Terra model family, not different-family evidence. D5a3 and the D5a
shared-control milestone are complete; D5b is now eligible.

D5b0 fixes the RPC model/thinking-control adoption contract after a read-only
Terra High investigation at `112397a`. Root validation passed 368 focused tests
and the full 6,022-test gate (two skipped), with lint, formatting, Mypy,
unchanged interpreter snapshots, docs-build and diff hygiene green. Fresh Terra
High plan review R1 found two Warnings about custom-model projection and
model-induced thinking persistence/event ordering, plus one Suggestion to state
model-switch versus thinking-only history and usage behavior. All were resolved;
focused R2 returned CLEAN. These are independent contexts within the selected
Terra model family, not different-family evidence. D5b1 is the sole next
eligible implementation.

D5b1 routes RPC model and thinking commands through the existing
`ProviderMutationEffects` owner and removes the transport-local thinking cache.
The full ready outcome now includes the non-null configuration port; catalog,
custom-model and `enabledModels` glob projection is truthful; idle-only model and
thinking mutations update the actual next provider binding with the specified
history/usage, persistence, event and state-first failure behavior. Deterministic
tests cover both admission/preparation race orders, catalog refusal and scope,
static injected providers, owner retention/reset and a subsequent real provider
request. Root validation passed 261 focused tests and the full 6,035-test gate
(two skipped), with lint, formatting, Mypy, unchanged interpreter snapshots,
docs-build and diff hygiene green. Fresh Terra High code review R1 found one
Warning that a filtered known active catalog row was re-added; root also found
exact-only scope matching. The bounded repair distinguishes catalog-absent
custom selections, uses the canonical exact/glob cycle helpers and handles
zero/one choices. Focused R2 returned CLEAN. These are independent contexts
within the selected Terra model family, not different-family evidence. D5b is
complete; live credential/provider acceptance and daily-use switching remain
unverified.

D5c0 fixes the RPC compaction-control adoption contract after a read-only Terra
High investigation at `b3819f0`. Manual work uses an exact claimed worker
operation in the existing native-control slot; automatic work stays in request
preparation; the semantic owner retains summary, cancellation and state-first
persistence; and a precedence-aware settings-owner mutation makes successful
auto-compaction controls agree with effective policy. Persistent results require
an exact durable origin while explicitly non-persistent results use `null`.
Root validation passed 14 focused tests and the full 6,035-test gate (two
skipped), with lint, formatting, Mypy, unchanged interpreter snapshots,
docs-build and diff hygiene green. Fresh Terra High plan review R1 found two
Warnings about ephemeral result IDs and settings precedence. Both were repaired;
focused R2 returned CLEAN. These are independent contexts within the selected
Terra model family, not different-family evidence. D5c1 is the sole next slice.

D5c1 adopts manual and automatic RPC compaction through the existing native
queue, semantic-compaction and settings owners. Manual requests are idle-only
claimed worker operations with exact cancellation, persistent or ephemeral
results, private custom focus, state-first persistence projection and ordered
settlement. Automatic lifecycle starts only for a real selected summary attempt,
keeps every summary/result detail private, and reports accepted persistence
failure truthfully before preserving and re-raising the original exception. The
transport-local enabled flag is gone; exact-boolean mutation and state projection use effective
settings precedence. Root validation passed 673 focused tests and the full
6,062-test gate (two skipped), with lint, formatting, Mypy, unchanged interpreter
snapshots, eight PTY smoke tests, docs-build and diff hygiene green. Terra High
code review R1 found one Critical privacy leak and two lifecycle Warnings; the
bounded repairs also closed an orchestrator-found output-under-owner-lock path.
Focused R2 returned CLEAN. This is same-model-family independent review evidence.
D5c is complete; live-provider summary quality remains unverified. D5d is the
sole next eligible task.

D5d0 fixes the retry-control adoption contract after a read-only Terra High
investigation at `ca21328`. D4 already owns ordinary request retry execution,
event projection, cancellation and guarded reissue; at that inspected baseline,
RPC's `set_auto_retry` only coerced a transport-local flag and `abort_retry` was
an unconditional no-op. The
selected contract removes that flag, mutates effective `retry.enabled` with the
same precedence discipline as compaction, and adds one private once-bound port
whose exact capability exists only during an ordinary canonical retry phase.
Result fixation and abort linearize so a late command cannot cancel the
surrounding provider turn or later work. Summary retries stay private. D5d1 is
the sole next implementation slice. Root validation passed 219 focused checks
and the full 6,062-test gate (two skipped), with lint, formatting, Mypy,
unchanged interpreter snapshots, docs-build and diff hygiene green. Fresh Terra
High plan review R1 found one Warning for the stale user-facing RPC overview;
the bounded repair made current versus selected behavior explicit and focused R2
returned CLEAN. This is same-model-family independent review evidence.

D5d1 removes the transport-local retry flag and adopts both controls through the
existing owners. The settings manager now performs exact-boolean,
precedence-aware `retry.enabled` mutation. One required readiness port carries a
canonical ordinary-retry capability whose activation precedes the start event
and whose retirement precedes the end event. An accepted abort is delivered to
the exact run latch before retirement can finish, so it cannot escape into a
promoted or later run; late and idle commands are no-ops. Private summary retries
never receive the capability, and `get_state` remains unchanged. D5d is complete
in this chunk pending the review gate. Root found and repaired an optional-ready
port and an abort-delivery race that could otherwise signal a promoted run, then
added sanitized persistence failure and end-to-end RPC successor coverage. The
expanded focused gate passed 628 tests and the full gate passed 6,070 tests (two
skipped), with lint, formatting, Mypy, unchanged interpreter snapshots,
docs-build and diff hygiene green. Fresh Terra High code review R1 found one
Warning in the user-facing cancellation wording. Root also found that routing
through generic abort would clear queued steering. The bounded repair documented
normal cancelled-run settlement and added the signal-only exact-claim owner path
with queue-preservation coverage; focused R2 returned CLEAN. This is
same-model-family independent review evidence.

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
4. Freeze repository writes. Per the user's 2026-09-08 credit-saving direction,
   use a fresh read-only **GPT-5.6 Sol** reviewer separate from the implementer,
   through the installed review-cycle workflow. Review all changed code, tests,
   documentation and selected contracts; record inspected scope and omissions.
   Reviewers work directly without delegation. This explicitly replaces the prior
   Opus gate with a same-model-family review, not different-family evidence.
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
The Sol reviewer selection above is explicitly user-authorized; earlier Opus
verdicts remain historical evidence and do not review future changes.

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
| D4b3a | Complete `30d6d33`; full checks and one adjudicated advisory Opus code round; D4b2 | Guarded branch-summary capture/acceptance and narrow tree/product acceptance-persistence seam; existing ordinary provider behavior | Reject stale generated text without tree/history/editor mutation; tree/leaf and coding projection accept coherently, branch-bound inputs clear, durable append follows under existing outer ordering; append failure retains accepted state |
| D4b3b | Complete `8c34a9c`; full checks and one adjudicated advisory Opus code round; D4b3a, D4b1 | Canonical private branch execution, retry and cancellation through the committed branch owner | Frozen request/policy/witness, prepared and non-capable provider paths, no private event/usage leak, cancellation in provider/backoff phases and correct manual input settlement; no repeat acceptance or persistence |
| D5a0 | Complete `c08aa72`; full checks and one advisory Opus plan round; D4b3b, D2b | Scheduled grooming and bounded queue mechanism contract in `docs/sdk.md`; backlog only otherwise | Independent review and commit before D5a1; later activation decisions explicitly owned |
| D5a1 | Complete `bb98e61`; full checks and focused Sol follow-up CLEAN; D5a0 | Guarded external admission/reservation/abort mechanism in existing `coding/input_queue.py`, focused queue tests and matching docs/release note | Dedicated lanes, exact token claim/settle, one-per-boundary promotion, coherent snapshots, fresh abort latches and callback-outside-guard race tests; no production adoption yet |
| D5a2a | Complete `277b991`; full checks and first Sol plan review CLEAN; D5a1 | Bounded submit-to-idle queue/cancellation ownership contract in SDK docs | Independent review and commit before D5a2b; public admission/RPC event timing explicitly deferred |
| D5a2b | Complete `4c62378`; full checks and first Terra code review CLEAN; D5a2a | Route ordinary `ProductSession.submit/cancel` through existing queue and native lifetime, removing the facade's independent active-latch writer | One token/latch through true idle and extension re-poll, atomic idle-only begin, exact cleanup on every exit, native signal view, unchanged public semantics and import boundaries |
| D5a2 | Complete at `4c62378`; D5a2b complete | Native/facade adoption milestone, not a separate implementation dispatch | Real product submits use managed ownership; no new public concurrent admission or premature RPC settlement hook |
| D5a3a0 | Complete `448f2a0`; full checks and focused Terra follow-up CLEAN; D5a2 | Reviewed internal control/readiness and atomic RPC migration contract in SDK/RPC/architecture docs | Independent review and commit before D5a3a; no runtime change or public SDK promise |
| D5a3a | Complete `65c2015`; full checks and focused Terra follow-up CLEAN; D5a3a0 | Transport-neutral native managed-control seam and immutable transitions without RPC activation | Terra R1 3 Critical/1 Warning repaired; one queue owner, exact claims, coherent readiness, guard-free signaling; existing ProductSession behavior unchanged |
| D5a3b | Complete `112397a`; full checks and focused Terra follow-up CLEAN; D5a3a `65c2015` | Atomically migrate RPC prompt/steer/follow-up/abort/state and end/settled projection; delete the old RPC writers/readers together | 276 focused and 6,022 full tests; stream-driven startup/failure readiness, wake/EOF-only transport, exact settlement, post-extension true idle, framing/correlation, typed delivery and EOF drain preserved |
| D5a3 | Complete `112397a`; D5a3a–D5a3b complete | Shared-control/RPC adoption milestone, not a separate dispatch | RPC retains transport/projection only; no dual queue, latch, reservation or active-state authority |
| D5a | Complete `112397a`; D5a1–D5a3 complete | Completion milestone for shared session queue and RPC adoption, not a separate implementation dispatch | No dual authorities or unadopted migration seams; all inventory and equivalence gates satisfied |
| D5b0 | Complete `386ab33`; full checks and focused Terra follow-up CLEAN; D5a `112397a` | Fix the private RPC-to-provider-mutation handoff, idle commit ordering, catalog/scoped-cycle projection and injected-provider fallback | Reviewed implementation contract; no runtime change |
| D5b1 | Complete `b3819f0`; full checks and focused Terra follow-up CLEAN; D5b0 `386ab33` | Route RPC model/thinking commands through the existing provider-mutation owner and delete RPC-local thinking authority | 261 focused and 6,035 full tests; truthful coherent snapshots, real next-request provider/level, catalog/trust/tool-capability filtering, durable thinking entry, correlated results |
| D5b | Complete `b3819f0`; D5b0–D5b1 complete | RPC model/thinking controls milestone, not a separate dispatch | Existing provider construction, refresh/trust, settings and coding-binding owners remain authoritative |
| D5c0 | Complete `67eb79c`; full checks and focused Terra follow-up CLEAN; D5a, D1b | Reviewed worker-operation, event/result, settings and state-first RPC compaction contract in SDK/RPC/architecture/harness docs | 14 focused and 6,035 full tests; one queue/abort/summary/settings owner; exact reason and output ordering |
| D5c1 | Complete `ca21328`; full checks and focused Terra follow-up CLEAN; D5c0 `67eb79c` | Adopt manual and automatic RPC compaction controls through the existing native queue, semantic owner and precedence-aware settings owner | 673 focused and 6,062 full tests; real persistent/ephemeral result and effective policy; custom focus; cancellation, persistence, privacy, override, event/correlation, state and admission races covered |
| D5c | Complete `ca21328`; D5c1 complete | RPC compaction-control milestone, not a separate dispatch | No transport-owned compaction policy, queue, latch, enabled flag, summary or durable state |
| D5d0 | Complete `e1060da`; full checks and focused Terra follow-up CLEAN; D5a, D4a3 | Reviewed settings/phase ownership and race contract in SDK/RPC overview/architecture/harness docs | 219 focused and 6,062 full tests; exact-bool effective policy; exact ordinary-retry capability; late abort no-op; private summary retries excluded |
| D5d1 | Complete `0bfd48c`; full checks and focused Terra follow-up CLEAN; D5d0 `e1060da` | Adopt RPC retry enable/abort through existing settings and canonical retry owners | 628 focused and 6,070 full tests; current request capture unchanged; abort/backoff/reissue/result-fixation/settlement races; no stale capability or private-summary exposure |
| D5d | Complete `0bfd48c`; D5d1 complete | RPC retry-control milestone, not a separate dispatch | No transport-owned policy, activity flag, latch, attempt allowance, or summary control |
| D6a0 | Complete `5c181dc`; full checks and focused Terra follow-up CLEAN; D2b, D5a | Review the exact-path public reopen, workspace authority and unchanged close/lifecycle contract in `docs/sdk.md`; backlog only otherwise | Independent review and commit before D6a1; no runtime or public behavior change |
| D6a1 | Complete `6e38076`; full checks and focused Terra follow-up CLEAN; D6a0 `5c181dc` | Add the explicit public product-session reopen factory, strict tree loading and canonical-path lifetime lease by delegating to existing owners | 69 focused and 6,082 full tests; equivalent reconstructed context; one in-process writer per durable file; same-file append; lifecycle once; invalid input refuses before composition; no late writes after retirement |
| D6a | Complete `6e38076`; D6a1 complete | Session resume/close API milestone, not a separate dispatch | Supported reopen plus existing idempotent close; no in-place replacement |
| D6b | D6a | Fork/clone/session replacement API | Existing tree and extension veto contracts; rebind observations once; caller-visible outcomes and persistence agree |
| D6c | D6b | Incremental frontend adoption and deliberate compatibility SDK retirement, one entry point per chunk | Same session owner across SDK/modes; executable embedding example; update docs and remove replaced surfaces without aliases |
| D7a0 | Complete `3327a3a`; full checks and focused Terra follow-up CLEAN; D0, D5a `112397a` | Review direct RPC bash operation identity, process lifetime, result and lock contract in automation/RPC/architecture docs | Independent review and commit before runtime work; current behavior remains explicit until D7a1 |
| D7a1 | Complete in this chunk; full checks and focused Terra follow-up CLEAN; D7a0 `3327a3a` | Add cancellable direct RPC bash through `command_sandbox.py`, current RPC direct-bash owners and focused tests | Abort all snapshotted operations; process-group termination/reap; timeout differs from explicit abort; one exact correlated terminal response; preserve sandbox policy |
| D7a | Complete in this chunk; D7a1 complete | Direct RPC bash cancellation milestone, not a separate dispatch | No uncancellable direct child, stale operation identity or timeout-as-cancel projection remains |
| D7b0 | Complete in this chunk; full checks and two Terra plan rounds, final Warning repaired and locally adjudicated; D7a `d900ca5` | Review correlated incremental direct-bash output, privacy, bounds and lifecycle contract in automation/RPC/architecture docs | Independent review and commit before runtime work; current terminal-only behavior remains explicit until D7b1 |
| D7b1 | D7b0 | Add line-gated, correlated direct-bash update callbacks through the existing sandbox and RPC writer | Pi-shaped optional-ID deltas before one terminal response; bounded per-stream updates; no raw partial line, secret fragment, late callback, or terminal-result change |
| D7b | D7b1 | Correlated incremental RPC bash output milestone, not a separate dispatch | No updates after completion; bounded output, JSONL purity and EOF disposal |
| D8 | D0; refresh embedding after D2b/D6c | Reuse existing extension conformance example/tests; audit provider replay before selecting changes | `docs/examples/extensions/pipy-extension-conformance.py` already covers tools/commands/events: prove missing behavior before adding examples; same-provider resume/cross-provider history evidence before schema work |

D1a/D1b delivered semantic continuity; D2a/D2b established the minimal reusable
session API. D4 recovery is complete at `8c34a9c`, D5a queue/RPC shared-control
adoption is complete at `112397a`, and D5b model/thinking adoption is complete
at `b3819f0`. D5c compaction-control adoption is complete at `ca21328`; D5d
retry-control adoption is complete at `0bfd48c`; D6a public reopen is complete
at `6e38076`.

The scheduled grooming after D5b1/D5c1/D5d1 inspected summary-safe archive
search/list results and current D6a/D7a source/test evidence at `0bfd48c`.
No matching prior decision record changes the queue. The supported SDK already
owns a persistent product lifetime and accepts an injected opened tree, but
public resume remains an undocumented escape hatch. Direct RPC bash cancellation
is also a real gap, although it affects only the restricted direct-command RPC
surface. D6a therefore remains first and is split into the reviewed D6a0
contract and narrow D6a1 implementation below. D7a follows D6a unless an
observed controller workflow makes it the immediate blocker. No simultaneous
runtime writers: the D1a–D4b slices share session/provider integration, and the
D5/D7 families share
`rpc.py`. Read-only investigation of later tasks can proceed during any of them.

### D6a — Public product-session reopen

At `0bfd48c`, `create_product_session(..., tree=NativeSessionTree)` already
rebuilds an injected tree's active coding context through the existing
composition and product-session owners. `NativeSessionTree.open(path)` already
parses the durable JSONL and reconstructs its active leaf, while
`ProductSession.close()` already provides idempotent construction-thread
retirement and a detached snapshot remains readable afterwards. The missing
behavior is a supported, bounded reopen entry point with an explicit workspace
authority check; D6a must not create another lifecycle or replace context in a
live facade.

D6a0 fixes that API contract in
[the SDK specification](sdk.md#d6a-public-product-session-reopen-contract).
D6a1 adds one exported keyword-only `open_product_session(...)` factory. It
accepts an exact `workspace: Path`, exact `session_path: Path`, explicit
tool-capable provider, and the same optional tools/settings/resources/observer/
diagnostic/context-file inputs as creation. It opens exactly that existing
native session file, requires its stored header cwd to be absolute and resolve
to the supplied workspace, and then delegates to the existing create-with-tree
path. It uses a strict opt-in tree load that rejects malformed JSON, non-object
records, invalid or unknown entries, duplicate headers/IDs and invalid ancestry
instead of silently skipping them. Missing, malformed, or foreign-workspace
input refuses before session composition, extension activation, provider work,
or durable mutation; the existing permissive internal/CLI loader remains
unchanged by default.

A reopened facade is a fresh native runtime lifetime over the same durable
conversation. It reconstructs the active leaf's provider-visible messages,
including compaction summary and retained-user anchors, while the explicitly
supplied provider remains authoritative. It appends later accepted product messages to
the same file once, emits current lifecycle events once without replaying
historical events, and uses the existing state, queue, controller, generation,
tree-lock, persistence and cancellation owners. Existing close semantics remain:
owner-thread and non-reentrant, idempotent while idle, snapshot-readable after
close, later submit refused, and delayed cancellation from the retired lifetime
unable to affect another reopened lifetime.

Before opening or composing a persistent tree, the public product facade
canonicalizes the existing file path and atomically claims one private
process-local lease for that path. The lease also applies when
`create_product_session(..., tree=...)` receives a persistent tree, so callers
cannot bypass it by mixing the two public factories or by using a symlink alias.
It is held through the entire native lifetime and released exactly once after
startup failure, terminal retirement, explicit close, or context exit. A second
active public lifetime for the same canonical file refuses before composition
or mutation. Direct internal tree use and coordination with another OS process
remain explicit caller responsibilities; D6a does not claim a cross-process
file-locking protocol.

D6a1 may write `src/pipy_harness/product_api.py`,
`src/pipy_harness/sdk.py`, `src/pipy_harness/native/session_tree.py`,
`tests/test_product_session_api.py`, `tests/test_native_session_tree_core.py`,
and an existing SDK/export contract test only when needed. Matching behavior documentation is
`docs/sdk.md`, `docs/session-tree.md`, `docs/harness-spec.md` and
`CHANGELOG.md`; `docs/backlog.md` remains orchestrator-owned. Acceptance must
cover exact reconstructed provider context, same-file single append, one
start/shutdown per lifetime, no historical event replay, strict malformed/
unknown/duplicate/ancestry refusal, missing and workspace-mismatched refusal
before effects, same-path and symlink-alias exclusion across both public
factories, lease release on every exit, close idempotence, post-close snapshot/
refusal, and stale-cancel isolation. Preserve explicit tree injection, privacy,
`run_native`, RPC, terminal commands and all current import boundaries.

Session-id/recency resolution, cross-project search, in-place resume or
replacement, fork/clone, concurrent close, public queue/model/compaction
controls, archive-backed resume, and compatibility SDK retirement remain D6b+
or D6c work. D6a introduces no alias or deprecation shim.

D6a1 `6e38076` implements that reviewed boundary in the existing product facade
and tree owners. Strict loading is opt-in, while permissive CLI/internal open behavior is
unchanged. The facade claims one resolved target before strict load or
composition, normalizes persistent injected trees to the same target, and
releases the exact lease on every lifetime exit. Focused tests cover same-path
and symlink-alias races, retarget resistance, startup/terminal release,
stale-cancel isolation, strict record/header/compaction refusal, current-only
lifecycle events, exact durable compaction reconstruction and single append.
Root's focused integration gate passed 69 tests. The full gate passed 6,082
tests with two skipped, plus lint, formatting, Mypy, docs-build and diff checks;
the virtualenv interpreter links and configuration stayed unchanged. Independent
Terra review R1 found one Warning: strict loading type-checked label and branch-
summary references without enforcing the writer's relationships. The bounded
repair requires an already accepted label target and an exact branch-summary
attachment reference; focused R2 returned CLEAN.

### D7a — Direct RPC bash cancellation

At `6e38076`, direct RPC `bash` registers only a count and worker thread before
calling `command_sandbox.run_command`; `abort_bash` errors while any such worker
is active, and sandbox timeout is projected as cancellation. Summary-safe
searches for direct RPC bash cancellation and `abort_bash` found no prior
decision record. Pi reference commit `aa23e78` keeps one abort controller for
each concurrent bash execution and aborts every controller active at the
command boundary. Pipy must preserve its stricter direct-command sandbox rather
than route this surface through the model's real-shell `BashTool`.

D7a was split into the reviewed D7a0 contract, now reflected in
[the shipped automation RPC specification](automation-rpc.md#d7a-shipped-contract),
and the D7a1 implementation. Each accepted direct bash gets a fresh cancellation
event and exact private operation identity registered before its worker can
spawn. `abort_bash` marks and snapshots all active operations under the existing
RPC lock, signals their events after releasing it, and returns correlated
success even when idle.
The sandbox keeps `shell=False`, the resolved allowlist and path/cwd confinement,
scrubbed environment, separate stream capture, output bounds and redaction.
Explicit abort and timeout both terminate the complete process group, drain
available output and reap the child; only explicit abort returns
`cancelled: true`, and both return a null exit code.

Terminal outcome fixation retires the exact operation under the RPC lock before
its one correlated bash response is written outside the lock. An abort mark that
wins that ordering fixes explicit cancellation even when timeout races; a timeout
fixed first cannot be reclassified by a later abort. An abort snapshot cannot
reach a fixed predecessor or a later fresh operation. No RPC lock spans spawn,
wait, kill, drain, reap, join, callbacks, or JSONL output; the JSONL writer
remains the serialization owner. EOF retains bounded worker joining.
D7a1 may write `src/pipy_harness/native/command_sandbox.py`,
`src/pipy_harness/native/automation/rpc.py`,
`tests/test_command_sandbox.py`, `tests/test_native_automation_rpc.py`, the
three D7a0 contract documents and `CHANGELOG.md`; the orchestrator owns this
backlog.

Acceptance covers a real active process-group abort, idle success, a pre-spawn
abort with no child, timeout/abort distinction, all-current snapshot behavior,
completion/abort and successor races, a barrier-controlled simultaneous timeout/
abort test for both lock orderings, one terminal response, EOF cleanup, and
unchanged allowlist/confinement/environment/redaction/output bounds. D7b owns
incremental output. D7a adds no history persistence, bash IDs, policy expansion,
model-tool change, public session API, event bus, or cross-process owner.

D7a0 full validation passed 6,082 tests with two skipped, plus lint, formatting,
Mypy, docs-build and diff checks; virtualenv interpreter links and configuration
stayed unchanged. Independent Terra plan review R1 found one Warning because the
timeout/abort race lacked a deterministic winner. The repair linearizes abort
marking and terminal fixation under the RPC lock and requires both lock orderings
in a barrier-controlled test; focused R2 returned CLEAN.

D7a1 implements the reviewed boundary with one exact operation object and fresh
cancellation event per accepted command. The sandbox launches the allowlisted
argv in a new process group, drains both streams, and independently escalates to
group SIGKILL after the grace period even when the direct child exits first.
Explicit abort and timeout retain distinct result projection, while abort marking
and terminal fixation use the RPC lock to determine the race winner. Captured
output still passes through redaction before the returned-output cap.

Root's focused integration gate passed 141 tests. The full gate passed 6,096
tests with two skipped, plus lint, formatting, Mypy, docs-build and diff checks;
the virtualenv interpreter links and configuration stayed unchanged. Independent
Terra review R1 found one Critical descendant-lifetime defect: escalation to
SIGKILL depended on the direct parent still running. The bounded repair made the
kill deadline independent and added explicit-abort and timeout regressions for a
parent-exited, SIGTERM-ignoring descendant. Root also caught and repaired a
redaction-before-truncation regression with decisive-suffix and split-read tests.
Focused R2 reviewed both repairs and returned CLEAN. The final test-only Mypy
correction used the test module's direct `CommandPolicy` import and changed no
runtime behavior, so the clean second round closes the review gate without a
third round.

The chunk-boundary dependency check leaves both D6b and D7b technically eligible.
D7b remains next because it extends the just-validated direct-bash operation and
JSONL response owners; inspecting and fixing that bounded contract before another
RPC edit reduces ownership churn. D6b remains deferred behind that adjacent RPC
slice, with no prerequisite or acceptance change.

### D7b — Correlated incremental direct-bash output

Read-only Terra investigations at `d900ca5` found that the sandbox owns selector
draining, separate raw stdout/stderr capture, process cleanup and terminal
redaction, while RPC owns the exact operation identity and the sole serialized
JSONL writer. Pi reference `aa23e78` emits append-only
`bash_execution_update` deltas with the original optional request ID and no
stream or sequence field. Copying its raw-chunk callback would violate pipy's
stricter privacy boundary: the existing secret classifier may need a suffix from
a later read before it can classify the prefix.

D7b0 fixes the bounded implementation contract in
[the automation RPC specification](automation-rpc.md#d7b-incremental-direct-bash-output-contract).
D7b1 adds an optional direct-RPC callback at the existing sandbox drain boundary.
Each source stream buffers raw bytes until a complete LF-delimited line or safe
EOF fragment can be classified, decoded and redacted. A pending unterminated line
and cumulative updates use the existing per-stream output limit; all text and
markers consume that budget, while an overlong line emits one opaque terminal
suppression marker without exposing its prefix. Callback admission into a bounded
private per-operation relay never blocks process cleanup; a separate emitter uses
the existing writer, drains before operation fixation, and orders every emitted
Pi-shaped delta before that exact operation's terminal response. Terminal output
keeps its current independent, authoritative stdout-then-stderr shaping.

D7b1 may write `src/pipy_harness/native/command_sandbox.py`,
`src/pipy_harness/native/automation/rpc.py`, `tests/test_command_sandbox.py`,
`tests/test_native_automation_rpc.py`, and `tests/test_native_automation_jsonl.py`
only if the shared writer needs an additional ordering witness. Matching behavior
documentation is `docs/automation-rpc.md`, `docs/rpc.md`, `docs/architecture.md`
and `CHANGELOG.md`; the orchestrator owns this backlog. It adds no public bash
operation ID, stream/sequence schema, history persistence, model-tool behavior,
event bus, async rewrite, sandbox-policy expansion, or terminal result change.

Acceptance must cover paced real output and missing-ID updates, concurrent
correlation and parseable JSONL, split-read secrets and UTF-8, secret and
overlong-line markers, per-stream budgets, stdout/stderr readiness versus
terminal authority, explicit abort and timeout, both abort/fixation orderings,
EOF joining, late/successor callback isolation, repeated secret lines under a
tiny budget, and process termination/reaping while the JSONL writer is blocked.
D7b depends only on completed D7a. D6b remains eligible afterward and its
contract is unchanged.

Independent Terra plan review R1 found one Critical issue: a synchronous update
write could block the sandbox's timeout/abort cleanup, plus Warnings for marker
accounting and line/EOF finalization. The repair introduced the bounded
nonblocking relay, charged all ordinary/redacted output and markers, and pinned
LF, CR, exact-stream EOF and termination-drain behavior. Focused R2 found one
remaining Warning because the fixed overlong-line marker can exceed a one-byte
policy budget. The final repair makes either exhausting marker the sole bounded
exception and caps each stream at its configured bytes plus at most that one
marker. The two-round docs cap is reached; the final disposition is advisory,
not CLEAN, with no unresolved material finding and no reason for another prose
round.

### D5a — Refreshed queue ownership basis

D5a0 selects only the internal mechanism contract in
[the SDK specification](sdk.md#d5a1-external-admission-mechanism).
D5a1 may write `src/pipy_harness/native/coding/input_queue.py`, its focused
queue test modules, that SDK section and `CHANGELOG.md`; the orchestrator updates
this index. It must not change `product_api.py`, wiring, controller, RPC or
canonical execution. Exact immutable value/helper names are implementation choices.

D5a2 refreshed these source/test witnesses before selecting activation. The RPC
bullets below describe that historical pre-D5a3 baseline:

- `product_api.py::ProductSession` preserves construction-thread entry and
  failure rules while delegating one submit-to-idle claim and cross-thread cancel
  to the native queue owner. The removed facade bridge is no longer a state writer.
- `CodingInputQueue` uses the existing coding-effects RLock for selection and
  all lanes. Extension clearing affects only extension inputs. Existing external
  ports may be polled by the active agent loop; new pending external admissions
  must not become in-turn injection accidentally.
- `NativeRpcServer` guards active/reserved state, both pending queues, admission,
  abort clearing, reservation, state snapshots and EOF/drain with its state
  lock. Reservation pops one item and marks active atomically; reserved content
  is excluded from pending counts. Abort discards steering but preserves follow-up.
- RPC `emit` settles/reserves and writes `agent_end` plus optional `agent_settled`
  under that RPC state lock (other writes use the JSONL writer). Command admission cannot interleave the pair.
  `AgentRunCompleted` projects synchronously before coding-run witness release
  and before extension `agent_end` hooks. Controller idle later emits extension
  settlement and re-polls. These are distinct boundaries; a detached idle
  snapshot cannot authorize a later public settled line after new admission.
- Preserve typed slash/shell/newline queue content, current idle prompt parsing,
  pending/state reporting, callback failure and preflight refusal cleanup,
  accepted-abort replay and fresh-next-run behavior. Preserve existing bounded
  EOF drain and once-only close; D6 owns broader lifecycle changes.

D5a3 completed that activation with one reader/writer migration, the reviewed
outer-gate ordering, wake-only transport and exact retirement of every accepted
operation. No transport-owned state writer or duplicate queue remains. D5b–d and
D6 are now unblocked by the D5a milestone. D7 stays in its existing priority
position; no evidence warrants reordering it. The next scheduled grooming is
after three further implementation commits, or earlier if implementation evidence
changes ownership.

### D5a2 — Ordinary product operation adoption

D5a2a passed full checks (5,997 tests, two skipped), unchanged interpreter
snapshots, static checks and docs-build. One fresh read-only Sol High plan review
returned CLEAN with no findings over the complete two-document diff and supplied
source/test/contract witnesses. Raw repository files outside that frozen bundle
were not reviewed; implementation review must verify complete callback-registration
and failure-retirement call contexts. No second plan round is warranted.

D5a2b implements that contract in the expected owners. The queue now provides
one atomic idle-only begin-and-claim operation and a stable once-bound abort
view; the existing native lifetime drives the claimed literal seed through true
idle and settles the exact token on idle, terminal and exceptional exits. The
facade's active-latch writer is removed. Focused tests cover exact ownership
through a settled-hook continuation, cancellation during that continuation,
busy refusal without mutation, binding/guard behavior and cleanup failures.
Independent root validation passed 160 focused tests and the full 6,006-test
gate with two skips, static checks, unchanged interpreter snapshots and a clean
documentation build. One fresh read-only Terra High review returned CLEAN with
zero findings over all eleven changed files and the coupled cancellation and
lifecycle paths. No follow-up round is warranted.

Read-only Sol evidence from D5a1's frozen delta, now `bb98e61`, selected the
[SDK adoption contract](sdk.md#d5a2b-product-operation-adoption).
`ProductSession.submit` already promises literal input through controller true
idle, including extension settled-hook re-poll. It is not one canonical provider
run. `AgentRunCompleted` occurs before canonical queue polling, coding history
mirror and witness release, so using it to retire this facade operation would
shorten current cancellation semantics. D5a2b must preserve those semantics.

The smallest useful activation replaces the facade's separate latch writer with
the queue's claimed reservation and fresh latch. The existing prepared/controller
lifetime drives the literal seed and settles that exact operation on every exit.
A stable native signal view binds to the queue once; it observes the claimed
latch rather than maintaining another current-latch slot. Bind after successful
startup and before exposing the facade. New public queue controls, pending-lane
execution, RPC readiness/EOF and end/settled event projection stay D5a3 decisions.
D5a3 must not reuse submit-to-idle retirement as if it were RPC's per-run boundary.

Actual D5a2b writes: `native/coding/input_queue.py` (idle-only atomic begin and
native observation view), `native/coding/session_controller.py` (existing lifetime
operation), `native/repl/wiring.py` (prepared delegation/binding), `product_api.py`
(facade adoption), their focused queue/lifetime/product tests, SDK/architecture
ownership text and `CHANGELOG.md`. All source paths are under `src/pipy_harness/`.
No changes to canonical execution, RPC, adapter construction options, selection
priority, extension clearing or a new lifecycle owner are authorized. Preserve
headless command-discovery imports: D5a1's cancellation import is deliberately
lazy, as pinned by both fresh-process coding-command import tests.

Root inspected the signal reads in budget refusal and compaction refusal: they
sample outside coding guards with context checks on either side. Provider and
model-tool cancellation share the existing external waiter. Activation must keep
that call placement and audit signal callback registration as well as reads;
never acquire the outer queue guard while retaining an inner session guard.
Existing ordered persistence is not redesigned. This is contract refinement at
a named ownership boundary, not a scheduled full grooming or a priority change.

### D5a3 — Shared control and RPC migration

Two independent read-only Terra High investigations at `4c62378` agree that the
native queue must become the sole authority for active/reserved state, pending
steering/follow-up, exact claims, promotion, cancellation and pending counts.
RPC retains JSONL framing/correlation, projection ordering, its input wake/EOF
transport and separately owned direct-bash state. Both reports reject a partial
reader/writer migration: moving only state, abort or settlement would make the
two authorities disagree and reopen false-idle or stale-cancel races.

Full checks passed 6,006 tests (two skipped), static checks, unchanged
interpreter snapshots and docs-build. The first read-only Terra High plan review
reported one Warning for the missing concrete claim/startup bridge and one
Suggestion for successor projection order. The bounded repair below defines both;
a focused second review returned CLEAN with no new findings. The reviewed gate
is closed at two rounds.

The reviewed contract is split into D5a3a and D5a3b. D5a3a introduces one
internal transport-neutral control over `CodingInputQueue`; it is not a new
public SDK surface. Its outer admission/publication gate carries no product
payload or active flag. All control mutations enter that gate before the existing
coding-effects RLock and return detached immutable state/transition values. The
queue lock is released before signaling latches, registering/invoking callbacks,
waking a worker or performing output. D5a3a defines the exact claimed-run handoff
and controller true-idle readiness port, but does not activate RPC or expose
managed lanes to current selectors.

D5a3b is one atomic RPC ownership migration. The worker readiness handshake must
publish the bound native control, abort view and readiness port before command
intake. The transport channel becomes wake/EOF-only; accepted full content and
delivery kind remain queue-owned, while an exact claim handle accompanies the
worker's selected run until its boundary settlement. `AgentRunCompleted`
settlement and the `agent_end` write share the outer gate. A promoted successor
suppresses public settlement and is woken only after the end record. Protocol
`agent_settled` is emitted from the later controller readiness callback, after
extension settlement/outbox re-poll, and rechecks idle under the same gate. A
concurrent admission therefore either precedes and suppresses it or follows the
already serialized idle record. No output or wake occurs under the coding-effects
lock.

D5a3a's allowed runtime writes are `native/coding/input_queue.py`,
`native/coding/session_controller.py` and `native/repl/wiring.py`, with their
focused queue/controller/product tests and matching SDK/architecture/changelog
text; `docs/backlog.md` stays orchestrator-owned. It leaves
`ProductSession.submit/cancel`, RPC, selectors, `run_native`, extension lanes,
model/thinking, compaction, retry, session replacement and bash unchanged.
D5a3a's private one-shot bridge travels through the existing pre-composition
abort/input side, publishes ready or failed exactly once, and unblocks waiters on
either outcome. It holds at most one immutable worker claim capability. Selection
attaches that exact claim to the run; `agent_end` consumes it once, while an
earlier run failure settles it through cleanup. Missing, duplicate,
cross-worker or mismatched use fails closed rather than settling current state.
D5a3b's exact runtime write set is `native/automation/rpc.py`,
`native/coding/input_queue.py`, `native/coding/session_controller.py`,
`native/coding/session.py`, `native/repl/wiring.py`, `native/repl/loop_step.py`
and, only if the claim-bearing value must cross the frozen loop record,
`native/repl/loop_scope.py`. Queue edits are limited to a claim-bearing selection
and the narrow same-gate publication operation; session/controller/wiring edits
are limited to stream-driven ready/failure publication, exact pre-end cleanup,
stable abort propagation and true-idle binding. Focused tests are
`tests/test_native_automation_rpc.py`,
`tests/test_native_coding_external_admission.py`,
`tests/test_native_coding_session_controller.py`,
`tests/test_native_coding_session.py` and the existing product API tests when a
shared lifetime path changes. Matching docs are `docs/automation-rpc.md`,
`docs/rpc.md`, `docs/sdk.md`, `docs/architecture.md`, `docs/harness-spec.md` and
`CHANGELOG.md`; `docs/backlog.md` stays orchestrator-owned. If implementation
cannot delete `_turn_active`, `_abort`, `_steering`, `_follow_up`,
`_QueuedPrompt`, `_PromptChannelItem`, payload-bearing channel methods and every
reservation reader/writer together, stop and revise this contract instead of
landing a temporary second authority. RPC's `_lock` may remain only for its
separately owned direct-bash worker inventory.

The two required activation connections are fixed by evidence, not open design
questions. First, RPC's stream-driven path must publish the matching control,
abort view and readiness port after `session_start`, before the reader admits a
command; all startup failures unblock the reader, and a claimed run that fails
before canonical `AgentRunCompleted` settles its exact bridge claim before the
existing lifetime retirement completes. Second, worker selection must claim the
queue-owned reservation under the outer gate at the current external-input
priority and carry only that immutable capability until synchronous `agent_end`.
The control provides one narrow operation that settles that claim, invokes the
`agent_end` and post-promotion `queue_update` writers while the outer gate remains
held and the coding-effects lock is released, then returns the successor decision
so waking happens after the gate is released. The wake channel carries only wake
and EOF signals; it never stores or reconstructs product content.

Acceptance must deterministically cover both command-admission versus run-end
interleavings; claim-before-provider execution; steering-first FIFO promotion;
literal slash, shell, newline and whitespace identity; abort before claim,
during provider/model-tool work and after old-token settlement; truthful active
and pending snapshots; extension settled-hook continuation without an early
protocol idle line; readiness versus new-admission serialization; startup
failure/binding; and the existing bounded EOF drain and worker/bash joins.
Existing response correlation, LF-only JSONL, one writer, prompt success before
its run events and queue-update visibility remain unchanged. A promoted successor
keeps the existing `agent_end → queue_update(post-promotion) → wake/next
agent_start` order. D5b–d, D6 and D7 retain their current dependencies and scope.

### D5b — RPC model and thinking control adoption

A read-only Terra High investigation at `112397a` found that current RPC model
commands do not reach the live coding binding: `set_model` only acknowledges the
already selected model, `cycle_model` always returns `null`, and
`get_available_models` fabricates a singleton. Thinking commands keep an
RPC-local value and directly assign provider state, but the running coding
session continues to use the provider captured in `CodingSessionState`; they also
bypass model-supported levels and the durable thinking entry. The sole mutation
owner remains `repl/provider_selection.py::ProviderMutationEffects`, with
`NativeReplProviderState` and `CodingSessionState` as its atomically published
values. D5b must reuse that owner rather than teach RPC to construct providers,
interpret catalog/auth/trust policy or persist defaults.

D5b0 fixes the implementation contract in `docs/sdk.md`,
`docs/automation-rpc.md` and `docs/architecture.md`. D5b1 may then write
`native/automation/rpc.py`, `native/coding/input_queue.py`,
`native/coding/session_controller.py`, `native/repl/wiring.py`,
`native/repl/provider_selection.py` and `native/repl_state.py`. A listed file is
used only when the selected behavior needs it. Focused tests are
`tests/test_native_automation_rpc.py`, `tests/test_native_coding_input_queue.py`,
`tests/test_native_coding_session_controller.py`,
`tests/test_native_coding_effects.py`, `tests/test_native_repl_state.py` and
`tests/test_native_thinking_model_hotkeys.py`. Matching user documentation is
`docs/automation-rpc.md` and `docs/rpc.md`; ownership text is `docs/sdk.md` and
`docs/architecture.md`; behavior changes require `CHANGELOG.md`.

The private D5a startup outcome gains one once-bound configuration port owned by
`ProviderMutationEffects`. RPC waits for that complete outcome before command
intake. The port exposes immutable configuration/catalog projections and bounded
model/thinking operations, never live state, locks, provider factories or
settings objects. Catalog enumeration includes only locally available,
tool-capable selections; unavailable, unauthenticated and non-tool-capable rows
cannot be selected. Cycling applies the existing `enabledModels` scope and
returns whether that scope selected the cycle set. The current selection remains
truthfully observable even when it is a custom row outside the catalog. Every
RPC model value is the exact privacy-safe `{provider, id}` projection; richer
catalog fields stay private and outside D5b. Selecting the active pair is an
idempotent success without provider construction, persistence or events.

Configuration mutation is idle-only. Capture expected queue and provider state,
perform fallible catalog/provider preparation without the D5a gate, then re-enter
the D5a outer gate. The final operation rechecks true idle and invokes the
existing provider mutation commit using the established outer-gate →
coding-effects/mutation-I/O lock → session/generation lock order. A prompt or
reservation admitted during preparation wins and makes the mutation fail without
changing provider state, coding binding, durable tree or defaults. Provider
construction, filesystem I/O, output, callbacks and worker wakes never run under
the D5a gate. A successful in-memory commit is visible to every later prompt;
presentation/default or durable-append failure follows the existing state-first
policy, returns truthful success for the live state and emits only bounded
diagnostics.

`get_state` reads model and thinking from one immutable owner snapshot and queue
state from the D5a control; it deletes the RPC-local thinking cache. Catalog-backed
`set_model` and `cycle_model` use the existing detached prepare/exact commit/live
coding rebind, including its current provider-visible history clear and new usage
accumulator. They clamp the prior level through the existing model-capability
helper; an effective level change is durably appended and emits one
`thinking_level_changed` after the model response. The durable append remains an
owner-side attempt before it returns the successful live-state result.
`set_thinking_level` and `cycle_thinking_level` use model-supported levels,
rebuild and atomically refresh
the same selection's provider binding while retaining provider-visible history
and the usage accumulator/counters, and append exactly one thinking-level entry
only for an effective change. A successful thinking response precedes its
`thinking_level_changed` event; a no-op emits no event. No model-change event is
added. An injected provider without `NativeReplProviderState` is a truthful static
singleton: selecting the current model succeeds, model/thinking cycles return
`null`, non-current models and unsupported non-`off` levels fail, and no
transport-local configuration state is created.

Acceptance deterministically covers two available tool-capable catalog models,
scoped and unscoped cycling, singleton `null`, unavailable/auth-blocked and
non-tool-capable refusal, next-request provider/model/thinking changes, coherent
state, durable effective thinking changes, no-op event suppression, and prompt
admission racing detached preparation in both orders. Tests separately pin model
switch history/usage reset, thinking-only history/usage retention, and
model-induced thinking response/entry/event ordering. Existing D5a queue,
settlement, idle, cancellation and EOF tests remain green. Synthetic tests do not
verify live credentials, provider-side model/reasoning acceptance or daily-use
switching; those remain explicit dogfood evidence.

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

`repl/collaborators.py::select_with_branch_summary` retains the original
work across canonical private provider execution, then prepares and conditionally accepts
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
D4b3b adds only collaborator execution/settlement and the existing executor's wiring,
with private retry, real callback-abort/provider/backoff and retained-worker-control
coverage. D5a must preserve this boundary while moving queue/RPC ownership.

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

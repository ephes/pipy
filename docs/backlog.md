# Pipy planning

Status: sole active planning basis, consolidated 2026-09-07.

## Start here

This document owns direction, priorities, task IDs, dependencies, and dispatch
state. Read the selected task and its referenced contracts, not old execution
ledgers. A task card is a bounded work order; it does not override a current
runtime contract. The orchestrator alone updates this index.

**Next dispatch:** A — daily-use baseline audit, B — semantic-compaction
contract, and C — product-session API ownership. These are three independent
read-only threads. No runtime implementation task is ready yet. The orchestrator
integrates their reports here and arranges Claude Code Opus 5 review before
releasing the first implementation task, D0.

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

## First dispatch: three bounded investigation threads

| ID | Status | Deliverable | Owns / must not do |
| --- | --- | --- | --- |
| A | Ready for read-only dispatch | Daily-use scenario and support matrix; propose the smallest missing acceptance scenario | No product fixes, provider calls or repository writes; distinguish source evidence, automated evidence and live-observed behavior |
| B | Ready for read-only dispatch | Semantic-compaction contract for D1 only, including ownership and failure semantics | Whole-user-group cuts; no budgeting, within-run cuts, retries or SDK migration |
| C | Ready for read-only dispatch | Minimal session API and ownership/migration map for D2 | No new event bus or alternate runtime; identify later queue controls without implementing RPC migration |

All three read `AGENTS.md`, this document, and the current architecture overview.
Source paths below are relative to `/Users/jochen/projects/pipy`; each thread
must inspect actual owners and existing tests rather than assuming the proposed
file location already exists.

### A — daily-use baseline

Read `docs/compaction.md`, `docs/rpc.md`, `docs/automation-rpc.md`, `docs/sdk.md`,
`tests/test_native_coding_session_resume_compact.py`,
`tests/test_native_automation_rpc.py`, the existing PTY tests named by
`just test-pty-smoke`, and `tests/test_native_extension_conformance.py`.

Return a matrix for edit/check, interrupt/steer, provider failure, restart/resume,
repeated compaction, extension load/reload/close, and an equivalent headless
workflow. For every row cite an existing test or a concrete missing assertion.
Identify the fake-provider scenario that D0 should reuse or extend. Mark live
provider/model configuration and semantic summary quality as untested until an
explicitly scoped dogfood run supplies evidence. No raw session bodies belong in
this report. Read-only tests may use isolated temporary state; do not contact
providers or mutate global settings.

### B — whole-group semantic compaction

Read `native/agent/history.py`, `native/repl/provider_selection.py`,
`native/repl/collaborators.py`, `native/coding/product_session.py`,
`native/coding/state.py`, and `native/agent/provider_turn.py` under
`src/pipy_harness/`; also read `docs/session-tree.md`, the compaction sections of
`docs/harness-spec.md`, and the focused coding/product-session compaction tests.

Return the summary inputs/output and safe publication contract: include prior
summary plus goals, decisions, constraints, relevant files and unfinished work;
retain valid recent tool exchanges. Reuse the existing branch-summary capability
but run through canonical provider execution/cancellation, not a copied direct
`provider.complete()` call. Enumerate snapshot identity, all guarded readers and
writers, cancellation/late-completion refusal, and resume reconstruction.

The current product-session coordinator is state-first:
`test_compaction_callback_failure_propagates_after_state_advances` pins that a
persistence callback failure can occur after state advances. Distinguish
summary generation failure (must not publish) from persistence failure after
acceptance; explicitly propose whether to retain or change that contract and
identify all affected tests/docs. Do not claim an atomic transaction exists.
Keep positive dropped-user-group counts in D1; D3 owns changing that invariant.
Return an implementation write set and decisive tests, not production changes.

### C — callable product-session boundary

Read `src/pipy_harness/sdk.py`, `native/coding/session.py`,
`native/coding/session_controller.py`, `native/coding/product_session.py`,
`native/coding/state.py`, `native/repl/wiring.py`, `native/automation/rpc.py`,
`docs/sdk.md`, and existing coding-session, RPC and event-order tests.

Return minimal construct/submit/observe/cancel/snapshot/close semantics, resource
ownership and thread-entry rules. Callers must not supply `TextIO`, terminal
factories, or private extension candidates. Reuse existing state, lifecycle,
provider construction and event owners; do not invent a second queue/history.
Map existing RPC shadow state and admission/settlement invariants to later D5a.
D2 may expose only the minimal boundary; D5a explicitly extends it with queue
admission and settlement before migrating RPC. Identify the deliberate removal
path for the compatibility SDK in D6, rather than silently changing
`run_native` behavior during D2. Return a narrow write set and two-turn headless
acceptance test proposal. Refresh the proposal after D1 is integrated.

### Copyable first-wave dispatch

Use this prompt in each fresh thread, selecting A, B or C before sending it:

```text
Perform task [A, B, or C — select exactly one] from the First dispatch section
of /Users/jochen/projects/pipy/docs/backlog.md. This is a read-only investigation
and specification task, not implementation and not an independent review gate.
Work in /Users/jochen/projects/pipy on its existing main checkout.
Read /Users/jochen/projects/pipy/AGENTS.md,
/Users/jochen/projects/pipy/docs/backlog.md and
/Users/jochen/projects/pipy/docs/architecture.md, then the selected card's sources.
Record git HEAD and the observed dirty state; do not switch branches or write
repository files. Do not invoke coding agents or reviewers: no claude, codex,
pi, review harness, review skill, or delegated subagents. The orchestrator owns
review, integration and commits; a self-produced verdict will be discarded.
Follow the card's acceptance criteria and non-goals. Return source/test evidence,
the concrete proposed contract or scenario, dependencies, smallest implementation
write set, decisive validation commands, and unresolved decisions. Name anything
not verified. If a requirement needs an ownership change outside the card,
report that boundary instead of broadening the task. Return the report by
message; do not create another planning document. Use summary-safe findings only.
```

## Implementation queue

These tasks are **not yet dispatched**. Each needs a refreshed bounded handoff
and reviewed contract when its prerequisites are accepted. If its concrete diff
would contain multiple independently useful changes, split the task here before
dispatch; the table is not permission for a large uncommitted batch. A thread implements
only its assigned task; completing a prerequisite does not authorize all of its
successors. Source write sets identify expected owners, not permission to edit
every listed module. Add a file only when the selected behavior needs it.

| ID | Prerequisites | Bounded outcome / expected write owners | Acceptance |
| --- | --- | --- | --- |
| D0 | A accepted | Reuse/extend one deterministic daily-use scenario and record a truthful support matrix; focused tests and user docs only | Tool-backed edit/check/resume evidence; identify uncovered cancel/compaction/extension behavior; live-provider status explicit; no incidental fixes |
| D1 | D0; B reviewed | Whole-group semantic compaction service and existing compaction integration; `repl/provider_selection.py`, `repl/collaborators.py`, necessary coding/history owners, focused tests, compaction contracts/docs/release note | Prior summary survives repeated compaction; next request retains task facts; failed/cancelled/stale generation cannot publish; explicit persistence-failure behavior; equivalent resume |
| D2 | D0; C reviewed and refreshed after D1 | Minimal product-session facade over coding/composition owners plus an explicitly distinct supported SDK entry point; API tests and SDK docs | Two-turn tool workflow, events, cancel, snapshot and disposal; no frontend-specific caller dependencies; no duplicate state owner |
| D3a | D1 | Model-aware budgeting at current safe request boundaries; model/request measurement and compaction trigger | Count system text, tools, attachments, history and output reserve; small-context tests; explicit recoverable refusal when no safe sufficient cut exists |
| D3b | D3a; dedicated safe-cut spec | Long-single-run compaction; history representation plus all product/persistence/reconstruction consumers | Explicit replacement of positive dropped-group invariant; no orphan tools, duplicated entries or lost prior summary; cancellation and resume coverage |
| D4a | D0; refresh against D1/D2 | Bounded cancellable provider retry; canonical agent/provider-turn mechanism and coding policy/configuration | Retry the unchanged failed request within one accepted iteration, not the whole run; bound nested transport attempts; preserve prior tool effects; cancel/exhaustion/event-order tests |
| D4b | D1, D4a | Apply the same retry mechanism to summaries | Failed/cancelled summaries publish nothing; accepted retry persists once; true-idle waits for settlement |
| D5a | D2 | Extend session facade with queue admission/settlement; migrate RPC prompt/queue/abort/state | Inventory guarded readers/writers; preserve reservation, agent-end/settled atomicity, isStreaming and abort/close boundaries; no old/new dual writers; API/RPC equivalence |
| D5b | D5a | RPC model/thinking controls through existing session model-selection owner | Truthful snapshots, real next-request selection, existing refresh/trust rules, correlated responses |
| D5c | D5a, D1 | RPC compaction and auto-compaction controls | Controls affect real session policy; preserve documented event names, reason values, framing and correlation |
| D5d | D5a, D4a | RPC retry enable/abort | Controls reach the real retry owner; abort/backoff/settlement races covered |
| D6a | D2, D5a | Session resume/close API | Equivalent reconstructed context; extension lifecycle once; no late writes after retirement |
| D6b | D6a | Fork/clone/session replacement API | Existing tree and extension veto contracts; rebind observations once; caller-visible outcomes and persistence agree |
| D6c | D6b | Incremental frontend adoption and deliberate compatibility SDK retirement, one entry point per chunk | Same session owner across SDK/modes; executable embedding example; update docs and remove replaced surfaces without aliases |
| D7a | D0; coordinate with D5 writer | Direct RPC bash process cancellation; `command_sandbox.py`, existing process-lifetime capabilities, RPC bash handlers and tests | Process-tree termination; timeout differs from explicit abort; one terminal response; preserve direct-command versus model-tool policies |
| D7b | D7a | Correlated incremental RPC bash output | No updates after completion; bounded output, JSONL purity and EOF disposal |
| D8 | D0; refresh embedding after D2/D6c | Reuse existing extension conformance example/tests; audit provider replay before selecting changes | `docs/examples/extensions/pipy-extension-conformance.py` already covers tools/commands/events: prove missing behavior before adding examples; same-provider resume/cross-provider history evidence before schema work |

D1 is the first product improvement. D2 is the main extensibility investment.
D3 and D4 can be reordered after measured failures justify it; D7 can move earlier
if direct-command cancellation matters to the selected workflow. The
orchestrator records such a decision here before dispatch. No simultaneous
runtime writers: D1/D2/D3/D4 share session/provider integration, and D5/D7 share
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
A self-produced review verdict is not evidence. No runtime task is currently
ready for such an implementation handoff.

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

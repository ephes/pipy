# Native durable execution implementation plan

Status: **execution authorized; DE01 and DE02 with all children landed;
DE03a/b/c/d1/d2 and cumulative DE03d/DE03 landed; DE04a LANDED; DE04/DE04b/DE04b1a IN_PROGRESS, DE04b1b OPEN; DE32a LANDED, DE32 IN_PROGRESS, DE32b OPEN; DE06a landed, DE06 IN_PROGRESS and DE06b OPEN**,
2026-10-22. Authored
by GPT-6.1 Sol at high effort. The owner authorized the DE00–DE45 campaign after
the planning handoff. The final plan review returned **ISSUES, Suggestions only**;
its two wording suggestions were applied before the frozen handoff. This is not
a CLEAN review. The [evidence ledger](2026-10-19-durable-execution-evidence.md)
records provenance and current gates. This work order does not enable a feature,
change a product default or claim that an acceptance row already passes. Each
implementation slice still requires its own independent CLEAN review.

## 1. Goal, reference and scope

Deliver **full useful Python execution parity with Pi durable 1.3.0** at
`2ec55d808d455b1a64a2bf7888198207a096120a`, through native pipy. That local
pi-mono checkout was clean when inspected on 2026-10-19. The
[upstream audit](2026-10-19-durable-upstream-audit.md) inventories its published
surfaces, source locations and tests, including telemetry and host capabilities.
The target is the durable package and the provider/codemode/environment behavior
it needs, **not every feature of every pi-mono package**. Updates after this ref
need a separate delta audit and owner scope decision.

The owner's first priority remains controlled continuation of one native run
after terminal/process death. **Milestone A** delivers that first. It is not a
permanent deferral of queues, streaming, extensions, cancellation, compaction,
subagents, Python codemode or native product integration. The entire matrix in
section 4 is the finish condition. Earlier MCP/codemode exclusions do not silently
exclude a feature now published by the durable target: durable codemode model
catalog/classification/image globals are explicitly included below.

Execution parity means the same observable guarantees and useful operations for
Python callers and pipy users: admission, committed history/state, scheduling,
recovery, uncertainty, cancellation/ownership, queues, usage, configuration,
plugins/tools and observation. Names may follow Python conventions. A TypeScript
class or token does not require a Python class of the same name, but every
behavior must have a supported Python operation, conformance evidence and docs.
No TypeScript runtime, Pi CLI wrapper, alternate coding-agent CLI, or subprocess
bridge may become the product execution path.

### Explicit scope boundaries

| Boundary | Decision and rationale |
| --- | --- |
| TypeScript syntax/types | Python API and Python WASI codemode, not TS syntax, branded numbers, structural typing, `Promise` or Vitest compatibility. Port observable semantics and finite value fidelity, not language machinery. |
| Chord | Support atomic typed JSON documents, histories, forks, operations, convergent state/watch semantics and bounded caches. Chord wire encoding, its JS tracker implementation and general remote CRDT collaboration are not required. Python operation encoding is versioned and conformance-tested. |
| Cloudflare/Node adapters | Local memory, SQLite and portable JSONL semantics are included. Node bindings, Cloudflare Durable Objects/alarms/D1, Workers bundling and Photon loading APIs are excluded platform implementations. Portable wake/timer/env/storage/image interfaces remain included so those hosts can be added later. No distributed ownership or multi-machine lease claim. |
| Experimental durable TUI | Removed upstream at the pinned ref; historical `5609b0d6c` is integration evidence only. No pixel layout, keymap or resurrected server/client stack requirement. Native pipy TUI queue/progress/reopen/subagent/task inspection and existing tree workflows remain included. |
| Provider/model breadth | Preserve the existing native provider set and exact replay fields; test all canonical/wire families offline. Add deferred and codemode paid operations for capable providers with explicit unsupported capability results elsewhere. Do not rebuild all pi-ai providers/catalog operations unrelated to durable execution. |
| Operating systems | Product acceptance targets current supported Linux/macOS/Python versions. PowerShell is exposed where an executable/environment capability exists, with fake env conformance on every host; Windows process-host support remains separate from durable parity. No silent claim of Windows runtime recovery. |
| External effects | No universal exactly-once guarantee. Safe replay needs an actual tool contract. Unsafe interrupted work returns an explicitly uncertain interrupted result at full parity; remote billing/completion/cancellation and orphan processes may remain uncertain. |
| Compatibility | Existing native CLI/TUI/SDK/RPC and tree behavior are part of adoption. Byte-compatible Pi databases/JSONL, import of Pi raw transcripts, and compatibility/deprecation aliases for old pipy-only APIs are not required. Existing pipy data receives an explicit supported migration/export path. |

These are proposed scope decisions made explicit for review, not unexplained
missing matrix rows. If the owner requires Cloudflare deployment, TS guest
scripts, Windows process hosting or Pi binary storage compatibility, revise the
plan and effort before implementation; none can be labeled parity by implication.

## 2. Evidence and initial state

Before editing the plan, the entire uncommitted worktree was captured through a
**temporary Git index** in baseline commit
`16e65c41c25ed6b4495d5356a3ce8d3b30ba8a63`. It is protected from garbage
collection by the named **local, non-branch** ref
`refs/pipy/baselines/durable-2026-10-19`, which must resolve to that exact commit.
The real index and `main` ref were unchanged; the baseline ref is not pushed. It contains the existing changes in `CHANGELOG.md`, `docs/backlog.md`,
`docs/harness-spec.md`, the two prototype docs, `scripts/durable_prototype/`, and
the two prototype test modules. This preserves provenance; it is not a landed
commit or permission to stage unrelated work.

The [prototype](../durable-prototype.md) and
[real-loop spike](../durable-loop-integration.md) establish admission/result
atomicity, one owner, safe replay/unsafe blocking, rich finite canonical values,
usage reconstruction and commit-before-completed-message observation. The second
uses the real `AgentLoop` with existing product request/tool/session adapters.
It does **not** prove arbitrary callbacks replay safely. Its active resume reruns
preparation, control flow, policies, normalization and queue callbacks. Those are
pure/fixed in the experiment. It suppresses partials, live usage, lifecycle and
progress; source hashes refuse upgrades rather than migrate them.

The production `CodingProductSessionCoordinator` currently mutates live state
before synchronous persistence; JSONL trees are transcripts, not schedulers.
`session_controller.py` and the input queue own in-memory claims.
`ProviderTurnExecutor` has bounded in-call retries, not persisted sleep phases.
Python codemode has a pinned CPython-on-WASI runtime and branch store, but no
persisted deterministic recovery journal. Existing per-call `CustomEntry`
records alone cannot recover a partially executed script.

Summary-safe archive `list` and targeted `search durable` were inspected before
planning. They corroborate these bounded experiments and previous persistence
ownership work; no raw transcript bodies were used. A new intentional partial
planning record stores summary-safe decisions, roles, review and verification
outcomes outside git.

## 3. Architecture and invariants

### 3.1 One runtime owner and one store authority

Add a native package, provisionally `src/pipy_harness/native/durable/`, for
records/codecs, storage, session mutation, documents, task runtime, scheduler,
submissions and views. Names are proposed seams, not a demand for an oversized
framework. Reuse canonical message/request/tool values, provider transports,
argument validation, native capabilities, budgets and existing policy functions.

The durable scheduler owns **progress**, including the generation/tool tasks and
their phase checkpoints. The existing synchronous loop stays usable for the
current non-durable product while adoption proceeds. Extract shared pure
preparation/classification/round-policy functions as needed; do not maintain two
independent policy implementations. Replaying the whole synchronous loop remains
a useful reference fixture, not the mandated final scheduler. A hook, queue,
compaction or child lifetime must not be smuggled through a receipt adapter that
reruns untracked external effects.

For a durable session, one canonical local path has one process owner holding a
persistent OS lock through opening, execution, joining and closing. Protect
in-process readers/writers with the same mutation authority; a DB lock alone
does not guard Python object graphs. Resolve aliases before admission. Lock
content is diagnostic metadata, never the sole ownership proof. A crashed
process releases the lock; no age-only lease stealing while old code runs.
Migrations, compaction, registry reservations and operator resolutions share this
line. Unsupported shared filesystems fail explicitly; no distributed guarantee.

**SQLite is the sole product authority for the initial durable mode.** Entries,
control, documents, tasks, submissions, usage and publication sequence commit
there. JSONL transcript export is a derived, regenerable snapshot and is never
an independently appended product writer. Later portable JSONL is an alternative
complete backend selected once per session, not a mirror beside SQLite.
The metadata-only `pipy-session` archive remains a separate optional workflow
record and cannot drive product recovery.

Milestone A has an explicit capability allowlist: a new single-run session,
completed-message observation, inspect, controlled resume/resolution and close.
Until their owning slices land, durable sessions disable automatic/overflow
compaction and reject manual compaction, tree/fork/clone/reset/handoff, queued
steer/follow-up, extension message injection and any other unsupported product
operation with an actionable unsupported-capability error **before accepting
input or creating effects**. Overflow fails the bounded run visibly; it cannot
invoke the legacy compactor. No operation may fall back to JSONL persistence,
a volatile queue claim or the non-durable runtime. Later slices enable each
capability only when its durable admission/state transitions and tests exist.

### 3.2 Commit, ownership, publication and failure

1. A single session mutation line spans admission, detached candidate creation,
   validation/preparation, backend commit settlement, in-memory adoption and
   publication enqueue. Readers cannot see a candidate. Every field's reader and
   writer, including close, abort, registry-driven scheduling and observers,
   follows this authority; check-then-mutate outside it is invalid.
2. Entries/IDs are immutable and never reused; commit sequences strictly
   increase. One transaction atomically covers all table/document/control and
   usage changes. Drafts expire at callback settlement and inputs/outputs detach
   mutable containers. Table reads after the first table write reject; document
   read-your-writes remains available.
3. No provider/tool/process/network/human effect runs on the mutation line.
   Durable intent commits first, then the external effect executes off-line,
   then result/control/usage settle together. The runtime cannot atomically
   commit a filesystem or remote side effect with SQLite.
4. All **published** messages, partials, progress, queues, usage, graph and
   lifecycle facts come from committed state. Milestone A publishes only
   completed messages/outcomes and documents that limit. Full parity adds
   committed throttled streaming. An observer may coalesce updates or receive
   a fact again on reconnect; stable sequence/entity keys permit dedupe.
   Exactly-once callbacks across external acknowledgement loss are not promised.
5. Backend errors and internal adoption/reconciliation errors seal the session,
   stop new work and publication, abort/join invocations and require reopen.
   A thrown acknowledgement may have committed. Request IDs reconcile that
   ambiguity. Invalid/cancelled read exceptions stay scoped as in the reference;
   preparation/hook validation failures before backend admission roll back.
   "Stop publication" means later publications: as in the reference
   (`session.ts` 806–817), a failure an internal commit listener raises still
   lets the current publication reach its remaining listeners, and the durable
   triggering commit resolves.
6. Close seals reservation/admission, lets admitted commits settle and joins
   invocation work before backend/lock release. Close is resumable shutdown;
   durable abort is a separate state transition. A noncooperative child requires
   process containment or a truthful blocker, never premature owner release.

Python spellings recorded before the first Session code (2026-10-21):

- Asyncio cancellation of an `acommit` caller (DE02b2b) reaches the running
  callback, unlike the reference's abort signal. Before backend admission it is
  the callback's own failure and rolls the commit back; once the callback
  settles, settlement is owned work that caller cancellation cannot cut. Only an
  explicit cancel view is the reference abort signal.
- Raw-base no-op suppression (DE02b2a) compares the prepared value with the
  loaded one. The reference's op-based rule, under which a structural no-op such
  as shift then unshift still writes and publishes a delta
  (`session-documents.test.ts` 190), is a cumulative DE04/DE27 obligation, not a
  dropped DE02b requirement.

### 3.3 Versioning, identity and uncertain effects

Persist format/schema, document/task definitions and checkpoint codec versions.
Reject newer versions, migrate supported older versions atomically and preserve
unavailable definitions. Schema migration owns the same lock and is restartable;
read-only historical document migration does not rewrite history. Use semantic
compatibility/version rules, not a permanent hash of every source file. The
initial capability allowlist can conservatively pin adapter versions until
migrations exist. Terminal history/inspection stays readable without credentials
or execution compatibility; resuming active work checks compatibility first.

Pinned identity covers provider/API/model/thinking, provider conversation/cache
session identity, stable call/request/task IDs, workspace namespace and canonical
path, execution environment, task/tool/extension versions, request preparation,
pricing/budget interpretation and relevant configuration. Credentials are
resolved from the existing auth boundary at invocation time and never persisted;
secret headers and tokens do not enter checkpoints. Semantic request headers or
metadata need a redacted reconstructable representation or a refusal to resume,
not accidental credential storage. A workspace identity is not a file snapshot.

Tool intent stores **prepared final arguments**, schema/policy/implementation
identity and nested parent/key before execution. Recovery executes only when
stored policy **and** currently resolved policy both permit replay and the
version contract still accepts the call. Reads may observe changed files only
if they had no committed result; committed results are reused exactly.
Native general write/edit/bash/MCP/extension calls default unsafe absent a
specific documented replay or idempotency protocol.

Milestone A exposes unsafe uncertainty as durable blocked work. Its inspect
operation reports the phase and possible effect without fabricated success.
DE13 changes the default interrupted path for top-level **model-issued tool
calls** to a durable `interrupted` error result containing committed partial
output/details/diagnostics, then lets the model continue. DE24 adds equivalent
interrupted-error continuation for nested tool calls once keyed nested identity
and results exist. Provider generations follow P17's partial/reissue contract,
not this unsafe-tool path. Neither DE13 nor DE24 reexecutes the unsafe tool call.
The uncertainty marker and attempts remain inspectable. An explicit recovery command may record
abandon/interrupted, attach a verifiable tool-specific receipt, or authorize a
**new** invocation after an explicit duplicate-effect warning. It cannot silently
flip an old attempt to success, mutate history, or use a manual SQL edit as an
API. Decisions are immutable, deduplicated and serialized with completion/abort;
one owner cannot resolve an attempt while another still runs. No universal
receipt verifier is assumed.

### 3.4 Task and extension contracts

Tasks persist versioned input/checkpoint/memos/outcomes, not Python frames. Start
with sequential generation/tool phases for Milestone A, then add the complete
pending/running/waiting/sleeping/completing/terminal state machine. Reopen
reconciles without dispatch; missing definitions/migration failures/restart
limits block rather than discard work. Definition replacement hands over only
at a phase boundary; running work keeps its snapshot. Persisted progress resets
restart counts; document chatter alone does not.

History ancestry and lifetime ownership are separate immutable edges. Ordinary
owned work drains before terminal settlement; background tasks are traversal
boundaries. Cancellation marks commit before signalling, abort handlers run
bottom-up, held failure outcomes cascade and fail-fast joins respect their wait
policy. In-memory wait cancellation does not abort shared tasks. Admission and
terminal/abort checks must use the final candidate records in one transaction.

Pure policy/request transforms may be recomputed against their pinned inputs.
The durable extension API follows upstream composition and failure semantics:
`beforeRequest`, `afterResponse`, `onYield`, `beforeTool`, `afterTool`,
`afterTools`, `beforeCompact`, ordered tools/sections/wrappers and first-writer
memos. Such callbacks may rerun before the consuming commit; **a receipt around
an arbitrary hook does not make its side effects replay-safe**. Durable extension
docs require external work to become an explicit task/tool with its own policy
or accept at-least-once hook behavior. Existing unrestricted extension hooks are
not automatically enrolled: classify and adapt each boundary; fail closed for
unsupported recovery effects in Milestone A, then provide documented durable
semantics during registry/hook adoption. Reload pins an active invocation and
uses new code at the next valid boundary; it never serializes arbitrary objects.

### 3.5 Python codemode recovery

Keep Python on the established WASI runtime. Extend the guest/host protocol so
a run records source/module version, runtime/stdlib fingerprint, seed, clock,
deadline, discovery/tool schema snapshot, model/global catalog and initial store
**before** issuing effects. Reexecute from this start, then deliver journaled
results in their original delivery order with the recorded clock. Every result
has a call ID, stable nested key, name/args, issued-count acknowledgement and
recorded mapping from structured/error outputs. Commit the acknowledgement that
made a call possible before admitting its new effect. Hold unjournaled calls
until replay catches up and all prior issue counts/identities match.

The current WASI guest is not assumed deterministic merely because it is
sandboxed. Audit/restrict time, random, entropy, filesystem/import inputs,
threading, host globals and observable delivery scheduling; pin runtime contents.
Use differential replay probes to prove the supported language subset. If the
host cannot reproduce an acknowledgement/fingerprint/call ordering, commit a
visible divergence result **before any new effect**, stop the guest outside its
catchable error path and clean up owned work. A failed capability proof is an
implementation blocker for codemode parity, not grounds to claim it complete or
to switch the product to TypeScript.

Nested results already committed are reused; unsafe interrupted child calls
return interrupted errors. Journal/result and successful store writes share the
same authority; fork/as-of follows document semantics. Stable output directories
and file names make overwrite recovery deliberate. Deadline expiry and module
upgrade end with stored errors. Paid classify/image globals are internal nested
tools with their own replay policy, concurrency bound, usage and cancellation.

## 4. Traceable parity matrix

Every row starts **OPEN**, tracked in the
[evidence ledger](2026-10-19-durable-execution-evidence.md#parity-status).
The implementing slice must append Python test IDs,
reference fixture IDs, review result and main commit; do not replace OPEN with a
prose assertion. A source file alone is not a passing comparison. Upstream paths
below are within `packages/durable/`; `spec` means its normative spec. Exact
reference exports and host options are indexed in the linked audit.

| ID | Required observable contract | Upstream source / representative tests | Slices |
| --- | --- | --- | --- |
| P01 | Atomic commit/adoption, immutable IDs/entries, detached values, revoked drafts, read-after-write and storage-error poisoning | spec §§1,4,10; `session/transaction.ts`; `session-tables`, `session-states`, `harness-storage-failure` | DE02–04 |
| P02 | SQLite schema/reopen/migration/newer refusal, indexed scans/cursors, bases/tails and retention | `storage/sqlite/*`; `sqlite-storage`, `sqlite-migrations`, `sqlite-facade` | DE03, DE27 |
| P03 | Memory and portable JSONL equivalent semantics; torn/unconfirmed tails, confirmed corruption, marker-before-publication/reclamation, explicit fsync limits | `storage/{memory,jsonl}/*`; `memory-storage`, `jsonl-storage`, testing conformance | DE02, DE40 |
| P04 | Typed singleton/family docs, scopes, latest/rewindable, create/retire/incarnations, strict JSON and checkpoints | spec §3; `documents.ts`; `session-documents`, `session-definitions` | DE04, DE27 |
| P05 | Access-driven document migration, detached historical reads and forks, preserve unknown definitions/versions, bounded cache and observed pinning | spec §§3.5–4; `session-checkpoints-migrations`, `session-document-retention`, `session-forks` | DE27, DE30 |
| P06 | Task phase/checkpoint/memo and effect sandwich, pending/running/terminal recovery, no dispatch on open | `tasks.ts`, `harness/scheduler.ts`; `harness-tasks`, `harness-tasks-recovery` | DE05, DE14 |
| P07 | Definition migrations/reload, blocked missing/newer/failed, restart counting/park/retry, abort orphan vs abort result | spec §5.4; `harness-blocked`, `harness-registry`, `harness-inspect` | DE14, DE23 |
| P08 | Durable sleep deadlines, clock/timers/host wake active/at, early/late wake, paused scheduling | spec §5.6; `harness-sleep`, `harness-host-inputs` | DE17 |
| P09 | Owned task/conversation graph, completing holds, background boundaries, allSettled/failFast and wait-cycle rejection | spec §§5.4–5.5; `harness-structured`, `harness-ownership` | DE15, DE31 |
| P10 | Abort mark before signal/join; bottom-up handlers, close distinct from abort, no post-end runtime writes or post-abort run commit | `harness-cancellation-barrier`, `harness-lifecycle`, `harness-structured` | DE16 |
| P11 | Submission input/write admission, request-ID dedupe/type conflict, status/wait/withdraw and stable answer/unanswered settlement | `harness/submissions.ts`; `harness-submissions`, `harness-input-entries` | DE18 |
| P12 | Inbox deterministic ID order, reject/steer/follow-up, all/one modes, postTools/final selection, passive writes/reset/stale heads | spec §6; `harness-inbox`, `harness-input-entries` | DE19, DE30 |
| P13 | Failed-run keep/continue/withdraw; abort preserves writes, settles each placed input exactly once | `harness-failed-run-inbox`, `harness-submissions` | DE19 |
| P14 | Entry/context at point, heads/edits/contributions, tool ordering/missing result synthesis, named system baseline/deltas and tool visibility | `harness/{context,prompt}.ts`; `harness-context`, `harness-prompt` | DE20, DE30 |
| P15 | Exact canonical ordered rich blocks: text/thinking/redaction/signatures/images/raw args, correlation IDs, details/structured result/diagnostics/controls and timestamps | `entries.ts`, `harness/{generation,tool}.ts`; `harness-generation`, `harness-structured-output` | DE06–08, DE24, DE33 |
| P16 | Request cutoff, provider/API/model/thinking/options, provider session/cache identity and matching-target projection; credentials reconstructed privately | `harness/{generation,provider}.ts`; `provider-session-cache-e2e`, `system-order-cache-e2e` | DE06, DE07, DE10, DE12 |
| P17 | Completed provider result reuse; interrupted partial -> aborted entry before reissue; deferred handle poll/cancel and no silent duplicate paid claim | `harness-generation-recovery`, `harness-generation` | DE07, DE17, DE26 |
| P18 | Durable attempt retry/error classification/backoff, layered SDK retry bounds, overflow compaction and cancellation while sleeping/polling | spec §§8.3,8.7; `harness-generation`, `harness-compaction` | DE17, DE29 |
| P19 | Usage/cache read/write/1h/reasoning/cost fidelity; failed/partial/compaction/tool spend committed once, per-model/tools/session sums and zero child-fork ledger | `harness/usage.ts`; generation/compaction/tools tests | DE06, DE07, DE26, DE29–31 |
| P20 | Tool offered-at-request and currently callable checks, repair + schema before/after hook, stored final args, stored/current replay intersection | `harness/tool.ts`; `harness-callers`, `harness-tools-recovery` | DE08, DE21, DE24 |
| P21 | Unsafe interrupted model-issued tool -> uncertain error result and **continued run**, no reexecution; nested equivalent once keyed calls exist; cleanup/terminal holds for owned children and missing tools | spec §8.4; `harness-tools-recovery`, `harness-ownership` | DE13, DE16, DE24, DE31 |
| P22 | Sequential/parallel rounds, declaration execution mode, call-order result view, addTools, all-terminate/any-handoff and no unstarted call after abort | spec §§8.4–8.5; `harness-tools` | DE25, DE30 |
| P23 | Nested keyed identity/result reuse, schema/error mapping and interrupted-error continuation without reexecution, output suppression/control exclusion, progress, usage, cancellation and abandonment on restart | spec §7.3; `harness-nested-tools`, `harness-nested-tools-restart` | DE24, DE25 |
| P24 | Registry ordered replacement/uninstall, tools/sections/wrappers, caller/deferred exposure, agent configure and host getters at documented boundaries | `harness/{registry,agent}.ts`; `harness-registry`, `harness-callers`, `harness-host-inputs` | DE21, DE23 |
| P25 | Hook composition, per-hook failures, replay-before-commit and first-writer memo decisions; no implicit exactly-once side effects | spec §7.2; generation/tools/compaction tests | DE22 |
| P26 | Resource/config/code reload with in-flight snapshots and supported task/doc version transitions | spec §7.5; `harness-registry`, examples/31 | DE23, DE32 |
| P27 | Committed partial/tool-output/details/diagnostics/usage progress, throttle/flush settlement, bounded output/spills | `harness/live.ts`; `harness-live-deltas`, `harness-tools`, `tools.test.ts` | DE26, DE32–33 |
| P28 | Atomic baseline+subscription, convergent document/conversation views, listener isolation/end reasons, experimental event stream snapshot/change semantics | spec §9; `session-watches`, `harness-view`, `harness-events` | DE09, DE28 |
| P29 | Live task graph ownership/waits/sleep/block/completing states and retained context/document cache bounds | `harness/task-graph.ts`; `harness-task-graph`, `harness-scheduler-{indexes,retention}` | DE27–28, DE31 |
| P30 | Manual/background/blocking compaction; immutable selected request, retry/usage, range invariants, stale summary placement, failure/cancellation | `harness/compaction.ts`; `harness-compaction` | DE29 |
| P31 | Fork at concrete entry, doc asOf/current/initial, zero queue/task/spend copy; reset/handoff cut context, preserve history and queue ordering | `session/forks.ts`; `session-forks`, `harness-conversations`, `harness-context` | DE27, DE30 |
| P32 | Per-conversation agents/environments and foreground/background subagent interaction, parent cancellation, completed child conversations remain usable | examples/22–24,29; `harness-ownership`, `harness-conversations` | DE31 |
| P33 | Env filesystem identity/reader/list/watch-ready/overflow and isolated shell cancellation/cleanup/output-window/spill capability semantics | `env/*`, testing env conformance; `env-node*`, `env-line-scan` | DE32 |
| P34 | Read/write/edit/bash/PowerShell results, mutation queue, image validation/orientation/limits and structured output | `tools/*`, `images/*`; `tools*`, `images-wasm-module` | DE33 |
| P35 | Deferred tool search and activation, BM25/namespaces, caller filtering and stable discovery schema snapshot | `tool-search/*`, `codemode/describe.ts`; `tool-search`, `codemode-describe` | DE34 |
| P36 | Python codemode deterministic start/fingerprint/seed/time/deadline, journal delivery/issue acknowledgement and new-effect gate | spec §7.6; `codemode/codemode.ts`, `codemode.test.ts` | DE35–36 |
| P37 | Codemode divergence/module change refusal before new effects; replay existing nested calls and clean unfinished/abandoned children | `codemode.test.ts`, `harness-nested-tools-restart` | DE35–36 |
| P38 | Codemode globals/discovery/result mapping, rewindable fork/as-of store, atomic successful store/result, stable output/image paths and limits | `codemode/{codemode,output,describe}.ts`; `codemode.test.ts` | DE34, DE37 |
| P39 | Catalog snapshots without secret headers; classify/image internal tools, schema errors/usage, max four model calls, hidden tools and cancellation | `codemode/models.ts`; `codemode-models.test.ts` | DE38 |
| P40 | Content-free submit/phase telemetry, reports and host error vocabulary, trace relationships, replay events and metric lifetime | `harness/telemetry.ts`; `harness-telemetry`, `harness-telemetry-options`, `harness-reports` | DE39 |
| P41 | Native SDK/headless/TUI/RPC recovery, queued input/stream/abort/retry/compaction/tree behavior uses same authority | current pipy SDK/RPC/tree contracts; historical experimental integration, not a current Pi package | DE11–12, DE41–44 |
| P42 | Private product data lifecycle, safe inspection, immutable workflow archive, explicit export/import/migration/default adoption; no dual writers | current `session-storage.md`, `session-tree.md`; spec storage failures | DE10–12, DE40–44 |
| P43 | Conformance runner/fixtures and useful performance bounds; no test skip or deviation substitutes for required behavior | upstream `src/testing/*`, storage/env suites and benchmarks | DE01, every slice, DE45 |

## 5. Delivery order and reviewable slices

Each ID is an independently green implementation increment. Do not implement
multiple rows in one uncontrolled change. If a row cannot be reviewed as one
small slice (normally a few engineering days), split it into suffixed work
orders **before editing** while preserving its acceptance and dependencies.
Update this index, docs and evidence ledger; splitting is not permission to drop
requirements. Paths under `native/` mean `src/pipy_harness/native/`; proposed
`durable/` modules are created only as needed. New tests conventionally live in
`tests/durable/`, with comparison code under `scripts/` and sanitized fixtures
under `tests/fixtures/durable/`.

Every implementation slice includes focused meaningful tests, updated topic
contracts/user docs and `CHANGELOG.md` when behavior changes, `just check`,
`prek run --all-files` **if** `.pre-commit-config.yaml` exists (it does not at
plan time), and `just docs-build` plus rendered-link checks for doc edits.
Terminal changes also require isolated real-PTY checks. It then receives
independent **Claude Code Opus 5.5 (`claude-opus-5-5`) HIGH** review through the
installed review harness, scoped fixes and value-driven re-review. Only then
commit and push that slice on `main`; confirm the pushed ref and checks before
starting its dependent writer. See section 7 for ownership and failure rules.

### Milestone A — first useful controlled single-run recovery

All DE00–DE12 are dependencies of the first advertised opt-in feature. There is
no public claim of full parity at this milestone.

| ID / dependencies | Concrete change and seams | Acceptance / crash cuts and documents |
| --- | --- | --- |
| DE00 / reviewed plan | Safely land the existing isolated prototypes, tests, docs and reviewed plan from the recorded uncommitted baseline; reconcile accumulated backlog/harness/changelog hunks. Production never imports prototype scripts. Inventory all unrelated changes first; stage only owned paths/hunks, verify and retain local `refs/pipy/baselines/durable-2026-10-19` at `16e65c41c25ed6b4495d5356a3ce8d3b30ba8a63`. | Reproduce both focused prototype test modules and evidence assertions, docs/link build and full checks; preserve unrelated files/index choices. Record prototype limits and review provenance. This is the first future commit, not a commit in this planning session. |
| DE01a / DE00 | Versioned pinned reference bootstrap and trace contract: private exact-commit archive, locked scratch installation, checked dependency/module provenance, strict finite JSON schema and comparison mechanism, sanitized fixture manifest and actual minimal Session/MemoryStorage reference execution. | Known trace mutation fails; missing Node/deps/ref/fixture or drift fails closed. Explicit versioned JSON/ID/clock representation only; preserve errors, calls, histories, usage and causal order. Reproduction, source hashes and MIT provenance. No Python durable adapter yet; all P rows stay OPEN. |
| DE01b1 / DE01a + R1 | Strict versioned multi-case registry and closed hashed adapter provenance; auditable bounded ID/clock/duration projection retaining raw observations; actual H1 Harness/tool and C1 context-head fixtures, with old kernel fixture retained byte-for-byte. | Complete pinned real runs, repeat projected equality; schema-valid lost-field and stale-head mutants mismatch; invalid/missing/drifted evidence fails closed. Python adapter and all P rows OPEN. |
| DE01b2 / DE01b1 | Real H2 SQLite tool recovery across all stored/current safety intersections, H3 durable abort/late-write refusal, and E1 Node execution-env byte/argv fixtures. | Add schema-valid duplicate-effect and post-abort-write mutants; rerun all prior cases and all four required negatives. Optional E2 cannot replace E1. |
| DE01b3a / DE01b2 | Executable fresh local acceptance; exported storage workload correctness/readback before bounded timing, hardware/count evidence, four fresh semantic mutants and exhaustive DE/P current-versus-future index. | One fresh six-family bootstrap per acceptance, H2x4/E1x2, twelve reads/three writes with fresh measured write seeds, four valid CLI1 mutants; exact provenance and all P/Python adapter OPEN. No timing golden or performance parity. |
| DE01b3b / DE01b3a | Mandatory real pinned reference CI and selective synthetic artifact collector. | Actual successful exact-pushed-commit required job executes fresh acceptance plus standalone Nodecodec, all six families/submatrices/workloads/four mutants; retained selective artifacts and full log/hash audit. All original b3 requirements remain cumulative. |
| DE02 / DE01b3b | Add records, strict finite JSON codec, storage port, detached memory reference and session commit/adoption core in `native/durable/{records,storage,session}.py`. | Cross-record atomicity, no alias/draft escape, immutable IDs, cancelled queued vs admitted commit, read-after-write and publication after storage only; inject failure before/after commit acknowledgement. Kernel/storage spec. |
| DE02a / DE01b3b | Cumulative owned finite JSON, complete versioned records, Storage port and detached atomic Memory boundary, delivered by DE02a1 then DE02a2. | All original DE02a obligations remain cumulative: a1 value/record/port proof plus a2 Memory/raw-base/ancestry/index/concurrency and full native storage comparison. No Session adoption or P closure. |
| DE02a1 / DE01b3b | Owned full-finite-binary64 values and versioned record-tree codec; complete record, query, cursor and write DTOs with Storage Protocol. | No caller mutable alias; omission/null, signed zero, above-safe payload values, nesting beyond256 and lone-surrogate identities. Two fresh pinned copyJson/record-consuming Pi versus Python codec probes and valid field/ID/number/alias mutants. No native Memory/Session or storage-parity claim. |
| DE02a2 / DE02a1 | Detached atomic MemoryStorage: one ID namespace/root1/mint2/seq1, complete replacements, fork-visible reads/cursors/indices, current raw-base create/change/retire atomic with tables. | Full DE02a backend acceptance: invalid-last-write rollback, retained/read ownership, deterministic concurrency barriers, real raw-base draft seam for b, two fresh native storage comparisons and schema-valid index/sequence/cursor/alias mutants. Request IDs, task kinds and document kind/key identities compare by exact UTF-16 code units across equivalent Python spellings. Unsupported delta/copy/as-of/fork document commands reject whole batches; cumulative DE04/DE27 requirements retained. |
| DE02a2a / DE02a1 | Internal atomic table Memory stage with one authority/candidate/adoption, global IDs/Seq, complete replacement indexes, ancestry/cursors/close and deterministic concurrency. | Real current Node/Python table operations, two fresh pinned paired chains, field/ID/Seq/request/status/cursor/alias mutants and failure/barrier proofs. No document support, product factory/default or full Storage claim; a2b remains mandatory. |
| DE02a2b / DE02a2a | Current raw-base documents and address/scope indexes inside the same table candidate/authority, with full cumulative bounded Storage Protocol acceptance. | Supported late-document failure rolls back tables/indexes/documents/counters; mixed concurrency/ownership plus two fresh cumulative native storage comparisons and document/index mutants. Original a2 requirements retained; a2 and a wait both children, b waits a2b, DE03 waits b; all P OPEN. |
| DE02b / DE02a2b | Session/Tx admission, revocable candidates, seal/prepare/guard, uncancellable admitted settlement, adoption/publication and close. | Retain original DE02 commit/adoption acceptance: queued cancellation cutoff versus running callback, read-after-write, final-owner checks, no late initializer/draft escape, first-failure poisoning and late-read discard, before/after-ack storage failure, commit-before-adopt/publication, listener/reentry and close/drain barriers. DE02 parent completes only after a1+a2+b; DE02b itself completes only after its b1a+b1b+b2+b3 children. |
| DE02b1a / DE02a2b | Private native guarded Storage and lifecycle owner: first failure, admission seal, call registration, drain, backend-close-once and report-once over the real Memory backend. | Deterministic permanent native tests: all16 delegates, 13 read-role exemptions versus never-exempt commit/mint, underway-call failure, paged-read seal, first-cause and close-failure, concurrent and cancelled closers, reporter and BaseException decisions, plus reached-guard private negative controls. No public Session, Tx, reference oracle or P closure. |
| DE02b1b / DE02b1a | Actual pinned Node guard oracle through a bounded driver subclass of the exported SessionImpl exposing its protected guarded storage, with paired Node/Python guard recipes. | Paired recipes, schema-valid guard mutants, a closed manifest and two fresh pinned chains over the same b1a owner. No Session/Tx surface or P closure. |
| DE02b2 / DE02b1b | First bounded internal Session and Tx over the same owner: FIFO line admission, sync and suspending callbacks, table phase/final owners, nested-revocable raw-base drafts, pending-operation seal/drain, prepare/discard/adopt, acknowledgement cuts, publication and close draining accepted work. | Callback API decision recorded before code; no-op suppression and late-initializer/mint gates; adopt failure rejects while an internal-listener failure poisons a durable commit that still resolves. The first exposed commit carries all coupled lifecycle/failure/publication behavior; may split again before code. Split 2026-10-21 into b2a+b2b; DE02b2 completes only after both. |
| DE02b2a / DE02b1b | Private sans-IO Tx core and mutable raw-base drafts over the same owner, with an inline sync driver and no commit entrypoint: table phase rules, internal current/committed task resolution, final-owner validation, replace and submission resolution, value no-op suppression, prepare/abort/adopt against a committed-only document cache, nested-revocable drafts. | Deterministic native tests composing guard, core, guard commit and adopt only inside the test module: Pi tables 82/104/121/132/188/213/303/330/354 and documents 78/158/174/207/228/363/380/405/479/519, escaped/nested/returned/cross-thread draft revocation and no late initializer/mint after a backend call; ten private negative controls, including Pi's phase-before-ingress order and no caller hook reaching live drafts or cache keys. No public Session, line, async driver, publication or P closure. |
| DE02b2b / DE02b2a | First exposed internal Session over the same owner and core: owner line and close-predicate amendments, sync and async commit with handles and pending seal/drain, cancellation matrix, reentry, acknowledgement cuts, adopt-reject, publication and listeners, close and snapshot. | The first commit surface carries every coupled lifecycle/failure/publication behavior and none moves to b3; negative controls cover the line hold during drain, pending check, shield, queued-cancel cut, post-callback health check, adopt-fail→fail, internal-versus-host routing, line-drain close predicate and close-listener ordering. |
| DE02b3 / DE02b2 | Cumulative actual paired Session acceptance and the final DE02b delivery gate. | Every original DE02b cell proven cumulatively; DE02b waits b1a+b1b+b2+b3, DE02 waits a1+a2+b and DE03 waits b; all P OPEN. |
| DE03 / DE02b | Implement local SQLite backend/schema and persistent path lock, owner-only creation, query cursors and initial migration discipline. | Two processes/aliases cannot own one DB; SIGKILL owner then reopen; transactional schema failure/newer refusal, acknowledgement loss and integrity checks; no world-readable DB/journal/lock. Storage/private lifecycle docs. Split 2026-10-21 into DE03a–DE03d; DE03 completes only after its a+b+c+d children. |
| DE03a / DE02b | Private path ownership in `native/durable/ownership.py`: canonical path, never-deleted `<db>.lock` sidecar flock, in-process lock-inode registry, fork cleanup, explicit create/open modes, owner-only files and shared-filesystem refusal; minimal verified SQLite connection open/close in `native/durable/sqlite_connection.py`. No schema or Storage semantics. | Real child processes with bounded deadlines: a second process and an in-process second open are refused; symlink, relative, `..` and case-variant aliases meet the same lock and hardlinks are refused even unowned; fork and CLOEXEC leave no inherited lock holder; umask 0 and permissive umasks still give 0600 database, -wal, -shm, -journal and lock; existing group/other bits per D5; SIGKILL at idle with committed WAL then reopen, modes intact, lock free; filesystem classifier; stale lock diagnostics never decide; invalid input and `open` of a missing path leave no files. Linux evidence on the macmini (D10). Storage docs: files, modes, lock, aliases, unsupported filesystems, durability class. |
| DE03b / DE03a | Schema v1 (Pi v2 shape and names plus the Python deltas) in `native/durable/sqlite_schema.py`: append-only contiguous migration runner under the path lock, application_id, newer refusal, open-time integrity and the D6 quick_check policy. | Repeatable apply; contiguous and newer refusal; a failing synthetic migration rolls back version, probe table and data, then retries; a synthetic table rebuild keeps rows and indexes; SIGKILL mid-migration leaves the old version, then migrates; foreign, corrupt and missing-metadata refusals leave bytes unchanged. Migration discipline, integrity policy and the early DE04b cumulative format-bump obligation (implemented by DE04b1b) documented before current deltas; DE27 retains full histories/as-of/fork/migrations. |
| DE03c / DE03b | `SqliteStorage` in `native/durable/sqlite.py` with MemoryStorage's sixteen exact signatures, exact-type ingress, lossless BLOB payloads and ASCII-JSON identity columns, one authority lock and D4 COMMIT poisoning; backend-agnostic contract cases plus SQLite-only tests. | Contract cases over Memory, SQLite and SQLite across reopen; lossless values across reopen; retention rows; EXPLAIN index use; WAL truncate, page reuse and bounded size; real write refusals; acknowledgement loss (after-COMMIT cut, COMMIT raise per D4); SIGKILL before and after COMMIT; contended threads linearize; snapshot reads; integrity_check after every cut. Codec/identity, Memory-versus-Pi-SQLite and durability-class docs. Split 2026-10-22 into DE03c1 and DE03c2; DE03c completes only after its c1+c2 children. |
| DE03c1 / DE03b | `SqliteStorage` and `open_sqlite_storage` in `native/durable/sqlite.py` for table operations: commit of table writes, `mint_id`, conversation, entry (both forms), latest head marker, task and submission reads and scans, and `submission_by_request` through the `submission_requests` last-writer table (D2 (a)); exact-type ingress through MemoryStorage's admission helpers, lossless BLOB payloads and ASCII-JSON identity columns, one authority lock and Pi's commit order with D4 COMMIT poisoning. Document methods and document writes refuse with `UnsupportedStorageOperation` as an explicit interim owned by DE03c2. New backend-agnostic `tests/durable/storage_contract_cases.py`. | Contract cases over Memory, SQLite and SQLite reopened after every commit, every document-related case marked as owned by DE03c2 on the SQLite backends; the table differences D2 (a), (c), (d) and (f) follow Memory, each pinned by a contract case (the paired Pi SQLite probe stays an open obligation); lossless values across reopen; keyset cursors; EXPLAIN index use for table scans; contended threads linearize under one authority; snapshot reads; every statement inside `damage_as_corruption`; `integrity_check` after each test's writes. Linux evidence on the macmini (D10). Codec/identity and table Memory-versus-Pi-SQLite docs. |
| DE03c2 / DE03c1 | Documents in `SqliteStorage`: create, change and retire, current reads and scans and Pi's retention rows; the remaining failure and crash cuts. | Document contract cases over all three backends with DE03c1's DE03c2 markers removed; D2 (b) signed zero and (e) raw numeric versions pinned by native contract cases; actual paired Pi SQLite probes of D2 (a)–(f) remain DE03d acceptance; real write refusals (`query_only`, `max_page_count`, a foreign write lock); acknowledgement loss (an after-COMMIT seam; a raising COMMIT poisons per D4); SIGKILL before and after COMMIT; WAL truncate, page reuse and bounded size; integrity_check after every cut. Document codec, retention and durability-class docs. |
| DE03d / DE03c | Paired pinned Pi SQLite oracle and Session over SQLite: Node driver, comparison script, inputs and closed manifests, P43 workload and acceptance/coverage registration. | Closed recipes against pinned `openNodeSqliteStorage` with and without a driver-local reconstruction of Pi's pinned test reopen wrapper (call-time delegation; finally after every commit) plus Pi Memory, a closed list of actually observed divergences including paired D2 (a)–(f) probes, mutants and two fresh chains; Session over SQLite commit/reopen, acknowledgement cut, poisoning by real errors and close releasing the lock; a reviewed practical P43 budget; durable-reference SQLite section; DE03 completes only after a+b+c+d; all P OPEN. Split into DE03d1 and DE03d2; DE03d completes only after its d1+d2 children. Every original DE03d acceptance cell remains cumulative. |
| DE03d1 / DE03c | Session over the real SQLite backend in sync and async mode, with a bounded native current-base SQLite workload; no product integration or new Storage capability. | Commit/adopt/publish and fresh Session reopen; acknowledgement loss after real COMMIT; real SQLite errors poison Session; close and failing close release ownership to a bounded fresh process; inline close bounded and deadlock-free. Deterministic native workload with clean/unclean reopen, exact/page/current-base reads and all three upstream write shapes with persisted readbacks, measured practical budgets and explicit DE27 exclusions. All P OPEN; no paired oracle claim. |
| DE03d2 / DE03d1 | Closed pinned Pi SQLite/SQLite-reopen/Memory oracle, native SQLite/reopen comparison, D2(a)–(f) probes, mutants, manifest and final DE03d gate. | Existing bounded Memory recipes/exported conformance on both Pi SQLite variants plus valid paired D2(a)–(f) probes, exact observed divergence inventory, native==Memory where SQLite differs, stale/missing divergence rejection and designated mutants; native d1 Session/workload artifacts bound into the closure; two fresh cumulative reference chains. DE03d and DE03 complete only with all original cumulative acceptance; every P remains OPEN. |
| DE04 / DE03 | Add minimal typed document singleton/family/scopes, initial/base/update/retire and one mutation authority. | Atomic entries/tasks/docs; copied inputs/reads, invalid JSON, init/checkpoint exceptions rollback, terminal task doc retirement; shared mutable readers/writers race barriers. Document contract.  Split into DE04a and DE04b; parent completes only after both children and all original acceptance. |
| DE04a / DE03 | Typed singleton/family current-base documents across session/conversation/task scopes on the existing Session authority. | Copied definition/seed/address admission, memo cutoffs, atomic entries/tasks/docs, initialization/JSON/assembly rollback, task-terminal all-page retirement including unknown kinds, committed cache/publication identity and deterministic lifecycle/race proofs across Memory/SQLite/reopen. Current full bases and value-based suppression are interim; no checkpoint predicate claim. |
| DE04b / DE04a | Bounded early operation/delta/checkpoint prerequisite for the retained DE04 checkpoint acceptance. | Real normalized operation provenance, current delta support on Memory/SQLite with semantic format bump, checkpoint policy after validation with actual ops/counts, and second-predicate exception rolling back all coupled candidates. Full histories/as-of/fork/migrations remain DE27; no always-base callback substitute. DE04b completes only after DE04b1a and DE04b1b join end to end. |
| DE04b1a / DE02a1 | Additive private pure decoded operation validation, ownership and application over owned finite JSON; independent of typed runtime definitions. | Exact decoded r/s/d/a/t/p/m grammar, path and array safety, UTF-16 prefix removal, detached atomic batch application, pinned actual Node comparison and controls. No tracker, draft, Tx, Storage, schema or checkpoint integration. |
| DE04b1b / DE04a + DE04b1a | All remaining normalized operation tracking, current Memory/SQLite deltas and semantic schema2 persistence, real checkpoint callbacks and linked publication. | Empty versus structural no-op normalization, current tail replay/count/reset, callback and backend atomic rollback, immutable policy/publication batches, unload/reopen and paired proofs. Further subdivision only immediately before code; full history/as-of/fork/migration remains DE27. |
| DE05 / DE04 | Add minimal versioned task definition/runtime and sequential reservation/checkpoint/recovery scheduler, no dispatch on open. | Kill before reservation, after running intent, before/after checkpoint; repeated resume stable; invocation cannot write after completion; storage error seals all later work. Task/effect contract. |
| DE06 / DE05 | Production checkpoint/request/outcome codecs using canonical `agent/messages.py`, `agent/request.py`, provider and tool outcome DTOs; extract audited finite encoding rather than importing scripts. | Field-level serialized equality including compare=False fields, raw whitespace, absent/null, thinking/redaction/signatures, images, IDs, structured/details/control/terminate and all usage/cache/cost units; reject malformed/newer data. Codec/provider privacy docs. |
| DE06a / DE02a1 | Additive versioned finite canonical message/request/provider and tool result/outcome codec over existing native DTOs in `native/durable/canonical_codec.py`, with no runtime hookup. | Explicit field fidelity including compare=False, ordered rich blocks, raw arguments, sentinels, IDs, timestamps, usage/cache/cost and frozen request catalogs; exact safe ingress, detached output, malformed/newer/unknown-field refusal and live credential/callback refusal. Native field tests and codec privacy docs; no cumulative DE06 or P closure. |
| DE06b / DE05 + DE06a | Integrate the canonical codec with actual versioned task checkpoints and prepare/request/outcome envelopes after the task runtime exists. | Retain every original DE06/P15/P16/P19 acceptance cell cumulatively; checkpoint phase/version validation, actual runtime recovery and provider privacy evidence. |
| DE07 / DE06 | Add durable generation prepare/request/settlement phases using native request snapshot and `ProviderTurnExecutor` transport seams, fixed/pure adapters only; share loop policy rather than run entire product loop on resume. | Assistant+tool admissions atomic; response committed before publication, stable request cutoff/provider identity, no duplicate committed usage; crash during provider/after response/before & after commit. No claim of durable partials yet. Generation contract. |
| DE08 / DE07 | Add sequential native tool intent/result state with stored prepared args and safety intersection; controlled blocked uncertainty for this milestone. | Pending runs once; safe explicit fixture retries; unsafe intent/effect cuts block even if effect absent; result-commit loss reuses receipt; no next provider call on block; current/stored policy drift fails closed. Tool safety docs. |
| DE09 / DE08 | Add committed completed-message/outcome projection and reconnect/drain sequence to Python observers, replacing state-first mutation for this mode. | Independent reader proves commit-before-callback; kill after commit/before callback and ambiguous callback ack produce no repeated effect; listener failure isolated. Document coalescing/dedupe and completed-only limit. |
| DE10 / DE09 | Add active-execution compatibility and redacted inspect/recovery resolution operations, supported version rules and terminal read-only access. | Changed model/env/workspace/tool/codec refuses before effect; terminal inspect works without credentials; resolution vs late result/abort race serialized; retry is a new authorized attempt, never old success forgery. Operations/privacy docs. |
| DE11 / DE10 | Expose opt-in new durable Python product-session factory plus explicit reopen/close, with one path authority; adapt existing `coding/product_session.py` seams. Existing defaults stay until DE44. | New session + one request + hard kill + reopen finish; executable tests reject unsupported compaction/tree/fork/reset/queued input/extension injection before admission, with no JSONL file or volatile queue claim; no accepted unpersisted input or workflow archive payloads; errors/close leave all locks/workers correct; legacy SDK behavior stays green. `sdk.md`, `session-storage.md`. |
| DE12 / DE11 | Expose opt-in headless native start/resume/inspect via existing CLI composition, graceful disconnect/exit and documented state locations; real-provider acceptance probe when auth available. | Real subprocess SIGKILL at assistant/tool/final cuts, read-only/missing path no accidental creation, terminal exit/resume and counted uncertain paid request; fake SSE/transport wire cases always required. CLI probes assert the same Milestone A unsupported-capability errors, disabled auto/overflow compaction and absence of competing stores/volatile queue claims or accepted unpersisted input. Record live credential blockers without a false live claim. CLI help/guide/changelog. |

### Milestone B — complete scheduling, queues, policies and observation

DE13–DE28 close deliberate Milestone A limits. They are required for full parity.

| ID / dependencies | Concrete change and seams | Acceptance / crash cuts and documents |
| --- | --- | --- |
| DE13 / DE12 | Implement **Pi interrupted-error continuation** for unsafe recovered top-level model-issued tool calls, retaining uncertainty diagnostics; replace initial blocking default for supported tasks. | Comparative fixture: kill before invocation and after unsafe append; zero/one actual effect respectively, exactly one interrupted error, next provider sees it and completes, repeat resume never appends; old milestone blocked records upgrade explicitly. Missing/unsafe-current implementation never reruns. Interruption/recovery docs. |
| DE14 / DE13 | Complete task migration/definition absence/newer refusal, progress-based restart limits, parked retry, task fault/orphan cleanup and inspection. | Reference recovery cases and upgrade fixtures; close does not increment, repeated crash does, document chatter does not reset; abort blocked becomes orphaned, missing code alone never settles. State-machine/migration docs. |
| DE15 / DE14 | Add immutable task/conversation ownership, durable waiting/completing, candidate-state child admission, allSettled/failFast and wait-cycle detection. | Crash held completion/cascade/final commit; no owner terminal before ordinary work drains; child created in finishing candidate holds/rejects as appropriate; wait cycles reject atomically; background boundaries. Ownership spec. |
| DE16 / DE15 | Complete durable cancellation/abort handlers, invocation gates, bottom-up cleanup, wait cancellation, close/admitted commit ordering, restart abandonment and process groups. | Barrier races abort vs result/new child/close/registry; no post-mark run writes; foreground cleanup joins before lock release; noncooperative tool isolated or explicit blocker. Kill parent while subprocess alive and verify no unowned mutation claim. Cancellation docs. |
| DE17 / DE16 | Add sleeping/wake host port, persisted generation retry backoff, deferred poll/cancel, timer failures and parked work wake behavior. Integrate native retry/overflow classifiers. | Fake clock early/late/multiple reopen, no busy loop, bounded layered retries, fixed due times, abort asleep/polling, unknown deferred model; host onWake after commit only. Retry/deferred/host settings docs. |
| DE18 / DE17 | Durable input/write submissions, request-ID scope/dedupe, settlement objects and withdraw/status/wait APIs. Adapt `coding/session_controller.py` admission boundary without dual queue ownership. | Competing duplicate submit plus commit-ack loss yields one record/run; type conflict rejects; exactly one done/unanswered settlement, cancelled waiter leaves shared work. Submission API/docs. |
| DE19 / DE18 | Durable inbox, steer/follow-up modes, deterministic postTools/final boundaries, passive writes, failed-run policy and abort queue behavior. | Reference ordered traces for all/one, failure keep/continue/withdraw, new input after failure, queued reset/display-only write, crash boundary and concurrent queue/cancel. Queue settings/CLI semantics docs. |
| DE20 / DE19 | Complete entry/context head/edit/contribution derivation, system named sections/tool deltas, missing-tool-result synthesis and historical cutoff reads. | Context fixtures with interleaved user/tool entries, resets and edits; system/tool baseline matches reference; malformed correlations fail before provider. Reuse existing cross-provider projection tests. Transcript/system docs. |
| DE21 / DE20 | Durable extension registry/agent configuration, selected tools/modelTools/deferred/callers, ordered wrappers/sections, per-conversation model/env and host settings resolution. | Snapshot vs live getter boundaries, duplicate/reserved definitions, offered-at-request vs enabled-at-execute, unavailable tool error; model/tool changes cannot escalate prepared request. Extension/settings docs. |
| DE22 / DE21 | Implement explicit durable hook composition and first-writer memos; adapt supported native extension hooks, classify unsupported effects. | Crash hook before consuming commit, first-writer race, throw/report/block composition, cancel propagates; side-effect fixture demonstrates documented replay rather than fake exactly-once; unadapted hook never silently runs in durable recovery. Extension replay guide. |
| DE23 / DE22 | Registry/resource reload and versioned task handover, in-flight snapshot retention, extension removal/reinstall and document-token compatibility. | Reload during long tool/phase, replacement maintains order; missing task blocks then unblocks; failed migration retried only for changed definition; old resources released after joined use. Reload/version docs. |
| DE24 / DE23 | General tool repair/schema/final-arg checkpoints, nested keyed admissions/results and structured output/diagnostics/control mapping, native extension/MCP adapters. | BeforeTool not rerun on execute recovery; key name/args divergence before effect; stored/current safe intersection; replay result/error schema; unsafe nested intent/effect interruption yields a stored interrupted error to the caller with no reexecution, repeated resume preserves effect counts and continuation; nested controls do not affect parent, nested usage once. Tool extension/MCP docs. |
| DE25 / DE24 | Complete concurrent/sequential tool-round scheduler and executionMode override, nested abandonment, addTools/terminate/handoff controls and round settlement. | Out-of-order completions retain call-order view; any sequential declaration serializes; abort prevents pending launch; kill mid-round never repeats committed effects, fail/return-error ownership differs. Tool-round docs. |
| DE26 / DE25 | Committed streaming partials and tool output/details/diagnostics/usage throttling, flush and bounded live slots. Integrate canonical event/partial adapters behind commit projection. | Kill each throttle/flush/terminal boundary; aborted partial retained before provider reissue; no stale partial after result; slow observer/backpressure and callback failure; exact cache/usage counts. Streaming/output docs. |
| DE27 / DE26 | Complete document histories/families/checkpoint policies/access-driven migrations, definition-free copy/retirement and bounded document/context retention. | As-of/base/delta equivalence at every commit, zero-retention vs cache, unknown versions/families preserved, failed migration/no-op migrated base, reclamation never deletes addressable history. Document/version/performance docs. |
| DE28 / DE27 | Document/conversation views, watch baseline+subscription, event streams and live task graph as convergent adapters to committed state. | Atomic aggregate baseline under concurrent commit, reconnect/coalescing/end reasons, listener isolation, blocked/sleeping/completing graph, no missed committed baseline or mixed revisions. Python observation/task inspection docs. |

### Milestone C — full workflows, Python codemode and host facilities

| ID / dependencies | Concrete change and seams | Acceptance / crash cuts and documents |
| --- | --- | --- |
| DE29 / DE28 | Durable manual/background/blocking compaction tasks, pinned summarizer request, retry/usage/failure and stale summary submissions; reuse native compaction policy/extract pure selection. | Compare range fixtures including system/thinking/large tool messages; crash summary before/after placement, competing newer cut makes old summary stale; threshold/overflow/decline/cancel and ownership behavior. `compaction.md`, settings. |
| DE30 / DE29 | Conversation fork/context-at-entry and per-doc fork policies; reset/handoff and transcript/tree semantics. | Child copies through concrete entry and final commit doc point, no tasks/inbox/spend duplication; unknown definitions/versions survive; reset vs queued summaries/user input races cannot resurrect cut history. Tree/fork/reset docs. |
| DE31 / DE30 | Native subagent tool on owned conversations/tasks, foreground/background routes and conversation selection APIs, completing tool-result and owned-child cleanup. | Parent kill/reopen/abort while child provider/tool runs; unsafe child interrupted then continuation, no duplicate child effect; owner waits drain; background survives ordinary abort; completed child can be queried. Subagent/task graph docs. |
| DE32 / DE31 | Portable execution env and host capability conformance adapters: stable fs namespace, bounded readers/listing/watch readiness/overflow, shell argv/string, cancellation, windows/spill and cleanup. Reuse native tool filesystem/process seams. | Local temp env + fake remote env conformance; watch-before-load race, symlink/path replacement limits documented, per-command kill leaves siblings, spill/recovery lifecycle. Env/resource reload docs. DE32 completes only after both DE32a and DE32b. |
| DE32a / DE00 | Pure bounded streaming line-range scanner and immutable seven-field LineScan; strict exact built-in ingress and pinned replacement UTF-8 byte counts, with no filesystem or runtime integration. | Pinned Node paired 5000-seed corpus across arbitrary chunking, all seven offsets/counts, file-leading BOM only, decoder flush, lifecycle and generator memory bounds; designated source mutants. Scanner contract docs; no P closure. |
| DE32b / DE31 + DE32a | All remaining DE32 environment adapters: stable filesystem identity/path and local/fake-remote readers wired to DE32a, bounded listing, watch readiness/overflow, shell argv/string, isolated cancellation, windows/spill and recovery cleanup. | Local temp env + fake remote env conformance; watch-before-load race, symlink/path replacement limits documented, per-command kill leaves siblings, spill/recovery lifecycle. Env/resource reload docs. Retain every original DE32 acceptance including stable-file scanner integration. |
| DE33 / DE32 | Close built-in read/write/edit/bash/available PowerShell semantics, per-file mutation ordering, bounded rich results and optional Python image processor parity. | Upstream read differential fixtures, edit conflicts and file queue crash; damaged/EXIF/oversized images, MIME/byte limits, decode and recovered attachment fidelity; no general write marked safe. Tool/image docs. |
| DE34 / DE33 | Tool-search extension/activation and codemode discovery/namespace/schema snapshots; adapt existing `coding/codemode_discovery.py` and extension/MCP exposure. | BM25/filter/limit/deferred activation reference fixtures; discovery resumes from start snapshot after registry drift; hidden internal tools never leak to search. Discovery/codemode docs. |
| DE35 / DE34 | Python WASI deterministic recovery capability and journal protocol foundation in `codemode/{host,worker,prelude}.py` plus durable task docs: start pin, clock/seed/fingerprint/deadline and issue acknowledgements. | Determinism probes for clock/random/import/iteration/input globals, all supported Python versions/guest pin; kill before first admission/after journal commit/before delivery and replay exact issue trace. Fail before effect on unsupported nondeterminism. Codemode recovery spec. |
| DE36 / DE35 | Deliver/replay journal in order, catch-up barrier and keyed nested-call reattachment; divergence/noncatchable guest stop and owned-call cleanup. | Interleaved out-of-order completions, journal ack lost, call issued but not delivered, changed name/args/count/fingerprint/global, end-too-early; assert no new effect before every historical gate matches. Restart stress uses real guest. Codemode recovery guide. |
| DE37 / DE36 | Atomic successful store/result and rewindable/fork/as-of store; stable output/spill/image directories, recorded globals/result mapping and timeout/module upgrade outcomes. | Kill store/result transaction, repeated resume writes once, failed script no store change, fork isolation, output file overwrite/reuse/error and expiry without new calls. Replace custom-entry-only durable path. Codemode/storage docs. |
| DE38 / DE37 | Durable codemode models catalog, classify/image internal nested tools, secret stripping, max-four concurrency, structured errors/usage/image notes and cancellation. | Fake provider model operation matrix, paid-effect kill/interrupted uncertainty, catalog header redaction, slot-wait not launched after guest end, usage once; live provider capability probe if auth available. Model/codemode capability docs. |
| DE39 / DE38 | Content-free telemetry schema/report/trace/metric adapters and host callback error containment, optional exporter integration using current telemetry architecture. | Compare submit-root/phase/entry/replay/report traces and active task gauges at terminal/close/reopen; secret/prompt/tool-output sentinel absence; throwing exporter cannot alter correctness. Observability docs. |
| DE40 / DE39 | Portable JSONL storage alternative with main markers, sidecars, torn-tail recovery, detach, reclamation and explicit fsync guarantees; same storage port, never dual writer. | Run entire storage conformance plus sidecar/marker/flush/rename/unlink crash cuts, missing confirmed data corruption, failed reclamation and stale descriptors. Compare memory/SQLite/JSONL outcomes. Storage backend docs. |

### Milestone D — native adoption, migration and final parity gate

| ID / dependencies | Concrete change and seams | Acceptance / crash cuts and documents |
| --- | --- | --- |
| DE41 / DE40 | Unified native product persistence adapter and explicit legacy JSONL-tree migration/export/import; preserve existing IDs/branches/compactions/custom entries/images without inventing past task execution. | Copy to private staging, validate, atomic activation marker/pointer under source+destination locks; SIGKILL every migration cut leaves old authoritative or new authoritative, never both writable; repeat idempotent; rollback selects one authority. Existing strict tree reopen/export fixtures. `session-tree.md`, `session-storage.md`, migration guide. |
| DE42 / DE41 | Terminal integration via existing reducer/projection/session controller: durable queue/progress/retry/compaction, reopen picker, subagent conversation/task inspection, recovery notices and clean disconnect. | Linux/macOS real PTY: kill streaming/tool/subagent, reopen renders committed state; no duplicate visible results, safe abort/EOF/terminal restoration, tree/fork workflow compatibility; isolate theme/HOME/state and restore pi theme. TUI/help docs. |
| DE43 / DE42 | Native JSON/RPC and public SDK parity for submission/status/watch/recovery/task/queue/tree controls with existing protocol compatibility. | RPC disconnect/reconnect under active work, stable sequence keys, abort/retry/compaction/config while running, bash and nested output, SDK same store then TUI reopen; standard automation comparison gate stays green. SDK/RPC docs. |
| DE44 / DE43 | Stable integration: make durable execution the supported native default after campaign gate evidence; remove temporary opt-in restriction/duplicate execution ownership and migrate old entry points directly. | Exercise every supported start/continue/resume/in-memory/test injection path; legacy files stay explicit until migrated, default never auto-imports private raw data into workflow archive; unsupported extensions/capabilities show actionable error. Update all architecture/onboarding/default/storage/release docs. No deprecation aliases. |
| DE45 / DE44 | Independent end-to-end acceptance audit, final comparison matrix/evidence and release readiness. Fix discoveries only in scoped follow-up slices with their own checks/review/commit/push. | Every P row has passing comparison/test/review/commit evidence; compare all included exported surface groups, subprocess crash matrix, provider wire families, real WASI, cross-mode migration and bounded performance; no open correctness or required-parity rows. Final independent Opus review and main CI/ref confirmation. |

DE44's default switch is one small final adoption change, **not** permission to
postpone regression testing until it. Every earlier integration slice must keep
the existing native path releasable and test its affected boundaries. The
comparison ledger can become complete before DE44, then DE44/DE45 rerun the
whole goal gate on the actual default. Full parity is not declared at DE12,
DE28 or DE40.

## 6. Verification and measurable completion

### Reference comparison, not self-grading

DE01 generates finite, sanitized traces from pinned Pi with fake models/envs and
from Python against equivalent scripts. Canonicalize only declared representation
differences: ID remapping, clocks, async scheduling of independent events and
Python field names. Preserve causal ordering, entry content/identity, effect
counts, settled outcomes, partials, snapshots, queue selection, usage, waits and
ownership. Keep rich JSON field assertions; dataclass equality omits important
compare=False fields. Include negative fixtures proving the oracle detects a
lost field, repeated effect, post-abort write and stale head. Reference tools
are verification only; product code never imports/runs them.

Storage/env conformance cases are ported from upstream testing exports, with
source names and fixture provenance. Each matrix row points to concrete Python
tests and source-derived reference cases. A different-family reviewer checks
whether the reference adaptation actually tests the contract. A mismatching
required row blocks parity; an approved platform/language exclusion must point
to section 1 rather than a broad catch-all allowlist. Future upstream changes
are reported separately, not mixed into the frozen gate.

### Crash and race cuts

Use actual subprocess death (`SIGKILL` as well as deterministic hard exits),
barriers and independently opened readers. For each important transition test:
queued admission; intent committed before invocation; effect before result;
result before commit/ack; commit before publish/observer ack; partial throttle
and terminal flush; abort mark before signal; child completion/owner hold;
retry/sleep/poll deadline; queue boundary; fork/migration activation; codemode
journal commit/delivery/ack/new-call gate; JSONL marker/reclamation. Reopen and
resume twice, verify stable final states and actual effect counts. Do not equate
an admitted attempt counter with a network/effect invocation counter.

Inject real write refusals plus before/after-COMMIT exceptions, failed reads,
failed ID allocation/close, interrupted schema migration and malformed/corrupt
confirmed records. Acknowledgement loss must yield either recovered committed
state or a safe no-result state, never duplicate successful side effects. Every
unsafe uncertainty case asserts no invented success. Concurrent barriers cover
all readers/writers of session state, not just the scheduler. If two reviews find
the same shared-mutable-state defect class, stop implementation and correct the
written contract before more code.

### Provider, privacy and operational evidence

Offline native wire tests cover exact-target vs changed provider/API/model
projection, all canonical rich fields, cache identity, usage normalization,
compaction/retry/deferred/error paths and credentials reconstructed from auth.
One supported live provider exercises start/stream/kill/reopen/tool/retry/cancel
and cache/session identity before stable default adoption. Record real attempts,
provider request IDs and cost uncertainty without secrets or raw payloads.
Unavailable other-provider credentials remain an explicit live-evidence limit;
they are not a reason to skip their deterministic wire/conformance tests. A
missing capability needed for the one supported live path blocks that acceptance
item; the coordinator reports it instead of fabricating a pass.

Product SQLite/JSONL, attachments, spill files, task checkpoints and migration
staging contain private full content; directory/file/journal permissions and
backup/export/delete/retention behavior must be explicit. Existing global
workflow archives keep their immutable metadata-first lifecycle; never sync a
live execution DB through finalized-record recipes. No auth headers, credentials,
raw tool errors or full transcripts in telemetry, summary-safe workflow events,
review status or committed fixtures. Raw product state may be read only where
the user explicitly selected that session. Read-only inspection does not create
stores or force unavailable code migrations.

Benchmarks compare useful semantics: long histories, high-fanout/parallel tasks,
document cache at retention zero/default, base-plus-tail reads, event coalescing,
large output and codemode replay. DE01 records hardware/reference workloads;
each owning slice sets and reviews a practical memory/latency/recovery budget
from those baselines. Avoid arbitrary claims of equal TS throughput. An
unbounded scan/replay tail on each provider turn or retained terminal task graph
is a correctness/maintainability issue to fix before final acceptance.

### Historical plan-only validation

The original planning change ran whitespace, documentation build/rendered link
checks and focused documentation checks that covered changed contracts. It did
not repeat the approximately 16-minute full implementation check solely for new
prose. The future DE00 implementation/evidence import and **every subsequent
implementation slice** still run the required full checks. Validation logs use
one unique scratch directory per run; retain command, exit status, full output,
base/head and review verdict, then record only summary-safe outcomes in archive.

## 7. Fresh-session execution protocol

The coordinator's fresh-session handoff must carry the reviewed plan/audit paths,
exact upstream ref, baseline commit and its local
`refs/pipy/baselines/durable-2026-10-19` locator, current dirty inventory and
plan-review log. Verify the locator resolves to
`16e65c41c25ed6b4495d5356a3ce8d3b30ba8a63` before using it.
It uses the `multi-agent-coordination` skill and any work-ledger requirements
applicable to the future session; this planning writer starts no campaign or
work-app mutation. Implementation workers are **GPT-6.1 Sol HIGH**, as selected
by the owner. Assign one writer for one bounded DE slice at a time on `main`;
parallel workers may investigate or review read-only, with file ownership and
explicit dependency barriers. A planning/implementation worker cannot grade its
own slice or final parity. Do not silently switch model/provider/effort.

Before the first commit, preserve and classify all dirty state, confirm reviewed
plan changes, and stage only the slice. DE00 imports the existing experiments
rather than losing them in a fresh checkout. No branch/worktree policy exception
is implied. Never discard someone else's staged changes or include unrelated
changes as a convenience. If pre-existing unrelated work prevents a meaningful
green baseline, document the exact failure and scoped remedy instead of folding
it into a durable slice unnoticed.

For each slice: read its dependencies and source contracts, write the concrete
small work order, implement/tests/docs, run focused and full gates, obtain fresh
read-only **Opus 5.5 HIGH** review via `claude-review-loop` and
`cross-agent-review-cycle`, address material correctness findings, then commit
and **push main**. Record pushed SHA, tests and review in the acceptance ledger
before advancing. No ad hoc dollar budget cap or write-capable reviewer bypass.
If the approved reviewer/model/auth is unavailable, **fail closed**, preserve the
work, report the blocker and stop dependent delivery. Tool/operator failure is
not an operator override pass or permission to substitute another reviewer.

There is no fixed review/retry round count. Follow value-driven cycles: continue
while findings materially improve correctness, maintainability, tests or durable
docs; record why another round is valuable. Stop on clean evidence or the
canonical diminishing-return signals. Precision questions in a plan belong to
the owning slice; unresolved implementation correctness remains blocking. Shared
mutable state defects are never deferred. The campaign's mandatory
**different-family CLEAN** gate is retained: if material findings or a non-clean
verdict remain when value diminishes, report a blocked slice; do not relabel an
advisory result CLEAN. No redundant pass after a clean reviewed artifact without
changed scope/risk. The plan review itself may close on a clean first pass.

After each slice, update docs/changelog/evidence, append summary-safe role,
review-outcome and validation events to the active archive, commit and push only
when gates pass. After final DE45, require the entire matrix and actual native
default path to pass, confirm main/remote CI and report exclusions/live limits
separately. Do not substitute a generic parity score or existing MCP goal-check
for this durable feature's acceptance.

**Owner decisions of 2026-10-21 (appended; earlier history unchanged).** From
DE02b1a onward, Claude Code `claude-opus-5-5` HIGH implements and repairs, and
Codex `gpt-6.1-sol` HIGH reviews through `codex-review-loop`. This supersedes the
GPT-implements/Opus-reviews assignment above for later slices only; earlier
slices remain recorded as GPT-implemented and Opus-reviewed. GitHub CI is no
longer a delivery gate (Actions quota): delivery requires local `just check`, a
Codex CLEAN review and the pushed SHA verified with `git ls-remote`. Docs for
DE02a2b and later slices claim no CI result. The "no branch/worktree policy
exception" rule above is withdrawn: `AGENTS.md` now allows short-lived branches
and worktrees, so independent slices may run in parallel and merge to `main` in
turn. Dependent slices still wait for their dependency to land on `main`.

**Owner decisions of 2026-10-21, later the same day (appended; earlier history
unchanged).** New implementation dispatches run Claude Code `claude-opus-5-5`
at **MAX** effort instead of HIGH; repairs and review continue as above. Review
and repair cycles still run per slice under the value-driven rules; the owner is
asked only when a slice would exceed 12 review rounds.

## 8. Effort, decision points and stop conditions

The earlier **2–4 engineering-week** quote sized only the bounded opt-in
completed-message headless feature. It is historical experimental guidance, not
an estimate for this plan. Adding a production task kernel and review gates may
make even Milestone A **4–7 engineering weeks** at current low-to-medium
confidence. The full campaign is approximately **22–38 engineering weeks** of
implementation/integration/verification, plus contingency for WASI determinism,
extension migration and native mode adoption. A single writer/reviewer delivery
lane suggests roughly **6–10 calendar months**; read-only investigation can
reduce waiting, but shared mutable runtime work and serial gates do not scale
linearly with more agents. These are planning ranges, not delivery promises.
Re-estimate after DE12 and DE36 from measured slice throughput and defects.

The main architectural decisions are made here: Python-native scheduler and
semantic parity, SQLite authority first, complete alternative backends later,
conservative first milestone followed by Pi interrupted-error continuation,
versioned explicit extension contracts, deterministic Python replay and a final
native default/migration step. No unresolved owner preference is necessary to
begin DE00 after the reviewed plan is approved for future execution. Review may
identify a genuine scope decision; record it explicitly rather than making up
an answer or stopping for routine file/API naming.

Stop and report a concrete blocker for: unavailable mandated reviewer/auth;
unresolved correctness or shared-mutable-state findings; inability to reproduce
required reference behavior; an unsupported deterministic guest path that would
admit duplicate effects; unpreservable existing private data; or a live acceptance
requirement lacking the needed authorized provider/capability. Preserve work and
logs. Routine implementation uncertainty calls for a smaller slice/probe, not a
silent scope cut or an unattended endless review loop.


### Python Storage ingress and authority

Every public document-point ingress admits only exact built-in `str` value
`current` or exact built-in positive safe `int` Seq before any value comparison,
coercion, hashing or diagnostic representation. Subclasses and foreign Python
objects are invalid input, not alternate JSON/Point representations. No arbitrary
caller hook may run under the Storage authority. Owned query/address/write DTOs
admit their values before backend use. The same close linearization and every
method's closed refusal remain; no reentrant lock or calling foreign hooks outside
the lock substitutes for admission. Valid numeric document points remain the
declared unsupported historical boundary in a2b. Raw document content.version is
a separate finite-number role:0/-1/1.5 are retained by raw Storage, independent of
later positive-safe defineDoc definition validation. Original future rows and
cumulative Session/document requirements are unchanged.


### 2026-10-22 implementation ownership and DE03c1 delivery

DE03c1 landed on `main` and origin at
`1563bb6871f869abfb93d704b01c637b2b2ef7c1`. Claude Opus 5.5 implemented;
Codex `gpt-6.1-sol` HIGH reviewed: round 1 three Warnings, round 2 one Warning
caused by that repair, both repaired, then round 3 CLEAN. Local `just check`
reported 15,184 passed, 2 skipped, 54 xfailed and the known docs-preview
load flake; isolated `tests/test_docs_build.py` passed 16/16. No CI result is
claimed. The 54 strict xfails and seven pending document ports belong to c2.

From DE03c2 onward the owner's current instruction is Codex implementation and
repairs, with independent Claude Code `claude-opus-5-5` HIGH review solely
through `claude-review-loop`. Earlier roles remain historical: GPT implemented
DE00–DE02a2b; Claude Opus 5.5 implemented DE02b1a–DE03c1 with Codex
`gpt-6.1-sol` HIGH review. Work proceeds on `main`, with independent parallel
slices allowed in their own temporary worktrees. Local `just check`, strict
docs build and rendered link checks for changed documentation, independent
CLEAN of the exact tree, push and `git ls-remote` confirmation are the delivery
gates; GitHub CI is not a gate. The owner permits up to twelve review rounds
per slice before asking again, with the value-driven stopping rule still binding.

The DE03c2 work order reconciles its earlier acceptance wording: c2 pins D2
(b) and (e) through native Memory/SQLite/reopen contract cases; actual paired
Pi SQLite probes of all D2 (a)–(f) belong to DE03d. Nothing removes cumulative
DE03 acceptance or closes a P row.

### DE06 pre-runtime codec split (2026-10-22)

DE06a depends only on delivered DE02a1 and the existing canonical DTOs; it is
an additive codec without scheduler or Session integration. DE06b depends on
DE05 and DE06a and owns the actual task checkpoint/envelope integration. DE06
remains cumulatively incomplete until both children and every original
acceptance cell pass; all 43 P rows stay OPEN. Current owner assignment: Codex implements and repairs;
Claude Code `claude-opus-5-5` HIGH reviews through the installed harness. Earlier
GPT and Claude implementation/review provenance above remains historical.

DE03c2 subsequently landed at `a2c4c6d2595e7c782d139b0380f918ac24887efd`.
DE03c is delivered. DE03d1 subsequently landed at `8ab38d20`; DE03d2 landed at `2dba6008`
and cumulative DE03d/DE03 are delivered. DE06a landed at `caf4d34e`;
DE06b remains OPEN and cumulative DE06 remains IN_PROGRESS. All43 P rows stay OPEN.


### DE04 coupled current-document split (2026-10-22)

DE03 completed with DE03d2 delivery `2dba6008`. DE04a implements typed current
document behavior and task lifetime on the existing authority; DE04b retains
all checkpoint rollback acceptance through a bounded early real operation/delta
prerequisite, without reassigning it to DE27. DE04 completes only after both
children; DE05 still depends on complete DE04. All43 P rows remain OPEN.

DE32a subsequently landed at `52ecc1fc`; DE32 remains IN_PROGRESS and DE32b
OPEN. DE04a subsequently landed at `e6c1239d`, with its exact reviewed tree,
green full check and push/remote confirmation. DE04/DE04b/DE04b1a remain
IN_PROGRESS; DE04b1b is OPEN and DE05 still waits complete DE04.

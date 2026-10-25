# Durable execution upstream audit

Audit date: 2026-10-19. This is a source audit for the
[implementation plan](2026-10-19-durable-execution-plan.md), not a claim that
pipy already implements the audited behavior.

## Reference and evidence

The reference is the local `/Users/jochen/src/pi-mono` checkout at
**`2ec55d808d455b1a64a2bf7888198207a096120a`**, subject
`feat: Durable and pi-ai telemetry; Context.telemetry is always a span`.
`git status --porcelain=v1` was empty. No fetch, checkout, installation, or
mutation was performed there. `packages/durable/package.json` names
`@earendil-works/pi-durable` **1.3.0**. Sources below are relative to that
checkout at that commit; the normative reference is
[durable/docs/spec.md](https://github.com/earendil-works/pi/blob/2ec55d808d455b1a64a2bf7888198207a096120a/packages/durable/docs/spec.md).
Source and tests, rather than a version number alone, define the frozen target.
An upstream update requires an explicit delta audit; it does not silently expand
this campaign.

The experimental durable coding-agent TUI is **absent at this reference**.
`git log -- packages/coding-agent/src/experimental/durable` identifies its
removal in `7472cda0d6e51dfbf53c03f46b47aa42ac64bf64` (`remove the experimental
server stack and the client, protocol, and server packages`). The historical
[README at 5609b0d6c](https://github.com/earendil-works/pi/blob/5609b0d6c/packages/coding-agent/src/experimental/durable/README.md)
was inspected with `git show`, along with the current experimental source
inventory. It demonstrates SQLite session ownership, committed partials,
conversation switching, subagents, queueing and a task panel. It is historical
integration evidence, not a second current package or a pixel-parity target.
The shipped pipy terminal, SDK and RPC must nevertheless work with the new
runtime; removing that work from the execution goal would be a scope reduction.

## Published package surface inventory

The package export map and `src/index.ts` were read directly. This inventory
groups related types with the behavior they expose; Python does not need the
same TypeScript spelling, branded numeric types, or helper identity functions.
Every group is assigned acceptance rows in the plan. Internal implementation
files do not become additional public APIs merely because they exist.

| Surface | Published operations and records | Source / normative section |
| --- | --- | --- |
| Main session kernel | `createSession`; commit/close lifecycle; scans and detached reads; immutable conversations, entries, tasks and submissions; typed IDs, sequences, cursors, pages, commit publications and storage writes; `ROOT_CONVERSATION_ID`; `ConversationBusy`, `ReadAfterWrite`, `SessionFailed`, `StorageRequestError` | `src/index.ts`, `src/types.ts`, `src/session/{session,transaction}.ts`; spec §§1–4, 10, 12 |
| Typed documents | `defineDoc`, `defineDocFamily`; singleton/family tokens; session/conversation/task scope; latest/rewindable history; initial/current/as-of fork; initialization, mutation, observation, bases, retirement, versions and migrations | `src/documents.ts`, `src/session/{forks,observation}.ts`; §§3–4, 9 |
| Entries/context | `defineEntry`; user, system, assistant, tool-result, compaction and reset tokens; entry drafts/records, context edits and contributions | `src/entries.ts`, `src/harness/context.ts`; §§2.1, 8.1 |
| Tasks | `defineTask`; versioned input/checkpoint/result, phase/abort handlers, memos, ownership, joins, reports, sleeps, task runtime, outcomes and terminal records | `src/tasks.ts`, `src/harness/scheduler.ts`; §§5, 8 |
| Harness | `Harness.open`, `resume`, `wake`, root/get/create conversation, get/inspect/retry/abort/wait task, submission/abort submission, idle, usage and task graph/watch | `src/harness/harness.ts`, `types.ts`; §§2.2, 5–6, 9.5 |
| Conversation/submission | agent/configure, commit, context at entry, entry scans, submit/status/wait/withdraw, fork, compact, reset/handoff, abort including background option, idle and view/watch | `src/harness/{harness,submissions,inbox,view}.ts`; §§2.2, 6 |
| Built-in state/task tokens | `AgentDoc`, `InboxDoc`, `LiveDoc`, `ProviderDoc`, `UsageDoc`, `NestedCallDoc`; `GenerationTask`, `ToolTask`, `CompactionTask`; their inputs/checkpoints/results | `src/harness/{agent,inbox,live,provider,usage,tool,generation,compaction}.ts`; §§8–9 |
| Extension authoring | `defineExtension`, `defineTool`, `hook`, `section`, `wrapTool`, `wrapSection`, `createRegistry`; registry snapshots; selected extensions, tools, model tools, sections and ordered wrappers | `src/harness/{define,registry,prompt,agent}.ts`; §§7.1–7.5 |
| Settings/host | retry, compaction including model overrides, progress, tool execution, queue modes, retention, failed-run policy and restart limit; model/settings getters, env factory, creation hook, clock/timers/wake and reports; exported default policies | `src/harness/{types,agent}.ts`; §§2.2, 5.6, 7.1, 10.1 |
| Provider execution | pinned model/thinking/stream options and cutoff; persisted provider session identity; throttled partials, retry sleeps, deferred handles/poll/cancel, usage/cache/cost | `src/harness/{generation,provider,usage}.ts`; §§8.3, 8.6 |
| Tools | declarations/repair/schema/wrapping, callers and offered/enabled/deferred sets; safe/unsafe replay, nested keys, output/details/diagnostics/structured output, usage and controls `addTools`, `terminate`, `handoff` | `src/harness/{tool,types}.ts`; §§7.3–7.4, 8.4–8.5 |
| Observation | document read/state/watch, conversation view, task graph; experimental `watchEvents`, `AgentEventStream`, snapshots, message/tool changes and end reasons | `src/harness/{events,view,task-graph}.ts`, `src/session/observation.ts`; §9 |
| Telemetry | `DURABLE_TELEMETRY_SCHEMA` v1: submit/phase spans; entry/submission/report/checkpoint/replay events; report counter, phase duration histogram, active-task counter | `src/harness/telemetry.ts`; `test/harness-telemetry*.test.ts` |
| `./env`, `./env/node` | result/errors; filesystem identity, cwd/path/canonical operations, text/binary/line/directory readers and bounded listing; watcher ready/overflow/error; shell string/argv execution, per-command cancellation, stream windows/spills and owner cleanup; local execution environment | `src/env/{index,node,node-watch,line-scan,decode}.ts`; `test/env-*.test.ts`, testing env conformance |
| `./tools` | `CodingTools`/factory; read/write/edit/bash/PowerShell factories and options; image limits/processor DTOs; file-mutation ordering, truncation, structured results | `src/tools/*.ts`; `test/{tools,tools-images,tools-read-differential}.test.ts` |
| `./images`, `./images/node`, `./images/cloudflare` | image preparation, decode refusal, EXIF orientation, dimensions/byte limits, resize/conversion; Photon core with Node worker and Workers loading adapters | `src/images/*.ts`; `test/images-wasm-module.test.ts`, `tools-images.test.ts` |
| `./tool-search` | tool-search extension/tool factory, name/default limit, result details; deferred discovery and activation | `src/tool-search/*.ts`; `test/tool-search.test.ts` |
| `./codemode` | extension/tool factories, module/tool versions, namespace budget, input/options/global/nested/details DTOs and `CodemodeStoreDoc`; deterministic VM start/journal/result/store; model catalog/classify/image globals | `src/codemode/{codemode,describe,models,output}.ts`; §7.6; `test/codemode*.test.ts` |
| Storage exports | main `MemoryStorage`; `./storage/memory`; portable JSONL and Node opener; generic SQLite facade/schema/migrations and Node/Cloudflare openers; `./package.json` metadata | `src/storage/**`; §§10–11; storage conformance and backend tests |
| `./testing` | storage/env conformance case/provider/assertion interfaces and registrars; expectation adapter; benchmark dataset/scales/seeding and read/write workload definitions | `src/testing/*.ts`; reusable semantic verification, not Vitest API compatibility |

## Behavior checks that change the design

- **One mutation line owns atomic commits and adoption.** Tables and document
  changes commit together; drafts cannot escape; external effects run off the
  line. Storage read/write/ID/close failures generally poison the session;
  invalid read requests and cancelled reads are the documented exceptions.
  A thrown commit acknowledgement may still mean the commit landed.
- **Recovery is a scheduler contract.** Open reconciles interrupted running
  tasks but dispatches no handler. Waiting/sleeping/completing work survives;
  missing or unmigratable definitions block without invented terminal outcomes.
  Restart counts measure lack of checkpoint progress, not every open/close.
- **Prototype blocking differs from Pi.** The prototype blocks an uncertain
  unsafe effect. Pi `src/harness/tool.ts` records an `interrupted` error result
  when stored/current safety do not both permit replay, then the run can
  continue. Unknown effects must never be reported as success or rerun merely
  to make progress. Full parity includes this error-to-model continuation and
  owned-child cleanup, with uncertainty still inspectable.
- **Prepared arguments survive.** Repair and `beforeTool` precede durable intent;
  recovery uses stored final arguments without rerunning `beforeTool`. Both
  stored and newly resolved replay policy must be safe. Nested keys bind tool
  name/arguments; changed reuse is divergence, not a new effect.
- **Hooks can rerun.** They run off the line before a consuming commit. Task
  memos permit stable first-writer decisions; observers are not transactional
  external effects. Persisting a result after a side-effecting hook cannot make
  that hook exactly-once.
- **Ownership differs from history.** Completing owners stay live until ordinary
  children drain; background boundaries, fail-fast joins, abort cascades and
  orphan settlement have distinct rules. Close preserves resumable work rather
  than pretending to abort it durably.
- **Everything visible is committed.** Partials and tool progress are throttled
  durable state; the prototype's completed-message-only sink is an initial
  milestone, not final parity. Views converge and may coalesce changes; they
  are not audit streams or exactly-once delivery queues.
- **Provider identity and usage are state.** Stream requests use pinned
  model/thinking/options/cutoff and persisted provider session identity;
  interrupted partials become aborted assistant entries. Usage includes failed
  attempts and compaction/tool spend, and forks begin their own ledger at zero.
- **Codemode uses deterministic replay, not stack serialization.** Start data
  includes module version, seed, clock, VM fingerprint, deadline, callable tools,
  globals and store snapshot. Journaled delivery order and issue acknowledgments
  gate new nested effects. Store writes and result settle together.
- **Compaction is concurrent work.** Manual, background and blocking tasks share
  selection/summarization but have different ownership/placement. A summary
  that became stale must not resurrect an older context head.
- **Storage transports differ.** Memory, SQLite and JSONL share semantics.
  JSONL commits use sidecars plus a main marker; its default process-crash
  guarantee does not imply power-loss durability. Cloudflare bindings and
  alarms are platform adapters, not prerequisites for local execution parity.

## Existing pipy evidence and limits

The [first prototype](../durable-prototype.md) has measured 33 matrix cases;
the [canonical-loop spike](../durable-loop-integration.md) demonstrates the real
`AgentLoop`, product adapters, rich canonical fields, commit-before-completed
observation and replayed usage. Their recorded counts are evidence runs, not
numbers of independent parity behaviors. Their independent probes overlap.
Neither claims upstream conformance, arbitrary hook safety, durable streaming,
long-lived task scheduling, owned-subagent recovery or Python codemode replay.

Native reuse seams inspected: `agent/loop.py`, `agent/runtime_ports.py`,
`agent/provider_turn.py`, `agent/messages.py`, `coding/product_session.py`,
`coding/session_controller.py`, `coding/codemode_runner.py`,
`coding/codemode_store.py`, `codemode/runtime.py`, and `session_tree.py`, under
`src/pipy_harness/native/`. The product coordinator currently applies live
state before persistence; the queue/lifetime controller owns volatile claims;
provider retries run within a call; codemode stores successful custom entries
but not a restart journal. These are deliberate integration changes, not a
reason to wrap TypeScript or to declare the experiment production-ready.


## DE05 bounded native execution

DE05a definitions/creation and DE05b runtime/reservation/recovery now implement
the bounded sequential task kernel. Real SQLite death and failure tests supplement
an actual pinned Harness/TaskScheduler pair; see the
[runtime contract](../durable-task-runtime.md). Full definitions/migrations/park,
ownership/wait/completing and abort/reload remain DE14/15/16/23. Unknown or
unsupported records stay durable; the interim refuses unsupported ownership
execution rather than fabricating terminal owners. P06/P07/P09/P10 remain OPEN.


### Native canonical checkpoint integration (DE06b)

The private package now joins canonical arenas to actual versioned task phases,
request semantics and provider/tool outcome receipts. Its opt-in phase wrappers
reuse the delivered runtime. Shared construction policy and an auth-owned private
material companion reconstruct headers/targets without serializing credentials
or callbacks. The comparison observes rich finite values through actual pinned
Harness task/SQLite transitions and native decoded DTOs; it is not Pi's raw arena
format, full generation, or raw credential equality. Explicit terminal/reopen
negative-zero representation accounting and native-only type/privacy/wire proofs
are documented in [the codec contract](../durable-canonical-codec.md). Every
published requirement retains its original owning slices; all P rows remain OPEN.


### Sequential native tool effects (DE08)

Pinned `harness/tool.ts` commits final args/replay intent before invoking and
reissues only stored-safe/current-safe. Native uses an explicit immutable trusted
registration descriptor; existing readOnly/idempotent annotations never supply
that contract. Its early unsafe BLOCK is Milestone A behavior, while pinned Pi
settles an interrupted error and continues (DE13). Actual Harness/SQLite/reopen
comparison retains both exact outcomes and compares only the declared logical
round/replay prefix; native real-process/OS-lock/lifetime proofs stay separate.
Pinned `generation.ts:finishToolRound` creates a successor generation task;
native currently advances the same parent with fresh request identity. Full
exposed ownership/lifecycle/graph compatibility remains DE15/28 before whole P
closure. Nested/extension/mode/control policies remain DE24/25/30, with every
original acceptance cell and all43 OPEN parity rows retained.

## DE10 public inspection boundary

Pinned `harness/harness.ts` getTask/inspect read on the Session line;
`harness/scheduler.ts` derives live state and fit without executing task code.
`test/harness-inspect.test.ts` explicitly proves paused inspection does not run
migrations. Public raw TaskRecord/errors are not a redacted operator report.
There is no generic resolve export: retryTask clears a parked restart count,
which is distinct from [native explicit new-attempt authorization](../durable-inspection.md).
The genuine phase19 oracle compares declared logical metadata only. Native
private-audit policy, redaction and physical ownership are separate evidence;
full missing/migration/restart/orphan/graph/control semantics keep their declared
owners. All43 whole P rows remain OPEN.

## DE12 headless and P16 request comparison

The public pinned Harness has no CLI activation API. The headless-cli-v1 fixture
therefore uses genuine Harness create/submit/wait/reopen and Storage commit cuts,
with explicit synthetic model/tool transport boundaries. generation.ts prepares
a model reference/thinking/stream/cutoff, then request resolves getModel again.
Actual retained getModel/streamSimple observations expose current target after
reopen; native captured prepared-target protection deliberately refuses drift.
The comparison records this difference explicitly rather than claiming raw
provider target parity. Original P16 stays whole OPEN with its named owners.

# Durable release 1.5 bridge

Owner decision of 2026-11-03. This bridge moves the native durable execution
campaign from literal Pi release 1.4.0 to literal release 1.5.0. It is a plan
document: it changes no runtime code, reference runner, fixture or comparison
result, and it closes no parity row.

## Target

The target is literal release `v1.5.0`: commit
`f9a26365568127461c47a07a37dcb42c140a3ac5`, tree
`b7734e15f2c1a10846284f77034a2214746a4da6`, released 2026-11-02. It is not
postrelease HEAD. At that commit `packages/durable/package.json` names
`@earendil-works/pi-durable` 1.5.0. The sources of truth, all at that commit,
are the `[1.5.0]` section of
[`packages/durable/CHANGELOG.md`](https://github.com/earendil-works/pi/blob/f9a26365568127461c47a07a37dcb42c140a3ac5/packages/durable/CHANGELOG.md),
[`packages/durable/docs/spec.md`](https://github.com/earendil-works/pi/blob/f9a26365568127461c47a07a37dcb42c140a3ac5/packages/durable/docs/spec.md),
`packages/durable/docs/group-commit.md` and the tests under
`packages/durable/test/`. 308 commits separate it from release 1.4.0
(`4a080252f06caf63a525d7873c5d94d3c23887e0`).

Earlier targets were Pi durable 1.3.0 at
`2ec55d808d455b1a64a2bf7888198207a096120a` (the
[upstream audit](2026-10-19-durable-upstream-audit.md) of 2026-10-19), then
release 1.4.0 (owner decision 2026-10-29, [1.4 bridge](2026-10-29-durable-release-1.4-bridge.md)).

### Why retarget now

Release 1.5 removes behavior that pipy delivered against 1.4: restart parking
and `Harness.retryTask()` (DE14a, DE14a1). It also changes contracts that every
remaining slice builds on: the `Storage.commit()` call, the watch, observer and
report shapes, and what happens to a model request that is in flight when the
host dies. DE16a, DE16b and DE16c are delivered on main (latest `d2919cc8`) and
the DE16 remainder has not started. Staying on 1.4 would build more work on
removed behavior, and a later switch would rework more of it.

### What carries over

- Earlier dated 1.3 and 1.4 receipts stay historical, bound to their own
  sources and pins. None is rebound to 1.5, and no 1.5 result is claimed here.
- Since R15-1b the reference runner, its manifests, fixtures and every
  comparison phase execute release 1.5.0. Native behavior keeps its 1.4 shape
  until the bridge slices port it; where a comparison shows the difference, a
  declared divergence names its owner. The topic docs keep describing the
  delivered native behavior.
- The obligations of the 1.4 bridge carry over unchanged. `documents.ts` is
  identical at both releases, so the required `checkpointWhen` policy and its
  checkpoint profile stand. pi-ai `utils/identity.ts` and the Harness model
  wrapper are unchanged, so the open P16 ClientIdentity obligation stands.
  CodeMode Store still checkpoints at `deltasSinceBase >= 64` (DE35–DE37).
- All 43 P rows remain OPEN.

## Release adoption rule

The campaign does not chase every upstream release:

1. Pin literal release tags only: the tag's exact commit and tree, never a
   branch head or a postrelease commit.
2. Adopt a later release only by owner decision, and at a slice boundary at
   the earliest: never switch in the middle of a slice, because a slice in
   progress finishes, or is abandoned, against the pin it started on. Prefer a
   milestone or bridge boundary. This retarget itself falls on a slice boundary
   inside Milestone B, between DE16c and the DE16 remainder.
3. Each adoption gets its own dated bridge like this one, with a complete
   classified inventory, ordered bridge slices and a dated owner note in plan
   §7. Earlier receipts stay historical.
4. A newer release published between decisions is recorded as a watch item. It
   changes neither the target nor any gate.

## Deviation D1: the Session line stays held through storage

Release 1.5 adds group commit (spec §1 invariant 7, §4 and §5.4;
`docs/group-commit.md`). The Session line no longer waits while a commit is
stored. Commits admitted while a storage call is in flight go out together in
the next `Storage.commit()` call. A later job runs until it touches a key that
an unpublished commit wrote, and cancellation is checked when the job starts.

pipy keeps the 1.4 shape: its Session line stays held through storage
settlement, adoption and publication, and the Session sends one batch per
storage call. This is a legal 1.5 schedule. Release 1.5 does not promise that a
job starts before an earlier commit is published; the spec only says that a
callback *can* run while an earlier commit is being stored. Every pipy schedule
is therefore the 1.5 interleaving in which each storage call settles and
publishes before the next job starts. Outcomes and publications stay in line
order, each commit keeps its own seq, a crash leaves a stored prefix, and no job
observes an unpublished write. What differs is throughput under storage latency:
upstream measured large gains with a slow storage round trip, about 30 percent
with local SQLite, and somewhat more CPU per commit with in-memory storage. The
deviation is performance-only.

Consequences:

- R15-3 still implements the whole batch `Storage.commit()` contract. Only the
  Session's use of it is limited to one batch per call.
- **DE16 invocation gate.** The 1.5 test "runs a handle operation queued behind
  the abort mark before the mark's publication, and the cascade aborts it"
  (`harness-ownership.test.ts`, line 1068 at v1.5.0) holds the storage call
  that writes the abort mark (its `HoldMark` storage), so that a tool's queued
  `handle.submit()` job starts while the mark is unpublished, lands, and is then
  aborted by the mark's cascade. Under D1 that schedule cannot arise: the queued
  job starts only after the mark is stored and published. DE16 records this
  test as a declared schedule deviation, not as a skip or a failure. It ports
  the same program as a line-held test instead. There the queued submit rejects,
  because its invocation is aborted by the time the job starts, the child
  conversation gets no entry, and no submission record exists. These are the
  assertions of the 1.4 test "rejects a handle operation queued on the line when
  the invocation ends before it runs", which 1.5 replaced. DE16 must fix when the
  abort signal reaches the invocation relative to the start of the next line
  job, so that this outcome is deterministic. That a cascade aborts a run admitted before the mark stays
  covered by the 1.5 cascade tests, such as "cascades an abort mark to the owned
  foreground subtree and withdraws its queued inputs".
- The other schedule-dependent group-commit tests (`session-group-commit`,
  `harness-group-commit`) are scoped by D1 the same way. Their invariants still
  apply and are tested with one batch per call: outcomes in line order, no
  observation of unpublished writes, one seq per commit, and later outcomes
  failing once a commit fails the Session.
- A wait whose commit is in flight when close begins ends with the closed
  error, while the commit is still stored and published
  (`group-commit.md` §2, item 7). That can happen under D1 as well, so DE16's
  close and admitted-commit ordering covers it.
- **Revisit at DE45.** The final acceptance audit measures throughput and
  decides between keeping D1 and a separate group-commit slice. Until then the
  plan and the harness spec state D1 wherever they describe the Session line.

## Inventory of 1.4 to 1.5 changes

Every entry of the CHANGELOG `[1.5.0]` section and every normative change in
the 1.4.0..1.5.0 diff of `spec.md` is covered by at least one row. Classes:

- **R**: rework of delivered pipy work, owned by the named bridge slice.
- **A**: absorbed by the named remaining DE row and its P rows. Where such a row
  must later rework delivered behavior, the note says so.
- **X**: out of scope, for the stated reason.
- **V**: the documented deviation D1.

"CL" names the CHANGELOG subsection. Section numbers are those of `spec.md` at
v1.5.0, and test names are files under `packages/durable/test/` without the
`.test.ts` suffix.

### Restart limit and recovery

| ID | Release 1.5 change | Upstream anchor | Class and owner | Effect on pipy |
| --- | --- | --- | --- | --- |
| U01 | Restart-limited tasks no longer park. At `settings.restartLimit` an extension's task ends `faulted` with detail `{ reason: "restart_limit" }` when the scheduler reserves it, reading the current setting; a raised limit lets it run, and an ended task stays ended. Built-in tasks end themselves visibly: a tool call with a `restart_limit` error result and a failed task, a compaction into `compactionFailure`, a generation with run failure `restart_limit`, while an answered run completes. `retryTask()`, the blocked reason `restart_limit` and the `task_parked` report are removed. | CL Breaking; §5.4; `harness-blocked`, `harness-restart-failure` | R: R15-2 | Removes DE14a parking, the DE14a1 Product `retry_task` and `pipy durable retry-task`, and `restart_parked` and the `restart_limit` hold reason from inspection. The DE14b2 policy publication and the DE14a limit normalization stay. P07, P21. |
| U02 | An abort handler cut off at the limit still faults `restart_limit`. A run that someone aborted then ends with run failure `aborted` and the cut-off as detail, and an aborted compaction leaves no `compactionFailure`. | §5.4; `harness-restart-failure` | A: DE16, DE19, DE29 | The DE16a exhaustion fault is unchanged. Run and compaction presentation belong to DE19 and DE29. P10, P13, P30. |
| U03 | `task_interrupted` report (reason `restart_limit` or `interrupted`, the task and its `restarts`) when crashes end a built-in task, with matching telemetry values. | CL Added; §10.1 | A: DE39 | R15-2 records the reason and restart count with each ending, so that DE39 can report them; pipy has no task report channel yet. P40. |
| U04 | A model request in flight when the host died is no longer resent on open. The attempt counts as failed under `settings.retry`: its partial becomes an aborted entry, the next attempt waits for the backoff, and with no retry left the run ends `interrupted` and its inputs settle `unanswered`. An in-flight deferred poll is charged the same way and polls the same response again, even when the new host's clock is behind; a run that ends instead cancels it. A close still resends. `restartLimit` bounds a charge that itself keeps being cut off. | CL Breaking, Fixed; §8.3; `harness-restart-failure` | A: DE17 | DE07, DE11 and DE12 resend before a receipt, as 1.4 did; DE17 reworks that path and now owns this acceptance. P17, P18. |
| U05 | Built-in tasks check the restart count when a phase starts (a tool call after reading its call) and read the setting only when the task has a count. | §5.4; `harness-restart-failure` | R: R15-2 | Part of the R15-2 endings. P07. |
| U06 | A crash counts a replay-safe caller and its nested calls, so the caller reaches the limit no later than its nested calls, and the model sees the caller's own `restart_limit` result. | §5.4 | A: DE24 | No nested calls exist natively yet. P23. |

### Storage

| ID | Release 1.5 change | Upstream anchor | Class and owner | Effect on pipy |
| --- | --- | --- | --- | --- |
| U07 | `Storage.commit(batches, receive, context)` resolves `{ stored, refused? }` and replaces `commit(writes, adopt, context)`. Each batch is one atomic commit with its own seq, checked against the state the earlier batches left. The first refused batch and everything after it are not stored; any other failure rejects the whole call as an unknown outcome. `receive(index, seq)` runs once per stored batch, in order, before the next call. Every batch has at least one write. An optional `publishing()` hook wraps each publication; `commitWrites()` is the one-batch testing helper; the storage conformance suite checks the contract; SQLite stores a call in one transaction and JSONL appends every batch's sidecars, then all markers in one write. | CL Breaking; §§10, 11.2, 11.3; `group-commit.md` §3; testing storage conformance | R: R15-3; A: DE40 | Reworks the delivered `commit(writes) -> Seq` port and its Memory and SQLite backends (DE02, DE03). The JSONL backend is DE40. P01, P03, P43. |
| U08 | A commit that Storage stored and whose call then rejects is durable: it is published, the waits it resolved stay resolved, then it rejects with the storage error and the Session fails. A call that resolves without a receipt for a batch it reports stored also fails the Session. | CL Fixed; §§1 invariants 2 and 8, 10.1; `session-adoption` | R: R15-3 | Reworks the delivered DE02b failure path. P01. |
| U09 | The `Storage.mintId()` contract is narrowed to IDs above every ID minted since the storage was opened and every stored ID, also across reopen. | CL Breaking; §10 | R: R15-3 | R15-3 documents the narrowed contract and tests the delivered backends against it. P01. |
| U10 | `SqliteExecutor.runCounted()` for custom `SqliteDatabase` implementations. | CL Breaking | X | A Chord/Node SQLite executor binding; pipy owns its SQLite backend (plan §1, Cloudflare/Node adapters). |
| U11 | `StoreStorage` on Chord v2's store, with one store transaction per call (`commitSeries`) and its `publishing()` scope. | CL Added; §10 | X | Chord v2 store host adapter; excluded by owner decision. |
| U12 | IndexedDB backend batch commit. | §10 | X | Browser host, excluded since the 1.4 bridge. |

### Session line, publication and commit observers

| ID | Release 1.5 change | Upstream anchor | Class and owner | Effect on pipy |
| --- | --- | --- | --- | --- |
| U13 | Group commit: storage overlaps the line, later jobs wait at the keys of unpublished commits, scheduler decisions rerun after such a wait, and at most 256 admitted jobs wait for their outcome. | CL Changed; §§1 invariant 7, 4, 5.4; `group-commit.md`; `session-group-commit`, `harness-group-commit` | V: D1 | The line stays held through storage; revisited at DE45. P01, P43. |
| U14 | Cancellation is checked when a job starts, which can be earlier than before: an invocation-bound submit queued behind an abort mark that is not stored yet lands, and the abort's cascade then aborts its run. | CL Changed; §4; `harness-ownership` line 1068 | V: D1; A: DE16 | DE16 pins the line-held outcome described under D1. P10. |
| U15 | A stored commit is published after its storage call settled, in line order, the commits of one call a macrotask apart. The Session's own observers run at publication, `observeCommits()` observers one microtask later and before the waits the commit resolves; the abort signal of a newly marked task fires after the commit observers instead of before them; `onWake` runs in a microtask after the commit that changed it was adopted. | CL Changed; §§4, 5.6 | R: R15-4; A: DE17 | R15-4 orders the delivered publication (DE02b, DE09, DE16c signalling) as own observers, then host commit observers, then wait continuations and abort signals. `onWake` timing belongs to DE17. P10, P28, P08. |
| U16 | A throwing Durable commit observer fails the Session only after the other observers and the host listeners got the commit, and the commit still stands. When Storage also rejects that commit after adopting it, the storage error fails the Session and the observer's error is reported as `listener_error`. | CL Changed; §§4, 10.1 | R: R15-4 | The delivered Session fails at the internal listener's throw, before host listeners run. P01. |
| U17 | A wait whose commit is still being stored when the Harness closes ends with the closed error; the commit is still stored and published before close completes. Close drains every pending and in-flight call and stores held changes through the writer. | CL Changed; §4; `group-commit.md` §2 | A: DE16 | Applies under D1 too; DE16 close and admitted-commit ordering. P10. |
| U18 | A normal agent run makes no storage reads: the Session caches recent records, conversation tails and live task documents, and the scheduler keeps a running conversation's context range. Read results are frozen objects shared with these caches. | CL Changed; §4; `session-cached-storage`, `session-conversation-tails`, `harness-context-range` | A: DE27, DE28, DE45 | Performance budgets of P29 and P43. Native reads already return detached owned values, so the frozen-object rule needs no Python change. |
| U19 | The scheduler's per-commit work no longer grows with the number of conversations (indexed idleness checks, expiry-ordered context ranges, earliest-sleep wake). | CL Changed; `harness-idle-scopes` | A: DE27, DE28, DE45 | Performance budgets of P29 and P43. |
| U20 | Deferred commits: `{ persistence: { kind: "deferred", withinMs? } }` holds commits that change only existing latest, initial-fork conversation documents. They are adopted and shown at once and stored with the next commit that acquires the document, at the deadline (`settings.progress.persistWithinMs`, default 1,000 ms), at close or at unload. The Harness defers generation partials and running tool progress, so a crash loses up to the window plus the line and I/O delay (§4.1), which the held line of D1 can lengthen; a stream that crashes before its first store leaves no aborted entry and records no usage. | CL Breaking, Added; §§1 invariants 2 and 3, 4.1, 5.6, 8.2, 8.3, 13; `session-deferred-commits`, `harness-deferred-progress` | A: DE26; DE17 for the deadline timer | P27, P19. Native progress currently commits immediately. |
| U21 | A tool slot's `running` status, a nested call's final arguments and a generation's attempt are stored with the next commit, like progress, so after a crash a slot may show `pending` while its task's checkpoint is at `execute`. | CL Changed; §§8.2, 8.4 | A: DE26 | Touches the presentation of the delivered DE08 intent commit. P27. |
| U22 | `subscribeCommits(listener)` becomes `observeCommits(observer)` with `advance(publication, context)` and `end(end)`, returning `{ detach() }`, without a value. An observer that throws or rejects is reported, detached and ended `listener_error`. Observers are read when a commit's dispatch runs. A Session close ends synchronous observers `failed` with "session closed", while watches end `ended`; spec §4 still says `ended` for commit observers at close, but the source and the last Breaking entry say `failed`. | CL Breaking, Changed; §§4, 9.1; `session-observers` | R: R15-4 | Reworks the delivered `subscribe_commits` (DE02b, DE09, product wake). The private Session-failing listener API that pipy already calls `observe_commits` is renamed first. R15-4 follows the source on the close end and records the spec discrepancy. P28. |
| U23 | `DocumentCommitChange` is tagged: `{ kind: "change", record, conversationId, version, value, ops }` or `{ kind: "retire", record, conversationId }`. | CL Breaking; §4 | R: R15-4 | Reworks the delivered document changes in commit publications. P28. |
| U24 | `SessionEnd` (`closed`) is tagged by `kind`: `closed`, or `failed` with the error. | CL Breaking; §§4, 10.1 | R: R15-4 | Reworks the delivered Session end value. P01. |

### Watches, views and observers

| ID | Release 1.5 change | Upstream anchor | Class and owner | Effect on pipy |
| --- | --- | --- | --- | --- |
| U25 | Watches use Chord's `WatchHandle` and tagged shapes: a listener gets `{ kind: "value", value, ops, retired }`, and `WatchEnd` is tagged by `kind` with `stopped`, `aborted` (was `cancelled`), `ended` (was `session_closed`), `listener_error` and `failed` (was `session_failed`); `retired` is gone. An ended watch drops every reference into its Session. | CL Breaking, Changed; §9.1 | R: R15-4; A: DE28 | R15-4 renames the end reasons of the delivered DE09 completed projection (`session_closed` becomes `ended`); its native-only reasons stay documented as native. The full watch semantics are DE28. P28. |
| U26 | Document watches follow their address. A retirement sets `retired` on the next call instead of ending the watch, a later incarnation arrives as `[["r", value]]`, a task document's watch ends `ended` after its retirement call, and a watch pins nothing while its address is absent. | CL Breaking; §§9.1, 9.2 | A: DE28 | P28, P05. |
| U27 | Commits that land while a watch callback runs are delivered together, with folded operations; past 10,000 pending operations one root replacement is sent instead (was 100 pending commits). | CL Breaking; §§9.2, 12 | A: DE28 | P28. |
| U28 | The Chord state getters `Session.documentState()`, `Conversation.viewState()` and `Harness.taskGraph()` and the `DocumentState` type are removed; Durable no longer uses `replicatedState()`. | CL Breaking; §§3.3, 9 | A: DE28 | Removed from the target: DE28 does not add them. P28. |
| U29 | Synchronous observers for adapters: `observeDoc()`, `observeConversation()` and `observeTaskGraph()` attach an `Observer` (`advance(call)`, `end(end)`) atomically on the line and return an `Observation` (`value`, `detach()`). The task graph also advances outside any commit when a registry change or a failed migration changes a `blocked` reason. | CL Added, Breaking; §9.1; `harness-observers`, `session-observers` | A: DE28 | P28, P29. |
| U30 | `ConversationView.entries` is a window over the fork-aware transcript, head markers in place, with `before` and per-observer `ConversationWindow` bounds (`from`, `min`, `max`); `Conversation.context()` derives the active transcript. | CL Breaking; §§2.1, 9.3; `harness-view-run` | A: DE28; DE30 | Head markers in place also concern DE30. P28, P31. |
| U31 | `ConversationTranscript` keeps a client's transcript over any view (`merge`, `loadEarlier`, `fillGap`, `resume`). | CL Added; §9.3; `conversation-transcript` | A: DE28; DE42 | The TUI consumer is DE42. P28, P41. |
| U32 | `watchEvents()` and its event types are removed, with spec §9.4; the old §9.5 task graph view is now §9.4. | CL Breaking | R: R15-1; A: DE28 | The DE09 comparison driver (`completed.mts`) uses `watchEvents`, so R15-1 re-derives that phase. DE28 drops the event stream from its scope; the native DE09 projection has no event stream API. P28. |
| U33 | `pi.live` keeps `runFailure` after a run that ended without an answer, so an idle check tests `run === undefined`. | CL Breaking; §8.2 | A: DE19 | No native `pi.live` document exists yet. P13. |

### Reports and errors

| ID | Release 1.5 change | Upstream anchor | Class and owner | Effect on pipy |
| --- | --- | --- | --- | --- |
| U34 | `onReport(report)` takes one `Report` tagged by `kind` (was `type`) that carries the `error`. `session_failed` is now `storage_failed`, and `task_outdated` uses `task_too_old` or `migration_failed` instead of `incompatible_task`. The `pi.durable.report` telemetry keeps its `type` attribute. | CL Breaking; §10.1 | R: R15-4; A: DE39 | R15-4 reworks the delivered Session reporter, which emits `(error, "session_failed")`; the full report vocabulary is DE39. P40. |
| U35 | `HostCallback` gains `telemetry`: a telemetry adapter that throws while a commit is published is reported as `host_error`, and the commit resolves; a throwing report metric or event is dropped. | CL Breaking, Fixed; §§4, 10.1 | A: DE39 | P40. |
| U36 | `ReportFrom` in `runtime.report(error, from)` is tagged by `kind`. | CL Breaking; §5.1 | A: DE22, DE39 | No native task report API exists yet. P40. |
| U37 | `TaskInspection.state` says `pending` (was `ready`). | CL Breaking; §2.2 | A: DE14b; R: R15-1 | The native inspection vocabulary has no `ready`; R15-1 adapts drivers that read Pi's word. P07. |

### Submissions, runs and the inbox

| ID | Release 1.5 change | Upstream anchor | Class and owner | Effect on pipy |
| --- | --- | --- | --- | --- |
| U38 | Continue submissions: `submit({ type: "continue" })` starts a run on the context as it is. It is admitted only while the conversation is idle (`ConversationBusy` otherwise) and places queued items first. A context with nothing to answer ends the run `nothing_to_answer` without a request, and an empty conversation no longer faults its generation. `ConversationHandle.submit()` takes any draft. | CL Breaking, Added; §§2.2, 6, 8.3; `harness-continue` | A: DE18, DE19 | The `nothing_to_answer` check touches the delivered DE07 prepare phase. P11, P13. |
| U39 | `whenIdle: "reject"` makes an idle conversation reject an input with `ConversationIdle` without writing. | CL Added; §§2.2, 6; `harness-submit-in-commit` | A: DE18 | P11. |
| U40 | `Conversation.submit(tx, draft)` and `ConversationHandle.submit(tx, draft)` admit a submission inside the caller's commit, before its first table write (`ReadAfterWrite` otherwise); the handle form rejects once its invocation is aborted or ended. | CL Added; §§4, 6, 7.3, 12; `harness-submit-in-commit` | A: DE18 | The handle form reuses the DE16 invocation gate. P11. |
| U41 | `pi.live.runFailure` records why the newest run ended without an answer: `model_error`, `no_model`, `aborted`, `reset`, `faulted`, `orphaned`, `interrupted`, `restart_limit`, `nothing_to_answer` or `turn_limit`, with the task, the inputs and a detail. `settings.afterFailedRun` applies to the new reasons too. | CL Added, Breaking; §§6, 8.2; `harness-run-failure` | A: DE19 | R15-2 and DE17 produce the `restart_limit` and `interrupted` endings. P13. |
| U42 | Per-conversation turn limit `AgentState.maxTurns`: the round that reaches it runs its tools, then the run ends `turn_limit`. `pi.live.run.turns` counts the rounds, an `onYield` continuation starts it over, children and forks inherit the limit, and `pi.live` is version 3. | CL Added, Breaking; §§2.2, 8.2, 8.3, 8.5; `harness-turn-limit` | A: DE25; DE21 | The agent state field is DE21. P22, P24. |
| U43 | Run-end tasks: a `GenerationHooks.afterRun(run, api)` hook returns tasks built with `runEndTask()` after a run answered. The inputs settle `done` at the answer, while the generation (version 2, phases `finish` and `drain`) owns the tasks and keeps the conversation busy until they are terminal; Stop and idle waits include them, and the final boundary waits for them. | CL Added; §§5.5, 6, 7.2, 8.2, 8.3, 12; `harness-after-run` | A: DE22; DE19, DE15 | Owned children and the final boundary concern DE15 and DE19. P25, P12, P09. |
| U44 | A tool round with `control.handoff` no longer ends the run: it writes the `pi.reset` entry and continues with a new generation in the new context. Queued writes and selected steers land after the handoff text, follow-ups wait for the final boundary, and a queued reset still ends the run `reset`. | CL Fixed; §§6, 8.5 | A: DE25; DE30 | P22, P12, P31. |

### Hooks, tools and rounds

| ID | Release 1.5 change | Upstream anchor | Class and owner | Effect on pipy |
| --- | --- | --- | --- | --- |
| U45 | `afterTool` runs on every result a tool task writes, including the error results the Harness writes itself (`tool_unavailable`, `invalid_arguments`, `blocked`, `interrupted`, `restart_limit`, `aborted`, `abandoned`). Such results are bounded after the hooks, keep no `structuredOutput` and apply `control` only on completion. Past the restart limit the hooks no longer run, and an abort's hooks delay `Conversation.abort()`. | CL Breaking; §§7.2, 7.3, 8.4, 12; `harness-tool-hooks` | A: DE22, DE24 | Without durable hooks natively, R15-2's tool ending is hook-free. P25, P20, P23. |
| U46 | A call to a tool its request did not offer gets a `pi.tool` task flagged `unoffered` that settles `tool_unavailable` without resolving the name; its slot starts `pending` with a task, controls apply in call order, `pi.tool` is version 3 and `pi.generation` version 2, and an older Harness leaves both blocked (`task_too_old`). | CL Breaking; §§7.3, 8.1, 8.3, 8.4 | A: DE25, DE24 | Reworks the delivered DE07 and DE08 round admission, where native generation writes the `tool_unavailable` result itself and stores `pi.generation` version 1 and `pi.tool` version 2; DE25 needs a version migration for stored native tasks. P20, P22. |
| U47 | `GenerationHooks.beforeProviderRequest({ model, payload }, api)` replaces the provider request body for one request; it runs each time pi-ai builds a body and not for compaction requests. | CL Added; §§7.2, 8.3 | A: DE22 | Uses the DE07 provider request seam. P25, P16. |
| U48 | `HookApi.entry()` and `entries()` and `TaskRuntime.entries()` read raw conversation history, which compaction never changes; `HookApi` has no `context()`. | CL Added; §§5.1, 7.2; `harness-hook-reads` | A: DE20, DE22 | P14, P25. |
| U49 | Tool schemas are JSON Schema typed through pi-schema, and `typebox` is no longer a dependency. Arguments and `structuredOutput` are validated with pi-schema, which changes coercion and the wording of validation errors. | CL Breaking; `tool-args-typebox` | X for TypeScript typing; A: DE24; R: R15-1 | TypeScript typing is excluded (plan §1). The observable validation, coercion and error text belong to DE24. R15-1 moves the drivers' tool schemas off pi-ai `Type`. P20. |

### Generation, compaction, environment and tools

| ID | Release 1.5 change | Upstream anchor | Class and owner | Effect on pipy |
| --- | --- | --- | --- | --- |
| U50 | A generation records the Pi thinking level its request asked for on the stored answer, also after polling a deferred response. | CL Fixed; §8.3 | A: DE17 | Touches the delivered DE07 settlement; DE17 reworks it with the deferred poll. P15, P16. |
| U51 | Compaction: `CompactionStatus.reason` and `CompactionFailure.reason` become `trigger` (`pi.live` version 2 migrates them), recovery resends the request until the restart limit, and a summary is placed at the boundary that ends a run waiting for after-run tasks. | CL Breaking; §§8.2, 8.7 | A: DE29 | P30. |
| U52 | The `FileSystem.watch()` change type is renamed `FileChange` and tagged by `kind` (`paths`, `overflow`, `failed`), and `FileWatcher.close()` becomes `stop()`. | CL Breaking | A: DE32b | Python names may differ; the semantics are unchanged. P33. |
| U53 | The built-in `read` declares a `structuredOutputSchema` (a text file's text or an image's content), so Code Mode receives images from it. | CL Fixed | A: DE33, DE37 | P34, P38. |
| U54 | `tool_search` and Code Mode `searchTools()` rank with the tool name as a separate BM25 field, coverage first and a whole-name bonus, and tokenize words in any script, including segmentation of unspaced text and canonical equivalence. | CL Fixed; §7.3; `search`, `tool-search` | A: DE34 | P35. |
| U55 | The Code Mode extension no longer loads the QuickJS WASM runtime it does not use. | CL Fixed | X | JavaScript packaging; pipy uses its own Python WASI runtime. |

### Host options, adapters and adjacent packages

| ID | Release 1.5 change | Upstream anchor | Class and owner | Effect on pipy |
| --- | --- | --- | --- | --- |
| U56 | `HarnessOptions.readOnly` opens a viewer: open recovers nothing, every commit with writes rejects with `ReadOnlySession` before it reaches Storage, `resume()` rejects, and `wake()` stays inactive. | CL Added; §2.2; `harness-read-only` | A: DE43 | Must be reconciled with the single-owner path lock (plan §3.1); DE10 passive inspection is the nearest delivered surface. P42, P41. |
| U57 | `registryReader()` over a Chord v2 registry value. | CL Added; `harness-registry-reader` | X | Chord v2 registry host adapter; excluded by owner decision. |
| U58 | Memory and CPU fixes: views no longer retain a string copy per partial, nested-call summaries and span messages no longer retain their source text, and partial commits append new text instead of copying it. | CL Fixed | A: DE26, DE28 | JavaScript string-retention details have no Python analogue; the remaining obligation is bounded per-partial cost and memory. P27, P43. |
| U59 | Partial commits no longer pollute `Object.prototype` through a `__proto__` argument member. | CL Fixed | X | JavaScript object semantics; native owned JSON treats `__proto__` as an ordinary key. |
| U60 | Spec clarifications without new behavior: settings changes "change no view", the new API footguns (§12) and the non-goal "no visible-undurable publication beyond deferred commits" (§13). | §§2.2, 12, 13 | A: owners of U20, U40, U43, U45 | Documentation duty of those owners. |
| U61 | Adjacent pi-ai 1.5 changes that durable behavior consumes: pi-schema validation, streamed tool-call argument parsing (`ToolCallArgumentsStream`), `Usage.cost.totalFromProvider`, a LiteLLM context-overflow pattern and a non-retryable Zhipu usage-limit error. | pi-ai CL `[1.5.0]` | A: DE24, DE26, DE17, DE29; X for the rest | Validation is DE24, argument streaming DE26, provider-reported totals P19, and the classifiers DE17 and DE29. Unrelated provider and catalog changes stay outside the plan §1 provider boundary. P18, P19, P20. |

## Bridge slices

The bridge runs in this order before the DE16 remainder resumes. Each slice is a
separate reviewed commit. A slice too large for one review splits before code,
keeps its whole acceptance, and records the split here and in the backlog.

### R15-0: this bridge

Docs only: this document, a dated owner note in plan §7, in-place amendments of
the §4 and §5 rows whose acceptance changes, the target statements of the
upstream audit, evidence ledger, backlog and harness spec, pointers in the
reference guide, the restart parking guide and the 1.4 bridge, and the release
notes. The coverage index mirrors the amended rows, and the fixture manifests
carry the new digests of the edited documents. No runtime, reference or fixture
result changes.

### R15-1: reference re-pin

- Pin `f9a26365568127461c47a07a37dcb42c140a3ac5` and tree
  `b7734e15f2c1a10846284f77034a2214746a4da6` in the runner, the CI job and
  every manifest. Each manifest's `reference_commit`, `reference_sources` and
  declared `upstream_tests` come from that commit (the complete pin identity
  rule of the 1.4 bridge).
- Re-pin the runtime with the reference. The runner accepts only Node v26.12.0
  and npm 11.20.0. This host's Homebrew Node moved to v26.13.0 on 2026-11-01,
  so the 1.4 chain now stops with "Runtime version drift: node". Upstream
  still requires Node 22.19.0 or later.
- Rebuild the dependency closure from the v1.5.0 lockfile: pi-durable adds
  `@earendil-works/pi-schema` and drops `typebox`.
- Move the drivers to 1.5 spellings: pi-schema tool schemas instead of pi-ai
  `Type`, `Storage.commit(batches, receive, context)` or `commitWrites()`,
  `observeCommits(observer)`, `onReport(report)`, tagged watch ends and the
  inspection word `pending`.
- Give every existing comparison phase one recorded disposition. A phase either
  passes against 1.5 under its declared projection, or is re-derived over a 1.5
  surface (`completed-projection-b-v1` uses the removed `watchEvents`), or is
  withdrawn because its upstream surface is gone (`restart-parking-v1` and
  `task-product-retry-enable-v1`, and any case of `restart-policy-live-v1`,
  `task-migration-cold-v1` or `task-orphan-missing-definition-v1` that relies
  on `retryTask` or the `restart_limit` blocked reason), with its successor
  owned by R15-2 and the parent chain re-linked. A phase may also carry a
  declared divergence owned by a named slice, such as a 1.5 trace that charges
  an in-flight request where native code resends it (DE17). A declared divergence is an open item of its owner,
  is removed when that owner lands, and never closes a row. No phase is skipped
  silently, and earlier 1.4 receipts stay historical.
- Gates: a fresh baseline acceptance and a complete fresh chain on 1.5, the
  manifest cascade, `durable-reference.md` and the evidence ledger. No P row
  closes.

R15-1 lands as two reviewed commits; together they carry the whole acceptance
above:

- **R15-1a, runtime re-pin.** The runner, the CI job and every comparison phase
  accept Node v26.13.0 with npm 11.20.0, the official release of 2026-10-28 and
  the Homebrew Node on the development host since 2026-11-01. One `RUNTIME`
  constant in the reference contract replaces the literals that the phases
  checked separately. The reference stays release 1.4.0, so a fresh 1.4 chain
  shows what the new runtime alone changes before the release pin moves.
- **R15-1b, release pin.** The remaining items above: commit, tree and package
  version, the manifests and the dependency closure, the drivers, the expected
  traces from actual 1.5 runs and the disposition of every phase, with a fresh
  baseline and complete chain on 1.5. Every phase checks the one reference
  commit of the baseline acceptance, so the release pin cannot be split by
  comparison family.

R15-1b recorded these dispositions; the
[reference guide](../durable-reference.md#release-15-dispositions-r15-1b) lists
every phase:

- Twenty phases pass under their declared projection, and the session-checkpoint
  contract expects the 1.5 group-commit order on the SQLite reference
  (deviation D1).
- `completed-projection-b-v1` is re-derived over the conversation watch alone
  (U32), and the session-guard scenario `reentrant-report` reaches its report
  through a fatal read instead of a guard-only commit failure (U07, U08).
- Withdrawn with successor R15-2, because 1.5 removed restart parking,
  `retryTask()` and the `restart_limit` blocked reason (U01): the phases
  `restart-parking-v1` and `task-product-retry-enable-v1`, whose manifests are
  closed retirement records that the baseline acceptance checks; the
  `parked-migration` case of `task-migration-cold-v1`, which now follows
  `interrupted-tool-continuation-v1`; four of the five cases of
  `restart-policy-live-v1`; and the parked control of
  `task-orphan-missing-definition-v1` and of `task-orphan-failed-migration-v1`.
  The remaining cases of these four phases pass, and the parked controls of
  `task-orphan-task-too-old-v1` and `task-orphan-missing-migration-v1` still
  pass. Withdrawn with successor
  R15-3: the guard mutant `commit-exempt`, since 1.5 does not report a
  guard-only commit failure. Native tests keep the withdrawn behavior.
- Declared divergences, each pinned to the exact value on both sides and failing
  the run once it no longer matches: `session-guard-b1b-v1` (R15-4 for U34,
  R15-3 for U07 and U08), `session-commit-b3-v1` (DE27/DE28/DE45 for U18, R15-4
  for U34 and U16, R15-3 for U08), `session-documents-a-v1` and
  `canonical-checkpoint-b-v1` (U18), `generation-b-v1` (DE25, U46) and
  `headless-cli-v1` (DE17, U04). Each is an open item of its owner.

### R15-2: restart-limit endings instead of parking and retry

- Remove restart parking and same-task retry: the Product and SDK `retry_task`,
  the `pipy durable retry-task` command, the private scheduler retry, and the
  inspection field `restart_parked` and hold reason `restart_limit`. There are
  no deprecation shims (backlog working rules). The DE10 `authorize-retry` new
  attempt is a separate native operator resolution and stays.
- A generic, extension-kind task at the limit ends `faulted` with detail
  `{ reason: "restart_limit" }` when the scheduler reserves it, reading the
  current policy. A limit raised before that reservation lets it run, and a task
  that ended stays ended. The DE14a limit normalization and the DE14b2 policy
  publication stay.
- Built-in endings: a model-issued `pi.tool` call ends with an error result
  with diagnostic code `restart_limit`, built from its slot like an interrupted
  call, and its task fails with that detail. A `pi.generation` ends its run
  without an answer, its inputs settle `unanswered` with reason
  `restart_limit`, and a run that already answered completes. Built-ins check
  the count when a phase starts. Native durable compaction does not exist yet;
  DE29 owns its `compactionFailure` ending.
- An abort handler cut off at the limit still faults `restart_limit`, as DE16a
  delivered.
- Until DE17 lands, a generation request in flight at host death is still
  resent, so repeated host death ends such a run `restart_limit` instead of the
  1.5 `interrupted`. Reference cases therefore cut off phases that 1.5 counts
  toward the limit: generic tasks, tool calls, and generation phases other than
  an in-flight request or poll.
- A stored task at or past the limit, which 1.4 parked, ends visibly at its next
  reservation; tests open a parked 1.4-shaped store.
- Each ending records its reason and restart count for the DE39 reports.
- References: successors of the withdrawn phases from `harness-blocked` ("faults
  an extension's task cut off restartLimit times in a row instead of running it
  again", "reads restartLimit when it reserves the task: a raised one runs it
  again") and `harness-restart-failure` ("ends a tool call with an error result
  instead of running it again").
- Docs: rewrite the restart parking guide as a restart-limit guide and update
  the Product, headless and inspection guides and the release notes. P07 and P21
  remain OPEN.

R15-2 lands as four reviewed commits; together they carry the whole acceptance
above:

- **R15-2a, same-task retry removal.** The Product and SDK `retry_task` with
  `DurableTaskRetry`, its `continuation_failed` latch and error code, `pipy
  durable retry-task` and the private scheduler retry are removed, without
  deprecation shims. Resume keeps its run ownership and close semantics.
  Parking itself stays until R15-2c: until then a fresh owner with a higher
  limit, or the private DE14b2 setter, runs parked work again.
- **R15-2b, built-in restart-limit endings.** The native `pi.tool` and
  `pi.generation` phases check the count when they start and end themselves at
  the limit as described above, and parking no longer applies to these kinds.
  The native `settle` phases commit only what a receipt already holds, so they
  settle it at the limit too. The successor of the tool reference test is the
  `safe_limit` case of `interrupted-tool-continuation-v1`, from an actual 1.5
  run.
- **R15-2c, generic restart-limit fault.** A generic task at the limit ends
  `faulted` with detail `{ reason: "restart_limit" }` when the scheduler reserves
  it; parking, `restart_parked` and the `restart_limit` hold reason are removed;
  the four withdrawn cases of `restart-policy-live-v1` come back from actual
  1.5 runs; and the restart parking guide becomes the restart-limit guide.
- **R15-2d, migration and orphan successors.** The successors of the withdrawn
  `parked-migration` case of `task-migration-cold-v1` and of the parked
  controls of `task-orphan-missing-definition-v1` and
  `task-orphan-failed-migration-v1`, from actual 1.5 runs: a task at the limit
  ends `restart_limit` before its definition is resolved or migrated.

### R15-3: batch Storage commit

- The Storage port's `commit` takes batches and a receipt callback and returns
  how many batches were stored and an optional refusal, in Python spelling.
- Memory and SQLite implement it natively. Each batch is checked against the
  state the earlier batches left (global IDs, document actions, copy
  resolution) before it writes. The first refused batch and everything after it
  are not stored. Any other failure rejects the whole call as an unknown
  outcome; SQLite runs one transaction per call, and a database error during the
  checks is not a refusal. `receive(index, seq)` runs once per stored batch, in
  order, before the next call, and every batch has at least one write.
- The Session sends one batch per call (D1). A stored commit whose call then
  rejects is published, its waits resolve, it rejects with the storage error,
  and the Session fails. A call that reports a batch stored without its receipt
  fails the Session, and a refusal rejects its commit and fails the Session.
- The narrowed `mint_id` contract is documented and tested.
- The optional `publishing()` hook is part of the portable Storage port, which
  plan §1 keeps for later hosts: the Session runs each publication through it
  when a backend provides one. Memory and SQLite provide none.
- A test helper equivalent to `commitWrites()` replaces direct one-batch
  commits in tests.
- Port the 1.5 storage conformance batch cases (stored prefix, refusal index,
  one seq per batch, copies against the state of earlier batches) to Memory and
  SQLite. The JSONL batch append is DE40.
- Re-derive the storage and Session comparison phases on the batch API. P01 and
  P03 remain OPEN.

### R15-4: watch, report and observer shapes

- The Session reporter emits a report tagged `storage_failed` that carries the
  error, once and after the seal, as now.
- Rename the private Session-failing listener API first, then make the public
  commit observer `observe_commits(observer)` with `advance` and `end`,
  returning a detachable observation. `end` comes once: `failed` with "session
  closed" at close, following the source over spec §4, and `failed` with the
  error when the Session fails. An observer that raises, or whose advance
  returns a failing awaitable, is reported, detached and ended
  `listener_error`.
- Publication order: own observers, then host commit observers, then wait
  continuations and abort signals. An own observer's error fails the Session
  only after the host observers got the commit, and the commit stands.
- Tag document changes in publications (`change` or `retire`) and the Session
  end (`closed` or `failed`).
- End reasons of the delivered completed projection take the 1.5 kinds where
  the cause is the same: `session_closed` becomes `ended`, and the projection
  ends `failed` for a Session failure and `aborted` for a cancelled acquisition
  where it can observe those causes. Native-only reasons stay documented as
  native.
- New 1.5 observation features (document, conversation and task graph
  observers, windowed views, folded delivery) stay with DE28.

### Then: the DE16 remainder

DE16 resumes against 1.5 with D1: owned cascade, public abort, generic failFast
and sibling cancellation, wait cancellation, close and admitted-commit ordering
including U17, restart abandonment and process groups. Its invocation gates
follow the D1 consequences above.

## Plan amendments made with this bridge

- §1 names the new target and the earlier ones; the status header notes the
  retarget.
- §3.2 and §3.4 state D1, deferred progress commits, durability receipts and
  the restart-limit endings in place.
- §7 has a dated owner note with this bridge's decisions, including the release
  adoption rule.
- §4 P rows P01, P03, P07, P10–P15, P17, P19–P23, P25, P27, P28, P30, P34,
  P35, P40 and P42 carry release 1.5 clauses dated by the §7 note. P01 and P28
  also mark their removed upstream tests, and P26 and P32 note the renumbered
  examples.
- §5 rows DE14, DE14a, DE14a1, DE14b, DE16–DE20, DE22, DE24–DE26, DE28,
  DE29, DE33, DE34, DE39, DE40, DE43 and DE45 carry release 1.5 clauses. DE17
  drops "parked work wake behavior", because 1.5 removed parking, and takes the
  in-flight request charge of U04.
- The coverage index mirrors every amended row, as its plan-row check requires.

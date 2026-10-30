# DE16a terminal-only ordinary root-leaf abort

## Implemented scope and source-qualified evidence

This internal implementation follows the Root-adopted terminal-only DE16a plan
against literal Pi release `4a080252f06caf63a525d7873c5d94d3c23887e0`.
The initial source-admitted input was joint 4b075 plus the separately adopted
Details inventory correction. That authoring checkpoint was not a runtime or
CODE result. Inherited DE15c 447/7ec9 and advisory CODE 3950 remain their own epochs.
The plan's raw second review remains ISSUES 0C/1W: Root disposed the remaining
validator lookup omission as documentary and verified the five-anchor addition,
without relabeling the verdict CLEAN.

Native 0d3 finite Core/retained checks collected 523: 522 PASS and one explicit
Memory cold-reopen skip, with the actual SQLite counterpart passing. Whole
mypy 1151/Ruff/format 1283 and configuration/strict-doc checks passed on that epoch.
The subsequent dfdb Broad33 and migration62 checks were separate actual runs;
physical cuts distinguish marking, reservation and fsynced callback entry.
CODE R1 was advisory 0C/0W/2S. Its malformed-state refusal and wording repairs were
validated on 512a by 115 scoped cases: 114 PASS plus the same qualified Memory skip.
The six exact malformed-state regressions failed on the old production and passed
on the repair. Independent CODE R2 was CLEAN on 512a; R1 stays advisory.

Combined b06 retains those reviewed executable bytes and the separately reviewed
joint 4456 fixture/spec repair. Its own 27-case module, whole mypy 1151/Ruff/format 1283
and current 34 configuration/coverage/strict-doc checks passed. None of these
separate scopes is summed or relabeled as a combined Full, paired-Pi, delivery
or all 43-parent result. All nine corrected literal release test pins are held.

## Private request and invocation authority

`TaskScheduler.aabort_leaf(task_id)` returns `marked` or `terminal`. This is an
internal API, not a Product/CLI/public abort export. Existing safe-ID, reentry,
Session failure/close and opened-owner gates apply. The scheduler must be paused
and actually retired. Terminal repetition writes nothing. Otherwise the target
must be an ordinary pending root leaf with a current exact-version definition:
no reserved model/tool kind, owner, owned conversation or child (including ended
children), inbound wait (including a marked waiter), abandonment flag or restart
reason. Refusals preserve durable state. Unknown IDs use ValueError; semantic
refusals use UnsupportedTaskExecution; activity/open/lifecycle failures retain
the existing native families.

The first real marking transaction preserves the checkpoint/documents, sets
abortRequested and removes the previous run's restarts field. Repeat marking
rechecks the current definition and complete topology, acknowledges without a
write, and never resets the abort counter. Its ACK neither enables dispatch nor
proves handler entry. Actual precommit rollback and lost COMMIT acknowledgement
retain Session authority; no synthetic successful ACK is returned.

Explicit resume reserves running+mark on the real Session line and captures a
frozen execution carrier containing the definition, phase map and run/abort mode.
All mutable invocation/lifecycle fields remain under the existing condition.
The captured carrier is never reassigned. The existing current-token checks,
FIFO, host ownership and close joins remain in force; no extra queue or durable
mode/cache is introduced. Run mode still refuses abort-marked work. Abort mode
admits only its own actual requested ordinary running root leaf. Topology uses
the real staged Tx graph; there is no caller-supplied graph or fake unmarked row.
The existing unmarked orphan predicate and synchronous ownership-query ABI are
unchanged. New immutable async graph projections retain only waiting IDs.

The abort callback is invoked once per actual reservation. Task-local memo/doc
work and commits returning no next state are supported. A genuine terminal state
performs normal atomic memo/document retirement. Proposed pending/running,
waiting (including allSettled), sleeping/completing states or new ownership that
breaks the root leaf refuse before transaction publication. Raw replacement of
the current invocation record is refused. Existing unrelated typed transaction
operations are not granted extra authority. Intentional ID mint consumption is
separate from durable rollback. External effects preceding a refused commit
are not undone.

A caught refusal may be followed by a supported terminal commit. An ordinary
callback exception, or return while still running without a terminal outcome,
produces the real faulted terminal transaction. Terminal-then-throw does not
overwrite the published outcome. Failed storage, Session failure, close or lost
current-token authority cannot be converted to successful fault publication.
Close signals and joins the real callback/finalizers before releasing storage;
caller cancellation does not cancel accepted marking work. Orderly stopped-row
rewrite preserves pending+mark and its counter without a host-death increment.
A callback ignoring its signal can keep close pending.

## Interrupted attempts, definition hold and cutoff

A durable pending mark is intent, not a prior invocation. Opening it invokes
nothing and adds no restart. Actual running+mark recovered after process death
becomes pending with the same checkpoint and restarts+1. A reserved-before-entry
cut proves an interrupted reservation, not callback execution; a separate actual
callback-entry barrier is required for that claim. External cleanup may repeat
across interrupted attempts.

For an eligible requested pending leaf, the current finite restart cutoff runs
before definition resolution. Exhaustion commits faulted with
`error.detail.reason = restart_limit` and normal cleanup without a callback.
Positive infinity creates no cutoff. Topology, reserved-kind, lifecycle and mode
admission still precede this decision. The existing run parking policy remains
unchanged and does not park abort marks.

In the original DE16a boundary, missing/older/newer definitions held the row
without migration, orphaning, reservation, counter growth or reset. The private
[DE16b successor](de16b-marked-missing-definition.md) now settles only absent code
for an already-marked eligible leaf, preserving cutoff-first ordering. Present
older/newer code still holds indefinitely; there is no broader blocked-abort
escape or global termination bound. Restoring an
exact-version definition can admit a later actual invocation. Same-version
replacement takes effect only at the next reservation. The existing publication
API refuses while draining or an invocation is active: a test must not fabricate
concurrent registry publication. After retirement, a new reservation captures the
new binding. No persistent callback fingerprint or cached obsolete authority is
added. Terminal repeat/reopen never reinvokes cleanup.

## Validation ownership and narrower boundary

Core fixtures use genuine Task/Session/Tx creation, mark/resume, terminal and
no-state/error/candidate gates, frozen mode, close joins and definition hold.
Numeric/kernel recovery controls are labeled; they do not claim physical death.
Broad owns actual SQLite COMMIT/ACK cuts, mark-only versus reservation versus
observed callback death, FIFO/late graph, cold reopen and lease/finalizer joins.
The actual scoped results are recorded above. Typed numeric recovery controls
are not physical-death evidence. The helper, Core and Broad sources are in the
complete inventory; existing profile membership changes are limited to the
new helper required by unchanged dynamic native-source enumeration. No new Pi
profile or old predicate weakening is claimed.

Pi release4a08 supplies marking/accounting (scheduler.ts542–573, 2390–2397),
cutoff ordering (1521–1554), callback/fault handling (1858–1935) and wait refusal
(1977–1981). Pi permits other nonterminal abort programs which this initial
subset refuses. Future pairing covers only actual terminal/throw/no-outcome
programs, retaining raw API/state/lifetime differences. Pi abortTask itself does
not resume; waitForTask/waitForIdle can enable and must be labeled accordingly.
Active cancellation, public abortTask, cascade/abortOwned, sibling failFast
settlement, blocked-abort migration/orphan policy, abandon-on-restart execution,
sleep/timers, full P09/DE16 and parent acceptance remain deferred.


## Active-run successor

[DE16c](de16c-active-ordinary-leaf-abort.md) separately extends internal marking
to the exact occupied ordinary run-mode strict leaf and joins its actual
physical unwind. The paused/retired admission statements above describe this
source checkpoint's original slice; its candidate, terminal-only cleanup,
cutoff/missing/version rules and source-qualified receipts remain unchanged.
Root accepted the separate 1f50 focused runtime epoch (`a9fee928`): mypy
1155/Ruff/format 1287, Core 166 = 165 PASS plus the known Memory cold
skip with SQLite counterpart PASS, Broad 51 PASS, current34 and strict docs.
Its four new cuts plus eleven retained cuts and fourteen OPENER records are
bounded observations, without exhaustive, exactly-once, atomic-counter or
all-platform authority. The DE16c PLAN remains advisory, not CLEAN; its actual
C7 runtime tests are not independent review. At this status checkpoint CODE,
public Pi comparison, Full and Main delivery remain open. No owned cascade,
generic failFast, public abort or parent closure is added; older receipts keep
their own source qualification.

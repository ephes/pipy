# DF1-F4b retry closeout contract

Status: all three slices and all four F4b bullets are complete. Slices 1–2 are
pushed; slice 3 passed review and verification, with final commit pending. Follow-on to
[DF1-F4](../parity-loop/plans/f4-retry-visibility.md) and the
[F4b backlog](../backlog.md#follow-ons).
The original red regressions land with the green slice-1 implementation.

## Ordinary attempt lifecycle and durable history

The canonical executor owns classification, captured settings, bounded delays
and attempts, and exact RPC retry leases. Before announcing a retry, it hands the
actual failed ProviderResult to the canonical loop on the session thread. The
loop constructs a stopped assistant using that attempt's streamed blocks/result,
including F6b stopped-content and CM1 parent-only bounded attempted-intent rules.
Existing guarded history owners and MessageCompleted →
ProductSessionEventProjection → CodingProductSessionCoordinator remain the only
history/tree writers. Every retry error survives reopen and is excluded from
ordinary replay. No executor filesystem writes or second history owner exist.

Attempt visibility uses existing AgentRunStarted/Completed and turn/message
events. Separately, new AgentRunSettled carries one terminal cumulative logical
AgentRunResult per accepted prompt, including without retries. This event is
internal bookkeeping; it has no JSON/RPC envelope or extension lifecycle hook.
Intermediate endings must not settle queues, release RPC claims, reset usage,
or publish an SDK final result. Hooks/budgets/request assembly and accepted user
admission run once. Prepared requests/headers remain frozen across reissue.

For each retryable failed attempt, visibility order is MessageCompleted(error),
TurnCompleted(failed), AgentRunCompleted(will_retry=true), RetryScheduled. After
backoff and guarded reissue admission, publish AgentRunStarted, TurnStarted,
and a fresh assistant MessageStarted before any provider delta. Never re-emit
accepted user/system messages on continuation. Attempt-local agent_end.messages
contains the first low-level run's accepted user/system plus generated messages;
continuations contain only their own assistant/tool/system appends. Final SDK
result from AgentRunSettled remains the logical cumulative enumeration. A failed
attempt's real usage is absorbed and stored accurately; missing usage is not
invented. Existing total/cost formulas stay unchanged, although new billable
attempt records can increase totals.

Accept/persist failed content before announcing its retry. Append failure escapes
without reissue or fake success. Preserve state-first acceptance and primary
persistence exceptions. Call lifecycle observers outside mutation locks, then
revalidate the original run witness before reissue admission and every mutation;
observer changes cannot authorize stale work. A lost witness may clear status
but cannot append into newer context.

| Consumer | Ownership after change |
| --- | --- |
| Automation adapter | Per-attempt starts/ends, attempt-local arrays; no AgentRunSettled projection |
| RPC worker bridge | Consume exact ordinary-run claim only from logical settlement callback at composition root; serialized publish/queue reservation retains existing ownership |
| Extension lifecycle adapter | Per-attempt agent/turn hooks; no logical-settled hook; request hooks remain once |
| SDK adapter | Final cumulative result only from AgentRunSettled |
| ProductSessionEventProjection | Persist each real MessageCompleted; reset per-attempt bookkeeping without re-appending accepted user/system |
| Workflow archive counters | Attempt run counters count visibility boundaries; separate fixed logical-settlement counter; no message/error bodies |
| UI reducer/renderers | Completed stopped assistant owns partial and Error row; schedule owns countdown only |

## Backoff and active cancellation

Backoff cancellation leaves the accepted error assistant intact. Externally it
emits only RetryCompleted(finalError=Retry cancelled) after the prior intermediate
agent_end; it creates no fresh assistant, canonical RunCancelled, agent_start or
additional agent_end. Internally AgentRunSettled is CANCELLED with the precise
OPERATOR_ABORT, STEERING, LOCAL_COMMAND or PROVIDER_CANCELLED reason. The
existing CodingAgentTurnStatusEffects.provider_cancellation_observed owner restores pending
input on operator abort and promotes it on steering/local commands; preserve its
existing provider-cancelled routing. SDK observes that cancelled logical result.
RPC consumes the exact accepted claim once through the logical settlement callback
and can reserve/wake a successor; agent_settled remains the existing true-idle
boundary. NativeRpcServer projects willRetry=true agent_end immediately without claim
consumption. Hold at most one pending willRetry=false envelope under its existing
mutex until AgentRunSettled; publish that final envelope atomically with exact
claim consumption using consume_attached_agent_end_and_publish, before successor
start. A client observing terminal agent_end must already observe settled state.
Backoff cancellation has no pending terminal envelope but still consumes once.
Clear the bounded pending slot on publication or callback failure; preserve the
primary failure and existing pre-end cleanup rather than consuming twice. Do not encode FAILED with a cancellation reason.

Cancellation before initial completion or during a running retried provider
retains F6b aborted partial semantics: one aborted assistant/turn, final attempt
agent_end, then one internal logical settlement. Exact abort-won and lease
retirement remain authoritative. One auto_retry_end closes the sequence.

MessageCompleted owns live failed partial/error rendering, matching resumed
history; RetryScheduled adds countdown only. PartialAssistantContent, automation
partial and UI stream resets happen on actual fresh attempt admission, never by
throwing away unaccepted failed blocks on retry scheduling.

## Footer estimate (slice 2)

Reuse agent.history.estimate_context_tokens. Read the active branch/provider
projection's stored last positive successful assistant usage and independently
rounded chars/4 for each trailing message. Never anchor on process-lifetime
last_total_tokens. Resume trusts the current branch's stored anchor; a same-history
model switch keeps it, as Pi does. Failed/aborted usage is not an anchor. With no
anchor, or a compaction whose covered prefix is unknown, estimate assembled
current context. This closes the resumed-context-anchor portion of USAGE1b;
update that backlog alongside F4b. Session total/cost formulas remain intact.

## Auxiliary retries (slice 3)

Widen compaction/branch-summary retry availability to every provider with captured
settings and jitter-free delays. Preserve prepared handles/frozen requests where
available, existing classifier without a progress/payload/usage veto, one preflight,
bounded F5b recovery requests, original currentness witnesses and state-first
publication. This is a behavior change requiring provider-policy regressions and
compaction/provider docs updates, beyond status visibility alone.

Use dedicated bounded status events for schedule, actual attempt start and finish;
never ordinary assistant messages or auto_retry events. Never publish summary
text, reasoning, request bodies or provider error strings to transcript, automation
or metadata-first workflow surfaces. Status carries source/attempt/delay/outcome
and fixed generic failure classification only. Call observers outside mutation
locks and revalidate after them. Clear status on success/exhaustion/cancel/stale/
exception. Persistence failure after acceptance never triggers summary retry.

Slice 3 owns precise JSON/RPC event names (`summarization_retry_scheduled`,
`summarization_retry_attempt_start`, `summarization_retry_finished`),
closed source/outcome enums and rendering. Generic accepted-operation abort cancels
auxiliary work; ordinary abort_retry remains a no-op for auxiliary phases because
no ordinary RPC retry capability is installed. Pin this through tests/docs.

## Reviewable increments and superseded contracts

1. Intermediate attempts/persistence, balanced visibility, separate logical
   settlement and backoff cancellation. Replace these three original F4 tests:
   test_retried_then_successful_turn_stores_only_the_success,
   test_finally_failed_retry_stores_one_error_message,
   test_abort_during_the_retry_wait_stores_one_aborted_message. They deliberately
   pin superseded behavior; their baseline passing is not a preservation promise.
   Update harness-spec retry lifecycle/no-intermediate-event paragraphs and
   agent_end enumeration, automation-rpc agent_end and retry-control paragraphs,
   sdk retry/final-result paragraphs, and F4 plan Deviations kept.
2. Current-projection footer estimate with resume/branch/model/compaction tests,
   F4b/USAGE1b backlog and usage/provider docs.
3. Sanitized every-provider auxiliary status with cancellation/stale/budget/privacy
   coverage, compaction/session-storage/automation/sdk docs and F4b closure.

Each slice needs focused production tests, release notes, just check and mandatory
independent review before commit/push. Preserve import/complexity/isolation gates.

## Slice-1 executable baseline and acceptance obligations

`uv run pytest -q tests/test_native_retry_visibility.py -k f4b` exits 1
with six intended failures; Ruff lint/format and `uv run mypy src tests` exit 0.
The original 41 tests pass on unchanged source, including the three superseded
assertions listed above.

Current baseline production-path regressions cover synchronous and interruptible
CodingSession durable reopen, exact automation boundary ordering, attempt-local
arrays, turn balance and unique accepted user, plus backoff error preservation and duplicate TUI/legacy Error rows.
The existing baseline fails as intended, with no network/global settings writes.
These tests cover slice 1 only; footer/auxiliary baselines belong to later slices.

Before slice-1 acceptance, additionally pin the new AgentRunSettled SDK final-only
ownership (not executable until the new API exists), RPC queued steer/follow-up
claim/true-idle behavior across two retries and cancelled backoff, one RetryCompleted
with Retry cancelled and exact cancelled result/reason without Operation aborted,
active-retry cancellation, stale observer/reissue/currentness failure, primary
persistence failure/no reissue, mixed thinking/text/partial calls and CM1 stopped
metadata, and TUI/legacy one Error row per failed attempt. Existing RPC abort_retry
queue tests remain mandatory. No test weakening, skips or xfails hide failures.

### Additional superseded baseline expectations (slice 1)

Replace stopped-turn-display retry_wait_abort's one abort marker with none;
replace reducer, partial-recorder, aborted-turn and automation partial resets on
RetryScheduled with reset on actual attempt admission/MessageStarted. Update
TUI retry loader and legacy renderer tests to complete the stopped assistant
before scheduling, expecting countdown-only scheduling. Update coding-session
retry and end-to-end Codex counts to include every failed attempt ProviderFailed,
TurnCompleted and AgentRunCompleted, while one AgentRunSettled closes each logical
prompt. Cancelled backoff emits zero RunCancelled; logical CANCELLED settlement
counts one workflow cancellation and invokes existing status routing directly.
ProductSessionEventProjection does not suppress its already completed error.
Update providers.md retry/history/cancellation paragraphs, sdk.md worker-claim
settlement at canonical event paragraphs (including 690/786/814), and slice-1
F4b backlog bullets. Manual-compaction claims keep their separate control owner;
AgentRunSettled only changes ordinary-run settlement.

Ordinary currentness compares binding identity/context_epoch under the existing
shared RLock and allows this run's own history appends. It does not compare full
history/tree equality; auxiliary summaries retain their stricter full witness.

### Explicit baseline replacements verified by slice 1

- test_tui_retry_shows_failed_attempt_then_pi_countdown_loader and
  test_legacy_renderer_writes_pi_retry_lines now render the stopped message before
  countdown, with one Error row.
- test_reducer_maps_retry_events_and_restarts_the_stream,
  test_automation_partial_restarts_after_a_scheduled_retry,
  test_partial_recorder_orders_segments_and_restarts_on_retry and
  test_a_retry_discards_the_failed_attempts_partial_text reset on fresh admission,
  not scheduling, while preserving the completed failed attempt.
- test_retry_wait_abort_keeps_existing_retry_notice_and_one_abort_marker becomes
  retry notice without an abort marker.
- test_policy_is_captured_per_request_and_next_prompt_refreshes and the end-to-end
  Codex tool-loop trace include failed-attempt provider/usage/turn/agent events,
  but only one logical settlement per prompt.
- test_cancellation_during_backoff_balances_retry_and_prevents_reissue records no
  RunCancelled and one cancelled logical settlement. Active retry cancellation
  retains RunCancelled and an aborted partial.
- test_stale_context_before_failed_attempt_acceptance_rejects_publication now
  refuses before failed-message acceptance/scheduling; no stale append is allowed.

New tests cover SDK final-only result, state-first failed-attempt append failure,
post-start-observer currentness refusal, ordered stopped blocks and unexecuted
codemode parent usage, two-retry RPC claim/follow-up ordering and no duplicate Error
rows. Existing RPC abort_retry/steer and terminal get_state tests remain green.
Independent isolated actual CLI JSON/PTY verification confirms durable reopen,
resume/tree, backoff cancellation and active-retry abort; these are deterministic
fixture checks, not new live-provider quality evidence.

### Slice 1 closeout evidence

At slice-1 closure, footer estimation and private-summary retry visibility were
pending; both are now complete. Independent verification completed with 8,074 tests passed and two
skipped, 49 runtime checks and eight PTY checks passed, and documentation/static
checks exiting zero. The isolated synthetic CLI checks above also passed.

Two design rounds and two code rounds used installed Claude Code Opus 5.5 at
high effort. The final code review reported no Critical or Warning findings and
two advisory suggestions: place normative lifecycle prose beside its table and
bound the two new RPC fixtures' isolated retry delays. Both were applied. This
is advisory closure, not a CLEAN verdict. The hypothetical explicit observer API
refactor and optional unused-field/signature cleanup were declined because no
current wrapper defect required them and they would add unrelated port churn.

## Slice 2 footer implementation

Seven deterministic CodingSession strict durable-reopen/chrome regressions failed
on unchanged production (exit 1), then passed using the existing history
estimator. Coverage pins 1,000 stored tokens plus two separately rounded trailing
characters as 1,002, branch history independent of nonzero lifetime usage, failed
and aborted samples ignored as anchors, distinct model denominators, system and
summary suffix counted once, and no-anchor full estimation. A later successful
post-compaction sample still uses full estimation while the suffix remains.

Chrome captures immutable coding result state, current branch system anchors and
all-branch totals with the coding-effect/tree coordinator lock first, then the
distinct coding-state/generation lock, and renders outside both guards. Chrome
requires the existing session_tree_section context port, which selects the live
tree pointer and holds the outer coordinator through projection capture. A
production two-lock subprocess regression reproduced a deadlock in the initial
footer implementation; the reversed order is forbidden. Existing estimator logic owns text/thinking/tool character counting.
The old fixed turn/tool guess is removed. No private REPL helper is imported.
The original footer test expecting unchanged telemetry after failed content is
replaced by a stronger stored-anchor-plus-failed-text assertion. No-provider and
stale active-run presentation remain read-only through result_snapshot, which
does not require an admitted provider run.

The full estimate retained after compaction is an intentional difference from
Pi's unknown meter until new valid usage. It is conservative, not a provider
acceptance guarantee. This closes only the USAGE1b footer restoration part; RPC
contextUsage remains deferred; auxiliary-summary visibility was then pending
and is now complete.

### Slice 2 verification and lock review

Independent checks on the repaired production implementation passed 8,085 tests
with two skipped, 49 runtime checks and eight PTY checks, plus documentation and
static checks. Isolated synthetic CLI branch/resume verification also passed.
The first Opus 5.5 high-effort footer review found the two-lock inversion and a
stale TUI documentation paragraph; both were fixed. The second review verified
production lock ordering and identified that the pointer-coherence test could
still pass a split-capture mutation. The strengthened test now observes pointer
assignment independently of state-lock admission and rejects that mutation in a
fresh owned source copy (exit 1 at the intended assertion). Child regression
failures distinguish deadlock from worker errors and include fixture tracebacks.
Production code remains unchanged after the independent full verification; the
test-only strengthening passed the third scoped Opus 5.5 high-effort review with
no Critical or Warning findings and one accepted diagnostic-formatting suggestion,
which was applied. All prior blocking findings are resolved. This is advisory
closure, not a CLEAN verdict.

Slice 2 is complete. Three footer code rounds used installed Claude Code Opus 5.5
at high effort: the first Critical and Warning findings were fixed; the second
Warning and Suggestion improved mutation sensitivity and child diagnostics; the
third advisory suggestion made parent diagnostics explicit. Full verification
remains 8,085 passed with two skipped, 49 runtime checks and eight PTY checks;
587 focused checks and an independent 11-test footer run passed. The isolated CLI
checks passed; both launched pipy instances exited zero. No full
suite was repeated after the test-only mechanical fixes because production code
was unchanged; final targeted/static checks cover that delta. Auxiliary retry
visibility was then pending in slice 3 and is now complete.

## Slice 3 auxiliary implementation contract

Unchanged production has six intended manual/automatic/branch regression failures:
plain providers make one failed summary call, while prepared providers already
retry but emit no dedicated status. All auxiliary policies will be captured once
for every provider with zero jitter, retaining frozen requests/handles, header
work, preflight bounds and the existing full summary witness. Progress, partial
payload and usage do not veto the shared retry classifier.

A dedicated coding/summary_retry status translator emits only
summarization_retry_scheduled, summarization_retry_attempt_start and
summarization_retry_finished through existing CodingSession automation observers
and renderers. Sources are compaction or branchSummary; outcomes are succeeded,
failed, cancelled or stale. Attempt and maximum are reissue ordinals/limits,
excluding the initial call; delay is bounded milliseconds. Failure text is fixed.
No optional reason is needed. No private message, error, thinking, delta, usage,
tool, request overlay or summary enters these records. Canonical AgentEvent SDK
subscriptions, lifecycle hooks, workflow archives and persistence receive none.

Status callbacks run outside mutation/generation guards; full witness admission
is rechecked after actual-start observers and before provider invocation. A
closed retry sequence reports provider execution, not accepted summary state;
later staleness/persistence failure does not rewrite a successful finished event.
A pending stale sequence closes as stale. Cleanup removes countdown/loaders on
every outer outcome and preserves the primary exception if notification fails.
Observer exceptions retain identity through generation owners; manual RPC retains
its existing generic command-failure projection. No auxiliary ordinary abort_retry
lease is installed; generic abort/Escape cancel existing auxiliary waiters.
Dedicated countdown takes precedence over compaction chrome and actual admission
restores Compaction or Summarizing branch loaders without ordinary Error rows.


Slice 3 implementation evidence before independent review: all six unchanged
production baselines failed for the intended provider-eligibility/status reasons.
The new production-path suite now has 85 passing regressions across plain and
prepared providers, manual/automatic compaction and branch summaries. It covers
backoff and active cancellation, exhaustion with frozen settings, stale admission
and acceptance, callback failures including BaseException, automatic lifecycle
cleanup, and accepted persistence failures. Projected accepted-but-undurable
results also survive secondary loader-cleanup failures; default exception mode
retains the exact persistence exception with a bounded secondary note. Scheduled
statuses retain Pi's errorMessage field using fixed generic text.

Focused existing retry/compaction and strict architecture checks passed together:
493 tests. Ruff lint and format checks passed (710 files), and mypy passed over
662 source files. Final independent full verification, actual CLI validation and
Opus 5.5 high-effort review were then pending; final closure is recorded below.


Auxiliary review round 1 accepted a Critical loader-cleanup defect, a Warning
for three stale documentation passages, and an early branch-loader Suggestion.
Four unchanged-production regressions failed at the intended idle-spinner and
unadmitted-loader assertions. Cleanup now stops and clears without restarting
idle Working chrome. Loaders arm only after work admission, followed by full
witness revalidation outside callback guards. The renderer test now follows
production order: compaction lifecycle end precedes dedicated loader clear.
Independent verification before this repair completed with 8,170 passed and two
skipped, 49 runtime checks and eight PTY checks; those results describe the
pre-repair snapshot. A fresh scoped review and final verification were then pending.


Auxiliary review round 2 accepted three test/documentation Warnings and one SDK
wording Suggestion; no production defect was identified. New caller-path tests
pin loader OSError/KeyboardInterrupt identity, zero provider calls, automatic
lifecycle end, post-loader stale admission, and blocked preparation, real header-capture staleness, and empty-branch
returns without loader activation. Branch idle cleanup no longer calls the
unrelated compaction end helper and asserts the animation thread is absent.
The visibility suite passes 100 tests. Fresh owned source copies reject deletion
of loader exception attribution (four failures) and initial witness admission
(three failures); both mutation processes exit 1 for the intended assertions.
Independent pre-test-delta verification passed 8,173 tests with two skipped and
49 runtime checks. Production remains unchanged in this repair; a fresh scoped
review and final verification were then pending.


Auxiliary review round 3 accepted two Warnings and one outcome-assertion
Suggestion. The early-stale case now mutates the actual tree epoch during the
stubbed header callback at the real production capture site, rather than mocking preparation. The synthetic refusal is
explicitly named blocked preparation, not budget coverage. Manual stale outcomes
and branch stale results are pinned separately from generic generation failure.
An import-order defect was repaired. The earlier lint success report was wrong:
a chained format command masked the lint failure; final lint is now executed and
reported separately. Independent isolated CLI verification passed all eight
instances, and its strict documentation build passed with no warnings. Existing
full/runtime/PTY evidence remains recorded above; final scoped review was then pending.


## Final F4b acceptance

All four F4b bullets are complete: durable ordinary failed attempts, backoff
cancellation without an extra aborted assistant, active-branch trailing chars/4
footer estimates, and private-safe auxiliary retry visibility for every provider.
Auxiliary review used installed Claude Code Opus 5.5 at high effort for four
rounds. Round 1's Critical loader cleanup and Warning documentation defects were
fixed; its early-loader Suggestion was applied. Rounds 2 and 3 strengthened
caller-path, stale-outcome and mutation-sensitive tests and corrected docs/lint.
Round 4 had no Critical or Warning findings and one accepted exact header-stale
outcome assertion, now applied. All blocking findings are resolved. The gate
closed on advisory convergence, not a CLEAN verdict; no further review is needed
for the final assertion and completion wording.

Final independent full verification passed 8,185 tests with two expected skips;
49 runtime checks and eight PTY checks passed. Isolated actual CLI verification
passed eight instances, including auxiliary retry/cancellation and strict reopen.
The independent strict documentation build completed with zero warnings.
Production is unchanged by the final assertion/docs delta. Final focused/static
checks cover that delta before committing slice 3. Privacy projections and the
conservative compaction footer estimate are the documented intentional Pi
differences; RPC contextUsage remains outside this completed F4b scope.

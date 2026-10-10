# DF1-F4b retry closeout contract

Status: slice 1 implemented and awaiting code review; slices 2–3 pending. Follow-on to
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
`summarization_retry_attempt_start`, `summarization_retry_finished` proposed),
source/reason enums and rendering. Generic accepted-operation abort cancels
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

Slice 1 is complete; footer estimation and private-summary retry visibility remain
pending. Independent verification completed with 8,074 tests passed and two
skipped, 49 runtime checks and eight PTY checks passed, and documentation/static
checks exiting zero. The isolated synthetic CLI checks above also passed.

Two design rounds and two code rounds used installed Claude Code Opus 5.5 at
high effort. The final code review reported no Critical or Warning findings and
two advisory suggestions: place normative lifecycle prose beside its table and
bound the two new RPC fixtures' isolated retry delays. Both were applied. This
is advisory closure, not a CLEAN verdict. The hypothetical explicit observer API
refactor and optional unused-field/signature cleanup were declined because no
current wrapper defect required them and they would add unrelated port churn.

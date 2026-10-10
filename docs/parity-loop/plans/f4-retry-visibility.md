# DF1-F4 retry visibility — plan

Gap: `docs/backlog.md` follow-on **DF1-F4** (evidence:
`docs/acceptance/2026-09-29-df1-dogfooding.md` scenario 9). Three symptoms:

1. Provider retries are silent in the TUI: up to ~14 s of `Working...`
   while Pi shows `Retrying (n/3) in Ns... (escape to cancel)`.
2. A Codex stream `error` event is never retried.
3. After a failed turn the footer context jumps (0.6% -> 7.2%) until the next
   success.

Pi reference: `~/src/pi-mono` @ `4df157433`.

## What Pi does

- **Agent-level auto-retry for every provider**
  (`coding-agent/src/core/agent-session.ts` `_handlePostAgentRun`,
  `_prepareRetry`, `_isRetryableError`, `_finishCancelledRetry`). After a run
  ends with an assistant message whose `stopReason === "error"`, Pi checks
  `isRetryableAssistantError` (`ai/src/utils/retry.ts`) — two regexes over
  `errorMessage` only: a non-retryable quota/billing pattern wins, else the
  retryable pattern (overloaded, rate limit, 429/500/502/503/504/520/524,
  service unavailable, server/internal error, network/connection/socket/fetch
  failures, timeouts, websocket closed/error, "ended without", "you can retry
  your request", ResourceExhausted, ...). Context overflow is excluded first.
  There is **no progress condition**: a message that already streamed text and
  then errored is retried. The failed message stays in raw history but is
  omitted from the model projection.
- **Policy:** settings `retry.enabled` (default true), `retry.maxRetries`
  (default 3), `retry.baseDelayMs` (default 2000),
  `retry.maxAgentDelayMs` (default 60000). Delay =
  `baseDelayMs * 2^(attempt-1)` capped, **no jitter**, no server hint.
  `attempt` counts retries (1..maxRetries); the first call is not a retry.
- **Events:** `auto_retry_start {attempt, maxAttempts=maxRetries, delayMs,
  errorMessage}` once per scheduled retry, before the backoff sleep.
  `auto_retry_end {success, attempt, finalError?}` **once per retry sequence**:
  success on the first later non-error assistant message; failure with the
  final error message when retries are exhausted or the later error is not
  retryable; `finalError: "Retry cancelled"` when the sleep is aborted
  (`abortRetry`) or the run is aborted while `_retryAttempt > 0`.
- **Interactive rendering** (`modes/interactive/interactive-mode.ts` 3631-3657,
  `components/status-indicator.ts` `RetryStatusIndicator`,
  `components/countdown-timer.ts`): each failed attempt renders as an
  assistant message with its partial content and an error-coloured
  `Error: <errorMessage>` line. `auto_retry_start` swaps the working loader
  for `Retrying (<attempt>/<maxAttempts>) in <ceil(delayMs/1000)>s...
  (<keyText(app.interrupt)> to cancel)`, spinner in `warning`, text in
  `muted`, the seconds counting down once per second to 0. Escape during the
  sleep calls `abortRetry`. `turn_start` of the retried run shows the working
  loader again. `auto_retry_end` with `success: false` shows
  `Retry failed after <attempt> attempts: <finalError || "Unknown error">`.
- **Codex stream errors** (`ai/src/api/openai-codex-responses.ts`
  `mapCodexEvents`): an `error` event throws
  `Codex error: <message || code || JSON.stringify(event)>` (message/code
  from the event or its nested `error`); `response.failed` throws
  `<response.error.message || "Codex response failed">`. The agent-level
  classifier then decides on that text. Pi's provider-level Codex fetch retry
  defaults to `maxRetries = 0`.
- **Footer context after an error** (`agent-session.ts getContextUsage`,
  `compaction.ts estimateContextTokens/getAssistantUsage`): the usage source is
  the last assistant message that is not `error`/`aborted` and has
  `calculateContextTokens(usage) > 0`, plus a chars/4 estimate of the messages
  after it. A failed turn therefore never resets the value.

## pipy today

- `agent/provider_turn.py` `ProviderTurnExecutor` owns a caller-managed retry
  loop, but only for `PreparedProviderPort` providers (OpenAI Codex); every
  other adapter makes one attempt and has no internal retry. `repl/loop_step.py`
  builds the policy only for prepared providers and keeps
  `RetryPolicy.jitter_seconds = 0.25`.
- `agent/provider_retry.py is_managed_retry_eligible` requires
  `metadata.retryable is True`, `metadata.progress == "none"` and no observed
  delta — a structured, no-progress rule. Codex marks progress on any event
  (including `response.created`), and classifies a stream `error` event as a
  non-retryable `OpenAICodexResponseParseError` with the fixed text
  `OpenAI Codex stream returned an error event.`.
- The executor emits `RetryCompleted` after **every** reissued attempt, so JSON
  and RPC see `auto_retry_start/end` pairs per attempt, and the cancellation
  text is `Retry was cancelled.`.
- `ui/state.py reduce` ignores `RetryScheduled`/`RetryCompleted`; the TUI keeps
  showing `Working...`.
- `agent/loop.py _publish_usage` publishes a zero sample for a failed result
  (`usage is None`), and `AgentUsageAccumulator.absorb` sets
  `last_total_tokens = 0`. `chrome.py _context_used_pct` then falls back to the
  per-turn heuristic (`2000 * turns + 1500 * tools`): the observed jump.

## Change (pipy-owned boundaries)

1. **Pi classifier.** Add `is_retryable_error_text(text)` to
   `agent/provider_retry.py` with Pi's two pattern lists copied verbatim
   (case-insensitive). Redefine `is_managed_retry_eligible(result)` as:
   `status is FAILED` and not the non-retryable limit pattern and
   (`metadata.retryable is True` or the retryable pattern matches). The text is
   `error_message` plus the sanitized `api_error_type`/`api_error_code`
   labels that pipy's HTTP errors lift from the body (pipy's messages do not
   carry the body, e.g. a 529 `overloaded_error` only reaches the classifier
   through its label). The structured `retryable` flag stays as an extra
   positive signal because pipy's sanitized transport messages (e.g.
   `OpenAI Codex stream was interrupted before completion.`) stand in for the
   Node error text Pi matches. The no-progress / no-delta / no-payload
   conditions are dropped (Pi has none); the `observed_delta` parameter goes
   away. Like Pi's `_isRetryableError`, a context-overflow error is vetoed
   first, even when its text contains retry-pattern digits (e.g. a token
   count `50000` matches `500`) or the structured flag is set: port the
   error-message case of Pi `ai/src/utils/overflow.ts isContextOverflow`
   (`OVERFLOW_PATTERNS` verbatim, `NON_OVERFLOW_PATTERNS` exclusion, and the
   Cerebras bodyless `400/413 (no body)` rule keyed on the provider name).
   Pi's silent-overflow cases need usage on a successful message and do not
   apply to failed attempts.
2. **Retry for every provider.** When a retry policy is present and the
   provider is not prepared (or declines preparation), the executor wraps it in
   a request-local handle whose `complete_attempt` calls `provider.complete`
   again with the same request, sinks, and cancel token (Pi reruns the request
   from the same context). `loop_step` builds the policy for every provider.
   Compaction and branch-summary callers keep their current prepared-only
   policy (Pi's summarization retry lifecycle is a separate gap).
3. **Pi delay.** The turn policy built in `loop_step` uses jitter 0 so the
   announced delay is exactly `baseDelayMs * 2^(n-1)` (capped). pipy keeps its
   larger bounded server `retry-after` hint (deviation: Pi's agent retry
   ignores it) and its existing cap source (`retry.provider.maxRetryDelayMs`,
   default 60 s like Pi's `maxAgentDelayMs`).
4. **One end per sequence.** The executor publishes `RetryCompleted` only
   when the sequence ends: success, a non-retryable later failure, exhaustion,
   an exception, or cancellation. A retryable failure that is followed by
   another attempt still retires its RPC retry lease (abort-won is still
   honoured), but publishes nothing. The cancellation failure text becomes
   `Retry cancelled` (Pi `finalError`). The RPC lease span per attempt is
   unchanged.
5. **Codex error events.** `_response_error_event` produces
   `Codex error: <message || code || compact JSON>` (message/code from the
   event or nested `error`, sanitized), keeping the `api_error_code`
   metadata. A `response.failed` terminal raises with
   `<response.error.message || "Codex response failed">`, keeping
   `response_status: failed`. Retrying is then decided by the classifier; e.g.
   `server_is_overloaded`, `rate_limit_exceeded`, "... You can retry your
   request" retry, while `insufficient_quota` / unknown errors do not. The
   websocket connection-limit path is unchanged.
6. **TUI rendering.** `ui/state.py` maps `RetryScheduled` to a
   `ScheduleRetry(attempt, max_attempts, delay_ms, error_message)` decision
   and `RetryCompleted` to `FinishRetry(succeeded, attempt, final_error)`, and
   resets `assistant_streamed` so the retried attempt's text is rendered fresh.
   `RenderingAgentEventAdapter` forwards them to two new renderer verbs:
   - `TuiToolLoopRenderer.schedule_retry`: settles any partial streamed
     assistant/reasoning text into history, appends an error block
     `Error: <message>`, and switches the animated working row to
     `Retrying (a/m) in Ns... (<interrupt key> to cancel)` with a
     warning-coloured spinner. `N = ceil(remaining/1000)` counts down on the
     spinner thread; at 0 the row returns to the normal working message (the
     retried request starts at the same instant; pipy has no attempt-start
     event, so the countdown expiry stands in for Pi's `turn_start`).
   - `finish_retry`: clears the retry state; on failure appends the error block
     `Retry failed after <attempt> attempts: <final_error>`.
   - The legacy stream renderer (`tool_renderers._ToolLoopRenderer`) writes the
     same two lines once, without animation.
   - The interrupt key text comes from the live keybindings
     (`app.interrupt`, default `escape`).
   Escape during the backoff already interrupts the executor's wait; it now
   shows `Retry failed after n attempts: Retry cancelled`.
7. **JSON/RPC.** `AutomationAgentEventAdapter` resets its partial assistant
   text on `RetryScheduled` so the retried attempt's `message_update.partial`
   does not concatenate the failed attempt's text. Event shapes are unchanged;
   the per-sequence end comes from change 4.
8. **Footer.** `AgentUsageAccumulator.absorb` replaces `last_total_tokens`
   only when the sample's effective total is positive (Pi's
   `calculateContextTokens(usage) > 0`). A failed turn (pipy failed results
   never carry usage) keeps the last successful value.

## Deviations kept (documented)

- DF1-F4b slice 1 closes the original intermediate-attempt and backoff-abort
  deviations: every ordinary failed attempt is persisted and emits message/turn/
  agent completion; cancelling backoff retains the error without an extra aborted
  message/row. AgentRunSettled retains logical SDK/RPC ownership across attempts.
  See [the closeout contract](../../specs/2026-10-10-f4b-retry-closeout.md).
- DF1-F4b slice 2 closes trailing chars/4 estimation and active-branch stored
  anchor restoration. Compacted contexts keep a conservative full estimate while
  their suffix remains; Pi's post-compaction unknown-until-new-usage meter remains
  an intentional difference. Server retry hint kept.
- DF1-F4b slice 3 retries plain and prepared auxiliary providers with dedicated
  bounded status lifecycle, keeping private content and usage suppressed. This
  privacy projection omits Pi's raw failure text and adds bounded source/outcome.

## Tests (done-when)

- Classifier: Pi strings (overloaded, 529 + `overloaded_error` label,
  `rate limit`, `503`, `network error`, `socket hang up`, "you can retry your
  request") retry; `insufficient_quota`, `billing`, plain 400 do not;
  structured `retryable` still retries; overflow prose with retry digits
  (`Codex error: ... 50000 tokens exceeds the context window`) and Cerebras
  `400 status code (no body)` are never retried, while `rate limit ... too
  many tokens` still is (non-overflow exclusion).
- Executor: a plain (non-prepared) provider returning 529 `overloaded_error`
  twice then success -> three `complete` calls, events
  `[RetryScheduled(1), RetryScheduled(2), RetryCompleted(2, True)]`;
  exhaustion -> `[RS1, RS2, RS3, RC(3, False, final error)]`; a failure after an
  observed delta is retried; cancellation during backoff ->
  `RC(False, "Retry cancelled")`; zero-jitter delays 2000/4000 ms.
- Codex: stream `error` event text and retry through the prepared path;
  `response.failed` message.
- Reducer/renderer: `ScheduleRetry`/`FinishRetry` decisions; TUI renderer
  writes `Error: ...`, the retry row text with countdown, settles partial text,
  and the final failure line; legacy renderer lines.
- JSON projection: single `auto_retry_end`; partial reset.
- Footer: a failed result after a successful one keeps `last_total_tokens`.
- Existing retry tests updated where they pinned per-attempt ends, the
  no-progress rule, or the prepared-only rule (deliberate Pi alignment).
- PTY (tmux, isolated env, local completions stub returning 529
  `overloaded_error` then 200): retry row with countdown visible, then the
  answer; a second run where Escape during the backoff cancels the retry.
- `just check`, `scripts/parity_checks/*`, `scripts/parity_score.sh`.

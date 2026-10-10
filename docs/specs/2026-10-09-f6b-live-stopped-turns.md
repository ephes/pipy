# DF1-F6b: live stopped-turn presentation

## Scope and reproduction

This bounded slice gives stopped provider answers a live terminal marker after
their partial content. Answers without tool calls match resumed history.
Partial-call stops get the marker too; rendering the calls live remains deferred.

On clean main `e39414f0`, the new stopped-turn display regression had
8 failures and 1 pass: operator abort already drew its marker, other cancellation
reasons did not, and provider failures either lacked the TUI marker or retained
the generic diagnostic. The production coding-session cases include both
streaming and buffered failures. Baseline output is private local evidence at
`/tmp/pipy-f6b-baseline-regression.log`.

## Contract

Canonical `MessageCompleted` owns the stopped answer's terminal marker. The
pure UI reducer keeps early failure/cancellation decisions to stop working
chrome and settle streamed output. It still ignores synthetic unstopped
completions after those events, but renders canonical stopped completions,
including buffered content not already streamed. Its inactive guard ensures a
second completion does not render twice. A shared pure marker function supplies
both live and restored text: `Error: <message or Unknown error>`, or
`Operation aborted` (a stored nonstandard abort error takes precedence).

Both product renderers implement the stopped-marker verb. Plain TTY notices
retain the previous red error style, leading indent and blank-line spacing. Failure
and cancellation callbacks themselves settle output without committing a marker;
completion adds the marker after partial content. The coding status-effect owner
continues recording provider failure and refreshing usage, but no longer emits
the generic provider-failure diagnostic or its type/status suffix. The error type
and message remain available in failure state/results and canonical events;
the response-status suffix is no longer rendered. Preparation and
other diagnostic paths are unchanged.

Retries retain their existing attempt error/countdown and final retry notice.
Only the terminal stopped message gets the new marker; successful retries get
none. Backoff cancellation still shows the existing retry-failed notice and one
abort marker. This slice does not change retry policy or attempt persistence.

## Boundaries

No message, persistence, request admission, hook ordering, ownership/currentness,
provider replay or cancellation contracts change. Accepted stopped messages
remain append-only and reopen through the existing tree. The next provider
request continues excluding stopped messages. No raw content is added to public
workflow metadata, and auxiliary summary output remains private.

Rendering the partial tool calls themselves remains deferred, as do synthesizing
an empty
aborted assistant after a tool interruption, stopped text in auxiliary summaries,
and retry-attempt persistence. A stopped message with partial tool calls gets
the fallback terminal marker too, so removing the old generic/operator notices
cannot hide its stopped status. Its buffered partial body is displayed as an
answer, not an executable tool preamble. The partial call rows still appear only
on resume; this slice does not claim full live/resume parity for that case. The historical F6 plan describes its original baseline; this
note supersedes its live text-only rendering deviations.

## Verification and independent review

The new regression exercises all cancellation reasons using production loop
events and compares live TUI rows with reopened tree history. Coding-session
cases verify one marker, continuation, skipped stopped content in the next
request, and durable reopen. Retry cases cover success, exhaustion and backoff
abort. Real PTY tests cover failure followed by a usable next prompt, Escape,
steering and local-command interruption. Tests isolate home/config/theme state.

Initial focused verification: 167 passed; expanded session/PTY contracts: 289
passed. Reducer regressions also pin fallback/custom markers and duplicate
completion suppression. The first full check found 3 failures (7957 passed, 2 expected
skips): an
unnecessary package export broke a pinned architecture surface, and two tests
still pinned the old diagnostic/early-abort presentation. The extra export was
removed, preserving the gate; tests now pin completion ownership and classified
failure state instead of the discarded diagnostic suffix.

The first valid supervised Claude Code Opus 5.5/high review returned 1 Warning
and 2 Suggestions. All were accepted: restore terminal indication for stopped
partial-call messages, close their buffered display with a marker, preserve
plain TTY error styling, and remove the unused abort-only transcript wrapper.
The reviewed repair baseline is `97d516df63e1da8eb713270b6badd8ebb0482819`.
Repair-focused verification passed 232 tests, including all three full-suite
failures and the new buffered/streamed partial-call cases. An intermediate
repaired full run stopped at mypy because a test imported a non-exported
`TextContent` alias; importing its owning module fixed that error. Ruff also caught a Python 3.11-incompatible f-string during style restoration;
its expression was split before final checks. A temporary repair fixture exposed the plain renderer's successful-tool-preamble
suppression; the stopped-body rendering flag now deliberately bypasses it.

Final verification, each process exited successfully:

- `just check`: 7970 passed, 2 expected skips; lint, format and mypy green.
- `just typecheck`: no issues in 656 source files.
- `uv run ruff check .`, `uv run ruff format --check .`, `git diff --check`: passed.
- `just docs-build`: passed, including the final documentation clarification.
- `just test-pty-smoke`: 8 passed.
- Required-runtime codemode integration: 49 passed.
- Expanded focused command below: 305 passed.
- After correcting the test import, `uv run pytest -q
  tests/test_native_stopped_turn_display.py tests/test_native_ui_state.py`:
  59 passed.
- No pre-commit configuration exists, so no prek hook run applies.

```bash
uv run pytest -q \
  tests/test_native_stopped_turn_display.py \
  tests/test_native_aborted_turn.py \
  tests/test_native_ui_state.py \
  tests/test_native_agent_event_adapters.py \
  tests/test_native_session_history_render.py \
  tests/test_native_retry_visibility.py \
  tests/test_native_coding_session_retry.py \
  tests/test_native_ui_coding_session_renderer.py \
  tests/test_native_coding_status_effects.py \
  tests/test_coding_session_provider_failure.py \
  tests/test_native_coding_session_streaming_and_rendering.py \
  tests/test_native_coding_session_terminal_pty.py
PIPY_CODEMODE_REQUIRE_RUNTIME=1 uv run --extra codemode pytest -q \
  tests/test_native_codemode_composite.py tests/test_native_codemode_delivery.py
```

The fresh scoped repair review used the installed supervised Claude harness,
again `claude-opus-5-5` at high effort, with the first snapshot as its baseline.
It recorded snapshot `713aa8a9c7ca78aec0ae2fd39bc6a883fdb44dfe` and returned
no Critical or Warning, and one documentation Suggestion. That clarification
was accepted and fixed: the intro, usage and release notes now describe the
fallback marker for partial-call stops while retaining the deferred call rows.
Both reviews completed validly, with no skips, truncations or forbidden/denied
tool calls, and their private reviewer copies were removed. The repair bundle
redacted one unchanged secret-shaped synthetic fixture assertion in diff
context; new TTY assertions were visible, and the complete fixture was present
in the review copy. That omission does not affect the accepted repair evidence.

The gate closes as **advisory, not CLEAN**, with all accepted findings repaired
and no outstanding blocking findings. Another review of this straightforward
description correction would seek agreement without reducing a demonstrated
risk. All 20 affected source/test files match the repair-reviewed snapshot;
only documentation clarification and status/evidence records follow it.

Codex owns implementation (exact model identifier not exposed); no additional
implementation agents were used. Summary-safe implementation, verification,
review dispositions and the stopping rationale are recorded in the private
`df1-f6b-live-stopped-turns` workflow session. No global settings, theme or
credentials were changed. The implementation was prepared on main without a commit or push. The owner
authorized committing and pushing this reviewed slice on 2026-10-10.

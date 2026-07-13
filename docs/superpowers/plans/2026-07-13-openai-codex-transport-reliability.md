# OpenAI-Codex Transport Reliability Implementation Plan

Date: 2026-07-13

Design: `docs/superpowers/specs/2026-07-13-openai-codex-transport-reliability-design.md`

Research: `docs/superpowers/specs/2026-07-13-openai-codex-transport-reliability-research.md`

## Execution rules

Work directly on `main`; do not create a branch or push. Begin every slice with
a clean worktree and record the current HEAD. End every slice with focused green
tests, `git diff --check`, proportionate documentation, a read-only Pi review
using exactly `openai-codex/gpt-5.6-sol` at high thinking, and a local commit.

For every review prompt, list every touched/untracked file, verification result,
trade-off, and prior finding. Use tools `read,grep,find,ls`, no session/context
files, the exact `=== REVIEW COMPLETE ===` sentinel, and at most three rounds.
Fix all Critical/Warning findings and fix or disposition Suggestions before
commit. Fail closed if the required Pi model/auth is unavailable. Record the
same-family reviewer limitation and summary-safe outcome in the active workflow
record.

The steps below are deliberately ordered so each commit is independently green
and reviewable. `just check` is unconditional before every review/commit and is
rerun after every review fix. Do not collapse the slices into one diff.

## Slice 1 — Timeout policy and transport-error normalization — COMPLETE (`683c2de`)

### Intent

Remove the provider-local 60-second behavior, make Pi-shaped timeout settings
effective, and guarantee that the historical streaming timeout becomes a
sanitized failed provider result while cancellation remains distinct. Do not
add automatic retries or WebSocket runtime behavior in this slice.

### Files

- `src/pipy_harness/native/settings.py`
- `src/pipy_harness/native/_provider_helpers.py`
- `src/pipy_harness/native/openai_codex_provider.py`
- `src/pipy_harness/cli.py`
- `tests/test_native_settings.py`
- `tests/test_native_openai_codex_provider.py`
- `tests/test_native_provider_cancellation.py`
- `tests/test_tool_loop_provider_failure.py`
- `tests/test_tool_loop_end_to_end_openai_codex.py`
- directly affected timeout/provider docs

### Steps

1. Add `DEFAULT_HTTP_IDLE_TIMEOUT_MS = 300_000` and
   `DEFAULT_WEBSOCKET_CONNECT_TIMEOUT_MS = 15_000` in the settings/provider
   ownership location selected by the design. Change
   `get_http_idle_timeout_ms()` to return the effective 300,000 ms default.
2. Add strict non-negative integer parsing for
   `retry.provider.timeoutMs` and `websocketConnectTimeoutMs`. Provide helpers
   that resolve provider timeout precedence and convert milliseconds to
   `float | None` seconds (`0` to `None`). Do not pass zero to `urllib`.
3. Extend settings reporting so the active 300,000 ms default and effective
   provider timeout are visible. Add tests for unset, positive, zero, invalid,
   and provider-override cases.
4. Thread the resolved timeout and existing transport value through
   `_provider_factory_for()` / `_native_provider_for_selection()` into
   `OpenAICodexResponsesProvider`. Transport remains SSE-only internally until
   Slice 3, but factory-wiring tests must prove the selected value reaches the
   provider rather than being merely reported.
5. Change SSE/client timeout annotations to `float | None`, including only the
   affected shared `open_url_cancellable()` boundary in
   `_provider_helpers.py`. Keep OAuth's bounded token-exchange timeout separate.
   Test `None` forwarding and cancellation during a disabled-timeout header
   wait.
6. Add narrow exception predicates/normalizers for the design's timeout,
   connection, selected-errno, DNS, premature-HTTP, and TLS cases. Preserve
   unexpected programming exceptions.
7. Refine the provider error hierarchy with
   `OpenAICodexStreamInterruptedError`. Normalize recognized header/open errors
   in `UrllibSseHTTPClient.post_sse()` and lazy read failures in `_events()`.
   Check the cancel token before normalization.
8. Add a provider-label helper accepting only the design's frozen known-code
   allowlist after its 1–64-character ASCII/shape check. Omit every unknown or
   invalid API code/type label. Test oversized, alphabetic prompt,
   URL/token/body-like, whitespace/control, and non-ASCII sentinels; recognized
   OS/library failures must never expose raw messages.
9. Convert the exact injected `TimeoutError("The read operation timed out")`
   into a stable failed `ProviderResult`. Assert no raw OS wording is required
   for classification, no assistant/tool state is appended, print-mode uses the
   stable provider-failure diagnostic, and a REPL/tool-loop fixture can run a
   later prompt.
10. Add cancellation-race tests proving timeout-looking close errors still raise
   `ProviderCancelledError` and are never labeled transport failures.
11. Update timeout/settings/provider docs touched by this behavior; leave retry
    and WebSocket shipping claims explicitly pending their later slices.

### Focused verification

```text
uv run pytest tests/test_native_settings.py -q
uv run pytest tests/test_native_openai_codex_provider.py -q
uv run pytest tests/test_native_provider_cancellation.py -q
uv run pytest tests/test_tool_loop_provider_failure.py tests/test_tool_loop_end_to_end_openai_codex.py -q
git diff --check
```

Run `just check` unconditionally before review and again after any review fix.
Run the mandatory Pi review/fix/re-review cycle, rerun `just check` after any
review fix, then commit:

```text
fix(provider): normalize Codex transport timeouts
```

## Slice 2 — Bounded progress-aware retries — COMPLETE (`31aad11`)

### Intent

Move retry ownership around the complete request plus stream attempt, honor
server delay within bounds, and retry only before any provider event. Preserve
exact-once sinks and tools. WebSocket remains unimplemented.

### Files

- `src/pipy_harness/native/retry.py`
- `src/pipy_harness/native/settings.py`
- `src/pipy_harness/native/openai_codex_provider.py`
- `tests/test_openai_codex_retry.py`
- `tests/test_native_openai_codex_provider.py`
- `tests/test_native_openai_codex_tool_calls.py`
- `tests/test_native_provider_streaming.py`
- `tests/test_native_provider_cancellation.py`
- directly affected retry/provider docs

### Steps

1. Add an attempt-local monotonic progress tracker and a transport-neutral event
   parsing entry point. Mark progress immediately before handling the first
   parsed event, including reasoning, metadata, errors, and tool deltas.
2. Keep the existing body-fixture SSE path working through the same parser.
   Require `completed`/`done` terminal success. Make missing terminal EOF a
   stream-interruption error and keep `incomplete` failed with no returned tool
   calls. Accept only `completed`, `failed`, `incomplete`, and `cancelled` as
   response-status labels; map everything else to fixed `unknown` metadata and
   never interpolate status into diagnostics. Add malicious-status SSE tests
   with alphabetic prompt, URL/token/body-like, control, oversized, and
   non-ASCII sentinels.
3. Extend `RetryPolicy`/`retry_with_backoff` only as needed for an injected
   exception decision and server-requested delay. Preserve existing HTTP-status
   behavior/tests for other providers and the deterministic sleep/jitter seams.
4. Add settings support for `retry.provider.maxRetries` precedence. With
   `retry.enabled=false`, create exactly one outer attempt and no backoff;
   call-local WS reconnect/fallback remains a Slice 3 concern. Keep all
   attempt/delay bounds from the design.
5. Parse `retry-after-ms` and `Retry-After` delta/date values at the HTTP error
   boundary using an injectable clock. Store only a capped numeric delay; never
   retain the raw header or body.
6. Add explicit OpenAI-Codex classification for transient status, terminal
   quota/auth/config, normalized transport, and missing-terminal errors.
   Cancellation is checked before classification and before every retry.
7. Replace the `_post()`-only wrapper with an attempt operation that acquires,
   validates, and fully consumes the stream. Retry only when classification is
   transient and progress is `none`.
8. Use cancellation-aware production sleep (`CancelToken.event.wait`) and
   injected fake sleep/jitter/clock in tests. Assert bounded exponential delay,
   server delay cap, attempt counts, and cancellation during backoff.
9. Inject timeout/reset/truncation/missing-terminal failures before the first
   event and prove success after retry. Inject the same failures after metadata,
   reasoning, text, partial/complete tool assembly, and prove one attempt only.
10. Add exact-once assertions: sinks contain no duplicate deltas; failed partial
    tool calls never surface; completed calls execute once; incomplete terminal
    calls never execute.
11. Update retry/provider/settings docs to describe request-plus-stream
    ownership and explicitly state there is no higher-level post-progress turn
    replay.

### Focused verification

```text
uv run pytest tests/test_openai_codex_retry.py -q
uv run pytest tests/test_native_openai_codex_provider.py tests/test_native_openai_codex_tool_calls.py -q
uv run pytest tests/test_native_provider_streaming.py tests/test_native_provider_cancellation.py -q
uv run pytest tests/test_tool_loop_provider_failure.py tests/test_tool_loop_end_to_end_openai_codex.py -q
git diff --check
```

Run `just check` unconditionally before review and again after any review fix.
Run the mandatory Pi review/fix/re-review cycle, rerun `just check` after any
review fix, then commit:

```text
fix(provider): retry safe Codex stream failures
```

## Slice 3 — Real WebSocket transport and Pi-shaped fallback — COMPLETE

### Intent

Make `transport: auto|sse|websocket` real for OpenAI-Codex, using a maintained
synchronous WebSocket client, shared event assembly, pre-event fallback, and no
post-event replay. This slice also removes `/settings`' fictional no-op status.

### Files

- `pyproject.toml`
- `uv.lock`
- `src/pipy_harness/native/settings.py`
- `src/pipy_harness/native/openai_codex_provider.py`
- `src/pipy_harness/cli.py`
- `tests/test_native_settings.py`
- `tests/test_native_openai_codex_provider.py`
- `tests/test_native_openai_codex_tool_calls.py`
- `tests/test_native_provider_streaming.py`
- `tests/test_native_provider_cancellation.py`
- directly affected transport/provider docs

### Steps

1. Add and lock `websockets>=16.0,<17`. Verify the resolved version supports
   Python 3.11 and the asyncio APIs used. Do not add another transport
   dependency.
2. Introduce a minimal synchronous `WebSocketClient` protocol and production
   `AsyncWebSocketClient` queue adapter over the dependency's asyncio API.
   Register an operation/task cancellation handle before connect begins, and
   configure URL conversion, the design's exact WS beta,
   auth/account/session/request attribution headers, 15-second/default
   open timeout, `None` disabled semantics, effective idle receive timeout,
   bounded message/queue limits, explicit keepalive posture, and reliable close.
3. Register the async operation with `CancelToken` before connect and cancel its
   task thread-safely on Escape/Ctrl-C; the task's async context owns and closes
   any live connection. Use a bounded non-blocking data queue plus separate
   completion/error state so a full queue cannot deadlock cancel/EOF.
   Unregister/join on all exits. Normalize only documented library transport
   exceptions, with token cancellation winning close races.
4. Send `{type: "response.create", ...body}` and yield decoded mapping events to
   the same parser/tool assembler as SSE. Reject non-text/invalid JSON and
   explicit API/protocol failures without transport fallback. Reuse the strict
   provider-label helper and test oversized, prompt/URL/token/body-like,
   control-text, and non-ASCII WebSocket error code/type values.
5. Implement transport selection exactly as designed:
   - `sse`: SSE only;
   - `websocket`: WS first, pre-event recognized transport failure falls back to
     SSE, post-event failure does not;
   - `auto`: WS first until provider-lifetime fallback memory is activated,
     then SSE directly on later calls.
6. Implement one fresh WS retry for pre-event
   `websocket_connection_limit_reached`, then normal fallback. Ensure API/auth
   errors other than that special code do not fall back. Define
   `retry.enabled=false` as one outer attempt: the one special reconnect and one
   fallback remain available without backoff. Add exact acquisition-count tests
   for ordinary failure and repeated connection-limit errors.
7. Preserve retry accounting: WS-to-SSE fallback is within one attempt; later
   pre-event SSE failure uses the bounded retry budget. Never fall back or retry
   after an event/sink/tool delta.
8. Add hermetic fake-client tests covering all three settings, success, connect
   timeout, missing dependency seam, special connection limit, pre-event close,
   post-event close, auto fallback memory, forced-mode documented fallback,
   disabled connect/idle timeouts, and cancellation during open/recv. The
   production adapter gets an injected hanging-connect test proving immediate
   cancellation with `websocketConnectTimeoutMs=0`, plus a close-race test that
   no request/event is published after cancellation and a queue-overflow test
   proving terminal signaling cannot deadlock. Add completion and error races
   with a nonempty queue, asserting the consumer drains every queued mapping in
   order before EOF/error. Repeat malicious response-status coverage through a
   fake WS stream.
9. Run existing SSE streaming/tool-call suites unchanged in behavior, proving
   one parser and exact-once tools. Test provider construction from real settings
   for each transport value and `websocketConnectTimeoutMs`.
10. Update provider/catalog/settings/architecture docs in the same slice. Remove
    accepted-but-inert/no-op claims for OpenAI-Codex transport while stating
    other providers may ignore transport. Document that `websocket` is WS-first
    with current-Pi pre-event SSE fallback, not an absolute no-fallback mode.

### Focused verification

```text
uv lock --check
uv run pytest tests/test_native_openai_codex_provider.py tests/test_native_openai_codex_tool_calls.py -q
uv run pytest tests/test_native_provider_streaming.py tests/test_native_provider_cancellation.py -q
uv run pytest tests/test_native_settings.py tests/test_openai_codex_retry.py -q
git diff --check
just check
```

Run the mandatory Pi review/fix/re-review cycle, rerun `just check` after any
review fix, then commit:

```text
feat(provider): add Codex WebSocket transport
```

## Slice 4 — Parity-runner and product failure handling — COMPLETE

### Intent

Keep normalized provider behavior primary while making parity-runner recognize
both the shipped diagnostic and the exact historical raw timeout as narrow
defense in depth. Prove runner retry cannot hide repository progress.

### Files

- `scripts/parity_runner.py`
- `tests/test_parity_runner.py`
- `tests/test_tool_loop_provider_failure.py`
- `tests/test_tool_loop_end_to_end_openai_codex.py`
- `docs/parity-loop/parity-runner.md`
- directly affected runner/provider docs

### Steps

1. Extend `child_block_reason()`'s bounded output-tail classifier for the exact
   stripped line `pipy: The read operation timed out`; keep the normalized
   `pipy: provider failure during turn:` path.
2. Add negative tests for arbitrary timeout/OSError text, earlier-than-tail
   text, payload-like text, and near matches so classification cannot become a
   broad exception swallow.
3. Replace the runner/hook `(exit_code, stdout)` child result with
   `ChildRunResult(exit_code, stdout, timed_out)`. Set `timed_out=True` only in
   `_spawn_capture()`'s `TimeoutExpired` path; never infer it from `-1`. Update
   fake hooks and test timeout separately from a non-timeout signal return using
   the same negative code.
4. Add `gap.attempt_started` immediately before every child invocation and
   `gap.attempt_finished` for every result, carrying only attempt number,
   bounded outcome/reason, exit-code presence, and timeout classification.
   Exercise the real runner retry loop with normalized and legacy classifications.
   Assert `gap.attempt_started`, `gap.attempt_finished`, `gap.retrying`, and
   `gap.retry_skipped` attempt numbers/reasons in `run.jsonl` without child body
   duplication. Cover first-attempt success, retry then success, exhausted
   failure, child timeout, and retry refusal after progress.
5. Add or retain separate refusal tests for unexpected branch, HEAD, any ref,
   dirty tracked file, untracked file, and baseline-dirty worktree. A single
   changed invariant must prevent retry.
6. Add product-boundary regression coverage: exhausted normalized failure does
   not escape as raw `OSError`, REPL state accepts a second prompt, print mode
   has stable nonzero diagnostic, and tool/session state has no failed-attempt
   assistant or tool effect.
7. Update the runner guide with provider-primary/runner-defense ownership and
   exact no-progress conditions. Do not change the 7,200-second child timeout.

### Focused verification

```text
uv run pytest tests/test_parity_runner.py -q
uv run pytest tests/test_tool_loop_provider_failure.py tests/test_tool_loop_end_to_end_openai_codex.py -q
uv run pytest tests/test_native_openai_codex_provider.py tests/test_openai_codex_retry.py -q
git diff --check
```

Run `just check` unconditionally before review and again after any review fix.
Run the mandatory Pi review/fix/re-review cycle, then commit:

```text
fix(parity): retry stalled provider attempts safely
```

## Slice 5 — Documentation, release notes, and integration closure

### Intent

Reconcile all planning/user-facing claims, append release notes in repository
order, run the full required validation, and close only if every acceptance
criterion is evidenced.

### Files to inspect and update

- `docs/harness-spec.md`
- `docs/provider-catalog.md`
- `docs/providers.md`
- `docs/settings-config.md`
- `docs/settings.md`
- `docs/backlog.md`
- `docs/pi-mono-gap-audit.md`
- `docs/pi-parity.md`
- `docs/parity-loop/parity-runner.md`
- `CHANGELOG.md`
- any focused tests/docs found stale by integration verification

### Steps

1. Search the repository for stale `60`, timeout, transport no-op/inert,
   WebSocket deferred, status-only retry, and parity-runner failure claims.
   Reconcile only claims affected by this work; do not close unrelated parity
   gaps.
2. Document exact effective settings, units, validation, zero/disabled mapping,
   precedence, transport semantics, fallback boundary, retry taxonomy,
   cancellation, incomplete/missing terminal behavior, and privacy-safe
   diagnostics.
3. Update backlog/audit/parity status with the delivered scope and explicit
   residual non-goals (no WS reuse/continuation cache and no post-event replay).
4. Append `[Unreleased]` changelog entries after older entries in each relevant
   subsection. Mention the 300-second default, normalized/retried transient
   failures, real transport selection/fallback, and runner hardening without
   exposing incident/session paths.
5. Run the combined required focused suite:

```text
uv run pytest tests/test_native_openai_codex_provider.py tests/test_native_openai_codex_tool_calls.py tests/test_openai_codex_retry.py tests/test_native_provider_cancellation.py tests/test_native_provider_streaming.py tests/test_native_settings.py tests/test_tool_loop_provider_failure.py tests/test_tool_loop_end_to_end_openai_codex.py tests/test_parity_runner.py -q
```

6. Run unconditional final gates:

```text
just check
git diff --check
```

If `.pre-commit-config.yaml` exists at this point, also run:

```text
prek run --all-files
```

7. Inspect the resulting diff for generated artifacts, credentials, raw payloads,
   theme/settings mutation, or unrelated work. Confirm dependency lock and docs
   build are included in `just check` or run their focused commands explicitly.
8. Run the mandatory Pi review/fix/re-review cycle over the complete final slice
   and integration evidence. Re-run the focused suite and `just check` after
   review fixes.
9. Record summary-safe final verification/review events and commit:

```text
docs(provider): document Codex transport reliability
```

## Final repository audit

After Slice 5 commit, do not modify files. Verify:

```text
git status --short --branch
git rev-list --left-right --count origin/main...main
git log --oneline 823ed1189a956eee33fb9ed9468f53cb73d433b1..HEAD
```

The branch must be `main`, the worktree clean, the requested commits local and
ahead of origin, and nothing pushed. Cross-check every acceptance criterion
against a test, documentation statement, review result, or repository-state
command before writing the final report.

## Acceptance-to-slice traceability

| Acceptance area | Primary slice | Evidence |
| --- | --- | --- |
| exact 60-second historical timeout; 300-second configurable/disabled policy | 1 | settings/factory/urllib regression tests |
| sanitized failure and usable REPL/print mode | 1 and 4 | provider + tool-loop/CLI tests |
| bounded pre-progress retry, Retry-After, cancellation-aware sleep | 2 | deterministic retry/failure-injection tests |
| no duplicate text/reasoning/tools/effects; incomplete-call protection | 2 | sink/tool exact-once tests |
| `sse`, `websocket`, `auto`, pre-event fallback, settings wiring | 3 | fake transport selection/fallback tests |
| cancellation during HTTP/WS/backoff | 1–3 | cancellation suite |
| parity classification and no-progress retry/refusal | 4 | temporary-repository runner tests |
| docs/backlog/parity/release notes | 5 | repository search + docs build/review |
| full regression suite and clean main | 5/final audit | required focused suite, `just check`, git state |

## Explicitly out of scope

- full provider-layer retry consolidation;
- wrapping Pi/Codex as the runtime;
- post-event or post-tool automatic turn replay;
- long-lived WebSocket connection reuse or response continuation caching;
- zstd request compression and transport performance telemetry;
- live-provider/network-required tests;
- parity-runner timeout increases; and
- pushing commits.

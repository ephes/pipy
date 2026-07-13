# OpenAI-Codex Transport Reliability Design

Date: 2026-07-13

Status: proposed for implementation
Research: `docs/superpowers/specs/2026-07-13-openai-codex-transport-reliability-research.md`

## Goal

Make Pipy's native `openai-codex` provider tolerate the idle periods and
recoverable transport interruptions handled by current Pi, without replaying
observable assistant output or tool effects. The product runtime remains
`pipy-native`; Pi is a behavioral reference and reviewer, not an execution
dependency.

This design covers the complete provider-to-runner reliability chain: timeout
settings, error normalization, retry ownership, WebSocket selection and
fallback, cancellation, tool replay safety, CLI behavior, runner defense in
depth, diagnostics, and hermetic verification.

## Resolved settings contract

### Idle timeout

`httpIdleTimeoutMs` is the product-wide setting and is measured in integer
milliseconds.

- Unset resolves to the named constant `DEFAULT_HTTP_IDLE_TIMEOUT_MS = 300_000`.
- A positive value is the effective header-wait and between-read/event idle
  deadline.
- `0` disables those deadlines. At Python socket/library boundaries it is
  converted to `None`; zero must never be passed to `urllib` or `recv()` as an
  immediate/non-blocking timeout.
- Booleans, non-integers, and negative integers raise the existing clear
  settings `ValueError` before provider construction.

`retry.provider.timeoutMs`, when explicitly present, is a provider-call
override with the same units, validation, and `0` semantics. Its precedence is:

1. explicit `retry.provider.timeoutMs`;
2. resolved `httpIdleTimeoutMs`;
3. the 300,000 ms default embodied by the resolver.

This matches Pi's effective provider timeout precedence without reproducing
its max-int SDK workaround. `/settings` reports both the resolved global idle
timeout and the effective provider override/inheritance, so it never displays
`(unset)` for a default that is active.

### Header versus stream behavior

The same effective idle duration configures two separately diagnosed phases:

- **header wait**: connect/TLS/request and response-header acquisition through
  `urllib`; and
- **stream idle**: a blocking SSE socket read or WebSocket `recv()` with no new
  bytes/message.

This is not a total-turn deadline. Each successful socket read or WebSocket
message starts a new idle interval naturally. SSE comment/keepalive bytes count
as network activity even when they don't become provider events.

### WebSocket connect timeout

Add Pi's top-level `websocketConnectTimeoutMs` setting:

- unset uses `DEFAULT_WEBSOCKET_CONNECT_TIMEOUT_MS = 15_000`;
- positive integer values are milliseconds;
- `0` disables only the WebSocket open-handshake deadline and maps to `None`;
- booleans, non-integers, and negative values raise before construction.

It is distinct from `httpIdleTimeoutMs`; after open, WebSocket reads use the
effective provider idle timeout.

### Retry settings

`retry.enabled=false` always means one outer provider retry-loop attempt and no
backoff. Pi-shaped call-local WebSocket recovery still applies inside that one
attempt: at most one connection-limit reconnect and at most one pre-event
WS-to-SSE fallback. Those are transport selection within one provider attempt,
not outer request/stream replay. Otherwise:

- explicit `retry.provider.maxRetries` wins;
- absent provider max retries inherits `retry.maxRetries` (default 3);
- retries are clamped to the existing safe maximum of nine, yielding at most
  ten attempts;
- `retry.baseDelayMs` supplies exponential delay (default 2,000 ms);
- `retry.provider.maxRetryDelayMs` caps exponential, jitter, and every parsed
  server-requested delay (default 60,000 ms).

Invalid retry integers continue to use the existing bounded settings-to-policy
conversion rather than turning a startup typo into unbounded retry. The new
provider-specific integer accessor ignores boolean/non-integer values and
inherits the global value, matching the current retry getter posture.

## Provider-domain failure model

`ProviderCancelledError` remains outside the OpenAI-Codex error hierarchy and
has precedence everywhere.

The existing provider hierarchy is refined as follows:

- `OpenAICodexProviderError`: sanitized base with safe metadata only.
- `OpenAICodexAuthError` / `OpenAICodexOAuthError`: credential and token-flow
  failures; never retried by the transport loop.
- `OpenAICodexHTTPStatusError`: non-success HTTP response. Retryability is
  derived from status and safe API error code/type, not arbitrary message text.
- `OpenAICodexTransportError`: connect, DNS, TLS, header, or WebSocket-open
  transport failure. Metadata includes a bounded phase and transport label.
- `OpenAICodexStreamInterruptedError(OpenAICodexTransportError)`: timeout,
  reset, truncated body, abnormal WebSocket close, or missing terminal event
  while consuming a stream.
- `OpenAICodexResponseParseError`: well-formed transport containing malformed
  JSON, explicit API/protocol failure, invalid terminal status, or unsupported
  response shape; non-retryable unless converted specifically to the stream-
  interruption class for missing terminal EOF.

Error strings are stable product diagnostics and never interpolate an OS error,
URL, header, response body, prompt, delta, or tool payload. Safe metadata is
limited to:

- `transport`: `sse` or `websocket`;
- `phase`: `connect`, `headers`, `stream`, or `protocol`;
- `retryable`, `exhausted`, `attempt`, and `max_attempts`;
- `progress`: `none` or `event`;
- numeric `http_status` and bounded `retry_after_seconds` when applicable;
- strictly validated API error type/code labels already used by the provider;
- configured/fallback transport labels.

Server-controlled API type/code values pass through a provider-label helper
before classification or metadata. A candidate must be 1–64 ASCII characters,
match `[A-Za-z0-9][A-Za-z0-9._:-]{0,63}`, **and** belong to a frozen provider
allowlist. The initial allowlist is `invalid_request_error`,
`authentication_error`, `permission_error`, `rate_limit_error`, `server_error`,
`insufficient_quota`, `billing_hard_limit_reached`, `usage_limit_reached`,
`quota_exceeded`, and `websocket_connection_limit_reached`. Every unknown,
invalid, oversized, redacted, or control-containing value is omitted (or
replaced by fixed `unknown` only where a result schema requires a label).
Neither diagnostics nor retry classification uses arbitrary server message
text, even if that text happens to look label-like.

Response `status` has a separate frozen allowlist: `completed`, `failed`,
`incomplete`, and `cancelled`. Every other value becomes fixed `unknown` in
metadata. Failure messages use fixed wording such as “response did not complete
successfully” and never interpolate even an allowlisted server value. Successful
results can report only the locally validated `completed` status.

### Narrow exception normalization

Cancellation is checked before normalization. A helper recognizes only:

- `TimeoutError` / `socket.timeout`;
- `ConnectionError` and selected `OSError.errno` values for refused, reset,
  aborted, unreachable, timed-out, and broken-pipe connections;
- DNS `socket.gaierror`;
- `http.client.IncompleteRead`, `RemoteDisconnected`, and equivalent premature
  HTTP EOF/disconnect failures;
- `urllib.error.URLError` only when its nested reason is one of the recognized
  transport failures;
- TLS transport failures, with certificate verification/configuration failures
  normalized but marked non-retryable; and
- the WebSocket dependency's documented timeout, connection, and close
  exceptions at the adapter boundary.

Unrelated `OSError`, `ValueError`, `AttributeError`, and programming defects are
not swallowed. This is intentionally narrower than the cancellation helper's
broad close-race catch tuple.

## Retry ownership and taxonomy

The OpenAI-Codex provider owns one attempt loop encompassing transport acquire,
status validation, and complete event-stream consumption. The existing shared
`RetryPolicy` remains the settings carrier and delay calculator; small generic
hooks may be added to `native/retry.py`, but transport classification and
progress decisions remain provider-owned.

### Retryable before progress

- HTTP 408, 425, 429, 500, 502, 503, and 504;
- connection refusal/reset/abort, transient DNS/network unreachable, timeout,
  premature EOF, and transient TLS/WS transport failure;
- response-header timeout;
- SSE or WS idle timeout before an accepted event;
- truncated stream or missing terminal response with zero accepted events; and
- the specific `websocket_connection_limit_reached` case, which receives one
  fresh WebSocket attempt before SSE fallback.

### Never retry

- user cancellation;
- HTTP 400, 401, 403, 404, and other non-transient statuses;
- terminal quota/billing API codes even when carried by HTTP 429:
  `insufficient_quota`, `billing_hard_limit_reached`, `usage_limit_reached`,
  and `quota_exceeded` (matched as bounded sanitized code/type labels);
- credential/configuration/certificate-verification failures;
- malformed event JSON, explicit API error, or failed/incomplete terminal
  response;
- any failure after the first accepted provider event; and
- anything after tool execution begins.

The intentionally narrow status taxonomy diverges from current Pi's broad
request-loop catch edge, which can retry some 400/401/403 responses.

### Backoff and `Retry-After`

Delay before retry `n` is the existing bounded exponential delay plus injected
jitter. A successfully parsed `retry-after-ms` or `Retry-After` delta/date value
may raise the delay, but the final wait is always capped by
`maxRetryDelayMs`. Parsing produces only a numeric duration; the raw header is
discarded. Malformed or past dates fall back to exponential delay.

The provider accepts injected jitter, wall clock, and a retry sleeper. The
production sleeper uses `CancelToken.event.wait(delay)`, followed by
`raise_if_cancelled()`, so cancellation interrupts backoff immediately. Tests
use deterministic clock/jitter/sleep fakes.

## Observable-progress state machine

An attempt-local `StreamProgress` object starts at `none` and changes
monotonically to `event` immediately before the first parsed provider event is
handled. It never resets within an attempt.

| Phase | Locally observable progress | Recovery allowed |
| --- | --- | --- |
| request not sent / waiting for headers or WS open | none | bounded retry; WS-to-SSE fallback |
| transport open, no parsed provider event | none | bounded retry; WS-to-SSE fallback |
| partial SSE bytes but no complete JSON event | none | bounded retry because no sink/tool state was exposed |
| first provider event, including metadata or tool delta | event | no request replay and no transport fallback |
| reasoning/text sink called | event | no replay; return sanitized failed result on later failure |
| function call partially or fully assembled | event | no replay; discard calls unless terminal completed |
| terminal `completed`/`done` validated | event | return result; tool loop may execute calls once |
| terminal `incomplete` | event | preserve only policy-approved text or fail; return no tool calls |
| tool loop execution begins | outside provider | transport retry has no ownership |
| cancellation at any phase | cancellation | close transport and abort; never normalize/retry |

Pipy will keep its current conservative behavior for `response.incomplete`: the
turn fails with a sanitized response error and returns no text or tool calls.
This is stricter than Pi's retained length-limited assistant text and guarantees
that apparently complete tool arguments in an incomplete response never run.

No higher-level automatic turn replay is added. The tool loop already returns a
failed result to an internally usable REPL. Restricting replay to zero provider
events prevents duplicate text, reasoning, tool assembly, tool execution, and
filesystem effects.

## Transport architecture

### Interfaces

SSE remains owned by `UrllibSseHTTPClient`. Its timeout parameter becomes
`float | None`, and its lazy iterator normalizes recognized read failures.

Add a narrow synchronous `WebSocketClient` protocol and a production
`AsyncWebSocketClient` adapter. The adapter runs the `websockets` asyncio client
on one owned thread/event loop and exposes a synchronous queue-backed event
iterator to the provider. Before starting `connect()`, it registers a closeable
operation with `CancelToken`; `close()` atomically marks cancellation and uses
`loop.call_soon_threadsafe(task.cancel)` as soon as the loop/task exists. This
interrupts DNS/proxy/TCP/TLS/HTTP-handshake awaits even when the configured open
timeout is `None`. A cancel racing loop setup is observed before the task enters
connect. The async task owns URL conversion, proxy/TLS handshake, connect
timeout, send/receive/close, message-size limits, and translation of library
exceptions. It publishes mappings with non-blocking writes to a bounded data
queue; completion and the single terminal exception use separate locked state
plus a `threading.Event`, so cancellation/EOF cannot deadlock behind a full
queue. The producer sets non-cancellation terminal state only after its last
successful queue write and never writes afterward. When the consumer observes
terminal state, it drains the queue to empty before applying the error or EOF;
this preserves all event order, including the terminal response mapping and any
events preceding a transport error. Cancellation alone has precedence and may
discard queued data. Queue overflow records a protocol/resource error after all
previously queued mappings, which are drained before that error is raised. The
synchronous iterator checks cancellation while waiting and joins the operation
on exit. No abandoned connector may send a request after cancellation.

The provider builds credentials, headers, and request body once per call. SSE
adds its existing `responses=experimental` beta and Accept/content-type headers.
WebSocket sends bearer auth, account ID, `originator: pipy`, user agent,
`session-id`, `x-client-request-id`, and
`OpenAI-Beta: responses_websockets=2026-02-06`; it omits HTTP Accept and
content-type. Because `ProviderRequest` has no provider-visible session ID and
this design does not cache WS connections, both correlation headers use one
fresh opaque ID from an injectable request-ID factory for each provider call.
This matches Pi's no-session fallback. Tests assert those header names and
request correlation while replacing auth values with sentinels; payload and
auth values never reach diagnostics.

The response assembler becomes transport-neutral. Existing SSE body fixtures
continue through an SSE event iterator; WebSocket fixtures yield decoded event
mappings. There is one tool-call assembly path.

### Dependency decision

Add `websockets>=16.0,<17` as an explicit runtime dependency and lock it in
`uv.lock`. Its asyncio client provides maintained RFC 6455 framing,
fragmentation/control handling, cancellable DNS/connect/TLS/proxy awaits, open
timeout, bounded messages/queues, and timed receive. Implementing those
security-sensitive features directly on stdlib sockets is out of scope and less
reliable.

The adapter sets finite message/queue limits, uses `ping_interval=None`, and
relies on provider idle semantics rather than hidden liveness traffic. Default
tests inject a fake client/connect coroutine and make no external connection.
An adapter-level test uses an injected hanging connect awaitable to prove
cancellation stops open immediately with `websocketConnectTimeoutMs=0`; a
separate close-race test proves the task cannot publish or send after
cancellation.

### `transport` semantics

- `sse`: use SSE only; never construct or call the WebSocket client.
- `websocket`: select WebSocket first. Matching current Pi, this is a transport
  preference, not absolute failure mode: a recognized pre-event WS transport
  failure falls back once to SSE. Protocol/auth failures and all post-event
  failures do not fall back.
- `auto`: select WebSocket first unless fallback memory is active. On a
  recognized pre-event WS transport failure, activate provider-lifetime SSE
  fallback memory and use SSE. Later calls on that provider instance start with
  SSE. Protocol/auth failures and all post-event failures do not activate
  fallback.

Both WS-first modes allow one special fresh-connection retry for
`websocket_connection_limit_reached` before the normal fallback decision.
The WS adapter recognizes that exact error code before yielding it to the
general event assembler, so it remains pre-progress; all other explicit API
error events are non-transport protocol failures and do not fall back. Fallback
does not consume a provider retry count because it is another transport for the
same attempt. Once a call falls back, all remaining retries for that call stay
on SSE rather than probing WS repeatedly. Subsequent pre-event SSE failures do
consume the bounded attempt budget.

These two call-local recovery actions remain enabled when
`retry.enabled=false`: the maximum acquisition sequence is first WS, one fresh
WS for the exact connection-limit code, then SSE fallback. There is still one
outer attempt and no backoff/replay. Ordinary pre-event WS transport failure
skips the special reconnect and falls back directly to SSE.

The provider is normally scoped to one native REPL/session. A small mutable,
lock-protected `CodexTransportState` field stores only the `auto` fallback flag;
it contains no credentials, payload, response identifier, or transcript. This
slice deliberately does not implement Pi's long-lived WS connection reuse or
connection-scoped request-continuation cache. That is a performance divergence,
not a selection/fallback divergence, and the client protocol leaves room for a
future cache.

### Terminal and close behavior

`response.completed` and `response.done` are successful terminal events.
`response.incomplete`, `response.failed`, explicit `error`, invalid JSON, and
protocol-invalid messages are terminal failures. Clean EOF/normal WebSocket
close without a successful terminal event becomes
`OpenAICodexStreamInterruptedError`.

That missing-terminal error is retryable only at progress `none`. Once any event
was accepted it returns a failed provider result without replay. Tool calls are
finalized only after completed/done status; incomplete and failed streams return
no calls.

## Cancellation and session consistency

Every attempt checks cancellation:

1. before credentials/request work;
2. before transport acquisition;
3. before and after each event read;
4. before classification/normalization;
5. before fallback or retry; and
6. during backoff.

SSE responses register their live connection with the existing `CancelToken`.
The WebSocket adapter registers its operation before connect and later owns the
live connection inside that cancellable task. Cancellation closes or task-
cancels the active object to unblock the worker, including during a disabled-
timeout handshake. If a close race raises a transport-looking exception, the
set token wins and `ProviderCancelledError` is re-raised.

An exhausted provider failure returns `failed_provider_result`. The tool loop
does not append an assistant message or execute calls for that attempt and
returns to the prompt. Print mode emits the existing stable
`pipy: provider failure during turn: <sanitized message>` form and exits
nonzero. No raw exception should reach the CLI-level `OSError` handler.

## Parity-runner defense in depth

Provider normalization is the primary fix. `child_block_reason()` additionally
recognizes, in its bounded tail only:

- the normalized `pipy: provider failure during turn:` line; and
- the exact legacy/raw `pipy: The read operation timed out` line (allowing only
  surrounding whitespace).

It does not classify arbitrary `TimeoutError`, `OSError`, or generic timeout
text. Existing retry eligibility remains mandatory and conjunctive: expected
branch, unchanged HEAD, unchanged full refs snapshot, and identical clean
worktree. Any unexpected progress records `gap.retry_skipped` and stops retry.

Add `gap.attempt_started` immediately before each child invocation and
`gap.attempt_finished` for every child result. Together with existing
`gap.retrying` and `gap.retry_skipped`, their payloads contain accurate attempt
numbers, bounded outcome/reason labels, and exit/timeout classification only.
They never contain stdout, stderr, child bodies, transcript content, or provider
payloads.

Replace the runner's ambiguous `(exit_code, stdout)` child result with an
internal `ChildRunResult(exit_code, stdout, timed_out)` value. `_spawn_capture()`
sets `timed_out=True` only in its `subprocess.TimeoutExpired` path. A natural
signal termination may share the same negative return code but retains
`timed_out=False`. Block classification and attempt events consume this explicit
provenance; they never infer timeout from `-1`.

## Test architecture

Hermetic seams are:

- fake SSE responses whose lazy iterators raise at controlled points;
- fake `WebSocketClient` calls yielding mappings or raising connect/recv/close
  failures;
- fake clock, jitter, and cancellation-aware sleep recorder;
- sink recorders that prove exact-once text/reasoning delivery;
- fake tool executor counters proving no call on incomplete/failed attempts;
- CLI/tool-loop fixtures issuing a second prompt after failure; and
- temporary git repositories for runner progress/ref safety.

Required injections include the exact `TimeoutError("The read operation timed
out")` before and after an event, connection/reset/truncated streams, missing
terminal events at zero/nonzero progress, Retry-After delta/date values, quota
429, cancellation at every boundary, incomplete responses containing complete-
looking and partial calls, transport-mode selection, WS pre/post-event failure,
auto fallback memory, and runner refusal after branch/HEAD/ref/tree changes.

No test uses live credentials, provider network access, global settings, theme
state, or raw transcript capture.

## Diagnostics and workflow capture

Provider diagnostics contain only the bounded metadata enumerated above.
Session workflow events record role/model, design decisions, review outcome,
verification, and commit completion in summary-safe prose. They never record
auth headers, tokens, URLs with secrets, request/response bodies, prompts,
deltas, tool arguments/results, raw reviewer prompts, or transcripts.

The Pi reviewer is `openai-codex/gpt-5.6-sol` as explicitly required. Because
the implementer is GPT-family, workflow reporting discloses that same-family
limitation rather than claiming independent-family coverage.

## Documentation and release-note contract

Behavioral slices update their directly affected docs. Final closeout reconciles
`docs/harness-spec.md`, `provider-catalog.md`, `providers.md`,
`settings-config.md`, `settings.md`, `backlog.md`, `pi-mono-gap-audit.md`,
`pi-parity.md`, `parity-loop/parity-runner.md`, and `CHANGELOG.md`.

The `[Unreleased]` changelog additions are appended after older entries in each
relevant subsection because this repository renders Unreleased oldest-first.
No documentation may continue calling OpenAI-Codex transport or timeout
settings inert after they ship.

## Non-goals and deliberate divergences

- No Pi/Codex CLI wrapper and no change from `pipy-native` ownership.
- No retry consolidation across all providers.
- No replay after any provider event and no automatic higher-level turn replay;
  this is stricter than Pi and protects exact-once output/effects.
- No successful partial result for `response.incomplete`; this is stricter than
  Pi and prevents incomplete tool execution.
- No long-lived WS connection reuse, response-id continuation cache, zstd
  request compression, or WebSocket performance telemetry in this work.
- No arbitrary `OSError` normalization.
- No parity child-timeout increase.
- No live-provider test requirement and no global theme/settings mutation.

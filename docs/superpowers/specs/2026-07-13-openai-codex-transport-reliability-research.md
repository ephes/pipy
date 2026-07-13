# OpenAI Codex transport reliability research

Status: research complete; implementation is intentionally out of scope for this
slice. Date: 2026-07-13.

## Question and evidence

Pipy's native `openai-codex` provider currently stops a healthy-looking stream
after roughly 60 seconds without bytes and lets the resulting raw
`TimeoutError` escape the provider boundary. This is recurring rather than a
single outage:

- summary-safe `pipy-session search "read operation timed out" --json` found
  four partial native-REPL records from 2026-05-26 with the same error;
- the 2026-07-13 parity run at
  `docs/parity-loop/runs/run-parity-20260713T141605Z/` ended with
  `pipy: The read operation timed out`, no commit, unchanged remote-tracking
  refs, and a clean repository; and
- summary-safe searches for `provider retry` and `openai-codex transport`
  returned no prior promoted session lesson. No raw transcript body was read.

The implementation baseline inspected was Pipy
`823ed1189a956eee33fb9ed9468f53cb73d433b1` on clean `main`. The Pi reference
was `/Users/jochen/src/pi-mono` at clean `main`
`b084d2fb395f0f1aa924cb07b14e5d0edab115e2` (also tracking `origin/main`).

## Pi sources and history inspected

Current files:

- `packages/ai/src/api/openai-codex-responses.ts`
- `packages/ai/src/utils/retry.ts`
- `packages/ai/src/utils/abort-signals.ts`
- `packages/ai/src/utils/event-stream.ts`
- `packages/ai/src/types.ts`
- `packages/coding-agent/src/core/http-dispatcher.ts`
- `packages/coding-agent/src/core/settings-manager.ts`
- `packages/coding-agent/src/core/sdk.ts`
- `packages/coding-agent/src/core/agent-session.ts`
- `packages/coding-agent/test/sdk-stream-options.test.ts`
- `packages/coding-agent/test/agent-session-retry.test.ts`
- `packages/ai/test/openai-codex-stream.test.ts`
- `packages/ai/test/openai-responses-terminal-event.test.ts`
- `packages/ai/test/retry.test.ts`

Relevant history was read, not inferred from filenames:

| Commit | Relevance |
| --- | --- |
| `a26a9cfa` | introduced configurable transport and Codex WebSocket session caching |
| `4745a958` | added cached Codex WebSocket transport |
| `370fdae6` | made a pre-event WebSocket transport failure fall back to SSE |
| `849f9d9c` | added the 300-second configurable Undici header/body idle timeout |
| `3e0d875c` | added WebSocket idle timeout and the no-fallback-after-first-event boundary |
| `493efd42` | separated the 15-second WebSocket connect timeout from stream idleness |
| `d0e0b84c` | added one reconnect for the pre-event WebSocket connection-limit error |
| `54113731` | made the configured HTTP timeout govern Codex SSE response headers |
| `cd95c274` | required a terminal Responses event instead of accepting EOF as success |
| `2117b61c` | prevented an Undici internal mid-stream error event from crashing Pi while preserving the body-stream rejection |
| `371adcf3`, `d53b5676`, `4285712b`, `57d96d72` | broadened higher-level retry classification for provider guidance, 524, socket drops, and resource exhaustion |
| `23d14626` | bounded cached WebSocket connection age |

The current Pi implementation is newer than several Pipy planning statements:
the old `packages/ai/src/providers/...` path has moved to `src/api`, WebSocket
transport is live, a five-minute idle timeout is the product default, missing
terminal events are errors, and network/stream failures participate in the
higher-level retry classifier.

## Pi-to-Pipy behavior matrix

| Concern | Current Pi at `b084d2fb` | Pipy at `823ed118` | Consequence / target direction |
| --- | --- | --- | --- |
| Header timeout | Global Undici `headersTimeout` defaults to 300,000 ms. Codex SSE also combines the user abort signal with `AbortSignal.timeout(timeoutMs)` until `fetch()` returns headers. | `urllib.request.urlopen(..., timeout=60.0)` uses the provider field for connect/header wait and later reads. | Use a named 300,000 ms policy from settings, not a provider-local 60-second literal. Keep the header phase identifiable in errors. |
| Body / stream idle timeout | Global Undici `bodyTimeout` defaults to 300,000 ms and is an inactivity timeout between body data. WebSocket `recv` logic independently waits at most `timeoutMs` for the next event. The Codex-local SSE abort signal is cleaned up after headers; body idleness is owned by Undici. | The same 60-second socket timeout remains on the `HTTPResponse` during line iteration, so a read with no bytes for that interval raises. | Preserve idle, not whole-request, semantics. Reset naturally after each successful read/event. |
| Default and disabled semantics | `DEFAULT_HTTP_IDLE_TIMEOUT_MS = 300_000`; settings choices include 30 s, 1 m, 2 m, 5 m, disabled. `0` configures Undici with disabled timeouts. SDKs that interpret zero as immediate instead receive max signed int32; provider-local Codex code treats `0` as no local timer. | `httpIdleTimeoutMs` is accepted/reported but unset returns `None` and is not wired. Provider still uses 60 seconds. The provider accepts a numeric zero, but if settings forwarded it unchanged, `urllib` would select non-blocking socket behavior rather than disable the deadline. | Effective default 300,000 ms; non-negative integer milliseconds; `0` means no header/body/WS idle deadline and must map to Python `None`. Pipy can implement a true disabled socket timeout rather than Pi's SDK max-int workaround. |
| HTTP-status retries | Codex has an explicit retry-status set of 429/500/502/503/504 and special-cases usage-limit errors, but the enclosing catch currently retries almost every parsed non-success error while budget remains—including many 400/401/403 responses. It uses exponential 1 s, 2 s, …, and reads `retry-after-ms` / `Retry-After`. Default provider `maxRetries` is zero unless configured. | Shared helper retries 408/425/429/500/502/503/504 using configured exponential backoff+jitter. It does not parse `Retry-After`, distinguish quota 429s, or see body-read failures. | Intentionally diverge from Pi's broad catch edge: retain bounded settings, retry only an explicit transient taxonomy, add terminal quota/auth/config classification, and honor bounded server delay. |
| Network / transport retries | The Codex request loop treats pre-response fetch failures as retryable up to provider `maxRetries`. The outer assistant retry classifier also recognizes network/fetch/socket/reset/timeout text. | `retry_with_backoff` retries only exceptions carrying a recognized `metadata.http_status`. `URLError` during open becomes `OpenAICodexTransportError` but isn't retried. A raw body-read `OSError` is neither normalized nor retried. | Normalize narrowly identified transport failures, then let the request boundary retry only while no event/output has escaped. |
| Stream interruption retries | Codex does not replay an SSE response inside its request loop after headers. WebSocket may fall back to SSE only before its first response event. Stream errors become assistant errors, and the separate agent-session classifier may restart the turn. | Stream consumption is outside `_post()` and outside the retry helper. Missing terminal event is a parse error. Raw timeout/reset exceptions escape. | Own stream consumption inside a progress-aware attempt. Replay only when no observable progress exists; otherwise fail the turn without transport fallback/replay. |
| Higher-level turn retries | `AgentSession` defaults to three retries with 2/4/8-second cancellation-aware backoff. It classifies sanitized assistant errors, removes the failed assistant from live agent state, retains it in session history, and calls `continue()`. Context overflow is separate. | Pipy's `retry.*` is used only to build the provider HTTP `RetryPolicy`; the tool loop emits a failure diagnostic and returns to the prompt. | Avoid adding a second blind turn replay. Provider pre-progress retry is sufficient for this slice; post-progress failure returns a stable failed result and a usable REPL. This is deliberately stricter than Pi to meet no-duplicate-output/effect requirements. |
| Backoff / `Retry-After` | Provider request retry starts at 1 second. Higher-level retry starts at configured 2 seconds. 429 server delays are capped by `maxRetryDelayMs` (60 seconds default); non-429 retry-after is currently not capped in the Codex local loop. Sleeps are abortable. | Provider retry defaults through settings to 2/4/8 seconds, adds jitter, caps at `provider.maxRetryDelayMs`, and uses blocking `time.sleep` without cancellation. | Use one bounded, injectable, cancellation-aware provider retry delay calculation. Cap all server-requested waits; do not reproduce Pi's uncapped non-429 edge. |
| Cancellation | User signal wins over the header timeout, closes WebSockets, cancels readers, yields `stopReason: aborted`, and never matches retryable error classification. Retry sleep has its own abort controller. | `CancelToken` shuts down the registered socket. Cancellation artifacts are converted only if the token is set. Cancellation is already distinct from provider failure, but retry sleep is not token-aware. | Check cancellation before normalization and before every retry/sleep. Preserve Escape/Ctrl-C behavior and `ProviderCancelledError`. |
| WebSocket selection | `sse` bypasses WS. Both public `auto` and `websocket` try WS; `auto` additionally uses cached continuation state. A session marked after WS failure stays on SSE. | `transport` is accepted, migrated, and reported but provider construction ignores it; only SSE exists. | Wire all three settings. Match Pi's current semantics: `websocket` means WS-first rather than absolute WS-only; document that a pre-event transport failure falls back to SSE. |
| SSE fallback | Any WS transport failure before the first response event falls back to SSE and marks the session fallback-active. One special connection-limit error gets one fresh WS connection first. API/protocol errors do not fall back, except the connection-limit special case. | None. | Fallback only before the first valid event and only for transport-class failures. Forced and auto modes share this Pi behavior; `sse` never probes WS. |
| Before / after first stream event | WS calls `onStart()` and emits assistant `start` on the first valid event. Before that point transport failure may replay via SSE. After it, no fallback. SSE emits `start` immediately after successful headers, but does not have a local stream retry. | Renderer starts before `complete`; parsing emits deltas directly. No explicit progress state. | Define progress independently of UI's generic start: a provider attempt is replayable until the first provider event is accepted or a sink is called. |
| Before / after visible text | Pi's WS boundary is conservative: even a metadata-only first event prevents fallback. Higher-level turn retry can still rerun an error assistant even if partial content exists, so UI duplication is possible by design. | Text sinks render immediately; a later raw exception kills the command. | Never replay after text or reasoning sink emission. Conservative first-event gating is simpler and at least as safe. |
| Before / after tool-call assembly / execution | Provider parsing assembles calls but agent-core executes them only after a successful terminal assistant message. A mid-stream provider error therefore cannot execute a partial call. Higher-level retry happens before any failed-attempt tool call executes. | `_parse_sse_response` assembles calls, and the tool loop executes only after a successful `ProviderResult`; partial calls on failure are discarded. | Preserve this boundary. No stream retry after any event; never retry a provider turn after tool execution begins. |
| Missing terminal event | Shared Responses processing treats EOF without `response.completed` as an error (`cd95c274`). Outer retry text recognizes several premature-stream messages. | `_parse_sse_response` already returns `OpenAICodexResponseParseError` for missing terminal response, but it is not classified for safe retry. | Treat as a retryable truncated stream only when progress is still zero; otherwise fail without replay. |
| Diagnostics and privacy | Assistant diagnostic metadata records failure kind, configured/fallback transports, phase, event-emitted boolean, and request byte count. It does not intentionally log auth or request/response bodies. | Failed results sanitize type/message and retain safe HTTP/status metadata. Raw CLI exception text currently exposes uncontrolled OS wording; session policy forbids payloads/secrets. | Emit stable provider-domain wording plus bounded labels: phase, transport, retryable/exhausted, attempt count, HTTP status. Never retain headers, credentials, bodies, prompts, deltas, tool payloads, or transcript content. |

## Exact Python `urllib` timeout semantics

The active interpreter is CPython 3.14.5. `socket.timeout is TimeoutError` is
true. `urllib.request.urlopen(request, timeout=x)` passes `x` through the
`HTTPConnection`; `HTTPConnection.connect()` calls `socket.create_connection`
with that timeout, leaving it on the socket. `HTTPResponse.__iter__` repeatedly
uses `HTTPResponse.readline()`, which delegates to the socket-backed buffered
file's `readline()`.

Therefore Pipy's `timeout_seconds=60.0` is neither a total request deadline nor
an SSE-aware timer. It is the maximum duration of an individual blocking socket
operation. Each successful underlying read makes later reads eligible for a
fresh wait; if no bytes arrive while a line read needs more data, the socket
raises `TimeoutError` after about 60 seconds. TCP fragmentation and buffering
mean the deadline is about byte availability for the pending buffered read, not
strictly about complete SSE event boundaries. `timeout=None` restores blocking
mode (disabled); `timeout=0` would make the socket non-blocking and is not the
desired disabled representation.

This is close enough to an idle timeout for ordinary SSE, but it cannot provide
cleanly separated connect/header and stream-event policies on one response.
Pipy should translate settings `0` to Python `None`, use the configured value
for the open/header socket, and keep the same value for body idleness unless a
future separate setting is introduced.

## Exact Pipy escape and retry paths

The historical failure follows this path:

1. `OpenAICodexResponsesProvider.complete()` calls `_post()` through
   `retry_with_backoff`.
2. `_post()` calls `UrllibSseHTTPClient.post_sse()`. `urlopen` returns after
   response headers, so `_post()` succeeds and the retry boundary ends.
3. `complete()` passes `response.event_stream` to `_parse_sse_response()`.
4. `_parse_sse_response()` iterates `_events()`.
5. `_events()` iterates `_iter_sse_stream()`, which iterates the
   `HTTPResponse`; its socket-backed `readline()` raises `TimeoutError`.
6. `_events()` catches `CANCELLED_READ_ERRORS`, which includes `OSError` and
   therefore `TimeoutError`, but re-raises unchanged because the cancel token
   is not cancelled.
7. `_parse_sse_response()` and `complete()` catch only
   `OpenAICodexProviderError`, so the raw exception leaves the provider.
8. The tool-loop worker stores and re-raises it on the main thread. The top-level
   CLI catches broad `OSError`, prints `pipy: The read operation timed out`, and
   exits 1. The normal failed-`ProviderResult` path that keeps the REPL alive is
   bypassed.

The settings construction path is separate:

1. global and project JSON are loaded by `SettingsManager`;
2. `retry_policy_from_settings()` maps `retry.enabled`, `maxRetries`,
   `baseDelayMs`, and `retry.provider.maxRetryDelayMs` to `RetryPolicy`;
3. CLI `_provider_factory_for()` binds that policy;
4. `_native_provider_for_selection()` supplies it only to the legacy
   `OpenAICodexResponsesProvider` constructor; and
5. the provider wraps only `_post()` with `retry_with_backoff`.

`httpIdleTimeoutMs`, `retry.provider.timeoutMs`,
`retry.provider.maxRetries`, and `transport` do not reach the provider. The
settings report therefore currently advertises an idle timeout and transport
choice that are inert. Even the wired policy recognizes only HTTP status
metadata, so it cannot protect header connection errors or body reads.

## How Pi avoids or recovers from the equivalent failure

Pi changes three layers rather than merely raising one timeout:

1. Its process-level Undici dispatcher owns a 300-second header/body idle
   policy, with an error listener that prevents a second internal EventEmitter
   error from crashing the process while the fetch body still rejects normally.
2. Codex normalizes every thrown value into an assistant error event. Request
   acquisition retries transient HTTP and network failures before body
   consumption. WS transport can fall back to SSE only before its first event.
3. `AgentSession` classifies the resulting assistant error and may rerun the
   turn with cancellation-aware exponential backoff.

Pipy should adopt layers 1 and 2 and the useful taxonomy from layer 3, but use a
stricter replay gate. Pipy streams synchronously into an externally visible
renderer and owns model-selected filesystem tools; replaying after progress
would risk duplicate text or effects. Safe pre-progress provider retry plus a
stable failed result gives resilience without inheriting Pi's possible
partial-output replay.

## WebSocket protocol and dependency implications

Codex WS uses the HTTPS Responses URL converted to `wss:` and an opening
handshake with bearer/account/session headers, then sends a JSON text message
shaped as `{type: "response.create", ...request}`. Pi's header builder replaces
the SSE beta value with `OpenAI-Beta: responses_websockets=2026-02-06`, but the
current `connectWebSocket()` removes that header immediately before invoking the
runtime WebSocket constructor. Pipy must test the actual required handshake
rather than copy only the builder's intermediate value. Incoming text messages
are the same Responses event objects consumed by the SSE assembler. A completed
result requires `response.completed` or `response.done`; `response.incomplete`
is terminal but not successful for tool execution. Close/error, invalid JSON,
`response.failed`, and explicit API `error` events must remain distinct.
Cancellation closes the socket. The initial WS connect/open timeout is separate
from message-idle timeout.

Python's standard library has HTTP, TLS, sockets, and base64 primitives but no
RFC 6455 client. A local implementation would have to own handshake validation,
client masking, fragmentation and reassembly, control frames, ping/pong, close
codes, size limits, TLS, proxies, cancellation races, and future protocol
maintenance. That is disproportionate and security-sensitive transport code.

The recommended dependency is the maintained `websockets` package's synchronous
client. Its documented public API supplies `additional_headers`, proxy support,
`open_timeout=None` disabled semantics, `recv(timeout=...)`, automatic control
frames/fragment reassembly, close diagnostics, and bounded message queues. The
design slice must pin a supported major range in `pyproject.toml`/`uv.lock`, set
explicit max message/queue limits, disable or consciously configure keepalive,
and wrap the library behind a tiny injectable `WebSocketClient` protocol so
tests never use the network. This is a justified exception to the current
zero-runtime-dependency posture; rolling an incomplete WebSocket stack is the
higher reliability risk.

References consulted for this dependency decision:

- <https://docs.python.org/3.14/library/urllib.request.html#urllib.request.urlopen>
- <https://websockets.readthedocs.io/en/stable/reference/sync/client.html>
- <https://websockets.readthedocs.io/en/stable/reference/features.html>
- <https://websockets.readthedocs.io/en/latest/topics/proxies.html>

## Retry-safety state machine

The replay decision belongs to one provider call and is monotonic:

| State | Entry | May retry same request / fall back? | Exit consequence |
| --- | --- | --- | --- |
| `PRE_REQUEST` | credentials and payload validated; nothing sent | yes for cancellation-aware delay; cancellation exits immediately | request or WS connect begins |
| `WAITING_HEADERS_OR_WS_OPEN` | transport acquisition in progress | yes for retryable transport/HTTP status; WS may fall back to SSE | no user-visible provider event exists |
| `OPEN_NO_EVENT` | HTTP headers or WS open succeeded, but no provider event accepted | yes for a retryable EOF/timeout/reset; WS may fall back to SSE | request may have reached server, but no local content/tool side effect exists; bounded replay is accepted |
| `EVENT_OBSERVED` | first valid SSE/WS event accepted, including metadata-only event | **no** replay/fallback | any later interruption becomes failed result |
| `OUTPUT_OBSERVED` | text or reasoning sink called | **no** replay | preserve already rendered output; fail without duplication |
| `TOOL_ASSEMBLED` | provider has enough data to form a tool call but no terminal success | **no** replay and no execution | discard partial attempt and fail |
| `TERMINAL_COMPLETED` | terminal completed/done event validated and final result built | no retry | tool loop may append assistant message and execute validated returned calls exactly once |
| `TERMINAL_INCOMPLETE` | terminal incomplete event accepted | no replay after any event; never execute tool calls | Pi preserves the assistant with `length` stop reason while agent-core suppresses its calls; Pipy may preserve safe text or fail the turn, but must not surface executable calls |
| `TOOL_EXECUTING_OR_DONE` | tool loop left provider boundary | never owned by transport retry | only a subsequent normal provider turn may occur |
| `CANCELLED` | cancel token set at any point | never retry; dominates normalization | close active transport, raise `ProviderCancelledError`, append no assistant/tool result |
| `FAILED` | non-retryable or exhausted failure | no | sanitized failed `ProviderResult`; REPL stays usable |

The conservative transition at the first valid event is stricter than "first
visible text" and deliberately matches Pi's WS fallback boundary. It makes
reasoning, partial function arguments, and future event kinds safe without
maintaining a fragile list of observable events.

## Failure-injection test matrix

All cases are hermetic through fake SSE/WS transports, fake sleep/jitter/clock,
and fake cancellation tokens.

| Injection | Phase/progress | Expected result |
| --- | --- | --- |
| `TimeoutError("The read operation timed out")` from SSE iterator | before first event | normalized transport failure; bounded retry; exhausted result is sanitized, no raw exception |
| same timeout | after metadata event | no retry; failed result; no duplicate sink call |
| same timeout | after text delta | no retry; one rendered delta only; failed result |
| connection refused / DNS `URLError` / reset during open | pre-response | retry while budget remains; cancellation checked first |
| reset or `IncompleteRead` during SSE | before event vs after event | retry only before event; otherwise fail |
| HTTP 429 with numeric/date `Retry-After` | pre-response | bounded server delay, injectable sleep; terminal quota 429 does not retry |
| HTTP 500/502/503/504 | pre-response | exponential bounded retry |
| HTTP 400/401/403 or invalid config/auth | any | no retry |
| EOF without terminal event | zero events vs any event | retry only at zero progress; otherwise truncated-stream failure |
| malformed JSON / explicit API error / `response.failed` | any | protocol/API failure, no transport fallback; retry only if taxonomy explicitly marks transient API status |
| `response.incomplete` with complete-looking or partial tool arguments | terminal event | preserve safe text or fail turn; never return or execute tool calls |
| cancel during headers, body read, WS open, WS recv, or backoff | any | immediate `ProviderCancelledError`; no retry/failure diagnostic |
| WS unavailable/connect timeout/reset | before first event, `auto` or `websocket` | SSE fallback once; same request context; transport metadata only |
| WS connection-limit API code | before first event | one fresh WS connect, then SSE fallback if still transport-retryable |
| WS timeout/close | after first event/text/tool delta | no SSE fallback or replay; failed result |
| `transport=sse` | all | WS fake never called |
| `transport=websocket` | success | WS called first; SSE unused |
| `transport=auto` after prior session fallback | next request | SSE directly for that provider/session lifetime |
| partial function-call arguments then interruption | progress | no retry; no surfaced/executed tool call |
| terminal tool call then tool execution | post-provider | transport layer cannot replay; exactly one tool execution |
| exhausted failure followed by another prompt | REPL | stable diagnostic; session remains internally consistent and accepts prompt two |
| print-mode exhausted failure | CLI | stable provider-failure diagnostic and nonzero exit, no raw OS text |
| parity child legacy raw timeout tail | runner | classified as provider failure; retry only if branch/HEAD/refs/tree unchanged |
| normalized provider failure with any git/ref/worktree change | runner | retry refused; structured `gap.retry_skipped` records unexpected progress |

## Recommended implementation boundaries

1. **Timeout and normalized failures.** Make `httpIdleTimeoutMs` effective with
   a 300,000 ms default and `0` disabled; distinguish header/body phases; add a
   sanitized transport error taxonomy; convert the historical timeout into a
   failed result; retain cancellation precedence.
2. **Progress-aware retries.** Extend or replace the status-only helper with an
   explicit retry classifier, `Retry-After`, cancellation-aware injected sleep,
   and a monotonic progress object. Move stream consumption inside the attempt
   boundary but reject replay once an event is observed.
3. **WebSocket transport.** Add the reviewed locked dependency and injectable
   sync client; share event assembly with SSE; implement `sse`, WS-first
   `websocket`, and stateful WS-first `auto`, including session-lifetime fallback
   memory, pre-event SSE fallback, and no post-event fallback. Treat connection
   reuse and request-continuation caching as separate optimizations rather than
   prerequisites for correct transport selection and fallback.
4. **Runner defense in depth.** Recognize normalized failures plus the narrow
   legacy raw timeout tail; retain exact branch/HEAD/full-ref/worktree snapshot
   checks; improve attempt/retry event assertions.
5. **Docs and closeout.** Update user/provider/settings/architecture/parity docs,
   backlog status, runner guide, gap audit, parity table, and oldest-first
   Unreleased release notes; run focused and full gates.

## Explicit non-goals

- No Pi/Codex CLI wrapper or change to `pipy-native` product ownership.
- No full provider-layer consolidation or retry rollout to every adapter.
- No retry after the first provider event, after visible reasoning/text, after
  tool assembly, or after any tool execution.
- No credential-store reuse, provider payload diagnostics, raw transcript
  inspection, or live-network requirement in tests.
- No long-lived WS connection reuse or request-continuation cache in the first
  transport slice. This deliberately omits Pi's connection/performance
  optimization while retaining the required selection semantics and
  session-lifetime SSE fallback memory; the design must state this divergence
  and keep a future cache compatible with the transport abstraction.
- No change to the parity runner's 7,200-second child timeout as a remedy.

## Documentation discrepancies to correct

- `docs/settings-config.md`, `docs/settings.md`, and `docs/pi-parity.md` say
  WebSocket transport is inert/no-op; current Pi and the required Pipy target
  make it live for `openai-codex`.
- Pipy reports `httpIdleTimeoutMs` but currently displays unset and ignores it;
  Pi resolves unset to 300,000 ms and applies it to headers/body.
- Pipy docs describe `retry.*` as provider HTTP retry policy without making
  clear that network and stream-read failures are outside the actual helper.
- `docs/pi-parity.md` calls transient HTTP retry implemented, but that statement
  is only accurate for recognized HTTP statuses and does not cover the recurring
  transport failure.
- `docs/harness-spec.md` describes Codex as one SSE request; this must become a
  transport-selected Responses stream while keeping the existing privacy and
  native-runtime constraints.
- `docs/provider-catalog.md` justifies the legacy Codex factory only by its
  settings-derived retry policy and OAuth/SSE shape; construction must now also
  carry timeout and transport settings, or be realigned without losing them.
- `docs/parity-loop/parity-runner.md` claims known provider-stream interruption
  classification, but `child_block_reason()` recognizes only the normalized
  `provider failure during turn` line and missed the historical raw timeout.

# Python SDK and Headless Embedding

Pipy is designed to run both as a CLI/TUI application and as an embeddable
Python agent runtime. If a Python program needs an agentic workflow, it should
link pipy in-process through `pipy_harness.sdk` rather than shelling out to the
`pipy` CLI.

## Current SDK Surface

The current public SDK is intentionally small and one-shot. It preserves the
metadata-first harness compatibility contract through
`NativeHarnessCompatibilityRuntime`; it is not yet a public embedding façade
over the canonical multi-turn product `AgentLoop`:

- `make_native_run_request(...)` builds a `RunRequest` with `pipy-native`
  defaults.
- `run_native(request, provider=..., stream_sink=...)` runs one native turn,
  finalizes the normal pipy record, and returns a `RunResult`.
- `HarnessRunner`, `ProviderPort`, `StreamChunkSink`, `CapturePolicy`,
  `RunRequest`, `RunResult`, and `HarnessStatus` are re-exported for callers
  that need lower-level composition.

Example:

```python
from pathlib import Path

from pipy_harness.sdk import make_native_run_request, run_native

request = make_native_run_request(
    goal="Summarize the current repository state",
    cwd=Path.cwd(),
)

result = run_native(request)
print(result.status)
```

`run_native(...)` defaults to the deterministic fake provider so tests and smoke
checks do not need network access. Production embeddings should inject a real
`ProviderPort` or compose `HarnessRunner` with a configured native adapter. The
catalog-backed auth/base-URL/header/routing setup used by the CLI and REPL lives
at the provider-construction boundary, so callers that want catalog behavior
should construct or inject the provider through that boundary rather than expect
`run_native(...)` to resolve catalog settings by itself.

## Intended Use

Use the Python SDK when:

- a Python application wants to drive pipy's native runtime directly;
- the caller wants normal Python objects instead of subprocess stdout parsing;
- tests or higher-level orchestrators need to inject fake providers, stream
  sinks, tools, or capture policy;
- process isolation is not required.

The SDK is the in-process headless surface. It is separate from the terminal UI
and should not depend on interactive input streams.

## Current Limits

The stable SDK surface currently covers a single native run. Richer embedding
features are design goals but not yet stabilized as public API:

- multi-turn session objects;
- native session-tree resume, fork, clone, naming, and compaction controls;
- mid-turn steering and cancellation;
- full event-stream callbacks beyond `StreamChunkSink`;
- extension UI bridging.

For now, advanced callers can compose lower-level ports directly, but should
treat those shapes as less stable than the named `pipy_harness.sdk` exports.

## Relationship to JSON/RPC Automation

The shipped Pi-style `--mode json` and `--mode rpc` transports are the
out-of-process headless surfaces. Start with the user-facing [JSON Mode](json.md)
and [RPC Mode](rpc.md) pages; the full protocol contract is specified in
[Automation & RPC](automation-rpc.md). These modes are intended for non-Python
callers or callers that want process isolation, JSONL framing, asynchronous
events, and mid-turn control.

JSON/RPC and product `--print` reuse the canonical coding session and
`AgentLoop`; they must not fork a separate product path. The narrower Python SDK
is an intentional compatibility runtime: it reuses the canonical provider-turn
executor, but retains its bounded provider-metadata fixture and workflow-archive
contract. A future canonical product SDK would replace that compatibility
surface as an explicit product/API slice rather than silently changing
`run_native(...)` semantics.

## Privacy and Storage

Embedding pipy through the SDK finalizes the normal pipy record just like the
CLI one-shot path. The metadata-first `pipy-session` archive remains
privacy-conscious by default and does not store prompts, assistant content, tool
payloads, stdout/stderr, or secrets unless a future explicit full-content
surface says otherwise.

The JSON/RPC live transports are different: they are full-content automation
streams by design, while archive privacy remains unchanged.

## D2 implementation contract

This is the selected product-session contract. D2a provides the internal
persistent lifetime; the supported public API remains pending D2b.
[The backlog](backlog.md) owns task status. The internal lifetime preserves D1's
guarded run and semantic-preparation contracts.

Provide a distinct `create_product_session(...)` entry point. Construct one
persistent synchronous product lifetime and drive it on its construction thread.
Reuse `CodingSessionState`, `CodingInputQueue`, `CodingSessionController`, current
provider/settings/trust/resource composition and fixed canonical event
projections. Callers supply workspace and optional provider/tools/settings/
resources/tree/observer inputs, never streams, terminal factories or private
extension candidates. The existing startup-candidate owner remains responsible
for activation cleanup. Default to an ephemeral product tree and no implicit
workflow archive; explicit private-tree injection enables persistence.

| Operation | Minimal contract |
| --- | --- |
| Submit | Construction-thread-only, non-reentrant, nonempty provider-visible content; preserve multiline text and bypass slash/shell command interpretation. Admit through the existing queue and drive through extension continuations to true idle. Preserve one state/history across calls. |
| Observe | One canonical sink fixed at construction, synchronous existing composite order, full-content private events, no replay/subscriber registry. Preserve callback failure semantics. |
| Cancel | Sole cross-thread method; signal the current accepted operation through canonical cancellation. Idle cancel is harmless and cannot poison the next submit. |
| Snapshot | Detached immutable state projection on the construction thread while idle; reject observer reentry. Do not infer arbitrary-thread coherence from partially locked counters. |
| Close | Idempotent construction-thread idle disposal/context-manager exit through existing lifecycle, effect and generation owners; later submit refuses. Fatal driver exit uses the same retirement path. |

Callbacks have no promised thread affinity: provider deltas may originate on a
worker. They may request cancellation, but may not submit, close or read a
thread-confined snapshot. Cancellation closes admission to new deltas without
promising that previously entered synchronous callbacks have already returned.
No new worker, mailbox, second queue, mutable event-built history, or async loop
is needed for D2.

Composition supplies internal headless I/O adapters: discard rendered transcript
presentation and forward diagnostic text to an optional construction-time
`diagnostic_sink`. Do not accumulate unbounded hidden stream buffers. Without
that sink, diagnostic text is intentionally dropped; structured run failures
remain observable through the existing state/result projection. Canonical events
alone do not contain compaction notices or extension diagnostics. The exact
adapter seam and focused diagnostic-delivery tests belong to D2b.

Callers must use the context manager or explicitly close the idle session.
Dropping an unclosed object does not guarantee shutdown hooks or retirement;
D2 promises no garbage-collection/thread-unsafe finalizer. This caller obligation
does not weaken cleanup on a driver failure or explicit close.

D2a implements explicit idle yield and a persistent lifecycle in the existing
controller/composition seam. `CodingSession._open_lifetime` is internal and still
takes composition streams; it is not the supported SDK factory. Its prepared
handle seeds the existing coding queue and drives it without reading fresh input
until the controller settles and re-polls to idle. The prepared adapter and
candidate-scoped composition live in existing `repl/wiring.py`; the facade
supplies only its explicit conversion callback. The stream-driven `run_loop` delegates to that same
owner and preserves its startup, EOF, fatal-return, settlement and shutdown
ordering. Idle is not EOF: yielding between submits must neither finalize nor
close the session. Preserve startup-failure cleanup and existing extension
candidate ownership. The startup-candidate scope surrounds the complete lifetime,
including all idle intervals and disposal. Closing a failed-startup handle also
prevents subsequent driving. Each agent run releases its witness before idle;
a later run captures current context without reinitializing session state.
D2b adds the supported factory/facade, thread-entry checks,
cancel linkage, immutable snapshots and two-turn headless acceptance.

The acceptance scenario uses a recording fake provider and real bounded tools in
a temporary workspace. Submit one write workflow, then read/check in a second
submission. Assert actual file/check results, the complete first exchange in the
second request exactly once, accurate state, balanced ordered events, one startup,
no intermediate shutdown and one shutdown on repeated close. Separate tests pin
blocked-provider cancel followed by successful submit, idle cancel, foreign-thread
and reentrant refusal, observer failure and startup/fatal cleanup. Preserve D1's
state-first persistence behavior rather than inventing rollback.

D5a later extends the shared owner before RPC migration. Its guarded inventory
must include RPC active/reserved state, steering/follow-up queues, prompt admission,
ended-run settlement/next reservation, abort clearing and EOF/drain. Preserve
steering-first one-per-boundary delivery, classified raw content, truthful pending
counts and `isStreaming`, no false-idle admission window, and one true-idle event.
Remove old transport writers as each family migrates; leave framing/correlation
in RPC. Model/thinking and compaction/retry controls stay D5b–d.

D6 owns resume/replacement and broader close semantics. D6c migrates one entrypoint
at a time, then removes replaced compatibility surfaces and their dedicated
implementation/tests when callers are gone. D2 leaves `run_native`,
`make_native_run_request` and `NativeHarnessCompatibilityRuntime` unchanged;
no alias may silently assign their names to different product semantics.

Expected D2 owners are the existing controller, coding session, wiring and loop
step, plus a small facade and `sdk.py`. Extract existing prompt/resource
composition only if needed; never copy it into the facade. Update architecture,
harness contracts and release notes when the behavior lands. Validate focused
controller/API/event/lifecycle tests, `just check`, `just docs-build` and
`git diff --check`; controller/input changes also need PTY smoke.

# Python SDK and Headless Embedding

Pipy is designed to run both as a CLI/TUI application and as an embeddable
Python agent runtime. If a Python program needs an agentic workflow, it should
link pipy in-process through `pipy_harness.sdk` rather than shelling out to the
`pipy` CLI.

## Product session API

`create_product_session(...)` starts one synchronous native coding lifetime with
an explicitly supplied tool-capable `ProviderPort`. `submit()` accepts literal
nonempty text, preserves multiline content (including leading `/` and `!`), and
returns a `CodingSessionResultSnapshot` after all extension continuations settle.
It retains the same conversation and resources for later submissions.

```python
from pathlib import Path

from pipy_harness.sdk import ProviderPort, create_product_session


def work(provider: ProviderPort) -> None:
    with create_product_session(workspace=Path.cwd(), provider=provider) as session:
        session.submit("Create a small example and check it")
        result = session.submit("Read the example and explain the check result")
        print(result.user_turn_count)
```

Construct the real provider through pipy's existing provider-construction
boundary, then inject it. The factory does not choose a catalog provider or a
fake implicitly. Tests can explicitly inject a tool-capable fake.

All factory arguments are keyword-only:

| Argument | Meaning |
| --- | --- |
| `workspace: Path` | Required directory; expanded, resolved and validated before preparation. |
| `provider: ProviderPort` | Required tool-capable provider. |
| `tools` | Optional name-to-model-tool dictionary; defaults to the production registry. |
| `settings: SettingsManager` | Optional existing settings/trust owner; omitted settings fail closed for project sources. |
| `resources: RuntimeResourceOptions` | Optional native resource loading options, including explicit extensions. |
| `tree: NativeSessionTree` | Optional private product tree; otherwise ephemeral. |
| `observer: AgentEventSink` | Optional fixed canonical sink implementing `emit(event)`; receives full-content `AgentEvent` values. |
| `diagnostic_sink: Callable[[str], None]` | Optional synchronous diagnostic destination. |
| `load_context_files: bool = True` | Discover workspace instructions normally; `False` matches `--no-context-files`. |

`ProductSession`, `CodingSessionResultSnapshot`, `AgentEvent` and `AgentEventSink`
are exported alongside the factory from `pipy_harness.sdk`. There is no workflow
archive or caller-owned stream requirement. Mutable injected settings, private
trees and other resources retain their native ownership requirements; do not
share them across simultaneously active lifetimes. In particular,
`SettingsManager.bind_state_lock` runs during composition, before workers can
reach settings; it is not a live rebinding facility.

### Operations and failures

`submit(content)`, `snapshot()`, `close()` and context entry/exit require the
construction thread and reject synchronous callback reentry. Whitespace-only or
non-string submissions refuse without retiring the session. `snapshot()` returns
the existing detached immutable native projection, including messages, usage and
provider failure and a separate `preparation_failure: AgentFailure | None`. It
remains readable after close. Both observer and diagnostic callbacks may
originate on provider or tool workers as well as the construction thread. Sinks
must tolerate those calling threads; the facade adds no callback serialization.
They may call `cancel()`, but cannot call the thread-confined operations from
workers or through reentry.

`cancel()` is the sole cross-thread method. It signals the current accepted
submission through canonical provider, semantic-summary and model-tool
cancellation. Idle cancellation is harmless, and a retired submission's delayed
cancellation cannot affect the next one. Cancellation does not undo tool effects
or guarantee that an already accepted tool will not run. A tool result that
completed before its cancellation signal remains recorded; late success after
cancellation is discarded. Uncooperative extension tools use bounded cleanup;
already entered callbacks may finish later, while the native output gates prevent
new provider deltas and tool live-output callbacks after execution retirement.
No additional driver worker or queue is created.

A returned provider failure remains visible in the snapshot and can be followed
by another submission. A request-preparation refusal also settles recoverably,
without a provider call or assistant message lifecycle events. Its full typed
failure appears in `preparation_failure` and the canonical failed run event;
accepted input, completed tools and usage remain recorded. The preparation field
clears before a new accepted input's callbacks or when context is replaced,
including an extension continuation in the same submit. Provider-failure
retention is independent. Model-aware admission now uses declared metadata or an
explicit `compaction.contextWindow` ceiling, with an estimated output reserve.
An injected provider without a resolved declaration stays unknown unless a ceiling
is supplied. Invalid policy and final estimated overflow refuse recoverably; later
settings changes apply to the next request preparation. Estimates are not
guaranteed fit.

A persistent tree cannot compact to a newly accepted first-iteration anchor before
that user has a durable origin; it refuses that summary attempt and may refuse the
ordinary request, then persists the accepted user once during normal settlement.
See [Compaction](compaction.md) for this boundary. Public compact/model/context
controls remain unavailable. Correct injected settings for a later submission, or
close and create a new session when explicit context replacement is needed; no
hidden retry or compatibility `run_native` change is introduced.

A terminal driver failure, such as three consecutive malformed tool calls,
retires the lifetime and raises `RuntimeError` containing
the native failure type and explanation. Its retained history and counters remain
available through `snapshot()`. Unexpected provider or observer exceptions propagate
through native cleanup and retire the lifetime; later submissions refuse.
Construction failure cleans up the startup candidate. Normal close and context
exit dispose once. Callers must use the context manager or close explicitly;
dropping the object provides no finalizer guarantee.

Rendered transcript presentation is discarded without buffering. Diagnostics
are delivered to `diagnostic_sink` as individual nonempty write fragments, which
may contain partial lines or standalone newlines; flush adds no fragment. The
sink follows the originating native callback's synchronous failure semantics.
Without a sink, diagnostics are dropped and structured failures remain visible
in state/results. Diagnostics and canonical events can contain private product
content and must not be routed into metadata-only workflow summaries.

## One-shot compatibility API

`make_native_run_request(...)` and `run_native(request, provider=...,
stream_sink=...)` retain their existing metadata-first harness semantics.
`run_native` returns `RunResult`, finalizes a workflow record, and defaults to a
deterministic fake provider. It does not silently acquire multi-turn product
semantics. `HarnessRunner`, `CapturePolicy`, `RunRequest`, `RunResult`,
`HarnessStatus`, `ProviderPort` and `StreamChunkSink` remain exported.

## Current limits and JSON/RPC

Public resume/fork/clone controls, steering queues, model/thinking controls,
manual compaction and extension UI bridging remain later work. The initial
product API supports a fixed observer and explicit provider injection. Live
provider dogfooding and semantic-summary quality remain unverified by the
synthetic acceptance tests.

[JSON Mode](json.md) and [RPC Mode](rpc.md) are the out-of-process headless
surfaces for process isolation, JSONL framing and mid-turn controls. Product
embedding, JSON/RPC and `--print` reuse the canonical coding session and agent
loop. RPC still owns its transport admission, reservations and settlement.
See [Automation & RPC](automation-rpc.md) for that contract.

## Privacy and storage

The product session API and JSON/RPC events are full-content surfaces. The
injected native product tree is private and full-content; default embedding is
ephemeral. The compatibility API's `pipy-session` workflow archive remains
metadata-first and excludes prompt, assistant, tool and file bodies by default.
These stores and their policies remain separate.

## D2 implementation contract

This is the implemented product-session contract. D2a provides the internal
persistent lifetime and D2b exposes the supported public API.
[The backlog](backlog.md) owns task status. The internal lifetime preserves D1's
guarded run and semantic-preparation contracts.

Provide a distinct `create_product_session(...)` entry point. Construct one
persistent synchronous product lifetime and drive it on its construction thread.
Reuse `CodingSessionState`, `CodingInputQueue`, `CodingSessionController`, current
provider/settings/trust/resource composition and fixed canonical event
projections. The initial factory requires workspace and an explicit tool-capable
`ProviderPort`; tools/settings/resources/tree/observer inputs and
`load_context_files: bool = True` remain optional.
Callers never supply streams, terminal factories or private
extension candidates. The existing startup-candidate owner remains responsible
for activation cleanup. Default to an ephemeral product tree and no implicit
workflow archive; explicit private-tree injection enables persistence.

An omitted provider does not silently select a fake or reproduce CLI catalog
policy. Catalog-backed callers can construct and inject a provider through the
existing provider-construction boundary. Automatic configured selection is outside
this initial API; it is not a prerequisite for embedding a real provider.

### Shared preparation prerequisite

D2p supplies reusable internal methods within `CodingSessionAdapter` in
`adapters/native.py`; the public factory delegates to them.
`prepare_session_context(cwd)` accepts an expanded, resolved and validated
workspace directory and resolves the
provider, fail-closed default settings (`project_trusted=False`), system prompt,
instruction/skill composition and bounded reference roots. Its private prepared
value retains the provider and settings identities. `build_session(context)`
uses that value and the adapter's configured options to construct `CodingSession`
without entering its lifetime. Call both methods on the same configured adapter,
using one adapter per lifetime; injected trees, activation batches, cancellation
and other mutable inputs retain their existing ownership requirements. The D2b
factory validates and normalizes its workspace before calling preparation.
Preparation remains in this existing composition owner; callers do not copy the
constructor mapping or move it into the narrower workspace-instruction loader.

The stream adapter emits `native.workspace_context.loaded` after successful
preparation and before session construction, preserving its archive emission
and failure ordering. A preparation failure emits no context event; a failing
event sink prevents construction; constructor failures follow the context event.
Shared preparation itself creates no workflow record or archive events. D2b uses
`default_workspace_instruction_loader` by default, matching normal CLI context
loading; `load_context_files=False` selects `empty_workspace_instruction_loader`,
matching `--no-context-files`. This switch controls AGENTS.md/pipy.md instruction
discovery independently of project trust. Trust continues to gate project
system-prompt defaults, settings and resources through their existing seams;
it does not become a new instruction-file filter. The direct adapter's default
empty loader remains unchanged. D2p compares stream and shared preparation with
the same loader/settings/options, including untrusted project resources and
injected providers. D2b tests separately pin default instruction loading and the explicit opt-out.
Preparation and wiring use the same settings instance; injected settings retain
their explicit trust decision. The compatibility one-shot SDK is unchanged.

### API ownership and cancellation

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
D2 uses the existing provider/tool execution workers and adds no product-driver
thread, mailbox, second queue, mutable event-built history, or async loop.

The small product facade belongs at harness level in `pipy_harness/product_api.py`,
alongside `sdk.py`. It owns composition of `CodingSessionAdapter`, the injected
provider, internal I/O and the entered native lifetime. `sdk.py` re-exports the
supported factory and API types. The facade calls the existing private
`CodingSession._open_lifetime` as-is; callers of the public factory never use that
internal seam themselves. No `native.*` module imports the product facade or SDK.
Native coding and
agent cores do not import the adapter; existing automation entrypoints retain
their adapter composition. `native/coding/product_session.py` remains the existing
state-first
persistence coordinator. Reuse `CodingSessionState.result_snapshot()` and its
frozen projection; do not reconstruct history or counters from events. Started
lifetime closed/result state stays with the D2a controller owner.

The facade's new concurrency state is limited to entry confinement and an active
operation cancellation slot. Its required reader/writer inventory is:

| State | Readers and writers | Boundary |
| --- | --- | --- |
| Construction-thread identity and entry-in-progress guard | Construction, submit, snapshot, close/context entry/exit, including calls made by observers or diagnostics | Identity is immutable; check thread before reading owner-thread state. Install the entry guard before callbacks and retain it through cleanup; synchronous reentry refuses. |
| Active operation cancellation latch | Submit admission and final settlement/retirement write the slot; cancel and the existing abort-signal bridge read it | One dedicated lock covers every slot access. Each operation uses a fresh latch; retirement detaches that exact latch. Capture under the lock, invoke cancellation callbacks outside it. A delayed cancellation of an old latch cannot signal the next operation. |
| Conversation, queue, generation and lifecycle state | Existing coding/queue/controller/effect owners | Preserve their current guards and per-run witnesses; the facade adds no second mutable owner. |

The accepted-abort callback primitive lives in `native/cancellation.py`; it was
moved unchanged from RPC. RPC imports it without
changing queue admission, reservations, settlement or abort clearing. The facade
does not import RPC. D2 does not reuse or clear a retired operation's latch.

Headless provider and semantic-summary cancellation already use canonical
execution. Model-tool cancellation connects the existing external-abort
signal to `ToolInterruptWaiter` in `repl/turn_leaves.py` and selects it in
`repl/loop_step.py` when no terminal exists and an abort signal is installed.
Headless runs without that signal retain direct tool execution, including current
print/JSON paths. Reuse the canonical tool worker,
ordered completion/cancellation, output admission and bounded cleanup; do not
create another executor. Production model-driven bash already consumes that
cancellation event. Arbitrary extension tools remain cooperative. This work is
distinct from D7's direct RPC bash path through `command_sandbox.py`.
The shared headless composition also enables canonical model-tool interruption
for RPC's active-run abort, with focused transport regression coverage; its
admission/settlement and event/correlation owners remain unchanged. D2b updates
`docs/automation-rpc.md`, the user-facing `docs/rpc.md` and `CHANGELOG.md` alongside
SDK/architecture/harness documentation for this behavior.

Executable D2b tests must cover cancellation during provider, semantic-summary
and model-tool execution, cancellation racing with settlement, harmless idle
cancel and a successful next submission. Preserve existing RPC behavior when
rehoming the signal, including accepted-abort replay and delta-admission ordering.

Composition supplies internal headless I/O adapters: discard rendered transcript
presentation and forward diagnostic text to an optional construction-time
`diagnostic_sink`. Do not accumulate unbounded hidden stream buffers. Without
that sink, diagnostic text is intentionally dropped; structured run failures
remain observable through the existing state/result projection. Canonical events
alone do not contain compaction notices or extension diagnostics. D2b supplies
that adapter seam and focused diagnostic-delivery tests.

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
D2b provides the supported factory/facade, thread-entry checks,
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

The D2 owners are the existing adapter preparation, the small outer facade
and `sdk.py`, the shared cancellation primitive and headless waiter composition.
Use D2a's lifetime without moving its ownership. Never copy prompt/resource
composition into the facade. Update architecture,
harness contracts and release notes with behavior changes. Validate focused
controller/API/event/lifecycle tests, `just check`, `just docs-build` and
`git diff --check`; controller/input changes also need PTY smoke.

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

`open_product_session(...)` starts a fresh lifetime over one exact existing
native session-tree JSONL file. It requires the same explicit provider and a
workspace that matches the stored, absolute header cwd after resolution. It is
for deliberate reopen after the prior public lifetime has closed; it does not
replace a live facade or select a recent/session-id target.

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
are exported alongside both product-session factories from `pipy_harness.sdk`. There is no workflow
archive or caller-owned stream requirement. Mutable injected settings, private
trees and other resources retain their native ownership requirements; do not
share them across simultaneously active lifetimes. In particular,
`SettingsManager.bind_state_lock` runs during composition, before workers can
reach settings; it is not a live rebinding facility.

To reopen a durable tree, pass its path explicitly. The loader is strict at this
public boundary: malformed JSON, unrecognized or invalid entries, duplicate
headers/entry IDs, and invalid parent or anchored-compaction ancestry refuse
before provider work, extension activation, or append. Existing internal and
CLI tree opens retain their permissive recovery behavior. Pipy holds one
process-local lease for each canonical durable path across both
`open_product_session(...)` and `create_product_session(..., tree=tree)`;
relative and symlink aliases conflict while a public lifetime is active. The
lease releases on startup failure and on normal, terminal, or explicit close.
This is not a cross-process file lock.

```python
from pipy_harness.sdk import open_product_session

with open_product_session(
    workspace=Path.cwd(), session_path=session_file, provider=provider
) as session:
    session.submit("Continue from the durable conversation")
```

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
close and create a new session when explicit context replacement is needed.
Request-preparation refusals do not trigger provider retry, and compatibility
`run_native` keeps its existing behavior.

Each ordinary product provider request captures the current retry settings once.
The prepared OpenAI-Codex capability may retry an explicitly transient,
no-progress failure within that request while reusing its body and headers;
completed tools and accepted input are not replayed. Settings changes apply to
the next provider request. Cancellation covers retry backoff and provider phases,
and stale context blocks reissue through the existing fatal cleanup. Injected
providers without the prepared capability remain single-call. Auxiliary summaries
used by semantic compaction capture the same policy and prepared capability while
keeping retry events, deltas, and usage private; their original cut and context
witness gate every reissue and final acceptance. Branch summaries retain their
provider-owned behavior. D5d adopts RPC retry controls through that ordinary
canonical owner; it does not expose auxiliary-summary retries or change the
in-process `ProductSession` surface.

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

## D6c staged frontend adoption and compatibility retirement

The product-session API above is the supported in-process coding product. The
interactive, JSON, print and RPC modes already drive the same canonical coding
session and agent loop through their mode-specific adapters. The one-shot
compatibility API is different: `run_native` returns `RunResult`, creates a
metadata-first workflow record and defaults to a fake provider. The installed
`pipy run --agent pipy-native` command still reaches that compatibility runtime
through `PipyNativeAdapter`; this is a real caller whose archive, streaming,
failure and exit semantics cannot be replaced implicitly.

At the D6c0 inventory boundary, `run_native` and
`make_native_run_request` have no production callers inside this repository,
but they remain documented and covered public names. D6c never aliases those
names to `ProductSession` behavior. A retirement slice may remove them outright
only after the user-facing embedding path and checked callers use the product
replacement and the exact export/documentation change has been reviewed. The
compatibility runtime and adapter remain while `pipy run` uses them, even if the
Python SDK names are later retired.

D6c1 is the first selected adoption: add a checked-in hermetic
`create_product_session` example, execute it in a focused test, and make that
example the README's primary Python embedding path. It must inject an explicit
tool-capable fake, perform two submissions in one construction-thread lifetime,
observe detached idle snapshots, close through the context manager, and create
no workflow archive. It changes no runtime package, provider selection,
session-tree policy, SDK export or compatibility behavior.

Terminal session replacement remains a separate adoption program. The current
`/new`, `/resume`, `/fork` and `/clone` handlers still replace trees directly
and the terminal lifetime does not yet own the D6b canonical lease slot. D6c2
will review the smallest `/resume` selection contract before D6c3 changes code;
listing, rename/delete, `/tree`, import/export, `/new` and fork/clone remain
outside that first terminal slice.

## D6b public session-transition contract

**D6b3 shipped:** `ProductSession.fork()`, `clone()`, `new_session()`, and
`switch_session()` and the matching JSONL RPC commands use the native
transition owner. Terminal command adoption remains deferred to D6c.

D6b adds four construction-thread, idle-only operations to the existing
`ProductSession` facade. They are not factories and do not create another
coding lifetime: `fork(entry_id: str | None = None)`, `clone()`,
`new_session()`, and `switch_session(session_path: Path)`. They use the
facade's already validated workspace and its current explicit provider,
tools, settings, resources, observer, diagnostic sink, and context-file
policy. A transition never selects a provider, adopts a target workspace, or
changes `run_native` semantics.

Each method returns this frozen public value (exported from
`pipy_harness.sdk` with `ProductSession`):

```python
@dataclass(frozen=True, slots=True)
class ProductSessionTarget:
    session_id: str
    session_path: Path | None
    leaf_id: str | None

@dataclass(frozen=True, slots=True)
class ProductSessionTransitionResult:
    operation: Literal["fork", "clone", "new", "switch"]
    status: Literal["completed", "refused"]
    active: ProductSessionTarget
    previous: ProductSessionTarget | None
    refusal: Literal[
        "ephemeral_source", "missing_leaf", "unknown_entry",
        "extension_refusal", "lease_conflict"
    ] | None

@dataclass(frozen=True, slots=True)
class ProductSessionTransitionFailure:
    operation: Literal["fork", "clone", "new", "switch"]
    retained: ProductSessionTarget
    stage: Literal["load", "create", "publish", "rebuild"]
    published: bool

class ProductSessionTransitionError(RuntimeError):
    failure: ProductSessionTransitionFailure
```

`completed` has `refusal is None` and exactly one active tree; except for the
same-target no-op its active target differs from `previous`. `refused` reports
the still-active old target in `active`, has `previous is None`, and has one
listed refusal. `session_path` is `None` for an ephemeral current or previous
tree, including an `ephemeral_source` refusal. A same-path
`switch_session` is a successful no-op: it reports `completed`, retains the
same target in both `active` and `previous`, runs no extension hook, changes no
lease, tree pointer, queue, context, or event/UI binding. Invalid public
argument types and empty/invalid entry identifiers raise `TypeError` or
`ValueError` before an operation begins. Unexpected loading, creation,
persistence during creation, or rebuild failures raise `ProductSessionTransitionError`; its
frozen `.failure: ProductSessionTransitionFailure` records the coordinator's
retained target and whether that target was already published. The SDK exports
`ProductSessionTarget`, `ProductSessionTransitionResult`,
`ProductSessionTransitionFailure`, and `ProductSessionTransitionError` alongside
`ProductSession` and the two factories. It deliberately does not expose
arbitrary exception text as a machine-readable outcome. Closed, reentrant
or non-idle calls retain the current `ProductSession` `RuntimeError` behavior;
they do not manufacture a transition result.

All four methods have the same thread and reentry rules as `submit()` and
`close()`: the construction thread calls while the existing facade is open and
the native queue is truly idle, with no accepted, reserved, extension-delivered,
or settling run. `cancel()` remains the only cross-thread operation. The
current provider and validated workspace remain owned by the facade for its
whole lifetime. Transition neither emits a new `session_start` nor a
`session_shutdown`; those lifecycle events still occur once for construction
and once for retirement of this facade.

`fork(entry_id)` requires a persistent source. With no `entry_id`, it uses the
current leaf; with one, it accepts an exact existing entry ID of any native
entry type, not merely a user entry. It copies that entry's root-to-leaf branch
from the guarded active in-memory tree; it does not permissively reopen the
source path.
`clone()` is `fork()` of the current leaf and refuses `missing_leaf` for an
empty persistent source. Both retain the terminal command's child semantics:
the child gets a fresh session ID and fresh entry IDs, `parentSession` points to
the source path, labels are reattached to mapped IDs, branch-summary references
are remapped, and the source name is copied. The child directory is exactly the
source persistent file's parent; callers cannot provide a child path or
directory. A fork/clone first resolves the selected source entry, then runs the
`session_before_fork` gate with the existing `operation="fork"` vocabulary,
then creates and claims its child before it can be published. The public result
still distinguishes `fork` from `clone`.

`new_session()` is the public equivalent of terminal `/new`. It uses the
current persistent tree's parent directory and creates a fresh, empty,
persistent tree for the already-owned workspace. It refuses `ephemeral_source`
when the active facade has no persistent tree. It runs `session_before_switch`
with target `"new"` before durable creation, so a veto leaves no new file.
`switch_session(session_path)` canonicalizes its exact supplied path and first
detects a same-target no-op without reading it. For another target it calls
`session_before_switch` with that canonical path, then claims the candidate
through the D6a canonical registry, then strict-loads and workspace-validates it
while the claim is held. It does not do partial-ID lookup, recency selection, or
permissive recovery. This preserves D6a claim-before-load protection and keeps
target content unread until an extension allows the operation.

The gate is fail-closed. A hook veto or hook failure returns the one stable
`extension_refusal`; the public projection never infers a crash from a
diagnostic string. Neither starts persistence, releases the old lease,
publishes a tree, rebuilds state, or emits a successful-transition observation.
For the public API, the one successful transition observation is its returned
immutable result; it emits no new lifecycle or extension-completion event and
has no UI subscription to rebind. RPC supplies one correlated result and one
event/UI rebind. D6b does not add a second extension lifecycle generation or an
event bus.

`src/pipy_harness/native/repl/session_transition.py` is D6b's narrow native
owner. D6b1 creates it, moves D6a's process-local canonical-path registry there
as `CanonicalSessionLeaseRegistry`, and adds its guarded
`CanonicalSessionLeaseSlot`. The slot holds at most one current lifetime lease;
all current-lease reads, replacement and retirement go through it. D6b1 composes one
`SessionTransitionCoordinator` in `native/repl/wiring.py`. Its typed port is
returned through the already prepared native lifetime. `product_api.py` calls
that port; native modules never import the facade or SDK. The port receives the
existing `RunControlState` tree setter, extension gate and
`CodingProductSessionCoordinator.rebuild_active_history` callbacks from wiring;
it does not own a queue, lifecycle, renderer, event bus, or DI container. The
same port serves RPC and is the eventual terminal-adoption seam.
The neutral module defines the exact immutable transition values and typed
error; `product_api.py` and `sdk.py` re-export those same classes rather than
maintaining a second public projection.

The slot prepares a candidate handoff by claiming its canonical path while
retaining the old current lease. That private handoff holds the candidate until
exactly one `publish()` or `abort()`. Prepublication load/create/claim failure
aborts the handoff, releases only the candidate, and leaves the old tree, lease,
slot and usable facade unchanged. The `RunControlState` setter either fails
before assignment or publishes the candidate pointer. Immediately after a
successful setter call, the coordinator calls the handoff's non-failing
`publish()`: it replaces the slot's current lease exactly once and then releases
the old claim. The public immutable result contains no lease or handoff object.

After an extension has allowed fork/clone or fresh replacement, a child/fresh
file may remain after any later creation, claim, publication, or rebuild failure;
a failed creation write is not promised to have a valid header or complete
branch. Strict-load failure creates no new artifact. The rebind is state-first:
once it has published the new pointer, rollback is not claimed. A rebuild
failure then retires/closes the public lifetime before raising
`ProductSessionTransitionError`; its failure has `published=True` and the
selected target, and only the existing post-close snapshot/close semantics
remain. Facade teardown calls the slot's idempotent `finish()`, which releases
the adopted candidate; the old claim was already released by `publish()`. If the
tree setter fails before assignment, the handoff aborts and teardown is not
required. A prepublication failure has `published=False` and retains the old
target.

For D6a create/open, `product_api.py` creates this neutral slot around the
initial lease before native lifetime composition and remains the lifecycle
caller of `finish()`. The prepared native lifetime binds that exact slot once to
the coordinator port before any public transition. Native wiring creates and
binds a slot for the RPC lifetime's initial persistent tree before accepting input;
its fatal teardown uses the same `finish()` path. The registry and slot use one
documented lock order, and no caller reads or swaps their mutable lease fields
directly.

At public true-idle the accepted, reserved, and settling queue is empty. The
coordinator reuses that queue, controller and stable external abort view for the
facade lifetime; it neither detaches the view nor settles retired-tree work.
The synchronous construction-thread facade proves that external-idle boundary
before it calls the coordinator; its transition body never holds the queue or
admission guard across an extension callback, filesystem operation, lease
handoff, tree publication, or rebuild. A separate private admitted-control port
is reserved for D6b3's already-claimed RPC control operation.
History rebuild clears the current existing tree-bound extension inputs/outboxes.
For D6b3 an RPC command can be the admitted native control claim, so the port
also exposes a private admitted-control call path; public methods use only its
external true-idle entry.

D6b1's exact write set is `src/pipy_harness/product_api.py`,
`src/pipy_harness/sdk.py`, `src/pipy_harness/native/session_tree.py`, new
`src/pipy_harness/native/repl/session_transition.py`, and
`src/pipy_harness/native/repl/wiring.py`; tests are
`tests/test_product_session_api.py`,
`tests/test_native_coding_session_fork_clone.py`, and
`tests/test_native_extension_lifecycle.py`; documentation is these five D6b0
documents plus `CHANGELOG.md`. It creates the registry/coordinator/common port
and implements only fork/clone regions. D6b2's exact write set is
`src/pipy_harness/product_api.py`,
`src/pipy_harness/native/repl/session_transition.py`, and
`src/pipy_harness/native/repl/wiring.py`; tests are
`tests/test_product_session_api.py`,
`tests/test_native_coding_session_lifetime.py`, and
`tests/test_native_extension_lifecycle.py`; documentation is these five D6b0
documents plus `CHANGELOG.md`. It implements only fresh/switch operation regions
and must not change the D6b1 result schema, lease registry or fork/clone
regions. These are serial single-writer slices; neither edits RPC files or
handlers. D6b3 alone changes `src/pipy_harness/native/automation/rpc.py`,
`src/pipy_harness/native/coding/session_controller.py`,
`src/pipy_harness/native/repl/wiring.py`,
`tests/test_native_automation_rpc.py`,
`tests/test_native_coding_session_controller.py`, and the RPC/architecture
contract after both public operations exist.

## Current limits and JSON/RPC

Public D6b product-session transitions now ship: `fork`, `clone`,
`new_session`, and strict `switch_session`; RPC uses the same transition owner.
Public steering queues, model/thinking controls, manual compaction and extension
UI bridging remain later work. The product API supports a fixed observer and
explicit provider injection. Live provider dogfooding and semantic-summary
quality remain unverified by the synthetic acceptance tests.

[JSON Mode](json.md) and [RPC Mode](rpc.md) are the out-of-process headless
surfaces for process isolation, JSONL framing and mid-turn controls. Product
embedding, JSON/RPC and `--print` reuse the canonical coding session and agent
loop. RPC owns transport framing, correlation and projection; native
queue/control owns RPC admission, reservations, abort and settlement. See
[Automation & RPC](automation-rpc.md) for that contract.

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

The facade's mutable concurrency state is limited to entry confinement. Managed
operation admission, settlement and cancellation are native queue-owned. The
required reader/writer inventory is:

| State | Readers and writers | Boundary |
| --- | --- | --- |
| Construction-thread identity and entry-in-progress guard | Construction, submit, snapshot, close/context entry/exit, including calls made by observers or diagnostics | Identity is immutable; check thread before reading owner-thread state. Install the entry guard before callbacks and retain it through cleanup; synchronous reentry refuses. |
| Managed operation reservation and cancellation latch | `CodingInputQueue` atomically admits/claims and settles the exact token under its existing guard; cancel captures the queue-owned latch and signals it after unlock | Each operation uses a fresh latch. A stable native signal view is bound once to the exact queue after successful startup; its binding lock protects only queue-reference publication/capture. Latch observation and callback registration happen after both guards are released, so delayed work on an old latch cannot affect the next operation. |
| Conversation, queue, generation and lifecycle state | Existing coding/queue/controller/effect owners | Preserve their current guards and per-run witnesses; the facade adds no second mutable owner. |

The accepted-abort callback primitive lives in `native/cancellation.py`; it was
moved unchanged from RPC. RPC imports it without
changing queue admission, reservations, settlement or abort clearing. The facade
does not import RPC. `ProductSession` does not reuse or clear a retired
operation's latch.

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

### D5a1 external admission mechanism

This internal D5a1 mechanism is implemented but remains unavailable as an SDK
control surface. The existing `CodingInputQueue` owns dedicated external steering
and follow-up FIFO lanes plus one reserved/active operation under its existing
coding-effects RLock. It does not reuse extension lanes or add another mutable
queue owner. All reads, snapshots, admission, claim, settlement and abort capture
of these fields use that same guard. Immutable results may leave the guard;
provider execution, cancellation callbacks, frontend output and lifecycle hooks
must not run inside it.

Admission validates exact immutable content and the closed delivery kind before
mutation, preserving content including whitespace, slash/shell prefixes and
newlines. An ordinary idle prompt reserves the active slot; an ordinary prompt
while active joins its steering lane when requested, otherwise its follow-up
lane. Explicit steering/follow-up admissions retain that classification even when idle. The reserved request keeps
an ordinary idle prompt distinguishable from a classified queued prompt; this
mechanism performs no local command parsing. An idle classified admission
reserves that admission immediately. Pending counts and lane snapshots exclude
the reserved item. Each reservation receives an opaque identity tied to this
queue and a fresh accepted-abort latch. Reservation is already active for state
reporting; claiming its content once does not create an idle interval.

Only the exact current reservation may be claimed, once. Only its valid claimed
operation may settle. A stale, foreign, duplicate or unclaimed settlement must
leave every field unchanged. Successful settlement retires that operation and,
in the same guarded transition, reserves at most one pending message, steering
first and FIFO within each lane. Its immutable result reports the next reservation
or idle from that transition. This result is not authority to emit a later public
`agent_settled`: D5a2 must specify serialization with concurrent admission first.

Abort atomically discards pending steering, preserves pending follow-up and
captures the exact current latch, including a not-yet-claimed reservation. It
signals that latch only after releasing the queue guard; the operation must be
entered without a caller-held queue guard. The latch is never cleared and reused.
If settlement promotes another reservation before delayed signaling, only the
captured old latch is affected. Idle abort does not poison a later admission.
Aborting a reservation does not discard its accepted content or release the slot;
its claimant observes cancellation and must eventually settle it. No callbacks
are registered or invoked while holding the queue guard.

D5a1's classified managed lanes remain unavailable as SDK controls. D5a2b adopts
the same owner only for ordinary `ProductSession` operations through its atomic
idle-only entry; existing selection methods, external ports, active-loop delivery
and extension clearing remain as implemented. Direct queue tests pin
content/kind identity, concurrent admission versus settlement without false idle,
claim/settle rejection without mutation, pending counts, abort versus promotion,
callback execution outside the guard, fresh-next-reservation cancellation and
extension-lane isolation, steering-first FIFO promotion and harmless idle abort.
The adoption does not add readiness, EOF/sealing/close or event projection
semantics. Full-content values stay private product data and never enter workflow
archive summaries. Compatibility `run_native` semantics remain unchanged.

### D5a2b product operation adoption

Status: implemented by D5a2b.

D5a2b activates D5a1 for ordinary `ProductSession.submit()` and `cancel()`.
The public thread/reentry, literal-content, diagnostics, result and close
contracts above remain. One accepted operation spans the entire submit-to-idle
drive, including canonical runs and extension settled-hook continuations. It is
not retired at `AgentRunCompleted`, provider completion or an intermediate empty
poll. Cancellation stays accepted across those continuations, as in D2b.

The queue owns one atomic idle-only admit-and-claim entry. It validates content,
refuses an existing managed reservation/pending lane without mutation, then
reserves and claims one fresh operation under its existing RLock. The existing prepared
handle/controller lifetime uses that claim's exact content as a literal seed and
drives the existing step-to-idle loop. Existing selectors and external ports do
not expose managed lanes. This adoption adds no public concurrent admissions.

The former facade active-latch slot is removed. A stable native signal view is
created before composition and bound once to this lifetime's exact queue after
successful startup, before the factory returns. Unbound observation/cancel is
inert and rebinding refuses. Its only binding state is the queue reference,
guarded during publication and capture; the binding guard is released before any
queue method. The view never creates, clears or replaces an operation latch. For
`is_set` and callback registration, the queue captures its exact claimed latch
under the queue guard, then reads or registers on that captured latch after
unlock. Cancellation delegates to the existing guarded abort-capture/after-unlock
signaling. No callback, registration or cancellation signal runs under either
guard. Late unregister/cancel uses the captured old latch and cannot detach or
signal the next operation. Current provider, summary and model-tool waiters remain
the canonical execution/cancellation path.

Successful startup is required before admission. After claim, every exit—seed
failure, drive exception, terminal result or true idle—settles that exact token
once through the queue. Queue settlement finishes before normal facade scope
closure/terminal-result handling; existing driver failure retirement remains in
its controller owner. A missing/stale settlement result is an internal invariant
failure, never a successful idle return: retire/refuse further driving through
the existing lifetime. Preserve any primary exception if this new cleanup also
fails, adding only a bounded diagnostic note; otherwise raise the cleanup failure.
Do not add a second close flag, roll back accepted product history, or invent a
new EOF/sealing/pending-drain policy. Public close remains owner-thread idle-only.

Focused tests prove one token/latch through a settled-hook continuation, no
`agent_end` retirement, exact literal slash/shell content, atomic busy refusal,
cancellation through provider/summary/model-tool paths and across continuations,
idle and delayed-old cancellation followed by a fresh usable submission, and
exact retirement after enqueue/driver/terminal failure. Pin once-only startup/
shutdown, binding refusal, guard-free signal registration/callbacks, and existing
headless import boundaries. Private bridge-only tests may be replaced by equivalent
queue/view ownership tests; do not delete the late-cancel guarantees they pinned.
Compatibility `run_native`, RPC's current queue/latch/event behavior and private
workflow-archive boundaries remain.
D5a3 owns concurrent control exposure and its distinct per-run RPC settlement
contract; this operation seam does not authorize public event emission.

### D5a3 shared native control and RPC adoption contract

D5a3 is split so the native control seam can be proved before RPC adopts it.
D5a3a adds one internal, transport-neutral control over the existing
`CodingInputQueue`; it does not add a supported concurrent method to
`ProductSession` or change the compatibility SDK. The control owns an outer
admission/publication gate and a stable queue reference, but no parallel active
flag, payload list, token slot or cancellation latch. Queue state remains under
the existing coding-effects RLock. Immutable control snapshots report whether a
reservation is active plus exact steering/follow-up tuples and their pending
count; the active/reserved item is excluded from pending.

The D5a3a seam is now composed privately with each native controller. Its
optional one-shot bridge rides the existing private abort/input carrier, so it
does not create an adapter or SDK option. Current ProductSession and RPC callers
do not supply or consume it: D5a3b remains the sole adoption point.

Prompt admission, explicit steering/follow-up admission, state observation,
claim, exact settlement and abort enter the same outer gate before the queue
guard. Ordinary idle prompt admission creates the reservation; an ordinary
prompt during an active operation uses steering only for an explicit steer
behavior and otherwise follows up. Explicit steering/follow-up retain their kind
when idle. Values preserve exact `ProductContent`, including leading/trailing
whitespace, slash/shell prefixes and newlines. The control returns detached
snapshots or transitions; transport callers never receive a mutable lane or
latch.

The worker bridge claims the exact reservation before provider execution and
retains that claim as the per-run settlement capability. Settlement retires
only that token and promotes at most one successor atomically, steering first
and FIFO. The bridge may temporarily carry the immutable claim and selected
content; it must not create another payload queue or reconstruct content from
line framing.

D5a3a defines one private, one-shot startup/run bridge carried through the
existing pre-composition abort-input side rather than adding a public adapter
option. After successful native composition and session start, wiring publishes
the exact `(control, stable abort view, readiness port)` once and unblocks the
waiting frontend. Startup failure publishes one failure outcome and also
unblocks it; success-after-failure, failure-after-success and repeated publish
refuse. No prompt may be admitted before that outcome.

On the worker thread, the bridge claims one exact reservation at wake selection,
attaches that immutable claim to the selected run, and refuses another claim
until the run closes. The synchronous `agent_end` observer consumes and clears
that same claim exactly once under the publication gate before settlement.
Missing, duplicate, foreign-thread or token-mismatched consumption is a fatal
invariant failure, never permission to settle whichever reservation is current.
If a claimed run exits before `AgentRunCompleted`, worker/lifetime cleanup
consumes and settles the same claim once, preserves the primary exception with a
bounded cleanup note, and retires the lifetime. The bridge holds no independent
active flag or cancellation latch; its claim slot is only the run-scoped
capability for the queue-owned reservation.

D5a3a also defines the controller readiness port for the point after extension
settlement, outbox drain and re-poll, but does not activate RPC or expose managed
lanes to current selectors. D5a3b wires that port into the existing loop step.

Lock order is the control admission/publication gate, then the existing
coding-effects RLock. No code may acquire the outer gate while retaining that
inner lock. Release the queue lock before JSONL output, worker wake, provider or
filesystem work, extension callbacks, latch observation/signaling, or callback
registration/invocation. Abort clears pending steering and captures the exact
current latch under the queue guard, then releases both guards before signaling.
Follow-ups remain. A delayed old signal therefore cannot affect a promoted or
later reservation. The coding-effects lock also orders session-tree writes, so
implementation must audit all new entry paths rather than assume only queue
methods take it.

D5a3b migrates RPC admission, state, abort and run-boundary settlement as one
reader/writer change. Before accepting control commands, the RPC worker must
successfully compose its native lifetime and publish the bound control, abort
view and true-idle readiness port through a one-shot readiness handshake. A
startup failure produces the existing bounded failure/teardown outcome; no
provisional transport queue or latch may accept content first.

At canonical `AgentRunCompleted`, the exact worker claim is settled and the
`agent_end` record is written while holding the outer publication gate, after
the queue guard has been released. If settlement promotes a successor, do not
emit protocol `agent_settled`; write `agent_end`, project one `queue_update` from
the post-promotion snapshot, release the gate and then wake the worker. The next
`agent_start` follows that wake. Public `agent_settled` comes only from the later controller
readiness callback after extension settlement and re-poll found no work. That
callback re-enters the publication gate and rechecks the queue: admission first
suppresses the stale idle record, while idle publication first serializes before
the next command is accepted. This transport event remains distinct from the
extension hook.

RPC deletes its `_turn_active`, `_steering`, `_follow_up`, `_abort` and
reservation helpers together with every reader. `get_state` and `queue_update`
project detached native snapshots. Its input channel becomes wake/EOF-only;
framing, correlation, the JSONL writer and direct-bash state stay transport-owned.
EOF stops intake, waits for native active/pending work using the existing bounded
drain, signals channel EOF once, and preserves current worker/bash joins. This
slice introduces no public close/seal policy.

Tests must pin startup-ready admission, both admission/run-end interleavings,
claim before provider work, exact per-run settlement, one-per-boundary
steering-first FIFO delivery, literal typed content, abort before claim/during
provider or model-tool work/after old settlement, truthful snapshots, and
readiness racing admission. An extension settled-hook continuation must not
produce an intervening protocol idle line. Preserve existing LF-only framing,
response correlation, prompt-success-before-run-events, queue-update visibility,
EOF drain and cleanup tests. D5b model/thinking, D5c compaction, D5d retry, D6
session replacement/public lifecycle, D7 bash cancellation and true in-turn
message injection remain separate tasks.

### D5b RPC model and thinking control adoption contract

D5b removes RPC's local model/thinking answers and binds a private configuration
port from the existing `ProviderMutationEffects` owner into the D5a startup
outcome. The outcome is not ready until both the native queue control and this
port are bound, so command intake cannot observe a partially composed session.
The port exposes immutable model/thinking/catalog projections and bounded
operations. It never exposes the provider-state object, coding-state object,
catalog, settings, locks or provider constructors to the transport.

`NativeReplProviderState` remains the provider/model/thinking owner and
`CodingSessionState` remains the live provider binding used by the next canonical
request. `ProviderMutationEffects` retains detached provider construction,
expected-state validation, atomic publication, model capability checks, footer
refresh and default persistence. RPC serializes commands and projects results;
it does not duplicate those policies. Available-model projection includes the
current custom selection plus catalog rows that are locally available and
tool-capable. Cycling applies the existing `enabledModels` scope over that same
effective set and reports whether the scoped set was used. Every RPC model value
uses the exact immutable projection `{ "provider": string, "id": string }`;
base URLs, headers, compatibility data and other catalog internals never cross
the transport. Selecting the already active pair is an idempotent success with
no provider construction, persistence write or event.

Mutations are idle-only. Capture the expected queue and configuration values,
prepare fallible provider work without the native-control gate, then enter the
D5a gate for one final true-idle check and the existing provider-mutation commit.
The lock order is native-control gate, coding-effects/mutation-I/O lock, then the
session/generation mutex. No provider construction, filesystem I/O, JSONL output,
callback, latch signal or worker wake occurs under the native-control gate. If a
prompt is admitted or reserved during preparation, admission wins and the
configuration operation returns a correlated failure without changing live or
durable state. Once the in-memory commit succeeds, later prompts observe the new
binding. Existing state-first handling applies to post-commit presentation,
default-persistence and thinking-entry failures: report bounded diagnostics but
do not claim that an already live mutation failed.

`set_model` and `cycle_model` publish provider state and the coding binding
together through the existing prepared mutation. They preserve the current
product behavior that clears provider-visible coding history and installs a new
usage accumulator for a model switch. The prior thinking level is clamped through
the existing model-capability helper for the selected model. If the effective
level changes, the existing persistence boundary attempts one durable
thinking-level append before returning the successful owner result. RPC then
writes the model command's correlated response followed by one
`thinking_level_changed` event. No effective thinking change means neither the
append nor the event occurs.

`set_thinking_level` and `cycle_thinking_level` accept only levels supported by
the selected model, construct the replacement provider for that same selection,
and atomically refresh only the provider object in the existing coding binding.
Thinking-only rebinds retain provider-visible history, the usage accumulator and
its counters. The owner appends one durable thinking-level entry only when the
effective value changes.
`get_state` obtains model and thinking from one immutable owner snapshot; RPC has
no `_thinking_level` cache. A successful thinking command writes its correlated
response before `thinking_level_changed`; a no-op produces no event. D5b adds no
model-change protocol event.

An injected provider without `NativeReplProviderState` is an explicit static
fallback, not a second mutable owner. It exposes a singleton current model,
accepts only that same model, returns `null` for model/thinking cycles, reports
`off`, rejects unsupported non-`off` levels, and never promises that a provider
can be rebuilt. Public `ProductSession` and compatibility `run_native` semantics
remain unchanged.

### D5c RPC compaction-control adoption contract

D5c exposes the already implemented semantic-compaction owner to RPC without
putting summary policy, provider execution, durable tree state, or settings
ownership in the transport. A second private, once-bound compaction port joins
the D5a startup outcome beside the D5b configuration port. Readiness is complete
only when the queue control, abort view, readiness port, configuration port, and
compaction port are all present. The port exposes immutable state and bounded
operations; RPC never receives `ProviderMutationEffects`, `SettingsManager`,
`CodingSessionState`, `NativeSessionTree`, provider objects, or their locks.

Manual RPC compaction is an asynchronous worker operation. The RPC reader first
validates `customInstructions` as absent or an exact string, then atomically
admits one private compaction operation only when the D5a native control is truly
idle: there is no active/reserved operation and no queued steering or follow-up.
Busy admission returns one correlated failure and emits no compaction event. A
second compaction request is not queued. Once admitted, the existing
`CodingInputQueue` active slot owns the operation token and fresh abort latch;
the worker claims that exact token and carries it until compaction settles. The
operation is not encoded as `"/compact"`, is never provider-visible, and does not
create another queue, active flag, cancellation source, or policy owner.

The worker invokes `ProviderMutationEffects.compact_context` with trigger
`manual`, using its existing extension gate, complete history/tree/generation
witness, private no-tool provider request, canonical retry and cancellation,
guarded acceptance, and state-first persistence. Nonempty custom instructions
are appended verbatim as additional focus to the private summary request; they
do not replace the standard continuity instructions. Empty text is equivalent
to no additional focus. The instruction itself is not appended to the product
tree or metadata archive. The accepted generated summary remains a full-content
native-session value and may be returned only through the explicitly requested
full-content RPC operation.

Manual lifecycle ordering matches the protocol's awaited command shape. After
the worker claims the operation it writes `compaction_start` with reason
`manual`, performs no JSONL output while summary work or an owner lock is held,
then settles the exact claim and publishes `compaction_end` followed by the
correlated `compact` response under the D5a publication gate. A promoted prompt
is reflected by the normal queue projection and is continued directly by the
same worker only after those records. Successful `compaction_end.result` and
response data carry the same immutable `CompactionResult`: generated `summary`, exact durable
`firstKeptEntryId` for a persistent session, and the owner's nonnegative pre-cut
`tokensBefore` measure; optional `details` may contain only bounded counts or
measurement metadata. An explicitly non-persistent native session may compact
successfully with `firstKeptEntryId: null` and performs no durable append. A
persistent session still refuses a cut with no resolved origin. Summary-provider
usage and private retry/delta events remain excluded.

No-cut, extension-veto, budget, provider, retry-exhaustion, or stale outcomes
emit a paired end event with no `result`, `aborted: false`, `willRetry: false`,
and a bounded content-free `errorMessage`, followed by a correlated error
response. Abort before claim invokes no summary hooks or provider work. Abort
during summary generation reaches the claimed operation's canonical waiter,
publishes no state/tree transition, and ends with `aborted: true`, no result or
error message, and a correlated cancellation error. A late abort cannot affect a
promoted operation. Existing state-first persistence remains deliberately
non-transactional: if live acceptance succeeds and durable append then fails,
`compaction_end` includes the accepted result plus a fixed persistence-failure
message, the correlated response is an error, and subsequent state/context
reflects the accepted in-memory cut. There is no rollback or hidden retry.

Automatic compaction stays in canonical request preparation; it is never
enqueued as an RPC control operation. A narrow observer around the existing
owner emits `compaction_start` only when pressure selects a real summary attempt
and emits exactly one matching `compaction_end`. Legacy message/byte pressure
maps to public reason `threshold`; known-window estimated preflight pressure maps
to `overflow`. Both use `willRetry: false`: pipy continues preparation of the
same accepted run after a successful preflight cut and has no Pi-style
post-response compact-and-retry path. Automatic lifecycle records always carry
`result: null`, including accepted-but-persistence-failed work: generated
summaries, origins, token measures, private retry/delta events, and usage stay
inside the owner. During automatic work `isStreaming` and
`isCompacting` are both true; during claimed manual work only `isCompacting` is
true. The compaction owner guards every activity-state reader and writer, and
the observer cannot emit after operation retirement.

`set_auto_compaction` accepts only an exact boolean and calls a dedicated
precedence-aware `SettingsManager` mutation for `compaction.enabled`. If the
requested value already equals the effective policy, it is an idempotent success
without a write. Otherwise the owner updates the explicit trusted-project value
when that writable layer currently supplies the key, or the global fallback
when no higher writable layer supplies it. A conflicting CLI/environment
override cannot be changed by RPC; the owner refuses before writing either file.
The command response follows successful atomic file replacement and in-memory
publication, and a success must leave the effective value equal to the request.
Failure returns a correlated error and leaves the prior effective value. The
operation may run while an agent turn is active: the settings lock linearizes it
against policy capture, so already captured preparation uses its old immutable
value and a later capture uses the new value. `get_state` reads
`autoCompactionEnabled` from that same effective owner and `isCompacting` from
the guarded compaction activity projection; RPC deletes its local compaction
flag. Test configuration roots must be isolated and the user's theme/settings
left intact.

The control gate remains outermost whenever queue admission, exact settlement,
and publication are combined. Release the queue/coding-effects locks before
provider or filesystem work, extension callbacks, JSONL output, worker wake, or
latch signaling. EOF uses the existing bounded drain and waits for a claimed
manual compaction plus any promoted prompt. Manual compaction synthesizes no
`agent_start`, `agent_end`, or `agent_settled`; `compaction_end` is its operation
boundary. Public `ProductSession`, compatibility `run_native`, terminal
`/compact`, session replacement, retry controls, direct bash, and archive schema
remain unchanged.

### D5d RPC retry-control adoption contract

D5d removes RPC's transport-local retry toggle and connects both retry commands
to existing owners. `set_auto_retry` accepts an exact boolean and uses a dedicated
`SettingsManager` operation for the effective `retry.enabled` value. The operation
is idempotent when the effective value already matches. Otherwise it writes the
trusted project layer only when that layer explicitly supplies the key, falling
back to the global layer; it refuses a conflicting CLI/environment override
without writing. The settings I/O lock covers layer selection, atomic replacement,
publication, and the effective-value postcondition. Policy already captured for
an ordinary request is immutable; a successful mutation affects its next policy
capture.

The product composition root also constructs one stable private retry-control
port and includes it in the complete native readiness outcome. RPC may request
`abort_retry` through that port, but cannot inspect or mutate retry phase state.
Canonical `ProviderTurnExecutor` execution for an ordinary product request is the
only path that installs an exact active capability. It is installed before the
matching `auto_retry_start`, remains active across delay, guarded reissue
admission, and the reissued provider phase, and is retired atomically when the
result becomes fixed. Private semantic-compaction and branch-summary execution
do not install it. Providers without the prepared capability never install it.

The capability reuses the accepted turn's ordered cancellation mechanism; it is
not a second run abort latch. Exactly one side wins a race between retry abort and
result fixation. An accepted retry abort wakes delay/provider waits, prevents any
later attempt, emits one unsuccessful `auto_retry_end`, and proceeds through the
ordinary cancelled run settlement. Once retirement wins, a concurrent or later
command is a successful no-op and cannot affect the surrounding ordinary provider
phase, a promoted queue item, or a later run. Activation and retirement are
paired on every callback, admission, provider, cancellation, and cleanup exit.
No control/settings/queue/coding lock spans callbacks, wake signaling, provider
I/O, or JSONL output.

RPC continues to project the D4a event sequence: each reissue has one
`auto_retry_start` and one `auto_retry_end`, with no intermediate turn or agent
end. Command responses remain correlated, but `abort_retry` response order is not
a synchronization guarantee for asynchronous events. `get_state` adds no retry
activity field. Generic `abort`, queue ownership, eligibility, attempt counters,
provider transport fallback, summary privacy, compatibility `run_native`, and the
public product-session API remain unchanged.

D6 owns resume/replacement and broader close semantics. D6c migrates one entrypoint
at a time, then removes replaced compatibility surfaces and their dedicated
implementation/tests when callers are gone. D2 leaves `run_native`,
`make_native_run_request` and `NativeHarnessCompatibilityRuntime` unchanged;
no alias may silently assign their names to different product semantics.

### D6a public product-session reopen contract

D6a ships one explicit `open_product_session(...)` factory for reopening a
specific durable native product-session file. The factory is keyword-only and
requires `workspace: Path`, `session_path: Path`, and an explicit tool-capable
`ProviderPort`. It accepts the same optional tools, settings, resources,
observer, diagnostic sink, and context-file loading switch as
`create_product_session(...)`. It does not add session-id lookup, recent-session
selection, or an in-place operation on an existing `ProductSession`.

The factory validates and resolves the workspace using the existing creation
rules and canonicalizes the existing session path before acquiring ownership.
It opens exactly that native-session JSONL through a strict opt-in
`NativeSessionTree.open` mode and requires the session header's stored cwd to be
absolute and resolve to the same workspace. Strict loading rejects malformed
JSON, non-object records, invalid or unknown entries, duplicate headers or IDs,
and invalid parent/compaction ancestry rather than skipping them. The current
permissive loader remains the default for existing internal and CLI callers. A
missing or malformed file, invalid argument, or workspace mismatch fails before
composition, extension activation, provider work, or durable append. Reopen
then delegates to the same internal create-with-tree path as explicit tree
injection; callers cannot supply a second tree authority.

The product facade owns one private process-local registry keyed by the
canonical persistent session path. `open_product_session(...)` claims that path
before loading. `create_product_session(..., tree=...)` claims the same key when
the injected tree is persistent, so two public lifetimes cannot append through
separate in-memory trees or evade exclusion through a relative path or symlink
alias. A conflicting active lifetime refuses before composition or mutation.
The exact lease is attached to the facade before callback-bearing startup and
released once only after startup failure, terminal retirement, explicit close,
or context exit. No registry guard spans tree parsing, composition, callbacks,
provider work, persistence, or cleanup.

This lease defines supported in-process public-factory ownership. Direct
internal `NativeSessionTree` use and coordination with another OS process remain
caller responsibilities; D6a does not introduce a cross-process locking format.

Reopen creates a fresh runtime lifetime over the existing durable conversation.
The active leaf's canonical messages, prior compaction summary, retained-user
anchor, and tree identity are reconstructed by the current tree/product-session
owners. The explicitly supplied provider remains authoritative; reopen does not
silently select a provider or thinking level from tree metadata. Historical
agent or extension events are not replayed. New accepted exchanges append to the
same file once, and the new lifetime emits one current `session_start` and one
`session_shutdown` through the existing extension lifecycle.

`ProductSession.close()` does not gain concurrent or replacement semantics. It
remains an idempotent, construction-thread, non-reentrant idle disposal; its
detached snapshot remains readable, and later submit refuses. Startup failure,
extension activation failure, terminal driver exit, explicit close, and context
exit continue through the one native lifetime owner. Its existing output gates,
exact queue settlement, tree locking, and stable cancellation view prevent late
writes or a delayed cancellation from reaching another reopened lifetime.

The reopened tree is full-content private product state. Reopen creates no
workflow-archive record and copies no tree content, path, events, or diagnostics
into metadata-only session summaries. Explicit tree injection remains supported,
and compatibility `run_native`, RPC, terminal resume/fork/clone, provider
construction, and resource/trust policy keep their existing semantics.

The D2 owners are the existing adapter preparation, the small outer facade
and `sdk.py`, the shared cancellation primitive and headless waiter composition.
Use D2a's lifetime without moving its ownership. Never copy prompt/resource
composition into the facade. Update architecture,
harness contracts and release notes with behavior changes. Validate focused
controller/API/event/lifecycle tests, `just check`, `just docs-build` and
`git diff --check`; controller/input changes also need PTY smoke.

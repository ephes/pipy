# Extension runtime

This page maps the current extension runtime's components and lifecycle. The
[architecture overview](architecture.md#extensions) places extensions within the
native product. The [Extension API](extension-api.md) owns the author-facing
surface, including [discovery](extension-api.md#discovery),
[entry points](extension-api.md#entry-point), [hooks](extension-api.md#event-hooks),
[UI](extension-api.md#ui-surface), and
[providers](extension-api.md#providers-and-models).

The exact concurrency contract remains the transactional reload spec's
[R0 current reconciliation](specs/2026-07-25-transactional-extension-reload-rebuild.md#r0-current-reconciliation-2026-07-30).
Its [R7 closeout](specs/2026-07-25-transactional-extension-reload-rebuild.md#r7-closeout-reconciliation-2026-08-02)
links executable evidence. Those sections control the boundary when older ideal
design clauses differ; the dated filename does not make the contract obsolete.
This component map summarizes that boundary without redefining its protocol.

## Component owners

Paths below are relative to `src/pipy_harness/native/`, except the public façade.

| Owner | Responsibility |
| --- | --- |
| `extensions/packages.py` | Discover and inventory local extension/package resources. |
| `extension_loader.py` | Load isolated modules and drive activation awaitables. |
| `extension_types.py` | Define contribution, hook, host-port, and UI value vocabulary. |
| `extensions/contracts.py` | Define activation records and API protocols. |
| `extensions/activation.py` | Own fail-closed activation, guarded registration, and capability-token/host lifecycle. |
| `extensions/collectors.py`, `dispatch.py` | Collect contributions and dispatch registered commands/shortcuts. |
| `extensions/message_routing.py` | Own each generation's message-route authority and retirement. |
| `extensions/custom_payloads.py`, `tool_port.py` | Normalize render payloads and adapt extension tools. |
| `extension_hooks.py` | Dispatch serial lifecycle, input, request, tool, trust, bash, and session-gate hooks. |
| `extension_ui.py` | Supply the deterministic headless UI bridge. |
| `tui.py`, `ui/extension_chrome.py` | Drive concrete live UI and extension chrome. |
| `repl/extension_operations.py` | Resolve published contributions for session operations. |
| `repl/extension_attach.py` | Attach startup/reload candidates through `attach_generation()`. |
| `session_generation.py` | Own the generation reference, immutable projections, delivery gates, and prepared effects. |

`pipy_harness.extensions` is the public import façade. Definitions and behavior
stay with these owners; the façade is not another activation path.

## Discovery, loading, and activation

Discovery inventories resources before activation executes trusted local code.
The loader owns module isolation and awaitable execution; the activation owner
collects and validates contributions with a per-extension fail-closed lifecycle.
Extension-controlled normalization and validation run outside the host guard;
the guarded commit rechecks registration eligibility before changing storage.

Activation hosts seal and freeze contributions before accepted ownership can
be published. Late registration through an ineligible retained host raises
`ExtensionCapabilityError`; sealing alone grants no live message-route authority.
Candidate ownership is one-shot, with explicit abandonment and disposal paths.
The exact registration families and validation precedence belong to the API
and R0 contract rather than this map.

Activation has **no timeout**. When an awaitable needs a private worker because
the caller already has an event loop, the loader joins that worker without a
timeout. Existing abandonment tests do not imply a production timeout policy.
Trusted extension code may perform external effects; failure isolation does not
roll those effects back.

## Published generation and consumers

`SessionGenerationRef` owns the current generation pointer, identity, and one
session mutex. A `SessionExtensionGeneration` contains the runtime, installed
`ExtensionProjection`, and inert chrome commit token. The projection holds parsed
flags, commands, hooks, tools, renderers, provider contributions, queue handles,
and the chrome handle used by that generation.

Projection construction copies mapping families into read-only values and
sequence families into tuples. Custom-message top-level options are copied and
frozen; opaque nested option values and `details` retain shallow semantics.
Mutable activation-host ownership state is separate from the projection.

Installed consumers take one coherent generation snapshot for each operation
or iteration. A missing installed projection fails closed before consuming
outboxes; it cannot silently switch to direct runtime fallback. Held old
snapshots retain their own generation's handles rather than resolving through
the successor. Settings, keybindings, and workspace resources are not fields of
the extension generation and have their own synchronization.

## Startup and reload attachment

`repl/extension_attach.py::attach_generation()` is the shared attachment owner.
`predecessor=None` selects startup; reload supplies the retained session
reference. The callers supply preparation, provider, UI, diagnostic, and delivery
ports rather than implementing a second publication transaction.

Attachment builds the detached projection and installs its candidate route.
Startup constructs the initial reference and publishes capability state under
the session mutex, then completes provider/UI preparation before transferring
candidate ownership. Reload prepares replacement owners and chrome, accepts
candidate host ownership, and publishes only after guarded expected-owner
checks. A failed check leaves the retained generation current and retires the
candidate's route/chrome. Fallible preparation and cleanup remain outside the
session mutex.

Candidate runtime and parsed flags are accepted or rejected together. Rejection
preserves retained title, widgets, listeners, editor registrations, and other
generation-owned chrome; it does not replay the retained `session_start` hook.
Successful attachment delivers staged messages, releases routing, drains the
delivery gate, and performs disposal/presentation through unlocked ports.

Reload as a whole is **not atomic**: settings, keybindings, package roots, and
resources refresh sequentially. Successfully refreshed configuration remains
applied even if the extension candidate rejects. Refusal may run a separate
retained-provider refresh publication section; the two gate sections are not a
continuous gate around configuration refresh. Exact admission and ordering
rules remain in R0.

## Host ports, queues, and effects

Ordinary command, hook, tool, and UI contexts retain their originating
generation. A stale host cannot redirect writes into its successor. Closed
chrome setters become silent no-ops; terminal-input registration returns an
inert disposer. Immediate dialogs, editor/overlay changes, notifications, and
session-scoped sticky UI state are not all transactional generation fields.

The class-A controls `set_active_tools`, `set_thinking_level`, and `set_model`
check generation identity, publication admission, and terminal state at their
in-memory commit. Refused calls return `False` without mutation. Model changes
prepare provider/coding replacements outside the session mutex, recheck expected
owners before committing, and persist defaults or paint only after unlocking.
Persistence failure is fail-soft and does not revert the live selection.

Message sends first stage or reserve under only the host guard. After unlocking,
the routing owner resolves that immutable reservation under the session mutex
without rereading mutable host fields. The guards are not nested. A claim that
won before retirement may finish against detached old-generation storage only;
it cannot affect the successor.

Live closed-check/append, queue detach/drain, and retirement use the exact mutex
shared with the generation reference. Draining takes one snapshot and detaches
both outboxes under that mutex, preventing a cancellation straggler's append
from being erased by copy-then-clear. Callbacks and message sinks run unlocked.
Accepted staged delivery sends users in order, then customs in order, before
FIFO gate drain. Direct durable custom-message tree/render/input delivery is a
separate sink from `custom_outbox`; there is no added retry, ID, or capacity
protocol. Retired storage is not rediscovered through a registry or successor.

## Shutdown and verification

Terminal shutdown closes admission and waits for already accepted effect
leases without holding the session mutex across their work. It detaches the
generation/outboxes and retires chrome through the established lock order,
then performs callbacks, disposal, diagnostics, and paint unlocked. Retained
effectful coding-session calls after terminal close are refused with
`ExtensionCapabilityError`; other retained ports follow their documented
refusal/no-op rules.

Session-mutex sections contain bounded in-memory checks and publication. They
must not perform filesystem/provider I/O, invoke extension callbacks or arbitrary
sinks, wait, paint, or release last references that can run cleanup. Preparation,
post-commit effects, and teardown retain that separation.

The [R7 evidence matrix](specs/2026-07-25-transactional-extension-reload-rebuild.md#r7-closeout-reconciliation-2026-08-02)
names behavioral tests for coherent publication, malformed-flag rejection,
abandonment, cancellation stragglers, teardown, and post-commit failure. Use it
with [R0](specs/2026-07-25-transactional-extension-reload-rebuild.md#r0-current-reconciliation-2026-07-30)
when changing any shared mutable state; this overview is not a replacement
acceptance checklist. Current task selection remains in the [backlog](backlog.md).

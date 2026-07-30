# Transactional extension reload — rebuild plan and concurrency contract

Status: R0-reconciled completion contract; R1–R2 and construction-only R3a are
shipped, and R3b is next. The original Slice 3 text remains historical design
evidence, while the 2026-07-30 table below controls bounded R1–R7 completion.

Date: 2026-07-25. Reconciled against shipped behavior: 2026-07-29. Current R0
clause disposition: 2026-07-30.

This document replaced an abandoned Slice 3 implementation attempt. It records
the ideal ownership and synchronization contract, the bounded sub-slices, stop
conditions, and behavioral scenarios the completed transaction was intended to
demonstrate. The shipped work is a material safety ratchet, but it did not land
every clause below. The current implementation has one generation pointer and
session mutex, runtime-plus-flags candidate rejection, a publication gate,
atomic active-tool/thinking admission, and post-selection default persistence.
R1 has since added the isolated, guarded, sealed/disposed activation host, and
R2 stages rejected candidate chrome/listener requests. R3a adds a standalone
immutable builder for every applicable extension contribution family and pure
composition adapters; R1's mutable `activation_hosts` ownership state is
explicitly excluded. Production startup/reload does not construct or install
that projection. The
implementation still lacks detached reload-effect preparation, projection
publication and consumer adoption, production operation snapshots, generation-
bound stale mutation refusal, and atomic `set_model` admission. R3b/R3c and
R4a–R4c retain those owners.

Read the contract below as the ideal target and historical design evidence, not
as a claim that all S3.0–S3.9 guarantees shipped. The living
[`architecture.md`](../architecture.md) and the dated
[2026-07-29 assessment](../2026-07-29-architecture-quality-assessment.md)
separate landed ratchets from outstanding correctness clauses.

## R0 current reconciliation 2026-07-30

This section is the controlling completion contract for R1–R7. The detailed
2026-07-25 design below remains historical design evidence; where this section
says **formally narrowed**, that decision supersedes the older ideal clause
without rewriting the dated text. Where it says **required**, the older clause
remains mandatory. No R0 edit changes product behavior. Readers entering through
the historical text should use the section-level back-references at
[The concurrency contract](#the-concurrency-contract),
[Bounded sub-slices](#bounded-sub-slices), and
[Behavioral scenario checklist](#behavioral-scenario-checklist); respectively,
they point back to the controlling clause/boundary, R1–R6-bound, and scenario
tables here.

The re-audit used the production owners and focused tests named below. It also
found one current lost-update window not called out by the assessment. An
extension tool closes over its activation `api`; `_ExtensionToolPort.invoke()`
runs that handler on `ToolExecutor`'s `pipy-tool-call` worker, and the bounded
cancel join permits a non-cooperative worker to outlive the returned cancellation
(`test_waiter_keyboard_interrupt_signals_abort_and_returns_cancellation` and
`test_interruption_signals_tool_and_returns_balanced_cancellation`). That worker
can call `_ActivationApi.send_user_message()` / `send_message()` while the
session/RPC-session worker has returned to
`CodingSessionController.select_next_step()`, whose injected
`_CustomEntryRenderer.drain_extension_outboxes()` calls copy-then-clear
`drain_user_messages()` / `drain_custom_messages()`. An append between those two
list operations is erased. This is not the joined `pipy-ext-activate` path. R3a
defines the detached queue-handle value; R3b defines the authoritative staged
activation detach/flush/delivery sequencer, and R3c installs and invokes it.
R4a later synchronizes accepted/live activation-api appends, the named renderer
drain, and queue close under the existing session mutex; it must not detach,
flush, or deliver the staged batch again. Staged activation custom messages are
harvested into `ActivatedExtension.custom_messages`; the R3b/R3c sequence sends
them to `_CustomEntryRenderer.extension_send_message()` after acceptance and
never appends them to `custom_outbox`.

`ExtensionCodingSessionControl` is not another outbox writer. Its completion
callable invokes the active provider directly; entry append, session-name set,
and label set append to `NativeSessionTree`; and its custom-message callable is
`_CustomEntryRenderer.extension_send_message()`, which appends a durable
`custom_message` tree entry, optionally renders/diagnoses it, and may enqueue a
`CodingInputQueue` next-turn/steer/follow-up/prompt effect. It never calls
`_ActivationApi.send_message()`, `drain_custom_messages()`, or writes either
generation outbox. Thus the R4a queue writer set is complete, but these distinct
sinks expose a second current shared-state defect. A retained control may run on
an extension-created thread while the session or RPC thread uses the same tree
and input queue. `NativeSessionTree._append_entry()` protects only the in-memory
append/leaf pair with `_write_lock`; id/parent selection and name/label updates
occur outside that section, and `_write_entry()` runs after unlock, so concurrent
appends can reverse durable JSONL order. `CodingInputQueue` mutates and reads all
of its deques and retained slots without a guard. The existing per-run
`mutation_io_lock` serializes only extension thinking-level mutation today and is
not adopted by either owner.

R5 is therefore split. R5a introduces one run-scoped coding-effect coordinator
backed by the existing `mutation_io_lock` and a condition on that same lock. Each
effectful retained coding-session adapter atomically acquires one exclusive
owner/depth lease unless terminal is closed (same-thread nested calls re-enter),
releases the lock while provider/render/callback work runs, and releases its
lease only after all effects finish. Thus retained effects are serialized
without blocking provider-worker read-only tree callbacks on a held lock. The active
`NativeSessionTree` and `CodingInputQueue` adopt the coordinator's reentrant lock
for every mutable-state reader/writer. Custom-message tree, render, and input
phases retain their existing order; only the tree/input phases take the lock.
Tree append methods hold it across id/parent selection,
all in-memory indexes, and `_write_entry()`, preserving one in-memory/JSONL
order. Terminal atomically closes admission and waits on the condition (which
releases the lock while accepted effects drain), then takes the session mutex
only for terminal generation and generation-queue state. No provider/render work
runs under `mutation_io_lock`, and no provider or filesystem I/O runs under the
session mutex. R5b retains the separately reviewable active-tool/thinking generation-admission work. This
adds one execution slice; the remediation plan contains exactly 27 execution
slices.

The re-audit also corrected a historical premise without weakening the sealing
requirement. `extension_loader._drive_awaitable()` starts
`pipy-ext-activate` only when its caller already has a running event loop, then
uses an unbounded `thread.join()`. Current production therefore has no
activation-timeout path. Before R1, a retained activation `api` could call
`register_*` or `on` after activation and appear to mutate already-harvested
state; extension-created activation threads could also race separate `staged_*`
reads. R1 shipped the required present-day correctness fix and future-timeout
seam: host-internal `_seal_and_freeze()` atomically closes registration, decides
first failure, and snapshots every staged family; rejection or abandonment calls
host-internal `_dispose()`.
All later class-D void, flag, direct-handler, decorator-factory, and retained-
decorator calls raise `ExtensionCapabilityError` without fabricated return
values. Parsed flag application and reads use host callbacks under the same
guard rather than a shared mutable `RegisteredFlag.values` mapping. The frozen
snapshot is authoritative for staged messages: sends after seal while activation
is still pending have no effect, and commit flushes only the frozen user/custom
messages exactly once. Accepted/live send routing after activation commit releases
the candidate guard before list append; R4a still owns the queue-sidecar/session-
mutex conversion for accepted live appends. Production still has no activation
timeout and R1 selected none.

### Assessment residual ownership

Every **Actual gap** in the 2026-07-29 assessment has one bounded owner. The
sub-labels R4a–R4c are parts of R4, so this remains an R1–R6 mapping.

| Assessment Actual gap | Disposition and executable owner |
| --- | --- |
| Candidate activation uses the live host and rejection can clear prior chrome. | **Landed through R1–R2.** R1 replaced and disposes the activation registration host. R2 added guarded candidate chrome/listener sinks, removed the pre-activation live clear, closes rejected sinks without delivery, and reconciles accepted retained chrome only after `SessionExtensionGeneration` publication. `test_invalid_flags_keep_live_title_widgets_and_listeners_without_candidate_paint`, `test_injected_activation_failure_keeps_live_chrome_and_disposes_candidate_sink`, and the focused PTY invalid-flag case pin the result. Complete projection carriage and retired-live binding remain R3/R4c rather than being claimed here. |
| An abandoned activation host is not sealed from later registration. | **Landed in R1.** `_ActivationApi` now owns one guard and the one-way candidate open→sealed→committed→published/disposed transitions plus the accepted-catalog terminal state for every staged family, flag value/failure, and `_activated`; `_seal_and_freeze()` returns the sole complete contribution snapshot without duplicating future live state. Loader abandonment and startup/reload candidate rejection dispose through private host seams, with reload disposal after the publication-gate mutex handoff. Retained late class-D calls raise `ExtensionCapabilityError`, and `RegisteredFlag` no longer aliases a mutable values mapping. Evidence: `tests/test_native_extension_activation_sealing.py` and `test_a_malformed_candidate_flag_retains_the_complete_prior_generation`. |
| `SessionExtensionGeneration` contains runtime plus flags while capability, renderer, lifecycle, provider, menu, and chrome projections publish separately. | **R3a construction landed; R3b/R3c installation and R4a–R4c consumer adoption remain.** `build_extension_projection()` constructs the detached immutable runtime/flag, command/menu/shortcut, hook, tool/capability, renderer, provider, queue-handle, and exact-chrome families. The live generation shape and every legacy consumer remain unchanged. Each equivalence arm stays until the corresponding R4 slice deletes its last legacy source. |
| Production operations read `current` per access rather than one snapshot. | **Required in R4a–R4c.** `SessionGenerationRef.snapshot()` and `test_a_snapshot_does_not_follow_a_later_publication` are landed foundations; `_RunControlState.extension_generation` and the composition adapters still read `current` repeatedly. |
| Class-A mutation ports do not capture or validate `generation_id`. | **Required in R5a–R6.** R5a owns shared terminal-generation support and teardown; R5b owns `set_active_tools` and `set_thinking_level`; R6 owns only `set_model`. |
| `set_model` checks the gate separately from provider construction, state mutation, and persistence. | **Required in R6.** `_ProviderMutationEffects.extension_set_model()` reads `publication_pending` before `apply_model_selection()`, whose current path selects/builds, rebinds coding state, refreshes presentation, and flushes a default in separate steps. R6 makes preparation / in-memory commit / fail-soft persistence three phases. |

The six residuals above are not narrowed. In particular, retained old chrome,
sealed/disposed rejected or abandoned registration, coherent published
projections, one snapshot per operation, generation-bound atomic class-A
admission, and three-phase `set_model` remain mandatory.

### Complete class-A inventory

For this contract, class A is the model-runtime control bundle exposed through
`ExtensionModelRuntimeControl`, not every synchronous command-context effect.
The complete inventory is exactly the expected three port families:

| Extension-facing port | Admission/composition owner | Mutable state owner | Slice |
| --- | --- | --- | --- |
| `_CommandContext.set_active_tools()` | `_ProviderMutationEffects.extension_set_active_tools()` | `NativeToolCapabilities` active selection (`ToolCapabilityState.active_tool_names`) | R5b |
| `_CommandContext.set_thinking_level()` | `_ProviderMutationEffects.extension_set_thinking_level()` | `NativeReplProviderState.thinking_level` | R5b |
| `_CommandContext.set_model()` | `_ProviderMutationEffects.extension_set_model()` | `NativeReplProviderState.selection` plus `CodingSessionState` provider binding/history/usage | R6 |

`extension_types.ExtensionModelRuntimeControl` has exactly those three callable
fields, and `_ProviderMutationEffects.model_runtime_control()` is the sole
production full-bundle constructor. Every production context site is enumerated
here; a context retained by trusted extension code is straggler-reachable even
when its original dispatch was synchronous, because the extension may store it
and invoke it from its own later thread.

| Production context family | Coding-session control | Model-runtime control | Executing thread/task and straggler consequence |
| --- | --- | --- | --- |
| Command and shortcut handlers | Full `ExtensionCodingSessionControl`: completion, entry append, session name/label, custom-message send, plus read views. | All three class-A families. | Session thread (the RPC session's worker is that mode's session thread). The context can be retained; class-A callables therefore need generation binding. A retained context can invoke coding-session writers concurrently during the live run; R5a serializes those effects with all tree/input readers and writers and refuses new effectful calls at terminal state. |
| `input`, `before_agent_start`, `user_bash`, and session-before gates | None; `_CommandContext` receives the empty default bundle. | All three families. | Synchronous session thread; an async return is driven to completion, and a private `pipy-ext-activate` task, when needed, is unboundedly joined. Retention still makes only the same three model callables stale-reachable. |
| `before_provider_request`, `tool_call`, and `tool_result` | `before_provider_request` gets a messages-only snapshot; tool-call/result get none. No mutator callable is present. | `set_active_tools`, `set_thinking_level`, and the same `set_model` field replaced by `_deny_model_mutation`. | Synchronous agent-loop/session thread before/after the provider/tool workers. Retention exposes only the same three model families; denied model mutation remains the `set_model` family, not a fourth port. |
| `before_provider_headers` | Read-only session-tree view only; all completion/entry/name/label/message mutation fields are `None`. | None. | Provider's `pipy-provider-turn` worker through the request header callback. A late/retained context has no coding-session or model mutation callable. |
| Lifecycle hooks (`session_*`, `agent_*`, `turn_*`) | None. | None in production `_ExtensionLifecycleAgentEventAdapter.fire_lifecycle()`. | Synchronous session thread after the corresponding immediate event projection; no class-A or coding-session mutation can straggle through the context. `test_lifecycle_hook_contexts_expose_no_model_runtime_controls` pins the model-control absence. |
| Extension tool handler | None. | `set_active_tools` only. | Cancellable `pipy-tool-call` worker, which may outlive cancellation; this is a direct straggler path for one of the three families. |
| Project-trust hook and activation API | `ProjectTrustContext` has neither control bundle; activation has the separate API. | None. | Trust dispatch is synchronous before the run; activation awaitables are joined. Activation API message queues are class C and contribution registration is class D, not class A. |

Thus hook aliases do not enlarge the class-A inventory. The coding-session
callables are not extra model-runtime ports and do not write generation outboxes.
Today a retained context remains fully callable after `run()`: completion can
start a provider request; append/name/label/custom-message operations mutate the
in-memory session tree and, when persistence is enabled, append corresponding
`custom`, `session_info`, `label`, or `custom_message` JSONL entries;
custom-message options can also mutate `CodingInputQueue` and rendering can run
after terminal close. During a live run those same calls can race session/RPC
readers and writers. R5a must serialize each complete retained coding-session
effect with the shared tree/input owners and make every retained effectful
coding-session callable refuse terminal use with the established
`ExtensionCapabilityError`. A call admitted before terminal owns the exclusive/reentrant effect lease until
all of its provider/tree/JSONL/render/input effects finish; teardown
waits for it without holding the coordinator lock. The read-only name getter
and session-manager views remain callable against a coherent final tree and take the same tree guard,
but cannot mutate it. R5b makes terminal or stale `set_active_tools` and
`set_thinking_level` return `False`; R6 applies terminal/stale `False` to
`set_model`. Ordinary stale-generation admission remains exactly those three
class-A families. The class-A inventory is still three families, while the R5a
split makes the program exactly 27 slices. R5a requires an extension-API update
and a definite changelog fix entry for preventing concurrent/reordered and
post-run provider/session-tree/persisted-entry/input-queue effects. A future
context that adds another mutation callable must classify and bind it in that
same change.

### Clause-disposition table

“Landed” means current code and focused evidence satisfy the bounded clause.
“Required” names the implementation slice. “Formally narrowed” includes the
reachability and no-lost-update/torn-read basis; these are the only narrowings.

| ID | Contract clause | Current disposition and concrete evidence |
| --- | --- | --- |
| C01 | One per-run session mutex guards the generation pointer and publication gate. | **Landed for live owners; R3a validates detached candidate inputs.** `NativeToolReplSession.run()` still passes one `threading.RLock` to the existing live owners. The unused R3a builder additionally refuses a queue/reference mutex mismatch before constructing a projection. It does not alter `SessionGenerationRef` or any runtime caller. |
| C02 | Publication is a non-fallible pointer operation and does not release the displaced generation under the mutex. | **Landed for the existing pointer; R3c/R4c still own complete projection publication.** R3a does not change `SessionGenerationRef.publish()` or call it. It validates the detached queue/reference input identities before projection construction; no post-construction mutex branch remains. Later slices retain host transfer and publication. |
| C03 | Every reader and writer of guarded state takes the same guard; no check-then-mutate occurs, and no provider/filesystem I/O runs under the session mutex. | **R1 candidate-host and R2 candidate-chrome sink portions landed; remaining work required in R4a/R4c/R5a/R5b/R6.** Coding-state and capability pointer access already use the session mutex (`tests/test_native_coding_state.py`, `tests/test_native_tool_capabilities.py`), and R1 now guards every activation-host reader/writer plus its atomic failure decision, seal/freeze, and disposal. R2 owns the sink-local closed-check/write/attach/close guard and the narrow replacement-`session_start` candidate routing seam only; it does **not** bind ordinary or retained invocations to a generation. R4c owns that invocation binding, live-handle selection, and retired-live close. Generation-outbox append/drain remains unsynchronized, `NativeReplProviderState` has bare selection/thinking fields, and `set_model` admission is split. In addition, `NativeSessionTree` releases `_write_lock` before `_write_entry()` and `CodingInputQueue` is wholly unguarded while a retained coding-session control is concurrently reachable. R4a owns the session-mutex generation queue; R5a makes one coordinator own retained-effect admission/in-flight state, active-tree mutation/order, and every coding-input reader/writer. Provider/render work runs outside `mutation_io_lock`; durable tree I/O deliberately holds that lock but remains outside the session mutex. The guarded-field table below is exhaustive for the target. |
| C04 | A generation has one immutable contribution/configuration projection. | **R3a detached construction landed; R3c installation and R4 consumer moves remain.** `ExtensionProjection` is standalone rather than a field of live `SessionExtensionGeneration`. Its builder copies/freezes every selected family, prepares private-flag tool ports and candidate capability state, and validates before return. Production still uses `_ExtensionRuntime` plus mutable flag values and separately publishes every legacy adapter. |
| C05 | Operations take one generation snapshot at start and keep it for their duration. | **Required in R4a–R4c.** `SessionGenerationRef.snapshot()` and its held-value unit test are landed, but its docstring and `_RunControlState.extension_generation` record production adoption as pending. |
| C06 | Class-A contexts capture their creating generation; liveness/gate check and in-memory mutation are one critical section and fail closed. | **Required in R5b–R6; not narrowed.** Current contexts close over unbound `_ProviderMutationEffects` methods. R5b owns active-tool/thinking; R6 owns model. |
| C07 | The publication gate opens before derived live selection is sampled, remains open through publication, closes on every failure, and is closed before replacement lifecycle hooks. | **Landed as a gate foundation; required in R4c/R5b/R6 for final atomic use.** `_ReloadCommandEffects.execute()` wraps all current phases in `generation_ref.publishing()` and fires `session_start` after it; `test_publication_gate_opens_and_closes_around_a_publication`, `test_the_gate_closes_even_when_candidate_preparation_raises`, and `test_the_gate_stays_open_across_the_pointer_swap` pin the current behavior. |
| C08 | Candidate contribution registration is isolated, guarded, one-way sealed before freeze, and sealed/disposed on rejection or abandonment. | **Landed in R1.** `_ActivationApi._seal_and_freeze()` takes the candidate guard once for commands, shortcuts, hooks, tools, providers/unregistrations, flags and guarded values, message/entry renderers, user/custom messages, first failure, `_activated`, and sealed state. `_dispose()` clears and terminally refuses rejected/abandoned hosts. The host lifecycle is authoritative: batch publication takes all host guards, validates every host, and only then applies committed→published to all, with no wrapper state or lock. An open/unsealed sibling refuses the complete set and leaves it disposable. A lock-free optional runtime holder covers the pre-runtime reload seam. Cleanup returns structured disposed/skipped-published/inaccessible counts through the single activation-cleanup reporter and never uses `warnings.warn`: startup/reload forward their existing sinks, and provider-only catalog harvest requires its caller's sink before terminally finalizing accepted hosts after immutable provider/unregistration outputs detach. Catalog finalization clears staging/outboxes and refuses registration, sends, and publication while preserving guarded registration-time default flag values for detached provider factories that captured `api` (the catalog helper does not parse/apply CLI tokens); a refused non-published transition disposes under the acquired host guard, a published refusal is counted and left live, and guard inaccessibility/failure is counted separately. Rejected/abandoned disposal still clears flags. Accepted `str` subclasses—including `StrEnum`, default-stringifying `(str, Enum)` values, and subclasses overriding `__str__`—are coerced outside the guard from their underlying value to exact plain strings without invoking the override; invalid provider unregistration records and raises `invalid_provider`. The shared typed staging seam preserves pre-R1 reason order for ordinary validation per family: command/tool/flag availability before remaining values; shortcut key shape and handler callability before normalized reserved/duplicate checks; provider factory/models/default/OAuth before duplicate; and message/entry renderer callability before duplicate. Unexpected extension-controlled normalization/copy exceptions instead record the first bounded family-invalid reason and type-only diagnostic even when extension code catches the raised error; exact pre-R1 reason behavior is not retained for that hostile case. Late void, flag, direct-handler, decorator-factory, and retained-decorator class-D calls raise `ExtensionCapabilityError`; no inert return is fabricated. Runtime sends release the host guard before list append; loader/reload rejection seams dispose, and the catalog seam uses its distinct accepted terminal transition, without adding a timeout. Recursive inventory pins `activate_extension_batch`, `activate_extensions`, `_compose_extension_runtime`, `_activate_workspace_extensions`, provider catalog harvest, the cleanup-reporting seams, and every production startup/reload caller; finalization forwards the correct diagnostic sink and pending pre-trust batches finalize or abandon once. The R1 concurrency and malformed-reload tests pin these facts. |
| C09 | Rejected activation keeps all previously live generation chrome; accepted removal clears it only after commit. | **R2 candidate boundary landed; R4c still owns coherent generation publication.** Reload no longer clears before activation/flag validation. A candidate sink closes without paint on rejection, and the retained generation's `session_start` is not re-fired; successful removal reconciles an empty accepted snapshot once after the generation pointer commit. Acceptance/reconcile failure retains and restores old chrome before candidate cleanup; if old restoration fails, bounded recovery retries and transfers the coherent candidate rather than closing a now-live sink. Focused captured and PTY tests cover repeated malformed-flag rejection with stable listener/provider/editor identity, injected activation/factory/reconcile failure, exact cleanup, and accepted removal. |
| C10 | Generation chrome writes target their owning sidecar; only live chrome is reconciled/rendered; rejected/retired sidecars close. | **R2 sink/reconciliation foundation landed; R3/R4c still required.** `_LiveExtensionUiDriver` routes retained header/footer/widgets/title/indicator, terminal-input, autocomplete, editor-component, and hidden-thinking-label writes through one guarded sidecar. Only an accepted replacement's `session_start` dispatch receives a candidate-bound driver, exactly once, proving production candidate writes stage without painting. Attach keeps delivery unpublished through snapshot reconciliation, queues concurrent writes, drains each exactly once, then exposes live delivery. A separate short owner-selection guard records handoff/lease transitions and queues racing retained writes until success or recovery chooses their one sink. Driver ownership swaps only after success; failure restores old chrome where possible or retries the candidate to leave it live/coherent, with bounded diagnostics and one close owner. Sink delivery, reconcile/paint, extension factories/callbacks, session-mutex acquisition, and disposal run outside both guards. Retired ownership transfers before cleanup, so `KeyboardInterrupt`/`SystemExit` propagate without closing the live candidate. R3a constructs the exact detached handle, R3c must install it, and R4c must bind all ordinary operations to their originating generation and close retired-live handles. |
| C11 | Status rows and working message/visibility are generation chrome. | **Formally narrowed.** They remain session-scoped sticky product state, exactly as current `ExtensionChromeState.retire_generation()` and `test_clear_extension_chrome_retires_generation_state_and_keeps_sticky_values` specify. Clear/reload retains them; their TUI readers/writers use the TUI paint lock, so the narrowed owner has no generation torn read. This does not narrow preservation of header/footer/widgets/title/listeners on rejection. |
| C12 | Imperative dialogs, editor text/paste, custom overlays, tools-expanded, and process theme changes are frozen chrome projections. | **Formally narrowed.** `_LiveExtensionUiDriver` executes these as immediate operation effects; they are not retained contribution maps and have no old/new projection to tear. TUI mutation/painting is serialized by `_paint_lock`; process theme is already explicitly non-transactional. R2 owns the landed retained chrome/listener/autocomplete/editor-component/hidden-thinking-label sink; R4c still owns complete generation binding and retired-live closure. |
| C13 | User/custom messages live in generation-owned queue sidecars; append and live drain synchronize, only the live generation drains, and rejected/retired sidecars close. | **R3a detached handles landed; authoritative staged detach/flush/delivery preparation is R3b, installation/invocation is R3c, and later live append/drain/close remains R4a with terminal invocation in R5a.** Runtime outbox identity and live late binding are pinned by `test_generation_preserves_outbox_identity_and_ui_adapter_late_binding`; the cancellable extension-tool path makes the direct-list copy/clear lost update reachable as described above. R3a's unused handles retain exact candidate outbox storage and queue/reference mutex identity but define no append, drain, close, delivery, or production hook. R3b/R3c detach the one frozen staged batch and deliver it once before releasing queued candidate/live sends. R4a then converts accepted/live `_ActivationApi.send_user_message()` and `send_message()`/`sendMessage()` appends plus `_CustomEntryRenderer.drain_extension_outboxes()` and queue rejection/retirement close: under the session mutex each live append performs closed-check+append atomically, each live drain atomically detaches the contents, and close is serialized with both. R4a must not re-read, re-flush, or redeliver the staged activation batch. `ExtensionCodingSessionControl` completion/entry/name/label/custom-message callables target accepted runtime/provider/tree/rendering/`CodingInputQueue` sinks, not these outboxes. On a closed queue, extension-facing activation send methods silently no-op and retain their existing `None` return; they raise no exception and emit no diagnostic. R5a's run-finally terminal transition invokes the already-built queue close as part of the same session-mutex terminal detach. |
| C14 | Queue delivery uses session-scoped ids, acknowledged cursors, retry/idempotence protocol, atomic cutoffs, compaction, and pending-capacity diagnostics. | **Formally narrowed.** These are not current product semantics and no assessment residual requires inventing them. R4a instead preserves ordered copy-and-clear delivery while making append versus detach atomic under the session mutex and closes non-live sidecars. Closing a retired handle does **not** change observable delivery: `test_generation_preserves_outbox_identity_and_ui_adapter_late_binding` proves the live adapter switches to the successor outbox, then an `old-after-swap` append remains only in `first_outbox` while the live adapter is empty. Current semantics already never drain that retired list; close changes stale accumulation/refusal only, and silent `None` preserves the send API shape. Delivery remains a synchronous operation effect: a delivery failure is diagnosed/fail-soft according to its existing sink, not retried by a new protocol. Trusted extension code can already generate work without a product quota, so R0 does not add a new queue-capacity behavior. R4a updates `docs/extension-api.md` with the closed-handle no-op contract. Retired-handle close needs no separate changelog text; R4a's user-visible C13 fix for a live append erased by drain does require a fix entry. |
| C15 | Notifications are a generation-owned queue sidecar. | **Formally narrowed.** `_extension_notify` sanitizes and immediately delegates to `_emit_diagnostic`; interactive notices/paint are TUI-owned and captured diagnostics are stream effects. There is no notification queue, delivered cursor, or separately published notification projection to tear. Stale explicit notification remains an allowed extension side effect, like trusted extension external effects; R4a does not synthesize a new delivery protocol. |
| C16 | Settings and keybindings frozen values publish inside the extension-generation commit and are pinned in every operation snapshot. | **Formally narrowed; the selected settings path is omit.** R3 contains **no settings projection** and R4a does **not consume settings from the generation snapshot**. `SettingsManager`/`KeybindingsManager` retain their own immutable state values and synchronization (`test_a_retained_settings_value_does_not_follow_a_later_reload`, `test_keybindings_state_is_immutable_and_published_wholesale`). Product reload applies them before extension activation and keeps them on candidate rejection, preserving the shipped behavior documented in `architecture.md` and tested by `test_reload_rereads_edited_settings_without_provider_turn` / `test_reload_malformed_settings_keeps_prior_and_warns`. The source-wide reachability matrix below records the external manager surface and the one provider-worker `project_trusted` read instead of relying on a false no-worker premise. R4a must replace that late manager reach with a request-local copied boolean, not a generation settings projection. |
| C17 | Package roots and workspace resources publish in the generation commit and are pinned in operation snapshots. | **Formally narrowed.** `_RunControlState.package_roots` and `workspace_resources` remain session-thread-owned configuration. `_reload_configuration_and_resources()` assigns them before candidate activation and keeps them on extension rejection. Their production readers (`_activate_workspace_extensions`, menu composition, `dispatch_resource_command`, and TUI input/menu setup) run on that mode's session thread. RPC's main reader/dispatcher and bash workers, provider/tool workers, event/capture sinks, and extension contexts receive neither `_RunControlState` object. CLI/adapter preparation builds separate pre-run local resource values, not aliases of the run-control pair. Therefore publication cannot interleave with a supported read, no update is lost, and R3/R4a omit resource fields. |
| C18 | Candidate settings/resources remain old on extension rejection and new settings/resources cannot coexist with old extension commands. | **Formally narrowed.** The shipped reload is a configuration refresh followed by a separately rejectable extension-generation refresh. New settings/keybindings/resources intentionally remain after an extension candidate fails. `/reload`, settings-dialog writes, command/resource dispatch, TUI keybinding reads, and resource/menu composition are serial on the session thread. RPC's main thread can mutate provider thinking state but has no settings/keybinding/resource reference; its session worker runs the same serial loop. The provider-worker trust exception is a copied run-local boolean after R4a and cannot observe a partially assigned resource pair. After rejection, the next operation observes the deliberate new-configuration/old-extension boundary. This is not the assessment's observed chrome failure and does not relabel it. |
| C19 | Frozen projections and successor state share no mutable contribution mapping/list. | **Landed for R3a detached values; installed successor proof remains R3c/R4.** Every projection-owned mapping is an unconditional read-only copy and sequence families are tuples. For custom messages this guarantee applies to the top-level `options` mapping only: opaque nested option values and `details` are not recursively transformed and may retain caller-defined aliases. Unit tests prove the bounded source/successor no-alias behavior and independent private tool-port flag dictionaries; only explicit unconsumed queue storage, opaque payload objects, and exact external R2 chrome handles remain. |
| C20 | Active provider, thinking level, and active tools are generation-scoped mutable selection, rebased without losing a mutation accepted before cutover. | **R3a builds only detached candidate capability state; R3c/R4c/R5b/R6 remain.** `NativeToolCapabilities.publish()` and every live selection owner are unchanged. |
| C21 | Provider catalog contribution and coding provider binding publish coherently; refresh preserves history/usage, fallback replaces history/usage, and compaction survives. | **R3a builds only detached provider contributions; R3b/R3c/R4b/R4c and R6 retain preparation/publication.** Current behavior is characterized by `test_reload_rebinds_active_extension_provider_factory`, `test_reload_falls_back_when_shadowing_extension_provider_is_removed`, and `test_rebind_clears_history_and_usage_but_preserves_compaction_suffix`, but publication still spans separate owners. |
| C22 | Coding provider binding, canonical history, usage accumulator, and compaction state use the session mutex on every reader/writer. | **Landed.** `CodingSessionState` guards binding, messages, usage and compaction APIs; blocking-reader/writer tests in `tests/test_native_coding_state.py` cover rebind, snapshots, append, and compaction metadata. R6 must preserve this owner rather than add a second lock. |
| C23 | Active-tool capability state uses immutable candidate values and the session mutex. | **R3a detached candidate build landed; R3c/R4b/R5b integration remains.** The pure builder creates ports and validates the returned `ToolCapabilityState` against them without publishing it. Existing live preparation/publication and context admission are unchanged. |
| C24 | Renderer selection remains pinned to the tool call that advertised it. | **Pinning remains landed; R3a builds equivalent detached maps and R4b adopts them.** No renderer consumer changed. |
| C25 | Runtime plus parsed flags reject as one candidate. | **Landed for current runtime/flags; R3a adds only failure-isolated detached construction.** `_reload_extension_generation()` and its observable order are unchanged. Builder-step failures return no projection and cannot change a live reference or legacy adapter; R3c later integrates that preparation. |
| C26 | Teardown invalidates class-A and coding-session mutation liveness and closes queue/chrome sidecars for a worker that outlives `run()`. | **Required in R4a/R4c/R5a/R5b/R6.** Current `CodingSessionController.run_loop()` finally clears live TUI chrome, but `SessionGenerationRef` has no terminal no-live value, runtime outboxes have no closed state, and retained command contexts remain callable. Today those contexts can race live tree/input readers and writers, reverse JSONL order, start completion, append durable entries, alter name/labels, render/diagnose custom messages, and enqueue coding input after `run()`. R4a/R4c build queue/chrome close operations. R5a owns run-finally wiring and the coding-effect coordinator. It preserves settle then `session_shutdown`; a nested teardown `finally` atomically closes admission under `mutation_io_lock`, then waits on that lock's condition for every admitted lease (the wait releases the lock). Once quiescent, it briefly takes the session mutex while holding `mutation_io_lock` to invalidate/detach the generation and close/detach its generation queue. No I/O occurs under the session mutex. After both release, chrome closes under its sink guard and paint/disposal follows. Calls admitted before terminal finish in coordinator order; later effectful coding-session calls raise `ExtensionCapabilityError`. R5b makes active-tool/thinking return `False`; R6 does so for `set_model`. The R5a API/changelog update is required. |
| C27 | `set_model` is fallible preparation outside the mutex, non-fallible selection/coding commit under it, then fail-soft presentation/default persistence. | **Required in R6; not narrowed.** Current default persistence tests (`test_defaults_persistence_creates_the_file_when_none_exists`, `test_defaults_persistence_failure_reports_without_claiming_rollback`, `test_a_concurrent_overwrite_never_leaves_a_torn_defaults_file`) remain acceptance evidence, but admission and provider construction are not atomic today. |
| C28 | Process-global theme reflection and persisted defaults/trust are post-commit, not rollback participants. | **Landed policy, with settings-generation semantics narrowed by C16–C18.** `_refresh_presentation_and_persistence()` updates `PIPY_THEME` and implicit trust after current publication steps; model defaults flush after selection. R6 keeps persistence outside the mutex. No changelog applies to this R0 decision. |
| C29 | Trusted extension import/activation external effects are outside pipy's in-process transaction. | **Landed policy.** `docs/extension-api.md` documents the trusted local-code boundary and activation already stages only pipy-owned contributions. R1 does not claim to undo extension filesystem/network/process effects. |
| C30 | No rollback framework, revision-counter transaction, uninitialized concrete subclass, or manager hand-copy is introduced. | **Landed design constraint and required throughout R1–R7.** Existing settings epochs are local stale-candidate protection, not a distributed reload transaction. R1–R6 use only the named candidate/sink guards, the existing session mutex, and R5a's existing per-run `mutation_io_lock` promoted into the one coding-effect coordinator. |

### Transactional boundary decisions and reachability proof

**Settings path selected: formally narrowed / omit.** The proof does not depend
on all manager access being session-thread-only. The complete supported-surface
matrix is:

| Surface | Settings/keybindings reach | Package/resource reach | Consequence |
| --- | --- | --- | --- |
| Interactive/print/json session loop and RPC session worker | Owns the managers; `/reload`, settings writes/dialogs, keybinding reads, dispatch, and menu/resource composition are serial operations on that mode's session thread. | Owns `_RunControlState.package_roots` / `workspace_resources`; all reads/writes are on that same thread. | Successful configuration reload remains sequentially visible on candidate rejection, never torn. |
| Provider turn worker | `dispatch_extension_provider_headers()` currently reaches `SettingsManager.project_trusted`; this is the one production detached read. It does not read `_state`, and trust is fixed during run assembly. R4a must capture the boolean when the request/header callback snapshot is created so a late provider callback has no manager reach. | None. | Honest exception; it requires an operation-local scalar copy, not a generation settings projection. |
| Tool worker | `_ExtensionToolPort` receives a copied `project_trusted` boolean and copied flags; no manager/keybinding object. | None. | A cancellable/late tool cannot read configuration owners. |
| RPC main reader/dispatcher and RPC bash workers | Source-wide grep of `native/automation` finds no `SettingsManager`, `KeybindingsManager`, `package_roots`, or `workspace_resources`. RPC does directly mutate provider thinking state, which is separately assigned to R5b. | None. | Automation is not an unlisted configuration reader/writer. |
| CLI package/config management and native adapter preparation | They create/read their own manager and build separate local package/resource values before a run. They do not receive an active run's `_RunControlState` pair. | Separate pre-run values only. | These are external owners, not aliases participating in extension-generation publication. |
| Caller retaining an injected manager | This is a real external reader/writer surface. `SettingsManager._state` APIs use `_state_lock`; file mutation/reload uses `_io_lock` plus epoch refusal (`test_publish_refuses_a_candidate_superseded_by_a_write`). Keybindings publishes one frozen state under its manager lock. Direct active-run mutation of the plain `project_trusted` attribute is not a supported production path; production sets it during assembly/trust handling. | No API exposes the internally built run-control resource pair. | Independent manager concurrency is preserved rather than folded into extension generation; the contract does not claim external readers do not exist. |
| Extension contexts, event/capture sinks, and TUI paint callbacks | Contexts receive copied booleans/snapshots; event/capture sinks receive events; class-B UI callbacks receive UI drivers, not managers. The TUI input owner alone has keybindings. | None outside the session/TUI owner. | No additional detached configuration path. |

`SettingsManager`'s immutable `_state` pointer is protected on every API
read/write by `_state_lock`, while `_io_lock` and `source_epoch` prevent a stale
reload candidate from overwriting a later write. Keybindings likewise publishes
one frozen value. There is no multi-field read through a shared mutable mapping
or lost-update reason to put either value in the extension generation. R3 omits
both; R4a consumes neither from its generation snapshot and removes the one
provider-worker manager reach by copying `project_trusted` at request-snapshot
time.

**Package roots and workspace resources: formally narrowed.** Source-wide
production references to `_RunControlState.package_roots` and
`workspace_resources` are the reload builder, extension activation, menu/TUI
composition, and `dispatch_resource_command`; all execute on the session thread.
Automation transports, provider/tool workers, extension contexts, and event
sinks do not receive either object. CLI and adapter discovery use separately
constructed pre-run locals. The session cannot dispatch another command while
`/reload` executes, so retaining a successful refresh when extension activation
rejects is sequentially observable but not torn and matches current documented
behavior.

**Queue sidecars: retained transactionally, protocol narrowed.** User/custom
outbox identity remains generation-owned. One queue state has one guard: under
the session mutex, append performs its closed check and append in the same
critical section, drain atomically detaches the live contents, and rejection,
retirement, or terminal teardown marks closed and detaches any contents. No
append may check outside that section. Candidate-host staged messages come only
from the authoritative R1 frozen snapshot; post-seal pending sends are silent
no-ops. R3b/R3c release the host guard before the authoritative frozen staged
batch is detached and delivered, then release their ordered gate so
accepted/live sends follow it. R4a subsequently converts only live append,
detach/drain, and close to session-mutex synchronization; it does not
reimplement the staged flush. Sink delivery and detached-value release happen
after unlock. Every extension-facing
activation send method already returns `None`; a send against a closed sidecar
therefore silently no-ops and returns `None`, with no exception or diagnostic.
Closing a retired queue only refuses accumulation that current live late binding
already makes undeliverable, so that close does not change observable delivery.
`ExtensionCodingSessionControl.send_message()` is deliberately absent from this
writer set: it appends a durable tree entry and can enqueue `CodingInputQueue`,
not a generation outbox. R4a updates the extension API with this fail-closed
shape; only its separate live append-versus-drain lost-update fix gets changelog
text. R0 does not add message ids, acknowledged cursors, retry/dedup, cutoff,
compaction, or capacity semantics. Notifications remain immediate sanitized
diagnostics, not a queue.

**Chrome sidecars: retained transactionally.** Header, footer, widgets, title,
working-indicator frames/interval, terminal-input listener registrations,
autocomplete providers, editor-component and hidden-thinking-label registration,
and their callback/disposer identities belong to a generation-owned sidecar. One
chrome state has one sink-local guard: every class-B write performs its closed
check and write under that guard, and candidate rejection, retirement, or
terminal teardown marks it closed and snapshots/detaches cleanup work under the
same guard. A write to a closed sidecar is an extension-visible silent no-op:
`ctx.ui` setters/registration methods keep their existing `None` return and emit
no exception or diagnostic, while `on_terminal_input()` preserves its callable
return shape by returning an inert disposer. R2 records this in
`docs/extension-api.md`; its changelog fix entry covers late writes to rejected
candidate chrome, R4c extends the entry when retired-live handles close, and R5a
reuses the same no-op contract at terminal teardown. R2 stages and closes
candidate sinks. R4c snapshots the published or retired handle under the session
mutex, releases it, then takes only the relevant sink guard for
reconciliation/close. Paint, callbacks, disposer execution, and release happen
after both guards. The TUI paint lock remains the terminal-effect lock and is
never nested with the session mutex or sink guard. Session-scoped status rows and
working message/visibility remain sticky by C11. Dialogs, editor text/paste,
custom overlays, tools-expanded, and theme selection are immediate operation
effects by C12, not retained chrome projection fields.

### Guarded fields, readers, and writers

This is the exhaustive target guarded set. “Reader/writer family” names the
public owner methods through which call sites must pass; direct field access is
not permitted after the owning slice.

| Owner and guarded fields | Readers | Writers | Owning slice/status |
| --- | --- | --- | --- |
| R1 candidate `_ActivationApi`: `_staged` commands, `_staged_shortcuts`, `_hooks`, `_staged_tools`, `_staged_providers`, `_staged_unregistered`, `_staged_flags`, `_flag_values`, `_staged_message_renderers`, `_staged_entry_renderers`, `_failure`, `_staged_messages`, `_staged_custom_messages`, the authoritative frozen contribution snapshot, `_activated`, the authenticated publication marker, the one-way candidate open→sealed→committed→published/disposed transitions, and accepted-catalog terminal state | `_seal_and_freeze()` performs the atomic failure decision and complete snapshot that replaced all separate staged harvest reads; `get_flag()`/`RegisteredFlag.get_value()` use guarded callbacks; normalized duplicate checks and send routing use the same guard | every guarded registration commit through one typed staging seam, staged send, first-failure recording, parsed flag-value application, successful activation/seal, host-internal commit, catalog finalization, and `_dispose()`; extension-controlled validation/normalization runs before the guarded commit | **Landed in R1.** All readers/writers take the candidate-host guard; extension callbacks/coercions run unlocked; late class-D calls raise at this boundary; post-seal pending sends silently do nothing; accepted runtime send after activation commit releases the guard before R4a's later session section |
| Run-scoped coding-effect coordinator: terminal admission state, active owner/depth plus condition, and the reentrant `mutation_io_lock` shared with active tree/input owners | every effectful `ExtensionCodingSessionControl` adapter atomically checks terminal and acquires the exclusive/reentrant lease; run-finally closes admission and condition-waits for the owner to clear; tree/input owner methods below take the lock | `_SessionCollaborators.extension_complete`, `extension_set_session_name`, and `extension_set_label`; `_CustomEntryRenderer.extension_append_entry` and `extension_send_message`; terminal close; R5b thinking uses the same shared-state lock | R5a promotes the existing per-run lock into one coordinator. Provider/render/callback work runs unlocked but retains a lease; tree/input phases take the shared lock. Accepted calls finish before terminal; later calls raise `ExtensionCapabilityError` |
| `_RunControlState.session_tree` active-tree pointer | session command/effect adapters and coding-session writer adapters resolve the active tree only while holding the coding-effect coordinator | `/new`, `/resume`, `/fork`, `/clone`, and `/import` rebinds | R5a; pointer check/use or rebind is one coordinator section, so a retained writer cannot target through a concurrently changing pointer |
| Active `NativeSessionTree`: `entries`, `by_id`, `labels_by_id`, `label_timestamps_by_id`, `leaf_id`, `_name`, and durable JSONL append order | all query/snapshot APIs, read-only extension session-manager/name views, RPC `get_entries`/`get_tree`/fork-message readers, and session-thread context/tree/render readers | every append family, including id/parent selection, entries/index/leaf/name/label updates and `_write_entry()`; branch/reset/set/summary navigation; active-session rename/label writers | R5a; the tree adopts the coordinator's reentrant `mutation_io_lock` instead of an independent partial `_write_lock`. Every API reader/writer takes it; each append holds it through in-memory commit and durable write, so memory and JSONL have one order. This is the coordinator's only intentional I/O-under-lock path |
| `CodingInputQueue`: all extension deques, seeds, retained wake/agent/fresh input, pending local command, next-turn context, and related check/use state | `has_pending_local_command`, `take_next`, `take_next_for_agent_loop`, `classify_external_wake`, and `take_next_turn_context`, including their private poll/take/check helpers | every `enqueue_*`, `defer_local_command`, `retain_agent_input`, wake-retain path, `clear_extension_inputs`, and take/clear path | R5a; the queue adopts the same reentrant `mutation_io_lock`; every public mutable-state reader/writer encloses its complete check-then-act path, and helpers are called only under that owner |
| `SessionGenerationRef`: live generation pointer, `generation_id`, `publication_pending`, terminal/no-live state | `snapshot()`; class-A admission; live-sidecar reconciliation snapshots | `publish()`; `publishing()` open/close; run-finally invalidation | Pointer/gate landed; complete pointer R4c; terminal state R5a |
| Detached immutable `ExtensionProjection` (not yet installed in `SessionExtensionGeneration`) | No production reader until R3c/R4 | R3a pure builder only | R3a construction; R3c installation; R4a–R4c consumers |
| Generation queue sidecar: user/custom outbox contents and closed state, all guarded by the session mutex | R4a live drain through one operation snapshot; closed check in the same drain section | accepted/live activation send methods perform closed-check+append atomically; R4a close on rejection/retirement; R5a invokes close on teardown | R3a detached storage/mutex handle; R3b/R3c authoritative staged detach/flush/delivery and gate installation; R4a converts later live append/drain/close only; R5a terminal invocation. Detached delivery releases the session mutex before entering `CodingInputQueue`, forbidding `session mutex → mutation_io_lock` |
| Generation chrome sidecar: retained chrome/listener/autocomplete/editor-component values and closed state, guarded by that sidecar's sink-local guard | R4c immutable reconciliation snapshot after session unlock | retained class-B setters/registrations; R2 candidate close; R4c retired close; R5a terminal close | R2 sink; R3a detached exact handle; R3c installation; R4c binding; R5a terminal invocation; paint/disposal after unlock |
| `NativeToolCapabilities`: `ToolCapabilityState` pointer including `active_tool_names` | state and request/execution snapshots | candidate `publish()`; class-A `set_active_tools()` | Base locking landed; R3a detached build; R3c/R4b integration; R5b admission |
| Guarded provider selection owner: `NativeReplProviderState.selection`, `thinking_level`, and pending-default payload/state | selection/provider/thinking/request/reload/persistence-payload reads | selection/auth/reset/startup/cycle/RPC/reload and class-A thinking/model commits | R5b thinking; R6 model/provider/default payload. R5b keeps `mutation_io_lock → session mutex` for thinking commit/order and releases the session mutex before durable tree I/O |
| `CodingSessionState`: `_binding`, `_messages`, `_usage_accumulator`, `_compaction_suffix`, `_compaction_count`, `_compaction_dropped_group_count` | provider/binding/name/model/messages/usage/compaction properties and result/usage snapshots | begin/refresh/rebind/unavailable, history append/mirror/clear/rebuild, usage absorb, compaction | Landed; R6 uses the same commit section |

`SettingsManager._state`, `KeybindingsManager` frozen state, and their manager-
local epochs remain protected by their existing locks but are outside the
extension-generation transaction under C16. Tree/prefill/turn flags other than
the active-tree pointer, package roots, and workspace resources remain session-
thread-owned. Sticky TUI state and immediate UI effects remain under the TUI
paint lock. `NativeSessionTree`, its active pointer, and `CodingInputQueue` are
not session-thread-only: the rows above cover their extension-created-thread,
provider/RPC-reader, agent-loop, and session-thread access. If a future detachable
context reaches another field, that change must add it to this table.

The class-A owner must remove direct `NativeReplProviderState.selection` and
`thinking_level` accesses from production paths it converts; otherwise taking
the mutex only in the extension port would remain one-sided. The current direct
writers include `_ProviderMutationEffects` restore/startup normalization,
`NativeToolReplSession._cycle_thinking_level`, and
`automation.rpc.NativeRpcServer._set_thinking_level`, in addition to
`NativeReplProviderState`'s own selection/auth/reset and pending-default
methods. R6 must detach the persistence payload under the mutex and perform the
file write after unlock; it may not read/clear `pending_default` around I/O as
two unguarded operations. R5b/R6 tests must enumerate the converted selection
reader/writer methods and fail if a new direct access appears. R5a inventory
tests do the same for the active-tree pointer, every mutable `NativeSessionTree`
API, every `CodingInputQueue` API, and all effectful coding-session adapters.
Provider construction, persistence, session-tree writes, footer refreshes,
diagnostics, callbacks, paint, and displaced-value destruction remain outside
the session-mutex section; R5a's exclusive/reentrant lease intentionally spans
those coding-session effects without holding `mutation_io_lock`; only the durable tree append holds that lock across
I/O.

### Guard acquisition graph

Normal reload phases keep the existing non-nesting rule:

1. **Seal/snapshot:** take only the R1 candidate guard, seal and detach the
   candidate projection/handles, then release it.
2. **Publication/generation queue:** in a separate session-mutex section,
   publish or reject the prepared generation and close/detach generation queues.
   Release before queue delivery, callbacks, I/O, construction, rendering, or
   cleanup. In particular R4a detaches an outbox under the session mutex and only
   then enters `CodingInputQueue`; `session mutex → mutation_io_lock` is forbidden.
3. **Chrome:** after session unlock, take the live driver's owner guard only to
   select/swap an owner or update handoff/lease state, then release it. Take one
   chrome sink guard separately to close/snapshot, release it, then
   deliver/reconcile/paint/dispose. No owner/sink guard spans extension
   factories/callbacks or session-mutex acquisition; neither `session → chrome`
   nor `chrome → paint` exists.

R5a adds one coordinator without holding its lock across arbitrary effects:

4. **Coding-effect admission:** under `mutation_io_lock`, an effectful retained
   adapter checks terminal and claims the coordinator's exclusive owner/depth in
   one section (waiting on the condition when another thread owns it), then
   unlocks. If terminal closes while an accepted owner runs, only a same-thread
   nested effect may re-enter that lease; unrelated waiters refuse. Provider
   completion, rendering, diagnostics, and
   callbacks run unlocked while the lease remains live. Active-tree pointer
   check/use, every `NativeSessionTree` mutable-state operation, and every
   `CodingInputQueue` mutable-state operation take the same reentrant lock.
   Custom-message tree, unlocked render, and input phases retain their existing
   order. A tree append alone retains the lock through `_write_entry()` to
   preserve durable order.
5. **Effect completion:** in `finally`, the adapter takes `mutation_io_lock`,
   decrements depth, clears the owner at zero, and notifies the coordinator
   condition. This executes on
   success or exception, so teardown cannot wait forever on an abandoned lease.
6. **Thinking/class-A commit:** R5b takes `mutation_io_lock → session mutex`,
   commits only selection state under the inner session section, releases it,
   performs the ordered tree append under the still-held outer lock, then
   releases before footer paint. No path acquires the locks in reverse order.
7. **Terminal:** after settle then `session_shutdown`, a nested teardown
   `finally` takes `mutation_io_lock`, atomically closes admission, and waits for
   the active owner to clear; `Condition.wait()` releases the lock while accepted
   effects finish. Once quiescent it takes the session mutex only long enough to
   invalidate/detach the live generation and close/detach its generation queue.
   It releases both before chrome close and paint/disposal. Calls admitted first
   finish; later calls raise `ExtensionCapabilityError`. There is no check-then-
   act and no provider or filesystem I/O under the session mutex.

The only nested cross-owner edge is `mutation_io_lock → session mutex`; candidate,
generation-queue, chrome, provider, and paint phases otherwise remain serial or
unlocked. Forbidden edges are `candidate ↔ session`,
`session → mutation_io_lock`, `session → chrome`, and `chrome → paint`. R1,
R4a, R4c, R5a, and R5b instrument their relevant edge/refusal boundary.

### Executable R1–R6 bounds

These bounds support the exact 27-slice plan. “Named mechanisms” may list
multiple owners only with the explicit order above; a hidden owner or reverse
edge requires plan revision before code.

| Slice | Owning module / touched family bound | Named concurrency mechanism/order |
| --- | --- | --- |
| R1 — **shipped** | `extension_runtime` activation/registration host: all command/shortcut/hook/tool/provider/unregistration/flag/value/renderer/message staged fields, `_failure`, `_activated`, candidate open→sealed→committed→published/disposed transitions, and accepted-catalog terminal state; `extension_loader` abandonment seam; startup/reload rejection wiring. Runtime sidecar append conversion, live chrome, selection, provider, and consumer dispatch remain excluded. | One candidate-host guard is taken by every listed reader/writer; extension-controlled validation/string coercion runs unlocked in the historical per-family ordinary-validation failure order before a guarded atomic recheck-and-commit; one-way seal plus one authoritative atomic freeze snapshot. The host lifecycle is the sole state machine. Host-authored batch validation and transition under all candidate guards prevents partial publication, including an open/unsealed refusal arm; there is no ownership-wrapper state/lock or publish/dispose forwarding helper. One lock-free optional runtime holder covers only the pre-runtime reload seam. Rejection disposes every unpublished sibling and returns structured skipped-published/inaccessible anomalies to startup/session diagnostics. Reload constructs the exact generation before transfer and then performs only its non-fallible pointer publication before later projection work. Startup constructs its generation reference before transfer. Accepted `str` subclasses become exact strings before reservation; invalid unregistration records/raises `invalid_provider`. The R1 frozen snapshot is authoritative; post-seal pending sends do nothing; R3b/R3c own its one staged detach/flush/delivery sequence, while accepted live routing after activation commit releases the host guard before the later R4a live-append section. Recursive inventory pins every runtime producer/caller. |
| R2 — **shipped** | Candidate chrome/listener sink owner, reload orchestration including accepted-only candidate-bound replacement `session_start`, TUI chrome reconciliation adapter, and closed-sink no-op API documentation. No other projection, selection owner, or ordinary invocation binding. | One sink-local guard serializes closed-check+write/close and attach handoff for every retained class-B family. A separate short driver owner guard records only selection/swap/handoff/lease state. Rejection closes without delivery and does not re-fire retained lifecycle; acceptance fires replacement lifecycle once, reconciles after semantic commit, drains concurrent retained/candidate handoff writes exactly once, and transfers ownership only on success. An explicitly nested retirement routing scope sends synchronous reconciliation/retired-cleanup disposal writes to a closed sink instead of the candidate queue. Delivery, paint, factories/callbacks, disposal, and session-mutex acquisition remain outside both guards; TUI clear detaches under its paint lock and releases it before disposal. Ownership transfers before retired cleanup so interrupts propagate without double-close. |
| R3a — **shipped** | Standalone `session_generation` projection values/builder and pure projected/legacy tool-port and candidate-composition adapters; no settings adapter and no R1 `activation_hosts` ownership state. | Detached immutable construction only. Copied mappings/tuples, shallow-frozen custom-message options, private port flag dictionaries, queue/reference mutex input validation, exact R2 chrome identity, per-family equivalence, and bounded no-alias tests are complete. The two real live tool-port sites call only the legacy helper; no production caller constructs, installs, publishes, snapshots, or consumes the projection. |
| R3b — **next** | Detached reload-effect preparation value and uninstalled ordered-delivery gate/token/sequencer, including authoritative frozen staged activation detach/flush/delivery. | Pure preparation only; no startup/reload or live queue hook calls it. |
| R3c | Startup/reload installation of the R3a/R3b values and bounded accepted staged-delivery wiring. | Invoke the R3b staged sequence exactly once, preserve every R3a equivalence arm, and defer ordinary consumer moves to R4. |
| R4a | Command/shortcut/input/request/session-hook consumers and snapshot adapter; accepted/live generation-outbox append writers (`_ActivationApi.send_user_message()` and `send_message()`/`sendMessage()`); `_CustomEntryRenderer.drain_extension_outboxes()` **only** (not rendering, accepted staged-custom-message delivery, or coding-session sends); queue rejection/retirement close; provider-header request-local trust capture; reload composition only for those close/deletion duties; closed-queue API documentation. `_commit_activation()` staged detach/flush/delivery remains R3b/R3c-owned and must not be reimplemented. | Existing session mutex as the queue guard for later live closed-check+append, atomic detach/drain, close, and one operation snapshot. R4a starts after R3c has delivered the authoritative staged batch and released its initial ordered gate; no candidate-host guard or repeated staged flush belongs here. No nested edge. |
| R4b | Tool advertisement/execution, renderer **selection/rendering other than the R4a drain method**, provider contribution/refresh consumers, snapshot adapters, and proven legacy-source deletion. | Existing session mutex for one snapshot; no class-A admission. |
| R4c | Menu/description/shortcut, lifecycle input, chrome reconciliation/retirement close, and reload publication composition. Terminal invocation remains R5a. | Existing session mutex for pointer publication/snapshot, then after unlock one chrome sink-local guard for closed-check/snapshot/close, then paint after all guards. |
| R5a | One coding-effect coordinator in the composition root; effectful `ExtensionCodingSessionControl` adapters; active `_RunControlState.session_tree` pointer access/rebinds; `NativeSessionTree` mutable owner and durable writer; all `CodingInputQueue` readers/writers and session-controller/agent/RPC adapters; `CodingSessionController.run_loop()` finally and `NativeToolReplSession.run()` terminal ports; terminal generation-queue/chrome invocation. Provider/selection mutation is excluded except terminal invalidation. | Promote existing `mutation_io_lock` plus a condition on it into one coordinator. An exclusive/reentrant owner lease serializes effects while the lock is released; shared tree/input phases use the lock; tree append alone holds it through durable I/O. Terminal condition-waits, then uses `mutation_io_lock → session mutex`; chrome/paint follow after release. |
| R5b | Only class-A `set_active_tools`/`set_thinking_level`; guarded selection owner; session-thread cycle and RPC thinking adapters; generation admission and post-lock tree/footer adapters. Coding-session callable design and terminal wiring are already R5a; `set_model` is excluded. | Existing session mutex for generation-bound active-tool/thinking admission. Thinking uses `mutation_io_lock → session mutex`, releases the inner lock before tree I/O and the outer lock before footer paint, and has no reverse edge. |
| R6 | `set_model` port including terminal refusal, provider/catalog preparation adapter, guarded provider/coding selection owner, and post-lock persistence/presentation adapter. | Three-phase model mutation with only the non-fallible commit under the existing session mutex. |

### Behavioral scenario ownership

The older checklist remains useful, with these current dispositions. This table
prevents a narrowed protocol from being mistaken for a required implementation.

| Checklist scenario family | Disposition |
| --- | --- |
| Retained frozen projection; successful coherent commands/hooks/tools/providers/renderers/flags/shortcuts/UI; derived projection failure | **R3a detached construction/equivalence/no-alias arms landed; R3c installation and R4a–R4c coherent consumer adoption remain**, closed in R7 integration. |
| Stale model and hook model controls; stale thinking; stale active tools; pending-publication refusal; accepted-before-gate survival | **Required in R5b–R6**, with model/hook model in R6 and thinking/tools in R5b. |
| Concurrent retained coding-session control versus session/RPC tree/input use; durable JSONL order; accepted-before-terminal completion; terminal refusal | **Required in R5a.** Every tree/input reader/writer and extension writer adapter uses the one coding-effect coordinator; no reachable lost update is treated as a trusted-extension effect. |
| Late cancelled tool/provider result contributes no result/history/tree/event/stream output | **Landed** in `tests/test_native_agent_tool_executor.py` and `tests/test_native_agent_provider_turn.py`; R7 retains the integration arm. Explicit extension side effects use their class rules. |
| Closed rejected/retired sidecar drops appends/chrome writes; retired queue handles remain observably undeliverable as today; worker outliving `run()` mutates no class-A or coding-session state | **Required in R4a/R4c/R5a/R5b/R6**. |
| Rejected cleanup cannot leave the gate open | **Landed in R1.** The gate `finally` closes before rejected runtime hosts dispose, and disposal cannot reopen registration. |
| Activation API exposes no class-A port | **Landed** by `PipyExtensionAPI`/`_ActivationApi` shape; R1 retained it. |
| Candidate user/custom outboxes are not delivered on rejection | Activation staging, R1 sealed/disposed abandonment, and R3a detached queue-handle construction are **landed**; R3b/R3c own gate preparation/installation and R4a owns synchronized append/drain/close. |
| Partial-delivery cursor resume, cutoff ids, duplicate-attempt idempotence, compaction, and capacity refusal | **Formally narrowed by C14.** R4a instead proves atomic ordered detach versus append and live-only drain. |
| Rejected listeners/chrome requests; post-seal contribution; normal published handler queue/chrome use | Post-seal contribution refusal **landed in R1** and rejected candidate chrome/listener close **landed in R2**; queue conversion remains R4a and generation-bound retired-live chrome remains R4c. |
| Candidate flag failure keeps runtime/flags | **Landed**, including R2 preservation of old retained chrome/listeners; R3a now builds an equivalent detached complete projection, while R3c/R4a–R4c still install and consume it. |
| Provider fallback and extension-provider refresh preserve their established history/usage/compaction behavior | Behavior characterization **landed**; coherent snapshot/publication **required in R4b/R4c**. |
| Active-tool filtering and renderer pinning stay compatible | Characterization **landed**; snapshot/generation admission **required in R4b/R5b**. |
| Lifecycle ordering stays compatible | **Required in R4c**, using the existing post-gate order as characterization. |
| Removed/disabled chrome clears only after successful commit | **R2 acceptance timing landed**; R4c still owns coherent complete-generation publication and retired-live closure. |
| Concurrent coding history/usage updates are not lost | Coding-state mutex **landed**; R6 must preserve it through atomic model commit. |
| Settings/trust/keybindings/resources old-on-rejection and snapshot-pinned settings/resources | **Formally narrowed by C16–C18.** Successful configuration/resource reload remains applied on extension rejection, with serial session-thread observation. |
| Failed process-theme mirror/repaint leaves a transactionally committed settings theme | **Formally narrowed with settings by C16/C28.** The manager's setting is already live before extension acceptance; process reflection remains fail-soft/non-transactional. |
| First defaults persistence and persistence failure/concurrent overwrite | **Landed behavior** in `tests/test_native_repl_state.py`; R6 retains it after atomic in-memory commit. |
| `before_agent_start` model changes affect the current turn | **Required compatibility in R6**; preparation/commit barriers may not move the established effect to a later turn. |

R7 runs the retained scenario matrix and records each required row as shipped or
blocks closeout. It does not reopen any formally narrowed row without first
revising this section and the remediation plan.

## Why the first attempt was abandoned

The first attempt grew into a distributed prepare/apply/rollback transaction
spanning `NativeReplProviderState`, `CodingSessionState`, `SettingsManager`,
`KeybindingsManager`, package/workspace resources, process-global theme state,
and persisted defaults. Each participant gained a revision counter, a prepared
value, apply/rollback methods, and compensating restoration. Independent review
kept finding new interleavings, because the mechanism itself was unsound:

1. **Compensating rollback is not atomicity.** Restoring a previous value after
   a partial apply is a second mutation, not an undo. Any reader between the
   apply and the restore observes a state that never existed as a generation.
2. **Optimistic revision checks without a shared lock do not exclude anyone.**
   Reading a revision and then assigning is a check-then-mutate window. Unless
   every writer of that field takes the same lock — or the field is
   single-thread-owned — the check proves nothing.
3. **Fallible work cannot live inside the commit.** Holding a generation lock
   across file writes, provider construction, extension activation, diagnostics,
   message delivery, rendering, or arbitrary callbacks converts every one of
   those failures into a partially committed generation.
4. **"Immutable" generations that share mutable containers are mutable.** A
   retained generation whose tool visibility, provider selection, settings
   contents, or renderer map is the same object as its successor's is not a
   generation at all.

The rebuild keeps the goal — publish one complete extension generation
atomically — and discards the mechanism.

## Executable facts this contract is built on

These were verified against the tree at `3dae3b8` and are the reason the
contract can be small.

**Exactly one thread runs a session.** Every mode drives the coding session on a
single thread:

- interactive TTY and captured-stream modes run it on the process main thread;
- `--mode json` / `--print` run it on the calling thread; and
- `--mode rpc` runs it on one dedicated worker (`rpc.py` `_run_worker`), while
  the socket reader thread only pushes text into a FIFO channel.

Command interpretation, `/reload`, provider-request preparation, event
projection, and session-tree writes all execute on that one thread. Call it the
**session thread**.

**Detached workers are the only real concurrency.** The session thread spawns
`pipy-provider-turn` (`agent/provider_turn.py`), `pipy-tool-call`
(`agent/tools.py`), `pipy-ext-activate` (`extension_loader.py`),
`pipy-local-shell`, and RPC bash workers. In the settled path the session thread
joins them before continuing, so they are not concurrent in any observable
sense. **On cancellation they are joined with a timeout and the session thread
proceeds regardless** (`agent/tools.py` `_execute_interruptibly`,
`agent/provider_turn.py`). A cancelled-but-still-running extension tool handler
therefore outlives the operation that started it and can call back into the
session afterwards — including after a subsequent `/reload`.

**A straggler's *result* is already discarded; only its *side effects* escape.**
This bounds the problem sharply, and it is existing behavior, not an aspiration:

- `agent/tools.py` `_execute_interruptibly` returns a cancellation outcome and
  never reads `result_holder` when completion did not precede cancellation, so a
  late tool result reaches neither history, nor the session tree, nor the event
  projection;
- `agent/provider_turn.py` does the same for a late provider completion; and
- the invocation output gate is closed on cancellation, so a straggler's
  streamed output stops reaching the event sink.

A late completion therefore cannot mutate a newly published generation through
the normal result path. What a straggler *can* still do is call back into pipy
from inside its handler — and those calls are exactly the four port classes
below, governed by their rules rather than by the result path. The contract must
keep this true, so a test pins that a tool worker completing after its
cancellation contributes no result message, no result-derived session-tree
entry, and no result-derived event.

That single window is the whole concurrency problem. It does not require a
general-purpose transaction; it requires that (a) the live generation pointer is
published under a lock the stragglers also take, and (b) every straggler-
reachable port is bound to the generation it was created for.

## The concurrency contract

> Historical design evidence. For implementation, the controlling dispositions
> are [C01–C30](#clause-disposition-table) plus the current
> [boundary/guard decisions](#transactional-boundary-decisions-and-reachability-proof).

### One synchronization boundary

`_RunControlState` owns exactly one `threading.RLock`, named
`session_state_lock`, created once per `NativeToolReplSession.run()`. It is
reentrant because a mutation port may be invoked from inside another port on the
session thread (`extension_set_model` re-enters the peer provider effects).

**Guarded state.** A field is guarded when a straggler can reach it, directly or
through any port. **Every** reader and writer of guarded state takes
`session_state_lock` — the session thread included. A lock that only one side
takes excludes nobody, which is exactly how the abandoned attempt failed. The
guarded set is:

- `_RunControlState.extension_generation`, the generation identity used for
  staleness checks, and the `publication_pending` gate described below;
- provider selection and thinking level in `NativeReplProviderState` — reachable
  from `extension_set_model` / `extension_set_thinking_level` and written by
  `/model`, model cycling, and auth changes on the session thread;
- the provider binding, canonical history, usage accumulator, and compaction
  state in `CodingSessionState` — `extension_set_model` reaches these through
  the provider rebind, which clears live history and resets usage;
- the provider-visible active tool selection in the tool-capability owner —
  reachable from `extension_set_active_tools`;
- the frozen state value inside each of `SettingsManager` and
  `KeybindingsManager` — extension tool ports and hook contexts are constructed
  against settings-derived values such as project trust, so a straggler can
  observe them;
- the enqueue and reading of a generation's user, custom, and notification
  queues, and their delivered cursors; and
- a generation's extension chrome state, on both the writing and rendering side.

The staging host is the one piece of cross-thread state guarded by something
other than `session_state_lock`: the activation worker writes staged
contributions while the session thread harvests and seals them, so the host's
staged contributions **and** its sealed flag share the host's own guard, taken
by writers and harvester alike. It is a separate guard because the staging host
exists before any generation does, and it is never held while
`session_state_lock` is held.

**Session-thread-owned state — no lock, and documented as unreachable from a
straggler:**

- `_RunControlState.session_tree`, `tree_filter_mode`, `pending_prefill`,
  `agent_settled_pending`, and `extension_in_agent_turn`;
- `_RunControlState.package_roots` and `workspace_resources` — read only on the
  session thread, but assigned inside the commit critical section so publication
  stays one step; and
- all terminal UI and renderer state (the TUI keeps its own paint lock for
  terminal writes; that lock is unrelated and is never nested inside
  `session_state_lock`).

If a future change gives a worker a path to any session-thread-owned field, that
field moves into the guarded set in the same change.

**The lock is never held across slow work.** An operation that needs guarded
state takes the lock, reads one consistent snapshot (generation plus the
provider/history/tool values it needs), releases it, and runs on the snapshot.
Provider turns, tool calls, rendering, and file I/O all happen outside the
critical section. Serializing turns is not the goal; excluding torn reads and
lost updates is.

**Nothing is released under the lock.** Dropping the last reference to a value
runs its finalizer and any weakref callbacks right there, which would smuggle
arbitrary code into a critical section that claims to contain none. So a single
rule applies everywhere, not only at commit: **any value displaced while the
lock is held is moved into a retired holder that outlives the critical section,
and is released after the lock.** This covers the commit's superseded generation,
settings, keybindings, resources, history container, and usage accumulator; it
equally covers queue entries compacted below a delivered cursor and chrome
values replaced by a class B write. A critical section overwrites references and
hands the old ones outward; it never lets them die.

### What a generation is

A generation has three parts, and conflating them is what made "immutable
generation" ambiguous in the first attempt:

- a **frozen projection** — commands, hooks, tools, providers, renderers, flags,
  shortcuts, parsed flag values, and the derived tool-capability and renderer
  state. Once published this never changes. "A retained old generation remains
  unchanged" is a statement about exactly this part, and it is what every
  consumer snapshots and reads.
- **owned mutable sidecars** — the generation's user/custom message queues, its
  notification queue, and its extension chrome state. These are append/update
  targets by design; the outbox list identities are load-bearing and pinned by
  `tests/test_native_session_extension_generation.py`. A straggler may still
  write into a retired generation's sidecars, and that is harmless precisely
  because nothing reads a non-live generation's sidecars.

- **generation-scoped selection state** — the active provider selection, the
  thinking level, and the active tool set. These are mutable *while the
  generation is live*: class A ports and session-thread writers such as `/model`
  change them under the lock, which is the whole point of class A. They are not
  part of the frozen projection and must not be described as if they were.

  At commit they are **rebased, not carried by reference**. The candidate
  computes each one from the outgoing generation's value — read in the same
  critical section that opened the publication gate, so no accepted mutation is
  missed — combined with the new catalog and registry: the provider selection
  survives unless it disappeared or lost tool-call support, the thinking level
  survives, and the active tool set survives filtered to names the new registry
  still defines. That filtering is today's behavior. The published generation
  then owns its own selection state, and the retired generation's copy stops
  being read at the swap.

  **A prepared value must never restore a superseded selection.** Reading the
  outgoing value at gate-open is only sound while the gate is open. Where a
  component publishes without one — a sub-slice landing before the gate exists,
  or any publication path that does not open it — the rebind must instead read
  the live selection *inside the publication critical section* and assign that
  reference onto the value being published. This is still a reference
  assignment, so it does not weaken the pointer-only rule, and it is strictly
  stronger than the gate for this field: no mutation accepted at any point
  before the swap can be overwritten. Publishing a selection captured earlier,
  outside the critical section, is the lost-update bug this contract exists to
  prevent.

So a retired generation is immutable where it matters and inert where it is not.
No part is ever shared with a successor: a new generation gets new projections,
new sidecars, and its own rebased selection state.

### Straggler-reachable mutation ports

These are the pipy-owned entry points an extension handler can call from a
worker thread. Every one of them belongs to exactly one of four classes, and the
class fixes its rule. There is no fifth, undocumented case: a new extension-
facing port is assigned a class in the same change that adds it.

| Class | Ports | Rule |
| --- | --- | --- |
| A — session-state mutation | `extension_set_model`, `extension_set_thinking_level`, `extension_set_active_tools`, and the hook-context model controls | generation-bound, gate-checked, lock-guarded; check and mutation in one critical section; fails closed |
| B — live-surface state | extension chrome: header, footer, widgets, title | writes into the *owning* generation's chrome state; only the live generation's chrome state is rendered |
| C — generation-owned queues | `send_user_message`, the custom-message equivalents, and extension notifications | appends to the *owning* generation's queue under the lock; only the live generation's queues are drained |
| D — contribution registration | commands, tools, providers, shortcuts, flags, message and entry renderers, event hooks | activation-scoped; sealed when the candidate is frozen or disposed |

**Class A** captures the `SessionGenerationId` of the generation it was
constructed for. Under `session_state_lock` it compares that id with the live
generation's id and checks the publication gate below; on a mismatch or an open
gate it mutates nothing and fails closed (`False`). Check and mutation happen
inside the same critical section, so this is not the rejected optimistic-CAS
pattern.

**Classes B and C never check liveness and never fail *for staleness*.** They
can still be refused for capacity or discarded after their sidecar is closed —
see those rules below — but there is no liveness comparison in them to fail, and
that is the property that matters here. Their acceptance semantics are exactly
three: accepted, refused because the queue is at capacity, or discarded because
the sidecar is closed. Where a port already returns a boolean, that boolean
reports acceptance; where it returns nothing today, the outcome surfaces only
through the diagnostic described under the capacity rule, so no existing
signature changes. A check-then-act port
cannot be made safe here: whatever it verified under the lock can be superseded
before the effect lands, which is the same check-then-mutate window that sank
the first attempt. Instead these ports are *write-only into the generation that
owns them*, and liveness is enforced at the reading end, where the reader
already holds the lock and the live pointer at the same instant:

- a class B write updates its own generation's chrome state; the TUI renders
  chrome from whichever generation is live;
- a class C append lands on its own generation's queue; the session drains only
  the live generation's queues.

**Both ends of both classes take `session_state_lock`.** Write-only-into-my-own-
generation removes the *staleness* window; it does not remove the *tearing*
window. A reader that takes the lock while writers do not is synchronized with
nobody. So a class B write updates chrome state under the lock, and a render
takes the lock, copies an immutable chrome snapshot, releases it, and paints
from the copy — painting never happens inside the critical section, and the
TUI's own paint lock is still never nested inside `session_state_lock`.
Likewise, class C appends and drains both take the lock, and delivery of a
drained batch happens after release — with the single named exception for user
messages described below, whose effect is an in-memory queue push.

A retired generation's writes therefore land in state nothing reads, with no
window between the pointer swap and disposal. A candidate's writes land in the
candidate's own chrome state and queues — that *is* the isolated staging sink
the candidate phase requires — and become visible at the instant the candidate
is published, never before. This is also how "removed or disabled extension
chrome clears only after a successful commit" holds: the committed generation's
chrome state simply lacks the removed extension's contributions, and post-commit
reconciliation repaints from it.

**Class C detail — appends land on the generation's own sink, bound at
drain.**
`send_user_message` and the custom-message equivalents append to the outbox list
objects that the generation's extensions captured at activation. Those list
identities are load-bearing and are pinned by
`tests/test_native_session_extension_generation.py`, so a straggler's append is
*not* refused: it lands on its own generation's outbox, exactly as it does
today. Staleness is enforced at the other end — the session drains only the live
generation's outboxes, so an append to a superseded generation is retained and
never delivered. Rejected candidates are disposed before they are ever live, so
their outboxes are likewise never drained. "No delivery from a non-live
generation" is the invariant; refusing the append is not.

Outbox contents are guarded state, so **both ends take the lock**: enqueue
appends under `session_state_lock`, and the session reads under the same lock.
Without that, an append racing a read is lost.

**Delivery is an acknowledged cursor, not a copy-and-clear.** Clearing a queue
before performing fallible delivery loses whatever was in flight when delivery
fails, and makes retry unsafe — the opposite of the idempotence the post-commit
phase claims. Each generation's queues are therefore append-only and never
cleared, and the session holds a per-queue **delivered cursor** as guarded
state:

1. under the lock, copy the pending slice from the cursor to the end;
2. release the lock and deliver, in order, advancing the cursor under the lock
   after each individual message rather than once per batch.

A failure part-way through a batch therefore leaves the cursor at the last
acknowledged message and the next drain resumes exactly there. Concurrent
appends land past the end of the copied slice and are picked up by the next
drain.

**The guarantee is in-order, gap-free delivery over an idempotent sink — not
at-least-once delivery.** Stated as the single rule an implementation and its
tests can enforce:

1. messages are delivered in queue order, starting at the delivered cursor;
2. no message is skipped — if message *i* was delivered, every message before it
   was delivered;
3. a delivery pass stops at its first failure and reports it; the messages
   behind that failure stay pending, are retried by the next drain while the
   generation is live, and are dropped with a diagnostic if the generation
   retires first; and
4. a message may be attempted more than once, and a repeat is harmless.

Nothing here promises eventual delivery, because a failed attempt is fail-soft.
Correctness therefore rests on an idempotent sink — not on a dedup table.
Delivery performs its effect before
the cursor advances, so a failure between the effect and the advance re-delivers
that message. A "seen ids" set does not fix this: unless recording the id is
atomic with the effect, the same window simply moves. The sink itself must be
idempotent.

So:

- every queued message carries a **session-scoped id**, assigned at enqueue
  under `session_state_lock` from a monotonic counter owned by the session
  rather than by a generation. Ids are therefore unique across generations and
  across reloads. This counter is the **one explicit exemption** to the
  candidate phase's "touches no live state" rule: a candidate's extensions may
  enqueue during activation, so they consume ids. Consuming an id mutates no
  semantic state — ids are opaque and only ever compared — so a rejected
  candidate leaves nothing behind but a gap in the sequence, which no consumer
  can observe. The alternative, generation-local ids, would break the
  cross-generation uniqueness the cutoff and the tree-entry identity both rely
  on;
- **custom messages** have a multi-step delivery — session-tree append, event
  projection, render — and only the first step is retried. The durable append
  *is* the delivery: its entry identity is derived deterministically from the
  message id, so a repeat is a no-op, and the delivered cursor advances as soon
  as it succeeds. Event projection and render then run as **post-delivery
  presentation derived from the committed tree**, under the same fail-soft rule
  as every other presentation effect in this contract: a failure is diagnosed,
  not retried. Nothing re-emits an event, so nothing can duplicate one, and no
  "already emitted" bookkeeping has to exist. The cost is stated plainly: a
  projection or render failure leaves the message durably recorded but not shown,
  which is exactly the trade the post-commit phase already makes for chrome and
  theme;
- **user messages** are different, because replaying one would start a duplicate
  turn. Their delivery effect — pushing the message onto the session input
  queue — happens *inside the same critical section* that advances the delivered
  cursor, so record and effect are atomic and a user message cannot be replayed
  at all. The session also keeps a monotonic highest-accepted-id watermark,
  checked in that same section, so an out-of-order or repeated id is refused.

  This is the one **named exception** to "deliver outside the lock", and it is
  narrow by construction. The rule that matters is *no slow or arbitrary work
  under the lock*: a push onto an in-memory queue calls no handler, renders
  nothing, touches no file, and releases nothing. Custom messages and
  notifications get no such exception, because their effects include session-tree
  appends and rendering; they deliver outside the lock and rely on idempotence
  instead; and
- **notifications** are a redraw and are idempotent by construction.

With an idempotent sink there is no dedup state to own, lock, or retain, and no
record-then-effect race. The delivered cursor stops being a correctness
mechanism and becomes what it should be — an optimization that avoids
re-attempting work already known to be done. The guarantee this contract makes
is in-order, gap-free delivery over an idempotent sink; a test covers a duplicate
delivery attempt explicitly and asserts a single applied effect.

**The no-loss guarantee is scoped to one generation's lifetime.** A retired
generation's queue is never read again and is never handed to its successor, so
anything still undelivered on it when the pointer swaps is dropped. That is the
current base's behavior — a reload abandons the old outbox and the new runtime
gets new lists — and this rebuild does not change it.

The ordinary case is nonetheless made lossless, via an **atomic cutoff** rather
than a drain-then-gate sequence. Draining first and opening the gate afterwards
would leave a window in which an append lands after the drain but before the
gate — a message that arrived "before the gate" yet gets dropped at publication.
So the two are one step, and they form the cutover phase described below:

1. in a single critical section, open the publication gate **and** record, per
   queue, the id of its last message as the drain cutoff;
2. deliver every pending message whose id is at or below that cutoff, using the
   normal locked-copy / unlocked-deliver / locked-advance rule.

The cutoff is a **message id, not a queue index**. An index would be invalidated
by the front compaction described above, which rebases a queue and its cursor
while the gate is open and could shift a post-cutoff message below a recorded
index. Ids are monotonic and never reused, so "at or below this id" survives any
amount of compaction.

Every message appended after the gate opened is unambiguously post-cutoff. The
pre-cutoff range is attempted **in order, up to the first failure**: the cursor
is contiguous and delivery order is observable, so a message that fails cannot
be skipped to reach the ones behind it. The cutover therefore stops at that
point and emits one diagnostic naming the failure and the number of pre-cutoff
messages left unattempted. It does not abort the reload, and those remaining
messages are dropped with the retired generation. That is a deliberate,
diagnosed loss on a failure path, not a silent one. Reload runs on
the session thread between turns, so in practice the pre-cutoff set is exactly
what a normal drain would have taken, and the only post-cutoff writer is a
straggler from a cancelled operation — whose append is dropped at the swap
exactly as it is today, or delivered normally if the reload is rejected and the
generation stays live. If the pre-cutoff delivery itself fails, the failure is
reported and the reload proceeds; the messages it could not deliver are dropped
with the retired generation rather than blocking the reload.

**Growth is bounded two ways.** Append-only is a delivery rule, not a retention
rule: under the lock, entries strictly below the delivered cursor may be
detached and the cursor rebased in the same critical section, with the detached
entries handed out to be released after the lock per the rule above. This
preserves the list *object* identity the existing characterization pins — only
its already-delivered contents go away, exactly as they do today.

Compaction only helps once messages are delivered, so each queue additionally
carries a **bounded pending capacity**. While the publication gate is open the
session thread is busy preparing a candidate and drains nothing beyond the
recorded cutoff, so a hung reload plus a hung straggler would otherwise grow the
live queue without limit. An append that would exceed the capacity is discarded
under the lock and counted, and the first discard emits one diagnostic naming
the queue. Dropping the oldest undelivered message instead is not an option —
delivery order is observable — so the newest is refused, which is also the
behavior an extension can reason about.

That handles the live generation. A *retired* generation's queues are never read
again, so nothing would ever compact them, and a hung worker could append to one
forever. Disposal therefore **closes** the retired generation's sidecars: a
one-way flag on the sidecar itself, set under the lock, after which an append is
discarded and a chrome write is dropped. This is not a liveness check — it does
not compare anything to the live pointer, so it reintroduces no check-then-act
window. Before the flag is set the writes land somewhere nothing reads; after it
is set they land nowhere. Either way no consumer sees them, and memory is
bounded. Rejected candidates are closed the same way at disposal.

### The publication gate

Generation binding alone does not prevent lost updates. Candidate preparation
reads live provider selection, thinking level, and active tool visibility, then
publishes derived values some time later. A straggler that mutates one of those
fields *validly* in between — its generation is still live, so its check passes —
would be silently overwritten by the commit. Serializing only the final
assignments does not close that window.

`_RunControlState` therefore carries one more guarded field,
`publication_pending`, holding the id of the generation being superseded, or
`None`.

- The cutover phase opens the gate under `session_state_lock`, in the same
  critical section in which it reads the live values the candidate will build on
  and records the outgoing message-queue cutoff described under class C. The
  lock is then released; the gate stays open, the lock does not.
- While the gate is open, every generation-bound mutation port fails closed
  (`False`) even though its generation id still matches. Refusal is already the
  defined contract for these ports, and a mutation arriving in this window comes
  from a straggler whose originating operation was cancelled.
- The gate closes when the *whole* publication is done, not at the pointer
  swap. A reload swaps the generation pointer partway through and republishes
  provider selection, tool visibility, and renderer projections afterwards;
  reopening mutations at the swap would let a change be accepted and then
  overwritten by those later projections.
- The gate also closes *before* post-reload extension lifecycle hooks run.
  This is defensive rather than load-bearing today: a lifecycle-hook context
  carries no class A port, so there is currently nothing for the gate to refuse
  there. The ordering is fixed now, and a test pins the capability fact, so
  wiring those controls into lifecycle contexts later cannot silently start
  refusing legitimate reload hooks.
- A rejected candidate closes the gate under the lock with no swap, and
  mutations are accepted again. **Closing the gate is guaranteed, not
  best-effort:** it happens in a `finally`, and before the candidate's fallible
  cleanup — sealing, closing sidecars, releasing listeners and chrome requests —
  rather than after it. Cleanup that raises or hangs must never leave the live
  generation permanently gated, which would silently disable every extension
  mutation for the rest of the session. The candidate is not live, so
  re-admitting mutations before its cleanup finishes is safe.

**Admission must be atomic with the effect.** Reading the gate and then
applying a mutation are two critical sections; a port that does so can pass the
check and land its effect after a reload has already read the state it will
republish. A gate-checking port must therefore hold the session mutex across
both the check and the effect. That is only possible where the effect is purely
in memory. `set_active_tools` qualifies directly. `set_thinking_level`
qualifies once its session-tree append and footer refresh are moved *after* the
critical section — they are post-mutation effects, not part of the decision.
`set_model` does not: it persists a default part-way through its mutation, and
holding the mutex across file I/O is forbidden above. Until provider
construction and persistence are lifted out of that port (S3.7c and S3.8), the
gate narrows its window rather than closing it. That single port is a recorded
residual, not a met guarantee.

No accepted mutation can occur between the candidate's read of live state and
the commit, so the candidate cannot overwrite one. Because the gate is opened
and closed under the lock but not *held* across the candidate phase, no fallible
or slow work runs inside a critical section. A reload that never finishes leaves
the gate open and extension mutations failing closed — the fail-closed direction.

`/model`, auth, and other session-thread writers take the lock like everyone
else. They cannot observe an open gate, because the session thread is the one
running the reload.

**Candidate activation is not affected by the gate**, and this is a structural
fact rather than a lucky ordering. Class A ports are exposed on the *handler*
context built per command, tool, or hook invocation — `make_extension_context`
wires `set_model`, `set_thinking_level`, and `set_active_tools` at invoke time —
not on the activation API an extension's `activate()` receives. An activating
extension therefore has no way to call them, with or without a gate, so opening
the gate before activation changes no extension-visible behavior. The contract
depends on that separation, so a test pins it: the activation API exposes no
class A port, and adding one would require staging the mutation into the
candidate instead.

### Four phases

**Cutover phase (touches the live generation, before any candidate exists).**
This phase exists because the candidate phase must be genuinely isolated, and
the two things that unavoidably touch live state cannot be smuggled into it. In
one critical section: open the publication gate and record the outgoing message
queues' cutoff. Then, outside the lock, deliver the pre-cutoff messages by the
normal locked-copy / unlocked-deliver / locked-advance rule, in order and
stopping at the first failure. A delivery failure here is reported and does not
abort the reload. Nothing about the candidate exists yet.

**Candidate phase (fallible, no lock held across its work, nothing live is
touched except the exempted message-id counter).** Build every
value that can fail, against a staging host that is not reachable from the live
generation: candidate settings/keybindings state, package roots and workspace
resources, the candidate `_ExtensionRuntime` (own outbox lists, own listener
registrations, own chrome request sink), parsed candidate flags, derived tool
ports, tool-capability state, renderer maps, provider catalog contributions,
command names/descriptions, shortcut keys, and — when the active selection would
be rebuilt or would disappear — the replacement or fallback provider object,
and for the fallback case a fresh usage accumulator and a fresh empty
live-history container. Any failure disposes the
candidate — close the publication gate first and unconditionally, then seal
contribution registration, close its sidecars, and release its listeners and
chrome requests without delivery — emits the existing diagnostic, and returns.
The live generation is untouched because nothing ever pointed at the candidate.

**Sealing the staging host.** Disposal is not enough on its own. Extension
activation is driven on the `pipy-ext-activate` worker and is bounded by a
timeout, so a slow or hung `activate` coroutine can keep running after the
session thread has given up on it — still holding the staging host and still
able to register commands, tools, providers, renderers, flags, shortcuts, chrome
requests, listeners, and messages. Dropping the sinks it already filled does not
stop the next registration.

The staging host therefore carries a one-way **sealed** flag, set under the
staging host's own guard. It applies to **class D — contribution registration —
and only class D**. Those registrations become the frozen contents of a
generation value, so they must stop the moment the candidate is frozen. This
matches the current base, where contributions are harvested from the activation
API once and a later registration already has no effect; sealing makes that
explicit and observable rather than incidental. A registration attempted after
the seal reports the same disabled-extension outcome activation already uses.

Classes A, B, and C need no seal, because their staleness is already handled:

- **Success:** seal class D *before* freezing the candidate, so the published
  generation cannot gain a late contribution and stays immutable. Classes A, B,
  and C stay live and belong to that generation, which is required — a command
  or tool handler calls `send_user_message` or updates its footer long after
  activation.
- **Rejection:** seal class D, then drop the sinks. A rejected candidate is
  never assigned a live generation id, so its class A ports fail their liveness
  check forever, and its class B chrome state and class C queues are never read.
  Nothing it produced is delivered, and nothing it produces later can be.
- **Retirement:** when a generation is superseded, its class A ports start
  failing their liveness check at the instant of the pointer swap, and its class
  B chrome state and class C queues stop being read at that same instant.
  Post-commit disposal then releases its
  listeners and chrome requests. There is no window between the swap and
  disposal in which a straggler can act on the live session.

**Commit phase (non-fallible, under `session_state_lock`).** One critical
section assigns **everything the reload prepared**, from values the candidate
phase already built:

1. the settings manager's frozen state value and the keybindings manager's
   frozen state value (each manager keeps its object identity);
2. `_RunControlState.package_roots` and `workspace_resources`;
3. the generation's frozen capability and renderer state, its provider catalog
   contributions, and its rebased selection state — provider selection, thinking
   level, and active tool set, computed as described under "What a generation
   is";
4. the coding-state provider binding, whenever the candidate phase determined
   the bound provider *object* changes. There are two such cases and both exist
   in the current base:

   - **Refresh** — the selection still resolves, but the reloaded catalog
     produced a new provider object for it. This is the ordinary case for an
     extension-contributed provider, which is rebuilt on every reload. Today's
     `refresh_provider` covers it: the binding moves to the new object while
     history and usage are preserved. Publishing a new catalog while coding
     state still points at the previous generation's provider object would both
     violate the coherent-generation requirement and keep a disposed extension's
     provider alive, so this case is staged, not skipped.
   - **Fallback** — the selection disappeared or lost tool-call support.
     Today's `rebind_provider` covers it: the binding moves to the fallback
     provider, live history is cleared, and usage is reset.

   In both cases the new provider object is constructed in the candidate phase,
   as are the fresh usage accumulator and fresh empty live-history container the
   fallback needs, so the commit *assigns* them rather than clearing anything in
   place. In-place clearing would drop references and run finalizers inside the
   critical section, which the pointer-only rule forbids; the superseded
   history and accumulator are moved into the retired record and released after
   the lock, exactly like the superseded generation. The resulting history clear
   and usage reset are the existing characterized rebind behavior and land in
   the same critical section as the new provider, so no reader observes a new
   provider against old usage.

   **Compaction state is deliberately not touched by either case.** Today's
   `rebind_provider` documents that the compaction suffix survives the
   transition, to preserve the characterized provider/auth/reload behavior, and
   `refresh_provider` retains all state. So the fallback needs no prepared
   compaction value and the commit assigns none; compaction is guarded because a
   straggler-triggered rebind executes alongside it, not because a reload
   replaces it. The only change here is mechanical: the in-place
   `self._messages.clear()` becomes an assignment of the prepared empty
   container, so no finalizer runs inside the critical section.

   When neither case applies — the selection resolves to the same provider
   object — reload touches coding state not at all. It never computes a
   candidate history or usage from the live values, so a history or usage update
   accepted before the gate opened cannot be overwritten by a wholesale
   replacement;
4. `_RunControlState.extension_generation` and its identity; and
5. the publication gate, closed.

Nothing published here is left to the post-commit phase. Items 1 and 2 are what
make "settings, trust, keybindings, package resources, and theme state remain
old on rejection and become new on success" a whole-generation property instead
of a sequence of independent updates; publishing them anywhere else would let a
reader see new commands against old settings. Package roots and workspace
resources are otherwise session-thread-owned and need no lock for their own
sake, but they are assigned here so publication is one indivisible step.

The critical section contains pointer assignments and nothing else. No I/O, no
provider construction, no activation, no callbacks, no rendering, no
diagnostics, no compensating restoration.

Assignment alone can still run arbitrary code: dropping the last reference to a
replaced value invokes finalizers and weakref callbacks at that point. The
commit therefore **pins what it replaces**. Every superseded value — the old
generation, the old settings and keybindings state, the old resources, and any
history container or usage accumulator displaced by a fallback rebind — is moved
into a locally held retired record before it is overwritten, and that record
outlives the critical section. Disposal of the retired generation — releasing its
listeners and chrome requests — is post-commit work, never a step inside the
lock.

**Post-commit phase (fail-soft, lock held only for brief reads).** Dispose the
retired generation, deliver the committed generation's custom messages,
reconcile terminal chrome, refresh footers and command menus, mirror the
committed theme into the process-global `PIPY_THEME` and repaint, and persist
implicit project trust and defaults. These are idempotent effects derived from
the committed snapshot. A failure here reports a diagnostic and never claims the
semantic generation rolled back, because it did not.

"Post-commit" means no slow or arbitrary effect runs under the lock; it does not
mean the phase is lock-free, and it does not exclude the named user-message
exception above. The reads it needs follow the same rule as everywhere else: copy the
live generation's pending message slice under `session_state_lock`, deliver
after release, then advance the delivered cursor under the lock; take a chrome
snapshot under the lock and paint from the copy after release. Each locked step
is a copy or a cursor advance, nothing more.

**The theme is two different things and the split matters.** The *semantic*
theme is the value inside the committed settings state; it is published in the
critical section with everything else and is therefore atomic. The
*presentation* theme is the process-global `PIPY_THEME` mirror and the repaint
that follows; those are post-commit and fail-soft. If the mirror or repaint
fails, the committed theme setting is still the new one and every subsequent
read of it sees the new value — the terminal is simply still showing the old
colors until the next successful paint, and a diagnostic says so. The scenario
checklist is satisfied at the semantic level; it does not claim the process
environment is transactional, because it cannot be.

### Run teardown

The lock, the generation pointer, and the whole contract are scoped to one
`NativeToolReplSession.run()`. That leaves one last straggler case: a worker
that outlives the run entirely. Its class A ports would still find a matching
generation id, and if the process reuses provider or coding state for a later
run, those ports would mutate it under a lock the new run does not hold.

Teardown therefore closes the session explicitly. In one critical section at the
end of `run()`, and in a `finally` so an aborted run is covered too:

- replace the live generation pointer with a terminal "no live generation"
  value, so every class A liveness check fails from that instant on; and
- close the sidecars of that generation, so class B and C writes are discarded
  rather than accumulating.

Superseded values are pinned and released after the lock, as everywhere else. A
later run constructs its own lock, its own generation, and its own ports; a
straggler from the previous run can match none of them.

### Snapshot discipline

Every operation that reads extension-owned state takes **one** snapshot at its
start, in a single locked read, and reads from that snapshot for its whole
duration. No consumer keeps a separately refreshed contribution map. This is
what makes "either wholly old or wholly new, never a mixture" an observable
property rather than an aspiration.

The snapshot pins the whole *configuration and contribution* view, not just the
generation pointer:

- the generation's frozen projection — commands, hooks, tools, providers,
  renderers, flags, shortcuts, parsed flag values, tool-capability and renderer
  state — and a handle to its sidecars;
- the `SettingsManager` and `KeybindingsManager` **frozen state values**, not the
  managers. The managers keep their identities and their state is replaced at
  commit, so a consumer that reads through a manager mid-operation would see new
  settings against an old generation. Generation-bound consumers therefore read
  the pinned values, and hook and tool contexts are constructed against them;
  and
- `package_roots` and `workspace_resources`.

**When this becomes load-bearing.** While the only publisher is `/reload` on
the session thread, a consumer that re-reads the live generation per access
cannot observe two generations within one operation — there is no concurrent
writer to interleave with. Snapshot discipline is therefore introduced with the
reference (S3.4) but only becomes *required* in the slice that lets a detached
worker publish or that adds generation-bound ports (S3.7). That slice must
convert consumers before, not after, it introduces the second publisher;
shipping the publisher first would open exactly the mixed-generation window
this section forbids.

The snapshot deliberately does **not** pin provider selection, thinking level,
or the active tool set. Established behavior lets a `before_agent_start` hook
change the model for the current turn, so those are read under the lock at their
point of use rather than frozen at operation start. That is a characterized
exception, not an oversight; changing it requires a dedicated behavior slice.

### What is not transactional, by construction

- **Trusted extension module side effects.** `import`/`activate` in a trusted
  extension may touch the filesystem, spawn processes, or make network calls.
  Those are outside pipy's in-process transaction and are documented as such;
  pipy-owned registries still publish nothing until commit.
- **Persisted defaults and implicit trust.** File writes are post-commit,
  idempotent, and atomic at the file level where practical. They are not
  rollback participants.
- **Process-global state** such as the `PIPY_THEME` mirror. It is post-commit
  and last-writer-wins by definition. The committed theme *setting* is
  transactional; its process-global reflection is not.

### Rules this contract makes non-negotiable

- No new revision counter, rollback framework, consistency exception hierarchy,
  or transaction participant may be added without amending this document first.
- No inheritance from an uninitialized concrete class to fake a surface; use a
  narrow `Protocol` when the composition root needs two implementations.
- No `object.__new__` hand-copying of managers; build state values through
  normal typed APIs.
- Tests exercise observable behavior and explicit synchronization seams, not
  private transaction internals. Green tests are evidence, never a concurrency
  proof — the ownership model above must stay small enough to reason about
  directly.

## Bounded sub-slices

> Historical S3.x decomposition. R1–R6 implementers must use the controlling
> [Executable R1–R6 bounds](#executable-r1r6-bounds), which supersede ownership
> or mechanism details below where they differ.

The labels below are the original implementation decomposition. The backlog's
“shipped” sub-slice ledger records useful pieces that landed under these labels;
it does not mean every ideal clause assigned to a label was completed. The
2026-07-29 reconciliation queues the remaining clauses as one newly bounded
contract-completion/re-specification slice rather than silently reopening this
whole historical sequence.

Hard bounds, per semantic slice: at most ~400 changed lines, at most four
production source files, at most one new module-level abstraction. Formatting-
only and docs-only slices are exempt from the line bound but must stay
mechanically or textually isolated. If a slice needs provider state,
configuration managers, and coding state at once, it is split before it is
written.

### S3.0 — formatting-only baseline

`ruff format` output for exactly the files later Slice 3 sub-slices touch:
`native/keybindings.py`, `native/tui.py`,
`tests/test_native_extension_lifecycle.py`,
`tests/test_native_extension_tool_renderer.py`,
`tests/test_native_tool_capabilities.py`, and
`tests/test_native_tool_loop_session.py`. Formatter output only; no manual
edits, no behavior change, no backlog or changelog update. This is a strict
subset of the eventual Slice 15 formatting program and does not pre-empt its
batching.

### S3.1 — concurrency contract

This document, landed as the reviewed contract before any code depends on it.

### S3.2 — renderer pinning

`native/tool_renderers.py`, `native/tui.py`, and the focused renderer test. The
renderer map used to render a tool result must be the one associated with the
tool set advertised for that request, not whatever visibility is live when the
result renders. Both renderer owners (captured-stream and live TUI) must obey
the same rule.

### S3.3 — tool-capability candidate values

`native/tool_capabilities.py`, the narrow reload construction seam in
`native/tool_loop_session.py`, and `tests/test_native_tool_capabilities.py`.
Build fresh or copy-on-write capability state for a candidate instead of
mutating a published registry through `replace_extensions`. Introduce a narrow
`Protocol` if the composition root needs more than one implementation.

### S3.4 — session-owned generation reference

A session-owned module (`native/session_generation.py` if a module is
warranted) holding the generation reference, its identity, and the shared lock.

Two pieces are deliberately **not** in this slice, because landing them here
would mean production state with no caller and no test that exercises it:

- the **publication gate**, which lands with the provider projection in S3.7
  where the class A ports that must observe it exist; and
- the **consumer conversion to one snapshot per operation**. This slice
  provides `snapshot()` and routes every read through the session mutex, but
  consumers still read per access. That is sound only while `/reload` on the
  session thread is the sole publisher — see "When this becomes load-bearing"
  under Snapshot discipline. S3.7 must convert consumers *before* it introduces
  a second publisher.

`_ExtensionRuntime` ownership stays in `extension_runtime.py`; that module must
not start importing settings, keybindings, provider construction, coding state,
or the TUI. Separately refreshed hook/flag paths are deleted only once the
snapshot path is complete.

### S3.5 — immutable settings and keybinding state

`native/settings.py`, `native/keybindings.py`, their focused tests, and at most
one composition seam. Each caller-owned manager keeps its object identity and
owns exactly one frozen state value (`SettingsManager._raw`/`_errors` and
`KeybindingsManager._user` collapse into one frozen value each). Reload builds a
new value through the normal typed load path; publication is one assignment.
Settings and keybindings split into separate commits if the bounds require it.

### S3.6 — coding/provider binding under the contract

`native/coding/state.py`, the generation boundary, and
`tests/test_native_coding_state.py`. Bring provider binding, canonical history,
usage, and compaction state under `session_state_lock` on both sides, as the
guarded set requires: the session thread's own reads and writes take the lock
too, not only the straggler-reachable rebind path. Message history and usage
survive concurrent and stale operations. No optimistic check whose writers
bypass the boundary.

### S3.7 — provider projection and generation-private catalog

Expected to be the largest area; split further rather than exceed bounds. The
candidate provider catalog, selection, and binding must be unreachable from the
old generation. `/model`, auth, thinking, and extension hook controls publish
against the operation's expected generation; stale operations fail closed rather
than mutate the latest generation. No provider construction under the lock.
`before_agent_start` model changes keep their current effect on the current turn
unless a dedicated behavior slice changes that contract.

### S3.8 — post-commit defaults persistence

Owned by the settings/default storage boundary, not by a transaction
participant in `tool_loop_session.py`. Persistence runs after the semantic
generation is committed, is idempotent, and is atomic at the file level where
practical. Explicit coverage for the no-prior-file path, an unwritable
directory, and a concurrent overwrite. Failure emits a safe diagnostic and never
claims a semantic rollback.

The concurrency policy for that file is **last writer wins, with no conflict
detection and no merge** — stated here so the concurrent-overwrite test has
something to assert. Atomic replacement guarantees a reader never sees a torn or
partial file; it does not guarantee that a concurrent writer's values survive,
and this slice does not try to make it. A second process that rewrites the file
between our read and our write loses its values to ours, exactly as today. The
test therefore asserts that the file is complete and parseable after interleaved
writes, not that both writers' values are present.

### S3.9 — reload integration and documentation

Only after the preceding foundations are reviewed clean: stage the candidate
runtime and every fallible derived projection, dispose rejected candidates
without delivery, commit through the non-fallible pointer assignments, reconcile
chrome and deliver committed custom messages afterwards, then run the full
scenario checklist and update `docs/architecture.md` and `docs/backlog.md` with
what actually landed.

## Behavioral scenario checklist

> Historical scenario inventory. The controlling required/narrowed status of
> each family is [Behavioral scenario ownership](#behavioral-scenario-ownership).

The finished slice must demonstrate, as observable behavior:

- a retained old generation's frozen projection is unchanged after a newer
  generation is published, and a straggler's write into that generation's
  sidecars reaches no live consumer;
- a stale model mutation cannot overwrite a newer generation;
- a stale thinking-level mutation cannot overwrite a newer generation;
- stale hook model controls fail closed;
- a stale active-tool mutation cannot alter the current generation;
- a tool worker that completes after its own cancellation contributes no
  *result* — no tool-result message, no result-derived session-tree entry, and
  no result-derived event. A message that handler deliberately sends through
  `send_user_message` is a separate extension effect governed by class C, and is
  accepted or dropped by that class's rules, exactly as today;
- appends and chrome writes to a closed retired or rejected sidecar are
  discarded rather than accumulating;
- a rejected reload whose candidate cleanup raises still leaves the live
  generation ungated and accepting mutations;
- a worker that outlives `run()` can mutate nothing: teardown invalidates the
  generation and closes its sidecars;
- a mutation that arrives while a publication is pending fails closed and is
  neither applied to the outgoing generation nor lost inside the incoming one;
- the activation API exposes no class A port, so an activating extension's
  behavior is unchanged by the gate;
- a mutation accepted before the gate opens is visible to the candidate and
  survives publication rather than being silently overwritten;
- candidate user and custom outboxes are not delivered when the candidate is
  rejected;
- a delivery that fails part-way through a batch resumes at the first
  undelivered message and loses none within that generation's lifetime;
- a reload delivers, in order, every message that existed when the publication
  gate opened — the cutoff and the gate being recorded in one critical section —
  stopping at the first delivery failure and diagnosing it together with the
  count left unattempted, while messages a straggler appends afterwards are
  dropped with the retired generation, as they are today, or delivered normally
  if the reload is rejected;
- an append that would exceed a queue's pending capacity is refused and
  diagnosed rather than growing the queue without bound;
- a custom message whose durable append succeeded is never appended twice, and a
  projection or render failure after it is diagnosed rather than retried,
  leaving the message recorded but unshown;
- rejected candidate listeners and chrome requests are disposed;
- a contribution registered after the staging host is sealed — the timed-out
  activation case — reaches neither the rejected candidate nor the published
  generation, while the published generation's own handlers keep sending
  messages and updating chrome normally;
- a candidate flag failure retains the complete prior generation — old flags
  paired with old commands, hooks, tools, providers, and renderers;
- a derived tool, provider, or renderer build failure retains the prior
  generation;
- a successful reload publishes commands, hooks, tools, providers, renderers,
  flags, shortcuts, and UI projections coherently;
- provider fallback after reload stays behaviorally compatible;
- a reload that rebuilds an extension-contributed provider under an unchanged
  selection leaves coding state bound to the new provider object, with history
  and usage preserved and no reference to the retired generation's provider;
- active-tool filtering stays compatible;
- lifecycle hook ordering stays compatible;
- removed or disabled extension chrome clears only after a successful commit;
- concurrent coding history and usage updates are not lost;
- settings, trust, keybindings, package resources, and the committed theme
  setting remain old on rejection and become new on success, with no observable
  point at which new commands run against old settings or resources;
- a failed post-commit theme mirror or repaint leaves the committed theme
  setting new and reports a diagnostic, without reverting the semantic
  generation;
- first-ever defaults persistence works with no prior file present;
- post-commit persistence failure reports a diagnostic while the semantic
  generation remains wholly new;
- renderer selection stays tied to the tool set advertised for the request; and
- `before_agent_start` model changes retain their established effect on the
  current turn.

## Verification and review gate

Each sub-slice runs focused tests, `git diff --check`, `just check`, and
`just docs-build`, then the mandatory independent review loop at high reasoning
until an explicit clean verdict with no relevant skipped or truncated files.
Findings are fixed repository-wide — every analogous instance, not the one
reported — before a fresh review round.

## Stop conditions

Implementation stops and reports rather than continuing when:

- the same substantive finding survives two consecutive fix attempts;
- findings oscillate between incompatible requirements;
- a slice exceeds the bounds above and cannot be split;
- two reviews find the same class of unsynchronized check-then-mutate bug, in
  which case this contract is revised before more code is written;
- the review gate is persistently unavailable;
- the change requires authority beyond this slice; or
- the contract above proves insufficient.

## Behavior preserved

This rebuild is behavior-preserving except where the scenario checklist names an
explicitly characterized change. CLI text, JSON/RPC schemas, provider wire
requests, session formats, event ordering, extension contracts, TUI behavior,
and command precedence stay as they are. `CHANGELOG.md` is not updated unless a
user-visible behavior change is intentionally introduced.

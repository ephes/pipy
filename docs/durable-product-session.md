# Opt-in durable product session (DE11)

## Runtime and acceptance contract

DE11 uses the existing SQLite PathOwner, Session line and scheduler host. The
new async SDK facade owns one caller loop; it does not invoke the legacy coding
coordinator, JSONL tree, volatile queue or workflow recorder. Existing SDK and
CLI defaults remain unchanged until DE44.

Create commits one product header and an actual ownerless conversation. Exact
open never creates/imports a missing or foreign product store and never
dispatches. One accepted user entry, real generation and initial identity
pointers commit atomically. Submit admits only; explicit resume executes.
Unsupported capabilities refuse before store/admission/effects.

The scheduler gains a narrow disable-and-quiesce barrier on its existing
lifecycle guard and host. Acknowledgement means dispatch disabled and every
in-progress reservation, admitted phase, real finalizer and retirement drained.
Already-admitted work may finish while pausing. Pausing is not abort; close may
supersede it but still joins actual work. No callbacks, I/O, Future completion,
joins or reference disposal occur under that guard.

Facade control work remains strongly owned and serialized through enable,
result and final quiescence. All-store live preflight refuses work outside the
selected real root/owned graph. A retry is a new explicit authorization, stays
disabled until resume, and cannot mint a second successor from a source.
Same-nonce winner recovery has priority. Generation retry derives the exact
bound initial seed and validates every audit edge; it accepts no request
override or second user input. Actual preparation reads full committed ordered
history. There is no latest-row choice or second active-pointer commit.

Captured native construction inputs are private and immutable within an
instance. Unprepared reopen requires an explicit current profile; full private
target/default/material binding remains the existing first prepare commit.
Prepared/receipt recovery uses that committed envelope exactly. Provider
pre-receipt interruption can reissue a possibly paid request. Intact recovered
unsafe tool intents now use the bounded C1 interrupted continuation below; all
historical blocked origins remain held by automatic resume. Post-receipt recovery never calls
the provider or preparer again. No exactly-once external-effect claim follows.

Cancellation detaches public waiters without dropping accepted acquisition,
control, read or invocation work. Unclaimed late resources close even after the
caller loop ends. Close starts before waiting for the control lock, stops
future observation, and waits for the actual Session/backend/lease lifetime.
Running consumer callbacks remain caller-owned. Noncooperation cannot produce
a timeout-success claim. All43 whole parity rows and the full deferred scope
remain OPEN; the bounded implementation does not close those whole obligations.

CompletedWatch deliberately filters nonterminal task changes. The facade uses a
scoped raw commit listener only to wake its caller-loop metadata waiter on
committed store changes. It registers while dispatch is paused, before enable
and result inspection, inside the strongly owned operation. One guarded pending
bit coalesces cross-thread scheduling; loop Event completion and unsubscribe
run outside that guard. A harmless store-local wake avoids decoding rich
private publications for filtering. The listener performs no
storage reads, user callbacks or mutation; the actual CompletedWatch remains
the sole rich completed projection and drain authority. Both subscriptions end
with the owned control operation. This is required to observe a pending blocked
child beneath a waiting parent; completed-only notifications cannot prove that
held outcome.

Construction API and completed replay API are distinct native facts. Mistral's
construction family `mistral` returns `mistral-conversations`; Cloudflare's
`cloudflare-workers-ai` returns `openai-completions`. Generation validates those
exact existing adapter outputs, keeps their raw replay API in the canonical
assistant/result, and keeps the construction family in saved settings. It does
not authorize another provider/model or accept arbitrary cross-family results.

A request template cannot select a caller session ID: the existing generation
prepare phase establishes its durable PROVIDER_DOC first winner. The factory
refuses a non-None template session ID before opening a store, rather than
accepting an input that that existing authority would reject during prepare.
Other admitted canonical template fields remain intact. Full P16 and the DE12
live/recovery proof obligation stay OPEN.

A second offered input refuses promptly, including while a resume owns the
control lease. A loop-local in-flight rejection latch is not an accepted-input
queue or durable first-winner authority: only the header/entry/task commit
accepts input. Cancellation of a submit waiter can lose its acknowledgement;
the strongly owned operation still finishes and observes the committed tuple.

## Public API and boundaries

The factories and `DurableProductSession`, `DurableProviderProfile`,
`DurableAdmission`, `DurableAdjudication`, `DurableRunSelection`, `DurableRunResult`,
`DurableProductError` and `UnsupportedDurableCapability` are exported from
`pipy_harness.sdk`. All methods belong to the acquiring asyncio loop.

| Operation | Committed meaning |
| --- | --- |
| `await create_durable_product_session(path, workspace=..., profile=...)` | Create one empty product header and actual ownerless conversation together. |
| `await open_durable_product_session(path, workspace=..., profile=None, selection=None)` | Open exactly an existing current-format product store; no create/import fallback, migration, auth or dispatch. Empty created stores reopen too. |
| `await product.submit(text_or_user_message)` | Admit one literal canonical input, actual initialized generation and both initial pointers in one commit; numeric acknowledgement only. |
| `await product.resume(selection=None)` | Explicit execution until actual selected terminal outcome or positively proven hold; always disable/quiesce before releasing the control lease. |
| `await product.inspect(task_id=None)` | Existing DE10 closed live metadata or one same-conversation task, with material still not_checked. |
| `await product.observe_completed()` | Actual DE09 completed replacement watch; no accumulator or durable replay cursor. |
| `await product.authorize_retry(task_id, nonce=...)` | A new DE10 attempt under the paused control lease. Same nonce recovers its winner; a fresh sibling generation refuses. No request override is accepted. |
| `await product.adjudicate_interrupted(task_id, nonce=...)` | Explicit same-attempt historical decision; preserve a valid receipt or commit an uncertain interruption, then acknowledge only the decision. Resume remains separate. |
| `await product.close()` / `async with` | Seal, stop future watch admission and await actual Session/provider/tool/read/finalizer/backend ownership. |

A held result can carry waiting parent metadata with a blocked child; use
`inspect()` for that child's closed reason. When a later paged read discovers a
positive hold, resume recaptures the selected parent's metadata before returning,
so its derived terminate reason includes that committed hold. A blocked child's
reason is never copied onto a parent whose own reason is None. These are separate
guarded captures, not an atomic multi-page snapshot. Ordinary waiting/idle is
never reported as terminal completion. Nonfinal terminate leaves later work pending;
final/only terminate remains pending blocked. No policy rewrite is performed.

Generation retry derives the exact initial bound seed and requires an explicit
`DurableRunSelection(new_task_id, resolution_nonce)` for a retried root. Every
ancestor winner is validated back to the initial run, including progressed
descendants. There is no latest-row selection or mutable active-run pointer.
Tool retry remains within the actual original root and replaces its real slot.
Terminal fast paths still refuse live owned children/conversations.

Profiles capture the existing finite native construction inputs, not an arbitrary
ProviderPort or provider factory. Spec/config/environment/options/pricing are
owned before suspension; the existing auth/material owner reconstructs execution.
All admitted native families remain supported, including their actual replay API
identities. An injected HTTP client is an explicit host transport boundary.

Compaction/automatic overflow, tree/fork/clone/reset/handoff, steer/follow-up,
queued second input, abort/reconfiguration and extension/MCP/codemode injection
are not admitted. Unknown options and unsupported control methods use fixed,
payload-free refusals. The legacy synchronous SDK retains its existing behavior.

## Evidence and deferred ownership

`test_product_session`, `test_product_faults`, `test_product_lifecycle`,
`test_product_process`, `test_product_profiles`, `test_product_values`,
`test_product_fences` and `test_scheduler_pause` exercise actual public SQLite
flows and the relevant Memory scheduler primitives. Process cuts include input
commit/ACK, provider in-flight/response/receipt, assistant admission, tool intent/
effect/receipt and final outcome; independent fake SQLite effect ledgers retain
paid-reissue uncertainty and explicit new unsafe authorization. Fresh-process
lease probes hold actual acquisition/read/provider/finalizer work through
cancelled waiters and caller-loop teardown. These are process-death/fake-wire
proofs, not power-loss or live-provider acceptance.

The source-closed `product-session-v1` comparison uses actual pinned public
Harness/Conversation admission, completion, usage and empty/terminal reopen.
Its provider stream and tools are explicit fake boundaries; real pi-ai
calculateCost and built-in settlement stay intact. A neutral fresh-process SQL
observer proves committed birth facts before acknowledgement in both fixtures.
Native-only controls separately check explicit resume, one input, derived retry,
closed DTOs, quiescence and physical ownership. They are not Pi API/thread
parity. Raw exact observations remain retained; common numeric projection does
not change native arena type/sign/presence.

[CLI/bounded live acceptance and the P16 comparison](durable-headless.md) are
provided by DE12, with explicit current-model/protected-target differences and
whole-P16 scope retained. Unsafe
interrupted-error continuation remains DE13; full version/ownership/cleanup
DE14–16, retries/deferred DE17, submissions DE18/19, context/hooks/extensions/
nested/modes/partials DE20–26, full views/history/retention DE27/28, compaction
DE29 and product/default adoption DE41–44. Pi successor-generation versus native
same-parent tool continuation remains a DE15/28 obligation. Quadratic saved
request-history and per-watch publication work retain DE27/28/41's measured
budget/refactor obligations before defaults/full-feature completion. All43 whole
P rows remain OPEN. Independent CODE CLEAN, full checks, relevant supported
platforms, strict/rendered docs and two fresh complete20phase chains gate delivery.


The default request template has an empty system prompt and loads no workspace
context or files automatically, even when the fixed native built-ins are
advertised. It is a bare model/tool session, not the normal coding-session
prompt loader. Supply an explicit canonical `request_template` with the
host-authorized system prompt/context and the exact advertised tool catalog
when those are required; its user/history fields remain empty until admission.

`inspect()` is a bounded metadata page. Pass its `next` cursor back as
`inspect(cursor=page.next, limit=128)` until `next` is None; an empty page can
still have a continuation because terminal rows are filtered after storage
paging. Task-specific inspection does not accept paging. Explicit resume itself
follows every page when determining a child's committed hold. Cursors belong
to the existing same-store guarded read API, not a durable watch replay cursor.
Invalid task IDs use fixed `DurableProductError("invalid_request")` before reads.


## Noninvoking recovered-tool resume (DE13c1)

Resume performs the closed C1 admission read before opening its scheduler. With
an unchanged tool catalog, exact saved/current safe replay remains possible.
Changed or missing capabilities are accepted only when every unresolved tool
can settle without invocation from intact execute authority or a valid receipt.
Pre-intent children, persisted old provider requests and a safe invocation still
needed anywhere in the round refuse before graph writes. That includes an
unchanged safe tool when an unrelated capability was removed. Provider profile,
material and transport compatibility remain independent requirements.

The run owns one immutable saved/current catalog value and fresh bound native
drivers. It never swaps a running scheduler's definitions or rewrites the saved
catalog. The immutable binding excludes only `initial_entry` and
`initial_generation`, which normal first submission fills in; all other header
fields stay bound, and the complete header/initial graph still undergoes product
validation. A rejected retry before submission cannot poison later admission.
For a nonterminal run, a missing profile still reports `profile_required` before
live-graph/catalog preflight; terminal reuse remains profile-free.
At the actual tool-round transaction, the next request retains saved
offered members in order only where their current declarations/descriptors are
unchanged. A same-catalog request keeps the normal serialization path. Repeated
resume cannot change the binding of an already owned scheduler.

A valid committed receipt wins unchanged, including its saved counters. Without
a receipt, recovery validates current restart metadata before dispatch and again
on the receipt transaction. Historical blocked states and their receipts remain
held; existing operator retry guards are not broadened. See the
[C1 contract](specs/2026-10-27-de13c1-interrupted-continuation-plan.md) for the
physical cuts, mixed-catalog and counter precedence acceptance boundaries.


## Explicit historical decisions (DE13c2)

[Historical adjudication](durable-adjudication.md) adds a separate same-attempt
operation. Its private audit and receipt/task transition commit atomically on
the existing Session line. Fresh matching-profile admission, fixed refusal codes,
auth-free same-nonce replay and later explicit resume are distinct operations.
Existing retry remains NEW-attempt authorization with its unchanged receipt fence.
A post-decision fault or abort can leave a replayable decision without a completed
answer; `DurableAdjudication` never claims task completion.


## Bounded restart parking (DE14a)

[Restart parking and same-task reset](durable-restart-parking.md) add an immutable
per-owner default-five ceiling and allowlisted inspection metadata. Public reset
requires existing profile/selection/ownership admission and does not dispatch;
explicit resume remains separate. It is distinct from new-attempt authorization.
Operational parking can delay valid receipt settlement without rewriting it.
Live settings and broader migration/ownership behavior remain deferred.

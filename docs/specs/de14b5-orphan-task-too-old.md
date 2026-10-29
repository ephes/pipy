# DE14b5 — explicit task_too_old orphan settlement

Adopted against delivered B4 `3f5473d49b590bee891f4f04b635a643edd1f3c2`, tree `6cf6bff631f1711d7f659cd01eba3fbd6263229e`. The normative requirements below follow repaired contract SHA256 `014ac71fe34bc5f7141a7c2ad7eced49ea2024378be2ef357ede5db9e2f0bf7a`. PLAN R1 reported zero Critical, one Warning and zero Suggestion findings. The bounded ownership/digest/test-wording repair received a scoped CLEAN R2 with no findings. The scope qualification concerns an oversized duplicate harness-spec; its complete original remained available in the review copy. This is a plan gate, not CODE or final runtime acceptance. Pi remains pinned to `2ec55d808d455b1a64a2bf7888198207a096120a`. [Development evidence](../evidence/de14b5-orphan-task-too-old.md) records the implemented core and pending work.

Root has chosen the two private API names below. The implementation will remain an incremental DE14b/P07 child; all 43 parent criteria remain open.

## Reachable setup and decision authority

This is reachable with genuine Session transactions, not an edited stored version. `Tx.create_task` captures its supplied Task and persists that version/input/initial checkpoint independently of the scheduler registry (tx.py:1097–1178). Create a conversation-owned root with stored v2; its creation initializer is part of the before-action baseline. A scheduler constructed with captured v1 starts `_enabled=False`; `open()` recovers records and sets `_opened` without enabling dispatch (scheduler.py:479–506,575–635). Warm-paused owners may publish v1 through the already-supported idle publication API; cold SQLite owners reopen the unchanged pending record with v1. No `_fit`, migrator, phase or abort invocation is needed to construct or examine these states. Memory reopening uses its supported materialized state; do not fabricate numeric history or an unowned background child.

Calling the new action must not use `_fitting_definition` as an eligibility oracle either: that helper serves dispatch and may change independently. Under the existing condition, capture the immutable generation on the Session line after strict graph validation; use `generation.definitions.get(identity(record['kind']))` and the captured exact numeric version. Do not reread `binding.source` or inspect callbacks. The reference lower-version branch is source-proved; the new explicit native/Pi runtime witness is still required.

## Single additional reason

Add only private `TaskScheduler.orphan_task_too_old(task_id: int)` and
`aorphan_task_too_old`, returning `Literal['orphaned', 'terminal']`. Preserve B4's
`orphan_missing_definition`/async method as distinct operations, with no aliases.
This action is explicit; it changes no automatic dispatch or recovery policy.

For a nonterminal target, require the existing strict generic, unmarked pending
root-leaf eligibility and a **present available captured definition** in one
immutable owner generation whose version is **strictly lower than the stored
task version**. Select that generation inside the actual Session-line decision,
under the existing condition; do not trust an earlier inspection or source Task.
Read its validated captured version directly. Do not call `_fit`, migration,
phase lookup, initializer, abort handler or user comparator to decide eligibility.
Missing, numerically equal and newer available definitions refuse without writes.
Even a newer definition with a missing/failed migrator is outside this action.

This comparator follows actual Pi `#fit`, not the English reason name: missing,
numeric equality, then available `<` stored; only then failed-migration handling.
It is numeric ordering, not string ordering, integer subtraction, a positive-only
version rule or Task identity comparison. Preserve the existing validated numeric
ABI; equivalent numeric values (including signed zero) are equal and refuse.
Cross-lane cases use values both runtimes actually represent. Native-only numeric
domain tests must not pretend distinct Python integers are identical JS values.

Use existing terminal conversion and transaction assembly to persist
`state.outcome = {status: 'orphaned', reason: 'task_too_old'}`. This is an existing
outcome reason, **not a new persisted reason field**, mutable authority bit or
inspection DTO. Preserve task identity, kind/version/input, flags, trace/links;
remove live memos/restarts and set timestamps only through existing terminal ABI.

## Inherited B4 authority is mandatory

Retain reentry/off-loop/exact safe-ID validation and owned async joining. Within
the actual commit retain failure → closed → not-open → busy admission precedence.
Reserved `pi.*` targets refuse before generic terminal handling. A missing ID is
the existing ValueError; generic terminal returns `terminal` without code/version
or graph checks, writes, revision changes, document retirement or publication.
Lifecycle admission still precedes this terminal no-op. Refusals use the existing
unsupported category; storage/read failures are not recast as unsupported.

Reuse one strict query rather than fork ownership semantics. Complete typed task
and conversation scans on this same transaction must validate target consistency,
cursor progress, identities, ownership endpoints/conversations and acyclicity.
Pending eligibility rejects marks/abortReason/abandonOnRestart, any target owner,
owned containing conversation, **any** direct owned task (terminal/background too),
any owned conversation and any incoming `waiting.on` from another task regardless
of its owner/background flag. No first-page, cached `_owned` or inspection shortcut.
These two absent first owned-edge classes exclude downstream owned graphs without
claiming general Pi transitive ownership parity. Existing eligible background roots
and ownerless conversation history are not newly excluded.

Repeat health/activity admission immediately before staging. Do not hold the
condition across storage I/O, waits or reference disposal. The occupied Session
line serializes publication/reservation against this decision; pick the generation
that actually precedes the admitted action, not a host-side precheck. Keep busy
refusal separate from deterministic paused and truly retired-enabled success.

The same real commit retires every target task document through existing assembly,
including unloaded/multiple pages, using unchanged backend retention policy.
Preserve conversation/session/unrelated task documents, entries, headers, catalogs,
winner receipts and nonces. No direct history manipulation or new cleanup callback.
No extra enable/resume/notify; ordinary committed publication may wake unrelated
enabled work. Promise zero **target** migration/phase/abort calls, not global silence.

Pre-admission/assembly failure must leave complete stored state/revisions/sequence
unchanged. Preserve real storage rollback and COMMIT-before-ACK-loss behavior: no
automatic retry or fabricated success; release the failed owner then reopen and
observe the terminal winner/no second settlement. Cancellation joins the accepted
owned worker; close cannot release its lease early. No cancellation-as-rollback
promise. Preserve C1/C2 reserved model/tool authority and all ordinary receipt,
catalog, counter, nonce and parser fences; this control never resolves those frames.

## Real Pi oracle and bounded reference extension

Use actual pinned Pi `2ec55d808d455b1a64a2bf7888198207a096120a` Harness
`abortTask`, with its installed outcome hook. For inactive eligible generic leaves,
`abort` resolves the captured registry and `#terminate` stages the terminal outcome.
The real `settleSchedulerOutcome` returns for eligible non-pi kinds before document
access; preserve Harness-created live/inbox baseline and prove it unchanged.
Map raw Pi `marked` to native `orphaned` **only when the recorded immediate terminal
outcome proves this exact reason**. Retain raw ACKs, full TaskRecords, callbacks,
documents, revisions and state trajectories; no broader error/history/flag/version
normalization. Native strict refusals are not cross-lane Pi abort promises.

Use additive phase/profile identity `task-orphan-task-too-old-v1` after existing phase29, with comparator parent key `orphan_missing_definition`. The reference child is registered and source-closed; direct development is source-qualified in the evidence record, while final cumulative execution remains separately gated. Preserve
all old29 IDs/order, members, Pi pins, edges, interfaces and raw qualifications. Astra refreshes necessary source-closure and parent-manifest digests in existing manifests, including the B4 manifest; preserving Pi pins does not freeze these derived digests.
Use real created version-2 tasks reopened/owned with version1; seeded parking is
an explicitly labelled policy control, not a crash. No waitForIdle on live blocked
work: snapshot-before-action on the real line, await the action's actual settlement
then terminal observation. Enabled native fixtures require the existing witnessed
retirement plus actual owner-loop event/current guarded activity proof; never busy
retry, flag assignment or timer-as-success. APIs/CLI controls/fresh-process proof
would require separate source-closed Root execution GO, not this draft.

## Shared helper and exact compatibility choice

Rename only the private graph prerequisite `require_missing_root_leaf` to `require_unmarked_root_leaf(tx, record)`. Its purpose is validated unmarked pending root-leaf eligibility, independently of why code cannot run. Keep its scans, target equality, cursor/endpoints/cycle and incoming-waiter predicates unchanged, and retain the local-control-only scope. Both orphan operations must call it; do not introduce a callback-parametrized/general registry or change `_owned`. Remove the old private helper name outright, with no alias. The shared refusal text becomes exactly `Task requires a validated unmarked pending root leaf`; this is the sole intentional B4 diagnostic-wording change, with the same exception class and refusal/no-write behavior. Astra updates the exact REFUSAL string in existing tests/durable/orphan_refusal_support.py accordingly; the retained B4 assertions keep their behavior and intentionally expect this new shared wording. Both APIs retain the existing lifecycle messages, unknown-ID ValueError and built-in refusal. New reason refusal uses `Task definition is not older than the stored task` for missing/equal/newer code; no new error category.

`orphan_control.py` keeps one shared transactional control skeleton, with a private closed choice of missing_task versus task_too_old and no caller-supplied callback/predicate. B4's public-to-private call path retains its missing-only behavior; the two new scheduler methods choose task_too_old. This is code organization, not an additional control API. Generic terminal remains before graph and generation lookup for either operation, after existing lifecycle and pi.* gates. Return statuses and `OrphanResult` stay unchanged. records.py:143–157 already validates orphaned.outcome.reason as text; task_too_old uses that existing field, adds no schema or diagnostic DTO. No deprecated API or public alias is introduced.

## Remaining scope and ownership

No substantive ownership, lifecycle or persistence decision is deferred. Review must verify the actual line/condition final admission, strict present-and-older comparison, shared helper behavior preservation, terminal-before-lookup and standard retirement/ACK-loss semantics. A genuine counterexample blocks implementation rather than expanding scope. Astra owns native/core integration, the retained B4 refusal helper, existing affected manifests and documentation; isolated broad and reference authors consume an immutable core snapshot. Only approved overlays are integrated. The [acceptance matrix](de14b5-orphan-task-too-old-tests.md) retains the required witnesses.

Public SDK/CLI abort, arbitrary blocked reasons, failed-migration orphaning, active abort, completing/cascade work, wait settlement, backend policy changes, active handover and full DE15/16/23 remain open. B4 is delivered at the commit above. This bounded successor does not close any whole parent criterion.

Actual test counts and runtime claims come from their retained source-bound execution epochs. Broad/reference integration, independent CODE review and final acceptance remain distinct from the adopted plan and initial core.
